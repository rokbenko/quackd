"""The MOSS adapter: a manifest narrowed by what answered, and a mock never kinder than the rover.

What this file holds the adapter to, against the mock and the pure parts of the package. The
real client against a fake cockpit is `tests/test_moss_cockpit.py`, the bridge on its own is
`tests/test_moss_bridge.py`, and the two halves of the bridge's protocol together are
`tests/test_moss_contract.py`. Nothing here has run on a rover.
"""

from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from quackd.adapters.base import AdapterError, RobotAdapter
from quackd.adapters.factory import make_adapter
from quackd.cli import app
from quackd.duckfile.parser import load_duck, parse_duck_text
from quackd.duckfile.schema import POLICY_VERBS
from quackd.duckfile.validate import validate_duck
from quackd.perception.color_blob import ColorBlobDetector
from quackd.safety import ConfirmDenied, Executor, VerbNotAllowed, allow_all
from quackd.transport.base import Intent, TransportError
from quackd.verbs.core import CORE
from quackd.verbs.core import MAX_VX as SCHEMA_VX
from quackd.verbs.core import MAX_VY as SCHEMA_VY
from quackd.verbs.core import MAX_WZ as SCHEMA_WZ
from quackd.verbs.registry import STOPPED, registry_from_manifest
from quackd_moss import (
    DATASHEET,
    MossAdapter,
    conditions,
    describe,
    implementations,
    make,
    moss_manifest,
    split_address,
)
from quackd_moss import upstream_api as up
from quackd_moss.cockpit import NEVER_POSTED, CockpitClient, MossCockpit
from quackd_moss.mock import (
    PICK_STARTUP_S,
    PULSE_S,
    STALL_S,
    TRACK_M,
    MossMock,
    pulse_rest_s,
    shape_twist,
    tracks_for,
)
from quackd_moss.verbs import (
    BATTERY_FLOOR_V,
    DRIVE_TO_MAX_M,
    MAX_WZ,
    PICK_POSE_NAMES,
    DriveToParams,
    PickAndDropParams,
    arm_note,
    moss_verbs,
    stack_refusal,
)

REPO = Path(__file__).resolve().parents[1]
PACKAGE = REPO / "adapters" / "moss" / "src" / "quackd_moss"
DOC = REPO / "docs" / "adapters" / "moss" / "README.md"
DUCK = load_duck("moss-lookout")

BASE = {"observe", "report_state", "stop"}
TWISTS = {"move", "go_to", "search_scan", "approach_and"}
PLANNER = {"drive_to", "explore"}
ARM = {"pick_and_drop", "stow_arm"}
OWN = {"drive_to", "explore", "collect_litter", "pick_and_drop", "stow_arm"}


def _contract(allow: str) -> Any:
    """A task file that allows `allow`: a dangerous verb runs under nothing less, since with no
    task file the executor allows every verb but a dangerous one."""
    return parse_duck_text(
        f"---\nduck: 0\nname: t\ndescription: d\nverbs:\n  allow: [{allow}]\n  confirm: []\n"
        "success: [x]\nabort_when: []\n---\n# Task\nx\n"
    ).frontmatter


def _executor(
    adapter: MossAdapter, manifest: Any, confirm: Any = allow_all, allow: str | None = None
) -> Executor:
    return Executor(
        registry_from_manifest(manifest, adapter),
        adapter,
        contract=None if allow is None else _contract(allow),
        detector=ColorBlobDetector(),
        confirm=confirm,
        manifest=manifest,
    )


def _a_person(answer: bool) -> Any:
    """A confirm marked the way the CLI marks the prompt a person at a terminal answers."""

    def says(_name: str, _params: dict[str, Any]) -> bool:
        return answer

    says.asks_a_person = True  # type: ignore[attr-defined]
    return says


async def _connected(
    allow: str | None = None, confirm: Any = allow_all, **kw: Any
) -> tuple[MossMock, MossAdapter, Executor]:
    mock = MossMock(**kw)
    adapter = MossAdapter(mock)
    manifest = await adapter.connect()
    return mock, adapter, _executor(adapter, manifest, confirm=confirm, allow=allow)


LITTER_ALLOW = "observe, report_state, stop, collect_litter"


# ── the manifest ────────────────────────────────────────────────────────────────────────


def test_describe_declares_every_stacks_verbs_together() -> None:
    """A task is validated against this before anything connects, so it is the union."""
    m = describe("cockpit")
    assert set(m.verb_names()) == BASE | TWISTS | PLANNER | ARM | {"collect_litter"}
    assert describe("mock").digest() == m.digest()


@pytest.mark.parametrize(
    ("stack", "bridge", "arm", "mission", "expected"),
    [
        ("moss-nav", True, True, True, BASE | TWISTS | ARM),
        ("moss-nav", True, False, True, BASE | TWISTS),
        ("moss-nav", False, True, True, BASE),
        ("moss-explore", True, True, True, BASE | TWISTS | PLANNER | ARM),
        ("moss-explore", False, False, False, BASE | PLANNER),
        ("moss-litter", True, True, True, BASE | {"collect_litter"}),
        ("moss-litter", True, True, False, BASE),
    ],
)
def test_each_stack_narrows_the_manifest_to_what_answered(
    stack: str, bridge: bool, arm: bool, mission: bool, expected: set[str]
) -> None:
    m = moss_manifest("cockpit", stack=stack, bridge=bridge, arm=arm, mission=mission)
    assert set(m.verb_names()) == expected
    assert ("twist" in m.intents) is bool(expected & TWISTS)
    assert ("pose" in m.intents) is ("drive_to" in expected)
    assert ("joint_state" in m.sensors) is (bridge and arm)


def test_a_rover_with_no_camera_loses_exactly_the_verbs_that_need_one() -> None:
    m = moss_manifest("cockpit", stack="moss-explore", camera=False)
    assert "camera" not in m.sensors
    assert not set(m.verb_names()) & {"observe", "go_to", "search_scan", "approach_and"}
    assert {"move", "drive_to", "explore"} <= set(m.verb_names())


