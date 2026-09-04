"""The only file in quackd allowed to spell an upstream Microduck method name.

Every constant is tagged VERIFIED (read from upstream source, link given) or UNVERIFIED
(designed upstream but not shipped, or an assumption of ours). `docs/transport-status.md`
is the human-readable version of this file; `tests/test_upstream_api.py` proves that
UNVERIFIED names are only reachable from the experimental and stub transports.

Pinned and read on 2026-09-04, at a commit rather than `main` (ADR-0022). The previous read
was 2026-08-28 against `main` with no pin, and upstream moved seven API versions in a week:
`API_VERSION` went 16 -> 23 and `Skill` stopped being an enum. A moving link cannot show that
happening, which is the whole reason ADR-0022 asks for a hash.

What changed between the two reads, for the names quackd actually uses: the version number,
`Skill` (now a free string, with the robot's own list in `robot.subscribe`'s answer), and
three additive fields (`RobotState.theremin`, `RobotState.chorale`, `HealthResult.cpu_temp_c`).
Everything else below — sockets, framing, the intent vocabulary, the state frame, the error
codes, the sound tags — read identically at v23.

Nothing here has been run against a physical Microduck.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Status = Literal["VERIFIED", "UNVERIFIED"]

REPO = "https://github.com/pollen-robotics/microduck"
PIN = "bc41fb5c9a9b39894669c1e022e375cf83800382"  # main, 2026-09-03
READ_ON = "2026-09-04"


def src(path: str, line: int | None = None) -> str:
    return f"{REPO}/blob/{PIN}/{path}" + (f"#L{line}" if line else "")


_PROTO = "duck-ipc-proto/src/lib.rs"

IPC_PROTO = src(_PROTO)
ARCH_DOC = src("docs/design/architecture.md")
ROBOTD_DOC = src("docs/design/robotd-design.md")
ROADMAP = src("docs/project/roadmap.md")
WEBRTC_DOC = src("docs/design/remote-webrtc.md")


@dataclass(frozen=True)
class UpstreamRef:
    """A named upstream thing and how sure we are that it exists as described."""

    name: str
    status: Status
    source: str
    note: str = ""

    def __str__(self) -> str:
        return self.name


# ── protocol ────────────────────────────────────────────────────────────────────────────

JSONRPC_VERSION = "2.0"
API_VERSION = UpstreamRef(
    "23",
    "VERIFIED",
    src(_PROTO, 276),
    "duck-ipc-proto API_VERSION at the pin. Was 16 on 2026-08-28; the handshake refuses on "
    "mismatch rather than guessing, so this number is the difference between a session and a "
    "closed socket.",
)
FRAMING = UpstreamRef("NDJSON: one JSON-RPC 2.0 object per line", "VERIFIED", ARCH_DOC)
RUNTIME_DIR_ENV = UpstreamRef("DUCK_RUNTIME_DIR", "VERIFIED", src(_PROTO, 348), "overrides /run")

SOCKET_ROBOTD = UpstreamRef("/run/robotd.sock", "VERIFIED", src(_PROTO, 320))
SOCKET_CONFIGD = UpstreamRef("/run/configd.sock", "VERIFIED", src(_PROTO, 321))
SOCKET_UPDATERD = UpstreamRef("/run/updaterd.sock", "VERIFIED", src(_PROTO, 309))
SOCKET_PADD = UpstreamRef("/run/padd/pad.sock", "VERIFIED", src(_PROTO, 328), "pad.input only")
SOCKET_TOFD = UpstreamRef("/run/tofd/tof.sock", "VERIFIED", src(_PROTO, 333), "tof.stream only")

# ── methods we use ──────────────────────────────────────────────────────────────────────

HELLO = UpstreamRef(
    "hello",
    "VERIFIED",
    src(_PROTO, 389),
    "params {api_version}; result {api_version, daemon_version?, revision?}",
)
ROBOT_MOVE = UpstreamRef(
    "robot.move",
    "VERIFIED",
    src(_PROTO, 437),
    "NOTIFICATION (no id). params {vx, vy, vyaw} m/s, rad/s, trunk frame; x fwd, y left, +vyaw left. "
    "Continuous: robotd's deadman zeroes velocity when these stop arriving.",
)
ROBOT_STOP = UpstreamRef(
    "robot.stop", "VERIFIED", src(_PROTO, 444), "request; zero velocity, NOT limp"
)
ROBOT_HEAD = UpstreamRef(
    "robot.head",
    "VERIFIED",
    src(_PROTO, 439),
    "NOTIFICATION {neck_pitch, head_pitch, head_yaw, head_roll} rad",
)
ROBOT_LOOK = UpstreamRef(
    "robot.look",
    "VERIFIED",
    src(_PROTO, 442),
    "request {x, y, z, neck_pitch} trunk-frame point -> {head, clamped}",
)
ROBOT_DO = UpstreamRef(
    "robot.do",
    "VERIFIED",
    src(_PROTO, 481),
    "request {skill} -> IntentResult {accepted, reason?}. Answered on accept/refuse, not on "
    "completion: 'a refusal names the scripted move already holding the robot'.",
)
ROBOT_POSE = UpstreamRef(
    "robot.pose",
    "VERIFIED",
    src(_PROTO, 486),
    "NOTIFICATION {z, roll, pitch, active} standing body pose offsets",
)
ROBOT_ENABLE = UpstreamRef(
    "robot.enable",
    "VERIFIED",
    src(_PROTO, 446),
    "request {on, toggle?} -> {accepted, reason?}; `toggle` is #[serde(default)], so sending "
    "{on} alone is valid. This is POLICY execution, not a flag: upstream says it 'can bring a "
    "limp robot up as a side effect of being asked to drive', so treat it as motion.",
)
ROBOT_INIT = UpstreamRef(
    "robot.init",
    "VERIFIED",
    src(_PROTO, 464),
    "power the joints and ramp to the home pose; MOVES EVERY JOINT. quackd never sends this.",
)
ROBOT_RELAX = UpstreamRef(
    "robot.relax",
    "VERIFIED",
    src(_PROTO, 470),
    "torque off — the robot collapses. quackd never sends this.",
)
ROBOT_SOUND = UpstreamRef(
    "robot.sound", "VERIFIED", src(_PROTO, 500), "request {tag, hold?}; tags only, no TTS"
)
ROBOT_SUBSCRIBE = UpstreamRef(
    "robot.subscribe",
    "VERIFIED",
    src(_PROTO, 602),
    "request {hz?} -> SubscribeResult, then robot.state notifications. State is NOT pushed "
    "until this is sent. The answer also carries what is constant for the process: "
    "{accepted, walk?, stand?, unavailable?, sitstand?, ground_pick?, skills[]}.",
)
ROBOT_STATE = UpstreamRef(
    "robot.state",
    "VERIFIED",
    src(_PROTO, 3226),
    "notification {t, move{requested,applied,limited_by}, head[4], policy, "
    "safety{fallen,limp,gravity,gain?}, loop{hz,missed}, joints, targets, odom, "
    "theremin?, chorale?}",
)
ROBOT_HEALTH = UpstreamRef(
    "robot.health",
    "VERIFIED",
    src(_PROTO, 2998),
    "request -> {healthy, degraded?, reason?, battery{volts,percent}?, motors?, cpu_temp_c?}",
)
ROBOT_MODE = UpstreamRef("robot.mode", "VERIFIED", src(_PROTO, 534), "request -> {mode}")
ROBOT_SET_MODE = UpstreamRef(
    "robot.setMode", "VERIFIED", src(_PROTO, 542), "request {mode: 'walk'|'roller'}"
)
TOF_STREAM = UpstreamRef(
    "tof.stream", "VERIFIED", src(_PROTO, 695), "on tofd socket; then tof.frame notifications (8x8)"
)
TOF_FRAME = UpstreamRef("tof.frame", "VERIFIED", src(_PROTO, 712))
PAD_INPUT = UpstreamRef(
    "pad.input", "VERIFIED", src(_PROTO, 679), "gamepad raw tap; the pad is the authority, not us"
)

SKILLS = UpstreamRef(
    "ground_pick | kick_left | kick_right | sit_toggle | roulade",
    "VERIFIED",
    src(_PROTO, 2031),
    "`pub type Skill = String` — 'A name, not an enumeration.' These five are still the names a "
    "stock robot answers to, but a robot's skills are config now, so an unknown one is refused "
    "with the list it does know. SKILL_SOURCE is where a client learns the real list.",
)
SKILL_SOURCE = UpstreamRef(
    "robot.subscribe -> SubscribeResult.skills",
    "VERIFIED",
    src(_PROTO, 2454),
    "'The configurable one-shot skills this robot has, in priority order, and the names "
    "robot.do answers to. A list rather than a field per skill, because which skills a robot "
    "has is now config: a client learns them here instead of assuming five.'",
)
SOUND_TAGS = UpstreamRef(
    "alarm | greet | inquire | peck | chirp | coo | wheee",
    "VERIFIED",
    src(_PROTO, 1903),
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
    ROBOTD_DOC,
    "if intents stop arriving the velocity goes to zero; 'stop is not limp'. Our walk verb re-sends robot.move at 10 Hz.",
)
STATE_NEEDS_SUBSCRIBE = UpstreamRef(
    "robot.state is not pushed until robot.subscribe",
    "VERIFIED",
    ROBOTD_DOC,
    "'robot.subscribe turns a connection into a stream; the loop publishes into a bounded "
    "broadcast and never waits on a subscriber, so a slow client gets a gap rather than "
    "applying backpressure.' A connection that never subscribes learns nothing about the "
    "robot, which is why the transport subscribes during connect().",
)
FALLEN_IS_A_REPORT = UpstreamRef(
    "safety.fallen gates nothing upstream",
    "VERIFIED",
    ROBOTD_DOC,
    "'computed every tick — projected gravity in the trunk frame, debounced 0.2 s', and it is "
    "'a report, not a rule'. robotd recovers from falls itself; refusing to walk a fallen "
    "robot is quackd's own decision, so quackd must actually be reading the frame to make it.",
)

# ── assumptions and things upstream has designed but not shipped ────────────────────────

POSTURE_FROM_POLICY = UpstreamRef(
    "robot.state.policy == 'sit' means sitting",
    "UNVERIFIED",
    ROBOTD_DOC,
    "The state frame names the policy that drove the tick ('walk', 'stand', 'held'), and the "
    "chain is 'roulade > kick > ground pick > sit/rise > stand > walk'. We assume a sitting "
    "robot reports something containing 'sit'. Upstream also notes two releases with different "
    "gaits 'both report walk', so the name is a policy, not a posture — which is why a policy "
    "we cannot read must read as unknown rather than as standing.",
)
WEBSOCKET_GATEWAY = UpstreamRef(
    "WebSocket agent gateway",
    "UNVERIFIED",
    ARCH_DOC,
    "architecture.md §5.3 designs 'open a WebSocket, poll a frame, send intents'. Roadmap M5 (2026-08-26): in progress, not shipped.",
)
GET_FRAME = UpstreamRef(
    "get_frame",
    "UNVERIFIED",
    ARCH_DOC,
    "§5.3: 'get_frame -> JPEG on demand, or 1-2 fps push'. Method name and params not in duck-ipc-proto yet.",
)
CAMERA_SNAPSHOT = UpstreamRef(
    "camera snapshot over a unix socket",
    "UNVERIFIED",
    WEBRTC_DOC,
    "Today the camera reaches clients only through mediad's WebRTC track. No socket-level frame method exists.",
)
FEATURE_STREAM = UpstreamRef(
    "mediad media.detections notifications",
    "UNVERIFIED",
    WEBRTC_DOC,
    "No longer 'designed, not built': mediad/src/detect.rs emits media.detections "
    "{width, height, took_ms, boxes[{x0,y0,x1,y1,score}]} at about 2 Hz, RKNN on the NPU or "
    "ONNX on CPU, and it detects DUCKS rather than balls. UNVERIFIED because it is broadcast "
    "to WebRTC signalling clients, and remote-webrtc.md still says perception consumes pixels "
    "locally rather than over the control datachannel — source and design doc disagree, and "
    "robotd's socket cannot reach it either way. Unreachable from jsonrpc; quackd's Detector "
    "protocol remains the stand-in.",
)
STAND_UP_RPC = UpstreamRef(
    "stand_up",
    "UNVERIFIED",
    ROBOTD_DOC,
    "No such RPC. robotd recovers from falls itself (limp -> settle -> ramp -> standing policy); "
    "the limp_fall detector fires at about 26 degrees and hands back a standing robot. Our "
    "stand_up verb sends robot.enable {on: true}, which upstream says can bring a limp robot up "
    "as a side effect of being asked to drive — close to what we want, but it is not a "
    "stand-up call and robot.init, the one that is, moves every joint and is never sent.",
)


def all_refs() -> list[UpstreamRef]:
    return [v for v in globals().values() if isinstance(v, UpstreamRef)]


def refs_by_status(status: Status) -> list[UpstreamRef]:
    return [r for r in all_refs() if r.status == status]
