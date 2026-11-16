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

    A detector that reads distance off the size of a target's box cannot measure a box that
    reaches the frame's edge band (`edge_band_px`, `box_edge_band_px`), since it marks one as
    `clipped`, so the nearest it measures is what the biggest box inside the band reads, and says
    so through `nearest_m(label, size)`, as every detector Roboglot ships does. That reading
    assumes the target is centred in the frame, as it is when the camera sits at the target's own
    height; a target above or below the camera's height runs off the frame sooner, farther away,
    and the approach ends there, where the frame's edge cuts it. A detector that names no nearest
    reading, a third party's, is not guessed for: `go_to` then has only its detections' marks and
    distances to go on."""
    ask = getattr(detector, "nearest_m", None)
    nearest = ask(label, size) if callable(ask) else None
    if isinstance(nearest, bool) or not isinstance(nearest, int | float):
        return None
    return float(nearest) if math.isfinite(nearest) and nearest > 0 else None


def usable_m(distance: float | None) -> float | None:
    """`distance` when it is one a body can be at, finite and above nought, else None.

    `est_distance_m` is any float a detector gives, and a third party's may give NaN, infinity,
    nought or less. NaN and infinity never compare at or under a stop distance, so `go_to` walked
    at full speed toward one until its timeout, and nought or less was announced as an arrival.
    Each is no distance at all, as a missing one is."""
    if distance is None or not 0 < distance < math.inf:
        return None
    return float(distance)


EDGE_BAND_INPUT_PX = 3.0
"""How many of a network's own input pixels from the frame's edge a box's edge may land and
still be one the frame cut. A network regresses a box on its input and it is scaled back to the
frame, so the edge of a box the frame cut lands a pixel or two inside the frame's edge as often
as on it, and a box reported two rows inside went unmarked: its reading then floored just above
the nearest a refusal names, and `go_to` crept on toward a target filling the frame. Three is
more than that. The colour detector marks its blobs by the same band, which a lens's vignetting,
darkening the frame's outer rows below its threshold, can eat into the same way."""

NETWORK_INPUT_PX = 640
"""The long side of the input YOLO is run at, ultralytics' default, which the board's daemon
keeps: one of its pixels is `max(w, h) / NETWORK_INPUT_PX` of a frame's."""


def edge_band_px(size: tuple[float, float]) -> float:
    """How near the edge of a frame of `size` (width, height), in its own pixels, a box or a
    blob has to come for the frame to have cut it (`EDGE_BAND_INPUT_PX`): never less than the
    outermost row. The colour detector's blobs, which are the frame's own pixels, are held to
    this; a network's boxes to the wider `box_edge_band_px`."""
    w, h = size
    return max(1.0, EDGE_BAND_INPUT_PX * max(w, h) / NETWORK_INPUT_PX)


EDGE_BAND_SHARE = 0.02
"""The least share of a frame's height within which a box's top or bottom counts as cut by the
frame's edge, for a detector that reads distance off a network's boxes.

It is a safety margin chosen on the safe side, not a measurement: nobody has measured how far
inside the frame a real network puts the edge of a box the frame cut. Three of its input pixels
(`EDGE_BAND_INPUT_PX`) was a guess too, and a person's box reported five rows inside the edge of a
640x480 frame went unmarked under it, so its reading floored above the stop distance a refusal
named and `go_to` walked on to 0.97 m of the person. Two hundredths of the height is nearly ten
rows there, about twice that. What it costs: the tallest box a detector still measures is that
share shorter at the top and the bottom, so the nearest it measures, and the stop distance a
refusal names, are about four hundredths farther, a person's 1.81 m becoming 1.86 m on such a
frame, and an approach ends that much sooner."""


def box_edge_band_px(size: tuple[float, float]) -> float:
    """The edge band for a network's boxes on a frame of `size`, in rows: the wider of
    `edge_band_px` and `EDGE_BAND_SHARE` of the frame's height, which is what a box detector
    measures distance along."""
    return max(edge_band_px(size), EDGE_BAND_SHARE * size[1])


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
