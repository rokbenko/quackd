"""The fake bridge and the fake arms, held to the sources they were written from.

`rosarm:mock` rehearses a task against `fake.py`, so a fake kinder than the real thing would be a
rehearsal that passes where the arm would refuse. Each test here is one thing a source in
`quackd_rosarm/upstream_api.py` says a bridge, a JointTrajectoryController or MotoROS2 does, and the
fake doing it: the goals each refuses, how a running goal is ended, the bridge that holds a cancel
behind its goal, and the one move that runs on when its client goes quiet.
"""

from __future__ import annotations

import math
from typing import Any

import pytest

from quackd.transport.base import Intent
from quackd_rosarm import upstream_api as up
from quackd_rosarm.bodies import arm_body
from quackd_rosarm.fake import (
    FakeBridge,
    FakeClock,
    FakeJtcArm,
    FakeMotoRos2Arm,
    GoalStatus,
    minimum_jerk,
)
from quackd_rosarm.transport import RosArmWs, trajectory_goal

UR = arm_body("ur5e")
IISY = arm_body("kuka-lbr-iisy-3-r760")
IIWA = arm_body("kuka-lbr-iiwa-7-r800")
MOTO = arm_body("yaskawa-motomini")


class Heard:
    """A goal's three callbacks, collecting what each was handed."""

    def __init__(self) -> None:
        self.results: list[Any] = []
        self.feedback: list[Any] = []
        self.refusals: list[Any] = []

    def args(self) -> tuple[Any, Any, Any]:
        return self.results.append, self.feedback.append, self.refusals.append

    @property
    def status(self) -> int | None:
        return int(self.results[-1]["status"]) if self.results else None

    @property
    def values(self) -> dict[str, Any]:
        return dict(self.results[-1]["values"]) if self.results else {}


def _goal(arm: Any, deg: float = 10.0, seconds: float = 2.0) -> dict[str, Any]:
    names = arm.names
    start = [arm.positions[n] for n in names]
    return trajectory_goal(names, start, [v + math.radians(deg) for v in start], seconds)


def _run(arm: Any, seconds: float, dt: float = 0.01) -> None:
    for _ in range(round(seconds / dt)):
        arm.tick(dt)


# ── the JointTrajectoryController ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("change", "why"),
    [
        (lambda g: g["trajectory"]["joint_names"].pop(), "don't match the controller joints"),
        (lambda g: g["trajectory"].update(points=[]), "Empty trajectory received."),
        (
            lambda g: g["trajectory"]["points"][1].update(time_from_start={"sec": 0, "nanosec": 0}),
            "not strictly increasing",
        ),
        (lambda g: g["trajectory"]["header"].update(stamp={"sec": 5, "nanosec": 0}), "in the past"),
        (lambda g: g["trajectory"]["points"][1].update(effort=[1.0] * 6), "effort"),
        (lambda g: g["trajectory"]["points"][1].update(velocities=[0.1] * 6), "is not zero"),
    ],
)
def test_the_jtc_rejects_what_its_validator_rejects(change: Any, why: str) -> None:
    """`up.JTC_ALL_JOINTS`, `up.JTC_TIMES_INCREASE`, `up.JTC_STAMP_ZERO` and, on Jazzy,
    `up.JTC_END_AT_REST`: a rejected goal never starts."""
    arm = FakeJtcArm(UR, distro="jazzy")
    goal = _goal(arm)
    change(goal)
    heard = Heard()
    assert why in str(arm.receive("g1", goal, *heard.args()[:2]))
    assert arm.goal is None


def test_a_moving_last_point_is_taken_on_humble_and_on_the_iisy_by_its_own_config() -> None:
    for arm in (FakeJtcArm(UR, distro="humble"), FakeJtcArm(IISY, distro="jazzy")):
        goal = _goal(arm)
        goal["trajectory"]["points"][1]["velocities"] = [0.1] * len(arm.names)
        assert arm.receive("g1", goal, *Heard().args()[:2]) is None, arm.body.robot_type


