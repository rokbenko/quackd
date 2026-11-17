#!/usr/bin/env python3
"""Roboglot's ToddlerBot daemon: the fifty hertz loop, and everything upstream does not do.

ToddlerBot has no network API of any kind. No socket, no daemon, no IPC: it is a Python
library whose control loop opens serial ports in-process. So Roboglot ships this, the way it
ships one for the Open Duck Mini, and it runs on the robot in upstream's own environment.

It exists for two reasons, and the second is the important one.

**A verb is episodic and this robot is not.** `RealWorld.step()` is a no-op, so nothing times
out and nothing re-arms: the last commanded pose is held forever. A humanoid frozen mid-stride
while an LLM thinks is a humanoid on the floor, so the loop has to run continuously
and Roboglot's intents only nudge what it is doing.

**Upstream protects nothing, and its shutdown drops the robot.** Verified at the pinned commit:
`set_motor_target` clamps nothing and never reads the joint limits that exist; the motors are
in multi-turn mode so the firmware limits are off too; there is no watchdog, no timeout, no
e-stop and no reset anywhere; a dropped packet returns an all-zeros observation that looks
exactly like every joint at zero; a controller fault surfaces as a bare `KeyError`; and a C
level `atexit` handler disconnects every client on any normal interpreter exit, which disables
torque and drops a standing robot. There is no Python signal handler anywhere upstream, so
`SIGTERM` does not even reach that.

So this daemon carries ten things upstream has not got:

1. signal handlers that reach a safe pose before anything is allowed to exit, and excepthooks
   for the same reason: upstream's C level atexit disables torque on any interpreter exit;
2. a hard-exit deadline that needs no GIL, around the settle and around `close()`, which is
   bound without releasing the GIL and can block forever on an unresponsive bus, freezing
   every thread that might have supervised it;
3. a safe-pose slew, since no reset exists: upstream's own default pose at upstream's own
   0.3 rad/s, waist first, because a position command here is a full-torque snap;
4. no command at all until it has read the robot once, because before that the target is a
   guess and writing a guess to a servo bus is a full-scale jump;
5. a last-known-good observation cache with an all-zeros detector, so a dropped packet cannot
   be mistaken for a reading;
6. its own clamp against the joint limits, a per-tick rate limit, and a refusal of any target
   that is not a finite number, since `json.loads` accepts a bare NaN;
7. a control loop that survives a raising tick instead of dying quietly while the socket goes
   on answering healthy;
8. keyframe playback paced to what the body can actually follow, rather than advancing a frame
   per tick and tracing a smoothed shortcut through the motion;
9. a construction watchdog, because the constructor busy-waits forever on a silent IMU with
   the motors already live;
10. capability dispatch by type rather than by testing whether a name contains "real".

Rules this file lives by, the same three as `bridge/open_duck/`:

- **It never imports Roboglot.** Roboglot's dependencies do not belong on a robot.
- **It ships in the sdist and never in the wheel**, so `packages` stays `["roboglot"]`.
- **It is testable with no hardware.** Everything above the `Robot` boundary is pure and takes
  plain arrays, and `--fake` runs the whole daemon and protocol against a fake body.

    python roboglot_toddlerbot_bridge.py --robot toddlerbot_2xc --fake
    python roboglot_toddlerbot_bridge.py --robot toddlerbot_2xc --toddlerbot ~/toddlerbot
"""

from __future__ import annotations

import argparse
import base64
import collections
import contextlib
import faulthandler
import hmac
import ipaddress
import json
import logging
import math
import os
import re
import signal
import socket
import socketserver
import sys
import threading
import time
from typing import Any

try:  # numpy is upstream's own dependency and is always present beside it
    import numpy as np
    from numpy.typing import NDArray
except ImportError:  # pragma: no cover - the daemon cannot run without it
    np = None  # type: ignore[assignment]

VERSION = "1"
DESCRIPTION = (
    "Roboglot's ToddlerBot daemon: the fifty hertz loop, and everything upstream does not do."
)
"""The first line of this file's docstring, written out: under `python -OO` the docstring is
None, and a parser built from it raised before it read a flag."""
PROTOCOL = "roboglot-toddlerbot-bridge"
PROTOCOL_VERSION = 1
JSONRPC_VERSION = "2.0"
DEFAULT_PORT = 9873
"""The Open Duck Mini takes 9871 for its bridge and 9872 for its camera daemon, and
SECURITY.md tells people to tunnel that pair, so this robot starts after both."""
MAX_LINE = 64 * 1024
"""The longest request line kept. Real requests are tiny and frames only go the other way, and with
no limit a peer that never sent a newline could fill the memory of the process walking the robot."""
HTTP_REQUEST_LINE = re.compile(rb"^[^\s{]+ \S+ HTTP/[0-9]")
"""The first line of an HTTP request, which is all a browser can open a connection with. A page the
operator happens to visit can POST to 127.0.0.1 through the tunnel the docs recommend: the headers
are no JSON and were skipped, and the body's line was then read as a command. Any word for the
method, because a page on a name it rebinds to 127.0.0.1 is same-origin and sends `patch` or `A1`
as written. No client of this daemon starts a connection with anything but a JSON object, which is
what the `{` keeps out."""
TOKEN_ENV = "ROBOGLOT_TODDLERBOT_TOKEN"

CONTROL_HZ = 50.0
CONTROL_DT = 1.0 / CONTROL_HZ
DEADMAN_S = 0.5
"""How long Roboglot may go quiet before the loop stops taking its word for anything. It does
not stop the robot, because stopping is not a thing this body can do: it slews to a safe pose
and holds it."""
RESET_VEL = 0.3
"""Radians per second, upstream's own rate for moving to a rest pose."""
SETTLE_OVERHEAD = 20.0
"""How much longer a slew takes than its arithmetic says.

It is handed out one `CONTROL_DT` tick at a time, and every one of those ticks costs a bus
round trip on top of its sleep: about 1.8x on an idle machine, and over 6x on a loaded CI
runner. `settle()` is a deadline for a body that is stuck, so it is sized well past both."""
SETTLE_FLOOR_S = 8.0
"""The shortest settle deadline, for a body already at the safe pose."""
SETTLE_MAX_S = 120.0
"""The longest, so a jammed joint is not waited on forever."""
WAIST_THRESHOLD = 0.5
"""Untwist the waist first if it is further than this from zero, as upstream's reset does."""
MAX_STEP_RAD = RESET_VEL * CONTROL_DT * 4.0
"""The most any joint may move in one tick, whatever asked for it."""
CLOSE_TIMEOUT_S = 5.0
FAULT_LIMIT = 10
CONTROL_THREAD = "roboglot-control"
"""The only thread whose death is the robot's problem."""
DROP_LIMIT = 5
FRAME_DWELL_LIMIT = 100
"""Ticks to spend trying to reach one keyframe before giving up on it. A frame outside the
joint limits can never be reached, and a motion that stalls forever is worse than one that
skips a frame and says so."""
"""Consecutive dropped reads before the last good one stops being good enough. One is
routine on this bus; five in a row at fifty hertz is a tenth of a second blind."""
CATCHUP_LIMIT_S = 0.25
"""How far behind the schedule may fall before it is reset rather than caught up.

Catching up means running the backlog with no sleep between ticks, and each of those
ticks is allowed a full MAX_STEP_RAD. Twelve ticks in a millisecond is not a rate limit,
it is a jump, so a loop that fell behind starts again from now instead."""
CAMERA_PERIOD_S = 0.1
CAMERA_JOIN_S = 2.0
"""How long closing the camera waits for its thread, which a USB read that never returns
wedges. It is the wait this file has always given it; past it the device is closed anyway."""
CAMERA_STALE_S = 2.0
"""How old the newest frame may be before it is no frame at all. A wedged USB camera
would otherwise serve its last good picture forever, and a pilot would navigate by a
photograph of somewhere the robot used to be."""
"""Ten frames a second. Roboglot asks for one when it wants one, so this only bounds how
stale the newest can be, and a USB read is expensive enough not to do at fifty."""

MOTIONS = ("hold", "kneel", "cuddle", "push_up", "crawl")
"""The keyframe motions Roboglot offers, out of the nine that ship.

`cartwheel` is excluded because this body has no fall recovery, and because its file
carries `action=None`: it is qpos-interpolated and meant to run as its own RL policy, so
replaying it would fail. `walk_zmp` is excluded because it is not a keyframe file at all,
it is a gait lookup table used at training time. The two `pull_up` motions need a bar."""
"""Consecutive failing ticks before the loop gives up and settles. One is a glitch on a
serial bus; ten in a row at fifty hertz is a fifth of a second of a robot nobody drives."""
"""`close()` holds the GIL and retries torque-off forever on a dead bus. If it has not
returned by now, leave hard: a torqued robot beats a frozen supervisor that cannot be killed."""
CONSTRUCT_TIMEOUT_S = 30.0
FALL_TILT_DEG = 50.0
"""Untested against a real robot, and named as an assumption in the adapter's docs."""
TICK_STALE_S = DEADMAN_S
"""How long after the last tick ended the loop counts as stalled rather than slow. The deadman
lives inside the tick, so past DEADMAN_S it can no longer fire on time either, and that is when
health has to stop answering ok."""
BUS_GONE_LIMIT = 2 * FAULT_LIMIT
"""Consecutive failing ticks before the loop stops ticking at all. The first FAULT_LIMIT end the
orders it takes and turn it to the safe pose; as many again with the bus still failing mean
there is no bus to reach the safe pose with, and failing fifty times a second forever helps
nobody."""
GAVE_UP = (
    f"the control loop gave up after {FAULT_LIMIT} failing ticks in a row: it takes no orders, "
    "and it settles the robot to its safe pose for as long as the bus answers. Restart the daemon"
)
SHUTTING_DOWN = "the daemon is shutting down and settling the robot to its safe pose"
LOST_TO_A_STOP = (
    "another connection's stop held the robot, so what this connection had it doing has ended"
)
LOST_TO_A_COMMAND = "another connection's command took the {part} this connection was driving"
ARRIVED_RAD = 1e-3
"""How near its goal a joint counts as there: the distance a stand has always counted as
arrived, and now a head or a gripper the deadman brings to rest too."""
FLOOR_MOTIONS = {
    "crawl": "a crawl goes down to hands and knees, the trunk near level",
    "push_up": "a push-up goes down prone, the trunk near level",
}
"""The motions Roboglot offers that take the robot to the floor on purpose, and why each is here.

THIS IS A TABLE, not a measurement. A tilt past FALL_TILT_DEG during one of these, or after it,
is the motion and not a fall. The daemon cannot read that from the keyframes: the files it loads
carry motor targets alone (`upstream_api.MOTION_ACTION_ARRAY`), with no orientation of the
trunk to compare against the threshold. So the table goes by what each motion is named for, which
this repository's own pages call "crawling or prone". `hold`, `kneel` and `cuddle` are taken as
upright, which is as unmeasured as this table until a robot plays them."""
FACE_DOWN = (-1.0, 0.0)
"""Which way the world's up leans, read in the body's own frame as `observe` reads it, when the
robot is face down, its front (+x) towards the floor. Both FLOOR_MOTIONS go down this way, by
their own reasons in that table: hands and knees, and prone. UNVERIFIED, like FALL_TILT_DEG: it
follows from upstream's orientation being the body's in the world, so `apply(..., inverse=True)`
reads the world in the body's frame, and from +x being the body's front. Neither the IMU's
mounting nor this sign has met a robot."""
FLOOR_HEADING_DEG = 45.0
"""How far a lean may turn from FACE_DOWN and still be the floor motion's own. Half the way to a
robot on its side, which is 90 degrees round from face down (on its back is 180), so a lean
nearer face down than on its side is the motion, and any other is a fall."""
BODY = "body"
"""The part a walk, a stand, a motion and the deadman's slew drive. The neck and each gripper
are parts of their own, each owned by the connection whose command it is executing."""
NECK = "neck"
FALLEN_REFUSES = frozenset({"bot.stand", "bot.perform", "bot.command"})
"""The moving methods a fallen robot refuses: the ones that drive the legs and the trunk. A stop,
a look and a grip stay open, as they always were."""

