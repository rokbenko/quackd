"""The only file in quackd allowed to spell a name an industrial arm's ROS 2 driver answers to.

Every constant is tagged VERIFIED (read from upstream source at a pinned commit, link given) or
UNVERIFIED (an assumption of ours, with what quackd does about it). `docs/adapters/rosarm/README.md`
is the human-readable version, and `tests/test_upstream_api.py` proves the UNVERIFIED names are
reachable from this package's transport, its bodies and its fake alone.

The family speaks rosbridge through roslibpy, the binding `quackd_rosbridge` already reads, so the
protocol's VERIFIED refs are that module's own, imported by name (ADR-0075's rule for a package
over a family, the other way round: here the new package is the family), and a re-pin of rosbridge
or roslibpy moves both. This module keeps what is the arms': the FollowJointTrajectory action and
the messages it carries, the JointTrajectoryController three of the four arms sit behind, and what
each arm's own driver names by default.

Read on 2026-11-08, at these pins: ros2_controllers on its jazzy branch (`PIN`), with what humble
does differently written into the notes from its own pin, `PIN_HUMBLE`; control_msgs on jazzy;
action_msgs in ros2/rcl_interfaces; the Universal Robots ROS 2 driver and description on jazzy;
KUKA's RSI driver and the LBR iisy's description; the LBR-Stack for the iiwa; MotoROS2 0.2.1, the
MotoMini's description, and industrial_msgs on the branch MotoROS2's client dependencies name.
JointState and JointTrajectory are read at rosbridge's own pin of ros2/common_interfaces. Nothing
here has been run against a bridge, a controller or an arm: the four bodies are described, and the
`mock` that rehearses them is a fake written from these sources (`FAKE_FROM_READING`).
"""

from __future__ import annotations

from quackd.upstream import UpstreamRef
from quackd_rosbridge.upstream_api import (
    ACTION_CANCEL_GOAL,
    ACTION_CLIENT,
    ACTION_GOAL,
    ACTION_RESULT_HANDLER,
    ACTION_SEND_GOAL,
    ACTIONS_SINCE,
    BRIDGE_NODE_PARAMS,
    CANCEL_WHILE_RUNNING,
    CLOSED_CLIENT_CANCELS_NOTHING,
    GOAL_DROPPED_BY_GLOB,
    GOAL_REJECTED,
    GOAL_RUNS_ON_THE_READER,
    GOAL_STATUS,
    GOAL_THREAD_DEFAULTS,
    GOAL_THREAD_PARAM,
    MSG_STRING,
    NO_ACTION_SERVER,
    ONE_REACTOR,
    OP_ACTION_FEEDBACK,
    OP_ACTION_RESULT,
    OP_CANCEL_ACTION_GOAL,
    OP_SEND_ACTION_GOAL,
    PARAM_NAME_FORM,
    PARAM_VALUE_JSON,
    QUEUED_UNTIL_READY,
    RESULT_TRUE_WHATEVER_STATUS,
    SRV_ACTION_SERVERS,
    SRV_GET_PARAM,
    SVC_ACTION_SERVERS,
    SVC_GET_PARAM,
    msg,
)

REPO = "https://github.com/ros-controls/ros2_controllers"
PIN = "bee185cbb98815116af9cfb00ff595b0b800e91a"
"""ros2_controllers on its jazzy branch: the JointTrajectoryController."""
PIN_HUMBLE = "65db73628ebac1948833f0fbdc60973c74d6cd0e"
"""The same on humble, cited in the notes only: the nightly follows one branch per repository."""
REPO_CONTROL_MSGS = "https://github.com/ros-controls/control_msgs"
PIN_CONTROL_MSGS = "ab911bf0746fb064b1a88a2c0d364d6b7abd477b"
REPO_ACTION_MSGS = "https://github.com/ros2/rcl_interfaces"
PIN_ACTION_MSGS = "56800da135660030bb816c72794afba15fe3a538"
REPO_UR = "https://github.com/UniversalRobots/Universal_Robots_ROS2_Driver"
PIN_UR = "44d40de0e3af064bfeba47d4b4a1f17e0c08407f"
REPO_UR_DESCRIPTION = "https://github.com/UniversalRobots/Universal_Robots_ROS2_Description"
PIN_UR_DESCRIPTION = "3852128295499b52f38f82171f5ff3f21ecdfa22"
REPO_KUKA = "https://github.com/kuka-ros/kuka_drivers"
PIN_KUKA = "15fbb6941356390c38bbd135a0676dfd96a050c0"
REPO_KUKA_DESCRIPTION = "https://github.com/kuka-ros/kuka_robot_descriptions"
PIN_KUKA_DESCRIPTION = "5ddbc41ce8726fd680bcc19562d78bcabfbdf96d"
REPO_LBR = "https://github.com/lbr-stack/lbr_fri_ros2_stack"
PIN_LBR = "d37b75142d5390132a6092305e6753f569e21f6a"
REPO_MOTOROS2 = "https://github.com/Yaskawa-Global/motoros2"
PIN_MOTOROS2 = "41ac074a879bb9f75fd1f96618ce54b4899cfd1a"
REPO_MOTOROS2_INTERFACES = "https://github.com/Yaskawa-Global/motoros2_interfaces"
PIN_MOTOROS2_INTERFACES = "b91a1948c080e4b03a5991341a99e53b73ed00b9"
REPO_MOTOMAN = "https://github.com/Yaskawa-Global/motoman_ros2_support_packages"
PIN_MOTOMAN = "a77f186048bd3c35fc3bb5142f9d9270dbebe69f"
REPO_INDUSTRIAL = "https://github.com/ros-industrial/industrial_core"
PIN_INDUSTRIAL = "d547cdcfdaf3bc0d46325215b8219b0a190c8e6c"
READ_ON = "2026-11-08"
MOTOROS2_VERSION = "0.2.1"

