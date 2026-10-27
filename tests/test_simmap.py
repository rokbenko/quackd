"""The simulators' stand-in for a robot's own map (`quackd.space.simmap`): it looks only at stops,
sees no further than the first wall, and is lost and found again as the real one says it is."""

from __future__ import annotations

import math
from dataclasses import dataclass, replace

import numpy as np
import pytest

from quackd.sim2d.world import DUCK_R
from quackd.space.grid import FREE, OCCUPIED, UNKNOWN, OccupancyGrid
from quackd.space.places import FrameRecord
from quackd.space.room import load_room
from quackd.space.simmap import BOOT, BUMPED, CARRIED, FELL, BodyNow, SimulatedMap
from quackd_microduck import mapd_upstream_api as mapd
from quackd_microduck.transports.mapd import sim_map_params

PARAMS = sim_map_params(DUCK_R)
DT = 0.05


@dataclass
class Body:
    """A body a test moves by hand, and the truth the map is told about it."""

    now: BodyNow

    def __call__(self) -> BodyNow:
        return self.now

    def set(self, **changes: object) -> None:
        self.now = replace(self.now, **changes)  # type: ignore[arg-type]


def _map(x: float = -1.0, y: float = 0.0, theta: float = 0.0) -> tuple[SimulatedMap, Body]:
    body = Body(BodyNow(0.0, x, y, theta, 0.0, standing=True, fallen=False, bumps=0))
    return SimulatedMap(load_room("two-rooms"), PARAMS, body=body, frame_key="sim:test"), body


def _still(m: SimulatedMap, body: Body, seconds: float) -> None:
    for _ in range(round(seconds / DT)):
        body.set(t=body.now.t + DT, moving=False)
        m.tick()


def _walk(m: SimulatedMap, body: Body, metres: float, speed: float = 0.15) -> None:
    """Straight ahead at `speed`, a tick at a time, and stopped at the end."""
    steps = round(metres / speed / DT)
    for _ in range(steps):
        th = body.now.theta
        body.set(
            t=body.now.t + DT,
            x=body.now.x + speed * DT * math.cos(th),
            y=body.now.y + speed * DT * math.sin(th),
            moving=True,
        )
        m.tick()
    body.set(moving=False)


def _turn(m: SimulatedMap, body: Body, radians: float, rate: float = 0.5) -> None:
    for _ in range(round(abs(radians) / rate / DT)):
        body.set(
            t=body.now.t + DT,
            theta=body.now.theta + math.copysign(rate * DT, radians),
            moving=True,
        )
        m.tick()
    body.set(moving=False)


def _cell(grid: OccupancyGrid, x: float, y: float) -> int:
    col = math.floor((x - grid.origin[0]) / grid.res_m)
    row = math.floor((y - grid.origin[1]) / grid.res_m)
    return int(grid.cells[row, col])


def test_it_knows_nothing_until_its_first_stop_and_then_where_the_duck_stands() -> None:
    m, body = _map()
    assert m.fix().lost == BOOT and m.fix().pose is None
    _still(m, body, PARAMS.still_s - DT)
    assert m.fix().lost == BOOT, "a keyframe before the stillness the real one waits for"
    assert not (m.grid_now().cells != UNKNOWN).any()
    _still(m, body, DT)
    fix = m.fix()
    assert fix.localized and fix.keyframes == 1 and not fix.collecting
    assert fix.pose is not None and (fix.pose.x, fix.pose.y, fix.pose.yaw) == (-1.0, 0.0, 0.0)
    assert _cell(m.grid_now(), -0.5, 0.0) == FREE, "the floor half a metre ahead was seen"
    assert _cell(m.grid_now(), -1.0 - DUCK_R / 2, 0.0) == FREE, "and the floor it stands on"
    assert _cell(m.grid_now(), -1.4, 0.0) == UNKNOWN, "but not the floor behind it"


def test_it_looks_only_when_the_duck_stops() -> None:
    m, body = _map()
    _still(m, body, PARAMS.still_s)
    seen = m.grid_now().cells.copy()
    _turn(m, body, math.pi / 2)
    assert np.array_equal(m.grid_now().cells, seen), "turning, it saw what it passed"
    assert m.fix().keyframes == 1
    _still(m, body, PARAMS.still_s)
    assert m.fix().keyframes == 2
    assert _cell(m.grid_now(), -1.0, 0.9) == FREE and (m.grid_now().cells != seen).any()


def test_a_wall_hides_what_is_behind_it() -> None:
    """From the left room, facing the wall below the doorway: the wall is solid in the map, the
    floor before it seen, and the room behind it as unknown as before."""
    m, body = _map(-0.6, -0.6, 0.0)
    _still(m, body, PARAMS.still_s)
    grid = m.grid_now()
    assert _cell(grid, -0.3, -0.6) == FREE
    assert _cell(grid, -0.01, -0.6) == OCCUPIED, "the wall's near face"
    assert _cell(grid, 0.4, -0.6) == UNKNOWN, "it saw through the wall"
    assert _cell(grid, 0.4, 0.0) != OCCUPIED, "the doorway is no wall"


