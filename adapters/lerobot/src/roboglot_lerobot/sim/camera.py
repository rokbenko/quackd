"""The arm simulator's cameras: the scene's mounts, rendered when somebody reads one.

The real backend reads a webcam through three calls, `connect()`, `read_latest()` with no
arguments (`upstream_api.CAMERA_READ_LATEST`) and `disconnect()`, and it makes each of them
from a worker thread, because a webcam blocks. A camera here answers the same three, so the
backend's own camera code, its refusals and its health rows run over it unchanged. What it
shows is one of the scene's mounts (`model.MOUNTS`), named by the url's `?name=`: the index a
webcam is opened by means nothing here and is ignored, and width, height, rotation and the
lens's field of view apply as they would.

Every GL call, making a renderer, rendering and freeing one, happens on the event loop's
thread, the one path proven on Windows, where a GL context belongs to the thread that made
it. So a read from a worker thread hops to the loop to render and waits there for the frame.
It renders only when the frame it holds is older than the world: staleness is counted in
physics steps, and while nobody sleeps on the clock time stands still, so a pilot reading the
same view twice between two sleeps is handed the same frame and pays for one render.

The field of view a url gives, or the detector's default without one, is horizontal, as the
detector measures bearings (`perception.color_blob.DEFAULT_FOV_DEG`). MuJoCo's is vertical, so
the camera is given the vertical angle that makes the requested horizontal one across the frame
it renders, and the horizontal one is what the transport publishes. Shadows and reflections are
off: each costs a render time, and a shadow is a dark patch a detector could take for something.

Nothing here imports `mujoco` when the module is imported.
"""

from __future__ import annotations

import asyncio
import math
from collections.abc import Callable
from typing import Any, TypeVar

import numpy as np

from roboglot.perception.color_blob import DEFAULT_FOV_DEG
from roboglot.transport.base import TransportError
from roboglot_lerobot.real import CameraSpec
from roboglot_lerobot.sim.model import LABEL, MOUNTS
from roboglot_lerobot.sim.world import ArmWorld

T = TypeVar("T")

CAMERA_HINT = (
    f"A camera here is one of the scene's mounts, {', '.join(MOUNTS)}, named with ?name= in "
    "the opencv:// url a webcam takes: the index is ignored, and width, height, rotation and "
    "fov apply, fov being the horizontal view in degrees"
)
"""What a refused `--camera-url` is told a camera is on the simulator
(`real.parse_camera_url`'s `hint`)."""
RENDER_WAIT_S = 10.0
"""How long a read in a worker thread waits for the event loop to render its frame: far past
what a render costs, and short of leaving a thread waiting for good on a loop that has
stopped."""
ROTATIONS = {0: 0, 90: -1, 180: 2, 270: 1}
"""Each rotation a url can ask for, as the quarter turns `numpy.rot90` makes, which turns
counterclockwise: 90 is clockwise, as the real backend asks OpenCV for it."""


class RenderError(TransportError):
    """No GL context to render in, or a camera the scene does not have."""


def vertical_fov(fov_deg: float, width: int, height: int, rotation: int) -> float:
    """MuJoCo's vertical field of view, in degrees, that shows `fov_deg` across a frame
    `width` by `height` after `rotation`. A quarter turn makes the render's vertical the
    frame's horizontal, so the angle is then the horizontal one as it is."""
    if rotation in (90, 270):
        return fov_deg
    half = math.radians(fov_deg) / 2
    return math.degrees(2 * math.atan(math.tan(half) * height / width))


def open_renderer(world: ArmWorld, width: int, height: int) -> Any:
    """A renderer `width` by `height` over the world's own model, with shadows and reflections
    off. On the event loop's thread only, as every GL call here is.

    The world's model is its own copy, so the offscreen buffer it asks MuJoCo for is widened
    there to fit the frame, and no other world's model changes."""
    import mujoco

    with world.locked() as (model, _):
        buffer = model.vis.global_
        buffer.offwidth = max(int(buffer.offwidth), width)
        buffer.offheight = max(int(buffer.offheight), height)
        try:
            renderer = mujoco.Renderer(model, height=height, width=width)
        except Exception as e:
            raise RenderError(
                f"{LABEL} there is no OpenGL context to render the scene in "
                f"({type(e).__name__}: {e}). On a headless Linux box install libosmesa6 and set "
                "MUJOCO_GL=osmesa, or MUJOCO_GL=egl where there is a GPU."
            ) from e
    renderer.scene.flags[mujoco.mjtRndFlag.mjRND_SHADOW] = False
    renderer.scene.flags[mujoco.mjtRndFlag.mjRND_REFLECTION] = False
    return renderer


