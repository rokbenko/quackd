"""Walking to a place (`go_to_place`, `roboglot.verbs.navigate`): on the rosbridge mock base's own
odometry, against maps a test hands a body that stands still, and stopped, saying why, on
everything that should stop it. Every walk runs on a mock's own clock, so a test of a twenty-second
walk takes a fraction of one."""

from __future__ import annotations

import asyncio
import math
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from typing import Any

import numpy as np
import pytest

from roboglot.safety import Aborted
from roboglot.space.grid import FREE, OCCUPIED, OccupancyGrid, PlanStopped
from roboglot.space.places import Place, PlaceBook, PlaceStore
from roboglot.space.pose import Fix, FrameRef, Pose2D
from roboglot.space.source import SpatialSource, SpatialSourceBase
from roboglot.transport.base import Ack, Intent, RobotState, TransportError, spatial_of
from roboglot.transport.mock import MockTransport
from roboglot.verbs import navigate
from roboglot.verbs.core import speed_limits
from roboglot.verbs.navigate import BLIND_S, SHARE, Gait, steer
from roboglot.verbs.places import PLACE_VERBS, GoToPlaceParams
from roboglot.verbs.registry import STOP_ENDED, STOPPED, VerbContext, VerbResult
from roboglot_microduck import MicroduckAdapter
from roboglot_rosbridge import RosbridgeAdapter
from roboglot_rosbridge.mock import TICK_S as BASE_TICK_S
from roboglot_rosbridge.mock import RosbridgeMock
from tests.support import UNTIL_WITHIN_S, until_async

FLOOR = {"vx": 0.2, "wz": 1.5}
"""A gait floor a body publishes (`extras.gait_floor`), this test's own: a walk above the share of
the forward clamp a `move` asks for, and a turn above the turn clamp, which no twist may pass."""
ACHIEVED = 0.5
"""The fraction of what it asks for that the same body says it covers
(`extras.achieved_fraction`)."""


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
                return Ack(accepted=False, reason="somebody is holding the robot")
            self.sent.append((self.inner.now(), "move", twist))
        return await self.inner.send_intent(intent)

    async def stop(self) -> None:
        self.sent.append((self.inner.now(), "stop", (0.0, 0.0, 0.0)))
        for _ in range(self.round_trip):
            await asyncio.sleep(0)
        await self.inner.stop()

    async def get_state(self) -> RobotState:
        state = await self.inner.get_state()
        if self.extras:
            state.extras = {**state.extras, **self.extras}
        return state

    def twists(self, since: float = -math.inf) -> list[tuple[float, float, float]]:
        return [
            twist for t, kind, twist in self.sent if kind == "move" and t >= since and any(twist)
        ]


class _Base(RosbridgeMock):
    """The rosbridge mock's wheeled base, which integrates every twist on its own clock and reads
    its own odometry as it goes, with a hand on that clock: each hook is called with the time
    after every tick it integrates, as a test reaches into a walk mid-stride."""

    def __init__(self) -> None:
        super().__init__(camera=False)
        self.hooks: list[Callable[[float], None]] = []

    async def sleep(self, seconds: float) -> None:
        remaining = seconds
        while remaining > 1e-9:
            step = min(BASE_TICK_S, remaining)
            await super().sleep(step)
            remaining -= step
            for hook in list(self.hooks):
                hook(self.now())


async def _base(base: _Base | None = None) -> tuple[_Base, Recording, VerbContext]:
    """A base on the rosbridge mock, connected and a second of its odometry read, and the context
    a verb of its runs in, on a body that writes down what it is sent, its places on that
    odometry."""
    base = base if base is not None else _Base()
    adapter = RosbridgeAdapter(base)
    manifest = await adapter.connect()
    await adapter.sleep(1.0)  # a second of readings, so its odometry has a period
    body = Recording(adapter)
    source = spatial_of(adapter)
    assert source is not None
    book = PlaceBook(PlaceStore("t", persist=False), source)
    return base, body, VerbContext(transport=body, manifest=manifest, places=book)


class _Hand(SpatialSourceBase):
    """The base's odometry, read through a hand that loses the robot, freezes the reading's clock
    or moves its pose once a test says so: a robot that no longer knows where it is, a stream
    that stopped, or a reading that jumped."""

    def __init__(self, inner: SpatialSource) -> None:
        self.inner = inner
        self.lost: str | None = None
        self.frozen_at: float | None = None
        self.shift = 0.0
        self.stop_every: float | None = None
        """How far the walk goes between two stops to look, as one by a map that looks at stops
        does (`stop_every_m`), or None for a walk with no stop on the way."""
        self._handed: float | None = None

    @property
    def stop_every_m(self) -> float | None:
        return self.stop_every

    def freeze(self) -> None:
        """Stop the stream where it stands: every reading after this one carries the time of the
        last it handed out, as a stream that stopped sending looks from the other end."""
        self.frozen_at = self._handed if self._handed is not None else self.inner.fix().t

    def fix(self) -> Fix:
        fix = self.inner.fix()
        self._handed = self.frozen_at if self.frozen_at is not None else fix.t
        if self.lost is not None:
            return replace(fix, pose=None, lost=self.lost)
        if self.frozen_at is not None:
            fix = replace(fix, t=self.frozen_at)
        if self.shift and fix.pose is not None:
            p = fix.pose
            fix = replace(fix, pose=Pose2D(p.x + self.shift, p.y, p.yaw))
        return fix

    @property
    def period_s(self) -> float | None:
        return self.inner.period_s