def test_limits_are_the_schema_and_the_makers_own_and_say_no_sideways_motion() -> None:
    m = describe("cockpit")
    assert m.limits["max_vx"] == SCHEMA_VX < float(up.MPS_AT_LIMIT.name)
    assert m.limits["max_vy"] == 0.0 <= SCHEMA_VY
    assert m.limits["max_wz"] == float(up.SERVO_W_MAX.name) == MAX_WZ <= SCHEMA_WZ
    assert "camera_fov_deg" not in m.limits, "no lens is declared until camera_info is heard"
    told = moss_manifest("cockpit", stack="moss-explore", fov_deg=86.0)
    assert told.limits["camera_fov_deg"] == 86.0


def test_the_deadman_is_claimed_only_where_the_bridge_runs_one() -> None:
    with_bridge = moss_manifest("cockpit", stack="moss-explore", bridge=True)
    without = moss_manifest("cockpit", stack="moss-explore", bridge=False)
    assert with_bridge.safety_authority.deadman is True
    assert without.safety_authority.deadman is False, "a wrong deadman: true is not honest"
    assert with_bridge.safety_authority.native == without.safety_authority.native == "none"
    assert "three missed keepalives" in with_bridge.extras["deadman_scope"]
    assert without.extras["deadman_scope"].startswith("none")
    assert any("finishes a goal" in a for a in without.extras["assumptions"])


@pytest.mark.parametrize(
    ("bridge", "arm", "seen", "said", "unsaid"),
    [
        (True, False, False, "nothing holds the arm", "found none"),
        (True, False, True, "found none", "nothing holds the arm"),
        (False, False, None, "nothing holds the arm", "found none"),
        (True, True, True, None, "nothing holds the arm"),
    ],
)
def test_the_manifest_says_the_arm_is_unseen_only_when_it_is(
    bridge: bool, arm: bool, seen: bool | None, said: str | None, unsaid: str
) -> None:
    m = moss_manifest("cockpit", stack="moss-explore", bridge=bridge, arm=arm, arm_seen=seen)
    told = " ".join(m.extras["assumptions"])
    assert said is None or said in told
    assert unsaid not in told
    assert not {"pick_and_drop", "stow_arm"} & set(m.verb_names()) or (bridge and arm)
    assert ("joint_state" in m.sensors) is bool(bridge and (arm if seen is None else seen))


def test_approach_and_has_time_for_a_go_to_and_the_arm_cycle() -> None:
    spec = describe("cockpit").verb("approach_and")
    assert spec is not None and spec.timeout_s is not None
    cycle = moss_verbs()["pick_and_drop"].timeout_s
    assert spec.timeout_s >= CORE["go_to"].timeout_s + cycle
    assert spec.timeout_s <= 600


def test_the_safety_classes_are_the_ones_decided() -> None:
    m = describe("cockpit")
    classes = {v.name: v.safety_class for v in m.verbs if v.name in OWN}
    assert classes == {
        "drive_to": "safe",
        "explore": "confirm",
        "pick_and_drop": "confirm",
        "stow_arm": "confirm",
        "collect_litter": "dangerous",
    }
    assert not OWN & set(POLICY_VERBS), "pick is a policy verb and is never one of these"


def test_the_datasheet_is_the_makers_and_leaves_out_what_nobody_published() -> None:
    ds = DATASHEET
    assert (
        ds.mass_kg is not None and ds.mass_kg.value == 4.2 and ds.mass_kg.confidence == "official"
    )
    assert "768 g" in ds.mass_kg.note
    assert ds.dof is not None and ds.dof.value == 6
    assert (ds.manipulator, ds.arms, ds.tethered) == ("gripper", 1, False)
    assert ds.payload_kg is None and ds.reach_m is None and ds.endurance_min is None
    assert ds.height_m is None
    assert {"slide sideways", "turn slowly on the spot", "reach for an object"} == {
        c.split(":")[0] for c in ds.cannot
    }
    assert set(ds.unknown()) == {"height", "payload", "reach", "endurance"}


def test_digest_ignores_the_id_and_the_backend() -> None:
    a = moss_manifest("mock", "one", stack="moss-nav")
    b = moss_manifest("cockpit", "two", stack="moss-nav")
    assert a.digest() == b.digest()
    assert a.digest() != moss_manifest("mock", "one", stack="moss-explore").digest()


def test_the_pick_poses_are_the_cycles_own() -> None:
    literal = PickAndDropParams.model_json_schema()["properties"]["pick_pose"]["enum"]
    assert tuple(literal) == PICK_POSE_NAMES == ("pick", "pick_low")
    assert PickAndDropParams().pick_pose == "pick", "every parameter defaulted, for approach_and"


# ── refusals at connect ─────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("kw", "said"),
    [
        ({"running": False, "blueprint": ""}, "no stack is running"),
        ({"blueprint": "unitree-go2"}, "not one of MOSS's"),
        ({"blueprint": "moss-gamepad"}, "the pad and the base alone"),
        ({"blueprint": "moss-base"}, "the pad and the base alone"),
        ({"blueprint": "moss-arm"}, "reads only the newest dimOS run, and that is moss-arm"),
        ({"blueprint": "moss-jev"}, "quackd knows moss-nav"),
        ({"bus": "zenoh"}, "speak LCM only"),
        ({"blueprint": "moss-litter", "bridge": False}, "only quackd's bridge"),
        ({"blueprint": "moss-litter", "mission": False}, "nothing could stop it"),
    ],
)
def test_a_stack_quackd_cannot_drive_is_refused_with_its_fix(kw: dict[str, Any], said: str) -> None:
    args: dict[str, Any] = {
        "running": True,
        "blueprint": "moss-explore",
        "bus": "lcm",
        "bridge": True,
        "mission": True,
    }
    args.update(kw)
    refusal = stack_refusal(**args)
    assert refusal is not None and said in refusal


