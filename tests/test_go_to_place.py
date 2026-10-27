"""Walking to a place (`go_to_place`, `quackd.verbs.navigate`): through a room on the simulated
map, round what it has seen, and stopped, saying why, on everything that should stop it. Every
walk runs on a simulator's own clock, so a test of a twenty-second walk takes a fraction of one."""

from __future__ import annotations

import asyncio
import math
from typing import Any

import numpy as np
import pytest

from quackd.adapters.factory import make_adapter
from quackd.safety import Aborted
from quackd.space.grid import FREE, OCCUPIED, OccupancyGrid
from quackd.space.places import Place, PlaceBook, PlaceStore
from quackd.space.pose import Fix, FrameRef, Pose2D
from quackd.space.room import Box, Room, Wall, load_room
from quackd.space.simmap import BUMPED, SimulatedMap
from quackd.space.source import SpatialSourceBase
from quackd.transport.base import Ack, DuckState, Intent, TransportError, spatial_of
from quackd.transport.mock import MockTransport
from quackd.transport.sim2d import Sim2DTransport
from quackd.verbs.core import speed_limits
from quackd.verbs.navigate import BLIND_S, SHARE, Gait, steer
from quackd.verbs.places import PLACE_VERBS, GoToPlaceParams
from quackd.verbs.registry import STOP_ENDED, STOPPED, VerbContext, VerbResult
from quackd_microduck import MicroduckAdapter


class Recording:
    """A body with every twist it is sent and every stop written down, on its own clock."""

    def __init__(self, inner: Any) -> None:
        self.inner = inner
        self.sent: list[tuple[float, str, tuple[float, float, float]]] = []
        self.refuse_after: int | None = None
        self.extras: dict[str, Any] = {}
        self.round_trip = 0
        """How many turns of the loop a stop takes to go out, as a link's round trip does."""

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)

    async def send_intent(self, intent: Intent) -> Ack:
        if intent.kind == "move":
            p = intent.params
            twist = (float(p.get("vx", 0.0)), float(p.get("vy", 0.0)), float(p.get("wz", 0.0)))
            moves = sum(1 for s in self.sent if s[1] == "move")
            if self.refuse_after is not None and moves >= self.refuse_after:
                return Ack(accepted=False, reason="somebody is holding the duck")
            self.sent.append((self.inner.now(), "move", twist))
        return await self.inner.send_intent(intent)

    async def stop(self) -> None:
        self.sent.append((self.inner.now(), "stop", (0.0, 0.0, 0.0)))
        for _ in range(self.round_trip):
            await asyncio.sleep(0)
        await self.inner.stop()

    async def get_state(self) -> DuckState:
        state = await self.inner.get_state()
        if self.extras:
            state.extras = {**state.extras, **self.extras}
        return state

    def twists(self, since: float = -math.inf) -> list[tuple[float, float, float]]:
        return [
            twist for t, kind, twist in self.sent if kind == "move" and t >= since and any(twist)
        ]


async def _duck(
    room: str | Room = "two-rooms", seed: int = 3
) -> tuple[Any, Recording, VerbContext]:
    """A duck in a room, connected and looked about from where it starts, and the context a verb
    of its runs in, on a body that writes down what it is sent."""
    if isinstance(room, Room):
        from quackd_microduck.transports.mapd import sim_map_params

        adapter: Any = MicroduckAdapter(
            Sim2DTransport(seed=seed, room=room, map_params=sim_map_params(0.08))
        )
    else:
        adapter = make_adapter("microduck:sim2d", seed=seed, room=room)
    manifest = await adapter.connect()
    await adapter.sleep(1.0)  # a first stop, looked from
    body = Recording(adapter)
    source = spatial_of(adapter)
    assert source is not None
    book = PlaceBook(PlaceStore("t", persist=False), source)
    return adapter, body, VerbContext(transport=body, manifest=manifest, places=book)


def _mark(ctx: VerbContext, name: str, x: float, y: float, yaw: float = 0.0) -> None:
    """A place at (x, y) in the frame the robot is in, as if it had stood there."""
    assert ctx.places is not None
    frame = ctx.places.frame()
    assert frame is not None
    ctx.places.store.mark(Place(name, x, y, yaw, frame.id, frame.kind, 0.0), reserved_ok=True)


async def _go(ctx: VerbContext, name: str, **kw: Any) -> VerbResult:
    return await PLACE_VERBS["go_to_place"].execute(ctx, GoToPlaceParams(name=name, **kw))


def _world(adapter: Any) -> Any:
    return adapter.transport.world


def _map(adapter: Any) -> SimulatedMap:
    source = spatial_of(adapter)
    assert isinstance(source, SimulatedMap)
    return source


# ── it gets there ───────────────────────────────────────────────────────────────────────


async def test_it_walks_across_a_room_to_a_place_and_faces_the_way_it_faced_there() -> None:
    adapter, _body, ctx = await _duck("arena")
    try:
        _mark(ctx, "corner", 0.5, 0.5, 1.0)
        result = await _go(ctx, "corner")
        assert result.ok, result.summary
        assert "by its map" in result.summary
        duck = _world(adapter).ducks[0]
        gait = Gait.of(ctx, _map(adapter), {})
        assert math.dist((duck.x, duck.y), (0.5, 0.5)) <= gait.tol_xy + 1e-9
        assert abs(math.remainder(duck.theta - 1.0, math.tau)) <= gait.tol_yaw + 1e-9
        assert duck.bumps == 0
    finally:
        await adapter.close()


async def test_it_walks_through_the_doorway_to_a_place_in_the_other_room() -> None:
    """The other room is unseen from where the duck starts, facing away from it: the walk plans
    across what it has not seen, stops where its map runs out to look, and plans again on what
    it saw."""
    two = load_room("two-rooms")
    facing_away = Room(
        "two-rooms", two.half_w, two.half_h, two.walls, two.boxes, start=(-1.0, 0.0, math.pi)
    )
    adapter, _body, ctx = await _duck(facing_away)
    try:
        _mark(ctx, "far", 0.8, -0.5)
        result = await _go(ctx, "far", face=False)
        assert result.ok, result.summary
        assert result.data["replans"] >= 1 and result.data["stops"] >= 2
        duck = _world(adapter).ducks[0]
        assert duck.x > 0.0 and duck.bumps == 0, "it walked through a wall"
    finally:
        await adapter.close()


async def test_a_box_its_first_look_missed_is_walked_round_once_a_stop_shows_it() -> None:
    room = Room("behind", 1.5, 1.0, boxes=(Box(0.2, 0.0, 0.3, 0.8),), start=(-1.0, 0.0, math.pi))
    adapter, _body, ctx = await _duck(room)
    try:
        assert not (_map(adapter).grid_now().cells == OCCUPIED)[:, 30:].any(), "box seen already"
        _mark(ctx, "past", 1.0, 0.0)
        result = await _go(ctx, "past", face=False)
        assert result.ok, result.summary
        assert result.data["replans"] >= 1
        assert _world(adapter).ducks[0].bumps == 0, "it walked into the box"
    finally:
        await adapter.close()


# ── it stops, and says why ──────────────────────────────────────────────────────────────


async def test_lost_mid_walk_it_stops_and_says_so_and_sends_nothing_after() -> None:
    adapter, body, ctx = await _duck("two-rooms")
    try:
        _mark(ctx, "far", 0.8, -0.5)
        source = _map(adapter)
        lost_at: list[float] = []

        def bump(world: Any) -> None:
            if not lost_at and body.twists():
                lost_at.append(world.t)
                source.lose(BUMPED)

        adapter.transport.add_tick_hook(bump)
        result = await _go(ctx, "far")
        assert not result.ok and result.data["outcome"] == "lost", result.summary
        assert "walked into something" in result.summary
        assert body.twists(since=lost_at[0] + 1e-9) == [], "a twist after it was lost"
        assert body.sent[-1][1] == "stop"
    finally:
        await adapter.close()


