"""The ToddlerBot adapter: a humanoid, and the verbs it does not get to have.

quackd's first full humanoid, and the manifest is mostly absences. Three of them are the
point of this file: there is no text to speech, so `say` does not exist; there is no walk
checkpoint in the repository, so locomotion does not exist unless the daemon says one is
staged; and there is no get-up policy, so a fall is terminal and every moving verb has to say
so rather than letting an LLM thrash a fallen robot against the floor.
"""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from quackd.adapters.base import RobotAdapter
from quackd.adapters.factory import make_adapter, parse_robot_spec
from quackd.agent.prompts import build_system_prompt
from quackd.cli import app
from quackd.duckfile.parser import load_duck
from quackd.duckfile.validate import validate_duck
from quackd.host import HostClient
from quackd.perception.color_blob import ColorBlobDetector
from quackd.perception.host import HostDetector
from quackd.safety import ConfirmDenied, Executor, VerbNotAllowed, allow_all
from quackd.transport.base import Intent
from quackd.verbs.core import scan_mode
from quackd.verbs.registry import VerbNotFound, registry_from_manifest
from quackd_toddlerbot import (
    MAX_VX,
    MAX_VY,
    MAX_WZ,
    ROBOTS,
    ToddlerBotAdapter,
    conditions,
    describe,
    implementations,
    toddlerbot_manifest,
)
from quackd_toddlerbot.mock import ToddlerBotMock
from quackd_toddlerbot.sim2d import ToddlerBotSim2D
from quackd_toddlerbot.verbs import MOTIONS, SHIPPED_MOTIONS, neck_limits
from tests.fake_jetson_hostd import FakeHostd

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


#: Verbs other robots have and this body has not, at this pin.
ABSENT = {"say", "quack", "express", "play_sound", "wake_up", "sit", "kick", "grab", "stand_up"}

DUCK = load_duck("toddlerbot-lookout")


def _executor(adapter: ToddlerBotAdapter, manifest: object) -> Executor:
    return Executor(
        registry_from_manifest(manifest, adapter),  # type: ignore[arg-type]
        adapter,
        contract=None,
        detector=ColorBlobDetector(),
        confirm=allow_all,
    )


# ── the manifest ────────────────────────────────────────────────────────────────────────


def test_manifest_is_a_humanoid_with_no_voice() -> None:
    m = toddlerbot_manifest("mock", camera=True, walk=True)
    assert (m.embodiment, m.mobility) == ("humanoid", "legged")
    assert "sound" not in m.intents, "there is no text to speech anywhere at this pin"
    assert not (set(m.verb_names()) & ABSENT)
    assert "battery" not in m.sensors, "nothing reports a battery to Python"
    assert set(m.sensors) == {"imu", "joint_state", "camera"}


def test_without_a_walk_checkpoint_there_is_no_locomotion_at_all() -> None:
    """The walk policy is an ONNX artifact from a wandb run that upstream does not publish
    and does not check in. Nothing about that is quackd's to gate: the verbs do not exist."""
    walking = toddlerbot_manifest("bridge", camera=True, walk=True)
    grounded = toddlerbot_manifest("bridge", camera=True, walk=False)
    needs_legs = {"move", "go_to", "approach_and"}
    assert needs_legs <= set(walking.verb_names())
    assert not (needs_legs & set(grounded.verb_names()))
    assert grounded.mobility == "none" and "twist" not in grounded.intents
    assert walking.mobility == "legged" and "twist" in walking.intents
    # the verbs that do not need legs are unaffected
    assert {"stand", "perform", "look", "observe"} <= set(grounded.verb_names())


def test_a_build_with_no_neck_loses_the_head_verbs() -> None:
    m = toddlerbot_manifest("bridge", camera=True, neck=False)
    assert not m.provides("look")
    assert "gaze" not in m.intents
    # search_scan needs a camera and something to look around with
    assert not m.provides("search_scan")


def test_grippers_exist_only_on_the_builds_that_have_them() -> None:
    assert not toddlerbot_manifest("bridge").provides("grip")
    with_grip = toddlerbot_manifest("bridge", gripper=True, robot="toddlerbot_2xc_gripper")
    assert with_grip.provides("grip") and "gripper" in with_grip.intents


