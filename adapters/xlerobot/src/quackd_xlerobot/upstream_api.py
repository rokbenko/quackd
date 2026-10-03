"""The only file in quackd allowed to spell an XLeRobot name (ADR-0022).

Every constant is tagged VERIFIED (read from upstream source, link given) or UNVERIFIED (an
assumption of ours, with what quackd does about it). `docs/adapters/xlerobot/README.md` is the
human-readable version; `tests/test_upstream_api.py` proves UNVERIFIED names are only
reachable from the experimental `zmq` backend.

Source of truth: https://github.com/Vector-Wangel/XLeRobot at commit
3d14695e40c9c68229c0aacffca6053c75cd3eb6 (main, 2026-07-22; read 2026-09-04). The repository
has **zero git tags and zero releases**, so a commit hash is the only honest pin there is.

quackd never imports XLeRobot. It is not a package: no PyPI entry, no `pyproject.toml`, no
`setup.py`, and it is absent from upstream `huggingface/lerobot`. Its documented install is
copying files into an existing lerobot source tree, which is not a dependency quackd can
express. So the adapter speaks the ZeroMQ host protocol below and imports nothing.

Note the `/xlerobot/` path segment in every link. A sibling `software/src/robots/
xlerobot_mecanum/` holds files with the same names and different content at the same line
numbers, so a citation that drops the segment names the wrong robot.

Nothing here has been run against an XLeRobot.
"""

from __future__ import annotations

from quackd.upstream import UpstreamRef

REPO = "https://github.com/Vector-Wangel/XLeRobot"
PIN = "3d14695e40c9c68229c0aacffca6053c75cd3eb6"  # main, 2026-07-22
READ_ON = "2026-09-04"


def src(path: str, line: int | None = None) -> str:
    return f"{REPO}/blob/{PIN}/{path}" + (f"#L{line}" if line else "")


_ROOT = "software/src/robots/xlerobot"
_ROBOT = f"{_ROOT}/xlerobot.py"
_CONFIG = f"{_ROOT}/config_xlerobot.py"
_HOST = f"{_ROOT}/xlerobot_host.py"
_CLIENT = f"{_ROOT}/xlerobot_client.py"
_INIT = f"{_ROOT}/__init__.py"

LICENCE = "Apache-2.0"

# ── the ZeroMQ host protocol: the whole integration surface ──────────────────────────────

PORT_ZMQ_CMD = UpstreamRef(
    "5555",
    "VERIFIED",
    src(_CONFIG, 90),
    "XLerobotHostConfig.port_zmq_cmd. The host BINDS zmq.PULL here, so quackd CONNECTS PUSH.",
)
PORT_ZMQ_OBSERVATIONS = UpstreamRef(
    "5556",
    "VERIFIED",
    src(_CONFIG, 91),
    "XLerobotHostConfig.port_zmq_observations. The host BINDS zmq.PUSH here, so quackd "
    "CONNECTS PULL.",
)
SOCKET_CONFLATE = UpstreamRef(
    "zmq.CONFLATE = 1",
    "VERIFIED",
    src(_HOST, 33),
    "set on both sockets (also line 37). Only the newest message survives, so two sends in "
    "one tick silently lose the first: quackd merges every pending intent into one action "
    "and sends exactly once per tick.",
)
WIRE_FORMAT = UpstreamRef(
    "json.dumps / recv_string",
    "VERIFIED",
    src(_HOST, 72),
    "one JSON object per message, as a string (also line 105). Not msgpack, not protobuf.",
)
MAX_LOOP_FREQ_HZ = UpstreamRef(
    "30",
    "VERIFIED",
    src(_CONFIG, 100),
    "XLerobotHostConfig.max_loop_freq_hz; the host publishes one observation per cycle.",
)
WATCHDOG_TIMEOUT_MS = UpstreamRef(
    "500",
    "VERIFIED",
    src(_CONFIG, 97),
    "XLerobotHostConfig.watchdog_timeout_ms. This is the robot's only safety authority.",
)
WATCHDOG_ACTION = UpstreamRef(
    "robot.stop_base()",
    "VERIFIED",
    src(_HOST, 89),
    "what the host does on silence. It zeroes the three wheels and NOTHING else: the 14 arm "
    "and head servos keep holding their last goal under torque.",
)
CONNECTION_TIME_S = UpstreamRef(
    "3600",
    "VERIFIED",
    src(_CONFIG, 94),
    "the host's own lifetime; `while duration < connection_time_s` at xlerobot_host.py:69, "
    "then it disconnects the robot and exits. There is no systemd unit or supervisor in the "
    "repo, so after an hour the socket simply stops answering.",
)
OBSERVATION_DROPPED_WHEN_NO_CLIENT = UpstreamRef(
    "zmq.Again",
    "VERIFIED",
    src(_HOST, 106),
    "the host sends observations NOBLOCK and drops them when nobody is attached; a late "
    "client is not owed the backlog.",
)
HOST_AND_CLIENT_COMMENTED_OUT = UpstreamRef(
    "# from .xlerobot_host import XLerobotHost",
    "VERIFIED",
    src(_INIT, 4),
    "both XLerobotHost and XLerobotClient (line 3) are commented out of the package's "
    "__init__; the robot's owner must uncomment them before the host can be started.",
)

