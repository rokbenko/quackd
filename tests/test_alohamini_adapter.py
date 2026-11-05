"""The AlohaMini adapter: two arms on a lift, and three honest refusals.

What this body cannot do is most of what the manifest is for. It has no speaker and no head,
so `say` and `gaze` do not exist. It reports no pose, so `go_to` closes on the camera alone.
And as shipped its arms have no torque at all, so the arm verbs refuse rather than command
something that would move nothing.
"""

from __future__ import annotations

import sys

import pytest
from typer.testing import CliRunner

from quackd.adapters.base import AdapterNotInstalled, RobotAdapter
from quackd.adapters.factory import make_adapter, parse_robot_spec
from quackd.agent.prompts import build_system_prompt
from quackd.cli import app
from quackd.duckfile.parser import load_duck
from quackd.duckfile.validate import validate_duck
from quackd.perception.color_blob import ColorBlobDetector
from quackd.safety import ConfirmDenied, Executor, VerbNotAllowed, allow_all
from quackd.transport.base import Intent
from quackd.verbs.core import scan_mode
from quackd.verbs.registry import VerbNotFound, registry_from_manifest
from quackd_alohamini import (
    MAX_VX,
    MAX_VY,
    MAX_WZ,
    AlohaMiniAdapter,
    alohamini_manifest,
    conditions,
    describe,
    implementations,
    parse_model,
)
from quackd_alohamini.mock import AlohaMiniMock
from quackd_alohamini.sim2d import AlohaMiniSim2D
from quackd_alohamini.verbs import (
    GRIPPER_CLOSED,
    JOINTS_5DOF,
    JOINTS_6DOF,
    LIFT_MAX_MM,
    LIFT_MIN_MM,
    MoveJointsParams,
    joints_for,
    model_from_keys,
)

runner = CliRunner()


#: Wide enough that rich never elides a verb name into `report_sta…`, which would make the
#: absence checks below weaker than they look.
WIDE = {"COLUMNS": "200"}


def _verb_column(output: str) -> set[str]:
    """The names in the rendered table's first column.

    Searching the whole output is wrong: `approach_and`'s own description names `kick` and
    `grab` as example follow-up verbs, so a substring check finds verbs that are not there.
    """
    names = {
        line.split(chr(9474))[1].strip()
        for line in output.splitlines()
        if line.count(chr(9474)) > 2
    }
    assert not any(chr(8230) in n for n in names), f"rendered too narrow, names elided: {names}"
    return names


ALOHAMINI_VERBS = {
    "report_state",
    "stop",
    "move",
    "lift",
    "move_joints",
    "gripper",
    "home_arms",
    "observe",
    "go_to",
    "search_scan",
    "approach_and",
}
#: Verbs other robots have and this body has not. No speaker, no head, no legs.
ABSENT = {"say", "quack", "gaze", "express", "play_sound", "wake_up", "sit", "stand", "kick"}

DUCK = load_duck("alohamini-lookout")


def _executor(adapter: AlohaMiniAdapter, manifest: object) -> Executor:
    return Executor(
        registry_from_manifest(manifest, adapter),  # type: ignore[arg-type]
        adapter,
        contract=None,
        detector=ColorBlobDetector(),
        confirm=allow_all,
    )


# ── the manifest ────────────────────────────────────────────────────────────────────────


def test_manifest_is_a_wheeled_body_with_two_arms_and_a_lift() -> None:
    m = alohamini_manifest("mock", camera=True)
    assert (m.embodiment, m.mobility) == ("wheeled", "wheeled")
    assert set(m.intents) == {"twist", "pose", "joint", "gripper"}
    assert "sound" not in m.intents, "there is no speaker anywhere in the driver"
    assert "gaze" not in m.intents, "there is no head"
    assert set(m.verb_names()) == ALOHAMINI_VERBS
    assert not (set(m.verb_names()) & ABSENT)
    # nothing reports a battery, so a battery abort could never fire
    assert set(m.sensors) == {"joint_state", "camera"}
    assert "odometry" not in m.sensors, "velocities only, never a pose"


def test_limits_stay_inside_the_schema_because_limits_may_only_narrow() -> None:
    from quackd.verbs.core import MAX_VX as SCHEMA_VX
    from quackd.verbs.core import MAX_VY as SCHEMA_VY
    from quackd.verbs.core import MAX_WZ as SCHEMA_WZ

    m = alohamini_manifest("mock", camera=True)
    assert m.limits["max_vx"] <= SCHEMA_VX
    assert m.limits["max_vy"] <= SCHEMA_VY
    assert m.limits["max_wz"] <= SCHEMA_WZ
    # the robot's own fast tier is 0.25 m/s, under the schema; on vy the schema binds instead
    assert (MAX_VX, MAX_VY, MAX_WZ) == (0.25, 0.2, 1.30)
    assert m.limits["lift_min_mm"] == LIFT_MIN_MM and m.limits["lift_max_mm"] == LIFT_MAX_MM


