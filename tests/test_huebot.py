"""Huebot's adapter against a fake of the shim its maker is asked to build.

Huebot's own API is closed, and its maker runs a shim on the robot that answers the JSON-RPC the
Microduck's robotd speaks. `FakeShim` answers what docs/adapters/huebot/README.md asks the shim
to answer, as robotd does at the pin quackd read it at: `hello`, `robot.subscribe` and its stream
of `robot.state`, `robot.move` as notifications with robotd's deadman, `robot.stop`,
`robot.health`, `robot.do` for the skills it lists, `robot.sound` and `robot.enable`. Every other
call is `METHOD_NOT_FOUND`, `robot.look` and `robot.head` among them, since the shim has neither
yet. So this proves quackd's half of that contract, and nothing about a real Huebot, which
nobody has run.
"""

from __future__ import annotations

import asyncio
import contextlib
import itertools
import json
import math
import sys
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from PIL import Image
from typer.testing import CliRunner

from quackd.adapters.factory import describe as describe_spec
from quackd.adapters.factory import make_adapter, parse_robot_spec
from quackd.cli import app
from quackd.duckfile.parser import parse_duck_text
from quackd.perception.base import Detection
from quackd.safety import Executor
from quackd.transport.base import DuckState, Intent, TransportError, twist_shown_since_of
from quackd.transport.mock import MockTransport
from quackd.verbs.registry import registry_from_manifest
from quackd_huebot import (
    HUEBOT_VERBS,
    HuebotAdapter,
    describe,
    huebot_manifest,
    make,
    offered_skill_verbs,
)
from quackd_huebot import upstream_api as up
from quackd_huebot.transports import jsonrpc_unix
from quackd_huebot.transports.jsonrpc_unix import (
    HELD_FOR_A_VERB,
    LOOK_REFUSED,
    NO_ADDRESS,
    JsonRpcUnixTransport,
)
from quackd_huebot.verbs import SKILLS_OF, STAND_UP_POLL_S, STAND_UP_WAIT_S

#: Every verb, for the tests that run them through an executor and its allowlist.
EVERYTHING = parse_duck_text(
    "---\nduck: 0\nname: t\ndescription: d\nverbs:\n"
    "  allow: [walk, go_to, search_scan, kick, grab, sit, stand, stand_up, quack, say, observe]\n"
    "success: [x]\n---\n# Task\nx\n"
)
#: Every skill a verb sends, which a shim that has them all lists.
ALL_SKILLS = sorted({skill for skills in SKILLS_OF.values() for skill in skills})
CURRENT_API = int(up.API_VERSION.name)
FLOOR = int(up.API_FLOOR.name)
NO_VERSION = object()
"""A hello answer with no `api_version` at all, for `FakeShim(api_version=...)`."""

#: Each call the shim answers that takes params, with every member robotd's params type declares
#: and the ones it requires. A member it does not know is INVALID_PARAMS naming it.
PARAMS: dict[str, tuple[frozenset[str], frozenset[str]]] = {
    "hello": (frozenset({"api_version"}), frozenset({"api_version"})),
    "robot.move": (frozenset({"vx", "vy", "vyaw"}), frozenset()),
    "robot.enable": (frozenset({"on", "toggle"}), frozenset({"on"})),
    "robot.do": (frozenset({"skill"}), frozenset({"skill"})),
    "robot.sound": (frozenset({"tag", "hold"}), frozenset({"tag"})),
    "robot.subscribe": (frozenset({"hz"}), frozenset()),
}
#: The calls it answers that take no params.
NO_PARAMS = frozenset({"robot.stop", "robot.health"})


