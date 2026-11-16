# Open Duck Mini v2

An open hardware 3D printed biped duck, about 42 cm tall, that walks on its own 50 Hz ONNX
policy on a Raspberry Pi Zero 2 W. It is the first robot Roboglot supports that you can build
yourself today, and the reason this adapter exists. The `mock` backend runs offline. The
`bridge` backend talks to a daemon Roboglot ships for the duck's Pi, and has **never been run
against a physical duck by us**.

Everything else, including every LLM call, runs off this Pi entirely: on your laptop or in
the cloud, talking to the two daemons below over the network (see the
[hardware checklist](hardware-checklist.md) for the exact steps).

Upstream: [apirrone/Open_Duck_Mini](https://github.com/apirrone/Open_Duck_Mini) (the design,
Apache-2.0) and
[apirrone/Open_Duck_Mini_Runtime](https://github.com/apirrone/Open_Duck_Mini_Runtime) (the
on-robot code). Read on 2026-09-03 at `3203734` and `b23317a`.

```bash
uv pip install 'roboglot[open_duck]'

roboglot list-verbs --robot open_duck:mock
roboglot run open-duck-scout --robot open_duck:mock --llm fake
roboglot validate tasks/find-and-kick.glot --robot open_duck:mock
# exit 1: requires kick, but open-duck-01 (open-duck-mini-v2) does not provide it
```

That extra dates from 0.10.0, and it installs exactly one package: `roboglot-open-duck`,
Roboglot's own Apache-2.0 adapter, whose only dependency is Roboglot itself. It still pulls nothing
from upstream, and that is deliberate. The Open Duck Mini Runtime has no license file, so Roboglot
never vendors it and never depends on it; the daemon below is copied onto the duck's own Pi
and lives beside upstream's code there, not in any wheel
([reference/licenses.md](../../reference/licenses.md)). Before 0.10.0 the adapter was part of the
core wheel, which is why there was no extra to name.

## Backends

| `--robot` | What it is | Status |
|---|---|---|
| `open_duck:mock` | scripted, for tests | ✅ |
| `open_duck:bridge` | a real duck, through Roboglot's daemon on its Pi | 🧪 never run on a duck |

## What this duck cannot do

This matters more than what it can, and it is why the manifest is short.

