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
from quackd_lerobot.sim.follower import (
    DeviceAlreadyConnectedError,
    DeviceNotConnectedError,
    SimFollowerConfig,
)
from quackd_lerobot.upstream_api import SO_MOTOR_IDS
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
    `release` as its disconnect's torque flag. Its port is not open: a test connects it first, as
    the real backend does, since a closed port refuses every read and write."""
    config = SimFollowerConfig(disable_torque_on_disconnect=release, max_relative_target=cap)
    return FakeFollower(
        config,
        travel=MOCK_RANGES,
        pose=REST,
        object_at=OBJECT_AT,
        near_deg=NEAR_DEG,
        grip_deg=GRIP_ON_OBJECT,
        temperature_c=30.0,
        motor_ids=SO_MOTOR_IDS,
    )


def _pos(**goals: float) -> dict[str, float]:
    return {f"{joint}.pos": goal for joint, goal in goals.items()}


# ── the follower ────────────────────────────────────────────────────────────────────────


def test_the_calibration_gives_the_travel_the_real_backend_reads_back_off_it() -> None:
    """The travel is read off the calibration with LeRobot's formula, as on an arm, and it is
    the mock's travel to the bit. The formula centres every travel on zero, so one that is not
    the same either side is refused rather than read back as another."""
    assert joint_ranges(dict(calibration_for(MOCK_RANGES, SO_MOTOR_IDS))) == MOCK_RANGES
    narrowed = dict(MOCK_RANGES) | {"elbow_flex": (-73.0, 73.0)}
    back = joint_ranges(dict(calibration_for(narrowed, SO_MOTOR_IDS)))
    assert back["elbow_flex"] == pytest.approx((-73.0, 73.0))
    with pytest.raises(ValueError, match="either side of zero"):
        calibration_for(dict(MOCK_RANGES) | {"elbow_flex": (-58.0, 73.0)}, SO_MOTOR_IDS)
    arm = _follower()
    assert all(arm.travel(joint) == pytest.approx(MOCK_RANGES[joint]) for joint in JOINTS)


def test_a_send_moves_a_joint_one_step_of_the_cap_into_the_travel_and_says_what_went_out() -> None:
    """LeRobot's step cap, read off the config as each send is made, and then the servo's own
    clamp to the travel, which nothing reports: the send says the capped goal, and the record
    keeps the goal as it was asked for."""
    arm = _follower()
    arm.connect(False)
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
    uncapped.connect(False)
    uncapped.send_action(_pos(shoulder_pan=30.0))
    assert uncapped.positions["shoulder_pan"] == 30.0, "upstream's None caps nothing"


def test_a_limp_or_seized_joint_takes_its_goal_and_does_not_move() -> None:
    arm = _follower()
    arm.connect(False)
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
    arm.connect(False)
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
    nothing.connect(False)
    nothing.object_at = None
    nothing.positions.update(OBJECT_AT)
    nothing.send_action(_pos(gripper=GRIPPER_CLOSED))
    assert not nothing.held and nothing.positions["gripper"] == GRIPPER_CLOSED


def test_the_bus_s_torque_turns_end_a_hand_off_the_ways_an_arm_can() -> None:
    """A release that some motors keep their torque through, a take-hold that a joint sags under
    and that the goal written straight after does not undo, and a torque register that stops
    answering after the write it was told to, with the reason it was given."""
    arm = _follower()
    arm.connect(False)
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
    quiet.connect(False)
    quiet.unread_after_release = "a reason made up for this test"
    quiet.bus.disable_torque()
    with pytest.raises(ConnectionError, match="a reason made up for this test"):
        quiet.bus.sync_read("Torque_Enable", normalize=False)
    quiet.bus.enable_torque()
    assert quiet.bus.sync_read("Torque_Enable", normalize=False) == dict.fromkeys(JOINTS, 1)


def test_the_connect_and_the_disconnect_are_upstream_s() -> None:
    """A second connect is refused while the port is open, as LeRobot's is, the connect's
    configure leaves every motor on, and the disconnect drops torque as its config says when it
    runs, which is the seam the real close lets go of an arm through. Closing the bus's port when
    it is not open is passed over without a word, as LeRobot 0.7.0's is (motors_bus.py lines 452
    and 453), which is how the real backend closes the port between two connect attempts."""
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
    arm.bus.disconnect(disable_torque=True)  # a port that is not open: nothing, and no write
    assert not arm.is_connected and not any(arm.torque.values())


def test_a_closed_port_refuses_every_read_and_write_as_lerobot_s_does() -> None:
    """LeRobot refuses a read, a send, a torque write and the follower's disconnect on a port that
    is not open, in one sentence under the class that refused (`up.NOT_CONNECTED`), and the mock's
    follower used to answer them all, before its connect and after its close alike. So a hand-off
    over a mock nobody had connected was let go of and read back rather than refused at its first
    read, as the arm's is. It refuses them now, as the arm and the simulator do, and touches no
    motor doing so, and the bus's own disconnect still passes over a closed port without a word."""
    arm = _follower()
    follower = "SOFollower is not connected. Run `.connect()` first."
    bus = "SerialMotorsBus is not connected. Run `.connect()` first."
    for _ in ("before the connect", "after the disconnect"):
        refusals = [
            (lambda: arm.get_observation(), follower),
            (lambda: arm.send_action(_pos(shoulder_pan=3.0)), follower),
            (lambda: arm.disconnect(), follower),
            (lambda: arm.bus.sync_read("Torque_Enable", normalize=False), bus),
            (lambda: arm.bus.disable_torque(num_retry=5), bus),
            (lambda: arm.bus.enable_torque(num_retry=5), bus),
        ]
        for call, said in refusals:
            with pytest.raises(DeviceNotConnectedError) as refused:
                call()
            assert str(refused.value) == said
        assert arm.actions == [] and all(arm.torque.values()), "a refused call reached a motor"
        assert arm.positions == REST
        arm.bus.disconnect(disable_torque=True)  # the bus's own: a closed port, and no word
        arm.connect(False)
        assert arm.get_observation()[f"{JOINTS[0]}.pos"] == REST[JOINTS[0]]
        arm.disconnect()


