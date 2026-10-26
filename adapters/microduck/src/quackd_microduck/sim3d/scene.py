"""The arena as MJCF text: floor, walls, a ball, and a body dropped in.

The arena is the cartoon's, 2 m across with the same orange ball, so the colour detector that
reads `sim2d`'s frames reads this one unchanged. Nobody stands in it: the cartoon puts a person
marker in its arena and this one has none, which is the single place the two worlds differ and
the reason `follow-me` is a 2D task. The floor, the sky and the lighting are upstream's, taken
from the `scene*.xml` wrappers in `microduck_rl`, so a Microduck here stands in the scene a
Microduck stands in there.

That palette and that detector cannot both have the head camera, which is why there are two
floors and why the head camera renders no sky: see `FLOOR_GROUP`.

Shadows are off unless `QUACKD_MUJOCO_SHADOWS` asks for them. They are upstream's default and
the single biggest render cost once a real robot is in the scene (366 ms per frame against
112 ms with `shadowsize="0"`, measured at 256 px on an Intel iGPU with the Microduck's 431k
triangles), and a GIF of a run needs tens of frames.

Bodies are strings the caller supplies, not files this module knows: the stand-in puppet
below is ours (a kinematic block, for tests and for anyone without the upstream meshes),
and the real Microduck arrives as an `<include>` plus an asset dict from the run-time cache,
with its own licence printed. Everything this module names is prefixed `quackd_`, because
an included robot brings its own names into the same document.
"""

from __future__ import annotations

import math
import os

from quackd.space.room import HEIGHT_M, Room

ARENA_HALF = 1.0  # metres; the arena is [-1, 1]², as in sim2d
DUCK_R = 0.08  # the body's radius, the cartoon's: what a room's walls hold the puppet off by
BALL_R = 0.05
WALL_H = 0.08
HEADCAM_FOV_DEG = 90.0  # the cartoon's, and the detector's default
OFFSCREEN_PX = 1024  # the largest --gif-size the offscreen buffer allows

#: The free camera upstream's own `scene*.xml` opens on, so `--live` and the recorded
#: overview frame it from the same side the Microduck's own scenes do.
VIEWER_AZIMUTH = 160.0
VIEWER_ELEVATION = -20.0
#: Shadows are upstream's default and they are the single biggest render cost once a real
#: robot is in the scene, so they are a choice rather than a constant. `QUACKD_MUJOCO_SHADOWS`
#: turns them on for a recording; a run that steers on rendered frames wants them off.
SHADOWS_ENV = "QUACKD_MUJOCO_SHADOWS"
SHADOWSIZE = 4096  # what upstream's viewer uses when shadows are on

#: There are two floors, and which one you see depends on who is looking.
#:
#: Upstream's blue-grey checker is the scene every operator view shows, because it is what a
#: Microduck's own `scene*.xml` looks like. It is also, to an HSV detector hunting a blue
#: person marker, a person: measured off a rendered frame, the floor sits at hue 105 with
#: saturation up to 185 and value up to 229, against the hue 114, saturation 185, value 197
#: that detector calls a person. They overlap on all three, so with only the pretty floor the
#: detector reported somebody standing 0.12 m ahead in every frame of every heading.
#:
#: No person stands in this arena now, and that makes the floor worse rather than harmless:
#: the detector still carries the person target, because the cartoon still has a person to
#: find, so every person it could report from here is a phantom and there is no true one to
#: weigh it against. So the head camera renders the same checker with the colour taken out,
#: and nothing else changes. It is a stand-in and it is listed as one. The honest defence of
#: it is that upstream's blue tiles are a *viewer* texture: the policies that ship with this
#: robot are blind, they never look at the floor, and a real Microduck's camera sees a room
#: rather than a scene file. Group 3 is hidden by MuJoCo everywhere by default, the live
#: viewer included, so only `render_headcam` ever turns it on.
FLOOR_GROUP = 1
FLOOR_CAM_GROUP = 3

BALL_RGBA = "1 0.55 0 1"  # (255, 140, 0): H≈16 in OpenCV, inside the detector's ball range
# A room's walls and its furniture, in the cartoon's greys (`quackd.sim2d.render.ROOM_WALL`):
# far below the saturation every detector band starts at, lit or not.
ROOM_WALL_RGBA = "0.588 0.565 0.533 1"  # (150, 144, 136)
FURNITURE_RGBA = "0.463 0.431 0.4 1"  # (118, 110, 102)
DUCK_RGBA = "0.98 0.82 0.16 1"  # the cream colorway

#: Where a held ball is parked: outside the walls, beyond what a 256 px frame can resolve.
BALL_PARK = (6.0, 6.0, BALL_R)

