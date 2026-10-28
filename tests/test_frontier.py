"""Frontier groups, which one a robot walks to first, what it gives up, and which ways it looks.

Every grid here is drawn as a map is read, the top row the highest y, one character a cell:
`#` solid, `.` floor seen empty, `?` not seen.
"""

from __future__ import annotations

import math

import pytest

from quackd.space.frontier import (
    GivenUp,
    body_cells,
    fan,
    frontier_groups,
    nearest_first,
    nearest_view,
    opening_cells,
    ray_to_wall,
    still_unseen_beside,
    unseen_in_view,
    views_round,
    views_worth_taking,
    world_cell,
)
from quackd.space.grid import FREE, OCCUPIED, UNKNOWN, OccupancyGrid
from quackd.verbs.core import MAX_SCAN_STEPS, SearchScanParams, scan_steps

RES = 0.1
ALPHABET = {"?": UNKNOWN, ".": FREE, "#": OCCUPIED}


def grid(*rows: str, origin: tuple[float, float] = (0.0, 0.0)) -> OccupancyGrid:
    assert len({len(row) for row in rows}) == 1, "every row the same width"
    return OccupancyGrid.from_text(
        "".join(reversed(rows)),
        width=len(rows[0]),
        height=len(rows),
        res_m=RES,
        origin=origin,
        alphabet=ALPHABET,
    )


def middle(col: int, row: int) -> tuple[float, float]:
    return ((col + 0.5) * RES, (row + 0.5) * RES)


TWO_ROOMS_ROWS = (
    "##############",
    "#........#...#",
    "#........#.?.#",
    "#?.......#...#",
    "#........#...#",
    "#........#...#",
    "#............#",
    "##############",
)
TWO_ROOMS = grid(*TWO_ROOMS_ROWS)
"""Two rooms with a doorway along the bottom row. Something not seen behind the wall, three cells
from where the robot stands, and something not seen across its own room, five cells off."""


def test_the_frontier_nearest_by_the_walk_beats_the_one_nearest_in_a_straight_line() -> None:
    start = middle(7, 4)
    groups = frontier_groups(TWO_ROOMS)
    assert len(groups) == 2
    ranked = nearest_first(TWO_ROOMS, start, groups, radius_m=0.0)
    near, far = ranked
    assert (2, 4) in near.cells, "the one across the robot's own room"
    assert (10, 5) in far.cells, "the one behind the wall, through the doorway"
    straight = math.dist(start, middle(10, 5))
    assert straight < near.distance_m < far.distance_m
    assert near.goal == pytest.approx(middle(2, 4))


def test_floor_nobody_has_seen_is_never_walked_over_to_reach_one() -> None:
    """The only way to the far room runs over floor nobody has seen, so its frontier cannot be
    reached at all, and is left out rather than ranked by a walk nobody could check."""
    shut = grid(
        "##########",
        "#....#...#",
        "#....?.?.#",
        "#....#...#",
        "##########",
    )
    groups = frontier_groups(shut)
    ranked = nearest_first(shut, middle(2, 2), groups, radius_m=0.0)
    assert [k.goal for k in ranked] == [pytest.approx(middle(4, 2))]


def test_a_frontier_looking_into_an_opening_narrower_than_the_body_is_left_out() -> None:
    """A gap one cell across, which a body three cells wide can neither pass nor see through,
    and the open edge of the room beside it."""
    room = grid(
        "##?######",
        "#.......#",
        "#.......#",
        "#.......?",
        "#.......?",
        "#.......?",
        "#########",
    )
    wide = body_cells(RES, 0.15)
    assert wide == 3
    everything = frontier_groups(room)
    kept = frontier_groups(room, min_cells=wide)
    assert len(everything) == 2 and len(kept) == 1
    (edge,) = kept
    assert all(col == 7 for col, _ in edge), "the open edge on the right"
    assert opening_cells(room, edge) >= wide


def test_one_unseen_cell_in_seen_floor_is_no_opening_whatever_its_ring_spans() -> None:
    pocket = grid(
        "#######",
        "#.....#",
        "#..?..#",
        "#.....#",
        "#######",
    )
    (ring,) = frontier_groups(pocket)
    assert len(ring) == 4 and opening_cells(pocket, ring) < body_cells(RES, 0.15)
    assert frontier_groups(pocket, min_cells=body_cells(RES, 0.15)) == []


def test_of_two_as_near_the_larger_goes_first_then_the_one_whose_first_cell_comes_first() -> None:
    open_floor = grid(
        ".........",
        ".........",
        ".........",
    )
    start = middle(4, 1)
    left, right = ((0, 1),), ((8, 1), (8, 2))
    ranked = nearest_first(open_floor, start, [left, right], radius_m=0.0)
    assert ranked[0].distance_m == pytest.approx(ranked[1].distance_m)
    assert [k.cells for k in ranked] == [right, left], "the larger of two as near"
    lower, upper = ((4, 0),), ((4, 2),)
    ranked = nearest_first(open_floor, start, [upper, lower, ((8, 1),), left], radius_m=0.0)
    assert [k.cells for k in ranked] == [lower, upper, left, ((8, 1),)]


