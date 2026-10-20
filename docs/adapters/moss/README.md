# MOSS

A small tracked rover that picks up litter: two tracks, a bin on top, and an arm derived from the
SO-101 with a parallel gripper widened to 90 mm, which drops what it picks into the bin. It is
mostly 3D printed and built by its owner from a bill of materials, by Laurent Genoud (Show
Robotics, Thailand). The maker's own word on where it stands, in the README at the pin: "The
physical rover drives and demonstrates pick-and-drop motions. Autonomous litter picking and the
full field test are not yet demonstrated."

Upstream: [metrox-eth/moss](https://github.com/metrox-eth/moss). Its code is Apache-2.0, its
hardware design files CERN-OHL-S-2.0 and its documentation CC BY 4.0. Read on **2026-10-20**,
pinned at
[`d288647`](https://github.com/metrox-eth/moss/tree/d288647adb2b7a132a69d89afa2f1b45d37b288a)
(`main`, 2026-10-18). MOSS's software is `moss_dimos`, a blueprint package for dimOS, which is
read at
[`a7d19f7`](https://github.com/dimensionalOS/dimos/tree/a7d19f76158b01739b26e7a17390f89764aaef83),
the commit MOSS's own Software.md installs. Neither has a software release, and MOSS's CHANGELOG
says nothing is frozen, so a commit is the only honest pin, and every name was read from code
rather than from docs. Every name quackd spells lives in
[`adapters/moss/src/quackd_moss/upstream_api.py`](../../../adapters/moss/src/quackd_moss/upstream_api.py).

**quackd has never driven a MOSS rover.** The `cockpit` backend is exercised against a fake of
MOSS's cockpit and against quackd's own bridge in `--fake`, both over loopback, and once by hand
against MOSS's own cockpit in its `--demo` mode ([below](#the-check-against-mosss-own-cockpit)).
Nothing here has run on a rover.

```bash
uv pip install 'quackd[moss]'     # quackd's own adapter package, standard library only

# offline, the default
quackd run moss-lookout --robot moss:mock --llm fake

# a real rover, once its cockpit, quackd's bridge and the tunnel are up (see "Before it will answer")
quackd run moss-lookout --robot moss:cockpit --address http://127.0.0.1:8904
```

## Backends

| `--robot` | What it is | Status |
|---|---|---|
| `moss:mock` | the rover in memory: the tracks with their power floor and full power pivots, the ramp, the planner, the explorer, the arm cycle, the litter mission and the idle stop, with a synthetic nose and wrist camera | ✅ every verb runs offline in the test suite |
| `moss:cockpit` | the real rover: MOSS's own cockpit over HTTP, and quackd's bridge on the rover for what the cockpit has no command for | 🧪 names VERIFIED at the pin, exercised against fakes over loopback and once against MOSS's `--demo` cockpit, **never run against a rover** |

The mock takes the rover's shape from its address, which a test or a dry run uses to see what a
rover without a bridge, an arm or a mission is offered:
`--address "mock://rover?stack=moss-nav&bridge=0"`. It reads `stack`, `bridge`, `arm`,
`mission`, `camera`, `stamps`, `wall` (a wall at that x, which stalls the tracks), `idle` (the
idle stop's limit in seconds) and `litter` (how many pieces of litter to lay out ahead).

## How quackd reaches it

```
laptop: quackd, moss:cockpit                 the rover, with MOSS's own .venv
  CockpitClient: HTTP :8904, no auth ──▶ MOSS's cockpit ──┐ LCM
  BridgeClient:  HTTP :9876, token ────▶ quackd's bridge ─┤──▶ the dimOS stack: moss-nav, moss-explore or moss-litter
                                              └── dimOS RPC ─▶ moss-arm (ArmCoordinator), LitterMission
```

**MOSS's cockpit** (`tools/moss_cockpit.py`) is the rover's only network surface. It is a
standard library HTTP server on port 8904 that taps the stack's LCM bus. quackd reads its
`/state.json` for the stack, the pose, the battery, the firmware's mode, the stall flag, the nav
state and the idle stop, and sends its commands: a goal for the planner, the explorer's start
and stop, the soft stop and the idle stop's keepalive. Every command carries the
`X-Requested-With: moss-cockpit` header the cockpit asks for.

**quackd's bridge** ([`bridge/moss/`](../../../bridge/moss/README.md)) is one file the owner copies
to the rover and runs with MOSS's own interpreter, beside the cockpit. It is what the cockpit
has no command for: a twist with a deadman of its own, a frame that says when it was taken, the
maker's arm cycle with a cancel that leaves the arm holding, and the litter mission's own stop.
It never imports quackd.

**quackd imports neither MOSS nor dimOS.** Both run on the rover, in MOSS's own environment, and
dimOS's RPC is pickled Python on the rover's LCM bus, which is nothing a laptop should join. So
`quackd[moss]` is `quackd-moss`, which speaks the two HTTP surfaces with the standard library and
installs anywhere quackd does.

## What this robot cannot do

- **Slide sideways.** It runs on two tracks, so `max_vy` is 0 and `vy` is dropped.
- **Turn slowly on the spot.** MOSS's base adapter raises any moving track below 20% to 20%, and
  sends every turn on the spot at 100% on both tracks, whatever the turn rate asked. An arc whose
  inner track reverses goes the same way. See [Motion](#motion-on-a-base-that-pivots-only-at-full-power).
- **Reach for anything.** The maker's cycle closes the jaws at one fixed point in front of the
  rover, so the body has to bring the object there. Where that point is, the maker marks to
  measure.
- **Speak.** There is a speaker, used by MOSS's own sound pack, and nothing quackd can say through,
  so there is no `say`.
- **Stop the litter mission any way but its own.** It listens to no stop at all: see
  [the stop contract](#the-stop-contract).

Its datasheet, which the pilot is shown and told to judge a task against before anything moves
([reference/manifest-spec.md](../../reference/manifest-spec.md)):

| | |
|---|---|
| Mass | 4.2 kg (official: the maker, in issue 2 of the MOSS repository, for the V0.3 prototype complete. The arm and gripper weigh 768 g on their own, and the V0.6 hull has not been weighed) |
| Actuated joints | 6 (official: the MOSS BOM, five ST3215 servos on the arm and a CF35-12 on the gripper, with the two tracks on top of that) |
| Manipulator | a gripper, one arm |
| Terrain | a flat indoor floor, which is what has been demonstrated. Not rated for stairs, steps, kerbs or slopes |
| Not published | payload, reach, endurance, height. Of the battery bay the maker writes that it is "not a claim about battery capacity or runtime" |

And its notes: 0.32 m/s at full power, measured by its maker. A 3S battery whose BMS cuts at
10.2 V, under the launcher's own floor of 10.8 V. Jaws that open to 90 mm. The maker's own
warning, a prototype with a lithium battery and no certified protection: "Put it on blocks for
the first run". And built for streets and beaches, with neither autonomous picking nor a field
test demonstrated yet.

## The manifest

| Field | Value |
|---|---|
| `embodiment` / `mobility` | `wheeled` / `wheeled`, and the frame note says tracks |
| `intents` | `twist` with the bridge, `pose` for a planner goal, `skill` for the explorer, the arm cycle and the mission |
| `sensors` | `camera`, `odometry`, `battery`, and `joint_state` when the arm answers |
| `limits` | `max_vx` 0.3, `max_vy` 0.0, `max_wz` 0.4, and `camera_fov_deg` only from the stack's own `camera_info` |
| `safety_authority` | `native: none`, `deadman: true` with the bridge and `false` without it, `heartbeat_hz: 2` |
| `extras` | the blueprint, the bridge's version, the track width, the stop ramp, the firmware and driver names, `deadman_scope`, `speech: none` and `assumptions` |

`max_vx` is quackd's schema cap of 0.3 m/s, which binds, since MOSS's full power is 0.32.
`max_wz` is MOSS's own `SERVO_W_MAX`, the litter mission's fastest servo turn. `deadman: true`
is claimed only when the bridge answers and can publish on the rover's bus, and
`extras.deadman_scope` says what its deadman covers: its own twist deadman, the arm cycle it
runs, and after three missed heartbeats whatever quackd started through the cockpit.

`quackd list-verbs` describes every stack's verbs together, which is what a task is validated
against before anything connects. Connect narrows it to what answered:

| Verbs | `moss-nav` | `moss-explore` | `moss-litter` |
|---|---|---|---|
| `observe`, `report_state`, `stop` | ✓ | ✓ | ✓ |
| with the bridge: `move`, `go_to`, `search_scan`, and `approach_and` with a camera (all core) | ✓ | ✓ | no: the mission owns the base |
| the planner: `drive_to`, `explore` | no planner | ✓ | no |
| with the bridge, the arm's coordinator answering and the maker's cycle found: `pick_and_drop`, `stow_arm` | ✓ | ✓ | no: the mission owns the arm |
| with the bridge and the mission answering: `collect_litter` | | | ✓ |

**Refused at connect**, each in a sentence that names the fix: no cockpit, a cockpit that answers
its state with an error, a cockpit in `--demo` mode, a bridge that refuses the token or speaks
another version of quackd's protocol, no stack, a stack that is not MOSS's or one of MOSS's that
quackd does not know, `moss-gamepad` or `moss-base` (the pad and the base alone, with no
odometry), the `moss-arm` run alone with no bridge to see the base stack behind it, a bus that
is not LCM, and `moss-litter` without the bridge or without the mission's RPC answering. A bridge
that answers but cannot publish on the rover's bus counts as no bridge, and says why. This is
the mock's, refused as the rover would be:

```
✗ error: moss:mock at mock://rover?stack=moss-litter&bridge=0: moss: on moss-litter the mission listens to no stop the cockpit can send, and only quackd's bridge can call the mission's own: start the
bridge on the rover (bridge/moss/README.md) (the mock was built with no bridge)
```

And this is a laptop with no tunnel up, on Windows:

```
✗ error: moss:cockpit at http://127.0.0.1:8904: moss: nothing answers at
http://127.0.0.1:8904/state.json (http://127.0.0.1:8904 did not answer GET /state.json: [WinError
10061] No connection could be made because the target machine actively refused it). Start the
cockpit on the rover (tools/moss-fly.sh cockpit) and reach it through the tunnel: ssh -L
8904:127.0.0.1:8904 -L 9876:127.0.0.1:9876 <rover>
```

**The preconditions**, each derived from an upstream constant and tested with synthetic values:

| Name | Refuses when |
|---|---|
| `cockpit_fresh` | the base's telemetry is older than four of the coordinator's 0.5 s periods, or its firmware line older than the cockpit's own 1 s rule |
| `pose_fresh` | the pose is older than four periods of the odometry rate the cockpit itself measured, and at least 2 s |
| `battery_ok` | the battery is under the launcher's own 10.8 V floor |
| `not_stalled` | the base's stall guard has stopped the tracks |
| `bridge_up` | quackd's bridge does not answer |
| `arm_idle` | an arm cycle is playing |
| `arm_folded` | the arm answers and is out of its drive pose, by the coordinator's own start tolerance, for every verb that moves the base |
| `explore_stack` | the stack is not `moss-explore` |
| `mission_up` | the litter mission's RPC does not answer, or the mission is stopped |

## Its own verbs

None of them is named `pick`, which is a policy verb.

| Verb | Class | What it does |
|---|---|---|
| `drive_to(x_m, y_m, frame)` | `safe`, as core `go_to` | a goal for MOSS's own planner, at most 2 m from the rover, in the body frame (forward and left) or along the map's axes. It trusts `goal_reached` only once the cockpit echoes the goal it sent, fails if another goal replaces it, stops on a stall or after 48 s, and always ends with a stop |
| `explore(minutes)` | `confirm` | MOSS's own explorer, at most 8 minutes. It checks for an interruption every second and ends with the explorer's stop and the soft stop, early if the explorer runs out of frontier |
| `collect_litter(minutes)` | `dangerous` | the rover handed to MOSS's own litter mission, at most 8 minutes: it explores, drives to any litter it confirms and runs the arm cycle by itself. It ends with the mission's own stop, and succeeds only if the bridge confirmed it |
| `pick_and_drop(pick_pose)` | `confirm` | the maker's fixed cycle, about 37 s: out of the drive pose, the jaws closed at one point in front, and whatever they hold into the bin. `pick` for an object standing, `pick_low` for one lying down. Every parameter has a default, so `approach_and(then="pick_and_drop")` can run it |
| `stow_arm` | `confirm` | the cycle from its own `back_to_clear`, which first moves the arm over the bin if it is elsewhere, then back to its drive pose. Nothing checks the scene on the way |

`collect_litter` works **once per stack start**. Every stop quackd sends on `moss-litter` ends the
mission, the `stop` verb's and the one at the end of every run among them, and a stopped mission
stays stopped until the owner restarts the stack. So does a `quackd doctor` that connects to
`moss-litter`: its close is a stop too, and doctor reports the stopped mission and exits 1.

Today the executor gives a `dangerous` verb the confirm a `confirm` verb gets, and leaves it out
of what may run with no task file, so `collect_litter` needs a `.duck` that allows it. The rule
that a `dangerous` verb runs only when a person actually answered, and never on `--yes`, a
standing answer or a client that answers for itself, is decided in
[ADR-0056](../../adr/0056-moss-through-its-cockpit-and-a-bridge.md) and lands with this adapter's
registration.

Over MCP, a verb that runs for minutes, `explore` and `collect_litter` among them, may outlast the
client's own timeout for one tool call.

## The stop contract

| What is moving | quackd's `stop` | A dropped link, with the bridge | Without the bridge |
|---|---|---|---|
| a twist, through the bridge | a zero on the pad channel, then the soft stop | the bridge's own 0.3 s twist deadman, one zero and then silence. Behind it, dimOS's 0.2 s velocity watchdog, the base adapter's 0.5 s and the firmware's 0.3 s | no twist verbs exist |
| a `drive_to` goal, through the cockpit | the soft stop's zero on `tele_cmd_vel`, which cancels the goal | after three missed heartbeats the bridge halts it: `stop_explore_cmd`, `stop_movement` and a zero | MOSS drives on to the goal, which is never more than 2 m away |
| `explore`, through the cockpit | `stop_explore_cmd` and `stop_movement`, then the soft stop | the bridge halts it the same way | MOSS explores on inside its radius, which the launcher sets to 4 m from where exploring started, until its frontier runs out. That is why `explore` is `confirm` |
| the arm cycle, through the bridge | the cancel: the script stopped, `cancel_trajectory`, the joints read twice, so the arm holds where it was | the bridge cancels it, so the arm holds | no arm verbs exist |
| the litter mission | the mission's own stop, confirmed, then the arm's cancel, then the zero and the soft stop | the bridge runs the whole stop | `moss-litter` is refused at connect |

- **The transport's `stop()`** is the bridge's `/stop` and then the cockpit's `/cmd/stop`, every
  time. Each failure is kept and reported, and neither half skips the other.
- **The mission's stop goes first.** A cycle cancelled under a mission still running makes the
  maker's script exit on its lag watch, the mission marks the pick failed, and its next tick
  sets the rover exploring again. The mock reproduces that race, and a test proves the order
  prevents it.
- **The mission's stop is confirmed by silence.** dimOS sends a module's stop without waiting
  for an answer, because the module closes its RPC service as it stops. So the bridge waits up
  to 4 s for `mission_status` to fall silent, and counts that silence only from a mission it
  heard answer before. A stop it cannot confirm is reported as a failure, the close note says
  the mission may still be running, and the arm's cancel and the zero go out either way.
- **A session that started nothing halts nothing.** The bridge halts what quackd started and
  nothing else, so a goal the owner clicked on the cockpit's own page is left alone.
- **`close()` never sends a go limp call**, and **quackd never posts `/cmd/stack`**, whose stop
  ends every dimOS run and turns the arm's torque off. A test reads the package's source to
  prove no code path can send either.
- **The arm and the idle stop.** A run that ends with the arm out of its drive pose leaves it
  holding, and the close note says so: the cockpit's idle stop ends an idle `moss-nav` or
  `moss-explore` after 300 s with `tools/moss-fly.sh stop`, which ends the arm's run too, and
  the arm's adapter turns its torque off as its run ends. quackd sends the cockpit's keepalive
  while it is connected, and nothing does after.
- **How long a stop takes** is the firmware's own ramp, which the bridge reads from the stack
  and reports: with MOSS's launcher, 5 ms per percent, half a second from full power.

## Motion on a base that pivots only at full power

No core verb is overridden. The bridge shapes every twist on the rover, where the timing is real,
and every rule is the maker's own.

- **Arcs.** With `vx` not zero, `|wz|` is kept at or under `2|vx| / track`, with the stack's own
  track width, so the inner track never reverses into a full power pivot. The answer reports any
  clip, and `move`'s description tells the pilot the rover turns slower than asked, never faster.
- **Spins.** With `vx` zero, a turn is sent as the maker's own pulses: `PULSE_S` of twist, then a
  rest of zeros. The rest is the maker's `REST_S` scaled by `max_wz / |wz|`, so at the manifest's
  ceiling it is the maker's own rhythm and a smaller turn rate turns proportionally slower. The
  pulses are timed on the rover: timed from the laptop, each would last as long as the deadman.
  How far one pulse turns the rover nobody has measured.
- **The core verbs.** `search_scan` turns in steps at 0.4 rad/s, which become pulses, and the real
  angle per step is step 6 of the hardware checklist. `go_to`'s turn to find a lost target and
  its holds become pulses too, and its steering arcs keep both tracks forward.
- **Floor and ramp.** Below the 20% floor the rover goes faster than asked. Starts and stops take
  the stack's ramp. The mock does both, under-reads its odometry on a pivot, and stalls against a
  wall, so a test that passes on the mock has not been given a kinder rover.

## Units

- `vx` is m/s and `wz` rad/s, `+x` forward and `+wz` a turn to the left, as MOSS's own
  `twist_to_tracks` mixes them. `vy` is dropped.
- The pose is the lidar odometry's, in the frame the stack started in, with `yaw` in radians.
  `drive_to`'s world goals are in that frame.
- The battery is volts, judged against the launcher's floor. The cockpit's percent is a linear
  gauge from 10.2 V to 12.6 V, a voltage gauge rather than a measure of charge, and quackd
  reports it as the cockpit's.
- The jaw gap is metres, 0.090 fully open.

## Cameras

**With the bridge**, both cameras come through `GET /frame`: the first image captured after the
request arrived, by the image's own stamp on the rover's clock. So `frame_taken_at` is the
moment the request went out, a true lower bound, and `frame_period_s` is the stack's 5 fps, or
the time one fetch takes when that is longer. A
frame whose stamp fails the bridge's check against its own receive time carries no
`frame_taken_at`, and a frame older than four periods, at least 2 s, is not handed out. The nose
camera is primary, for detection and steering, and the wrist camera is the extra view `wrist`.

**Without the bridge**, the nose comes from the cockpit's own MJPEG stream, through one
persistent reader, which takes one of the cockpit's four viewer slots and leaves room for the
owner's page. An arrival time is all a stream says of a frame's age, which is an upper bound, so
no `frame_taken_at` is offered, and no steering verb exists in that mode anyway.

**The field of view** comes from the stack's own `camera_info`, through the bridge, when the stack
publishes it. MOSS streams 640 by 480, which may be a crop of the sensor, so without it no lens is
declared and `--fov-deg` says it.

## Before it will answer

On the rover, in the MOSS checkout, the arm's coordinator runs on its own, as MOSS's own
docs/litter.md starts it, and MOSS's launcher runs its gates, the stack and the cockpit. Start the
stack without `EXPLORE=1`, which would set the rover exploring before quackd asks:

```bash
.venv/bin/dimos run moss-dimos.moss-arm     # in a terminal of its own: the arm verbs and moss-litter need it
tools/moss-fly.sh explore                   # or map, or litter
```

Then start quackd's bridge, which [`bridge/moss/README.md`](../../../bridge/moss/README.md)
installs as a service with its token. On the laptop, read the token once and open one tunnel for
both:

```bash
export QUACKD_MOSS_TOKEN="$(ssh <rover> cat /etc/quackd/moss-bridge.token)"
ssh -L 8904:127.0.0.1:8904 -L 9876:127.0.0.1:9876 <rover>
```

`--address http://127.0.0.1:8904` then reaches the cockpit, and the bridge on 9876 beside it.
A bridge on another port goes in the address as `?bridge=PORT`. The token comes from `--token`
or `QUACKD_MOSS_TOKEN`, never from the address, which is printed and lands in transcripts.
quackd takes no `--camera-url` here, since both cameras come from the rover itself.

**Nothing authenticates MOSS's cockpit.** It binds `0.0.0.0` on port 8904, and anything that
reaches that port reads the rover and commands it. The header it asks for stops a cross site
form, not a stranger. Reach it through the tunnel, or keep the rover on a network you trust.

## The check against MOSS's own cockpit

On 2026-10-20 quackd's client was pointed by hand at MOSS's own cockpit at `d288647`, started with
`--demo` from a copy outside this repository and never vendored, on Windows, with quackd's own
Python 3.11.8. `--demo` serves a synthetic rover with no bus and no stack behind it.

| Asked | The cockpit answered | quackd |
|---|---|---|
| `GET /state.json` | 500, `AttributeError: 'NoneType' object has no attribute 'splitlines'` | took it as an answer that was not the rover's state, as data |
| `GET /camera.mjpg?cam=nose` | a stream: the first frame a 4476 byte 320x240 JPEG, and frames still coming 1.06 s later | parsed the parts and kept one reader open |
| `POST /cmd/goal`, twice, the second while exploring | `ok`, and the channel it said it sent on | read the answers |
| `POST /cmd/explore` start and stop, `POST /cmd/stop` | `ok`, and the channels | read the answers |
| `POST /cmd/keepalive` | `ok`, with the idle document: a 300 s limit | read it |
| `POST /cmd/stack` | nothing: it was never sent | the client refused it before anything went out |
| `MossCockpit.connect` | the same 500 | refused: "the cockpit at http://127.0.0.1:50982 answered, but not with the rover's state" |

The 500 is the cockpit's machine metrics, which read `/proc/stat` and call `os.getloadavg`, and
Windows has neither, so the cockpit's own state document and a connect to a cockpit that answers
it are left for a Linux machine: this one's WSL has no distribution but Docker Desktop's. The demo
answers commands with code of its own rather than the live cockpit's, so the check proved the
HTTP side, the header, the framing and the refusals, and not the live cockpit's goal deferral
while exploring. One thing the maker may want to know: the 500s left no line in the cockpit's
log, because its handler logs failures through `log_error`, which the standard library sends
through `log_message`, and the cockpit silences `log_message`.

## Upstream API

### VERIFIED (read from upstream source on 2026-10-20, at `d288647` and dimOS `a7d19f7`)

| Thing | Value | Used for |
|---|---|---|
| Cockpit port | `8904` | the default address and the tunnel |
| Cockpit bind | `0.0.0.0` | why the docs say tunnel: nothing in the server authenticates |
| Command header | `X-Requested-With: moss-cockpit` | sent on every command, which is answered 403 without it |
| State | `GET /state.json` | the stack, the pose, the battery, the nav state and the idle stop |
| Pose | `pose: x, y, yaw, age_s` | the pose and how fresh it is |
| Topic rates | `topics: age_s, hz` | `pose_fresh`, from the rover's own odometry rate |
| Navigation | `nav: exploring, explore_done, goal, goal_reached, last_cmd_vel` | `drive_to` and `explore` |
| Base | `base_view` | the battery's volts, the firmware's mode and age, the stall flag |
| Battery gauge | `10.2 V to 12.6 V` | the cockpit's percent, reported as the cockpit's |
| Idle state | `IDLE_KEYS` | the keepalive's period, from `limit_s` |
| Stack | `stack_status` | the stack, when there is no bridge |
| Bus | `bus: transport, listening, error` | a stack off LCM is refused |
| Mode | `mode` | a `demo` cockpit is refused |
| Stream | `GET /camera.mjpg` | the nose without a bridge |
| Stream framing | `boundary=frame` | the reader's parts, each with its `Content-Length` |
| Stream period | `0.2` | at most 5 fps per viewer |
| First frame wait | `2.0` | connect waits twice this for a nose frame with no bridge |
| Viewers | `4` | per camera, so quackd takes one nose slot |
| Goal | `POST /cmd/goal` | `drive_to` |
| Goal while exploring | `3.0` | the deferral `drive_to` allows for |
| Explorer | `POST /cmd/explore` | `explore` and `collect_litter` |
| Soft stop | `POST /cmd/stop` | the second half of every stop |
| Keepalive | `POST /cmd/keepalive` | holds the idle stop off while quackd is connected |
| Launcher | `POST /cmd/stack` | never posted |
| Commands | `LiveCockpit.command` | no velocity and no arm command, which is what the bridge is for |
| Idle stop | `300` | seconds |
| Idle stop floor | `30` | the shortest limit the cockpit accepts |
| Idle stop stacks | `"moss-nav", "moss-explore"` | the only stacks it ends |
| Firmware age | `1.0` | `cockpit_fresh`, the cockpit's own rule |
| Demo | `--demo` | refused at connect, and the check by hand |
| Run registry | `~/.local/state/dimos/runs/*.json` | where the bridge reads the stack |
| Stack environment | `/proc/<pid>/environ` | the stack's bus and its `MOSS_*` overrides |
| Bus variables | `DIMOS_TRANSPORT, TRANSPORT, LCM_DEFAULT_URL` | the bridge refuses another bus or address |
| Channels | `CHANNELS` | the cockpit's own list of the stack's channels |
| Cameras | `"nose", "wrist"` | the two camera names |
| Explorer channels | `"explore_cmd", "stop_explore_cmd"` | the bridge halts an exploration with the second |
| Channel names | `/<stream>#<msg_name>` | how the bridge names what it publishes on |
| Full power | `0.32` | m/s, above quackd's 0.3 cap |
| Power floor | `20` | percent, which the mock applies too |
| Pivot power | `100` | percent, for every turn on the spot |
| Track mixing | `twist_to_tracks` | `+wz` turns left, and `vy` is dropped |
| Firmware ceiling | `100` | percent, the length of a stop from full power |
| Firmware deadman | `0.300` | s, and the bridge's own twist deadman is as long |
| Base adapter stale | `0.500` | s, behind the bridge's deadman |
| Velocity watchdog | `0.2` | s, dimOS zeroes the base's command after it |
| Ramp | `MOSS_RAMP_MS` | the stop's length, which the bridge reports |
| Launcher ramp | `"5 5"` | half a second from full power, and the mock ramps by it |
| Explorer radius | `MOSS_EXPLORE_RADIUS_M` | 4 m by the launcher, where an exploration quackd lost goes on |
| Stall | `1.0` | s, `not_stalled`, and the mock stalls the same way |
| Track width | `0.266` | the arc clip, which uses the stack's own value |
| Telemetry period | `0.5` | s, `cockpit_fresh` allows four |
| Camera rate | `5` | fps, at 640 by 480, the frame period |
| Frames on the bus | `JpegLcmTransport` | the bridge hands the JPEG on and decodes nothing |
| Pad on `moss-nav` | `(GamepadTeleop, "tele_cmd_vel", "cmd_vel")` | the twist goes on `cmd_vel` there |
| Planner | `MovementManager.blueprint` | `moss-explore` and `moss-litter` have one |
| Explorer goal timeout | `45.0` | s, `drive_to`'s give up, with the deferral |
| Arm run | `build_moss_arm` | a separate run serves the arm |
| Teleop cancels navigation | `MovementManager._on_teleop` | why the bridge falls silent after its one zero |
| Teleop cooldown | `1.0` | s the planner is held off after a pad message |
| Turn ceiling | `0.4` | rad/s, `max_wz`, and the pulses' rhythm |
| Pulse | `0.1` | s of twist |
| Rest | `0.4` | s of zeros at the ceiling |
| Mission states | `EXPLORING, STOPPING, APPROACHING, SERVOING, PICKING, RESUMING` | `collect_litter` reports the changes it sees |
| Mission settle | `3.0` | s, which the mock's mission waits too |
| Mission tick | `10.0` | Hz, and the bridge's twist rate |
| Mission inputs | `objects, odom, goal_reached` | it listens to no stop |
| Mission loop | `LitterMission._run` | the only place the mission commands |
| Mission stop | `LitterMission.stop` | the bridge's first step on `moss-litter` |
| Mission status | `LitterMission.mission_status` | `/mission`, and the stop's confirmation |
| Mission pick | `[sys.executable, tools/moss_arm_play.py]` | the bridge runs the cycle the same way |
| Mission resumes | `RESUMING` | why the mission is stopped before the arm is cancelled |
| Mission autostart | `MOSS_MISSION_AUTOSTART` | `collect_litter` starts the explorer itself |
| Pick timeout | `180.0` | s, which `pick_and_drop` gives the arm too |
| Cycle script | `tools/moss_arm_play.py` | `pick_and_drop` and `stow_arm` |
| One trajectory | `client.execute_trajectory(traj)` | killing the script cancels nothing |
| Exit codes | `0, 2, 3, 4` | done, refused, refused by the coordinator, a lag |
| Progress lines | `running:, done:, ARM LAG, refused` | the job's state |
| Lag watch | `client.cancel_trajectory()` | the race the stop order prevents |
| Script's client | `RPCClient(None, ControlCoordinator, remote_name=A.ARM_COORDINATOR_NAME)` | the bridge builds the same for its cancel |
| Arm coordinator | `ArmCoordinator` | the arm's RPC name |
| Start tolerance | `0.05` | rad, folded means every joint this close to the drive pose |
| Joints | `JOINT_NAMES` | the arm's five joints and its gripper |
| Drive pose | `drive_pose_rad` | `arm_folded` |
| Jaws open | `0.090` | m, the 90 mm jaws |
| Cycle time | `36.9` | s as the maker timed it, and the mock's cycle |
| Stow | `back_to_clear` | `stow_arm` plays from here |
| Pick poses | `"pick", "pick_low"` | `pick_and_drop`'s `pick_pose` |
| Still rule | `20.0` | ticks a second, whether the arm held after a cancel |
| Torque off | `FeetechSo101Adapter.disconnect` | why quackd never ends the arm's run |
| Joint reads | `ControlCoordinator.get_joint_positions` | `/arm` and the hold check |
| Cancel | `ControlCoordinator.cancel_trajectory` | the arm's cancel |
| Cancel result | `TrajectoryCancellationStatus` | the name `/stop` reports |
| Launch floor | `10.8` | V, `battery_ok` |
| BMS cut | `10.2` | V |
| Hard stop | `tools/moss-fly.sh stop` | never sent by quackd, and what the idle stop runs |
| RPC names | `serve_module_rpc` | `LitterMission/stop` and `LitterMission/mission_status` |
| A stop sent without an answer | `call_nowait` | why the stop is confirmed by silence |
| RPC timeout | `120.0` | s, dimOS's default, where the bridge's own is 2 s |
| RPC over LCM | `LCMRPC` | how the bridge calls the mission and the arm |
| A module's stop | `ModuleBase._close_module` | why the mission's RPC falls silent as it stops |
| Image time | `Image.ts` | a frame's own stamp on the bus |
| RealSense time | `global_time_enabled` | the nose's stamps on the host clock, where the sensor supports it |
| RealSense stamp | `frame.get_timestamp()` | the camera's own time, not its arrival |
| Webcam stamp | `ts=time.time()` | the wrist's stamps, on the rover's clock |
| Intrinsics | `CameraInfo.K` | the field of view, when `camera_info` is published |
| Twist message | `geometry_msgs.Twist` | every twist channel |
| Status line | `Autonomous litter picking and the full field test are not yet demonstrated` | the datasheet's last note |
| First run | `Put it on blocks for the first run` | the hardware checklist's first step |
| Servos | `ST3215 12 V serial bus servo` | six actuated joints with the gripper's |
| Battery bay | `not a claim about battery capacity or runtime` | why endurance stays not published |

### UNVERIFIED, and what quackd does about it

| Name | The assumption | What quackd does |
|---|---|---|
| `MISSION_STAYS_STOPPED` | a mission whose stop has run stays stopped until the stack restarts. Nothing read restarts it, and no run has shown it | the bridge remembers the run it stopped, and `collect_litter` refuses until the run is a new one |
| `MISSION_STOP_REACHES_IT` | a stop sent over LCM RPC from the bridge's process reaches the mission | confirmed only by the mission's RPC falling silent, from a mission heard before. Unconfirmed is a failure, and the arm's cancel and the zero go out either way |
| `IMAGE_STAMP_CLOCK` | a nose frame's stamp is on the rover's wall clock. dimOS asks librealsense for global time, and what that means is outside both pins | the bridge checks every stamp against its own receive time, and only a frame that passes carries `frame_taken_at` |
| `PULSE_TURN_RATE` | how far one pulse turns the rover | `search_scan`'s angles are what it asked for. Step 6 of the checklist measures it |
| `PICK_POINT` | where the jaws close, which the maker marks to measure | `pick_and_drop` says the arm reaches for nothing, and to check the wrist view first |
| `CYCLE_FROM_ANYWHERE` | the cycle is safe from wherever the arm is, though its first move goes straight in joint space and checks no scene | both arm verbs are `confirm` and say so in their descriptions |
| `LENS_WITHOUT_CAMERA_INFO` | the nose lens, when no `camera_info` is heard | no field of view is declared, and `--fov-deg` says it |
| `TRACK_WIDTH_IS_CAD` | 0.266 m is the CAD's, a placeholder to measure | the arc clip uses the stack's own value, whatever it is |
| `ODOMETRY_UNDER_READS` | how far the lidar odometry lags a full power pivot, which the maker notes and nobody has measured | `drive_to` judges arrival by the planner's `goal_reached`, and the mock under-reads its pivots on purpose |
| `MASS` | 4.2 kg for the rover complete, the maker's figure in issue 2, for the V0.3 prototype. An issue is not versioned, so nothing at the pin carries it | the datasheet gives it as official, the maker's word, with that source, and says the V0.6 hull has not been weighed |
| `ARM_MASS` | 768 g for the arm and gripper, weighed off the rover, in a comment on issue 2 | the datasheet's mass note gives it |
| `ARM_UNSEEN_WITHOUT_MOSS_ARM` | with no `moss-arm` run nothing holds the arm or reports it | the base is driven as if the arm were folded, no arm verb is offered, and the manifest's assumptions say so |

The bridge's own protocol is quackd's at both ends, so it is outside this table, and its names
are pinned by tests that read both files instead.

## What the cockpit would need to answer to retire quackd's bridge

This part is for MOSS's maker. quackd runs a small bridge on the rover because the cockpit has no
way to do six things. If the cockpit could do them, quackd would talk to the cockpit alone and
the bridge would not be needed.

1. **Drive with a velocity, with a deadman.** For example `POST /cmd/twist {vx, wz}`, passed on
   to the base while new requests keep arriving, and stopped by the cockpit itself a short time
   after the last one. Turning would be safest if it worked like the litter mission's own turns:
   short pulses for a turn on the spot, and arcs that keep the inner track moving forward, so the
   base never pivots at full power by accident.
2. **Play the arm cycle and cancel it.** For example `POST /cmd/arm {pick_pose, from}` to play
   the cycle in `tools/moss_arm_play.py`, a cancel that leaves the arm holding where it is, and a
   way to read how the cycle ended: done, refused, lag or cancelled.
3. **Start and stop the litter mission.** Right now the mission ignores every stop except its own
   `stop` RPC. A command for each, with the mission's state in `/state.json`, would let any
   client stop it without going through dimOS.
4. **Say when a frame was taken, and what the camera sees.** The capture time on the rover's
   clock, as a header on a single frame request, and the nose camera's `camera_info` in
   `/state.json`, so a client knows the field of view.
5. **Say which cockpit it is.** One field in `/state.json`, a commit or a protocol number.
6. **Ask for a token.** The cockpit listens on `0.0.0.0`, and anything that can reach it can
   drive the rover. An optional token, read from a file and checked on every request, would
   make it safe on a shared network.

A small thing we noticed while testing against the cockpit's demo mode: a request that fails
inside the cockpit leaves nothing in its log. The handler reports it through `log_error`, the
standard library passes that to `log_message`, and the cockpit turns `log_message` off to keep
the log quiet.

## How to help

If you have built a MOSS, the useful thing is a first run. Work through
[the hardware checklist](hardware-checklist.md) in order. It starts on blocks, as the maker asks,
and the first thing it asks for is `quackd doctor --robot moss:cockpit --address
http://127.0.0.1:8904`, which connects, rather than `list-verbs`, which does not.

What most needs a real rover: how far a pulse turns it, whether the stamp on a nose frame is on
the rover's clock, whether the mission's stop ends it cleanly from the bridge, and whether the
arm holds after a cancel. Open an issue with the transcript.
