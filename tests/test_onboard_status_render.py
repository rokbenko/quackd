"""`quackd onboard status`, the radio's half: a snapshot made from the live objects, plain JSON, and
the lines a person reads from it, on a sim radio, a fake planner and a clock the test turns."""

from __future__ import annotations

import json
import random

from quackd.passport.beacon import FLAG_LINK, new_beacon_id
from quackd.radio import RadioStatus, Sighting
from quackd.radio.meet import MeetPolicy, Outcome, Planner
from quackd.radio.proximity import ProximityPolicy, Tracker
from quackd.radio.rotation import BeaconRotor
from quackd.radio.statusview import (
    PEERS_SHOWN,
    Rate,
    duration,
    radio_snapshot,
    render_radio,
    render_status,
)

READY = RadioStatus(
    "ready",
    "sim",
    adapter="sim",
    address="sim:robot-a",
    address_type="random",
    advertising=True,
    scanning=True,
    accepting=True,
    links=1,
    detail="the sim air at /tmp/air, on network none",
)


def scene() -> tuple[Tracker, Planner, BeaconRotor, str, str]:
    """A robot that met one robot over a link and hears another, far off."""
    me = "0" * 16
    met, far = "1" * 16, "f" * 16
    tracker = Tracker(ProximityPolicy(), me=me)
    planner = Planner(lambda: me, MeetPolicy(), rng=random.Random(0))
    for step in range(10):
        at = step * 0.2
        for event in tracker.observe(Sighting(met, FLAG_LINK, -60.0, "sim:b", at, true_m=1.0)):
            planner.on_proximity(event)
        tracker.observe(Sighting(far, FLAG_LINK, -84.0, "sim:c", at, true_m=14.0))
    planner.due(2.0)
    planner.on_outcome(met, Outcome("met", "did:key:z6MkMet", "ble://00000001", "link"), 2.0)
    rotor = BeaconRotor(busy=lambda: False, on_rotate=lambda b: None, clock=lambda: 0.0, current=me)
    return tracker, planner, rotor, met, far


def test_a_snapshot_is_plain_json_with_the_radio_the_beacon_the_thresholds_and_the_robots() -> None:
    tracker, planner, rotor, met, far = scene()
    snap = radio_snapshot(
        status=READY, now=2.0, rotor=rotor, tracker=tracker, planner=planner, heard_per_s=4.94
    )
    assert json.loads(json.dumps(snap)) == snap
    assert snap["state"] == "ready" and snap["links"] == 1 and snap["heard_per_s"] == 4.9
    assert snap["beacon"] == {
        "id": rotor.current,
        "rotates_in_s": rotor.period_s - 2.0,
        "rotations": 0,
        "deferred": 0,
    }
    policy = tracker.policy
    assert snap["near"]["near_dbm"] == round(policy.near_dbm, 1)
    assert snap["near"]["far_dbm"] == round(policy.far_dbm, 1)
    assert "privacy" not in snap  # a sim radio has no address to keep private
    peers = {p["beacon_id"]: p for p in snap["peers"]}
    assert (
        peers[met]["near"] is True and peers[met]["plan"] == "met" and peers[met]["via"] == "link"
    )
    assert peers[met]["did"] == "did:key:z6MkMet" and peers[met]["true_m"] == 1.0
    assert peers[far]["near"] is False and peers[far]["plan"] is None
    assert [p["beacon_id"] for p in snap["peers"]] == [met, far]  # loudest first


def test_the_lines_say_what_the_radio_does_and_what_each_robot_is_to_this_one() -> None:
    tracker, planner, rotor, met, far = scene()
    lines = render_radio(
        radio_snapshot(
            status=READY, now=2.0, rotor=rotor, tracker=tracker, planner=planner, heard_per_s=5.0
        )
    )
    text = "\n".join(lines)
    assert lines[0] == f"radio     sim sim, ready: {READY.detail}"
    assert "advertising, scanning (5 passport adverts a second), taking links; 1 link open" in text
    due = duration(rotor.period_s - 2.0)
    assert f"beacon    {rotor.current} (next rotation in {due}; rotated 0 times)" in text
    assert "dBm or louder is near" in text and "uncalibrated" in text
    met_line = next(line for line in lines if met in line)
    assert "near" in met_line and "met over a link" in met_line and "did:key:z6MkMet" in met_line
    assert "(the sim puts it at 1 m)" in met_line and "~" in met_line
    far_line = next(line for line in lines if far in line)
    assert "far" in far_line and "heard" in far_line


def test_a_radio_that_is_not_ready_shows_why_and_what_to_do() -> None:
    down = RadioStatus(
        "off", "bluez", adapter="hci0", detail="hci0 is powered off", hint="bluetoothctl power on"
    )
    lines = render_radio(radio_snapshot(status=down, now=0.0, main_conf="[General]\n"))
    assert lines[0] == "radio     bluez hci0, off: hci0 is powered off"
    assert lines[1] == "          -> bluetoothctl power on"
    assert "nothing; 0 links open" in lines[2]
    privacy = next(line for line in lines if line.startswith("privacy"))
    assert "does not stop tracking" in privacy
    assert lines[-1] == "robots    none heard"


def test_robots_past_the_shown_few_are_counted_not_listed() -> None:
    tracker = Tracker(ProximityPolicy())
    for i in range(PEERS_SHOWN + 3):
        tracker.observe(Sighting(new_beacon_id(), FLAG_LINK, -80.0 - i * 0.1, f"sim:{i}", 0.0))
    snap = radio_snapshot(status=READY, now=0.1, tracker=tracker)
    assert len(snap["peers"]) == PEERS_SHOWN and snap["peers_more"] == 3
    assert render_radio(snap)[-1] == "          and 3 more"


def test_the_advert_rate_is_smoothed_and_durations_read_as_people_say_them() -> None:
    rate = Rate(smoothing_s=10.0)
    assert rate.update(0, 0.0) is None
    assert rate.update(10, 2.0) == 5.0
    smoothed = rate.update(110, 4.0)
    assert smoothed is not None and 5.0 < smoothed < 50.0
    assert [duration(s) for s in (None, 45.2, 512, 7500)] == [
        "unknown",
        "45 s",
        "8 min 32 s",
        "2 h 5 min",
    ]


def test_a_whole_status_file_is_rendered_section_by_section() -> None:
    status = {
        "service": {"state": "running", "pid": 4242, "version": "0.29.0", "note": None},
        "radio": radio_snapshot(status=READY, now=0.0),
        "meetings": ["courier, stranger, over a link", "household robot, known, over the LAN"],
        "unsigned": "",
    }
    lines = render_status(status)
    assert lines[0] == "service   state running, pid 4242, version 0.29.0"
    assert any(line.startswith("radio     sim") for line in lines)
    assert "meetings  2" in lines and lines[-1].strip() == "household robot, known, over the LAN"
    assert not any(line.startswith("unsigned") for line in lines)
