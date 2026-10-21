"""The only file in quackd allowed to spell a MOSS or dimOS name.

Every constant is tagged VERIFIED (read from upstream source at the pin, link given) or
UNVERIFIED (an assumption of ours, with what quackd does about it). The human-readable version
is `docs/adapters/moss/README.md`, which names every ref here.

Two upstreams, both pinned by commit because neither has a software release:

- MOSS, https://github.com/metrox-eth/moss at d288647adb2b7a132a69d89afa2f1b45d37b288a (main,
  2026-10-18, read 2026-10-20). Its own CHANGELOG says nothing is frozen, and some of its docs
  lag its code, so every ref below is read from code.
- dimOS, https://github.com/dimensionalOS/dimos at a7d19f76158b01739b26e7a17390f89764aaef83,
  the commit MOSS's own Software.md installs.

quackd imports neither. The cockpit is spoken over HTTP, and everything the cockpit cannot do
goes through quackd's own bridge on the rover (`bridge/moss/`), which is the one place that
imports MOSS and dimOS, lazily, in MOSS's own virtual environment. The bridge's protocol is
quackd's own at both ends, so it is not in here: its names are pinned by source string tests.

Nothing here has been run against a MOSS rover.
"""

from __future__ import annotations

from quackd.upstream import UpstreamRef

REPO = "https://github.com/metrox-eth/moss"
PIN = "d288647adb2b7a132a69d89afa2f1b45d37b288a"  # main, 2026-10-18
READ_ON = "2026-10-20"

DIMOS_REPO = "https://github.com/dimensionalOS/dimos"
DIMOS_PIN = "a7d19f76158b01739b26e7a17390f89764aaef83"  # what MOSS's Software.md installs


def src(path: str, line: int | None = None) -> str:
    return f"{REPO}/blob/{PIN}/{path}" + (f"#L{line}" if line else "")


def dimos(path: str, line: int | None = None) -> str:
    return f"{DIMOS_REPO}/blob/{DIMOS_PIN}/{path}" + (f"#L{line}" if line else "")


_COCKPIT = "tools/moss_cockpit.py"
_EXPLORE_CTL = "tools/explore_ctl.py"
_ADAPTER = "moss_dimos/moss_adapter.py"
_PROFILE = "moss_dimos/robot_profile.py"
_BLUEPRINTS = "moss_dimos/blueprints.py"
_MISSION = "moss_dimos/mission.py"
_ARM = "moss_dimos/arm.py"
_FEETECH = "moss_dimos/arm_feetech.py"
_ARM_PLAY = "tools/moss_arm_play.py"
_POSES = "config/arm_drop_poses.json"
_PREFLIGHT = "tools/moss_preflight.py"
_FLY = "tools/moss-fly.sh"

_MOVEMENT = "dimos/navigation/movement_manager/movement_manager.py"
_MODULE = "dimos/core/module.py"
_RPC_SPEC = "dimos/protocol/rpc/spec.py"
_RPC_CLIENT = "dimos/core/rpc_client.py"
_PUBSUB_RPC = "dimos/protocol/rpc/pubsubrpc.py"
_COORDINATOR = "dimos/control/coordinator.py"
_TRAJECTORY = "dimos/control/tasks/trajectory_task/trajectory_task.py"
_VELOCITY = "dimos/control/tasks/velocity_task/velocity_task.py"
_LCM_PUBSUB = "dimos/protocol/pubsub/impl/lcmpubsub.py"
_IMAGE = "dimos/msgs/sensor_msgs/Image.py"
_JPEG_LCM = "dimos/protocol/pubsub/impl/jpeg_lcm.py"
_REALSENSE = "dimos/hardware/sensors/camera/realsense/camera.py"
_WEBCAM = "dimos/hardware/sensors/camera/webcam.py"
_CAMERA_INFO = "dimos/msgs/sensor_msgs/CameraInfo.py"

LICENCE = "Apache-2.0"
"""MOSS's code. Its hardware files are CERN-OHL-S-2.0 and its documentation CC BY 4.0
(README.md:76). quackd copies none of it."""

# ── the cockpit: MOSS's only network surface ─────────────────────────────────────────────

