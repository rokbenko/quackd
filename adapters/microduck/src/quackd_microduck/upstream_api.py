"""The only file in quackd allowed to spell an upstream Microduck method name.

Every constant is tagged VERIFIED (read from upstream source, link given) or UNVERIFIED
(designed upstream but not shipped, or an assumption of ours). `docs/adapters/microduck/README.md`
is the human-readable version of this file; `tests/test_upstream_api.py` proves that
UNVERIFIED names are only reachable from the experimental and stub transports.

Pinned and read on 2026-10-08, at daemon-v0.16.1 (API 41). The pin is a release now rather than
whatever `main` was on the day, because a robot runs a release. `main` was three commits past
the tag on the day of the read, with the same `duck-ipc-proto` byte for byte, and one of the
three turns `limp_fall` back on, which no release had done yet.

This is the third read. The first, on 2026-08-28, was against `main` with no pin, and in the
week after it upstream went from API 16 to 23 and `Skill` stopped being an enum, which a moving
link could not show (ADR-0022). The second, on 2026-09-04, pinned `main` at bc41fb5 on API 23.
quackd's handshake accepted that number and no other, so it refused every robotd release from
daemon-v0.12.0 (API 27) on at connect. It now serves any number from API_FLOOR up and warns on
every one but API_VERSION, which is upstream's own rule (VERSION_SKEW, ADR-0051): a robot
quackd has not read is refused by the calls that changed, by name, rather than by its number.

What changed between the last two reads, for the names quackd actually uses, is the version
number and fields added to three of the types it reads: `RobotState` gained `t_ns`, `imu`,
`frames`, `skeleton`, `velocities` and `currents_ma`, `SafetyState` gained `picked_up`, and
`HealthResult` gained `cpu_throttle`, each of which a reader that does not know it skips. Every
other type quackd sends or reads, `hello`'s both ways, the intents' params, `LookResult`,
`IntentResult`, `SubscribeResult`, `MoveState` and the sound tags, reads the same at both pins,
doc comments and serde attributes included, and so do the error codes. The drift elsewhere is
not purely additive. GitHub's compare of the two `lib.rs` files counts 1240 lines added and 44
removed, and outside the version number and the crate's own tests the removals are two doc lines
and `BusHealth` losing its `Copy` derive, none of them in a shape quackd sends or reads.
Upstream's own rule for the number is "A bump promises nothing in either direction" (above
`API_VERSION` in `lib.rs`).

robotd has also changed what it does without changing a shape. A stock robot walks and stands
on one network, `velstand.onnx` (VELSTAND), and with no standing network to hand a fall back
to, `limp_fall` is off, so a fall changes nothing in robotd. The duck looks around on its own
once nobody has steered its head for two seconds and it has then stood still for two more
(IDLE_HEAD), and a pick-up detector pauses the policy while somebody holds it
(SAFETY_PICKED_UP).

Nothing here has been run against a physical Microduck.
"""

from __future__ import annotations

from quackd.upstream import Status, UpstreamRef

REPO = "https://github.com/pollen-robotics/microduck"
PIN = "d818af5d91fce0e3f5f8c22d47d47caf7b3f17a1"  # daemon-v0.16.1, 2026-10-07
RELEASE = "daemon-v0.16.1"
"""The tag the pin is. robotd answers hello with its crate's version as `daemon_version`, which
is this without the `daemon-v` on a robot that runs the release."""
READ_ON = "2026-10-08"


def src(path: str, line: int | None = None) -> str:
    return f"{REPO}/blob/{PIN}/{path}" + (f"#L{line}" if line else "")


_PROTO = "duck-ipc-proto/src/lib.rs"
_ROBOTD_DOC = "docs/design/robotd-design.md"
_ARCH_DOC = "docs/design/architecture.md"

IPC_PROTO = src(_PROTO)
ARCH_DOC = src(_ARCH_DOC)
ROBOTD_DOC = src(_ROBOTD_DOC)
ROADMAP = src("docs/project/roadmap.md")
WEBRTC_DOC = src("docs/design/remote-webrtc.md")


__all__ = ["Status", "UpstreamRef"]  # re-exported: this file's own refs are the Microduck's

# ── protocol ────────────────────────────────────────────────────────────────────────────

