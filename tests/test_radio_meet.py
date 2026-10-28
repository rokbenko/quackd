"""The meeting planner on a clock the test turns: who dials first, how often a robot is tried
again, what a refusal and a meeting just ended hold back, and what parting owes. Every interval is
read off `MeetPolicy`."""

from __future__ import annotations

import random

from quackd.passport.beacon import FLAG_LAN, FLAG_LINK
from quackd.radio.meet import Meet, MeetPolicy, Outcome, Part, Planner
from quackd.radio.proximity import ProximityEvent

POLICY = MeetPolicy()
LOW, MID, HIGH = "1" * 16, "5" * 16, "9" * 16
DID_A, DID_B = "did:key:z6MkA", "did:key:z6MkB"


def ev(kind: str, beacon: str, at: float, flags: int = FLAG_LINK) -> ProximityEvent:
    return ProximityEvent(kind, beacon, f"sim:{beacon[:4]}", flags, -60.0, 1.0, at, "test")  # type: ignore[arg-type]


def planner(me: str = MID, **kw: object) -> Planner:
    return Planner(lambda: me, POLICY, rng=random.Random(1), **kw)  # type: ignore[arg-type]


def meets(actions: list[Meet | Part]) -> list[str]:
    return [a.beacon_id for a in actions if isinstance(a, Meet)]


def test_the_lower_beacon_id_dials_at_once_and_the_higher_after_the_grace() -> None:
    p = planner(MID)
    p.on_proximity(ev("near", HIGH, 0.0))  # this robot is the lower of the two
    p.on_proximity(ev("near", LOW, 0.0))  # and the higher of these
    assert meets(p.due(0.0)) == [HIGH]
    assert meets(p.due(POLICY.grace_s - 0.1)) == []
    assert meets(p.due(POLICY.grace_s)) == [LOW]


def test_a_robot_that_does_not_advertise_dials_everyone_at_once() -> None:
    p = planner(MID, advertising=False)
    p.on_proximity(ev("near", LOW, 0.0))
    assert meets(p.due(0.0)) == [LOW]


def test_no_more_meetings_at_once_than_the_policy_allows() -> None:
    p = planner("0" * 16)
    beacons = [f"{i:016x}" for i in range(1, 6)]
    for b in beacons:
        p.on_proximity(ev("near", b, 0.0))
    assert len(meets(p.due(0.0))) == POLICY.max_meetings
    assert meets(p.due(0.1)) == []
    p.on_outcome(beacons[0], Outcome("met", DID_A, "ble://1", "link"), 0.2)
    assert len(meets(p.due(0.3))) == 1


def test_an_unreachable_robot_is_tried_again_further_apart_each_time() -> None:
    p = planner(MID)
    p.on_proximity(ev("near", HIGH, 0.0))
    now = 0.0
    waits = []
    for _ in range(len(POLICY.backoff_s) + 2):
        assert meets(p.due(now)) == [HIGH]
        p.on_outcome(HIGH, Outcome("unreachable", reason="silent"), now)
        plan = p.peers()[0]
        waits.append(plan.next_at - now)
        now = plan.next_at
    steps = [*POLICY.backoff_s, POLICY.backoff_s[-1], POLICY.backoff_s[-1]]
    for waited, step in zip(waits, steps, strict=True):
        assert step <= waited <= step * 1.25


def test_busy_waits_the_busy_time_and_refused_the_refusal_time() -> None:
    p = planner(MID)
    p.on_proximity(ev("near", HIGH, 0.0))
    p.due(0.0)
    p.on_outcome(HIGH, Outcome("busy", reason="full"), 1.0)
    assert meets(p.due(1.0 + POLICY.busy_s - 0.1)) == []
    assert meets(p.due(1.0 + POLICY.busy_s)) == [HIGH]
    p.on_outcome(HIGH, Outcome("refused", reason="4.4: not signed by its owner"), 40.0)
    assert p.peers()[0].state == "refused"
    assert meets(p.due(40.0 + POLICY.refused_s - 1)) == []
    assert meets(p.due(40.0 + POLICY.refused_s)) == [HIGH]


def test_a_refusal_is_kept_by_did_so_a_robot_met_again_is_held_back_by_it() -> None:
    p = planner(MID)
    p.on_proximity(ev("near", HIGH, 0.0))
    p.due(0.0)
    p.on_outcome(HIGH, Outcome("refused", did=DID_B, reason="refused"), 1.0)
    p.on_proximity(ev("far", HIGH, 2.0))
    assert p.peers() == []
    # the same beacon id, still unrotated, near again: its did is known, and refused
    p.on_proximity(ev("near", HIGH, 3.0))
    assert p.peers()[0].state == "refused"
    assert meets(p.due(4.0)) == []