async def test_a_map_wiped_under_the_walk_stops_it_for_that_reason() -> None:
    adapter, body, ctx = await _duck("two-rooms")
    try:
        _mark(ctx, "far", 0.8, -0.5)
        source = _map(adapter)
        wiped_at: list[float] = []

        def wipe(world: Any) -> None:
            if not wiped_at and body.twists():
                wiped_at.append(world.t)
                source.wipe_now()

        adapter.transport.add_tick_hook(wipe)
        result = await _go(ctx, "far")
        assert result.data["outcome"] == "frame" and "another frame" in result.summary
        assert body.twists(since=wiped_at[0] + 1e-9) == []
        assert body.sent[-1][1] == "stop"
    finally:
        await adapter.close()


async def test_a_stop_from_beside_the_walk_sends_no_twist_after_it() -> None:
    """The stop lands on the clock, mid-stride, not when the walk next asks: it is the next twist
    that has to be the one not sent."""
    adapter, body, ctx = await _duck("two-rooms")
    try:
        _mark(ctx, "far", 0.8, -0.5)
        told: list[float] = []

        def brake(world: Any) -> None:
            if not told and len(body.twists()) >= 5:
                told.append(world.t)

        adapter.transport.add_tick_hook(brake)
        ctx.interrupted = lambda: STOPPED if told else None
        result = await _go(ctx, "far")
        assert not result.ok and result.data.get("ended_by") == STOP_ENDED, result.summary
        assert body.twists(since=told[0]) == []
        assert body.sent[-1][1] == "stop"
    finally:
        await adapter.close()


async def test_the_battery_rule_read_at_a_stop_ends_the_walk_stopped() -> None:
    adapter, body, ctx = await _duck("two-rooms")
    try:
        _mark(ctx, "far", 0.8, -0.5)

        async def battery(_state: DuckState) -> None:
            raise Aborted("battery below the duck's own rule")

        ctx.abort_when = battery
        with pytest.raises(Aborted):
            await _go(ctx, "far")
        assert body.twists(), "it never walked, so no stop was reached"
        assert body.sent[-1][1] == "stop"
    finally:
        await adapter.close()


async def test_a_twist_the_body_refuses_stops_the_walk_and_says_why() -> None:
    adapter, body, ctx = await _duck("two-rooms")
    try:
        _mark(ctx, "far", 0.8, -0.5)
        body.refuse_after = 3
        result = await _go(ctx, "far")
        assert not result.ok and "somebody is holding the duck" in result.summary
        assert result.data["move_refused"] == "somebody is holding the duck"
        assert body.sent[-1][1] == "stop"
    finally:
        await adapter.close()


async def test_its_own_clock_ends_it_stopped() -> None:
    """A place behind it: enough time by the way's length alone, and not for the turn too."""
    adapter, body, ctx = await _duck("two-rooms")
    try:
        _mark(ctx, "behind", -1.4, 0.0)
        result = await _go(ctx, "behind", timeout_s=4.0)
        assert not result.ok and result.data["outcome"] == "timeout", result.summary
        assert body.sent[-1][1] == "stop"
    finally:
        await adapter.close()


# ── refused before anything moves ───────────────────────────────────────────────────────


async def test_an_orphaned_place_is_refused_before_anything_moves() -> None:
    adapter, body, ctx = await _duck("two-rooms")
    try:
        assert ctx.places is not None
        ctx.places.store.mark(Place("old", 0.0, 0.0, 0.0, "map:another", "map", 0.0))
        result = await _go(ctx, "old")
        assert not result.ok and "orphaned" in result.summary and "mark it again" in result.summary
        assert body.sent == []
    finally:
        await adapter.close()


async def test_a_robot_that_does_not_know_where_it_is_is_told_how_to_find_out() -> None:
    adapter, body, ctx = await _duck("two-rooms")
    try:
        _mark(ctx, "far", 0.8, -0.5)
        _map(adapter).lose(BUMPED)
        result = await _go(ctx, "far")
        assert not result.ok and result.data["outcome"] == "lost"
        assert "walk a little and stop" in result.summary
        assert body.twists() == []
    finally:
        await adapter.close()


async def test_a_way_longer_than_its_time_is_refused_before_anything_moves() -> None:
    adapter, body, ctx = await _duck("two-rooms")
    try:
        _mark(ctx, "far", 0.8, -0.5)
        result = await _go(ctx, "far", timeout_s=2.0)
        assert not result.ok and "longer than the 2 s it was given" in result.summary
        assert body.twists() == []
    finally:
        await adapter.close()


class _Still(SpatialSourceBase):
    """A reading that stays where it is, whatever the body does, with a grid a test hands it, and
    a clock that moves on once a period when it is given one."""

    def __init__(
        self, transport: Any, grid: OccupancyGrid | None = None, period: float | None = None
    ) -> None:
        self._transport = transport
        self._grid = grid
        self._period = period

    def fix(self) -> Fix:
        frame = FrameRef("odometry", "odom:still", persistent=False)
        t = self._transport.now()
        if self._period is not None:
            t = math.floor(t / self._period) * self._period
        return Fix(Pose2D(0.0, 0.0, 0.0), "odometry", frame, t=t)

    @property
    def period_s(self) -> float | None:
        return self._period

    @property
    def footprint_m(self) -> float | None:
        return 0.08 if self._grid is not None else None

    @property
    def has_grid(self) -> bool:
        return self._grid is not None

    async def grid(self, res_m: float | None = None) -> OccupancyGrid | None:
        return self._grid


async def _still_duck(
    grid: OccupancyGrid | None = None, period: float | None = None
) -> tuple[MockTransport, VerbContext]:
    mock = MockTransport()
    adapter = MicroduckAdapter(mock)
    manifest = await adapter.connect()
    source = _Still(mock, grid, period)
    book = PlaceBook(PlaceStore("t", persist=False), source)
    return mock, VerbContext(transport=mock, manifest=manifest, places=book)


async def test_a_place_inside_something_its_map_says_is_solid_is_refused_before_moving() -> None:
    cells = np.full((40, 40), FREE, dtype=np.uint8)
    cells[15:25, 25:35] = OCCUPIED  # half a metre of solid round where the place is
    grid = OccupancyGrid(0.05, (-1.0, -1.0), cells)
    mock, ctx = await _still_duck(grid)
    _mark(ctx, "inside", 0.5, 0.0)
    result = await _go(ctx, "inside")
    assert not result.ok and "is where its map has something solid" in result.summary
    assert "that a body 0.16 m across fits on" in result.summary
    assert [i for i in mock.intents if i.kind == "move"] == []


async def test_a_body_pushing_at_something_its_reading_does_not_show_stops_for_no_headway() -> None:
    mock, ctx = await _still_duck()
    _mark(ctx, "ahead", 1.0, 0.0)
    result = await _go(ctx, "ahead", face=False)
    assert not result.ok and result.data["outcome"] == "stalled", result.summary
    assert "no headway" in result.summary
    assert mock.stops >= 1


# ── its limits ──────────────────────────────────────────────────────────────────────────