def test_the_deadman_is_only_claimed_when_quackd_owns_the_loop() -> None:
    """There is no watchdog, no timeout and no e-stop anywhere upstream, and silence on this
    body means hold forever. The only deadman that can exist is the daemon's own."""
    m = toddlerbot_manifest("bridge", deadman=True)
    assert m.safety_authority.native == "none"
    assert m.safety_authority.deadman is True
    assert toddlerbot_manifest("bridge", deadman=False).safety_authority.deadman is False
    assert "safe pose" in m.extras["deadman_scope"]


def test_extras_say_what_is_missing_and_what_is_assumed() -> None:
    m = toddlerbot_manifest("mock", camera=True, walk=True)
    assert "get-up" in m.extras["no_recovery"]
    assert "battery" in m.extras["no_battery"]
    assert m.extras["speech"] == "none"
    assumptions = " ".join(m.extras["assumptions"]).lower()
    for expected in ("tilt", "neck", "holding", "odometry"):
        assert expected in assumptions


def test_limits_stay_inside_the_schema() -> None:
    from quackd.verbs.core import MAX_VX as SCHEMA_VX
    from quackd.verbs.core import MAX_VY as SCHEMA_VY
    from quackd.verbs.core import MAX_WZ as SCHEMA_WZ

    m = toddlerbot_manifest("mock", camera=True, walk=True)
    assert m.limits["max_vx"] <= SCHEMA_VX
    assert m.limits["max_vy"] <= SCHEMA_VY
    assert m.limits["max_wz"] <= SCHEMA_WZ
    assert (MAX_VX, MAX_VY, MAX_WZ) == (0.2, 0.1, 1.0)


def test_digest_ignores_the_id_and_the_backend() -> None:
    a = toddlerbot_manifest("mock", "one", camera=True, walk=True)
    b = toddlerbot_manifest("sim2d", "two", camera=True, walk=True)
    assert a.digest() == b.digest()
    assert a.digest() != toddlerbot_manifest("mock", "one", camera=True, walk=False).digest()


def test_the_bridge_claims_nothing_until_the_daemon_has_spoken() -> None:
    static = describe(parse_robot_spec("toddlerbot:bridge").backend)
    assert static.mobility == "none"
    assert not static.provides("observe") and not static.provides("move")
    assert describe(parse_robot_spec("toddlerbot:mock").backend).provides("move")


# ── the motions quackd will and will not offer ─────────────────────────────────────────


def test_quackd_curates_the_motion_list_and_says_so() -> None:
    """Every shipped motion is something the robot can do, so this is a safety judgement
    rather than a capability claim, and the docs say which and why."""
    assert set(MOTIONS) < set(SHIPPED_MOTIONS)
    for excluded in ("cartwheel", "pull_up_grasp", "pull_up_pull", "walk_zmp"):
        assert excluded in SHIPPED_MOTIONS
        assert excluded not in MOTIONS


def test_the_neck_is_clamped_to_a_fraction_of_its_travel() -> None:
    limits = neck_limits({"neck_yaw": (-1.0, 1.0), "neck_pitch": (-0.5, 0.5), "knee": (-2, 2)})
    assert set(limits) == {"neck_yaw", "neck_pitch"}, "only the neck"
    assert limits["neck_yaw"] == pytest.approx((-0.8, 0.8))


# ── how this robot looks around ────────────────────────────────────────────────────────


def test_search_scan_sweeps_the_head_rather_than_turning_a_humanoid() -> None:
    """A body turn is available once a walk policy is staged, but turning a humanoid with no
    fall recovery to look around is not what quackd reaches for first."""
    grounded = toddlerbot_manifest("bridge", camera=True, walk=False)
    assert scan_mode(grounded) == "gaze"


# ── the registry, the prompt and .duck validation all agree ────────────────────────────


def test_an_absent_verb_is_absent_everywhere() -> None:
    m = toddlerbot_manifest("mock", camera=True, walk=True)
    registry = registry_from_manifest(m, implementations=implementations(), conditions=conditions())
    allow = DUCK.frontmatter.verbs.allow
    prompt = build_system_prompt(DUCK, [registry.view(n) for n in allow], "mock", manifest=m)
    offered = {line.split("`")[1] for line in prompt.splitlines() if line.startswith("- `")}
    for verb in ABSENT:
        with pytest.raises(VerbNotFound):
            registry.get(verb)
        assert verb not in offered
    assert offered == set(allow)


