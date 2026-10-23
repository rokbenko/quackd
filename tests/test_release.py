"""`quackd.release`: what both the end of a run and `quackd robot release` do with a fold that
missed, tested on its own with a stand-in person and a stand-in arm.

The run's and the command's own tests cover each path end to end (tests/test_loop.py,
tests/test_cli.py). These pin the parts neither can reach cleanly: that a fold stopped by a
press is cancelled and waited for, and never outlives the call that raced it; that a fold the
caller's own task is cancelled out of is cancelled with it; and that nothing here ever raises
past the close that has to follow it."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from quackd.adapters.base import HandResult, RestResult
from quackd.release import (
    CLEAR_THE_WAY,
    CLEAR_THE_WAY_S,
    DECLINED,
    DROP_AFTER_TWO_FOLDS,
    ENTER,
    ENTER_DROPS_S,
    FOLD_INTERRUPTED,
    FOLD_UNANSWERED,
    NO_KEYS,
    NOBODY,
    NOT_SENT,
    WAIT_INTERRUPTED,
    FoldStopped,
    ask,
    fold_unless_stopped,
    release_where_it_stands,
    settle_missed_fold,
)

MISSED = RestResult("stalled", "elbow_flex is at 61 with a goal of 20, and it has stopped moving")


class Person:
    """Somebody at the arm, answering each question from a script, the last answer again for
    any after, and saying how each wait ended where `ended` is given."""

    def __init__(self, *answers: bool, ended: str | None = None) -> None:
        self.answers = list(answers) or [False]
        self.ended = ended
        self.asked: list[tuple[str, float | None]] = []
        self.said: list[str] = []

    def say(self, text: str) -> None:
        self.said.append(text)

    async def wait(
        self,
        text: str,
        *,
        timeout_s: float | None = None,
        until_abort: bool = True,
        deliberate: bool = False,
    ) -> bool:
        assert until_abort is False, "a question about a missed fold watched the abort flag"
        assert deliberate is True, "a question that moves or drops the arm took any line as Enter"
        self.asked.append((text, timeout_s))
        return self.answers[min(len(self.asked) - 1, len(self.answers) - 1)]


class Arm:
    """The arm's half: its release, recorded, with what it answers and whether it is in a hand."""

    def __init__(self, released: HandResult | None = None) -> None:
        self.released = released or HandResult("released", "torque is off", torque_on=())
        self.calls: list[dict[str, Any]] = []
        self.in_hand = False

    async def let_go(self, **kw: Any) -> HandResult:
        self.calls.append(kw)
        self.in_hand = True
        return self.released


def _folds(*results: RestResult) -> tuple[Any, list[str]]:
    """A fold that answers each time with the next result, and a record of every one made."""
    made: list[str] = []

    async def fold() -> RestResult:
        made.append("fold")
        return results[min(len(made) - 1, len(results) - 1)]

    return fold, made


async def _nothing() -> None:
    return None


# ── fold_unless_stopped ───────────────────────────────────────────────────────────────


async def test_a_press_cancels_the_fold_and_waits_for_it_before_saying_so() -> None:
    """The fold a person asked for is raced against a fresh press, and the press wins here. The
    fold is cancelled at its await and waited for, so by the time `FoldStopped` reaches the
    caller there is no fold task left anywhere to send another goal."""
    sent: list[str] = []
    finished: list[str] = []

    async def fold() -> RestResult:
        try:
            while True:
                sent.append("goal")
                await asyncio.sleep(0.01)
        finally:
            finished.append("fold")

    async def stopped() -> None:
        await asyncio.sleep(0.05)

    with pytest.raises(FoldStopped):
        await fold_unless_stopped(fold, stopped)
    assert finished == ["fold"], "the fold task was left running"
    count = len(sent)
    await asyncio.sleep(0.05)
    assert len(sent) == count, "a goal was sent after the fold was stopped"


async def test_a_fold_that_ends_first_is_what_happened_and_the_watch_is_ended() -> None:
    """A fold that arrives before anybody presses anything returns its own result, and the watch
    raced against it is cancelled rather than left waiting on a press for ever."""
    watching: list[str] = []

    async def stopped() -> None:
        try:
            await asyncio.Event().wait()
        finally:
            watching.append("ended")

    arrived = RestResult("arrived", "moved to the rest pose")
    fold, _made = _folds(arrived)
    assert await fold_unless_stopped(fold, stopped) is arrived
    assert watching == ["ended"], "the watch for a press outlived the fold"