class FakeShim:
    """Huebot's shim as its maker is asked to build it, over TCP loopback."""

    def __init__(
        self,
        *,
        api_version: object = CURRENT_API,
        healthy: bool = True,
        fallen: bool = False,
        policy: str | None = "walk",
        limp: bool = False,
        picked_up: bool | None = False,
        skills: list[str] | None = None,
        lists_skills: bool = True,
        missing: frozenset[str] = frozenset(),
        drop_moves: bool = False,
    ) -> None:
        self.api_version = api_version
        self.healthy = healthy
        # The robot as the next state frame describes it, built from these as each frame is
        # sent, so a test can pick it up, lay it down or stand it up mid-verb.
        self.fallen = fallen
        self.policy = policy
        self.limp = limp
        self.picked_up = picked_up
        """None leaves `safety.picked_up` out of every frame."""
        self.skills = list(ALL_SKILLS if skills is None else skills)
        self.lists_skills = lists_skills
        """False leaves `skills` out of the subscribe answer altogether."""
        self.missing = missing
        """Calls this shim answers METHOD_NOT_FOUND although it would otherwise take them."""
        self.drop_moves = drop_moves
        """Take no `robot.move`: nothing is said back, and `move.requested` goes on reporting
        what it was asked for before, as a shim that cannot parse one would."""
        self.head_yaw = 0.0
        """The head_yaw joint, at index 7 of the joints, in radians, positive left."""
        self.extra_state: dict[str, Any] = {}
        self.drop_state: set[str] = set()
        """Members left out of every state frame, as a shim that does not report one would."""
        self.answers: dict[str, Any] = {}
        """What a call is answered with in place of the shim's own answer, by method."""
        self.twist = [0.0, 0.0, 0.0]
        """What the robot was last asked for, `move.requested`. A stop zeroes it."""
        self.moved_at: float | None = None
        """When the newest `robot.move` was read, which robotd's deadman ages."""
        self.stand_after_s: float | None = None
        """Stand a fallen robot up this long after `robot.enable` arrives, as its own software
        getting it up would, or never."""
        self.enabled_at: float | None = None
        self.notifications: list[dict[str, Any]] = []
        self.requests: list[dict[str, Any]] = []
        self.arrived: list[tuple[float, str]] = []
        """When each line arrived, on this machine's monotonic clock, and which call it was."""
        self.rejected: list[tuple[str, str]] = []
        self.frames_sent = 0
        self.writers: list[asyncio.StreamWriter] = []
        self.server: asyncio.AbstractServer | None = None
        self.port = 0
        self._streams: list[asyncio.Task[None]] = []
        self._t0 = time.monotonic()

    async def start(self) -> None:
        self.server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        self.port = self.server.sockets[0].getsockname()[1]

    @property
    def address(self) -> str:
        return f"tcp://127.0.0.1:{self.port}"

    async def stop(self) -> None:
        assert self.server is not None
        for stream in self._streams:
            stream.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await stream
        self.hang_up()
        self.server.close()
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self.server.wait_closed(), 2.0)

    def hang_up(self) -> None:
        """Close every connection from this end, as a tunnel pulled out does."""
        for writer in self.writers:
            writer.close()

    def driving(self) -> list[float]:
        """The twist the legs are on now: the newest one asked for, until a stop, or until
        robotd's deadman finds it older than `up.DEADMAN_S`, which is what the shim is asked to
        keep (`up.SHIM_DEADMAN`)."""
        if self.moved_at is None or time.monotonic() - self.moved_at > up.DEADMAN_S:
            return [0.0, 0.0, 0.0]
        return list(self.twist)

    def methods(self) -> list[str]:
        return [method for _at, method in self.arrived]

    def frame(self) -> dict[str, Any]:
        """The `robot.state` notification for this tick, from the attributes as they are now."""
        if (
            self.stand_after_s is not None
            and self.enabled_at is not None
            and time.monotonic() - self.enabled_at >= self.stand_after_s
        ):
            self.fallen, self.policy, self.enabled_at = False, "stand", None
        safety: dict[str, Any] = {"fallen": self.fallen, "limp": self.limp, "gravity": [0, 0, -1]}
        if self.picked_up is not None:
            safety["picked_up"] = self.picked_up
        joints = [0.0] * len(up.JOINT_NAMES)
        joints[up.JOINT_NAMES.index("head_yaw")] = self.head_yaw
        params: dict[str, Any] = {
            "t": time.monotonic() - self._t0,
            "move": {"requested": list(self.twist), "applied": self.driving()},
            "head": [0.0, 0.0, self.head_yaw, 0.0],
            "policy": self.policy,
            "safety": safety,
            "loop": {"hz": 50.0, "missed": 0},
            "joints": joints,
            "targets": joints,
            "odom": {"position": [0.0, 0.0, 0.0], "yaw": 0.0},
            "t_ns": int(time.monotonic_ns()),
        }
        params |= self.extra_state
        for key in self.drop_state:
            params.pop(key, None)
        return {"jsonrpc": "2.0", "method": "robot.state", "params": params}

    async def _publish(self, writer: asyncio.StreamWriter, hz: int) -> None:
        while not writer.is_closing():
            try:
                writer.write((json.dumps(self.frame()) + "\n").encode())
                await writer.drain()
            except (ConnectionError, OSError):
                return
            self.frames_sent += 1
            await asyncio.sleep(1.0 / max(1, hz))

    def refusal(self, method: str, params: Any) -> tuple[int, str] | None:
        """The error the shim answers this call with, or None when it takes it."""
        if method in self.missing or method not in PARAMS.keys() | NO_PARAMS:
            return up.ERR_METHOD_NOT_FOUND, f"unknown method {json.dumps(method)}"
        if method in NO_PARAMS:
            return None
        known, required = PARAMS[method]
        if not isinstance(params, dict):
            return up.ERR_INVALID_PARAMS, "expected a map"
        for member in params:
            if member not in known:
                return up.ERR_INVALID_PARAMS, f"unknown field `{member}`"
        for member in sorted(required - params.keys()):
            return up.ERR_INVALID_PARAMS, f"missing field `{member}`"
        if method == "robot.sound" and params["tag"] not in up.SOUND_TAG_LIST:
            return up.ERR_INVALID_PARAMS, f"unknown variant `{params['tag']}`"
        return None

    def _answer(self, method: str, params: dict[str, Any], writer: asyncio.StreamWriter) -> Any:
        if method == "hello":
            result: dict[str, Any] = {"daemon_version": "0.1.0", "revision": None}
            if self.api_version is not NO_VERSION:
                result["api_version"] = self.api_version
            return result
        if method == "robot.subscribe":
            answer: dict[str, Any] = {"accepted": True}
            if self.lists_skills:
                answer["skills"] = list(self.skills)
            hz = params.get("hz") or 10
            self._streams.append(asyncio.create_task(self._publish(writer, hz)))
            return answer
        if method == "robot.health":
            health: dict[str, Any] = {"healthy": self.healthy, "battery": {"percent": 71.0}}
            if not self.healthy:
                health["reason"] = "servo bus timeout"
            return health
        if method == "robot.stop":
            self.twist = [0.0, 0.0, 0.0]
            return {"accepted": True}
        if method == "robot.do":
            listed = params["skill"] in self.skills
            return {"accepted": listed, "reason": None if listed else "unknown skill"}
        if method == "robot.enable":
            self.enabled_at = time.monotonic()
            return {"accepted": True}
        return {"accepted": True}  # robot.sound

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.writers.append(writer)
        while line := await reader.readline():
            msg = json.loads(line)
            method, params = msg["method"], msg.get("params") or {}
            self.arrived.append((time.monotonic(), method))
            refused = self.refusal(method, params)
            if "id" not in msg:
                self.notifications.append(msg)
                if refused is not None:
                    self.rejected.append((method, refused[1]))  # nothing is said back
                elif method == "robot.move" and not self.drop_moves:
                    self.twist = [float(params.get(k, 0.0)) for k in ("vx", "vy", "vyaw")]
                    self.moved_at = time.monotonic()
                continue
            self.requests.append(msg)
            if refused is not None:
                self.rejected.append((method, refused[1]))
                error = {"code": refused[0], "message": refused[1]}
                answer: dict[str, Any] = {"jsonrpc": "2.0", "id": msg["id"], "error": error}
            else:
                result = (
                    self.answers[method]
                    if method in self.answers
                    else self._answer(method, params, writer)
                )
                answer = {"jsonrpc": "2.0", "id": msg["id"], "result": result}
            writer.write((json.dumps(answer) + "\n").encode())
            await writer.drain()
        writer.close()


