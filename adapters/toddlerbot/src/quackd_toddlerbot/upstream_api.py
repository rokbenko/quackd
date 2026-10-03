"""The only file in quackd allowed to spell a ToddlerBot name (ADR-0028).

Every constant is tagged VERIFIED (read from upstream source, link given) or UNVERIFIED (an
assumption of ours, with what quackd does about it). `docs/adapters/toddlerbot/README.md` is the
human-readable version; `tests/test_upstream_api.py` proves UNVERIFIED names are only
reachable from the experimental `bridge` backend.

Source of truth: https://github.com/hshi74/toddlerbot at commit
84e02d14261292eec5d06f896e3145b35c54856c, which is what the annotated tag `v2.0.0` points at
(read 2026-09-05). The tag object's own sha is 6fde5df5 and is NOT a commit: pinning that gets
a 422 from the API.

This robot has no network API of any kind: no socket, no daemon, no IPC. It is a Python
library whose control loop opens serial ports in-process. So quackd ships a daemon, as it does
for the Open Duck Mini, and this file is what that daemon is written against.

Two facts about the pin itself, before anything else. `pyproject.toml` calls this version
0.2.0 while the tag says v2.0.0, and CI is disabled: the workflow echoes a skip and exits 0.
Nothing here was ever machine-verified by upstream.

Nothing here has been run against a ToddlerBot.
"""

from __future__ import annotations

from quackd.upstream import UpstreamRef

REPO = "https://github.com/hshi74/toddlerbot"
PIN = "84e02d14261292eec5d06f896e3145b35c54856c"  # what tag v2.0.0 points at
READ_ON = "2026-09-05"
TAG = "v2.0.0"


def src(path: str, line: int | None = None) -> str:
    return f"{REPO}/blob/{PIN}/{path}" + (f"#L{line}" if line else "")


_SIM = "toddlerbot/sim/__init__.py"
_REAL = "toddlerbot/sim/real_world.py"
_ROBOT = "toddlerbot/sim/robot.py"
_POL = "toddlerbot/policies/__init__.py"
_RESET = "toddlerbot/policies/reset_pd.py"
_MJX = "toddlerbot/policies/mjx_policy.py"
_MUJOCO = "toddlerbot/sim/mujoco_sim.py"
_MJUTILS = "toddlerbot/sim/mujoco_utils.py"
_MOTOR = "toddlerbot/sim/motor_control.py"
_MCH = "toddlerbot/actuation/src/dynamixel_mch.cpp"
_CLIENT = "toddlerbot/actuation/src/dynamixel_control/dynamixel_client.cpp"
_CTRL = "toddlerbot/actuation/src/dynamixel_control/dynamixel_control.cpp"
_CAM = "toddlerbot/sensing/camera.py"
_WALK = "toddlerbot/policies/walk.py"
_REPLAY = "toddlerbot/policies/replay.py"
_PULLUP = "toddlerbot/policies/pull_up.py"
_MIGRATE = "motion/migrate_pkl.py"
_ZMPREF = "toddlerbot/reference/walk_zmp_ref.py"
_GIN = "toddlerbot/locomotion/walk.gin"
_SPK = "toddlerbot/sensing/speaker.py"
_PROJ = "pyproject.toml"
_CI = ".github/workflows/pytest_ci.yml"

LICENCE_CODE = "MIT"
LICENCE_DESIGN = "CC BY-NC-SA 4.0 (non-commercial)"

# ── the contract, which is all six methods of it ────────────────────────────────────────