JSONRPC_VERSION = "2.0"
API_VERSION = UpstreamRef(
    "41",
    "VERIFIED",
    src(_PROTO, 454),
    "duck-ipc-proto API_VERSION at the pin: the version quackd read, and the one it sends in "
    "hello. It was 16 on 2026-08-28 and 23 at the previous pin, on 2026-09-04. A robotd on any "
    "version from API_FLOOR up is served, as upstream's own clients serve one (VERSION_SKEW), "
    "and one on any version but this is warned about: in the log, in doctor and in every "
    "state's extras.robotd.",
)
API_FLOOR = UpstreamRef(
    "23",
    "VERIFIED",
    src(_PROTO, 165),
    "The oldest duck-ipc-proto quackd has read end to end. The previous read, at bc41fb5 on "
    "2026-09-04, was on API 23, which is the version note this cites, and daemon-v0.11.0 was the "
    "first release on it. Everything quackd sends has the same shape there as at the pin, and so "
    "does everything it reads but safety.picked_up and frames, which a robotd that old does not "
    "report. A robotd below this is refused at connect, and so is a hello that names no version, "
    "which is not a robotd quackd can check against anything.",
)
VERSION_SKEW = UpstreamRef(
    "No daemon refuses a call because this number differs",
    "VERIFIED",
    src(_PROTO, 57),
    "Upstream's rule for two peers on different versions (lib.rs:51-82). 'A bump promises "
    "nothing in either direction', and no daemon refuses a peer over the number: robotd answers "
    "hello without comparing (robotd/src/main.rs:5397), and robotctl warns once and carries on, "
    "because 'a call that cannot be served will name itself' (robotctl/src/main.rs:1571-1572). "
    "'What refuses instead' is the call: a method a release does not have is METHOD_NOT_FOUND "
    "naming it, and a params member it does not know is INVALID_PARAMS naming the member, since "
    "every params type denies unknown fields. A notification gets neither, because nothing "
    "answers one, which is what MOVE_ECHO is for. The doc comment above the rule still says 'A "
    "peer speaking a different version is refused rather than misparsed' (lib.rs:39-40); the rule "
    "and robotd's own hello say otherwise.",
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
    "go out after that answer, and none built after it can be held back past the next.",
)
OTHER_DAEMONS_ANSWER_HELLO = UpstreamRef(
    "updaterd answers hello too",
    "VERIFIED",
    src("updater/src/ipc.rs", 538),
    "updaterd shares duck-ipc-proto with robotd and answers hello with its own API_VERSION, "
    "release and revision (updater/src/ipc.rs:538-549), and every robot.* call, robot.subscribe "
    "among them, with METHOD_NOT_FOUND 'robot.* is served by robotd, not updaterd', because "
    "'Reaching this means a client aimed the wrong socket' (ipc.rs:777-815). So a hello that "
    "passes does not say which daemon answered it, and a robot.subscribe that is "
    "METHOD_NOT_FOUND, a call every robotd from API_FLOOR up has, says it was not robotd.",
)
RUNTIME_DIR_ENV = UpstreamRef(
    "DUCK_RUNTIME_DIR",
    "VERIFIED",
    src(_PROTO, 532),
    "Moves runtime_root(), the directory each daemon publishes /run/<service>/identity.json under "
    "(lib.rs:520-541), and nothing else: 'Not a configuration knob for a robot — nothing on the "
    "board sets it', it exists for upstream's tests and for a daemon run by hand. robotd's socket "
    "is a literal --socket default, SOCKET_ROBOTD (robotd/src/main.rs:235), which it does not "
    "move, and scripts/duck-sim names each simulated robotd's socket itself. quackd does not read "
    "it: with no --address, it connects to SOCKET_ROBOTD.",
)

SOCKET_ROBOTD = UpstreamRef("/run/robotd.sock", "VERIFIED", src(_PROTO, 500))
SOCKET_CONFIGD = UpstreamRef("/run/configd.sock", "VERIFIED", src(_PROTO, 501))
SOCKET_UPDATERD = UpstreamRef("/run/updaterd.sock", "VERIFIED", src(_PROTO, 487))
SOCKET_PADD = UpstreamRef("/run/padd/pad.sock", "VERIFIED", src(_PROTO, 508), "pad.input only")
SOCKET_TOFD = UpstreamRef("/run/tofd/tof.sock", "VERIFIED", src(_PROTO, 513), "tof.stream only")
SOCKET_MEDIA = UpstreamRef(
    "/run/mediad/media.sock",
    "VERIFIED",
    src(_PROTO, 517),
    "mediad's own socket, for media.frame only, and local-only: 'a raw camera frame is for a "
    "recorder or perception process on the robot, not a multi-megabyte WebRTC control reply'.",
)

