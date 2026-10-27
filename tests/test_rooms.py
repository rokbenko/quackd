"""A room for the duck's simulator (`--room`): the world it lays out, the map the transport keeps of
it, and the flag on its way from the command line to the body."""

from __future__ import annotations

import io
import math
import re
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import numpy as np
import pytest
from PIL import Image
from typer.testing import CliRunner

from quackd.adapters import factory
from quackd.adapters.base import AdapterError
from quackd.adapters.factory import describe, make_adapter, parse_robot_spec
from quackd.agent.transcript import Transcript
from quackd.cli import app
from quackd.drive.run import DriveArgs, run_drive
from quackd.mcp_server import fleet_from_flags
from quackd.perception.color_blob import ColorBlobDetector
from quackd.sim2d.recorder import CAPTION_H, FrameRecorder
from quackd.sim2d.render import render_duckcam, render_topdown
from quackd.sim2d.world import DT, DUCK_R, Ball, World
from quackd.space.grid import UNKNOWN
from quackd.space.places import PlaceBook, PlaceStore
from quackd.space.room import RoomError, load_room
from quackd.space.simmap import STAND_IN, BodyNow, SimulatedMap
from quackd.transport.base import spatial_of
from quackd.transport.sim2d import Sim2DTransport
from quackd.verbs.places import PLACE_VERBS, MapParams, MarkPlaceParams
from quackd.verbs.registry import VerbContext
from quackd_microduck.transports.mapd import sim_map_params
from tests.test_simmap import PARAMS, Body
from tests.test_simmap import _still as _map_still
from tests.test_simmap import _turn as _map_turn
from tests.test_simmap import _walk as _map_walk

PLACE_NAMES = set(PLACE_VERBS)
TWO_ROOMS = load_room("two-rooms")
runner = CliRunner()


def _flat(text: str) -> str:
    return " ".join(re.sub(r"\x1b\[[0-9;]*m", "", text).split())


def _walk(world: World, vx: float, seconds: float, wz: float = 0.0) -> None:
    for k in range(round(seconds / DT)):
        if k % 4 == 0:  # inside the deadman, as the walk verb re-sends
            world.set_velocity(vx, 0.0, wz)
        world.step(DT)
    world.stop()


# ── the world ───────────────────────────────────────────────────────────────────────────


def test_a_room_holds_a_walking_duck_at_its_wall_and_counts_the_bump() -> None:
    world = World(seed=3, room=TWO_ROOMS)
    duck = world.ducks[0]
    duck.theta = math.pi  # the outer wall half a metre behind where the room starts it
    _walk(world, 0.2, 5.0)
    assert duck.bumps > 0
    assert duck.x >= -TWO_ROOMS.half_w + duck.r - 1e-9
    assert not TWO_ROOMS.blocked(duck.x, duck.y, duck.r)
    assert isinstance(duck.x, float) and isinstance(duck.y, float), "a plain float, as without"


def test_without_a_room_the_arena_clamps_as_it_always_did_and_counts_no_bump() -> None:
    world = World(seed=3)
    duck = world.ducks[0]
    duck.theta = math.pi
    _walk(world, 0.2, 10.0)
    assert duck.x == pytest.approx(-(1.0 - duck.r)) and duck.bumps == 0
    assert "room" not in world.snapshot()


def test_a_room_draws_nothing_from_the_worlds_own_random_stream() -> None:
    """The world's stream drives the duck's motion noise, so a room that drew where to put the
    ball from it would move every step that came after."""
    world = World(seed=5, room=TWO_ROOMS)
    assert world.rng.bit_generator.state == np.random.default_rng(5).bit_generator.state
    assert world.snapshot()["room"] == "two-rooms"


