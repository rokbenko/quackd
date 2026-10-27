"""EXPERIMENTAL: a wheeled base over rosbridge through roslibpy. Verified names, never run.

Every roslibpy and rosbridge name comes from `upstream_api.py` (ADR-0022). The address
carries everything: `ws://host:9090?cmd_vel=/cmd_vel&odom=/odom&image=/camera/compressed`.
quackd publishes `geometry_msgs/msg/Twist` on the command topic, re-sent at 10 Hz by the
core `move` verb, and a zero Twist on stop, which is the only stop authority there is
(`upstream_api.NO_DEADMAN`). Odometry and the optional compressed image are subscribed,
and only the latest of each is kept, under a lock, because callbacks arrive on roslibpy's
own thread. roslibpy is imported inside `connect()` only: `quackd[rosbridge]` is an extra.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import io
import json
import math
import threading
import time
import uuid
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import parse_qs, urlsplit

from PIL import Image

from quackd.adapters.base import AdapterNotInstalled
from quackd.passport.beacon import ROS_TOPIC
from quackd.space.odometry import OdometrySource, OdometryTrack
from quackd.space.pose import Fix, where_record
from quackd.transport.base import Ack, DuckState, HeartbeatError, Intent, TransportError
from quackd_rosbridge import upstream_api as up
from quackd_rosbridge.introspection import (
    Introspection,
    Source,
    UrdfSummary,
    parse_urdf,
)

STATUS = "EXPERIMENTAL: roslibpy and rosbridge names verified at pinned commits, never run"
DEFAULT_ADDRESS = "ws://localhost:9090"
DEFAULT_CMD_VEL = "/cmd_vel"
DEFAULT_ODOM = "/odom"
DEFAULT_URDF_PARAM = up.DESCRIPTION_PARAM.name
DEFAULT_URDF_TOPIC = up.DESCRIPTION_TOPIC.name
OFF = "off"
"""What either URDF source is set to in the address when a bridge should not be asked."""


def _off(value: str) -> str | None:
    return None if value == OFF else value


def _param_text(value: Any) -> str:
    """A parameter arrives JSON-encoded (`upstream_api.PARAM_VALUE_JSON`), so a description is
    a quoted string. A bridge that sends it raw is taken at its word rather than refused."""
    if not isinstance(value, str):
        return ""
    try:
        decoded = json.loads(value)
    except ValueError:
        return value
    return decoded if isinstance(decoded, str) else value


@dataclass(frozen=True)
class Endpoint:
    host: str
    port: int
    secure: bool
    cmd_vel: str
    odom: str
    image: str | None
    urdf_param: str | None
    urdf_topic: str | None

    @property
    def url(self) -> str:
        return f"{'wss' if self.secure else 'ws'}://{self.host}:{self.port}"


def parse_address(address: str | None) -> Endpoint:
    """`ws://host:9090?cmd_vel=/cmd_vel&odom=/odom&image=/camera/image/compressed`."""
    parts = urlsplit(address or DEFAULT_ADDRESS)
    if parts.scheme not in ("ws", "wss"):
        raise TransportError(f"rosbridge address must start with ws:// or wss://, not {address!r}")
    query = {k: v[-1] for k, v in parse_qs(parts.query).items()}
    for key in ("cmd_vel", "odom", "image", "urdf_topic"):
        if key in query and query[key] != OFF and not query[key].startswith("/"):
            raise TransportError(f"rosbridge topic {key}={query[key]!r} must start with /")
    if (param := query.get("urdf_param", DEFAULT_URDF_PARAM)) != OFF and not param.startswith("/"):
        raise TransportError(
            f"rosbridge urdf_param={param!r} must start with / and name a node and a parameter, "
            f"as in {DEFAULT_URDF_PARAM}"
        )
    return Endpoint(
        host=parts.hostname or "localhost",
        port=parts.port or 9090,
        secure=parts.scheme == "wss",
        cmd_vel=query.get("cmd_vel", DEFAULT_CMD_VEL),
        odom=query.get("odom", DEFAULT_ODOM),
        image=query.get("image"),
        urdf_param=_off(query.get("urdf_param", DEFAULT_URDF_PARAM)),
        urdf_topic=_off(query.get("urdf_topic", DEFAULT_URDF_TOPIC)),
    )


class TopicLike(Protocol):
    """The slice of `roslibpy.Topic` the backend uses."""

    def publish(self, message: Any) -> Any: ...

    def subscribe(self, callback: Callable[[dict[str, Any]], None]) -> Any: ...

    def unsubscribe(self) -> Any: ...

    def unadvertise(self) -> Any: ...


class RosLike(Protocol):
    """The slice of `roslibpy.Ros` the backend uses."""

    @property
    def is_connected(self) -> bool: ...

    def run(self, timeout: float = ...) -> Any: ...

    def close(self, timeout: float = ...) -> Any: ...

    def terminate(self) -> Any: ...


class ServiceLike(Protocol):
    """The slice of `roslibpy.Service` the backend uses: one blocking call with a deadline."""

    def call(self, args: dict[str, Any], *, timeout: float) -> dict[str, Any]: ...


TopicFactory = Callable[[Any, str, str], TopicLike]
ServiceFactory = Callable[[Any, str, str], ServiceLike]


def twist_message(vx: float, vy: float, wz: float) -> dict[str, Any]:
    """`geometry_msgs/msg/Twist` as a dict (roslibpy sends dict(message))."""
    return {
        "linear": {"x": float(vx), "y": float(vy), "z": 0.0},
        "angular": {"x": 0.0, "y": 0.0, "z": float(wz)},
    }


def yaw_from_quaternion(q: dict[str, Any]) -> float:
    """Planar yaw from (x, y, z, w); `upstream_api.ODOM_YAW`."""
    x, y, z, w = (float(q.get(k, 0.0)) for k in ("x", "y", "z", "w"))
    return math.atan2(2.0 * (w * z + x * y), 1.0 - 2.0 * (y * y + z * z))


def odom_stamp(message: dict[str, Any]) -> float | None:
    """When the stack took an /odom message, by its header's stamp, in seconds: ROS 2's `sec`
    and `nanosec`, or ROS 1's `secs` and `nsecs`. None for a stamp of nothing, which a stack
    that stamps nothing sends."""
    header = message.get("header")
    stamp = header.get("stamp") if isinstance(header, dict) else None
    if not isinstance(stamp, dict):
        return None
    sec, nsec = stamp.get("sec", stamp.get("secs")), stamp.get("nanosec", stamp.get("nsecs", 0))
    parts = (sec, nsec)
    if not all(isinstance(v, int | float) and not isinstance(v, bool) for v in parts):
        return None
    t = float(sec) + float(nsec) * 1e-9  # type: ignore[arg-type]
    return t if t > 0 and math.isfinite(t) else None


def decode_compressed_image(message: dict[str, Any]) -> Image.Image:
    """A `sensor_msgs/msg/CompressedImage` (data base64 over rosbridge) to RGB."""
    fmt = str(message.get("format", "")).lower()
    data = message.get("data", "")
    raw = base64.b64decode(data) if isinstance(data, str) else bytes(data)
    if not any(codec in fmt for codec in ("jpeg", "jpg", "png")) and fmt:
        raise ValueError(f"unsupported CompressedImage format {fmt!r} (jpeg or png only)")
    img: Image.Image = Image.open(io.BytesIO(raw))
    img.load()
    if img.mode != "RGB":
        img = img.convert("RGB")
    if "bgr8" in fmt:
        r, g, b = img.split()
        img = Image.merge("RGB", (b, g, r))
    return img


class RosbridgeWs:
    name = "ws"
    mobility = "wheeled"

    def __init__(
        self,
        address: str | None = None,
        *,
        ros: RosLike | None = None,
        topic_factory: TopicFactory | None = None,
        service_factory: ServiceFactory | None = None,
        timeout_s: float = 5.0,
    ) -> None:
        self.endpoint = parse_address(address)
        self.timeout_s = timeout_s
        self._ros: Any = ros  # injected in tests; built in connect() otherwise
        self._topic_factory = topic_factory
        self._service_factory = service_factory
        self.introspection: Introspection | None = None
        """What the bridge last said about the body under it. None until `connect`."""
        self._cmd: TopicLike | None = None
        self._odom: TopicLike | None = None
        self._image: TopicLike | None = None
        self._lock = threading.RLock()
        """Guards what roslibpy's thread hands over, the odometry track included. Re-entrant,
        since a reading read under it asks how stale the stream is, which takes it again."""
        self._latest_odom: dict[str, Any] | None = None
        self._track: OdometryTrack | None = None
        self._odom_stamped = False
        self._odom_stamp: float | None = None
        self._odom_fresh_at: float | None = None
        self.spatial: OdometrySource | None = None
        """Where the base is by its /odom (`_OdomReading`): set at connect, a reading from the
        first message on, and the place verbs walk by it."""
        self._latest_image: dict[str, Any] | None = None
        self._closed = False
        self._t0 = time.monotonic()
        self.last_twist = (0.0, 0.0, 0.0)
        self.published = 0
        self.roslibpy_version: str | None = None
        self.post_sleep: Callable[[], None] | None = None

    @property
    def camera_available(self) -> bool:
        return self.endpoint.image is not None

    # ── plumbing ────────────────────────────────────────────────────────────────────

    def _build(self) -> tuple[Any, TopicFactory, ServiceFactory]:
        try:
            import roslibpy
        except ImportError as e:
            raise AdapterNotInstalled("rosbridge", "quackd[rosbridge]") from e
        self.roslibpy_version = getattr(roslibpy, "__version__", None)
        ros = roslibpy.Ros(self.endpoint.host, self.endpoint.port, is_secure=self.endpoint.secure)

        def factory(client: Any, name: str, message_type: str) -> TopicLike:
            topic: TopicLike = roslibpy.Topic(client, name, message_type, compression="none")
            return topic

        def services(client: Any, name: str, service_type: str) -> ServiceLike:
            service = roslibpy.Service(client, name, service_type)

            class _Call:
                def call(self, args: dict[str, Any], *, timeout: float) -> dict[str, Any]:
                    # no callback, so this blocks until the answer or the deadline
                    answer = service.call(roslibpy.ServiceRequest(args), timeout=timeout)
                    return dict(answer) if answer is not None else {}

            return _Call()

        return ros, factory, services

    def _on_odom(self, message: dict[str, Any]) -> None:  # roslibpy's thread
        at = time.monotonic()
        with self._lock:
            self._latest_odom = dict(message)
            self._feed_odom(self._latest_odom, at)

    def _feed_odom(self, message: dict[str, Any], at: float) -> None:
        """One /odom into the track as it arrives, under the lock, so the track measures the
        stream's own period rather than how often quackd reads it, which can be seconds apart
        while a pilot thinks. It is dated by its header's stamp, the stack's own clock, or by its
        arrival here while the stack has stamped none. Once one is stamped the stream is: an
        unstamped message after that is another clock's and no reading, and the same stamp
        again is the same reading, which says nothing of whether the stream still runs."""
        track = self._track
        if track is None:
            return
        try:
            pose = dict(message.get("pose", {})).get("pose", {})
            position = dict(pose.get("position", {}))
            x, y = float(position.get("x", 0.0)), float(position.get("y", 0.0))
            yaw = yaw_from_quaternion(dict(pose.get("orientation", {})))
        except (AttributeError, TypeError, ValueError):
            return  # a pose that is not one moves nothing
        stamp = odom_stamp(message)
        if stamp is None:
            if self._odom_stamped:
                return
            t = at
        else:
            if stamp == self._odom_stamp:
                return
            if not self._odom_stamped:
                self._odom_stamped = True
                track.reclock()  # from its arrival here to the stack's clock, in one frame
            self._odom_stamp = t = stamp
        track.update(x, y, yaw, t)
        self._odom_fresh_at = at

    def _odom_staleness(self) -> str | None:
        """Why the newest /odom cannot be trusted now, or None: no new reading has come for five
        of the stream's own periods, a second at least, as a stream's stale window is reckoned.
        rosbridge has no deadman, so a walk on a reading that stopped is ended by this or by its
        own clock."""
        with self._lock:
            at = self._odom_fresh_at
            period = self._track.period_s if self._track is not None else None
        if at is None:
            return None  # none yet, which the track says itself
        window = max(1.0, 5 * period) if period else 1.0
        age = time.monotonic() - at
        return f"no new {self.endpoint.odom} for {age:.1f} s" if age > window else None

    def _on_image(self, message: dict[str, Any]) -> None:  # roslibpy's thread
        with self._lock:
            self._latest_image = dict(message)

    # ── protocol ────────────────────────────────────────────────────────────────────

    async def connect(self) -> None:
        self._closed = False
        # only when nothing was injected: a test that hands over a ros and a topic factory but
        # no service factory gets a connection that drives and an introspection that says it
        # could not ask, which is exactly what a bridge without rosapi looks like
        if self._ros is None or self._topic_factory is None:
            ros, factory, services = await asyncio.to_thread(self._build)
            self._ros = self._ros or ros
            self._topic_factory = self._topic_factory or factory
            self._service_factory = self._service_factory or services
        try:
            await asyncio.wait_for(
                asyncio.to_thread(self._ros.run, self.timeout_s), timeout=self.timeout_s + 1.0
            )
        except Exception as e:
            raise TransportError(f"rosbridge ws: cannot reach {self.endpoint.url}: {e}") from e
        make = self._topic_factory
        self._cmd = make(self._ros, self.endpoint.cmd_vel, up.MSG_TWIST.name)
        self._odom = make(self._ros, self.endpoint.odom, up.MSG_ODOMETRY.name)
        with self._lock:
            # a frame for this connection, before the first message can arrive
            self._track = OdometryTrack(f"odom:{uuid.uuid4().hex[:12]}")
            self._odom_stamped, self._odom_stamp, self._odom_fresh_at = False, None, None
        self.spatial = _OdomReading(self, self._track)
        self._odom.subscribe(self._on_odom)
        if self.endpoint.image is not None:
            self._image = make(self._ros, self.endpoint.image, up.MSG_COMPRESSED_IMAGE.name)
            self._image.subscribe(self._on_image)
        try:
            self.introspection = await self.introspect()
        except Exception as e:
            # a bridge that will not describe the body is still a bridge that drives it; the
            # datasheet says nothing was discovered and the pilot declines what hinges on it
            self.introspection = Introspection.from_urdf(
                None, errors=(f"introspection failed: {type(e).__name__}: {e}",)
            )

    async def _bounded(
        self, work: Callable[[], Any], seconds: float, what: str, errors: list[str]
    ) -> Any:
        """Run one blocking call with a deadline. A failure is a line in `errors`, never a
        raise: a bridge that will not answer about the body is still a bridge that drives."""
        try:
            return await asyncio.wait_for(asyncio.to_thread(work), seconds)
        except TimeoutError:
            errors.append(f"{what}: no answer within {seconds:.0f} s")
        except Exception as e:
            errors.append(f"{what}: {type(e).__name__}: {e}")
        return None

    def _service(self, name: str, service_type: str) -> ServiceLike:
        if self._ros is None or self._service_factory is None:
            raise TransportError("rosbridge ws: not connected")
        return self._service_factory(self._ros, name, service_type)

    async def introspect(self) -> Introspection:
        """Ask the bridge what this body is: the topic list, then the robot's description.

        One deadline covers all of it, so introspection can add at most `timeout_s` to a
        connect. The description is looked for on the parameter first and on the topic second,
        because a parameter answers at once while a topic only answers if somebody publishes;
        either can be turned off in the address."""
        if self._ros is None:
            raise TransportError("rosbridge ws: not connected")
        deadline = time.monotonic() + self.timeout_s
        errors: list[str] = []

        def left() -> float:
            return max(0.2, deadline - time.monotonic())

        topics: dict[str, str] = {}
        listing = self._service(up.SVC_TOPICS.name, up.SRV_TOPICS.name)
        answer = await self._bounded(
            lambda: listing.call({}, timeout=left()), left(), up.SVC_TOPICS.name, errors
        )
        if isinstance(answer, dict):
            names, types = answer.get("topics") or [], answer.get("types") or []
            topics = dict(zip(names, types, strict=False))

        summary: UrdfSummary | None = None
        source: Source | None = None
        where: str | None = None

        if (param := self.endpoint.urdf_param) is not None:
            service = self._service(up.SVC_GET_PARAM.name, up.SRV_GET_PARAM.name)
            answer = await self._bounded(
                lambda: service.call({"name": param, "default_value": ""}, timeout=left()),
                left(),
                f"{up.SVC_GET_PARAM.name} {param}",
                errors,
            )
            if isinstance(answer, dict):
                if answer.get("successful") is False:
                    errors.append(f"{param}: {answer.get('reason') or 'the bridge said no'}")
                elif text := _param_text(answer.get("value")):
                    summary, source, where = parse_urdf(text), "param", param

        if summary is None and (topic_name := self.endpoint.urdf_topic) is not None:
            summary, source, where = await self._read_description(topic_name, left(), errors)

        intro = Introspection.from_urdf(
            summary, topics=topics, source=source, where=where, errors=tuple(errors)
        )
        if not intro.discovered:
            intro = Introspection.from_urdf(
                None, errors=(*errors, "nothing discovered on the bridge")
            )
        return intro

    async def _read_description(
        self, topic_name: str, seconds: float, errors: list[str]
    ) -> tuple[UrdfSummary | None, Source | None, str | None]:
        """The second place a description hides: a latched topic.

        `robot_state_publisher` publishes it transient-local, and rosbridge's own subscriber
        follows a transient-local publisher, so a subscription made now can still see a
        message sent at startup (`upstream_api.SUBSCRIBER_QOS_FOLLOWS_PUBLISHERS`)."""
        text = await self._read_latched(topic_name, seconds, errors)
        if text is None:
            return None, None, None
        return parse_urdf(text), "topic", topic_name

    async def _read_latched(self, topic_name: str, seconds: float, errors: list[str]) -> str | None:
        """One `std_msgs/String` from a latched topic within `seconds`, or None with a line in
        `errors` saying why. A URDF and a Robot Passport are both read this way."""
        make = self._topic_factory
        if make is None or self._ros is None:
            return None
        arrived = threading.Event()
        box: dict[str, Any] = {}
        topic = make(self._ros, topic_name, up.MSG_STRING.name)

        def on_message(message: dict[str, Any]) -> None:  # roslibpy's thread
            box.update(message)
            arrived.set()

        topic.subscribe(on_message)
        try:
            await self._bounded(
                lambda: arrived.wait(seconds), seconds, f"a message on {topic_name}", errors
            )
        finally:
            with contextlib.suppress(Exception):
                topic.unsubscribe()
        if not arrived.is_set():
            errors.append(
                f"nothing published on {topic_name} within {seconds:.0f} s; "
                "it is published transient-local, and a subscription only inherits that when "
                "every publisher on the topic has it"
            )
            return None
        return str(box.get("data", ""))

    async def close(self) -> None:
        self._closed = True
        with contextlib.suppress(Exception):
            self._publish_twist(0.0, 0.0, 0.0)
        for topic in (self._odom, self._image):
            if topic is not None:
                with contextlib.suppress(Exception):
                    topic.unsubscribe()
        if self._cmd is not None:
            with contextlib.suppress(Exception):
                self._cmd.unadvertise()
        if self._ros is not None:
            with contextlib.suppress(Exception):
                await asyncio.to_thread(self._ros.terminate)

    def _publish_twist(self, vx: float, vy: float, wz: float) -> None:
        if self._cmd is None:
            raise TransportError("rosbridge ws: not connected")
        self.last_twist = (vx, vy, wz)
        self.published += 1
        self._cmd.publish(twist_message(vx, vy, wz))

    async def get_frame(self) -> Image.Image | None:
        if self._image is None:
            return None
        with self._lock:
            message = self._latest_image
        if message is None:
            return None
        return decode_compressed_image(message)

    async def get_state(self) -> DuckState:
        with self._lock:
            odom = self._latest_odom
        x = y = theta = None
        if odom is not None:
            pose = dict(odom.get("pose", {})).get("pose", {})
            position = dict(pose.get("position", {}))
            x, y = float(position.get("x", 0.0)), float(position.get("y", 0.0))
            theta = yaw_from_quaternion(dict(pose.get("orientation", {})))
        vx, vy, wz = self.last_twist
        intro = self.introspection
        return DuckState(
            t=self.now(),
            policy="idle",
            posture="unknown",
            fallen=False,
            battery_percent=None,
            x=x,
            y=y,
            theta=theta,
            extras={
                **({"where": where_record(self.spatial.fix())} if self.spatial else {}),
                "odom_seen": odom is not None,
                "twist": {"vx": vx, "vy": vy, "wz": wz},
                "assumptions": self._assumptions(),
                "introspection": {
                    "discovered": intro.discovered if intro else False,
                    "source": intro.source if intro else None,
                    "topics": len(intro.topics) if intro else 0,
                    "urdf": intro.urdf_name if intro else None,
                    "errors": list(intro.errors) if intro else [],
                },
            },
        )

    def _assumptions(self) -> list[str]:
        """What quackd is standing in for, in the robot's own observation. A description read
        off the bridge adds two more, because both are readings of somebody else's file."""
        names = [
            up.NO_DEADMAN.name,
            up.TWIST_UNITS.name,
            up.ODOM_YAW.name,
            up.DESCRIPTION_NAMES.name,
        ]
        if self.introspection is not None and self.introspection.source is not None:
            names += [up.URDF_MASS_IS_THE_BODY.name, up.DOF_IS_NON_FIXED_JOINTS.name]
        return names

    async def send_intent(self, intent: Intent) -> Ack:
        p = intent.params
        try:
            match intent.kind:
                case "move":
                    self._publish_twist(
                        float(p.get("vx", 0.0)), float(p.get("vy", 0.0)), float(p.get("wz", 0.0))
                    )
                case "stop":
                    self._publish_twist(0.0, 0.0, 0.0)
                case _:
                    return Ack(accepted=False, reason=f"a base over rosbridge cannot {intent.kind}")
        except Exception as e:
            return Ack(accepted=False, reason=f"{intent.kind} failed: {type(e).__name__}: {e}")
        return Ack()

    async def subscribe(self, topic: str) -> AsyncIterator[dict[str, Any]]:  # type: ignore[override]
        while not self._closed:
            await self.sleep(0.5)
            yield {"topic": topic, **(await self.get_state()).model_dump()}

    async def heartbeat(self) -> None:
        if self._closed:
            raise HeartbeatError("rosbridge ws transport is closed")
        if self._ros is None or not bool(self._ros.is_connected):
            raise HeartbeatError(f"rosbridge at {self.endpoint.url} is not connected")

    async def stop(self) -> None:
        with contextlib.suppress(Exception):
            self._publish_twist(0.0, 0.0, 0.0)

    def now(self) -> float:
        return time.monotonic() - self._t0

    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(seconds)
        if self.post_sleep is not None:
            self.post_sleep()


