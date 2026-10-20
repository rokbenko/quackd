"""The only file in quackd allowed to spell a method name Huebot's shim answers.

Huebot's own API is closed. Its maker, Leif Rydenfalk, wrote to Rok Benko on 2026-10-15 that he
will run a shim on the robot that answers the JSON-RPC the Microduck's `robotd` speaks, and named
`hello`, `robot.move`, `robot.stop`, `robot.head`, `robot.state` and `robot.health`. So the
protocol here is robotd's, read in the Microduck's own source at the pin the Microduck adapter
reads it at, daemon-v0.16.1 (d818af5), and every ref that says what that protocol is stays
VERIFIED, as a fact about the protocol.

Whether Huebot's shim answers it that way is another matter. Nobody has seen the shim, Huebot's
own API is closed, and the maker's page publishes neither joint names nor a joint order, so every
claim about Huebot's side is an UNVERIFIED ref, sourced to his email of 2026-10-15 and to his page,
ce-net.com/huebot, read on 2026-10-20: that the shim answers the calls quackd makes, that it does
not answer `robot.look` yet, the order of its joints, its deadman, its skills, and what
`robot.enable` does for a Huebot on the floor. `docs/adapters/huebot/README.md` is the human
readable version of this file, and its section on what the shim must answer is what the maker is
asked to build. `tests/test_upstream_api.py` proves that the UNVERIFIED names are reachable from
the `jsonrpc` backend alone.

The protocol refs are copied from `quackd_microduck.upstream_api` rather than imported from it,
because the two robots may diverge: a Microduck re-read moves that file, and this one moves when
Huebot's maker says his shim does. While both read the same pin, the facts they share agree, and
a test holds them to it.

The maker's own code, github.com/Leif-Rydenfalk/walking-robot-kit, is the servo bus alone: a
daemon serving an HTTP control page on port 8938, with no JSON-RPC, which quackd does not speak.

Nothing here has been run against a Huebot.
"""

from __future__ import annotations

from quackd.upstream import Status, UpstreamRef

REPO = "https://github.com/pollen-robotics/microduck"
PIN = "d818af5d91fce0e3f5f8c22d47d47caf7b3f17a1"  # daemon-v0.16.1, 2026-10-07
RELEASE = "daemon-v0.16.1"
"""The robotd release the protocol was read at, which a shim's `hello` may name as its build."""
READ_ON = "2026-10-08"
"""When the protocol was read, in the Microduck's own source."""

MAKER = "https://ce-net.com/huebot"
"""The maker's page, where every UNVERIFIED ref here is sourced."""
MAKER_READ_ON = "2026-10-20"
MAKER_EMAIL_ON = "2026-10-15"
"""When Leif Rydenfalk wrote to Rok Benko about the shim: not published, and the only word there
is on what the shim answers."""


def src(path: str, line: int | None = None) -> str:
    return f"{REPO}/blob/{PIN}/{path}" + (f"#L{line}" if line else "")


_PROTO = "duck-ipc-proto/src/lib.rs"
_ROBOTD_DOC = "docs/design/robotd-design.md"
_ARCH_DOC = "docs/design/architecture.md"

IPC_PROTO = src(_PROTO)


__all__ = ["Status", "UpstreamRef"]  # re-exported: this file's own refs are Huebot's

# ── the protocol, as robotd speaks it at the pin ────────────────────────────────────────

