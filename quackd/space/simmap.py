"""A stand-in for a robot's own mapper, for the simulators given a room.

The real Microduck's `mapd` keeps a keyframe each time the duck stands still, reveals what its
time-of-flight sensor sees from there, says where the duck is in its map, and says it is lost
after a fall or a carry until two stops agree on where it is. This does the same against a room
whose every wall it knows, so a walk, a map and an exploration can be tested with no robot and
no luck: it draws no random numbers, and the same seed and room make the same map. It is lost as
well after the duck walks into something, which the real one says nothing of in those words: a
duck that pushed at a wall has feet that slipped, and odometry that no longer says where it is.

It is a stand-in, said so in every state (`extras.assumptions`), and it takes the real mapper's
numbers as plain floats (`SimMapParams`), read by the adapter from what it read of the real one.
Its lost words are quackd's own. Its frame is the room's own coordinates rather than its first
stop's: what a frame's coordinates are is opaque to everything above, so nothing reads the
difference, and a grid that never turns needs no rotating.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np
import numpy.typing as npt

from quackd.space.grid import FREE, OCCUPIED, UNKNOWN, OccupancyGrid
from quackd.space.pose import Fix, FrameRef, Pose2D
from quackd.space.room import Room
from quackd.space.source import SpatialSourceBase

if TYPE_CHECKING:
    from quackd.space.places import FrameRecord

BOOT = "not localized since it started: it has made no stop yet"
BUMPED = "walked into something"
FELL = "fell"
CARRIED = "carried"

CLOCK_SLACK_S = 1e-9
"""A simulator's clock is a sum of its steps, and 0.4 s of them can add up to a hair under 0.4."""

STAND_IN = (
    "the map is quackd's stand-in for the robot's own mapper: it knows the room's walls "
    "exactly, looks only from a stop, and is lost at the start, after walking into something "
    "and after a fall until two stops agree, so a walk here is no measure of the real map's errors"
)
"""Said in every state of a simulator with a room (`extras.assumptions`)."""


@dataclass(frozen=True)
class SimMapParams:
    """The real mapper's numbers, which the stand-in keeps to. Each is the adapter's to give,
    from what it read of the real one; none is quackd's own."""

    still_s: float
    """How long the body stands still before a keyframe is taken."""
    still_speed_mps: float
    still_turn_rps: float
    """Below these the body counts as still."""
    period_s: float
    """How often the real mapper says where the robot is."""
    reloc_stops: int
    """How many stops it takes a lost robot to know where it is again."""
    gate_m: float
    gate_rad: float
    """The largest correction the real mapper believes at a stop."""
    res_m: float
    """The grid's cell, as the real mapper renders one unless asked."""
    tof_fov_rad: float
    tof_min_m: float
    tof_range_m: float
    """What the depth sensor sees: its field across, and the ranges it reports."""
    footprint_m: float
    """The body's radius, the simulator's own."""


@dataclass(frozen=True)
class BodyNow:
    """The truth about the simulated body this tick, which only the simulator knows."""

    t: float
    x: float
    y: float
    theta: float
    head_yaw: float
    standing: bool
    fallen: bool
    bumps: int
    moving: bool = False
    """Whether the body is told to move now: a body standing still that has not been looked
    from since it last moved is a stop the map is collecting a keyframe at."""


