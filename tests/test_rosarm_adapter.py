"""The industrial arm family (`quackd_rosarm`): four bodies, two backends, one rule (ADR-0078).

What these hold is that the family describes each arm from its body file and drives it only where
quackd may: every figure is the YAML's, every move asks a person first, the travel and the speeds
are the robot's own description, no rest pose and no tool are claimed, and `ws` is refused for
every arm in the owner rule's words, at `make()` and at the registry's door, until somebody names a
date for that arm's first hardware run (ADR-0076).
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import pytest

from quackd.adapters.base import AdapterError
from quackd.adapters.catalogue import BY_NAME, DESCRIBED_BODIES
from quackd.adapters.described import describe as described
from quackd.adapters.factory import RobotSpec, make_adapter, refusal
from quackd.adapters.factory import describe as describe_spec
from quackd.agent.prompts import DESCRIBED_LINE, body_lines
from quackd.registry import Registry, RegistryError, RobotEntry
from quackd.safety import ConfirmDenied, Executor, allow_all, deny_all
from quackd.verbs.registry import registry_from_manifest
from quackd_rosarm import (
    BACKENDS,
    NO_REST_POSE,
    ROBOT_TYPE,
    ROBOT_TYPES,
    RosArmAdapter,
    describe,
    make,
)
from quackd_rosarm.bodies import BODIES, ws_tier
from quackd_rosarm.fake import FakeBridge
from quackd_rosarm.transport import RosArmWs
from quackd_rosarm.verbs import (
    MOVE_MAX_S,
    NOT_OFFERED,
    NOTHING_TO_MOVE_DEG,
    PEAK_OVER_MEAN,
    SPEED_SHARE,
    TOL_DEG,
)

OWNER_RULE = "bound code needs a named owner and a date for its first hardware run (docs/adr/0076)"


# ── the bodies and the catalogue agree ──────────────────────────────────────────────────────


def test_the_types_are_the_catalogues_and_four_body_files() -> None:
    assert ROBOT_TYPES == tuple(BODIES) == BY_NAME["rosarm"].robot_types
    assert ROBOT_TYPES[0] == ROBOT_TYPE == "ur5e"
    assert set(ROBOT_TYPES) <= set(DESCRIBED_BODIES), "each is a body file the described row reads"
    assert len(ROBOT_TYPES) == 4
    assert BY_NAME["rosarm"].backends == BACKENDS == ("mock", "ws")


def test_the_catalogues_ws_tier_is_the_best_any_body_has() -> None:
    row = BY_NAME["rosarm"]
    assert row.tiers["ws"] == ws_tier()
    assert row.tiers["ws"].name == "described" and row.tiers["mock"].name == "rehearsed"
    assert all(not body.drivable for body in BODIES.values()), "nobody has named a date yet"
    assert row.family == ("arm",) and row.route == ("sdk",) and not row.connect


@pytest.mark.parametrize("robot_type", ROBOT_TYPES)
def test_a_bodys_figures_are_its_body_files(robot_type: str) -> None:
    """One file holds the figures whichever row reaches the body (`described.body_data`)."""
    driven = describe("mock", robot_type=robot_type)
    only = described(robot_type)
    for field in ("vendor", "model", "embodiment", "mobility"):
        assert getattr(driven, field) == getattr(only, field), field
    assert driven.datasheet is not None and only.datasheet is not None
    assert driven.datasheet.known() == only.datasheet.known()
    assert driven.datasheet.manipulator == "arms" and driven.datasheet.arms == 1
    # the family adds its own notes, and claims nothing the described row said it could not do
    assert set(only.datasheet.cannot) <= set(driven.datasheet.cannot)
    said = " ".join(driven.datasheet.notes)
    assert "never powers, enables, un-brakes or un-stops it" in said
    assert "the arm finishes that move and holds" in said
    assert "quackd clamps nothing here because it drives nothing" not in said
    # and it is not told it is a described body, since this row drives it
    assert DESCRIBED_LINE not in body_lines(driven)


@pytest.mark.parametrize("backend", BACKENDS)
@pytest.mark.parametrize("robot_type", ROBOT_TYPES)
def test_every_move_asks_a_person_and_nothing_held_is_claimed(
    backend: str, robot_type: str
) -> None:
    m = describe(backend, robot_type=robot_type)
    assert m.verb_names() == ["report_state", "stop", "move_joints"]
    assert m.verb("move_joints").safety_class == "confirm"  # type: ignore[union-attr]
    assert m.verb("stop").safety_class == "safe"  # type: ignore[union-attr]
    assert m.preconditions == {"move_joints": ["fresh_state", "can_move"]}
    assert m.intents == ["joint"] and m.sensors == ["joint_state"]
    assert m.not_offered() == NOT_OFFERED
    assert set(NOT_OFFERED) == {"gripper", "place", "pick", "manipulate"}
    authority = m.safety_authority
    assert (authority.native, authority.deadman) == ("estop", False)
    assert m.limits == {"speed_share": SPEED_SHARE}
    assert m.extras["robot_type"] == robot_type
    assert m.extras["joints"] == list(BODIES[robot_type].joints)


@pytest.mark.parametrize("robot_type", ROBOT_TYPES)
def test_the_mock_publishes_the_travel_its_description_gives_and_ws_none(robot_type: str) -> None:
    mock = describe("mock", robot_type=robot_type)
    travel = mock.extras["joint_range_deg"]
    assert list(travel) == list(BODIES[robot_type].joints)
    assert mock.extras["joint_range_from"] == "the robot's own description on the bridge"
    assert all(speed > 0 for speed in mock.extras["joint_speed_deg_s"].values())
    ws = describe("ws", robot_type=robot_type)
    assert "joint_range_deg" not in ws.extras and "joint_speed_deg_s" not in ws.extras


async def test_the_static_mock_is_what_a_connected_run_reads() -> None:
    for robot_type in ROBOT_TYPES:
        adapter = make("mock", robot_type=robot_type)
        live = await adapter.connect()
        assert live.digest() == describe("mock", robot_type=robot_type).digest(), robot_type
        await adapter.close()


def test_the_prompt_says_where_the_travel_was_read_and_the_ceiling() -> None:
    lines = " ".join(body_lines(describe("mock")))
    assert "travel in degrees, read from the robot's own description on the bridge" in lines
    assert "torque off" not in lines and "rest pose" not in lines, "the SO-101's own sentence"
    assert "quackd clamps you to 0.1 of each joint's rated speed." in lines
    assert "one arm with a bare wrist: it holds nothing until somebody fits a tool" in lines
    assert "gripper, place, pick, manipulate: no gripper or tool is fitted" in lines


# ── the owner rule: ws is refused for every arm ──────────────────────────────────────────────


@pytest.mark.parametrize("robot_type", ROBOT_TYPES)
def test_ws_is_refused_for_every_arm_in_the_owner_rules_words(robot_type: str) -> None:
    with pytest.raises(AdapterError) as refused:
        make("ws", robot_type=robot_type, address="ws://127.0.0.1:9090")
    said = str(refused.value)
    assert OWNER_RULE in said and f"rosarm:ws on a {robot_type}" in said
    assert "rosarm:mock rehearses it" in said
    spec = RobotSpec("rosarm", "ws", robot_type=robot_type)
    assert refusal(spec) == said
    with pytest.raises(AdapterError, match="docs/adr/0076"):
        make_adapter(spec)


def test_the_refusal_comes_before_anything_is_built(monkeypatch: pytest.MonkeyPatch) -> None:
    """Nothing is imported, no socket is opened and no address is parsed for an arm quackd may
    not drive: the refusal is the first thing make() does."""
    import sys

    monkeypatch.setitem(sys.modules, "roslibpy", None)
    with pytest.raises(AdapterError, match="docs/adr/0076"):
        make("ws", address="not even an address")


def test_the_registry_refuses_ws_at_the_door_and_takes_the_mock(tmp_path: Path) -> None:
    registry = Registry(str(tmp_path))
    for robot_type in (None, *ROBOT_TYPES):
        with pytest.raises(RegistryError) as refused:
            registry.add_robot(RobotEntry(name="cell-1", spec="rosarm:ws", robot_type=robot_type))
        assert OWNER_RULE in str(refused.value), refused.value
    assert registry.robots() == {}, "nothing was written"
    entry = registry.add_robot(RobotEntry(name="cell-1", spec="rosarm:mock", robot_type="ur5e"))
    assert entry.robot_type is None, "the default type is stored as no type"
    other = registry.add_robot(
        RobotEntry(name="cell-2", spec="rosarm:mock", robot_type="yaskawa-motomini")
    )
    assert other.robot_type == "yaskawa-motomini"
    built: Any = make_adapter(other.robot_spec)
    assert built.transport.body.robot_type == "yaskawa-motomini"
    # an edit that would move a robot onto ws is refused the same way, and leaves it as it was
    with pytest.raises(RegistryError, match="docs/adr/0076"):
        registry.update_robot("cell-2", {"spec": "rosarm:ws"})
    assert registry.robots()["cell-2"].spec == "rosarm:mock"
    with pytest.raises(RegistryError, match="rosarm drives no robot type 'koch_follower'"):
        registry.add_robot(
            RobotEntry(name="cell-3", spec="rosarm:mock", robot_type="koch_follower")
        )


def test_an_unknown_type_names_the_four(tmp_path: Path) -> None:
    with pytest.raises(AdapterError, match="choose one of ur5e, kuka-lbr-iisy-3-r760"):
        make("mock", robot_type="ur10e")
    with pytest.raises(AdapterError, match="choose one of ur5e"):
        describe_spec(RobotSpec("rosarm", "mock", robot_type="ur10e"))


# ── what it does not have ────────────────────────────────────────────────────────────────────


async def test_it_parks_nothing_and_says_why() -> None:
    with pytest.raises(AdapterError, match="does not return to a rest pose"):
        make("mock", rest_pose={"shoulder_pan_joint": 0.0})
    adapter = make("mock")
    rest = await adapter.go_to_rest()
    assert rest.how == "none" and rest.reason == NO_REST_POSE
    assert "a move nobody asked for at the end of a run is the hazard" in rest.reason
    assert adapter.supports_hand_off is False and adapter.supports_rest_pose is False


def test_it_reads_no_camera_and_says_where_one_comes_from() -> None:
    with pytest.raises(AdapterError, match="reads no camera") as refused:
        make("mock", camera_url=("http://127.0.0.1:8080/frame",))
    assert "--host" in str(refused.value)
    make("mock", camera_url=())  # none given is none refused


def test_an_unknown_backend_is_refused() -> None:
    with pytest.raises(ValueError, match="unknown rosarm backend"):
        make("real")


# ── the executor's gates ─────────────────────────────────────────────────────────────────────


async def test_a_move_nobody_confirmed_sends_nothing() -> None:
    adapter = make("mock")
    manifest = await adapter.connect()
    executor = Executor(registry_from_manifest(manifest, adapter), adapter, contract=None)
    assert executor.confirm is deny_all
    with pytest.raises(ConfirmDenied, match="declined move_joints"):
        await executor.run_verb("move_joints", {"positions": {"shoulder_pan_joint": 10.0}})
    assert "send_action_goal" not in adapter.bridge.operations()
    await adapter.close()


@pytest.mark.parametrize("robot_type", list(BODIES))
async def test_no_joint_ever_goes_faster_than_a_tenth_of_its_rated_speed(robot_type: str) -> None:
    """Read off the fake arm on every step of its clock, at the peak of the segment and not on
    average: the controller's quintic goes 15/8 of its mean at the middle, so a ceiling held on
    the mean let a joint reach almost a fifth of its rated speed. A time too short is stretched,
    and the result says so."""
    import itertools
    import math

    adapter = make("mock", robot_type=robot_type)
    manifest = await adapter.connect()
    bridge = adapter.bridge
    joint = BODIES[robot_type].joints[0]
    rated = float(manifest.extras["joint_speed_deg_s"][joint])
    _lo, hi = manifest.extras["joint_range_deg"][joint]
    start = float((await adapter.get_state()).extras["joints"][joint])
    target = start + min(SPEED_SHARE * rated * MOVE_MAX_S / PEAK_OVER_MEAN, hi - start) * 0.9
    samples: list[tuple[float, float]] = []
    bridge.clock.add_hook(lambda now, _dt: samples.append((now, bridge.arm.positions[joint])))
    executor = Executor(
        registry_from_manifest(manifest, adapter), adapter, contract=None, confirm=allow_all
    )
    moved = await executor.run_verb(
        "move_joints", {"positions": {joint: target}, "duration_s": 0.2}
    )
    assert moved.ok, moved.summary
    assert "rather than 0.2, since no joint moves faster than a tenth" in moved.summary
    speeds = [
        abs(math.degrees(b - a)) / (t1 - t0)
        for (t0, a), (t1, b) in itertools.pairwise(samples)
        if t1 > t0
    ]
    assert max(speeds) <= SPEED_SHARE * rated * 1.001, (max(speeds), SPEED_SHARE * rated)
    await adapter.close()


async def test_a_move_longer_than_twelve_seconds_at_the_ceiling_is_refused_and_sends_nothing() -> (
    None
):
    adapter = make("mock")
    manifest = await adapter.connect()
    executor = Executor(
        registry_from_manifest(manifest, adapter), adapter, contract=None, confirm=allow_all
    )
    # 18 degrees a second at the peak on a UR5e joint, so 200 degrees needs about 21 s
    moved = await executor.run_verb("move_joints", {"positions": {"shoulder_pan_joint": 200.0}})
    assert not moved.ok and "split it into shorter moves" in moved.summary, moved.summary
    assert "send_action_goal" not in adapter.bridge.operations()
    await adapter.close()


async def test_two_moves_at_once_are_one_move_and_one_refusal() -> None:
    """An MCP client can ask two tool calls at once. The second move is refused while the first is
    still watched to its end, rather than sent once the first goal ended and its record read as
    the first one's."""
    import asyncio

    adapter = make("mock")
    manifest = await adapter.connect()
    executor = Executor(
        registry_from_manifest(manifest, adapter), adapter, contract=None, confirm=allow_all
    )
    first = asyncio.create_task(
        executor.run_verb("move_joints", {"positions": {"shoulder_pan_joint": 20.0}})
    )
    for _ in range(100):
        if adapter.move_lock.locked():
            break
        await asyncio.sleep(0)
    second = await executor.run_verb("move_joints", {"positions": {"shoulder_pan_joint": -20.0}})
    assert not second.ok and "another move is still being watched" in second.summary
    assert (await first).ok
    assert adapter.bridge.operations().count("send_action_goal") == 1
    await adapter.close()