BASE_SIM = UpstreamRef(
    "BaseSim",
    "VERIFIED",
    src(_SIM, 65),
    "six abstract methods and nothing else: __init__(name), set_motor_target, set_motor_kps, "
    "step, get_observation, close. set_qpos and check_self_collisions are MuJoCo-only, so "
    "calling either on hardware is an AttributeError rather than a no-op.",
)
OBS = UpstreamRef(
    "Obs",
    "VERIFIED",
    src(_SIM, 10),
    "twelve fields, three of them required. There is no image field of any kind, so a camera "
    "frame cannot come from an observation and the daemon has to own the camera itself.",
)
OBS_NONE_ON_HARDWARE = UpstreamRef(
    "pos, lin_vel, joint_pos, joint_vel",
    "VERIFIED",
    src(_SIM, 10),
    "all None from RealWorld, along with motor_acc, and the two backends' None-sets are "
    "disjoint: MuJoCo fills these and leaves motor_cur None instead. Only time, motor_pos, "
    "motor_vel and motor_tor are non-None on both. There is no pose on hardware, ever.",
)
STEP_IS_A_NO_OP = UpstreamRef(
    "def step(self)",
    "VERIFIED",
    src(_REAL, 85),
    "its body is pass: the write already happened in set_motor_target. Nothing about calling "
    "step refreshes or re-arms anything, so a loop that pumps it believing it feeds a "
    "watchdog is doing nothing at all.",
)

# ── commanding, which clamps nothing ────────────────────────────────────────────────────

SET_MOTOR_TARGET = UpstreamRef(
    "RealWorld.set_motor_target",
    "VERIFIED",
    src(_REAL, 168),
    "absolute joint positions in radians, float32, in robot.motor_ordering order. It does not "
    "clamp: no clip, no min, no max, and it never reads robot.motor_limits, which exists. The "
    "value goes straight to the goal-position register.",
)
SET_MOTOR_TARGET_DICT_IS_ORDER_DEPENDENT = UpstreamRef(
    "motor_angles.values()",
    "VERIFIED",
    src(_REAL, 177),
    "the dict form takes insertion order with no reindex against motor_ordering, so a hip "
    "angle can be delivered to a neck motor. quackd only ever passes an array already in "
    "motor_ordering order.",
)
EXTENDED_POSITION_MODE = UpstreamRef(
    "extended_position",
    "VERIFIED",
    src(_CTRL, 10),
    "Dynamixel operating mode 4, multi-turn, which means the firmware position limits are "
    "OFF. Nothing stands between an out-of-range radian and a joint winding itself round "
    "except whatever the caller clamps.",
)
SET_MOTOR_KPS_RAISES = UpstreamRef(
    "NotImplementedError",
    "VERIFIED",
    src(_REAL, 199),
    "one of the six contract methods is unimplemented on hardware: it computes the gains, "
    "discards them and raises. Any shutdown that tried to soften the gains before releasing "
    "torque would die on this line, so quackd never calls it.",
)

# ── observing, which can lie ────────────────────────────────────────────────────────────

GET_OBSERVATION_IGNORES_RETRIES = UpstreamRef(
    "retries",
    "VERIFIED",
    src(_REAL, 116),
    "the parameter is accepted and then never read; the call it forwards to hardcodes zero. "
    "run_policy passes minus one at startup and it is silently ignored.",
)
OBSERVATION_CAN_BE_ALL_ZEROS = UpstreamRef(
    "bulk_read",
    "VERIFIED",
    src(_CLIENT, 196),
    "with zero retries a comm failure returns the pre-zeroed buffer and unavailable motors "
    "are skipped, leaving position, velocity and current at exactly zero. An all-zeros "
    "reading is indistinguishable from every joint genuinely at zero, and feeding it to a "
    "position controller commands a full-scale move to zero. quackd detects it and refuses.",
)
CONTROLLER_FAULT_IS_A_KEYERROR = UpstreamRef(
    "KeyError",
    "VERIFIED",
    src(_MCH, 248),
    "the C++ swallows a per-controller exception, logs to stderr and inserts an empty map to "
    "keep the dict key, so Python sees no exception and then raises KeyError indexing it. "
    "There is no try/except anywhere in get_observation. It is a hardware fault, not a "
    "transient: retrying spins against a dead bus while the robot holds its last target.",
)
MOTOR_CUR_IS_MILLIAMPS = UpstreamRef(
    "motor currents in Amperes",
    "VERIFIED",
    src(_SIM, 23),
    "the docstring says Amperes and the hardware delivers raw Dynamixel counts, which the "
    "torque conversion itself treats as milliamps by dividing by a thousand. A current limit "
    "written in Amperes would never trip.",
)
IMU_QUATERNION_IS_SCALAR_FIRST = UpstreamRef(
    "scalar_first",
    "VERIFIED",
    src(_REAL, 155),
    "the rotation is built from a w-first quaternion. This also puts a real floor under "
    "scipy, which upstream does not declare: the argument did not exist before 1.14.0.",
)

