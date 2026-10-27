"""A room for the simulators: walls, boxes, where the duck starts and where the ball may lie.

A room is what makes a simulated map worth having. The cartoon's 2 m arena has nothing in it to
map, so a simulator given a room (`--room`) gets its walls and furniture, and the simulated map
(`quackd.space.simmap`) reveals them as the duck stops and looks. Without one, every world is
exactly what it was, to the bit.

A wall is every point within half its thickness of the segment it is written as, round at its
ends, and a box the rectangle it is written as: what the world collides with (`clearance`) and
what a ray meets (`cast`) are the one shape.

A room is YAML, a built-in by name (`quackd/space/rooms/*.yaml`) or a file:

    name: two-rooms
    size: [3.0, 2.0]            # metres across, centred on the origin: the outer walls
    walls:                      # more walls, each from (x0, y0) to (x1, y1), and a thickness
      - [0.0, -1.0, 0.0, -0.3]  # as a fifth number when it is not 4 cm
      - [0.0, 0.3, 0.0, 1.0]
    boxes:                      # furniture, each a centre and a size: cx, cy, width, depth
      - [1.0, 0.6, 0.4, 0.3]
    start: [-1.0, 0.0, 0.0]     # where the duck stands and which way it faces, or left out
    ball: {region: [0.3, -0.8, 1.4, 0.3]}   # a spot, [x, y], or a region it is drawn in
    person: [-1.2, 0.7]         # the cartoon's person, sim2d only

Errors name the file and the key, since a room is something people write by hand.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path
from typing import Any, Literal

import numpy as np
import yaml

WALL_THICKNESS_M = 0.04
"""How thick a wall is drawn and collided with, unless the room says."""

HEIGHT_M = 0.30
"""How tall a room's walls and furniture stand, for whatever draws them standing up: above a
duck's camera, so they hide what is behind them as a room's walls do."""

ROOM_STREAM = 7_003
"""A room's own random stream, beside the seed: where a room puts what it does not place itself
is drawn from it and never from a world's, so a room changes nothing a world draws for anything
else, the duck's motion noise above all."""

BUILT_IN = ("arena", "two-rooms", "hall")
"""The rooms quackd ships, by name."""


class RoomError(ValueError):
    """A room file that cannot be read, in words naming the file and the key."""


@dataclass(frozen=True)
class Wall:
    x0: float
    y0: float
    x1: float
    y1: float
    thickness: float = WALL_THICKNESS_M

    def distance(self, x: float, y: float) -> float:
        """How far (x, y) is from the wall's centre line."""
        dx, dy = self.x1 - self.x0, self.y1 - self.y0
        length2 = dx * dx + dy * dy
        along = 0.0 if length2 == 0 else ((x - self.x0) * dx + (y - self.y0) * dy) / length2
        t = max(0.0, min(1.0, along))
        return math.hypot(x - (self.x0 + t * dx), y - (self.y0 + t * dy))


@dataclass(frozen=True)
class Box:
    cx: float
    cy: float
    w: float
    d: float

    def distance(self, x: float, y: float) -> float:
        """How far (x, y) is from the box, 0 inside it."""
        ex = max(abs(x - self.cx) - self.w / 2, 0.0)
        ey = max(abs(y - self.cy) - self.d / 2, 0.0)
        return math.hypot(ex, ey)


