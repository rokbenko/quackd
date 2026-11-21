"""drive's front ends: the keyboard, through prompt_toolkit, a pipe or a file, and the page.

The keyboard never waits on a command: a line goes to the session as Enter is pressed and the
prompt is back at once, so a person can type over a walk, and an empty Enter or Ctrl-C brakes
whatever is going on, a question pending included. A question the session puts, "did you mean
kick(leg=left)?", is answered by a `y` or an `n` typed next, and any other line is a newer
command, which drops it. A pipe reads one line, waits for that command to end, and reads the
next, so a captured session is the same every time, and a question put to it is answered no:
nobody is there to answer it. Neither installs the kill switch, whose `q` would end a session at
"quack". The page (`roboglot ui`, through `roboglot.front`) takes lines as the keyboard does, each
the moment it arrives, and puts a question to the person at the page, by id, to answer there.

prompt_toolkit is imported here and nowhere else in Roboglot, and only when the keyboard is used.
"""

from __future__ import annotations

import asyncio
import contextlib
import sys
import threading
import time
from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING, Any, TextIO

from roboglot.drive.lanes import answer_word, is_brake, session_word
from roboglot.drive.session import Command, DriveSession

if TYPE_CHECKING:
    from roboglot.front import Front

QUIT_AGAIN_S = 1.5
"""A second Ctrl-C within this many seconds of the first ends the session, in order: the robot
stopped, folded where it folds, and let go of."""

ENDS = ("quit", "exit")


def told(cmd: Command) -> list[str]:
    """What a front end prints of one command once it has ended, in drive's own words."""
    if not cmd.routed.done() or not cmd.ended.done():
        return []
    routed, ended = cmd.routed.result(), cmd.ended.result()
    lane = routed.lane
    what = routed.label or routed.note or cmd.text
    lines = [f"{lane:<8} {what} ({routed.latency_ms:g} ms)"]
    if ended.summary and lane != "brake":
        lines.append(f"{'result':<8} {ended.outcome}: {ended.summary}")
    return lines


async def _next_line(source: TextIO) -> str:
    """The next line of `source`, read on a daemon thread of its own, and only once the pipe asks
    for it, as each line always was.

    Read in asyncio's default executor, a line nobody sent held the process after a Ctrl-C had
    closed the session and the robot in order: `asyncio.run` joins that executor as it closes, on
    Python 3.11 with no bound, and the read waits for a line or the end of input, which from a
    producer that keeps its end open never comes. A daemon thread is left behind at the exit."""
    loop = asyncio.get_running_loop()
    read: asyncio.Future[str] = loop.create_future()

    def got(line: str) -> None:
        if not read.done():  # the session stopped waiting for it: a press, among the ways
            read.set_result(line)

    def failed(error: Exception) -> None:
        if not read.done():
            read.set_exception(error)

    def reading() -> None:
        try:
            line = source.readline()
        except Exception as e:  # the session ends on it, as it did when the read raised there
            with contextlib.suppress(RuntimeError):  # the loop has closed: the session is over
                loop.call_soon_threadsafe(failed, e)
            return
        with contextlib.suppress(RuntimeError):  # the loop has closed: the session is over
            loop.call_soon_threadsafe(got, line)

    threading.Thread(target=reading, name="roboglot-drive-pipe", daemon=True).start()
    return await read


async def drive_from_pipe(
    session: DriveSession, stream: TextIO | None = None, *, say: Callable[[str], None] = print
) -> None:
    """Every line of `stream`, stdin by default, one at a time, each waited for, as `a pipe`."""
    source = stream if stream is not None else sys.stdin
    while True:
        line = await _next_line(source)
        if not line:
            return
        text = line.rstrip("\r\n")
        cmd = session.submit(text, by="cli", channel="pipe")
        await asyncio.wait({cmd.ended})
        for out in told(cmd):
            say(out)
        if session_word(text) in ENDS:
            return


