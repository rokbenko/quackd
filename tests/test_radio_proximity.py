"""Proximity on a clock the test turns: near after a held median at the threshold, far after a
held one below the hysteresis, or silence, or a lost link; and the table that never grows past
its bound. Every threshold and hold is read off `ProximityPolicy`."""

from __future__ import annotations

import math

import pytest

from quackd.radio import Sighting
from quackd.radio.proximity import ProximityEvent, ProximityPolicy, Tracker

POLICY = ProximityPolicy()
PEER = "00112233aabbccdd"
STEP = 0.2  # five adverts a second, as the sim and a BLE advertiser at 200 ms send


def heard(at: float, rssi: float, beacon: str = PEER, flags: int = 3) -> Sighting:
    return Sighting(beacon, flags, rssi, "sim:peer", at)


def feed(
    tracker: Tracker, start: float, until: float, rssi: float, beacon: str = PEER
) -> list[ProximityEvent]:
    events: list[ProximityEvent] = []
    t = start
    while t <= until + 1e-9:
        events += tracker.observe(heard(t, rssi, beacon))
        events += tracker.tick(t)
        t = round(t + STEP, 6)
    return events


def kinds(events: list[ProximityEvent]) -> list[str]:
    return [e.kind for e in events]


def test_the_thresholds_are_the_model_at_the_meeting_distance() -> None:
    assert POLICY.near_dbm == pytest.approx(
        POLICY.rssi_at_1m - 10 * POLICY.path_loss_n * math.log10(POLICY.meet_within_m)
    )
    assert POLICY.far_dbm == POLICY.near_dbm - POLICY.hysteresis_db
    assert POLICY.metres(POLICY.near_dbm) == pytest.approx(POLICY.meet_within_m)
    assert POLICY.metres(POLICY.rssi_at_1m) == pytest.approx(1.0)
    # the plan's figures, which the docs quote: 2 m at -59 dBm and n 2.2
    assert round(POLICY.near_dbm, 1) == -65.6 and round(POLICY.far_dbm, 1) == -71.6


def test_a_robot_is_near_once_its_median_has_held_at_the_threshold_long_enough() -> None:
    tracker = Tracker(POLICY)
    events = feed(tracker, 0.0, POLICY.near_hold_s - STEP, POLICY.near_dbm + 1)
    assert kinds(events) == ["seen"]
    assert not tracker.is_near(PEER)
    events = feed(tracker, POLICY.near_hold_s, POLICY.near_hold_s + 2 * STEP, POLICY.near_dbm + 1)
    near = [e for e in events if e.kind == "near"]
    assert len(near) == 1 and tracker.is_near(PEER)
    assert f">= {POLICY.near_dbm:.1f} dBm" in near[0].reason
    assert near[0].metres is not None and near[0].metres < POLICY.meet_within_m


def test_one_loud_advert_is_not_near_and_one_deep_dip_is_not_far() -> None:
    tracker = Tracker(POLICY)
    tracker.observe(heard(0.0, -30.0))
    assert tracker.tick(POLICY.near_hold_s * 3) == [] and not tracker.is_near(PEER)
    tracker = Tracker(POLICY)
    feed(tracker, 0.0, POLICY.near_hold_s + 1, POLICY.near_dbm + 3)
    assert tracker.is_near(PEER)
    start = POLICY.near_hold_s + 1 + STEP
    # one advert in three, one advertising channel of three, lost in a 30 dB multipath dip,
    # for longer than far takes: a median does not move, where a mean would fall past far
    t, events = start, []
    while t < start + POLICY.window_s + POLICY.far_hold_s * 2:
        dip = round((t - start) / STEP) % 3 == 0
        events += tracker.observe(heard(t, POLICY.near_dbm + 3 - (30 if dip else 0)))
        events += tracker.tick(t)
        t = round(t + STEP, 6)
    assert "far" not in kinds(events) and tracker.is_near(PEER)


