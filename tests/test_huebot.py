"""Huebot's adapter against a fake of the shim its maker is asked to build.

Huebot's own API is closed, and its maker is writing a shim for the robot that is to answer the
JSON-RPC the Microduck's robotd speaks. `FakeShim` answers what docs/adapters/huebot/README.md asks
the shim to answer, as robotd does at the pin quackd read it at: `hello`, `robot.subscribe` and its
stream of `robot.state`, `robot.move` as notifications with robotd's deadman, `robot.stop`,
`robot.health`, `robot.do` for the skills it lists, `robot.sound` and `robot.enable`. Every other
call is `METHOD_NOT_FOUND`, `robot.look` and `robot.head` among them, since the shim has neither
yet. So this proves quackd's half of that contract, and nothing about a real Huebot, which nobody
has run.
"""

from __future__ import annotations

import asyncio
import contextlib
import http.client
import io
import itertools
import json
import math
import sys
import threading
import time
from collections.abc import Callable, Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
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
from quackd.transport.base import (
    Ack,
    DuckState,
    Intent,
    Posture,
    TransportError,
    twist_shown_since_of,
)
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
    SNAPSHOT_MAX_BYTES,
    JsonRpcUnixTransport,
    NotAStill,
    SnapshotRefused,
    StillTooSlow,
    fetch_still,
)
from quackd_huebot.verbs import SKILLS_OF, STAND_UP_POLL_S, STAND_UP_WAIT_S

