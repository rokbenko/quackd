"""`explore`: the duck walks to what its map has not seen and stops so the map can look.

Every run is the duck's simulator in a room, on the simulator's own clock, so minutes of
exploring take a second or two.
"""

from __future__ import annotations

import asyncio
import dataclasses
import itertools
import math
from typing import Any

import pytest

from quackd.adapters.factory import make_adapter
from quackd.adapters.manifest import VerbSpec
from quackd.agent.prompts import body_lines, body_summary
from quackd.duckfile.parser import parse_duck_text
from quackd.duckfile.validate import validate_duck
from quackd.safety import Aborted, Executor, allow_all
from quackd.sim2d.world import DUCK_R, World
from quackd.space.grid import FREE
from quackd.space.places import MAX_PLACES, Place, PlaceBook, PlaceStore
from quackd.space.pose import wrap_angle
from quackd.space.room import Box, Wall, load_room
from quackd.space.simmap import BUMPED, CARRIED, SimulatedMap
from quackd.transport.base import DuckState, spatial_of
from quackd.transport.sim2d import Sim2DTransport
from quackd.verbs import explore as explore_module
from quackd.verbs.explore import (
    EXPLORE_MAX_MIN,
    EXPLORE_TIMEOUT_S,
    START,
    TRIES,
    ExploreParams,
)
from quackd.verbs.navigate import Walked
from quackd.verbs.registry import (
    STOP_ENDED,
    STOPPED,
    VerbContext,
    VerbResult,
    registry_from_manifest,
)
from quackd_microduck import (
    MICRODUCK_VERBS,
    NO_MAP_WHY,
    NO_SIZE_WHY,
    MicroduckAdapter,
    describe,
)
from quackd_microduck.transports.jsonrpc_unix import JsonRpcUnixTransport
from quackd_microduck.transports.mapd import MapdSource, sim_map_params
from quackd_moss import verbs as moss_verbs
from tests.fake_mapd import FakeMapd
from tests.test_go_to_place import Recording, _map
from tests.test_jsonrpc import FakeRobotd


async def _duck(room: str = "two-rooms", seed: int = 3) -> tuple[Any, Recording, VerbContext]:
    """A duck in a room, connected, and the context a verb of its runs in, on a body that writes
    down what it is sent."""
    adapter = make_adapter("microduck:sim2d", seed=seed, room=room)
    manifest = await adapter.connect()
    body = Recording(adapter)
    source = spatial_of(adapter)
    assert source is not None
    book = PlaceBook(PlaceStore("t", persist=False), source)
    return adapter, body, VerbContext(transport=body, manifest=manifest, places=book)


async def _explore(ctx: VerbContext, minutes: float = 4.0) -> VerbResult:
    return await MICRODUCK_VERBS["explore"].execute(ctx, ExploreParams(minutes=minutes))


def _keyframes_noted(monkeypatch: pytest.MonkeyPatch) -> list[tuple[float, float, float]]:
    """Where each keyframe the map takes stood and looked, from now on."""
    looked: list[tuple[float, float, float]] = []
    keyframe = SimulatedMap._keyframe

    def noted(self: Any, now: Any) -> None:
        looked.append((now.x, now.y, now.theta + now.head_yaw))
        keyframe(self, now)

    monkeypatch.setattr(SimulatedMap, "_keyframe", noted)
    return looked


def _unseen_from_the_first_stop(looked: list[tuple[float, float, float]], view: float) -> list[int]:
    """The whole degrees round the first stop that no keyframe taken there saw."""
    x0, y0, _ = looked[0]
    here = [d for x, y, d in looked if math.dist((x, y), (x0, y0)) < 0.05]
    return [
        degree
        for degree in range(360)
        if not any(abs(wrap_angle(math.radians(degree) - d)) <= view / 2 + 1e-9 for d in here)
    ]


# ── what it covers ──────────────────────────────────────────────────────────────────────


async def test_it_maps_both_rooms_and_marks_where_it_began() -> None:
    adapter, body, ctx = await _duck("two-rooms")
    try:
        result = await _explore(ctx)
        assert result.ok and result.data["exit"] == "done", result.summary
        assert "'start' is where it began" in result.summary
        grid = await _map(adapter).grid()
        assert grid is not None
        for middle in ((-0.75, 0.0), (0.75, -0.3)):
            assert grid.state_at(*middle) == FREE, middle
        assert ctx.places is not None
        start = ctx.places.store.get("start")
        assert start is not None and (start.x, start.y) == pytest.approx((-1.0, 0.0), abs=0.05)
        assert body.sent[-1][1] == "stop"
    finally:
        await adapter.close()


