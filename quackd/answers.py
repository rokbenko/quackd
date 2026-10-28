"""One way to wait for a person's answer, however it is given.

A run asks four questions: the confirm gate's before a gated verb, the pilot's doubt about the
task (`decide`), whether the arm did it (`judge`, for a pilot that cannot tell) and whether anybody
is watching a body that cannot see a fall (`acknowledge`). Whoever answers is handed in. A
terminal's prompt and every standing answer reply at once. A drive session's goals put theirs to
the person at the session's keyboard or page while the session goes on reading lines, and the
reply comes later, from a coroutine. `answered` takes either.

A coroutine is truthy, so a site that called an asker and took what came back for the answer
would read every question a session's goal put as a yes nobody gave. Every site goes through
here, and a test reads the code for one that does not (`tests/test_answers.py`).

While a later answer is awaited the run's abort is watched beside it: a Stop, a failing heartbeat
or a newer command takes the question back, which raises `QuestionWithdrawn`, recorded as a
question nobody answered and never as a no."""

from __future__ import annotations

import asyncio
import contextlib
import inspect
from collections.abc import Callable, Mapping
from typing import Any


class QuestionWithdrawn(Exception):
    """A question taken back before anybody answered it: by a stop, by the page going away, or
    by nobody answering in time."""


WITHDRAWN = "the run was stopped"
"""What a question the run's abort took back says it was withdrawn for."""

GO_AHEAD = "Go ahead anyway?"
"""What a person is asked under the pilot's reason for doubting the task (`decide`)."""
WATCHING = "Are you watching the robot right now?"
"""What a person is asked under the warning about a body that cannot see a fall
(`acknowledge`)."""
DID_IT = "Did the arm do it?"
"""What a person is asked under what ran, for a pilot that cannot tell (`judge`)."""


def confirm_question(name: str, params: Mapping[str, Any]) -> str:
    """The confirm gate's question, `run kick(leg=left)?`, in the words the gate's record quotes,
    however the question is put."""
    from quackd.log import fmt_params

    return f"run {name}({fmt_params(params)})?"


async def answered(
    asker: Callable[..., Any], *args: Any, abort: asyncio.Event | None = None
) -> bool:
    """What `asker` says to `args`: at once where it answers synchronously, and awaited where it
    answers as a coroutine, with `abort` watched beside it. An answer that came as the abort was
    set has been given, and stands; one still awaited is taken back, its coroutine cancelled, and
    `QuestionWithdrawn` raised. Whatever the asker itself raises goes up as it was."""
    from quackd.release import cancel_and_wait

    said = asker(*args)
    if not inspect.isawaitable(said):
        return bool(said)
    if abort is not None and abort.is_set():
        # stopped before it was put: a coroutine that never ran has shown nobody anything, and
        # the page is not handed a question only to see it taken back
        if inspect.iscoroutine(said):
            said.close()
        else:
            asyncio.ensure_future(said).cancel()
        raise QuestionWithdrawn(WITHDRAWN)
    waiting = asyncio.ensure_future(said)
    if abort is None:
        return bool(await waiting)
    stopping = asyncio.ensure_future(abort.wait())
    try:
        await asyncio.wait({waiting, stopping}, return_when=asyncio.FIRST_COMPLETED)
    except BaseException:
        # this task was cancelled, or interrupted, while the question waited: neither is left
        # running behind it, and the cancellation goes on up
        with contextlib.suppress(BaseException):
            await cancel_and_wait(waiting, stopping)
        raise
    if waiting.done():
        await cancel_and_wait(stopping)
        return bool(waiting.result())
    await cancel_and_wait(waiting, stopping)
    raise QuestionWithdrawn(WITHDRAWN)
