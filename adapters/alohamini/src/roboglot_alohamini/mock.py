"""A two-armed cart that does exactly what the test tells it to.

`AlohaMiniMock` is a holonomic base, a vertical axis and two arms in memory. It reproduces the
upstream behaviours that decide whether a stop is real:

- **The lift latches.** Its velocity is a register, not a request. Nothing but an explicit zero
  clears it, so a command that says nothing about the lift leaves it travelling
  (`upstream_api.LIFT_APPLY_ACTION_HAS_NO_ELSE`).
- **Homing leaves full-speed descent in that register.** `connect()` ends with the lift
  descending at 1300, because upstream's zeroing write is commented out
  (`upstream_api.LIFT_HOME_LEAVES_FULL_SPEED_IN_THE_REGISTER`).
- **The watchdog covers the base and the lift, never the arms**
  (`upstream_api.STOP_MOTION_SKIPS_THE_ARMS`).
- **The arms are limp unless a host wrapper enabled torque**
  (`upstream_api.ARM_TORQUE_IS_NEVER_ENABLED`). Build with `arm_torque=False` to get the robot
  upstream actually ships.
"""

from __future__ import annotations

import asyncio
import math
from collections.abc import AsyncIterator
from typing import Any

from PIL import Image, ImageDraw

from roboglot.perception.palette import BALL, FLOOR, HORIZON, SKY, focal_px
from roboglot.transport.base import Ack, DuckState, HeartbeatError, Intent
from roboglot_alohamini.verbs import (
    ARMS,
    DEFAULT_MODEL,
    GRIPPER_CLOSED,
    GRIPPER_OPEN,
    LIFT_MAX_MM,
    LIFT_MIN_MM,
    joint_range,
    joints_for,
)

WATCHDOG_S = 1.0
"""Upstream's own `watchdog_timeout_ms`, in seconds."""
TICK_S = 0.05
HOME_DESCENT_VELOCITY = 1300
"""What `home()` leaves in the register: see
`upstream_api.LIFT_HOME_LEAVES_FULL_SPEED_IN_THE_REGISTER`."""
LIFT_KP = 300.0
LIFT_V_MAX = 1300.0
LIFT_DEADBAND_MM = 1.0
LIFT_MM_PER_UNIT = 60.0 / LIFT_V_MAX
"""Roboglot's own integration constant, chosen so a lift at full velocity travels 60 mm/s.

Upstream states no mm/s figure anywhere: its velocity maximum is 1300 raw units and nothing
says what that is in millimetres. So this is a plausible number that makes the mock's lift
take a few seconds rather than a guess dressed up as a measurement, and the manifest records
the real speed as unknown."""
MOCK_CAM_HEIGHT_M = 0.4
MOCK_BALL_R = 0.05
FOV_DEG = 45.0
DEFAULT_CAMERAS = ("forward", "wrist_right")