async def test_a_cancelled_caller_cancels_the_fold_with_it() -> None:
    """A later Ctrl-C reaches the teardown as a cancellation of its own task, not through the
    switch. The fold racing inside it must be cancelled and waited for before that cancellation
    goes on up: a fold left running behind it would go on sending goals while the close decides
    whether to let go."""
    finished: list[str] = []
    started = asyncio.Event()

    async def fold() -> RestResult:
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            finished.append("fold")
        raise AssertionError("unreachable")

    async def stopped() -> None:
        await asyncio.Event().wait()

    racing = asyncio.ensure_future(fold_unless_stopped(fold, stopped))
    await started.wait()
    racing.cancel()
    with pytest.raises(asyncio.CancelledError):
        await racing
    assert finished == ["fold"], "the fold outlived the call that raced it"


async def test_a_watch_that_fails_is_no_stop() -> None:
    """A `stopped` that raises is a broken watch, not a press: the fold goes on alone, and its
    own result comes back."""

    async def broken() -> None:
        raise RuntimeError("a watch that broke")

    arrived = RestResult("arrived", "moved to the rest pose")

    async def fold() -> RestResult:
        await asyncio.sleep(0.02)
        return arrived

    assert await fold_unless_stopped(fold, broken) is arrived


async def test_a_person_who_cannot_say_stop_gets_the_fold_awaited() -> None:
    fold, made = _folds(RestResult("arrived", "moved to the rest pose"))
    folded = await fold_unless_stopped(fold, None)
    assert folded is not None and folded.reached and made == ["fold"]


# ── ask ───────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("answer", "ended", "said"),
    [
        (True, "enter", ENTER),
        (False, "kill switch", WAIT_INTERRUPTED),
        (False, "no keys", NO_KEYS),
        (False, "timeout", NOBODY),
        (False, None, NOBODY),
    ],
)
async def test_ask_says_how_each_wait_ended(answer: bool, ended: str | None, said: str) -> None:
    person = Person(answer, ended=ended)
    recorded: list[tuple[str, bool]] = []
    got = await ask(person, "a question", 60.0, asked=lambda q, a: recorded.append((q, a)))
    assert got == said
    assert recorded == [("a question", answer)], "the exchange was not recorded"


@pytest.mark.parametrize("press", [KeyboardInterrupt, asyncio.CancelledError])
async def test_ask_catches_an_interrupt_and_records_no_exchange(press: type[BaseException]) -> None:
    """An interrupt that lands on a wait means "skip this", and whatever follows the question,
    the close above all, still has to happen, so it is caught. Nobody answered, so nothing is
    recorded as an answer."""

    class Interrupted(Person):
        async def wait(self, text: str, **_: Any) -> bool:
            raise press

    recorded: list[Any] = []
    got = await ask(Interrupted(), "a question", 60.0, asked=lambda *a: recorded.append(a))
    assert got == WAIT_INTERRUPTED and recorded == []


# ── release_where_it_stands ───────────────────────────────────────────────────────────


@pytest.mark.parametrize("sent", [False, True], ids=["before it went out", "while it went out"])
async def test_an_interrupted_release_says_which_side_of_the_send_it_landed_on(sent: bool) -> None:
    """The arm's own backend marks the arm in a hand the moment the release goes out, so which
    side of the send an interrupt landed on is its `in_hand`. Before it, nothing was sent; after
    it, part of the arm may be limp, which is the reading that never drops an arm."""

    class Interrupted(Arm):
        async def let_go(self, **kw: Any) -> HandResult:
            self.in_hand = sent
            raise KeyboardInterrupt

    events: list[dict[str, Any]] = []
    settled = await release_where_it_stands(Interrupted(), event=lambda **d: events.append(d))
    if sent:
        assert settled.how == "interrupted" and "may be limp" in (settled.said or "")
    else:
        assert settled.how == "kept" and settled.reason == NOT_SENT
        assert "before anything was sent" in (settled.said or "")
    assert events[-1]["stage"] == settled.how


async def test_the_release_where_it_stands_is_the_second_door() -> None:
    arm = Arm()
    settled = await release_where_it_stands(arm)
    assert arm.calls == [{"anywhere": True}], arm.calls
    assert settled.how == "released" and settled.released is arm.released
    assert "the arm is in your hands, so put it down" in (settled.said or "")


# ── settle_missed_fold ────────────────────────────────────────────────────────────────


