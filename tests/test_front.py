"""The page's end of a run or a drive session, in-process: JSON lines out, ops in, questions put
by id, and a stop that sets the run's abort (`quackd.front`).

The commands behind it, run as the page runs them, are in `test_front_run.py` and
`test_front_drive.py`."""

from __future__ import annotations

import asyncio
import base64
import io
import json
import os
import sys
import threading
import time
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any

import pytest
from PIL import Image

from quackd import front as front_mod
from quackd.agent.loop import KILL_SWITCH, PERSON_STOPS, STOP_BUTTON
from quackd.front import (
    CHANNEL,
    PAGE_GONE,
    Channel,
    Front,
    Questions,
    QuestionWithdrawn,
    trimmed,
)
from quackd.log import A_PERSON, A_PIPE, asked_on, who_answered
from quackd.safety import VerbNotAllowed
from quackd.transport.mock import MockTransport
from quackd.verbs.registry import VerbRegistry
from tests.test_executor import _dangerous, gates, logged


def _lines(out: io.StringIO) -> list[dict[str, Any]]:
    return [json.loads(line) for line in out.getvalue().splitlines() if line]


def _until(done: Callable[[], bool], within_s: float = 10.0) -> None:
    """Wait until `done()`: a bound on a wait, never a figure a test checks."""
    deadline = time.monotonic() + within_s
    while not done():
        if time.monotonic() > deadline:
            raise AssertionError("it never came")
        time.sleep(0.005)


def _asked(out: io.StringIO, n: int = 1) -> Callable[[], bool]:
    return lambda: len([line for line in _lines(out) if line["ev"] == "ask"]) >= n


def _written(out: io.StringIO, line: dict[str, Any]) -> Callable[[], bool]:
    """`line` has been written: the writer's thread writes what was sent a moment later."""
    return lambda: line in _lines(out)


def _front(command: str = "run") -> tuple[Front, io.StringIO]:
    out = io.StringIO()
    return Front(Channel(out), command), out


def _answer_every_question(front: Front, out: io.StringIO, yes: bool = True) -> threading.Event:
    """Answer each question the page is asked, as it is asked, until the event is set."""
    done = threading.Event()

    def answer() -> None:
        answered: set[int] = set()
        while not done.is_set():
            for line in _lines(out):
                if line["ev"] == "ask" and line["id"] not in answered:
                    answered.add(line["id"])
                    front.questions.answer(line["id"], yes)
            time.sleep(0.005)

    threading.Thread(target=answer, daemon=True).start()
    return done


class _Agent:
    """What the front binds to: a run's stop reason and its executor's abort flag."""

    def __init__(self) -> None:
        self.stop_reason: str | None = None
        self.executor = SimpleNamespace(abort=asyncio.Event())


# ── the lines out ──


def test_trimmed_keeps_what_a_pipe_should_carry_and_says_what_it_cut() -> None:
    long = "x" * (front_mod.TEXT_CAP + 10)
    assert trimmed(long) == f"{'x' * front_mod.TEXT_CAP}... (10 more characters)"
    assert trimmed(b"abc") == "<3 bytes>"
    many = trimmed(list(range(front_mod.ITEMS_CAP + 5)))
    assert len(many) == front_mod.ITEMS_CAP + 1 and many[-1] == "... (5 more)"
    nested: Any = "bottom"
    for _ in range(front_mod.DEPTH_CAP + 3):
        nested = {"in": nested}
    assert "..." in json.dumps(trimmed(nested))
    assert trimmed({"a": 1, 2: ("b",)}) == {"a": 1, "2": ["b"]}


def test_the_last_line_comes_after_every_line_before_it_and_nothing_after_it() -> None:
    out = io.StringIO()
    channel = Channel(out)
    for n in range(200):
        channel.send("event", n=n)
    channel.close({"ev": "end", "outcome": "success"})
    channel.send("event", n=999)
    lines = _lines(out)
    assert [line["n"] for line in lines[:-1]] == list(range(200))
    assert lines[-1] == {"ev": "end", "outcome": "success"}


def test_a_frame_is_shrunk_and_sent_as_a_jpeg_and_a_second_one_too_soon_is_dropped() -> None:
    out = io.StringIO()
    channel = Channel(out)
    channel.frame(Image.new("RGB", (1280, 960), "red"), "step 1")
    channel.frame(Image.new("RGB", (1280, 960), "blue"), "step 1 again")
    _until(lambda: any(line["ev"] == "frame" for line in _lines(out)))
    channel.close()
    frames = [line for line in _lines(out) if line["ev"] == "frame"]
    assert len(frames) == 1, "the second came within FRAME_EVERY_S of the first"
    (frame,) = frames
    assert (frame["width"], frame["height"], frame["caption"]) == (640, 480, "step 1")
    assert base64.b64decode(frame["jpeg"]).startswith(b"\xff\xd8"), "a JPEG"


