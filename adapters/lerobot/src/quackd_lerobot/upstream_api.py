"""The only file in quackd allowed to spell a LeRobot name (ADR-0022).

Every constant is tagged VERIFIED (read from upstream source, link given) or UNVERIFIED
(an assumption of ours, with what quackd does about it). `docs/adapters/lerobot/README.md` is the
human-readable version; `tests/test_upstream_api.py` proves UNVERIFIED names are only
reachable from the experimental `real` backend.

Source of truth: https://github.com/huggingface/lerobot at commit
fbb811fca92504439792b97d216f0d00c2268382 (main, 2026-09-01). First read 2026-09-02; read
again on 2026-09-13, at the same commit, when the arm adapter was hardened, which is where
most of the rows below come from. PyPI had 0.6.1 on both days; the pinned tree calls itself
0.6.2. LeRobot is never imported outside the `real` backend.

First run against an arm on 2026-09-15, on lerobot 0.6.1 (Windows 11, Python 3.12.12):
connect, `get_observation`, `send_action`, the two register reads and `disconnect` behaved
as the rows below say. The rows still carry the status they were read with, because one
afternoon on one arm confirms what was exercised and says nothing about the rest.

A policy's names are not here. They live in `policy/upstream_api.py`, read against 0.6.1, the
version a policy server runs, because a checkpoint is loaded there and never beside the arm's
bus.
"""

from __future__ import annotations

from quackd.upstream import UpstreamRef

REPO = "https://github.com/huggingface/lerobot"
PIN = "fbb811fca92504439792b97d216f0d00c2268382"
READ_ON = "2026-09-13"
PYPI_VERSION_READ = "0.6.1"


def src(path: str, line: int | None = None) -> str:
    return f"{REPO}/blob/{PIN}/{path}" + (f"#L{line}" if line else "")


_ROBOT = "src/lerobot/robots/robot.py"
_UTILS = "src/lerobot/robots/utils.py"
_SO = "src/lerobot/robots/so_follower/so_follower.py"
_SO_CFG = "src/lerobot/robots/so_follower/config_so_follower.py"
_TYPES = "src/lerobot/lerobot_types.py"
_BUS = "src/lerobot/motors/motors_bus.py"
_FEETECH = "src/lerobot/motors/feetech/feetech.py"
_TABLES = "src/lerobot/motors/feetech/tables.py"
_CONSTANTS = "src/lerobot/utils/constants.py"
_CAMERA = "src/lerobot/cameras/camera.py"
_OPENCV = "src/lerobot/cameras/opencv/camera_opencv.py"
_OPENCV_CFG = "src/lerobot/cameras/opencv/configuration_opencv.py"
_CAM_CFG = "src/lerobot/cameras/configs.py"
_CAMERAS_INIT = "src/lerobot/cameras/__init__.py"
_KINEMATICS = "src/lerobot/model/kinematics.py"
_DECORATORS = "src/lerobot/utils/decorators.py"

# ── package ─────────────────────────────────────────────────────────────────────────────

PACKAGE = UpstreamRef("lerobot", "VERIFIED", src("pyproject.toml", 27), "PyPI and import name")
PYTHON = UpstreamRef(
    ">=3.12",
    "VERIFIED",
    src("pyproject.toml", 32),
    "requires-python; quackd's floor is 3.11, so the extra carries a python_version marker",
)
VERSION_AT_PIN = UpstreamRef("0.6.2", "VERIFIED", src("pyproject.toml", 28), "PyPI had 0.6.1")
FEETECH_EXTRA = UpstreamRef(
    "lerobot[feetech]",
    "VERIFIED",
    src("pyproject.toml", 178),
    "feetech-servo-sdk (imported as scservo_sdk) and pyserial live in this extra and not in "
    "lerobot's base dependencies, so a plain lerobot imports cleanly and then cannot open the "
    "arm's port. quackd[lerobot] asks for lerobot[feetech] and doctor has a row for the SDK",
)

# ── the Robot interface ─────────────────────────────────────────────────────────────────