def test_where_a_room_puts_the_ball_is_the_seeds_and_on_free_floor_in_its_region() -> None:
    first, again, other = (World(seed=s, room=TWO_ROOMS) for s in (1, 1, 2))
    assert (first.ball.x, first.ball.y) == (again.ball.x, again.ball.y)
    assert (first.ball.x, first.ball.y) != (other.ball.x, other.ball.y)
    assert TWO_ROOMS.ball_region is not None
    xmin, ymin, xmax, ymax = TWO_ROOMS.ball_region
    for world in (first, other):
        assert xmin <= world.ball.x <= xmax and ymin <= world.ball.y <= ymax
        assert not TWO_ROOMS.blocked(world.ball.x, world.ball.y, world.ball.r)
    assert (first.ducks[0].x, first.ducks[0].y, first.ducks[0].theta) == TWO_ROOMS.start


def test_a_rolling_ball_comes_back_off_a_wall_rather_than_through_it() -> None:
    world = World(seed=3, room=TWO_ROOMS, person=False)
    world.ball = Ball(-0.3, -0.6, vx=1.0, start_x=-0.3, start_y=-0.6)
    for _ in range(round(2.0 / DT)):
        world.step(DT)
    assert world.ball.x < 0, "it rolled through the wall below the doorway"
    assert not TWO_ROOMS.blocked(world.ball.x, world.ball.y, world.ball.r)


# ── what the duck's camera sees ─────────────────────────────────────────────────────────


def _labels(world: World) -> list[str]:
    return [d.label for d in ColorBlobDetector().detect(render_duckcam(world, 256))]


def test_a_wall_hides_the_ball_and_shows_the_detector_nothing_of_its_own() -> None:
    def with_ball_at(x: float, y: float) -> World:
        world = World(seed=3, room=TWO_ROOMS, person=False)
        world.ball = Ball(x, y, start_x=x, start_y=y)
        return world

    assert "ball" in _labels(with_ball_at(0.9, -0.15)), "through the doorway"
    assert "ball" not in _labels(with_ball_at(0.5, -0.8)), "behind the wall below it"
    empty = World(seed=3, room=TWO_ROOMS, person=False)
    empty.ball.present = False
    for heading in np.linspace(-math.pi, math.pi, 12, endpoint=False):
        empty.ducks[0].theta = float(heading)
        assert _labels(empty) == [], f"a wall or a box seen as something, facing {heading:.2f}"


def test_a_room_can_hide_the_ball_from_where_the_duck_starts_whichever_way_it_turns() -> None:
    """two-rooms and sofa-gap draw the ball only where no straight line from the start reaches
    it, so the duck's camera, turned round where it starts, never sees it: a robot sent to find it
    has to go and look. In two-rooms it is always in the room the duck is not in."""
    for name in ("two-rooms", "sofa-gap"):
        room = load_room(name)
        assert room.ball_hidden and room.start is not None
        x0, y0, _ = room.start
        for seed in range(10):
            world = World(seed=seed, room=room, person=False)
            ball = (world.ball.x, world.ball.y)
            assert room.hides(x0, y0, *ball, world.ball.r), (name, seed, ball)
            if name == "two-rooms":
                assert ball[0] > 0 > x0, "the other room"
            for heading in np.linspace(-math.pi, math.pi, 12, endpoint=False):
                world.ducks[0].theta = float(heading)
                assert "ball" not in _labels(world), (name, seed, round(float(heading), 2))


def test_a_spot_any_straight_line_reaches_is_not_hidden() -> None:
    assert not TWO_ROOMS.hides(-1.0, 0.0, 0.9, -0.15, 0.05), "seen through the doorway"
    assert TWO_ROOMS.hides(-1.0, 0.0, 0.5, -0.8, 0.05), "behind the wall below it"
    assert not TWO_ROOMS.hides(-1.0, 0.0, 0.0, -0.33, 0.05), "its edge shows past the wall's end"


