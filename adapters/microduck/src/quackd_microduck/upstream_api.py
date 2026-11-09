"""The only file in quackd allowed to spell an upstream Microduck method name.

Every constant is tagged VERIFIED (read from upstream source, link given) or UNVERIFIED
(designed upstream but not shipped, or an assumption of ours). `docs/adapters/microduck/README.md`
is the human-readable version of this file; `tests/test_upstream_api.py` proves that
UNVERIFIED names are only reachable from the experimental transport.

Pinned and read on 2026-11-08, at daemon-v0.18.8 (API 50), the newest release that day. The pin
is a release rather than whatever `main` was on the day, because a robot runs a release.

This is the fourth read. The first, on 2026-08-28, was against `main` with no pin, and in the
week after it upstream went from API 16 to 23 and `Skill` stopped being an enum, which a moving
link could not show (ADR-0022). The second, on 2026-09-04, pinned `main` at bc41fb5 on API 23.
quackd's handshake accepted that number and no other, so it refused every robotd release from
daemon-v0.12.0 (API 27) on at connect. It now serves any number from API_FLOOR up and warns on
every one but API_VERSION, which is upstream's own rule (VERSION_SKEW, ADR-0051): a robot
quackd has not read is refused by the calls that changed, by name, rather than by its number.
The third, on 2026-10-08, pinned daemon-v0.16.1 (d818af5) on API 41.

What changed between the last two reads, for the names quackd actually uses, is the version
number and doc comments. The nine bumps from 41 to 50 add or remove only calls quackd does not
make: the vet book (`robot.vetbook`, `robot.vetbookNote`, `robot.vetbookTally`, `vetbook.share`),
updates of the operating system (`os.*`), anonymous usage data (`telemetry.*`), an outcome in the
update log, and `robot.remoteSessionActive`, which is gone. Every type quackd sends or reads,
`hello`'s both ways, the intents' params, `LookResult`, `IntentResult`, `SubscribeResult`,
`RobotState` and the blocks in it, `HealthResult`, the sound tags and the error codes, reads the
same at both pins but for two doc comments: `SubscribeResult.skills` lists the robot's emotions
after its skills (SKILL_SOURCE), and a servo or the IMU board that goes quiet on a running robot
is reported as degraded rather than unhealthy, with `healthy` false either way (ROBOT_HEALTH).
Two error codes were added, both for calls quackd does not make. Upstream's own rule for the
number is "A bump promises nothing in either direction" (above `API_VERSION` in `lib.rs`).

robotd has also changed what it does without changing a shape. From daemon-v0.16.2 `limp_fall`
is on by default, so robotd takes a falling duck off its policy, poses it and hands it back
(LIMP_FALL), a robot is seeded with the policy set `v8` (VELSTAND), and a gamepad's full stick
asks for 0.4 m/s forward and 1.0 rad/s of turn (NO_TWIST_CLAMP). From daemon-v0.17.3 the idle
look-around's timings are settings, and from daemon-v0.17.9 it takes the head over rather than
adding to it (IDLE_HEAD). From daemon-v0.18.8 `robot.do` plays an emotion as well as a skill,
and while one plays robotd holds the twist at zero and reports it so, which quackd cannot tell
from a twist robotd did not take (MOVE_ECHO). The pad's mapping is a crate of its own, shared
with a second pad daemon for a pad over UDP (PAD_DRIVES), every `GET /frame` counts as a photo
in the robot's vet book (MEDIAD_FRAME_HTTP), and the beta's camera has a calibration
(IMX219_FOV). A stock robot still walks and stands on one network, `velstand.onnx` (VELSTAND),
and a pick-up detector still pauses the policy while somebody holds it (SAFETY_PICKED_UP).

Nothing here has been run against a physical Microduck.
"""

from __future__ import annotations

from quackd.upstream import Status, UpstreamRef

REPO = "https://github.com/pollen-robotics/microduck"
PIN = "4a763dd893f3982c3a11c136468ccf5d26bcc28a"  # daemon-v0.18.8, 2026-11-06
RELEASE = "daemon-v0.18.8"
"""The tag the pin is. robotd answers hello with its crate's version as `daemon_version`, which
is this without the `daemon-v` on a robot that runs the release."""
READ_ON = "2026-11-08"


def src(path: str, line: int | None = None) -> str:
    return f"{REPO}/blob/{PIN}/{path}" + (f"#L{line}" if line else "")


_PROTO = "duck-ipc-proto/src/lib.rs"
_ROBOTD_DOC = "docs/design/robotd-design.md"
_ARCH_DOC = "docs/design/architecture.md"

IPC_PROTO = src(_PROTO)
ARCH_DOC = src(_ARCH_DOC)
ROBOTD_DOC = src(_ROBOTD_DOC)
WEBRTC_DOC = src("docs/design/remote-webrtc.md")


__all__ = ["Status", "UpstreamRef"]  # re-exported: this file's own refs are the Microduck's

# ── protocol ────────────────────────────────────────────────────────────────────────────

