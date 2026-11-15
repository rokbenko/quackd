"""The XLeRobot adapter: a cart with two arms that never claims a voice or a head.

The first body in Roboglot with both a mobile base and arms, so the things worth pinning are
the ones no other adapter could get wrong: that `move` strafes only on the base that can,
that arm units are normalised rather than degrees, that commanding an arm stops the wheels
because upstream writes them on every action, and that `say` and `gaze` do not exist here.
"""

from __future__ import annotations

import math
import sys

import pytest
from typer.testing import CliRunner

from roboglot.adapters.base import AdapterNotInstalled, RobotAdapter
from roboglot.adapters.factory import make_adapter, parse_robot_spec
from roboglot.agent.prompts import build_system_prompt
from roboglot.cli import app
from roboglot.glotfile.parser import load_glot, parse_glot_text
from roboglot.glotfile.validate import validate_glot
from roboglot.perception.color_blob import ColorBlobDetector
from roboglot.safety import ConfirmDenied, Executor, VerbNotAllowed, allow_all
from roboglot.transport.base import Intent, TransportError
from roboglot.verbs.core import scan_mode
from roboglot.verbs.registry import VerbNotFound, registry_from_manifest
from roboglot_xlerobot import (
    DEFAULT_VARIANT,
    MAX_VX,
    MAX_VY,
    MAX_WZ,
    XLerobotAdapter,
    conditions,
    describe,
    implementations,
    max_vy_for,
    parse_swap_colour,
    parse_variant,
    xlerobot_manifest,
)
from roboglot_xlerobot.mock import XLerobotMock
from roboglot_xlerobot.verbs import GRIPPER_CLOSED, GRIPPER_OPEN, JOINTS
from tests.conftest import REPO

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


XLEROBOT_VERBS = {
    "report_state",
    "stop",
    "move",
    "move_joints",
    "gripper",
    "observe",
    "go_to",
    "search_scan",
    "approach_and",
}
#: Verbs other robots have and this body has not. No speaker, no microphone, no head mapping.
ABSENT = {"say", "quack", "gaze", "express", "play_sound", "wake_up", "sit", "stand", "kick"}

DUCK = load_glot("xlerobot-lookout")
DUCK_TEXT = (REPO / "tasks" / "xlerobot-lookout.glot").read_text(encoding="utf-8")


def _executor(adapter: XLerobotAdapter, manifest: object, *, contract: object = None) -> Executor:
    """No contract means every non-dangerous verb, which is what exercising the body needs.

    The shipped lookout task deliberately allows three verbs and nothing else, so it is the
    contract for the validation tests and the wrong one for these."""
    return Executor(
        registry_from_manifest(manifest, adapter),  # type: ignore[arg-type]
        adapter,
        contract=contract,  # type: ignore[arg-type]
        detector=ColorBlobDetector(),
        confirm=allow_all,
    )


# ── the manifest ────────────────────────────────────────────────────────────────────────


def test_manifest_is_a_wheeled_body_with_arms_and_no_voice() -> None:
    m = xlerobot_manifest("mock", camera=True)
    assert (m.embodiment, m.mobility) == ("wheeled", "wheeled")
    assert set(m.intents) == {"twist", "joint", "gripper"}
    assert "sound" not in m.intents, "there is no speaker in the bill of materials"
    assert set(m.verb_names()) == XLEROBOT_VERBS
    assert not (set(m.verb_names()) & ABSENT)
    # no BMS exists, so a battery abort could never fire and the sensor must not be claimed
    assert "battery" not in m.sensors
    assert set(m.sensors) == {"joint_state", "camera"}


def test_limits_stay_inside_the_schema_because_limits_may_only_narrow() -> None:
    """The robot's own fast tier is 0.3 m/s and 90 deg/s, but Roboglot's schema caps vy at 0.2
    and wz at 1.5 rad/s. Declaring the robot's numbers would be declaring more than the core
    verbs can express."""
    from roboglot.verbs.core import MAX_VX as SCHEMA_VX
    from roboglot.verbs.core import MAX_VY as SCHEMA_VY
    from roboglot.verbs.core import MAX_WZ as SCHEMA_WZ

    m = xlerobot_manifest("mock", camera=True)
    assert m.limits["max_vx"] <= SCHEMA_VX
    assert m.limits["max_vy"] <= SCHEMA_VY
    assert m.limits["max_wz"] <= SCHEMA_WZ
    assert (MAX_VX, MAX_VY, MAX_WZ) == (0.3, 0.2, 1.5)
    # 90 deg/s is 1.571 rad/s: the schema binds, not the robot
    assert math.radians(90.0) > MAX_WZ


