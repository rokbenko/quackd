"""The only file in quackd allowed to spell an AlohaMini name (ADR-0027).

Every constant is tagged VERIFIED (read from upstream source, link given) or UNVERIFIED (an
assumption of ours, with what quackd does about it). `docs/adapters/alohamini/README.md` is the
human-readable version; `tests/test_upstream_api.py` proves UNVERIFIED names are only
reachable from the experimental `zmq` backend.

Source of truth: https://github.com/liyiteng/lerobot_alohamini at commit
ab4462b713aeb24d0473f1ec6c8812290ab19510 (main, 2026-07-24; read 2026-09-05). There are no
tags and no releases, so a commit hash is the only pin available. The hardware repository
https://github.com/liyiteng/AlohaMini (commit 17c6a98d, 2026-07-01) holds the CAD and the bill
of materials and no software quackd uses: its own README says lerobot_alohamini is the single
source of truth for software.

quackd never imports it. The project is a **fork of LeRobot that calls itself `lerobot`**, is
not published to PyPI, and installs only from a 133 MB git clone on Python 3.12 with torch. A
name collision with HuggingFace's own package is not a dependency quackd can express, so the
adapter speaks the ZeroMQ host protocol below and imports nothing.

Nothing here has been run against an AlohaMini.
"""

from __future__ import annotations

from quackd.upstream import UpstreamRef

REPO = "https://github.com/liyiteng/lerobot_alohamini"
PIN = "ab4462b713aeb24d0473f1ec6c8812290ab19510"  # main, 2026-07-24
READ_ON = "2026-09-05"

REPO_HW = "https://github.com/liyiteng/AlohaMini"
PIN_HW = "17c6a98d79881a45ab869c1f392ed89c0723a298"  # main, 2026-07-01


def src(path: str, line: int | None = None) -> str:
    return f"{REPO}/blob/{PIN}/{path}" + (f"#L{line}" if line else "")


def hw(path: str, line: int | None = None) -> str:
    return f"{REPO_HW}/blob/{PIN_HW}/{path}" + (f"#L{line}" if line else "")


_ROOT = "src/lerobot/robots/alohamini"
_ROBOT = f"{_ROOT}/alohamini.py"
_CONFIG = f"{_ROOT}/config_alohamini.py"
_HOST = f"{_ROOT}/alohamini_host.py"
_CLIENT = f"{_ROOT}/alohamini_client.py"
_LIFT = f"{_ROOT}/lift_axis.py"
_SPECS = f"{_ROOT}/model_specs.py"

LICENCE = "Apache-2.0"

# ── the ZeroMQ host protocol ─────────────────────────────────────────────────────────────