# ── the action and observation contract ──────────────────────────────────────────────────

STATE_FEATURES = UpstreamRef(
    "XLerobot._state_ft",
    "VERIFIED",
    src(_ROBOT, 131),
    "the 17 flat dotted keys (lines 134-150): twelve <side>_arm_<joint>.pos, two "
    "head_motor_N.pos, and x.vel / y.vel / theta.vel. Spelled out because quackd refuses "
    "any joint name it does not recognise and a typo here would refuse every "
    "`move_joints`: side is left or right, and joint is shoulder_pan, shoulder_lift, "
    "elbow_flex, wrist_flex, wrist_roll or gripper.",
)
ACTION_FEATURES = UpstreamRef(
    "XLerobot.action_features",
    "VERIFIED",
    src(_ROBOT, 166),
    "returns _state_ft unchanged, so the action space and the state space are the same keys.",
)
OBSERVATION_FEATURES = UpstreamRef(
    "XLerobot.observation_features",
    "VERIFIED",
    src(_ROBOT, 162),
    "{**_state_ft, **_cameras_ft}: the 17 floats plus one key per configured camera.",
)
SEND_ACTION_PARTIAL = UpstreamRef(
    "XLerobot.send_action",
    "VERIFIED",
    src(_ROBOT, 567),
    "filters by prefix and suffix (lines 583-586) and guards each bus write with `if <dict>:` "
    "(lines 619-626), so a partial action is supported by design and a three-key velocity "
    "push touches no arm joint. quackd sends a whole desired action anyway, not because "
    "the robot needs it but because the command socket is CONFLATE: two intents in one "
    "tick collapse into the newest, so the newest has to carry both.",
)
SEND_ACTION_ALWAYS_WRITES_BASE = UpstreamRef(
    "_body_to_wheel_raw(...)",
    "VERIFIED",
    src(_ROBOT, 587),
    "called unconditionally with .get(key, 0.0) defaults and always returning three wheels "
    "(lines 436-440), so `if base_wheel_goal_vel:` at line 625 is always true. Every action "
    "without velocity keys therefore commands zero base velocity: an arms-only command halts "
    "a driving base. quackd re-sends velocity while `move` runs.",
)
SEND_ACTION_NEVER_REFUSES = UpstreamRef(
    "return {**left_arm_pos, ...}",
    "VERIFIED",
    src(_ROBOT, 627),
    "send_action returns the possibly-clipped action and has no refusal channel, so quackd "
    "synthesises its Ack: accepted when the command was written, refused only for what it "
    "can actually detect (not connected, no observation yet, unknown joint, out of range).",
)
GET_OBSERVATION = UpstreamRef(
    "XLerobot.get_observation",
    "VERIFIED",
    src(_ROBOT, 526),
    "four sync_read round-trips plus one async_read per camera; base velocities are decoded "
    "from wheel Present_Velocity through _wheel_raw_to_body.",
)
STOP_BASE = UpstreamRef(
    "XLerobot.stop_base",
    "VERIFIED",
    src(_ROBOT, 634),
    'sync_write("Goal_Velocity", {each base motor: 0}, num_retry=5). Wheels only.',
)
DISCONNECT_GOES_LIMP = UpstreamRef(
    "XLerobot.disconnect",
    "VERIFIED",
    src(_ROBOT, 638),
    "stop_base(), then bus.disconnect(disable_torque_on_disconnect) with that flag defaulting "
    "to True (config_xlerobot.py:56), so the arms go limp and drop what they hold. quackd "
    "never sends this: stop means stop, not collapse.",
)
CONNECT_BLOCKS_ON_INPUT = UpstreamRef(
    "input(...)",
    "VERIFIED",
    src(_ROBOT, 186),
    "XLerobot.connect() blocks on a bare input() whenever a calibration file exists, and the "
    "`calibrate` argument does not gate it. One of the reasons quackd talks to the host "
    "rather than driving the buses itself.",
)

