"""The shape of "what the robot sees", independent of how it was seen.

`Detection` is the only thing verbs and the LLM consume. Whether it came from an HSV
threshold over a frame, a YOLO model on a laptop, or — one day — upstream `mediad`'s feature
stream, the rest of Roboglot does not care.
"""

from __future__ import annotations

import asyncio
import math
from typing import Any, ClassVar, Protocol, runtime_checkable

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
        description="Positive = left of the robot's heading (upstream's +yaw convention).",
    )
    est_distance_m: float | None = None
    calibrated: bool = Field(
        default=True,
        description="False when the camera model is a guess, so bearing and distance are "
        "the right shape but the wrong size.",
    )

    clipped: ClassVar[bool] = False
    """True on a detection the frame's edge cuts in the dimension its distance is measured
    from, so `est_distance_m` is only an upper bound: the target may be nearer, and nearer than
    the camera can measure (`nearest_measurable`). A detector marks one with `mark_clipped`.

    `go_to` walked on toward such a target, a person whose head had left the frame or a ball
    under the nose, on a reading that could no longer fall to its stop distance, and the mark is
    what ends the approach there. It says how far a reading can be trusted, which is the
    detector's to know and only the steering loop's to act on, so it is kept out of the fields:
    a detection is recorded, shown and compared exactly as it always was, and one read back from
    a record is unmarked, as a third party's detector that marks nothing is."""

    def mark_clipped(self) -> Detection:
        """This detection, marked `clipped`."""
        # an attribute of the instance beside the fields, so neither `model_dump` nor `==` sees it
        object.__setattr__(self, "clipped", True)
        return self

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


async def detect_off_loop(detector: Detector, image: Image.Image) -> list[Detection]:
    """`detector.detect(image)`, in a worker thread when the detector says it waits on
    something outside this process or holds its thread for long.

    Such a detector sets `blocking = True`; today that is `HostDetector`, whose model runs on
    the board `--host` names, and `YoloDetector`, whose forward pass holds this process's thread
    for as long as a round trip to a board would. Called on the event loop, either would hold up
    everything else the loop runs, the twist `go_to` re-sends while it looks among them, so a
    frame slower than a body's deadman would stop the body on every frame, or keep a body with
    no deadman walking. The colour detector, a few milliseconds a frame, runs where it always
    has, on the loop."""
    if getattr(detector, "blocking", False):
        return await asyncio.to_thread(detector.detect, image)
    return detector.detect(image)


def nearest_measurable(
    detector: Detector | None, label: str, size: tuple[int, int]
) -> float | None:
    """The nearest distance `detector` can measure a `label` at on a frame of `size` (width,
    height) through its lens, in metres, or None when it does not say.

    A detector that reads distance off the size of a target's box cannot read less than a box as
    big as the frame gives, and says so through `nearest_m(label, size)`, as every detector
    Roboglot ships does. A nearer target still reads that, its detection `clipped`. A detector
    that names no nearest reading, a third party's, is not guessed for: `go_to` then has only its
    detections' marks and distances to go on."""
    ask = getattr(detector, "nearest_m", None)
    nearest = ask(label, size) if callable(ask) else None
    if isinstance(nearest, bool) or not isinstance(nearest, int | float):
        return None
    return float(nearest) if math.isfinite(nearest) and nearest > 0 else None


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
