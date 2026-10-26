"""`quackd drive` from the command line, through a pipe, and its record replayed by `quackd log`."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from quackd.agent.transcript import Transcript
from quackd.cli import app

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
