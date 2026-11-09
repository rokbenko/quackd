"""A fake robotd over TCP loopback, and the fake of Huebot's shim that is a robotd with less.

`FakeRobotd` answers as a robotd at the pin does (`upstream_api.RELEASE`): its hello and its
subscribe answer, a state frame of that release's shape every tick, reporting the twist it was
last asked for, and params decoded the way robotd decodes them, where a member it does not know
is refused by name. Switches make it a robotd of another release: another API version, calls it
does not have, a `robot.move` it does not take, no pick-up detector, no `frames`. `FakeShim` is
the same fake with what a Huebot's shim does not answer taken away.

`tests/test_jsonrpc.py` and `tests/test_huebot.py` drive quackd against these, and what they
prove is quackd's framing, handshake, deadman feeding and error handling against what upstream's
source says, not that a real duck or a real Huebot agrees.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import math
import threading
import time
from collections.abc import Iterator
from typing import Any, NamedTuple

from quackd_huebot import HuebotAdapter
from quackd_huebot.body import HUEBOT, SKILLS_OF
from quackd_microduck import MicroduckAdapter
from quackd_microduck import upstream_api as up
from quackd_microduck.body import MICRODUCK, Body
from quackd_microduck.transports.jsonrpc_unix import JsonRpcUnixTransport

#: The skills a stock robot answers `robot.subscribe` with.
STOCK_SKILLS = ["roulade", "kick_left", "kick_right"]
#: What else a stock robot at the pin answers `robot.subscribe` with: the walk it loads
#: (`up.VELSTAND`) and no standing network, then the sit-stand and ground-pick files that
#: robotd-params names for the walk mode.
STOCK_POLICIES = {
    "walk": up.VELSTAND.name,
    "sitstand": "alpha_sitstand.onnx",
    "ground_pick": "alpha_ground_pick.onnx",
}
#: The order `robot.state.joints` is indexed in, `JOINT_NAMES` in duck-ipc-proto.
JOINTS = (
    "left_hip_yaw",
    "left_hip_roll",
    "left_hip_pitch",
    "left_knee",
    "left_ankle",
    "neck_pitch",
    "head_pitch",
    "head_yaw",
    "head_roll",
    "mouth",
    "right_hip_yaw",
    "right_hip_roll",
    "right_hip_pitch",
    "right_knee",
    "right_ankle",
)
#: `HeadParams` and `LookResult.head`, in the order `robot.state.head` carries them.
HEAD = ("neck_pitch", "head_pitch", "head_yaw", "head_roll")
#: Each call quackd makes that takes params, with every member its params type declares at the
#: pin and the ones that have no default. Every one of those types denies unknown fields, so a
#: member robotd does not know is INVALID_PARAMS naming it (the doc above `API_VERSION` in
#: lib.rs says why).
PARAMS: dict[str, tuple[frozenset[str], frozenset[str]]] = {
    "hello": (frozenset({"api_version"}), frozenset({"api_version"})),
    "robot.move": (frozenset({"vx", "vy", "vyaw"}), frozenset()),
    "robot.head": (frozenset(HEAD), frozenset()),
    "robot.look": (frozenset({"x", "y", "z", "neck_pitch"}), frozenset()),
    "robot.enable": (frozenset({"on", "toggle"}), frozenset({"on"})),
    "robot.do": (frozenset({"skill"}), frozenset({"skill"})),
    "robot.pose": (frozenset({"z", "roll", "pitch", "active"}), frozenset()),
    "robot.sound": (frozenset({"tag", "hold"}), frozenset({"tag"})),
    "robot.subscribe": (frozenset({"hz"}), frozenset()),
}
#: The calls quackd makes that take no params, which robotd answers whatever arrives with them.
NO_PARAMS = frozenset({"robot.stop", "robot.health"})


CURRENT_API = int(up.API_VERSION.name)
FLOOR = int(up.API_FLOOR.name)
NO_VERSION = object()
"""A hello answer with no `api_version` at all, for `FakeRobotd(api_version=...)`."""
NO_HEAD = object()
"""A `robot.look` answer with no `head` at all, for `FakeRobotd.look_head`."""
#: A robot on its feet, and one on the floor, each as a `safety` block every frame carries.
UPRIGHT = {"fallen": False, "picked_up": False, "limp": False}
DOWN_ON_THE_FLOOR = {"fallen": True, "picked_up": False, "limp": False}


Quat = tuple[float, float, float, float]
"""A rotation as a scalar-first quaternion, `[w, x, y, z]`, the way `RobotState.frames` has it."""

#: `frames.camera` looking straight ahead. The camera frame is OpenCV's (+x right, +y down, +z
#: along the optical axis) and the pose is in the trunk frame (x forward, y left, z up), so the
#: optical axis is the trunk's +x, the camera's +x is the trunk's -y and its +y the trunk's -z.
AHEAD: Quat = (0.5, -0.5, 0.5, -0.5)


def _turned(axis: tuple[float, float, float], angle: float) -> Quat:
    """A turn of `angle` radians about the unit `axis`, right-handed."""
    s = math.sin(angle / 2)
    return (math.cos(angle / 2), axis[0] * s, axis[1] * s, axis[2] * s)


def _then(a: Quat, b: Quat) -> Quat:
    """`b`, then `a`: the Hamilton product `a * b`."""
    aw, ax, ay, az = a
    bw, bx, by, bz = b
    return (
        aw * bw - ax * bx - ay * by - az * bz,
        aw * bx + ax * bw + ay * bz - az * by,
        aw * by - ax * bz + ay * bw + az * bx,
        aw * bz + ax * by - ay * bx + az * bw,
    )


def camera_pose(yaw: float, pitch: float = 0.0) -> dict[str, list[float]]:
    """`frames.camera` for a camera turned `yaw` radians to the left of straight ahead and tilted
    `pitch` radians up: `AHEAD`, tilted up about the trunk's +y, which takes a negative angle,
    then turned about the trunk's +z. Built from the turns rather than from the angles quackd
    reads back, so that reading them back is something the test checks."""
    quat = _then(_turned((0.0, 0.0, 1.0), yaw), _then(_turned((0.0, 1.0, 0.0), -pitch), AHEAD))
    return {"pos": [0.05, 0.0, 0.1], "quat": list(quat)}


class FakeRobotd:
    def __init__(
        self,
        *,
        api_version: object = CURRENT_API,
        healthy: bool = True,
        fallen: bool = False,
        policy: str | None = "walk",
        limp: bool = False,
        picked_up: bool | None = False,
        skills: list[str] | None = STOCK_SKILLS,
        lists_skills: bool = True,
        missing: frozenset[str] = frozenset(),
        drop_moves: bool = False,
        reports_frames: bool = True,
    ) -> None:
        self.api_version = api_version
        """What `hello` answers in `api_version`, NO_VERSION to leave it out."""
        self.daemon_version: Any = up.RELEASE.removeprefix("daemon-v")
        """What `hello` answers in `daemon_version`: the release quackd read, as robotd names
        it, by default."""
        self.revision: Any = up.PIN[:7]
        """What `hello` answers in `revision`: the commit quackd read, by default."""
        self.healthy = healthy
        # The duck as the next state frame will describe it. Each frame is built from these when
        # it is sent, so a test can pick the duck up, lay it down or start a skill mid-verb.
        self.fallen = fallen
        self.policy = policy
        self.limp = limp
        self.picked_up = picked_up
        """None leaves `safety.picked_up` out, as a robotd from before the pick-up detector,
        daemon-v0.15.2, does. One at the pin writes it on every frame, held or not."""
        self.skills = skills
        """What `robot.subscribe` answers in `skills`; None leaves the key out, as a robotd whose
        skills are not config yet does."""
        self.lists_skills = lists_skills
        """False leaves `skills` out of the `robot.subscribe` answer altogether, whatever the
        list holds, as a shim that does not list them does."""
        self.policies: dict[str, str] = dict(STOCK_POLICIES)
        """What `robot.subscribe` answers beside `accepted` and `skills`: the policy files a
        stock robot names, and nothing on a shim."""
        self.missing = missing
        """Calls this robotd answers METHOD_NOT_FOUND, as one from before they existed would."""
        self.params = dict(PARAMS)
        """The members each call's params take, this robotd's own copy, so a test can give one
        call the shape a newer robotd might."""
        self.drop_moves = drop_moves
        """Take no `robot.move` at all, as a robotd that cannot parse one does: it says nothing
        back, and `move.requested` goes on reporting what it was asked for before."""
        self.reports_frames = reports_frames
        """False leaves `frames` out of the state, as a robotd from before API 24 does."""
        self.reports_odom = True
        """False leaves `odom` out of the state, as a robotd from before the estimator does."""
        self.odom: dict[str, Any] = {"position": [0.0, 0.0, 0.0], "yaw": 0.0}
        """What every state frame reports as `odom`: where contact odometry puts the duck."""
        self.extra_state: dict[str, Any] = {}
        """Members added to every state frame, to make one as long as a test needs."""
        self.drop_state: set[str] = set()
        """Members left out of every state frame, as a robot that does not report one would."""
        self.safety_block: dict[str, Any] | None = None
        """Every frame's `safety` block exactly as given, in place of the one built from the
        attributes, for a robot that writes something other than true or false there, or no
        key at all."""
        self.stand_after_s: float | None = None
        """Stand a fallen robot up this long after `robot.enable` arrives, as its own software
        getting it up would, or never."""
        self.enabled_at: float | None = None
        """When the newest `robot.enable` arrived, which `stand_after_s` counts from."""
        self.frames_sent = 0
        self.battery: Any = {"volts": 7.4, "percent": 66.0}
        """What `robot.health` answers in `battery`."""
        self.unhealthy_reason = "control loop at 43.9 Hz"
        """What `robot.health` answers in `reason`, on a robot that is not healthy."""
        self.twist = [0.0, 0.0, 0.0]
        """The twist robotd was last asked for, `[vx, vy, vyaw]`, which every state frame reports
        as `move.requested` (`up.MOVE_ECHO`). A stop zeroes it; the deadman does not."""
        self.emotion: str | None = None
        """The emotion robotd is playing, or None. While one plays every frame reports a zero
        twist in place of `twist`, which is kept as it was asked for, and nothing else in the
        frame says so, as robotd puts the emotion's zero in place of what its clients asked for
        on every tick of it (`up.MOVE_ECHO`)."""
        self.head = [0.0, 0.0, 0.0, 0.0]
        """The head slot, `[neck_pitch, head_pitch, head_yaw, head_roll]`: what the last
        `robot.look` or `robot.head` asked for, from anyone."""
        self.head_alpha = 1.0
        """How far each state frame's `head` moves toward the slot, the smoothing robotd applies
        every control tick. 1 reports the slot as it is."""
        self.shown = [0.0, 0.0, 0.0, 0.0]
        """The `head` the last state frame reported, which the joints and the camera follow."""
        self.steering: list[float] | None = None
        """Another client's head, written over the slot before every frame, the way a gamepad
        posing the head sends one at least ten times a second; None while nobody else does."""
        self.glance: list[float] = []
        """Yaw to add to the `head` the next frames report, one a frame, the way robotd adds an
        idle glance to the head slot while it fades one out."""
        self.look_head: Any = None
        """What `robot.look` answers in `head` instead of the head it turned to, when set, and
        NO_HEAD to leave `head` out."""
        self.looked_at: list[int] = []
        """How many notifications had arrived when each `robot.look` did."""
        self.arrived: list[tuple[float, str]] = []
        """When each call arrived, on this machine's monotonic clock, and which it was."""
        self.recover_on_enable = False
        """Stand a fallen duck up when `robot.enable` arrives, as the policy taking over would."""
        self.on_enable: dict[str, Any] = {}
        """What the duck is, attribute by attribute, in the frames after `robot.enable` arrives,
        as what robotd did meanwhile would leave it: a fall it caught, or a duck it handed back."""
        self.notifications: list[dict[str, Any]] = []
        self.requests: list[dict[str, Any]] = []
        self.rejected: list[tuple[str, str]] = []
        """Every call robotd would have refused, with why. A notification gets no answer, so this
        is the only place a refused `robot.move` shows up at all."""
        self.link = asyncio.Event()
        self.link.set()
        """Cleared, the link holds quackd's lines back, as one lost segment on an ssh tunnel does
        until it is sent again: the fake reads nothing more, while its state stream goes on, and
        once it is set again takes every line it was sent, in the order sent."""
        self.junk: list[str] = []
        """Lines written, in order, just before the next answer, each once."""
        self.answers: dict[str, Any] = {}
        """What a call is answered with instead of what robotd answers it with, by method, for
        an answer whose shape moved."""
        self.unanswered: set[str] = set()
        """Calls the fake carries out and then never answers, by method, as a robotd whose
        answer a link lost after it had read the call would look from quackd's end."""
        self.writers: list[asyncio.StreamWriter] = []
        self.server: asyncio.AbstractServer | None = None
        self.port = 0
        self._arrived = asyncio.Event()
        self._stream: asyncio.Task[None] | None = None
        self._streams: list[asyncio.Task[None]] = []
        self._t0 = time.monotonic()

    async def start(self) -> None:
        self.server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        self.port = self.server.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        assert self.server is not None
        for stream in self._streams:
            stream.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await stream
        self._stream = None
        # From Python 3.12.1 `wait_closed` also waits for every connection the server took to
        # close, and a test that fails before it closes its transport leaves quackd's open, so
        # teardown waited until CI's job limit cancelled it, and the failure was never printed.
        # Hanging up first is what a robotd that exits does, and the bound is the backstop for a
        # connection that does not close when told, as `_DelayingLink.stop` has.
        self.hang_up()
        self.server.close()
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self.server.wait_closed(), 2.0)

    def hang_up(self) -> None:
        """Close every connection from this end, as a robotd that exits does."""
        for writer in self.writers:
            writer.close()

    @property
    def address(self) -> str:
        return f"tcp://127.0.0.1:{self.port}"

    def methods(self) -> list[str]:
        """The name of every call that reached this fake, oldest first."""
        return [method for _at, method in self.arrived]

    async def wait_notifications(self, n: int, wait_s: float = 2.0) -> list[dict[str, Any]]:
        """Notifications have no reply, so the test must wait for the server to read them."""
        async with asyncio.timeout(wait_s):
            while len(self.notifications) < n:
                self._arrived.clear()
                await self._arrived.wait()
        return self.notifications

    def frame(self) -> dict[str, Any]:
        """The `robot.state` notification for this tick, from the attributes as they are now."""
        if (
            self.stand_after_s is not None
            and self.enabled_at is not None
            and time.monotonic() - self.enabled_at >= self.stand_after_s
        ):
            self.fallen, self.policy, self.enabled_at = False, "stand", None
        safety: dict[str, Any] = {
            "fallen": self.fallen,
            "limp": self.limp,
            "gravity": [0.0, 0.0, -1.0],
            "gain": 200,
        }
        if self.picked_up is not None:
            safety["picked_up"] = self.picked_up
        if self.safety_block is not None:
            safety = dict(self.safety_block)
        if self.steering is not None:
            self.head = list(self.steering)
        shown = zip(self.shown, self.head, strict=True)
        self.shown = [s + self.head_alpha * (h - s) for s, h in shown]
        joints = [0.0] * len(JOINTS)
        for name, angle in zip(HEAD, self.shown, strict=True):
            joints[JOINTS.index(name)] = angle
        reported = list(self.shown)
        reported[HEAD.index("head_yaw")] += self.glance.pop(0) if self.glance else 0.0
        t = time.monotonic() - self._t0
        twist = list(self.twist) if self.emotion is None else [0.0, 0.0, 0.0]
        params: dict[str, Any] = {
            "t": t,
            "move": {"requested": twist, "applied": self._applied(twist)},
            "head": reported,
            "policy": self.policy,
            "safety": safety,
            "loop": {"hz": 50.0, "missed": 0},
            "joints": joints,
            "targets": joints,
            "t_ns": int(time.monotonic_ns()),
        }
        if self.reports_odom:
            params["odom"] = {"position": list(self.odom["position"]), "yaw": self.odom["yaw"]}
        if self.reports_frames:
            # the fake's own head: yaw turns the camera, and a positive head_pitch tilts it down,
            # the sign upstream's FK test pins (`up.HEAD_YAW_SIGN`)
            params["frames"] = {
                "camera": camera_pose(self.shown[2], -self.shown[1]),
                "tof": {"pos": [0.05, 0.0, 0.1], "quat": [1.0, 0.0, 0.0, 0.0]},
            }
        params |= self.extra_state
        for key in self.drop_state:
            params.pop(key, None)
        return {"jsonrpc": "2.0", "method": "robot.state", "params": params}

    def _applied(self, twist: list[float]) -> list[float]:
        """What a state frame reports as `move.applied`, beside the `requested` it reports."""
        return list(twist)

    async def _publish(self, writer: asyncio.StreamWriter, hz: int) -> None:
        """Stream `robot.state` the way a real robotd does, until the connection goes away."""
        while not writer.is_closing():
            try:
                writer.write((json.dumps(self.frame()) + "\n").encode())
                await writer.drain()
            except (ConnectionError, OSError):
                return
            self.frames_sent += 1
            await asyncio.sleep(1.0 / max(1, hz))

    def _take(self, method: str, params: dict[str, Any]) -> None:
        """A continuous intent robotd parsed, however it was framed (`apply_intent`)."""
        if method == "robot.head":
            self.head = [float(params.get(k, 0.0)) for k in HEAD]
        elif method == "robot.move" and not self.drop_moves:
            self.twist = [float(params.get(k, 0.0)) for k in ("vx", "vy", "vyaw")]

    def refusal(self, method: str, params: Any) -> tuple[int, str] | None:
        """The error robotd answers this call with, or None when it would take it.

        In robotd's order (`Call::parse`): a method it does not have is METHOD_NOT_FOUND, then
        params are decoded, and a member the params type does not declare, one it requires and
        did not get, or a sound tag that is not one of the seven, is INVALID_PARAMS naming it, in
        serde's words."""
        if method in self.missing or method not in self.params.keys() | NO_PARAMS:
            return up.ERR_METHOD_NOT_FOUND, f"unknown method {json.dumps(method)}"
        if method in NO_PARAMS:
            return None
        known, required = self.params[method]
        if not isinstance(params, dict):
            return up.ERR_INVALID_PARAMS, f"invalid type: {json.dumps(params)}, expected a map"
        for member in params:
            if member not in known:
                expected = ", ".join(f"`{name}`" for name in sorted(known))
                return (
                    up.ERR_INVALID_PARAMS,
                    f"unknown field `{member}`, expected one of {expected}",
                )
        for member in sorted(required - params.keys()):
            return up.ERR_INVALID_PARAMS, f"missing field `{member}`"
        if method == "robot.sound" and params["tag"] not in up.SOUND_TAG_LIST:
            return up.ERR_INVALID_PARAMS, f"unknown variant `{params['tag']}`"
        return None

    def _takes_skill(self, skill: Any) -> bool:
        """Whether `robot.do` accepts this skill: every one robotd runs, listed or not, the
        two it keeps back from `skills` among them."""
        return skill in (
            "ground_pick",
            "kick_left",
            "kick_right",
            "sit_toggle",
            "roulade",
        )

    def _answer(self, writer: asyncio.StreamWriter, answer: dict[str, Any]) -> None:
        for junk in self.junk:
            writer.write((junk + "\n").encode())
        self.junk = []
        writer.write((json.dumps(answer) + "\n").encode())

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.writers.append(writer)
        while line := await reader.readline():
            await self.link.wait()
            msg = json.loads(line)
            method, params = msg["method"], msg.get("params") or {}
            self.arrived.append((time.monotonic(), method))
            if "id" not in msg:
                self.notifications.append(msg)
                if (refused := self.refusal(method, params)) is not None:
                    # robotd drops a notification it cannot parse and says nothing back
                    self.rejected.append((method, refused[1]))
                else:
                    self._take(method, params)
                self._arrived.set()
                continue
            self.requests.append(msg)
            if method == "robot.neverAnswered":
                # A robotd that accepts a call and never replies, which is what a wedged
                # daemon looks like from here. Also feeds a junk line, to prove the pump
                # skips what it cannot parse instead of dying on it.
                writer.write(b"{not json at all\n")
                await writer.drain()
                continue
            if (refused := self.refusal(method, params)) is not None:
                self.rejected.append((method, refused[1]))
                code, message = refused
                error = {"code": code, "message": message}
                self._answer(writer, {"jsonrpc": "2.0", "id": msg["id"], "error": error})
                await writer.drain()
                continue
            result: Any
            if method == "hello":
                result = {"daemon_version": self.daemon_version, "revision": self.revision}
                if self.api_version is not NO_VERSION:
                    result["api_version"] = self.api_version
            elif method == "robot.health":
                result = {"healthy": self.healthy, "battery": self.battery}
                if not self.healthy:
                    result["reason"] = self.unhealthy_reason
            elif method in ("robot.stop", "robot.enable"):
                result = {"accepted": True}
                if method == "robot.stop":
                    self.twist = [0.0, 0.0, 0.0]  # robotd's stop zeroes the twist it was asked for
                if method == "robot.enable" and self.recover_on_enable:
                    self.fallen, self.policy = False, "stand"
                if method == "robot.enable":
                    self.enabled_at = time.monotonic()
                    for name, value in self.on_enable.items():
                        setattr(self, name, value)
            elif method == "robot.do":
                ok = self._takes_skill(params.get("skill"))
                result = {"accepted": ok, "reason": None if ok else "unknown skill"}
            elif method == "robot.look":
                # Turned toward the point, which is the fake's own head and not robotd's IK: a
                # client never has to know which way a positive head_yaw turns, upstream says.
                self.looked_at.append(len(self.notifications))
                x, y = float(params.get("x", 1.0)), float(params.get("y", 0.0))
                up_by = math.atan2(float(params.get("z", 0.0)), math.hypot(x, y))
                self.head = [float(params.get("neck_pitch", 0.0)), -up_by, math.atan2(y, x), 0.0]
                result = {"head": dict(zip(HEAD, self.head, strict=True)), "clamped": False}
                if self.look_head is NO_HEAD:
                    del result["head"]
                elif self.look_head is not None:
                    result["head"] = self.look_head
            elif method == "robot.sound":
                result = {"accepted": True}
            elif method == "robot.subscribe":
                result = {"accepted": True, **self.policies}
                if self.skills is not None and self.lists_skills:
                    result["skills"] = list(self.skills)
                # A real robotd streams until the connection ends. Sending one frame and
                # stopping made every posture assertion race the staleness window, which is
                # a flake on a loaded CI box and, worse, meant nothing ever exercised the
                # steady state, only the first 300 ms after connecting.
                self._stream = asyncio.create_task(self._publish(writer, params.get("hz") or 10))
                self._streams.append(self._stream)
            else:
                # A continuous intent sent with an id, which quackd never does. robotd takes it
                # the same as the notification and answers it, rather than ignoring a call for
                # its framing (`apply_intent` in robotd/src/main.rs).
                self._take(method, params)
                result = {"accepted": True}
            if method in self.unanswered:
                continue
            result = self.answers.get(method, result)
            self._answer(writer, {"jsonrpc": "2.0", "id": msg["id"], "result": result})
            await writer.drain()
        writer.close()


