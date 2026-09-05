"""The Open Duck Mini v2 adapter: a duck that walks and looks, and never pretends otherwise.

The verbs this robot lacks are the point of the file. It has no beak, no kick policy, no sit
policy and no way back up after a fall, so `kick`, `grab`, `sit`, `stand` and `stand_up` must
be absent from the manifest, from the registry, from `.duck` validation and from the prompt.
"""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from quackd.adapters.base import RobotAdapter
from quackd.adapters.factory import make_adapter, parse_robot_spec
from quackd.adapters.open_duck import (
    OpenDuckAdapter,
    conditions,
    describe,
    implementations,
    open_duck_manifest,
)
from quackd.adapters.open_duck.mock import OpenDuckMock
from quackd.adapters.open_duck.sim2d import OpenDuckSim2D
from quackd.adapters.open_duck.verbs import GAZE_YAW_DEG, MAX_VX, MAX_VY, MAX_WZ, mood_for
from quackd.agent.prompts import build_system_prompt
from quackd.cli import app
from quackd.duckfile.parser import load_duck, parse_duck_text
from quackd.duckfile.validate import validate_duck
from quackd.perception.color_blob import ColorBlobDetector
from quackd.safety import Executor, allow_all
from quackd.transport.base import Intent
from quackd.verbs.core import scan_mode
from quackd.verbs.registry import VerbNotFound, registry_from_manifest

runner = CliRunner()

OPEN_DUCK_VERBS = {
    "report_state",
    "stop",
    "move",
    "observe",
    "go_to",
    "search_scan",
    "approach_and",
    "say",
    "quack",
    "gaze",
    "express",
}
#: Verbs this body cannot do. Some are the Microduck's, some are other robots'.
GATED = {
    "kick",
    "grab",
    "sit",
    "stand",
    "stand_up",
    "wake_up",
    "play_sound",
    "move_joints",
    "gripper",
    "pick",
    "place",
}
#: Every skill the shared cartoon duck understands and this robot does not.
NOT_THIS_DUCK = ("kick_left", "kick_right", "ground_pick", "sit_toggle", "roulade")

DUCK = parse_duck_text(
    "---\nduck: 1\nname: t\ndescription: d\nrequires: [observe, move, say]\nverbs:\n"
    "  allow: [observe, search_scan, go_to, move, gaze, express, say, quack, "
    "report_state, stop]\nsuccess: [x]\n---\n# Task\nx\n"
)


def _executor(adapter: OpenDuckAdapter, manifest: object) -> Executor:
    return Executor(
        registry_from_manifest(manifest, adapter),  # type: ignore[arg-type]
        adapter,
        contract=DUCK.frontmatter,
        detector=ColorBlobDetector(),
        confirm=allow_all,
    )


# ── the manifest ────────────────────────────────────────────────────────────────────────


def test_manifest_is_a_slow_biped_that_cannot_kick() -> None:
    m = open_duck_manifest("mock")
    assert m.vendor == "apirrone" and m.model == "open-duck-mini-v2"
    assert m.embodiment == "biped" and m.mobility == "legged"
    assert set(m.verb_names()) == OPEN_DUCK_VERBS
    assert not [v for v in GATED if m.provides(v)]
    # the aliases an older .duck still spells resolve, because their canonical verbs exist
    assert m.provides("walk") and m.provides("walk_to") and m.provides("get_frame")
    assert m.limits == {
        "max_vx": MAX_VX,
        "max_vy": MAX_VY,
        "max_wz": MAX_WZ,
        "gaze_yaw_deg": round(GAZE_YAW_DEG, 1),
    }
    # the deadman is quackd's own bridge on the robot, so it is real but it is not native
    assert m.safety_authority.native == "none" and m.safety_authority.deadman
    assert m.extras["speech"] == "sounds"
    assert "get-up" in m.extras["no_recovery"] and "battery" in m.extras["no_battery"]


def test_the_same_duck_over_sim2d_and_mock_has_the_same_capabilities() -> None:
    assert open_duck_manifest("sim2d").digest() == open_duck_manifest("mock").digest()
    assert describe("sim2d").digest() == describe("mock").digest()


def test_a_duck_built_without_a_camera_or_a_speaker_loses_exactly_those_verbs() -> None:
    """`REQUIREMENTS` does the gating: no branching of ours decides this."""
    blind = open_duck_manifest("mock", camera=False)
    assert not [v for v in ("observe", "go_to", "search_scan", "approach_and") if blind.provides(v)]
    assert blind.provides("move") and blind.provides("say") and blind.provides("gaze")
    assert "camera" not in blind.sensors

    mute = open_duck_manifest("mock", speaker=False)
    assert not mute.provides("say") and not mute.provides("quack")
    assert "sound" not in mute.intents and mute.provides("observe")

    bare = open_duck_manifest("mock", camera=False, speaker=False, antennas=False, head=False)
    assert set(bare.verb_names()) == {"report_state", "stop", "move"}