async def test_no_twist_ever_passes_the_bodys_clamps_even_with_a_gait_floor_above_them() -> None:
    adapter, body, ctx = await _duck("two-rooms")
    try:
        _mark(ctx, "far", 0.8, -0.5, 2.0)
        body.extras = {"gait_floor": {"vx": 0.5, "wz": 2.0}, "achieved_fraction": 1.0}
        await _go(ctx, "far")
        max_vx, _max_vy, max_wz = speed_limits(ctx.manifest)
        assert body.twists(), "it never walked"
        for vx, vy, wz in body.twists():
            assert abs(vx) <= max_vx + 1e-9 and vy == 0.0 and abs(wz) <= max_wz + 1e-9
    finally:
        await adapter.close()


class _Odometry(SpatialSourceBase):
    """Perfect odometry over the cartoon's duck, and no map: a body that knows where it is and
    nothing of what is around it."""

    def __init__(self, transport: Sim2DTransport) -> None:
        self._transport = transport
        self.epoch = 0

    def fix(self) -> Fix:
        world = self._transport.world
        duck = world.ducks[0]
        frame = FrameRef("odometry", "odom:test", persistent=False, epoch=self.epoch)
        return Fix(Pose2D(duck.x, duck.y, duck.theta), "odometry", frame, t=world.t)


async def test_with_no_map_it_walks_no_further_than_one_move_could_and_says_how_far_is_left() -> (
    None
):
    room = Room("long", 3.0, 0.5, start=(-2.8, 0.0, 0.0))
    transport = Sim2DTransport(seed=3, room=room)
    transport.spatial = _Odometry(transport)  # type: ignore[assignment]
    adapter = MicroduckAdapter(transport)
    manifest = await adapter.connect()
    try:
        body = Recording(adapter)
        source = spatial_of(adapter)
        assert source is not None
        book = PlaceBook(PlaceStore("t", persist=False), source)
        ctx = VerbContext(transport=body, manifest=manifest, places=book)
        _mark(ctx, "far end", 2.5, 0.0)
        result = await _go(ctx, "far end", timeout_s=120.0)
        cap = BLIND_S * speed_limits(manifest)[0]
        assert not result.ok and result.data["outcome"] == "capped", result.summary
        assert result.data["walked_m"] == pytest.approx(cap, abs=0.1)
        assert result.data["left_m"] == pytest.approx(5.3 - cap, abs=0.15)
        assert f"{result.data['walked_m']:.1f} m where its map has seen nothing" in result.summary
        assert body.sent[-1][1] == "stop"
    finally:
        await adapter.close()


def test_the_walks_numbers_come_from_the_body_and_its_source() -> None:
    """None of them is a number of its own: the share of the clamp a `move` walks at, raised to
    a published gait floor, covered at a published fraction, and near enough by the footprint or
    by what a reading's period lets it walk between two."""

    class Source(SpatialSourceBase):
        def fix(self) -> Fix:
            raise AssertionError

        @property
        def period_s(self) -> float | None:
            return 0.25

        @property
        def footprint_m(self) -> float | None:
            return 0.08

    from quackd_microduck import microduck_manifest

    ctx = VerbContext(transport=MockTransport(), manifest=microduck_manifest("sim2d"))
    max_vx, _max_vy, max_wz = speed_limits(ctx.manifest)
    plain = Gait.of(ctx, Source(), {})
    assert plain.v == pytest.approx(SHARE * max_vx) and plain.v_exp == plain.v
    assert plain.w == pytest.approx(min(1.0, max_wz))
    assert plain.tol_xy == pytest.approx(max(0.08, plain.v * (0.25 + 0.1)))
    assert plain.blind_m == pytest.approx(BLIND_S * max_vx)
    from quackd_microduck.sim3d.gait import ACHIEVED_FRACTION, GAIT_FLOOR_VX, GAIT_FLOOR_WZ

    floored = Gait.of(
        ctx,
        Source(),
        {
            "gait_floor": {"vx": GAIT_FLOOR_VX, "wz": GAIT_FLOOR_WZ},
            "achieved_fraction": ACHIEVED_FRACTION,
        },
    )
    assert floored.v == pytest.approx(min(GAIT_FLOOR_VX, max_vx))
    assert floored.w == pytest.approx(min(GAIT_FLOOR_WZ, max_wz)), "never past the turn clamp"
    assert floored.v_exp == pytest.approx(floored.v * ACHIEVED_FRACTION)
    # how closely it follows its way, and a heading near enough, from what it covers
    assert floored.track_m == pytest.approx(floored.v_exp * (0.25 + 0.1))
    assert floored.tol_yaw == pytest.approx(floored.w_exp * (0.25 + 0.1))


class _Tampered(SpatialSourceBase):
    """The simulated map, read through a hand that freezes its clock or moves its pose once a
    test says so: a stream that stopped, or a reading that jumped."""

    def __init__(self, inner: SimulatedMap) -> None:
        self.inner = inner
        self.frozen_at: float | None = None
        self.shift = 0.0
        self._handed: float | None = None

    def freeze(self) -> None:
        """Stop the stream where it stands: every reading after this one carries the time of the
        last it handed out, as a stream that stopped sending looks from the other end."""
        self.frozen_at = self._handed if self._handed is not None else self.inner.fix().t

    def fix(self) -> Fix:
        fix = self.inner.fix()
        self._handed = self.frozen_at if self.frozen_at is not None else fix.t
        if self.frozen_at is not None:
            fix = Fix(
                fix.pose,
                fix.kind,
                fix.frame,
                lost=fix.lost,
                t=self.frozen_at,
                keyframes=fix.keyframes,
                collecting=fix.collecting,
            )
        if self.shift and fix.pose is not None:
            p = fix.pose
            fix = Fix(
                Pose2D(p.x + self.shift, p.y, p.yaw),
                fix.kind,
                fix.frame,
                lost=fix.lost,
                t=fix.t,
                keyframes=fix.keyframes,
                collecting=fix.collecting,
            )
        return fix

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)

    @property
    def period_s(self) -> float | None:
        return self.inner.period_s

    @property
    def footprint_m(self) -> float | None:
        return self.inner.footprint_m

    @property
    def stop_every_m(self) -> float | None:
        return self.inner.stop_every_m

    @property
    def settle_timeout_s(self) -> float | None:
        return self.inner.settle_timeout_s

    @property
    def has_grid(self) -> bool:
        return True

    @property
    def view(self) -> tuple[float, float] | None:
        return self.inner.view

    async def grid(self, res_m: float | None = None) -> OccupancyGrid | None:
        return await self.inner.grid(res_m)


async def _tampered_duck(
    room: str | Room = "two-rooms",
) -> tuple[Any, Recording, VerbContext, _Tampered]:
    adapter, body, ctx = await _duck(room)
    source = _Tampered(_map(adapter))
    ctx.places = PlaceBook(PlaceStore("t", persist=False), source)
    return adapter, body, ctx, source


async def test_a_reading_that_stops_moving_on_holds_the_body_then_ends_the_walk() -> None:
    """Freshness is the reading's own clock moving on, never a message arriving: past a twist's
    hold the body is held still, and past the source's stale window the walk ends."""
    adapter, body, ctx, source = await _tampered_duck()
    try:
        _mark(ctx, "far", 0.8, -0.5)
        frozen: list[float] = []

        def freeze(world: Any) -> None:
            if not frozen and len(body.twists()) >= 5:
                frozen.append(world.t)
                source.freeze()

        adapter.transport.add_tick_hook(freeze)
        result = await _go(ctx, "far")
        assert result.data["outcome"] == "stale" and "has not moved on" in result.summary
        held_from = frozen[0] + Gait.of(ctx, source, {}).fresh_s + 0.11  # and the tick it read on
        assert body.twists(since=held_from) == [], "it walked on a reading that stopped"
        zeros = [t for t, kind, twist in body.sent if kind == "move" and t > held_from]
        assert zeros and all(
            not any(tw) for _t, kind, tw in body.sent if kind == "move" and _t > held_from
        )
        assert body.sent[-1][1] == "stop"
    finally:
        await adapter.close()