def _handled(ctx: VerbContext) -> _Hand:
    """Read `ctx`'s places through a hand on its odometry (`_Hand`), from a book of its own."""
    assert ctx.places is not None
    source = _Hand(ctx.places.source)
    ctx.places = PlaceBook(PlaceStore("t", persist=False), source)
    return source


def _mark(ctx: VerbContext, name: str, x: float, y: float, yaw: float = 0.0) -> None:
    """A place at (x, y) in the frame the robot is in, as if it had stood there."""
    assert ctx.places is not None
    frame = ctx.places.frame()
    assert frame is not None
    ctx.places.store.mark(Place(name, x, y, yaw, frame.id, frame.kind, 0.0))


async def _go(ctx: VerbContext, name: str, **kw: Any) -> VerbResult:
    return await PLACE_VERBS["go_to_place"].execute(ctx, GoToPlaceParams(name=name, **kw))


# ── it gets there ───────────────────────────────────────────────────────────────────────


async def test_it_walks_to_a_place_and_faces_the_way_it_faced_there() -> None:
    base, _body, ctx = await _base()
    _mark(ctx, "corner", 0.5, 0.5, 1.0)
    result = await _go(ctx, "corner")
    assert result.ok, result.summary
    assert "by its odometry" in result.summary
    assert ctx.places is not None
    gait = Gait.of(ctx, ctx.places.source, {})
    assert math.dist((base.x, base.y), (0.5, 0.5)) <= gait.tol_xy + 1e-9
    assert abs(math.remainder(base.theta - 1.0, math.tau)) <= gait.tol_yaw + 1e-9


# ── it stops, and says why ──────────────────────────────────────────────────────────────


async def test_lost_mid_walk_it_stops_and_says_so_and_sends_nothing_after() -> None:
    base, body, ctx = await _base()
    source = _handled(ctx)
    _mark(ctx, "far", 0.8, -0.5)
    lost_at: list[float] = []

    def lose(t: float) -> None:
        if not lost_at and body.twists():
            lost_at.append(t)
            source.lost = "walked into something"

    base.hooks.append(lose)
    result = await _go(ctx, "far")
    assert not result.ok and result.data["outcome"] == "lost", result.summary
    assert "walked into something" in result.summary
    assert body.twists(since=lost_at[0] + 1e-9) == [], "a twist after it was lost"
    assert body.sent[-1][1] == "stop"


async def test_a_stop_from_beside_the_walk_sends_no_twist_after_it() -> None:
    """The stop lands on the clock, mid-stride, not when the walk next asks: it is the next twist
    that has to be the one not sent."""
    base, body, ctx = await _base()
    _mark(ctx, "far", 0.8, -0.5)
    told: list[float] = []

    def brake(t: float) -> None:
        if not told and len(body.twists()) >= 5:
            told.append(t)

    base.hooks.append(brake)
    ctx.interrupted = lambda: STOPPED if told else None
    result = await _go(ctx, "far")
    assert not result.ok and result.data.get("ended_by") == STOP_ENDED, result.summary
    assert body.twists(since=told[0]) == []
    assert body.sent[-1][1] == "stop"


async def test_the_battery_rule_read_at_a_stop_ends_the_walk_stopped() -> None:
    _, body, ctx = await _base()
    _mark(ctx, "far", 0.8, -0.5)

    async def battery(_state: RobotState) -> None:
        raise Aborted("battery below the robot's own rule")

    ctx.abort_when = battery
    with pytest.raises(Aborted):
        await _go(ctx, "far")
    assert body.twists(), "it never walked, so no stop was reached"
    assert body.sent[-1][1] == "stop"


async def test_a_twist_the_body_refuses_stops_the_walk_and_says_why() -> None:
    _, body, ctx = await _base()
    _mark(ctx, "far", 0.8, -0.5)
    body.refuse_after = 3
    result = await _go(ctx, "far")
    assert not result.ok and "somebody is holding the robot" in result.summary
    assert result.data["move_refused"] == "somebody is holding the robot"
    assert body.sent[-1][1] == "stop"


