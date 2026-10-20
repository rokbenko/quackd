# Microduck

A 25 cm biped from Pollen Robotics, installed with `quackd[microduck]`. `microduck:sim2d` is
the cartoon simulator and the default, `microduck:mujoco` is the same robot in MuJoCo, walking
on `alpha_walking.onnx`, which Pollen trained before the `velstand.onnx` a stock robot walks on
now (`quackd[mujoco]`), and `microduck:jsonrpc` is the real one, over `robotd`, which quackd has
never run on a duck. Every backend's row, and how far it has got, is in
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
quackd reads two: `safety.picked_up`, whether somebody holds the duck, and `frames`, where the
camera points, for `extras.head_yaw_deg` and `head_pitch_deg`. A robotd on API v23 reports
neither, so there nothing tells quackd that the duck is held, and the head's yaw comes off the
head_yaw joint, with no pitch (the `safety.picked_up` and `RobotState.frames` rows below). A
third, `robot.health`'s `cpu_throttle`, reaches `extras.health` with the rest of that answer,
which quackd passes on as robotd sent it. Upstream's own rule for the number is that "A bump
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

Upstream released `daemon-v0.16.2`, `v0.16.3`, `v0.17.0` and `v0.17.1` on 2026-10-12, each on
API v41, so quackd serves them without a warning. Read against this pin, none changes the shape
of anything quackd sends or reads, and robotd's own source is the same outside its tests, but
`daemon-v0.16.2` changed three defaults the rows below cite: `limp_fall` is on, a robot is seeded
with the policy set `v8`, and a gamepad's full stick asks for 0.4 m/s forward and 1.0 rad/s of
turn. Those rows say what changed beside what they read at this pin.

Upstream released `daemon-v0.17.2` to `v0.17.6` from 2026-10-13 to 2026-10-17. `v0.17.2` is on API
v41 and is served without a warning, and from `v0.17.3` they are on API v44, which quackd serves
with one. The three bumps add only calls quackd does not make, `robot.vetbook` and the calls around
it, and none changes the shape of anything quackd sends or reads. From `v0.17.3` the idle
look-around also leans the neck, opens the beak and yawns after 45 s, and its 2 s and 1.5 s timings
became `[idle]` settings in `robotd.toml`. At the shipped values quackd's gaze hold and its fade
wait still hold, since a `robot.look` or `robot.head` still clears every part of it within the fade.
A robot retuned to glance sooner or fade longer is not one quackd can see: its gaze hold then lets
go, taking the glance for another client's head, and `go_to` and `search_scan` can steer on a frame
a glance is still fading out of.

