"""`lerobot:mock` is the real backend over an arm in memory (`quackd_lerobot.fake_follower`).

The follower is held here to what it stands in for, LeRobot's follower and its bus as the real
backend reads and writes them, and the mock to being that backend: a change to the real backend's
hand-off is a change to the mock's, with no second copy of it to keep in step.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pytest

from quackd.sim2d.render import BALL
from quackd.transport.base import Intent
from quackd_lerobot import LeRobotAdapter, make
from quackd_lerobot.fake_follower import FakeCamera, FakeFollower, calibration_for
from quackd_lerobot.mock import (
    GRIP_ON_OBJECT,
    MOCK_CAM_HEIGHT_M,
    MOCK_OBJECT_R,
    MOCK_RANGES,
    NEAR_DEG,
    OBJECT_AT,
    REST,
    LeRobotMock,
)
from quackd_lerobot.real import MAX_STEP_DEG, STEP_ENV, LeRobotReal, joint_ranges
from quackd_lerobot.sim.follower import DeviceAlreadyConnectedError, SimFollowerConfig
from quackd_lerobot.verbs import (
    GRIPPER_CLOSED,
    GRIPPER_OPEN,
    JOINTS,
    LET_GO_TO_PLACE,
    LIMP_IN_HAND,
    at_rest,
    rest_goal,
    still_holding_at_rest,
)
from tests.test_lerobot_adapter import FakeArm, _handover_arm


def _follower(*, cap: float | None = MAX_STEP_DEG, release: bool = False) -> FakeFollower:
    """The mock's follower on its own, at `REST`, with `cap` as the step cap on its config and
    `release` as its disconnect's torque flag."""
    config = SimFollowerConfig(disable_torque_on_disconnect=release, max_relative_target=cap)
    return FakeFollower(
        config,
        travel=MOCK_RANGES,
        pose=REST,
        object_at=OBJECT_AT,
        near_deg=NEAR_DEG,
        grip_deg=GRIP_ON_OBJECT,
        temperature_c=30.0,
    )


def _pos(**goals: float) -> dict[str, float]:
    return {f"{joint}.pos": goal for joint, goal in goals.items()}


# ── the follower ────────────────────────────────────────────────────────────────────────


def test_the_calibration_gives_the_travel_the_real_backend_reads_back_off_it() -> None:
    """The travel is read off the calibration with LeRobot's formula, as on an arm, and it is
    the mock's travel to the bit. The formula centres every travel on zero, so one that is not
    the same either side is refused rather than read back as another."""
    assert joint_ranges(dict(calibration_for(MOCK_RANGES))) == MOCK_RANGES
    narrowed = dict(MOCK_RANGES) | {"elbow_flex": (-73.0, 73.0)}
    back = joint_ranges(dict(calibration_for(narrowed)))
    assert back["elbow_flex"] == pytest.approx((-73.0, 73.0))
    with pytest.raises(ValueError, match="either side of zero"):
        calibration_for(dict(MOCK_RANGES) | {"elbow_flex": (-58.0, 73.0)})
    arm = _follower()
    assert all(arm.travel(joint) == pytest.approx(MOCK_RANGES[joint]) for joint in JOINTS)


def test_a_send_moves_a_joint_one_step_of_the_cap_into_the_travel_and_says_what_went_out() -> None:
    """LeRobot's step cap, read off the config as each send is made, and then the servo's own
    clamp to the travel, which nothing reports: the send says the capped goal, and the record
    keeps the goal as it was asked for."""
    arm = _follower()
    sent = arm.send_action(_pos(shoulder_pan=30.0))
    assert sent == _pos(shoulder_pan=MAX_STEP_DEG), "capped a step from the reading"
    assert arm.positions["shoulder_pan"] == MAX_STEP_DEG
    assert arm.actions == [{"shoulder_pan": 30.0}], "kept as asked for, by joint name"
    assert {j: arm.positions[j] for j in JOINTS if j != "shoulder_pan"} == {
        j: REST[j] for j in JOINTS if j != "shoulder_pan"
    }, "only the keys given move"

    hi = MOCK_RANGES["elbow_flex"][1]
    arm.positions["elbow_flex"] = hi - 2.0
    sent = arm.send_action(_pos(elbow_flex=hi + 20.0))
    assert sent == _pos(elbow_flex=hi + 3.0), "the send says the capped goal, unclamped"
    assert arm.positions["elbow_flex"] == hi, "and the servo stops at its travel"

    arm.config.max_relative_target = 2.0  # what a policy segment writes for its own sends
    arm.send_action(_pos(shoulder_pan=30.0))
    assert arm.positions["shoulder_pan"] == MAX_STEP_DEG + 2.0
    uncapped = _follower(cap=None)
    uncapped.send_action(_pos(shoulder_pan=30.0))
    assert uncapped.positions["shoulder_pan"] == 30.0, "upstream's None caps nothing"