@pytest.fixture
async def shim():
    fake = FakeShim()
    await fake.start()
    yield fake
    await fake.stop()


async def _connected(fake: FakeShim, **kwargs: Any) -> HuebotAdapter:
    adapter = make("jsonrpc", address=fake.address, **kwargs)
    await adapter.connect()
    return adapter


def _executor(adapter: HuebotAdapter, **kwargs: Any) -> Executor:
    assert adapter.manifest is not None
    registry = registry_from_manifest(adapter.manifest, adapter)
    return Executor(
        registry, adapter, contract=EVERYTHING.frontmatter, manifest=adapter.manifest, **kwargs
    )


async def _until(t: JsonRpcUnixTransport, check: Callable[[dict[str, Any]], bool]) -> None:
    """Wait until the transport has read a state frame `check` accepts, which is when a change
    made on the fake has reached quackd. The fake sends one every tenth of a second."""
    for _ in range(300):
        if check(t._last_state or {}):
            return
        await asyncio.sleep(0.01)
    raise AssertionError(
        f"the transport never read the frame the fake was changed to: {t._last_state}"
    )


async def _landed(t: JsonRpcUnixTransport) -> None:
    """Return once the shim has read every line the transport sent before this one: it reads a
    connection in order, so by the time it answers this, it has read everything ahead of it."""
    await t.request(up.ROBOT_HEALTH.name)


def _transport(adapter: HuebotAdapter) -> JsonRpcUnixTransport:
    assert isinstance(adapter.transport, JsonRpcUnixTransport)
    return adapter.transport


# ── the description ─────────────────────────────────────────────────────────────────────


def test_the_description_is_a_biped_with_no_gaze_and_quackds_own_speed_caps() -> None:
    """The static manifest, which `validate` and `announce` read, before any shim is asked
    anything: every verb the adapter has, none that turns the head, and quackd's own clamp."""
    manifest = describe("jsonrpc")
    names = set(manifest.verb_names())
    assert {"move", "observe", "go_to", "search_scan", "approach_and", "say"} <= names
    assert set(HUEBOT_VERBS) <= names
    assert not names & {"gaze", "watch", "look"}, names
    assert "gaze" not in manifest.intents
    assert manifest.limits == {"max_vx": 0.15, "max_vy": 0.1, "max_wz": 0.75}
    assert "camera_fov_deg" not in manifest.limits, "nobody published the lens"
    assert manifest.blurb == "a small biped robot (25 cm)"
    assert manifest.embodiment == "biped" and manifest.mobility == "legged"
    sheet = manifest.datasheet
    assert sheet is not None and sheet.mass_kg is None, "the maker publishes no mass"
    for figure in (sheet.height_m, sheet.dof):
        assert figure is not None and figure.confidence == "official"
        assert figure.source == "ce-net.com/huebot, read 2026-10-20"
    assert (sheet.height_m.value, sheet.dof.value) == (0.25, 15)  # type: ignore[union-attr]
    assert sheet.manipulator == "none" and sheet.arms == 0
    assert any("still being tuned" in note for note in sheet.notes)
    assert any(c.startswith("carry or push") for c in sheet.cannot)
    assert any(c.startswith("climb") for c in sheet.cannot)
    assert {"camera", "tof", "microphone"} == set(manifest.sensors)


async def test_the_mock_is_the_description_whole() -> None:
    """The mock reports no skill list and always has a camera, so it is described whole, and its
    connected manifest is the static one, which keeps the digests equal."""
    adapter = make_adapter("huebot:mock")
    live = await adapter.connect()
    assert live.digest() == describe_spec(parse_robot_spec("huebot:mock")).digest()
    assert parse_robot_spec("huebot").key == "huebot:mock", "the mock is the default backend"


@pytest.mark.parametrize(
    ("skills", "offered"),
    [
        (None, ["sit", "stand", "kick", "grab"]),
        ([], []),
        (["kick_left"], []),
        (["kick_left", "kick_right"], ["kick"]),
        (["sit_toggle"], ["sit", "stand"]),
        (["ground_pick", "roulade"], ["grab"]),
        (ALL_SKILLS, ["sit", "stand", "kick", "grab"]),
    ],
    ids=["undescribed", "none", "one-kick", "both-kicks", "sit-toggle", "ground-pick", "all"],
)
def test_a_skill_verb_is_offered_only_when_the_shim_lists_every_skill_it_sends(
    skills: list[str] | None, offered: list[str]
) -> None:
    """`kick` sends either leg's kick, so it needs both. A verb withdrawn takes its
    precondition with it, or the manifest would refuse to build."""
    assert offered_skill_verbs(skills) == offered
    manifest = huebot_manifest("jsonrpc", skills=skills)
    names = set(manifest.verb_names())
    for verb in SKILLS_OF:
        assert (verb in names) is (verb in offered), verb
        assert (verb in manifest.preconditions) is (verb in offered), verb
    assert {"stand_up", "quack", "say", "move"} <= names, "a verb that sends no skill stays"


def test_a_huebot_with_no_camera_has_no_verb_that_needs_one() -> None:
    blind = huebot_manifest("jsonrpc", camera=False)
    needs_eyes = {"observe", "go_to", "search_scan", "approach_and"}
    assert not needs_eyes & set(blind.verb_names())
    assert "camera" not in blind.sensors
    assert not needs_eyes & set(blind.preconditions)


