"""The YOLO box maths, shared by a local model and one on a board reached with `--host`.

`detections_from_boxes` was factored out of `YoloDetector.detect` so both detectors turn the
same boxes into the same detections. These tests hold it to numbers worked out by hand from the
formula, and to the inline code it replaced, which is kept below verbatim so "the same as
before" is a comparison and not a recollection. No test here needs ultralytics: a fake module
stands in for it in `sys.modules`.
"""

from __future__ import annotations

import math
import sys
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest
from PIL import Image

from roboglot.perception.base import Detection
from roboglot.perception.yolo import (
    COCO_TO_LABEL,
    HEIGHT_M,
    YoloDetector,
    detections_from_boxes,
)

Box = tuple[str, float, float, float, float, float]

#: COCO's own class numbers for the names Roboglot maps, and one it does not.
COCO_NAMES = {0: "person", 15: "cat", 16: "dog", 32: "sports ball", 58: "potted plant"}
CLASS_OF = {name: cls for cls, name in COCO_NAMES.items()}


def _results(*frames: list[Box]) -> list[Any]:
    """What `YOLO.predict` returns, as far as `YoloDetector` reads it: one result per image,
    each with the model's class names and boxes carrying `cls`, `conf` and `xyxy`."""
    return [
        SimpleNamespace(
            names=COCO_NAMES,
            boxes=[
                SimpleNamespace(cls=CLASS_OF[name], conf=conf, xyxy=[(x1, y1, x2, y2)])
                for name, conf, x1, y1, x2, y2 in boxes
            ],
        )
        for boxes in frames
    ]


def _inline_maths_before_the_refactor(
    results: list[Any], w: int, h: int, fov_deg: float
) -> list[Detection]:
    """`YoloDetector.detect`'s loop as it stood before `detections_from_boxes`, verbatim but for
    `self.fov_deg` and the predict call, which arrive as arguments."""
    f = (w / 2) / math.tan(math.radians(fov_deg) / 2)
    out: list[Detection] = []
    for res in results:
        names = res.names
        for box in res.boxes:
            label = COCO_TO_LABEL.get(names[int(box.cls)])
            if label is None:
                continue
            x1, y1, x2, y2 = (float(v) for v in box.xyxy[0])
            cx, bh = (x1 + x2) / 2, max(1.0, y2 - y1)
            out.append(
                Detection(
                    label=label,
                    cx=cx / w,
                    cy=(y1 + y2) / 2 / h,
                    area=(x2 - x1) * bh / (w * h),
                    confidence=float(box.conf),
                    bearing_deg=round(-math.degrees(math.atan((cx - w / 2) / f)), 1),
                    est_distance_m=round(f * HEIGHT_M[label] / bh, 3),
                )
            )
    return out


class _FakeYOLO:
    """`ultralytics.YOLO`, reduced to the constructor and the one method Roboglot calls."""

    made: list[_FakeYOLO] = []
    results: list[Any] = []

    def __init__(self, model: str) -> None:
        self.model = model
        self.calls: list[tuple[tuple[int, int], float, bool]] = []
        _FakeYOLO.made.append(self)

    def predict(self, image: Image.Image, *, conf: float, verbose: bool) -> list[Any]:
        self.calls.append((image.size, conf, verbose))
        return _FakeYOLO.results


@pytest.fixture
def fake_ultralytics(monkeypatch: pytest.MonkeyPatch) -> type[_FakeYOLO]:
    module = ModuleType("ultralytics")
    module.YOLO = _FakeYOLO  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "ultralytics", module)
    monkeypatch.setattr(_FakeYOLO, "made", [])
    monkeypatch.setattr(_FakeYOLO, "results", [])
    return _FakeYOLO


# ── the geometry, by hand ───────────────────────────────────────────────────────────────