# ── torque, and every way it goes away ──────────────────────────────────────────────────

PYBIND_MODULE = UpstreamRef(
    "PYBIND11_MODULE(dynamixel_cpp, m)",
    "VERIFIED",
    src(_MCH, 302),
    "eight names are bound and no more: create_controllers, scan_port, initialize, "
    "get_motor_states, get_motor_ids, set_motor_pos, disable_motors and close.",
)
ENABLE_MOTORS_IS_NOT_BOUND = UpstreamRef(
    "enable_motors",
    "VERIFIED",
    src(_CTRL, 184),
    "it exists in C++ and is not bound to Python at this pin, so there is no way back from a "
    "torque-off short of initialize, which also rewrites the control mode and every gain "
    "register and re-latches the zero positions. quackd therefore never disables torque: "
    "there would be no way to undo it.",
)
CLOSE_DISABLES_TORQUE = UpstreamRef(
    "set_torque_enabled",
    "VERIFIED",
    src(_CLIENT, 72),
    "close reaches disconnect reaches this, with false. The robot goes limp, with no "
    "lowering, no ramp and no settle. On a standing humanoid that is a fall.",
)
ATEXIT_DROPS_THE_ROBOT = UpstreamRef(
    "atexit(dynamixel_cleanup_handler)",
    "VERIFIED",
    src(_CLIENT, 31),
    "registered once from the client constructor; the handler walks every open client and "
    "disconnects it, which disables torque. So ANY normal interpreter exit drops a standing "
    "robot, including an unhandled exception. This is the most important fact about this "
    "machine, and it is why quackd's daemon reaches a safe pose before anything may exit.",
)
ATEXIT_DOES_NOT_RUN_ON_SIGTERM = UpstreamRef(
    "dynamixel_cleanup_handler",
    "VERIFIED",
    src(_CLIENT, 344),
    "C atexit does not run on SIGTERM, SIGKILL, a hard exit or a crash, and upstream installs "
    "no signal handler anywhere in its Python. So systemd stopping a bare upstream loop "
    "leaves the robot fully torqued, holding its last target, indefinitely.",
)
CLOSE_HOLDS_THE_GIL = UpstreamRef(
    "close",
    "VERIFIED",
    src(_MCH, 327),
    "bound without a gil_scoped_release call guard, unlike get_motor_states and set_motor_pos "
    "which have one. Torque-off also retries on an unresponsive bus, so a shutdown can block "
    "forever holding the GIL and freezing every other thread, including anything supervising "
    "it. quackd's daemon arms a hard-exit timer before it calls close.",
)
CLOSE_MOTORS_IS_PROCESS_GLOBAL = UpstreamRef(
    "close_motors",
    "VERIFIED",
    src(_CTRL, 167),
    "it iterates a process-global set of open clients rather than its own, and disconnect "
    "erases from that same set while the handler walks it. Closing one controller disconnects "
    "every controller in the process, and with two or more the erase during iteration can "
    "leave part of the robot torqued.",
)
CLOSE_IS_UNGUARDED = UpstreamRef(
    "self.imu.close()",
    "VERIFIED",
    src(_REAL, 208),
    "there is no try around it, so an exception closing the IMU skips the motor close on the "
    "next line entirely and leaves torque on.",
)

# ── constructing, which can hang ────────────────────────────────────────────────────────