ERR_BAD_TOKEN = 2
ERR_REFUSED = 3
ERR_UNKNOWN = 4

COMMAND_METHODS = frozenset(
    {
        "bot.keepalive",
        "bot.stop",
        "bot.stand",
        "bot.perform",
        "bot.look",
        "bot.command",
        "bot.grip",
    }
)
"""The methods that drive the robot, and the only ones that feed the deadman.

Questions (state, health, frame) do not, and neither does anything the daemon has never
heard of: a client built against a newer protocol would otherwise hold the deadman off
with calls this daemon is refusing, which is exactly the version-skew case it is for."""
MOVING_METHODS = COMMAND_METHODS - {"bot.keepalive"}
"""The methods that change what the robot does. Every one of them goes through
`Daemon.obey`, the one place that decides whether the robot may move now."""

log = logging.getLogger("roboglot-toddlerbot")


# ── the pure core: no hardware, no upstream, no Roboglot ──────────────────────────────────


def clamp_to_limits(target: Any, lo: Any, hi: Any) -> Any:
    """The only thing standing between a commanded radian and a joint winding itself round.

    Upstream's `set_motor_target` does not clip and never reads the joint limits, and the
    motors are in multi-turn mode so the firmware limits are off as well."""
    return np.clip(target, lo, hi)


def rate_limit(target: Any, current: Any, max_step: float = MAX_STEP_RAD) -> Any:
    """No more than `max_step` of movement per joint per tick, whatever was asked for.

    A position command on this body is a full-torque snap, so the difference between a slew
    and a jump is the difference between a move and a bang."""
    delta = np.clip(target - current, -max_step, max_step)
    return current + delta


def looks_like_a_dropped_read(pos: Any, vel: Any) -> bool:
    """True when an observation is the all-zeros buffer a failed bulk read returns.

    With zero retries a comm failure hands back the pre-zeroed buffer, which is
    indistinguishable from every joint genuinely at zero. Feeding that to a position
    controller commands a full-scale move to zero, so it is refused instead.

    It is indistinguishable only because a real bus can fail that way. The fake body cannot:
    it has no bulk read to drop. So the caller decides whether the guard applies, and
    `Daemon.bus` is how it knows."""
    if pos is None or len(pos) == 0:
        return True
    return bool(np.all(pos == 0.0) and np.all(vel == 0.0))


def tilt_degrees(gravity_z: float) -> float:
    """How far off upright, from the body-frame gravity vector's vertical component."""
    return math.degrees(math.acos(max(-1.0, min(1.0, gravity_z))))


def leans_face_down(up_x: float, up_y: float) -> bool:
    """Whether the world's up, read in the body's frame, leans within FLOOR_HEADING_DEG of
    FACE_DOWN: the robot going down the way a floor motion goes, and not on its side or back."""
    fx, fy = FACE_DOWN
    turned = math.degrees(math.atan2(fx * up_y - fy * up_x, fx * up_x + fy * up_y))
    return abs(turned) <= FLOOR_HEADING_DEG


def slew(current: Any, goal: Any, dt: float, vel: float = RESET_VEL) -> Any:
    """One step of a bounded-rate move towards a goal."""
    return rate_limit(goal, current, max_step=vel * dt)


WALK_KEYS = ("walk_x", "walk_y", "walk_turn")
"""The walk policy's three inputs, which it indexes unconditionally."""
ENVELOPE_KEYS = ("max_vx", "max_vy", "max_wz")
"""The envelope's bound for each of WALK_KEYS, in the same order, as `build_walk_policy` reads
them off the checkpoint and the hello reports them."""


def within_envelope(
    walk: tuple[float, float, float], envelope: dict[str, float] | None
) -> tuple[float, float, float]:
    """A walk command held to the envelope its checkpoint was trained on, each axis on both
    sides, as the client reads the envelope. With no envelope there is nothing to hold it to.

    Upstream never clips these on the way into the network, so a client other than Roboglot
    could otherwise drive the gait with inputs it was never trained on."""
    if envelope is None:
        return walk
    held = []
    for value, key in zip(walk, ENVELOPE_KEYS, strict=True):
        bound = abs(float(envelope[key]))
        held.append(max(-bound, min(bound, value)))
    return held[0], held[1], held[2]


def waist_first(goal: Any, current: Any, waist: Any) -> Any:
    """Upstream's own two-phase rule: untwist the waist before anything else moves.

    If any waist joint is more than half a radian from zero, drive only the waist to zero and
    hold everything where it is; the rest of the pose follows once it is straight."""
    if waist is None or len(waist) == 0:
        return goal
    if float(np.max(np.abs(current[waist]))) <= WAIST_THRESHOLD:
        return goal
    staged = current.copy()
    staged[waist] = 0.0
    return staged


class SafeState:
    """The last observation Roboglot is willing to believe, and the pose derived from it.

    Kept because a stop on this body means *hold the last verified-good measured pose*, and a
    fresh read at that moment may be the all-zeros buffer."""

    def __init__(self, initial: Any) -> None:
        self.pose = np.array(initial, dtype=np.float32)
        self.stamp = 0.0
        self.rejected = 0

    def offer(self, pos: Any, vel: Any, now: float, *, checked: bool = True) -> bool:
        """`checked` is whether these numbers came off a bus that can drop a read."""
        if checked and looks_like_a_dropped_read(pos, vel):
            self.rejected += 1
            return False
        self.pose = np.array(pos, dtype=np.float32)
        self.stamp = now
        return True


# ── the command source: what the loop is trying to do this tick ─────────────────────────


class Command:
    """A tiny state machine, because the loop must always be doing exactly one thing."""

    HOLD = "hold"
    STAND = "stand"
    MOTION = "motion"
    WALK = "walk"
    DEADMAN = "deadman"

    def __init__(self, default_pose: Any) -> None:
        #: Whether anything has actually asked for a pose yet. Until something has, the
        #: goal is the home pose by default, and the daemon prefers to hold where the
        #: robot really is rather than move it somewhere nobody requested.
        self.given = False
        self.default_pose = np.array(default_pose, dtype=np.float32)
        self.mode = self.HOLD
        self.goal = np.array(default_pose, dtype=np.float32)
        self.motion: str | None = None
        self.frames: list[Any] = []
        self.frame_index = 0
        self.dwell = 0
        self.walk: dict[str, float] = {"walk_x": 0.0, "walk_y": 0.0, "walk_turn": 0.0}
        self.last_client = 0.0

    def hold(self, pose: Any) -> None:
        self.given = True
        self.mode = self.HOLD
        self.goal = np.array(pose, dtype=np.float32)
        self.motion = None
        self.frames = []

    def stand(self) -> None:
        self.given = True
        self.mode = self.STAND
        self.goal = self.default_pose.copy()
        self.motion = None
        self.frames = []

    def play(self, name: str, frames: list[Any]) -> None:
        self.given = True
        self.mode = self.MOTION
        self.motion = name
        self.frames = frames
        self.frame_index = 0
        self.dwell = 0

    def drive(self, walk_x: float, walk_y: float, walk_turn: float) -> None:
        self.given = True
        self.mode = self.WALK
        # all three keys or none: the walk policy indexes them unconditionally
        self.walk = {"walk_x": walk_x, "walk_y": walk_y, "walk_turn": walk_turn}

    def trip(self, pose: Any) -> None:
        """The deadman. It is a trajectory, not a message: silence on this body means hold
        forever, and neither holding a bad target nor going limp is safe."""
        self.given = True
        self.mode = self.DEADMAN
        self.goal = np.array(pose, dtype=np.float32)
        self.motion = None
        self.frames = []

    @property
    def busy(self) -> bool:
        return self.mode in (self.STAND, self.MOTION)


# ── the loop ────────────────────────────────────────────────────────────────────────────


def settle_budget(travel: float) -> float:
    """How long to let a slew of `travel` radians run before calling it stuck.

    The slew cannot finish sooner than `travel / RESET_VEL`, so a deadline under that gives up
    on a robot that is still moving."""
    return min(SETTLE_MAX_S, SETTLE_FLOOR_S + SETTLE_OVERHEAD * travel / RESET_VEL)


def hard_exit_after(seconds: float, what: str) -> None:
    """End the process with code 1 in `seconds`, whatever it is doing by then, unless the
    deadline is moved by another call or cancelled (`faulthandler.cancel_dump_traceback_later`).

    faulthandler's watchdog is a thread written in C that needs no GIL, so it fires through a
    `close()` that holds the GIL and retries torque-off forever on a dead bus. The Python timer
    this replaced needed the GIL to run, so it could not fire in exactly the case it was for.
    It writes every thread's stack before it exits, which is the log of where it hung."""
    log.info("%s; if that has not finished in %.1fs the process exits hard", what, seconds)
    # The stream the process started with, which is the journal under a service manager. A
    # process started with it closed has none, and faulthandler will not arm without a file, so
    # the stacks go nowhere then and the exit still comes; faulthandler keeps the file open.
    stream = sys.__stderr__ or sys.stderr or open(os.devnull, "w")  # noqa: SIM115
    try:
        faulthandler.dump_traceback_later(seconds, exit=True, file=stream)
    except Exception:
        log.exception("could not arm the hard exit for %s; going on without it", what)