JSONRPC_VERSION = "2.0"
API_VERSION = UpstreamRef(
    "41",
    "VERIFIED",
    src(_PROTO, 454),
    "duck-ipc-proto API_VERSION at the pin: the version quackd read, and the one it sends in "
    "hello. A shim that answers with any version from API_FLOOR up is served, as upstream's own "
    "clients serve one (VERSION_SKEW), and one that answers with any other than this is warned "
    "about: in the log, in doctor and in every state's extras.shim.",
)
API_FLOOR = UpstreamRef(
    "23",
    "VERIFIED",
    src(_PROTO, 165),
    "The oldest duck-ipc-proto quackd has read end to end, the Microduck adapter's previous read, "
    "at bc41fb5 on 2026-09-04, which is the version note this cites. A hello answered with a "
    "version below it is refused at connect, and so is one that names no version, which is not "
    "a peer quackd can check against anything.",
)
VERSION_SKEW = UpstreamRef(
    "No daemon refuses a call because this number differs",
    "VERIFIED",
    src(_PROTO, 57),
    "Upstream's rule for two peers on different versions (lib.rs:51-82). 'A bump promises "
    "nothing in either direction', and no daemon refuses a peer over the number: robotd answers "
    "hello without comparing (robotd/src/main.rs:5397), because 'a call that cannot be served "
    "will name itself' (robotctl/src/main.rs:1571-1572). 'What refuses instead' is the call: a "
    "method a release does not have is METHOD_NOT_FOUND naming it, and a params member it does "
    "not know is INVALID_PARAMS naming the member, since every params type denies unknown "
    "fields. A notification gets neither, because nothing answers one, which is what MOVE_ECHO "
    "is for.",
)
FRAMING = UpstreamRef(
    "NDJSON: one JSON-RPC 2.0 object per line",
    "VERIFIED",
    src(_ARCH_DOC, 190),
    "'Wire format: JSON-RPC 2.0, one object per line (NDJSON).'",
)
LINES_IN_ORDER = UpstreamRef(
    "robotd reads a connection's lines in order",
    "VERIFIED",
    src("robotd/src/main.rs", 4188),
    "One task per connection reads its lines one at a time (robotd/src/main.rs:4182-4349). A "
    "notification such as robot.move is applied as it is read, before the next line is "
    "(main.rs:4309-4316), and a request is answered before the line after it is read "
    "(main.rs:4343-4347). So an answer to any call quackd wrote after a robot.move says robotd "
    "has read and stored that twist, which is when MOVE_ECHO can first show it. State frames "
    "reach the connection from a broadcast the control loop publishes into, decimated to the "
    "rate asked for (main.rs:4254-4266), so one built just before the twist was stored can still "
    "go out after that answer. The same task writes the frames and the answers, a line at a "
    "time, and writes a request's answer straight after it handles the request, so every frame "
    "written before the answer to a robot.stop was built before the stop zeroed the twist. "
    "quackd's check that a twist was taken counts on all of this, so a shim has to read and "
    "answer a connection's lines the same way.",
)

# ── methods quackd sends ────────────────────────────────────────────────────────────────