async def test_a_reading_that_jumps_while_it_walks_ends_the_walk() -> None:
    adapter, body, ctx, source = await _tampered_duck()
    try:
        _mark(ctx, "far", 0.8, -0.5)
        jumped: list[float] = []

        def jump(world: Any) -> None:
            if not jumped and len(body.twists()) >= 5:
                jumped.append(world.t)
                source.shift = 0.5

        adapter.transport.add_tick_hook(jump)
        result = await _go(ctx, "far")
        assert result.data["outcome"] == "jumped" and result.data["jump_m"] >= 0.5
        assert body.twists(since=jumped[0] + 0.11) == []
        assert body.sent[-1][1] == "stop"
    finally:
        await adapter.close()


async def test_a_walk_cancelled_from_outside_leaves_the_body_stopped() -> None:
    """The executor's own clock cancels a verb it has waited on too long: the walk stops the
    body on its way out, whatever it was sending."""
    import asyncio

    adapter, body, ctx = await _duck("two-rooms")
    try:
        _mark(ctx, "far", 0.8, -0.5)
        under_way = asyncio.Event()
        adapter.transport.add_tick_hook(
            lambda _world: under_way.set() if len(body.twists()) >= 5 else None
        )
        walking = asyncio.create_task(_go(ctx, "far"))
        await under_way.wait()
        walking.cancel()
        with pytest.raises(asyncio.CancelledError):
            await walking
        assert body.sent[-1][1] == "stop", "cancelled mid-walk with the last twist standing"
    finally:
        await adapter.close()


async def test_a_place_well_off_to_its_side_is_walked_to_on_an_arc_without_a_false_stall() -> None:
    """On a wide arc the straight distance to the place barely shrinks at first, so headway is
    measured along the arc it walks, which shrinks at its own speed."""
    room = Room("open", 1.5, 1.5, start=(0.0, 0.0, 0.0))
    transport = Sim2DTransport(seed=3, room=room)
    transport.spatial = _Odometry(transport)  # type: ignore[assignment]
    adapter = MicroduckAdapter(transport)
    manifest = await adapter.connect()
    try:
        source = spatial_of(adapter)
        assert source is not None
        ctx = VerbContext(
            transport=adapter,
            manifest=manifest,
            places=PlaceBook(PlaceStore("t", persist=False), source),
        )
        aside = math.radians(80)
        _mark(ctx, "aside", 0.5 * math.cos(aside), 0.5 * math.sin(aside))
        result = await _go(ctx, "aside", face=False)
        assert result.ok, result.summary
    finally:
        await adapter.close()


def _facing_away() -> Room:
    """two-rooms, the duck starting with its back to the doorway, so its first look sees only the
    near room and the walk to the far one makes stops on the way."""
    two = load_room("two-rooms")
    return Room(
        "two-rooms", two.half_w, two.half_h, two.walls, two.boxes, start=(-1.0, 0.0, math.pi)
    )


# ── the review's findings, each a test ──────────────────────────────────────────────────


async def test_a_sharp_turn_beside_a_box_its_map_has_seen_walks_round_it_not_into_it() -> None:
    """Pure pursuit to a corner well off its heading walks an arc that bulges up to half the way
    to it off the line the planner cleared, and took the duck into a box the map had seen. It
    turns in place first until the arc keeps within the room the planner left."""
    room = Room("boxed", 1.5, 1.0, boxes=(Box(0.0, 0.0, 0.3, 1.0),), start=(-1.0, 0.0, 0.0))
    adapter, _body, ctx = await _duck(room)
    try:
        _mark(ctx, "beyond", 1.0, 0.0)
        result = await _go(ctx, "beyond", face=False)
        assert result.ok, result.summary
        assert _world(adapter).ducks[0].bumps == 0, "it walked into a box its map had seen"
    finally:
        await adapter.close()


async def test_a_wall_its_map_has_not_seen_is_looked_at_before_the_body_walks_into_it() -> None:
    """The planner does not grow floor nobody has seen, so a way can run along a wall nobody has
    seen: the floor the body's front is about to cover is looked at first, not only the floor
    under its middle."""
    room = Room(
        "edge",
        1.5,
        1.0,
        walls=(Wall(0.0, -1.0, 0.0, 0.45),),
        start=(-0.4, -0.6, math.pi / 2),
    )
    adapter, _body, ctx = await _duck(room)
    try:
        _mark(ctx, "over there", 0.4, -0.6)
        result = await _go(ctx, "over there", face=False)
        assert result.ok, result.summary
        assert _world(adapter).ducks[0].bumps == 0, "it walked into the wall it had not seen"
    finally:
        await adapter.close()


async def test_arrival_is_judged_by_the_reading_after_its_last_look() -> None:
    """The look at the last stop may correct where the robot is by as much as the map believes,
    and it was said to have arrived 0.15 m off. It walks on to the place, or says it did not."""
    adapter, body, ctx, source = await _tampered_duck()
    try:
        _mark(ctx, "far", 0.8, -0.5)
        gait = Gait.of(ctx, source, {})
        corrected: list[float] = []

        def correct(world: Any) -> None:
            fix = source.inner.fix()
            near = (
                fix.pose is not None
                and math.dist((fix.pose.x, fix.pose.y), (0.8, -0.5)) < gait.tol_xy
            )
            # the last look: the walk's own stop, then the stop it looks at, two in a row
            last_look = len(body.sent) >= 2 and [k for _t, k, _tw in body.sent[-2:]] == ["stop"] * 2
            if not corrected and near and last_look:
                corrected.append(world.t)
                assert fix.pose is not None
                # a correction as large as the map believes one, away from the place
                source.shift = 0.10 if fix.pose.x >= 0.8 else -0.10

        adapter.transport.add_tick_hook(correct)
        # facing the way the place faces, so its last look waits on a keyframe the turn made due
        result = await _go(ctx, "far")
        assert corrected, "the walk never stood at the place to be corrected there"
        assert result.ok, result.summary
        assert result.data["error_m"] <= gait.tol_xy, "it called itself arrived further off"
    finally:
        await adapter.close()


async def test_a_reading_that_was_not_moving_on_when_it_began_sends_no_twist() -> None:
    adapter, body, ctx, source = await _tampered_duck()
    try:
        _mark(ctx, "far", 0.8, -0.5)
        source.freeze()  # stopped before the walk began
        result = await _go(ctx, "far")
        assert result.data["outcome"] == "stale", result.summary
        assert body.twists() == [], "it walked on a reading that had already stopped"
    finally:
        await adapter.close()


async def test_a_reading_that_stops_moving_on_while_it_stands_and_looks_sends_no_twist_after() -> (
    None
):
    adapter, body, ctx, source = await _tampered_duck(_facing_away())
    try:
        _mark(ctx, "far", 0.8, -0.5)
        frozen: list[float] = []

        def freeze(world: Any) -> None:
            standing = body.twists() and body.sent[-1][1] == "stop"
            if not frozen and standing:
                frozen.append(world.t)
                source.freeze()

        adapter.transport.add_tick_hook(freeze)
        result = await _go(ctx, "far", face=False)
        assert frozen, "the walk made no stop on the way"
        assert result.data["outcome"] == "stale", result.summary
        assert body.twists(since=frozen[0]) == [], "a stop made a stopped reading fresh again"
    finally:
        await adapter.close()


