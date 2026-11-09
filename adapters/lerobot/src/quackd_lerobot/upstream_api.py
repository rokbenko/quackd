"""The only file in quackd allowed to spell a LeRobot name (ADR-0022).

Every constant is tagged VERIFIED (read from upstream source, link given) or UNVERIFIED
(an assumption of ours, with what quackd does about it). `docs/adapters/lerobot/README.md` is the
human-readable version; `tests/test_upstream_api.py` proves UNVERIFIED names are only
reachable from the experimental `real` backend.

Source of truth: https://github.com/huggingface/lerobot at the commit its v0.7.0 tag names,
410c095eacc0011d998a73383eb94abed2715af0, read on 2026-11-08. 0.7.0 is the first release with
LeRobot's #4672, which replaced the servo SDK the arm's bus spoke through, feetech-servo-sdk, with
rustypot, under one `SerialMotorsBus` for every Feetech and Dynamixel servo. So the rows about the
bus below its Python are rustypot's, at the commit its v1.13.1 tag names, the only release so far
in the `>=1.13.1,<2.0.0` LeRobot 0.7.0 allows, and what two rows say of the port's exclusive open
and of the words for a reply that never came is serialport-rs's, at the v4.9.0 rustypot's
Cargo.lock pins. These rows were first read on 2026-09-02 and 2026-09-13 at
fbb811fca92504439792b97d216f0d00c2268382 (main, 2026-09-01, which called itself 0.6.2), and every
one was read again at the tag: each row about something 0.7.0 removed says what replaced it.
LeRobot is never imported outside the `real` backend.

First run against an arm on 2026-09-15, on lerobot 0.6.1 (Windows 11, Python 3.12.12):
connect, `get_observation`, `send_action`, the two register reads and `disconnect` behaved
as the rows read at the old pin said. No arm has run on 0.7.0, whose bus is new from the port it
opens to the words a failure raises, so what only an arm can settle is in CHANGELOG.md's Known
limitations and PLAN.md. The rows carry the status they were read with, because one afternoon on
one arm confirmed what was exercised and says nothing about the rest.

A policy's names are not here. They live in `policy/upstream_api.py`, read at the same tag,
because a checkpoint is loaded in the policy server and never beside the arm's bus.
"""

from __future__ import annotations

from quackd.upstream import UpstreamRef

REPO = "https://github.com/huggingface/lerobot"
PIN = "410c095eacc0011d998a73383eb94abed2715af0"  # the v0.7.0 tag
VERSION = "0.7.0"
"""The release the pin is, which is how the nightly drift job knows to compare these rows with
LeRobot's newest release tag and not with its main branch (`scripts/upstream_drift.py`)."""
READ_ON = "2026-11-08"
PYPI_VERSION_READ = "0.7.0"

RUSTYPOT_REPO = "https://github.com/pollen-robotics/rustypot"
RUSTYPOT_PIN = "7aa85e13143594ea4fd4ed9f29e3e29d39b5de85"  # the v1.13.1 tag
RUSTYPOT_VERSION = "1.13.1"
"""The one rustypot LeRobot 0.7.0's `rustypot>=1.13.1,<2.0.0` allowed on the day (FEETECH_EXTRA)."""
RELEASES = {"pollen-robotics/rustypot": "v1.13.1"}
"""The other repositories these rows cite as a source, each with the release it was read at, so
the nightly compares each with its own newest release and not with its default branch."""
SERIALPORT_REPO = "https://github.com/serialport/serialport-rs"
SERIALPORT_PIN = "2a62b225b2dd043fa982f91d0fb54c7e26fb221e"  # the v4.9.0 tag
"""serialport-rs at the version rustypot 1.13.1's Cargo.lock pins, which opens the port. Cited in
notes only."""


def src(path: str, line: int | None = None) -> str:
    return f"{REPO}/blob/{PIN}/{path}" + (f"#L{line}" if line else "")


def rp(path: str, line: int | None = None) -> str:
    """A rustypot source at its pin."""
    return f"{RUSTYPOT_REPO}/blob/{RUSTYPOT_PIN}/{path}" + (f"#L{line}" if line else "")


def serialport(path: str, line: int | None = None) -> str:
    """A serialport-rs source at its pin, for a note."""
    return f"{SERIALPORT_REPO}/blob/{SERIALPORT_PIN}/{path}" + (f"#L{line}" if line else "")


_ROBOT = "src/lerobot/robots/robot.py"
_UTILS = "src/lerobot/robots/utils.py"
_SO = "src/lerobot/robots/so_follower/so_follower.py"
_SO_CFG = "src/lerobot/robots/so_follower/config_so_follower.py"
_KOCH = "src/lerobot/robots/koch_follower/koch_follower.py"
_KOCH_CFG = "src/lerobot/robots/koch_follower/config_koch_follower.py"
_TYPES = "src/lerobot/lerobot_types.py"
_BUS = "src/lerobot/motors/motors_bus.py"
_CONSTANTS = "src/lerobot/utils/constants.py"
_CAMERA = "src/lerobot/cameras/camera.py"
_OPENCV = "src/lerobot/cameras/opencv/camera_opencv.py"
_OPENCV_CFG = "src/lerobot/cameras/opencv/configuration_opencv.py"
_CAM_CFG = "src/lerobot/cameras/configs.py"
_CAMERAS_INIT = "src/lerobot/cameras/__init__.py"
_KINEMATICS = "src/lerobot/model/kinematics.py"
_DECORATORS = "src/lerobot/utils/decorators.py"
_RP_BUS = "src/bus.rs"
_RP_PROTOCOL = "src/dynamixel_protocol/mod.rs"
_RP_DEFINITION = "src/servo/definition.rs"
_RP_STS3215 = "src/servo/feetech/sts3215.rs"
_RP_XL430 = "src/servo/dynamixel/xl430.rs"
_RP_SERVOS = "src/servo/mod.rs"

# ── package ─────────────────────────────────────────────────────────────────────────────

PACKAGE = UpstreamRef("lerobot", "VERIFIED", src("pyproject.toml", 27), "PyPI and import name")
PYTHON = UpstreamRef(
    ">=3.12",
    "VERIFIED",
    src("pyproject.toml", 32),
    "requires-python; quackd's floor is 3.11, so the extra carries a python_version marker. The "
    "classifiers name 3.12 and 3.13 (lines 53 and 54)",
)
VERSION_AT_PIN = UpstreamRef(
    "0.7.0", "VERIFIED", src("pyproject.toml", 28), "the release the pin is, and PyPI's"
)
FEETECH_EXTRA = UpstreamRef(
    "lerobot[feetech]",
    "VERIFIED",
    src("pyproject.toml", 188),
    '`feetech = ["lerobot[serial-motors]"]`, which is rustypot and pyserial (line 187, '
    "`rustypot>=1.13.1,<2.0.0` at line 176), neither of them a base dependency of lerobot, and "
    "the comment above it says pyserial is for `lerobot-find-port` alone (lines 185 and 186). "
    "rustypot drives the motors, so a plain lerobot imports cleanly and then builds no follower: "
    "SerialMotorsBus asks for rustypot as it is built, and refuses with an ImportError naming "
    "`lerobot[serial-motors]` (`_find_model`, motors_bus.py lines 241 to 246). quackd[lerobot] "
    "asks for lerobot[feetech] and doctor has a row for rustypot. No feetech-servo-sdk, no "
    "scservo_sdk and no deepdiff are in it any more",
)

# ── the Robot interface ─────────────────────────────────────────────────────────────────

