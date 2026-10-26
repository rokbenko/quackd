"""`quackd drive` from the command line, through a pipe, and its record replayed by `quackd log`."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from quackd.agent.transcript import Transcript
from quackd.cli import app
from quackd.drive.run import SESSION_BUDGETS, session_duck
from quackd.duckfile.schema import Budgets

runner = CliRunner()


def test_a_piped_drive_runs_each_line_and_its_record_replays(tmp_path: Path) -> None:
    """Each line routed and run in turn, as a pipe, a record that opens and closes as any run's
    does, a summary that counts the lanes, and `quackd log` replaying it with the pipe's verdict
    on each call it ran."""
    lines = "stand\nforward\nkick left\nquit\n"
    args = ["--no-color", "drive", "--robot", "microduck:mock", "--llm", "fake"]
    result = runner.invoke(app, [*args, "--runs-dir", str(tmp_path)], input=lines)
    assert result.exit_code == 0, result.output
    assert "exact    forward: move(duration_s=1, vx=0.15)" in result.output
    assert "result   ok: kicked with left leg" in result.output
    (run,) = tmp_path.glob("*-drive")
    events = Transcript.read(run / "transcript.jsonl")
    assert events[0]["kind"] == "run_start" and events[-1]["kind"] == "run_end"
    assert [e["text"] for e in events if e["kind"] == "command"] == [
        "stand",
        "forward",
        "kick left",
        "quit",
    ]
    assert {e["by"] for e in events if e["kind"] == "command"} == {"cli"}
    summary = json.loads((run / "summary.json").read_text(encoding="utf-8"))
    assert summary["outcome"] == "success"
    assert summary["drive"]["lanes"] == {"exact": 3, "session": 1}
    replay = runner.invoke(app, ["--no-color", "log", str(run)])
    assert replay.exit_code == 0, replay.output
    assert "a pipe named this call" in replay.output


def test_drive_names_the_one_robot_it_drives_before_the_first_line(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        ["--no-color", "drive", "--robot", "microduck:mock", "--runs-dir", str(tmp_path)],
        input="quit\n",
    )
    assert result.exit_code == 0, result.output
    assert "drive    microduck: type a command" in result.output


def test_a_session_outlasts_one_tasks_budget(tmp_path: Path) -> None:
    """A person drives for longer than one task's defaults last: a session held to them refused
    every call once a task's steps were spent, and every one once its minutes were."""
    calls = Budgets().max_steps + 1
    result = runner.invoke(
        app,
        ["--no-color", "drive", "--robot", "microduck:mock", "--runs-dir", str(tmp_path)],
        input="".join(f"{line}\n" for line in ["report state"] * calls + ["quit"]),
    )
    assert result.exit_code == 0, result.output
    assert "max_steps" not in result.output
    assert result.output.count("result   ok: ") == calls + 1, "a call was refused"


def test_a_session_without_a_duck_keeps_the_battery_rule_and_the_ceilings() -> None:
    """Not the rule that ends a run on three failures in a row, which would end a session a
    person was still driving; and each budget the schema's own ceiling, one past it refused."""
    front = session_duck(["move", "stop"]).frontmatter
    assert front.battery_abort_percent is not None
    assert front.repeat_failure_abort is None
    assert front.budgets == SESSION_BUDGETS
    for name in Budgets.model_fields:
        with pytest.raises(ValidationError):
            Budgets(**{name: getattr(SESSION_BUDGETS, name) + 1})


@pytest.mark.parametrize("terminal", [True, False])
def test_drive_opens_the_keyboard_only_where_a_person_can_be_asked(
    terminal: bool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The same seam every question quackd puts goes through (`_can_prompt`), and not `isatty`
    alone, which says yes to NUL on Windows."""
    import quackd.cli
    import quackd.drive.run

    handed: dict[str, object] = {}

    async def run_drive(args: object, **kwargs: object) -> Path:
        handed.update(kwargs)
        return tmp_path

    monkeypatch.setattr(quackd.cli, "_can_prompt", lambda: terminal)
    monkeypatch.setattr(quackd.drive.run, "run_drive", run_drive)
    result = runner.invoke(app, ["--no-color", "drive", "--robot", "microduck:mock"])
    assert result.exit_code == 0, result.output
    assert handed["keyboard"] is terminal


def test_a_registered_robot_is_flown_by_the_pilot_it_names(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """As `quackd run` reads it: drive took `--llm` alone, and a robot registered against a
    vendor was driven by the scripted pilot."""
    import quackd.agent.providers.factory as factory
    from quackd.registry import Registry, RobotEntry

    registry = tmp_path / "registry"
    Registry(registry).add_robot(RobotEntry(name="duck", spec="microduck:mock", llm="openai"))
    asked: list[str] = []

    def make_provider(vendor: str, **kwargs: object) -> object:
        asked.append(vendor)
        return object()

    monkeypatch.setattr(factory, "make_provider", make_provider)
    monkeypatch.delenv("QUACKD_LLM", raising=False)
    args = ["--no-color", "drive", "--robot", "duck", "--registry-dir", str(registry)]
    result = runner.invoke(app, [*args, "--runs-dir", str(tmp_path / "runs")], input="quit\n")
    assert result.exit_code == 0, result.output
    assert asked == ["openai"]
    (run,) = (tmp_path / "runs").glob("*-drive")
    assert Transcript.read(run / "transcript.jsonl")[0]["provider"] == "openai"
