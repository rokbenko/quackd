"""The sim2d acceptance sweep, on the physics simulator: with the scripted pilot,
`find-and-kick` succeeds on seeds 0..9, and the world's own ball telemetry agrees.

Twice: once on the kinematic stand-in, which is what a runner with the physics extra can
always do, and once on upstream's own trained gait, which needs the model in the cache and so
is marked `real_duck`. The second is the one the README's claim rests on, and it exists
because that claim had no test behind it at all.

Skipped without `quackd[mujoco]` or without an OpenGL context, because the composite verbs
steer on rendered frames and there is nothing to steer on without one.
"""

from __future__ import annotations

import math
import os
import time
from pathlib import Path

import pytest

pytest.importorskip("mujoco")

from quackd.agent.loop import RunConfig, run_duck
from quackd.agent.providers.fake import FakeProvider
from quackd.duckfile.parser import load_duck
from quackd.perception.color_blob import ColorBlobDetector
from quackd.sim2d.recorder import FrameRecorder
from quackd_microduck import MicroduckAdapter
from quackd_microduck.sim3d.world import MujocoWorld
from quackd_microduck.transports.mujoco import MujocoTransport
from tests.gl import require_render

SEEDS = range(10)
MIN_SUCCESSES = 10 if os.environ.get("QUACKD_STRICT_SEEDS") == "1" else 8
#: What the trained gait manages on the same ten seeds. Measured on one machine, because CI
#: fetches no model; the number in the docs is this number and moves when it does.
REAL_MIN_SUCCESSES = 10 if os.environ.get("QUACKD_STRICT_SEEDS") == "1" else 8
#: A ten-seed sweep is one test, and `faulthandler_timeout` is per test. This is a tripwire
#: for a ten-fold regression, not a benchmark: per-seed times stay in the report either way.
MAX_SWEEP_S = 600.0


async def test_find_and_kick_acceptance_in_mujoco(tmp_path: Path) -> None:
    _opengl_or_skip()
    duck = load_duck("find-and-kick")
    successes = 0
    report = []
    walls: list[float] = []
    for seed in SEEDS:
        transport = MujocoTransport(seed=seed, body="puppet")
        adapter = MicroduckAdapter(transport)
        recorder = FrameRecorder(adapter, size=96) if seed == 0 else None
        t0 = time.perf_counter()
        result = await run_duck(
            RunConfig(
                duck=duck,
                provider=FakeProvider.for_duck("find-and-kick"),
                transport=adapter,
                detector=ColorBlobDetector(),
                runs_dir=tmp_path,
                on_frame=recorder.capture if recorder else None,
            )
        )
        wall = time.perf_counter() - t0
        walls.append(wall)
        truth = transport.world.ball_displacement_m
        ok = result.outcome == "success" and truth >= 0.3
        successes += ok
        report.append(
            f"seed {seed}: {result.outcome} truth={truth:.2f} m steps={result.steps} {wall:.1f}s"
        )
        assert (result.run_dir / "transcript.jsonl").exists()
        if recorder is not None:
            gif = recorder.save_gif(result.run_dir / "run.gif")
            assert gif.exists() and gif.stat().st_size > 1000
            assert len(recorder.frames) > 5
    assert sum(walls) < MAX_SWEEP_S, "\n".join(report)
    assert successes >= MIN_SUCCESSES, "\n".join(report)


@pytest.mark.real_duck
async def test_find_and_kick_on_the_real_duck(tmp_path: Path) -> None:
    """The same sweep with upstream's trained gait under it, which is what the README claims.

    Skipped where the model is not already cached, so it runs on a developer's machine and
    nowhere else: CI fetches nothing. No recorder, because the GIF roughly doubles the cost
    and the sweep above already proves the recorder.
    """
    _opengl_or_skip()
    _cached_microduck()
    duck = load_duck("find-and-kick")
    successes = 0
    report = []
    for seed in SEEDS:
        transport = MujocoTransport(seed=seed, body="microduck")
        adapter = MicroduckAdapter(transport)
        t0 = time.perf_counter()
        result = await run_duck(
            RunConfig(
                duck=duck,
                provider=FakeProvider.for_duck("find-and-kick"),
                transport=adapter,
                detector=ColorBlobDetector(),
                runs_dir=tmp_path,
            )
        )
        wall = time.perf_counter() - t0
        snap = transport.world.snapshot()
        # A silent fall back to the puppet passing this test is the whole reason it exists.
        assert snap["physics"] == "microduck", f"seed {seed} did not run the trained gait"
        truth = transport.world.ball_displacement_m
        ok = result.outcome == "success" and truth >= 0.3
        successes += ok
        report.append(
            f"seed {seed}: {result.outcome} truth={truth:.2f} m steps={result.steps} "
            f"posture={transport.world.posture} {wall:.1f}s"
        )
    assert successes >= REAL_MIN_SUCCESSES, "\n".join(report)