async def test_its_own_clock_ends_it_stopped() -> None:
    """A place behind it: enough time by the way's length alone, and not for the turn too."""
    _, body, ctx = await _base()
    assert ctx.places is not None
    gait = Gait.of(ctx, ctx.places.source, {})
    _mark(ctx, "behind", -1.0, 0.0)
    walk_s, turn_s = 1.0 / gait.v_exp, math.pi / gait.w_exp
    result = await _go(ctx, "behind", timeout_s=walk_s + turn_s / 2)
    assert not result.ok and result.data["outcome"] == "timeout", result.summary
    assert body.sent[-1][1] == "stop"


#: What `_StopsLost` says of a stop that did not reach the body.
LOST = "the link to the base is gone"


class _StopsLost(Recording):
    """A body whose stops stop reaching it once `lost` says so: each says why in `stop_error` and
    raises nothing, as a transport that knows its stop did not land does."""

    def __init__(self, inner: Any, lost: Callable[[_StopsLost], bool]) -> None:
        super().__init__(inner)
        self.lost = lost
        self.stop_error: str | None = None

    async def stop(self) -> None:
        await super().stop()
        self.stop_error = LOST if self.lost(self) else None


async def _stops_lost() -> tuple[_StopsLost, VerbContext]:
    """`_base`'s walk, on a body every stop of which is lost."""
    _, recording, ctx = await _base()
    body = _StopsLost(recording.inner, lambda _b: True)
    ctx.transport = body
    return body, ctx


async def test_a_stop_partway_through_the_walk_that_did_not_reach_the_body_ends_it_there() -> None:
    """The walk stops to look along its way, and read nothing of whether those stops landed: a
    base whose link had gone was stopped, looked from and walked on from, as if it stood still.
    It ends at the first that did not land, says so, and sends nothing after it."""
    body, ctx = await _stops_lost()
    _handled(ctx).stop_every = 0.2
    _mark(ctx, "far", 0.8, -0.5)
    result = await _go(ctx, "far")
    assert not result.ok and result.data["outcome"] == "unstopped", result.summary
    assert result.summary.startswith("go_to_place: it walked ")
    said = f"But go_to_place's stop could not be delivered, so the body may still be moving: {LOST}"
    assert said in result.summary
    assert result.data["stop_error"] == LOST
    stops = [i for i, sent in enumerate(body.sent) if sent[1] == "stop"]
    assert body.twists(), "it never walked to its first stop"
    assert stops == [len(body.sent) - 1], "the walk went on after a stop that did not land"


async def test_a_walk_that_ends_on_a_stop_that_did_not_reach_the_body_says_so() -> None:
    """Every ending of a walk sends one stop, and each read it as landed: its own clock running
    out said the walk ended stopped over a base that may still be turning."""
    body, ctx = await _stops_lost()
    assert ctx.places is not None
    gait = Gait.of(ctx, ctx.places.source, {})
    _mark(ctx, "behind", -1.0, 0.0)
    walk_s, turn_s = 1.0 / gait.v_exp, math.pi / gait.w_exp
    result = await _go(ctx, "behind", timeout_s=walk_s + turn_s / 2)
    assert not result.ok and result.data["outcome"] == "timeout", result.summary
    assert result.summary.startswith("go_to_place: its ")
    said = f"go_to_place's stop could not be delivered, so the body may still be moving: {LOST}"
    assert said in result.summary
    assert result.data["stop_error"] == LOST
    assert body.sent[-1][1] == "stop"


# ── refused before anything moves ───────────────────────────────────────────────────────


async def test_an_orphaned_place_is_refused_before_anything_moves() -> None:
    _, body, ctx = await _base()
    assert ctx.places is not None
    ctx.places.store.mark(Place("old", 0.0, 0.0, 0.0, "map:another", "map", 0.0))
    result = await _go(ctx, "old")
    assert not result.ok and "orphaned" in result.summary and "mark it again" in result.summary
    assert body.sent == []


class _LostMap(SpatialSourceBase):
    """A robot's own map that has lost it, as a mapper does once it no longer knows where the
    robot is."""

    def __init__(self, transport: Any) -> None:
        self._transport = transport

    def fix(self) -> Fix:
        frame = FrameRef("map", "map:lost", persistent=True)
        return Fix(None, "map", frame, lost="walked into something", t=self._transport.now())


async def test_a_robot_that_does_not_know_where_it_is_is_told_how_to_find_out() -> None:
    mock = MockTransport()
    manifest = await MicroduckAdapter(mock).connect()
    book = PlaceBook(PlaceStore("t", persist=False), _LostMap(mock))
    ctx = VerbContext(transport=mock, manifest=manifest, places=book)
    _mark(ctx, "far", 0.8, -0.5)
    result = await _go(ctx, "far")
    assert not result.ok and result.data["outcome"] == "lost"
    assert "walk a little and stop" in result.summary
    assert [i for i in mock.intents if i.kind == "move"] == []