@pytest.mark.parametrize(
    ("variant", "strafes"), [("omni3", True), ("diff2", False), ("mecanum", False)]
)
def test_only_the_omniwheel_base_declares_a_sideways_speed(variant: str, strafes: bool) -> None:
    """The other bases accept y.vel and do nothing with it. Declaring 0.0 makes Roboglot's own
    clamp zero the request and report it, instead of promising a move that will not happen."""
    m = xlerobot_manifest("mock", camera=True, variant=variant)
    assert (m.limits["max_vy"] > 0.0) is strafes
    assert max_vy_for(variant) == (MAX_VY if strafes else 0.0)


def test_safety_authority_says_the_deadman_covers_only_the_base() -> None:
    """The host's 500 ms watchdog is real, and it zeroes the wheels and nothing else: the arms
    keep holding under torque. That is not an estop and not a torque limit."""
    m = xlerobot_manifest("mock", camera=True)
    assert m.safety_authority.native == "none"
    assert m.safety_authority.deadman is True
    assert m.extras["deadman_scope"] == "base_only"
    assert m.extras["watchdog_ms"] == 500


def test_extras_record_every_assumption_rather_than_hiding_it() -> None:
    m = xlerobot_manifest("mock", camera=True)
    assumptions = " ".join(m.extras["assumptions"]).lower()
    for expected in ("holding", "bgr", "field of view", "yaw", "odometry"):
        assert expected in assumptions
    assert m.extras["speech"] == "none"
    assert m.extras["variant"] == DEFAULT_VARIANT


def test_digest_ignores_the_id_and_the_backend() -> None:
    """Two carts with the same capability are the same capability, whatever they are called."""
    a = xlerobot_manifest("mock", "cart-a", camera=True)
    b = xlerobot_manifest("zmq", "cart-b", camera=True)
    assert a.digest() == b.digest()
    assert a.digest() != xlerobot_manifest("mock", "cart-a", camera=False).digest()


# ── a blind cart, which is the one upstream actually ships ──────────────────────────────


def test_a_cart_with_no_camera_loses_exactly_the_verbs_that_need_one() -> None:
    """A stock XLeRobot has every camera commented out of its config, so it is blind. The four
    camera verbs must not merely be gated, they must not exist."""
    blind = xlerobot_manifest("zmq", camera=False)
    assert "camera" not in blind.sensors
    assert set(blind.verb_names()) == {"report_state", "stop", "move", "move_joints", "gripper"}
    for verb in ("observe", "go_to", "search_scan", "approach_and"):
        assert not blind.provides(verb)
        assert verb not in blind.preconditions


def test_the_static_manifest_claims_a_camera_only_for_the_mock() -> None:
    assert describe(parse_robot_spec("xlerobot:mock").backend).provides("observe")
    assert not describe(parse_robot_spec("xlerobot:zmq").backend).provides("observe")


async def test_connect_narrows_the_manifest_to_what_the_wire_actually_carried() -> None:
    seeing = XLerobotAdapter(XLerobotMock(camera=True))
    assert (await seeing.connect()).provides("observe")
    blind = XLerobotAdapter(XLerobotMock(camera=False))
    manifest = await blind.connect()
    assert not manifest.provides("observe")
    assert manifest.extras["camera_key"] is None


# ── how this robot looks around ─────────────────────────────────────────────────────────


def test_search_scan_turns_the_whole_cart_because_there_is_no_head_to_sweep() -> None:
    """head_motor_1 and head_motor_2 are on the wire, but which is yaw is stated nowhere
    upstream, so Roboglot declares no gaze and the body turns instead."""
    m = xlerobot_manifest("mock", camera=True)
    assert scan_mode(m) == "turn"
    assert "gaze" not in m.intents


# ── the registry, the prompt and .glot validation all agree ────────────────────────────


