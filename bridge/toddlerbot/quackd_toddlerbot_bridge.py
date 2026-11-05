#!/usr/bin/env python3
"""quackd's ToddlerBot daemon: the fifty hertz loop, and everything upstream does not do.

ToddlerBot has no network API of any kind. No socket, no daemon, no IPC: it is a Python
library whose control loop opens serial ports in-process. So quackd ships this, the way it
ships one for the Open Duck Mini, and it runs on the robot in upstream's own environment.

It exists for two reasons, and the second is the important one.

**A verb is episodic and this robot is not.** `RealWorld.step()` is a no-op, so nothing times
out and nothing re-arms: the last commanded pose is held forever. A humanoid frozen mid-stride
while an LLM thinks is a humanoid on the floor, so the loop has to run continuously
and quackd's intents only nudge what it is doing.

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
2. a hard-exit timer around `close()`, which is bound without releasing the GIL and can block
   forever on an unresponsive bus, freezing every thread that might have supervised it;
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

- **It never imports quackd.** quackd's dependencies do not belong on a robot.
- **It ships in the sdist and never in the wheel**, so `packages` stays `["quackd"]`.
- **It is testable with no hardware.** Everything above the `Robot` boundary is pure and takes
  plain arrays, and `--fake` runs the whole daemon and protocol against a simulated body.

    python quackd_toddlerbot_bridge.py --robot toddlerbot_2xc --fake
    python quackd_toddlerbot_bridge.py --robot toddlerbot_2xc --toddlerbot ~/toddlerbot
"""

from __future__ import annotations

import argparse
import base64
import collections
import contextlib
import hmac
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
PROTOCOL = "quackd-toddlerbot-bridge"
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
TOKEN_ENV = "QUACKD_TODDLERBOT_TOKEN"

CONTROL_HZ = 50.0
CONTROL_DT = 1.0 / CONTROL_HZ
DEADMAN_S = 0.5
"""How long quackd may go quiet before the loop stops taking its word for anything. It does
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
CONTROL_THREAD = "quackd-control"
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
CAMERA_STALE_S = 2.0
"""How old the newest frame may be before it is no frame at all. A wedged USB camera
would otherwise serve its last good picture forever, and a pilot would navigate by a
photograph of somewhere the robot used to be."""
"""Ten frames a second. quackd asks for one when it wants one, so this only bounds how
stale the newest can be, and a USB read is expensive enough not to do at fifty."""

MOTIONS = ("hold", "kneel", "cuddle", "push_up", "crawl")
"""The keyframe motions quackd offers, out of the nine that ship.

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

log = logging.getLogger("quackd-toddlerbot")


# ── the pure core: no hardware, no upstream, no quackd ──────────────────────────────────


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

    It is indistinguishable only because a real bus can fail that way. A simulator cannot:
    there is no bulk read to drop, and all-zeros with zero velocity is simply a body standing
    at its home pose, which is exactly where upstream's MuJoCo model starts. So the caller
    decides whether the guard applies, and `Daemon.bus` is how it knows."""
    if pos is None or len(pos) == 0:
        return True
    return bool(np.all(pos == 0.0) and np.all(vel == 0.0))


def tilt_degrees(gravity_z: float) -> float:
    """How far off upright, from the body-frame gravity vector's vertical component."""
    return math.degrees(math.acos(max(-1.0, min(1.0, gravity_z))))


