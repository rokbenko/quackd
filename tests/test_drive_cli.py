"""`roboglot drive` from the command line, through a pipe, and its record replayed by `roboglot
log`."""

from __future__ import annotations

import asyncio
import io
import json
import logging
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from roboglot.adapters.base import RestResult
from roboglot.agent.transcript import Transcript
from roboglot.cli import app, run_counters
from roboglot.drive.run import SESSION_BUDGETS, DriveArgs, run_drive, session_glot
from roboglot.glotfile.schema import Budgets
from roboglot.release import FOLD_STOPPED

runner = CliRunner()


def test_a_piped_drive_runs_each_line_and_its_record_replays(tmp_path: Path) -> None:
    """Each line routed and run in turn, as a pipe, a record that opens and closes as any run's
    does, a summary that counts the lanes, and `roboglot log` replaying it with the pipe's verdict
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


def test_a_sessions_record_adds_up_what_its_goals_spent(tmp_path: Path) -> None:
    """A session wrote neither its goals' calls nor their tokens, and `roboglot log` read the two
    missing keys as zeros, so it said `LLM calls 0 · tokens 0+0` under a session that had flown
    a goal. Each goal's own record is what the session's is held to here."""
    args = ["--no-color", "drive", "--robot", "microduck:mock", "--llm", "fake"]
    goals = "find the ball\nfind the ball again\nquit\n"
    result = runner.invoke(app, [*args, "--runs-dir", str(tmp_path), "--yes"], input=goals)
    assert result.exit_code == 0, result.output
    (run,) = tmp_path.glob("*-drive")
    flown = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in (run / "goals").glob("*/summary.json")
    ]
    assert len(flown) == 2, "with fewer than two there is nothing to add up"
    calls = sum(goal["llm_calls"] for goal in flown)
    tokens = sum(goal["usage"]["input_tokens"] for goal in flown)
    out = sum(goal["usage"]["output_tokens"] for goal in flown)
    assert calls > 0
    summary = _summary_of(tmp_path)
    assert summary["provider"] == "fake" and summary["llm_calls"] == calls
    assert (summary["usage"]["input_tokens"], summary["usage"]["output_tokens"]) == (tokens, out)
    replay = " ".join(runner.invoke(app, ["--no-color", "log", str(run)]).output.split())
    assert f"LLM calls {calls} · tokens {tokens}+{out}" in replay


def test_a_session_with_no_goal_says_it_spent_nothing(tmp_path: Path) -> None:
    args = ["--no-color", "drive", "--robot", "microduck:mock", "--llm", "fake"]
    result = runner.invoke(app, [*args, "--runs-dir", str(tmp_path)], input="stand\nquit\n")
    assert result.exit_code == 0, result.output
    summary = _summary_of(tmp_path)
    assert (summary["llm_calls"], summary["usage"]) == (0, {})


def test_drive_names_the_one_robot_it_drives_before_the_first_line(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        ["--no-color", "drive", "--robot", "microduck:mock", "--runs-dir", str(tmp_path)],
        input="quit\n",
    )
    assert result.exit_code == 0, result.output
    assert "drive    microduck: type a command" in result.output


async def test_a_verb_the_exact_lane_cannot_read_is_named_once_as_the_session_opens(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A verb past `MAX_KEYS_PER_VERB` gets nothing in the exact lane, and a line naming it that
    would have run at once goes to the fast lane or the pilot: the person is told which verb, once,
    before the first line. The bound is lowered here to below the duck's `gaze`, whose five calls
    are five lines, and above its `kick`, whose two are two."""
    import roboglot.drive.lanes

    monkeypatch.setattr(roboglot.drive.lanes, "MAX_KEYS_PER_VERB", 4)
    said: list[str] = []
    args = DriveArgs(robot="microduck:mock", llm="fake", runs_dir=str(tmp_path))
    await run_drive(args, say=said.append, stream=io.StringIO("quit\n"), keyboard=False)
    notes = [line for line in said if "the exact lane cannot read it" in line]
    assert notes == [
        "note     gaze: the exact lane cannot read it, since its calls said with their values in "
        "every order would be 5 lines, past the 4 it takes for one verb, so a line naming it goes "
        "on to the fast lane or the pilot"
    ], said
    assert said.index(notes[0]) < next(i for i, s in enumerate(said) if s.startswith("drive "))


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


