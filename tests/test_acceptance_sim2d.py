"""M2 acceptance: with the scripted pilot, find-and-kick succeeds on >= 8 of seeds 0..9,
headless, in under 60 s each, and a GIF lands in the run directory.

`outcome == "success"` is the LLM's *claim*; the sim's ground truth
(`ball_displacement_m`) is checked too, so a lying pilot cannot pass this test.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

from roboglot.agent.loop import RunConfig, run_glot
from roboglot.agent.providers.fake import FakeProvider
from roboglot.glotfile.schema import GlotFile
from roboglot.perception.color_blob import ColorBlobDetector
from roboglot.sim2d.recorder import FrameRecorder
from roboglot.transport.sim2d import Sim2DTransport

SEEDS = range(10)
# CI sets ROBOGLOT_STRICT_SEEDS=1: the shipped claim is 10 of 10, and a refactor must not
# quietly spend the two seeds of slack the local default allows.
MIN_SUCCESSES = 10 if os.environ.get("ROBOGLOT_STRICT_SEEDS") == "1" else 8


async def test_find_and_kick_acceptance(kick_duck: GlotFile, tmp_path: Path) -> None:
    successes = 0
    report = []
    for seed in SEEDS:
        transport = Sim2DTransport(seed=seed)
        recorder = FrameRecorder(transport) if seed == 0 else None
        t0 = time.perf_counter()
        result = await run_glot(
            RunConfig(
                duck=kick_duck,
                provider=FakeProvider.for_glot("find-and-kick"),
                transport=transport,
                detector=ColorBlobDetector(),
                runs_dir=tmp_path,
                on_frame=recorder.capture if recorder else None,
            )
        )
        wall = time.perf_counter() - t0
        truth = transport.world.ball_displacement_m
        ok = result.outcome == "success" and truth >= 0.3
        successes += ok
        report.append(
            f"seed {seed}: {result.outcome} truth={truth:.2f} m steps={result.steps} {wall:.1f}s"
        )
        assert wall < 60, report[-1]
        assert (result.run_dir / "transcript.jsonl").exists()
        if recorder is not None:
            gif = recorder.save_gif(result.run_dir / "run.gif")
            assert gif.exists() and gif.stat().st_size > 1000
            assert len(recorder.frames) > 5
    assert successes >= MIN_SUCCESSES, "\n".join(report)
