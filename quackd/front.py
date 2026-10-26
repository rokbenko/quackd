"""quackd's front end for a program rather than a person at a terminal: JSON lines in and out.

`quackd run` and `quackd drive` take a hidden `--front jsonl`, and that is how `quackd ui`
drives them, each from a child process of its own. The terminal stays the default front end and
this one leaves it as it was. It changes three things about a run and nothing about what the
run does: where its questions are answered, where its narration is read, and how it is stopped.

**Why a child process speaking JSON.** The page could import quackd and run the loop in a thread
of its own, and every way that goes wrong is a robot's: the kill switch reads the server's own
terminal, the consoles and the saved terminal are process globals, the record would carry the
page's argv, and a page reloading mid-verb would take the run with it. Driven as a child, a run
is the command a terminal runs, flag for flag and refusal for refusal, and the page holds nothing
but the pipe. A stop is a message on that pipe, so it reaches the run on Windows too, where no
signal a parent can send gets a child to stop and fold its arm first.

**The protocol**, one JSON object per line, `PROTOCOL` saying which:

    stdin   {"op": "stop"}                          stop the run, or brake the session
            {"op": "stop", "now": true}             after a stop, quit at once: a second Ctrl-C
            {"op": "answer", "id": 3, "yes": true}  answer question 3, and no other
            {"op": "line", "text": "kick"}          drive: one line, as if typed
            {"op": "quit"}                          drive: end the session
            end of input                            a stop, and the end of the session

    stdout  {"ev": "hello", "protocol": 1, ...}     first, once
            {"ev": "run_dir", "path": ...}          where the record is being written
            {"ev": "event", "kind": ..., "t": ..., "data": {...}}   a record, as it is written
            {"ev": "frame", "jpeg": ..., "caption": ...}            the camera, a few a second
            {"ev": "status", "text": ..., "step": ...}              the status line's words
            {"ev": "notice", "text": ...}           a warning a terminal prints before a run
            {"ev": "ask", "id": 3, "kind": ..., "question": ..., "detail": ...}
            {"ev": "asked", "id": 3, "answer": true}   or `"withdrawn": why`
            {"ev": "say", "text": ...}              drive: drive's own lines
            {"ev": "command", ...}                  drive: one line's lane and what came of it
            {"ev": "refused", "message": ..., "hint": ...}
            {"ev": "end", "outcome": ..., "reason": ..., "exit_code": 0, ...}   last, once

Everything a terminal would show still goes to stderr, which is where the page reads the run as
a terminal would have printed it, and stdout carries these lines and nothing else: a write meant
for stdout, a library's or a stray one, lands on stderr too, so it can never break a line.

**A question is put to a person and answered only by one.** Each carries an id, and an answer
names the question it answers, so a second tap on a phone cannot answer the next question, which
may be about a dangerous verb. A question taken back unanswered, by a stop, by the page going
away, or by nobody answering within `QUESTION_TIMEOUT_S`, raises `QuestionWithdrawn` rather than
returning no: the executor and the loop record a question that raised as unanswered, and a record
must never say a person said no when nobody did.
"""

from __future__ import annotations

import _thread
import asyncio
import base64
import contextlib
import functools
import io
import itertools
import json
import os
import sys
import threading
import time
from collections import deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, TYPE_CHECKING, Any

from quackd import __version__

if TYPE_CHECKING:
    from PIL import Image

    from quackd.log import LogEvent

PROTOCOL = 1
"""The version of the lines above. A page refuses a child whose `hello` names another."""

CHANNEL = "web"
"""The channel a page's lines and answers are recorded under, beside the keyboard and a pipe."""

QUESTION_TIMEOUT_S = 300.0
"""How long a run waits on a question nobody answers before taking it back. A terminal waits for
ever, and the person is in the room; a page may be a phone in a pocket, and a run that asks is
holding its loop, and its heartbeat, until the answer comes."""

FRAME_EVERY_S = 0.25
"""At most this often a frame goes to the page. The page shows the newest, so more is waste."""

FRAME_LONGEST_SIDE = 640
FRAME_QUALITY = 70

TEXT_CAP = 4000
"""The characters a string in an `event` keeps. The transcript keeps all of it; a page reading a
prompt or a model's whole reply follows the run directory, and a pipe is no place for megabytes."""