def test_a_torque_call_tries_every_motor_and_names_the_ones_that_did_not_answer() -> None:
    """LeRobot 0.7.0's torque calls try every motor whichever fails and then raise one error
    naming each that did not answer, in the order of their ids (motors_bus.py lines 642 to 648,
    rustypot's bus.rs lines 237 to 254). A motor in `unanswered` keeps its torque and every other
    one takes the write, on a release, a take-hold, the connect's configure, which raises on its
    way in and leaves the others limp, and the disconnect, which shuts the port all the same."""
    arm = _follower()
    arm.connect(False)
    arm.unanswered = {"gripper", "elbow_flex"}
    with pytest.raises(ConnectionError) as raised:
        arm.bus.disable_torque(num_retry=5)
    assert str(raised.value) == (
        "Failed to disable torque after 6 tries on 'elbow_flex' (motor ID 3): Operation timed "
        "out, 'gripper' (motor ID 6): Operation timed out."
    )
    assert {j for j, on in arm.torque.items() if on} == {"elbow_flex", "gripper"}
    with pytest.raises(ConnectionError, match=r"^Failed to enable torque after 1 tries on "):
        arm.bus.enable_torque()
    assert all(arm.torque.values())

    arm.config.disable_torque_on_disconnect = True
    with pytest.raises(ConnectionError, match=r"^Failed to disable torque after 6 tries on "):
        arm.disconnect()
    assert not arm.is_connected, "the disconnect kept the port"
    assert {j for j, on in arm.torque.items() if on} == {"elbow_flex", "gripper"}

    arm.unanswered = {"wrist_roll"}
    arm.torque["wrist_roll"] = True
    with pytest.raises(ConnectionError, match=r"'wrist_roll' \(motor ID 5\)"):
        arm.connect(False)
    assert arm.is_connected, "a configure that raised closed the port"
    assert {j for j, on in arm.torque.items() if on} == {"wrist_roll"}, "the others were not limp"
    arm.unanswered = set()
    arm.bus.disconnect(disable_torque=False)
    assert not arm.is_connected


def test_the_camera_draws_the_object_where_the_shoulder_points_and_not_once_it_is_held() -> None:
    arm = _follower(cap=None)
    arm.connect(False)
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


async def test_a_release_over_a_motor_that_does_not_answer_names_it_as_the_arm_does() -> None:
    """LeRobot 0.7.0's release tries every motor and names each one that did not answer, and the
    real backend says which joint may still hold. The mock's follower answers so for a motor in
    its `unanswered`, so the mock says it in the arm's words, every other joint let go."""
    mock = LeRobotMock(rest_pose=rest_goal(REST))
    adapter = LeRobotAdapter(mock)
    await adapter.connect()
    mock.follower.unanswered = {"gripper"}
    released = await adapter.let_go(asked=True)
    assert released.how == "released" and released.torque_on is None, released
    assert released.reason.startswith(
        "torque was taken off at the rest pose on every joint but gripper, whose write LeRobot "
        "could not confirm (ConnectionError: Failed to disable torque after 6 tries on 'gripper' "
        "(motor ID 6): Operation timed out.), so it may still hold"
    ), released.reason
    assert {j for j, on in mock.follower.torque.items() if on} == {"gripper"}
    mock.follower.unanswered = set()
    await adapter.close()


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


async def test_a_second_close_of_the_mock_keeps_what_the_first_one_said() -> None:
    """The mock's port refuses a read once the first close has shut it, as the arm's does, so a
    second close read nothing and ended on not being able to tell whether the arm holds itself
    up, over an arm the first close had let go of at its rest pose. It keeps the first close's
    line, as the arm's does."""
    mock = LeRobotMock(rest_pose=dict(REST))
    await mock.connect()
    await mock.close()
    assert mock.close_note is None and not mock.torque, mock.close_note
    await mock.close()
    assert mock.close_note is None, mock.close_note
    assert mock.sequence == ["close", "close"]