async def test_no_headway_is_seen_on_a_source_that_reads_slower_than_a_twists_hold() -> None:
    """Fresh for the source's own period and a tick: a source reading twice a second held the
    body still between readings and reset the stall clock each time, so a body pushing at
    nothing that moved walked until its own clock ran out."""
    _mock, ctx = await _still_duck(period=0.5)
    _mark(ctx, "ahead", 1.0, 0.0)
    result = await _go(ctx, "ahead", face=False, timeout_s=30.0)
    assert result.data["outcome"] == "stalled", result.summary


async def test_the_battery_rule_read_at_a_stop_ends_the_run_in_its_own_words() -> None:
    """Through the executor: the rule's reader stops the body and raises; a stop awaited on the
    way out let the run's abort cancel the walk, and the words became a cancelled verb's."""
    from quackd.duckfile.parser import parse_duck_text
    from quackd.safety import Executor
    from quackd.verbs.registry import registry_from_manifest

    adapter, body, ctx = await _duck(_facing_away())
    try:
        walked = body.twists

        async def battery() -> DuckState:
            state = await adapter.get_state()
            state.battery_percent = 10.0 if walked() else 40.0
            return state

        body.get_state = battery  # type: ignore[method-assign]
        duck = parse_duck_text(
            "---\nduck: 0\nname: t\ndescription: d\nverbs:\n  allow: [go_to_place]\n"
            "success: [x]\nabort_when: [Battery below 15%]\n---\n# Task\nx\n"
        )
        registry = registry_from_manifest(ctx.manifest, adapter)  # type: ignore[arg-type]
        assert ctx.places is not None
        executor = Executor(registry, body, contract=duck.frontmatter, places=ctx.places.store)
        body.round_trip = 3  # an abort set while a stop is on its way can cancel what sent it
        _mark(ctx, "far", 0.8, -0.5)
        with pytest.raises(Aborted, match="battery 10% below 15%; the verb stopped where it stood"):
            await executor.run_verb("go_to_place", {"name": "far", "face": False})
        assert body.twists(), "it never walked"
        assert body.sent[-1][1] == "stop"
    finally:
        await adapter.close()


async def test_a_stop_while_it_stands_and_looks_mid_walk_starts_no_new_leg() -> None:
    adapter, body, ctx = await _duck(_facing_away())
    try:
        _mark(ctx, "far", 0.8, -0.5)
        told: list[float] = []

        def interrupted() -> str | None:
            standing = body.twists() and body.sent[-1][1] == "stop"
            if not told and standing:
                told.append(body.inner.now())
            return STOPPED if told else None

        ctx.interrupted = interrupted
        result = await _go(ctx, "far", face=False)
        assert told, "the walk made no stop on the way"
        assert not result.ok and result.data.get("ended_by") == STOP_ENDED, result.summary
        assert body.twists(since=told[0]) == []
    finally:
        await adapter.close()


async def test_a_place_its_map_shows_walled_in_is_refused_before_anything_moves() -> None:
    cells = np.full((40, 40), FREE, dtype=np.uint8)
    cells[14:26, 24] = cells[14:26, 35] = OCCUPIED  # a closed box of solid round the place
    cells[14, 24:36] = cells[25, 24:36] = OCCUPIED
    mock, ctx = await _still_duck(OccupancyGrid(0.05, (-1.0, -1.0), cells))
    _mark(ctx, "boxed in", 0.5, 0.0)
    result = await _go(ctx, "boxed in")
    assert not result.ok and "has no way to 'boxed in'" in result.summary
    assert [i for i in mock.intents if i.kind == "move"] == []


async def test_a_robot_its_map_puts_inside_something_solid_is_refused_before_moving() -> None:
    cells = np.full((40, 40), FREE, dtype=np.uint8)
    cells[19:21, 19:21] = OCCUPIED  # under the robot, which stands at the origin
    mock, ctx = await _still_duck(OccupancyGrid(0.05, (-1.0, -1.0), cells))
    _mark(ctx, "away", 0.5, 0.0)
    result = await _go(ctx, "away")
    assert not result.ok and "stands inside something solid" in result.summary
    assert [i for i in mock.intents if i.kind == "move"] == []


async def test_a_map_that_cannot_be_read_is_said_and_nothing_moves() -> None:
    class Broken(_Still):
        async def grid(self, res_m: float | None = None) -> OccupancyGrid | None:
            raise TransportError("map.grid: no answer within 2s")

    mock, ctx = await _still_duck(OccupancyGrid(0.05, (-1.0, -1.0), np.zeros((4, 4), np.uint8)))
    assert ctx.places is not None
    ctx.places = PlaceBook(ctx.places.store, Broken(mock, ctx.places.source._grid))  # type: ignore[attr-defined]
    _mark(ctx, "away", 0.5, 0.0)
    result = await _go(ctx, "away")
    assert not result.ok and "its map could not be read: map.grid: no answer" in result.summary
    assert [i for i in mock.intents if i.kind == "move"] == []


async def test_odometry_that_begins_again_mid_walk_says_why_it_might_have() -> None:
    room = Room("open", 1.5, 1.5, start=(0.0, 0.0, 0.0))
    transport = Sim2DTransport(seed=3, room=room)
    source = _Odometry(transport)
    transport.spatial = source  # type: ignore[assignment]
    adapter = MicroduckAdapter(transport)
    manifest = await adapter.connect()
    try:
        ctx = VerbContext(
            transport=adapter,
            manifest=manifest,
            places=PlaceBook(PlaceStore("t", persist=False), source),
        )
        _mark(ctx, "ahead", 1.0, 0.0)
        adapter.transport.add_tick_hook(
            lambda world: setattr(source, "epoch", 1) if world.t > 2.0 else None
        )
        result = await _go(ctx, "ahead", face=False)
        assert result.data["outcome"] == "frame"
        assert "picked up, falls or goes limp" in result.summary
    finally:
        await adapter.close()


def test_it_turns_in_place_before_an_arc_would_bulge_past_the_room_its_way_was_given() -> None:
    """Pure pursuit to a point well off its heading walks an arc bulging up to half the way to it
    off the straight line the planner cleared, and took a duck into a box its map had seen."""

    class Source(SpatialSourceBase):
        def fix(self) -> Fix:
            raise AssertionError

        @property
        def period_s(self) -> float | None:
            return 0.25

        @property
        def footprint_m(self) -> float | None:
            return 0.08

    from quackd_microduck import microduck_manifest

    ctx = VerbContext(transport=MockTransport(), manifest=microduck_manifest("sim2d"))
    gait = Gait.of(ctx, Source(), {})
    spin, wz = steer(math.radians(80), 0.69, gait)
    assert spin and wz == pytest.approx(gait.w), "an arc that would bulge 0.29 m off its way"
    for bearing in np.linspace(-1.5, 1.5, 61):
        for d in (0.1, 0.3, 0.69, 1.5, 3.0):
            spin, wz = steer(float(bearing), d, gait)
            if not spin:
                assert d / 2 * math.tan(abs(bearing) / 2) <= gait.track_m + 1e-12
                assert abs(wz) <= gait.max_wz + 1e-12
    walked = steer(math.radians(3), 1.0, gait)
    assert not walked[0] and walked[1] == pytest.approx(2 * gait.v * math.sin(math.radians(3)))