def test_safety_authority_says_the_deadman_misses_the_arms() -> None:
    """The host's watchdog calls stop_motion, which is the base and the lift and nothing else."""
    m = alohamini_manifest("mock", camera=True)
    assert m.safety_authority.native == "none"
    assert m.safety_authority.deadman is True
    assert m.extras["deadman_scope"] == "base_and_lift_only"
    assert m.extras["watchdog_ms"] == 1000
    # one proportional lift step per command, so the command rate is the control rate
    assert m.safety_authority.heartbeat_hz == 10.0


def test_extras_record_every_assumption_rather_than_hiding_it() -> None:
    m = alohamini_manifest("mock", camera=True)
    assumptions = " ".join(m.extras["assumptions"]).lower()
    for expected in ("sign", "holding", "odometry", "lift"):
        assert expected in assumptions
    assert m.extras["speech"] == "none"


def test_digest_ignores_the_id_and_the_backend() -> None:
    a = alohamini_manifest("mock", "one", camera=True)
    b = alohamini_manifest("zmq", "two", camera=True)
    assert a.digest() == b.digest()
    assert a.digest() != alohamini_manifest("mock", "one", camera=False).digest()


def test_a_host_with_no_cameras_loses_exactly_the_verbs_that_need_one() -> None:
    blind = alohamini_manifest("zmq", camera=False)
    assert "camera" not in blind.sensors
    for verb in ("observe", "go_to", "search_scan", "approach_and"):
        assert not blind.provides(verb)
        assert verb not in blind.preconditions


def test_a_no_follower_host_is_a_base_and_a_lift_and_says_so() -> None:
    """`--no_follower` runs the base and the lift alone. The arm verbs must not exist, and the
    joint and gripper intents must not be claimed either."""
    m = alohamini_manifest("zmq", camera=True, arms=False)
    for verb in ("move_joints", "gripper", "home_arms"):
        assert not m.provides(verb)
    assert set(m.intents) == {"twist", "pose"}
    assert m.provides("lift") and m.provides("move")
    assert m.extras["joints"] == []


def test_search_scan_turns_the_body_because_there_is_no_head() -> None:
    assert scan_mode(alohamini_manifest("mock", camera=True)) == "turn"


# ── the SKU comes off the wire, never from config ──────────────────────────────────────


@pytest.mark.parametrize(
    ("model", "per_arm"), [("alohamini1", JOINTS_5DOF), ("alohamini2", JOINTS_6DOF)]
)
def test_each_sku_has_its_own_joint_set(model: str, per_arm: tuple[str, ...]) -> None:
    joints = joints_for(model)
    assert len(joints) == len(per_arm) * 2
    assert all(j.startswith(("arm_left_", "arm_right_")) for j in joints)
    assert ("arm_left_wrist_yaw" in joints) is (model != "alohamini1")


def test_the_sku_is_read_from_the_keys_not_guessed() -> None:
    """The host defaults to alohamini2 and upstream's own client to alohamini1, with nothing
    cross-checking them, so trusting config would silently zero-fill two joints."""
    assert model_from_keys({"arm_left_wrist_yaw.pos": 0.0}) == "alohamini2"
    assert model_from_keys({"arm_left_wrist_roll.pos": 0.0}) == "alohamini1"
    assert model_from_keys(()) == "alohamini1"


def test_joint_goals_are_normalised_and_not_degrees() -> None:
    """`use_degrees` is False upstream, so a joint goal is a normalised -100..100 number and
    reading it as degrees would make every one of them mean something else.

    The old version of this test asserted that a one-entry dict was truthy and an empty one
    raised, which is true of any model and says nothing about units at all.
    """
    manifest = alohamini_manifest("zmq", camera=True, arms=True)
    assert manifest.limits["joint_norm"] == 100.0
    assert "joint_deg" not in manifest.limits, "that is the LeRobot arm's contract, not this one"

    # and the value reaches the wire unscaled: no degree conversion anywhere on this path
    goal = 42.5
    params = MoveJointsParams(arm="left", positions={"shoulder_pan": goal})
    assert params.positions["shoulder_pan"] == pytest.approx(goal)

    with pytest.raises(ValueError, match="at least one joint"):
        MoveJointsParams(arm="left", positions={})