ROBOT_BASE = UpstreamRef(
    "lerobot.robots.Robot", "VERIFIED", src(_ROBOT, 30), "the abstract base every robot implements"
)
ROBOT_CONNECT = UpstreamRef(
    "Robot.connect(calibrate=True)",
    "VERIFIED",
    src(_ROBOT, 125),
    "quackd passes calibrate=False: calibration is interactive (see ROBOT_CALIBRATE). The SO "
    "follower's connect() runs bus.connect() and configure() and writes no calibration into "
    "the motors at all, so the file on disk and the arm must already agree",
)
ROBOT_DISCONNECT = UpstreamRef("Robot.disconnect()", "VERIFIED", src(_ROBOT, 209))
ROBOT_GET_OBSERVATION = UpstreamRef(
    "Robot.get_observation() -> dict",
    "VERIFIED",
    src(_ROBOT, 182),
    "a flat dict: '<motor>.pos' floats plus one array per camera, keyed by camera name",
)
ROBOT_SEND_ACTION = UpstreamRef(
    "Robot.send_action(action: dict) -> dict",
    "VERIFIED",
    src(_ROBOT, 194),
    "'<motor>.pos' -> goal; returns what was actually sent, possibly clipped",
)
ROBOT_OBSERVATION_FEATURES = UpstreamRef(
    "Robot.observation_features",
    "VERIFIED",
    src(_ROBOT, 90),
    "key -> float, or a (h, w, c) shape tuple for a camera; usable before connect()",
)
ROBOT_ACTION_FEATURES = UpstreamRef("Robot.action_features", "VERIFIED", src(_ROBOT, 104))
ROBOT_IS_CONNECTED = UpstreamRef("Robot.is_connected", "VERIFIED", src(_ROBOT, 117))
ROBOT_IS_CALIBRATED = UpstreamRef("Robot.is_calibrated", "VERIFIED", src(_ROBOT, 137))
ROBOT_CALIBRATE = UpstreamRef(
    "Robot.calibrate() is interactive",
    "VERIFIED",
    src(_SO, 118),
    "the SO follower's calibrate() calls input() (also line 131); quackd never triggers it "
    "and refuses to drive an uncalibrated arm",
)
ROBOT_CONFIGURE = UpstreamRef("Robot.configure()", "VERIFIED", src(_ROBOT, 174))
ROBOT_CONTEXT = UpstreamRef(
    "Robot.__enter__/__exit__", "VERIFIED", src(_ROBOT, 61), "connect on enter, disconnect on exit"
)
ROBOT_DEL = UpstreamRef(
    "Robot.__del__ disconnects a robot still connected",
    "VERIFIED",
    src(_ROBOT, 76),
    "a destructor safety net: a robot collected while is_connected is still true is "
    "disconnected, and whatever that raises is swallowed. So an exit that never reaches "
    "quackd's close() can still end in the follower's own disconnect(), and what that does to "
    "torque is whatever the config holds by then (SO_DISCONNECT_READS_ITS_CONFIG_LATE), "
    "which is why quackd builds the follower asking to keep it (SO_DISCONNECT_TORQUE). "
    "Read again on 2026-09-26, at the same commit",
)
TYPES = UpstreamRef(
    "RobotAction = dict[str, Any]; RobotObservation = dict[str, Any]",
    "VERIFIED",
    src(_TYPES, 40),
)
ROBOT_CALIBRATION_ATTR = UpstreamRef(
    "Robot.calibration",
    "VERIFIED",
    src(_ROBOT, 54),
    "motor name -> MotorCalibration, loaded from the file in __init__ when one exists. It is "
    "where quackd reads each joint's travel, so it is populated before connect() and empty "
    "when this arm has never been calibrated on this machine",
)
ROBOT_CALIBRATION_FPATH = UpstreamRef(
    "Robot.calibration_fpath",
    "VERIFIED",
    src(_ROBOT, 53),
    "calibration_dir / '<id>.json'; quackd reports the path, so a wrong id is visible",
)
WRIST_ROLL_IS_A_FULL_TURN = UpstreamRef(
    "calibrate() records wrist_roll as a full turn",
    "VERIFIED",
    src(_SO),
    'the SO follower\'s calibrate() sets `full_turn_motor = "wrist_roll"`, prints "Move '
    "all joints except 'wrist_roll' sequentially through their entire ranges of "
    'motion", and then writes `range_mins[full_turn_motor] = 0` and '
    "`range_maxes[full_turn_motor] = 4095` rather than anything swept. quackd derives every "
    "joint's travel from those numbers, so wrist_roll comes out as -180..180 and the "
    "out-of-range refusal, which is real on the other four body joints, cannot catch "
    "anything on that one",
)
CALIBRATION_DIR = UpstreamRef(
    "HF_LEROBOT_CALIBRATION/robots/so_follower/",
    "VERIFIED",
    src(_CONSTANTS, 86),
    "the default calibration directory: $HF_LEROBOT_CALIBRATION, else $HF_LEROBOT_HOME/"
    "calibration (line 85), else $HF_HOME/lerobot/calibration (line 77), each expanded for ~, "
    "then 'robots' (line 52) and the robot class's own name (robot.py line 50), and the file in "
    "it is '<id>.json' (robot.py line 53). HF_HOME is huggingface_hub's, which this module "
    "imports (line 18): $HF_HOME, else $XDG_CACHE_HOME/huggingface, else ~/.cache/huggingface, "
    "expanded for ~ and then for environment variables (huggingface_hub 1.31.0, installed beside "
    "lerobot 0.6.1). Every step is os.getenv with a default, so a variable set to nothing still "
    "counts as set. The arm simulator walks the same chain without importing lerobot, to find "
    "the file a registered arm's twin reads. Two arms sharing an id share a file, and nothing in "
    "it names a serial number. The chain read again on 2026-09-26, at the same commit",
)
CALIBRATION_ENV = "HF_LEROBOT_CALIBRATION"
LEROBOT_HOME_ENV = "HF_LEROBOT_HOME"
HF_HOME_ENV = "HF_HOME"
XDG_CACHE_ENV = "XDG_CACHE_HOME"
"""The four variables CALIBRATION_DIR's chain reads, in the order it reads them."""
CALIBRATION_SUBDIR = "calibration"
LEROBOT_SUBDIR = "lerobot"
HF_SUBDIR = "huggingface"
ROBOTS_SUBDIR = "robots"
"""The directory names CALIBRATION_DIR's chain joins, each where its row says."""
CALIBRATION_FILE = UpstreamRef(
    "a calibration file is draccus JSON of motor name -> MotorCalibration",
    "VERIFIED",
    src(_ROBOT, 159),
    "_load_calibration() opens the file under draccus.config_type('json') and loads it as "
    "dict[str, MotorCalibration] (line 160), and _save_calibration() writes it back the same "
    "way (line 171). So the file is one JSON object with a key per motor, each holding exactly "
    "MotorCalibration's five integer fields, each decoded as CALIBRATION_INTS says. The arm "
    "simulator reads it with the json module and refuses an entry that leaves a field out or "
    "adds one, as draccus does. Read on 2026-09-26, at the same commit, and the same in lerobot "
    "0.6.1",
)
CALIBRATION_INTS = UpstreamRef(
    "draccus>=0.11.6,<0.12.0",
    "VERIFIED",
    src("pyproject.toml", 70),
    "the draccus LeRobot reads a calibration file with (CALIBRATION_FILE). draccus 0.11.6, the "
    "version installed beside lerobot 0.6.1, decodes an int field in decode_int "
    "(draccus/parsers/decoding.py line 111 at its v0.11.6 tag, github.com/dlwh/draccus): a "
    "float is refused, 100.0 included, and anything else goes through int(), so true reads as "
    "1 and a string of digits as its number. A null passes through as None before decode_int "
    "is reached (line 256). The arm simulator decodes each field the same way, so an arm's "
    "twin loads the file its arm loads, except that it refuses a null, because it builds the "
    "arm's travel from every field. Read on 2026-09-26, at the same commit",
)

# ── the SO-101 follower (the arm the adapter targets by default) ────────────────────────

