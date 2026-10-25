"""Nearest-rank percentiles for the seconds a run spent waiting on something.

`summary.json` has carried one total per clock since ADR-0041: `llm_latency_s` for the model,
`latency_s` in the stepper's block. A total says where a run's time went and nothing about the
calls that spent it, and ten calls of six seconds and one of sixty beside nine of one add up the
same. Somebody deciding whether a pilot feels slow wants the typical call and the bad one, so a
summary carries the median, the ninetieth percentile and the longest beside each total.

Nearest rank rather than interpolation, because every figure it gives is a call that happened:
with ten calls the p90 is the ninth longest, a number a reader can find among the `llm` records,
where an interpolated one would sit between two calls and be neither. The same three figures are
what a `quackd drive` session reports per lane, so both read off one definition here.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

SPREAD = ("p50", "p90", "max")
"""The figures `spread` gives, in the order a line prints them."""


def nearest_rank(values: Sequence[float], q: float) -> float:
    """The `q` quantile of `values` by nearest rank: the smallest of them with at least a share
    `q` of all of them at or below it. `q` is in (0, 1], and `values` may not be empty, since
    the quantile of nothing is not zero."""
    if not values:
        raise ValueError("the quantile of no values is not a number")
    if not 0 < q <= 1:
        raise ValueError(f"a quantile is in (0, 1], not {q}")
    ordered = sorted(values)
    return ordered[max(1, math.ceil(q * len(ordered))) - 1]


def spread(values: Sequence[float], *, digits: int = 3) -> dict[str, float] | None:
    """`{"p50": ..., "p90": ..., "max": ...}` of `values`, each rounded to `digits`, or None for
    no values, so a summary with nothing to say about a clock leaves its keys out rather than
    writing zeros nobody measured."""
    if not values:
        return None
    return {
        "p50": round(nearest_rank(values, 0.5), digits),
        "p90": round(nearest_rank(values, 0.9), digits),
        "max": round(max(values), digits),
    }
