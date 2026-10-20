"""EXPERIMENTAL: a Huebot, over the shim its maker runs on it.

Huebot's own API is closed. Its maker runs a shim on the robot that answers the JSON-RPC the
Microduck's `robotd` speaks, and this is the Microduck's transport, copied and renamed, less what
the shim does not answer and less the Microduck's camera. Every method name is robotd's, VERIFIED
at the Microduck adapter's pin (see `upstream_api.py`), and nobody has seen the shim or run this
against a Huebot. What is honest today: the handshake, which refuses a peer older than the
oldest protocol quackd has read and warns about any version but the one it read, the intent
vocabulary, the deadman-friendly `move` notifications and the shim's own echo of them, the state
subscription, the health poll, and posture, read off the policy label against the labels robotd's
own source names and failing closed on every other. What is not is listed as UNVERIFIED in
`upstream_api.py`: that the shim answers any of it as robotd does (`up.SHIM_CALLS`), the order of
its joints (`up.JOINT_ORDER`), its deadman (`up.SHIM_DEADMAN`), its skills (`up.SHIM_SKILLS`),
and what `stand_up`'s `robot.enable` does for a Huebot on the floor (`up.STAND_UP_RPC`), which
every state lists in `extras.assumptions`.

These rules run through this file, and each exists because the robot is on a floor and quackd
is at the other end of an ssh tunnel:

- **No address is assumed.** The shim is wherever its maker put it, so `--address` says where,
  and a connect without one is refused, naming the ssh forward that reaches it. The protocol
  has no authentication at all, so that forward, or a socket on the robot itself, is the only
  way in, never a port open to the network.
- **Silence is not consent.** A state frame that stops arriving, or arrives without a `safety`
  block, reads as `unknown` rather than as a standing robot, because `fallen: false` from a
  stream nobody is watching is not a fact about the robot. The preconditions refuse on
  `unknown`, so a link that dies stops the robot instead of freeing it.
- **Only a label quackd has read stands the robot up.** `robot.state.policy` names whatever
  drove the tick, and robotd reports far more labels than `walk` and `stand`. Each of them reads
  as `unknown`, with `extras.posture_reason` saying which, and so does any label a shim makes up,
  so a vocabulary that grows refuses walks instead of allowing them.
- **Once the robot stops standing, the walk stays stopped.** No `robot.move` goes out unless the
  newest state reads standing, and once the robot stops standing while a verb walks, none goes
  out for that verb until it ends, which every verb that walks does at its first twist held
  back, saying why. The preconditions are read once, when a verb starts, so without the hold a
  robot picked up halfway through a `go_to` would walk off the moment it was put down. The hold
  belongs to the verbs that have walked, by the task each runs in, and lasts until that task
  ends, so no stop lifts it. While it stands the state reads `unknown` and says why.
- **A version quackd has not read is served, and says so.** No robotd refuses a peer for a
  different API version, and a call whose shape moved refuses itself by name, so quackd serves
  any peer from `up.API_FLOOR` up, warns whenever its version is not the one read, and names the
  call and both versions when one is refused (`up.VERSION_SKEW`).
- **A notification nobody answered is not a walk.** Nothing answers a `robot.move`, so one the
  shim could not parse would be a walk reported and never taken. Its state says what it was last
  asked for (`up.MOVE_ECHO`), and a twist that state has not shown a few frames after the shim
  can have read it holds `robot.move` back the way a robot that stops standing does. When the
  shim can have read it is what its answers say, not a clock: it reads a connection's lines in
  order, as robotd does (`up.LINES_IN_ORDER`), so a link that held quackd's lines back for a
  while holds back nothing the shim then takes.
- **A link that fails says so, and only so.** Whatever the socket does, every call here fails
  with `TransportError`, `stop()` does not raise, and no one line the shim writes ends the task
  that reads them.
- **Nothing goes out that the shim does not answer.** It does not answer `robot.look` yet
  (`up.SHIM_LOOK`), so a look is refused here before anything is written, and so is a
  `robot.do` for a skill its `robot.subscribe` answer did not list (`up.SHIM_SKILLS`).
- **A frame kept is not a frame seen.** `get_frame` expires what it caches. `go_to` steers on
  every tick, so a camera that died with the ball in shot would otherwise show the ball in shot
  forever while the robot walked at it.

Addresses: `tcp://host:port`, after `ssh -L 9870:<socket> <huebot>`, which also works from
Windows, or `unix:///path` to the shim's socket on the robot itself, POSIX only. Frames: the
shim's socket carries no picture, so `--camera-url` names one, any HTTP URL that answers each
request with one still image.
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
import urllib.error
import urllib.request
from collections import deque
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from http import HTTPStatus
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
from quackd_huebot import upstream_api as up

STATUS = "EXPERIMENTAL: robotd's method names, never run against Huebot's shim"

log = logging.getLogger("quackd.transport.huebot")

NO_ADDRESS = (
    "huebot:jsonrpc needs --address: quackd knows no default for Huebot's shim, which is "
    "wherever its maker put it. Forward the shim's socket over ssh, "
    "`ssh -L 9870:<socket> <huebot>`, then pass --address tcp://127.0.0.1:9870"
)
"""Why a Huebot with no `--address` is refused, as it is built (`make_transport`) and again at
a connect, and the forward that fixes it."""

LOOK_REFUSED = "Huebot's shim does not answer robot.look yet, so quackd sends it no look"
"""What a look is answered with, here, before anything is written (`up.SHIM_LOOK`)."""

#: How often we ask the shim for a state frame. Ten a second is the rate the walk verb already
#: feeds intents at, and robotd decimates per subscriber, so asking for less than its loop's
#: 50 Hz costs the robot proportionally less (`up.ROBOT_SUBSCRIBE`).
STATE_HZ = 10


def state_stale_after_s(hz: float) -> float:
    """How old a state frame may be before `get_state` stops believing it.

    Derived from the rate actually subscribed at, not from the default: a transport built with
    `state_hz=2` gets a 2.5 s window, where a fixed 0.3 s one would declare every healthy robot
    blind half a second after each frame. Five frame periods, floored at one second so link
    jitter on an ssh tunnel does not blank the posture (`up.STATE_NEEDS_SUBSCRIBE`).
    """
    return max(1.0, 5.0 / max(0.1, hz))


#: The window at the default rate, for tests and for anything reading the module constant.
STATE_STALE_AFTER_S = state_stale_after_s(STATE_HZ)

#: The longest line the reader takes from the shim, a mebibyte. asyncio's default is 64 KiB,
#: and nothing bounds what a peer writes: robotd's `robot.state` grew the joints' velocities and
#: currents, an IMU and the whole skeleton between API 24 and 36. Over the default, `readline`
#: raised out of the pump, which died without a word, and every request after it waited out its
#: timeout on a socket nobody read. `_read_loop` skips a line longer still instead of ending the
#: link on it.
READER_LIMIT = 2**20

#: How many state frames the shim gets to show a twist before quackd decides it has not taken it.
#: robotd stores a `robot.move` as it reads it and reports it in the next frame it publishes
#: (`up.MOVE_ECHO`), so a twist that has not shown three frames after it went out, and
#: `move_echo_after_s` after, is not one a healthy link is still delivering.
#:
#: The verdict is counted from when the shim can have read the twist, and that is not a clock
#: reading. robotd reads one connection's lines in order and stores a notification before it
#: reads the next line (`up.LINES_IN_ORDER`), so an answer to any call quackd wrote after the
#: twist says it has read it, and only the frames read after that answer count against it: three
#: of them, since one built just before the twist was stored can still go out after the answer.
#: Counted from the send alone, a link that held quackd's lines back for longer, a lost segment
#: on an ssh tunnel resent a few hundred milliseconds later, would call every twist under way
#: refused. Whatever call answers will do, the heartbeat's included, and quackd asks one of its
#: own as each walk starts and whenever a twist has gone the window unshown with no answer yet.
#:
#: A walk ends where a stop is answered, since robotd zeroes its twist as it reads the stop and
#: writes the stop's answer before another frame: the frames read before that answer are the
#: only ones that can show the twists sent before it. A verb's walks are counted together until
#: it ends (`_missed`): each walk's frames read after the shim answered a call made after it, less
#: the first of them, and `MOVE_ECHO_FRAMES` less one in all is the verdict, which is what three
#: frames in one walk always were. Counted as frames and not only as time, so that a frame built
#: before the twist reached the shim, read late when quackd's own process stalled, does not read
#: as a twist the shim did not take.
MOVE_ECHO_FRAMES = 3


def move_echo_after_s(hz: float) -> float:
    """How long after a twist went out a state frame may arrive still not showing it, with
    `MOVE_ECHO_FRAMES` read since, before quackd asks the shim whether it has read the twist:
    `MOVE_ECHO_FRAMES` frame periods at the rate actually subscribed at."""
    return MOVE_ECHO_FRAMES / max(0.1, hz)


#: What `extras.posture_reason` says of a robot that stands while `robot.move` is held back for
#: a verb still running, ahead of why it is (`extras.move_hold`), when posture reads `unknown`
#: for it.
HELD_FOR_A_VERB = (
    "a verb that was walking is still running, and quackd holds robot.move back for it until it "
    "ends"
)


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
    """A twist component as quackd sent it, or NaN for one that is not a number. A peer drops a
    `robot.move` it cannot parse, and NaN matches nothing a frame shows, so the echo still
    judges an intent built by hand with a member the shim cannot read."""
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


def _shows(asked: Twist | None, *, past: bool = False) -> str:
    """What a state frame said the shim was asked for, in the words a verdict that it has not
    taken a twist quotes it in (`_not_taken`), of the newest frame, or `past`, of frames that
    came before a stop."""
    if asked is None:
        return "carried no move.requested to say" if past else "carries no move.requested to say"
    said = "still said" if past else "still says"
    return f"{said} it was asked for {_shown(asked)}"


@dataclass(frozen=True)
class _Closed:
    """A `robot.stop` the shim answered, which ends the walk the twists written before it were
    part of (`_closed`). Every frame read before the answer was built before the twist was
    zeroed, so those twists are judged on the frames counted here and on no later ones."""

    line: int
    """The stop's place among the lines quackd wrote on this connection."""
    frames: int
    """How many state frames had been read when the shim answered it."""
    at: float | None
    """When the newest of them arrived, or None when none had."""
    asked: Twist | None
    """What the newest of them said the shim was asked for, or None when it did not say."""