def test_a_session_without_a_task_file_keeps_the_battery_rule_and_the_ceilings() -> None:
    """Not the rule that ends a run on three failures in a row, which would end a session a
    person was still driving; and each budget the schema's own ceiling, one past it refused."""
    front = session_glot(["move", "stop"]).frontmatter
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
    """The same seam every question Roboglot puts goes through (`_can_prompt`), and not `isatty`
    alone, which says yes to NUL on Windows; and an output the prompt can be drawn on, since a
    session whose output was piped to a file died before its first line on Windows."""
    import roboglot.cli
    import roboglot.drive.run

    handed: dict[str, object] = {}

    async def run_drive(args: object, **kwargs: object) -> Path:
        handed.update(kwargs)
        return tmp_path

    monkeypatch.setattr(roboglot.cli, "_can_prompt", lambda: terminal)
    monkeypatch.setattr(roboglot.cli, "_can_draw_a_prompt", lambda: drawn)
    monkeypatch.setattr(roboglot.drive.run, "run_drive", run_drive)
    result = runner.invoke(app, ["--no-color", "drive", "--robot", "microduck:mock"])
    assert result.exit_code == 0, result.output
    assert handed["keyboard"] is (terminal and drawn)
    assert ("the prompt needs a terminal for its output too" in result.output) is (
        terminal and not drawn
    )


