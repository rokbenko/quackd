"""MuJoCo's own passive viewer as the `--live` window.

The cartoon opens a pygame window because it has nothing else; the physics world has a
viewer upstream already, with orbit, pan and a contact overlay, so this is a thin hook that
keeps it in step with the clock and turns its close button into the same interrupt the
pygame window raises. Kept out of the default path like `sim2d/live.py`.
"""

from __future__ import annotations

import sys
from typing import Any

from roboglot.transport.base import TransportError


class LiveViewer:
    def __init__(self, world: Any) -> None:
        import mujoco.viewer

        try:
            self.handle = mujoco.viewer.launch_passive(
                world.model, world.data, show_left_ui=False, show_right_ui=False
            )
        except RuntimeError as e:  # pragma: no cover - macOS main-thread rule
            hint = (
                " On macOS the viewer must own the main thread: run the same command "
                "under `mjpython` (installed with mujoco) instead of `python`."
                if sys.platform == "darwin"
                else ""
            )
            raise TransportError(f"could not open the MuJoCo viewer: {e}.{hint}") from e
        self.handle.cam.distance = 2.5
        self.handle.cam.elevation = -30
        self.handle.cam.azimuth = 135

    def sync(self, _world: Any) -> None:
        """A tick hook. Closing the window stops the run, as the pygame one does."""
        if not self.handle.is_running():
            raise KeyboardInterrupt
        self.handle.sync()

    def close(self) -> None:
        self.handle.close()