def test_an_absent_verb_is_absent_everywhere() -> None:
    m = xlerobot_manifest("mock", camera=True)
    registry = registry_from_manifest(m, implementations=implementations(), conditions=conditions())
    allow = DUCK.frontmatter.verbs.allow
    prompt = build_system_prompt(DUCK, [registry.view(n) for n in allow], "mock", manifest=m)
    # the prompt offers a verb as "- `name`: description"; the task's own prose may still say
    # the word, and does, because it explains that this robot has no voice
    offered = {line.split("`")[1] for line in prompt.splitlines() if line.startswith("- `")}
    for verb in ABSENT:
        with pytest.raises(VerbNotFound):
            registry.get(verb)
        assert verb not in offered
    assert offered == set(allow)
    for verb in XLEROBOT_VERBS:
        assert registry.get(verb) is not None


def test_a_task_needing_a_voice_is_refused_against_this_robot() -> None:
    text = DUCK_TEXT.replace(
        "requires: [observe, report_state]", "requires: [observe, say]"
    ).replace("allow: [observe, report_state, stop]", "allow: [observe, report_state, stop, say]")
    duck = parse_glot_text(text)
    problems = validate_glot(duck, [xlerobot_manifest("mock", camera=True)])
    assert any("say" in p.message for p in problems)


def test_the_shipped_lookout_task_validates_and_moves_nothing() -> None:
    assert validate_glot(DUCK, [xlerobot_manifest("mock", camera=True)]) == []
    allowed = set(DUCK.frontmatter.verbs.allow)
    assert not (allowed & {"move", "go_to", "approach_and", "search_scan", "move_joints"}), (
        "the first task pointed at real hardware must not drive a 12 kg cart"
    )


def test_the_cli_refuses_a_task_this_robot_cannot_do() -> None:
    result = runner.invoke(
        app, ["validate", "tasks/open-duck-scout.glot", "--robot", "xlerobot:mock"]
    )
    assert result.exit_code != 0


# ── the mock backend, through a real executor ──────────────────────────────────────────


async def test_every_verb_runs_offline_through_the_executor() -> None:
    adapter = XLerobotAdapter(XLerobotMock())
    assert isinstance(adapter, RobotAdapter)
    manifest = await adapter.connect()
    assert manifest.backend == "mock"
    ex = _executor(adapter, manifest)
    mock = adapter.transport
    assert isinstance(mock, XLerobotMock)

    assert (await ex.run_verb("report_state", {})).ok
    assert (await ex.run_verb("observe", {})).ok

    driven = await ex.run_verb("move", {"vx": MAX_VX, "vy": MAX_VY, "duration_s": 0.4})
    assert driven.ok
    first = mock.intents_of("move")[0].params
    assert (first["vx"], first["vy"]) == pytest.approx((MAX_VX, MAX_VY))

    moved = await ex.run_verb(
        "move_joints", {"positions": {"left_arm_elbow_flex": 25.0}, "duration_s": 0.3}
    )
    assert moved.ok and mock.joints["left_arm_elbow_flex"] == pytest.approx(25.0)

    closed = await ex.run_verb("gripper", {"side": "left", "open": False})
    assert closed.ok and mock.joints["left_arm_gripper"] == pytest.approx(GRIPPER_CLOSED)
    assert mock.holding == {"left": True, "right": False}
    opened = await ex.run_verb("gripper", {"side": "both", "open": True})
    assert opened.ok and mock.holding == {"left": False, "right": False}
    assert mock.joints["right_arm_gripper"] == pytest.approx(GRIPPER_OPEN)

    assert (await ex.run_verb("stop", {})).ok
    state = await adapter.get_state()
    assert state.battery_percent is None, "no data link to the power station"
    health = await adapter.health()
    assert health.ok and health.battery_percent is None


async def test_the_mock_refuses_what_this_body_cannot_do() -> None:
    adapter = XLerobotAdapter(XLerobotMock())
    await adapter.connect()
    assert not (await adapter.send_intent(Intent.sound("chirp"))).accepted
    assert not (await adapter.send_intent(Intent.look(1.0, 0.0, 0.0))).accepted
    assert not (await adapter.send_intent(Intent.do("kick_left"))).accepted
    # Roboglot never limps a robot, on any body
    assert not (await adapter.send_intent(Intent.enable(False))).accepted