HELLO = UpstreamRef(
    "hello",
    "VERIFIED",
    src(_PROTO, 593),
    "params {api_version}; result {api_version, daemon_version, revision}. The last two are "
    "always on the wire and may be null: revision is null 'for a build that did not come from "
    "CI'.",
)
ROBOT_MOVE = UpstreamRef(
    "robot.move",
    "VERIFIED",
    src(_PROTO, 647),
    "NOTIFICATION (no id). params {vx, vy, vyaw} in m/s and rad/s, trunk frame: x forward, y "
    "left, +vyaw left. Continuous: robotd's deadman zeroes velocity when these stop arriving.",
)
ROBOT_STOP = UpstreamRef(
    "robot.stop", "VERIFIED", src(_PROTO, 654), "request; zero velocity, NOT limp"
)
ROBOT_DO = UpstreamRef(
    "robot.do",
    "VERIFIED",
    src(_PROTO, 703),
    "request {skill} -> IntentResult {accepted, reason?}. Answered on accept/refuse, not on "
    "completion: 'a refusal names the scripted move already holding the robot'.",
)
ROBOT_ENABLE = UpstreamRef(
    "robot.enable",
    "VERIFIED",
    src(_PROTO, 656),
    "request {on, toggle?} -> {accepted, reason?}; `toggle` is #[serde(default)], so sending "
    "{on} alone is valid. This is POLICY execution, not a flag: upstream says it 'can bring a "
    "limp robot up as a side effect of being asked to drive', so treat it as motion.",
)
ROBOT_INIT = UpstreamRef(
    "robot.init",
    "VERIFIED",
    src(_PROTO, 674),
    "power the joints and ramp to the home pose; MOVES EVERY JOINT. quackd never sends this.",
)
ROBOT_RELAX = UpstreamRef(
    "robot.relax",
    "VERIFIED",
    src(_PROTO, 680),
    "torque off: the robot collapses. quackd never sends this.",
)
ROBOT_SOUND = UpstreamRef(
    "robot.sound", "VERIFIED", src(_PROTO, 722), "request {tag, hold?}; tags only, no TTS"
)
ROBOT_SUBSCRIBE = UpstreamRef(
    "robot.subscribe",
    "VERIFIED",
    src(_PROTO, 841),
    "request {hz?} -> SubscribeResult, then robot.state notifications. State is NOT pushed "
    "until this is sent. The answer also carries what is constant for the process: "
    "{accepted, walk?, stand?, unavailable?, sitstand?, ground_pick?, skills[]}.",
)
ROBOT_STATE = UpstreamRef(
    "robot.state",
    "VERIFIED",
    src(_PROTO, 3860),
    "notification {t, move{requested,applied,limited_by}, head[4], policy, "
    "safety{fallen,limp,gravity,gain?,picked_up}, loop{hz,missed}, joints, targets, "
    "velocities?, currents_ma?, odom, theremin?, chorale?, t_ns, imu?, frames?, skeleton?}. "
    "head is the head robotd commands this tick, in HEAD_PARAMS order, and joints are the "
    "measured joints in JOINT_NAMES order.",
)
ROBOT_HEALTH = UpstreamRef(
    "robot.health",
    "VERIFIED",
    src(_PROTO, 3552),
    "request -> {healthy, degraded?, reason?, battery{volts,percent}?, motors?, cpu_temp_c?, "
    "cpu_throttle?, control_loop?, bus, imu?}. quackd reads healthy, reason and battery.",
)
STATE_NEEDS_SUBSCRIBE = UpstreamRef(
    "robot.state is not pushed until robot.subscribe",
    "VERIFIED",
    src(_ROBOTD_DOC, 752),
    "'robot.subscribe turns a connection into a stream; the loop publishes into a bounded "
    "broadcast and never waits on a subscriber, so a slow client gets a gap rather than "
    "applying backpressure.' A connection that never subscribes learns nothing about the "
    "robot, which is why the transport subscribes during connect().",
)

# ── methods quackd does not send to Huebot yet ──────────────────────────────────────────

ROBOT_LOOK = UpstreamRef(
    "robot.look",
    "VERIFIED",
    src(_PROTO, 652),
    "request {x, y, z, neck_pitch} trunk-frame point -> {head, clamped}. What a gaze sends. "
    "Huebot's shim does not answer it yet (SHIM_LOOK), so quackd sends Huebot none.",
)
ROBOT_HEAD = UpstreamRef(
    "robot.head",
    "VERIFIED",
    src(_PROTO, 649),
    "NOTIFICATION {neck_pitch, head_pitch, head_yaw, head_roll} rad, HEAD_PARAMS, in joint "
    "space, which robotd stores in the one head slot robot.look writes too. What a held gaze "
    "sends again. quackd sends Huebot none, since it sends no robot.look.",
)
#: HeadParams: the four head joints robot.head takes and LookResult.head answers with, in the
#: order robot.state.head carries them (lib.rs:2206-2211).
HEAD_PARAMS = ("neck_pitch", "head_pitch", "head_yaw", "head_roll")
#: The order robot.state.joints is indexed in, robotd's JOINT_NAMES (lib.rs:552-568): the left
#: leg, the neck, head and mouth, then the right leg. A Microduck's, which Huebot's shim is asked
#: to keep (JOINT_ORDER).
JOINT_NAMES = (
    "left_hip_yaw",
    "left_hip_roll",
    "left_hip_pitch",
    "left_knee",
    "left_ankle",
    "neck_pitch",
    "head_pitch",
    "head_yaw",
    "head_roll",
    "mouth",
    "right_hip_yaw",
    "right_hip_roll",
    "right_hip_pitch",
    "right_knee",
    "right_ankle",
)

