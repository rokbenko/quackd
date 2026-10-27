"""Acceptance for exploring: with the scripted pilot, `find-the-ball` and `favourite-spot` succeed
on seeds 0..9 in two-rooms, headless, judged by the simulator's own truth as well as the pilot's
word, so a pilot that says it succeeded and did not cannot pass.

The ball hides in the room the duck does not start in (`two-rooms`), so finding it means
exploring through the doorway; the duck must then have stood within a body's width of the stop
distance it asked for, and be back where it began.
"""

from __future__ import annotations

import json
import math
import os
import time
from pathlib import Path
from typing import Any

from quackd.adapters.factory import make_adapter
from quackd.agent.loop import RunConfig, RunResult, run_duck
from quackd.agent.providers.fake import BALL_STOP_M, FakeProvider
from quackd.duckfile.parser import load_duck
from quackd.perception.color_blob import ColorBlobDetector
from quackd.safety import allow_all
from quackd.sim2d.world import DUCK_R
from quackd.space.room import load_room

ROOM = "two-rooms"
SEEDS = range(10)
# CI sets QUACKD_STRICT_SEEDS=1: the shipped claim is 10 of 10, and a refactor must not quietly
# spend the two seeds of slack the local default allows.
MIN_SUCCESSES = 10 if os.environ.get("QUACKD_STRICT_SEEDS") == "1" else 8
BODY_WIDTH_M = 2 * DUCK_R


async def _run(name: str, seed: int, runs_dir: Path) -> tuple[RunResult, dict[str, Any]]:
    """`name` run by the scripted pilot in the room on `seed`, and what the simulator saw of it:
    how near the duck came to the ball, how far into the room past the doorway's wall it went,
    and where it ended."""
    adapter = make_adapter("microduck:sim2d", seed=seed, room=ROOM)
    world = adapter.transport.world
    truth: dict[str, Any] = {"closest_m": math.inf, "furthest_x": -math.inf}

    def watch(_world: Any) -> None:
        duck = world.ducks[0]
        truth["closest_m"] = min(
            truth["closest_m"], math.dist((duck.x, duck.y), (world.ball.x, world.ball.y))
        )
        truth["furthest_x"] = max(truth["furthest_x"], duck.x)

    adapter.transport.add_tick_hook(watch)
    try:
        result = await run_duck(
            RunConfig(
                duck=load_duck(name),
                provider=FakeProvider.for_duck(name),
                transport=adapter,
                detector=ColorBlobDetector(),
                runs_dir=runs_dir,
                confirm=allow_all,
            )
        )
        duck = world.ducks[0]
        truth["end"] = (duck.x, duck.y)
        truth["ball"] = (world.ball.x, world.ball.y)
    finally:
        await adapter.close()
    return result, truth


def _verbs(result: RunResult) -> list[tuple[str, bool]]:
    """The verbs the pilot called, in order, and whether each worked."""
    lines = (result.run_dir / "transcript.jsonl").read_text(encoding="utf-8").splitlines()
    events = [json.loads(line) for line in lines if line.strip()]
    return [
        (event["name"], event["ok"])
        for event in events
        if event.get("kind") == "verb_end" and not event.get("nested")
    ]


def _start() -> tuple[float, float]:
    start = load_room(ROOM).start
    assert start is not None
    return start[0], start[1]


async def test_find_the_ball_acceptance(tmp_path: Path) -> None:
    successes = 0
    report = []
    for seed in SEEDS:
        t0 = time.perf_counter()
        result, truth = await _run("find-the-ball", seed, tmp_path)
        wall = time.perf_counter() - t0
        reached = truth["closest_m"] <= BALL_STOP_M + BODY_WIDTH_M
        back = math.dist(truth["end"], _start()) <= BODY_WIDTH_M
        ok = result.outcome == "success" and reached and back
        successes += ok
        report.append(
            f"seed {seed}: {result.outcome} closest={truth['closest_m']:.2f} m "
            f"back={math.dist(truth['end'], _start()):.2f} m {wall:.1f}s {_verbs(result)}"
        )
        assert wall < 60, report[-1]
    assert successes >= MIN_SUCCESSES, "\n".join(report)


async def test_favourite_spot_acceptance(tmp_path: Path) -> None:
    doorway = load_room(ROOM).walls[0].x0  # the wall the doorway is in, between the two rooms
    successes = 0
    report = []
    for seed in SEEDS:
        t0 = time.perf_counter()
        result, truth = await _run("favourite-spot", seed, tmp_path)
        wall = time.perf_counter() - t0
        explored = truth["furthest_x"] > doorway  # through the doorway into the other room
        back = math.dist(truth["end"], _start()) <= BODY_WIDTH_M
        ok = result.outcome == "success" and explored and back
        successes += ok
        report.append(
            f"seed {seed}: {result.outcome} furthest x={truth['furthest_x']:.2f} "
            f"back={math.dist(truth['end'], _start()):.2f} m {wall:.1f}s {_verbs(result)}"
        )
        assert wall < 60, report[-1]
    assert successes >= MIN_SUCCESSES, "\n".join(report)