def test_a_broken_page_is_noticed_once_and_nothing_more_is_written() -> None:
    class Gone(io.StringIO):
        def write(self, _text: str) -> int:
            raise BrokenPipeError

    broken: list[str] = []
    channel = Channel(Gone())
    channel.on_broken(lambda: broken.append("gone"))
    channel.send("event", n=1)
    channel.send("event", n=2)
    channel.close()
    assert channel.broken.is_set()
    assert broken == ["gone"]


# ── the ops in ──


def _read(data: bytes) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    out = io.StringIO()
    channel = Channel(out)
    taken: list[dict[str, Any]] = []
    ended = threading.Event()
    r, w = os.pipe()
    reader = channel.start_reading(lambda op: taken.append(dict(op)), ended.set, fd=r)
    os.write(w, data)
    os.close(w)
    assert ended.wait(10), "the end of the input was never noticed"
    reader.join(10)
    os.close(r)
    channel.close()
    return taken, _lines(out)


def test_every_op_is_taken_and_a_line_that_is_none_is_said_back() -> None:
    taken, lines = _read(
        b'{"op": "stop"}\nnot json\n{"no": "op"}\n\n{"op": "answer", "id": 1, "yes": true}'
    )
    assert taken == [{"op": "stop"}, {"op": "answer", "id": 1, "yes": True}], (
        "the last line counts without its newline"
    )
    notices = [line["text"] for line in lines if line["ev"] == "notice"]
    assert len(notices) == 2 and all(n.startswith("skipped a line") for n in notices)


@pytest.mark.skipif(sys.platform != "win32", reason="a pending read holds a pipe on Windows")
def test_the_reader_never_holds_a_read_open_on_a_windows_pipe() -> None:
    """A read left pending on a pipe makes a question about its handle wait behind it, and a DLL
    loading asks one of the standard handles: a run that imported numpy behind the page's reader
    sat in the import until the page next sent a line. The reader only reads what is already
    there, so the question answers at once while nothing has been sent."""
    import ctypes
    import msvcrt
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GetFileInformationByHandle.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
    kernel32.GetFileInformationByHandle.restype = wintypes.BOOL
    out = io.StringIO()
    channel = Channel(out)
    ended = threading.Event()
    r, w = os.pipe()
    channel.start_reading(lambda _op: None, ended.set, fd=r)
    time.sleep(0.3)  # the reader is looking at the pipe, with nothing sent
    answered = threading.Event()
    handle = wintypes.HANDLE(msvcrt.get_osfhandle(r))
    info = (ctypes.c_byte * 64)()  # BY_HANDLE_FILE_INFORMATION is 52 bytes
    looker = threading.Thread(
        target=lambda: (kernel32.GetFileInformationByHandle(handle, info), answered.set()),
        daemon=True,
    )
    looker.start()
    try:
        assert answered.wait(5), "a question about the pipe waited behind the reader"
    finally:
        os.close(w)
        assert ended.wait(10)
        os.close(r)
        channel.close()


# ── questions ──


def test_a_question_is_answered_by_its_id_once_and_by_nothing_else() -> None:
    out = io.StringIO()
    questions = Questions(Channel(out))
    got: dict[str, bool] = {}
    asking = threading.Thread(target=lambda: got.update(a=questions.ask("confirm", "run kick()?")))
    asking.start()
    _until(_asked(out))
    ask = next(line for line in _lines(out) if line["ev"] == "ask")
    assert ask == {"ev": "ask", "id": 1, "kind": "confirm", "question": "run kick()?"}
    assert not questions.answer(2, True), "no such question"
    assert not questions.answer(1, "yes"), "a yes must be a boolean"
    assert not questions.answer(True, True), "a boolean is not an id"
    assert questions.answer(1, True)
    assert not questions.answer(1, False), "a second tap answers nothing"
    asking.join(10)
    assert got == {"a": True}
    _until(_written(out, {"ev": "asked", "id": 1, "answer": True}))


def test_a_question_taken_back_raises_and_is_never_answered_no() -> None:
    out = io.StringIO()
    questions = Questions(Channel(out))
    caught: dict[str, str] = {}

    def ask() -> None:
        try:
            questions.ask("decide", "Go ahead anyway?", "the pilot is unsure")
        except QuestionWithdrawn as e:
            caught["why"] = str(e)

    asking = threading.Thread(target=ask)
    asking.start()
    _until(_asked(out))
    questions.withdraw_all("the run was stopped")
    asking.join(10)
    assert caught == {"why": "the run was stopped"}
    assert not questions.answer(1, True), "an answer after it was taken back answers nothing"
    _until(_written(out, {"ev": "asked", "id": 1, "withdrawn": "the run was stopped"}))
    assert len([line for line in _lines(out) if line["ev"] == "asked"]) == 1
    ask = next(line for line in _lines(out) if line["ev"] == "ask")
    assert ask["detail"] == "the pilot is unsure"


