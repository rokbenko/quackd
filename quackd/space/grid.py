"""An occupancy grid read from whoever made one, and the arithmetic a walk needs from it.

quackd makes no map. A grid arrives from a robot's own mapper (the Microduck's `mapd`, as text) or
from a simulator, and this module turns it into three states and plans on them. The states are
numbered so that `max` is the order a coarser view keeps: something solid in a block is shown
even when most of it is floor, and floor seen anywhere beats a block never looked at.
"""

from __future__ import annotations

import heapq
import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

from quackd.space.pose import Pose2D

UNKNOWN = 0
FREE = 1
OCCUPIED = 2

RENDER = {UNKNOWN: "?", FREE: ".", OCCUPIED: "#"}
"""The characters quackd prints a grid with. A source that sends its grid as text says what its own
characters mean (`OccupancyGrid.from_text`); these are quackd's, whatever the source's were."""

LEGEND = "# solid · . floor seen empty · ? not seen · > ^ < v the robot, pointing the way it faces"

_STEPS: tuple[tuple[int, int, float], ...] = tuple(
    (dc, dr, math.hypot(dc, dr)) for dc in (-1, 0, 1) for dr in (-1, 0, 1) if dc or dr
)

Cell = tuple[int, int]
"""(column, row): column along x, row along y, row 0 the lowest y."""


@dataclass(frozen=True)
class Plan:
    """A walk through a grid: the corners to walk between, the start left out and the goal last,
    how long it is, and how much of it crosses floor nobody has seen."""

    waypoints: tuple[tuple[float, float], ...]
    length_m: float
    unseen_m: float