MAKE_ROBOT = UpstreamRef(
    "lerobot.robots.make_robot_from_config(config)", "VERIFIED", src(_UTILS, 27)
)
ROBOT_TYPE_SO101 = UpstreamRef(
    "so101_follower",
    "VERIFIED",
    src(_SO_CFG, 56),
    "the registered config type; make_robot_from_config dispatches on it (utils.py line 41)",
)
SO_FOLLOWER = UpstreamRef(
    "lerobot.robots.so_follower.SO101Follower",
    "VERIFIED",
    src(_SO, 242),
    "an alias of SOFollower (SO100Follower too); exported by the package __init__",
)
SO_NAME = UpstreamRef(
    "SOFollower.name is so_follower",
    "VERIFIED",
    src(_SO, 44),
    "the class name, and therefore the calibration subdirectory: an SO-100 and an SO-101 "
    "share one, because at this commit they are the same class",
)
SO_FOLLOWER_NAME = "so_follower"
"""SO_NAME's name: the directory under 'robots' that holds an SO-101's calibration files."""
SO_CONFIG = UpstreamRef(
    "SO101FollowerConfig(port, disable_torque_on_disconnect=True, max_relative_target=None, "
    "cameras={}, use_degrees=True, position_p_coefficient=16, position_i_coefficient=0, "
    "position_d_coefficient=32, num_read_retries=2)",
    "VERIFIED",
    src(_SO_CFG, 25),
    "an alias of SOFollowerRobotConfig; id and calibration_dir come from RobotConfig. quackd "
    "passes every safety-shaped field explicitly rather than inheriting a default it has not "
    "read, sets max_relative_target, which upstream leaves at None, and passes "
    "disable_torque_on_disconnect as False, the opposite of upstream's default "
    "(SO_DISCONNECT_TORQUE)",
)
SO_MOTORS = UpstreamRef(
    "shoulder_pan, shoulder_lift, elbow_flex, wrist_flex, wrist_roll, gripper",
    "VERIFIED",
    src(_SO, 54),
    "six Feetech sts3215 motors, ids 1..6 (lines 54 to 59, SO_MOTOR_IDS). A calibration file "
    "carries each motor's id too, so quackd reads a real arm's ids off its bus table "
    "(BUS_MOTORS), and the arm simulator gives the generic arm it builds these",
)
SO_MOTOR_IDS: dict[str, int] = {
    "shoulder_pan": 1,
    "shoulder_lift": 2,
    "elbow_flex": 3,
    "wrist_flex": 4,
    "wrist_roll": 5,
    "gripper": 6,
}
"""Each motor's bus id in the SO follower's table, as SO_MOTORS reads it at the pin."""
SO_OBSERVATION_KEYS = UpstreamRef(
    "'<motor>.pos'", "VERIFIED", src(_SO, 184), "joint positions; the same keys are the action"
)
SO_OBSERVATION_IS_POSITION_ONLY = UpstreamRef(
    "get_observation() reads Present_Position and nothing else",
    "VERIFIED",
    src(_SO, 183),
    "one sync_read of the positions, with num_read_retries extra attempts, plus a frame per "
    "camera. No torque state, no current, no temperature and no error flag, so a joint that "
    "has tripped its own protection looks exactly like one that has arrived",
)
SO_CAMERA_KEYS = UpstreamRef(
    "camera name -> array",
    "VERIFIED",
    src(_SO, 192),
    "get_observation() adds cam.read_latest() under each configured camera's name",
)
SO_ACTION_CLAMP = UpstreamRef(
    "max_relative_target caps each step",
    "VERIFIED",
    src(_SO, 223),
    "ensure_safe_goal_position (utils.py line 93) clips a goal to present +/- the cap and "
    "logs a warning when it does; None, which is upstream's default, means no cap. Setting "
    "it costs one extra sync_read of the present position per send_action. send_action reads "
    "it off self.config on every call (lines 223 and 226), so a cap written between two sends "
    "caps the next, which is how a policy segment gets its own",
)
SO_ACTION_CLAMP_IS_FLOAT = UpstreamRef(
    "max_relative_target must be a float or a dict per motor",
    "VERIFIED",
    src(_UTILS, 98),
    "ensure_safe_goal_position tests isinstance(float), then isinstance(dict), and raises "
    "TypeError on anything else, so an int cap raises rather than capping. quackd casts",
)
ENSURE_SAFE_GOAL_POSITION = UpstreamRef(
    "ensure_safe_goal_position(goal_present_pos, max_relative_target)",
    "VERIFIED",
    src(_UTILS, 93),
    "the whole step cap, twenty lines. A float cap applies to every motor in the goal; a dict "
    "cap must have exactly the goal's keys or it raises ValueError (line 102); anything else, "
    "an int included, raises TypeError (line 105). Each goal then becomes present + (goal - "
    "present) clipped to the cap either way (lines 110 to 114), so the cap is measured from the "
    "present reading, which send_action() reads fresh off Present_Position just before "
    "(so_follower.py line 224), and a goal moved by more than 1e-4 is logged as a warning "
    "(line 116). A NaN cap caps nothing, because min and max hand back the goal when the other "
    "side is NaN. The arm simulator's follower reimplements it rather than importing it, "
    "because lerobot needs Python 3.12 and torch and the simulator runs on 3.11 without either. "
    "Read on 2026-09-26, at the same commit; lerobot 0.6.1 has the same function two lines "
    "higher, logging through the root logger",
)
SO_SEND_ACTION_RETURN = UpstreamRef(
    "send_action() returns the goal actually sent",
    "VERIFIED",
    src(_SO, 230),
    "the clipped goal in '<motor>.pos' keys, which is not the measured position. quackd "
    "keeps it rather than assuming the goal it asked for was the one written",
)
SO_DEGREES = UpstreamRef(
    "use_degrees=True -> body joints in degrees",
    "VERIFIED",
    src(_SO, 50),
    "MotorNormMode.DEGREES; False means a -100..100 range",
)
SO_GRIPPER_RANGE = UpstreamRef(
    "gripper is 0..100", "VERIFIED", src(_SO, 59), "MotorNormMode.RANGE_0_100 whatever use_degrees"
)
SO_DISCONNECT_TORQUE = UpstreamRef(
    "disconnect() disables torque by default",
    "VERIFIED",
    src(_SO, 234),
    "disable_torque_on_disconnect defaults to True (config line 31): LeRobot lets the arm go "
    "limp whenever the follower is disconnected, and that includes the disconnect it makes of "
    "a follower nobody closed (ROBOT_DEL), so under that default an exit that skipped quackd's "
    "close(), a second Ctrl-C during the rest move or a crash, could drop the arm. quackd builds "
    "the follower with it False, and close() writes it on the instance every time: True over "
    "an arm at its recorded rest pose or with none recorded, which is the limp end of every "
    "clean session, a doctor probe included, and False over one that did not reach its pose, "
    "where letting go would drop it (SO_DISCONNECT_READS_ITS_CONFIG_LATE). A connect quackd "
    "refuses once the arm is energised (not calibrated, no calibration file, no motors bus) "
    "writes True before its own disconnect, and one that fails any other way closes the port "
    "and keeps torque. Nothing runs when the process is killed",
)
SO_DISCONNECT_READS_ITS_CONFIG_LATE = UpstreamRef(
    "disconnect() reads config.disable_torque_on_disconnect when it runs",
    "VERIFIED",
    src(_SO, 234),
    "the flag is read off the config instance inside disconnect() rather than copied at "
    "construction, and SOFollowerConfig is a plain dataclass, so the value on the instance when "
    "disconnect() runs is what it does, whoever calls it. _config_kwargs() asks for False and "
    "every connect asks again, so a disconnect quackd did not make, ROBOT_DEL's included, "
    "keeps torque, and MotorsBus.disconnect(False) closes the port with every motor still "
    "holding its goal (BUS_DISCONNECT). quackd writes True just before its own disconnect "
    "only over an arm that may be let go. Read against lerobot 0.6.1, the version the first "
    "real arm ran",
)
SO_GRIPPER_TORQUE_LIMIT = UpstreamRef(
    "Max_Torque_Limit 500 on the gripper",
    "VERIFIED",
    src(_SO, 169),
    "configure() caps the gripper at 50 % torque, 50 % current (Protection_Current 250) and "
    "25 % torque once overloaded (Overload_Torque 25): the native safety authority. The line's "
    "own comment calls 500 '50% of max torque', so the register's full scale is 1000, and the "
    "arm simulator scales its model gripper's force range by that ratio "
    "(GRIPPER_MAX_TORQUE_LIMIT over MAX_TORQUE_LIMIT_FULL). Read again on 2026-09-26, at the "
    "same commit",
)
GRIPPER_MAX_TORQUE_LIMIT = 500
"""What configure() writes to the gripper's Max_Torque_Limit (SO_GRIPPER_TORQUE_LIMIT)."""
MAX_TORQUE_LIMIT_FULL = 1000
"""The register's full scale, from the comment beside that write, which calls 500 half of it."""
SO_BODY_HAS_NO_TORQUE_CAP = UpstreamRef(
    "the five body joints get no torque or current cap",
    "VERIFIED",
    src(_SO, 168),
    "the three caps above are inside a check for the gripper. Every other joint keeps "
    "whatever its firmware defaults to, so the manifest's torque_limit authority covers the "
    "gripper only and an elbow against an obstacle is the servo's own protection and nothing "
    "else",
)
SO_CONFIGURE_MOTORS = UpstreamRef(
    "configure_motors() writes Return_Delay_Time 0 and Acceleration 254",
    "VERIFIED",
    src(_FEETECH, 209),
    "plus Maximum_Acceleration 254 on protocol 0 and an sts3215 Phase fix. configure() calls "
    "it inside torque_disabled(), so connecting always drops torque briefly",
)
SO_IS_CONNECTED = UpstreamRef(
    "SOFollower.is_connected is the serial port plus the cameras",
    "VERIFIED",
    src(_SO, 88),
    "bus.is_connected and every camera's; BUS_IS_CONNECTED is what the bus half means",
)
SO_BUS = UpstreamRef(
    "SOFollower.bus is a FeetechMotorsBus",
    "VERIFIED",
    src(_SO, 51),
    "the attribute quackd reads registers through when the Robot interface has no answer",
)
NO_CLIENT_DEADMAN = UpstreamRef(
    "no deadman: nothing stops the arm when the client goes quiet",
    "VERIFIED",
    src(_SO, 205),
    "read end to end at the pin: SOFollower has no thread, timer, timeout or watchdog, and "
    "send_action writes Goal_Position and returns. A position-controlled arm holds its last "
    "goal under torque until the next write or disconnect(). quackd's stop re-sends the "
    "present position as the goal (hold) and never disables torque",
)
KINEMATICS_DEG2RAD = UpstreamRef(
    "RobotKinematics sets a URDF joint to np.deg2rad(degrees)",
    "VERIFIED",
    src(_KINEMATICS, 86),
    "forward_kinematics hands each joint in its joint_names the reading in radians with no "
    "offset and no sign, and inverse_kinematics seeds its solve the same way (line 119). It is "
    "LeRobot's own precedent for putting a calibrated reading on a model of the arm, and the "
    "one the arm simulator follows for the five arm joints, whose zero and sign are its "
    "assumptions in quackd_lerobot.sim.upstream_api. Read on 2026-09-26, at the same commit",
)