def render(renderer: Any, world: ArmWorld, camera: int) -> np.ndarray:
    """One frame from `camera`, HxWx3 uint8 RGB. The scene is read under the world's lock,
    which a step also takes, and drawn outside it, which is the slow part."""
    with world.locked() as (_, data):
        renderer.update_scene(data, camera=camera)
    return np.asarray(renderer.render())


class SimCamera:
    """One of the scene's mounts, read as the real backend reads a webcam.

    Built with the url's spec, the world, the mount it shows and the event loop every GL call
    is made on. A mount the scene does not have is refused with the ones it does. The frame is
    the spec's width and height, which are the frame's after its rotation, as LeRobot reads
    them. Without them it is the loaded model's own offscreen size, turned by the rotation, so
    a quarter turn hands it over on its side as a webcam's own mode would be."""

    def __init__(
        self, spec: CameraSpec, world: ArmWorld, mount: str, loop: asyncio.AbstractEventLoop
    ) -> None:
        model = world.arm.model  # as loaded: the world's copy may have a wider buffer by now
        names = [model.camera(i).name for i in range(model.ncam)]
        if mount not in names:
            raise RenderError(
                f"{LABEL} the scene has no camera called {mount!r}; its cameras are "
                f"{', '.join(names) or 'none'}. Name one with ?name= in the --camera-url."
            )
        self.spec = spec
        self.world = world
        self.mount = mount
        self._camera = names.index(mount)
        self._loop = loop
        turned = spec.rotation in (90, 270)
        if spec.width and spec.height:
            self.width, self.height = spec.width, spec.height
        else:
            # a webcam asked for no size keeps its own mode, and a quarter turn hands that mode
            # over on its side (`upstream_api.OPENCV_MODE_DEFAULTS_TO_THE_CAMERA`): the model's
            # offscreen size is this camera's own mode
            mode = (int(model.vis.global_.offwidth), int(model.vis.global_.offheight))
            self.width, self.height = mode[::-1] if turned else mode
        self._turns = ROTATIONS[spec.rotation]
        self._render_size = (self.height, self.width) if turned else (self.width, self.height)
        self.fov_deg = spec.fov_deg if spec.fov_deg is not None else DEFAULT_FOV_DEG
        """The horizontal field of view, in degrees, across the frame as it is handed over."""
        self.fovy_deg = vertical_fov(self.fov_deg, self.width, self.height, spec.rotation)
        """MuJoCo's vertical one, which the mount is given for the frame it renders."""
        self._renderer: Any = None
        self._frame: tuple[int, np.ndarray] | None = None

    @property
    def is_connected(self) -> bool:
        return self._renderer is not None

    def connect(self) -> None:
        """Make the renderer, give the mount its field of view, and render a first frame, as a
        webcam's connect reads frames before it returns (`upstream_api.CAMERA_CONNECT`)."""
        self._on_loop(self._open)

    def read_latest(self) -> np.ndarray:
        """The newest frame, HxWx3 uint8 RGB: the one held while the world has not stepped
        since it was rendered, and a fresh render otherwise."""
        held = self._frame
        if self._renderer is not None and held is not None and held[0] >= self._step():
            return held[1]
        return self._on_loop(self._latest)

    def disconnect(self) -> None:
        """Free the renderer. A camera that is not connected has nothing to free."""
        self._on_loop(self._close)

    # ── on the event loop's thread ──────────────────────────────────────────────────────

    def _open(self) -> None:
        if self._renderer is not None:
            return
        width, height = self._render_size
        self._renderer = open_renderer(self.world, width, height)
        with self.world.locked() as (model, _):
            model.cam_fovy[self._camera] = self.fovy_deg
        self._frame = None
        self._latest()

    def _latest(self) -> np.ndarray:
        if self._renderer is None:
            raise RuntimeError(
                f"{LABEL} the {self.mount} camera is not connected, so it has no frame."
            )
        step = self._step()
        held = self._frame
        if held is not None and held[0] >= step:
            return held[1]
        pixels = render(self._renderer, self.world, self._camera)
        frame = np.ascontiguousarray(np.rot90(pixels, self._turns))
        self._frame = (step, frame)
        return frame

    def _close(self) -> None:
        renderer, self._renderer = self._renderer, None
        self._frame = None
        if renderer is not None:
            renderer.close()

    # ── plumbing ────────────────────────────────────────────────────────────────────────

    def _step(self) -> int:
        """The world's physics step: how old a frame is, is counted in these."""
        return round(self.world.t / self.world.timestep)

    def _on_loop(self, fn: Callable[[], T]) -> T:
        """`fn`, run on the event loop's thread, where every GL call is made, and waited for.
        Run in place when this already is that thread, which waiting on it would deadlock."""
        try:
            running: asyncio.AbstractEventLoop | None = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is self._loop:
            return fn()

        async def call() -> T:
            return fn()

        return asyncio.run_coroutine_threadsafe(call(), self._loop).result(RENDER_WAIT_S)