def test_an_inactive_controller_rejects_every_goal() -> None:
    arm = FakeJtcArm(IISY, active=False)  # robot_manager not activated (`up.IISY_INACTIVE_FIRST`)
    assert arm.receive("g1", _goal(arm), *Heard().args()[:2]) == up.JTC_INACTIVE_REJECTS.name


@pytest.mark.parametrize(
    ("distro", "status", "words"),
    [
        ("humble", GoalStatus.CANCELED, "Current goal cancelled due to new incoming action."),
        ("jazzy", GoalStatus.ABORTED, "Current goal preempted by new incoming action."),
    ],
)
def test_a_new_goal_ends_the_running_one_as_each_distribution_ends_it(
    distro: str, status: GoalStatus, words: str
) -> None:
    arm = FakeJtcArm(UR, distro=distro)  # type: ignore[arg-type]
    first, second = Heard(), Heard()
    arm.receive("g1", _goal(arm, 20.0, 4.0), *first.args()[:2])
    _run(arm, 1.0)
    arm.receive("g2", _goal(arm, 5.0, 1.0), *second.args()[:2])
    assert first.status == status and first.values == {"error_code": -1, "error_string": words}
    assert arm.goal is not None and arm.goal.id == "g2"


def test_succeeded_at_the_end_time_whatever_the_joints_read_where_the_tolerance_is_zero() -> None:
    """`up.JTC_ZERO_TOLERANCE`: the iisy's stock config leaves the goal tolerance at 0."""
    arm = FakeJtcArm(IISY, short_rad=math.radians(3.0))
    heard = Heard()
    arm.receive("g1", _goal(arm, 20.0, 2.0), *heard.args()[:2])
    _run(arm, 2.05)
    assert heard.status == GoalStatus.SUCCEEDED
    assert heard.values["error_string"] == "Goal successfully reached!"
    # joint_1 started mid travel, at 0, and stopped three degrees short of its twenty
    assert math.degrees(arm.positions["joint_1"]) == pytest.approx(17.0, abs=0.01)


def test_the_urs_tolerance_waits_for_ever_on_an_arm_that_stops_short() -> None:
    """`up.UR_TOLERANCES`: 0.1 rad at the goal and goal_time 0, so only the client ends it."""
    arm = FakeJtcArm(UR, short_rad=math.radians(10.0))
    heard = Heard()
    arm.receive("g1", _goal(arm, 20.0, 1.0), *heard.args()[:2])
    _run(arm, 30.0)
    assert heard.results == [] and arm.goal is not None, "no result outside its tolerance"


def test_a_cancel_holds_where_the_arm_is_and_ends_the_goal_canceled() -> None:
    arm = FakeJtcArm(UR)
    heard = Heard()
    arm.receive("g1", _goal(arm, 40.0, 4.0), *heard.args()[:2])
    _run(arm, 1.0)
    where = dict(arm.positions)
    arm.cancel("g1")
    _run(arm, 2.0)
    assert heard.status == GoalStatus.CANCELED and arm.positions == where


def test_a_deactivation_aborts_the_goal_and_refuses_the_next() -> None:
    arm = FakeJtcArm(UR)
    heard = Heard()
    arm.receive("g1", _goal(arm, 40.0, 4.0), *heard.args()[:2])
    _run(arm, 1.0)
    arm.protective_stop()
    assert heard.status == GoalStatus.ABORTED
    assert heard.values["error_string"] == up.JTC_DEACTIVATE_ABORTS.name
    assert arm.receive("g2", _goal(arm), *Heard().args()[:2]) == up.JTC_INACTIVE_REJECTS.name