# ── methods we use ──────────────────────────────────────────────────────────────────────

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
    "NOTIFICATION (no id). params {vx, vy, vyaw} m/s, rad/s, trunk frame; x fwd, y left, +vyaw left. "
    "Continuous: robotd's deadman zeroes velocity when these stop arriving.",
)
ROBOT_STOP = UpstreamRef(
    "robot.stop", "VERIFIED", src(_PROTO, 654), "request; zero velocity, NOT limp"
)
ROBOT_HEAD = UpstreamRef(
    "robot.head",
    "VERIFIED",
    src(_PROTO, 649),
    "NOTIFICATION {neck_pitch, head_pitch, head_yaw, head_roll} rad, HEAD_PARAMS, in joint "
    "space. robotd stores it as it arrives, from any client, in the one head slot robot.look "
    "writes too (robotd/src/main.rs:4364-4365), and robot.state.head reports that slot smoothed "
    "toward each new value a little every control tick, with any idle glance added on top "
    "(main.rs:3059-3060 and :3099-3104). quackd sends it for one thing, to hold a gaze: the head "
    "a robot.look answered with, sent again every GAZE_HOLD_S (IDLE_HEAD).",
)
ROBOT_LOOK = UpstreamRef(
    "robot.look",
    "VERIFIED",
    src(_PROTO, 652),
    "request {x, y, z, neck_pitch} trunk-frame point -> {head, clamped}. LookResult.head is "
    "'What was handed to the same path robot.head feeds — resend these to hold the gaze' "
    "(lib.rs:2233), which since daemon-v0.16.1 matters: see IDLE_HEAD.",
)
#: HeadParams: the four head joints robot.head takes and LookResult.head answers with, in the
#: order robot.state.head carries them (lib.rs:2206-2211). The type defaults a member it is not
#: sent to 0, and denies one it does not know.
HEAD_PARAMS = ("neck_pitch", "head_pitch", "head_yaw", "head_roll")
ROBOT_DO = UpstreamRef(
    "robot.do",
    "VERIFIED",
    src(_PROTO, 703),
    "request {skill} -> IntentResult {accepted, reason?}. Answered on accept/refuse, not on "
    "completion: 'a refusal names the scripted move already holding the robot'.",
)
ROBOT_POSE = UpstreamRef(
    "robot.pose",
    "VERIFIED",
    src(_PROTO, 708),
    "NOTIFICATION {z, roll, pitch, active} standing body pose offsets",
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
    "torque off — the robot collapses. quackd never sends this.",
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
    "head is the head robotd commands this tick: the slot robot.look and robot.head write, "
    "smoothed, with the idle look-around's glances on top (ROBOT_HEAD).",
)
#: The order robot.state.joints is indexed in, JOINT_NAMES (lib.rs:552-568): the left leg, the
#: neck, head and mouth, then the right leg.
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
MOVE_ECHO = UpstreamRef(
    "robot.state.move.requested",
    "VERIFIED",
    src("robotd/src/main.rs", 3746),
    "The twist robotd was last asked for, as it was asked. robot.move stores [vx, vy, vyaw] "
    "untouched (robotd/src/main.rs:4361) and every state frame reports it as requested, beside "
    "applied, the twist after the deadman and the limits, and robotd's own test holds it to "
    "that: 'what the client asked for must survive to the stream' (main.rs:7419). robot.move is "
    "a notification, so robotd answers nothing when it cannot parse one, and this is the only "
    "place quackd can see whether a twist was taken. Once the state has gone a few frames "
    "without showing one it sent, quackd sends no robot.move until the verb that sent it stops.",
)
ROBOT_HEALTH = UpstreamRef(
    "robot.health",
    "VERIFIED",
    src(_PROTO, 3552),
    "request -> {healthy, degraded?, reason?, battery{volts,percent}?, motors?, cpu_temp_c?, "
    "cpu_throttle?, control_loop?, bus, imu?}. quackd reads healthy, reason and battery.",
)
ROBOT_MODE = UpstreamRef("robot.mode", "VERIFIED", src(_PROTO, 759), "request -> {mode}")
ROBOT_SET_MODE = UpstreamRef(
    "robot.setMode", "VERIFIED", src(_PROTO, 767), "request {mode: 'walk'|'roller'}"
)
ROBOT_POLICIES = UpstreamRef(
    "robot.policies",
    "VERIFIED",
    src(_PROTO, 773),
    "request -> PoliciesResult {mode, enabled, slots, skills, homed?, sitting?}: what each "
    "policy slot runs, whether the duck has reached its home pose, and whether it is 'parked in "
    "its seat'. Documented, not called. The policy label on every robot.state frame already "
    "says both: robotd reports 'sit' while the seat latch holds the duck and 'homing' while the "
    "joints ramp home (POLICY_LABELS_DRIVEN, POLICY_LABELS_NOT_DRIVEN), so asking would cost a "
    "request per state read for nothing the label lacks. It is what to switch to if the labels "
    "ever stop saying it.",
)
TOF_STREAM = UpstreamRef(
    "tof.stream", "VERIFIED", src(_PROTO, 936), "on tofd socket; then tof.frame notifications (8x8)"
)
TOF_FRAME = UpstreamRef("tof.frame", "VERIFIED", src(_PROTO, 953))
PAD_INPUT = UpstreamRef(
    "pad.input",
    "VERIFIED",
    src(_PROTO, 920),
    "The raw input stream of the pad padd drives from, which padd answers itself on SOCKET_PADD. "
    "It exists because 'padd polls the last known stick value and keeps sending it at 50 Hz', so "
    "a radio that stops delivering reports still looks like a live driver to robotd, and only "
    "this stream shows the gap (lib.rs:908-919). It is a tap and ranks nothing: what the pad asks "
    "for reaches robotd as robot.move and robot.do, the calls any client makes (PAD_DRIVES).",
)
PAD_DRIVES = UpstreamRef(
    "padd drives while a pad is connected",
    "VERIFIED",
    src("padd/src/main.rs", 1230),
    "From the moment a gamepad connects ('pad connected — driving', padd/src/main.rs:727), padd "
    "sends robot.move on every tick its sticks ask for motion, fifty a second (lib.rs:915-916), "
    "and re-sends an unchanged stop every HEARTBEAT, 100 ms, while they do not: 'without it "
    "robotd's twist would age past the deadman while a pad sat connected and idle' "
    "(main.rs:1215-1229). robotd keeps one twist and ranks no client above another: robot.move "
    "sets it whoever sent it (robotd/src/main.rs:4358-4362), and a continuous intent is "
    "'last-writer-wins' (lib.rs:636-641, robotd/src/intents.rs:7-10). So a pad does not out-rank "
    "quackd. While its sticks are still, its stops and quackd's re-sent twist land in turn, ten "
    "a second each, and robot.state.move.requested shows whichever came last (MOVE_ECHO). A stick "
    "that asks for motion is sent more often than quackd re-sends, so the pad's twist is the one "
    "robotd holds most of the time. The buttons are robot.do requests, and a pad nobody has "
    "configured runs sit_toggle on A (robotd-params/src/lib.rs:214 and :233). With the pad gone, "
    "padd sends nothing, and after PAD_LOST_SIT, 1 s, it asks robotd to sit a robot that is not "
    "sitting already (padd/src/main.rs:190-193 and :1174-1205).",
)

