"""The experimental robot transport against a fake robotd over TCP loopback.

The fake answers as a robotd at the pin does (`upstream_api.RELEASE`): its hello and its
subscribe answer, a state frame of that release's shape every tick, and params decoded the way
robotd decodes them, where a member it does not know is refused by name. So this proves our
framing, handshake, deadman feeding and error handling against what upstream's source says,
not that a real duck agrees.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import math
import sys
import time
from collections.abc import Callable
from typing import Any

import pytest
from PIL import Image

from quackd.duckfile.parser import parse_duck_text
from quackd.perception.base import Detection
from quackd.safety import Executor
from quackd.transport.base import HeartbeatError, Intent, TransportError
from quackd.verbs.registry import VerbRegistry
from quackd_microduck import upstream_api as up
from quackd_microduck.transports.jsonrpc_unix import (
    JsonRpcUnixTransport,
    camera_stale_after_s,
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


def camera_pose(yaw: float) -> dict[str, list[float]]:
    """`frames.camera` for a camera turned `yaw` radians to the left of straight ahead.

    The camera frame is OpenCV's (+x right, +y down, +z along the optical axis) and the pose is
    in the trunk frame (x forward, y left, z up), with a scalar-first quaternion. Looking straight
    ahead the optical axis is the trunk's +x, the camera's +x is the trunk's -y and its +y the
    trunk's -z, which is the quaternion [0.5, -0.5, 0.5, -0.5]; a turn left is that, rotated by
    `yaw` about the trunk's +z."""
    c, s = math.cos(yaw / 2), math.sin(yaw / 2)
    w, x, y, z = 0.5, -0.5, 0.5, -0.5
    return {
        "pos": [0.05, 0.0, 0.1],
        "quat": [c * w - s * z, c * x - s * y, c * y + s * x, c * z + s * w],
    }


class FakeRobotd:
    def __init__(
        self,
        *,
        api_version: int = CURRENT_API,
        healthy: bool = True,
        fallen: bool = False,
        policy: str | None = "walk",
        limp: bool = False,
        picked_up: bool | None = None,
        skills: list[str] | None = STOCK_SKILLS,
        missing: frozenset[str] = frozenset(),
    ) -> None:
        self.api_version = api_version
        self.healthy = healthy
        # The duck as the next state frame will describe it. Each frame is built from these when
        # it is sent, so a test can pick the duck up, lay it down or start a skill mid-verb.
        self.fallen = fallen
        self.policy = policy
        self.limp = limp
        self.picked_up = picked_up
        """None leaves `safety.picked_up` out, as a robotd from before the pick-up detector does."""
        self.skills = skills
        """What `robot.subscribe` answers in `skills`; None leaves the key out, as a robotd whose
        skills are not config yet does."""
        self.missing = missing
        """Calls this robotd answers METHOD_NOT_FOUND, as one from before they existed would."""
        self.head = [0.0, 0.0, 0.0, 0.0]
        """The commanded head, `[neck_pitch, head_pitch, head_yaw, head_roll]`: what the last
        `robot.look` or `robot.head` asked for. A real robotd adds its idle glances on top."""
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
        joints = [0.0] * len(JOINTS)
        for name, angle in zip(HEAD, self.head, strict=True):
            joints[JOINTS.index(name)] = angle
        t = time.monotonic() - self._t0
        return {
            "jsonrpc": "2.0",
            "method": "robot.state",
            "params": {
                "t": t,
                "move": {"requested": [0.0, 0.0, 0.0], "applied": [0.0, 0.0, 0.0]},
                "head": list(self.head),
                "policy": self.policy,
                "safety": safety,
                "loop": {"hz": 50.0, "missed": 0},
                "joints": joints,
                "targets": joints,
                "odom": {"position": [0.0, 0.0, 0.0], "yaw": 0.0},
                "t_ns": int(time.monotonic_ns()),
                "frames": {
                    "camera": camera_pose(self.head[2]),
                    "tof": {"pos": [0.05, 0.0, 0.1], "quat": [1.0, 0.0, 0.0, 0.0]},
                },
            },
        }

    async def _publish(self, writer: asyncio.StreamWriter, hz: int) -> None:
        """Stream `robot.state` the way a real robotd does, until the connection goes away."""
        while not writer.is_closing():
            try:
                writer.write((json.dumps(self.frame()) + "\n").encode())
                await writer.drain()
            except (ConnectionError, OSError):
                return
            await asyncio.sleep(1.0 / max(1, hz))

    def refusal(self, method: str, params: Any) -> tuple[int, str] | None:
        """The error robotd answers this call with, or None when it would take it.

        In robotd's order (`Call::parse`): a method it does not have is METHOD_NOT_FOUND, then
        params are decoded, and a member the params type does not declare, one it requires and
        did not get, or a sound tag that is not one of the seven, is INVALID_PARAMS naming it, in
        serde's words."""
        if method in self.missing or method not in PARAMS.keys() | NO_PARAMS:
            return up.ERR_METHOD_NOT_FOUND, f"unknown method {json.dumps(method)}"
        if method in NO_PARAMS:
            return None
        known, required = PARAMS[method]
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
            if "id" not in msg:
                self.notifications.append(msg)
                if (refused := self.refusal(method, params)) is not None:
                    # robotd drops a notification it cannot parse and says nothing back
                    self.rejected.append((method, refused[1]))
                elif method == "robot.head":
                    self.head = [float(params.get(k, 0.0)) for k in HEAD]
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
                    "api_version": self.api_version,
                    "daemon_version": up.RELEASE.removeprefix("daemon-v"),
                    "revision": up.PIN[:7],
                }
            elif method == "robot.health":
                result = {"healthy": self.healthy, "battery": {"volts": 7.4, "percent": 66.0}}
                if not self.healthy:
                    result["reason"] = "control loop at 43.9 Hz"
            elif method in ("robot.stop", "robot.enable"):
                result = {"accepted": True}
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
                yaw = math.atan2(float(params.get("y", 0.0)), float(params.get("x", 1.0)))
                self.head = [float(params.get("neck_pitch", 0.0)), 0.0, yaw, 0.0]
                result = {
                    "head": dict(zip(HEAD, self.head, strict=True)),
                    "clamped": False,
                }
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
                if method == "robot.head":
                    self.head = [float(params.get(k, 0.0)) for k in HEAD]
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