BRANCHES = {
    "ros-controls/ros2_controllers": "jazzy",
    "ros-controls/control_msgs": "jazzy",
    "UniversalRobots/Universal_Robots_ROS2_Driver": "jazzy",
    "UniversalRobots/Universal_Robots_ROS2_Description": "jazzy",
    "ros-industrial/industrial_core": "ros2_msgs_only",
}
"""The branch each pin sits on where it is not the repository's default, for the nightly
(`scripts/upstream_drift.py`). The rest are on their defaults: KUKA's two on master, the
LBR-Stack on jazzy, MotoROS2 and Yaskawa's descriptions on main, rcl_interfaces on rolling."""


def _blob(repo: str, pin: str, path: str, line: int | None) -> str:
    return f"{repo}/blob/{pin}/{path}" + (f"#L{line}" if line else "")


def jtc(path: str, line: int | None = None) -> str:
    return _blob(REPO, PIN, f"joint_trajectory_controller/{path}", line)


def control_msgs(path: str, line: int | None = None) -> str:
    return _blob(REPO_CONTROL_MSGS, PIN_CONTROL_MSGS, path, line)


def action_msgs(path: str, line: int | None = None) -> str:
    return _blob(REPO_ACTION_MSGS, PIN_ACTION_MSGS, path, line)


def ur(path: str, line: int | None = None) -> str:
    return _blob(REPO_UR, PIN_UR, path, line)


def ur_description(path: str, line: int | None = None) -> str:
    return _blob(REPO_UR_DESCRIPTION, PIN_UR_DESCRIPTION, path, line)


def kuka(path: str, line: int | None = None) -> str:
    return _blob(REPO_KUKA, PIN_KUKA, path, line)


def kuka_description(path: str, line: int | None = None) -> str:
    return _blob(REPO_KUKA_DESCRIPTION, PIN_KUKA_DESCRIPTION, path, line)


def lbr(path: str, line: int | None = None) -> str:
    return _blob(REPO_LBR, PIN_LBR, path, line)


def motoros2(path: str, line: int | None = None) -> str:
    return _blob(REPO_MOTOROS2, PIN_MOTOROS2, path, line)


def motoros2_interfaces(path: str, line: int | None = None) -> str:
    return _blob(REPO_MOTOROS2_INTERFACES, PIN_MOTOROS2_INTERFACES, path, line)


def motoman(path: str, line: int | None = None) -> str:
    return _blob(REPO_MOTOMAN, PIN_MOTOMAN, path, line)


def industrial(path: str, line: int | None = None) -> str:
    return _blob(REPO_INDUSTRIAL, PIN_INDUSTRIAL, path, line)


_JTC = "src/joint_trajectory_controller.cpp"
_TRAJECTORY = "src/trajectory.cpp"
_JTC_PARAMS = "src/joint_trajectory_controller_parameters.yaml"
_UR_CONFIG = "ur_robot_driver/config/ur_controllers.yaml"
_UR_LAUNCH = "ur_robot_driver/launch/ur_control.launch.py"
_UR_GPIO = "ur_controllers/src/gpio_controller.cpp"
_KUKA_JTC = "kuka_rsi_driver/config/joint_trajectory_controller_config.yaml"
_KUKA_LAUNCH = "kuka_rsi_driver/launch/startup.launch.py"
_KUKA_EVENTS = "controllers/kuka_event_broadcaster/src/kuka_event_broadcaster.cpp"
_FJT = "src/ActionServer_FJT.c"
_FJT_H = "src/ActionServer_FJT.h"
_MOTION = "src/MotionControl.c"

# ── the action and the messages it carries ──────────────────────────────────────────────────