JSONRPC_VERSION = "2.0"
API_VERSION = UpstreamRef(
    "50",
    "VERIFIED",
    src(_PROTO, 519),
    "duck-ipc-proto API_VERSION at the pin: the version quackd read, and the one it sends in "
    "hello. It was 16 on 2026-08-28, 23 at the pin of 2026-09-04 and 41 at the pin of "
    "2026-10-08, daemon-v0.16.1. A robotd on any version from API_FLOOR up is served, as "
    "upstream's own clients serve one (VERSION_SKEW), and one on any version but this is warned "
    "about: in the log, in doctor and in every state's extras.robotd. The version notes for v42 "
    "to v50 (lib.rs:455-518) add or remove calls quackd does not make, and v48's changes what "
    "robot.health says of a quiet servo, not its shape (ROBOT_HEALTH).",
)
API_FLOOR = UpstreamRef(
    "23",
    "VERIFIED",
    src(_PROTO, 165),
    "The oldest duck-ipc-proto quackd has read end to end. The read at bc41fb5 on "
    "2026-09-04 was on API 23, which is the version note this cites, and daemon-v0.11.0 was the "
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
    "hello without comparing (robotd/src/main.rs:5832), and robotctl warns once and carries on, "
    "because 'a call that cannot be served will name itself' (robotctl/src/main.rs:1622-1623). "
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
    src(_ARCH_DOC, 229),
    "'Wire format: JSON-RPC 2.0, one object per line (NDJSON).'",
)
LINES_IN_ORDER = UpstreamRef(
    "robotd reads a connection's lines in order",
    "VERIFIED",
    src("robotd/src/main.rs", 4591),
    "One task per connection reads its lines one at a time (robotd/src/main.rs:4585-4752). A "
    "notification such as robot.move is applied as it is read, before the next line is "
    "(main.rs:4712-4719), and a request is answered before the line after it is read "
    "(main.rs:4746-4750). So an answer to any call quackd wrote after a robot.move says robotd "
    "has read and stored that twist, which is when MOVE_ECHO can first show it. State frames "
    "reach the connection from a broadcast the control loop publishes into, decimated to the "
    "rate asked for (main.rs:4657-4669), so one built just before the twist was stored can still "
    "go out after that answer, and none built after it can be held back past the next. The same "
    "task writes the frames and the answers, a line at a time, and writes a request's answer "
    "straight after it handles the request, so every frame written before the answer to a "
    "robot.stop was built before the stop zeroed the twist (MOVE_ECHO), and the twists before "
    "the stop show in those frames or in none.",
)
OTHER_DAEMONS_ANSWER_HELLO = UpstreamRef(
    "updaterd answers hello too",
    "VERIFIED",
    src("updater/src/ipc.rs", 568),
    "updaterd shares duck-ipc-proto with robotd and answers hello with its own API_VERSION, "
    "release and revision (updater/src/ipc.rs:568-579), and every robot.* call, robot.subscribe "
    "among them, with METHOD_NOT_FOUND 'robot.* is served by robotd, not updaterd', because "
    "'Reaching this means a client aimed the wrong socket' (ipc.rs:866-906). So a hello that "
    "passes does not say which daemon answered it, and a robot.subscribe that is "
    "METHOD_NOT_FOUND, a call every robotd from API_FLOOR up has, says it was not robotd.",
)
RUNTIME_DIR_ENV = UpstreamRef(
    "DUCK_RUNTIME_DIR",
    "VERIFIED",
    src(_PROTO, 608),
    "Moves runtime_root(), the directory each daemon publishes /run/<service>/identity.json under "
    "(lib.rs:596-617), and nothing else: 'Not a configuration knob for a robot — nothing on the "
    "board sets it', it exists for upstream's tests and for a daemon run by hand. robotd's socket "
    "is a literal --socket default, SOCKET_ROBOTD (robotd/src/main.rs:247), which it does not "
    "move, and scripts/duck-sim names each simulated robotd's socket itself. quackd does not read "
    "it: with no --address, it connects to SOCKET_ROBOTD.",
)

SOCKET_ROBOTD = UpstreamRef("/run/robotd.sock", "VERIFIED", src(_PROTO, 576))
SOCKET_CONFIGD = UpstreamRef("/run/configd.sock", "VERIFIED", src(_PROTO, 577))
SOCKET_UPDATERD = UpstreamRef("/run/updaterd.sock", "VERIFIED", src(_PROTO, 552))
SOCKET_PADD = UpstreamRef("/run/padd/pad.sock", "VERIFIED", src(_PROTO, 584), "pad.input only")
SOCKET_TOFD = UpstreamRef("/run/tofd/tof.sock", "VERIFIED", src(_PROTO, 589), "tof.stream only")
SOCKET_MEDIA = UpstreamRef(
    "/run/mediad/media.sock",
    "VERIFIED",
    src(_PROTO, 593),
    "mediad's own socket, for media.frame only, and local-only: 'a raw camera frame is for a "
    "recorder or perception process on the robot, not a multi-megabyte WebRTC control reply'.",
)

# ── methods we use ──────────────────────────────────────────────────────────────────────