def test_search_scan_turns_the_body_because_this_duck_can_walk() -> None:
    """A legged robot with a head still scans by turning. Pinned so it stays a decision."""
    assert scan_mode(open_duck_manifest("mock")) == "turn"


def test_the_registry_and_the_prompt_never_offer_a_verb_this_body_lacks() -> None:
    registry = registry_from_manifest(
        open_duck_manifest("mock"), implementations=implementations(), conditions=conditions()
    )
    assert set(registry.names()) == OPEN_DUCK_VERBS
    for gone in GATED:
        assert gone not in registry
        with pytest.raises(VerbNotFound):
            registry.get(gone)
    allow = DUCK.frontmatter.verbs.allow
    prompt = build_system_prompt(
        DUCK, [registry.view(n) for n in allow], "mock", manifest=open_duck_manifest("mock")
    )
    for gone in ("kick", "grab", "stand_up"):
        assert gone not in prompt


# ── the mock backend ────────────────────────────────────────────────────────────────────


async def test_mock_backend_walks_looks_chirps_and_refuses_every_duck_skill() -> None:
    adapter = OpenDuckAdapter(OpenDuckMock())
    assert isinstance(adapter, RobotAdapter)
    manifest = await adapter.connect()
    assert manifest.backend == "mock"
    ex = _executor(adapter, manifest)
    mock = adapter.transport
    assert isinstance(mock, OpenDuckMock)

    assert (await ex.run_verb("report_state", {})).ok
    walked = await ex.run_verb("move", {"vx": 0.3, "duration_s": 0.3})
    assert walked.ok and "clamped" in walked.summary
    assert mock.intents_of("move")[0].params["vx"] == pytest.approx(MAX_VX)

    assert (await ex.run_verb("gaze", {"direction": "left"})).ok and mock.head_yaw_deg > 0
    said = await ex.run_verb("say", {"text": "hello there!"})
    assert said.ok and said.data["mood"] == "greet"
    assert (await ex.run_verb("quack", {})).ok
    assert mock.sounds == [("greet", "hello there!"), ("chirp", None)]
    assert (await ex.run_verb("express", {"gesture": "wiggle"})).ok
    assert mock.gestures == ["wiggle"]

    # the manifest hides these verbs; the backend refuses the intents too
    for skill in NOT_THIS_DUCK:
        assert not (await adapter.send_intent(Intent.do(skill))).accepted
    assert not (await adapter.send_intent(Intent.enable(False))).accepted

    state = await adapter.get_state()
    assert state.battery_percent is None
    health = await adapter.health()
    assert health.ok and health.battery_percent is None


async def test_a_fallen_duck_is_told_to_wait_for_a_human() -> None:
    """There is no get-up policy for this robot, so the refusal must not name a verb."""
    adapter = OpenDuckAdapter(OpenDuckMock(fallen=True))
    manifest = await adapter.connect()
    ex = _executor(adapter, manifest)
    for verb, params in (("move", {"vx": 0.1, "duration_s": 0.2}), ("gaze", {})):
        result = await ex.run_verb(verb, params)
        assert not result.ok
        assert "by hand" in result.summary and "stand_up" not in result.summary
    assert (await ex.run_verb("stop", {})).ok  # stop is never gated


async def test_a_paused_walk_policy_refuses_to_walk() -> None:
    adapter = OpenDuckAdapter(OpenDuckMock(policy_running=False))
    manifest = await adapter.connect()
    ex = _executor(adapter, manifest)
    result = await ex.run_verb("move", {"vx": 0.1, "duration_s": 0.2})
    assert not result.ok and "paused" in result.summary
    assert (await ex.run_verb("gaze", {})).ok  # the head does not need the walk policy


async def test_gaze_refuses_an_angle_this_neck_cannot_reach() -> None:
    adapter = OpenDuckAdapter(OpenDuckMock())
    ex = _executor(adapter, await adapter.connect())
    result = await ex.run_verb("gaze", {"bearing_deg": 80})
    assert not result.ok and "bearing_deg" in result.summary


async def test_the_mock_finds_the_ball_by_turning_and_walks_to_it() -> None:
    adapter = OpenDuckAdapter(OpenDuckMock(ball_xy=(0.2, 1.2)))
    ex = _executor(adapter, await adapter.connect())
    found = await ex.run_verb("search_scan", {"target": "ball", "step_deg": 30, "max_steps": 12})
    assert found.ok, found.summary
    assert (await ex.run_verb("go_to", {"target": "ball", "stop_distance": 0.4})).ok


# ── the sim2d backend ───────────────────────────────────────────────────────────────────


async def test_sim2d_reports_no_battery_and_no_kick_telemetry() -> None:
    adapter = make_adapter("open_duck:sim2d", seed=0)
    await adapter.connect()
    state = await adapter.get_state()
    assert state.battery_percent is None
    assert not [k for k in ("kicks", "last_kick_ball_moved_m", "holding") if k in state.extras]
    assert state.extras["policy_running"] is True
    await adapter.disconnect()