def test_a_limp_or_seized_joint_takes_its_goal_and_does_not_move() -> None:
    arm = _follower()
    arm.torque["wrist_flex"] = False
    arm.stuck.add("wrist_roll")
    arm.send_action(_pos(wrist_flex=3.0, wrist_roll=3.0, shoulder_pan=3.0))
    assert (arm.positions["wrist_flex"], arm.positions["wrist_roll"]) == (0.0, 0.0)
    assert arm.positions["shoulder_pan"] == 3.0


def test_the_jaws_stop_on_the_object_and_let_go_of_it_as_they_open() -> None:
    """Short of shut is the whole of what an arm knows about holding something, so that is
    what the follower does where the object is, and the object goes where the arm goes until the
    jaws open past it. Away from it, or with the jaws already shut, there is nothing between
    them to stop on."""
    arm = _follower(cap=None)
    arm.positions.update(OBJECT_AT)
    arm.send_action(_pos(gripper=GRIPPER_CLOSED))
    assert arm.positions["gripper"] == GRIP_ON_OBJECT and arm.held
    arm.send_action(_pos(shoulder_pan=OBJECT_AT["shoulder_pan"] - 60.0))
    arm.send_action(_pos(gripper=GRIPPER_CLOSED))
    assert arm.held and arm.positions["gripper"] == GRIP_ON_OBJECT, "carried, and still held"
    arm.send_action(_pos(gripper=GRIPPER_OPEN))
    assert not arm.held and arm.positions["gripper"] == GRIPPER_OPEN
    arm.send_action(_pos(gripper=GRIPPER_CLOSED))
    assert not arm.held and arm.positions["gripper"] == GRIPPER_CLOSED, "nothing out here"

    arm.positions.update(OBJECT_AT)
    arm.send_action(_pos(gripper=GRIPPER_CLOSED))
    assert not arm.held, "jaws already shut close on nothing"
    nothing = _follower(cap=None)
    nothing.object_at = None
    nothing.positions.update(OBJECT_AT)
    nothing.send_action(_pos(gripper=GRIPPER_CLOSED))
    assert not nothing.held and nothing.positions["gripper"] == GRIPPER_CLOSED


def test_the_bus_s_torque_turns_end_a_hand_off_the_ways_an_arm_can() -> None:
    """A release that some motors keep their torque through, a take-hold that a joint sags under
    and that the goal written straight after does not undo, and a torque register that stops
    answering after the write it was told to, with the reason it was given."""
    arm = _follower()
    arm.holdouts = ("wrist_roll",)
    arm.bus.disable_torque(num_retry=5)
    read = arm.bus.sync_read("Torque_Enable", normalize=False, num_retry=2)
    assert read == {j: int(j == "wrist_roll") for j in JOINTS}, read
    assert arm.bus.sync_read("Present_Temperature", normalize=False)["elbow_flex"] == 30

    arm.slips = {"elbow_flex": -9.0}
    arm.bus.enable_torque(num_retry=5)
    assert all(arm.torque.values())
    assert arm.positions["elbow_flex"] == REST["elbow_flex"] - 9.0
    arm.send_action(_pos(elbow_flex=REST["elbow_flex"]))
    assert arm.positions["elbow_flex"] == REST["elbow_flex"] - 9.0, "the second ask fails too"
    arm.send_action(_pos(elbow_flex=REST["elbow_flex"]))
    assert arm.positions["elbow_flex"] == REST["elbow_flex"] - 4.0, "and the next one moves it"

    quiet = _follower()
    quiet.unread_after_release = "a reason made up for this test"
    quiet.bus.disable_torque()
    with pytest.raises(ConnectionError, match="a reason made up for this test"):
        quiet.bus.sync_read("Torque_Enable", normalize=False)
    quiet.bus.enable_torque()
    assert quiet.bus.sync_read("Torque_Enable", normalize=False) == dict.fromkeys(JOINTS, 1)


