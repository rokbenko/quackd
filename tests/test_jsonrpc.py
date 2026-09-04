"""The experimental robot transport against a fake robotd over TCP loopback.

The fake speaks exactly the VERIFIED subset of duck-ipc-proto we rely on, so this proves
our framing, handshake, deadman feeding, and error handling — not that a real duck agrees.
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from typing import Any

import pytest
from PIL import Image

from quackd.duckfile.parser import parse_duck_text
from quackd.safety import Executor
from quackd.transport import upstream_api as up
from quackd.transport.base import HeartbeatError, Intent, TransportError
from quackd.transport.jsonrpc_unix import (
    STATE_STALE_AFTER_S,
    JsonRpcUnixTransport,
    parse_address,
)
from quackd.verbs.registry import VerbRegistry

DUCK = parse_duck_text(
    "---\nduck: 0\nname: t\ndescription: d\nverbs:\n  allow: [walk, kick, quack, gaze]\n"
    "success: [x]\n---\n# Task\nx\n"
)


CURRENT_API = int(up.API_VERSION.name)


class FakeRobotd:
    def __init__(
        self,
        *,
        api_version: int = CURRENT_API,
        healthy: bool = True,
        fallen: bool = False,
        policy: str = "walk",
    ) -> None:
        self.api_version = api_version
        self.healthy = healthy
        self.fallen = fallen
        self.policy = policy
        self.notifications: list[dict[str, Any]] = []
        self.requests: list[dict[str, Any]] = []
        self.server: asyncio.AbstractServer | None = None
        self.port = 0
        self._arrived = asyncio.Event()

    async def start(self) -> None:
        self.server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        self.port = self.server.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        assert self.server is not None
        self.server.close()
        await self.server.wait_closed()

    async def wait_notifications(self, n: int, wait_s: float = 2.0) -> list[dict[str, Any]]:
        """Notifications have no reply, so the test must wait for the server to read them."""
        async with asyncio.timeout(wait_s):
            while len(self.notifications) < n:
                self._arrived.clear()
                await self._arrived.wait()
        return self.notifications

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        while line := await reader.readline():
            msg = json.loads(line)
            if "id" not in msg:
                self.notifications.append(msg)
                self._arrived.set()
                continue
            self.requests.append(msg)
            method, params = msg["method"], msg.get("params") or {}
            result: Any
            if method == "hello":
                result = {"api_version": self.api_version, "daemon_version": "0.9.9"}
            elif method == "robot.health":
                result = {"healthy": self.healthy, "battery": {"volts": 7.4, "percent": 66.0}}
                if not self.healthy:
                    result["reason"] = "control loop at 43.9 Hz"
            elif method in ("robot.stop", "robot.enable"):
                result = {"accepted": True}
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
                result = {
                    "head": {"neck_pitch": 0, "head_pitch": 0, "head_yaw": 0.3, "head_roll": 0},
                    "clamped": False,
                }
            elif method == "robot.sound":
                result = {"accepted": params.get("tag") in up.SOUND_TAG_LIST}
            elif method == "robot.subscribe":
                result = {"accepted": True, "walk": "alpha_walking.onnx"}
                writer.write(
                    (
                        json.dumps(
                            {
                                "jsonrpc": "2.0",
                                "method": "robot.state",
                                "params": {
                                    "t": 1.0,
                                    "policy": self.policy,
                                    "safety": {"fallen": self.fallen, "limp": False},
                                },
                            }
                        )
                        + "\n"
                    ).encode()
                )
            elif method == "robot.neverAnswered":
                # A robotd that accepts a call and never replies, which is what a wedged
                # daemon looks like from here. Also feeds a junk line, to prove the pump
                # skips what it cannot parse instead of dying on it.
                writer.write(b"{not json at all\n")
                await writer.drain()
                continue
            else:
                writer.write(
                    (
                        json.dumps(
                            {
                                "jsonrpc": "2.0",
                                "id": msg["id"],
                                "error": {"code": -32601, "message": f"unknown method {method}"},
                            }
                        )
                        + "\n"
                    ).encode()
                )
                await writer.drain()
                continue
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


async def test_handshake_and_intents(robotd: FakeRobotd) -> None:
    t = JsonRpcUnixTransport(f"tcp://127.0.0.1:{robotd.port}")
    await t.connect()
    assert t.hello == {"api_version": CURRENT_API, "daemon_version": "0.9.9"}
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
    """A robotd on the old contract is refused, not guessed at.

    `API_VERSION` was 16 for a week after upstream had moved to 23, and nothing here noticed
    because the fake was pinned to the same stale number. Naming 16 explicitly means a future
    bump has to come past this test rather than sliding through with the fake.
    """
    assert CURRENT_API >= 23
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
    assert t.subscribed == {"accepted": True, "walk": "alpha_walking.onnx"}

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


async def test_stale_state_stops_being_believed(robotd: FakeRobotd) -> None:
    t = JsonRpcUnixTransport(f"tcp://127.0.0.1:{robotd.port}")
    await t.connect()
    assert (await t.get_state()).posture == "standing"
    t._last_state_at = time.monotonic() - 10 * STATE_STALE_AFTER_S
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
    for _ in range(200):  # the capture loop records the reason on its own clock
        if t._frame_error is not None:
            break
        await asyncio.sleep(0.01)
    health = t.camera_health()
    assert health["configured"] is True and health["ok"] is False and health["error"]
    await t.close()


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

    docs/adapter-status.md promises neither is ever sent. Nothing asserted it, and both names
    are one typo away from `robot.enable` in the same match statement.
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
    """The `"sit" in policy` branch had never been exercised over the wire."""
    fake = FakeRobotd(policy="sit_rise")
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


async def test_no_camera_without_url(robotd: FakeRobotd) -> None:
    t = JsonRpcUnixTransport(f"tcp://127.0.0.1:{robotd.port}")
    await t.connect()
    assert await t.get_frame() is None
    await t.close()