@dataclass
class _Sent:
    """A twist quackd sent that no state frame has shown yet (`_settle`, `_not_taken`)."""

    twist: Twist
    at: float
    """When it had been written and drained."""
    frames: int
    """How many state frames had been read by then (`_frames_read`)."""
    line: int
    """Its place among the lines quackd wrote on this connection, which an answer to a later
    call is compared with (`_heard`)."""
    task: asyncio.Task[Any] | None = None
    """The verb that sent it, by the task it runs in, which a verdict on it holds back and which
    `twist_shown_since` answers for. It is forgotten when that verb ends (`_release`)."""
    heard: int | None = None
    """How many state frames had been read when the shim answered a call quackd wrote after
    this twist, which says it had read the twist by then (`up.LINES_IN_ORDER`), or None while no
    such answer has come."""
    unheard: str | None = None
    """Why the call quackd made to find that out got no answer, when it got none."""
    closed: _Closed | None = None
    """The stop that ended the walk this twist was part of, once the shim has answered it, or
    None while the walk goes on."""


def _id_of(value: Any) -> int | None:
    """An answer's `id` as one of the integers quackd numbers its requests with, or None for
    anything else: a string, a list, a float, a bool, which Python would otherwise read as 0
    or 1, and the null a peer answers a line it could not parse with. None matches no request."""
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _finite(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool) and math.isfinite(value)


def _head_yaw(state: dict[str, Any]) -> float | None:
    """Where the head points from the trunk, in degrees of yaw, positive left: the head_yaw
    joint, read at its index in robotd's JOINT_NAMES (`up.JOINT_ORDER`), or None when the frame
    has no joints of that shape. The simulators publish `head_yaw_deg` the same way, and a flock
    member adds it to a camera bearing to get one from the body. Nothing steers on it."""
    joints = state.get("joints")
    at = up.JOINT_NAMES.index("head_yaw")
    if isinstance(joints, list) and len(joints) == len(up.JOINT_NAMES) and _finite(joints[at]):
        return round(math.degrees(float(joints[at])), 1)
    return None


#: How often to pull a snapshot when `--camera-url` names one. `go_to` and `search_scan` steer
#: at 10 Hz, so this is the rate a control decision can be stale by: well above the pilot's
#: roughly-once-a-second glance, and below what an ssh tunnel will carry.
CAMERA_FPS = 5.0

#: How long one snapshot may take before it counts as failed.
SNAPSHOT_TIMEOUT_S = 3.0

#: How long `connect()` waits for the camera's first frame, through the failures a camera that
#: is coming up answers with (`_snapshot_failure`), before it reports no camera for the run.
FIRST_FRAME_WAIT_S = 5.0


def camera_stale_after_s(fps: float) -> float:
    """How old a frame may be before `get_frame` stops handing it out.

    A cached frame that never expires is worse than no frame at all: `go_to` steers on every
    tick, and a camera that died with the ball in shot would show the ball in shot forever while
    the robot walked at it. Four frame periods, floored at two seconds so a slow but working
    camera is not dropped. How old a frame is counts from the moment quackd asked for it, not
    from the moment the last of it arrived, so a frame slower to arrive than this window is
    never handed out.
    """
    return max(2.0, 4.0 / max(0.1, fps))


class SnapshotRefused(Exception):
    """An HTTP camera that answered with an error status instead of a picture, with the first
    line of what it said, which urllib's own message leaves out."""

    def __init__(self, code: int, reason: str, said: str) -> None:
        self.code = code
        status = " ".join(f"HTTP {code} {reason}".split())
        super().__init__(f"{status}: {said}" if said else status)


def _first_line(answer: Any) -> str:
    """The first line of an HTTP error's body, read only as far as a short one goes."""
    with contextlib.suppress(Exception):
        body = answer.read(512).decode("utf-8", errors="replace")
        for line in body.splitlines():
            if line.strip():
                return str(line.strip()[:200])
    return ""


def _snapshot_failure(error: BaseException) -> tuple[str, bool]:
    """What to say about a snapshot that did not come, and whether `connect()` should wait for
    another.

    It waits through two failures, the two a camera that is coming up answers with: a 503, and a
    request that ran out of time. Any other is a URL with nothing behind it, a refused
    connection, a 404 or a page that is not a picture, and waiting longer would only put off
    saying so."""
    if isinstance(error, SnapshotRefused | urllib.error.HTTPError):
        return str(error), error.code == HTTPStatus.SERVICE_UNAVAILABLE
    reason = error.reason if isinstance(error, urllib.error.URLError) else error
    if isinstance(reason, TimeoutError):
        return f"no answer within {SNAPSHOT_TIMEOUT_S:g} s", True
    return str(reason) or type(reason).__name__, False


def _flag(safety: dict[str, Any], key: str) -> bool | None:
    """One flag of a state frame's `safety` block as the shim reported it: True or False when it
    is a JSON boolean, and None for anything else, null, a number or a string included, which
    reads exactly as the key being absent. A null `fallen` read as falsy was a fall detector
    saying all clear, and a null `picked_up` was a pick-up detector saying nobody holds the
    robot, when neither had said anything."""
    value = safety.get(key)
    return value if isinstance(value, bool) else None


def posture_of(
    policy: str | None, safety: dict[str, Any], *, skills: Sequence[str] = ()
) -> tuple[Posture, str | None]:
    """Where the robot is, from one fresh state frame, and why it is `unknown` when it is.

    An allowlist, because the label names whatever drove the tick and a peer has more labels than
    quackd will ever have read: two stand the robot up, one seats it, and every other one reads as
    `unknown` with a reason naming it.

    The order is the safety order. Somebody holding the robot comes first, before `fallen`: a
    robot held at an angle reads as fallen, and "fallen" points the pilot at `stand_up`, which
    asks the robot to drive legs that are in a person's hands. `fallen` comes before the label,
    because a robot on the floor is down whatever is driving it. `safety.limp`, the gains dropped
    so the robot yields, is next, and then the label. Each flag counts only as the JSON boolean
    true (`_flag`).
    """
    if _flag(safety, "picked_up") is True:
        return "unknown", "somebody is holding Huebot (safety.picked_up)"
    if _flag(safety, "fallen") is True:
        return "fallen", None
    if _flag(safety, "limp") is True:
        return "unknown", "Huebot's gains are dropped so it yields (safety.limp)"
    # A skill reports its own name, so a skill called `walk` would otherwise stand the robot up
    # halfway through it. The robot's own list is read before the allowlist.
    if policy and policy in skills:
        return "unknown", _not_standing_because(policy, skills)
    if policy in up.SITTING_LABELS:
        return "sitting", None
    if policy in up.STANDING_LABELS:
        return "standing", None
    return "unknown", _not_standing_because(policy, skills)


