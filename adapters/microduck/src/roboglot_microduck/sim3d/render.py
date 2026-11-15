"""Two views of the physics world: what the head sees, and the duck from over its shoulder.

Both are free cameras placed by hand rather than the MJCF's own `<camera>` elements. That
is not a shortcut: upstream's `head_camera` carries a quaternion that is not MuJoCo's
viewing convention, so rendering through it looks backwards into the duck's own shell. The
body reports where its camera is and which way it faces, and this module points a free
camera there, at the field of view the detector assumes (`scene.HEADCAM_FOV_DEG`), so
bearings and distances mean what they mean in sim2d.

The head view hides geom group 2 — every mesh of the robot's own shell, and the puppet's
boxes. It is what a camera on the head sees anyway, and it is the difference between 3 ms
and 400 ms a frame with upstream's 431k triangles in the scene.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Any

import numpy as np
from PIL import Image

if TYPE_CHECKING:
    from roboglot_microduck.sim3d.world import MujocoWorld

#: Over the duck's shoulder: close enough that a 25 cm robot reads, wide enough to place it.
OVERVIEW_DISTANCE = 1.05
OVERVIEW_ELEVATION_DEG = -22.0
OVERVIEW_AZIMUTH_DEG = 125.0
OVERVIEW_HEIGHT = 0.12
OVERVIEW_FOV_DEG = 42.0  # the head camera's 90 is the detector's, not a viewer's
ROBOT_VISUAL_GROUP = 2


def _free_camera(
    lookat: tuple[float, float, float], distance: float, azimuth_deg: float, elevation_deg: float
) -> Any:
    import mujoco

    cam = mujoco.MjvCamera()
    cam.type = mujoco.mjtCamera.mjCAMERA_FREE
    cam.lookat[:] = lookat
    cam.distance = distance
    cam.azimuth = azimuth_deg
    cam.elevation = elevation_deg
    return cam


def _head_camera_view() -> Any:
    """What the robot's own camera is allowed to see.

    The shell goes, because a camera bolted to the head does not see the head, and because it
    is the difference between 3 ms and 400 ms a frame. The floor is swapped for the colourless
    copy of itself: upstream's blue-grey checker is what every operator view shows, and it is
    also, to an HSV detector hunting a blue person marker, a person in every frame. Nobody
    stands in this arena, so that would be a phantom every time. See `scene.FLOOR_GROUP`.
    """
    import mujoco

    from roboglot_microduck.sim3d.scene import FLOOR_CAM_GROUP, FLOOR_GROUP

    option = mujoco.MjvOption()
    option.geomgroup[ROBOT_VISUAL_GROUP] = 0
    option.geomgroup[FLOOR_GROUP] = 0
    option.geomgroup[FLOOR_CAM_GROUP] = 1
    return option


def render_headcam(world: MujocoWorld, size: int = 256) -> Image.Image:
    """First person: the camera sits at the head and looks along the head's yaw and pitch.

    A free camera sits `distance` behind its `lookat` along the view direction, so putting
    the lookat half a metre ahead of the head and the distance at half a metre places the
    eye exactly on the head.
    """
    x, y, z, yaw, pitch = world.head_pose()
    ahead = 0.5
    forward = (
        math.cos(pitch) * math.cos(yaw),
        math.cos(pitch) * math.sin(yaw),
        math.sin(pitch),
    )
    lookat = (x + ahead * forward[0], y + ahead * forward[1], z + ahead * forward[2])
    cam = _free_camera(lookat, ahead, math.degrees(yaw), math.degrees(pitch))
    # No skybox. Upstream's gradient is a saturated blue at the horizon, and above a 8 cm
    # wall it fills the top half of this frame, where the detector reads it as a person for
    # the same reason the floor is one. What is left is the model's flat background, which
    # is what a camera in a 2 m arena has any business seeing.
    return _render(world, size, cam, _head_camera_view(), skybox=False)


def render_overview(world: MujocoWorld, size: int = 256) -> Image.Image:
    """The duck in its arena, from a fixed bearing that follows it: the GIF's left pane.

    A free camera takes its field of view from the model, and the model's is the detector's
    90 degrees, which is right for the head and makes a 25 cm robot a speck from a metre
    away. So this one borrows the model for the length of a frame and puts it back.
    """
    cam = _free_camera(
        (world.x, world.y, OVERVIEW_HEIGHT),
        OVERVIEW_DISTANCE,
        OVERVIEW_AZIMUTH_DEG,
        OVERVIEW_ELEVATION_DEG,
    )
    was = float(world.model.vis.global_.fovy)
    world.model.vis.global_.fovy = OVERVIEW_FOV_DEG
    try:
        return _render(world, size, cam, None)
    finally:
        world.model.vis.global_.fovy = was


def _render(
    world: MujocoWorld, size: int, cam: Any, option: Any, *, skybox: bool = True
) -> Image.Image:
    import mujoco

    renderer = world.renderer(size)
    if option is None:
        renderer.update_scene(world.data, camera=cam)
    else:
        renderer.update_scene(world.data, camera=cam, scene_option=option)
    # After `update_scene`, which resets the scene's own flags from the model.
    renderer.scene.flags[mujoco.mjtRndFlag.mjRND_SKYBOX] = 1 if skybox else 0
    pixels = np.asarray(renderer.render())
    return Image.fromarray(pixels, "RGB")