COCKPIT_PORT = UpstreamRef(
    "8904",
    "VERIFIED",
    src(_COCKPIT, 125),
    "DEFAULT_PORT. MOSS_COCKPIT_PORT moves it (main at line 3115), which is why the address "
    "carries the port and quackd never assumes one past the default.",
)
COCKPIT_BIND = UpstreamRef(
    "0.0.0.0",
    "VERIFIED",
    src(_COCKPIT, 126),
    "DEFAULT_BIND, with no authentication anywhere in the server: anything that reaches the "
    "port reads the rover and commands it. The docs say to reach it through an ssh tunnel.",
)
CMD_HEADER = UpstreamRef(
    "X-Requested-With: moss-cockpit",
    "VERIFIED",
    src(_COCKPIT, 175),
    "every POST /cmd/* without it is answered 403 and its body is not read (do_POST, line 3058). "
    "It stops a cross site form, not a stranger, and quackd sends it on every command.",
)
STATE_JSON = UpstreamRef(
    "GET /state.json",
    "VERIFIED",
    src(_COCKPIT, 1950),
    "LiveCockpit.state: ts, mode, stack, idle, pose, topics, base, nav, recording, system, bus, "
    "camera and launch. A failing request answers 500 with an error and never kills the server.",
)
STATE_POSE = UpstreamRef(
    "pose: x, y, yaw, age_s",
    "VERIFIED",
    src(_COCKPIT, 1862),
    "the newest /odom in the world frame with its age since it arrived, or null before the "
    "first. quackd reads it as the pose and the age as how fresh it is.",
)
STATE_TOPIC_RATES = UpstreamRef(
    "topics: age_s, hz",
    "VERIFIED",
    src(_COCKPIT, 1620),
    "BusCache.stats, the arrival rate over HZ_WINDOW_S per tapped topic. quackd sizes its "
    "freshness rule for the pose from the rover's own odom rate rather than a number of ours.",
)
STATE_NAV = UpstreamRef(
    "nav: exploring, explore_done, goal, goal_reached, last_cmd_vel",
    "VERIFIED",
    src(_COCKPIT, 1882),
    "goal is the newest goal_request from anyone, the explorer included, and goal_reached is "
    "reported only once it arrived after that goal (line 1902). So drive_to trusts it only once "
    "the echoed goal is its own.",
)
STATE_BASE = UpstreamRef(
    "base_view",
    "VERIFIED",
    src(_COCKPIT, 431),
    "the coordinator's base_telemetry as the page shows it: fw, driver, mode, battery_v, "
    "battery_pct, output_pct, ramp_ms, firmware_age_s, stalled, stall_count and age_s.",
)
BATTERY_GAUGE = UpstreamRef(
    "10.2 V to 12.6 V",
    "VERIFIED",
    src(_COCKPIT, 194),
    "BATTERY_EMPTY_V and BATTERY_FULL_V, a linear 3S voltage gauge clamped to 0 to 100 "
    "(battery_pct, line 423). A voltage gauge, not a coulomb counter: quackd reports it as the "
    "cockpit's percent and judges the battery by volts.",
)
STATE_IDLE = UpstreamRef(
    "IDLE_KEYS",
    "VERIFIED",
    src(_COCKPIT, 188),
    "enabled, idle_s, stop_in_s, limit_s, last_source and blind. quackd sizes its cockpit "
    "keepalive from limit_s.",
)
STATE_STACK = UpstreamRef(
    "stack_status",
    "VERIFIED",
    src(_COCKPIT, 647),
    "the newest live run in dimOS's registry, by started_at (line 662), with its blueprint's "
    "short name. A moss-arm run started after the base stack is the one the cockpit then shows, "
    "so quackd prefers the bridge's reading, which keeps both.",
)
STATE_BUS = UpstreamRef(
    "bus: transport, listening, error",
    "VERIFIED",
    src(_COCKPIT, 1975),
    "the bus the running stack chose. Every command but keepalive and stack answers 503 off "
    "LCM (line 2241), so quackd refuses a stack that is not on LCM at connect.",
)
STATE_MODE = UpstreamRef(
    "mode",
    "VERIFIED",
    src(_COCKPIT, 2555),
    "live, or demo for the synthetic rover of --demo (DemoCockpit). quackd refuses a demo "
    "cockpit at connect: it has no bus and no rover behind it.",
)
MJPEG_ROUTE = UpstreamRef(
    "GET /camera.mjpg",
    "VERIFIED",
    src(_COCKPIT, 2973),
    "?cam=nose (the default) or ?cam=wrist. One reader takes one viewer slot for as long as it "
    "stays open.",
)
MJPEG_FRAMING = UpstreamRef(
    "boundary=frame",
    "VERIFIED",
    src(_COCKPIT, 3024),
    "the stream is multipart/x-mixed-replace with this boundary, and each part is --frame, "
    "Content-Type: image/jpeg, Content-Length, a blank line and the JPEG (line 3043). quackd's "
    "reader parses exactly that.",
)
MJPEG_PERIOD_S = UpstreamRef(
    "0.2",
    "VERIFIED",
    src(_COCKPIT, 163),
    "at most 5 fps per viewer. A frame's arrival is all a stream says of its age, which is an "
    "upper bound on when it was taken, so no frame_taken_at is offered on this route.",
)
FIRST_FRAME_WAIT_S = UpstreamRef(
    "2.0",
    "VERIFIED",
    src(_COCKPIT, 165),
    "how long the cockpit waits for a camera's first frame before it opens a stream anyway. "
    "quackd gives a rover with no bridge twice this to show it a nose frame at connect.",
)
MAX_CAMERA_CLIENTS = UpstreamRef(
    "4",
    "VERIFIED",
    src(_COCKPIT, 164),
    "per camera, and a fifth is answered 503 (camera_open, line 1767). quackd holds one nose "
    "reader and no wrist reader, so the owner's page keeps room.",
)
CMD_GOAL = UpstreamRef(
    "POST /cmd/goal",
    "VERIFIED",
    src(_COCKPIT, 2275),
    "x and y in metres, world frame, finite and within GOAL_LIMIT_M (line 1456). Only on a MOSS "
    "stack (409 otherwise) and only on LCM (503).",
)
GOAL_AFTER_STOP_S = UpstreamRef(
    "3.0",
    "VERIFIED",
    src(_COCKPIT, 173),
    "while exploring, the cockpit stops the explorer first and sends the goal this long after "
    "(line 2296), so the explorer's own stay here goal lands first. drive_to waits for its own "
    "goal to be echoed before it believes goal_reached.",
)
CMD_EXPLORE = UpstreamRef(
    "POST /cmd/explore",
    "VERIFIED",
    src(_COCKPIT, 2256),
    'action "start" or "stop", published as Bool(True) on explore_cmd or stop_explore_cmd.',
)
CMD_STOP = UpstreamRef(
    "POST /cmd/stop",
    "VERIFIED",
    src(_COCKPIT, 2300),
    "the soft stop: stop_explore_cmd, stop_movement and a zero twist on tele_cmd_vel, plus a "
    "zero on cmd_vel itself on any blueprint but moss-explore (line 2308), since moss-nav has no "
    "MovementManager. quackd sends it as the second half of every stop.",
)
CMD_KEEPALIVE = UpstreamRef(
    "POST /cmd/keepalive",
    "VERIFIED",
    src(_COCKPIT, 1722),
    "restarts the idle stop's count, and its answer carries the idle document.",
)
CMD_STACK = UpstreamRef(
    "POST /cmd/stack",
    "VERIFIED",
    src(_COCKPIT, 2320),
    "runs tools/moss-fly.sh map, explore, stop or check. quackd never posts it: stop there ends "
    "every dimOS run and drops the arm's torque, and a test says no code path can send it.",
)
NO_TWIST_NO_ARM = UpstreamRef(
    "LiveCockpit.command",
    "VERIFIED",
    src(_COCKPIT, 2227),
    "stack, keepalive, explore, map, goal, stop and episode, and nothing else: no velocity "
    "command and no arm command. Those are what quackd's bridge is for.",
)
IDLE_STOP_DEFAULT_S = UpstreamRef(
    "300",
    "VERIFIED",
    src(_COCKPIT, 176),
    "an idle moss-nav or moss-explore is stopped with tools/moss-fly.sh stop after this long, "
    "MOSS_IDLE_STOP_S to change it, 0 for never.",
)
IDLE_STOP_MIN_S = UpstreamRef(
    "30",
    "VERIFIED",
    src(_COCKPIT, 177),
    "a shorter limit is refused and the default applies, so an enabled idle stop never fires "
    "sooner than this.",
)
IDLE_STOP_BLUEPRINTS = UpstreamRef(
    '"moss-nav", "moss-explore"',
    "VERIFIED",
    src(_COCKPIT, 178),
    "the only stacks the idle stop ends. moss-litter and moss-arm are never stopped by it.",
)
IDLE_FIRMWARE_AGE_S = UpstreamRef(
    "1.0",
    "VERIFIED",
    src(_COCKPIT, 185),
    "a telemetry line whose firmware line is older is the coordinator repeating its last one "
    "with the board lost. quackd uses the same rule for whether the base is answering.",
)
COCKPIT_DEMO = UpstreamRef(
    "--demo",
    "VERIFIED",
    src(_COCKPIT, 3117),
    "a synthetic rover with no bus, no stack and no launcher. quackd's one check by hand ran "
    "its client against it.",
)
RUNS_REGISTRY = UpstreamRef(
    "~/.local/state/dimos/runs/*.json",
    "VERIFIED",
    src(_COCKPIT, 214),
    "runs_dir, XDG_STATE_HOME aware. The bridge reads the stack where the cockpit does.",
)
PROC_ENVIRON = UpstreamRef(
    "/proc/<pid>/environ",
    "VERIFIED",
    src(_COCKPIT, 713),
    "the environment the stack started with: its bus (bus_env, line 733) and its MOSS_* "
    "overrides. The bridge reads the track width and the ramp from there.",
)
BUS_VARS = UpstreamRef(
    "DIMOS_TRANSPORT, TRANSPORT, LCM_DEFAULT_URL",
    "VERIFIED",
    src(_COCKPIT, 729),
    "what chose the stack's bus. The bridge refuses to publish when its own LCM URL is not the "
    "stack's, as the cockpit's idle stop holds itself when they differ.",
)
CHANNELS = UpstreamRef(
    "CHANNELS",
    "VERIFIED",
    src(_COCKPIT, 133),
    "the LCM channels as the stack names them, /<stream>#<msg_name>: /odom, /cmd_vel, "
    "/stop_movement, /explore_cmd, /stop_explore_cmd, /goal_request, /goal_reached and the rest.",
)
CAMERAS = UpstreamRef(
    '"nose", "wrist"',
    "VERIFIED",
    src(_COCKPIT, 149),
    "the cockpit's two cameras, color_image and wrist_image on the bus. The nose is primary.",
)
EXPLORE_CHANNELS = UpstreamRef(
    '"explore_cmd", "stop_explore_cmd"',
    "VERIFIED",
    src(_EXPLORE_CTL, 26),
    "explore_ctl.CHANNELS start and stop, each a Bool(True). The bridge halts an exploration "
    "quackd started with stop_explore_cmd.",
)
LCM_CHANNEL_NAME = UpstreamRef(
    "/<stream>#<msg_name>",
    "VERIFIED",
    src(_EXPLORE_CTL, 61),
    "explore_ctl.channel, the same rule as dimOS's own Topic (lcmpubsub.py:56). The bridge "
    "builds every channel it publishes on this way.",
)

