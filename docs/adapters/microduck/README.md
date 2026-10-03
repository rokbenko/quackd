# Microduck

A 25 cm biped from Pollen Robotics, installed with `quackd[microduck]`. `microduck:sim2d` is
the cartoon simulator and the default, `microduck:mujoco` is the same robot in MuJoCo, on its
own walking policy (`quackd[mujoco]`), and `microduck:jsonrpc` is the real one, over `robotd`,
which quackd has never run on a duck. Every backend's row, and how far it has got, is in
[adapters/status.md](../status.md). The order to try a real one in is
[adapters/microduck/hardware-checklist.md](hardware-checklist.md).

The rest of this page is the robot's upstreams, read at pinned commits: `robotd`'s API and what
quackd never sends it, what the robot is as numbers, how to get a picture off one, and the model
and policies the physics simulator runs.

## Upstream API

Read: 2026-09-04, pinned at [`bc41fb5`](https://github.com/pollen-robotics/microduck/tree/bc41fb5c9a9b39894669c1e022e375cf83800382)
(upstream `main`, 2026-09-03). Upstream contract: `duck-ipc-proto` **API v23** (`API_VERSION`),
JSON-RPC 2.0, one object per line (NDJSON), one unix socket per service.
Sources: [duck-ipc-proto/src/lib.rs](https://github.com/pollen-robotics/microduck/blob/bc41fb5c9a9b39894669c1e022e375cf83800382/duck-ipc-proto/src/lib.rs) ·
[architecture.md](https://github.com/pollen-robotics/microduck/blob/bc41fb5c9a9b39894669c1e022e375cf83800382/docs/design/architecture.md) ·
[robotd-design.md](https://github.com/pollen-robotics/microduck/blob/bc41fb5c9a9b39894669c1e022e375cf83800382/docs/design/robotd-design.md) ·
[remote-webrtc.md](https://github.com/pollen-robotics/microduck/blob/bc41fb5c9a9b39894669c1e022e375cf83800382/docs/design/remote-webrtc.md) ·
[roadmap.md](https://github.com/pollen-robotics/microduck/blob/bc41fb5c9a9b39894669c1e022e375cf83800382/docs/project/roadmap.md).

### VERIFIED (read from upstream source)

| Thing | Value | Used for |
|---|---|---|
| API version | `23` | `hello` handshake; mismatch → we refuse rather than guess |
| Framing | `NDJSON: one JSON-RPC 2.0 object per line` | wire |
| Runtime dir | env `DUCK_RUNTIME_DIR` overrides `/run` | socket path |
| Sockets | `/run/robotd.sock`, `/run/configd.sock`, `/run/updaterd.sock`, `/run/padd/pad.sock` (pad.input only), `/run/tofd/tof.sock` (tof.stream only) | addresses |
| `hello` | params `{api_version}` → `{api_version, daemon_version?, revision?}` | connect |
| `robot.move` | **notification** `{vx, vy, vyaw}` m/s, rad/s, trunk frame, x forward, y left, +vyaw left | `move`, `go_to`, `search_scan` (re-sent every 100 ms) |
| `robot.stop` | request; zero velocity, *not* limp | `stop`, every run's final stop |
| `robot.head` | notification `{neck_pitch, head_pitch, head_yaw, head_roll}` | (not used; `robot.look` preferred) |
| `robot.look` | request `{x, y, z, neck_pitch}` → `{head, clamped}` | `gaze`, re-centering before steering |
| `robot.do` | request `{skill}` → `{accepted, reason?}`, answered on accept/refuse rather than on completion. `Skill` is now `String` — "a name, not an enumeration" — and `ground_pick | kick_left | kick_right | sit_toggle | roulade` are the names a *stock* robot answers to; a robot's skills are config, and an unknown one is refused with the list it does know | `kick`, `grab`, `sit`/`stand` |
| `robot.pose` | notification `{z, roll, pitch, active}` | `pose` intent (no verb yet) |
| `robot.enable` | request `{on, toggle?}` (`toggle` is `#[serde(default)]`, so `{on}` alone is valid). Policy execution, **not a flag**: upstream says it "can bring a limp robot up as a side effect of being asked to drive", so treat it as motion | `stand_up` |
| `robot.init` / `robot.relax` | power the joints + ramp to home pose (moves every joint) / torque **off** (collapse) | **never sent by quackd** |
| `robot.sound` | request `{tag, hold?}`; tags `alarm | greet | inquire | peck | chirp | coo | wheee` — no TTS | `quack` and `say` (text → tag) |
| `robot.subscribe` → `robot.state` | request `{hz?}`, then notifications `{t, move{requested,applied,limited_by}, head[4], policy, safety{fallen,limp,gravity,gain?}, loop{hz,missed}, joints, targets, odom, theremin?, chorale?}` | state |
| `robot.state is not pushed until robot.subscribe` | the loop publishes into a bounded broadcast and never waits on a subscriber, so a slow client gets a gap rather than backpressure | why the transport subscribes inside `connect()` |
| `robot.subscribe -> SubscribeResult.skills` | the answer carries `{accepted, walk?, stand?, unavailable?, sitstand?, ground_pick?, skills[]}` — what is constant for the process | learning the robot's real skill list instead of assuming five |
| `safety.fallen gates nothing upstream` | "computed every tick … debounced 0.2 s", and "a report, not a rule" | refusing to walk a fallen duck is quackd's own rule, so quackd must read the frame |
| `robot.health` | request → `{healthy, degraded?, reason?, battery{volts,percent}?, motors?}` | heartbeat every 500 ms; battery abort |
| `robot.mode` / `robot.setMode` | `{mode: walk|roller}` | (not used yet) |
| `tof.stream` → `tof.frame` | 8×8 depth on tofd's socket | (not used yet) |
| `pad.input` | gamepad raw tap; the pad is the authority | documented, not used |
| `robotd intent deadman` | velocity zeroes when intents stop; "stop is not limp" | why `move` re-sends |

### UNVERIFIED (designed, assumed, or missing upstream) — and what we do

| Thing | Status upstream | What quackd does |
|---|---|---|
| `robot.state.policy == 'sit' means sitting` | assumption: the state frame names the policy that drove the tick, and we assume a sitting robot's is named something containing `sit`. Upstream notes two gaits can "both report `walk`", so the name is a policy and not a posture | `jsonrpc` infers posture from it and lists the assumption in `extras.assumptions`. `sit`/`stand` read posture first and **refuse** when it is unknown, because upstream has one `sit_toggle` rather than a sit and a stand: firing it unaimed is a coin flip whose losing side sits a standing duck down |
| `WebSocket agent gateway` | architecture.md §5.3 designs "open a WebSocket, poll a frame, send intents"; roadmap M5 in progress, not shipped | `--robot microduck:websocket` is a stub that raises with the links |
| `get_frame` | §5.3: "JPEG on demand, or 1–2 fps push"; not in duck-ipc-proto | not called anywhere; the stub will use it when it exists |
| `camera snapshot over a unix socket` | today the camera reaches clients only through `mediad`'s WebRTC track; no socket-level frame method, and `robotctl`/`duckctl` have no camera subcommand either | `jsonrpc.get_frame()` returns `None` unless `--camera-url` names a source: an HTTP snapshot you provide, or `mediad`'s WebRTC track (below). A snapshot is pulled on a 5 fps timer and served from memory, so `observe` costs no round trip and a failed fetch is reported by `camera_health()` rather than raised. Without one the manifest drops `camera` and the four verbs that need eyes, instead of advertising sight the robot has not got |
| `mediad media.detections notifications` | **built**, not merely designed: `mediad/src/detect.rs` emits `{width, height, took_ms, boxes[{x0,y0,x1,y1,score}]}` at ~2 Hz (RKNN on the NPU, ONNX on CPU) — and it detects *ducks*, not balls. UNVERIFIED because it is broadcast to WebRTC signalling clients while `remote-webrtc.md` still says perception consumes pixels locally: source and design doc disagree | unreachable from `robotd`'s socket either way, so our `Detector` protocol is still the stand-in |
| `stand_up` | no such RPC; `robotd` recovers from falls itself (limp → settle → ramp → standing policy) | `stand_up` sends `robot.enable {on: true}` and checks `safety.fallen` afterwards — and fails rather than claiming "upright" when nothing is reporting falls |

## What this robot is, as numbers

Its datasheet, which the pilot is shown and told to judge a task against before anything moves ([reference/manifest-spec.md](../../reference/manifest-spec.md)):

| | |
|---|---|
| Mass | 0.8 kg (official: the Pollen Robotics README) |
| Height | 0.25 m (official: the Pollen Robotics README) |
| Actuated joints | 15 (official: the Pollen Robotics README; XL330 class servos, which is an estimate) |
| Not published | payload, reach, endurance |

And what it cannot do whatever the task says, which is the half a refusal usually turns on, in the words the pilot is shown:

- carry, hold or push anything: the beak scoops at the floor and nothing else
- climb or descend a step
- hold a heading for long without a landmark: the IMU has no magnetometer, so heading drifts

A figure nobody published is listed as not published, and the pilot is told to answer `uncertain` and name it, rather than guess, where a task turns on it. A `.duck` file can correct any of it for the build in front of you ([reference/duck-spec.md](../../reference/duck-spec.md)).

## What we do not touch

`robot.init` (moves every joint), `robot.relax` (the robot collapses), `system.*`, `net.*`,
`update.*`. The gamepad (`padd`) keeps authority on hardware; quackd does not arbitrate. The
same principle holds on every adapter: quackd never sends `disable_torque` to an arm of its own
accord, and a base over rosbridge gets a zero Twist, not silence. A LeRobot arm is let go of
only at its rest pose, or as near it as its calibration lets the servos go, where upstream's own
`disconnect()` does it, or when a person holding it asks
([adapters/lerobot/README.md](../lerobot/README.md#the-torque-rule)). On an Open Duck the guarantee
is stronger than a promise: the bridge protocol has no word that reaches torque, so going limp is
unreachable rather than merely forbidden.

## Getting a picture off a real Microduck

There is no camera method in `duck-ipc-proto`, no snapshot or MJPEG route in `mediad` (its HTTP
port serves one page), and no camera subcommand in `robotctl` or `duckctl`. The camera reaches
clients as an H.264 WebRTC track and nowhere else. Two ways to point quackd at it:

| | `--camera-url webrtc://<duck>:8443` | `--camera-url http://<host>:9872/snapshot.jpg` |
|---|---|---|
| Where it runs | your machine | wherever the snapshot server is |
| Touches the robot | nothing | a snapshot server has to hold `/dev/video0`, so `mediad` must be stopped first — it holds the device for the life of its process |
| Needs | `quackd[microduck-camera]` (aiortc, av, websockets) | nothing |
| Costs | one media session, so it competes with the browser console | `mediad`, and therefore the console and the WebRTC path, while it runs |

The WebRTC route is the one to reach for on a robot you do not own. Signalling is
gst-plugins-rs `net/webrtc`, read from `mediad/webclient/index.html` at the pin: the producer
offers and quackd answers. **There is no authentication** — upstream's own note is that a
pairing PIN which is `000000` on every robot "authenticates nobody" — so tunnel it
(`ssh -L 8443:127.0.0.1:8443 radxa@<duck>`) rather than trusting the network.

`mediad` opens a `control` datachannel at every peer whether it wants one or not. quackd reads
`media.detections` off it and writes nothing: motion goes over `robotd`'s socket, where the
allowlist, the confirm gates and the deadman feed already are, and two ways to move one robot
would mean two places to be sure about.

Neither route has been run against a Microduck.

## The duck's physics upstreams

`microduck:mujoco` runs the robot Pollen trains, on the policy Pollen trained. Two upstreams,
both pinned, both fetched at run time into `~/.quackd/cache` and checked against a recorded
sha256, and neither shipped: the 3D model files are CC BY-NC-SA
([reference/licenses.md](../../reference/licenses.md)). Every name quackd relies on lives in
[`adapters/microduck/src/quackd_microduck/sim3d/upstream_api.py`](../../../adapters/microduck/src/quackd_microduck/sim3d/upstream_api.py), and
[ADR-0030](../../adr/0030-mujoco-physics-backend.md) is the reasoning.

**What `microduck:mujoco`'s ✅ rests on.** Two `find-and-kick` sweeps over the same ten seeds: one on
the kinematic stand-in, which CI's `physics` job runs on every push against a software rasteriser,
and one on the trained gait, which needs upstream's model and so runs nightly, fetched the way a
first run fetches it. The real duck's other tests — that it walks, turns, stays upright, refuses
to sit and stands itself up — go with the second. The gait numbers below are still one machine's
word; the sweeps are not.

Read: 2026-09-07, pinned at
[`2b25a48`](https://github.com/pollen-robotics/microduck_rl/tree/2b25a48b08f1f17bc38c90bb03144c81fbd9ed07)
(`develop`, 2026-09-06) and policies at
[`088524a`](https://huggingface.co/pollen-robotics/microduck-policies/tree/088524a64e2557dc453256b6071dbb9d23888802).

| What | Status | Why it matters |
|---|---|---|
| `robot_walk.xml` and its 38 STL meshes | **VERIFIED** | the body: one free joint, 14 hinges, position actuators, an IMU and a head camera |
| `alpha_walking.onnx`, `alpha_stand.onnx` | **VERIFIED** | `obs[1,61] → actions[1,14]`, the normaliser baked in, Apache-2.0 on the Hub |
| observation layout, 13-value command, `ctrl = default_pose + action`, 50 Hz | **VERIFIED** | read from `scripts/infer_policy.py`; the same layout appears in the daemon and in Pollen's own browser simulator |
| projected gravity is the world's `-z` in the trunk frame | **VERIFIED** | get its sign wrong and the duck braces and stands still for every command, silently |
| the gait floor: `no gait below vx 0.23 m/s or wz 1.0 rad/s; above it about 0.38x the commanded speed` | **UNVERIFIED** | measured here on one machine with the XML's own actuators, and re-measured on 2026-09-17 when MuJoCo 3.13 moved it up from 0.22: the floor belongs to the physics build as much as to the policy. Upstream trains and deploys with a different actuator model, so a real duck may track commands directly |
| `a positive head_pitch in the command vector tilts the camera down` | **UNVERIFIED** | measured by driving the command and watching the rendered head camera, not read anywhere, so quackd negates its own pitch to make looking up positive. `neck_pitch` and `head_roll` are left at zero: quackd's gaze has one pitch and no roll, so nothing has exercised them |
| the four stand-ins, in place of `ball_kick_left.onnx, ball_kick_right.onnx, alpha_ground_pick.onnx, alpha_sitstand.onnx` | **UNVERIFIED** | upstream's episodic policies did nothing from a standing pose when tried and the sit-stand one toppled the model, so these four are quackd's stand-ins and say so in `extras.assumptions` |

The head camera is the one place quackd deliberately does not do what the file says: upstream's
`<camera>` quaternion is not MuJoCo's viewing convention, so rendering through it looks
backwards into the duck's own shell. quackd renders from the camera's position along the head
body's forward axis instead.