#: Every skill a verb sends, which a shim that has them all lists.
ALL_SKILLS = sorted({skill for skills in SKILLS_OF.values() for skill in skills})
#: The calls a Microduck's robotd answers that a Huebot's shim does not answer yet.
_NOT_ON_THE_SHIM = frozenset({up.ROBOT_LOOK.name, up.ROBOT_HEAD.name, up.ROBOT_POSE.name})


class FakeShim(FakeRobotd):
    """Huebot's shim as its maker is asked to build it, over TCP loopback: the robotd above with
    less of it. It answers what docs/adapters/huebot/README.md asks the shim to answer, as robotd
    does at the pin quackd read it at, and nothing else: `robot.look`, `robot.head` and
    `robot.pose` are `METHOD_NOT_FOUND` until the shim has them. Its state has no `frames` and no
    odometry, its `skills` are every skill Huebot's verbs send, `sit_toggle` among them, and it
    keeps a deadman of its own (`driving`), which is what the shim is asked to keep."""

    def __init__(
        self,
        *,
        skills: list[str] | None = None,
        missing: frozenset[str] = frozenset(),
        **kwargs: Any,
    ) -> None:
        super().__init__(
            skills=list(ALL_SKILLS if skills is None else skills),
            missing=_NOT_ON_THE_SHIM | missing,
            **kwargs,
        )
        self.daemon_version = "0.1.0"
        self.revision = None
        self.policies = {}
        self.battery = {"percent": 71.0}
        self.unhealthy_reason = "servo bus timeout"
        self.reports_frames = False
        self.reports_odom = False
        self.moved_at: float | None = None
        """When the newest `robot.move` was read, which robotd's deadman ages."""

    def driving(self) -> list[float]:
        """The twist the legs are on now: the newest one asked for, until a stop, or until
        robotd's deadman finds it older than `up.DEADMAN_S`, which is what the shim is asked to
        keep (`up.SHIM_DEADMAN`)."""
        if self.moved_at is None or time.monotonic() - self.moved_at > up.DEADMAN_S:
            return [0.0, 0.0, 0.0]
        return list(self.twist)

    def _applied(self, twist: list[float]) -> list[float]:
        return self.driving()

    def _take(self, method: str, params: dict[str, Any]) -> None:
        super()._take(method, params)
        if method == "robot.move" and not self.drop_moves:
            self.moved_at = time.monotonic()

    def _takes_skill(self, skill: Any) -> bool:
        """Whether `robot.do` accepts this skill: only one the shim listed."""
        return skill in (self.skills or ())