# ── the tracks ──────────────────────────────────────────────────────────────────────────

MPS_AT_LIMIT = UpstreamRef(
    "0.32",
    "VERIFIED",
    src(_ADAPTER, 294),
    "MossGeometry.mps_at_limit, m/s at full power, measured by its maker on the floor. Above "
    "quackd's 0.3 schema cap, so the cap binds and max_vx is 0.3.",
)
MIN_PCT_FLOOR = UpstreamRef(
    "20",
    "VERIFIED",
    src(_ADAPTER, 297),
    "MossGeometry.min_pct: a non zero track below this percent is raised to it (floor_tracks, "
    "line 187), so a slow command is not slow. The mock does the same.",
)
PIVOT_PCT = UpstreamRef(
    "100",
    "VERIFIED",
    src(_ADAPTER, 125),
    "PIVOT_PCT_DEFAULT: tracks in opposite directions are sent at this percent on both "
    "(pivot_tracks, line 217, applied on every write at line 727). Any spin on the spot, and any "
    "arc whose inner track reverses, runs both tracks at full power.",
)
TWIST_TO_TRACKS = UpstreamRef(
    "twist_to_tracks",
    "VERIFIED",
    src(_ADAPTER, 319),
    "v_left = vx minus wz times track/2 and v_right = vx plus wz times track/2, so +wz turns "
    "left. vy is dropped: a tracked base cannot strafe, and max_vy is 0.",
)
FIRMWARE_MAX_PCT = UpstreamRef(
    "100",
    "VERIFIED",
    src(_ADAPTER, 103),
    "the firmware's percent ceiling. A stop from full power takes the ramp's fall times this.",
)
FIRMWARE_DEADMAN_S = UpstreamRef(
    "0.300",
    "VERIFIED",
    src(_ADAPTER, 104),
    "FIRMWARE_COMMAND_TIMEOUT_S, Control.h's COMMAND_TIMEOUT_MS: the board stops the tracks "
    "this long after the last drive. The bridge's own twist deadman is the same length.",
)
ADAPTER_STALE_S = UpstreamRef(
    "0.500",
    "VERIFIED",
    src(_ADAPTER, 107),
    "STALE_S: no fresh twist for this long and the base adapter sends stop once and holds.",
)
VELOCITY_TASK_TIMEOUT_S = UpstreamRef(
    "0.2",
    "VERIFIED",
    dimos(_VELOCITY, 59),
    "the coordinator's velocity task zeroes its output after this long with no command "
    "(zero_on_timeout, line 60). MOSS's base runs on one (blueprints.py:292).",
)
RAMP_MS = UpstreamRef(
    "MOSS_RAMP_MS",
    "VERIFIED",
    src(_ADAPTER, 538),
    '"UP DOWN", ms per percent of rise and fall, sent to the firmware at connect. The launcher '
    'exports "5 5" unless pulsing (moss-fly.sh:79), so a stop from full power takes 0.5 s. The '
    "bridge reads it from the stack's environment and reports the stop's length.",
)
LAUNCHER_RAMP_MS = UpstreamRef(
    '"5 5"',
    "VERIFIED",
    src(_FLY, 79),
    "the ramp tools/moss-fly.sh exports when nothing else set one and the base does not pulse "
    '("1 1" when it does). The mock ramps by it.',
)
LAUNCHER_EXPLORE_RADIUS = UpstreamRef(
    "MOSS_EXPLORE_RADIUS_M",
    "VERIFIED",
    src(_FLY, 87),
    "the area the explorer keeps to around where exploring started, which tools/moss-fly.sh "
    "sets to 4 m unless the owner set one. The explorer's own default is 0, no limit "
    "(moss_dimos/explorer2.py:270). An exploration quackd started goes on inside it when the "
    "link drops and there is no bridge to halt it.",
)
STALL_S = UpstreamRef(
    "1.0",
    "VERIFIED",
    src(_ADAPTER, 114),
    "a commanded track whose encoder barely moved for this long is a stall: the base stops, "
    "counts it and flags it until the next accepted command. The mock stalls the same way.",
)
TRACK_M = UpstreamRef(
    "0.266",
    "VERIFIED",
    src(_PROFILE, 125),
    "RobotProfile.track_m, the CAD's track centre distance, overridden by MOSS_TRACK_M "
    "(line 143). The bridge reads the stack's own value through robot_profile.current().",
)
TELEMETRY_PERIOD_S = UpstreamRef(
    "0.5",
    "VERIFIED",
    src(_BLUEPRINTS, 207),
    "MossControlCoordinator republishes the base's firmware telemetry this often. quackd calls "
    "the cockpit's copy stale after four of these periods.",
)
CAMERA_FPS = UpstreamRef(
    "5",
    "VERIFIED",
    src(_BLUEPRINTS, 302),
    "the nose camera's rate, 640 by 480, and the wrist's (line 359). quackd's frame period.",
)
JPEG_ON_THE_BUS = UpstreamRef(
    "JpegLcmTransport",
    "VERIFIED",
    src(_BLUEPRINTS, 633),
    "color_image and wrist_image cross the bus as JPEG, so the bridge hands the bytes on and "
    "decodes nothing.",
)
NAV_PAD_ON_CMD_VEL = UpstreamRef(
    '(GamepadTeleop, "tele_cmd_vel", "cmd_vel")',
    "VERIFIED",
    src(_BLUEPRINTS, 689),
    "on moss-nav the pad's channel is remapped onto cmd_vel and there is no MovementManager, so "
    "the bridge publishes a twist on cmd_vel there and on tele_cmd_vel on moss-explore.",
)
EXPLORE_MOVEMENT_MANAGER = UpstreamRef(
    "MovementManager.blueprint",
    "VERIFIED",
    src(_BLUEPRINTS, 706),
    "moss-explore adds the planner, the MovementManager and the explorer. moss-litter is "
    "moss-explore plus the detection and the mission (build_moss_litter, line 741).",
)
EXPLORER_GOAL_TIMEOUT_S = UpstreamRef(
    "45.0",
    "VERIFIED",
    src(_BLUEPRINTS, 305),
    "how long the explorer gives each goal it picks. drive_to gives its own goal the same, plus "
    "the cockpit's deferral while exploring.",
)
MOSS_ARM_RUN = UpstreamRef(
    "build_moss_arm",
    "VERIFIED",
    src(_BLUEPRINTS, 767),
    "a separate dimos run serving the arm's RPCs under ARM_COORDINATOR_NAME (line 796).",
)