def test_stand_up_waits_inside_its_own_timeout() -> None:
    """The longest wait plus a poll has to end before the executor cancels the verb, or a robot
    that stands at the last moment is reported as a timeout."""
    timeout = HUEBOT_VERBS["stand_up"].timeout_s
    assert timeout is not None and timeout > STAND_UP_WAIT_S + 2 * STAND_UP_POLL_S
    assert (STAND_UP_POLL_S, STAND_UP_WAIT_S) == (0.25, 10.0)


def test_the_head_fades_for_nobody() -> None:
    """`go_to` and `search_scan` wait `head_fade_s` after centring the head, which on a
    Microduck that glances on its own is a second and a half. Huebot is sent no look at all, so
    there is nothing to wait out, on any backend."""
    assert HuebotAdapter(MockTransport()).head_fade_s == 0.0
    assert HuebotAdapter(JsonRpcUnixTransport("tcp://127.0.0.1:1")).head_fade_s == 0.0
    assert HuebotAdapter(JsonRpcUnixTransport("tcp://127.0.0.1:1")).frames_upright is False


# ── the connect ─────────────────────────────────────────────────────────────────────────


async def test_connect_shakes_hands_subscribes_and_narrows_to_what_the_shim_has(
    shim: FakeShim,
) -> None:
    shim.skills = ["kick_left", "kick_right"]
    adapter = await _connected(shim)
    t = _transport(adapter)
    assert [r["method"] for r in shim.requests][:2] == ["hello", "robot.subscribe"]
    assert shim.requests[0]["params"] == {"api_version": CURRENT_API}
    assert shim.requests[1]["params"] == {"hz": jsonrpc_unix.STATE_HZ}
    assert t.runtime_warning is None and t.skills == ("kick_left", "kick_right")
    manifest = adapter.manifest
    assert manifest is not None
    assert "kick" in manifest.verb_names()
    assert not {"sit", "stand", "grab"} & set(manifest.verb_names())
    # no --camera-url: nothing that needs a picture is offered
    assert not {"observe", "go_to", "search_scan", "approach_and"} & set(manifest.verb_names())
    state = await adapter.get_state()
    assert state.posture == "standing" and state.extras["fall_detection"] is True
    assert state.extras["shim"]["api_version"] == CURRENT_API
    assert state.extras["assumptions"] == [up.STAND_UP_RPC.name]
    health = await adapter.health()
    assert health.ok and health.battery_percent == 71.0
    assert health.extras["shim"] == f"API v{CURRENT_API}, build 0.1.0, revision none"
    assert not shim.rejected, shim.rejected
    await adapter.disconnect()


@pytest.mark.parametrize("address", [None, ""])
async def test_no_address_is_refused_naming_the_ssh_forward(address: str | None) -> None:
    """The shim is wherever its maker put it, so there is no default to fall back on, and the
    refusal names the forward that reaches it."""
    t = JsonRpcUnixTransport(address)
    with pytest.raises(TransportError) as refused:
        await t.connect()
    assert str(refused.value) == NO_ADDRESS
    assert "ssh -L 9870:<socket> <huebot>" in NO_ADDRESS
    assert "--address tcp://127.0.0.1:9870" in NO_ADDRESS
    assert t._writer is None and t._pump is None, "nothing was opened"
    with pytest.raises(TransportError, match="needs --address"):
        await make("jsonrpc", address=address).connect()


def test_a_run_with_no_address_is_refused_before_anything_moves(tmp_path: Path) -> None:
    result = CliRunner().invoke(
        app,
        [
            *("run", "hello-world", "--robot", "huebot:jsonrpc", "--llm", "fake"),
            *("--no-memory", "--runs-dir", str(tmp_path)),
        ],
        env={"COLUMNS": "400"},
    )
    assert result.exit_code != 0
    said = " ".join(result.output.split())
    assert "huebot:jsonrpc needs --address" in said, said
    assert "ssh -L 9870:<socket> <huebot>" in said and "tcp://127.0.0.1:9870" in said


@pytest.mark.skipif(sys.platform != "win32", reason="the refusal is Windows' own")
async def test_a_unix_socket_on_windows_names_the_forward() -> None:
    with pytest.raises(TransportError, match=r"ssh -L 9870:<socket> <huebot>"):
        await JsonRpcUnixTransport("unix:///run/huebot.sock").connect()


async def test_a_shim_older_than_the_floor_is_refused() -> None:
    fake = FakeShim(api_version=FLOOR - 1)
    await fake.start()
    try:
        with pytest.raises(TransportError, match=f"API v{FLOOR - 1}, older than API v{FLOOR}"):
            await _connected(fake)
    finally:
        await fake.stop()


@pytest.mark.parametrize("answered", [NO_VERSION, "41", None, True, 41.0])
async def test_a_hello_that_names_no_version_is_refused(answered: object) -> None:
    fake = FakeShim(api_version=answered)
    await fake.start()
    try:
        with pytest.raises(TransportError, match="not a version number"):
            await _connected(fake)
    finally:
        await fake.stop()


async def test_a_shim_on_another_version_is_served_and_warned_about() -> None:
    fake = FakeShim(api_version=CURRENT_API + 3)
    await fake.start()
    try:
        adapter = await _connected(fake)
        t = _transport(adapter)
        assert t.runtime_warning is not None and "newer than" in t.runtime_warning
        assert f"answer with API v{CURRENT_API}" in t.runtime_warning
        assert (await adapter.get_state()).extras["shim"]["warning"] == t.runtime_warning
        await adapter.disconnect()
    finally:
        await fake.stop()


