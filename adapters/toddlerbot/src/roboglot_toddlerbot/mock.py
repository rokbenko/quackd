"""A humanoid that does exactly what the test tells it to.

`ToddlerBotMock` stands in for the daemon rather than for the robot: it answers the same
intents and reports the same state, so every verb, every gate and the detector run offline.

It reproduces the three things about this body that decide whether a verb is honest:

- **A fall is terminal.** There is no get-up policy for this robot, so once it is down every
  moving verb refuses and says a human is needed. Build with `fallen=True` to test that.
- **Moves take time and report it.** `stand` and `perform` finish when `moving` goes false,
  not after a fixed sleep, because on the real robot they are slews at a bounded rate.
- **Capabilities are reported, not assumed.** Build with `walk=False` and locomotion does not
  exist; with `neck=False` the head verbs do not; with `camera=False` the camera ones do not.
"""

from __future__ import annotations

import asyncio
import math
from collections.abc import AsyncIterator
from typing import Any

from PIL import Image, ImageDraw

from roboglot.perception.palette import BALL, FLOOR, HORIZON, SKY, focal_px
from roboglot.transport.base import Ack, DuckState, HeartbeatError, Intent
from roboglot_toddlerbot.verbs import look_degrees

TICK_S = 0.05
STAND_S = 3.0
"""How long the safe-pose slew takes. At upstream's own 0.3 rad/s a wide pose is seconds."""
PERFORM_S = 6.0
NECK_YAW_LIMIT = 90.0
NECK_PITCH_LIMIT = 45.0
MOCK_CAM_HEIGHT_M = 0.5
MOCK_BALL_R = 0.05
FOV_DEG = 45.0
MOTIONS = ("hold", "kneel", "cuddle", "push_up", "crawl")


