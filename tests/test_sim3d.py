"""The physics world keeps the cartoon's rules, and the transport over it speaks the protocol.

Skipped wholesale without `quackd[mujoco]`. The rendering tests are skipped separately when
no OpenGL context can be made, which is what a bare CI runner without EGL or OSMesa looks
like; everything else runs headless.
"""

from __future__ import annotations

import asyncio
import math
import re
from pathlib import Path
from typing import Any

import pytest

mujoco = pytest.importorskip("mujoco")

from quackd.perception.color_blob import ColorBlobDetector  # noqa: E402
from quackd.sim2d.recorder import FrameRecorder  # noqa: E402
from quackd.sim2d.world import World  # noqa: E402
from quackd.transport.base import Intent, TransportError  # noqa: E402
from quackd_microduck.sim3d.render import render_headcam, render_overview  # noqa: E402
from quackd_microduck.sim3d.scene import BALL_PARK  # noqa: E402
from quackd_microduck.sim3d.world import CONTROL_DT, DEADMAN_S, MujocoWorld, Puppet  # noqa: E402
from quackd_microduck.transports.mujoco import MujocoTransport  # noqa: E402
from tests.gl import require_render  # noqa: E402


def _run(world: MujocoWorld, seconds: float) -> None:
    for _ in range(round(seconds / CONTROL_DT)):
        world.step()


def _place_ball(world: MujocoWorld, dist: float, bearing_deg: float) -> None:
    """Put the ball `dist` metres from the front of the duck at `bearing_deg`."""
    hx, hy, _z, _yaw, _pitch = world.head_pose()
    ang = world.theta + math.radians(bearing_deg)
    adr = world._ball_qpos
    world.data.qpos[adr : adr + 3] = (hx + dist * math.cos(ang), hy + dist * math.sin(ang), 0.05)
    world.data.qvel[world._ball_dof : world._ball_dof + 6] = 0.0
    mujoco.mj_forward(world.model, world.data)
    world.ball_start = (world.ball_x, world.ball_y)


# ── the world ───────────────────────────────────────────────────────────────────────────


def test_a_seed_lays_the_arena_out_as_it_does_in_sim2d() -> None:
    """Everything the two arenas still share lands in the same place.

    The cartoon draws a person and this world has none, so the person is what they no longer
    share. That draw came last in the cartoon's order, after the duck and after the ball,
    which is the whole reason this test is still about equality and not about tolerance:
    dropping the last draw moves nothing drawn before it.
    """
    for seed in range(10):
        cartoon, physics = World(seed=seed), MujocoWorld(seed=seed)
        assert (cartoon.duck.x, cartoon.duck.y, cartoon.duck.theta) == pytest.approx(
            (physics.x, physics.y, physics.theta)
        )
        assert (cartoon.ball.x, cartoon.ball.y) == pytest.approx(
            (physics.ball_x, physics.ball_y), abs=1e-3
        )
        assert cartoon.people, "the cartoon still stands somebody up; the physics world does not"
        physics.close()


def test_deadman_zeroes_velocity() -> None:
    w = MujocoWorld(seed=0)
    w.set_velocity(0.2, 0.0, 0.0)
    _run(w, DEADMAN_S / 2)
    assert w.moving
    _run(w, DEADMAN_S)
    assert not w.moving
    w.close()


def test_walking_moves_the_body_and_the_walls_hold_it() -> None:
    w = MujocoWorld(seed=0)
    w.body.reset(0.0, 0.0, 0.0)
    for _ in range(5):
        w.set_velocity(0.2, 0.0, 0.0)
        _run(w, 0.2)
    assert 0.15 < w.x < 0.25 and abs(w.y) < 0.02
    for _ in range(60):
        w.set_velocity(0.3, 0.0, 0.0)
        _run(w, 0.2)
    assert w.x <= 1.0 - 0.08 + 1e-9
    w.close()


def test_kick_connects_only_when_close_and_ahead_and_the_ball_rolls() -> None:
    w = MujocoWorld(seed=3)
    w.body.reset(0.0, 0.0, 0.0)
    _place_ball(w, 0.6, 0.0)
    assert not w.kick()
    _place_ball(w, 0.2, 60.0)
    assert not w.kick()
    assert w.last_kick_ball_moved_m == pytest.approx(0.0, abs=1e-6)
    _place_ball(w, 0.2, 0.0)
    assert w.kick()
    assert w.kicks == 3 and w.kicks_connected == 1
    _run(w, 1.5)  # what the kick verb waits before it reads the telemetry
    moved = w.last_kick_ball_moved_m
    assert moved is not None and 0.3 <= moved <= 1.2, moved
    _run(w, 3.0)
    assert w.last_kick_ball_moved_m == pytest.approx(moved, abs=0.2)  # it stopped
    assert w.snapshot()["ball"]["present"]
    w.close()


