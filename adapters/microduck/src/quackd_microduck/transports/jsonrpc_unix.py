"""EXPERIMENTAL: the real robot, over robotd's JSON-RPC socket.

Every method name here is VERIFIED against upstream's `duck-ipc-proto` at a pinned release
(see `upstream_api.py`), but nobody has run this against a Microduck. What is honest today:
the handshake, which refuses a robotd older than the oldest version quackd has read and warns
about any version but the one it read, the intent vocabulary, the deadman-friendly `move`
notifications and robotd's own echo of them, the state subscription, the health poll, and
posture, which is read off the policy label against the labels robotd's own source names and
fails closed on every other. What is not: everything about the camera.

These rules run through this file, and each exists because the robot is on a floor and quackd
is at the other end of an ssh tunnel:

- **Silence is not consent.** A state frame that stops arriving, or arrives without a
  `safety` block, reads as `unknown` rather than as a standing duck, because `fallen: false`
  from a stream nobody is watching is not a fact about the robot. The preconditions refuse
  on `unknown`, so a link that dies stops the duck instead of freeing it.
- **Only a label quackd has read stands the duck up.** `robot.state.policy` names whatever
  drove the tick, and robotd reports far more labels than `walk` and `stand`: a duck in
  somebody's hands, one riding a fall down, one halfway through a kick. Each of them reads as
  `unknown`, with `extras.posture_reason` saying which, and so does any label a newer robotd
  adds, so a vocabulary that grows refuses walks instead of allowing them.
- **Once the duck stops standing, the walk stays stopped.** No `robot.move` goes out unless
  the newest state reads standing, and once one is held back none goes out until `stop()`,
  which every verb that walks sends on its way out. The preconditions are read once, when a
  verb starts, and `go_to` sends its next twist whatever became of the last one, so without
  the hold a duck picked up halfway through a `go_to` would walk off the moment it was put
  down.
- **A version quackd has not read is served, and says so.** No robotd refuses a peer for a
  different API version, and a call whose shape moved refuses itself by name, so quackd
  serves any robotd from `up.API_FLOOR` up, warns whenever its version is not the one read,
  and names the call and both versions when one is refused (`up.VERSION_SKEW`, ADR-0051).
- **A notification nobody answered is not a walk.** robotd answers no `robot.move`, so one it
  could not parse used to be a walk reported and never taken. Its own state says what it was
  last asked for (`up.MOVE_ECHO`), and a twist that state has not shown a few frames later
  holds `robot.move` back the way a duck that stops standing does.
- **A gaze is held until something says to let go, and never against anyone.** Since
  daemon-v0.16.1 robotd glances around on its own once nobody has steered the head for two
  seconds (`up.IDLE_HEAD`), so a `robot.look` no longer keeps the camera where it pointed it.
  The head a look answered with goes out again every `GAZE_HOLD_S`, as upstream says to, and
  nothing goes out while the duck is not standing or sitting. The hold ends on a stop, on the
  next look, on a link that fails, and on `robot.state.head` showing that something else is
  steering the head, which quackd lets have it rather than pulling it back every second.
- **A frame kept is not a frame seen.** `get_frame` expires what it caches. `go_to` steers
  on every tick, so a camera that died with the ball in shot would otherwise show the ball
  in shot forever while the duck walked at it.

Addresses: `unix:///run/robotd.sock` (on the robot, POSIX only) or `tcp://host:port`
(e.g. after `ssh -L 9870:/run/robotd.sock robot`, which also works from Windows).
Frames: `--camera-url http://.../snapshot.jpg`, or `webrtc://host:8443` for `mediad`'s own
video track. robotd's socket carries no picture.
"""

from __future__ import annotations

import asyncio
import contextlib
import io
import json
import logging
import math
import sys
import time
import urllib.parse
import urllib.request
from collections import deque
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from typing import Any, NamedTuple

from PIL import Image

from quackd.transport.base import (
    Ack,
    DuckState,
    HeartbeatError,
    Intent,
    Posture,
    TransportError,
)
from quackd_microduck import upstream_api as up
from quackd_microduck.webrtc import WebRtcCamera, is_webrtc_url

STATUS = "EXPERIMENTAL — verified method names, unverified against hardware"

log = logging.getLogger("quackd.transport.jsonrpc")

#: How often we ask robotd for a state frame. Ten a second is the rate the walk verb already
#: feeds intents at, and upstream decimates per-subscriber server-side, so asking for less than
#: the loop's 50 Hz costs the robot proportionally less (`up.ROBOT_SUBSCRIBE`).
STATE_HZ = 10


def state_stale_after_s(hz: float) -> float:
    """How old a state frame may be before `get_state` stops believing it.

    Derived from the rate actually subscribed at, not from the default: a transport built with
    `state_hz=2` gets a 2.5 s window, where a fixed 0.3 s one would have declared every healthy
    duck blind half a second after each frame and refused every verb that moves it, with a
    message saying the stream was dead while it streamed exactly as asked.

    Five frame periods, floored at one second so link jitter on an ssh tunnel does not blank
    the posture (`up.STATE_NEEDS_SUBSCRIBE`).
    """
    return max(1.0, 5.0 / max(0.1, hz))


#: The window at the default rate, for tests and for anything reading the module constant.
STATE_STALE_AFTER_S = state_stale_after_s(STATE_HZ)

#: The longest line the reader takes from robotd, a mebibyte. asyncio's default is 64 KiB,
#: which is also the most robotd takes in a line it reads (`MAX_LINE` in robotd/src/main.rs),
#: but nothing bounds what robotd writes, and `robot.state` gained the joints' velocities and
#: currents, an IMU, the camera's and the ToF sensor's poses and the whole skeleton between API
#: 24 and 36. A frame is a few kilobytes at the pin. Over the default, `readline` raised out of
#: the pump, which died without a word, and every request after it waited out its timeout on a
#: socket nobody read. Sixteen times robotd's own limit leaves room for what a newer robotd
#: adds, and `_read_loop` skips a line longer still instead of ending the link on it.
READER_LIMIT = 2**20

#: How many state frames robotd gets to show a twist before quackd decides it has not taken it.
#: robotd stores a `robot.move` as it arrives and reports it in the next frame it publishes
#: (`up.MOVE_ECHO`), at most one frame period later, so three periods leave two for the twist
#: to reach the robot and the frame to come back. A link slower than that has a walk robotd
#: did take held back, which stops the duck and says why.
#:
#: Counted as frames read and as time, and the verdict waits for both. On time alone, a frame
#: robotd built before the twist reached it, still in the socket when quackd's own process
#: stalled and read the moment it woke, arrives "three periods later" and reads as a twist
#: robotd did not take. On frames alone, three read in one burst say nothing about time.
MOVE_ECHO_FRAMES = 3


def move_echo_after_s(hz: float) -> float:
    """How long after a twist went out a state frame may arrive still not showing it, with
    `MOVE_ECHO_FRAMES` read since, before `robot.move` is held back as a call robotd has not
    taken.

    `MOVE_ECHO_FRAMES` frame periods at the rate actually subscribed at, for the reason
    `state_stale_after_s` is: a transport built with a slower stream waits proportionally
    longer, rather than calling every twist lost before a frame could have shown it."""
    return MOVE_ECHO_FRAMES / max(0.1, hz)


Twist = tuple[float, float, float]
"""`[vx, vy, vyaw]`, the order `robot.move`'s params and `move.requested` both use."""

#: robotd reports the very float it parsed, and JSON carries a float's shortest round-trip form
#: both ways, so a twist it took reads back equal. The tolerance only spares a reader that
#: prints a number differently; it is not a measurement of anything.
_ECHO_TOLERANCE = 1e-9


def _twist_of(move: Any) -> Twist | None:
    """`move.requested` from a state frame, or None when it is not three numbers."""
    asked = move.get("requested") if isinstance(move, dict) else None
    if not isinstance(asked, list) or len(asked) != 3:
        return None
    if not all(isinstance(v, int | float) and not isinstance(v, bool) for v in asked):
        return None
    return (float(asked[0]), float(asked[1]), float(asked[2]))


