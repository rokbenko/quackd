"""`quackd onboard demo --offline --fast`: two robots' services as two processes on the sim air,
through every act, as the README tells a reader to run it.

The robots fly the scripted pilot, so nothing here needs a key or a network, and each act's
outcome is the demo's own: what it expected beside what it observed. Outcomes only, with the
demo's own deadlines, never a timing, so a slow runner passes too.
"""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from quackd.cli import app
from quackd.onboard import demo

runner = CliRunner()


def test_every_act_of_the_offline_demo_happens_as_it_says(tmp_path: Path) -> None:
    kept = tmp_path / "demo"
    ran = runner.invoke(
        app, ["onboard", "demo", "--offline", "--fast", "--json", "--keep", str(kept)]
    )
    assert ran.exit_code == 0, ran.output
    found = json.loads(ran.output.strip().splitlines()[-1])
    assert found["ok"] is True
    assert [act["act"] for act in found["acts"]] == list(demo.ACTS)
    assert all(act["ok"] for act in found["acts"]), found
    by_act = {act["act"]: act["observed"] for act in found["acts"]}
    assert "over the link" in by_act["strangers"] and "(stranger)" in by_act["strangers"]
    assert "(known)" in by_act["appointment"]
    assert "over the LAN" in by_act["office"]
    assert "4.4" in by_act["tampered"]
    assert "the brain was told only that a stranger spoke" in by_act["injection"]
    # every robot's records stay where --keep said, a meeting's run among them
    for name in (demo.COURIER, demo.HOUSEHOLD):
        assert list((kept / name / "runs").glob("*/summary.json"))
    # and the household's met.jsonl holds every act's meeting and the refusal, in order
    rows = demo.met_rows(demo.Robot(demo.HOUSEHOLD, kept / demo.HOUSEHOLD))
    assert [r["tier"] for r in rows if "id" in r] == ["stranger", "known", "stranger", "stranger"]
    assert sum("refused" in r for r in rows) == 1