def test_the_connect_and_the_disconnect_are_upstream_s() -> None:
    """A second connect is refused while the port is open, as LeRobot's is, the connect's
    configure leaves every motor on, and the disconnect drops torque as its config says when it
    runs, which is the seam the real close lets go of an arm through."""
    arm = _follower()
    arm.torque["gripper"] = False
    arm.connect(False)
    assert arm.is_connected and all(arm.torque.values())
    with pytest.raises(DeviceAlreadyConnectedError):
        arm.connect(False)
    arm.disconnect()
    assert not arm.is_connected and all(arm.torque.values()), "asked to keep its torque"
    arm.connect(False)
    arm.config.disable_torque_on_disconnect = True
    arm.disconnect()
    assert not any(arm.torque.values())
    with pytest.raises(ConnectionError):
        arm.bus.disconnect(disable_torque=False)  # a port that is not open is refused


def test_the_camera_draws_the_object_where_the_shoulder_points_and_not_once_it_is_held() -> None:
    arm = _follower(cap=None)
    camera = FakeCamera(
        arm,
        size=96,
        bearing_deg=OBJECT_AT["shoulder_pan"],
        distance_m=0.35,
        height_m=MOCK_CAM_HEIGHT_M,
        radius_m=MOCK_OBJECT_R,
    )

    def orange(frame: Any) -> int:
        return int(np.all(np.asarray(frame) == BALL, axis=-1).sum())

    frame = camera.read_latest()
    assert frame.shape == (96, 96, 3) and frame.dtype == np.uint8
    assert orange(frame) > 0, "the object is in view from the rest pose"
    before = np.argwhere(np.all(frame == BALL, axis=-1))[:, 1].mean()
    arm.send_action(_pos(shoulder_pan=15.0))
    after = np.argwhere(np.all(camera.read_latest() == BALL, axis=-1))[:, 1].mean()
    assert after > before, "it slides toward the middle as the shoulder pans toward it"
    arm.send_action(_pos(shoulder_pan=OBJECT_AT["shoulder_pan"] - 60.0))
    assert orange(camera.read_latest()) == 0, "and leaves it past 45 degrees"
    arm.positions.update(OBJECT_AT)
    arm.send_action(_pos(gripper=GRIPPER_CLOSED))
    assert arm.held and orange(camera.read_latest()) == 0, "and is not drawn in the jaws"


# ── the mock is the real backend over it ────────────────────────────────────────────────


async def test_the_mock_is_the_real_backend_and_reads_its_travel_off_its_calibration() -> None:
    mock = LeRobotMock()
    assert isinstance(mock, LeRobotReal)
    assert mock.joint_range_deg == MOCK_RANGES, "known before the connect, as it always was"
    adapter = LeRobotAdapter(mock)
    manifest = await adapter.connect()
    assert mock.connected and mock.joint_range_deg == MOCK_RANGES
    assert manifest.limits["step_deg"] == MAX_STEP_DEG, "the cap it moves under, as the arm's"
    assert "calibration_file" not in manifest.extras and manifest.extras["lerobot_version"] is None
    assert "camera" not in manifest.extras, "a view it draws has no url to name"
    await adapter.close()
    assert not mock.connected


