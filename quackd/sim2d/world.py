"""A top-down toy world with one or more ducks, a ball, a person, and walls.

This is a cartoon on purpose. It exists to test the agent loop — search, approach, act,
verify — not physics. What it *does* take seriously: determinism under a seed, a per-duck
deadman that zeroes velocity when `move` intents stop (mirroring upstream `robotd`), and
enough geometry that a kick only connects when the ball is actually in front of that duck.

Multi-duck rules that keep single-duck runs bit-identical: duck 0, the ball and the person
are spawned in exactly the pre-flock RNG order, and duck 0's random stream IS the world's;
extra ducks are placed afterwards and carry their own seeded streams, so concurrent flock
members can never perturb each other's noise.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Literal

import numpy as np

from quackd.space.room import Room, free_spot, spawn

DT = 0.05  # 20 Hz
ARENA_HALF = 1.0  # metres; the arena is [-1, 1]²
DUCK_R = 0.08
BALL_R = 0.05
PERSON_R = 0.12
MAX_VX = 0.3
MAX_VY = 0.2
MAX_WZ = 1.5
DEADMAN_S = 0.3
KICK_RANGE_M = 0.30
KICK_CONE_DEG = 35.0
KICK_SPEED = 1.2
BALL_FRICTION = 1.0  # m/s² deceleration
GRAB_RANGE_M = 0.18
GRAB_CONE_DEG = 30.0
GRAB_SUCCESS_P = 0.6  # open-loop scoop: deliberately unreliable (fetch is experimental)
HEAD_YAW_LIMIT = math.radians(60)

MAX_DUCKS = 4
DUCK_MIN_SPAWN_SEP = 0.35  # pairwise, metres

COLORWAYS = ("cream", "sky", "lavender", "graphite")

Posture = Literal["standing", "sitting", "fallen"]


@dataclass
class Duck:
    x: float = 0.0
    y: float = 0.0
    theta: float = 0.0
    head_yaw: float = 0.0
    posture: Posture = "standing"
    holding: bool = False
    r: float = DUCK_R
    cmd: tuple[float, float, float] = (0.0, 0.0, 0.0)
    cmd_age: float = 0.0
    kicks: int = 0
    kicks_connected: int = 0
    colorway: str = "cream"
    bumps: int = 0
    """Ticks this duck walked into a room's wall or furniture and was held where it was."""
    rng: np.random.Generator = field(
        default_factory=lambda: np.random.default_rng(0), repr=False, compare=False
    )
    """Per-duck noise stream, reassigned by World.__post_init__ (duck 0 shares world.rng)."""

    @property
    def moving(self) -> bool:
        return any(abs(c) > 1e-6 for c in self.cmd)


@dataclass
class Ball:
    x: float
    y: float
    vx: float = 0.0
    vy: float = 0.0
    r: float = BALL_R
    start_x: float = 0.0
    start_y: float = 0.0
    present: bool = True


@dataclass
class Person:
    x: float
    y: float
    r: float = PERSON_R
    label: str = "person"