MSG_FOLLOW_JOINT_TRAJECTORY = UpstreamRef(
    "control_msgs/action/FollowJointTrajectory",
    "VERIFIED",
    control_msgs("control_msgs/action/FollowJointTrajectory.action", 2),
    "the goal is a trajectory and its tolerances; the result an error_code, 0 SUCCESSFUL to -5 "
    "GOAL_TOLERANCE_VIOLATED (lines 33 to 39), and an error_string (line 49); the feedback the "
    "desired, actual and error points (lines 52 to 56)",
)
MSG_JOINT_TRAJECTORY = UpstreamRef(
    "trajectory_msgs/msg/JointTrajectory",
    "VERIFIED",
    msg("trajectory_msgs/msg/JointTrajectory.msg", 3),
    "{header, joint_names, points}, the names ordered as every point's values are (lines 5 to 7)",
)
MSG_JOINT_TRAJECTORY_POINT = UpstreamRef(
    "trajectory_msgs/msg/JointTrajectoryPoint",
    "VERIFIED",
    msg("trajectory_msgs/msg/JointTrajectoryPoint.msg", 8),
    "positions in radians for a revolute joint (lines 5 to 8), velocities, accelerations, effort "
    "and time_from_start (line 26)",
)
MSG_JOINT_STATE = UpstreamRef(
    "sensor_msgs/msg/JointState",
    "VERIFIED",
    msg("sensor_msgs/msg/JointState.msg", 22),
    "{header, name, position, velocity, effort}; a revolute joint's position in radians (line 4), "
    "each joint known by its name (line 8), so quackd reads them by name and never by place",
)
MSG_GOAL_STATUS = UpstreamRef(
    "action_msgs/msg/GoalStatus",
    "VERIFIED",
    action_msgs("action_msgs/msg/GoalStatus.msg", 7),
    "0 UNKNOWN, 1 ACCEPTED, 2 EXECUTING, 3 CANCELING, 4 SUCCEEDED, 5 CANCELED and 6 ABORTED "
    "(lines 7 to 26)",
)

# ── the JointTrajectoryController, under the UR5e, the iisy and the iiwa ────────────────────

JTC_ACTION = UpstreamRef(
    "<controller>/follow_joint_trajectory",
    "VERIFIED",
    jtc(_JTC, 1091),
    "the action server is the controller's name and /follow_joint_trajectory (lines 1088 to "
    "1094); the controller also takes a JointTrajectory on ~/joint_trajectory (line 1037), which "
    "quackd does not use",
)
JTC_INACTIVE_REJECTS = UpstreamRef(
    "Can't accept new action goals. Controller is not running.",
    "VERIFIED",
    jtc(_JTC, 1470),
    "a goal to an inactive controller is rejected (lines 1466 to 1472), and the same on humble "
    "at 65db736 (line 1260)",
)
JTC_ALL_JOINTS = UpstreamRef(
    "allow_partial_joints_goal",
    "VERIFIED",
    jtc(_JTC_PARAMS, 75),
    "false by default (line 77), so a goal must name every joint (the check at lines 1722 to "
    "1733 of the controller); the same default on humble (parameters line 44)",
)
JTC_END_AT_REST = UpstreamRef(
    "allow_nonzero_velocity_at_trajectory_end",
    "VERIFIED",
    jtc(_JTC_PARAMS, 129),
    "false by default on jazzy (line 131), so a goal whose last point still moves is rejected "
    "(lines 1764 to 1778 of the controller); true by default on humble (parameters line 100)",
)
JTC_STAMP_ZERO = UpstreamRef(
    "a header stamp of zero starts the trajectory now",
    "VERIFIED",
    jtc(_JTC, 1782),
    "a stamp other than zero that ends in the past is rejected (lines 1781 to 1797)",
)
JTC_TIMES_INCREASE = UpstreamRef(
    "time_from_start strictly increasing",
    "VERIFIED",
    jtc(_JTC, 1803),
    "a point no later than the one before it is rejected (lines 1799 to 1811)",
)
JTC_QUINTIC = UpstreamRef(
    "do quintic interpolation",
    "VERIFIED",
    jtc(_TRAJECTORY, 367),
    "between two points that both carry velocities and accelerations (lines 279 to 281, 365 to "
    "367), as quackd's goal's do; between two at rest its speed at the middle is 15/8 of its mean",
)
JTC_CANCEL_HOLDS = UpstreamRef(
    "a cancel holds the arm where it is",
    "VERIFIED",
    jtc(_JTC, 1483),
    "the running goal ends CANCELED and the controller holds its present position (lines 1495 to "
    "1510), or first decelerates where constraints.decelerate_on_cancel is set, which is false "
    "by default (parameters lines 243 to 250)",
)
JTC_PREEMPTS = UpstreamRef(
    "a new goal ends the running one",
    "VERIFIED",
    jtc(_JTC, 1872),
    "on jazzy the old goal ends ABORTED with INVALID_GOAL and 'Current goal preempted by new "
    "incoming action.' (lines 1872 to 1885); on humble at 65db736 it ends CANCELED with "
    "INVALID_GOAL and 'Current goal cancelled due to new incoming action.' (lines 1603 to 1614). "
    "quackd never sends a goal over a running one",
)
JTC_DEACTIVATE_ABORTS = UpstreamRef(
    "Current goal cancelled during deactivate transition.",
    "VERIFIED",
    jtc(_JTC, 1325),
    "a goal running when the controller is deactivated ends ABORTED with INVALID_GOAL (lines "
    "1315 to 1328), which is how a UR program that stops ends a goal",
)
JTC_SUCCEEDS_AT_THE_END = UpstreamRef(
    "Goal successfully reached!",
    "VERIFIED",
    jtc(_JTC, 501),
    "SUCCEEDED once the last point's time has passed with no goal tolerance violated (lines 495 "
    "to 512)",
)
JTC_ZERO_TOLERANCE = UpstreamRef(
    "a tolerance of 0 is not checked",
    "VERIFIED",
    jtc("include/joint_trajectory_controller/tolerances.hpp", 309),
    "lines 308 to 311, and every joint's trajectory and goal tolerance defaults to 0 (parameters "
    "lines 252 to 261): with them left alone, SUCCEEDED says the time ran out and nothing about "
    "where the arm is, so quackd judges arrival from /joint_states itself",
)
JTC_GOAL_TIME = UpstreamRef(
    "constraints.goal_time",
    "VERIFIED",
    jtc(_JTC_PARAMS, 234),
    "0 by default, and then the controller 'will wait a potentially infinite amount of time' "
    "(lines 236 to 238): a goal whose arm never comes within its tolerance is ended by nobody but "
    "the client",
)
JTC_NO_CLIENT_TIMEOUT = UpstreamRef(
    "cmd_timeout",
    "VERIFIED",
    jtc(_JTC_PARAMS, 134),
    "0 by default, which is off, and counted from the end of the trajectory (lines 136 to 140): "
    "nothing in the controller ends a goal when its client goes quiet",
)