class Daemon:
    """Owns the robot, the loop and the safe pose. Everything else asks it politely."""

    def __init__(self, robot: Any, sim: Any, *, fake: bool = False, bus: bool = True) -> None:
        self.robot = robot
        self.sim = sim
        self.fake = fake
        #: Whether readings arrive over a serial bus that can hand back a zeroed buffer. On
        #: hardware they do, and an all-zeros read is refused rather than driven to. The fake
        #: body's do not, and an all-zeros pose there is just a pose.
        self.bus = bus
        self.lock = threading.Lock()
        self.running = False
        self.loop_hz = 0.0
        #: Whether the trunk reads past FALL_TILT_DEG, and whether that is a fall: it is not
        #: while the robot is down on purpose, after a motion in FLOOR_MOTIONS (`down_by`)
        #: and until it is upright again.
        self.lying = False
        self.fallen = False
        self.down_by: str | None = None
        #: A stand from the floor counts as done only once the trunk reads upright: by when it
        #: must (`stand`), and, if it did not, what health says until the robot is up.
        self.rise_deadline: float | None = None
        self.rise_failed: str | None = None
        #: For each part at rest, the nearest it has come to its goal and the ticks since it
        #: came any nearer, so a rest that cannot arrive ends (`_with_neck`).
        self._rest_progress: dict[str, tuple[float, int]] = {}
        #: The parts the motion now playing took, released when it ends.
        self.motion_parts: list[str] = []
        self.tilt_deg = 0.0
        #: When the last tick ended, however it ended, or None before the first. `bot.health`
        #: reads it without `lock`, because a tick blocked on the bus holds the lock and leaves
        #: everything else the loop writes frozen at its last good value.
        self.tick_ended: float | None = None
        #: When `run` began, so a first tick that never ends counts as a stall from then.
        self.loop_started: float | None = None
        self.fault: str | None = None
        self.dropped = 0
        self.neck_goal: Any = None
        self.grip_goal: dict[int, float] = {}
        self.faults = 0
        #: Set once FAULT_LIMIT ticks have failed in a row, and never cleared: from then the
        #: loop takes no orders (`refusal`) and only settles the robot to its safe pose.
        self.gave_up = False
        self._shut = False
        #: Shutdown is entered once, with no lock a signal could catch half taken: the one
        #: caller that pops `_first_shutdown` (a `list.pop`, which is atomic) does it, `_inside`
        #: marks each thread already in `shutdown`, and `_shut_done` is set when it has closed,
        #: for a caller on another thread to wait on.
        self._first_shutdown = [True]
        self._inside = threading.local()
        self._shut_done = threading.Event()
        self.calibrated = bool(getattr(robot, "roboglot_calibrated", False))
        self.motion_library: dict[str, list[Any]] = {}
        self.walk_policy: Any = None
        self.walk_envelope: dict[str, float] | None = None
        self.camera: Any = None
        self.last_obs: Any = None
        n = int(getattr(robot, "nu", 30))
        order = list(getattr(robot, "motor_ordering", [f"m{i}" for i in range(n)]))
        self.order = order
        limits = dict(getattr(robot, "motor_limits", {}) or {})
        self.lo = np.array([limits.get(k, (-math.pi, math.pi))[0] for k in order], np.float32)
        self.hi = np.array([limits.get(k, (-math.pi, math.pi))[1] for k in order], np.float32)
        self.waist = np.array([i for i, k in enumerate(order) if "waist" in k], dtype=int)
        self.neck = [i for i, k in enumerate(order) if "neck" in k]
        #: By name, like the neck. A build without them has no gripper motors at all, so
        #: the capability is what was found rather than what the operator passed.
        self.grippers = {
            "left": [i for i, k in enumerate(order) if "gripper" in k and "left" in k],
            "right": [i for i, k in enumerate(order) if "gripper" in k and "right" in k],
        }
        # By name, not by position. `self.neck` is whatever contains 'neck', and taking
        # [0] as yaw and [1] as pitch assumes an ordering upstream never promises: an
        # alphabetical motors block lists neck_pitch before neck_yaw, which would send
        # every look to the wrong joint. Fall back to order only when the names do not
        # say, and say so when that happens.
        self.neck_yaw = next((i for i in self.neck if "yaw" in order[i]), None)
        self.neck_pitch = next((i for i in self.neck if "pitch" in order[i]), None)
        if self.neck and (self.neck_yaw is None or self.neck_pitch is None):
            log.warning(
                "neck motors %s do not say which is yaw and which is pitch; assuming order",
                [order[i] for i in self.neck],
            )
            self.neck_yaw = self.neck[0]
            self.neck_pitch = self.neck[1] if len(self.neck) > 1 else None
        # `default_motor_angles`, and no getattr. The name matters more here than
        # anywhere else in this file: this is the pose the deadman slews to, the pose
        # `stand` targets and the pose the excepthook settles to. Upstream does have a
        # `default_motor_pos`, on BasePolicy, which is what makes the wrong name so easy
        # to reach for. On Robot it is a dict keyed by motor name, in motor_ordering
        # order because that is what it is built from, in radians.
        #
        # A fallback here would be worse than a crash. Zeros are not a neutral pose on
        # this body: home carries plus or minus 1.57 rad of shoulder and elbow yaw and
        # 1.22 of wrist, so slewing to zeros is a large wrong motion on every limb, and
        # it would happen exactly when nobody is driving the robot.
        # By name, exactly as `lo` and `hi` are built three lines up. The dict is built by
        # iterating motor_ordering upstream, so its insertion order matches today, but the
        # limits do not rely on that and neither should the pose the deadman slews to.
        angles = dict(robot.default_motor_angles)
        missing = [k for k in order if k not in angles]
        if missing:
            raise SystemExit(
                f"this robot has no home angle for {missing}. Refusing to start: the safe "
                "pose has to cover every motor."
            )
        default = np.array([angles[k] for k in order], np.float32)
        self.safe = SafeState(default)
        self.command = Command(default)
        #: The parts this robot has: the body, and a neck and each gripper where it has them.
        self.parts = (
            [BODY]
            + ([NECK] if self.neck else [])
            + [f"{side}_gripper" for side, motors in self.grippers.items() if motors]
        )
        #: Who owns each part: the connection whose command that part is executing. A part
        #: holding after a stop, or never commanded, has no owner and no deadman: a daemon
        #: nobody has commanded holds where the robot is, and so does one a stop left holding.
        #: One owner for the whole robot let a second client's look adopt a walk it never asked
        #: for and keep it going after the walking client died. None is a caller inside this
        #: process, which `--once` and tests are.
        self.owners: dict[str, Any] = {}
        #: When each connection was last heard from: its own clock, which its keepalive
        #: restarts and which keeps alive only the parts it owns. The in-process caller's
        #: clock is `command.last_client`.
        self.heard_at: dict[Any, float] = {}
        #: The parts the deadman is bringing to rest, until one is commanded or stopped.
        self.resting: set[str] = set()
        #: What a connection that lost a part to someone else has yet to be told, and why.
        self.lost: dict[Any, str] = {}
        self.target = default.copy()
        #: Whether the first real reading has arrived. Until it has, `target` is a guess
        #: (the home pose), and writing a guess to a servo bus is a full-scale jump from
        #: wherever the robot actually is. Nothing is commanded before this is true.
        self.seeded = False
        if fake:
            # So --fake exercises perform end to end. On a robot these frames come from
            # upstream's keyframe files and nothing here is synthesised.
            self.motion_library = {n: [default.copy()] * 10 for n in MOTIONS}

    # -- one tick ------------------------------------------------------------------

    def observe(self) -> Any:
        """Read, and refuse to believe a reading that looks like a dropped packet.

        A controller fault arrives as a bare `KeyError` rather than an exception the C++
        raised, so it is caught here and treated as a hardware fault, not a transient."""
        try:
            obs = self.sim.get_observation()
        except KeyError as e:
            log.error("controller fault while reading (%s); holding the last good pose", e)
            return None
        pos = np.asarray(obs.motor_pos, dtype=np.float32)
        vel = np.asarray(obs.motor_vel, dtype=np.float32)
        if len(pos) != len(self.order):
            log.error("partial motor read: %d of %d; holding", len(pos), len(self.order))
            return None
        if not self.safe.offer(pos, vel, time.monotonic(), checked=self.bus):
            return None
        if getattr(obs, "rot", None) is not None:
            try:
                gravity = obs.rot.apply([0.0, 0.0, 1.0], inverse=True)
                self.tilt_deg = tilt_degrees(float(gravity[2]))
                self.lying = self.tilt_deg > FALL_TILT_DEG
                face_down = leans_face_down(float(gravity[0]), float(gravity[1]))
                if self.down_by and self.lying and not face_down:
                    # Down on purpose is face down and no other way. A robot on its side or
                    # its back during a floor motion, lying after one, or standing back up
                    # has fallen, and it stays fallen until a person rights it.
                    log.warning("the robot went down another way than %s: a fall", self.down_by)
                    self.down_by = None
                playing = self.command.mode == Command.MOTION and self.command.motion
                if self.down_by and not self.lying and playing not in FLOOR_MOTIONS:
                    self.down_by = None  # upright again, so a tilt is a fall again
                if self.down_by is None:
                    self.rise_failed = None
                self.fallen = self.lying and self.down_by is None
            except Exception:  # orientation is advisory, never fatal
                pass
        return obs

    def plan(self, dt: float) -> Any:
        """What this tick's target is, before any clamping."""
        cmd = self.command
        current = self.safe.pose
        fell = self.fallen and cmd.mode != Command.HOLD
        if fell or (self.lying and cmd.mode == Command.DEADMAN):
            # A body on the floor is driven by nothing: a stand, a motion, a walk or the
            # deadman's slew to the standing pose would each drive the legs and the trunk against
            # the floor, which is why `FALLEN_REFUSES` will not start them. So whatever was in
            # flight ends on the tick that reads the fall, and the robot holds where it lies.
            # Upstream detects no fall and prescribes nothing (`upstream_api.FALL_DETECTION`).
            # A floor motion lying the robot down on purpose is no fall and plays on, but the
            # deadman's rest of a body lying down, either way, is the same hold.
            log.warning(
                "the robot is down; ending %s and holding where it lies", cmd.motion or cmd.mode
            )
            cmd.hold(current)
            # every part a motion took is let go with the body, so its client is not told later
            # that it lost a part it was no longer driving
            self._finished([*self.motion_parts, BODY])
            self.resting.discard(BODY)
            return current
        if cmd.mode in (Command.STAND, Command.DEADMAN):
            # The trajectory advances from the last thing commanded, not from the last thing
            # measured. These motors are position controlled, so the torque they make is
            # proportional to target minus measured: slewing from the measurement pinned that
            # difference at one step, 0.006 rad at fifty hertz, which is almost no torque at
            # all. The arms were asked for ninety degrees and asymptoted at twenty-six, the
            # command creeping along behind them, and `stand` never finished because finishing
            # meant the *body* arriving. Seeding still starts from where the robot is, which is
            # what `tick` does on the first reading and what upstream's own policies do, and
            # `rate_limit` below still bounds every tick to MAX_STEP_RAD. `waist_first` keeps
            # reading the measured pose, because whether the waist is twisted is a question
            # about the body rather than about the plan.
            goal = waist_first(cmd.goal, current, self.waist)
            nxt = slew(self.target, goal, dt)
            if float(np.max(np.abs(goal - nxt))) < ARRIVED_RAD and cmd.mode == Command.STAND:
                if self.down_by and self.lying:
                    # From the floor the commanded pose arrives whatever the body does, so the
                    # stand is done only once the trunk reads upright (`observe` then clears
                    # `down_by`), and past its deadline it ends as not having got up.
                    if self.rise_deadline is not None and time.monotonic() < self.rise_deadline:
                        return nxt
                    self.rise_failed = (
                        "the stand did not get the robot up from the floor: its trunk still "
                        f"reads {self.tilt_deg:.0f} degrees from upright"
                    )
                    log.warning("%s", self.rise_failed)
                cmd.hold(goal)
                self._finished([BODY])
            return nxt
        if cmd.mode == Command.MOTION:
            if cmd.frame_index >= len(cmd.frames):
                cmd.hold(current)
                self._finished(self.motion_parts)
                return current
            frame = np.asarray(cmd.frames[cmd.frame_index], dtype=np.float32)
            # Advance only once the body has actually reached this frame, or after waiting
            # long enough that it plainly never will.
            #
            # The files are fifty hertz recordings of a body moving faster than this loop's
            # per-tick rate limit allows, and `rate_limit` caps every step at MAX_STEP_RAD.
            # Advancing regardless means the robot traces a smoothed shortcut through the
            # keyframes and still reports the motion complete. Playing it slower is a
            # different motion; playing it truncated is a different motion AND a wrong claim
            # about which one ran. A joint an overlay holds (a head another connection turned
            # since the motion began) is not the motion's to reach, so it is not waited for.
            driven = np.ones(len(self.order), dtype=bool)
            driven[self._overlaid()] = False
            away = float(np.max(np.abs(frame - self.target)[driven], initial=0.0))
            if away <= MAX_STEP_RAD or cmd.dwell >= FRAME_DWELL_LIMIT:
                if away > MAX_STEP_RAD:
                    log.warning(
                        "motion %r frame %d is %.3f rad away and not getting closer; moving on",
                        cmd.motion,
                        cmd.frame_index,
                        away,
                    )
                cmd.frame_index += 1
                cmd.dwell = 0
            else:
                cmd.dwell += 1
            return frame
        if cmd.mode == Command.WALK:
            # The policy owns the gait. Roboglot feeds it and clamps what comes back, because
            # upstream never clips walk_x or walk_y on this path: an out-of-envelope command
            # reaches the network as out-of-distribution input. `step` wants the whole
            # observation and the sim, and answers with (control_inputs, motor_target).
            if self.walk_policy is None or self.last_obs is None:
                cmd.hold(current)
                return current
            self.walk_policy.control_inputs = dict(cmd.walk)
            _, motor_target = self.walk_policy.step(self.last_obs, self.sim)
            return np.asarray(motor_target, dtype=np.float32)
        return cmd.goal

    def tick(self, dt: float = CONTROL_DT) -> Any:
        """One control step: read, decide, clamp, write. Never raises for a bad reading.

        Its end is stamped whether it returned or raised, the settle's ticks too, so `bot.health`
        can see a tick that has not ended at all."""
        try:
            return self._tick(dt)
        finally:
            self.tick_ended = time.monotonic()

    def tick_age_s(self) -> float | None:
        """How long since the last tick ended, or since the loop began if none has yet: a first
        tick that never ends is a stall like any other. None before the loop or a tick."""
        ended = self.tick_ended if self.tick_ended is not None else self.loop_started
        return None if ended is None else time.monotonic() - ended

    def _tick(self, dt: float) -> Any:
        with self.lock:
            obs = self.observe()
            if obs is not None:
                self.last_obs = obs
                self.dropped = 0
                if not self.seeded:
                    # Start from where the robot IS. Upstream's own policies do the same:
                    # they take the measured pose as init_motor_pos and interpolate to home
                    # from there, rather than commanding home outright.
                    self.seeded = True
                    here = np.asarray(self.safe.pose, dtype=np.float32).copy()
                    self.target = here
                    if not self.command.given:
                        # Nothing has asked for anything yet, so hold where the robot is
                        # rather than moving it to a pose nobody requested. A command that
                        # arrived before the first reading is honoured as given.
                        self.command.hold(here)
                        self.command.given = False
                    log.info("seeded from the first reading at %s", np.round(here, 3)[:4])
            else:
                # Keep the last good reading rather than dropping to None. A single dropped
                # packet is routine on this bus, and cancelling a gait mid-stride because of
                # one is worse than feeding the policy a reading one tick old.
                self.dropped += 1
                if self.dropped > DROP_LIMIT:
                    self.last_obs = None
            self._deadman()
            if not self.seeded:
                # No reading yet, so no idea where the robot is, so nothing is commanded.
                return self.target
            wanted = self.plan(dt)
            wanted = self._with_neck(wanted)
            if not np.all(np.isfinite(wanted)):
                # np.clip propagates NaN, rate_limit would then carry it into self.target, and
                # every later tick would rate-limit from NaN: one bad number latches forever.
                # json.loads accepts a bare NaN literal, so this is reachable from the wire.
                log.error("a non-finite target reached the loop; holding the last good pose")
                wanted = self.target
            wanted = clamp_to_limits(wanted, self.lo, self.hi)
            self.target = rate_limit(wanted, self.target)
            self.sim.set_motor_target(self.target)
            self.sim.step()
            return self.target

    def _with_neck(self, wanted: Any) -> Any:
        """Apply the head's own goal on top of whatever is driving the body.

        Looking around used to `hold()`, which cancelled a walk or a motion mid-stride and
        still answered accepted. The neck is independent of the gait on a real body, so it is
        an overlay here too: the policy owns the other twenty-eight joints and the head goes
        where it was last told.

        A head or gripper the deadman is bringing to rest (`resting`) slews to its place in the
        safe pose at RESET_VEL, as the body's own rest does, whatever drives the body, and on
        a robot lying down holds where it is, as the body does. Its rest ends once it is there,
        so it is a part nobody owns, holding, which a motion may move again; a rest that stayed
        for ever stalled every later motion on a head nobody held."""
        home = self.target if self.lying else self.command.default_pose
        for part in sorted(self.resting - {BODY}):
            joints = self._joints(part)
            away = float(np.max(np.abs(home[joints] - self.target[joints]), initial=0.0))
            best, still = self._rest_progress.get(part, (math.inf, 0))
            best, still = (away, 0) if away < best else (best, still + 1)
            self._rest_progress[part] = (best, still)
            # A rest that has stopped closing on its goal (a place in the safe pose outside the
            # joint's limits, which the clamp holds it from) ends after as many ticks as a
            # keyframe that cannot be reached is given, rather than refuse every motion for good.
            if away < ARRIVED_RAD or still >= FRAME_DWELL_LIMIT:
                if away >= ARRIVED_RAD:
                    log.warning(
                        "the %s's rest stopped closing on its place in the safe pose %.3f rad "
                        "short, outside its joint's limits by the look of it; it ends here and "
                        "the %s holds where it is",
                        part,
                        away,
                        part,
                    )
                self.resting.discard(part)
                self._rest_progress.pop(part, None)
                if self.command.mode == Command.HOLD:
                    # held where it arrived, not sent back to where the body's hold had it
                    self.command.goal[joints] = self.target[joints]
        rest = [i for part in self.resting - {BODY} for i in self._joints(part)]
        if self.neck_goal is None and not self.grip_goal and not rest:
            return wanted
        out = np.array(wanted, dtype=np.float32, copy=True)
        if self.neck_goal is not None and self.neck:
            yaw, pitch = self.neck_goal
            if self.neck_yaw is not None:
                out[self.neck_yaw] = yaw
            if self.neck_pitch is not None:
                out[self.neck_pitch] = pitch
        for index, value in self.grip_goal.items():
            out[index] = value
        if rest:
            out[rest] = slew(self.target[rest], home[rest], CONTROL_DT)
        return out

    def _overlaid(self) -> list[int]:
        """The joints an overlay holds this tick: an aimed head, a gripper goal, a part at rest."""
        held = list(self.grip_goal)
        if self.neck_goal is not None:
            held += self.neck
        return held + [i for part in self.resting - {BODY} for i in self._joints(part)]

    def _finished(self, parts: Any) -> None:
        """A stand or a motion has played to its end and the robot holds: the parts it drove are
        owned by nobody now, as after a stop, so its client's leaving moves nothing. A finished
        kneel kept its owner, and that client's death minutes later stood the robot up."""
        owner = self.owners.get(BODY)
        for part in parts:
            if part in self.owners and self.owners[part] is owner:
                del self.owners[part]
        self.motion_parts = []

    def _tell(self, client: Any, reason: str) -> None:
        """Owe `client` a notice that it lost a part, if it is still there to be told: a notice
        for a connection that has closed would be kept for the life of the daemon."""
        if client is not None and client in self.heard_at:
            self.lost[client] = reason

    def _joints(self, part: str) -> list[int]:
        """The motor indexes a part moves: the whole body, the neck, or one side's gripper."""
        if part == BODY:
            return list(range(len(self.order)))
        if part == NECK:
            return list(self.neck)
        return list(self.grippers.get(part.removesuffix("_gripper"), []))

    def release_grip(self) -> None:
        """Stop holding the gripper goal.

        `grip_goal` overlays every tick, so without this a closed gripper is held against
        its hard joint limit for the life of the process: through `stand`, through a
        motion, through the deadman's slew and through `settle()`, which could then never
        report the safe pose reached because the overlay keeps putting the gripper back."""
        with self.lock:
            self.grip_goal.clear()

    def set_grip(self, side: str, close: bool, client: Any = None) -> list[str]:
        """Command the gripper motors, and say which ones moved.

        Returning the names matters: `bot.grip` used to answer accepted and command nothing
        at all, on a capability taken from a flag rather than from the body.

        Which end of a gripper's travel is closed is not stated anywhere upstream, so it is
        an assumption (`upstream_api.GRIPPER_AXES`) and it is written down as one.
        """
        sides = ["left", "right"] if side == "both" else [side]
        indexes = [index for one in sides for index in self.grippers.get(one, [])]
        if not indexes:
            return []

        def grip() -> None:
            for index in indexes:
                low, high = float(self.lo[index]), float(self.hi[index])
                self.grip_goal[index] = low if close else high

        sided = [one for one in sides if self.grippers.get(one)]
        self.obey("bot.grip", grip, client, [f"{one}_gripper" for one in sided])
        return [self.order[index] for index in indexes]

    def halt(self) -> list[str]:
        """Hold every part where it was last measured, leave every part owned by nobody, and
        say what that stopped.

        The caller holds `lock`. The neck and gripper goals are overlays on whatever drives the
        body, so a stop that held only the body left a closing gripper closing on to its joint
        limit and the head slewing on to its last aim. The hold's pose is the measured one, head
        and hands included, so clearing the overlays is what holds them too. A part holding after
        a stop has no owner, so nothing a stop leaves can be moved by the deadman later: a stop
        arms nothing, the client's closing stop above all."""
        stopped: list[str] = []
        if self.command.mode != Command.HOLD:
            stopped.append(self.command.motion or self.command.mode)
        if self.neck_goal is not None:
            stopped.append("neck")
        stopped += [self.order[index] for index in sorted(self.grip_goal)]
        self.command.hold(self.safe.pose)
        if not self.seeded:
            # Before the first good reading `safe.pose` is the home pose, a guess, so holding it
            # would send the robot there; left ungiven, seeding holds where the robot really is.
            self.command.given = False
        self.neck_goal = None
        self.grip_goal.clear()
        self.owners.clear()
        self.resting.clear()
        self.motion_parts = []
        return stopped

    def play(self, name: str, frames: list[Any], client: Any = None) -> list[str]:
        """Start the motion `name`, as `client`'s change through `obey`: the caller holds `lock`.
        Answers the parts it kept as `client` holds them.

        A motion's keyframes move every joint, the head and the hands too, so it takes the neck
        and the grippers with the body from nobody. A head or hand `client` itself holds by a look
        or a grip stays held through the motion, which plays on the rest and does not wait for it:
        taking it would open a closed hand and drop what it held. A part another connection holds
        refuses the motion, with the reason, rather than play round a head it cannot reach, which
        stalled every frame; so does one the deadman is still bringing to rest, which it soon is
        not. While the robot is down after a floor motion, only another floor motion plays: an
        upright one waits for a stand."""
        if self.down_by and name not in FLOOR_MOTIONS:
            raise NotNow(f"the robot is down after {self.down_by}: stand it up first")
        others = [
            part.replace("_", " ")
            for part in self.parts
            if part != BODY and self.owners.get(part) not in (None, client) and self._holds(part)
        ]
        if others:
            raise NotNow(
                f"another connection is driving the {' and the '.join(others)}, which this "
                "motion moves too: a stop frees every part"
            )
        resting = sorted(part.replace("_", " ") for part in self.resting - {BODY})
        if resting:
            raise NotNow(
                f"the {' and the '.join(resting)} is still being brought to rest, which takes a "
                "moment, and a stop ends it at once; this motion moves it too"
            )
        self._end_motion()
        kept = [
            part
            for part in self.parts
            if part != BODY and self.owners.get(part) is client and self._holds(part)
        ]
        for part in self.parts:
            if part != BODY and part not in kept:
                self._let_go(part)
        self.command.play(name, frames)
        self.motion_parts = [part for part in self.parts if part not in kept]
        if name in FLOOR_MOTIONS:
            self.down_by = name
        return kept

    def stand(self) -> dict[str, Any]:
        """Stand, as `obey`'s change: the caller holds `lock`. Answers what the reply adds.

        From the floor, after a floor motion, the commanded pose arrives whatever the body does,
        so the stand is done only once the trunk reads upright, by the settle's own deadline for
        a body to reach the safe pose from that far; past it, it ends as not having got up."""
        self._end_motion()
        self.command.stand()
        self.rise_failed = None
        self.rise_deadline = None
        if not (self.down_by and self.lying):
            return {}
        travel = float(np.max(np.abs(self.command.default_pose - self.target)))
        self.rise_deadline = time.monotonic() + settle_budget(travel)
        return {"note": "from the floor: done once the trunk reads upright, and said if it is not"}

    def drive(self, walk_x: float, walk_y: float, walk_turn: float) -> None:
        """Walk, as `obey`'s change: the caller holds `lock`."""
        self._end_motion()
        self.command.drive(walk_x, walk_y, walk_turn)

    def _end_motion(self) -> None:
        """A new command for the body ends a motion that is playing: the head and the hands it
        took go back to nobody, so its client is not told later that it lost a part it was no
        longer driving, and nobody's motion is refused on its account. The body stays with the
        motion's client for the new command to take, and that is what tells it."""
        if self.command.mode == Command.MOTION:
            self._finished([part for part in self.motion_parts if part != BODY])

    def _holds(self, part: str) -> bool:
        """Whether a look or a grip is holding `part` this tick."""
        if part == BODY:
            return False
        if part == NECK:
            return self.neck_goal is not None
        return any(index in self.grip_goal for index in self._joints(part))

    def _let_go(self, part: str) -> None:
        """Drop the look or grip on `part`, if there is one."""
        if part == NECK:
            self.neck_goal = None
        elif part != BODY:
            for index in self._joints(part):
                self.grip_goal.pop(index, None)

    def stop(self, client: Any = None) -> dict[str, Any]:
        """A stop from any connection, answered truthfully whatever the daemon is doing.

        It is accepted in every state, because a stop that is refused is recorded as one that
        never arrived, and the advice for that, the power switch, is a fall on this robot. While
        a shutdown settles the robot, or after the loop has given up and settles it, the robot is
        already being brought to rest: the stop changes nothing and its note says which. Every
        other time it holds every part (`halt`), and each connection that owned one is told."""
        with self.lock:
            if self._shut:
                return {"accepted": True, "stopped": [], "note": SHUTTING_DOWN}
            if self.gave_up:
                return {"accepted": True, "stopped": [], "note": GAVE_UP}
            for owner in set(self.owners.values()):
                if owner is not client:
                    self._tell(owner, LOST_TO_A_STOP)
            return {"accepted": True, "stopped": self.halt()}

    def refusal(self, method: str) -> str | None:
        """Why `method` may not move the robot now, or None if it may. The caller holds `lock`.

        The one place that decides it, so no moving method can forget a reason another one
        checks. A stop is not judged here: it moves nothing, and `stop` answers it."""
        if self._shut:
            # The settle trips to the safe pose once and then ticks towards it. A command that
            # landed after that trip (a walk, a look putting the neck goal back) kept it from
            # ever arriving, and close() then turned torque off wherever the robot stood.
            return SHUTTING_DOWN
        if self.gave_up:
            # Accepting a move the loop will not carry out is a wrong answer, not a cautious one.
            return GAVE_UP
        if self.fallen and method in FALLEN_REFUSES:
            return "the robot has fallen"
        if self.down_by and method == "bot.command":
            # Lying down on purpose is no fall, and no stance to walk from either.
            return f"the robot is down after {self.down_by}: stand it up first"
        return None

    def obey(self, method: str, change: Any, client: Any = None, parts: Any = None) -> Any:
        """Make `client`'s change to `parts` of the robot, if it may move now, and answer what
        `change` returns; raise `NotNow` with the reason otherwise. `client` then owns `parts`,
        and only those (`touch`): a look never takes over a walk.

        The check and the change happen under one hold of `lock`, so nothing that stops the
        robot (a shutdown, the loop giving up) can come between them."""
        with self.lock:
            reason = self.refusal(method)
            if reason is not None:
                raise NotNow(reason)
            result = change()
            self.touch(client, parts)
            return result

    def aim_neck(self, yaw_rad: float, pitch_rad: float, client: Any = None) -> None:
        """Where the head should point. It does not change what the body is doing."""

        def aim() -> None:
            self.neck_goal = (float(yaw_rad), float(pitch_rad))

        self.obey("bot.look", aim, client, [NECK])

    def run(self) -> None:
        """The absolute schedule upstream uses, so a slow tick does not accumulate error."""
        self.running = True
        start = time.monotonic()
        self.loop_started = start
        step = 0
        recent: Any = collections.deque(maxlen=256)
        while self.running:
            try:
                self.tick()
            except Exception as e:
                # A tick that raises must not kill this thread and leave the socket answering
                # healthy while nothing drives the robot. Upstream surfaces a controller fault
                # as a bare KeyError, so this is a live path rather than a theoretical one.
                # Record it, force the deadman so the next good tick slews to safety, and stop
                # claiming health. After FAULT_LIMIT in a row it takes no more orders and only
                # settles, which needs ticks: so it goes on ticking, and stops only once the bus
                # has failed BUS_GONE_LIMIT in a row and there is nothing to settle with.
                self.faults += 1
                self.fault = f"{type(e).__name__}: {e}"
                log.exception("tick failed (%d in a row)", self.faults)
                with self.lock:
                    self.command.trip(self.command.default_pose)
                    if self.faults >= FAULT_LIMIT and not self.gave_up:
                        log.error(
                            "%d ticks failed in a row; taking no more orders and settling to "
                            "the safe pose",
                            self.faults,
                        )
                        self.gave_up = True
                        # the safe pose is the whole body, head and hands included
                        self.neck_goal = None
                        self.grip_goal.clear()
                if self.faults >= BUS_GONE_LIMIT:
                    log.error("%d ticks failed in a row; the bus is gone, stopping", self.faults)
                    self.running = False
            else:
                self.faults = 0
                self.fault = None
            step += 1
            now = time.monotonic()
            # A rate over the last second, not over the run. A lifetime average takes tens of
            # minutes to fall below the client's floor, so a loop that collapsed to 10 Hz an
            # hour in would still report 49 and the heartbeat would never fire.
            recent.append(now)
            # Keep two, always. Trimming to one leaves nothing to measure a span with, and
            # the fallback below is the lifetime average, which is what this window exists to
            # replace: a single tick over a second long would hide the stall it just had.
            while len(recent) > 2 and now - recent[0] > 1.0:
                recent.popleft()
            if len(recent) > 1:
                self.loop_hz = (len(recent) - 1) / max(1e-6, now - recent[0])
            else:
                # the very first tick, before the window has two samples to span
                self.loop_hz = step / max(1e-6, now - start)

            slack = start + CONTROL_DT * step - now
            if slack > 0:
                time.sleep(slack)
            elif slack < -CATCHUP_LIMIT_S:
                log.warning(
                    "the loop fell %.2f s behind; restarting the schedule rather than "
                    "running the backlog at full speed",
                    -slack,
                )
                start, step = time.monotonic(), 0

    # -- shutdown, which is the dangerous part --------------------------------------

    def settle(self, timeout_s: float | None = None) -> bool:
        """Reach the safe pose before anything is allowed to exit.

        Nothing upstream does this: its own shutdown disables torque with no lowering and no
        ramp, which on a standing humanoid is a fall.

        Left to itself this gives the slew a deadline sized to the distance it has to cover.
        A fixed 8s used to be less than a full-scale slew needs -- 1.5 rad at RESET_VEL is 5s
        of slew and closer to 9s of wall time -- so a shutdown from a wide pose gave up partway
        and torqued off a robot that was still moving, which is the fall this method exists to
        get in front of. The deadline is a ceiling for a body that is stuck, not a schedule:
        this returns the moment the pose arrives."""
        log.info("settling to the safe pose before shutdown")
        # The overlays are goals Roboglot asked for, and the safe pose is the whole body.
        self.release_grip()
        with self.lock:
            self.neck_goal = None
            # The settle owns every part from here: no deadman of a client's is left to run, and
            # every client that owned a part is told why it no longer does.
            for owner in set(self.owners.values()):
                self._tell(owner, SHUTTING_DOWN)
            self.owners.clear()
            self.resting.clear()
            travel = float(np.max(np.abs(self.command.default_pose - self.target)))
            self.command.trip(self.command.default_pose)
        if timeout_s is None:
            timeout_s = settle_budget(travel)
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            try:
                self.tick()
            except Exception:
                # `tick()` swallows a bad read; a bad WRITE still propagates. Letting it out
                # of here would abandon the slew halfway and go straight to close(), which is
                # the torque-off this whole method exists to get in front of. Keep trying: the
                # deadline is what ends this, not the first fault.
                log.exception("a tick failed while settling; continuing to the deadline")
                time.sleep(CONTROL_DT)
                continue
            with self.lock:
                if float(np.max(np.abs(self.command.default_pose - self.target))) < 1e-2:
                    log.info("settled")
                    return True
                if self.fallen:
                    # Rest for a body on the floor is holding where it lies (`plan`), which
                    # this tick has done: waiting out the deadline would change nothing.
                    log.warning("the robot is down, so it holds where it lies rather than settle")
                    return False
            time.sleep(CONTROL_DT)
        log.warning("did not settle within %.0fs; leaving it where it is", timeout_s)
        return False

    def shutdown(self) -> None:
        """Settle, then close, each under a hard deadline that needs no GIL (`hard_exit_after`).

        `close()` is bound without releasing the GIL and retries torque-off forever on an
        unresponsive bus, and a settle tick on a dead bus can block for good, so a stuck
        shutdown freezes every thread including this one. A torqued robot and a dead process
        beats a frozen process nobody can signal.

        The settle starts as soon as the loop is told to stop, so the body is never left
        uncommanded in between: closing the camera, which can wait seconds on a wedged USB
        read, comes after it.

        Entered once, from whatever started it. A caller on another thread (the control
        thread's excepthook while a signal's shutdown settles, say) waits until that one has
        closed, so it cannot leave while the robot is still being settled; the hard exit bounds
        the wait. The thread already in here, reached again from a signal handler that CPython
        runs between any two of its lines, returns at once, and what it interrupted goes on.
        No lock is taken on the way in: a signal landing while one was held by its own thread
        waited on it for ever, before the hard exit was armed."""
        if self.shutdown_here():
            return
        self._inside.here = True
        try:
            first = self._first_shutdown.pop()  # `list.pop` is atomic: one caller gets True
        except IndexError:
            first = False
        if not first:
            self._shut_done.wait()
            return
        # From here every moving method is refused (`refusal`), so a connected client cannot
        # keep the settle from the safe pose.
        self._shut = True
        # Read without the lock: a tick blocked on the bus may hold it, and the deadline has to
        # be armed before anything here waits on the lock or on the bus.
        travel = float(np.max(np.abs(self.command.default_pose - self.target)))
        hard_exit_after(settle_budget(travel) + CLOSE_TIMEOUT_S, "settling to the safe pose")
        try:
            self.running = False
            try:
                self.settle()
            except Exception:  # never let settling stop the close
                log.exception("settling failed; closing anyway")
            if self.camera is not None:
                hard_exit_after(CAMERA_JOIN_S + CLOSE_TIMEOUT_S, "closing the camera")
                try:
                    self.camera.close()
                except Exception:  # never let the camera stop the close
                    log.exception("closing the camera failed; closing the bus anyway")
            hard_exit_after(CLOSE_TIMEOUT_S, "closing the bus")
            self.sim.close()
        finally:
            faulthandler.cancel_dump_traceback_later()
            self._shut_done.set()

    def shutdown_here(self) -> bool:
        """Whether this very thread is already in `shutdown`: a signal handler that CPython runs
        inside it finds it is. Per thread, and with no lock, so a handler never waits on one."""
        return bool(getattr(self._inside, "here", False))

    # -- what the socket asks it ----------------------------------------------------

    def state(self) -> dict[str, Any]:
        with self.lock:
            return {
                "t": round(time.monotonic(), 3),
                "policy": self.command.motion or self.command.mode,
                # The client's posture words are a closed set (standing, sitting, fallen,
                # unknown), and a client from before refuses any other. A trunk past the
                # threshold that is no fall (down after a floor motion, or a stand from the floor
                # that did not get it up) is none of the first three, so it is `unknown`, never
                # `standing`; `down` says why.
                "posture": "fallen" if self.fallen else "unknown" if self.lying else "standing",
                "down": self.down_by,
                "fallen": self.fallen,
                "tilt_deg": round(self.tilt_deg, 1),
                "joints": {
                    k: round(float(v), 3) for k, v in zip(self.order, self.target, strict=True)
                },
                # By name, exactly as `aim_neck` commands them. Reporting by list position
                # while commanding by name means a robot whose motors are ordered
                # pitch-then-yaw reports the two swapped, and `search_scan` centres its sweep
                # on this number.
                "neck": {
                    "yaw_deg": round(math.degrees(float(self.target[self.neck_yaw])), 1)
                    if self.neck_yaw is not None
                    else 0.0,
                    "pitch_deg": round(math.degrees(float(self.target[self.neck_pitch])), 1)
                    if self.neck_pitch is not None
                    else 0.0,
                },
                "holding": {},
                "calibrated": self.calibrated,
                "moving": self.command.busy,
                "loop_hz": round(self.loop_hz, 1),
                "deadman_tripped": self.deadman_tripped,
                "age_ms": round((time.monotonic() - self.safe.stamp) * 1000.0, 1),
                "rejected_reads": self.safe.rejected,
            }

    def touch(self, client: Any = None, parts: Any = None) -> None:
        """`client` commanded `parts` (every part, for a caller inside this process that names
        none): it owns them from now on, they are no longer at rest, and its clock restarts. A
        connection that was driving one of them is told another connection took it, as a stop
        tells it; the caller inside this process is no connection, and tells nobody."""
        self.heard(client)
        for part in self.parts if parts is None else parts:
            previous = self.owners.get(part)
            if client is not None and previous is not client:
                self._tell(previous, LOST_TO_A_COMMAND.format(part=part.replace("_", " ")))
            self.owners[part] = client
            self.resting.discard(part)

    def heard(self, client: Any) -> None:
        """`client` is there: its clock restarts, which keeps alive the parts it owns and no
        other. Another connection's keepalive says nothing about the one driving a part.

        Without `lock`, as `touch` always was: a tick blocked on the bus holds the lock, and a
        keepalive must not stall the connection behind it, or its health question goes
        unanswered too."""
        if client is None:
            self.command.last_client = time.monotonic()
        else:
            self.heard_at[client] = time.monotonic()

    def left(self, client: Any) -> None:
        """`client`'s connection has closed: its clock goes, so the next tick brings what it
        owned to rest, and nothing is kept for it. Under `lock`, which every notice is written
        under (`_tell`), so none can be written between its check and this."""
        with self.lock:
            self.heard_at.pop(client, None)
            self.lost.pop(client, None)

    def _silent(self, client: Any) -> bool:
        last = self.command.last_client if client is None else self.heard_at.get(client)
        return last is None or time.monotonic() - last > DEADMAN_S

    def _deadman(self) -> None:
        """Bring to rest each part whose owner has said nothing for DEADMAN_S, or has gone, and
        only those. The caller holds `lock`.

        Rest is the safe pose, reached at RESET_VEL: the body's by the deadman's trajectory,
        waist first, a head's or a gripper's by its own (`_with_neck`); on a fallen robot every
        part holds where it lies instead (`plan`). A part with no owner, holding after a stop or
        never commanded, is never moved by it."""
        silent = [part for part, owner in self.owners.items() if self._silent(owner)]
        if not silent:
            return
        log.warning("the client driving %s went quiet; bringing it to rest", ", ".join(silent))
        for part in silent:
            del self.owners[part]
            self.resting.add(part)
            self._rest_progress.pop(part, None)
            if part == BODY:
                self.command.trip(self.command.default_pose)
            elif part == NECK:
                self.neck_goal = None
            else:
                for index in self._joints(part):
                    self.grip_goal.pop(index, None)

    @property
    def deadman_tripped(self) -> bool:
        """Whether the deadman is bringing any part to rest."""
        return bool(self.resting)


