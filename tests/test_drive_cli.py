"""`roboglot drive` from the command line, through a pipe, and its record replayed by `roboglot
log`."""

from __future__ import annotations

import asyncio
import contextlib
import functools
import io
import json
import logging
from collections.abc import Callable
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


_HANG_S = 300.0
"""How long a test of a press waits for the event it asserts: the suite's own hang bound
(`faulthandler_timeout`), so only a hang runs it out. Shorter waits ran out on a machine under
load, where nothing had hung."""


def _notes(run: Path) -> list[str]:
    return [e["text"] for e in Transcript.read(run / "transcript.jsonl") if e["kind"] == "note"]


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


async def test_a_failed_write_still_leaves_a_session_that_ended_as_asked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With nothing else ending the session, a `summary.json` the disk refused is the error
    that leaves it, after every other step of its end: a session whose record could not be
    written must not end as though it had been."""

    def full(self: Transcript, summary: dict[str, Any]) -> Path:
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(Transcript, "write_summary", full)
    args = DriveArgs(robot="microduck:mock", llm="fake", runs_dir=str(tmp_path))
    with pytest.raises(OSError, match="No space left on device"):
        await run_drive(args, say=lambda _: None, stream=io.StringIO("quit\n"), keyboard=False)
    assert Transcript.read(_the_run(tmp_path) / "transcript.jsonl")[-1]["kind"] == "run_end"


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
    as an arm a session has moved is, or, with `real`, is the mock's own drive. Everything around
    it is the real rest move's: the cancel it re-raises and the result the close reads."""

    def __init__(
        self, monkeypatch: pytest.MonkeyPatch, *, hold_on: int, real: bool = False
    ) -> None:
        from roboglot_lerobot.mock import LeRobotMock

        self.calls = 0
        self.arm: Any = None
        self.folding = asyncio.Event()
        never = asyncio.Event()
        folds = self
        own = LeRobotMock._drive_to_rest

        async def drive_to_rest(
            arm: Any, goal: dict[str, float], recorded: dict[str, float]
        ) -> RestResult:
            folds.arm = arm
            folds.calls += 1
            if folds.calls == hold_on:
                folds.folding.set()
                await never.wait()  # the fold under way when the press comes
            if real:
                return await own(arm, goal, recorded)
            return RestResult("arrived", "a stand-in fold")

        monkeypatch.setattr(LeRobotMock, "_drive_to_rest", drive_to_rest)


async def _pressed_during_fold(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    fold: int,
    held: asyncio.Event | None = None,
    then: Callable[[], None] | None = None,
    real: bool = False,
) -> tuple[asyncio.Task[Path], _Folds, list[str], Path]:
    """A session on the arm whose fold number `fold` a press lands on, or whatever else a
    stand-in of the caller's holds open until it sets `held`, as asyncio's own Ctrl-C handler
    delivers one: a cancel of the main task, and never a real signal. `then` is called once the
    press has been made, to let a stand-in go on, and `real` keeps the mock's own drive for every
    other fold (`_Folds`). Returns once the session has ended."""
    registry = _arm_with_a_rest_pose(tmp_path)
    folds = _Folds(monkeypatch, hold_on=fold, real=real)
    said: list[str] = []
    runs = tmp_path / "runs"
    args = DriveArgs(robot="arm-01", registry_dir=str(registry), llm="fake", runs_dir=str(runs))
    session = asyncio.ensure_future(
        run_drive(args, say=said.append, stream=io.StringIO("quit\n"), keyboard=False)
    )
    await asyncio.wait_for((held or folds.folding).wait(), _HANG_S)
    session.cancel()
    if then is not None:
        then()
    await asyncio.wait({session}, timeout=_HANG_S)
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


