"""`roboglot run --team NAME`: a stored team of pilots, one LLM per body.

The counterpart to `tests/test_pilots.py`, which drives the runner directly. This drives the
command a person types.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from roboglot.cli import app
from roboglot.registry import Registry, RobotEntry, StoredTeam

runner = CliRunner()


def _reg(tmp_path: Path) -> list[str]:
    return ["--registry-dir", str(tmp_path)]


def _run_args(tmp_path: Path) -> list[str]:
    return [
        "--llm",
        "fake",
        "--no-log",
        "--runs-dir",
        str(tmp_path / "runs"),
        "--memory-dir",
        str(tmp_path / "mem"),
        *_reg(tmp_path),
    ]


def _seed(tmp_path: Path, robots: dict[str, str], teams: dict[str, list[str]]) -> Registry:
    registry = Registry(tmp_path)
    for name, spec in robots.items():
        registry.add_robot(RobotEntry(name=name, spec=spec))
    for name, members in teams.items():
        registry.add_team(StoredTeam(name=name, members=members))
    return registry


def _newest(tmp_path: Path) -> Path:
    return sorted((tmp_path / "runs").iterdir())[-1]


def _summary(tmp_path: Path) -> dict[str, Any]:
    return json.loads((_newest(tmp_path) / "summary.json").read_text(encoding="utf-8"))


# ── a name is a team of pilots ─────────────────────────────────────────────────────────


def test_the_demo_task_file_runs_with_no_registry_and_no_key(tmp_path: Path) -> None:
    """`roboglot run team-hello --llm fake` has to work in a fresh checkout."""
    result = runner.invoke(app, ["run", "team-hello", *_run_args(tmp_path)])
    assert result.exit_code == 0, result.output
    assert "members 2/2 succeeded" in result.output
    assert "talk 2" in result.output
    summary = _summary(tmp_path)
    assert summary["team"]["method"] == "pilots"
    assert summary["robots"] == {"duck": "microduck:mock", "arm": "lerobot:mock"}
    for name in ("duck", "arm"):
        assert (_newest(tmp_path) / "robots" / name / "transcript.jsonl").exists()


def test_three_different_bodies_run_as_one_stored_team(tmp_path: Path) -> None:
    _seed(
        tmp_path,
        {"duck": "microduck:mock", "arm": "lerobot:mock", "cart": "rosbridge:mock"},
        {"trio": ["duck", "arm", "cart"]},
    )
    result = runner.invoke(app, ["run", "team-hello", "--team", "trio", *_run_args(tmp_path)])
    assert result.exit_code == 0, result.output
    assert "members 3/3 succeeded" in result.output
    assert "stored as" in result.output and "trio" in result.output
    assert _summary(tmp_path)["team"]["name"] == "trio"


def test_two_bodies_of_one_kind_run_and_keep_separate_notes(tmp_path: Path) -> None:
    _seed(
        tmp_path,
        {"duck-a": "microduck:mock", "duck-b": "microduck:mock"},
        {"twins": ["duck-a", "duck-b"]},
    )
    result = runner.invoke(app, ["run", "team-hello", "--team", "twins", *_run_args(tmp_path)])
    assert result.exit_code == 0, result.output
    assert (tmp_path / "mem" / "duck-a.jsonl").exists()
    assert (tmp_path / "mem" / "duck-b.jsonl").exists()
    assert not (tmp_path / "mem" / "microduck-mock.jsonl").exists()


def test_a_goal_can_be_given_to_a_stored_team(tmp_path: Path) -> None:
    _seed(
        tmp_path,
        {"duck": "microduck:mock", "arm": "lerobot:mock"},
        {"pair": ["duck", "arm"]},
    )
    result = runner.invoke(
        app,
        [
            "run",
            "--goal",
            "say hello to your team and stop",
            "--team",
            "pair",
            *_run_args(tmp_path),
        ],
    )
    assert result.exit_code in (0, 1), result.output
    assert "members" in result.output, "it ran rather than refusing"


def test_a_member_that_fails_fails_the_team(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from roboglot.agent.providers.base import ToolCall
    from roboglot.agent.providers.fake import FakeProvider

    def give_up(obs: Any, step: int, history: list[Any]) -> ToolCall:
        return ToolCall(name="declare_failure", arguments={"reason": "I do not want to"})

    monkeypatch.setattr(
        "roboglot.agent.providers.factory.make_provider",
        lambda *a, **k: FakeProvider(strategy=give_up),
    )
    result = runner.invoke(app, ["run", "team-hello", *_run_args(tmp_path)])
    assert result.exit_code == 1, result.output
    assert "members 0/2 succeeded" in result.output
    assert "I do not want to" in result.output


def test_dry_run_reaches_a_pilot_team(tmp_path: Path) -> None:
    result = runner.invoke(app, ["run", "team-hello", "--dry-run", *_run_args(tmp_path)])
    assert result.exit_code == 0, result.output
    assert "DRY RUN" in result.output
    assert _summary(tmp_path)["dry_run"] is True


def test_no_memory_leaves_nothing_behind(tmp_path: Path) -> None:
    result = runner.invoke(app, ["run", "team-hello", "--no-memory", *_run_args(tmp_path)])
    assert result.exit_code == 0, result.output
    assert not (tmp_path / "mem").exists()


# ── what the summary wrote down ─────────────────────────────────────────────────────────


def test_a_team_writes_no_key_into_its_summary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A team's summary carries the command, so it carries the thing a solo run redacts out of
    `run_start`. A run directory is pasted into issues, and a key that reaches one has to be
    rotated.

    `sys.argv` is pytest's own here, because `CliRunner` calls the command in-process while
    the record is built from the arguments the process was given.
    """
    glotfile = "team-hello"
    argv = [glotfile, "--api-key", "sk-never-in-a-run-directory"]
    monkeypatch.setattr(sys, "argv", ["roboglot", "run", *argv, *_run_args(tmp_path)])
    result = runner.invoke(app, ["run", *argv, *_run_args(tmp_path)])
    assert result.exit_code == 0, result.output
    summary = _summary(tmp_path)
    assert summary["command"][:3] == ["roboglot", "run", glotfile]
    # the flag itself stays: that a key was given is often the thing a reader is checking
    assert summary["command"][3:5] == ["--api-key", "***"]
    assert "sk-never-in-a-run-directory" not in json.dumps(summary)


