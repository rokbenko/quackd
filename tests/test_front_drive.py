"""`quackd drive` behind the page: its front end in-process (`drive_from_jsonl`), and the command
started as the page starts it (`quackd.front`)."""

from __future__ import annotations

import asyncio
import io
import json
from pathlib import Path
from typing import Any

import pytest

from quackd.drive.session import DriveSession
from quackd.drive.terminal import PageAsker, drive_from_jsonl
from quackd.front import CHANNEL, Channel, Front
from quackd.transport.mock import MockTransport
from tests.test_drive_session import _drive, _hold, _until
from tests.test_front_run import _Page, _transcript


def _lines(out: io.StringIO) -> list[dict[str, Any]]:
    return [json.loads(line) for line in out.getvalue().splitlines() if line]


async def _behind_the_page(
    tmp_path: Path,
) -> tuple[
    DriveSession,
    MockTransport,
    list[dict[str, Any]],
    Front,
    io.StringIO,
    asyncio.Queue[dict[str, Any]],
    asyncio.Future[None],
]:
    """A drive session on the mock duck with the page's front end reading `ops`, its asker the
    page's, as `run_drive` wires them."""
    drive, body, records = await _drive(tmp_path)
    out = io.StringIO()
    front = Front(Channel(out), "drive")
    said: list[str] = []
    drive.ask = PageAsker(front, said.append)
    ops: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
    task = asyncio.ensure_future(drive_from_jsonl(drive, front, ops, say=said.append))  # type: ignore[arg-type]
    return drive, body, records, front, out, ops, task


async def test_each_line_is_a_persons_on_the_web_channel_and_quit_ends_the_session(
    tmp_path: Path,
) -> None:
    drive, body, records, _front, out, ops, task = await _behind_the_page(tmp_path)
    # each line goes to the session the moment it arrives, so a quit sent with a walk still
    # being routed would drop the walk, as at the keyboard: the walk is under way first
    ops.put_nowait({"op": "line", "text": "forward"})
    await _until(lambda: bool(body.intents_of("move")))
    for text in ("report state", "quit", "stand"):
        ops.put_nowait({"op": "line", "text": text})
    await asyncio.wait_for(task, 30)
    commands = [r for r in records if r["kind"] == "command"]
    assert [c["text"] for c in commands] == ["forward", "report state", "quit"], "read past quit"
    assert {(c["by"], c["channel"]) for c in commands} == {("person", CHANNEL)}
    await _until(lambda: len([x for x in _lines(out) if x["ev"] == "command"]) == 3)
    told = {x["text"]: x["lane"] for x in _lines(out) if x["ev"] == "command"}
    assert (told["forward"], told["quit"]) == ("exact", "session")
    assert told["report state"] in ("exact", "dropped"), "a quit drops a line still routed"
    await drive.robot.close()


async def test_a_stop_in_the_page_brakes_a_walk_still_going(tmp_path: Path) -> None:
    drive, body, records, _front, _out, ops, task = await _behind_the_page(tmp_path)
    walking = _hold(body, "sleep")
    ops.put_nowait({"op": "line", "text": "forward"})
    await _until(lambda: bool(body.intents_of("move")))
    ops.put_nowait({"op": "stop"})
    await _until(lambda: any(r["kind"] == "brake" for r in records))
    ops.put_nowait({"op": "quit"})
    await asyncio.wait_for(task, 10)
    walking.set()
    brake = next(r for r in records if r["kind"] == "brake")
    assert (brake["why"], brake["channel"], brake["by"]) == ("Stop in the page", CHANNEL, "person")
    ended = {r["n"]: r["outcome"] for r in records if r["kind"] == "ended"}
    assert ended[1] == "preempted", "the walk went on past the Stop"
    await drive.robot.close()


async def test_the_page_going_away_brakes_and_ends_the_session_as_nobody_pressing(
    tmp_path: Path,
) -> None:
    drive, body, records, _front, _out, ops, task = await _behind_the_page(tmp_path)
    walking = _hold(body, "sleep")
    ops.put_nowait({"op": "line", "text": "forward"})
    await _until(lambda: bool(body.intents_of("move")))
    ops.put_nowait({"op": "end"})
    await asyncio.wait_for(task, 10)
    walking.set()
    brake = next(r for r in records if r["kind"] == "brake")
    assert (brake["why"], brake["by"]) == ("the page went away", "cli")
    ended = {r["n"]: r["outcome"] for r in records if r["kind"] == "ended"}
    assert ended[1] == "preempted"
    await drive.robot.close()