# ── the socket ──────────────────────────────────────────────────────────────────────────


class Handler(socketserver.StreamRequestHandler):
    daemon_ref: Daemon
    token: str | None
    capabilities: dict[str, bool]
    robot_name: str
    motors: int

    def handle(self) -> None:
        try:
            self._converse()
        finally:
            # Its clock goes with it, so what it owned is brought to rest at the next tick.
            self.daemon_ref.left(self)

    def _converse(self) -> None:
        authed = not self.token
        spoken = False
        while raw := self.rfile.readline(MAX_LINE + 1):
            if len(raw) > MAX_LINE:
                # No client opens with this, and a browser can: a URL past the limit would push
                # its request line out of the first place, and the body behind it would be read.
                if not spoken:
                    log.warning("closing a connection that opened with an oversized line")
                    return
                log.warning("dropping an oversized line from a client")
                while raw and not raw.endswith(b"\n"):
                    raw = self.rfile.readline(MAX_LINE + 1)
                continue
            if not raw.strip():
                continue
            first, spoken = not spoken, True
            if first and HTTP_REQUEST_LINE.match(raw):
                log.warning("closing a connection that opened with an HTTP request")
                return
            try:
                msg = json.loads(raw)
            except ValueError:
                continue
            # `null`, `7`, `[]` and `"hi"` are all valid JSON and none of them is a request.
            # `msg.get` on any of them raises outside the dispatch guard, which drops the
            # connection and loses every later verb on that session over one bad line.
            if not isinstance(msg, dict):
                log.warning("ignoring a line that is not a JSON-RPC object: %.80r", msg)
                continue
            method, params = msg.get("method"), msg.get("params")
            if not isinstance(params, dict):
                params = {}
            req_id = msg.get("id")
            try:
                result, authed = self._dispatch(method, params, authed)
            except _Refused as e:
                self._error(req_id, e.code, str(e))
                continue
            if req_id is not None:
                self._reply(req_id, result)
            elif method == "bot.keepalive" and (notice := self.daemon_ref.lost.pop(self, None)):
                # A keepalive has no answer, so a connection that lost a part to someone else
                # is told by a notification of its own, once, at its next keepalive.
                self._notify("bot.lost", {"reason": notice})

    def _dispatch(
        self, method: str | None, params: dict[str, Any], authed: bool
    ) -> tuple[Any, bool]:
        d = self.daemon_ref
        if method == "bot.hello":
            # As bytes: compare_digest raises on a str that is not ASCII. An empty token is no
            # token, as `main` reads one: comparing against "" admitted a hello that sent none.
            if self.token and not hmac.compare_digest(
                str(params.get("token") or "").encode("utf-8", "surrogatepass"),
                self.token.encode("utf-8", "surrogatepass"),
            ):
                raise _Refused(ERR_BAD_TOKEN, "bad or missing token")
            return {
                "protocol": PROTOCOL,
                "protocol_version": PROTOCOL_VERSION,
                "daemon_version": VERSION,
                "robot": self.robot_name,
                "motors": self.motors,
                "capabilities": dict(self.capabilities),
                # Whether a token was checked, which a client that sent one cannot otherwise
                # tell from a daemon that took it and checked nothing.
                "auth": "token" if self.token else "none",
            }, True
        if not authed:
            raise _Refused(ERR_BAD_TOKEN, "say bot.hello with a token first")
        # Only a method that drives the robot feeds the deadman: the keepalive here, and every
        # moving method once `Daemon.obey` has carried it out. Reading state, health or a
        # frame is a question, not driving: a client whose LLM stalled mid-verb, or which
        # polls while it thinks, would otherwise hold the deadman off for as long as it kept
        # asking. WALK is latched, so the robot would go on walking with nobody steering it,
        # which is the exact failure the deadman exists for.
        if method == "bot.keepalive":
            # A notification, and the only thing a client has to send to say it is still
            # there. It keeps alive the parts this connection owns, and no other (`heard`).
            d.heard(self)
            return None, authed
        if method in MOVING_METHODS:
            try:
                answer = self._move(d, str(method), params)
            except NotNow as e:
                return {"accepted": False, "reason": str(e)}, authed
            # A connection that lost a part to someone else learns it at its next command too.
            if answer.get("accepted") and (notice := d.lost.pop(self, None)):
                answer["lost"] = notice
            return answer, authed
        if method == "bot.state":
            return d.state(), authed
        if method == "bot.health":
            # Nothing here takes `lock`: a tick blocked on the bus holds it, and the loop's own
            # numbers stay at their last good values while it does. The age of the last tick's
            # end is the one thing that keeps moving.
            age = d.tick_age_s()
            reason = None
            if d.fault is not None:
                reason = f"the control loop faulted: {d.fault}"
            elif age is not None and age > TICK_STALE_S:
                reason = (
                    f"no control tick has ended for {age:.2f}s: the loop is stalled, and the "
                    "deadman inside it cannot fire"
                )
            elif d._shut:
                reason = SHUTTING_DOWN
            elif d.gave_up:
                reason = GAVE_UP
            elif d.fallen:
                reason = "the robot has fallen"
            elif d.rise_failed is not None:
                reason = d.rise_failed
            return {
                "ok": reason is None,
                "reason": reason,
                "loop_hz": round(d.loop_hz, 1),
                "tick_age_ms": None if age is None else round(age * 1000.0, 1),
            }, authed
        if method == "bot.frame":
            return {"jpeg": d.camera.latest() if d.camera is not None else None}, authed
        raise _Refused(ERR_UNKNOWN, f"unknown method {method!r}")

    def _move(self, d: Daemon, method: str, params: dict[str, Any]) -> dict[str, Any]:
        """One of MOVING_METHODS. Each checks what it was asked for, and its change reaches the
        loop only through `Daemon.obey`, which raises `NotNow` when the robot may not move.
        This connection then owns the part it commanded, and only that part. A stop is
        `Daemon.stop`: from anyone, in every state, owning nothing."""
        if method == "bot.stop":
            return d.stop(self)
        if method == "bot.stand":
            return {"accepted": True, **d.obey(method, d.stand, self, [BODY])}
        if method == "bot.perform":
            name = str(params.get("motion", ""))
            frames = d.motion_library.get(name)
            if not frames:
                return {"accepted": False, "reason": f"no motion named {name!r}"}
            kept = d.obey(method, lambda: d.play(name, frames, self), self, d.parts)
            # what this connection's own look or grip still holds through the motion
            return {"accepted": True, "kept": kept} if kept else {"accepted": True}
        if method == "bot.look":
            if not self.capabilities.get("neck"):
                return {"accepted": False, "reason": "this build has no neck"}
            d.aim_neck(
                math.radians(_number(params, "yaw_deg")),
                math.radians(_number(params, "pitch_deg")),
                self,
            )
            return {"accepted": True}
        if method == "bot.command":
            if not self.capabilities.get("walk"):
                return {"accepted": False, "reason": "no walk policy is staged"}
            asked = (
                _number(params, "walk_x"),
                _number(params, "walk_y"),
                _number(params, "walk_turn"),
            )
            walk = within_envelope(asked, d.walk_envelope)
            d.obey(method, lambda: d.drive(*walk), self, [BODY])
            if walk == asked:
                return {"accepted": True}
            return {"accepted": True, "clamped_to": dict(zip(WALK_KEYS, walk, strict=True))}
        # bot.grip, the last of MOVING_METHODS
        if not self.capabilities.get("gripper"):
            return {"accepted": False, "reason": "this build has no grippers"}
        side = str(params.get("side", "right"))
        if side not in ("left", "right", "both"):
            raise _Refused(ERR_REFUSED, f"unknown side {side!r}")
        moved = d.set_grip(side, bool(params.get("close", True)), self)
        if not moved:
            return {"accepted": False, "reason": f"this robot has no gripper motor on the {side}"}
        return {"accepted": True, "motors": moved}

    def _reply(self, req_id: Any, result: Any) -> None:
        self.wfile.write(
            (
                json.dumps({"jsonrpc": JSONRPC_VERSION, "id": req_id, "result": result}) + "\n"
            ).encode()
        )

    def _notify(self, method: str, params: dict[str, Any]) -> None:
        """A message to this client that answers nothing it asked."""
        payload = {"jsonrpc": JSONRPC_VERSION, "method": method, "params": params}
        self.wfile.write((json.dumps(payload) + "\n").encode())

    def _error(self, req_id: Any, code: int, message: str) -> None:
        if req_id is None:
            return
        payload = {
            "jsonrpc": JSONRPC_VERSION,
            "id": req_id,
            "error": {"code": code, "message": message},
        }
        self.wfile.write((json.dumps(payload) + "\n").encode())


