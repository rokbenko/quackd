"""The experimental robot transport against a fake robotd over TCP loopback.

The fake answers as a robotd at the pin does (`upstream_api.RELEASE`): its hello and its
subscribe answer, a state frame of that release's shape every tick, reporting the twist it was
last asked for, and params decoded the way robotd decodes them, where a member it does not know
is refused by name. Switches make it a robotd of another release: another API version, calls it
does not have, a `robot.move` it does not take, no pick-up detector, no `frames`. So this proves
our framing, handshake, deadman feeding and error handling against what upstream's source says,
not that a real duck agrees.
"""

from __future__ import annotations

import asyncio
import contextlib
import http.server
import io
import json
import logging
import math
import socket
import sys
import threading
import time
import urllib.error
from collections.abc import Callable, Iterator
from typing import Any

import pytest
from PIL import Image

from quackd.duckfile.parser import parse_duck_text
from quackd.perception.base import Detection
from quackd.safety import Executor
from quackd.transport.base import HeartbeatError, Intent, TransportError
from quackd.transport.mock import MockTransport
from quackd.verbs.core import MOVE_RESEND_S
from quackd.verbs.registry import VerbRegistry
from quackd_microduck import MicroduckAdapter
from quackd_microduck import upstream_api as up
from quackd_microduck.transports import jsonrpc_unix
from quackd_microduck.transports.jsonrpc_unix import (
    CAMERA_FPS,
    GAZE_HOLD_S,
    MEDIAD_FRAME_FPS,
    MOVE_ECHO_FRAMES,
    READER_LIMIT,
    JsonRpcUnixTransport,
    camera_stale_after_s,
    default_address,
    move_echo_after_s,
    parse_address,
    state_stale_after_s,
)

