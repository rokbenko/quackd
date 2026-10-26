"""The gait floor: the arithmetic between a twist quackd sends and a duck that moves.

This is the one piece of the physics backend that decides whether a run is honest. The
walking policy does not step below about 0.24 m/s, `move` defaults to 0.15 and `go_to` creeps
at 0.05, so passing those through unchanged gives a duck that reports walking and stands
still. It lived on `MicroduckBody` and so could only be tested where the physics extra and a
filled asset cache happened to meet, which was nowhere in CI. It is pure arithmetic and it is
tested here, on every runner.
"""

from __future__ import annotations

import math

import pytest

from quackd_microduck.sim3d.gait import GAIT_FLOOR_VX, GAIT_FLOOR_WZ, tilt_deg, usable_twist


def test_the_gait_floor_scales_a_twist_instead_of_dropping_or_lurching() -> None:
    # too small to step at all: standing still beats a lurch nobody asked for
    assert usable_twist((0.02, 0.0, 0.0)) == (0.0, 0.0, 0.0)
    assert usable_twist((0.0, 0.0, 0.1)) == (0.0, 0.0, 0.0)
    # Below the floor: raised, keeping the ratio, so an arc stays an arc. Written as fractions
    # of the floors rather than as two decimals, because the forward floor is a measurement
    # and it moved once already when MuJoCo went from 3.12 to 3.13. The old pair happened to
    # put both axes at their floor together, so a floor that moved a hundredth made the turn
    # the limiting axis and this read as a broken scale rather than a stale example.
    vx, _vy, wz = usable_twist((GAIT_FLOOR_VX / 2, 0.0, GAIT_FLOOR_WZ / 4))
    assert vx == pytest.approx(GAIT_FLOOR_VX), "the limiting axis is raised to its floor"
    assert wz == pytest.approx(GAIT_FLOOR_WZ / 2), "and the other keeps its share of the arc"
    # already walking: passed through untouched, however small the turn
    assert usable_twist((0.25, 0.0, 0.3)) == pytest.approx((0.25, 0.0, 0.3))
    # a turn on its own is raised to the turning floor
    assert usable_twist((0.0, 0.0, 0.6))[2] == pytest.approx(GAIT_FLOOR_WZ)
    # a fallen duck is sent nothing at all
    assert usable_twist((0.3, 0.0, 0.0), standing=False) == (0.0, 0.0, 0.0)


def test_a_twist_that_is_not_a_number_is_refused_rather_than_sent() -> None:
    """Every comparison against NaN is False, so an unguarded NaN walks through the dead zone
    with a gain of 1.0 and reaches the servos unchanged. `max()` will not catch it either: it
    only survives as the running maximum if it happens to come first."""
    for bad in (
        (math.nan, 0.0, 0.0),
        (0.0, math.nan, 0.0),  # masked by max(), which is why the guard is not a comparison
        (0.0, 0.0, math.inf),
    ):
        with pytest.raises(ValueError, match="finite"):
            usable_twist(bad)
    # and it is refused before the posture gate, so a fallen duck reports the real fault
    with pytest.raises(ValueError, match="finite"):
        usable_twist((math.nan, 0.0, 0.0), standing=False)


def test_reading_the_tilt_of_an_upside_down_duck_does_not_raise() -> None:
    """`gravity_z` is a matrix element, so it leaves [-1, 1] by about 1e-10 at either pole and
    `acos` raises rather than saturating. Only the upper bound was clamped, so `extras()`,
    `snapshot()` and every `get_state()` raised while the duck was on its back: the reading a
    fall is exactly when a pilot needs it."""
    assert tilt_deg(-1.0) == pytest.approx(0.0)
    assert tilt_deg(0.0) == pytest.approx(90.0)
    assert tilt_deg(1.0) == pytest.approx(180.0)
    # the two that used to raise, one of them on every observation of a fallen duck
    assert tilt_deg(1.0000000002) == pytest.approx(180.0)
    assert tilt_deg(-1.0000000002) == pytest.approx(0.0)