def slew(current: Any, goal: Any, dt: float, vel: float = RESET_VEL) -> Any:
    """One step of a bounded-rate move towards a goal."""
    return rate_limit(goal, current, max_step=vel * dt)


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
    """The last observation quackd is willing to believe, and the pose derived from it.

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


class Daemon:
    """Owns the robot, the loop and the safe pose. Everything else asks it politely."""

    def __init__(self, robot: Any, sim: Any, *, fake: bool = False, bus: bool = True) -> None:
        self.robot = robot
        self.sim = sim
        self.fake = fake
        #: Whether readings arrive over a serial bus that can hand back a zeroed buffer. On
        #: hardware they do, and an all-zeros read is refused rather than driven to. On a
        #: simulator they do not, and refusing it means refusing the home pose upstream's
        #: MuJoCo body starts in: every read rejected, `plan` never reached, and a `stand`
        #: that reports itself moving for as long as anybody waits without a joint turning.
        self.bus = bus
        self.lock = threading.Lock()
        self.running = False
        self.loop_hz = 0.0
        self.fallen = False
        self.tilt_deg = 0.0
        self.deadman_tripped = False
        self.fault: str | None = None
        self.dropped = 0
        self.neck_goal: Any = None
        self.grip_goal: dict[int, float] = {}
        self.faults = 0
        self._shut = False
        self.calibrated = bool(getattr(robot, "quackd_calibrated", False))
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
        self.command.last_client = time.monotonic()
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
                self.fallen = self.tilt_deg > FALL_TILT_DEG
            except Exception:  # orientation is advisory, never fatal
                pass
        return obs

    def plan(self, dt: float) -> Any:
        """What this tick's target is, before any clamping."""
        cmd = self.command
        current = self.safe.pose
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
            if float(np.max(np.abs(goal - nxt))) < 1e-3 and cmd.mode == Command.STAND:
                cmd.hold(goal)
            return nxt
        if cmd.mode == Command.MOTION:
            if cmd.frame_index >= len(cmd.frames):
                cmd.hold(current)
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
            # about which one ran.
            away = float(np.max(np.abs(frame - self.target)))
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
            # The policy owns the gait. quackd feeds it and clamps what comes back, because
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
        """One control step: read, decide, clamp, write. Never raises for a bad reading."""
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
            if time.monotonic() - self.command.last_client > DEADMAN_S:
                if not self.deadman_tripped:
                    log.warning("quackd went quiet; slewing to the safe pose and holding")
                    self.deadman_tripped = True
                    # The safe pose is the whole body, including the head and the hands.
                    self.neck_goal = None
                    self.grip_goal.clear()
                    self.command.trip(self.command.default_pose)
            else:
                self.deadman_tripped = False
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
        """
        if self.neck_goal is None and not self.grip_goal:
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
        return out

    def release_grip(self) -> None:
        """Stop holding the gripper goal.

        `grip_goal` overlays every tick, so without this a closed gripper is held against
        its hard joint limit for the life of the process: through `stand`, through a
        motion, through the deadman's slew and through `settle()`, which could then never
        report the safe pose reached because the overlay keeps putting the gripper back."""
        with self.lock:
            self.grip_goal.clear()

    def set_grip(self, side: str, close: bool) -> list[str]:
        """Command the gripper motors, and say which ones moved.

        Returning the names matters: `bot.grip` used to answer accepted and command nothing
        at all, on a capability taken from a flag rather than from the body.

        Which end of a gripper's travel is closed is not stated anywhere upstream, so it is
        an assumption (`upstream_api.GRIPPER_AXES`) and it is written down as one.
        """
        sides = ["left", "right"] if side == "both" else [side]
        moved: list[str] = []
        with self.lock:
            for one in sides:
                for index in self.grippers.get(one, []):
                    low, high = float(self.lo[index]), float(self.hi[index])
                    self.grip_goal[index] = low if close else high
                    moved.append(self.order[index])
        return moved

    def aim_neck(self, yaw_rad: float, pitch_rad: float) -> None:
        """Where the head should point. It does not change what the body is doing."""
        with self.lock:
            self.neck_goal = (float(yaw_rad), float(pitch_rad))

    def run(self) -> None:
        """The absolute schedule upstream uses, so a slow tick does not accumulate error."""
        self.running = True
        start = time.monotonic()
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
                # claiming health. If the bus never comes back, settle and give up.
                self.faults += 1
                self.fault = f"{type(e).__name__}: {e}"
                log.exception("tick failed (%d in a row)", self.faults)
                with self.lock:
                    self.command.trip(self.command.default_pose)
                if self.faults >= FAULT_LIMIT:
                    log.error("%d ticks failed in a row; settling and stopping", self.faults)
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
        # The overlays are goals quackd asked for, and the safe pose is the whole body.
        self.release_grip()
        with self.lock:
            self.neck_goal = None
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
            time.sleep(CONTROL_DT)
        log.warning("did not settle within %.0fs; leaving it where it is", timeout_s)
        return False

    def shutdown(self) -> None:
        """Settle, then close under a hard deadline.

        `close()` is bound without releasing the GIL and retries torque-off forever on an
        unresponsive bus, so a stuck shutdown freezes every thread including this one. A
        torqued robot and a dead process beats a frozen process nobody can signal."""
        if self._shut:
            return
        self._shut = True
        self.running = False
        if self.camera is not None:
            self.camera.close()
        try:
            self.settle()
        except Exception:  # never let settling stop the close
            log.exception("settling failed; closing anyway")

        def bail() -> None:
            log.error("close() did not return in %.0fs; exiting hard", CLOSE_TIMEOUT_S)
            os._exit(1)

        timer = threading.Timer(CLOSE_TIMEOUT_S, bail)
        timer.daemon = True
        timer.start()
        try:
            self.sim.close()
        finally:
            timer.cancel()

    # -- what the socket asks it ----------------------------------------------------

    def state(self) -> dict[str, Any]:
        with self.lock:
            return {
                "t": round(time.monotonic(), 3),
                "policy": self.command.motion or self.command.mode,
                "posture": "fallen" if self.fallen else "standing",
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

    def touch(self) -> None:
        self.command.last_client = time.monotonic()


# ── the socket ──────────────────────────────────────────────────────────────────────────


class Handler(socketserver.StreamRequestHandler):
    daemon_ref: Daemon
    token: str | None
    capabilities: dict[str, bool]
    robot_name: str
    motors: int

    def handle(self) -> None:
        authed = self.token is None
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

    def _dispatch(
        self, method: str | None, params: dict[str, Any], authed: bool
    ) -> tuple[Any, bool]:
        d = self.daemon_ref
        if method == "bot.hello":
            # as bytes: compare_digest raises on a str that is not ASCII
            if self.token is not None and not hmac.compare_digest(
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
            }, True
        if not authed:
            raise _Refused(ERR_BAD_TOKEN, "say bot.hello with a token first")
        # Only a method that drives the robot feeds the deadman. Reading state, health or a
        # frame is a question, not driving: a client whose LLM stalled mid-verb, or which
        # polls while it thinks, would otherwise hold the deadman off for as long as it kept
        # asking. WALK is latched, so the robot would go on walking with nobody steering it,
        # which is the exact failure the deadman exists for.
        if method in COMMAND_METHODS:
            d.touch()
        if method == "bot.state":
            return d.state(), authed
        if method == "bot.health":
            reason = None
            if d.fault is not None:
                reason = f"the control loop faulted: {d.fault}"
            elif d.fallen:
                reason = "the robot has fallen"
            return {
                "ok": reason is None,
                "reason": reason,
                "loop_hz": round(d.loop_hz, 1),
            }, authed
        if method == "bot.stop":
            with d.lock:
                d.command.hold(d.safe.pose)
            return {"accepted": True}, authed
        if method == "bot.stand":
            if d.fallen:
                return {"accepted": False, "reason": "the robot has fallen"}, authed
            with d.lock:
                d.command.stand()
            return {"accepted": True}, authed
        if method == "bot.perform":
            name = str(params.get("motion", ""))
            frames = d.motion_library.get(name)
            if not frames:
                return {"accepted": False, "reason": f"no motion named {name!r}"}, authed
            if d.fallen:
                return {"accepted": False, "reason": "the robot has fallen"}, authed
            with d.lock:
                d.command.play(name, frames)
            return {"accepted": True}, authed
        if method == "bot.look":
            if not self.capabilities.get("neck"):
                return {"accepted": False, "reason": "this build has no neck"}, authed
            d.aim_neck(
                math.radians(_number(params, "yaw_deg")),
                math.radians(_number(params, "pitch_deg")),
            )
            return {"accepted": True}, authed
        if method == "bot.command":
            if not self.capabilities.get("walk"):
                return {"accepted": False, "reason": "no walk policy is staged"}, authed
            with d.lock:
                d.command.drive(
                    _number(params, "walk_x"),
                    _number(params, "walk_y"),
                    _number(params, "walk_turn"),
                )
            return {"accepted": True}, authed
        if method == "bot.grip":
            if not self.capabilities.get("gripper"):
                return {"accepted": False, "reason": "this build has no grippers"}, authed
            side = str(params.get("side", "right"))
            if side not in ("left", "right", "both"):
                raise _Refused(ERR_REFUSED, f"unknown side {side!r}")
            moved = d.set_grip(side, bool(params.get("close", True)))
            if not moved:
                return {
                    "accepted": False,
                    "reason": f"this robot has no gripper motor on the {side}",
                }, authed
            return {"accepted": True, "motors": moved}, authed
        if method == "bot.keepalive":
            # A notification, and the only thing a client has to send to say it is
            # still there. `d.touch()` above already did the work.
            return None, authed
        if method == "bot.frame":
            return {"jpeg": d.camera.latest() if d.camera is not None else None}, authed
        raise _Refused(ERR_UNKNOWN, f"unknown method {method!r}")

    def _reply(self, req_id: Any, result: Any) -> None:
        self.wfile.write(
            (
                json.dumps({"jsonrpc": JSONRPC_VERSION, "id": req_id, "result": result}) + "\n"
            ).encode()
        )

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
        self.quackd_calibrated = True


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
    quackd would rather publish a shorter list than a verb that refuses on a robot.
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

    quackd encodes the frame itself rather than calling upstream's `get_jpeg`, for two
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
        self.thread = threading.Thread(target=self._loop, daemon=True, name="quackd-camera")
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
        self.thread.join(timeout=2.0)
        # upstream's own close, which the device needs and which nothing else calls
        with contextlib.suppress(Exception):
            self.camera.close()


def build_walk_policy(ckpt: str, robot: Any, init_pos: Any, root: str) -> tuple[Any, Any]:
    """Load a local ONNX walk checkpoint, and read the envelope it was really trained on.

    Upstream's only loader reaches for a wandb artifact when the file is missing, so quackd
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
                "supply. quackd will not reach for a wandb artifact from a robot."
            )
    from toddlerbot.policies.walk import WalkPolicy  # late: onnxruntime, jax and wandb

    policy = WalkPolicy(ckpt, robot, init_pos, ckpt_dir)
    rows = np.asarray(policy.command_range, dtype=np.float32)
    if rows.ndim != 2 or len(rows) < 8:
        raise SystemExit(
            f"the checkpoint at {ckpt_dir} reports a command_range shaped {rows.shape}, "
            "and quackd expected at least eight rows with the walk velocities at five, "
            "six and seven. Refusing to guess which rows are the velocities."
        )
    # The trained range is asymmetric (forward further than backward) and quackd's limits
    # are symmetric, so the honest reading of each row is its tighter half.
    envelope = {
        "max_vx": float(min(abs(rows[5][0]), abs(rows[5][1]))),
        "max_vy": float(min(abs(rows[6][0]), abs(rows[6][1]))),
        "max_wz": float(min(abs(rows[7][0]), abs(rows[7][1]))),
    }
    log.info("walk checkpoint %s loaded; envelope %s", ckpt, envelope)
    return policy, envelope