# ── the registry, the prompt and .duck validation all agree ────────────────────────────


def test_an_absent_verb_is_absent_everywhere() -> None:
    m = alohamini_manifest("mock", camera=True)
    registry = registry_from_manifest(m, implementations=implementations(), conditions=conditions())
    allow = DUCK.frontmatter.verbs.allow
    prompt = build_system_prompt(DUCK, [registry.view(n) for n in allow], "mock", manifest=m)
    offered = {line.split("`")[1] for line in prompt.splitlines() if line.startswith("- `")}
    for verb in ABSENT:
        with pytest.raises(VerbNotFound):
            registry.get(verb)
        assert verb not in offered
    assert offered == set(allow)


def test_the_shipped_lookout_task_validates_and_moves_nothing() -> None:
    assert validate_duck(DUCK, [alohamini_manifest("sim2d", camera=True)]) == []
    allowed = set(DUCK.frontmatter.verbs.allow)
    assert not (allowed & {"move", "lift", "move_joints", "go_to", "approach_and", "search_scan"})


def test_the_cli_refuses_a_task_this_robot_cannot_do() -> None:
    result = runner.invoke(
        app, ["validate", "ducks/open-duck-scout.duck", "--robot", "alohamini:mock"]
    )
    assert result.exit_code != 0


# ── the mock backend, through a real executor ──────────────────────────────────────────


async def test_every_verb_runs_offline_through_the_executor() -> None:
    adapter = AlohaMiniAdapter(AlohaMiniMock())
    assert isinstance(adapter, RobotAdapter)
    manifest = await adapter.connect()
    ex = _executor(adapter, manifest)
    mock = adapter.transport
    assert isinstance(mock, AlohaMiniMock)

    assert (await ex.run_verb("report_state", {})).ok
    assert (await ex.run_verb("observe", {})).ok
    assert (await ex.run_verb("move", {"vx": 0.2, "vy": 0.1, "duration_s": 0.4})).ok

    moved = await ex.run_verb(
        "move_joints", {"arm": "left", "positions": {"elbow_flex": 30.0}, "duration_s": 0.3}
    )
    assert moved.ok and mock.joints["arm_left_elbow_flex"] == pytest.approx(30.0)
    assert moved.data["base_stopped"] is True

    closed = await ex.run_verb("gripper", {"side": "left", "open": False})
    assert closed.ok and mock.joints["arm_left_gripper"] == pytest.approx(GRIPPER_CLOSED)
    assert mock.holding == {"left": True, "right": False}

    lifted = await ex.run_verb("lift", {"height_mm": 300.0})
    assert lifted.ok and abs(mock.lift_height_mm - 300.0) <= 3.0

    assert (await ex.run_verb("home_arms", {})).ok
    assert (await ex.run_verb("stop", {})).ok
    assert (await adapter.get_state()).battery_percent is None


async def test_the_mock_refuses_what_this_body_cannot_do() -> None:
    adapter = AlohaMiniAdapter(AlohaMiniMock())
    await adapter.connect()
    assert not (await adapter.send_intent(Intent.sound("chirp"))).accepted
    assert not (await adapter.send_intent(Intent.look(1.0, 0.0, 0.0))).accepted
    assert not (await adapter.send_intent(Intent.do("kick_left"))).accepted
    assert not (await adapter.send_intent(Intent.enable(False))).accepted


async def test_homing_leaves_the_lift_travelling_and_a_stop_is_what_ends_it() -> None:
    """upstream's zeroing write after home() is commented out, so the register holds full
    speed descent. Nothing but an explicit zero clears it."""
    mock = AlohaMiniMock()
    adapter = AlohaMiniAdapter(mock)
    await adapter.connect()
    assert mock.lift_velocity < 0, "the lift is descending the moment quackd arrives"
    await adapter.stop()
    assert mock.lift_velocity == 0


async def test_the_watchdog_stops_the_base_and_the_lift_and_leaves_the_arms() -> None:
    mock = AlohaMiniMock()
    adapter = AlohaMiniAdapter(mock)
    await adapter.connect()
    await adapter.send_intent(Intent.joint({"arm_left_shoulder_pan": 20.0}, 0.2))
    await adapter.send_intent(Intent.move(vx=0.2))
    await mock.sleep(1.5)  # longer than the host's 1 s window
    assert (mock.vx, mock.vy, mock.wz) == (0.0, 0.0, 0.0)
    assert mock.lift_velocity == 0
    assert mock.joints["arm_left_shoulder_pan"] == pytest.approx(20.0)