PORT_ZMQ_CMD = UpstreamRef(
    "5555",
    "VERIFIED",
    src(_CONFIG, 76),
    "AlohaMiniHostConfig.port_zmq_cmd. The host BINDS zmq.PULL here, so quackd CONNECTS PUSH.",
)
PORT_ZMQ_OBSERVATIONS = UpstreamRef(
    "5556",
    "VERIFIED",
    src(_CONFIG, 77),
    "AlohaMiniHostConfig.port_zmq_observations. The host BINDS zmq.ROUTER here, so quackd "
    "CONNECTS DEALER. This is request/reply, not a stream: nothing arrives unasked.",
)
CMD_SOCKET_CONFLATE = UpstreamRef(
    "zmq.CONFLATE = 1",
    "VERIFIED",
    src(_HOST, 33),
    "on the command socket only. Only the newest command survives, so quackd merges every "
    "pending intent into one action and sends exactly once per tick.",
)
OBSERVATION_REQUEST_WINDOW = UpstreamRef(
    "3",
    "VERIFIED",
    src(_CONFIG, 78),
    "SNDHWM and RCVHWM on the ROUTER (alohamini_host.py:40-41). The host consumes at most one "
    "request credit per loop, so a client may have a few requests in flight.",
)
OBSERVATION_REQUEST_REPLY = UpstreamRef(
    "recv_multipart / send_multipart",
    "VERIFIED",
    src(_HOST, 205),
    "the client sends a token, the host replies [identity, token, *observation_parts] "
    "(line 217). DEALER strips the identity, so quackd sees [token, state_json, ...].",
)
OBSERVATION_MULTIPART = UpstreamRef(
    "build_observation_multipart",
    "VERIFIED",
    src(_HOST, 64),
    "parts[0] is the state as JSON; each camera then adds two frames, its name and its JPEG "
    "bytes (line 82).",
)
IMAGE_ENCODING = UpstreamRef(
    '"_image_encoding": "jpeg"',
    "VERIFIED",
    src(_HOST, 69),
    "always jpeg at this pin. A legacy base64-in-JSON path still exists in upstream's client, "
    "so quackd refuses any other value rather than guessing.",
)
IMAGES_LIST = UpstreamRef(
    '"_images"',
    "VERIFIED",
    src(_HOST, 84),
    "the host names the cameras it actually sent, and rewrites parts[0] afterwards (line 85) "
    "so the list really is in the JSON. Upstream's own client discards it; quackd reads it, "
    "which is how the manifest describes itself instead of trusting a config it cannot see.",
)
CAMERA_JPEG_QUALITY = UpstreamRef(
    "cv2.IMWRITE_JPEG_QUALITY, 70",
    "VERIFIED",
    src(_HOST, 77),
    "fixed, with no config knob: quackd never sees a raw sensor frame. A camera that fails to "
    "encode is skipped entirely rather than sent empty (line 80).",
)
WATCHDOG_TIMEOUT_MS = UpstreamRef(
    "1000",
    "VERIFIED",
    src(_CONFIG, 84),
    "AlohaMiniHostConfig.watchdog_timeout_ms.",
)
WATCHDOG_ACTION = UpstreamRef(
    "robot.stop_motion()",
    "VERIFIED",
    src(_HOST, 193),
    "what the host does on silence. stop_motion is the base and the lift, and NOT the arms, so "
    "this deadman covers two of the three subsystems.",
)
CONNECTION_TIME_S = UpstreamRef(
    "6000",
    "VERIFIED",
    src(_CONFIG, 81),
    "the host's own lifetime; `while duration < host.connection_time_s` at alohamini_host.py:"
    "169, then it disconnects the robot and exits. Roughly an hour and forty minutes.",
)
MAX_LOOP_FREQ_HZ = UpstreamRef(
    "30",
    "VERIFIED",
    src(_CONFIG, 87),
    "single-threaded: one command, one observation and at most one reply per cycle.",
)
CLIENT_POLLING_TIMEOUT_MS = UpstreamRef(
    "200",
    "VERIFIED",
    src(_CONFIG, 130),
    "upstream's own client poll window, which must exceed one host cycle at 30 Hz.",
)
CLIENT_CONNECT_TIMEOUT_S = UpstreamRef(
    "5",
    "VERIFIED",
    src(_CONFIG, 131),
    "there is no hello on this wire, so a handshake is the first observation coming back.",
)

# ── the action and observation contract ──────────────────────────────────────────────────

STATE_FEATURES = UpstreamRef(
    "AlohaMini._state_ft",
    "VERIFIED",
    src(_ROBOT, 222),
    "both arms' joint keys, then x.vel, y.vel, theta.vel and lift_axis.height_mm.",
)
LIFT_VEL_IS_NOT_A_STATE_KEY = UpstreamRef(
    '#"lift_axis.vel"',
    "VERIFIED",
    src(_ROBOT, 231),
    "commented out of _state_ft, so it can never appear in a recorded dataset and upstream's "
    "own action space does not carry it. quackd injects it anyway, because it is the only "
    "thing that stops the lift.",
)
ARM_KEY_PREFIXES = UpstreamRef(
    'startswith("arm_left_")',
    "VERIFIED",
    src(_ROBOT, 702),
    "and arm_right_ on line 703, both with a .pos suffix, and both requiring the motor to "
    "exist on that bus.",
)
BASE_VEL_KEYS_ARE_MANDATORY = UpstreamRef(
    'base_goal_vel["x.vel"]',
    "VERIFIED",
    src(_ROBOT, 708),
    "indexed directly, with no .get() and no default, on lines 709-710 for y and theta too. "
    "Omitting one raises KeyError BEFORE the lift is touched (line 721) and before any bus "
    "write, so the ENTIRE action is discarded, arms included, and last_cmd_time is never "
    "refreshed. Every payload quackd sends carries all three.",
)
BASE_IS_ALWAYS_WRITTEN = UpstreamRef(
    'self.left_bus.sync_write("Goal_Velocity", base_wheel_goal_vel)',
    "VERIFIED",
    src(_ROBOT, 761),
    "unconditional, so every accepted action commands the wheels. An arms-only action would "
    "still stop the base, which is why quackd zeroes velocity explicitly on an arm command "
    "rather than letting it happen by accident.",
)
ARM_PROFILE_JOINTS = UpstreamRef(
    "ARM_PROFILE_JOINTS",
    "VERIFIED",
    src(_SPECS, 15),
    "so-arm-5dof has six joints; the three 6dof profiles have seven, inserting wrist_yaw "
    "between wrist_flex and wrist_roll.",
)
ROBOT_SPECS = UpstreamRef(
    "ROBOT_SPECS",
    "VERIFIED",
    src(_SPECS, 56),
    "alohamini1 is so-arm-5dof with a 84 mm/rev lead and 0.05 m wheels; alohamini2 and "
    "alohamini2pro are 6dof with 131 mm/rev and 0.063 m wheels.",
)
ARM_STATE_KEYS = UpstreamRef(
    'f"{prefix}_{joint}.pos"',
    "VERIFIED",
    src(_SPECS, 100),
    "with prefixes arm_left and arm_right (line 105), giving keys like arm_left_gripper.pos.",
)
ROBOT_MODEL_DEFAULTS_DISAGREE = UpstreamRef(
    'robot_model: str = "alohamini2"',
    "VERIFIED",
    src(_CONFIG, 54),
    "the host defaults to alohamini2 while the client defaults to alohamini1 (line 105), and "
    "nothing cross-checks them. quackd derives the model from the observed key set instead of "
    "from any config, so a mismatch is impossible rather than silent.",
)

