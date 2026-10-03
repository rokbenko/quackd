"""Acceptance for the ToddlerBot simulator: `toddlerbot-lookout`, seeds 0..9.

This is what earns `toddlerbot:sim2d` its row in `docs/adapters/status.md`.

The ground truth here is stricter than any other body's, because this one cannot get up. The
task must succeed, the robot must not have taken a step, and nothing that moves a leg, an arm
or the waist may appear in the transcript. The head is the only thing allowed to move, and
`stand` is not in the allowlist either: on a real robot this task runs on a safety stand.
"""

from __future__ import annotations

import math
import os
import time
from pathlib import Path

import pytest

from quackd.adapters.factory import make_adapter
from quackd.agent.loop import RunConfig, run_duck
from quackd.agent.providers.fake import FakeProvider
from quackd.agent.transcript import Transcript
from quackd.duckfile.parser import load_duck
from quackd.perception.color_blob import ColorBlobDetector
from quackd.transport.base import Intent

SEEDS = range(10)
MIN_SUCCESSES = 10 if os.environ.get("QUACKD_STRICT_SEEDS") == "1" else 8

#: Anything that would move this body rather than only its head.
NOT_ON_A_SAFETY_STAND = {
    "move",
    "go_to",
    "approach_and",
    "search_scan",
    "stand",
    "perform",
    "say",
    "grip",
}


async def test_toddlerbot_lookout_acceptance(tmp_path: Path) -> None:
    duck = load_duck("toddlerbot-lookout")
    successes = 0
    report = []
    for seed in SEEDS:
        adapter = make_adapter("toddlerbot:sim2d", seed=seed)
        body = adapter.world.ducks[adapter.duck_index]
        start = (body.x, body.y, body.theta)
        t0 = time.perf_counter()
        result = await run_duck(
            RunConfig(
                duck=duck,
                provider=FakeProvider.for_duck("toddlerbot-lookout"),
                transport=adapter,
                detector=ColorBlobDetector(),
                runs_dir=tmp_path,
            )
        )
        wall = time.perf_counter() - t0
        successes += result.outcome == "success"
        report.append(f"seed {seed}: {result.outcome} steps={result.steps} {wall:.1f}s")
        assert wall < 60, report[-1]

        events = Transcript.read(result.run_dir / "transcript.jsonl")
        verbs = {e["name"] for e in events if e["kind"] == "verb"}
        moved_it = verbs & NOT_ON_A_SAFETY_STAND
        assert not moved_it, f"{report[-1]}: it moved the body: {moved_it}"
        assert events[0]["robot"]["model"] == "toddlerbot_2xc"
        assert events[0]["transport"] == "sim2d"

        end = adapter.world.ducks[adapter.duck_index]
        assert math.hypot(end.x - start[0], end.y - start[1]) < 1e-6, report[-1]
        assert abs(end.theta - start[2]) < 1e-6, f"{report[-1]}: it turned the whole body"
    assert successes >= MIN_SUCCESSES, "\n".join(report)


async def test_the_lookout_reports_without_a_voice_and_without_a_battery(tmp_path: Path) -> None:
    """Two absences this body has and the ducks do not: nothing synthesises speech at the
    pin, and nothing reports a battery to Python, so a battery abort can never fire here."""
    adapter = make_adapter("toddlerbot:sim2d", seed=0)
    manifest = await adapter.connect()
    assert not manifest.provides("say")
    assert "battery" not in manifest.sensors
    assert (await adapter.get_state()).battery_percent is None

    result = await run_duck(
        RunConfig(
            duck=load_duck("toddlerbot-lookout"),
            provider=FakeProvider.for_duck("toddlerbot-lookout"),
            transport=adapter,
            detector=ColorBlobDetector(),
            runs_dir=tmp_path,
        )
    )
    assert result.outcome == "success", result.reason
    events = Transcript.read(result.run_dir / "transcript.jsonl")
    assert "say" not in {e["name"] for e in events if e["kind"] == "verb"}


async def test_the_simulated_head_actually_turns(tmp_path: Path) -> None:
    """The sweep above would pass without this.

    `ToddlerBotSim2D.send_intent` used to store the neck angle and return, never touching the
    cartoon head, so `observe` after a `look` saw exactly what it saw before and a ten seed
    acceptance run proved only that nothing crashed.
    """
    adapter = make_adapter("toddlerbot:sim2d", seed=0)
    await adapter.connect()
    duck = adapter.world.ducks[adapter.duck_index]
    before = duck.head_yaw

    ack = await adapter.send_intent(Intent.look(x=0.0, y=1.0, z=0.0))  # ninety degrees left
    assert ack.accepted
    after = adapter.world.ducks[adapter.duck_index].head_yaw
    assert after != pytest.approx(before), "the cartoon head never moved"
    assert after > before, "and it turned the way it was asked to"

    state = await adapter.get_state()
    assert state.extras["neck"]["yaw_deg"] == pytest.approx(90.0, abs=1.0)
    assert state.extras["head_yaw_deg"] == pytest.approx(90.0, abs=1.0)
