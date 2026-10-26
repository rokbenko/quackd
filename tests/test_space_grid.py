"""An occupancy grid read from whoever made one: parsing in the source's own alphabet, the
coarser views, the walls a body has to keep clear of, and the walk through it."""

from __future__ import annotations

import math

import numpy as np
import pytest

from quackd.space.grid import FREE, OCCUPIED, UNKNOWN, OccupancyGrid, Plan
from quackd.space.pose import Pose2D

ALPHABET = {"?": UNKNOWN, ".": FREE, "#": OCCUPIED}


def grid(*top_down: str, res: float = 0.1) -> OccupancyGrid:
    """A grid drawn the way it reads, the top line the highest y. The text a source sends is
    row-major from the lowest y, so the lines are reversed on the way in."""
    rows = list(reversed(top_down))
    return OccupancyGrid.from_text(
        "".join(rows),
        width=len(rows[0]),
        height=len(rows),
        res_m=res,
        origin=(0.0, 0.0),
        alphabet=ALPHABET,
    )


def centre(g: OccupancyGrid, col: int, row_from_top: int) -> tuple[float, float]:
    return g.center_of((col, g.height - 1 - row_from_top))


def crosses_solid(g: OccupancyGrid, start: tuple[float, float], plan: Plan) -> bool:
    """Whether any leg of the walk passes over a solid cell, sampled finer than a cell."""
    last = start
    for point in plan.waypoints:
        n = max(1, math.ceil(math.dist(last, point) / (g.res_m / 8)))
        for i in range(n + 1):
            x = last[0] + (point[0] - last[0]) * i / n
            y = last[1] + (point[1] - last[1]) * i / n
            if g.state_at(x, y) == OCCUPIED:
                return True
        last = point
    return False


def test_a_grid_is_read_in_the_alphabet_its_source_names() -> None:
    g = grid("?.#", "##.")
    assert g.width == 3 and g.height == 2
    assert g.cells.tolist() == [[OCCUPIED, OCCUPIED, FREE], [UNKNOWN, FREE, OCCUPIED]]
    with pytest.raises(ValueError, match="'x' is not one the source said it sends"):
        OccupancyGrid.from_text(
            "..x", width=3, height=1, res_m=0.1, origin=(0.0, 0.0), alphabet=ALPHABET
        )
    with pytest.raises(ValueError, match="needs 4 cells, and 3 came"):
        OccupancyGrid.from_text(
            "...", width=2, height=2, res_m=0.1, origin=(0.0, 0.0), alphabet=ALPHABET
        )
    with pytest.raises(ValueError, match="ASCII"):
        OccupancyGrid.from_text(
            ".é", width=2, height=1, res_m=0.1, origin=(0.0, 0.0), alphabet=ALPHABET
        )


def test_a_coarser_view_keeps_solid_over_floor_over_unseen() -> None:
    g = grid("?.?", "..#", "???")
    coarse = g.downsample(2)
    assert coarse.res_m == pytest.approx(0.2)
    # blocks from the lowest y: [???/..] has floor, [?/#] has something solid, [?./??] floor
    assert coarse.cells.tolist() == [[FREE, OCCUPIED], [FREE, UNKNOWN]]
    assert g.downsample(1) is g


def test_walls_are_grown_by_the_radius_and_one_cell_more() -> None:
    g = grid(".......", ".......", "...#...", ".......", ".......")
    grown = g.inflated(0.1)  # one cell of radius, plus one
    assert grown.sum() == 1 + 4 + 4 + 4, "a disc of two cells around the one solid cell"
    assert not grown[2, 0] and grown[2, 1] and grown[0, 3] and not grown[0, 1]
    assert (g.inflated(0.0) == (g.cells == OCCUPIED)).all()


def test_the_walk_goes_through_the_doorway_and_never_through_the_wall() -> None:
    g = grid(
        ".........#.........",
        ".........#.........",
        ".........#.........",
        "...................",
        "...................",
        "...................",
        "...................",
        "...................",
        "...................",
        ".........#.........",
        ".........#.........",
        ".........#.........",
    )
    start, goal = centre(g, 2, 1), centre(g, 16, 10)
    plan = g.plan(start, goal, radius_m=0.04)
    assert plan is not None
    assert plan.waypoints[-1] == goal
    assert plan.unseen_m == 0.0
    assert not crosses_solid(g, start, plan)
    assert plan.length_m >= math.dist(start, goal)
    # the doorway above is six cells tall, and a wall grown by two cells either side leaves two;
    # a doorway one cell tall leaves none (round the wall's ends is unseen floor, kept out here)
    narrow = grid(
        ".........#.........",
        ".........#.........",
        "...................",
        ".........#.........",
        ".........#.........",
    )
    start_n, goal_n = centre(narrow, 2, 0), centre(narrow, 16, 4)
    assert narrow.plan(start_n, goal_n, radius_m=0.04, unknown_blocks=True) is None
    assert narrow.plan(start_n, goal_n, radius_m=0.0, unknown_blocks=True) is not None