# ── units, and the two places they are easy to get wrong ─────────────────────────────────

THETA_VEL_IS_DEGPS = UpstreamRef(
    "theta_cmd : Rotational velocity (deg/s)",
    "VERIFIED",
    src(_ROBOT, 392),
    "and the body converts with `theta_rad = theta * (np.pi / 180.0)` at line 408. quackd's "
    "wz is rad/s, so the adapter multiplies by 180/pi outbound and by pi/180 inbound. A "
    "pass-through would be a 57x error.",
)
WHEEL_RAW_TO_BODY_DOCSTRING_IS_WRONG = UpstreamRef(
    "A dict (x.vel, y.vel, theta.vel) all in m/s",
    "VERIFIED",
    src(_ROBOT, 459),
    "the _wheel_raw_to_body docstring is wrong. Line 484 converts theta back to degrees and "
    "the trailing comment at line 489 says `# m/s and deg/s`, which is the truth. Observed "
    "theta.vel is deg/s.",
)
USE_DEGREES_DEFAULT = UpstreamRef(
    "use_degrees: bool = False",
    "VERIFIED",
    src(_CONFIG, 66),
    "so arm and head .pos are MotorNormMode.RANGE_M100_100 (xlerobot.py:62), a normalised "
    "-100..100, NOT degrees. quackd declares joint_norm rather than lerobot's joint_deg.",
)
GRIPPER_NORM_MODE = UpstreamRef(
    "MotorNormMode.RANGE_0_100",
    "VERIFIED",
    src(_ROBOT, 86),
    "both grippers are 0..100 regardless of use_degrees (also line 116 for the right arm).",
)
SPEED_LEVELS = UpstreamRef(
    "[{xy: 0.1, theta: 30}, {xy: 0.2, theta: 60}, {xy: 0.3, theta: 90}]",
    "VERIFIED",
    src(_ROBOT, 56),
    "upstream's own three teleop tiers, with speed_index starting at slow (line 61). quackd "
    "takes 0.3 m/s and 90 deg/s as the ceiling and opens at the slow tier.",
)
BASE_MAX_RAW = UpstreamRef(
    "max_raw: int = 3000",
    "VERIFIED",
    src(_ROBOT, 384),
    "per-wheel tick cap; if any wheel exceeds it all three scale down proportionally (lines "
    "429-431), so asking for too much yields a slower version of the same direction rather "
    "than a clamp on one axis.",
)
MAX_RELATIVE_TARGET_DEFAULT = UpstreamRef(
    "max_relative_target: int | None = None",
    "VERIFIED",
    src(_CONFIG, 61),
    "so there is no per-step clamp on the arms at all unless the owner sets one. quackd "
    "validates every joint goal against the manifest before sending.",
)

# ── cameras ──────────────────────────────────────────────────────────────────────────────