async def test_a_second_stop_ends_the_session_as_two_presses_do(tmp_path: Path) -> None:
    drive, _body, records, _front, _out, ops, task = await _behind_the_page(tmp_path)
    ops.put_nowait({"op": "stop"})
    ops.put_nowait({"op": "stop", "now": True})
    await asyncio.wait_for(task, 10)
    await asyncio.gather(*drive._brakes)
    assert [r["why"] for r in records if r["kind"] == "brake"] == ["Stop in the page"] * 2
    await drive.robot.close()


async def test_a_question_is_put_to_the_page_by_id_and_answered_there(tmp_path: Path) -> None:
    """A gated verb's question goes to the page, and only an answer naming it answers it; the
    gate then records a person on the page's channel."""
    drive, _body, records, front, out, ops, task = await _behind_the_page(tmp_path)
    drive.robot.executor.registry.get("kick").safety_class = "confirm"
    seen: list[Any] = []
    drive.robot.executor.event_log.add(seen.append)  # type: ignore[union-attr]
    ops.put_nowait({"op": "line", "text": "kick left"})
    await _until(lambda: any(x["ev"] == "ask" for x in _lines(out)))
    ask = next(x for x in _lines(out) if x["ev"] == "ask")
    assert ask["kind"] == "drive" and ask["question"].startswith("run kick(")
    assert not front.questions.answer(ask["id"] + 1, True), "another id answers nothing"
    assert front.questions.answer(ask["id"], True)
    await _until(lambda: any(r["kind"] == "ended" for r in records))
    ops.put_nowait({"op": "quit"})
    await asyncio.wait_for(task, 10)
    assert next(r for r in records if r["kind"] == "ended")["outcome"] == "ok"
    prompts = [e.data for e in seen if e.kind == "prompt"]
    assert [(p["answer"], p.get("channel")) for p in prompts] == [(True, CHANNEL)]
    await drive.robot.close()


def test_drive_from_the_page_takes_lines_before_it_is_ready_and_ends_on_quit(
    tmp_path: Path,
) -> None:
    """The command as the page starts it: a line sent before the robot connected waits for the
    session, a goal's records come tagged with its number, and quit is the last word."""
    runs = tmp_path / "runs"
    page = _Page(
        ["drive", "--robot", "microduck:sim2d", "--llm", "fake", "--runs-dir", str(runs)],
        tmp_path,
    )
    page.send(op="line", text="quack")
    page.wait_for(lambda line: line["ev"] == "command" and line["text"] == "quack")
    page.send(op="line", text="find the ball and kick it")
    page.wait_for(lambda line: line["ev"] == "command" and line["lane"] == "pilot")
    page.send(op="quit")
    end = page.wait_for(lambda line: line["ev"] == "end")
    assert page.finish() == 0
    assert page.not_json == []
    assert (end["outcome"], end["reason"]) == ("success", "the session ended")
    assert any(line.get("goal") == 1 for line in page.lines if line["ev"] == "event")
    start = next(r for r in _transcript(end["run_dir"]) if r["kind"] == "run_start")
    assert start["front"] == "jsonl"


@pytest.mark.parametrize(
    ("op", "outcome", "said", "code"),
    [
        ("stop", "aborted", "the page's Stop came before the robot connected", 1),
        ("quit", "success", "the session ended before the robot connected", 0),
    ],
    ids=["stop", "quit"],
)
def test_a_drive_the_page_halts_while_it_is_set_up_never_connects(
    tmp_path: Path, op: str, outcome: str, said: str, code: int
) -> None:
    """A Stop or a quit sent with the command: the session ends before its robot connects,
    and says which. A quit ends it as asked, as it ends a connected one; a Stop pressed while
    it was being set up is a session stopped."""
    runs = tmp_path / "runs"
    page = _Page(
        ["drive", "--robot", "microduck:sim2d", "--llm", "fake", "--runs-dir", str(runs)],
        tmp_path,
    )
    page.send(op=op)
    end = page.wait_for(lambda line: line["ev"] == "end")
    assert page.finish() == code
    assert (end["outcome"], end["reason"], end["exit_code"]) == (outcome, said, code)
    records = _transcript(end["run_dir"])
    assert not [r for r in records if r["kind"] == "run_start"], "it never connected"
    assert (records[-1]["kind"], records[-1]["outcome"]) == ("run_end", outcome)
    assert said in "".join(page.stderr)