def _opengl_or_skip() -> None:
    probe = MujocoWorld(seed=0)
    try:
        require_render(probe)
    finally:
        probe.close()


def _cached_microduck() -> object:
    from quackd_microduck.sim3d.assets import AssetError, ensure_microduck

    try:
        return ensure_microduck(offline=True)
    except AssetError as e:
        pytest.skip(f"upstream's Microduck model is not cached: {e}")


class _BallPlaced(MujocoTransport):
    """The physics arena with its ball put `bearing_deg` to the left of where the head points and
    `distance_m` ahead of the camera, on the floor, so below the head as well."""

    def __init__(self, *, bearing_deg: float, distance_m: float) -> None:
        super().__init__(seed=0, body="puppet")
        self.bearing_deg = bearing_deg
        self.distance_m = distance_m

    async def connect(self) -> None:
        import mujoco

        from quackd_microduck.sim3d.scene import BALL_R

        await super().connect()
        world = self.world
        x, y, _z, yaw, _pitch = world.head_pose()
        heading = yaw + math.radians(self.bearing_deg)
        qpos = world._ball_qpos  # the one free joint the arena's ball rides on
        world.data.qpos[qpos : qpos + 3] = (
            x + self.distance_m * math.cos(heading),
            y + self.distance_m * math.sin(heading),
            BALL_R,
        )
        world.data.qvel[world._ball_dof : world._ball_dof + 6] = 0.0
        mujoco.mj_forward(world.model, world.data)


async def test_watch_turns_and_tilts_the_head_onto_the_ball_in_mujoco(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`watch` closed through quackd's own client for OpenAI's Decisions API, on the fake SDK,
    with `BlobEyes` answering where the ball is in each JPEG it is handed. The ball is left of the
    head and below it, on the floor, so both the yaw and the pitch have to close, and both stay
    inside the Microduck's envelope (`MICRODUCK_HEAD`) while they do."""
    from quackd.agent.decision.catalogue import PRESETS
    from quackd.agent.decision.factory import make_decision_llm, resolve_decision_price
    from quackd.agent.providers.base import ToolCall
    from quackd.duckfile.parser import parse_duck_text
    from quackd_microduck.verbs import MICRODUCK_HEAD
    from tests import fake_decisions_api as fake_api
    from tests.fake_eyes import BlobEyes

    _opengl_or_skip()
    fake = fake_api.install(monkeypatch, BlobEyes(noise=0.02, seed=5))
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-a-real-key")
    for name in ("OPENAI_BASE_URL", "OPENAI_CUSTOM_HEADERS", "OPENAI_ORG_ID", "OPENAI_PROJECT_ID"):
        monkeypatch.setenv(name, "")
    openai = PRESETS["openai"]
    transport = _BallPlaced(bearing_deg=25.0, distance_m=0.6)
    duck = parse_duck_text(
        "---\nduck: 1\nname: watch-mujoco\ndescription: d\nverbs:\n  allow: [watch]\n"
        "success: [x]\n---\n# Task\nWatch the ball.\n"
    )
    result = await run_duck(
        RunConfig(
            duck=duck,
            provider=FakeProvider(
                script=[
                    ToolCall(
                        name="watch", arguments={"target": "the orange ball", "duration_s": 8}
                    ),
                    ToolCall(name="declare_success", arguments={"reason": "watched"}),
                ]
            ),
            transport=MicroduckAdapter(transport),
            detector=ColorBlobDetector(),
            runs_dir=tmp_path,
            decision_llm=make_decision_llm(openai, frames=True),
            decision_price=resolve_decision_price(openai),
            decision_frames=True,
        )
    )
    assert result.outcome == "success", result.reason
    seen = [at for at in fake.truth if at is not None]
    assert len(seen) == len(fake.truth) > 6, "the ball went out of view"
    first, last = seen[0], seen[-1]
    assert first[0] < -0.15 and first[1] > 0.08, "the ball starts left of the head and below it"
    assert abs(last[0]) <= 0.1 and abs(last[1]) <= 0.12, seen
    asked = {q["name"] for post in fake.posts for q in post["body"]["questions"]}
    assert asked == {"where", "height"}, "the physics head tilts, so a height is asked"
    _x, _y, _z, yaw, pitch = transport.world.head_pose()
    yaw_deg = math.degrees(yaw - transport.world.theta)
    pitch_deg = math.degrees(pitch)
    assert 10.0 < yaw_deg <= MICRODUCK_HEAD.yaw_deg
    assert -MICRODUCK_HEAD.pitch_down_deg - 1e-6 <= pitch_deg < -4.0