def test_far_takes_the_hysteresis_held_for_the_far_hold() -> None:
    tracker = Tracker(POLICY)
    feed(tracker, 0.0, 2.0, POLICY.near_dbm + 2)
    assert tracker.is_near(PEER)
    # between the two thresholds: neither near nor far changes
    events = feed(tracker, 2.2, 2.2 + POLICY.far_hold_s * 2, POLICY.far_dbm + 1)
    assert "far" not in kinds(events) and tracker.is_near(PEER)
    start = 2.2 + POLICY.far_hold_s * 2 + STEP
    weak = POLICY.far_dbm - 1
    events = feed(tracker, start, start + POLICY.window_s + POLICY.far_hold_s + 1, weak)
    far = [e for e in events if e.kind == "far"]
    assert len(far) == 1 and far[0].reason.startswith("weak:")
    # the median has to have been weak for the whole hold, so far comes after it, not before
    assert far[0].at - start >= POLICY.far_hold_s


def test_a_robot_unheard_for_the_stale_time_is_far_unless_a_link_holds_it() -> None:
    tracker = Tracker(POLICY)
    feed(tracker, 0.0, 2.0, POLICY.near_dbm + 2)
    assert tracker.tick(2.0 + POLICY.stale_s - STEP) == []
    events = tracker.tick(2.0 + POLICY.stale_s + STEP)
    assert kinds(events) == ["far"] and events[0].reason.startswith("stale:")
    held = Tracker(POLICY)
    feed(held, 0.0, 2.0, POLICY.near_dbm + 2)
    held.hold(PEER, 2.0)
    assert held.tick(2.0 + POLICY.stale_s * 3) == [] and held.is_near(PEER)
    # a hold keeps silence from meaning far, never quiet adverts
    events = feed(held, 30.0, 30.0 + POLICY.window_s + POLICY.far_hold_s + 1, POLICY.far_dbm - 2)
    assert "far" in kinds(events)


def test_a_lost_link_is_far_at_once_and_a_closed_one_changes_nothing() -> None:
    tracker = Tracker(POLICY)
    feed(tracker, 0.0, 2.0, POLICY.near_dbm + 2)
    tracker.hold(PEER, 2.0)
    assert tracker.release(PEER, 2.1, lost=False) == [] and tracker.is_near(PEER)
    tracker.hold(PEER, 2.2)
    events = tracker.release(PEER, 2.3, lost=True)
    assert kinds(events) == ["far"] and events[0].reason == "link lost"
    assert tracker.release("ffffffffffffffff", 2.4, lost=True) == []


def test_a_suspended_tracker_lets_no_time_pass() -> None:
    tracker = Tracker(POLICY)
    feed(tracker, 0.0, 2.0, POLICY.near_dbm + 2)
    tracker.suspend(2.0)
    assert tracker.suspended and tracker.tick(500.0) == []
    tracker.resume(500.0)
    assert not tracker.suspended
    # as if it were still 2.0: no staler than then, and stale once the stale time passes after
    assert tracker.tick(500.0 + STEP) == []
    events = tracker.tick(500.0 + POLICY.stale_s + STEP)
    assert kinds(events) == ["far"]


def test_heard_answers_for_recent_adverts_and_open_links() -> None:
    tracker = Tracker(POLICY)
    tracker.observe(heard(10.0, -80.0))
    assert tracker.heard(PEER, within_s=8, now=17.9)
    assert not tracker.heard(PEER, within_s=8, now=18.1)
    tracker.hold(PEER, 18.1)
    assert tracker.heard(PEER, within_s=8, now=40.0)
    assert not tracker.heard("ffffffffffffffff", within_s=8, now=10.0)


def test_seen_is_said_when_first_heard_and_again_now_and_then() -> None:
    tracker = Tracker(POLICY)
    events = feed(tracker, 0.0, POLICY.seen_every_s * 2.5, POLICY.far_dbm - 10)
    seen = [e for e in events if e.kind == "seen"]
    assert [e.reason for e in seen] == ["first heard", "still heard", "still heard"]