# ── the lift, which is where a stop is won or lost ───────────────────────────────────────

LIFT_APPLY_ACTION_HAS_NO_ELSE = UpstreamRef(
    "LiftAxis.apply_action",
    "VERIFIED",
    src(_LIFT, 157),
    "two independent `if key in action:` blocks (lines 167 and 193), no else and no "
    "unconditional write. An action carrying neither lift key writes nothing at all, so the "
    "servo keeps its last Goal_Velocity and KEEPS TRAVELLING, while the command itself "
    "refreshes the watchdog that would have stopped it.",
)
LIFT_HOME_LEAVES_FULL_SPEED_IN_THE_REGISTER = UpstreamRef(
    '#self._bus.write("Goal_Velocity", name, 0)',
    "VERIFIED",
    src(_LIFT, 134),
    "commented out. home() drives down at home_down_speed (line 109, value 1300 at line 32) "
    "and then disables torque (line 135) instead of zeroing the register. connect() calls it "
    "on every calibrated robot (alohamini.py:278), so the first command quackd sends after "
    "connecting is a stop.",
)
LIFT_STOP = UpstreamRef(
    "LiftAxis.stop",
    "VERIFIED",
    src(_LIFT, 218),
    "writes Goal_Velocity 0. Reached from AlohaMini.stop_lift (alohamini.py:898).",
)
LIFT_VEL_REACHES_THE_BUS = UpstreamRef(
    'self._bus.write("Goal_Velocity", self.cfg.name, v * self.cfg.dir_sign)',
    "VERIFIED",
    src(_LIFT, 212),
    "outside the try that wraps get_height_mm (lines 198-211), so a zero still reaches the bus "
    "even when the position read fails. Zero also passes both guards untouched, because the "
    "descent floor tests v < 0 and the soft limits test v > 0 or v < 0.",
)
LIFT_VEL_IS_CAST_BEFORE_THE_TRY = UpstreamRef(
    "v = int(action[key_v])",
    "VERIFIED",
    src(_LIFT, 195),
    "so None, a string or a NaN raises out of send_action, is swallowed by the host's handler, "
    "and the stop silently does nothing for a full watchdog window. quackd sends ints.",
)
LIFT_LIMITS = UpstreamRef(
    "soft_min_mm 0.0, soft_max_mm 600, descent_floor_mm 5.0",
    "VERIFIED",
    src(_LIFT, 27),
    "the floor is 5 mm, not 0, and it is a real refusal rather than a clamp to zero.",
)
LIFT_CONTROL_GAINS = UpstreamRef(
    "kp_vel 300, v_max 1300, on_target_mm 1.0",
    "VERIFIED",
    src(_LIFT, 37),
    "one proportional step per received command, so the command rate is the control rate: at "
    "2 Hz the lift would move in coarse bursts between corrections. quackd runs it at 10.",
)
LIFT_CONFIGURE_IS_COMMENTED_OUT = UpstreamRef(
    "#self.lift.configure()",
    "VERIFIED",
    src(_ROBOT, 461),
    "so the lift's VELOCITY operating mode is set only inside home(), which runs only when the "
    "robot is calibrated (alohamini.py:277-281). On an uncalibrated robot a Goal_Velocity "
    "write has undefined meaning, which is why quackd makes calibration a precondition.",
)