def test_a_hidden_ball_needs_a_region_with_floor_out_of_sight(tmp_path: Path) -> None:
    def room(ball: str) -> Path:
        path = tmp_path / "r.yaml"
        path.write_text(f"name: r\nsize: [2.0, 2.0]\nstart: [0, 0, 0]\nball: {ball}\n")
        return path

    with pytest.raises(RoomError, match="out of sight of where the duck starts"):
        World(seed=0, room=load_room(room("{region: [0.3, -0.8, 0.9, 0.8], hidden: true}")))
    with pytest.raises(RoomError, match=r"ball\.hidden is true or false"):
        load_room(room("{region: [0.3, -0.8, 0.9, 0.8], hidden: yes please}"))
    with pytest.raises(RoomError, match="unknown keys"):
        load_room(room("{region: [0.3, -0.8, 0.9, 0.8], hide: true}"))
    with pytest.raises(RoomError, match=r"ball\.region is 4 numbers"):
        load_room(room("{hidden: true}"))


def test_the_gap_behind_the_sofa_is_narrower_than_the_duck_and_seen_only_from_its_ends() -> None:
    """Open floor a ray can cross, too narrow for the duck to stand in, and looked into from
    its two ends alone: from beside the sofa at either end, the floor in the middle of the gap is
    never seen, and each end borders floor nobody has seen."""
    sofa = load_room("sofa-gap")
    gap_y = 0.95
    assert all(sofa.blocked(x / 10, gap_y, DUCK_R) for x in range(-5, 6))
    assert not sofa.blocked(0.0, gap_y, 0.0)
    body = Body(BodyNow(0.0, -1.0, -0.2, math.pi / 2, 0.0, standing=True, fallen=False, bumps=0))
    seen = SimulatedMap(sofa, PARAMS, body=body, frame_key="sim:sofa")
    _map_still(seen, body, PARAMS.still_s)
    # up beside the sofa's left end, looking along the gap, then across to its right end
    _map_walk(seen, body, 0.7)
    _map_turn(seen, body, math.atan2(0.45, 0.4) - math.pi / 2)
    _map_still(seen, body, PARAMS.still_s)
    _map_turn(seen, body, -math.atan2(0.45, 0.4))
    _map_walk(seen, body, 2.0)
    _map_turn(seen, body, math.pi - math.atan2(0.45, 0.4))
    _map_still(seen, body, PARAMS.still_s)
    grid = seen.grid_now()
    assert len(grid.keyframes) == 3
    assert grid.state_at(0.0, gap_y) == UNKNOWN, "the middle of the gap is never seen"
    inside = {grid.cell_of(x / 100, y) for x in range(-55, 56, 5) for y in (0.92, 0.97)}
    ends = {
        side
        for col, row in grid.frontiers()
        for side, near in (("left", (col + 1, row)), ("right", (col - 1, row)))
        if near in inside
    }
    assert ends == {"left", "right"}, "each end borders the gap's unseen floor"


async def test_a_recorded_run_in_a_room_shades_what_the_map_has_not_seen() -> None:
    adapter = make_adapter("microduck:sim2d", seed=3, room="two-rooms")
    await adapter.connect()
    try:
        await adapter.sleep(1.0)  # a stop looked from, and most of the room still unseen
        recorder = FrameRecorder(adapter, size=128)
        recorder.capture(Image.new("RGB", (1, 1)), "look")
        world = adapter.transport.world  # type: ignore[attr-defined]
        top = recorder.frames[-1].crop((0, CAPTION_H, 128, CAPTION_H + 128))
        assert top.tobytes() != render_topdown(world, 128).tobytes(), "nothing shaded"
        source = spatial_of(adapter)
        assert isinstance(source, SimulatedMap)
        assert top.tobytes() == render_topdown(world, 128, seen=source.grid_now()).tobytes()
    finally:
        await adapter.close()


# ── the transport and its map ───────────────────────────────────────────────────────────


async def test_a_duck_in_a_room_says_where_its_simulated_map_says_it_is() -> None:
    transport = Sim2DTransport(seed=3, room=TWO_ROOMS, map_params=sim_map_params(0.08))
    assert isinstance(transport.spatial, SimulatedMap)
    await transport.connect()
    try:
        state = await transport.get_state()
        assert state.extras["assumptions"] == [STAND_IN]
        assert "where=lost(" in state.summary() and "pose=" not in state.summary()
        await transport.sleep(1.0)
        state = await transport.get_state()
        assert "where=map(-1.00, 0.00, 0.00 rad)" in state.summary()
        assert state.extras["where"]["frame"].startswith("sim:sim2d:")
    finally:
        await transport.close()


