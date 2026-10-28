"""Two robots' radios in two processes on one sim air: each hears the other, the one with the lower
beacon id dials, and a request and its answer cross the link between the processes.

This is what `quackd onboard demo` stands on, so it runs everywhere the suite does, Windows
first among them: the air is files replaced under readers, the link a loopback socket. Outcomes
only, with a generous deadline, never a timing, so a slow runner passes too."""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

from quackd.passport.beacon import new_beacon_id
from quackd.radio.sim import AirModel

HELPER = Path(__file__).resolve().parent / "sim_robot.py"
DEADLINE_S = 60.0


def test_two_processes_hear_each_other_and_talk_over_a_sim_link(tmp_path: Path) -> None:
    a, b = sorted(new_beacon_id() for _ in range(2))
    t0 = time.time()
    model = AirModel()
    places = {a: "0,0@0", b: "1,0@0"}  # one metre apart, standing still
    procs = {}
    for me, other in ((a, b), (b, a)):
        procs[me] = subprocess.Popen(
            [
                sys.executable,
                str(HELPER),
                "--air",
                str(tmp_path / "air"),
                "--beacon",
                me,
                "--expect",
                other,
                "--t0",
                repr(t0),
                "--path",
                places[me],
                "--out",
                str(tmp_path / f"{me}.json"),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    try:
        for me, proc in procs.items():
            _out, err = proc.communicate(timeout=DEADLINE_S)
            written = tmp_path / f"{me}.json"
            said = written.read_text(encoding="utf-8") if written.exists() else "(nothing)"
            assert proc.returncode == 0, f"{me}: {said} {err[-2000:]}"
    finally:
        for proc in procs.values():
            if proc.poll() is None:
                proc.kill()
                proc.communicate()
    dialler = json.loads((tmp_path / f"{a}.json").read_text(encoding="utf-8"))
    answerer = json.loads((tmp_path / f"{b}.json").read_text(encoding="utf-8"))
    assert dialler["radio"] == answerer["radio"] == "ready"
    assert dialler["dialled"] is True and answerer["dialled"] is False
    assert dialler["reply"] == [200, f"hello back from {b}"]
    assert answerer["answered"] == [f"hello from {a}"]
    # each heard the other, as loud as the model makes one metre, never louder or softer than
    # its noise allows, and neither heard itself
    low = model.clean_rssi(1.0) - model.noise_bound_db
    high = model.clean_rssi(1.0) + model.noise_bound_db
    for result, other in ((dialler, b), (answerer, a)):
        assert set(result["heard"]) == {other}, result["heard"]
        heard = result["heard"][other]
        assert heard["count"] >= 1
        assert low <= heard["min"] <= heard["max"] <= high, heard
    # the air is left as it was found: each radio took its file with it
    assert list((tmp_path / "air" / "radios").glob("*.json")) == []