def test_a_robot_met_that_goes_far_is_parted_from_then_not_met_again_at_once() -> None:
    p = planner(MID)
    p.on_proximity(ev("near", HIGH, 0.0))
    p.due(0.0)
    p.on_outcome(HIGH, Outcome("met", DID_B, "ble://00000001", "link"), 0.5)
    assert p.peers()[0].state == "met" and p.did_of(HIGH) == DID_B
    p.on_proximity(ev("far", HIGH, 10.0))
    actions = p.due(10.0)
    assert actions == [Part(HIGH, DID_B, "ble://00000001")]
    assert p.peers() == []
    # back within the re-meet time: held back by its did, then met again
    p.on_proximity(ev("near", HIGH, 20.0))
    assert p.peers()[0].state == "cooldown"
    assert meets(p.due(10.0 + POLICY.remeet_s - 1)) == []
    assert meets(p.due(10.0 + POLICY.remeet_s)) == [HIGH]


def test_a_robot_kept_when_far_is_not_parted_from() -> None:
    p = Planner(lambda: MID, MeetPolicy(on_far="keep"), rng=random.Random(1))
    p.on_proximity(ev("near", HIGH, 0.0))
    p.due(0.0)
    p.on_outcome(HIGH, Outcome("met", DID_B, "ble://1", "link"), 0.5)
    p.on_proximity(ev("far", HIGH, 10.0))
    assert p.due(10.0) == [] and p.peers()[0].state == "met"


def test_a_robot_that_dialled_this_one_is_waited_for_not_dialled_back() -> None:
    p = planner(MID)
    p.on_link(LOW, opened=True, inbound=True, now=0.0)
    p.on_proximity(ev("near", LOW, 0.0))
    assert meets(p.due(POLICY.grace_s + 1)) == []
    p.on_session(LOW, opened=True, route="ble://2", did=DID_A, now=POLICY.grace_s + 2)
    assert p.peers()[0].state == "met"
    # its link closed without a session: this robot is back in line, after the grace
    q = planner(MID)
    q.on_proximity(ev("near", LOW, 0.0))
    q.on_link(LOW, opened=True, inbound=True, now=1.0)
    q.on_link(LOW, opened=False, inbound=True, now=2.0)
    assert meets(q.due(POLICY.grace_s)) == [LOW]
    # and links this robot dialled itself change nothing
    q.on_link(HIGH, opened=True, inbound=False, now=3.0)
    assert {plan.beacon_id for plan in q.peers()} == {LOW}


def test_a_session_that_ends_near_waits_the_remeet_time_and_far_forgets() -> None:
    p = planner(MID)
    p.on_session(
        HIGH, opened=True, route="http://10.0.0.2:8790/robot-passport/inbox", did=DID_B, now=0.0
    )
    p.on_proximity(ev("near", HIGH, 0.5))
    p.on_session(HIGH, opened=False, route=None, did=DID_B, now=5.0)
    plan = p.peers()[0]
    assert plan.state == "cooldown" and plan.next_at == 5.0 + POLICY.remeet_s
    p.on_proximity(ev("far", HIGH, 6.0))
    assert p.peers() == []
    # an end for a session it does not know changes nothing
    p.on_session(None, opened=False, route=None, did="did:key:z6MkNobody", now=7.0)


def test_a_try_that_never_reports_back_is_given_up_after_the_attempt_time() -> None:
    p = planner(MID)
    p.on_proximity(ev("near", HIGH, 0.0))
    assert meets(p.due(0.0)) == [HIGH]
    assert meets(p.due(POLICY.attempt_s - 1)) == []
    assert meets(p.due(POLICY.attempt_s + 1)) == [HIGH]


def test_a_meeting_that_finishes_after_its_robot_went_far_is_parted_from() -> None:
    p = planner(MID)
    p.on_proximity(ev("near", HIGH, 0.0))
    p.due(0.0)
    p.on_proximity(ev("far", HIGH, 1.0))
    p.on_outcome(HIGH, Outcome("met", DID_B, "ble://3", "link"), 2.0)
    assert p.due(2.0) == [Part(HIGH, DID_B, "ble://3")]
    # and one gone far and forgotten entirely still owes its part
    q = planner(MID)
    q.on_outcome(LOW, Outcome("met", DID_A, "ble://4", "link"), 0.0)
    assert q.due(0.0) == [Part(LOW, DID_A, "ble://4")]


def test_seen_updates_where_a_robot_is_and_what_it_offers() -> None:
    p = planner(MID)
    p.on_proximity(ev("near", HIGH, 0.0, flags=FLAG_LINK))
    p.on_proximity(ev("seen", HIGH, 1.0, flags=FLAG_LAN | FLAG_LINK))
    assert p.peers()[0].flags == FLAG_LAN | FLAG_LINK
    p.on_proximity(ev("seen", LOW, 1.0))
    assert len(p.peers()) == 1


def test_the_table_and_its_memories_stay_bounded() -> None:
    small = MeetPolicy(remember=4)
    p = Planner(lambda: "0" * 16, small, rng=random.Random(1))
    for i in range(1, 20):
        p.on_proximity(ev("near", f"{i:016x}", float(i)))
    assert len(p.peers()) == small.remember