ITEMS_CAP = 100
DEPTH_CAP = 8

END_FLUSH_S = 5.0
"""How long the last line waits for the page to read the ones before it."""

PAGE_GONE = "the page went away"
"""The reason a run ends with when the page's input ended, which nobody pressed: unlike the Stop
button's, it is no person's stop, so a fold the run began by itself goes on to its pose."""

PIPE_POLL_S = 0.05
"""How often stdin is looked at on Windows, where it is never read until something is there."""


class QuestionWithdrawn(Exception):
    """A question the page was asked, taken back before anybody answered it."""


def trimmed(value: Any, depth: int = 0) -> Any:
    """`value` cut to a size a pipe can carry: long strings shortened and saying by how much,
    long lists cut, bytes named by their length, and anything nested past `DEPTH_CAP` elided."""
    if depth > DEPTH_CAP:
        return "..."
    if isinstance(value, str):
        if len(value) <= TEXT_CAP:
            return value
        return f"{value[:TEXT_CAP]}... ({len(value) - TEXT_CAP} more characters)"
    if isinstance(value, (bytes, bytearray, memoryview)):
        return f"<{len(value)} bytes>"
    if isinstance(value, Mapping):
        return {str(k): trimmed(v, depth + 1) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        items = list(value)
        kept = [trimmed(v, depth + 1) for v in items[:ITEMS_CAP]]
        if len(items) > ITEMS_CAP:
            kept.append(f"... ({len(items) - ITEMS_CAP} more)")
        return kept
    return value


def _frame_line(image: Image.Image, caption: str | None) -> dict[str, Any]:
    picture = image if image.mode == "RGB" else image.convert("RGB")
    picture.thumbnail((FRAME_LONGEST_SIDE, FRAME_LONGEST_SIDE))
    buffer = io.BytesIO()
    picture.save(buffer, format="JPEG", quality=FRAME_QUALITY)
    return {
        "ev": "frame",
        "jpeg": base64.b64encode(buffer.getvalue()).decode("ascii"),
        "width": picture.width,
        "height": picture.height,
        "caption": caption,
    }


class Channel:
    """stdout for the protocol's lines and stdin for the page's, each served by a thread.

    The writer drains a queue, so a page slow to read never stalls the run's loop, which shares
    its thread with the heartbeat and every verb steering the robot. Frames wait in one slot that
    a newer frame replaces, and are encoded on the writer's thread, never the loop's: a picture
    is the one line worth dropping, and every other line is kept. The reader takes stdin with
    `os.read` on a thread of its own rather than through `sys.stdin`, so nothing else in the
    process ever reads the page's lines, and a blocked read can neither hold up the loop at its
    end nor hold stdin's lock while the interpreter shuts down."""

    def __init__(self, out: IO[str]) -> None:
        self._out = out
        self._cv = threading.Condition()
        self._events: deque[dict[str, Any]] = deque()
        self._frame: tuple[Image.Image, str | None] | None = None
        self._last_frame_at = float("-inf")
        self._closing = False
        self._on_broken: Callable[[], None] | None = None
        self.broken = threading.Event()
        """A write failed: the page is gone, and nothing more will be read."""
        self._writer = threading.Thread(target=self._pump, name="quackd-front-out", daemon=True)
        self._writer.start()

    @classmethod
    def open(cls) -> Channel:
        """Keep stdout for the protocol from here on, and send every other write to stderr.

        fd 1 is duplicated for the lines, then pointed at stderr, and `sys.stdout` is pointed at
        `sys.stderr` rather than rewrapped, which keeps the `errors="replace"` that `ui` gave it.
        quackd's consoles look the stream up at every print, so they follow without a rebuild.
        Only ever called in the process the page started, never under a test runner, where it
        would take the runner's own stdout away for good."""
        sys.stdout.flush()
        out = os.fdopen(os.dup(1), "w", encoding="utf-8", newline="\n", buffering=1)
        os.dup2(2, 1)
        sys.stdout = sys.stderr
        return cls(out)

    def on_broken(self, callback: Callable[[], None]) -> None:
        self._on_broken = callback

    def send(self, ev: str, **data: Any) -> None:
        with self._cv:
            if self._closing:
                return
            self._events.append({"ev": ev, **data})
            self._cv.notify()

    def frame(self, image: Image.Image, caption: str | None) -> None:
        """Offer the newest frame. Copied here, because the loop may draw over its own, and
        encoded on the writer's thread."""
        now = time.monotonic()
        with self._cv:
            if self._closing or now - self._last_frame_at < FRAME_EVERY_S:
                return
            self._last_frame_at = now
        copy = image.copy()
        with self._cv:
            if self._closing:
                return
            self._frame = (copy, caption)
            self._cv.notify()

    def close(self, last: dict[str, Any] | None = None, *, timeout_s: float = END_FLUSH_S) -> None:
        """Send `last` after every line still queued, drop any frame still waiting, and wait
        for the writer, so the last line is out before the process exits. Once only."""
        with self._cv:
            if self._closing:
                return
            if last is not None:
                self._events.append(last)
            self._frame = None
            self._closing = True
            self._cv.notify()
        self._writer.join(timeout_s)

    def _pump(self) -> None:
        while True:
            with self._cv:
                while not self._events and self._frame is None and not self._closing:
                    self._cv.wait()
                frame = None
                if self._events:
                    line: dict[str, Any] | None = self._events.popleft()
                elif self._frame is not None:
                    line, frame, self._frame = None, self._frame, None
                else:
                    return
            if frame is not None:
                try:
                    line = _frame_line(*frame)
                except Exception:  # a frame that will not encode is a frame not shown
                    continue
            if line is not None:
                self._write(line)

    def _write(self, line: dict[str, Any]) -> None:
        if self.broken.is_set():
            return
        try:
            self._out.write(json.dumps(line, ensure_ascii=True, default=str) + "\n")
            self._out.flush()
        except (OSError, ValueError):
            self.broken.set()
            callback = self._on_broken
            if callback is not None:
                with contextlib.suppress(Exception):
                    callback()

    def start_reading(
        self, on_op: Callable[[Mapping[str, Any]], None], on_end: Callable[[], None], fd: int = 0
    ) -> threading.Thread:
        """Read the page's lines from `fd` until it ends, each handed to `on_op`, then
        `on_end` once. A line that is not an op is said back as a notice and skipped."""

        take_chunk = _waiting_reader(fd) or (lambda: os.read(fd, 65536))

        def read() -> None:
            pending = b""
            while True:
                try:
                    chunk = take_chunk()
                except OSError:
                    chunk = b""
                if not chunk:
                    break
                pending += chunk
                *lines, pending = pending.split(b"\n")
                for raw in lines:
                    self._take(raw, on_op)
            if pending.strip():
                self._take(pending, on_op)
            with contextlib.suppress(Exception):
                on_end()

        reader = threading.Thread(target=read, name="quackd-front-in", daemon=True)
        reader.start()
        return reader

    def _take(self, raw: bytes, on_op: Callable[[Mapping[str, Any]], None]) -> None:
        text = raw.decode("utf-8", errors="replace").strip()
        if not text:
            return
        try:
            op = json.loads(text)
        except ValueError:
            op = None
        if not isinstance(op, dict) or not isinstance(op.get("op"), str):
            self.send("notice", text=f"skipped a line that is not an op: {text[:80]!r}")
            return
        try:
            on_op(op)
        except Exception as e:
            self.send("notice", text=f"could not act on {op.get('op')!r}: {type(e).__name__}: {e}")


def _waiting_reader(fd: int) -> Callable[[], bytes] | None:
    """On Windows, a reader for a pipe on `fd` that only ever reads what is already waiting.

    A read left pending on a pipe holds its handle, and Windows makes a question about that
    handle (`GetFileInformationByHandle`) wait behind the read; a DLL loading asks it of the
    standard handles, so a run that imported numpy, OpenCV or a robot's SDK after the page's
    reader had started sat in the import until the page next sent a line. So the pipe is looked
    at, and read only when `PeekNamedPipe` says bytes are there, which never blocks; a pipe whose
    other end has closed fails the look, which is the end of the input. None anywhere else, and
    for a stdin that is not a pipe, which are read as they always are."""
    if sys.platform != "win32":
        return None
    import ctypes
    import msvcrt
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GetFileType.argtypes = [wintypes.HANDLE]
    kernel32.GetFileType.restype = wintypes.DWORD
    kernel32.PeekNamedPipe.argtypes = [
        wintypes.HANDLE,
        wintypes.LPVOID,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        ctypes.POINTER(wintypes.DWORD),
        ctypes.POINTER(wintypes.DWORD),
    ]
    kernel32.PeekNamedPipe.restype = wintypes.BOOL
    try:
        handle = wintypes.HANDLE(msvcrt.get_osfhandle(fd))
    except OSError:
        return None
    file_type_pipe = 3
    if kernel32.GetFileType(handle) != file_type_pipe:
        return None
    waiting = wintypes.DWORD()

    def take() -> bytes:
        while True:
            if not kernel32.PeekNamedPipe(handle, None, 0, None, ctypes.byref(waiting), None):
                return b""
            if waiting.value:
                return os.read(fd, min(int(waiting.value), 65536))
            time.sleep(PIPE_POLL_S)

    return take


@dataclass(eq=False)
class _Question:
    id: int
    kind: str
    question: str
    answered: threading.Event = field(default_factory=threading.Event)
    future: asyncio.Future[None] | None = None
    loop: asyncio.AbstractEventLoop | None = None
    answer: bool | None = None
    withdrawn: str | None = None


def _resolve(future: asyncio.Future[None]) -> None:
    if not future.done():
        future.set_result(None)


class Questions:
    """The page's yes-or-no questions: put, answered by id, or taken back.

    `ask` waits on a thread, for the run's own answerers, which the loop and the executor call
    synchronously; `ask_async` waits as a coroutine, for drive's, which must never block the loop
    that also reads the page's lines."""

    def __init__(self, channel: Channel) -> None:
        self._channel = channel
        self._lock = threading.Lock()
        self._pending: dict[int, _Question] = {}
        self._ids = itertools.count(1)
        self.gone = False
        """The page's input has ended, so nobody is there to answer anything."""

    def someone_there(self) -> bool:
        return not self.gone

    def _put(
        self, kind: str, question: str, detail: str | None, loop: asyncio.AbstractEventLoop | None
    ) -> _Question:
        if self.gone:
            raise QuestionWithdrawn("nobody is at the page to answer it")
        asked = _Question(next(self._ids), kind, question)
        if loop is not None:
            asked.loop, asked.future = loop, loop.create_future()
        with self._lock:
            self._pending[asked.id] = asked
        extra = {"detail": detail} if detail else {}
        self._channel.send("ask", id=asked.id, kind=kind, question=question, **extra)
        return asked

    def ask(
        self,
        kind: str,
        question: str,
        detail: str | None = None,
        *,
        timeout_s: float = QUESTION_TIMEOUT_S,
    ) -> bool:
        asked = self._put(kind, question, detail, None)
        if not asked.answered.wait(timeout_s):
            self._withdraw(asked, f"nobody answered within {timeout_s / 60:g} minutes")
        return self._settle(asked)

    async def ask_async(self, kind: str, question: str, detail: str | None = None) -> bool:
        asked = self._put(kind, question, detail, asyncio.get_running_loop())
        assert asked.future is not None
        try:
            await asked.future
        except asyncio.CancelledError:
            self._withdraw(asked, "a newer line or the brake took its place")
            with self._lock:
                self._pending.pop(asked.id, None)
            raise
        return self._settle(asked)

    def answer(self, question_id: object, yes: object) -> bool:
        """Answer the question `question_id` names, if it is still waiting. False, and nothing
        changed, for an id that is unknown, already answered or taken back, and for a yes that
        is not a boolean: a stray or doubled tap answers nothing."""
        if isinstance(question_id, bool) or not isinstance(question_id, int):
            return False
        if not isinstance(yes, bool):
            return False
        with self._lock:
            asked = self._pending.get(question_id)
            if asked is None or asked.answer is not None or asked.withdrawn is not None:
                return False
            asked.answer = yes
        self._wake(asked)
        return True

    def withdraw_all(self, why: str) -> None:
        with self._lock:
            waiting = list(self._pending.values())
        for asked in waiting:
            self._withdraw(asked, why)

    def _withdraw(self, asked: _Question, why: str) -> None:
        with self._lock:
            if asked.answer is not None or asked.withdrawn is not None:
                return
            asked.withdrawn = why
        self._channel.send("asked", id=asked.id, withdrawn=why)
        self._wake(asked)

    def _wake(self, asked: _Question) -> None:
        asked.answered.set()
        if asked.future is not None and asked.loop is not None:
            with contextlib.suppress(RuntimeError):  # the loop has already closed
                asked.loop.call_soon_threadsafe(_resolve, asked.future)

    def _settle(self, asked: _Question) -> bool:
        with self._lock:
            self._pending.pop(asked.id, None)
        if asked.withdrawn is not None:
            raise QuestionWithdrawn(asked.withdrawn)
        answer = bool(asked.answer)
        self._channel.send("asked", id=asked.id, answer=answer)
        return answer


class Answerer:
    """One kind of question a run puts, answered in the page, and marked for the record.

    `asks_a_person` is asked at the moment of asking, as the terminal's are: true while the page
    is there, false once its input has ended, so a dangerous verb is refused rather than put to
    nobody. `channel` names where the answer came from beside that."""

    channel = CHANNEL

    def __init__(
        self,
        front: Front,
        kind: str,
        words: Callable[..., tuple[str, str | None]],
    ) -> None:
        self._front = front
        self._kind = kind
        self._words = words

    def __call__(self, *args: Any) -> bool:
        question, detail = self._words(*args)
        return self._front.ask(self._kind, question, detail)

    def asks_a_person(self) -> bool:
        return self._front.questions.someone_there()


def _confirm_words(name: str, params: dict[str, Any]) -> tuple[str, str | None]:
    # the executor quotes the question in its record in exactly these words
    from quackd.log import fmt_params

    return f"run {name}({fmt_params(params)})?", None


def quiet_broken_pipes() -> None:
    """A page that went away must not end the run in the middle of its teardown. Rich answers a
    broken pipe by raising `SystemExit` from inside `print`, which would end a fold half way and
    leave a record saying the run was interrupted; under the front a console that can no longer
    write goes quiet instead, and the run finishes as it would have."""
    from quackd import ui

    def quiet(console: Any) -> None:
        console.quiet = True
        with contextlib.suppress(Exception):
            os.dup2(os.open(os.devnull, os.O_WRONLY), console.file.fileno())

    for console in (ui.console, ui.err_console):
        console.on_broken_pipe = functools.partial(quiet, console)  # type: ignore[method-assign]


class Front:
    """The page's end of one command: its lines out, its ops in, its questions and its stop.

    `quackd run` binds it to the run's loop, and its stop sets that loop's abort as the kill
    switch's first press does; `quackd drive` hands it the session's own queue, and its ops
    become lines, brakes and the session's end."""

    def __init__(self, channel: Channel, command: str) -> None:
        from quackd import ui

        self.command = command
        self._channel = channel
        self.questions = Questions(channel)
        self._status = ui.RunStatus()
        self._said: tuple[str, str] = ("", "")
        self._lock = threading.Lock()
        self._agent: Any = None
        self._aio: asyncio.AbstractEventLoop | None = None
        self._stops = 0
        self._gone_stop = False
        self._finished = False
        self._drive: Callable[[Mapping[str, Any]], None] | None = None
        self._early: list[dict[str, Any]] = []
        """Drive's ops that arrived before its session took them (`attach_drive`)."""
        self.outcome: dict[str, Any] | None = None
        """What `end` reports, once known: a run's result, a refusal or an error."""
        self.confirm = Answerer(self, "confirm", _confirm_words)
        self.decide = Answerer(self, "decide", lambda why: ("Go ahead anyway?", why))
        self.acknowledge = Answerer(
            self, "acknowledge", lambda why: ("Are you watching the robot right now?", why)
        )
        self.judge = Answerer(self, "judge", lambda why: ("Did the arm do it?", why))

    @classmethod
    def open(cls, command: str) -> Front:
        """Take stdout for the protocol, say hello, and start reading the page."""
        channel = Channel.open()
        quiet_broken_pipes()
        front = cls(channel, command)
        channel.on_broken(front.on_end)
        channel.send(
            "hello", protocol=PROTOCOL, version=__version__, command=command, pid=os.getpid()
        )
        channel.start_reading(front.on_op, front.on_end)
        return front

    # ── lines out ──

    def send(self, ev: str, **data: Any) -> None:
        self._channel.send(ev, **data)

    def notice(self, text: str) -> None:
        self._channel.send("notice", text=text)

    def say(self, text: str) -> None:
        self._channel.send("say", text=text)

    def run_dir(self, path: Path | str) -> None:
        self._channel.send("run_dir", path=str(path))

    def view(self, event: LogEvent) -> None:
        """A log observer: each record to the page as it is written, and the status line's
        words whenever they change. It never raises: the page is a second reader of the record,
        and a reader that failed must not cost the run an event."""
        with contextlib.suppress(Exception):
            self._channel.send(
                "event", kind=event.kind, t=round(event.t, 3), data=trimmed(event.data)
            )
            self.status_from(event)

    def goal_view(self, number: int) -> Callable[[LogEvent], None]:
        """A log observer for drive's goal `number`: its records to the page as `view` sends a
        run's, each tagged with the goal it belongs to."""

        def view(event: LogEvent) -> None:
            with contextlib.suppress(Exception):
                self._channel.send(
                    "event",
                    kind=event.kind,
                    t=round(event.t, 3),
                    data=trimmed(event.data),
                    goal=number,
                )
                self.status_from(event)

        return view

    def record(self, kind: str, data: Mapping[str, Any], *, goal: int | None = None) -> None:
        """A record written beside the event log rather than through it (drive's own)."""
        with contextlib.suppress(Exception):
            extra = {"goal": goal} if goal is not None else {}
            self._channel.send("event", kind=kind, data=trimmed(dict(data)), **extra)

    def status_from(self, event: LogEvent) -> None:
        self._status.sink(event)
        said = self._status.words
        if said != self._said:
            self._said = said
            step, text = said
            self._channel.send("status", text=text, step=step or None)

    def frame(self, image: Image.Image, caption: str | None) -> None:
        with contextlib.suppress(Exception):
            self._channel.frame(image, caption)

    def refused(self, message: str, hint: str | None = None) -> None:
        self._channel.send("refused", message=message, hint=hint)
        if self.outcome is None:
            self.outcome = {"outcome": "refused", "reason": message, "hint": hint}

    def errored(self, reason: str) -> None:
        if self.outcome is None or self.outcome.get("outcome") == "refused":
            self.outcome = {"outcome": "error", "reason": reason}

    def finished(
        self,
        outcome: str,
        reason: str,
        *,
        run_dir: Path | str | None = None,
        summary: Mapping[str, Any] | None = None,
    ) -> None:
        self.outcome = {
            "outcome": outcome,
            "reason": reason,
            "run_dir": None if run_dir is None else str(run_dir),
            "summary": trimmed(dict(summary or {})),
        }

    def end(self, exit_code: int, *, fallback: str | None = None) -> None:
        """The last line: the outcome, its reason and the exit code, then the channel closes.

        `fallback` is the reason where nothing better was learnt: a crash, a second Ctrl-C."""
        out = dict(self.outcome or {})
        if not out:
            out = {
                "outcome": "success" if exit_code == 0 else "error",
                "reason": fallback or ("" if exit_code == 0 else f"exit code {exit_code}"),
            }
        self._channel.close({"ev": "end", **out, "exit_code": exit_code})

    # ── questions ──

    def ask(self, kind: str, question: str, detail: str | None = None) -> bool:
        """One of the run's questions, waited for on the loop's thread, as the terminal's are.

        Said on stderr and kept in the saved terminal, as a terminal's question and its answer
        are. A question taken back because the run was stopped sets the stop before the loop
        runs another line, so nothing moves between the two."""
        from rich.text import Text

        from quackd import ui

        if detail:
            ui.err_console.print(Text(detail, style=ui.STYLES["warn"]))
        ui.err_console.print(Text(f"{question} [answered in the page]"), markup=False)
        try:
            answer = self.questions.ask(kind, question, detail)
        except QuestionWithdrawn as e:
            ui.note(f"{question} [page]: withdrawn ({e})")
            if self._stops or self.questions.gone:
                self._stop_on_loop()
            raise
        ui.note(f"{question} [page]: {'y' if answer else 'n'}")
        return answer

    # ── ops in ──

    def on_op(self, op: Mapping[str, Any]) -> None:
        name = op.get("op")
        if name == "answer":
            if not self.questions.answer(op.get("id"), op.get("yes")):
                self.notice(f"no question {op.get('id')!r} is waiting for that answer")
            return
        if self._for_drive(op):
            return
        if name == "stop":
            self.stop(now=op.get("now") is True)
            return
        self.notice(f"quackd {self.command} takes stop and answer, not {name!r}")

    def on_end(self) -> None:
        """The page's input ended, or its pipe broke: nobody is there any more. Every question
        is taken back, and the run is stopped, or the session ended, as the stop and the quit
        a person there would have sent."""
        with self._lock:
            if self.questions.gone:
                return
            self.questions.gone = True
        self.questions.withdraw_all("the page went away")
        if not self._for_drive({"op": "end"}):
            self.stop(gone=True)

    def _for_drive(self, op: Mapping[str, Any]) -> bool:
        """Hand `op` to drive's session, or keep it until the session takes them, and say so.
        False for a run, whose ops this front acts on itself."""
        with self._lock:
            drive = self._drive
            if drive is None:
                if self.command != "drive":
                    return False
                # drive's session is still being set up: a line or a Stop sent now waits for it
                self._early.append(dict(op))
                return True
        drive(op)
        return True

    def attach_drive(self, take: Callable[[Mapping[str, Any]], None]) -> None:
        """Hand every op but an answer to drive's session, which takes them on its own loop,
        those that arrived while it was being set up first."""
        with self._lock:
            self._drive = take
            early, self._early = self._early, []
        for op in early:
            take(op)

    # ── a run's stop ──

    def bind(self, agent: Any, aio: asyncio.AbstractEventLoop) -> None:
        """The run's loop, on the loop's thread, once it exists. A stop the page sent before
        there was a loop to stop is applied now."""
        with self._lock:
            self._agent, self._aio = agent, aio
            stopped = self._stops > 0
        if stopped:
            self._stop_on_loop()

    def unbind(self) -> None:
        """The run has ended: a stop from now on stops nothing, and is not said to."""
        with self._lock:
            self._finished = True

    def stop(self, *, now: bool = False, gone: bool = False) -> None:
        """The page's Stop. The first is the kill switch's first press: the abort is set, which
        ends the verb that is moving and sends a stop, and the run's teardown runs as for any
        run a person stopped. A repeat is the same stop. `now`, after a stop, is the second
        press: the run quits at once, as a second Ctrl-C does.

        `gone` is the stop the page's input ending makes, which nobody pressed: the run ends as
        `PAGE_GONE` says, and a fold the run began by itself goes on to its pose, as it does when
        a heartbeat fails, since only a person's stop leaves an arm where it is."""
        from rich.text import Text

        from quackd import ui

        with self._lock:
            if self._finished:
                return
            self._stops += 1
            first = self._stops == 1
            if first:
                self._gone_stop = gone
            aio = self._aio
        if now and not first:
            ui.err_console.print(Text("stop: quitting at once", style=ui.STYLES["warn"]))
            _thread.interrupt_main()
            return
        if not first:
            return
        said = "the page went away" if gone else "the page's Stop"
        ui.err_console.print(
            Text(
                f"stop: {said}, cancelling the verb and stopping the robot", style=ui.STYLES["warn"]
            )
        )
        self._channel.send("status", text="stopping the robot", step=None)
        if aio is not None:
            with contextlib.suppress(RuntimeError):  # the loop has already closed
                aio.call_soon_threadsafe(self._stop_on_loop)
        self.questions.withdraw_all("the page went away" if gone else "the run was stopped")

    def _stop_on_loop(self) -> None:
        """On the loop's thread: name the stop, unless something else already stopped the run,
        and set the abort."""
        from quackd.agent.loop import STOP_BUTTON

        agent = self._agent
        if agent is None or self._finished:
            return
        if not agent.executor.abort.is_set() and not agent.stop_reason:
            agent.stop_reason = PAGE_GONE if self._gone_stop else STOP_BUTTON
        agent.executor.abort.set()