async def test_a_joint_reading_past_its_travel_stops_any_move_before_it_is_sent() -> None:
    """A joint the pilot does not name is held where it reads, so a reading past the travel would
    be a goal past it: a UR's elbow jogged to 190 on the pendant, where the description says 180."""
    import math

    adapter = make("mock")
    manifest = await adapter.connect()
    adapter.bridge.arm.positions["elbow_joint"] = math.radians(190.0)
    adapter.bridge.arm.hold()  # jogged there on the pendant, and held
    await adapter.sleep(0.1)
    executor = Executor(
        registry_from_manifest(manifest, adapter), adapter, contract=None, confirm=allow_all
    )
    moved = await executor.run_verb("move_joints", {"positions": {"shoulder_pan_joint": 10.0}})
    assert not moved.ok and "elbow_joint reads 190.0, past its travel of -180 to 180" in (
        moved.summary
    )
    assert "send_action_goal" not in adapter.bridge.operations()
    await adapter.close()


async def test_a_move_no_result_answers_says_whether_its_cancel_went() -> None:
    """A goal the bridge's glob does not match is never answered (`up.GOAL_DROPPED_BY_GLOB`), nor
    is its cancel: the verb says the cancel got no answer rather than that it cancelled the goal.
    On a socket that has dropped, no goal is sent at all."""
    bridge = FakeBridge(BODIES["ur5e"], actions_glob=("/nothing/*",))
    transport = RosArmWs(
        None, body=bridge.body, roslibpy=bridge.roslibpy(), clock=bridge.clock, name="mock"
    )
    adapter = RosArmAdapter(transport, bridge=bridge)
    manifest = await adapter.connect()
    executor = Executor(
        registry_from_manifest(manifest, adapter), adapter, contract=None, confirm=allow_all
    )
    lost = await executor.run_verb(
        "move_joints", {"positions": {"shoulder_pan_joint": 5.0}, "duration_s": 1}
    )
    assert not lost.ok and "quackd could not cancel the goal: no answer to the cancel" in (
        lost.summary
    ), lost.summary
    adapter.bridge.drop()
    gone = await executor.run_verb(
        "move_joints", {"positions": {"shoulder_pan_joint": 5.0}, "duration_s": 1}
    )
    assert not gone.ok and "is not connected, so no goal is sent" in gone.summary, gone.summary
    await adapter.close()