def test_a_ninety_degree_lens_gives_the_bearings_and_distances_worked_out_by_hand() -> None:
    """A 90 degree lens makes the focal length half the width, 320 px on a 640 px frame, which
    keeps the arithmetic checkable on paper.

    The ball's box is centred, so its bearing is 0, and it is 40 px tall: 320 x 0.10 m / 40 =
    0.8 m. The person's centre is at x = 80, 240 px left of centre: atan(240 / 320) is the 3-4-5
    triangle's 36.87 degrees, positive because left is positive, and 320 x 1.6 m / 480 px =
    1.0667 m. A ball 80 px tall centred at x = 480 is 160 px right: atan(0.5) = 26.57 degrees,
    negative, and 320 x 0.10 / 80 = 0.4 m."""
    boxes: list[Box] = [
        ("sports ball", 0.87, 300.0, 200.0, 340.0, 240.0),
        ("person", 0.9, 0.0, 0.0, 160.0, 480.0),
        ("sports ball", 0.6, 440.0, 200.0, 520.0, 280.0),
    ]
    ball, person, right = detections_from_boxes(boxes, 640, 480, fov_deg=90.0)

    assert (ball.label, ball.bearing_deg, ball.est_distance_m) == ("ball", 0.0, 0.8)
    assert (ball.cx, ball.cy, ball.area) == (0.5, 220 / 480, 40 * 40 / (640 * 480))
    assert ball.confidence == 0.87 and ball.calibrated is True

    assert (person.label, person.bearing_deg, person.est_distance_m) == ("person", 36.9, 1.067)
    assert (person.cx, person.cy, person.area) == (0.125, 0.5, 0.25)

    assert (right.bearing_deg, right.est_distance_m) == (-26.6, 0.4)
    assert "at bearing 27° right" in right.summary()


def test_a_real_lens_narrows_the_bearing_and_lengthens_the_distance() -> None:
    """At 62 degrees, a Pi camera module's, the focal length is 320 / tan(31 degrees) = 532.57 px.
    The same centred ball is then 532.57 x 0.10 / 40 = 1.331 m away, and the person's 240 px
    offset is atan(240 / 532.57) = 24.26 degrees rather than 36.87."""
    boxes: list[Box] = [
        ("sports ball", 0.87, 300.0, 200.0, 340.0, 240.0),
        ("person", 0.9, 0.0, 0.0, 160.0, 480.0),
    ]
    ball, person = detections_from_boxes(boxes, 640, 480, fov_deg=62.0)
    assert (ball.bearing_deg, ball.est_distance_m) == (0.0, 1.331)
    assert (person.bearing_deg, person.est_distance_m) == (24.3, 1.775)


def test_names_nothing_maps_are_dropped_and_the_pets_share_a_label() -> None:
    boxes: list[Box] = [
        ("potted plant", 0.99, 0.0, 0.0, 100.0, 100.0),
        ("cat", 0.5, 480.0, 360.0, 640.0, 480.0),
        ("dog", 0.5, 0.0, 360.0, 160.0, 480.0),
        ("Sports Ball", 0.9, 0.0, 0.0, 10.0, 10.0),
    ]
    found = detections_from_boxes(boxes, 640, 480, fov_deg=90.0)
    assert [d.label for d in found] == ["pet", "pet"]
    cat = found[0]
    # 320 x 0.35 m / 120 px, and 240 px right of centre
    assert (cat.bearing_deg, cat.est_distance_m) == (-36.9, 0.933)
    assert detections_from_boxes([], 640, 480, fov_deg=90.0) == []


def test_a_box_under_a_pixel_tall_is_floored_at_one_rather_than_dividing_by_nothing() -> None:
    (flat,) = detections_from_boxes(
        [("sports ball", 0.6, 100.0, 100.0, 110.0, 100.5)], 640, 480, fov_deg=90.0
    )
    assert flat.est_distance_m == 32.0  # 320 x 0.10 / 1
    assert flat.area == 10.0 * 1.0 / (640 * 480)


