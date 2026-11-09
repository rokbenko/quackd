"""`RosArmWs`: an industrial arm's own ROS 2 driver, reached through rosbridge by roslibpy.

The only code in this package that sends anything. Every rosbridge and roslibpy name is the
binding's (`quackd_rosbridge.upstream_api`, imported by name into this package's), every driver
name is the body record's (`bodies.py`), and the address carries what a stock bringup does not:

    ws://HOST:9090?action=/scaled_joint_trajectory_controller/follow_joint_trajectory
        &joint_states=/joint_states&prefix=&urdf_param=/robot_state_publisher:robot_description
        &urdf_topic=/robot_description&bridge_node=/rosbridge_websocket&status=on

Every key defaults from the body record, so `ws://cell.local:9090` is enough for a stock bringup.

At connect each check is a refusal that says what it found: the bridge must run each goal on a
thread of its own, or a cancel would wait until the move had ended (`up.GOAL_THREAD_PARAM`); it
must list the action; the action's own status, where the controller publishes one, must show no
goal still running, another client's or one an earlier run left going (`up.ACTION_STATUS_TOPIC`);
the first `/joint_states` must name every joint of the body; and the description must give every
joint a travel and a speed, since a goal there is no travel to check against is one quackd does
not send.

It sends two operations and nothing else: `send_action_goal` with a FollowJointTrajectory goal,
and `cancel_action_goal`. No service that powers an arm, turns its servos on, releases its
brakes, clears an alarm, unlocks a protective stop, plays a program or moves a driver through its
lifecycle: the person at the cell brings the arm up, and quackd waits until the driver says it can
move. It reads `action_result`, `action_feedback` (only as the goal having started),
`/joint_states` and the driver's status, each kept as the latest under one lock, since roslibpy
hands them over on a thread of its own.

The clock is handed in: the wall's on `ws`, the fake's on `mock` (`fake.FakeClock`), so a whole
task runs in CI in fake time through every line here, the roslibpy calls included.
"""

from __future__ import annotations

import asyncio
import contextlib
import math
import threading
import time
from collections.abc import AsyncIterator, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol
from urllib.parse import parse_qs, urlsplit

from PIL import Image

from quackd.transport.base import Ack, DuckState, HeartbeatError, Intent, TransportError
from quackd_rosarm import upstream_api as up
from quackd_rosarm.bodies import ArmBody
from quackd_rosbridge.introspection import UrdfJoint, UrdfSummary, parse_urdf
from quackd_rosbridge.upstream_api import DESCRIPTION_PARAM
from quackd_rosbridge.ws import (
    ActionLike,
    Roslibpy,
    ServiceLike,
    TopicLike,
    build_roslibpy,
    param_text,
)

DEFAULT_ADDRESS = "ws://localhost:9090"
DEFAULT_BRIDGE_NODE = up.BRIDGE_NODE_PARAMS.name
ACTION_TYPE = up.MSG_FOLLOW_JOINT_TRAJECTORY.name
OFF = "off"
KEYS = ("action", "joint_states", "prefix", "urdf_param", "urdf_topic", "bridge_node", "status")
"""What the address may say. A key it does not know is refused: a typo read as nothing would put a
stock bringup's default in front of an arm somebody configured otherwise."""

TIMEOUT_S = 5.0
"""How long each check at connect may take, and the first reading."""
POLL_S = 0.05
"""How often a wait looks again: for a reading, for a result, for a cancel's answer."""
STATUS_WAIT_S = 0.2
"""How long the connect waits for the driver's first word about itself. A latched or a fast status
answers inside it; one published only on change may say nothing at all, which is no fault."""
ACTION_STATUS_WAIT_S = 1.0
"""How long the connect listens on the action's own status before it takes the action to be
running nothing. The server latches the last array it published, so a bridge hands it over on
subscribing (`up.ACTION_STATUS_TOPIC`), and a controller that has had no goal since it started
has published none, which is no fault (`up.ACTION_STATUS_HEARD`)."""
RUNNING_STATUSES = {1: "accepted", 2: "executing", 3: "canceling"}
"""`up.MSG_GOAL_STATUS`: the statuses of a goal that has not ended."""
LIMIT_SLACK_RAD = math.radians(1e-5)
"""How far past the description's own radians a goal may lie and still be sent, held to the limit:
the travel is read to a millionth of a degree before its tenth is published inward, so a goal at
the published edge can lie a hair outside the radians. Anything further is refused."""
CANCEL_WAIT_S = 2.0
"""How long `stop()` waits for the cancelled goal's result before it says the stop could not be
delivered. On the stock controllers a cancel is answered as soon as the bridge reaches it."""
STALE_PERIODS = 5
STALE_FLOOR_S = 0.5
"""A reading is stale once no new one has come for five of the stream's own periods, half a second
at least: the base's `/odom` rule with a smaller floor, since these drivers publish at a hundred
times a second or more."""