async def test_a_press_the_opening_fold_loses_still_ends_the_session_there(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A fold can lose the cancel a press is, as Python 3.11's `asyncio.wait_for` loses one that
    lands as its call returns (`release.FOLD_WENT_ON`), and go on to its pose; the connect awaits
    its fold directly, so it then returned, and the session opened and ran as if nobody had
    pressed, with nothing in its record of the press. The stand-in is the arm mock's
    `_drive_to_rest` taking the cancel on the opening fold and saying it arrived. The session must
    end there, say so, hold the arm and fold it no more."""
    from roboglot.drive.run import FOLD_DID_NOT_STOP
    from roboglot_lerobot.mock import LeRobotMock

    folding, never = asyncio.Event(), asyncio.Event()
    arms: list[Any] = []

    async def lossy(arm: Any, goal: dict[str, float], recorded: dict[str, float]) -> RestResult:
        arms.append(arm)
        if len(arms) == 1:
            folding.set()
            with contextlib.suppress(asyncio.CancelledError):  # lost as the call came back
                await never.wait()
        return RestResult("arrived", "a stand-in fold")

    monkeypatch.setattr(LeRobotMock, "_drive_to_rest", lossy)
    registry = _arm_with_a_rest_pose(tmp_path)
    runs = tmp_path / "runs"
    said: list[str] = []
    args = DriveArgs(robot="arm-01", registry_dir=str(registry), llm="fake", runs_dir=str(runs))
    session = asyncio.ensure_future(
        run_drive(args, say=said.append, stream=io.StringIO("quit\n"), keyboard=False)
    )
    await asyncio.wait_for(folding.wait(), _HANG_S)
    session.cancel()
    await asyncio.wait({session}, timeout=_HANG_S)
    assert session.cancelled(), "the session went on past the press"
    assert not [line for line in said if line.startswith("drive ")], said
    assert len(arms) == 1, "the arm was folded again"
    sequence = arms[0].sequence
    assert sequence[sequence.index("rest") :] == ["rest", "stop", "close"], sequence
    events = Transcript.read(_the_run(runs) / "transcript.jsonl")
    assert any(e["kind"] == "note" and e["text"] == FOLD_DID_NOT_STOP for e in events), events
    assert events[-1]["kind"] == "run_end" and events[-1]["outcome"] == "aborted"


async def test_a_press_on_the_close_after_a_missed_opening_fold_says_the_refusal_and_the_close(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The opening fold misses (the arm mock's own `rest_fails`, a stall that sends nothing), and
    the connect closes the arm before it refuses the session. A press on that close (its read of
    where the arm is, held open as a slow bus read is) was said to have stopped the fold, which
    had ended by missing, and the connect's refusal was never said or recorded. Both are said now:
    the refusal, and that the press came on the close, which is made again to its end."""
    from roboglot.drive.run import CONNECTS_CLOSE_PRESSED
    from roboglot_lerobot.mock import LeRobotMock

    stall = "a stand-in stall: the shoulder met the desk"
    rest = LeRobotMock.go_to_rest

    async def missing(arm: Any) -> RestResult:
        arm.rest_fails = stall
        return await rest(arm)

    monkeypatch.setattr(LeRobotMock, "go_to_rest", missing)
    reading, released = _hold_the_close_read(monkeypatch)
    session, folds, said, run = await _pressed_during_fold(
        tmp_path, monkeypatch, fold=0, held=reading, then=released.set, real=True
    )
    assert session.cancelled()
    notes = [e["text"] for e in Transcript.read(run / "transcript.jsonl") if e["kind"] == "note"]
    refusal = f"arm-01: the arm did not reach its rest pose: {stall}"
    assert FOLD_STOPPED not in notes, "a fold that had missed is said to have been stopped"
    assert notes.index(refusal) < notes.index(CONNECTS_CLOSE_PRESSED), notes
    assert any(refusal in line for line in said), said
    assert not folds.arm.connected and str(folds.arm.close_note) in notes, notes


async def test_a_press_on_the_close_after_a_rest_move_that_raised_says_the_refusal_too(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A body whose rest move raises, which `go_to_rest_if_any` turns into a fold that missed:
    the connect closes the arm before it refuses the session, and a press lands on that close. The
    fold was watched beginning and never ending, so the press was said to have stopped it, and the
    refusal was lost, as for a fold that missed by stalling. The stand-in is the arm's adapter's
    own rest move raising, as a third party's that does not catch its backend's error would."""
    from roboglot.drive.run import CONNECTS_CLOSE_PRESSED
    from roboglot_lerobot import LeRobotAdapter

    async def raising(_adapter: Any) -> Any:
        raise RuntimeError("the rest move's first read raised")

    monkeypatch.setattr(LeRobotAdapter, "go_to_rest", raising)
    reading, released = _hold_the_close_read(monkeypatch)
    session, _folds, _said, run = await _pressed_during_fold(
        tmp_path, monkeypatch, fold=0, held=reading, then=released.set
    )
    assert session.cancelled()
    notes = _notes(run)
    refusal = (
        "arm-01: the arm did not reach its rest pose: "
        "RuntimeError: the rest move's first read raised"
    )
    assert FOLD_STOPPED not in notes, "a rest move that raised is said to have been stopped"
    assert notes.index(refusal) < notes.index(CONNECTS_CLOSE_PRESSED), notes


_FOLD_WORDS = ("the fold was stopped", "the fold was told to stop", "did not stop for the press")
"""What each line saying what became of a fold under a press says, in drive's words and in the
session logger's (`FOLD_STOPPED`, `FOLD_WENT_ON`, `FOLD_DID_NOT_STOP`, `FOLD_WENT_TO_ITS_END`)."""


@pytest.mark.parametrize("ends", ["stopped", "went on", "at its pose", "short of its pose"])
async def test_what_a_press_did_to_the_opening_fold_is_said_once_and_truly(
    ends: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """The connect folds through `fold_unless_stopped` and keeps what a press did to that fold
    (`RobotSession.opening_fold`), and the session read the same fold again from outside, by
    wrapping the arm's rest move: a stopped fold was said twice, on the session's logger and by
    drive, and a fold that took the press and ended short of its pose was recorded as the refusal
    and "the press came as the session was closing the arm after that", of a close the connect
    never reached. The fold here is pressed and stops; takes every cancel and does not end; or
    takes the press and runs the mock's own drive on to its pose, or ends short of it (`stalled`,
    as the real drive ends on a joint that stops moving). What became of it is said once, in
    drive's words, the arm is held and nothing folds it again. The second case waits out the
    fold's bound twice (`release.STOPPED_FOLD_S`)."""
    from roboglot.drive.run import CONNECTS_CLOSE_PRESSED, FOLD_DID_NOT_STOP
    from roboglot.release import FOLD_WENT_ON
    from roboglot_lerobot.mock import LeRobotMock

    caplog.set_level(logging.WARNING, logger="roboglot.mcp")
    folding, let_go = asyncio.Event(), asyncio.Event()
    own, arms = LeRobotMock._drive_to_rest, []

    async def drive_to_rest(arm: Any, goal: dict[str, float], recorded: dict[str, float]) -> Any:
        arms.append(arm)
        if len(arms) != 1:
            return RestResult("arrived", "a stand-in fold")
        folding.set()
        while not let_go.is_set():
            try:
                await let_go.wait()
            except asyncio.CancelledError:
                if ends == "stopped":
                    raise
                if ends != "went on":
                    break
        if ends == "at its pose":
            return await own(arm, goal, recorded)
        if ends == "short of its pose":
            return RestResult("stalled", "shoulder_pan stopped moving 20 degrees short")
        return RestResult("arrived", "a stand-in fold, let go once the session had ended")

    monkeypatch.setattr(LeRobotMock, "_drive_to_rest", drive_to_rest)
    registry = _arm_with_a_rest_pose(tmp_path)
    runs = tmp_path / "runs"
    said: list[str] = []
    args = DriveArgs(robot="arm-01", registry_dir=str(registry), llm="fake", runs_dir=str(runs))
    session = asyncio.ensure_future(
        run_drive(args, say=said.append, stream=io.StringIO("quit\n"), keyboard=False)
    )
    try:
        await asyncio.wait_for(folding.wait(), _HANG_S)
        session.cancel()
        await asyncio.wait({session}, timeout=_HANG_S)
        assert session.done(), "the session never ended"
    finally:
        let_go.set()
    expected = {
        "stopped": FOLD_STOPPED,
        "went on": FOLD_WENT_ON,
        "at its pose": FOLD_DID_NOT_STOP,
        "short of its pose": FOLD_DID_NOT_STOP,
    }[ends]
    told = [line for line in said if any(w in line for w in _FOLD_WORDS)]
    logged = [m for m in caplog.messages if any(w in m for w in _FOLD_WORDS)]
    assert told == [f"{'brake':<8} {expected}"] and not logged, f"{ends}: {said}\n{logged}"
    assert session.cancelled(), "the press is what the session ended on"
    assert len(arms) == 1, "the arm was folded again"
    sequence = arms[0].sequence
    assert sequence[sequence.index("rest") :] == ["rest", "stop", "close"], sequence
    events = Transcript.read(_the_run(runs) / "transcript.jsonl")
    notes = [e["text"] for e in events if e["kind"] == "note"]
    assert notes.count(expected) == 1, notes
    assert CONNECTS_CLOSE_PRESSED not in notes, "the press came during the fold, not on a close"
    assert not [n for n in notes if "did not reach its rest pose" in n], notes
    assert events[-1]["kind"] == "run_end" and events[-1]["outcome"] == "aborted"
    assert (_the_run(runs) / "summary.json").exists()


async def test_a_press_while_the_close_stops_the_heartbeat_still_ends_the_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A session's close stops the heartbeat first, and the press lands there. Drive closes its
    robot with its own steps (`_close_robot`), each taking a press, and never calls
    `RobotSession.close`, which raises the press it meets once the close has run: through it, the
    press went past the session's end, and the record had no `run_end` and no `summary.json`. The
    beat stand-in takes the cancel `Heartbeat.stop` sends it and answers once let go after the
    press, as a beat whose cancel Python 3.11's `wait_for` lost does. The line `quit` is read
    once the beat is under way, so the close finds it there."""
    import threading

    from roboglot_lerobot.mock import LeRobotMock

    _Folds(monkeypatch, hold_on=0)
    beating, held, released = threading.Event(), asyncio.Event(), asyncio.Event()
    beat = LeRobotMock.heartbeat

    async def heartbeat(arm: Any) -> None:
        task = asyncio.current_task()
        if task is None or task.get_name() != "roboglot-heartbeat":
            return await beat(arm)
        beating.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            held.set()
            await released.wait()

    class _QuitOnceBeating(io.StringIO):
        def readline(self, size: int | None = -1) -> str:
            beating.wait(_HANG_S)
            return super().readline()

    monkeypatch.setattr(LeRobotMock, "heartbeat", heartbeat)
    registry = _arm_with_a_rest_pose(tmp_path)
    runs = tmp_path / "runs"
    args = DriveArgs(robot="arm-01", registry_dir=str(registry), llm="fake", runs_dir=str(runs))
    session = asyncio.ensure_future(
        run_drive(args, say=lambda _: None, stream=_QuitOnceBeating("quit\n"), keyboard=False)
    )
    await asyncio.wait_for(held.wait(), _HANG_S)
    session.cancel()
    await asyncio.sleep(0)
    released.set()
    await asyncio.wait({session}, timeout=_HANG_S)
    assert session.cancelled(), "the press goes on up once the session has ended in order"
    run = _the_run(runs)
    events = Transcript.read(run / "transcript.jsonl")
    assert events[-1]["kind"] == "run_end", [e["kind"] for e in events[-6:]]
    assert events[-1]["outcome"] == "success", "the session had ended as asked, by `quit`"
    assert (run / "summary.json").exists()


@pytest.mark.parametrize("ending", ["nobody", "pressed-in-the-fold"])
async def test_watching_the_opening_fold_leaves_the_adapter_as_it_found_it(
    ending: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The opening fold was once watched by wrapping the adapter's rest move for the connect
    alone; the session now reads what `RobotSession.connect` recorded of it, and nothing wraps
    the rest move. An adapter with no `go_to_rest` of its own, as every shipped one is, must still
    have none after the session, however it ended: a wrapper left behind would be read as the
    opening fold by anything that folded the arm later."""
    import roboglot.mcp_server as mcp_server

    plan_of, adapters = mcp_server.team_from_flags, []

    def planned(*args: Any, **kwargs: Any) -> Any:
        plan = plan_of(*args, **kwargs)
        adapters.extend(plan.adapters.values())
        return plan

    monkeypatch.setattr(mcp_server, "team_from_flags", planned)
    if ending == "nobody":
        _Folds(monkeypatch, hold_on=0)
        registry = _arm_with_a_rest_pose(tmp_path)
        args = DriveArgs(
            robot="arm-01", registry_dir=str(registry), llm="fake", runs_dir=str(tmp_path)
        )
        await run_drive(args, say=lambda _: None, stream=io.StringIO("quit\n"), keyboard=False)
    else:
        await _pressed_during_fold(tmp_path, monkeypatch, fold=1)
    (adapter,) = adapters
    assert "go_to_rest" not in vars(adapter), vars(adapter)["go_to_rest"]


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


async def test_a_second_press_while_the_hold_is_out_still_says_what_became_of_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A first press stops the closing fold and the stop that holds the arm goes out. A second
    press while that stop was still on its way cut its wait short, and what became of it was
    never said, where a run says Roboglot cannot tell whether the arm is holding where it stopped.
    The stand-in holds that stop (the arm mock's second, after the close's own) on its way until
    the session has ended."""
    from roboglot_lerobot.mock import LeRobotMock

    folds = _Folds(monkeypatch, hold_on=2)
    stopping, released = asyncio.Event(), asyncio.Event()
    stop, stops = LeRobotMock.stop, [0]

    async def held_stop(arm: Any) -> None:
        stops[0] += 1
        if stops[0] == 2:
            stopping.set()
            await released.wait()
        await stop(arm)

    monkeypatch.setattr(LeRobotMock, "stop", held_stop)
    registry = _arm_with_a_rest_pose(tmp_path)
    runs = tmp_path / "runs"
    args = DriveArgs(robot="arm-01", registry_dir=str(registry), llm="fake", runs_dir=str(runs))
    session = asyncio.ensure_future(
        run_drive(args, say=lambda _: None, stream=io.StringIO("quit\n"), keyboard=False)
    )
    await asyncio.wait_for(folds.folding.wait(), _HANG_S)
    session.cancel()  # the first press, on the closing fold
    await asyncio.wait_for(stopping.wait(), _HANG_S)
    session.cancel()  # the second, while the hold is on its way
    await asyncio.wait({session}, timeout=_HANG_S)
    released.set()
    assert session.cancelled()
    notes = _notes(_the_run(runs))
    assert any(n.startswith("the stop after the press: ") for n in notes), notes
    assert notes.index(FOLD_STOPPED) > 0, notes


async def test_a_closing_fold_that_will_not_end_when_told_is_never_said_to_have_stopped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A fold can lose the cancel a press is (Python 3.11's `asyncio.wait_for`, a third party's
    fold) and go on. Raced against the press (`fold_unless_stopped`), it is told to stop twice
    and, still going, said to have gone on (`FOLD_WENT_ON`); awaited directly, the session waited
    on it for good. The stand-in takes every cancel until the test lets it go. It waits the code's
    own `STOPPED_FOLD_S` twice, and what it asserts does not depend on how long that takes."""
    from roboglot.release import FOLD_WENT_ON
    from roboglot_lerobot.mock import LeRobotMock

    folding, let_go, calls = asyncio.Event(), asyncio.Event(), [0]

    async def deaf(arm: Any, goal: dict[str, float], recorded: dict[str, float]) -> RestResult:
        calls[0] += 1
        if calls[0] == 2:
            folding.set()
            while not let_go.is_set():
                with contextlib.suppress(asyncio.CancelledError):  # the cancel lost
                    await let_go.wait()
        return RestResult("arrived", "a stand-in fold")

    monkeypatch.setattr(LeRobotMock, "_drive_to_rest", deaf)
    registry = _arm_with_a_rest_pose(tmp_path)
    runs = tmp_path / "runs"
    args = DriveArgs(robot="arm-01", registry_dir=str(registry), llm="fake", runs_dir=str(runs))
    session = asyncio.ensure_future(
        run_drive(args, say=lambda _: None, stream=io.StringIO("quit\n"), keyboard=False)
    )
    await asyncio.wait_for(folding.wait(), _HANG_S)
    session.cancel()
    await asyncio.wait({session}, timeout=_HANG_S)
    let_go.set()
    assert session.cancelled(), "the session waited on a fold that would not end"
    notes = _notes(_the_run(runs))
    assert FOLD_WENT_ON in notes and FOLD_STOPPED not in notes, notes


def _hold_the_close_read(monkeypatch: pytest.MonkeyPatch) -> tuple[asyncio.Event, asyncio.Event]:
    """The arm's close's read of where the arm is, which decides whether its torque is let go of,
    held open on the first close as a slow bus read is, until the test lets it go on to the real
    read: `reading` is set as it begins, and `released` lets it go."""
    from roboglot_lerobot.mock import LeRobotMock

    reading, released = asyncio.Event(), asyncio.Event()
    not_resting = LeRobotMock._not_resting

    async def held(arm: Any) -> Any:
        if not reading.is_set():
            reading.set()
            await released.wait()
        return await not_resting(arm)

    monkeypatch.setattr(LeRobotMock, "_not_resting", held)
    return reading, released


async def test_a_press_during_the_transports_close_lets_that_close_end_and_says_so(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A Ctrl-C on the arm's close, whose read of where the arm is decides whether torque is let
    go of, cut that close short where it landed: the arm stayed connected with every motor
    energised, its close said nothing, and the record said the session had ended. The close now
    goes on to its end through the press, its line is said and recorded, and the record says the
    press came; a close cut short, a missing line or a press forgotten fails here."""
    from roboglot.drive.run import CLOSED_THROUGH_A_PRESS

    reading, released = _hold_the_close_read(monkeypatch)
    session, folds, said, run = await _pressed_during_fold(
        tmp_path, monkeypatch, fold=0, held=reading, then=released.set
    )
    arm = folds.arm
    assert session.cancelled(), "the press is still a press"
    assert not arm.connected, "the close was cut short by the press"
    notes = [e["text"] for e in Transcript.read(run / "transcript.jsonl") if e["kind"] == "note"]
    assert arm.close_note is not None and str(arm.close_note) in notes, notes
    assert notes.index(CLOSED_THROUGH_A_PRESS) < notes.index(str(arm.close_note)), notes
    assert any(CLOSED_THROUGH_A_PRESS in line for line in said), said
    assert _summary_of(tmp_path / "runs")["outcome"] == "success"


async def test_a_close_still_going_after_its_bound_is_said_and_left_to_end(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A close that has not ended as long after a press as the body says its close can take
    (`close_bound_s`) is not waited for longer: the person is told Roboglot cannot tell what it
    did, in that figure, the record still ends, and the close is let go of. The arm's figure is
    made nothing here, so no clock decides the test, and the line saying it shows drive read it."""
    import roboglot.drive.run as drive_run
    from roboglot_lerobot.mock import LeRobotMock

    monkeypatch.setattr(LeRobotMock, "close_bound_s", property(lambda _arm: 0.0))
    reading, released = _hold_the_close_read(monkeypatch)
    session, folds, _said, run = await _pressed_during_fold(
        tmp_path, monkeypatch, fold=0, held=reading
    )
    assert session.cancelled()
    events = Transcript.read(run / "transcript.jsonl")
    line = drive_run.CLOSE_NOT_ENDED.format(seconds=0.0)
    assert any(e["kind"] == "note" and e["text"] == line for e in events), events
    assert events[-1]["kind"] == "run_end"
    left = set(drive_run._CLOSES_LEFT)
    assert left, "the close was not left to end by itself"
    released.set()
    await asyncio.wait(left, timeout=_HANG_S)
    assert not folds.arm.connected and not drive_run._CLOSES_LEFT


@pytest.mark.parametrize("figure", ["raises", "none", "not a number", "negative", "a bool"])
def test_a_close_figure_that_cannot_be_read_is_no_figure(figure: str) -> None:
    """The close's bound is read off the body before the close begins, outside every guard the
    close's own steps have, so a figure that raised skipped the transport's close, `run_end` and
    the summary. A figure that raises, or that is not a finite number of seconds, is no figure:
    the close is waited for `CLOSE_AFTER_A_PRESS_S`, as for a body that names none."""
    import roboglot.drive.run as drive_run

    values: dict[str, object] = {
        "none": None,
        "not a number": float("nan"),
        "negative": -1.0,
        "a bool": True,
    }

    class Body:
        @property
        def close_bound_s(self) -> object:
            if figure == "raises":
                raise RuntimeError("a stand-in: the figure's transport is gone")
            return values[figure]

    assert drive_run._close_bound_s(Body()) == drive_run.CLOSE_AFTER_A_PRESS_S


@pytest.mark.parametrize("ends", ["cancelled", "raised"])
async def test_a_close_cut_short_after_a_press_is_never_said_to_have_ended(
    ends: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A close a press landed on was said to have gone on to its end whenever its task was done,
    and a task asyncio cancelled is done, as every task is that `asyncio.Runner.close` cancels
    after a second real Ctrl-C, and so is one whose close raised: the arm stayed connected and
    energised, with no close note, under a line saying the close had ended. Only a close that
    returned is said to have; one cut short or failed is said not to have finished, and what to
    do. The stand-in is the arm's close's read, held until the press and then cancelled with
    every other task, or let go to raise."""
    from roboglot.drive.run import CLOSE_CUT_SHORT, CLOSED_THROUGH_A_PRESS
    from roboglot_lerobot.mock import LeRobotMock

    reading, released = asyncio.Event(), asyncio.Event()

    async def read(arm: Any) -> Any:
        reading.set()
        await released.wait()
        raise ConnectionError("the bus went quiet")

    monkeypatch.setattr(LeRobotMock, "_not_resting", read)
    folds = _Folds(monkeypatch, hold_on=0)
    registry = _arm_with_a_rest_pose(tmp_path)
    runs = tmp_path / "runs"
    args = DriveArgs(robot="arm-01", registry_dir=str(registry), llm="fake", runs_dir=str(runs))
    session = asyncio.ensure_future(
        run_drive(args, say=lambda _: None, stream=io.StringIO("quit\n"), keyboard=False)
    )
    await asyncio.wait_for(reading.wait(), _HANG_S)
    session.cancel()
    if ends == "raised":
        released.set()
    else:
        for _ in range(20):  # the press taken first, then what `Runner.close` does
            await asyncio.sleep(0)
        for task in asyncio.all_tasks() - {asyncio.current_task()}:
            task.cancel()
    await asyncio.wait({session}, timeout=_HANG_S)
    assert session.done()
    why = "it was cancelled" if ends == "cancelled" else "ConnectionError: the bus went quiet"
    notes = _notes(_the_run(runs))
    assert CLOSED_THROUGH_A_PRESS not in notes, notes
    assert CLOSE_CUT_SHORT.format(why=why) in notes, notes
    assert folds.arm.connected, "the stand-in keeps the close from its disconnect"


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


async def test_a_press_the_heartbeats_stop_takes_still_keeps_the_closing_fold_from_starting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`Heartbeat.stop` waits for its task under a `suppress` that takes its caller's cancel
    too, so a press there leaves no exception behind, and only the task's count of the cancels it
    was asked for (`Task.cancelling`) says it came. The stand-in is the arm mock's beat, held as a
    bus call in a thread is, and still held once told to stop, so the close's first step waits on
    it when the press comes."""
    import threading

    from roboglot.agent.loop import AgentLoop
    from roboglot_lerobot.mock import LeRobotMock

    folds = _Folds(monkeypatch, hold_on=0)
    beating, told, never = threading.Event(), asyncio.Event(), asyncio.Event()
    beat, beats = LeRobotMock.heartbeat, [0]

    async def held_beat(arm: Any) -> None:
        beats[0] += 1
        if beats[0] == 1:
            beating.set()
            try:
                await never.wait()
            except asyncio.CancelledError:
                told.set()
                await never.wait()  # the bus call still out; the press cancels this wait too
                raise
        await beat(arm)

    class _QuitOnceBeating(io.StringIO):
        def readline(self, size: int | None = -1) -> str:
            beating.wait(_HANG_S)
            return super().readline()

    monkeypatch.setattr(LeRobotMock, "heartbeat", held_beat)
    registry = _arm_with_a_rest_pose(tmp_path)
    runs = tmp_path / "runs"
    args = DriveArgs(robot="arm-01", registry_dir=str(registry), llm="fake", runs_dir=str(runs))
    session = asyncio.ensure_future(
        run_drive(args, say=lambda _: None, stream=_QuitOnceBeating("quit\n"), keyboard=False)
    )
    await asyncio.wait_for(told.wait(), _HANG_S)
    session.cancel()
    await asyncio.wait({session}, timeout=_HANG_S)
    assert session.cancelled(), "the press the heartbeat's stop took was not raised again"
    assert folds.calls == 1, "the closing fold started after the press"
    events = Transcript.read(_the_run(runs) / "transcript.jsonl")
    assert any(e["kind"] == "note" and e["text"] == AgentLoop.HELD_BACK for e in events), events


@pytest.mark.parametrize("body", ["base", "dry-arm"])
async def test_a_press_on_the_closes_stop_where_nothing_folds_says_nothing_of_a_fold(
    body: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A base has no rest pose and a dry run folds nothing, so a press on the close's stop there
    keeps no fold from starting, and nothing may say one was kept from starting or stopped. The
    transport is still closed, once, and the record still ends."""
    from roboglot.agent.loop import AgentLoop
    from roboglot.release import FOLD_WENT_ON

    if body == "base":
        from roboglot_microduck import MicroduckAdapter as Body
    else:
        from roboglot_lerobot.mock import LeRobotMock as Body
    stopping, never, closed = asyncio.Event(), asyncio.Event(), [0]
    stop, close = Body.stop, Body.close

    async def held_stop(transport: Any) -> None:
        if not stopping.is_set():
            stopping.set()
            await never.wait()
        await stop(transport)

    async def closing(transport: Any) -> None:
        closed[0] += 1
        await close(transport)

    monkeypatch.setattr(Body, "stop", held_stop)
    monkeypatch.setattr(Body, "close", closing)
    runs = tmp_path / "runs"
    if body == "base":
        args = DriveArgs(robot="microduck:mock", llm="fake", runs_dir=str(runs))
    else:
        registry = _arm_with_a_rest_pose(tmp_path)
        args = DriveArgs(
            robot="arm-01", registry_dir=str(registry), llm="fake", runs_dir=str(runs), dry_run=True
        )
    session = asyncio.ensure_future(
        run_drive(args, say=lambda _: None, stream=io.StringIO("quit\n"), keyboard=False)
    )
    await asyncio.wait_for(stopping.wait(), _HANG_S)
    session.cancel()
    await asyncio.wait({session}, timeout=_HANG_S)
    events = Transcript.read(_the_run(runs) / "transcript.jsonl")
    notes = {e["text"] for e in events if e["kind"] == "note"}
    assert closed[0] == 1 and events[-1]["kind"] == "run_end", (closed, events[-1])
    assert not {FOLD_STOPPED, FOLD_WENT_ON, AgentLoop.HELD_BACK} & notes, notes


def _a_brake_on_its_way(monkeypatch: pytest.MonkeyPatch) -> tuple[asyncio.Event, asyncio.Event]:
    """The first stop the arm is sent, a brake's, held on its way as a stop waiting its turn on a
    slow bus is (`stopping` is set as it is held), and `closing` set as a front end begins to
    close the session's commands, which then wait on that brake."""
    from roboglot.drive.session import DriveSession
    from roboglot_lerobot.mock import LeRobotMock

    stopping, closing, never = asyncio.Event(), asyncio.Event(), asyncio.Event()
    stop, close = LeRobotMock.stop, DriveSession.close

    async def held_stop(arm: Any) -> None:
        if not stopping.is_set():
            stopping.set()
            await never.wait()
        await stop(arm)

    async def spied_close(session: Any, *args: Any, **kwargs: Any) -> None:
        closing.set()
        await close(session, *args, **kwargs)

    monkeypatch.setattr(LeRobotMock, "stop", held_stop)
    monkeypatch.setattr(DriveSession, "close", spied_close)
    return stopping, closing


def _held_back_and_success(run: Path) -> None:
    from roboglot.agent.loop import AgentLoop

    events = Transcript.read(run / "transcript.jsonl")
    assert any(e["kind"] == "note" and e["text"] == AgentLoop.HELD_BACK for e in events), events
    # the session had ended as asked, as a pipe's that the same press lands on has
    assert events[-1]["kind"] == "run_end" and events[-1]["outcome"] == "success", events[-1]


async def test_a_press_while_the_page_closes_its_commands_keeps_the_closing_fold_from_starting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The page closes the session's commands itself once it quits, before `run_drive`'s
    `finally`, and a Ctrl-C then, the first once its prompt has closed, was taken for the press
    that ended the session: the closing fold moved the arm to its pose with nothing said, and the
    record said the session was interrupted. A pipe answers the same press as a press during the
    close. Here a brake's stop is still on its way when `quit` comes, so the close waits on it."""
    from roboglot.front import Channel, Front

    stopping, closing = _a_brake_on_its_way(monkeypatch)
    folds = _Folds(monkeypatch, hold_on=0)
    registry = _arm_with_a_rest_pose(tmp_path)
    runs = tmp_path / "runs"
    ready = asyncio.Event()

    def say(line: str) -> None:
        if line.startswith("drive "):
            ready.set()

    front = Front(Channel(io.StringIO()), "drive")
    args = DriveArgs(robot="arm-01", registry_dir=str(registry), llm="fake", runs_dir=str(runs))
    session = asyncio.ensure_future(run_drive(args, say=say, front=front))
    await asyncio.wait_for(ready.wait(), _HANG_S)
    front.on_op({"op": "stop"})
    await asyncio.wait_for(stopping.wait(), _HANG_S)
    front.on_op({"op": "quit"})
    await asyncio.wait_for(closing.wait(), _HANG_S)
    session.cancel()
    await asyncio.wait({session}, timeout=_HANG_S)
    assert session.cancelled()
    assert folds.calls == 1, "the closing fold started after the press"
    _held_back_and_success(_the_run(runs))


async def test_the_pages_stop_while_the_page_closes_its_commands_raises_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The page's Stop while the page's front end closes its commands, which wait here on a
    brake's stop still on its way, is a press during the close, and keeps the fold from starting.
    No Ctrl-C was made, so nothing may leave the session: a bare cancel there, which asyncio has
    no Ctrl-C to turn into an interrupt, would be a traceback where the session had ended as
    asked."""
    from roboglot.front import Channel, Front

    stopping, closing = _a_brake_on_its_way(monkeypatch)
    folds = _Folds(monkeypatch, hold_on=0)
    registry = _arm_with_a_rest_pose(tmp_path)
    runs = tmp_path / "runs"
    ready = asyncio.Event()

    def say(line: str) -> None:
        if line.startswith("drive "):
            ready.set()

    front = Front(Channel(io.StringIO()), "drive")
    args = DriveArgs(robot="arm-01", registry_dir=str(registry), llm="fake", runs_dir=str(runs))
    session = asyncio.ensure_future(run_drive(args, say=say, front=front))
    await asyncio.wait_for(ready.wait(), _HANG_S)
    front.on_op({"op": "stop"})
    await asyncio.wait_for(stopping.wait(), _HANG_S)
    front.on_op({"op": "quit"})
    await asyncio.wait_for(closing.wait(), _HANG_S)
    front.on_op({"op": "stop"})
    await asyncio.wait({session}, timeout=_HANG_S)
    assert session.done() and not session.cancelled() and session.exception() is None
    assert folds.calls == 1, "the closing fold started after the Stop"
    _held_back_and_success(_the_run(runs))


async def _page_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, fold: int
) -> tuple[asyncio.Task[Path], _Folds, Any, Path, asyncio.Event]:
    """A session behind the page on the arm, its fold number `fold` held open (`_Folds`), and an
    event set as the session says its first line."""
    from roboglot.front import Channel, Front

    folds = _Folds(monkeypatch, hold_on=fold)
    registry = _arm_with_a_rest_pose(tmp_path)
    runs = tmp_path / "runs"
    ready = asyncio.Event()

    def say(line: str) -> None:
        if line.startswith("drive "):
            ready.set()

    front = Front(Channel(io.StringIO()), "drive")
    args = DriveArgs(robot="arm-01", registry_dir=str(registry), llm="fake", runs_dir=str(runs))
    session = asyncio.ensure_future(run_drive(args, say=say, front=front))
    return session, folds, front, runs, ready


async def test_the_pages_stop_during_the_opening_fold_stops_it_as_a_press_does(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The page's ops are read by its front end alone, which has not begun while the session
    connects, so a Stop during the opening fold stopped nothing, where `web-ui.md` says every Stop
    brakes. It stops the fold now as a Ctrl-C does, and the session ends as one the page stopped
    before it connected ends: refused, in the page's words."""
    from roboglot.drive.run import PAGE_STOP_WHILE_CONNECTING, DriveError

    session, folds, front, runs, _ready = await _page_session(tmp_path, monkeypatch, fold=1)
    await asyncio.wait_for(folds.folding.wait(), _HANG_S)
    front.on_op({"op": "stop"})
    await asyncio.wait({session}, timeout=_HANG_S)
    assert session.done() and isinstance(session.exception(), DriveError), session
    assert str(session.exception()) == PAGE_STOP_WHILE_CONNECTING
    assert folds.calls == 1, "the fold the Stop stopped was started again"
    events = Transcript.read(_the_run(runs) / "transcript.jsonl")
    assert any(e["kind"] == "note" and e["text"] == FOLD_STOPPED for e in events), events
    end = events[-1]
    assert (end["kind"], end["outcome"], end["reason"]) == (
        "run_end",
        "aborted",
        PAGE_STOP_WHILE_CONNECTING,
    )


async def test_the_pages_stop_before_the_connect_says_nothing_of_an_arm(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A Stop the page sent before the session began refuses it before anything connects. The
    same Stop also reached the session's own reader, ran at its first wait, in its close, and was
    taken there for a press on the close: the person was told that an arm nothing had connected
    to was holding where it stopped. It ends the session in the page's words, and says nothing of
    an arm."""
    from roboglot.agent.loop import AgentLoop
    from roboglot.drive.run import DriveError
    from roboglot.front import Channel, Front

    folds = _Folds(monkeypatch, hold_on=0)
    registry = _arm_with_a_rest_pose(tmp_path)
    runs = tmp_path / "runs"
    said: list[str] = []
    front = Front(Channel(io.StringIO()), "drive")
    front.on_op({"op": "stop"})
    args = DriveArgs(robot="arm-01", registry_dir=str(registry), llm="fake", runs_dir=str(runs))
    with pytest.raises(DriveError, match="the page's Stop came before the robot connected"):
        await run_drive(args, say=said.append, front=front)
    assert folds.calls == 0 and AgentLoop.HELD_BACK not in _notes(_the_run(runs))
    assert not [line for line in said if line.startswith("brake")], said


async def test_the_pages_stop_during_the_closing_fold_stops_it_as_a_press_does(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """After the page quits, its front end has returned, so a Stop during the closing fold
    stopped nothing and the arm went on to its pose. It stops the fold now as a Ctrl-C does, and
    the session, which ended as asked, ends as one: nothing is raised."""
    session, folds, front, runs, ready = await _page_session(tmp_path, monkeypatch, fold=2)
    await asyncio.wait_for(ready.wait(), _HANG_S)
    front.on_op({"op": "quit"})
    await asyncio.wait_for(folds.folding.wait(), _HANG_S)
    front.on_op({"op": "stop"})
    await asyncio.wait({session}, timeout=_HANG_S)
    assert session.done() and not session.cancelled() and session.exception() is None
    sequence = folds.arm.sequence
    closing = len(sequence) - 1 - sequence[::-1].index("rest")
    assert sequence[closing:] == ["rest", "stop", "close"], sequence
    events = Transcript.read(_the_run(runs) / "transcript.jsonl")
    assert any(e["kind"] == "note" and e["text"] == FOLD_STOPPED for e in events), events
    assert events[-1]["kind"] == "run_end" and events[-1]["outcome"] == "success"


async def test_a_press_while_the_keyboard_closes_its_commands_keeps_the_closing_fold_from_starting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The same at the keyboard, through the real prompt on prompt_toolkit's own pipe input:
    `stop` brakes, `quit` closes the prompt, and a Ctrl-C while the commands are closed, the
    ordinary third press of a person who pressed twice to end the session, is a press during the
    close."""
    from prompt_toolkit.input import create_pipe_input
    from prompt_toolkit.output import DummyOutput

    import roboglot.drive.terminal as terminal

    stopping, closing = _a_brake_on_its_way(monkeypatch)
    folds = _Folds(monkeypatch, hold_on=0)
    registry = _arm_with_a_rest_pose(tmp_path)
    runs = tmp_path / "runs"
    with create_pipe_input() as typed:
        keyboard = functools.partial(
            terminal.drive_from_keyboard, input=typed, output=DummyOutput(), patch=False
        )
        monkeypatch.setattr(terminal, "drive_from_keyboard", keyboard)
        args = DriveArgs(robot="arm-01", registry_dir=str(registry), llm="fake", runs_dir=str(runs))
        session = asyncio.ensure_future(run_drive(args, say=lambda _: None, keyboard=True))
        typed.send_text("stop\r")
        await asyncio.wait_for(stopping.wait(), _HANG_S)
        typed.send_text("quit\r")
        await asyncio.wait_for(closing.wait(), _HANG_S)
        session.cancel()
        await asyncio.wait({session}, timeout=_HANG_S)
    assert session.cancelled()
    assert folds.calls == 1, "the closing fold started after the press"
    _held_back_and_success(_the_run(runs))


async def test_a_press_while_a_pipe_closes_its_commands_keeps_the_closing_fold_from_starting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A pipe's commands are closed by `run_drive`'s `finally` itself, waiting here on a brake's
    stop still on its way, and a press there went past the rest of the close. It is a press
    during the close, as at the keyboard and the page."""
    stopping, closing = _a_brake_on_its_way(monkeypatch)
    folds = _Folds(monkeypatch, hold_on=0)
    registry = _arm_with_a_rest_pose(tmp_path)
    runs = tmp_path / "runs"
    args = DriveArgs(robot="arm-01", registry_dir=str(registry), llm="fake", runs_dir=str(runs))
    session = asyncio.ensure_future(
        run_drive(args, say=lambda _: None, stream=io.StringIO("stop\nquit\n"), keyboard=False)
    )
    await asyncio.wait_for(stopping.wait(), _HANG_S)
    await asyncio.wait_for(closing.wait(), _HANG_S)
    session.cancel()
    await asyncio.wait({session}, timeout=_HANG_S)
    assert session.cancelled()
    assert folds.calls == 1, "the closing fold started after the press"
    _held_back_and_success(_the_run(runs))


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


async def test_a_close_whose_stop_fails_logs_what_the_sessions_close_always_logged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """`RobotSession.close` went on past a stop that raised and said nothing, and drive's own
    close said `the stop failed` at INFO, a line a session nobody pressed in never had. Kept
    below INFO now, where a person reading the log never sees it unless they asked for all of
    it."""
    from roboglot_microduck import MicroduckAdapter

    async def gone(_adapter: Any) -> None:
        raise ConnectionError("the link is gone")

    monkeypatch.setattr(MicroduckAdapter, "stop", gone)
    # On drive's own logger: the CLI sets the level of the `roboglot` logger, and a test that ran
    # the CLI before this one on the same worker leaves it there, above DEBUG.
    caplog.set_level(logging.DEBUG, logger="roboglot.drive")
    args = DriveArgs(robot="microduck:mock", llm="fake", runs_dir=str(tmp_path))
    await run_drive(args, say=lambda _: None, stream=io.StringIO("quit\n"), keyboard=False)
    drive = [r for r in caplog.records if r.name == "roboglot.drive"]
    assert [r for r in drive if r.levelno >= logging.INFO] == [], caplog.text
    assert any("the link is gone" in r.getMessage() for r in drive), "the failure went unrecorded"


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


async def test_what_the_close_says_is_in_the_drive_record_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The robot's close leaves its own line (`close_note`): a stop that did not land on a
    base, torque left on an arm. A run writes it in its record, and drive's close gave it to
    the logger alone, a bare line on stderr, so the session's record and the page never had it.
    It is written in the record now, once, before the session's end, and the screen, which has
    the logger's line, is not told it twice."""
    from roboglot_microduck import MicroduckAdapter

    note = "at the close, the stop is not known to have reached the body"
    closed = MicroduckAdapter.close

    async def close(self: Any) -> None:
        await closed(self)
        self.close_note = note

    monkeypatch.setattr(MicroduckAdapter, "close", close)
    said: list[str] = []
    args = DriveArgs(robot="microduck:mock", llm="fake", runs_dir=str(tmp_path))
    run_dir = await run_drive(args, say=said.append, stream=io.StringIO("quit\n"), keyboard=False)
    assert not any(note in line for line in said), said
    events = Transcript.read(run_dir / "transcript.jsonl")
    notes = [e for e in events if e["kind"] == "note" and e.get("text") == note]
    assert len(notes) == 1, [e["kind"] for e in events]
    assert events.index(notes[0]) < next(i for i, e in enumerate(events) if e["kind"] == "run_end")
