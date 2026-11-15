"""A cart that does exactly what the test tells it to.

`XLerobotMock` is a holonomic base and two arms in memory. It reproduces three upstream
behaviours on purpose, because they are the ones an adapter gets wrong:

- **Any arm or gripper command also zeroes the base**, because upstream writes the wheels on
  every action (`upstream_api.SEND_ACTION_ALWAYS_WRITES_BASE`), so a `move_joints` issued
  mid-drive stops the cart.
- **Silence stops the base after 500 ms and leaves the arms holding**
  (`upstream_api.WATCHDOG_ACTION`), which is the host's watchdog and the only safety authority
  this robot has.
- **Only the three-omniwheel base can strafe**; the other variants take `y.vel` and do nothing
  with it.

The camera is a synthetic frame with an orange disc at a fixed spot, so `observe`, `search_scan`
in turn mode and `go_to` run offline all the way through the executor's gates. Build it with
`camera=False` to get the cart upstream actually ships, which has every camera commented out.
"""

from __future__ import annotations

import asyncio
import math
from collections.abc import AsyncIterator
from typing import Any

from PIL import Image, ImageDraw

from roboglot.perception.palette import BALL, FLOOR, HORIZON, SKY, focal_px
from roboglot.transport.base import Ack, DuckState, HeartbeatError, Intent
from roboglot_xlerobot.verbs import ARMS, GRIPPER_CLOSED, GRIPPER_OPEN, JOINTS, joint_range

WATCHDOG_S = 0.5
"""Upstream's own `watchdog_timeout_ms`, in seconds."""
TICK_S = 0.05
MOCK_CAM_HEIGHT_M = 0.35
MOCK_BALL_R = 0.05
FOV_DEG = 45.0
REST = dict.fromkeys(JOINTS, 0.0) | {f"{arm}_arm_gripper": GRIPPER_OPEN for arm in ARMS}


