"""The shape of "what the duck sees", independent of how it was seen.

`Detection` is the only thing verbs and the LLM consume. Whether it came from an HSV
threshold in sim, a YOLO model on a laptop, or — one day — upstream `mediad`'s feature
stream, the rest of quackd does not care.
"""

from __future__ import annotations

import asyncio
from typing import Any, Protocol, runtime_checkable

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


def labels_of(detector: object) -> frozenset[str] | None:
    """The labels `detector` can find, when it says (`labels`), or None for one that does not:
    a verb that looks for one thing refuses a label the detector never gives before anything
    moves, rather than search a room for it."""
    labels = getattr(detector, "labels", None)
    if isinstance(labels, frozenset | set | tuple | list) and labels:
        return frozenset(str(label) for label in labels)
    return None


async def detect_off_loop(detector: Detector, image: Image.Image) -> list[Detection]:
    """`detector.detect(image)`, in a worker thread when the detector says it waits on
    something outside this process.

    Such a detector sets `blocking = True`; today that is `HostDetector`, whose model runs on
    the board `--host` names. Called on the event loop, its round trip would hold up
    everything else the loop runs, the twist `go_to` re-sends while it looks among them, so a
    board slower than a body's deadman would stop the body on every frame. Every other
    detector runs where it always has, on the loop, so a run without a board is scheduled
    exactly as it was."""
    if getattr(detector, "blocking", False):
        return await asyncio.to_thread(detector.detect, image)
    return detector.detect(image)


def detect_times(detector: Detector | None) -> dict[str, Any]:
    """The `detect` key of the record that closes a stretch of detecting: what the frames
    `detector` was sent since the last such record cost, from a detector that keeps those times
    (the board's, `HostDetector.take_timing`), or nothing from one that keeps none or was sent
    no frame.

    Taken at each `observation` the loop makes and at each `verb_end`, which the executor
    writes however a verb ends, so no frame is on two records and every frame of a verb's
    steering loop is on that verb's, which is where a slow round trip to a board shows. A
    composite's record has the frames it sent itself, and the verbs it called have their own.
    A frame still in flight when an abort cancels its verb lands on the next record, or on none
    once the run has ended. The count is one per detector, so two verbs running at once, which an
    MCP client can start and a run's loop never does, share it, and whichever ends first takes
    the frames of both so far."""
    take = getattr(detector, "take_timing", None)
    times = take() if callable(take) else None
    return {"detect": times} if isinstance(times, dict) else {}


def summarize_detections(detections: list[Detection]) -> str:
    if not detections:
        return "nothing detected"
    return "; ".join(d.summary() for d in detections)
