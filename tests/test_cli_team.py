"""`roboglot team create|list|show|edit|delete`: named groups of registered robots.

Not the `team:` block of a `.glot` file, which says how a task is shared out. This says
which bodies share it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from roboglot.cli import app
from roboglot.registry import Registry, RobotEntry, StoredTeam

runner = CliRunner()


def _reg(tmp_path: Path) -> list[str]:
    return ["--registry-dir", str(tmp_path)]


def _seed(tmp_path: Path) -> Registry:
    registry = Registry(tmp_path)
    registry.add_robot(RobotEntry(name="arm", spec="lerobot:mock", note="the one with a gripper"))
    registry.add_robot(RobotEntry(name="duck-a", spec="microduck:mock"))
    registry.add_robot(RobotEntry(name="duck-b", spec="microduck:mock"))
    return registry


# ── the round trip ──────────────────────────────────────────────────────────────────────


def test_create_list_show_edit_delete(tmp_path: Path) -> None:
    _seed(tmp_path)
    created = runner.invoke(
        app,
        ["team", "create", "kitchen", "--robot", "duck-a", "--robot", "arm", *_reg(tmp_path)],
    )
    assert created.exit_code == 0, created.output
    assert "created team kitchen: duck-a, arm (2 robots)" in created.output
    assert "--team kitchen" in created.output
    assert Registry(tmp_path).team("kitchen").members == ["duck-a", "arm"], "order is kept"

    listed = runner.invoke(app, ["team", "list", *_reg(tmp_path)])
    assert listed.exit_code == 0, listed.output
    assert "kitchen" in listed.output and "ok" in listed.output

    shown = runner.invoke(app, ["team", "show", "kitchen", *_reg(tmp_path)])
    assert shown.exit_code == 0, shown.output
    assert "lerobot:mock" in shown.output, "show carries each member's body"
    assert "the one with a gripper" in shown.output

    edited = runner.invoke(
        app,
        [
            "team",
            "edit",
            "kitchen",
            "--add",
            "duck-b",
            "--description",
            "by the sink",
            *_reg(tmp_path),
        ],
    )
    assert edited.exit_code == 0, edited.output
    assert "added duck-b" in edited.output
    assert Registry(tmp_path).team("kitchen").members == ["duck-a", "arm", "duck-b"]

    deleted = runner.invoke(app, ["team", "delete", "kitchen", "--yes", *_reg(tmp_path)])
    assert deleted.exit_code == 0, deleted.output
    assert Registry(tmp_path).get_team("kitchen") is None
    assert set(Registry(tmp_path).robots()) == {"arm", "duck-a", "duck-b"}, "the robots stay"


def test_no_teams_says_how_to_make_one(tmp_path: Path) -> None:
    result = runner.invoke(app, ["team", "list", *_reg(tmp_path)])
    assert result.exit_code == 0, result.output
    assert "roboglot team create" in result.output


# ── the picker ──────────────────────────────────────────────────────────────────────────


def test_create_lists_the_robots_and_asks_when_no_robot_is_given(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed(tmp_path)
    monkeypatch.setattr("roboglot.cli._can_prompt", lambda: True)
    result = runner.invoke(app, ["team", "create", "pair", *_reg(tmp_path)], input="1, duck-b\n")
    assert result.exit_code == 0, result.output
    assert "robots you have registered" in result.output
    assert "which robots?" in result.output
    assert Registry(tmp_path).team("pair").members == ["arm", "duck-b"], "1 is the first row"


def test_the_picker_says_what_was_wrong_and_asks_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed(tmp_path)
    monkeypatch.setattr("roboglot.cli._can_prompt", lambda: True)
    result = runner.invoke(
        app, ["team", "create", "pair", *_reg(tmp_path)], input="9\nghost\n1 2\n"
    )
    assert result.exit_code == 0, result.output
    assert "there is no robot 9" in result.output
    assert "no robot called 'ghost'" in result.output
    assert Registry(tmp_path).team("pair").members == ["arm", "duck-a"]


def test_the_picker_gives_up_after_three_tries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed(tmp_path)
    monkeypatch.setattr("roboglot.cli._can_prompt", lambda: True)
    result = runner.invoke(app, ["team", "create", "pair", *_reg(tmp_path)], input="9\n9\n9\n")
    assert result.exit_code == 1, result.output
    assert "no valid answer in 3 tries" in result.output
    assert Registry(tmp_path).get_team("pair") is None


def test_an_empty_answer_cancels(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _seed(tmp_path)
    monkeypatch.setattr("roboglot.cli._can_prompt", lambda: True)
    result = runner.invoke(app, ["team", "create", "pair", *_reg(tmp_path)], input="\n")
    assert result.exit_code == 0, result.output
    assert "nothing created" in result.output
    assert Registry(tmp_path).get_team("pair") is None


def test_the_picker_refuses_the_same_robot_twice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed(tmp_path)
    monkeypatch.setattr("roboglot.cli._can_prompt", lambda: True)
    result = runner.invoke(app, ["team", "create", "pair", *_reg(tmp_path)], input="1,arm\n1 2\n")
    assert result.exit_code == 0, result.output
    assert "arm twice" in result.output


def test_without_a_terminal_it_says_to_pass_robot(tmp_path: Path) -> None:
    """CliRunner's stdin is never a terminal, which is exactly a script's situation."""
    _seed(tmp_path)
    result = runner.invoke(app, ["team", "create", "pair", *_reg(tmp_path)])
    assert result.exit_code == 1, result.output
    assert "no terminal to ask on" in result.output
    assert "--robot A --robot B" in result.output


def test_with_nothing_registered_it_says_to_register_first(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("roboglot.cli._can_prompt", lambda: True)
    result = runner.invoke(app, ["team", "create", "pair", *_reg(tmp_path)])
    assert result.exit_code == 1, result.output
    assert "no robots registered yet" in result.output


# ── refusals ────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("argv", "needle"),
    [
        (["team", "create", "3", "--robot", "duck-a"], "simulated ducks"),
        (["team", "create", "K", "--robot", "duck-a"], "not a valid team name"),
        (["team", "create", "k", "--robot", "ghost"], "no robot called 'ghost'"),
        (["team", "show", "ghost"], "no team called 'ghost'"),
        (["team", "edit", "ghost", "--add", "arm"], "no team called 'ghost'"),
        (["team", "delete", "ghost", "--yes"], "no team called 'ghost'"),
    ],
)
def test_a_refusal_is_one_line_and_never_a_traceback(
    tmp_path: Path, argv: list[str], needle: str
) -> None:
    _seed(tmp_path)
    result = runner.invoke(app, [*argv, *_reg(tmp_path)])
    assert result.exit_code == 1, result.output
    assert needle in result.output
    assert "Traceback" not in result.output


def test_two_teams_may_not_share_a_name(tmp_path: Path) -> None:
    _seed(tmp_path).add_team(StoredTeam(name="kitchen", members=["arm"]))
    result = runner.invoke(app, ["team", "create", "kitchen", "--robot", "duck-a", *_reg(tmp_path)])
    assert result.exit_code == 1 and "already exists" in result.output


def test_a_ninth_member_is_refused(tmp_path: Path) -> None:
    registry = Registry(tmp_path)
    for i in range(9):
        registry.add_robot(RobotEntry(name=f"duck-{i}", spec="microduck:mock"))
    argv = ["team", "create", "big"]
    for i in range(9):
        argv += ["--robot", f"duck-{i}"]
    result = runner.invoke(app, [*argv, *_reg(tmp_path)])
    assert result.exit_code == 1, result.output
    assert "at most 8" in result.output


def test_one_robot_is_stored_and_warned_about(tmp_path: Path) -> None:
    _seed(tmp_path)
    result = runner.invoke(app, ["team", "create", "solo", "--robot", "arm", *_reg(tmp_path)])
    assert result.exit_code == 0, result.output
    assert "2 to 8" in result.output
    assert Registry(tmp_path).team("solo").members == ["arm"]
    listed = runner.invoke(app, ["team", "list", *_reg(tmp_path)])
    assert "1 robot: running needs 2 to 8" in listed.output


def test_edit_needs_something_to_change(tmp_path: Path) -> None:
    _seed(tmp_path).add_team(StoredTeam(name="kitchen", members=["arm"]))
    result = runner.invoke(app, ["team", "edit", "kitchen", *_reg(tmp_path)])
    assert result.exit_code == 1 and "nothing to change" in result.output


def test_edit_renames_and_clears_a_description(tmp_path: Path) -> None:
    _seed(tmp_path).add_team(StoredTeam(name="kitchen", members=["arm"], description="by the sink"))
    renamed = runner.invoke(
        app, ["team", "edit", "kitchen", "--rename", "sink", "--description", "", *_reg(tmp_path)]
    )
    assert renamed.exit_code == 0, renamed.output
    assert "renamed to sink" in renamed.output and "cleared the description" in renamed.output
    registry = Registry(tmp_path)
    assert registry.get_team("kitchen") is None
    assert registry.team("sink").description is None


def test_delete_asks_unless_yes(tmp_path: Path) -> None:
    _seed(tmp_path).add_team(StoredTeam(name="kitchen", members=["arm"]))
    said_no = runner.invoke(app, ["team", "delete", "kitchen", *_reg(tmp_path)], input="n\n")
    assert said_no.exit_code == 0, said_no.output
    assert Registry(tmp_path).get_team("kitchen") is not None
    said_yes = runner.invoke(app, ["team", "delete", "kitchen", *_reg(tmp_path)], input="y\n")
    assert said_yes.exit_code == 0
    assert Registry(tmp_path).get_team("kitchen") is None


# ── the one broken state ────────────────────────────────────────────────────────────────


def test_a_member_that_is_no_longer_registered_is_marked_and_repairable(tmp_path: Path) -> None:
    registry = _seed(tmp_path)
    registry.add_team(StoredTeam(name="kitchen", members=["duck-a", "arm"]))
    (tmp_path / "robots.json").write_text(
        json.dumps({"version": 1, "robots": {"arm": {"spec": "lerobot:mock"}}}), encoding="utf-8"
    )
    listed = runner.invoke(app, ["team", "list", *_reg(tmp_path)])
    assert listed.exit_code == 0, listed.output
    assert "duck-a not registered" in listed.output
    assert "roboglot team edit NAME --remove" in listed.output
    shown = runner.invoke(app, ["team", "show", "kitchen", *_reg(tmp_path)])
    assert "not registered" in shown.output
    repaired = runner.invoke(
        app, ["team", "edit", "kitchen", "--remove", "duck-a", *_reg(tmp_path)]
    )
    assert repaired.exit_code == 0, repaired.output
    assert Registry(tmp_path).team("kitchen").members == ["arm"]


# ── --json ──────────────────────────────────────────────────────────────────────────────


def test_json_is_one_object_per_line_and_says_whether_it_could_run(tmp_path: Path) -> None:
    registry = _seed(tmp_path)
    registry.add_team(StoredTeam(name="kitchen", members=["duck-a", "arm"]))
    registry.add_team(StoredTeam(name="solo", members=["arm"]))
    result = runner.invoke(app, ["team", "list", "--json", *_reg(tmp_path)])
    assert result.exit_code == 0, result.output
    rows = {r["name"]: r for r in (json.loads(x) for x in result.output.strip().splitlines())}
    assert rows["kitchen"]["runnable"] is True and rows["kitchen"]["missing"] == []
    assert rows["solo"]["runnable"] is False
    one = runner.invoke(app, ["team", "show", "kitchen", "--json", *_reg(tmp_path)])
    payload = json.loads(one.output.strip())
    assert [r["name"] for r in payload["robots"]] == ["duck-a", "arm"]


@pytest.mark.parametrize("command", ["create", "list", "show", "edit", "delete"])
def test_every_team_command_answers_help(command: str) -> None:
    result = runner.invoke(app, ["team", command, "--help"])
    assert result.exit_code == 0, result.output
