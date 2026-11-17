"""EXPERIMENTAL: a wheeled base over rosbridge through roslibpy. Verified names, never run.

Every roslibpy and rosbridge name comes from `upstream_api.py` (ADR-0022). The address
carries everything: `ws://host:9090?cmd_vel=/cmd_vel&odom=/odom&image=/camera/compressed`.
Roboglot publishes `geometry_msgs/msg/Twist` on the command topic, re-sent at 10 Hz by the
core `move` verb, and a zero Twist on stop, which is the only stop authority there is
(`upstream_api.NO_DEADMAN`). Odometry and the optional compressed image are subscribed,
and only the latest of each is kept, under a lock, because callbacks arrive on roslibpy's
own thread. roslibpy is imported inside `connect()` only: `roboglot[rosbridge]` is an extra.
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
from collections import deque
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, replace
from typing import Any, Protocol
from urllib.parse import parse_qs, urlsplit

from PIL import Image

from roboglot.adapters.base import AdapterNotInstalled
from roboglot.space.odometry import OdometrySource, OdometryTrack
from roboglot.space.pose import Fix, where_record
from roboglot.transport.base import (
    CLOSE_STOP_NOTE,
    Ack,
    HeartbeatError,
    Intent,
    RobotState,
    TransportError,
)
from roboglot_rosbridge import upstream_api as up
from roboglot_rosbridge.introspection import (
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
STALE_PERIODS = 5
STALE_FLOOR_S = 1.0
"""How long a stream may bring nothing new before it is stale: five of its own periods, and a
second at least, as a stream's stale window is reckoned across Roboglot (the Microduck's state
stream, mapd's `STALE_S`). /odom is judged by it for where the base is
(`RosbridgeWs._odom_staleness`), and the link by it, over its streams' pace now
(`RosbridgeWs._quiet`, `SLOWEST_PACE_S`), an answer counting as a message."""
SILENT_BEATS = 2
"""How many heartbeats in a row the link must give no sign of life before the heartbeat fails:
more than one, the fewest that tell a late answer or a slow message, which a live link gives,
from a link that has gone, since one failed beat ends a run, and an MCP session for good."""
SLOWEST_PACE_S = 2 * STALE_FLOOR_S
"""The slowest pace a stream is taken at: half a hertz, the slowest /odom a base keeps its
heartbeat on. A stream's pace is the slowest of its last `STALE_PERIODS` gaps, what it does now,
and a slower stream, or a pause the link came back from, is taken at this one, so no pace
stretches the link's window past `STALE_PERIODS` of it, ten seconds, and a pause in the past
widens nothing once `STALE_PERIODS` fresh gaps have come."""
ANSWER_WAIT_S = 2 * STALE_FLOOR_S
"""How long a request to the bridge is given before a beat on a base with no stream counts it
unanswered: the floor a message is given, and as long again, since an answer queued behind
camera frames over Wi-Fi came half as late again as the floor. An answer after it is still a
sign of life when it comes, up to the transport's own request deadline (`timeout_s`)."""
LISTEN_PERIODS = 2
"""How many of its streams' own periods a stop listens after its zero Twist for a sign that the
link is alive: the next message is due within one, and the second lets one be lost. Nothing in
that time, and the zero is not known to have reached the base; on a base with no stream there
is nothing to listen to, and the stop says what the socket says."""
BACK_TO_BACK_S = 0.01
"""How close together two messages come when a socket gives up what a hiccup held rather than at
a stream's pace: under a hundredth of a second, where those six held messages came a millisecond
apart, and a 100 Hz stream, faster than an /odom or a camera a base publishes, is a hundredth
apart. After a long gap only messages this close are taken as ones it held (`_Pace`), so a
stream that starts again at its own rate sets its pace at once."""
LISTEN_FLOOR_S = STALE_FLOOR_S
"""The least a stop listens, whatever its stream's pace: a stop gives the link no less time to
show it is alive than a stream's window gives it, the floor. A pace is measured from arrivals,
and messages that came together, a driver flushing its queue, measure one far shorter than the
stream's: two of that pace was two milliseconds, and a live 10 Hz base's stop said its zero was
not known to have arrived. It costs a stop into a link that is gone a second, not a fifth."""
FRAME_STALE_PERIODS = 4
FRAME_STALE_FLOOR_S = 2.0
"""How old a camera image may be before `get_frame` stops handing it out: four of the image
stream's own periods, and two seconds at least, the rule the Microduck's transport keeps for the
same hazard (`camera_stale_after_s`). A camera that stopped with the target in shot would show it
in shot for as long as `go_to` steered at it. An image is as old as its arrival here: nothing
on the wire says how long it was on the way."""