async def test_commanding_an_arm_stops_the_cart_because_upstream_writes_the_wheels() -> None:
    """`_body_to_wheel_raw` is called on every action with .get(key, 0.0) defaults, so an
    action carrying no velocity keys commands zero base velocity. A move_joints issued
    mid-drive halts the base, and Roboglot must not pretend otherwise."""
    adapter = XLerobotAdapter(XLerobotMock())
    manifest = await adapter.connect()
    ex = _executor(adapter, manifest)
    mock = adapter.transport
    assert isinstance(mock, XLerobotMock)

    await adapter.send_intent(Intent.move(vx=0.2))
    assert (mock.vx, mock.vy, mock.wz) == (0.2, 0.0, 0.0)
    result = await ex.run_verb(
        "move_joints", {"positions": {"right_arm_wrist_roll": 10.0}, "duration_s": 0.3}
    )
    assert result.ok and result.data["base_stopped"] is True
    assert (mock.vx, mock.vy, mock.wz) == (0.0, 0.0, 0.0)


async def test_the_watchdog_stops_the_base_on_silence_and_leaves_the_arms_alone() -> None:
    adapter = XLerobotAdapter(XLerobotMock())
    await adapter.connect()
    mock = adapter.transport
    assert isinstance(mock, XLerobotMock)
    await adapter.send_intent(Intent.joint({"left_arm_shoulder_pan": 40.0}, 0.1))
    await adapter.send_intent(Intent.move(vx=0.2))
    await mock.sleep(1.0)  # longer than the host's 500 ms window
    assert (mock.vx, mock.vy, mock.wz) == (0.0, 0.0, 0.0)
    assert mock.joints["left_arm_shoulder_pan"] == pytest.approx(40.0), "the arms keep holding"


async def test_a_base_that_cannot_strafe_ignores_a_sideways_command() -> None:
    adapter = XLerobotAdapter(XLerobotMock(variant="diff2"))
    await adapter.connect()
    mock = adapter.transport
    assert isinstance(mock, XLerobotMock)
    await adapter.send_intent(Intent.move(vx=0.1, vy=0.2))
    assert mock.vy == 0.0 and mock.vx == pytest.approx(0.1)


async def test_a_sideways_request_to_a_base_that_cannot_strafe_is_clamped_and_reported() -> None:
    """`limits` may only narrow, and max_vy 0.0 is how "this variant cannot strafe" is said.
    The LLM must be told the move it asked for is not the move it got."""
    adapter = XLerobotAdapter(XLerobotMock(variant="diff2"))
    manifest = await adapter.connect()
    assert manifest.limits["max_vy"] == 0.0
    ex = _executor(adapter, manifest)
    mock = adapter.transport
    assert isinstance(mock, XLerobotMock)
    result = await ex.run_verb("move", {"vx": 0.1, "vy": MAX_VY, "duration_s": 0.3})
    assert result.ok and "clamped" in result.summary
    assert mock.intents_of("move")[0].params["vy"] == 0.0


# ── preconditions ──────────────────────────────────────────────────────────────────────


async def test_a_silent_host_refuses_every_moving_verb_but_never_stop() -> None:
    """The host exits by itself an hour after it was started. When it does, the reading goes
    stale, and a verb that claimed a move nobody made would be the bug worth preventing."""
    adapter = XLerobotAdapter(XLerobotMock())
    manifest = await adapter.connect()
    ex = _executor(adapter, manifest)
    mock = adapter.transport
    assert isinstance(mock, XLerobotMock)
    mock.stale_ms = 4000.0

    for verb, params in (
        ("move", {"vx": 0.1, "duration_s": 0.2}),
        ("move_joints", {"positions": {"left_arm_elbow_flex": 5.0}}),
        ("gripper", {"side": "left", "open": True}),
    ):
        result = await ex.run_verb(verb, params)
        assert not result.ok
        assert "not answering" in result.summary and "restarting" in result.summary
    assert (await ex.run_verb("stop", {})).ok, "stop is never gated"
    assert (await ex.run_verb("report_state", {})).ok


# ── units ──────────────────────────────────────────────────────────────────────────────