ROBOT_BASE = UpstreamRef(
    "lerobot.robots.Robot", "VERIFIED", src(_ROBOT, 33), "the abstract base every robot implements"
)
ROBOT_CONNECT = UpstreamRef(
    "Robot.connect(calibrate=True)",
    "VERIFIED",
    src(_ROBOT, 137),
    "quackd passes calibrate=False: calibration is interactive (see ROBOT_CALIBRATE). The SO "
    "follower's connect() runs bus.connect() and configure() and writes no calibration into "
    "the motors at all (so_follower.py lines 111 to 128), so the file on disk and the arm must "
    "already agree",
)
ROBOT_DISCONNECT = UpstreamRef("Robot.disconnect()", "VERIFIED", src(_ROBOT, 238))
ROBOT_GET_OBSERVATION = UpstreamRef(
    "Robot.get_observation() -> dict",
    "VERIFIED",
    src(_ROBOT, 211),
    "a flat dict: '<motor>.pos' floats plus one array per camera, keyed by camera name",
)
ROBOT_SEND_ACTION = UpstreamRef(
    "Robot.send_action(action: dict) -> dict",
    "VERIFIED",
    src(_ROBOT, 223),
    "'<motor>.pos' -> goal; returns what was actually sent, possibly clipped",
)
ROBOT_OBSERVATION_FEATURES = UpstreamRef(
    "Robot.observation_features",
    "VERIFIED",
    src(_ROBOT, 102),
    "key -> float, or a (h, w, c) shape tuple for a camera; usable before connect()",
)
ROBOT_ACTION_FEATURES = UpstreamRef("Robot.action_features", "VERIFIED", src(_ROBOT, 116))
ROBOT_IS_CONNECTED = UpstreamRef("Robot.is_connected", "VERIFIED", src(_ROBOT, 129))
ROBOT_IS_CALIBRATED = UpstreamRef("Robot.is_calibrated", "VERIFIED", src(_ROBOT, 149))
ROBOT_CALIBRATE = UpstreamRef(
    "Robot.calibrate() is interactive",
    "VERIFIED",
    src(_SO, 134),
    "the SO follower's calibrate() first offers the file it has through "
    "`Robot._keep_calibration_file`, which calls input() (robot.py line 190) and, when the file "
    "is kept, switches torque off on every motor and writes it into them (lines 195 and 196), and "
    "otherwise records a new one through `bus.record_calibration` (so_follower.py line 140), "
    "which calls input() too (motors_bus.py line 986). quackd never triggers it and refuses to "
    "drive an uncalibrated arm",
)
ROBOT_CONFIGURE = UpstreamRef("Robot.configure()", "VERIFIED", src(_ROBOT, 203))
ROBOT_CONTEXT = UpstreamRef(
    "Robot.__enter__/__exit__", "VERIFIED", src(_ROBOT, 73), "connect on enter, disconnect on exit"
)
ROBOT_DEL = UpstreamRef(
    "Robot.__del__ disconnects a robot still connected",
    "VERIFIED",
    src(_ROBOT, 88),
    "a destructor safety net: a robot collected while is_connected is still true is "
    "disconnected, and whatever that raises is swallowed (lines 93 to 97). So an exit that never "
    "reaches quackd's close() can still end in the follower's own disconnect(), and what that "
    "does to torque is whatever the config holds by then (SO_DISCONNECT_READS_ITS_CONFIG_LATE), "
    "which is why quackd builds the follower asking to keep it (SO_DISCONNECT_TORQUE)",
)
TYPES = UpstreamRef(
    "RobotAction = dict[str, Any]; RobotObservation = dict[str, Any]",
    "VERIFIED",
    src(_TYPES, 41),
    "RobotObservation at line 43",
)
ROBOT_CALIBRATION_ATTR = UpstreamRef(
    "Robot.calibration",
    "VERIFIED",
    src(_ROBOT, 66),
    "motor name -> MotorCalibration, loaded from the file in __init__ when one exists (lines 67 "
    "and 68). It is where quackd reads each joint's travel, so it is populated before connect() "
    "and empty when this arm has never been calibrated on this machine",
)
ROBOT_CALIBRATION_FPATH = UpstreamRef(
    "Robot.calibration_fpath",
    "VERIFIED",
    src(_ROBOT, 65),
    "calibration_dir / '<id>.json'; quackd reports the path, so a wrong id is visible",
)
WRIST_ROLL_IS_A_FULL_TURN = UpstreamRef(
    "calibrate() records wrist_roll as a full turn",
    "VERIFIED",
    src(_SO, 140),
    'the SO follower\'s calibrate() records with `full_turn_motors=["wrist_roll"]`, and '
    '`record_calibration` leaves a full-turn motor out of the sweep it prints, "Move '
    '{recorded} sequentially through their entire ranges of motion", and then writes '
    "`range_mins[motor] = 0` and `range_maxes[motor]` the resolution less one, 4095 on an "
    "sts3215, rather than anything swept (motors_bus.py lines 989 to 997). quackd derives every "
    "joint's travel from those numbers, so wrist_roll comes out as -180..180 and the "
    "out-of-range refusal, which is real on the other four body joints, cannot catch anything on "
    "that one",
)
CALIBRATION_DIR = UpstreamRef(
    "HF_LEROBOT_CALIBRATION/robots/so_follower/",
    "VERIFIED",
    src(_CONSTANTS, 101),
    "the default calibration directory: $HF_LEROBOT_CALIBRATION, else $HF_LEROBOT_HOME/"
    "calibration (line 100), else $HF_HOME/lerobot/calibration (lines 92 and 93), each expanded "
    "for ~, then 'robots' (line 63) and the robot class's own name (robot.py line 63), and the "
    "file in it is '<id>.json' (robot.py line 65). HF_HOME is huggingface_hub's, which this module "
    "imports (line 19): $HF_HOME, else $XDG_CACHE_HOME/huggingface, else ~/.cache/huggingface, "
    "expanded for ~ and then for environment variables (huggingface_hub 1.33.0, the version "
    "uv.lock holds beside lerobot 0.7.0, constants.py lines 159 to 167, and the same lines in the "
    "2.4.0 a `uv pip install` of lerobot 0.7.0 resolved to on the day, with nothing to hold the "
    "hub below 2). Every step is os.getenv with a default, so a variable set to nothing still "
    "counts as set. Building a robot no longer makes the directory; saving a calibration does "
    "(robot.py line 182). The arm simulator walks the same chain without importing lerobot, to "
    "find the file a registered arm's twin reads. Two arms sharing an id share a file, and "
    "nothing in it names a serial number",
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
    src(_ROBOT, 171),
    "_load_calibration() opens the file under draccus.config_type('json') and loads it as "
    "dict[str, MotorCalibration] (line 172), and _save_calibration() writes it back the same "
    "way (lines 183 and 184). So the file is one JSON object with a key per motor, each holding "
    "exactly MotorCalibration's five integer fields, each decoded as CALIBRATION_INTS says. The "
    "arm simulator reads it with the json module and refuses an entry that leaves a field out or "
    "adds one, as draccus does. 0.7.0's release notes say calibration files are unchanged, and a "
    "file written under 0.6.1 is the file this reads",
)
CALIBRATION_INTS = UpstreamRef(
    "draccus>=0.11.6,<0.12.0",
    "VERIFIED",
    src("pyproject.toml", 70),
    "the draccus LeRobot reads a calibration file with (CALIBRATION_FILE). draccus 0.11.6, the "
    "version uv.lock holds beside lerobot 0.7.0, decodes an int field in decode_int "
    "(draccus/parsers/decoding.py line 111 at its v0.11.6 tag, github.com/dlwh/draccus): a "
    "float is refused, 100.0 included, and anything else goes through int(), so true reads as "
    "1 and a string of digits as its number. A null passes through as None before decode_int "
    "is reached (line 256). The arm simulator decodes each field the same way, so an arm's "
    "twin loads the file its arm loads, except that it refuses a null, because it builds the "
    "arm's travel from every field",
)

# ── the SO-101 follower (the arm the adapter targets by default) ────────────────────────