class _Pace:
    """A stream's pace, as its arrivals show it, kept under the transport's lock: the slowest of
    its last `STALE_PERIODS` gaps, taken at no slower than `SLOWEST_PACE_S`, or None before two
    messages have come.

    Messages that a long gap held, a link that hiccupped and then let them through together,
    come back to back, and are not the stream's pace: taken as one, six of them made a 10 Hz
    base's pace a millisecond, and a slow stream's window a second. After a gap more than twice
    the pace, as many messages as that gap could have held that come back to back
    (`BACK_TO_BACK_S`, and closer than half the pace before it) are counted as held, and set
    nothing; the gap itself is the pace until the stream's own gaps come again. A gap at the
    stream's own rate breaks the run at once."""

    def __init__(self) -> None:
        self.at: float | None = None
        """When the last message came, on this machine's clock."""
        self.pace: float | None = None
        self._gaps: deque[float] = deque(maxlen=STALE_PERIODS)
        self._held = 0
        """How many more messages that come closer than `_held_below` are ones a gap held."""
        self._held_below = 0.0

    def clear(self) -> None:
        self.at, self.pace, self._held, self._held_below = None, None, 0, 0.0
        self._gaps.clear()

    def heard(self, at: float) -> None:
        """A message came at `at`."""
        if self.at is not None:
            gap = at - self.at
            if self._held and gap < self._held_below:
                self._held -= 1  # one the gap before held, come with the rest
            else:
                pace = self.pace
                if pace is not None and pace > 0 and gap > 2 * pace:
                    self._held = math.ceil(gap / pace)
                    self._held_below = min(pace / 2, BACK_TO_BACK_S)
                else:
                    self._held, self._held_below = 0, 0.0
                self._gaps.append(gap)
                self.pace = min(max(self._gaps), SLOWEST_PACE_S)
        self.at = at

    def window(self) -> float | None:
        """How long the stream may bring nothing before it is quiet: five of its pace, a second
        at least (`STALE_PERIODS`, `STALE_FLOOR_S`), or None while no pace is known."""
        return None if self.pace is None else max(STALE_FLOOR_S, STALE_PERIODS * self.pace)


def _listen_s(pace: float) -> float:
    """How long a stop listens on a stream of `pace`: `LISTEN_PERIODS` of it, `LISTEN_FLOOR_S` at
    least, and so `LISTEN_PERIODS` of `SLOWEST_PACE_S` at most."""
    return max(LISTEN_FLOOR_S, LISTEN_PERIODS * pace)


def _off(value: str) -> str | None:
    return None if value == OFF else value


def param_text(value: Any) -> str:
    """A parameter arrives JSON-encoded (`upstream_api.PARAM_VALUE_JSON`), so a description is
    a quoted string. A bridge that sends it raw is taken at its word rather than refused. Public,
    since the industrial arm family reads a description off a bridge the same way."""
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


class ActionLike(Protocol):
    """The slice of `roslibpy.ActionClient` a client of an action uses: a goal sent, which
    returns its id at once and answers through the three callbacks on roslibpy's thread, and a
    goal cancelled by that id (`upstream_api.ACTION_SEND_GOAL`, `ACTION_CANCEL_GOAL`)."""

    def send_goal(
        self,
        goal: dict[str, Any],
        resultback: Callable[[Any], None],
        feedback: Callable[[Any], None],
        errback: Callable[[Any], None],
    ) -> Any: ...

    def cancel_goal(self, goal_id: Any) -> Any: ...


TopicFactory = Callable[[Any, str, str], TopicLike]
ServiceFactory = Callable[[Any, str, str], ServiceLike]
ActionFactory = Callable[[Any, str, str], ActionLike]


@dataclass(frozen=True)
class Roslibpy:
    """roslibpy as a transport here uses it: one connection, and a factory for each of the three
    things made over it. A family over this binding (`roboglot_rosarm`) builds the same four."""

    ros: Any
    topic: TopicFactory
    service: ServiceFactory
    action: ActionFactory
    version: str | None


