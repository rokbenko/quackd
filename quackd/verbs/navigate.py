"""The walk to a spot on the robot's own reading of where it is: what `go_to_place` does.

A place is a pose in the frame it was marked in, and the walk there is steered by the robot's
reading of where it is in the same frame (`quackd.space.source`), never by the camera, which
`go_to` steers by. With a map the source keeps (`has_grid`) and a body whose size is known
(`footprint_m`), it plans a walk round what the map says is solid, crossing floor nobody has seen
only where nothing seen goes through. With neither, it walks a straight line, as far as one `move`
could walk blind and no further.

Every number comes from the body or its source, none from a trace:

- the speed is the share of the forward clamp a `move` walks at by default, raised to the gait
  floor a body publishes (`extras.gait_floor`), and what it covers is that times the fraction the
  body says it achieves (`extras.achieved_fraction`), all of it when it says nothing;
- the turn is `search_scan`'s rate, held to the turn clamp and raised to the gait floor's;
- how near is near enough is the body's footprint, or the distance it covers between two
  readings and a tick when that is more, and the same for a heading. The planner keeps that much
  more room from what the map has seen to be solid than the body needs, and the walk turns in
  place before it walks an arc that would bulge further than that off its way;
- it stops every `stop_every_m`, the longest leg the source's map follows, and where the
  floor its front is about to cover is floor the map has not seen and its last look did not
  cover (`view`), and waits each time for the map to look (`settle_timeout_s`);
- a reading is fresh for as long as the source takes to read again and a tick, and it walks on
  none that has not moved on since the walk began;
- blind, it walks at most what one `move` could: its bound on a duration times the forward clamp.

It ends, stopped, on everything that should end it, each in words of its own: arrived, a stop or
a pause from beside it, a twist the body refused, lost, a reading that went stale or jumped, the
frame changing under it, no headway, another try that came hardly any nearer, its own clock,
and the blind cap, which says how far is left. A safety stop the run's rules raise in it, the
battery's among them, leaves in the rule's own words, the body stopped by the rule's reader, or by
the walk when anything else raised it. It never says more than the reading says: arrived means
within a tolerance by the map, read after its last look, or by odometry, which drifts.

Kept out of `quackd.verbs.core` and loaded by `go_to_place` when it runs, since it steers with
core's twist helpers and core carries the place verbs as templates.
"""

from __future__ import annotations

import itertools
import math
from dataclasses import dataclass, field
from typing import Any, Literal

from quackd.safety import SafetyStop
from quackd.space.grid import OCCUPIED, UNKNOWN, OccupancyGrid
from quackd.space.pose import Fix, Pose2D, wrap_angle
from quackd.space.source import SpatialError, SpatialSource
from quackd.transport.base import Intent, TransportError
from quackd.verbs.core import (
    HOLD_TTL_S,
    MAX_VX,
    TICK_S,
    TURN_RATE,
    MoveParams,
    _clamped,
    _ended_by,
    _Interrupted,
    _Refused,
    _stopped_by,
    _twist,
    speed_limits,
)
from quackd.verbs.places import settled_fix
from quackd.verbs.registry import VerbContext, VerbResult

SHARE = MoveParams().vx / MAX_VX
"""How much of the forward clamp the walk asks for: the share `move`'s own default speed is of the
schema's bound on it, as `drive`'s presets walk at."""


def _bound(name: str) -> float:
    for rule in MoveParams.model_fields[name].metadata:
        if (le := getattr(rule, "le", None)) is not None:
            return float(le)
    raise ValueError(f"MoveParams.{name} has no upper bound")


BLIND_S = _bound("duration_s")
"""The longest one `move` lasts: a walk with nothing to see by goes no further than one could."""

Outcome = Literal[
    "arrived",
    "capped",
    "refused",
    "blocked",
    "lost",
    "frame",
    "stale",
    "jumped",
    "stalled",
    "timeout",
    "interrupted",
    "body",
]


