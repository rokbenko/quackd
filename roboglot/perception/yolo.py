"""An optional YOLO detector (`roboglot[yolo]`) for real cameras and cluttered scenes.

Lazily imports `ultralytics` so the default install never pays for it. Maps COCO classes
onto the labels Roboglot's verbs look for — `ball`, `person`, `pet` — the colour detector's
`ball` among them, so verbs and `.glot` files do not care which detector produced a detection.

The box maths lives in `detections_from_boxes`, apart from the model, because the model does not
have to run on this machine: a Jetson reached with `--host` runs the same YOLO on its own GPU and
sends back boxes, and those must become exactly the detections a local model's boxes would.
"""

from __future__ import annotations

import logging
import math
import threading
from collections.abc import Iterable
from typing import Any, ClassVar

from PIL import Image

from roboglot.perception.base import Detection, box_edge_band_px

logger = logging.getLogger("roboglot.perception")

COCO_TO_LABEL = {"sports ball": "ball", "person": "person", "cat": "pet", "dog": "pet"}
HEIGHT_M = {"ball": 0.10, "person": 1.6, "pet": 0.35}
GUESSED_FOV_DEG = 62.0
"""The lens assumed when nobody said which one this is: a Pi Camera Module 2's."""

WIDEST_FRAME = 16 / 9
"""The widest frame, width over height, Roboglot expects a camera to give: 16:9, the shape of a
Microduck beta's frame at every size mediad serves (upstream's 1280 by 720) and of a webcam's HD
modes. A lens's field of view is across the width, so the wider the frame, the shorter its height
for that lens, and the farther the nearest reading of a box that fits in it (`nearest_box_m`)."""


def box_reading_m(height_m: float, w: float, box_h: float, fov_deg: float) -> float:
    """What a box `box_h` pixels tall reads on a frame `w` pixels wide through a lens `fov_deg`
    wide, for a target `height_m` tall, by `detections_from_boxes`' own arithmetic."""
    f = (w / 2) / math.tan(math.radians(fov_deg) / 2)
    return round(f * height_m / max(1.0, box_h), 3)


def detections_from_boxes(
    boxes: Iterable[tuple[str, float, float, float, float, float]],
    w: int,
    h: int,
    *,
    fov_deg: float,
    calibrated: bool = True,
) -> list[Detection]:
    """Boxes in pixels, as a YOLO model reports them, turned into Roboglot's detections.

    Each box is (the model's own class name, confidence, x1, y1, x2, y2) in the pixels of a
    `w` by `h` image. Names `COCO_TO_LABEL` does not map are dropped, because a detection the
    verbs have no word for is one they cannot act on. Bearing comes from the box centre's
    horizontal offset through a pinhole lens of `fov_deg`, and distance from the box height
    against `HEIGHT_M`, a typical real height for the label. Both are only as good as `fov_deg`,
    which is why `calibrated` is carried onto every detection: False says the lens was a guess.

    One function for every detector that sees boxes, so a local YOLO and one on a board reached
    with `--host` cannot drift apart: the same boxes are the same detections, to the last bit.

    A box the frame cuts at its top or its bottom is shorter than the target, so its distance
    is only an upper bound, and the detection is marked `clipped`: one whose top or bottom lies in
    the frame's edge band (`box_edge_band_px`). One cut at a side only keeps its whole height and
    is measured as any other.
    """
    f = (w / 2) / math.tan(math.radians(fov_deg) / 2)
    band = box_edge_band_px((w, h))
    out: list[Detection] = []
    for name, conf, *corners in boxes:
        label = COCO_TO_LABEL.get(name)
        if label is None:
            continue
        # float() as the inline version did on the model's tensors, so integer pixel boxes and
        # float ones take the same arithmetic
        x1, y1, x2, y2 = (float(v) for v in corners)
        cx, bh = (x1 + x2) / 2, max(1.0, y2 - y1)
        detection = Detection(
            label=label,
            cx=cx / w,
            cy=(y1 + y2) / 2 / h,
            area=(x2 - x1) * bh / (w * h),
            confidence=float(conf),
            bearing_deg=round(-math.degrees(math.atan((cx - w / 2) / f)), 1),
            est_distance_m=round(f * HEIGHT_M[label] / bh, 3),
            calibrated=calibrated,
        )
        if y1 < band or y2 > h - band:
            detection.mark_clipped()
        out.append(detection)
    return out