INIT_ENABLES_TORQUE = UpstreamRef(
    "initialize",
    "VERIFIED",
    src(_REAL, 57),
    "the robot is live and torqued the moment construction returns. There is no separate "
    "enable step and no dry-run flag: the constructor takes only a Robot.",
)
INIT_REQUIRES_AN_IMU = UpstreamRef(
    "get_latest_state",
    "VERIFIED",
    src(_REAL, 71),
    "dereferenced with no None guard and outside the try that would have caught a missing "
    "IMU, so a robot built without one raises AttributeError here despite the optional-IMU "
    "handling twenty lines above.",
)
INIT_BUSY_WAITS_FOREVER = UpstreamRef(
    "while not imu_data",
    "VERIFIED",
    src(_REAL, 73),
    "no sleep, no timeout, no retry cap, and torque is already on by this point. A silent IMU "
    "hangs construction at full CPU with the motors live and no target established. quackd "
    "runs construction under its own watchdog because nothing inside the class can recover.",
)
INIT_SWALLOWS_BUS_FAILURE = UpstreamRef(
    "Dynamixel controller not found",
    "VERIFIED",
    src(_REAL, 68),
    "construction still succeeds with the controller list empty and the index arrays never "
    "assigned, so the failure surfaces later as an AttributeError inside set_motor_target. "
    "quackd asserts the invariants immediately after construction instead.",
)

# ── what a safe pose has to be built out of ─────────────────────────────────────────────

NO_RESET_EXISTS = UpstreamRef(
    "reset",
    "VERIFIED",
    src(_SIM, 65),
    "there is none anywhere in the sim package: no home, no safe pose, no e-stop and no way "
    "to reach a known state through the contract. quackd has to manufacture one.",
)
RESET_VEL = UpstreamRef(
    "reset_vel",
    "VERIFIED",
    src(_RESET, 63),
    "0.3 rad/s, upstream's own per-motor rate for moving to a rest pose. quackd's deadman "
    "slews at this rate rather than commanding a jump, because a position command on this "
    "body is a full-torque snap.",
)
RESET_UNTWISTS_THE_WAIST_FIRST = UpstreamRef(
    "ResetPDPolicy",
    "VERIFIED",
    src(_RESET, 91),
    "if any waist motor is more than half a radian from zero it drives only the waist to zero "
    "while everything else holds, and goes to the default pose afterwards. quackd's safe-pose "
    "slew follows the same two phases.",
)
CONTROL_DT = UpstreamRef(
    "control_dt",
    "VERIFIED",
    src(_POL, 66),
    "0.02 seconds, fifty hertz, and no shipped policy overrides it. The training config's own "
    "timestep and frame count multiply to the same figure, so running at another rate "
    "desynchronises the gait phase.",
)

# ── walking, which needs something nobody ships ─────────────────────────────────────────

WALK_NEEDS_A_WANDB_CHECKPOINT = UpstreamRef(
    "load_wandb_policy",
    "VERIFIED",
    src(_MJX, 27),
    "it looks for a local onnx checkpoint and otherwise pulls a wandb artifact. Nothing is "
    "checked into the repository and the README mentions no checkpoint, no wandb entity and "
    "no download at all. So on a bare install there is no walk policy, and quackd does not "
    "declare move, go_to or approach_and unless the daemon reports one staged."
    " Its annotation says Dict[str, Any] and it returns a str directory, ckpts/<name>, and its "
    "argument is a run name rather than a path."
    " quackd checks for model_best.onnx and env_config.json itself and refuses when either is "
    "absent, because a robot should not silently download the thing that decides how it walks.",
)
WALK_COMMAND_KEYS_ARE_ALL_OR_NOTHING = UpstreamRef(
    "control_inputs",
    "VERIFIED",
    src(_MJX, 143),
    "a plain attribute with no setter, and the walk policy indexes its three keys "
    "unconditionally, so a partial command raises KeyError mid-tick."
    " An empty dict is not a stop either: it falls back to the fixed command, so quackd always "
    "sends all three keys and sends explicit zeros to stand still.",
)
WALK_COMMAND_RANGE = UpstreamRef(
    "command_range",
    "VERIFIED",
    src(_MJX, 92),
    "the range actually enforced comes from the checkpoint's own config rather than from the "
    "gin file, so quackd reads it at connect rather than hardcoding it. The gin file at this "
    "pin says forward velocity minus 0.2 to plus 0.3, which is asymmetric."
    " It is shaped (num_commands, 2) and the walk velocities are rows 5, 6 and 7: the first five "
    "are upper-body pose commands, so reading rows 0 to 2 would clamp against the wrong thing "
    "entirely.",
)