def _number(params: Any, key: str, default: float = 0.0) -> float:
    """A float off the wire, or a refusal.

    `json.loads` accepts a bare NaN and Infinity by default, and `float(None)` raises a
    TypeError that would escape the handler and drop the connection rather than answer
    it. Refusal is data here too."""
    value = params.get(key, default) if isinstance(params, dict) else default
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise _Refused(ERR_REFUSED, f"{key} must be a number, got {value!r}") from None
    if not math.isfinite(number):
        raise _Refused(ERR_REFUSED, f"{key} must be finite, got {number!r}")
    return number


class _Refused(Exception):
    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code


class NotNow(Exception):
    """Why the robot may not move at the moment. A client hears it as a refusal with this
    reason, the way it hears "the robot has fallen", and not as a protocol error."""


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True
    address_family = socket.AF_INET


# ── the fake body, so the whole daemon runs with nothing installed ──────────────────────


class FakeRobot:
    def __init__(self, name: str = "toddlerbot_2xc", nu: int | None = None) -> None:
        # The gripper builds carry two more motors than the plain ones, and the difference
        # matters: a keyframe file is per variant, and grip has to find a motor to command.
        grippers = ["left_gripper", "right_gripper"] if "gripper" in name else []
        self.nu = nu if nu is not None else 30 + len(grippers)
        self.name = name
        head = ["neck_yaw", "neck_pitch", "waist_roll", "waist_yaw"]
        filler = [f"joint_{i}" for i in range(self.nu - len(head) - len(grippers))]
        self.motor_ordering = head + filler + grippers
        self.motor_limits = {k: (-2.0, 2.0) for k in self.motor_ordering}
        self.default_motor_angles = {k: 0.0 for k in self.motor_ordering}
        self.roboglot_calibrated = True