def _not_standing_because(policy: str | None, skills: Sequence[str]) -> str:
    """Why a label that neither stands nor seats the robot leaves its posture unknown."""
    if not policy:
        return "the shim's state frame names no policy"
    said = f"the shim reports policy {policy!r}"
    if policy in skills:
        if policy in up.STANDING_LABELS or policy in up.SITTING_LABELS:
            return f"{said}, which is also one of its skills, so quackd cannot tell which it is"
        return f"{said} (one of its skills, which holds the legs until it ends)"
    if policy in up.NOT_STANDING_LABELS:
        return f"{said} ({up.NOT_STANDING_LABELS[policy]})"
    if policy.endswith(up.UNWIND_SUFFIX):
        return f"{said} (a skill unwinding back to the gait)"
    return f"{said}, which quackd has not read, so it cannot tell where Huebot is"


class StateRead(NamedTuple):
    """The newest state frame, read once for both `get_state` and the hold on `robot.move`, so
    what the pilot is told about the robot and what the wire is allowed cannot disagree."""

    state: dict[str, Any]
    age: float | None
    fresh: bool
    """A frame recent enough to believe, carrying a `safety` block that says `fallen`."""
    policy: str
    fallen: bool
    picked_up: bool | None
    """None when nothing says either way: no fresh frame, or a frame without the key. False only
    when a frame says so."""
    posture: Posture
    reason: str | None
    """Why `posture` is `unknown`, or None when it is not."""


def _pickup_detection(read: StateRead) -> bool | None:
    """Whether a fresh frame's `safety` block reports `picked_up` as a JSON boolean, or None with
    no fresh frame.

    That it reports one and not which: a peer that has no pick-up detector leaves the key out,
    which a reader takes for never picked up (`up.SAFETY_PICKED_UP`), and one that writes null or
    anything else but true or false has said no more (`_flag`). That reading is exactly what
    quackd must not take on trust, since a robot in somebody's hands is then sent walks like any
    other."""
    safety = read.state.get("safety") if read.fresh else None
    return (_flag(safety, "picked_up") is not None) if isinstance(safety, dict) else None