@dataclass
class Walked:
    """What a walk did, whatever ended it."""

    outcome: Outcome
    said: str
    metres: float = 0.0
    seconds: float = 0.0
    stops: int = 0
    replans: int = 0
    error_m: float | None = None
    left_m: float | None = None
    data: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.outcome == "arrived"

    def record(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "outcome": self.outcome,
            "walked_m": round(self.metres, 2),
            "seconds": round(self.seconds, 1),
            "stops": self.stops,
            "replans": self.replans,
        }
        if self.error_m is not None:
            out["error_m"] = round(self.error_m, 3)
        if self.left_m is not None:
            out["left_m"] = round(self.left_m, 2)
        return {**out, **self.data}


@dataclass(frozen=True)
class Gait:
    """How this body walks to a place, every number from the body or its source."""

    v: float
    """Forward speed asked for."""
    w: float
    """Turn rate asked for, turning in place."""
    v_exp: float
    w_exp: float
    """What the body covers asking for those."""
    max_wz: float
    tol_xy: float
    tol_yaw: float
    """Near enough to the place, in metres and radians."""
    track_m: float
    """How closely it follows its way: what it walks between two readings and a tick. The
    planner keeps that much room beside the body, an arc bulges no further, and a corner of the
    way is turned within it."""
    period_s: float
    """How often the source reads."""
    blind_m: float
    """The furthest it walks blind."""

    @property
    def fresh_s(self) -> float:
        """How long a reading is fresh: the source's period and a tick, a twist's hold at least."""
        return max(HOLD_TTL_S, self.period_s + TICK_S)

    @property
    def stale_s(self) -> float:
        """How long a reading may not move on before the walk ends: five of the source's periods,
        a second at least, as a stream's stale window is reckoned."""
        return max(1.0, 5 * self.period_s)

    @property
    def coast_m(self) -> float:
        """How far it may go on after it is told to stop: a twist's hold at the speed it covers,
        the time the walk gives a body to come to rest."""
        return self.v_exp * HOLD_TTL_S

    def no_headway_s(self, tol: float, rate: float) -> float:
        """How long it may come no nearer by `tol`, at `rate`, before it calls that no headway:
        twice the time that should take, and the stale window more. A gait that stood takes a
        moment to start, and one judged by the rate alone was called stalled before its first
        step. No body says how long its gait takes to start, so it is given the time the walk
        gives anything to move on, and one slower to start than that is taken for no headway."""
        return 2 * tol / rate + self.stale_s

    @classmethod
    def of(cls, ctx: VerbContext, source: SpatialSource, extras: dict[str, Any]) -> Gait:
        max_vx, _max_vy, max_wz = speed_limits(ctx.manifest)
        floor = extras.get("gait_floor") if isinstance(extras.get("gait_floor"), dict) else {}
        assert isinstance(floor, dict)
        achieved = _positive(extras.get("achieved_fraction")) or 1.0
        v = min(max(SHARE * max_vx, _positive(floor.get("vx")) or 0.0), max_vx)
        w = min(max(min(TURN_RATE, max_wz), _positive(floor.get("wz")) or 0.0), max_wz)
        period = _positive(source.period_s) or TICK_S
        # what it covers, not what it asks for: a body raised to its gait floor walks a fraction
        v_exp, w_exp = v * achieved, w * achieved
        return cls(
            v=v,
            w=w,
            v_exp=v_exp,
            w_exp=w_exp,
            max_wz=max_wz,
            tol_xy=max(_positive(source.footprint_m) or 0.0, v_exp * (period + TICK_S)),
            tol_yaw=w_exp * (period + TICK_S),
            track_m=v_exp * (period + TICK_S),
            period_s=period,
            blind_m=BLIND_S * max_vx,
        )


def _positive(value: Any) -> float | None:
    """`value` as a float when it is a finite number above zero, else None."""
    number = isinstance(value, int | float) and not isinstance(value, bool)
    return float(value) if number and value > 0 and math.isfinite(value) else None


class _End(Exception):
    """A walk that ends here, for the reason it carries, the body stopped first."""

    def __init__(self, walked: Walked) -> None:
        super().__init__(walked.said)
        self.walked = walked