def build_roslibpy(
    host: str,
    port: int,
    secure: bool,
    *,
    adapter: str = "rosbridge",
    extra: str = "roboglot[rosbridge]",
) -> Roslibpy:
    """The connection and its factories, with roslibpy imported here and nowhere earlier, so an
    install without it is told which extra to add by the adapter that needed it. The `Ros` is
    built and not yet run: its constructor starts connecting (`upstream_api.ROS_CLIENT`), and
    `run()` is what waits for it."""
    try:
        import roslibpy
    except ImportError as e:
        raise AdapterNotInstalled(adapter, extra) from e
    ros = roslibpy.Ros(host, port, is_secure=secure)

    def topics(client: Any, name: str, message_type: str) -> TopicLike:
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

    def actions(client: Any, name: str, action_type: str) -> ActionLike:
        action = roslibpy.ActionClient(client, name, action_type)

        class _Goals:
            def send_goal(
                self,
                goal: dict[str, Any],
                resultback: Callable[[Any], None],
                feedback: Callable[[Any], None],
                errback: Callable[[Any], None],
            ) -> Any:
                return action.send_goal(roslibpy.Goal(goal), resultback, feedback, errback)

            def cancel_goal(self, goal_id: Any) -> Any:
                return action.cancel_goal(goal_id)

        return _Goals()

    return Roslibpy(ros, topics, services, actions, getattr(roslibpy, "__version__", None))


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


def _same_type(listed: str, wanted: str) -> bool:
    """Whether a type the bridge lists is `wanted`, in either form a bridge resolves
    (`up.TYPE_STRING_FORMS`): `geometry_msgs/msg/Twist` or `geometry_msgs/Twist`."""
    return str(listed).replace("/msg/", "/") == wanted.replace("/msg/", "/")


def command_topic(name: str, topics: dict[str, str]) -> tuple[str | None, str | None]:
    """What a topic list read off the bridge says of `name`, the topic Roboglot publishes its
    Twist on: why it is not driven, or None, and what to say of it, or None.

    A list that names no topic at all was not read, or says nothing, and nothing is held
    against it, so a bridge without rosapi drives as before. Listed is no proof that a base
    subscribes, only that somebody on the bridge has the topic."""
    if not topics:
        return None, None
    twist = up.MSG_TWIST.name
    twists = sorted(t for t, kind in topics.items() if _same_type(kind, twist))
    found = f"the Twist topics it lists: {', '.join(twists) or 'none'}"
    listed = topics.get(name)
    if listed is None:
        why = (
            f"the bridge lists no {name}, so Roboglot sends no Twist there ({found}); name the "
            "base's own in the address, as ?cmd_vel=/its/topic"
        )
        return why, why
    if not _same_type(listed, twist):
        why = (
            f"the bridge lists {name} as {listed}, and Roboglot publishes only {twist}, so it "
            f"sends no Twist there ({found})"
        )
        return why, why
    others = [t for t in twists if t != name]
    if not others:
        return None, None
    more = "topic" if len(others) == 1 else "topics"
    return None, (
        f"Roboglot drives {name}, the topic the address names; the bridge lists {len(others)} "
        f"more Twist {more} it does not drive: {', '.join(others)}"
    )


