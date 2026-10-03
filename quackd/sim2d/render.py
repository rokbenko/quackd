"""Two views of the same cartoon world, for one duck or a flock.

`render_topdown` is for humans and GIFs. `render_duckcam` is what one *duck* sees — a
first-person projection with real perspective, so the same colour-blob detector that
finds an orange ball in a real camera frame finds it here, and its distance estimate
comes out in metres. In a flock, teammates appear in each other's cameras in their own
colorway body colour (deliberately WITHOUT the beak: the beak orange sits inside the
ball's hue band and would forge ball detections).
"""

from __future__ import annotations

import math

from PIL import Image, ImageDraw

from quackd.sim2d.world import ARENA_HALF, World

# Colours are part of the contract: the detector's HSV ranges are tuned to these.
FLOOR = (236, 229, 212)
SKY = (204, 222, 240)
WALL = (70, 62, 55)
BALL = (255, 140, 0)  # orange — hue ≈ 33°
DUCK = (250, 210, 40)  # cream colorway (the historic duck yellow)
BEAK = (230, 100, 20)  # NEVER used for peers in duck-cams: its hue is inside the ball band
PERSON = (60, 90, 220)  # blue
PET = (60, 180, 80)  # green
TEXT = (40, 40, 40)

# The four Microduck colorways, HSV-separable from ball/person/pet and from each other
# (OpenCV hues: cream 24, sky 93, lavender 138, graphite 161). A literal grey has no hue,
# so "graphite" is a saturated dark plum stand-in — documented in docs/guides/flock.md.
COLORWAY_RGB: dict[str, tuple[int, int, int]] = {
    "cream": DUCK,
    "sky": (70, 210, 225),
    "lavender": (185, 105, 235),
    "graphite": (140, 60, 110),
}
DUCK_BODY_HEIGHT_M = 0.18  # how tall a peer looks in another duck's camera

CAM_FOV_DEG = 90.0
CAM_HEIGHT_M = 0.20
HORIZON = 0.45  # fraction of frame height


def focal_px(width: int, fov_deg: float = CAM_FOV_DEG) -> float:
    return (width / 2) / math.tan(math.radians(fov_deg) / 2)


def render_topdown(world: World, size: int = 256) -> Image.Image:
    img = Image.new("RGB", (size, size), FLOOR)
    draw = ImageDraw.Draw(img)
    scale = size / (2 * ARENA_HALF)

    def px(x: float, y: float) -> tuple[float, float]:
        return (x + ARENA_HALF) * scale, (ARENA_HALF - y) * scale

    draw.rectangle([0, 0, size - 1, size - 1], outline=WALL, width=3)
    b = world.ball
    if b.present:
        sx, sy = px(b.start_x, b.start_y)
        rr = b.r * scale
        draw.ellipse([sx - rr, sy - rr, sx + rr, sy + rr], outline=(220, 200, 170), width=1)
        cx, cy = px(b.x, b.y)
        draw.ellipse([cx - rr, cy - rr, cx + rr, cy + rr], fill=BALL)
    for p in world.people:
        cx, cy = px(p.x, p.y)
        rr = p.r * scale
        draw.ellipse(
            [cx - rr, cy - rr, cx + rr, cy + rr], fill=PERSON if p.label == "person" else PET
        )
    flock = len(world.ducks) > 1
    for i, d in enumerate(world.ducks):
        body = COLORWAY_RGB.get(d.colorway, DUCK)
        cx, cy = px(d.x, d.y)
        rr = d.r * scale * 1.4
        pts = []
        for ang in (0.0, 2.4, -2.4):
            a = d.theta + ang
            r = rr if ang == 0.0 else rr * 0.8
            pts.append((cx + r * math.cos(a), cy - r * math.sin(a)))
        draw.polygon(pts, fill=body, outline=WALL)
        ha = d.theta + d.head_yaw
        draw.ellipse(
            [
                cx + rr * 0.55 * math.cos(ha) - 3,
                cy - rr * 0.55 * math.sin(ha) - 3,
                cx + rr * 0.55 * math.cos(ha) + 3,
                cy - rr * 0.55 * math.sin(ha) + 3,
            ],
            fill=BEAK,
        )
        if flock:
            draw.text((cx - 3, cy - rr - 12), chr(ord("A") + i), fill=TEXT)
        if d.posture != "standing":
            draw.text((cx - 10, cy + rr), d.posture, fill=TEXT)
        if d.holding:
            draw.ellipse([cx - 4, cy - 4, cx + 4, cy + 4], fill=BALL)
    return img


def _render_cam(
    world: World,
    size: int,
    *,
    ox: float,
    oy: float,
    heading: float,
    cam_height: float,
    skip_duck: int | None,
) -> Image.Image:
    """A first-person view from (ox, oy) facing `heading`. Floor objects project below the
    horizon by f·h/d, size ∝ 1/d. Peers are body-colour boxes (no beak — hue collision with
    the ball)."""
    w = h = size
    img = Image.new("RGB", (w, h), SKY)
    draw = ImageDraw.Draw(img)
    horizon = int(h * HORIZON)
    draw.rectangle([0, horizon, w, h], fill=FLOOR)
    f = focal_px(w)
    half_fov = math.radians(CAM_FOV_DEG) / 2

    objects: list[tuple[float, float, float, float, tuple[int, int, int], bool]] = []
    for p in world.people:
        dist, bearing = world.relative_to(p.x, p.y, ox=ox, oy=oy, heading=heading)
        objects.append((dist, bearing, p.r, 0.5, PERSON if p.label == "person" else PET, False))
    for i, peer in enumerate(world.ducks):
        if i == skip_duck:
            continue  # the viewer never sees itself
        dist, bearing = world.relative_to(peer.x, peer.y, ox=ox, oy=oy, heading=heading)
        body = COLORWAY_RGB.get(peer.colorway, DUCK)
        objects.append((dist, bearing, peer.r, DUCK_BODY_HEIGHT_M, body, False))
    if world.ball.present:
        dist, bearing = world.relative_to(world.ball.x, world.ball.y, ox=ox, oy=oy, heading=heading)
        objects.append((dist, bearing, world.ball.r, 0.0, BALL, True))

    # far to near, so near things occlude
    for dist, bearing, radius, height, colour, round_ in sorted(objects, key=lambda o: -o[0]):
        if abs(bearing) > half_fov + 0.2 or dist < 0.02:
            continue
        cx = w / 2 - math.tan(bearing) * f
        ground_y = horizon + f * cam_height / dist
        rpx = f * radius / dist
        if round_:
            cy = ground_y - rpx
            draw.ellipse([cx - rpx, cy - rpx, cx + rpx, cy + rpx], fill=colour)
        else:
            hpx = f * height / dist
            draw.rectangle([cx - rpx, ground_y - hpx, cx + rpx, ground_y], fill=colour)
    return img


def render_duckcam(world: World, size: int = 256, *, duck_index: int = 0) -> Image.Image:
    """One duck's first-person view (byte-identical to 0.3)."""
    d = world.ducks[duck_index]
    return _render_cam(
        world,
        size,
        ox=d.x,
        oy=d.y,
        heading=d.theta + d.head_yaw,
        cam_height=CAM_HEIGHT_M,
        skip_duck=duck_index,
    )