async def test_a_way_longer_than_its_time_is_refused_before_anything_moves() -> None:
    _, body, ctx = await _base()
    _mark(ctx, "far", 0.8, -0.5)
    result = await _go(ctx, "far", timeout_s=2.0)
    assert not result.ok and "longer than the 2 s it was given" in result.summary
    assert body.twists() == []


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
    _, body, ctx = await _base()
    _mark(ctx, "far", 0.8, -0.5, 2.0)
    body.extras = {"gait_floor": {"vx": 0.5, "wz": 2.0}, "achieved_fraction": 1.0}
    await _go(ctx, "far")
    max_vx, _max_vy, max_wz = speed_limits(ctx.manifest)
    assert body.twists(), "it never walked"
    for vx, vy, wz in body.twists():
        assert abs(vx) <= max_vx + 1e-9 and vy == 0.0 and abs(wz) <= max_wz + 1e-9


async def test_with_no_map_it_walks_no_further_than_one_move_could_and_says_how_far_is_left() -> (
    None
):
    _, body, ctx = await _base()
    _mark(ctx, "far end", 5.3, 0.0)
    result = await _go(ctx, "far end", timeout_s=120.0)
    cap = BLIND_S * speed_limits(ctx.manifest)[0]
    assert not result.ok and result.data["outcome"] == "capped", result.summary
    assert result.data["walked_m"] == pytest.approx(cap, abs=0.1)
    assert result.data["left_m"] == pytest.approx(5.3 - cap, abs=0.15)
    assert f"{result.data['walked_m']:.1f} m where its map has seen nothing" in result.summary
    assert body.sent[-1][1] == "stop"


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

    from roboglot_microduck import microduck_manifest

    ctx = VerbContext(transport=MockTransport(), manifest=microduck_manifest("mock"))
    max_vx, _max_vy, max_wz = speed_limits(ctx.manifest)
    plain = Gait.of(ctx, Source(), {})
    assert plain.v == pytest.approx(SHARE * max_vx) and plain.v_exp == plain.v
    assert plain.w == pytest.approx(min(1.0, max_wz))
    assert plain.tol_xy == pytest.approx(max(0.08, plain.v * (0.25 + 0.1)))
    assert plain.blind_m == pytest.approx(BLIND_S * max_vx)
    assert SHARE * max_vx < FLOOR["vx"] < max_vx and FLOOR["wz"] > max_wz

    floored = Gait.of(ctx, Source(), {"gait_floor": FLOOR, "achieved_fraction": ACHIEVED})
    assert floored.v == pytest.approx(min(FLOOR["vx"], max_vx))
    assert floored.w == pytest.approx(min(FLOOR["wz"], max_wz)), "never past the turn clamp"
    assert floored.v_exp == pytest.approx(floored.v * ACHIEVED)
    # how closely it follows its way, and a heading near enough, from what it covers
    assert floored.track_m == pytest.approx(floored.v_exp * (0.25 + 0.1))
    assert floored.tol_yaw == pytest.approx(floored.w_exp * (0.25 + 0.1))


async def test_a_reading_that_stops_moving_on_holds_the_body_then_ends_the_walk() -> None:
    """Freshness is the reading's own clock moving on, never a message arriving: past a twist's
    hold the body is held still, and past the source's stale window the walk ends."""
    base, body, ctx = await _base()
    source = _handled(ctx)
    _mark(ctx, "far", 0.8, -0.5)
    frozen: list[float] = []

    def freeze(t: float) -> None:
        if not frozen and len(body.twists()) >= 5:
            frozen.append(t)
            source.freeze()

    base.hooks.append(freeze)
    result = await _go(ctx, "far")
    assert result.data["outcome"] == "stale" and "has not moved on" in result.summary
    held_from = frozen[0] + Gait.of(ctx, source, {}).fresh_s + 0.11  # and the tick it read on
    assert body.twists(since=held_from) == [], "it walked on a reading that stopped"
    zeros = [t for t, kind, twist in body.sent if kind == "move" and t > held_from]
    assert zeros and all(
        not any(tw) for _t, kind, tw in body.sent if kind == "move" and _t > held_from
    )
    assert body.sent[-1][1] == "stop"


async def test_a_reading_that_jumps_while_it_walks_ends_the_walk() -> None:
    base, body, ctx = await _base()
    source = _handled(ctx)
    _mark(ctx, "far", 0.8, -0.5)
    jumped: list[float] = []

    def jump(t: float) -> None:
        if not jumped and len(body.twists()) >= 5:
            jumped.append(t)
            source.shift = 0.5

    base.hooks.append(jump)
    result = await _go(ctx, "far")
    assert result.data["outcome"] == "jumped" and result.data["jump_m"] >= 0.5
    assert body.twists(since=jumped[0] + 0.11) == []
    assert body.sent[-1][1] == "stop"


async def test_a_walk_cancelled_from_outside_leaves_the_body_stopped() -> None:
    """The executor's own clock cancels a verb it has waited on too long: the walk stops the
    body on its way out, whatever it was sending."""
    base, body, ctx = await _base()
    _mark(ctx, "far", 0.8, -0.5)
    under_way = asyncio.Event()
    base.hooks.append(lambda _t: under_way.set() if len(body.twists()) >= 5 else None)
    walking = asyncio.create_task(_go(ctx, "far"))
    await under_way.wait()
    walking.cancel()
    with pytest.raises(asyncio.CancelledError):
        await walking
    assert body.sent[-1][1] == "stop", "cancelled mid-walk with the last twist standing"