UR_MOVING_MODES = {1: "NORMAL", 2: "REDUCED"}
UR_STOPPED_MODES = {
    3: "PROTECTIVE_STOP",
    4: "RECOVERY",
    5: "SAFEGUARD_STOP",
    6: "SYSTEM_EMERGENCY_STOP",
    7: "ROBOT_EMERGENCY_STOP",
    8: "VIOLATION",
    9: "FAULT",
}
"""`up.UR_SAFETY_MODES`: the two a UR moves in, and the ones it stops in by name. Any other mode
is a stop too, said by its number (`up.UR_PROTECTIVE_STOP`)."""
KUKA_EVENTS = {3: "CONTROL_STARTED", 4: "CONTROL_STOPPED", 6: "ERROR"}
KUKA_STOPPED = (4, 6)
"""`up.IISY_EVENT_CODES`."""
TRUE, FALSE = 1, 0
"""`up.TRI_STATE`'s two known values; -1 is UNKNOWN."""

GOAL_STATUS_WORDS = {4: "succeeded", 5: "canceled", 6: "aborted"}
"""`up.MSG_GOAL_STATUS`: how a goal that ran ended, by its status."""


class Clock(Protocol):
    """Time as this transport reads it: the wall's on `ws`, the fake's on `mock`."""

    def now(self) -> float: ...

    async def sleep(self, seconds: float) -> None: ...


class WallClock:
    def __init__(self) -> None:
        self._t0 = time.monotonic()

    def now(self) -> float:
        return time.monotonic() - self._t0

    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(seconds)


@dataclass(frozen=True)
class Endpoint:
    host: str
    port: int
    secure: bool
    action: str
    joint_states: str
    prefix: str
    urdf_param: str | None
    urdf_topic: str | None
    bridge_node: str
    status: bool

    @property
    def url(self) -> str:
        return f"{'wss' if self.secure else 'ws'}://{self.host}:{self.port}"


def parse_address(address: str | None, body: ArmBody) -> Endpoint:
    """The address, every key defaulted from the body record (`up.STOCK_NAMES`)."""
    parts = urlsplit(address or DEFAULT_ADDRESS)
    if parts.scheme not in ("ws", "wss"):
        raise TransportError(f"rosarm address must start with ws:// or wss://, not {address!r}")
    query = {k: v[-1] for k, v in parse_qs(parts.query, keep_blank_values=True).items()}
    unknown = sorted(set(query) - set(KEYS))
    if unknown:
        raise TransportError(
            f"rosarm does not read {', '.join(unknown)} in an address: it reads {', '.join(KEYS)}"
        )
    names = {
        "action": query.get("action", body.action),
        "joint_states": query.get("joint_states", body.joint_states),
        "bridge_node": query.get("bridge_node", DEFAULT_BRIDGE_NODE),
    }
    urdf_topic = query.get("urdf_topic", body.description_topic)
    if urdf_topic != OFF:
        names["urdf_topic"] = urdf_topic
    for key, name in names.items():
        if not name.startswith("/"):
            raise TransportError(f"rosarm {key}={name!r} must start with /")
    param = query.get("urdf_param", body.description_param)
    if param != OFF and (not param.startswith("/") or ":" not in param):
        raise TransportError(
            f"rosarm urdf_param={param!r} must name a node and a parameter, as in "
            f"{DESCRIPTION_PARAM.name}"
        )
    prefix = query.get("prefix", "")
    if "/" in prefix or " " in prefix:
        raise TransportError(
            f"rosarm prefix={prefix!r} is put before each joint's name: no / or space"
        )
    status = query.get("status", "on")
    if status not in ("on", OFF):
        raise TransportError(f"rosarm status={status!r} is on or off")
    return Endpoint(
        host=parts.hostname or "localhost",
        port=parts.port or 9090,
        secure=parts.scheme == "wss",
        action=names["action"],
        joint_states=names["joint_states"],
        prefix=prefix,
        urdf_param=None if param == OFF else param,
        urdf_topic=None if urdf_topic == OFF else urdf_topic,
        bridge_node=names["bridge_node"],
        status=status == "on" and body.status is not None,
    )


def _duration(seconds: float) -> dict[str, int]:
    """`builtin_interfaces/Duration` for a time from the trajectory's start."""
    whole = int(seconds)
    nanosec = round((seconds - whole) * 1e9)
    if nanosec >= 1_000_000_000:
        whole, nanosec = whole + 1, nanosec - 1_000_000_000
    return {"sec": whole, "nanosec": nanosec}


def trajectory_goal(
    names: list[str], start: list[float], goal: list[float], duration_s: float
) -> dict[str, Any]:
    """The one goal shape all four arms take: every joint, two points both at rest.

    The present reading at a time from start of 0 and the goal at `duration_s`, each with zero
    velocities and accelerations and no effort, under a header stamp of zero, which starts it now
    (`up.JTC_STAMP_ZERO`). MotoROS2 wants at least two points, velocities in every point, a zero
    last velocity and a first point where the arm is (`up.MOTO_TWO_POINTS`, `up.MOTO_EVERY_JOINT`,
    `up.MOTO_END_AT_REST`, `up.MOTO_START_POINT`); Jazzy's controller wants a last point at rest
    (`up.JTC_END_AT_REST`); and zero velocities and accelerations make the controller's segment a
    quintic, which starts and stops smoothly. Radians, as the messages are."""
    zeros = [0.0] * len(names)
    return {
        "trajectory": {
            "header": {"stamp": {"sec": 0, "nanosec": 0}, "frame_id": ""},
            "joint_names": list(names),
            "points": [
                {
                    "positions": [float(v) for v in start],
                    "velocities": list(zeros),
                    "accelerations": list(zeros),
                    "effort": [],
                    "time_from_start": _duration(0.0),
                },
                {
                    "positions": [float(v) for v in goal],
                    "velocities": list(zeros),
                    "accelerations": list(zeros),
                    "effort": [],
                    "time_from_start": _duration(duration_s),
                },
            ],
        }
    }


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value) if math.isfinite(value) else None


