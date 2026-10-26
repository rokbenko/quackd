"""Odometry read as a pose: what it walked, and every way its frame begins again."""

from __future__ import annotations

import math

import pytest

from quackd.space.odometry import OdometrySource, OdometryTrack
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
