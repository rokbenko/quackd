"""A MOSS rover in memory that is never kinder than the real one.

`MossMock` is the cockpit, quackd's bridge and the rover behind them, on a clock of its own that
moves only when something sleeps. Every rule it plays by is read upstream at the pin and comes
from `upstream_api`, so a task that passes here has met the same traps the rover sets:

- **No slow pivot.** A track below the 20 percent floor is raised to it, and tracks in opposite
  directions run at full power, whatever the twist asked (`upstream_api.PIVOT_PCT`). The bridge's
  shaping runs here as it does on the rover: an arc is clipped so the inner track never
  reverses, and a spin goes out as the maker's pulses.
- **The ramp.** Each track moves toward its target at the launcher's ramp, so a stop from full
  power takes half a second and a 0.1 s pulse never reaches full power.
- **The odometry under-reads a pivot**, by `odom_gain`, so a pose after a turn is not where the
  rover is.
- **Stalls.** A wall at `wall_x` stops the tracks, and after the stall guard's second the base
  says so until the next command.
- **The idle stop.** A moss-nav or moss-explore stack nobody keeps alive is stopped after
  `idle_limit_s`, which ends every dimOS run, so the arm loses its torque too.
- **A mission that acts on its own.** On moss-litter the mission goes for any litter it is
  given from the moment the stack starts, the soft stop does not end it, and an arm cancelled
  before the mission is stopped makes it start exploring again.

The address carries the knobs: `?stack=moss-litter&bridge=0&arm=0&mission=0&camera=0&stamps=0`
and the rest of the keyword arguments by the same names.
"""

from __future__ import annotations

import asyncio
import math
import secrets
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs, urlsplit

from PIL import Image, ImageDraw

from quackd.sim2d.render import BALL, FLOOR, HORIZON, SKY, focal_px
from quackd.transport.base import Ack, CameraFrame, DuckState, HeartbeatError, Intent
from quackd_moss import upstream_api as up
from quackd_moss.verbs import (
    BATTERY_FLOOR_V,
    FIRMWARE_LIMIT_S,
    MIN_STALE_S,
    MISSION_STOPPED,
    SKILL_ARM_CYCLE,
    SKILL_COLLECT,
    SKILL_EXPLORE,
    STOW_FROM_PHASE,
    TELEMETRY_LIMIT_S,
    arm_note,
    stack_refusal,
    stale_after_s,
)

TICK_S = 0.05
"""The mock's own integration step: half the bridge's 10 Hz, so a 0.1 s pulse is two steps."""

MPS_AT_LIMIT = float(up.MPS_AT_LIMIT.name)
MIN_PCT = int(up.MIN_PCT_FLOOR.name)
PIVOT_PCT = int(up.PIVOT_PCT.name)
MAX_PCT = int(up.FIRMWARE_MAX_PCT.name)
TRACK_M = float(up.TRACK_M.name)
RAMP_UP_MS, RAMP_DOWN_MS = (float(v) for v in up.LAUNCHER_RAMP_MS.name.strip('"').split())
TWIST_DEADMAN_S = float(up.FIRMWARE_DEADMAN_S.name)
VELOCITY_TIMEOUT_S = float(up.VELOCITY_TASK_TIMEOUT_S.name)
PULSE_S = float(up.PULSE_S.name)
REST_S = float(up.REST_S.name)
SERVO_W_MAX = float(up.SERVO_W_MAX.name)
STALL_S = float(up.STALL_S.name)
IDLE_STOP_DEFAULT_S = float(up.IDLE_STOP_DEFAULT_S.name)
IDLE_STOP_BLUEPRINTS = tuple(n.strip().strip('"') for n in up.IDLE_STOP_BLUEPRINTS.name.split(","))
CAMERA_PERIOD_S = 1.0 / float(up.CAMERA_FPS.name)
MISSION_SETTLE_S = float(up.STOP_SETTLE_S.name)
CYCLE_S = float(up.CYCLE_TIMED_S.name)
GOAL_AFTER_STOP_S = float(up.GOAL_AFTER_STOP_S.name)
SESSION_MISSES = 3
"""As quackd's bridge counts them (`cockpit.SESSION_MISSES`)."""
KEEPALIVE_S = 0.5
"""The period the real transport's keepalive thread beats at and declares
(`cockpit.HEARTBEAT_S`)."""
MAX_KEEPALIVE_S = 1.0
"""The longest period the bridge takes from a client (`cockpit.MAX_KEEPALIVE_S`)."""
EXPLORER_REPLAN_S = 2.0
"""quackd's own guess, for the mock, at how soon the explorer sends a goal of its own over one
sent beside it. Nobody has timed its next frontier on a rover."""
PICK_STARTUP_S = 2.0
"""quackd's own guess, for the mock, at how long the maker's script takes to import dimOS,
build its client and read the joints before it sends its one trajectory. Nobody has measured
it on a rover. Inside it a cancel finds nothing executing, and only ending the script keeps
the trajectory from going out after the cancel (`up.MISSION_STOP_LEAVES_PICK`)."""

MOCK_CAM_HEIGHT_M = 0.12
MOCK_BALL_R = 0.04
RENDER_FOV_DEG = 45.0
"""What the synthetic nose draws with, as the other mocks do. Not MOSS's lens: nobody has read
one, and the manifest declares no field of view unless `fov_deg` is given."""