# ── the arms, which are limp unless quackd's own host is running ───────────────────────


async def test_a_stock_host_refuses_the_arm_verbs_with_a_reason_that_names_the_fix() -> None:
    adapter = AlohaMiniAdapter(AlohaMiniMock(arm_torque=False))
    manifest = await adapter.connect()
    ex = _executor(adapter, manifest)
    for verb, params in (
        ("move_joints", {"arm": "left", "positions": {"elbow_flex": 10.0}}),
        ("gripper", {"side": "left", "open": True}),
        ("home_arms", {}),
    ):
        result = await ex.run_verb(verb, params)
        assert not result.ok
        assert "torque" in result.summary and "host wrapper" in result.summary
    # the base, the lift and stop are unaffected
    assert (await ex.run_verb("move", {"vx": 0.1, "duration_s": 0.2})).ok
    assert (await ex.run_verb("stop", {})).ok


async def test_an_uncalibrated_robot_refuses_to_move_and_says_why() -> None:
    adapter = AlohaMiniAdapter(AlohaMiniMock(calibrated=False))
    manifest = await adapter.connect()
    ex = _executor(adapter, manifest)
    result = await ex.run_verb("move", {"vx": 0.1, "duration_s": 0.2})
    assert not result.ok and "not calibrated" in result.summary
    assert (await ex.run_verb("stop", {})).ok, "stop is never gated"


async def test_a_silent_host_refuses_every_moving_verb_but_never_stop() -> None:
    mock = AlohaMiniMock()
    adapter = AlohaMiniAdapter(mock)
    manifest = await adapter.connect()
    ex = _executor(adapter, manifest)
    mock.stale_ms = 5000.0
    result = await ex.run_verb("move", {"vx": 0.1, "duration_s": 0.2})
    assert not result.ok and "not answering" in result.summary
    assert (await ex.run_verb("stop", {})).ok
    assert (await ex.run_verb("report_state", {})).ok


# ── the simulator ──────────────────────────────────────────────────────────────────────


async def test_the_simulated_robot_drives_lifts_and_sees() -> None:
    adapter = AlohaMiniAdapter(AlohaMiniSim2D(seed=3))
    manifest = await adapter.connect()
    ex = _executor(adapter, manifest)
    sim = adapter.transport
    assert isinstance(sim, AlohaMiniSim2D)

    assert (await ex.run_verb("observe", {})).ok
    assert (await ex.run_verb("move", {"vx": 0.2, "duration_s": 0.5})).ok
    lifted = await ex.run_verb("lift", {"height_mm": 250.0})
    assert lifted.ok and abs(sim.lift_height_mm - 250.0) <= 3.0
    assert (await ex.run_verb("gripper", {"side": "both", "open": False})).ok
    assert sim.holding == {"left": True, "right": True}
    assert (await ex.run_verb("stop", {})).ok


async def test_the_simulator_refuses_the_intents_this_body_has_not_got() -> None:
    """A bug that somehow sent one should fail loudly in sim, not only on hardware."""
    sim = AlohaMiniSim2D(seed=1)
    await sim.connect()
    for intent in (Intent.sound("chirp"), Intent.look(1.0, 0.0, 0.0), Intent.do("kick_left")):
        assert not (await sim.send_intent(intent)).accepted


# ── the factory ────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("backend", ["mock", "sim2d", "zmq"])
def test_the_factory_builds_every_backend(backend: str) -> None:
    adapter = make_adapter(f"alohamini:{backend}")
    assert adapter.name == "alohamini" and adapter.backend == backend


def test_an_unknown_backend_names_the_real_ones() -> None:
    from quackd_alohamini import make

    with pytest.raises(ValueError, match="unknown alohamini backend"):
        make("serial")


async def test_the_zmq_backend_without_the_extra_names_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(sys.modules, "zmq", None)
    adapter = make_adapter("alohamini:zmq", address="tcp://robot.local:5555")
    with pytest.raises(AdapterNotInstalled, match=r"quackd\[alohamini\]"):
        await adapter.connect()


def test_the_static_manifest_claims_nothing_the_real_host_has_not_shown() -> None:
    assert describe(parse_robot_spec("alohamini:mock").backend).provides("observe")
    assert not describe(parse_robot_spec("alohamini:zmq").backend).provides("observe")