@dataclass
class _Walk:
    """One walk's state: the source, the goal, the plan it is on, and its counts."""

    ctx: VerbContext
    source: SpatialSource
    goal: tuple[float, float]
    goal_yaw: float | None
    frame_id: str
    label: str
    gait: Gait
    timeout_s: float
    t0: float
    path: list[tuple[float, float]] = field(default_factory=list)
    aim: tuple[float, float] | None = None
    """Where the way ends: the place, or the nearest floor to it when its map says the place
    itself is solid, which arrival is judged against."""
    grid: OccupancyGrid | None = None
    metres: float = 0.0
    blind_walked: float = 0.0
    stops: int = 0
    replans: int = 0
    last: Fix | None = None
    fresh_at: float = -math.inf
    """When the reading last moved on, on the transport's clock: never, until it has during
    this walk. A reading the walk began on, or one it stood still for, is not fresh for that."""
    held: tuple[float, float, float] = (0.0, 0.0, 0.0)
    looked_from: Pose2D | None = None
    """Where it last stopped and looked, and which way it faced: the floor that look covered
    (`SpatialSource.view`) is no edge to stop at again."""
    reading_rule: bool = False
    """Whether the run's own rule is being read (`abort_when`), whose reader stops the body
    before it raises."""

    # ── what it reads ───────────────────────────────────────────────────────────────

    def now(self) -> float:
        return self.ctx.transport.now()

    def walked(self, outcome: Outcome, said: str, **data: Any) -> Walked:
        pose = self.last.pose if self.last is not None else None
        error = math.dist((pose.x, pose.y), self.goal) if pose is not None else None
        return Walked(
            outcome,
            said,
            metres=self.metres,
            seconds=self.now() - self.t0,
            stops=self.stops,
            replans=self.replans,
            error_m=error,
            data=data,
        )

    def read(self, *, walking: bool) -> Pose2D:
        """The newest reading, or the walk's end: lost, in another frame, gone quiet past what a
        twist may be held for, or a jump no walk could make between two readings."""
        fix = self.source.fix()
        now = self.now()
        # the frame first: a map wiped under the walk is lost too, and the wipe is the reason
        if fix.frame is None or fix.frame.id != self.frame_id:
            now_in = fix.frame.id if fix.frame is not None else "no frame"
            why = (
                "its odometry began again, which it does when the robot is picked up, falls or "
                "goes limp, or its clock goes back"
                if fix.kind == "odometry"
                else "its map was wiped, or it began another"
            )
            raise _End(
                self.walked(
                    "frame",
                    f"its reading is in another frame now ({now_in}, where {self.label} is in "
                    f"{self.frame_id}): {why}",
                )
            )
        if not fix.localized or fix.pose is None:
            raise _End(self.walked("lost", self._lost_words(fix)))
        last = self.last
        if fix.t is None:
            self.last, self.fresh_at = fix, now  # a reading with no clock cannot be judged
        elif last is None or last.t is None:
            self.last = fix  # the first: fresh once a newer one comes
        elif fix.t > last.t:
            if last.pose is not None and walking:
                self._check_jump(last, fix)
            self.last, self.fresh_at = fix, now
        quiet = now - max(self.fresh_at, self.t0)
        if not self.fresh() and quiet > self.gait.stale_s:
            raise _End(
                self.walked(
                    "stale",
                    f"its reading of where it is has not moved on in {quiet:.1f} s, so it "
                    "stopped rather than walk on a reading it cannot trust",
                )
            )
        assert self.last is not None and self.last.pose is not None
        return self.last.pose

    def _check_jump(self, last: Fix, fix: Fix) -> None:
        assert last.pose is not None and fix.pose is not None
        dt = (fix.t or 0.0) - (last.t or 0.0) if fix.t is not None and last.t is not None else 0.0
        reach = speed_limits(self.ctx.manifest)[0] * max(dt, TICK_S) + self.gait.tol_xy
        moved = math.dist((last.pose.x, last.pose.y), (fix.pose.x, fix.pose.y))
        if moved > reach:
            raise _End(
                self.walked(
                    "jumped",
                    f"its reading jumped {moved:.2f} m while it walked, further than it could walk "
                    "between two readings, so it stopped rather than steer on it",
                    jump_m=round(moved, 3),
                )
            )

    def _lost_words(self, fix: Fix) -> str:
        why = fix.lost or "no reading"
        if fix.kind == "map":
            return (
                f"it does not know where it is ({why}); its map finds it again only at stops, so "
                "walk a little and stop, and again, until it says where it is"
            )
        return f"it does not know where it is ({why})"

    def fresh(self) -> bool:
        """Whether the reading moved on within the time the source takes to read again and a
        tick, and never less than a twist's hold (`HOLD_TTL_S`)."""
        return self.now() - self.fresh_at <= self.gait.fresh_s

    async def until_fresh(self) -> Pose2D:
        """Stand until the reading moves on, the walk's first or its first after a stop, which
        ends it as stale past the source's stale window, and a stop from beside it ends too."""
        pose = self.read(walking=False)
        while not self.fresh():
            if (why := self.ctx.interrupted()) is not None:
                raise _Interrupted(why)
            await self.ctx.transport.sleep(TICK_S)
            pose = self.read(walking=False)
        return pose

    # ── what it does ────────────────────────────────────────────────────────────────

    async def send(self, vx: float, wz: float) -> None:
        """One twist, through the body's clamps (`_clamped`) and the one way a twist goes out
        (`_twist`), which a stop from beside the walk ends. A reading that has not moved on holds
        the body still rather than walk it blind."""
        twist = _clamped(self.ctx, vx, 0.0, wz)
        if not self.fresh():
            twist = (0.0, 0.0, 0.0)
        self.held = twist
        await _twist(self.ctx, *twist)
        await self.ctx.transport.sleep(TICK_S)

    async def stop_and_look(self, *, replan: bool = True) -> None:
        """Stand still and let a map that looks at stops look (`settled_fix`), read the battery
        rule there, and plan again when the map saw more or the reading was corrected, unless
        the walk is over."""
        await self.ctx.transport.stop()
        self.held = (0.0, 0.0, 0.0)
        before = self.last.pose if self.last is not None else None
        keyframes = self.last.keyframes if self.last is not None else 0
        if (why := self.ctx.interrupted()) is not None:
            raise _Interrupted(why)
        if self.source.settle_timeout_s is None:
            # no map to wait on: a body still coasting on its last twist is at rest within a
            # twist's hold, and a reading taken before then says where it was going, not where
            # it is
            for _ in range(max(1, round(HOLD_TTL_S / TICK_S))):
                await self.ctx.transport.sleep(TICK_S)
                if (why := self.ctx.interrupted()) is not None:
                    raise _Interrupted(why)
        fix = await settled_fix(self.ctx, self.source, tick=TICK_S)
        if (why := self.ctx.interrupted()) is not None:
            raise _Interrupted(why)
        self.stops += 1
        state = await self.ctx.transport.get_state()
        self.reading_rule = True
        await self.ctx.abort_when(state)
        self.reading_rule = False
        pose = self.read(walking=False)
        self.looked_from = pose
        corrected = (
            before is not None
            and math.dist((before.x, before.y), (pose.x, pose.y)) > self.gait.tol_xy / 2
        )
        if replan and (fix.keyframes != keyframes or corrected):
            await self.plan(pose)
            self.replans += 1

    async def plan(self, pose: Pose2D) -> None:
        """The way from `pose` to the goal: round what the map has seen to be solid when there is
        a map and a known size, and a straight line otherwise."""
        footprint = _positive(self.source.footprint_m)
        self.aim = self.goal
        if not (self.source.has_grid and footprint):
            self.grid, self.path = None, [self.goal]
            return
        try:
            grid = await self.source.grid()
        except (TransportError, SpatialError) as e:
            raise _End(self.walked("refused", f"its map could not be read: {e}")) from None
        if grid is None:
            self.grid, self.path = None, [self.goal]
            return
        here = grid.cell_of(pose.x, pose.y)
        if grid.inside(here) and grid.cells[here[1], here[0]] == OCCUPIED:
            raise _End(
                self.walked(
                    "refused",
                    "its map says the robot stands inside something solid, which a correction "
                    "at a stop may put right: stop and look again before walking",
                )
            )
        # where the body fits at the place, or beside it: a body's width and the place's own
        # tolerance away at most
        within = 2 * footprint + self.gait.tol_xy
        goal = _standable(grid, self.goal, footprint, within)
        if goal is None:
            raise _End(
                self.walked(
                    "refused",
                    f"{self.label} is where its map has something solid, with no floor within "
                    f"{within:.2f} m of it that a body {2 * footprint:.2f} m across fits on",
                )
            )
        # room beside the body for how closely the walk follows its way
        radius = footprint + self.gait.track_m
        found = grid.plan((pose.x, pose.y), goal, radius_m=radius)
        if found is None:
            raise _End(
                self.walked(
                    # before a step, the place cannot be walked to; after, the way is shut
                    "blocked" if self.metres > 0 else "refused",
                    f"its map has no way to {self.label} that keeps "
                    f"{grid.kept_clear_m(radius):.2f} m from everything it has seen to be solid, "
                    "which a body "
                    f"{2 * footprint:.2f} m across needs, following its way to within "
                    f"{self.gait.track_m:.2f} m, on a map of {grid.res_m * 100:.0f} cm cells",
                )
            )
        self.grid, self.path, self.aim = grid, list(found.waypoints), goal

    def blind_at(self, x: float, y: float) -> bool:
        """Whether (x, y) is floor nobody has seen: all of it, with no map."""
        if self.grid is None:
            return True
        col, row = self.grid.cell_of(x, y)
        return not self.grid.inside((col, row)) or self.grid.cells[row, col] == UNKNOWN

    def looked_here(self, pose: Pose2D) -> bool:
        """Whether its last stop looked from here, this way: within the walk's tolerance of where
        it stood, and facing within half of what a look covers of the way it faced
        (`SpatialSource.view`). Looking again would see no more, so an edge is no reason to."""
        looked = self.looked_from
        if looked is None or math.dist((looked.x, looked.y), (pose.x, pose.y)) > self.gait.tol_xy:
            return False
        view = self.source.view
        return view is None or abs(wrap_angle(pose.yaw - looked.yaw)) <= view[0] / 2

    def left_m(self, pose: Pose2D) -> float:
        points = [(pose.x, pose.y), *self.path]
        return sum(math.dist(a, b) for a, b in itertools.pairwise(points))

    async def leg(self, pose: Pose2D) -> Pose2D:
        """Walk the path from `pose` until a stop is due: the leg the source's map follows, the
        edge of floor the map has seen, the blind cap, or the goal."""
        stop_every = _positive(self.source.stop_every_m)
        # where its front is a reading on, and where it comes to rest once told to stop
        front = (_positive(self.source.footprint_m) or 0.0) + self.gait.track_m + self.gait.coast_m
        walked = 0.0
        best, quiet = math.inf, 0.0
        then = self.now()
        turning = False
        while self.path:
            self.tick_clock()
            now = self.now()
            elapsed, then = now - then, now
            target = self.path[0]
            d = math.dist((pose.x, pose.y), target)
            # a corner of the way is turned within how closely it follows it, the place itself
            # reached within the place's tolerance
            if d <= (self.gait.tol_xy if len(self.path) == 1 else self.gait.track_m):
                self.path.pop(0)
                best, quiet = math.inf, 0.0
                continue
            bearing = wrap_angle(math.atan2(target[1] - pose.y, target[0] - pose.x) - pose.yaw)
            spin, wz = steer(bearing, d, self.gait)
            # walking, how much of the arc to the target is left, which shrinks at the walk's own
            # speed: the straight distance barely does at first on a wide arc
            progress = abs(bearing) if spin else d * _arc_over_chord(bearing)
            if spin != turning:
                # its headway taken in the other's measure from here, the clock running on: one
                # begun again at every change let a reading that sways across the line between a
                # turn and an arc keep a body that something holds pushing until its time ran out
                turning, best = spin, progress
            tol = self.gait.tol_yaw if turning else self.gait.tol_xy
            rate = self.gait.w_exp if turning else self.gait.v_exp
            if not self.fresh():
                pass  # the clock stands while no reading moves on, and the body with it
            elif progress < best - tol:
                best, quiet = progress, 0.0
            else:
                quiet += elapsed
                if quiet > self.gait.no_headway_s(tol, rate):
                    raise _End(self.walked("stalled", self._no_headway(turning, quiet)))
            await self.send(0.0 if turning else self.gait.v, wz)
            new = self.read(walking=True)
            step = math.dist((pose.x, pose.y), (new.x, new.y))
            self.metres += step
            walked += step
            if self.blind_at(new.x, new.y):
                self.blind_walked += step
                if self.blind_walked >= self.gait.blind_m:
                    await self.ctx.transport.stop()
                    raise _End(
                        Walked(
                            "capped",
                            f"it walked {self.blind_walked:.1f} m where its map has seen nothing, "
                            f"as far as one move goes, and stopped short of {self.label}",
                            metres=self.metres,
                            seconds=self.now() - self.t0,
                            stops=self.stops,
                            replans=self.replans,
                            error_m=math.dist((new.x, new.y), self.goal),
                            left_m=self.left_m(new),
                        )
                    )
            pose = new
            if self.path and self.grid is not None:
                # the floor its front covers a reading on: look before walking onto it unseen
                ahead = _toward((pose.x, pose.y), self.path[0], front)
                if self.blind_at(*ahead) and not self.looked_here(pose):
                    return pose
            if stop_every is not None and walked >= stop_every:
                return pose
        return pose

    def _no_headway(self, turning: bool, seconds: float) -> str:
        """Why a leg that came no nearer ended, for a turn in place or a walk."""
        if turning:
            return (
                f"turning in place toward its way to {self.label}, it turned no nearer for "
                f"{seconds:.1f} s: something holds it, or the body does not turn in place"
            )
        blocker = (
            "something is in its way"
            if self.grid is None
            else "something its map does not show is in its way"
        )
        return (
            f"it made no headway toward {self.label} for {seconds:.1f} s: {blocker}, or the body "
            "is not walking"
        )

    def tick_clock(self) -> None:
        if self.now() - self.t0 > self.timeout_s:
            raise _End(
                self.walked(
                    "timeout",
                    f"its {self.timeout_s:g} s ran out short of {self.label}",
                )
            )

    async def face(self, pose: Pose2D, yaw: float) -> Pose2D:
        """Turn in place until it faces `yaw` within the turn's tolerance, or until it turns no
        nearer, which the reading after the walk's last look judges: a reading taken turning
        sways with the gait, so it may say it faces the way and the body at rest not."""
        best, quiet = math.inf, 0.0
        then = self.now()
        while True:
            self.tick_clock()
            now = self.now()
            elapsed, then = now - then, now
            error = wrap_angle(yaw - pose.yaw)
            if abs(error) <= self.gait.tol_yaw:
                break
            if not self.fresh():
                pass  # the clock stands while no reading moves on, and the body with it
            elif abs(error) < best - self.gait.tol_yaw:
                best, quiet = abs(error), 0.0
            else:
                quiet += elapsed
                if quiet > self.gait.no_headway_s(self.gait.tol_yaw, self.gait.w_exp):
                    break
            await self.send(0.0, math.copysign(self.gait.w, error))
            pose = self.read(walking=True)
        await self.ctx.transport.stop()
        return pose