async def test_the_way_is_planned_with_room_for_how_closely_the_walk_follows_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    radii: list[float] = []
    real = OccupancyGrid.plan

    def spy(self: OccupancyGrid, *args: Any, radius_m: float, **kw: Any) -> Any:
        radii.append(radius_m)
        return real(self, *args, radius_m=radius_m, **kw)

    monkeypatch.setattr(OccupancyGrid, "plan", spy)
    cells = np.full((40, 40), FREE, dtype=np.uint8)
    _mock, ctx = await _still_duck(OccupancyGrid(0.05, (-1.0, -1.0), cells))
    _mark(ctx, "away", 0.5, 0.0)
    assert ctx.places is not None
    await _go(ctx, "away", face=False)
    gait = Gait.of(ctx, ctx.places.source, {})
    assert radii and radii[0] == pytest.approx(0.08 + gait.track_m)


async def test_a_place_something_now_stands_on_is_walked_to_the_nearest_floor_beside_it() -> None:
    """A box put down on the place since it was marked. The walk aimed at the nearest floor and
    judged its arrival against the place, so it planned and stopped there without end, and the
    simulator's clock never moved; aimed at floor too near the box, it walked into it."""
    import asyncio

    room = Room("on it", 1.5, 1.0, boxes=(Box(0.5, 0.0, 0.04, 0.04),), start=(-1.0, 0.0, 0.0))
    adapter, _body, ctx = await _duck(room)
    try:
        _mark(ctx, "under the box", 0.5, 0.0)
        result = await asyncio.wait_for(_go(ctx, "under the box", face=False), 60)
        assert result.ok, result.summary
        assert "at the nearest floor to it the body fits on" in result.summary
        assert _world(adapter).ducks[0].bumps == 0, "it walked into the box"
    finally:
        await adapter.close()


async def test_a_place_in_a_rooms_corner_is_walked_to() -> None:
    """A tenth of a metre off both walls: in the band each is grown by, and left only by the
    corner's diagonal, which a rule of every step further from the walls left no way to."""
    adapter, _body, ctx = await _duck("two-rooms")
    try:
        _mark(ctx, "corner", -1.4, -0.9)
        result = await _go(ctx, "corner", face=False)
        assert result.ok, result.summary
        assert _world(adapter).ducks[0].bumps == 0
    finally:
        await adapter.close()


async def test_a_way_too_narrow_says_how_much_room_it_needed() -> None:
    cells = np.full((40, 40), FREE, dtype=np.uint8)
    cells[:, 25] = OCCUPIED  # a wall across the room at x = 0.25
    cells[17:24, 25] = FREE  # and a door in it 0.35 m wide
    grid = OccupancyGrid(0.05, (-1.0, -1.0), cells)
    mock, ctx = await _still_duck(grid)
    _mark(ctx, "beyond the door", 0.7, 0.0)
    result = await _go(ctx, "beyond the door")
    assert ctx.places is not None
    gait = Gait.of(ctx, ctx.places.source, {})
    kept = grid.kept_clear_m(0.08 + gait.track_m)
    assert not result.ok, result.summary
    assert f"keeps {kept:.2f} m from everything it has seen to be solid" in result.summary
    assert [i for i in mock.intents if i.kind == "move"] == []


class _Coasting(Sim2DTransport):
    """A body that goes on at its last twist for a moment after it is told to stop, as one
    walking does: a reading taken at once says where it was going, not where it came to rest."""

    COAST_S = 0.2

    def __init__(self, **kw: Any) -> None:
        super().__init__(**kw)
        self._stop_at: float | None = None
        self.add_tick_hook(self._coast)

    async def stop(self) -> None:
        if self._stop_at is None:
            self._stop_at = self.world.t + self.COAST_S
            vx, vy, wz = self.world.ducks[0].cmd
            self.world.set_velocity(vx, vy, wz)  # keep its deadman fed while it coasts

    async def send_intent(self, intent: Intent) -> Ack:
        if intent.kind == "move":
            self._stop_at = None
        return await super().send_intent(intent)

    def _coast(self, world: Any) -> None:
        if self._stop_at is not None and world.t >= self._stop_at:
            world.stop()
            self._stop_at = None


async def test_on_odometry_it_says_how_far_it_came_to_rest_from_the_place() -> None:
    room = Room("open", 1.5, 1.5, start=(0.0, 0.0, 0.0))
    transport = _Coasting(seed=3, room=room)
    source = _Odometry(transport)
    transport.spatial = source  # type: ignore[assignment]
    adapter = MicroduckAdapter(transport)
    manifest = await adapter.connect()
    try:
        ctx = VerbContext(
            transport=adapter,
            manifest=manifest,
            places=PlaceBook(PlaceStore("t", persist=False), source),
        )
        _mark(ctx, "ahead", 0.6, 0.0)
        result = await _go(ctx, "ahead", face=False)
        await adapter.sleep(1.0)  # well past any coast
        duck = transport.world.ducks[0]
        at_rest = math.dist((duck.x, duck.y), (0.6, 0.0))
        assert result.ok, result.summary
        assert result.data["error_m"] == pytest.approx(at_rest, abs=0.005), "read mid-coast"
    finally:
        await adapter.close()


async def test_a_place_too_near_something_for_the_body_is_walked_to_beside_it() -> None:
    """Its own cell free and a box 0.07 m off, nearer than the body's radius: walked to as it
    was, the duck met the box."""
    room = Room("near it", 1.5, 1.0, boxes=(Box(0.5, 0.0, 0.1, 0.4),), start=(-1.0, 0.0, 0.0))
    adapter, _body, ctx = await _duck(room)
    try:
        _mark(ctx, "by the box", 0.38, 0.0)
        result = await _go(ctx, "by the box", face=False)
        assert result.ok, result.summary
        assert "at the nearest floor to it the body fits on" in result.summary
        assert _world(adapter).ducks[0].bumps == 0, "it walked into the box"
    finally:
        await adapter.close()


class _SlowStart(Sim2DTransport):
    """A gait that takes a moment to start: a body standing still that is sent a twist stands a
    while longer before it steps, as a legged walk does from a stand."""

    def __init__(self, **kw: Any) -> None:
        super().__init__(**kw)
        self.start_s = 0.0
        self._asked_at: float | None = None

    async def send_intent(self, intent: Intent) -> Ack:
        if intent.kind == "move":
            if not any(intent.params.get(k) for k in ("vx", "vy", "wz")):
                self._asked_at = None
            else:
                if self._asked_at is None:
                    self._asked_at = self.world.t
                if self.world.t - self._asked_at < self.start_s:
                    intent = Intent.move()  # still standing, its deadman fed
        return await super().send_intent(intent)

    async def stop(self) -> None:
        self._asked_at = None
        await super().stop()


async def test_a_gait_that_takes_a_moment_to_start_is_not_taken_for_no_headway() -> None:
    """Judged by its rate alone, a walk from a stand was called stalled before its first step:
    the physics duck stands a second and more before it steps."""
    from quackd_microduck.transports.mapd import sim_map_params

    room = Room("open", 1.5, 1.5, start=(-0.5, 0.0, 0.0))
    transport = _SlowStart(seed=3, room=room, map_params=sim_map_params(0.08))
    adapter = MicroduckAdapter(transport)
    manifest = await adapter.connect()
    try:
        await adapter.sleep(1.0)
        source = spatial_of(adapter)
        assert source is not None
        book = PlaceBook(PlaceStore("t", persist=False), source)
        ctx = VerbContext(transport=adapter, manifest=manifest, places=book)
        gait = Gait.of(ctx, source, {})
        # standing as long as its rate alone gave it to come the place's tolerance nearer
        transport.start_s = 2 * gait.tol_xy / gait.v_exp
        _mark(ctx, "ahead", 0.4, 0.0)
        result = await _go(ctx, "ahead", face=False)
        assert result.ok, result.summary
    finally:
        await adapter.close()