class SimulatedMap(SpatialSourceBase):
    """The simulators' `mapd`: `tick()` on every step of the simulator's clock, `fix()` and
    `grid()` for everything above."""

    def __init__(
        self,
        room: Room,
        params: SimMapParams,
        *,
        body: Callable[[], BodyNow],
        frame_key: str,
    ) -> None:
        self.room = room
        self.params = params
        self._body = body
        self._frame_key = frame_key
        xmin, ymin, xmax, ymax = room.bounds
        margin = params.res_m
        self._origin = (xmin - margin, ymin - margin)
        w = math.ceil((xmax - xmin + 2 * margin) / params.res_m)
        h = math.ceil((ymax - ymin + 2 * margin) / params.res_m)
        self._cells: npt.NDArray[np.uint8] = np.full((h, w), UNKNOWN, dtype=np.uint8)
        self._keyframes: list[Pose2D] = []
        self._wipes = 0
        self._lost: str | None = BOOT
        self._stops_since_lost = 0
        first = body()
        self._last: BodyNow | None = first
        self._still_since: float | None = None
        self._taken_this_stop = False
        self._bumps = first.bumps

    # ── the clock ───────────────────────────────────────────────────────────────────

    def tick(self) -> None:
        """One step of the simulator: is the body still, has it been long enough for a keyframe,
        did it bump or fall."""
        now = self._body()
        if now.bumps > self._bumps:
            self._bumps = now.bumps
            self._lose(BUMPED)
        if now.fallen:
            self._lose(FELL)
        last, self._last = self._last, now
        # still since the sample before this one, when nothing moved between the two
        since = now.t if last is None else last.t
        if last is None:
            # nothing to measure against once it was put down: a body standing and told to stay
            # where it is is at a stop
            moving = now.moving or not now.standing
        elif now.t > last.t:
            dt = now.t - last.t
            speed = math.hypot(now.x - last.x, now.y - last.y) / dt
            turn = abs(math.remainder(now.theta - last.theta, math.tau)) / dt
            head = abs(now.head_yaw - last.head_yaw) / dt
            moving = (
                speed >= self.params.still_speed_mps
                or turn >= self.params.still_turn_rps
                or head >= self.params.still_turn_rps
                or not now.standing
            )
        else:
            moving = False  # the clock did not move, so neither did the body
        if moving:
            self._still_since, self._taken_this_stop = None, False
            return
        if self._still_since is None:
            self._still_since = since
        still_for = now.t - self._still_since
        if not self._taken_this_stop and still_for >= self.params.still_s - CLOCK_SLACK_S:
            self._keyframe(now)
            self._taken_this_stop = True

    def carry(
        self, x: float, y: float, theta: float, move: Callable[[float, float, float], None]
    ) -> None:
        """A test's hand: put the body down at (x, y, theta) through `move`, the simulator's own,
        and lose it as a real map loses a duck that was carried."""
        move(x, y, theta)
        self._lose(CARRIED)
        self._last = None

    def lose(self, why: str) -> None:
        """A test's hook: lose the body for `why`, as the real map says it."""
        self._lose(why)

    def _lose(self, why: str) -> None:
        self._lost = why
        self._stops_since_lost = 0

    def _keyframe(self, now: BodyNow) -> None:
        self._reveal(now)
        pose = Pose2D(now.x, now.y, now.theta)
        self._keyframes.append(pose)
        if self._lost is not None:
            if self._lost == BOOT:
                self._lost = None  # the first stop makes the map, and it knows where it is in it
            else:
                self._stops_since_lost += 1
                if self._stops_since_lost >= self.params.reloc_stops:
                    self._lost = None

    def _reveal(self, now: BodyNow) -> None:
        p = self.params
        heading = now.theta + now.head_yaw
        # enough rays that two neighbours are no more than a cell apart at the far end of the range
        rays = max(1, math.ceil(p.tof_fov_rad * p.tof_range_m / p.res_m))
        step = p.res_m / 2
        for k in range(rays):
            angle = heading - p.tof_fov_rad / 2 + p.tof_fov_rad * (k + 0.5) / rays
            c, s = math.cos(angle), math.sin(angle)
            hit = self.room.raycast(now.x, now.y, angle, p.tof_range_m)
            reach = hit if hit is not None else p.tof_range_m
            r = p.tof_min_m
            while r < reach:
                self._mark(now.x + c * r, now.y + s * r, FREE, keep_occupied=True)
                r += step
            if hit is not None and hit >= p.tof_min_m:
                # just past the face, so the cell marked is the one the obstacle is in
                inside = hit + step / 2
                self._mark(now.x + c * inside, now.y + s * inside, OCCUPIED)
        # the floor the body stands on is floor
        reach = math.ceil(p.footprint_m / p.res_m)
        for dc in range(-reach, reach + 1):
            for dr in range(-reach, reach + 1):
                x, y = now.x + dc * p.res_m, now.y + dr * p.res_m
                if math.hypot(x - now.x, y - now.y) <= p.footprint_m:
                    self._mark(x, y, FREE, keep_occupied=True)

    def _mark(self, x: float, y: float, state: int, *, keep_occupied: bool = False) -> None:
        col = math.floor((x - self._origin[0]) / self.params.res_m)
        row = math.floor((y - self._origin[1]) / self.params.res_m)
        h, w = self._cells.shape
        if 0 <= col < w and 0 <= row < h:
            if keep_occupied and self._cells[row, col] == OCCUPIED:
                return
            self._cells[row, col] = state

    # ── what everything above reads ─────────────────────────────────────────────────

    @property
    def period_s(self) -> float | None:
        return self.params.period_s

    @property
    def footprint_m(self) -> float | None:
        return self.params.footprint_m

    @property
    def stop_every_m(self) -> float | None:
        return self.params.gate_m / math.sin(self.params.gate_rad)

    @property
    def settle_timeout_s(self) -> float | None:
        return self.params.still_s + 2 * self.params.period_s

    @property
    def keyframes_follow_head(self) -> bool:
        return True

    @property
    def view(self) -> tuple[float, float] | None:
        return self.params.tof_fov_rad, self.params.tof_range_m

    @property
    def has_grid(self) -> bool:
        return True

    @property
    def can_wipe(self) -> bool:
        return True

    @property
    def frame(self) -> FrameRef:
        return FrameRef("map", self._frame_key, persistent=True, epoch=self._wipes)

    def resolve_frame(self, records: Sequence[FrameRecord]) -> FrameRef | None:
        """The room's frame, past every epoch of it the place store remembers wiping: the same
        room is the same map from one session to the next, and a map wiped in an earlier one
        stays wiped, so the places marked in it stay orphaned."""
        wiped = {record.id for record in records if record.wiped}
        while self.frame.id in wiped:
            self._wipes += 1
        return self.frame

    def fix(self) -> Fix:
        now = self._body()
        # Collecting while it stands still at a stop it has not looked from yet: one the clock
        # has not ticked through since it stopped is one, and so is one where anything moved,
        # the head included, since the last tick.
        last = self._last
        moved = last is not None and (now.x, now.y, now.theta, now.head_yaw) != (
            last.x,
            last.y,
            last.theta,
            last.head_yaw,
        )
        looked = self._taken_this_stop and not moved
        collecting = not looked and now.standing and not now.moving and not now.fallen
        if self._lost is not None:
            return Fix(
                None,
                "map",
                self.frame,
                lost=self._lost,
                t=now.t,
                keyframes=len(self._keyframes),
                collecting=collecting,
            )
        return Fix(
            Pose2D(now.x, now.y, now.theta),
            "map",
            self.frame,
            t=now.t,
            keyframes=len(self._keyframes),
            collecting=collecting,
        )

    async def grid(self, res_m: float | None = None) -> OccupancyGrid | None:
        fix = self.fix()
        return OccupancyGrid(
            self.params.res_m,
            self._origin,
            self._cells.copy(),
            tuple(self._keyframes),
            fix.pose,
        )

    def grid_now(self) -> OccupancyGrid:
        """The revealed grid, without awaiting: for a test, and a renderer drawing it each frame."""
        return OccupancyGrid(
            self.params.res_m, self._origin, self._cells.copy(), tuple(self._keyframes), None
        )

    async def wipe(self) -> int:
        return self.wipe_now()

    def wipe_now(self) -> int:
        """`wipe`, from a simulator's clock hook as well, which cannot await: forget the map, and
        start a frame of its own at the next stop. How many stops it forgot."""
        forgotten = len(self._keyframes)
        self._cells[:] = UNKNOWN
        self._keyframes.clear()
        self._wipes += 1
        self._lose(BOOT)
        return forgotten

    def describe(self) -> str:
        return f"the simulator's stand-in for the robot's own map ({self.room.name})"
