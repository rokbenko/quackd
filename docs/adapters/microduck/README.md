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
| `No daemon refuses a call because this number differs` | upstream's rule for two peers on different versions: "A bump promises nothing in either direction", robotd answers `hello` without comparing, and `robotctl` warns once and carries on, because "a call that cannot be served will name itself". What refuses instead is the call: `METHOD_NOT_FOUND` naming a method a release does not have, `INVALID_PARAMS` naming a member it does not know. The doc comment above the rule still says a peer on a different version "is refused rather than misparsed"; the rule and robotd's own `hello` say otherwise | why quackd serves a robotd it has not read. A verb whose call is refused either way fails naming the call and both versions, and a `hello` that is `METHOD_NOT_FOUND` says the socket is not robotd's, as does a `robot.subscribe` that is (`updaterd answers hello too`, below). An `IntentResult` that carries no `accepted` quackd can read is refused by nothing upstream, and quackd reads it as a refusal that names what came back |
| Framing | `NDJSON: one JSON-RPC 2.0 object per line` | wire. No one line robotd writes ends the link: one quackd cannot read, an answer whose `id` it did not send, and one whose `error` is not an object are skipped or fail the call they answer, and only robotd closing the socket, or the socket failing, ends it, every call after saying so at once |
| `robotd reads a connection's lines in order` | one task per connection reads its lines one at a time: a notification such as `robot.move` is stored as it is read, before the next line, and a request is answered before the line after it is read. State frames reach the connection from a broadcast the control loop publishes into, decimated to the rate asked for, so one built just before a twist was stored can still go out after the answer to a later call | when robotd can have read a twist: an answer to any call quackd made after it says it has, and only the frames read after that answer count against it (`robot.state.move.requested`, below) |
| `updaterd answers hello too` | `updaterd` shares robotd's protocol, answers `hello` with a version of its own, and refuses every `robot.*` call, `robot.subscribe` among them, with `METHOD_NOT_FOUND`, "robot.* is served by robotd, not updaterd" | a `robot.subscribe` refused that way, a call every robotd from v23 up has, is a socket that is not robotd's, and connect says so and names `/run/robotd.sock`, where it used to blame a robotd built from a protocol quackd had not read |
| Runtime dir | env `DUCK_RUNTIME_DIR` moves the directory each daemon publishes `/run/<service>/identity.json` under, for upstream's own tests and a daemon run by hand: "nothing on the board sets it". robotd's socket is a literal `--socket` default, which it does not move | not read. With no `--address`, quackd connects to `unix:///run/robotd.sock`, robotd's own default, and a robotd anywhere else, such as each duck upstream's `scripts/duck-sim` starts, is reached with `--address` |
| Sockets | `/run/robotd.sock`, `/run/configd.sock`, `/run/updaterd.sock`, `/run/padd/pad.sock` (pad.input only), `/run/tofd/tof.sock` (tof.stream only), `/run/mediad/media.sock` (media.frame only, and local). robotd's is `root:robot`, mode 0660, and robotd checks nothing else about who connects: "the group decides who may ask" | addresses, and why the account an ssh forward logs in as has to be in the `robot` group, unless it is root ([hardware checklist, step 1](hardware-checklist.md#1-reach-the-daemon-at-all)) |
| `hello` | params `{api_version}` → `{api_version, daemon_version, revision}`, the last two always on the wire and possibly `null` | connect |
| `robot.move` | **notification** `{vx, vy, vyaw}` m/s, rad/s, trunk frame, x forward, y left, +vyaw left | `move`, `go_to`, `search_scan` (re-sent every 100 ms). Never sent while posture is not `standing`, nor once robotd's state has not shown one it was sent (`robot.state.move.requested`). Once the duck stops standing while a verb walks, or robotd stops showing its twists, `robot.move` is held back for every verb walking until each stops, and each of the three stops at its first twist held back and fails saying why. The hold is each verb's own: a `stop` from another call, the pilot's among them, does not set walking again a verb still running. While it stands the state reads `unknown` and `extras.move_hold` says why. `doctor` prints a `move_hold` row as well, which reads `none` on the connection it opens itself |
| `robot.state.move.requested` | the twist robotd was last asked for, as it was asked: `robot.move` stores it untouched and every state frame reports it beside `applied`, the twist after the deadman and the limits, and robotd's own test holds it to that, "what the client asked for must survive to the stream" | the only place quackd can see a `robot.move` was taken, since robotd answers a notification with nothing, even one it could not parse. A twist the state has not shown three frames after it went out (0.3 s at the 10 Hz quackd subscribes at) is judged on the frames read after robotd answered a call quackd made after it (`robotd reads a connection's lines in order`, above): three more of those that still do not show it hold `robot.move` back as a duck that stops standing does, the verb that sent it fails saying what the state showed instead, and `extras.move_echo` keeps the last verdict. quackd asks robotd one `robot.health` as each walk starts, and another when a twist has gone the window unshown with no answer yet. A link that holds quackd's lines back holds the answer back with them, so a stall delays the verdict rather than stopping a walk robotd then takes, and one that holds them past a call's timeout is said to have left robotd unable to answer |
| `robot.stop` | request; zero velocity, *not* limp | `stop`, every run's final stop |
| `robot.head` | notification `{neck_pitch, head_pitch, head_yaw, head_roll}`, in joint space. robotd keeps one head slot, which this and `robot.look` both write, from any client, and `robot.state.head` reports it smoothed toward each new value a little every control tick, with any idle glance on top | holding a gaze. After a `robot.look`, the head it answered with goes out again every 1 s (`GAZE_HOLD_S`, half of robotd's 2 s wait, below) until a stop, the next look, a link that fails or the end of the connection, and `extras.gaze_hold` says what is held. Nothing goes out while the duck reads as anything but standing or sitting, and a head somebody turns meanwhile is let be rather than turned back once the duck stands if robotd said that somebody held the duck, or that it was limp or in a skill, none of which robotd glances at, or if its 2 s wait had not run out since the head last went out. After longer in any other state, a fall among them, a glance of robotd's own cannot be told from another client's head, and the head goes out again. Once `robot.state.head` moves away from the held head or stays off it, which is another client steering it, a gamepad posing the head among them, the hold lets go rather than pull the head back every second, and `extras.gaze_released` says `overridden` |
| `robot.look` | request `{x, y, z, neck_pitch}` → `{head, clamped}`. `head` is what went down the path `robot.head` feeds, and upstream's word on it is "resend these to hold the gaze" | `gaze`, and centring the head before `go_to` and `search_scan` steer. Its answer starts the gaze hold (`robot.head`, above). A `head` that is not exactly the four joints, each a number, starts none, since every re-send would zero or guess a joint |
| `robot.do` | request `{skill}` → `{accepted, reason?}`, answered on accept/refuse rather than on completion. `Skill` is now `String` — "a name, not an enumeration" — and `ground_pick | kick_left | kick_right | sit_toggle | roulade` are the names a *stock* robot answers to; a robot's skills are config, and an unknown one is refused with the list it does know | `kick`, `grab`, `sit`/`stand` |
| `robot.pose` | notification `{z, roll, pitch, active}` | `pose` intent (no verb yet) |
| `robot.enable` | request `{on, toggle?}` (`toggle` is `#[serde(default)]`, so `{on}` alone is valid). Policy execution, **not a flag**: upstream says it "can bring a limp robot up as a side effect of being asked to drive", so treat it as motion | `stand_up` |
| `robot.init` / `robot.relax` | power the joints + ramp to home pose (moves every joint) / torque **off** (collapse) | **never sent by quackd** |
| `robot.sound` | request `{tag, hold?}`; tags `alarm | greet | inquire | peck | chirp | coo | wheee` — no TTS | `quack` and `say` (text → tag) |
| `robot.subscribe` → `robot.state` | request `{hz?}`, then notifications `{t, move{requested,applied,limited_by}, head[4], policy, safety{fallen,limp,gravity,gain?,picked_up}, loop{hz,missed}, joints, targets, velocities?, currents_ma?, odom, theremin?, chorale?, t_ns, imu?, frames?, skeleton?}`. `head` is robotd's head slot, smoothed, idle glances included | state |
| `robot.state is not pushed until robot.subscribe` | the loop publishes into a bounded broadcast and never waits on a subscriber, so a slow client gets a gap rather than backpressure | why the transport subscribes inside `connect()` |
| `robot.subscribe -> SubscribeResult.skills` | the answer carries `{accepted, walk?, stand?, unavailable?, sitstand?, ground_pick?, skills[]}` — what is constant for the process | posture: a label that is one of the robot's own skills reads as `unknown`, a skill named `walk` included. The verbs still send the names a stock robot answers to, `kick_left`, `kick_right`, `ground_pick` and `sit_toggle` |
| `robot.state.policy`: `walk, stand, ground_pick, sit, rise, <skill>, <skill>:unwind, skill` while a network drives, `held, homing, rest, limp_fall, limp_pose` while none does | read in robotd's `control.rs` and `main.rs`. The label names what drove the tick, not where the duck is, but which of the first five it is follows the controller's seat latch: `sit` while it holds the seat, `rise` on the way up, `walk` or `stand` once the duck is up. `held` is also what a duck somebody holds reports. Every label but `rest`, the end of a shutdown, was there at `bc41fb5` too | `jsonrpc` reads `walk` and `stand` as standing and `sit`, matched exactly, as sitting. Every other label, a skill's own name, and a label quackd has never read are `unknown`, and so are a duck somebody holds (`safety.picked_up`, read before `safety.fallen`) and a limp one that has not fallen (`safety.limp`), with `extras.posture_reason` saying which. Every verb that walks refuses on `unknown`, `go_to`, `search_scan` and `approach_and` included. `sit`/`stand` read posture first and **refuse** when it is unknown, because upstream has one `sit_toggle` rather than a sit and a stand: firing it unaimed is a coin flip whose losing side sits a standing duck down |
| `safety.fallen gates nothing upstream` | "computed every tick … debounced 0.2 s", and "a report, not a rule". robotd does nothing else about a fall at this pin: `limp_fall`, its one answer to one, is off by default, and "With it off, a fall changes nothing" | refusing to walk a fallen duck is quackd's own rule, so quackd must read the frame |
| `safety.picked_up` | somebody is holding the duck: the pick-up detector has paused the policy, which reads `held`, until the duck is put down. Absent from a robotd older than the detector, "which reads as never picked up". First released in `daemon-v0.15.2`, on the API v37 that `daemon-v0.15.0` and `daemon-v0.15.1` report without it | read before `safety.fallen`, since a duck held at an angle reads as fallen too. Nothing that moves the duck is sent while it is true, and a walk it interrupts stays stopped (`robot.move`, above). The version cannot say whether a robot has it, so `extras.pickup_detection` says, from the frame, and `doctor` warns when it does not: nothing then tells quackd that somebody is holding the duck. `extras.picked_up` is then `null`, not `false`, and `stand_up` refuses to ask the policy to drive a duck nobody can say is not held |
| `robot.health` | request → `{healthy, degraded?, reason?, battery{volts,percent}?, motors?, cpu_temp_c?, cpu_throttle?, control_loop?, bus, imu?}` | heartbeat every 500 ms; battery abort |
| `robot.mode` / `robot.setMode` | `{mode}`, `walk` or `roller` | (not used yet) |
| `robot.policies` | request → `{mode, enabled, slots, skills, homed?, sitting?}`: what each policy slot runs, whether the duck has reached its home pose, and whether it is "parked in its seat" | documented, not called: every state frame's label already says `sit` and `homing`, so asking would cost a request per state read for nothing new |
| `tof.stream` → `tof.frame` | 8×8 depth on tofd's socket | (not used yet) |
| `pad.input` | gamepad raw tap; the pad is the authority | documented, not used |
| `padd drives while a pad is connected` | from the moment a gamepad connects, `padd` sends `robot.move` on every tick its sticks ask for motion, and re-sends an unchanged stop every 100 ms while they do not, so robotd's twist never ages past its deadman while a pad sits idle. robotd keeps the newest twist from any client. With the pad gone, `padd` sits the robot down a second later | nothing, since the pad is the authority. But a connected pad nobody is touching tells robotd to stand still at least ten times a second, over any twist quackd sends, so it fights every walk, and once three state frames after robotd's answer show only its stop, quackd holds the walk back (`robot.state.move.requested`) |
| `robotd intent deadman` | "if intents stop arriving, the velocity goes to zero. Stop is not limp" | why `move` re-sends |
| `RobotState.frames` | where the camera and the ToF sensor are in the trunk frame at the measured head joints, the camera in OpenCV axes (+x right, +y down, +z along the optical axis). On the stream since API v24, first released in `daemon-v0.12.0` | the camera's optical axis is `extras.head_yaw_deg` and `head_pitch_deg`: where the camera points from the trunk, in degrees, positive left and up, the convention the simulators have always published `head_yaw_deg` in, with `extras.head_pose_from` saying `frames.camera`. Each `/frame` also carries the yaw the newest state showed when quackd asked for it, the head the picture was taken through. `extras.robotd.state_frames` records whether a robot reports it, since the floor serves robots that do not |
| `+head_yaw looks left (+y) on alpha` | upstream's own test of its head FK pins this sign, and that "+head_pitch looks DOWN (-z) on alpha", so that an update to the model cannot make the robot nod the wrong way | on a robotd that reports no `frames`, the measured head_yaw joint is `extras.head_yaw_deg`, and `head_pose_from` says `joints`. No pitch comes that way: the camera's pitch is the neck and head pitch joints through the FK together, and robotd's home pose holds both at 0.3491 rad |
| `robotd's idle look-around` | new in `daemon-v0.16.1`, the first release with it, on API v41. Once the duck is still and nobody has steered its head for 2 s, robotd glances around on top of the commanded head, up to 0.6 rad of yaw and 0.2 rad of pitch, fading in and out over 1.5 s rather than jumping. A `robot.look` or `robot.head` from anyone holds it off for another 2 s | quackd holds a gaze against it (`robot.head`, above). On a robotd from API v41 up, `go_to` and `search_scan` wait 1.5 s after centring the head, sending nothing that moves the duck, since a glance under way when the look arrived takes that long to fade out, and on `/frame` the first frame they act on is one taken after the wait (`a frame asked for is taken after it is asked for`, below). There `search_scan` also centres the head again after each turn, since the stop that ends a turn ends the gaze hold too. `daemon-v0.16.0`, on API v39, has no look-around, and neither verb waits there. An `observe` with no gaze held, the first of a run or one after a stop, can still see a glancing head once the duck has stood still for 2 s |
| `velstand.onnx` | the walk a stock robotd loads at this pin: one network that "walks on a twist and stands still at zero command", so no standing network is loaded by default. That is why `limp_fall`, whose stand-up hands the duck to the standing network, is off | what a real duck walks on. `microduck:mujoco` still runs `alpha_walking` and `alpha_stand` ([below](#the-ducks-physics-upstreams)) |
| `GET :8080/frame` | `mediad`'s console port answers with one upright PNG per request, encoded from the whole picture on the robot with "No downscale", uncached and at most four at a time. When it cannot hand one out, four being encoded already or no frame from its capture within 3 s, it answers `503` "Camera snapshot unavailable; retry when capture is running." Port 8080 on every address by default, with no authentication. New since `bc41fb5`, where the port served the console page alone, and first released in `daemon-v0.13.0` | the camera this page starts with ([below](#getting-a-picture-off-a-real-microduck)): pulled at a rate of its own, waited for through a `503` at connect, and its first frame's shape taken for the lens. `extras.robotd.mediad_frame` says whether the route has answered |
| `a frame asked for is taken after it is asked for` | `mediad` copies a frame out of its capture only for a reader that has asked for one, and "A reader waits for the next frame rather than taking the last one. The demand has to be answered by a capture that happens *after* it". `GET /frame` asks for its picture that way, through `media.frame`, once the request has reached it, so the PNG it answers with was taken after the request went out. The PNG carries no capture time | quackd stamps each `/frame` with the moment its request went out, which the picture is no older than. A frame's age runs from then, so one slow to come down the tunnel is as old as it is, and one slower to come than the 2 s quackd steers on is not handed out. `go_to` and `search_scan` act first on a frame taken after the head was centred and a glance waited out, and after each turn `search_scan` looks, and `go_to` steers, only on a frame taken once the turn had settled. Each frame also carries the head's yaw from the newest state then (`RobotState.frames`, above). Any other HTTP camera may hand out a picture it took before it was asked, and a WebRTC frame arrives some unknown while after it was taken, so on those the verbs act on the newest frame, as before |
| `media.frame` | one raw UYVY frame on `mediad`'s local socket: a JSON header `{width, height, format, bytes, captured_at_unix_us, rotate}`, then the bytes. "Deliberately not a `Call`", so it never reaches the WebRTC control datachannel | not called |
| `media.video` | `{width, height, rotate, mono_ns, real_ns, intrinsics?}`, which `mediad` answers itself on each WebRTC peer's control datachannel, as a call and as a push when the channel opens. The push can be lost, so upstream's own console asks, "because a question it asks when it is ready cannot arrive too early". `rotate` is "Degrees clockwise the camera is mounted from upright", and `intrinsics`, absent when the geometry is unknown, are for the frame as it is sent, which nothing turns: a consumer that turns the frame swaps `fx` with `fy` and `cx` with `cy` | the WebRTC camera asks once, the one line it writes to the channel, and reads the answer or the push. It turns every frame upright by `rotate` and hands out no frame until one of the two has said it. The lens of an upright frame comes from `intrinsics`, as twice the arctangent of half the upright width over the focal length across it, and goes in the manifest as `camera_fov_deg` |
| `an alpha's camera is mounted a quarter turn off upright, a beta's upright` | `rotate` is 90 on the Zero 3W, the alpha board, and 0 on the beta. Nothing on the robot turns the pixels, so the WebRTC track off an alpha is sideways and its consumer turns it clockwise by `rotate`, as upstream's `uyvy` crate does for `mediad`'s PNGs and its duck detector: a quarter turn sends source (x, y) to upright (h-1-y, x) | quackd turns each WebRTC frame the same way. A `mediad` started with `--flip-in-pipeline` turns the pixels as well and still reports the mount, so quackd would turn its frames twice, as upstream's console would |
| `the alpha family's camera calibration` | one solve shared by every alpha unit, made on a 1280 by 720 frame as `mediad` sends it, unturned: `fx` 1061.8, `fy` 1062.2, `cx` 596.8, `cy` 474.5. `mediad` sends it, scaled to the stream, as `media.video`'s `intrinsics`, unless the robot carries a solve of its own | an upright alpha frame is as wide as the rows it was sent with, across `fy`, so its lens is 37.4°. Its principal point is not the frame's centre, and quackd's detectors, which take a bearing from the centre, do not correct for it |
| `an IMX219 sees 62 degrees across its frame's long side` | the alpha's head camera behind its M12 lens: its field of view across a frame in the 1920 by 1080 mode `mediad` pins, the same at every size it streams at, since each is the sensor's whole field scaled. The beta's GC2093 has none: "nobody has measured this lens yet" | what the lens of a tall `/frame` is worked out from (the UNVERIFIED row below) |
| `rendezvous control lane` | what upstream sends a server-side agent over now, in place of the WebSocket gateway it once designed: JSON-RPC over HTTP/SSE through upstream's rendezvous service, which `mediad`'s relay bridges to the robot's own sockets. Upstream names two limits on it. "No pixels", since video "would be base64 inside JSON", so frames come from WebRTC or `media.stream`. And "What it is not is a teleop lane": "The rendezvous allows 1200 requests per 60 s per peer", and going over it takes the whole peer off the service, the robot's own lease included | not used. A walk under quackd re-sends its twist against robotd's deadman until it ends, a joystick's rate rather than a behaviour's, so quackd does not drive over it. `microduck:websocket` is deprecated, and a robot elsewhere is reached over ssh with `microduck:jsonrpc` |
| `media.detections` | `mediad`'s duck detector, `{width, height, took_ms, boxes[{x0,y0,x1,y1,score}]}`, pushed on each WebRTC peer's control datachannel and nowhere else. It finds ducks, not balls. Off by default, and two looks a second when on, which upstream calls "a thermal number, not a taste" | the WebRTC camera keeps the newest one. Nothing steers on it |

### UNVERIFIED (designed, assumed, or missing upstream) — and what we do

| Thing | Status upstream | What quackd does |
|---|---|---|
| `WebSocket agent gateway` | dropped. At `bc41fb5`, architecture.md §5.3 designed one, "open a WebSocket, poll a frame, send intents". At this pin the same table sends a server-side agent over the "rendezvous control lane", JSON-RPC over HTTP/SSE with no WebRTC, and roadmap M5's SDK is that lane | `--robot microduck:websocket` is a stub that refuses to connect, with the links. It is deprecated and is removed in 0.19.0, and `quackd run`, `doctor` and `serve-mcp` say so in one line on stderr when they connect through it, naming `--robot microduck:jsonrpc` with `--camera-url http://127.0.0.1:8080/frame` instead |
| `stand_up asks robotd's policy to drive (robot.enable), which upstream does not promise gets a fallen duck up` | there is no stand-up call for a duck on the floor. `robot.enable` is policy execution, which "can bring a limp robot up as a side effect of being asked to drive", and `robot.init`, upstream's stand-up for a duck on its feet, moves every joint. robotd's own stand-up after a fall was the end of `limp_fall`, which is off by default at this pin. Whether velstand gets a duck off the floor has not been seen | `stand_up` reads the duck before it sends anything. It refuses a duck somebody holds and one nothing reports on, says so of one already standing, and points one on its seat at `stand`. It refuses as well while a walk is held back for a verb still running, and on a robot that does not report `safety.picked_up` at all, since nothing there can say the duck is not in somebody's hands. Only a fallen duck, or one whose posture is `unknown` for another reason, is sent `robot.enable {on: true}`, and afterwards it is called upright only if posture reads `standing`. Every state lists this in `extras.assumptions`, which the pilot is told not to take for the robot's own work |
| `a tall /frame is assumed to be an alpha's IMX219, 62 degrees from top to bottom` | quackd's own reading of a PNG that carries no lens. On a stock robot only an alpha's camera, mounted a quarter turn off and turned upright for the PNG, gives a picture taller than wide, and a beta's gives a wide one. It is the module's design with the principal point at the centre, not a calibration, and it rests on the shape alone: upstream's `scripts/duck-sim` renders its twin's camera rolled like an alpha's at MuJoCo's 45° across the frame as sent, so a tall `/frame` from a twin is 45° wide and this is wrong there | the first frame of a working `/frame` camera, when taller than wide, sets the manifest's `camera_fov_deg` to 2·atan(tan 31° · w/h) and names this assumption beside it, so detections through that lens say they are uncalibrated and the log says to pass `--fov-deg`, and every state lists this in `extras.assumptions`. `--fov-deg` overrides it on `quackd run`, and from then on the state lists it no more and `extras.camera` says the lens was given. A wide frame gets no lens |

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
need eyes, instead of advertising sight the robot has not got. `mediad` on the duck serves the
camera two ways quackd reads, and its own snapshot route, `/frame`, is the one to start with:

| | `--camera-url http://127.0.0.1:8080/frame` | `--camera-url webrtc://127.0.0.1:8443` |
|---|---|---|
| What it is | `mediad`'s snapshot route: one upright PNG per request | `mediad`'s H.264 video track, which quackd turns upright |
| The tunnel | `ssh -L 8080:127.0.0.1:8080 microduck@<duck>` | `ssh -L 8443:127.0.0.1:8443 microduck@<duck>` |
| Needs | nothing | `quackd[microduck-camera]` (aiortc, av, websockets) |
| How often | 2 frames a second | as fast as the track decodes |
| The lens | assumed from the first frame's shape | `mediad`'s own calibration |
| Costs | a PNG of the whole picture, encoded on the duck for every frame | one media session, so it competes with the browser console |
| Since | `daemon-v0.13.0` | every release quackd serves |

Neither installs anything on the duck, and neither authenticates: `mediad` answers on every
address, and upstream's own note on its WebRTC side is that a pairing PIN which is `000000` on
every robot "authenticates nobody". Reach both through the tunnel rather than across a room's
wifi.

**`/frame`** is pulled on a timer and the newest frame is served from memory, so `observe` costs
no round trip, and a failed fetch is reported rather than raised. quackd asks mediad for 2 frames
a second, where any other HTTP camera is asked for 5, since every request has the duck encode a
PNG of its whole picture and nobody has measured what that costs a robot, and a frame older than
2 s is not handed out, counted from the moment quackd asked for it, since `mediad` takes the
picture after the request arrives, so one slow PNG stops `go_to` rather than steering it on an old
picture. A camera this slow is too slow for the way `go_to` steers on a stream, holding the turn a
frame calls for until a newer frame comes, which can carry the duck past what the frame measured.
On `/frame` `go_to` measures, then moves: it turns no further than each frame measured, walks on
straight while the turn settles, and steers again only on a frame taken after it, so it closes on
a target more slowly than on a faster camera. At connect quackd waits up to 5 s for the first
frame, through the `503` mediad answers while it cannot hand one out and through requests that run
out of time, and gives up at once on anything else, such as a refused connection or the `404` of a
robot too old for the route. Whatever came last is in `camera_health()`, which `quackd doctor`
prints under the camera's row, and a verb that finds no frame says it.

The PNG carries no lens. A first frame taller than wide is taken for an alpha's, whose IMX219
sees 62° along its long side and is turned upright for the PNG, so across a 720 by 1280 frame,
the default 720p turned upright, quackd measures through 37.3°. That goes in the manifest as
`camera_fov_deg`, beside the assumption it rests on, so a run and an MCP session measure through
it, their detections say they are uncalibrated, and the log says to pass `--fov-deg`. Every state
lists the assumption in `extras.assumptions` (the UNVERIFIED row above) until `--fov-deg`
overrides the lens on `quackd run`, after which it lists it no more. A frame wider than tall,
which is a beta's, gets no lens: pass `--fov-deg` there. Through a lens narrower than 45°,
`search_scan` turns in steps smaller than its usual 45°, so that each view overlaps the next.

**WebRTC** is the stream. Signalling is gst-plugins-rs `net/webrtc`, read from
`mediad/webclient/index.html` at the pin: the producer offers and quackd answers.

On an alpha the WebRTC track is sideways: the camera is mounted a quarter turn off upright and
nothing on the robot turns the pixels. `mediad` says by how much, as `media.video`'s `rotate`,
and quackd turns every frame upright by it, the way upstream's own code does, and hands out no
frame until `mediad` has said. A `mediad` that never says leaves the run with no camera, and the
reason in `camera_health()`, rather than one that steers on a picture on its side. The same
answer carries the lens, and across an upright alpha frame it is 37.4° by the family's
calibration. It goes in the manifest as `camera_fov_deg`, so a run and an MCP session measure
bearings through it with no `--fov-deg`, and `camera_health()` reports the turn, the lens and
the intrinsics.

`mediad` opens a `control` datachannel at every peer whether it wants one or not. quackd asks it
once what the video is, reads `media.video` and `media.detections` off it, and writes nothing
else: motion goes over `robotd`'s socket, where the allowlist, the confirm gates and the deadman
feed already are, and two ways to move one robot would mean two places to be sure about.

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

Read: 2026-10-09, pinned at
[`2b25a48`](https://github.com/pollen-robotics/microduck_rl/tree/2b25a48b08f1f17bc38c90bb03144c81fbd9ed07)
(`develop`, 2026-09-06) and policies at
[`6677ec0`](https://huggingface.co/pollen-robotics/microduck-policies/tree/6677ec007142bbaf05123a468f6ae58d13808786)
(`v7`, 2026-10-05), the set daemon-v0.16.1 seeds a robot with. The first read, on 2026-09-07,
pinned the policies at `088524a`, from before `v5`, and `alpha_walking.onnx` and
`alpha_stand.onnx` are the same bytes at both, so the simulator walks as it did. `develop` had
moved on to `273afe0` by this read, and every file quackd fetches from it is the same bytes
there, so the model's commit stays.

| What | Status | Why it matters |
|---|---|---|
| `robot_walk.xml` and its 38 STL meshes | **VERIFIED** | the body: one free joint, 14 hinges, position actuators, an IMU and a head camera |
| `alpha_walking.onnx`, `alpha_stand.onnx` | **VERIFIED** | `obs[1,61] → actions[1,14]`, the normaliser baked in, Apache-2.0 on the Hub. Not what a stock robot runs: every robotd from daemon-v0.13.0 walks and stands on `velstand.onnx` (the robot's own row above), and keeps this pair in the set "for a board that loads them back by hand" |
| observation layout, 13-value command, `ctrl = default_pose + action`, 50 Hz | **VERIFIED** | read from `scripts/infer_policy.py`; the same layout appears in the daemon and in Pollen's own browser simulator |
| projected gravity is the world's `-z` in the trunk frame | **VERIFIED** | get its sign wrong and the duck braces and stands still for every command, silently |
| the gait floor: `no gait below vx 0.23 m/s or wz 1.0 rad/s; above it about 0.38x the commanded speed` | **UNVERIFIED** | measured here on one machine with the XML's own actuators, and re-measured on 2026-09-17 when MuJoCo 3.13 moved it up from 0.22: the floor belongs to the physics build as much as to the policy. Upstream trains and deploys with a different actuator model, so a real duck may track commands directly |
| `a positive head_pitch in the command vector tilts the camera down` | **UNVERIFIED** | measured by driving the command and watching the rendered head camera, not read anywhere, so quackd negates its own pitch to make looking up positive. `neck_pitch` and `head_roll` are left at zero: quackd's gaze has one pitch and no roll, so nothing has exercised them |
| the four stand-ins, in place of `ball_kick_left.onnx, ball_kick_right.onnx, alpha_ground_pick.onnx, alpha_sitstand.onnx` | **UNVERIFIED** | upstream's episodic policies did nothing from a standing pose when tried and the sit-stand one toppled the model, so these four are quackd's stand-ins and say so in `extras.assumptions`. The sit-stand file tried was the one before `v6`, which replaced it with one upstream says "no longer tips over backward when the head moves abruptly while seated", and that one has not been tried |

The head camera is the one place quackd deliberately does not do what the file says: upstream's
`<camera>` quaternion is not MuJoCo's viewing convention, so rendering through it looks
backwards into the duck's own shell. quackd renders from the camera's position along the head
body's forward axis instead.
