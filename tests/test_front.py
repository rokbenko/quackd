"""The page's end of a run or a drive session, in-process: JSON lines out, ops in, questions put
by id, and a stop that sets the run's abort (`roboglot.front`).

The commands behind it, run as the page runs them, are in `test_front_run.py` and
`test_front_drive.py`."""

from __future__ import annotations

import asyncio
import base64
import contextlib
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

from roboglot import front as front_mod
from roboglot.agent.loop import KILL_SWITCH, PERSON_STOPS, STOP_BUTTON
from roboglot.front import (
    CHANNEL,
    PAGE_GONE,
    Channel,
    Forgiving,
    Front,
    Questions,
    QuestionWithdrawn,
    trimmed,
)
from roboglot.log import A_PERSON, A_PIPE, LogEvent, asked_on, who_answered
from roboglot.safety import VerbNotAllowed
from roboglot.transport.mock import MockTransport
from roboglot.verbs.registry import VerbRegistry
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


def test_a_number_json_has_no_word_for_is_sent_as_text_and_the_line_stays_json() -> None:
    """A page reads the lines with a strict parser, and `NaN` is not JSON: a record carrying
    one, a distance nothing was measured for, still arrives, with the number said in words."""
    out = io.StringIO()
    channel = Channel(out)
    channel.send("event", kind="verb_end", data={"range_m": float("nan"), "seen": [float("inf")]})
    channel.send("event", kind="verb_end", data={"range_m": 0.5})
    channel.close()

    def strict(word: str) -> Any:
        raise ValueError(f"{word} is not JSON")

    lines = [json.loads(line, parse_constant=strict) for line in out.getvalue().splitlines()]
    assert [line["data"] for line in lines] == [
        {"range_m": "nan", "seen": ["inf"]},
        {"range_m": 0.5},
    ]


def test_stderr_behind_the_page_drops_a_write_the_pipe_refuses_and_every_one_after() -> None:
    """On Windows a write to the pipe of a page that has gone raises OSError (EINVAL), which
    nothing caught: whatever was saying something, a stop among them, raised instead of
    finishing. Behind the page stderr drops it, and every write after it."""

    class Pipe(io.StringIO):
        refuse = False

        def write(self, text: str) -> int:
            if self.refuse:
                raise OSError(22, "Invalid argument")
            return super().write(text)

    pipe = Pipe()
    err = Forgiving(pipe)
    assert err.write("said\n") == 5
    pipe.refuse = True
    assert err.write("not said\n") == 9
    err.flush()
    assert err.dead
    pipe.refuse = False
    err.write("nor this\n")
    assert pipe.getvalue() == "said\n", "once dead, nothing more is written"
    with pytest.raises(TypeError):
        err.write(b"bytes")  # type: ignore[arg-type]
    assert not err.closed, "dead is its own word: the stream itself is open"
    pipe.close()
    assert err.closed


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


def test_a_line_in_longer_than_any_op_is_skipped_with_a_word_and_the_next_is_read() -> None:
    """The reader kept a line of any length, and every chunk copied and scanned all of it
    again, so one long line with no newline cost more than its length, a GIL held for every
    copy, and memory without end. A line past `OP_LINE_MAX` is dropped once it is that long,
    with a notice, and the reader picks up at the next newline. Every line a page or a client
    really sends fits, a chat line as long as the page lets one be typed included."""
    from roboglot.front import CHAT_LINE_MAX, OP_LINE_MAX

    widest = json.dumps({"op": "line", "text": "\U0001f986" * CHAT_LINE_MAX})
    assert len(widest.encode("utf-8")) <= OP_LINE_MAX, "a chat line the page allows is dropped"
    chunks = [b'{"op": "line", "text": "', b"x" * OP_LINE_MAX, b"x" * OP_LINE_MAX, b'"}\n']
    chunks += [widest.encode("utf-8") + b"\n", b'{"op": "stop"}\n']
    out = io.StringIO()
    channel = Channel(out)
    taken: list[dict[str, Any]] = []
    ended = threading.Event()
    reader = channel.start_reading(
        lambda op: taken.append(dict(op)), ended.set, take=lambda: chunks.pop(0) if chunks else b""
    )
    assert ended.wait(10), "the end of the input was never noticed"
    reader.join(10)
    channel.close()
    assert [op["op"] for op in taken] == ["line", "stop"], "the line after the long one was lost"
    assert taken[0]["text"] == "\U0001f986" * CHAT_LINE_MAX
    notices = [line["text"] for line in _lines(out) if line["ev"] == "notice"]
    assert len(notices) == 1 and str(OP_LINE_MAX) in notices[0], notices