# ── stopping, and what it does not cover ─────────────────────────────────────────────────

STOP_BASE_IS_NOT_RETRIED = UpstreamRef(
    "num_retry=0",
    "VERIFIED",
    src(_ROBOT, 895),
    "the emergency wheel write is not retried if the serial packet is lost, and the base and "
    "the lift share the left bus. quackd sends its stop more than once for that reason.",
)
STOP_MOTION_SKIPS_THE_ARMS = UpstreamRef(
    "AlohaMini.stop_motion",
    "VERIFIED",
    src(_ROBOT, 902),
    "stop_base() plus stop_lift() and nothing else. The arms are never commanded by a stop.",
)
DISCONNECT_GOES_LIMP = UpstreamRef(
    "AlohaMini.disconnect",
    "VERIFIED",
    src(_ROBOT, 959),
    "stop_motion(), then bus.disconnect(disable_torque_on_disconnect) with that flag "
    "defaulting to True (config_alohamini.py:48), so a loaded arm falls. quackd never sends "
    "it: stop means stop, not collapse.",
)
ARM_TORQUE_IS_NEVER_ENABLED = UpstreamRef(
    "#self.left_bus.enable_torque()",
    "VERIFIED",
    src(_ROBOT, 449),
    "and the right arm's on line 459, both commented out, while configure() disables torque on "
    "both buses (lines 436 and 452). Nothing else in the driver enables it: the only "
    "Torque_Enable write in the package is a zero in lift_axis.home(), and neither "
    "configure_motors nor write_calibration touches torque. As shipped the arms are limp, so "
    "quackd cannot honour a hold through a stock host and does not pretend to.",
)
OVERCURRENT_TRIP_EXITS_THE_PROCESS = UpstreamRef(
    "sys.exit(1)",
    "VERIFIED",
    src(_ROBOT, 953),
    "after 20 consecutive over-limit reads on any motor (_overcurrent_trip_n at line 189) the "
    "host stops, disconnects and kills itself. quackd treats the host vanishing as an expected "
    "safety outcome, not a crash to retry through.",
)
CURRENT_LIMITS = UpstreamRef(
    "gripper 500 mA, joint 1800 mA, global 2000 mA",
    "VERIFIED",
    src(_ROBOT, 191),
    "the joint limit is line 215 and the global check runs on every observation with "
    "limit_ma=2000 (line 660). Raw counts scale by 6.5 to mA (line 908).",
)
MAX_RELATIVE_TARGET_DEFAULT = UpstreamRef(
    "max_relative_target: int | None = None",
    "VERIFIED",
    src(_CONFIG, 59),
    "no per-step arm clamp unless the owner sets one, and it never applies to the base or the "
    "lift. quackd validates every goal against the manifest before sending.",
)

# ── units ────────────────────────────────────────────────────────────────────────────────

THETA_VEL_IS_DEGPS = UpstreamRef(
    "theta_cmd  : Rotational velocity (deg/s)",
    "VERIFIED",
    src(_ROBOT, 506),
    "quackd's wz is rad/s, so the adapter multiplies by 180/pi outbound and by pi/180 inbound. "
    "A pass-through would be a 57x error.",
)
BASE_KINEMATICS_NEGATE_X_AND_Y = UpstreamRef(
    "velocity_vector = np.array([-x, -y, theta_rad])",
    "VERIFIED",
    src(_ROBOT, 527),
    "and the feedback path negates them back (lines 606-607), so command and observation agree "
    "with each other. Whether either agrees with physical forward is a wiring question no "
    "source read can settle: see BASE_SIGN_CONVENTION.",
)
USE_DEGREES_DEFAULT = UpstreamRef(
    "use_degrees: bool = False",
    "VERIFIED",
    src(_CONFIG, 64),
    "so arm joints are normalised -100..100 and grippers 0..100, NOT degrees.",
)
SPEED_LEVELS = UpstreamRef(
    '[{"xy": 0.15, "theta": 45}, {"xy": 0.2, "theta": 60}, {"xy": 0.25, "theta": 75}]',
    "VERIFIED",
    src(_CLIENT, 85),
    "upstream's own three teleop tiers, starting at slow (line 90). quackd takes 0.25 m/s and "
    "75 deg/s as the ceiling.",
)
CAMERAS_CONFIG = UpstreamRef(
    "alohamini_cameras_config",
    "VERIFIED",
    src(_CONFIG, 23),
    "five positions declared, of which only forward (line 25) and wrist_right (line 37) are "
    "uncommented. Enabling another means editing Python on the robot and restarting the host.",
)