@pytest.mark.parametrize("stack", ["moss-nav", "moss-explore", "moss-litter"])
def test_the_stacks_quackd_drives_are_not_refused(stack: str) -> None:
    assert (
        stack_refusal(running=True, blueprint=stack, bus="lcm", bridge=True, mission=True) is None
    )


async def test_the_mock_refuses_what_the_rover_refuses() -> None:
    for kw in ({"stack": "moss-gamepad"}, {"stack": "moss-litter", "bridge": False}):
        with pytest.raises(TransportError, match="moss: "):
            await MossAdapter(MossMock(**kw)).connect()  # type: ignore[arg-type]


# ── the factory ─────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("backend", ["mock", "cockpit"])
def test_the_factory_builds_every_backend_with_no_address(backend: str) -> None:
    adapter = make_adapter(f"moss:{backend}")
    assert isinstance(adapter, RobotAdapter)
    assert (adapter.name, adapter.backend) == ("moss", backend)


def test_the_address_carries_the_bridge_port_and_never_the_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert split_address(None) == ("127.0.0.1", 8904, 9876)
    assert split_address("http://rover.local:8905?bridge=9999") == ("rover.local", 8905, 9999)
    assert split_address("10.0.0.7") == ("10.0.0.7", 8904, 9876)
    monkeypatch.setenv("QUACKD_MOSS_TOKEN", "from-the-environment")
    built = make("cockpit", address="http://rover.local:8904")
    transport = built.transport
    assert isinstance(transport, MossCockpit)
    assert transport.bridge.token == "from-the-environment"
    assert make("cockpit", token="typed").transport.bridge.token == "typed"  # type: ignore[attr-defined]


def test_make_refuses_a_camera_url_and_a_rest_pose() -> None:
    with pytest.raises(AdapterError, match="takes no --camera-url"):
        make("cockpit", camera_url=("http://cam/x.jpg",))
    with pytest.raises(AdapterError, match="does not return to a rest pose"):
        make("mock", rest_pose={"arm/a": 0.0})
    with pytest.raises(ValueError, match="unknown moss backend"):
        make("sim2d")


def test_the_mock_reads_its_knobs_off_the_address() -> None:
    mock = MossMock.from_address("mock://?stack=moss-litter&bridge=1&arm=0&litter=2&wall=1.5")
    assert (mock.stack, mock.bridge_up, mock.arm_up, mock.wall_x) == (
        "moss-litter",
        True,
        False,
        1.5,
    )
    assert len(mock.mission.litter) == 2
    assert MossMock.from_address(None).stack == "moss-explore"


# ── every verb, through a real executor ─────────────────────────────────────────────────


async def test_every_verb_runs_offline_through_the_executor() -> None:
    mock, adapter, ex = await _connected()
    for name, params in [
        ("report_state", {}),
        ("observe", {}),
        ("move", {"vx": 0.2, "duration_s": 1.0}),
        ("move", {"vx": 0.0, "wz": 0.4, "duration_s": 2.0}),
        ("search_scan", {"target": "ball", "max_steps": 4}),
        ("go_to", {"target": "ball", "stop_distance": 0.5}),
        ("approach_and", {"target": "ball", "stop_distance": 0.4, "then": "pick_and_drop"}),
        ("stow_arm", {}),
        ("drive_to", {"x_m": 0.5, "y_m": 0.0}),
        ("explore", {"minutes": 0.1}),
        ("stop", {}),
    ]:
        result = await ex.run_verb(name, params)
        assert result.ok, f"{name}: {result.summary}"
    assert mock.pulses > 0, "a spin goes out as the maker's pulses"
    await adapter.close()


async def test_a_read_only_verb_sends_nothing() -> None:
    mock, adapter, ex = await _connected()
    registry = registry_from_manifest(adapter.manifest, adapter)  # type: ignore[arg-type]
    read_only = [v.name for v in registry.verbs() if v.read_only]
    assert set(read_only) == {"observe", "report_state"}
    for name in read_only:
        assert (await ex.run_verb(name)).ok
    assert mock.intents == []


async def test_the_gated_verbs_ask_and_a_no_stops_them() -> None:
    asked: list[str] = []

    def refuse(name: str, _params: dict[str, Any]) -> bool:
        asked.append(name)
        return False

    refuse.asks_a_person = True  # type: ignore[attr-defined]
    _, adapter, _ = await _connected(stack="moss-explore")
    ex = _executor(adapter, adapter.manifest, confirm=refuse)
    for verb in ("explore", "pick_and_drop", "stow_arm"):
        with pytest.raises(ConfirmDenied, match=verb):
            await ex.run_verb(verb, {})
    _, litter, _ = await _connected(stack="moss-litter", litter=[])
    ex = _executor(litter, litter.manifest, confirm=refuse, allow=LITTER_ALLOW)
    with pytest.raises(ConfirmDenied, match="collect_litter"):
        await ex.run_verb("collect_litter", {})
    assert asked == ["explore", "pick_and_drop", "stow_arm", "collect_litter"]


@pytest.mark.parametrize("asker", [allow_all, _a_person(True)], ids=["--yes", "a person"])
async def test_collect_litter_runs_only_when_a_person_answers_it(asker: Any) -> None:
    """The one dangerous verb quackd ships: `--yes`, a flock's standing answer and the MCP
    server never clear it, and nothing is started for the mission before it is refused. A
    person at a terminal does clear it."""
    mock, adapter, _ = await _connected(stack="moss-litter", litter=[(0.4, 0.0)])
    ex = _executor(adapter, adapter.manifest, confirm=asker, allow=LITTER_ALLOW)
    if asker is allow_all:
        with pytest.raises(VerbNotAllowed, match="only a person can clear it"):
            await ex.run_verb("collect_litter", {"minutes": 1})
        assert mock.started is None and not mock.mission_stopped, "nothing was started"
    else:
        result = await ex.run_verb("collect_litter", {"minutes": 1})
        assert result.ok, result.summary
        assert mock.mission_stopped