def tracks_for(vx: float, wz: float, track_m: float = TRACK_M) -> tuple[int, int]:
    """The base adapter's own mapping: differential drive, the floor, then full power pivots."""
    half = track_m / 2.0
    scale = MAX_PCT / MPS_AT_LIMIT

    def pct(v: float) -> int:
        return max(-MAX_PCT, min(MAX_PCT, round(v * scale)))

    left, right = pct(vx - wz * half), pct(vx + wz * half)
    if (left, right) == (0, 0):
        return 0, 0
    if left == 0 or right == 0 or (left > 0) != (right > 0):
        left = left if left == 0 or abs(left) >= MIN_PCT else int(math.copysign(MIN_PCT, left))
        right = right if right == 0 or abs(right) >= MIN_PCT else int(math.copysign(MIN_PCT, right))
    else:
        inner, outer = min(abs(left), abs(right)), max(abs(left), abs(right))
        if inner < MIN_PCT:
            k = MIN_PCT / inner
            if outer * k > MAX_PCT + 0.5:
                sign = 1 if left > 0 else -1
                kept = round(MAX_PCT * inner / outer)
                kept = kept if kept >= MIN_PCT else 0
                if abs(left) >= abs(right):
                    left, right = sign * MAX_PCT, sign * kept
                else:
                    left, right = sign * kept, sign * MAX_PCT
            else:
                left, right = round(left * k), round(right * k)
    if left and right and (left > 0) != (right > 0):
        left = PIVOT_PCT if left > 0 else -PIVOT_PCT
        right = PIVOT_PCT if right > 0 else -PIVOT_PCT
    return left, right


def shape_twist(vx: float, wz: float, track_m: float = TRACK_M) -> tuple[float, float, bool]:
    """What quackd's bridge does to a twist before it publishes one: (vx, wz, clipped). An arc
    keeps its inner track from reversing, and a spin is left for the pulses."""
    if vx == 0.0:
        return 0.0, wz, False
    limit = 2.0 * abs(vx) / track_m
    if abs(wz) > limit:
        return vx, math.copysign(limit, wz), True
    return vx, wz, False


def pulse_rest_s(wz: float) -> float:
    """The bridge's rest after a spin pulse: the maker's own at the ceiling, longer below it."""
    return REST_S * SERVO_W_MAX / max(1e-9, min(abs(wz), SERVO_W_MAX))


@dataclass
class _Job:
    id: str
    state: str
    started: float
    lasts_s: float
    reason: str = ""
    exit: int | None = None
    held: bool | None = None
    owner: str = "quackd"
    startup_s: float = 0.0
    """How long a `starting` job's script takes to send its trajectory."""
    script_alive: bool = True

    def doc(self, now: float) -> dict[str, Any]:
        return {
            "id": self.id,
            "state": self.state,
            "reason": self.reason,
            "exit": self.exit,
            "held": self.held,
            "elapsed_s": round(now - self.started, 1),
            "owner": self.owner,
        }


@dataclass
class _Mission:
    litter: list[tuple[float, float]] = field(default_factory=list)
    state: str = "EXPLORING"
    since: float = 0.0
    running: bool = True
    picked: int = 0
    skipped: int = 0
    target: tuple[float, float] | None = None