def steer(bearing: float, d: float, gait: Gait) -> tuple[bool, float]:
    """Whether to turn in place toward a point at `bearing` and `d` metres, and the turn rate:
    in place at the gait's turn, or along the arc pure pursuit walks to it, `2 v sin b / d`. It
    turns in place when the point is behind its side, when that arc would bulge further off the
    straight line to the point than the room the planner kept beside the body (`track_m`), its
    sagitta being `d/2 tan(b/2)`, and when it would turn faster than the body may."""
    spin = (
        abs(bearing) >= math.pi / 2
        or d / 2 * math.tan(abs(bearing) / 2) > gait.track_m
        or 2 * gait.v * abs(math.sin(bearing)) / d > gait.max_wz
    )
    if spin:
        return True, math.copysign(gait.w, bearing)
    return False, 2 * gait.v * math.sin(bearing) / d


def _toward(a: tuple[float, float], b: tuple[float, float], metres: float) -> tuple[float, float]:
    """The point `metres` from `a` toward `b`, or `b` when it is nearer than that."""
    d = math.dist(a, b)
    if d <= metres:
        return b
    return a[0] + (b[0] - a[0]) * metres / d, a[1] + (b[1] - a[1]) * metres / d


def _arc_over_chord(bearing: float) -> float:
    """How much longer the arc pure pursuit walks to a point at `bearing` is than the straight
    line to it: the bearing over its sine, 1 dead ahead and pi/2 at a right angle."""
    return 1.0 if abs(bearing) < 1e-6 else bearing / math.sin(bearing)