# ── Universal Robots UR5e: ur_robot_driver ──────────────────────────────────────────────────

UR_ACTION = UpstreamRef(
    "/scaled_joint_trajectory_controller/follow_joint_trajectory",
    "VERIFIED",
    ur(_UR_LAUNCH, 384),
    "the initial joint controller by default (lines 383 to 384), a JointTrajectoryController "
    "subclass (ur_controllers.yaml lines 18 to 19), so its action is JTC_ACTION's",
)
UR_JOINTS = UpstreamRef(
    "shoulder_pan_joint, shoulder_lift_joint, elbow_joint, wrist_1_joint, wrist_2_joint, "
    "wrist_3_joint",
    "VERIFIED",
    ur(_UR_CONFIG, 124),
    "the controller's joints in its order (lines 126 to 132), each behind tf_prefix, which is "
    "empty by default (ur_control.launch.py lines 345 to 346)",
)
UR_TOLERANCES = UpstreamRef(
    "goal 0.1 rad, trajectory 0.2 rad",
    "VERIFIED",
    ur(_UR_CONFIG, 144),
    "per joint (lines 144 to 149), with goal_time 0.0 (line 143) and allow_partial_joints_goal "
    "false (line 140): a goal whose arm stays more than 0.1 rad off waits for ever",
)
UR_STATUS = UpstreamRef(
    "/io_and_status_controller/robot_program_running, /io_and_status_controller/safety_mode",
    "VERIFIED",
    ur(_UR_GPIO, 281),
    "std_msgs/msg/Bool and ur_dashboard_msgs/msg/SafetyMode (lines 280 to 284) on the controller "
    "ur_controllers.yaml names io_and_status_controller (line 6), reliable and transient-local "
    "(lines 264 to 266), and each published only when its value changes (lines 53 to 67)",
)
UR_SAFETY_MODES = UpstreamRef(
    "ur_dashboard_msgs/msg/SafetyMode",
    "VERIFIED",
    ur("ur_dashboard_msgs/msg/SafetyMode.msg", 15),
    "{mode}: 1 NORMAL, 2 REDUCED, 3 PROTECTIVE_STOP, 5 SAFEGUARD_STOP, 6 SYSTEM_EMERGENCY_STOP and "
    "7 ROBOT_EMERGENCY_STOP among thirteen (lines 1 to 13)",
)
UR_BEFORE_A_GOAL = UpstreamRef(
    "the External Control program playing",
    "VERIFIED",
    ur("ur_robot_driver/doc/usage/startup.rst", 83),
    "unless the driver runs headless, a person starts the External Control program on the "
    "pendant, or through the dashboard in remote control mode (lines 83 to 97); until it plays "
    "the scaled controller is inactive",
)
UR_LIMITS = UpstreamRef(
    "the UR5e's joint limits",
    "VERIFIED",
    ur_description("config/ur5e/joint_limits.yaml", 10),
    "every joint -360 to 360 degrees but the elbow, -180 to 180, which is halved on purpose for "
    "planning; 180 degrees per second each (lines 10 to 80)",
)