@dataclass(frozen=True)
class Room:
    name: str
    half_w: float
    half_h: float
    walls: tuple[Wall, ...] = ()
    boxes: tuple[Box, ...] = ()
    start: tuple[float, float, float] | None = None
    ball: tuple[float, float] | None = None
    ball_region: tuple[float, float, float, float] | None = None
    person: tuple[float, float] | None = None
    source: str = field(default="", compare=False)
    """Where it was read from, for error messages."""

    @property
    def bounds(self) -> tuple[float, float, float, float]:
        """(xmin, ymin, xmax, ymax): the inside of the outer walls."""
        return (-self.half_w, -self.half_h, self.half_w, self.half_h)

    def digest(self) -> str:
        """A fingerprint of the geometry, which a simulated map's frame is named by: the same
        room is the same frame, whoever runs it."""
        shape = {
            "half": [round(self.half_w, 4), round(self.half_h, 4)],
            "walls": [
                [round(v, 4) for v in (w.x0, w.y0, w.x1, w.y1, w.thickness)] for w in self.walls
            ],
            "boxes": [[round(v, 4) for v in (b.cx, b.cy, b.w, b.d)] for b in self.boxes],
        }
        return hashlib.sha1(json.dumps(shape, sort_keys=True).encode()).hexdigest()

    def clearance(self, x: float, y: float) -> float:
        """How far (x, y) is from the nearest solid thing: an outer wall, a wall, a box. Negative
        outside the outer walls or inside a wall's thickness or a box."""
        best = min(self.half_w - abs(x), self.half_h - abs(y))
        for wall in self.walls:
            best = min(best, wall.distance(x, y) - wall.thickness / 2)
        for box in self.boxes:
            inside = box.distance(x, y)
            if inside == 0.0:
                return -min(box.w / 2 - abs(x - box.cx), box.d / 2 - abs(y - box.cy))
            best = min(best, inside)
        return best

    def blocked(self, x: float, y: float, radius: float = 0.0) -> bool:
        """Whether a disc of `radius` at (x, y) touches something solid."""
        return self.clearance(x, y) < radius

    def raycast(self, x: float, y: float, heading: float, max_range: float) -> float | None:
        """How far along `heading` from (x, y) the first solid thing is, within `max_range`, or
        None when nothing is."""
        hit = self.cast(x, y, heading, max_range)
        return None if hit is None else hit[0]

    def cast(
        self, x: float, y: float, heading: float, max_range: float
    ) -> tuple[float, Literal["wall", "box"]] | None:
        """How far along `heading` from (x, y) the first solid thing is, and whether it is a
        wall or furniture, within `max_range`, or None when nothing is: worked out against each
        wall, box and the outer walls rather than marched, since a camera casts one ray a column
        and a map one a zone every stop. Each is the shape `clearance` measures: a box its
        rectangle, and a wall every point within half its thickness of its centre line, so a ray
        that grazes a wall's rounded end meets it as a disc there would be met."""
        c, s = math.cos(heading), math.sin(heading)
        best, what = math.inf, "wall"
        if c:
            best = min(best, ((self.half_w if c > 0 else -self.half_w) - x) / c)
        if s:
            best = min(best, ((self.half_h if s > 0 else -self.half_h) - y) / s)
        for wall in self.walls:
            if (hit := _capsule_hit(wall, x, y, c, s)) is not None:
                best = min(best, hit)
        for box in self.boxes:
            hit = _slab_hit(
                (x, y),
                (c, s),
                (
                    (box.cx - box.w / 2, box.cx + box.w / 2),
                    (box.cy - box.d / 2, box.cy + box.d / 2),
                ),
            )
            if hit is not None and hit < best:
                best, what = hit, "box"
        if best > max_range:
            return None
        return best, ("box" if what == "box" else "wall")


def _slab_hit(
    origin: tuple[float, float],
    direction: tuple[float, float],
    spans: tuple[tuple[float, float], tuple[float, float]],
) -> float | None:
    """Where a ray first meets the rectangle `spans` makes along its two axes, 0 from inside it,
    or None when it misses: the slab method."""
    near, far = -math.inf, math.inf
    for o, d, (lo, hi) in zip(origin, direction, spans, strict=True):
        if d == 0:
            if not lo <= o <= hi:
                return None
            continue
        t0, t1 = sorted(((lo - o) / d, (hi - o) / d))
        near, far = max(near, t0), min(far, t1)
    if near > far or far < 0:
        return None
    return max(0.0, near)