@dataclass
class Goal:
    """One goal sent, as far as anything has been heard of it."""

    id: Any
    goal_deg: dict[str, float]
    duration_s: float
    sent_at: float
    started: bool = False
    status: str = "running"
    """running, then succeeded, canceled, aborted, ended (any other status), or refused (the bridge
    answered result false)."""
    error_code: int | None = None
    error_string: str = ""
    ended_at: float | None = None
    cancel_sent_at: float | None = None

    @property
    def running(self) -> bool:
        return self.status == "running"

    def record(self, now: float) -> dict[str, Any]:
        return {
            "status": self.status,
            "goal_deg": {k: round(v, 3) for k, v in self.goal_deg.items()},
            "duration_s": round(self.duration_s, 3),
            "elapsed_s": round(
                (self.ended_at if self.ended_at is not None else now) - self.sent_at, 3
            ),
            "started": self.started,
            "error_code": self.error_code,
            "error_string": self.error_string,
            "cancel_sent": self.cancel_sent_at is not None,
        }


@dataclass
class _Driver:
    """What the driver last said about itself."""

    heard: dict[str, Any] = field(default_factory=dict)
    can_move: bool | None = None
    says: str = ""


class RosArmWs:
    """The transport under `rosarm:ws`, and under `rosarm:mock` over a fake bridge (`fake.py`)."""

    mobility = "none"

    def __init__(
        self,
        address: str | None = None,
        *,
        body: ArmBody,
        roslibpy: Roslibpy | None = None,
        clock: Clock | None = None,
        name: str = "ws",
        timeout_s: float = TIMEOUT_S,
        assumptions: tuple[str, ...] = (),
    ) -> None:
        self.name = name
        self.body = body
        self.endpoint = parse_address(address, body)
        self.clock: Clock = clock or WallClock()
        self.timeout_s = timeout_s
        self._roslibpy = roslibpy  # injected on mock; built in connect() on ws
        self._extra_assumptions = assumptions
        self.roslibpy_version: str | None = roslibpy.version if roslibpy is not None else None
        self._lock = threading.RLock()
        """Guards what roslibpy's thread hands over: readings, the driver's status, the goal."""
        self._readings: dict[str, float] = {}
        self._last_names: dict[str, None] = {}
        self._read_at: dict[str, float] = {}
        self._period: float | None = None
        self._last_message_at: float | None = None
        self._driver = _Driver()
        self._driver_stopped: str | None = None
        self._goal: Goal | None = None
        self._actions: ActionLike | None = None
        self._topics: list[TopicLike] = []
        self._connected = False
        self._closed = False
        self.description: UrdfSummary | None = None
        self.travel_deg: dict[str, tuple[float, float]] = {}
        self.speed_deg_s: dict[str, float] = {}
        self._limits_rad: dict[str, tuple[float, float]] = {}
        """Each joint's lower and upper as the description gives them, which every goal sent lies
        inside: the travel published in degrees is rounded, and a goal is not."""
        self.stop_error: str | None = None
        self.post_sleep: Callable[[], None] | None = None

    # ── names ───────────────────────────────────────────────────────────────────────────

    def wire(self, joint: str) -> str:
        """A body joint's name as the controller knows it: behind the address's prefix."""
        return f"{self.endpoint.prefix}{joint}"

    @property
    def wire_joints(self) -> list[str]:
        return [self.wire(joint) for joint in self.body.joints]

    # ── plumbing ────────────────────────────────────────────────────────────────────────

    def _built(self) -> Roslibpy:
        if self._roslibpy is None:
            raise TransportError("rosarm: not connected")
        return self._roslibpy

    async def _call(self, service: str, service_type: str, args: dict[str, Any]) -> dict[str, Any]:
        """One service call with a deadline, raising what went wrong as a sentence."""
        built = self._built()
        caller: ServiceLike = built.service(built.ros, service, service_type)
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(caller.call, args, timeout=self.timeout_s), self.timeout_s + 1.0
            )
        except TimeoutError:
            raise TransportError(f"{service} did not answer within {self.timeout_s:g} s") from None
        except Exception as e:
            raise TransportError(f"{service} failed: {type(e).__name__}: {e}") from e

    def _subscribe(
        self, topic: str, message_type: str, on: Callable[[dict[str, Any]], None]
    ) -> None:
        built = self._built()
        made = built.topic(built.ros, topic, message_type)
        made.subscribe(on)
        self._topics.append(made)

    async def _wait(self, done: Callable[[], bool], seconds: float) -> bool:
        """Look every `POLL_S` on this transport's clock until `done` or the time is up."""
        deadline = self.clock.now() + seconds
        while not done():
            if self.clock.now() >= deadline:
                return False
            await self.clock.sleep(POLL_S)
        return True

    # ── connect ─────────────────────────────────────────────────────────────────────────

    async def connect(self) -> None:
        self._closed = False
        self.stop_error = None
        if self._roslibpy is None:
            self._roslibpy = await asyncio.to_thread(
                build_roslibpy,
                self.endpoint.host,
                self.endpoint.port,
                self.endpoint.secure,
                adapter="rosarm",
                extra="quackd[rosarm]",
            )
            self.roslibpy_version = self._roslibpy.version
        built = self._roslibpy
        try:
            await asyncio.wait_for(
                asyncio.to_thread(built.ros.run, self.timeout_s), timeout=self.timeout_s + 1.0
            )
        except Exception as e:
            raise TransportError(f"rosarm: cannot reach {self.endpoint.url}: {e}") from e
        try:
            await self._check_bridge_threads()
            await self._check_action()
            await self._check_nothing_running()
            await self._first_reading()
            await self._check_description()
            if self.endpoint.status and self.body.status is not None:
                for topic, message_type in self.body.status.topics:
                    self._subscribe(topic, message_type, self._status_handler(topic))
                await self._wait(lambda: bool(self._driver.heard), STATUS_WAIT_S)
            self._actions = built.action(built.ros, self.endpoint.action, ACTION_TYPE)
        except BaseException:
            await self._let_go()
            raise
        with self._lock:
            self._connected = True

    async def _check_bridge_threads(self) -> None:
        """The bridge must give each goal a thread of its own, or a cancel waits until the move
        has ended (`up.GOAL_THREAD_PARAM`, `up.GOAL_THREAD_DEFAULTS`). Asked of the bridge's own
        parameter through rosapi (`up.BRIDGE_NODE_PARAMS`); an answer that cannot be read is
        refused the same way, since a stop quackd cannot vouch for is no stop."""
        name = f"{self.endpoint.bridge_node}:{up.GOAL_THREAD_PARAM.name}"
        why = (
            "start it with send_action_goals_in_new_thread:=true, as Jazzy's launch file does and "
            "Humble's does not"
        )
        try:
            answer = await self._call(
                up.SVC_GET_PARAM.name, up.SRV_GET_PARAM.name, {"name": name, "default_value": ""}
            )
        except TransportError as e:
            raise TransportError(
                f"rosarm: could not read {name} ({e}), so quackd cannot tell whether a stop would "
                f"reach this arm before its move ended; {why}, or name the bridge's node with "
                "?bridge_node="
            ) from None
        value = param_text(answer.get("value")) if answer.get("successful", True) else ""
        said = value.strip().lower()
        if said == "true":
            return
        if said == "false":
            raise TransportError(
                f"rosarm: {self.endpoint.url} runs each action goal on the thread that reads "
                "every message, so a stop would wait until the move had ended (rosbridge_suite "
                f"issue #909); {why}"
            )
        reason = answer.get("reason") or f"it answered {answer.get('value')!r}"
        raise TransportError(
            f"rosarm: could not read {name} ({reason}), so quackd cannot tell whether a stop would "
            f"reach this arm before its move ended; {why}, or name the bridge's node with "
            "?bridge_node="
        )

    async def _check_action(self) -> None:
        answer = await self._call(up.SVC_ACTION_SERVERS.name, up.SRV_ACTION_SERVERS.name, {})
        listed = [str(name) for name in answer.get("action_servers") or []]
        if self.endpoint.action not in listed:
            shown = ", ".join(listed) if listed else "none"
            raise TransportError(
                f"rosarm: the bridge lists no action server {self.endpoint.action}; it lists "
                f"{shown}. Give the controller's with ?action= if it is another"
            )

    async def _check_nothing_running(self) -> None:
        """No goal may be running on the action when quackd connects. One an earlier run left going
        when it went quiet runs on to its end (`up.ONE_MOVE_WHEN_QUIET`), another client's for as
        long as it was given, and a goal quackd sent over either would replace it on a controller
        that takes one (`up.JTC_PREEMPTS`) or wait behind it on MotoROS2 (`up.MOTO_SINGLE_GOAL`).
        Read off the action's own status array, which the controllers behind the UR5e, the iisy
        and the iiwa publish whenever a goal changes state and latch
        (`up.ACTION_SERVER_PUBLISHES_STATUS`). MotoROS2 is read to publish none
        (`up.MOTO_NO_ACTION_STATUS`), so on the MotoMini this hears nothing and refuses nothing."""
        topic = self.endpoint.action + up.ACTION_STATUS_TOPIC.name.removeprefix("<action>")
        box: dict[str, Any] = {}

        def on(message: dict[str, Any]) -> None:  # roslibpy's thread
            with self._lock:
                box["latest"] = dict(message)

        built = self._built()
        made = built.topic(built.ros, topic, up.MSG_GOAL_STATUS_ARRAY.name)
        made.subscribe(on)
        try:
            await self._wait(lambda: bool(box), ACTION_STATUS_WAIT_S)
        finally:
            with contextlib.suppress(Exception):
                made.unsubscribe()
        with self._lock:
            latest = dict(box.get("latest") or {})
        running: list[str] = []
        for entry in latest.get("status_list") or []:
            code = _number(entry.get("status")) if isinstance(entry, Mapping) else None
            if code is not None and int(code) in RUNNING_STATUSES:
                running.append(RUNNING_STATUSES[int(code)])
        if running:
            goals = "a goal" if len(running) == 1 else f"{len(running)} goals"
            raise TransportError(
                f"rosarm: {self.endpoint.action} is still running {goals} sent before quackd "
                f"connected ({', '.join(running)}): another client's, or one an earlier run left "
                "going when it went quiet, so the arm may be moving. quackd sends no goal over a "
                "running one: wait for it to end, or stop it on the pendant, then connect again"
            )

    async def _first_reading(self) -> None:
        self._subscribe(self.endpoint.joint_states, up.MSG_JOINT_STATE.name, self._on_joint_states)
        if await self._wait(self._all_read, self.timeout_s):
            return
        with self._lock:
            heard = dict(self._last_names)
        missing = [name for name in self.wire_joints if name not in self._readings]
        if not heard:
            raise TransportError(
                f"rosarm: nothing on {self.endpoint.joint_states} within {self.timeout_s:g} s"
            )
        raise TransportError(
            f"rosarm: {self.endpoint.joint_states} names {', '.join(heard) or 'no joint'} and not "
            f"{', '.join(missing)}: give the joints' prefix with ?prefix=, or check the robot type"
        )

    def _all_read(self) -> bool:
        with self._lock:
            return all(name in self._readings for name in self.wire_joints)

    async def _check_description(self) -> None:
        """Every body joint needs a travel and a speed from the robot's own description, since a
        goal with no travel to check it against is one quackd will not send. Read off the
        parameter first and the latched topic second, as the base's introspection reads it."""
        errors: list[str] = []
        summary: UrdfSummary | None = None
        if (param := self.endpoint.urdf_param) is not None:
            try:
                answer = await self._call(
                    up.SVC_GET_PARAM.name,
                    up.SRV_GET_PARAM.name,
                    {"name": param, "default_value": ""},
                )
                if answer.get("successful") is False:
                    errors.append(f"{param}: {answer.get('reason') or 'the bridge said no'}")
                elif text := param_text(answer.get("value")):
                    summary = parse_urdf(text)
            except TransportError as e:
                errors.append(str(e))
        if summary is None and (topic := self.endpoint.urdf_topic) is not None:
            latched = await self._read_latched(topic, errors)
            if latched:
                summary = parse_urdf(latched)
        if summary is None or not summary.parsed:
            why = "; ".join([*errors, *(summary.errors if summary else ())]) or "nothing answered"
            raise TransportError(
                f"rosarm: no description of the arm could be read ({why}), so no goal could be "
                "checked against its travel"
            )
        by_name = {joint.name: joint for joint in summary.joints}
        travel: dict[str, tuple[float, float]] = {}
        speed: dict[str, float] = {}
        limits_rad: dict[str, tuple[float, float]] = {}
        for joint in self.body.joints:
            found: UrdfJoint | None = by_name.get(self.wire(joint))
            if found is None or None in (found.lower, found.upper, found.velocity):
                lacks = "is not in it" if found is None else "has no lower, upper and velocity"
                raise TransportError(
                    f"rosarm: {self.wire(joint)} {lacks} in the description, so quackd will not "
                    "send it a goal it cannot check against a travel"
                )
            assert found.lower is not None and found.upper is not None
            assert found.velocity is not None
            limits = (found.lower, found.upper, found.velocity)
            if (
                not all(map(math.isfinite, limits))
                or found.velocity <= 0
                or found.lower > found.upper
            ):
                # `float()` reads nan and inf, and either would take the ceiling away
                raise TransportError(
                    f"rosarm: {self.wire(joint)} has lower {found.lower:g}, upper {found.upper:g} "
                    f"and velocity {found.velocity:g} in the description, which is no travel and "
                    "no rated speed a goal can be held to, so quackd will not send it one"
                )
            # to a millionth of a degree, past which a description's radians carry nothing, and
            # which keeps a limit written in degrees, radians(-30), from publishing as -29.9
            travel[joint] = (
                round(math.degrees(found.lower), 6),
                round(math.degrees(found.upper), 6),
            )
            # never above the stock description's own: an absurd figure in a cell's
            # description would lift the ceiling with it
            stock = self.body.description[self.body.joints.index(joint)].velocity
            speed[joint] = round(math.degrees(min(found.velocity, stock)), 6)
            limits_rad[joint] = (found.lower, found.upper)
        self.description = summary
        self.travel_deg, self.speed_deg_s = travel, speed
        self._limits_rad = limits_rad

    async def _read_latched(self, topic: str, errors: list[str]) -> str | None:
        box: dict[str, Any] = {}

        def on(message: dict[str, Any]) -> None:  # roslibpy's thread
            box.update(message)

        built = self._built()
        made = built.topic(built.ros, topic, up.MSG_STRING.name)
        made.subscribe(on)
        try:
            await self._wait(lambda: bool(box), self.timeout_s)
        finally:
            with contextlib.suppress(Exception):
                made.unsubscribe()
        if not box:
            errors.append(f"nothing published on {topic} within {self.timeout_s:g} s")
            return None
        return str(box.get("data", ""))

    # ── what roslibpy's thread hands over ───────────────────────────────────────────────

    def _on_joint_states(self, message: dict[str, Any]) -> None:  # roslibpy's thread
        """Readings by name, never by place (`up.MSG_JOINT_STATE`); a position that is not a
        finite number is no reading (`up.IISY_NAN_READINGS`)."""
        names = message.get("name") or []
        positions = message.get("position") or []
        at = self.clock.now()
        wanted = set(self.wire_joints)
        with self._lock:
            self._last_names = {str(n): None for n in names}
            for name, value in zip(names, positions, strict=False):
                reading = _number(value)
                if name in wanted and reading is not None:
                    self._readings[str(name)] = reading
                    self._read_at[str(name)] = at
            if self._last_message_at is not None and at > self._last_message_at:
                gap = at - self._last_message_at
                self._period = gap if self._period is None else 0.8 * self._period + 0.2 * gap
            self._last_message_at = at

    def _status_handler(self, topic: str) -> Callable[[dict[str, Any]], None]:
        def on(message: dict[str, Any]) -> None:  # roslibpy's thread
            with self._lock:
                self._driver.heard[topic] = dict(message)
                before = self._driver.can_move
                self._driver.can_move, self._driver.says = self._read_driver()
                stopping = before is not False or self._driver_stopped is not None
                if self._connected and stopping and self._driver.can_move is False:
                    # it stopped itself while quackd watched, which the heartbeat says, in the
                    # words of the latest of the topics that say so
                    self._driver_stopped = self._driver.says

        return on

    def _read_driver(self) -> tuple[bool | None, str]:
        """Whether the driver says the arm can move, and in what words; None where it has said
        nothing that settles it, which is believed (`up.STATUS_TYPES_LOADED`)."""
        status = self.body.status
        if status is None:
            return None, ""
        heard = self._driver.heard
        if status.reader == "ur":
            running_topic, mode_topic = (topic for topic, _ in status.topics)
            running = heard.get(running_topic, {}).get("data")
            mode = _number(heard.get(mode_topic, {}).get("mode"))
            words: list[str] = []
            if isinstance(running, bool):
                words.append("program running" if running else "program not running")
            if mode is not None:
                code = int(mode)
                words.append(
                    f"safety mode {UR_MOVING_MODES.get(code) or UR_STOPPED_MODES.get(code) or code}"
                )
            if running is False or (mode is not None and int(mode) not in UR_MOVING_MODES):
                return False, ", ".join(words)
            if running is True and mode is not None:
                return True, ", ".join(words)
            return None, ", ".join(words)
        if status.reader == "kuka_events":
            topic = status.topics[0][0]
            event = _number(heard.get(topic, {}).get("event"))
            if event is None:
                return None, ""
            code = int(event)
            said = f"last event {KUKA_EVENTS.get(code, code)}"
            if code in KUKA_STOPPED:
                return False, said
            return (True if code == 3 else None), said
        robot = heard.get(status.topics[0][0], {})

        def tri(key: str) -> int | None:
            field_ = robot.get(key)
            value = _number(field_.get("val")) if isinstance(field_, Mapping) else None
            return None if value is None else int(value)

        possible, stopped, error = tri("motion_possible"), tri("e_stopped"), tri("in_error")
        if not robot:
            return None, ""
        words = []
        if stopped == TRUE:
            words.append("e-stopped")
        if error == TRUE:
            codes = robot.get("error_codes") or []
            words.append("in error" + (f" ({', '.join(str(c) for c in codes)})" if codes else ""))
        if possible == FALSE:
            words.append("motion not possible")
        if words:
            return False, ", ".join(words)
        if possible == TRUE:
            return True, "motion possible"
        return None, "motion possible unknown"

    def _on_result(self, goal: Goal) -> Callable[[Any], None]:
        def on(result: Any) -> None:  # roslibpy's thread
            values = result.get("values") if isinstance(result, Mapping) else None
            raw = result.get("status") if isinstance(result, Mapping) else None
            code = _number(getattr(raw, "value", raw))
            with self._lock:
                if not goal.running:
                    return
                status = int(code) if code is not None else 0
                goal.status = GOAL_STATUS_WORDS.get(status, "ended")
                if isinstance(values, Mapping):
                    error = _number(values.get("error_code"))
                    goal.error_code = None if error is None else int(error)
                    goal.error_string = str(values.get("error_string") or "")
                if goal.status == "ended":
                    goal.error_string = goal.error_string or f"the goal ended with status {status}"
                goal.ended_at = self.clock.now()

        return on

    def _on_refusal(self, goal: Goal) -> Callable[[Any], None]:
        def on(failure: Any) -> None:  # roslibpy's thread
            values = failure.get("values") if isinstance(failure, Mapping) else failure
            with self._lock:
                if not goal.running:
                    return
                goal.status = "refused"
                goal.error_string = str(values) if values else "the bridge refused the goal"
                goal.ended_at = self.clock.now()

        return on

    def _on_feedback(self, goal: Goal) -> Callable[[Any], None]:
        def on(_feedback: Any) -> None:  # roslibpy's thread
            with self._lock:
                goal.started = True

        return on

    # ── the protocol ────────────────────────────────────────────────────────────────────

    def staleness(self) -> str | None:
        """Why the readings cannot be trusted now, or None: no new one has come for five of the
        stream's own periods, half a second at least, or a joint has never been read."""
        with self._lock:
            if not self._read_at:
                return f"nothing read on {self.endpoint.joint_states}"
            oldest = min(self._read_at.get(name, -math.inf) for name in self.wire_joints)
            period = self._period
        window = max(STALE_FLOOR_S, STALE_PERIODS * period) if period else STALE_FLOOR_S
        age = self.clock.now() - oldest
        if age > window:
            return f"no new {self.endpoint.joint_states} for {age:.1f} s"
        return None

    def readings_deg(self) -> dict[str, float]:
        with self._lock:
            return {
                joint: math.degrees(self._readings[self.wire(joint)])
                for joint in self.body.joints
                if self.wire(joint) in self._readings
            }

    def _assumptions(self) -> list[str]:
        names = [up.STOCK_NAMES.name, up.BRIDGE_NODE_NAME.name, up.ONE_MOVE_WHEN_QUIET.name]
        if self.body.status is not None:
            names.append(up.STATUS_TYPES_LOADED.name)
        if self.body.controller == "jtc":
            names.append(up.ACTION_STATUS_HEARD.name)
        names += {
            "ur5e": [up.UR_PROTECTIVE_STOP.name],
            "kuka-lbr-iiwa-7-r800": [up.IIWA_STATUS.name, up.IIWA_READINGS.name],
            "yaskawa-motomini": [
                up.MOTO_FIRST_POINT.name,
                up.MOTO_SINGLE_GOAL.name,
                up.MOTO_NO_ACTION_STATUS.name,
            ],
        }.get(self.body.robot_type, [])
        return [*names, *self._extra_assumptions]

    async def get_state(self) -> DuckState:
        now = self.clock.now()
        joints = self.readings_deg()
        with self._lock:
            goal = self._goal
            move = goal.record(now) if goal is not None else None
            can_move, says = self._driver.can_move, self._driver.says
            read = bool(self._driver.heard)
            ages = [now - at for at in self._read_at.values()]
        stale = self.staleness()
        return DuckState(
            t=now,
            policy="moving" if goal is not None and goal.running else "idle",
            posture="unknown",
            fallen=False,
            battery_percent=None,
            holding=False,
            extras={
                "joints": {joint: round(value, 3) for joint, value in joints.items()},
                "state_age_s": round(max(ages), 3) if ages else None,
                "stale": stale,
                "move": move,
                "driver": {"read": read, "can_move": can_move, "says": says},
                "readings": self.body.readings,
                "holds": "nothing: no tool is fitted",
                "assumptions": self._assumptions(),
            },
        )

    async def send_intent(self, intent: Intent) -> Ack:
        match intent.kind:
            case "joint":
                return self._send_goal(intent.params)
            case "stop":
                await self.stop()
                return Ack(accepted=self.stop_error is None, reason=self.stop_error)
            case _:
                return Ack(
                    accepted=False,
                    reason=f"an industrial arm over rosbridge takes a joint goal and a stop, not "
                    f"{intent.kind}",
                )

    def _send_goal(self, params: Mapping[str, Any]) -> Ack:
        positions = params.get("positions")
        duration = _number(params.get("duration_s"))
        if (
            not isinstance(positions, Mapping)
            or duration is None
            or not math.isfinite(duration)
            or duration <= 0
        ):
            return Ack(accepted=False, reason="a joint goal names every joint and a duration")
        missing = [
            joint
            for joint in self.body.joints
            if (value := _number(positions.get(joint))) is None or not math.isfinite(value)
        ]
        if missing:
            return Ack(
                accepted=False,
                reason=f"a goal names every joint, and this one leaves out {', '.join(missing)}",
            )
        if not self._connected or self._actions is None:
            return Ack(accepted=False, reason="rosarm: not connected")
        if not self._link_up():
            # roslibpy would hold the goal until the socket came back and send it then
            # (`up.QUEUED_UNTIL_READY`), when nobody is watching the move
            return Ack(
                accepted=False,
                reason=f"rosbridge at {self.endpoint.url} is not connected, so no goal is sent",
            )
        if (stale := self.staleness()) is not None:
            return Ack(accepted=False, reason=f"the readings are stale: {stale}")
        with self._lock:
            if self._goal is not None and self._goal.running:
                return Ack(
                    accepted=False,
                    reason="a move is still running, and quackd never sends a goal over one",
                )
            start = [self._readings[name] for name in self.wire_joints]
        goal_deg = {joint: float(positions[joint]) for joint in self.body.joints}
        goal_rad: list[float] = []
        for joint in self.body.joints:
            lo, hi = self._limits_rad.get(joint, (-math.inf, math.inf))
            rad = math.radians(goal_deg[joint])
            # past the description by more than the rounding of what was published is no goal
            # quackd sends; inside it, the goal is held to the description's own radians
            if not lo - LIMIT_SLACK_RAD <= rad <= hi + LIMIT_SLACK_RAD:
                return Ack(
                    accepted=False,
                    reason=f"{joint}'s goal of {goal_deg[joint]:g} lies outside the travel the "
                    f"description gives, {math.degrees(lo):g} to {math.degrees(hi):g}",
                )
            goal_rad.append(min(max(rad, lo), hi))
        if (fast := self._too_fast(start, goal_rad, duration)) is not None:
            return Ack(accepted=False, reason=fast)
        message = trajectory_goal(self.wire_joints, start, goal_rad, duration)
        record = Goal(id=None, goal_deg=goal_deg, duration_s=duration, sent_at=self.clock.now())
        with self._lock:
            self._goal = record
        try:
            record.id = self._actions.send_goal(
                message,
                self._on_result(record),
                self._on_feedback(record),
                self._on_refusal(record),
            )
        except Exception as e:
            with self._lock:
                record.status, record.error_string = "refused", f"{type(e).__name__}: {e}"
                record.ended_at = self.clock.now()
            return Ack(accepted=False, reason=f"the goal was not sent: {record.error_string}")
        return Ack()

    def _too_fast(self, start: list[float], goal: list[float], duration: float) -> str | None:
        """The ceiling `move_joints` keeps, held here as well, so that no caller of the transport
        sends a goal faster or longer than the verb would: a tenth of each joint's rated speed at
        the segment's peak, and the longest move a pilot may ask for."""
        from quackd_rosarm.verbs import MOVE_MAX_S, PEAK_OVER_MEAN, SPEED_SHARE

        if duration > MOVE_MAX_S + 1e-6:
            return f"a goal of {duration:g} s is longer than the {MOVE_MAX_S:g} s a move may take"
        for joint, a, b in zip(self.body.joints, start, goal, strict=True):
            rated = self.speed_deg_s.get(joint)
            if rated is None or rated <= 0:
                return f"{joint} has no rated speed, so no goal is sent"
            peak = PEAK_OVER_MEAN * abs(math.degrees(b - a)) / duration
            # a thousandth over, for the figures the manifest hands the verb to three places
            if peak > SPEED_SHARE * rated * 1.001:
                return (
                    f"{joint} would peak at {peak:.1f} degrees a second, past a tenth of its rated "
                    f"{rated:g}"
                )
        return None

    def goal(self) -> dict[str, Any] | None:
        """The last goal's record, as the state carries it."""
        with self._lock:
            return self._goal.record(self.clock.now()) if self._goal is not None else None

    async def stop(self) -> None:
        """Cancel the running goal and wait a bounded time for its CANCELED result. With nothing
        running there is nothing to send: every one of these controllers holds its last point
        (`up.JTC_CANCEL_HOLDS`, `up.MOTO_CANCEL`). When the cancel cannot go, or no answer comes,
        `stop_error` says so, and the core `stop` verb says the stop was not delivered."""
        self.stop_error = None
        with self._lock:
            goal = self._goal
            running = goal is not None and goal.running
        if goal is None or not running:
            return
        # sent again on every stop while the goal runs: a cancel the bridge lost is answered
        # by nobody, and the next stop is the one that has to reach the arm
        if not self._link_up() or self._actions is None:
            self.stop_error = (
                "the bridge is not connected, so no cancel could be sent, and the arm goes on to "
                "the end of the move it was given"
            )
            return
        try:
            self._actions.cancel_goal(goal.id)
        except Exception as e:
            self.stop_error = f"the cancel could not be sent: {type(e).__name__}: {e}"
            return
        with self._lock:
            goal.cancel_sent_at = self.clock.now()
        if not await self._wait(lambda: not goal.running, CANCEL_WAIT_S):
            self.stop_error = (
                f"no answer to the cancel within {CANCEL_WAIT_S:g} s, so the arm may still be "
                "going on to the end of its move"
            )

    def _link_up(self) -> bool:
        if self._closed or self._roslibpy is None:
            return False
        return bool(self._roslibpy.ros.is_connected)

    async def heartbeat(self) -> None:
        if self._closed:
            raise HeartbeatError("rosarm transport is closed")
        if not self._link_up():
            # roslibpy reconnects by itself and sends then whatever it was handed while the
            # socket was down (`up.QUEUED_UNTIL_READY`): quackd hands this client nothing more,
            # and a new connection is what drives the arm again
            await self._let_go()
            raise HeartbeatError(
                f"rosbridge at {self.endpoint.url} is not connected, and quackd let go of the "
                "client, so nothing it held back is sent later: start a new run or session to "
                "drive this arm"
            )
        if (stale := self.staleness()) is not None:
            raise HeartbeatError(stale)
        with self._lock:
            stopped = self._driver_stopped
        if stopped is not None:
            raise HeartbeatError(f"the arm's driver stopped it: {stopped}")

    async def close(self) -> None:
        """Cancel a goal still running, without waiting, then let go of everything: quackd parks
        nothing, and a goal it no longer watches is one it does not leave going."""
        with self._lock:
            goal = self._goal
            running = goal is not None and goal.running
        if running and goal is not None and self._actions is not None and self._link_up():
            with contextlib.suppress(Exception):
                self._actions.cancel_goal(goal.id)
                goal.cancel_sent_at = self.clock.now()
            # a cancel still in the bridge's queue when the socket closes is finished with it
            # (`up.CLOSED_CLIENT_CANCELS_NOTHING`), so the answer is waited for first
            await self._wait(lambda: not goal.running, CANCEL_WAIT_S)
        await self._let_go()
        self._closed = True

    async def _let_go(self) -> None:
        with self._lock:
            self._connected = False
        for topic in self._topics:
            with contextlib.suppress(Exception):
                topic.unsubscribe()
        self._topics = []
        # this client's own socket, closed as one it asked to close, so its factory does not
        # reconnect; never `terminate()`, which ends every roslibpy client's socket in the
        # process, another robot's in a flock among them (`up.ONE_REACTOR`)
        if self._roslibpy is not None and bool(self._roslibpy.ros.is_connected):
            with contextlib.suppress(Exception):
                await asyncio.to_thread(self._roslibpy.ros.close, self.timeout_s)

    async def get_frame(self) -> Image.Image | None:
        return None

    async def subscribe(self, topic: str) -> AsyncIterator[dict[str, Any]]:  # type: ignore[override]
        while not self._closed:
            await self.sleep(0.5)
            yield {"topic": topic, **(await self.get_state()).model_dump()}

    def now(self) -> float:
        return self.clock.now()

    async def sleep(self, seconds: float) -> None:
        await self.clock.sleep(seconds)
        if self.post_sleep is not None:
            self.post_sleep()
