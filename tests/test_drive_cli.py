"""`quackd drive` from the command line, through a pipe, and its record replayed by `quackd log`."""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from quackd.agent.transcript import Transcript
from quackd.cli import app
from quackd.drive.run import SESSION_BUDGETS, DriveArgs, run_drive, session_duck
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


@pytest.mark.parametrize(("terminal", "drawn"), [(True, True), (True, False), (False, True)])
def test_drive_opens_the_keyboard_only_where_a_person_can_be_asked(
    terminal: bool, drawn: bool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The same seam every question quackd puts goes through (`_can_prompt`), and not `isatty`
    alone, which says yes to NUL on Windows; and an output the prompt can be drawn on, since a
    session whose output was piped to a file died before its first line on Windows."""
    import quackd.cli
    import quackd.drive.run

    handed: dict[str, object] = {}

    async def run_drive(args: object, **kwargs: object) -> Path:
        handed.update(kwargs)
        return tmp_path

    monkeypatch.setattr(quackd.cli, "_can_prompt", lambda: terminal)
    monkeypatch.setattr(quackd.cli, "_can_draw_a_prompt", lambda: drawn)
    monkeypatch.setattr(quackd.drive.run, "run_drive", run_drive)
    result = runner.invoke(app, ["--no-color", "drive", "--robot", "microduck:mock"])
    assert result.exit_code == 0, result.output
    assert handed["keyboard"] is (terminal and drawn)
    assert ("the prompt needs a terminal for its output too" in result.output) is (
        terminal and not drawn
    )


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


def _summary_of(runs: Path) -> dict[str, Any]:
    (run,) = runs.glob("*-drive")
    summary: dict[str, Any] = json.loads((run / "summary.json").read_text(encoding="utf-8"))
    return summary


class _Broken:
    """A stdin whose read fails after its first line, as a broken pipe's does."""

    def __init__(self) -> None:
        self.n = 0

    def readline(self) -> str:
        self.n += 1
        if self.n == 1:
            return "forward\n"
        raise OSError("the pipe broke")


async def test_a_session_whose_input_broke_is_not_recorded_as_a_success(tmp_path: Path) -> None:
    """Every way out was written down as a success, a crash included."""
    args = DriveArgs(robot="microduck:mock", llm="fake", runs_dir=str(tmp_path))
    with pytest.raises(OSError, match="the pipe broke"):
        await run_drive(args, say=lambda _line: None, stream=_Broken(), keyboard=False)  # type: ignore[arg-type]
    summary = _summary_of(tmp_path)
    assert summary["outcome"] == "error" and "the pipe broke" in summary["reason"]


async def test_a_session_whose_robot_never_connected_is_not_a_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import quackd.mcp_server
    from quackd.transport.base import TransportError

    async def refuse(self: object) -> None:
        raise TransportError("the robot did not answer")

    monkeypatch.setattr(quackd.mcp_server.RobotSession, "connect", refuse)
    args = DriveArgs(robot="microduck:mock", llm="fake", runs_dir=str(tmp_path))
    with pytest.raises(TransportError):
        await run_drive(args, say=lambda _line: None, stream=None, keyboard=False)
    summary = _summary_of(tmp_path)
    assert summary["outcome"] == "error" and "did not answer" in summary["reason"]


GATED_KICK = """---
duck: 0
name: gated
description: kick behind a yes
verbs:
  allow: [kick, move, report_state, stop]
  confirm: [kick]
success: [x]
abort_when: []
---
# Task
x
"""


async def test_the_fast_lanes_floors_answer_to_the_ducks_own_gates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A kick the `.duck` gates answered to the motion floor rather than the confirm one, the
    bug `verb_class` names in the run's own stepper: under `--yes` it ran unasked at a
    confidence the stepper would have asked about."""
    import quackd.agent.decision.commands as commands
    import quackd.agent.decision.factory as factory

    class _Decider:
        name = "scripted"

    real = commands.offers_for
    seen: list[frozenset[str]] = []

    def offers_for(verbs: Any, presets: Any, gated: frozenset[str] = frozenset()) -> Any:
        seen.append(gated)
        return real(verbs, presets, gated)

    monkeypatch.setattr(commands, "offers_for", offers_for)
    monkeypatch.setattr(factory, "parse_decision_llm", lambda name: (object(), None))
    monkeypatch.setattr(factory, "decision_llm_is_available", lambda spec, url=None: (True, ""))
    monkeypatch.setattr(factory, "make_decision_llm", lambda spec, model=None, url=None: _Decider())
    duck = tmp_path / "gated.duck"
    duck.write_text(GATED_KICK, encoding="utf-8")
    args = DriveArgs(
        robot="microduck:mock",
        llm="fake",
        decision_llm="scripted",
        duckfile=str(duck),
        runs_dir=str(tmp_path / "runs"),
    )
    await run_drive(args, say=lambda _line: None, stream=io.StringIO("quit\n"), keyboard=False)
    assert seen == [frozenset({"kick"})]