def test_a_question_nobody_answers_in_time_is_taken_back() -> None:
    questions = Questions(Channel(io.StringIO()))
    with pytest.raises(QuestionWithdrawn, match="nobody answered within"):
        questions.ask("confirm", "run kick()?", timeout_s=0.05)


def test_a_page_that_has_gone_is_asked_nothing() -> None:
    out = io.StringIO()
    questions = Questions(Channel(out))
    questions.gone = True
    with pytest.raises(QuestionWithdrawn, match="nobody is at the page"):
        questions.ask("confirm", "run kick()?")
    assert not [line for line in _lines(out) if line["ev"] == "ask"]


async def test_a_drive_question_waits_as_a_coroutine_and_a_cancel_takes_it_back() -> None:
    out = io.StringIO()
    questions = Questions(Channel(out))
    first = asyncio.ensure_future(questions.ask_async("drive", "did you mean kick?"))
    await asyncio.to_thread(_until, _asked(out))
    assert await asyncio.to_thread(questions.answer, 1, False), "answered from another thread"
    assert await asyncio.wait_for(first, 10) is False
    second = asyncio.ensure_future(questions.ask_async("drive", "should the pilot take this?"))
    await asyncio.to_thread(_until, _asked(out, 2))
    second.cancel()
    with pytest.raises(asyncio.CancelledError):
        await second
    withdrawn = {"ev": "asked", "id": 2, "withdrawn": "a newer line or the brake took its place"}
    await asyncio.to_thread(_until, _written(out, withdrawn))


# ── the page answers as a person ──


def test_the_page_answers_as_a_person_on_the_web_channel_until_it_has_gone() -> None:
    front, _out = _front()
    for asker in (front.confirm, front.decide, front.acknowledge, front.judge):
        assert who_answered(asker) == A_PERSON
        assert asked_on(asker) == {"channel": CHANNEL}
    front.on_end()
    assert who_answered(front.confirm) == A_PIPE, "nobody is there, and no record says somebody"


def test_each_kind_of_question_is_put_in_the_words_the_terminal_uses() -> None:
    front, out = _front()
    answering = _answer_every_question(front, out)
    try:
        assert front.decide("unsure of the payload") is True
        assert front.acknowledge("it cannot see a fall") is True
        assert front.judge("the last segment ran") is True
    finally:
        answering.set()
    asks = [(x["kind"], x["question"], x["detail"]) for x in _lines(out) if x["ev"] == "ask"]
    assert asks == [
        ("decide", "Go ahead anyway?", "unsure of the payload"),
        ("acknowledge", "Are you watching the robot right now?", "it cannot see a fall"),
        ("judge", "Did the arm do it?", "the last segment ran"),
    ]


async def test_a_dangerous_verb_is_cleared_by_a_yes_in_the_page_and_refused_once_it_has_gone(
    registry: VerbRegistry, mock_transport: MockTransport
) -> None:
    """ADR-0056: only a person clears a dangerous verb. The page is a person while it is there,
    and its answer is recorded on its channel; once its input has ended nobody is asked, and the
    verb is refused before its question is put."""
    ran = _dangerous(registry)
    front, out = _front()
    ex, seen = logged(registry, mock_transport, allow="nuke")
    ex.confirm = front.confirm
    answering = _answer_every_question(front, out)
    try:
        assert (await ex.run_verb("nuke")).ok
    finally:
        answering.set()
    assert ran == ["nuke"]
    assert gates(seen) == [("confirm", "allowed")]
    prompts = [e.data for e in seen if e.kind == "prompt"]
    assert prompts == [
        {"what": "confirm", "question": "run nuke()?", "answer": True, "channel": CHANNEL}
    ]

    front.on_end()
    ex2, seen2 = logged(registry, mock_transport, allow="nuke")
    ex2.confirm = front.confirm
    with pytest.raises(VerbNotAllowed, match="only a person can clear it"):
        await ex2.run_verb("nuke")
    assert ran == ["nuke"]
    assert gates(seen2) == [("confirm", "refused")]


# ── a run's stop ──


async def test_the_page_stop_sets_the_abort_and_names_the_stop_button() -> None:
    front, out = _front()
    agent = _Agent()
    front.bind(agent, asyncio.get_running_loop())
    await asyncio.to_thread(front.stop)
    await asyncio.wait_for(agent.executor.abort.wait(), 10)
    assert agent.stop_reason == STOP_BUTTON
    stopping = {"ev": "status", "text": "stopping the robot", "step": None}
    await asyncio.to_thread(_until, _written(out, stopping))