CAMERAS_CONFIG_IS_EMPTY = UpstreamRef(
    "xlerobot_cameras_config",
    "VERIFIED",
    src(_CONFIG, 24),
    "every entry is commented out (lines 26-46), so the function returns {} and a stock "
    "XLeRobot is blind: the observation carries no image keys at all.",
)
CAMERA_DEVICE_CLASH = UpstreamRef(
    "/dev/video2",
    "VERIFIED",
    src(_CONFIG, 31),
    "the commented right_wrist and head(RGDB) entries both name /dev/video2 (also line 35), "
    "so an owner enabling cameras has to edit the config regardless.",
)
CAMERA_JPEG_QUALITY = UpstreamRef(
    "cv2.IMWRITE_JPEG_QUALITY, 90",
    "VERIFIED",
    src(_HOST, 96),
    "the host replaces each camera ndarray with a base64 JPEG string in the same JSON object "
    "(line 99), so quackd never sees a raw sensor frame.",
)
CAMERA_ENCODE_FAILURE_IS_EMPTY_STRING = UpstreamRef(
    'last_observation[cam_key] = ""',
    "VERIFIED",
    src(_HOST, 101),
    "on a failed encode the key stays present with an empty string, so a camera can be "
    "advertised and still yield no frame. quackd reports the camera and returns None.",
)
CAMERA_KEY_SHAPE = UpstreamRef(
    "CAMERA_KEY_SHAPE",
    "UNVERIFIED",
    src(_HOST, 94),
    "quackd identifies a camera as any observation key whose value is a string, because the "
    "17 state keys are all floats and the host writes cameras as base64 strings. Upstream "
    "publishes no schema and no camera list, so this is a heuristic; the chosen key is "
    "recorded in the manifest's extras.",
)
CAMERA_COLOR_ORDER = UpstreamRef(
    "CAMERA_COLOR_ORDER",
    "UNVERIFIED",
    src(_HOST, 95),
    "cv2.imencode treats its input as BGR, and the RealSense entry in the commented-out camera "
    "config asks for ColorMode.BGR, so quackd assumes BGR and swaps. One photograph of a red "
    "object retires this.",
)
CAMERA_FOV_DEG = UpstreamRef(
    "CAMERA_FOV_DEG",
    "UNVERIFIED",
    src(_CONFIG, 27),
    "upstream names no field of view. quackd's ColorBlobDetector assumes 90 degrees, which "
    "is the simulator's camera; a real C920 is about 78. The assumption is recorded in "
    "extras rather than silently inherited, and bearings are approximate until measured.",
)

# ── what quackd assumes about the body ───────────────────────────────────────────────────

HEAD_AXES = UpstreamRef(
    "HEAD_AXES",
    "UNVERIFIED",
    src(_ROBOT, 88),
    "head_motor_1 is id 7 and head_motor_2 is id 8 (line 89); which is yaw and which is "
    "pitch is nowhere stated. Upstream's own agent library RoboCrew drives id 7 as yaw and "
    "id 8 as pitch with clamps yaw [-120, 120] and pitch [0, 85], but that is second-hand "
    "and the pitch range is not centred on zero. quackd therefore does NOT declare gaze and "
    "does not move the head at all; search_scan turns the body instead.",
)
BASE_VARIANT = UpstreamRef(
    "BASE_VARIANT",
    "UNVERIFIED",
    src(_ROBOT, 118),
    "the three base wheels are ids 7, 8 and 9 on bus2, which is the 3-omniwheel holonomic "
    "base this adapter targets. Upstream also ships a 2-wheel differential and a mecanum "
    "variant in sibling packages with different action spaces; quackd defaults to omni3 and "
    "declares max_vy 0.0 for any other variant, so a strafe is zeroed and reported rather "
    "than silently ignored.",
)
OBSERVATION_STALENESS = UpstreamRef(
    "OBSERVATION_STALENESS",
    "UNVERIFIED",
    src(_CLIENT, 237),
    "nothing on the wire is timestamped, and upstream's own client returns its cached "
    "last_frames when nothing arrived within polling_timeout_ms (line 156). quackd does not "
    "copy that: "
    "it stamps on arrival and treats an observation older than the watchdog window as a "
    "heartbeat failure rather than as a reading.",
)
THREAD_SAFETY = UpstreamRef(
    "THREAD_SAFETY",
    "UNVERIFIED",
    src(_HOST, 50),
    "the host loop is single-threaded and nothing documents the socket's thread safety, so "
    "quackd serialises every send and receive under one lock in a worker thread.",
)
BATTERY = UpstreamRef(
    "BATTERY",
    "UNVERIFIED",
    src(_ROBOT, 131),
    "no battery, voltage, current or temperature appears anywhere in the state features. The "
    "BOM's power station has no data link, so battery_percent is permanently None and the "
    "manifest does not list a battery sensor.",
)


def all_refs() -> list[UpstreamRef]:
    return [v for v in globals().values() if isinstance(v, UpstreamRef)]


def refs_by_status(status: str) -> list[UpstreamRef]:
    return [r for r in all_refs() if r.status == status]