def test_the_shipped_lookout_task_validates_and_moves_no_legs() -> None:
    assert validate_duck(DUCK, [toddlerbot_manifest("mock", camera=True, walk=True)]) == []
    allowed = set(DUCK.frontmatter.verbs.allow)
    assert not (allowed & {"move", "go_to", "approach_and", "perform", "stand"}), (
        "the first task pointed at a real humanoid must not move it"
    )


def test_the_cli_refuses_a_task_this_robot_cannot_do() -> None:
    result = runner.invoke(
        app, ["validate", "ducks/find-and-kick.duck", "--robot", "toddlerbot:mock"]
    )
    assert result.exit_code != 0


# ── the mock backend, through a real executor ──────────────────────────────────────────


async def test_every_verb_runs_offline_through_the_executor() -> None:
    adapter = ToddlerBotAdapter(ToddlerBotMock())
    assert isinstance(adapter, RobotAdapter)
    manifest = await adapter.connect()
    ex = _executor(adapter, manifest)
    mock = adapter.transport
    assert isinstance(mock, ToddlerBotMock)

    assert (await ex.run_verb("report_state", {})).ok
    assert (await ex.run_verb("observe", {})).ok
    assert (await ex.run_verb("look", {"yaw_deg": 30.0, "pitch_deg": -10.0})).ok
    assert mock.neck_yaw == pytest.approx(30.0)

    stood = await ex.run_verb("stand", {})
    assert stood.ok and mock.stands == 1

    performed = await ex.run_verb("perform", {"motion": "kneel"})
    assert performed.ok and mock.performed == ["kneel"]

    assert (await ex.run_verb("move", {"vx": 0.1, "duration_s": 0.4})).ok
    assert (await ex.run_verb("stop", {})).ok
    assert (await adapter.get_state()).battery_percent is None
    health = await adapter.health()
    assert health.ok and health.battery_percent is None


async def test_the_mock_refuses_what_this_body_cannot_do() -> None:
    adapter = ToddlerBotAdapter(ToddlerBotMock())
    await adapter.connect()
    assert not (await adapter.send_intent(Intent.sound("chirp"))).accepted
    assert not (await adapter.send_intent(Intent.do("kick_left"))).accepted
    assert not (await adapter.send_intent(Intent.do("motion:cartwheel"))).accepted
    # quackd never limps a robot, and here there is no way back: torque-on is unreachable
    assert not (await adapter.send_intent(Intent.enable(False))).accepted


async def test_a_fallen_robot_is_told_to_wait_for_a_human() -> None:
    """There is no get-up policy for this body, so the refusal names no verb: `stand_up` does
    not exist and offering it would invite an LLM to thrash a fallen humanoid."""
    adapter = ToddlerBotAdapter(ToddlerBotMock(fallen=True))
    manifest = await adapter.connect()
    ex = _executor(adapter, manifest)
    for verb, params in (
        ("perform", {"motion": "kneel"}),
        ("look", {"yaw_deg": 10.0}),
        ("move", {"vx": 0.1, "duration_s": 0.2}),
    ):
        result = await ex.run_verb(verb, params)
        assert not result.ok
        assert "human" in result.summary and "stand_up" not in result.summary
    assert (await ex.run_verb("stop", {})).ok, "stop is never gated"
    assert not (await adapter.health()).ok


async def test_an_uncalibrated_robot_refuses_to_move() -> None:
    adapter = ToddlerBotAdapter(ToddlerBotMock(calibrated=False))
    manifest = await adapter.connect()
    ex = _executor(adapter, manifest)
    result = await ex.run_verb("stand", {})
    assert not result.ok and "calibration" in result.summary
    assert (await ex.run_verb("stop", {})).ok


