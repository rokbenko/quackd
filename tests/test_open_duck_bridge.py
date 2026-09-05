"""The bridge backend against a fake bridge daemon over TCP loopback.

The fake speaks exactly the protocol quackd defines for this robot, so this proves our
framing, the handshake and its version gate, the capability narrowing that decides which
verbs the robot has, the deadman-feeding command notifications, and the heartbeat that
refuses a starved control loop. It proves nothing about a real duck.
"""

from __future__ import annotations

import asyncio
import json
import math
from typing import Any

import pytest

from quackd.adapters.open_duck import OpenDuckAdapter
from quackd.adapters.open_duck.bridge import (
    COMMAND,
    MIN_LOOP_HZ,
    PROTOCOL,
    PROTOCOL_VERSION,
    TOKEN_ENV,
    OpenDuckBridge,
    parse_address,
)
from quackd.adapters.open_duck.verbs import HEAD_YAW_RANGE
from quackd.duckfile.parser import parse_duck_text
from quackd.perception.color_blob import ColorBlobDetector
from quackd.safety import Executor, allow_all
from quackd.transport.base import HeartbeatError, Intent, TransportError
from quackd.verbs.registry import registry_from_manifest

DUCK = parse_duck_text(
    "---\nduck: 1\nname: t\ndescription: d\nrequires: [move]\nverbs:\n"
    "  allow: [move, gaze, say, quack, express, report_state, stop]\n"
    "success: [x]\n---\n# Task\nx\n"
)
FULL_DUCK = {"camera": True, "speaker": True, "antennas": True, "head": True}