def test_the_walk_takes_seen_floor_round_an_unseen_patch_rather_than_across_it() -> None:
    g = grid(
        "...........",
        ".?????????.",
        ".?????????.",
        ".?????????.",
        "...........",
    )
    start, goal = centre(g, 0, 2), centre(g, 10, 2)
    plan = g.plan(start, goal, radius_m=0.0)
    assert plan is not None and plan.unseen_m == 0.0
    assert plan.length_m > math.dist(start, goal), "round the patch, though across is shorter"
    assert all(g.state_at(x, y) == FREE for x, y in plan.waypoints)


def test_unseen_floor_is_crossed_only_where_it_must_be_and_the_walk_says_how_much() -> None:
    g = grid(
        "###########",
        "....???....",
        "###########",
    )
    start, goal = centre(g, 0, 1), centre(g, 10, 1)
    plan = g.plan(start, goal, radius_m=0.0)
    assert plan is not None
    assert plan.unseen_m == pytest.approx(0.3), "three cells of unseen floor, a decimetre each"
    assert g.plan(start, goal, radius_m=0.0, unknown_blocks=True) is None


def test_a_goal_in_a_wall_or_walled_off_has_no_walk() -> None:
    g = grid(
        ".......",
        ".#####.",
        ".#...#.",
        ".#####.",
        ".......",
    )
    start = centre(g, 0, 0)
    assert g.plan(start, centre(g, 3, 1), radius_m=0.0) is None, "into the wall itself"
    assert g.plan(start, centre(g, 3, 2), radius_m=0.0) is None, "a room with no way in"


def test_a_walk_that_starts_against_a_wall_still_gets_away_from_it() -> None:
    g = grid(
        "........",
        "........",
        "........",
        "########",
    )
    start = centre(g, 1, 2)  # one cell above the wall, well inside the band it is grown by
    plan = g.plan(start, centre(g, 6, 0), radius_m=0.1)
    assert plan is not None
    assert not crosses_solid(g, start, plan)


def test_a_goal_past_the_edge_of_the_map_is_unseen_floor_to_walk_over() -> None:
    g = grid("....", "....")
    start = centre(g, 0, 0)
    goal = (0.75, 0.15)  # three cells beyond the right edge
    plan = g.plan(start, goal, radius_m=0.0)
    assert plan is not None and plan.unseen_m > 0.0
    assert plan.waypoints[-1] == goal
    assert g.plan(start, goal, radius_m=0.0, unknown_blocks=True) is None


def test_cells_a_caller_has_its_own_reason_to_avoid_are_walked_round() -> None:
    g = grid(
        ".....",
        ".....",
        ".....",
    )
    start, goal = centre(g, 0, 1), centre(g, 4, 1)
    straight = g.plan(start, goal, radius_m=0.0)
    assert straight is not None and len(straight.waypoints) == 1
    middle = (2, g.height - 1 - 1)
    around = g.plan(start, goal, radius_m=0.0, extra_blocked=[middle])
    assert around is not None and len(around.waypoints) > 1
    assert around.length_m > straight.length_m


def test_frontiers_are_floor_beside_unseen_floor_the_edge_included() -> None:
    g = grid(
        "?..",
        "...",
        "..#",
    )
    cells = set(g.frontiers())
    assert (1, 2) in cells and (0, 1) in cells, "beside the unseen corner"
    assert (0, 0) in cells, "on the edge, which counts as unseen"
    assert (1, 1) not in cells, "floor with floor and wall all round"
    assert all(g.cells[r, c] == FREE for c, r in cells)


def test_the_map_reads_top_line_highest_with_the_robot_an_arrow_and_places_lettered() -> None:
    g = grid(
        "????",
        "#..#",
        "#..#",
    )
    text = g.render_text(
        cells_across=10,
        robot=Pose2D(0.15, 0.05, math.pi / 2),
        places=[("door", 0.25, 0.15)],
    )
    assert text.splitlines() == [
        "map: 4 x 3 cells of 10 cm, x right, y up (the map has no north)",
        "# solid · . floor seen empty · ? not seen · > ^ < v the robot, pointing the way it faces",
        "????",
        "#.A#",
        "#^.#",
        "places: A door",
    ]


def test_a_wide_map_is_shown_in_coarser_cells() -> None:
    g = OccupancyGrid(0.05, (0.0, 0.0), np.ones((4, 40), dtype=np.uint8))
    first = g.render_text(cells_across=10).splitlines()[0]
    assert first == "map: 10 x 1 cells of 20 cm, x right, y up (the map has no north)"