class AlohaMiniMock:
    name = "mock"
    mobility = "wheeled"

    def __init__(
        self,
        *,
        camera: bool = True,
        cameras: tuple[str, ...] = DEFAULT_CAMERAS,
        model: str = DEFAULT_MODEL,
        arms: bool = True,
        arm_torque: bool = True,
        calibrated: bool = True,
        ball_xy: tuple[float, float] | None = (1.5, 0.5),
        frame_size: int = 128,
        fail_heartbeat_after: int | None = None,
        refuse_kinds: set[str] | None = None,
    ) -> None:
        self.camera_available = camera
        self.cameras = cameras if camera else ()
        self.robot_model = model
        self.arms_available = arms
        self.arm_torque = arm_torque and arms
        self.calibrated = calibrated
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
        self.joints: dict[str, float] = dict.fromkeys(joints_for(model), 0.0) | {
            f"arm_{arm}_gripper": GRIPPER_OPEN for arm in ARMS
        }
        self.holding: dict[str, bool] = dict.fromkeys(ARMS, False)
        self.lift_height_mm = 120.0
        self.lift_velocity = 0
        """The register. Latches until something writes a zero."""
        self.lift_target_mm: float | None = None
        self.intents: list[Intent] = []
        self.stops = 0
        self.heartbeats = 0
        self.connected = False
        self.stale_ms: float = 0.0

    # ── motion ──────────────────────────────────────────────────────────────────────

    def _integrate(self, dt: float) -> None:
        if self._last_cmd_t is not None and self._t - self._last_cmd_t > WATCHDOG_S:
            # the host's watchdog: stop_motion is the base and the lift, never the arms
            self.vx = self.vy = self.wz = 0.0
            self.lift_velocity = 0
            self.lift_target_mm = None
        if self.lift_target_mm is not None:
            err = self.lift_target_mm - self.lift_height_mm
            if abs(err) <= LIFT_DEADBAND_MM:
                self.lift_velocity = 0
            else:
                self.lift_velocity = int(max(-LIFT_V_MAX, min(LIFT_V_MAX, LIFT_KP * err)))
        self.lift_height_mm += self.lift_velocity * LIFT_MM_PER_UNIT * dt
        self.lift_height_mm = max(0.0, min(LIFT_MAX_MM, self.lift_height_mm))
        if self.lift_height_mm <= LIFT_MIN_MM and self.lift_velocity < 0:
            self.lift_velocity = 0  # the descent floor is a refusal, not a clamp to zero
        c, s = math.cos(self.theta), math.sin(self.theta)
        self.x += (self.vx * c - self.vy * s) * dt
        self.y += (self.vx * s + self.vy * c) * dt
        self.theta = math.atan2(
            math.sin(self.theta + self.wz * dt), math.cos(self.theta + self.wz * dt)
        )

    def ball_relative(self) -> tuple[float, float] | None:
        if self.ball_xy is None:
            return None
        dx, dy = self.ball_xy[0] - self.x, self.ball_xy[1] - self.y
        bearing = math.degrees(math.atan2(dy, dx) - self.theta)
        return math.hypot(dx, dy), (bearing + 180.0) % 360.0 - 180.0

    def _halt_motion(self) -> None:
        """`stop_motion`: the base and the lift, and deliberately not the arms."""
        self.vx = self.vy = self.wz = 0.0
        self.lift_velocity = 0
        self.lift_target_mm = None

    def _goto(self, goals: dict[str, float]) -> None:
        for joint, goal in goals.items():
            if joint in self.joints:
                lo, hi = joint_range(joint)
                self.joints[joint] = min(hi, max(lo, float(goal)))

    # ── protocol ────────────────────────────────────────────────────────────────────

    async def connect(self) -> None:
        self.connected = True
        # home() ran and left full-speed descent in the register, with no zero after it
        if self.calibrated:
            self.lift_velocity = -HOME_DESCENT_VELOCITY

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
                "lift_height_mm": round(self.lift_height_mm, 1),
                "lift_velocity": self.lift_velocity,
                "robot_model": self.robot_model,
                "arm_torque": self.arm_torque,
                "calibrated": self.calibrated,
                "cameras": list(self.cameras),
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
                self.vx = float(p.get("vx", 0.0))
                self.vy = float(p.get("vy", 0.0))
                self.wz = float(p.get("wz", 0.0))
                self._last_cmd_t = self._t
            case "stop":
                self._halt_motion()
                self._last_cmd_t = self._t
            case "pose":
                target = p.get("lift_height_mm")
                if target is None:
                    return Ack(accepted=False, reason="pose needs a lift_height_mm")
                self.lift_target_mm = float(target)
                self._last_cmd_t = self._t
            case "joint":
                if not self.arm_torque:
                    return Ack(accepted=False, reason="the arms have no torque")
                self._goto({str(k): float(v) for k, v in dict(p.get("positions", {})).items()})
                self.vx = self.vy = self.wz = 0.0  # upstream writes the wheels on every action
                self._last_cmd_t = self._t
            case "gripper":
                if not self.arm_torque:
                    return Ack(accepted=False, reason="the arms have no torque")
                open_ = bool(p.get("open", True))
                sides = list(ARMS) if p.get("side") == "both" else [str(p.get("side", "right"))]
                for side in sides:
                    if side in ARMS:
                        self._goto(
                            {f"arm_{side}_gripper": GRIPPER_OPEN if open_ else GRIPPER_CLOSED}
                        )
                        self.holding[side] = not open_  # commanded, never sensed
                self.vx = self.vy = self.wz = 0.0
                self._last_cmd_t = self._t
            case "enable":
                if not p.get("on", True):
                    return Ack(accepted=False, reason="Roboglot never limps a robot")
            case _:
                return Ack(accepted=False, reason=f"an AlohaMini cannot {intent.kind}")
        return Ack()

    async def subscribe(self, topic: str) -> AsyncIterator[dict[str, Any]]:  # type: ignore[override]
        while self.connected:
            await self.sleep(0.5)
            yield {"topic": topic, **(await self.get_state()).model_dump()}

    async def heartbeat(self) -> None:
        self.heartbeats += 1
        if self._fail_after is not None and self.heartbeats > self._fail_after:
            raise HeartbeatError("mock alohamini heartbeat failure (scripted)")

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