def test_joint_goals_are_normalised_and_not_degrees() -> None:
    """The SO-101 adapter next door sets use_degrees=True and validates -180..180. This robot
    does not, so the same number means a different angle and the schema must differ."""
    from roboglot_xlerobot.verbs import MoveJointsParams

    assert MoveJointsParams(positions={"left_arm_shoulder_pan": 100.0}).positions
    with pytest.raises(ValueError, match="outside"):
        MoveJointsParams(positions={"left_arm_shoulder_pan": 150.0})
    with pytest.raises(ValueError, match="outside"):
        MoveJointsParams(positions={"left_arm_gripper": -1.0})
    with pytest.raises(ValueError, match="unknown joints"):
        MoveJointsParams(positions={"shoulder_pan": 10.0})
    with pytest.raises(ValueError, match="unknown joints"):
        MoveJointsParams(positions={"head_motor_1": 10.0})


def test_the_manifest_names_every_joint_the_robot_has_and_no_head() -> None:
    m = xlerobot_manifest("mock", camera=True)
    assert m.extras["joints"] == list(JOINTS)
    assert len(JOINTS) == 12
    assert not any("head" in j for j in JOINTS)
    assert all(j.startswith(("left_arm_", "right_arm_")) for j in JOINTS)


# ── the factory ────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("backend", ["mock", "zmq"])
def test_the_factory_builds_every_backend(backend: str) -> None:
    adapter = make_adapter(f"xlerobot:{backend}")
    assert adapter.name == "xlerobot" and adapter.backend == backend


async def test_the_zmq_backend_without_the_extra_names_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`pyzmq` is in the dev extra so the loopback test can run, so this cannot be a skipif on
    its absence the way the other adapters' are: it has to make the absence happen."""
    monkeypatch.setitem(sys.modules, "zmq", None)
    adapter = make_adapter("xlerobot:zmq", address="tcp://robot.local:5555")
    with pytest.raises(AdapterNotInstalled, match=r"roboglot\[xlerobot\]"):
        await adapter.connect()


class _BrokenLink:
    """A socket that has died in every way it can, without needing pyzmq to do it."""

    def open(self, host: str, cmd_port: int, obs_port: int) -> None:
        pass

    def send(self, text: str) -> None:
        raise RuntimeError("socket is gone")

    def recv(self, timeout_ms: int) -> str | None:
        return None

    def close(self) -> None:
        raise RuntimeError("already closed")


async def test_stop_never_raises_even_when_the_socket_does() -> None:
    """The executor calls `stop` exactly when things have gone wrong, and a dead socket raises
    its own library's error rather than one of ours. If that escaped, a stop would be reported
    as a failed verb and the reason the base actually stopped - the host's own watchdog - would
    be hidden behind it."""
    from roboglot_xlerobot.zmq_host import XLerobotZmq

    link = XLerobotZmq(address="tcp://127.0.0.1:5555", client=_BrokenLink(), connect_timeout_s=0.05)
    with pytest.raises(TransportError, match="no observation"):
        await link.connect()
    await link.stop()  # must not raise
    await link.close()  # must not raise


def test_an_unknown_backend_names_the_real_ones() -> None:
    with pytest.raises(ValueError, match="unknown xlerobot backend"):
        from roboglot_xlerobot import make

        make("serial")


@pytest.mark.parametrize(
    ("address", "expected"),
    [
        (None, "omni3"),
        ("tcp://10.0.0.4:5555", "omni3"),
        ("tcp://10.0.0.4:5555?variant=mecanum", "mecanum"),
        ("tcp://10.0.0.4:5555?variant=diff2", "diff2"),
        ("tcp://10.0.0.4:5555?variant=nonsense", "omni3"),
    ],
)
def test_the_variant_comes_off_the_address(address: str | None, expected: str) -> None:
    assert parse_variant(address) == expected


async def test_the_camera_composites_run_through_the_executor() -> None:
    """`search_scan`, `go_to` and `approach_and` are in this manifest whenever a camera is,
    so they get driven rather than assumed. They are also the only declared verbs that would
    move a 12 kg cart across a room, which makes them the last ones to leave untested."""
    adapter = XLerobotAdapter(XLerobotMock())
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