@dataclass
class World:
    seed: int = 0
    person: bool = True
    n_ducks: int = 1
    t: float = 0.0
    ducks: list[Duck] = field(default_factory=list)
    ball: Ball = field(default_factory=lambda: Ball(0.5, 0.5))
    people: list[Person] = field(default_factory=list)
    kick_origin: tuple[float, float] | None = None
    kicks: int = 0
    kicks_connected: int = 0
    quacks: list[tuple[float, int, str, str | None]] = field(default_factory=list)
    steps: int = 0
    room: Room | None = None
    """Walls and furniture inside bounds of their own (`quackd.space.room`), or None for the 2 m
    arena, which is every world there was before rooms, unchanged."""

    def __post_init__(self) -> None:
        if not 1 <= self.n_ducks <= MAX_DUCKS:
            raise ValueError(f"n_ducks must be 1..{MAX_DUCKS}, got {self.n_ducks}")
        self.rng = np.random.default_rng(self.seed)
        if self.room is not None:
            self._spawn_in(self.room)
            return
        # duck 0, ball and person spawn in EXACTLY the pre-flock RNG order (determinism)
        duck0 = Duck(
            x=float(self.rng.uniform(-0.3, 0.3)),
            y=float(self.rng.uniform(-0.3, 0.3)),
            theta=float(self.rng.uniform(-math.pi, math.pi)),
        )
        duck0.rng = self.rng  # same object: single-duck draw sequence is bit-identical
        ax, ay = duck0.x, duck0.y
        for _ in range(1000):
            bx, by = self.rng.uniform(-0.75, 0.75, size=2)
            if math.hypot(bx - ax, by - ay) >= 0.5:
                break
        self.ball = Ball(float(bx), float(by), start_x=float(bx), start_y=float(by))
        self.people = []
        if self.person:
            for _ in range(1000):
                px, py = self.rng.uniform(-0.8, 0.8, size=2)
                if (
                    math.hypot(px - ax, py - ay) >= 0.6
                    and math.hypot(px - self.ball.x, py - self.ball.y) >= 0.4
                ):
                    break
            self.people.append(Person(float(px), float(py)))
        self.ducks = [duck0]
        for i in range(1, self.n_ducks):
            extra = self._spawn_extra_duck(i)
            self.ducks.append(extra)
        for i, d in enumerate(self.ducks):
            d.colorway = COLORWAYS[i % len(COLORWAYS)]

    def _spawn_in(self, room: Room) -> None:
        """Where a room puts everything (`quackd.space.room.spawn`): its own start, spot and
        person where it names them, and free floor drawn from the room's stream where it does
        not, the other ducks of a flock after the duck and the ball."""
        placed, draw = spawn(room, self.seed, duck_r=DUCK_R, ball_r=BALL_R)
        x, y, theta = placed.duck
        duck0 = Duck(x=x, y=y, theta=theta)
        duck0.rng = self.rng  # the world's stream drives its motion noise, as without a room
        bx, by = placed.ball
        self.ball = Ball(bx, by, start_x=bx, start_y=by)
        self.people = [Person(*room.person)] if self.person and room.person is not None else []
        self.ducks = [duck0]
        for index in range(1, self.n_ducks):
            own = np.random.default_rng([self.seed, index])
            avoid = [(d.x, d.y, DUCK_MIN_SPAWN_SEP) for d in self.ducks] + [(bx, by, 0.5)]
            ex, ey = free_spot(room, draw, DUCK_R, avoid=avoid)
            duck = Duck(x=ex, y=ey, theta=float(own.uniform(-math.pi, math.pi)))
            duck.rng = own
            self.ducks.append(duck)
        for i, d in enumerate(self.ducks):
            d.colorway = COLORWAYS[i % len(COLORWAYS)]

    def _spawn_extra_duck(self, index: int) -> Duck:
        rng = np.random.default_rng([self.seed, index])
        x = y = 0.0
        for _ in range(1000):
            x, y = (float(v) for v in rng.uniform(-0.7, 0.7, size=2))
            if (
                all(math.hypot(x - d.x, y - d.y) >= DUCK_MIN_SPAWN_SEP for d in self.ducks)
                and math.hypot(x - self.ball.x, y - self.ball.y) >= 0.5
                and all(math.hypot(x - p.x, y - p.y) >= 0.4 for p in self.people)
            ):
                break
        duck = Duck(x=x, y=y, theta=float(rng.uniform(-math.pi, math.pi)))
        duck.rng = rng  # own stream: task wake order cannot perturb another duck's noise
        return duck

    # ── back-compat: the single-duck API delegates to duck 0 ────────────────────────

    @property
    def duck(self) -> Duck:
        return self.ducks[0]

    @property
    def cmd(self) -> tuple[float, float, float]:
        return self.ducks[0].cmd

    @property
    def cmd_age(self) -> float:
        return self.ducks[0].cmd_age

    @property
    def moving(self) -> bool:
        return self.ducks[0].moving

    # ── geometry ────────────────────────────────────────────────────────────────────

    def relative_to(
        self, x: float, y: float, *, ox: float, oy: float, heading: float
    ) -> tuple[float, float]:
        """(distance, bearing_rad) of a point from an observer at (ox, oy) facing `heading`."""
        dx, dy = x - ox, y - oy
        dist = math.hypot(dx, dy)
        bearing = math.atan2(dy, dx) - heading
        return dist, math.atan2(math.sin(bearing), math.cos(bearing))

    def relative(
        self, x: float, y: float, *, camera: bool = False, duck_index: int = 0
    ) -> tuple[float, float]:
        """(distance, bearing_rad) of a point in a duck's body frame (or camera frame)."""
        d = self.ducks[duck_index]
        heading = d.theta + (d.head_yaw if camera else 0.0)
        return self.relative_to(x, y, ox=d.x, oy=d.y, heading=heading)

    @property
    def ball_displacement_m(self) -> float:
        return math.hypot(self.ball.x - self.ball.start_x, self.ball.y - self.ball.start_y)

    @property
    def last_kick_ball_moved_m(self) -> float | None:
        if self.kick_origin is None:
            return None
        return math.hypot(self.ball.x - self.kick_origin[0], self.ball.y - self.kick_origin[1])

    # ── intents (per duck; index 0 keeps the historic single-duck behaviour) ────────

    def set_velocity(self, vx: float, vy: float, wz: float, duck_index: int = 0) -> None:
        d = self.ducks[duck_index]
        d.cmd = (
            float(np.clip(vx, -MAX_VX, MAX_VX)),
            float(np.clip(vy, -MAX_VY, MAX_VY)),
            float(np.clip(wz, -MAX_WZ, MAX_WZ)),
        )
        d.cmd_age = 0.0

    def stop(self, duck_index: int = 0) -> None:
        d = self.ducks[duck_index]
        d.cmd = (0.0, 0.0, 0.0)
        d.cmd_age = 0.0

    def stop_all(self) -> None:
        for i in range(len(self.ducks)):
            self.stop(i)

    def look(self, x: float, y: float, _z: float = 0.0, duck_index: int = 0) -> bool:
        """Point a duck's camera at a trunk-frame point. Returns True if clamped."""
        d = self.ducks[duck_index]
        yaw = math.atan2(y, x)
        clamped = abs(yaw) > HEAD_YAW_LIMIT
        d.head_yaw = float(np.clip(yaw, -HEAD_YAW_LIMIT, HEAD_YAW_LIMIT))
        return clamped

    def kick(self, leg: str = "right", duck_index: int = 0) -> bool:
        """Kick along that duck's heading. Connects only if the ball is close and ahead."""
        d = self.ducks[duck_index]
        self.kicks += 1
        d.kicks += 1
        if not self.ball.present or d.posture != "standing":
            return False
        dist, bearing = self.relative(self.ball.x, self.ball.y, duck_index=duck_index)
        if dist > KICK_RANGE_M or abs(math.degrees(bearing)) > KICK_CONE_DEG:
            self.kick_origin = (self.ball.x, self.ball.y)  # a miss moves nothing
            return False
        self.kick_origin = (self.ball.x, self.ball.y)
        self.kicks_connected += 1
        d.kicks_connected += 1
        skew = math.radians(d.rng.normal(0.0, 6.0)) + (0.05 if leg == "left" else -0.05)
        ang = d.theta + skew
        self.ball.vx = KICK_SPEED * math.cos(ang)
        self.ball.vy = KICK_SPEED * math.sin(ang)
        return True

    def ground_pick(self, duck_index: int = 0) -> bool:
        d = self.ducks[duck_index]
        if d.holding or not self.ball.present or d.posture != "standing":
            return False
        dist, bearing = self.relative(self.ball.x, self.ball.y, duck_index=duck_index)
        if dist > GRAB_RANGE_M or abs(math.degrees(bearing)) > GRAB_CONE_DEG:
            return False
        if d.rng.random() > GRAB_SUCCESS_P:
            # the scoop nudges the ball away — realistic and annoying
            self.ball.vx = 0.2 * math.cos(d.theta + 0.6)
            self.ball.vy = 0.2 * math.sin(d.theta + 0.6)
            return False
        d.holding = True
        self.ball.present = False
        return True

    def sit_toggle(self, duck_index: int = 0) -> Posture:
        d = self.ducks[duck_index]
        if d.posture == "fallen":
            return d.posture
        d.posture = "sitting" if d.posture == "standing" else "standing"
        self.stop(duck_index)
        return d.posture

    def enable(self, duck_index: int = 0) -> None:
        d = self.ducks[duck_index]
        if d.posture == "fallen":
            d.posture = "standing"

    def sound(self, tag: str, text: str | None, duck_index: int = 0) -> None:
        self.quacks.append((self.t, duck_index, tag, text))

    # ── physics ─────────────────────────────────────────────────────────────────────

    def _integrate_duck(self, d: Duck, dt: float) -> None:
        d.cmd_age += dt
        vx, vy, wz = d.cmd
        if d.cmd_age > DEADMAN_S and d.moving:
            d.cmd = (0.0, 0.0, 0.0)  # upstream's deadman: stop is not limp
            vx = vy = wz = 0.0
        if d.posture != "standing":
            vx = vy = wz = 0.0
        if vx or vy or wz:
            noise = d.rng.normal(0.0, 0.02, size=3)
            vx *= 1 + noise[0]
            vy *= 1 + noise[1]
            wz *= 1 + noise[2]
            d.theta = math.atan2(math.sin(d.theta + wz * dt), math.cos(d.theta + wz * dt))
            x = d.x + (vx * math.cos(d.theta) - vy * math.sin(d.theta)) * dt
            y = d.y + (vx * math.sin(d.theta) + vy * math.cos(d.theta)) * dt
            if self.room is None:
                lim = ARENA_HALF - d.r
                d.x = float(np.clip(x, -lim, lim))
                d.y = float(np.clip(y, -lim, lim))
            elif self.room.blocked(x, y, d.r):
                d.bumps += 1  # a wall holds a walking duck where it is, turned as it turned
            else:
                d.x, d.y = float(x), float(y)

    def _resolve_duck_collisions(self) -> None:
        """Ducks are solid: overlapping pairs are pushed apart symmetrically."""
        for i in range(len(self.ducks)):
            for j in range(i + 1, len(self.ducks)):
                a, b = self.ducks[i], self.ducks[j]
                dx, dy = b.x - a.x, b.y - a.y
                dist = math.hypot(dx, dy)
                min_dist = a.r + b.r
                if dist >= min_dist:
                    continue
                if dist < 1e-6:
                    dx, dy, dist = 1.0, 0.0, 1.0
                nx, ny = dx / dist, dy / dist
                push = (min_dist - dist) / 2
                if self.room is not None:
                    # pushed only where there is floor to be pushed onto
                    for duck, sign in ((a, -1.0), (b, 1.0)):
                        px, py = duck.x + sign * nx * push, duck.y + sign * ny * push
                        if not self.room.blocked(px, py, duck.r):
                            duck.x, duck.y = px, py
                    continue
                lim = ARENA_HALF - DUCK_R
                a.x = float(np.clip(a.x - nx * push, -lim, lim))
                a.y = float(np.clip(a.y - ny * push, -lim, lim))
                b.x = float(np.clip(b.x + nx * push, -lim, lim))
                b.y = float(np.clip(b.y + ny * push, -lim, lim))

    def step(self, dt: float = DT) -> None:
        for d in self.ducks:  # index order: deterministic
            self._integrate_duck(d, dt)
        if len(self.ducks) > 1:
            self._resolve_duck_collisions()

        b = self.ball
        if b.present:
            speed = math.hypot(b.vx, b.vy)
            if speed > 0:
                decel = min(speed, BALL_FRICTION * dt)
                b.vx *= (speed - decel) / speed
                b.vy *= (speed - decel) / speed
                if self.room is not None:
                    x, y = b.x + b.vx * dt, b.y + b.vy * dt
                    if self.room.blocked(x, y, b.r):
                        b.vx, b.vy = -0.5 * b.vx, -0.5 * b.vy  # off whatever it met
                    else:
                        b.x, b.y = float(x), float(y)
                else:
                    b.x += b.vx * dt
                    b.y += b.vy * dt
                    lim = ARENA_HALF - b.r
                    if abs(b.x) > lim:
                        b.x = float(np.clip(b.x, -lim, lim))
                        b.vx = -0.5 * b.vx
                    if abs(b.y) > lim:
                        b.y = float(np.clip(b.y, -lim, lim))
                        b.vy = -0.5 * b.vy
            # contact push: any duck's body shoves the ball (index order)
            for d in self.ducks:
                dx, dy = b.x - d.x, b.y - d.y
                dist = math.hypot(dx, dy)
                min_dist = d.r + b.r
                if dist < min_dist:
                    if dist < 1e-6:
                        dx, dy, dist = math.cos(d.theta), math.sin(d.theta), 1.0
                    nx, ny = dx / dist, dy / dist
                    if self.room is not None and self.room.blocked(
                        d.x + nx * min_dist, d.y + ny * min_dist, b.r
                    ):
                        continue  # pinned against a wall, it stays where it is
                    b.x = d.x + nx * min_dist
                    b.y = d.y + ny * min_dist
                    b.vx, b.vy = 0.3 * nx, 0.3 * ny
        self.t += dt
        self.steps += 1

    # ── telemetry ───────────────────────────────────────────────────────────────────

    def snapshot(self, duck_index: int = 0) -> dict[str, Any]:
        d = self.ducks[duck_index]
        snap: dict[str, Any] = {
            "sim_time": round(self.t, 3),
            "ball": (
                {"x": round(self.ball.x, 3), "y": round(self.ball.y, 3), "present": True}
                if self.ball.present
                else {"present": False}
            ),
            "ball_displacement_m": round(self.ball_displacement_m, 3),
            "last_kick_ball_moved_m": (
                None
                if self.last_kick_ball_moved_m is None
                else round(self.last_kick_ball_moved_m, 3)
            ),
            "kicks": self.kicks,
            "quacks": len(self.quacks),
            "head_yaw_deg": round(math.degrees(d.head_yaw), 1),
            "people": [{"x": round(p.x, 3), "y": round(p.y, 3)} for p in self.people],
        }
        if len(self.ducks) > 1:
            snap["ducks"] = self._ducks_snapshot()
        if self.room is not None:
            snap["room"] = self.room.name
        return snap

    def _ducks_snapshot(self) -> list[dict[str, Any]]:
        return [
            {
                "index": i,
                "colorway": duck.colorway,
                "x": round(duck.x, 3),
                "y": round(duck.y, 3),
                "theta": round(duck.theta, 3),
                "posture": duck.posture,
                "kicks_connected": duck.kicks_connected,
            }
            for i, duck in enumerate(self.ducks)
        ]