def _ask_line(label: str | None, detail: str | None, question: str) -> str:
    """The words of an `ask` line: whose question it is, the detail, the question.

    The detail is a goal's doubt in the pilot's own words, so it goes through `printable`:
    printed raw, an escape sequence in it could erase lines of the session above the question
    and write others in Roboglot's voice."""
    from roboglot.ui import printable

    said = printable(detail) if detail else ""
    return " ".join(part for part in (f"{label}:" if label else "", said, question) if part)


class Asker:
    """A yes-or-no question put to the person at the keyboard: `y` or `yes` typed next is yes,
    and `n` or `no` is no. Any other line is not an answer. An empty Enter or `stop` brakes as it
    always does, and a command drops the question as a newer line drops any line still being
    routed, so neither is taken for a no while something is still moving.

    One question is shown at a time, and a `y` answers the one on the screen, the last `ask` line
    printed. A question about a line the person has just typed, a gated call's or the fast lane's,
    goes in front and is shown at once, since that is the question they are about to answer. A
    goal's question waits behind whatever is showing (`behind`), since a goal asks when it gets
    there, and one that took the screen just before a keystroke would take the answer meant for
    another. When the question on the screen is answered or taken back, the next is shown again.

    Marked as a person, for the record of a goal's questions put through it: the keyboard opens
    only where a person is at the terminal (`roboglot.cli._can_prompt`)."""

    asks_a_person = True

    def __init__(self, say: Callable[[str], None]) -> None:
        self.say = say
        self.waiting: list[tuple[str, asyncio.Future[bool]]] = []
        """Every question not yet answered or taken back: the first is the one shown."""

    @property
    def pending(self) -> asyncio.Future[bool] | None:
        """The question on the screen, or None."""
        return self.waiting[0][1] if self.waiting else None

    async def __call__(
        self,
        question: str,
        *,
        detail: str | None = None,
        kind: str = "drive",
        behind: bool = False,
        label: str | None = None,
    ) -> bool:
        shown = _ask_line(label, detail, question)
        future: asyncio.Future[bool] = asyncio.get_running_loop().create_future()
        if behind:
            self.waiting.append((shown, future))
        else:
            self.waiting.insert(0, (shown, future))
        if self.waiting[0][1] is future:
            self._show()
        try:
            return await future
        finally:
            was_shown = bool(self.waiting) and self.waiting[0][1] is future
            self.waiting = [(q, f) for q, f in self.waiting if f is not future]
            if was_shown and self.waiting:
                self._show()

    def _show(self) -> None:
        self.say(f"{'ask':<8} {self.waiting[0][0]} [y/N]")

    def answers(self, line: str) -> bool:
        """Whether `line` answered the question on the screen, which it then has."""
        pending = self.pending
        said = answer_word(line)
        if pending is None or pending.done() or said is None:
            return False
        pending.set_result(said)
        return True


async def drive_from_keyboard(
    session: DriveSession,
    asker: Asker,
    *,
    say: Callable[[str], None] = print,
    input: Any = None,
    output: Any = None,
    patch: bool = True,
    clock: Callable[[], float] = time.monotonic,
) -> None:
    """Lines typed at the keyboard until `quit`, `exit`, the end of input or a second Ctrl-C."""
    from contextlib import nullcontext

    from prompt_toolkit import PromptSession
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.patch_stdout import patch_stdout

    bindings = KeyBindings()
    pressed: list[float] = []

    @bindings.add("c-c")
    def _brake(event: Any) -> None:
        now = clock()
        if pressed and now - pressed[-1] <= QUIT_AGAIN_S:
            event.app.exit(exception=EOFError())
            return
        pressed.append(now)
        session.brake("Ctrl-C")
        say(f"{'brake':<8} Ctrl-C; press it again within {QUIT_AGAIN_S:g} s to end the session")

    prompt: PromptSession[str] = PromptSession(
        key_bindings=bindings,
        input=input,
        output=output,
        bottom_toolbar=lambda: f" {session.doing()} ",
    )
    tasks: set[asyncio.Task[None]] = set()

    async def tell(cmd: Command) -> None:
        await asyncio.wait({cmd.ended})
        for out in told(cmd):
            say(out)

    with patch_stdout(raw=True) if patch else nullcontext():
        while True:
            try:
                line = await prompt.prompt_async("drive> ")
            except EOFError:
                break
            if not is_brake(line) and asker.answers(line):
                continue
            cmd = session.submit(line, by="person", channel="keyboard")
            task = asyncio.ensure_future(tell(cmd))
            tasks.add(task)
            task.add_done_callback(tasks.discard)
            if session_word(line) in ENDS:
                break
    # ended rather than waited for: a walk still going when the person quit is stopped
    await session.close()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


