"""Coloured blobs in an RGB frame, with numpy alone: what the default detector needs of a frame.

It used to ask OpenCV, which is half of a default install on disk and most of its download, for an
HSV conversion, a threshold, the outer contours of what passed, and each contour's area and box.
These give the answers OpenCV gives on x86 to the last bit, its 8-bit HSV and its polygon areas
included, and `tests/test_color_blob_parity.py` holds them to it against OpenCV itself. OpenCV on
Arm reads a few saturations a step off from that. This reads them the x86 way on every machine,
which moves no pixel across the threshold of any target quackd ships.

Most of a frame matches no target, so the work goes where the pixels are. `Candidates` finds the
ones bright and saturated enough for any target with cheap arithmetic, and does the exact conversion
on those alone. `biggest_blobs` joins a target's pixels into blobs with array operations, so a noisy
frame's tens of thousands of little runs cost array time, and walks the outline only of a blob whose
box could hold the area asked for. That is why the worst frames, full of small blobs, are quicker
here than through OpenCV, whose contours all come back as Python objects to be measured and sorted.
"""

from __future__ import annotations

import numpy as np

_SHIFT = 12
_ROUND = 1 << (_SHIFT - 1)
_DIVISORS = np.arange(256, dtype=np.float64)
_DIVISORS[0] = np.inf  # a value or a spread of 0 divides nothing: its tables stay 0
_SATURATION_OVER = np.rint((255 << _SHIFT) / _DIVISORS).astype(np.int32)
_HUE_OVER = np.rint((180 << _SHIFT) / (6.0 * _DIVISORS)).astype(np.int32)

_AROUND = ((0, -1), (-1, -1), (-1, 0), (-1, 1), (0, 1), (1, 1), (1, 0), (1, -1))
"""The eight neighbours of a pixel as (row, column), clockwise from the west."""