# ── the sensors that exist, and the one that does not ───────────────────────────────────

CAMERA = UpstreamRef(
    "Camera",
    "VERIFIED",
    src(_CAM, 112),
    "get_frame, get_jpeg, detect_tags and close, constructed with a side. It is not part of "
    "the sim contract and no observation carries a frame, so the daemon owns it on its own "
    "thread and hands quackd the newest one.",
)
SPEAKER_CANNOT_SPEAK = UpstreamRef(
    "Speaker",
    "VERIFIED",
    src(_SPK, 13),
    "it imports re, subprocess and sounddevice and nothing else: it plays audio, it does not "
    "synthesise it. There is no text to speech at this pin, so quackd does not declare the "
    "sound intent and say does not exist on this robot.",
)
NO_BATTERY_IN_PYTHON = UpstreamRef(
    "read_vin",
    "VERIFIED",
    src(_CTRL, 184),
    "bus voltage is read in C++ during initialisation and only printed. Nothing exposes it to "
    "Python, so battery_percent is always None and a battery abort can never fire.",
)

# ── packaging ───────────────────────────────────────────────────────────────────────────

VERSION_DISAGREES_WITH_THE_TAG = UpstreamRef(
    "0.2.0",
    "VERIFIED",
    src(_PROJ, 7),
    "the tag says v2.0.0 and the package says 0.2.0. A version string is not a reliable way "
    "to tell which tree you have here; the commit hash is.",
)
UPSTREAM_CI_IS_DISABLED = UpstreamRef(
    "Skipping tests",
    "VERIFIED",
    src(_CI, 11),
    "the only step echoes a skip and exits zero, and the repository has one test file. "
    "Nothing at this pin was ever machine-verified upstream, so quackd assumes nothing works "
    "until it has run it itself.",
)
MUJOCO_IS_NOT_A_DIRECT_DEPENDENCY = UpstreamRef(
    "brax",
    "VERIFIED",
    src(_PROJ, 19),
    "mujoco is never declared and arrives transitively, unpinned, while numpy is hard-pinned. "
    "The daemon's install instructions pin mujoco explicitly rather than letting a resolver "
    "choose one.",
)
CONSTRUCTIBLE_ROBOTS = UpstreamRef(
    "Robot",
    "VERIFIED",
    src(_ROBOT, 71),
    "five names have a robot.yml: toddlerbot_2xc, toddlerbot_2xc_gripper, toddlerbot_2xm, "
    "toddlerbot_2xm_gripper and teleop_leader, with thirty, thirty-two, thirty, thirty-two "
    "and fourteen motors. The gripper motors are appended last and teleop_leader shifts its "
    "ids, so every index quackd uses is derived from motor_ordering at runtime.",
)
MOTOR_LIMITS_COME_FROM_THE_MJCF = UpstreamRef(
    "motor_limits",
    "VERIFIED",
    src(_ROBOT, 191),
    "parsed from the joint ranges in the fixed-base MJCF, never from YAML. This is the only "
    "source of a joint limit anywhere, and it is what quackd clamps against because nothing "
    "upstream does."
    " It is a Dict[str, List[float]] of [low, high] in radians rather than an array, so it has to "
    "be ordered through motor_ordering before it can clamp anything.",
)
CALIBRATION_IS_NOT_IN_THE_REPO = UpstreamRef(
    "motors.yml",
    "VERIFIED",
    src(_ROBOT, 118),
    "per-instance zero calibration, gitignored and absent from a fresh clone, so every zero "
    "falls back to nought. On hardware that means every commanded angle is offset by however "
    "the robot happened to be assembled. quackd's daemon refuses to actuate without it.",
)
MOTION_KEYFRAMES = UpstreamRef(
    "motion",
    "VERIFIED",
    f"{REPO}/tree/{PIN}/motion",
    "eighteen compressed keyframes, nine motions across the two variants: cartwheel, crawl, "
    "cuddle, hold, kneel, pull_up_grasp, pull_up_pull, push_up and walk_zmp. These are the "
    "only motion that ships in the repository and therefore the only motion that needs no "
    "download.",
)