def test_a_registered_robot_is_flown_by_the_pilot_it_names(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """As `roboglot run` reads it: drive took `--llm` alone, and a robot registered against a
    vendor was driven by the scripted pilot."""
    import roboglot.agent.providers.factory as factory
    from roboglot.registry import Registry, RobotEntry

    registry = tmp_path / "registry"
    Registry(registry).add_robot(RobotEntry(name="duck", spec="microduck:mock", llm="openai"))
    asked: list[str] = []

    def make_provider(vendor: str, **kwargs: object) -> object:
        asked.append(vendor)
        return object()

    monkeypatch.setattr(factory, "make_provider", make_provider)
    monkeypatch.delenv("ROBOGLOT_LLM", raising=False)
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


def _the_run(runs: Path) -> Path:
    (run,) = runs.glob("*-drive")
    return run


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


@pytest.mark.parametrize("fails", ["summary", "run_end", "close", "finished"])
async def test_each_step_of_a_sessions_end_is_taken_whatever_the_one_before_did(
    fails: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A session's end writes `summary.json`, records `run_end`, closes its record and tells the
    page, and did so with nothing between them: one that failed, a full disk or the run folder
    gone, skipped every one after it, and its `OSError` took the place of the error the session
    was ending on. Each step fails here in turn, under a session the page stopped before it
    connected; every other step must still be taken, the failure said, and the page's refusal
    must be what leaves."""
    from roboglot.drive.run import DriveError
    from roboglot.front import Channel, Front

    full = OSError(28, "No space left on device")
    closed: list[bool] = []
    write, close, finished = Transcript.write, Transcript.close, Front.finished

    def write_summary(self: Transcript, summary: dict[str, Any]) -> Path:
        raise full

    def written(self: Transcript, kind: str, **payload: Any) -> None:
        if kind == "run_end" and fails == "run_end":
            raise full
        write(self, kind, **payload)

    def closing(self: Transcript) -> None:
        close(self)
        closed.append(True)
        if fails == "close":
            raise full

    def finishing(self: Front, *args: Any, **kwargs: Any) -> None:
        if fails == "finished":
            raise full
        finished(self, *args, **kwargs)

    if fails == "summary":
        monkeypatch.setattr(Transcript, "write_summary", write_summary)
    monkeypatch.setattr(Transcript, "write", written)
    monkeypatch.setattr(Transcript, "close", closing)
    monkeypatch.setattr(Front, "finished", finishing)
    front = Front(Channel(io.StringIO()), "drive")
    front.on_op({"op": "stop"})
    args = DriveArgs(robot="microduck:mock", llm="fake", runs_dir=str(tmp_path))
    with pytest.raises(DriveError, match="the page's Stop came before the robot connected"):
        await run_drive(args, say=lambda _line: None, front=front)
    run = _the_run(tmp_path)
    assert (run / "summary.json").exists() is (fails != "summary")
    kinds = [e["kind"] for e in Transcript.read(run / "transcript.jsonl")]
    assert (kinds[-1:] == ["run_end"]) is (fails != "run_end"), kinds
    assert closed == [True], "the record was never closed"
    assert (front.outcome is not None and front.outcome["outcome"] == "aborted") is (
        fails != "finished"
    ), front.outcome
    assert "No space left on device" in caplog.text


async def test_a_session_whose_robot_never_connected_is_not_a_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import roboglot.mcp_server
    from roboglot.transport.base import TransportError

    async def refuse(self: object) -> None:
        raise TransportError("the robot did not answer")

    monkeypatch.setattr(roboglot.mcp_server.RobotSession, "connect", refuse)
    args = DriveArgs(robot="microduck:mock", llm="fake", runs_dir=str(tmp_path))
    with pytest.raises(TransportError):
        await run_drive(args, say=lambda _line: None, stream=None, keyboard=False)
    summary = _summary_of(tmp_path)
    assert summary["outcome"] == "error" and "did not answer" in summary["reason"]


def _arm_with_a_rest_pose(tmp_path: Path) -> Path:
    """A registry holding the arm's mock with a rest pose away from where the mock stands, so a
    fold to it moves the arm at each end of a session."""
    from roboglot.registry import Registry
    from roboglot_lerobot.mock import REST

    registry = tmp_path / "registry"
    added = runner.invoke(
        app, ["robot", "add", "arm-01", "lerobot:mock", "--registry-dir", str(registry)]
    )
    assert added.exit_code == 0, added.output
    # a pan inside the mock's travel and well off where it stands, so the pose is one to move to
    away = {"shoulder_pan": REST["shoulder_pan"] + 35.0}
    Registry(registry).update_robot("arm-01", {"rest_pose": dict(REST) | away})
    return registry


class _Folds:
    """The arm mock's `_drive_to_rest`, the step inside the real rest move that sends its goals,
    counted, and held open on fold number `hold_on` until a press cancels it. Every other fold
    says it arrived without moving the arm, so the arm is still away from its pose at the close,
    as an arm a session has moved is. Everything around it is the real rest move's: the cancel it
    re-raises and the result the close reads."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, *, hold_on: int) -> None:
        from roboglot_lerobot.mock import LeRobotMock

        self.calls = 0
        self.arm: Any = None
        self.folding = asyncio.Event()
        never = asyncio.Event()
        folds = self

        async def drive_to_rest(
            arm: Any, goal: dict[str, float], recorded: dict[str, float]
        ) -> RestResult:
            folds.arm = arm
            folds.calls += 1
            if folds.calls == hold_on:
                folds.folding.set()
                await never.wait()  # the fold under way when the press comes
            return RestResult("arrived", "a stand-in fold")

        monkeypatch.setattr(LeRobotMock, "_drive_to_rest", drive_to_rest)


async def _pressed_during_fold(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    fold: int,
    held: asyncio.Event | None = None,
) -> tuple[asyncio.Task[Path], _Folds, list[str], Path]:
    """A session on the arm whose fold number `fold` a press lands on, or whatever else a
    stand-in of the caller's holds open until it sets `held`, as asyncio's own Ctrl-C handler
    delivers one: a cancel of the main task, and never a real signal. Returns once the session
    has ended."""
    registry = _arm_with_a_rest_pose(tmp_path)
    folds = _Folds(monkeypatch, hold_on=fold)
    said: list[str] = []
    runs = tmp_path / "runs"
    args = DriveArgs(robot="arm-01", registry_dir=str(registry), llm="fake", runs_dir=str(runs))
    session = asyncio.ensure_future(
        run_drive(args, say=said.append, stream=io.StringIO("quit\n"), keyboard=False)
    )
    await asyncio.wait_for((held or folds.folding).wait(), 30)
    session.cancel()
    await asyncio.wait({session}, timeout=30)
    assert session.done(), "the session never ended"
    return session, folds, said, _the_run(runs)


async def test_a_press_during_the_opening_fold_holds_the_arm_and_nothing_folds_it_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A Ctrl-C while the session's connect folds the arm stopped that fold, and the session's
    close then sent a stop and started the same fold again, with the press's one cancel spent, so
    the arm the person had just stopped went on to its pose with nothing said, and that fold's
    result took the place of the stopped one in the close's line. A second fold, a missing hold,
    a missing line or a close that no longer names the stopped fold fails here."""
    caplog.set_level(logging.WARNING)
    session, folds, said, run = await _pressed_during_fold(tmp_path, monkeypatch, fold=1)
    assert session.cancelled(), "the press is what the session ended on"
    assert folds.calls == 1, "the fold the press stopped was started again"
    sequence = folds.arm.sequence
    assert sequence[sequence.index("rest") :] == ["rest", "stop", "close"], sequence
    assert any(FOLD_STOPPED in line for line in said), said
    assert "a fold to it was stopped partway" in str(folds.arm.close_note)
    assert any(str(folds.arm.close_note) in r.getMessage() for r in caplog.records), caplog.text
    events = Transcript.read(run / "transcript.jsonl")
    assert any(e["kind"] == "note" and e["text"] == FOLD_STOPPED for e in events), events
    assert events[-1]["kind"] == "run_end" and events[-1]["outcome"] == "aborted"
    assert (run / "summary.json").exists()


async def test_a_press_during_the_closing_fold_holds_the_arm_and_the_session_still_ends(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """After `quit`, a Ctrl-C during the session's closing fold is asyncio's one cancel of the
    main task, and it passed every `suppress(Exception)` of the close: the transport was never
    closed, so the arm was left energised partway through its fold with nothing said of its
    torque, and the session wrote neither its `run_end` nor its `summary.json`. A press there
    now holds the arm, says so, closes the transport with its note and ends the record; a close
    the press skips, a missing hold or a missing record fails here."""
    caplog.set_level(logging.WARNING)
    session, folds, said, run = await _pressed_during_fold(tmp_path, monkeypatch, fold=2)
    assert session.cancelled(), "the press goes on up once the session has ended in order"
    assert folds.calls == 2
    sequence = folds.arm.sequence
    closing = len(sequence) - 1 - sequence[::-1].index("rest")
    assert sequence[closing:] == ["rest", "stop", "close"], sequence
    assert any(FOLD_STOPPED in line for line in said), said
    assert "a fold to it was stopped partway" in str(folds.arm.close_note)
    assert any(str(folds.arm.close_note) in r.getMessage() for r in caplog.records), caplog.text
    events = Transcript.read(run / "transcript.jsonl")
    assert any(e["kind"] == "note" and e["text"] == FOLD_STOPPED for e in events), events
    # the session ended as asked, by `quit`, and the press came after that
    assert events[-1]["kind"] == "run_end" and events[-1]["outcome"] == "success"
    assert (run / "summary.json").exists()


async def test_a_press_on_the_closes_stop_keeps_the_closing_fold_from_starting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A Ctrl-C that lands on the close's stop, before its fold, went past the rest of the close
    as one during the fold did, and a close that carried on through it would start the fold over
    the press, moving the arm the person had just pressed to stop. The arm is held where it is, the
    person is told it was not folded, and the session still closes and ends its record; a fold
    after the press, a missing line or a missing close fails here."""
    from roboglot.agent.loop import AgentLoop
    from roboglot_lerobot.mock import LeRobotMock

    stopping = asyncio.Event()
    never = asyncio.Event()
    stop = LeRobotMock.stop

    async def held_stop(arm: Any) -> None:
        if not stopping.is_set():  # the close's own stop, the first a session of `quit` sends
            stopping.set()
            await never.wait()
        await stop(arm)

    monkeypatch.setattr(LeRobotMock, "stop", held_stop)
    session, folds, said, run = await _pressed_during_fold(
        tmp_path, monkeypatch, fold=0, held=stopping
    )
    assert session.cancelled()
    assert folds.calls == 1, "the closing fold started after the press"
    assert folds.arm.sequence[-2:] == ["stop", "close"], folds.arm.sequence
    assert any(AgentLoop.HELD_BACK in line for line in said), said
    events = Transcript.read(run / "transcript.jsonl")
    assert any(e["kind"] == "note" and e["text"] == AgentLoop.HELD_BACK for e in events)
    assert events[-1]["kind"] == "run_end" and (run / "summary.json").exists()


async def test_a_session_nobody_presses_in_folds_the_arm_at_each_end_as_it_always_has(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """drive closes the robot itself now, in `RobotSession.close`'s order, so a press can be
    answered at each step: an arm nobody pressed for is still folded as the session opens,
    stopped, folded again and let go of as it closes, with nothing said about a press."""
    registry = _arm_with_a_rest_pose(tmp_path)
    folds = _Folds(monkeypatch, hold_on=0)
    said: list[str] = []
    runs = tmp_path / "runs"
    args = DriveArgs(robot="arm-01", registry_dir=str(registry), llm="fake", runs_dir=str(runs))
    await run_drive(args, say=said.append, stream=io.StringIO("quit\n"), keyboard=False)
    assert folds.calls == 2
    assert folds.arm.sequence == ["rest", "stop", "rest", "close"]
    assert not [line for line in said if line.startswith("brake")], said
    summary = _summary_of(runs)
    assert (summary["outcome"], summary["reason"]) == ("success", "the session ended")


GATED_KICK = """---
glot: 0
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


async def test_the_fast_lanes_floors_answer_to_the_task_files_own_gates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A kick the `.glot` gates answered to the motion floor rather than the confirm one, the
    bug `verb_class` names in the run's own stepper: under `--yes` it ran unasked at a
    confidence the stepper would have asked about."""
    import roboglot.agent.decision.commands as commands
    import roboglot.agent.decision.factory as factory

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
    monkeypatch.setattr(
        factory, "make_decision_llm", lambda spec, llm_id=None, url=None: _Decider()
    )
    glot = tmp_path / "gated.glot"
    glot.write_text(GATED_KICK, encoding="utf-8")
    args = DriveArgs(
        robot="microduck:mock",
        llm="fake",
        decision_llm="scripted",
        glotfile=str(glot),
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
        (["--controller", "vla"], "this session names no policy server"),
        (["--controller", "vla", *POLICY, "--llm", "fake"], "--llm would do nothing"),
        (["--controller", "vla", *POLICY, "--play"], "--play would do nothing"),
        (["--controller", "vla", *POLICY, "--effort", "low"], "--effort says how hard"),
        (
            ["--controller", "vla", *POLICY, "--decision-llm", "kev", "--decision-mode", "on"],
            "runs no pilot LLM for a decision LLM to step in front of, and --decision-mode",
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
    import roboglot.cli as cli

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
    import roboglot.agent.decision.factory as factory
    import roboglot.drive.run as drive_run

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
    """`--decision-llm` alone is the fast lane, as it always was, and a `ROBOGLOT_DECISION_MODE`
    in a `.env` written for `roboglot run` gives no goal a stepper."""
    decider, kept = _decision_stubs(monkeypatch)
    monkeypatch.setenv("ROBOGLOT_DECISION_MODE", "on")
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
    from roboglot.agent.decision.notices import MODE_ON_UNMEASURED

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
    import roboglot.drive.run as drive_run

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
    from roboglot.drive.session import GoalSettings

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
        args,
        say=lambda _line: None,
        stream=io.StringIO("patrol the kitchen\nquit\n"),
        keyboard=False,
    )
    (goal_dir,) = (run_dir / "goals").iterdir()
    events = Transcript.read(goal_dir / "transcript.jsonl")
    start = next(e for e in events if e["kind"] == "run_start")
    assert start["memory"]["path"] == str(folder / "microduck-mock.jsonl")
    assert (folder / "microduck-mock.jsonl").exists(), "the goal's episode was not kept"


def _kept_sessions(patch: pytest.MonkeyPatch) -> list[Any]:
    """Every `DriveSession` `run_drive` builds, kept for the test to read."""
    import roboglot.drive.run as drive_run

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
    import roboglot.drive.run as drive_run

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
    from roboglot.drive.run import SESSION_POLICY_S
    from roboglot.glotfile.schema import POLICY_TOTAL_MAX_S

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
    import roboglot.agent.decision.factory as factory

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


async def test_a_vla_session_flies_each_goal_by_the_scripted_pilot_and_asks_no_llm(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The robot's registered pilot and `ROBOGLOT_LLM` name an LLM a VLA session never asks, so
    neither is resolved, and a key missing for it refuses nothing."""
    from roboglot.agent.providers.vla import VlaProvider

    monkeypatch.setenv("ROBOGLOT_LLM", "anthropic")
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
    assert (start["provider"], start["llm_id"]) == ("vla", "scripted:goal")
    summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
    assert summary["provider"] == "vla"
    assert run_counters(summary)[1] == "turns 0", "the record of a session that asks no LLM"


async def test_the_sessions_run_start_names_its_policy_server_and_a_bare_one_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import roboglot.drive.run as drive_run

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
    monkeypatch.setattr(drive_run, "_served", lambda transport: {})
    quiet = await run_drive(
        DriveArgs(robot="microduck:mock", llm="fake", runs_dir=str(tmp_path / "quiet")),
        say=lambda _line: None,
        stream=io.StringIO("quit\n"),
        keyboard=False,
    )
    assert "policy" not in start_of(quiet), "a policy that says nothing writes no block"
    served = {"server": "http://127.0.0.1:9", "async_chunks": False}
    monkeypatch.setattr(drive_run, "_served", lambda transport: served)
    armed = await run_drive(
        DriveArgs(robot="microduck:mock", llm="fake", runs_dir=str(tmp_path / "armed")),
        say=lambda _line: None,
        stream=io.StringIO("quit\n"),
        keyboard=False,
    )
    assert start_of(armed)["policy"] == served


def test_the_no_policy_refusal_names_drive(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import roboglot.cli as cli

    monkeypatch.setattr(cli, "_can_prompt", lambda: True)
    monkeypatch.setattr(cli, "_can_draw_a_prompt", lambda: True)
    result = runner.invoke(
        app, ["--no-color", "drive", "--robot", "lerobot:mock", "--controller", "vla"]
    )
    said = " ".join(result.output.split())
    assert "give roboglot drive its address with --policy-url" in said, said


async def test_a_vla_session_whose_task_file_does_not_allow_manipulate_is_refused_first(
    tmp_path: Path,
) -> None:
    """It started, put every line's doubt to the person, and then failed on the allowlist."""
    from roboglot.drive.run import DriveError

    glot = tmp_path / "look.glot"
    glot.write_text(
        "---\nglot: 0\nname: look\ndescription: d\nverbs:\n  allow: [report_state, stop]\n"
        "success: [x]\n---\n# Task\nx\n",
        encoding="utf-8",
    )
    runs = tmp_path / "runs"
    args = DriveArgs(robot="lerobot:mock", controller="vla", glotfile=str(glot), runs_dir=str(runs))
    with pytest.raises(DriveError, match="look does not allow it"):
        await run_drive(args, say=lambda _line: None, stream=io.StringIO("quit\n"), keyboard=False)
    assert not runs.exists(), "refused before anything was made"


async def test_decision_frames_are_read_from_the_command_line_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`ROBOGLOT_DECISION_FRAMES=1` in a `.env` written for `roboglot run` would refuse a session
    whose decision LLM may not be shown a frame, or send one where none was asked for."""
    _decider, kept = _decision_stubs(monkeypatch)
    monkeypatch.setenv("ROBOGLOT_DECISION_FRAMES", "1")
    args = DriveArgs(
        robot="microduck:mock", llm="fake", decision_llm="scripted", runs_dir=str(tmp_path)
    )
    await run_drive(args, say=lambda _line: None, stream=io.StringIO("quit\n"), keyboard=False)
    (session,) = kept
    assert session.settings.decision_frames is False
    assert "watch" not in session.goal_verbs