def test_a_kick_while_sitting_or_fallen_does_nothing() -> None:
    w = MujocoWorld(seed=1)
    w.body.reset(0.0, 0.0, 0.0)
    _place_ball(w, 0.2, 0.0)
    assert w.sit_toggle() == "sitting"
    assert not w.kick()
    w.set_velocity(0.2, 0.0, 0.0)
    _run(w, 0.2)
    assert (w.x, w.y) == (0.0, 0.0), "moved while sitting"
    assert w.sit_toggle() == "standing"
    assert isinstance(w.body, Puppet)
    w.body.fall()
    assert w.posture == "fallen" and not w.kick()
    w.enable()
    assert w.posture == "standing" and w.kick()
    w.close()


def test_ground_pick_is_unreliable_and_parks_a_held_ball() -> None:
    outcomes = []
    for seed in range(12):
        w = MujocoWorld(seed=seed)
        w.body.reset(0.0, 0.0, 0.0)
        _place_ball(w, 0.1, 0.0)
        got = w.ground_pick()
        outcomes.append(got)
        if got:
            assert w.holding and not w.ball_present
            assert (w.ball_x, w.ball_y) == pytest.approx(BALL_PARK[:2])
            _run(w, 1.0)
            assert (w.ball_x, w.ball_y) == pytest.approx(BALL_PARK[:2])
            assert w.snapshot()["ball"] == {"present": False}
            assert w.ball_displacement_m == 0.0
            assert not w.ground_pick()  # the beak is full
        w.close()
    assert any(outcomes) and not all(outcomes), outcomes
    # out of reach: never
    w = MujocoWorld(seed=0)
    w.body.reset(0.0, 0.0, 0.0)
    _place_ball(w, 0.4, 0.0)
    assert not w.ground_pick()
    w.close()


def test_look_pans_the_camera_and_clamps() -> None:
    w = MujocoWorld(seed=0)
    assert not w.look(1.0, 0.5)
    assert w.head_yaw == pytest.approx(math.atan2(0.5, 1.0))
    assert w.look(0.0, 1.0)
    assert w.head_yaw == pytest.approx(math.radians(60))
    assert w.head_pose()[3] == pytest.approx(w.theta + math.radians(60))
    w.close()


def test_determinism_under_seed() -> None:
    def run(seed: int) -> tuple[float, ...]:
        w = MujocoWorld(seed=seed)
        for i in range(100):
            if i % 10 == 0:
                w.set_velocity(0.2, 0.05, 0.5)
            w.step()
        w.kick()
        _run(w, 1.0)
        out = (w.x, w.y, w.theta, w.ball_x, w.ball_y)
        w.close()
        return out

    assert run(7) == run(7)
    assert run(7) != run(8)


# ── the transport ───────────────────────────────────────────────────────────────────────


def test_construction_imports_nothing_and_refuses_an_unknown_body() -> None:
    t = MujocoTransport(seed=1, body="puppet")
    assert t.world is None and t.clock is None and t.now() == 0.0
    with pytest.raises(TransportError, match="unknown mujoco body"):
        MujocoTransport(body="toaster")
    # the real duck is the default, because a physics backend that simulates a stand-in
    # would be a cartoon with extra steps; the tests ask for the stand-in explicitly
    assert MujocoTransport().body == "microduck"


async def test_intents_reach_the_world_and_state_reads_back() -> None:
    t = MujocoTransport(seed=2, body="puppet")
    hooks: list[float] = []
    t.add_tick_hook(lambda w: hooks.append(w.t))  # before connect: buffered
    await t.connect()
    state = await t.get_state()
    assert state.posture == "standing" and state.policy == "stand" and state.battery_percent == 100
    assert (state.x, state.y, state.theta) == (t.world.x, t.world.y, t.world.theta)
    assert state.extras["physics"] == "puppet" and state.extras["ball"]["present"]

    assert (await t.send_intent(Intent.move(0.2, 0.0, 0.0))).accepted
    await t.sleep(0.1)
    assert t.world.moving and (await t.get_state()).policy == "walk"
    assert t.now() == pytest.approx(0.1) and len(hooks) == 5
    await t.stop()
    assert not t.world.moving

    ack = await t.send_intent(Intent(kind="look", params={"x": 0.0, "y": 1.0, "z": 0.0}))
    assert ack.accepted and ack.reason == "clamped to head limits"
    assert (await t.send_intent(Intent(kind="sound", params={"tag": "greet"}))).accepted
    assert t.world.quacks[0][1] == "greet"
    assert (await t.send_intent(Intent.do("sit_toggle"))).accepted
    assert (await t.get_state()).posture == "sitting"
    ack = await t.send_intent(Intent.do("kick_left"))
    assert not ack.accepted and "sitting" in (ack.reason or "")
    assert (await t.send_intent(Intent.do("sit_toggle"))).accepted
    assert (await t.send_intent(Intent.do("kick_right"))).accepted
    assert (await t.send_intent(Intent.do("roulade"))).accepted
    assert not (await t.send_intent(Intent.do("moonwalk"))).accepted
    assert not (await t.send_intent(Intent(kind="joint", params={}))).accepted
    assert (await t.send_intent(Intent.enable(True))).accepted

    got = []
    async for row in t.subscribe("state"):
        got.append(row)
        if len(got) == 2:
            break
    assert got[1]["t"] > got[0]["t"] and got[0]["topic"] == "state"
    await t.heartbeat()
    await t.close()
    with pytest.raises(Exception, match="closed"):
        await t.heartbeat()