def _standable(
    grid: OccupancyGrid, goal: tuple[float, float], body_m: float, within: float
) -> tuple[float, float] | None:
    """`goal` when a body of radius `body_m` fits there on what the map has seen to be solid, or
    else the nearest cell centre within `within` of it where one does, or None. A body fits where
    no solid cell's centre is nearer than the radius and half a cell, the most a cell marked
    solid can hold its solid short of its centre."""
    need = body_m + grid.res_m / 2
    reach = math.ceil(need / grid.res_m) + 1
    solid = grid.cells == OCCUPIED

    def fits(x: float, y: float) -> bool:
        col, row = grid.cell_of(x, y)
        for dr in range(-reach, reach + 1):
            for dc in range(-reach, reach + 1):
                cell = (col + dc, row + dr)
                near = grid.inside(cell) and solid[cell[1], cell[0]]
                if near and math.dist(grid.center_of(cell), (x, y)) < need:
                    return False
        return True

    if fits(*goal):
        return goal
    col, row = grid.cell_of(*goal)
    ring = math.ceil(within / grid.res_m)
    nearby = sorted(
        (math.dist(grid.center_of((col + dc, row + dr)), goal), (col + dc, row + dr))
        for dr in range(-ring, ring + 1)
        for dc in range(-ring, ring + 1)
    )
    for d, cell in nearby:
        if d > within:
            break
        if grid.inside(cell) and grid.cells[cell[1], cell[0]] != OCCUPIED:
            centre = grid.center_of(cell)
            if fits(*centre):
                return centre
    return None