def test_its_own_beacon_and_a_strength_that_is_no_number_are_never_peers() -> None:
    tracker = Tracker(POLICY, me=PEER)
    assert tracker.observe(heard(0.0, -40.0)) == []
    assert tracker.observe(heard(0.0, math.nan, "ffffffffffffffff")) == []
    assert tracker.peers(0.0) == []


def test_the_table_stays_bounded_and_a_flood_never_pushes_out_a_robot_that_is_near() -> None:
    tracker = Tracker(POLICY, max_peers=8)
    feed(tracker, 0.0, 2.0, POLICY.near_dbm + 3)
    assert tracker.is_near(PEER)
    for i in range(100):
        tracker.observe(heard(3.0 + i * 0.01, -90.0, f"{i:016x}"))
    assert len(tracker.peers(5.0)) == 8 and tracker.is_near(PEER)
    # a table full of robots near or held takes no more
    full = Tracker(POLICY, max_peers=2)
    for beacon in ("aa" * 8, "bb" * 8):
        feed(full, 0.0, 2.0, POLICY.near_dbm + 3, beacon)
    assert full.observe(heard(3.0, -40.0, "cc" * 8)) == []
    assert {p.beacon_id for p in full.peers(3.0)} == {"aa" * 8, "bb" * 8}


def test_robots_long_gone_are_forgotten_and_the_rest_listed_loudest_first() -> None:
    tracker = Tracker(POLICY)
    tracker.observe(heard(0.0, -80.0, "aa" * 8))
    tracker.observe(heard(0.0, -60.0, "bb" * 8))
    views = tracker.peers(0.1)
    assert [v.beacon_id for v in views] == ["bb" * 8, "aa" * 8]
    assert views[0].metres == pytest.approx(POLICY.metres(-60.0), abs=0.01)
    tracker.tick(2 * POLICY.stale_s + 1)
    assert tracker.peers(2 * POLICY.stale_s + 1) == []


def test_a_fast_scene_scales_every_clock_and_no_threshold() -> None:
    fast = POLICY.scaled(4.0)
    assert fast.near_hold_s == POLICY.near_hold_s / 4 and fast.stale_s == POLICY.stale_s / 4
    assert fast.window_s == POLICY.window_s / 4 and fast.far_hold_s == POLICY.far_hold_s / 4
    assert fast.near_dbm == POLICY.near_dbm and fast.far_dbm == POLICY.far_dbm
    with pytest.raises(ValueError):
        POLICY.scaled(0)


def test_an_advert_heard_while_the_tracker_was_paused_keeps_the_time_it_was_heard() -> None:
    """The scanner can start again after a connect, and an advert arrive, before the service
    tells the tracker to resume: that advert is not moved on by the length of the pause."""
    tracker = Tracker(POLICY)
    feed(tracker, 0.0, 2.0, POLICY.near_dbm + 2)
    tracker.suspend(10.0)
    tracker.observe(heard(11.0, POLICY.near_dbm + 2))
    tracker.resume(12.0)
    # unheard since 11.0: far once the stale time has passed from then, not from later
    assert tracker.tick(11.0 + POLICY.stale_s - STEP) == []
    assert kinds(tracker.tick(11.0 + POLICY.stale_s + STEP)) == ["far"]


def test_a_link_that_opened_before_its_robot_was_heard_still_holds_it() -> None:
    tracker = Tracker(POLICY)
    tracker.hold(PEER, 0.0)  # a link, before any advert of it was heard
    feed(tracker, 0.0, 2.0, POLICY.near_dbm + 2)
    tracker.hold(PEER, 2.0)  # a second link
    tracker.release(PEER, 2.1, lost=False)  # the first closes
    assert tracker.peers(2.2)[0].held
    assert tracker.tick(2.0 + POLICY.stale_s * 3) == [] and tracker.is_near(PEER)
    assert tracker.heard(PEER, within_s=1, now=2.0 + POLICY.stale_s * 3)
    tracker.release(PEER, 50.0, lost=False)
    assert kinds(tracker.tick(50.1)) == ["far"]
