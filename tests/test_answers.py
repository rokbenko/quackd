"""One way to wait for a person's answer (`quackd.answers`), and every question a run puts going
through it."""

from __future__ import annotations

import ast
import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path

import pytest

from quackd.answers import WITHDRAWN, QuestionWithdrawn, answered

ROOT = Path(__file__).resolve().parents[1]

ANSWERERS = {"confirm", "decide", "judge", "acknowledge"}
"""The four questions a run puts, by the names `RunConfig` and the executor hold them under."""

SITES = ("quackd/agent/loop.py", "quackd/safety.py")
"""Where they are put. Nothing else in quackd calls one (`git grep` for the names finds the
decision LLM's `decide`, the auction's and typer's `confirm`, never these)."""


def _nobody(put: asyncio.Event, cancelled: list[str]) -> Callable[[str], Awaitable[bool]]:
    """An asker that puts its question and waits for an answer that never comes, recording that
    it was taken back."""

    async def waits(question: str) -> bool:
        put.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.append(question)
            raise
        return True

    return waits


async def test_an_asker_that_answers_at_once_is_taken_at_its_word() -> None:
    abort = asyncio.Event()
    assert await answered(lambda q: q == "go?", "go?", abort=abort) is True
    assert await answered(lambda _q: 0, "go?", abort=abort) is False
    assert await answered(lambda _name, _params: "yes", "kick", {}) is True


async def test_an_asker_that_answers_later_is_awaited() -> None:
    async def later(question: str) -> bool:
        await asyncio.sleep(0)
        return question == "go?"

    abort = asyncio.Event()
    assert await answered(later, "go?", abort=abort) is True
    assert await answered(later, "stay?", abort=abort) is False
    assert await answered(later, "go?") is True, "with no abort to watch, it is simply awaited"


async def test_a_question_still_waiting_when_the_run_is_stopped_is_taken_back() -> None:
    """A coroutine is truthy: a question nobody answered must never come back as a yes, nor as
    a no, and must not be left open where it was put."""
    abort, put, cancelled = asyncio.Event(), asyncio.Event(), list[str]()
    waiting = asyncio.ensure_future(answered(_nobody(put, cancelled), "go?", abort=abort))
    await asyncio.wait_for(put.wait(), timeout=2)
    abort.set()
    with pytest.raises(QuestionWithdrawn, match=WITHDRAWN):
        await asyncio.wait_for(waiting, timeout=2)
    assert cancelled == ["go?"], "the question is taken back where it was put"


async def test_a_question_put_after_the_run_was_stopped_is_never_shown() -> None:
    abort = asyncio.Event()
    abort.set()
    shown: list[str] = []

    async def shows(question: str) -> bool:
        shown.append(question)
        return True

    with pytest.raises(QuestionWithdrawn, match=WITHDRAWN):
        await answered(shows, "go?", abort=abort)
    assert shown == [], "nobody is handed a question the run has already taken back"
    assert await answered(lambda _q: True, "go?", abort=abort) is True, (
        "a prompt that answers at once is asked as it always was"
    )


async def test_an_answer_given_as_the_run_was_stopped_stands() -> None:
    abort = asyncio.Event()

    async def yes_then_stop(_question: str) -> bool:
        abort.set()
        return True

    assert await answered(yes_then_stop, "go?", abort=abort) is True


async def test_a_question_whose_run_is_cancelled_goes_with_it() -> None:
    abort, put, cancelled = asyncio.Event(), asyncio.Event(), list[str]()
    waiting = asyncio.ensure_future(answered(_nobody(put, cancelled), "go?", abort=abort))
    await asyncio.wait_for(put.wait(), timeout=2)
    waiting.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiting
    assert cancelled == ["go?"]


async def test_what_an_asker_raises_goes_up_as_it_was() -> None:
    """click's `Abort` from a y/N prompt, the page's own withdrawal: each site records what was
    raised as a question nobody answered, so it must reach the site unchanged."""

    def interrupted(_question: str) -> bool:
        raise RuntimeError("Abort")

    async def gone(_question: str) -> bool:
        raise QuestionWithdrawn("the page went away")

    abort = asyncio.Event()
    with pytest.raises(RuntimeError, match="Abort"):
        await answered(interrupted, "go?", abort=abort)
    with pytest.raises(QuestionWithdrawn, match="the page went away"):
        await answered(gone, "go?", abort=abort)
    assert not abort.is_set()


# ── every site goes through it ──────────────────────────────────────────────────────────


def _name(node: ast.expr) -> str | None:
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Name):
        return node.id
    return None


@pytest.mark.parametrize("path", SITES)
def test_no_question_a_run_puts_is_called_around_answered(path: str) -> None:
    """`bool(self.confirm(...))` was how all four were read, and with an answerer that answers
    from a coroutine it reads every question as a yes nobody gave."""
    tree = ast.parse((ROOT / path).read_text(encoding="utf-8"))
    direct = [
        f"{path}:{node.lineno} calls {_name(node.func)}(...)"
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and _name(node.func) in ANSWERERS
    ]
    assert direct == [], "put the question through quackd.answers.answered"


def test_all_four_questions_are_put_through_answered() -> None:
    """The other half: a site renamed or rewritten out of the AST's sight would leave the test
    above passing with nothing to check."""
    put: list[str] = []
    for path in SITES:
        tree = ast.parse((ROOT / path).read_text(encoding="utf-8"))
        put += [
            name
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and _name(node.func) == "answered" and node.args
            for name in [_name(node.args[0])]
            if name is not None
        ]
    assert sorted(put) == sorted(ANSWERERS), put
