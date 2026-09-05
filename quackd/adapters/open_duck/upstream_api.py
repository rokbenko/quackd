"""The only file in quackd allowed to spell an Open Duck Mini name (ADR-0022, ADR-0024).

Every constant is tagged VERIFIED (read from upstream source, link given) or UNVERIFIED (an
assumption of ours, with what quackd does about it). `docs/adapters/open_duck.md` is the
human-readable version; `tests/test_upstream_api.py` proves UNVERIFIED names are only
reachable from the experimental `bridge` backend.

Two upstreams, both pinned and read on 2026-09-03. `Open_Duck_Mini_Runtime` is the code
that runs on the robot's Raspberry Pi; **it carries no LICENSE file, so all rights are
reserved by its authors.** quackd vendors none of it, depends on none of it and ships none
of it: the names below are cited as facts, and the bridge daemon imports the package on
*your* Pi, where *you* installed it from upstream. `Open_Duck_Mini` is Apache-2.0 and holds
the walk policy, which quackd links to and never redistributes.

The bridge's own wire protocol is deliberately NOT in this file. quackd defines both ends of
it, so it has no upstream to be verified against; citing it here with a link to a repo that
does not define it would be a false citation. It lives in `bridge.py` and is specified in
`docs/adapters/open_duck.md`.

Nothing here has been run against a physical duck.
"""

from __future__ import annotations

from quackd.transport.upstream_api import UpstreamRef

REPO = "https://github.com/apirrone/Open_Duck_Mini_Runtime"
PIN = "32037347dc43186a017f2116bcfde7c461b81f54"  # branch v2, 2025-06-24
REPO_HUB = "https://github.com/apirrone/Open_Duck_Mini"
PIN_HUB = "b23317a485b3cec7d8417f352478778b3475173c"  # branch v2, 2026-01-31
READ_ON = "2026-09-03"


def src(path: str, line: int | None = None) -> str:
    return f"{REPO}/blob/{PIN}/{path}" + (f"#L{line}" if line else "")


def hub(path: str, line: int | None = None) -> str:
    return f"{REPO_HUB}/blob/{PIN_HUB}/{path}" + (f"#L{line}" if line else "")


_PKG = "mini_bdx_runtime/mini_bdx_runtime"
_WALK = "scripts/v2_rl_walk_mujoco.py"
_PAD = f"{_PKG}/xbox_controller.py"
_HWI = f"{_PKG}/rustypot_position_hwi.py"
_CONFIG = f"{_PKG}/duck_config.py"

# ── the repository, and what its licence does and does not allow ────────────────────────

RUNTIME_REPO = UpstreamRef(
    "apirrone/Open_Duck_Mini_Runtime",
    "VERIFIED",
    src(""),
    "branch v2 is the default branch; the on-robot code for the Open Duck Mini v2",
)
RUNTIME_LICENSE = UpstreamRef(
    "Open_Duck_Mini_Runtime has no LICENSE file",
    "VERIFIED",
    src(""),
    "the repository root holds no LICENSE, LICENSE.md, LICENCE or COPYING, and the GitHub "
    "API reports no license, so all rights are reserved. quackd vendors none of it, never "
    "declares it as a dependency or an extra, and never ships it. The bridge daemon imports "
    "it on the owner's own Raspberry Pi",
)
HUB_REPO = UpstreamRef(
    "apirrone/Open_Duck_Mini",
    "VERIFIED",
    hub(""),
    "Apache-2.0; the design, the docs and the walk policy",
)
PACKAGE = UpstreamRef(
    "mini_bdx_runtime",
    "VERIFIED",
    src(_PKG),
    "the import name. It runs inside the bridge daemon on the Pi and never in quackd's "
    "own process, on any platform",
)

# ── the control loop the bridge borrows ─────────────────────────────────────────────────

RL_WALK_SCRIPT = UpstreamRef(
    "scripts/v2_rl_walk_mujoco.py",
    "VERIFIED",
    src(_WALK),
    "the on-Pi walk loop: reads a local pygame gamepad and runs an ONNX policy",
)
RL_WALK_CLASS = UpstreamRef(
    "RLWalk",
    "VERIFIED",
    src(_WALK),
    "the class that owns the loop, with a run() method behind an if __name__ guard",
)
RL_WALK_ARGS = UpstreamRef(
    "--onnx_model_path, --duck_config_path, --control_freq",
    "VERIFIED",
    src(_WALK),
    "control_freq defaults to 50 Hz; quackd passes the owner's arguments through verbatim",
)
CONTROL_FREQ = UpstreamRef(
    "50",
    "VERIFIED",
    src(_WALK),
    "the control loop's default rate in Hz, which is also what the policy was trained at",
)
PAD_CONSTRUCTION = UpstreamRef(
    "self.xbox_controller = XBoxController(self.command_freq)",
    "VERIFIED",
    src(_WALK),
    "the single construction site the bridge replaces. command_freq is 20 Hz, distinct from "
    "the 50 Hz control rate",
)