async def test_without_a_room_or_a_map_to_keep_to_the_duck_says_nothing_of_where_it_is() -> None:
    plain = Sim2DTransport(seed=3)
    assert plain.spatial is None
    state = await plain.get_state()
    assert "where" not in state.extras and "assumptions" not in state.extras
    assert "pose=(" in state.summary()
    assert Sim2DTransport(seed=3, room=TWO_ROOMS).spatial is None, "a room with no map's numbers"
    with pytest.raises(ValueError, match="give the room to the World"):
        Sim2DTransport(seed=3, room=TWO_ROOMS, world=World(seed=3))


async def test_map_and_mark_place_wait_for_the_stop_to_be_looked_from() -> None:
    """Asked at once, at boot or straight after a walk, the map had not looked from the stop the
    duck stood at: `map` said it was lost, and `mark_place` refused. Each waits, up to the map's
    own settle time, for the stop's keyframe."""
    adapter = make_adapter("microduck:sim2d", seed=3, room="two-rooms")
    await adapter.connect()
    try:
        source = spatial_of(adapter)
        assert source is not None
        ctx = VerbContext(
            transport=adapter, places=PlaceBook(PlaceStore("t", persist=False), source)
        )
        drawn = await PLACE_VERBS["map"].execute(ctx, MapParams())
        assert drawn.ok and "where: map(-1.00, 0.00, 0.00 rad)" in drawn.summary
        adapter.transport.world.set_velocity(0.15, 0.0, 0.0)  # type: ignore[attr-defined]
        await adapter.sleep(0.5)
        await adapter.stop()
        marked = await PLACE_VERBS["mark_place"].execute(ctx, MarkPlaceParams(name="hall"))
        assert marked.ok, marked.summary
        assert source.fix().keyframes == 2, "marked where the map looked from"
    finally:
        await adapter.close()


# ── from the flag to the body ───────────────────────────────────────────────────────────


def test_the_duck_offers_places_in_a_room_and_none_without_one() -> None:
    spec = parse_robot_spec("microduck:sim2d")
    assert {v.name for v in describe(spec, room="two-rooms").verbs} >= PLACE_NAMES
    assert not PLACE_NAMES & {v.name for v in describe(spec).verbs}


async def test_connected_in_a_room_the_duck_keeps_its_place_verbs() -> None:
    adapter = make_adapter("microduck:sim2d", seed=3, room="two-rooms")
    try:
        manifest = await adapter.connect()
        assert {v.name for v in manifest.verbs} >= PLACE_NAMES
    finally:
        await adapter.close()


@pytest.mark.parametrize(
    ("backend", "said"),
    [
        ("jsonrpc", "microduck:jsonrpc is a real duck, whose room is the one it stands in"),
        ("mock", "microduck:mock lays out no room: --room is for microduck:sim2d"),
    ],
)
def test_a_room_is_refused_where_no_simulator_lays_one_out(backend: str, said: str) -> None:
    with pytest.raises(AdapterError, match=said):
        describe(parse_robot_spec(f"microduck:{backend}"), room="two-rooms")
    with pytest.raises(AdapterError, match=said):
        make_adapter(f"microduck:{backend}", room="two-rooms")


def test_a_room_file_that_cannot_be_read_is_refused_as_the_body_is_built(tmp_path: Path) -> None:
    bad = tmp_path / "bad.yaml"
    bad.write_text("name: bad\nsize: [2]\n", encoding="utf-8")
    with pytest.raises(AdapterError, match="size is 2 numbers"):
        make_adapter("microduck:sim2d", room=str(bad))