def test_a_frontier_given_up_is_never_chosen_again_in_that_map_and_a_new_map_forgets_it() -> None:
    given_up = GivenUp()
    groups = frontier_groups(TWO_ROOMS, given_up.in_frame("map:a"))
    behind = next(g for g in groups if (10, 5) in g)
    given_up.in_frame("map:a").update(world_cell(TWO_ROOMS, cell) for cell in behind)
    left = frontier_groups(TWO_ROOMS, given_up.in_frame("map:a"))
    assert [g for g in left if (10, 5) in g] == [] and len(left) == 1
    # the same cells in a grid that grew a cell to the left and below are the same cells
    moved = grid(*["#" + row for row in TWO_ROOMS_ROWS], "#" * 15, origin=(-RES, -RES))
    shifted = (10 + 1, 5 + 1)
    assert world_cell(moved, shifted) == world_cell(TWO_ROOMS, (10, 5))
    assert any(shifted in g for g in frontier_groups(moved))
    assert not any(shifted in g for g in frontier_groups(moved, given_up.in_frame("map:a")))
    # a map of its own, after a wipe say, starts with nothing given up, and the old one is gone
    assert any(shifted in g for g in frontier_groups(moved, given_up.in_frame("map:b")))
    assert given_up.in_frame("map:a") == set()


def test_a_look_gives_up_only_the_frontier_it_did_not_see_past() -> None:
    before = TWO_ROOMS
    behind = next(g for g in frontier_groups(before) if (10, 5) in g)
    looked = grid(
        "##############",
        "#........#...#",
        "#........#.#.#",
        "#?.......#...#",
        "#........#...#",
        "#........#...#",
        "#............#",
        "##############",
    )
    assert still_unseen_beside(looked, [world_cell(before, c) for c in behind]) == set()
    assert still_unseen_beside(before, [world_cell(before, c) for c in behind]) == {
        world_cell(before, c) for c in behind
    }


def test_a_sensor_45_degrees_wide_looks_40_degrees_apart_as_a_camera_does() -> None:
    assert views_round(45.0) == (40.0, 9)
    assert scan_steps(SearchScanParams(), 45.0) == (40.0, min(MAX_SCAN_STEPS, 9))
    assert views_round(90.0) == (72.0, 5)


def test_of_two_views_as_near_the_smaller_heading_goes_first_whatever_the_last_bit() -> None:
    """A stop's views lie whole steps either side of the way the robot faces, so each turn to the
    left has one to the right as long. A yaw read a last bit or three off, as one platform's libm
    gives it and another's does not, must not choose between the two: a run on Linux once turned
    the other way from the same run on Windows and macOS, and every look after differed."""
    step = math.radians(views_round(45.0)[0])
    for yaw in (0.3, 1.1, 2.0, 2.9, -0.7, -1.9, -3.0):
        for k in (1, 2, 3, 4):
            pair = [
                math.remainder(yaw + k * step, math.tau),
                math.remainder(yaw - k * step, math.tau),
            ]
            nearer = math.remainder(yaw + (k - 1) * step, math.tau)
            for ulps in range(-3, 4):
                read = yaw
                for _ in range(abs(ulps)):
                    read = math.nextafter(read, math.copysign(math.inf, ulps))
                assert nearest_view(pair, read) == min(pair)
                assert nearest_view(pair[::-1], read) == min(pair)
                assert nearest_view([*pair, nearer], read) == nearer


def test_a_ray_stops_at_the_first_wall_and_runs_its_range_without_one() -> None:
    corridor = grid(
        "##########",
        "#...#....#",
        "##########",
    )
    x, y = middle(1, 1)
    face = 4 * RES - x
    seen = ray_to_wall(corridor, x, y, 0.0, 1.0)
    assert face <= seen <= face + RES / 2 + 1e-9
    assert ray_to_wall(corridor, x, y, 0.0, face / 2) == face / 2


def test_only_the_views_that_would_see_floor_nobody_has_seen_are_worth_taking() -> None:
    room = grid(
        "#########",
        "#.......?",
        "#.......?",
        "#.......?",
        "#.......?",
        "#.......?",
        "#########",
    )
    x, y = middle(4, 3)
    fov, reach = math.radians(45.0), 1.0
    headings = views_worth_taking(room, x, y, 0.0, fov_rad=fov, range_m=reach, radius_m=0.15)
    assert 0.0 in headings
    assert all(math.cos(h) > 0 for h in headings), "only those facing the open side"
    assert unseen_in_view(room, x, y, math.pi, fov_rad=fov, range_m=reach) == 0
    assert fan(fov, reach, RES) == math.ceil(fov * reach / RES)


def test_the_walk_to_every_cell_keeps_off_floor_nobody_has_seen_and_cuts_no_corner() -> None:
    walls = grid(
        "#####",
        "#.#.#",
        "#.?.#",
        "#####",
    )
    far = walls.distances(middle(1, 1), radius_m=0.0)
    assert far[1, 1] == 0.0 and far[2, 1] == pytest.approx(RES)
    assert math.isinf(far[1, 2]), "floor nobody has seen is not walked"
    assert math.isinf(far[1, 3]), "nor the floor past it"
    corner = grid(
        "####",
        "##.#",
        "#.##",
        "####",
    )
    far = corner.distances(middle(1, 1), radius_m=0.0)
    assert math.isinf(far[2, 2]), "two cells that meet at a corner between two solid ones"


def test_a_walk_that_starts_close_to_a_wall_may_leave_it() -> None:
    """A body stands nearer a wall than the band a wall is grown by, as a place it stood at may
    be: the search leaves the band from there, as a walk to a place does."""
    room = grid(
        "########",
        "#......#",
        "#......#",
        "#......#",
        "#......#",
        "########",
    )
    far = room.distances(middle(1, 1), radius_m=0.1)
    assert math.isfinite(far[3, 4])