async def test_a_fallen_duck_refuses_skills_until_enabled() -> None:
    t = MujocoTransport(seed=0, body="puppet")
    await t.connect()
    t.world.body.fall()
    assert (await t.get_state()).fallen
    ack = await t.send_intent(Intent.do("kick_right"))
    assert not ack.accepted and "fallen" in (ack.reason or "")
    await t.send_intent(Intent.enable(True))
    assert not (await t.get_state()).fallen
    await t.close()


# ── rendering ───────────────────────────────────────────────────────────────────────────


def test_the_head_camera_shows_the_detector_what_the_cartoon_would() -> None:
    w = MujocoWorld(seed=3)
    require_render(w)
    w.body.reset(0.0, 0.0, 0.3)
    _place_ball(w, 0.5, 20.0)
    w.step()
    dets = {d.label: d for d in ColorBlobDetector().detect(render_headcam(w, 256))}
    assert "ball" in dets, dets
    assert dets["ball"].bearing_deg == pytest.approx(20.0, abs=6.0)
    assert dets["ball"].est_distance_m == pytest.approx(0.5, abs=0.2)
    # the head pans the view, so the ball moves the other way
    w.look(math.cos(math.radians(45)), math.sin(math.radians(45)))
    dets = {d.label: d for d in ColorBlobDetector().detect(render_headcam(w, 256))}
    assert dets["ball"].bearing_deg == pytest.approx(-25.0, abs=6.0)
    # a held ball is out of every frame
    w.look(1.0, 0.0)
    w.ground_pick()
    while w.ball_present:
        _place_ball(w, 0.1, 0.0)
        w.ground_pick()
    assert "ball" not in {d.label for d in ColorBlobDetector().detect(render_headcam(w, 256))}
    assert render_overview(w, 64).size == (64, 64)
    w.close()


def test_upstreams_blue_scene_is_never_mistaken_for_a_person() -> None:
    """The scene is upstream's, and upstream's policies are blind: nothing in `microduck_rl`
    ever looks at its own floor. quackd put a colour detector in front of it, and that floor
    is the blue that detector calls a person — hue 105 against 114, and overlapping on
    saturation and value too, so no threshold separates them. Measured before the head camera
    got its own view: a person 0.12 m ahead in 48 of 48 frames, whichever way the duck faced.

    The head camera renders the colourless copy of the floor and no skybox. Every other view
    keeps upstream's palette.

    Nobody stands in this arena, which sharpens the test rather than retiring it: the detector
    still carries the person target, because the cartoon still has a person to find, so any
    person reported here is the scenery and there is no true one to hide behind. That is why
    this counts every person at every range, where it used to excuse anything beyond a duck's
    own radius.
    """
    detector = ColorBlobDetector()
    phantoms = frames = 0
    for seed in range(4):
        w = MujocoWorld(seed=seed, body=Puppet())
        require_render(w)
        for eighth in range(8):
            w.body.theta = eighth * math.pi / 4
            w.body._place()
            mujoco.mj_forward(w.model, w.data)
            frames += 1
            phantoms += sum(d.label == "person" for d in detector.detect(render_headcam(w, 256)))
        w.close()
    assert frames == 32
    assert phantoms == 0, f"the scenery reads as a person in {phantoms} of {frames} frames"