def decode_compressed_image(message: dict[str, Any]) -> Image.Image:
    """A `sensor_msgs/msg/CompressedImage` (data base64 over rosbridge) to RGB."""
    fmt = str(message.get("format", "")).lower()
    data = message.get("data", "")
    raw = base64.b64decode(data) if isinstance(data, str) else bytes(data)
    if not any(codec in fmt for codec in ("jpeg", "jpg", "png")) and fmt:
        raise ValueError(f"unsupported CompressedImage format {fmt!r} (jpeg or png only)")
    # the same two the check above takes, since the bytes need not be what `format` says
    img: Image.Image = Image.open(io.BytesIO(raw), formats=["JPEG", "PNG"])
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
        self._image_at: float | None = None
        """When the newest image arrived, on this machine's clock, under the lock."""
        self._image_period: float | None = None
        """How long the newest image came after the one before it: the stream's own period."""
        self._closed = False
        self._t0 = time.monotonic()
        self.last_twist = (0.0, 0.0, 0.0)
        self.published = 0
        self.roslibpy_version: str | None = None
        self.stop_error: str | None = None
        """Why the last stop, or the zero Twist `close()` sends, did not reach the base or is not
        known to have, or None. A stop never raises: the core verbs and the executor read this."""
        self.stop_unconfirmed = False
        """Whether that stop's zero Twist went out, and only whether it arrived is not known."""
        self.close_note: str | None = None
        """What the close has to say, which a run reads after it: a zero Twist it could not send."""
        self._arrivals = _Pace()
        """Messages on every topic this transport subscribes: the link's own pace, under the
        lock."""
        self._odom_arrivals = _Pace()
        """Messages on /odom alone: the base's own pace, the one sign of the base itself, where
        a camera or an answer from the bridge vouches only for the link, under the lock."""
        self._asked_at: float | None = None
        """When the newest request for the topic list went out (`_ask_in_background`)."""
        self._asks: set[asyncio.Task[Any]] = set()
        """Requests for the topic list still out, each in a task of its own, ended at the close."""
        self._listeners: list[tuple[threading.Event, bool]] = []
        """Stops listening after their zero Twist (`_hears_within`): for any sign of life, or,
        where the second is true, for /odom alone."""
        self._answered_at: float | None = None
        """When the bridge last answered a request, in any words, or None while it has given no
        answer, so has no round trip to be asked by (`_ask_topics`)."""
        self._unanswered: str | None = None
        """Why the heartbeat's last round trip went unanswered, or None."""
        self.said_no: str | None = None
        """The last answer to a round trip that was a failure, as a bridge whose rosapi node is
        restarting gives: still an answer, so still a sign of life."""
        self._silent_beats = 0
        """Heartbeats in a row that found no sign of life (`SILENT_BEATS`)."""
        self._refused: str | None = None
        """Why the command topic is not driven, from the newest topic list read off the bridge
        (`command_topic`), or None while it is driven."""
        self._command_said: str | None = None
        """What that list said of the command topic, said in every look at the bridge after it."""

    @property
    def camera_available(self) -> bool:
        return self.endpoint.image is not None

    # ── plumbing ────────────────────────────────────────────────────────────────────

    def _build(self) -> tuple[Any, TopicFactory, ServiceFactory]:
        built = build_roslibpy(self.endpoint.host, self.endpoint.port, self.endpoint.secure)
        self.roslibpy_version = built.version
        return built.ros, built.topic, built.service

    def _on_odom(self, message: dict[str, Any]) -> None:  # roslibpy's thread
        at = time.monotonic()
        with self._lock:
            self._odom_arrivals.heard(at)
            self._heard(at, base=True)
            self._latest_odom = dict(message)
            self._feed_odom(self._latest_odom, at)

    def _heard(self, at: float, *, base: bool = False) -> None:
        """A message came over the link at `at`, under the lock, from the base itself where
        `base` (/odom): a sign of life, which wakes a stop listening for one, and a gap since the
        one before it (`_Pace`). The pace is the slowest of the last few gaps, what the stream
        does now: kept as the slowest ever, one pause the link came back from widened its window
        for the rest of the connection, and a dead link ran unseen, and stops into it said
        `stopped`, for five times that pause."""
        self._arrivals.heard(at)
        self._wake(base=base)

    @property
    def _heard_at(self) -> float | None:
        """When a message last came on any topic this transport subscribes."""
        return self._arrivals.at

    @property
    def _heard_gap(self) -> float | None:
        """The link's streams' pace now (`_Pace`)."""
        return self._arrivals.pace

    def _wake(self, *, base: bool = False) -> None:
        """Tell the stops listening after their zero Twist that the link is alive, and, where
        `base`, those listening for /odom that the base is, under the lock."""
        for listener, base_only in self._listeners:
            if base or not base_only:
                listener.set()

    def _window(self) -> float | None:
        """How long the link may bring nothing before it may be gone: five of its streams' pace
        now, a second at least (`STALE_PERIODS`, `STALE_FLOOR_S`), and never more than five of
        the slowest pace taken (`SLOWEST_PACE_S`), or None while no pace is known."""
        with self._lock:
            return self._arrivals.window()

    def _base_quiet(self) -> str | None:
        """Why the base itself may not have taken a zero Twist the link carried, or None: /odom
        had a pace and has brought nothing for longer than its window (`_Pace.window`). A base
        that never had one is not judged by it."""
        with self._lock:
            at, window = self._odom_arrivals.at, self._odom_arrivals.window()
        if at is None or window is None or self.endpoint.odom is None:
            return None
        age = time.monotonic() - at
        if age <= window:
            return None
        return (
            f"{self.endpoint.odom} has brought nothing for {age:.1f} s, longer than the "
            f"{window:.1f} s its pace gives it"
        )

    def _quiet(self) -> str | None:
        """Why the link's own streams say it may be gone, or None: nothing has come on any topic
        this transport subscribes for longer than its window (`_window`). None too while no gap
        is known, as before a second message: a stream is not judged before its pace is, and
        one slower than the floor was otherwise quiet between every two of its first three
        messages."""
        with self._lock:
            at = self._heard_at
        window = self._window()
        if at is None or window is None:
            return None
        age = time.monotonic() - at
        if age <= window:
            return None
        on = ", ".join(t for t in (self.endpoint.odom, self.endpoint.image) if t)
        return (
            f"nothing has come over it for {age:.1f} s, on {on}, longer than the {window:.1f} s "
            "its streams are given"
        )

    def _feed_odom(self, message: dict[str, Any], at: float) -> None:
        """One /odom into the track as it arrives, under the lock, so the track measures the
        stream's own period rather than how often Roboglot reads it, which can be seconds apart
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
        window = max(STALE_FLOOR_S, STALE_PERIODS * period) if period else STALE_FLOOR_S
        age = time.monotonic() - at
        return f"no new {self.endpoint.odom} for {age:.1f} s" if age > window else None

    def _on_image(self, message: dict[str, Any]) -> None:  # roslibpy's thread
        at = time.monotonic()
        with self._lock:
            self._heard(at)
            if self._image_at is not None:
                self._image_period = at - self._image_at
            self._latest_image, self._image_at = dict(message), at

    def _image_stale(self) -> str | None:
        """Why the newest image is too old to hand out, or None: older than four of the stream's
        own periods, and two seconds at least (`FRAME_STALE_FLOOR_S`), counted from its arrival."""
        with self._lock:
            at, period = self._image_at, self._image_period
        if at is None:
            return None
        window = FRAME_STALE_FLOOR_S
        if period is not None:
            window = max(FRAME_STALE_FLOOR_S, FRAME_STALE_PERIODS * period)
        age = time.monotonic() - at
        if age <= window:
            return None
        return (
            f"the newest image on {self.endpoint.image} is {age:.1f} s old, older than the "
            f"{window:.1f} s Roboglot steers on"
        )

    @property
    def camera_error(self) -> str | None:
        """Why `get_frame` hands out no image although one came: it is too old (`_image_stale`).
        `observe`, `go_to` and `search_scan` read it off what they were handed when a frame does
        not come, so the camera going quiet is said rather than a frame that never came."""
        return None if self._image is None else self._image_stale()

    # ── protocol ────────────────────────────────────────────────────────────────────

    async def connect(self) -> None:
        self._closed = False
        self._answered_at, self._unanswered, self._silent_beats = None, None, 0
        self._refused, self._command_said, self.close_note = None, None, None
        self._asked_at = None
        with self._lock:
            self._arrivals.clear()
            self._odom_arrivals.clear()
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
            # it judges the command topic too, before anything is published (`_judge`)
            self.introspection = await self.introspect()
        except Exception as e:
            # a bridge that will not describe the body is still a bridge that drives it; the
            # datasheet says nothing was discovered and the pilot declines what hinges on it
            self.introspection = Introspection.from_urdf(
                None, errors=(f"introspection failed: {type(e).__name__}: {e}",)
            )

    def _judge(self, topics: dict[str, str]) -> None:
        """Hold the command topic against a topic list read off the bridge (`command_topic`),
        each time one is read: at connect, whenever `introspect` reads the bridge again, and
        when a move would be refused. Judged once, a base driver that came up after the connect
        was refused for the whole session. An empty or unread list judges nothing. Before
        Roboglot's first publish on the topic the list says what the bridge has; after it, the
        topic is listed by Roboglot's own advertise, so a topic that went is seen as gone only
        while Roboglot has sent nothing on it."""
        if topics:
            self._refused, self._command_said = command_topic(self.endpoint.cmd_vel, topics)

    async def _ask_topics(
        self, seconds: float, errors: list[str] | None = None
    ) -> tuple[dict[str, str], bool]:
        """The bridge's topic list, by type, asked for within `seconds`, and whether the bridge
        answered at all: a failed answer, as a bridge gives at once while its rosapi node
        restarts, is an answer, and only no answer within the deadline is silence. An answer
        is a sign of life (`_answered_at`). Trouble goes into `errors` in `_bounded`'s words.
        Never raises."""
        name = up.SVC_TOPICS.name
        listing = self._service(name, up.SRV_TOPICS.name)
        loop = asyncio.get_running_loop()
        asked = loop.time()
        try:
            answer = await asyncio.wait_for(
                asyncio.to_thread(listing.call, {}, timeout=seconds), seconds
            )
        except TimeoutError:
            if errors is not None:
                errors.append(f"{name}: no answer within {seconds:.0f} s")
            self._unanswered = f"{name}: no answer within {seconds:g} s"
            return {}, False
        except Exception as e:
            if errors is not None:
                errors.append(f"{name}: {type(e).__name__}: {e}")
            if loop.time() - asked >= seconds:
                # the client's own deadline, at the same moment as this one's: no answer
                self._unanswered = f"{name}: no answer within {seconds:g} s ({e})"
                return {}, False
            self.said_no = f"{name}: {type(e).__name__}: {e}"
            answer = None
        self._answered_at, self._unanswered = time.monotonic(), None
        with self._lock:
            self._wake()  # an answer is a sign of life to a stop listening for one
        if not isinstance(answer, dict):
            return {}, True
        names, types = answer.get("topics") or [], answer.get("types") or []
        return dict(zip(names, types, strict=False)), True

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

        topics, _answered = await self._ask_topics(left(), errors)
        self._judge(topics)

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
                elif text := param_text(answer.get("value")):
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
        if self._command_said is not None:  # what the newest list said of it (`_judge`)
            intro = replace(intro, errors=(*intro.errors, self._command_said))
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
        """Send the zero Twist and let go of this client. A zero that cannot go out is recorded
        in `stop_error`: the client closes as one that asked to, which roslibpy does not
        reconnect, so a zero held for the socket would never be sent. Nothing reads `stop_error`
        after a close, so it goes into `close_note` too, which a run reads and says after it.
        The close does not listen after its zero, as a stop does: it lets go of the client at
        once, and says the zero is not known to have arrived where the link was quiet already."""
        self._closed = True
        if self._zero() and (unheard := self._unheard()) is not None:
            self._not_known(unheard)
        self.close_note = f"{CLOSE_STOP_NOTE}{self.stop_error}" if self.stop_error else None
        for ask in list(self._asks):
            ask.cancel()
        for topic in (self._odom, self._image):
            if topic is not None:
                with contextlib.suppress(Exception):
                    topic.unsubscribe()
        if self._cmd is not None:
            with contextlib.suppress(Exception):
                self._cmd.unadvertise()
        if self._ros is not None:
            # this client's own socket and nothing more: `terminate()` stops the one reactor every
            # roslibpy client in the process runs on (`up.ONE_REACTOR`), so a base whose run ended
            # first took an industrial arm's link, or another base's, down with it in a team
            with contextlib.suppress(Exception):
                await asyncio.to_thread(self._ros.close, self.timeout_s)

    def _publish_twist(self, vx: float, vy: float, wz: float) -> None:
        if self._cmd is None:
            raise TransportError("rosbridge ws: not connected")
        if (down := self._link_down()) is not None:
            raise TransportError(down)
        self.last_twist = (vx, vy, wz)
        self.published += 1
        self._cmd.publish(twist_message(vx, vy, wz))

    def _link_down(self) -> str | None:
        """Why no Twist can go out now, or None while roslibpy reads the socket as up.

        roslibpy raises nothing for a publish with the socket down: it holds the message for the
        socket to come back and sends it then (`up.QUEUED_UNTIL_READY`), when nobody is watching
        the base, or drops it with a client that was asked to close. So the link is read before
        every publish, as the industrial arm over this binding reads it before a goal. A socket
        that reads up can still be half open; the heartbeat watches for that."""
        if self._ros is None or not bool(self._ros.is_connected):
            return f"rosbridge at {self.endpoint.url} is not connected"
        return None

    def _zero(self) -> bool:
        """Publish the zero Twist, and say whether it went out; one that did not says why in
        `stop_error`. Never raises. A client never connected has sent nothing, owes no zero, and
        has nothing to say. On a command topic the bridge listed otherwise (`command_topic`)
        nothing goes out, and that is the reason. Whether a zero that went out arrived is the
        caller's to judge (`stop`, `close`)."""
        self.stop_error, self.stop_unconfirmed = None, False
        if self._cmd is None:
            return False
        if self._refused is not None:
            self.stop_error = self._refused
            return False
        try:
            self._publish_twist(0.0, 0.0, 0.0)
        except TransportError as e:
            why = str(e)
        except Exception as e:
            why = f"the publish raised {type(e).__name__}: {e}"
        else:
            return True
        self.stop_error = (
            f"{why}, so the zero Twist was not sent, and the base, which has no deadman, keeps "
            "the last Twist that reached it"
        )
        return False

    def _not_known(self, why: str) -> None:
        """Say that the zero Twist went out into a socket that may be half open, so is not known
        to have reached the base, and is not said to have failed to go (`stop_unconfirmed`)."""
        self.stop_unconfirmed = True
        self.stop_error = (
            f"rosbridge at {self.endpoint.url} reads connected, but {why}, so the zero Twist is "
            "not known to have reached the base, which has no deadman and keeps its last Twist "
            "if it did not"
        )

    async def _hears_within(self, seconds: float, *, base_only: bool = False) -> bool:
        """Whether a message or an answer comes over the link within `seconds` from now, or, where
        `base_only`, a message on /odom, waited for in a worker thread that roslibpy's own thread
        wakes (`_wake`)."""
        heard = threading.Event()
        listening = (heard, base_only)
        with self._lock:
            self._listeners.append(listening)
        try:
            return await asyncio.to_thread(heard.wait, seconds)
        finally:
            with self._lock:
                self._listeners.remove(listening)

    def _bridge_took_it(self, why: str) -> None:
        """Say that the bridge was heard from after the zero Twist went out, so took it, and
        that the base, whose /odom is quiet (`why`), is not known to have (`stop_unconfirmed`)."""
        self.stop_unconfirmed = True
        self.stop_error = (
            f"rosbridge at {self.endpoint.url} was heard from after the zero Twist went out, so "
            f"the bridge took it, but {why}, so the base is not known to have taken it, and it has "
            "no deadman and keeps its last Twist if it did not"
        )

    def _ask_in_background(self) -> None:
        """Ask the bridge for its topic list in a task of its own, never waited on by whoever
        asks: an answer within the transport's own request deadline is a sign of life when it
        comes (`_ask_topics`), and none is the silence a beat counts. Ended at the close."""
        self._asked_at = time.monotonic()
        task = asyncio.ensure_future(self._ask_for_life())
        self._asks.add(task)
        task.add_done_callback(self._asks.discard)

    async def _ask_for_life(self) -> None:
        """One request for the topic list, for its answer alone (`_ask_in_background`)."""
        try:
            await self._ask_topics(self.timeout_s)
        except TransportError as e:  # no client to ask through: nothing answers
            self._unanswered = str(e)

    def _unheard(self) -> str | None:
        """Why a stop cannot vouch for the link, or None: its streams have gone quiet (`_quiet`)
        and no answer has come within the floor either. Judged by what is heard and nothing kept
        from a beat: a message, or an answer, clears it, so a stop after the link came back is
        not judged by a heartbeat long gone, and with no stream to go by there is nothing to say."""
        quiet = self._quiet()
        if quiet is None:
            return None
        answered = self._answered_at
        if answered is not None and time.monotonic() - answered <= STALE_FLOOR_S:
            return None
        return quiet

    async def get_frame(self) -> Image.Image | None:
        """The newest image, or None: no image topic, none yet, one that will not decode, or one
        too old to steer on (`_image_stale`), which `camera_error` says."""
        if self._image is None:
            return None
        with self._lock:
            message = self._latest_image
        if message is None or self._image_stale() is not None:
            return None
        try:
            return decode_compressed_image(message)
        except (ValueError, OSError, Image.DecompressionBombError):
            # `frames_of` promises every caller this never raises for a camera that failed
            return None

    async def get_state(self) -> RobotState:
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
        return RobotState(
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
        """What Roboglot is standing in for, in the robot's own observation. A description read
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
        if intent.kind in ("move", "stop") and self._cmd is not None:
            if self._refused is not None and intent.kind == "move":
                # judged when it is needed: a base driver that has come up since is driven
                self._judge((await self._ask_topics(self.timeout_s))[0])
            if self._refused is not None:
                return Ack(accepted=False, reason=self._refused)
            if (down := self._link_down()) is not None:
                # held for the socket, it would go out later with nobody watching the base
                return Ack(accepted=False, reason=f"{down}, so no Twist is sent")
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
        """Fail when the link is closed, reads down, or has given no sign of life for
        `SILENT_BEATS` beats in a row, and on nothing less.

        A link that dies without a close, as one over Wi-Fi does, leaves roslibpy reading the
        socket as up until TCP gives up, which can be minutes, and every Twist and every stop
        goes into it as sent while the base, which has no deadman, drives on. So the link has to
        show it is alive. A sign of life is any message on a topic this transport subscribes, or
        any answer from the bridge, a failed one included.

        With a stream whose pace is known, the link is alive while something has come within
        its window, five of its pace now, a second at least, ten at most (`_window`), and a beat
        past it is silent, whether or not the bridge has rosapi: a quiet beat asks the bridge
        for its topic list in the background, and an answer, whenever it comes, is life, so a
        stream that stopped over a link that answers keeps its run, but no beat waits on it,
        and a link that is gone is noticed as soon with rosapi as without. A stream is not
        judged before its pace is known, so a base whose /odom comes once every two seconds is
        not quiet between its first messages.

        With none, on a bridge that answered at connect, a beat asks once the last sign of life
        is a floor old, and a beat is silent only once its ask has gone unanswered `ANSWER_WAIT_S`,
        asking again; a base that publishes no /odom, or publishes it on a topic the address
        does not name, is watched so and still driven. With neither, nothing but the socket's
        own state watches the link. One beat without a sign is a slow link, and fails nothing."""
        if self._closed:
            raise HeartbeatError("rosbridge ws transport is closed")
        if (down := self._link_down()) is not None:
            raise HeartbeatError(down)
        now = time.monotonic()
        with self._lock:
            heard = self._heard_at
        answered = self._answered_at
        lives = [t for t in (heard, answered) if t is not None]
        life = max(lives) if lives else None
        window = self._window()
        if window is not None:
            if life is not None and now - life <= window:
                self._silent_beats = 0
                return
            silent = self._quiet() or f"nothing has come over it for more than {window:.1f} s"
            if answered is not None and not self._asks:
                self._ask_in_background()  # its answer, whenever it comes, is life
        elif answered is None or life is None:
            return  # nothing to go by but the socket, which reads up
        else:
            if now - life <= STALE_FLOOR_S:
                self._silent_beats = 0
                return
            asked = self._asked_at
            if asked is None or asked <= life:
                self._ask_in_background()
                return  # asked now: no verdict before it has had its time
            if now - asked <= ANSWER_WAIT_S:
                return
            silent = (
                f"the bridge did not answer a request for its topic list ({up.SVC_TOPICS.name}) "
                f"within {ANSWER_WAIT_S:g} s"
            )
            if len(self._asks) < SILENT_BEATS:
                self._ask_in_background()  # once more: an answer lost is not a link gone
        self._silent_beats += 1
        if self._silent_beats >= SILENT_BEATS:
            raise HeartbeatError(
                f"rosbridge at {self.endpoint.url} reads connected, but {silent}, "
                f"{self._silent_beats} beats in a row, so the link may be gone"
            )

    async def stop(self) -> None:
        """Publish the zero Twist, the only stop this base has (`up.NO_DEADMAN`), and say
        whether it arrived as far as the link can tell. Never raises: one that did not go out,
        or is not known to have arrived, says so in `stop_error` and `stop_unconfirmed`, which
        the stop verb and the executor read.

        A window cannot tell a stop sent just after the link died from one sent just before it,
        so a stop on a link whose streams have a pace listens after its zero Twist for a sign of
        life (`_listen_s`): `LISTEN_PERIODS` of that pace, `LISTEN_FLOOR_S` at least and twice
        `SLOWEST_PACE_S` at most. A message, or an answer to the ask it sends where the bridge
        answers, and the zero is said to have landed; nothing, and it went out and is not known
        to have reached the base. It goes out at once all the same, and the listening is what a
        stop costs here: up to a second on a link that has gone quiet, four at the slowest pace
        taken, and on a live one until its next message or the answer.

        Where /odom had a pace and has gone quiet past its window, only /odom vouches for the
        base: a base's driver can die while the bridge and rosapi run on, and an answer, or a
        camera's image, says the zero reached the bridge and nothing of the base. So the stop
        listens for /odom alone, and with only the bridge heard says the bridge took the zero
        and the base is not known to have (`_bridge_took_it`). A base that never had /odom, or
        has none, is vouched for by its link, and one with no stream at all by its socket, as it
        always was."""
        if not self._zero():
            return
        sent_at = time.monotonic()
        with self._lock:
            pace, odom_pace = self._arrivals.pace, self._odom_arrivals.pace
        if pace is None:
            return
        base = self._base_quiet()
        if self._answered_at is not None and not self._asks:
            self._ask_in_background()
        if base is None:
            listen = _listen_s(pace)
            if await self._hears_within(listen):
                return
        else:
            listen = _listen_s(odom_pace if odom_pace is not None else pace)
            if await self._hears_within(listen, base_only=True):
                return
            with self._lock:
                heard = self._arrivals.at
            lives = [t for t in (heard, self._answered_at) if t is not None]
            if lives and max(lives) >= sent_at:
                self._bridge_took_it(base)
                return
        on = ", ".join(t for t in (self.endpoint.odom, self.endpoint.image) if t)
        self._not_known(
            f"nothing has come over it, on {on}, in the {listen:.1f} s after the zero Twist "
            "went out"
        )

    def now(self) -> float:
        return time.monotonic() - self._t0

    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(seconds)


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