def _hsv(
    red: np.ndarray, green: np.ndarray, blue: np.ndarray
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """OpenCV's 8-bit HSV of int32 channels, in its own fixed-point arithmetic: H in [0, 180), S and
    V in [0, 255]. The tables are why the rounding matches: a float conversion differs on some."""
    value = np.maximum(np.maximum(red, green), blue)
    spread = value - np.minimum(np.minimum(red, green), blue)
    saturation = (spread * _SATURATION_OVER[value] + _ROUND) >> _SHIFT
    hue = np.where(
        value == red,
        green - blue,
        np.where(value == green, blue - red + 2 * spread, red - green + 4 * spread),
    )
    hue = (hue * _HUE_OVER[spread] + _ROUND) >> _SHIFT
    return np.where(hue < 0, hue + 180, hue), saturation, value


def rgb_to_hsv(rgb: np.ndarray) -> np.ndarray:
    """A whole uint8 RGB frame as OpenCV's 8-bit HSV. The detector does not need all of a frame
    converted (`Candidates`); this is the conversion as one thing, for what wants it so."""
    channels = rgb.astype(np.int32)
    hue, saturation, value = _hsv(channels[..., 0], channels[..., 1], channels[..., 2])
    return np.stack((hue, saturation, value), axis=-1).astype(np.uint8)


class Candidates:
    """The pixels of a uint8 RGB frame with at least `saturation` and `value`, and their exact HSV.

    A pixel's rounded saturation can only reach `saturation` if its spread times 255 reaches
    `saturation - 1` times its value, so this keeps every pixel the exact test would, and throws
    away most of a frame without dividing anything."""

    __slots__ = ("columns", "hue", "rows", "saturation", "value")

    def __init__(self, rgb: np.ndarray, saturation: int, value: int) -> None:
        red, green, blue = rgb[..., 0], rgb[..., 1], rgb[..., 2]
        top = np.maximum(np.maximum(red, green), blue)
        low = np.minimum(np.minimum(red, green), blue)
        # 255 * 255 fits in 16 bits, and 16 bits is half the work of 32
        spread = (top - low).astype(np.uint16)
        keep = (top >= value) & (spread * 255 >= top.astype(np.uint16) * max(saturation - 1, 0))
        self.rows, self.columns = np.nonzero(keep)
        picked = rgb[self.rows, self.columns].astype(np.int32)
        self.hue, self.saturation, self.value = _hsv(picked[:, 0], picked[:, 1], picked[:, 2])

    def matching(
        self, hue_low: int, hue_high: int, saturation: int, value: int
    ) -> tuple[np.ndarray, int, int] | None:
        """The pixels in the range, both hue ends included, as a mask of the smallest box holding
        them, with the box's row and column in the frame. None when no pixel is in it."""
        hit = (
            (self.hue >= hue_low)
            & (self.hue <= hue_high)
            & (self.saturation >= saturation)
            & (self.value >= value)
        )
        if not hit.any():
            return None
        rows, columns = self.rows[hit], self.columns[hit]
        row, column = int(rows.min()), int(columns.min())
        mask = np.zeros((int(rows.max()) - row + 1, int(columns.max()) - column + 1), dtype=np.int8)
        mask[rows - row, columns - column] = 1
        return mask, row, column


def _runs(mask: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Every horizontal run of a mask's pixels as parallel arrays, in raster order: row, first
    column, and the column past the last."""
    height, width = mask.shape
    padded = np.zeros((height, width + 2), dtype=np.int8)
    padded[:, 1:-1] = mask
    steps = padded[:, 1:] - padded[:, :-1]
    rows, starts = np.nonzero(steps == 1)
    stops = np.nonzero(steps == -1)[1]
    return rows, starts, stops


def _labels(rows: np.ndarray, starts: np.ndarray, stops: np.ndarray) -> np.ndarray:
    """Which blob each run is in, by 8-connectivity, as the index of the blob's first run: runs on
    adjacent rows that touch or meet at a corner are joined, then every run follows its parent up
    until nothing changes."""
    parent: np.ndarray = np.arange(rows.size, dtype=np.int64)
    # One ascending key for (row, column): runs are in raster order, so both are sorted under it.
    # A run on row r over [a, b) meets one on row r - 1 over [c, d) when c <= b and d >= a.
    width = int(stops.max()) + 3
    first = np.searchsorted(rows * width + stops, (rows - 1) * width + starts, side="left")
    past = np.searchsorted(rows * width + starts, (rows - 1) * width + stops, side="right")
    counts = np.maximum(past - first, 0)
    total = int(counts.sum())
    if not total:
        return parent
    lower = np.repeat(np.arange(rows.size, dtype=np.int64), counts)
    offsets = np.concatenate(([0], np.cumsum(counts)[:-1]))
    upper = np.arange(total, dtype=np.int64) - np.repeat(offsets, counts) + np.repeat(first, counts)
    while True:
        a, b = parent[lower], parent[upper]
        apart = a != b
        if not apart.any():
            return parent
        np.minimum.at(parent, np.maximum(a[apart], b[apart]), np.minimum(a[apart], b[apart]))
        while True:
            followed = parent[parent]
            if np.array_equal(followed, parent):
                break
            parent = followed


def _outline(rows: list[list[int]], row: int, column: int) -> list[tuple[int, int]]:
    """The outer border of the blob whose raster-first pixel is at (row, column), walked clockwise
    through pixel centres, in a grid with a border of zeros. A walk may pass a pixel twice, as a
    line one pixel wide does, and ends where it would set off the same way again."""
    start = (row, column)
    look = 0  # the raster-first pixel has background to its west
    path = [start]
    first_step: tuple[int, int] | None = None
    while True:
        for turn in range(8):
            direction = (look + turn) % 8
            next_row, next_column = row + _AROUND[direction][0], column + _AROUND[direction][1]
            if rows[next_row][next_column]:
                break
        else:
            return path  # a pixel on its own
        if (row, column) == start and (next_row, next_column) == first_step:
            path.pop()
            return path
        if first_step is None:
            first_step = (next_row, next_column)
        look = (direction + (6 if direction % 2 else 5)) % 8
        row, column = next_row, next_column
        path.append((row, column))


def _area(path: list[tuple[int, int]]) -> float:
    """The polygon through the pixel centres of an outline, by the shoelace: a square of 2 x 2
    pixels encloses 1, and one pixel, or a line of them, encloses 0."""
    twice = 0
    previous_y, previous_x = path[-1]
    for y, x in path:
        twice += previous_x * y - x * previous_y
        previous_y, previous_x = y, x
    return abs(twice) * 0.5


def _encloses(path: list[tuple[int, int]], row: int, column: int) -> bool:
    """Whether a pixel of another blob is inside an outline. It cannot be on it: two blobs share no
    pixel, and an edge of an outline joins neighbours and so passes through none."""
    inside = False
    previous_y, previous_x = path[-1]
    for y, x in path:
        if (y > row) != (previous_y > row) and (
            column < previous_x + (row - previous_y) * (x - previous_x) / (y - previous_y)
        ):
            inside = not inside
        previous_y, previous_x = y, x
    return inside


def biggest_blobs(
    mask: np.ndarray, keep: int | None, min_area: float = 0.0
) -> list[tuple[float, tuple[int, int, int, int]]]:
    """(area, (x, y, width, height)) of up to `keep` blobs of a mask with at least `min_area`,
    biggest first and all of them if `keep` is None: exactly what OpenCV's `findContours` with
    `RETR_EXTERNAL`, sorted by `contourArea`, gives. A blob is 8-connected, a blob inside another's
    hole is no outer contour, and among equal areas the one found last in raster order comes first,
    because that is the order OpenCV hands them back in and its sort is stable."""
    rows, starts, stops = _runs(mask)
    if rows.size == 0 or keep == 0:
        return []
    labels = _labels(rows, starts, stops)
    firsts, inverse = np.unique(labels, return_inverse=True)
    lefts = np.full(firsts.size, mask.shape[1], dtype=np.int64)
    rights = np.zeros(firsts.size, dtype=np.int64)
    bottoms = np.zeros(firsts.size, dtype=np.int64)
    np.minimum.at(lefts, inverse, starts)
    np.maximum.at(rights, inverse, stops)
    np.maximum.at(bottoms, inverse, rows)
    tops = rows[firsts]
    widths, heights = rights - lefts, bottoms - tops + 1
    # the most a polygon through pixel centres can enclose in that box
    bounds = (widths - 1) * (heights - 1)
    worth_walking = np.flatnonzero(bounds >= min_area)
    worth_walking = worth_walking[np.lexsort((firsts[worth_walking], bounds[worth_walking]))[::-1]]

    found: list[tuple[float, int, list[tuple[int, int]], tuple[int, int, int, int]]] = []
    best = -1.0
    for index in worth_walking.tolist():
        if keep == 1 and int(bounds[index]) < best:
            break  # none further can beat this or tie it, and the biggest is never in a hole
        top, left = int(tops[index]), int(lefts[index])
        width, height = int(widths[index]), int(heights[index])
        box = (left, top, width, height)
        first = int(firsts[index])
        if width == 1 or height == 1:
            found.append((0.0, first, [(top, left), (top + height - 1, left + width - 1)], box))
            best = max(best, 0.0)
            continue
        grid = np.zeros((height + 2, width + 2), dtype=np.uint8)
        grid[1:-1, 1:-1] = mask[top : top + height, left : left + width]
        walk = _outline(grid.tolist(), 1, int(starts[first]) - left + 1)
        area = _area(walk)
        path = [(y + top - 1, x + left - 1) for y, x in walk]
        found.append((area, first, path, box))
        best = max(best, area)

    found.sort(key=lambda blob: (blob[0], blob[1]), reverse=True)
    blobs: list[tuple[float, tuple[int, int, int, int]]] = []
    outer: list[list[tuple[int, int]]] = []
    for area, _first, path, box in found:
        if area < min_area or any(_encloses(other, *path[0]) for other in outer):
            continue
        blobs.append((area, box))
        outer.append(path)
        if keep is not None and len(blobs) >= keep:
            break
    return blobs
