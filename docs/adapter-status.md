# Adapter status — what has run against its real target, and what has not

quackd never silently invents an upstream API. Every method name, socket path, topic,
message type, enum or convention it relies on lives in one file per upstream, tagged
**VERIFIED** (read from upstream source on the date given, link given) or **UNVERIFIED**
(designed upstream but not shipped, or an assumption of ours, with what quackd does about
it). A test proves UNVERIFIED names are only reachable from the experimental backends.
`quackd doctor` prints every UNVERIFIED list on your machine.

| Adapter | `--robot` | Status | Upstream file | Page |
|---|---|---|---|---|
| Microduck | `microduck:sim2d` | ✅ default | | this page |
| | `microduck:mock` | ✅ | | |
| | `microduck:jsonrpc` | 🧪 experimental: every method VERIFIED, never run on a duck | [`quackd/transport/upstream_api.py`](../quackd/transport/upstream_api.py) | |
| | `microduck:websocket` | ⏳ stub: raises with a link until upstream ships it | | |
| Reachy Mini | `reachy_mini:sim2d` | ✅ `reachy-spotter` 10 of 10 seeds | | [adapters/reachy_mini.md](adapters/reachy_mini.md) |
| | `reachy_mini:mock` | ✅ | | |
| | `reachy_mini:sdk` | 🧪 every SDK name VERIFIED at a pinned commit and the 1.10.0 wheel, never run on a robot | [`quackd/adapters/reachy_mini/upstream_api.py`](../quackd/adapters/reachy_mini/upstream_api.py) | |
| LeRobot | `lerobot:mock` | ✅ | | [adapters/lerobot.md](adapters/lerobot.md) |
| | `lerobot:real` | 🧪 every LeRobot name VERIFIED at a pinned commit, exercised with a fake arm, never run on an arm (Python 3.12+) | [`quackd/adapters/lerobot/upstream_api.py`](../quackd/adapters/lerobot/upstream_api.py) | |
| rosbridge | `rosbridge:mock` | ✅ | | [adapters/rosbridge.md](adapters/rosbridge.md) |
| | `rosbridge:ws` | 🧪 every roslibpy, rosbridge and message name VERIFIED at pinned commits, exercised with fake topics, never run against a bridge | [`quackd/adapters/rosbridge/upstream_api.py`](../quackd/adapters/rosbridge/upstream_api.py) | |
| Open Duck Mini v2 | `open_duck:sim2d` | ✅ `open-duck-scout` 10 of 10 seeds | | [adapters/open_duck.md](adapters/open_duck.md) |
| | `open_duck:mock` | ✅ | | |
| | `open_duck:bridge` | 🧪 every runtime name VERIFIED at a pinned commit, the protocol exercised against the real daemon over loopback, never run on a duck | [`quackd/adapters/open_duck/upstream_api.py`](../quackd/adapters/open_duck/upstream_api.py) | |

**Flocks** (`--flock`, `flock.roles`) run N in-process views of one simulated world on
one lockstep clock. The MQTT bus implements the same `Bus` protocol and was exercised
once against a local broker ([lan.md](lan.md)); a flock across machines also needs a
clock across machines, which does not exist yet.

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

The previous read was 2026-08-28 against `main`, unpinned — the one adapter ADR-0022 let keep a
moving link. Upstream moved seven API versions in the week that followed and nothing here showed
it, which is what the pin is for. What actually changed for the names quackd uses: the version
number, `Skill` (an enum then, a free string now), and three additive fields
(`RobotState.theremin`, `RobotState.chorale`, `HealthResult.cpu_temp_c`). Everything else read
identically.

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
| `camera snapshot over a unix socket` | today the camera reaches clients only through `mediad`'s WebRTC track; no socket-level frame method, and `robotctl`/`duckctl` have no camera subcommand either | `jsonrpc.get_frame()` returns `None` unless `--camera-url` points at an HTTP snapshot you provide. With one, frames are pulled on a 2 Hz timer and served from memory, so `observe` costs no round trip and a failed fetch is reported by `camera_health()` rather than raised. Without one the manifest drops `camera` and the four verbs that need eyes, instead of advertising sight the robot has not got |
| `mediad media.detections notifications` | **built**, not merely designed: `mediad/src/detect.rs` emits `{width, height, took_ms, boxes[{x0,y0,x1,y1,score}]}` at ~2 Hz (RKNN on the NPU, ONNX on CPU) — and it detects *ducks*, not balls. UNVERIFIED because it is broadcast to WebRTC signalling clients while `remote-webrtc.md` still says perception consumes pixels locally: source and design doc disagree | unreachable from `robotd`'s socket either way, so our `Detector` protocol is still the stand-in |
| `stand_up` | no such RPC; `robotd` recovers from falls itself (limp → settle → ramp → standing policy) | `stand_up` sends `robot.enable {on: true}` and checks `safety.fallen` afterwards — and fails rather than claiming "upright" when nothing is reporting falls |

### What we do not touch

`robot.init` (moves every joint), `robot.relax` (the robot collapses), `system.*`, `net.*`,
`update.*`. The gamepad (`padd`) keeps authority on hardware; quackd does not arbitrate.
The same principle holds on every adapter: `disable_motors` is never sent to a Reachy,
`disable_torque` never to an arm, and a base over rosbridge gets a zero Twist, not silence.
On an Open Duck the guarantee is stronger than a promise: the bridge protocol has no word
that reaches torque, so going limp is unreachable rather than merely forbidden.

## How to help

**Built an Open Duck Mini v2?** That is the row most likely to flip this year, because it
is the only body here you can build from scratch and the only one whose robot side quackd
ships and already exercises. [open-duck-hardware-checklist.md](open-duck-hardware-checklist.md)
is the order to try it in, and there is an issue template waiting for the result.

Ran `--robot open_duck:bridge` against a duck you built, `microduck:jsonrpc` against a real
duck, `reachy_mini:sdk` against a Reachy Mini (or its `--mockup-sim` daemon), `lerobot:real`
against an arm, or `rosbridge:ws` against a bridge? Open an issue with `quackd doctor` output and the first lines of
`transcript.jsonl`. Every row above that flips from 🧪/⏳ to ✅ is one line in an
`upstream_api.py` and one row here.