async def test_every_look_waits_for_a_keyframe_and_no_leg_outruns_a_look(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A look is the map's keyframe, so one taken before the map has looked again saw nothing;
    and the map corrects drift only at stops, so the walk between two is never longer than a look
    reaches."""
    adapter, _body, ctx = await _duck("sofa-gap")
    source = _map(adapter)
    head = explore_module._Explore.look_with_head
    looks: list[tuple[int, int]] = []

    async def looked(self: Any, yaw: float) -> Any:
        before = source.fix().keyframes or 0
        fix = await head(self, yaw)
        looks.append((before, fix.keyframes or 0))
        return fix

    monkeypatch.setattr(explore_module._Explore, "look_with_head", looked)
    try:
        result = await _explore(ctx)
        assert result.ok, result.summary
        assert looks and all(after > before for before, after in looks), looks
        grid = await source.grid()
        assert grid is not None and source.view is not None
        stops = grid.keyframes
        legs = [math.dist((a.x, a.y), (b.x, b.y)) for a, b in itertools.pairwise(stops)]
        assert max(legs) <= source.view[1], max(legs)
    finally:
        await adapter.close()


async def test_one_seed_explores_one_way() -> None:
    def run() -> Any:
        async def go() -> VerbResult:
            adapter, _body, ctx = await _duck("hall", seed=5)
            try:
                return await _explore(ctx)
            finally:
                await adapter.close()

        return asyncio.run(go())

    first, again = await asyncio.to_thread(run), await asyncio.to_thread(run)
    assert first.data["stops"] == again.data["stops"] and first.data["looks"] == again.data["looks"]


# ── how it ends ─────────────────────────────────────────────────────────────────────────


async def test_a_stop_from_beside_it_ends_it_as_the_brake_with_no_twist_after() -> None:
    adapter, body, ctx = await _duck("two-rooms")
    try:
        told: list[float] = []

        def brake(world: Any) -> None:
            if not told and world.t > 4.0 and body.twists():
                told.append(world.t)

        adapter.transport.add_tick_hook(brake)
        ctx.interrupted = lambda: STOPPED if told else None
        result = await _explore(ctx)
        assert not result.ok and result.data["exit"] == "stopped", result.summary
        assert result.data.get("ended_by") == STOP_ENDED
        assert body.twists(since=told[0] + 1e-9) == []
        assert body.sent[-1][1] == "stop"
    finally:
        await adapter.close()


async def test_a_newer_command_cancelling_it_leaves_the_body_stopped() -> None:
    adapter, body, ctx = await _duck("two-rooms")
    try:
        task = asyncio.ensure_future(_explore(ctx))
        cut: list[float] = []

        def preempt(world: Any) -> None:
            if not cut and world.t > 4.0 and body.twists():
                cut.append(world.t)
                task.cancel()

        adapter.transport.add_tick_hook(preempt)
        with pytest.raises(asyncio.CancelledError):
            await task
        assert body.twists(since=cut[0] + 1e-9) == []
        assert body.sent[-1][1] == "stop"
    finally:
        await adapter.close()


async def test_its_own_minutes_end_it_well_and_the_runs_budget_does_not() -> None:
    # minutes that run out in the middle of a walk, whose own clock they are, and in the middle of
    # the first stop's looks, which no walk's clock is
    for minutes in (0.25, 0.1):
        adapter, body, ctx = await _duck("hall")
        try:
            result = await _explore(ctx, minutes=minutes)
            assert result.ok and result.data["exit"] == "minutes", result.summary
            assert result.data["seconds"] <= minutes * 60 + 1.0, (minutes, result.data)
            assert body.sent[-1][1] == "stop"
        finally:
            await adapter.close()
    # and minutes that run out while it turns to find its map again, which no walk does
    adapter, body, ctx = await _duck("hall")
    try:
        source = _map(adapter)
        adapter.transport.add_tick_hook(lambda _world: source.lose(CARRIED))
        result = await _explore(ctx, minutes=0.05)
        assert result.ok and result.data["exit"] == "minutes", result.summary
        assert result.data["seconds"] <= 0.05 * 60 + 1.0, result.data
    finally:
        await adapter.close()
    adapter, body, ctx = await _duck("hall")
    try:
        spent: list[float] = []

        def budget(world: Any) -> None:
            if not spent and world.t > 6.0:
                spent.append(world.t)

        adapter.transport.add_tick_hook(budget)
        ctx.interrupted = lambda: "the run's 4 steps are spent" if spent else None
        result = await _explore(ctx)
        assert not result.ok and result.data["exit"] == "budget", result.summary
        assert result.data["interrupted"] == "the run's 4 steps are spent"
        assert body.twists(since=spent[0] + 1e-9) == []
    finally:
        await adapter.close()


async def test_a_walk_its_minutes_leave_no_time_for_says_so(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The nearest stretch further than the time left can walk it is refused before a step, and
    the words say that, not that its minutes ran out with seconds still left."""
    look = explore_module._Explore.look

    async def little_left(self: Any, *, every_way: bool) -> Any:
        fix = await look(self, every_way=every_way)
        self.deadline = self.now() + 1.0  # a second, which walks no way to anywhere
        return fix

    monkeypatch.setattr(explore_module._Explore, "look", little_left)
    adapter, body, ctx = await _duck("two-rooms")
    try:
        result = await _explore(ctx)
        assert result.ok and result.data["exit"] == "minutes", result.summary
        assert "s of walking, and its minutes leave 1 s" in result.summary
        assert "ran out" not in result.summary
        assert body.sent[-1][1] == "stop"
    finally:
        await adapter.close()


async def test_its_words_name_start_only_where_this_call_marked_it() -> None:
    """Minutes that run out before it knows where it began leave 'start' unmarked, and an earlier
    exploration's 'start', elsewhere, standing: its words do not send the pilot back there. A
    'start' it could not keep is said to be one."""
    adapter, _body, ctx = await _duck("hall")
    try:
        source = _map(adapter)
        assert ctx.places is not None
        earlier = Place(START, 1.5, 0.0, 0.0, source.frame.id, "map", 0.0)
        ctx.places.store.mark(earlier, reserved_ok=True)
        adapter.transport.add_tick_hook(lambda _world: source.lose(CARRIED))
        result = await _explore(ctx, minutes=0.05)
        assert result.ok and result.data["exit"] == "minutes", result.summary
        assert f"'{START}'" not in result.summary and result.data["marked_start"] is False
        start = ctx.places.store.get(START)
        assert start is not None and (start.x, start.y) == (earlier.x, earlier.y)
    finally:
        await adapter.close()
    adapter, _body, ctx = await _duck("arena")
    try:
        assert ctx.places is not None
        for k in range(MAX_PLACES):
            ctx.places.store.mark(Place(f"p{k}", 0.0, 0.0, 0.0, "elsewhere", "map", 0.0))
        result = await _explore(ctx)
        assert result.ok and result.data["marked_start"] is False, result.summary
        assert f"'{START}' was not marked where it began: this robot keeps" in result.summary
        assert ctx.places.store.get(START) is None
    finally:
        await adapter.close()


async def test_the_runs_battery_rule_is_read_at_its_own_stops_and_aborts_it() -> None:
    """Read at the stop it begins at, before any walk reads it at one of its own: a pack already
    under the line ends the exploration before the body has moved at all."""
    adapter, body, ctx = await _duck("hall")
    try:

        async def rule(_state: Any) -> None:
            await body.stop()
            raise Aborted("abort_when: battery 9% below 20%; the verb stopped where it stood")

        ctx.abort_when = rule
        with pytest.raises(Aborted, match="battery"):
            await _explore(ctx)
        assert body.twists() == []
        assert body.sent[-1][1] == "stop"
    finally:
        await adapter.close()


async def test_the_battery_rule_read_inside_a_walk_of_its_own_ends_the_run_in_its_words() -> None:
    """Through the executor, as go_to_place's own guard: the rule's reader at a stop of a walk
    stops the body and raises, and a stop awaited again on the way out of the exploration let the
    run's abort cancel it there, the words becoming a cancelled verb's."""
    adapter, body, ctx = await _duck("two-rooms")
    try:
        walked = body.twists

        async def battery() -> DuckState:
            state = await adapter.get_state()
            state.battery_percent = 10.0 if walked() else 40.0
            return state

        body.get_state = battery  # type: ignore[method-assign]
        duck = parse_duck_text(
            "---\nduck: 0\nname: t\ndescription: d\nverbs:\n  allow: [explore]\n"
            "success: [x]\nabort_when: [Battery below 15%]\n---\n# Task\nx\n"
        )
        registry = registry_from_manifest(ctx.manifest, adapter)  # type: ignore[arg-type]
        assert ctx.places is not None
        executor = Executor(
            registry, body, contract=duck.frontmatter, places=ctx.places.store, confirm=allow_all
        )
        body.round_trip = 3  # an abort set while a stop is on its way can cancel what sent it
        with pytest.raises(Aborted, match="battery 10% below 15%; the verb stopped where it stood"):
            await executor.run_verb("explore", {"minutes": 1.0})
        assert body.twists(), "it never walked"
        assert body.sent[-1][1] == "stop"
    finally:
        await adapter.close()


async def test_somebody_holding_it_between_two_looks_ends_it_before_the_next_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Read before every command it sends: a duck held while it stood looking is not turned."""
    adapter, body, ctx = await _duck("two-rooms")
    head = explore_module._Explore.look_with_head
    looks_after: list[Any] = []

    async def held_after(self: Any, yaw: float) -> Any:
        fix = await head(self, yaw)
        body.extras["picked_up"] = True  # held once the first look is done
        return fix

    send = body.send_intent

    async def noted(intent: Any) -> Any:
        if intent.kind == "look" and body.extras.get("picked_up"):
            looks_after.append(intent)
        return await send(intent)

    monkeypatch.setattr(explore_module._Explore, "look_with_head", held_after)
    body.send_intent = noted  # type: ignore[method-assign]
    try:
        result = await _explore(ctx)
        assert not result.ok and result.data["exit"] == "held", result.summary
        assert body.twists() == [] and looks_after == [], "the head turned for a held duck"
    finally:
        await adapter.close()


async def test_a_twist_the_body_refuses_ends_it_whatever_its_state_says() -> None:
    """A body refuses a twist for reasons its state need not show, robotd no longer showing the
    twists it was sent among them: one refused ends the exploration rather than the next walk
    being sent into the same refusal, and the one after it."""
    adapter, body, ctx = await _duck("two-rooms")
    try:
        refused: list[float] = []

        def refuse(world: Any) -> None:
            if not refused and world.t > 4.0 and body.twists():
                refused.append(world.t)
                body.refuse_after = sum(1 for s in body.sent if s[1] == "move")

        adapter.transport.add_tick_hook(refuse)
        result = await _explore(ctx)
        assert not result.ok and result.data["exit"] == "held", result.summary
        assert "a twist was refused" in result.summary
        assert result.summary.count("explore:") == 1, result.summary
        assert body.twists(since=refused[0] + 1e-9) == []
    finally:
        await adapter.close()


async def test_the_first_stop_sees_all_the_way_round(monkeypatch: pytest.MonkeyPatch) -> None:
    """Views a step apart overlap only by what a view is wider than the step, so one that lands
    off its heading by more leaves a wedge no view saw: a turn read four times a second does, and
    the head finishes it."""
    looked = _keyframes_noted(monkeypatch)
    adapter, _body, ctx = await _duck("arena")
    try:
        result = await _explore(ctx)
        assert result.ok, result.summary
        source = _map(adapter)
        assert source.view is not None
        unseen = _unseen_from_the_first_stop(looked, source.view[0])
        assert unseen == [], unseen
    finally:
        await adapter.close()


async def test_a_head_that_gets_there_a_tick_late_still_gets_its_look(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A head that says where it got to rather than where it was sent, as the physics duck's
    does, has not moved when it is first read after the look, and a map that has looked from the
    stop reads as done with it: each look waits for a keyframe more than the map had, so the
    first stop still sees all the way round."""
    pending: dict[int, float] = {}
    look, step = World.look, World.step

    def lagging(self: Any, x: float, y: float, z: float = 0.0, duck_index: int = 0) -> bool:
        duck = self.ducks[duck_index]
        was = duck.head_yaw
        clamped = look(self, x, y, z, duck_index)
        pending[duck_index], duck.head_yaw = duck.head_yaw, was  # there at the next step
        return clamped

    def stepped(self: Any, *args: Any, **kw: Any) -> None:
        for index, yaw in pending.items():
            self.ducks[index].head_yaw = yaw
        pending.clear()
        step(self, *args, **kw)

    monkeypatch.setattr(World, "look", lagging)
    monkeypatch.setattr(World, "step", stepped)
    looked = _keyframes_noted(monkeypatch)
    adapter, _body, ctx = await _duck("arena")
    try:
        result = await _explore(ctx)
        assert result.ok, result.summary
        source = _map(adapter)
        assert source.view is not None
        unseen = _unseen_from_the_first_stop(looked, source.view[0])
        assert unseen == [], unseen
    finally:
        await adapter.close()


async def test_somebody_picking_it_up_ends_it_at_once() -> None:
    adapter, body, ctx = await _duck("two-rooms")
    try:
        held: list[float] = []

        def pick_up(world: Any) -> None:
            if not held and world.t > 4.0 and body.twists():
                held.append(world.t)
                body.refuse_after = sum(1 for s in body.sent if s[1] == "move")
                body.extras["picked_up"] = True

        adapter.transport.add_tick_hook(pick_up)
        result = await _explore(ctx)
        assert not result.ok and result.data["exit"] == "held", result.summary
        assert body.twists(since=held[0] + 1e-9) == []
        assert body.sent[-1][1] == "stop"
    finally:
        await adapter.close()


# ── when its map loses it ───────────────────────────────────────────────────────────────


async def test_a_bump_is_found_again_and_the_room_explored_after_it() -> None:
    adapter, body, ctx = await _duck("two-rooms")
    try:
        source = _map(adapter)
        bumped: list[float] = []

        def bump(world: Any) -> None:
            if not bumped and world.t > 4.0 and body.twists():
                bumped.append(world.t)
                source.lose(BUMPED)

        adapter.transport.add_tick_hook(bump)
        result = await _explore(ctx)
        assert result.ok and result.data["exit"] == "done", result.summary
        assert bumped and source.fix().localized
    finally:
        await adapter.close()


async def test_a_map_that_never_finds_it_gets_one_whole_turn_and_then_it_ends_lost() -> None:
    adapter, body, ctx = await _duck("two-rooms")
    try:
        source = _map(adapter)
        carried: list[float] = []

        def carry(world: Any) -> None:
            if not carried and world.t > 4.0 and body.twists():
                carried.append(world.t)
            if carried:
                source.lose(CARRIED)  # whatever it looks at, the map does not find it

        adapter.transport.add_tick_hook(carry)
        start = _heading(adapter)
        result = await _explore(ctx)
        assert not result.ok and result.data["exit"] == "lost", result.summary
        assert "a whole turn of looks did not find it" in result.summary
        stops = [t for t, kind, _ in body.sent if kind == "stop" and t > carried[0]]
        assert source.view is not None
        assert len(stops) >= explore_module.views_round(math.degrees(source.view[0]))[1]
        assert abs(wrap_angle(_heading(adapter) - start)) < math.pi, "it ends facing round again"
    finally:
        await adapter.close()


def _heading(adapter: Any) -> float:
    return float(adapter.transport.world.ducks[0].theta)


async def test_a_stretch_it_looked_at_without_seeing_past_is_given_up_and_left(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Floor a look cannot see, glass or a gap at a grazing angle: here a corner of the far room
    the map never marks. It is walked to once, looked at, given up, and never walked at again."""
    glass = (1.0, -0.95, 1.45, -0.5)
    mark = SimulatedMap._mark

    def blind_there(self: Any, x: float, y: float, state: int, **kw: Any) -> None:
        if glass[0] <= x <= glass[2] and glass[1] <= y <= glass[3]:
            return
        mark(self, x, y, state, **kw)

    monkeypatch.setattr(SimulatedMap, "_mark", blind_there)
    adapter, _body, ctx = await _duck("two-rooms")
    try:
        result = await _explore(ctx)
        assert result.ok and result.data["exit"] == "done", result.summary
        assert result.data["given_up"] > 0
        middle = ((glass[0] + glass[2]) / 2, (glass[1] + glass[3]) / 2)
        near = [stop for stop in result.data["stops"] if math.dist(stop, middle) < 0.6]
        assert len(near) <= 1, result.data["stops"]
        # and the next call on the same connection, in the same map, which begins where the last
        # one ended, walks to it no more
        again = await _explore(ctx)
        assert again.ok and again.data["exit"] == "done", again.summary
        walked_to = again.data["stops"][1:]
        assert not [stop for stop in walked_to if math.dist(stop, middle) < 0.6], walked_to
        assert again.data["given_up"] >= result.data["given_up"]
    finally:
        await adapter.close()


async def _behind_glass(
    monkeypatch: pytest.MonkeyPatch, *, walls: tuple[Wall, ...] = (), boxes: tuple[Box, ...] = ()
) -> tuple[Any, VerbContext]:
    """A duck in the hall with `walls` and `boxes` of glass added: the body walks into them, and
    the map, whose ToF sees through them, never marks them."""
    hall = load_room("hall")
    reveal = SimulatedMap._reveal

    def through_the_glass(self: Any, now: Any) -> None:
        kept, self.room = self.room, hall
        try:
            reveal(self, now)
        finally:
            self.room = kept

    monkeypatch.setattr(SimulatedMap, "_reveal", through_the_glass)
    room = dataclasses.replace(hall, walls=(*hall.walls, *walls), boxes=(*hall.boxes, *boxes))
    adapter = MicroduckAdapter(Sim2DTransport(seed=3, room=room, map_params=sim_map_params(DUCK_R)))
    manifest = await adapter.connect()
    source = spatial_of(adapter)
    assert source is not None
    book = PlaceBook(PlaceStore("t", persist=False), source)
    return adapter, VerbContext(transport=Recording(adapter), manifest=manifest, places=book)


def _tries_noted(monkeypatch: pytest.MonkeyPatch) -> list[dict[Any, int]]:
    """The count of tries against each cell after every walk that did not get there."""
    noted: list[dict[Any, int]] = []
    missed_once = explore_module._Explore.missed_once

    def counted(self: Any, *args: Any) -> None:
        missed_once(self, *args)
        noted.append(dict(self.tries))

    monkeypatch.setattr(explore_module._Explore, "missed_once", counted)
    return noted


async def test_floor_behind_glass_is_walked_at_twice_and_left_for_the_rest_of_the_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A pane across the hall that the map sees through and the body walks into: every walk at the
    floor beyond it ends lost. Each spot there is walked at twice and then left, and the call ends
    saying so, rather than walking into the pane until its minutes run out. The next call tries
    again: a pane can be a door somebody opened since."""
    hall = load_room("hall")
    tries = _tries_noted(monkeypatch)
    adapter, ctx = await _behind_glass(
        monkeypatch, walls=(Wall(-0.9, -hall.half_h, -0.9, hall.half_h),)
    )
    try:
        minutes = 2.0
        result = await _explore(ctx, minutes=minutes)
        assert result.ok and result.data["exit"] == "done", result.summary
        assert result.data["seconds"] < minutes * 60
        assert "could not get to" in result.summary and result.data["missed"] > 0
        assert result.data["given_up"] == 0, "floor it never reached is not given up for good"
        assert tries and max(tries[-1].values()) <= TRIES, tries[-1]
        first = len(tries)
        again = await _explore(ctx, minutes=minutes)
        assert again.data["exit"] == "done", again.summary
        assert len(tries) > first, "the next call tried nothing again"
    finally:
        await adapter.close()


async def test_a_walk_into_a_small_pane_leaves_the_rest_of_its_stretch_to_walk_to(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A block of glass beside the hall's first box, on the way to the stretch of frontier that
    spans the hall: the walks into it count against the floor within a body's width of where they
    went, not the whole stretch, so the hall beyond is still walked to and seen as it is without
    the block."""
    adapter, _body, ctx = await _duck("hall")
    try:
        clean = await _explore(ctx)
    finally:
        await adapter.close()
    tries = _tries_noted(monkeypatch)
    adapter, ctx = await _behind_glass(monkeypatch, boxes=(Box(-0.17, 0.33, 0.1, 0.1),))
    try:
        result = await _explore(ctx)
        assert tries, "it never walked into the block"
        assert result.ok and result.data["exit"] == "done", result.summary
        assert result.data["seen_m2"] >= clean.data["seen_m2"], (result.data, clean.data)
    finally:
        await adapter.close()


async def test_a_spot_it_could_not_get_to_is_tried_again_after_the_others(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A person standing in the way of the first stretch it walks at: the next walk goes to another
    stretch, so the person has that walk's time to move, and nothing is left at the end."""
    navigate = explore_module.navigate
    goals: list[tuple[float, float]] = []

    async def held_up_once(*args: Any, **kw: Any) -> Walked:
        if kw["label"] == "the edge of what its map has seen":
            goals.append(args[2])
            if len(goals) == 1:
                return Walked("stalled", "it got no nearer, as a person standing there would")
        return await navigate(*args, **kw)

    monkeypatch.setattr(explore_module, "navigate", held_up_once)
    adapter, _body, ctx = await _duck("two-rooms")
    try:
        result = await _explore(ctx)
        assert len(goals) > 1 and math.dist(goals[1], goals[0]) > 2 * DUCK_R, goals[:2]
        assert result.ok and result.data["exit"] == "done", result.summary
        assert result.data["missed"] == 0
    finally:
        await adapter.close()


async def test_done_names_no_floor_it_could_not_get_to_that_its_map_has_seen_since(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A stretch walked at twice and not got to, which a look from the next stop sees all of:
    nothing is left that it could not get to, and its words and count say so."""
    navigate = explore_module.navigate
    goals: list[tuple[float, float]] = []

    async def stalls_there(*args: Any, **kw: Any) -> Walked:
        goal = args[2]
        if kw["label"] == "the edge of what its map has seen" and (
            not goals or math.dist(goal, goals[0]) < DUCK_R
        ):
            goals.append(goal)
            if len(goals) <= TRIES:
                return Walked("stalled", "it got no nearer, as a person standing there would")
        return await navigate(*args, **kw)

    monkeypatch.setattr(explore_module, "navigate", stalls_there)
    # walked at again at once, as no other stretch is tried first
    monkeypatch.setattr(explore_module._Explore, "tried", lambda _self, _grid, _target: 0)
    adapter, _body, ctx = await _duck("two-rooms")
    try:
        result = await _explore(ctx)
        assert len(goals) >= TRIES, goals
        assert result.ok and result.data["exit"] == "done", result.summary
        assert "could not get to" not in result.summary and result.data["missed"] == 0
    finally:
        await adapter.close()


async def test_a_walk_that_fails_once_gives_no_floor_up(monkeypatch: pytest.MonkeyPatch) -> None:
    """A walk its map could not be read for, once, as a request to mapd times out: the spot is
    walked at again, here at once since the hall has no other stretch to try first, and nothing
    is given up, so the call sees what one with no hiccup sees."""
    adapter, _body, ctx = await _duck("hall")
    try:
        clean = await _explore(ctx)
    finally:
        await adapter.close()
    navigate = explore_module.navigate
    goals: list[tuple[float, float]] = []

    async def once(*args: Any, **kw: Any) -> Walked:
        if kw["label"] == "the edge of what its map has seen":
            goals.append(args[2])
            if len(goals) == 1:
                return Walked("refused", "its map could not be read: mapd did not answer in time")
        return await navigate(*args, **kw)

    monkeypatch.setattr(explore_module, "navigate", once)
    adapter, _body, ctx = await _duck("hall")
    try:
        hiccup = await _explore(ctx)
        assert len(goals) > 1 and math.dist(goals[1], goals[0]) <= 2 * DUCK_R, goals[:2]
        assert hiccup.ok and hiccup.data["exit"] == "done", hiccup.summary
        assert (hiccup.data["given_up"], hiccup.data["missed"]) == (clean.data["given_up"], 0)
        assert hiccup.data["seen_m2"] == clean.data["seen_m2"]
    finally:
        await adapter.close()


# ── where it is offered ─────────────────────────────────────────────────────────────────


async def test_a_duck_whose_reading_keeps_no_map_is_not_offered_explore_and_says_why() -> None:
    """Beside its body, where the pilot judges a task, and in the refusal of a task that needs it,
    which comes before anything connects."""
    adapter = make_adapter("microduck:mock")
    manifest = await adapter.connect()
    try:
        assert not {"explore", "find"} & set(manifest.verb_names())
        assert manifest.not_offered() == {"explore": NO_MAP_WHY, "find": NO_MAP_WHY}
        assert "odometry" in NO_MAP_WHY and "no map" in NO_MAP_WHY
        lines = body_lines(manifest)
        assert f"- explore, find: {NO_MAP_WHY}" in lines, lines  # one reason, said once
        assert f"Not offered here: explore and find, {NO_MAP_WHY}." in body_summary(manifest)
    finally:
        await adapter.close()
    needs = parse_duck_text(
        "---\nduck: 1\nname: t\ndescription: d\nrequires: [explore]\n"
        "verbs:\n  allow: [explore, stop]\nsuccess: [x]\n---\n# T\nx\n"
    )
    allows = parse_duck_text(
        "---\nduck: 1\nname: t\ndescription: d\nrequires: [stop]\n"
        "verbs:\n  allow: [explore, stop]\nsuccess: [x]\n---\n# T\nx\n"
    )
    for backend, why in (("mock", NO_MAP_WHY), ("jsonrpc", NO_SIZE_WHY)):
        static = describe(backend)
        assert [p.message for p in validate_duck(needs, [static])] == [
            f"requires explore, but microduck (microduck) does not provide it ({why})"
        ], backend
        assert [p.message for p in validate_duck(allows, [static])] == [
            f"explore is not provided by microduck ({why})"
        ], backend


async def test_a_real_duck_is_not_offered_explore_with_mapd_or_without_and_says_why() -> None:
    """A way round a room keeps the body's radius from everything solid, and mapd's map comes
    with none while the real duck's is published nowhere: before anything connects, and after,
    with mapd answering beside robotd or not, the duck says why it does not explore."""
    static = describe("jsonrpc")
    assert not {"explore", "find"} & set(static.verb_names())
    assert static.not_offered() == {"explore": NO_SIZE_WHY, "find": NO_SIZE_WHY}
    robotd, mapd = FakeRobotd(), FakeMapd()
    await robotd.start()
    await mapd.start()
    try:
        for beside, why in ((mapd.address, NO_SIZE_WHY), (None, NO_MAP_WHY)):
            transport = JsonRpcUnixTransport(f"tcp://127.0.0.1:{robotd.port}", map_address=beside)
            adapter = MicroduckAdapter(transport)
            try:
                manifest = await adapter.connect()
                source = spatial_of(adapter)
                assert source is not None and isinstance(source, MapdSource) == (beside is not None)
                assert not source.footprint_m
                assert not {"explore", "find"} & set(manifest.verb_names())
                assert manifest.not_offered() == {"explore": why, "find": why}
            finally:
                await adapter.close()
    finally:
        await mapd.stop()
        await robotd.stop()


async def test_a_reading_with_no_map_is_refused_before_anything_moves() -> None:
    adapter = make_adapter("microduck:mock")
    manifest = await adapter.connect()
    try:
        body = Recording(adapter)
        source = spatial_of(adapter)
        assert source is not None and not source.has_grid
        book = PlaceBook(PlaceStore("t", persist=False), source)
        ctx = VerbContext(transport=body, manifest=manifest, places=book)
        result = await _explore(ctx)
        assert not result.ok and "keeps no map to explore by" in result.summary
        assert body.sent == []
    finally:
        await adapter.close()


def test_its_clock_fits_a_verb_and_its_minutes_are_moss_s() -> None:
    cap = next(b.le for b in VerbSpec.model_fields["timeout_s"].metadata if hasattr(b, "le"))
    assert cap >= EXPLORE_TIMEOUT_S
    assert EXPLORE_TIMEOUT_S > EXPLORE_MAX_MIN * 60
    assert EXPLORE_MAX_MIN == moss_verbs.EXPLORE_MAX_MIN
    assert ExploreParams.model_fields["minutes"].metadata == (
        moss_verbs.MinutesParams.model_fields["minutes"].metadata
    )
    assert MICRODUCK_VERBS["explore"].timeout_s == EXPLORE_TIMEOUT_S
    assert MICRODUCK_VERBS["explore"].safety_class == "confirm"