def test_a_speed_scaling_of_zero_pauses_a_goal_without_ending_it() -> None:
    arm = FakeJtcArm(UR)
    heard = Heard()
    arm.receive("g1", _goal(arm, 20.0, 2.0), *heard.args()[:2])
    _run(arm, 0.5)
    arm.speed_scaling = 0.0
    where = dict(arm.positions)
    _run(arm, 5.0)
    assert heard.results == [] and arm.positions == where, "a pause keeps the goal"
    arm.speed_scaling = 1.0
    _run(arm, 2.0)
    assert heard.status == GoalStatus.SUCCEEDED


def test_the_segment_is_a_quintic_that_starts_and_ends_at_rest() -> None:
    assert minimum_jerk(0.0) == 0.0 and minimum_jerk(1.0) == 1.0
    assert minimum_jerk(0.5) == pytest.approx(0.5)
    assert minimum_jerk(0.01) < 0.001, "it starts gently"


def test_the_iiwas_readings_are_the_commands_under_its_stock_open_loop() -> None:
    arm = FakeJtcArm(IIWA, lag_s=1.0)
    arm.receive("g1", _goal(arm, 20.0, 1.0), *Heard().args()[:2])
    _run(arm, 1.0)
    command, reading = arm.commanded["lbr_A1"], arm.readings()["lbr_A1"]
    assert reading == command and arm.positions["lbr_A1"] < command, "the arm lags; its reading not"


# ── MotoROS2 ─────────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("change", "code"),
    [
        (lambda g: g["trajectory"].update(points=g["trajectory"]["points"][1:]), -100201),
        (lambda g: g["trajectory"]["joint_names"].pop(), -100207),
        (lambda g: g["trajectory"]["points"][0].update(velocities=[]), -100212),
        (lambda g: g["trajectory"]["points"][1].update(velocities=[0.1] * 6), -100213),
        (lambda g: g["trajectory"]["points"][0].update(positions=[0.5] * 6), -100204),
        (
            lambda g: g["trajectory"]["points"][1].update(time_from_start={"sec": 0, "nanosec": 0}),
            -100208,
        ),
    ],
)
def test_motoros2_accepts_every_goal_and_aborts_an_invalid_one_with_its_code(
    change: Any, code: int
) -> None:
    """`up.MOTO_ACCEPTS_THEN_ABORTS` with `up.MOTO_INIT_TRAJ_CODES`: no rejection ever crosses the
    bridge, only an abort with the ROS code times 100000 less MotoROS2's."""
    arm = FakeMotoRos2Arm(MOTO)
    goal = _goal(arm)
    change(goal)
    heard = Heard()
    assert arm.receive("g1", goal, *heard.args()[:2]) is None, "accepted"
    assert heard.status == GoalStatus.ABORTED and heard.values["error_code"] == code


def test_motoros2_refuses_a_goal_while_motion_is_not_possible() -> None:
    for flag, code in (("servo_on", -100106), ("e_stopped", -100103), ("in_error", -100101)):
        arm = FakeMotoRos2Arm(MOTO)
        setattr(arm, flag, flag != "servo_on")
        heard = Heard()
        arm.receive("g1", _goal(arm), *heard.args()[:2])
        assert heard.values["error_code"] == code, flag


def test_motoros2_answers_a_second_goal_only_once_the_first_has_ended() -> None:
    arm = FakeMotoRos2Arm(MOTO)
    first, second = Heard(), Heard()
    arm.receive("g1", _goal(arm, 10.0, 1.0), *first.args()[:2])
    arm.receive("g2", _goal(arm, 10.0, 1.0), *second.args()[:2])
    assert arm.goal.id == "g1" and second.results == []  # type: ignore[union-attr]
    _run(arm, 1.5)
    assert first.status == GoalStatus.SUCCEEDED
    # the waiting goal is answered once the first has ended, and by then its first point is no
    # longer where the arm is: one more reason quackd never sends a goal over a running one
    assert second.status == GoalStatus.ABORTED and second.values["error_code"] == -100204