MAKE_ROBOT = UpstreamRef(
    "lerobot.robots.make_robot_from_config(config)", "VERIFIED", src(_UTILS, 28)
)
ROBOT_TYPE_SO101 = UpstreamRef(
    "so101_follower",
    "VERIFIED",
    src(_SO_CFG, 56),
    "the registered config type; make_robot_from_config dispatches on it (utils.py line 46)",
)
ROBOT_TYPE_SO100 = UpstreamRef(
    "so100_follower",
    "VERIFIED",
    src(_SO_CFG, 57),
    "registered on the one config class so101_follower is, so at this tag the two are one class "
    "under two names (SO_NAME): an SO-100 runs the code that drove the SO-101",
)
SO101_CONFIG = UpstreamRef(
    "lerobot.robots.so_follower.SO101FollowerConfig",
    "VERIFIED",
    src(_SO_CFG, 64),
    "an alias of SOFollowerRobotConfig (SO_CONFIG has its fields)",
)
SO100_CONFIG = UpstreamRef(
    "lerobot.robots.so_follower.SO100FollowerConfig",
    "VERIFIED",
    src(_SO_CFG, 63),
    "the same alias as SO101_CONFIG",
)
ROBOT_TYPE_KOCH = UpstreamRef(
    "koch_follower",
    "VERIFIED",
    src(_KOCH_CFG, 22),
    "another class, on a bus of other servos. quackd lists it and describes it, and builds none",
)
KOCH_CONFIG = UpstreamRef(
    "lerobot.robots.koch_follower.KochFollowerConfig",
    "VERIFIED",
    src(_KOCH_CFG, 24),
    "use_degrees defaults to False on it (line 39), where the SO's defaults to True "
    "(config_so_follower.py, line 42), so wiring one would have to pass True as quackd does for an "
    "SO arm",
)
KOCH_MOTORS = UpstreamRef(
    "shoulder_pan to gripper on a SerialMotorsBus: xl430-w250 on the first two, xl330-m288 on "
    "the other four",
    "VERIFIED",
    src(_KOCH, 51),
    "the SO-101's six joint names, on servos of another maker, to line 56, on the one bus class "
    "the SO follower uses too (line 48), the ids in KOCH_MOTOR_IDS",
)
KOCH_MOTOR_IDS: dict[str, int] = {
    "shoulder_pan": 1,
    "shoulder_lift": 2,
    "elbow_flex": 3,
    "wrist_flex": 4,
    "wrist_roll": 5,
    "gripper": 6,
}
"""Each motor's bus id in the Koch follower's table, as KOCH_MOTORS reads it at the pin."""
KOCH_REGISTERS = UpstreamRef(
    "Torque_Enable (64, 1) and Present_Temperature (146, 1)",
    "VERIFIED",
    rp("src/servo/dynamixel/xl430.rs", 51),
    "the two names quackd reads off a bus, in rustypot's XL430 table, with Present_Temperature "
    "at line 80, and at the same addresses in its XL330 table (xl330.rs lines 52 and 81): "
    "LeRobot's own Dynamixel tables went with #4672. How a Dynamixel bus fails has been read at "
    "the pin (DYNAMIXEL_BUS_WORDS and the rows beside it, including protocol 2's status and what "
    "rustypot makes of it), which wiring a Koch would have to act on",
)
SO_FOLLOWER = UpstreamRef(
    "lerobot.robots.so_follower.SO101Follower",
    "VERIFIED",
    src(_SO, 225),
    "an alias of SOFollower (SO100Follower too, line 224); exported by the package __init__",
)
SO_NAME = UpstreamRef(
    "SOFollower.name is so_follower",
    "VERIFIED",
    src(_SO, 61),
    "the class name, and therefore the calibration subdirectory: an SO-100 and an SO-101 "
    "share one, because at this tag they are the same class",
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
    src(_SO, 72),
    "six Feetech sts3215 motors, ids 1..6 (lines 72 to 77, SO_MOTOR_IDS). A calibration file "
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
    "'<motor>.pos'", "VERIFIED", src(_SO, 167), "joint positions; the same keys are the action"
)
SO_OBSERVATION_IS_POSITION_ONLY = UpstreamRef(
    "get_observation() reads Present_Position and nothing else",
    "VERIFIED",
    src(_SO, 166),
    "one sync_read of the positions, with num_read_retries extra attempts, plus a frame per "
    "camera. No torque state, no current, no temperature and no error flag, so a joint that "
    "has tripped its own protection looks exactly like one that has arrived",
)
SO_CAMERA_KEYS = UpstreamRef(
    "camera name -> array",
    "VERIFIED",
    src(_SO, 175),
    "get_observation() adds cam.read_latest() under each configured camera's name, and a depth "
    "camera's depth under `<name>_depth` (line 179)",
)
SO_ACTION_CLAMP = UpstreamRef(
    "max_relative_target caps each step",
    "VERIFIED",
    src(_SO, 206),
    "ensure_safe_goal_position (utils.py line 137) clips a goal to present +/- the cap and "
    "logs a warning when it does; None, which is upstream's default, means no cap. Setting "
    "it costs one extra sync_read of the present position per send_action (line 207). "
    "send_action reads it off self.config on every call (lines 206 and 209), so a cap written "
    "between two sends caps the next, which is how a policy segment gets its own",
)
SO_ACTION_CLAMP_IS_FLOAT = UpstreamRef(
    "max_relative_target must be a float or a dict per motor",
    "VERIFIED",
    src(_UTILS, 142),
    "ensure_safe_goal_position tests isinstance(float), then isinstance(dict), and raises "
    "TypeError on anything else, so an int cap raises rather than capping. quackd casts",
)
ENSURE_SAFE_GOAL_POSITION = UpstreamRef(
    "ensure_safe_goal_position(goal_present_pos, max_relative_target)",
    "VERIFIED",
    src(_UTILS, 137),
    "the whole step cap, twenty lines. A float cap applies to every motor in the goal; a dict "
    "cap must have exactly the goal's keys or it raises ValueError (line 146); anything else, "
    "an int included, raises TypeError (line 149). Each goal then becomes present + (goal - "
    "present) clipped to the cap either way (lines 153 to 159), so the cap is measured from the "
    "present reading, which send_action() reads fresh off Present_Position just before "
    "(so_follower.py line 207), and a goal moved by more than 1e-4 is logged as a warning "
    "(line 160) through the module's logger (line 167). A NaN cap caps nothing, because min "
    "and max hand back the goal when the other side is NaN. The arm simulator's follower "
    "reimplements it rather than importing it, because lerobot needs Python 3.12 and torch and "
    "the simulator runs on 3.11 without either",
)
SO_SEND_ACTION_RETURN = UpstreamRef(
    "send_action() returns the goal actually sent",
    "VERIFIED",
    src(_SO, 213),
    "the clipped goal in '<motor>.pos' keys, which is not the measured position. quackd "
    "keeps it rather than assuming the goal it asked for was the one written",
)
SO_DEGREES = UpstreamRef(
    "use_degrees=True -> body joints in degrees",
    "VERIFIED",
    src(_SO, 68),
    "MotorNormMode.DEGREES; False means a -100..100 range",
)
SO_GRIPPER_RANGE = UpstreamRef(
    "gripper is 0..100", "VERIFIED", src(_SO, 77), "MotorNormMode.RANGE_0_100 whatever use_degrees"
)
SO_DISCONNECT_TORQUE = UpstreamRef(
    "disconnect() disables torque by default",
    "VERIFIED",
    src(_SO, 217),
    "disable_torque_on_disconnect defaults to True (config line 31): LeRobot lets the arm go "
    "limp whenever the follower is disconnected, and that includes the disconnect it makes of "
    "a follower nobody closed (ROBOT_DEL), so under that default an exit that skipped quackd's "
    "close(), a second Ctrl-C during the rest move or a crash, could drop the arm. quackd builds "
    "the follower with it False, and close() writes it on the instance every time: True over "
    "an arm at its recorded rest pose, which is the limp end of every clean session, and False "
    "over one that did not reach its pose, where letting go would drop it, and over a real arm "
    "with none recorded, which only a probe such as doctor reaches, a run of one being refused "
    "(SO_DISCONNECT_READS_ITS_CONFIG_LATE). A connect quackd "
    "refuses once the arm is energised (not calibrated, no calibration file, no motors bus) "
    "writes True before its own disconnect, and one that fails any other way closes the port "
    "and keeps torque. Nothing runs when the process is killed. Under the flag the bus tries "
    "every motor and releases the port whatever the torque-off did (BUS_DISCONNECT)",
)
SO_DISCONNECT_READS_ITS_CONFIG_LATE = UpstreamRef(
    "disconnect() reads config.disable_torque_on_disconnect when it runs",
    "VERIFIED",
    src(_SO, 217),
    "the flag is read off the config instance inside disconnect() rather than copied at "
    "construction, and SOFollowerConfig is a plain dataclass, so the value on the instance when "
    "disconnect() runs is what it does, whoever calls it. _config_kwargs() asks for False and "
    "every connect asks again, so a disconnect quackd did not make, ROBOT_DEL's included, "
    "keeps torque, and the bus's disconnect(False) closes the port with every motor still "
    "holding its goal (BUS_DISCONNECT). quackd writes True just before its own disconnect "
    "only over an arm that may be let go. Read first against lerobot 0.6.1, the version the "
    "first real arm ran, and the same line at the tag",
)
SO_GRIPPER_TORQUE_LIMIT = UpstreamRef(
    "Max_Torque_Limit 500 on the gripper",
    "VERIFIED",
    src(_SO, 155),
    "configure() caps the gripper at 50 % torque, 50 % current (Protection_Current 250) and "
    "25 % torque once overloaded (Overload_Torque 25), lines 155 to 157: the native safety "
    "authority. The line's own comment calls 500 '50% of max torque', so the register's full "
    "scale is 1000, and the arm simulator scales its model gripper's force range by that ratio "
    "(GRIPPER_MAX_TORQUE_LIMIT over MAX_TORQUE_LIMIT_FULL)",
)
GRIPPER_MAX_TORQUE_LIMIT = 500
"""What configure() writes to the gripper's Max_Torque_Limit (SO_GRIPPER_TORQUE_LIMIT)."""
MAX_TORQUE_LIMIT_FULL = 1000
"""The register's full scale, from the comment beside that write, which calls 500 half of it."""
SO_BODY_HAS_NO_TORQUE_CAP = UpstreamRef(
    "the five body joints get no torque or current cap",
    "VERIFIED",
    src(_SO, 154),
    "the three caps above are inside a check for the gripper. Every other joint keeps "
    "whatever its firmware defaults to, so the manifest's torque_limit authority covers the "
    "gripper only and an elbow against an obstacle is the servo's own protection and nothing "
    "else",
)
SO_CONFIGURE_MOTORS = UpstreamRef(
    "configure_motors() writes Return_Delay_Time 0 and Acceleration 254",
    "VERIFIED",
    src(_BUS, 572),
    "to every motor (line 588 and lines 591 and 592), plus Maximum_Acceleration 254 where the "
    "servo has the register, as an sts3215 does (lines 589 and 590), and an sts3215 Phase fix "
    "(lines 595 to 598). configure() calls it inside torque_disabled() (so_follower.py lines 146 "
    "and 147), so connecting always drops torque briefly, and then puts each motor in position "
    "mode with an Operating_Mode write (line 149, motors_bus.py line 634)",
)
SO_IS_CONNECTED = UpstreamRef(
    "SOFollower.is_connected is the serial port plus the cameras",
    "VERIFIED",
    src(_SO, 107),
    "bus.is_connected and every camera's (line 108); BUS_IS_CONNECTED is what the bus half means",
)
SO_BUS = UpstreamRef(
    "SOFollower.bus is a SerialMotorsBus",
    "VERIFIED",
    src(_SO, 69),
    "the attribute quackd reads registers through when the Robot interface has no answer, and "
    "the class LeRobot's refusals name for the bus. FeetechMotorsBus is a deprecated alias of it "
    "(motors/feetech/feetech.py line 42) that the follower does not use",
)
NO_CLIENT_DEADMAN = UpstreamRef(
    "no deadman: nothing stops the arm when the client goes quiet",
    "VERIFIED",
    src(_SO, 188),
    "read end to end at the tag: SOFollower has no thread, timer, timeout or watchdog, and "
    "send_action writes Goal_Position and returns. Its new `supports_position_hold = True` "
    "(line 62) and Robot.hold() (robot.py lines 55 to 57) are switches for LeRobot's own rollout "
    "loop, not a deadman, and rustypot's Bus has no thread of its own (bus.rs read in full). A "
    "position-controlled arm holds its last goal under torque until the next write or "
    "disconnect(). quackd's stop re-sends the present position as the goal (hold) and never "
    "disables torque",
)
KINEMATICS_DEG2RAD = UpstreamRef(
    "RobotKinematics sets a URDF joint to np.deg2rad(degrees)",
    "VERIFIED",
    src(_KINEMATICS, 88),
    "forward_kinematics hands each joint in its joint_names the reading in radians with no "
    "offset and no sign, and inverse_kinematics seeds its solve the same way (line 123). It is "
    "LeRobot's own precedent for putting a calibrated reading on a model of the arm, and the "
    "one the arm simulator follows for the five arm joints, whose zero and sign are its "
    "assumptions in quackd_lerobot.sim.upstream_api",
)