# ── KUKA LBR iisy 3 R760: kuka_rsi_driver ───────────────────────────────────────────────────

IISY_JOINTS = UpstreamRef(
    "joint_1, joint_2, joint_3, joint_4, joint_5, joint_6",
    "VERIFIED",
    kuka(_KUKA_JTC, 3),
    "the stock joint_trajectory_controller's joints (lines 1 to 9) over position command and "
    "state alone, with allow_nonzero_velocity_at_trajectory_end true (line 17) and every other "
    "parameter the controller's default, so /joint_trajectory_controller/follow_joint_trajectory "
    "under the launch's empty namespace (startup.launch.py line 443)",
)
IISY_INACTIVE_FIRST = UpstreamRef(
    "every controller spawned inactive",
    "VERIFIED",
    kuka(_KUKA_LAUNCH, 340),
    "the launch spawns the joint_trajectory_controller and the event_broadcaster with --inactive "
    "(lines 339 to 340, 349 to 353) beside robot_manager, a lifecycle node (line 301): until "
    "somebody activates it, a goal is refused as JTC_INACTIVE_REJECTS says",
)
IISY_EVENTS = UpstreamRef(
    "/event_broadcaster/hardware_event",
    "VERIFIED",
    kuka(_KUKA_EVENTS, 39),
    "kuka_driver_interfaces/msg/HardwareEvent {robot_name, event} (lines 38 to 39, 153 to 154), "
    "published only when the event changes (lines 150 to 160), so a late subscriber may hear none",
)
IISY_EVENT_CODES = UpstreamRef(
    "3 CONTROL_STARTED, 4 CONTROL_STOPPED, 6 ERROR",
    "VERIFIED",
    kuka("kuka_drivers_core/include/kuka_drivers_core/hardware_event.hpp", 29),
    "the events of lines 25 to 33",
)
IISY_NAN_READINGS = UpstreamRef(
    "velocity and effort NaN",
    "VERIFIED",
    kuka("kuka_drivers/doc/1_RSI.md", 243),
    "an interface the RSI configuration does not map stays at its default, NaN; quackd reads "
    "positions alone, and a position that is not finite is no reading",
)
IISY_BEFORE_A_GOAL = UpstreamRef(
    "ros2 lifecycle set robot_manager configure, then activate",
    "VERIFIED",
    kuka("kuka_drivers/doc/0_Overview.md", 64),
    "the three steps that start external control on an iiQKA.OS2 robot (lines 64 to 69), and "
    "the activation is what activates the controllers (line 53)",
)
IISY_LIMITS = UpstreamRef(
    "the LBR iisy 3 R760's joint limits",
    "VERIFIED",
    kuka_description("kuka_lbr_iisy_support/urdf/lbr_iisy3_r760_macro.xacro", 151),
    "joint_1 -3.229 to 3.229 rad, joint_2 -4.014 to 0.873, joint_3 -2.618 to 2.618, joint_4 -3.054 "
    "to 3.054, joint_5 -1.920 to 1.920, joint_6 -3.840 to 3.840; 3.49 rad/s on joints 1 to 3, "
    "5.24 on 4 and 5, 6.98 on 6 (lines 148 to 189)",
)

# ── KUKA LBR iiwa 7 R800: the LBR-Stack over FRI ────────────────────────────────────────────

IIWA_JOINTS = UpstreamRef(
    "lbr_A1, lbr_A2, lbr_A3, lbr_A4, lbr_A5, lbr_A6, lbr_A7",
    "VERIFIED",
    lbr("lbr_ros2_control/config/controllers/hardware.yaml", 71),
    "the joint_trajectory_controller's joints (lines 71 to 80) with no constraints set, so every "
    "tolerance is JTC_ZERO_TOLERANCE's",
)
IIWA_NAMESPACE = UpstreamRef(
    "/lbr",
    "VERIFIED",
    lbr("lbr_bringup/launch/hardware.launch.py", 29),
    "the namespace by default (lines 27 to 31), with joint_trajectory_controller the default ctrl "
    "(lines 52 to 55): /lbr/joint_trajectory_controller/follow_joint_trajectory and "
    "/lbr/joint_states, and the robot_state_publisher runs under it too (line 94), so the "
    "description is /lbr/robot_state_publisher:robot_description",
)
IIWA_OPEN_LOOP = UpstreamRef(
    "open_loop: true",
    "VERIFIED",
    lbr("lbr_ros2_control/config/lbr_system_config.yaml", 32),
    "'for position control, LBR works best in open_loop control mode' (lines 30 to 32), and in "
    "it the state's joint position is set from the command (lbr_fri_ros2/src/async_client.cpp "
    "lines 93 to 99): /lbr/joint_states then reports what was sent, not where the arm is",
)
IIWA_BEFORE_A_GOAL = UpstreamRef(
    "LBRServer on the smartPAD",
    "VERIFIED",
    lbr("lbr_bringup/doc/lbr_bringup.rst", 76),
    "a person launches LBRServer with the send period, the client's address and the control "
    "mode, always in T1 first (lines 66 to 85), and then the stack's hardware launch",
)
IIWA_LIMITS = UpstreamRef(
    "the LBR iiwa 7 R800's joint limits",
    "VERIFIED",
    lbr("lbr_ros2_control/system_integration/iiwa7/joint_limits.yaml", 1),
    "A1, A3 and A5 -169 to 169 degrees, A2, A4 and A6 -119 to 119, A7 -174 to 174, each a degree "
    "inside the robot's own; 98, 98, 100, 130, 140, 180 and 180 degrees per second (lines 1 to 49)",
)