def _number(value: Any) -> float:
    """A twist component as quackd sent it, or NaN for one that is not a number. robotd drops a
    `robot.move` it cannot parse, and NaN matches nothing a frame shows, so the echo still
    judges an intent built by hand with a member robotd cannot read."""
    if isinstance(value, int | float) and not isinstance(value, bool):
        return float(value)
    return math.nan


def _same_twist(a: Twist, b: Twist) -> bool:
    return all(
        math.isclose(x, y, rel_tol=_ECHO_TOLERANCE, abs_tol=_ECHO_TOLERANCE)
        for x, y in zip(a, b, strict=True)
    )


def _shown(values: Sequence[float]) -> str:
    return "[" + ", ".join(f"{v:g}" for v in values) + "]"


#: How often a held gaze goes out again (`_hold_gaze`): half of robotd's IDLE_AFTER, the wait
#: since the last head command after which it starts to glance (`up.IDLE_HEAD`), so a re-send
#: that a tunnel holds up for as long again as a whole period still lands before robotd decides
#: that nobody is steering the head.
GAZE_HOLD_S = up.IDLE_AFTER_S / 2

Head = tuple[float, float, float, float]
"""`[neck_pitch, head_pitch, head_yaw, head_roll]` in radians: `up.HEAD_PARAMS`, in the order
`robot.state.head` carries them too."""


def _finite(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool) and math.isfinite(value)


def _head_of(value: Any) -> Head | None:
    """`robot.state.head` as four finite numbers, or None when it is anything else."""
    if not isinstance(value, list) or len(value) != len(up.HEAD_PARAMS):
        return None
    if not all(_finite(v) for v in value):
        return None
    neck_pitch, head_pitch, head_yaw, head_roll = (float(v) for v in value)
    return (neck_pitch, head_pitch, head_yaw, head_roll)


def _look_head(result: Any) -> Head | None:
    """The head a `robot.look` answered with, as the four joints `robot.head` takes, or None
    unless `LookResult.head` is exactly those four, each a finite number.

    Exactly, because the hold sends it back as it came. `HeadParams` sets a member it is not sent
    to 0, so a fifth joint a newer robotd added would be zeroed by every re-send of the four quackd
    knows, and a missing or unreadable one would have to be guessed. No hold beats either."""
    head = result.get("head") if isinstance(result, dict) else None
    if not isinstance(head, dict) or set(head) != set(up.HEAD_PARAMS):
        return None
    return _head_of([head[name] for name in up.HEAD_PARAMS])


def _farthest(a: Head, b: Head) -> float:
    """How far apart two heads are on the joint where they differ most, in radians."""
    return max(abs(x - y) for x, y in zip(a, b, strict=True))


def _optical_axis(frames: Any) -> tuple[float, float, float] | None:
    """Where `frames.camera` looks, as a unit vector in the trunk frame (x forward, y left, z up):
    its scalar-first quaternion applied to the OpenCV camera's +z, which is the optical axis
    (`up.STATE_FRAMES`). None when there is no such pose or it is not a rotation."""
    camera = frames.get("camera") if isinstance(frames, dict) else None
    quat = camera.get("quat") if isinstance(camera, dict) else None
    if not isinstance(quat, list) or len(quat) != 4 or not all(_finite(v) for v in quat):
        return None
    norm = math.sqrt(sum(float(v) ** 2 for v in quat))
    if norm == 0.0:
        return None
    w, x, y, z = (float(v) / norm for v in quat)
    return (2 * (x * z + w * y), 2 * (y * z - w * x), 1 - 2 * (x * x + y * y))


def _head_pose(state: dict[str, Any]) -> tuple[float | None, float | None, str | None]:
    """Where the camera points from the trunk, in degrees of yaw, positive left, and of pitch,
    positive up, and what said so: `frames.camera`, or on a robotd that reports no frames, the
    measured head_yaw joint, which gives a yaw and no pitch (`up.HEAD_YAW_SIGN`).

    The simulators have always published `head_yaw_deg` this way, and the real duck never did.
    A flock member adds it to a camera bearing to get one from the body, so on a real Microduck
    it took the camera for looking straight ahead wherever the head had turned."""
    axis = _optical_axis(state.get("frames"))
    if axis is not None:
        vx, vy, vz = axis
        yaw = math.degrees(math.atan2(vy, vx))
        pitch = math.degrees(math.atan2(vz, math.hypot(vx, vy)))
        return round(yaw, 1), round(pitch, 1), "frames.camera"
    joints = state.get("joints")
    at = up.JOINT_NAMES.index("head_yaw")
    if isinstance(joints, list) and len(joints) == len(up.JOINT_NAMES) and _finite(joints[at]):
        return round(math.degrees(float(joints[at])), 1), None, "joints"
    return None, None, None


@dataclass
class _GazeHold:
    """A gaze quackd keeps on the head: the joints a `robot.look` answered with, which
    `_hold_gaze` sends again as `robot.head`."""

    head: Head
    since: float
    """When the head was last sent this target after a gap: by the look, or by the first re-send
    after a pause. robotd fades a glance out from about then (`head_fade_s`), so
    `robot.state.head` is not judged on frames read before that and a hold period more."""
    paused: str | None = None
    """Why nothing goes out just now, or None while it does."""

    def params(self) -> dict[str, float]:
        return dict(zip(up.HEAD_PARAMS, self.head, strict=True))


#: How often to pull a snapshot when `--camera-url` names one. `go_to` and `search_scan` steer
#: at 10 Hz, so this is the rate a control decision can be stale by: keep it well above the
#: pilot's roughly-once-a-second glance, and below what an ssh tunnel will carry.
CAMERA_FPS = 5.0


def camera_stale_after_s(fps: float) -> float:
    """How old a frame may be before `get_frame` stops handing it out.

    A cached frame that never expires is worse than no frame at all: `go_to` steers on every
    tick, and a camera that died with the ball in shot would show the ball in shot forever
    while the duck walked at it. Four frame periods, floored at two seconds so a slow-but-
    working camera is not dropped, and floored again by the fetch timeout so one slow HTTP
    round trip cannot look like a dead camera.
    """
    return max(2.0, 4.0 / max(0.1, fps))


def posture_of(
    policy: str | None, safety: dict[str, Any], *, skills: Sequence[str] = ()
) -> tuple[Posture, str | None]:
    """Where the duck is, from one fresh state frame, and why it is `unknown` when it is.

    An allowlist, because the label names whatever drove the tick and robotd has more labels
    than quackd will ever have read: two stand the duck up, one seats it, and every other one
    reads as `unknown` with a reason naming it. It used to run the other way, with `sit` matched
    as a substring and every other label read as standing, so a duck in somebody's hands
    (`held`), one riding a fall down (`limp_fall`), one halfway through a kick, and whatever
    label a newer robot added could each be sent a walk.

    The order is the safety order. Somebody holding the duck comes first, before `fallen`: a
    duck held at an angle reads as fallen, and "fallen" points the pilot at `stand_up`, which
    asks the policy to drive legs that are in a person's hands. `fallen` comes before the label,
    because a duck on the floor is down whatever network is driving it, and on a robot whose
    `limp_fall` is off the walk network is still what drives one lying there. `safety.limp`,
    the gains dropped so the duck yields, is next, and then the label.
    """
    if safety.get("picked_up"):
        return "unknown", "somebody is holding the duck (safety.picked_up)"
    if safety.get("fallen"):
        return "fallen", None
    if safety.get("limp"):
        return "unknown", "robotd has dropped the gains so the duck yields (safety.limp)"
    # A skill reports its own name, and robotd keeps only `ground_pick` and `sit_toggle` back
    # from the names a policy set may give one, so a set can call a skill `walk`. The robot's
    # own list is read before the allowlist, or a duck halfway through that skill would stand.
    if policy and policy in skills:
        return "unknown", _not_standing_because(policy, skills)
    if policy in up.SITTING_LABELS:
        return "sitting", None
    if policy in up.STANDING_LABELS:
        return "standing", None
    return "unknown", _not_standing_because(policy, skills)