# ── the Feetech bus, below the Robot interface, where the registers are ─────────────────

BUS_DISABLE_TORQUE = UpstreamRef(
    "MotorsBus.disable_torque()",
    "VERIFIED",
    src(_BUS, 118),
    "NEVER called on quackd's own initiative. The one call is `let_go()`, and it has two "
    "doors, each opened by a person at a terminal. `let_go()` is `quackd run --by-hand`'s, and "
    "refuses anywhere but the arm's recorded rest pose, the same condition `close()` uses to "
    "decide that letting go will not drop it. `let_go(anywhere=True)` is `quackd robot "
    "release`'s and the end-of-run offer's, after each has told the person to hold the arm, "
    "and releases wherever the arm stands, with or without a rest pose recorded. No verb "
    "reaches either and no model can ask for it. On a Feetech bus it writes Torque_Enable 0 "
    "and then Lock 0 to each motor in turn (feetech.py lines 291 to 294), each write tried "
    "num_retry + 1 times; num_retry defaults to 0 and quackd passes 5, the count upstream's "
    "own disconnect() gives the same call (motors_bus.py line 559)",
)
BUS_ENABLE_TORQUE = UpstreamRef(
    "MotorsBus.enable_torque()",
    "VERIFIED",
    src(_BUS, 113),
    "called by `take_hold()`, to pick up an arm a person has just placed, with num_retry=5 "
    "as for the release. On a Feetech bus it writes Torque_Enable 1 and then Lock 1 to each "
    "motor in turn (feetech.py lines 302 to 305, the same in the installed lerobot 0.6.1): "
    "two writes a motor, not one, and connect() makes this same call with no retry at all "
    "(CONFIGURE_TORQUE_WRITES_ONCE)",
)
BUS_DISCONNECT = UpstreamRef(
    "MotorsBus.disconnect(disable_torque=True)",
    "VERIFIED",
    src(_BUS, 82),
    "the disable_torque call is inside `if disable_torque`, so False closes the port and "
    "leaves every motor holding the goal it was last written: what an arm that missed its "
    "rest pose gets instead of falling. The same call closes the port between two connect "
    "attempts (CONFIGURE_TORQUE_WRITES_ONCE), because the follower's own disconnect() would "
    "first switch torque off on every motor, five tries a write, on a bus that has just lost "
    "a packet. The concrete method is at lines 546 to 562 in lerobot 0.6.1 (clearPort and "
    "port_handler.is_using = False at 557 and 558, then the torque off at 559, all three only "
    "under the flag; closePort at 561) and is check_if_not_connected, so it raises on a port "
    "that never opened, which quackd ignores: that port is already shut. The busy flag is the "
    "servo SDK's: its txPacket returns COMM_PORT_BUSY while it is set and sets it before a "
    "packet goes out (scservo_sdk protocol_packet_handler.py lines 73 to 75), and a serial "
    "error raised between that and the reply leaves it set, which neither closePort nor the "
    "next openPort clears. MotorsBus declares it on its PortHandler protocol (line 199). So "
    "quackd clears it after its own close, as the skipped branch would have, and writes nothing "
    "to any motor doing so. Read in lerobot 0.6.1, the same lines at the pin",
)
BUS_MOTORS = UpstreamRef(
    "MotorsBus.motors: name -> Motor(id, model, norm_mode)",
    "VERIFIED",
    src(_BUS, 185),
    "the table the bus addresses every servo through (kept at line 73), which the SO follower "
    "fills at so_follower.py lines 53 to 60. quackd reads a Motor's id to name the joint a bus "
    "error is about, and looks it up there rather than assume the order SO_MOTORS lists, "
    "because the table is what gave each servo its address. A bus call that takes motors "
    "resolves them through it (_get_motors_list, line 431): None is every motor in the table's "
    "order, a name is itself, an id is the motor the table gives it, a sequence is each of those "
    "in its own order, and anything else is a TypeError. The Feetech torque calls write to "
    "those motors and no others, in that order (feetech.py lines 291 to 305). The arm "
    "simulator's bus resolves them the same way. Read on 2026-09-26, at the same commit, and "
    "the same in lerobot 0.6.1",
)
BUS_WRITE_ERROR_NAMES_THE_ID = UpstreamRef(
    "Failed to write '<register>' on id_=<N> with '<value>' after <k> tries. <result>",
    "VERIFIED",
    src(_BUS, 1096),
    "the message MotorsBus.write() raises: a ConnectionError ending in the transaction's "
    "[TxRxResult] text when no good status packet came back (line 1121), a RuntimeError ending "
    "in the servo's own status text when one reported an error (line 1123). read() names its "
    "motor the same way, on id_=<N> (line 1020). The id is the servo's bus address, and quackd "
    "names the joint through BUS_MOTORS. sync_read and sync_write say ids= and ids_values=, "
    "several servos at once, and quackd names no joint for those. Read in lerobot 0.6.1",
)
BUS_HANDSHAKE = UpstreamRef(
    "_handshake",
    "VERIFIED",
    src(_BUS, 543),
    "MotorsBus.connect() opens the port and then runs _handshake() (line 535), which on a "
    "Feetech bus is _assert_motors_exist() and then _assert_same_firmware() (feetech.py lines "
    "155 to 157): a ping per motor, then two firmware reads per motor, and not one write. "
    "configure(), where every write of a connect is, runs only once bus.connect() has returned "
    "(so_follower.py lines 98 and 108). Between the two, line 99 is `if not self.is_calibrated "
    "and calibrate:`, whose left side is evaluated first, so the calibration check "
    "(BUS_IS_CALIBRATED) runs on every connect, calibrate=False included: it reads every motor "
    "and writes to none. So a connect that fails in the handshake or in that check has left every "
    "motor's torque as it found it. A failure raised in it has this frame in its traceback; "
    "_connect re-raises a serial error or an OSError from in there (a failed read is a "
    "ConnectionError, which is one) as its own port error, from the original (lines 530 to "
    "540), so for those the frame is in the traceback of the __cause__. Read in lerobot 0.6.1, "
    "the same lines at the pin",
)
HANDSHAKE_NAMES_THE_ID = UpstreamRef(
    "Missing motor IDs: / Motors with incorrect model numbers: - <N> (...)",
    "VERIFIED",
    src(_BUS, 465),
    "the RuntimeError _assert_motors_exist() raises (lines 465 to 502), headed 'motor check "
    "failed on port': a line '  - <N> (expected model: <M>)' per motor that did not answer its "
    "ping, and a line '  - <N> (<joint>): expected <M>, found <K>' per motor that answered as "
    "another model, each list under its own heading. ping() returns None for a servo that "
    "answers with an error bit set as it does for one that does not answer (lines 967 to 976), "
    "so an overloaded servo is listed as missing too. The id is the bus address, and quackd names "
    "the joint through BUS_MOTORS as for a write. Read in lerobot 0.6.1, the same lines at the "
    "pin",
)
BUS_SYNC_READ_ERROR = UpstreamRef(
    "Failed to sync read '<register>' on ids=[<N>, ...] after <k> tries. <result>",
    "VERIFIED",
    src(_BUS, 1160),
    "the ConnectionError MotorsBus.sync_read() raises when no good reply came back (line 1194), "
    "listing every motor it read by bus id, in the table's order. sync_write() says \"Failed to "
    "sync write '<register>' with ids_values={<N>: <tick>, ...} after <k> tries. <result>\" "
    "(line 1257) and raises it (line 1282) only when the packet could not be sent, because it "
    "waits for no reply (line 1231). <result> is the servo SDK's getTxRxResult text: "
    "'[TxRxResult] There is no status packet!' for a reply that never came and '[TxRxResult] "
    "Failed transmit instruction packet!' for a packet that never went (scservo_sdk "
    "protocol_packet_handler.py lines 43 and 35, in the feetech-servo-sdk 1.0.0 installed beside "
    "lerobot 0.6.1). Neither names one motor, so quackd names no joint for either "
    "(BUS_WRITE_ERROR_NAMES_THE_ID). The arm simulator raises these words for the read and goal "
    "write faults it injects. Read on 2026-09-26, at the same commit, and at the same lines in "
    "lerobot 0.6.1",
)
STS3215_MODEL_NUMBER = UpstreamRef(
    "sts3215 model number 777",
    "VERIFIED",
    src(_TABLES, 243),
    "what a servo of the SO-101's model answers a ping with, which the handshake expects of every "
    "motor in the bus table and prints beside each id it lists (HANDSHAKE_NAMES_THE_ID). The arm "
    "simulator's handshake fault prints it where the arm's would. Read on 2026-09-26, at the same "
    "commit, and the same in lerobot 0.6.1",
)
STS3215_MODEL = 777
"""STS3215_MODEL_NUMBER's number."""
NOT_CONNECTED = UpstreamRef(
    "check_if_not_connected refuses a call on a port that is not open",
    "VERIFIED",
    src(_DECORATORS, 22),
    "a DeviceNotConnectedError, which is a ConnectionError (utils/errors.py line 16), saying "
    "'<class> is not connected. Run `.connect()` first.' (line 27). SOFollower's "
    "get_observation(), send_action() and disconnect() carry it (so_follower.py lines 179, 204 "
    "and 232), and so do MotorsBus's disconnect(), read(), write(), sync_read() and sync_write() "
    "(motors_bus.py lines 546, 994, 1066, 1127 and 1220), so nothing reaches a motor through a "
    "port that is shut. Its twin check_if_already_connected (line 34) says '<class> is already "
    "connected.' (SO_CONNECT_REFUSES_WHILE_OPEN). The arm simulator's follower and bus refuse in "
    "the same words under upstream's class names, SOFollower and FeetechMotorsBus, so a "
    "rehearsal fails where the arm would and says what the arm would. Read on 2026-09-26, at the "
    "same commit, and the same in lerobot 0.6.1",
)
SO_CONNECT_REFUSES_WHILE_OPEN = UpstreamRef(
    "SOFollower.connect() refuses while the port is open",
    "VERIFIED",
    src(_SO, 91),
    "it is check_if_already_connected (utils/decorators.py lines 34 to 41), which raises "
    "DeviceAlreadyConnectedError while is_connected is True, and is_connected is the bus's "
    "port flag and every camera's (SO_IS_CONNECTED; quackd's follower has no camera). connect() "
    "opens the port first (line 98) and configures last (line 108), and nothing closes the "
    "port when configure() raises, so after one failed connect every later connect() is "
    "refused until the port is shut. MotorsBus.connect() carries the same decorator "
    "(motors_bus.py line 513). quackd closes the port with MotorsBus.disconnect(False) between "
    "attempts (BUS_DISCONNECT). Read in lerobot 0.6.1, the same lines at the pin",
)
CONFIGURE_TORQUE_WRITES_ONCE = UpstreamRef(
    "configure() switches torque off and on again with no retry",
    "VERIFIED",
    src(_BUS, 676),
    "SOFollower.configure() (so_follower.py line 159) runs inside torque_disabled(), which "
    "calls disable_torque() on the way in and enable_torque() in its finally (lines 687 and "
    "691), both with num_retry left at 0. Each writes Torque_Enable and then Lock to one motor "
    "after another (feetech.py lines 291 to 305), and each write is one transaction that raises "
    "when its status packet does not come back (lines 1111 to 1121). So one lost packet fails "
    "the whole connect, with the port left open and the motors before that write in one torque "
    "state and the rest in the other. On 2026-09-23 an SO-101 failed three connects this way, "
    "each on a Lock write to a different motor, and the next connect went through each time. "
    "quackd tries again (CONNECT_ATTEMPTS in real.py) instead of giving up on the first packet, "
    "and says which joint each failure named. Read in lerobot 0.6.1, the same lines at the pin",
)
BUS_IS_CONNECTED = UpstreamRef(
    "MotorsBus.is_connected is port_handler.is_open",
    "VERIFIED",
    src(_BUS, 511),
    "a serial port's open flag, not a reply from a motor. Unplug the arm and it stays True "
    "until a read fails, which is why quackd's heartbeat reads the arm rather than the flag",
)
BUS_IS_CALIBRATED = UpstreamRef(
    "FeetechMotorsBus.is_calibrated reads the motors back",
    "VERIFIED",
    src(_FEETECH, 228),
    "it reads Min_Position_Limit, Max_Position_Limit and Homing_Offset off every motor and "
    "compares them with the cached file. A missing file, a stale file, and the file of a "
    "different arm all come back False, which is the check quackd refuses on. The reads are "
    "read_calibration() (line 247), each a read() with num_retry left at 0 (motors_bus.py lines "
    "995 to 1001), and a lost status packet in one raises \"Failed to read '<register>' on id_=<N> "
    'after 1 tries" (line 1020). SOFollower.connect() makes this check itself, between the '
    "handshake and configure() (so_follower.py line 99, BUS_HANDSHAKE), so a connect can fail "
    "in it having written nothing, and quackd places such a failure by the is_calibrated and "
    "read_calibration frames in its traceback. Read in lerobot 0.6.1",
)
BUS_WRITE_CALIBRATION = UpstreamRef(
    "write_calibration() is reached only through calibrate()",
    "VERIFIED",
    src(_FEETECH, 268),
    "it writes the limits and the homing offset into the motors; connect(calibrate=False) "
    "never calls it, so quackd cannot move an arm's zero even by accident. The limits are "
    "each motor's calibrated range_min and range_max, written into its Min_Position_Limit and "
    "Max_Position_Limit registers (POSITION_LIMITS_CLAMP_GOALS says what the servo does with "
    "them)",
)
POSITION_LIMITS_CLAMP_GOALS = UpstreamRef(
    "write_calibration() writes Min_Position_Limit and Max_Position_Limit, and the servo "
    "clamps Goal_Position to them",
    "VERIFIED",
    src(_FEETECH, 268),
    "lines 268 to 276, the same in the installed lerobot 0.6.1: each motor's range_min and "
    "range_max go into its own EEPROM as Min_Position_Limit and Max_Position_Limit. The "
    "STS3215 firmware then clamps every Goal_Position write to those two registers, which no "
    "LeRobot source says (DEGREES_NO_CLAMP bounds nothing) and an SO-101 showed on 2026-09-23: "
    "a joint driven down from above stopped one encoder tick inside its floor, and every goal "
    "written below the floor moved a joint folded past it up to it. A reading is not clamped: "
    "with torque off an arm folds wherever a hand or its weight puts it, past either limit. "
    "So quackd drives a rest pose clipped into the travel, judges a joint folded past its "
    "limit as at rest, and never writes a goal for a joint that reads past its travel, "
    "because the one goal the servo would take there is the limit and it hauls the joint to it",
)
BUS_SYNC_READ = UpstreamRef(
    "MotorsBus.sync_read(data_name, motors=None, normalize=True, num_retry=0)",
    "VERIFIED",
    src(_BUS, 1128),
    "one framed transaction for every motor named; quackd uses it for the registers "
    "get_observation() does not read",
)
BUS_NORMALIZED_DATA = UpstreamRef(
    "NORMALIZED_DATA is Goal_Position and Present_Position",
    "VERIFIED",
    src(_FEETECH, 47),
    "the only two names sync_read normalises (motors_bus.py line 1167), so every other "
    "register comes back raw whatever normalize says. quackd passes normalize=False anyway, "
    "because a raw register is what it means to read",
)
MOTOR_CALIBRATION = UpstreamRef(
    "MotorCalibration(id, drive_mode, homing_offset, range_min, range_max)",
    "VERIFIED",
    src(_BUS, 176),
    "range_min and range_max are raw encoder ticks recorded by calibration, not degrees",
)
CALIBRATION_EQUAL_RANGE = UpstreamRef(
    "Invalid calibration for motor '<motor>': min and max are equal.",
    "VERIFIED",
    src(_BUS, 865),
    "_normalize() raises this ValueError for a motor whose range_min equals its range_max, "
    "and _unnormalize() raises it too (line 894), so LeRobot loads such a file and then "
    "refuses the first reading or goal that goes through the motor. The arm simulator refuses "
    "the file as it reads it, which is the same arm refused sooner. Read on 2026-09-26, at the "
    "same commit, and at the same lines in lerobot 0.6.1",
)
DEGREES_FORMULA = UpstreamRef(
    "degrees = (raw - mid) * 360 / 4095",
    "VERIFIED",
    src(_BUS, 874),
    "mid is (range_min + range_max) / 2 and 4095 is the resolution less one, so a joint's "
    "travel in degrees is (range_max - range_min) * 360 / 4095, centred on zero. That is how "
    "quackd turns a calibration file into the range it will accept a goal inside",
)
DEGREES_NO_CLAMP = UpstreamRef(
    "a degrees goal is not clamped to the calibrated range",
    "VERIFIED",
    src(_BUS, 904),
    "_unnormalize bounds the RANGE_0_100 and RANGE_M100_100 modes and does not bound "
    "DEGREES: the tick it computes is written to Goal_Position as-is. So the gripper is "
    "clamped by LeRobot and the five body joints are not. The firmware clamps them instead, to "
    "the Min_Position_Limit and Max_Position_Limit calibration wrote into it "
    "(POSITION_LIMITS_CLAMP_GOALS, seen on an arm on 2026-09-23), so a goal past the travel is "
    "one the arm silently stops short of. quackd refuses a pilot's goal there rather than let "
    "it be quietly rewritten",
)
STS3215_RESOLUTION = UpstreamRef(
    "sts3215 resolution 4096",
    "VERIFIED",
    src(_TABLES, 190),
    "12 bits over a full turn, so one tick is about 0.088 degrees",
)
STS3215_REGISTERS = UpstreamRef(
    "Torque_Enable (40, 1) and Present_Temperature (63, 1)",
    "VERIFIED",
    src(_TABLES, 76),
    "address and length in the STS/SMS control table, temperature at line 87. Present_Load, "
    "Present_Current, Present_Voltage, Status and Max_Temperature_Limit are in the same "
    "table and quackd reads none of them yet. Nothing upstream reads these two either: the "
    "arm's own protection can drop torque and get_observation() will not mention it",
)

