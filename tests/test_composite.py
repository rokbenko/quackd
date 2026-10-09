"""Composite verbs close their own loops on the camera — the steering tier, tested alone."""

from __future__ import annotations

import pytest
from PIL import Image

from quackd.duckfile.parser import parse_duck_text
from quackd.perception.base import Detection
from quackd.perception.color_blob import ColorBlobDetector
from quackd.safety import Executor
from quackd.transport.mock import MockTransport
from quackd.transport.sim2d import Sim2DTransport
from quackd.verbs.registry import VerbRegistry

DUCK = parse_duck_text(
    """---
duck: 0
name: t
description: d
verbs:
  allow: [search_scan, walk_to, kick, approach_and, grab]
success: [x]
---
# Task
x
"""
)


def _executor(registry: VerbRegistry, seed: int) -> tuple[Executor, Sim2DTransport]:
    transport = Sim2DTransport(seed=seed)
    ex = Executor(registry, transport, contract=DUCK.frontmatter, detector=ColorBlobDetector())
    return ex, transport


async def test_search_scan_finds_the_ball(registry: VerbRegistry) -> None:
    ex, transport = _executor(registry, seed=4)
    result = await ex.run_verb("search_scan", {"target": "ball"})
    assert result.ok, result.summary
    _dist, bearing = transport.world.relative(transport.world.ball.x, transport.world.ball.y)
    assert abs(bearing) < 0.9  # the ball is now in the 90° field of view


async def test_walk_to_reaches_the_ball(registry: VerbRegistry) -> None:
    ex, transport = _executor(registry, seed=2)
    assert (await ex.run_verb("search_scan", {"target": "ball"})).ok
    result = await ex.run_verb("walk_to", {"target": "ball", "stop_distance": 0.25})
    assert result.ok, result.summary
    true_dist, true_bearing = transport.world.relative(
        transport.world.ball.x, transport.world.ball.y
    )
    assert true_dist <= 0.36
    assert abs(true_bearing) < 0.6


async def test_approach_and_kick_moves_the_ball(registry: VerbRegistry) -> None:
    ex, transport = _executor(registry, seed=6)
    assert (await ex.run_verb("search_scan", {"target": "ball"})).ok
    result = await ex.run_verb(
        "approach_and", {"target": "ball", "stop_distance": 0.22, "then": "kick"}
    )
    assert result.ok, result.summary
    # the kick connected; how far the ball travels also depends on walls (seed 6 bounces)
    assert transport.world.kicks_connected == 1
    assert transport.world.last_kick_ball_moved_m is not None
    assert transport.world.last_kick_ball_moved_m > 0.1


async def test_walk_to_without_detector_explains(registry: VerbRegistry) -> None:
    transport = Sim2DTransport(seed=0)
    ex = Executor(registry, transport, contract=DUCK.frontmatter, detector=None)
    result = await ex.run_verb("walk_to")
    assert not result.ok and "detector" in result.summary


class _BallUnderfoot:
    """A detector that sees the ball dead ahead and close enough that `go_to` has arrived and
    `search_scan` has found it on the first frame either looks at."""

    name = "ball_underfoot"

    def detect(self, image: Image.Image) -> list[Detection]:
        return [
            Detection(label="ball", cx=0.5, cy=0.5, area=0.01, bearing_deg=0.0, est_distance_m=0.1)
        ]


class _Glancing(MockTransport):
    """A body whose robot turns the head on its own while it stands still, as a Microduck on
    daemon-v0.16.1 does, and says how long that takes to wear off once a look arrives. It notes
    the body's clock at every frame it hands out, and what had been sent by the first."""

    def __init__(self, head_fade_s: float) -> None:
        super().__init__()
        self.head_fade_s = head_fade_s
        self.handed_out_at: list[float] = []
        self.sent_by_the_first_frame: list[str] | None = None

    async def get_frame(self) -> Image.Image | None:
        if self.sent_by_the_first_frame is None:
            self.sent_by_the_first_frame = [intent.kind for intent in self.intents]
        self.handed_out_at.append(self.now())
        return await super().get_frame()


@pytest.mark.parametrize("verb", ["go_to", "search_scan"])
@pytest.mark.parametrize("fade_s", [1.5, 0.0], ids=["glances", "still"])
async def test_a_steering_verb_lets_the_head_settle_before_its_first_frame(
    registry: VerbRegistry, verb: str, fade_s: float
) -> None:
    """Both verbs centre the head and then act on the next frame. On a body whose head was
    glancing when the look arrived, that frame came from a head still most of a glance off
    centre. They wait `head_fade_s` first, with nothing sent that moves the body, and a body
    whose head moves only when asked waits for nothing, as before."""
    body = _Glancing(head_fade_s=fade_s)
    ex = Executor(registry, body, contract=DUCK.frontmatter, detector=_BallUnderfoot())
    result = await ex.run_verb(verb, {"target": "ball"})
    assert result.ok, result.summary
    assert body.sent_by_the_first_frame == ["look"]
    assert body.handed_out_at[0] == pytest.approx(fade_s)