HELLO = UpstreamRef(
    "hello",
    "VERIFIED",
    src(_PROTO, 669),
    "params {api_version}; result {api_version, daemon_version, revision}. The last two are "
    "always on the wire and may be null: revision is null 'for a build that did not come from "
    "CI'.",
)
ROBOT_MOVE = UpstreamRef(
    "robot.move",
    "VERIFIED",
    src(_PROTO, 721),
    "NOTIFICATION (no id). params {vx, vy, vyaw} m/s, rad/s, trunk frame; x fwd, y left, +vyaw left. "
    "Continuous: robotd's deadman zeroes velocity when these stop arriving.",
)
ROBOT_STOP = UpstreamRef(
    "robot.stop", "VERIFIED", src(_PROTO, 728), "request; zero velocity, NOT limp"
)
ROBOT_HEAD = UpstreamRef(
    "robot.head",
    "VERIFIED",
    src(_PROTO, 723),
    "NOTIFICATION {neck_pitch, head_pitch, head_yaw, head_roll} rad, HEAD_PARAMS, in joint "
    "space. robotd stores it as it arrives, from any client, in the one head slot robot.look "
    "writes too (robotd/src/main.rs:4767-4768), and robot.state.head reports that slot smoothed "
    "toward each new value a little every control tick (main.rs:3399-3400 and :4117). While the "
    "idle look-around runs it takes the head over: the slot's head is scaled down as the "
    "look-around fades in, and its glances are added (main.rs:3444-3445). quackd sends it for "
    "one thing, to hold a gaze: the head a robot.look answered with, sent again every "
    "GAZE_HOLD_S (IDLE_HEAD).",
)
ROBOT_LOOK = UpstreamRef(
    "robot.look",
    "VERIFIED",
    src(_PROTO, 726),
    "request {x, y, z, neck_pitch} trunk-frame point -> {head, clamped}. LookResult.head is "
    "'What was handed to the same path robot.head feeds — resend these to hold the gaze' "
    "(lib.rs:2476), which since daemon-v0.16.1 matters: see IDLE_HEAD.",
)
#: HeadParams: the four head joints robot.head takes and LookResult.head answers with, in the
#: order robot.state.head carries them (lib.rs:2449-2454). The type defaults a member it is not
#: sent to 0, and denies one it does not know.
HEAD_PARAMS = ("neck_pitch", "head_pitch", "head_yaw", "head_roll")
ROBOT_DO = UpstreamRef(
    "robot.do",
    "VERIFIED",
    src(_PROTO, 777),
    "request {skill} -> IntentResult {accepted, reason?}. Answered on accept/refuse, not on "
    "completion: 'a refusal names the scripted move already holding the robot'. From "
    "daemon-v0.18.8 it plays an emotion as well as a skill (SKILLS, MOVE_ECHO).",
)
ROBOT_POSE = UpstreamRef(
    "robot.pose",
    "VERIFIED",
    src(_PROTO, 782),
    "NOTIFICATION {z, roll, pitch, active} standing body pose offsets",
)
ROBOT_ENABLE = UpstreamRef(
    "robot.enable",
    "VERIFIED",
    src(_PROTO, 730),
    "request {on, toggle?} -> {accepted, reason?}; `toggle` is #[serde(default)], so sending "
    "{on} alone is valid. This is POLICY execution, not a flag: upstream says it 'can bring a "
    "limp robot up as a side effect of being asked to drive', so treat it as motion.",
)
ROBOT_INIT = UpstreamRef(
    "robot.init",
    "VERIFIED",
    src(_PROTO, 748),
    "power the joints and ramp to the home pose; MOVES EVERY JOINT. quackd never sends this.",
)
ROBOT_RELAX = UpstreamRef(
    "robot.relax",
    "VERIFIED",
    src(_PROTO, 754),
    "torque off — the robot collapses. quackd never sends this.",
)
ROBOT_SOUND = UpstreamRef(
    "robot.sound", "VERIFIED", src(_PROTO, 796), "request {tag, hold?}; tags only, no TTS"
)
ROBOT_SUBSCRIBE = UpstreamRef(
    "robot.subscribe",
    "VERIFIED",
    src(_PROTO, 966),
    "request {hz?} -> SubscribeResult, then robot.state notifications. State is NOT pushed "
    "until this is sent. The answer also carries what is constant for the process: "
    "{accepted, walk?, stand?, unavailable?, sitstand?, ground_pick?, skills[]}.",
)
ROBOT_STATE = UpstreamRef(
    "robot.state",
    "VERIFIED",
    src(_PROTO, 4260),
    "notification {t, move{requested,applied,limited_by}, head[4], policy, "
    "safety{fallen,limp,gravity,gain?,picked_up}, loop{hz,missed}, joints, targets, "
    "velocities?, currents_ma?, odom, theremin?, chorale?, t_ns, imu?, frames?, skeleton?}. "
    "head is the head robotd commands this tick: the slot robot.look and robot.head write, "
    "smoothed, and taken over by the idle look-around while it runs (ROBOT_HEAD).",
)
STATE_ODOM = UpstreamRef(
    "robot.state.odom",
    "VERIFIED",
    src(_PROTO, 4461),
    "{position: [x, y, z] in metres, z the trunk's height above the ground plane; yaw in "
    "radians} (lib.rs:4461-4466). 'The contact-odometry estimate: trunk pose in the world frame "
    "the IMU chose at boot. There is no magnetometer and no absolute reference', the frame being "
    "'wherever the robot was when it came up' (lib.rs:4456-4459). The field is `default` "
    "(lib.rs:4318-4322), so a robotd from before the estimator sends no key, which quackd reads "
    "as no odometry. quackd reads it as a pose whose frame lasts one connection and begins again "
    "when the duck is picked up, falls or rides a fall down (quackd.space.odometry).",
)
STATE_T_NS = UpstreamRef(
    "robot.state.t_ns",
    "VERIFIED",
    src(_PROTO, 4331),
    "CLOCK_MONOTONIC at the tick in nanoseconds, 'the same clock as TofFrame::t_ns, so a mapper "
    "can put a state sample and a depth frame on one axis'; zero from a daemon predating it, "
    "where 't is still the loop's own elapsed time' (lib.rs:4331-4335). quackd times odometry by "
    "it, and by t on a robotd that sends no t_ns.",
)
TOF_FOV = UpstreamRef(
    "the head's ToF sees 45 degrees per axis",
    "VERIFIED",
    src("kinematics/src/tof.rs", 34),
    "'The sensor's square field of view, degrees per axis' is 45 by 45, 'per ST's datasheet for "
    "both generations' (kinematics/src/tof.rs:32-34), an 8 by 8 grid of zones across it "
    "(tof.rs:28-29). "
    "The simulators' stand-in for the duck's map reveals what this field sees from each stop.",
)
TOF_FOV_DEG = 45.0
"""TOF_FOV, as a number."""
#: The order robot.state.joints is indexed in, JOINT_NAMES (lib.rs:628-644): the left leg, the
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
    src("robotd/src/main.rs", 4113),
    "The twist robotd was last asked for, as it was asked. robot.move stores [vx, vy, vyaw] "
    "untouched (robotd/src/main.rs:4764) and every state frame reports it as requested, beside "
    "applied, the twist the control loop hands its controller: the one asked for, or zero once the "
    "deadman finds it stale, smoothed toward each new value and held at zero through robotd's "
    "limp-fall sequence (main.rs:3385-3398, :3437-3438). robotd's own test holds requested to what "
    "was asked: 'what the client asked for must survive to the stream' (main.rs:7940), so the "
    "deadman does not zero it. robot.stop does, and is answered at once (main.rs:5752-5755, "
    "robotd/src/intents.rs:327-330). So does an emotion, new in daemon-v0.18.8: while robot.do "
    "plays one, robotd puts a zero twist in place of the one asked for on every tick of it, and "
    "requested reports that zero (robotd/src/emotion.rs:12-17 and :77-78, main.rs:2631-2635). "
    "robot.move is a notification, so robotd answers nothing when it cannot parse one, and this "
    "is the only place quackd can see whether a twist was taken. Once the state has gone a few "
    "frames without showing one it sent, counted over the walks of a verb up to each stop, "
    "quackd sends no robot.move until the verb that sent it ends. An emotion that plays while a "
    "verb walks, one a stock pad's Y or bumpers start among them (PAD_DRIVES), ends the walk "
    "that way, since nothing in the state says an emotion is playing, and the verb says robotd "
    "has not taken its twist. When the frame it quotes said a zero twist, the only one an "
    "emotion leaves, on a robotd that names daemon-v0.18.8 or later in its hello "
    "(EMOTIONS_SINCE), it names an emotion as one cause beside a twist robotd could not parse "
    "and another client's, and says robotd logs 'emotion started' as one begins (main.rs:2820). "
    "The API version cannot say which robotd that is: daemon-v0.18.6 and daemon-v0.18.7 report "
    "API 50 too, and play no emotion.",
)
ROBOT_HEALTH = UpstreamRef(
    "robot.health",
    "VERIFIED",
    src(_PROTO, 3940),
    "request -> {healthy, degraded?, reason?, battery{volts,percent}?, motors?, cpu_temp_c?, "
    "cpu_throttle?, control_loop?, bus, imu?}. quackd reads healthy, reason and battery. "
    "degraded is 'Only meaningful when healthy is false' (lib.rs:3942-3952), and from v48 a "
    "servo or the IMU board that goes quiet on a running robot is degraded, naming what went "
    "silent, where it was unhealthy (lib.rs:495-502). healthy is false either way, so quackd's "
    "heartbeat stops a verb on both, as it did.",
)
ROBOT_MODE = UpstreamRef("robot.mode", "VERIFIED", src(_PROTO, 833), "request -> {mode}")
ROBOT_SET_MODE = UpstreamRef(
    "robot.setMode", "VERIFIED", src(_PROTO, 841), "request {mode: 'walk'|'roller'}"
)
ROBOT_POLICIES = UpstreamRef(
    "robot.policies",
    "VERIFIED",
    src(_PROTO, 847),
    "request -> PoliciesResult {mode, enabled, slots, skills, homed?, sitting?}: what each "
    "policy slot runs, whether the duck has reached its home pose, and whether it is 'parked in "
    "its seat'. Documented, not called. The policy label on every robot.state frame already "
    "says both: robotd reports 'sit' while the seat latch holds the duck and 'homing' while the "
    "joints ramp home (POLICY_LABELS_DRIVEN, POLICY_LABELS_NOT_DRIVEN), so asking would cost a "
    "request per state read for nothing the label lacks. It is what to switch to if the labels "
    "ever stop saying it.",
)
TOF_STREAM = UpstreamRef(
    "tof.stream",
    "VERIFIED",
    src(_PROTO, 1061),
    "on tofd socket; then tof.frame notifications (8x8)",
)
TOF_FRAME = UpstreamRef("tof.frame", "VERIFIED", src(_PROTO, 1078))
PAD_INPUT = UpstreamRef(
    "pad.input",
    "VERIFIED",
    src(_PROTO, 1045),
    "The raw input stream of the pad padd drives from, which padd answers itself on SOCKET_PADD. "
    "It exists because 'padd polls the last known stick value and keeps sending it at 50 Hz', so "
    "a radio that stops delivering reports still looks like a live driver to robotd, and only "
    "this stream shows the gap (lib.rs:1033-1044). It is a tap and ranks nothing: what the pad "
    "asks for reaches robotd as robot.move and robot.do, the calls any client makes (PAD_DRIVES).",
)
PAD_DRIVES = UpstreamRef(
    "padd drives while a pad is connected",
    "VERIFIED",
    src("pad-map/src/continuous.rs", 22),
    "From the moment a gamepad connects ('pad connected — driving', padd/src/main.rs:444), padd "
    "sends robot.move on every tick its sticks ask for motion, fifty a second (lib.rs:1040-1041), "
    "and re-sends an unchanged stop every HEARTBEAT, 100 ms, while they do not: 'without it "
    "robotd's twist would age past the deadman while a pad sat connected and idle' "
    "(pad-map/src/continuous.rs:7-22, padd/src/main.rs:511-516). What a pad asks for is worked "
    "out in pad-map since daemon-v0.17.0, a crate netpadd runs too, which drives robotd from a "
    "pad over UDP in padd's place when [netpad] enabled says so (padd/src/main.rs:157-160, "
    "pad-map/src/loss.rs:8-11). robotd keeps one twist and ranks no client above another: "
    "robot.move sets it whoever sent it (robotd/src/main.rs:4761-4765), and a continuous intent "
    "is 'last-writer-wins' (lib.rs:710-715, robotd/src/intents.rs:7-10). So a pad does not "
    "out-rank quackd. While its sticks are still, its stops and quackd's re-sent twist land in "
    "turn, ten a second each, and robot.state.move.requested shows whichever came last "
    "(MOVE_ECHO). A stick that asks for motion is sent more often than quackd re-sends, so the "
    "pad's twist is the one robotd holds most of the time. The buttons are robot.do requests, "
    "and a pad nobody has configured runs sit_toggle on A, the ground pick on B and the emotions "
    "excited, yes and no on Y and the two bumpers (robotd-params/src/lib.rs:387-391 and "
    ":419-424). With the pad gone, padd sends nothing, and after PAD_LOST_SIT, 1 s, it asks "
    "robotd to sit a robot that is not sitting already, and asks again every 100 ms for 5 s more "
    "while robotd refuses (pad-map/src/loss.rs:17-28, padd/src/main.rs:388-427 and :543-550).",
)