# ── what quackd reads back ──────────────────────────────────────────────────────────────

MOVE_ECHO = UpstreamRef(
    "robot.state.move.requested",
    "VERIFIED",
    src("robotd/src/main.rs", 3746),
    "The twist robotd was last asked for, as it was asked. robot.move stores [vx, vy, vyaw] "
    "untouched (robotd/src/main.rs:4361) and every state frame reports it as requested, beside "
    "applied, the twist the control loop hands its controller. robotd's own test holds "
    "requested to what was asked: 'what the client asked for must survive to the stream' "
    "(main.rs:7419), so the deadman does not zero it. robot.stop does, and is answered at once "
    "(main.rs:5311-5314, robotd/src/intents.rs:320-323). robot.move is a notification, so "
    "nothing answers one that could not be parsed, and this is the only place quackd can see "
    "whether a twist was taken. Once the state has gone a few frames without showing one it "
    "sent, counted over the walks of a verb up to each stop, quackd sends no robot.move until "
    "the verb that sent it ends.",
)
SAFETY_PICKED_UP = UpstreamRef(
    "safety.picked_up",
    "VERIFIED",
    src(_PROTO, 4104),
    "'Somebody is holding the robot: the pick-up detector ([pickup]) has paused the policy and "
    "the legs hold the pause pose until it is put back down', and while it is true the policy "
    "label reads held. 'Absent from a robotd that predates the detector, which reads as never "
    "picked up.' quackd reads it before safety.fallen, because a robot held at an angle also "
    "reads as fallen, and nothing that moves the robot is sent while it is true. A frame "
    "without the key is not read as nobody holding it: quackd publishes whether frames carry "
    "the key as extras.pickup_detection.",
)
SKILLS = UpstreamRef(
    "ground_pick | kick_left | kick_right | sit_toggle | roulade",
    "VERIFIED",
    src(_PROTO, 2412),
    "`pub type Skill = String`: 'A name, not an enumeration.' These five are the names a stock "
    "Microduck answers to, but a robot's skills are config, so an unknown one is refused with "
    "the list it does know. SKILL_SOURCE is where a client learns the real list.",
)
SKILL_SOURCE = UpstreamRef(
    "robot.subscribe -> SubscribeResult.skills",
    "VERIFIED",
    src(_PROTO, 2905),
    "'The configurable one-shot skills this robot has, in priority order, and the names "
    "robot.do answers to. A list rather than a field per skill, because which skills a robot "
    "has is now config: a client learns them here instead of assuming five.'",
)
SOUND_TAGS = UpstreamRef(
    "alarm | greet | inquire | peck | chirp | coo | wheee",
    "VERIFIED",
    src(_PROTO, 2244),
    "SoundTag enum, snake_case on the wire",
)
SOUND_TAG_LIST = ("alarm", "greet", "inquire", "peck", "chirp", "coo", "wheee")

ERR_METHOD_NOT_FOUND = -32601
ERR_INVALID_PARAMS = -32602

