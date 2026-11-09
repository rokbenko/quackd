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
import functools
import itertools
import time
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
from quackd_huebot.body import HUEBOT, LOOK_REFUSED, NO_ADDRESS
from quackd_huebot.verbs import SKILLS_OF, STAND_UP_POLL_S, STAND_UP_WAIT_S
from quackd_microduck.transports import jsonrpc_unix
from quackd_microduck.transports.jsonrpc_unix import JsonRpcUnixTransport
from tests.fake_robotd import (
    ALL_SKILLS,
    CURRENT_API,
    DOWN_ON_THE_FLOOR,
    FakeShim,
    on_its_own_loop,
)

Shim = functools.partial(JsonRpcUnixTransport, body=HUEBOT)
"""The family's transport, on Huebot's body, as `make` builds it."""

#: Every verb, for the tests that run them through an executor and its allowlist.
EVERYTHING = parse_duck_text(
    "---\nduck: 0\nname: t\ndescription: d\nverbs:\n"
    "  allow: [walk, go_to, search_scan, kick, sit, stand, stand_up, quack, say, observe]\n"
    "success: [x]\n---\n# Task\nx\n"
)


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
    assert HuebotAdapter(Shim("tcp://127.0.0.1:1")).head_fade_s == 0.0
    assert HuebotAdapter(Shim("tcp://127.0.0.1:1")).frames_upright is False


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
    t = Shim(address)
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


# a margin on a wall clock, half the deadman between two feeds: in CI's parallel stage a loaded
# macOS runner put 0.28 s between two of them (main's run on 49db6a2e, as on 2026-10-28)
@pytest.mark.serial
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
    # A close with a twist still unread can arrive as a reset and not as an end of file, and a
    # write can meet it before the reader does. Each says what it saw, and each is the link gone.
    gone = (
        "the shim closed the connection",
        "the link to the shim failed",
        "the link to the shim is gone",
    )
    assert not result.ok and any(said in result.summary for said in gone), result.summary
    assert t.stop_error is not None and any(said in t.stop_error for said in gone), t.stop_error
    last_move = max(at for at, m in shim.arrived if m == "robot.move")
    await asyncio.sleep(max(0.0, last_move + up.DEADMAN_S + 0.1 - time.monotonic()))
    assert shim.driving() == [0.0, 0.0, 0.0], "the deadman never fired"
    assert shim.twist == [0.1, 0.0, 0.0], "nothing from quackd zeroed it: the deadman did"
    await t.stop()  # never raises on a dead link
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


async def test_stand_up_stops_waiting_once_a_safety_flag_cannot_be_read() -> None:
    """A flag the shim stops sending as a boolean while `stand_up` waits ends the wait at once,
    as a robot somebody picks up does, rather than leaving it to judge for 10 s legs nobody can
    vouch for."""
    fake = FakeShim()
    fake.safety_block = dict(DOWN_ON_THE_FLOOR)
    await fake.start()
    try:
        adapter = await _connected(fake)

        async def unreadable_once_enabled() -> None:
            for _ in range(500):
                if "robot.enable" in fake.methods():
                    fake.safety_block = {**DOWN_ON_THE_FLOOR, "limp": "true"}
                    return
                await asyncio.sleep(0.02)

        garbling = asyncio.create_task(unreadable_once_enabled())
        started = time.monotonic()
        result = await _executor(adapter).run_verb("stand_up", {})
        await garbling
        assert not result.ok and 'safety.limp is "true"' in result.summary, result.summary
        assert time.monotonic() - started < 5.0, "the wait ran on"
        await adapter.disconnect()
    finally:
        await fake.stop()


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


def test_a_run_with_no_camera_drives_the_shim_without_a_verb_that_needs_one(
    tmp_path: Path,
) -> None:
    """`quackd run` on huebot:jsonrpc against the fake shim, with no --camera-url: the pilot is
    offered nothing that needs a picture, quacks, walks and quacks, and the shim is sent nothing
    it does not answer."""
    with on_its_own_loop(FakeShim) as fake:
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


class _SlowLink(_AfterEnable):
    """Each reading takes a second of the robot's own time, as over a link that holds lines up."""

    async def get_state(self) -> DuckState:
        self._t += 1.0
        return await super().get_state()


async def test_stand_up_waits_by_the_clock_and_not_by_its_polls() -> None:
    """A wait counted in polls ran four times as long as it said over this link: forty polls of a
    quarter second each, every one a second slower. Timed by the transport's clock it is ten
    seconds, as it says, after eight."""
    robot = _SlowLink(DOWN)
    result = await _stand_up_on(robot)
    assert not result.ok and "10 s later" in result.summary, result.summary
    assert robot.sleeps == [STAND_UP_POLL_S] * 8


async def test_a_connected_huebot_waits_for_no_glance_to_fade(shim: FakeShim) -> None:
    """robotd glances on its own from API 41 and a Microduck's verbs wait a glance out once they
    centre its head. A Huebot is sent no look, so its shim speaking 41 makes no wait."""
    adapter = await _connected(shim)
    assert _transport(adapter).answered is not None
    assert _transport(adapter).head_fade_s == 0.0 and adapter.head_fade_s == 0.0
    await adapter.disconnect()


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
