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


# ── run's flags, given once for every goal ──────────────────────────────────────────────

POLICY = ["--policy-url", "http://127.0.0.1:9"]


@pytest.mark.parametrize(
    ("flags", "said"),
    [
        (["--controller", "vla", *POLICY, "--yes"], "--yes answers every question"),
        (["--controller", "vla", *POLICY, "--dry-run"], "--dry-run moves nothing"),
        (["--controller", "vla"], "names no policy server"),
        (["--controller", "vla", *POLICY, "--llm", "fake"], "--llm would do nothing"),
        (["--controller", "vla", *POLICY, "--play"], "--play would do nothing"),
        (["--controller", "vla", *POLICY, "--effort", "low"], "--effort says how hard"),
        (
            ["--controller", "vla", *POLICY, "--decision-llm", "kev", "--decision-mode", "on"],
            "runs no model for a decision LLM to step in front of, and --decision-mode",
        ),
        (["--controller", "nope"], "--controller is llm or vla, not 'nope'"),
        (["--price", "lots"], "--price"),
    ],
    ids=["yes", "dry-run", "no policy", "llm", "play", "effort", "stepper", "controller", "price"],
)
def test_drive_refuses_what_run_refuses_of_the_same_flags(
    flags: list[str], said: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """In run's words, before anything is built: no run directory is left behind. A person is
    taken to be at the keyboard, so each refusal is its own and not the pipe's."""
    import quackd.cli as cli

    monkeypatch.setattr(cli, "_can_prompt", lambda: True)
    monkeypatch.setattr(cli, "_can_draw_a_prompt", lambda: True)
    runs = tmp_path / "runs"
    result = runner.invoke(
        app, ["--no-color", "drive", "--robot", "lerobot:mock", *flags, "--runs-dir", str(runs)]
    )
    assert result.exit_code == 1, result.output
    assert said in " ".join(result.output.split()), result.output
    assert not runs.exists(), "a refused session left a run directory"


def test_a_vla_pilot_through_a_pipe_is_refused_for_nobody_to_ask(tmp_path: Path) -> None:
    runs = tmp_path / "runs"
    result = runner.invoke(
        app,
        [
            *("--no-color", "drive", "--robot", "lerobot:mock", "--controller", "vla"),
            *(*POLICY, "--runs-dir", str(runs)),
        ],
        input="pick up the cube\nquit\n",
    )
    assert result.exit_code == 1, result.output
    assert "there is no terminal to ask on" in " ".join(result.output.split())
    assert not runs.exists()


@pytest.mark.parametrize(
    ("max_steps", "refused"), [(0, True), (1001, True), (1, False)], ids=["0", "1001", "1"]
)
def test_max_steps_is_a_goals_steps_within_the_schemas_bounds(
    max_steps: int, refused: bool, tmp_path: Path
) -> None:
    result = runner.invoke(
        app,
        [
            *("--no-color", "drive", "--robot", "microduck:mock", "--llm", "fake"),
            *("--max-steps", str(max_steps), "--runs-dir", str(tmp_path)),
        ],
        input="quit\n",
    )
    if refused:
        assert result.exit_code == 1
        assert "--max-steps is how many steps each goal may take, 1 to 1000" in result.output
    else:
        assert result.exit_code == 0, result.output


def _decision_stubs(monkeypatch: pytest.MonkeyPatch) -> Any:
    """A decision LLM that is here and builds, and the session it was handed to, kept."""
    import quackd.agent.decision.factory as factory
    import quackd.drive.run as drive_run

    class _Decider:
        name = "scripted"
        url = None

    decider = _Decider()
    kept: list[Any] = []
    real = drive_run.DriveSession

    class Kept(real):  # type: ignore[misc, valid-type]
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            kept.append(self)

    monkeypatch.setattr(drive_run, "DriveSession", Kept)
    monkeypatch.setattr(factory, "parse_decision_llm", lambda name: (object(), None))
    monkeypatch.setattr(factory, "decision_llm_is_available", lambda spec, url=None: (True, ""))
    monkeypatch.setattr(factory, "make_decision_llm", lambda spec, **_kw: decider)
    monkeypatch.setattr(factory, "resolve_decision_address", lambda spec, url=None: None)
    monkeypatch.setattr(factory, "resolve_decision_price", lambda spec, url=None: "a rate")
    return decider, kept


async def test_a_goal_gets_a_stepper_only_where_the_command_line_asks_for_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--decision-llm` alone is the fast lane, as it always was, and a `QUACKD_DECISION_MODE`
    in a `.env` written for `quackd run` gives no goal a stepper."""
    decider, kept = _decision_stubs(monkeypatch)
    monkeypatch.setenv("QUACKD_DECISION_MODE", "on")
    args = DriveArgs(
        robot="microduck:mock", llm="fake", decision_llm="scripted", runs_dir=str(tmp_path)
    )
    await run_drive(args, say=lambda _line: None, stream=io.StringIO("quit\n"), keyboard=False)
    (session,) = kept
    assert session.chooser is not None and session.chooser.llm is decider, "the fast lane"
    assert (session.settings.decision, session.settings.decision_llm) == ("off", None)


async def test_decision_mode_gives_every_goal_the_fast_lanes_own_decision_llm(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from quackd.agent.decision.notices import MODE_ON_UNMEASURED

    decider, kept = _decision_stubs(monkeypatch)
    said: list[str] = []
    args = DriveArgs(
        robot="microduck:mock",
        llm="fake",
        decision_llm="scripted",
        decision_mode="on",
        runs_dir=str(tmp_path),
    )
    await run_drive(args, say=said.append, stream=io.StringIO("quit\n"), keyboard=False)
    (session,) = kept
    settings = session.settings
    assert (settings.decision, settings.decision_llm, settings.decision_price) == (
        "on",
        decider,
        "a rate",
    )
    assert session.chooser is not None and session.chooser.llm is decider, "one client for both"
    assert f"{'note':<8} {MODE_ON_UNMEASURED}" in said


async def test_a_bare_session_flies_its_goals_as_it_always_did(tmp_path: Path) -> None:
    """None of run's flags given: no memory, no stepper, no part to play, the default steps."""
    import quackd.drive.run as drive_run

    kept: list[Any] = []
    real = drive_run.DriveSession

    class Kept(real):  # type: ignore[misc, valid-type]
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            kept.append(self)

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(drive_run, "DriveSession", Kept)
        args = DriveArgs(robot="microduck:mock", llm="fake", runs_dir=str(tmp_path))
        await run_drive(args, say=lambda _line: None, stream=io.StringIO("quit\n"), keyboard=False)
    (session,) = kept
    from quackd.drive.session import GoalSettings

    assert session.settings == GoalSettings()


async def test_memory_and_its_folder_reach_every_goal_and_the_places(tmp_path: Path) -> None:
    folder = tmp_path / "memory"
    args = DriveArgs(
        robot="microduck:mock",
        llm="fake",
        memory=True,
        memory_dir=str(folder),
        runs_dir=str(tmp_path / "runs"),
    )
    run_dir = await run_drive(
        args, say=lambda _line: None, stream=io.StringIO("patrol the room\nquit\n"), keyboard=False
    )
    (goal_dir,) = (run_dir / "goals").iterdir()
    events = Transcript.read(goal_dir / "transcript.jsonl")
    start = next(e for e in events if e["kind"] == "run_start")
    assert start["memory"]["path"] == str(folder / "microduck-mock.jsonl")
    assert (folder / "microduck-mock.jsonl").exists(), "the goal's episode was not kept"


def _kept_sessions(patch: pytest.MonkeyPatch) -> list[Any]:
    """Every `DriveSession` `run_drive` builds, kept for the test to read."""
    import quackd.drive.run as drive_run

    kept: list[Any] = []
    real = drive_run.DriveSession

    class Kept(real):  # type: ignore[misc, valid-type]
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            kept.append(self)

    patch.setattr(drive_run, "DriveSession", Kept)
    return kept


async def test_the_lens_is_given_once_as_the_session_connects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import quackd.drive.run as drive_run

    given: list[float] = []
    monkeypatch.setattr(drive_run, "_given_lens", lambda robot, fov: given.append(fov))
    args = DriveArgs(robot="microduck:mock", llm="fake", fov_deg=62, runs_dir=str(tmp_path))
    await run_drive(args, say=lambda _line: None, stream=io.StringIO("quit\n"), keyboard=False)
    assert given == [62.0]


async def test_a_contractless_session_has_the_schemas_ceiling_of_policy_seconds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A task's two minutes of segments are a dozen short lines to an arm, and a session held to
    them refused the thirteenth, as one held to a task's five minutes refused its sixth."""
    from quackd.drive.run import SESSION_POLICY_S
    from quackd.duckfile.schema import POLICY_TOTAL_MAX_S

    kept = _kept_sessions(monkeypatch)
    args = DriveArgs(robot="lerobot:mock", llm="fake", runs_dir=str(tmp_path))
    await run_drive(args, say=lambda _line: None, stream=io.StringIO("quit\n"), keyboard=False)
    (session,) = kept
    budget = session.robot.executor.budget
    assert SESSION_POLICY_S == POLICY_TOTAL_MAX_S and budget.policy_total_s == SESSION_POLICY_S


async def test_frames_give_every_goal_watch_and_say_where_a_frame_goes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The session withdraws `watch` for having nothing to look through, and its goals were
    shown the camera through the decision LLM all the same: they are offered it, as a run that
    asked for frames offers it."""
    import quackd.agent.decision.factory as factory

    decider, kept = _decision_stubs(monkeypatch)
    monkeypatch.setattr(factory, "resolve_decision_frames", lambda flag, **_kw: True)
    said: list[str] = []
    args = DriveArgs(
        robot="microduck:mock",
        llm="fake",
        decision_llm="scripted",
        decision_frames=True,
        runs_dir=str(tmp_path),
    )
    await run_drive(args, say=said.append, stream=io.StringIO("quit\n"), keyboard=False)
    (session,) = kept
    assert "watch" in session.goal_verbs
    settings = session.settings
    assert (settings.decision, settings.decision_llm, settings.decision_frames) == (
        "off",
        decider,
        True,
    )
    assert any("--decision-frames:" in line for line in said), said


async def test_a_vla_session_flies_each_goal_by_the_scripted_pilot_and_asks_no_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The robot's registered pilot and `QUACKD_LLM` name a model a VLA session never asks, so
    neither is resolved, and a key missing for it refuses nothing."""
    from quackd.agent.providers.vla import VlaProvider

    monkeypatch.setenv("QUACKD_LLM", "anthropic")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    kept = _kept_sessions(monkeypatch)
    args = DriveArgs(robot="lerobot:mock", controller="vla", runs_dir=str(tmp_path))
    run_dir = await run_drive(
        args, say=lambda _line: None, stream=io.StringIO("quit\n"), keyboard=False
    )
    (session,) = kept
    assert session.settings.vla
    pilot = session.pilot("pick up the red cube")
    assert isinstance(pilot, VlaProvider) and pilot.instructions == ("pick up the red cube",)
    events = Transcript.read(run_dir / "transcript.jsonl")
    start = next(e for e in events if e["kind"] == "run_start")
    assert (start["provider"], start["model"]) == ("vla", "scripted:goal")


async def test_the_sessions_run_start_names_its_policy_server_and_a_bare_one_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import quackd.drive.run as drive_run

    def start_of(run_dir: Path) -> dict[str, Any]:
        events = Transcript.read(run_dir / "transcript.jsonl")
        return next(e for e in events if e["kind"] == "run_start")

    bare = await run_drive(
        DriveArgs(robot="microduck:mock", llm="fake", runs_dir=str(tmp_path / "bare")),
        say=lambda _line: None,
        stream=io.StringIO("quit\n"),
        keyboard=False,
    )
    assert "policy" not in start_of(bare), "a record from before reads as it did"
    served = {"server": "http://127.0.0.1:9", "async_chunks": False}
    monkeypatch.setattr(drive_run, "_served", lambda transport: served)
    armed = await run_drive(
        DriveArgs(robot="microduck:mock", llm="fake", runs_dir=str(tmp_path / "armed")),
        say=lambda _line: None,
        stream=io.StringIO("quit\n"),
        keyboard=False,
    )
    assert start_of(armed)["policy"] == served