def test_a_bump_loses_it_until_two_stops_agree_again() -> None:
    m, body = _map()
    _still(m, body, PARAMS.still_s)
    body.set(bumps=1)
    _still(m, body, PARAMS.still_s)
    assert m.fix().lost == BUMPED, "the stop it bumped at is not one of the two"
    for stop in range(1, PARAMS.reloc_stops + 1):
        _turn(m, body, 0.3)
        _still(m, body, PARAMS.still_s)
        assert m.fix().localized == (stop == PARAMS.reloc_stops), stop
    assert PARAMS.reloc_stops == mapd.AGREE_STOPS


def test_a_fall_and_a_carry_lose_it_too() -> None:
    m, body = _map()
    _still(m, body, PARAMS.still_s)
    body.set(fallen=True, standing=False)
    _still(m, body, DT)
    assert m.fix().lost == FELL and not m.fix().collecting
    m2, body2 = _map()
    _still(m2, body2, PARAMS.still_s)
    put: list[tuple[float, float, float]] = []

    def move(x: float, y: float, theta: float) -> None:
        put.append((x, y, theta))
        body2.set(x=x, y=y, theta=theta)

    m2.carry(1.0, -0.5, 1.0, move)
    assert put == [(1.0, -0.5, 1.0)] and m2.fix().lost == CARRIED


def test_a_wipe_forgets_the_map_and_starts_a_frame_of_its_own() -> None:
    m, body = _map()
    _still(m, body, PARAMS.still_s)
    before = m.fix().frame
    import asyncio

    assert asyncio.run(m.wipe()) == 1
    assert not (m.grid_now().cells != UNKNOWN).any() and m.fix().lost == BOOT
    assert m.fix().frame.id != before.id and m.fix().frame.key == before.key
    _turn(m, body, 0.2)
    _still(m, body, PARAMS.still_s)
    assert m.fix().localized and m.fix().frame.id != before.id


def test_a_wipe_retires_the_frame_it_ended_as_mapds_source_does() -> None:
    """Said in `retired_frames`, so the place store keeps that map as wiped and a later session
    of the same room skips it (below)."""
    m, body = _map()
    _still(m, body, PARAMS.still_s)
    first = m.fix().frame
    assert first is not None and m.retired_frames == frozenset()
    m.wipe_now()
    assert m.retired_frames == {first.id}
    _turn(m, body, 0.2)
    _still(m, body, PARAMS.still_s)
    second = m.fix().frame
    assert second is not None and second.id not in m.retired_frames
    m.wipe_now()
    assert m.retired_frames == {first.id, second.id}


def test_a_map_wiped_in_an_earlier_session_stays_wiped() -> None:
    """The same room is the same frame from one session to the next, and a wipe in one of them
    orphans what was marked before it in every session after."""
    m, _body = _map()
    first = m.frame
    records = [FrameRecord(first.id, "map", True, 0.0, wiped=True)]
    resolved = m.resolve_frame(records)
    assert resolved is not None and resolved.id != first.id
    assert m.fix().frame.id == resolved.id, "the readings say the frame the places are kept in"
    assert m.resolve_frame([]) == resolved, "and it never goes back"


def test_a_stop_not_looked_from_yet_is_being_collected() -> None:
    m, body = _map()
    assert m.fix().collecting, "standing still at boot, with no stop looked from"
    _still(m, body, PARAMS.still_s)
    assert not m.fix().collecting
    body.set(head_yaw=0.4)  # the head turned, and the clock has not ticked since
    assert m.fix().collecting
    _still(m, body, PARAMS.still_s + DT)  # still from the tick that saw the head turn
    assert not m.fix().collecting
    body.set(moving=True)
    assert not m.fix().collecting, "a body told to move is no stop"


def test_two_runs_of_one_walk_make_one_map() -> None:
    def run() -> OccupancyGrid:
        m, body = _map()
        _still(m, body, PARAMS.still_s)
        _walk(m, body, 0.6)
        _still(m, body, PARAMS.still_s)
        _turn(m, body, -1.0)
        _still(m, body, PARAMS.still_s)
        return m.grid_now()

    a, b = run(), run()
    assert np.array_equal(a.cells, b.cells) and a.keyframes == b.keyframes


def test_its_numbers_are_the_real_maps() -> None:
    """Each is the adapter's, from what it read of the real mapper, and the walk's leg between
    stops is the longest the real one's correction gate can follow."""
    assert PARAMS.still_s == mapd.SETTLE_S and PARAMS.period_s == mapd.STREAM_PERIOD_S
    assert PARAMS.res_m == mapd.GRID_RES_M and PARAMS.tof_range_m == mapd.TOF_MAX_M
    m, _body = _map()
    assert m.stop_every_m == pytest.approx(
        mapd.CORRECTION_M / math.sin(math.radians(mapd.CORRECTION_DEG))
    )
    assert m.settle_timeout_s == pytest.approx(mapd.SETTLE_S + 2 * mapd.STREAM_PERIOD_S)