def test_a_page_that_stops_reading_with_its_pipe_open_is_a_page_that_went_away(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every line was kept for a page that stopped reading with its pipe still open, so every
    record of the rest of the run queued behind a write that never finished, and `close` left
    the writer blocked. Once a write has waited `END_FLUSH_S`, the next line finds the page
    gone, as a broken pipe is: the channel says so once, as it does when the page goes away,
    which stops the run and takes back its questions, and nothing more is queued."""
    import roboglot.front as front_module

    monkeypatch.setattr(front_module, "END_FLUSH_S", 0.3)
    r, w = os.pipe()
    out = os.fdopen(w, "w", encoding="utf-8", newline="\n")
    channel = Channel(out)
    gone: list[str] = []
    channel.on_broken(lambda: gone.append("gone"))
    try:
        filler = "x" * 4000
        for n in range(200):  # far past what a pipe holds, so the writer is stuck in a write
            channel.send("event", n=n, text=filler)
        time.sleep(0.6)
        channel.send("ask", id=1, kind="confirm", question="run kick()?")
        assert channel.broken.is_set(), "a stalled page was waited on"
        _until(lambda: gone == ["gone"])  # said on a thread of its own, never the sender's
        queued = len(channel._events)
        for n in range(100):
            channel.send("event", n=n, text=filler)
        assert len(channel._events) <= queued, "lines went on queueing for a page gone"
        started = time.monotonic()
        channel.close()
        assert time.monotonic() - started < 5, "close waited on a page that was gone"
    finally:
        os.close(r)  # the stuck write fails, and the writer ends
        channel._writer.join(10)
        with contextlib.suppress(OSError):
            out.close()
    assert not channel._writer.is_alive(), "the writer was left blocked"
    assert gone == ["gone"], "said more than once"


@pytest.mark.parametrize("when", ["put", "timed out"])
def test_a_question_put_or_taken_back_on_a_stalled_page_does_not_hang_its_thread(
    when: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A send that finds the page stalled ran the page's end on the thread that sent, and that
    end takes the questions' lock, which `Questions` holds while it sends a question or takes
    one back: the thread waited on itself for ever. For a run's question that thread is the
    loop's, so the run, its heartbeat and its stop all stopped there. Here the very send that
    finds the stall is a question put, or one taken back when nobody answered it (the way a
    page that stopped reading always ends): the thread that asked must come back, with the
    question taken back, and the end must still reach the run."""
    monkeypatch.setattr(front_mod, "END_FLUSH_S", 0.2)
    stuck = threading.Event()

    class StoppedReading:
        def write(self, _text: str) -> int:
            stuck.set()
            threading.Event().wait()  # the page never reads this line
            return 0

        def flush(self) -> None:
            pass

    channel = Channel(StoppedReading())
    front = Front(channel, "run")
    channel.on_broken(front.on_end)  # as `Front.open` wires it
    channel.send("hello")
    assert stuck.wait(5.0)
    outcome: list[str] = []

    def ask() -> None:
        try:
            outcome.append(str(front.questions.ask("confirm", "run kick()?", timeout_s=0.4)))
        except QuestionWithdrawn as e:
            outcome.append(f"withdrawn: {e}")

    if when == "put":
        time.sleep(0.3)  # the write has waited past END_FLUSH_S before the question is put
    asking = threading.Thread(target=ask, daemon=True)
    asking.start()
    asking.join(5.0)
    assert not asking.is_alive(), "the thread that asked waits on its own lock for ever"
    assert outcome and outcome[0].startswith("withdrawn"), outcome
    _until(lambda: front.questions.gone, 5.0)


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
    withdrawn = {"ev": "asked", "id": 2, "withdrawn": "taken back before anybody answered it"}
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


def test_a_page_question_says_the_pilots_doubt_on_the_runs_terminal_with_no_escape_in_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A run driven from the page also says each question's detail on its own stderr, for the
    saved terminal and the terminal it was started from, and said it raw: the pilot's
    `ESC[1A ESC[2K` reached that terminal as the cursor moves they are. The console here writes
    no escape of its own, so this fails if one comes through again. The page is sent the
    pilot's words as they were, and shows them as text."""
    from rich.console import Console

    from roboglot import ui

    shown = io.StringIO()
    console = Console(file=shown, force_terminal=True, color_system=None, width=400)
    monkeypatch.setattr(ui, "err_console", console)
    doubt = "grip is fine\x1b[1A\x1b[2KRoboglot: all limits met"
    front, out = _front()
    answering = _answer_every_question(front, out)
    try:
        assert front.decide(doubt) is True
    finally:
        answering.set()
    said = shown.getvalue()
    assert "\x1b" not in said, f"the pilot's escapes reach stderr: {said!r}"
    assert "grip is fine\\x1b[1A\\x1b[2KRoboglot: all limits met" in said, said
    assert [x["detail"] for x in _lines(out) if x["ev"] == "ask"] == [doubt]


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


async def test_a_goal_whose_client_answers_with_yes_is_never_taken_for_a_person(
    registry: VerbRegistry, mock_transport: MockTransport, hello_glot: Any, tmp_path: Any
) -> None:
    """`roboglot onboard goal --yes` answers every question in the client, unseen, and the
    service marked its questions `--yes`, a mark nothing read: an open connection made each
    one read as put to a person. The record wrote a person's prompt row, the pilot was told a
    person read its doubt and said go, and the executor's rules for a dangerous verb and for a
    verb a person answers each time saw a person too. Marked as the service marks them, and
    answered yes by a client as `--yes` answers, nobody may be named: both rules refuse, the
    record names `--yes` with no prompt row, and the pilot is told nobody was asked."""
    from roboglot.agent.loop import RunConfig, run_glot
    from roboglot.agent.providers.base import ToolCall
    from roboglot.agent.providers.fake import FakeProvider
    from roboglot.agent.transcript import Transcript
    from roboglot.log import a_person_was_asked
    from roboglot.verbs.registry import NoParams, Verb, VerbContext, VerbResult

    front, out = _front("onboard goal")
    for asker in (front.confirm, front.decide, front.acknowledge, front.judge):
        asker.answers_as = "--yes"
    ran = _dangerous(registry)

    async def grip(_ctx: VerbContext, _p: NoParams) -> VerbResult:
        ran.append("grip")
        return VerbResult.success("gripped")

    registry.register(
        Verb("grip", "a person answers it", grip, safety_class="confirm", no_standing_answer=True)
    )
    answering = _answer_every_question(front, out)  # the client, saying yes to every ask
    try:
        for asker in (front.confirm, front.decide, front.acknowledge, front.judge):
            assert not a_person_was_asked(asker) and who_answered(asker) == "--yes"
        for verb, refusal in (("nuke", "only a person can clear it"), ("grip", "each time")):
            ex, seen = logged(registry, mock_transport, allow=verb)
            ex.confirm = front.confirm
            with pytest.raises(VerbNotAllowed, match=refusal):
                await ex.run_verb(verb)
            assert gates(seen) == [("confirm", "refused")]
        assert ran == []
        result = await run_glot(
            RunConfig(
                glot=hello_glot,
                provider=FakeProvider(
                    script=[
                        ToolCall(
                            name="assess_task",
                            arguments={"verdict": "uncertain", "reason": "the door may be shut"},
                        ),
                        ToolCall(name="declare_failure", arguments={"reason": "stop here"}),
                    ]
                ),
                transport=mock_transport,
                runs_dir=tmp_path,
                decide=front.decide,
                confirm=front.confirm,
            )
        )
    finally:
        answering.set()
    events = Transcript.read(result.run_dir / "transcript.jsonl")
    (assessed,) = [e for e in events if e["kind"] == "assess"]
    assert assessed["answered_by"] == "--yes", assessed
    assert not [e for e in events if e["kind"] == "prompt"], "a prompt row names a person"
    told = str(assessed.get("summary"))
    assert "without asking anybody" in told and "a person read that" not in told, told


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


async def test_the_line_stays_stopping_after_the_pages_stop() -> None:
    """What the cancelled call and the verb then wrote went over it: `the call to openrouter failed`
    on a page whose Stop had just been pressed."""
    front, _out = _front()
    agent = _Agent()
    front.bind(agent, asyncio.get_running_loop())
    await asyncio.to_thread(front.stop)
    front.status_from(LogEvent("llm", 0.0, {"provider": "openrouter", "error": "cancelled"}))
    front.status_from(LogEvent("verb_end", 0.0, {"name": "go_to", "ok": False}))
    assert front._status.words[1] == "stopping the robot"
    front.status_from(LogEvent("run_end", 0.0, {"outcome": "aborted"}))
    assert front._status.words[1] == "finishing"


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
    agent.executor.abort.set()  # a heartbeat, a team, a rule: already stopped
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


async def test_a_stop_sets_the_abort_even_where_saying_so_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The page's process dying closes its run's stderr, and on Windows the next write to it
    raises. The stop used to say what it was doing first, raise there, and never set the abort,
    so the robot went on: the abort is set first now, and a word that fails is not said."""
    from roboglot import ui

    def gone(*_args: Any, **_kwargs: Any) -> None:
        raise OSError(22, "Invalid argument")

    monkeypatch.setattr(ui.err_console, "print", gone)
    front, _out = _front()
    agent = _Agent()
    front.bind(agent, asyncio.get_running_loop())
    await asyncio.to_thread(front.on_end)
    await asyncio.wait_for(agent.executor.abort.wait(), 10)
    assert agent.stop_reason == PAGE_GONE


def test_a_run_already_stopped_puts_no_new_question() -> None:
    """A question asked once the page's Stop has come would hold the loop, and the heartbeat
    with it, for an answer nobody is going to give: it is taken back before it is put."""
    front, out = _front()
    front.stop()
    caught: dict[str, str] = {}

    def ask() -> None:
        try:
            front.confirm("kick", {"leg": "left"})
        except QuestionWithdrawn as e:
            caught["why"] = str(e)

    asking = threading.Thread(target=ask)
    asking.start()
    asking.join(10)
    waited = asking.is_alive()
    front.questions.withdraw_all("the test is over")  # a question wrongly put is let go
    asking.join(10)
    assert not waited, "the question was put, and waited for an answer"
    assert caught == {"why": "the run was stopped"}
    front.end(1)
    assert not [line for line in _lines(out) if line["ev"] == "ask"]


def test_a_stop_that_comes_while_a_question_is_being_put_is_never_outwaited(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The Stop can land after the run looked for one and before its question is registered,
    where taking back what is waiting finds nothing, and the run would hold its loop on the
    question with the Stop already pressed. The question is refused there instead."""
    front, out = _front()
    said = front._say

    def stop_while_saying(text: str, *, warn: bool = False) -> None:
        if not front._stops:
            front.stop()
        said(text, warn=warn)

    monkeypatch.setattr(front, "_say", stop_while_saying)
    caught: dict[str, str] = {}

    def ask() -> None:
        try:
            front.confirm("kick", {"leg": "left"})
        except QuestionWithdrawn as e:
            caught["why"] = str(e)

    asking = threading.Thread(target=ask)
    asking.start()
    asking.join(10)
    waited = asking.is_alive()
    front.questions.withdraw_all("the test is over")  # a question wrongly put is let go
    asking.join(10)
    assert not waited, "the question was put after the Stop, and waited for an answer"
    assert caught == {"why": "the run was stopped"}
    front.end(1)
    assert not [line for line in _lines(out) if line["ev"] == "ask"]


def test_the_quit_at_once_comes_before_its_word(monkeypatch: pytest.MonkeyPatch) -> None:
    """A word on stderr can wait on a page that has stopped reading it, and the quit, and every
    op behind it, would wait too."""
    order: list[str] = []
    monkeypatch.setattr(front_mod._thread, "interrupt_main", lambda: order.append("quit"))
    front, _out = _front()
    monkeypatch.setattr(front, "_say", lambda text, *, warn=False: order.append("said"))
    front.stop()
    order.clear()
    front.stop(now=True)
    assert order == ["quit", "said"]


def test_after_a_stop_no_question_is_put_even_past_the_fronts_own_check() -> None:
    """The questions are closed under the lock that puts them, so one asked of them directly,
    past the check `Front.ask` makes, is refused once a Stop has come, rather than put and
    waited on with the loop's thread, where no second press reaches."""
    front, out = _front()
    front.stop()
    caught: dict[str, str] = {}

    def ask() -> None:
        try:
            front.questions.ask("confirm", "run kick(leg=left)?")
        except QuestionWithdrawn as e:
            caught["why"] = str(e)

    asking = threading.Thread(target=ask)
    asking.start()
    asking.join(10)
    waited = asking.is_alive()
    front.questions.withdraw_all("the test is over")  # a question wrongly put is let go
    asking.join(10)
    assert not waited, "the question was put after the Stop, and waited for an answer"
    assert caught == {"why": "the run was stopped"}
    front.end(1)
    assert not [line for line in _lines(out) if line["ev"] == "ask"]


async def test_a_question_the_page_takes_back_by_going_away_never_names_the_stop_button() -> None:
    """The page's input ending takes back the question the run waits on, and the run stops on
    that withdrawal, perhaps before the stop is counted: it names the page's going away either
    way, and never a Stop nobody pressed."""
    front, _out = _front()
    agent = _Agent()
    front.bind(agent, asyncio.get_running_loop())
    front.questions.end()  # the page has gone, and its stop is not counted yet
    with pytest.raises(QuestionWithdrawn, match="the page went away"):
        front.confirm("kick", {"leg": "left"})
    assert agent.executor.abort.is_set()
    assert agent.stop_reason == PAGE_GONE


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
        "roboglot run takes stop and answer, not 'line'",
        "no question 9 is waiting for that answer",
    ]


def test_a_run_stopped_before_it_began_says_by_which_stop() -> None:
    """Read before the run is built: a run the page stopped that early never connects."""
    front, _out = _front()
    assert front.stopped_before_start() is None
    front.stop()
    front.stop()
    assert front.stopped_before_start() == STOP_BUTTON
    gone, _out = _front()
    gone.on_end()
    assert gone.stopped_before_start() == PAGE_GONE


def test_drive_is_halted_by_a_stop_a_quit_or_the_page_going_away_and_never_by_a_line() -> None:
    """Read just before drive's robot connects, which it then never does."""
    for op in ("stop", "quit"):
        front, _out = _front("drive")
        front.on_op({"op": op})
        front.on_op({"op": "quit" if op == "stop" else "stop"})
        assert front.halted() == op, "the first to come is the one named"
    front, _out = _front("drive")
    front.on_op({"op": "line", "text": "walk forward"})
    assert front.halted() is None, "a line is what the session is for"
    front.on_end()
    assert front.halted() == "end"
    run, _out = _front("run")
    run.on_op({"op": "stop"})
    assert run.halted() is None, "a run's stop is its own (`stopped_before_start`)"