async def test_a_silent_daemon_refuses_moving_verbs_but_never_stop() -> None:
    mock = ToddlerBotMock()
    adapter = ToddlerBotAdapter(mock)
    manifest = await adapter.connect()
    ex = _executor(adapter, manifest)
    mock.stale_ms = 5000.0
    result = await ex.run_verb("stand", {})
    assert not result.ok and "not answering" in result.summary
    assert "safe pose" in result.summary, "and it says the robot is standing, not falling"
    assert (await ex.run_verb("stop", {})).ok
    assert (await ex.run_verb("report_state", {})).ok


async def test_connect_narrows_to_what_the_transport_reported() -> None:
    grounded = ToddlerBotAdapter(ToddlerBotMock(walk=False, camera=False, neck=False))
    manifest = await grounded.connect()
    assert manifest.mobility == "none"
    for verb in ("move", "go_to", "approach_and", "observe", "search_scan", "look"):
        assert not manifest.provides(verb)
    assert manifest.provides("stand") and manifest.provides("perform")


async def test_the_camera_composites_run_through_the_executor() -> None:
    """`search_scan`, `go_to` and `approach_and` are in this manifest, so they get driven
    rather than assumed. They are also the only declared verbs that would walk a body which
    cannot get up if it falls, which makes them the last ones to leave untested."""
    adapter = ToddlerBotAdapter(ToddlerBotMock())
    manifest = await adapter.connect()
    ex = _executor(adapter, manifest)
    mock = adapter.transport
    assert isinstance(mock, ToddlerBotMock)
    for verb in ("search_scan", "go_to", "approach_and"):
        assert manifest.provides(verb), verb

    scanned = await ex.run_verb("search_scan", {"target": "ball", "max_steps": 4})
    assert scanned.ok, scanned.summary

    before = mock.ball_relative()
    assert before is not None
    went = await ex.run_verb("go_to", {"target": "ball", "stop_distance": 0.4})
    assert went.ok, went.summary
    rel = mock.ball_relative()
    # Against where it started, not against a constant above the starting distance.
    assert rel is not None and rel[0] < before[0] - 0.2, "it actually closed the distance"


async def test_approach_and_runs_its_then_verb_through_the_executor() -> None:
    adapter = ToddlerBotAdapter(ToddlerBotMock())
    manifest = await adapter.connect()
    ex = _executor(adapter, manifest)
    mock = adapter.transport
    assert isinstance(mock, ToddlerBotMock)
    then = await ex.run_verb(
        "approach_and", {"target": "ball", "stop_distance": 0.8, "then": "stand"}
    )
    assert then.ok, then.summary
    assert mock.stands >= 1, "the `then` verb ran"


async def test_a_blind_build_has_no_composites_to_run() -> None:
    """With no camera these four are absent from the registry rather than refused, so asking
    for one is a VerbNotFound and never an accepted call that does nothing."""
    adapter = ToddlerBotAdapter(ToddlerBotMock(camera=False))
    manifest = await adapter.connect()
    ex = _executor(adapter, manifest)
    for verb in ("observe", "search_scan", "go_to", "approach_and"):
        assert not manifest.provides(verb), verb
        with pytest.raises((VerbNotFound, VerbNotAllowed)):
            await ex.run_verb(verb, {})


async def test_the_gripper_build_grips_through_the_executor() -> None:
    """`grip` exists only on the gripper builds, so it is only driveable there."""
    adapter = ToddlerBotAdapter(ToddlerBotMock(gripper=True))
    manifest = await adapter.connect()
    ex = _executor(adapter, manifest)
    mock = adapter.transport
    assert isinstance(mock, ToddlerBotMock)
    assert manifest.provides("grip")
    closed = await ex.run_verb("grip", {"close": True, "side": "right"})
    assert closed.ok, closed.summary
    assert mock.holding["right"] is True


# ── the simulator ──────────────────────────────────────────────────────────────────────


async def test_the_simulated_humanoid_walks_looks_and_performs() -> None:
    adapter = ToddlerBotAdapter(ToddlerBotSim2D(seed=3))
    manifest = await adapter.connect()
    ex = _executor(adapter, manifest)
    sim = adapter.transport
    assert isinstance(sim, ToddlerBotSim2D)

    assert (await ex.run_verb("observe", {})).ok
    assert (await ex.run_verb("move", {"vx": 0.15, "duration_s": 0.5})).ok
    assert (await ex.run_verb("look", {"yaw_deg": 25.0})).ok
    assert sim.neck_yaw == pytest.approx(25.0)
    assert (await ex.run_verb("stand", {})).ok and sim.stands == 1
    assert (await ex.run_verb("perform", {"motion": "hold"})).ok
    assert sim.performed == ["hold"]
    assert (await ex.run_verb("stop", {})).ok