async def test_arrival_is_read_only_off_fresh_readings() -> None:
    """A goal that ends SUCCEEDED on readings gone stale says nothing of where the arm is: the
    verb fails rather than call it arrived."""
    adapter = make("mock")
    manifest = await adapter.connect()
    executor = Executor(
        registry_from_manifest(manifest, adapter), adapter, contract=None, confirm=allow_all
    )
    bridge = adapter.bridge
    fired: list[bool] = []

    def hook(_now: float, _dt: float) -> None:
        goal = bridge.arm.goal
        if not fired and goal is not None and goal.wall >= 0.5:
            fired.append(True)
            bridge.quiet()

    bridge.clock.add_hook(hook)
    moved = await executor.run_verb(
        "move_joints", {"positions": {"shoulder_pan_joint": 5.0}, "duration_s": 1}
    )
    assert not moved.ok and "the readings are stale" in moved.summary, moved.summary
    await adapter.close()


async def test_a_goal_within_the_tolerance_is_arrived_at_whether_or_not_the_arm_moved() -> None:
    """A joint within `TOL_DEG` of its goal counts as at it, so a goal that near where the joints
    read passes the arrival check at once: an arm that stops short of every goal by the whole of
    this one, and so never moves, is called arrived. The verb's description tells the pilot so, and
    a goal within `NOTHING_TO_MOVE_DEG` sends nothing at all."""
    adapter = make("mock")
    manifest = await adapter.connect()
    registry = registry_from_manifest(manifest, adapter)
    executor = Executor(registry, adapter, contract=None, confirm=allow_all)
    bridge = adapter.bridge
    bridge.arm.short_rad = math.radians(TOL_DEG)  # short of every goal by half a degree
    here = (await adapter.get_state()).extras["joints"]["shoulder_pan_joint"]
    before = dict(bridge.arm.positions)
    small = await executor.run_verb(
        "move_joints", {"positions": {"shoulder_pan_joint": here + 0.8 * TOL_DEG}, "duration_s": 1}
    )
    assert small.ok and small.summary.startswith("moved shoulder_pan_joint="), small.summary
    assert bridge.arm.positions == before, "the arm never moved"
    assert bridge.operations().count("send_action_goal") == 1
    nothing = await executor.run_verb(
        "move_joints",
        {"positions": {"shoulder_pan_joint": here + 0.5 * NOTHING_TO_MOVE_DEG}, "duration_s": 1},
    )
    assert nothing.ok and nothing.summary == "already there: nothing was sent", nothing.summary
    assert bridge.operations().count("send_action_goal") == 1
    said = registry.get("move_joints").description
    assert f"within {TOL_DEG:g} degrees of its goal" in said, said
    assert "counts as arrived whether or not the arm moved" in said, said
    await adapter.close()


async def test_a_confirmed_move_runs_and_report_state_says_it_in_an_arms_words() -> None:
    adapter = make("mock")
    manifest = await adapter.connect()
    executor = Executor(
        registry_from_manifest(manifest, adapter), adapter, contract=None, confirm=allow_all
    )
    report = await executor.run_verb("report_state", {})
    assert report.ok and "shoulder_pan_joint 0.0" in report.summary
    assert "the driver says program running, safety mode NORMAL" in report.summary
    assert "holds nothing: no tool is fitted" in report.summary
    moved = await executor.run_verb(
        "move_joints", {"positions": {"shoulder_pan_joint": 12.0}, "duration_s": 3}
    )
    assert moved.ok, moved.summary
    assert moved.summary.startswith("moved shoulder_pan_joint=12.0")
    assert adapter.bridge.operations().count("send_action_goal") == 1
    await adapter.close()