async def test_api_version_mismatch_refuses() -> None:
    fake = FakeRobotd(api_version=CURRENT_API + 1)
    await fake.start()
    try:
        t = JsonRpcUnixTransport(f"tcp://127.0.0.1:{fake.port}")
        with pytest.raises(TransportError, match=f"API v{CURRENT_API + 1}"):
            await t.connect()
    finally:
        await fake.stop()


async def test_the_version_quackd_was_written_against_is_the_one_upstream_ships() -> None:
    """A robotd on an old contract is refused, not guessed at.

    `API_VERSION` was 16 for a week after upstream had moved to 23, and nothing here noticed
    because the fake was pinned to the same stale number. It was then 23 while every robotd
    release from daemon-v0.12.0 on said 27 or more, and quackd refused each of them. Naming the
    number means a bump has to come past this test rather than sliding through with the fake,
    and naming 16 keeps the oldest contract quackd ever spoke refused.
    """
    # Named, not `>= 41`: a bump has to come past this line and past a fresh read of upstream,
    # rather than sailing through because the number only went up.
    assert CURRENT_API == 41, "re-read duck-ipc-proto at a new pin before changing this"
    fake = FakeRobotd(api_version=16)
    await fake.start()
    try:
        t = JsonRpcUnixTransport(f"tcp://127.0.0.1:{fake.port}")
        with pytest.raises(TransportError, match="API v16"):
            await t.connect()
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
    does at the pin, and every verb that reaches the socket, a pose and a stop go through it
    with nothing refused."""
    robotd.fallen, robotd.recover_on_enable = True, True  # so that stand_up has to ask
    t = JsonRpcUnixTransport(f"tcp://127.0.0.1:{robotd.port}")
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
    assert (await t.send_intent(Intent(kind="pose", params={"z": 0.0, "pitch": 0.1}))).accepted
    await t.stop()
    await _landed(t)
    assert robotd.rejected == []
    sent = {r["method"] for r in robotd.requests} | {n["method"] for n in robotd.notifications}
    wire = (up.HELLO, up.ROBOT_SUBSCRIBE, up.ROBOT_ENABLE, up.ROBOT_MOVE, up.ROBOT_DO)
    wire += (up.ROBOT_SOUND, up.ROBOT_LOOK, up.ROBOT_POSE, up.ROBOT_STOP, up.ROBOT_HEALTH)
    assert {ref.name for ref in wire} <= sent
    await t.close()


async def test_a_call_the_robot_does_not_have_fails_the_verb_that_needs_it(
    registry: VerbRegistry,
) -> None:
    """A robotd from before a call existed answers METHOD_NOT_FOUND naming it, the other way a
    robot from another release says no. The verb that needed the call fails with the code and
    the name, instead of reporting what the duck did not do."""
    fake = FakeRobotd(missing=frozenset({up.ROBOT_LOOK.name}))
    await fake.start()
    try:
        t = JsonRpcUnixTransport(f"tcp://127.0.0.1:{fake.port}")
        await t.connect()
        ex = Executor(registry, t, contract=DUCK.frontmatter)
        result = await ex.run_verb("gaze", {"direction": "left"})
        assert not result.ok and str(up.ERR_METHOD_NOT_FOUND) in result.summary
        assert up.ROBOT_LOOK.name in result.summary
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
        # robot.look is a request, so until the pick-up every notification is a robot.move
        await robotd.wait_notifications(3, wait_s=5.0)
        assert _moves(robotd) == len(robotd.notifications)

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
