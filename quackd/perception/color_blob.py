"""The default detector: an HSV threshold. No model, no download, ~1 ms per frame.

The duck's two simulators draw the ball in a known orange, and the cartoon draws its person in
a known blue, so this works out of the box. The person target stays for the cartoon's sake:
nobody stands in the duck's physics arena, which is why that one renders its head camera over a
colourless floor rather than let upstream's blue checker forge the target nothing there can
match. The arm's simulator draws nothing in a detector's colour unless a sidecar names one. On a
real camera you tune the HSV ranges to *your* ball (see
`docs/faq.md`); the geometry — bearing from horizontal position, distance from apparent
size — is the same either way, which is the whole point of sharing one perception path.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import cv2
import numpy as np
from PIL import Image

from quackd.perception.base import Detection

DEFAULT_FOV_DEG = 90.0  # matches the sim camera; a real IMX219 is ~62°


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
    Target("ball", HSVRange(5, 22), 0.05, round=True),  # sim orange (255,140,0) → H≈16
    Target("person", HSVRange(100, 130), 0.12, round=False),  # sim blue (60,90,220) → H≈112
    Target("pet", HSVRange(50, 80), 0.10, round=False),  # sim green (60,180,80) → H≈65
    # the four Microduck colorways, one Target each so a flock member can see its peers
    # (max_per_label applies per Target, so this yields up to one blob per colorway)
    Target("duck", HSVRange(23, 34), 0.08, round=False),  # cream (250,210,40) → H≈24
    Target("duck", HSVRange(86, 98), 0.08, round=False),  # sky (70,210,225) → H≈93
    Target("duck", HSVRange(133, 148), 0.08, round=False),  # lavender (185,105,235) → H≈138
    Target("duck", HSVRange(152, 172), 0.08, round=False),  # graphite (140,60,110) → H≈161
)


@dataclass
class ColorBlobDetector:
    name: str = "color_blob"
    targets: tuple[Target, ...] = DEFAULT_TARGETS
    fov_deg: float = DEFAULT_FOV_DEG
    calibrated: bool = True
    """False when `fov_deg` is the simulator default on a real lens, or a lens the body
    assumed rather than measured."""
    min_area_px: int = 12
    max_per_label: int = 1
    extra: dict[str, int] = field(default_factory=dict)
    lens_at_connect: bool = False
    """True on one `detector_for` built, which may be before the body connected, whose lens the
    loop and the MCP server set again from what the body reported. One built anywhere else, such
    as one handed to a run from Python, keeps the lens it was built with."""
    _told_lens: str | None = field(default=None, init=False, repr=False, compare=False)

    @property
    def labels(self) -> frozenset[str]:
        """The labels its targets give (`labels_of`)."""
        return frozenset(target.label for target in self.targets)

    def calibrate(
        self, fov_deg: float | None, *, backend: str | None, assumed: str | None = None
    ) -> None:
        """Set the lens from what is known about the camera in front of the run, by the rule
        the other detectors follow (`perception.lens`), and say once when nobody measured it."""
        from quackd.perception import lens, tell_lens

        self.fov_deg, self.calibrated = lens(
            fov_deg, backend, guess=DEFAULT_FOV_DEG, assumed=assumed
        )
        tell_lens(
            self,
            backend,
            f"no camera field of view given for a {backend} camera, so detections use the "
            f"simulator's {self.fov_deg:.0f} degrees. Distances will be out by tens of percent: "
            "pass --fov-deg (a Pi Camera Module 2 is about 62) once you know yours.",
            assumed=assumed,
        )

    def detect(self, image: Image.Image) -> list[Detection]:
        rgb = np.asarray(image.convert("RGB"))
        h, w = rgb.shape[:2]
        hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
        f = (w / 2) / math.tan(math.radians(self.fov_deg) / 2)
        out: list[Detection] = []
        for target in self.targets:
            r = target.hsv
            # uint8 arrays, not tuples: OpenCV accepts both, but the stubs only type the
            # array overload, and which stub version resolves depends on the Python minor
            lo = np.array([r.h_lo, r.s_lo, r.v_lo], dtype=np.uint8)
            hi = np.array([r.h_hi, 255, 255], dtype=np.uint8)
            mask = cv2.inRange(hsv, lo, hi)
            contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            blobs = sorted(contours, key=cv2.contourArea, reverse=True)[: self.max_per_label]
            for c in blobs:
                area = float(cv2.contourArea(c))
                if area < self.min_area_px:
                    continue
                x, y, bw, bh = cv2.boundingRect(c)
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