def test_an_adapter_whose_make_takes_no_room_is_refused_by_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A third party's adapter is never handed a keyword it does not take, and never one it
    would swallow with `**kwargs` and drop."""
    calls: list[dict[str, Any]] = []

    def make(backend: str, **kwargs: Any) -> Any:
        calls.append(kwargs)
        return object()

    module = ModuleType("quackd_toybot")
    module.BACKENDS = ("sim",)  # type: ignore[attr-defined]
    module.make = make  # type: ignore[attr-defined]
    module.describe = lambda backend, robot_id=None: describe(  # type: ignore[attr-defined]
        parse_robot_spec("microduck:sim2d")
    )
    monkeypatch.setitem(sys.modules, "quackd_toybot", module)
    installed = {**factory._installed(), "toybot": "quackd_toybot"}
    monkeypatch.setattr(factory, "_installed", lambda: installed)
    said = "toybot:sim has no simulator that lays out a room"
    with pytest.raises(AdapterError, match=said):
        make_adapter("toybot:sim", room="two-rooms")
    with pytest.raises(AdapterError, match=said):
        describe(parse_robot_spec("toybot:sim"), room="two-rooms")
    assert calls == []


def _run(tmp_path: Path, *argv: str) -> Any:
    return runner.invoke(
        app,
        [
            "run",
            "--goal",
            "look around and tell me what you see",
            "--llm",
            "fake",
            "--no-memory",
            "--no-gif",
            "--runs-dir",
            str(tmp_path / "runs"),
            *argv,
        ],
    )


def test_a_room_nobody_ships_is_refused_before_anything_is_built(tmp_path: Path) -> None:
    """Before the pilot too: a pilot with no key to build with is a second mistake, and the run
    says the first, which costs one sentence to fix rather than a key found for nothing."""
    result = _run(tmp_path, "--robot", "microduck:sim2d", "--room", "kitchen", "--llm", "openai")
    assert result.exit_code == 1 and "no room called 'kitchen'" in _flat(result.output)
    assert not (tmp_path / "runs").exists() or not any((tmp_path / "runs").iterdir())


def test_a_fleet_is_refused_a_room_by_every_door(tmp_path: Path) -> None:
    fleet = "a=microduck:sim2d,b=microduck:sim2d"
    result = _run(tmp_path, "--robots", fleet, "--room", "two-rooms")
    said = "--room lays out one simulator's room, and a fleet has several bodies"
    assert result.exit_code == 1 and said in _flat(result.output), result.output
    with pytest.raises(SystemExit, match=said):
        fleet_from_flags(robots=fleet, room="two-rooms")


def test_a_run_in_a_room_shows_the_pilot_where_the_duck_is(tmp_path: Path) -> None:
    result = _run(tmp_path, "--robot", "microduck:sim2d", "--room", "two-rooms", "--seed", "3")
    assert result.exit_code == 0, result.output
    (run_dir,) = (tmp_path / "runs").iterdir()
    events = Transcript.read(run_dir / "transcript.jsonl")
    texts = [str(e.get("text", "")) for e in events if e["kind"] == "observation"]
    assert texts and all("where=" in text and "pose=(" not in text for text in texts)
    assert "where=lost(not localized since it started" in texts[0], "no stop made yet"


def test_serve_mcp_describes_the_duck_in_its_room_and_refuses_a_room_nobody_ships() -> None:
    with pytest.raises(SystemExit, match="no room called 'kitchen'"):
        fleet_from_flags(robot="microduck:sim2d", room="kitchen")
    plan = fleet_from_flags(robot="microduck:sim2d", room="two-rooms")
    (manifest,) = plan.manifests.values()
    assert {v.name for v in manifest.verbs} >= PLACE_NAMES


async def test_a_drive_in_a_room_marks_places_on_the_map(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("QUACKD_MEMORY_DIR", str(tmp_path / "memory"))
    said: list[str] = []
    args = DriveArgs(
        robot="microduck:sim2d", llm="fake", room="two-rooms", runs_dir=str(tmp_path / "runs")
    )
    await run_drive(
        args, say=said.append, stream=io.StringIO("mark_place door\nmap\nquit\n"), keyboard=False
    )
    text = "\n".join(said)
    assert "marked 'door' where the robot stands, (-1.00, 0.00) by its map" in text
    assert "- door: here, where the robot stands" in text