class PageAsker:
    """A yes-or-no question put to the person at the page, who answers it there, by its id.

    Never answered by a line: the page answers with an `answer` op naming the question, so a line
    typed into the chat is always a command, and a brake or a newer line drops the question as
    it drops one at the keyboard. It waits as long as the keyboard's does: nothing blocks while it
    waits, and a session nobody watches is ended by the page. Each question has buttons of its
    own, so none waits behind another here, and a goal's is named after its goal, as at the
    keyboard."""

    def __init__(self, front: Front, say: Callable[[str], None]) -> None:
        from roboglot.front import CHANNEL

        self.front = front
        self.say = say
        self.channel = CHANNEL
        """Where a goal's questions put through this were answered, for its record."""

    async def __call__(
        self,
        question: str,
        *,
        detail: str | None = None,
        kind: str = "drive",
        behind: bool = False,
        label: str | None = None,
    ) -> bool:
        asked = f"{label}: {question}" if label else question
        shown = _ask_line(label, detail, question)
        self.say(f"{'ask':<8} {shown} [answered in the page]")
        return await self.front.questions.ask_async(kind, asked, detail)

    def asks_a_person(self) -> bool:
        """Whether the page is there to answer, asked as each question is put: false once its
        input has ended, as the run's own questions in the page are marked."""
        return self.front.questions.someone_there()


async def drive_from_jsonl(
    session: DriveSession,
    front: Front,
    ops: asyncio.Queue[Mapping[str, Any]],
    *,
    say: Callable[[str], None] = print,
) -> None:
    """The page's ops until it quits, its input ends, or a second Stop ends the session.

    `ops` is filled from the page's reader thread from the moment the session began, so a line
    sent while the robot was still connecting is waiting here, not lost. A line goes to the
    session at once, a person's on the channel the page's answers are recorded under, and a Stop
    brakes; `quit`, the end of the page's input and a second Stop end the session in order."""
    from roboglot.front import CHANNEL

    tasks: set[asyncio.Task[None]] = set()

    async def tell(cmd: Command) -> None:
        await asyncio.wait({cmd.ended})
        if cmd.routed.done() and cmd.ended.done():
            routed, ended = cmd.routed.result(), cmd.ended.result()
            front.send(
                "command",
                n=cmd.n,
                text=cmd.text,
                lane=routed.lane,
                label=routed.label or None,
                note=routed.note or None,
                latency_ms=routed.latency_ms,
                outcome=ended.outcome,
                summary=ended.summary,
            )
        for out in told(cmd):
            say(out)

    while True:
        op = await ops.get()
        name = op.get("op")
        if name == "line":
            line = str(op.get("text") or "")
            cmd = session.submit(line, by="person", channel=CHANNEL)
            task = asyncio.ensure_future(tell(cmd))
            tasks.add(task)
            task.add_done_callback(tasks.discard)
            if session_word(line) in ENDS:
                break
        elif name == "stop":
            session.brake("Stop in the page", channel=CHANNEL)
            if op.get("now") is True:
                # the second Stop: the session ends, in order, as two Ctrl-C end it
                break
        elif name == "quit":
            break
        elif name == "end":
            # nobody pressed anything: the page's input ended, as a pipe's does
            session.brake("the page went away", channel=CHANNEL, by="cli")
            break
        else:
            front.notice(f"roboglot drive takes line, stop, quit and answer, not {name!r}")
    # ended rather than waited for: a walk still going when the page quit is stopped
    await session.close()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
