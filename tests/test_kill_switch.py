"""The kill switch: the key thread that reads stdin, and the one wait a run makes on a person.

`KillSwitch.install` wants a real terminal — it swaps the SIGINT handler and starts the key
thread only when stdin is a tty — so nothing here calls it. Every test sets `_loop` itself,
which is the one thing `install` does that the rest of the class needs, and then either runs
`_watch_keys` over a `StringIO` or sets the events the way that thread would. Nothing here
reads a real terminal, and nothing here calls `input()`: a wait that ends only on a keystroke
would hang the suite exactly as a `--by-hand` run correctly hangs waiting for a person.
"""

from __future__ import annotations

import asyncio
import gc
import io
import sys
import time
from typing import Any

import pytest

from quackd.safety import KillSwitch


def switch() -> tuple[KillSwitch, list[str]]:
    """A switch wired to the running loop, as `install` leaves one on a terminal."""
    said: list[str] = []
    ks = KillSwitch(asyncio.Event(), log=said.append)
    ks._loop = asyncio.get_running_loop()
    return ks, said


def waited(timeout_s: float) -> float:
    """The least a wait of `timeout_s` may measure here, which is not `timeout_s`.

    asyncio fires a timer as soon as the loop's clock is within one resolution tick of the
    deadline, and that tick is `time.get_clock_info("monotonic").resolution`: about 15.6 ms on
    Windows and a fraction of a microsecond on Linux. So a 50 ms wait legitimately returns
    after 34 ms on one runner and after very nearly 50 ms on the next, and a floor typed as a
    constant is a floor that passes on the machine it was written on. This one is the
    platform's own answer, and the gap it leaves is still three orders of magnitude wider than
    a wait that was satisfied at once, which is the only thing these tests are telling apart.
    """
    return timeout_s - time.get_clock_info("monotonic").resolution