| Not here | Why |
|---|---|
| `kick` | no kick policy exists in the runtime |
| `grab` | no beak, no gripper, no hardware to grab with |
| `sit`, `stand` | no sit policy exists |
| `stand_up` | **no get-up-after-fall policy exists.** A fallen v2 duck needs a human |
| a battery abort | nothing in the runtime reports a battery percentage |
| obstacle avoidance | no depth sensor in the official build, and Roboglot does not read one yet even where it exists (the Microduck's own TOF stream is unused too); the only sensing is a colour camera, so the duck steers toward or away from a detected target and avoids nothing else |

These verbs are not gated off. They are never declared, so they do not exist for this robot
in the registry, in the MCP tool list, in `.glot` validation or in the prompt. A task that
requires one is refused before a run starts, with the validator's own sentence.

When the duck is down, `move` and `gaze` refuse with a message that names no verb and says a
human must stand it up, because nothing Roboglot can call will recover it. A task pointed at
this robot should say so in its body, and `open-duck-scout` does.

Its datasheet, which the pilot is shown and told to judge a task against before anything moves ([reference/manifest-spec.md](../../reference/manifest-spec.md)):

| | |
|---|---|
| Height | 0.42 m (official: the Open Duck Mini v2 repository) |
| Actuated joints | 14 (estimate: the BOM and the runtime's joint list; STS3215 servos, which the BOM states) |
| Not published | mass, endurance |

And what it cannot do whatever the task says, which is the half a refusal usually turns on, in the words the pilot is shown:

- pick up, push or carry anything: there is no arm, no gripper and no beak
- get back on its feet after a fall: there is no recovery policy, so a fall ends the run and needs a human

A figure nobody published is listed as not published, and the pilot is told to answer `uncertain` and name it, rather than guess, where a task turns on it. A `.glot` file can correct any of it for the build in front of you ([reference/glot-spec.md](../../reference/glot-spec.md)).

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
driving a legged robot that also has a head. `tasks/open-duck-lookout.glot` deliberately
leaves `search_scan` out for exactly this reason.

`say` has no voice behind it. There is no text to speech anywhere in the runtime, so the
text is logged verbatim and voiced as the closest of the duck's own sounds
([ADR-0024](../../adr/0024-open-duck-mini.md)).

## The bridge daemon

The robot has no network control API at all, so `bridge/open_duck/` holds a daemon that runs
on its Pi. It does not reimplement the control loop: it rebinds the class upstream imports
to read a gamepad, then runs upstream's own script, so there is one process, one owner of
the Feetech serial bus, and nothing of upstream's copied.

The protocol is Roboglot's own, NDJSON JSON-RPC 2.0 over TCP, with methods `duck.hello`,
`duck.command`, `duck.stop`, `duck.state`, `duck.health`, `duck.sound` and `duck.antennas`.
It is deliberately not the Microduck's `robot.move` and `robot.health`: those are a different
robot's `duck-ipc-proto` API v50, and reusing the words would make a transcript ambiguous
about which robot moved.

Safety, in the order it matters:

- The deadman is **Roboglot's own, running on the robot**, and it is evaluated inside the call
  the control loop makes every tick rather than by a timer. No command for the configured
  window (300 ms by default, `--deadman-ms`) and the three velocities go to zero, even if the
  server thread is starved, wedged or dead. The bridge reports the window it is actually
  enforcing at connect; Roboglot refuses to drive one outside 200-500 ms, and the manifest
  claims a deadman only when a bridge has said it has one. `roboglot doctor` prints it.
- It **holds the head** instead of zeroing it. A velocity dropping to zero is what releasing
  a stick does; a neck snapping to centre is what upstream warns can break the head.
- **Going limp is unreachable.** The only channel from the network to the body is seven
  floats and a few buttons, so no message can reach a torque register.
- Head control is **off unless you ask**, then clamped to 80 percent of the runtime's range
  and rate limited to 1 rad/s of elapsed time, inside the control loop. Roboglot never presses
  upstream's head-control mode button.
- The bridge **binds loopback by default**, and bound wider with no token it **refuses to
  start** unless `--allow-no-token` says that is on purpose. Prefer
  `ssh -L 9871:127.0.0.1:9871 your-pi`. This port walks a robot, and it has no TLS, so its
  token and every command cross the network in the clear unless a tunnel carries them. If a
  token file named with `--token-file` is missing, or the service user cannot read the token
  file, or it is empty, the bridge **refuses to start** rather than running with
  authentication silently disabled, and it reports `auth: token` or `auth: none` at connect
  so the difference is visible from the laptop.
- A shutdown (SIGTERM or Ctrl-C) zeroes the command and **refuses every command** until the
  loop has settled and ended, and says so: `move` and `gaze` fail with `the bridge is shutting
  down; nothing moves now`, and so does the heartbeat. A command with a number that is not
  finite is refused whole.
- The only e-stop is the power switch.

Every flag, the token, the ports, `--fake`, and how to get these files onto a Pi at all:
[`bridge/open_duck/README.md`](../../../bridge/open_duck/README.md). The order to bring a real
duck up in: [adapters/open_duck/hardware-checklist.md](hardware-checklist.md).

### The camera is a second process

Frames do not travel over the bridge's socket. Encoding a 512 by 512 JPEG inside a 20 ms
control tick is not affordable on a Pi Zero 2 W, and picamzero in the walk process would
cost tens of megabytes on a 512 MB board. So the camera lives in
`roboglot_duck_camd.py`, its own process with its own memory limit and an OOM score that makes
the kernel take it before it takes the walk loop. It captures on a timer rather than on
request, so a slow client cannot stall the capture, and serves the newest frame at
`/snapshot.jpg` with a `/healthz` beside it. Roboglot fetches it directly, the way
`--camera-url` already works for `microduck:jsonrpc`, and takes a JPEG or a PNG from whatever
that URL names. A camera URL is http or https: a `--camera-url` with any other scheme is
refused before anything connects, by a sentence that names the two. A URL the bridge names is
followed only over HTTP or HTTPS to the host Roboglot connected to (loopback counts when it
reached the bridge on loopback, as the tunnel does). Any other is refused at connect, with the
reason in the camera's error, and the duck then has no camera unless you name one with
`--camera-url`. Each frame is fetched whole within one second or not at all, at most 8 MB of it,
with no proxy, and a redirect is followed only to the same scheme, host and port.

It reads a camera and answers GET. There is no control path in that file at all, so the
camera port cannot move the robot even if you expose it.

Start it first and point the bridge at it with `--camera-url`. **The URL is what decides
whether this duck has a camera as far as Roboglot is concerned**, because it is the only place
frames can come from; `expression_features.camera` decides who owns the *device*. Without a
URL the bridge advertises no camera, and `observe`,
`go_to`, `search_scan` and `approach_and` do not exist for that duck rather than existing
and failing. On camera ownership: upstream's walk loop — the script the bridge runs — opens no camera at
all, so `camd` and the bridge cannot be fighting over the device, and `camd` only warns when
`expression_features.camera` is true. Setting the flag false is still tidier, and if you run
one of upstream's *own* camera scripts alongside, the two really will contend, which shows up
as failing captures and expiring snapshots.

### What is honestly degraded on hardware

The manifest is honest about what exists. These are the places where a verb exists and does
less than its name suggests, and they are worth knowing before you write a task.

| Thing | What actually happens |
|---|---|
| `say`, `quack` | The only channel the bridge has to the speaker is upstream's random-sound button, so the mood Roboglot picks is logged but selects nothing. A duck says *something*, not the right thing |
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
| `self.buttons.B.triggered` | the random-sound button, the only one Roboglot presses |
| `self.antennas.set_position_left(right_trigger)` | the antennas are driven from the pad's triggers, cross-wired |
| `vx, vy, vyaw, neck_pitch, head_pitch, head_yaw, head_roll` | the seven floats the policy consumes |
| `vx +-0.15, vy +-0.2, vyaw +-1.0, neck_pitch -0.34..1.1, head_pitch -0.78..0.3, head_yaw +-0.5, head_roll +-0.5` | every clamp Roboglot obeys |
| `XBoxController(command_freq, only_head_control=False)` | the shape the daemon's shim answers |
| `A, B, X, Y, LB, RB, dpad_up, dpad_down` | the buttons; Roboglot never presses Y |
| `HWI(duck_config, usb_port="/dev/ttyACM0")` | the servo bus, which Roboglot never opens itself |
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

## UNVERIFIED (an assumption of ours) and what Roboglot does

| Thing | What Roboglot does |
|---|---|
| `COMMAND_TTL` | Roboglot's own deadman, in the consumer, reported at connect; the manifest claims one only when a bridge says it has one |
| `FALL_SIGNAL` | the bridge does not read the IMU, so it never reports a fall and posture reads unknown. There is no recovery to attempt anyway |
| `SOUND_FILE_NAMES` | Roboglot sends a mood from its own vocabulary and never spells a `.wav` name; the bridge logs it and presses the random-sound button |
| `ANTENNA_GESTURES` | perk, droop and wiggle are Roboglot's words, turned into servo positions by the bridge |
| `LOOP_HEADROOM` | the bridge reports its loop rate and Roboglot's heartbeat fails below 35 Hz, so a starved Pi aborts a run instead of walking badly |
| the walk loop on a KeyboardInterrupt | nobody here has read what upstream's walk loop does with the KeyboardInterrupt that ends the bridge's shutdown settle: whether it de-energises the servos, or catches it and loops on. The bridge sends it after the settle so the loop's own handling can run. The hard exit stays behind it: a second SIGTERM or Ctrl-C exits at once, and systemd kills the unit at its `TimeoutStopSec` |

## How to help

Built one? `open_duck:bridge` is the row most likely to flip to 🤖 this year, and it needs a
person with a duck. [`docs/adapters/open_duck/hardware-checklist.md`](hardware-checklist.md)
says what to run and in what order, and what to attach to an issue. Start with
`open-duck-lookout`, whose allowlist moves no legs at all.