class Member(NamedTuple):
    """One body of the Microduck's family, for a test that holds both to one behaviour: the fake
    that answers for it, its `Body`, and the adapter that drives it over its transport."""

    fake: type[FakeRobotd]
    body: Body
    adapter: type[MicroduckAdapter]

    async def connected(self, fake: FakeRobotd, **transport: Any) -> MicroduckAdapter:
        """This body's adapter, connected to `fake` the way a run connects it."""
        adapter = self.adapter(JsonRpcUnixTransport(fake.address, body=self.body, **transport))
        await adapter.connect()
        return adapter


MEMBERS = (
    Member(FakeRobotd, MICRODUCK, MicroduckAdapter),
    Member(FakeShim, HUEBOT, HuebotAdapter),
)
"""The bodies the family's transport drives, each with the fake that answers for it."""


@contextlib.contextmanager
def on_its_own_loop(kind: type[FakeRobotd] = FakeRobotd, **kwargs: Any) -> Iterator[FakeRobotd]:
    """A fake serving from a loop in a thread of its own, for code that runs a loop of its own,
    as `asyncio.run` and `doctor.probe` do."""
    loop = asyncio.new_event_loop()
    started: list[FakeRobotd] = []
    ready = threading.Event()

    def serve() -> None:
        asyncio.set_event_loop(loop)
        fake = kind(**kwargs)
        loop.run_until_complete(fake.start())
        started.append(fake)
        ready.set()
        loop.run_forever()

    thread = threading.Thread(target=serve, name="fake-robotd", daemon=True)
    thread.start()
    assert ready.wait(30), "the fake never started"
    try:
        yield started[0]
    finally:
        asyncio.run_coroutine_threadsafe(started[0].stop(), loop).result(30)
        loop.call_soon_threadsafe(loop.stop)
        thread.join(30)
        loop.close()