class FakeSim:
    """A body that holds what it was told, so the loop and the protocol are exercised whole."""

    def __init__(self, robot: FakeRobot) -> None:
        self.robot = robot
        # Not exactly zeros. All-zeros is the sentinel a dropped packet returns on this bus,
        # so the detector refuses it, and a fake that starts there is a fake the daemon can
        # never seed itself from. A real robot is never at exactly zero either.
        # Annotated shape-free on purpose: numpy 2.2's stubs infer a fixed 1-D shape from
        # `np.full` and then refuse the `asarray(...).copy()` that set_motor_target stores.
        self.pos: NDArray[np.float32] = np.full(robot.nu, 0.01, dtype=np.float32)
        self.writes = 0
        self.closed = False
        self.drop_next = False

    def get_observation(self) -> Any:
        pos = np.zeros(self.robot.nu, np.float32) if self.drop_next else self.pos.copy()
        self.drop_next = False
        return type("Obs", (), {"motor_pos": pos, "motor_vel": np.zeros_like(pos), "rot": None})()

    def set_motor_target(self, target: Any) -> None:
        self.pos = np.asarray(target, dtype=np.float32).copy()
        self.writes += 1

    def step(self) -> None:
        pass

    def close(self) -> None:
        self.closed = True


def load_motions(root: str, robot_name: str, robot: Any = None) -> dict[str, list[Any]]:
    """Read the keyframes upstream ships. There is no loader upstream to call.

    Every call site there does `joblib.load(path)` inline, so this does the same. The
    files are lz4-framed pickles carrying an `action` array of per-frame motor positions,
    in radians, in `robot.motor_ordering`, at fifty hertz, which is this loop's own rate.

    A motion that is missing, unreadable, or carries no action array is not offered.
    Roboglot would rather publish a shorter list than a verb that refuses on a robot.
    """
    suffix = "_2xc" if "_2xc" in robot_name else "_2xm"
    paths = {n: os.path.join(root, "motion", f"{n}{suffix}.lz4") for n in MOTIONS}
    present = {n: q for n, q in paths.items() if os.path.exists(q)}
    for name in MOTIONS:
        if name not in present:
            log.warning("motion %r is not at %s, so it is not offered", name, paths[name])
    if not present:
        return {}
    try:
        import joblib  # late: upstream's own dependency, and not one this side has
    except ImportError:
        log.error("joblib is not installed beside upstream, so no motion can be loaded")
        return {}

    library: dict[str, list[Any]] = {}
    for name, path in present.items():
        try:
            data = joblib.load(path)
            action = data["action"] if isinstance(data, dict) else None
            if action is None:
                log.warning("motion %r has no action array, so it is not offered", name)
                continue
            frames = np.asarray(action, dtype=np.float32)
        except Exception:
            log.exception("motion %r failed to load, so it is not offered", name)
            continue
        if frames.ndim != 2:
            log.error("motion %r is shaped %s, not frames of motors", name, frames.shape)
            continue
        # The keyframe files are per variant, and the gripper builds have two more motors
        # than the plain ones. A 30-wide frame sent to a 32-motor robot is not a short read,
        # it is a command that means something different on every joint after the first
        # mismatch, so it is refused rather than padded.
        # `robot` is optional here, so fall back to the frame's own width, which makes
        # the check a no-op rather than a crash when nobody passed a body.
        nu = getattr(robot, "nu", None)
        wanted = int(nu) if nu is not None else int(frames.shape[1])
        if frames.shape[1] != wanted:
            log.error(
                "motion %r has %d motors and this robot has %d, so it is not offered",
                name,
                frames.shape[1],
                wanted,
            )
            continue
        library[name] = [frames[i] for i in range(len(frames))]
        log.info("motion %r: %d frames", name, len(frames))
    return library