def _not_standing_because(policy: str | None, skills: Sequence[str]) -> str:
    """Why a label that neither stands nor seats the duck leaves its posture unknown."""
    if not policy:
        return "robotd's state frame names no policy"
    said = f"robotd reports policy {policy!r}"
    if policy in skills:
        if policy in up.STANDING_LABELS or policy in up.SITTING_LABELS:
            return f"{said}, which is also one of its skills, so quackd cannot tell which it is"
        return f"{said} (one of its skills, which holds the legs until it ends)"
    if policy in up.NOT_STANDING_LABELS:
        return f"{said} ({up.NOT_STANDING_LABELS[policy]})"
    if policy.endswith(up.UNWIND_SUFFIX):
        return f"{said} (a skill unwinding back to the gait)"
    return f"{said}, which quackd has not read, so it cannot tell where the duck is"


class StateRead(NamedTuple):
    """The newest state frame, read once for both `get_state` and the hold on `robot.move`, so
    what the pilot is told about the duck and what the wire is allowed cannot disagree."""

    state: dict[str, Any]
    age: float | None
    fresh: bool
    """A frame recent enough to believe, carrying a `safety` block that says `fallen`."""
    policy: str
    fallen: bool
    picked_up: bool | None
    """None when no fresh frame says either way, since a stream nobody is watching cannot say
    that the duck is on the floor."""
    posture: Posture
    reason: str | None
    """Why `posture` is `unknown`, or None when it is not."""


def _pickup_detection(read: StateRead) -> bool | None:
    """Whether a fresh frame's `safety` block carries `picked_up`, or None with no fresh frame.

    Its presence and not its value: robotd writes the key whenever it has the detector, and one
    from before it leaves the key out, which a reader takes for never picked up
    (`up.SAFETY_PICKED_UP`). That reading is exactly what quackd must not take on trust, since
    a duck in somebody's hands is then sent walks like any other."""
    safety = read.state.get("safety") if read.fresh else None
    return ("picked_up" in safety) if isinstance(safety, dict) else None


def default_address() -> str:
    """Where robotd listens unless it was told otherwise: its own `--socket` default.

    It used to be `$DUCK_RUNTIME_DIR/robotd.sock` whenever that variable was set, read on the
    machine running quackd. Upstream's variable moves only the directories its daemons publish
    their `identity.json` in, for its own tests and for a daemon run by hand
    (`up.RUNTIME_DIR_ENV`), and robotd's socket is a literal default that no variable moves
    (`up.SOCKET_ROBOTD`), so with the variable set the default named a socket no robotd ever
    creates. A robotd somewhere else, as each duck in upstream's `scripts/duck-sim` is, under a
    name of its own, is reached with `--address`, like any other.
    """
    return f"unix://{up.SOCKET_ROBOTD.name}"


def parse_address(address: str) -> tuple[str, str, int | None]:
    if address.startswith("unix://"):
        return "unix", address[len("unix://") :], None
    if address.startswith("tcp://"):
        host, _, port = address[len("tcp://") :].rpartition(":")
        if not host or not port.isdigit():
            raise TransportError(f"bad tcp address {address!r}; expected tcp://host:port")
        return "tcp", host, int(port)
    if address.startswith("/"):
        return "unix", address, None
    raise TransportError(f"unknown address {address!r}; use unix:///path or tcp://host:port")


