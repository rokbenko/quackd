"""Coloured blobs in an RGB frame, with numpy alone: what the default detector needs of a frame.

It used to ask OpenCV, which is half of a default install on disk and most of its download, for an
HSV conversion, a threshold, the outer contours of what passed, and each contour's area and box.
These give the answers OpenCV gives on x86 to the last bit, its 8-bit HSV and its polygon areas
included, and `tests/test_color_blob_parity.py` holds them to it against OpenCV itself. OpenCV on
Apple Silicon and Linux on Arm reads a few saturations a step off from that. This reads them the x86
way on every machine, which moves no pixel across the threshold of any target quackd ships.

Most of a frame matches no target, so the work goes where the pixels are. `Candidates` finds the
ones bright and saturated enough for any target with cheap arithmetic, and does the exact conversion
on those alone. `biggest_blobs` joins a target's pixels into blobs and reads each blob's area off
its runs, all with array operations and no walk round an outline. That is why a frame of tens of
thousands of tiny blobs is quicker here than through OpenCV, whose contours all come back as Python
objects to be measured and sorted. A frame of many short runs is slower here, since a mask and its
background are labelled run by run, where OpenCV follows an edge in C.
"""

from __future__ import annotations

import numpy as np

_SHIFT = 12
_ROUND = 1 << (_SHIFT - 1)
_DIVISORS = np.arange(256, dtype=np.float64)
_DIVISORS[0] = np.inf  # a value or a spread of 0 divides nothing: its tables stay 0
_SATURATION_OVER = np.rint((255 << _SHIFT) / _DIVISORS).astype(np.int32)
_HUE_OVER = np.rint((180 << _SHIFT) / (6.0 * _DIVISORS)).astype(np.int32)


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
    rows, columns = np.nonzero(padded[:, 1:] != padded[:, :-1])
    return rows[::2], columns[::2], columns[1::2]


def _spread(firsts: np.ndarray, counts: np.ndarray) -> np.ndarray:
    """`firsts[i]` and the `counts[i] - 1` numbers after it, for each i in turn."""
    offsets = np.cumsum(counts) - counts
    total = int(counts.sum())
    return np.arange(total, dtype=np.int64) - np.repeat(offsets, counts) + np.repeat(firsts, counts)


def _meeting(
    rows: np.ndarray, starts: np.ndarray, stops: np.ndarray, corners: bool
) -> tuple[np.ndarray, np.ndarray]:
    """Each pair of runs on adjacent rows that are joined, as the later run and the earlier. A run
    on row r over [a, b) meets one on row r - 1 over [c, d) at a corner or better when c <= b and
    d >= a, and shares a column with it when c < b and d > a."""
    # One ascending key for (row, column): runs are in raster order, so both are sorted under it.
    width = int(stops.max()) + 3
    first = np.searchsorted(
        rows * width + stops, (rows - 1) * width + starts, side="left" if corners else "right"
    )
    past = np.searchsorted(
        rows * width + starts, (rows - 1) * width + stops, side="right" if corners else "left"
    )
    counts = np.maximum(past - first, 0)
    return np.repeat(np.arange(rows.size, dtype=np.int64), counts), _spread(first, counts)


def _firsts(count: int, later: np.ndarray, earlier: np.ndarray) -> np.ndarray:
    """The first run of the blob each of `count` runs is in, given pairs that are joined. Each round
    hooks every first still apart under the smallest it is paired with, flattens what it hooked, and
    keeps only the pairs not yet together, so that rounds after the first touch few runs."""
    parent: np.ndarray = np.arange(count, dtype=np.int64)
    while later.size:
        np.minimum.at(parent, later, earlier)
        while True:
            hooked = parent[later]
            jumped = parent[hooked]
            if np.array_equal(hooked, jumped):
                break
            parent[later] = jumped
        one, other = parent[later], parent[earlier]
        apart = one != other
        later, earlier = np.maximum(one[apart], other[apart]), np.minimum(one[apart], other[apart])
    while True:
        followed = parent[parent]
        if np.array_equal(followed, parent):
            return parent
        parent = followed


def _filled(mask: np.ndarray) -> np.ndarray:
    """The mask with what its blobs enclose set too: the background no 4-connected path leads out of
    the frame from, and any blob in there with it, which is no outer contour."""
    height, width = mask.shape
    around = np.ones((height + 2, width + 2), dtype=np.int8)
    around[1:-1, 1:-1] = 1 - mask
    rows, starts, stops = _runs(around)
    enclosed = _firsts(rows.size, *_meeting(rows, starts, stops, corners=False)) != 0
    if not enclosed.any():  # run 0 is the row above the frame, so whatever joins it is outside
        return mask
    lengths = stops[enclosed] - starts[enclosed]
    filled = mask.copy()
    filled[np.repeat(rows[enclosed], lengths) - 1, _spread(starts[enclosed], lengths) - 1] = 1
    return filled


def biggest_blobs(
    mask: np.ndarray, keep: int | None, min_area: float = 0.0
) -> list[tuple[float, tuple[int, int, int, int]]]:
    """(area, (x, y, width, height)) of up to `keep` blobs of a mask of 0s and 1s with at least
    `min_area`, biggest first and all of them if `keep` is None: exactly what OpenCV's
    `findContours` with `RETR_EXTERNAL`, sorted by `contourArea`, gives. A blob is 8-connected, a
    blob inside another's hole is no outer contour, and among equal areas the one found last in
    raster order comes first, because that is the order OpenCV hands them back in and its sort is
    stable.

    The area is of the polygon through the pixel centres of a blob's outer border, and takes no walk
    round it. With what the blob encloses filled in, that polygon holds a whole 2 x 2 window of
    pixels where all 4 are set, half of one where 3 are, and none of any other. A window with 3 or 4
    set has a column set in both its rows, so it belongs to one pair of overlapping runs. A pair
    sharing n columns holds n - 1 whole windows, and half of one at each end where the two do not
    line up."""
    if keep == 0 or not mask.any():
        return []
    rows, starts, stops = _runs(_filled(mask))
    later, earlier = _meeting(rows, starts, stops, corners=True)
    firsts_of = _firsts(rows.size, later, earlier)
    is_first = firsts_of == np.arange(rows.size)
    firsts = np.flatnonzero(is_first)
    blob_of = (np.cumsum(is_first) - 1)[firsts_of]

    lefts = np.full(firsts.size, mask.shape[1], dtype=np.int64)
    rights = np.zeros(firsts.size, dtype=np.int64)
    bottoms = np.zeros(firsts.size, dtype=np.int64)
    np.minimum.at(lefts, blob_of, starts)
    np.maximum.at(rights, blob_of, stops)
    np.maximum.at(bottoms, blob_of, rows)
    tops = rows[firsts]

    a, b, c, d = starts[later], stops[later], starts[earlier], stops[earlier]
    shared = np.minimum(b, d) - np.maximum(a, c)
    overlap = shared > 0
    twice = 2 * (shared[overlap] - 1) + (a != c)[overlap] + (b != d)[overlap]
    areas = 0.5 * np.bincount(blob_of[later[overlap]], weights=twice, minlength=firsts.size)

    order = np.lexsort((firsts, areas))[::-1]
    order = order[areas[order] >= min_area][:keep]
    return [
        (
            float(areas[blob]),
            (
                int(lefts[blob]),
                int(tops[blob]),
                int(rights[blob] - lefts[blob]),
                int(bottoms[blob] - tops[blob] + 1),
            ),
        )
        for blob in order.tolist()
    ]
