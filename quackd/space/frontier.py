"""Where a robot that wants to see more of a room goes next, and which ways it looks once there.

A frontier is floor the map has seen beside floor it has not (`OccupancyGrid.frontiers`). This
module groups those cells into the stretches a robot could walk to, leaves out the ones too narrow
for its body and the ones it has looked at already without seeing past, ranks the rest by how far
the robot would walk to reach them over floor the map has seen, and says which ways a sensor of a
given width and range should look from a stop to see something it has not. Every number comes from
the caller: a body's size, a sensor's width and range, the grid's own cells.
"""

from __future__ import annotations

import math
from collections.abc import Collection, Iterable
from dataclasses import dataclass, field

from quackd.space.grid import OCCUPIED, UNKNOWN, Cell, OccupancyGrid

WorldCell = tuple[int, int]
"""A cell named by where it is in the frame rather than by its place in one grid: a map's grid grows
and moves its origin as the robot walks, and a cell given up must stay given up in the next one."""


@dataclass(frozen=True)
class Cluster:
    """A stretch of frontier the robot can walk to: its cells, how far the robot would walk to the
    nearest of them over floor the map has seen, and the middle of that cell."""

    cells: tuple[Cell, ...]
    distance_m: float
    goal: tuple[float, float]

    @property
    def size(self) -> int:
        return len(self.cells)


@dataclass
class GivenUp:
    """The frontier a robot walked to, looked at, and still could not see past, kept for the map
    frame it was given up in: under the sofa, glass, a gap seen at a grazing angle. A new frame,
    a wipe among the ways to one, starts with nothing given up."""

    frame: str | None = None
    cells: set[WorldCell] = field(default_factory=set)

    def in_frame(self, frame_id: str) -> set[WorldCell]:
        """The cells given up in `frame_id`, emptied first when the frame is not the one kept."""
        if frame_id != self.frame:
            self.frame, self.cells = frame_id, set()
        return self.cells


def world_cell(grid: OccupancyGrid, cell: Cell) -> WorldCell:
    """The cell's name in the frame: where its middle is, in cells of the grid's size."""
    x, y = grid.center_of(cell)
    return (math.floor(x / grid.res_m), math.floor(y / grid.res_m))


def body_cells(res_m: float, radius_m: float) -> int:
    """How many cells a body of `radius_m` is wide."""
    return math.ceil(2 * radius_m / res_m)


def extent_cells(cells: Iterable[Cell]) -> float:
    """How far a stretch of cells reaches, in cells: the diagonal of the box it fills, which is its
    length for a straight one along a side or a diagonal."""
    cols = [c for c, _ in cells]
    rows = [r for _, r in cells]
    if not cols:
        return 0.0
    return math.hypot(max(cols) - min(cols) + 1, max(rows) - min(rows) + 1)


def opening_cells(grid: OccupancyGrid, cells: Iterable[Cell]) -> float:
    """How wide the opening a frontier group looks into is, in cells: the reach of the cells nobody
    has seen that it borders at a side (`extent_cells`), past the grid's edge included. The group's
    own reach says nothing of it: a single cell nobody has seen, in the middle of seen floor, is
    ringed by a group three cells across."""
    beside: set[Cell] = set()
    for c, r in cells:
        for dc, dr in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            near = (c + dc, r + dr)
            if not grid.inside(near) or grid.cells[near[1], near[0]] == UNKNOWN:
                beside.add(near)
    return extent_cells(beside)


def frontier_groups(
    grid: OccupancyGrid, given_up: Collection[WorldCell] = (), *, min_cells: float = 0.0
) -> list[tuple[Cell, ...]]:
    """The frontier cells not given up, in groups of cells that touch at a side or a corner, each
    looking into an opening no narrower than `min_cells` (`opening_cells`), as a body that cannot
    pass through one cannot see past it either. Each group's cells are in row order, and the
    groups are in the order of their first cell, so the same grid gives the same groups."""
    open_cells = {cell for cell in grid.frontiers() if world_cell(grid, cell) not in given_up}
    groups: list[tuple[Cell, ...]] = []
    seen: set[Cell] = set()
    for start in sorted(open_cells, key=_row_order):
        if start in seen:
            continue
        group = [start]
        seen.add(start)
        i = 0
        while i < len(group):
            c, r = group[i]
            i += 1
            for dc in (-1, 0, 1):
                for dr in (-1, 0, 1):
                    near = (c + dc, r + dr)
                    if near in open_cells and near not in seen:
                        seen.add(near)
                        group.append(near)
        if opening_cells(grid, group) >= min_cells:
            groups.append(tuple(sorted(group, key=_row_order)))
    return groups