async def read_passport(
    address: str,
    *,
    topic: str = ROS_TOPIC,
    timeout_s: float = 5.0,
    ros: RosLike | None = None,
    topic_factory: TopicFactory | None = None,
) -> str:
    """A ROS robot's own Robot Passport, read once from its latched topic.

    It opens the bridge, reads one message and closes it again, and nothing else: no `cmd_vel`
    is advertised, no twist is ever published, no odometry is subscribed, because quackd is only
    meeting this robot and drives nothing it was not given (docs/passport/discovery.md#ros-2)."""
    reader = RosbridgeWs(address, ros=ros, topic_factory=topic_factory, timeout_s=timeout_s)
    if reader._ros is None or reader._topic_factory is None:
        built, factory, _services = await asyncio.to_thread(reader._build)
        reader._ros = reader._ros or built
        reader._topic_factory = reader._topic_factory or factory
    try:
        await asyncio.wait_for(
            asyncio.to_thread(reader._ros.run, timeout_s), timeout=timeout_s + 1.0
        )
    except Exception as e:
        raise TransportError(f"rosbridge ws: cannot reach {reader.endpoint.url}: {e}") from e
    errors: list[str] = []
    try:
        text = await reader._read_latched(topic, timeout_s, errors)
    finally:
        with contextlib.suppress(Exception):
            await asyncio.to_thread(reader._ros.terminate)
    if text is None:
        raise TransportError("; ".join(errors) or f"no passport on {topic}")
    return text


class _OdomReading(OdometrySource):
    """The base's /odom as where it is. roslibpy hands each message over on a thread of its own,
    which feeds the track as it arrives (`RosbridgeWs._feed_odom`), so the reading and its period
    are read under the same lock."""

    def __init__(self, transport: RosbridgeWs, track: OdometryTrack) -> None:
        super().__init__(
            track,
            stale=transport._odom_staleness,
            label=f"{transport.endpoint.odom} over rosbridge",
        )
        self._lock = transport._lock

    @property
    def period_s(self) -> float | None:
        with self._lock:
            return self.track.period_s

    def fix(self) -> Fix:
        with self._lock:
            return super().fix()
