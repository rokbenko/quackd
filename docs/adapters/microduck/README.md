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

Read: 2026-10-08, pinned at [`d818af5`](https://github.com/pollen-robotics/microduck/tree/d818af5d91fce0e3f5f8c22d47d47caf7b3f17a1)
(`daemon-v0.16.1`, 2026-10-07). Upstream contract: `duck-ipc-proto` **API v41** (`API_VERSION`),
JSON-RPC 2.0, one object per line (NDJSON), one unix socket per service. The pin is a release,
because a robot runs one.

The previous read, on 2026-09-04, pinned `main` at `bc41fb5` on API v23. quackd's handshake
accepted that number and no other, so it refused every robotd release from `daemon-v0.12.0`
(API v27) on. Everything quackd sends reads the same at both pins, doc comments included, and so
does everything it reads but for fields added to `robot.state` and `robot.health`, of which
quackd reads one, `safety.picked_up`. Upstream's own rule for the number is that "A bump
promises nothing in either direction".

quackd follows that rule now ([ADR-0051](../../adr/0051-a-microduck-newer-than-the-read-is-served.md)).
A robotd on any version from API v23, the oldest quackd has read end to end, is served, and one
on any version but v41 is warned about, in the log, in `quackd doctor` and in every state's
`extras.robotd`. One older than v23 is refused at connect, and so is one whose `hello` names no
version. What refuses a call that changed shape is the call: robotd answers a method it does not
have with `METHOD_NOT_FOUND` and a member it does not know with `INVALID_PARAMS`, each by name,
and the verb that sent it fails naming the call and both versions. A notification gets no
answer, so quackd checks every `robot.move` against the robot's own state instead
(`robot.state.move.requested`, below).

Sources: [duck-ipc-proto/src/lib.rs](https://github.com/pollen-robotics/microduck/blob/d818af5d91fce0e3f5f8c22d47d47caf7b3f17a1/duck-ipc-proto/src/lib.rs) ·
[robotd/src](https://github.com/pollen-robotics/microduck/tree/d818af5d91fce0e3f5f8c22d47d47caf7b3f17a1/robotd/src) ·
[robotd-params/src/lib.rs](https://github.com/pollen-robotics/microduck/blob/d818af5d91fce0e3f5f8c22d47d47caf7b3f17a1/robotd-params/src/lib.rs) ·
[mediad/src](https://github.com/pollen-robotics/microduck/tree/d818af5d91fce0e3f5f8c22d47d47caf7b3f17a1/mediad/src) ·
[architecture.md](https://github.com/pollen-robotics/microduck/blob/d818af5d91fce0e3f5f8c22d47d47caf7b3f17a1/docs/design/architecture.md) ·
[robotd-design.md](https://github.com/pollen-robotics/microduck/blob/d818af5d91fce0e3f5f8c22d47d47caf7b3f17a1/docs/design/robotd-design.md) ·
[remote-webrtc.md](https://github.com/pollen-robotics/microduck/blob/d818af5d91fce0e3f5f8c22d47d47caf7b3f17a1/docs/design/remote-webrtc.md) ·
[roadmap.md](https://github.com/pollen-robotics/microduck/blob/d818af5d91fce0e3f5f8c22d47d47caf7b3f17a1/docs/project/roadmap.md).

### VERIFIED (read from upstream source)

| Thing | Value | Used for |
|---|---|---|
| API version | `41` | `hello` handshake: the version quackd read, and the one it sends. A robotd on any other version from the floor up is served, with a warning in the log, in `doctor` and in `extras.robotd`, which also names the release and the revision the robot answered with |
| API floor | `23` | the version note for v23 in `lib.rs`: the oldest `duck-ipc-proto` quackd has read end to end, the previous read's, and `daemon-v0.11.0` was the first release on it. A robotd that old reports no `safety.picked_up` and no `frames` | a robotd below it is refused at connect, naming both numbers, and so is a `hello` that names no integer version |
| `No daemon refuses a call because this number differs` | upstream's rule for two peers on different versions: "A bump promises nothing in either direction", robotd answers `hello` without comparing, and `robotctl` warns once and carries on, because "a call that cannot be served will name itself". What refuses instead is the call: `METHOD_NOT_FOUND` naming a method a release does not have, `INVALID_PARAMS` naming a member it does not know. The doc comment above the rule still says a peer on a different version "is refused rather than misparsed"; the rule and robotd's own `hello` say otherwise | why quackd serves a robotd it has not read. A verb whose call is refused either way fails naming the call and both versions, and a `hello` that is `METHOD_NOT_FOUND` says the socket is not robotd's |
| Framing | `NDJSON: one JSON-RPC 2.0 object per line` | wire |
| Runtime dir | env `DUCK_RUNTIME_DIR` overrides `/run` | socket path |
| Sockets | `/run/robotd.sock`, `/run/configd.sock`, `/run/updaterd.sock`, `/run/padd/pad.sock` (pad.input only), `/run/tofd/tof.sock` (tof.stream only), `/run/mediad/media.sock` (media.frame only, and local) | addresses |
| `hello` | params `{api_version}` → `{api_version, daemon_version, revision}`, the last two always on the wire and possibly `null` | connect |
| `robot.move` | **notification** `{vx, vy, vyaw}` m/s, rad/s, trunk frame, x forward, y left, +vyaw left | `move`, `go_to`, `search_scan` (re-sent every 100 ms). Never sent while posture is not `standing`, nor once robotd's state has not shown one it was sent (`robot.state.move.requested`), and once one is held back for either, none goes out until the verb that sent it stops |
| `robot.state.move.requested` | the twist robotd was last asked for, as it was asked: `robot.move` stores it untouched and every state frame reports it beside `applied`, the twist after the deadman and the limits, and robotd's own test holds it to that, "what the client asked for must survive to the stream" | the only place quackd can see a `robot.move` was taken, since robotd answers a notification with nothing, even one it could not parse. A twist the state has not shown three frames after it went out (0.3 s at the 10 Hz quackd subscribes at) holds `robot.move` back as a duck that stops standing does, the verb that sent it fails saying what the state showed instead, and `extras.move_echo` keeps the last verdict |
| `robot.stop` | request; zero velocity, *not* limp | `stop`, every run's final stop |
| `robot.head` | notification `{neck_pitch, head_pitch, head_yaw, head_roll}` | (not used; `robot.look` preferred) |
| `robot.look` | request `{x, y, z, neck_pitch}` → `{head, clamped}`. `head` is what went down the path `robot.head` feeds, and upstream's word on it is "resend these to hold the gaze" | `gaze`, re-centering before steering. Sent once per gaze, so on `daemon-v0.16.1` the idle look-around (below) can move the head off it two seconds later |
| `robot.do` | request `{skill}` → `{accepted, reason?}`, answered on accept/refuse rather than on completion. `Skill` is now `String` — "a name, not an enumeration" — and `ground_pick | kick_left | kick_right | sit_toggle | roulade` are the names a *stock* robot answers to; a robot's skills are config, and an unknown one is refused with the list it does know | `kick`, `grab`, `sit`/`stand` |
| `robot.pose` | notification `{z, roll, pitch, active}` | `pose` intent (no verb yet) |
| `robot.enable` | request `{on, toggle?}` (`toggle` is `#[serde(default)]`, so `{on}` alone is valid). Policy execution, **not a flag**: upstream says it "can bring a limp robot up as a side effect of being asked to drive", so treat it as motion | `stand_up` |
| `robot.init` / `robot.relax` | power the joints + ramp to home pose (moves every joint) / torque **off** (collapse) | **never sent by quackd** |
| `robot.sound` | request `{tag, hold?}`; tags `alarm | greet | inquire | peck | chirp | coo | wheee` — no TTS | `quack` and `say` (text → tag) |
| `robot.subscribe` → `robot.state` | request `{hz?}`, then notifications `{t, move{requested,applied,limited_by}, head[4], policy, safety{fallen,limp,gravity,gain?,picked_up}, loop{hz,missed}, joints, targets, velocities?, currents_ma?, odom, theremin?, chorale?, t_ns, imu?, frames?, skeleton?}`. `head` is the commanded head, idle glances included | state |
| `robot.state is not pushed until robot.subscribe` | the loop publishes into a bounded broadcast and never waits on a subscriber, so a slow client gets a gap rather than backpressure | why the transport subscribes inside `connect()` |
| `robot.subscribe -> SubscribeResult.skills` | the answer carries `{accepted, walk?, stand?, unavailable?, sitstand?, ground_pick?, skills[]}` — what is constant for the process | learning the robot's real skill list instead of assuming five |
| `robot.state.policy`: `walk, stand, ground_pick, sit, rise, <skill>, <skill>:unwind, skill` while a network drives, `held, homing, rest, limp_fall, limp_pose` while none does | read in robotd's `control.rs` and `main.rs`. The label names what drove the tick, not where the duck is, but which of the first five it is follows the controller's seat latch: `sit` while it holds the seat, `rise` on the way up, `walk` or `stand` once the duck is up. `held` is also what a duck somebody holds reports. Every label but `rest`, the end of a shutdown, was there at `bc41fb5` too | `jsonrpc` reads `walk` and `stand` as standing and `sit`, matched exactly, as sitting. Every other label, a skill's own name, and a label quackd has never read are `unknown`, and so are a duck somebody holds (`safety.picked_up`, read before `safety.fallen`) and a limp one that has not fallen (`safety.limp`), with `extras.posture_reason` saying which. Every verb that walks refuses on `unknown`, `go_to`, `search_scan` and `approach_and` included. `sit`/`stand` read posture first and **refuse** when it is unknown, because upstream has one `sit_toggle` rather than a sit and a stand: firing it unaimed is a coin flip whose losing side sits a standing duck down |
| `safety.fallen gates nothing upstream` | "computed every tick … debounced 0.2 s", and "a report, not a rule". robotd does nothing else about a fall at this pin: `limp_fall`, its one answer to one, is off by default, and "With it off, a fall changes nothing" | refusing to walk a fallen duck is quackd's own rule, so quackd must read the frame |
| `safety.picked_up` | somebody is holding the duck: the pick-up detector has paused the policy, which reads `held`, until the duck is put down. Absent from a robotd older than the detector, "which reads as never picked up". First released in `daemon-v0.15.2`, on the API v37 that `daemon-v0.15.0` and `daemon-v0.15.1` report without it | read before `safety.fallen`, since a duck held at an angle reads as fallen too. Nothing that moves the duck is sent while it is true, and a walk it interrupts stays stopped (`robot.move`, above). The version cannot say whether a robot has it, so `extras.pickup_detection` says, from the frame, and `doctor` warns when it does not: nothing then tells quackd that somebody is holding the duck |
| `robot.health` | request → `{healthy, degraded?, reason?, battery{volts,percent}?, motors?, cpu_temp_c?, cpu_throttle?, control_loop?, bus, imu?}` | heartbeat every 500 ms; battery abort |
| `robot.mode` / `robot.setMode` | `{mode}`, `walk` or `roller` | (not used yet) |
| `robot.policies` | request → `{mode, enabled, slots, skills, homed?, sitting?}`: what each policy slot runs, whether the duck has reached its home pose, and whether it is "parked in its seat" | documented, not called: every state frame's label already says `sit` and `homing`, so asking would cost a request per state read for nothing new |
| `tof.stream` → `tof.frame` | 8×8 depth on tofd's socket | (not used yet) |
| `pad.input` | gamepad raw tap; the pad is the authority | documented, not used |
| `padd drives while a pad is connected` | from the moment a gamepad connects, `padd` sends `robot.move` on every tick its sticks ask for motion, and re-sends an unchanged stop every 100 ms while they do not, so robotd's twist never ages past its deadman while a pad sits idle. robotd keeps the newest twist from any client. With the pad gone, `padd` sits the robot down a second later | nothing, since the pad is the authority. But a connected pad nobody is touching tells robotd to stand still at least ten times a second, over any twist quackd sends, so it fights every walk, and once three state frames in a row show only its stop, quackd holds the walk back (`robot.state.move.requested`) |
| `robotd intent deadman` | "if intents stop arriving, the velocity goes to zero. Stop is not limp" | why `move` re-sends |
| `RobotState.frames` | where the camera and the ToF sensor are in the trunk frame at the measured head joints, the camera in OpenCV axes (+x right, +y down, +z along the optical axis). On the stream since API v24, first released in `daemon-v0.12.0` | not read. `extras.robotd.state_frames` records whether a robot reports it, since the floor serves robots that do not |
| `robotd's idle look-around` | new in `daemon-v0.16.1`. Once the duck is still and nobody has steered its head for 2 s, robotd glances around on top of the commanded head, up to 0.6 rad of yaw and 0.2 rad of pitch, fading in and out over 1.5 s. A `robot.look` or `robot.head` from anyone holds it off for another 2 s | nothing yet. A `gaze` is one `robot.look`, so on a robot this new the head wanders off it two seconds after the gaze, whenever the duck is standing still |
| `velstand.onnx` | the walk a stock robotd loads at this pin: one network that "walks on a twist and stands still at zero command", so no standing network is loaded by default. That is why `limp_fall`, whose stand-up hands the duck to the standing network, is off | what a real duck walks on. `microduck:mujoco` still runs `alpha_walking` and `alpha_stand` ([below](#the-ducks-physics-upstreams)) |
| `GET :8080/frame` | `mediad`'s console port answers with one upright PNG per request, uncached and at most four at a time, or with `503` "Camera snapshot unavailable; retry when capture is running." Port 8080 on every address by default, with no authentication. New since `bc41fb5`, where the port served the console page alone, and first released in `daemon-v0.13.0` | nothing by name. An HTTP `--camera-url` decodes any PNG, so it can point here, and nobody has pointed it at a robot. When its path is `/frame`, `extras.robotd.mediad_frame` says whether the route has answered |
| `media.frame` | one raw UYVY frame on `mediad`'s local socket: a JSON header `{width, height, format, bytes, captured_at_unix_us, rotate}`, then the bytes. "Deliberately not a `Call`", so it never reaches the WebRTC control datachannel | not called |
| `media.detections` | `mediad`'s duck detector, `{width, height, took_ms, boxes[{x0,y0,x1,y1,score}]}`, pushed on each WebRTC peer's control datachannel and nowhere else. It finds ducks, not balls. Off by default, and two looks a second when on, which upstream calls "a thermal number, not a taste" | the WebRTC camera keeps the newest one. Nothing steers on it |

### UNVERIFIED (designed, assumed, or missing upstream) — and what we do

| Thing | Status upstream | What quackd does |
|---|---|---|
| `WebSocket agent gateway` | dropped. At `bc41fb5`, architecture.md §5.3 designed one, "open a WebSocket, poll a frame, send intents". At this pin the same table sends a server-side agent over the "rendezvous control lane", JSON-RPC over HTTP/SSE with no WebRTC, and roadmap M5's SDK is that lane | `--robot microduck:websocket` is a stub that refuses to connect, with the links |
| `stand_up asks robotd's policy to drive (robot.enable), which upstream does not promise gets a fallen duck up` | there is no stand-up call for a duck on the floor. `robot.enable` is policy execution, which "can bring a limp robot up as a side effect of being asked to drive", and `robot.init`, upstream's stand-up for a duck on its feet, moves every joint. robotd's own stand-up after a fall was the end of `limp_fall`, which is off by default at this pin. Whether velstand gets a duck off the floor has not been seen | `stand_up` reads the duck before it sends anything. It refuses a duck somebody holds and one nothing reports on, says so of one already standing, and points one on its seat at `stand`. Only a fallen duck, or one whose posture is `unknown` for another reason, is sent `robot.enable {on: true}`, and afterwards it is called upright only if posture reads `standing`. Every state lists this in `extras.assumptions`, which the pilot is told not to take for the robot's own work |

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

`robotd`'s socket carries no picture, so quackd takes the camera from `--camera-url`. Without
one, `jsonrpc.get_frame()` returns `None` and the manifest drops `camera` and the four verbs that
need eyes, instead of advertising sight the robot has not got. An HTTP snapshot is pulled on a
5 fps timer and served from memory, so `observe` costs no round trip, and a failed fetch is
reported by `camera_health()` rather than raised. Two ways to point quackd at the camera:

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