SKILLS = UpstreamRef(
    "ground_pick | kick_left | kick_right | sit_toggle | roulade",
    "VERIFIED",
    src(_PROTO, 2655),
    "`pub type Skill = String`: 'A name, not an enumeration.' These five are still the skills a "
    "stock robot answers to, but a robot's skills are config now, so an unknown one is refused "
    "with the list it does know. From daemon-v0.18.8 robot.do plays an emotion too, a head, "
    "body and beak timeline with a sound, and a release ships thirteen: angry, curious, defiant, "
    "devastated, excited, impatient, laugh, mmh, mock, no, sad, yes and yes_fast "
    "(robotd/emotions/, robotd/src/emotion.rs:1-10, robotd/src/main.rs:5333-5346). quackd sends "
    "none of them. SKILL_SOURCE is where a client learns the real list.",
)
#: The release robot.do first plays an emotion in, daemon-v0.18.8 (SKILLS): robotd/src/emotion.rs
#: is not at daemon-v0.18.7, and daemon-v0.18.6 and daemon-v0.18.7 report API 50 as
#: daemon-v0.18.8 does, so it is the release a robotd names in hello, not its API version, that
#: says whether it can play one (MOVE_ECHO).
EMOTIONS_SINCE = (0, 18, 8)
SKILL_SOURCE = UpstreamRef(
    "robot.subscribe -> SubscribeResult.skills",
    "VERIFIED",
    src(_PROTO, 3284),
    "'The configurable one-shot skills this robot has, in priority order, and the names "
    "robot.do answers to. A list rather than a field per skill, because which skills a robot "
    "has is now config: a client learns them here instead of assuming five. The emotions in "
    "`[emotions] dir` come last.' (lib.rs:3284-3289). robotd lists the release's own emotions "
    "there as well as the ones in that directory (robotd/src/emotion.rs:7-10, "
    "robotd/src/main.rs:5732-5739).",
)
SOUND_TAGS = UpstreamRef(
    "alarm | greet | inquire | peck | chirp | coo | wheee",
    "VERIFIED",
    src(_PROTO, 2487),
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
    "by default (robotd-params/src/lib.rs:2444-2445 and :2536). Our walk verb re-sends robot.move "
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
NO_TWIST_CLAMP = UpstreamRef(
    "robotd clamps no twist",
    "VERIFIED",
    src("robotd/src/main.rs", 4327),
    "What limits a tick, robot.state's move.limited_by, is the deadman, a joint target past the "
    "actuator's travel, or one that is not finite, and nothing else (robotd/src/main.rs:"
    "4327-4333). While the walking policy drives, the twist robot.move last asked for reaches "
    "its observation as it was asked, smoothed toward each new value, until the deadman zeroes "
    "a stale one (main.rs:3388-3398, duck-control/src/obs.rs:210-215), an emotion plays "
    "(MOVE_ECHO), or a client holds the body-pose mode, robot.pose with active true, in which "
    "the controller zeroes it (robotd/src/control.rs:528-530 and :639-641). The controller "
    "zeroes its own copy (control.rs:626), so robot.state's move.requested and move.applied "
    "(main.rs:4113-4114) never show that zero. From daemon-v0.18.6 the smoothing can be held to "
    "an acceleration too, [policy] accel_vx, accel_vy and accel_vyaw, and only the roller's "
    "forward and back has one by default: 'walking never asked for a limit' (main.rs:1897-1910, "
    "robotd-params/src/lib.rs:2419-2426). padd sends a zero twist with every pose in its own "
    "body-pose mode (pad-map/src/mapper.rs:520-521), which move.requested does show, as it shows "
    "a pad's stops (PAD_DRIVES). And the policy 'only follows commands inside the range it was "
    "trained on — asking for more than that is asking, not getting' "
    "(robotd-params/src/lib.rs:254-255). [pad_drive], what a gamepad's full stick asks for, is "
    "'what the *pad* asks for, not a limit on the robot. A robot.move from anywhere else is not "
    "bounded by it' (lib.rs:253-254): 0.4 m/s forward and back, 0.3 m/s sideways and 1.0 rad/s "
    "of turn, since v8's velstand 'tracks yaw commands' and that is 'what that policy follows "
    "well on the robot' (lib.rs:274-284). It has been that since daemon-v0.16.2, and was 0.3 m/s "
    "forward and back and 1.5 rad/s of turn to daemon-v0.16.1. The Microduck's speed caps are "
    "quackd's own: its manifest's limits, 0.3 m/s forward, 0.2 m/s sideways and 1.0 rad/s of "
    "turn, the turn the pad asks for, which every verb that walks is clamped to "
    "(quackd.verbs.core.speed_limits).",
)
STATE_NEEDS_SUBSCRIBE = UpstreamRef(
    "robot.state is not pushed until robot.subscribe",
    "VERIFIED",
    src(_ROBOTD_DOC, 758),
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
    "in the trunk frame, debounced 0.2 s', and it 'gates nothing'. robotd's one answer to a fall "
    "is limp_fall, on by default at the pin as it has been since daemon-v0.16.2 "
    "(robotd-params/src/lib.rs:2539): robotd poses a duck it catches falling and hands it back "
    "to the policy (LIMP_FALL), while the verdict still gates nothing. With it off, as it was by "
    "default from daemon-v0.13.0 to daemon-v0.16.1 (VELSTAND says why), 'a fall changes "
    "nothing: the policy keeps driving and the humans stay in charge' (lib.rs:2460-2461). "
    "Refusing to walk a fallen robot is quackd's own decision, so quackd must actually be "
    "reading the frame to make it.",
)
LIMP_FALL = UpstreamRef(
    "robotd's limp-fall sequence",
    "VERIFIED",
    src("robotd/src/main.rs", 3219),
    "[safety] limp_fall, 'The only thing the daemon does about a fall' "
    "(robotd-params/src/lib.rs:2457). On by default at the pin (lib.rs:2539), as it has been "
    "since daemon-v0.16.2, and off by default at daemon-v0.16.1 (lib.rs:2048 there). "
    "daemon-v0.11.0 and daemon-v0.12.0 had it on by default too, handing the duck back to the "
    "standing network (lib.rs:1576 at daemon-v0.11.0, :1666 at daemon-v0.12.0), and "
    "daemon-v0.13.0 turned it off as velstand became the default walk (lib.rs:1771 there). On, "
    "robotd takes a duck off its policy once fresh readings from a ready IMU predict a fall "
    "while the walking or standing network drives it, unless the duck is already down, and "
    "never during a skill, a ground pick, a sit or the rise from one (robotd/src/main.rs:"
    "3249-3286, robotd/src/control.rs:376-386), so it catches no fall during quackd's kick or "
    "grab. It holds the twist at zero until it hands the duck back (main.rs:3385-3391). It lets "
    "the duck land limp, each tick labelled limp_fall with safety.limp true, until it has landed "
    "or limp_fall_max_ms, 1.5 s, has passed, then ramps it to the standing pose over "
    "limp_fall_pose_ms, 0.6 s, labelled limp_pose (main.rs:3288-3335, :3816-3837 and :4124, "
    "lib.rs:2547-2548). Then it hands the duck back to the policy, where 'with nobody driving "
    "the standing network is what command magnitude selects — which is the stand-up. A client "
    "that *is* driving gets its walk back' (main.rs:3327-3329). With no standing network loaded, "
    "as on a stock robot, 'the hand-back is to velstand at zero command, which stands still' "
    "(lib.rs:2453-2455), and that velstand gets the duck up from there is upstream's word on v8 "
    "(VELSTAND). It drops the sequence only when something else takes the robot: the policy "
    "disabled, the torque off or the joints ramping home, the shutdown sit, or the power off "
    "(main.rs:3232-3247). quackd sends no walk meanwhile: both "
    "labels and safety.limp read as not standing, as safety.fallen does, and a verb that was "
    "walking is held until it ends. stand_up says so of a duck robotd still has in the sequence "
    "when its wait for the duck to stand is over, ten seconds after its request. With these "
    "defaults the sequence is over within about 2.1 s, so that is a fall robotd caught in the "
    "wait's last two seconds or so, and a duck it had when stand_up was asked is back with the "
    "policy well before then. Whether the policy then gets the duck up is the policy's to say "
    "(VELSTAND), and nobody has seen it under quackd.",
)
#: The labels of the two halves of robotd's limp-fall sequence, the limp and the pose
#: (LIMP_FALL). robotd gives every tick of the sequence one of them, whether or not its fall
#: verdict says the duck is down, and safety.limp is true on the first half's ticks and no others.
LIMP_FALL_LABELS = ("limp_fall", "limp_pose")
SAFETY_PICKED_UP = UpstreamRef(
    "safety.picked_up",
    "VERIFIED",
    src(_PROTO, 4504),
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
    src(_PROTO, 4344),
    "{camera, tof, head_imu?}: where the camera and the ToF sensor are in the trunk frame at "
    "this tick's measured head joints, 'from the same head FK robot.look uses'. A pose is "
    "{pos} in metres and {quat} scalar-first [w, x, y, z], and camera is the OpenCV camera "
    "frame, '+x right, +y down, +z along the optical axis' (lib.rs:4363-4374). On the stream "
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
    src("robotd-params/src/lib.rs", 1097),
    "New in daemon-v0.16.1, the first release with it, on API 41; daemon-v0.16.0, on API 39, "
    "has none, and the commit that brought it to upstream's main was still on API 39. robotd "
    "counts the duck still once the look-around is on in the robot's mode, which by default it "
    "is when walking and is not on the roller (robotd-params/src/lib.rs:1031-1033 and "
    ":1138-1140), the policy has the robot, nothing asks it to move, nobody has commanded the "
    "head for IDLE_AFTER (2 s), and it is not falling, held, singing or running a skill "
    "(robotd/src/main.rs:3409-3422), and once it has counted it still for IDLE_AFTER more "
    "(robotd/src/idle_head.rs:118-125), so no sooner than twice IDLE_AFTER after the last head "
    "command, it takes the head over. 'It looks around the straight head, not wherever the head "
    "was last left': it eases the commanded head back to the middle as it fades in and hands it "
    "back as it fades out, both over FADE (1.5 s), 'so nothing jumps' (idle_head.rs:21-25, "
    "robotd/src/main.rs:3444-3445). Around the middle it glances up to 0.6 rad of yaw and 0.2 "
    "rad of pitch, with small shifts and a slow drift on top, leans the neck up to 0.15 rad, now "
    "and then tilts the head 0.3 rad, and once it has been idle 45 s now and then yawns, the head "
    "tipping back (robotd-params/src/lib.rs:1097-1125). IDLE_AFTER and FADE are [idle] after_s "
    "and fade_s from daemon-v0.17.3, read here at their defaults, and the head is taken over "
    "rather than added to from daemon-v0.17.9. A robot.look or robot.head from anyone resets the "
    "wait, 'so a client steering the head is never fought' (robotd/src/intents.rs:253-254), and "
    "only for as long as it keeps steering. So quackd holds a gaze: after a robot.look it sends "
    "LookResult.head again as robot.head every GAZE_HOLD_S, half of IDLE_AFTER, until a stop, "
    "the next look, another client steering the head, a link that fails or the end of the "
    "connection. And go_to and search_scan wait FADE after centring the head, since a glance "
    "that was under way when the look arrived takes that long to fade out, and on mediad's "
    "/frame they steer only on a frame taken after the wait (MEDIA_FRAME_ON_REQUEST). No call "
    "reports [idle]'s after_s, fade_s or enabled, so quackd takes the defaults read here for "
    "every robotd from IDLE_HEAD_SINCE_API up, in whichever mode. A robot retuned to glance "
    "sooner or to fade for longer is not one quackd can see: its gaze hold can then let go, "
    "taking a glance for another client's head, and go_to and search_scan can steer on a frame "
    "a glance is still fading out of. On one with the look-around off, slower to start or "
    "quicker to fade, quackd only waits longer than it needs, so a robot quackd drives should "
    "keep after_s at IDLE_AFTER or more and fade_s at FADE or less, or set enabled false.",
)
#: The idle look-around in numbers (IDLE_HEAD): robotd counts the duck still only once nobody
#: has commanded the head for IDLE_AFTER (robotd/src/main.rs:3420), starts the look-around once
#: it has counted it still for IDLE_AFTER more (robotd/src/idle_head.rs:118-125), and fades it
#: in or out over FADE (:127). Both are [idle] settings, after_s and fade_s, and these are their
#: defaults (robotd-params/src/lib.rs:1097-1098). No call reports either, so quackd takes these
#: for every robot's, a retuned one's included (IDLE_HEAD says what that costs).
IDLE_AFTER_S = 2.0
IDLE_FADE_S = 1.5
#: The API version the first release with the idle look-around reports, daemon-v0.16.1's. A
#: robotd on an older one is not waited for. A build of upstream's main from between
#: daemon-v0.16.0 and daemon-v0.16.1 can glance on a lower number, but a robot runs a release.
IDLE_HEAD_SINCE_API = 41
VELSTAND = UpstreamRef(
    "velstand.onnx",
    "VERIFIED",
    src("robotd-params/src/lib.rs", 2336),
    "The walk a stock robotd loads at the pin: 'The velstand gait (set v5) walks on a twist and "
    "stands still at zero command, so no standing network is loaded by default', and "
    "alpha_walking.onnx and alpha_stand.onnx 'stay in the set for a board that loads them back "
    "by hand' (robotd-params/src/lib.rs:2331-2334). Its subscribe answer names it in `walk`. "
    "limp_fall's stand-up hands the duck to the standing network, which is why limp_fall was "
    "'Off by default since the velstand gait became the default walk' from daemon-v0.13.0 to "
    "daemon-v0.16.1 (lib.rs:1971 at daemon-v0.16.1). It is on from daemon-v0.16.2, and with no "
    "standing network loaded 'the hand-back is to velstand at zero command, which stands still' "
    "(lib.rs:2453-2455; LIMP_FALL). robotd seeds a robot with the policy set v8, as every "
    "release has since daemon-v0.16.2, where daemon-v0.16.1 seeded v7 (scripts/"
    "seed-policies.sh:51 at each), and moves a board on an older official set up to it on "
    "update. v8 changes no policy but velstand.onnx, and its manifest says that 'From v8 it gets "
    "up from a fall smoothly and reliably instead of violently or getting stuck on its back, and "
    "yaw commands are tracked (turning in place and while walking now reaches the commanded "
    "rate)' (POLICY_SET in sim3d/upstream_api.py). That is upstream's word on its model, and "
    "nobody has seen it on a duck under quackd. quackd's physics simulator still runs "
    "alpha_walking and alpha_stand.",
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
    "different gaits both report walk' (robotd-design.md:767-768). An emotion reports no label "
    "of its own: the policy goes on driving under it (robotd/src/emotion.rs:12-17).",
)
POLICY_LABELS_NOT_DRIVEN = UpstreamRef(
    "held, homing, rest, limp_fall, limp_pose",
    "VERIFIED",
    src("robotd/src/main.rs", 3807),
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
#: limp_pose are the two halves of the limp-fall sequence (robotd/src/main.rs:3825 and :3836),
#: which robotd's still excludes (main.rs:3416), and ground_pick, rise and skill are a scripted
#: move under way, which the controller counts as busy (robotd/src/control.rs:382-386) and still
#: excludes too (main.rs:3422), as it does a configured skill reporting its own name, and that
#: name with UNWIND_SUFFIX. held is not one of them: robotd also labels held a tick on which its
#: policy failed while it went on driving (main.rs:3873). A frame whose safety.picked_up or
#: safety.limp is true comes from a tick robotd did not count still on either (main.rs:4124-4127).
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
    "running.' (mediad/src/web.rs:102-136). The port is 8080 on every address by default "
    "(mediad/src/main.rs:32 and :79), with no authentication. New since bc41fb5, where the port "
    "served the console page alone: it first shipped in daemon-v0.13.0. From daemon-v0.17.3 "
    "mediad counts every PNG it serves as a photo taken, which it adds to the robot's vet book "
    "once a minute (mediad/src/web.rs:119-122, mediad/src/vetbook.rs:1-5 and :20-23), so each "
    "frame quackd pulls is one more photo in a book its owner can publish. When --camera-url's "
    "path is MEDIAD_FRAME_PATH, quackd pulls it at a rate of its own, lower than any other "
    "camera's, waits through a 503 at connect, takes the lens from the first frame's shape "
    "(ALPHA_UPRIGHT_FOV, BETA_UPRIGHT_FOV), and records whether the route has answered as "
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
    "captured_at at 'the moment the buffer existed' (pipeline.rs:1158-1160). GET /frame asks "
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
    src(_PROTO, 675),
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
    "it pushes when the channel opens (mediad/src/session.rs:35-54 and :160-192). The push can be "
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
    src("robotd-params/src/board.rs", 100),
    "camera_mount_degrees is 90 on the Zero 3W, the alpha board, whose 'IMX219 sits a quarter "
    "turn off', and 0 on the beta, whose 'GC2093 sits the right way up', and on the beta's main "
    "board with its V1.0 face board, a board new since daemon-v0.16.1, and mediad tells every "
    "consumer that angle as media.video's rotate (robotd-params/src/board.rs:95-105). Nothing on "
    "the robot turns the pixels, 'Nothing converts and nothing rotates' "
    "(mediad/src/pipeline.rs:28), since a videoflip cost the encoder its zero-copy path, so the "
    "WebRTC track off an alpha is sideways and the consumer turns it. Upstream's uyvy crate, "
    "which mediad turns its PNGs and its duck detector's input with, turns a frame clockwise by "
    "rotate: 'A quarter turn clockwise sends source (x, y) to upright (h-1-y, x)' "
    "(uyvy/src/lib.rs:56-80). quackd's WebRTC camera turns each frame the same way. A mediad "
    "started with --flip-in-pipeline turns the pixels in its pipeline as well and still reports "
    "the mount as rotate and the size the camera takes as media.video's width and height "
    "(mediad/src/main.rs:571-608 and :628-629), so a frame that comes in tall where those "
    "describe a wide one is one the pipeline turned, and quackd hands it out as it came rather "
    "than turn it onto its side again.",
)
ALPHA_INTRINSICS = UpstreamRef(
    "the alpha family's camera calibration",
    "VERIFIED",
    src("robotd-params/src/lib.rs", 859),
    "CameraIntrinsics::alpha(), 'the head camera module and M12 lens every alpha unit carries — "
    "one part across the family, so one solve is every unit's calibration', solved on a 1280 by "
    "720 frame 'as mediad sends it (unrotated)' (robotd-params/src/lib.rs:848-875), its numbers "
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
    src("mediad/src/sensor.rs", 120),
    "hfov_deg of the IMX219, the alpha robots' head camera behind its M12 lens, the 'Horizontal "
    "field of view across a frame in [Sensor::mode], degrees', that mode being 1920 by 1080 "
    "(mediad/src/sensor.rs:38-41 and :101-122). It is the same at every size mediad streams "
    "at, since each is the sensor's whole field scaled: 'the field of view is what stays fixed "
    "as the ISP scales the frame to whatever [media] quality asks for' (mediad/src/camera.rs:"
    "16-18). The beta's GC2093 had none at daemon-v0.16.1, since 'nobody has measured this lens "
    "yet', and from daemon-v0.18.0 it has a solve of its own, CameraIntrinsics::beta, '~85° "
    "across the 16:9 frame', its numbers BETA_SOLVE (robotd-params/src/lib.rs:830-846), and an "
    "hfov_deg of 85.2 (sensor.rs:131-132 and :152-153), which mediad sends as media.video's "
    "intrinsics. IMX219_FOV_DEG is the IMX219's number, and GC2093_FOV_DEG the GC2093's.",
)
#: The IMX219's field of view across its frame's long side, in degrees (IMX219_FOV).
IMX219_FOV_DEG = 62.0
#: The GC2093's field of view across its 16:9 frame's width, its long side too, in degrees:
#: mediad's hfov_deg, 2*atan(640/fx) on BETA_SOLVE (IMX219_FOV, BETA_UPRIGHT_FOV).
GC2093_FOV_DEG = 85.2
#: CameraIntrinsics::beta() (IMX219_FOV): the frame it was solved on, its focal lengths and its
#: principal point, in pixels of that frame as mediad sends it, which on a beta is upright
#: (robotd-params/src/lib.rs:830-846).
BETA_SOLVE = {
    "width": 1280,
    "height": 720,
    "fx": 695.6216231288809,
    "fy": 696.0209167299262,
    "cx": 627.4740538073111,
    "cy": 363.60566030048255,
}
#: The same solve's distortion, k1, k2, p1, p2 and k3 in OpenCV's order
#: (robotd-params/src/lib.rs:838-844), which quackd's detectors do not correct for
#: (BETA_UPRIGHT_FOV).
BETA_DISTORTION = (
    -0.368607360841971,
    0.13550162561831905,
    0.00012682873881955374,
    0.0008719998338708391,
    -0.022368269599378002,
)
FEATURE_STREAM = UpstreamRef(
    "media.detections",
    "VERIFIED",
    src("mediad/src/main.rs", 690),
    "mediad's duck detector, pushed as a notification {width, height, took_ms, "
    "boxes[{x0,y0,x1,y1,score}]} (mediad/src/detect.rs:332) on each WebRTC peer's control "
    "datachannel and nowhere else: 'Detections go to the peer as notifications, on the same "
    "channel the console already reads robot.state from'. It finds ducks, not balls. Off by "
    "default, and two looks a second when on: '2 Hz is a thermal number, not a taste' "
    "(robotd-params/src/lib.rs:912-938, [duck_detector]). quackd's WebRTC camera keeps the "
    "newest one and steers on none of them.",
)

# ── how a program reaches a duck from somewhere else ────────────────────────────────────

RENDEZVOUS_LANE = UpstreamRef(
    "rendezvous control lane",
    "VERIFIED",
    src("mediad/src/relay.rs", 44),
    "What upstream sends a server-side agent over: JSON-RPC over HTTP/SSE through upstream's "
    "rendezvous service, which mediad's relay bridges to the robot's own sockets. Upstream names "
    "two limits on it. "
    "'No pixels': 'Video over this would be base64 inside JSON at the rate below', so frames "
    "come from WebRTC or media.stream (docs/design/remote-access-design.md:608-609). And 'What "
    "it is not is a teleop lane': 'The rendezvous allows 1200 requests per 60 s per peer', and "
    "'exceeding it earns a `429` on the *whole peer*, which would take the robot's own lease "
    "down with it'. A walk under quackd re-sends its twist against robotd's deadman until it "
    "ends, a joystick's rate rather than a behaviour's, so quackd does not drive over it, and a "
    "robot elsewhere is reached over ssh with microduck:jsonrpc.",
)

# ── assumptions and things upstream has designed but not shipped ────────────────────────

STAND_UP_RPC = UpstreamRef(
    "stand_up asks robotd's policy to drive (robot.enable), which upstream does not promise "
    "gets a fallen duck up",
    "UNVERIFIED",
    src("robotd-params/src/lib.rs", 2453),
    "No such RPC. robot.enable {on: true} is policy execution, which 'can bring a limp robot up "
    "as a side effect of being asked to drive', and robot.init, the call upstream names for "
    "bringing up a duck that is on its feet, moves every joint and is never sent. On a robot "
    "whose policy is enabled already, as it is when the duck fell while the policy drove it, "
    "robot.enable {on: true} only sets the flag again (robotd/src/main.rs:5760-5769, "
    "robotd/src/intents.rs:494-496), so what stand_up learns comes from reading the duck as it "
    "waits, every quarter second for up to ten seconds. robotd's own stand-up after a fall is "
    "the end of limp_fall, on by default at the pin as it has been since daemon-v0.16.2 "
    "(robotd-params/src/lib.rs:2539): robotd hands a duck it caught falling back to velstand "
    "once it has posed it (LIMP_FALL), which upstream says gets up from a fall from v8 on "
    "(VELSTAND). On daemon-v0.16.1 it is off, and 'With it off, a fall changes nothing: the "
    "policy keeps driving and the humans stay in charge' (lib.rs:2460-2461). Whether velstand "
    "gets a duck off the floor has not been seen under quackd on either release. A duck robotd "
    "still has in that sequence when stand_up's wait is over is told so, and to run stand_up "
    "again in a moment. The sequence is over within about 2.1 s by default, so that is a fall "
    "robotd caught late in the wait, and a duck it had when stand_up was asked is judged like "
    "any other: upright as soon as it reads standing, and told it may need a hand only if it is "
    "still down when the wait is over.",
)
ALPHA_UPRIGHT_FOV = UpstreamRef(
    "a tall /frame is assumed to be an alpha's IMX219, 62 degrees from top to bottom",
    "UNVERIFIED",
    src("mediad/src/sensor.rs", 120),
    "quackd's own reading of mediad's GET /frame (MEDIAD_FRAME_HTTP), whose PNG carries no lens. "
    "On a stock robot only an alpha's camera gives a picture taller than wide: its IMX219 is "
    "mounted a quarter turn off upright (CAMERA_MOUNT) and turned upright for the PNG, so the 62 "
    "degrees along its long side (IMX219_FOV) run from the picture's top to its bottom, and "
    "across its width it sees 2*atan(tan(31 degrees) * w/h), 37.3 degrees at 720 by 1280. That "
    "is the module's design, with the principal point at the centre, and not a calibration: the "
    "alpha family's own solve gives a little more (ALPHA_INTRINSICS). It rests on the shape "
    "alone. A beta's camera sits upright and gives a 16:9 picture wider than tall, whose lens is "
    "BETA_UPRIGHT_FOV's, and a Zero 3W fitted with a GC2093 through [media] sensor gives a tall "
    "one, which is taken for an alpha's and is wrong there. And upstream's scripts/duck-sim "
    "renders its twin's camera "
    "rolled like an alpha's, at MuJoCo's 45 "
    "degrees across the frame as sent (mediad/src/main.rs:246-249, mediad/src/camera.rs:236-256), "
    "so a tall /frame from a twin is 45 degrees wide and this is wrong there. The first frame of "
    "a working /frame camera, when taller than wide, sets the manifest's camera_fov_deg from it "
    "and names this assumption beside it, so detections through it say they are uncalibrated "
    "and the log says to pass --fov-deg, which overrides it on quackd run, and every state lists "
    "it in extras.assumptions while it is in use.",
)
BETA_UPRIGHT_FOV = UpstreamRef(
    "a 16:9 /frame wider than tall is assumed to be a beta's GC2093, measured through its "
    "solve's focal length, 85.2 degrees across",
    "UNVERIFIED",
    src("mediad/src/sensor.rs", 152),
    "quackd's own reading of mediad's GET /frame (MEDIAD_FRAME_HTTP), whose PNG carries no lens, "
    "beside ALPHA_UPRIGHT_FOV. On a stock robot only a beta's camera gives a picture wider than "
    "tall. Its GC2093 sits upright (CAMERA_MOUNT), so every frame media.frame hands the route "
    "carries a rotate of 0 (mediad/src/main.rs:250-252 and :478, mediad/src/frame.rs:215-222), "
    "and the PNG is that frame unturned and scaled by nothing (mediad/src/snapshot.rs:83-99, "
    "uyvy/src/lib.rs:180-185). The frame is the size [media] quality names, 1280 by 720 by "
    "default and 16:9 at every size (robotd-params/src/lib.rs:476-478 and :522-530), scaled by "
    "the ISP from the sensor's only mode, 1920 by 1080 (mediad/src/sensor.rs:126, "
    "mediad/src/pipeline.rs:1662-1665). The beta's solve was made on the 1280 by 720 frame as "
    "mediad sends it, which upstream calls a scaled readout of the sensor's only mode, 'so the "
    "same field at any size `mediad` streams' (robotd-params/src/lib.rs:826-827), its numbers "
    "BETA_SOLVE, and mediad's hfov_deg of 85.2 is 2*atan(640/fx) on it (sensor.rs:151-152): the "
    "field the solve's focal length gives across the frame's width, the same at any size. So a "
    "first frame that is 16:9 and wider than tall is taken for a beta's and measured through "
    "85.2 degrees, and a wide frame of any other shape gets no lens. That is the solve's focal "
    "length alone. Its principal point sits about 12 pixels left of the centre of a 1280 by 720 "
    "frame, which alone puts a bearing quackd's detectors take from the centre about 1 degree "
    "left of the one from the optical axis at the middle of the picture, and about half that at "
    "its edges. Its distortion (BETA_DISTORTION), which they do not correct for either, draws a "
    "thing in toward the middle the further out it is, so with both a bearing reads short of "
    "the solve's by about 1 to 3 degrees half way from the centre to an edge, 6 to 8 three "
    "quarters of the way and 12 to 14 near the edges, the more on the right, where the two add. "
    "It rests on the shape alone: an alpha whose mediad was started with --rotate 0, or a board "
    "fitted with another camera through [media] sensor, can give a 16:9 picture wider than tall "
    "too, and so does a 16:9 camera of your own served at /frame, which quackd reads as "
    "mediad's, and upstream's scripts/duck-sim twin with --rotate 0 or a declared beta board, "
    "whose frame is then not turned and is MuJoCo's 45 degrees from top to bottom, 72.7 across "
    "(mediad/src/main.rs:246-252, mediad/src/camera.rs:236-256), and this is wrong for each of "
    "them. mediad's own test pattern, which a beta board with no camera serves at 256 by 144 "
    "(robotd-params/src/lib.rs:740-742), is 16:9 too and is taken for a beta's. The first frame "
    "of a working /frame camera, when 16:9 and wider than tall, sets the manifest's "
    "camera_fov_deg to GC2093_FOV_DEG and names this assumption beside it, so detections through "
    "it say they are uncalibrated and the log says to pass --fov-deg, which overrides it on "
    "quackd run, and every state lists it in extras.assumptions while it is in use. Until "
    "upstream's solve was read, a wide frame got no lens at all.",
)


def all_refs() -> list[UpstreamRef]:
    return [v for v in globals().values() if isinstance(v, UpstreamRef)]


def refs_by_status(status: Status) -> list[UpstreamRef]:
    return [r for r in all_refs() if r.status == status]