@pytest.mark.parametrize(
    ("address", "expected"),
    [
        (None, "alohamini2"),
        ("tcp://10.0.0.4:5555", "alohamini2"),
        ("tcp://10.0.0.4:5555?model=alohamini1", "alohamini1"),
        ("tcp://10.0.0.4:5555?model=nonsense", "alohamini2"),
    ],
)
def test_the_llm_hint_comes_off_the_address(address: str | None, expected: str) -> None:
    assert parse_model(address) == expected


async def test_the_camera_composites_run_through_the_executor() -> None:
    """`search_scan`, `go_to` and `approach_and` are in this manifest, so they get driven
    rather than assumed: they are the verbs that would drive the base across a room."""
    adapter = AlohaMiniAdapter(AlohaMiniMock())
    manifest = await adapter.connect()
    ex = _executor(adapter, manifest)
    for verb in ("search_scan", "go_to", "approach_and"):
        assert manifest.provides(verb), verb

    scanned = await ex.run_verb("search_scan", {"target": "ball", "max_steps": 4})
    assert scanned.ok, scanned.summary

    went = await ex.run_verb("go_to", {"target": "ball", "stop_distance": 0.8})
    assert went.ok, went.summary

    then = await ex.run_verb(
        "approach_and", {"target": "ball", "stop_distance": 0.8, "then": "stop"}
    )
    assert then.ok, then.summary


async def test_a_host_with_no_camera_has_no_composites_to_run() -> None:
    adapter = AlohaMiniAdapter(AlohaMiniMock(camera=False))
    manifest = await adapter.connect()
    ex = _executor(adapter, manifest)
    for verb in ("observe", "search_scan", "go_to", "approach_and"):
        assert not manifest.provides(verb), verb
        with pytest.raises((VerbNotFound, VerbNotAllowed)):
            await ex.run_verb(verb, {})


# ── the contract, at the library level and at the exit code ────────────────────────────


def test_the_manifest_declares_exactly_this_set_and_no_more() -> None:
    full = alohamini_manifest("zmq", camera=True, arms=True)
    assert set(full.verb_names()) == {
        "report_state",
        "stop",
        "move",
        "lift",
        "move_joints",
        "gripper",
        "home_arms",
        "observe",
        "go_to",
        "approach_and",
        "search_scan",
    }
    no_arms = alohamini_manifest("zmq", camera=True, arms=False)
    assert not (set(no_arms.verb_names()) & {"move_joints", "gripper", "home_arms"})


def test_a_kicking_task_is_refused_against_this_robot_with_the_validators_words() -> None:
    problems = validate_duck(load_duck("find-and-kick"), [describe("mock", "alohamini-01")])
    assert any("does not provide it" in p.message for p in problems), [p.message for p in problems]
    cli = runner.invoke(app, ["validate", "ducks/find-and-kick.duck", "--robot", "alohamini:mock"])
    assert cli.exit_code == 1 and "does not provide it" in cli.output


def test_list_verbs_shows_the_real_set() -> None:
    result = runner.invoke(env=WIDE, app=app, args=["list-verbs", "--robot", "alohamini:mock"])
    assert result.exit_code == 0
    names = _verb_column(result.output)
    assert {"move", "lift", "gripper", "observe"} <= names, names
    assert not names & {"kick", "quack", "say", "gaze", "perform", "stand"}, names


async def test_the_confirm_gated_verbs_are_actually_gated() -> None:
    """`lift`, `move_joints` and `home_arms` all declare `safety_class="confirm"` and nothing
    verified any of it. These are the verbs that drive a 600 mm motorised axis and two arms
    whose torque quackd's own host wrapper had to switch on."""
    asked: list[str] = []

    def refuse(name: str, _params: dict[str, object]) -> bool:
        asked.append(name)
        return False

    adapter = AlohaMiniAdapter(AlohaMiniMock())
    manifest = await adapter.connect()
    ex = Executor(
        registry_from_manifest(manifest, adapter),  # type: ignore[arg-type]
        adapter,
        contract=None,
        detector=ColorBlobDetector(),
        confirm=refuse,
    )
    for verb, params in (
        ("lift", {"height_mm": 200.0}),
        ("move_joints", {"arm": "left", "positions": {"shoulder_pan": 10.0}}),
        ("home_arms", {}),
    ):
        with pytest.raises(ConfirmDenied, match=verb):
            await ex.run_verb(verb, params)
    assert asked == ["lift", "move_joints", "home_arms"], asked

    assert (await ex.run_verb("report_state", {})).ok
    assert asked == ["lift", "move_joints", "home_arms"], "a safe verb must not ask"