async def test_a_place_well_off_to_its_side_is_walked_to_on_an_arc_without_a_false_stall() -> None:
    """On a wide arc the straight distance to the place barely shrinks at first, so headway is
    measured along the arc it walks, which shrinks at its own speed."""
    _, _body, ctx = await _base()
    aside = math.radians(80)
    _mark(ctx, "aside", 0.5 * math.cos(aside), 0.5 * math.sin(aside))
    result = await _go(ctx, "aside", face=False)
    assert result.ok, result.summary


class _Ahead(_Hand):
    """The base's odometry read while it walks says it is `metres` further on than it is, as a
    reading taken on a stride may; at rest, where it stands. Each leg ends short of the place, so
    the walk stands, looks and tries again. It knows the body is 0.16 m across, as a map that
    knows the body's size does, so the place's tolerance is that body's and not the few
    centimetres one tick of walking covers, and a try can come a step nearer and still fall
    short."""

    FOOTPRINT_M = 0.08

    def __init__(self, inner: SpatialSource, base: _Base, metres: float) -> None:
        super().__init__(inner)
        self.base, self.metres = base, metres

    @property
    def footprint_m(self) -> float | None:
        return self.FOOTPRINT_M

    def fix(self) -> Fix:
        fix = super().fix()
        if self.base.vx <= 0 or fix.pose is None:
            return fix
        p, on = fix.pose, self.metres
        ahead = Pose2D(p.x + on * math.cos(p.yaw), p.y + on * math.sin(p.yaw), p.yaw)
        return replace(fix, pose=ahead)


async def _ahead() -> tuple[_Base, Recording, VerbContext, _Ahead]:
    """A base whose reading taken walking is its place's tolerance ahead of it."""
    base, body, ctx = await _base()
    assert ctx.places is not None
    source = _Ahead(ctx.places.source, base, 0.0)
    source.metres = Gait.of(ctx, source, {}).tol_xy
    ctx.places = PlaceBook(PlaceStore("t", persist=False), source)
    return base, body, ctx, source


# ── the review's findings, each a test ──────────────────────────────────────────────────


async def test_arrival_is_judged_by_the_reading_after_its_last_look() -> None:
    """The reading at the last stop may say the robot is elsewhere than the walk last read, as a
    map's correction there does, and it was said to have arrived 0.15 m off. It walks on to the
    place, or says it did not."""
    base, body, ctx = await _base()
    source = _handled(ctx)
    _mark(ctx, "far", 0.8, -0.5)
    gait = Gait.of(ctx, source, {})
    corrected: list[float] = []

    def correct(t: float) -> None:
        fix = source.inner.fix()
        near = (
            fix.pose is not None and math.dist((fix.pose.x, fix.pose.y), (0.8, -0.5)) < gait.tol_xy
        )
        # the last look: the walk's own stop, then the stop it looks at, two in a row
        last_look = len(body.sent) >= 2 and [k for _t, k, _tw in body.sent[-2:]] == ["stop"] * 2
        if not corrected and near and last_look:
            corrected.append(t)
            assert fix.pose is not None
            # a correction as large as a map believes one, away from the place
            source.shift = 0.10 if fix.pose.x >= 0.8 else -0.10

    base.hooks.append(correct)
    result = await _go(ctx, "far")
    assert corrected, "the walk never stood at the place to be corrected there"
    assert result.ok, result.summary
    assert result.data["error_m"] <= gait.tol_xy, "it called itself arrived further off"


async def test_a_reading_that_was_not_moving_on_when_it_began_sends_no_twist() -> None:
    _, body, ctx = await _base()
    source = _handled(ctx)
    _mark(ctx, "far", 0.8, -0.5)
    source.freeze()  # stopped before the walk began
    result = await _go(ctx, "far")
    assert result.data["outcome"] == "stale", result.summary
    assert body.twists() == [], "it walked on a reading that had already stopped"


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
    from roboglot.glotfile.parser import parse_glot_text
    from roboglot.safety import Executor
    from roboglot.verbs.registry import registry_from_manifest

    _, body, ctx = await _base()
    adapter = body.inner
    walked = body.twists

    async def battery() -> RobotState:
        state = await adapter.get_state()
        state.battery_percent = 10.0 if walked() else 40.0
        return state

    body.get_state = battery  # type: ignore[method-assign]
    glot = parse_glot_text(
        "---\nglot: 0\nname: t\ndescription: d\nverbs:\n  allow: [go_to_place]\n"
        "success: [x]\nabort_when: [Battery below 15%]\n---\n# Task\nx\n"
    )
    registry = registry_from_manifest(ctx.manifest, adapter)  # type: ignore[arg-type]
    assert ctx.places is not None
    executor = Executor(registry, body, contract=glot.frontmatter, places=ctx.places.store)
    body.round_trip = 3  # an abort set while a stop is on its way can cancel what sent it
    _mark(ctx, "far", 0.8, -0.5)
    with pytest.raises(Aborted, match="battery 10% below 15%; the verb stopped where it stood"):
        await executor.run_verb("go_to_place", {"name": "far", "face": False})
    assert body.twists(), "it never walked"
    assert body.sent[-1][1] == "stop"