# ── refusals ────────────────────────────────────────────────────────────────────────────


def test_a_number_for_a_team_is_refused_in_one_line(tmp_path: Path) -> None:
    """`--team 3` was the coordinator's count of ducks, and a team is a stored one now."""
    result = runner.invoke(app, ["run", "team-hello", "--team", "3", *_run_args(tmp_path)])
    assert result.exit_code == 1
    said = " ".join(result.output.split())
    assert "--team 3 was a coordinator team of 3 ducks, which Roboglot no longer has" in said
    assert "roboglot team list" in said
    assert not (tmp_path / "runs").exists(), "nothing was written"


def test_a_pilot_team_of_one_is_refused(tmp_path: Path) -> None:
    _seed(tmp_path, {"duck": "microduck:mock"}, {"solo": ["duck"]})
    result = runner.invoke(app, ["run", "team-hello", "--team", "solo", *_run_args(tmp_path)])
    assert result.exit_code == 1, result.output
    assert "2 to 8 members" in result.output


def test_an_unknown_team_says_where_to_find_the_stored_ones(tmp_path: Path) -> None:
    result = runner.invoke(app, ["run", "team-hello", "--team", "ghost", *_run_args(tmp_path)])
    assert result.exit_code == 1
    assert "no team called 'ghost'" in result.output
    assert "roboglot team list" in result.output


def test_a_dangling_team_refuses_before_it_runs(tmp_path: Path) -> None:
    _seed(
        tmp_path,
        {"duck": "microduck:mock", "arm": "lerobot:mock"},
        {"pair": ["duck", "arm"]},
    )
    # by hand, which is the only way to make a team dangle: `robot remove` refuses
    (tmp_path / "robots.json").write_text(
        json.dumps({"version": 1, "robots": {"duck": {"spec": "microduck:mock"}}}),
        encoding="utf-8",
    )
    result = runner.invoke(app, ["run", "team-hello", "--team", "pair", *_run_args(tmp_path)])
    assert result.exit_code == 1, result.output
    assert "no longer registered" in result.output
    assert not (tmp_path / "runs").exists(), "nothing was written"


def test_a_stored_team_brings_its_own_robots(tmp_path: Path) -> None:
    _seed(
        tmp_path,
        {"duck": "microduck:mock", "arm": "lerobot:mock"},
        {"pair": ["duck", "arm"]},
    )
    for extra in (["--robot", "duck"], ["--robots", "a=microduck:mock,b=lerobot:mock"]):
        result = runner.invoke(
            app, ["run", "team-hello", "--team", "pair", *extra, *_run_args(tmp_path)]
        )
        assert result.exit_code == 1, result.output
        assert "brings its own robots" in result.output


def test_a_gated_verb_needs_yes(tmp_path: Path) -> None:
    glot = tmp_path / "gated.glot"
    glot.write_text(
        "---\nglot: 1\nname: gated\ndescription: d\nverbs:\n  allow: [report_state, stop, pick]\n"
        "  confirm: [pick]\nsuccess: [x]\nrequires: [report_state]\n"
        "team:\n  members: [duck, arm]\n  allocation:\n    method: pilots\n"
        "---\n# Task\nx\n",
        encoding="utf-8",
    )
    _seed(
        tmp_path,
        {"duck": "microduck:mock", "arm": "lerobot:mock"},
        {"pair": ["duck", "arm"]},
    )
    refused = runner.invoke(app, ["run", str(glot), "--team", "pair", *_run_args(tmp_path)])
    assert refused.exit_code == 1, refused.output
    assert "cannot prompt y/N per member" in refused.output


def test_a_stored_team_refuses_the_endpoint_flags_it_would_have_dropped(
    tmp_path: Path,
) -> None:
    _seed(
        tmp_path,
        {"duck": "microduck:mock", "arm": "lerobot:mock"},
        {"pair": ["duck", "arm"]},
    )
    for extra in (["--address", "tcp://x:1"], ["--token", "t"], ["--camera-url", "http://x"]):
        result = runner.invoke(
            app, ["run", "team-hello", "--team", "pair", *extra, *_run_args(tmp_path)]
        )
        assert result.exit_code == 1, result.output
        assert "takes every member's address" in result.output


def test_validate_answers_in_one_line_when_the_registry_is_broken(tmp_path: Path) -> None:
    """`validate --robot <name>` reads robots.json, and a broken one must not be a traceback."""
    (tmp_path / "robots.json").write_text("{ not json", encoding="utf-8")
    result = runner.invoke(app, ["validate", "hello-world", "--robot", "duck-a", *_reg(tmp_path)])
    assert result.exit_code == 1, result.output
    assert "not valid JSON" in result.output
    assert "Traceback" not in result.output