class _NoTurnInPlace(Sim2DTransport):
    """A body that does not turn when asked to turn in place, as the physics duck asked for a spin
    from a stand may: the twist reaches it and it stays facing the way it faced. With `swings_to`,
    its trunk swings round to face that way while it is told to spin and back when it is not, so a
    reading taken turning says it faces the way and one at rest says it does not."""

    def __init__(self, swings_to: float | None = None, **kw: Any) -> None:
        super().__init__(**kw)
        self.swings_to = swings_to
        self._faced: float | None = None

    async def send_intent(self, intent: Intent) -> Ack:
        if intent.kind == "move":
            if not intent.params.get("vx") and intent.params.get("wz"):
                self._swing()
                intent = Intent.move()  # still fed, and still
            else:
                self._back()
        return await super().send_intent(intent)

    async def stop(self) -> None:
        self._back()
        await super().stop()

    def _swing(self) -> None:
        duck = self.world.ducks[0]
        if self.swings_to is not None and self._faced is None:
            self._faced, duck.theta = duck.theta, self.swings_to

    def _back(self) -> None:
        if self._faced is not None:
            self.world.ducks[0].theta, self._faced = self._faced, None


@pytest.mark.parametrize("sways", [True, False], ids=["its reading sways", "it turns no nearer"])
async def test_at_the_place_a_body_that_turns_no_nearer_its_heading_says_so_and_stops(
    sways: bool,
) -> None:
    """At the place, facing off the way it faced there, with a body that will not turn in place:
    a reading taken turning that said it faced the way had the walk look, plan and turn again
    until its clock ran out, seventy-six stops on the physics duck, and a turn that came no nearer
    ended a walk that had arrived as stalled."""
    from quackd_microduck.transports.mapd import sim_map_params

    room = Room("open", 1.5, 1.5, start=(-0.3, 0.0, 0.0))
    transport = _NoTurnInPlace(
        math.pi / 2 if sways else None, seed=3, room=room, map_params=sim_map_params(0.08)
    )
    adapter = MicroduckAdapter(transport)
    manifest = await adapter.connect()
    try:
        await adapter.sleep(1.0)
        source = spatial_of(adapter)
        assert source is not None
        book = PlaceBook(PlaceStore("t", persist=False), source)
        ctx = VerbContext(transport=adapter, manifest=manifest, places=book)
        _mark(ctx, "ahead", 0.3, 0.0, math.pi / 2)
        result = await _go(ctx, "ahead", timeout_s=60.0)
        duck = transport.world.ducks[0]
        off = abs(math.degrees(math.remainder(math.pi / 2 - duck.theta, math.tau)))
        assert result.ok, result.summary
        assert f"facing {off:.0f}° off the way it faced there, as near as it turned" in (
            result.summary
        )
        assert result.data["replans"] == 1
    finally:
        await adapter.close()


class _Ahead(_Tampered):
    """The simulated map read while the body walks says it is `metres` further on than it is, as
    a reading taken on a stride may; at rest, where it stands."""

    def __init__(self, inner: SimulatedMap, world: Any, metres: float) -> None:
        super().__init__(inner)
        self.world, self.metres = world, metres

    def fix(self) -> Fix:
        fix = super().fix()
        if self.world.ducks[0].cmd[0] <= 0 or fix.pose is None:
            return fix
        p = fix.pose
        return Fix(
            Pose2D(p.x + self.metres * math.cos(p.yaw), p.y + self.metres * math.sin(p.yaw), p.yaw),
            fix.kind,
            fix.frame,
            lost=fix.lost,
            t=fix.t,
            keyframes=fix.keyframes,
            collecting=fix.collecting,
        )


async def test_another_try_that_brings_it_hardly_any_nearer_ends_the_walk_where_it_is() -> None:
    """A reading taken walking that put the body further on than it came to rest: each leg ended
    near enough by it, the reading after the look said not, and the walk planned, stepped and
    stopped again, a step nearer each time, a look apart."""
    adapter, body, ctx = await _duck(Room("open", 1.5, 1.5, start=(-0.5, 0.0, 0.0)))
    try:
        assert ctx.places is not None
        gait = Gait.of(ctx, ctx.places.source, {})
        source = _Ahead(_map(adapter), _world(adapter), gait.tol_xy)
        ctx.places = PlaceBook(PlaceStore("t", persist=False), source)
        _mark(ctx, "ahead", 0.4, 0.0)
        result = await _go(ctx, "ahead", face=False, timeout_s=60.0)
        assert not result.ok and result.data["outcome"] == "stalled", result.summary
        assert f"where {gait.tol_xy:.2f} m is near enough, and another try brought it" in (
            result.summary
        )
        assert result.data["replans"] == 1
        assert result.data["error_m"] > gait.tol_xy
        assert body.sent[-1][1] == "stop"
    finally:
        await adapter.close()


class _SwayAround(SpatialSourceBase):
    """A body something holds where it stands, its heading reading swaying `sway` either side of
    `centre` from one reading to the next, as a legged trunk stepping in place does."""

    def __init__(self, transport: Any, every: float = 0.25) -> None:
        self._transport, self.every = transport, every
        self.centre = self.sway = 0.0

    @property
    def period_s(self) -> float | None:
        return self.every

    def fix(self) -> Fix:
        frame = FrameRef("odometry", "odom:sway", persistent=False)
        t = math.floor(self._transport.now() / self.every) * self.every
        side = self.sway if round(t / self.every) % 2 else -self.sway
        return Fix(Pose2D(0.0, 0.0, self.centre + side), "odometry", frame, t=t)


async def test_a_held_body_whose_heading_sways_across_a_turns_line_stops_for_no_headway() -> None:
    """The physics duck's numbers, held where it stands, its heading reading swaying across the
    line between turning in place and walking an arc to a place a metre off: each change began the
    no-headway clock again, and it pushed until its time ran out."""
    from quackd_microduck.sim3d.gait import ACHIEVED_FRACTION, GAIT_FLOOR_VX, GAIT_FLOOR_WZ

    mock = MockTransport()
    adapter = MicroduckAdapter(mock)
    manifest = await adapter.connect()
    body = Recording(mock)
    body.extras = {
        "gait_floor": {"vx": GAIT_FLOOR_VX, "wz": GAIT_FLOOR_WZ},
        "achieved_fraction": ACHIEVED_FRACTION,
    }
    source = _SwayAround(mock)
    ctx = VerbContext(
        transport=body, manifest=manifest, places=PlaceBook(PlaceStore("t", persist=False), source)
    )
    gait = Gait.of(ctx, source, body.extras)
    # the bearing at a metre past which an arc bulges more than it may, and a sway either side
    line = 2 * math.atan(2 * gait.track_m / 1.0)
    source.centre, source.sway = -line, line / 2
    _mark(ctx, "ahead", 1.0, 0.0)
    result = await _go(ctx, "ahead", face=False, timeout_s=30.0)
    assert result.data["outcome"] == "stalled", result.summary


class _Gappy(SpatialSourceBase):
    """A body something holds where it stands, read every `every` seconds, its stream silent for
    `gap` seconds of every `cycle`, as a link that stalls now and then is."""

    def __init__(self, transport: Any, every: float, gap: float, cycle: float) -> None:
        self._transport, self.every, self.gap, self.cycle = transport, every, gap, cycle

    @property
    def period_s(self) -> float | None:
        return self.every

    def fix(self) -> Fix:
        frame = FrameRef("odometry", "odom:gappy", persistent=False)
        now = self._transport.now()
        if now % self.cycle < self.gap:
            t = math.floor(now / self.cycle) * self.cycle
        else:
            t = math.floor(now / self.every) * self.every
        return Fix(Pose2D(0.0, 0.0, 0.0), "odometry", frame, t=t)


