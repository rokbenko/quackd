"""Odometry read as a pose: what it walked, and every way its frame begins again."""

from __future__ import annotations

import math

import pytest

from quackd.space.odometry import PERIOD_SAMPLES, OdometrySource, OdometryTrack
from quackd.space.pose import Pose2D


def track(**kwargs: float) -> OdometryTrack:
    return OdometryTrack("odom:test", **kwargs)


def test_a_walk_counts_its_metres_and_the_robots_clock_gives_the_period() -> None:
    t = track()
    for i in range(5):
        t.update(0.1 * i, 0.0, 0.0, 10.0 + 0.1 * i)
    fix = t.fix()
    assert fix.localized and fix.pose == Pose2D(0.1 * 4, 0.0, 0.0)
    assert fix.walked_m == pytest.approx(0.4)
    assert t.period_s == pytest.approx(0.1)
    assert fix.frame is not None and fix.frame.id == "odom:test" and not fix.frame.persistent


def test_the_same_reading_twice_walks_nowhere_and_times_nothing() -> None:
    """A stream that has frozen sends its last frame again: no time passed, so no step counts,
    whatever the pose says."""
    t = track()
    t.update(0.0, 0.0, 0.0, 1.0)
    t.update(0.0, 0.0, 0.0, 1.0)
    t.update(0.1, 0.0, 0.0, 1.0)
    assert t.walked_m == 0.0 and t.period_s is None
    assert t.fix().t == 1.0


def test_picking_the_duck_up_ends_its_frame_and_putting_it_down_begins_another() -> None:
    t = track()
    t.update(0.0, 0.0, 0.0, 1.0)
    t.update(0.5, 0.0, 0.0, 2.0)
    before = t.frame
    t.update(0.6, 0.1, 0.0, 2.1, held="picked up")
    held = t.fix()
    assert held.pose is None and held.lost == "picked up" and not held.localized
    t.update(0.9, 0.2, 0.0, 2.2, held="picked up")
    assert t.frame.epoch == before.epoch + 1, "one new frame for the whole time it was held"
    t.update(1.0, 0.2, 0.3, 3.0)
    down = t.fix()
    assert down.localized and down.frame is not None and down.frame.id == "odom:test#1"
    assert down.walked_m == 0.0, "nothing walked yet in the frame that began"


def test_a_clock_that_goes_back_begins_the_frame_again() -> None:
    t = track()
    t.update(0.0, 0.0, 0.0, 5.0)
    t.update(0.1, 0.0, 0.0, 5.1)
    t.update(0.1, 0.0, 0.0, 0.2)  # whatever produced the readings started over
    assert t.frame.epoch == 1 and t.walked_m == 0.0


def test_pauses_fewer_than_half_its_samples_leave_the_period_the_streams_own() -> None:
    """A mean let one pause stretch the period, and every window a walk sizes from it: a
    stream that went quiet for half a minute read afterwards as a slow one."""
    period, pause = 0.02, 30.0
    t, clock = track(), 100.0
    t.update(0.0, 0.0, 0.0, clock)
    pauses = (PERIOD_SAMPLES - 1) // 2
    for i in range(PERIOD_SAMPLES):
        clock += pause if i < pauses else period
        t.update(0.0, 0.0, 0.0, clock)
    assert t.period_s == pytest.approx(period)


def test_one_interval_alone_gives_no_period_and_two_give_the_shorter() -> None:
    """One interval may be a pause, so it says nothing; of two, the shorter, since a period
    read short only tightens the windows sized from it."""
    t = track()
    t.update(0.0, 0.0, 0.0, 100.0)
    t.update(0.0, 0.0, 0.0, 130.0)
    assert t.period_s is None
    t.update(0.0, 0.0, 0.0, 130.02)
    assert t.period_s == pytest.approx(0.02)


def test_a_frame_something_else_keeps_goes_on_when_the_clock_goes_back() -> None:
    """A rover's run says when its odometry starts over, so a date going back there is a clock
    being set, or read loosely: the frame and what was walked in it go on, and the period is
    measured afresh."""
    t = OdometryTrack("odom:run", persistent=True, restarts_with_clock=False)
    for i in range(3):
        t.update(0.1 * i, 0.0, 0.0, 5.0 + 0.1 * i)
    walked = t.walked_m
    t.update(0.3, 0.0, 0.0, 5.0)  # dated a fifth of a second back
    assert t.frame.epoch == 0 and t.walked_m == pytest.approx(walked)
    assert t.period_s is None and t.fix().localized


def test_readings_dated_on_another_clock_from_here_on_go_on_in_the_same_frame() -> None:
    """A stream dated by its arrival here until the stack began stamping it: the stamps are
    another clock, far behind this machine's, and the frame goes on across the change."""
    t = track()
    t.update(0.0, 0.0, 0.0, 50_000.0)
    t.update(0.1, 0.0, 0.0, 50_000.1)
    t.reclock()
    for i in range(3):
        t.update(0.2 + 0.1 * i, 0.0, 0.0, 3.0 + 0.1 * i)
    assert t.frame.epoch == 0 and t.fix().t == pytest.approx(3.2)
    assert t.period_s == pytest.approx(0.1)


def test_a_step_no_walk_could_make_begins_the_frame_again() -> None:
    t = track(max_speed_mps=0.5)
    t.update(0.0, 0.0, 0.0, 1.0)
    t.update(0.05, 0.0, 0.0, 1.1)  # half a metre a second: a walk
    assert t.frame.epoch == 0 and t.walked_m == pytest.approx(0.05)
    t.update(0.12, 0.0, 0.0, 1.3)  # a frame came late: the same pace over twice the time
    assert t.frame.epoch == 0
    t.update(1.12, 0.0, 0.0, 1.4)  # a metre in a tenth of a second
    assert t.frame.epoch == 1 and t.walked_m == 0.0


def test_a_reading_that_is_not_a_number_moves_nothing() -> None:
    t = track()
    t.update(0.0, 0.0, 0.0, 1.0)
    t.update(math.nan, 0.0, 0.0, 1.1)
    t.update(0.0, math.inf, 0.0, 1.2)
    assert t.fix().pose == Pose2D(0.0, 0.0, 0.0) and t.walked_m == 0.0


def test_no_reading_yet_is_no_pose() -> None:
    fix = track().fix()
    assert fix.pose is None and fix.lost == "no odometry reading yet"


def test_a_source_whose_stream_went_stale_has_no_pose_and_says_why() -> None:
    t = track()
    t.update(0.0, 0.0, 0.0, 1.0)
    why: list[str | None] = [None]
    source = OdometrySource(t, stale=lambda: why[0], label="contact odometry from robotd")
    assert source.fix().localized
    why[0] = "the newest robot.state frame is 3.0 s old"
    assert source.fix().lost == "the newest robot.state frame is 3.0 s old"
    assert source.describe() == "contact odometry from robotd"
    assert not source.has_grid and source.period_s is None