def test_the_stop_button_is_a_person_stop_and_a_page_gone_is_not() -> None:
    """A person's stop ends a fold the run began by itself; a page that went away is nobody
    pressing anything, so the fold goes on to its pose, as for a heartbeat that failed."""
    assert STOP_BUTTON in PERSON_STOPS and KILL_SWITCH in PERSON_STOPS
    assert PAGE_GONE not in PERSON_STOPS


async def test_a_stop_sent_before_the_run_began_stops_it_as_it_begins() -> None:
    front, _out = _front()
    front.stop()
    agent = _Agent()
    front.bind(agent, asyncio.get_running_loop())
    assert agent.executor.abort.is_set()
    assert agent.stop_reason == STOP_BUTTON


async def test_a_stop_after_the_run_ended_stops_nothing() -> None:
    front, _out = _front()
    agent = _Agent()
    front.bind(agent, asyncio.get_running_loop())
    front.unbind()
    front.stop()
    await asyncio.sleep(0.01)
    assert not agent.executor.abort.is_set() and agent.stop_reason is None


async def test_a_stop_leaves_the_reason_whatever_stopped_the_run_first_gave_it() -> None:
    front, _out = _front()
    agent = _Agent()
    agent.executor.abort.set()  # a heartbeat, a flock, a rule: already stopped
    front.bind(agent, asyncio.get_running_loop())
    front.stop()
    await asyncio.sleep(0.01)
    assert agent.stop_reason is None


async def test_the_page_going_away_stops_the_run_as_nobody_pressing() -> None:
    front, _out = _front()
    agent = _Agent()
    front.bind(agent, asyncio.get_running_loop())
    await asyncio.to_thread(front.on_end)
    await asyncio.wait_for(agent.executor.abort.wait(), 10)
    assert agent.stop_reason == PAGE_GONE


def test_only_a_second_stop_now_quits_at_once(monkeypatch: pytest.MonkeyPatch) -> None:
    quits: list[int] = []
    monkeypatch.setattr(front_mod._thread, "interrupt_main", lambda: quits.append(1))
    front, _out = _front()
    front.stop(now=True)
    assert quits == [], "the first stop is the orderly one, whatever it asks"
    front.stop()
    assert quits == [], "a repeat is the same stop"
    front.stop(now=True)
    assert quits == [1]


def test_a_stop_withdraws_the_question_the_run_is_waiting_on() -> None:
    front, out = _front()
    caught: dict[str, str] = {}

    def ask() -> None:
        try:
            front.confirm("kick", {"leg": "left"})
        except QuestionWithdrawn as e:
            caught["why"] = str(e)

    asking = threading.Thread(target=ask)
    asking.start()
    _until(_asked(out))
    front.stop()
    asking.join(10)
    assert caught == {"why": "the run was stopped"}


# ── the last line, and drive's ops ──


def test_the_end_line_names_a_refusal_an_error_or_how_the_run_ended() -> None:
    front, out = _front()
    front.refused("no robot answered", "check its address")
    front.end(1)
    assert _lines(out)[-1] == {
        "ev": "end",
        "outcome": "refused",
        "reason": "no robot answered",
        "hint": "check its address",
        "exit_code": 1,
    }

    front, out = _front()
    front.refused("a sentence")
    front.errored("TransportError: gone")
    front.end(1)
    assert _lines(out)[-1]["outcome"] == "error"

    front, out = _front()
    front.finished("success", "done", run_dir="runs/x", summary={"steps": 3})
    front.end(0)
    end = _lines(out)[-1]
    assert (end["outcome"], end["run_dir"], end["summary"], end["exit_code"]) == (
        "success",
        "runs/x",
        {"steps": 3},
        0,
    )

    front, out = _front()
    front.end(1, fallback="quit at once")
    assert _lines(out)[-1]["reason"] == "quit at once"


def test_drive_ops_sent_before_its_session_was_ready_wait_for_it() -> None:
    front, _out = _front("drive")
    front.on_op({"op": "line", "text": "quack"})
    front.on_op({"op": "stop"})
    taken: list[dict[str, Any]] = []
    front.attach_drive(lambda op: taken.append(dict(op)))
    assert taken == [{"op": "line", "text": "quack"}, {"op": "stop"}]
    front.on_op({"op": "line", "text": "kick"})
    front.on_end()
    assert taken[-2:] == [{"op": "line", "text": "kick"}, {"op": "end"}]


def test_a_run_takes_stop_and_answer_and_says_so_of_anything_else() -> None:
    front, out = _front()
    front.on_op({"op": "line", "text": "kick"})
    front.on_op({"op": "answer", "id": 9, "yes": True})
    _until(lambda: len([line for line in _lines(out) if line["ev"] == "notice"]) == 2)
    notices = [line["text"] for line in _lines(out) if line["ev"] == "notice"]
    assert notices == [
        "quackd run takes stop and answer, not 'line'",
        "no question 9 is waiting for that answer",
    ]