async def test_a_socket_with_no_robot_subscribe_is_not_a_shim_quackd_can_drive() -> None:
    fake = FakeShim(missing=frozenset({"robot.subscribe"}))
    await fake.start()
    try:
        with pytest.raises(TransportError) as refused:
            await _connected(fake)
        said = str(refused.value)
        assert "not robotd and not a shim quackd can drive" in said
        assert "docs/adapters/huebot/README.md" in said
    finally:
        await fake.stop()


async def test_a_subscribe_answer_with_no_skill_list_offers_no_skill_verb() -> None:
    fake = FakeShim(lists_skills=False)
    await fake.start()
    try:
        adapter = await _connected(fake)
        assert _transport(adapter).skills == ()
        assert adapter.manifest is not None
        assert not set(SKILLS_OF) & set(adapter.manifest.verb_names())
        await adapter.disconnect()
    finally:
        await fake.stop()


# ── a walk, its echo and the deadman ────────────────────────────────────────────────────


async def test_a_walk_feeds_the_deadman_and_the_shim_echoes_it(shim: FakeShim) -> None:
    """A walk re-sends `robot.move` every 100 ms, so robotd's 500 ms deadman never fires while a
    verb walks, and the shim's own `move.requested` shows each twist it took. Clamped to
    quackd's own caps first: 0.3 m/s asked is 0.15 m/s sent."""
    adapter = await _connected(shim)
    t = _transport(adapter)
    result = await _executor(adapter).run_verb("walk", {"vx": 0.3, "duration_s": 1.0})
    assert result.ok and "clamped" in result.summary, result.summary
    moves = [(at, m) for at, m in shim.arrived if m == "robot.move"]
    assert len(moves) == 10
    gaps = [b[0] - a[0] for a, b in itertools.pairwise(moves)]
    assert max(gaps) < up.DEADMAN_S / 2, gaps
    assert {n["params"]["vx"] for n in shim.notifications if n["method"] == "robot.move"} == {0.15}
    assert shim.methods()[-1] == "robot.stop" and shim.driving() == [0.0, 0.0, 0.0]
    assert t.move_echo is not None and t.move_echo["taken"] is True, t.move_echo
    assert t.move_echo["twist"] == [0.15, 0.0, 0.0]
    await adapter.disconnect()


async def test_a_walk_whose_link_is_cut_leaves_the_robots_deadman_to_stop_it(
    shim: FakeShim,
) -> None:
    """Pull the tunnel out mid-walk. quackd cannot deliver the stop, says so and does not
    raise, and the next twist fails the verb rather than being reported. What stops the legs is
    the robot's own deadman, which the shim is asked to keep: the twist it drives on ages past
    500 ms and goes to zero with nothing more from quackd."""
    adapter = await _connected(shim)
    t = _transport(adapter)
    walk = asyncio.create_task(_executor(adapter).run_verb("walk", {"vx": 0.1, "duration_s": 5.0}))
    for _ in range(300):
        if shim.driving() == [0.1, 0.0, 0.0]:
            break
        await asyncio.sleep(0.01)
    assert shim.driving() == [0.1, 0.0, 0.0], "the walk never reached the shim"
    shim.hang_up()
    result = await asyncio.wait_for(walk, 5.0)
    assert not result.ok and "the shim closed the connection" in result.summary, result.summary
    assert t.stop_error is not None and "closed the connection" in t.stop_error
    last_move = max(at for at, m in shim.arrived if m == "robot.move")
    await asyncio.sleep(max(0.0, last_move + up.DEADMAN_S + 0.1 - time.monotonic()))
    assert shim.driving() == [0.0, 0.0, 0.0], "the deadman never fired"
    assert shim.twist == [0.1, 0.0, 0.0], "nothing from quackd zeroed it: the deadman did"
    await t.stop()  # never raises on a dead link
    await adapter.disconnect()


async def test_a_move_the_shim_does_not_take_is_held_back_and_the_walk_fails() -> None:
    """robot.move is a notification, so a shim that cannot parse one says nothing. Its state
    goes on showing the old twist, and after three frames quackd holds the walk back."""
    fake = FakeShim(drop_moves=True)
    await fake.start()
    try:
        adapter = await _connected(fake)
        t = _transport(adapter)
        result = await _executor(adapter).run_verb("walk", {"vx": 0.1, "duration_s": 3.0})
        assert not result.ok and "has not taken robot.move" in result.summary, result.summary
        assert t.move_echo is not None and t.move_echo["taken"] is False
        assert t.move_echo["requested"] == [0.0, 0.0, 0.0]
        sent = sum(1 for m in fake.methods() if m == "robot.move")
        assert sent < 10, f"the walk went on through {sent} twists"
        await adapter.disconnect()
    finally:
        await fake.stop()


class _Verb:
    """A verb's own task, which makes the calls a test hands it, one at a time, in order: the
    transport keeps a walk and its hold by the task that sent the twists, until it ends."""

    def __init__(self, t: JsonRpcUnixTransport) -> None:
        self.t = t
        self._calls: asyncio.Queue[Any] = asyncio.Queue()
        self.task = asyncio.create_task(self._run(), name="a verb")

    async def _run(self) -> None:
        while (call := await self._calls.get()) is not None:
            make_call, answer = call
            try:
                answer.set_result(await make_call())
            except Exception as e:
                answer.set_exception(e)

    async def send(self, intent: Intent) -> Any:
        answer = asyncio.get_running_loop().create_future()
        await self._calls.put((lambda: self.t.send_intent(intent), answer))
        return await answer

    async def end(self) -> None:
        await self._calls.put(None)
        await self.task