def test_the_two_floors_are_the_same_floor_seen_by_different_eyes() -> None:
    """One collides and one does not, so the physics cannot notice which is drawn."""
    from quackd_microduck.sim3d.scene import FLOOR_CAM_GROUP, FLOOR_GROUP

    w = MujocoWorld(seed=0, body=Puppet())
    groups = {}
    for i in range(w.model.ngeom):
        name = mujoco.mj_id2name(w.model, mujoco.mjtObj.mjOBJ_GEOM, i)
        if name and "floor" in name:
            groups[name] = (int(w.model.geom_group[i]), int(w.model.geom_contype[i]))
    assert groups["quackd_floor"] == (FLOOR_GROUP, 1), "the scene's floor is the one that collides"
    assert groups["quackd_floor_cam"] == (FLOOR_CAM_GROUP, 0), "the camera's floor is a picture"
    assert FLOOR_CAM_GROUP >= 3, "MuJoCo hides 3 and up by default, the live viewer included"
    w.close()


def test_nobody_is_in_the_arena() -> None:
    """The person marker is gone from the physics world, and gone by more than one measure.

    Three ways of asking, because a half-removal passes any one of them: the compiled model
    has no body by that name, the world exposes no people to place or avoid, and the arena XML
    carries no geom whose name says person. The cartoon keeps its own person; this asserts
    nothing about `sim2d`.
    """
    w = MujocoWorld(seed=0)
    names = {mujoco.mj_id2name(w.model, mujoco.mjtObj.mjOBJ_BODY, i) for i in range(w.model.nbody)}
    assert "person" not in names, sorted(n for n in names if n)
    assert not hasattr(w, "people"), "the world still tracks people"
    geoms = {mujoco.mj_id2name(w.model, mujoco.mjtObj.mjOBJ_GEOM, i) for i in range(w.model.ngeom)}
    assert not any("person" in (g or "") for g in geoms), sorted(g for g in geoms if g)
    assert "people" not in w.snapshot()
    w.close()


async def test_the_recorder_draws_the_physics_panes(tmp_path: Path) -> None:
    t = MujocoTransport(seed=0, body="puppet")
    rec = FrameRecorder(t, size=64)  # before connect, as the CLI does
    await t.connect()
    require_render(t.world)
    await t.send_intent(Intent.move(0.2, 0.0, 0.3))
    await t.sleep(0.6)
    rec.capture(await t.get_frame(), "observe")
    gif = rec.save_gif(tmp_path / "run.gif")
    assert gif is not None and gif.exists() and gif.stat().st_size > 500
    assert len(rec.frames) >= 3 and rec.frames[0].size == (64 * 2 + 4, 64 + 22)
    await t.close()