class ToddlerBotMock:
    name = "mock"
    mobility = "legged"

    def __init__(
        self,
        *,
        camera: bool = True,
        neck: bool = True,
        gripper: bool = False,
        walk: bool = True,
        calibrated: bool = True,
        fallen: bool = False,
        robot_name: str = "toddlerbot_2xc",
        ball_xy: tuple[float, float] | None = (1.2, 0.4),
        frame_size: int = 128,
        fail_heartbeat_after: int | None = None,
        refuse_kinds: set[str] | None = None,
    ) -> None:
        self.camera_available = camera
        self.neck_available = neck
        self.gripper_available = gripper
        self.walk_available = walk
        self.deadman = True
        self.calibrated = calibrated
        self.robot_name = robot_name
        self.motors = 32 if gripper else 30
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
        self.neck_yaw = 0.0
        self.neck_pitch = 0.0
        self.fallen = fallen
        self.holding: dict[str, bool] = {"left": False, "right": False}
        self._t = 0.0
        self._busy_until = 0.0
        self.motion: str | None = None
        self.stands = 0
        self.performed: list[str] = []
        self.intents: list[Intent] = []
        self.stops = 0
        self.heartbeats = 0
        self.connected = False
        self.stale_ms: float = 0.0
        self.loop_hz: float = 50.0

    # ── physics ─────────────────────────────────────────────────────────────────────

    @property
    def moving(self) -> bool:
        return self._t < self._busy_until

    def _integrate(self, dt: float) -> None:
        if self.fallen:
            self.vx = self.vy = self.wz = 0.0
            return
        c, s = math.cos(self.theta), math.sin(self.theta)
        self.x += (self.vx * c - self.vy * s) * dt
        self.y += (self.vx * s + self.vy * c) * dt
        self.theta = math.atan2(
            math.sin(self.theta + self.wz * dt), math.cos(self.theta + self.wz * dt)
        )
        if not self.moving:
            self.motion = None

    def ball_relative(self) -> tuple[float, float] | None:
        if self.ball_xy is None:
            return None
        dx, dy = self.ball_xy[0] - self.x, self.ball_xy[1] - self.y
        # MINUS the head: a target dead ahead of a head turned left is to the RIGHT of
        # the camera. open_duck/mock.py does the same, and it is easy to get backwards.
        bearing = math.degrees(math.atan2(dy, dx) - self.theta) - self.neck_yaw
        return math.hypot(dx, dy), (bearing + 180.0) % 360.0 - 180.0

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
            policy=self.motion or ("walk" if any((self.vx, self.vy, self.wz)) else "idle"),
            posture="fallen" if self.fallen else "standing",
            fallen=self.fallen,
            # nothing on this robot reports a battery to Python
            battery_percent=None,
            # no odometry on hardware, so the mock does not offer one either
            x=None,
            y=None,
            theta=None,
            holding=any(self.holding.values()),
            extras={
                "neck": {
                    "yaw_deg": round(self.neck_yaw, 1),
                    "pitch_deg": round(self.neck_pitch, 1),
                },
                # what core's gaze sweep centres on, spelled the way every gaze body
                # in this repository spells it
                "head_yaw_deg": round(self.neck_yaw, 1),
                "holding": dict(self.holding),
                "robot": self.robot_name,
                "calibrated": self.calibrated,
                "moving": self.moving,
                "loop_hz": self.loop_hz,
                "deadman_tripped": False,
                "walk_policy": self.walk_available,
                "stale_ms": self.stale_ms,
                "stale_limit_ms": 1000.0,
                "tilt_deg": 90.0 if self.fallen else 2.0,
            },
        )

    async def send_intent(self, intent: Intent) -> Ack:
        self.intents.append(intent)
        if intent.kind in self._refuse:
            return Ack(accepted=False, reason=f"mock refuses {intent.kind}")
        p = intent.params
        match intent.kind:
            case "move":
                if not self.walk_available:
                    return Ack(accepted=False, reason="no walk policy is staged on this robot")
                if self.fallen:
                    return Ack(accepted=False, reason="the robot has fallen")
                self.vx = float(p.get("vx", 0.0))
                self.vy = float(p.get("vy", 0.0))
                self.wz = float(p.get("wz", 0.0))
            case "stop":
                # hold, never limp: there is no zero velocity on this body
                self.vx = self.vy = self.wz = 0.0
                self._busy_until = self._t
                self.motion = None
            case "look":
                if not self.neck_available:
                    return Ack(accepted=False, reason="this build has no neck")
                if self.fallen:
                    return Ack(accepted=False, reason="the robot has fallen")
                # a direction, not degrees: the same three slots every other body uses
                yaw, pitch = look_degrees(
                    float(p.get("x", 1.0)), float(p.get("y", 0.0)), float(p.get("z", 0.0))
                )
                self.neck_yaw = max(-NECK_YAW_LIMIT, min(NECK_YAW_LIMIT, yaw))
                self.neck_pitch = max(-NECK_PITCH_LIMIT, min(NECK_PITCH_LIMIT, pitch))
            case "gripper":
                if not self.gripper_available:
                    return Ack(accepted=False, reason="this build has no grippers")
                close = not bool(p.get("open", True))
                side = str(p.get("side", "right"))
                sides = ["left", "right"] if side == "both" else [side]
                for one in sides:
                    if one in self.holding:
                        self.holding[one] = close  # commanded, never sensed
            case "do":
                skill = str(p.get("skill", ""))
                if self.fallen:
                    return Ack(accepted=False, reason="the robot has fallen")
                if skill == "stand":
                    self.stands += 1
                    self._busy_until = self._t + STAND_S
                    self.vx = self.vy = self.wz = 0.0
                    return Ack()
                kind, _, name = skill.partition(":")
                if kind != "motion" or name not in MOTIONS:
                    return Ack(accepted=False, reason=f"unknown skill {skill!r}")
                self.performed.append(name)
                self.motion = name
                self._busy_until = self._t + PERFORM_S
            case "enable":
                if not p.get("on", True):
                    return Ack(accepted=False, reason="Roboglot never limps a robot")
            case _:
                return Ack(accepted=False, reason=f"a ToddlerBot cannot {intent.kind}")
        return Ack()

    async def subscribe(self, topic: str) -> AsyncIterator[dict[str, Any]]:  # type: ignore[override]
        while self.connected:
            await self.sleep(0.5)
            yield {"topic": topic, **(await self.get_state()).model_dump()}

    async def heartbeat(self) -> None:
        self.heartbeats += 1
        if self._fail_after is not None and self.heartbeats > self._fail_after:
            raise HeartbeatError("mock toddlerbot heartbeat failure (scripted)")

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
        await asyncio.sleep(0)

    # ── test helpers ────────────────────────────────────────────────────────────────

    def intents_of(self, kind: str) -> list[Intent]:
        return [i for i in self.intents if i.kind == kind]