SKILLS = UpstreamRef(
    "ground_pick | kick_left | kick_right | sit_toggle | roulade",
    "VERIFIED",
    src(_PROTO, 2412),
    "`pub type Skill = String` — 'A name, not an enumeration.' These five are still the names a "
    "stock robot answers to, but a robot's skills are config now, so an unknown one is refused "
    "with the list it does know. SKILL_SOURCE is where a client learns the real list.",
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
ERR_BUSY = 1
ERR_PROTOCOL_MISMATCH = 3
ERR_PERMISSION_DENIED = 14

DEADMAN = UpstreamRef(
    "robotd intent deadman",
    "VERIFIED",
    src(_ROBOTD_DOC, 561),
    "'if intents stop arriving, the velocity goes to zero. Stop is not limp'. robotd zeroes a "
    "twist older than [safety] deadman_ms, 'Intent age past which the velocity is zeroed', 500 ms "
    "by default (robotd-params/src/lib.rs:1961-1962 and :2045). Our walk verb re-sends robot.move "
    "at 10 Hz, so robotd's twist never grows that old while a verb walks, and the deadman stops "
    "the legs only once quackd's twists stop arriving: a link that drops, a process that dies. A "
    "connected pad's stops reach robotd sooner than that (PAD_DRIVES).",
)
#: What stops a walk, in seconds: the age past which robotd's deadman zeroes a twist by default
#: (DEADMAN), how often padd re-sends its stop while a pad's sticks are still, its HEARTBEAT, and
#: how long after a driving pad goes padd asks robotd to sit the duck, its PAD_LOST_SIT
#: (PAD_DRIVES). The pages that say what stops a quackd walk state each of them.
DEADMAN_S = 0.5
PAD_HEARTBEAT_S = 0.1
PAD_LOST_SIT_S = 1.0
STATE_NEEDS_SUBSCRIBE = UpstreamRef(
    "robot.state is not pushed until robot.subscribe",
    "VERIFIED",
    src(_ROBOTD_DOC, 752),
    "'robot.subscribe turns a connection into a stream; the loop publishes into a bounded "
    "broadcast and never waits on a subscriber, so a slow client gets a gap rather than "
    "applying backpressure.' A connection that never subscribes learns nothing about the "
    "robot, which is why the transport subscribes during connect().",
)
FALLEN_IS_A_REPORT = UpstreamRef(
    "safety.fallen gates nothing upstream",
    "VERIFIED",
    src(_ROBOTD_DOC, 566),
    "'The fall verdict is a report, not a rule. It is computed every tick — projected gravity "
    "in the trunk frame, debounced 0.2 s', and it 'gates nothing'. Nor does robotd stand a "
    "fallen duck up at the pin: limp_fall, its one answer to a fall, is off by default (VELSTAND "
    "says why), and 'With it off, a fall changes nothing: the policy keeps driving and the "
    "humans stay in charge' (robotd-params/src/lib.rs:1978-1979). Refusing to walk a fallen "
    "robot is quackd's own decision, so quackd must actually be reading the frame to make it.",
)
SAFETY_PICKED_UP = UpstreamRef(
    "safety.picked_up",
    "VERIFIED",
    src(_PROTO, 4104),
    "'Somebody is holding the robot: the pick-up detector ([pickup]) has paused the policy and "
    "the legs hold the pause pose until it is put back down', and while it is true the policy "
    "label reads held. 'Absent from a robotd that predates the detector, which reads as never "
    "picked up.' quackd reads it before safety.fallen, because a duck held at an angle also "
    "reads as fallen, and nothing that moves the duck is sent while it is true. It first shipped "
    "in daemon-v0.15.2, on the API 37 that daemon-v0.15.0 and daemon-v0.15.1 report without it, "
    "so the version cannot say whether a robot has it, and quackd publishes whether its frames "
    "carry the key as extras.pickup_detection. robotd writes the key whether or not [pickup] is "
    "enabled, so present means the detector exists, not that it runs.",
)
STATE_FRAMES = UpstreamRef(
    "RobotState.frames",
    "VERIFIED",
    src(_PROTO, 3944),
    "{camera, tof, head_imu?}: where the camera and the ToF sensor are in the trunk frame at "
    "this tick's measured head joints, 'from the same head FK robot.look uses'. A pose is "
    "{pos} in metres and {quat} scalar-first [w, x, y, z], and camera is the OpenCV camera "
    "frame, '+x right, +y down, +z along the optical axis' (lib.rs:3963-3974). On the stream "
    "since API 24, which first shipped in daemon-v0.12.0 on API 27. quackd turns camera's "
    "optical axis, quat applied to [0, 0, 1], into extras.head_yaw_deg and head_pitch_deg, "
    "where the camera points from the trunk, positive left and up: the convention the "
    "simulators have always published head_yaw_deg in. It records whether a robot reports "
    "frames at all as extras.robotd.state_frames, and reads the head_yaw joint instead on a "
    "robotd that does not (HEAD_YAW_SIGN).",
)
HEAD_YAW_SIGN = UpstreamRef(
    "+head_yaw looks left (+y) on alpha",
    "VERIFIED",
    src("kinematics/src/head.rs", 261),
    "Upstream's own test of its head FK pins the sign of two joints, so that 'an MJCF update' "
    "cannot make 'the robot nod the wrong way' (kinematics/src/head.rs:250-264): this one, and "
    "'+head_pitch looks DOWN (-z) on alpha'. On a frame with no frames.camera, quackd takes "
    "the measured head_yaw joint, robot.state.joints at its index in JOINT_NAMES, as "
    "extras.head_yaw_deg, with this sign, which ignores how the pitch joints tilt the plane "
    "the head pans in. It takes no pitch that way: the camera's pitch is neck_pitch and "
    "head_pitch through the FK together, and robotd's home pose holds both at 0.3491 rad "
    "(DEFAULT_POSITION, duck-control/src/model.rs:37-53), so the head_pitch joint alone says "
    "nothing quackd could read as up or down.",
)
IDLE_HEAD = UpstreamRef(
    "robotd's idle look-around",
    "VERIFIED",
    src("robotd/src/idle_head.rs", 26),
    "New in daemon-v0.16.1, the first release with it, on API 41; daemon-v0.16.0, on API 39, "
    "has none, and the commit that brought it to upstream's main was still on API 39. robotd "
    "counts the duck still once the policy has the robot, nothing asks it to move, nobody has "
    "commanded the head for IDLE_AFTER (2 s), and it is not falling, held, singing or running a "
    "skill (robotd/src/main.rs:3071-3078), and once it has counted it still for IDLE_AFTER more "
    "(idle_head.rs:118-120), so no sooner than twice IDLE_AFTER after the last head command, it "
    "glances around on top of the commanded head, up to 0.6 rad of yaw and 0.2 rad of pitch, "
    "fading in and out over FADE (1.5 s) 'so it never jumps' (idle_head.rs:16-17). A robot.look or "
    "robot.head from anyone resets the wait, 'so a client steering the head is never fought' "
    "(robotd/src/intents.rs:247-248), and only for as long as it keeps steering. So quackd holds a "
    "gaze: after a robot.look it sends LookResult.head again as robot.head every GAZE_HOLD_S, half "
    "of IDLE_AFTER, until a stop, the next look, another client steering the head, a link that "
    "fails or the end of the connection. And go_to and search_scan wait FADE after centring the "
    "head, since a glance that was under way when the look arrived takes that long to fade out, "
    "and on mediad's /frame they steer only on a frame taken after the wait "
    "(MEDIA_FRAME_ON_REQUEST).",
)
#: The idle look-around in numbers (IDLE_HEAD): robotd counts the duck still only once nobody
#: has commanded the head for IDLE_AFTER (robotd/src/main.rs:3076), starts the look-around once
#: it has counted it still for IDLE_AFTER more (robotd/src/idle_head.rs:26 and :118-120), and
#: fades it in or out over FADE (:29).
IDLE_AFTER_S = 2.0
IDLE_FADE_S = 1.5
#: The API version the first release with the idle look-around reports, daemon-v0.16.1's. A
#: robotd on an older one is not waited for. A build of upstream's main from between
#: daemon-v0.16.0 and daemon-v0.16.1 can glance on a lower number, but a robot runs a release.
IDLE_HEAD_SINCE_API = 41
VELSTAND = UpstreamRef(
    "velstand.onnx",
    "VERIFIED",
    src("robotd-params/src/lib.rs", 1861),
    "The walk a stock robotd loads at the pin: 'The velstand gait (set v5) walks on a twist and "
    "stands still at zero command, so no standing network is loaded by default', and "
    "alpha_walking.onnx and alpha_stand.onnx 'stay in the set for a board that loads them back "
    "by hand' (robotd-params/src/lib.rs:1856-1859). Its subscribe answer names it in `walk`. "
    "limp_fall's stand-up hands the duck to the standing network, which is why limp_fall is "
    "'Off by default since the velstand gait became the default walk' (lib.rs:1971). quackd's "
    "physics simulator still runs alpha_walking and alpha_stand.",
)

# ── what robot.state.policy can say ─────────────────────────────────────────────────────
#
# The wire label of whatever drove the tick, which names a network or a state of the robot.
# POLICY_LABELS_DRIVEN and POLICY_LABELS_NOT_DRIVEN are where each label is read, at the pin,
# and every one of them but `rest`, the end of a shutdown, was already there at bc41fb5. The
# tuples below sort them by what quackd may do with the duck, and an allowlist is what makes
# that safe: two labels stand the duck up, one seats it, and every other label, a skill's
# name, or one a newer robotd adds, reads as unknown.

POLICY_LABELS_DRIVEN = UpstreamRef(
    "walk, stand, ground_pick, sit, rise, <skill>, <skill>:unwind, skill",
    "VERIFIED",
    src("robotd/src/control.rs", 125),
    "The labels of a tick a network drove. 'Which network drove, as the wire label: walk, "
    "stand, ground_pick, sit, rise, or a configured skill's own name' (control.rs:125-126); a "
    "skill reports its name with ':unwind' while it unwinds, and 'skill' when its definition "
    "cannot be found (control.rs:605-609). Whether the duck is seated is the controller's seat "
    "latch: 'sit' while it holds the seat, 'rise' on the way up, and walk or stand once it is "
    "up (control.rs:627-648). The label names a role and not a file: 'Two releases with "
    "different gaits both report walk' (robotd-design.md:761-762).",
)
POLICY_LABELS_NOT_DRIVEN = UpstreamRef(
    "held, homing, rest, limp_fall, limp_pose",
    "VERIFIED",
    src("robotd/src/main.rs", 3461),
    "The labels of a tick no network drove, from the match that picks policy_label every tick: "
    "limp_fall and limp_pose for the two halves of the limp-fall sequence, rest for the end of "
    "a shutdown, held while the pick-up detector pauses the policy (SAFETY_PICKED_UP), when "
    "inference fails, or when nothing is driving, and homing while the joints ramp to the home "
    "pose.",
)

#: The labels of the two networks that drive a duck on its feet: what quackd reads as standing.
STANDING_LABELS = ("walk", "stand")
#: The sit-stand network holding the seat: what quackd reads as sitting. Matched exactly.
SITTING_LABELS = ("sit",)
#: Every other label robotd itself names, and what the robot is doing while it says it. A
#: configured skill reports its own name instead (the robot's list is SKILL_SOURCE), then that
#: name with UNWIND_SUFFIX while it unwinds.
NOT_STANDING_LABELS = {
    "rise": "the sit-stand network is getting the duck up off its seat",
    "ground_pick": "the ground pick is scooping at the floor",
    "skill": "a skill whose definition robotd could not find is running",
    "held": "no network is driving the legs: none is enabled, a pick-up paused it, or it failed",
    "homing": "the joints are ramping to the home pose",
    "rest": "the duck is easing into its rest pose to shut down",
    "limp_fall": "the duck is riding a fall down at limp gain",
    "limp_pose": "the duck is ramping to its pose after a fall",
}
UNWIND_SUFFIX = ":unwind"
#: The labels of a tick robotd does not count the duck still on, whatever else holds, so that
#: its idle look-around cannot be waiting out IDLE_AFTER through one (IDLE_HEAD). limp_fall and
#: limp_pose are the two halves of the limp-fall sequence (robotd/src/main.rs:3479 and :3490),
#: which robotd's still excludes (main.rs:3072), and ground_pick, rise and skill are a scripted
#: move under way, which the controller counts as busy (robotd/src/control.rs:382-386) and still
#: excludes too (main.rs:3078), as it does a configured skill reporting its own name, and that
#: name with UNWIND_SUFFIX. held is not one of them: robotd also labels held a tick on which its
#: policy failed while it went on driving (main.rs:3527). A frame whose safety.picked_up or
#: safety.limp is true comes from a tick robotd did not count still on either (main.rs:3757-3760).
NOT_STILL_LABELS = ("limp_fall", "limp_pose", "ground_pick", "rise", "skill")

# ── where a frame comes from ────────────────────────────────────────────────────────────
#
# robotd's socket carries no picture. At the pin mediad hands one out three ways: its WebRTC
# video track, GET /frame on its console port, and media.frame on its local socket. quackd's
# `--camera-url` can reach the first two. The track is the picture as the camera took it, which
# on an alpha is a quarter turn off upright, and media.video, on each WebRTC peer's control
# channel, says how far to turn it and what the lens is. What mediad's duck detector finds rides
# that channel too.

MEDIAD_FRAME_HTTP = UpstreamRef(
    "GET :8080/frame",
    "VERIFIED",
    src("mediad/src/web.rs", 97),
    "mediad's console port answers GET /frame with one upright PNG per request, encoded from the "
    "whole picture on the robot, 'No downscale' (mediad/src/snapshot.rs:94), uncached "
    "(Cache-Control no-store) and at most four at a time. When it cannot hand one out, four "
    "being encoded already, no frame from its capture, or none within the 3 s it waits for one "
    "(snapshot.rs:9), it answers 503 'Camera snapshot unavailable; retry when capture is "
    "running.' (mediad/src/web.rs:102-133). The port is 8080 on every address by default "
    "(mediad/src/main.rs:32 and :79), with no authentication. New since bc41fb5, where the port "
    "served the console page alone: it first shipped in daemon-v0.13.0. When --camera-url's path "
    "is MEDIAD_FRAME_PATH, quackd pulls it at a rate of its own, lower than any other camera's, "
    "waits through a 503 at connect, takes the lens from the first frame's shape "
    "(ALPHA_UPRIGHT_FOV), and records whether the route has answered as "
    "extras.robotd.mediad_frame.",
)
#: The path mediad serves its snapshot at (MEDIAD_FRAME_HTTP), which is how quackd tells a
#: `--camera-url` that points at it from any other HTTP camera.
MEDIAD_FRAME_PATH = "/frame"
MEDIA_FRAME_ON_REQUEST = UpstreamRef(
    "a frame asked for is taken after it is asked for",
    "VERIFIED",
    src("mediad/src/pipeline.rs", 275),
    "mediad's capture branch copies a frame out only for a reader that has asked for one, and 'A "
    "reader waits for the next frame rather than taking the last one. The demand has to be "
    "answered by a capture that happens *after* it' (mediad/src/pipeline.rs:265-284), stamped "
    "captured_at at 'the moment the buffer existed' (pipeline.rs:1151-1153). GET /frame asks "
    "for its picture that way, through media.frame on SOCKET_MEDIA, once the request has reached "
    "it (mediad/src/web.rs:102-113, mediad/src/snapshot.rs:8-49, mediad/src/frame.rs:9 and "
    ":195), so the PNG it answers with was taken after quackd sent the request. The PNG carries "
    "no captured_at, so quackd stamps each /frame with the moment its request went out, which "
    "the picture is no older than: how old a frame is, and whether it was taken after a move "
    "go_to or search_scan made had settled, are both counted from then, which errs toward older "
    "and never toward younger.",
)
MEDIA_FRAME = UpstreamRef(
    "media.frame",
    "VERIFIED",
    src(_PROTO, 599),
    "On SOCKET_MEDIA: 'One raw camera frame. mediad answers the JSON-RPC header, followed "
    "immediately by the bytes named in that header', the header being {width, height, format, "
    "bytes, captured_at_unix_us, rotate} with format UYVY. 'Deliberately not a Call', so it "
    "never reaches the WebRTC control datachannel. quackd does not call it.",
)
MEDIA_VIDEO = UpstreamRef(
    "media.video",
    "VERIFIED",
    src("mediad/src/session.rs", 57),
    "{width, height, rotate, mono_ns, real_ns, intrinsics?}: what the video is, which mediad "
    "answers itself on each WebRTC peer's control datachannel, as a call and as a notification "
    "it pushes when the channel opens (mediad/src/session.rs:35-54 and :82-114). The push can be "
    "lost: 'Pushing it when the channel appears races the browser', so upstream's own console "
    "asks, 'because a question it asks when it is ready cannot arrive too early'. width and "
    "height are the frame as it is sent, which nothing on the robot turns (CAMERA_MOUNT), rotate "
    "is 'Degrees clockwise the camera is mounted from upright', and intrinsics, absent rather "
    "than null 'when the geometry is unknown', are 'For the frame as it is sent': 'A consumer "
    "that rotates the image has to rotate these too — cx and cy swap, and so do fx and fy' "
    "(mediad/src/camera.rs:90-93). The swap is of the axes they lie along: a quarter turn "
    "clockwise sends the principal point, as it sends every pixel, from (cx, cy) to "
    "(height-1-cy, cx), not to (cy, cx). "
    "quackd's WebRTC camera asks once, which is the one line it "
    "writes to the channel, and reads the answer or the push, whichever comes. It hands out no "
    "frame until one of them has said rotate, and takes the field of view of an upright frame "
    "from intrinsics.",
)
CAMERA_MOUNT = UpstreamRef(
    "an alpha's camera is mounted a quarter turn off upright, a beta's upright",
    "VERIFIED",
    src("robotd-params/src/board.rs", 82),
    "camera_mount_degrees is 90 on the Zero 3W, the alpha board, whose 'IMX219 sits a quarter "
    "turn off', and 0 on the beta, whose 'GC2093 sits the right way up', and mediad tells every "
    "consumer that angle as media.video's rotate (robotd-params/src/board.rs:77-87). Nothing on "
    "the robot turns the pixels, 'Nothing converts and nothing rotates' "
    "(mediad/src/pipeline.rs:28), since a videoflip cost the encoder its zero-copy path, so the "
    "WebRTC track off an alpha is sideways and the consumer turns it. Upstream's uyvy crate, "
    "which mediad turns its PNGs and its duck detector's input with, turns a frame clockwise by "
    "rotate: 'A quarter turn clockwise sends source (x, y) to upright (h-1-y, x)' "
    "(uyvy/src/lib.rs:56-80). quackd's WebRTC camera turns each frame the same way. A mediad "
    "started with --flip-in-pipeline turns the pixels in its pipeline as well and still reports "
    "the mount as rotate and the size the camera takes as media.video's width and height "
    "(mediad/src/main.rs:557-594 and :614-615), so a frame that comes in tall where those "
    "describe a wide one is one the pipeline turned, and quackd hands it out as it came rather "
    "than turn it onto its side again.",
)
ALPHA_INTRINSICS = UpstreamRef(
    "the alpha family's camera calibration",
    "VERIFIED",
    src("robotd-params/src/lib.rs", 645),
    "CameraIntrinsics::alpha(), 'the head camera module and M12 lens every alpha unit carries — "
    "one part across the family, so one solve is every unit's calibration', solved on a 1280 by "
    "720 frame 'as mediad sends it (unrotated)' (robotd-params/src/lib.rs:634-661), its numbers "
    "ALPHA_SOLVE. mediad sends it as media.video's intrinsics, scaled to the stream and "
    "with source family, unless the robot carries a solve of its own "
    "(mediad/src/camera.rs:221-234). Turned upright, an alpha frame is as wide as the rows it "
    "was sent with, across fy, so its lens is 2*atan(360/fy) on the solve's own frame: 37.4 "
    "degrees. Its principal point is not the frame's centre: turned upright it sits at "
    "720-1-cy, about 115 pixels left of the centre, and quackd's detectors take a bearing from "
    "the centre, so every bearing is about 6 degrees left of the one from the optical axis. "
    "They do not correct for it.",
)
#: CameraIntrinsics::alpha() (ALPHA_INTRINSICS): the frame it was solved on, its focal lengths
#: and its principal point, in pixels of that frame as mediad sends it, which is not turned.
ALPHA_SOLVE = {
    "width": 1280,
    "height": 720,
    "fx": 1061.8060025020175,
    "fy": 1062.193713957031,
    "cx": 596.7776848217006,
    "cy": 474.52348043048636,
}
IMX219_FOV = UpstreamRef(
    "an IMX219 sees 62 degrees across its frame's long side",
    "VERIFIED",
    src("mediad/src/sensor.rs", 99),
    "hfov_deg of the IMX219, the alpha robots' head camera behind its M12 lens, the 'Horizontal "
    "field of view across a frame in [Sensor::mode], degrees', that mode being 1920 by 1080 "
    "(mediad/src/sensor.rs:36-39 and :81-101). It is the same at every size mediad streams "
    "at, since each is the sensor's whole field scaled: 'the field of view is what stays fixed "
    "as the ISP scales the frame to whatever [media] quality asks for' (mediad/src/camera.rs:"
    "16-18). The beta's GC2093 has none, since 'nobody has measured this lens yet' "
    "(sensor.rs:110-111). IMX219_FOV_DEG is the number.",
)
#: The IMX219's field of view across its frame's long side, in degrees (IMX219_FOV).
IMX219_FOV_DEG = 62.0
FEATURE_STREAM = UpstreamRef(
    "media.detections",
    "VERIFIED",
    src("mediad/src/main.rs", 676),
    "mediad's duck detector, pushed as a notification {width, height, took_ms, "
    "boxes[{x0,y0,x1,y1,score}]} (mediad/src/detect.rs:332) on each WebRTC peer's control "
    "datachannel and nowhere else: 'Detections go to the peer as notifications, on the same "
    "channel the console already reads robot.state from'. It finds ducks, not balls. Off by "
    "default, and two looks a second when on: '2 Hz is a thermal number, not a taste' "
    "(robotd-params/src/lib.rs:698-724, [duck_detector]). quackd's WebRTC camera keeps the "
    "newest one and steers on none of them.",
)

# ── how a program reaches a duck from somewhere else ────────────────────────────────────

RENDEZVOUS_LANE = UpstreamRef(
    "rendezvous control lane",
    "VERIFIED",
    src("mediad/src/relay.rs", 44),
    "What upstream sends a server-side agent over now, in place of the WebSocket gateway it once "
    "designed (WEBSOCKET_GATEWAY): JSON-RPC over HTTP/SSE through upstream's rendezvous service, "
    "which mediad's relay bridges to the robot's own sockets. Upstream names two limits on it. "
    "'No pixels': 'Video over this would be base64 inside JSON at the rate below', so frames "
    "come from WebRTC or media.stream (docs/design/remote-access-design.md:589-590). And 'What "
    "it is not is a teleop lane': 'The rendezvous allows 1200 requests per 60 s per peer', and "
    "'exceeding it earns a `429` on the *whole peer*, which would take the robot's own lease "
    "down with it'. A walk under quackd re-sends its twist against robotd's deadman until it "
    "ends, a joystick's rate rather than a behaviour's, so quackd does not drive over it: "
    "microduck:websocket is deprecated, and a robot elsewhere is reached over ssh with "
    "microduck:jsonrpc.",
)

# ── assumptions and things upstream has designed but not shipped ────────────────────────

WEBSOCKET_GATEWAY = UpstreamRef(
    "WebSocket agent gateway",
    "UNVERIFIED",
    src(_ARCH_DOC, 443),
    "Dropped upstream. At bc41fb5 architecture.md §5.3 designed one, 'open a WebSocket, poll a "
    "frame, send intents', for a server-side agent. At the pin the same table sends that agent "
    "over the 'rendezvous control lane — JSON-RPC over HTTP/SSE, no WebRTC', and roadmap M5's "
    "SDK is that lane. There is no gateway to connect to.",
)
STAND_UP_RPC = UpstreamRef(
    "stand_up asks robotd's policy to drive (robot.enable), which upstream does not promise "
    "gets a fallen duck up",
    "UNVERIFIED",
    src("robotd-params/src/lib.rs", 1970),
    "No such RPC. robot.enable {on: true} is policy execution, which 'can bring a limp robot up "
    "as a side effect of being asked to drive', and robot.init, the call upstream names for "
    "bringing up a duck that is on its feet, moves every joint and is never sent. robotd's own "
    "stand-up after a fall was the end of limp_fall, and at the pin limp_fall is off by "
    "default: 'With it off, a fall changes nothing: the policy keeps driving and the humans "
    "stay in charge' (robotd-params/src/lib.rs:1978-1979). Whether velstand gets a duck off the "
    "floor has not been seen.",
)
ALPHA_UPRIGHT_FOV = UpstreamRef(
    "a tall /frame is assumed to be an alpha's IMX219, 62 degrees from top to bottom",
    "UNVERIFIED",
    src("mediad/src/sensor.rs", 99),
    "quackd's own reading of mediad's GET /frame (MEDIAD_FRAME_HTTP), whose PNG carries no lens. "
    "On a stock robot only an alpha's camera gives a picture taller than wide: its IMX219 is "
    "mounted a quarter turn off upright (CAMERA_MOUNT) and turned upright for the PNG, so the 62 "
    "degrees along its long side (IMX219_FOV) run from the picture's top to its bottom, and "
    "across its width it sees 2*atan(tan(31 degrees) * w/h), 37.3 degrees at 720 by 1280. That "
    "is the module's design, with the principal point at the centre, and not a calibration: the "
    "alpha family's own solve gives a little more (ALPHA_INTRINSICS). It rests on the shape "
    "alone. A beta's camera sits upright and gives a wide picture, which gets no lens, but "
    "upstream's scripts/duck-sim renders its twin's camera rolled like an alpha's, at MuJoCo's 45 "
    "degrees across the frame as sent (mediad/src/main.rs:245-248, mediad/src/camera.rs:236-256), "
    "so a tall /frame from a twin is 45 degrees wide and this is wrong there. The first frame of "
    "a working /frame camera sets the manifest's camera_fov_deg from it and names this "
    "assumption beside it, so detections through it say they are uncalibrated and the log says "
    "to pass --fov-deg, which overrides it on quackd run, and every state lists it in "
    "extras.assumptions while it is in use.",
)


def all_refs() -> list[UpstreamRef]:
    return [v for v in globals().values() if isinstance(v, UpstreamRef)]


def refs_by_status(status: Status) -> list[UpstreamRef]:
    return [r for r in all_refs() if r.status == status]