# ── Yaskawa MotoMini: MotoROS2 on the YRC1000micro ──────────────────────────────────────────

MOTO_ACTION = UpstreamRef(
    "follow_joint_trajectory",
    "VERIFIED",
    motoros2("src/RosApiNameConstants.h", 32),
    "/follow_joint_trajectory under the default empty namespace, beside robot_status and "
    "joint_states (lines 16 to 17). It is MotoROS2's own action server and not a "
    "JointTrajectoryController",
)
MOTO_JOINTS = UpstreamRef(
    "joint_%d",
    "VERIFIED",
    motoros2("src/ConfigFile.h", 37),
    "a single group's joints are joint_1 to joint_6 by default",
)
MOTO_ACCEPTS_THEN_ABORTS = UpstreamRef(
    "every goal is accepted",
    "VERIFIED",
    motoros2(_FJT, 282),
    "an invalid goal is accepted and then aborted with its reason in error_string (lines 235 to "
    "280), and its error_code is the ROS code times 100000 less MotoROS2's own (line 46)",
)
MOTO_TWO_POINTS = UpstreamRef(
    "at least 2 points, at most 200",
    "VERIFIED",
    motoros2(_FJT_H, 12),
    "lines 11 to 12; fewer than two is INIT_TRAJ_TOO_SMALL (MotionControl.c lines 220 to 221)",
)
MOTO_EVERY_JOINT = UpstreamRef(
    "every joint, with positions and velocities in every point",
    "VERIFIED",
    motoros2(_MOTION, 71),
    "lines 71 to 93, the joints matched by name (lines 106 to 121)",
)
MOTO_TIMES = UpstreamRef(
    "time_from_start 0 only on the first point",
    "VERIFIED",
    motoros2(_MOTION, 258),
    "and never earlier than the point before it (lines 250 to 276)",
)
MOTO_END_AT_REST = UpstreamRef(
    "The final point in a trajectory must specify a target velocity of '0'.",
    "VERIFIED",
    motoros2(_MOTION, 284),
    "lines 278 to 286",
)
MOTO_START_POINT = UpstreamRef(
    "the first point where the arm is commanded",
    "VERIFIED",
    motoros2(_MOTION, 155),
    "a first point more than START_MAX_PULSE_DEVIATION pulses from the command position is "
    "INIT_TRAJ_INVALID_STARTING_POS (lines 151 to 175)",
)
MOTO_CUBIC = UpstreamRef(
    "accCoef1",
    "VERIFIED",
    motoros2(_MOTION, 395),
    "MotoROS2 plays the points as a cubic in positions and velocities (lines 395 to 398, 437 to "
    "446); between two at rest its speed at the middle is 3/2 of its mean",
)
MOTO_GOAL_TOLERANCE = UpstreamRef(
    "0.01 rad and 0.5 s",
    "VERIFIED",
    motoros2(_FJT_H, 14),
    "the goal tolerance and the time tolerance MotoROS2 uses when a goal gives none (lines 14 to "
    "15)",
)
MOTO_CANCEL = UpstreamRef(
    "Goal was cancelled by the user.",
    "VERIFIED",
    motoros2(_FJT, 741),
    "a cancel stops the motion and keeps the job running (lines 775 to 787), and the goal ends "
    "CANCELED with GOAL_TOLERANCE_VIOLATED times 100000 less FAIL_TRAJ_CANCEL, which is 300 "
    "(ErrorHandling.h line 63): -500300 (lines 736 to 746)",
)
MOTO_ALARM_ABORTS = UpstreamRef(
    "Goal was aborted due to an alarm or error (check RobotStatus messages).",
    "VERIFIED",
    motoros2(_FJT, 755),
    "an alarm mid-move stops the motion and aborts the goal (lines 408 to 415) with INVALID_GOAL "
    "times 100000 less FAIL_TRAJ_ALARM, 303: -100303 (lines 749 to 760)",
)
MOTO_ECO_REENABLES = UpstreamRef(
    "Energy saving function is active. Re-enabling the robot.",
    "VERIFIED",
    motoros2(_FJT, 183),
    "a goal that arrives while the energy saving function has turned the servos off makes "
    "MotoROS2 call its own start_traj_mode, which turns them on (lines 181 to 199). quackd sends "
    "no such call, and its goal can still cause this one",
)
MOTO_INIT_TRAJ_CODES = UpstreamRef(
    "motoros2_interfaces/msg/InitTrajEnum",
    "VERIFIED",
    motoros2_interfaces("msg/InitTrajEnum.msg", 20),
    "the codes a goal MotoROS2 will not run is aborted with, 201 INIT_TRAJ_TOO_SMALL to 213 "
    "INIT_TRAJ_INVALID_ENDING_VELOCITY (lines 20 to 57), each with its sentence; the fake's are "
    "these",
)
MOTO_NOT_READY_CODES = UpstreamRef(
    "motoros2_interfaces/msg/MotionReadyEnum",
    "VERIFIED",
    motoros2_interfaces("msg/MotionReadyEnum.msg", 22),
    "the codes a goal sent while motion is not possible is aborted with, 101 NOT_READY_ALARM to "
    "118 NOT_READY_SERVO_ON_TIMEOUT (lines 22 to 71), NOT_READY_SERVO_OFF among them",
)
MOTO_STATUS = UpstreamRef(
    "robot_status",
    "VERIFIED",
    motoros2("src/ControllerStatusIO.c", 664),
    "drives_powered from the servos, e_stopped, in_motion, motion_possible from motion ready, "
    "and in_error from any fault or MotoROS2 error (lines 664 to 673)",
)
MOTO_BEFORE_A_GOAL = UpstreamRef(
    "start_traj_mode",
    "VERIFIED",
    motoros2_interfaces("srv/StartTrajMode.srv", 6),
    "enables the robot and starts trajectory mode, which must be done before a goal; it succeeds "
    "only in AUTO, with no e-stop and no error (lines 6 to 12). A person calls it: quackd never "
    "does",
)
MOTO_LIMITS = UpstreamRef(
    "the MotoMini's joint limits",
    "VERIFIED",
    motoman("motoman_motomini_support/urdf/motomini_macro.xacro", 106),
    "joint_1 -170 to 170 degrees, joint_2 -85 to 90, joint_3 -50 to 90, joint_4 -140 to 140, "
    "joint_5 -30 to 210, joint_6 -360 to 360; 315, 315, 420, 600, 600 and 600 degrees per second "
    "(lines 101 to 142). MotoROS2 publishes none: the owner runs a robot_state_publisher with this",
)
ROBOT_STATUS = UpstreamRef(
    "industrial_msgs/msg/RobotStatus",
    "VERIFIED",
    industrial("industrial_msgs/msg/RobotStatus.msg", 14),
    "e_stopped, drives_powered, motion_possible, in_motion and in_error, each a TriState (lines "
    "14 to 29)",
)
TRI_STATE = UpstreamRef(
    "industrial_msgs/msg/TriState",
    "VERIFIED",
    industrial("industrial_msgs/msg/TriState.msg", 3),
    "{val}: 1 TRUE, 0 FALSE and -1 UNKNOWN (lines 8 to 22)",
)