async def test_a_stop_while_it_stands_and_looks_mid_walk_starts_no_new_leg() -> None:
    _, body, ctx = await _base()
    _handled(ctx).stop_every = 0.3  # stops on the way to the place, a look at each
    _mark(ctx, "far", 0.9, 0.0)
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
    base, body, ctx = await _base()
    _mark(ctx, "ahead", 1.0, 0.0)
    assert base.spatial is not None
    track, begun = base.spatial.track, base.now()

    def pick_up(t: float) -> None:
        # held for one reading a second into the walk, which is the frame it walked in ending
        if t > begun + 1.0 and track.epoch == 0:
            track.update(base.x, base.y, base.theta, t, held="picked up")

    base.hooks.append(pick_up)
    result = await _go(ctx, "ahead", face=False)
    assert result.data["outcome"] == "frame"
    assert "picked up, falls or goes limp" in result.summary
    assert body.sent[-1][1] == "stop"


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

    from roboglot_microduck import microduck_manifest

    ctx = VerbContext(transport=MockTransport(), manifest=microduck_manifest("mock"))
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


async def test_the_way_is_planned_off_the_event_loop_and_a_stop_meanwhile_does_not_wait_for_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Planning a walk ran on the event loop, in pure Python over a grid as large as the map
    service sends, and a few hundred cells a side held the loop for seconds: the heartbeat, the
    kill switch's callbacks and MCP waited for it. The seam records which thread plans, and holds
    the plan until the test lets it go, so nothing here is timed: the plan and the search for
    floor the body fits on run off the loop's thread, and a walk cancelled while its plan is out
    ends at once, the body stopped, and the plan is let go only after."""
    loop_thread = threading.get_ident()
    threads: dict[str, int] = {}
    entered, release = threading.Event(), threading.Event()
    real_plan, real_standable = OccupancyGrid.plan, navigate._standable

    def plan(self: OccupancyGrid, *args: Any, **kw: Any) -> Any:
        threads["plan"] = threading.get_ident()
        entered.set()
        if not release.wait(UNTIL_WITHIN_S):
            raise AssertionError("the plan was never let go")
        return real_plan(self, *args, **kw)

    def standable(*args: Any, **kw: Any) -> Any:
        threads["standable"] = threading.get_ident()
        return real_standable(*args, **kw)

    monkeypatch.setattr(OccupancyGrid, "plan", plan)
    monkeypatch.setattr(navigate, "_standable", standable)
    mock, ctx = await _still_duck(OccupancyGrid(0.05, (-1.0, -1.0), np.zeros((40, 40), np.uint8)))
    _mark(ctx, "away", 0.5, 0.0)
    walking = asyncio.create_task(_go(ctx, "away", face=False))
    try:
        await until_async(entered.is_set, what="the walk starting its plan")
        assert loop_thread not in threads.values(), "planned on the event loop's thread"
        walking.cancel()
        with pytest.raises(asyncio.CancelledError):
            await walking
        assert not release.is_set() and mock.intents[-1].kind == "stop"
        assert [i for i in mock.intents if i.kind == "move"] == []
    finally:
        release.set()


async def test_a_walk_stopped_while_it_plans_ends_its_plan_and_holds_no_shared_worker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A walk stopped while its plan ran left the plan running to its end in a worker of the
    loop's default executor, which the board's detector, its camera and the Open Duck's camera
    share: twelve walks started and stopped on a large map held every worker, and the next look
    waited behind plans nobody would read. The plan runs on a thread of its own, never one of the
    shared workers, and the walk's stop is the plan's stop: the stand-in plan below does what
    the search does, give up once its stop is set, and is never let go by the test otherwise."""
    loop = asyncio.get_running_loop()
    shared = ThreadPoolExecutor(thread_name_prefix="shared-default")
    loop.set_default_executor(shared)
    names: list[str] = []
    stops: list[threading.Event | None] = []
    entered, ended = threading.Event(), threading.Event()

    def plan(self: OccupancyGrid, *args: Any, **kw: Any) -> Any:
        names.append(threading.current_thread().name)
        stop = kw.get("stop")
        stops.append(stop)
        entered.set()
        try:
            if stop is None or not stop.wait(UNTIL_WITHIN_S):
                raise AssertionError("the plan's stop was never set")
            raise PlanStopped
        finally:
            ended.set()

    monkeypatch.setattr(OccupancyGrid, "plan", plan)
    mock, ctx = await _still_duck(OccupancyGrid(0.05, (-1.0, -1.0), np.zeros((40, 40), np.uint8)))
    _mark(ctx, "away", 0.5, 0.0)
    walking = asyncio.create_task(_go(ctx, "away", face=False))
    await until_async(entered.is_set, what="the walk starting its plan")
    assert not names[0].startswith("shared-default"), f"planned on a shared worker: {names}"
    walking.cancel()
    with pytest.raises(asyncio.CancelledError):
        await walking
    await until_async(ended.is_set, what="the plan ending once its walk was stopped")
    assert stops[0] is not None and stops[0].is_set()
    assert mock.intents[-1].kind == "stop" and [i for i in mock.intents if i.kind == "move"] == []