async def test_collect_litter_waits_for_the_arm_in_its_drive_pose() -> None:
    """The mission drives off as soon as it starts, so an arm out of its drive pose refuses
    collect_litter, with how to stow it on a stack where quackd stows nothing, and nothing is
    started for the mission."""
    mock, adapter, _ = await _connected(stack="moss-litter", litter=[(0.4, 0.0)])
    ex = _executor(adapter, adapter.manifest, confirm=_a_person(True), allow=LITTER_ALLOW)
    mock.arm_folded = False
    result = await ex.run_verb("collect_litter", {"minutes": 1})
    assert not result.ok and "--from back_to_clear" in result.summary, result.summary
    assert mock.started is None and not mock.mission_stopped


async def test_a_dangerous_verb_is_out_of_a_session_with_no_task_file() -> None:
    """With no `.duck`, the executor allows every verb but a dangerous one."""
    _, _, ex = await _connected(stack="moss-litter", litter=[])
    assert "collect_litter" not in ex.allowed
    assert {"observe", "report_state", "stop"} <= set(ex.allowed)


# ── the gates ───────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("volts", [10.75, 9.9])
async def test_a_battery_under_the_launchers_floor_refuses_every_drive(volts: float) -> None:
    _, _, ex = await _connected(battery_v=volts)
    for verb, params in (("move", {"vx": 0.1}), ("drive_to", {"x_m": 0.3, "y_m": 0.0})):
        result = await ex.run_verb(verb, params)
        assert not result.ok and f"{BATTERY_FLOOR_V:g} V" in result.summary, result.summary
    assert (await ex.run_verb("stop", {})).ok


async def test_an_unreadable_battery_refuses_rather_than_assumes() -> None:
    _, _, ex = await _connected(battery_v=None)
    result = await ex.run_verb("move", {"vx": 0.1})
    assert not result.ok and "cannot be read" in result.summary


@pytest.mark.parametrize("age", [2.5, 30.0])
async def test_stale_telemetry_refuses_and_says_the_coordinator_stopped(age: float) -> None:
    mock, _, ex = await _connected()
    mock.telemetry_age_s = age
    result = await ex.run_verb("move", {"vx": 0.1})
    assert not result.ok and "stopped publishing" in result.summary


async def test_a_motor_board_lost_behind_a_live_coordinator_is_refused() -> None:
    mock, _, ex = await _connected()
    mock.firmware_age_s = 4.0
    result = await ex.run_verb("explore", {"minutes": 0.1})
    assert not result.ok and "board lost" in result.summary


async def test_a_stale_pose_refuses_a_goal() -> None:
    mock, _, ex = await _connected()
    mock.pose_age_s = 9.0
    result = await ex.run_verb("drive_to", {"x_m": 0.3, "y_m": 0.0})
    assert not result.ok and "odometry has stopped" in result.summary


async def test_an_arm_out_of_its_drive_pose_keeps_the_base_still() -> None:
    mock, _, ex = await _connected()
    mock.arm_folded = False
    for verb, params in (("move", {"vx": 0.1}), ("explore", {"minutes": 0.1})):
        result = await ex.run_verb(verb, params)
        assert not result.ok and "stow_arm first" in result.summary
    assert (await ex.run_verb("stow_arm", {})).ok
    assert (await ex.run_verb("move", {"vx": 0.1, "duration_s": 0.2})).ok


@pytest.mark.parametrize("wall", [0.3, 0.6])
async def test_a_stall_refuses_the_planner_and_leaves_move_to_back_away(wall: float) -> None:
    mock, _, ex = await _connected(wall_x=wall)
    await ex.run_verb("move", {"vx": 0.3, "duration_s": wall / 0.3 + STALL_S + 1.0})
    assert mock.stalled and mock.x < wall
    result = await ex.run_verb("drive_to", {"x_m": -0.3, "y_m": 0.0})
    assert not result.ok and "stall guard" in result.summary
    assert (await ex.run_verb("move", {"vx": -0.2, "duration_s": 0.5})).ok
    assert not mock.stalled, "the next accepted twist clears the stall, as upstream's adapter does"


# ── the body: no slow pivot, the floor, the ramp, an odometry that lags ─────────────────


@pytest.mark.parametrize("wz", [0.05, 0.4, -1.0])
def test_any_spin_on_the_spot_runs_both_tracks_at_full_power(wz: float) -> None:
    left, right = tracks_for(0.0, wz)
    assert abs(left) == abs(right) == int(up.PIVOT_PCT.name)
    assert (right > 0) is (wz > 0), "+wz turns left: the right track runs forward"


@pytest.mark.parametrize("vx", [0.01, 0.03])
def test_a_slow_command_is_raised_to_the_floor(vx: float) -> None:
    left, right = tracks_for(vx, 0.0)
    assert left == right == int(up.MIN_PCT_FLOOR.name)


@pytest.mark.parametrize(("vx", "wz"), [(0.05, 0.4), (0.1, 1.0), (-0.08, 0.9)])
def test_an_arc_whose_inner_track_would_reverse_is_a_full_power_pivot(vx: float, wz: float) -> None:
    """Which is why the bridge clips an arc first: core go_to's (0.05, 0.4) is one of these."""
    left, right = tracks_for(vx, wz)
    assert left * right < 0 and abs(left) == abs(right) == int(up.PIVOT_PCT.name)
    svx, swz, clipped = shape_twist(vx, wz)
    assert clipped and abs(swz) <= 2 * abs(svx) / TRACK_M + 1e-12
    shaped = tracks_for(svx, swz)
    assert shaped[0] * shaped[1] >= 0, "after the clip the inner track never reverses"


@pytest.mark.parametrize("wz", [0.4, 0.2, 0.1])
def test_a_spin_rests_longer_the_slower_it_was_asked_for(wz: float) -> None:
    assert pulse_rest_s(wz) == pytest.approx(float(up.REST_S.name) * MAX_WZ / wz)
    assert float(up.PULSE_S.name) == PULSE_S


