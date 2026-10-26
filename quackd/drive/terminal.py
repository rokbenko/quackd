"""drive's front ends: the keyboard, through prompt_toolkit, and a pipe or a file.

The keyboard never waits on a command: a line goes to the session as Enter is pressed and the
prompt is back at once, so a person can type over a walk, and an empty Enter or Ctrl-C brakes
whatever is going on, a question pending included. A question the session puts, "did you mean
kick(leg=left)?", is answered by a `y` or an `n` typed next, and any other line is a newer
command, which drops it. A pipe reads one line, waits for that command to end, and reads the
next, so a captured session is the same every time, and a question put to it is answered no:
nobody is there to answer it. Neither installs the kill switch, whose `q` would end a session at
"quack".

prompt_toolkit is imported here and nowhere else in quackd, and only when the keyboard is used.
"""

from __future__ import annotations

import asyncio
import sys
import time
from collections.abc import Callable
from typing import Any, TextIO

from quackd.drive.lanes import is_brake, session_word, words
from quackd.drive.session import Command, DriveSession

QUIT_AGAIN_S = 1.5
"""A second Ctrl-C within this many seconds of the first ends the session, in order: the robot
stopped, folded where it folds, and let go of."""

ENDS = ("quit", "exit")

YES = ("y", "yes")
NO = ("n", "no")


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


async def drive_from_pipe(
    session: DriveSession, stream: TextIO | None = None, *, say: Callable[[str], None] = print
) -> None:
    """Every line of `stream`, stdin by default, one at a time, each waited for, as `a pipe`."""
    source = stream if stream is not None else sys.stdin
    while True:
        line = await asyncio.to_thread(source.readline)
        if not line:
            return
        text = line.rstrip("\r\n")
        cmd = session.submit(text, by="cli", channel="pipe")
        await asyncio.wait({cmd.ended})
        for out in told(cmd):
            say(out)
        if session_word(text) in ENDS:
            return


class Asker:
    """A yes-or-no question put to the person at the keyboard: `y` or `yes` typed next is yes,
    and `n` or `no` is no. Any other line is not an answer. An empty Enter or `stop` brakes as it
    always does, and a command drops the question as a newer line drops any line still being
    routed, so neither is taken for a no while something is still moving."""

    def __init__(self, say: Callable[[str], None]) -> None:
        self.say = say
        self.pending: asyncio.Future[bool] | None = None

    async def __call__(self, question: str) -> bool:
        self.say(f"{'ask':<8} {question} [y/N]")
        self.pending = asyncio.get_running_loop().create_future()
        try:
            return await self.pending
        finally:
            self.pending = None

    def answers(self, line: str) -> bool:
        """Whether `line` answered a question, which it then has."""
        pending = self.pending
        said = words(line)
        if pending is None or pending.done() or len(said) != 1 or said[0] not in YES + NO:
            return False
        pending.set_result(said[0] in YES)
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