def test_motoros2s_cancel_is_a_hold_and_its_alarm_an_abort_with_their_codes() -> None:
    arm = FakeMotoRos2Arm(MOTO)
    heard = Heard()
    arm.receive("g1", _goal(arm, 30.0, 3.0), *heard.args()[:2])
    _run(arm, 1.0)
    arm.cancel("g1")
    assert heard.status == GoalStatus.CANCELED and heard.values["error_code"] == -500300
    assert heard.values["error_string"] == up.MOTO_CANCEL.name
    alarmed = FakeMotoRos2Arm(MOTO)
    loud = Heard()
    alarmed.receive("g1", _goal(alarmed, 30.0, 3.0), *loud.args()[:2])
    _run(alarmed, 1.0)
    alarmed.alarm()
    assert loud.status == GoalStatus.ABORTED and loud.values["error_code"] == -100303
    assert not alarmed.motion_possible


def test_motoros2_judges_a_goal_short_of_its_tolerance_a_failure() -> None:
    arm = FakeMotoRos2Arm(MOTO, short_rad=math.radians(2.0))
    heard = Heard()
    arm.receive("g1", _goal(arm, 20.0, 1.0), *heard.args()[:2])
    _run(arm, 1.5)
    assert heard.status == GoalStatus.ABORTED and heard.values["error_code"] == -500301
    assert "Final position was outside tolerance" in heard.values["error_string"]


# ── the bridge ───────────────────────────────────────────────────────────────────────────────


def _client(bridge: FakeBridge) -> tuple[Any, Heard]:
    built = bridge.roslibpy()
    built.ros.run(1.0)
    return built.action(built.ros, bridge.body.action, up.MSG_FOLLOW_JOINT_TRAJECTORY.name), Heard()


def test_a_blocking_bridge_lands_a_cancel_only_after_the_move_has_ended() -> None:
    """`up.GOAL_THREAD_PARAM`: what quackd refuses such a bridge for, shown on the fake itself."""
    bridge = FakeBridge(UR, new_thread=False)
    actions, heard = _client(bridge)
    goal_id = actions.send_goal(_goal(bridge.arm, 30.0, 3.0), *heard.args())
    bridge.clock.advance(1.0)
    actions.cancel_goal(goal_id)
    bridge.clock.advance(3.0)
    assert heard.status == GoalStatus.SUCCEEDED, "the move ran to its end: the cancel came after"
    assert bridge.arm.goal is None


def test_the_same_cancel_on_a_threaded_bridge_stops_the_move() -> None:
    bridge = FakeBridge(UR, new_thread=True)
    actions, heard = _client(bridge)
    goal_id = actions.send_goal(_goal(bridge.arm, 30.0, 3.0), *heard.args())
    bridge.clock.advance(1.0)
    actions.cancel_goal(goal_id)
    assert heard.status == GoalStatus.CANCELED


def test_a_goal_the_glob_does_not_match_is_never_answered() -> None:
    bridge = FakeBridge(UR, actions_glob=("/some_other/*",))
    actions, heard = _client(bridge)
    actions.send_goal(_goal(bridge.arm), *heard.args())
    bridge.clock.advance(10.0)
    assert heard.results == heard.refusals == [] and bridge.arm.goal is None


def test_no_action_server_is_answered_after_a_second_and_a_rejection_at_once() -> None:
    bridge = FakeBridge(UR, server=False)
    actions, heard = _client(bridge)
    actions.send_goal(_goal(bridge.arm), *heard.args())
    bridge.clock.advance(0.9)
    assert heard.refusals == []
    bridge.clock.advance(0.2)
    assert heard.refusals == [{"status": GoalStatus.UNKNOWN, "values": up.NO_ACTION_SERVER.name}]
    inactive = FakeBridge(UR)
    inactive.arm.active = False  # type: ignore[attr-defined]
    actions, heard = _client(inactive)
    actions.send_goal(_goal(inactive.arm), *heard.args())
    assert heard.refusals == [{"status": GoalStatus.UNKNOWN, "values": up.GOAL_REJECTED.name}]