DEADMAN = UpstreamRef(
    "robotd intent deadman",
    "VERIFIED",
    src(_ROBOTD_DOC, 561),
    "'if intents stop arriving, the velocity goes to zero. Stop is not limp'. robotd zeroes a "
    "twist older than [safety] deadman_ms, 'Intent age past which the velocity is zeroed', 500 ms "
    "by default (robotd-params/src/lib.rs:1961-1962 and :2045). quackd's walk re-sends "
    "robot.move every 100 ms, so the twist never grows that old while a verb walks, and the "
    "deadman stops the legs only once quackd's twists stop arriving: a link that drops, a "
    "process that dies. Whether Huebot stops so is SHIM_DEADMAN.",
)
#: The age past which robotd's deadman zeroes a twist by default, in seconds (DEADMAN), which
#: Huebot's shim is asked to keep (SHIM_DEADMAN) and the pages that say what stops a walk state.
DEADMAN_S = 0.5

# ── what robot.state.policy can say ─────────────────────────────────────────────────────
#
# The wire label of whatever drove the tick. The tuples below sort them by what quackd may do
# with the robot, and an allowlist is what makes that safe: two labels stand the robot up, one
# seats it, and every other label, a skill's name, or one a shim makes up, reads as unknown.

POLICY_LABELS_DRIVEN = UpstreamRef(
    "walk, stand, ground_pick, sit, rise, <skill>, <skill>:unwind, skill",
    "VERIFIED",
    src("robotd/src/control.rs", 125),
    "The labels of a tick a network drove. 'Which network drove, as the wire label: walk, "
    "stand, ground_pick, sit, rise, or a configured skill's own name' (control.rs:125-126); a "
    "skill reports its name with ':unwind' while it unwinds, and 'skill' when its definition "
    "cannot be found (control.rs:605-609). Whether the robot is seated is the controller's seat "
    "latch: 'sit' while it holds the seat, 'rise' on the way up, and walk or stand once it is "
    "up (control.rs:627-648).",
)
POLICY_LABELS_NOT_DRIVEN = UpstreamRef(
    "held, homing, rest, limp_fall, limp_pose",
    "VERIFIED",
    src("robotd/src/main.rs", 3461),
    "The labels of a tick no network drove, from the match that picks policy_label every tick: "
    "limp_fall and limp_pose for the two halves of robotd's limp-fall sequence, rest for the end "
    "of a shutdown, held while the pick-up detector pauses the policy (SAFETY_PICKED_UP), when "
    "inference fails, or when nothing is driving, and homing while the joints ramp to the home "
    "pose.",
)

#: The labels of the two networks that drive a robot on its feet: what quackd reads as standing.
STANDING_LABELS = ("walk", "stand")
#: The sit-stand network holding the seat: what quackd reads as sitting. Matched exactly.
SITTING_LABELS = ("sit",)
#: Every other label robotd itself names, and what the robot is doing while it says it. A
#: configured skill reports its own name instead (the robot's list is SKILL_SOURCE), then that
#: name with UNWIND_SUFFIX while it unwinds.
NOT_STANDING_LABELS = {
    "rise": "the sit-stand network is getting the robot up off its seat",
    "ground_pick": "the ground pick is scooping at the floor",
    "skill": "a skill whose definition could not be found is running",
    "held": "no network is driving the legs: none is enabled, a pick-up paused it, or it failed",
    "homing": "the joints are ramping to the home pose",
    "rest": "the robot is easing into its rest pose to shut down",
    "limp_fall": "the robot is riding a fall down at limp gain",
    "limp_pose": "the robot is ramping to its pose after a fall",
}
UNWIND_SUFFIX = ":unwind"
#: The labels of the two halves of robotd's own answer to a fall, the limp and the pose
#: (POLICY_LABELS_NOT_DRIVEN). A shim that reports either is answering a fall itself.
LIMP_FALL_LABELS = ("limp_fall", "limp_pose")

# ── Huebot's side: what its maker says, and what nobody has seen ────────────────────────