# ── the bus, below the Robot interface, where the registers are ─────────────────────────

BUS_DISABLE_TORQUE = UpstreamRef(
    "SerialMotorsBus.disable_torque()",
    "VERIFIED",
    src(_BUS, 651),
    "NEVER called on quackd's own initiative. The one call is `let_go()`, and it has two "
    "doors, each opened by a person at a terminal. `let_go()` is `quackd run --by-hand`'s, and "
    "refuses anywhere but the arm's recorded rest pose, the same condition `close()` uses to "
    "decide that letting go will not drop it. `let_go(anywhere=True)` is `quackd robot "
    "release`'s and the end-of-run offer's, after each has told the person to hold the arm, "
    "and releases wherever the arm stands, with or without a rest pose recorded. No verb "
    "reaches either and no LLM can ask for it. On an sts3215 it writes Torque_Enable 0 and then "
    "Lock 0 to each motor in turn, through rustypot's Bus.set_torque (lines 636 to 648), the "
    "pair tried num_retry + 1 times a motor, and every motor is tried even after one fails "
    "(TORQUE_TRIED_ON_EVERY_MOTOR). num_retry defaults to 0 and quackd passes 5, the count "
    "upstream's own disconnect() gives the same call (line 457)",
)
BUS_ENABLE_TORQUE = UpstreamRef(
    "SerialMotorsBus.enable_torque()",
    "VERIFIED",
    src(_BUS, 670),
    "called by `take_hold()`, to pick up an arm a person has just placed, with num_retry=5 "
    "as for the release. On an sts3215 it writes Torque_Enable 1 and then Lock 1 to each "
    "motor in turn (rustypot bus.rs lines 240 to 247): two writes a motor, not one, every motor "
    "tried (TORQUE_TRIED_ON_EVERY_MOTOR), and connect() makes this same call with no retry at "
    "all (CONFIGURE_TORQUE_WRITES_ONCE)",
)
TORQUE_TRIED_ON_EVERY_MOTOR = UpstreamRef(
    "Failed to <enable|disable> torque after <k> tries on '<joint>' (motor ID <N>): <error>, ...",
    "VERIFIED",
    src(_BUS, 647),
    "the one ConnectionError a torque call raises (lines 642 to 648) once rustypot's "
    "Bus.set_torque has tried every motor, even after one failed, and handed back each that "
    "still failed with its error (RUSTYPOT_SET_TORQUE): one `'<joint>' (motor ID <N>): "
    "<error>` per motor, joined by commas. So a torque call that raised has reached every motor "
    "it does not name, and those took the write, while each it names may not have: on the old "
    "pin the call stopped at the first write that failed. A Torque_Enable and a Lock are retried "
    "together, a motor at a time. quackd names every joint the message names, through "
    "BUS_MOTORS, and counts it as a failed write",
)
RUSTYPOT_SET_TORQUE = UpstreamRef(
    "rustypot's Bus.set_torque tries every motor and hands back each that still failed",
    "VERIFIED",
    rp(_RP_BUS, 237),
    "a loop over every id (to line 253): each id's Torque_Enable, and then its Lock where the "
    "servo's EEPROM has one, as an sts3215's does, written in one try and retried together "
    "(`retrying`, lines 238 to 249), a failure kept by id in a sorted map and the next id tried "
    "(lines 250 to 252), and the map handed back (line 254). A try ends at its first write that "
    "gets no reply, so a motor that does not answer never gets its Lock. This is the half of "
    "TORQUE_TRIED_ON_EVERY_MOTOR below LeRobot's Python, its source here so the nightly compares "
    "it with rustypot's newest release, since LeRobot 0.7.0 allows any rustypot below 2: a "
    "set_torque that stopped at its first failure would have quackd tell a person that joints "
    "had let go which still hold",
)
BUS_DISCONNECT = UpstreamRef(
    "SerialMotorsBus.disconnect(disable_torque=True)",
    "VERIFIED",
    src(_BUS, 440),
    "the disable_torque call is inside `if disable_torque` (lines 456 and 457), so False closes "
    "the port and writes to no motor, leaving every motor holding the goal it was last written: "
    "what an arm that missed its rest pose gets instead of falling. The same call closes the port "
    "between two connect attempts (CONFIGURE_TORQUE_WRITES_ONCE), because the follower's own "
    "disconnect() would first switch torque off on every motor, five tries a write, on a bus that "
    "has just lost a packet. It is not check_if_not_connected: a bus already closed returns "
    "without a word (lines 452 and 453), and the port is released whatever the torque-off did "
    "(DISCONNECT_RELEASES_THE_PORT). There is no port handler and no busy flag to clear after it "
    "any more (NO_BUSY_FLAG)",
)
DISCONNECT_RELEASES_THE_PORT = UpstreamRef(
    "SerialMotorsBus.disconnect() releases the port whatever its torque-off did",
    "VERIFIED",
    src(_BUS, 458),
    "the torque-off runs in a `try` whose `finally` is `_close()` (lines 455 to 459), which "
    "closes rustypot's Bus and forgets it (lines 432 to 434), so a release that raised has still "
    "shut the port. On the old pin a torque-off that raised left the port open, and the close "
    "never ran. A disconnect that raised has shut the port and tried every motor, and the motors "
    "its error names may still hold (TORQUE_TRIED_ON_EVERY_MOTOR)",
)
NO_BUSY_FLAG = UpstreamRef(
    "the bus holds rustypot's Bus and no port handler",
    "VERIFIED",
    src(_BUS, 215),
    "`self._bus`, rustypot's Bus while the port is open and None otherwise, is the whole of the "
    "connection: there is no `port_handler` anywhere in LeRobot at the tag, so no servo SDK's busy "
    "flag that a serial error in the middle of a packet could leave raised. rustypot flushes "
    "whatever is waiting on the line before every instruction instead, up to three times 5 ms "
    "apart, and fails with `Timeout error` when bytes keep coming "
    f"({rp(_RP_PROTOCOL, 757)}, to line 806). quackd's close used to lower that flag after its "
    "own; it has nothing to lower now",
)
BUS_MOTORS = UpstreamRef(
    "SerialMotorsBus.motors: name -> Motor(id, model, norm_mode)",
    "VERIFIED",
    src(_BUS, 148),
    "the table the bus addresses every servo through (kept at line 65), which the SO follower "
    "fills at so_follower.py lines 71 to 78. quackd reads a Motor's id to name the joint a bus "
    "error is about, and looks it up there rather than assume the order SO_MOTORS lists, "
    "because the table is what gave each servo its address. A bus call that takes motors "
    "resolves them through it (_get_motors_list, line 310): None is every motor in the table's "
    "order, a name is itself, an id is the motor the table gives it, a sequence is each of those "
    "in its own order, and anything else is a TypeError. The torque calls write to those motors "
    "and no others, in that order (lines 639 to 641). The arm simulator's bus resolves them the "
    "same way",
)
BUS_WRITE_ERROR_NAMES_THE_ID = UpstreamRef(
    "Failed to write <value> to '<register>' of motor ID <N> after <k> tries. <error>",
    "VERIFIED",
    src(_BUS, 1184),
    "the ConnectionError SerialMotorsBus.write() raises when rustypot's write got no good status "
    "packet back (lines 1182 to 1185), and read() says \"Failed to read '<register>' from motor ID "
    '<N> after <k> tries. <error>" the same way (line 1136). The RuntimeError each raises for a '
    "motor that refused the instruction (lines 1187 to 1191, and 1139 to 1141) never comes from "
    "an sts3215 (STATUS_ALARMS_ONLY). The id is the servo's bus address, and quackd names the "
    "joint through BUS_MOTORS, off `motor ID <N>`. <error> is rustypot's text (BUS_SYNC_READ_ERROR "
    "says which). A sync read and a sync write say `motor IDs [...]`, several servos at once",
)
BUS_HANDSHAKE = UpstreamRef(
    "_handshake",
    "VERIFIED",
    src(_BUS, 436),
    "SerialMotorsBus.connect() opens the port and then runs _handshake() (lines 414 to 430), "
    "which is _assert_motors_exist() and then _assert_same_firmware() (lines 437 and 438): a "
    "read of each motor's Model_Number, then two sync reads of the firmware's version, and not "
    "one write. configure(), where every write of a connect is, runs only once bus.connect() "
    "has returned (so_follower.py lines 117 and 127). Between the two, line 118 is `if not "
    "self.is_calibrated and calibrate:`, whose left side is evaluated first, so the calibration "
    "check (BUS_IS_CALIBRATED) runs on every connect, calibrate=False included: it reads every "
    "motor and writes to none. So a connect that fails in the handshake or in that check has left "
    "every motor's torque as it found it. A failure raised in the handshake has this frame in its "
    "own traceback: _connect closes the port behind it and raises it again as it was "
    "(HANDSHAKE_CLOSES_THE_PORT), and makes its own port error, `from` the original, only of an "
    "OSError from opening the port (lines 417 to 421)",
)
HANDSHAKE_CLOSES_THE_PORT = UpstreamRef(
    "a failed handshake closes the port behind it",
    "VERIFIED",
    src(_BUS, 429),
    "_connect catches whatever the handshake raises, closes rustypot's Bus and raises it again "
    "(lines 424 to 430), so a connect refused in the handshake, a servo that did not answer "
    "among them, leaves the port shut and every later connect free to open it. On the old pin it "
    "stayed open. A failure in the calibration check or in configure() still leaves it open "
    "(SO_CONNECT_REFUSES_WHILE_OPEN). So a port shut after a failed connect is either one that "
    "never opened or one this closed, and the handshake wrote to no motor either way: quackd "
    "tells the two apart by the handshake's frame and says which",
)
HANDSHAKE_NAMES_THE_ID = UpstreamRef(
    "Missing motor IDs: / Motors with incorrect model numbers: - <N> (...)",
    "VERIFIED",
    src(_BUS, 334),
    "the RuntimeError _assert_motors_exist() raises (lines 350 to 371), headed '<class> motor "
    "check failed on port' (line 351): a line '  - <N> (expected model: <M>)' per motor that did "
    "not answer, and a line '  - <N> (<joint>): expected <M>, found <K>' per motor that answered "
    "with another model number, each list under its own heading. Each motor is asked for its "
    "Model_Number by ping() (lines 1085 to 1088), which answers None only for a ConnectionError "
    "or a refusal, so an sts3215 answering with an alarm set, an overload say, is found and not "
    "listed as missing, as it was on the old pin (STATUS_ALARMS_ONLY). The id is the bus "
    "address, and quackd names the joint through BUS_MOTORS as for a write",
)
BUS_SYNC_READ_ERROR = UpstreamRef(
    "Failed to sync read '<register>' from motor IDs [<N>, ...] after <k> tries. motor <M>: "
    "<error>",
    "VERIFIED",
    src(_BUS, 1238),
    "the ConnectionError SerialMotorsBus.sync_read() raises when no good reply came back "
    "(lines 1236 to 1239), listing every motor it read by bus id, in the table's order. "
    "sync_write() says \"Failed to sync write '<register>' to motor IDs [<N>, ...] after <k> "
    'tries. <error>" (lines 1292 to 1295) only when the packet could not be sent, because it '
    "waits for no reply (rustypot's sync_write sends and returns, "
    f"{rp(_RP_PROTOCOL, 746)}, to line 755). The words after the list are rustypot's: a sync "
    "read that failed at one motor's reply says `motor <M>: ` and why (lines 997 to 1001 there), "
    "M being the first motor whose own status packet did not come back whole, with every motor "
    "after it not known to be fine (lines 973 to 978). `Operation timed out` is serialport-rs's "
    f"for a reply that never came ({serialport('src/windows/com.rs', 229)} and "
    f"{serialport('src/posix/poll.rs', 37)}), and `Timeout error` is rustypot's for a packet "
    "that could not go out or stale input it could not flush (lines 757 to 806). quackd "
    "names motor M as the first that did not answer, through BUS_MOTORS, and names no joint for "
    "a sync write. The arm simulator raises these words for the read and goal write faults it "
    "injects",
)
STS3215_MODEL_NUMBER = UpstreamRef(
    "sts3215 model number 777",
    "VERIFIED",
    rp("src/servo/mod.rs", 156),
    "what a servo of the SO-101's model answers a ping with, in rustypot's table, which LeRobot "
    "reads through `rustypot.find_model` (motors_bus.py lines 241 to 246) and the handshake "
    "expects of every motor in the bus table and prints beside each id it lists "
    "(HANDSHAKE_NAMES_THE_ID). The arm simulator's handshake fault prints it where the arm's "
    "would",
)
STS3215_MODEL = 777
"""STS3215_MODEL_NUMBER's number."""
NOT_CONNECTED = UpstreamRef(
    "check_if_not_connected refuses a call on a port that is not open",
    "VERIFIED",
    src(_DECORATORS, 22),
    "a DeviceNotConnectedError, which is a ConnectionError (utils/errors.py line 16), saying "
    "'<class> is not connected. Run `.connect()` first.' (line 27). SOFollower's "
    "get_observation(), send_action() and disconnect() carry it (so_follower.py lines 162, 187 "
    "and 215), and so do SerialMotorsBus's ping(), read(), write(), sync_read(), sync_write(), "
    "disable_torque() and enable_torque() (motors_bus.py lines 1069, 1090, 1146, 1193, 1253, 650 "
    "and 669), and everything else that reaches the port refuses in the same words "
    "(`_connected_bus`, lines 253 to 260), so nothing reaches a motor through a port that is "
    "shut. The bus's disconnect() does not carry it (BUS_DISCONNECT). Its twin "
    "check_if_already_connected (line 34) says '<class> is already connected.' "
    "(SO_CONNECT_REFUSES_WHILE_OPEN). The arm simulator's follower and bus refuse in the same "
    "words under upstream's class names, SOFollower and SerialMotorsBus, so a rehearsal fails "
    "where the arm would and says what the arm would",
)
SO_CONNECT_REFUSES_WHILE_OPEN = UpstreamRef(
    "SOFollower.connect() refuses while the port is open",
    "VERIFIED",
    src(_SO, 110),
    "it is check_if_already_connected (utils/decorators.py lines 34 to 41), which raises "
    "DeviceAlreadyConnectedError while is_connected is True, and is_connected is the bus's "
    "port flag and every camera's (SO_IS_CONNECTED; quackd's follower has no camera). connect() "
    "opens the port first (line 117) and configures last (line 127). A failed handshake shuts the "
    "port itself (HANDSHAKE_CLOSES_THE_PORT), and nothing shuts it when the calibration check or "
    "configure() raises, so after one of those every later connect() is refused until the port "
    "is shut. SerialMotorsBus.connect() carries the same decorator (motors_bus.py line 396). "
    "quackd closes the port with the bus's disconnect(False) between attempts (BUS_DISCONNECT)",
)
CONFIGURE_TORQUE_WRITES_ONCE = UpstreamRef(
    "configure() switches torque off and on again with no retry",
    "VERIFIED",
    src(_BUS, 685),
    "SOFollower.configure() (so_follower.py line 146) runs inside torque_disabled(), which "
    "calls disable_torque() on the way in and enable_torque() in its finally (lines 701 and "
    "705), both with num_retry left at 0. Each tries every motor and raises once, naming each "
    "that failed (TORQUE_TRIED_ON_EVERY_MOTOR), and each other write of configure() is one "
    "transaction that raises when its status packet does not come back (BUS_WRITE_ERROR_NAMES_THE_"
    "ID). So one lost packet still fails the whole connect, with the port left open. Lost on the "
    "way in, it leaves every motor but the ones named limp, since the raise comes before the "
    "`try` and nothing switches them back on; lost on the way back on, the ones named may be limp "
    "and the rest hold. On 2026-09-23 an SO-101 on lerobot 0.6.1 failed three connects this way, "
    "each on a Lock write to a different motor, and the next connect went through each time. "
    "quackd tries again (CONNECT_ATTEMPTS in real.py) instead of giving up on the first packet, "
    "and says which joints each failure named",
)
BUS_IS_CONNECTED = UpstreamRef(
    "SerialMotorsBus.is_connected is whether it holds rustypot's Bus",
    "VERIFIED",
    src(_BUS, 394),
    "`self._bus is not None`: whether the port is open, not a reply from a motor. Unplug the arm "
    "and it stays True until somebody closes the bus, a read failing included, which is why "
    "quackd's heartbeat reads the arm rather than the flag",
)
BUS_IS_CALIBRATED = UpstreamRef(
    "SerialMotorsBus.is_calibrated reads the motors back",
    "VERIFIED",
    src(_BUS, 733),
    "it reads Min_Position_Limit, Max_Position_Limit and Homing_Offset off every motor, and "
    "Drive_Mode off a motor that has it, which an sts3215 does not, and compares them with the "
    "cached file (lines 719 to 742). A missing file, a stale file, and the file of a different "
    "arm all come back False, which is the check quackd refuses on. The reads are "
    "read_calibration() (lines 744 to 767), sync reads with num_retry left at 0 (lines 753 to "
    "756), and a lost status packet in one raises \"Failed to sync read 'Min_Position_Limit' from "
    'motor IDs [...] after 1 tries. motor <N>: ..." (BUS_SYNC_READ_ERROR). SOFollower.connect() '
    "makes this check itself, between the handshake and configure() (so_follower.py line 118, "
    "BUS_HANDSHAKE), so a connect can fail in it having written nothing, and quackd places such a "
    "failure by the is_calibrated and read_calibration frames in its traceback",
)
BUS_WRITE_CALIBRATION = UpstreamRef(
    "write_calibration() is reached only through calibrate()",
    "VERIFIED",
    src(_BUS, 774),
    "it writes the limits and the homing offset into the motors (lines 790 to 794), reached "
    "through calibrate() (robot.py line 196, so_follower.py line 141); connect(calibrate=False) "
    "never calls it, so quackd cannot move an arm's zero even by accident. The limits are "
    "each motor's calibrated range_min and range_max, written into its Min_Position_Limit and "
    "Max_Position_Limit registers (POSITION_LIMITS_CLAMP_GOALS says what the servo does with "
    "them)",
)
POSITION_LIMITS_CLAMP_GOALS = UpstreamRef(
    "write_calibration() writes Min_Position_Limit and Max_Position_Limit, and the servo "
    "clamps Goal_Position to them",
    "VERIFIED",
    src(_BUS, 774),
    "lines 793 and 794: each motor's range_min and range_max go into its own EEPROM as "
    "Min_Position_Limit and Max_Position_Limit. The STS3215 firmware then clamps every "
    "Goal_Position write to those two registers, which no LeRobot source says (DEGREES_NO_CLAMP "
    "bounds nothing) and an SO-101 showed on 2026-09-23: a joint driven down from above stopped "
    "one encoder tick inside its floor, and every goal written below the floor moved a joint "
    "folded past it up to it. A reading is not clamped: with torque off an arm folds wherever a "
    "hand or its weight puts it, past either limit. So quackd drives a rest pose clipped into the "
    "travel, judges a joint folded past its limit as at rest, and never writes a goal for a joint "
    "that reads past its travel, because the one goal the servo would take there is the limit "
    "and it hauls the joint to it",
)
BUS_SYNC_READ = UpstreamRef(
    "SerialMotorsBus.sync_read(data_name, motors=None, *, normalize=True, num_retry=0)",
    "VERIFIED",
    src(_BUS, 1194),
    "rustypot's Sync Read of every motor named, one per group of motors that share a protocol "
    "and the register's address, which on an SO-101 is one, and a retry sends every group again "
    "(lines 1215 to 1218); quackd uses it for the registers get_observation() does not read",
)
BUS_NORMALIZED_DATA = UpstreamRef(
    "normalized_data is goal_position and present_position, matched without case",
    "VERIFIED",
    src(_BUS, 197),
    "the only two names sync_read normalises, matched on `data_name.lower()` (line 1248), so "
    "every other register comes back raw whatever normalize says. quackd passes "
    "normalize=False anyway, because a raw register is what it means to read",
)
MOTOR_CALIBRATION = UpstreamRef(
    "MotorCalibration(id, drive_mode, homing_offset, range_min, range_max)",
    "VERIFIED",
    src(_BUS, 139),
    "range_min and range_max are raw encoder ticks recorded by calibration, not degrees",
)
CALIBRATION_EQUAL_RANGE = UpstreamRef(
    "Invalid calibration for motor '<motor>': min and max are equal.",
    "VERIFIED",
    src(_BUS, 1021),
    "_normalize() raises this ValueError for a motor whose range_min equals its range_max, "
    "and _unnormalize() raises it too (line 1050), so LeRobot loads such a file and then "
    "refuses the first reading or goal that goes through the motor. The arm simulator refuses "
    "the file as it reads it, which is the same arm refused sooner",
)
DEGREES_FORMULA = UpstreamRef(
    "degrees = (raw - mid) * 360 / 4095",
    "VERIFIED",
    src(_BUS, 1033),
    "mid is (range_min + range_max) / 2 (line 1031) and 4095 is the resolution less one (line "
    "1032, STS3215_RESOLUTION), so a joint's travel in degrees is (range_max - range_min) * 360 / "
    "4095, centred on zero. That is how quackd turns a calibration file into the range it will "
    "accept a goal inside",
)
DEGREES_NO_CLAMP = UpstreamRef(
    "a degrees goal is not clamped to the calibrated range",
    "VERIFIED",
    src(_BUS, 1060),
    "_unnormalize bounds the RANGE_0_100 and RANGE_M100_100 modes and does not bound "
    "DEGREES (lines 1060 to 1063): the tick it computes is written to Goal_Position as-is. So the "
    "gripper is clamped by LeRobot and the five body joints are not. The firmware clamps them "
    "instead, to the Min_Position_Limit and Max_Position_Limit calibration wrote into it "
    "(POSITION_LIMITS_CLAMP_GOALS, seen on an arm on 2026-09-23), so a goal past the travel is "
    "one the arm silently stops short of. quackd refuses a pilot's goal there rather than let "
    "it be quietly rewritten",
)
STS3215_RESOLUTION = UpstreamRef(
    "sts3215 resolution 4096",
    "VERIFIED",
    rp(_RP_STS3215, 9),
    "12 bits over a full turn, so one tick is about 0.088 degrees, in rustypot's table, which "
    "LeRobot reads through `resolution()` (motors_bus.py lines 268 to 283)",
)
STS3215_REGISTERS = UpstreamRef(
    "Torque_Enable (40, 1) and Present_Temperature (63, 1)",
    "VERIFIED",
    rp(_RP_STS3215, 68),
    "address and size in rustypot's STS3215 table, which LeRobot reads every register name "
    "through, temperature at line 81. Present_Load (line 79), Present_Voltage (80), Status (82), "
    "Present_Current (84) and Max_Temperature_Limit (43) are in the same table and quackd reads "
    "none of them yet. Nothing upstream reads these two either: the arm's own protection can "
    "drop torque and get_observation() will not mention it",
)
STATUS_ALARMS_ONLY = UpstreamRef(
    "an sts3215's status byte is alarms only, and nothing raises on one",
    "VERIFIED",
    rp(_RP_STS3215, 19),
    "the STS3215's definition says `status_alarms_only: true`, so `refuses()` is False for "
    f"every status it can send ({rp(_RP_DEFINITION, 320)}, to line 322), and the RuntimeError "
    "LeRobot raises for a motor that refused an instruction never comes from an sts3215 "
    "(motors_bus.py lines 1139, 1187 and 1240). An overloaded or overheated servo answers reads "
    "and writes as any other, its status byte thrown away (line 1246), and the handshake finds it "
    "(HANDSHAKE_NAMES_THE_ID). On the old pin a single read, write or ping raised on any error "
    "bit, so the handshake listed such a servo as missing. So LeRobot 0.7.0 surfaces no overload "
    "and no overheat at all, and quackd's own read of Present_Temperature, refused at 60 "
    "(TEMPERATURE_C), is the only heat guard it has. The Status register (line 82) holds the "
    "alarms, and quackd does not read it. What a servo's own protection then does to its torque "
    "is its firmware's, and nothing here has read or measured it",
)
PORT_OPENED_EXCLUSIVELY = UpstreamRef(
    "rustypot opens the port exclusively, on every platform",
    "VERIFIED",
    rp(_RP_BUS, 367),
    "rustypot's Bus opens the port through serialport-rs's builder (to line 370), whose open is "
    f"exclusive: no sharing on Windows ({serialport('src/windows/com.rs', 62)}), and TIOCEXCL "
    f"and an exclusive flock on Linux and macOS ({serialport('src/posix/tty.rs', 134)}, to line "
    f"136), the default there ({serialport('src/lib.rs', 913)}), a flock that fails rather than "
    f"waits ({serialport('src/posix/flock.rs', 12)}). So once quackd holds the port another "
    "program's open of it fails, and quackd's own open fails on a port another program holds "
    "for itself, as anything on Windows and LeRobot 0.7.0's own tools do, with an OSError, which "
    'LeRobot makes its port error, "Could not connect on port ..." (motors_bus.py lines 417 to '
    "421). On Linux and macOS a program that opened the port first with neither TIOCEXCL nor a "
    "flock, as pyserial does unless asked, does not stop quackd opening it too: serialport-rs's "
    "own comment says its lock holds only against programs that lock as well (flock.rs lines 9 "
    "and 10). Nor does TIOCEXCL stop root, so a program run as root that takes no lock opens the "
    "port beside quackd. The old SDK opened the port through pyserial, which locks it on Windows "
    "and, unless asked, not on Linux or macOS, so there two programs could talk over each other "
    "on one bus. quackd's refusal names the port and says to check that nothing else has it open",
)
STATUS_TIMEOUT_MS = UpstreamRef(
    "each attempt waits 50 ms for a status packet",
    "VERIFIED",
    src(_BUS, 195),
    "`default_timeout: int = 50`, in milliseconds, longer than a USB adapter's 16 ms latency "
    "timer by its own comment (lines 193 and 194), handed to rustypot's Bus as the port's read "
    "timeout in seconds (line 251), and a retry waits again, after rustypot's flush "
    "(NO_BUSY_FLAG). So a read of an arm that has stopped answering, with num_retry=2, comes "
    "back in some 150 ms a register, and a torque call with num_retry=5 spends some 300 ms on "
    "each motor that does not answer, every motor tried (TORQUE_TRIED_ON_EVERY_MOTOR): some 1.8 s "
    "over six silent motors, where the old pin's stopped at the first. That is past the second "
    "quackd gives most calls, so a release's and a take-hold's torque call get a deadline of "
    "their own (TORQUE_CALL_S in real.py)",
)
STATUS_TIMEOUT_S = 0.05
"""STATUS_TIMEOUT_MS's wait for one status packet, in seconds."""
FLUSH_TRIES = 3
FLUSH_PAUSE_S = 0.005
"""rustypot's flush before every instruction: up to three tries, 5 ms apart (NO_BUSY_FLAG)."""