# Rolling friction slows the ball, as it does a real one: the coefficient is a lever arm
# in metres, so a rolling sphere decelerates at 2.5·coef·g/r, and 0.002 gives the cartoon's
# 1 m/s² (a 1.2 m/s kick travels about 0.8 m). Joint damping would brake the spin too,
# which stops a rolling ball dead in a few centimetres.
BALL_ROLLING = 0.002
BALL_MASS = 0.05

#: The kinematic stand-in. A mocap body is placed, not simulated: it collides with the ball
#: and nothing collides back, which is exactly what a test double for the plumbing wants.
#: Its trunk reaches the floor so a ball it walks into is shoved ahead, the way the
#: cartoon's contact push shoves it, rather than slipping underneath and being pinned.
PUPPET_BODY_Z = 0.125
PUPPET_SIT_Z = 0.07
PUPPET_HEAD_Z = 0.20  # the camera's height above the floor, the cartoon's figure
PUPPET_HEAD_AHEAD = 0.09  # the camera sits this far ahead of the centre: clear of the head
PUPPET_XML = f"""
    <body name="duck" mocap="true" pos="0 0 {PUPPET_BODY_Z}">
      <geom name="duck_body" type="box" size="0.05 0.04 0.062" pos="0 0 -0.063"
            group="2" rgba="{DUCK_RGBA}"/>
      <geom name="duck_head" type="box" size="0.03 0.03 0.03" pos="0.05 0 0.09"
            group="2" rgba="{DUCK_RGBA}"/>
      <geom name="duck_beak" type="box" size="0.02 0.012 0.008" pos="0.09 0 0.06"
            group="2" rgba="0.25 0.25 0.25 1"/>
    </body>
"""


def arena_xml(
    body_xml: str,
    *,
    ball: tuple[float, float],
    timestep: float = 0.005,
    include: str = "",
    shadows: bool | None = None,
    room: Room | None = None,
) -> str:
    """The whole scene. `include` goes first (a robot's own MJCF, merged by MuJoCo's
    `<include>`); `body_xml` is dropped into `<worldbody>` as given.

    `shadows` defaults to whatever `QUACKD_MUJOCO_SHADOWS` says, and off when it says
    nothing: see `SHADOWS_ENV`.

    `room` replaces the arena with a room's floor and outer walls, and adds its walls and its
    furniture (`room_geoms`). Without one, the arena exactly as it always was."""
    if shadows is None:
        shadows = os.environ.get(SHADOWS_ENV, "").strip().lower() in {"1", "true", "yes", "on"}
    shadowsize = SHADOWSIZE if shadows else 0
    castshadow = "true" if shadows else "false"
    if room is None:
        half_x = half_y = ARENA_HALF
        lim = ARENA_HALF + 0.02
        walls = "\n".join(
            f'    <geom name="quackd_wall_{name}" type="box" pos="{px} {py} {WALL_H}" '
            f'size="{sx} {sy} {WALL_H}" rgba="0.55 0.55 0.58 1"/>'
            for name, px, py, sx, sy in (
                ("east", lim, 0.0, 0.02, lim),
                ("west", -lim, 0.0, 0.02, lim),
                ("north", 0.0, lim, lim, 0.02),
                ("south", 0.0, -lim, lim, 0.02),
            )
        )
    else:
        half_x, half_y = room.half_w, room.half_h
        walls = room_geoms(room)
    return f"""
<mujoco model="quackd arena">
{include}
  <option timestep="{timestep}" gravity="0 0 -9.81"/>
  <visual>
    <global fovy="{HEADCAM_FOV_DEG:g}" offwidth="{OFFSCREEN_PX}" offheight="{OFFSCREEN_PX}"
            azimuth="{VIEWER_AZIMUTH:g}" elevation="{VIEWER_ELEVATION:g}"/>
    <headlight ambient="0.3 0.3 0.3" diffuse="0.6 0.6 0.6" specular="0 0 0"/>
    <rgba haze="0.15 0.25 0.35 1"/>
    <quality shadowsize="{shadowsize}" offsamples="0"/>
    <!-- No antialiasing: a blended edge pixel on the orange ball lands in the
         detector's cream band and reads as a second duck. The cartoon draws flat
         fills for the same reason. -->
  </visual>
  <asset>
    <!-- Upstream's own scene palette, from the `scene*.xml` wrappers in microduck_rl. -->
    <texture name="quackd_floor" type="2d" builtin="checker" mark="edge" width="300" height="300"
             rgb1="0.2 0.3 0.4" rgb2="0.1 0.2 0.3" markrgb="0.8 0.8 0.8"/>
    <material name="quackd_floor" texture="quackd_floor" texuniform="true" texrepeat="5 5"
              reflectance="0.2"/>
    <!-- The same checker with the colour taken out, for the head camera alone. See
         FLOOR_GROUP below: to an HSV detector this blue floor is a person, and with nobody
         standing here every person it could report would be that floor. -->
    <texture name="quackd_floor_cam" type="2d" builtin="checker" mark="edge" width="300"
             height="300" rgb1="0.62 0.62 0.62" rgb2="0.50 0.50 0.50" markrgb="0.85 0.85 0.85"/>
    <material name="quackd_floor_cam" texture="quackd_floor_cam" texuniform="true"
              texrepeat="5 5" reflectance="0"/>
    <texture name="quackd_sky" type="skybox" builtin="gradient" width="512" height="3072"
             rgb1="0.3 0.5 0.7" rgb2="0 0 0"/>
  </asset>
  <worldbody>
    <light name="quackd_sun" pos="0 0 3.5" dir="0 0 -1" directional="true"
           castshadow="{castshadow}"/>
    <geom name="quackd_floor" type="plane" group="{FLOOR_GROUP}"
          size="{half_x + 0.5:g} {half_y + 0.5:g} 0.05"
          material="quackd_floor" friction="0.8 0.005 0.0001"/>
    <geom name="quackd_floor_cam" type="plane" group="{FLOOR_CAM_GROUP}" contype="0"
          conaffinity="0" size="{half_x + 0.5:g} {half_y + 0.5:g} 0.05"
          material="quackd_floor_cam"/>
{walls}
    <body name="ball" pos="{ball[0]} {ball[1]} {BALL_R}">
      <joint name="ball_free" type="free"/>
      <geom name="ball_geom" type="sphere" size="{BALL_R}" rgba="{BALL_RGBA}" condim="6"
            mass="{BALL_MASS}" friction="0.8 0.005 {BALL_ROLLING}"/>
    </body>
{body_xml}
  </worldbody>
</mujoco>
"""


