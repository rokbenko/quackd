"""`quackd map` and `quackd places list|add|rm`: a robot's map and its places, for a person.

`map` and `places add` connect to the robot and read it as the `map` and `mark_place` verbs do;
`list` and `rm` read the place file with nothing connected. A wipe is a person's alone: asked at
a terminal, naming the places it orphans, and refused without one.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

import quackd.cli as cli
from quackd.cli import app
from quackd.space.places import PlaceStore
from quackd.verbs.places import MapParams

runner = CliRunner()
ROOM = ["--robot", "microduck:sim2d", "--room", "two-rooms"]


def _dirs(tmp_path: Path) -> list[str]:
    return ["--memory-dir", str(tmp_path / "memory"), "--registry-dir", str(tmp_path / "registry")]


def _store(tmp_path: Path) -> PlaceStore:
    return PlaceStore("microduck:sim2d", tmp_path / "memory")


def _add(tmp_path: Path, name: str = "sofa", *extra: str) -> Any:
    return runner.invoke(app, ["places", "add", name, *extra, *ROOM, *_dirs(tmp_path)])


def test_a_place_marked_where_the_duck_stands_is_listed_with_its_frame_and_forgotten(
    tmp_path: Path,
) -> None:
    added = _add(tmp_path, "sofa", "--note", "the grey one")
    assert added.exit_code == 0, added.output
    assert "microduck:sim2d: marked 'sofa' where the robot stands" in added.output
    (place,) = _store(tmp_path).places()
    assert place.kind == "map" and place.note == "the grey one"
    listed = runner.invoke(
        app,
        ["places", "list", "--robot", "microduck:sim2d", *_dirs(tmp_path)],
        env={"COLUMNS": "200"},
    )
    assert listed.exit_code == 0, listed.output
    assert "1 place" in listed.output and "the grey one" in listed.output
    assert place.frame in listed.output and "quackd map says which" in listed.output
    removed = runner.invoke(
        app, ["places", "rm", "SOFA", "--robot", "microduck:sim2d", *_dirs(tmp_path)]
    )
    assert removed.exit_code == 0 and "forgot 'sofa'" in removed.output, removed.output
    assert _store(tmp_path).places() == []


def test_listing_a_robot_with_no_places_says_so(tmp_path: Path) -> None:
    listed = runner.invoke(app, ["places", "list", "--robot", "microduck:sim2d", *_dirs(tmp_path)])
    assert listed.exit_code == 0 and "no places kept yet" in listed.output, listed.output


def test_forgetting_a_place_it_does_not_keep_is_one_line_naming_those_it_does(
    tmp_path: Path,
) -> None:
    assert _add(tmp_path, "sofa").exit_code == 0
    gone = runner.invoke(
        app, ["places", "rm", "desk", "--robot", "microduck:sim2d", *_dirs(tmp_path)]
    )
    assert gone.exit_code == 1 and "no place called 'desk' (places: sofa)" in gone.output
    assert "Traceback" not in gone.output


def test_a_body_that_says_nowhere_keeps_no_places_and_is_told_where_it_would(
    tmp_path: Path,
) -> None:
    """The duck's simulator keeps a map only in a room, and says which rooms there are; an arm
    keeps none at all, and is refused before anything connects."""
    roomless = runner.invoke(
        app, ["places", "add", "sofa", "--robot", "microduck:sim2d", *_dirs(tmp_path)]
    )
    assert roomless.exit_code == 1 and "keeps no places" in roomless.output, roomless.output
    assert "--room" in roomless.output and "two-rooms" in roomless.output
    arm = runner.invoke(app, ["map", "--robot", "lerobot:mock", *_dirs(tmp_path)])
    assert arm.exit_code == 1 and "lerobot:mock keeps no places" in arm.output, arm.output
    assert "--room" not in arm.output
    assert _store(tmp_path).frames() == [], "nothing connected, so no frame was seen"


def test_a_name_quackd_keeps_for_itself_or_one_too_long_is_refused_in_one_line(
    tmp_path: Path,
) -> None:
    reserved = _add(tmp_path, "start")
    assert reserved.exit_code == 1 and "quackd marks itself" in reserved.output, reserved.output
    long = _add(tmp_path, "x" * 40)
    assert long.exit_code == 1 and long.output.count("\n") <= 3, long.output
    assert _store(tmp_path).places() == []


def test_map_says_where_the_duck_is_its_places_and_draws_what_it_has_seen(
    tmp_path: Path,
) -> None:
    assert _add(tmp_path, "sofa").exit_code == 0
    shown = runner.invoke(app, ["map", *ROOM, "--cells", "30", *_dirs(tmp_path)])
    assert shown.exit_code == 0, shown.output
    for needle in (
        "where: map(",
        "- sofa: here, where the robot stands",
        "x right, y up (the map has no north)",
        "places: A sofa",
    ):
        assert needle in shown.output, needle
    widths = {len(line) for line in shown.output.splitlines() if set(line) <= set("?.#>^<vA")}
    assert widths and max(widths) <= 30, widths


def test_the_cells_flag_says_the_map_verbs_range_and_keeps_to_it(tmp_path: Path) -> None:
    """The flag's help names the map verb's own bounds, and the verb's model is what keeps them."""
    bounds = MapParams.model_fields["cells_across"].metadata
    low = next(b.ge for b in bounds if hasattr(b, "ge"))
    high = next(b.le for b in bounds if hasattr(b, "le"))
    said = str(cli._CELLS.help)
    assert f"{low} to {high}" in said and f"{MapParams().cells_across} when" in said, said
    for bad in (low - 1, high + 1):
        with pytest.raises(ValidationError):
            MapParams(cells_across=bad)
    refused = runner.invoke(app, ["map", *ROOM, "--cells", str(low - 1), *_dirs(tmp_path)])
    assert refused.exit_code == 1 and "--cells" in refused.output, refused.output
    assert _store(tmp_path).frames() == [], "refused before anything connected"