Sources: [duck-ipc-proto/src/lib.rs](https://github.com/pollen-robotics/microduck/blob/d818af5d91fce0e3f5f8c22d47d47caf7b3f17a1/duck-ipc-proto/src/lib.rs) ·
[robotd/src](https://github.com/pollen-robotics/microduck/tree/d818af5d91fce0e3f5f8c22d47d47caf7b3f17a1/robotd/src) ·
[robotd-params/src/lib.rs](https://github.com/pollen-robotics/microduck/blob/d818af5d91fce0e3f5f8c22d47d47caf7b3f17a1/robotd-params/src/lib.rs) ·
[mediad/src](https://github.com/pollen-robotics/microduck/tree/d818af5d91fce0e3f5f8c22d47d47caf7b3f17a1/mediad/src) ·
[architecture.md](https://github.com/pollen-robotics/microduck/blob/d818af5d91fce0e3f5f8c22d47d47caf7b3f17a1/docs/design/architecture.md) ·
[robotd-design.md](https://github.com/pollen-robotics/microduck/blob/d818af5d91fce0e3f5f8c22d47d47caf7b3f17a1/docs/design/robotd-design.md) ·
[remote-webrtc.md](https://github.com/pollen-robotics/microduck/blob/d818af5d91fce0e3f5f8c22d47d47caf7b3f17a1/docs/design/remote-webrtc.md).

### VERIFIED (read from upstream source)

| Thing | Value | Used for |
|---|---|---|
| API version | `41` | `hello` handshake: the version quackd read, and the one it sends. A robotd on any other version from the floor up is served, with a warning in the log, in `doctor` and in `extras.robotd`, which also names the release and the revision the robot answered with |
| API floor | `23`, the version note for v23 in `lib.rs` | the oldest `duck-ipc-proto` quackd has read end to end, the previous read's, on which `daemon-v0.11.0` was the first release. A robotd below it is refused at connect, naming both numbers, and so is a `hello` that names no integer version. A robotd at it reports no `safety.picked_up` and no `frames` |
| `No daemon refuses a call because this number differs` | upstream's rule for two peers on different versions: "A bump promises nothing in either direction", robotd answers `hello` without comparing, and `robotctl` warns once and carries on, because "a call that cannot be served will name itself". What refuses instead is the call: `METHOD_NOT_FOUND` naming a method a release does not have, `INVALID_PARAMS` naming a member it does not know. The doc comment above the rule still says a peer on a different version "is refused rather than misparsed"; the rule and robotd's own `hello` say otherwise | why quackd serves a robotd it has not read. A verb whose call is refused either way fails naming the call and both versions, and a `hello` that is `METHOD_NOT_FOUND` says the socket is not robotd's, as does a `robot.subscribe` that is (`updaterd answers hello too`, below). An `IntentResult` that carries no `accepted` quackd can read is refused by nothing upstream, and quackd reads it as a refusal that names what came back |
| Framing | `NDJSON: one JSON-RPC 2.0 object per line` | wire. No one line robotd writes ends the link: one quackd cannot read, an answer whose `id` it did not send, and one whose `error` is not an object are skipped or fail the call they answer, and only robotd closing the socket, or the socket failing, ends it, every call after saying so at once |
| `robotd reads a connection's lines in order` | one task per connection reads its lines one at a time: a notification such as `robot.move` is stored as it is read, before the next line, and a request is answered before the line after it is read. State frames reach the connection from a broadcast the control loop publishes into, decimated to the rate asked for, so one built just before a twist was stored can still go out after the answer to a later call. The same task writes the frames and the answers, a line at a time, and writes a request's answer straight after it handles the request, so every frame written before the answer to a `robot.stop` was built before the stop zeroed the twist | when robotd can have read a twist: an answer to any call quackd made after it says it has, and only the frames read after that answer count against it. And when it can no longer show one: no frame read after robotd answered a stop written after the twist counts against it (`robot.state.move.requested`, below) |
| `updaterd answers hello too` | `updaterd` shares robotd's protocol, answers `hello` with a version of its own, and refuses every `robot.*` call, `robot.subscribe` among them, with `METHOD_NOT_FOUND`, "robot.* is served by robotd, not updaterd" | a `robot.subscribe` refused that way, a call every robotd from v23 up has, is a socket that is not robotd's, and connect says so and names `/run/robotd.sock`, where it used to blame a robotd built from a protocol quackd had not read |
| Runtime dir | env `DUCK_RUNTIME_DIR` moves the directory each daemon publishes `/run/<service>/identity.json` under, for upstream's own tests and a daemon run by hand: "nothing on the board sets it". robotd's socket is a literal `--socket` default, which it does not move | not read. With no `--address`, quackd connects to `unix:///run/robotd.sock`, robotd's own default, and a robotd anywhere else, such as each duck upstream's `scripts/duck-sim` starts, is reached with `--address` |
| Sockets | `/run/robotd.sock`, `/run/configd.sock`, `/run/updaterd.sock`, `/run/padd/pad.sock` (pad.input only), `/run/tofd/tof.sock` (tof.stream only), `/run/mediad/media.sock` (media.frame only, and local). robotd's is `root:robot`, mode 0660, and robotd checks nothing else about who connects: "the group decides who may ask" | addresses, and why the account an ssh forward logs in as has to be in the `robot` group, unless it is root ([hardware checklist, step 1](hardware-checklist.md#1-reach-the-daemon-at-all)) |
| `hello` | params `{api_version}` → `{api_version, daemon_version, revision}`, the last two always on the wire and possibly `null` | connect |
| `robot.move` | **notification** `{vx, vy, vyaw}` m/s, rad/s, trunk frame, x forward, y left, +vyaw left | `move`, `go_to`, `search_scan` (re-sent every 100 ms). Never sent while posture is not `standing`, nor once robotd's state has not shown one it was sent (`robot.state.move.requested`). Once the duck stops standing while a verb walks, or robotd stops showing its twists, `robot.move` is held back for every verb that has walked until each ends, and each of the three stops at its first twist held back and fails saying why. The hold is each verb's own and lasts until it ends: no stop lifts it, not a `stop` from another call, the pilot's among them, and not the verb's own stop between two of `search_scan`'s turns. While it stands the state reads `unknown` and `extras.move_hold` says why. `doctor` prints a `move_hold` row as well, which reads `none` on the connection it opens itself |
| `robot.state.move.requested` | the twist robotd was last asked for, as it was asked: `robot.move` stores it untouched and every state frame reports it beside `applied`, the twist the control loop hands its controller: the one asked for, or zero once the deadman finds it stale, smoothed toward each new value and held at zero through robotd's limp-fall sequence. robotd's own test holds `requested` to what was asked, "what the client asked for must survive to the stream", so the deadman does not zero it. `robot.stop` does, and robotd answers the stop at once | the only place quackd can see a `robot.move` was taken, since robotd answers a notification with nothing, even one it could not parse. A twist the state has not shown three frames after it went out (0.3 s at the 10 Hz quackd subscribes at) is judged on the frames read after robotd answered a call quackd made after it (`robotd reads a connection's lines in order`, above), and only up to robotd's answer to the stop that ended its walk, since robotd zeroes its twist as it reads the stop. The first of those frames in each walk can have been built before the twist was stored, so it is left out, and two more that still do not show it, in one walk or across the walks of the verbs still running, hold `robot.move` back as a duck that stops standing does: three frames in a walk, as before, and the turns of a `search_scan`, each a walk the scan's own stop ends, judged together. The verb that sent it fails saying what the state showed instead, `extras.move_echo` keeps the last verdict, and `search_scan` counts the turns it says it made by the ones the state showed robotd took, saying so when that was none. quackd asks robotd one `robot.health` as each walk starts, and another when a twist has gone the window unshown with no answer yet. A link that holds quackd's lines back holds the answer back with them, so a stall delays the verdict rather than stopping a walk robotd then takes, and one that holds them past a call's timeout is said to have left robotd unable to answer. A stop robotd never answers leaves its walk unjudged, since nothing then says which frames came before the stop. A scan stops on the verdict only at a turn it sends after it: one whose turns hold fewer frames than a verdict takes, a single short turn among them, and one whose verdict comes with its last turn, end not finding the target on a robotd that took none of their turns, and say the state confirmed none of them, not that robotd has not taken robot.move |
| `robot.stop` | request; zero velocity, *not* limp | `stop`, every run's final stop |
| `robot.head` | notification `{neck_pitch, head_pitch, head_yaw, head_roll}`, in joint space. robotd keeps one head slot, which this and `robot.look` both write, from any client, and `robot.state.head` reports it smoothed toward each new value a little every control tick, with any idle glance on top | holding a gaze. After a `robot.look`, the head it answered with goes out again every 1 s (`GAZE_HOLD_S`, half of robotd's 2 s wait, below) until a stop, the next look, a link that fails or the end of the connection, and `extras.gaze_hold` says what is held. Nothing goes out while the duck reads as anything but standing or sitting, and a head somebody turns meanwhile is let be rather than turned back once the duck stands if robotd said that somebody held the duck, or that it was limp or in a skill, none of which robotd glances at, or if its 2 s wait had not run out since the head last went out. After longer in any other state, a fall among them, a glance of robotd's own cannot be told from another client's head, and the head goes out again. Once `robot.state.head` moves away from the held head or stays off it, which is another client steering it, a gamepad posing the head among them, the hold lets go rather than pull the head back every second, and `extras.gaze_released` says `overridden` |
| `robot.look` | request `{x, y, z, neck_pitch}` → `{head, clamped}`. `head` is what went down the path `robot.head` feeds, and upstream's word on it is "resend these to hold the gaze" | `gaze`, and centring the head before `go_to` and `search_scan` steer. Its answer starts the gaze hold (`robot.head`, above). A `head` that is not exactly the four joints, each a number, starts none, since every re-send would zero or guess a joint |
| `robot.do` | request `{skill}` → `{accepted, reason?}`, answered on accept/refuse rather than on completion. `Skill` is now `String` — "a name, not an enumeration" — and `ground_pick \| kick_left \| kick_right \| sit_toggle \| roulade` are the names a *stock* robot answers to; a robot's skills are config, and an unknown one is refused with the list it does know | `kick`, `grab`, `sit`/`stand` |
| `robot.pose` | notification `{z, roll, pitch, active}` | `pose` intent (no verb yet) |
| `robot.enable` | request `{on, toggle?}` (`toggle` is `#[serde(default)]`, so `{on}` alone is valid). Policy execution, **not a flag**: upstream says it "can bring a limp robot up as a side effect of being asked to drive", so treat it as motion | `stand_up` |
| `robot.init` / `robot.relax` | power the joints + ramp to home pose (moves every joint) / torque **off** (collapse) | **never sent by quackd** |
| `robot.sound` | request `{tag, hold?}`; tags `alarm \| greet \| inquire \| peck \| chirp \| coo \| wheee` — no TTS | `quack` and `say` (text → tag) |
| `robot.subscribe` → `robot.state` | request `{hz?}`, then notifications `{t, move{requested,applied,limited_by}, head[4], policy, safety{fallen,limp,gravity,gain?,picked_up}, loop{hz,missed}, joints, targets, velocities?, currents_ma?, odom, theremin?, chorale?, t_ns, imu?, frames?, skeleton?}`. `head` is robotd's head slot, smoothed, idle glances included | state |
| `robot.state is not pushed until robot.subscribe` | the loop publishes into a bounded broadcast and never waits on a subscriber, so a slow client gets a gap rather than backpressure | why the transport subscribes inside `connect()` |
| `robot.subscribe -> SubscribeResult.skills` | the answer carries `{accepted, walk?, stand?, unavailable?, sitstand?, ground_pick?, skills[]}` — what is constant for the process | posture: a label that is one of the robot's own skills reads as `unknown`, a skill named `walk` included. The verbs still send the names a stock robot answers to, `kick_left`, `kick_right`, `ground_pick` and `sit_toggle` |
| `robot.state.policy`: `walk, stand, ground_pick, sit, rise, <skill>, <skill>:unwind, skill` while a network drives, `held, homing, rest, limp_fall, limp_pose` while none does | read in robotd's `control.rs` and `main.rs`. The label names what drove the tick, not where the duck is, but which of the first five it is follows the controller's seat latch: `sit` while it holds the seat, `rise` on the way up, `walk` or `stand` once the duck is up. `held` is also what a duck somebody holds reports. Every label but `rest`, the end of a shutdown, was there at `bc41fb5` too | `jsonrpc` reads `walk` and `stand` as standing and `sit`, matched exactly, as sitting. Every other label, a skill's own name, and a label quackd has never read are `unknown`, and so are a duck somebody holds (`safety.picked_up`, read before `safety.fallen`) and a limp one that has not fallen (`safety.limp`), with `extras.posture_reason` saying which. Every verb that walks refuses on `unknown`, `go_to`, `search_scan` and `approach_and` included. `sit`/`stand` read posture first and **refuse** when it is unknown, because upstream has one `sit_toggle` rather than a sit and a stand: firing it unaimed is a coin flip whose losing side sits a standing duck down |
| `safety.fallen gates nothing upstream` | "computed every tick … debounced 0.2 s", and "a report, not a rule". robotd does nothing else about a fall at this pin: `limp_fall`, its one answer to one, is off by default, and "With it off, a fall changes nothing". From `daemon-v0.16.2` it is on by default again, and robotd poses a duck it catches falling and hands it back to the policy (the next row), while the verdict still gates nothing | refusing to walk a fallen duck is quackd's own rule, so quackd must read the frame |
| `robotd's limp-fall sequence` | `[safety] limp_fall`, "The only thing the daemon does about a fall": off by default at this pin, and on by default again from `daemon-v0.16.2`, whose robotd runs the same code outside its tests. `daemon-v0.11.0` and `daemon-v0.12.0` had it on by default too, handing the duck back to the standing network, and `daemon-v0.13.0` turned it off as velstand became the default walk. On, robotd takes a duck off its policy once fresh readings from a ready IMU predict a fall while the walking or standing network drives it, unless the duck is already down, and never during a skill, a ground pick, a sit or the rise from one, so it catches no fall during quackd's `kick` or `grab`. It holds the twist at zero until it hands the duck back. It lets the duck land limp, labelled `limp_fall` with `safety.limp` true, until it has landed or 1.5 s have passed, then ramps it to the standing pose over 0.6 s, labelled `limp_pose`, and hands it back to the policy, where "with nobody driving the standing network is what command magnitude selects — which is the stand-up". With no standing network loaded, as on a stock robot, `daemon-v0.16.2` says "the hand-back is to velstand at zero command, which stands still", and that velstand gets the duck up from there is upstream's word on `v8` (`velstand.onnx`, below). It drops the sequence only when something else takes the robot: the policy disabled, the torque off or the joints ramping home, the shutdown sit, or the power off | quackd sends no walk while it runs: both labels and `safety.limp` read as not standing, as `safety.fallen` does, and a verb that was walking stays held until it ends (`robot.move`, above). `stand_up` reads the duck every quarter second for up to ten seconds after its request, calls it upright as soon as it stands, and says so of a duck robotd still has in the sequence when that wait is over, and to run it again in a moment. The sequence is over within about 2.1 s, so that is a fall robotd caught during the wait, and a duck it had when `stand_up` was asked is back with the policy well before then and judged like any other. Whether the policy then gets the duck up nobody has seen under quackd |
| `safety.picked_up` | somebody is holding the duck: the pick-up detector has paused the policy, which reads `held`, until the duck is put down. Absent from a robotd older than the detector, "which reads as never picked up". First released in `daemon-v0.15.2`, on the API v37 that `daemon-v0.15.0` and `daemon-v0.15.1` report without it | read before `safety.fallen`, since a duck held at an angle reads as fallen too. Nothing that moves the duck is sent while it is true, and a walk it interrupts stays stopped (`robot.move`, above). The version cannot say whether a robot has it, so `extras.pickup_detection` says, from the frame, and `doctor` warns when it does not: nothing then tells quackd that somebody is holding the duck. `extras.picked_up` is then `null`, not `false`, and `stand_up` refuses to ask the policy to drive a duck nobody can say is not held |
| `robot.health` | request → `{healthy, degraded?, reason?, battery{volts,percent}?, motors?, cpu_temp_c?, cpu_throttle?, control_loop?, bus, imu?}` | heartbeat every 500 ms; battery abort |
| `robot.mode` / `robot.setMode` | `{mode}`, `walk` or `roller` | (not used yet) |
| `robot.policies` | request → `{mode, enabled, slots, skills, homed?, sitting?}`: what each policy slot runs, whether the duck has reached its home pose, and whether it is "parked in its seat" | documented, not called: every state frame's label already says `sit` and `homing`, so asking would cost a request per state read for nothing new |
| `tof.stream` → `tof.frame` | 8×8 depth on tofd's socket | (not used yet) |
| `pad.input` | the raw input stream of the pad `padd` drives from, which `padd` answers itself on its own socket. It is there because "`padd` polls the last known stick value and keeps sending it at 50 Hz": a radio that stops delivering still looks like a live driver to robotd, and only this stream shows the gap. A tap: nothing on the driving path depends on it | documented, not used |
| `padd drives while a pad is connected` | from the moment a gamepad connects, `padd` sends `robot.move` on every tick its sticks ask for motion, fifty a second, and re-sends an unchanged stop every 100 ms while they do not, so robotd's twist never ages past its deadman while a pad sits idle. robotd keeps one twist and ranks no client above another: `robot.move` sets it whoever sent it, and a continuous intent is "last-writer-wins". The pad's buttons are `robot.do` requests, and a pad nobody has configured runs `sit_toggle` on A. With the pad gone, `padd` sends nothing, and 1 s later asks robotd to sit a duck that is not sitting already | nothing: quackd sends `padd` nothing and does not try to out-rank it. A connected pad whose sticks nobody touches sends its stop every 100 ms and quackd re-sends its twist as often, so the twist the duck walks on flips between the two, and once three state frames after robotd's answer show the pad's twist and not quackd's, quackd holds the walk back (`robot.state.move.requested`). A stick that asks for motion goes out more often than quackd's twist, so it takes the walk over, and quackd's verb fails the same way soon after. quackd sends no walk to a duck the pad's sit button has sat, nor to one `padd` sat after the pad went |
| `robotd intent deadman` | "if intents stop arriving, the velocity goes to zero. Stop is not limp". robotd zeroes a twist older than `[safety] deadman_ms`, 500 ms by default | why `move` re-sends: every 100 ms while a verb walks, so the deadman does not fire during a quackd walk. It stops the legs once quackd's twists stop arriving, a tunnel pulled out or a process killed, and the [hardware checklist](hardware-checklist.md#8-pull-the-tunnel-out-mid-walk-with-the-pad-switched-off) tests it with no pad connected, since a pad's stops would halt the legs first |
| `robotd clamps no twist` | what limits a tick, `move.limited_by` in `robot.state`, is the deadman, a joint target past the actuator's travel, or one that is not finite, and nothing else. While the walking policy drives, it is handed the twist `robot.move` last asked for, smoothed toward each new value, until the deadman zeroes a stale one or a client holds the body-pose mode (`robot.pose` with `active` true), in which the controller zeroes it. The controller zeroes its own copy, so `move.requested` and `move.applied` never show that zero. `padd` sends a zero twist with every pose in its own body-pose mode, which `move.requested` does show, as it shows a pad's stops. And the policy "only follows commands inside the range it was trained on". `[pad_drive]`, what a gamepad's full stick asks for, is "what the *pad* asks for, not a limit on the robot": 0.3 m/s forward and back, 0.3 m/s sideways and 1.5 rad/s of turn at this pin, and from `daemon-v0.16.2`, since the `v8` velstand, 0.4 m/s forward and back and 1.0 rad/s of turn, "what that policy follows well on the robot" | the Microduck's speed caps are quackd's own: the manifest's `limits`, 0.3 m/s forward, 0.2 m/s sideways and 1.0 rad/s of turn, the turn the pad asks for from `daemon-v0.16.2`, which every verb that walks is held to and the datasheet calls quackd's clamp |
| `RobotState.frames` | where the camera and the ToF sensor are in the trunk frame at the measured head joints, the camera in OpenCV axes (+x right, +y down, +z along the optical axis). On the stream since API v24, first released in `daemon-v0.12.0` | the camera's optical axis is `extras.head_yaw_deg` and `head_pitch_deg`: where the camera points from the trunk, in degrees, positive left and up, the convention the simulators have always published `head_yaw_deg` in, with `extras.head_pose_from` saying `frames.camera`. Each `/frame` also carries the yaw the newest state showed when quackd asked for it, the head the picture was taken through. `extras.robotd.state_frames` records whether a robot reports it, since the floor serves robots that do not |
| `+head_yaw looks left (+y) on alpha` | upstream's own test of its head FK pins this sign, and that "+head_pitch looks DOWN (-z) on alpha", so that an update to the model cannot make the robot nod the wrong way | on a robotd that reports no `frames`, the measured head_yaw joint is `extras.head_yaw_deg`, and `head_pose_from` says `joints`. No pitch comes that way: the camera's pitch is the neck and head pitch joints through the FK together, and robotd's home pose holds both at 0.3491 rad |
| `robotd's idle look-around` | new in `daemon-v0.16.1`, the first release with it, on API v41. robotd counts the duck still once nothing asks it to move and nobody has steered its head for 2 s, and once it has counted it still for 2 s more it glances around on top of the commanded head, up to 0.6 rad of yaw and 0.2 rad of pitch. On a duck that stands still, that is about 4 s after the last `robot.look` or `robot.head`, and the look-around fades in rather than jumping, reaching its full size 1.5 s later. A `robot.look` or `robot.head` from anyone, or a twist, ends the count, and the look-around fades out over the same 1.5 s | quackd holds a gaze against it (`robot.head`, above). On a robotd from API v41 up, `go_to` and `search_scan` wait 1.5 s after centring the head, sending nothing that moves the duck, since a glance under way when the look arrived takes that long to fade out, and on `/frame` the first frame they act on is one taken after the wait (`a frame asked for is taken after it is asked for`, below). There `search_scan` also centres the head again after each turn, since the stop that ends a turn ends the gaze hold too. `daemon-v0.16.0`, on API v39, has no look-around, and neither verb waits there. An `observe` with no gaze held can still see a glancing head: the first of a run, on a duck that stood still before quackd connected, and any made once 3 to 4 s have passed since a stop, which ends the hold at most 1 s after the head last went out |
| `velstand.onnx` | the walk a stock robotd loads at this pin: one network that "walks on a twist and stands still at zero command", so no standing network is loaded by default. That is why `limp_fall`, whose stand-up hands the duck to the standing network, is off. `daemon-v0.16.2` turns it back on, and with no standing network loaded "the hand-back is to velstand at zero command, which stands still". It also seeds a robot with upstream's policy set `v8` in place of `v7`, and moves a board on an older official set up to it on update. `v8` changes no policy but `velstand.onnx`, and upstream says that from it velstand "gets up from a fall smoothly and reliably instead of violently or getting stuck on its back, and yaw commands are tracked" | what a real duck walks on. Nobody has seen either set get a duck up under quackd. `microduck:mujoco` still runs `alpha_walking` and `alpha_stand` ([below](#the-ducks-physics-upstreams)) |
| `GET :8080/frame` | `mediad`'s console port answers with one upright PNG per request, encoded from the whole picture on the robot with "No downscale", uncached and at most four at a time. When it cannot hand one out, four being encoded already or no frame from its capture within 3 s, it answers `503` "Camera snapshot unavailable; retry when capture is running." Port 8080 on every address by default, with no authentication. New since `bc41fb5`, where the port served the console page alone, and first released in `daemon-v0.13.0` | the camera this page starts with ([below](#getting-a-picture-off-a-real-microduck)): pulled at a rate of its own, waited for through a `503` at connect, and its first frame's shape taken for the lens. `extras.robotd.mediad_frame` says whether the route has answered |
| `a frame asked for is taken after it is asked for` | `mediad` copies a frame out of its capture only for a reader that has asked for one, and "A reader waits for the next frame rather than taking the last one. The demand has to be answered by a capture that happens *after* it". `GET /frame` asks for its picture that way, through `media.frame`, once the request has reached it, so the PNG it answers with was taken after the request went out. The PNG carries no capture time | quackd stamps each `/frame` with the moment its request went out, which the picture is no older than. A frame's age runs from then, so one slow to come down the tunnel is as old as it is, and one slower to come than the 2 s quackd steers on is not handed out. `go_to` and `search_scan` act first on a frame taken after the head was centred and a glance waited out, and after each turn `search_scan` looks, and `go_to` steers, only on a frame taken once the turn had settled. Each frame also carries the head's yaw from the newest state then (`RobotState.frames`, above). Any other HTTP camera may hand out a picture it took before it was asked, and a WebRTC frame arrives some unknown while after it was taken, so on those the verbs act on the newest frame, as before |
| `media.frame` | one raw UYVY frame on `mediad`'s local socket: a JSON header `{width, height, format, bytes, captured_at_unix_us, rotate}`, then the bytes. "Deliberately not a `Call`", so it never reaches the WebRTC control datachannel | not called |
| `media.video` | `{width, height, rotate, mono_ns, real_ns, intrinsics?}`, which `mediad` answers itself on each WebRTC peer's control datachannel, as a call and as a push when the channel opens. The push can be lost, so upstream's own console asks, "because a question it asks when it is ready cannot arrive too early". `rotate` is "Degrees clockwise the camera is mounted from upright", and `intrinsics`, absent when the geometry is unknown, are for the frame as it is sent, which nothing turns. A consumer that turns the frame a quarter turn swaps `fx` with `fy`, and moves the principal point as it moves every pixel: clockwise, from (`cx`, `cy`) to (h-1-`cy`, `cx`) in a frame h tall as sent, where upstream's note says only that `cx` and `cy` swap | the WebRTC camera asks once, the one line it writes to the channel, and reads the answer or the push. It turns every frame upright by `rotate` and hands out no frame until one of the two has said it. The lens of an upright frame comes from `intrinsics`, as twice the arctangent of half the upright width over the focal length across it, and goes in the manifest as `camera_fov_deg` |
| `an alpha's camera is mounted a quarter turn off upright, a beta's upright` | `rotate` is 90 on the Zero 3W, the alpha board, and 0 on the beta. Nothing on the robot turns the pixels, so the WebRTC track off an alpha is sideways and its consumer turns it clockwise by `rotate`, as upstream's `uyvy` crate does for `mediad`'s PNGs and its duck detector: a quarter turn sends source (x, y) to upright (h-1-y, x) | quackd turns each WebRTC frame the same way. A `mediad` started with `--flip-in-pipeline` turns the pixels as well and still reports the mount and the size as the camera takes it, so a frame that comes in tall where `media.video` describes a wide one is one the pipeline turned, and quackd hands it out as it came rather than turn it onto its side again |
| `the alpha family's camera calibration` | one solve shared by every alpha unit, made on a 1280 by 720 frame as `mediad` sends it, unturned: `fx` 1061.8, `fy` 1062.2, `cx` 596.8, `cy` 474.5. `mediad` sends it, scaled to the stream, as `media.video`'s `intrinsics`, unless the robot carries a solve of its own | an upright alpha frame is as wide as the rows it was sent with, across `fy`, so its lens is 37.4°. Its principal point is not the frame's centre: turned upright it sits about 115 pixels left of it in a frame 720 wide, so every bearing quackd's detectors take from the centre, over WebRTC and from a tall `/frame` alike, is about 6° to the left of the bearing from the camera's axis, and they do not correct for it |
| `an IMX219 sees 62 degrees across its frame's long side` | the alpha's head camera behind its M12 lens: its field of view across a frame in the 1920 by 1080 mode `mediad` pins, the same at every size it streams at, since each is the sensor's whole field scaled. The beta's GC2093 has none: "nobody has measured this lens yet" | what the lens of a tall `/frame` is worked out from (the UNVERIFIED row below) |
| `rendezvous control lane` | what upstream sends a server-side agent over: JSON-RPC over HTTP/SSE through upstream's rendezvous service, which `mediad`'s relay bridges to the robot's own sockets. Upstream names two limits on it. "No pixels", since video "would be base64 inside JSON", so frames come from WebRTC or `media.stream`. And "What it is not is a teleop lane": "The rendezvous allows 1200 requests per 60 s per peer", and going over it takes the whole peer off the service, the robot's own lease included | not used. A walk under quackd re-sends its twist against robotd's deadman until it ends, a joystick's rate rather than a behaviour's, so quackd does not drive over it, and a robot elsewhere is reached over ssh with `microduck:jsonrpc` |
| `media.detections` | `mediad`'s duck detector, `{width, height, took_ms, boxes[{x0,y0,x1,y1,score}]}`, pushed on each WebRTC peer's control datachannel and nowhere else. It finds ducks, not balls. Off by default, and two looks a second when on, which upstream calls "a thermal number, not a taste" | the WebRTC camera keeps the newest one. Nothing steers on it |

### UNVERIFIED (designed, assumed, or missing upstream) — and what we do

| Thing | Status upstream | What quackd does |
|---|---|---|
| `stand_up asks robotd's policy to drive (robot.enable), which upstream does not promise gets a fallen duck up` | there is no stand-up call for a duck on the floor. `robot.enable` is policy execution, which "can bring a limp robot up as a side effect of being asked to drive", and `robot.init`, upstream's stand-up for a duck on its feet, moves every joint. robotd's own stand-up after a fall is the end of `limp_fall`, which is off by default at this pin and on again from `daemon-v0.16.2`, where robotd hands a duck it caught falling back to velstand once it has posed it, and upstream says the `v8` velstand gets up from a fall. Whether velstand gets a duck off the floor has not been seen under quackd on either release | `stand_up` reads the duck before it sends anything. It refuses a duck somebody holds and one nothing reports on, says so of one already standing, and points one on its seat at `stand`. It refuses as well while a walk is held back for a verb still running, and on a robot that does not report `safety.picked_up` at all, since nothing there can say the duck is not in somebody's hands. Only a fallen duck, or one whose posture is `unknown` for another reason, is sent `robot.enable {on: true}`, which only sets the flag again on a robot whose policy is enabled already, as it is when the duck fell while the policy drove it. Then the duck is read every quarter second, with nothing more sent: it is called upright as soon as posture reads `standing`, and the wait ends at once on a duck somebody picks up or nothing reports on. It used to be read once, three seconds after the request, and a duck still down then was told it may need a hand even while the policy was still getting it up. Only a duck that has not stood ten seconds after the request is judged: one that robotd still has in its limp-fall sequence, as its policy label says, is told so and to run `stand_up` again in a moment, where one that read as fallen was told it may need a hand and one that did not that its posture was unknown, and one still down is told it may need a hand. The sequence is over within about 2.1 s by default, so that is a fall robotd caught late in the wait. Every state lists this in `extras.assumptions`, which the pilot is told not to take for the robot's own work |
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
`update.*`. The same principle holds on every adapter: quackd never sends `disable_torque` to an
arm of its own accord, and a base over rosbridge gets a zero Twist, not silence. A LeRobot arm is
let go of only at its rest pose, or as near it as its calibration lets the servos go, where
upstream's own `disconnect()` does it, or when a person holding it asks
([adapters/lerobot/README.md](../lerobot/README.md#the-torque-rule)). On an Open Duck the guarantee
is stronger than a promise: the bridge protocol has no word that reaches torque, so going limp is
unreachable rather than merely forbidden.

Nor does quackd send `padd` anything, and neither a gamepad nor quackd ranks above the other:
robotd walks on the newest twist from either (`padd drives while a pad is connected`, above).

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
| The lens | assumed from the first frame's shape | the focal length of `mediad`'s own calibration |
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
which is a beta's, gets no lens: pass `--fov-deg` there. Through a lens no wider than 45°,
`search_scan` turns in steps smaller than its usual 45°, so that each view overlaps the next.

**WebRTC** is the stream. Signalling is gst-plugins-rs `net/webrtc`, read from
`mediad/webclient/index.html` at the pin: the producer offers and quackd answers.

On an alpha the WebRTC track is sideways: the camera is mounted a quarter turn off upright and
nothing on the robot turns the pixels. `mediad` says by how much, as `media.video`'s `rotate`, and
quackd turns every frame upright by it, the way upstream's own code does, and hands out no frame
until `mediad` has said. A `mediad` that never says leaves the run with no camera, and the reason
in `camera_health()`, rather than one that steers on a picture on its side. The same answer
carries the lens, and across an upright alpha frame it is 37.4° by the family's calibration. It
goes in the manifest as `camera_fov_deg`, so a run and an MCP session measure bearings through it
with no `--fov-deg`, and `camera_health()` reports the turn, the lens and the intrinsics. The lens
is the calibration's focal length alone: the principal point is taken for the frame's centre,
which on an alpha leaves every bearing about 6° to the left (the calibration's row above).

`mediad` opens a `control` datachannel at every peer whether it wants one or not. quackd asks it
once what the video is, reads `media.video` and `media.detections` off it, and writes nothing
else: motion goes over `robotd`'s socket, where the allowlist, the confirm gates and the deadman
feed already are, and two ways to move one robot would mean two places to be sure about.

Neither route has been run against a Microduck.

## `watch`: a head that keeps something in view

`watch(target="the apple", duration_s=15)` keeps something the pilot describes in view with the
head alone, for 2 to 30 seconds, the way OpenAI's launch video for its Decisions API has a
Microduck do it. It is quackd's own reflex, not upstream's. Each look takes a frame, asks a
decision LLM that can see two closed questions about it, where across the picture the thing is and
how high, and turns the head by arithmetic of its own on the answer. The decision LLM says where
the thing is and never how far to turn, so what it says moves the head one clamped step at most,
and the next frame corrects a wrong one
([ADR-0054](../../adr/0054-a-reflex-may-look-through-a-decision-llm.md)).

It is there only on a run that can look through a decision LLM: `--decision-llm openai
--decision-frames`, with `OPENAI_API_KEY` set, which sends OpenAI each frame `watch` looks at.
With `--decision-mode off` beside them the run has `watch` and no stepper. Without the flags it is
withdrawn when the duck connects, and a task that requires it, as the bundled `microduck-watch`
does, is refused before anything connects:

```
$ quackd run microduck-watch --llm fake --no-memory
✗ error: microduck-watch requires watch, and watch looks through a decision LLM that can see, and this run has none: run it with --decision-llm openai --decision-frames, and OPENAI_API_KEY set
```

It needs a camera that worked at connect, as `observe` does, whose frames are upright, since it
turns the head by where a thing is across the picture, and known to be taken after the head settled,
since a frame from before would steer a look behind. mediad's `/frame` is both, quackd stamping each
frame with when it asked for it, and both simulators draw each frame when it is asked for. mediad's
WebRTC track is turned upright but hands out frames up to 2 s old with no time on them, so on it, as
on any other `--camera-url`, `watch` is withdrawn when the duck connects, and the note says to use
`--camera-url http://127.0.0.1:8080/frame`. The head is moved only on a duck that reads as standing
or sitting, the allowlist quackd's own gaze hold keeps: a seated duck may watch, as it may `gaze`,
and one held, partway through a skill or on a label quackd has not read may not.

| | |
|---|---|
| What it asks | `where`, one of `far_left`, `left`, `middle`, `right`, `far_right` and `out_of_view`, the fifths of the picture's width, and `height`, one of `top`, `middle`, `bottom` and `out_of_view`, its thirds. The target goes in the state as a description somebody wrote, and never in the instructions, which are fixed and ask for the thing nearest the middle where more than one fits. `microduck:sim2d` is asked no height, since its world is flat |
| How it reads the answer | by the distribution, and never by OpenAI's `confidence`, which OpenAI do not define and which the record keeps: 0.5 or more on `out_of_view` is not seen, 0.6 or more on the fifths spread no wider than a quarter of the picture is seen at their mean weighted by probability, and anything else holds the head still |
| How far it turns | 0.6 of the angle the answer puts the thing at through the lens, nothing within 0.08 of the picture's middle, and at most 15° of yaw and 8° of pitch a look. The claim at the start is no look: a head something else turned past the envelope, as a `gaze` to 90° does, is brought back inside it in one move |
| Where the head may go | 45° either way, 14° up and 11.5° down: as far as `gaze`'s own `left`, `right` and `up` turn it, half the 90° its `bearing_deg` may ask for, and as far down as robotd's own idle glances tilt it, until a duck is measured |
| The lens | `--fov-deg`, else the camera's own, which on an alpha's `/frame` is the 37.3° assumed from the picture's shape and named as assumed in the record and the result, else an assumed 40°, named too. A lens more than 1.5 times wider than the one the camera reports is warned about once, before the run starts |
| How often | one request out at a time, a look at most every 0.5 s on the robot's clock and a request at most every 0.5 s on the wall's, so a simulator stepped faster than real time sends no more either, 60 looks a call, and each answer bounded at 1.5 s |

robotd glances around on its own once nobody has steered the head for a while (`robotd's idle
look-around`, above), so `watch` claims the head before its first look and waits out the 1.5 s
robotd takes to let go of a glance and a 0.4 s settle, 1.9 s in all, sends the head its target again
at least every second while it waits for a frame or an answer, reading the duck before each, and
claims it again, with the same wait, after any gap of more than 2 s since the duck last accepted a
look. A frame is used only when the fetch that took it was asked for after the head settled, and
never twice. On `/frame` at 2 frames a second that is the newest fetch asked for after the settle,
and a look waits up to a period and two and a half fetches for one before it counts the camera as
stale. How many looks a second that comes to on a duck is unmeasured.

Every way a call ends is named as its `ended`, counted in the summary, on the row of the look that
ended it where a look did, and in the result the pilot reads, but for `abort`, `timeout` and
`cancel`, whose result is the executor's own. An exit found between two looks, a time up or a duck
picked up while the head was held, lands on no row. None of them sends the head anywhere on its way
out, and on a duck quackd's gaze hold keeps the head where it was last sent, but for `stopped`,
`abort`, `timeout` and `cancel`: the stop that ended the call, or that the executor sends after it,
ends that hold, after which robotd's own idle glances may turn the head.

| `ended` | When |
|---|---|
| `duration` | its time is up on the robot's clock, or 5 s later on the wall's. The call succeeds when it saw the thing at least once, and says which look last saw it and where |
| `lost` | 4 looks in a row did not see it, out of view or unsure. The pilot is pointed at `search_scan` or `gaze` |
| `edge` | 3 looks in a row wanted the head past its reach, or robotd answered a turn `clamped`. The pilot is told to turn the body, then watch again |
| `errors` | 3 looks in a row had no answer to use: a request that timed out or failed, or an answer nobody could read |
| `refused` | OpenAI declined to say where the thing is. The call is not asked again |
| `camera` | 2 looks in a row had no fresh frame, and the result names the camera's own error |
| `fallen`, `blind`, `held` | the duck fell, nothing reports whether it is upright, or it reads as neither standing nor sitting: somebody holds it, or robotd's own answer to a fall, a skill, a rise, a scoop or a label quackd has not read drives it. Read before every command the head is sent: the claim, every move and every hold |
| `look_refused` | the duck refused a look |
| `stopped` | a `stop` was asked for from beside the call |
| `budget` | the run's own minutes ran out |
| `error` | something raised that is no exit of the call's own, a link to the robot that dropped among them: the executor's result names it, and the row of the look it ended has its type |
| `abort`, `timeout`, `cancel` | the kill switch or a failed heartbeat, the verb's own 45 s, or a caller that went away. The executor ends the call, sends a stop of its own, and answers with its own message |

The record keeps what was sent. Each frame is written beside the transcript as
`sent/watch-NNNN.jpg`, and a `watch_frame` line naming it, its size and its sha256 is written
through to the transcript, both before the request that carries it is handed to the client, so a
process killed with a request out has still recorded the frame. A disk that refuses the copy leaves
no partial file and records `kept: false`. Each look leaves a `watch_tick` row with the
distribution, OpenAI's `confidence`, the gate, the move and what robotd answered the look with, its
time, its tokens and its cost, written whatever ended the look. `run_start.watch` says what the head
was held to and through which lens, and the summary's `decision` block gains `watch`, whose bill is
in that block's totals ([architecture](../../concepts/architecture.md#transcript-format)).

Nothing of this has run on a duck or against OpenAI. In the suite the head closes on the ball on
both simulators through quackd's own client for OpenAI's Decisions API, on a fake of its SDK that
finds the ball in each JPEG it is handed. How long a look takes over a tunnel, what it costs, the
head's real reach and settle, and whether robotd takes a look while somebody holds the duck are
owed to the bench.

## The duck's physics upstreams

`microduck:mujoco` runs the robot Pollen trains, on the pair of policies Pollen trained before
`velstand.onnx`. Two upstreams, both pinned, both fetched at run time into `~/.quackd/cache` and
checked against a recorded sha256, and neither shipped: the 3D model files are CC BY-NC-SA
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
there, so the model's commit stays. daemon-v0.16.2 and later seed a robot with `v8`, which
changes no policy but `velstand.onnx`, so the simulator's pair is the same bytes there too.

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