# ── UNVERIFIED: what quackd assumes, and what it does about each ────────────────────────────

STOCK_NAMES = UpstreamRef(
    "STOCK_NAMES",
    "UNVERIFIED",
    ur(_UR_CONFIG, 124),
    "the action, the joint names, the readings topic and the status topics in each body record "
    "are a stock bringup's defaults, read in the drivers' own files; an installed robot's "
    "prefixes, namespaces and controller choice change them. The address sets the action, the "
    "readings topic and a prefix for every joint's name, and turns the status read off; the "
    "connect checks the action and the joints against what the bridge lists, and a status topic "
    "nothing publishes is read as no status at all",
)
BRIDGE_NODE_NAME = UpstreamRef(
    "BRIDGE_NODE_NAME",
    "UNVERIFIED",
    jtc(_JTC, 1091),
    "that the bridge's node is /rosbridge_websocket, un-namespaced, and that rosapi reaches its "
    "parameters (BRIDGE_NODE_PARAMS); quackd refuses a bridge whose answer it cannot read, and "
    "?bridge_node= names another",
)
STATUS_TYPES_LOADED = UpstreamRef(
    "STATUS_TYPES_LOADED",
    "UNVERIFIED",
    ur(_UR_GPIO, 281),
    "that the bridge's machine has the driver's message package for its status topic "
    "(ur_dashboard_msgs, kuka_driver_interfaces, industrial_msgs); without it no status arrives, "
    "report_state says none has been read, and can_move believes the driver",
)
UR_PROTECTIVE_STOP = UpstreamRef(
    "UR_PROTECTIVE_STOP",
    "UNVERIFIED",
    ur("ur_dashboard_msgs/msg/SafetyMode.msg", 3),
    "how a protective stop reaches the controller, as a speed scaling of 0 that keeps the goal or "
    "as a stopped program that aborts it: quackd takes any safety mode but NORMAL and REDUCED, or "
    "a program that is not running, as the arm having stopped itself",
)
IIWA_STATUS = UpstreamRef(
    "IIWA_STATUS",
    "UNVERIFIED",
    lbr("lbr_ros2_control/src/controllers/lbr_state_broadcaster.cpp", 19),
    "where FRI's session state is published: the stack's code names the topic lbr_state and its "
    "documentation state, so quackd reads no status on the iiwa and believes the driver",
)
IIWA_READINGS = UpstreamRef(
    "IIWA_READINGS",
    "UNVERIFIED",
    lbr("lbr_ros2_control/config/lbr_system_config.yaml", 32),
    "that the iiwa runs the stock open_loop: true, so its readings are the commands sent "
    "(IIWA_OPEN_LOOP): report_state says so on this body, and an arrival judged there proves the "
    "command arrived. An owner who sets open_loop false gets measured positions, which quackd "
    "cannot tell from here",
)
MOTO_FIRST_POINT = UpstreamRef(
    "MOTO_FIRST_POINT",
    "UNVERIFIED",
    motoros2(_MOTION, 155),
    "that /joint_states on a MotoMini at rest is close enough to the command position for a first "
    "point taken from it to be accepted (MOTO_START_POINT); one that is not is aborted with its "
    "reason, which the verb says",
)
MOTO_SINGLE_GOAL = UpstreamRef(
    "MOTO_SINGLE_GOAL",
    "UNVERIFIED",
    motoros2(_FJT, 282),
    "that MotoROS2 answers no second goal while one runs, inferred from its single goal handle; "
    "quackd never sends a goal over a running one, on any body",
)
ONE_MOVE_WHEN_QUIET = UpstreamRef(
    "ONE_MOVE_WHEN_QUIET",
    "UNVERIFIED",
    jtc(_JTC_PARAMS, 134),
    "that an arm whose client goes quiet finishes the move it was given and holds there, put "
    "together from CLOSED_CLIENT_CANCELS_NOTHING, JTC_NO_CLIENT_TIMEOUT and MotoROS2 watching "
    "only its own agent; no arm has shown it. quackd keeps every move inside MOVE_MAX_S and the "
    "manifest says the arm has no deadman quackd can feed",
)
FAKE_FROM_READING = UpstreamRef(
    "FAKE_FROM_READING",
    "UNVERIFIED",
    jtc(_JTC, 1483),
    "that the fake bridge and the fake arms behave as these sources read: they were written from "
    "reading them and have met no bridge, no controller and no arm",
)