async def test_sim2d_refuses_the_skills_this_duck_does_not_have() -> None:
    transport = OpenDuckSim2D(0)
    await transport.connect()
    for skill in NOT_THIS_DUCK:
        ack = await transport.send_intent(Intent.do(skill))
        assert not ack.accepted and "cannot" in (ack.reason or "")
    assert (await transport.send_intent(Intent.do("antennas:perk"))).accepted
    assert transport.gestures == ["perk"]
    assert not (await transport.send_intent(Intent.do("antennas:moonwalk"))).accepted
    await transport.close()


@pytest.mark.parametrize("seed", range(4))
async def test_sim2d_walks_to_the_ball(seed: int) -> None:
    adapter = make_adapter("open_duck:sim2d", seed=seed)
    manifest = await adapter.connect()
    ex = _executor(adapter, manifest)  # type: ignore[arg-type]
    await ex.run_verb("search_scan", {"target": "ball", "step_deg": 30, "max_steps": 12})
    await ex.run_verb("go_to", {"target": "ball", "stop_distance": 0.35})
    world = adapter.world  # type: ignore[attr-defined]
    duck, ball = world.ducks[0], world.ball
    assert ((duck.x - ball.x) ** 2 + (duck.y - ball.y) ** 2) ** 0.5 < 0.6
    await adapter.disconnect()


# ── the contract ────────────────────────────────────────────────────────────────────────


def test_a_kick_task_is_refused_against_this_duck_with_the_validator_s_words() -> None:
    problems = validate_duck(load_duck("find-and-kick"), [describe("mock", "open-duck-01")])
    assert any(
        p.message == "requires kick, but open-duck-01 (open-duck-mini-v2) does not provide it"
        for p in problems
    ), [p.message for p in problems]
    cli = runner.invoke(app, ["validate", "ducks/find-and-kick.duck", "--robot", "open_duck:mock"])
    assert cli.exit_code == 1 and "does not provide it" in cli.output


def test_list_verbs_shows_the_real_set() -> None:
    result = runner.invoke(app, ["list-verbs", "--robot", "open_duck:sim2d"])
    assert result.exit_code == 0
    for name in ("move", "gaze", "quack", "say"):
        assert name in result.output
    for gone in ("kick", "grab", "stand_up"):
        assert gone not in result.output


def test_mood_mapping_is_stable() -> None:
    assert mood_for("Hello there") == "greet"
    assert mood_for("where is the ball?") == "inquire"
    assert mood_for("careful, a person") == "alert"
    assert mood_for("found it, well done") == "happy"
    assert mood_for("oh no, lost it") == "sad"
    assert mood_for(None) == "chirp"


def test_the_factory_makes_every_backend() -> None:
    for backend in ("sim2d", "mock", "bridge"):
        adapter = make_adapter(parse_robot_spec(f"open_duck:{backend}"))
        assert adapter.name == "open_duck" and adapter.backend == backend
    with pytest.raises(ValueError, match="unknown open_duck backend"):
        from quackd.adapters.open_duck import make

        make("mujoco")


def test_the_bring_up_task_does_not_require_the_dangerous_flag() -> None:
    """`open-duck-lookout` is what you point at a real duck first. Head control is off by
    default and is the one thing that can damage this robot, so the task must not need it."""
    lookout = load_duck("open-duck-lookout")
    assert "gaze" not in lookout.frontmatter.requires
    assert "gaze" in lookout.frontmatter.verbs.allow, "still used when the duck has a head"
    headless = open_duck_manifest("bridge", head=False)
    assert not headless.provides("gaze")
    assert not [v for v in lookout.frontmatter.requires if not headless.provides(v)]


async def test_a_fall_blind_robot_asks_the_human_once_before_it_walks() -> None:
    """Not a precondition. On the bridge backend `fall_detection` is a constant False — the
    IMU has one owner and it is upstream's loop — so refusing per verb would refuse every
    locomotion verb forever and decommission the robot. Ask the person in the room once,
    before a leg moves, and let them say no."""
    from quackd.adapters.open_duck.mock import OpenDuckMock
    from quackd.agent.loop import AgentLoop, RunConfig
    from quackd.agent.providers.fake import FakeProvider
    from quackd.duckfile.parser import parse_duck_text
    from quackd.safety import Aborted

    duck = parse_duck_text(
        "---\nduck: 1\nname: t\ndescription: d\nrequires: [move]\nverbs:\n"
        "  allow: [move, report_state, stop]\nsuccess: [x]\n---\n# Task\nx\n"
    )

    class Blind(OpenDuckMock):
        async def get_state(self):  # type: ignore[no-untyped-def]
            state = await super().get_state()
            return state.model_copy(update={"extras": {**state.extras, "fall_detection": False}})

    asked: list[str] = []
    adapter = OpenDuckAdapter(Blind())
    with pytest.raises(Aborted, match="watching"):
        await AgentLoop(
            RunConfig(
                duck=duck,
                provider=FakeProvider(),
                transport=adapter,
                acknowledge=lambda why: (asked.append(why), False)[1],
            )
        ).run()
    assert asked and "no way to get up" in asked[0]
