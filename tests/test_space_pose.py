"""A pose, the frame it belongs to, and the one shape a reading takes outside quackd.space."""

from __future__ import annotations

import math

import pytest

from quackd.space.pose import Fix, FrameRef, Pose2D, where_record, where_summary, wrap_angle


def _same(a: Pose2D, b: Pose2D) -> bool:
    return (
        a.x == pytest.approx(b.x, abs=1e-9)
        and a.y == pytest.approx(b.y, abs=1e-9)
        and wrap_angle(a.yaw - b.yaw) == pytest.approx(0.0, abs=1e-9)
    )


def test_an_angle_is_wrapped_into_the_half_open_turn() -> None:
    assert wrap_angle(math.pi) == math.pi
    assert wrap_angle(-math.pi) == math.pi
    assert wrap_angle(3 * math.pi / 2) == pytest.approx(-math.pi / 2)
    assert wrap_angle(-5 * math.pi / 2) == pytest.approx(-math.pi / 2)


@pytest.mark.parametrize(
    "pose", [Pose2D(0.0, 0.0, 0.0), Pose2D(1.2, -0.4, 0.7), Pose2D(-3.0, 2.0, -2.9)]
)
def test_a_pose_composed_with_its_inverse_is_the_origin(pose: Pose2D) -> None:
    origin = Pose2D(0.0, 0.0, 0.0)
    assert _same(pose.compose(pose.inverse()), origin)
    assert _same(pose.inverse().compose(pose), origin)


def test_relative_to_is_what_the_origin_sees() -> None:
    origin = Pose2D(1.0, 1.0, math.pi / 2)
    ahead = Pose2D(1.0, 2.0, math.pi / 2)
    seen = ahead.relative_to(origin)
    assert _same(seen, Pose2D(1.0, 0.0, 0.0)), "one metre straight ahead of it"
    assert _same(origin.compose(seen), ahead)


def test_a_bearing_is_from_the_heading_and_positive_to_the_left() -> None:
    facing_y = Pose2D(0.0, 0.0, math.pi / 2)
    assert facing_y.bearing_to(0.0, 1.0) == pytest.approx(0.0)
    assert facing_y.bearing_to(-1.0, 0.0) == pytest.approx(math.pi / 2)
    assert facing_y.bearing_to(1.0, 0.0) == pytest.approx(-math.pi / 2)
    assert facing_y.distance_to(3.0, 4.0) == pytest.approx(5.0)


def test_a_frame_id_carries_its_epoch_only_once_there_is_one() -> None:
    assert FrameRef("odometry", "odom:c1", persistent=False).id == "odom:c1"
    assert FrameRef("odometry", "odom:c1", persistent=False, epoch=2).id == "odom:c1#2"


def test_a_fix_is_localized_only_with_a_pose_a_frame_and_no_word_for_lost() -> None:
    frame = FrameRef("map", "map:m", persistent=True)
    here = Pose2D(0.0, 0.0, 0.0)
    assert Fix(here, "map", frame).localized
    assert not Fix(None, "map", frame, lost="carried").localized
    assert not Fix(here, "map", None).localized
    assert not Fix(here, "map", frame, lost="bumped").localized


def test_a_reading_says_what_it_knows_and_nothing_it_does_not() -> None:
    odometry = Fix(
        Pose2D(1.2, -0.5, 1.5708),
        "odometry",
        FrameRef("odometry", "odom:c", persistent=False),
        walked_m=3.217,
    )
    record = where_record(odometry)
    assert record == {
        "kind": "odometry",
        "frame": "odom:c",
        "x": 1.2,
        "y": -0.5,
        "yaw": 1.571,
        "walked_m": 3.22,
    }
    assert where_summary(record) == "odometry(1.20, -0.50, 1.57 rad, walked 3.2 m)"

    lost = Fix(None, "map", FrameRef("map", "map:m", persistent=True), lost="carried", keyframes=7)
    assert where_record(lost) == {
        "kind": "map",
        "frame": "map:m",
        "lost": "carried",
        "keyframes": 7,
    }
    assert where_summary(where_record(lost)) == "lost(carried)"

    mapped = Fix(Pose2D(0.1, 0.2, -3.0), "map", FrameRef("map", "map:m", persistent=True))
    assert where_summary(where_record(mapped)) == "map(0.10, 0.20, -3.00 rad)"
    assert where_summary({"kind": "map"}) == "unknown", "a record with no pose claims none"