DUCK = parse_duck_text(
    "---\nduck: 0\nname: t\ndescription: d\nverbs:\n  allow: [walk, kick, quack, gaze]\n"
    "success: [x]\n---\n# Task\nx\n"
)
#: Every verb that moves the duck, for the tests of what a duck that is not standing refuses.
MOVERS = parse_duck_text(
    "---\nduck: 0\nname: t\ndescription: d\nverbs:\n"
    "  allow: [walk, go_to, kick, gaze, sit, stand, stand_up]\nsuccess: [x]\n---\n# Task\nx\n"
)
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
        missing: frozenset[str] = frozenset(),
        drop_moves: bool = False,
        reports_frames: bool = True,
    ) -> None:
        self.api_version = api_version
        """What `hello` answers in `api_version`, NO_VERSION to leave it out."""
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
        self.extra_state: dict[str, Any] = {}
        """Members added to every state frame, to make one as long as a test needs."""
        self.frames_sent = 0
        self.twist = [0.0, 0.0, 0.0]
        """The twist robotd was last asked for, `[vx, vy, vyaw]`, which every state frame reports
        as `move.requested` (`up.MOVE_ECHO`). A stop zeroes it; the deadman does not."""
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
        self.notifications: list[dict[str, Any]] = []
        self.requests: list[dict[str, Any]] = []
        self.rejected: list[tuple[str, str]] = []
        """Every call robotd would have refused, with why. A notification gets no answer, so this
        is the only place a refused `robot.move` shows up at all."""
        self.server: asyncio.AbstractServer | None = None
        self.port = 0
        self._arrived = asyncio.Event()
        self._stream: asyncio.Task[None] | None = None
        self._t0 = time.monotonic()

    async def start(self) -> None:
        self.server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        self.port = self.server.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        assert self.server is not None
        if self._stream is not None:
            self._stream.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._stream
            self._stream = None
        self.server.close()
        await self.server.wait_closed()

    async def wait_notifications(self, n: int, wait_s: float = 2.0) -> list[dict[str, Any]]:
        """Notifications have no reply, so the test must wait for the server to read them."""
        async with asyncio.timeout(wait_s):
            while len(self.notifications) < n:
                self._arrived.clear()
                await self._arrived.wait()
        return self.notifications

    def frame(self) -> dict[str, Any]:
        """The `robot.state` notification for this tick, from the attributes as they are now."""
        safety: dict[str, Any] = {
            "fallen": self.fallen,
            "limp": self.limp,
            "gravity": [0.0, 0.0, -1.0],
            "gain": 200,
        }
        if self.picked_up is not None:
            safety["picked_up"] = self.picked_up
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
        params: dict[str, Any] = {
            "t": t,
            "move": {"requested": list(self.twist), "applied": list(self.twist)},
            "head": reported,
            "policy": self.policy,
            "safety": safety,
            "loop": {"hz": 50.0, "missed": 0},
            "joints": joints,
            "targets": joints,
            "odom": {"position": [0.0, 0.0, 0.0], "yaw": 0.0},
            "t_ns": int(time.monotonic_ns()),
        }
        if self.reports_frames:
            # the fake's own head: yaw turns the camera, and a positive head_pitch tilts it down,
            # the sign upstream's FK test pins (`up.HEAD_YAW_SIGN`)
            params["frames"] = {
                "camera": camera_pose(self.shown[2], -self.shown[1]),
                "tof": {"pos": [0.05, 0.0, 0.1], "quat": [1.0, 0.0, 0.0, 0.0]},
            }
        return {"jsonrpc": "2.0", "method": "robot.state", "params": params | self.extra_state}

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

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        while line := await reader.readline():
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
                writer.write(
                    (
                        json.dumps(
                            {
                                "jsonrpc": "2.0",
                                "id": msg["id"],
                                "error": {"code": code, "message": message},
                            }
                        )
                        + "\n"
                    ).encode()
                )
                await writer.drain()
                continue
            result: Any
            if method == "hello":
                result = {
                    "daemon_version": up.RELEASE.removeprefix("daemon-v"),
                    "revision": up.PIN[:7],
                }
                if self.api_version is not NO_VERSION:
                    result["api_version"] = self.api_version
            elif method == "robot.health":
                result = {"healthy": self.healthy, "battery": {"volts": 7.4, "percent": 66.0}}
                if not self.healthy:
                    result["reason"] = "control loop at 43.9 Hz"
            elif method in ("robot.stop", "robot.enable"):
                result = {"accepted": True}
                if method == "robot.stop":
                    self.twist = [0.0, 0.0, 0.0]  # robotd's stop zeroes the twist it was asked for
                if method == "robot.enable" and self.recover_on_enable:
                    self.fallen, self.policy = False, "stand"
            elif method == "robot.do":
                ok = params.get("skill") in (
                    "ground_pick",
                    "kick_left",
                    "kick_right",
                    "sit_toggle",
                    "roulade",
                )
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
                result = {"accepted": True, **STOCK_POLICIES}
                if self.skills:
                    result["skills"] = self.skills
                # A real robotd streams until the connection ends. Sending one frame and
                # stopping made every posture assertion race the staleness window, which is
                # a flake on a loaded CI box and, worse, meant nothing ever exercised the
                # steady state — only the first 300 ms after connecting.
                self._stream = asyncio.create_task(self._publish(writer, params.get("hz") or 10))
            else:
                # A continuous intent sent with an id, which quackd never does. robotd takes it
                # the same as the notification and answers it, rather than ignoring a call for
                # its framing (`apply_intent` in robotd/src/main.rs).
                self._take(method, params)
                result = {"accepted": True}
            writer.write(
                (json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": result}) + "\n").encode()
            )
            await writer.drain()
        writer.close()


@pytest.fixture
async def robotd():
    fake = FakeRobotd()
    await fake.start()
    yield fake
    await fake.stop()


async def _until(t: JsonRpcUnixTransport, check: Callable[[dict[str, Any]], bool]) -> None:
    """Wait until the transport has read a state frame `check` accepts, which is when a change
    made on the fake has reached quackd. The fake sends one every tenth of a second."""
    for _ in range(200):
        if check(t._last_state or {}):
            return
        await asyncio.sleep(0.01)
    raise AssertionError(
        f"the transport never read the frame the fake was changed to: {t._last_state}"
    )


async def _landed(t: JsonRpcUnixTransport) -> None:
    """Return once the fake has read every line the transport sent before this one.

    Notifications get no reply, so a test cannot wait on them directly. A request can be: the
    fake reads one connection in order, so by the time it answers this, it has recorded every
    `robot.move` written ahead of it."""
    await t.request(up.ROBOT_HEALTH.name)


def _moves(fake: FakeRobotd) -> int:
    return sum(1 for n in fake.notifications if n["method"] == up.ROBOT_MOVE.name)


def _heads(fake: FakeRobotd) -> int:
    return sum(1 for n in fake.notifications if n["method"] == up.ROBOT_HEAD.name)


async def test_handshake_and_intents(robotd: FakeRobotd) -> None:
    t = JsonRpcUnixTransport(f"tcp://127.0.0.1:{robotd.port}")
    await t.connect()
    assert t.hello == {
        "api_version": CURRENT_API,
        "daemon_version": up.RELEASE.removeprefix("daemon-v"),
        "revision": up.PIN[:7],
    }
    assert (await t.send_intent(Intent.move(0.1, 0.0, 0.2))).accepted
    notifications = await robotd.wait_notifications(1)
    assert notifications[-1] == {
        "jsonrpc": "2.0",
        "method": "robot.move",
        "params": {"vx": 0.1, "vy": 0.0, "vyaw": 0.2},
    }
    assert (await t.send_intent(Intent.do("kick_right"))).accepted
    assert not (await t.send_intent(Intent(kind="do", params={"skill": "moonwalk"}))).accepted
    assert (await t.send_intent(Intent.sound("greet", "hi"))).accepted
    assert robotd.requests[-1]["params"] == {"tag": "greet"}  # text never goes on the wire
    await t.heartbeat()
    state = await t.get_state()
    assert state.battery_percent == 66.0
    await t.stop()
    assert robotd.requests[-1]["method"] == "robot.stop"
    await t.close()


async def test_walk_verb_feeds_deadman_over_the_wire(
    registry: VerbRegistry, robotd: FakeRobotd
) -> None:
    t = JsonRpcUnixTransport(f"tcp://127.0.0.1:{robotd.port}")
    await t.connect()
    ex = Executor(registry, t, contract=DUCK.frontmatter)
    result = await ex.run_verb("walk", {"vx": 0.1, "duration_s": 0.3})
    assert result.ok
    notifications = await robotd.wait_notifications(3)
    moves = [n for n in notifications if n["method"] == "robot.move"]
    assert len(moves) == 3
    assert [r["method"] for r in robotd.requests][-1] == "robot.stop"
    await t.close()


async def test_subscribe_yields_state_and_posture(robotd: FakeRobotd) -> None:
    t = JsonRpcUnixTransport(f"tcp://127.0.0.1:{robotd.port}")
    await t.connect()
    gen = t.subscribe("state")
    first = await asyncio.wait_for(gen.__anext__(), timeout=2)
    assert first["topic"] == "robot.state" and first["policy"] == "walk"
    state = await t.get_state()
    assert state.posture == "standing" and state.policy == "walk"
    await gen.aclose()
    await t.close()


async def test_a_robotd_newer_than_the_read_is_served_and_says_so(
    registry: VerbRegistry, caplog: pytest.LogCaptureFixture
) -> None:
    """It used to be refused at connect, which is how every robotd release from daemon-v0.12.0
    on was a closed socket, the first of them six days after the read. No robotd refuses a peer
    for a different number, and a call whose shape moved refuses itself by name
    (`up.VERSION_SKEW`), so a newer one is served now, and the log, `doctor` and every state
    say which robot it is and that quackd has not read it."""
    newer = CURRENT_API + 1
    fake = FakeRobotd(api_version=newer)
    await fake.start()
    try:
        t = JsonRpcUnixTransport(f"tcp://127.0.0.1:{fake.port}")
        with caplog.at_level(logging.WARNING, logger="quackd.transport.jsonrpc"):
            await t.connect()
        warning = t.runtime_warning or ""
        assert f"API v{newer}" in warning and f"API v{CURRENT_API}" in warning
        assert "newer" in warning and up.RELEASE in warning
        assert any(r.getMessage() == warning for r in caplog.records), caplog.records
        state = await t.get_state()
        assert state.extras["robotd"] == {
            "api_version": newer,
            "read": CURRENT_API,
            "floor": FLOOR,
            "daemon_version": up.RELEASE.removeprefix("daemon-v"),
            "revision": up.PIN[:7],
            "warning": warning,
            "state_frames": True,
            "mediad_frame": None,
        }
        # and served: the duck walks
        result = await Executor(registry, t, contract=DUCK.frontmatter).run_verb(
            "walk", {"vx": 0.1, "duration_s": 0.2}
        )
        assert result.ok, result.summary
        await t.close()
    finally:
        await fake.stop()


async def test_the_version_quackd_was_written_against_is_the_one_upstream_ships() -> None:
    """A robotd older than the oldest contract quackd has read is refused, not guessed at.

    `API_VERSION` was 16 for a week after upstream had moved to 23, and nothing here noticed
    because the fake was pinned to the same stale number. It was then 23 while every robotd
    release from daemon-v0.12.0 on said 27 or more, and quackd refused each of them. Naming the
    number means a bump has to come past this test rather than sliding through with the fake,
    and so does the floor, the oldest read: 16 stays refused, and so does the version just
    below the floor, in words that name both numbers.
    """
    # Named, not `>= 41`: a bump has to come past this line and past a fresh read of upstream,
    # rather than sailing through because the number only went up.
    assert CURRENT_API == 41, "re-read duck-ipc-proto at a new pin before changing this"
    assert FLOOR == 23, "the floor is the oldest contract quackd has read end to end"
    for below in (16, FLOOR - 1):
        fake = FakeRobotd(api_version=below)
        await fake.start()
        try:
            t = JsonRpcUnixTransport(f"tcp://127.0.0.1:{fake.port}")
            with pytest.raises(TransportError) as refused:
                await t.connect()
            said = str(refused.value)
            assert f"API v{below}," in said and f"older than API v{FLOOR}" in said, said
            assert t._pump is None and t._writer is None  # and nothing is left open
        finally:
            await fake.stop()


async def test_a_robotd_older_than_the_read_but_not_the_floor_is_served_and_warned_about() -> None:
    """The floor is the oldest version quackd has read, not a promise about every version
    between it and the pin: daemon-v0.15.0 to daemon-v0.15.2 all say API 37, and only the last
    reports `safety.picked_up`. So an older robotd is warned about as a newer one is."""
    fake = FakeRobotd(api_version=FLOOR, picked_up=None)
    await fake.start()
    try:
        t = JsonRpcUnixTransport(f"tcp://127.0.0.1:{fake.port}")
        await t.connect()
        warning = t.runtime_warning or ""
        assert f"API v{FLOOR}" in warning and "older" in warning, warning
        state = await t.get_state()
        assert state.extras["robotd"]["warning"] == warning
        assert state.extras["pickup_detection"] is False
        await t.close()
    finally:
        await fake.stop()


async def test_the_version_read_connects_without_a_warning(robotd: FakeRobotd) -> None:
    t = JsonRpcUnixTransport(f"tcp://127.0.0.1:{robotd.port}")
    await t.connect()
    assert t.runtime_warning is None
    record = (await t.get_state()).extras["robotd"]
    assert record["api_version"] == record["read"] == CURRENT_API and record["warning"] is None
    await t.close()


@pytest.mark.parametrize(
    "answered",
    [NO_VERSION, None, str(CURRENT_API), float(CURRENT_API), True],
    ids=["missing", "null", "string", "float", "bool"],
)
async def test_a_hello_that_names_no_version_is_refused(answered: object) -> None:
    """Whatever answered `hello` without an integer `api_version` is not a robotd quackd can
    check against anything. It used to be served, because the old gate compared only a version
    that was there, and `True` passed as the number 1 would have."""
    fake = FakeRobotd(api_version=answered)
    await fake.start()
    try:
        t = JsonRpcUnixTransport(f"tcp://127.0.0.1:{fake.port}")
        with pytest.raises(TransportError, match="not a version number"):
            await t.connect()
    finally:
        await fake.stop()


async def test_a_socket_with_no_hello_is_not_robotd() -> None:
    """tofd, for one, answers every call but its two streams with METHOD_NOT_FOUND, `hello`
    included, so `--address` pointed at the wrong socket says so rather than reading as a
    robotd that lacks a call."""
    fake = FakeRobotd(missing=frozenset({up.HELLO.name}))
    await fake.start()
    try:
        t = JsonRpcUnixTransport(f"tcp://127.0.0.1:{fake.port}")
        with pytest.raises(TransportError) as refused:
            await t.connect()
        assert refused.value.code == up.ERR_METHOD_NOT_FOUND
        assert "is not robotd" in str(refused.value) and up.SOCKET_ROBOTD.name in str(refused.value)
    finally:
        await fake.stop()


async def test_unhealthy_robot_fails_heartbeat() -> None:
    fake = FakeRobotd(healthy=False)
    await fake.start()
    try:
        t = JsonRpcUnixTransport(f"tcp://127.0.0.1:{fake.port}")
        await t.connect()
        with pytest.raises(HeartbeatError, match=r"43\.9"):
            await t.heartbeat()
        await t.close()
    finally:
        await fake.stop()


async def test_connection_refused_is_clean() -> None:
    t = JsonRpcUnixTransport("tcp://127.0.0.1:1")
    with pytest.raises(TransportError, match="cannot connect"):
        await t.connect()


def test_parse_address() -> None:
    assert parse_address("unix:///run/robotd.sock") == ("unix", "/run/robotd.sock", None)
    assert parse_address("tcp://duck.local:9870") == ("tcp", "duck.local", 9870)
    with pytest.raises(TransportError):
        parse_address("http://nope")


def test_the_default_address_is_robotds_own_socket_whatever_duck_runtime_dir_says(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`DUCK_RUNTIME_DIR` moves the directory upstream's daemons publish their identity files in,
    never robotd's socket, which is a literal `--socket` default (`up.RUNTIME_DIR_ENV`). quackd
    read it on the machine it ran on, so in a shell that set it, for upstream's tests or a daemon
    run by hand, the default named a socket no robotd creates."""
    monkeypatch.setenv(up.RUNTIME_DIR_ENV.name, "/home/someone/.cache/duck-sim")
    assert default_address() == f"unix://{up.SOCKET_ROBOTD.name}" == "unix:///run/robotd.sock"
    assert JsonRpcUnixTransport().address == default_address()


@pytest.mark.skipif(sys.platform != "win32", reason="the Windows hint only fires on Windows")
async def test_unix_socket_on_windows_explains_ssh_forward() -> None:
    with pytest.raises(TransportError, match="ssh -L"):
        await JsonRpcUnixTransport("unix:///run/robotd.sock").connect()


async def test_connect_subscribes_so_state_arrives_without_anyone_iterating(
    robotd: FakeRobotd,
) -> None:
    """The bug this file could not see: `robot.subscribe` was only ever sent by `subscribe()`.

    Nothing in the CLI, the agent loop, the MCP server or the executor iterates that generator,
    and upstream does not push state until asked — so on hardware every frame-derived fact was
    permanently empty while reading as safe.
    """
    t = JsonRpcUnixTransport(f"tcp://127.0.0.1:{robotd.port}")
    await t.connect()
    assert [r["method"] for r in robotd.requests] == ["hello", "robot.subscribe"]
    assert t.subscribed == {"accepted": True, **STOCK_POLICIES, "skills": STOCK_SKILLS}

    state = await t.get_state()  # no subscribe() call anywhere in this test
    assert state.policy == "walk" and state.posture == "standing"
    assert state.extras["fall_detection"] is True
    await t.close()


async def test_a_duck_nobody_is_watching_reads_as_unknown_never_as_standing(
    robotd: FakeRobotd,
) -> None:
    t = JsonRpcUnixTransport(f"tcp://127.0.0.1:{robotd.port}")
    await t.connect()
    t._last_state_at = None  # the stream never started
    state = await t.get_state()
    assert state.posture == "unknown"
    assert state.extras["fall_detection"] is False
    # `fallen` is a plain bool and still False here, which is exactly why posture and
    # fall_detection have to carry the difference.
    assert state.fallen is False
    await t.close()


async def test_a_frame_with_no_safety_block_is_not_a_verdict_either(robotd: FakeRobotd) -> None:
    """Frames arriving is not the same as frames that say anything about falling."""
    t = JsonRpcUnixTransport(f"tcp://127.0.0.1:{robotd.port}")
    await t.connect()
    t._last_state = {"t": 2.0, "policy": "walk"}  # a state frame carrying no safety block
    state = await t.get_state()
    assert state.posture == "unknown" and state.extras["fall_detection"] is False
    await t.close()


async def test_stale_state_stops_being_believed(robotd: FakeRobotd) -> None:
    t = JsonRpcUnixTransport(f"tcp://127.0.0.1:{robotd.port}")
    await t.connect()
    assert (await t.get_state()).posture == "standing"
    t._last_state_at = time.monotonic() - 10 * state_stale_after_s(t.state_hz)
    state = await t.get_state()
    assert state.posture == "unknown" and state.extras["fall_detection"] is False
    await t.close()


async def test_a_fallen_duck_refuses_to_walk(registry: VerbRegistry) -> None:
    fake = FakeRobotd(fallen=True)
    await fake.start()
    try:
        t = JsonRpcUnixTransport(f"tcp://127.0.0.1:{fake.port}")
        await t.connect()
        assert (await t.get_state()).posture == "fallen"
        ex = Executor(registry, t, contract=DUCK.frontmatter)
        result = await ex.run_verb("walk", {"vx": 0.1, "duration_s": 0.2})
        assert not result.ok and "fallen" in result.summary
        assert not [r for r in fake.notifications if r["method"] == "robot.move"]
        await t.close()
    finally:
        await fake.stop()


async def test_a_blind_duck_refuses_to_walk(registry: VerbRegistry, robotd: FakeRobotd) -> None:
    """`fallen: false` from a stream that never started must not read as permission to walk."""
    t = JsonRpcUnixTransport(f"tcp://127.0.0.1:{robotd.port}")
    await t.connect()
    t._last_state_at = None
    ex = Executor(registry, t, contract=DUCK.frontmatter)
    result = await ex.run_verb("walk", {"vx": 0.1, "duration_s": 0.2})
    assert not result.ok and "cannot tell" in result.summary
    assert not [r for r in robotd.notifications if r["method"] == "robot.move"]
    await t.close()


async def test_a_broken_camera_does_not_end_the_run(robotd: FakeRobotd) -> None:
    """`get_frame` used to raise, under a comment promising it would not.

    `AgentLoop._observe` calls it every step and catches nothing, and the loop's own handlers
    only cover SafetyStop, so one refused HTTP response ended the session at the CLI.
    """
    t = JsonRpcUnixTransport(
        f"tcp://127.0.0.1:{robotd.port}", camera_url="http://127.0.0.1:1/snapshot.jpg"
    )
    await t.connect()
    assert await t.get_frame() is None  # no frame, but no exception either
    # load-bearing: a *working* camera also returns a frame here, so the useful assertion is
    # that connect knew it had failed and said why.
    assert t.camera_working is False
    health = t.camera_health()
    assert health["configured"] is True and health["ok"] is False and health["error"]
    await t.close()


async def test_a_dead_camera_stops_handing_out_its_last_good_frame(robotd: FakeRobotd) -> None:
    """A cached frame that never expires is worse than no frame at all.

    `go_to` steers on `get_frame()` every tick for up to 20 s. A camera that died with the
    ball in shot would show the ball in shot forever, and the duck would walk at it. Callers
    already treat a missing frame as a reason to stop, so expiry is what turns a dead camera
    back into a stop.
    """
    t = JsonRpcUnixTransport(f"tcp://127.0.0.1:{robotd.port}", camera_url="http://example/x.jpg")
    t._camera_task = object()  # type: ignore[assignment]  # pretend the capture loop is running
    t._frame = Image.new("RGB", (8, 8))

    t._frame_at = time.monotonic()
    assert await t.get_frame() is not None
    t._frame_at = time.monotonic() - 10 * camera_stale_after_s(t.camera_fps)
    assert await t.get_frame() is None
    assert t.frame_age_s() is None


async def test_camera_serves_the_newest_frame_from_memory(robotd: FakeRobotd) -> None:
    t = JsonRpcUnixTransport(f"tcp://127.0.0.1:{robotd.port}", camera_url="http://example/x.jpg")
    made = Image.new("RGB", (8, 8), (1, 2, 3))

    async def fake_fetch() -> Image.Image:
        return made

    t._fetch_frame = fake_fetch  # type: ignore[method-assign]
    await t.connect()
    for _ in range(100):  # the capture loop runs on its own clock
        if t._frame is not None:
            break
        await asyncio.sleep(0.01)
    assert await t.get_frame() is made
    health = t.camera_health()
    assert health["ok"] is True and health["size"] == [8, 8] and health["error"] is None
    await t.close()


async def test_stop_says_when_it_could_not_be_delivered(robotd: FakeRobotd) -> None:
    """A stop into a dead socket used to be swallowed, so the log claimed a stop it never sent."""
    t = JsonRpcUnixTransport(f"tcp://127.0.0.1:{robotd.port}")
    await t.connect()
    await t.stop()
    assert t.stop_error is None

    t._writer = None  # the link is gone, which is exactly when a stop gets asked for
    await t.stop()  # must not raise: this runs from the exception paths
    assert t.stop_error is not None and "not connected" in t.stop_error
    assert (await t.get_state()).extras["stop_error"] == t.stop_error
    await t.close()


async def test_quackd_never_sends_the_two_methods_that_move_or_drop_every_joint(
    registry: VerbRegistry, robotd: FakeRobotd
) -> None:
    """`robot.init` moves every joint and `robot.relax` collapses the robot.

    docs/adapters/microduck/README.md promises neither is ever sent. Nothing asserted it, and both
    names are one typo away from `robot.enable` in the same match statement.
    """
    t = JsonRpcUnixTransport(f"tcp://127.0.0.1:{robotd.port}")
    await t.connect()
    ex = Executor(registry, t, contract=DUCK.frontmatter)
    for verb, params in (
        ("walk", {"vx": 0.1, "duration_s": 0.2}),
        ("kick", {"leg": "left"}),
        ("quack", {"text": "hi"}),
        ("gaze", {"direction": "left"}),
    ):
        await ex.run_verb(verb, params)
    await t.stop()
    spoken = {r["method"] for r in robotd.requests} | {n["method"] for n in robotd.notifications}
    assert not spoken & {"robot.init", "robot.relax"}
    await t.close()


async def test_a_sitting_policy_reads_as_sitting(registry: VerbRegistry) -> None:
    """`sit` is the label robotd reports while the sit-stand network holds the seat.

    This used `sit_rise`, which no robotd has ever reported, and passed because `sit` was
    matched as a substring. The match is exact now, and `sit_rise` reads as unknown."""
    fake = FakeRobotd(policy="sit")
    await fake.start()
    try:
        t = JsonRpcUnixTransport(f"tcp://127.0.0.1:{fake.port}")
        await t.connect()
        assert (await t.get_state()).posture == "sitting"
        # and a sitting duck is refused a walk, rather than dragged along the floor
        ex = Executor(registry, t, contract=DUCK.frontmatter)
        result = await ex.run_verb("walk", {"vx": 0.1, "duration_s": 0.2})
        assert not result.ok and "sitting" in result.summary
        await t.close()
    finally:
        await fake.stop()


async def test_a_malformed_line_does_not_kill_the_pump(robotd: FakeRobotd) -> None:
    """One unparseable line must not take the connection down with it."""
    t = JsonRpcUnixTransport(f"tcp://127.0.0.1:{robotd.port}", request_timeout_s=0.05)
    await t.connect()
    with pytest.raises(TransportError):
        await t.request("robot.neverAnswered")  # the fake answers this with junk
    assert t._pump is not None and not t._pump.done()
    await t.heartbeat()  # and the link still works afterwards
    assert (await t.get_state()).battery_percent == 66.0
    await t.close()


async def test_a_request_with_no_answer_times_out_and_forgets_itself(robotd: FakeRobotd) -> None:
    t = JsonRpcUnixTransport(f"tcp://127.0.0.1:{robotd.port}", request_timeout_s=0.05)
    await t.connect()
    with pytest.raises(TransportError, match="no answer within"):
        await t.request("robot.neverAnswered")
    assert not t._pending  # a timed-out request must not leak its future
    await t.close()


async def test_a_substituted_sound_says_it_was_substituted(robotd: FakeRobotd) -> None:
    """Seven tags and no TTS, so substituting is right — doing it silently was not."""
    t = JsonRpcUnixTransport(f"tcp://127.0.0.1:{robotd.port}")
    await t.connect()
    ack = await t.send_intent(Intent.sound("trumpet", "ta-da"))
    assert ack.accepted and ack.reason is not None and "not a duck sound" in ack.reason
    assert robotd.requests[-1]["params"] == {"tag": "chirp"}
    assert (await t.send_intent(Intent.sound("greet", "hi"))).reason is None
    await t.close()


async def test_an_upstream_error_carries_its_code(robotd: FakeRobotd) -> None:
    """`BUSY` and `PERMISSION_DENIED` are different answers, and both were flattened to text.
    So are a call robotd does not have and a member of one it does not know, the two ways a
    robotd from another release refuses, each by name."""
    t = JsonRpcUnixTransport(f"tcp://127.0.0.1:{robotd.port}")
    await t.connect()
    with pytest.raises(TransportError) as caught:
        await t.request("robot.notAMethod")
    assert caught.value.code == up.ERR_METHOD_NOT_FOUND and "robot.notAMethod" in str(caught.value)
    with pytest.raises(TransportError) as caught:
        await t.request(up.ROBOT_LOOK.name, {"x": 1.0, "elevation": 0.2})
    assert caught.value.code == up.ERR_INVALID_PARAMS and "`elevation`" in str(caught.value)
    # on the version quackd read, which upstream does not promise means the same calls
    assert f"API v{CURRENT_API}" in str(caught.value) and "has not read" in str(caught.value)
    await t.close()


async def test_no_camera_without_url(robotd: FakeRobotd) -> None:
    t = JsonRpcUnixTransport(f"tcp://127.0.0.1:{robotd.port}")
    await t.connect()
    assert await t.get_frame() is None
    await t.close()


# ── the robot at the pin ─────────────────────────────────────────────────────────────────

#: Every verb of the Microduck's that reaches robotd's socket without a camera.
WIRE_VERBS = parse_duck_text(
    "---\nduck: 0\nname: t\ndescription: d\nverbs:\n"
    "  allow: [walk, kick, quack, gaze, stand_up]\nsuccess: [x]\n---\n# Task\nx\n"
)


async def test_nothing_quackd_sends_is_a_member_robotd_does_not_know(
    registry: VerbRegistry, robotd: FakeRobotd
) -> None:
    """Every params type robotd decodes denies unknown fields, so a member it does not know is
    refused by name, on that one call. That is how a robotd from another release says a call
    changed shape, and a notification gets no answer at all: a `robot.move` robotd refused
    would be a walk quackd reported and the duck never took. The fake decodes the way robotd
    does at the pin, and every verb that reaches the socket, a held gaze, a pose and a stop go
    through it with nothing refused."""
    robotd.fallen, robotd.recover_on_enable = True, True  # so that stand_up has to ask
    t = JsonRpcUnixTransport(f"tcp://127.0.0.1:{robotd.port}", gaze_hold_s=HOLD_S)
    await t.connect()
    t.sleep = _quick  # type: ignore[method-assign]
    ex = Executor(registry, t, contract=WIRE_VERBS.frontmatter)
    for verb, params in (
        ("stand_up", {}),
        ("walk", {"vx": 0.1, "duration_s": 0.2}),
        ("kick", {"leg": "left"}),
        ("quack", {"text": "hi"}),
        ("gaze", {"direction": "left"}),
    ):
        result = await ex.run_verb(verb, params)
        assert result.ok, (verb, result.summary)
    await _eventually(lambda: _heads(robotd) > 0, "the gaze going out again as robot.head")
    assert (await t.send_intent(Intent(kind="pose", params={"z": 0.0, "pitch": 0.1}))).accepted
    await t.stop()
    await _landed(t)
    assert robotd.rejected == []
    sent = {r["method"] for r in robotd.requests} | {n["method"] for n in robotd.notifications}
    wire = (up.HELLO, up.ROBOT_SUBSCRIBE, up.ROBOT_ENABLE, up.ROBOT_MOVE, up.ROBOT_DO)
    wire += (up.ROBOT_SOUND, up.ROBOT_LOOK, up.ROBOT_HEAD, up.ROBOT_POSE, up.ROBOT_STOP)
    wire += (up.ROBOT_HEALTH,)
    assert {ref.name for ref in wire} <= sent
    await t.close()


async def test_a_call_the_robot_does_not_have_fails_the_verb_that_needs_it(
    registry: VerbRegistry,
) -> None:
    """A robotd from another release answers METHOD_NOT_FOUND naming a call it does not have,
    which is the refusal upstream put where the version gate was. The verb that needed the call
    fails with the code, the name and both versions, instead of reporting what the duck did not
    do, since a robot quackd has not read is served now and this is how it says no."""
    newer = CURRENT_API + 1
    fake = FakeRobotd(api_version=newer, missing=frozenset({up.ROBOT_LOOK.name}))
    await fake.start()
    try:
        t = JsonRpcUnixTransport(f"tcp://127.0.0.1:{fake.port}")
        await t.connect()
        ex = Executor(registry, t, contract=DUCK.frontmatter)
        result = await ex.run_verb("gaze", {"direction": "left"})
        assert not result.ok and str(up.ERR_METHOD_NOT_FOUND) in result.summary
        assert up.ROBOT_LOOK.name in result.summary
        assert f"robotd speaks API v{newer} and quackd read API v{CURRENT_API}" in result.summary
        await t.close()
    finally:
        await fake.stop()


async def test_the_state_lists_as_assumed_only_what_upstream_api_calls_unverified(
    robotd: FakeRobotd,
) -> None:
    """`extras.assumptions` is what the pilot is told not to take for the robot's own work, so
    nothing read in robotd's source belongs in it. Posture was its one entry until the policy
    labels were read at the pin, and what is left is stand_up's `robot.enable`, which upstream
    does not promise gets a fallen duck up."""
    t = JsonRpcUnixTransport(f"tcp://127.0.0.1:{robotd.port}")
    await t.connect()
    listed = (await t.get_state()).extras["assumptions"]
    assert listed == [up.STAND_UP_RPC.name]
    assert set(listed) <= {ref.name for ref in up.refs_by_status("UNVERIFIED")}
    await t.close()


# ── posture fails closed ─────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(("label", "posture"), [("walk", "standing"), ("stand", "standing")])
async def test_the_two_labels_that_stand_the_duck_up(label: str, posture: str) -> None:
    fake = FakeRobotd(policy=label, skills=STOCK_SKILLS)
    await fake.start()
    try:
        t = JsonRpcUnixTransport(f"tcp://127.0.0.1:{fake.port}")
        await t.connect()
        state = await t.get_state()
        assert state.posture == posture and state.extras["posture_reason"] is None
        await t.close()
    finally:
        await fake.stop()


@pytest.mark.parametrize(
    "label",
    [
        "sit_rise",  # no robotd says this; the substring match read it as sitting
        "rise",
        "ground_pick",
        "skill",
        "held",
        "homing",
        "rest",
        "limp_fall",
        "limp_pose",
        "kick_left",  # one of the robot's own skills
        "kick_left:unwind",
        "moonwalk",  # whatever a newer robotd adds
    ],
)
async def test_every_other_label_reads_as_unknown_and_refuses_a_walk(
    registry: VerbRegistry, label: str
) -> None:
    """robotd reports far more labels than the three quackd reads, and every one without `sit`
    in it used to read as standing unless the duck had fallen, so a duck in somebody's hands,
    one riding a fall down, one halfway through a kick and whatever a newer robot adds could
    each be sent a walk. Each now reads as unknown, says which label it was, and refuses twice:
    in the precondition, and in the transport, for a caller that never checked one."""
    fake = FakeRobotd(policy=label, skills=STOCK_SKILLS)
    await fake.start()
    try:
        t = JsonRpcUnixTransport(f"tcp://127.0.0.1:{fake.port}")
        await t.connect()
        state = await t.get_state()
        assert state.posture == "unknown" and state.extras["fall_detection"] is True
        assert repr(label) in state.extras["posture_reason"]

        ex = Executor(registry, t, contract=DUCK.frontmatter)
        result = await ex.run_verb("walk", {"vx": 0.1, "duration_s": 0.2})
        assert not result.ok and result.summary.startswith("cannot walk: posture is unknown")
        assert repr(label) in result.summary

        ack = await t.send_intent(Intent.move(0.1, 0.0, 0.0))
        assert not ack.accepted and repr(label) in (ack.reason or "")
        await _landed(t)
        assert _moves(fake) == 0
        await t.close()
    finally:
        await fake.stop()


async def test_a_skill_named_like_a_gait_is_still_a_skill() -> None:
    """robotd keeps only `ground_pick` and `sit_toggle` back from the names a policy set may
    give a skill, so a set can call one `walk`. The robot's own skill list is read first."""
    fake = FakeRobotd(policy="walk", skills=["walk"])
    await fake.start()
    try:
        t = JsonRpcUnixTransport(f"tcp://127.0.0.1:{fake.port}")
        await t.connect()
        state = await t.get_state()
        assert (
            state.posture == "unknown"
            and "also one of its skills" in state.extras["posture_reason"]
        )
        await t.close()
    finally:
        await fake.stop()


async def test_a_frame_that_names_no_policy_is_unknown() -> None:
    fake = FakeRobotd(policy=None)
    await fake.start()
    try:
        t = JsonRpcUnixTransport(f"tcp://127.0.0.1:{fake.port}")
        await t.connect()
        state = await t.get_state()
        assert state.posture == "unknown" and "names no policy" in state.extras["posture_reason"]
        await t.close()
    finally:
        await fake.stop()


async def test_a_limp_duck_is_not_standing_whatever_its_label_says(registry: VerbRegistry) -> None:
    """`safety.limp` is robotd dropping the gains so the duck yields. Read before the label,
    which on its own would have stood this one up."""
    fake = FakeRobotd(policy="walk", limp=True)
    await fake.start()
    try:
        t = JsonRpcUnixTransport(f"tcp://127.0.0.1:{fake.port}")
        await t.connect()
        state = await t.get_state()
        assert state.posture == "unknown" and "safety.limp" in state.extras["posture_reason"]
        ex = Executor(registry, t, contract=DUCK.frontmatter)
        result = await ex.run_verb("walk", {"vx": 0.1, "duration_s": 0.2})
        assert not result.ok and "safety.limp" in result.summary
        await _landed(t)
        assert _moves(fake) == 0
        await t.close()
    finally:
        await fake.stop()


async def test_a_duck_somebody_holds_is_held_before_it_is_fallen(registry: VerbRegistry) -> None:
    """Held at an angle, a duck reads as fallen as well as picked up. "Fallen; run stand_up
    first" sent the pilot to the one verb that asks the policy to drive, with the legs in a
    person's hands. Being held is read first, everything that moves the duck refuses on it,
    `stand_up` among them, and nothing that moves a joint goes out."""
    fake = FakeRobotd(policy="held", picked_up=True, fallen=True)
    await fake.start()
    try:
        t = JsonRpcUnixTransport(f"tcp://127.0.0.1:{fake.port}")
        await t.connect()
        state = await t.get_state()
        assert state.posture == "unknown" and state.extras["picked_up"] is True
        assert "holding" in state.extras["posture_reason"]

        ex = Executor(registry, t, contract=MOVERS.frontmatter)
        for verb, params in (
            ("walk", {"vx": 0.1, "duration_s": 0.2}),
            ("kick", {"leg": "left"}),
            ("gaze", {"direction": "left"}),
            ("stand", {}),
            ("stand_up", {}),
        ):
            result = await ex.run_verb(verb, params)
            assert not result.ok and "holding" in result.summary, (verb, result.summary)
            assert "stand_up first" not in result.summary, verb
        await _landed(t)
        spoken = {r["method"] for r in fake.requests} | {n["method"] for n in fake.notifications}
        assert not spoken & {"robot.move", "robot.do", "robot.look", "robot.enable"}
        await t.close()
    finally:
        await fake.stop()


async def _quick(seconds: float) -> None:
    """`stand_up` gives the policy three seconds. The fake needs one frame to answer in."""
    await asyncio.sleep(min(seconds, 0.3))


@pytest.mark.parametrize(
    ("policy", "fallen", "recovers", "enabled", "ok", "summary"),
    [
        ("walk", False, False, False, True, "already upright"),
        ("sit", False, False, False, False, "the duck is upright and seated: run stand"),
        ("walk", True, True, True, True, "upright"),
        ("homing", False, True, True, True, "upright"),
        ("homing", False, False, True, False, "posture is unknown (robotd reports policy 'homing'"),
        ("walk", True, False, True, False, "robotd does not promise to get a fallen duck up"),
    ],
    ids=["standing", "seated", "fallen", "unknown", "unknown-after", "fallen-stays-down"],
)
async def test_stand_up_reads_the_duck_before_it_asks_the_policy_to_drive(
    registry: VerbRegistry,
    policy: str,
    fallen: bool,
    recovers: bool,
    enabled: bool,
    ok: bool,
    summary: str,
) -> None:
    """`robot.enable` is motion. It went out before anything was read, to a duck already on its
    feet and to one on its seat as much as to one on the floor, and afterwards a duck that was
    merely not fallen was called upright. Now a fallen duck and one that is `unknown` for a
    reason other than a person holding it are asked, and only one that reads standing after is
    called upright. One still down afterwards was told an onboard recovery had not finished, and
    on daemon-v0.16.1 robotd runs none (`up.FALLEN_IS_A_REPORT`)."""
    fake = FakeRobotd(policy=policy, fallen=fallen)
    fake.recover_on_enable = recovers
    await fake.start()
    try:
        t = JsonRpcUnixTransport(f"tcp://127.0.0.1:{fake.port}")
        await t.connect()
        t.sleep = _quick  # type: ignore[method-assign]
        result = await Executor(registry, t, contract=MOVERS.frontmatter).run_verb("stand_up", {})
        assert result.ok is ok and summary in result.summary, result.summary
        assert ("robot.enable" in [r["method"] for r in fake.requests]) is enabled
        await t.close()
    finally:
        await fake.stop()


async def test_stand_up_asks_nothing_of_a_duck_nobody_can_see(
    registry: VerbRegistry, robotd: FakeRobotd
) -> None:
    """With no state arriving, quackd cannot see whether the duck is down or in somebody's
    hands, so it does not ask the policy to drive it either way."""
    t = JsonRpcUnixTransport(f"tcp://127.0.0.1:{robotd.port}")
    await t.connect()
    assert robotd._stream is not None
    robotd._stream.cancel()  # the stream stops, and nothing marks the last frame fresh again
    with contextlib.suppress(asyncio.CancelledError):
        await robotd._stream
    robotd._stream = None
    t._last_state_at = None
    result = await Executor(registry, t, contract=MOVERS.frontmatter).run_verb("stand_up", {})
    assert not result.ok and "nothing is reporting" in result.summary
    assert "robot.enable" not in [r["method"] for r in robotd.requests]
    await t.close()


async def test_a_move_held_back_holds_every_move_until_a_stop(robotd: FakeRobotd) -> None:
    """The duck reading standing again is not enough: the verb that was walking when it stopped
    standing is still sending, and what it sends is the walk it was doing before."""
    t = JsonRpcUnixTransport(f"tcp://127.0.0.1:{robotd.port}")
    await t.connect()
    assert (await t.send_intent(Intent.move(0.1, 0.0, 0.0))).accepted

    robotd.picked_up, robotd.policy = True, "held"
    await _until(t, lambda s: s["safety"].get("picked_up") is True)
    ack = await t.send_intent(Intent.move(0.1, 0.0, 0.0))
    assert not ack.accepted and "holding" in (ack.reason or "")

    robotd.picked_up, robotd.policy = False, "walk"
    await _until(t, lambda s: s["safety"].get("picked_up") is False and s["policy"] == "walk")
    assert (await t.get_state()).posture == "standing"
    ack = await t.send_intent(Intent.move(0.1, 0.0, 0.0))
    assert not ack.accepted and "until the verb that was moving it stops" in (ack.reason or "")
    await _landed(t)
    assert _moves(robotd) == 1, "only the move from before the duck was picked up went out"

    await t.stop()
    assert (await t.send_intent(Intent.move(0.1, 0.0, 0.0))).accepted
    await _landed(t)
    assert _moves(robotd) == 2

    # the stop a caller sends as an intent is the same robot.stop, and ends the hold the same way
    robotd.policy = "homing"
    await _until(t, lambda s: s["policy"] == "homing")
    assert not (await t.send_intent(Intent.move(0.1, 0.0, 0.0))).accepted
    robotd.policy = "walk"
    await _until(t, lambda s: s["policy"] == "walk")
    assert (await t.send_intent(Intent.stop())).accepted
    assert (await t.send_intent(Intent.move(0.1, 0.0, 0.0))).accepted
    await _landed(t)
    assert _moves(robotd) == 3
    await t.close()


class _BallAhead:
    """A detector that always sees the ball dead ahead, `distance_m` away."""

    name = "ball_ahead"

    def __init__(self, distance_m: float) -> None:
        self.distance_m = distance_m

    def detect(self, image: Image.Image) -> list[Detection]:
        return [
            Detection(
                label="ball",
                cx=0.5,
                cy=0.5,
                area=0.01,
                bearing_deg=0.0,
                est_distance_m=self.distance_m,
            )
        ]


async def test_a_duck_picked_up_during_go_to_does_not_walk_off_when_it_is_put_down(
    registry: VerbRegistry, robotd: FakeRobotd
) -> None:
    """`go_to`'s precondition was read when it started, and it sends its next twist ten times
    a second whatever became of the last. A duck picked up halfway through would have had a
    fresh twist waiting for it the whole time, and walked off on it the moment it was put down,
    since robotd's policy drives whatever twist is freshest. The first `robot.move` after the
    pick-up is held back, and so is every one after it until the verb has stopped the duck."""
    t = JsonRpcUnixTransport(f"tcp://127.0.0.1:{robotd.port}", camera_url="http://example/x.jpg")
    picture = Image.new("RGB", (8, 8))

    async def fetch() -> Image.Image:
        return picture

    t._fetch_frame = fetch  # type: ignore[method-assign]
    await t.connect()
    assert t.camera_working
    ball = _BallAhead(distance_m=1.0)
    ex = Executor(registry, t, contract=MOVERS.frontmatter, detector=ball)
    going = asyncio.create_task(ex.run_verb("go_to", {"target": "ball", "timeout_s": 30}))
    try:
        # after its look go_to waits for robotd to let go of any glance (`head_fade_s`), and the
        # gaze hold may send robot.head meanwhile, so it is steering once three moves are out
        await _eventually(lambda: _moves(robotd) >= 3, "go_to steering", wait_s=10.0)

        robotd.picked_up, robotd.policy = True, "held"  # somebody picks the duck up
        await _until(t, lambda s: s["safety"].get("picked_up") is True)
        await _landed(t)
        held_at = _moves(robotd)
        await asyncio.sleep(0.3)  # three of go_to's ticks, with the duck in somebody's hands
        await _landed(t)
        assert _moves(robotd) == held_at, "robot.move kept going out to a duck somebody holds"

        robotd.picked_up, robotd.policy = False, "walk"  # and puts it down on its feet
        await _until(t, lambda s: s["safety"].get("picked_up") is False and s["policy"] == "walk")
        await asyncio.sleep(0.5)  # five more ticks, the duck standing again
        await _landed(t)
        assert not going.done(), "go_to is still running, so the hold is what kept the duck still"
        assert _moves(robotd) == held_at, "a robot.move went out after the duck was put down"

        ball.distance_m = 0.1  # let go_to arrive, so it ends as every walking verb does, stopped
        result = await asyncio.wait_for(going, 5)
    finally:
        going.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await going
    assert result.ok and robotd.requests[-1]["method"] == up.ROBOT_STOP.name

    walked = await ex.run_verb("walk", {"vx": 0.1, "duration_s": 0.3})  # a new verb walks again
    await _landed(t)
    assert walked.ok and _moves(robotd) > held_at
    await t.close()


# ── a robotd quackd has not read ─────────────────────────────────────────────────────────


async def _eventually(check: Callable[[], bool], what: str, wait_s: float = 30.0) -> None:
    """Return once `check` holds. Waited for rather than slept, so a loaded machine that is slow
    to deliver frames is slow here too rather than wrong; `wait_s` only ends a test whose
    `check` never comes true."""
    deadline = time.monotonic() + wait_s
    while not check():
        if time.monotonic() > deadline:
            raise AssertionError(f"{what} did not happen within {wait_s:g} s")
        await asyncio.sleep(0.01)


async def _past_the_echo_window(t: JsonRpcUnixTransport, since: float) -> None:
    """Return once `MOVE_ECHO_FRAMES` state frames have been read since `since`, the newest of
    them `move_echo_after_s` after it: the first frame a twist sent at `since` is judged on."""
    window, read = move_echo_after_s(t.state_hz), t._frames_read
    await _eventually(
        lambda: (
            t._frames_read - read >= MOVE_ECHO_FRAMES
            and t._last_state_at is not None
            and t._last_state_at - since >= window
        ),
        "the echo window closing",
    )


def _drop_by_switch(fake: FakeRobotd) -> None:
    fake.drop_moves = True


def _drop_by_renaming(fake: FakeRobotd) -> None:
    # a robotd whose MoveParams call the turn rate `wz`, as a newer one might: quackd's `vyaw`
    # is then a member it does not know, which it refuses by name, to nobody, as a notification
    fake.params["robot.move"] = (frozenset({"vx", "vy", "wz"}), frozenset())


@pytest.mark.parametrize("drop", [_drop_by_switch, _drop_by_renaming], ids=["dropped", "renamed"])
async def test_a_move_robotd_did_not_take_holds_every_move_until_a_stop(
    robotd: FakeRobotd, drop: Callable[[FakeRobotd], None]
) -> None:
    """robotd answers no notification, so a `robot.move` it could not parse used to be a walk
    quackd reported and the duck never took, and serving a robotd quackd has not read is what
    makes one likely. Its own state says what it was last asked for (`up.MOVE_ECHO`): a twist
    that state has not shown `move_echo_after_s` later holds every `robot.move` back, the way a
    duck that stops standing does, until the verb that sent it stops."""
    drop(robotd)
    t = JsonRpcUnixTransport(f"tcp://127.0.0.1:{robotd.port}")
    await t.connect()
    assert (await t.send_intent(Intent.move(0.1, 0.0, 0.0))).accepted  # nothing to judge yet
    await _past_the_echo_window(t, time.monotonic())
    ack = await t.send_intent(Intent.move(0.1, 0.0, 0.0))
    reason = ack.reason or ""
    assert not ack.accepted and reason.startswith("robotd has not taken robot.move"), reason
    assert "still says it was asked for [0, 0, 0]" in reason
    # both things that look like this from here: a robotd that could not parse the twist, and
    # another client, a gamepad's padd among them, sending one after it (`up.PAD_DRIVES`)
    assert "could not parse" in reason and "another client" in reason
    if drop is _drop_by_renaming:  # refused by name, to nobody, as a notification is
        refused = [why for method, why in robotd.rejected if method == up.ROBOT_MOVE.name]
        assert refused and refused[0].startswith("unknown field `vyaw`"), robotd.rejected
    echo = (await t.get_state()).extras["move_echo"]
    assert echo["taken"] is False and echo["twist"] == [0.1, 0.0, 0.0]
    assert echo["requested"] == [0.0, 0.0, 0.0]
    assert echo["after_s"] >= move_echo_after_s(t.state_hz)
    await _landed(t)
    assert _moves(robotd) == 1, "a robot.move went out after the robot's state had not shown one"

    # robotd taking twists again does not lift the hold: the verb that ran into it has to stop
    robotd.drop_moves, robotd.params = False, dict(PARAMS)
    ack = await t.send_intent(Intent.move(0.1, 0.0, 0.0))
    assert not ack.accepted and "since robotd stopped showing the twists" in (ack.reason or "")
    await t.stop()
    assert (await t.send_intent(Intent.move(0.2, 0.0, 0.0))).accepted
    await _until(t, lambda s: s["move"]["requested"] == [0.2, 0.0, 0.0])
    assert t.move_echo is not None and t.move_echo["taken"] is True
    assert t.move_echo["twist"] == [0.2, 0.0, 0.0]
    await t.close()


async def test_a_frame_read_late_is_not_taken_for_robotds_answer(robotd: FakeRobotd) -> None:
    """A frame robotd built before a twist reached it can be read well after the twist went
    out, when quackd's own process stalls with the frame still in its socket. Judged on time
    alone, that one frame read as robotd not taking the twist, and held back a walk it took. The
    echo waits for `MOVE_ECHO_FRAMES` frames read since the twist went out as well, so a stall
    delays the verdict rather than inventing one.

    The stream is stopped and the frames handed to the transport one at a time, all of them
    built before the twist, so nothing about this machine's timing decides the outcome."""
    robotd.drop_moves = True  # the state never shows the twist, so every frame here is stale
    t = JsonRpcUnixTransport(f"tcp://127.0.0.1:{robotd.port}")
    await t.connect()
    assert robotd._stream is not None
    robotd._stream.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await robotd._stream
    robotd._stream = None
    await _landed(t)  # every frame the stream wrote has been read; from here, only these
    stale = robotd.frame()["params"]
    t._took_state(stale)  # so the duck reads standing however long the line above took
    assert (await t.send_intent(Intent.move(0.1, 0.0, 0.0))).accepted
    sent_at, read_then, twist = t._unshown[0]
    # long enough ago that time alone has closed the window
    t._unshown[0] = (sent_at - 10 * move_echo_after_s(t.state_hz), read_then, twist)
    for _ in range(MOVE_ECHO_FRAMES - 1):
        t._took_state(stale)  # read now, built before the twist could show in it
        assert (await t.send_intent(Intent.move(0.1, 0.0, 0.0))).accepted
    t._took_state(stale)
    ack = await t.send_intent(Intent.move(0.1, 0.0, 0.0))
    assert not ack.accepted and (ack.reason or "").startswith("robotd has not taken robot.move")
    await t.close()


async def test_a_walk_robotd_does_not_take_stops_and_says_so(
    registry: VerbRegistry, robotd: FakeRobotd
) -> None:
    """The walk used to say `walked` for every twist it had sent, taken or not. Now the slice
    after the echo window fails it, the verb stops the duck as it does on any refusal, and the
    pilot is told what robotd's state said instead."""
    robotd.drop_moves = True
    t = JsonRpcUnixTransport(f"tcp://127.0.0.1:{robotd.port}")
    await t.connect()
    duration = 10 * move_echo_after_s(t.state_hz)
    result = await Executor(registry, t, contract=DUCK.frontmatter).run_verb(
        "walk", {"vx": 0.1, "duration_s": duration}
    )
    assert not result.ok and "robotd has not taken robot.move" in result.summary, result.summary
    assert robotd.requests[-1]["method"] == up.ROBOT_STOP.name
    await _landed(t)
    assert _moves(robotd) < round(duration / MOVE_RESEND_S), "the walk ran its whole length"
    await t.close()


async def test_a_walk_robotd_takes_is_shown_and_never_held(
    registry: VerbRegistry, robotd: FakeRobotd
) -> None:
    """The same walk on a robotd that takes every twist runs its whole length, and the state
    says how long after it went out robotd showed the last one."""
    t = JsonRpcUnixTransport(f"tcp://127.0.0.1:{robotd.port}")
    await t.connect()
    duration = 3 * move_echo_after_s(t.state_hz)
    result = await Executor(registry, t, contract=DUCK.frontmatter).run_verb(
        "walk", {"vx": 0.1, "duration_s": duration}
    )
    assert result.ok, result.summary
    await _landed(t)
    assert _moves(robotd) == round(duration / MOVE_RESEND_S)
    echo = t.move_echo
    assert echo is not None and echo["taken"] is True and echo["twist"] == [0.1, 0.0, 0.0]
    await t.close()


#: What asyncio's StreamReader takes in a line unless it is told otherwise, 64 KiB.
ASYNCIO_DEFAULT_LIMIT = 2**16


async def test_a_state_frame_longer_than_asyncios_default_line_is_read(
    robotd: FakeRobotd,
) -> None:
    """`robot.state` has grown with every release since API 24, and robotd bounds nothing it
    writes. A line over asyncio's default limit raised out of the pump, which died without a
    word, and every answer after it was a timeout."""
    t = JsonRpcUnixTransport(f"tcp://127.0.0.1:{robotd.port}")
    await t.connect()
    pose = {"pos": [0.0, 0.0, 0.0], "quat": [1.0, 0.0, 0.0, 0.0]}
    count = (READER_LIMIT // 2) // len(json.dumps(pose))
    robotd.extra_state = {"skeleton": [pose] * count}
    assert ASYNCIO_DEFAULT_LIMIT < len(json.dumps(robotd.frame())) < READER_LIMIT
    await _eventually(
        lambda: len((t._last_state or {}).get("skeleton") or ()) == count,
        "a state frame longer than asyncio's default line reaching quackd",
    )
    assert (await t.get_state()).posture == "standing"
    await t.close()


async def test_a_line_over_the_limit_is_skipped_and_the_link_lives(robotd: FakeRobotd) -> None:
    """A line longer than the reader takes is let go of, and the next one is read: the pump
    lives, a request is still answered, and state frames still arrive."""
    t = JsonRpcUnixTransport(f"tcp://127.0.0.1:{robotd.port}")
    await t.connect()
    sent = robotd.frames_sent
    robotd.extra_state = {"skeleton": "x" * (2 * READER_LIMIT)}
    await _eventually(lambda: robotd.frames_sent >= sent + 2, "two long lines going out")
    robotd.extra_state = {}
    after = time.monotonic()
    await t.heartbeat()  # answered, so the pump read past both long lines
    await _eventually(
        lambda: t._last_state_at is not None and t._last_state_at > after,
        "a state frame after the long lines",
    )
    assert t._pump is not None and not t._pump.done()
    assert (await t.get_state()).posture == "standing"
    await t.close()


@pytest.mark.parametrize(
    ("picked_up", "detection"),
    [(False, True), (True, True), (None, False)],
    ids=["reported", "held", "older-than-the-detector"],
)
async def test_the_state_says_whether_the_robot_reports_a_pickup(
    picked_up: bool | None, detection: bool
) -> None:
    """A robotd from before its pick-up detector leaves `safety.picked_up` out, which reads as
    never picked up, so a duck in somebody's hands is sent walks like any other. The version
    cannot say which robot that is, so the state says, from the frame itself."""
    fake = FakeRobotd(picked_up=picked_up, policy="held" if picked_up else "walk")
    await fake.start()
    try:
        t = JsonRpcUnixTransport(f"tcp://127.0.0.1:{fake.port}")
        await t.connect()
        assert (await t.get_state()).extras["pickup_detection"] is detection
        assert t.safety == {"pickup_detection": detection}
        t._last_state_at = None  # a stream that never started says neither
        assert (await t.get_state()).extras["pickup_detection"] is None and t.safety == {}
        await t.close()
    finally:
        await fake.stop()


@pytest.mark.parametrize("reports", [True, False], ids=["frames", "older-than-frames"])
async def test_the_state_says_whether_the_robot_reports_its_frames(reports: bool) -> None:
    fake = FakeRobotd(reports_frames=reports)
    await fake.start()
    try:
        t = JsonRpcUnixTransport(f"tcp://127.0.0.1:{fake.port}")
        await t.connect()
        assert (await t.get_state()).extras["robotd"]["state_frames"] is reports
        await t.close()
    finally:
        await fake.stop()


@pytest.mark.parametrize(
    ("path", "answers", "recorded"),
    [
        (up.MEDIAD_FRAME_PATH, True, True),
        (up.MEDIAD_FRAME_PATH, False, False),
        ("/x.jpg", True, None),
    ],
    ids=["answered", "silent", "another-camera"],
)
async def test_the_state_says_whether_mediads_frame_answered(
    robotd: FakeRobotd, path: str, answers: bool, recorded: bool | None
) -> None:
    """mediad's `GET /frame` arrived in daemon-v0.13.0, so a robotd the floor serves may have
    nothing there. quackd knows only when `--camera-url` asks it, and says so then."""
    t = JsonRpcUnixTransport(
        f"tcp://127.0.0.1:{robotd.port}", camera_url=f"http://127.0.0.1:1{path}"
    )
    picture = Image.new("RGB", (8, 8))

    async def fetch() -> Image.Image:
        if not answers:
            raise OSError("connection refused")
        return picture

    t._fetch_frame = fetch  # type: ignore[method-assign]
    await t.connect()
    assert (await t.get_state()).extras["robotd"]["mediad_frame"] is recorded
    await t.close()


@contextlib.contextmanager
def _robotd_on_its_own_loop(**kwargs: Any) -> Iterator[FakeRobotd]:
    """A fake robotd serving from a loop in a thread of its own, for code that runs a loop of
    its own, as `doctor.probe` does with `asyncio.run`."""
    loop = asyncio.new_event_loop()
    started: list[FakeRobotd] = []
    ready = threading.Event()

    def serve() -> None:
        asyncio.set_event_loop(loop)
        fake = FakeRobotd(**kwargs)
        loop.run_until_complete(fake.start())
        started.append(fake)
        ready.set()
        loop.run_forever()

    thread = threading.Thread(target=serve, name="fake-robotd", daemon=True)
    thread.start()
    assert ready.wait(30), "the fake robotd never started"
    try:
        yield started[0]
    finally:
        asyncio.run_coroutine_threadsafe(started[0].stop(), loop).result(30)
        loop.call_soon_threadsafe(loop.stop)
        thread.join(30)
        loop.close()


def _probed(**kwargs: Any) -> Any:
    from quackd import doctor
    from quackd_microduck import describe

    with _robotd_on_its_own_loop(**kwargs) as fake:
        address = f"tcp://127.0.0.1:{fake.port}"
        report = doctor.probe("microduck:jsonrpc", describe("jsonrpc"), address, None, None)
    assert report.ok and report.error is None, report
    return report


def test_doctor_says_which_robotd_answered_and_what_it_cannot_see() -> None:
    """`doctor` is the hardware checklist's go/no-go gate, so the robot's release, a version
    quackd has not read and a robotd with no pick-up detector are on its screen before
    anything moves: a row for the first, advice for the other two."""
    newer = CURRENT_API + 1
    report = _probed(api_version=newer, picked_up=None)
    rows = {row.what: row for row in report.rows}
    release = up.RELEASE.removeprefix("daemon-v")
    assert rows["  robotd"].value == f"{release}, API v{newer}, revision {up.PIN[:7]}"
    assert (rows["  pickup_detection"].value, rows["  pickup_detection"].state) == ("False", "warn")
    advice = "\n".join(report.advisories)
    assert f"API v{newer}" in advice and "newer than" in advice, advice
    assert "nothing on this robot reports when somebody is holding it" in advice

    report = _probed()
    rows = {row.what: row for row in report.rows}
    assert (rows["  pickup_detection"].value, rows["  pickup_detection"].state) == ("True", "plain")
    advice = "\n".join(report.advisories)
    assert "API v" not in advice and "holding" not in advice, advice


# ── the gaze hold, and where the camera points ──────────────────────────────────────────

#: How often a held gaze goes out again in these tests: twenty times as often as on a robot, so a
#: test sees a run of re-sends in the time a robot would send one.
HOLD_S = GAZE_HOLD_S / 20


async def _looked(fake: FakeRobotd, bearing_deg: float = 30.0) -> JsonRpcUnixTransport:
    """A transport connected to `fake` that has looked `bearing_deg` to the left, as `gaze` does,
    and whose hold has gone out again at least once since. The fake does not glance, so there is
    no glance for the hold to wait out before it judges what the head shows."""
    t = JsonRpcUnixTransport(f"tcp://127.0.0.1:{fake.port}", gaze_hold_s=HOLD_S)
    await t.connect()
    t.head_fade_s = 0.0
    rad = math.radians(bearing_deg)
    assert (await t.send_intent(Intent.look(math.cos(rad), math.sin(rad), 0.0))).accepted
    sent = _heads(fake)
    await _eventually(lambda: _heads(fake) > sent, "the held gaze going out again")
    return t


async def _nothing_more_goes_out(t: JsonRpcUnixTransport, fake: FakeRobotd) -> None:
    """Wait ten hold periods, and check that no `robot.head` reached the fake in them."""
    await _landed(t)
    sent = _heads(fake)
    await asyncio.sleep(10 * HOLD_S)
    await _landed(t)
    assert _heads(fake) == sent, "robot.head kept going out"


async def test_a_gaze_is_sent_again_until_something_ends_it(
    registry: VerbRegistry, robotd: FakeRobotd
) -> None:
    """Since daemon-v0.16.1 robotd glances around on its own two seconds after anybody last
    steered the head (`up.IDLE_HEAD`), so one `robot.look` no longer holds a gaze, and a pilot's
    `observe` after its `gaze` could be taken from a head looking somewhere else. The head the
    look answered with goes back as `robot.head`, as upstream says to, at half robotd's wait,
    and the state says what is held."""
    assert JsonRpcUnixTransport().gaze_hold_s == GAZE_HOLD_S == up.IDLE_AFTER_S / 2
    t = JsonRpcUnixTransport(f"tcp://127.0.0.1:{robotd.port}", gaze_hold_s=HOLD_S)
    await t.connect()
    result = await Executor(registry, t, contract=DUCK.frontmatter).run_verb(
        "gaze", {"direction": "left"}
    )
    assert result.ok, result.summary
    answered = dict(zip(HEAD, robotd.head, strict=True))
    await _eventually(lambda: _heads(robotd) >= 3, "the gaze going out three more times")
    sent = [n["params"] for n in robotd.notifications if n["method"] == up.ROBOT_HEAD.name]
    assert all(params == answered for params in sent), sent
    state = await t.get_state()
    assert state.extras["gaze_hold"] == {"head": answered, "every_s": HOLD_S, "paused": None}
    assert state.extras["gaze_released"] is None and robotd.rejected == []
    await t.close()


async def test_a_later_look_replaces_the_held_gaze(robotd: FakeRobotd) -> None:
    """A hold that went on sending the head of the look before would pull the head back from
    the one after, so the old hold ends before the new look goes out, and every `robot.head`
    after it is the new head."""
    t = await _looked(robotd, 30.0)
    before = dict(zip(HEAD, robotd.head, strict=True))
    rad = math.radians(-30.0)
    assert (await t.send_intent(Intent.look(math.cos(rad), math.sin(rad), 0.0))).accepted
    after = dict(zip(HEAD, robotd.head, strict=True))
    assert after != before
    at = robotd.looked_at[-1]

    def later() -> list[Any]:
        heads = robotd.notifications[at:]
        return [n["params"] for n in heads if n["method"] == up.ROBOT_HEAD.name]

    await _eventually(lambda: len(later()) >= 3, "the new gaze going out three times")
    assert all(params == after for params in later()), later()
    await t.close()


@pytest.mark.parametrize("how", ["stop()", "stop intent", "stop verb"])
async def test_a_stop_ends_the_held_gaze(
    registry: VerbRegistry, robotd: FakeRobotd, how: str
) -> None:
    """The `stop` verb is what the pilot is told to reach for when anything looks wrong, and the
    heartbeat sends the same stop when the link is in doubt. A hold that went on pulling the
    head back every second after either would be quackd still steering the robot."""
    t = await _looked(robotd)
    if how == "stop()":
        await t.stop()
    elif how == "stop intent":
        assert (await t.send_intent(Intent.stop())).accepted
    else:
        assert (await Executor(registry, t, contract=DUCK.frontmatter).run_verb("stop", {})).ok
    extras = (await t.get_state()).extras
    assert extras["gaze_hold"] is None and extras["gaze_released"] == "stopped"
    await _nothing_more_goes_out(t, robotd)
    await t.close()


def _pick_up(fake: FakeRobotd, held: bool) -> None:
    fake.picked_up, fake.policy = held, "held" if held else "walk"


def _go_limp(fake: FakeRobotd, limp: bool) -> None:
    fake.limp = limp


def _fall(fake: FakeRobotd, fallen: bool) -> None:
    fake.fallen = fallen


def _kick(fake: FakeRobotd, kicking: bool) -> None:
    fake.policy = "kick_left" if kicking else "walk"  # one of the robot's own skills


@pytest.mark.parametrize(
    ("change", "said"),
    [
        (_pick_up, "somebody is holding the duck"),
        (_go_limp, "safety.limp"),
        (_fall, "the duck has fallen"),
        (_kick, "'kick_left'"),
    ],
    ids=["picked-up", "limp", "fallen", "off-the-allowlist"],
)
async def test_a_held_gaze_waits_while_the_duck_is_not_standing_or_sitting(
    robotd: FakeRobotd, change: Callable[[FakeRobotd, bool], None], said: str
) -> None:
    """quackd moves nothing of a duck it cannot vouch for, and the head is part of it, so no
    `robot.head` goes out while the duck is in somebody's hands, limp, fallen or on a label
    other than `walk`, `stand` or `sit`. Those pass, so the hold waits rather than ends, and the
    head goes out again once the duck stands."""
    t = await _looked(robotd)
    change(robotd, True)
    await _eventually(lambda: t._read().posture not in ("standing", "sitting"), "the change")
    await _nothing_more_goes_out(t, robotd)
    hold = (await t.get_state()).extras["gaze_hold"]
    assert hold is not None and said in (hold["paused"] or ""), hold

    change(robotd, False)
    await _eventually(lambda: t._read().posture == "standing", "the duck standing again")
    sent = _heads(robotd)
    await _eventually(lambda: _heads(robotd) > sent, "the held gaze going out again")
    assert (await t.get_state()).extras["gaze_hold"]["paused"] is None
    await t.close()


async def test_a_head_somebody_else_steers_is_let_go_of(robotd: FakeRobotd) -> None:
    """`robot.state.head` is the head slot every client writes, a gamepad posing the head among
    them, ten times a second. Pulling it back every second would be the fight upstream's own idle
    wait exists to spare such a client, so once the head moves away from the held one, the hold
    lets go and says why. robotd smooths a new head in over several frames, and so does the fake
    here."""
    robotd.head_alpha = 0.5
    t = await _looked(robotd, 30.0)
    robotd.steering = [0.0, 0.0, math.radians(-40.0), 0.0]  # a pad turns the head the other way
    await _eventually(lambda: t._gaze is None, "the hold letting go")
    extras = (await t.get_state()).extras
    assert extras["gaze_hold"] is None
    released = extras["gaze_released"] or ""
    assert released.startswith("overridden: robot.state.head went from"), released
    assert "another client is steering the head" in released
    await _nothing_more_goes_out(t, robotd)
    await t.close()


async def test_a_head_still_closing_on_the_held_one_is_not_somebody_elses(
    robotd: FakeRobotd,
) -> None:
    """robotd's smoothing is a setting, and a robot set to smooth slowly shows a head that is
    not yet the held one for many frames after the look. It closes on it every frame, which no
    other client's head does, so the hold keeps it."""
    robotd.head_alpha = 0.1
    t = await _looked(robotd, 30.0)
    reads = t._frames_read
    await _eventually(lambda: t._frames_read >= reads + 10, "ten more state frames")
    held = dict(zip(HEAD, robotd.head, strict=True))
    assert robotd.shown != robotd.head, "the fake's head reached the held one, proving nothing"
    assert t._gaze is not None and t.gaze_released is None, t.gaze_released
    assert (await t.get_state()).extras["gaze_hold"]["head"] == held
    await t.close()


@pytest.mark.parametrize(
    "failure",
    [ConnectionResetError("Connection lost"), TransportError("not connected")],
    ids=["socket-died", "writer-gone"],
)
async def test_a_link_that_fails_under_the_hold_ends_it_quietly(
    robotd: FakeRobotd, failure: Exception
) -> None:
    """A `robot.head` that cannot be written ends the hold, and not with an exception nobody
    reads: a socket that died raises `ConnectionResetError` out of the drain rather than
    `TransportError`. The heartbeat answers for the link, and the state stops saying a gaze is
    held."""
    t = await _looked(robotd)
    task = t._gaze_task
    assert task is not None

    async def broken(method: str, params: dict[str, Any]) -> None:
        raise failure

    t.notify = broken  # type: ignore[method-assign]
    await _eventually(task.done, "the hold ending")
    assert not task.cancelled() and task.exception() is None
    extras = (await t.get_state()).extras
    assert extras["gaze_hold"] is None
    assert str(failure) in (extras["gaze_released"] or ""), extras["gaze_released"]
    await t.close()


async def test_closing_the_connection_ends_the_held_gaze(robotd: FakeRobotd) -> None:
    t = await _looked(robotd)
    task = t._gaze_task
    await t.close()
    assert task is not None and task.cancelled()
    assert t._gaze is None and t._gaze_task is None
    assert t.gaze_released == "the connection was closed"


@pytest.mark.parametrize(
    "answered",
    [
        NO_HEAD,
        [0.0, 0.0, 0.5, 0.0],
        {"neck_pitch": 0.0, "head_pitch": 0.0, "head_yaw": 0.5},
        {"neck_pitch": 0.0, "head_pitch": 0.0, "head_yaw": 0.5, "head_roll": 0.0, "eye": 0.0},
        {"neck_pitch": 0.0, "head_pitch": 0.0, "head_yaw": "0.5", "head_roll": 0.0},
        {"neck_pitch": 0.0, "head_pitch": 0.0, "head_yaw": True, "head_roll": 0.0},
        {"neck_pitch": 0.0, "head_pitch": 0.0, "head_yaw": math.inf, "head_roll": 0.0},
    ],
    ids=["absent", "a-list", "three-joints", "a-fifth-joint", "a-string", "a-bool", "infinite"],
)
async def test_a_look_answered_with_no_head_to_send_back_holds_nothing(
    robotd: FakeRobotd, answered: Any
) -> None:
    """The hold sends `LookResult.head` back as it came, and `HeadParams` sets a joint it is not
    sent to 0, so only exactly its four joints, each a number, are held. Anything else would be
    a joint zeroed or guessed on every re-send, and no hold beats that."""
    robotd.look_head = answered
    t = JsonRpcUnixTransport(f"tcp://127.0.0.1:{robotd.port}", gaze_hold_s=HOLD_S)
    await t.connect()
    assert (await t.send_intent(Intent.look(1.0, 0.5, 0.0))).accepted
    await _nothing_more_goes_out(t, robotd)
    assert _heads(robotd) == 0 and t._gaze is None
    assert (await t.get_state()).extras["gaze_hold"] is None
    await t.close()


async def test_a_dry_run_holds_no_gaze(registry: VerbRegistry, robotd: FakeRobotd) -> None:
    """A dry run sends nothing, and the hold starts only from a look robotd answered, so a dry
    run's `gaze` leaves nothing behind that goes on sending."""
    t = JsonRpcUnixTransport(f"tcp://127.0.0.1:{robotd.port}", gaze_hold_s=HOLD_S)
    await t.connect()
    ex = Executor(registry, t, contract=DUCK.frontmatter, dry_run=True)
    result = await ex.run_verb("gaze", {"direction": "left"})
    assert result.ok and result.data.get("dry_run") is True
    await _nothing_more_goes_out(t, robotd)
    assert _heads(robotd) == 0 and up.ROBOT_LOOK.name not in [r["method"] for r in robotd.requests]
    await t.close()


@pytest.mark.parametrize(
    ("yaw_deg", "pitch_deg"), [(30.0, 0.0), (-45.0, 0.0), (0.0, 14.0), (20.0, -10.0)]
)
async def test_the_state_says_where_the_camera_points(
    robotd: FakeRobotd, yaw_deg: float, pitch_deg: float
) -> None:
    """The simulators have always said where the head points as `head_yaw_deg`, and a flock
    member adds it to a camera bearing to get one from the body. The real duck never said, so on
    a robot the camera read as straight ahead wherever the head had turned. `frames.camera` is
    where robotd's own FK puts the camera, and its optical axis is the answer."""
    t = JsonRpcUnixTransport(f"tcp://127.0.0.1:{robotd.port}")
    await t.connect()
    yaw, pitch = math.radians(yaw_deg), math.radians(pitch_deg)
    look = Intent.look(math.cos(yaw), math.sin(yaw), math.tan(pitch))
    assert (await t.send_intent(look)).accepted
    await _until(t, lambda s: s["head"] == robotd.head)
    extras = (await t.get_state()).extras
    assert extras["head_yaw_deg"] == pytest.approx(yaw_deg, abs=0.05)
    assert extras["head_pitch_deg"] == pytest.approx(pitch_deg, abs=0.05)
    assert extras["head_pose_from"] == "frames.camera"
    await t.close()


async def test_without_frames_the_head_yaw_joint_says_where_the_camera_points() -> None:
    """A robotd from before API 24 reports no `frames`. The measured head_yaw joint turns the
    camera left when it is positive, which upstream's FK test pins (`up.HEAD_YAW_SIGN`), so it
    gives the yaw. It gives no pitch, and nothing at all comes from a frame that is too old."""
    fake = FakeRobotd(reports_frames=False)
    await fake.start()
    try:
        t = JsonRpcUnixTransport(f"tcp://127.0.0.1:{fake.port}")
        await t.connect()
        yaw = math.radians(30.0)
        assert (await t.send_intent(Intent.look(math.cos(yaw), math.sin(yaw), 0.1))).accepted
        await _until(t, lambda s: s["head"] == fake.head)
        extras = (await t.get_state()).extras
        assert extras["head_yaw_deg"] == pytest.approx(30.0, abs=0.05)
        assert extras["head_pitch_deg"] is None and extras["head_pose_from"] == "joints"
        t._last_state_at = None
        extras = (await t.get_state()).extras
        assert extras["head_yaw_deg"] is None and extras["head_pose_from"] is None
        await t.close()
    finally:
        await fake.stop()


@pytest.mark.parametrize(
    ("api", "fade"),
    [
        (up.IDLE_HEAD_SINCE_API, up.IDLE_FADE_S),
        (CURRENT_API + 1, up.IDLE_FADE_S),
        (up.IDLE_HEAD_SINCE_API - 1, 0.0),
        (FLOOR, 0.0),
    ],
    ids=["first-to-glance", "newer", "older", "floor"],
)
async def test_the_head_fade_is_robotds_on_a_robotd_that_glances(api: int, fade: float) -> None:
    """`go_to` and `search_scan` wait `head_fade_s` after centring the head, so it is robotd's
    FADE on a release that glances and nothing on one that does not, and the adapter, which is
    what those verbs are handed, says the same. A body whose head moves only when asked says 0."""
    fake = FakeRobotd(api_version=api)
    await fake.start()
    try:
        t = JsonRpcUnixTransport(f"tcp://127.0.0.1:{fake.port}")
        assert t.head_fade_s == 0.0
        await t.connect()
        assert t.head_fade_s == fade and MicroduckAdapter(t).head_fade_s == fade
        await t.close()
    finally:
        await fake.stop()
    assert MicroduckAdapter(MockTransport()).head_fade_s == 0.0


async def test_go_to_lets_a_glance_fade_before_it_steers(
    registry: VerbRegistry, robotd: FakeRobotd
) -> None:
    """robotd fades a glance out over FADE once a look arrives, rather than dropping it, so a
    frame taken straight after `go_to` centred the head could come from a head still most of a
    glance off, and `go_to` would turn toward a ball that is not there. Through the adapter, as
    a run hands it to the verb, nothing that moves the duck goes out until FADE after the look:
    this ball is close enough that the first frame ends the walk, so the stop comes then."""
    t = JsonRpcUnixTransport(f"tcp://127.0.0.1:{robotd.port}", camera_url="http://example/x.jpg")
    picture = Image.new("RGB", (8, 8))

    async def fetch() -> Image.Image:
        return picture

    t._fetch_frame = fetch  # type: ignore[method-assign]
    adapter = MicroduckAdapter(t)
    await adapter.connect()
    ex = Executor(registry, adapter, contract=MOVERS.frontmatter, detector=_BallAhead(0.1))
    result = await ex.run_verb("go_to", {"target": "ball", "timeout_s": 30})
    assert result.ok, result.summary
    looked = next(at for at, method in robotd.arrived if method == up.ROBOT_LOOK.name)
    stopped = next(at for at, method in robotd.arrived if method == up.ROBOT_STOP.name)
    assert _moves(robotd) == 0
    # asyncio may wake a timer up to one tick of its clock early
    early = time.get_clock_info("monotonic").resolution
    assert stopped - looked >= up.IDLE_FADE_S - early, stopped - looked
    assert adapter.head_fade_s == up.IDLE_FADE_S
    await adapter.close()


@pytest.mark.parametrize("after", ["the look", "a pause"])
async def test_a_glance_fading_out_is_not_somebody_elses_head(
    robotd: FakeRobotd, after: str
) -> None:
    """robotd fades a glance out rather than dropping it, and while it fades, the head it reports
    can swing away from the held one with the glance riding on it. So nothing is judged on frames
    read before `head_fade_s` and a hold period have passed since the head was last sent its
    target: since the look, and since the first re-send after a pause, which is when robotd
    starts to let go of whatever it glanced at while nothing went out. The fade is this test's,
    and the glance the fake's, three frames swinging away."""
    fade = 40 * HOLD_S
    t = JsonRpcUnixTransport(f"tcp://127.0.0.1:{robotd.port}", gaze_hold_s=HOLD_S)
    await t.connect()
    t.head_fade_s = fade
    assert (await t.send_intent(Intent.look(1.0, 0.5, 0.0))).accepted
    gaze = t._gaze
    assert gaze is not None
    if after == "a pause":
        # the window after the look closes first, then the duck is held and put back down
        looked = gaze.since
        await _eventually(lambda: time.monotonic() > looked + fade + 2 * HOLD_S, "the window")
        _pick_up(robotd, True)
        await _eventually(lambda: gaze.paused is not None, "the hold pausing")
        _pick_up(robotd, False)
        await _eventually(lambda: gaze.paused is None, "the hold taking up again")
    robotd.glance = [0.05, 0.1, 0.15]  # swinging away from the held head, frame by frame
    sent = time.monotonic()  # the head was sent its target a moment ago, by the look or a re-send
    await _eventually(lambda: not robotd.glance, "the glance going by")
    reads = t._frames_read
    await _eventually(
        lambda: t._frames_read >= reads + 3 and time.monotonic() > sent + fade + 5 * HOLD_S,
        "the frames after the glance, and five hold periods past the fade",
    )
    assert t._gaze is gaze and t.gaze_released is None, t.gaze_released
    await t.close()


# ── mediad's /frame, the camera the docs start with ─────────────────────────────────────

#: What mediad's `/frame` answers when it cannot hand a frame out (mediad/src/web.rs:123-124).
UNAVAILABLE = b"Camera snapshot unavailable; retry when capture is running.\n"
#: The words of it that a reader of quackd's error should be shown.
UNAVAILABLE_SAID = UNAVAILABLE.decode().strip()
#: A contract under which the two verbs that need a frame may run.
SEES = parse_duck_text(
    "---\nduck: 0\nname: t\ndescription: d\nverbs:\n  allow: [observe, go_to]\n"
    "success: [x]\n---\n# Task\nx\n"
)


def _png(width: int, height: int) -> bytes:
    picture = io.BytesIO()
    Image.new("RGB", (width, height), (40, 80, 120)).save(picture, "PNG")
    return picture.getvalue()


def _closed_port() -> int:
    """A loopback port nothing listens on, so a connection to it is refused."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


class SnapshotServer:
    """An HTTP camera on loopback that answers each GET with the next reply of its script, and
    with the last one for ever after: 200 and a PNG, or a status and a reason, as mediad's
    `/frame` answers. It counts the requests, so a test can tell a camera waited for from one
    given up on."""

    def __init__(self, *replies: tuple[int, bytes]) -> None:
        self.replies = list(replies)
        self.requests = 0
        self._lock = threading.Lock()
        server = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                status, body = server._next()
                self.send_response(status)
                self.send_header("Content-Type", "image/png" if status == 200 else "text/plain")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, fmt: str, *args: Any) -> None:
                pass

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = int(self.httpd.server_address[1])
        self._thread = threading.Thread(
            target=self.httpd.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True
        )

    def _next(self) -> tuple[int, bytes]:
        with self._lock:
            self.requests += 1
            return self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]

    def url(self, path: str = up.MEDIAD_FRAME_PATH) -> str:
        return f"http://127.0.0.1:{self.port}{path}"

    def __enter__(self) -> SnapshotServer:
        self._thread.start()
        return self

    def __exit__(self, *_: object) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
        self._thread.join(30)


def test_mediads_frame_is_asked_for_at_a_rate_of_its_own() -> None:
    """Every `GET /frame` has the duck encode a PNG of its whole picture, which nobody has
    measured on a robot, so mediad's route is asked at `MEDIAD_FRAME_FPS` and every other camera
    at `CAMERA_FPS`, as before. The rate is what the snapshot loop's period and the age a frame
    may reach both come from. A rate the caller chooses still wins."""
    address = "tcp://127.0.0.1:1"
    route = f"http://127.0.0.1:8080{up.MEDIAD_FRAME_PATH}"
    assert JsonRpcUnixTransport(address, camera_url=route).camera_fps == MEDIAD_FRAME_FPS
    assert MEDIAD_FRAME_FPS < CAMERA_FPS, "mediad's route is asked for less than any camera"
    for other in (
        "http://127.0.0.1:9872/snapshot.jpg",
        f"http://127.0.0.1:8080{up.MEDIAD_FRAME_PATH}/x",
        "webrtc://127.0.0.1:8443",
        None,
    ):
        assert JsonRpcUnixTransport(address, camera_url=other).camera_fps == CAMERA_FPS, other
    chosen = JsonRpcUnixTransport(address, camera_url=route, camera_fps=CAMERA_FPS + 1)
    assert chosen.camera_fps == CAMERA_FPS + 1


async def test_a_503_at_connect_is_waited_through_until_a_frame_comes(robotd: FakeRobotd) -> None:
    """mediad's `/frame` answers 503 while it cannot hand a frame out, and its body says to try
    again. One failure of any kind used to end the wait at connect, and with it the camera for
    the whole run, since whether a run can see is decided there once. quackd asks again now,
    until a frame comes or the wait runs out."""
    with SnapshotServer((503, UNAVAILABLE), (503, UNAVAILABLE), (200, _png(720, 1280))) as camera:
        t = JsonRpcUnixTransport(
            f"tcp://127.0.0.1:{robotd.port}", camera_url=camera.url(), camera_wait_s=60.0
        )
        await t.connect()
        try:
            assert t.camera_working and camera.requests >= 3
            assert t.camera_error is None and t.camera_health()["error"] is None
        finally:
            await t.close()


async def test_a_camera_still_unavailable_when_the_wait_runs_out_is_none_and_says_why(
    robotd: FakeRobotd,
) -> None:
    """A mediad that answers 503 for the whole wait gives the run no camera, and the last thing
    it said is what `camera_health()`, the state and `camera_error` say, mediad's reason
    included, which urllib's own words for a 503 leave out."""
    with SnapshotServer((503, UNAVAILABLE)) as camera:
        t = JsonRpcUnixTransport(
            f"tcp://127.0.0.1:{robotd.port}", camera_url=camera.url(), camera_wait_s=3.0
        )
        await t.connect()
        try:
            assert not t.camera_working
            assert camera.requests > 1, "it asked again before it gave up"
            state = await t.get_state()
            for told in (t.camera_health()["error"], state.extras["camera"]["error"]):
                assert told is not None and "503" in told and UNAVAILABLE_SAID in told, told
            assert t.camera_error == f"GET {camera.url()}: {t.camera_health()['error']}"
        finally:
            await t.close()


@pytest.mark.parametrize("answer", ["404", "refused"])
async def test_a_url_with_nothing_behind_it_is_given_up_on_at_once(
    robotd: FakeRobotd, answer: str
) -> None:
    """A refused connection, or the 404 of a robot older than the route, is not a camera coming
    up, and waiting out the rest of the wait for it would only put off saying so."""
    wait = 60.0
    with SnapshotServer((404, b"Not Found\n")) as camera:
        url = camera.url() if answer == "404" else f"http://127.0.0.1:{_closed_port()}/frame"
        t = JsonRpcUnixTransport(
            f"tcp://127.0.0.1:{robotd.port}", camera_url=url, camera_wait_s=wait
        )
        started = time.monotonic()
        await t.connect()
        took = time.monotonic() - started
        try:
            assert not t.camera_working and took < wait / 4, took
            error = t.camera_health()["error"]
            assert error, "and it says why"
            if answer == "404":
                assert error.startswith("HTTP 404"), error
        finally:
            await t.close()


async def test_a_snapshot_that_runs_out_of_time_is_asked_for_again(robotd: FakeRobotd) -> None:
    """A duck busy encoding a PNG can be slow to answer, and a request that runs out of time
    comes back from urllib in either of two forms, a bare `TimeoutError` while reading and a
    `URLError` around one while connecting. Both are waited through at connect."""
    t = JsonRpcUnixTransport(
        f"tcp://127.0.0.1:{robotd.port}",
        camera_url=f"http://127.0.0.1:1{up.MEDIAD_FRAME_PATH}",
        camera_wait_s=60.0,
    )
    failures: list[Exception] = [
        TimeoutError("timed out"),
        urllib.error.URLError(TimeoutError("timed out")),
    ]
    picture = Image.new("RGB", (8, 8))

    async def fetch() -> Image.Image:
        if failures:
            raise failures.pop(0)
        return picture

    t._fetch_frame = fetch  # type: ignore[method-assign]
    await t.connect()
    try:
        assert t.camera_working and not failures
    finally:
        await t.close()


@pytest.mark.parametrize(
    ("path", "size", "assumed"),
    [
        (up.MEDIAD_FRAME_PATH, (720, 1280), True),
        (up.MEDIAD_FRAME_PATH, (1280, 720), False),
        ("/snapshot.png", (720, 1280), False),
    ],
    ids=["tall-frame", "wide-frame", "another-camera"],
)
async def test_a_tall_first_frame_from_mediad_is_taken_for_an_alphas_lens(
    robotd: FakeRobotd, path: str, size: tuple[int, int], assumed: bool
) -> None:
    """The PNG carries no lens, and `serve-mcp` has no `--fov-deg` to give one, so a body with
    none is measured through the simulator's 90 degrees. On a stock robot only an alpha's camera,
    turned upright for the PNG, gives a picture taller than wide, so a tall first frame from
    mediad's route is taken for the IMX219's 62 degrees running down the picture: the lens goes
    in the manifest, and every state names the assumption. A wide picture, a beta's, and any
    other camera are left without one."""
    with SnapshotServer((200, _png(*size))) as camera:
        t = JsonRpcUnixTransport(
            f"tcp://127.0.0.1:{robotd.port}", camera_url=camera.url(path), camera_wait_s=60.0
        )
        adapter = MicroduckAdapter(t)
        manifest = await adapter.connect()
        try:
            assert t.camera_working
            extras = (await t.get_state()).extras
            if not assumed:
                assert t.camera_fov_deg is None and "camera_fov_deg" not in manifest.limits
                assert extras["assumptions"] == [up.STAND_UP_RPC.name]
                assert extras["camera"]["fov_deg"] is None
                return
            width, height = size
            half = math.radians(up.IMX219_FOV_DEG) / 2
            lens = math.degrees(2 * math.atan(math.tan(half) * width / height))
            assert t.camera_fov_deg == pytest.approx(lens)
            assert manifest.limits["camera_fov_deg"] == pytest.approx(lens)
            assert extras["assumptions"] == [up.STAND_UP_RPC.name, up.ALPHA_UPRIGHT_FOV.name]
            assert extras["camera"]["fov_deg"] == round(lens, 1)
            assert extras["camera"]["fov_assumed"] == up.ALPHA_UPRIGHT_FOV.name
            # what the assumption itself says the lens is, at this very shape
            assert f"{lens:.1f} degrees at {width} by {height}" in up.ALPHA_UPRIGHT_FOV.note
        finally:
            await adapter.disconnect()


async def test_the_lens_is_the_first_frames_and_stays(robotd: FakeRobotd) -> None:
    """The detector reads its lens off the manifest, which is built once, at connect, so the
    lens the transport reports and the assumption every state names stay the first frame's
    whatever shape a later frame is."""
    t = JsonRpcUnixTransport(
        f"tcp://127.0.0.1:{robotd.port}",
        camera_url=f"http://127.0.0.1:1{up.MEDIAD_FRAME_PATH}",
        camera_wait_s=60.0,
    )
    frames = [Image.new("RGB", (720, 1280)), Image.new("RGB", (1280, 720))]

    async def fetch() -> Image.Image:
        return frames.pop(0) if len(frames) > 1 else frames[0]

    t._fetch_frame = fetch  # type: ignore[method-assign]
    await t.connect()
    try:
        lens = t.camera_fov_deg
        assert lens is not None
        await _eventually(
            lambda: t._frame is not None and t._frame.size == (1280, 720), "a wide frame"
        )
        assert t.camera_fov_deg == lens
        assert up.ALPHA_UPRIGHT_FOV.name in (await t.get_state()).extras["assumptions"]
    finally:
        await t.close()


def test_camera_error_says_why_get_frame_has_nothing() -> None:
    """Nothing yet, the newest fetch's failure, or a frame too old to steer on, and None while a
    frame is there to hand out. No `--camera-url` is None too: `observe` says that itself."""
    address = "tcp://127.0.0.1:1"
    assert JsonRpcUnixTransport(address).camera_error is None
    t = JsonRpcUnixTransport(address, camera_url=f"http://127.0.0.1:1{up.MEDIAD_FRAME_PATH}")
    assert t.camera_error == f"no frame has come from {t.camera_url} yet"
    t._frame_error = "HTTP 503 Service Unavailable"
    assert t.camera_error == f"GET {t.camera_url}: HTTP 503 Service Unavailable"
    t._frame, t._frame_at, t._frame_error = Image.new("RGB", (8, 8)), time.monotonic(), None
    assert t.camera_error is None
    window = camera_stale_after_s(t.camera_fps)
    t._frame_at = time.monotonic() - 10 * window
    assert t.camera_error is not None and f"older than the {window:g} s" in t.camera_error


async def test_a_verb_that_finds_no_frame_says_why(
    robotd: FakeRobotd, registry: VerbRegistry
) -> None:
    """`observe`, `go_to` and `search_scan` quote `camera_error` off what they were handed when a
    frame does not come, which on a run is the adapter. `microduck:jsonrpc` had none to give, so
    a verb said only that it got no frame while `camera_health()` knew mediad's 503."""
    with SnapshotServer((200, _png(720, 1280)), (503, UNAVAILABLE)) as camera:
        t = JsonRpcUnixTransport(
            f"tcp://127.0.0.1:{robotd.port}", camera_url=camera.url(), camera_wait_s=60.0
        )
        adapter = MicroduckAdapter(t)
        await adapter.connect()
        try:
            assert t.camera_working, "a frame came, and then the camera stopped answering"
            await _eventually(
                lambda: t.frame_age_s() is None and t._frame_error is not None,
                "the frame going stale behind 503s",
            )
            ex = Executor(registry, adapter, contract=SEES.frontmatter, detector=_BallAhead(1.0))
            for verb, params in (("observe", {}), ("go_to", {"target": "ball", "timeout_s": 20})):
                result = await ex.run_verb(verb, params)
                assert not result.ok, verb
                assert "503" in result.summary and UNAVAILABLE_SAID in result.summary, (
                    verb,
                    result.summary,
                )
            assert adapter.camera_error == t.camera_error is not None
        finally:
            await adapter.disconnect()


def test_doctor_prints_the_cameras_last_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """`doctor` is the checklist's go/no-go gate, and the camera's rows are where somebody sees
    why a `/frame` camera gave nothing: mediad's own 503, the last thing it said after quackd
    waited through it."""
    from quackd import doctor
    from quackd_microduck import describe

    monkeypatch.setattr(jsonrpc_unix, "FIRST_FRAME_WAIT_S", 1.0)
    with SnapshotServer((503, UNAVAILABLE)) as camera, _robotd_on_its_own_loop() as fake:
        address = f"tcp://127.0.0.1:{fake.port}"
        report = doctor.probe("microduck:jsonrpc", describe("jsonrpc"), address, camera.url(), None)
    rows = {row.what: row for row in report.rows}
    assert not report.ok and rows["camera"].value == "no frame"
    error = rows["  error"]
    assert error.state == "fail" and "503" in error.value and UNAVAILABLE_SAID in error.value
    assert camera.requests > 1
