"""`roboglot.release`: what both the end of a run and `roboglot robot release` do with a fold that
missed, tested on its own with a stand-in person and a stand-in arm.

The run's and the command's own tests cover each path end to end (tests/test_loop.py,
tests/test_cli.py). These pin the parts neither can reach cleanly: that a fold stopped by a
press is cancelled and waited for, and never outlives the call that raced it; that a fold the
caller's own task is cancelled out of is cancelled with it; and that nothing here ever raises
past the close that has to follow it."""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import replace
from typing import Any

import pytest

from roboglot.adapters.base import HandResult, RestResult
from roboglot.release import (
    CLEAR_THE_WAY,
    CLEAR_THE_WAY_S,
    DECLINED,
    DROP_AFTER_TWO_FOLDS,
    ENTER,
    ENTER_DROPS_S,
    FOLD_INTERRUPTED,
    FOLD_STOPPED,
    FOLD_UNANSWERED,
    FOLD_WENT_ON,
    LIMP_AFTER_A_MISS,
    NO_KEYS,
    NOBODY,
    NOT_SENT,
    RELEASE_INTERRUPTED,
    RELEASE_NOT_SENT,
    TORQUE_READS_OFF,
    WAIT_INTERRUPTED,
    FoldStopped,
    ask,
    cancel_and_wait,
    fold_unless_stopped,
    fold_went_on,
    one_watch,
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


def _pressed_once_the_release_begins(began: asyncio.Event) -> Any:
    async def press() -> None:
        await began.wait()

    return lambda: press()


async def test_a_press_stops_a_drop_whose_torque_write_has_not_gone_out() -> None:
    """Enter at the question that says the arm will drop, and then a change of mind. The release
    reads first, and with a Ctrl-C no longer cancelling anything nothing heard the press: the
    person read that the robot was stopping as the arm went limp."""
    events: list[dict[str, Any]] = []
    began = asyncio.Event()

    class ReadingFirst(Arm):
        async def let_go(self, **kw: Any) -> HandResult:
            began.set()
            await asyncio.sleep(30)  # the read it begins with, on a bus that is slow today
            return await super().let_go(**kw)

    arm = ReadingFirst()
    settled = await release_where_it_stands(
        arm,
        event=lambda **d: events.append(d),
        stopped=_pressed_once_the_release_begins(began),
    )
    assert arm.calls == [] and arm.in_hand is False, "the torque write went out"
    assert settled.how == "kept"
    assert settled.said == RELEASE_NOT_SENT
    assert [e["stage"] for e in events] == ["kept"]


async def test_a_press_after_the_write_has_gone_out_says_it_may_be_limp() -> None:
    began = asyncio.Event()

    class WroteThenRead(Arm):
        async def let_go(self, **kw: Any) -> HandResult:
            self.in_hand = True
            began.set()
            await asyncio.sleep(30)
            return self.released

    settled = await release_where_it_stands(
        WroteThenRead(), stopped=_pressed_once_the_release_begins(began)
    )
    assert settled.how == "interrupted"
    assert settled.said == RELEASE_INTERRUPTED


async def test_the_drop_at_the_second_question_hears_a_press() -> None:
    """Through the two questions, the way a run and `roboglot robot release` come to it."""

    class Stopping(Person):
        def __init__(self, *answers: bool) -> None:
            super().__init__(*answers)
            self.began = asyncio.Event()

        def stopped(self) -> Any:
            return _pressed_once_the_release_begins(self.began)()

    person = Stopping(True)

    class ReadingFirst(Arm):
        async def let_go(self, **kw: Any) -> HandResult:
            person.began.set()
            await asyncio.sleep(30)
            return await super().let_go(**kw)

    arm = ReadingFirst()
    fold, _made = _folds(MISSED)

    async def stop() -> None:
        return None

    settled = await settle_missed_fold(arm, person, MISSED, fold=fold, stop=stop)
    assert settled.how == "kept" and arm.calls == [], "the arm went limp after the press"


class Pressing(Person):
    """Somebody who can press, heard the way a terminal hears it: counted, from when asked."""

    def __init__(self, *answers: bool) -> None:
        super().__init__(*(answers or (True,)))
        self.presses = 0
        self.pressed = asyncio.Event()

    def press(self) -> None:
        self.presses += 1
        self.pressed.set()

    def stopped(self) -> Any:
        presses = self.presses

        async def fresh() -> None:
            while self.presses == presses:
                self.pressed.clear()
                await self.pressed.wait()

        return fresh()


async def test_a_press_as_the_enter_for_a_drop_is_recorded_stops_the_drop() -> None:
    """Two to eight milliseconds in a real console, between the Enter being taken and the release's
    own watch beginning: the press was counted, and missed, and the torque write went out."""
    person = Pressing(True, True)

    class ReadingFirst(Arm):
        async def let_go(self, **kw: Any) -> HandResult:
            await asyncio.sleep(0.2)  # the read it begins with, which is on the bus
            return await super().let_go(**kw)

    arm = ReadingFirst()
    fold, _made = _folds(MISSED)

    async def stop() -> None:
        return None

    def asked(question: str, _agreed: bool) -> None:
        if question.startswith(DROP_AFTER_TWO_FOLDS[:20]):
            person.press()

    settled = await settle_missed_fold(arm, person, MISSED, fold=fold, stop=stop, asked=asked)
    assert arm.calls == [], "the torque write went out"
    assert settled.how == "kept" and settled.said == RELEASE_NOT_SENT


async def test_one_watch_hears_a_press_made_between_what_it_watches() -> None:
    """The count and then the fold, with a line printed in between. Each counting presses from where
    it began took one made there for an old one, and the arm folded under the hand that made it."""
    person = Pressing()
    moved: list[str] = []

    async def fold() -> str:
        await asyncio.sleep(0.2)  # the read a real fold begins with, and it goes on the bus
        moved.append("the fold ran on")
        return "arrived"

    async with one_watch(person) as stopped:
        assert await fold_unless_stopped(_nothing, stopped) is None
        person.press()  # in between: nothing is racing a press just now
        with pytest.raises(FoldStopped):
            await fold_unless_stopped(fold, stopped)
    assert moved == [], "the fold moved after the press"


async def test_one_watch_of_a_person_who_cannot_say_watches_nothing() -> None:
    async with one_watch(object()) as stopped:
        assert stopped is None


async def test_a_fold_that_will_not_go_does_not_hold_up_what_comes_after(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A press can no longer quit past it (`KillSwitch.stay_to_the_close`), so nothing else would
    end the wait. No fold Roboglot ships ignores a cancellation: this is for a third party's."""
    monkeypatch.setattr("roboglot.release.STOPPED_FOLD_S", 0.05)
    let_go = asyncio.Event()

    async def stubborn() -> None:
        while not let_go.is_set():
            with contextlib.suppress(asyncio.CancelledError):
                await let_go.wait()

    task = asyncio.ensure_future(stubborn())
    await asyncio.sleep(0)
    try:
        await asyncio.wait_for(cancel_and_wait(task), timeout=10)
    finally:
        let_go.set()
        await task


#: `STOPPED_FOLD_S` shrunk for the tests below, as the test above shrinks it, and a fold's tick.
SHORT_STOPPED_FOLD_S = 0.05
FOLD_TICK_S = SHORT_STOPPED_FOLD_S / 5


class _PressingPerson(Person):
    """Enter at the first question, then a press once the second fold has begun."""

    def __init__(self, folding: asyncio.Event) -> None:
        super().__init__(True)
        self.folding = folding

    async def stopped(self) -> None:
        await self.folding.wait()
        await asyncio.sleep(2 * FOLD_TICK_S)


def _fold_that_swallows(
    timeline: list[str], folding: asyncio.Event, ended: asyncio.Event, *, cancels: int
) -> Any:
    """A second fold that sends a goal every tick and catches its first `cancels` cancels, as one
    whose cancel was lost on its way does, running on until `ended` is set or one gets through."""

    async def fold() -> RestResult:
        caught = 0
        try:
            folding.set()
            while not ended.is_set():
                timeline.append("goal")
                try:
                    await asyncio.sleep(FOLD_TICK_S)
                except asyncio.CancelledError:
                    caught += 1
                    if caught > cancels:
                        raise
            return RestResult("arrived", "moved to the rest pose")
        finally:
            ended.set()

    return fold


async def test_a_fold_that_lost_its_cancel_is_told_again_before_it_is_said_to_have_stopped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A fold the person stopped was cancelled once and left after `STOPPED_FOLD_S` whether it had
    ended or not: the person was told it had stopped where it was, the stop went out, and the
    fold, which had lost its cancel, sent its next goals after that stop."""
    monkeypatch.setattr("roboglot.release.STOPPED_FOLD_S", SHORT_STOPPED_FOLD_S)
    timeline: list[str] = []
    folding, ended = asyncio.Event(), asyncio.Event()

    async def stop() -> None:
        timeline.append("stop")

    fold = _fold_that_swallows(timeline, folding, ended, cancels=1)
    settled = await asyncio.wait_for(
        settle_missed_fold(Arm(), _PressingPerson(folding), MISSED, fold=fold, stop=stop), 2.0
    )
    alive_when_said = not ended.is_set()
    ended.set()
    assert settled.said == FOLD_STOPPED
    assert not alive_when_said, "told the fold stopped while it was still running"
    assert timeline[-1] == "stop", f"goals went out after the stop: {timeline}"


async def test_a_fold_that_will_not_stop_is_never_said_to_have_stopped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The same fold catching every cancel: after the second wait it is left, and the person is
    told plainly that Roboglot cannot tell whether the arm still moves, not that it stopped."""
    monkeypatch.setattr("roboglot.release.STOPPED_FOLD_S", SHORT_STOPPED_FOLD_S)
    timeline: list[str] = []
    folding, ended = asyncio.Event(), asyncio.Event()
    noted: list[str] = []

    async def stop() -> None:
        timeline.append("stop")

    fold = _fold_that_swallows(timeline, folding, ended, cancels=10**6)
    try:
        settled = await asyncio.wait_for(
            settle_missed_fold(
                Arm(), _PressingPerson(folding), MISSED, fold=fold, stop=stop, note=noted.append
            ),
            2.0,
        )
        assert not ended.is_set(), "the stand-in fold ended, so this proves nothing"
        assert settled.how == "kept" and settled.reason == FOLD_INTERRUPTED
        assert settled.said == FOLD_WENT_ON and noted == [FOLD_WENT_ON]
        assert "stop" in timeline, "the stop is still sent"
    finally:
        ended.set()


async def test_a_release_that_will_not_stop_is_never_said_to_have_sent_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A release that had not gone out yet when it was stopped, and would not end, was read as
    one that sent nothing, though it could still go out after: it gets the limp reading."""
    monkeypatch.setattr("roboglot.release.STOPPED_FOLD_S", SHORT_STOPPED_FOLD_S)
    let_go = asyncio.Event()

    class Stubborn(Arm):
        async def let_go(self, **kw: Any) -> HandResult:
            while not let_go.is_set():
                with contextlib.suppress(asyncio.CancelledError):
                    await let_go.wait()
            return await super().let_go(**kw)

    async def stopped() -> None:
        await asyncio.sleep(FOLD_TICK_S)

    try:
        settled = await asyncio.wait_for(release_where_it_stands(Stubborn(), stopped=stopped), 2.0)
        assert settled.how == "interrupted" and settled.said == RELEASE_INTERRUPTED
    finally:
        let_go.set()


class _Watching(Person):
    """Enter at every question, and a watch for a press that never fires: nobody presses."""

    async def stopped(self) -> None:
        await asyncio.Event().wait()


async def test_a_fold_cancelled_from_outside_that_goes_on_is_never_said_to_have_stopped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The second fold cancelled from outside, a later Ctrl-C handed back to asyncio, rather than
    stopped by a press, was told to stop once: a fold that lost that cancel was said to have
    stopped where it was, and its next goals followed the stop sent after it."""
    monkeypatch.setattr("roboglot.release.STOPPED_FOLD_S", SHORT_STOPPED_FOLD_S)
    timeline: list[str] = []
    folding, ended = asyncio.Event(), asyncio.Event()

    async def fold() -> RestResult:
        try:
            folding.set()
            while not ended.is_set():
                timeline.append("goal")
                try:
                    await asyncio.sleep(FOLD_TICK_S)
                except asyncio.CancelledError:
                    timeline.append("cancel caught")
            return RestResult("arrived", "moved to the rest pose")
        finally:
            ended.set()

    async def stop() -> None:
        timeline.append("stop")

    settling = asyncio.create_task(
        settle_missed_fold(Arm(), _Watching(True), MISSED, fold=fold, stop=stop)
    )
    try:
        await asyncio.wait_for(folding.wait(), 2.0)
        await asyncio.sleep(2 * FOLD_TICK_S)
        settling.cancel()
        settled = await asyncio.wait_for(settling, 2.0)
        assert not ended.is_set(), "the stand-in fold ended, so this proves nothing"
        assert timeline.count("cancel caught") == 2, "it was told twice"
        assert settled.said == FOLD_WENT_ON, settled.said
    finally:
        ended.set()


async def test_a_release_cancelled_from_outside_that_goes_on_is_never_said_unsent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The release cancelled from outside the same way, read before it wrote, as the arm's is:
    `in_hand` was still False, the person was told nothing had been sent, and then it went out
    and the arm dropped."""
    monkeypatch.setattr("roboglot.release.STOPPED_FOLD_S", SHORT_STOPPED_FOLD_S)
    started, go_on = asyncio.Event(), asyncio.Event()

    class Stubborn(Arm):
        async def let_go(self, **kw: Any) -> HandResult:
            started.set()
            while not go_on.is_set():
                with contextlib.suppress(asyncio.CancelledError):
                    await go_on.wait()
            return await super().let_go(**kw)

    arm = Stubborn()
    releasing = asyncio.create_task(release_where_it_stands(arm, stopped=_Watching(True).stopped))
    try:
        await asyncio.wait_for(started.wait(), 2.0)
        releasing.cancel()
        settled = await asyncio.wait_for(releasing, 2.0)
        assert settled.said == RELEASE_INTERRUPTED, settled.said
    finally:
        go_on.set()


@pytest.mark.parametrize("swallows", [1, 2])
async def test_a_cancel_of_a_fold_says_whether_the_fold_went_on(
    monkeypatch: pytest.MonkeyPatch, swallows: int
) -> None:
    """What every caller of `fold_unless_stopped` reads a cancel by (`fold_went_on`), in
    roboglot/agent/loop.py and roboglot/cli.py as here: a fold that lost the first cancel and
    ended on the second is not marked, and one that lost both is."""
    monkeypatch.setattr("roboglot.release.STOPPED_FOLD_S", SHORT_STOPPED_FOLD_S)
    folding, ended = asyncio.Event(), asyncio.Event()

    async def fold() -> RestResult:
        caught = 0
        folding.set()
        while not ended.is_set():
            try:
                await asyncio.sleep(FOLD_TICK_S)
            except asyncio.CancelledError:
                caught += 1
                if caught > swallows:
                    raise
        return RestResult("arrived", "moved to the rest pose")

    async def nobody() -> None:
        await asyncio.Event().wait()

    running = asyncio.create_task(fold_unless_stopped(fold, nobody))
    try:
        await asyncio.wait_for(folding.wait(), 2.0)
        running.cancel()
        await asyncio.wait({running}, timeout=2.0)
        assert running.done()
        with pytest.raises(asyncio.CancelledError) as cancelled:
            running.result()
        assert fold_went_on(cancelled.value) is (swallows == 2)
    finally:
        ended.set()


async def _pressed_twice(task: asyncio.Task[Any]) -> None:
    """A press that lands in the fold, and a second one while the fold told to stop is waited
    for, as a person pressing twice does."""
    task.cancel()
    await asyncio.sleep(SHORT_STOPPED_FOLD_S / 2)
    task.cancel()


async def test_a_fold_pressed_twice_that_goes_on_is_never_said_to_have_stopped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The second press, landing while the fold told to stop was waited for, raised out of that
    wait and took the place of everything after it: the fold was told once, nothing marked it,
    and the person was told it had stopped while it went on."""
    monkeypatch.setattr("roboglot.release.STOPPED_FOLD_S", SHORT_STOPPED_FOLD_S)
    timeline: list[str] = []
    folding, ended = asyncio.Event(), asyncio.Event()

    async def fold() -> RestResult:
        try:
            folding.set()
            while not ended.is_set():
                timeline.append("goal")
                try:
                    await asyncio.sleep(FOLD_TICK_S)
                except asyncio.CancelledError:
                    timeline.append("cancel caught")
            return RestResult("arrived", "moved to the rest pose")
        finally:
            ended.set()

    async def stop() -> None:
        timeline.append("stop")

    settling = asyncio.create_task(
        settle_missed_fold(Arm(), _Watching(True), MISSED, fold=fold, stop=stop)
    )
    try:
        await asyncio.wait_for(folding.wait(), 2.0)
        await asyncio.sleep(2 * FOLD_TICK_S)
        await _pressed_twice(settling)
        settled = await asyncio.wait_for(settling, 2.0)
        assert not ended.is_set(), "the stand-in fold ended, so this proves nothing"
        assert timeline.count("cancel caught") == 2, "it was not told twice"
        assert settled.said == FOLD_WENT_ON, settled.said
    finally:
        ended.set()


async def test_a_release_pressed_twice_that_goes_on_is_never_said_unsent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The same second press over a release that read before it wrote: the person was told
    nothing had been sent, and then it went out and the arm dropped."""
    monkeypatch.setattr("roboglot.release.STOPPED_FOLD_S", SHORT_STOPPED_FOLD_S)
    started, go_on = asyncio.Event(), asyncio.Event()

    class Stubborn(Arm):
        async def let_go(self, **kw: Any) -> HandResult:
            started.set()
            while not go_on.is_set():
                with contextlib.suppress(asyncio.CancelledError):
                    await go_on.wait()
            return await super().let_go(**kw)

    arm = Stubborn()
    releasing = asyncio.create_task(release_where_it_stands(arm, stopped=_Watching(True).stopped))
    try:
        await asyncio.wait_for(started.wait(), 2.0)
        await _pressed_twice(releasing)
        settled = await asyncio.wait_for(releasing, 2.0)
        assert settled.said == RELEASE_INTERRUPTED, settled.said
    finally:
        go_on.set()


def _full_disk_on(stage: str) -> Any:
    """The run's record of the release, raising as a full disk does on one stage of it."""

    def event(**data: Any) -> None:
        if data.get("stage") == stage:
            raise OSError(28, "No space left on device")

    return event


async def test_a_record_that_fails_after_the_release_went_out_still_says_what_it_did() -> None:
    """`settle_missed_fold` never raises, but a record write that failed after the release had
    gone out raised out of it, and the caller, which swallows that, never said the line for the
    person holding the arm: whether torque came off, and what still holds."""
    person, arm, noted = Person(True), Arm(), []
    fold, _made = _folds(MISSED, MISSED)
    settled = await asyncio.wait_for(
        settle_missed_fold(
            arm,
            person,
            MISSED,
            fold=fold,
            stop=_nothing,
            event=_full_disk_on("released"),
            note=noted.append,
        ),
        2.0,
    )
    assert arm.calls == [{"anywhere": True}], "the release did not go out"
    assert settled.how == "released" and settled.said and "in your hands" in settled.said
    assert any("No space left on device" in n for n in noted), noted


async def test_a_record_that_fails_before_the_second_fold_still_makes_it() -> None:
    """With the disk already full the first write to raise is the one before the second fold
    (the question's own is caught), and Enter at the first question then folded nothing and said
    nothing."""
    person, arm = Person(True), Arm()
    fold, made = _folds(MISSED, MISSED)
    settled = await asyncio.wait_for(
        settle_missed_fold(
            arm, person, MISSED, fold=fold, stop=_nothing, event=_full_disk_on("refold")
        ),
        2.0,
    )
    assert made == ["fold"], "the second fold was never made"
    assert settled.how == "released" and settled.said


async def test_two_questions_over_an_arm_keep_every_press_short_of_quitting() -> None:
    """Somebody is at the arm from the first question on, and the third Ctrl-C used to go past the
    close with the arm energised and nothing said of it (`KillSwitch.stay_to_the_close`)."""
    told: list[str] = []
    person, arm = Person(False), Arm()
    person.stay_to_the_close = lambda: told.append(f"after {len(person.asked)} questions")  # type: ignore[attr-defined]
    fold, _made = _folds(MISSED)

    async def stop() -> None:
        return None

    await settle_missed_fold(arm, person, MISSED, fold=fold, stop=stop)
    assert told == ["after 0 questions"]


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


async def test_a_fold_reason_quoted_inside_a_question_carries_no_escape() -> None:
    """Both questions over an arm whose fold missed quote the fold's reason, which can carry a
    transport's error text, and the terminal printed it as it came: an escape sequence in it
    could erase the warning around it, in the one question whose Enter drops the arm. The reason
    is written out through `printable`, so this fails if one reaches the question again; a
    reason with no control character reads as it did (the test above)."""
    from roboglot.ui import printable

    why = "the bus said \x1b[2K\x1b[1Aall clear"
    missed = RestResult("stalled", why)
    person, arm = Person(True), Arm()
    fold, _made = _folds(missed)

    async def stop() -> None:
        return None

    await settle_missed_fold(arm, person, missed, fold=fold, stop=stop)
    asked = [a[0] for a in person.asked]
    assert asked == [
        CLEAR_THE_WAY.format(why=printable(why), seconds=CLEAR_THE_WAY_S),
        DROP_AFTER_TWO_FOLDS.format(why=printable(why), seconds=ENTER_DROPS_S),
    ]
    assert not any("\x1b" in question for question in asked), asked


async def test_a_fold_that_missed_over_limp_joints_asks_nothing() -> None:
    """A fold that missed because servos lost their torque on the way, a brown-out under stall
    current, is no arm holding itself up where it stopped, which is what the first question
    begins by saying. A second fold cannot move a joint with no torque, and a drop would let go
    of whatever still holds while the rest has gone, so neither is offered: what the read found
    is said, and torque stays on wherever it still is."""
    person, arm = Person(True), Arm()
    fold, made = _folds(MISSED)
    events: list[dict[str, Any]] = []
    notes: list[str] = []
    limp = replace(MISSED, limp=("shoulder_lift", "elbow_flex"))
    settled = await settle_missed_fold(
        arm,
        person,
        limp,
        fold=fold,
        stop=_nothing,
        event=lambda **kw: events.append(kw),
        note=notes.append,
    )
    said = LIMP_AFTER_A_MISS.format(why=MISSED.reason, limp="shoulder_lift, elbow_flex")
    assert person.asked == [] and made == [] and arm.calls == [], "something was asked or moved"
    assert settled.how == "kept" and settled.reason == TORQUE_READS_OFF and settled.said == said
    assert events == [{"stage": "kept", "reason": TORQUE_READS_OFF}] and notes == [said]


async def test_a_second_fold_that_found_the_joints_limp_puts_no_question_that_drops() -> None:
    """The way was cleared, Enter pressed, and the arm browned out on its second fold: what the
    read found is said, and the question whose Enter drops the arm is never put."""
    person, arm = Person(True), Arm()
    browned = replace(MISSED, limp=("elbow_flex",))
    fold, made = _folds(browned)
    settled = await settle_missed_fold(arm, person, MISSED, fold=fold, stop=_nothing)
    assert [a[0] for a in person.asked] == [
        CLEAR_THE_WAY.format(why=MISSED.reason, seconds=CLEAR_THE_WAY_S)
    ]
    assert made == ["fold"] and arm.calls == [], "the arm was let go of where it stands"
    assert settled.how == "kept" and settled.reason == TORQUE_READS_OFF
    assert settled.asked == "clear" and settled.fold is browned


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