# ── how a Dynamixel bus fails, where it differs from an SO arm's ────────────────────────

DYNAMIXEL_BUS_WORDS = UpstreamRef(
    "SerialMotorsBus is one class for Feetech and Dynamixel, so its failures read alike",
    "VERIFIED",
    src(_BUS, 157),
    "its docstring says so, and `DynamixelMotorsBus` is a deprecated alias of it "
    "(dynamixel/dynamixel.py line 44). So the sentences real.py reads are a Koch's as they are an "
    "SO arm's (BUS_WRITE_ERROR_NAMES_THE_ID, TORQUE_TRIED_ON_EVERY_MOTOR, BUS_SYNC_READ_ERROR and "
    "HANDSHAKE_NAMES_THE_ID), and rustypot words a failure the same way on both protocols: a "
    "Dynamixel speaks protocol 2 (xl430.rs line 8), whose Protocol impl is the shared one "
    "(dynamixel_protocol/v2.rs line 14, with sync_read_with_error at mod.rs lines 729 to 745), and "
    "Bus::new leaves fast sync read off (bus.rs line 45, mod.rs line 78), so `motor <M>: <error>` "
    "is still one motor's own status packet that did not come back whole. KochFollower.connect() "
    "has the SO's shape, the same frames real.py places a failure by (koch_follower.py line 96, "
    "bus.connect(), line 97, the calibration check, whatever `calibrate` says, line 106, "
    "configure(), under bus.torque_disabled() at line 125), and it disconnects the same way "
    "(line 206). Each pattern real.py reads holds there, except where the rows below say, each "
    "with what differs",
)
DYNAMIXEL_HANDSHAKE_PINGS_ONLY = UpstreamRef(
    "a Dynamixel bus's handshake is its Model_Number reads, with no firmware check",
    "VERIFIED",
    src(_BUS, 375),
    "_assert_same_firmware() reads Firmware_Major_Version and Firmware_Minor_Version (lines 377 "
    "and 378) through _sync_read_where, which reads a register only from the motors that have it "
    "(lines 769 to 772), and an XL430 or XL330 has neither (its firmware register is "
    "`firmware_version`, xl430.rs line 31, xl330.rs line 28), so no sync read goes out at all. A "
    "Koch's handshake is the six reads of Model_Number in _assert_motors_exist (BUS_HANDSHAKE), "
    "and writes nothing, as an SO arm's does, and a failure in it leaves the port shut the same "
    "way (HANDSHAKE_CLOSES_THE_PORT). The two firmware sync reads an SO's handshake makes are "
    "simply not there to fail",
)
DYNAMIXEL_FACTORY_BAUD = UpstreamRef(
    "an XL430 or XL330 leaves the factory at 57600 baud",
    "VERIFIED",
    rp(_RP_XL430, 15),
    "and xl330.rs line 17, where an sts3215 leaves it at 1000000 (sts3215.rs line 15), which is "
    "the rate LeRobot opens every bus at (motors_bus.py lines 192 and 251). setup_motors() is what "
    "writes a new rate to each servo (line 513). So a Koch whose servos were never set up answers "
    "no ping on a port opened at 1 Mbps, every motor comes back as missing, and quackd reads that "
    "as the arm's cables or its power (real.py's _every_motor_missing), which is the wrong remedy "
    "for a servo that was never told its baud rate. An SO's servos ship at the rate LeRobot "
    "speaks, so this is a failure only a Dynamixel has",
)
DYNAMIXEL_STATUS_REFUSES = UpstreamRef(
    "a Dynamixel can refuse an instruction, and LeRobot raises on it",
    "VERIFIED",
    rp(_RP_DEFINITION, 324),
    "STATUS_ALARMS_ONLY's opposite: an XL430 or XL330 does not set status_alarms_only (its default "
    "is false, servo_macro.rs lines 1139 to 1142), so `refuses()` is true for any non-zero error "
    "number on protocol 2 (bits 0 to 6, dynamixel_protocol/mod.rs lines 893 to 895), and the "
    "RuntimeError LeRobot raises for a motor that refused an instruction, which never comes from "
    "an sts3215, can come from one, on a read, a write or a sync read (motors_bus.py lines 1139 "
    "to 1141, 1187 to 1191 and 1240 to 1245, each naming `motor ID <N>`). The alert bit alone, "
    "0x80, is no refusal (definition.rs line 684, and `v2_alert`, mod.rs line 901): a latched "
    "hardware fault answers reads as an sts3215 in an alarm does (DYNAMIXEL_LATCHED_FAULT). A "
    "torque call's failure keeps its form with rustypot's words inside it, `motor <N> refused "
    "the instruction with status error 0x<XX>` (mod.rs lines 925 to 932), and a refused write is "
    "not tried again whatever num_retry says (definition.rs line 514). real.py's patterns read "
    "each of these as they read the rest: `motor ID <N>` names the joint, `Failed to write` "
    "counts as a write that failed, `'<joint>' (motor ID <N>)` names a torque failure, and "
    "LeRobot's words are quoted as they stand",
)
DYNAMIXEL_TORQUE_ONE_WRITE = UpstreamRef(
    "a torque call writes only Torque_Enable to a Dynamixel",
    "VERIFIED",
    rp(_RP_BUS, 244),
    "the Lock write is behind `if definition.info.eeprom_lock` (lines 244 to 247), which an XL430 "
    "or XL330 does not set and which defaults to false (servo_macro.rs lines 1128 to 1131), and "
    "neither table has a lock register. So a Koch's torque call is one write a motor, where an "
    "sts3215's is Torque_Enable and then Lock (BUS_ENABLE_TORQUE); every motor is still tried and "
    "retried the same way (RUSTYPOT_SET_TORQUE), so TORQUE_TRIED_ON_EVERY_MOTOR holds. real.py's "
    "TORQUE_WRITES of 2 is the SO arms' arithmetic for its deadline TORQUE_CALL_S, which on a "
    "Koch would only be longer than it needs",
)
BUS_IS_CALIBRATED_DYNAMIXEL = UpstreamRef(
    "is_calibrated also compares Drive_Mode on a Dynamixel",
    "VERIFIED",
    src(_BUS, 729),
    "BUS_IS_CALIBRATED's twin. An XL430 and an XL330 both have Homing_Offset and Drive_Mode "
    "(rustypot xl430.rs lines 39 and 35, xl330.rs lines 36 and 32), so read_calibration() makes "
    "four sync reads on a Koch, the two limits and then both of those (lines 753 to 756), where "
    "on an sts3215 it makes three, and _same_calibration() holds the file's drive_mode against "
    "the servo's as well as its homing_offset and limits (lines 725 to 729), where on an sts3215 "
    "the drive mode lives only in the file. A file whose drive_mode disagrees with the Drive_Mode "
    "register therefore reads as not calibrated on a Koch, which quackd refuses as it does any "
    "other mismatch. The check is still reads only: the same sync reads with num_retry 0, "
    "reached on every connect, calibrate=False included (koch_follower.py line 97), and placed "
    "by the is_calibrated and read_calibration frames real.py reads",
)
DYNAMIXEL_REFUSED_PING_IS_MISSING = UpstreamRef(
    "a Dynamixel that refuses the handshake's read is listed as missing",
    "VERIFIED",
    src(_BUS, 1087),
    "ping() returns None for a refusal as for a silence (lines 1085 to 1088), and the handshake "
    "lists a None as missing (lines 343 and 354 to 357), so a Koch servo that answered "
    "Model_Number with an error number (DYNAMIXEL_STATUS_REFUSES) reads as `Missing motor IDs: "
    "- <N>`, the same as one that did not answer. HANDSHAKE_NAMES_THE_ID's sts3215 with an alarm "
    "set answers and is found; a Dynamixel with only the alert bit is found too, and one that "
    "refuses is not. quackd names the joint through BUS_MOTORS and says to check its cable, which "
    "is right for a silent servo and misses one that answered and refused",
)
KOCH_POSITION_LIMITS_IGNORED = UpstreamRef(
    "a Koch's body motors ignore Min_Position_Limit and Max_Position_Limit",
    "VERIFIED",
    src(_KOCH, 132),
    "configure() puts every motor but the gripper in extended_position (lines 130 to 132), and "
    "SerialMotorsBus.set_operating_mode's docstring says both limit registers are ignored in that "
    "mode (motors_bus.py lines 612 and 613), where an sts3215 in position mode clamps every goal "
    "to them (POSITION_LIMITS_CLAMP_GOALS, an observation on a real SO-101). So the firmware "
    "clamp DEGREES_NO_CLAMP leans on is not there on a Koch's body joints: no limit register "
    "bounds a degrees goal, which leaves max_relative_target for each step and quackd's own "
    "check against the travel, and real.py's rest-pose logic, which judges a joint folded past "
    "its limit and never writes it a goal there because the servo would haul it to the limit, was "
    "written against a Feetech and would have to be read again before a Koch is wired. The "
    "gripper runs current_based_position (line 140)",
)
KOCH_READS_HAVE_NO_RETRIES = UpstreamRef(
    "a Koch's get_observation reads with no retries",
    "VERIFIED",
    src(_KOCH, 155),
    "KochFollowerConfig has no num_read_retries (config_koch_follower.py lines 24 to 39), so its "
    "get_observation's read of the positions and the read before a capped send_action (line "
    "196) take no retries, where an SO follower's both pass num_retry=config.num_read_retries, 2 "
    "by default (so_follower.py lines 166 and 207, config_so_follower.py line 53). One lost status "
    "packet is therefore a raised `Failed to sync read 'Present_Position' ... after 1 tries` on a "
    "Koch, where an SO arm's takes three losses in a row. real.py's patterns read that wording "
    "the same way, since the count of tries is no part of what they match, but a Koch's reads "
    "would fail where an SO's would not, so wiring one would want its heartbeat read retried",
)
DYNAMIXEL_LATCHED_FAULT = UpstreamRef(
    "DYNAMIXEL_LATCHED_FAULT",
    "UNVERIFIED",
    rp(_RP_PROTOCOL, 899),
    "rustypot says the alert bit of a protocol 2 status means a hardware fault is latched and "
    "not which one, that being in the servo's Hardware Error Status register (lines 899 and "
    "900, at address 70 in both XL tables, xl430.rs line 55 and xl330.rs line 56), and LeRobot "
    "reads the status byte and acts on refuses() alone (motors_bus.py lines 1139 and 1187). What "
    "the servo does next, whether it cuts its own torque and whether it keeps answering, is its "
    "firmware's, and nothing in LeRobot or rustypot reads it. quackd would not know either: it "
    "reads Torque_Enable and would say torque reads off, which names no fault, and it reads no "
    "Hardware Error Status. Whoever wires a Koch would have to decide whether that is enough",
)
KOCH_RESOLUTION = UpstreamRef(
    "xl430-w250 and xl330-m288 resolution 4096",
    "VERIFIED",
    rp(_RP_XL430, 9),
    "and xl330.rs line 11, in rustypot's tables, which LeRobot reads motor by motor through "
    "`resolution()` (motors_bus.py lines 268 to 283, its degrees formula taking each motor's own "
    "at lines 1032 and 1062). So a Koch's body joints are 4095 ticks a turn, as an SO's are "
    "(STS3215_RESOLUTION), and `ArmType.encoder_ticks` records it",
)
KOCH_MODEL_NUMBERS = UpstreamRef(
    "xl430-w250 model number 1060, xl330-m288 model number 1200",
    "VERIFIED",
    rp(_RP_SERVOS, 148),
    "(line 143 for the XL330), what a Koch's servos answer a ping with in rustypot's table, "
    "as STS3215_MODEL_NUMBER is an SO's, found by name without hyphens or case "
    "(servo_macro.rs lines 1901 to 1918). The handshake prints them beside each id it lists "
    "(HANDSHAKE_NAMES_THE_ID), two different numbers on a Koch's bus where an SO's shows one, and "
    "nothing quackd reads depends on them: it reads the ids",
)

