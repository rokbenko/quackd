"""The cartoon world: deterministic, honest about kicks, and legible to the detector."""

from __future__ import annotations

import math
from pathlib import Path

import pytest

from roboglot.perception.color_blob import ColorBlobDetector
from roboglot.sim2d.recorder import FrameRecorder
from roboglot.sim2d.render import render_duckcam, render_topdown
from roboglot.sim2d.world import DEADMAN_S, DT, World


def _run(world: World, seconds: float) -> None:
    for _ in range(round(seconds / DT)):
        world.step()


def test_determinism_under_seed() -> None:
    a, b = World(seed=7), World(seed=7)
    for w in (a, b):
        w.set_velocity(0.2, 0.0, 0.5)
        _run(w, 2.0)
        w.kick()
        _run(w, 1.0)
    assert (a.duck.x, a.duck.y, a.duck.theta) == (b.duck.x, b.duck.y, b.duck.theta)
    assert (a.ball.x, a.ball.y) == (b.ball.x, b.ball.y)
    assert World(seed=1).ball.x != World(seed=2).ball.x


def test_ball_starts_away_from_duck() -> None:
    for seed in range(20):
        w = World(seed=seed)
        dist, _ = w.relative(w.ball.x, w.ball.y)
        assert dist >= 0.5


def test_deadman_zeroes_velocity() -> None:
    w = World(seed=0)
    w.set_velocity(0.2, 0.0, 0.0)
    _run(w, DEADMAN_S / 2)
    assert w.moving
    _run(w, DEADMAN_S)
    assert not w.moving


def _place_ball(w: World, dist: float, bearing_deg: float) -> None:
    ang = w.duck.theta + math.radians(bearing_deg)
    w.ball.x = w.duck.x + dist * math.cos(ang)
    w.ball.y = w.duck.y + dist * math.sin(ang)
    w.ball.start_x, w.ball.start_y = w.ball.x, w.ball.y


def test_kick_connects_only_when_close_and_ahead() -> None:
    w = World(seed=3)
    w.duck.x = w.duck.y = 0.0
    _place_ball(w, 0.6, 0.0)
    assert not w.kick()
    _place_ball(w, 0.2, 60.0)
    assert not w.kick()
    _place_ball(w, 0.2, 5.0)
    assert w.kick()
    _run(w, 1.5)
    assert w.last_kick_ball_moved_m is not None and w.last_kick_ball_moved_m >= 0.3


def test_contact_pushes_the_ball() -> None:
    w = World(seed=0)
    w.duck.x = w.duck.y = w.duck.theta = 0.0
    _place_ball(w, 0.2, 0.0)
    w.set_velocity(0.3, 0.0, 0.0)
    for _ in range(20):
        w.step()
        w.set_velocity(0.3, 0.0, 0.0)
    dist, _ = w.relative(w.ball.x, w.ball.y)
    assert dist >= w.duck.r + w.ball.r - 1e-6
    assert w.ball_displacement_m > 0


def test_renders_have_the_right_shape() -> None:
    w = World(seed=0)
    assert render_topdown(w).size == (256, 256)
    assert render_duckcam(w, 128).size == (128, 128)


@pytest.mark.parametrize("bearing", [-30.0, 0.0, 25.0])
@pytest.mark.parametrize("dist", [0.3, 0.6, 1.2])
def test_detector_reads_bearing_and_distance_from_duckcam(dist: float, bearing: float) -> None:
    w = World(seed=0, person=False)
    w.duck.x = w.duck.y = 0.0
    w.duck.theta = 0.4
    _place_ball(w, dist, bearing)
    dets = ColorBlobDetector().detect(render_duckcam(w))
    balls = [d for d in dets if d.label == "ball"]
    assert len(balls) == 1
    b = balls[0]
    assert b.bearing_deg is not None and abs(b.bearing_deg - bearing) < 4.0
    assert b.est_distance_m is not None and abs(b.est_distance_m - dist) / dist < 0.25


def test_detector_sees_person_and_ignores_floor() -> None:
    w = World(seed=5)
    w.duck.x = w.duck.y = w.duck.theta = 0.0
    w.people[0].x, w.people[0].y = 0.8, 0.1
    w.ball.present = False
    dets = ColorBlobDetector().detect(render_duckcam(w))
    assert [d.label for d in dets] == ["person"]
    assert dets[0].est_distance_m is not None and 0.5 < dets[0].est_distance_m < 1.2


def test_ball_behind_duck_is_not_seen() -> None:
    w = World(seed=0, person=False)
    w.duck.x = w.duck.y = w.duck.theta = 0.0
    _place_ball(w, 0.5, 180.0)
    assert ColorBlobDetector().detect(render_duckcam(w)) == []


def test_a_recorder_with_nothing_drawn_writes_no_gif(tmp_path: Path) -> None:
    """A GIF of no frames cannot be encoded, and a recorder whose world was never built has
    none: a physics run that ended before its connect built the world. The run has finished
    by the time this is asked, so it writes nothing and says so, rather than raising an
    IndexError over a run that was otherwise fine."""

    class NoWorld:
        world = None

    recorder = FrameRecorder(NoWorld())
    assert recorder.save_gif(tmp_path / "run.gif") is None
    assert not (tmp_path / "run.gif").exists()
