"""A wheeled base that does exactly what the test tells it to.

`RosbridgeMock` is a planar kinematic integrator with the same deadman semantics as the
simulator: a Twist holds for half a second, then the base coasts to zero. Odometry is the
integrated pose, and the optional camera serves a synthetic frame with an orange disc at a
fixed spot in the arena, so `observe`, `search_scan` (turn mode) and `go_to` can be
exercised offline, all the way to the executor's gates.
"""

from __future__ import annotations

import asyncio
import math
import uuid
from collections.abc import AsyncIterator
from typing import Any

from PIL import Image, ImageDraw

from quackd.sim2d.render import BALL, FLOOR, HORIZON, SKY, focal_px
from quackd.space.odometry import OdometrySource, OdometryTrack
from quackd.space.pose import where_record
from quackd.transport.base import Ack, DuckState, HeartbeatError, Intent
from quackd_rosbridge import upstream_api as up
from quackd_rosbridge.introspection import Introspection, parse_urdf

MOCK_URDF = """<robot name="mock-base">
  <link name="base_link"><inertial><mass value="10.0"/></inertial></link>
  <link name="left_wheel"><inertial><mass value="0.5"/></inertial></link>
  <link name="right_wheel"><inertial><mass value="0.5"/></inertial></link>
  <link name="caster"/>
  <joint name="left_wheel_joint" type="continuous">
    <parent link="base_link"/><child link="left_wheel"/>
    <limit effort="5" velocity="10"/>
  </joint>
  <joint name="right_wheel_joint" type="continuous">
    <parent link="base_link"/><child link="right_wheel"/>
    <limit effort="5" velocity="10"/>
  </joint>
  <joint name="caster_joint" type="fixed">
    <parent link="base_link"/><child link="caster"/>
  </joint>
</robot>
"""
"""A description shaped like the ones a real base publishes: a chassis with inertials, a
wheel pair that moves, and one link (the caster) with no inertial at all, because plenty of
real URDFs leave them out. 11 kg over three links, two moving joints."""

DEADMAN_S = 0.5
TICK_S = 0.05
MOCK_CAM_HEIGHT_M = 0.2
MOCK_BALL_R = 0.05
FOV_DEG = 45.0


def mock_introspection(urdf: str | None = MOCK_URDF, *, camera: bool = True) -> Introspection:
    """What the mock bridge answers when it is asked about the body.

    Shared with `describe("mock")`, so the static description of this backend and the one a
    connected run reads are the same thing rather than two guesses that might drift."""
    topics = {
        "/cmd_vel": up.MSG_TWIST.name,
        "/odom": up.MSG_ODOMETRY.name,
    }
    if camera:
        topics["/camera/compressed"] = up.MSG_COMPRESSED_IMAGE.name
    if urdf is None:
        return Introspection.from_urdf(
            None, topics=topics, errors=("the mock was built without a description",)
        )
    topics[up.DESCRIPTION_TOPIC.name] = up.MSG_STRING.name
    return Introspection.from_urdf(
        parse_urdf(urdf), topics=topics, source="mock", where="the mock's canned URDF"
    )


class RosbridgeMock:
    name = "mock"
    mobility = "wheeled"

    def __init__(
        self,
        *,
        camera: bool = True,
        ball_xy: tuple[float, float] | None = (1.5, 0.5),
        frame_size: int = 128,
        fail_heartbeat_after: int | None = None,
        urdf: str | None = MOCK_URDF,
    ) -> None:
        self.camera_available = camera
        self.urdf = urdf
        self.introspection: Introspection | None = None
        self.introspections = 0
        self.ball_xy = ball_xy
        self._frame_size = frame_size
        self._fail_after = fail_heartbeat_after
        self.x = 0.0
        self.y = 0.0
        self.theta = 0.0
        self.vx = 0.0
        self.vy = 0.0
        self.wz = 0.0
        self._t = 0.0
        self._last_cmd_t: float | None = None
        self.twists: list[dict[str, float]] = []
        self.stops = 0
        self.heartbeats = 0
        self.connected = False
        self.distance_m = 0.0
        self._track: OdometryTrack | None = None
        self.spatial: OdometrySource | None = None
        """Where the mock base is by its own integration, as /odom would say, in a frame that
        lasts the connection, as the ws backend's does. Set at connect."""

    # ── physics ─────────────────────────────────────────────────────────────────────

    def _integrate(self, dt: float) -> None:
        if self._last_cmd_t is not None and self._t - self._last_cmd_t > DEADMAN_S:
            self.vx = self.vy = self.wz = 0.0  # the deadman: silence means stop
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
        """(distance, bearing in degrees, + = left) of the ball from the base, or None."""
        if self.ball_xy is None:
            return None
        dx, dy = self.ball_xy[0] - self.x, self.ball_xy[1] - self.y
        dist = math.hypot(dx, dy)
        bearing = math.degrees(math.atan2(dy, dx) - self.theta)
        bearing = (bearing + 180.0) % 360.0 - 180.0
        return dist, bearing

    # ── protocol ────────────────────────────────────────────────────────────────────

    async def connect(self) -> None:
        self.connected = True
        self.introspection = mock_introspection(self.urdf, camera=self.camera_available)
        self._track = OdometryTrack(f"odom:mock:{uuid.uuid4().hex[:12]}")
        self._track.update(self.x, self.y, self.theta, self._t)
        self.spatial = OdometrySource(self._track, label="the mock base's odometry")

    async def introspect(self) -> Introspection:
        self.introspections += 1
        self.introspection = mock_introspection(self.urdf, camera=self.camera_available)
        return self.introspection

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
            x=self.x,
            y=self.y,
            theta=self.theta,
            extras={
                **({"where": where_record(self.spatial.fix())} if self.spatial else {}),
                "odom": {
                    "x": round(self.x, 3),
                    "y": round(self.y, 3),
                    "yaw_deg": round(math.degrees(self.theta), 1),
                },
                "twist": {"vx": self.vx, "vy": self.vy, "wz": self.wz},
                "distance_m": round(self.distance_m, 3),
            },
        )

    async def send_intent(self, intent: Intent) -> Ack:
        p = intent.params
        match intent.kind:
            case "move":
                self.vx, self.vy, self.wz = (
                    float(p.get("vx", 0.0)),
                    float(p.get("vy", 0.0)),
                    float(p.get("wz", 0.0)),
                )
                self._last_cmd_t = self._t
                self.twists.append({"vx": self.vx, "vy": self.vy, "wz": self.wz})
            case "stop":
                self.vx = self.vy = self.wz = 0.0
                self.twists.append({"vx": 0.0, "vy": 0.0, "wz": 0.0})
            case _:
                return Ack(
                    accepted=False, reason=f"a wheeled base over rosbridge cannot {intent.kind}"
                )
        return Ack()

    async def subscribe(self, topic: str) -> AsyncIterator[dict[str, Any]]:  # type: ignore[override]
        while self.connected:
            await self.sleep(0.5)
            yield {"topic": topic, **(await self.get_state()).model_dump()}

    async def heartbeat(self) -> None:
        self.heartbeats += 1
        if self._fail_after is not None and self.heartbeats > self._fail_after:
            raise HeartbeatError("mock rosbridge heartbeat failure (scripted)")

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
            if self._track is not None:
                self._track.update(self.x, self.y, self.theta, self._t)
            remaining -= dt
        await asyncio.sleep(0)  # yield so the heartbeat task gets a turn
