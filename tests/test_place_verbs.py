"""The verbs that name places and list them, through the executor as a pilot reaches them: where
they exist, what they say, what they leave in the store, and what reaches the pilot's text."""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from quackd.agent.prompts import build_observation_text, places_section
from quackd.drive.run import DriveArgs, run_drive
from quackd.memory import RobotMemory
from quackd.safety import Executor
from quackd.space.places import PlaceBook, PlaceStore, places_line
from quackd.space.pose import Fix, FrameRef, Pose2D
from quackd.space.source import SpatialSourceBase
from quackd.transport.base import DuckState
from quackd.transport.mock import MockTransport
from quackd.verbs.registry import VerbRegistry, registry_from_manifest
from quackd.verdict import BEFORE_VERDICT, MOVES_THE_BODY
from quackd_microduck import (
    MICRODUCK_VERBS,
    MicroduckAdapter,
    microduck_conditions,
    microduck_manifest,
)

ODOM = FrameRef("odometry", "odom:test", persistent=False)


def placed_registry() -> VerbRegistry:
    """The Microduck's vocabulary on a backend that says where it is, place verbs included."""
    return registry_from_manifest(
        microduck_manifest("mock"),
        implementations=MICRODUCK_VERBS,
        conditions=microduck_conditions(),
    )


class Source(SpatialSourceBase):
    """A source whose reading a test sets."""

    def __init__(self, fix: Fix) -> None:
        self.reading = fix

    def fix(self) -> Fix:
        return self.reading


class Placed(MockTransport):
    """A mock whose `spatial` is a source the test drives."""

    def __init__(self, source: Source) -> None:
        super().__init__()
        self.spatial = source  # type: ignore[assignment]


def at(x: float, y: float, yaw: float = 0.0, frame: FrameRef = ODOM) -> Fix:
    return Fix(Pose2D(x, y, yaw), frame.kind, frame, walked_m=1.5)


def executor(source: Source, tmp_path: Path) -> Executor:
    return Executor(
        placed_registry(),
        Placed(source),
        places=PlaceStore("microduck:test", tmp_path),
    )


async def test_marking_a_place_takes_the_robots_own_reading_and_keeps_it(tmp_path: Path) -> None:
    source = Source(at(1.25, -0.5, 0.3))
    ex = executor(source, tmp_path)
    result = await ex.run_verb("mark_place", {"name": "sofa", "note": "left end"})
    assert result.ok, result.summary
    assert result.summary.startswith("marked 'sofa' where the robot stands, (1.25, -0.50) by its")
    assert "Odometry drifts with every metre walked" in result.summary
    (place,) = PlaceStore("microduck:test", tmp_path).places()
    assert (place.x, place.y, place.yaw, place.frame) == (1.25, -0.5, 0.3, ODOM.id)
    assert place.note == "left end"


async def test_mark_place_takes_no_coordinates_from_anybody(tmp_path: Path) -> None:
    """A place is where the robot stood: a pilot that names coordinates is refused at the
    schema, so a place can never be somewhere the robot has not been."""
    ex = executor(Source(at(0.0, 0.0)), tmp_path)
    result = await ex.run_verb("mark_place", {"name": "far", "x": 5.0, "y": 5.0})
    assert not result.ok and "invalid params" in result.summary
    assert PlaceStore("microduck:test", tmp_path).places() == []


async def test_nothing_is_marked_while_the_robot_is_lost(tmp_path: Path) -> None:
    ex = executor(Source(Fix(None, "odometry", ODOM, lost="picked up")), tmp_path)
    result = await ex.run_verb("mark_place", {"name": "here"})
    assert not result.ok and "picked up" in result.summary