SHIM_CALLS = UpstreamRef(
    "Huebot's shim answers the calls quackd makes as robotd does",
    "UNVERIFIED",
    MAKER,
    "Leif Rydenfalk's email of 2026-10-15 says the shim he will run on Huebot will answer the "
    "JSON-RPC quackd speaks to robotd, and names hello, robot.move, robot.stop, robot.head, "
    "robot.state and robot.health. quackd also needs robot.subscribe, without which the connect "
    "fails as not robotd, and robot.do, robot.sound and robot.enable for its verbs, whose "
    "answers it reads a boolean accepted in. Nobody has seen the shim answer any of them. "
    "docs/adapters/huebot/README.md says what each call has to carry.",
)
SHIM_LOOK = UpstreamRef(
    "robot.look, which Huebot's shim does not answer yet",
    "UNVERIFIED",
    MAKER,
    "The email of 2026-10-15 says robot.look is not on Huebot's side yet, and that the head "
    "turns with four servos. So quackd answers a look itself, refused, and writes nothing, there "
    "is no gaze and no watch, and the head is not centred before go_to and search_scan steer. "
    "Since no look goes out, neither waits for a glance to fade after one: head_fade_s is 0.",
)
JOINT_ORDER = UpstreamRef(
    "robot.state.joints in the Microduck's order, head_yaw at index 7, positive to the left",
    "UNVERIFIED",
    MAKER,
    "Huebot has 15 servos, the maker's page says, and it publishes neither their names nor "
    "their order. quackd reads joints by robotd's JOINT_NAMES, so the shim is asked to report "
    "them in that order, with head_yaw at index 7 in radians and positive when the head looks "
    "left, as a Microduck's does. The head yaw becomes extras.head_yaw_deg and nothing else: no "
    "verb steers on it.",
)
SHIM_DEADMAN = UpstreamRef(
    "Huebot stops when robot.move notifications stop arriving",
    "UNVERIFIED",
    MAKER,
    "robotd zeroes a twist older than 500 ms (DEADMAN), and quackd relies on that: a walk "
    "re-sends robot.move every 100 ms, a twist quackd holds back is not replaced by a zero, and "
    "a stop that cannot be delivered leaves the robot's deadman to stop it. That the shim keeps "
    "one is an assumption: answering robotd's calls is not the same as stopping as robotd does. "
    "Step 3 of the hardware checklist tests it, on a stand, before any walk on the floor, and "
    "the description of a real Huebot claims a deadman only once a Huebot has passed that step "
    "(DEADMAN_SEEN).",
)
SHIM_SKILLS = UpstreamRef(
    "the skills Huebot's robot.do answers to are the ones its robot.subscribe lists",
    "UNVERIFIED",
    MAKER,
    "Huebot runs behaviour code of its own, its maker says, and quackd assumes nothing about "
    "which skills it has. It offers each verb that sends robot.do only when every skill the verb "
    "sends is in the skills of the shim's robot.subscribe answer: sit and stand send "
    "sit_toggle, and kick sends kick_left and kick_right. grab, which sends ground_pick, is "
    "offered on no Huebot until its maker says what ground_pick does there and how the state "
    "reports that the robot holds something. The transport refuses a robot.do for any name the "
    "shim did not list without writing it, and a shim that lists none is offered none of the "
    "three.",
)
STAND_UP_RPC = UpstreamRef(
    "stand_up asks Huebot's shim to drive (robot.enable), which nobody has said gets a fallen "
    "Huebot up",
    "UNVERIFIED",
    MAKER,
    "There is no stand-up call. On robotd, robot.enable {on: true} is policy execution, which "
    "'can bring a limp robot up as a side effect of being asked to drive' (ROBOT_ENABLE), and "
    "what a shim does with it is the shim's. The maker says Huebot stands and steps, and "
    "whether it gets up from a fall is not published. stand_up reads the robot every 0.25 s "
    "after the request, for up to 10 s, sending nothing more, calls it upright as soon as it "
    "reads standing, and says it may need a hand only once the 10 s are up.",
)


def all_refs() -> list[UpstreamRef]:
    return [v for v in globals().values() if isinstance(v, UpstreamRef)]


def refs_by_status(status: Status) -> list[UpstreamRef]:
    return [r for r in all_refs() if r.status == status]