# ── the command vector: the whole interface between a pilot and this robot ──────────────

COMMAND_VECTOR = UpstreamRef(
    "vx, vy, vyaw, neck_pitch, head_pitch, head_yaw, head_roll",
    "VERIFIED",
    src(_PAD),
    "the seven floats the policy consumes every tick, in this order: metres per second, "
    "radians per second, then four head values in radians. The training environment's "
    "sample_command in Open_Duck_Playground agrees element for element. Re-read 2026-09-05: "
    "the last four are OFFSETS, not absolute joint angles. The walk loop recomputes "
    "motor_targets = init_pos + action * action_scale each tick and then does "
    "motor_targets[5:9] = last_commands[3:] + motor_targets[5:9], so a head value is added "
    "to wherever the policy is holding the head. They do not accumulate, because the base is "
    "rebuilt every tick, but quackd clamping them to 80 percent of the joint range bounds an "
    "offset rather than a joint",
)
COMMAND_RANGES = UpstreamRef(
    "vx +-0.15, vy +-0.2, vyaw +-1.0, neck_pitch -0.34..1.1, head_pitch -0.78..0.3, "
    "head_yaw +-0.5, head_roll +-0.5",
    "VERIFIED",
    src(_PAD),
    "the runtime's own clamps. They are TIGHTER than the training environment's on head "
    "pitch and head yaw, and quackd obeys the tighter pair",
)
PAD_CLASS = UpstreamRef(
    "XBoxController(command_freq, only_head_control=False)",
    "VERIFIED",
    src(_PAD),
    "get_last_command() returns (commands of length 7, buttons, left_trigger, right_trigger)",
)
PAD_BUTTONS = UpstreamRef(
    "A, B, X, Y, LB, RB, dpad_up, dpad_down",
    "VERIFIED",
    src(f"{_PKG}/buttons.py"),
    "each is a Button carrying is_pressed, triggered and released. quackd never presses Y: "
    "upstream's README calls head control very experimental and warns it can break the head",
)

# ── the body ────────────────────────────────────────────────────────────────────────────

