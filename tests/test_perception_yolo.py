"""The YOLO box maths, shared by a local model and one on a board reached with `--host`.

`detections_from_boxes` was factored out of `YoloDetector.detect` so both detectors turn the
same boxes into the same detections. These tests hold it to numbers worked out by hand from the
formula, and to the inline code it replaced, which is kept below verbatim so "the same as
before" is a comparison and not a recollection. No test here needs ultralytics: a fake module
stands in for it in `sys.modules`.
"""

from __future__ import annotations

import asyncio
import math
import sys
import threading
import time
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest
from PIL import Image

from roboglot.perception.base import Detection, detect_off_loop
from roboglot.perception.yolo import (
    COCO_TO_LABEL,
    GUESSED_FOV_DEG,
    HEIGHT_M,
    YoloDetector,
    detections_from_boxes,
)
from roboglot.transport.base import Ack, Intent
from roboglot.verbs.core import HOLD_TTL_S, MOVE_RESEND_S, _see_holding
from roboglot.verbs.registry import VerbContext

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


def test_a_box_the_frame_cuts_at_its_top_or_bottom_says_its_distance_is_only_an_upper_bound() -> (
    None
):
    """A box detector reads distance off a box's height, and a box the frame cuts at its top or
    its bottom is shorter than the target, so it reads farther than the target is: `go_to`
    walked on toward a person whose head had left the frame, on a reading that fell more slowly
    than the gap did. Such a detection is marked `clipped`; one the frame cuts at a side only,
    whose height is whole, is not, and nor is one inside the frame. The mark stays out of the
    record and out of equality, so a detection serializes and compares as it always has."""
    boxes: list[Box] = [
        ("person", 0.9, 200.0, 0.0, 300.0, 400.0),
        ("person", 0.9, 200.0, 80.0, 300.0, 480.0),
        ("person", 0.9, 0.0, 80.0, 100.0, 400.0),
        ("person", 0.9, 200.0, 80.0, 300.0, 400.0),
    ]
    found = detections_from_boxes(boxes, 640, 480, fov_deg=62.0)
    assert [d.clipped for d in found] == [True, True, False, False]
    assert all("clipped" not in d.model_dump() for d in found)
    assert found[0] == Detection(**found[0].model_dump())


@pytest.mark.parametrize(
    ("w", "h", "fov_deg"), [(640, 480, 62.0), (1280, 720, 90.0), (320, 240, 48.5)]
)
def test_a_box_detector_names_as_its_nearest_reading_what_a_box_as_tall_as_the_frame_reads(
    w: int, h: int, fov_deg: float, fake_ultralytics: type[_FakeYOLO]
) -> None:
    """`go_to` refuses a stop distance nearer than the detector can read for the target, and
    asks the detector, since only the detector knows how it measures. A box detector's nearest is
    what a box as tall as the frame reads, to the last bit, for every label it has a height for,
    the same in this process and on a board, and nothing for a label it has none for."""
    from roboglot.perception.host import HostDetector

    local = YoloDetector(fov_deg=fov_deg)
    board = HostDetector(SimpleNamespace(address="board:0"), fov_deg=fov_deg)  # type: ignore[arg-type]
    for coco, label in COCO_TO_LABEL.items():
        (tall,) = detections_from_boxes(
            [(coco, 0.9, 0.0, 0.0, w / 3, float(h))], w, h, fov_deg=fov_deg
        )
        assert local.nearest_m(label, (w, h)) == tall.est_distance_m, label
        assert board.nearest_m(label, (w, h)) == tall.est_distance_m, label
    assert set(COCO_TO_LABEL.values()) == set(HEIGHT_M)
    assert local.nearest_m("cup", (w, h)) is None and board.nearest_m("cup", (w, h)) is None


def test_the_yolo_detector_carries_calibrated_through(fake_ultralytics: type[_FakeYOLO]) -> None:
    fake_ultralytics.results = _results([("sports ball", 0.87, 300.0, 200.0, 340.0, 240.0)])
    detector = YoloDetector(calibrated=False)
    assert detector.calibrated is False and detector.fov_deg == 62.0
    (ball,) = detector.detect(Image.new("RGB", (640, 480)))
    assert ball.calibrated is False
    assert YoloDetector().calibrated is True


# ── off the event loop ──────────────────────────────────────────────────────────────────

#: How long the stand-in model takes over one frame: two deadman windows, so the window closes
#: well inside it.
SLOW_S = 2 * HOLD_TTL_S