def nearest_box_m(label: str, size: tuple[float, float], fov_deg: float) -> float | None:
    """The nearest a box detector measures `label` at on a frame of `size` through a lens
    `fov_deg` wide, or None for a label it has no height for: what the tallest box it does not
    mark reads, as tall as the frame less its edge band at the top and the bottom. A nearer target
    reads farther than it is, its box cut, and is marked. It assumes the target is centred in the
    frame (`nearest_measurable`). What `YoloDetector` and `HostDetector` name as `nearest_m`."""
    height = HEIGHT_M.get(label)
    if height is None:
        return None
    w, h = size
    return box_reading_m(height, w, h - 2 * box_edge_band_px(size), fov_deg)


class YoloDetector:
    name = "yolo"
    blocking = True
    """`detect` holds the thread that calls it for a whole forward pass, so `detect_off_loop` runs
    it in a worker thread, as it does the board's detector. On the event loop the pass held up
    everything else the loop runs: `go_to` could neither re-send its twist nor zero it once
    `HOLD_TTL_S` had passed, and the body kept walking on its last twist for as long as the
    frame took."""
    _one_pass_at_a_time: ClassVar[threading.Lock] = threading.Lock()
    """Held around each forward pass. A frame a verb stopped waiting for, when the verb ended, is
    still in the network when the next verb's first frame comes, and a network is not safe to run
    from two threads at once, so the next pass waits in its own thread. One lock for the process,
    not one per detector: two detectors' passes share the machine's GPU or CPU in any case."""
    lens_at_connect = False
    """True on one `explicit_detector` built before the body connected, whose lens the loop and
    the MCP server set again from what the body reported. One built anywhere else, such as a
    `YoloDetector(fov_deg=70.0)` handed to a run from Python, keeps the lens it was built with."""

    def __init__(
        self,
        model: str = "yolov8n.pt",
        conf: float = 0.4,
        fov_deg: float = GUESSED_FOV_DEG,
        calibrated: bool = True,
    ) -> None:
        try:
            from ultralytics import YOLO
        except ImportError as e:
            raise ImportError(
                "YoloDetector needs ultralytics: uv pip install 'roboglot[yolo]'"
            ) from e
        self._model: Any = YOLO(model)
        self.conf = conf
        self.fov_deg = fov_deg
        # False when `fov_deg` is a default rather than this lens's, as `ColorBlobDetector`
        # carries it: the detections still point the right way and say their size is a guess
        self.calibrated = calibrated
        self._told_lens: str | None = None

    def calibrate(
        self, fov_deg: float | None, *, backend: str | None, assumed: str | None = None
    ) -> None:
        """Set the lens from what is known about the camera in front of the run, by the rule
        `HostDetector.calibrate` follows (`perception.lens`).

        A detector asked for with `--detector yolo` is built before the body connects, and the
        loop calls this again with what the body reported: a body described without a camera
        may report one of its own then, or have only a `--host` board's, and the lens is
        whichever one the primary view comes through. With none given on a real body the lens
        is a guess, and with one the body assumed (`assumed`) it is that one: the detections carry
        `calibrated=False` either way, and this says so once."""
        from roboglot.perception import lens, tell_lens

        self.fov_deg, self.calibrated = lens(
            fov_deg, backend, guess=GUESSED_FOV_DEG, assumed=assumed
        )
        tell_lens(
            self,
            backend,
            f"no camera field of view given for a {backend} camera, so YOLO's detections use "
            f"{self.fov_deg:.0f} degrees. Distances will be out by tens of percent: pass --fov-deg "
            "(a Pi Camera Module 2 is about 62) once you know yours.",
            assumed=assumed,
        )

    def nearest_m(self, label: str, size: tuple[int, int]) -> float | None:
        """The nearest this detector reads `label` at on a frame of `size` (`nearest_box_m`)."""
        return nearest_box_m(label, size, self.fov_deg)

    def detect(self, image: Image.Image) -> list[Detection]:
        w, h = image.size
        with self._one_pass_at_a_time:
            results = self._model.predict(image, conf=self.conf, verbose=False)
        boxes: list[tuple[str, float, float, float, float, float]] = []
        for res in results:
            names = res.names
            for box in res.boxes:
                x1, y1, x2, y2 = (float(v) for v in box.xyxy[0])
                boxes.append((names[int(box.cls)], float(box.conf), x1, y1, x2, y2))
        return detections_from_boxes(boxes, w, h, fov_deg=self.fov_deg, calibrated=self.calibrated)