async def test_a_robot_picked_up_mid_walk_is_not_walked_again_until_the_verb_ends(
    shim: FakeShim,
) -> None:
    """Put back down, Huebot reads standing again, and the verb that was walking is still the
    one that was walking when it was picked up: none of its twists goes out until it ends."""
    adapter = await _connected(shim)
    t = _transport(adapter)
    walk = _Verb(t)
    assert (await walk.send(Intent.move(0.1, 0.0, 0.0))).accepted
    shim.picked_up, shim.policy = True, "held"
    await _until(t, lambda s: s["safety"].get("picked_up") is True)
    ack = await walk.send(Intent.move(0.1, 0.0, 0.0))
    assert not ack.accepted and "somebody is holding Huebot" in (ack.reason or "")
    shim.picked_up, shim.policy = False, "walk"
    await _until(t, lambda s: s["safety"].get("picked_up") is False and s["policy"] == "walk")
    state = await t.get_state()
    assert state.posture == "unknown" and state.extras["posture_reason"].startswith(HELD_FOR_A_VERB)
    ack = await walk.send(Intent.move(0.1, 0.0, 0.0))
    assert not ack.accepted and "until the verb that was moving it ends" in (ack.reason or "")
    await _landed(t)
    assert sum(1 for m in shim.methods() if m == "robot.move") == 1
    await walk.end()
    assert (await t.get_state()).posture == "standing"
    nxt = _Verb(t)
    assert (await nxt.send(Intent.move(0.1, 0.0, 0.0))).accepted, "the next verb starts free"
    await nxt.end()
    await adapter.disconnect()


@pytest.mark.parametrize(
    ("policy", "safety", "why"),
    [
        ("held", {}, "no network is driving the legs"),
        ("limp_fall", {}, "riding a fall down"),
        ("kick_left", {}, "one of its skills"),
        ("dance", {}, "which quackd has not read"),
        ("walk", {"limp": True}, "safety.limp"),
        ("walk", {"picked_up": True, "fallen": True}, "somebody is holding Huebot"),
        (None, {}, "names no policy"),
    ],
    ids=["held", "limp-fall", "skill", "unread", "limp", "held-before-fallen", "no-policy"],
)
async def test_only_walk_and_stand_stand_the_robot_up(
    policy: str | None, safety: dict[str, Any], why: str
) -> None:
    """An allowlist: every other label, a skill's own name and one quackd has never read, is
    `unknown`, and a walk is refused on it before any twist goes out."""
    fake = FakeShim(policy=policy, skills=["kick_left", "kick_right"])
    fake.picked_up = safety.get("picked_up", False)
    fake.limp = safety.get("limp", False)
    fake.fallen = safety.get("fallen", False)
    await fake.start()
    try:
        adapter = await _connected(fake)
        state = await adapter.get_state()
        assert state.posture == "unknown" and why in state.extras["posture_reason"], state
        result = await _executor(adapter).run_verb("walk", {"vx": 0.1, "duration_s": 0.3})
        assert not result.ok
        assert "robot.move" not in fake.methods()
        await adapter.disconnect()
    finally:
        await fake.stop()


async def test_silence_and_a_frame_with_no_safety_block_are_not_a_standing_robot(
    shim: FakeShim,
) -> None:
    shim.drop_state = {"safety"}
    adapter = await _connected(shim)
    state = await adapter.get_state()
    assert state.posture == "unknown" and state.extras["fall_detection"] is False
    assert "carries no safety block" in state.extras["posture_reason"]
    shim.drop_state = set()
    await _until(_transport(adapter), lambda s: "safety" in s)
    assert (await adapter.get_state()).posture == "standing"
    shim.hang_up()
    await asyncio.sleep(0.2)
    state = await adapter.get_state()
    assert (
        state.posture == "unknown"
        and "the link to the shim is gone" in state.extras["posture_reason"]
    )
    await adapter.disconnect()


async def test_an_unhealthy_shim_fails_the_heartbeat() -> None:
    fake = FakeShim(healthy=False)
    await fake.start()
    try:
        adapter = await _connected(fake)
        health = await adapter.health()
        assert not health.ok and "servo bus timeout" in (health.reason or "")
        await adapter.disconnect()
    finally:
        await fake.stop()


async def test_an_answer_with_no_accepted_is_not_a_yes(shim: FakeShim) -> None:
    shim.answers["robot.do"] = {"ok": True}
    adapter = await _connected(shim)
    ack = await adapter.send_intent(Intent.do("kick_left"))
    assert not ack.accepted and "carries no accepted" in (ack.reason or "")
    await adapter.disconnect()


async def test_the_head_yaw_is_read_off_the_joint_at_index_seven(shim: FakeShim) -> None:
    shim.head_yaw = math.radians(30.0)
    adapter = await _connected(shim)
    await _until(_transport(adapter), lambda s: (s.get("joints") or [0.0] * 15)[7] != 0.0)
    assert (await adapter.get_state()).extras["head_yaw_deg"] == 30.0
    await adapter.disconnect()


# ── nothing goes out that the shim does not answer ──────────────────────────────────────


class _NothingThere:
    """A detector that sees nothing, so `search_scan` turns through every step it has."""

    name = "nothing_there"

    def detect(self, image: Image.Image) -> list[Detection]:
        return []


async def test_robot_look_is_never_sent(shim: FakeShim, monkeypatch: pytest.MonkeyPatch) -> None:
    """A look is refused where it is made, before anything is written, and the verbs that
    centre the head before they steer go on without it, waiting for no fade."""

    async def fetch(self: JsonRpcUnixTransport) -> Image.Image:
        return Image.new("RGB", (64, 48), (40, 40, 40))

    monkeypatch.setattr(JsonRpcUnixTransport, "_fetch_frame", fetch)
    adapter = await _connected(shim, camera_url="http://127.0.0.1:1/still.jpg")
    t = _transport(adapter)
    assert adapter.manifest is not None and "search_scan" in adapter.manifest.verb_names()
    lines = t._lines
    ack = await adapter.send_intent(Intent.look(1.0, 0.5, 0.0))
    assert not ack.accepted and ack.reason == LOOK_REFUSED
    assert t._lines == lines, "a line went out for the look"
    ex = _executor(adapter, detector=_NothingThere())
    started = time.monotonic()
    result = await ex.run_verb("search_scan", {"target": "ball", "step_deg": 45, "max_steps": 1})
    assert not result.ok and "not found after 1 steps" in result.summary, result.summary
    assert time.monotonic() - started < 3.0, "the scan waited out a fade"
    await _landed(t)
    assert "robot.move" in shim.methods(), "the scan turned"
    assert not {"robot.look", "robot.head"} & set(shim.methods()), shim.methods()
    await adapter.disconnect()