async def test_the_ramp_means_a_short_pulse_never_reaches_full_power() -> None:
    mock, adapter, _ = await _connected()
    await adapter.send_intent(Intent.move(0.0, 0.0, MAX_WZ))
    peak = 0.0
    for _ in range(round(PULSE_S / 0.01)):
        await mock.sleep(0.01)
        peak = max(peak, *(abs(v) for v in mock.out))
    assert 0 < peak < int(up.PIVOT_PCT.name), "asked for a full power pivot, it got this far"


@pytest.mark.parametrize("gain", [0.8, 1.0])
async def test_the_odometry_under_reads_a_pivot_by_its_gain(gain: float) -> None:
    mock, _, ex = await _connected(odom_gain=gain)
    await ex.run_verb("move", {"vx": 0.0, "wz": MAX_WZ, "duration_s": 3.0})
    assert mock.yaw > 0
    if gain < 1.0:
        assert abs(mock.oyaw) < abs(mock.yaw)
    else:
        assert mock.oyaw == pytest.approx(mock.yaw)


# ── drive_to and explore ────────────────────────────────────────────────────────────────


def test_a_goal_further_than_two_metres_is_refused_in_the_params_or_by_its_length() -> None:
    with pytest.raises(ValueError):
        DriveToParams(x_m=DRIVE_TO_MAX_M + 0.1, y_m=0.0)
    assert DriveToParams(x_m=2.0, y_m=0.0).frame == "body"


@pytest.mark.parametrize(("x", "y"), [(1.6, 1.6), (2.0, 0.5)])
async def test_drive_to_refuses_a_goal_beyond_its_cap_before_sending_it(x: float, y: float) -> None:
    mock, _, ex = await _connected()
    result = await ex.run_verb("drive_to", {"x_m": x, "y_m": y})
    assert not result.ok and "at most 2 m" in result.summary
    assert mock.intents_of("pose") == []


@pytest.mark.parametrize("frame", ["body", "world"])
async def test_drive_to_reads_its_offset_in_the_frame_it_names(frame: str) -> None:
    """After a turn the two frames differ: body is forward of the rover, world is the map's x."""
    mock, adapter, ex = await _connected()
    await ex.run_verb("move", {"vx": 0.0, "wz": MAX_WZ, "duration_s": 4.0})
    before = await adapter.get_state()
    assert before.theta and abs(before.theta) > 0.1
    await ex.run_verb("drive_to", {"x_m": 0.5, "y_m": 0.0, "frame": frame})
    sent = mock.intents_of("pose")[-1].params
    assert sent["frame"] == "world"
    if frame == "world":
        assert (sent["x"], sent["y"]) == pytest.approx((before.x + 0.5, before.y))
    else:
        assert sent["x"] == pytest.approx(before.x + 0.5 * math.cos(before.theta))
        assert sent["y"] == pytest.approx(before.y + 0.5 * math.sin(before.theta))


async def test_drive_to_waits_out_the_cockpits_deferral_while_exploring() -> None:
    """With no arm's run newer than the base, the cockpit sees the exploration and defers."""
    mock, _, ex = await _connected(arm=False)
    mock.exploring = True
    result = await ex.run_verb("drive_to", {"x_m": 0.4, "y_m": 0.0})
    assert result.ok, result.summary
    assert result.data["seconds"] >= float(up.GOAL_AFTER_STOP_S.name)
    assert [i.params.get("action") for i in mock.intents_of("do")] == [], "the cockpit stopped it"


async def test_drive_to_stops_an_exploration_the_cockpit_cannot_see() -> None:
    """moss-arm runs after the base stack and the cockpit reads only the newest run: it sees no
    exploration and would send the goal straight under the explorer, whose next frontier then
    replaces it. The bridge reads the base stack's own nav, so drive_to sees the exploration,
    stops it itself and waits out the explorer's stay here goal before it sends its own."""
    mock, adapter, ex = await _connected()
    mock.exploring = True
    state = await adapter.get_state()
    assert state.extras["nav"]["exploring"] and not state.extras["cockpit_nav"]["exploring"]
    result = await ex.run_verb("drive_to", {"x_m": 0.4, "y_m": 0.0})
    assert result.ok, result.summary
    kinds = [(i.kind, i.params.get("action")) for i in mock.intents if i.kind in ("do", "pose")]
    assert kinds[:2] == [("do", "stop"), ("pose", None)], kinds


async def test_drive_to_fails_when_the_planner_gives_the_goal_up() -> None:
    mock, _, ex = await _connected(goal_refused=True)
    result = await ex.run_verb("drive_to", {"x_m": 1.0, "y_m": 0.0})
    assert not result.ok and "gave the goal up" in result.summary
    assert mock.stop_log[-1] == "cockpit.stop", "it ends with a stop whatever happens"


async def test_explore_ends_with_the_explorers_stop_and_a_soft_stop() -> None:
    mock, _, ex = await _connected()
    result = await ex.run_verb("explore", {"minutes": 0.05})
    assert result.ok, result.summary
    stops = [i for i in mock.intents_of("do") if i.params.get("action") == "stop"]
    assert stops and mock.stop_log[-1] == "cockpit.stop"
    assert not mock.exploring


async def test_explore_hears_a_brake_pulled_beside_it_within_a_second() -> None:
    from quackd.verbs.registry import VerbContext
    from quackd_moss.verbs import MinutesParams, explore

    mock = MossMock()
    adapter = MossAdapter(mock)
    await adapter.connect()
    asked = {"n": 0}

    def interrupted() -> str | None:
        asked["n"] += 1
        return STOPPED if asked["n"] > 3 else None

    ctx = VerbContext(transport=adapter, interrupted=interrupted)
    result = await explore(ctx, MinutesParams(minutes=8))
    assert not result.ok and STOPPED in result.summary
    assert result.data["seconds"] <= 4.0
    assert not mock.exploring and mock.stop_log[-1] == "cockpit.stop"


