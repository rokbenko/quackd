# Adapter status — what has run against its real target, and what has not

quackd never silently invents an upstream API. Every method name, socket path, topic,
message type, enum or convention it relies on lives in one file per upstream, tagged
**VERIFIED** (read from upstream source on the date given, link given) or **UNVERIFIED**
(designed upstream but not shipped, or an assumption of ours, with what quackd does about
it). A test proves UNVERIFIED names stay inside the backend that needs them.
`quackd doctor` prints every UNVERIFIED list on your machine.

One row below has met hardware: `lerobot:real`, on a LeRobot SO-101 arm, on 2026-09-15 and again
on 2026-09-23. Six of the seven bodies here have still never been driven by quackd, and the row
that has was driven on one arm for two afternoons, the second on quackd 0.12.0. Nothing changed
since that afternoon has run on it: the rest pose clipped into the travel,
`quackd robot release`, the paced `move_joints` and the connect retries are exercised only
against a fake arm, `lerobot:mock` and the test suite ([CHANGELOG.md](../CHANGELOG.md), Known
limitations). What the first afternoon did and did not settle is under the table.

> [!IMPORTANT]
> `uv pip install quackd` installs the core and no robot at all. Every body below is its own
> distribution, and the extra that names it is what installs it.

| Adapter | Extra | Distribution |
|---|---|---|
| Microduck | `quackd[microduck]` | `quackd-microduck` |
| LeRobot | `quackd[lerobot]` | `quackd-lerobot` |
| rosbridge | `quackd[rosbridge]` | `quackd-rosbridge` |
| Open Duck Mini v2 | `quackd[open_duck]` | `quackd-open-duck` |
| XLeRobot | `quackd[xlerobot]` | `quackd-xlerobot` |
| AlohaMini | `quackd[alohamini]` | `quackd-alohamini` |
| ToddlerBot | `quackd[toddlerbot]` | `quackd-toddlerbot` |

So `uv pip install "quackd[open_duck]"` buys the Open Duck Mini and nothing else, and
`quackd[robots]` buys all seven, each with the SDK its real backend needs. Two extras buy the
duck and one heavy thing it can do: `quackd[mujoco]` is `quackd-microduck[mujoco]`, the duck
with its physics simulator, and `quackd[microduck-camera]` is the duck with its WebRTC camera.
One buys the arm's simulator: `quackd[lerobot-sim]` is `quackd-lerobot[sim]`, the arm with
MuJoCo and without LeRobot, so it installs on Python 3.11 as well.

An adapter that is not installed keeps its row in `quackd list-adapters` and in
`quackd doctor`, marked not installed. Naming one anyway refuses with the extra to type:
`adapter 'lerobot' needs an extra: uv pip install 'quackd[lerobot]'`. With nothing installed
at all, every command that needs a body refuses and names all seven.