# ── playing a motion, which upstream has no loader for ──────────────────────────────────

MOTION_FILES_ARE_PER_VARIANT = UpstreamRef(
    "robot_suffix",
    "VERIFIED",
    src(_PULLUP, 72),
    "there is no bare cuddle.lz4: every motion is written twice, once per variant, and the "
    'suffix is chosen at runtime as "_2xc" if "_2xc" in robot.name else "_2xm". The daemon '
    "picks the same way, from the robot name it was started with.",
)
MOTION_HAS_NO_LOADER = UpstreamRef(
    "joblib.load",
    "VERIFIED",
    src(_REPLAY, 42),
    "there is no load_motion() anywhere upstream: ReplayPolicy and every other reader calls "
    "joblib.load(path) inline on a full path, so quackd's daemon does the same. The files are "
    "lz4-framed pickles, so joblib and lz4 are both needed and pickle alone is not enough.",
)
MOTION_ACTION_ARRAY = UpstreamRef(
    "action",
    "VERIFIED",
    src(_REPLAY, 44),
    "the per-frame motor targets, shaped (frames, 30), float32, in radians, in "
    "robot.motor_ordering, at fifty hertz, which is this daemon's own rate. ReplayPolicy "
    "accepts either this schema or an obs_list/action_list one.",
)
MOTION_ORDERING_IS_THE_ROBOTS = UpstreamRef(
    "motor_ordering",
    "VERIFIED",
    src(_MIGRATE, 36),
    "dict(zip(robot.motor_ordering, motor_pos)) is what proves the frames are in the robot's "
    "own motor order rather than the file's, so quackd only ever sends a whole array.",
)
CARTWHEEL_CANNOT_BE_REPLAYED = UpstreamRef(
    "cartwheel",
    "VERIFIED",
    src(_REPLAY, 44),
    "its file carries action=None because it is qpos-interpolated and meant to run as its own "
    'RL policy. ReplayPolicy\'s own `"action" in data_dict` check passes and then fails on the '
    "shape. quackd does not offer it, and its loader skips any motion whose action is None.",
)
WALK_ZMP_IS_NOT_A_MOTION = UpstreamRef(
    "walk_zmp",
    "VERIFIED",
    src(_ZMPREF, 39),
    "it sits in the same directory with the same extension and is not a keyframe file at all: "
    "it is a two-tuple gait lookup table used at training time. Replaying it fails at once. "
    "quackd does not offer it, and walking is the ONNX policy's job.",
)

# ── the camera, which the daemon owns because no observation carries one ────────────────

CAMERA_TAKES_A_SIDE = UpstreamRef(
    "Camera.__init__",
    "VERIFIED",
    src(_CAM, 115),
    "def __init__(self, side, width=640, height=480), untyped. `side` is only ever compared "
    '== "left", so any other value silently means right. The constructor is synchronous and '
    "Linux only: it scans /dev, shells out to v4l2-ctl, and unpickles a calibration file by a "
    "relative path, so the working directory has to be the checkout root.",
)
CAMERA_GET_FRAME_IS_BGR = UpstreamRef(
    "Camera.get_frame",
    "VERIFIED",
    src(_CAM, 178),
    "returns OpenCV's own uint8 (height, width, 3) BGR array, and raises rather than "
    "returning None on a failed read. quackd reads it on its own thread for exactly that "
    "reason, and encodes this array, which is already the order imencode wants.",
)
CAMERA_GET_JPEG_SWAPS_RED_AND_BLUE = UpstreamRef(
    "Camera.get_jpeg",
    "VERIFIED",
    src(_CAM, 194),
    "it returns a (buffer, array) pair rather than bytes, and it hands an RGB array to "
    "cv2.imencode, which expects BGR, so its JPEG comes out with red and blue swapped. quackd "
    "does not call it: a swapped frame would quietly break every colour the detector looks "
    "for. Its docstring also says quality 90 while the code sets 50.",
)