# ── the bus rule that shapes every stop ──────────────────────────────────────────────────

TELEOP_CANCELS_NAVIGATION = UpstreamRef(
    "MovementManager._on_teleop",
    "VERIFIED",
    dimos(_MOVEMENT, 122),
    "every message on tele_cmd_vel, a zero included, cancels the planner's goal with "
    "stop_movement and a NaN goal (_cancel_goal, line 100) and holds the planner off for "
    "tele_cooldown_sec. That is why the bridge falls silent after its deadman's one zero.",
)
TELE_COOLDOWN_S = UpstreamRef(
    "1.0",
    "VERIFIED",
    dimos(_MOVEMENT, 47),
    "MovementManagerConfig.tele_cooldown_sec: nav_cmd_vel is dropped this long after the last "
    "teleop message (_on_nav, line 112).",
)

# ── the litter mission ──────────────────────────────────────────────────────────────────

SERVO_W_MAX = UpstreamRef(
    "0.4",
    "VERIFIED",
    src(_MISSION, 79),
    "the mission's fastest servo turn, rad/s. quackd's max_wz, and the turn rate at which the "
    "bridge's pulses keep the maker's own rhythm.",
)
PULSE_S = UpstreamRef(
    "0.1",
    "VERIFIED",
    src(_MISSION, 87),
    "one tick of twist, then rest. MOSS_SERVO_PULSE_S changes it, and the bridge reads the "
    "stack's value.",
)
REST_S = UpstreamRef(
    "0.4",
    "VERIFIED",
    src(_MISSION, 89),
    "zeros after a pulse, near the object and while turning. The bridge scales it by max_wz "
    "over the asked turn rate.",
)
MISSION_STATES = UpstreamRef(
    "EXPLORING, STOPPING, APPROACHING, SERVOING, PICKING, RESUMING",
    "VERIFIED",
    src(_MISSION, 93),
    "the states mission_status reports. collect_litter narrates the changes it sees.",
)
STOP_SETTLE_S = UpstreamRef(
    "3.0",
    "VERIFIED",
    src(_MISSION, 69),
    "after the mission stops the explorer for a target, it waits this long before its goal, "
    "as the cockpit does. The mock's mission does too.",
)
MISSION_TICK_HZ = UpstreamRef(
    "10.0",
    "VERIFIED",
    src(_MISSION, 85),
    "the mission's own loop rate (TICK_HZ).",
)
MISSION_INPUTS = UpstreamRef(
    "objects, odom, goal_reached",
    "VERIFIED",
    src(_MISSION, 374),
    "LitterMission's only In streams. It listens to no stop: the soft stop does not end it, "
    "and from the moment a moss-litter stack starts it goes for any confirmed litter in view.",
)
MISSION_LOOP = UpstreamRef(
    "LitterMission._run",
    "VERIFIED",
    src(_MISSION, 472),
    "the 10 Hz tick, the only place the mission publishes a command (_do), and it runs "
    "while not self._stop.is_set() (line 474).",
)
MISSION_STOP_RPC = UpstreamRef(
    "LitterMission.stop",
    "VERIFIED",
    src(_MISSION, 430),
    "an @rpc: sets the stop event, joins the loop for up to 2 s, publishes a zero tele twist "
    "(line 436) and calls super().stop() (line 439), which closes its RPC service and stops its "
    "streams (module.py:200). It does not touch a pick already running.",
)
MISSION_STATUS_RPC = UpstreamRef(
    "LitterMission.mission_status",
    "VERIFIED",
    src(_MISSION, 441),
    "state, target, picked and skipped. Once stop() has closed the RPC service it answers "
    "nothing, which is how the bridge confirms a stop.",
)
MISSION_PICK_SUBPROCESS = UpstreamRef(
    "[sys.executable, tools/moss_arm_play.py]",
    "VERIFIED",
    src(_MISSION, 512),
    "the mission picks by running the arm script as a subprocess in a thread of its own, with a "
    "PICK_TIMEOUT_S of 180 (line 72). stop() leaves that thread and that process alone.",
)
MISSION_PICK_CMD = UpstreamRef(
    "MOSS_PICK_CMD",
    "VERIFIED",
    src(_MISSION, 65),
    "the stack's own override of the pick command (line 400 reads it, line 512 splits it). The "
    "bridge reads it from the stack's environment to find the mission's pick when it is not "
    "the maker's script.",
)
MISSION_STOP_LEAVES_PICK = UpstreamRef(
    "LitterMission.stop leaves its pick running",
    "VERIFIED",
    src(_MISSION, 431),
    "stop() sets the stop event, joins the 10 Hz loop, publishes a zero and closes the module "
    "(lines 431 to 439), and never touches self._pick_thread or the subprocess.run it waits on "
    "(lines 507 to 527). A pick in flight outlives the stop: its script, still importing dimOS "
    "and reading the joints (moss_arm_play.py lines 86 to 110), sends its one trajectory after "
    "any cancel, and the coordinator plays the whole cycle. So the bridge's stop ends that "
    "script first, then cancels the trajectory and reads the arm twice.",
)
MISSION_RESUMES_EXPLORING = UpstreamRef(
    "RESUMING",
    "VERIFIED",
    src(_MISSION, 339),
    "after a pick, and after a skipped one (_skip, line 282), the mission publishes explore_cmd "
    "itself. A cancelled cycle is a failed pick, so a mission still running when its arm is "
    "cancelled starts exploring again. The bridge stops the mission before it cancels the arm.",
)
MISSION_AUTOSTART = UpstreamRef(
    "MOSS_MISSION_AUTOSTART",
    "VERIFIED",
    src(_MISSION, 67),
    "1 by default (explore_cmd at start, line 427), but tools/moss-fly.sh litter sets 0 "
    "(moss-fly.sh:251), so collect_litter starts the explorer itself.",
)
PICK_TIMEOUT_S = UpstreamRef(
    "180.0",
    "VERIFIED",
    src(_MISSION, 72),
    "how long the mission gives one pick. pick_and_drop gives the arm the same.",
)

