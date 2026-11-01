"""The default detector, held to OpenCV. It used to ask OpenCV for all of this, and what it asks
numpy for now has to give the same answers to the last bit, because a detection's area sets its
distance and a run's next move. OpenCV is here as the oracle, in `dev` and imported by nothing
quackd ships.

OpenCV gives two answers, one on x86 and another on Arm, so the conversion is held to the x86 one,
which numpy's integers give on either, and on Arm what the difference can reach is held to nothing.

CI locks OpenCV 4.13, and the nightly `fresh-install` job runs this on the newest there is, 5.0 as
this is written, so both major versions are held to it."""

from __future__ import annotations

import ast
import math
import platform
import tomllib
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import pytest
from PIL import Image

from quackd.perception.base import Detection
from quackd.perception.blobs import Candidates, biggest_blobs, rgb_to_hsv
from quackd.perception.color_blob import DEFAULT_TARGETS, ColorBlobDetector, HSVRange, Target
from quackd.sim2d.render import render_duckcam
from quackd.sim2d.world import World

REPO = Path(__file__).resolve().parents[1]
ON_ARM = platform.machine().lower() in ("arm64", "aarch64")


def _every_colour(red: int) -> np.ndarray:
    """All 65,536 colours with this much red, as one frame."""
    green, blue = np.meshgrid(np.arange(256), np.arange(256), indexing="ij")
    return np.stack((np.full((256, 256), red), green, blue), axis=-1).astype(np.uint8)


def _read_both_ways(red: int, width: int) -> tuple[np.ndarray, np.ndarray]:
    """This red's colours as numpy reads them and as OpenCV does `width` pixels to a row, since on
    Arm a row's width decides which of OpenCV's code each pixel goes through."""
    frame = _every_colour(red)
    pixels = frame.reshape(-1, 3)
    usable = len(pixels) - len(pixels) % width
    theirs = cv2.cvtColor(pixels[:usable].reshape(-1, width, 3), cv2.COLOR_RGB2HSV)
    return rgb_to_hsv(frame).reshape(-1, 3)[:usable], theirs.reshape(-1, 3)


@pytest.mark.skipif(ON_ARM, reason="OpenCV rounds its own way on Arm: the test after this one")
@pytest.mark.parametrize("width", [256, 1, 17])
def test_every_colour_there_is_converts_as_opencvs_does(width: int) -> None:
    wrong = 0
    for red in range(256):
        ours, theirs = _read_both_ways(red, width)
        wrong += int(np.any(ours != theirs, axis=-1).sum())
    assert wrong == 0, f"{wrong} of 16,777,216 colours"


def _within(hsv: np.ndarray, target: Target) -> np.ndarray:
    r = target.hsv
    return (
        (hsv[:, 0] >= r.h_lo)
        & (hsv[:, 0] <= r.h_hi)
        & (hsv[:, 1] >= r.s_lo)
        & (hsv[:, 2] >= r.v_lo)
    )


@pytest.mark.skipif(not ON_ARM, reason="OpenCV gives one answer at any width off Arm")
@pytest.mark.parametrize("width", [256, 1, 17])
def test_opencvs_rounding_on_arm_moves_no_pixel_of_any_target_quackd_ships(width: int) -> None:
    """On Apple Silicon and Linux on Arm, OpenCV does not read a colour as it does on x86, and not
    always the same way twice: its NEON code reads 6,582 colours a saturation step lower, and the
    code for what is left of a row, the last columns of a frame no multiple of 8 wide, reads 123,444
    a step either way. numpy keeps the x86 answer everywhere. So what has to hold here is what that
    can reach, which is nothing: hue and value agree, saturation is a step off at most, and no
    colour crosses the threshold of a target quackd ships. A mask is a pixel's colour and nothing
    else, so the masks are the same, and what is made from a mask is held to OpenCV further down."""
    moved = 0
    for red in range(256):
        ours, theirs = _read_both_ways(red, width)
        difference = ours.astype(np.int16) - theirs.astype(np.int16)
        assert not difference[:, [0, 2]].any()
        assert np.abs(difference[:, 1]).max() <= 1
        moved += int(np.count_nonzero(difference[:, 1]))
        for target in DEFAULT_TARGETS:
            assert np.array_equal(_within(ours, target), _within(theirs, target)), target
    assert moved < 16_777_216 // 100, f"{moved} colours, more than OpenCV has moved until now"