# ── what quackd assumes ──────────────────────────────────────────────────────────────────

BASE_SIGN_CONVENTION = UpstreamRef(
    "BASE_SIGN_CONVENTION",
    "UNVERIFIED",
    src(_ROBOT, 527),
    "whether a positive x.vel drives the robot forward in quackd's sense depends on wheel "
    "mounting and motor polarity, which no source read can settle. quackd sends +x for forward "
    "and says so; the bring-up step is to drive +x briefly and watch which way it goes.",
)
CAMERA_COLOUR_ORDER = UpstreamRef(
    "CAMERA_COLOUR_ORDER",
    "UNVERIFIED",
    src(_HOST, 77),
    "the chain swaps twice: the camera config defaults to RGB, cv2.imencode treats its input "
    "as BGR, and upstream's client decodes with IMREAD_COLOR back to BGR. Net, the bytes most "
    "likely hold RGB in stored order, so quackd decodes with Pillow and does NOT swap. One "
    "photograph of a red object retires this.",
)
LIFT_TRAVEL_SPEED = UpstreamRef(
    "LIFT_TRAVEL_SPEED",
    "UNVERIFIED",
    src(_LIFT, 38),
    "v_max is 1300 raw velocity units and nothing states what that is in mm/s, so the lift "
    "verb's duration estimate is a guess. quackd gates the verb behind a confirmation and "
    "gives it a generous timeout rather than pretending to know.",
)
USE_DEGREES_IS_INVISIBLE_TO_A_CLIENT = UpstreamRef(
    "USE_DEGREES_IS_INVISIBLE_TO_A_CLIENT",
    "UNVERIFIED",
    src(_CONFIG, 64),
    "use_degrees is host-side config and nothing on the wire reports it, so a host built with "
    "degrees would make every joint number mean something else. quackd declares normalised "
    "units in the manifest and warns when an observed joint value falls outside -100..100.",
)
ARM_TORQUE_NEEDS_THE_QUACKD_HOST = UpstreamRef(
    "ARM_TORQUE_NEEDS_THE_QUACKD_HOST",
    "UNVERIFIED",
    src(_ROBOT, 449),
    "quackd's own host wrapper enables arm torque and advertises itself in the state JSON. "
    "That the wrapper is running is something quackd asserts from a field it puts there "
    "itself, not something upstream reports, and the arm verbs are unavailable without it.",
)
OBSERVATION_STALENESS = UpstreamRef(
    "OBSERVATION_STALENESS",
    "UNVERIFIED",
    src(_CLIENT, 386),
    "nothing on the wire is timestamped, and upstream's client returns its cached last_frames "
    "and last_remote_state on a timeout, where last_remote_state starts as {} so the very "
    "first miss yields a dict with no state keys at all. quackd stamps on arrival and refuses "
    "to surface a reading whose sequence did not advance.",
)
THREAD_SAFETY = UpstreamRef(
    "THREAD_SAFETY",
    "UNVERIFIED",
    src(_HOST, 101),
    "the host loop is single-threaded and nothing documents the sockets' thread safety, so "
    "quackd serialises every send and receive under one lock in a worker thread.",
)
BATTERY = UpstreamRef(
    "BATTERY",
    "UNVERIFIED",
    src(_ROBOT, 222),
    "no battery, voltage or temperature appears in the state features, so battery_percent is "
    "None and the manifest declares no battery sensor. A battery abort can never fire here.",
)
ODOMETRY = UpstreamRef(
    "ODOMETRY",
    "UNVERIFIED",
    src(_ROBOT, 222),
    "the state carries x.vel, y.vel and theta.vel and no pose at all, so go_to closes the loop "
    "on the camera alone and a lost target has no dead-reckoned fallback.",
)


def all_refs() -> list[UpstreamRef]:
    return [v for v in globals().values() if isinstance(v, UpstreamRef)]


def refs_by_status(status: str) -> list[UpstreamRef]:
    return [r for r in all_refs() if r.status == status]