class JsonRpcUnixTransport:
    name = "jsonrpc"

    def __init__(
        self,
        address: str | None = None,
        *,
        camera_url: str | None = None,
        api_version: int = int(up.API_VERSION.name),
        request_timeout_s: float = 2.0,
        state_hz: int = STATE_HZ,
        camera_fps: float = CAMERA_FPS,
        gaze_hold_s: float = GAZE_HOLD_S,
    ) -> None:
        self.address = address or default_address()
        self.camera_url = camera_url
        self.api_version = api_version
        self.request_timeout_s = request_timeout_s
        self.state_hz = state_hz
        self.camera_fps = camera_fps
        self.gaze_hold_s = gaze_hold_s
        self.head_fade_s = 0.0
        """How long robotd takes to fade an idle glance out once a look arrives: `up.IDLE_FADE_S`
        on a robotd that glances, from `up.IDLE_HEAD_SINCE_API`, and 0 on one that does not, set
        at the handshake. `go_to` and `search_scan` read it with `getattr` and wait it out after
        centring the head, so that they never steer on a head still swinging back."""
        self._gaze: _GazeHold | None = None
        """The gaze `_hold_gaze` is keeping on the head, or None while there is none."""
        self._gaze_task: asyncio.Task[None] | None = None
        self.gaze_released: str | None = None
        """Why the last gaze hold ended, or None while one runs or before any has."""
        self._previous_state: dict[str, Any] | None = None
        self._previous_state_at: float | None = None
        """The state frame read before the newest, and when, which `_head_taken` compares it
        with."""
        self._camera_task: asyncio.Task[None] | None = None
        self._webrtc: WebRtcCamera | None = None
        self._frame: Image.Image | None = None
        self._frame_at: float | None = None
        self._frame_error: str | None = None
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._pump: asyncio.Task[None] | None = None
        self._pending: dict[int, asyncio.Future[Any]] = {}
        self._notifications: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=256)
        self._next_id = 1
        self._last_state: dict[str, Any] | None = None
        self._last_state_at: float | None = None
        self._state_arrived = asyncio.Event()
        self._last_health: dict[str, Any] | None = None
        self._t0 = time.monotonic()
        self.hello: dict[str, Any] | None = None
        self.stop_error: str | None = None
        """Why the last `stop` did not reach the robot, or None if it did (or none was sent)."""
        self._move_held: str | None = None
        """What made quackd hold `robot.move` back until the next `stop()`, or None while it is
        not: the duck stopped standing, or robotd stopped showing the twists it was sent
        (`_move_refusal`). Worded to follow "since"."""
        self._unshown: deque[tuple[float, int, Twist]] = deque(maxlen=256)
        """Every twist sent since the newest one a state frame has shown, oldest first, each
        with when it went out and how many frames had been read by then (`_frames_read`).
        Emptied by a stop, which ends the verb that sent them. Bounded only so that a stream
        that never echoes cannot grow it: the hold empties it long before, once
        `MOVE_ECHO_FRAMES` frames and `move_echo_after_s` have passed."""
        self._frames_read = 0
        """How many `robot.state` frames this connection has read, for `_not_taken`."""
        self.move_echo: dict[str, Any] | None = None
        """What robotd's own state last said about the twists quackd sent (`up.MOVE_ECHO`):
        `taken` True with the twist it showed and how long after it went out the frame that
        showed it arrived, or False with what it showed instead when `robot.move` was held
        back for it. None until a frame has said either."""
        self.robotd: dict[str, Any] | None = None
        """Which robotd answered `hello`, beside what quackd read, as `extras.robotd`."""
        self.runtime_warning: str | None = None
        """Why this robotd's API version is worth a word, or None when it is the one quackd
        read. Logged at connect, carried in `extras.robotd`, and printed by `doctor`, which
        reads it off any transport under this name."""
        self.camera_working = False
        """Whether a frame actually arrived during connect. What the manifest narrows on."""
        self.subscribed: dict[str, Any] | None = None
        """`robot.subscribe`'s answer: the policies and the skill names this robot actually has."""

    # ── wire ────────────────────────────────────────────────────────────────────────

    async def connect(self) -> None:
        kind, host, port = parse_address(self.address)
        try:
            if kind == "unix":
                if sys.platform == "win32":
                    raise TransportError(
                        "unix sockets are not available on Windows; forward the robot's socket "
                        "with `ssh -L 9870:/run/robotd.sock <robot>` and use --address tcp://127.0.0.1:9870"
                    )
                self._reader, self._writer = await asyncio.open_unix_connection(
                    host, limit=READER_LIMIT
                )
            else:
                self._reader, self._writer = await asyncio.open_connection(
                    host, port, limit=READER_LIMIT
                )
        except OSError as e:
            raise TransportError(f"cannot connect to {self.address}: {e}") from e
        self._pump = asyncio.create_task(self._read_loop(), name="quackd-jsonrpc-pump")
        # Past this point a task and a socket are open, so every exit has to close them. The
        # callers retry (`mcp_server`, `flock.member`), so a leak here is one task and one fd
        # per attempt, not one per process.
        try:
            await self._handshake()
        except BaseException:
            await self.close()
            raise

    async def _handshake(self) -> None:
        result = await self.request(up.HELLO.name, {"api_version": self.api_version})
        self.hello = result if isinstance(result, dict) else {"result": result}
        remote = self.hello.get("api_version")
        floor = int(up.API_FLOOR.name)
        # The number decides only whether quackd has read anything this robot speaks. Above the
        # floor a difference is served, as upstream's own clients serve one, because what
        # refuses a changed call is the call, by name (up.VERSION_SKEW). The exact match this
        # replaced refused every robotd release from daemon-v0.12.0 on, six days after the read.
        if not isinstance(remote, int) or isinstance(remote, bool):
            raise TransportError(
                f"robotd's hello carried api_version {remote!r}, which is not a version number, "
                f"so whatever answered at {self.address} is not a robotd quackd can check against "
                f"the API v{self.api_version} it read; refusing"
            )
        if remote < floor:
            raise TransportError(
                f"robotd speaks API v{remote}, older than API v{floor}, the oldest quackd has read "
                f"({up.IPC_PROTO}); refusing. Update the robot to a release on API v{floor} or "
                f"later: quackd read {up.RELEASE}, on API v{self.api_version}"
            )
        if remote != self.api_version:
            self.runtime_warning = self._skew_warning(remote)
            log.warning("%s", self.runtime_warning)
        # A robotd that glances around on its own takes a moment to let go of a glance once a
        # look arrives, and the verbs that steer on the camera wait that out (`up.IDLE_HEAD`).
        self.head_fade_s = up.IDLE_FADE_S if remote >= up.IDLE_HEAD_SINCE_API else 0.0
        self.robotd = {
            "api_version": remote,
            "read": self.api_version,
            "floor": floor,
            "daemon_version": self.hello.get("daemon_version"),
            "revision": self.hello.get("revision"),
            "warning": self.runtime_warning,
        }
        # Nothing arrives until we ask (up.STATE_NEEDS_SUBSCRIBE), and everything that decides
        # whether the duck may walk reads the frames this starts: posture, `safety.fallen`, the
        # preconditions in the manifest. Subscribing here rather than in `subscribe()` is what
        # makes those true for every caller instead of only for one that happens to iterate the
        # stream. The answer also names the robot's real skills and policies.
        result = await self.request(up.ROBOT_SUBSCRIBE.name, {"hz": self.state_hz})
        self.subscribed = result if isinstance(result, dict) else {}
        await self._await_first_state()
        # `camera_working` is what the manifest narrows on, and it has to mean "a frame
        # arrived", not "a URL was typed": a typo'd host, a missing extra or a mediad that
        # never reached PLAYING would otherwise still advertise observe, go_to, search_scan
        # and approach_and, every one of which can then only answer "no camera".
        if is_webrtc_url(self.camera_url):
            # The only camera upstream actually offers. Nothing is installed on the robot.
            self._webrtc = WebRtcCamera(str(self.camera_url))
            self.camera_working = await self._webrtc.start()
        elif self.camera_url:
            self._camera_task = asyncio.create_task(
                self._camera_loop(), name="quackd-jsonrpc-camera"
            )
            self.camera_working = await self._await_first_frame()

    def _skew_warning(self, remote: int) -> str:
        """What to tell the person running quackd about a robotd on an API version it did not
        read, newer or older, with what quackd does about the difference.

        Older is warned about as well as newer. The floor is the oldest version quackd has read,
        not every version between it and the pin, and the number is a poor guide to what a
        robot has: daemon-v0.15.0 to daemon-v0.15.2 all say API 37 and only the last reports
        `safety.picked_up` (`up.SAFETY_PICKED_UP`)."""
        build = (self.hello or {}).get("daemon_version")
        which = f"API v{remote}" + (f" (daemon {build})" if build else "")
        read = f"the API v{self.api_version} quackd read at {up.RELEASE} on {up.READ_ON}"
        if remote > self.api_version:
            return (
                f"this robotd speaks {which}, newer than {read}. It is served, as upstream's own "
                "clients serve one, because a call whose shape moved refuses itself by name: a "
                "verb that fails naming METHOD_NOT_FOUND or INVALID_PARAMS is that, and a "
                "robot.move the robot's state does not show is held back (extras.move_echo). "
                "Before it walks, read the robot's own duck-ipc-proto version notes, above "
                "API_VERSION in its lib.rs, for anything touching the calls quackd makes."
            )
        return (
            f"this robotd speaks {which}, older than {read}. quackd serves every version from "
            f"API v{int(up.API_FLOOR.name)}, the oldest it has read end to end, but has not read "
            "this one: a call it lacks fails the verb naming the call, and what it does not "
            "report reads as absent, safety.picked_up among them (extras.pickup_detection)."
        )

    async def _await_first_frame(self, timeout_s: float = 5.0) -> bool:
        """Wait for the snapshot loop's first frame, so `connect()` knows if there is a camera."""
        deadline = time.monotonic() + timeout_s
        while self._frame is None and self._frame_error is None:
            if time.monotonic() >= deadline:
                break
            await asyncio.sleep(0.02)
        return self._frame is not None

    async def _await_first_state(self, timeout_s: float = 1.0) -> bool:
        """Wait briefly for the first state frame, so `connect()` returns a duck we can see.

        The subscribe *reply* and the first state *notification* are two messages and nothing
        orders them, so without this the first `get_state()` after connecting can honestly
        report `unknown` on a robot that is about to start streaming. Not fatal if it never
        comes: `get_state` already reads that as unknown and the preconditions refuse, which is
        the outcome we want anyway. Returning the answer lets `doctor` say which happened.
        """
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self._state_arrived.wait(), timeout=timeout_s)
        return self._last_state_at is not None

    async def close(self) -> None:
        # First, while the socket is still open: a hold that woke after the writer had gone would
        # end on its own, with a warning about a link that nobody broke.
        if (gaze := self._end_gaze("the connection was closed")) is not None:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await gaze
        if self._webrtc is not None:
            await self._webrtc.close()
            self._webrtc = None
        if self._camera_task is not None:
            self._camera_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._camera_task
            self._camera_task = None
        if self._pump is not None:
            self._pump.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._pump
            self._pump = None
        if self._writer is not None:
            self._writer.close()
            with contextlib.suppress(Exception):
                await self._writer.wait_closed()
            self._writer = None
            self._reader = None

    async def _read_loop(self) -> None:
        assert self._reader is not None
        while True:
            try:
                line = await self._reader.readline()
            except ValueError as e:
                # A line over READER_LIMIT. `readline` has already let go of it, so the next read
                # starts past it, or at a tail of it that the parse below throws away. Raising
                # here is what used to end the pump, and with it every answer still to come.
                log.warning("skipped a line from robotd over %d bytes: %s", READER_LIMIT, e)
                continue
            if not line:
                for fut in self._pending.values():
                    if not fut.done():
                        fut.set_exception(TransportError("robotd closed the connection"))
                return
            try:
                msg = json.loads(line)
            except ValueError:  # not JSON, or the tail of a skipped line cut mid-character
                continue
            if not isinstance(msg, dict):
                continue
            if "id" in msg and msg["id"] is not None and ("result" in msg or "error" in msg):
                pending = self._pending.get(int(msg["id"]))
                if pending is not None and not pending.done():
                    del self._pending[int(msg["id"])]
                    if "error" in msg:
                        err = msg["error"]
                        # Carrying the number lets a caller tell BUSY (retry) from
                        # PERMISSION_DENIED (do not) without parsing the message text. The
                        # codes are in `upstream_api` and were spelled nowhere else.
                        pending.set_exception(
                            TransportError(
                                f"{err.get('code')}: {err.get('message')}", code=err.get("code")
                            )
                        )
                    else:
                        pending.set_result(msg.get("result"))
            elif "method" in msg:
                if msg["method"] == up.ROBOT_STATE.name:
                    # Before the queue push, deliberately: the queue is bounded and drops when
                    # nobody drains it, and freshness must not depend on anyone consuming.
                    self._took_state(msg.get("params"))
                with contextlib.suppress(asyncio.QueueFull):
                    self._notifications.put_nowait(msg)

    def _took_state(self, params: Any) -> None:
        """One `robot.state` frame read: the newest state, when it arrived, how many have, and
        which of the twists quackd sent it shows robotd has taken (`_settle`)."""
        self._previous_state, self._previous_state_at = self._last_state, self._last_state_at
        self._last_state = params if isinstance(params, dict) else {}
        self._last_state_at = time.monotonic()
        self._frames_read += 1
        self._state_arrived.set()
        self._settle(self._last_state, self._last_state_at)

    def _write(self, obj: dict[str, Any]) -> None:
        if self._writer is None:
            raise TransportError("not connected")
        self._writer.write((json.dumps(obj, separators=(",", ":")) + "\n").encode())

    async def request(self, method: str, params: dict[str, Any] | None = None) -> Any:
        req_id = self._next_id
        self._next_id += 1
        fut: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        self._pending[req_id] = fut
        msg: dict[str, Any] = {"jsonrpc": up.JSONRPC_VERSION, "id": req_id, "method": method}
        if params is not None:
            msg["params"] = params
        self._write(msg)
        assert self._writer is not None
        await self._writer.drain()
        try:
            return await asyncio.wait_for(fut, timeout=self.request_timeout_s)
        except TimeoutError as e:
            self._pending.pop(req_id, None)
            raise TransportError(f"{method}: no answer within {self.request_timeout_s:g}s") from e
        except TransportError as e:
            if e.code in (up.ERR_METHOD_NOT_FOUND, up.ERR_INVALID_PARAMS):
                raise TransportError(self._refused_by_name(method, e), code=e.code) from e
            raise

    def _refused_by_name(self, method: str, error: TransportError) -> str:
        """robotd's own words for a call it refused by its name or its params, with what the
        two versions were, since that is the refusal upstream put where the version gate was
        (`up.VERSION_SKEW`) and the version is half of what it means.

        `hello` that robotd's socket does not have is a socket that is not robotd's: tofd, for
        one, answers every call but its two streams with METHOD_NOT_FOUND."""
        said = f"{method}: {error}"
        if method == up.HELLO.name and error.code == up.ERR_METHOD_NOT_FOUND:
            return (
                f"{said}. Whatever answered at {self.address} has no {method}, so it is not "
                f"robotd: point --address at robotd's socket, {up.SOCKET_ROBOTD.name}"
            )
        remote = (self.robotd or {}).get("api_version")
        read = f"API v{self.api_version} at {up.RELEASE}"
        kind = (
            "a call this robotd does not have"
            if error.code == up.ERR_METHOD_NOT_FOUND
            else "params this robotd does not take"
        )
        if remote is None:
            return f"{said}. That is {kind}, before it said its version; quackd read {read}"
        if remote != self.api_version:
            return (
                f"{said}. That is {kind}: robotd speaks API v{remote} and quackd read {read}, "
                "and the call differs between the two"
            )
        return (
            f"{said}. That is {kind}, though it speaks the {read} quackd read, so this robot was "
            "built from a duck-ipc-proto quackd has not read: upstream does not promise one "
            "version number the same calls"
        )

    async def notify(self, method: str, params: dict[str, Any]) -> None:
        self._write({"jsonrpc": up.JSONRPC_VERSION, "method": method, "params": params})
        assert self._writer is not None
        await self._writer.drain()

    # ── protocol ────────────────────────────────────────────────────────────────────

    async def _fetch_frame(self) -> Image.Image:
        url = self.camera_url or ""

        def fetch() -> bytes:
            with urllib.request.urlopen(url, timeout=3) as resp:
                return resp.read()

        data = await asyncio.to_thread(fetch)
        return Image.open(io.BytesIO(data)).convert("RGB")

    async def _camera_loop(self) -> None:
        """Keep the newest frame, on a timer, so `get_frame` is a memory read.

        Same shape as the camera daemon quackd already ships for the Open Duck: capture on a
        clock rather than on request, so a slow or broken camera cannot stall the caller and a
        run asking for a frame every step does not pay an HTTP round trip every step.
        """
        period = 1.0 / max(0.1, self.camera_fps)
        while True:
            started = time.monotonic()
            try:
                self._frame = await self._fetch_frame()
                self._frame_at = time.monotonic()
                self._frame_error = None
            except Exception as e:  # a camera hiccup must not end a run
                self._frame_error = str(e)
            await asyncio.sleep(max(0.0, period - (time.monotonic() - started)))

    async def get_frame(self) -> Image.Image | None:
        """The newest frame, or None when there is no camera to read.

        Never raises. It used to raise `TransportError` on a failed snapshot, directly under a
        comment saying snapshot failures must not kill a run — and `AgentLoop._observe` calls
        this every step without catching anything, so one dropped HTTP response ended the
        session. `camera_health()` is where the failure is visible instead.
        """
        if not self.camera_url:
            return None  # robotd's socket carries no picture, so --camera-url is the only one
        if self._webrtc is not None:
            return self._webrtc.latest(max_age_s=camera_stale_after_s(self.camera_fps))
        if self._camera_task is None:  # not connected: fetch once rather than nothing
            with contextlib.suppress(Exception):
                self._frame = await self._fetch_frame()
                self._frame_at = time.monotonic()
        return None if self.frame_age_s() is None else self._frame

    def frame_age_s(self) -> float | None:
        """Seconds since the newest usable frame, or None if there is none to steer on.

        None covers both "nothing has ever arrived" and "what we have is too old to act on".
        The callers already treat a missing frame as a reason to stop — `go_to` fails on the
        next tick rather than driving at a memory — so expiry is what turns a dead camera back
        into a stop instead of twenty seconds of blind walking.
        """
        if self._frame is None or self._frame_at is None:
            return None
        age = time.monotonic() - self._frame_at
        return None if age > camera_stale_after_s(self.camera_fps) else age

    @property
    def camera_fov_deg(self) -> float | None:
        """How wide the frames `get_frame` hands out are, in degrees, when the camera says, or
        None when it does not.

        `MicroduckAdapter.connect` puts it in the manifest's limits as `camera_fov_deg`, which
        is where `quackd run` takes the lens its detector measures bearings through when no
        `--fov-deg` names one, and where `serve-mcp`, which has no `--fov-deg`, always takes it.
        Without one a real camera is measured through the simulator's 90 degrees. Over WebRTC
        it is `mediad`'s own intrinsics, turned with the frame (`up.MEDIA_VIDEO`)."""
        if self._webrtc is not None:
            return self._webrtc.fov_deg
        return None

    def camera_health(self) -> dict[str, Any]:
        """What the camera is doing, for `doctor` and for the run's own state."""
        if not self.camera_url:
            return {"configured": False}
        if self._webrtc is not None:
            return self._webrtc.health()
        age = None if self._frame_at is None else round(time.monotonic() - self._frame_at, 2)
        return {
            "configured": True,
            "url": self.camera_url,
            "ok": self._frame is not None,
            "age_s": age,
            "size": list(self._frame.size) if self._frame is not None else None,
            "error": self._frame_error,
        }

    def state_age_s(self) -> float | None:
        """Seconds since the last `robot.state` frame, or None if none has ever arrived."""
        if self._last_state_at is None:
            return None
        return time.monotonic() - self._last_state_at

    def _skills(self) -> tuple[str, ...]:
        """The skill names this robot answered `robot.subscribe` with (`up.SKILL_SOURCE`), which
        are also the labels it reports while one of them runs."""
        answer = self.subscribed if isinstance(self.subscribed, dict) else {}
        skills = answer.get("skills")
        return tuple(str(s) for s in skills) if isinstance(skills, list) else ()

    def _read(self) -> StateRead:
        """The newest state frame and what it says about the duck, without asking robotd
        anything: `robot.move` is held back on this, every tenth of a second, mid-verb."""
        # A duck nobody is watching reads as unknown, never as standing. `fallen` is a plain
        # bool on the wire and in DuckState, so silence and "upright" are the same value there
        # — the difference has to live in `posture` and in `fall_detection`, or a stream that
        # never started looks exactly like a robot that is fine. Same invariant the Open Duck
        # bridge states in `bridge.py`.
        age = self.state_age_s()
        arriving = age is not None and age <= state_stale_after_s(self.state_hz)
        state = (self._last_state or {}) if arriving else {}
        safety = state.get("safety")
        named = state.get("policy")
        policy = str(named or "unknown")
        fallen = bool((safety or {}).get("fallen"))
        # Frames arriving is not the same as frames that say anything about falling. Upstream
        # declares `safety` on every state frame, but a frame without one would otherwise read
        # as "not fallen" on the strength of a key that was never there.
        if not (arriving and isinstance(safety, dict) and "fallen" in safety):
            if age is None:
                silent = "no robot.state frame has arrived"
            elif not arriving:
                silent = f"the newest robot.state frame is {age:.1f} s old"
            else:
                silent = "robotd's state frame carries no safety block"
            return StateRead(state, age, False, policy, fallen, None, "unknown", silent)
        posture, why = posture_of(str(named) if named else None, safety, skills=self._skills())
        picked_up = bool(safety.get("picked_up"))
        return StateRead(state, age, True, policy, fallen, picked_up, posture, why)

    async def get_state(self) -> DuckState:
        health = self._last_health
        if health is None:
            with contextlib.suppress(TransportError):
                health = await self.request(up.ROBOT_HEALTH.name)
                self._last_health = health if isinstance(health, dict) else None

        read = self._read()
        battery = ((health or {}).get("battery") or {}).get("percent")
        yaw, pitch, pose_from = _head_pose(read.state)
        return DuckState(
            t=self.now(),
            policy=read.policy,
            posture=read.posture,
            fallen=read.fallen,
            battery_percent=float(battery) if battery is not None else None,
            extras={
                "health": health,
                "move": read.state.get("move"),
                "loop": read.state.get("loop"),
                "odom": read.state.get("odom"),
                # False means quackd cannot see falls at all right now, so `fallen: false` above
                # is silence rather than a verdict. The preconditions read this.
                "fall_detection": read.fresh,
                # Why the posture is `unknown`, in words the pilot and `report_state` can use,
                # or None when it is not. `standing` refuses on it and quotes it.
                "posture_reason": read.reason,
                # True while somebody holds the duck, which the preconditions and `stand_up`
                # read before `fallen`; None while no fresh frame says either way.
                "picked_up": read.picked_up,
                # Whether this robotd reports safety.picked_up at all, which one older than its
                # pick-up detector does not, and then nothing tells quackd somebody holds the
                # duck. None while no fresh frame says either way.
                "pickup_detection": _pickup_detection(read),
                # Which robotd this is beside what quackd read, the warning when the two
                # differ, and what it reports that the floor does not promise.
                "robotd": self._robotd_record(read),
                # What robotd's own state last said about the twists quackd sent.
                "move_echo": self.move_echo,
                # Where the camera points from the trunk, in degrees, positive left and up, the
                # simulators' convention, and what said so; None from a frame that does not say.
                "head_yaw_deg": yaw,
                "head_pitch_deg": pitch,
                "head_pose_from": pose_from,
                # The gaze quackd keeps on the head against robotd's idle look-around, and why
                # the last one ended, which is how somebody else taking the head shows.
                "gaze_hold": self._gaze_record(),
                "gaze_released": self.gaze_released,
                "state_age_s": round(read.age, 3) if read.age is not None else None,
                "subscribed": self.subscribed,
                "camera": self.camera_health(),
                "stop_error": self.stop_error,
                # What quackd stands in for on this robot, which the pilot is told not to take
                # for the robot's own work. Posture used to be the first of them. It is read off
                # labels robotd's own source names now, so what is left is stand_up, which asks
                # the policy to drive and is all quackd has for a duck on the floor.
                "assumptions": [up.STAND_UP_RPC.name],
            },
        )

    def _robotd_record(self, read: StateRead) -> dict[str, Any] | None:
        """`extras.robotd`: the hello, what quackd read, the floor, the warning, and two things
        the floor serves robots without, read off the robot rather than off its version.

        `state_frames` is whether the newest state carries `frames` (`up.STATE_FRAMES`),
        `mediad_frame` whether mediad's `GET /frame` has answered (`up.MEDIAD_FRAME_HTTP`), which
        quackd knows only when `--camera-url` points at it. Each is None when nothing has said."""
        if self.robotd is None:
            return None
        return {
            **self.robotd,
            "state_frames": (read.state.get("frames") is not None) if read.state else None,
            "mediad_frame": self._mediad_frame_answered(),
        }

    def _mediad_frame_answered(self) -> bool | None:
        """Whether mediad's `GET /frame` has answered at least once, or None when `--camera-url`
        is not that route. A robotd from before daemon-v0.13.0 has no such route to answer."""
        url = self.camera_url
        if not url or is_webrtc_url(url):
            return None
        if urllib.parse.urlsplit(url).path != up.MEDIAD_FRAME_PATH:
            return None
        return self._frame_at is not None

    @property
    def safety(self) -> dict[str, Any]:
        """What robotd's own frames say it can detect, for `doctor`, which reads a transport's
        `safety` for its go/no-go rows: whether it reports `safety.picked_up` at all. Empty
        until a fresh frame has said either way."""
        detection = _pickup_detection(self._read())
        return {} if detection is None else {"pickup_detection": detection}

    def _move_refusal(self) -> str | None:
        """Why the next `robot.move` must not go out, or None when it may.

        It may only while the newest state reads standing, and once one has been held back no
        other goes out until `stop()`. The preconditions cannot do this alone: they are read
        once, when a verb starts, and `go_to` sends its next twist whatever became of the last
        one. Picked up halfway through a `go_to`, a duck reads `held` once robotd's pick-up
        detector has paused its policy, but the moment it is put down the policy drives
        whatever twist is freshest, and `go_to` had kept sending one ten times a second. Held
        back until the verb that was walking has stopped, the duck stays where it was put.

        `stop()` is what every verb that walks sends on its way out, so the hold lasts as long
        as the verb that ran into it. `search_scan` also stops between its turns, so a scan
        whose duck was picked up turns again once the duck is back on its feet and reads
        standing, one turn at a time, where a `go_to` or a `move` does not move again at all.

        Nothing is sent in place of the refused twist: robotd zeroes a twist nobody refreshes
        within its deadman window (`up.DEADMAN`), and a zero sent here would be one more
        `robot.move` to a duck quackd has just decided not to drive.

        The same hold covers a duck that is standing and a robotd that is not taking the twists:
        one quackd sent that the state has not shown `MOVE_ECHO_FRAMES` frames and
        `move_echo_after_s` later (`_not_taken`).
        That is how a `robot.move` robotd could not parse shows, since a notification gets no
        answer, and the verb that ran into it stops rather than reporting a walk.
        """
        if self._move_held is not None:
            return (
                f"quackd has sent no robot.move since {self._move_held}, and sends none until "
                "the verb that was moving it stops"
            )
        read = self._read()
        if read.posture != "standing":
            why = read.reason or (
                "the duck has fallen" if read.posture == "fallen" else f"the duck is {read.posture}"
            )
            self._move_held = f"the duck stopped standing ({why})"
            log.warning(
                "robot.move held back until the next stop: the duck is not standing (%s)", why
            )
            return (
                f"the duck is not standing ({why}), so quackd sent no robot.move, and sends none "
                "until the verb that was moving it stops"
            )
        if (unseen := self._not_taken()) is not None:
            self._move_held = "robotd stopped showing the twists it was sent"
            log.warning("robot.move held back until the next stop: %s", unseen)
            return (
                f"robotd has not taken robot.move: {unseen}. quackd sends no more until the verb "
                "that was moving it stops"
            )
        return None

    def _settle(self, state: dict[str, Any], arrived: float) -> None:
        """Strike off every twist a state frame shows robotd has taken.

        The frame's `move.requested` is the twist robotd was last asked for (`up.MOVE_ECHO`).
        Matched against the newest of the unshown twists first, since robotd takes them in the
        order they were sent and a frame that shows one has taken every one before it. A frame
        that shows only an older twist than one still waiting, which is robotd's state from
        before the newer one arrived, strikes off nothing newer than the one it shows."""
        if not self._unshown:
            return
        asked = _twist_of(state.get("move"))
        if asked is None:
            return
        for i in range(len(self._unshown) - 1, -1, -1):
            sent_at, _, twist = self._unshown[i]
            if _same_twist(twist, asked):
                break
        else:
            return
        for _ in range(i + 1):
            self._unshown.popleft()
        self.move_echo = {
            "taken": True,
            "twist": list(twist),
            "after_s": round(arrived - sent_at, 3),
        }

    def _not_taken(self) -> str | None:
        """Why the newest state says robotd has not taken the twists quackd sent, or None.

        Judged on the oldest moving twist no frame has shown, and only once `MOVE_ECHO_FRAMES`
        frames have been read since it went out, the newest of them `move_echo_after_s` after
        it: a twist sent a moment ago has not had the time to show, a frame read late may have
        been built before the twist reached robotd, and a stopped twist proves nothing, since a
        robot that took nothing reads stopped too. So a walk shorter than the window is never
        judged, and is reported on what it sent alone, as it always was.

        Two things look alike here, and the words name both. A robotd that could not parse the
        twist answers nothing and goes on showing what it had. One that took it shows the next
        twist anybody sent, and a connected gamepad sends one at least ten times a second, a stop
        while its sticks are still (`up.PAD_DRIVES`), so a pad in somebody's hand fights every
        walk quackd sends. Holding the walk back is right either way: the duck is not doing what
        quackd asked, and the pad is the robot's authority, not quackd."""
        waiting = next((entry for entry in self._unshown if any(entry[2])), None)
        if waiting is None or self._last_state_at is None:
            return None
        sent_at, frames_then, twist = waiting
        waited = self._last_state_at - sent_at
        if self._frames_read - frames_then < MOVE_ECHO_FRAMES:
            return None
        if waited < move_echo_after_s(self.state_hz):
            return None
        asked = _twist_of((self._last_state or {}).get("move"))
        self.move_echo = {
            "taken": False,
            "twist": list(twist),
            "requested": list(asked) if asked is not None else None,
            "after_s": round(waited, 3),
        }
        shows = (
            f"still says it was asked for {_shown(asked)}"
            if asked is not None
            else "carries no move.requested to say"
        )
        remote = (self.robotd or {}).get("api_version")
        unread = (
            f", and this robotd speaks API v{remote}, which quackd has not read"
            if remote is not None and remote != self.api_version
            else ""
        )
        return (
            f"quackd sent {_shown(twist)}, and the robot.state that arrived {waited:.1f} s later "
            f"{shows}. robotd answers a robot.move with nothing, so either it could not parse "
            f"this one{unread}, or another client sent one after it, as padd does at least ten "
            "times a second while a gamepad is connected, a stop while its sticks are still"
        )

    async def send_intent(self, intent: Intent) -> Ack:
        p = intent.params
        try:
            match intent.kind:
                case "move":
                    if (refusal := self._move_refusal()) is not None:
                        return Ack(accepted=False, reason=refusal)
                    vx, vy, wz = p.get("vx", 0.0), p.get("vy", 0.0), p.get("wz", 0.0)
                    await self.notify(up.ROBOT_MOVE.name, {"vx": vx, "vy": vy, "vyaw": wz})
                    # Written and drained, so the state could show it from now on (_settle).
                    sent = (_number(vx), _number(vy), _number(wz))
                    self._unshown.append((time.monotonic(), self._frames_read, sent))
                    return Ack()
                case "stop":
                    self._end_gaze("stopped")  # first, as `stop()` ends it
                    try:
                        await self.request(up.ROBOT_STOP.name)
                    finally:
                        self._release()  # the stop `stop()` sends, so it ends the hold too
                    return Ack()
                case "do":
                    res = await self.request(up.ROBOT_DO.name, {"skill": p.get("skill")})
                    return _ack(res)
                case "look":
                    # This look replaces whatever head a hold was keeping, so the hold ends before
                    # it goes out: a re-send landing after it would put the old gaze back.
                    self._end_gaze("a later robot.look replaced it")
                    res = await self.request(
                        up.ROBOT_LOOK.name,
                        {
                            "x": p.get("x", 1.0),
                            "y": p.get("y", 0.0),
                            "z": p.get("z", 0.0),
                            "neck_pitch": 0.0,
                        },
                    )
                    if (head := _look_head(res)) is not None:
                        self._hold(head)
                    clamped = isinstance(res, dict) and res.get("clamped")
                    return Ack(accepted=True, reason="clamped" if clamped else None)
                case "sound":
                    asked = p.get("tag", "chirp")
                    tag = asked if asked in up.SOUND_TAG_LIST else "chirp"
                    res = await self.request(up.ROBOT_SOUND.name, {"tag": tag})
                    ack = _ack(res)
                    if tag != asked and ack.accepted:
                        # Upstream has seven tags and no TTS. Substituting is right; doing it
                        # without saying so meant a verb reporting a sound nobody asked for.
                        return Ack(accepted=True, reason=f"{asked!r} is not a duck sound; {tag}")
                    return ack
                case "enable":
                    res = await self.request(up.ROBOT_ENABLE.name, {"on": bool(p.get("on", True))})
                    return _ack(res)
                case "pose":
                    await self.notify(
                        up.ROBOT_POSE.name, {k: p[k] for k in ("z", "roll", "pitch") if k in p}
                    )
                    return Ack()
                case _:
                    return Ack(accepted=False, reason=f"no upstream mapping for {intent.kind}")
        except TransportError as e:
            return Ack(accepted=False, reason=str(e))

    async def subscribe(self, topic: str) -> AsyncIterator[dict[str, Any]]:  # type: ignore[override]
        if topic in ("state", up.ROBOT_STATE.name) and self.subscribed is None:
            # connect() normally does this; only a transport used without it needs asking twice.
            self.subscribed = await self.request(up.ROBOT_SUBSCRIBE.name, {"hz": self.state_hz})
        while True:
            msg = await self._notifications.get()
            yield {"topic": msg.get("method"), **(msg.get("params") or {})}

    async def heartbeat(self) -> None:
        try:
            health = await self.request(up.ROBOT_HEALTH.name)
        except TransportError as e:
            raise HeartbeatError(f"robot.health failed: {e}") from e
        if isinstance(health, dict):
            self._last_health = health
            if health.get("healthy") is False:
                raise HeartbeatError(f"robotd unhealthy: {health.get('reason') or 'no reason'}")

    async def stop(self) -> None:
        """Zero the velocity. Never raises — but never pretends it landed, either.

        The protocol asks that this be safe to call from anywhere at any time, including from
        the exception paths that run *because* the socket died, so it cannot raise. It used to
        swallow the failure silently, which meant the heartbeat could log "stopping the duck"
        while delivering nothing. What actually stops a Microduck in that situation is robotd's
        own deadman: `robot.move` notifications stop arriving and the velocity goes to zero
        (`up.DEADMAN`). That is a real protection and this is not a substitute for it.

        It also ends the hold on `robot.move` (`_move_refusal`), after the request rather than
        before it, so no twist slips out while the stop is on the wire. Whether or not the stop
        landed, the verb that was walking is over, and the next one is let through only by a
        state that reads standing again.

        And it ends a held gaze (`_hold_gaze`), before the request, so that no `robot.head`
        follows a stop onto the wire. The `stop` verb is what the pilot is told to reach for when
        anything looks wrong and the heartbeat sends this when the link is in doubt, and a head
        pulled back to where it was every second after either would be quackd going on steering
        the robot. A verb that steers on the camera centres the head again for itself.
        """
        self._end_gaze("stopped")
        try:
            await self.request(up.ROBOT_STOP.name)
            self.stop_error = None
        except TransportError as e:
            self.stop_error = str(e)
            log.warning(
                "stop could not be delivered to %s (%s). The robot's own deadman zeroes "
                "velocity when robot.move stops arriving, which is what stops it now.",
                self.address,
                e,
            )
        finally:
            self._release()

    def _release(self) -> None:
        """The verb that was walking is over: lift the hold on `robot.move`, and forget the
        twists it sent, which a stop replaces with zero whether or not robotd showed them."""
        self._move_held = None
        self._unshown.clear()

    # ── the gaze hold ───────────────────────────────────────────────────────────────

    def _hold(self, head: Head) -> None:
        """Start holding the head a `robot.look` answered with."""
        gaze = _GazeHold(head=head, since=time.monotonic())
        self._gaze, self.gaze_released = gaze, None
        self._gaze_task = asyncio.create_task(self._hold_gaze(gaze), name="quackd-jsonrpc-gaze")

    def _end_gaze(self, why: str) -> asyncio.Task[None] | None:
        """End the gaze hold if one runs, saying why, and hand back its task, cancelled.

        Not a coroutine, so whoever ends the hold knows nothing more goes out from the moment it
        returns: the hold writes only right after it wakes, and a cancelled task wakes only to
        end."""
        task, gaze = self._gaze_task, self._gaze
        self._gaze, self._gaze_task = None, None
        if gaze is not None:
            self.gaze_released = why
        if task is not None and not task.done():
            task.cancel()
        return task

    async def _hold_gaze(self, gaze: _GazeHold) -> None:
        """Send the held head again as `robot.head` every `gaze_hold_s` until something ends it.

        `LookResult.head` is "What was handed to the same path `robot.head` feeds — resend these
        to hold the gaze" (`up.ROBOT_LOOK`), and since daemon-v0.16.1 that is no longer a
        nicety: robotd glances around on its own once nobody has commanded the head for two
        seconds (`up.IDLE_HEAD`). A gaze used to be one look, so a pilot's `observe` a few
        seconds after `gaze` could be taken from a head glancing up to 0.6 rad of yaw away from
        where it was pointed, and report bearings off by as much.

        Nothing goes out while the duck reads as anything but standing or sitting: in somebody's
        hands, limp, fallen, halfway through a skill, on a label quackd has not read, or with no
        frame to say. quackd moves nothing of a duck it cannot vouch for, and the head is part of
        it. Those pass, a duck held is put back down, so the hold waits rather than ends, and
        sends the head again once the duck stands or sits, the head being where the pilot last
        pointed it.

        It ends, with `gaze_released` saying why, when `robot.state.head` shows something else
        steering the head (`_head_taken`), and when a `robot.head` cannot be written, which is
        `TransportError` from a writer that is gone and an `OSError` such as
        `ConnectionResetError` from a socket that died under it: the heartbeat is what answers
        for a link that is gone. `stop()`, `close()` and the next look end it from outside,
        through `_end_gaze`.
        """
        why = "it ended"
        try:
            while True:
                await asyncio.sleep(self.gaze_hold_s)
                read = self._read()
                if read.posture not in ("standing", "sitting"):
                    gaze.paused = read.reason or (
                        "the duck has fallen"
                        if read.posture == "fallen"
                        else f"the duck is {read.posture}"
                    )
                    continue
                if gaze.paused is not None:
                    # robotd may have glanced while nothing went out, and lets go of it from the
                    # send below, so the judging of what the head shows starts over from here
                    gaze.paused, gaze.since = None, time.monotonic()
                elif (taken := self._head_taken(gaze)) is not None:
                    why = f"overridden: {taken}"
                    log.warning("gaze hold released, %s", why)
                    return
                await self.notify(up.ROBOT_HEAD.name, gaze.params())
        except (TransportError, OSError) as e:
            why = f"robot.head could not be sent: {e}"
            log.warning("gaze hold ended: %s", why)
        finally:
            if self._gaze is gaze:  # ended in here: `_end_gaze` clears it before it cancels
                self._gaze, self._gaze_task, self.gaze_released = None, None, why

    def _head_taken(self, gaze: _GazeHold) -> str | None:
        """Why `robot.state.head` says something other than this hold is steering the head, or
        None while nothing does.

        `robot.state.head` is not an echo of what was sent, the way `move.requested` is. It is
        the one head slot any client writes, smoothed toward each new value a little every
        control tick, with an idle glance on top while one fades out (`up.ROBOT_HEAD`), so it is
        never read as equal or not. With quackd alone on the head it closes on the held head
        with every frame, however slowly robotd is set to smooth, and once a glance has faded it
        never moves away from it. A gamepad posing the head, `robotctl`, or another client that
        writes the slot moves it toward a head of its own, away from quackd's, and so does
        robotd glancing again because it is not taking quackd's `robot.head`.

        So the newest two frames are compared, both read at least `head_fade_s` and a hold
        period after the head was last sent this target, by when any glance has faded. A head
        off the held one by more than JSON's rounding, and farther off than in the frame before
        it, is somebody else's. The hold then lets go rather than pull the head back every
        second against whoever has it, which is the fight upstream's own wait exists to spare
        "a client steering the head" (`up.IDLE_HEAD`)."""
        if self._previous_state_at is None:
            return None
        if self._previous_state_at < gaze.since + self.head_fade_s + self.gaze_hold_s:
            return None
        now = _head_of((self._last_state or {}).get("head"))
        before = _head_of((self._previous_state or {}).get("head"))
        if now is None or before is None:
            return None
        off_now, off_before = _farthest(now, gaze.head), _farthest(before, gaze.head)
        if off_now <= _ECHO_TOLERANCE or off_now <= off_before:
            return None
        return (
            f"robot.state.head went from {_shown(before)} to {_shown(now)}, away from the "
            f"{_shown(gaze.head)} quackd held: another client is steering the head, or robotd "
            "is not taking quackd's robot.head"
        )

    def _gaze_record(self) -> dict[str, Any] | None:
        """`extras.gaze_hold`: the head being held, how often it goes out, and why nothing does
        just now, or None while no gaze is held."""
        gaze = self._gaze
        if gaze is None:
            return None
        return {"head": gaze.params(), "every_s": self.gaze_hold_s, "paused": gaze.paused}

    def now(self) -> float:
        return time.monotonic() - self._t0

    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(seconds)


def _ack(result: Any) -> Ack:
    if isinstance(result, dict) and "accepted" in result:
        return Ack(accepted=bool(result["accepted"]), reason=result.get("reason"))
    return Ack()