def _battery_percent(health: Any) -> float | None:
    """`robot.health`'s `battery.percent`, when it is a finite number, and None for anything
    else: an answer that is not an object, a `battery` that is not one, or a `percent` that is
    not a number. A `battery` of `80` used to raise out of every state read, and so out of every
    verb, since the executor reads the state before each one."""
    battery = health.get("battery") if isinstance(health, dict) else None
    percent: Any = battery.get("percent") if isinstance(battery, dict) else None
    return float(percent) if _finite(percent) else None


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

    frames_upright = False
    """No camera quackd reads on a Huebot says its frames are upright or when they were taken,
    which `watch` would need, and Huebot has no `watch`."""

    def __init__(
        self,
        address: str | None = None,
        *,
        camera_url: str | None = None,
        api_version: int = int(up.API_VERSION.name),
        request_timeout_s: float = 2.0,
        state_hz: int = STATE_HZ,
        camera_fps: float = CAMERA_FPS,
        camera_wait_s: float = FIRST_FRAME_WAIT_S,
    ) -> None:
        self.address = address or None
        """Where the shim listens, or None, which `connect()` refuses (`NO_ADDRESS`). Built by
        `make_transport`, which every run does, it is never None: that refuses first."""
        self.camera_url = camera_url
        self.api_version = api_version
        self.request_timeout_s = request_timeout_s
        self.state_hz = state_hz
        self.camera_fps = camera_fps
        """How often the camera is asked for a frame, and what sets how old one may be."""
        self.camera_wait_s = camera_wait_s
        """How long `connect()` waits for the camera's first frame (`_await_first_frame`)."""
        self._camera_task: asyncio.Task[None] | None = None
        self._frame: Image.Image | None = None
        self._frame_at: float | None = None
        self._frame_error: str | None = None
        """Why the newest snapshot did not come, or None since one did."""
        self._frame_retryable = False
        """Whether `_frame_error` is a failure `connect()` waits through (`_snapshot_failure`)."""
        self._fetch_s = 0.0
        """How long the newest fetch took, answered or not, which the snapshot loop waits out
        before it asks again."""
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._pump: asyncio.Task[None] | None = None
        self._pending: dict[int, asyncio.Future[Any]] = {}
        self._lines = 0
        """How many lines quackd has written on this connection: each line's place on the wire."""
        self._asked: dict[int, tuple[int, str]] = {}
        """The line each request still waiting on its answer went out as, and its method, by its
        id: what its answer says the shim has read (`_heard`), and, for a stop, which walks the
        answer ends (`_closed`)."""
        self._lost: str | None = None
        """Why the link ended, once the shim closed it or the socket failed, which every call
        made after says at once instead of waiting out `request_timeout_s` (`_lose`)."""
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
        self._walking: set[asyncio.Task[Any] | None] = set()
        """The verbs that have walked, by the task each runs in: every one that has sent a
        `robot.move` and not yet ended (`_prune`). What a hold is kept for (`_hold_back`)."""
        self._walk_asked: set[asyncio.Task[Any] | None] = set()
        """The verbs the shim has been asked about since their last walk began, one question a
        walk (`_ask`). A walk is a verb's twists up to a stop, its own or another call's, so a
        verb leaves this when a stop ends its walk, and its next twist asks again."""
        self._held: dict[asyncio.Task[Any] | None, str] = {}
        """The verbs `robot.move` is held back for, by the task each runs in, and why: the robot
        stopped standing while they walked, or the shim stopped showing the twists it was sent
        (`_judge`). Each is let go when it ends (`_prune`), and by nothing else, since a verb
        that stops between its turns, as `search_scan` does, goes on after its stop."""
        self._shown_at: dict[asyncio.Task[Any] | None, float] = {}
        """When the newest twist each verb sent that a state frame has shown was sent, on the
        monotonic clock, by the task it runs in (`_settle`, `twist_shown_since`)."""
        self._unshown: deque[_Sent] = deque(maxlen=256)
        """Every twist sent since the newest one a state frame has shown, oldest first. Bounded
        only so that a stream that never echoes cannot grow it: the verdict empties it long
        before."""
        self._frames_read = 0
        """How many `robot.state` frames this connection has read, for `_not_taken`."""
        self._probe: asyncio.Task[None] | None = None
        """The call out to find whether the shim has read the twists before it (`_ask`), if any."""
        self.move_echo: dict[str, Any] | None = None
        """What the shim's own state last said about the twists quackd sent (`up.MOVE_ECHO`):
        `taken` True with the twist it showed and how long after it went out the frame that
        showed it arrived, or False with what it showed instead when `robot.move` was held back
        for it. None until a frame has said either."""
        self.shim: dict[str, Any] | None = None
        """What the shim answered `hello` with, beside what quackd read, as `extras.shim`."""
        self.runtime_warning: str | None = None
        """Why the shim's API version is worth a word, or None when it is the one quackd read.
        Logged at connect, carried in `extras.shim`, and printed by `doctor`, which reads it off
        any transport under this name."""
        self.camera_working = False
        """Whether a frame actually arrived during connect. What the manifest narrows on."""
        self.subscribed: dict[str, Any] | None = None
        """`robot.subscribe`'s answer, which names the skills this robot actually has."""

    # ── wire ────────────────────────────────────────────────────────────────────────

    async def connect(self) -> None:
        if not self.address:
            raise TransportError(NO_ADDRESS)
        kind, host, port = parse_address(self.address)
        try:
            if kind == "unix":
                if sys.platform == "win32":
                    raise TransportError(
                        "unix sockets are not available on Windows; forward the shim's socket "
                        "with `ssh -L 9870:<socket> <huebot>` and use --address tcp://127.0.0.1:9870"
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
        self._pump = asyncio.create_task(self._read_loop(), name="quackd-huebot-pump")
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
        # The number decides only whether quackd has read anything this peer speaks. Above the
        # floor a difference is served, as upstream's own clients serve one, because what
        # refuses a changed call is the call, by name (up.VERSION_SKEW).
        if not isinstance(remote, int) or isinstance(remote, bool):
            raise TransportError(
                f"the shim's hello carried api_version {remote!r}, which is not a version "
                f"number, so whatever answered at {self.address} is not a peer quackd can check "
                f"against the API v{self.api_version} it read; refusing"
            )
        if remote < floor:
            raise TransportError(
                f"the shim speaks API v{remote}, older than API v{floor}, the oldest quackd has "
                f"read ({up.IPC_PROTO}); refusing. Huebot's shim is asked to answer with API "
                f"v{self.api_version}, the version quackd read at {up.RELEASE}"
            )
        if remote != self.api_version:
            self.runtime_warning = self._skew_warning(remote)
            log.warning("%s", self.runtime_warning)
        self.shim = {
            "api_version": remote,
            "read": self.api_version,
            "floor": floor,
            "daemon_version": self.hello.get("daemon_version"),
            "revision": self.hello.get("revision"),
            "warning": self.runtime_warning,
        }
        # Nothing arrives until we ask (up.STATE_NEEDS_SUBSCRIBE), and everything that decides
        # whether the robot may walk reads the frames this starts: posture, `safety.fallen`, the
        # preconditions in the manifest. The answer also names the robot's real skills, which
        # are the only ones quackd offers a verb for and sends a `robot.do` for.
        result = await self.request(up.ROBOT_SUBSCRIBE.name, {"hz": self.state_hz})
        self.subscribed = result if isinstance(result, dict) else {}
        await self._await_first_state()
        # `camera_working` is what the manifest narrows on, and it has to mean "a frame
        # arrived", not "a URL was typed": a typo'd host would otherwise still advertise
        # observe, go_to, search_scan and approach_and, every one of which can then only answer
        # "no camera".
        if self.camera_url:
            self._camera_task = asyncio.create_task(
                self._camera_loop(), name="quackd-huebot-camera"
            )
            self.camera_working = await self._await_first_frame()

    def _skew_warning(self, remote: int) -> str:
        """What to tell the person running quackd about a shim on an API version quackd did not
        read, newer or older, with what quackd does about the difference."""
        build = (self.hello or {}).get("daemon_version")
        which = f"API v{remote}" + (f" (build {build})" if build else "")
        read = f"the API v{self.api_version} quackd read at {up.RELEASE} on {up.READ_ON}"
        if remote > self.api_version:
            return (
                f"Huebot's shim speaks {which}, newer than {read}. It is served, as upstream's "
                "own clients serve one, because a call whose shape moved refuses itself by name: "
                "a verb that fails naming METHOD_NOT_FOUND or INVALID_PARAMS is that, and a "
                "robot.move the robot's state does not show is held back (extras.move_echo). "
                f"The shim is asked to answer with API v{self.api_version}."
            )
        return (
            f"Huebot's shim speaks {which}, older than {read}. quackd serves every version from "
            f"API v{int(up.API_FLOOR.name)}, the oldest it has read end to end, but has not read "
            "this one: a call it lacks fails the verb naming the call, and what it does not "
            "report reads as absent, safety.picked_up among them (extras.pickup_detection). "
            f"The shim is asked to answer with API v{self.api_version}."
        )

    async def _await_first_frame(self) -> bool:
        """Wait for the snapshot loop's first frame, so `connect()` knows if there is a camera.

        Whether a run can see is decided here, once: the manifest offers `observe`, `go_to`,
        `search_scan` and `approach_and` only on a camera that worked at connect. So the wait
        lasts `camera_wait_s` through the failures of a camera that is coming up, a 503 and a
        request that ran out of time, and any other failure ends it at once
        (`_snapshot_failure`)."""
        deadline = time.monotonic() + self.camera_wait_s
        while self._frame is None:
            if self._frame_error is not None and not self._frame_retryable:
                break
            if time.monotonic() >= deadline:
                break
            await asyncio.sleep(0.02)
        return self._frame is not None

    async def _await_first_state(self, timeout_s: float = 1.0) -> bool:
        """Wait briefly for the first state frame, so `connect()` returns a robot we can see.

        The subscribe answer and the first state notification are two messages and nothing
        orders them, so without this the first `get_state()` after connecting can honestly
        report `unknown` on a robot that is about to start streaming. Not fatal if it never
        comes: `get_state` already reads that as unknown and the preconditions refuse.
        """
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self._state_arrived.wait(), timeout=timeout_s)
        return self._last_state_at is not None

    async def close(self) -> None:
        if self._camera_task is not None:
            self._camera_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._camera_task
            self._camera_task = None
        # before the pump, whose answer the question is waiting on
        if self._probe is not None:
            self._probe.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._probe
            self._probe = None
        self._walking.clear()
        self._walk_asked.clear()
        self._held.clear()
        self._shown_at.clear()
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
        """Read the shim's lines until the link ends: hand each answer to the request waiting on
        it, and keep each state frame.

        No one line ends it. A line quackd cannot read is skipped, or answered as the error it
        is, and a line whose handling raises is skipped too. Only the end of the link ends it,
        the shim closing the socket or the socket failing, and that fails every request still
        waiting, and every one after, with why (`_lose`)."""
        assert self._reader is not None
        while True:
            try:
                line = await self._reader.readline()
            except ValueError as e:
                # A line over READER_LIMIT. `readline` has already let go of it, so the next read
                # starts past it, or at a tail of it that the parse below throws away.
                log.warning("skipped a line from the shim over %d bytes: %s", READER_LIMIT, e)
                continue
            except OSError as e:
                self._lose(f"the link to the shim failed: {e}")
                return
            if not line:
                self._lose("the shim closed the connection")
                return
            try:
                self._take_line(line)
            except Exception as e:  # one line quackd mishandles must not cut the robot off
                log.warning("skipped a line from the shim that quackd could not read: %r", e)

    def _take_line(self, line: bytes) -> None:
        """One line from the shim: an answer to a request, or a notification."""
        try:
            msg = json.loads(line)
        except ValueError:  # not JSON, or the tail of a skipped line cut mid-character
            return
        if not isinstance(msg, dict):
            return
        if msg.get("id") is not None and ("result" in msg or "error" in msg):
            # the shim echoes the integer id quackd sent, and an id that is anything else
            # answers nothing quackd is waiting on
            if (req_id := _id_of(msg["id"])) is not None:
                self._answered(req_id, msg)
        elif "method" in msg:
            if msg["method"] == up.ROBOT_STATE.name:
                # Before the queue push, deliberately: the queue is bounded and drops when
                # nobody drains it, and freshness must not depend on anyone consuming.
                self._took_state(msg.get("params"))
            with contextlib.suppress(asyncio.QueueFull):
                self._notifications.put_nowait(msg)

    def _answered(self, req_id: int, msg: dict[str, Any]) -> None:
        """Hand the shim's answer to the request waiting on it, and note what an answer says it
        has read (`_heard`), an error as much as a result, and, for a stop, which walks it has
        ended (`_closed`). Noted here, as the line is read, and not where the call that waited
        on it wakes: the reader can take several frames off the socket before that call runs
        again."""
        pending = self._pending.pop(req_id, None)
        if (asked := self._asked.pop(req_id, None)) is not None:
            line, method = asked
            self._heard(line)
            if method == up.ROBOT_STOP.name:
                self._closed(line)
        if pending is None or pending.done():
            return
        error = msg.get("error")
        if error is None and "result" in msg:
            # a JSON-RPC 1.0 peer writes a null error beside every result
            pending.set_result(msg.get("result"))
        elif isinstance(error, dict):
            code = error.get("code")
            number = code if isinstance(code, int) and not isinstance(code, bool) else None
            pending.set_exception(TransportError(f"{code}: {error.get('message')}", code=number))
        else:
            pending.set_exception(
                TransportError(
                    f"the shim answered with an error quackd cannot read: {error!r:.200}"
                )
            )

    def _lose(self, why: str) -> None:
        """The link has ended: every request still waiting fails with why, and so does every
        call made after, at once."""
        self._lost = why
        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(TransportError(why))
        self._pending.clear()
        self._asked.clear()

    def _took_state(self, params: Any) -> None:
        """One `robot.state` frame read: the newest state, when it arrived, how many have, which
        of the twists quackd sent it shows the shim has taken (`_settle`), and whether it says
        that the verbs walking now should walk no further (`_judge`).

        Judged here as well as when a twist goes out, because a verb is not always sending. A
        `go_to` waiting on a slow frame sends nothing for as long as the detector takes, and a
        robot picked up and put back down inside that wait would never be seen held."""
        self._last_state = params if isinstance(params, dict) else {}
        self._last_state_at = time.monotonic()
        self._frames_read += 1
        self._state_arrived.set()
        self._settle(self._last_state, self._last_state_at)
        self._prune()
        walking = any(task not in self._held for task in self._walking)
        if walking and (hold := self._judge()) is not None:
            self._hold_back(hold)

    def _write(self, obj: dict[str, Any]) -> int:
        """Put one line on the wire, and return its place there: how many lines quackd had
        written on this connection once it was, which an answer is compared with (`_heard`)."""
        if self._writer is None:
            raise TransportError("not connected")
        try:
            self._writer.write((json.dumps(obj, separators=(",", ":")) + "\n").encode())
        except (OSError, RuntimeError) as e:
            raise TransportError(f"{obj.get('method')}: the link to the shim is gone: {e}") from e
        self._lines += 1
        return self._lines

    async def _drain(self, method: str) -> None:
        """Wait for the line just written to be taken by the socket, which raises once the link
        has dropped, as `TransportError`, which is all every caller here catches."""
        writer = self._writer
        if writer is None:
            raise TransportError(f"{method}: not connected")
        try:
            await writer.drain()
        except (OSError, RuntimeError) as e:
            raise TransportError(f"{method}: the link to the shim is gone: {e}") from e

    async def request(self, method: str, params: dict[str, Any] | None = None) -> Any:
        """Send one call and wait for the shim's answer to it.

        Every way it can fail raises `TransportError`, which is all any caller here catches.
        After the link has ended it fails at once, saying why, rather than wait out
        `request_timeout_s` for an answer nobody can send."""
        if self._lost is not None:
            raise TransportError(f"{method}: {self._lost}")
        req_id = self._next_id
        self._next_id += 1
        fut: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        msg: dict[str, Any] = {"jsonrpc": up.JSONRPC_VERSION, "id": req_id, "method": method}
        if params is not None:
            msg["params"] = params
        self._pending[req_id] = fut
        try:
            # its place on the wire, before anything is awaited: on a loopback the answer can
            # come back while the line is still draining, and what it says the shim read needs it
            self._asked[req_id] = (self._write(msg), method)
            await self._drain(method)
            return await asyncio.wait_for(fut, timeout=self.request_timeout_s)
        except TimeoutError as e:
            raise TransportError(f"{method}: no answer within {self.request_timeout_s:g}s") from e
        except TransportError as e:
            if e.code in (up.ERR_METHOD_NOT_FOUND, up.ERR_INVALID_PARAMS):
                raise TransportError(self._refused_by_name(method, e), code=e.code) from e
            raise
        finally:
            # answered, timed out or never written, a request is forgotten once it is over
            self._pending.pop(req_id, None)
            self._asked.pop(req_id, None)

    def _refused_by_name(self, method: str, error: TransportError) -> str:
        """The shim's own words for a call it refused by its name or its params, with what the
        two versions were, since that is the refusal upstream put where a version gate would be
        (`up.VERSION_SKEW`) and the version is half of what it means.

        `hello` that the socket does not have is a socket that is not the shim's. So is one that
        has no `robot.subscribe`, the first `robot.*` call the handshake makes, which every
        robotd from `up.API_FLOOR` up has and Huebot's shim is asked to answer: without it no
        state ever arrives, and nothing could say whether the robot may walk."""
        said = f"{method}: {error}"
        floor = int(up.API_FLOOR.name)
        if method == up.HELLO.name and error.code == up.ERR_METHOD_NOT_FOUND:
            return (
                f"{said}. Whatever answered at {self.address} has no {method}, so it is not "
                "Huebot's shim: point --address at the socket the shim listens on"
            )
        if method == up.ROBOT_SUBSCRIBE.name and error.code == up.ERR_METHOD_NOT_FOUND:
            return (
                f"{said}. Whatever answered at {self.address} answered {up.HELLO.name} but has "
                f"no {method}, which every robotd from API v{floor} up has, so it is not robotd "
                "and not a shim quackd can drive: Huebot's shim has to answer it, since no state "
                "arrives until it does (docs/adapters/huebot/README.md)"
            )
        remote = (self.shim or {}).get("api_version")
        read = f"API v{self.api_version} at {up.RELEASE}"
        kind = (
            "a call this shim does not have"
            if error.code == up.ERR_METHOD_NOT_FOUND
            else "params this shim does not take"
        )
        if remote is None:
            return f"{said}. That is {kind}, before it said its version; quackd read {read}"
        if remote != self.api_version:
            return (
                f"{said}. That is {kind}: the shim speaks API v{remote} and quackd read {read}, "
                "and the call differs between the two"
            )
        return (
            f"{said}. That is {kind}, though it speaks the {read} quackd read, so it does not "
            "answer this call as robotd does (docs/adapters/huebot/README.md)"
        )

    async def notify(self, method: str, params: dict[str, Any]) -> int:
        """Send one notification, which nothing answers, and return its place on the wire
        (`_write`). Fails with `TransportError` however the link has, as `request` does."""
        if self._lost is not None:
            raise TransportError(f"{method}: {self._lost}")
        line = self._write({"jsonrpc": up.JSONRPC_VERSION, "method": method, "params": params})
        await self._drain(method)
        return line

    # ── the camera ──────────────────────────────────────────────────────────────────

    async def _fetch_frame(self) -> Image.Image:
        url = self.camera_url or ""

        def fetch() -> bytes:
            try:
                with urllib.request.urlopen(url, timeout=SNAPSHOT_TIMEOUT_S) as resp:
                    return resp.read()
            except urllib.error.HTTPError as e:
                # read here, in the thread and while the answer is open: the body says why
                raise SnapshotRefused(e.code, str(e.reason or ""), _first_line(e)) from e

        data = await asyncio.to_thread(fetch)
        return Image.open(io.BytesIO(data)).convert("RGB")

    async def _camera_loop(self) -> None:
        """Keep the newest frame, on a timer, so `get_frame` is a memory read.

        Capture on a clock rather than on request, so a slow or broken camera cannot stall the
        caller and a run asking for a frame every step does not pay an HTTP round trip every
        step. A failure is kept, with whether `connect()` waits through it, until the next frame
        comes. Each frame is stamped with the moment its request went out, which its age counts
        from (`camera_stale_after_s`)."""
        period = 1.0 / max(0.1, self.camera_fps)
        while True:
            started = time.monotonic()
            try:
                frame = await self._fetch_frame()
            except Exception as e:  # a camera hiccup must not end a run
                self._frame_error, self._frame_retryable = _snapshot_failure(e)
            else:
                self._frame, self._frame_at = frame, started
                self._frame_error, self._frame_retryable = None, False
            self._fetch_s = time.monotonic() - started
            await asyncio.sleep(max(0.0, period - self._fetch_s))

    async def get_frame(self) -> Image.Image | None:
        """The newest frame, or None when there is no camera to read. Never raises: a failed
        snapshot is what `camera_health()` and `camera_error` say, not the end of a run."""
        if not self.camera_url:
            return None  # the shim's socket carries no picture, so --camera-url is the only one
        if self._camera_task is None:  # not connected: fetch once rather than nothing
            started = time.monotonic()  # what the frame is no older than, as the loop stamps it
            with contextlib.suppress(Exception):
                self._frame = await self._fetch_frame()
                self._frame_at = started
        return None if self.frame_age_s() is None else self._frame

    def frame_age_s(self) -> float | None:
        """Seconds since the newest usable frame was asked for, or None if there is none to
        steer on: nothing has ever arrived, or what we have is too old to act on, which is what
        turns a dead camera back into a stop instead of blind walking."""
        if self._frame is None or self._frame_at is None:
            return None
        age = time.monotonic() - self._frame_at
        return None if age > camera_stale_after_s(self.camera_fps) else age

    @property
    def camera_error(self) -> str | None:
        """Why `get_frame` has no frame to hand out just now, or None when it has one, and when
        no `--camera-url` named a camera, which `observe` says in words of its own."""
        if not self.camera_url:
            return None
        if self.frame_age_s() is not None:
            return None
        if self._frame_error is not None:
            return f"GET {self.camera_url}: {self._frame_error}"
        if self._frame_at is None:
            return f"no frame has come from {self.camera_url} yet"
        age = time.monotonic() - self._frame_at
        return (
            f"the newest frame from {self.camera_url} is {age:.1f} s old, older than the "
            f"{camera_stale_after_s(self.camera_fps):g} s quackd steers on"
        )

    def camera_health(self) -> dict[str, Any]:
        """What the camera is doing, for `doctor` and for the run's own state. `error` is the
        newest fetch's failure, kept until a frame comes, which `doctor` prints under the
        camera's row."""
        if not self.camera_url:
            return {"configured": False}
        age = None if self._frame_at is None else round(time.monotonic() - self._frame_at, 2)
        return {
            "configured": True,
            "url": self.camera_url,
            "ok": self._frame is not None,
            "age_s": age,
            "size": list(self._frame.size) if self._frame is not None else None,
            "fps": self.camera_fps,
            "error": self._frame_error,
        }

    # ── the state ───────────────────────────────────────────────────────────────────

    def state_age_s(self) -> float | None:
        """Seconds since the last `robot.state` frame, or None if none has ever arrived."""
        if self._last_state_at is None:
            return None
        return time.monotonic() - self._last_state_at

    def _skills(self) -> tuple[str, ...]:
        """The skill names the shim answered `robot.subscribe` with (`up.SKILL_SOURCE`), and none
        before it has answered or when its answer carries no list."""
        answer = self.subscribed if isinstance(self.subscribed, dict) else {}
        skills = answer.get("skills")
        return tuple(str(s) for s in skills) if isinstance(skills, list) else ()

    @property
    def skills(self) -> tuple[str, ...] | None:
        """The skills Huebot's shim listed, which are the only ones a `robot.do` goes out for and
        the only ones `HuebotAdapter` offers a verb for (`up.SHIM_SKILLS`), or None before
        `robot.subscribe` has been answered. An answer with no `skills` list lists none."""
        return None if self.subscribed is None else self._skills()

    def _read(self) -> StateRead:
        """The newest state frame and what it says about the robot, without asking the shim
        anything: `robot.move` is held back on this, every tenth of a second, mid-verb."""
        # A robot nobody is watching reads as unknown, never as standing. `fallen` is a plain
        # bool on the wire and in DuckState, so silence and "upright" are the same value there:
        # the difference has to live in `posture` and in `fall_detection`, or a stream that
        # never started looks exactly like a robot that is fine.
        age = self.state_age_s()
        # a link that has ended sends no frame again, so the last one stops being believed at
        # once rather than once it has aged
        arriving = (
            self._lost is None and age is not None and age <= state_stale_after_s(self.state_hz)
        )
        state = (self._last_state or {}) if arriving else {}
        safety = state.get("safety")
        told = safety if isinstance(safety, dict) else {}
        named = state.get("policy")
        policy = str(named or "unknown")
        fallen = _flag(told, "fallen") is True
        # Frames arriving is not the same as frames that say anything about falling. A frame
        # without a safety block, or whose `fallen` is not a JSON boolean, would otherwise read
        # as "not fallen" on the strength of a key that was never there, or that said nothing.
        if not (arriving and _flag(told, "fallen") is not None):
            if self._lost is not None:
                silent = f"the link to the shim is gone ({self._lost})"
            elif age is None:
                silent = "no robot.state frame has arrived"
            elif not arriving:
                silent = f"the newest robot.state frame is {age:.1f} s old"
            elif not isinstance(safety, dict):
                silent = "the shim's state frame carries no safety block"
            elif "fallen" not in safety:
                silent = "the shim's state frame carries no safety.fallen"
            else:
                silent = (
                    f"the shim's state frame says safety.fallen is {json.dumps(safety['fallen'])}, "
                    "not true or false, which quackd reads as not reported"
                )
            return StateRead(state, age, False, policy, fallen, None, "unknown", silent)
        posture, why = posture_of(str(named) if named else None, told, skills=self._skills())
        # Absent is not False, and neither is null: reading a key that says nothing as "nobody
        # holds the robot" is the reading `stand_up` refuses to act on.
        picked_up = _flag(told, "picked_up")
        return StateRead(state, age, True, policy, fallen, picked_up, posture, why)

    async def get_state(self) -> DuckState:
        health = self._last_health
        if health is None:
            with contextlib.suppress(TransportError):
                health = await self.request(up.ROBOT_HEALTH.name)
                self._last_health = health if isinstance(health, dict) else None

        read = self._read()
        battery = _battery_percent(health)
        hold = self._hold_record()
        posture, reason = read.posture, read.reason
        if hold is not None and posture == "standing":
            # A robot that stands while quackd holds back the walk of a verb still running is
            # not a robot ready to walk: it reads as unknown until that verb ends.
            posture, reason = "unknown", f"{HELD_FOR_A_VERB}: {hold}"
        return DuckState(
            t=self.now(),
            policy=read.policy,
            posture=posture,
            fallen=read.fallen,
            battery_percent=battery,
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
                "posture_reason": reason,
                # Why robot.move is held back for a verb still running, or None while it is held
                # back for none (`_move_refusal`). `stand_up` refuses while it is set.
                "move_hold": hold,
                # True while somebody holds the robot, False when a fresh frame says nobody does,
                # and None when nothing says either way.
                "picked_up": read.picked_up,
                # Whether the shim reports safety.picked_up at all. None while no fresh frame
                # says either way.
                "pickup_detection": _pickup_detection(read),
                # What the shim answered hello with, beside what quackd read, and the warning
                # when the two differ.
                "shim": self.shim,
                # What the shim's own state last said about the twists quackd sent.
                "move_echo": self.move_echo,
                # Where the head points from the trunk, in degrees, positive left, off the
                # head_yaw joint; None from a frame that does not carry it.
                "head_yaw_deg": _head_yaw(read.state),
                "state_age_s": round(read.age, 3) if read.age is not None else None,
                "subscribed": self.subscribed,
                "camera": self.camera_health(),
                "stop_error": self.stop_error,
                # What quackd stands in for on this robot, which the pilot is told not to take
                # for the robot's own work: stand_up, which asks the shim to drive and is all
                # quackd has for a robot on the floor.
                "assumptions": [up.STAND_UP_RPC.name],
            },
        )

    @property
    def safety(self) -> dict[str, Any]:
        """What the shim's own frames say it can detect, and what quackd is holding back, for
        `doctor`, which reads a transport's `safety` for its go/no-go rows: whether it reports
        `safety.picked_up` at all, which is left out until a fresh frame has said either way,
        and `move_hold`, why `robot.move` is held back for a verb still running, or None."""
        detection = _pickup_detection(self._read())
        told: dict[str, Any] = {} if detection is None else {"pickup_detection": detection}
        told["move_hold"] = self._hold_record()
        return told

    # ── the hold on robot.move ──────────────────────────────────────────────────────

    def _move_refusal(self, task: asyncio.Task[Any] | None) -> str | None:
        """Why the next `robot.move` from the verb running as `task` must not go out, or None
        when it may.

        It may only while the newest state reads standing, and once the robot has stopped
        standing while a verb walked, that verb sends none until it ends. The preconditions
        cannot do this alone: they are read once, when a verb starts. Every verb that walks ends
        at the first twist refused, stops, and says why (`quackd.verbs.core`), and the hold is
        what makes that first refusal stick for as long as the verb runs. It belongs to the verbs
        it was set for, by the task each runs in (`_hold_back`), and only a verb's end lets it go
        (`_prune`): no stop does, the pilot's included.

        Nothing is sent in place of the refused twist. The robot is asked to zero a twist nobody
        refreshes within its deadman window (`up.SHIM_DEADMAN`), and a zero sent here would be
        one more `robot.move` to a robot quackd has just decided not to drive. The same hold
        covers a robot that is standing and a shim that is not taking the twists (`_not_taken`).
        """
        self._prune()
        if task not in self._held and (hold := self._judge()) is not None:
            self._hold_back(hold, task)
        if (hold := self._held.get(task)) is not None:
            return f"{hold}, so quackd sends no robot.move until the verb that was moving it ends"
        return None

    def _judge(self) -> str | None:
        """Why the newest state says no verb should walk on, or None when it says nothing of
        the kind: the robot is not standing, or the shim has not taken the twists it was sent."""
        read = self._read()
        if read.posture != "standing":
            why = read.reason or (
                "it has fallen" if read.posture == "fallen" else f"it is {read.posture}"
            )
            return f"Huebot stopped standing ({why})"
        if (unseen := self._not_taken()) is not None:
            # written off: what the shim showed instead is in `move_echo`, which a frame showing
            # one of them later must not overwrite while the hold its verdict set stands
            self._unshown.clear()
            return f"Huebot's shim has not taken robot.move: {unseen}"
        return None

    def _hold_back(self, hold: str, *also: asyncio.Task[Any] | None) -> None:
        """Hold `robot.move` back for every verb that has walked and is still running, and for
        `also`, until each ends. A robot that stopped standing stopped for all of them, and a
        shim that does not take one client's twists takes none of them. A verb held already
        keeps the reason it was held for."""
        fresh = [task for task in dict.fromkeys((*self._walking, *also)) if task not in self._held]
        for task in fresh:
            self._held[task] = hold
        if fresh:
            log.warning("robot.move held back until the verb that was walking ends: %s", hold)

    def _release(self, task: asyncio.Task[Any] | None) -> None:
        """The verb that ran as `task` has ended: it walks no more, the hold on it is lifted, and
        the twists it sent that no frame has judged are forgotten with it, so the next verb is
        judged on its own twists and starts with no hold. Every other verb keeps its hold. The
        executor runs every verb as a task of its own, so a task that has ended is a verb that
        has, whichever way it ended."""
        self._walking.discard(task)
        self._walk_asked.discard(task)
        self._held.pop(task, None)
        self._shown_at.pop(task, None)
        if any(sent.task is task for sent in self._unshown):
            kept = [sent for sent in self._unshown if sent.task is not task]
            self._unshown = deque(kept, maxlen=self._unshown.maxlen)

    def _prune(self) -> None:
        """Forget the verbs that have ended (`_release`). Read before every judgement and every
        refusal, and as each frame arrives, so a hold kept for a verb that is gone holds back
        nothing, the next verb least of all."""
        known = {*self._walking, *self._walk_asked, *self._held, *self._shown_at}
        for task in [t for t in known if t is not None and t.done()]:
            self._release(task)

    def _hold_record(self) -> str | None:
        """`extras.move_hold`: why `robot.move` is held back for a verb still running, or None
        while it is held back for none."""
        self._prune()
        return next(iter(self._held.values()), None)

    def twist_shown_since(self, since: float) -> bool:
        """Whether the shim's state has shown a twist the calling verb sent no earlier than
        `since`, on this transport's clock (`now`), which is how `search_scan` counts the turns
        it says it made (`quackd.transport.base.twist_shown_since_of`). A twist accepted here is
        a line written to the socket, which nothing answers, so a turn quackd sent is not a turn
        the robot took, and a frame that shows one shows that the shim took every one before
        it (`_settle`)."""
        shown = self._shown_at.get(asyncio.current_task())
        return shown is not None and shown - self._t0 >= since

    def _settle(self, state: dict[str, Any], arrived: float) -> None:
        """Strike off every twist a state frame shows the shim has taken.

        The frame's `move.requested` is the twist it was last asked for (`up.MOVE_ECHO`). Matched
        against the newest of the unshown twists first, since twists are taken in the order they
        were sent and a frame that shows one has taken every one before it. A frame that shows
        only an older twist than one still waiting strikes off nothing newer than the one it
        shows."""
        if not self._unshown:
            return
        asked = _twist_of(state.get("move"))
        if asked is None:
            return
        for i in range(len(self._unshown) - 1, -1, -1):
            shown = self._unshown[i]
            if _same_twist(shown.twist, asked):
                break
        else:
            return
        for _ in range(i + 1):
            taken = self._unshown.popleft()
            self._shown_at[taken.task] = taken.at
        self.move_echo = {
            "taken": True,
            "twist": list(shown.twist),
            "after_s": round(arrived - shown.at, 3),
        }

    def _heard(self, line: int) -> None:
        """The shim has answered a call quackd wrote as line `line`, so it has read every line
        before it (`up.LINES_IN_ORDER`), and every twist among them can show from the frames
        that follow. Each one not yet marked is marked with how many frames had been read."""
        for sent in self._unshown:
            if sent.heard is None and sent.line < line:
                sent.heard = self._frames_read

    def _closed(self, line: int) -> None:
        """The shim has answered a stop quackd wrote as line `line`, so every twist written
        before it that no frame has shown can show in no frame read from now on (`_Closed`):
        each is marked with how many frames had been read, and with what the newest of them
        showed, and is judged on those frames alone. Each verb whose walk the stop ended asks
        the shim again as its next one starts."""
        asked = _twist_of((self._last_state or {}).get("move"))
        close = _Closed(line, self._frames_read, self._last_state_at, asked)
        for sent in self._unshown:
            if sent.line < line and sent.closed is None:
                sent.closed = close
                self._walk_asked.discard(sent.task)

    def _unjudged(self, before: int) -> None:
        """A stop written after line `before` got no answer: it failed, or the call that sent it
        was cancelled first. Nothing then says which frames read since were built before the shim
        zeroed its twist for it, if it read it at all, so every twist written before it that no
        answer had ended is written off unjudged."""

        def ended(sent: _Sent) -> bool:
            return sent.line <= before and sent.closed is None

        if not any(ended(sent) for sent in self._unshown):
            return
        for sent in self._unshown:
            if ended(sent):
                self._walk_asked.discard(sent.task)
        kept = [sent for sent in self._unshown if not ended(sent)]
        self._unshown = deque(kept, maxlen=self._unshown.maxlen)

    def _stopped(self, task: asyncio.Task[Any] | None, before: int) -> None:
        """The stop `task` sent, written after line `before`, has been answered, or has failed:
        that verb's walk is over, so its next twist starts another, which the shim is asked
        about (`_walk_asked`), and the twists before the stop that no answer ended are written
        off (`_unjudged`). Nothing here lets go of a hold: only the end of the verb that walked
        does (`_release`)."""
        self._walk_asked.discard(task)
        self._unjudged(before)

    def _ask(self) -> None:
        """Ask the shim something, unless a question is out already, so that its answer says
        whether it has read the twists written before the question (`_heard`)."""
        if self._probe is None or self._probe.done():
            self._probe = asyncio.get_running_loop().create_task(
                self._probe_echo(), name="quackd-huebot-echo"
            )

    async def _probe_echo(self) -> None:
        """The question `_ask` puts: `robot.health`, the call the heartbeat makes, which changes
        nothing on the robot. Its answer marks the twists before it as read on its way in
        (`_answered`). One that does not come is said on every twist it was asked about, which
        `_not_taken` then judges on: a shim that cannot answer cannot be shown to have taken
        them."""
        before = self._lines
        try:
            health = await self.request(up.ROBOT_HEALTH.name)
        except TransportError as e:
            for sent in self._unshown:
                if sent.heard is None and sent.line <= before:
                    sent.unheard = str(e)
        else:
            if isinstance(health, dict):
                self._last_health = health

    def _waited(self, sent: _Sent) -> bool:
        """Whether `sent` has had `MOVE_ECHO_FRAMES` frames read since it went out, the newest of
        them `move_echo_after_s` after it, before which nothing is judged on it."""
        if self._last_state_at is None or self._frames_read - sent.frames < MOVE_ECHO_FRAMES:
            return False
        return self._last_state_at - sent.at >= move_echo_after_s(self.state_hz)

    def _missed(self, first: _Sent) -> int:
        """How many frames say the shim did not hold `first`, the first twist of a walk that no
        frame has shown, though it had read it: those read after the shim answered a call made
        after it (`_heard`), up to the answer to the stop that ended the walk (`_closed`) or up
        to now while it goes on, less the first of them, which can have been built before the
        twist was stored (`up.LINES_IN_ORDER`)."""
        if first.heard is None:
            return 0
        end = first.closed.frames if first.closed is not None else self._frames_read
        return max(0, end - first.heard - 1)

    def _not_taken(self) -> str | None:
        """Why the newest state says the shim has not taken the twists quackd sent, or None.

        Judged on the moving twists no frame has shown: a stopped twist proves nothing, since a
        robot that took nothing reads stopped too. Nothing is judged before `MOVE_ECHO_FRAMES`
        frames have been read since the oldest of them went out, the newest of those frames
        `move_echo_after_s` after it (`_waited`). Then only the frames read after the shim
        answered a call quackd made after a walk's first twist count, and only until it answered
        the stop that ended the walk (`_missed`). It is asked once as each walk starts, and again
        here when no answer has come by the end of the window (`_ask`). A question that got no
        answer is a verdict of its own. Every walk since the newest twist a frame showed is
        counted, each less its first frame, and `MOVE_ECHO_FRAMES` less one in all is the
        verdict.

        Two things look alike here, and the words name both: a shim that could not parse the
        twist answers nothing and goes on showing what it had, and one that took it shows the
        next twist another client sent. Holding the walk back is right either way: the robot is
        not doing what quackd asked."""
        moving = [sent for sent in self._unshown if any(sent.twist)]
        if not moving or self._last_state_at is None:
            return None
        unanswered = next((sent for sent in moving if sent.unheard is not None), None)
        if unanswered is not None and self._waited(unanswered):
            asked = _twist_of((self._last_state or {}).get("move"))
            waited = self._last_state_at - unanswered.at
            self._echo_not_taken(unanswered, asked, waited)
            return (
                f"quackd sent {_shown(unanswered.twist)}, the shim did not answer the "
                f"{up.ROBOT_HEALTH.name} quackd sent after it to find out whether it had read it "
                f"({unanswered.unheard}), and the robot.state that arrived {waited:.1f} s after "
                f"the twist {_shows(asked)}"
            )
        waiting = moving[0]
        if not self._waited(waiting):
            return None
        # the first twist of each walk, oldest first: a walk is the twists up to a stop the shim
        # answered, the newest one still going on until one is
        firsts: list[_Sent] = []
        for sent in moving:
            if not firsts or sent.closed is not firsts[-1].closed:
                firsts.append(sent)
        newest = firsts[-1]
        if newest.closed is None and newest.heard is None and self._waited(newest):
            self._ask()
        counted = [first for first in firsts if self._missed(first) > 0]
        if sum(self._missed(first) for first in counted) < MOVE_ECHO_FRAMES - 1:
            return None
        # what the newest frame that counted said instead, and when it came
        last = counted[-1]
        if last.closed is None or last.closed.at is None:
            asked, at = _twist_of((self._last_state or {}).get("move")), self._last_state_at
        else:
            asked, at = last.closed.asked, last.closed.at
        waited = at - waiting.at
        self._echo_not_taken(waiting, asked, waited)
        remote = (self.shim or {}).get("api_version")
        unread = (
            f", and this shim speaks API v{remote}, which quackd has not read"
            if remote is not None and remote != self.api_version
            else ""
        )

        def either(these: str, after: str) -> str:
            return (
                f"the shim answers a robot.move with nothing, so either it could not parse "
                f"{these}{unread}, or another client sent one after {after}"
            )

        if len(counted) == 1:
            ended = last.closed is not None
            before = ", the last before a stop ended the walk," if ended else ""
            return (
                f"quackd sent {_shown(waiting.twist)}, the shim has since answered a call quackd "
                f"made after it, so it had read it, and the robot.state that arrived {waited:.1f} "
                f"s after the twist{before} {_shows(asked, past=ended)}. {either('this one', 'it')}"
            )
        return (
            f"quackd sent {_shown(waiting.twist)} and more after it, in {len(counted)} walks "
            "with a stop between each, the shim answered a call quackd made after each walk "
            "began, so it had read them, and the robot.state frames it sent while each walk went "
            f"on, the last arriving {waited:.1f} s after the first twist, "
            f"{_shows(asked, past=True)}. {either('them', 'each')}"
        )

    def _echo_not_taken(self, sent: _Sent, asked: Twist | None, waited: float) -> None:
        """`extras.move_echo` for a verdict that the shim has not taken `sent`: what the newest
        frame that counted showed instead, and how long after the twist it came."""
        self.move_echo = {
            "taken": False,
            "twist": list(sent.twist),
            "requested": list(asked) if asked is not None else None,
            "after_s": round(waited, 3),
        }

    # ── intents ─────────────────────────────────────────────────────────────────────

    async def send_intent(self, intent: Intent) -> Ack:
        p = intent.params
        try:
            match intent.kind:
                case "move":
                    # the verb sending it, which a hold is kept for until it ends
                    task = asyncio.current_task()
                    if (refusal := self._move_refusal(task)) is not None:
                        return Ack(accepted=False, reason=refusal)
                    vx, vy, wz = p.get("vx", 0.0), p.get("vy", 0.0), p.get("wz", 0.0)
                    line = await self.notify(up.ROBOT_MOVE.name, {"vx": vx, "vy": vy, "vyaw": wz})
                    # Written and drained, so the state could show it from now on (_settle), and
                    # the verb that sent it has walked until it ends (_hold_back).
                    sent = (_number(vx), _number(vy), _number(wz))
                    at = time.monotonic()
                    self._unshown.append(_Sent(sent, at, self._frames_read, line, task))
                    self._walking.add(task)
                    if task not in self._walk_asked:
                        # a walk starting: the shim is asked once now, so that its answer, which
                        # says it has read this twist, comes while the walk goes on, and the
                        # frames after it count (`_missed`)
                        self._walk_asked.add(task)
                        self._ask()
                    return Ack()
                case "stop":
                    task, before = asyncio.current_task(), self._lines
                    try:
                        await self.request(up.ROBOT_STOP.name)
                    finally:
                        # the stop `stop()` sends, so it ends the caller's walk as that does
                        self._stopped(task, before)
                    return Ack()
                case "do":
                    # Only a skill the shim listed goes out (up.SHIM_SKILLS): the adapter offers
                    # no verb for any other, and this refuses one that reached here anyway.
                    skill = p.get("skill")
                    listed = self._skills()
                    if skill not in listed:
                        return Ack(
                            accepted=False,
                            reason=(
                                f"Huebot's shim did not list {skill!r} among its skills "
                                f"({', '.join(listed) or 'none'}), so quackd sends no robot.do "
                                "for it"
                            ),
                        )
                    res = await self.request(up.ROBOT_DO.name, {"skill": skill})
                    return _ack(res)
                case "look":
                    # The shim does not answer robot.look yet (up.SHIM_LOOK), so a look is
                    # answered here, before anything is written: a robot.look on the wire would
                    # be a call nothing on the robot was built to take.
                    return Ack(accepted=False, reason=LOOK_REFUSED)
                case "sound":
                    asked = p.get("tag", "chirp")
                    tag = asked if asked in up.SOUND_TAG_LIST else "chirp"
                    res = await self.request(up.ROBOT_SOUND.name, {"tag": tag})
                    ack = _ack(res)
                    if tag != asked and ack.accepted:
                        # The protocol has seven tags and no TTS. Substituting is right; doing it
                        # without saying so meant a verb reporting a sound nobody asked for.
                        return Ack(accepted=True, reason=f"{asked!r} is not a duck sound; {tag}")
                    return ack
                case "enable":
                    res = await self.request(up.ROBOT_ENABLE.name, {"on": bool(p.get("on", True))})
                    return _ack(res)
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
                raise HeartbeatError(
                    f"Huebot's shim says it is unhealthy: {health.get('reason') or 'no reason'}"
                )

    async def stop(self) -> None:
        """Zero the velocity. Never raises, and never pretends it landed, either.

        The protocol asks that this be safe to call from anywhere at any time, including from
        the exception paths that run because the socket died, so it cannot raise. What stops the
        robot when it cannot be delivered is the robot's own deadman: `robot.move` notifications
        stop arriving and the velocity goes to zero, which robotd does (`up.DEADMAN`) and Huebot's
        shim is asked to (`up.SHIM_DEADMAN`). This is not a substitute for it.

        It also ends the walk of every twist written before it, once the shim has answered it, so
        that those twists are judged on the frames read until then and on none after
        (`_closed`), and if no answer comes it writes them off unjudged (`_stopped`). It lifts no
        hold on `robot.move`: a hold ends when the verb it was set for ends (`_release`).
        """
        task, before = asyncio.current_task(), self._lines
        try:
            await self.request(up.ROBOT_STOP.name)
            self.stop_error = None
        except Exception as e:  # nothing the socket does gets out of here
            self.stop_error = str(e) or type(e).__name__
            log.warning(
                "stop could not be delivered to %s (%s). What stops Huebot now is its own "
                "deadman, which zeroes velocity once robot.move stops arriving, if its shim "
                "keeps one as robotd does.",
                self.address,
                self.stop_error,
            )
        finally:
            self._stopped(task, before)

    def now(self) -> float:
        return time.monotonic() - self._t0

    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(seconds)


def _ack(result: Any) -> Ack:
    """The shim's `IntentResult` as an `Ack`, and a refusal for an answer that is not one.

    `accepted` is a bool robotd writes on every `IntentResult`, so an answer without it is not
    an answer quackd has read: a shim whose result moved, or something else on the socket. Read
    as accepted, a `robot.do` the shim refused could be reported as a kick. Unread is not
    consent, and the refusal names what came back."""
    if isinstance(result, dict) and isinstance(result.get("accepted"), bool):
        reason = result.get("reason")
        return Ack(accepted=result["accepted"], reason=None if reason is None else str(reason))
    return Ack(
        accepted=False,
        reason=(
            f"the shim answered {result!r:.200}, which carries no accepted that quackd can read, "
            "so it is not taken for a yes"
        ),
    )