# ── cameras ─────────────────────────────────────────────────────────────────────────────

CAMERA_ASYNC_READ = UpstreamRef(
    "Camera.async_read(timeout_ms)", "VERIFIED", src(_CAMERA, 122), "the most recent new frame"
)
CAMERA_READ = UpstreamRef("Camera.read()", "VERIFIED", src(_CAMERA, 111))
CAMERA_RGB_CONVERSION = UpstreamRef(
    "OpenCVCamera converts BGR to RGB when color_mode is RGB",
    "VERIFIED",
    src(_OPENCV, 452),
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
    "with DepthCamera and make_cameras_from_configs beside them (lines 15 and 17). "
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
    src(_OPENCV, 174),
    "its own words name `lerobot-find-cameras opencv` (line 175), so quackd passes them through",
)
OPENCV_MODE_IS_A_DEMAND = UpstreamRef(
    "a requested fps or size that the camera refuses raises RuntimeError",
    "VERIFIED",
    src(_OPENCV, 263),
    "the width at line 303 and the height at line 309; a fourcc only warns (line 284). So "
    "pinning a mode is a refusal on a camera that cannot do it, which is why quackd asks for "
    "none by default",
)
OPENCV_MODE_DEFAULTS_TO_THE_CAMERA = UpstreamRef(
    "an unset fps, width or height keeps the camera's own mode",
    "VERIFIED",
    src(_OPENCV, 231),
    "the guards read the device's defaults back instead of setting anything (fps at line "
    "240), which is what quackd relies on when it is pointed at an unknown webcam",
)
CAMERA_READ_LATEST = UpstreamRef(
    "Camera.read_latest(max_age_ms=500)",
    "VERIFIED",
    src(_OPENCV, 588),
    "the newest buffered frame, non-blocking; raises TimeoutError when it is older than "
    "max_age_ms (opencv line 616) and RuntimeError before the first frame or if the read "
    "thread has died (lines 605 and 612). quackd catches all of it and reports a camera error "
    "instead. The anchor is the OpenCV override on purpose: the base method (camera.py line 153) "
    "is not abstract, it emits a FutureWarning and delegates to async_read(), which blocks and "
    "takes a timeout_ms instead, so these semantics are the subclass's",
)
CAMERA_DISCONNECT = UpstreamRef("Camera.disconnect()", "VERIFIED", src(_CAMERA, 182))
SO_CAMERAS_ARE_THE_FOLLOWERS = UpstreamRef(
    "a follower's cameras are part of its connected state",
    "VERIFIED",
    src(_SO, 107),
    "is_connected is the bus AND every camera, and send_action and disconnect() are "
    "decorated on it, so a webcam that drops would make every move and every hold raise. "
    "That is why quackd builds its camera beside the follower and passes cameras={}",
)
FIND_PORT = UpstreamRef(
    "lerobot-find-port",
    "VERIFIED",
    src("pyproject.toml", 402),
    "lerobot.scripts.lerobot_find_port:main, a [project.scripts] entry beside "
    "lerobot-calibrate and lerobot-setup-motors. It lists the ports, asks you to unplug the "
    "arm, and names the one that disappeared, which is the only way to be sure which port "
    "is the arm rather than something else on the bus",
)
FIND_CAMERAS = UpstreamRef(
    "lerobot-find-cameras opencv",
    "VERIFIED",
    src("pyproject.toml", 401),
    "lerobot.scripts.lerobot_find_cameras:main; prints an Id per camera and saves one "
    "<type>_<id>.png per camera under outputs/captured_images (lerobot_find_cameras.py lines 143, "
    "144 and 292), which is how an owner learns which index is which. Off Linux it scans indices "
    "0 to 59 (camera_opencv.py lines 47 and 333)",
)

