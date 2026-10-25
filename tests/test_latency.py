"""Nearest rank, the one definition every percentile in a summary is read off."""

from __future__ import annotations

import pytest

from quackd.latency import SPREAD, nearest_rank, spread


def test_a_percentile_is_always_a_value_that_was_measured() -> None:
    """With ten calls the p90 is the ninth longest, which a reader can find among the `llm`
    records: an interpolated figure would sit between two calls and be neither."""
    ten = [float(n) for n in range(1, 11)]
    assert nearest_rank(ten, 0.5) == 5.0
    assert nearest_rank(ten, 0.9) == 9.0
    assert nearest_rank(ten, 1.0) == 10.0
    assert nearest_rank(list(reversed(ten)), 0.9) == 9.0, "the order they came in is not a rank"
    for q in (0.1, 0.25, 0.5, 0.75, 0.9, 0.95, 1.0):
        assert nearest_rank(ten, q) in ten


def test_one_call_is_its_own_median_and_its_own_worst() -> None:
    assert spread([6.21]) == {"p50": 6.21, "p90": 6.21, "max": 6.21}


def test_two_calls_have_the_shorter_for_a_median() -> None:
    """Nearest rank takes the smallest value with at least half at or below it, so of two the
    median is the shorter, and the p90 and the worst are the longer."""
    assert spread([8.0, 2.0]) == {"p50": 2.0, "p90": 8.0, "max": 8.0}


def test_one_slow_call_shows_in_the_spread_and_not_in_the_median() -> None:
    """The case a total cannot see: nine quick answers and one that hung look like ten
    average ones once they are added up."""
    calls = [1.0] * 9 + [60.0]
    figures = spread(calls)
    assert figures == {"p50": 1.0, "p90": 1.0, "max": 60.0}
    assert sum(calls) / len(calls) == pytest.approx(6.9), "the mean says neither thing"


def test_nothing_measured_is_no_figures_at_all() -> None:
    """A summary with nothing to say about a clock leaves its keys out, rather than writing
    zeros nobody measured, and asking for the quantile of nothing is a mistake, not a zero."""
    assert spread([]) is None
    with pytest.raises(ValueError):
        nearest_rank([], 0.5)
    for q in (0.0, -0.1, 1.1):
        with pytest.raises(ValueError):
            nearest_rank([1.0], q)


def test_the_figures_are_rounded_like_a_records_seconds() -> None:
    figures = spread([0.12345, 0.98765])
    assert figures == {"p50": 0.123, "p90": 0.988, "max": 0.988}
    assert tuple(figures) == SPREAD, "the order a line prints them in"