HWI_CLASS = UpstreamRef(
    'HWI(duck_config, usb_port="/dev/ttyACM0")',
    "VERIFIED",
    src(_HWI),
    "the hardware interface over the Feetech STS3215 serial bus. quackd never constructs "
    "one: the bus has exactly one owner and it is upstream's own loop",
)
JOINTS = UpstreamRef(
    "left_hip_yaw 20, left_hip_roll 21, left_hip_pitch 22, left_knee 23, left_ankle 24, "
    "neck_pitch 30, head_pitch 31, head_yaw 32, head_roll 33, right_hip_yaw 10, "
    "right_hip_roll 11, right_hip_pitch 12, right_knee 13, right_ankle 14",
    "VERIFIED",
    src(_HWI),
    "the ordered joint dict and its motor ids. The deployed interface HAS head_roll and has "
    "no head_pitch1 or head_pitch2; upstream docs that say otherwise are stale",
)
DUCK_CONFIG = UpstreamRef(
    'DuckConfig(config_json_path="~/duck_config.json")',
    "VERIFIED",
    src(_CONFIG),
    "also carries start_paused, imu_upside_down and a soft offset per joint",
)
EXPRESSION_FEATURES = UpstreamRef(
    "expression_features.eyes, .projector, .antennas, .speaker, .microphone, .camera",
    "VERIFIED",
    src(_CONFIG),
    "all default to false. This is what the bridge reports at connect and what narrows the "
    "manifest, so a duck built without a camera loses exactly the verbs that need one",
)
SOUNDS = UpstreamRef(
    "Sounds(volume, sound_directory)",
    "VERIFIED",
    src(f"{_PKG}/sounds.py"),
    "pygame.mixer over whatever .wav files it finds, with play(name), play_random_sound() "
    "and play_happy(). There is no text to speech anywhere in the runtime, which is why "
    "quackd's say logs the text and plays a mood sound",
)
CAM = UpstreamRef(
    "Cam.get_encoded_image()",
    "VERIFIED",
    src(f"{_PKG}/camera.py"),
    "picamzero, 512 by 512, returned base64. Too slow to run inside a 20 ms control tick",
)
WALK_LOOP_OPENS_NO_CAMERA = UpstreamRef(
    "v2_rl_walk_mujoco.py references no camera",
    "VERIFIED",
    src(_WALK),
    "read 2026-09-05: the walk loop imports and constructs nothing camera-related, so the "
    "process the bridge runs never opens the device. camd refusing to start whenever "
    "expression_features.camera is true was therefore avoiding a contention that this "
    "process cannot cause. It warns instead - some other upstream script might still open "
    "it, and two owners of one camera is a real failure, just not this one",
)
IMU_CLASS = UpstreamRef(
    "raw_imu.Imu.get_data() -> {gyro, accelero}",
    "VERIFIED",
    src(f"{_PKG}/raw_imu.py"),
    "read 2026-09-05: the walk loop imports Imu from raw_imu (NOT imu, whose own get_data "
    "returns a quaternion), and its get_data returns a dict of two 3-vectors: gyro in rad/s "
    "and accelero in m/s^2, from a BNO055. It remaps axes Y->X, X->Y, Z->Z and flips signs "
    "depending on duck_config.imu_upside_down, and subtracts a tare offset from accelero[0]. "
    "There is no fall or tilt detection anywhere in it",
)
ANTENNAS = UpstreamRef(
    "Antennas.set_position_left(position), .set_position_right(position)",
    "VERIFIED",
    src(f"{_PKG}/antennas.py"),
    "two 9 g servos on board pins D13 and D12, positions in -1..1",
)
IMU_CHECK_SERVER = UpstreamRef(
    "scripts/imu_server.py",
    "VERIFIED",
    src("scripts/imu_server.py"),
    "the only network-facing script in the runtime, and it only checks the IMU frame",
)
NO_CONTROL_SERVER = UpstreamRef(
    "the runtime has no network control API",
    "VERIFIED",
    src(""),
    "a full listing of the 34 Python files at the pin shows no control server: the only "
    "command source is a local pygame gamepad. This is the entire reason the bridge exists",
)
NO_KICK_NO_SIT_NO_GETUP = UpstreamRef(
    "the runtime has no kick, sit or get-up policy",
    "VERIFIED",
    src(""),
    "no module or script implements any of them, and there is no gripper or beak. quackd "
    "does not gate these verbs, it never declares them. A fallen v2 duck needs a human",
)
NO_BATTERY_READOUT = UpstreamRef(
    "the runtime reports no battery percentage",
    "VERIFIED",
    src(""),
    "scripts/check_voltage.py reads a pack voltage offline; nothing converts it to a "
    "percentage. battery_percent is always None and a battery abort cannot fire",
)
WALK_POLICY = UpstreamRef(
    "BEST_WALK_ONNX_2.onnx",
    "VERIFIED",
    hub("BEST_WALK_ONNX_2.onnx"),
    "at the root of the Apache-2.0 design repository. quackd links to it and never "
    "vendors, caches or ships it; the owner fetches it onto their own robot",
)

# ── UNVERIFIED: what the bridge assumes, and what quackd does about it ───────────────────