# ── cameras ─────────────────────────────────────────────────────────────────────────────

CAMERA_ASYNC_READ = UpstreamRef(
    "Camera.async_read(timeout_ms)", "VERIFIED", src(_CAMERA, 122), "the most recent new frame"
)
CAMERA_READ = UpstreamRef("Camera.read()", "VERIFIED", src(_CAMERA, 111))
CAMERA_RGB_CONVERSION = UpstreamRef(
    "OpenCVCamera converts BGR to RGB when color_mode is RGB",
    "VERIFIED",
    src(_OPENCV, 446),
    "so a camera array's channel order is a config choice, not a constant",
)
CAMERA_COLOR_MODE_DEFAULT = UpstreamRef(
    "OpenCVCameraConfig.color_mode defaults to ColorMode.RGB",
    "VERIFIED",
    src(_OPENCV_CFG, 62),
    "which settles the channel order quackd used to assume: a stock OpenCV camera hands "
    "over RGB, and quackd passes it explicitly anyway",
)
OPENCV_CAMERA = UpstreamRef(
    "lerobot.cameras.opencv.OpenCVCamera(config)",
    "VERIFIED",
    src(_OPENCV, 52),
    "the class quackd builds and owns itself, beside the follower rather than inside it",
)
OPENCV_CAMERA_CONFIG = UpstreamRef(
    "OpenCVCameraConfig(index_or_path, fps=None, width=None, height=None, "
    "color_mode=ColorMode.RGB, rotation=Cv2Rotation.NO_ROTATION, warmup_s=1, fourcc=None, "
    "backend=Cv2Backends.ANY)",
    "VERIFIED",
    src(_OPENCV_CFG, 25),
    "an index or a device path, and a fourcc that must be four characters",
)
CAMERA_EXPORTS = UpstreamRef(
    "lerobot.cameras exports Camera, CameraConfig, ColorMode, Cv2Backends, Cv2Rotation",
    "VERIFIED",
    src(_CAMERAS_INIT, 16),
    "OpenCVCameraConfig is deliberately NOT among them (a note at line 19 says so): it "
    "comes from lerobot.cameras.opencv, which is why quackd imports from both",
)
CV2_BACKENDS = UpstreamRef(
    "Cv2Backends: ANY, V4L2, DSHOW, PVAPI, ANDROID, AVFOUNDATION, MSMF",
    "VERIFIED",
    src(_CAM_CFG, 45),
    "the backend is a config field, so the Windows fix people circulate as a source patch "
    "is a query key here: ?backend=msmf. ANY is the default and lets OpenCV choose. All "
    "seven are upstream's; --camera-url accepts the five that name a platform quackd's "
    "owners are on (any, v4l2, dshow, avfoundation, msmf), because an unreachable backend "
    "is a refusal at connect and PVAPI and ANDROID would only ever be one",
)
CAMERA_CONNECT = UpstreamRef(
    "Camera.connect(warmup=True)",
    "VERIFIED",
    src(_CAMERA, 100),
    "opens the device and reads frames for warmup_s before returning, so a camera that "
    "opens and never delivers fails here rather than at the first observe",
)
OPENCV_OPEN_FAILS = UpstreamRef(
    "connect() raises ConnectionError on an index that will not open",
    "VERIFIED",
    src(_OPENCV, 171),
    "its own words name `lerobot-find-cameras opencv`, so quackd passes them through",
)
OPENCV_MODE_IS_A_DEMAND = UpstreamRef(
    "a requested fps or size that the camera refuses raises RuntimeError",
    "VERIFIED",
    src(_OPENCV, 260),
    "width and height at line 297; fourcc only warns (line 262). So pinning a mode is a "
    "refusal on a camera that cannot do it, which is why quackd asks for none by default",
)
OPENCV_MODE_DEFAULTS_TO_THE_CAMERA = UpstreamRef(
    "an unset fps, width or height keeps the camera's own mode",
    "VERIFIED",
    src(_OPENCV, 228),
    "the guards read the device's defaults back instead of setting anything (fps at line "
    "237), which is what quackd relies on when it is pointed at an unknown webcam",
)
CAMERA_READ_LATEST = UpstreamRef(
    "Camera.read_latest(max_age_ms=500)",
    "VERIFIED",
    src(_OPENCV, 582),
    "the newest buffered frame, non-blocking; raises TimeoutError when it is older than "
    "max_age_ms (opencv line 610) and RuntimeError before the first frame or if the read "
    "thread has died. quackd catches all of it and reports a camera error instead. The "
    "anchor is the OpenCV override on purpose: the base method (camera.py line 153) is not "
    "abstract, it emits a FutureWarning and delegates to async_read(), which blocks and "
    "takes a timeout_ms instead, so these semantics are the subclass's",
)
CAMERA_DISCONNECT = UpstreamRef("Camera.disconnect()", "VERIFIED", src(_CAMERA, 182))
SO_CAMERAS_ARE_THE_FOLLOWERS = UpstreamRef(
    "a follower's cameras are part of its connected state",
    "VERIFIED",
    src(_SO, 88),
    "is_connected is the bus AND every camera, and send_action and disconnect() are "
    "decorated on it, so a webcam that drops would make every move and every hold raise. "
    "That is why quackd builds its camera beside the follower and passes cameras={}",
)
FIND_PORT = UpstreamRef(
    "lerobot-find-port",
    "VERIFIED",
    src("pyproject.toml"),
    "lerobot.scripts.lerobot_find_port:main, a [project.scripts] entry beside "
    "lerobot-calibrate and lerobot-setup-motors. It lists the ports, asks you to unplug the "
    "arm, and names the one that disappeared, which is the only way to be sure which port "
    "is the arm rather than something else on the bus",
)
FIND_CAMERAS = UpstreamRef(
    "lerobot-find-cameras opencv",
    "VERIFIED",
    src("pyproject.toml", 352),
    "lerobot.scripts.lerobot_find_cameras:main; prints an Id per camera and saves one "
    "outputs/captured_images/opencv_<id>.png (line 289), which is how an owner learns "
    "which index is which. Off Linux it scans indices 0 to 59",
)