async def test_the_turns_a_scan_counts_are_the_ones_the_shim_showed(shim: FakeShim) -> None:
    """Nothing answers a `robot.move`, so `search_scan` counts the turns it made by the ones the
    shim's state showed it took, and reads that off the object it was handed, the adapter. The
    mock says nothing of the kind, and must not read as a body that took none."""
    adapter = await _connected(shim)
    t = _transport(adapter)

    async def verb() -> bool | None:
        since = adapter.now()
        assert (await adapter.send_intent(Intent.move(0.0, 0.0, 0.5))).accepted
        await _until(t, lambda s: s["move"]["requested"] == [0.0, 0.0, 0.5])
        return twist_shown_since_of(adapter, since)

    assert await asyncio.create_task(verb()) is True
    assert twist_shown_since_of(HuebotAdapter(MockTransport()), 0.0) is None
    await adapter.disconnect()


async def test_a_skill_the_shim_did_not_list_is_never_sent(shim: FakeShim) -> None:
    shim.skills = ["kick_left", "kick_right"]
    adapter = await _connected(shim)
    t = _transport(adapter)
    lines = t._lines
    for skill in ("ground_pick", "sit_toggle", "roulade"):
        ack = await adapter.send_intent(Intent.do(skill))
        assert not ack.accepted and "did not list" in (ack.reason or ""), ack
        assert "kick_left, kick_right" in (ack.reason or "")
    assert t._lines == lines, "a robot.do went out for a skill nobody listed"
    assert (await adapter.send_intent(Intent.do("kick_left"))).accepted
    assert [r["params"] for r in shim.requests if r["method"] == "robot.do"] == [
        {"skill": "kick_left"}
    ]
    result = await _executor(adapter).run_verb("kick", {"leg": "right"})
    assert result.ok, result.summary
    await adapter.disconnect()


# ── a run with no camera, end to end ────────────────────────────────────────────────────


@contextlib.contextmanager
def _shim_on_its_own_loop(**kwargs: Any) -> Iterator[FakeShim]:
    """A fake shim serving from a loop in a thread of its own, for the CLI, which runs a loop of
    its own with `asyncio.run`."""
    loop = asyncio.new_event_loop()
    started: list[FakeShim] = []
    ready = threading.Event()

    def serve() -> None:
        asyncio.set_event_loop(loop)
        fake = FakeShim(**kwargs)
        loop.run_until_complete(fake.start())
        started.append(fake)
        ready.set()
        loop.run_forever()

    thread = threading.Thread(target=serve, name="fake-shim", daemon=True)
    thread.start()
    assert ready.wait(30), "the fake shim never started"
    try:
        yield started[0]
    finally:
        asyncio.run_coroutine_threadsafe(started[0].stop(), loop).result(30)
        loop.call_soon_threadsafe(loop.stop)
        thread.join(30)
        loop.close()


def test_a_run_with_no_camera_drives_the_shim_without_a_verb_that_needs_one(
    tmp_path: Path,
) -> None:
    """`quackd run` on huebot:jsonrpc against the fake shim, with no --camera-url: the pilot is
    offered nothing that needs a picture, quacks, walks and quacks, and the shim is sent nothing
    it does not answer."""
    with _shim_on_its_own_loop() as fake:
        result = CliRunner().invoke(
            app,
            [
                *("run", "hello-world", "--robot", "huebot:jsonrpc", "--address", fake.address),
                *("--llm", "fake", "--no-memory", "--runs-dir", str(tmp_path)),
            ],
            env={"COLUMNS": "400"},
        )
        methods = fake.methods()
        rejected = list(fake.rejected)
    assert result.exit_code == 0, result.output
    said = " ".join(result.output.split())
    assert "SUCCESS" in said, said
    for blind in ("observe", "go_to", "search_scan", "approach_and"):
        assert f"`{blind}`" not in said, blind
    assert "robot.move" in methods and "robot.sound" in methods
    assert not {"robot.look", "robot.head"} & set(methods)
    assert not rejected, rejected


async def test_a_camera_that_never_answers_is_no_camera(shim: FakeShim) -> None:
    adapter = await _connected(shim, camera_url="http://127.0.0.1:9/never")
    t = _transport(adapter)
    assert t.camera_working is False
    assert adapter.manifest is not None and "observe" not in adapter.manifest.verb_names()
    assert "camera" not in adapter.manifest.sensors
    assert await adapter.get_frame() is None and adapter.camera_error
    assert t.camera_health()["configured"] is True and t.camera_health()["ok"] is False
    await adapter.disconnect()


# ── stand_up waits for the robot to stand ───────────────────────────────────────────────


async def test_stand_up_returns_as_soon_as_the_robot_stands_and_sends_nothing_meanwhile() -> None:
    """The robot gets up 1.2 s after `robot.enable`. A single read three seconds later, which
    the Microduck's verb made before 0.21.0, would have kept the pilot waiting; this one answers
    within a poll of it, and nothing goes to the shim meanwhile but the reads it already makes."""
    fake = FakeShim(fallen=True)
    fake.stand_after_s = 1.2
    await fake.start()
    try:
        adapter = await _connected(fake)
        started = time.monotonic()
        result = await _executor(adapter).run_verb("stand_up", {})
        took = time.monotonic() - started
        assert result.ok and result.summary.startswith("upright after"), result.summary
        assert 1.2 <= result.data["waited_s"] < 2.2, result.data
        assert took < 3.0, took
        sent = fake.methods()
        after = sent[sent.index("robot.enable") + 1 :]
        assert set(after) <= {"robot.health"}, after
        assert sent.count("robot.enable") == 1
        await adapter.disconnect()
    finally:
        await fake.stop()


