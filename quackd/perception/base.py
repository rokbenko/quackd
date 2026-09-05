"""The shape of "what the duck sees", independent of how it was seen.

`Detection` is the only thing verbs and the LLM consume. Whether it came from an HSV
threshold in sim, a YOLO model on a laptop, or — one day — upstream `mediad`'s feature
stream, the rest of quackd does not care.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from PIL import Image
from pydantic import BaseModel, Field


class Detection(BaseModel):
    label: str
    cx: float = Field(..., description="Centre x in [0,1] of image width.")
    cy: float = Field(..., description="Centre y in [0,1] of image height.")
    area: float = Field(..., description="Fraction of the image covered, in [0,1].")
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    bearing_deg: float | None = Field(
        default=None,
        description="Positive = left of the duck's heading (upstream's +yaw convention).",
    )
    est_distance_m: float | None = None
    calibrated: bool = Field(
        default=True,
        description="False when the camera model is a guess, so bearing and distance are "
        "the right shape but the wrong size.",
    )

    def summary(self) -> str:
        parts = [self.label]
        if self.bearing_deg is not None:
            side = "left" if self.bearing_deg > 0 else "right"
            mag = abs(self.bearing_deg)
            parts.append("dead ahead" if mag < 3 else f"at bearing {mag:.0f}° {side}")
        if self.est_distance_m is not None:
            parts.append(f"~{self.est_distance_m:.2f} m")
        # Geometry is only as good as the lens it assumes. On a real camera at the default
        # field of view the distance is out by tens of percent, which is the difference
        # between `go_to` arriving and announcing it from half a metre away — so the pilot
        # is told, rather than being handed a number that looks measured.
        if not self.calibrated:
            parts.append("(uncalibrated: distance is a rough guess)")
        return " ".join(parts)


@runtime_checkable
class Detector(Protocol):
    name: str

    def detect(self, image: Image.Image) -> list[Detection]: ...


def summarize_detections(detections: list[Detection]) -> str:
    if not detections:
        return "nothing detected"
    return "; ".join(d.summary() for d in detections)