PROTOCOL_REFS: tuple[UpstreamRef, ...] = (
    OP_SEND_ACTION_GOAL,
    OP_CANCEL_ACTION_GOAL,
    OP_ACTION_FEEDBACK,
    OP_ACTION_RESULT,
    ACTIONS_SINCE,
    GOAL_THREAD_PARAM,
    GOAL_RUNS_ON_THE_READER,
    GOAL_THREAD_DEFAULTS,
    BRIDGE_NODE_PARAMS,
    GOAL_DROPPED_BY_GLOB,
    NO_ACTION_SERVER,
    GOAL_REJECTED,
    RESULT_TRUE_WHATEVER_STATUS,
    CANCEL_WHILE_RUNNING,
    CLOSED_CLIENT_CANCELS_NOTHING,
    SVC_ACTION_SERVERS,
    SRV_ACTION_SERVERS,
    SVC_GET_PARAM,
    SRV_GET_PARAM,
    PARAM_NAME_FORM,
    PARAM_VALUE_JSON,
    MSG_STRING,
    ACTION_CLIENT,
    ACTION_SEND_GOAL,
    ACTION_CANCEL_GOAL,
    ACTION_GOAL,
    GOAL_STATUS,
    ACTION_RESULT_HANDLER,
    QUEUED_UNTIL_READY,
    ONE_REACTOR,
)
"""The binding's refs this family relies on, imported from `quackd_rosbridge.upstream_api` by name:
the same objects, so a re-pin there moves them here, and the doc page lists them with the rest."""


def all_refs() -> list[UpstreamRef]:
    """This family's own refs and the binding's it relies on, each once."""
    seen: dict[int, UpstreamRef] = {}
    for value in globals().values():
        if isinstance(value, UpstreamRef):
            seen.setdefault(id(value), value)
    return list(seen.values())


def refs_by_status(status: str) -> list[UpstreamRef]:
    return [r for r in all_refs() if r.status == status]
