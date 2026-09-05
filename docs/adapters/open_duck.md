# Open Duck Mini v2

An open hardware 3D printed biped duck, about 42 cm tall, that walks on its own 50 Hz ONNX
policy on a Raspberry Pi Zero 2 W. It is the first robot quackd supports that you can build
yourself today, and the reason this adapter exists. The `sim2d` and `mock` backends run
offline; the `bridge` backend talks to a daemon quackd ships for the duck's Pi, and has
**never been run against a physical duck by us**.

Everything else, including every LLM call, runs off this Pi entirely: on your laptop or in
the cloud, talking to the two daemons below over the network (see the
[hardware checklist](../open-duck-hardware-checklist.md) for the exact steps).

Upstream: [apirrone/Open_Duck_Mini](https://github.com/apirrone/Open_Duck_Mini) (the design,
Apache-2.0) and
[apirrone/Open_Duck_Mini_Runtime](https://github.com/apirrone/Open_Duck_Mini_Runtime) (the
on-robot code). Read on 2026-09-03 at `3203734` and `b23317a`.

```bash
quackd list-verbs --robot open_duck:sim2d
quackd run open-duck-scout --robot open_duck:sim2d --provider fake
quackd validate ducks/find-and-kick.duck --robot open_duck:sim2d
# exit 1: requires kick, but open-duck-01 (open-duck-mini-v2) does not provide it
```

## Backends

| `--robot` | What it is | Status |
|---|---|---|
| `open_duck:sim2d` | a duck in the cartoon world | ✅ `open-duck-scout` 10 of 10 seeds |
| `open_duck:mock` | scripted, for tests | ✅ |
| `open_duck:bridge` | a real duck, through quackd's daemon on its Pi | 🧪 never run on a duck |

## What this duck cannot do

This matters more than what it can, and it is why the manifest is short.