# ── the arm ─────────────────────────────────────────────────────────────────────────────

ARM_PLAY = UpstreamRef(
    "tools/moss_arm_play.py",
    "VERIFIED",
    src(_ARM_PLAY, 58),
    "the maker's cycle player: --pick-pose, --from (line 64) and --sim. The bridge runs it as a "
    "subprocess, exactly as the mission does, and mirrors none of it.",
)
ARM_PLAY_ONE_TRAJECTORY = UpstreamRef(
    "client.execute_trajectory(traj)",
    "VERIFIED",
    src(_ARM_PLAY, 111),
    "the whole cycle is one trajectory sent once. Killing the script does not cancel it.",
)
ARM_PLAY_EXIT_CODES = UpstreamRef(
    "0, 2, 3, 4",
    "VERIFIED",
    src(_ARM_PLAY, 145),
    "done, refused (lines 96 and 106), refused by the coordinator (line 114) and ARM LAG "
    "(line 133). Anything else is a crash, and the bridge says so.",
)
ARM_PLAY_LINES = UpstreamRef(
    "running:, done:, ARM LAG, refused",
    "VERIFIED",
    src(_ARM_PLAY, 115),
    "the lines the bridge reads progress from, with the exit code.",
)
ARM_PLAY_LAG_WATCH = UpstreamRef(
    "client.cancel_trajectory()",
    "VERIFIED",
    src(_ARM_PLAY, 129),
    "a joint more than --max-lag behind for --lag-seconds cancels the trajectory and the script "
    "exits 4. A trajectory cancelled from outside trips it, which is the mission race.",
)
ARM_PLAY_RPC_CLIENT = UpstreamRef(
    "RPCClient(None, ControlCoordinator, remote_name=A.ARM_COORDINATOR_NAME)",
    "VERIFIED",
    src(_ARM_PLAY, 90),
    "how the script reaches the arm. The bridge builds the same client for its cancel.",
)
ARM_COORDINATOR_NAME = UpstreamRef(
    "ArmCoordinator",
    "VERIFIED",
    src(_ARM, 65),
    "the arm's coordinator serves its RPCs under this name, the base's keeps ControlCoordinator "
    "(blueprints.py:177).",
)
START_TOLERANCE_RAD = UpstreamRef(
    "0.05",
    "VERIFIED",
    src(_ARM, 63),
    "the task refuses a trajectory starting further than this from the arm. The bridge calls the "
    "arm folded when every joint is this close to the drive pose.",
)
JOINT_NAMES = UpstreamRef(
    "JOINT_NAMES",
    "VERIFIED",
    src(_ARM, 51),
    "arm/shoulder_pan, arm/shoulder_lift, arm/elbow_flex, arm/wrist_flex, arm/wrist_roll and "
    "arm/gripper. The gripper reads its jaw gap in metres.",
)
ARM_CALIBRATION_ENV = UpstreamRef(
    "MOSS_ARM_CALIB",
    "VERIFIED",
    src(_ARM, 45),
    "a calibration file elsewhere. calibration_path() (line 105) reads it from the environment it "
    "is given, else from its own process's, and the drive pose and the maker's script take the "
    "arm's poses from that file. The bridge runs the script in the moss-arm run's own "
    "environment and reads the drive pose with it, never with its own.",
)
DRIVE_POSE = UpstreamRef(
    "drive_pose_rad",
    "VERIFIED",
    src(_ARM, 234),
    "the pose the arm rests in for driving, from the calibration when it exists, else "
    "DRIVE_TICKS (line 99).",
)
GAP_OPEN_M = UpstreamRef(
    "0.090",
    "VERIFIED",
    src(_ARM, 60),
    "the jaws fully open, the NormaCore gripper widened to 90 mm.",
)
CYCLE_FROM_BACK_TO_CLEAR = UpstreamRef(
    "back_to_clear",
    "VERIFIED",
    src(_POSES, 361),
    "the cycle's return from the bin: drop to clear, hold, then the drive pose. stow_arm plays "
    "the cycle from here, which first moves the arm to the drop pose if it is elsewhere.",
)
CYCLE_TIMED_S = UpstreamRef(
    "36.9",
    "VERIFIED",
    src(_POSES, 187),
    "the sequence's ten moves, leave_drive to to_drive, add to this many seconds as the maker "
    "timed them, before any move is stretched to the speed ceiling. The mock's cycle lasts "
    "this long.",
)
PICK_POSES = UpstreamRef(
    '"pick", "pick_low"',
    "VERIFIED",
    src(_POSES, 67),
    "the pose played as pick: the video's, for an object standing, or pick_low (line 164), for "
    "one lying on the floor.",
)
STILL_TICKS_PER_S = UpstreamRef(
    "20.0",
    "VERIFIED",
    src(_FEETECH, 62),
    "below this a servo counts as stopped. The bridge's two joint reads after a cancel call the "
    "arm held when every joint moved slower than this.",
)
DISCONNECT_TORQUE_OFF = UpstreamRef(
    "FeetechSo101Adapter.disconnect",
    "VERIFIED",
    src(_FEETECH, 237),
    "turns torque off on all six servos. Every end of the moss-arm run calls it, so the idle "
    "stop and tools/moss-fly.sh stop drop the arm. quackd never ends that run.",
)
GET_JOINT_POSITIONS = UpstreamRef(
    "ControlCoordinator.get_joint_positions",
    "VERIFIED",
    dimos(_COORDINATOR, 434),
    "joint name to position for every joint of the coordinator's hardware.",
)
CANCEL_TRAJECTORY = UpstreamRef(
    "ControlCoordinator.cancel_trajectory",
    "VERIFIED",
    dimos(_COORDINATOR, 457),
    "cancels the sole trajectory task and answers a TrajectoryCancellationResult.",
)
CANCELLATION_STATUS = UpstreamRef(
    "TrajectoryCancellationStatus",
    "VERIFIED",
    dimos(_TRAJECTORY, 91),
    "CANCELLED, ALREADY_STOPPED, NO_TRAJECTORY_TASK or UNCERTAIN. The bridge reports the name.",
)