| Adapter | `--robot` | Status | Upstream file | Page |
|---|---|---|---|---|
| Microduck | `microduck:sim2d` | ✅ default | | this page |
| | `microduck:mujoco` | ✅ physics simulator (MuJoCo, `quackd[mujoco]`): `find-and-kick` 10 of 10 seeds on the stand-in, and 9 or 10 of 10 on the trained gait depending on the machine and the run, seed 4 being the marginal one | [`adapters/microduck/src/quackd_microduck/sim3d/upstream_api.py`](../adapters/microduck/src/quackd_microduck/sim3d/upstream_api.py) | |
| | `microduck:mock` | ✅ | | |
| | `microduck:jsonrpc` | 🧪 experimental: every method VERIFIED, never run on a duck | [`adapters/microduck/src/quackd_microduck/upstream_api.py`](../adapters/microduck/src/quackd_microduck/upstream_api.py) | |
| | `microduck:websocket` | ⏳ stub: raises with a link until upstream ships it | | |
| LeRobot | `lerobot:mock` | ✅ | | [adapters/lerobot.md](adapters/lerobot.md) |
| | `lerobot:real` | ✅ **run on a real arm on 2026-09-15 and again on 2026-09-23**, the only row here that has been. On 2026-09-15 it was an SO-101 follower calibrated as `arm-01` and reached as `--robot lerobot:real --address COM3` with no registered name, on Windows 11, Python 3.12.12, lerobot 0.6.1, quackd 0.9.0, piloted by OpenAI `gpt-6-astra`. `lerobot-lookout` ran, once with `--llm fake` as well. Free-form `--goal` runs waved the wrist roll about plus or minus 27 degrees, reached wider with `shoulder_lift` -39 and `elbow_flex` 24 to 30, opened and closed the gripper (commanded 100, reported 98 open and 3 closed with the jaws nearly touching), and one of them mimed a duck quacking with the gripper. A USB webcam answered at `opencv://1`, and at `opencv://2` after a replug, 640x480, with no `?backend=` key needed. **The arm fell at the end of every run that day**, which is the fault the rest pose was written to fix. The rest pose first met that arm on 2026-09-23 and could not reach a fold that lay past the travel its calibration recorded, which is [ADR-0045](adr/0045-a-rest-pose-the-calibration-cannot-reach.md). That second afternoon was 26 runs on quackd 0.12.0 with the arm registered as `arm-01`: 19 never moved the arm at a pilot's request, three failed at connect, each on one bad status packet, one lost and two garbled, and all 21 that reached their close kept torque on and ended at the power switch, which `quackd robot release` now answers. None of what changed after it has run on the arm. Every LeRobot name is still VERIFIED at a pinned commit, and still exercised with a fake arm (Python 3.12+, [checklist](lerobot-hardware-checklist.md)) | [`adapters/lerobot/src/quackd_lerobot/upstream_api.py`](../adapters/lerobot/src/quackd_lerobot/upstream_api.py) | |
| | `lerobot:mujoco` | ✅ the arm's simulator (MuJoCo, `quackd[lerobot-sim]`): `lerobot:real`'s own code over a physics model of the SO-101, the maker's model fetched at a pinned commit. On that model a grasp driven through the real backend's own verbs lifts a cube clear of the table between both finger pads on 10 of 10 seeds, judged by the world's truth and not by quackd, and the bundled `lerobot-lookout` and a grasp task with a sidecar each pass the rehearsal `quackd preflight` runs, on 10 seeds of 10: the lookout on the generic arm, which has no rest pose to return to, and the grasp task from a rest pose its close has to reach, though the grasp task's seeds move only a pen on the table, never the block it lifts (`tests/test_lerobot_sim_model.py`, by hand on 2026-09-27). The nightly `lerobot-sim-assets` job fetches the model and runs those sweeps, asking ten of ten, and its first run, dispatched on `main` on 2026-09-29 at the commit tagged `v0.16.0`, passed all three ten of ten ([run 36523568197](https://github.com/rokbenko/quackd/actions/runs/36523568197)). CI's gating job runs the simulator's other tests on a primitives-only stand-in, because it fetches nothing. Done means the simulator does what it says, not that it moves like an arm: nothing has compared it against one, and it never raises `lerobot:real`'s status | [`adapters/lerobot/src/quackd_lerobot/sim/upstream_api.py`](../adapters/lerobot/src/quackd_lerobot/sim/upstream_api.py) | |
| rosbridge | `rosbridge:mock` | ✅ | | [adapters/rosbridge.md](adapters/rosbridge.md) |
| | `rosbridge:ws` | 🧪 every roslibpy, rosbridge and message name VERIFIED at pinned commits, exercised with fake topics and fake services, including reading the robot's own description off the bridge, never run against a bridge | [`adapters/rosbridge/src/quackd_rosbridge/upstream_api.py`](../adapters/rosbridge/src/quackd_rosbridge/upstream_api.py) | |
| Open Duck Mini v2 | `open_duck:sim2d` | ✅ `open-duck-scout` 10 of 10 seeds | | [adapters/open_duck.md](adapters/open_duck.md) |
| | `open_duck:mock` | ✅ | | |
| | `open_duck:bridge` | 🧪 every runtime name VERIFIED at a pinned commit, the protocol exercised against the real daemon over loopback, never run on a duck | [`adapters/open_duck/src/quackd_open_duck/upstream_api.py`](../adapters/open_duck/src/quackd_open_duck/upstream_api.py) | |
| XLeRobot | `xlerobot:mock` | ✅ | | [adapters/xlerobot.md](adapters/xlerobot.md) |
| | `xlerobot:zmq` | 🧪 the whole wire format VERIFIED at a pinned commit, the client exercised against a fake host quackd wrote from that source over loopback, never run on a cart | [`adapters/xlerobot/src/quackd_xlerobot/upstream_api.py`](../adapters/xlerobot/src/quackd_xlerobot/upstream_api.py) | |
| AlohaMini | `alohamini:mock` | ✅ | | [adapters/alohamini.md](adapters/alohamini.md) |
| | `alohamini:sim2d` | ✅ `alohamini-lookout` 10 of 10 seeds | | |
| | `alohamini:zmq` | 🧪 the whole wire format VERIFIED at a pinned commit, the client exercised against a fake host quackd wrote from that source over loopback, never run on a robot. The arm verbs additionally need quackd's own host wrapper, which nobody has run either | [`adapters/alohamini/src/quackd_alohamini/upstream_api.py`](../adapters/alohamini/src/quackd_alohamini/upstream_api.py) | |
| ToddlerBot | `toddlerbot:mock` | ✅ | | [adapters/toddlerbot.md](adapters/toddlerbot.md) |
| | `toddlerbot:sim2d` | ✅ `toddlerbot-lookout` 10 of 10 seeds | | |
| | `toddlerbot:bridge` | 🧪 every upstream name VERIFIED at the commit the v2.0.0 tag points at, the protocol and the daemon's own safety machinery exercised against a fake body over loopback, never run on a robot | [`adapters/toddlerbot/src/quackd_toddlerbot/upstream_api.py`](../adapters/toddlerbot/src/quackd_toddlerbot/upstream_api.py) | |

**The first real run, and what it does not prove.** One SO-101, one bench, one afternoon.
The arm fell at the end of every run, because LeRobot's `disconnect()` disables torque by its
own default and quackd kept that default: `quackd robot rest-pose`, and the parking at both
ends of a run that goes with it, exist because of those falls. One dry run aborted with `the
arm did not answer: TimeoutError` after a single heartbeat round trip failed, and nothing like
it happened again. One dry run aborted because the pilot answered `uncertain` and the human
said no. The camera framed the gripper and cropped the raised arm, so the model checked its own
waves against joint readings rather than against the picture. And four questions came back
unanswered: whether the holding band is anywhere near right, what a joint reads after ten
minutes of work, whether a stall is caught when you cause one on purpose, and whether 5 degrees
an action felt right in the room. They are in the list at the foot of
[lerobot-hardware-checklist.md](lerobot-hardware-checklist.md), which has since grown to ask
whether a slow `move_joints` looks like one motion and arrives when its time is up, whether a
joint let go at the edge of its travel settles onto its fold, and what the hand placed start
and a decision LLM leave open. Those four are why that ✅ is a robot quackd has worked on
rather than a robot quackd is tested on.

**Pilot flocks** (`flock.allocation.method: pilots`, `--flock NAME`) run one LLM pilot per body
on wall-clock time, on any adapter and backend including mixed ones, and have run on `mock` and
`sim2d` bodies and on no hardware. **Coordinator flocks** (`--flock N`, `flock.roles`) run N
in-process views of one simulated world on one lockstep clock, Microducks only. The MQTT bus
implements the same `Bus` protocol and was exercised once against a local broker
([lan.md](lan.md)); a coordinator flock across machines also needs a clock across machines,
which does not exist yet, and a pilot flock needs no such clock and has never been tried
across two.

The rest of this page is the Microduck's table; the other adapters keep theirs on their
own pages.

## Microduck

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

### What this robot is, as numbers

Its datasheet, which the pilot is shown and told to judge a task against before anything moves ([manifest-spec.md](manifest-spec.md)):

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

A figure nobody published is listed as not published, and the pilot is told to answer `uncertain` and name it, rather than guess, where a task turns on it. A `.duck` file can correct any of it for the build in front of you ([duck-spec.md](duck-spec.md)).

### What we do not touch

`robot.init` (moves every joint), `robot.relax` (the robot collapses), `system.*`, `net.*`,
`update.*`. The gamepad (`padd`) keeps authority on hardware; quackd does not arbitrate. The
same principle holds on every adapter: quackd never sends `disable_torque` to an arm of its own
accord, and a base over rosbridge gets a zero Twist, not silence. A LeRobot arm is let go of
only at its rest pose, or as near it as its calibration lets the servos go, where upstream's own
`disconnect()` does it, or when a person holding it asks
([adapters/lerobot.md](adapters/lerobot.md#the-torque-rule)). On an Open Duck the guarantee is
stronger than a promise: the bridge protocol has no word that reaches torque, so going limp is
unreachable rather than merely forbidden.

### Getting a picture off a real Microduck

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

## How to help

**Built an Open Duck Mini v2?** That is the row most likely to flip this year, because it
is one of three bodies here you can build from scratch, and one of three whose robot side quackd
ships and already exercises. [open-duck-hardware-checklist.md](open-duck-hardware-checklist.md)
is the order to try it in, and there is an issue template waiting for the result.

### The duck's physics upstreams

`microduck:mujoco` runs the robot Pollen trains, on the policy Pollen trained. Two upstreams,
both pinned, both fetched at run time into `~/.quackd/cache` and checked against a recorded
sha256, and neither shipped: the 3D model files are CC BY-NC-SA
([licenses.md](licenses.md)). Every name quackd relies on lives in
[`adapters/microduck/src/quackd_microduck/sim3d/upstream_api.py`](../adapters/microduck/src/quackd_microduck/sim3d/upstream_api.py), and
[ADR-0030](adr/0030-mujoco-physics-backend.md) is the reasoning.

**What that ✅ rests on.** Two `find-and-kick` sweeps over the same ten seeds: one on the
kinematic stand-in, which CI's `physics` job runs on every push against a software rasteriser,
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

### The arm simulator's upstream

`lerobot:mujoco` runs `lerobot:real`'s own code over the SO-101's model from the arm's makers,
[TheRobotStudio/SO-ARM100](https://github.com/TheRobotStudio/SO-ARM100). One upstream, pinned,
fetched at run time one file at a time into `~/.quackd/cache` and checked against a recorded
sha256, and never shipped, although its Apache-2.0 licence would allow it
([licenses.md](licenses.md)). Every name quackd relies on lives in
[`adapters/lerobot/src/quackd_lerobot/sim/upstream_api.py`](../adapters/lerobot/src/quackd_lerobot/sim/upstream_api.py),
the whole table is on [the arm's page](adapters/lerobot.md#the-simulators-upstream-so-arm100),
and [ADR-0047](adr/0047-the-arms-simulator-runs-the-real-backend.md) is the reasoning.

**What that ✅ rests on.** Sweeps on the maker's model, run by hand on 2026-09-27 and put in the
nightly `lerobot-sim-assets` job, which fetches the model the way a first run does: a grasp
driven through the real backend's own verbs, judged by the world's truth, and `quackd preflight`
over the bundled lookout on the generic arm, which has no rest pose to return to, and a grasp
task with a sidecar from a rest pose its close has to reach, ten seeds each. The job's first
run on GitHub, dispatched on `main` on 2026-09-29 at the commit tagged `v0.16.0`, passed all
three ten of ten
([run 36523568197](https://github.com/rokbenko/quackd/actions/runs/36523568197)).
CI's `physics` job runs the rest of the simulator's tests on every push, on a primitives-only
stand-in arm that needs nothing fetched. None of it has been compared against an arm, and it
never raises `lerobot:real`'s row.

Read: 2026-09-26, pinned at
[`5f6d2b8`](https://github.com/TheRobotStudio/SO-ARM100/tree/5f6d2b876a53a4872e405b991dd925556c9e38a4)
(`main`, 2026-09-23).

| What | Status | Why it matters |
|---|---|---|
| `so101_new_calib_camera.xml` and the 15 STL meshes it names | **VERIFIED** | the arm: new_calib, which LeRobot recommends, with upstream's wrist camera mount, and joint and actuator names that are exactly LeRobot's motor names |
| no table, light, camera or physics option in the model | **VERIFIED** | the scene around the arm, every camera it renders from and the contact settings are quackd's, and a seeded grasp sweep is what proves the settings |
| the gains, from a calculation that assumes LeRobot's servo gain, and servo properties adapted from the Open Duck Mini | **VERIFIED** | what the file says of itself, which is why the simulated dynamics are the model's and never the arm's |
| `SERVO_DYNAMICS` | **UNVERIFIED** | a settle time, a push or a grasp that holds in the simulator is evidence about the model, and only the bench can say it about an arm |
| `JOINT_ZERO`, `JOINT_SIGN` | **UNVERIFIED** | a zero offset and a positive sign on the five arm joints, as LeRobot's own kinematics helper assumes, until a bench nudges each joint and reads each stop against the model's |
| `GRIPPER_MAP` | **UNVERIFIED** | LeRobot's 0 to 100 over the model's gripper hinge, linearly, with the closed end found from the model |
| `WRIST_CAMERA_POSE` | **UNVERIFIED** | the wrist view is rendered from upstream's printed mount, which may not be where a real wrist camera sits. The front and top views are quackd's own, and a connect note says so |

**Got your hands on a Microduck?** [microduck-hardware-checklist.md](microduck-hardware-checklist.md)
is the order to try it in, and there is an issue template waiting for the result. Nothing in it
installs anything on the robot or needs `sudo`, because the first Microduck most people touch
will belong to somebody else. `microduck-lookout` is the task to point at it first: nothing in
its allowlist moves a leg.

Ran `--robot open_duck:bridge` against a duck you built, `toddlerbot:bridge` against a
ToddlerBot on its stand, `xlerobot:zmq` against a cart, `alohamini:zmq` against an AlohaMini,
`microduck:jsonrpc` against a real duck, or `rosbridge:ws` against a bridge?
Open an issue with `quackd doctor` output and the first lines of `transcript.jsonl`, whose
`run_start` names the command that was typed and the version that ran it, so a report no longer
rests on anyone remembering either. A run directory holds `terminal.txt` beside it, and that is
not the evidence: it is the screen, a rendering of the same events that `--no-log` shortens. The
transcript is every intent quackd sent and every answer the robot gave back, which is what
somebody else can check. Every row above that flips from 🧪/⏳ to ✅ is one line in an
`upstream_api.py` and one row here. `lerobot:real` is the one that has already flipped, on one
arm on one bench, so a second run against an SO-101 is still worth an issue: it either widens
that row or contradicts it, and the one that contradicts it is worth more.