async def test_the_simulator_refuses_the_skills_this_body_has_not_got() -> None:
    sim = ToddlerBotSim2D(seed=1)
    await sim.connect()
    for intent in (Intent.sound("chirp"), Intent.do("kick_left"), Intent.do("ground_pick")):
        assert not (await sim.send_intent(intent)).accepted


# ── the factory ────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("backend", ["mock", "sim2d", "bridge"])
def test_the_factory_builds_every_backend(backend: str) -> None:
    adapter = make_adapter(f"toddlerbot:{backend}")
    assert adapter.name == "toddlerbot" and adapter.backend == backend


def test_an_unknown_backend_names_the_real_ones() -> None:
    from quackd_toddlerbot import make

    with pytest.raises(ValueError, match="unknown toddlerbot backend"):
        make("real")


def test_only_the_builds_quackd_will_drive_are_listed() -> None:
    """Upstream also ships teleop_leader, which is the arm a human holds, not a robot."""
    assert "teleop_leader" not in ROBOTS
    assert set(ROBOTS) == {
        "toddlerbot_2xc",
        "toddlerbot_2xc_gripper",
        "toddlerbot_2xm",
        "toddlerbot_2xm_gripper",
    }


# ── the contract, at the library level and at the exit code ────────────────────────────


def test_the_manifest_declares_exactly_this_set_and_no_more() -> None:
    """Pinned on purpose. A verb that appears here without anyone deciding to add it is a
    verb the LLM will be offered on a body that cannot get up if it falls."""
    full = toddlerbot_manifest("bridge", camera=True, neck=True, gripper=True, walk=True)
    assert set(full.verb_names()) == {
        "report_state",
        "stop",
        "observe",
        "move",
        "go_to",
        "approach_and",
        "search_scan",
        "look",
        "stand",
        "perform",
        "grip",
    }
    bare = toddlerbot_manifest("bridge", camera=False, neck=False, gripper=False, walk=False)
    assert set(bare.verb_names()) == {"report_state", "stop", "stand", "perform"}


def test_a_kicking_task_is_refused_against_this_body_with_the_validators_words() -> None:
    problems = validate_duck(load_duck("find-and-kick"), [describe("mock", "toddlerbot-01")])
    assert any("does not provide it" in p.message for p in problems), [p.message for p in problems]
    cli = runner.invoke(app, ["validate", "ducks/find-and-kick.duck", "--robot", "toddlerbot:mock"])
    assert cli.exit_code == 1 and "does not provide it" in cli.output


def test_list_verbs_shows_the_real_set() -> None:
    result = runner.invoke(env=WIDE, app=app, args=["list-verbs", "--robot", "toddlerbot:mock"])
    assert result.exit_code == 0
    names = _verb_column(result.output)
    assert {"look", "stand", "perform", "observe"} <= names, names
    assert not names & {"kick", "quack", "say", "relax", "stand_up", "sit_toggle"}, names


async def test_search_scan_sweeps_the_head_and_never_turns_the_body() -> None:
    """`scan_mode` turns any robot with mobility and the twist intent, which is right for a
    duck and wrong for a humanoid that cannot get up. With a walk checkpoint staged this body
    is mobile, so the shared verb would pirouette it to look for a ball. quackd supplies its
    own implementation, and the manifest's own comment claims exactly this."""
    from quackd.verbs.core import scan_mode

    # The ball starts BEHIND the robot, outside the camera's field of view. With it in view
    # the verb succeeds on the first frame without sweeping anything, and this guard passes
    # just as happily with the override reverted.
    adapter = ToddlerBotAdapter(ToddlerBotMock(walk=True, ball_xy=(-1.2, 0.4)))
    manifest = await adapter.connect()
    mock = adapter.transport
    assert isinstance(mock, ToddlerBotMock)
    assert manifest.provides("search_scan")
    assert mock.ball_relative() is not None
    assert abs(mock.ball_relative()[1]) > 90.0, "the ball really is out of view to start"
    # the shared rule would turn the body here, which is the whole reason for the override
    assert scan_mode(manifest) == "turn"

    ex = _executor(adapter, manifest)
    start_theta = mock.theta
    await ex.run_verb("search_scan", {"target": "ball", "max_steps": 6})
    # Whether it finds a ball behind it is not the point; not turning the body is.
    assert mock.theta == pytest.approx(start_theta), "it never turned the body"
    assert not mock.intents_of("move"), "and never asked to walk"
    assert len(mock.intents_of("look")) > 1, "it swept the head instead"


