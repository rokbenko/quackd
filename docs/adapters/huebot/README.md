# Huebot

A 25 cm biped from Leif Rydenfalk, in Shenzhen ([ce-net.com/huebot](https://ce-net.com/huebot)),
installed with `quackd[huebot]`. Its maker describes it as a Microduck clone: fifteen servos, an
Arduino UNO Q for a brain, and behaviour code of its own. Huebot's own API is closed, so its
maker runs a shim on the robot that answers the JSON-RPC the Microduck's `robotd` speaks, and
`huebot:jsonrpc` speaks that to it. `huebot:mock` is the core's scripted body.

**quackd has never run on a Huebot, and nobody has seen the shim.** Everything below is what
quackd sends and reads, and what the shim is asked to answer for that to work. Every backend's
row is in [adapters/status.md](../status.md), and the order to try a real one in is
[adapters/huebot/hardware-checklist.md](hardware-checklist.md).

```bash
uv pip install 'quackd[huebot]'      # quackd's own adapter package, and no robot library

# offline
quackd run hello-world --robot huebot:mock --llm fake

# the robot, once its shim answers, through an ssh forward (below)
quackd doctor --robot huebot:jsonrpc --address tcp://127.0.0.1:9870
```

## Backends

| `--robot` | What it is | Status |
|---|---|---|
| `huebot:mock` | the core's scripted body, with Huebot's manifest and verbs | ✅ every verb runs offline in the test suite |
| `huebot:jsonrpc` | the robot, over the shim its maker runs on it | 🧪 robotd's method names, VERIFIED at the Microduck's pin, exercised against a fake shim over loopback, **never run against Huebot's shim** |

There is no simulator: the Microduck's `sim2d` and `mujoco` backends model the Microduck's
body and gait, which are not Huebot's.

The adapter is the Microduck's, copied and renamed rather than imported, so the two can diverge
as the robots do. It keeps the protocol, the version gate, the check that the robot took each
twist, the posture allowlist, the hold on a walk once the robot stops standing, the heartbeat
and the reliance on the robot's own deadman. It leaves out what the shim does not answer yet,
`robot.look`, so there is no `gaze` and no `watch`, and the Microduck's camera, which comes from
a daemon Huebot does not run.

## What Huebot's shim must answer

This is what quackd needs from the shim, call by call, in the shapes robotd uses at
`daemon-v0.16.1`, where quackd read them. Its maker named six calls: `hello`, `robot.move`,
`robot.stop`, `robot.head`, `robot.state` and `robot.health`. quackd also needs four more,
`robot.subscribe`, `robot.do`, `robot.sound` and `robot.enable`, and does not send
`robot.head` yet. A call the shim does not answer fails the verb that sent it, by name, and a
missing `robot.subscribe` fails the connect.

### How quackd reaches it

The protocol has no authentication: whoever can open the socket can drive the robot. So the shim
listens where only the robot itself can reach it, on a unix socket or on a port of the robot's
own loopback, `127.0.0.1`, and never on an address of the local network. quackd reaches it
through an ssh forward, which carries the account and the key that ssh already checks:

```bash
ssh -L 9870:/path/to/the/shim.sock <user>@<huebot>     # a unix socket on the robot
ssh -L 9870:127.0.0.1:<port> <user>@<huebot>          # or a port on the robot's own loopback
quackd doctor --robot huebot:jsonrpc --address tcp://127.0.0.1:9870
```

quackd has no default address for the shim. Without `--address` it refuses before it opens
anything, and says what to do:

```text
huebot:jsonrpc needs --address: quackd knows no default for Huebot's shim, which is wherever its
maker put it. Forward the shim's socket over ssh, `ssh -L 9870:<socket> <huebot>`, then pass
--address tcp://127.0.0.1:9870
```

### The wire

- JSON-RPC 2.0, one object per line (NDJSON), each line ending in a newline. A line may be up to
  1 MiB.
- A request carries an integer `id`, and its answer echoes it with a `result`, or with an
  `error` of `{code, message}`. A notification carries no `id`, and nothing answers it.
- A method the shim does not have is `METHOD_NOT_FOUND`, -32601, and a params member it does
  not take is `INVALID_PARAMS`, -32602, with the member named in the message.
- Read each connection's lines in order. Apply a `robot.move` as it is read, before the next
  line, answer a request before reading the line after it, and write the `robot.state` frames
  on the same connection. quackd's check that a twist was taken counts on that order: an answer
  to anything it sent after a twist says the shim has read the twist.

### `hello`

```json
{"jsonrpc": "2.0", "id": 1, "method": "hello", "params": {"api_version": 41}}
{"jsonrpc": "2.0", "id": 1, "result": {"api_version": 41, "daemon_version": "0.1.0", "revision": null}}
```

`api_version` has to be an integer. Answer 41, the version quackd read. A shim on any version
from 23 up is served, with a warning in the log, in `quackd doctor` and in every state, and one
below 23, or one whose answer names no integer version, is refused. `daemon_version` and
`revision` are the shim's own build and commit, or `null`, and `quackd doctor` prints them.

### `robot.subscribe`, then `robot.state`

**Required.** It is the first `robot.*` call quackd makes, and a shim that answers it
`METHOD_NOT_FOUND` fails the connect as not robotd. Until it is answered no state arrives, and
without state nothing walks.

```json
{"jsonrpc": "2.0", "id": 2, "method": "robot.subscribe", "params": {"hz": 10}}
{"jsonrpc": "2.0", "id": 2, "result": {"accepted": true, "skills": ["sit_toggle", "kick_left", "kick_right", "ground_pick"]}}
```

`skills` lists every name `robot.do` answers to, and quackd offers a verb only for the skills
listed there:

| Verb | Sends | Offered when `skills` lists |
|---|---|---|
| `sit`, `stand` | `robot.do {skill: "sit_toggle"}` | `sit_toggle` |
| `kick` | `robot.do {skill: "kick_left"}` or `"kick_right"` | both `kick_left` and `kick_right` |
| `grab` | `robot.do {skill: "ground_pick"}` | `ground_pick` |

robotd keeps `sit_toggle` and `ground_pick` out of this list, since they are not configurable
skills there. The shim should list them if Huebot has them, or quackd offers none of the four
verbs that send them. An answer with no `skills` lists none. quackd reads nothing else in the
answer, and shows the whole of it to the pilot in `extras.subscribed`.

From then on the shim sends a `robot.state` notification at the rate asked for, 10 a second, on
that connection, until it closes. A frame older than 1 s is not believed, and the robot reads as
`unknown` until the next one arrives. Each frame carries at least this:

```json
{
  "jsonrpc": "2.0",
  "method": "robot.state",
  "params": {
    "policy": "walk",
    "safety": {"fallen": false, "picked_up": false, "limp": false},
    "move": {"requested": [0.1, 0.0, 0.0]},
    "head": [0.0, 0.0, 0.0, 0.0],
    "joints": [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
  }
}
```

- `safety.fallen`, a boolean, in every frame. A frame with no `safety` block, or one without
  `fallen`, reads as `unknown`, and nothing walks: `fallen: false` from a frame that does not
  say is not a fact about the robot.
- `safety.picked_up`, a boolean, true while somebody holds the robot, in every frame. quackd
  reads it before `fallen`, since a robot held at an angle reads as fallen, and sends nothing
  that moves the robot while it is true. A shim that leaves it out cannot tell quackd that
  somebody is holding Huebot: walks then go out to a robot in somebody's hands, `stand_up`
  refuses, and `quackd doctor` warns.
- `safety.limp`, a boolean, true while the gains are dropped so the robot yields. It reads as
  `unknown`.
- `policy`, a string. `walk` or `stand` while the robot is on its feet and takes a twist, and
  `sit` while it is seated. Every other label reads as `unknown`, and nothing walks: a skill's
  own name while the skill runs, `held`, `homing`, or anything quackd has not read. Only these
  three are read as a posture.
- `move.requested`, `[vx, vy, vyaw]`, exactly as the newest `robot.move` asked for it, unrounded.
  `robot.stop` zeroes it, and the deadman does not. `robot.move` is a notification, so this is
  the only place quackd can see that a twist was taken. A twist the state has not shown three
  frames after it went out, 0.3 s at 10 a second, is judged on the frames that follow the shim's
  answer to a call quackd made after it, and once those do not show it either, quackd holds the
  walk back and the verb fails.
- `head`, four numbers in radians, `[neck_pitch, head_pitch, head_yaw, head_roll]`: the head as
  it is commanded. This release does not read it, and the gaze a later one adds will.
- `joints`, fifteen numbers in radians, as measured, in the Microduck's order: `left_hip_yaw`,
  `left_hip_roll`, `left_hip_pitch`, `left_knee`, `left_ankle`, `neck_pitch`, `head_pitch`,
  `head_yaw`, `head_roll`, `mouth`, `right_hip_yaw`, `right_hip_roll`, `right_hip_pitch`,
  `right_knee`, `right_ankle`. So `head_yaw` is at index 7, and it is positive when the head
  looks left. quackd reads that one joint, as `extras.head_yaw_deg`, and steers on none.

robotd's frame carries more than this, and quackd passes `odom` and `loop` on to the pilot when
they are there. Nothing else in a frame is read.

### `robot.move`, and the deadman

```json
{"jsonrpc": "2.0", "method": "robot.move", "params": {"vx": 0.1, "vy": 0.0, "vyaw": 0.0}}
```

A notification: metres a second forward and to the left, and radians a second of turn, positive
to the left, in the trunk's frame. quackd sends one every 100 ms for as long as a verb walks, and
never more than 0.15 m/s forward, 0.1 m/s sideways or 0.75 rad/s of turn.

**The deadman is the shim's.** Once no `robot.move` has arrived for 500 ms, robotd's default,
the robot stops. quackd relies on that and nothing else when the link goes: a tunnel pulled out,
a laptop shut, a process killed. When quackd holds a walk back it sends nothing in its place, not
even a zero, and a `robot.stop` it cannot deliver leaves the deadman to stop the robot. The first
step of the [hardware checklist](hardware-checklist.md) tests it, before anything walks.

### `robot.stop` and `robot.health`

```json
{"jsonrpc": "2.0", "id": 3, "method": "robot.stop"}
{"jsonrpc": "2.0", "id": 3, "result": {"accepted": true}}
{"jsonrpc": "2.0", "id": 4, "method": "robot.health"}
{"jsonrpc": "2.0", "id": 4, "result": {"healthy": true, "reason": null, "battery": {"percent": 80}}}
```

- `robot.stop` takes no params. It zeroes the velocity at once, and is not a limp. It zeroes
  `move.requested` too, and is answered before the shim writes another state frame, so that the
  frames before the answer are the ones built before the stop. quackd ends every walk and every
  run with one, and sends one when the pilot asks or the heartbeat fails.
- `robot.health` takes no params, and quackd asks it twice a second as its heartbeat. `healthy:
  false` ends the run, with `reason` as the reason. `battery.percent` is optional, and a task can
  end a run on it. quackd also asks it as each walk starts, since its answer says the shim has
  read the walk's first twist.

### `robot.do`, `robot.sound` and `robot.enable`

```json
{"jsonrpc": "2.0", "id": 5, "method": "robot.do", "params": {"skill": "kick_left"}}
{"jsonrpc": "2.0", "id": 5, "result": {"accepted": true}}
{"jsonrpc": "2.0", "id": 6, "method": "robot.sound", "params": {"tag": "greet"}}
{"jsonrpc": "2.0", "id": 6, "result": {"accepted": true}}
{"jsonrpc": "2.0", "id": 7, "method": "robot.enable", "params": {"on": true}}
{"jsonrpc": "2.0", "id": 7, "result": {"accepted": true}}
```

- `robot.do` runs one of the skills the subscribe answer listed, and is answered when the skill
  is accepted or refused, not when it ends. quackd sends no `robot.do` for a name that list did
  not carry. While a skill runs, `policy` names it, or says anything but `walk`, `stand` and
  `sit`, since a robot reading `walk` mid-kick would be sent walks.
- `robot.sound` plays one of seven tags, `alarm`, `greet`, `inquire`, `peck`, `chirp`, `coo` or
  `wheee`. `quack` and `say` map their text to a tag, and the text never goes on the wire.
- `robot.enable` asks the robot to drive again, and `stand_up` is the one verb that sends it, to a
  robot that is down and that nobody holds. On robotd it is policy execution, which can bring a
  limp robot up as a side effect of being asked to drive, so quackd treats it as motion. After
  it, quackd reads the state every 0.25 s, sending nothing more, and calls the robot upright the
  first time `policy` reads `walk` or `stand`, with `fallen` false and nobody holding it. The wait
  ends at once if somebody picks the robot up or nothing reports on it. After 10 s without any of
  that, it says the robot may need a hand. A refusal is a `stand_up` that failed.

### A boolean `accepted`

The answers to `robot.do`, `robot.sound` and `robot.enable` each carry a boolean `accepted`,
and a `reason` beside it when it is false. An answer without one is read as a refusal, never as
a yes: a skill the robot refused must not be reported as done. robotd writes `accepted` in its
answers to `robot.stop` and `robot.subscribe` too, and the shim may as well.

### Not needed yet

`robot.look`, which points the head at a point and answers with the head joints it chose, and
`robot.head`, which a held gaze sends again. quackd sends neither to Huebot. Its maker says
`robot.look` is not on Huebot's side yet, so a look is refused inside quackd before anything is
written, and `gaze` and `watch` are not offered. A later release adds them once the maker says
`robot.look` answers on Huebot.

quackd never sends `robot.init`, which moves every joint to its home pose, or `robot.relax`,
which drops the torque.

### The camera

The shim's socket carries no picture. quackd takes one from `--camera-url`: any HTTP address
that answers each request with one still image, a JPEG or a PNG, pulled five times a second. A
frame older than 2 s is not handed out. Without a frame at connect, `observe`, `go_to`,
`search_scan` and `approach_and` are not offered, and the manifest lists no camera. How a frame
gets off Huebot's IMX219 is the maker's to say, and the same forward reaches it:

```bash
ssh -L 8080:127.0.0.1:<port> <user>@<huebot>
quackd doctor --robot huebot:jsonrpc --address tcp://127.0.0.1:9870 \
  --camera-url http://127.0.0.1:8080/<path>
```

Nothing publishes the lens's field of view, so the detector measures through a guess and says
so, and `--fov-deg` gives it the real one.

## Upstream API

The protocol was read on 2026-10-08, in the Microduck's own source, pinned at
[`d818af5`](https://github.com/pollen-robotics/microduck/tree/d818af5d91fce0e3f5f8c22d47d47caf7b3f17a1)
(`daemon-v0.16.1`), **API v41**, the pin the [Microduck adapter](../microduck/README.md) reads it
at. Every name quackd spells lives in
[`adapters/huebot/src/quackd_huebot/upstream_api.py`](../../../adapters/huebot/src/quackd_huebot/upstream_api.py),
copied from the Microduck's rather than imported, and a test holds the two to agree while they
read one pin.

Huebot's side is the maker's word: his email to Rok Benko of 2026-10-15, which is not public,
and his page, [ce-net.com/huebot](https://ce-net.com/huebot), read on 2026-10-20. His public
code, [walking-robot-kit](https://github.com/Leif-Rydenfalk/walking-robot-kit), Apache-2.0 and
CC BY-SA 4.0, is the servo bus alone, a Rust daemon serving an HTTP control page on port 8938,
with no JSON-RPC, and quackd does not speak it. Nothing here has been run against Huebot's shim.

### VERIFIED (robotd's protocol, read at the pin)

| Thing | Value | Used for |
|---|---|---|
| API version | `41` | `hello`: the version quackd read, and the one it sends. A shim on any other version from the floor up is served, with a warning in the log, in `doctor` and in `extras.shim` |
| API floor | `23` | the oldest protocol quackd has read end to end. A `hello` answered below it, or with no integer version, is refused at connect |
| `No daemon refuses a call because this number differs` | upstream's rule for two peers on different versions: what refuses instead is the call, `METHOD_NOT_FOUND` naming a method a release does not have, `INVALID_PARAMS` naming a member it does not know | why quackd serves a shim on a version it has not read. A verb whose call is refused either way fails naming the call and both versions |
| Framing | `NDJSON: one JSON-RPC 2.0 object per line` | the wire. No one line ends the link: one quackd cannot read is skipped, and only the shim closing the socket, or the socket failing, ends it |
| `robotd reads a connection's lines in order` | a notification is applied as it is read, a request is answered before the next line is read, and the frames and the answers go out on the same connection, a line at a time | when the shim can have read a twist: only the frames read after it answered a call quackd made after the twist count against it, and none read after it answered a stop |
| `hello` | params `{api_version}` → `{api_version, daemon_version, revision}`, the last two possibly `null` | connect |
| `robot.move` | **notification** `{vx, vy, vyaw}` m/s, rad/s, trunk frame, x forward, y left, +vyaw left | `move`, `go_to`, `search_scan`, re-sent every 100 ms. Never sent while posture is not `standing`, nor once the state has not shown one it was sent. Once the robot stops standing while a verb walks, or the shim stops showing its twists, `robot.move` is held back for every verb that has walked until each ends, and `extras.move_hold` says why |
| `robot.state.move.requested` | the twist the robot was last asked for, as it was asked, which `robot.stop` zeroes and the deadman does not | the only place quackd can see a `robot.move` was taken. Three frames after the shim answered a later call, 0.3 s at the 10 Hz quackd subscribes at, without it, and the walk is held back. `extras.move_echo` keeps the last verdict, and `search_scan` counts the turns it says it made by the ones the state showed |
| `robot.stop` | request; zero velocity, *not* limp | `stop`, every walk's end and every run's |
| `robot.do` | request `{skill}` → `{accepted, reason?}`, answered on accept or refusal rather than on completion | `kick`, `grab`, `sit` and `stand`, only for skills the subscribe answer listed |
| `ground_pick \| kick_left \| kick_right \| sit_toggle \| roulade` | the names a stock Microduck answers to. A robot's skills are config, and an unknown one is refused | the names quackd's verbs send |
| `robot.subscribe -> SubscribeResult.skills` | the skills a robot has, and the names `robot.do` answers to | which of those four verbs a connected Huebot is offered, and which `robot.do` goes out |
| `robot.sound` | request `{tag, hold?}`; tags `alarm \| greet \| inquire \| peck \| chirp \| coo \| wheee`, no TTS | `quack` and `say`, text mapped to a tag |
| `robot.enable` | request `{on, toggle?}` → `{accepted, reason?}`. Policy execution, **not a flag**: it "can bring a limp robot up as a side effect of being asked to drive" | `stand_up` |
| `robot.init` / `robot.relax` | power the joints and ramp to the home pose, moving every joint / torque **off**, and the robot collapses | **never sent by quackd** |
| `robot.subscribe` → `robot.state` | request `{hz?}`, then notifications `{t, move{requested,applied,limited_by}, head[4], policy, safety{fallen,limp,gravity,gain?,picked_up}, loop{hz,missed}, joints, targets, ...}` | state, at 10 Hz |
| `robot.state is not pushed until robot.subscribe` | a connection that never subscribes learns nothing about the robot | why the transport subscribes inside `connect()` |
| `safety.picked_up` | somebody is holding the robot; absent from a robotd older than its pick-up detector, "which reads as never picked up" | read before `safety.fallen`, and nothing that moves the robot goes out while it is true. Its absence is recorded as `extras.pickup_detection`, and `stand_up` refuses then |
| `walk, stand, ground_pick, sit, rise, <skill>, <skill>:unwind, skill` | the labels of a tick a network drove | `walk` and `stand` read as standing and `sit`, matched exactly, as sitting. Every other label is `unknown`, a skill's own name included, and refuses every verb that walks |
| `held, homing, rest, limp_fall, limp_pose` | the labels of a tick no network drove, `limp_fall` and `limp_pose` robotd's own answer to a fall | `unknown`. `stand_up` says a robot still reporting either at the end of its wait is answering a fall itself |
| `robot.health` | request → `{healthy, degraded?, reason?, battery{volts,percent}?, ...}` | the heartbeat, twice a second, and the battery |
| `robotd intent deadman` | "if intents stop arriving, the velocity goes to zero. Stop is not limp", after 500 ms by default | why `move` re-sends every 100 ms, and what stops the robot when quackd's twists stop arriving (`Huebot stops when robot.move notifications stop arriving`, below) |
| `robot.look` | request `{x, y, z, neck_pitch}` → `{head, clamped}` | not sent to Huebot (below) |
| `robot.head` | notification `{neck_pitch, head_pitch, head_yaw, head_roll}`, in joint space | not sent to Huebot |

### UNVERIFIED (Huebot's side), and what we do

| Thing | The maker's word | What quackd does |
|---|---|---|
| `Huebot's shim answers the calls quackd makes as robotd does` | his email of 2026-10-15: the shim will answer the JSON-RPC quackd speaks to robotd, and he named `hello`, `robot.move`, `robot.stop`, `robot.head`, `robot.state` and `robot.health` | speaks robotd's protocol to it, and asks for four calls he did not name, `robot.subscribe`, `robot.do`, `robot.sound` and `robot.enable` ([above](#what-huebots-shim-must-answer)) |
| `robot.look, which Huebot's shim does not answer yet` | the same email: `robot.look` is not on Huebot's side yet, and the head turns with four servos | refuses a look itself, before anything is written, offers no `gaze` and no `watch`, and waits for no glance to fade before `go_to` and `search_scan` steer |
| `robot.state.joints in the Microduck's order, head_yaw at index 7, positive to the left` | his page names fifteen servos and neither their names nor their order | asks the shim for robotd's order, and reads index 7 as `extras.head_yaw_deg`, which nothing steers on |
| `Huebot stops when robot.move notifications stop arriving` | assumed: a shim that answers robotd's calls need not stop as robotd does | relies on it whenever the link goes, and step 1 of the checklist tests it before anything walks |
| `the skills Huebot's robot.do answers to are the ones its robot.subscribe lists` | Huebot runs behaviour code of its own, and which skills the shim lists is his to say | offers `sit`, `stand`, `kick` and `grab` only for the skills the shim lists, and refuses any other `robot.do` without writing it |
| `stand_up asks Huebot's shim to drive (robot.enable), which nobody has said gets a fallen Huebot up` | Huebot stands and steps, and the walk's balance is still being tuned. Getting up from a fall is not published | sends `robot.enable {on: true}` only to a robot that is down and nobody holds, waits up to 10 s for it to read standing, and every state lists this in `extras.assumptions`, which the pilot is told not to take for the robot's own work |

## What this robot is, as numbers

Its datasheet, which the pilot is shown and told to judge a task against before anything moves
([reference/manifest-spec.md](../../reference/manifest-spec.md)):

| | |
|---|---|
| Height | 0.25 m (official: ce-net.com/huebot, read 2026-10-20) |
| Actuated joints | 15 (official: ce-net.com/huebot, read 2026-10-20; Feetech HD-1910 bus servos on one serial bus) |
| Not published | mass, endurance, the power source, the terrain it is rated for |

And what it cannot do whatever the task says, in the words the pilot is shown:

- carry or push anything: it has no arms
- climb or descend a step

The pilot is also told that the speed caps are quackd's own clamp and not a measured maximum,
that its maker said on 2026-10-15 that it stands and steps and that the walk's balance is still
being tuned, that it has a speaker and a 13 by 8 LED face, which quackd does not drive, and that
its 8 by 8 time-of-flight sensor is not read. The maker's page lists the battery as coming. A
figure nobody published is listed as not published, and the pilot is told to answer `uncertain`
and name it, rather than guess, where a task turns on it. A `.duck` file can correct any of it
for the build in front of you ([reference/duck-spec.md](../../reference/duck-spec.md)).

The maker's page also lists an Arduino UNO Q with a Huebot hat board, an IMX219 camera behind an
M12 lens, a VL53L8CX time-of-flight sensor, an electret microphone, and a kit at $449 that ships
in December.