def _mock_state(
    posture: str, *, fallen: bool = False, policy: str = "walk", **extras: Any
) -> DuckState:
    told = {"fall_detection": True, "picked_up": False, "pickup_detection": True} | extras
    return DuckState(policy=policy, posture=posture, fallen=fallen, extras=told)  # type: ignore[arg-type]


class _AfterEnable(MockTransport):
    """A mock robot that reads `before` until `stand_up` asks it to drive, then `after`, and
    `then` from `then_at` seconds of its own time after the request. Keyed by time rather than by
    how many reads were made, since the executor reads the state before every verb too."""

    def __init__(
        self,
        before: DuckState,
        after: DuckState | None = None,
        *,
        then: DuckState | None = None,
        then_at: float = 0.0,
    ) -> None:
        super().__init__(states=[before])
        self.before, self.after, self.then, self.then_at = before, after or before, then, then_at
        self.enabled_at: float | None = None

    async def send_intent(self, intent: Intent) -> Any:
        if intent.kind == "enable":
            self.enabled_at = self.now()
        return await super().send_intent(intent)

    async def get_state(self) -> DuckState:
        if self.enabled_at is None:
            state = self.before
        elif self.then is not None and self.now() - self.enabled_at >= self.then_at:
            state = self.then
        else:
            state = self.after
        return state.model_copy(update={"t": self.now()})


async def _stand_up_on(robot: MockTransport) -> Any:
    adapter = HuebotAdapter(robot)
    await adapter.connect()
    return await _executor(adapter).run_verb("stand_up", {})


DOWN = _mock_state("fallen", fallen=True)


async def test_stand_up_says_the_robot_may_need_a_hand_only_once_ten_seconds_are_up() -> None:
    robot = _AfterEnable(DOWN)
    result = await _stand_up_on(robot)
    assert not result.ok and "may need a hand" in result.summary, result.summary
    assert "10 s later" in result.summary
    assert robot.sleeps == [STAND_UP_POLL_S] * round(STAND_UP_WAIT_S / STAND_UP_POLL_S)
    assert [i.kind for i in robot.intents] == ["enable"], "nothing but the request went out"


async def test_stand_up_calls_the_robot_upright_on_the_poll_it_stands() -> None:
    robot = _AfterEnable(DOWN, then=_mock_state("standing"), then_at=0.75)
    result = await _stand_up_on(robot)
    assert result.ok and result.summary == "upright after 0.8 s", result
    assert result.data["waited_s"] == 0.75
    assert robot.sleeps == [STAND_UP_POLL_S] * 3
    assert [i.kind for i in robot.intents] == ["enable"]


@pytest.mark.parametrize(
    ("after", "summary"),
    [
        (_mock_state("unknown", policy="limp_pose", fallen=True), "still answering a fall itself"),
        (_mock_state("unknown", policy="homing"), "10 s later posture is unknown"),
    ],
    ids=["answering-a-fall", "unknown"],
)
async def test_stand_up_judges_the_last_reading_once_the_wait_is_up(
    after: DuckState, summary: str
) -> None:
    """A robot still in its own answer to a fall is told to run the verb again, and one whose
    posture is unknown says why, each only once the whole wait is up."""
    robot = _AfterEnable(DOWN, after)
    result = await _stand_up_on(robot)
    assert not result.ok and summary in result.summary, result.summary
    assert sum(robot.sleeps) == STAND_UP_WAIT_S


@pytest.mark.parametrize(
    ("after", "summary"),
    [
        (_mock_state("unknown", fall_detection=False), "nothing reports whether it is upright"),
        (_mock_state("unknown", picked_up=True), "but somebody is holding Huebot"),
        (_mock_state("standing", picked_up=True), "but somebody is holding Huebot"),
    ],
    ids=["blind", "held", "held-upright"],
)
async def test_stand_up_stops_waiting_for_a_robot_somebody_picks_up_or_nothing_reports_on(
    after: DuckState, summary: str
) -> None:
    """A robot somebody picks up while `stand_up` waits is in their hands, and one nothing
    reports on cannot be called upright, so the wait ends on the first reading that says so,
    rather than running on to judge legs a person is holding. One held up in the air reads as
    standing and is still not called upright."""
    robot = _AfterEnable(DOWN, after)
    result = await _stand_up_on(robot)
    assert not result.ok and summary in result.summary, result.summary
    assert robot.sleeps == [STAND_UP_POLL_S], robot.sleeps


@pytest.mark.parametrize(
    ("state", "summary"),
    [
        (_mock_state("unknown", picked_up=True), "cannot stand_up: somebody is holding Huebot"),
        (_mock_state("unknown", fall_detection=False), "nothing is reporting whether"),
        (_mock_state("fallen", fallen=True, pickup_detection=False), "no safety.picked_up"),
        (_mock_state("unknown", move_hold="a verb"), "a verb that was walking is still running"),
        (_mock_state("sitting", policy="sit"), "upright and seated: run stand"),
        (_mock_state("standing"), "already upright"),
    ],
    ids=["held", "blind", "no-pickup-detector", "walk-held", "seated", "standing"],
)
async def test_stand_up_reads_the_robot_before_it_asks_it_to_drive(
    state: DuckState, summary: str
) -> None:
    robot = _AfterEnable(state)
    result = await _stand_up_on(robot)
    assert summary in result.summary, result.summary
    assert not robot.intents_of("enable"), "it asked a robot it should not have"