async def test_the_mock_moves_under_the_step_the_arm_s_variable_sets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`QUACKD_LEROBOT_MAX_STEP_DEG` is read for the mock as for the arm and its simulator, so a
    rehearsal under a lowered cap moves as slowly as the arm will and says so in its manifest."""
    lowered = MAX_STEP_DEG / 2
    monkeypatch.setenv(STEP_ENV, str(lowered))
    adapter = make("mock")
    mock = adapter.transport
    assert isinstance(mock, LeRobotMock)
    manifest = await adapter.connect()
    assert manifest.limits["step_deg"] == lowered
    assert (await mock.send_intent(Intent.joint({"shoulder_pan": 30.0}, 1.0))).accepted
    assert mock.joints["shoulder_pan"] == REST["shoulder_pan"] + lowered, "one send, one step"
    await adapter.close()


async def test_a_change_to_the_real_close_is_a_change_to_the_mock_s(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The mock's teardown is the real one: the rest move folds the arm home a step of the cap
    at a time, and the close's own read decides whether it may let go. So a change to the real
    close reaches the mock as it is made, as this one to the line it says over an arm kept on
    away from its pose does, and there is no second close to change by hand."""
    said = "a line made up for this test, over an arm that is {why}"
    monkeypatch.setattr(LeRobotReal, "_torque_left_on", lambda _self, why: said.format(why=why))
    mock = LeRobotMock(rest_pose=dict(REST))
    adapter = LeRobotAdapter(mock)
    await adapter.connect()
    away = {"shoulder_pan": 40.0, "elbow_flex": 50.0}
    mock.joints.update(away)

    await adapter.stop()
    folded = await adapter.go_to_rest()
    assert folded.how == "arrived", folded.reason
    rests = [sent for sent in mock.actions if sent == rest_goal(REST)]
    assert len(rests) >= max(abs(REST[j] - v) for j, v in away.items()) / MAX_STEP_DEG - 1, (
        "home a step of the cap at a time, as the arm's rest move takes it"
    )
    # inside the move's tolerance of the pose, where the arm's own move stops driving it
    assert at_rest(rest_goal(REST), mock.joints) and mock.joints != REST, mock.joints
    mock.joints["shoulder_pan"] = 30.0  # knocked off its pose after the fold
    await adapter.close()
    assert mock.sequence == ["stop", "rest", "close"]
    assert mock.torque is True, "the close's own read found it away from its pose"
    assert mock.close_note == said.format(
        why="shoulder_pan is at 30 with a goal of 0; nothing moved it there"
    ), mock.close_note


@pytest.mark.parametrize("backend", ["real", "mock"])
async def test_an_asked_release_a_motor_kept_on_through_closes_in_the_real_close_s_words(
    backend: str,
) -> None:
    """`quackd robot release` lets go of the arm at its rest pose, one motor keeps its torque,
    and the close's read finds the arm lying in its fold: the real close says it lies there with
    that joint still on, and to leave it where it rests. The mock's own close told the person to
    put down an arm that was down, a line the real close stopped saying and the mock went on
    with. It runs the real close now, so the two say the same."""
    if backend == "real":
        arm, transport = _handover_arm()
        arm.torque_holdouts = {"elbow_flex"}
        adapter: Any = LeRobotAdapter(transport)
    else:
        mock = LeRobotMock(rest_pose=rest_goal(REST))
        mock.release_holdouts = ("elbow_flex",)
        adapter = LeRobotAdapter(mock)
    await adapter.connect()
    released = await adapter.let_go(asked=True)
    assert released.how == "released" and released.torque_on == ("elbow_flex",), released
    await adapter.close()
    assert adapter.close_note == still_holding_at_rest(("elbow_flex",)), adapter.close_note
    assert adapter.close_note != LIMP_IN_HAND.format(why=LET_GO_TO_PLACE)


async def test_a_raw_goal_moves_the_mock_one_step_of_the_cap_as_it_moves_the_arm() -> None:
    """One `joint` intent is one send, which LeRobot caps a step from the reading, on the arm
    and so on the mock; the verbs re-send a goal every tick until the arm is there."""
    mock = LeRobotMock()
    real = LeRobotReal("COM5", robot=FakeArm(step=MAX_STEP_DEG))
    for transport in (mock, real):
        await transport.connect()
        assert (await transport.send_intent(Intent.joint({"shoulder_pan": 30.0}))).accepted
        state = await transport.get_state()
        assert state.extras["joints"]["shoulder_pan"] == MAX_STEP_DEG, transport.name
        await transport.close()