async def test_a_held_body_on_a_stream_that_falls_silent_now_and_then_stops_for_no_headway() -> (
    None
):
    """Every silence longer than a reading's freshness began the no-headway clock again, so a
    stream that fell silent once a window kept a body that something holds pushing until its time
    ran out. Silent for less than its stale window, so the walk does not end as stale."""
    mock = MockTransport()
    adapter = MicroduckAdapter(mock)
    manifest = await adapter.connect()
    source = _Gappy(mock, every=0.25, gap=0.0, cycle=1.0)
    ctx = VerbContext(
        transport=mock, manifest=manifest, places=PlaceBook(PlaceStore("t", persist=False), source)
    )
    gait = Gait.of(ctx, source, {})
    # silent past a reading's freshness, short of the stale window, and more than once a window
    source.gap = (gait.fresh_s + gait.stale_s) / 2
    source.cycle = gait.no_headway_s(gait.tol_xy, gait.v_exp) / 2
    assert gait.fresh_s < source.gap < gait.stale_s and source.gap < source.cycle
    _mark(ctx, "ahead", 1.0, 0.0)
    result = await _go(ctx, "ahead", face=False, timeout_s=30.0)
    assert result.data["outcome"] == "stalled", result.summary


async def test_another_try_short_of_the_floor_beside_a_place_names_that_floor() -> None:
    """A box on the place, and a reading taken walking that put the body further on than it came
    to rest: the words gave the distance to the floor beside the place and called it the place's."""
    room = Room("on it", 1.5, 1.0, boxes=(Box(0.5, 0.0, 0.04, 0.04),), start=(-1.0, 0.0, 0.0))
    adapter, _body, ctx = await _duck(room)
    try:
        assert ctx.places is not None
        gait = Gait.of(ctx, ctx.places.source, {})
        source = _Ahead(_map(adapter), _world(adapter), gait.tol_xy)
        ctx.places = PlaceBook(PlaceStore("t", persist=False), source)
        _mark(ctx, "under the box", 0.5, 0.0)
        result = await _go(ctx, "under the box", face=False, timeout_s=60.0)
        assert result.data["outcome"] == "stalled", result.summary
        assert "from the nearest floor to 'under the box' the body fits on, where" in (
            result.summary
        )
    finally:
        await adapter.close()


async def test_a_body_that_does_not_turn_in_place_toward_its_way_is_told_so() -> None:
    """A place behind a body that does not turn in place: the leg said something its map does
    not show was in its way."""
    from quackd_microduck.transports.mapd import sim_map_params

    room = Room("open", 1.5, 1.5, start=(0.3, 0.0, 0.0))
    transport = _NoTurnInPlace(None, seed=3, room=room, map_params=sim_map_params(0.08))
    adapter = MicroduckAdapter(transport)
    manifest = await adapter.connect()
    try:
        await adapter.sleep(1.0)
        source = spatial_of(adapter)
        assert source is not None
        book = PlaceBook(PlaceStore("t", persist=False), source)
        ctx = VerbContext(transport=adapter, manifest=manifest, places=book)
        _mark(ctx, "behind", -0.5, 0.0)
        result = await _go(ctx, "behind", face=False, timeout_s=60.0)
        assert result.data["outcome"] == "stalled", result.summary
        assert "turning in place toward its way to 'behind', it turned no nearer" in (
            result.summary
        )
    finally:
        await adapter.close()


class _RestsOff(Sim2DTransport):
    """A body that comes to rest after each turn in place at a heading a test scripts, as a
    legged turn that goes on past where its reading said it faced does."""

    def __init__(self, goal_yaw: float, offs: list[float], **kw: Any) -> None:
        super().__init__(**kw)
        self.goal_yaw, self.offs = goal_yaw, list(offs)
        self._spun = False

    async def send_intent(self, intent: Intent) -> Ack:
        if intent.kind == "move":
            if not intent.params.get("vx") and intent.params.get("wz"):
                self._spun = True
            elif intent.params.get("vx"):
                self._spun = False
        return await super().send_intent(intent)

    async def stop(self) -> None:
        await super().stop()
        if self._spun and self.offs:
            self.world.ducks[0].theta = self.goal_yaw + self.offs.pop(0)
        self._spun = False


async def test_a_heading_further_off_than_a_try_before_says_how_near_that_try_came() -> None:
    """At the place, the first turn came to rest nearer the way than the second: it said the
    second was as near as it turned."""
    from quackd_microduck.transports.mapd import sim_map_params

    room = Room("open", 1.5, 1.5, start=(-0.3, 0.0, 0.0))
    probe = _RestsOff(0.0, [], seed=3, room=room, map_params=sim_map_params(0.08))
    probe_adapter = MicroduckAdapter(probe)
    probe_manifest = await probe_adapter.connect()
    source = spatial_of(probe_adapter)
    assert source is not None
    tol_yaw = Gait.of(VerbContext(transport=probe_adapter, manifest=probe_manifest), source, {})
    await probe_adapter.close()
    # off the way by more than the turn's tolerance each time, the second by more than the first
    first, second = 1.25 * tol_yaw.tol_yaw, 2.25 * tol_yaw.tol_yaw
    goal_yaw = math.pi / 2
    transport = _RestsOff(
        goal_yaw, [first, second], seed=3, room=room, map_params=sim_map_params(0.08)
    )
    adapter = MicroduckAdapter(transport)
    manifest = await adapter.connect()
    try:
        await adapter.sleep(1.0)
        source = spatial_of(adapter)
        assert source is not None
        book = PlaceBook(PlaceStore("t", persist=False), source)
        ctx = VerbContext(transport=adapter, manifest=manifest, places=book)
        _mark(ctx, "ahead", 0.3, 0.0, goal_yaw)
        result = await _go(ctx, "ahead", timeout_s=60.0)
        assert result.ok, result.summary
        assert (
            f"facing {math.degrees(second):.0f}° off the way it faced there, where a try before "
            f"came to within {math.degrees(first):.0f}°"
        ) in result.summary
    finally:
        await adapter.close()


class _ClockRaises(Recording):
    """A body whose clock raises the run's own stop on the first sleep after `after` twists, as a
    live window closed mid-walk does: with the last twist still standing."""

    def __init__(self, inner: Any, after: int) -> None:
        super().__init__(inner)
        self.after, self.raised = after, False

    async def sleep(self, seconds: float) -> None:
        if not self.raised and len(self.twists()) >= self.after:
            self.raised = True
            raise Aborted("the window was closed")
        await self.inner.sleep(seconds)


async def test_a_stop_of_the_runs_raised_by_its_clock_mid_walk_leaves_the_body_stopped() -> None:
    """Every stop of the run's was taken for the rule's reader's, which stops the body before it
    raises, so one a sleep raised left the walk's last twist standing."""
    adapter, _body, ctx = await _duck("two-rooms")
    try:
        body = _ClockRaises(adapter, after=3)
        ctx = VerbContext(transport=body, manifest=ctx.manifest, places=ctx.places)
        _mark(ctx, "far", 0.8, -0.5)
        with pytest.raises(Aborted, match="the window was closed"):
            await _go(ctx, "far", face=False)
        assert len(body.twists()) >= 3
        assert body.sent[-1][1] == "stop"
    finally:
        await adapter.close()