class _SlowModel:
    """Stands in for ultralytics' model: `predict` is a forward pass that holds the thread that
    calls it for `SLOW_S` (here `time.sleep`, which holds the calling thread as a forward pass
    does; a real pass holds the GIL as well, which only makes it worse), then finds nothing. It
    keeps which threads called it and the most passes it was in at once."""

    def __init__(self) -> None:
        self.threads: list[int] = []
        self.running = 0
        self.most_at_once = 0
        self._count = threading.Lock()

    def predict(self, image: Image.Image, conf: float, verbose: bool) -> list[Any]:
        self.threads.append(threading.get_ident())
        with self._count:
            self.running += 1
            self.most_at_once = max(self.most_at_once, self.running)
        time.sleep(SLOW_S)
        with self._count:
            self.running -= 1
        return []


def _slow_yolo(model: _SlowModel) -> YoloDetector:
    det = YoloDetector.__new__(YoloDetector)  # no ultralytics needed
    det._model = model
    det.conf = 0.4
    det.fov_deg = GUESSED_FOV_DEG
    det.calibrated = True
    det._told_lens = None
    return det


class _Walking:
    """A body that is walking: one frame to look at, and every twist sent to it timed on the
    event loop's clock, the clock `_see_holding` holds its twist by (as in
    tests/test_host_detector.py)."""

    def __init__(self) -> None:
        self.moves: list[tuple[float, tuple[float, float, float]]] = []

    async def get_frame(self) -> Image.Image:
        return Image.new("RGB", (64, 48), (40, 40, 40))

    async def send_intent(self, intent: Intent) -> Ack:
        if intent.kind == "move":
            twist = (intent.params["vx"], intent.params["vy"], intent.params["wz"])
            self.moves.append((self.now(), twist))
        return Ack()

    def now(self) -> float:
        return asyncio.get_running_loop().time()


async def test_a_slow_local_yolo_does_not_hold_go_to_s_twist_past_one_deadman_window() -> None:
    """`detect_off_loop` moves a detector to a worker thread only when it declares
    `blocking = True`, and `YoloDetector` declared nothing, so its forward pass ran on the event
    loop. Inside `_see_holding` that meant no re-send and no zero twist while the model ran: the
    body kept its last twist for the whole pass. As the board's detector already did, the held
    twist is now re-sent and then zeroed once `HOLD_TTL_S` has passed, while the frame is still
    being looked at."""
    model = _SlowModel()
    body = _Walking()
    ctx = VerbContext(transport=body, detector=_slow_yolo(model))  # type: ignore[arg-type]
    loop_thread = threading.get_ident()
    started = asyncio.get_running_loop().time()
    await _see_holding(ctx, "ball", "go_to", (0.2, 0.0, 0.0))
    took = asyncio.get_running_loop().time() - started
    # less a Windows clock tick (about 16 ms) of slack between time.sleep and the loop's clock
    assert took >= SLOW_S - 0.05, f"the model really was slow: {took:.3f} s"
    zeros = [round(t - started, 3) for t, tw in body.moves if tw == (0.0, 0.0, 0.0)]
    on_loop = loop_thread in model.threads
    assert zeros and zeros[0] <= HOLD_TTL_S + 2 * MOVE_RESEND_S, (
        f"no zero twist within one deadman window ({HOLD_TTL_S} s) of a {SLOW_S} s forward pass: "
        f"twists sent {[(round(t - started, 3), tw) for t, tw in body.moves]}, the frame took "
        f"{took:.2f} s, predict ran on the event loop's thread: {on_loop}"
    )


async def test_detect_off_loop_runs_yolo_off_the_event_loop_thread() -> None:
    """`detect_off_loop`, which `_see`, `observe` and the loop's observation all go through, ran
    `YoloDetector.detect` on the calling thread, the event loop's."""
    model = _SlowModel()
    loop_thread = threading.get_ident()
    await detect_off_loop(_slow_yolo(model), Image.new("RGB", (64, 48)))
    assert loop_thread not in model.threads, "YOLO's forward pass ran on the event loop's thread"


async def test_two_frames_at_once_never_run_the_model_at_once() -> None:
    """Off the loop, a frame `go_to` stops waiting for when its verb ends is still in the model
    when the next verb's first frame comes, and a model is not safe to run from two threads at
    once. The detector runs one forward pass at a time, the next waiting in its own thread."""
    model = _SlowModel()
    detector = _slow_yolo(model)
    frame = Image.new("RGB", (64, 48))
    await asyncio.gather(detect_off_loop(detector, frame), detect_off_loop(detector, frame))
    assert len(model.threads) == 2 and model.most_at_once == 1, model.most_at_once


def test_without_ultralytics_the_detector_names_the_extra_to_install(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(sys.modules, "ultralytics", None)
    with pytest.raises(ImportError, match=r"roboglot\[yolo\]"):
        YoloDetector()
