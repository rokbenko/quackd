"""Rooms for the simulators (`quackd.space.room`): the files, the geometry, and the ray a camera
column and a map's zone are cast along."""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pytest

from quackd.sim2d.world import DUCK_R
from quackd.space.room import BUILT_IN, Box, Room, RoomError, Wall, load_room

ROOM = Room(
    "test",
    half_w=1.5,
    half_h=1.0,
    walls=(Wall(0.0, -1.0, 0.0, -0.3), Wall(0.0, 0.3, 0.0, 1.0)),
    boxes=(Box(1.0, 0.6, 0.4, 0.3),),
)


def _marched(room: Room, x: float, y: float, heading: float, max_range: float) -> float | None:
    """The first point along the ray that is solid, found the slow way, a millimetre at a time."""
    c, s = math.cos(heading), math.sin(heading)
    for k in range(1, round(max_range / 0.001) + 1):
        r = k * 0.001
        if room.blocked(x + c * r, y + s * r):
            return r
    return None


@pytest.mark.parametrize("name", BUILT_IN)
def test_every_built_in_room_loads_and_starts_the_duck_on_free_floor(name: str) -> None:
    room = load_room(name)
    assert room.name == name, "a room's file and its name are one"
    if room.start is not None:
        x, y, _theta = room.start
        assert not room.blocked(x, y, DUCK_R), f"{name} starts the duck inside something"


def test_a_ray_stops_at_the_face_of_whatever_it_meets_first() -> None:
    # straight at a wall's centre line, half its thickness short of it
    assert ROOM.raycast(-1.0, -0.6, 0.0, 5.0) == pytest.approx(1.0 - 0.02)
    # at 45 degrees the face is farther along the ray by the same half thickness over the sine
    assert ROOM.raycast(-0.5, -0.8, math.pi / 4, 5.0) == pytest.approx(0.48 * math.sqrt(2))
    # through the doorway to the far wall, and up to the outer wall
    assert ROOM.raycast(-1.0, 0.0, 0.0, 5.0) == pytest.approx(2.5)
    assert ROOM.raycast(0.5, 0.0, math.pi / 2, 5.0) == pytest.approx(1.0)
    # the near face of a box
    assert ROOM.raycast(0.5, 0.6, 0.0, 5.0) == pytest.approx(0.3)
    # nothing within the range
    assert ROOM.raycast(-1.0, 0.0, 0.0, 2.0) is None


def test_a_ray_says_whether_it_met_a_wall_or_furniture() -> None:
    assert ROOM.cast(0.5, 0.6, 0.0, 5.0) == (pytest.approx(0.3), "box")
    assert ROOM.cast(-1.0, -0.6, 0.0, 5.0) == (pytest.approx(0.98), "wall")
    assert ROOM.cast(0.5, 0.0, math.pi / 2, 5.0) == (pytest.approx(1.0), "wall")


def test_the_cast_ray_is_the_one_a_march_finds_from_anywhere_on_the_floor() -> None:
    """Worked out rather than marched, so it is checked against a march: from points across the
    floor, in every direction, the two agree to the march's step."""
    rng = np.random.default_rng(7)
    checked = 0
    while checked < 200:
        x, y = float(rng.uniform(-1.5, 1.5)), float(rng.uniform(-1.0, 1.0))
        if ROOM.blocked(x, y):
            continue
        heading = float(rng.uniform(-math.pi, math.pi))
        cast, marched = ROOM.raycast(x, y, heading, 4.0), _marched(ROOM, x, y, heading, 4.0)
        assert (cast is None) == (marched is None), (x, y, heading)
        if cast is not None and marched is not None:
            assert cast == pytest.approx(marched, abs=0.0015), (x, y, heading)
        checked += 1


def test_clearance_is_how_far_the_nearest_solid_thing_is() -> None:
    assert ROOM.clearance(-1.0, 0.0) == pytest.approx(0.5)  # the outer wall, and not the doorway
    assert ROOM.clearance(0.5, 0.6) == pytest.approx(0.3)  # the box's near face
    assert ROOM.clearance(1.0, 0.6) < 0, "inside the box"
    assert ROOM.blocked(0.0, -0.5) and not ROOM.blocked(0.0, 0.0)
    assert not ROOM.blocked(-0.05, -0.5) and ROOM.blocked(-0.05, -0.5, radius=0.1), (
        "a disc can reach a wall its centre is not in"
    )


def test_a_rooms_fingerprint_is_its_geometry_and_nothing_else() -> None:
    """A simulated map's frame is named by it, so a place marked in a room is in the same frame
    whoever starts the duck where."""
    same = Room("renamed", 1.5, 1.0, ROOM.walls, ROOM.boxes, start=(-1.0, 0.0, 0.0))
    assert same.digest() == ROOM.digest()
    moved = Room("test", 1.5, 1.0, ROOM.walls, (Box(1.1, 0.6, 0.4, 0.3),))
    assert moved.digest() != ROOM.digest()


@pytest.mark.parametrize(
    ("text", "said"),
    [
        ("name: x\nsize: [2, 2]\ncolour: red\n", "unknown keys ['colour']"),
        ("name: x\nsize: [2]\n", "size is 2 numbers"),
        ("name: x\nsize: [2, -1]\n", "size is two lengths above zero"),
        ("name: x\nsize: [2, 2]\nwalls:\n  - [0, 0, 1]\n", "walls[0] is 4 numbers"),
        ("name: x\nsize: [2, 2]\nboxes:\n  - [0, 0, 1, true]\n", "boxes[0] is 4 numbers"),
        ("name: x\nsize: [2, 2]\nboxes:\n  - [0, 0, 1, 1]\nstart: [0, 0, 0]\n", "start is inside"),
        ("name: x\nsize: [2, 2]\nball: 3\n", "ball is a spot"),
        ("- a list\n", "a room is a mapping"),
        ("size: [2, 2]\n", "name is a word"),
    ],
)
def test_a_room_files_mistake_names_the_file_and_the_key(
    tmp_path: Path, text: str, said: str
) -> None:
    path = tmp_path / "mine.yaml"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(RoomError) as caught:
        load_room(path)
    assert str(path) in str(caught.value) and said in str(caught.value)


def test_a_room_that_is_not_there_says_which_rooms_are() -> None:
    with pytest.raises(RoomError, match="no room called 'kitchen'") as caught:
        load_room("kitchen")
    assert all(name in str(caught.value) for name in BUILT_IN)