PAD_SUBSTITUTION = UpstreamRef(
    "from mini_bdx_runtime.xbox_controller import XBoxController",
    "VERIFIED",
    src(_WALK),
    "read 2026-09-05: upstream uses the from-import form and constructs the class inside "
    "RLWalk.__init__ under `if self.commands:`, which argparse defaults to true. The bridge "
    "rebinds the module attribute before `runpy` executes the script, so the from-import "
    "resolves to quackd's factory. Upstream still offers no integration point, so this "
    "remains a substitution rather than an API: the bridge refuses to serve if its factory "
    "was never called, and a test drives the real rebind against a fake runtime",
)
CONTROL_RATE_OF_READS = UpstreamRef(
    "self.last_commands, self.buttons, lt, rt = self.xbox_controller.get_last_command()",
    "VERIFIED",
    src(_WALK),
    "read 2026-09-05: called once per control-loop iteration at control_freq (50 Hz), not at "
    "the separate command_freq of 20 Hz. This is what quackd measures its loop rate from, so "
    "MIN_LOOP_HZ can be judged against 50. Note the call happens BEFORE upstream's pause "
    "check, and a paused loop sleeps 0.1 s a tick, so a paused duck reports about 10 Hz",
)
SOUND_BUTTON = UpstreamRef(
    "self.buttons.B.triggered",
    "VERIFIED",
    src(_WALK),
    "read 2026-09-05: B plays a random sound, A toggles pause, X toggles the projector, LB "
    "sets a phase-frequency factor and the dpad nudges a phase offset. quackd pulses only B, "
    "and never A: pause is a toggle whose real state the bridge cannot read",
)
ANTENNA_TRIGGERS = UpstreamRef(
    "self.antennas.set_position_left(right_trigger)",
    "VERIFIED",
    src(_WALK),
    "read 2026-09-05: the walk loop drives the antennas from the pad's trigger values, and "
    "cross-wires them (left trigger to the right antenna). quackd's gestures are symmetric, "
    "so the crossing does not change what they look like",
)
COMMAND_TTL = UpstreamRef(
    "COMMAND_TTL",
    "UNVERIFIED",
    src(_WALK),
    "nothing upstream zeroes the command on silence, because a local pad is never silent. "
    "The bridge zeroes the three velocities when no command has arrived recently, inside "
    "the consumer, so a dead server thread still stops the duck. It reports the window at "
    "connect and the manifest claims a deadman only when a bridge says it has one",
)
HEAD_APPLIED_UNCONDITIONALLY = UpstreamRef(
    "self.motor_targets[5:9] = self.last_commands[3:] + self.motor_targets[5:9]",
    "VERIFIED",
    src(_WALK),
    "read 2026-09-05: the walk loop writes the four head slots on every tick with no "
    "conditional, no mode flag and no toggle, and it never reads button Y at all. The "
    "head-control mode button lives inside XBoxController, which the bridge replaces, so "
    "writing the head slots takes effect without pressing anything. quackd still keeps head "
    "control off unless asked, and clamped, because upstream's README warns it can break the "
    "head - but the reason is the hardware, not an unknown",
)
FALL_SIGNAL = UpstreamRef(
    "FALL_SIGNAL",
    "UNVERIFIED",
    src("scripts/imu_server.py"),
    "upstream has no fall flag, and IMU_CLASS confirms there is no tilt detection to borrow. "
    "The accelerometer would give it away - gravity moves off the upright axis - but which "
    "axis is upright depends on the axis remap, on duck_config.imu_upside_down and on a tare "
    "offset, so it cannot be known without a duck to hold still. quackd therefore does NOT "
    "guess: it reports posture unknown, says fall-blind in every observation, and asks the "
    "operator once whether they are watching. A fall detector that is wrong is worse than "
    "none, because the failure mode is a confident 'not fallen'. Closing this needs someone "
    "to record accelero with the duck upright and on its side",
)
SOUND_FILE_NAMES = UpstreamRef(
    "SOUND_FILE_NAMES",
    "UNVERIFIED",
    src(f"{_PKG}/sounds.py"),
    "the .wav names in a given build's sound directory were not read and vary per owner, "
    "so quackd never spells one. It sends a mood from its own small vocabulary and the "
    "bridge resolves it against what is actually installed, falling back to a random sound",
)
ANTENNA_GESTURES = UpstreamRef(
    "ANTENNA_GESTURES",
    "UNVERIFIED",
    src(f"{_PKG}/antennas.py"),
    "upstream exposes servo positions, not named gestures, so perk, droop and wiggle are "
    "quackd's own vocabulary, turned into positions by the bridge. Antennas.set_position "
    "accepts -1..1 with 0 as rest, and the walk loop passes the trigger value straight "
    "through with no clamping (both read 2026-09-05) - so quackd's negative droop does reach "
    "the servo, even though a physical trigger axis only produces 0..1 and upstream's own "
    "pad could never ask for it. What no one has watched is what the two 9 g servos do there",
)
LOOP_HEADROOM = UpstreamRef(
    "LOOP_HEADROOM",
    "UNVERIFIED",
    src(_WALK),
    "how much CPU a 50 Hz ONNX loop leaves on a Raspberry Pi Zero 2 W with 512 MB is not "
    "measured by us. The bridge reports its observed loop rate and quackd's heartbeat "
    "fails below a floor, so a starved control loop aborts a run instead of walking badly",
)


def all_refs() -> list[UpstreamRef]:
    return [v for v in globals().values() if isinstance(v, UpstreamRef)]


def refs_by_status(status: str) -> list[UpstreamRef]:
    return [r for r in all_refs() if r.status == status]