async def navigate(
    ctx: VerbContext,
    source: SpatialSource,
    goal: tuple[float, float],
    *,
    goal_yaw: float | None,
    frame_id: str,
    timeout_s: float,
    label: str,
    verb: str = "go_to_place",
) -> Walked:
    """Walk to `goal`, a point in the frame `frame_id`, on `source`'s reading, turning to face
    `goal_yaw` at the end when one is given. Refused before anything moves when the robot does not
    know where it is, its frame is not the goal's, its map has no way there, or the way is longer
    than `timeout_s` allows. Stopped, whatever ends it. `verb` is the one a stop or a refused twist
    is said to have ended, for a verb that walks by this as `explore` does."""
    state = await ctx.transport.get_state()
    extras = state.extras if isinstance(state.extras, dict) else {}
    walk = _Walk(
        ctx=ctx,
        source=source,
        goal=goal,
        goal_yaw=goal_yaw,
        frame_id=frame_id,
        label=label,
        gait=Gait.of(ctx, source, extras),
        timeout_s=timeout_s,
        t0=ctx.transport.now(),
    )
    try:
        try:
            return await _navigate(walk)
        except _End as end:
            await ctx.transport.stop()
            return end.walked
    except _Refused as refused:
        failed = await _stopped_by(ctx, verb, "the body stopped", refused)
        return walk.walked("body", failed.summary, move_refused=str(refused))
    except _Interrupted as told:
        ended = await _ended_by(ctx, verb, told.why, "the body stopped")
        return walk.walked("interrupted", ended.summary, **ended.data)
    except SafetyStop:
        # the run's own rule, read at a stop: its reader stopped the body before it raised, and a
        # stop awaited here would let the run's abort cancel this walk before its words are read.
        # One raised anywhere else, as a sleep raises one for a window closed or a body handed to
        # another goal, came with the last twist still standing
        if not walk.reading_rule:
            await ctx.transport.stop()
        raise
    except BaseException:
        # cancelled from outside, by the executor's own clock, or a fault: the body left stopped
        await ctx.transport.stop()
        raise