def test_a_wipe_with_no_terminal_to_ask_is_refused_before_anything_connects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert _add(tmp_path, "sofa").exit_code == 0
    before = _store(tmp_path).frames()
    monkeypatch.setattr(cli, "_can_prompt", lambda: False)
    refused = runner.invoke(app, ["map", "--wipe", *ROOM, *_dirs(tmp_path)])
    assert refused.exit_code == 1 and "no terminal" in refused.output, refused.output
    assert _store(tmp_path).frames() == before and not any(f.wiped for f in before)


def test_a_wipe_said_yes_to_orphans_the_places_of_that_map_in_every_session_after(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The same room is the same map from one session to the next until it is wiped, and a wipe
    in one session leaves the places marked before it orphaned in every one after."""
    assert _add(tmp_path, "sofa").exit_code == 0
    monkeypatch.setattr(cli, "_can_prompt", lambda: True)
    wiped = runner.invoke(app, ["map", "--wipe", *ROOM, *_dirs(tmp_path)], input="y\n")
    assert wiped.exit_code == 0, wiped.output
    assert "orphans 1 place: sofa" in wiped.output, "the question names what it orphans"
    assert "wiped microduck:sim2d's map" in wiped.output
    assert "- sofa: orphaned, its map was wiped" in wiped.output
    later = runner.invoke(app, ["map", *ROOM, *_dirs(tmp_path)])
    assert later.exit_code == 0 and "- sofa: orphaned, its map was wiped" in later.output
    listed = runner.invoke(
        app,
        ["places", "list", "--robot", "microduck:sim2d", *_dirs(tmp_path)],
        env={"COLUMNS": "200"},
    )
    assert "its map wiped" in listed.output, listed.output


def test_a_wipe_said_no_to_wipes_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert _add(tmp_path, "sofa").exit_code == 0
    monkeypatch.setattr(cli, "_can_prompt", lambda: True)
    kept = runner.invoke(app, ["map", "--wipe", *ROOM, *_dirs(tmp_path)], input="n\n")
    assert kept.exit_code == 0 and "nothing wiped" in kept.output, kept.output
    assert "- sofa: here, where the robot stands" in kept.output
    assert not any(f.wiped for f in _store(tmp_path).frames())


def test_a_reading_that_keeps_no_map_has_none_to_wipe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "_can_prompt", lambda: True)
    refused = runner.invoke(app, ["map", "--wipe", "--robot", "rosbridge:mock", *_dirs(tmp_path)])
    assert refused.exit_code == 1 and "keeps no map to wipe" in refused.output, refused.output


def test_watch_draws_the_map_again_until_it_is_stopped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ctrl+C cancels the watch where it waits, and the robot is closed on the way out."""
    pauses: list[None] = []

    async def pause() -> None:
        pauses.append(None)
        if len(pauses) > 1:
            raise asyncio.CancelledError

    monkeypatch.setattr(cli, "_map_watch_pause", pause)
    watched = runner.invoke(app, ["map", "--watch", *ROOM, *_dirs(tmp_path)])
    assert watched.exit_code == 0, watched.output
    assert watched.output.count("where: map(") == 2 and "stopped watching" in watched.output