class MossMock:
    name = "mock"
    mobility = "wheeled"

    @classmethod
    def from_address(cls, address: str | None) -> MossMock:
        """`moss:mock?stack=moss-nav&bridge=0`, read as keyword arguments."""
        if not address:
            return cls()
        query = parse_qs(urlsplit(address if "://" in address else f"mock://{address}").query)

        def flag(name: str, default: bool) -> bool:
            raw = query.get(name, [""])[0].strip().lower()
            return default if not raw else raw not in ("0", "false", "no", "off")

        def number(name: str) -> float | None:
            raw = query.get(name, [""])[0].strip()
            try:
                return float(raw) if raw else None
            except ValueError:
                return None

        litter = int(number("litter") or 0)
        return cls(
            stack=query.get("stack", [up.EXPLORE_STACK])[0],
            bridge=flag("bridge", True),
            arm=flag("arm", True),
            mission=flag("mission", True),
            camera=flag("camera", True),
            stamps=flag("stamps", True),
            wall_x=number("wall"),
            idle_limit_s=number("idle"),
            litter=[(1.0 + i, 0.0) for i in range(litter)],
        )

    def __init__(
        self,
        *,
        stack: str = up.EXPLORE_STACK,
        bridge: bool = True,
        arm: bool = True,
        mission: bool = True,
        camera: bool = True,
        stamps: bool = True,
        fov_deg: float | None = None,
        battery_v: float | None = 11.6,
        odom_gain: float = 0.9,
        wall_x: float | None = None,
        idle_limit_s: float | None = None,
        litter: list[tuple[float, float]] | None = None,
        ball_xy: tuple[float, float] | None = (1.5, 0.3),
        frame_size: int = 128,
        goal_tol_m: float = 0.1,
        goal_refused: bool = False,
        explore_done_after_s: float | None = None,
        arm_lag_at_s: float | None = None,
        arm_dies_at_s: float | None = None,
        bus: str = "lcm",
        fail_heartbeat_after: int | None = None,
        stop_fails: str | None = None,
    ) -> None:
        self.stack = stack
        self.bus = bus
        self.bridge_up = bridge
        self.arm_up = bridge and arm
        self.arm_run = arm
        """The arm's own dimOS run is there. tools/moss-fly.sh refuses to start beside any run,
        so the arm's starts after the base stack, and then it is the newest run, which is all
        the cockpit reads."""
        self.mission_up = bridge and mission and stack == up.LITTER_STACK
        self.camera_available = camera
        self.stamps = stamps
        self.camera_keys: list[str] = (
            [up.NOSE, up.WRIST] if (camera and bridge) else ([up.NOSE] if camera else [])
        )
        self.camera_error: str | None = None if camera else "the mock was built with no camera"
        self.fov_deg = fov_deg
        self.manifest_extras: dict[str, Any] = {
            "cockpit": "mock",
            "bridge": {"url": "mock", "version": "mock"} if bridge else None,
            "track_m": TRACK_M,
            "stop_ramp_s": RAMP_DOWN_MS * MAX_PCT / 1000.0,
        }
        self.battery_v = battery_v
        self.telemetry_age_s: float | None = 0.25
        """How old the cockpit's copy of the base telemetry is: a test ages it."""
        self.firmware_age_s: float | None = 0.1
        self.pose_age_s: float | None = 0.05
        self.odom_gain = odom_gain
        self.wall_x = wall_x
        self.idle_limit_s = IDLE_STOP_DEFAULT_S if idle_limit_s is None else idle_limit_s
        self.ball_xy = ball_xy
        self._frame_size = frame_size
        self.goal_tol_m = goal_tol_m
        self.goal_refused = goal_refused
        self.explore_done_after_s = explore_done_after_s
        self.arm_lag_at_s = arm_lag_at_s
        self.arm_dies_at_s = arm_dies_at_s
        """The maker's script dies this far into a cycle of quackd's, any way but its own (an
        exception, a signal), and its one trajectory plays on until something cancels it."""
        self.arm_moving = False
        """A trajectory playing with no script left to watch it."""
        self._fail_after = fail_heartbeat_after
        self.stop_fails = stop_fails
        self.connected = False
        self.stack_running = True
        self.run_id = "mock-run-1"
        # the rover, true and as its odometry reports it
        self.x = self.y = self.yaw = 0.0
        self.ox = self.oy = self.oyaw = 0.0
        self.out = [0.0, 0.0]
        """Each track's output percent, which ramps toward its target."""
        self._t = 0.0
        # the bridge's twist pump
        self._twist: tuple[float, float] | None = None
        self._twist_at = -1e9
        self._pulse_t0 = 0.0
        self._zero_sent = True
        self.clipped = 0
        self.pulses = 0
        self._pulse_on = False
        # what reaches the base, and when it was published
        self._cmd: tuple[float, float] = (0.0, 0.0)
        self._cmd_at = -1e9
        self.stalled = False
        self._blocked_s = 0.0
        self.stall_count = 0
        # the planner, the explorer and the cockpit
        self.goal: tuple[float, float] | None = None
        self.goal_reached: bool | None = None
        self._goal_at = -1e9
        self._goal_done: tuple[float, float] | None = None
        self._pending_goal: tuple[tuple[float, float], float] | None = None
        """A goal the cockpit holds back while it stops the explorer (`up.GOAL_AFTER_STOP_S`)."""
        self._beside: float | None = None
        """When a goal went out beside an explorer the cockpit could not see, so never stopped."""
        self.exploring = False
        self.explore_done = False
        self._explore_at = 0.0
        self._activity = 0.0
        self.idle_stopped = False
        self.last_keepalive: float | None = None
        self.cockpit_keepalives = 0
        self.soft_stops = 0
        # the arm, its jobs and its torque
        self.arm_folded = True
        self.arm_torque = self.arm_up
        self.job: _Job | None = None
        self._jobs = 0
        self.arm_job_id: str | None = None
        # the mission
        self.mission = _Mission(litter=list(litter or []))
        self.mission_stopped = False
        # quackd's session with the bridge
        self.started: str | None = None
        self.litter_started = False
        """`collect_litter` started in this session, so close() stops the mission."""
        self.stop_sent = False
        """A stop ran in this session, so close() says what it did to the mission."""
        # quackd's session with the bridge, kept by the transport's own keepalive thread
        self.session_id = secrets.token_hex(8)
        self._session_at: float | None = None
        """When the bridge took this session's last keepalive, None while it has none."""
        self._session_s = min(MAX_KEEPALIVE_S, KEEPALIVE_S)
        """The declared, configured period, capped as the bridge caps it."""
        self._beat_started: str | None = None
        """What that last keepalive said this session started: what its halt halts."""
        self.link_down = False
        """Set by `drop_link`: nothing of quackd's reaches the rover any more."""
        self.session_halts = 0
        self.stop_log: list[str] = []
        self.stop_error: str | None = None
        self.close_note: str | None = None
        self.heartbeats = 0
        self.intents: list[Intent] = []
        self.post_sleep: Callable[[], None] | None = None
        self._frame: Image.Image | None = None
        self._frame_at: float | None = None

    # ── the clock ───────────────────────────────────────────────────────────────────

    def now(self) -> float:
        return self._t

    async def sleep(self, seconds: float) -> None:
        remaining = seconds
        while remaining > 1e-9:
            dt = min(TICK_S, remaining)
            self._t += dt
            self._step(dt)
            remaining -= dt
        await asyncio.sleep(0)
        if self.post_sleep is not None:
            self.post_sleep()

    # ── the rover ───────────────────────────────────────────────────────────────────

    def _pump(self) -> None:
        """quackd's bridge: the twist while it is fresh, then one zero, then silence."""
        if self._twist is not None and self._t - self._twist_at <= TWIST_DEADMAN_S:
            vx, wz = self._twist
            if vx == 0.0 and wz != 0.0:
                period = PULSE_S + pulse_rest_s(wz)
                on = (self._t - self._pulse_t0) % period < PULSE_S
                if on and not self._pulse_on:
                    self.pulses += 1
                self._pulse_on = on
                self._publish((0.0, wz) if on else (0.0, 0.0))
            else:
                self._publish((vx, wz))
            return
        if not self._zero_sent:
            self._zero_sent = True
            self._twist = None
            self._publish((0.0, 0.0))

    def _publish(self, twist: tuple[float, float]) -> None:
        self._cmd, self._cmd_at = twist, self._t
        if self.stack in (up.EXPLORE_STACK, up.LITTER_STACK):
            # MovementManager: any message on the pad's channel cancels navigation
            self.goal, self.exploring = None, False

    @property
    def cockpit_view(self) -> str:
        """The blueprint the cockpit reads: the newest dimOS run, moss-arm whenever it runs."""
        return up.ARM_STACK if self.arm_run else self.stack

    def _planner(self) -> tuple[float, float] | None:
        """The stack's own driving: the goal, the explorer, or the mission's servo."""
        beside = self._beside
        if beside is not None and self.exploring and self._t - beside >= EXPLORER_REPLAN_S:
            # the explorer, never stopped, sends its next frontier over the goal
            self.goal, self.goal_reached = (self.x + 1.5, self.y + 1.5), None
            self._goal_at, self._beside = self._t, None
        if self._pending_goal is not None and self._t >= self._pending_goal[1]:
            self.goal, self._goal_at = self._pending_goal[0], self._t
            self._pending_goal = None
        if self.goal is not None and self.stack != up.NAV_STACK:
            gx, gy = self.goal
            if self.goal_refused and self._t - self._goal_at > 1.0:
                self.goal, self.goal_reached = None, False
                return None
            if math.hypot(gx - self.x, gy - self.y) <= self.goal_tol_m:
                self.goal_reached = True
                self._goal_done = (gx, gy)
                self.goal = None
                return None
            err = math.atan2(gy - self.y, gx - self.x) - self.yaw
            err = math.atan2(math.sin(err), math.cos(err))
            if abs(err) > 0.3:
                return 0.0, math.copysign(SERVO_W_MAX, err)
            return MPS_AT_LIMIT, 0.0
        if self.exploring:
            if (
                self.explore_done_after_s is not None
                and self._t - self._explore_at >= self.explore_done_after_s
            ):
                self.exploring, self.explore_done = False, True
                return None
            return MPS_AT_LIMIT / 2.0, SERVO_W_MAX / 4.0
        return None

    def _base(self, dt: float) -> None:
        planned = self._planner()
        cmd = self._cmd if self._t - self._cmd_at <= VELOCITY_TIMEOUT_S else (0.0, 0.0)
        if cmd == (0.0, 0.0) and planned is not None:
            cmd = planned
        if self.stalled:
            cmd = (0.0, 0.0)
        target = tracks_for(*cmd)
        for i in (0, 1):
            want, have = float(target[i]), self.out[i]
            rising = (
                want != 0.0 and (have == 0.0 or (want > 0) == (have > 0)) and abs(want) > abs(have)
            )
            rate = 1000.0 / (RAMP_UP_MS if rising else RAMP_DOWN_MS)
            step = rate * dt
            goal = want if rising or (want == 0.0 or (want > 0) == (have > 0)) else 0.0
            if abs(goal - have) <= step:
                self.out[i] = goal
            else:
                self.out[i] = have + math.copysign(step, goal - have)
        vl, vr = (self.out[0] / 100.0) * MPS_AT_LIMIT, (self.out[1] / 100.0) * MPS_AT_LIMIT
        vx, wz = (vl + vr) / 2.0, (vr - vl) / TRACK_M
        nx = self.x + vx * math.cos(self.yaw) * dt
        commanded = target != (0, 0)
        if self.wall_x is not None and vx > 0 and nx >= self.wall_x:
            # the tracks push against the wall and the encoders say so
            vx, nx = 0.0, self.x
            if commanded:
                self._blocked_s += dt
                if self._blocked_s >= STALL_S and not self.stalled:
                    self.stalled = True
                    self.stall_count += 1
                    self.out = [0.0, 0.0]
            wz = 0.0
        else:
            self._blocked_s = 0.0
        self.x, self.y = nx, self.y + vx * math.sin(self.yaw) * dt
        self.yaw = math.atan2(math.sin(self.yaw + wz * dt), math.cos(self.yaw + wz * dt))
        pivoting = self.out[0] * self.out[1] < 0
        gain = self.odom_gain if pivoting else 1.0
        self.oyaw = math.atan2(
            math.sin(self.oyaw + wz * gain * dt), math.cos(self.oyaw + wz * gain * dt)
        )
        self.ox += vx * math.cos(self.oyaw) * dt
        self.oy += vx * math.sin(self.oyaw) * dt
        if commanded or abs(vx) > 0 or abs(wz) > 0:
            self._activity = self._t

    def _arm_step(self) -> None:
        job = self.job
        if job is None or job.state not in ("starting", "running"):
            return
        if job.state == "starting":
            # the script is still importing and reading the joints: at the end of that, and
            # only if nothing ended it, its one trajectory goes out
            if job.script_alive and self._t - job.started >= job.startup_s:
                job.state, job.started = "running", self._t
                self.arm_folded = False
            return
        elapsed = self._t - job.started
        dies = self.arm_dies_at_s
        if job.owner == "quackd" and dies is not None and elapsed >= dies:
            job.state, job.exit, job.reason = "failed", -9, "the script exited -9"
            self.arm_moving, self.arm_folded = True, False
            self._after_a_death(job)
            return
        if self.arm_lag_at_s is not None and elapsed >= self.arm_lag_at_s:
            job.state, job.exit, job.held = "lag", 4, True
            job.reason = "ARM LAG: a joint fell behind, the trajectory was cancelled"
            self.arm_folded = False
            self._job_ended(job, ok=False)
            return
        if elapsed >= job.lasts_s:
            job.state, job.exit, job.reason = "done", 0, "done"
            self.arm_folded = True
            self._job_ended(job, ok=True)

    def _after_a_death(self, job: _Job) -> None:
        """The bridge reads the script's exit, cancels the trajectory it left playing and reads
        the arm, before the job ends and a twist or a new cycle may start."""
        self.stop_log.append("arm.cancel")
        self.arm_moving = False
        job.held = True

    def _job_ended(self, job: _Job, *, ok: bool) -> None:
        if job.owner != "mission":
            return
        m = self.mission
        if ok:
            m.picked += 1
        else:
            m.skipped += 1
        if m.running:
            # the mission's own next tick: explore again, whether the pick worked or not
            m.state, m.since, m.target = "RESUMING", self._t, None
            self._explore(start=True)

    def _mission_step(self) -> None:
        m = self.mission
        if self.stack != up.LITTER_STACK or not m.running or not self.stack_running:
            return
        if m.state in ("EXPLORING", "RESUMING"):
            if m.state == "RESUMING" and self._t - m.since < 1.0:
                return
            if m.litter:
                m.target = m.litter.pop(0)
                m.state, m.since = "STOPPING", self._t
                self.exploring = False
            else:
                m.state = "EXPLORING"
        elif m.state == "STOPPING" and self._t - m.since >= MISSION_SETTLE_S:
            m.state, m.since = "APPROACHING", self._t
            self.goal, self.goal_reached, self._goal_at = m.target, None, self._t
        elif m.state == "APPROACHING":
            # the soft stop cancels the goal, and the mission takes no notice: with no verdict
            # it goes on from wherever the rover stopped, as the real one does after its timeout
            if self.goal_reached or self.goal is None:
                m.state, m.since = "PICKING", self._t
                self._jobs += 1
                self.job = _Job(
                    f"mission-{self._jobs}",
                    "starting",
                    self._t,
                    CYCLE_S,
                    owner="mission",
                    startup_s=PICK_STARTUP_S,
                )

    def _keepalive_step(self) -> None:
        """The transport's keepalive thread: while connected and the link is up, a keepalive
        every period, carrying what the session has started, and the cockpit's idle keepalive
        when it is due, whatever the event loop is doing."""
        if not self.connected or self.link_down:
            return
        if self.bridge_up and (
            self._session_at is None or self._t - self._session_at >= self._session_s
        ):
            self._beat()
        if self.idle_limit_s > 0:
            due = self.idle_limit_s / 3.0
            if self.last_keepalive is None or self._t - self.last_keepalive >= due:
                self.last_keepalive = self._t
                self.cockpit_keepalives += 1
                self._activity = self._t

    def _beat(self) -> None:
        self._session_at, self._beat_started = self._t, self.started

    def _session_step(self) -> None:
        if self._session_at is None:
            return
        if self._t - self._session_at > SESSION_MISSES * self._session_s:
            self._session_at = None
            self.session_halts += 1
            self._session_halt()

    def _idle_step(self) -> None:
        if (
            self.idle_limit_s <= 0
            or self.idle_stopped
            or self.stack not in IDLE_STOP_BLUEPRINTS
            or not self.stack_running
        ):
            return
        if self._t - self._activity >= self.idle_limit_s:
            # tools/moss-fly.sh stop: every dimOS run ends, the arm's with it, torque and all
            self.idle_stopped = True
            self.stack_running = False
            self.arm_torque = False
            self.exploring, self.goal = False, None
            self.out = [0.0, 0.0]

    def _step(self, dt: float) -> None:
        if not self.stack_running:
            return
        self._pump()
        self._mission_step()
        self._base(dt)
        self._arm_step()
        self._keepalive_step()
        self._session_step()
        self._idle_step()

    # ── the surfaces ────────────────────────────────────────────────────────────────

    def _explore(self, *, start: bool) -> None:
        self.exploring = start and self.stack in (up.EXPLORE_STACK, up.LITTER_STACK)
        self._beside = None
        if start:
            self.explore_done, self._explore_at = False, self._t

    def _cockpit_stop(self) -> None:
        """The cockpit's soft stop. It ends the explorer and any goal, and the mission takes no
        notice of it."""
        self.stop_log.append("cockpit.stop")
        self.soft_stops += 1
        self._beside = None
        self._activity = self._t
        self.exploring, self.goal, self._pending_goal = False, None, None
        self._publish((0.0, 0.0))

    def _arm_cancel(self) -> bool | None:
        """The bridge's cancel. For a cycle of quackd's it stops the script before it cancels,
        as `ArmJobs.cancel` does, so the cycle ends either way. For the mission's own it is
        cancel_trajectory alone, which finds nothing to cancel while that script is still
        starting: unless the script was ended, its trajectory goes out afterwards."""
        job = self.job
        running = job is not None and job.state in ("starting", "running")
        if not running and not self.arm_up:
            return None
        # sent whenever the arm's coordinator is there, as the bridge sends it
        self.stop_log.append("arm.cancel")
        if job is None or not running or (job.owner == "mission" and job.state == "starting"):
            return None
        job.state, job.held, job.exit = "cancelled", True, None
        job.reason = "cancelled: the coordinator holds the last commanded position"
        self.arm_folded = False
        self._job_ended(job, ok=False)
        return True

    def _end_mission_script(self) -> None:
        """The bridge ends the mission's own pick script, which the mission's stop leaves
        running. One still starting never sends its trajectory, and one already playing leaves
        the coordinator playing it until the cancel that follows."""
        job = self.job
        if job is None or job.owner != "mission" or not job.script_alive:
            return
        if job.state not in ("starting", "running"):
            return
        self.stop_log.append("pick.script")
        job.script_alive = False
        if job.state == "starting":
            job.state, job.exit = "failed", -15
            job.reason = "the script was ended before it sent its trajectory"
            self._job_ended(job, ok=False)

    def _bridge_stop(self) -> list[str]:
        """The bridge's `/stop`: on moss-litter the mission's own stop first, then the end of
        the mission's own pick script, the arm's cancel and a zero twist. Elsewhere the zero
        first, which also cancels any goal the planner has, and then the arm's cancel, so the
        tracks stop whatever the arm's coordinator takes to answer."""
        litter = self.stack == up.LITTER_STACK
        if litter and self.mission.running:
            self.stop_log.append("mission.stop")
            self.mission.running = False
            self.mission_stopped = True
        if litter:
            self._end_mission_script()
            self._arm_cancel()
            self._zero()
        else:
            self._zero()
            self._arm_cancel()
        return []

    def _zero(self) -> None:
        """The bridge's zero twist, and its pump left silent."""
        self.stop_log.append("twist.zero")
        self._twist, self._zero_sent = None, True
        self._publish((0.0, 0.0))

    def _session_halt(self) -> None:
        """Three missed keepalives: halt what the last one the bridge took said this session
        started, and nothing it did not. On moss-litter the whole stop only for a session that
        started the mission: one that only watched the owner's mission leaves it running."""
        self.stop_log.append("session.halt")
        started = self._beat_started
        if self.stack == up.LITTER_STACK:
            if started == "litter":
                self._bridge_stop()
            return
        job = self.job
        if job is not None and job.owner == "quackd":
            self._arm_cancel()
        if started in ("goal", "explore"):
            self.stop_log.append("halt.started")
            self.exploring, self.goal, self._pending_goal = False, None, None
            self._publish((0.0, 0.0))

    def _announce(self, started: str) -> str | None:
        """Tell the bridge first, as the real transport does: a keepalive that carries what is
        about to start, so the deadman halts it even if quackd dies the instant after."""
        if self.link_down:
            return (
                f"the bridge did not take this session's word that it is starting {started}, "
                "so nothing was started: the link is down"
            )
        self.started = started
        if self.bridge_up:
            self._beat()
        return None

    def drop_link(self) -> None:
        """quackd's process died, or its link to the rover dropped: no keepalive of this
        session reaches the bridge any more, and nothing quackd sends arrives."""
        self.link_down = True

    def _halt_own(self) -> None:
        """close()'s halt when no `collect_litter` ran, as the real transport sends it: this
        session's own arm cycle by its id, a twist of its own the bridge still repeats, and a
        goal or an exploration it started. A session that started none of them sends nothing."""
        job = self.job
        if job is not None and job.owner == "quackd" and job.id == self.arm_job_id:
            self._arm_cancel()
        fresh = self._twist is not None and self._t - self._twist_at <= TWIST_DEADMAN_S
        if fresh and self._twist != (0.0, 0.0):
            self.stop_log.append("twist.zero")
            self._twist, self._zero_sent = None, True
            self._publish((0.0, 0.0))
        if self.started in ("goal", "explore"):
            self._cockpit_stop()
            self.started = None

    # ── protocol ────────────────────────────────────────────────────────────────────

    async def connect(self) -> None:
        refusal = stack_refusal(
            running=self.stack_running,
            # the bridge reads the base stack apart from the arm's run, the cockpit the newest
            blueprint=self.stack if self.bridge_up else self.cockpit_view,
            bus=self.bus,
            bridge=self.bridge_up,
            mission=self.mission_up,
            mission_error=None if self.mission_up else "the mock was built with no mission",
            bridge_error=None if self.bridge_up else "the mock was built with no bridge",
        )
        if refusal is not None:
            from quackd.transport.base import TransportError

            raise TransportError(f"moss: {refusal}")
        self.connected = True
        self._activity = self._t
        if self.bridge_up:
            self._beat()  # the session is open before anything can be started in it

    async def close(self) -> None:
        """What this session started, halted, and nothing else, as the real transport does it:
        the whole stop after `collect_litter`, and otherwise `_halt_own`."""
        if self.connected:
            if self.litter_started:
                await self.stop()
            else:
                self._halt_own()
            notes = []
            moved_arm = self.arm_job_id is not None or self.litter_started
            if moved_arm and self.arm_up and self.arm_torque and not self.arm_folded:
                said = arm_note(None if self.job is None else self.job.held)
                notes.append(said[0].upper() + said[1:] + ".")
            if self.stack == up.LITTER_STACK and self.stop_sent and self.mission_stopped:
                notes.append(
                    "The litter mission is stopped, and runs again only after the stack restarts "
                    "on the rover."
                )
            elif self.stack == up.LITTER_STACK and self.stop_sent:
                notes.append(
                    "The litter mission's stop was not confirmed, so it may still be running: "
                    "stop the stack on the rover."
                )
            self.close_note = " ".join(notes) or None
            self._session_at = None
        self.connected = False

    def _render(self) -> Image.Image:
        size = self._frame_size
        img = Image.new("RGB", (size, size), SKY)
        draw = ImageDraw.Draw(img)
        horizon = int(size * HORIZON)
        draw.rectangle([0, horizon, size, size], fill=FLOOR)
        if self.ball_xy is None:
            return img
        dx, dy = self.ball_xy[0] - self.x, self.ball_xy[1] - self.y
        dist = math.hypot(dx, dy)
        bearing = (math.degrees(math.atan2(dy, dx) - self.yaw) + 180.0) % 360.0 - 180.0
        if abs(bearing) > RENDER_FOV_DEG or dist < 0.05:
            return img
        f = focal_px(size)
        cx = size / 2 - math.tan(math.radians(bearing)) * f
        ground_y = horizon + f * MOCK_CAM_HEIGHT_M / dist
        r = f * MOCK_BALL_R / dist
        draw.ellipse([cx - r, ground_y - 2 * r, cx + r, ground_y], fill=BALL)
        return img

    async def get_frame(self) -> Image.Image | None:
        """The nose at the stack's own five frames a second, drawn at each frame's time."""
        if not self.camera_available or not self.stack_running:
            return None
        taken = math.floor(self._t / CAMERA_PERIOD_S) * CAMERA_PERIOD_S
        if self._frame is None or self._frame_at != taken:
            self._frame, self._frame_at = self._render(), taken
        return self._frame

    async def get_frames(self) -> list[CameraFrame]:
        nose = await self.get_frame()
        frames = [] if nose is None else [CameraFrame(up.NOSE, nose, primary=True)]
        if nose is not None and up.WRIST in self.camera_keys:
            size = self._frame_size
            frames.append(CameraFrame(up.WRIST, Image.new("RGB", (size, size), FLOOR)))
        return frames

    def frame_taken_at(self) -> float | None:
        if not (self.bridge_up and self.stamps) or self._frame_at is None:
            return None
        if self._t - self._frame_at > stale_after_s(CAMERA_PERIOD_S):
            return None
        return self._frame_at

    @property
    def frame_period_s(self) -> float | None:
        return CAMERA_PERIOD_S if self.bridge_up else None

    async def get_state(self) -> DuckState:
        if not self.stack_running:
            extras: dict[str, Any] = {
                "stack": self.stack,
                "bridge_up": self.bridge_up,
                "telemetry_age_s": None,
                "telemetry_limit_s": TELEMETRY_LIMIT_S,
                "firmware_limit_s": FIRMWARE_LIMIT_S,
                "battery_floor_v": BATTERY_FLOOR_V,
                "stack_running": False,
            }
            return DuckState(t=self._t, extras=extras)
        job = self.job.doc(self._t) if self.job is not None else None
        arm = (
            {
                "answering": self.arm_up and self.arm_torque,
                "folded": self.arm_folded,
                "job": job,
                "torque": self.arm_torque,
            }
            if self.bridge_up
            else None
        )
        mission = None
        if self.stack == up.LITTER_STACK and self.bridge_up:
            m = self.mission
            mission = {
                "answering": m.running,
                "stopped": self.mission_stopped,
                "status": {
                    "state": m.state,
                    "target": None if m.target is None else {"x": m.target[0], "y": m.target[1]},
                    "picked": m.picked,
                    "skipped": m.skipped,
                }
                if m.running
                else None,
            }
        shown = self._nav_goal()
        goal = None if shown is None else {"x": round(shown[0], 3), "y": round(shown[1], 3)}
        # the cockpit's own rule: only an explore stack explores, and it reads the newest run
        cockpit_exploring = self.exploring and "explore" in self.cockpit_view
        extras = {
            "stack": self.stack,
            "bridge_up": self.bridge_up,
            "telemetry_age_s": self.telemetry_age_s,
            "firmware_age_s": self.firmware_age_s,
            "telemetry_limit_s": TELEMETRY_LIMIT_S,
            "firmware_limit_s": FIRMWARE_LIMIT_S,
            "pose_age_s": self.pose_age_s,
            "pose_limit_s": MIN_STALE_S,
            "battery_v": self.battery_v,
            "battery_floor_v": BATTERY_FLOOR_V,
            "stalled": self.stalled,
            "output_pct": [round(v) for v in self.out],
            "nav": {
                "exploring": self.exploring if self.bridge_up else cockpit_exploring,
                "explore_done": self.explore_done,
                "goal": goal,
                "goal_reached": self.goal_reached,
            },
            "idle": {"enabled": self.idle_limit_s > 0, "limit_s": self.idle_limit_s},
            "arm": arm,
            "mission": mission,
            "started": self.started,
            "clipped": self.clipped,
        }
        if self.bridge_up:
            # the nav above is the bridge's reading of the base stack, and this the cockpit's
            extras["cockpit_nav"] = {**extras["nav"], "exploring": cockpit_exploring}
        percent = None
        if self.battery_v is not None:
            empty, full = (float(v) for v in up.BATTERY_GAUGE.name.replace(" V", "").split(" to "))
            percent = round(100.0 * min(1.0, max(0.0, (self.battery_v - empty) / (full - empty))))
        return DuckState(
            t=self._t,
            x=round(self.ox, 3),
            y=round(self.oy, 3),
            theta=round(self.oyaw, 4),
            battery_percent=percent,
            extras=extras,
        )

    def _nav_goal(self) -> tuple[float, float] | None:
        if self.goal is not None:
            return self.goal
        return getattr(self, "_goal_done", None)

    async def send_intent(self, intent: Intent) -> Ack:
        self.intents.append(intent)
        if self.link_down:
            return Ack(accepted=False, reason="moss: the link to the rover is down")
        if not self.connected or not self.stack_running:
            return Ack(accepted=False, reason="moss: the stack is not running")
        p = intent.params
        self._activity = self._t
        match intent.kind:
            case "move":
                if not self.bridge_up:
                    return Ack(
                        accepted=False,
                        reason="a twist goes through quackd's bridge, and it is down",
                    )
                if float(p.get("vy", 0.0)) != 0.0:
                    return Ack(
                        accepted=False, reason="MOSS runs on tracks and cannot slide sideways"
                    )
                if self.stack == up.LITTER_STACK:
                    return Ack(
                        accepted=False, reason=f"on {up.LITTER_STACK} the mission owns the base"
                    )
                if self.job is not None and self.job.state in ("starting", "running"):
                    return Ack(accepted=False, reason="the arm is playing a cycle")
                if self.arm_moving:
                    return Ack(accepted=False, reason="the arm is still moving")
                vx, wz, clipped = shape_twist(float(p.get("vx", 0.0)), float(p.get("wz", 0.0)))
                if clipped:
                    self.clipped += 1
                if self._twist is None or (self._twist[0] == 0.0) != (vx == 0.0):
                    self._pulse_t0 = self._t
                if (vx, wz) != self._twist:
                    self.stalled = False
                self._twist, self._twist_at, self._zero_sent = (vx, wz), self._t, False
                return Ack()
            case "stop":
                await self.stop()
                return Ack(accepted=self.stop_error is None, reason=self.stop_error)
            case "pose":
                if self.stack != up.EXPLORE_STACK:
                    return Ack(accepted=False, reason=f"{self.stack} has no planner")
                if (why := self._announce("goal")) is not None:
                    return Ack(accepted=False, reason=why)
                self.stalled = False
                wanted = (float(p["x"]), float(p["y"]))
                self.goal_reached, self._goal_done = None, None
                if self.exploring and "explore" in self.cockpit_view:
                    # the cockpit stops the explorer and sends the goal after its deferral
                    self.exploring, self.goal = False, None
                    self._pending_goal = (wanted, self._t + GOAL_AFTER_STOP_S)
                else:
                    # an explorer the cockpit cannot see is not stopped, and goes on
                    self._beside = self._t if self.exploring else None
                    self.goal, self._goal_at, self._pending_goal = wanted, self._t, None
                return Ack()
            case "do":
                return self._skill(p)
            case "look":
                return Ack(
                    accepted=False,
                    reason="MOSS has no head: its cameras turn with the body or the arm",
                )
            case "enable":
                if not p.get("on", True):
                    return Ack(accepted=False, reason="quackd never limps a robot")
                return Ack()
            case _:
                return Ack(accepted=False, reason=f"MOSS cannot {intent.kind}")

    def _skill(self, p: dict[str, Any]) -> Ack:
        skill = p.get("skill")
        if skill in (SKILL_EXPLORE, SKILL_COLLECT):
            start = p.get("action", "start") == "start"
            if skill == SKILL_COLLECT and (self.mission_stopped or not self.mission.running):
                return Ack(accepted=False, reason=MISSION_STOPPED)
            if start:
                why = self._announce("litter" if skill == SKILL_COLLECT else "explore")
                if why is not None:
                    return Ack(accepted=False, reason=why)
            if skill == SKILL_COLLECT and start:
                self.litter_started = True
            self._explore(start=start)
            if not start and self.started in ("explore", "litter"):
                self.started = None
            return Ack()
        if skill == SKILL_ARM_CYCLE:
            if not self.arm_up or not self.arm_torque:
                return Ack(
                    accepted=False, reason="the arm's coordinator is not answering the bridge"
                )
            if self.stack == up.LITTER_STACK:
                return Ack(accepted=False, reason=f"on {up.LITTER_STACK} the mission owns the arm")
            if self.job is not None and self.job.state in ("starting", "running"):
                return Ack(accepted=False, reason="another arm job is running")
            if self._twist is not None and self._t - self._twist_at <= TWIST_DEADMAN_S:
                return Ack(accepted=False, reason="a twist is running: stop first")
            self._jobs += 1
            stow = p.get("from_phase") == STOW_FROM_PHASE
            self.job = _Job(f"job-{self._jobs}", "running", self._t, CYCLE_S)
            self.arm_job_id = self.job.id
            self.arm_folded = False
            if stow:
                self.job.reason = "stowing"
            return Ack()
        return Ack(accepted=False, reason=f"MOSS has no skill called {skill!r}")

    async def stop(self) -> None:
        """The bridge's stop and the cockpit's soft stop. The real transport sends both at once
        and either can land first. The mock runs the bridge's first, which is one of the
        orders the rover can see, and leaves the same rover behind as the other."""
        errors: list[str] = []
        self.stop_sent = True
        if self.link_down:
            self.stop_error = (
                "the bridge's stop: the link is down and the cockpit's soft stop: the link is down"
            )
            return
        if self.bridge_up:
            if self.stop_fails == "bridge":
                errors.append("the bridge's stop: the mock was told to fail it")
            else:
                errors += self._bridge_stop()
        if self.stop_fails == "cockpit":
            errors.append("the cockpit's soft stop: the mock was told to fail it")
        else:
            self._cockpit_stop()
        if not errors:
            # a stop that failed keeps what the session started, as the real transport does
            self.started = None
        self.stop_error = " and ".join(errors) if errors else None

    async def heartbeat(self) -> None:
        self.heartbeats += 1
        if self._fail_after is not None and self.heartbeats > self._fail_after:
            raise HeartbeatError("mock moss heartbeat failure (scripted)")
        if self.link_down:
            raise HeartbeatError("moss: the mock's link to the rover is down")
        if not self.stack_running:
            raise HeartbeatError(
                f"moss: the stack quackd connected to ({self.stack}) is not running any more"
            )

    async def subscribe(self, topic: str) -> AsyncIterator[dict[str, Any]]:  # type: ignore[override]
        while self.connected:
            await self.sleep(0.5)
            yield {"topic": topic, **(await self.get_state()).model_dump()}

    # ── test helpers ────────────────────────────────────────────────────────────────

    def intents_of(self, kind: str) -> list[Intent]:
        return [i for i in self.intents if i.kind == kind]

    def true_pose(self) -> tuple[float, float, float]:
        return self.x, self.y, self.yaw