class XLerobotMock:
    name = "mock"
    mobility = "wheeled"

    def __init__(
        self,
        *,
        camera: bool = True,
        camera_key: str | None = "head",
        variant: str = "omni3",
        ball_xy: tuple[float, float] | None = (1.5, 0.5),
        frame_size: int = 128,
        fail_heartbeat_after: int | None = None,
        refuse_kinds: set[str] | None = None,
    ) -> None:
        self.camera_available = camera
        self.camera_key = camera_key if camera else None
        self.variant = variant
        self.host = "mock"
        self.ball_xy = ball_xy
        self._frame_size = frame_size
        self._fail_after = fail_heartbeat_after
        self._refuse = refuse_kinds or set()
        self.x = 0.0
        self.y = 0.0
        self.theta = 0.0
        self.vx = 0.0
        self.vy = 0.0
        self.wz = 0.0
        self._t = 0.0
        self._last_cmd_t: float | None = None
        self.joints: dict[str, float] = dict(REST)
        self.holding: dict[str, bool] = dict.fromkeys(ARMS, False)
        self.intents: list[Intent] = []
        self.actions: list[dict[str, float]] = []
        self.twists: list[dict[str, float]] = []
        self.stops = 0
        self.heartbeats = 0
        self.connected = False
        self.distance_m = 0.0
        self.stale_ms: float = 0.0
        """Set a test to something over 500 to make `link_fresh` refuse."""

    # ── motion ──────────────────────────────────────────────────────────────────────

    def _integrate(self, dt: float) -> None:
        if self._last_cmd_t is not None and self._t - self._last_cmd_t > WATCHDOG_S:
            # the host's watchdog: it stops the base and leaves the arms holding
            self.vx = self.vy = self.wz = 0.0
        c, s = math.cos(self.theta), math.sin(self.theta)
        dx = (self.vx * c - self.vy * s) * dt
        dy = (self.vx * s + self.vy * c) * dt
        self.x += dx
        self.y += dy
        self.distance_m += math.hypot(dx, dy)
        self.theta = math.atan2(
            math.sin(self.theta + self.wz * dt), math.cos(self.theta + self.wz * dt)
        )

    def ball_relative(self) -> tuple[float, float] | None:
        """(distance, bearing in degrees, + = left) of the ball from the cart, or None."""
        if self.ball_xy is None:
            return None
        dx, dy = self.ball_xy[0] - self.x, self.ball_xy[1] - self.y
        bearing = math.degrees(math.atan2(dy, dx) - self.theta)
        return math.hypot(dx, dy), (bearing + 180.0) % 360.0 - 180.0

    def _halt_base(self) -> None:
        """What upstream does on every action that carries no velocity keys."""
        self.vx = self.vy = self.wz = 0.0
        self.twists.append({"vx": 0.0, "vy": 0.0, "wz": 0.0})

    def _goto(self, goals: dict[str, float]) -> None:
        for joint, goal in goals.items():
            if joint in JOINTS:
                lo, hi = joint_range(joint)
                self.joints[joint] = min(hi, max(lo, float(goal)))
        self.actions.append(dict(goals))

    # ── protocol ────────────────────────────────────────────────────────────────────

    async def connect(self) -> None:
        self.connected = True

    async def close(self) -> None:
        self.connected = False

    async def get_frame(self) -> Image.Image | None:
        if not self.camera_available:
            return None
        size = self._frame_size
        img = Image.new("RGB", (size, size), SKY)
        draw = ImageDraw.Draw(img)
        horizon = int(size * HORIZON)
        draw.rectangle([0, horizon, size, size], fill=FLOOR)
        rel = self.ball_relative()
        if rel is None:
            return img
        dist, bearing = rel
        if abs(bearing) > FOV_DEG or dist < 0.05:
            return img
        f = focal_px(size)
        cx = size / 2 - math.tan(math.radians(bearing)) * f
        ground_y = horizon + f * MOCK_CAM_HEIGHT_M / dist
        r = f * MOCK_BALL_R / dist
        draw.ellipse([cx - r, ground_y - 2 * r, cx + r, ground_y], fill=BALL)
        return img

    async def get_state(self) -> DuckState:
        return DuckState(
            t=self._t,
            policy="idle",
            posture="unknown",
            fallen=False,
            # no BMS in the bill of materials
            battery_percent=None,
            # No pose. Neither robot has odometry: both wires carry velocities and never a
            # position, so the real backends report None and the mock has to as well. A mock
            # that is easier than the robot is a task that passes here and fails there.
            x=None,
            y=None,
            theta=None,
            holding=any(self.holding.values()),
            extras={
                "joints": {k: round(v, 1) for k, v in self.joints.items()},
                "holding": dict(self.holding),
                "twist": {"vx": self.vx, "vy": self.vy, "wz": self.wz},
                "distance_m": round(self.distance_m, 3),
                "variant": self.variant,
                "stale_ms": self.stale_ms,
                "stale_limit_ms": WATCHDOG_S * 1000.0,
            },
        )

    async def send_intent(self, intent: Intent) -> Ack:
        self.intents.append(intent)
        if intent.kind in self._refuse:
            return Ack(accepted=False, reason=f"mock refuses {intent.kind}")
        p = intent.params
        match intent.kind:
            case "move":
                vy = float(p.get("vy", 0.0))
                if self.variant != "omni3" and vy:
                    # upstream accepts y.vel on these bases and does nothing with it
                    vy = 0.0
                self.vx, self.vy, self.wz = float(p.get("vx", 0.0)), vy, float(p.get("wz", 0.0))
                self._last_cmd_t = self._t
                self.twists.append({"vx": self.vx, "vy": self.vy, "wz": self.wz})
            case "stop":
                self._halt_base()
            case "joint":
                self._goto({str(k): float(v) for k, v in dict(p.get("positions", {})).items()})
                self._halt_base()  # upstream writes the wheels on every action
            case "gripper":
                open_ = bool(p.get("open", True))
                sides = list(ARMS) if p.get("side") == "both" else [str(p.get("side", "right"))]
                for side in sides:
                    if side in ARMS:
                        goal = GRIPPER_OPEN if open_ else GRIPPER_CLOSED
                        self._goto({f"{side}_arm_gripper": goal})
                        # commanded, never sensed: nothing reports grip force
                        self.holding[side] = not open_
                self._halt_base()
            case "enable":
                if not p.get("on", True):
                    return Ack(accepted=False, reason="Roboglot never limps a robot")
            case _:
                return Ack(accepted=False, reason=f"an XLeRobot cannot {intent.kind}")
        return Ack()

    async def subscribe(self, topic: str) -> AsyncIterator[dict[str, Any]]:  # type: ignore[override]
        while self.connected:
            await self.sleep(0.5)
            yield {"topic": topic, **(await self.get_state()).model_dump()}

    async def heartbeat(self) -> None:
        self.heartbeats += 1
        if self._fail_after is not None and self.heartbeats > self._fail_after:
            raise HeartbeatError("mock xlerobot heartbeat failure (scripted)")

    async def stop(self) -> None:
        self.stops += 1
        await self.send_intent(Intent.stop())

    def now(self) -> float:
        return self._t

    async def sleep(self, seconds: float) -> None:
        remaining = seconds
        while remaining > 1e-9:
            dt = min(TICK_S, remaining)
            self._t += dt
            self._integrate(dt)
            remaining -= dt
        await asyncio.sleep(0)  # yield so the heartbeat task gets a turn

    # ── test helpers ────────────────────────────────────────────────────────────────

    def intents_of(self, kind: str) -> list[Intent]:
        return [i for i in self.intents if i.kind == kind]