# ── wiring it up ────────────────────────────────────────────────────────────────────────


def build_mujoco(robot_name: str, root: str) -> tuple[Any, Any]:
    """Construct upstream's Robot and its MuJoCo body, headless.

    This is the contract path: upstream's own physics, upstream's own model, and no
    hardware anywhere. It exists so the client, the protocol and the fifty hertz loop can
    be exercised against something that pushes back, which the fake body cannot do.

    Three things about it are not obvious and all three were read at the pin.

    `vis_type` is left at its default, which is the only value that builds neither a
    viewer nor a renderer, so nothing on this path needs a display or a GL context.
    Upstream never reads MUJOCO_GL, so setting it changes nothing here.

    `controller_type` stays at torque because the position controller's `step` takes
    three arguments and the sim calls it with four, so the other setting raises on the
    first tick.

    And the working directory has to be the checkout root, because the model path and
    every path `Robot` reads are relative and it offers no way to override them.
    """
    os.chdir(root)
    sys.path.insert(0, root)
    from toddlerbot.sim.mujoco_sim import MuJoCoSim  # late: needs mujoco, cv2 and jax
    from toddlerbot.sim.robot import Robot

    robot = Robot(robot_name)
    # A simulated body has no assembly for a zero to be offset by, so the calibration
    # the real path refuses to run without does not apply and must not block this one.
    robot.quackd_calibrated = True
    return robot, MuJoCoSim(robot)


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
    robot.quackd_calibrated = os.path.exists(motors_yml)
    if not robot.quackd_calibrated:
        raise SystemExit(
            f"{robot_name} has no zero calibration at {motors_yml}, so every commanded angle "
            "would be offset by however this robot was assembled. Run upstream's "
            "calibrate_zero first. quackd refuses to actuate without it."
        )

    box: dict[str, Any] = {}

    def construct() -> None:
        from toddlerbot.sim.real_world import (
            RealWorld,
        )  # late: this is the import that needs abandoning

        box["sim"] = RealWorld(robot)

    thread = threading.Thread(target=construct, daemon=True, name="quackd-construct")
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--robot", default="toddlerbot_2xc")
    parser.add_argument("--toddlerbot", default=os.environ.get("TODDLERBOT_ROOT", "."))
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--token", default=os.environ.get(TOKEN_ENV))
    parser.add_argument("--fake", action="store_true", help="Run with a simulated body.")
    parser.add_argument(
        "--sim",
        choices=("real", "mujoco"),
        default="real",
        help="Which body to drive. mujoco is upstream's own physics, headless, no robot.",
    )
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
        sys.stderr.write("quackd's ToddlerBot daemon needs numpy\n")
        return 2

    # Resolved once, before anything changes directory. build_real and build_mujoco both
    # chdir into the checkout, so a second os.path.abspath of a relative --toddlerbot would
    # resolve against the new directory and quietly name a different path.
    root = os.path.abspath(os.path.expanduser(args.toddlerbot))

    if args.fake:
        robot: Any = FakeRobot(args.robot)
        sim: Any = FakeSim(robot)
    else:
        build = build_mujoco if args.sim == "mujoco" else build_real
        try:
            robot, sim = build(args.robot, root)
        except ImportError as e:
            sys.stderr.write(
                "quackd's ToddlerBot daemon needs upstream installed on this robot:\n"
                "  git clone https://github.com/hshi74/toddlerbot\n"
                "  cd toddlerbot && pip install -e .\n"
                f"Point --toddlerbot at that checkout. ({e})\n"
            )
            return 2

    # a simulated body has no bulk read to drop, and upstream's MuJoCo model starts at
    # exactly the all-zeros pose the guard is built to refuse
    daemon = Daemon(robot, sim, fake=args.fake, bus=args.sim == "real" and not args.fake)

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
                # the handshake says so, and quackd never declares `observe`.
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
    # capability quackd reports is a verb quackd will offer, and a verb it offers is one it
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
    leaving = threading.Event()

    def on_signal(signum: int, _frame: Any) -> None:
        # The settle takes seconds and prints nothing while it runs, so an operator's reflex
        # is a second Ctrl-C. That used to re-enter here and raise straight through the slew,
        # leaving the robot wherever it had got to and skipping the close entirely.
        if leaving.is_set():
            log.warning("signal %s: already settling, and it will not be hurried", signum)
            return
        leaving.set()
        log.warning("signal %s: settling before exit", signum)
        daemon.shutdown()
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
    Handler.token = args.token
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