def _capsule_hit(wall: Wall, x: float, y: float, c: float, s: float) -> float | None:
    """Where a ray from (x, y) along (c, s) first meets a wall: the rectangle along its centre
    line, half its thickness to either side, and the disc of that radius at each end."""
    half = wall.thickness / 2
    hits: list[float] = []
    ex, ey = wall.x1 - wall.x0, wall.y1 - wall.y0
    length = math.hypot(ex, ey)
    if length > 0:
        ux, uy = ex / length, ey / length  # along the wall, and its normal (-uy, ux)
        ox, oy = x - wall.x0, y - wall.y0
        side = _slab_hit(
            (ox * ux + oy * uy, -ox * uy + oy * ux),
            (c * ux + s * uy, -c * uy + s * ux),
            ((0.0, length), (-half, half)),
        )
        if side is not None:
            hits.append(side)
    for cx, cy in ((wall.x0, wall.y0), (wall.x1, wall.y1)):
        # |o + t d - centre|^2 = half^2, with |d| = 1
        ox, oy = x - cx, y - cy
        b = ox * c + oy * s
        disc = b * b - (ox * ox + oy * oy - half * half)
        if disc < 0:
            continue
        root = math.sqrt(disc)
        if -b + root >= 0:
            hits.append(max(0.0, -b - root))
    return min(hits) if hits else None


@dataclass(frozen=True)
class Spawn:
    """Where a room puts the duck, and which way it faces, and the ball."""

    duck: tuple[float, float, float]
    ball: tuple[float, float]


def spawn(
    room: Room, seed: int, *, duck_r: float, ball_r: float
) -> tuple[Spawn, np.random.Generator]:
    """Where `room` puts the duck and the ball for `seed`: its own start and spot where it names
    them, and free floor drawn from its stream where it does not, the ball half a metre or more
    from the duck. The stream comes back with them, for a world that draws more from it."""
    draw = np.random.default_rng([seed, ROOM_STREAM])
    if room.start is not None:
        duck = room.start
    else:
        x, y = free_spot(room, draw, duck_r)
        duck = (x, y, float(draw.uniform(-math.pi, math.pi)))
    if room.ball is not None:
        ball = room.ball
    else:
        ball = free_spot(
            room, draw, ball_r, region=room.ball_region, avoid=[(duck[0], duck[1], 0.5)]
        )
    return Spawn(duck, ball), draw


def free_spot(
    room: Room,
    draw: np.random.Generator,
    r: float,
    *,
    region: tuple[float, float, float, float] | None = None,
    avoid: list[tuple[float, float, float]] | tuple[()] = (),
) -> tuple[float, float]:
    """A spot a disc of `r` stands on in `room`, inside `region` when one is given, and at least
    each `avoid` entry's distance from its point, drawn from `draw`."""
    xmin, ymin, xmax, ymax = region or room.bounds
    for _ in range(10_000):
        x = float(draw.uniform(xmin + r, xmax - r))
        y = float(draw.uniform(ymin + r, ymax - r))
        clear = all(math.hypot(x - ax, y - ay) >= sep for ax, ay, sep in avoid)
        if clear and not room.blocked(x, y, r):
            return x, y
    raise RoomError(
        f"{room.source or room.name}: no free floor for something {2 * r:.2f} m wide there"
    )


def nearest_free(room: Room, x: float, y: float, r: float) -> tuple[float, float]:
    """The spot nearest (x, y) a disc of `r` stands on in `room`: (x, y) itself when it fits,
    else the first that fits on rings outward a quarter of `r` apart, each ring's points no
    further apart than that. For a body set back on its feet where it fell, which may be against
    a wall or partly in one."""
    if not room.blocked(x, y, r):
        return x, y
    step = r / 4
    reach = 2 * math.hypot(room.half_w, room.half_h)
    ring = step
    while ring <= reach:
        points = max(8, math.ceil(2 * math.pi * ring / step))
        for k in range(points):
            angle = 2 * math.pi * k / points
            cx, cy = x + ring * math.cos(angle), y + ring * math.sin(angle)
            if not room.blocked(cx, cy, r):
                return cx, cy
        ring += step
    raise RoomError(f"{room.source or room.name}: no free floor for something {2 * r:.2f} m wide")


