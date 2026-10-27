"""Places a pilot or a person named: the file beside memory, the names it takes, and what a
place is worth in the frame the robot is in now."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from quackd.memory import RobotMemory
from quackd.space.places import (
    MAX_PLACES,
    FrameRecord,
    Place,
    PlaceBook,
    PlaceStore,
    clean_name,
    place_listing,
    places_line,
)
from quackd.space.pose import Fix, FrameRef, Pose2D
from quackd.space.source import SpatialError, SpatialSourceBase

ODOM = FrameRef("odometry", "odom:c1", persistent=False)
MAP = FrameRef("map", "map:m1", persistent=True)


class Source(SpatialSourceBase):
    """A source whose reading a test sets."""

    def __init__(self, fix: Fix) -> None:
        self.reading = fix

    def fix(self) -> Fix:
        return self.reading


def at(x: float, y: float, yaw: float = 0.0, *, frame: FrameRef = ODOM, walked: float = 0.0) -> Fix:
    return Fix(Pose2D(x, y, yaw), frame.kind, frame, walked_m=walked)


def test_places_live_beside_memory_and_never_in_its_file(tmp_path: Path) -> None:
    store = PlaceStore("microduck:sim2d", tmp_path)
    memory = RobotMemory("microduck:sim2d", tmp_path)
    assert store.path == tmp_path / "microduck-sim2d.places.jsonl"
    assert store.path != memory.path
    book = PlaceBook(store, Source(at(1.0, 2.0)))
    book.mark("kitchen")
    memory.remember("the ball rolls under the sofa")
    again = PlaceStore("microduck:sim2d", tmp_path)
    assert [p.name for p in again.places()] == ["kitchen"]
    assert len(memory.notes()) == 1


def test_a_mark_is_where_the_robot_stands_in_the_frame_it_stands_in(tmp_path: Path) -> None:
    book = PlaceBook(PlaceStore("r", tmp_path), Source(at(0.5, -1.0, 1.2, walked=4.2)))
    place, old = book.mark("sofa", note="  the  left end ")
    assert old is None
    assert (place.x, place.y, place.yaw) == (0.5, -1.0, 1.2)
    assert place.frame == ODOM.id and place.kind == "odometry"
    assert place.walked_m == 4.2, "how far it had walked, which says how far it may drift"
    assert place.note == "the left end"
    mapped = PlaceBook(PlaceStore("m", tmp_path), Source(at(0.0, 0.0, frame=MAP, walked=9.0)))
    assert mapped.mark("door")[0].walked_m is None, "a map corrects itself: no drift to count"


def test_nothing_is_marked_while_the_robot_does_not_know_where_it_is(tmp_path: Path) -> None:
    book = PlaceBook(PlaceStore("r", tmp_path), Source(Fix(None, "map", MAP, lost="carried")))
    with pytest.raises(ValueError, match=r"does not know where it is \(carried\)"):
        book.mark("hall")
    assert book.store.places() == []


def test_a_name_is_one_place_whatever_its_case_and_marking_it_again_moves_it(
    tmp_path: Path,
) -> None:
    source = Source(at(1.0, 1.0))
    book = PlaceBook(PlaceStore("r", tmp_path), source)
    book.mark("Kitchen Door")
    source.reading = at(2.0, 3.0)
    _, old = book.mark("kitchen  door")
    assert old is not None and (old.x, old.y) == (1.0, 1.0)
    assert [(p.name, p.x, p.y) for p in book.store.places()] == [("kitchen door", 2.0, 3.0)]
    assert book.store.forget("KITCHEN DOOR") is not None
    assert book.store.places() == []
    assert book.store.forget("kitchen door") is None


@pytest.mark.parametrize("name", ["", " ", "a" * 33, "-sofa", "sofa·bed", "<script>"])
def test_a_name_has_to_be_one_a_map_can_print(name: str) -> None:
    with pytest.raises(ValueError, match="a place's name is"):
        clean_name(name)


def test_the_spaces_in_a_name_are_made_one_as_a_note_s_are() -> None:
    assert clean_name("  sofa\n  bed ") == "sofa bed"
    assert clean_name("Rok's chair") == "Rok's chair"


def test_start_is_quackds_own_to_mark(tmp_path: Path) -> None:
    book = PlaceBook(PlaceStore("r", tmp_path), Source(at(0.0, 0.0)))
    with pytest.raises(ValueError, match="where an explore or a find began"):
        book.mark("Start")
    place, _ = book.mark("start", reserved_ok=True)
    assert place.name == "start"


def test_a_robot_keeps_a_hundred_places_and_says_so_at_the_next(tmp_path: Path) -> None:
    book = PlaceBook(PlaceStore("r", tmp_path, persist=False), Source(at(0.0, 0.0)))
    for i in range(MAX_PLACES):
        book.mark(f"p{i}")
    with pytest.raises(ValueError, match=f"keeps {MAX_PLACES} places at most"):
        book.mark("one more")
    book.mark("p7")  # moving one it has is not a hundred and first
    assert len(book.store.places()) == MAX_PLACES


def test_a_store_told_not_to_persist_writes_nothing(tmp_path: Path) -> None:
    store = PlaceStore("r", tmp_path, persist=False)
    PlaceBook(store, Source(at(0.0, 0.0))).mark("here")
    assert [p.name for p in store.places()] == ["here"]
    assert not store.path.exists()
    assert not tmp_path.joinpath("r.places.jsonl").exists()


def test_a_place_in_the_frame_the_robot_is_in_is_here_with_where_it_is(tmp_path: Path) -> None:
    source = Source(at(0.0, 0.0, 0.0))
    book = PlaceBook(PlaceStore("r", tmp_path), source)
    source.reading = at(0.0, 2.0)
    book.mark("far")
    source.reading = at(1.0, 0.0)
    book.mark("near")
    source.reading = at(0.0, 0.0, 0.0)
    near, far = book.views()
    assert (near.place.name, near.status, near.distance_m) == ("near", "here", 1.0)
    assert near.bearing_rad == pytest.approx(0.0)
    assert far.status == "here" and far.bearing_rad == pytest.approx(1.5708, abs=1e-4)
    assert [v.place.name for v in book.nearest(1)] == ["near"]


def test_a_place_the_robot_stands_on_is_here_and_no_way_from_it(tmp_path: Path) -> None:
    """The bearing to the spot underfoot is the heading's own noise: a robot facing 46° from the
    way it faced when it marked the spot was told the spot was 46° to its right."""
    source = Source(at(1.0, 1.0, 0.0))
    book = PlaceBook(PlaceStore("r", tmp_path), source)
    book.mark("door")
    source.reading = at(1.0 + 0.002, 1.0, 0.8)
    assert place_listing(book.views()) == ["- door: here, where the robot stands"]
    assert places_line(book.views()) == "places: door here"
    source.reading = at(1.0, 1.3, 0.0)
    assert place_listing(book.views()) == ["- door: 0.30 m away, 90° to the right"]
    assert places_line(book.views()) == "places: door 0.3 m -90°"


def test_a_place_from_odometry_that_started_again_is_orphaned_and_says_why(
    tmp_path: Path,
) -> None:
    source = Source(at(1.0, 1.0))
    book = PlaceBook(PlaceStore("r", tmp_path), source)
    book.mark("sofa")
    picked_up = FrameRef("odometry", "odom:c1", persistent=False, epoch=1)
    source.reading = at(0.0, 0.0, frame=picked_up)
    (view,) = book.views()
    assert view.status == "orphaned"
    assert view.reason == "it was marked on odometry that has started again since"
    assert view.distance_m is None, "a place in another frame is nowhere from here"


def test_a_place_in_a_wiped_map_is_orphaned_for_that_reason(tmp_path: Path) -> None:
    source = Source(at(1.0, 1.0, frame=MAP))
    book = PlaceBook(PlaceStore("r", tmp_path), source)
    book.mark("charger")
    book.store.mark_wiped(MAP.id)
    source.reading = at(0.0, 0.0, frame=FrameRef("map", "map:m2", persistent=True))
    (view,) = book.views()
    assert (view.status, view.reason) == ("orphaned", "its map was wiped")


def test_a_place_waits_while_the_robot_is_lost_in_the_map_it_was_marked_in(
    tmp_path: Path,
) -> None:
    source = Source(at(1.0, 1.0, frame=MAP))
    book = PlaceBook(PlaceStore("r", tmp_path), source)
    book.mark("charger")
    source.reading = Fix(None, "map", MAP, lost="bumped")
    (view,) = book.views()
    assert view.status == "lost" and "bumped" in (view.reason or "")


def test_the_frames_a_place_was_marked_in_are_remembered_with_it(tmp_path: Path) -> None:
    store = PlaceStore("r", tmp_path)
    PlaceBook(store, Source(at(0.0, 0.0, frame=MAP))).mark("door")
    (record,) = store.frames()
    assert (record.id, record.kind, record.persistent) == (MAP.id, "map", True)
    lines = [json.loads(line) for line in store.path.read_text(encoding="utf-8").splitlines()]
    assert {line["type"] for line in lines} == {"frame", "place"}


def test_a_line_a_person_broke_by_hand_is_skipped_and_the_rest_read(tmp_path: Path) -> None:
    store = PlaceStore("r", tmp_path)
    good = Place("door", 1.0, 2.0, 0.0, MAP.id, "map", 1.0).to_json()
    frame = FrameRecord(MAP.id, "map", True, 1.0, ((0.0, 0.0, 0.0), (1.0, 0.5, 0.1))).to_json()
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.path.write_text(
        "not json\n"
        + json.dumps(good)
        + "\n[1, 2]\n"
        + json.dumps({"type": "place", "name": "x", "x": "far"})
        + "\n"
        + json.dumps(frame)
        + "\n",
        encoding="utf-8",
    )
    assert [p.name for p in store.places()] == ["door"]
    (record,) = store.frames()
    assert record.fingerprint == ((0.0, 0.0, 0.0), (1.0, 0.5, 0.1))


async def test_a_source_with_no_map_says_so_when_asked_to_wipe_one() -> None:
    source = Source(at(0.0, 0.0))
    assert await source.grid() is None
    with pytest.raises(SpatialError, match="keeps no map to wipe"):
        await source.wipe()
    assert source.resolve_frame([]) == ODOM