async def test_search_scan_stops_the_sweep_when_the_board_detector_fails() -> None:
    """With `--host`, a real ToddlerBot's detector is YOLO on the board, and a board can stop
    answering. A failed call is a frame with no detections, and the sweep read each one as the
    ball not being there: every look taken, then `ball not found`, with the ball in plain view
    of the first. It stops on the first frame the board could not read and says why."""
    adapter = ToddlerBotAdapter(ToddlerBotMock(walk=True))
    manifest = await adapter.connect()
    mock = adapter.transport
    assert isinstance(mock, ToddlerBotMock)
    seen = await _executor(adapter, manifest).run_verb("search_scan", {"target": "ball"})
    assert seen.ok and seen.data["steps"] == 0, "the ball is in view of the first look"
    looks = len(mock.intents_of("look"))

    with FakeHostd() as hostd:
        hostd.detect_status = 503
        hostd.detect_reply = {"ok": False, "reason": "detection is not available: CUDA OOM"}
        ex = Executor(
            registry_from_manifest(manifest, adapter),
            adapter,
            contract=None,
            detector=HostDetector(HostClient(hostd.address), fov_deg=62.2),
            confirm=allow_all,
        )
        result = await ex.run_verb("search_scan", {"target": "ball", "max_steps": 6})
        assert len(hostd.requests_to("/detect")) == 1
    assert not result.ok
    assert result.summary.startswith("search_scan: yolo@host failed, so the sweep stopped: ")
    assert "not found" not in result.summary
    assert len(mock.intents_of("look")) == looks + 1, "one look, not the whole sweep"
    assert not mock.intents_of("move")


async def test_the_confirm_gated_verbs_are_actually_gated() -> None:
    """`stand` and `perform` declare `safety_class="confirm"`, and nothing in the suite
    verified that declaration. These are the two verbs that move the whole body of a robot
    which cannot get up, so the gate is the point of them being declared at all."""
    asked: list[str] = []

    def refuse(name: str, _params: dict[str, object]) -> bool:
        asked.append(name)
        return False

    adapter = ToddlerBotAdapter(ToddlerBotMock())
    manifest = await adapter.connect()
    mock = adapter.transport
    assert isinstance(mock, ToddlerBotMock)
    ex = Executor(
        registry_from_manifest(manifest, adapter),  # type: ignore[arg-type]
        adapter,
        contract=None,
        detector=ColorBlobDetector(),
        confirm=refuse,
    )

    for verb, params in (("stand", {}), ("perform", {"motion": "kneel"})):
        # a denial stops the verb outright rather than returning a failed result
        with pytest.raises(ConfirmDenied, match=verb):
            await ex.run_verb(verb, params)
    assert asked == ["stand", "perform"], asked
    assert mock.stands == 0 and mock.performed == [], "and neither reached the robot"

    # while the safe ones are never gated
    assert (await ex.run_verb("report_state", {})).ok
    assert (await ex.run_verb("observe", {})).ok
    assert asked == ["stand", "perform"], "a safe verb must not ask"


def test_the_deadman_flag_says_what_each_backend_can_actually_do() -> None:
    """ADR-0028 said `deadman` was true only on `:bridge`, which is exactly backwards: the
    offline doubles emulate one, and the static `bridge` manifest cannot know whether a daemon
    is there until it has answered the handshake."""
    assert describe("mock").safety_authority.deadman is True
    assert describe("sim2d").safety_authority.deadman is True
    assert describe("bridge").safety_authority.deadman is False, "nothing has answered yet"
    # and connecting is what turns it true
    assert toddlerbot_manifest("bridge", deadman=True).safety_authority.deadman is True