async def feed(ks: KillSwitch, keys: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """Run the whole of the key thread's loop over `keys`, in a thread as it really runs, and
    then let the event loop run the callbacks it pushed across. `_watch_keys` returns at the
    end of the stream, so a finite string is a reader that always terminates."""
    monkeypatch.setattr(sys, "stdin", io.StringIO(keys))
    await asyncio.to_thread(ks._watch_keys)
    await asyncio.sleep(0)


# ── the key thread ──────────────────────────────────────────────────────────────────


async def test_a_newline_and_a_carriage_return_both_count_as_enter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A terminal hands over one or the other depending on the platform and the mode it is
    in, and a person who pressed Enter has pressed Enter either way."""
    for key in ("\n", "\r"):
        ks, said = switch()
        await feed(ks, key, monkeypatch)
        assert ks.entered.is_set(), f"{key!r} was not taken for Enter"
        # Enter is not the brake: nothing about it may end the run
        assert ks.presses == 0
        assert not ks.abort.is_set()
        assert not ks.pressed.is_set()
        assert said == []


async def test_the_reader_keeps_reading_after_a_q(monkeypatch: pytest.MonkeyPatch) -> None:
    """The reader used to return at the first `q`, which was enough while the only keystroke
    that meant anything was the one that ended the run. A run that hands the arm to a person
    waits for Enter again in its teardown, on every run somebody already stopped, so a reader
    that stopped at the first `q` left that wait with nobody listening for the answer."""
    ks, said = switch()
    await feed(ks, "q\nq", monkeypatch)
    assert ks.presses == 2, "the reader stopped at the first 'q'"
    assert ks.entered.is_set(), "the Enter between the two was lost"
    assert len(said) == 2


async def test_q_fires_the_switch_and_an_ordinary_character_does_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ks, said = switch()
    await feed(ks, "q", monkeypatch)
    assert ks.presses == 1
    assert ks.abort.is_set()
    assert ks.pressed.is_set()
    assert not ks.entered.is_set()
    assert len(said) == 1 and "'q' pressed" in said[0]

    quiet, quiet_said = switch()
    await feed(quiet, "x", monkeypatch)
    assert quiet.presses == 0
    assert not quiet.abort.is_set()
    assert not quiet.pressed.is_set()
    assert not quiet.entered.is_set()
    assert quiet_said == []


# ── the wait ────────────────────────────────────────────────────────────────────────


async def test_enter_during_the_wait_is_what_ends_it() -> None:
    ks, _ = switch()
    asyncio.get_running_loop().call_later(0.01, ks.entered.set)
    # True is already the proof that Enter ended it rather than the clock: a wait that sat out
    # its timeout returns False. A wall-clock upper bound here would only add a way for a
    # loaded CI runner to fail a test about a keystroke.
    assert await ks.wait_for_enter(timeout_s=0.5) is True


async def test_a_press_from_before_the_wait_does_not_satisfy_it() -> None:
    """Somebody leaning on Enter while the arm was still moving must not answer a question
    that had not been asked yet, so the waiter clears the flag on the way in. Without that,
    the run would take a stray keystroke from a minute ago for the operator saying they are
    holding the thing in the gripper, and open it on an empty hand."""
    ks, _ = switch()
    ks.entered.set()
    started = time.perf_counter()
    assert await ks.wait_for_enter(timeout_s=0.05) is False
    assert time.perf_counter() - started >= waited(0.05), "a stale Enter ended the wait"
    assert not ks.entered.is_set()


async def test_the_wait_ends_on_its_timeout() -> None:
    """Which is also what a wait on a machine with no key thread does, and why a caller who
    needs a real answer checks for a terminal before asking for one."""
    ks, _ = switch()
    started = time.perf_counter()
    assert await ks.wait_for_enter(timeout_s=0.05) is False
    elapsed = time.perf_counter() - started
    assert waited(0.05) <= elapsed < 0.9, elapsed


async def test_until_abort_ends_the_wait_on_a_ctrl_c() -> None:
    """The wait before the first turn: the abort flag is clear when it starts, so watching it
    is how Ctrl-C gets out of a wait for somebody who has walked away."""
    ks, _ = switch()
    asyncio.get_running_loop().call_later(0.01, ks.abort.set)
    started = time.perf_counter()
    assert await ks.wait_for_enter(timeout_s=0.5) is False
    assert time.perf_counter() - started < 0.4, "the abort did not end the wait"

    already, _ = switch()
    already.abort.set()
    started = time.perf_counter()
    assert await already.wait_for_enter(timeout_s=0.5) is False
    assert time.perf_counter() - started < 0.2, "an abort already set did not end the wait"


async def test_the_hand_back_wait_ignores_a_stale_abort_but_not_a_fresh_press() -> None:
    """The distinction the end-of-run hand-back is built on. On every run a person ended with
    Ctrl-C the abort flag is already set by the time the arm is handed back, and a wait that
    watched it would skip the question and fold the arm with the pencil still in the gripper.
    So that one watches `pressed`, which the waiter clears on the way in: the stale flag is
    ignored and a second Ctrl-C, from somebody who has decided they want out, still ends it."""
    ks, _ = switch()
    ks.abort.set()
    ks.pressed.set()  # and the press that set it, equally stale
    started = time.perf_counter()
    assert await ks.wait_for_enter(timeout_s=0.05, until_abort=False) is False
    assert time.perf_counter() - started >= waited(0.05), "a stale abort ended the hand-back wait"

    # the same switch, with the abort still set: a fresh press is what gets out of it
    asyncio.get_running_loop().call_later(0.01, ks._fire, "Ctrl-C")
    started = time.perf_counter()
    assert await ks.wait_for_enter(timeout_s=0.5, until_abort=False) is False
    assert time.perf_counter() - started < 0.4, "a fresh Ctrl-C did not end the hand-back wait"
    assert ks.presses == 1


async def test_the_waiter_cancels_its_own_tasks() -> None:
    """Every wait starts two tasks and only one of them can win, so the loser is cancelled and
    awaited in a `finally`. A waiter that dropped them would leave one pending task per wait
    for the loop to garbage collect, and `Task was destroyed but it is pending` on the console
    of a person who had just been asked to take hold of the arm."""
    loop = asyncio.get_running_loop()
    complaints: list[dict[str, Any]] = []
    loop.set_exception_handler(lambda _loop, context: complaints.append(context))
    try:
        ks, _ = switch()
        before = asyncio.all_tasks()

        assert await ks.wait_for_enter(timeout_s=0.02) is False  # the timeout: neither won
        loop.call_later(0.01, ks.entered.set)
        assert await ks.wait_for_enter(timeout_s=0.5) is True  # Enter won, the abort waiter lost
        ks.abort.set()
        assert await ks.wait_for_enter(timeout_s=0.5) is False  # the abort won, Enter lost

        gc.collect()  # a dropped task complains from its finaliser, so make it run
        await asyncio.sleep(0)
        assert asyncio.all_tasks() - before == set(), "the waiter left a task behind"
        assert complaints == []
    finally:
        loop.set_exception_handler(None)


# ── a wait nothing can ever answer ──────────────────────────────────────────────────────


async def test_stdin_running_out_ends_a_wait_that_has_no_clock_on_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The placement wait has no timeout on purpose, so somebody can go and find a pencil.

    That makes the reader the only thing that can ever end it, and a reader that reaches the
    end of its input is a reader that never will. Without this the run waited for ever with
    the arm limp: a closed terminal, or a Ctrl-D at the wrong moment, and quackd is holding
    nothing and asking nobody."""
    ks, _ = switch()
    await feed(ks, "", monkeypatch)
    assert ks.keys_ended.is_set()
    assert await asyncio.wait_for(ks.wait_for_enter(timeout_s=None), timeout=2) is False


async def test_a_run_with_no_reader_at_all_does_not_wait_for_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`install` starts the key thread only where stdin is a terminal, and the caller that
    waits checks for a terminal of its own before it asks. Those are two checks, made at
    different moments by different code, and the cost of them disagreeing is the same forever
    wait. So the switch says outright that nothing is reading."""
    monkeypatch.setattr(sys, "stdin", io.StringIO("this is not a terminal"))
    ks = KillSwitch(asyncio.Event())
    ks.install()
    try:
        assert ks._thread is None, "no terminal, so no reader"
        assert ks.keys_ended.is_set(), "and it says so rather than letting a wait hang"
        assert await asyncio.wait_for(ks.wait_for_enter(timeout_s=None), timeout=2) is False
    finally:
        ks.uninstall()


# ── a question whose Enter moves or drops an arm ────────────────────────────────────


@pytest.mark.parametrize("deliberate", [False, True])
async def test_a_press_during_a_wait_is_never_an_enter(
    monkeypatch: pytest.MonkeyPatch, deliberate: bool
) -> None:
    """A terminal in its usual mode hands `q` to the key thread only with the Enter that sends
    its line, so a press and an Enter arrive together. The wait used to answer True whenever
    both were in by the time it looked: `q`, the key that stops a run, read as a yes, at the
    question whose yes drops the arm. A press is never an Enter, at any wait."""
    monkeypatch.setattr("quackd.safety.TYPED_AHEAD_S", 0.0)
    ks, _ = switch()

    def q_and_its_enter() -> None:
        ks._fire("'q' pressed")
        ks._heard_enter("q")

    asyncio.get_running_loop().call_later(0.02, q_and_its_enter)
    got = await ks.wait_for_enter(timeout_s=2.0, until_abort=False, deliberate=deliberate)
    assert got is False, "q was taken for Enter"


@pytest.mark.parametrize(("typed", "yes"), [("", True), ("n", False), ("no", False), ("y", False)])
async def test_a_deliberate_question_takes_only_a_bare_enter(
    monkeypatch: pytest.MonkeyPatch, typed: str, yes: bool
) -> None:
    """Every line ends in Enter, so the key thread used to take `n` and Enter for Enter, and an
    arm with no rest pose was connected to and dropped on a person saying no. A question whose
    Enter moves or drops an arm takes only a bare one: anything typed on the line is a no, and
    says so (`declined`), `y` too, since the question asks for Enter."""
    monkeypatch.setattr("quackd.safety.TYPED_AHEAD_S", 0.0)
    ks, _ = switch()
    asyncio.get_running_loop().call_later(0.02, ks._heard_enter, typed)
    got = await ks.wait_for_enter(timeout_s=2.0, until_abort=False, deliberate=True)
    assert got is yes, typed
    assert ks.declined is (not yes), typed


async def test_an_ordinary_wait_still_takes_any_line_as_enter() -> None:
    """`--by-hand`'s waits are about placing an arm, not dropping one, and a line typed there is
    still the Enter it always was."""
    ks, _ = switch()
    asyncio.get_running_loop().call_later(0.02, ks._heard_enter, "placed it")
    assert await ks.wait_for_enter(timeout_s=2.0) is True


async def test_an_enter_typed_ahead_of_a_deliberate_question_is_passed_by(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The key thread reads whatever is already in the terminal as it starts, and an Enter typed
    while a command was starting used to answer the first question it asked. One read within
    `TYPED_AHEAD_S` of the question being put is passed by, and the wait goes on to the next."""
    monkeypatch.setattr("quackd.safety.TYPED_AHEAD_S", 0.2)
    ks, _ = switch()
    ks._heard_enter("")  # already in the terminal when the question was put
    loop = asyncio.get_running_loop()
    loop.call_later(0.05, ks._heard_enter, "")  # still too soon to have read it
    loop.call_later(0.4, ks._heard_enter, "")  # the answer
    started = time.perf_counter()
    assert await ks.wait_for_enter(timeout_s=3.0, until_abort=False, deliberate=True) is True
    assert time.perf_counter() - started >= waited(0.4), "an Enter typed ahead answered it"

    quiet, _ = switch()
    quiet._heard_enter("")
    assert await quiet.wait_for_enter(timeout_s=0.4, until_abort=False, deliberate=True) is False
    assert quiet.declined is False, "a question nobody answered was filed as a no"


async def test_the_key_thread_records_each_line_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """What was typed on each line is what a deliberate question reads, and a terminal that
    ends its lines with a carriage return and a newline has sent one Enter, not two: the
    second, an empty line, would otherwise be read as the bare Enter after an `n`."""
    ks, _ = switch()
    await feed(ks, "n\r\nyes\n\n", monkeypatch)
    assert [text for _at, text in ks._enters] == ["n", "yes", ""]
    assert ks.presses == 0