class CameraFeed:
    """Upstream's `Camera`, on its own thread, because it cannot be called from the loop.

    Its constructor scans `/dev`, shells out to `v4l2-ctl` and unpickles a calibration
    file relative to the working directory, and `get_frame` raises rather than returning
    None on a failed read. A stalled USB device on the control thread would cost ticks on
    a robot that falls over when the ticks stop.

    Roboglot encodes the frame itself rather than calling upstream's `get_jpeg`, for two
    reasons. That returns a `(buffer, array)` pair rather than bytes. And it hands an RGB
    array to `cv2.imencode`, which expects BGR, so its JPEG comes out with red and blue
    swapped, which would quietly break every colour the detector looks for.
    """

    def __init__(self, side: str) -> None:
        from toddlerbot.sensing.camera import Camera  # late: needs cv2 and a real device

        self.camera = Camera(side)
        self.lock = threading.Lock()
        self.jpeg: bytes | None = None
        self.taken = 0.0
        self.running = True
        self.thread = threading.Thread(target=self._loop, daemon=True, name="roboglot-camera")
        self.thread.start()

    def _loop(self) -> None:
        import cv2  # late: upstream's own dependency, beside the Camera above

        while self.running:
            try:
                frame = self.camera.get_frame()  # BGR, which is what imencode wants
                ok, buf = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
            except Exception:
                log.exception("camera read failed; keeping the last frame")
                time.sleep(0.5)
                continue
            if ok:
                with self.lock:
                    self.jpeg = buf.tobytes()
                    self.taken = time.monotonic()
            time.sleep(CAMERA_PERIOD_S)

    def latest(self) -> str | None:
        with self.lock:
            jpeg, taken = self.jpeg, self.taken
        if not jpeg or time.monotonic() - taken > CAMERA_STALE_S:
            return None
        return base64.b64encode(jpeg).decode("ascii")

    def close(self) -> None:
        self.running = False
        self.thread.join(timeout=CAMERA_JOIN_S)
        # upstream's own close, which the device needs and which nothing else calls
        with contextlib.suppress(Exception):
            self.camera.close()


def build_walk_policy(ckpt: str, robot: Any, init_pos: Any, root: str) -> tuple[Any, Any]:
    """Load a local ONNX walk checkpoint, and read the envelope it was really trained on.

    Upstream's only loader reaches for a wandb artifact when the file is missing, so Roboglot
    checks for the file first: a robot should not silently download the thing that decides
    how it walks. That loader's annotation says it returns a dict and it returns the
    directory, and the checkpoint is unusable without the `env_config.json` beside it.

    The envelope is why this is read here rather than hardcoded. `command_range` comes
    from the checkpoint's own config, so it is the range this policy was actually trained
    on, and the walk velocities are rows five, six and seven: the first five are
    upper-body pose commands.
    """
    ckpt_dir = os.path.join(root, "ckpts", ckpt)
    for path in (
        os.path.join(ckpt_dir, "model_best.onnx"),
        os.path.join(ckpt_dir, "env_config.json"),
    ):
        if not os.path.exists(path):
            raise SystemExit(
                f"--walk-policy {ckpt!r} needs {path}, which is not there. Upstream "
                "publishes no checkpoint and checks none in, so this is a file you "
                "supply. Roboglot will not reach for a wandb artifact from a robot."
            )
    from toddlerbot.policies.walk import WalkPolicy  # late: onnxruntime, jax and wandb

    policy = WalkPolicy(ckpt, robot, init_pos, ckpt_dir)
    rows = np.asarray(policy.command_range, dtype=np.float32)
    if rows.ndim != 2 or len(rows) < 8:
        raise SystemExit(
            f"the checkpoint at {ckpt_dir} reports a command_range shaped {rows.shape}, "
            "and Roboglot expected at least eight rows with the walk velocities at five, "
            "six and seven. Refusing to guess which rows are the velocities."
        )
    # The trained range is asymmetric (forward further than backward) and Roboglot's limits
    # are symmetric, so the honest reading of each row is its tighter half.
    envelope = {
        "max_vx": float(min(abs(rows[5][0]), abs(rows[5][1]))),
        "max_vy": float(min(abs(rows[6][0]), abs(rows[6][1]))),
        "max_wz": float(min(abs(rows[7][0]), abs(rows[7][1]))),
    }
    log.info("walk checkpoint %s loaded; envelope %s", ckpt, envelope)
    return policy, envelope


# ── wiring it up ────────────────────────────────────────────────────────────────────────