async def test_map_says_where_by_what_and_every_place_with_why_it_cannot_be_reached(
    tmp_path: Path,
) -> None:
    source = Source(at(0.0, 0.0, 0.0))
    ex = executor(source, tmp_path)
    source.reading = at(1.0, 0.0)
    await ex.run_verb("mark_place", {"name": "door"})
    source.reading = at(0.0, 0.0, frame=FrameRef("odometry", "odom:test", False, epoch=1))
    await ex.run_verb("mark_place", {"name": "chair"})
    source.reading = at(0.0, 1.0, frame=FrameRef("odometry", "odom:test", False, epoch=1))
    result = await ex.run_verb("map", {})
    assert result.ok, result.summary
    lines = result.summary.splitlines()
    assert lines[0] == "where: odometry(0.00, 1.00, 0.00 rad, walked 1.5 m), by Source"
    assert "- chair: 1.00 m away, 90° to the right" in lines, "facing x, with it straight below"
    assert "- door: orphaned, it was marked on odometry that has started again since" in lines
    assert lines[-1] == "no map to draw: Source keeps none, only a position"
    statuses = {p["name"]: p["status"] for p in result.data["places"]}
    assert statuses == {"chair": "here", "door": "orphaned"}


async def test_forgetting_a_place_says_which_places_there_are_when_it_has_no_such(
    tmp_path: Path,
) -> None:
    ex = executor(Source(at(0.0, 0.0)), tmp_path)
    await ex.run_verb("mark_place", {"name": "sofa"})
    missing = await ex.run_verb("forget_place", {"name": "bed"})
    assert not missing.ok and missing.summary.endswith("(places: sofa)")
    gone = await ex.run_verb("forget_place", {"name": "SOFA"})
    assert gone.ok and gone.summary == "forgot 'sofa'"


async def test_the_place_verbs_move_nothing_and_run_before_a_verdict_and_while_paused(
    tmp_path: Path,
) -> None:
    from quackd.safety import WHILE_PAUSED

    for name in ("map", "mark_place", "forget_place"):
        assert name in BEFORE_VERDICT and name in WHILE_PAUSED and name not in MOVES_THE_BODY
    source = Source(at(0.0, 0.0))
    ex = executor(source, tmp_path)
    ex.require_verdict = True
    ex.pause_for(5.0)
    for name, params in (
        ("mark_place", {"name": "x"}),
        ("map", {}),
        ("forget_place", {"name": "x"}),
    ):
        assert (await ex.run_verb(name, params)).ok, name
    assert ex._moving == set() and Placed(source).intents == []


async def test_a_dry_run_marks_nothing(tmp_path: Path) -> None:
    source = Source(at(0.0, 0.0))
    ex = Executor(
        placed_registry(),
        Placed(source),
        dry_run=True,
        places=PlaceStore("microduck:test", tmp_path),
    )
    result = await ex.run_verb("mark_place", {"name": "sofa"})
    assert result.ok and result.data.get("dry_run")
    assert not (tmp_path / "microduck-test.places.jsonl").exists()


async def test_a_body_that_does_not_say_where_it_is_has_no_place_verbs() -> None:
    """The Microduck declares them, as it declares its camera's, and a connection whose
    transport carries no reading leaves them out, so a pilot is never offered a verb that could
    only say it has no places."""
    plain = await MicroduckAdapter(MockTransport()).connect()
    assert not {"map", "mark_place", "forget_place"} & {v.name for v in plain.verbs}
    placed = await MicroduckAdapter(MockTransport(spatial=True)).connect()
    assert {"map", "mark_place", "forget_place"} <= {v.name for v in placed.verbs}


async def test_a_verb_with_no_reading_to_read_says_so(tmp_path: Path) -> None:
    ex = Executor(placed_registry(), MockTransport(), places=PlaceStore("r", tmp_path))
    result = await ex.run_verb("map", {})
    assert not result.ok and "does not say where it is" in result.summary


