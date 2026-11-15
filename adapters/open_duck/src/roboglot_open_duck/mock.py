"""An Open Duck Mini v2 that does exactly what the test tells it to.

`OpenDuckMock` records intents like `MockTransport` and additionally integrates the twist it
was last given, so the verbs that close a loop (`search_scan` turning in place, `go_to`
steering on a bearing) can be exercised offline with no simulator and no world. The frame it
serves puts an orange ball at a fixed world position, seen from wherever the duck has walked
to, so the colour detector has something real to find.
"""

from __future__ import annotations

import math

from PIL import Image, ImageDraw

from roboglot.perception.palette import BALL, FLOOR, HORIZON, SKY, focal_px
from roboglot.transport.base import Ack, DuckState, Intent
from roboglot.transport.mock import MockTransport
from roboglot_open_duck.verbs import GESTURES

MOCK_CAM_HEIGHT_M = 0.30  # the v2 stands about 42 cm; its camera sits below the head top
MOCK_BALL_R = 0.05
MOCK_FOV_HALF_DEG = 45.0


class OpenDuckMock(MockTransport):
    name = "mock"
    mobility = "legged"
    features = {"camera": True, "speaker": True, "antennas": True, "head": True}

    def __init__(
        self,
        *,
        ball_xy: tuple[float, float] | None = (0.9, 0.4),
        frame_size: int = 128,
        fallen: bool = False,
        policy_running: bool = True,
        fail_heartbeat_after: int | None = None,
        refuse_kinds: set[str] | None = None,
    ) -> None:
        super().__init__(
            states=[
                DuckState(
                    policy="stand",
                    posture="standing",
                    battery_percent=None,
                    extras={"head_yaw_deg": 0.0, "loop_hz": 50.0},
                )
            ],
            fail_heartbeat_after=fail_heartbeat_after,
            refuse_kinds=refuse_kinds,
            frame_size=(frame_size, frame_size),
        )
        self.fallen = fallen
        self.policy_running = policy_running
        self.x = 0.0
        self.y = 0.0
        self.theta = 0.0
        self.head_yaw_deg = 0.0
        self.head_pitch_deg = 0.0
        self.ball_xy = ball_xy
        self.gestures: list[str] = []
        self.sounds: list[tuple[str, str | None]] = []
        self._vx = 0.0
        self._vy = 0.0
        self._wz = 0.0

    # ── motion ──────────────────────────────────────────────────────────────────────

    async def sleep(self, seconds: float) -> None:
        self.theta += self._wz * seconds
        self.x += (self._vx * math.cos(self.theta) - self._vy * math.sin(self.theta)) * seconds
        self.y += (self._vx * math.sin(self.theta) + self._vy * math.cos(self.theta)) * seconds
        await super().sleep(seconds)

    async def get_state(self) -> DuckState:
        state = await super().get_state()
        return state.model_copy(
            update={
                "x": round(self.x, 4),
                "y": round(self.y, 4),
                "theta": round(self.theta, 4),
                "policy": "walk" if (self._vx or self._vy or self._wz) else "stand",
                "posture": "fallen" if self.fallen else "standing",
                "fallen": self.fallen,
                "extras": {
                    **state.extras,
                    "head_yaw_deg": round(self.head_yaw_deg, 1),
                    "head_pitch_deg": round(self.head_pitch_deg, 1),
                    "policy_running": self.policy_running,
                    "gestures": len(self.gestures),
                },
            }
        )

    async def stop(self) -> None:
        self._vx = self._vy = self._wz = 0.0
        await super().stop()

    # ── perception ──────────────────────────────────────────────────────────────────

    async def get_frame(self) -> Image.Image | None:
        size = self._frame_size[0]
        img = Image.new("RGB", (size, size), SKY)
        draw = ImageDraw.Draw(img)
        horizon = int(size * HORIZON)
        draw.rectangle([0, horizon, size, size], fill=FLOOR)
        if self.ball_xy is None:
            return img
        dx, dy = self.ball_xy[0] - self.x, self.ball_xy[1] - self.y
        distance = math.hypot(dx, dy)
        if distance < 1e-3:
            return img
        rel = math.atan2(dy, dx) - self.theta - math.radians(self.head_yaw_deg)
        rel = math.atan2(math.sin(rel), math.cos(rel))
        if abs(rel) > math.radians(MOCK_FOV_HALF_DEG):
            return img
        f = focal_px(size)
        cx = size / 2 - math.tan(rel) * f
        ground_y = horizon + f * MOCK_CAM_HEIGHT_M / distance
        r = max(1.0, f * MOCK_BALL_R / distance)
        draw.ellipse([cx - r, ground_y - 2 * r, cx + r, ground_y], fill=BALL)
        return img

    # ── intents ─────────────────────────────────────────────────────────────────────

    async def send_intent(self, intent: Intent) -> Ack:
        ack = await super().send_intent(intent)
        if not ack.accepted:
            return ack
        p = intent.params
        match intent.kind:
            case "move":
                self._vx = float(p.get("vx", 0.0))
                self._vy = float(p.get("vy", 0.0))
                self._wz = float(p.get("wz", 0.0))
            case "stop":
                self._vx = self._vy = self._wz = 0.0
            case "look":
                self.head_yaw_deg = math.degrees(math.atan2(p.get("y", 0.0), p.get("x", 1.0)))
                self.head_pitch_deg = math.degrees(
                    math.atan2(p.get("z", 0.0), math.hypot(p.get("x", 1.0), p.get("y", 0.0)))
                )
            case "sound":
                text = p.get("text")
                self.sounds.append((str(p.get("tag")), None if text is None else str(text)))
            case "do":
                kind, _, arg = str(p.get("skill")).partition(":")
                if kind != "antennas" or arg not in GESTURES:
                    return Ack(accepted=False, reason=f"unknown skill {p.get('skill')!r}")
                self.gestures.append(arg)
            case "enable":
                if not p.get("on", True):
                    return Ack(accepted=False, reason="Roboglot never limps a robot")
        return ack