def load_room(name_or_path: str | Path) -> Room:
    """A built-in room by name, or a room file by path."""
    text: str
    where: str
    candidate = Path(name_or_path)
    if str(name_or_path) in BUILT_IN:
        where = f"built-in room {name_or_path}"
        text = (
            resources.files("quackd.space")
            .joinpath("rooms", f"{name_or_path}.yaml")
            .read_text(encoding="utf-8")
        )
    elif candidate.suffix in (".yaml", ".yml") and candidate.is_file():
        where = str(candidate)
        text = candidate.read_text(encoding="utf-8")
    else:
        raise RoomError(
            f"no room called {str(name_or_path)!r}: a room is one of {', '.join(BUILT_IN)}, "
            "or a .yaml file"
        )
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise RoomError(f"{where}: not YAML: {e}") from None
    if not isinstance(data, dict):
        raise RoomError(f"{where}: a room is a mapping of keys, not {type(data).__name__}")
    return _room(data, where)


def _numbers(where: str, key: str, value: Any, n: int) -> list[float]:
    """`value` as `n` finite numbers, or RoomError naming the file and the key."""
    if (
        not isinstance(value, list)
        or len(value) != n
        or not all(
            isinstance(v, int | float) and not isinstance(v, bool) and math.isfinite(v)
            for v in value
        )
    ):
        raise RoomError(f"{where}: {key} is {n} numbers, not {value!r}")
    return [float(v) for v in value]


def _pair(where: str, key: str, value: Any) -> tuple[float, float]:
    a, b = _numbers(where, key, value, 2)
    return a, b


def _triple(where: str, key: str, value: Any) -> tuple[float, float, float]:
    a, b, c = _numbers(where, key, value, 3)
    return a, b, c


def _quad(where: str, key: str, value: Any) -> tuple[float, float, float, float]:
    a, b, c, d = _numbers(where, key, value, 4)
    return a, b, c, d


def _room(data: dict[str, Any], where: str) -> Room:
    unknown = set(data) - {"name", "size", "walls", "boxes", "start", "ball", "person"}
    if unknown:
        raise RoomError(f"{where}: unknown keys {sorted(unknown)}")
    name = data.get("name")
    if not isinstance(name, str) or not name:
        raise RoomError(f"{where}: name is a word")
    w, h = _pair(where, "size", data.get("size"))
    if w <= 0 or h <= 0:
        raise RoomError(f"{where}: size is two lengths above zero")
    walls = []
    for i, raw in enumerate(data.get("walls") or []):
        if isinstance(raw, list) and len(raw) == 5:
            x0, y0, x1, y1, thick = _numbers(where, f"walls[{i}]", raw, 5)
            walls.append(Wall(x0, y0, x1, y1, thick))
        else:
            walls.append(Wall(*_quad(where, f"walls[{i}]", raw)))
    boxes = [
        Box(*_quad(where, f"boxes[{i}]", raw)) for i, raw in enumerate(data.get("boxes") or [])
    ]
    start, ball, person = data.get("start"), data.get("ball"), data.get("person")
    if ball is not None and not isinstance(ball, list | dict):
        raise RoomError(f"{where}: ball is a spot, [x, y], or {{region: [xmin, ymin, xmax, ymax]}}")
    room = Room(
        name=name,
        half_w=w / 2,
        half_h=h / 2,
        walls=tuple(walls),
        boxes=tuple(boxes),
        start=_triple(where, "start", start) if start is not None else None,
        ball=_pair(where, "ball", ball) if isinstance(ball, list) else None,
        ball_region=(
            _quad(where, "ball.region", ball.get("region")) if isinstance(ball, dict) else None
        ),
        person=_pair(where, "person", person) if person is not None else None,
        source=where,
    )
    if room.start is not None and room.blocked(room.start[0], room.start[1]):
        raise RoomError(f"{where}: start is inside something solid")
    return room