# ── the walk policy, whose interface is not what a reader would guess ───────────────────

WALK_STEP_TAKES_THE_OBSERVATION = UpstreamRef(
    "WalkPolicy.step",
    "VERIFIED",
    src(_WALK, 73),
    "def step(self, obs: Obs, sim: BaseSim) -> Tuple[Dict[str, float], NDArray]: the whole "
    "observation and the sim, answering with (control_inputs, motor_target). The target is "
    "(30,) float32 radians in motor_ordering, already clipped to motor_limits. There is no "
    "method that takes only the current pose.",
)
WALK_INPUTS_ARE_NEVER_CLIPPED = UpstreamRef(
    "walk_x",
    "VERIFIED",
    src(_GIN, 1),
    "walk_x and walk_y are not clipped anywhere on the control_inputs path, so an "
    "out-of-envelope command reaches the network as out-of-distribution input. quackd clamps "
    "against the envelope it read at connect. walk_turn is separately overwritten by a yaw "
    "correction integrator and re-clipped, so the commanded turn rate is not passed through.",
)

WALK_POLICY_IS_STATEFUL = UpstreamRef(
    "WALK_POLICY_IS_STATEFUL",
    "UNVERIFIED",
    src(_MJX, 202),
    "the policy keeps an observation history and an action buffer, expects to be stepped at "
    "a fixed fifty hertz, and ignores its commands for the first seven seconds while it plays "
    "a prep trajectory. quackd's daemon steps it only while `move` is running, so that window "
    "opens on the first walk command rather than at startup. Whether a gait driven this way "
    "behaves like one driven continuously has not been tested on a robot.",
)


# ── the simulated body, which is what CI can actually drive ─────────────────────────────

MUJOCO_SIM = UpstreamRef(
    "MuJoCoSim",
    "VERIFIED",
    src(_MUJOCO, 18),
    "def __init__(self, robot, n_frames=20, dt=0.001, fixed_base=False, xml_path='', "
    "vis_type='', controller_type='torque'). It takes the Robot object rather than a name, "
    "and n_frames * dt is 0.02, so its control rate is the same fifty hertz the daemon runs. "
    "It fills joint_pos, lin_vel and pos, which the real robot always leaves None, and leaves "
    "motor_cur None, which the real robot fills.",
)
MUJOCO_IS_HEADLESS_BY_DEFAULT = UpstreamRef(
    "vis_type",
    "VERIFIED",
    src(_MUJOCO, 121),
    "only 'render' builds a renderer and only 'view' builds a viewer, so the default builds "
    "neither and no GL context is ever created. Upstream reads MUJOCO_GL nowhere, so setting "
    "it changes nothing on this path. What the contract job does need is that `import "
    "mujoco.viewer` succeeds, because mujoco_sim imports it at module scope before it looks "
    "at vis_type, and that import loads glfw's shared library.",
)
MUJOCO_POSITION_CONTROLLER_IS_BROKEN = UpstreamRef(
    "controller_type",
    "VERIFIED",
    src(_MOTOR, 114),
    "the sim calls the controller's step with four arguments and PositionController.step "
    "takes three, so anything but the default torque raises on the first tick. quackd never "
    "passes it.",
)
MUJOCO_PATHS_ARE_RELATIVE = UpstreamRef(
    "scene.xml",
    "VERIFIED",
    src(_MUJOCO, 64),
    "the model path is built as toddlerbot/descriptions/<robot>/scene.xml relative to the "
    "working directory, and Robot reads its own configs the same way with no override, so "
    "the daemon changes directory to the checkout root before constructing either. Robot "
    "also parses <robot>_fixed.xml unconditionally, for the motor ordering, even when the "
    "body is free.",
)