class FakeBridge:
    """A duck's Pi, as far as the wire can tell."""

    def __init__(
        self,
        *,
        protocol_version: int = PROTOCOL_VERSION,
        capabilities: dict[str, bool] | None = None,
        healthy: bool = True,
        loop_hz: float = 49.8,
        fallen: bool | None = False,
        token: str | None = None,
    ) -> None:
        self.protocol_version = protocol_version
        self.capabilities = FULL_DUCK if capabilities is None else capabilities
        self.healthy = healthy
        self.loop_hz = loop_hz
        self.ticks = 0
        self.safety: dict[str, Any] = {}
        self.silent = False
        self.fallen = fallen
        self.token = token
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

    @property
    def address(self) -> str:
        return f"tcp://127.0.0.1:{self.port}"

    async def wait_notifications(self, n: int, wait_s: float = 2.0) -> list[dict[str, Any]]:
        async with asyncio.timeout(wait_s):
            while len(self.notifications) < n:
                self._arrived.clear()
                await self._arrived.wait()
        return self.notifications

    def commands(self) -> list[dict[str, Any]]:
        return [m.get("params") or {} for m in self.notifications if m.get("method") == COMMAND]

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        while line := await reader.readline():
            msg = json.loads(line)
            if "id" not in msg:
                self.notifications.append(msg)
                self._arrived.set()
                continue
            self.requests.append(msg)
            method, params = msg["method"], msg.get("params") or {}
            if self.silent:  # the socket is up and nothing comes back
                continue
            result: Any
            if method == "duck.hello":
                if self.token is not None and params.get("token") != self.token:
                    error = {"code": 2, "message": "bad or missing token; see the token file"}
                    payload = {"jsonrpc": "2.0", "id": msg["id"], "error": error}
                    writer.write((json.dumps(payload) + "\n").encode())
                    await writer.drain()
                    continue
                result = {
                    "protocol": PROTOCOL,
                    "protocol_version": self.protocol_version,
                    "bridge_version": "0.1.0",
                    "runtime": {"commit": "3203734", "dirty": False},
                    "capabilities": self.capabilities,
                    "camera": {"url": "http://127.0.0.1:9872/snapshot.jpg"},
                    "safety": self.safety,
                }
            elif method == "duck.health":
                # `ticks` advances on every beat, because a real loop that is merely slow is
                # still ticking. The client needs two beats before it will judge a rate at
                # all (during ONNX load and servo init there is no rate yet), and a count
                # that does not move is how a wedged loop is told from a slow one.
                self.ticks += 7
                result = {"healthy": self.healthy, "loop_hz": self.loop_hz, "ticks": self.ticks}
                if not self.healthy:
                    result["reason"] = "the serial bus stopped answering"
            elif method == "duck.state":
                result = {
                    "policy_running": True,
                    "fallen": self.fallen,
                    "loop_hz": self.loop_hz,
                    "command_age_ms": 40,
                    "pose": {"x": 0.1, "y": 0.2, "theta": 0.3},
                }
            elif method == "duck.stop":
                result = {"stopped": True, "limp": False}
            elif method == "duck.sound":
                result = {"accepted": True, "played": params.get("mood")}
            elif method == "duck.antennas":
                result = {"accepted": True, "gesture": params.get("gesture")}
            else:
                error = {"code": -32601, "message": f"unknown method {method}"}
                payload = {"jsonrpc": "2.0", "id": msg["id"], "error": error}
                writer.write((json.dumps(payload) + "\n").encode())
                await writer.drain()
                continue
            writer.write(
                (json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": result}) + "\n").encode()
            )
            await writer.drain()
        writer.close()


@pytest.fixture
async def bridge():
    fake = FakeBridge()
    await fake.start()
    yield fake
    await fake.stop()


# ── the wire ────────────────────────────────────────────────────────────────────────────


async def test_handshake_reports_what_this_duck_was_built_with(bridge: FakeBridge) -> None:
    t = OpenDuckBridge(bridge.address)
    await t.connect()
    assert t.features == FULL_DUCK
    assert t.bridge_version == "0.1.0" and t.runtime_commit == "3203734"
    assert t.camera_url == "http://127.0.0.1:9872/snapshot.jpg"
    hello = bridge.requests[0]
    assert hello["method"] == "duck.hello"
    assert hello["params"] == {"protocol": PROTOCOL, "protocol_version": PROTOCOL_VERSION}
    await t.close()


async def test_a_protocol_mismatch_refuses_rather_than_guessing() -> None:
    fake = FakeBridge(protocol_version=PROTOCOL_VERSION + 1)
    await fake.start()
    t = OpenDuckBridge(fake.address)
    with pytest.raises(TransportError, match="refusing rather than guessing"):
        await t.connect()
    await fake.stop()


async def test_a_missing_bridge_says_so_in_words_an_owner_can_act_on() -> None:
    t = OpenDuckBridge("tcp://127.0.0.1:1")
    with pytest.raises(TransportError, match="quackd-duck-bridge running"):
        await t.connect()


def test_parse_address() -> None:
    assert parse_address("tcp://duck.local:9871") == ("duck.local", 9871)
    assert parse_address("duck.local:9871") == ("duck.local", 9871)
    assert parse_address("duck.local") == ("duck.local", 9871)
    with pytest.raises(TransportError):
        parse_address("http://duck.local:9871")


# ── the intents ─────────────────────────────────────────────────────────────────────────


async def test_walking_feeds_the_deadman_and_then_stops(bridge: FakeBridge) -> None:
    """`move` re-sends at 10 Hz, which is what keeps the daemon's deadman from tripping."""
    adapter = OpenDuckAdapter(OpenDuckBridge(bridge.address))
    manifest = await adapter.connect()
    ex = Executor(
        registry_from_manifest(manifest, adapter),
        adapter,
        contract=DUCK.frontmatter,
        detector=ColorBlobDetector(),
        confirm=allow_all,
    )
    assert (await ex.run_verb("move", {"vx": 0.1, "duration_s": 0.3})).ok
    commands = [c for c in (await bridge.wait_notifications(3)) if c.get("method") == COMMAND]
    assert len(commands) >= 3
    assert (commands[0].get("params") or {})["vx"] == pytest.approx(0.1)
    assert any(r["method"] == "duck.stop" for r in bridge.requests)
    await adapter.disconnect()


async def test_a_gaze_becomes_head_angles_and_is_clamped_to_this_neck(bridge: FakeBridge) -> None:
    t = OpenDuckBridge(bridge.address)
    await t.connect()
    ack = await t.send_intent(Intent.look(1.0, 0.0, 0.0))
    assert ack.accepted and ack.reason is None
    await bridge.wait_notifications(1)  # a notification has no reply to wait on
    head = (bridge.commands()[-1])["head"]
    assert set(head) == {"head_yaw", "head_pitch"}  # unset joints are left where they are
    assert head["head_yaw"] == pytest.approx(0.0)

    # a yaw past the neck's travel is clamped, and the ack says so
    far = math.radians(80)
    ack = await t.send_intent(Intent.look(math.cos(far), math.sin(far), 0.0))
    assert ack.accepted and "clamped" in (ack.reason or "")
    await bridge.wait_notifications(2)
    assert (bridge.commands()[-1])["head"]["head_yaw"] == pytest.approx(HEAD_YAW_RANGE[1])
    await t.close()


async def test_sounds_and_antennas_go_out_as_this_robot_s_own_vocabulary(
    bridge: FakeBridge,
) -> None:
    t = OpenDuckBridge(bridge.address)
    await t.connect()
    assert (await t.send_intent(Intent.sound("greet", "hello"))).accepted
    assert bridge.requests[-1]["params"] == {"mood": "greet"}
    assert (await t.send_intent(Intent.sound("moonwalk", None))).accepted
    assert bridge.requests[-1]["params"] == {"mood": "chirp"}  # unknown moods degrade
    assert (await t.send_intent(Intent.do("antennas:perk"))).accepted
    assert bridge.requests[-1]["params"] == {"gesture": "perk"}
    await t.close()


async def test_the_bridge_has_no_word_for_going_limp(bridge: FakeBridge) -> None:
    """`stop` is a zero twist. Nothing in the protocol can reach torque, by construction."""
    t = OpenDuckBridge(bridge.address)
    await t.connect()
    assert not (await t.send_intent(Intent.enable(False))).accepted
    for skill in ("kick_left", "ground_pick", "sit_toggle"):
        ack = await t.send_intent(Intent.do(skill))
        assert not ack.accepted and "has no skill" in (ack.reason or "")
    await t.stop()
    assert bridge.requests[-1]["method"] == "duck.stop"
    assert not [r for r in bridge.requests if "relax" in r["method"] or "torque" in r["method"]]
    await t.close()


# ── health and state ────────────────────────────────────────────────────────────────────


async def test_a_starved_control_loop_fails_the_heartbeat() -> None:
    fake = FakeBridge(loop_hz=31.2)
    await fake.start()
    t = OpenDuckBridge(fake.address)
    await t.connect()
    with pytest.raises(HeartbeatError, match=r"31\.2"):
        await t.heartbeat()
    assert MIN_LOOP_HZ > 31.2
    await t.close()
    await fake.stop()


async def test_an_unhealthy_bridge_fails_the_heartbeat() -> None:
    fake = FakeBridge(healthy=False)
    await fake.start()
    t = OpenDuckBridge(fake.address)
    await t.connect()
    with pytest.raises(HeartbeatError, match="serial bus"):
        await t.heartbeat()
    await t.close()
    await fake.stop()


async def test_state_carries_no_battery_and_names_its_assumptions(bridge: FakeBridge) -> None:
    t = OpenDuckBridge(bridge.address)
    await t.connect()
    state = await t.get_state()
    assert state.battery_percent is None  # nothing in the runtime reports one
    assert state.posture == "standing" and not state.fallen
    assert state.extras["loop_hz"] == pytest.approx(49.8)
    assert "FALL_SIGNAL" in state.extras["assumptions"]
    await t.close()


async def test_a_fallen_duck_is_reported_and_nothing_tries_to_stand_it_up() -> None:
    fake = FakeBridge(fallen=True)
    await fake.start()
    adapter = OpenDuckAdapter(OpenDuckBridge(fake.address))
    manifest = await adapter.connect()
    assert not manifest.provides("stand_up")
    ex = Executor(
        registry_from_manifest(manifest, adapter),
        adapter,
        contract=DUCK.frontmatter,
        detector=ColorBlobDetector(),
        confirm=allow_all,
    )
    result = await ex.run_verb("move", {"vx": 0.1, "duration_s": 0.2})
    assert not result.ok and "by hand" in result.summary
    assert not [r for r in fake.requests if r["method"] == COMMAND]
    await adapter.disconnect()
    await fake.stop()


# ── the manifest the handshake produces ─────────────────────────────────────────────────


async def test_a_duck_with_no_camera_or_speaker_loses_exactly_those_verbs() -> None:
    fake = FakeBridge(
        capabilities={"camera": False, "speaker": False, "antennas": False, "head": False}
    )
    await fake.start()
    adapter = OpenDuckAdapter(OpenDuckBridge(fake.address))
    manifest = await adapter.connect()
    assert set(manifest.verb_names()) == {"report_state", "stop", "move"}
    assert manifest.backend == "bridge"
    assert manifest.extras["expression_features"] == {
        "camera": False,
        "speaker": False,
        "antennas": False,
        "microphone": False,
    }
    await adapter.disconnect()
    await fake.stop()


async def test_head_control_is_off_unless_the_bridge_says_it_is_on() -> None:
    fake = FakeBridge(capabilities={**FULL_DUCK, "head": False})
    await fake.start()
    adapter = OpenDuckAdapter(OpenDuckBridge(fake.address))
    manifest = await adapter.connect()
    assert not manifest.provides("gaze") and "gaze" not in manifest.intents
    assert manifest.provides("search_scan")  # it still scans, by turning the body
    await adapter.disconnect()
    await fake.stop()


async def test_a_bridge_that_cannot_see_falls_reports_unknown_not_standing() -> None:
    """A duck nobody is watching must never read as upright. Nothing upstream reports a
    fall, so a bridge with no IMU tap sends null and quackd says it does not know."""
    fake = FakeBridge(fallen=None)
    await fake.start()
    t = OpenDuckBridge(fake.address)
    await t.connect()
    state = await t.get_state()
    assert state.posture == "unknown" and state.fallen is False
    assert state.extras["fall_detection"] is False
    await t.close()
    await fake.stop()


# ── the token the installer writes ──────────────────────────────────────────────────────


async def test_the_token_travels_in_the_handshake_not_the_address() -> None:
    fake = FakeBridge(token="s3cret")
    await fake.start()
    t = OpenDuckBridge(fake.address, token="s3cret")
    await t.connect()
    assert fake.requests[0]["params"]["token"] == "s3cret"
    assert "s3cret" not in t.address, "an address is printed and lands in transcripts"
    await t.close()
    await fake.stop()


async def test_a_missing_token_says_which_flag_to_use() -> None:
    """The bridge's own installer writes a token, so this is what a by-the-book duck does."""
    fake = FakeBridge(token="s3cret")
    await fake.start()
    t = OpenDuckBridge(fake.address)
    with pytest.raises(TransportError, match="--token"):
        await t.connect()
    assert TOKEN_ENV in str(await _caught(fake.address))
    await fake.stop()


async def _caught(address: str) -> Exception:
    try:
        await OpenDuckBridge(address).connect()
    except TransportError as e:
        return e
    raise AssertionError("expected a refusal")


async def test_the_token_can_come_from_the_environment(monkeypatch) -> None:
    fake = FakeBridge(token="from-the-env")
    await fake.start()
    monkeypatch.setenv(TOKEN_ENV, "from-the-env")
    t = OpenDuckBridge(fake.address)
    await t.connect()
    assert fake.requests[0]["params"]["token"] == "from-the-env"
    await t.close()
    await fake.stop()


# ── an unreadable state, and a fall nobody is watching for ──────────────────────────────


async def test_a_state_that_could_not_be_read_is_not_a_healthy_duck() -> None:
    """The client used to suppress the error and return a default frame, so `fallen` was
    False, `policy_running` was None, both preconditions passed, and quackd would start a
    `move` into a link that was not there. `report_state` printed a pose of exactly
    (0, 0, 0) for a robot that has no odometry at all."""
    fake = FakeBridge()
    await fake.start()
    t = OpenDuckBridge(fake.address, request_timeout_s=0.2)
    await t.connect()
    fake.silent = True  # the socket is still up; nothing answers on it

    state = await t.get_state()
    assert state.extras["state_stale"], "a failed read is silence, not a verdict"
    assert state.posture == "unknown"
    assert "UNREADABLE" in state.summary()
    assert state.x is None and state.y is None and state.theta is None

    from quackd.adapters.open_duck.verbs import open_duck_conditions

    for name, check in open_duck_conditions().items():
        assert check(state) is not None, f"{name} must refuse on a state it could not read"
    await t.close()
    await fake.stop()


async def test_a_fall_blind_duck_says_so_in_every_observation() -> None:
    """`posture=unknown` on its own reads as "no news". It is not: nothing is watching, so
    `fallen=False` is silence, and a pilot told that moving verbs refuse when it is down
    would read an accepted move as proof that it is upright."""
    fake = FakeBridge(fallen=None)  # the bridge cannot see falls at all
    await fake.start()
    t = OpenDuckBridge(fake.address)
    await t.connect()
    state = await t.get_state()
    assert state.extras["fall_detection"] is False
    assert "fall-blind" in state.summary()
    assert state.posture == "unknown"
    await t.close()
    await fake.stop()


async def test_a_bridge_with_no_token_says_so_when_one_was_sent() -> None:
    """An unreadable token file left authentication off while the client happily sent one,
    and nothing anywhere could tell the difference."""
    fake = FakeBridge()
    fake.safety = {"auth": "none", "deadman_ms": 300}
    await fake.start()
    t = OpenDuckBridge(fake.address, token="s3cret")
    await t.connect()
    assert t.safety["auth"] == "none"
    assert t.auth_warning and "was not checked" in t.auth_warning
    await t.close()
    await fake.stop()