ROOM_GEOM_PREFIX = "quackd_room_"
"""What every geom of a room's is named from, so a world can tell its walls from everything else."""


def room_geoms(room: Room) -> str:
    """A room's outer walls, walls and furniture as MJCF geoms, each `HEIGHT_M` tall: every wall
    a box along its centre line with an upright cylinder at each end, which is the shape the room
    is written as (`quackd.space.room.Wall`), and every box a box. Turned by quaternion, which no
    `<compiler angle>` a robot's own MJCF brings can read another way."""
    h = HEIGHT_M / 2
    thick = 0.02
    lines: list[str] = []
    for name, px, py, sx, sy in (
        ("east", room.half_w + thick, 0.0, thick, room.half_h + 2 * thick),
        ("west", -room.half_w - thick, 0.0, thick, room.half_h + 2 * thick),
        ("north", 0.0, room.half_h + thick, room.half_w + 2 * thick, thick),
        ("south", 0.0, -room.half_h - thick, room.half_w + 2 * thick, thick),
    ):
        lines.append(
            f'    <geom name="{ROOM_GEOM_PREFIX}outer_{name}" type="box" pos="{px:g} {py:g} {h:g}" '
            f'size="{sx:g} {sy:g} {h:g}" rgba="{ROOM_WALL_RGBA}"/>'
        )
    for i, wall in enumerate(room.walls):
        half_t = wall.thickness / 2
        length = math.hypot(wall.x1 - wall.x0, wall.y1 - wall.y0)
        if length > 0:
            yaw = math.atan2(wall.y1 - wall.y0, wall.x1 - wall.x0)
            cx, cy = (wall.x0 + wall.x1) / 2, (wall.y0 + wall.y1) / 2
            # to twelve figures: at `:g`'s six a wall written at 45 degrees stood at 44.9999
            turn = f"{math.cos(yaw / 2):.12g} 0 0 {math.sin(yaw / 2):.12g}"
            lines.append(
                f'    <geom name="{ROOM_GEOM_PREFIX}wall_{i}" type="box" '
                f'pos="{cx:g} {cy:g} {h:g}" quat="{turn}" '
                f'size="{length / 2:g} {half_t:g} {h:g}" rgba="{ROOM_WALL_RGBA}"/>'
            )
        for end, (ex, ey) in enumerate(((wall.x0, wall.y0), (wall.x1, wall.y1))):
            lines.append(
                f'    <geom name="{ROOM_GEOM_PREFIX}wall_{i}_end_{end}" type="cylinder" '
                f'pos="{ex:g} {ey:g} {h:g}" size="{half_t:g} {h:g}" rgba="{ROOM_WALL_RGBA}"/>'
            )
    for i, box in enumerate(room.boxes):
        lines.append(
            f'    <geom name="{ROOM_GEOM_PREFIX}box_{i}" type="box" '
            f'pos="{box.cx:g} {box.cy:g} {h:g}" size="{box.w / 2:g} {box.d / 2:g} {h:g}" '
            f'rgba="{FURNITURE_RGBA}"/>'
        )
    return "\n".join(lines)