async def test_explore_stops_when_the_explorer_finds_nothing_left() -> None:
    _, _, ex = await _connected(explore_done_after_s=3.0)
    result = await ex.run_verb("explore", {"minutes": 1})
    assert result.ok and result.data["explore_done"] is True
    assert result.data["seconds"] < 10


# ── the arm ─────────────────────────────────────────────────────────────────────────────


async def test_pick_and_drop_reports_an_arm_lag_as_its_ending() -> None:
    mock, _, ex = await _connected(arm_lag_at_s=5.0)
    result = await ex.run_verb("pick_and_drop", {"pick_pose": "pick_low"})
    assert not result.ok and "ended lag" in result.summary
    assert mock.intents_of("do")[-1].params["pick_pose"] == "pick_low"
    assert mock.stop_log[-1] == "cockpit.stop", "and the body stopped after it"


async def test_a_cycle_whose_script_dies_ends_with_the_body_stopped_and_the_arm_read() -> None:
    """The script dies any way but its own and its trajectory plays on: the bridge cancels it
    and reads the arm, and the verb stops the body before it says how the cycle ended."""
    mock, _, ex = await _connected(arm_dies_at_s=3.0)
    result = await ex.run_verb("pick_and_drop", {})
    assert not result.ok and "ended failed" in result.summary, result.summary
    assert "The arm holds where it stopped." in result.summary
    assert not mock.arm_moving and mock.job is not None and mock.job.held is True
    assert mock.stop_log[-3:] == [
        "twist.zero",
        "arm.cancel",
        "cockpit.stop",
    ], "the body stopped after it"


@pytest.mark.parametrize(
    ("held", "said", "unsaid"),
    [
        (True, "holding where the run left it", "nobody read"),
        (False, "still moving when it was last read", "holding"),
        (None, "nobody read whether it holds", "holding"),
    ],
)
def test_the_close_says_holding_only_after_the_hold_was_read(
    held: bool | None, said: str, unsaid: str
) -> None:
    note = arm_note(held)
    assert said in note and unsaid not in note


async def test_stow_arm_plays_the_cycle_from_its_return() -> None:
    mock, _, ex = await _connected()
    assert (await ex.run_verb("stow_arm", {})).ok
    sent = mock.intents_of("do")[-1].params
    assert sent["from_phase"] == up.CYCLE_FROM_BACK_TO_CLEAR.name == "back_to_clear"


async def test_a_stop_mid_cycle_cancels_it_and_the_arm_holds() -> None:
    from quackd.verbs.registry import VerbContext
    from quackd_moss.verbs import pick_and_drop

    mock = MossMock()
    adapter = MossAdapter(mock)
    await adapter.connect()
    asked = {"n": 0}

    def interrupted() -> str | None:
        asked["n"] += 1
        return STOPPED if asked["n"] > 6 else None

    result = await pick_and_drop(
        VerbContext(transport=adapter, interrupted=interrupted), PickAndDropParams()
    )
    assert not result.ok and "holds where it stopped" in result.summary
    assert mock.job is not None and (mock.job.state, mock.job.held) == ("cancelled", True)
    assert "arm.cancel" in mock.stop_log


async def test_the_arm_and_a_twist_never_run_together() -> None:
    mock, adapter, _ = await _connected()
    assert (await adapter.send_intent(Intent.move(0.1, 0.0, 0.0))).accepted
    refused = await adapter.send_intent(
        Intent(kind="do", params={"skill": "arm_cycle", "pick_pose": "pick"})
    )
    assert not refused.accepted and "twist" in (refused.reason or "")
    await mock.sleep(1.0)
    assert (
        await adapter.send_intent(
            Intent(kind="do", params={"skill": "arm_cycle", "pick_pose": "pick"})
        )
    ).accepted
    moved = await adapter.send_intent(Intent.move(0.1, 0.0, 0.0))
    assert not moved.accepted and "cycle" in (moved.reason or "")


# ── the stop contract ───────────────────────────────────────────────────────────────────


async def test_stop_is_the_bridges_zero_then_its_cancel_and_the_soft_stop_every_time() -> None:
    mock, adapter, _ = await _connected()
    await adapter.stop()
    assert mock.stop_log[-3:] == ["twist.zero", "arm.cancel", "cockpit.stop"]


@pytest.mark.parametrize("fails", ["bridge", "cockpit"])
async def test_a_stop_that_failed_on_one_surface_still_ran_the_other_and_says_so(
    fails: str,
) -> None:
    mock, _, ex = await _connected(stop_fails=fails)
    result = await ex.run_verb("stop", {})
    assert not result.ok and "could not be delivered" in result.summary
    other = "cockpit.stop" if fails == "bridge" else "twist.zero"
    assert other in mock.stop_log


async def _picking(mock: MossMock, state: str) -> None:
    """Until the mission's own pick is in `state`: starting, its script still reading the
    joints, or running, its trajectory out."""
    while mock.job is None or mock.job.owner != "mission" or mock.job.state != state:
        await mock.sleep(0.25)


async def test_on_moss_litter_the_mission_stops_before_the_arm_is_cancelled() -> None:
    mock, adapter, _ = await _connected(stack="moss-litter", litter=[(0.4, 0.0)])
    await _picking(mock, "running")
    await adapter.stop()
    assert mock.stop_log[:4] == ["mission.stop", "pick.script", "arm.cancel", "twist.zero"]
    await mock.sleep(5.0)
    assert not mock.exploring, "a stopped mission explores nothing after its cancelled pick"


async def test_on_moss_litter_a_stop_ends_the_missions_pick_while_its_script_is_starting() -> None:
    """Inside the script's startup a cancel finds nothing executing, and the mission's stop
    leaves the script running (`up.MISSION_STOP_LEAVES_PICK`): only ending it keeps its
    trajectory from going out after the stop."""
    mock, adapter, _ = await _connected(stack="moss-litter", litter=[(0.4, 0.0)])
    await _picking(mock, "starting")
    await adapter.stop()
    await mock.sleep(PICK_STARTUP_S + 5.0)
    assert mock.job is not None and mock.job.state == "failed", mock.job
    assert mock.arm_folded, "the trajectory never went out, so the arm never left its pose"
    assert mock.stop_log[:3] == ["mission.stop", "pick.script", "arm.cancel"]


