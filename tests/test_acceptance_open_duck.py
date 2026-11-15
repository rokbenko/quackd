"""Acceptance for the Open Duck Mini v2: `open-duck-scout` with the scripted pilot, seeds 0..9.

`outcome == "success"` is the pilot's claim; the sim's ground truth (the duck actually
walked up to the ball) is checked too. The verbs this body does not have must never appear
in the transcript, because they do not exist in its registry at all.
"""

from __future__ import annotations

import math
import os
import time
from pathlib import Path

from roboglot.adapters.factory import make_adapter
from roboglot.agent.loop import RunConfig, run_glot
from roboglot.agent.providers.fake import FakeProvider
from roboglot.agent.transcript import Transcript
from roboglot.glotfile.parser import load_glot
from roboglot.perception.color_blob import ColorBlobDetector
from roboglot.sim2d.recorder import FrameRecorder

SEEDS = range(10)
MIN_SUCCESSES = 10 if os.environ.get("ROBOGLOT_STRICT_SEEDS") == "1" else 8
ARRIVED_M = 0.5
NOT_THIS_DUCK = {"kick", "grab", "sit", "stand", "stand_up"}


async def test_open_duck_scout_acceptance(tmp_path: Path) -> None:
    duck = load_glot("open-duck-scout")
    successes = 0
    report = []
    for seed in SEEDS:
        adapter = make_adapter("open_duck:sim2d", seed=seed)
        recorder = FrameRecorder(adapter) if seed == 0 else None
        t0 = time.perf_counter()
        result = await run_glot(
            RunConfig(
                duck=duck,
                provider=FakeProvider.for_glot("open-duck-scout"),
                transport=adapter,
                detector=ColorBlobDetector(),
                runs_dir=tmp_path,
                on_frame=recorder.capture if recorder else None,
            )
        )
        wall = time.perf_counter() - t0
        world = adapter.world
        duck0, ball = world.ducks[0], world.ball
        distance = math.hypot(duck0.x - ball.x, duck0.y - ball.y)
        ok = result.outcome == "success" and distance < ARRIVED_M
        successes += ok
        report.append(
            f"seed {seed}: {result.outcome} distance={distance:.2f} m "
            f"steps={result.steps} {wall:.1f}s"
        )
        assert wall < 60, report[-1]
        events = Transcript.read(result.run_dir / "transcript.jsonl")
        verbs = [e["name"] for e in events if e["kind"] == "verb"]
        assert not NOT_THIS_DUCK & set(verbs), report[-1]
        assert events[0]["robot"]["model"] == "open-duck-mini-v2"
        assert events[0]["transport"] == "sim2d"
        said = [e for e in events if e["kind"] == "verb" and e["name"] == "say"]
        assert said and said[0]["ok"] and said[0]["data"]["mood"]
        if recorder is not None:
            gif = recorder.save_gif(result.run_dir / "run.gif")
            assert gif.exists() and gif.stat().st_size > 1000
    assert successes >= MIN_SUCCESSES, "\n".join(report)


async def test_open_duck_lookout_never_takes_a_step(tmp_path: Path) -> None:
    """The bring-up task: it is what you point at a real duck first, so it must not walk."""
    duck = load_glot("open-duck-lookout")
    adapter = make_adapter("open_duck:sim2d", seed=0)
    start = (adapter.world.ducks[0].x, adapter.world.ducks[0].y)  # the seed placed it, not us
    result = await run_glot(
        RunConfig(
            duck=duck,
            provider=FakeProvider.for_glot("open-duck-lookout"),
            transport=adapter,
            detector=ColorBlobDetector(),
            runs_dir=tmp_path,
        )
    )
    assert result.outcome == "success", result.reason
    events = Transcript.read(result.run_dir / "transcript.jsonl")
    verbs = {e["name"] for e in events if e["kind"] == "verb"}
    assert not verbs & ({"move", "walk", "go_to", "walk_to", "search_scan"} | NOT_THIS_DUCK)
    moved = adapter.world.ducks[0]
    assert math.hypot(moved.x - start[0], moved.y - start[1]) < 1e-6  # it never left the spot