#: Every verb, for the tests that run them through an executor and its allowlist.
EVERYTHING = parse_duck_text(
    "---\nduck: 0\nname: t\ndescription: d\nverbs:\n"
    "  allow: [walk, go_to, search_scan, kick, sit, stand, stand_up, quack, say, observe]\n"
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
        self.safety_block: dict[str, Any] | None = None
        """Every frame's `safety` block exactly as given, in place of the one built from the
        attributes, for a shim that writes something other than true or false there."""
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
        if self.safety_block is not None:
            safety = dict(self.safety_block)
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
        (None, ["sit", "stand", "kick"]),
        ([], []),
        (["kick_left"], []),
        (["kick_left", "kick_right"], ["kick"]),
        (["sit_toggle"], ["sit", "stand"]),
        (["ground_pick", "roulade"], []),
        (ALL_SKILLS, ["sit", "stand", "kick"]),
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


async def test_grab_is_offered_nowhere_even_to_a_shim_that_lists_ground_pick(
    shim: FakeShim,
) -> None:
    """`grab` sends `ground_pick`, and nobody has said what that does on Huebot or how its state
    would report holding something, so no Huebot is offered it: not the description, not the
    mock, and not a robot whose shim lists `ground_pick`. `approach_and` says `e.g. kick`."""
    assert "grab" not in HUEBOT_VERBS and "grab" not in SKILLS_OF
    for backend in ("mock", "jsonrpc"):
        described = describe(backend)
        assert "grab" not in described.verb_names() and "grab" not in described.preconditions
        (approach,) = [spec for spec in described.verbs if spec.name == "approach_and"]
        assert approach.description == "walk_to a target, then run another verb (e.g. kick)."
    assert "grab" not in (await make_adapter("huebot:mock").connect()).verb_names()
    shim.skills = [*ALL_SKILLS, "ground_pick"]
    adapter = await _connected(shim)
    assert adapter.manifest is not None and "grab" not in adapter.manifest.verb_names()
    assert {"sit", "stand", "kick"} <= set(adapter.manifest.verb_names())
    await adapter.disconnect()


async def test_a_real_huebot_claims_no_deadman_until_one_has_been_seen(
    shim: FakeShim, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Huebot's deadman is the shim's, which nobody has seen (`up.SHIM_DEADMAN`), so the
    description of a real Huebot claims none, connected or not, as the Open Duck's bridge and the
    ToddlerBot's daemon claim none they have not reported. The mock claims what every offline
    backend claims. Once a Huebot has passed step 3 of its checklist, `DEADMAN_SEEN` makes the
    claim."""
    real = describe("jsonrpc").safety_authority
    assert (real.native, real.deadman) == ("none", False)
    adapter = await _connected(shim)
    assert adapter.manifest is not None
    live = adapter.manifest.safety_authority
    assert (live.native, live.deadman) == ("none", False)
    await adapter.disconnect()
    mock = describe("mock").safety_authority
    assert (mock.native, mock.deadman) == ("robotd_deadman", True)
    monkeypatch.setattr("quackd_huebot.DEADMAN_SEEN", True)
    seen = describe("jsonrpc").safety_authority
    assert (seen.native, seen.deadman) == ("robotd_deadman", True)


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


# ── every verb, offline ─────────────────────────────────────────────────────────────────


class _Body(MockTransport):
    """The core's scripted body with a posture of its own, so each verb can be seen to do what
    it says rather than only to send something: `sit_toggle` sits a standing robot and stands a
    seated one, and `robot.enable` stands a fallen one up."""

    def __init__(self) -> None:
        super().__init__()
        self.posture: Posture = "standing"

    async def get_state(self) -> DuckState:
        state = await super().get_state()
        return state.model_copy(
            update={"posture": self.posture, "fallen": self.posture == "fallen"}
        )

    async def send_intent(self, intent: Intent) -> Ack:
        ack = await super().send_intent(intent)
        if intent.kind == "do" and intent.params.get("skill") == "sit_toggle":
            self.posture = "sitting" if self.posture == "standing" else "standing"
        elif intent.kind == "enable" and intent.params.get("on") and self.posture == "fallen":
            self.posture = "standing"
        return ack


class _BallUnderfoot:
    """Sees the ball dead ahead and close enough that `go_to` has arrived and `search_scan` has
    found it on the first frame either looks at."""

    name = "ball_underfoot"

    def detect(self, image: Image.Image) -> list[Detection]:
        return [
            Detection(label="ball", cx=0.5, cy=0.5, area=0.01, bearing_deg=0.0, est_distance_m=0.1)
        ]


async def test_every_verb_runs_offline_through_the_executor() -> None:
    """Every verb `huebot:mock` offers runs through the executor and its allowlist, each doing
    what it says on a body that sits, stands and gets up as it is asked to, which is what the
    backends table on Huebot's page says of the mock. A verb the manifest gains fails this until
    it is run here too."""
    body = _Body()
    adapter = HuebotAdapter(body)
    manifest = await adapter.connect()
    assert manifest.backend == "mock"
    every = manifest.verb_names()
    contract = parse_duck_text(
        "---\nduck: 0\nname: t\ndescription: d\nverbs:\n"
        f"  allow: [{', '.join(every)}]\nsuccess: [x]\n---\n# Task\nx\n"
    )
    ex = Executor(
        registry_from_manifest(manifest, adapter),
        adapter,
        contract=contract.frontmatter,
        manifest=manifest,
        detector=_BallUnderfoot(),
    )
    ran: list[str] = []

    async def run(verb: str, **params: Any) -> str:
        result = await ex.run_verb(verb, params)
        assert result.ok, (verb, result.summary)
        ran.append(verb)
        return result.summary

    await run("report_state")
    await run("observe")
    await run("say", text="hello")
    await run("quack")
    await run("move", vx=0.1, duration_s=0.4)
    assert body.intents_of("move"), "the walk sent no twist"
    await run("search_scan", target="ball")
    await run("go_to", target="ball")
    await run("kick", leg="left")
    await run("approach_and", target="ball", then="kick")
    assert [i.params["skill"] for i in body.intents_of("do")] == ["kick_left", "kick_right"]
    assert await run("sit") == "now sitting" and body.posture == "sitting"
    assert await run("stand") == "now standing" and body.posture == "standing"
    body.posture = "fallen"
    assert (await run("stand_up")).startswith("upright after") and body.posture == "standing"
    await run("stop")
    assert sorted(ran) == sorted(every), set(every) ^ set(ran)
    await adapter.disconnect()


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
    assert list(tmp_path.iterdir()) == [], "the refusal came before the run directory"


@pytest.mark.parametrize("address", [None, ""])
def test_no_address_is_refused_as_the_body_is_built(address: str | None) -> None:
    """Refused as the body is built, before it connects, since a run and a pilot flock build
    every body before they write anything: refused at the connect, a run left its directory
    behind. A description builds no transport, so what `validate` and `doctor` read still
    answers without an address."""
    with pytest.raises(TransportError) as refused:
        make_adapter("huebot:jsonrpc", address=address)
    assert str(refused.value) == NO_ADDRESS
    assert describe_spec(parse_robot_spec("huebot:jsonrpc")).backend == "jsonrpc"
    assert describe("jsonrpc").backend == "jsonrpc"


def test_a_pilot_flock_with_no_address_for_its_huebot_writes_nothing(tmp_path: Path) -> None:
    """A pilot flock builds its members before its run directory exists, so a Huebot with no
    address stored refuses the flock with nothing on disk."""
    from quackd.registry import Registry, RobotEntry, StoredFlock

    registry = Registry(tmp_path / "registry")
    registry.add_robot(RobotEntry(name="hue", spec="huebot:jsonrpc"))
    registry.add_robot(RobotEntry(name="duck", spec="microduck:mock"))
    registry.add_flock(StoredFlock(name="pair", members=["hue", "duck"]))
    result = CliRunner().invoke(
        app,
        [
            *("run", "flock-hello", "--flock", "pair", "--llm", "fake", "--no-gif", "--no-log"),
            *("--runs-dir", str(tmp_path / "runs"), "--memory-dir", str(tmp_path / "memory")),
            *("--registry-dir", str(tmp_path / "registry")),
        ],
        env={"COLUMNS": "400"},
    )
    assert result.exit_code != 0
    said = " ".join(result.output.split())
    assert "huebot:jsonrpc needs --address" in said, said
    runs = tmp_path / "runs"
    assert not runs.exists() or list(runs.iterdir()) == [], (
        "the refusal came after the run directory"
    )


@pytest.mark.parametrize(
    ("command", "refusal"),
    [("release", "huebot:jsonrpc needs --address"), ("rest-pose", "has no joints")],
)
def test_a_robot_command_refuses_a_huebot_with_no_address_in_one_line(
    tmp_path: Path, command: str, refusal: str
) -> None:
    """`quackd robot release` builds the robot to ask whether it is an arm, and catches only an
    adapter's refusal there, so a Huebot with no address went past it as a traceback. It is an
    adapter's refusal now. `rest-pose` refuses a body with no joints before it builds one."""
    from quackd.registry import Registry, RobotEntry

    Registry(tmp_path).add_robot(RobotEntry(name="hue", spec="huebot:jsonrpc"))
    result = CliRunner().invoke(
        app,
        ["robot", command, "hue", "--yes", "--registry-dir", str(tmp_path)],
        env={"COLUMNS": "400"},
    )
    assert result.exit_code == 1, result.output
    assert not isinstance(result.exception, TransportError), result.exception
    said = " ".join(result.output.split())
    assert refusal in said, said


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


#: What a test leaves out of a `safety` block, as against a value it writes there.
ABSENT = object()
#: A robot on its feet, and one on the floor, each as a `safety` block every frame carries.
UPRIGHT = {"fallen": False, "picked_up": False, "limp": False}
DOWN_ON_THE_FLOOR = {"fallen": True, "picked_up": False, "limp": False}


def _block(base: dict[str, Any], key: str, value: object) -> dict[str, Any]:
    block = dict(base)
    if value is ABSENT:
        del block[key]
    else:
        block[key] = value
    return block


async def _what_quackd_makes_of(safety: dict[str, Any]) -> dict[str, Any]:
    """What quackd makes of a robot whose every frame carries `safety`: the state it reads, and
    whether a walk sent a `robot.move` and `stand_up` a `robot.enable`. The robot stands up a
    moment after it is asked to, so a `stand_up` that asks ends quickly."""
    fake = FakeShim()
    fake.safety_block = safety
    fake.stand_after_s = 0.3
    await fake.start()
    try:
        adapter = await _connected(fake)
        state = await adapter.get_state()
        ex = _executor(adapter)
        await ex.run_verb("walk", {"vx": 0.1, "duration_s": 0.3})
        await ex.run_verb("stand_up", {})
        await _landed(_transport(adapter))
        sent = fake.methods()
        await adapter.disconnect()
    finally:
        await fake.stop()
    return {
        "posture": state.posture,
        "fall_detection": state.extras["fall_detection"],
        "pickup_detection": state.extras["pickup_detection"],
        "picked_up": state.extras["picked_up"],
        "walked": "robot.move" in sent,
        "enabled": "robot.enable" in sent,
    }


@pytest.mark.parametrize("value", [None, "true"], ids=["null", "string"])
@pytest.mark.parametrize("key", ["fallen", "picked_up", "limp"])
async def test_a_safety_flag_that_is_not_a_json_boolean_reads_as_the_key_being_absent(
    key: str, value: object
) -> None:
    """`fallen`, `picked_up` and `limp` count only as JSON booleans. A null `fallen` used to read
    as a fall detector saying all clear, and a null `picked_up` as a pick-up detector saying
    nobody holds the robot, so walks went out and `stand_up` asked legs nobody could vouch for to
    drive. Anything but true or false now reads exactly as the key being absent, on a robot on
    its feet and on one on the floor."""
    for base in (UPRIGHT, DOWN_ON_THE_FLOOR):
        given = await _what_quackd_makes_of(_block(base, key, value))
        absent = await _what_quackd_makes_of(_block(base, key, ABSENT))
        assert given == absent, (base, given, absent)


@pytest.mark.parametrize(
    ("safety", "policy"),
    [({"fallen": "x" * 5000, "picked_up": False}, "walk"), (None, "p" * 5000)],
    ids=["fallen", "policy"],
)
async def test_a_reason_quotes_no_more_than_a_short_piece_of_what_the_shim_sent(
    safety: dict[str, Any] | None, policy: str
) -> None:
    """`posture_reason` goes into every refusal that quotes it and into the log, and a frame can
    carry a value of any size: a `safety.fallen` or a `policy` of a megabyte was quoted whole."""
    fake = FakeShim(policy=policy)
    fake.safety_block = safety
    await fake.start()
    try:
        adapter = await _connected(fake)
        state = await adapter.get_state()
        reason = state.extras["posture_reason"]
        assert state.posture == "unknown" and len(reason) < 200, reason
        assert reason.count("x") + reason.count("p") < 100, reason
        await adapter.disconnect()
    finally:
        await fake.stop()


async def test_a_null_fall_or_pick_up_flag_sends_nothing_its_absence_would_not() -> None:
    """The two that move a robot nobody can vouch for, said outright. With `fallen` null nothing
    reports falls, so no walk goes out and `stand_up` asks nothing. With `picked_up` null nothing
    reports whether somebody holds the robot, so `stand_up` refuses a robot on the floor rather
    than ask its legs to drive. `limp` null is not limp."""
    blind = await _what_quackd_makes_of(_block(UPRIGHT, "fallen", None))
    assert blind["fall_detection"] is False and blind["posture"] == "unknown"
    assert not blind["walked"] and not blind["enabled"]
    unheld = await _what_quackd_makes_of(_block(DOWN_ON_THE_FLOOR, "picked_up", None))
    assert unheld["pickup_detection"] is False and unheld["picked_up"] is None
    assert not unheld["enabled"]
    loose = await _what_quackd_makes_of(_block(UPRIGHT, "limp", None))
    assert loose["posture"] == "standing" and loose["walked"]


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


@pytest.mark.parametrize(
    ("answer", "percent"),
    [
        ({"healthy": True, "battery": 80}, None),
        ({"healthy": True, "battery": "80%"}, None),
        ({"healthy": True, "battery": [80]}, None),
        ({"healthy": True, "battery": {"percent": "80%"}}, None),
        ({"healthy": True, "battery": {"percent": None}}, None),
        ([80], None),
        ({"healthy": True, "battery": {"percent": 55}}, 55.0),
    ],
    ids=["number", "string", "list", "string-percent", "null-percent", "answer-a-list", "object"],
)
async def test_a_battery_that_is_not_an_object_reads_as_no_battery(
    shim: FakeShim, answer: object, percent: float | None
) -> None:
    """`robot.health`'s `battery` is an object whose `percent` is a number. Anything else is no
    battery, and every state read goes on: a `battery` of `80` used to raise out of each one, so
    no verb could run, since the executor reads the robot before every one."""
    shim.answers["robot.health"] = answer
    adapter = await _connected(shim)
    state = await adapter.get_state()
    assert state.battery_percent == percent
    health = await adapter.health()
    assert health.ok and health.battery_percent == percent
    assert (await _executor(adapter).run_verb("walk", {"vx": 0.1, "duration_s": 0.3})).ok
    assert "robot.move" in shim.methods()
    await adapter.disconnect()


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


# ── a camera that sends more than one still ─────────────────────────────────────────────

#: One small JPEG, the still every camera below sends.
_STILL = io.BytesIO()
Image.new("RGB", (32, 24), (90, 40, 40)).save(_STILL, format="JPEG")
STILL_JPEG = _STILL.getvalue()


@contextlib.contextmanager
def _camera(kind: str, **opts: float) -> Iterator[tuple[str, threading.Event]]:
    """An HTTP camera on loopback, and an event set once the client has dropped what it was
    sent. `still` answers one JPEG; `mjpeg` a `multipart/x-mixed-replace` stream of them;
    `endless` an `image/jpeg` body with no end; `trickle` one byte every `every_s`. The rest
    write the wire by hand: `slow-headers` a header that never ends, a byte at a time;
    `redirect` a 302 whose body never ends; `trailers` a chunked body whose trailer never ends;
    `truncated` 10 of the 100 bytes its length promises; `error` a 503 whose page trickles. All
    but `still` and `truncated` send for `for_s`, a minute unless told otherwise, so that a fetch
    that never lets go fails its test rather than hanging it."""
    dropped = threading.Event()

    class Camera(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:
            pass

        def do_GET(self) -> None:
            if kind in RAW:
                with contextlib.suppress(OSError):
                    self.wfile.write(RAW[kind])
                    if kind != "truncated":
                        self.keep_sending()
                return
            self.send_response(200)
            if kind == "still":
                self.send_header("Content-Type", "image/jpeg")
                self.send_header("Content-Length", str(len(STILL_JPEG)))
                self.end_headers()
                self.wfile.write(STILL_JPEG)
                return
            boundary = "frame"
            mime = f"multipart/x-mixed-replace; boundary={boundary}"
            self.send_header("Content-Type", mime if kind == "mjpeg" else "image/jpeg")
            self.end_headers()
            until = time.monotonic() + opts.get("for_s", 60.0)
            try:
                while time.monotonic() < until:
                    if kind == "mjpeg":
                        head = f"--{boundary}\r\nContent-Type: image/jpeg\r\n\r\n".encode()
                        self.wfile.write(head + STILL_JPEG + b"\r\n")
                        time.sleep(0.02)
                    elif kind == "endless":
                        self.wfile.write(STILL_JPEG * 16)
                    else:
                        self.wfile.write(b"x")
                        self.wfile.flush()
                        time.sleep(opts.get("every_s", 0.02))
            except OSError:
                dropped.set()

        def keep_sending(self) -> None:
            until = time.monotonic() + opts.get("for_s", 60.0)
            more = {"redirect": b"<p>moved</p>" * 1000, "trailers": b"X-More: yes\r\n"}
            try:
                while time.monotonic() < until:
                    self.wfile.write(more.get(kind, b"x"))
                    if kind != "redirect":
                        time.sleep(0.05)
            except OSError:
                dropped.set()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Camera)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, name="fake-camera", daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}/still.jpg", dropped
    finally:
        server.shutdown()
        server.server_close()


#: What each hand-written camera in `_camera` sends first, before `keep_sending` goes on.
RAW = {
    "slow-headers": b"HTTP/1.1 200 OK\r\nContent-Type: image/jpeg\r\nX-Slow: ",
    "redirect": (
        b"HTTP/1.1 302 Found\r\nLocation: /elsewhere.jpg\r\nContent-Type: text/html\r\n\r\n"
    ),
    "trailers": (
        b"HTTP/1.1 200 OK\r\nContent-Type: image/jpeg\r\nTransfer-Encoding: chunked\r\n\r\n"
        b"1\r\nx\r\n0\r\n"
    ),
    "truncated": (
        b"HTTP/1.1 200 OK\r\nContent-Type: image/jpeg\r\nContent-Length: 100\r\n\r\n" + b"x" * 10
    ),
    "error": b"HTTP/1.1 503 Service Unavailable\r\nContent-Type: text/plain\r\n\r\n",
}


def test_a_still_is_fetched_whole() -> None:
    with _camera("still") as (url, _):
        assert fetch_still(url, deadline_s=3.0, max_bytes=SNAPSHOT_MAX_BYTES) == STILL_JPEG


def test_a_stream_is_refused_by_its_type_before_any_of_it_is_read() -> None:
    with _camera("mjpeg") as (url, dropped):
        started = time.monotonic()
        with pytest.raises(NotAStill) as refused:
            fetch_still(url, deadline_s=3.0, max_bytes=SNAPSHOT_MAX_BYTES)
        assert time.monotonic() - started < 2.0
        said = str(refused.value)
        assert "multipart/x-mixed-replace stream, not one still image" in said, said
        assert "a URL that returns one still image, a JPEG or a PNG" in said, said
        assert dropped.wait(5.0), "the stream is still being read"


def test_a_body_past_the_cap_is_refused_and_dropped() -> None:
    """An answer that says it is a JPEG and never ends: the cap ends it, long before the
    deadline would."""
    with _camera("endless") as (url, dropped):
        started = time.monotonic()
        with pytest.raises(NotAStill, match=r"more than 0\.2 MB for one picture"):
            fetch_still(url, deadline_s=10.0, max_bytes=200_000)
        assert time.monotonic() - started < 8.0
        assert dropped.wait(5.0), "the body is still being read"


def test_a_picture_still_arriving_at_its_deadline_is_given_up() -> None:
    """A byte every 20 ms passes every read's own timeout, so only the deadline on the whole
    fetch ends it. The camera stops after 6 s, so a fetch without that deadline ends there,
    with every byte it was sent."""
    with _camera("trickle", every_s=0.02, for_s=6.0) as (url, _):
        started = time.monotonic()
        with pytest.raises(StillTooSlow, match=r"no whole picture within 0\.5 s"):
            fetch_still(url, deadline_s=0.5, max_bytes=SNAPSHOT_MAX_BYTES)
        assert time.monotonic() - started < 4.0


def test_a_fetch_told_to_stop_stops_at_its_next_read() -> None:
    """What `close()` does to a fetch still reading in its thread. The camera stops after 6 s,
    and the deadline is longer, so a fetch that ignored the stop would read until then."""
    stop = threading.Event()
    with _camera("trickle", every_s=0.02, for_s=6.0) as (url, _):
        threading.Timer(0.3, stop.set).start()
        started = time.monotonic()
        with pytest.raises(ConnectionAbortedError, match="the transport closed"):
            fetch_still(url, deadline_s=30.0, max_bytes=SNAPSHOT_MAX_BYTES, stop=stop)
        assert time.monotonic() - started < 4.0


@pytest.mark.parametrize("kind", ["slow-headers", "trailers"])
def test_a_fetch_is_cut_off_at_its_deadline_wherever_it_is_reading(kind: str) -> None:
    """A header that never ends and a chunked body's trailer that never ends are read inside
    urllib and http.client, where no check between reads of the body can see them, and each
    of their bytes comes inside every read's own timeout. The guard shuts the socket down at the
    deadline all the same. The camera stops after 4 s, so a fetch it did not cut ends there."""
    with _camera(kind, for_s=4.0) as (url, _):
        started = time.monotonic()
        with pytest.raises(StillTooSlow, match=r"no whole picture within 0\.5 s"):
            fetch_still(url, deadline_s=0.5, max_bytes=SNAPSHOT_MAX_BYTES)
        assert time.monotonic() - started < 3.0


def test_a_stop_ends_a_fetch_still_reading_its_headers() -> None:
    stop = threading.Event()
    with _camera("slow-headers", for_s=4.0) as (url, _):
        threading.Timer(0.3, stop.set).start()
        started = time.monotonic()
        with pytest.raises(ConnectionAbortedError, match="the transport closed"):
            fetch_still(url, deadline_s=30.0, max_bytes=SNAPSHOT_MAX_BYTES, stop=stop)
        assert time.monotonic() - started < 3.0


def test_a_redirect_is_refused_without_reading_its_body() -> None:
    """urllib reads a redirect's whole body before it follows it, past every bound a fetch
    sets, so a redirect is refused, naming where it points, and its body is never read."""
    with _camera("redirect", for_s=6.0) as (url, dropped):
        started = time.monotonic()
        with pytest.raises(NotAStill) as refused:
            fetch_still(url, deadline_s=5.0, max_bytes=SNAPSHOT_MAX_BYTES)
        assert time.monotonic() - started < 3.0
        said = str(refused.value)
        assert "HTTP 302 Found, a redirect to http://127.0.0.1:" in said, said
        assert "/elsewhere.jpg, which quackd does not follow" in said, said
        assert dropped.wait(5.0), "the redirect's body is still being read"


def test_a_body_shorter_than_its_length_is_no_picture() -> None:
    with _camera("truncated") as (url, _), pytest.raises(http.client.IncompleteRead):
        fetch_still(url, deadline_s=3.0, max_bytes=SNAPSHOT_MAX_BYTES)


def test_an_error_page_that_trickles_is_read_no_longer_than_the_deadline() -> None:
    with _camera("error", for_s=4.0) as (url, _):
        started = time.monotonic()
        with pytest.raises(SnapshotRefused) as refused:
            fetch_still(url, deadline_s=0.5, max_bytes=SNAPSHOT_MAX_BYTES)
        assert refused.value.code == 503
        assert time.monotonic() - started < 3.0


def test_a_camera_that_streams_is_named_and_leaves_nothing_reading() -> None:
    """An MJPEG camera answers each request with a body that never ends. Read as a still, it was
    read for as long as it ran: past the run's end, by a thread the transport's close did not
    stop, which `asyncio.run` then waited on at exit. Now the connect names it and offers no
    verb that needs a picture, the close is prompt, and the run's loop ends."""
    said: dict[str, Any] = {}
    with _shim_on_its_own_loop() as fake, _camera("mjpeg") as (url, dropped):

        async def run() -> None:
            adapter = await _connected(fake, camera_url=url)
            said["camera"] = _transport(adapter).camera_working
            said["error"] = adapter.camera_error
            said["verbs"] = adapter.manifest.verb_names() if adapter.manifest else []
            started = time.monotonic()
            await adapter.disconnect()
            said["close_s"] = time.monotonic() - started

        runner = threading.Thread(target=asyncio.run, args=(run(),), daemon=True)
        runner.start()
        runner.join(20.0)
        assert not runner.is_alive(), "asyncio.run is still waiting on a camera fetch"
        assert said["camera"] is False
        assert "multipart/x-mixed-replace stream, not one still image" in said["error"], said
        assert not {"observe", "go_to", "search_scan", "approach_and"} & set(said["verbs"])
        assert said["close_s"] < 1.0, said
        assert dropped.wait(5.0), "the stream is still being read"


def test_a_close_stops_the_fetch_still_reading() -> None:
    """A camera that trickles, so each fetch reads until its 3 s deadline. A close in the middle
    of one ends it at its next read, rather than leaving it reading behind the close for the
    rest of its 3 s, with `asyncio.run` waiting on its thread at exit."""
    when: dict[str, float] = {}
    with (
        _shim_on_its_own_loop() as fake,
        _camera("trickle", every_s=0.02, for_s=30.0) as (url, dropped),
    ):

        async def run() -> None:
            camera = JsonRpcUnixTransport(fake.address, camera_url=url, camera_wait_s=0.3)
            adapter = HuebotAdapter(camera)
            await adapter.connect()
            assert camera.camera_working is False
            await asyncio.sleep(0.3)  # the first fetch is still reading
            await adapter.disconnect()
            when["closed"] = time.monotonic()

        runner = threading.Thread(target=asyncio.run, args=(run(),), daemon=True)
        runner.start()
        runner.join(20.0)
        assert not runner.is_alive(), "asyncio.run is still waiting on a camera fetch"
        exited = time.monotonic()
        assert "closed" in when, "the run did not get as far as its close"
        assert dropped.wait(1.0), "the fetch read on behind the close"
        assert exited - when["closed"] < 1.5, "asyncio.run waited on a fetch's thread"


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