async def test_cancelling_the_arm_before_the_mission_is_the_race_the_order_prevents() -> None:
    """The mock reproduces upstream's race on purpose: a pick cancelled under a mission that is
    still running is a failed pick, and the mission's next tick explores again."""
    mock, _, _ = await _connected(stack="moss-litter", litter=[(0.4, 0.0)])
    await _picking(mock, "running")
    mock._arm_cancel()
    await mock.sleep(2.0)
    assert mock.exploring and mock.mission.skipped == 1


async def test_the_soft_stop_does_not_end_the_mission() -> None:
    mock, _, _ = await _connected(stack="moss-litter", litter=[(0.6, 0.0)])
    await mock.sleep(float(up.STOP_SETTLE_S.name) + 0.5)
    assert mock.mission.state == "APPROACHING"
    mock._cockpit_stop()
    await mock.sleep(1.0)
    assert mock.mission.state == "PICKING", "it listens to no stop of the cockpit's"


async def test_collect_litter_runs_once_per_stack_start() -> None:
    mock, _, ex = await _connected(
        LITTER_ALLOW, confirm=_a_person(True), stack="moss-litter", litter=[(0.4, 0.0)]
    )
    first = await ex.run_verb("collect_litter", {"minutes": 1})
    assert first.ok, first.summary
    assert "runs again only after the stack restarts" in first.summary
    assert mock.mission_stopped
    second = await ex.run_verb("collect_litter", {"minutes": 1})
    assert not second.ok and "restart the stack" in second.summary


async def test_a_dropped_link_halts_what_quackd_started_and_nothing_else() -> None:
    """The bridge halts what the last keepalive it took said the session started. The start
    was said before it went out, so a link that drops the instant after still halts it."""
    mock, adapter, _ = await _connected()
    await adapter.send_intent(Intent(kind="do", params={"skill": "explore", "action": "start"}))
    assert mock.exploring and mock.started == "explore"
    mock.drop_link()
    await mock.sleep(2.0)  # past the session's window
    assert mock.session_halts == 1 and not mock.exploring
    assert mock.stop_log[-2:] == ["session.halt", "halt.started"]
    idle, _, _ = await _connected()
    idle.exploring = True  # the owner's own exploration, from the cockpit's page
    idle.drop_link()
    await idle.sleep(2.0)
    assert idle.session_halts == 1
    assert idle.exploring, "an exploration quackd did not start is not quackd's to halt"


async def test_a_session_kept_by_its_thread_never_reads_as_dead() -> None:
    """No heartbeat at all from the loop, a prompt blocking it, and the session lives on: its
    keepalives are the transport's own thread's. Only a dead link or process ends it."""
    mock, adapter, _ = await _connected()
    await adapter.send_intent(Intent(kind="do", params={"skill": "explore", "action": "start"}))
    await mock.sleep(10.0)
    assert mock.session_halts == 0 and mock.exploring


@pytest.mark.parametrize("limit", [30.0, 90.0])
async def test_the_idle_stop_ends_every_run_and_the_arms_torque_unless_kept_alive(
    limit: float,
) -> None:
    """quackd sends the cockpit's keepalive while it is connected, from its own thread, and
    nothing does once it has gone."""
    kept, _, _ = await _connected(idle_limit_s=limit)
    await kept.sleep(limit * 1.5)
    assert kept.stack_running and kept.cockpit_keepalives >= 4
    left, adapter, _ = await _connected(idle_limit_s=limit)
    await adapter.close()
    await left.sleep(limit + 1.0)
    assert left.idle_stopped and not left.stack_running and not left.arm_torque


async def test_close_says_when_the_arm_it_moved_is_left_holding_out_of_its_drive_pose() -> None:
    mock, adapter, ex = await _connected(arm_lag_at_s=5.0)
    result = await ex.run_verb("pick_and_drop", {})
    assert not result.ok and not mock.arm_folded, "the lag left the arm out"
    await adapter.close()
    note = adapter.close_note or ""
    assert "idle stop" in note and "torque off" in note and "stow_arm" in note
    untouched, other, _ = await _connected()
    untouched.arm_folded = False  # left out before this session, which moves nothing
    await other.close()
    assert other.close_note is None, "an arm this session never moved is not its to report"


@pytest.mark.parametrize(("fails", "said"), [(None, "is stopped"), ("bridge", "not confirmed")])
async def test_close_on_moss_litter_says_the_mission_stopped_only_if_it_did(
    fails: str | None, said: str
) -> None:
    """After the stop a run ends with, which is what ends the mission."""
    mock, adapter, _ = await _connected(stack="moss-litter", stop_fails=fails)
    await adapter.stop()
    await adapter.close()
    assert said in (adapter.close_note or ""), adapter.close_note
    assert mock.mission_stopped is (fails is None)


async def test_on_moss_litter_a_session_that_started_nothing_leaves_the_mission_running() -> None:
    """A probe's connect, health and close, `quackd doctor`'s: no stop, no note, and no session
    left for the bridge's deadman, which on moss-litter is the whole stop, however long the probe
    takes to close."""
    mock, adapter, _ = await _connected(stack="moss-litter", litter=[(0.6, 0.0)])
    assert (await adapter.health()).ok
    await mock.sleep(5.0)
    await adapter.close()
    assert adapter.close_note is None
    assert mock.stop_log == [] and mock.session_halts == 0
    assert mock.mission.running and not mock.mission_stopped


async def test_on_moss_litter_close_stops_a_mission_this_session_started() -> None:
    mock, adapter, _ = await _connected(stack="moss-litter")
    start = Intent(kind="do", params={"skill": "collect_litter", "action": "start"})
    assert (await adapter.send_intent(start)).accepted
    await adapter.close()
    assert mock.stop_log[0] == "mission.stop" and mock.mission_stopped
    assert "litter mission is stopped" in (adapter.close_note or "")


