"""The default detector: an HSV threshold. No model, no download, a few ms per frame.

A mock's camera paints the ball in a known orange (`roboglot.perception.palette`), so this works
out of the box there. On a real camera you tune the HSV range to *your* ball (see
`docs/faq.md`); the geometry — bearing from horizontal position, distance from apparent
size — is the same either way, which is the whole point of sharing one perception path.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
from PIL import Image

from roboglot.perception.base import Detection
from roboglot.perception.blobs import Candidates, biggest_blobs

DEFAULT_FOV_DEG = 90.0  # matches a mock's camera; a real IMX219 is ~62°


@dataclass(frozen=True)
class HSVRange:
    """OpenCV HSV: H in [0,180), S and V in [0,255]."""

    h_lo: int
    h_hi: int
    s_lo: int = 120
    v_lo: int = 120


@dataclass(frozen=True)
class Target:
    label: str
    hsv: HSVRange
    size_m: float
    """Radius for round things, half-width for upright things."""
    round: bool = True


DEFAULT_TARGETS: tuple[Target, ...] = (
    Target("ball", HSVRange(5, 22), 0.05, round=True),  # the palette's orange (255,140,0) → H≈16
)


@dataclass
class ColorBlobDetector:
    name: str = "color_blob"
    targets: tuple[Target, ...] = DEFAULT_TARGETS
    fov_deg: float = DEFAULT_FOV_DEG
    calibrated: bool = True
    """False when `fov_deg` is the default on a real lens, or a lens the body
    assumed rather than measured."""
    min_area_px: int = 12
    max_per_label: int = 1
    extra: dict[str, int] = field(default_factory=dict)
    lens_at_connect: bool = False
    """True on one `detector_for` built, which may be before the body connected, whose lens the
    loop and the MCP server set again from what the body reported. One built anywhere else, such
    as one handed to a run from Python, keeps the lens it was built with."""
    _told_lens: str | None = field(default=None, init=False, repr=False, compare=False)

    def calibrate(
        self, fov_deg: float | None, *, backend: str | None, assumed: str | None = None
    ) -> None:
        """Set the lens from what is known about the camera in front of the run, by the rule
        the other detectors follow (`perception.lens`), and say once when nobody measured it."""
        from roboglot.perception import lens, tell_lens

        self.fov_deg, self.calibrated = lens(
            fov_deg, backend, guess=DEFAULT_FOV_DEG, assumed=assumed
        )
        tell_lens(
            self,
            backend,
            f"no camera field of view given for a {backend} camera, so detections use the "
            f"default {self.fov_deg:.0f} degrees. Distances will be out by tens of percent: "
            "pass --fov-deg (a Pi Camera Module 2 is about 62) once you know yours.",
            assumed=assumed,
        )

    def detect(self, image: Image.Image) -> list[Detection]:
        rgb = np.asarray(image.convert("RGB"))
        h, w = rgb.shape[:2]
        f = (w / 2) / math.tan(math.radians(self.fov_deg) / 2)
        out: list[Detection] = []
        if not self.targets:
            return out
        pixels = Candidates(
            rgb, min(t.hsv.s_lo for t in self.targets), min(t.hsv.v_lo for t in self.targets)
        )
        for target in self.targets:
            r = target.hsv
            found = pixels.matching(r.h_lo, r.h_hi, r.s_lo, r.v_lo)
            if found is None:
                continue
            mask, top, left = found
            for area, (x, y, bw, bh) in biggest_blobs(mask, self.max_per_label, self.min_area_px):
                x, y = x + left, y + top
                cx, cy = x + bw / 2, y + bh / 2
                bearing = -math.degrees(math.atan((cx - w / 2) / f))
                if target.round:
                    radius_px = max(1.0, math.sqrt(area / math.pi))
                    # a ball clipped by the frame edge looks smaller than it is
                    if y + bh >= h - 1 or x <= 0 or x + bw >= w - 1:
                        radius_px = max(radius_px, bw / 2)
                else:
                    radius_px = max(1.0, bw / 2)
                dist = f * target.size_m / radius_px
                out.append(
                    Detection(
                        label=target.label,
                        cx=cx / w,
                        cy=cy / h,
                        area=area / (w * h),
                        confidence=min(1.0, 0.5 + area / 400.0),
                        calibrated=self.calibrated,
                        bearing_deg=round(bearing, 1),
                        est_distance_m=round(dist, 3),
                    )
                )
        out.sort(key=lambda d: (d.label != "ball", d.est_distance_m or 0.0))
        return out