# ── UNVERIFIED: our assumptions, and what quackd does about each ────────────────────────

TORQUE_ENABLE_HOLDS_PRESENT = UpstreamRef(
    "TORQUE_ENABLE_HOLDS_PRESENT",
    "UNVERIFIED",
    src(_BUS, 113),
    "what a servo does with its goal when torque is switched back on. `enable_torque()` "
    "writes Torque_Enable and then Lock on each motor (feetech.py lines 302 to 305 at 0.6.1), "
    "neither of them a goal, so whether the motor then holds "
    "where it is or drives to the goal it was last written is the firmware's business and is "
    "documented nowhere quackd can read. It matters because the goal last written before a "
    "hand-off is the rest pose the arm has since been lifted out of by hand, so a snap back "
    "to it would happen with somebody's fingers in the way. quackd writes the present "
    "position as the goal BEFORE enabling torque, writes it again after, and reads the arm "
    "back to check it stayed: the assumption is never relied on in either direction. A joint "
    "placed past its calibrated travel is where that cannot work: a goal written there is "
    "clamped to the limit (POSITION_LIMITS_CLAMP_GOALS), and no goal leaves the servo the "
    "last one it had, the rest move's, which only this row could say it ignores. So "
    "take_hold() leaves torque off and refuses while any body joint reads outside its travel",
)
GRIPPER_OPEN_VALUE = UpstreamRef(
    "GRIPPER_OPEN_VALUE",
    "UNVERIFIED",
    src(_SO, 59),
    "which end of the gripper's 0..100 range is open. 0 is the range_min tick of that "
    "motor's calibration and 100 the range_max, so which one is the open jaw is how the arm "
    "was assembled and calibrated. quackd assumes 100 is open, and the checklist asks for it "
    "to be confirmed by hand before anything is believed about holding",
)
HOLDING_INFERRED = UpstreamRef(
    "HOLDING_INFERRED",
    "UNVERIFIED",
    src(_SO, 183),
    "nothing reports grip force, so holding is inferred: the gripper was told to close, its "
    "reading has settled, and it settled short of shut. The band is quackd's own guess, an "
    "empty hand that binds reads as holding, and a thin enough object may not",
)
TEMPERATURE_C = UpstreamRef(
    "TEMPERATURE_C",
    "UNVERIFIED",
    src(_TABLES, 87),
    "Present_Temperature is one byte, and Feetech's documentation calls it degrees Celsius, "
    "which nothing in LeRobot reads or converts. quackd reports it raw and refuses to move a "
    "joint at or above 60, which is the datasheet's operating maximum and below the servo's "
    "own 70 cut-off, so the number and the threshold are both ours to be wrong about",
)
CAMERA_INDEX_MOVES = UpstreamRef(
    "CAMERA_INDEX_MOVES",
    "UNVERIFIED",
    src(_OPENCV, 308),
    "an OpenCV index is a scan position, not an identity: it can change when a camera is "
    "replugged or the machine reboots, and a laptop's own webcam usually holds 0. quackd "
    "records the index it opened and the frame size it got, and cannot tell you it is the "
    "camera you meant. `lerobot-find-cameras opencv` saves a frame per index for that",
)
WINDOWS_CAMERA_BACKEND = UpstreamRef(
    "WINDOWS_CAMERA_BACKEND",
    "UNVERIFIED",
    src(_CAM_CFG, 45),
    "which OpenCV backend a given Windows machine needs for a given webcam is not knowable "
    "in advance: the common report is a camera that lists and then will not open under the "
    "default. quackd leaves upstream's ANY alone and gives the owner ?backend=msmf rather "
    "than guessing per platform",
)
JOINT_RANGES = UpstreamRef(
    "JOINT_RANGES",
    "UNVERIFIED",
    src(_SO, 50),
    "the reachable range of each joint is whatever calibration recorded, and no vendor "
    "publishes what it ought to be. quackd computes each joint's travel from the calibration "
    "file and refuses a goal outside it rather than writing a tick LeRobot will not clamp "
    "(DEGREES_NO_CLAMP) and the servo will (POSITION_LIMITS_CLAMP_GOALS). The travel is not "
    "the mechanical limit on every arm: a calibration that never saw a joint folded all the "
    "way leaves the fold past it, which is why a rest pose is clipped into the travel. "
    "Whether it is the mechanical limit on any given arm is unverified",
)
SERIAL_PORT = UpstreamRef(
    "SERIAL_PORT",
    "UNVERIFIED",
    src(_SO_CFG, 29),
    "the arm's serial port (/dev/ttyACM0, COM5) comes from --address. quackd checks its "
    "shape and nothing more: which port is the arm, and whether a CH340 or CP210x driver is "
    "installed, is between the owner and their machine",
)
THREAD_SAFETY = UpstreamRef(
    "THREAD_SAFETY",
    "UNVERIFIED",
    src(_ROBOT),
    "Robot is synchronous and not documented as thread-safe, over a half-duplex serial bus "
    "where two talkers is a corrupt packet. quackd serialises every call under one lock in a "
    "worker thread with a deadline, and when a call blows its deadline it refuses every "
    "later call rather than starting a second thread on the same bus",
)


def all_refs() -> list[UpstreamRef]:
    return [v for v in globals().values() if isinstance(v, UpstreamRef)]


def refs_by_status(status: str) -> list[UpstreamRef]:
    return [r for r in all_refs() if r.status == status]
