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
from collections.abc import Callable

import numpy as np
import numpy.typing as npt
from PIL import Image, ImageChops, ImageDraw

from quackd.sim2d.world import ARENA_HALF, World
from quackd.space.grid import UNKNOWN, OccupancyGrid
from quackd.space.room import Room

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

# A room's walls and furniture, in greys far below the saturation every detector band starts at
# (`quackd.perception.color_blob`), so nothing solid is ever taken for a ball, a person or a duck.
ROOM_WALL = (150, 144, 136)
FURNITURE = (118, 110, 102)
ROOM_WALL_H_M = 0.30  # how tall walls and furniture stand, above the camera so they hide the sky
BEYOND = (128, 122, 114)  # the top-down view outside a room's walls
UNSEEN = (96, 96, 104)  # laid over floor the simulated map has not seen yet
UNSEEN_ALPHA = 110  # of 255
KEYFRAME = (40, 120, 140)  # where the simulated map stopped and looked


def focal_px(width: int, fov_deg: float = CAM_FOV_DEG) -> float:
    return (width / 2) / math.tan(math.radians(fov_deg) / 2)


def render_topdown(
    world: World, size: int = 256, *, seen: OccupancyGrid | None = None
) -> Image.Image:
    """The world from above. A room's walls and furniture are drawn to its own scale, and
    `seen`, a simulated map's grid, shades the floor it has not seen and dots where it stopped.
    With no room, the arena exactly as it always was."""
    px: Callable[[float, float], tuple[float, float]]
    if world.room is None:
        img = Image.new("RGB", (size, size), FLOOR)
        draw = ImageDraw.Draw(img)
        scale = size / (2 * ARENA_HALF)

        def arena(x: float, y: float) -> tuple[float, float]:
            return (x + ARENA_HALF) * scale, (ARENA_HALF - y) * scale

        px = arena
        draw.rectangle([0, 0, size - 1, size - 1], outline=WALL, width=3)
    else:
        img, scale, px = _room_canvas(world.room, size, seen)
        draw = ImageDraw.Draw(img)
    _draw_world(draw, world, scale, px)
    return img


def _room_canvas(
    room: Room, size: int, seen: OccupancyGrid | None
) -> tuple[Image.Image, float, Callable[[float, float], tuple[float, float]]]:
    """A square canvas with `room` centred in it at the largest scale that fits, its walls and
    furniture drawn, and what `seen` has not seen shaded."""
    xmin, ymin, xmax, ymax = room.bounds
    scale = (size - 8) / max(xmax - xmin, ymax - ymin)
    left = (size - (xmax - xmin) * scale) / 2
    top = (size - (ymax - ymin) * scale) / 2

    def px(x: float, y: float) -> tuple[float, float]:
        return left + (x - xmin) * scale, top + (ymax - y) * scale

    img = Image.new("RGB", (size, size), BEYOND)
    draw = ImageDraw.Draw(img)
    draw.rectangle([*px(xmin, ymax), *px(xmax, ymin)], fill=FLOOR, outline=WALL, width=3)
    for box in room.boxes:
        corner0 = px(box.cx - box.w / 2, box.cy + box.d / 2)
        corner1 = px(box.cx + box.w / 2, box.cy - box.d / 2)
        draw.rectangle([*corner0, *corner1], fill=FURNITURE, outline=WALL)
    for wall in room.walls:
        width = max(2, round(wall.thickness * scale))
        draw.line([px(wall.x0, wall.y0), px(wall.x1, wall.y1)], fill=WALL, width=width)
    if seen is not None:
        rows, cols = seen.cells.shape
        ox, oy = seen.origin
        x0, y0 = px(ox, oy + rows * seen.res_m)
        x1, y1 = px(ox + cols * seen.res_m, oy)
        unseen = np.flipud(seen.cells == UNKNOWN).astype(np.uint8) * UNSEEN_ALPHA
        mask = Image.fromarray(unseen, mode="L").resize(
            (max(1, round(x1 - x0)), max(1, round(y1 - y0))), Image.Resampling.NEAREST
        )
        shade = Image.new("RGB", mask.size, UNSEEN)
        img.paste(shade, (round(x0), round(y0)), mask)
        for pose in seen.keyframes:
            kx, ky = px(pose.x, pose.y)
            draw.ellipse([kx - 2, ky - 2, kx + 2, ky + 2], fill=KEYFRAME)
    return img, scale, px


def _draw_world(
    draw: ImageDraw.ImageDraw,
    world: World,
    scale: float,
    px: Callable[[float, float], tuple[float, float]],
) -> None:
    """The ball, the people and the ducks, from above."""
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
    the ball). A room's walls and furniture stand as columns cast one to a pixel, and hide
    whatever is behind them."""
    w = h = size
    img = Image.new("RGB", (w, h), SKY)
    draw = ImageDraw.Draw(img)
    horizon = int(h * HORIZON)
    draw.rectangle([0, horizon, w, h], fill=FLOOR)
    f = focal_px(w)
    half_fov = math.radians(CAM_FOV_DEG) / 2
    depth = None
    if world.room is not None:
        depth = _draw_room(draw, world.room, w, f, horizon, (ox, oy, heading), cam_height)

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
            shape = [cx - rpx, cy - rpx, cx + rpx, cy + rpx]
        else:
            hpx = f * height / dist
            shape = [cx - rpx, ground_y - hpx, cx + rpx, ground_y]
        if depth is None:
            if round_:
                draw.ellipse(shape, fill=colour)
            else:
                draw.rectangle(shape, fill=colour)
            continue
        # drawn only in the columns where it stands nearer than the room's walls
        layer = Image.new("L", (w, h), 0)
        if round_:
            ImageDraw.Draw(layer).ellipse(shape, fill=255)
        else:
            ImageDraw.Draw(layer).rectangle(shape, fill=255)
        nearer = (depth > dist * math.cos(bearing)).astype(np.uint8) * 255
        columns = Image.fromarray(np.ascontiguousarray(np.broadcast_to(nearer, (h, w))), mode="L")
        img.paste(colour, None, ImageChops.multiply(layer, columns))
    return img


def _draw_room(
    draw: ImageDraw.ImageDraw,
    room: Room,
    w: int,
    f: float,
    horizon: int,
    view: tuple[float, float, float],
    cam_height: float,
) -> npt.NDArray[np.float64]:
    """The room's walls and furniture seen from `view` (x, y, heading), one column to a pixel,
    and how deep each column's is along the view, so a straight wall is drawn straight and what
    stands behind it can be hidden."""
    ox, oy, heading = view
    reach = 2 * math.hypot(room.half_w, room.half_h) + 1.0
    depth = np.full(w, np.inf)
    for col in range(w):
        bearing = math.atan((w / 2 - (col + 0.5)) / f)
        hit = room.cast(ox, oy, heading + bearing, reach)
        if hit is None:
            continue
        along = max(hit[0] * math.cos(bearing), 0.01)
        depth[col] = along
        top = horizon - f * (ROOM_WALL_H_M - cam_height) / along
        bottom = horizon + f * cam_height / along
        draw.line([(col, top), (col, bottom)], fill=FURNITURE if hit[1] == "box" else ROOM_WALL)
    return depth


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