ROBOT_DEFAULT_POSE = UpstreamRef(
    "default_motor_angles",
    "VERIFIED",
    src(_ROBOT, 105),
    "a Dict[str, float] keyed by motor name, in motor_ordering order because it is built by "
    "iterating it, in radians, from each motor's home_pos in default.yml. It is NOT "
    "default_motor_pos, which is a real upstream name but lives on BasePolicy, and NOT "
    "default_joint_angles, which is a different space. Every upstream caller converts it the "
    "same way: np.array(list(robot.default_motor_angles.values())). quackd reads it with no "
    "fallback, because this is the pose the deadman slews to and zeros are not a neutral pose "
    "on this body: home carries plus or minus 1.57 rad of shoulder and elbow yaw and 1.22 of "
    "wrist, so a wrong name would mean a large wrong motion on every limb at the exact moment "
    "nobody is driving the robot.",
)
ROBOT_MOTOR_COUNT = UpstreamRef(
    "nu",
    "VERIFIED",
    src(_ROBOT, 88),
    "len(motor_ordering): thirty on the 2xc and 2xm, thirty-two on the gripper builds and "
    "fourteen on the teleop leader, so nothing may assume a count.",
)

# ── what quackd assumes ─────────────────────────────────────────────────────────────────

DAEMON_PROTOCOL = UpstreamRef(
    "DAEMON_PROTOCOL",
    "UNVERIFIED",
    src(_SIM, 65),
    "the JSON-RPC over TCP the daemon speaks is quackd's own, defined at both ends, so there "
    "is no upstream to verify it against and citing one would be a false citation. What this "
    "file carries instead is every assumption the daemon makes about the robot.",
)
SAFE_POSE = UpstreamRef(
    "SAFE_POSE",
    "UNVERIFIED",
    src(_RESET, 91),
    "quackd's safe pose is upstream's default pose, reached at upstream's own reset rate, "
    "with upstream's waist rule first. Whether that pose is safe to slew to from a walking, "
    "crawling or prone start is documented nowhere and must be tested on a stand.",
)
FALL_DETECTION = UpstreamRef(
    "FALL_DETECTION",
    "UNVERIFIED",
    src(_REAL, 155),
    "there is none upstream. quackd synthesises a fall from the gravity vector in the "
    "orientation past a tilt threshold, and that threshold is a guess until somebody tips a "
    "real robot.",
)
GRIPPER_AXES = UpstreamRef(
    "GRIPPER_AXES",
    "UNVERIFIED",
    src(_ROBOT, 195),
    "which end of a gripper motor's travel is closed. Upstream states it nowhere, so "
    "the daemon takes the low end of the MJCF joint range as closed and the high end as "
    "open, and finds the motors by name. If a real robot opens when quackd says close, "
    "this is the line that was wrong. It is also why the capability is only reported "
    "when a gripper motor is actually found, rather than when the flag was passed.",
)
NECK_AXES = UpstreamRef(
    "NECK_AXES",
    "UNVERIFIED",
    src(_ROBOT, 191),
    "which neck motor is yaw and which is pitch is inferred from the motor names rather than "
    "stated anywhere, and look clamps to a fraction of the MJCF range on that basis.",
)
ZERO_LATCHING = UpstreamRef(
    "ZERO_LATCHING",
    "UNVERIFIED",
    src(_CTRL, 150),
    "whether a calibrated zero file survives initialize, or is overwritten by a re-latch at "
    "every startup, was not settled by reading. quackd refuses to actuate without the file "
    "and says in its docs that this interaction is unresolved.",
)
THREAD_SAFETY = UpstreamRef(
    "THREAD_SAFETY",
    "UNVERIFIED",
    src(_MCH, 302),
    "two of the eight bindings release the GIL and six do not, and nothing documents whether "
    "the C++ is safe to call from more than one thread. The daemon serialises every call onto "
    "its control thread and keeps the camera on another.",
)


def all_refs() -> list[UpstreamRef]:
    return [v for v in globals().values() if isinstance(v, UpstreamRef)]


def refs_by_status(status: str) -> list[UpstreamRef]:
    return [r for r in all_refs() if r.status == status]