# ── the battery ─────────────────────────────────────────────────────────────────────────

BATTERY_FLOOR_V = UpstreamRef(
    "10.8",
    "VERIFIED",
    src(_PREFLIGHT, 74),
    "BATTERY_FLOOR_MV, the launcher's floor: the flight check refuses under it. quackd's "
    "battery_ok uses the same.",
)
BMS_CUT_V = UpstreamRef(
    "10.2",
    "VERIFIED",
    src(_PREFLIGHT, 75),
    "BMS_CUT_MV, where the 3S pack's BMS cuts.",
)

# ── the hard stop quackd never sends ────────────────────────────────────────────────────

FLY_STOP = UpstreamRef(
    "tools/moss-fly.sh stop",
    "VERIFIED",
    src(_FLY, 224),
    "explore_ctl stop, then dimos stop and a pkill of every dimos run (stack_down, line 214), "
    "moss-arm included.",
)

# ── dimOS RPC, which the bridge reaches the arm and the mission through ─────────────────

RPC_NAME_IS_THE_CLASS = UpstreamRef(
    "serve_module_rpc",
    "VERIFIED",
    dimos(_RPC_SPEC, 110),
    "a module's RPCs are served as <name>/<method>, the name being its class's unless an "
    "instance_name was given (module.py:112). moss-litter gives LitterMission none "
    "(blueprints.py:760), so its RPCs are LitterMission/stop and LitterMission/mission_status.",
)
RPC_STOP_IS_NOWAIT = UpstreamRef(
    "call_nowait",
    "VERIFIED",
    dimos(_RPC_CLIENT, 85),
    "an RPCClient sends stop without waiting for an answer, because the remote side closes its "
    "RPC service before it could answer. So a stop is confirmed by the silence after it.",
)
RPC_TIMEOUT_DEFAULT = UpstreamRef(
    "120.0",
    "VERIFIED",
    dimos(_RPC_SPEC, 36),
    "DEFAULT_RPC_TIMEOUT, then call_sync raises TimeoutError (line 83). The bridge builds its "
    "RPC with a timeout of its own, so a call to a module that is gone does not hold a stop.",
)
LCM_RPC = UpstreamRef(
    "LCMRPC",
    "VERIFIED",
    dimos(_PUBSUB_RPC, 303),
    "RPC over LCM on /rpc/<name>/req and /res (topicgen, line 317), pickled payloads.",
)
MODULE_STOP_CLOSES = UpstreamRef(
    "ModuleBase._close_module",
    "VERIFIED",
    dimos(_MODULE, 200),
    "what a module's stop() ends with: its tools, its RPC service (line 207), its loop and its "
    "streams (line 228).",
)

