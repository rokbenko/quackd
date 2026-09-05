"""Features, not frames.

Mirroring upstream's principle, the LLM sees "ball at bearing 12° left, ~0.8 m", not pixels,
and the steering loop closes on detections at ~10 Hz without an LLM in the way. This package
turns images into that.
"""

from __future__ import annotations

import logging
from collections.abc import Container
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from quackd.perception.base import Detector

__all__ = ["detector_for"]

log = logging.getLogger("quackd.perception")

#: Backends whose camera really is the one the default geometry assumes.
_SIMULATED = ("sim2d", "mock")


def detector_for(
    sensors: Container[str],
    current: Detector | None = None,
    *,
    fov_deg: float | None = None,
    backend: str | None = None,
) -> Detector | None:
    """A robot with a camera needs something to look at its frames with. Any robot.

    Keying this on the backend being `sim2d` is the bug that made every hardware body run
    blind: it fetched a frame, detected nothing because nothing was detecting, and reported
    that it could not see. 0.5 fixed that in `quackd run` and missed `serve-mcp`, which is
    why the decision now lives in one function that both entry points call.

    Call it with what the robot said when it *connected*, not with its description. A
    rosbridge base has no camera in its static manifest and may well have one in its live
    one, and the description of a fully built duck promises a camera the duck in front of
    you may not have been built with.

    `fov_deg` is the horizontal field of view of the lens actually in front of you. The
    default is the simulator's 90 degrees, and a real Pi camera module is nearer 62, which
    makes the focal length half what it should be: bearings come out inflated by about 1.7x
    and distances short by about 40 percent. `go_to` then announces it has arrived while the
    duck is still half a metre out — outside `open-duck-scout`'s own success criterion, so
    the run reports success and the ground truth says it failed. There was no flag, env var
    or manifest key to correct it; `docs/faq.md` said to edit quackd's source.
    """
    if current is not None or "camera" not in sensors:
        return current
    from quackd.perception.color_blob import DEFAULT_FOV_DEG, ColorBlobDetector

    simulated = backend is None or backend in _SIMULATED
    if fov_deg is not None:
        return ColorBlobDetector(fov_deg=fov_deg)
    if simulated:
        return ColorBlobDetector()
    # A real lens, and nobody said which. The geometry still works — the target is in the
    # right direction — but the numbers are the wrong size, and saying so beats a confident
    # measurement nobody can act on.
    log.warning(
        "no camera field of view given for a %s camera, so detections use the simulator's "
        "%.0f degrees. Distances will be out by tens of percent: pass --fov-deg (a Pi Camera "
        "Module 2 is about 62) once you know yours.",
        backend,
        DEFAULT_FOV_DEG,
    )
    return ColorBlobDetector(calibrated=False)