@pytest.mark.parametrize(
    ("saturation", "value"),
    [(120, 120), (0, 0), (1, 1), (2, 2), (60, 200), (255, 255), (200, 40), (121, 119)],
)
def test_the_prefilter_never_loses_a_pixel_and_reads_each_it_keeps_exactly(
    saturation: int, value: int
) -> None:
    """Most of what makes it quick is what it throws away without dividing, so what it may never do
    is throw away a pixel the exact test would have kept."""
    for red in range(0, 256, 5):
        frame = _every_colour(red)
        truth = rgb_to_hsv(frame)
        found = Candidates(frame, saturation, value)
        kept = np.zeros(frame.shape[:2], dtype=bool)
        kept[found.rows, found.columns] = True
        assert not np.any((truth[..., 1] >= saturation) & (truth[..., 2] >= value) & ~kept)
        read = np.stack((found.hue, found.saturation, found.value), axis=-1)
        assert np.array_equal(read, truth[found.rows, found.columns])


@pytest.mark.parametrize(
    ("hue_low", "hue_high", "saturation", "value"),
    [
        (5, 22, 120, 120),
        (0, 0, 0, 0),
        (179, 179, 255, 255),
        (100, 130, 120, 120),
        (0, 179, 1, 1),
        (133, 148, 120, 120),
        (30, 10, 0, 0),
    ],
)
def test_the_range_test_picks_what_inrange_picks(
    hue_low: int, hue_high: int, saturation: int, value: int
) -> None:
    for red in range(0, 256, 5):
        frame = _every_colour(red)
        truth = rgb_to_hsv(frame)
        low = np.array([hue_low, saturation, value], dtype=np.uint8)
        high = np.array([hue_high, 255, 255], dtype=np.uint8)
        theirs = cv2.inRange(truth, low, high) != 0
        ours = np.zeros(frame.shape[:2], dtype=bool)
        found = Candidates(frame, saturation, value).matching(hue_low, hue_high, saturation, value)
        if found is not None:
            mask, row, column = found
            ours[row : row + mask.shape[0], column : column + mask.shape[1]] = mask != 0
        assert np.array_equal(theirs, ours)


def _opencvs_blobs(
    mask: np.ndarray, keep: int | None, min_area: float
) -> list[tuple[float, tuple[int, int, int, int]]]:
    contours, _ = cv2.findContours(
        mask.astype(np.uint8) * 255, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
    )
    ordered = sorted(contours, key=cv2.contourArea, reverse=True)
    if keep is not None:
        ordered = ordered[:keep]
    return [
        (float(cv2.contourArea(c)), tuple(int(v) for v in cv2.boundingRect(c)))  # type: ignore[misc]
        for c in ordered
        if float(cv2.contourArea(c)) >= min_area
    ]