@pytest.mark.parametrize("calibrated", [True, False])
def test_calibrated_is_carried_onto_every_detection(calibrated: bool) -> None:
    boxes: list[Box] = [
        ("sports ball", 0.87, 300.0, 200.0, 340.0, 240.0),
        ("person", 0.9, 0.0, 0.0, 160.0, 480.0),
    ]
    found = detections_from_boxes(boxes, 640, 480, fov_deg=62.0, calibrated=calibrated)
    assert [d.calibrated for d in found] == [calibrated, calibrated]
    assert ("uncalibrated" in found[0].summary()) is not calibrated


def test_integer_pixel_boxes_give_the_same_detections_as_float_ones() -> None:
    as_ints = detections_from_boxes([("person", 0.9, 0, 0, 160, 480)], 640, 480, fov_deg=62.0)
    as_floats = detections_from_boxes(
        [("person", 0.9, 0.0, 0.0, 160.0, 480.0)], 640, 480, fov_deg=62.0
    )
    assert [d.model_dump() for d in as_ints] == [d.model_dump() for d in as_floats]


# ── the same as before, exactly ─────────────────────────────────────────────────────────


def _awkward_boxes() -> list[Box]:
    """Fractional corners across the whole frame, every class, and a box under a pixel tall:
    the inputs where a reordered sum or a lost float() would show in the last bit."""
    names = [*COCO_NAMES.values()]
    boxes: list[Box] = []
    for i in range(60):
        x1 = (i * 37.13) % 600.0
        y1 = (i * 11.71) % 440.0
        width = 3.3 + (i * 7.07) % 120.0
        height = 0.4 if i % 13 == 0 else 1.7 + (i * 5.31) % 200.0
        conf = 0.25 + (i * 0.137) % 0.7
        boxes.append((names[i % len(names)], conf, x1, y1, x1 + width, y1 + height))
    return boxes


@pytest.mark.parametrize(
    ("w", "h", "fov_deg"), [(640, 480, 62.0), (1280, 720, 90.0), (320, 240, 48.5)]
)
def test_the_shared_maths_is_bit_for_bit_the_inline_maths_it_replaced(
    w: int, h: int, fov_deg: float
) -> None:
    boxes = _awkward_boxes()
    before = _inline_maths_before_the_refactor(_results(boxes[:30], boxes[30:]), w, h, fov_deg)
    after = detections_from_boxes(boxes, w, h, fov_deg=fov_deg)
    assert len(after) == len(before) > 30
    assert [d.model_dump() for d in after] == [d.model_dump() for d in before]


# ── YoloDetector on top of it ───────────────────────────────────────────────────────────


def test_the_yolo_detector_gives_what_the_shared_maths_gives_for_its_boxes(
    fake_ultralytics: type[_FakeYOLO],
) -> None:
    boxes = _awkward_boxes()
    fake_ultralytics.results = _results(boxes[:20], boxes[20:])
    detector = YoloDetector(model="yolov8s.pt", conf=0.25, fov_deg=62.0)
    image = Image.new("RGB", (640, 480))

    found = detector.detect(image)

    assert found == detections_from_boxes(boxes, 640, 480, fov_deg=62.0)
    assert found == _inline_maths_before_the_refactor(fake_ultralytics.results, 640, 480, 62.0)
    (model,) = fake_ultralytics.made
    assert model.model == "yolov8s.pt"
    assert model.calls == [((640, 480), 0.25, False)]


def test_the_yolo_detector_carries_calibrated_through(fake_ultralytics: type[_FakeYOLO]) -> None:
    fake_ultralytics.results = _results([("sports ball", 0.87, 300.0, 200.0, 340.0, 240.0)])
    detector = YoloDetector(calibrated=False)
    assert detector.calibrated is False and detector.fov_deg == 62.0
    (ball,) = detector.detect(Image.new("RGB", (640, 480)))
    assert ball.calibrated is False
    assert YoloDetector().calibrated is True


def test_without_ultralytics_the_detector_names_the_extra_to_install(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(sys.modules, "ultralytics", None)
    with pytest.raises(ImportError, match=r"roboglot\[yolo\]"):
        YoloDetector()