def nearest_first(
    grid: OccupancyGrid,
    start: tuple[float, float],
    groups: Iterable[tuple[Cell, ...]],
    *,
    radius_m: float,
) -> list[Cluster]:
    """Every group a body of `radius_m` can walk to from `start` without crossing floor the map has
    not seen, nearest by the walk first (`OccupancyGrid.distances`), the larger first of two as
    near, then the one whose first cell comes first, so runs choose alike. A group none of whose
    cells can be reached is left out."""
    far = grid.distances(start, radius_m=radius_m)
    out: list[Cluster] = []
    for cells in groups:
        reached = [(float(far[r, c]), (c, r)) for c, r in cells if math.isfinite(far[r, c])]
        if not reached:
            continue
        distance, cell = min(reached, key=lambda pair: (pair[0], _row_order(pair[1])))
        out.append(Cluster(cells, distance, grid.center_of(cell)))
    # distances are sums of steps, which two ways of an equal length can add in another order
    out.sort(key=lambda k: (round(k.distance_m, 9), -k.size, _row_order(k.cells[0])))
    return out


def still_unseen_beside(grid: OccupancyGrid, cells: Iterable[WorldCell]) -> set[WorldCell]:
    """Those of `cells` that are still frontier in `grid`: what a look at them did not see past."""
    frontier = {world_cell(grid, cell) for cell in grid.frontiers()}
    return {cell for cell in cells if cell in frontier}


# ── looking ─────────────────────────────────────────────────────────────────────────────


def views_round(width_deg: float) -> tuple[float, int]:
    """The step between the views of a sensor `width_deg` wide that turns a whole circle in whole
    steps, each view overlapping the next, and how many views make the circle: one more than fit
    into it edge to edge. `quackd.verbs.core.scan_steps` turns a camera by the same rule."""
    per_turn = math.floor(360.0 / width_deg) + 1
    return 360.0 / per_turn, per_turn


def nearest_view(headings: Iterable[float], yaw: float) -> float:
    """The one of `headings` the shortest turn from `yaw` reaches, and of two as near, a step
    either side of it, the smaller heading. The turns are rounded before they are compared: two as
    long may differ in their last bit, and which is the shorter there differs from one platform's
    libm to another's, so a run would choose by the platform it ran on."""
    return min(headings, key=lambda h: (round(abs(math.remainder(h - yaw, math.tau)), 9), h))


def fan(fov_rad: float, range_m: float, res_m: float) -> int:
    """How many rays a cone `fov_rad` wide is cast as, so two neighbours are no more than a cell
    apart at the end of its range."""
    return max(1, math.ceil(fov_rad * range_m / res_m))


def ray_to_wall(grid: OccupancyGrid, x: float, y: float, heading: float, range_m: float) -> float:
    """How far a ray from (x, y) along `heading` runs before a cell the map has seen to be solid,
    up to `range_m`: as far as a sensor looking that way can see."""
    step = grid.res_m / 2
    c, s = math.cos(heading), math.sin(heading)
    d = step
    while d < range_m:
        if grid.state_at(x + c * d, y + s * d) == OCCUPIED:
            return d
        d += step
    return range_m


def unseen_in_view(
    grid: OccupancyGrid, x: float, y: float, heading: float, *, fov_rad: float, range_m: float
) -> int:
    """How many cells the map has not seen a sensor at (x, y) looking along `heading` would see:
    those its cone's rays cross before the first cell seen to be solid, out to `range_m`."""
    step = grid.res_m / 2
    rays = fan(fov_rad, range_m, grid.res_m)
    unseen: set[Cell] = set()
    for k in range(rays):
        angle = heading - fov_rad / 2 + fov_rad * (k + 0.5) / rays
        c, s = math.cos(angle), math.sin(angle)
        d = step
        while d < range_m:
            px, py = x + c * d, y + s * d
            state = grid.state_at(px, py)
            if state == OCCUPIED:
                break
            if state == UNKNOWN:
                unseen.add(grid.cell_of(px, py))
            d += step
    return len(unseen)


def views_worth_taking(
    grid: OccupancyGrid,
    x: float,
    y: float,
    yaw: float,
    *,
    fov_rad: float,
    range_m: float,
    radius_m: float,
) -> list[float]:
    """The headings worth a look from (x, y), out of a whole circle of views from `yaw` on
    (`views_round`): those whose cone would see at least as many cells the map has not seen as a
    body of `radius_m` is wide, so a look is spent on something it can learn."""
    step_deg, count = views_round(math.degrees(fov_rad))
    needed = max(1, body_cells(grid.res_m, radius_m))
    headings = [math.remainder(yaw + math.radians(step_deg) * k, math.tau) for k in range(count)]
    return [
        h
        for h in headings
        if unseen_in_view(grid, x, y, h, fov_rad=fov_rad, range_m=range_m) >= needed
    ]


def _row_order(cell: Cell) -> tuple[int, int]:
    return (cell[1], cell[0])
