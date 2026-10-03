"""Acceptance for the AlohaMini simulator: `alohamini-lookout`, seeds 0..9.

This is what earns `alohamini:sim2d` its row in `docs/adapters/status.md`. The row above it
says what a simulator has to do to claim one: run the shipped task on ten seeds, not merely
import cleanly.

The task is the bring-up task, so the ground truth is the opposite of the duck's. It must
succeed **and** the robot must not have moved: no wheel, no arm, no lift. A lookout that
drives is a lookout that would have driven a real 12 kg base across a real room.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

from quackd.adapters.factory import make_adapter
from quackd.agent.loop import RunConfig, run_duck
from quackd.agent.providers.fake import FakeProvider
from quackd.agent.transcript import Transcript
from quackd.duckfile.parser import load_duck
from quackd.perception.color_blob import ColorBlobDetector

SEEDS = range(10)
MIN_SUCCESSES = 10 if os.environ.get("QUACKD_STRICT_SEEDS") == "1" else 8

#: Everything in this body's registry that would move it, plus the verbs it does not have.
NOT_IN_THIS_TASK = {
    "move",
    "go_to",
    "approach_and",
    "search_scan",
    "lift",
    "move_joints",
    "gripper",
    "home_arms",
    "say",
    "look",
}


async def test_alohamini_lookout_acceptance(tmp_path: Path) -> None:
    duck = load_duck("alohamini-lookout")
    successes = 0
    report = []
    for seed in SEEDS:
        adapter = make_adapter("alohamini:sim2d", seed=seed)
        start = (
            adapter.world.ducks[adapter.duck_index].x,
            adapter.world.ducks[adapter.duck_index].y,
        )
        t0 = time.perf_counter()
        result = await run_duck(
            RunConfig(
                duck=duck,
                provider=FakeProvider.for_duck("alohamini-lookout"),
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
        assert not verbs & NOT_IN_THIS_TASK, f"{report[-1]}: it moved: {verbs & NOT_IN_THIS_TASK}"
        assert events[0]["robot"]["model"] == "alohamini2"
        assert events[0]["transport"] == "sim2d"

        moved = adapter.world.ducks[adapter.duck_index]
        assert abs(moved.x - start[0]) < 1e-6 and abs(moved.y - start[1]) < 1e-6, report[-1]
    assert successes >= MIN_SUCCESSES, "\n".join(report)


async def test_the_lookout_never_asks_for_a_voice_this_robot_has_not_got(tmp_path: Path) -> None:
    """There is no speaker in the bill of materials, so `say` is absent from the registry
    rather than gated. A task that reached for it would fail, not be refused."""
    adapter = make_adapter("alohamini:sim2d", seed=0)
    manifest = await adapter.connect()
    assert not manifest.provides("say")
    result = await run_duck(
        RunConfig(
            duck=load_duck("alohamini-lookout"),
            provider=FakeProvider.for_duck("alohamini-lookout"),
            transport=adapter,
            detector=ColorBlobDetector(),
            runs_dir=tmp_path,
        )
    )
    assert result.outcome == "success", result.reason
    events = Transcript.read(result.run_dir / "transcript.jsonl")
    assert "say" not in {e["name"] for e in events if e["kind"] == "verb"}