def test_a_bridge_without_actions_answers_no_goal() -> None:
    bridge = FakeBridge(UR, actions=False)
    actions, heard = _client(bridge)
    actions.send_goal(_goal(bridge.arm), *heard.args())
    bridge.clock.advance(5.0)
    assert heard.results == heard.refusals == []


def test_the_status_topics_publish_as_each_driver_does() -> None:
    """The UR's are latched and sent on change, the iisy's events on change and not latched, and
    MotoROS2's robot_status at a hundred a second."""
    for body, expect in ((UR, 2), (IISY, 0), (MOTO, 1)):
        bridge = FakeBridge(body)
        bridge.roslibpy().ros.run(1.0)
        bridge.clock.advance(0.05)  # published before anybody subscribed
        heard: list[Any] = []
        built = bridge.roslibpy()
        for topic, kind in body.status.topics if body.status else ():
            built.topic(built.ros, topic, kind).subscribe(heard.append)
        bridge.clock.advance(0.01)
        assert len(heard) == expect, body.robot_type


# ── roslibpy's queue ─────────────────────────────────────────────────────────────────────────


def test_a_goal_handed_over_on_a_dropped_socket_goes_out_when_the_socket_is_back() -> None:
    """`up.QUEUED_UNTIL_READY`: roslibpy holds what it is handed while the socket is down and sends
    it once its factory has reconnected by itself, a goal among them, which is why the transport
    sends none on a dropped socket. A client let go of for good sends nothing it held."""
    bridge = FakeBridge(UR)
    built = bridge.roslibpy()
    built.ros.run()
    actions = built.action(built.ros, UR.action, "control_msgs/action/FollowJointTrajectory")
    bridge.drop()
    heard = Heard()
    actions.send_goal(_goal(bridge.arm), *heard.args())
    assert "send_action_goal" not in bridge.operations()
    bridge.reconnect()
    assert bridge.operations().count("send_action_goal") == 1, (
        "it went out once the socket was back"
    )
    bridge.drop()
    actions.send_goal(_goal(bridge.arm), *heard.args())
    built.ros.terminate()
    bridge.reconnect()
    assert bridge.operations().count("send_action_goal") == 1, "a terminated client sends nothing"


# ── quackd going quiet ───────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("body", [UR, IISY, IIWA, MOTO], ids=lambda b: b.robot_type)
async def test_when_quackd_vanishes_mid_move_the_arm_finishes_that_move_and_holds(
    body: Any,
) -> None:
    """`up.ONE_MOVE_WHEN_QUIET`, which the manifest promises and the page says: nothing between
    quackd and the arm ends a goal on its client's silence, so the move it was given runs to its
    end, and then the arm holds there."""
    bridge = FakeBridge(body)
    transport = RosArmWs(
        None, body=body, roslibpy=bridge.roslibpy(), clock=bridge.clock, name="mock"
    )
    await transport.connect()
    joint = body.joints[0]
    goal = {j: v for j, v in (await transport.get_state()).extras["joints"].items()}
    target = goal[joint] + 15.0
    goal[joint] = target
    assert (await transport.send_intent(Intent.joint(goal, 4.0))).accepted
    await transport.sleep(1.0)
    bridge.client_gone()  # no cancel, no close: quackd is simply gone
    bridge.clock.advance(4.0)
    reached = math.degrees(bridge.arm.positions[joint])
    assert reached == pytest.approx(target, abs=0.01), "it finished the move it was given"
    bridge.clock.advance(5.0)
    assert math.degrees(bridge.arm.positions[joint]) == pytest.approx(reached, abs=1e-9), "held"
    assert "cancel_action_goal" not in bridge.operations()


def test_the_fake_clock_passes_only_while_something_sleeps() -> None:
    clock = FakeClock()
    ticks: list[float] = []
    clock.add_hook(lambda now, dt: ticks.append(dt))
    clock.advance(0.05)
    assert clock.now() == pytest.approx(0.05) and len(ticks) == 5