def build_real(robot_name: str, root: str) -> tuple[Any, Any]:
    """Construct upstream's Robot and RealWorld, under a watchdog.

    The constructor busy-waits forever on a silent IMU with the motors already live and
    torqued, and nothing inside the class can recover from that, so it runs in a thread this
    one is willing to abandon."""
    os.chdir(root)  # every description path upstream builds is relative
    sys.path.insert(0, root)
    from toddlerbot.sim.robot import Robot  # late: importable only after the chdir above

    robot = Robot(robot_name)
    motors_yml = os.path.join(root, "toddlerbot", "descriptions", robot_name, "motors.yml")
    robot.roboglot_calibrated = os.path.exists(motors_yml)
    if not robot.roboglot_calibrated:
        raise SystemExit(
            f"{robot_name} has no zero calibration at {motors_yml}, so every commanded angle "
            "would be offset by however this robot was assembled. Run upstream's "
            "calibrate_zero first. Roboglot refuses to actuate without it."
        )

    box: dict[str, Any] = {}

    def construct() -> None:
        from toddlerbot.sim.real_world import (
            RealWorld,
        )  # late: this is the import that needs abandoning

        box["sim"] = RealWorld(robot)

    thread = threading.Thread(target=construct, daemon=True, name="roboglot-construct")
    thread.start()
    thread.join(CONSTRUCT_TIMEOUT_S)
    if "sim" not in box:
        raise SystemExit(
            f"the robot did not finish connecting in {CONSTRUCT_TIMEOUT_S:.0f}s. Its "
            "constructor busy-waits forever on a silent IMU, with the motors already "
            "powered, so check the IMU before anything else."
        )
    sim = box["sim"]
    if not getattr(sim, "controllers", None):
        raise SystemExit("no Dynamixel controllers were found; the robot is not on its bus")
    return robot, sim


# ── who may drive it ────────────────────────────────────────────────────────────────────


class TokenFileError(RuntimeError):
    """A token file that was named and is missing, unreadable, or holds no token."""


def read_token(path: str) -> str:
    """The token in `path`. The Jetson and MOSS daemons' rules: the flag has no default, so a
    file that is named is a token asked for, and a missing, unreadable or empty one refuses to
    start rather than starting with nobody checked, which no client could tell from a daemon
    that had checked it."""
    try:
        with open(path, encoding="utf-8") as fh:
            token = fh.read().strip()
    except UnicodeDecodeError:
        # Not the decoder's own words: they name the byte it stopped at, which is a byte of
        # the token, and this refusal goes to the journal.
        raise TokenFileError(
            f"the token file {path} is not UTF-8 text. Refusing to start without it."
        ) from None
    except OSError as e:
        raise TokenFileError(
            f"cannot read the token file {path}: {e}. Refusing to start without it."
        ) from e
    if not token:
        raise TokenFileError(f"the token file {path} is empty. Refusing to start without a token.")
    return token


def resolve_token(args: argparse.Namespace) -> str | None:
    """`--token-file`, else `--token`, else the environment. An empty token, from the flag or
    the environment, is no token: kept as "", it admitted a hello that carried none."""
    if args.token_file:
        return read_token(args.token_file)
    if args.token is not None:
        if not args.token.strip():
            return None
        log.warning(
            "a token given with --token can be read by every account on this machine (ps, "
            "/proc/<pid>/cmdline); prefer --token-file or %s",
            TOKEN_ENV,
        )
        return str(args.token)
    value = os.environ.get(TOKEN_ENV, "")
    return value if value.strip() else None


def is_loopback(host: str) -> bool:
    """Whether `host` is reachable from this machine alone: `localhost`, or an address in
    127.0.0.0/8 or ::1. Any other name can resolve to anything, so it counts as wide."""
    name = host.strip().lower()
    if name == "localhost":
        return True
    try:
        return ipaddress.ip_address(name).is_loopback
    except ValueError:
        return False


def open_address_refusal(host: str, token: str | None, *, allow: bool) -> str | None:
    """Why the daemon will not start on `host` with `token`, or None if it may. It logs a
    warning when it lets an open start through on purpose.

    Off loopback with no token, anything that can reach the address could drive the robot, so
    that start needs saying twice. Loopback with no token is what it always was: the docs reach
    it through an ssh tunnel."""
    if token or is_loopback(host):
        return None
    if allow:
        log.warning(
            "listening on %s with no token, on purpose (--allow-no-token): anything that can "
            "reach this address can drive the robot",
            host,
        )
        return None
    return (
        f"refusing to listen on {host} with no token: anything that can reach that address "
        f"could drive this robot. Give it a token with --token-file (or {TOKEN_ENV}), or pass "
        "--allow-no-token to serve anyone who can reach it, on purpose."
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=DESCRIPTION)
    parser.add_argument("--robot", default="toddlerbot_2xc")
    parser.add_argument("--toddlerbot", default=os.environ.get("TODDLERBOT_ROOT", "."))
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    tokens = parser.add_mutually_exclusive_group()
    tokens.add_argument(
        "--token",
        default=None,
        help=f"The token clients must send (default: ${TOKEN_ENV}). Every account on this "
        "machine can read a command line, so prefer --token-file.",
    )
    tokens.add_argument(
        "--token-file",
        default=None,
        help="Read the token from this file. Refuses to start if it is missing, unreadable "
        "or empty.",
    )
    parser.add_argument(
        "--allow-no-token",
        action="store_true",
        help="Serve anyone who can reach a --host that is not loopback, with no token, on "
        "purpose. Without it that start is refused.",
    )
    parser.add_argument("--fake", action="store_true", help="Run with a fake body, no robot.")
    parser.add_argument(
        "--camera",
        choices=("left", "right"),
        default=None,
        help="Open this camera. Upstream's Camera takes a side, not an index.",
    )
    parser.add_argument(
        "--walk-policy",
        default=None,
        metavar="NAME",
        help="Run name under ckpts/ holding model_best.onnx and env_config.json.",
    )
    parser.add_argument("--gripper", action="store_true", help="This build has grippers.")
    parser.add_argument("--once", action="store_true", help="Set up, report, and exit.")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    if np is None:
        sys.stderr.write("Roboglot's ToddlerBot daemon needs numpy\n")
        return 2

    # Who may drive the robot is settled before the robot is touched, so a start refused here
    # has energised nothing.
    try:
        token = resolve_token(args)
    except TokenFileError as e:
        log.error("%s", e)
        sys.stderr.write(f"{e}\n")
        return 2
    refused = open_address_refusal(args.host, token, allow=args.allow_no_token)
    if refused is not None:
        log.error("%s", refused)
        sys.stderr.write(refused + "\n")
        return 2

    # Resolved once, before anything changes directory. build_real chdirs into the checkout,
    # so a second os.path.abspath of a relative --toddlerbot would resolve against the new
    # directory and quietly name a different path.
    root = os.path.abspath(os.path.expanduser(args.toddlerbot))

    if args.fake:
        robot: Any = FakeRobot(args.robot)
        sim: Any = FakeSim(robot)
    else:
        try:
            robot, sim = build_real(args.robot, root)
        except ImportError as e:
            sys.stderr.write(
                "Roboglot's ToddlerBot daemon needs upstream installed on this robot:\n"
                "  git clone https://github.com/hshi74/toddlerbot\n"
                "  cd toddlerbot && pip install -e .\n"
                f"Point --toddlerbot at that checkout. ({e})\n"
            )
            return 2

    # the fake body has no bulk read to drop
    daemon = Daemon(robot, sim, fake=args.fake, bus=not args.fake)

    # From here the motors are live and torqued: upstream's RealWorld constructor energises
    # them the moment it returns. Everything that follows can fail, and anything that leaves
    # by exception or SystemExit would otherwise reach the C level atexit, which disconnects
    # every client, which disables torque, which drops a standing robot. So the rest of
    # start-up settles first and exits second.
    def die(message: str) -> int:
        log.error("%s", message)
        daemon.shutdown()
        sys.stderr.write(message + "\n")
        return 2

    try:
        if not args.fake:
            daemon.motion_library = load_motions(root, args.robot, robot)
        if args.camera:
            try:
                daemon.camera = CameraFeed(args.camera)
            except Exception:
                # Not fatal, and not papered over either: the robot simply has no camera,
                # the handshake says so, and Roboglot never declares `observe`.
                log.exception("the %s camera did not open, so this robot has none", args.camera)
        if args.walk_policy and not args.fake:
            daemon.walk_policy, daemon.walk_envelope = build_walk_policy(
                args.walk_policy, robot, daemon.target, root
            )
    except SystemExit as e:
        return die(str(e) or "start-up refused")
    except Exception as e:
        return die(f"start-up failed after the motors were live: {e!r}")

    # Every one of these is what actually loaded, never what the operator claimed. A
    # capability Roboglot reports is a verb Roboglot will offer, and a verb it offers is one it
    # has to be able to deliver.
    capabilities: dict[str, Any] = {
        "camera": daemon.camera is not None,
        "neck": bool(daemon.neck),
        "gripper": bool(args.gripper) and any(daemon.grippers.values()),
        "walk": daemon.walk_policy is not None,
        "deadman": True,
        "motions": sorted(daemon.motion_library),
    }
    if daemon.walk_envelope is not None:
        capabilities["walk_envelope"] = daemon.walk_envelope
    if args.once:
        log.info("robot=%s motors=%d capabilities=%s", args.robot, robot.nu, capabilities)
        # On a real body this has already energised every motor, so it cannot simply return:
        # the interpreter exit would reach upstream's atexit and drop the robot where it
        # stands. Settle, then close, then leave.
        daemon.shutdown()
        return 0

    # Nothing upstream installs a signal handler, and its C level atexit does not run on
    # SIGTERM at all, so a systemd stop would leave a torqued robot holding its last target.
    def on_signal(signum: int, _frame: Any) -> None:
        # The settle takes seconds and prints nothing while it runs, so an operator's reflex
        # is a second Ctrl-C. A signal that lands inside a settle on this thread, whichever
        # thing began the shutdown (a first signal, or main's `finally` after the server died),
        # returns and lets it finish: raising here went straight through the slew, skipped the
        # close, and the interpreter's exit then turned torque off wherever the robot stood.
        if daemon.shutdown_here():
            log.warning("signal %s: already settling, and it will not be hurried", signum)
            return
        log.warning("signal %s: settling before exit", signum)
        daemon.shutdown()  # on another thread already? This waits for it to have closed.
        raise SystemExit(0)

    for sig in (signal.SIGINT, getattr(signal, "SIGTERM", signal.SIGINT)):
        signal.signal(sig, on_signal)

    # A signal handler is not enough. Upstream's C level atexit disconnects every client
    # on any interpreter exit, and disconnecting disables torque, so an unhandled
    # exception anywhere drops a standing robot before Python gets a say. These settle
    # first, then let the original hook print the traceback that says why.
    def settle_first(original: Any) -> Any:
        def hook(*args: Any) -> None:
            log.error("unhandled exception; settling to the safe pose before anything else")
            try:
                daemon.shutdown()
            except Exception:
                log.exception("settling failed on the way out; closing anyway")
            original(*args)

        return hook

    def settle_if_it_was_driving(original: Any) -> Any:
        """Thread deaths are not all the same.

        `threading.excepthook` is global. Shutting the whole robot down because the camera
        thread raised would settle a robot that was walking perfectly well, so only the
        thread that drives it gets that treatment. Every other thread is logged, and the
        control loop's own fault handling deals with the rest.
        """

        def hook(args: Any) -> None:
            if getattr(getattr(args, "thread", None), "name", "") == CONTROL_THREAD:
                settle_first(original)(args)
                return
            log.exception(
                "thread %s died: %s",
                getattr(getattr(args, "thread", None), "name", "?"),
                args.exc_value,
            )
            original(args)

        return hook

    sys.excepthook = settle_first(sys.excepthook)
    threading.excepthook = settle_if_it_was_driving(threading.excepthook)

    loop = threading.Thread(target=daemon.run, daemon=True, name=CONTROL_THREAD)
    loop.start()

    Handler.daemon_ref = daemon
    Handler.token = token
    Handler.capabilities = capabilities
    Handler.robot_name = args.robot
    Handler.motors = int(robot.nu)
    with Server((args.host, args.port), Handler) as server:
        log.info("listening on tcp://%s:%d for %s", args.host, args.port, args.robot)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            daemon.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