def test_a_physics_run_on_the_puppet_still_writes_its_gif(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`quackd run` on `microduck:mujoco` as a person types it, with no `--no-gif`. The
    recorder is built only for a body that has a world to draw, and the Microduck's physics
    transport has none until connect() builds it, after the recorder is made. A gate that read
    the world's value rather than asking whether the body has one would quietly stop every
    physics GIF, so this pins that the duck still gets its run.gif."""
    from typer.testing import CliRunner

    from quackd.cli import app
    from quackd_microduck.transports.mujoco import BODY_ENV

    probe = MujocoWorld(seed=0, body=Puppet())
    require_render(probe)
    probe.close()
    monkeypatch.setenv(BODY_ENV, "puppet")
    runs = tmp_path / "runs"
    result = CliRunner().invoke(
        app,
        [
            "run",
            "hello-world",
            "--llm",
            "fake",
            "--robot",
            "microduck:mujoco",
            "--no-memory",
            "--runs-dir",
            str(runs),
            "--no-log",
            "--gif-size",
            "64",
        ],
    )
    assert result.exit_code == 0, result.output
    gif = next(runs.rglob("run.gif"))
    assert gif.stat().st_size > 0, "an empty GIF is a README with a broken image in it"


# ── standing back up ────────────────────────────────────────────────────────────────────


def test_standing_up_clears_the_twist_that_put_it_down() -> None:
    """The command that made it fall is still on the books until the deadman notices, so a
    duck stood back up used to walk straight off again."""
    w = MujocoWorld(seed=0, body=Puppet())
    w.set_velocity(0.2, 0.0, 0.0)
    w.body.fall()
    w.enable()
    assert w.posture == "standing"
    assert w.cmd == (0.0, 0.0, 0.0)


def test_standing_up_does_not_leave_the_duck_inside_something() -> None:
    """A duck goes down while walking, so it comes to rest wherever it slid to: against a wall
    or on top of the ball. Standing up in place puts it inside them.

    The ball is the only thing left in this arena to be inside of, now that nobody stands in
    it, so it is the ball that covers the obstacle branch the person used to cover."""
    from quackd_microduck.sim3d.scene import ARENA_HALF, BALL_R
    from quackd_microduck.sim3d.world import DUCK_R

    w = MujocoWorld(seed=0, body=Puppet())
    bx, by = w.ball_x, w.ball_y
    w.body.x, w.body.y = bx, by  # face down on top of the ball
    w.body.fall()
    w.enable()
    assert math.hypot(w.x - bx, w.y - by) >= DUCK_R + BALL_R - 1e-9

    w.body.x, w.body.y = ARENA_HALF * 2, 0.0  # slid through the wall
    w.body.fall()
    w.enable()
    assert abs(w.x) <= ARENA_HALF - DUCK_R + 1e-9


# ── rendering, and what happens without a screen ────────────────────────────────────────


def test_a_frame_bigger_than_the_offscreen_buffer_is_refused_by_name() -> None:
    """The buffer is compiled into the model, so a larger frame fails inside MuJoCo. Refuse it
    where the number lives, and bound `--gif-size` to the same figure."""
    from quackd_microduck.sim3d.scene import OFFSCREEN_PX
    from quackd_microduck.sim3d.world import RenderError

    w = MujocoWorld(seed=0, body=Puppet())
    with pytest.raises(RenderError, match="OFFSCREEN_PX"):
        w.renderer(OFFSCREEN_PX + 1)
    with pytest.raises(RenderError, match="OFFSCREEN_PX"):
        w.renderer(0)


def test_the_cli_caps_a_gif_pane_at_the_size_the_model_can_actually_render() -> None:
    """`cli.py` spells 1024 rather than importing it, because importing `sim3d` on the default
    path would drag in the physics extra. This is the thread between the two numbers."""
    from quackd.cli import _GIFSIZE
    from quackd_microduck.sim3d.scene import OFFSCREEN_PX

    assert _GIFSIZE.max == OFFSCREEN_PX


def test_a_machine_with_no_opengl_is_told_which_variable_to_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """docs/faq.md promises this sentence. A bare OpenGL traceback is the least useful thing
    to hand someone on a server."""
    from quackd_microduck.sim3d.world import RenderError

    w = MujocoWorld(seed=0, body=Puppet())

    def no_display(*_a: object, **_k: object) -> None:
        raise RuntimeError("could not create an OpenGL context")

    monkeypatch.setattr(mujoco, "Renderer", no_display)
    with pytest.raises(RenderError, match="MUJOCO_GL=osmesa"):
        w.renderer(64)


def test_a_world_can_be_closed_twice_and_refuses_to_render_after() -> None:
    w = MujocoWorld(seed=0, body=Puppet())
    from quackd_microduck.sim3d.world import RenderError

    w.close()
    w.close()
    with pytest.raises(RenderError, match="closed"):
        w.renderer(64)


async def test_the_transport_says_it_is_not_connected_rather_than_dereferencing_nothing() -> None:
    t = MujocoTransport(seed=0, body="puppet")
    for call in (t.get_state(), t.get_frame()):
        with pytest.raises(TransportError, match="not connected"):
            await call
    with pytest.raises(TransportError, match="not connected"):
        t.render_panes(64)


# ── when time stops ─────────────────────────────────────────────────────────────────────


async def test_a_world_that_cannot_step_ends_the_run_instead_of_hanging_it() -> None:
    """The advancer is a task nobody awaits until `stop()`. An exception in it used to sit
    unretrieved while every sleeper waited on a future that would never resolve: the run hung
    until a verb timed out and the reason was collected by the garbage collector."""
    from quackd.transport.base import HeartbeatError

    t = MujocoTransport(seed=0, body="puppet")
    await t.connect()

    def explode(*_a: object, **_k: object) -> None:
        raise RuntimeError("the physics gave up")

    t.world.body.control = explode  # type: ignore[method-assign]
    with pytest.raises(TransportError, match="the physics gave up"):
        await asyncio.wait_for(t.sleep(0.1), timeout=5)
    # the heartbeat says the same thing, so the run aborts rather than limping on
    with pytest.raises(HeartbeatError, match="the physics gave up"):
        await t.heartbeat()
    # and a later sleep raises at once rather than starting a fresh advancer over a dead world
    with pytest.raises(TransportError, match="the physics gave up"):
        await asyncio.wait_for(t.sleep(0.1), timeout=5)
    await t.close()


async def test_two_tasks_sleeping_as_one_duck_say_so_instead_of_stranding_each_other() -> None:
    """The clock keeps one parked waiter per participant, so a second task sleeping under the
    same id silently overwrote the first and left its future unresolved for good. One
    participant is one task; the pair is usually a subscription and a verb, and the answer is
    to say which id rather than to hang."""
    t = MujocoTransport(seed=0, body="puppet")
    await t.connect()
    first = asyncio.create_task(t.sleep(0.2))
    await asyncio.sleep(0)  # let it park
    with pytest.raises(RuntimeError, match="two tasks are sleeping as 'duck-0'"):
        await asyncio.wait_for(t.sleep(0.2), timeout=5)
    await asyncio.wait_for(first, timeout=5), "and the one that got there first still wakes"
    await t.close()


# ── what the state says the duck is doing ───────────────────────────────────────────────


async def test_the_state_reports_the_gait_that_ran_not_the_one_that_was_asked_for() -> None:
    """`policy` is what reaches the model; the body's own honest `extras["policy"]` does not.
    Reporting the commanded twist here said `walk` while a duck below the gait floor stood
    still, which is the failure the floor exists to prevent."""
    t = MujocoTransport(seed=0, body="puppet")
    await t.connect()
    assert (await t.get_state()).policy == "stand"
    assert (await t.send_intent(Intent(kind="move", params={"vx": 0.2}))).accepted
    assert (await t.get_state()).policy == "stand", "commanded, but no tick has run yet"
    await t.sleep(0.1)
    assert (await t.get_state()).policy == "walk"
    await t.send_intent(Intent(kind="stop", params={}))
    await t.sleep(0.1)
    assert (await t.get_state()).policy == "stand"
    await t.close()


def test_a_body_that_declines_a_twist_is_not_reported_as_walking() -> None:
    """The puppet takes every twist, so this pins the wiring with a body that refuses one:
    the world must read the body's answer, not its own command."""
    w = MujocoWorld(seed=0, body=Puppet())
    w.set_velocity(0.2, 0.0, 0.0)
    w.body.fall()  # a body that is down declines whatever it is sent
    w.step()
    assert w.moving, "the command is still on the books"
    assert not w.body.walking
    assert w.policy == "stand"


# ── what the world refuses to do ────────────────────────────────────────────────────────


def test_a_twist_that_is_not_a_number_never_reaches_the_body() -> None:
    """`np.clip` passes NaN through and every comparison against it is False, so without a
    guard a NaN twist arrives at the servos as a NaN target and the physics quietly resets."""
    w = MujocoWorld(seed=0, body=Puppet())
    for bad in ((math.nan, 0.0, 0.0), (0.0, math.inf, 0.0), (0.0, 0.0, math.nan)):
        with pytest.raises(ValueError, match="finite"):
            w.set_velocity(*bad)
    assert w.cmd == (0.0, 0.0, 0.0), "and nothing was commanded on the way out"
    with pytest.raises(ValueError, match="finite"):
        w.look(math.nan, 0.0)


async def test_a_non_finite_intent_is_refused_rather_than_ending_the_run() -> None:
    """A refusal is an answer the pilot can read and correct; an exception out of
    `send_intent` would end the run over one bad number."""
    t = MujocoTransport(seed=0, body="puppet")
    await t.connect()
    ack = await t.send_intent(Intent(kind="move", params={"vx": math.nan}))
    assert not ack.accepted
    assert "finite" in (ack.reason or "")
    assert (await t.get_state()).policy != "walk", "and the duck was not left walking"
    assert (await t.send_intent(Intent(kind="move", params={"vx": 0.2}))).accepted
    await t.close()


def test_a_world_that_mujoco_has_reset_under_us_refuses_to_carry_on() -> None:
    """MuJoCo answers a non-finite state by resetting the world and logging a warning, not by
    raising. A run that kept going would report poses from a world that had quietly restarted:
    an ordinary-looking transcript that is fiction."""
    w = MujocoWorld(seed=0, body=Puppet())
    w.data.qvel[w._ball_dof] = math.inf
    with pytest.raises(TransportError, match="diverged"):
        for _ in range(5):
            w.step()


# ── what a wrong policy file does ───────────────────────────────────────────────────────


def test_a_policy_of_the_wrong_shape_is_refused_by_name() -> None:
    """`OBS_LEN` was a number in a comment: nothing compared it to the model, and the input
    name and output index were written in by hand. A re-export with one more observation
    would have failed inside onnxruntime on the first tick, with nothing naming the file."""
    from types import SimpleNamespace

    from quackd_microduck.sim3d.microduck import ACTION_LEN, OBS_LEN, PolicyError, _check_io

    def session(inputs: list[Any], outputs: list[Any]) -> Any:
        return SimpleNamespace(get_inputs=lambda: inputs, get_outputs=lambda: outputs)

    good_in = SimpleNamespace(name="obs", shape=[1, OBS_LEN])
    good_out = SimpleNamespace(name="actions", shape=[1, ACTION_LEN])
    assert _check_io(session([good_in], [good_out]), "walk.onnx") == "obs"
    # whatever upstream calls it, quackd asks by the name the file declares
    renamed = SimpleNamespace(name="observation", shape=[1, OBS_LEN])
    assert _check_io(session([renamed], [good_out]), "walk.onnx") == "observation"

    short = SimpleNamespace(name="obs", shape=[1, OBS_LEN - 1])
    with pytest.raises(PolicyError, match=re.escape("walk.onnx")):
        _check_io(session([short], [good_out]), "walk.onnx")
    wide = SimpleNamespace(name="actions", shape=[1, ACTION_LEN + 2])
    with pytest.raises(PolicyError, match="actuators"):
        _check_io(session([good_in], [wide]), "walk.onnx")
    with pytest.raises(PolicyError, match="one of each"):
        _check_io(session([good_in, good_in], [good_out]), "walk.onnx")


# ── the real duck ───────────────────────────────────────────────────────────────────────


#: The Microduck body needs upstream's model and policies, which are fetched at run time and
#: never shipped. Tests must not reach the network, so these run only when a previous run
#: (or a developer) has already filled the cache, and are skipped everywhere else. Every
#: caller carries `@pytest.mark.real_duck`, which is what exempts it from the conftest's
#: throwaway cache; without the marker this would look in an empty directory and always skip.
def _cached_microduck() -> Any:
    from quackd_microduck.sim3d.assets import AssetError, ensure_microduck

    try:
        return ensure_microduck(offline=True)
    except AssetError as e:
        pytest.skip(f"upstream's Microduck model is not cached: {e}")


@pytest.mark.real_duck
def test_the_real_duck_walks_turns_and_stays_upright() -> None:
    assets = _cached_microduck()
    from quackd_microduck.sim3d.microduck import MicroduckBody

    w = MujocoWorld(seed=6, body=MicroduckBody(assets))
    assert w.posture == "standing"
    assert w.snapshot()["physics"] == "microduck"
    assert any("ONNX" in a for a in w.snapshot()["assumptions"])

    def drive(vx: float, wz: float, seconds: float) -> tuple[float, float]:
        """Metres travelled and NET heading change. Net, not the sum of the steps: a real
        gait wags the trunk every stride, so the absolute total is large walking straight."""
        x0, y0, turned, previous = w.x, w.y, 0.0, w.theta
        for i in range(round(seconds / CONTROL_DT)):
            if i % 5 == 0:  # re-sent at 10 Hz, as the move verb does, or the deadman fires
                w.set_velocity(vx, 0.0, wz)
            w.step()
            turned += math.atan2(math.sin(w.theta - previous), math.cos(w.theta - previous))
            previous = w.theta
        return math.hypot(w.x - x0, w.y - y0), turned

    walked, turned = drive(0.3, 0.0, 6.0)
    assert walked > 0.3, f"asked for 0.3 m/s and moved {walked:.2f} m in 6 s"
    assert abs(turned) < 1.0, f"walking straight turned it {turned:.2f} rad"
    assert w.posture == "standing"
    _walked, turned = drive(0.0, 1.2, 6.0)
    assert turned > 1.5, f"asked for 1.2 rad/s and turned {turned:.2f} rad in 6 s"
    assert w.posture == "standing", "the duck fell over walking on its own policy"
    w.close()


@pytest.mark.real_duck
def test_a_duck_that_has_stood_still_walks_when_asked_for_the_forward_floor() -> None:
    """The floor was measured from a reset, where the walking policy stepped at 0.23. Once the
    stand policy had held the duck still two seconds it stepped at 0.23 on no seed, so a `move`
    raised to the floor after a stop said it walked and covered nothing."""
    from quackd_microduck.sim3d.gait import ACHIEVED_FRACTION, GAIT_FLOOR_VX
    from quackd_microduck.sim3d.microduck import MicroduckBody

    w = MujocoWorld(seed=3, body=MicroduckBody(_cached_microduck()))
    for _ in range(round(2.0 / CONTROL_DT)):
        w.step()  # nothing asked of it: the stand policy holds it still
    x0, y0 = w.x, w.y
    seconds = 6.0
    for i in range(round(seconds / CONTROL_DT)):
        if i % 5 == 0:  # re-sent at 10 Hz, as the move verb does
            w.set_velocity(GAIT_FLOOR_VX, 0.0, 0.0)
        w.step()
    walked = math.hypot(w.x - x0, w.y - y0)
    promised = ACHIEVED_FRACTION * GAIT_FLOOR_VX * seconds
    assert walked > promised / 2, f"asked for the floor after a stand, it walked {walked:.2f} m"
    assert w.posture == "standing"
    w.close()


@pytest.mark.real_duck
@pytest.mark.parametrize("way", [1.0, -1.0], ids=["left", "right"])
def test_a_duck_that_has_stood_still_turns_both_ways_when_asked_at_quackds_clamp(
    way: float,
) -> None:
    """The turning floor was 1.0 rad/s, quackd's own turn clamp, and asked for that the duck turned
    right no more than sixteen degrees in five seconds, from a stand or a reset, and from a stand
    stalled two seconds before it turned left: a `turn` right barely turned."""
    from quackd_microduck import microduck_manifest
    from quackd_microduck.sim3d.gait import ACHIEVED_FRACTION, GAIT_FLOOR_WZ
    from quackd_microduck.sim3d.microduck import MicroduckBody

    clamp = float(microduck_manifest("mujoco").limits["max_wz"])
    w = MujocoWorld(seed=3, body=MicroduckBody(_cached_microduck()))
    for _ in range(round(2.0 / CONTROL_DT)):
        w.step()  # nothing asked of it: the stand policy holds it still
    turned, previous = 0.0, w.theta
    seconds = 3.0
    for i in range(round(seconds / CONTROL_DT)):
        if i % 5 == 0:  # re-sent at 10 Hz, as the turn verb does
            w.set_velocity(0.0, 0.0, way * clamp)
        w.step()
        turned += math.atan2(math.sin(w.theta - previous), math.cos(w.theta - previous))
        previous = w.theta
    promised = ACHIEVED_FRACTION * max(clamp, GAIT_FLOOR_WZ) * seconds
    assert way * turned > promised / 2, f"asked to turn after a stand, it turned {turned:.2f} rad"
    assert w.posture == "standing"
    w.close()


@pytest.mark.real_duck
def test_a_duck_that_fell_on_its_face_stands_up_facing_the_way_it_was_going() -> None:
    """Yaw from the trunk quaternion is the right answer while the duck is upright and an
    arbitrary one where it usually is not. Face-down is the gimbal degeneracy, and it is also
    how a biped most often lands: measured on the real model, a duck facing +x that goes onto
    its nose reads as yaw 3.14, so `stand_up` used to put it back on its feet facing
    backwards, one step into whatever it had been walking towards."""
    from quackd_microduck.sim3d.microduck import MicroduckBody

    w = MujocoWorld(seed=0, body=MicroduckBody(_cached_microduck()))
    b = w.body
    b.reset(0.0, 0.0, 0.0)  # facing +x
    b._data.qpos[b.free_q + 3 : b.free_q + 7] = (
        math.cos(-math.pi / 4),
        0.0,
        math.sin(-math.pi / 4),
        0.0,
    )
    mujoco.mj_forward(b._model, b._data)

    # Prone is exactly the singularity, so both arguments to the yaw's `atan2` collapse to
    # zero and what comes back is decided by the last bit of `1 - 2 * (qy² + qz²)`, which is
    # mathematically zero. It rounds negative on Windows and positive on Linux, so the same
    # duck in the same pose reads pi here and 0 in CI, and asserting either one is asserting a
    # coin flip. What is true everywhere is that the reading carries no information at all.
    qw, qx, qy, qz = b._data.qpos[b.free_q + 3 : b.free_q + 7]
    assert abs(2 * (qw * qz + qx * qy)) < 1e-9, "the quaternion yaw's numerator has not collapsed"
    assert abs(1 - 2 * (qy * qy + qz * qz)) < 1e-9, "nor its denominator, so this is not prone"
    assert abs(math.sin(b.pose()[2])) < 0.01, "and so it reads as 0 or pi, never anything between"

    # the assertion with something behind it: the trunk's own axis still knows which way the
    # duck was walking, which is what `stand_up` reads and why it exists
    assert b.heading() == pytest.approx(0.0, abs=0.01), "the trunk's own axis says forwards"
    w.close()


@pytest.mark.real_duck
def test_the_real_duck_refuses_to_sit_and_stands_itself_up() -> None:
    from quackd_microduck.sim3d.microduck import MicroduckBody
    from quackd_microduck.sim3d.world import NotSupported

    w = MujocoWorld(seed=0, body=MicroduckBody(_cached_microduck()))
    with pytest.raises(NotSupported, match="sit"):
        w.sit_toggle()
    # tip it over: a policy that cannot get up must at least report that it is down
    w.data.qpos[w.body.free_q + 3 : w.body.free_q + 7] = (0.7, 0.7, 0.0, 0.0)
    for _ in range(30):
        w.step()
    assert w.posture == "fallen"
    assert w.snapshot()["tilt_deg"] > 45
    w.enable()
    for _ in range(50):
        w.step()
    assert w.posture == "standing"
    w.close()