# ── UNVERIFIED: our assumptions, and what quackd does about each ────────────────────────

TORQUE_ENABLE_HOLDS_PRESENT = UpstreamRef(
    "TORQUE_ENABLE_HOLDS_PRESENT",
    "UNVERIFIED",
    src(_BUS, 670),
    "what a servo does with its goal when torque is switched back on. `enable_torque()` "
    "writes Torque_Enable and then Lock on each motor (rustypot bus.rs lines 240 to 247), "
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
    src(_SO, 77),
    "which end of the gripper's 0..100 range is open. 0 is the range_min tick of that "
    "motor's calibration and 100 the range_max, so which one is the open jaw is how the arm "
    "was assembled and calibrated. quackd assumes 100 is open, and the checklist asks for it "
    "to be confirmed by hand before anything is believed about holding",
)
HOLDING_INFERRED = UpstreamRef(
    "HOLDING_INFERRED",
    "UNVERIFIED",
    src(_SO, 166),
    "nothing reports grip force, so holding is inferred: the gripper was told to close, its "
    "reading has settled, and it settled short of shut. The band is quackd's own guess, an "
    "empty hand that binds reads as holding, and a thin enough object may not",
)
TEMPERATURE_C = UpstreamRef(
    "TEMPERATURE_C",
    "UNVERIFIED",
    rp(_RP_STS3215, 81),
    "Present_Temperature is one byte with no unit in rustypot's table, and Feetech's "
    "documentation calls it degrees Celsius, which nothing in LeRobot reads or converts. quackd "
    "reports it raw and refuses to move a joint at or above 60, which is the datasheet's "
    "operating maximum and below the servo's own 70 cut-off, so the number and the threshold are "
    "both ours to be wrong about. LeRobot 0.7.0 surfaces no overheat alarm (STATUS_ALARMS_ONLY), "
    "so this is the only heat guard quackd has",
)
CAMERA_INDEX_MOVES = UpstreamRef(
    "CAMERA_INDEX_MOVES",
    "UNVERIFIED",
    src(_OPENCV, 314),
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
    src(_SO, 68),
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
    "installed, is between the owner and their machine. The port is opened exclusively "
    "(PORT_OPENED_EXCLUSIVELY), so whatever else holds it for itself is a refusal at connect, "
    "and on Linux and macOS a program that opened it with no lock is not",
)
THREAD_SAFETY = UpstreamRef(
    "THREAD_SAFETY",
    "UNVERIFIED",
    src(_ROBOT),
    "Robot is synchronous and not documented as thread-safe, over a half-duplex serial bus "
    "where two talkers is a corrupt packet. quackd serialises every call under one lock in a "
    "worker thread with a deadline, and when a call blows its deadline it refuses every "
    "later call rather than starting a second thread on the same bus. Under LeRobot, rustypot's "
    "Bus serialises every call on one lock and lets go of the GIL while it waits for it and "
    f"while it talks ({rp(_RP_BUS, 326)}, and lines 336 and 337), and its close takes the same "
    "lock holding the GIL (lines 375 to 377), so a close made while another thread's call is on "
    "the wire holds up every Python thread until that call ends. quackd makes no close over a "
    "call it has lost, and LeRobot's own Robot.__del__ (ROBOT_DEL) is the one close it does not "
    "make. It opens the port holding the GIL as well (lines 358 to 371), so an open that blocks, "
    "a Bluetooth port's say, holds up every Python thread, quackd's own deadlines and its Ctrl-C "
    "among them, until the open returns, where pyserial's open let go of the GIL",
)


def all_refs() -> list[UpstreamRef]:
    return [v for v in globals().values() if isinstance(v, UpstreamRef)]


def refs_by_status(status: str) -> list[UpstreamRef]:
    return [r for r in all_refs() if r.status == status]