# ── frames ──────────────────────────────────────────────────────────────────────────────

IMAGE_TS = UpstreamRef(
    "Image.ts",
    "VERIFIED",
    dimos(_IMAGE, 74),
    "time.time() unless the camera says otherwise, and the header stamp a JPEG frame carries on "
    "the bus (lcm_jpeg_encode, line 568, through JpegLCM, jpeg_lcm.py:34).",
)
REALSENSE_GLOBAL_TIME = UpstreamRef(
    "global_time_enabled",
    "VERIFIED",
    dimos(_REALSENSE, 317),
    "the RealSense module puts every sensor that supports it on the host clock, and warns about "
    "one that does not, which stays on the device's own clock.",
)
REALSENSE_FRAME_STAMP = UpstreamRef(
    "frame.get_timestamp()",
    "VERIFIED",
    dimos(_REALSENSE, 554),
    "a colour frame's ts is the camera's own timestamp in seconds (line 593), not its arrival.",
)
WEBCAM_STAMP = UpstreamRef(
    "ts=time.time()",
    "VERIFIED",
    dimos(_WEBCAM, 133),
    "the wrist camera stamps a frame when it reads it, on the rover's wall clock.",
)
CAMERA_INFO_K = UpstreamRef(
    "CameraInfo.K",
    "VERIFIED",
    dimos(_CAMERA_INFO, 292),
    "with width (line 282), what the bridge turns into a horizontal field of view when the "
    "stack publishes camera_info.",
)
TWIST_MSG_NAME = UpstreamRef(
    "geometry_msgs.Twist",
    "VERIFIED",
    dimos(
        "dimos/msgs/geometry_msgs/Twist.py",
        28,
    ),
    "the msg_name in every twist channel the stack listens on.",
)

# ── the body ────────────────────────────────────────────────────────────────────────────