async def test_two_folds_and_two_questions_in_order_before_anything_drops() -> None:
    """The whole rule: the first question, the second fold, and only after that one missed too,
    the question that says the arm will drop, and the release on Enter."""
    person, arm = Person(True), Arm()
    fold, made = _folds(MISSED)
    stops: list[str] = []

    async def stop() -> None:
        stops.append("stop")

    settled = await settle_missed_fold(arm, person, MISSED, fold=fold, stop=stop)
    assert [a[0] for a in person.asked] == [
        CLEAR_THE_WAY.format(why=MISSED.reason, seconds=CLEAR_THE_WAY_S),
        DROP_AFTER_TWO_FOLDS.format(why=MISSED.reason, seconds=ENTER_DROPS_S),
    ]
    assert made == ["fold"] and stops == [], "one fold more, and nothing stopped"
    assert arm.calls == [{"anywhere": True}]
    assert settled.how == "released" and settled.asked == "drop"


async def test_a_fold_that_raises_is_settled_and_never_raises_past_the_close() -> None:
    """The caller closes the arm after this, so nothing may come out of it but a value. A fold
    that failed in a way nothing here can read is settled as one over an arm that stopped
    answering: nothing more is asked, since a drop offered over an arm nobody can read is
    offered blind."""

    async def fold() -> RestResult:
        raise RuntimeError("a bug in the fold")

    person, arm = Person(True), Arm()
    settled = await settle_missed_fold(arm, person, MISSED, fold=fold, stop=_nothing)
    assert settled.how == "kept" and settled.reason == FOLD_UNANSWERED
    assert len(person.asked) == 1 and arm.calls == [], "a drop was offered over an unread arm"


@pytest.mark.parametrize("press", [KeyboardInterrupt, asyncio.CancelledError])
async def test_an_interrupted_fold_is_stopped_and_kept(press: type[BaseException]) -> None:
    async def fold() -> RestResult:
        raise press

    stops: list[str] = []

    async def stop() -> None:
        stops.append("stop")

    person, arm = Person(True), Arm()
    settled = await settle_missed_fold(arm, person, MISSED, fold=fold, stop=stop)
    assert settled.how == "kept" and settled.reason == FOLD_INTERRUPTED
    assert stops == ["stop"] and arm.calls == [] and len(person.asked) == 1


@pytest.mark.parametrize("press", [KeyboardInterrupt, asyncio.CancelledError])
async def test_a_second_press_landing_on_the_stop_after_a_stopped_fold_is_caught(
    press: type[BaseException],
) -> None:
    """A person stopping a fold often presses twice: the first stops the fold, and the second
    lands on the stop that holds the arm after it. That one used to go straight through, and
    in a run's teardown it took the close, the close's line and the record with it, over an arm
    still holding itself up. It is caught: the arm holds, and the close still comes."""

    async def fold() -> RestResult:
        raise asyncio.CancelledError

    async def stop() -> None:
        raise press

    person, arm = Person(True), Arm()
    settled = await settle_missed_fold(arm, person, MISSED, fold=fold, stop=stop)
    assert settled.how == "kept" and settled.reason == FOLD_INTERRUPTED
    assert arm.calls == []


async def test_a_cancelled_caller_still_waits_the_stopped_fold_out() -> None:
    """A fold told to stop is gone at its next await, unless its own clean-up has more to do,
    and a later Ctrl-C landing while that clean-up runs used to let the cancellation go on up
    at once, with the fold still finishing behind it. The fold is waited out first, up to
    `STOPPED_FOLD_S`, and only then does the cancellation go on."""
    finished: list[str] = []
    started = asyncio.Event()

    async def fold() -> RestResult:
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            await asyncio.sleep(0.1)  # its clean-up: a hold, a last read
            finished.append("fold")
        raise AssertionError("unreachable")

    async def stopped() -> None:
        await started.wait()  # a press the moment the fold moves

    racing = asyncio.ensure_future(fold_unless_stopped(fold, stopped))
    await started.wait()
    await asyncio.sleep(0.02)  # the press has landed and the fold is cleaning up
    racing.cancel()  # and a later Ctrl-C lands on the wait for it
    with pytest.raises((asyncio.CancelledError, FoldStopped)):
        await racing
    assert finished == ["fold"], "the cancellation went on up before the fold had finished"


async def test_a_line_typed_at_a_question_is_answered_no() -> None:
    person = Person(False, ended="declined")
    assert await ask(person, "a question", 60.0) == DECLINED