async def test_close_halts_its_own_twist_goal_and_cycle_and_nothing_else() -> None:
    """What `_halt_own` sends, on the mock as on the rover: the zero for a twist still fresh,
    the soft stop for a goal it set, the cancel for its own cycle, and for nothing it did not
    start not a thing."""
    mock, adapter, _ = await _connected()
    assert (await adapter.send_intent(Intent.move(0.1, 0.0, 0.0))).accepted
    await adapter.close()
    assert mock.stop_log == ["twist.zero"], "a zero, and no soft stop: it set no goal"
    mock, adapter, _ = await _connected()
    goal = Intent(kind="pose", params={"x": 1.0, "y": 0.0, "frame": "world"})
    assert (await adapter.send_intent(goal)).accepted
    await adapter.close()
    assert mock.stop_log == ["cockpit.stop"] and mock.goal is None
    mock, adapter, _ = await _connected()
    cycle = Intent(kind="do", params={"skill": "arm_cycle", "pick_pose": "pick"})
    assert (await adapter.send_intent(cycle)).accepted
    await adapter.close()
    assert mock.stop_log == ["arm.cancel"] and mock.job is not None
    assert (mock.job.state, mock.job.held) == ("cancelled", True)
    mock, adapter, _ = await _connected()
    mock.goal, mock.exploring = (1.0, 0.0), True  # the owner's, from the cockpit's own page
    await adapter.close()
    assert mock.stop_log == [] and mock.goal == (1.0, 0.0) and mock.exploring


def test_doctor_on_moss_litter_is_healthy_and_leaves_the_mission_running() -> None:
    result = CliRunner().invoke(
        app, ["doctor", "--robot", "moss:mock", "--address", "mock://rover?stack=moss-litter"]
    )
    out = " ".join(result.output.split())
    assert result.exit_code == 0, out[-3000:]
    assert "litter mission is stopped" not in out and "was not confirmed" not in out


def test_a_run_on_moss_litter_still_ends_with_the_missions_stop(tmp_path: Path) -> None:
    """The run's own end sends a stop, and on moss-litter that ends the mission."""
    result = CliRunner().invoke(
        app,
        [
            "run",
            "moss-lookout",
            "--robot",
            "moss:mock",
            "--address",
            "mock://rover?stack=moss-litter",
            "--llm",
            "fake",
            "--runs-dir",
            str(tmp_path),
            "--no-gif",
        ],
    )
    out = " ".join(result.output.split())
    assert result.exit_code == 0, out[-3000:]
    from quackd.agent.transcript import Transcript

    events = Transcript.read(next(tmp_path.rglob("transcript.jsonl")))
    notes = [str(e.get("text")) for e in events if e["kind"] == "note"]
    assert any(n.startswith("The litter mission is stopped, and runs again") for n in notes), notes


# ── what quackd never sends ─────────────────────────────────────────────────────────────


def test_no_code_path_posts_the_cockpits_stack_command() -> None:
    client = CockpitClient()
    with pytest.raises(ValueError, match="never posts /cmd/stack"):
        client.command(NEVER_POSTED)
    assert NEVER_POSTED == "stack"
    for path in PACKAGE.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert not re.search(r"command\(\s*[\"']stack", text), path.name
        assert '"/cmd/stack"' not in text, path.name


def test_no_code_path_sends_a_go_limp_call_or_ends_a_dimos_run() -> None:
    """The arm's torque goes off only when its dimOS run ends, so nothing here may end one:
    the client runs no process at all, and the bridge's one process is the maker's cycle."""
    bridge = REPO / "bridge" / "moss" / "quackd_moss_bridge.py"
    # upstream_api.py is left out: it describes what upstream does, the hard stop included
    code = [p for p in PACKAGE.rglob("*.py") if p.name != "upstream_api.py"]
    for path in [*code, bridge]:
        text = path.read_text(encoding="utf-8")
        for limp in ("write_enable(", "disable_torque", "Torque_Enable", "pkill", "killpg"):
            assert limp not in text, f"{path.name} spells {limp}"
    for path in code:
        assert "subprocess" not in path.read_text(encoding="utf-8"), path.name
    source = bridge.read_text(encoding="utf-8")
    assert source.count("subprocess.Popen(") == 1
    assert '"moss-fly.sh"' not in source and "'moss-fly.sh'" not in source


# ── upstream names ──────────────────────────────────────────────────────────────────────


def test_every_ref_links_to_one_of_the_two_pinned_upstreams() -> None:
    for ref in up.all_refs():
        assert ref.status in ("VERIFIED", "UNVERIFIED"), ref
        assert ref.source.startswith((up.REPO, up.DIMOS_REPO)), ref
        if "/blob/" in ref.source:
            assert up.PIN in ref.source or up.DIMOS_PIN in ref.source, ref
    assert len(up.PIN) == len(up.DIMOS_PIN) == 40


def test_the_page_names_every_ref_carries_the_pin_and_says_never() -> None:
    doc = DOC.read_text(encoding="utf-8").replace("\\|", "|")
    missing = [ref.name for ref in up.all_refs() if ref.name not in doc]
    assert not missing, f"docs/adapters/moss/README.md is missing: {missing}"
    assert up.PIN[:7] in doc and up.DIMOS_PIN[:7] in doc and "never" in doc.lower()


def test_the_shipped_lookout_validates_and_moves_nothing() -> None:
    assert validate_duck(DUCK, [describe("mock")]) == []
    assert not set(DUCK.frontmatter.verbs.allow) & (TWISTS | PLANNER | ARM | {"collect_litter"})


def test_the_preconditions_and_the_implementations_cover_the_manifest() -> None:
    m = describe("cockpit")
    named = {c for gates in m.preconditions.values() for c in gates}
    assert named <= set(conditions())
    assert set(implementations()) == OWN