STATUS_LINE = UpstreamRef(
    "Autonomous litter picking and the full field test are not yet demonstrated",
    "VERIFIED",
    src("README.md", 14),
    "the maker's own word on where MOSS stands.",
)
BLOCKS_FIRST = UpstreamRef(
    "Put it on blocks for the first run",
    "VERIFIED",
    src("README.md", 72),
    "a prototype with a lithium battery and no certified protection. The checklist starts here.",
)
MASS = UpstreamRef(
    "MASS",
    "UNVERIFIED",
    f"{REPO}/issues/2",
    "4.2 kg, the maker's own figure for the V0.3 prototype complete, read on 2026-10-20 in "
    "issue 2. An issue is not versioned, so nothing at the pin carries it. The datasheet gives it "
    "as official, the maker's word, with that source, and says the V0.6 hull has not been "
    "weighed.",
)
ARM_MASS = UpstreamRef(
    "ARM_MASS",
    "UNVERIFIED",
    f"{REPO}/issues/2#issuecomment-5849657605",
    "768 g, the arm and gripper together, weighed off the rover on 27 September, in a comment "
    "on issue 2, which nothing at the pin carries either. The datasheet's mass note gives it.",
)
BOM_SERVOS = UpstreamRef(
    "ST3215 12 V serial bus servo",
    "VERIFIED",
    src("BOM.md", 42),
    "five of them on the arm, and a CF35-12 on the gripper (line 43): six actuated joints.",
)
NOT_A_RUNTIME_CLAIM = UpstreamRef(
    "not a claim about battery capacity or runtime",
    "VERIFIED",
    src("docs/cad-with-codex.md", 13),
    "the maker on the battery bay, which is why endurance stays not published.",
)

# ── what quackd assumes ─────────────────────────────────────────────────────────────────

MISSION_STAYS_STOPPED = UpstreamRef(
    "MISSION_STAYS_STOPPED",
    "UNVERIFIED",
    src(_MISSION, 430),
    "nothing read restarts a module whose stop() has run, but no run has shown it. quackd treats "
    "a stopped mission as stopped until the stack restarts: the bridge remembers the run it "
    "stopped, and collect_litter refuses until the run is a new one.",
)
MISSION_STOP_REACHES_IT = UpstreamRef(
    "MISSION_STOP_REACHES_IT",
    "UNVERIFIED",
    dimos(_RPC_CLIENT, 85),
    "that a stop sent over LCM RPC from the bridge's process reaches the mission on the rover. "
    "It is sent without an answer, so the bridge confirms it by mission_status no longer "
    "answering, reports a stop it could not confirm as a failure, and stops the arm and sends "
    "the zero twist either way.",
)
IMAGE_STAMP_CLOCK = UpstreamRef(
    "IMAGE_STAMP_CLOCK",
    "UNVERIFIED",
    dimos(_REALSENSE, 317),
    "that a nose frame's stamp is on the rover's wall clock. dimOS asks for global time, but "
    "what global time means is librealsense's, outside both pins, and a sensor without it stays "
    "on its own clock. The bridge checks every stamp against its own receive time and only a "
    "frame that passes carries frame_taken_at.",
)
PULSE_TURN_RATE = UpstreamRef(
    "PULSE_TURN_RATE",
    "UNVERIFIED",
    src(_MISSION, 87),
    "how far one pulse turns the rover. Nothing measures it, and the firmware's ramp means a "
    "0.1 s pulse never reaches full power. search_scan's steps are the angles it asks for, and "
    "the real angle per step is the hardware checklist's step 6.",
)
PICK_POINT = UpstreamRef(
    "PICK_POINT",
    "UNVERIFIED",
    src(_MISSION, 60),
    "where the jaws close, which the maker marks to measure. pick_and_drop says the arm reaches "
    "for nothing and tells the pilot to check the wrist view first.",
)
CYCLE_FROM_ANYWHERE = UpstreamRef(
    "CYCLE_FROM_ANYWHERE",
    "UNVERIFIED",
    src(_ARM_PLAY, 107),
    "that the cycle is safe from wherever the arm is: the script's first move goes straight in "
    "joint space to the cycle's first pose and checks no scene. Both arm verbs are confirm "
    "class and say so in their descriptions.",
)
LENS_WITHOUT_CAMERA_INFO = UpstreamRef(
    "LENS_WITHOUT_CAMERA_INFO",
    "UNVERIFIED",
    src(_BLUEPRINTS, 302),
    "the nose lens when no camera_info is heard. MOSS streams 640 by 480, which may be a crop "
    "of the sensor, so quackd declares no field of view and --fov-deg says it.",
)
TRACK_WIDTH_IS_CAD = UpstreamRef(
    "TRACK_WIDTH_IS_CAD",
    "UNVERIFIED",
    src(_PROFILE, 125),
    "0.266 m is the CAD's, marked as a placeholder to measure. The arc clip uses the stack's "
    "own value, so it is right for whatever track width the stack drives with.",
)
ODOMETRY_UNDER_READS = UpstreamRef(
    "ODOMETRY_UNDER_READS",
    "UNVERIFIED",
    src(_BLUEPRINTS, 513),
    "how far the lidar odometry lags a full power pivot, which the maker notes and nobody has "
    "measured. drive_to judges arrival by the planner's goal_reached, not by the pose, and the "
    "mock under-reads its pivots on purpose.",
)
ARM_UNSEEN_WITHOUT_MOSS_ARM = UpstreamRef(
    "ARM_UNSEEN_WITHOUT_MOSS_ARM",
    "UNVERIFIED",
    src(_BLUEPRINTS, 767),
    "with no moss-arm run nothing holds the arm and nothing reports it. quackd drives the base "
    "as if it were folded, offers no arm verb, and says so in the manifest's assumptions.",
)


def all_refs() -> list[UpstreamRef]:
    return [v for v in globals().values() if isinstance(v, UpstreamRef)]


def refs_by_status(status: str) -> list[UpstreamRef]:
    return [r for r in all_refs() if r.status == status]