| Not here | Why |
|---|---|
| `kick` | no kick policy exists in the runtime |
| `grab` | no beak, no gripper, no hardware to grab with |
| `sit`, `stand` | no sit policy exists |
| `stand_up` | **no get-up-after-fall policy exists.** A fallen v2 duck needs a human |
| a battery abort | nothing in the runtime reports a battery percentage |
| obstacle avoidance | no depth sensor in the official build, and quackd does not read one yet even where it exists (the Microduck's own TOF stream is unused too); the only sensing is a colour camera, so the duck steers toward or away from a detected target and avoids nothing else |

These verbs are not gated off. They are never declared, so they do not exist for this robot
in the registry, in the MCP tool list, in `.duck` validation or in the prompt. A task that
requires one is refused before a run starts, with the validator's own sentence.

When the duck is down, `move` and `gaze` refuse with a message that names no verb and says a
human must stand it up, because nothing quackd can call will recover it. A task pointed at
this robot should say so in its body, and both starter tasks do.

## The manifest

A fully built duck. A real one is whatever its owner soldered, so the `bridge` backend
narrows this at connect from the `expression_features` flags in the robot's own
`duck_config.json`, and from whether the daemon was started with head control enabled.

| Verb | Kind | Present when |
|---|---|---|
| `report_state`, `stop`, `move` (alias `walk`) | core | always |
| `observe` (alias `get_frame`), `go_to` (alias `walk_to`), `search_scan`, `approach_and` | core | the duck has a camera |
| `say`, `quack` | core, extension | the duck has a speaker |
| `gaze` | extension | head control is enabled |
| `express` | extension | the duck has antennas |

Velocities are clamped to the runtime's own numbers: 0.15 m/s forward, 0.2 m/s sideways,
1.0 rad/s turning. Ask for more and it is silently clamped, and the verb says so.

`search_scan` **turns the whole body** on this robot, because `scan_mode()` picks turning
for any robot with legs and a twist. That is right for a neck that moves about 23 degrees,
but it is slower and noisier than a head sweep, and it is the most surprising thing about
driving a legged robot that also has a head. `ducks/open-duck-lookout.duck` deliberately
leaves `search_scan` out for exactly this reason.

`say` has no voice behind it. There is no text to speech anywhere in the runtime, so the
text is logged verbatim and voiced as the closest of the duck's own sounds, the way the
Reachy's does ([ADR-0023](../adr/0023-reachy-mini.md), [ADR-0024](../adr/0024-open-duck-mini.md)).

## The bridge daemon

The robot has no network control API at all, so `bridge/open_duck/` holds a daemon that runs
on its Pi. It does not reimplement the control loop: it rebinds the class upstream imports
to read a gamepad, then runs upstream's own script, so there is one process, one owner of
the Feetech serial bus, and nothing of upstream's copied.

The protocol is quackd's own, NDJSON JSON-RPC 2.0 over TCP, with methods `duck.hello`,
`duck.command`, `duck.stop`, `duck.state`, `duck.health`, `duck.sound` and `duck.antennas`.
It is deliberately not the Microduck's `robot.move` and `robot.health`: those are a different
robot's `duck-ipc-proto` API v16, and reusing the words would make a transcript ambiguous
about which robot moved.

Safety, in the order it matters:

- The deadman is **quackd's own, running on the robot**, and it is evaluated inside the call
  the control loop makes every tick rather than by a timer. No command for the configured
  window (300 ms by default, `--deadman-ms`) and the three velocities go to zero, even if the
  server thread is starved, wedged or dead. The bridge reports the window it is actually
  enforcing at connect; quackd refuses to drive one outside 200-500 ms, and the manifest
  claims a deadman only when a bridge has said it has one. `quackd doctor` prints it.
- It **holds the head** instead of zeroing it. A velocity dropping to zero is what releasing
  a stick does; a neck snapping to centre is what upstream warns can break the head.
- **Going limp is unreachable.** The only channel from the network to the body is seven
  floats and a few buttons, so no message can reach a torque register.
- Head control is **off unless you ask**, then clamped to 80 percent of the runtime's range
  and rate limited to 1 rad/s of elapsed time, inside the control loop. quackd never presses
  upstream's head-control mode button.
- The bridge **binds loopback by default** and wants a token. Prefer
  `ssh -L 9871:127.0.0.1:9871 your-pi`. This port walks a robot. If the token file exists but
  the service user cannot read it, the bridge **refuses to start** rather than running with
  authentication silently disabled, and it reports `auth: token` or `auth: none` at connect
  so the difference is visible from the laptop.
- The only e-stop is the power switch.

Every flag, the token, the ports, `--fake`, and how to get these files onto a Pi at all:
[`bridge/open_duck/README.md`](../../bridge/open_duck/README.md). The order to bring a real
duck up in: [open-duck-hardware-checklist.md](../open-duck-hardware-checklist.md).

### The camera is a second process

Frames do not travel over the bridge's socket. Encoding a 512 by 512 JPEG inside a 20 ms
control tick is not affordable on a Pi Zero 2 W, and picamzero in the walk process would
cost tens of megabytes on a 512 MB board. So the camera lives in
`quackd_duck_camd.py`, its own process with its own memory limit and an OOM score that makes
the kernel take it before it takes the walk loop. It captures on a timer rather than on
request, so a slow client cannot stall the capture, and serves the newest frame at
`/snapshot.jpg` with a `/healthz` beside it. quackd fetches it directly, the way
`--camera-url` already works for `microduck:jsonrpc`.

It reads a camera and answers GET. There is no control path in that file at all, so the
camera port cannot move the robot even if you expose it.

Start it first and point the bridge at it with `--camera-url`. **The URL is what decides
whether this duck has a camera as far as quackd is concerned**, because it is the only place
frames can come from; `expression_features.camera` decides who owns the *device*. Without a
URL the bridge advertises no camera, and `observe`,
`go_to`, `search_scan` and `approach_and` do not exist for that duck rather than existing
and failing. On camera ownership: upstream's walk loop — the script the bridge runs — opens no camera at
all, so `camd` and the bridge cannot be fighting over the device. `camd` used to refuse to
start when `expression_features.camera` was true and now only warns, because the collision it
was avoiding cannot happen in that process. Setting the flag false is still tidier, and if
you run one of upstream's *own* camera scripts alongside, the two really will contend — which
now shows up honestly, as failing captures and expiring snapshots rather than a frozen frame.

### What is honestly degraded on hardware

The manifest is honest about what exists. These are the places where a verb exists and does
less than its name suggests, and they are worth knowing before you write a task.

| Thing | What actually happens |
|---|---|
| `say`, `quack` | The only channel the bridge has to the speaker is upstream's random-sound button, so the mood quackd picks is logged but selects nothing. A duck says *something*, not the right thing |
| a fall | Nothing detects one. `posture` reads `unknown`, never `standing`, and the `not_fallen` precondition never fires on hardware. Every observation says `fall-blind`, `doctor` says so, and a task that can make the duck walk asks you once whether you are watching. You are the fall detector |
| a battery | Nothing reports one, so `abort_when: battery below N%` parses and can never fire |
| `gaze` | Off unless the daemon was started with head control enabled. The head slots *are* written every tick with no mode button involved (the mode button lives inside the pad class the bridge replaced), so this works — it is off by default because upstream warns the neck can be damaged, not because it is unknown. Upstream *adds* the four values to the walk policy's own head targets, so they are offsets from wherever the policy is holding the head, not absolute joint angles |

## VERIFIED (read from upstream source on 2026-09-03)

| Thing | Used for |
|---|---|
| `apirrone/Open_Duck_Mini_Runtime` | the on-robot code, branch `v2` |
| `Open_Duck_Mini_Runtime has no LICENSE file` | all rights reserved: never vendored, never a dependency |
| `apirrone/Open_Duck_Mini` | Apache-2.0: the design and the walk policy |
| `mini_bdx_runtime` | the import name, used only by the daemon on your Pi |
| `scripts/v2_rl_walk_mujoco.py` | the walk loop the daemon runs |
| `RLWalk` | the class that owns it |
| `--onnx_model_path, --duck_config_path, --control_freq` | passed through verbatim |
| `50` | the control rate in Hz |
| `self.xbox_controller = XBoxController(self.command_freq)` | the one construction site the daemon replaces |
| `from mini_bdx_runtime.xbox_controller import XBoxController` | the import form, which is why rebinding the module attribute before `runpy` works |
| `self.last_commands, self.buttons, lt, rt = self.xbox_controller.get_last_command()` | read once per control tick at 50 Hz, and before the pause check |
| `self.buttons.B.triggered` | the random-sound button, the only one quackd presses |
| `self.antennas.set_position_left(right_trigger)` | the antennas are driven from the pad's triggers, cross-wired |
| `vx, vy, vyaw, neck_pitch, head_pitch, head_yaw, head_roll` | the seven floats the policy consumes |
| `vx +-0.15, vy +-0.2, vyaw +-1.0, neck_pitch -0.34..1.1, head_pitch -0.78..0.3, head_yaw +-0.5, head_roll +-0.5` | every clamp quackd obeys |
| `XBoxController(command_freq, only_head_control=False)` | the shape the daemon's shim answers |
| `A, B, X, Y, LB, RB, dpad_up, dpad_down` | the buttons; quackd never presses Y |
| `HWI(duck_config, usb_port="/dev/ttyACM0")` | the servo bus, which quackd never opens itself |
| `left_hip_yaw 20, left_hip_roll 21, left_hip_pitch 22, left_knee 23, left_ankle 24, neck_pitch 30, head_pitch 31, head_yaw 32, head_roll 33, right_hip_yaw 10, right_hip_roll 11, right_hip_pitch 12, right_knee 13, right_ankle 14` | the fourteen joints, which do have `head_roll` |
| `DuckConfig(config_json_path="~/duck_config.json")` | offsets and flags |
| `expression_features.eyes, .projector, .antennas, .speaker, .microphone, .camera` | what narrows the manifest at connect |
| `Sounds(volume, sound_directory)` | the speaker, and the absence of any text to speech |
| `Cam.get_encoded_image()` | the camera, too slow for a 20 ms tick |
| `v2_rl_walk_mujoco.py references no camera` | the walk loop opens no camera, so camd cannot be fighting it |
| `raw_imu.Imu.get_data() -> {gyro, accelero}` | the IMU the loop actually uses, and what it returns |
| `self.motor_targets[5:9] = self.last_commands[3:] + self.motor_targets[5:9]` | the head slots are written every tick, unconditionally, and `Y` is never read |
| `Antennas.set_position_left(position), .set_position_right(position)` | `express` |
| `scripts/imu_server.py` | the only network-facing script upstream has |
| `the runtime has no network control API` | why the bridge exists |
| `the runtime has no kick, sit or get-up policy` | why those verbs are never declared |
| `the runtime reports no battery percentage` | why a battery abort cannot fire |
| `BEST_WALK_ONNX_2.onnx` | the walk policy, linked and never vendored |

## UNVERIFIED (an assumption of ours) and what quackd does

| Thing | What quackd does |
|---|---|
| `COMMAND_TTL` | quackd's own deadman, in the consumer, reported at connect; the manifest claims one only when a bridge says it has one |
| `FALL_SIGNAL` | a bridge that can see the IMU latches a fall; one that cannot reports posture unknown. Either way there is no recovery to attempt |
| `SOUND_FILE_NAMES` | quackd sends a mood from its own vocabulary and the bridge resolves it, never spelling a `.wav` name |
| `ANTENNA_GESTURES` | perk, droop and wiggle are quackd's words, turned into servo positions by the bridge |
| `LOOP_HEADROOM` | the bridge reports its loop rate and quackd's heartbeat fails below 35 Hz, so a starved Pi aborts a run instead of walking badly |

## How to help

Built one? `open_duck:bridge` is the row most likely to flip to ✅ this year, and it needs a
person with a duck. [`docs/open-duck-hardware-checklist.md`](../open-duck-hardware-checklist.md)
says what to run and in what order, and what to attach to an issue. Start with
`open-duck-lookout`, whose allowlist moves no legs at all.