async def test_a_blind_cart_has_no_composites_to_run() -> None:
    """A stock XLeRobot ships with every camera commented out, so this is the common case
    rather than the odd one. The four camera verbs are absent from the registry, not refused."""
    adapter = XLerobotAdapter(XLerobotMock(camera=False))
    manifest = await adapter.connect()
    ex = _executor(adapter, manifest)
    for verb in ("observe", "search_scan", "go_to", "approach_and"):
        assert not manifest.provides(verb), verb
        with pytest.raises((VerbNotFound, VerbNotAllowed)):
            await ex.run_verb(verb, {})


async def test_the_colour_swap_is_reachable_from_the_address() -> None:
    """The detector every camera verb steers by does not fail loudly on a swapped frame, it
    quietly stops finding things, so the switch has to be reachable without editing Roboglot.
    The Open Duck Mini's camera daemon has had the same one since 0.5."""
    assert parse_swap_colour(None) is True
    assert parse_swap_colour("tcp://cart:5555") is True
    assert parse_swap_colour("tcp://cart:5555?swap_colour=1") is True
    for off in ("0", "false", "no", "off", "OFF"):
        assert parse_swap_colour(f"tcp://cart:5555?swap_colour={off}") is False, off
    # it composes with the flags already on the address rather than replacing them
    both = "tcp://cart:5555?variant=omni3&swap_colour=0"
    assert parse_variant(both) == "omni3" and parse_swap_colour(both) is False

    adapter = make_adapter("xlerobot:zmq", address="tcp://cart:5555?swap_colour=0")
    assert adapter.transport.swap_colour is False


# ── the contract, at the library level and at the exit code ────────────────────────────


def test_the_manifest_declares_exactly_this_set_and_no_more() -> None:
    full = xlerobot_manifest("zmq", camera=True)
    assert set(full.verb_names()) == {
        "report_state",
        "stop",
        "move",
        "move_joints",
        "gripper",
        "observe",
        "go_to",
        "approach_and",
        "search_scan",
    }
    blind = xlerobot_manifest("zmq", camera=False)
    assert set(blind.verb_names()) == {
        "report_state",
        "stop",
        "move",
        "move_joints",
        "gripper",
    }


def test_a_kicking_task_is_refused_against_this_cart_with_the_validators_words() -> None:
    problems = validate_glot(load_glot("find-and-kick"), [describe("mock", "xlerobot-01")])
    assert any("does not provide it" in p.message for p in problems), [p.message for p in problems]
    cli = runner.invoke(app, ["validate", "tasks/find-and-kick.glot", "--robot", "xlerobot:mock"])
    assert cli.exit_code == 1 and "does not provide it" in cli.output


def test_list_verbs_shows_the_real_set() -> None:
    result = runner.invoke(env=WIDE, app=app, args=["list-verbs", "--robot", "xlerobot:mock"])
    assert result.exit_code == 0
    names = _verb_column(result.output)
    assert {"move", "gripper", "observe"} <= names, names
    assert not names & {"kick", "quack", "say", "gaze", "lift", "perform"}, names


async def test_the_confirm_gated_verb_is_actually_gated() -> None:
    """`move_joints` declares `safety_class="confirm"` and nothing verified that declaration.
    It is the verb that moves twelve arm servos on a cart with no per-step clamp of its own,
    so the gate is the point of it being declared at all."""
    asked: list[str] = []

    def refuse(name: str, _params: dict[str, object]) -> bool:
        asked.append(name)
        return False

    adapter = XLerobotAdapter(XLerobotMock())
    manifest = await adapter.connect()
    mock = adapter.transport
    assert isinstance(mock, XLerobotMock)
    ex = Executor(
        registry_from_manifest(manifest, adapter),  # type: ignore[arg-type]
        adapter,
        contract=None,
        detector=ColorBlobDetector(),
        confirm=refuse,
    )
    with pytest.raises(ConfirmDenied, match="move_joints"):
        await ex.run_verb("move_joints", {"positions": {"left_arm_elbow_flex": 5.0}})
    assert asked == ["move_joints"]
    assert not mock.intents_of("joint"), "it never reached the robot"

    assert (await ex.run_verb("report_state", {})).ok
    assert asked == ["move_joints"], "a safe verb must not ask"
