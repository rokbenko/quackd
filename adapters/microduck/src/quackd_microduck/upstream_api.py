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
daemon-v0.12.0 (API 27) on at connect.

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
once it is still and nobody has steered its head for two seconds (IDLE_HEAD), and a pick-up
detector pauses the policy while somebody holds it (SAFETY_PICKED_UP).

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
    "duck-ipc-proto API_VERSION at the pin. It was 16 on 2026-08-28 and 23 at the previous pin, "
    "on 2026-09-04. No robotd refuses a peer for a different number ('No daemon refuses a call "
    "because this number differs', lib.rs:57), but quackd's handshake refuses any number but "
    "this one rather than guessing, so this number is the difference between a session and a "
    "closed socket.",
)
FRAMING = UpstreamRef(
    "NDJSON: one JSON-RPC 2.0 object per line",
    "VERIFIED",
    src(_ARCH_DOC, 190),
    "'Wire format: JSON-RPC 2.0, one object per line (NDJSON).'",
)
RUNTIME_DIR_ENV = UpstreamRef("DUCK_RUNTIME_DIR", "VERIFIED", src(_PROTO, 532), "overrides /run")

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
    "NOTIFICATION {neck_pitch, head_pitch, head_yaw, head_roll} rad",
)
ROBOT_LOOK = UpstreamRef(
    "robot.look",
    "VERIFIED",
    src(_PROTO, 652),
    "request {x, y, z, neck_pitch} trunk-frame point -> {head, clamped}. LookResult.head is "
    "'What was handed to the same path robot.head feeds — resend these to hold the gaze' "
    "(lib.rs:2233), which since daemon-v0.16.1 matters: see IDLE_HEAD.",
)
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
    "head is the commanded head, the idle look-around's glances included.",
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
    "pad.input", "VERIFIED", src(_PROTO, 920), "gamepad raw tap; the pad is the authority, not us"
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
    "'if intents stop arriving, the velocity goes to zero. Stop is not limp'. Our walk verb "
    "re-sends robot.move at 10 Hz.",
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
    "reads as fallen, and nothing that moves the duck is sent while it is true.",
)
STATE_FRAMES = UpstreamRef(
    "RobotState.frames",
    "VERIFIED",
    src(_PROTO, 3944),
    "{camera, tof, head_imu?}: where the camera and the ToF sensor are in the trunk frame at "
    "this tick's measured head joints, 'from the same head FK robot.look uses'. A pose is "
    "{pos} in metres and {quat} scalar-first [w, x, y, z], and camera is the OpenCV camera "
    "frame, '+x right, +y down, +z along the optical axis' (lib.rs:3963-3974). On the stream "
    "since API 24. quackd does not read it.",
)
IDLE_HEAD = UpstreamRef(
    "robotd's idle look-around",
    "VERIFIED",
    src("robotd/src/idle_head.rs", 26),
    "New in daemon-v0.16.1. Once the policy has the robot, nothing asks it to move, nobody has "
    "commanded the head for IDLE_AFTER (2 s), and it is not falling, held, singing or running "
    "a skill (robotd/src/main.rs:3071-3078), robotd glances around on top of the commanded "
    "head, up to 0.6 rad of yaw and 0.2 rad of pitch, fading in and out over FADE (1.5 s). A "
    "robot.look or robot.head from anyone resets the wait, 'so a client steering the head is "
    "never fought' (robotd/src/intents.rs:247-248), and only for as long as it keeps steering. "
    "quackd's gaze sends one robot.look and does not send it again.",
)
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

# ── where a frame comes from ────────────────────────────────────────────────────────────
#
# robotd's socket carries no picture. At the pin mediad hands one out three ways: its WebRTC
# video track, GET /frame on its console port, and media.frame on its local socket. quackd's
# `--camera-url` can reach the first two. What its duck detector finds rides each WebRTC peer's
# control channel, beside the track.

MEDIAD_FRAME_HTTP = UpstreamRef(
    "GET :8080/frame",
    "VERIFIED",
    src("mediad/src/web.rs", 97),
    "mediad's console port answers GET /frame with one upright PNG per request, uncached "
    "(Cache-Control no-store) and at most four at a time, or with 503 'Camera snapshot "
    "unavailable; retry when capture is running.' The port is 8080 on every address by default "
    "(mediad/src/main.rs:32 and :79), with no authentication. New since bc41fb5, where the port "
    "served the console page alone.",
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


def all_refs() -> list[UpstreamRef]:
    return [v for v in globals().values() if isinstance(v, UpstreamRef)]


def refs_by_status(status: Status) -> list[UpstreamRef]:
    return [r for r in all_refs() if r.status == status]