async def test_a_way_too_narrow_says_how_much_room_it_needed() -> None:
    cells = np.full((40, 40), FREE, dtype=np.uint8)
    cells[:, 25] = OCCUPIED  # a wall across the grid at x = 0.25
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


class _Coasting(_Base):
    """A body that goes on at its last twist for a moment after it is told to stop, as one
    walking does: a reading taken at once says where it was going, not where it came to rest."""

    COAST_S = 0.2

    def __init__(self) -> None:
        super().__init__()
        self._stop_at: float | None = None
        self.hooks.append(self._coast)

    async def stop(self) -> None:
        if self._stop_at is None:
            self._stop_at = self.now() + self.COAST_S
            self._last_cmd_t = self.now()  # keep its deadman fed while it coasts

    async def send_intent(self, intent: Intent) -> Ack:
        if intent.kind == "move":
            self._stop_at = None
        return await super().send_intent(intent)

    def _coast(self, t: float) -> None:
        if self._stop_at is not None and t >= self._stop_at:
            self.vx = self.vy = self.wz = 0.0
            self._stop_at = None


async def test_on_odometry_it_says_how_far_it_came_to_rest_from_the_place() -> None:
    base, body, ctx = await _base(_Coasting())
    _mark(ctx, "ahead", 0.6, 0.0)
    result = await _go(ctx, "ahead", face=False)
    await body.sleep(1.0)  # well past any coast
    at_rest = math.dist((base.x, base.y), (0.6, 0.0))
    assert result.ok, result.summary
    assert result.data["error_m"] == pytest.approx(at_rest, abs=0.005), "read mid-coast"


class _SlowStart(_Base):
    """A gait that takes a moment to start: a body standing still that is sent a twist stands a
    while longer before it steps, as a legged walk does from a stand."""

    def __init__(self) -> None:
        super().__init__()
        self.start_s = 0.0
        self._asked_at: float | None = None

    async def send_intent(self, intent: Intent) -> Ack:
        if intent.kind == "move":
            if not any(intent.params.get(k) for k in ("vx", "vy", "wz")):
                self._asked_at = None
            else:
                if self._asked_at is None:
                    self._asked_at = self.now()
                if self.now() - self._asked_at < self.start_s:
                    intent = Intent.move()  # still standing, its deadman fed
        return await super().send_intent(intent)

    async def stop(self) -> None:
        self._asked_at = None
        await super().stop()


async def test_a_gait_that_takes_a_moment_to_start_is_not_taken_for_no_headway() -> None:
    """Judged by its rate alone, a walk from a stand was called stalled before its first step, on
    a gait that stands a moment before it steps."""
    base, _body, ctx = await _base(_SlowStart())
    assert ctx.places is not None
    gait = Gait.of(ctx, ctx.places.source, {})
    # standing as long as its rate alone gave it to come the place's tolerance nearer
    base.start_s = 2 * gait.tol_xy / gait.v_exp
    _mark(ctx, "ahead", 0.9, 0.0)
    result = await _go(ctx, "ahead", face=False)
    assert result.ok, result.summary


class _NoTurnInPlace(_Base):
    """A body that does not turn when asked to turn in place, as a legged one asked for a spin
    from a stand may: the twist reaches it and it stays facing the way it faced. With `swings_to`,
    its trunk swings round to face that way while it is told to spin and back when it is not, so a
    reading taken turning says it faces the way and one at rest says it does not."""

    def __init__(self, swings_to: float | None = None) -> None:
        super().__init__()
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
        if self.swings_to is not None and self._faced is None:
            self._faced, self.theta = self.theta, self.swings_to

    def _back(self) -> None:
        if self._faced is not None:
            self.theta, self._faced = self._faced, None