def _mask(rng: np.random.Generator, height: int, width: int) -> np.ndarray:
    """A mask with something awkward in it, one kind per call."""
    kind = int(rng.integers(0, 8))
    mask = np.zeros((height, width), dtype=np.uint8)
    at = lambda: (int(rng.integers(0, width)), int(rng.integers(0, height)))  # noqa: E731
    if kind == 0:  # noise, at any density
        mask = (rng.random((height, width)) < rng.uniform(0.01, 0.6)).astype(np.uint8)
    elif kind == 1:  # discs
        for _ in range(int(rng.integers(1, 7))):
            cv2.circle(mask, at(), int(rng.integers(1, 30)), 1, -1)
    elif kind == 2:  # rectangles, some of them over the edge
        for _ in range(int(rng.integers(1, 7))):
            x, y = at()
            cv2.rectangle(
                mask, (x, y), (x + int(rng.integers(0, 40)), y + int(rng.integers(0, 40))), 1, -1
            )
    elif kind == 3:  # rings with islands in their holes, which are no outer contours
        for _ in range(int(rng.integers(1, 4))):
            centre = at()
            radius = int(rng.integers(8, 40))
            cv2.circle(mask, centre, radius, 1, int(rng.integers(1, 4)))
            cv2.circle(mask, centre, max(1, radius // 4), 1, -1)
    elif kind == 4:  # thin lines: a walk that passes the same pixel twice encloses nothing
        for _ in range(int(rng.integers(1, 8))):
            cv2.line(mask, at(), at(), 1, 1)
    elif kind == 5:  # single pixels, and pairs that touch only at a corner
        for _ in range(int(rng.integers(1, 40))):
            y, x = int(rng.integers(0, max(1, height - 1))), int(rng.integers(0, max(1, width - 1)))
            mask[y, x] = 1
            if rng.random() < 0.5:
                mask[min(y + 1, height - 1), min(x + 1, width - 1)] = 1
    elif kind == 6:  # blocks, with many holes
        small = (rng.random((height // 6 + 1, width // 6 + 1)) < rng.uniform(0.2, 0.7)).astype(
            np.uint8
        )
        mask = cv2.resize(small, (width, height), interpolation=cv2.INTER_NEAREST)
    else:  # everything or nothing
        mask[:] = 1 if rng.random() < 0.5 else 0
    return mask


def test_blobs_come_back_as_opencvs_contours_do() -> None:
    """Areas, boxes and order, whichever way the detector could ask. Ties are in here on purpose: a
    mask of equal squares takes the blob OpenCV would, and that decides which ball a robot sees."""
    rng = np.random.default_rng(20261101)
    for number in range(1500):
        mask = _mask(rng, int(rng.integers(2, 120)), int(rng.integers(2, 160)))
        keep = [None, 1, 2, 3][int(rng.integers(0, 4))]
        min_area = [0, 1, 12, 50][int(rng.integers(0, 4))]
        ours = biggest_blobs(mask, keep, min_area)
        assert ours == _opencvs_blobs(mask, keep, min_area), (number, mask.shape, keep, min_area)


def test_asking_for_no_blobs_gets_none() -> None:
    mask = np.ones((8, 8), dtype=np.uint8)
    assert biggest_blobs(mask, 0) == _opencvs_blobs(mask, 0, 0) == []


def _detect_with_opencv(detector: ColorBlobDetector, image: Image.Image) -> list[Detection]:
    """`ColorBlobDetector.detect` as it was when it asked OpenCV, word for word, so what is compared
    is the whole answer, the geometry on top of the blobs with it."""
    rgb = np.asarray(image.convert("RGB"))
    h, w = rgb.shape[:2]
    hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
    f = (w / 2) / math.tan(math.radians(detector.fov_deg) / 2)
    out: list[Detection] = []
    for target in detector.targets:
        r = target.hsv
        lo = np.array([r.h_lo, r.s_lo, r.v_lo], dtype=np.uint8)
        hi = np.array([r.h_hi, 255, 255], dtype=np.uint8)
        mask = cv2.inRange(hsv, lo, hi)
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        blobs = sorted(contours, key=cv2.contourArea, reverse=True)[: detector.max_per_label]
        for c in blobs:
            area = float(cv2.contourArea(c))
            if area < detector.min_area_px:
                continue
            x, y, bw, bh = cv2.boundingRect(c)
            cx, cy = x + bw / 2, y + bh / 2
            bearing = -math.degrees(math.atan((cx - w / 2) / f))
            if target.round:
                radius_px = max(1.0, math.sqrt(area / math.pi))
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
                    calibrated=detector.calibrated,
                    bearing_deg=round(bearing, 1),
                    est_distance_m=round(dist, 3),
                )
            )
    out.sort(key=lambda d: (d.label != "ball", d.est_distance_m or 0.0))
    return out


def _simulator_frames(count: int) -> list[Image.Image]:
    rng = np.random.default_rng(7)
    return [
        render_duckcam(World(seed=seed), size=int(rng.choice([128, 256, 256, 320, 480])))
        for seed in range(count)
    ]


def _with_sensor_noise(image: Image.Image, rng: np.random.Generator) -> Image.Image:
    """What a real camera does to a frame: every mask comes out messy, which is where a walk round a
    blob's edge earns its keep."""
    pixels = np.asarray(image.convert("RGB")).astype(np.int16)
    pixels = pixels + rng.normal(0, 18, pixels.shape).astype(np.int16) + int(rng.integers(-30, 30))
    return Image.fromarray(np.clip(pixels, 0, 255).astype(np.uint8))


def _saturated(density: float, height: int = 240, width: int = 320) -> Image.Image:
    """Bright, saturated pixels of every hue: the worst a frame can be, every target's mask full."""
    rng = np.random.default_rng(int(density * 100))
    frame = np.full((height, width, 3), 60, dtype=np.uint8)
    hit = rng.random((height, width)) < density
    hue = rng.integers(0, 180, (height, width), dtype=np.uint8)
    bright = np.full((height, width), 230, dtype=np.uint8)
    colour = cv2.cvtColor(np.stack((hue, bright, bright), axis=-1), cv2.COLOR_HSV2RGB)
    frame[hit] = colour[hit]
    return Image.fromarray(frame)


@pytest.mark.parametrize(
    "detector",
    [
        ColorBlobDetector(),
        ColorBlobDetector(max_per_label=3, min_area_px=4),
        ColorBlobDetector(min_area_px=0),
        ColorBlobDetector(
            targets=(Target("mine", HSVRange(0, 179, s_lo=30, v_lo=30), 0.1, round=True),),
            fov_deg=62.0,
            calibrated=False,
        ),
    ],
    ids=["default", "several per label", "no minimum area", "a target of ones own"],
)
def test_every_field_of_every_detection_is_what_opencv_gave(detector: ColorBlobDetector) -> None:
    rng = np.random.default_rng(3)
    frames = _simulator_frames(60)
    frames += [_with_sensor_noise(frame, rng) for frame in frames[:40]]
    frames += [_saturated(density) for density in (0.05, 0.2, 0.5, 1.0)]
    seen = 0
    for frame in frames:
        theirs = _detect_with_opencv(detector, frame)
        assert detector.detect(frame) == theirs
        seen += len(theirs)
    assert seen > 0, "nothing was detected, so nothing was compared"


def test_a_detector_with_no_targets_finds_nothing() -> None:
    assert ColorBlobDetector(targets=()).detect(Image.new("RGB", (32, 32))) == []


def _imports(tree: ast.AST) -> list[str]:
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found += [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.append(node.module)
    return found


def test_nothing_quackd_ships_to_a_laptop_imports_opencv() -> None:
    """The daemons under `bridge/` run on a robot or a board, on that machine's own OpenCV, and a
    robot's SDK may bring its own. Nothing else asks for it, because the default install has none:
    the import would pass in this suite, where `dev` carries it, and fail for everybody else."""
    shipped = [*(REPO / "quackd").rglob("*.py"), *(REPO / "adapters").rglob("src/**/*.py")]
    assert len(shipped) > 200
    offenders = [
        str(path.relative_to(REPO))
        for path in shipped
        if "cv2" in _imports(ast.parse(path.read_text(encoding="utf-8")))
    ]
    assert not offenders, offenders


def _pyproject() -> dict[str, Any]:
    return tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))


def test_the_default_install_has_no_opencv_and_yolo_keeps_a_headless_one() -> None:
    project = _pyproject()["project"]
    assert not [d for d in project["dependencies"] if "opencv" in d]
    extras = project["optional-dependencies"]
    assert any(d.startswith("opencv-python-headless") for d in extras["yolo"]), (
        "ultralytics brings the opencv wanting a display's libraries, which headless Linux lacks"
    )
    assert any(d.startswith("opencv-python-headless") for d in extras["dev"]), "the oracle here"