@dataclass
class OccupancyGrid:
    """`cells[row, col]` holds one of UNKNOWN, FREE, OCCUPIED. Row `r` spans y from
    `origin[1] + r * res_m`, and column `c` x from `origin[0] + c * res_m`: `origin` is the low
    corner of cell (0, 0) in the frame the grid's poses are in."""

    res_m: float
    origin: tuple[float, float]
    cells: npt.NDArray[np.uint8]
    keyframes: tuple[Pose2D, ...] = ()
    pose: Pose2D | None = None

    def __post_init__(self) -> None:
        if not (math.isfinite(self.res_m) and self.res_m > 0):
            raise ValueError(f"a grid's cells need a size above zero, not {self.res_m!r}")
        if self.cells.ndim != 2:
            raise ValueError("a grid's cells are a table of rows and columns")
        if self.cells.size and int(self.cells.max()) > OCCUPIED:
            raise ValueError("a grid's cells are unknown, free or occupied, and nothing else")
        self.cells = self.cells.astype(np.uint8, copy=False)

    @property
    def width(self) -> int:
        return int(self.cells.shape[1])

    @property
    def height(self) -> int:
        return int(self.cells.shape[0])

    @classmethod
    def from_text(
        cls,
        text: str,
        *,
        width: int,
        height: int,
        res_m: float,
        origin: tuple[float, float],
        alphabet: Mapping[str, int],
        keyframes: Sequence[Pose2D] = (),
        pose: Pose2D | None = None,
    ) -> OccupancyGrid:
        """A grid sent as one character per cell, row-major from the lowest y, in an alphabet the
        caller names: which character means unknown, free and occupied is the source's word, so it
        is an argument here and never spelled in this module."""
        if width < 0 or height < 0 or len(text) != width * height:
            raise ValueError(
                f"a {width} by {height} grid needs {width * height} cells, and {len(text)} came"
            )
        lookup = np.full(128, 255, dtype=np.uint8)
        for char, state in alphabet.items():
            if len(char) != 1 or ord(char) >= 128 or state not in RENDER:
                raise ValueError(f"an alphabet maps one ASCII character to a state, not {char!r}")
            lookup[ord(char)] = state
        try:
            codes = np.frombuffer(text.encode("ascii"), dtype=np.uint8)
        except UnicodeEncodeError as e:
            raise ValueError(f"a grid's cells are ASCII characters: {e}") from None
        states = lookup[codes]
        if (bad := np.flatnonzero(states == 255)).size:
            char = text[int(bad[0])]
            raise ValueError(f"cell character {char!r} is not one the source said it sends")
        return cls(res_m, origin, states.reshape(height, width), tuple(keyframes), pose)

    # ── cells and metres ────────────────────────────────────────────────────────────

    def cell_of(self, x: float, y: float) -> Cell:
        """The cell (x, y) falls in, which may be outside the grid."""
        return (
            math.floor((x - self.origin[0]) / self.res_m),
            math.floor((y - self.origin[1]) / self.res_m),
        )

    def center_of(self, cell: Cell) -> tuple[float, float]:
        col, row = cell
        return (
            self.origin[0] + (col + 0.5) * self.res_m,
            self.origin[1] + (row + 0.5) * self.res_m,
        )

    def inside(self, cell: Cell) -> bool:
        col, row = cell
        return 0 <= col < self.width and 0 <= row < self.height

    def state_at(self, x: float, y: float) -> int:
        """The state of the cell (x, y) falls in, UNKNOWN outside the grid."""
        cell = self.cell_of(x, y)
        return int(self.cells[cell[1], cell[0]]) if self.inside(cell) else UNKNOWN

    # ── views ───────────────────────────────────────────────────────────────────────

    def downsample(self, factor: int) -> OccupancyGrid:
        """A grid of `factor` x `factor` blocks, each the most telling of its cells: occupied over
        free over unknown. A block past the edge counts its missing cells as unknown."""
        if factor <= 1:
            return self
        h = -(-self.height // factor) * factor
        w = -(-self.width // factor) * factor
        padded = np.zeros((h, w), dtype=np.uint8)
        padded[: self.height, : self.width] = self.cells
        blocks = padded.reshape(h // factor, factor, w // factor, factor).max(axis=(1, 3))
        return OccupancyGrid(
            self.res_m * factor, self.origin, blocks.astype(np.uint8), self.keyframes, self.pose
        )

    def padded_to(self, *points: tuple[float, float], margin_m: float = 0.0) -> OccupancyGrid:
        """This grid grown with unknown cells until every point, and `margin_m` around it, lies
        inside: a walk may start or end where the map has not reached yet."""
        if not points:
            return self
        cols = [self.cell_of(x, y)[0] for x, y in points]
        rows = [self.cell_of(x, y)[1] for x, y in points]
        extra = math.ceil(margin_m / self.res_m) if margin_m > 0 else 0
        left = max(0, extra - min(cols))
        below = max(0, extra - min(rows))
        right = max(0, max(cols) + extra + 1 - self.width)
        above = max(0, max(rows) + extra + 1 - self.height)
        if not (left or below or right or above):
            return self
        grown = np.zeros((self.height + below + above, self.width + left + right), dtype=np.uint8)
        grown[below : below + self.height, left : left + self.width] = self.cells
        origin = (self.origin[0] - left * self.res_m, self.origin[1] - below * self.res_m)
        return OccupancyGrid(self.res_m, origin, grown, self.keyframes, self.pose)

    def inflated(self, radius_m: float) -> npt.NDArray[np.bool_]:
        """Every cell a body of `radius_m` cannot stand on without touching something solid: the
        occupied cells grown by the radius and one cell more, since a pose read off a grid is
        only ever good to a cell."""
        solid = self.cells == OCCUPIED
        if radius_m <= 0 or not solid.any():
            return solid.copy()
        return _grown(solid, math.ceil(radius_m / self.res_m) + 1)

    def kept_clear_m(self, radius_m: float) -> float:
        """How far from anything solid a walk planned for a body of `radius_m` keeps its middle:
        the radius, rounded up to cells, and the cell more `inflated` grows it by."""
        return (math.ceil(radius_m / self.res_m) + 1) * self.res_m

    def frontiers(self) -> list[Cell]:
        """Free cells with a side on floor nobody has seen, the edge of the grid counting as
        unseen: where a robot that wants to see more would go and look."""
        free = self.cells == FREE
        unseen = np.pad(self.cells == UNKNOWN, 1, constant_values=True)
        beside = unseen[:-2, 1:-1] | unseen[2:, 1:-1] | unseen[1:-1, :-2] | unseen[1:-1, 2:]
        rows, cols = np.nonzero(free & beside)
        return [(int(c), int(r)) for r, c in zip(rows, cols, strict=True)]

    # ── planning ────────────────────────────────────────────────────────────────────

    def plan(
        self,
        start: tuple[float, float],
        goal: tuple[float, float],
        *,
        radius_m: float,
        unknown_blocks: bool = False,
        extra_blocked: Iterable[Cell] = (),
    ) -> Plan | None:
        """The walk from `start` to `goal` for a body of `radius_m`, or None when there is none.

        A* over the eight neighbours, never cutting a corner, on a cost compared in order: first
        the steps where the body would cover floor nobody has seen, then the steps inside the
        band a wall is grown by, then the length. So a walk keeps the body's width off unseen
        floor wherever a way over seen floor does, crosses it only where none does, by as little
        as it can, and leaves a wall's band by the fewest steps it can, and no weight trades one
        against another. With `unknown_blocks` unseen floor is not crossed at all.

        Start and goal may lie in the band a wall is grown by (`inflated`), since a place is
        where the robot once stood and a robot can stand closer to a wall than the band allows.
        So a walk may leave the band from the start, and enter it to reach the goal, coming no
        nearer to anything solid than each stands (`_escape`), and nothing solid is ever
        walkable.
        `extra_blocked` are cells a caller has its own reason to avoid, in this grid's cells."""
        g = self.padded_to(start, goal, margin_m=radius_m + self.res_m)
        if g is not self:
            shift = (
                round((self.origin[0] - g.origin[0]) / g.res_m),
                round((self.origin[1] - g.origin[1]) / g.res_m),
            )
            extra_blocked = [(c + shift[0], r + shift[1]) for c, r in extra_blocked]
        solid = g.cells == OCCUPIED
        unseen = g.cells == UNKNOWN
        blocked = g.inflated(radius_m)
        band = blocked & ~solid
        for col, row in extra_blocked:
            if g.inside((col, row)):
                blocked[row, col] = True
                band[row, col] = False
        if unknown_blocks:
            blocked |= unseen
            band &= ~unseen
        s, t = g.cell_of(*start), g.cell_of(*goal)
        if solid[t[1], t[0]] or solid[s[1], s[0]]:
            return None
        passable = ~blocked
        for end in (s, t):
            passable |= _escape(end, band, solid, math.ceil(radius_m / g.res_m) + 1)
        if not passable[t[1], t[0]]:
            return None
        # a step is on unseen floor when any of the floor the body covers there is
        covered = _grown(unseen, math.ceil(radius_m / g.res_m))
        criteria = (covered, band & passable)
        cells = _astar(passable, criteria, s, t)
        if cells is None:
            return None
        return g._as_plan(cells, start, goal, passable, unseen, criteria)

    def _as_plan(
        self,
        cells: list[Cell],
        start: tuple[float, float],
        goal: tuple[float, float],
        passable: npt.NDArray[np.bool_],
        unseen: npt.NDArray[np.bool_],
        criteria: Sequence[npt.NDArray[np.bool_]],
    ) -> Plan:
        corners = _line_of_sight(cells, passable, criteria)
        points = [self.center_of(c) for c in corners[1:-1]] + [goal]
        length = 0.0
        last = start
        for point in points:
            length += math.dist(last, point)
            last = point
        unseen_m = sum(
            math.dist(cells[i - 1], cells[i]) * self.res_m
            for i in range(1, len(cells))
            if unseen[cells[i][1], cells[i][0]]
        )
        return Plan(tuple(points), length, unseen_m)

    # ── text ────────────────────────────────────────────────────────────────────────

    def render_text(
        self,
        *,
        cells_across: int,
        robot: Pose2D | None = None,
        places: Sequence[tuple[str, float, float]] = (),
    ) -> str:
        """The grid as lines a terminal or a model reads: `cells_across` columns at most, the top
        line the highest y, the robot an arrow pointing its heading, and each place a letter with
        its name below. x is right and y is up, and the map has no north."""
        factor = max(1, math.ceil(self.width / max(1, cells_across)))
        view = self.downsample(factor)
        rows = [[RENDER[int(v)] for v in row] for row in view.cells]
        named: list[str] = []
        for i, (name, x, y) in enumerate(places):
            letter = chr(ord("A") + i) if i < 26 else "*"
            named.append(f"{letter} {name}")
            col, row = view.cell_of(x, y)
            if view.inside((col, row)):
                rows[row][col] = letter
        if robot is not None:
            col, row = view.cell_of(robot.x, robot.y)
            if view.inside((col, row)):
                rows[row][col] = _arrow(robot.yaw)
        cm = view.res_m * 100
        lines = [
            f"map: {view.width} x {view.height} cells of {cm:.0f} cm, x right, y up "
            "(the map has no north)",
            LEGEND,
        ]
        lines.extend("".join(row) for row in reversed(rows))
        if named:
            lines.append("places: " + " · ".join(named))
        return "\n".join(lines)


def _arrow(yaw: float) -> str:
    """`>` for a heading along x, `^` along y, and so on round."""
    quarter = round(math.degrees(yaw) / 90.0) % 4
    return ">^<v"[quarter]


def _grown(mask: npt.NDArray[np.bool_], r: int) -> npt.NDArray[np.bool_]:
    """`mask` grown by a disc of `r` cells: every cell within `r` of one it holds."""
    out = mask.copy()
    if r <= 0 or not mask.any():
        return out
    h, w = mask.shape
    for dr in range(-r, r + 1):
        for dc in range(-r, r + 1):
            if dr * dr + dc * dc > r * r or not (dr or dc):
                continue
            src = mask[max(0, -dr) : h - max(0, dr), max(0, -dc) : w - max(0, dc)]
            out[max(0, dr) : h - max(0, -dr), max(0, dc) : w - max(0, -dc)] |= src
    return out


def _escape(
    cell: Cell, band: npt.NDArray[np.bool_], solid: npt.NDArray[np.bool_], reach: int
) -> npt.NDArray[np.bool_]:
    """The cells of `band` a walk reaches from `cell` without coming any nearer to anything solid
    than `cell` stands, or nothing when `cell` stands outside the band: the way out of the band
    for a walk that starts there, and the way in for one that ends there. It may run beside a
    wall at the end's own distance from it, along a passage's middle and round a corner, and
    never nearer: the whole band was walkable, and a walk ran the length of a wall a cell off it.
    `reach` is how far the band reaches from what is solid, in cells, so every cell of it has
    something solid within that."""
    out = np.zeros_like(band)
    col, row = cell
    if not band[row, col]:
        return out
    h, w = band.shape
    offsets = sorted(
        (math.hypot(dc, dr), dc, dr)
        for dr in range(-reach, reach + 1)
        for dc in range(-reach, reach + 1)
        if (dc or dr) and dc * dc + dr * dr <= reach * reach
    )
    known: dict[Cell, float] = {}

    def clearance(c: int, r: int) -> float:
        if (c, r) not in known:
            known[(c, r)] = next(
                (
                    d
                    for d, dc, dr in offsets
                    if 0 <= c + dc < w and 0 <= r + dr < h and solid[r + dr, c + dc]
                ),
                math.inf,
            )
        return known[(c, r)]

    least = clearance(col, row)
    stack = [cell]
    out[row, col] = True
    while stack:
        c, r = stack.pop()
        for dc, dr, _step in _STEPS:
            nc, nr = c + dc, r + dr
            if not (0 <= nc < w and 0 <= nr < h) or out[nr, nc] or not band[nr, nc]:
                continue
            if clearance(nc, nr) >= least - 1e-9:
                out[nr, nc] = True
                stack.append((nc, nr))
    return out


def _astar(
    passable: npt.NDArray[np.bool_],
    criteria: Sequence[npt.NDArray[np.bool_]],
    start: Cell,
    goal: Cell,
) -> list[Cell] | None:
    """Cells from `start` to `goal`, both included, on a cost compared in order: the cells of
    each of `criteria` entered, then the length."""
    h, w = passable.shape
    gx, gy = goal

    def heuristic(cell: Cell) -> float:
        return math.hypot(cell[0] - gx, cell[1] - gy)

    zero = tuple(0 for _ in criteria)
    best: dict[Cell, tuple[tuple[int, ...], float]] = {start: (zero, 0.0)}
    came: dict[Cell, Cell] = {}
    frontier: list[tuple[tuple[int, ...], float, int, Cell, float]] = [
        (zero, heuristic(start), 0, start, 0.0)
    ]
    order = 0
    while frontier:
        counts, _, _, cell, length = heapq.heappop(frontier)
        cost = best[cell]
        if (counts, length) != cost:
            continue  # a cheaper way to this cell was found after this entry was queued
        if cell == goal:
            path = [cell]
            while path[-1] in came:
                path.append(came[path[-1]])
            return path[::-1]
        c, r = cell
        for dc, dr, step in _STEPS:
            nc, nr = c + dc, r + dr
            if not (0 <= nc < w and 0 <= nr < h) or not passable[nr, nc]:
                continue
            if dc and dr and not (passable[r, nc] and passable[nr, c]):
                continue  # never through the corner of two cells that are not both walkable
            entered = tuple(n + int(m[nr, nc]) for n, m in zip(cost[0], criteria, strict=True))
            new = (entered, cost[1] + step)
            old = best.get((nc, nr))
            if old is not None and old <= new:
                continue
            best[(nc, nr)] = new
            came[(nc, nr)] = cell
            order += 1
            heapq.heappush(
                frontier, (entered, new[1] + heuristic((nc, nr)), order, (nc, nr), new[1])
            )
    return None


def _crossed(a: Cell, b: Cell) -> list[Cell]:
    """Every cell the segment between the centres of `a` and `b` passes through."""
    (x0, y0), (x1, y1) = a, b
    dx, dy = x1 - x0, y1 - y0
    n = max(abs(dx), abs(dy))
    if n == 0:
        return [a]
    out: list[Cell] = []
    seen: set[Cell] = set()
    steps = 4 * n
    for i in range(steps + 1):
        t = i / steps
        cell = (math.floor(x0 + 0.5 + dx * t), math.floor(y0 + 0.5 + dy * t))
        if cell not in seen:
            seen.add(cell)
            out.append(cell)
    return out


def _line_of_sight(
    cells: list[Cell],
    passable: npt.NDArray[np.bool_],
    criteria: Sequence[npt.NDArray[np.bool_]],
) -> list[Cell]:
    """The path's corners: from each kept cell, the farthest later cell reachable in a straight
    line over walkable cells entering no more cells of each of `criteria` than the path it
    replaces."""
    if len(cells) <= 2:
        return list(cells)
    before = [[0] for _ in criteria]
    for cell in cells[1:]:
        for counted, mask in zip(before, criteria, strict=True):
            counted.append(counted[-1] + int(mask[cell[1], cell[0]]))
    kept = [cells[0]]
    i = 0
    while i < len(cells) - 1:
        j = len(cells) - 1
        while j > i + 1:
            line = _crossed(cells[i], cells[j])
            if all(passable[r, c] for c, r in line) and all(
                sum(int(mask[r, c]) for c, r in line[1:]) <= counted[j] - counted[i]
                for counted, mask in zip(before, criteria, strict=True)
            ):
                break
            j -= 1
        kept.append(cells[j])
        i = j
    return kept