@pytest.mark.parametrize("sways", [True, False], ids=["its reading sways", "it turns no nearer"])
async def test_at_the_place_a_body_that_turns_no_nearer_its_heading_says_so_and_stops(
    sways: bool,
) -> None:
    """At the place, facing off the way it faced there, with a body that will not turn in place:
    a reading taken turning that said it faced the way had the walk look, plan and turn again
    until its clock ran out, and a turn that came no nearer ended a walk that had arrived as
    stalled."""
    base, _body, ctx = await _base(_NoTurnInPlace(math.pi / 2 if sways else None))
    _mark(ctx, "ahead", 0.6, 0.0, math.pi / 2)
    result = await _go(ctx, "ahead", timeout_s=60.0)
    off = abs(math.degrees(math.remainder(math.pi / 2 - base.theta, math.tau)))
    assert result.ok, result.summary
    assert f"facing {off:.0f}° off the way it faced there, as near as it turned" in (result.summary)
    assert result.data["replans"] == 1


async def test_another_try_that_brings_it_hardly_any_nearer_ends_the_walk_where_it_is() -> None:
    """A reading taken walking that put the body further on than it came to rest: each leg ended
    near enough by it, the reading after the look said not, and the walk planned, stepped and
    stopped again, a step nearer each time, a look apart."""
    _, body, ctx, source = await _ahead()
    gait = Gait.of(ctx, source, {})
    _mark(ctx, "ahead", 0.9, 0.0)
    result = await _go(ctx, "ahead", face=False, timeout_s=60.0)
    assert not result.ok and result.data["outcome"] == "stalled", result.summary
    assert f"where {gait.tol_xy:.2f} m is near enough, and another try brought it" in (
        result.summary
    )
    assert result.data["replans"] == 1
    assert result.data["error_m"] > gait.tol_xy
    assert body.sent[-1][1] == "stop"


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
    """A body that publishes a gait floor, held where it stands, its heading reading swaying across
    the line between turning in place and walking an arc to a place a metre off: each change began
    the no-headway clock again, and it pushed until its time ran out."""
    mock = MockTransport()
    adapter = MicroduckAdapter(mock)
    manifest = await adapter.connect()
    body = Recording(mock)
    body.extras = {"gait_floor": FLOOR, "achieved_fraction": ACHIEVED}
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


async def test_a_body_that_does_not_turn_in_place_toward_its_way_is_told_so() -> None:
    """A place behind a body that does not turn in place: the leg said something its map does
    not show was in its way."""
    _, _body, ctx = await _base(_NoTurnInPlace(None))
    _mark(ctx, "behind", -0.8, 0.0)
    result = await _go(ctx, "behind", face=False, timeout_s=60.0)
    assert result.data["outcome"] == "stalled", result.summary
    assert "turning in place toward its way to 'behind', it turned no nearer" in result.summary


class _RestsOff(_Base):
    """A body that comes to rest after each turn in place at a heading a test scripts, as a
    legged turn that goes on past where its reading said it faced does."""

    def __init__(self, goal_yaw: float, offs: list[float]) -> None:
        super().__init__()
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
            self.theta = self.goal_yaw + self.offs.pop(0)
        self._spun = False


async def test_a_heading_further_off_than_a_try_before_says_how_near_that_try_came() -> None:
    """At the place, the first turn came to rest nearer the way than the second: it said the
    second was as near as it turned."""
    goal_yaw = math.pi / 2
    base, _body, ctx = await _base(_RestsOff(goal_yaw, []))
    assert ctx.places is not None
    tol_yaw = Gait.of(ctx, ctx.places.source, {}).tol_yaw
    # off the way by more than the turn's tolerance each time, the second by more than the first
    first, second = 1.25 * tol_yaw, 2.25 * tol_yaw
    assert isinstance(base, _RestsOff)
    base.offs = [first, second]
    _mark(ctx, "ahead", 0.6, 0.0, goal_yaw)
    result = await _go(ctx, "ahead", timeout_s=60.0)
    assert result.ok, result.summary
    assert (
        f"facing {math.degrees(second):.0f}° off the way it faced there, where a try before "
        f"came to within {math.degrees(first):.0f}°"
    ) in result.summary


class _ClockRaises(Recording):
    """A body whose clock raises the run's own stop on the first sleep after `after` twists, as a
    sleep does for a body handed to another goal mid-walk: with the last twist still standing."""

    def __init__(self, inner: Any, after: int) -> None:
        super().__init__(inner)
        self.after, self.raised = after, False

    async def sleep(self, seconds: float) -> None:
        if not self.raised and len(self.twists()) >= self.after:
            self.raised = True
            raise Aborted("the body was handed to another goal")
        await self.inner.sleep(seconds)


async def test_a_stop_of_the_runs_raised_by_its_clock_mid_walk_leaves_the_body_stopped() -> None:
    """Every stop of the run's was taken for the rule's reader's, which stops the body before it
    raises, so one a sleep raised left the walk's last twist standing."""
    _, recording, ctx = await _base()
    body = _ClockRaises(recording.inner, after=3)
    ctx = VerbContext(transport=body, manifest=ctx.manifest, places=ctx.places)
    _mark(ctx, "far", 0.8, -0.5)
    with pytest.raises(Aborted, match="the body was handed to another goal"):
        await _go(ctx, "far", face=False)
    assert len(body.twists()) >= 3
    assert body.sent[-1][1] == "stop"