async def _navigate(walk: _Walk) -> Walked:
    fix = await settled_fix(walk.ctx, walk.source, tick=TICK_S)
    if not fix.localized or fix.pose is None:
        raise _End(walk.walked("lost", walk._lost_words(fix)))
    pose = await walk.until_fresh()
    if math.dist((pose.x, pose.y), walk.goal) <= walk.gait.tol_xy and walk.goal_yaw is None:
        return walk.walked("arrived", f"already at {walk.label}")
    await walk.plan(pose)
    length = walk.left_m(pose)
    needs = length / walk.gait.v_exp
    if needs > walk.timeout_s:
        raise _End(
            walk.walked(
                "refused",
                f"the way to {walk.label} is {length:.1f} m, about {needs:.0f} s at the speed this "
                f"body walks, longer than the {walk.timeout_s:g} s it was given",
                way_m=round(length, 2),
                needs_s=round(needs, 1),
                given_s=round(walk.timeout_s, 1),
            )
        )
    # the head faces the way it walks, so a map that looks along it looks ahead
    if (why := walk.ctx.interrupted()) is not None:
        raise _Interrupted(why)
    await walk.ctx.transport.send_intent(Intent.look(1.0, 0.0, 0.0))
    nearest, truest = math.inf, math.inf
    aimed: tuple[float, float] | None = None
    while True:
        while walk.path:
            pose = await walk.leg(pose)
            if walk.path:
                await walk.stop_and_look()
                pose = await walk.until_fresh()
        await walk.ctx.transport.stop()
        if walk.goal_yaw is not None:
            pose = await walk.face(pose, walk.goal_yaw)
        await walk.stop_and_look(replan=False)
        pose = await walk.until_fresh()
        # arrived only by the reading after its last look, which may have corrected where it is,
        # at the end of its way
        aim = walk.aim if walk.aim is not None else walk.goal
        away = math.dist((pose.x, pose.y), aim)
        off = 0.0 if walk.goal_yaw is None else abs(wrap_angle(walk.goal_yaw - pose.yaw))
        if away <= walk.gait.tol_xy and off <= walk.gait.tol_yaw:
            break
        if aimed is not None and math.dist(aim, aimed) > walk.gait.tol_xy / 2:
            nearest = math.inf  # a new end to its way: the passes before came near another
        aimed = aim
        # another pass only after one that came nearer, to the place or to its heading, by more
        # than a correction at a stop is taken for: one that did not would be walked again until
        # the walk's clock ran out
        if away > nearest - walk.gait.tol_xy / 2 and off > truest - walk.gait.tol_yaw / 2:
            if away <= walk.gait.tol_xy:
                break  # at the place, and turned as near its heading as it would: said below
            how = "no" if away >= nearest else "hardly any"
            to = (
                walk.label
                if aim == walk.goal
                else f"the nearest floor to {walk.label} the body fits on"
            )
            raise _End(
                walk.walked(
                    "stalled",
                    f"it came to {away:.2f} m from {to}, where {walk.gait.tol_xy:.2f} m is near "
                    f"enough, and another try brought it {how} nearer, so it stopped there",
                )
            )
        nearest, truest = min(nearest, away), min(truest, off)
        # a tick standing, so the walk's clock moves even on a pass that walked nothing
        await walk.ctx.transport.sleep(TICK_S)
        walk.tick_clock()
        await walk.plan(pose)
        walk.replans += 1
    error = math.dist((pose.x, pose.y), walk.goal)
    assert walk.last is not None
    by = "by its map" if walk.last.kind == "map" else "by its odometry, which drifts"
    facing = ""
    if walk.goal_yaw is not None:
        off = abs(wrap_angle(walk.goal_yaw - pose.yaw))
        if off <= walk.gait.tol_yaw:
            facing = f", facing within {math.degrees(off):.0f}° of the way it faced there"
        elif off <= truest:
            facing = (
                f", facing {math.degrees(off):.0f}° off the way it faced there, as near as it "
                "turned"
            )
        else:
            facing = (
                f", facing {math.degrees(off):.0f}° off the way it faced there, where a try "
                f"before came to within {math.degrees(truest):.0f}°"
            )
    beside = ""
    if walk.aim is not None and walk.aim != walk.goal:
        beside = (
            ", at the nearest floor to it the body fits on: its map has something solid at the "
            "place itself"
        )
    return walk.walked(
        "arrived",
        f"{error:.2f} m from {walk.label} {by}{beside}{facing}; walked {walk.metres:.1f} m in "
        f"{walk.now() - walk.t0:.0f} s, {walk.stops} stop{'s' if walk.stops != 1 else ''}, "
        f"{walk.replans} re-plan{'s' if walk.replans != 1 else ''}",
    )


def verb_result(walked: Walked) -> VerbResult:
    """`go_to_place`'s answer from a walk."""
    data = walked.record()
    if walked.ok:
        return VerbResult.success(f"go_to_place: {walked.said}", **data)
    if walked.outcome in ("interrupted", "body"):
        return VerbResult.fail(walked.said, **data)
    return VerbResult.fail(f"go_to_place: {walked.said}", **data)