def test_the_pilot_reads_the_nearest_places_and_the_orphaned_after_them(tmp_path: Path) -> None:
    source = Source(at(0.0, 0.0))
    book = PlaceBook(PlaceStore("r", tmp_path), source)
    source.reading = at(1.0, 1.0)
    book.mark("kitchen")
    source.reading = at(0.0, 0.0, frame=FrameRef("odometry", "odom:test", False, epoch=1))
    book.mark("door")
    line = places_line(book.views())
    assert line == "places: door here · orphaned: kitchen"
    text = build_observation_text(
        step=1,
        max_steps=40,
        state=DuckState(),
        detections=[],
        last_verb=None,
        last_result=None,
        budget_status="step 1/40",
        places=line,
    )
    assert line in text.splitlines()
    without = build_observation_text(
        step=1,
        max_steps=40,
        state=DuckState(),
        detections=[],
        last_verb=None,
        last_result=None,
        budget_status="step 1/40",
    )
    assert "places:" not in without, "a body with no places reads exactly as before"


def test_the_places_section_is_there_only_when_a_place_verb_is() -> None:
    assert places_section(["move", "stop"]) == ""
    section = places_section(["map", "mark_place", "stop"])
    assert "## Places" in section and "`mark_place`" in section and "`forget_place`" not in section
    assert "drifts a little with every metre walked" in section


async def test_a_note_saved_where_the_robot_knew_says_where_when_recalled(tmp_path: Path) -> None:
    source = Source(at(0.0, 0.0))
    book = PlaceBook(PlaceStore("r", tmp_path), source)
    memory = RobotMemory("r", tmp_path)
    source.reading = at(2.0, 0.0)
    book.mark("sofa")
    memory.remember(
        "the ball rolls under the sofa",
        where={"kind": "odometry", "frame": ODOM.id, "x": 2.3, "y": 0.0},
    )
    memory.remember("an old note from before places")
    source.reading = at(0.0, 0.0)
    recalled = memory.recall(where_said=book.where_words)
    assert "the ball rolls under the sofa (saved 0.3 m from sofa, 2.3 m from here)" in recalled
    assert any(line.endswith("] an old note from before places") for line in recalled.splitlines())
    source.reading = at(0.0, 0.0, frame=FrameRef("odometry", "odom:test", False, epoch=1))
    later = memory.recall(where_said=book.where_words)
    assert "(saved in a frame that has ended since, so where is not known now)" in later


@pytest.mark.parametrize("persist", [True, False])
def test_an_old_memory_file_loads_and_a_where_round_trips(tmp_path: Path, persist: bool) -> None:
    memory = RobotMemory("r", tmp_path)
    memory.path.parent.mkdir(parents=True, exist_ok=True)
    memory.path.write_text('{"kind": "note", "text": "old", "ts": 1.0}\n', encoding="utf-8")
    assert memory.notes()[0].where is None
    where = {"kind": "map", "frame": "map:m", "x": 1.0, "y": 2.0, "yaw": 0.0}
    memory.remember("new", where=where if persist else None)
    reread = RobotMemory("r", tmp_path).notes()[-1]
    assert reread.where == (where if persist else None)


async def test_a_place_a_person_marks_while_driving_is_there_for_the_next_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A drive session kept what it marked for as long as it lasted: the next session, and a run
    sent to walk there, found no such place. Kept beside memory now, as a run's and an MCP
    session's are, and a dry session, which runs no `mark_place`, leaves them as they were."""
    home = tmp_path / "memory"
    monkeypatch.setenv("QUACKD_MEMORY_DIR", str(home))

    async def drive(lines: str, *, dry_run: bool = False) -> None:
        args = DriveArgs(
            robot="microduck:mock", llm="fake", dry_run=dry_run, runs_dir=str(tmp_path / "runs")
        )
        await run_drive(args, say=lambda _line: None, stream=io.StringIO(lines), keyboard=False)

    await drive("mark_place sofa\nquit\n")
    assert [p.name for p in PlaceStore("microduck:mock", home).places()] == ["sofa"]
    await drive("mark_place hall\nquit\n", dry_run=True)
    assert [p.name for p in PlaceStore("microduck:mock", home).places()] == ["sofa"]
