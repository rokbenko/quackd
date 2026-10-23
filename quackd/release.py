"""Letting go of an arm: fold it to its rest pose first, and drop it only when a person says.

Taking torque off a LeRobot arm where it stands drops it: nothing but torque holds it up, and
an SO-101 has no brake. So releasing it means putting it down first. `quackd robot release`
folds the arm to its recorded rest pose and lets go there, and the end of every run already
did the same. This module is what both do when that fold misses, in one place so the two
cannot come to say different things to a person standing at the same arm.

A fold misses when something is in its way, a hand, the desk, an object the arm closed on, or
when a joint stalls. Rok's rule for that (ADR-0058) has two questions, in this order:

1. Ask the person to clear whatever is in its way and press Enter, and fold once more.
2. Only if that fold misses too, tell them that Enter now takes torque off where the arm
   stands and that it will drop, so to hold it first, and release on Enter.

Neither question has a default that drops anything. A wait that runs out, a Ctrl-C, a `q`,
or a terminal with nothing to read leaves torque on and the arm holding itself up, which is
what the close does with an arm away from its pose. Only Enter at the second question lets go
of an arm that is not down.

Nothing here prints. Every caller narrates through its own hooks (`event` for the record,
`note` for the log, `asked` for a question a person was put) and says the returned line
(`Settled.said`) its own way: the run's teardown to the person at the terminal, `quackd robot
release` through its own result table."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from typing import Any, Literal, Protocol

from quackd.adapters.base import HandResult, RestResult, let_go_if_any


class Person(Protocol):
    """Whoever stands at the arm, as the run's `HandOff` sees them: told things, waited for.

    Two more members are read with `getattr`, so that a stand-in that has neither is still a
    person: `ended`, the word the CLI's terminal leaves after each wait (`enter`, `kill
    switch`, `no keys`, `stopped`, `timeout`), and `stopped()`, which returns when a fresh
    Ctrl-C or `q` is pressed, and is what lets a person stop a fold they asked for."""

    def say(self, text: str) -> None: ...

    async def wait(
        self,
        text: str,
        *,
        timeout_s: float | None = None,
        until_abort: bool = True,
        deliberate: bool = False,
    ) -> bool: ...


Hook = Callable[..., None]


def _nothing(*_args: Any, **_kwargs: Any) -> None:
    return None


class FoldStopped(Exception):
    """A fold the person stopped with a fresh Ctrl-C or `q`, while it was moving."""


SettledHow = Literal["folded", "released", "kept", "interrupted"]


@dataclass(frozen=True)
class Settled:
    """How a missed fold was settled, as a value the caller narrates and turns into an exit.

    `folded`: the second fold reached the pose, and the arm is let go of there by whoever
    closes it. `released`: Enter at the second question, and `released` says what the release
    did. `kept`: torque stays on, for the reason given: a question nobody answered with Enter,
    a fold the person stopped, an arm that stopped answering, a release interrupted before it
    went out. `interrupted`: a release interrupted after it went out, so the arm may be limp in
    part."""

    how: SettledHow
    reason: str
    """The record's words for why it ended this way: one of the reasons below, or the
    release's own."""
    asked: Literal["clear", "drop"] | None = None
    """The last question put: `clear` (fold once more) or `drop` (release where it stands)."""
    fold: RestResult | None = None
    """The second fold, where one was made."""
    released: HandResult | None = None
    """What the release did, where Enter at the second question got as far as one."""
    said: str | None = None
    """The line for the person, where there is one beyond the note already made: the release's
    outcome, a fold they stopped, an arm that stopped answering. None where the note is all
    there is to say, a question that ended without Enter."""


CLEAR_THE_WAY_S = 60.0
"""How long the first question waits for Enter. Bounded for the reason every wait at an arm
is: a run must end when the room is empty, and nothing is lost by not answering, since the arm
is left holding itself up exactly as a close away from its pose leaves it."""

ENTER_DROPS_S = 60.0
"""How long the second question waits for Enter, for `CLEAR_THE_WAY_S`'s reason. Not longer:
what the wait costs is an arm holding itself up against a pose it twice could not reach."""

HANDS_AWAY_S = 3.0
"""How long `quackd robot release` waits between the connect and its first fold, for the hands
that supported the arm through the connect to come away. Its warning asks for both, the support
and then the hands away, and the fold used to start a few milliseconds after the connect, under
the hands still holding the arm. Three seconds is a deliberate letting go and no longer; a
Ctrl-C or `q` during them stops the command before anything has moved."""

FOLDING_IN = (
    "folding {name} to its rest pose in {seconds:.0f} s: take your hands away from it now. "
    "Ctrl-C or q stops it before it moves"
)
"""Said as `HANDS_AWAY_S` begins, once the arm is connected and holding itself up again."""

CLEAR_THE_WAY = (
    "the arm did not reach its rest pose ({why}), so it is holding itself up where it stopped. "
    "Clear whatever is in its way and keep your hands out of its path, then press Enter and it "
    "tries the fold once more. Leave it, and after {seconds:.0f} s it stays that way"
)
"""The first question, after a fold that missed with the arm answering. It asks for the one
thing that makes a second fold worth making, a clear path, and promises nothing about letting
go: an arm whose second fold arrives is let go of at the pose, and one whose second fold misses
is asked about again."""

WILL_DROP = (
    "takes torque off where it stands, and the arm will drop: hold it first, or it can be damaged"
)
"""What Enter does at either question that drops the arm, in one copy, so the two read as the
same warning: the release goes out wherever the arm is, nothing then holds it up, and an arm
that falls onto the desk can break itself or what it lands on."""

DROP_AFTER_TWO_FOLDS = (
    "the arm did not reach its rest pose the second time either ({why}), so it is holding itself "
    f"up where it stopped. Pressing Enter now {WILL_DROP}. Leave it, and after {{seconds:.0f}} s "
    "torque stays on"
)
"""The second question, only after the fold the first one asked for missed too. It says the
drop before anything else a person could act on, because the person pressing Enter here is the
one whose hands decide where the arm lands."""

DROP_UNFOLDED = (
    "{name} has no rest pose recorded, so there is nothing to fold it to. Pressing Enter "
    f"connects to it and {WILL_DROP}. Leave it, and after {{seconds:.0f}} s nothing is connected"
)
"""`quackd robot release`'s only question for an arm with no rest pose, asked before it
connects, not after. With no pose to fold it to, letting go of it where it stands is all the
command can do, and connecting is where the arm first goes limp, for the moment LeRobot takes to
configure its motors: a no put after the connect would come after that moment, over an arm that
had already sagged. Asked first, a no leaves the arm exactly as it was."""

DROP_UNPOSED = (
    "{name} has no rest pose recorded, so torque was kept on rather than let go of where the arm "
    f"stands. Pressing Enter now {WILL_DROP}. Leave it, and after {{seconds:.0f}} s torque stays on"
)
"""`quackd doctor`'s one question, put at a terminal once it has read an arm with no rest pose
and before it lets go of the bus. A real arm's close keeps such an arm powered rather than let
it go where it stands (ADR-0058), and at first contact the person usually wants it limp, to fold
it by hand and record its pose: Enter drops it into their hands, and anything else keeps it as
it is. With no terminal nothing is asked, and torque stays on."""

REFOLDED = (
    "the arm reached its rest pose this time, so it is let go of there when the run disconnects "
    "from it, as at the end of every run that folds"
)
"""Said after the second fold arrived, in a run's teardown, whose close then lets go at the pose
with nothing more to say."""

FOLD_STOPPED = (
    "the fold was stopped where it was, and nothing was released: torque is as the fold left it"
)
"""Said when a Ctrl-C or `q` stopped a fold: one a person asked for, the second fold or the
release's, `doctor`'s, or the fold at the end of a run that ended by itself. Nothing was
released, which is all this knows: whether the arm is holding itself up is the close's line to
say, from its own read, and it follows this one, as after `RELEASE_NOT_SENT`."""

LIMP_AFTER_A_MISS = (
    "the arm did not reach its rest pose ({why}), and its last read finds torque off on {limp}: "
    "that much of it is not holding itself up and may already have dropped, and a fold cannot "
    "move a joint with no torque, so nothing is asked. Hold the arm, put it down, and cut its "
    "power before it is powered again"
)
"""Said in place of either question when the fold missed over joints whose own torque read
found them off (`RestResult.limp`): a brown-out under stall current, or a supply cut and back,
leaves servos answering with their torque disabled. "It is holding itself up where it stopped"
would be untrue of them, a second fold would send goals to joints that cannot move, and a
release would let go of whatever still holds while the rest has already gone."""

REFOLD_UNANSWERED = (
    "the arm stopped answering during the second fold ({why}), so quackd cannot tell whether it "
    "is holding itself up and asks nothing more: hold it, and cut its power"
)
"""Said when the second fold ended because the arm stopped answering. Cutting the servo supply
looks exactly like this, and so does a pulled cable in front of servos still holding, so there
is no second question: the release it would offer refuses at its first read for as long as the
arm stays silent, after a person has been kept waiting for it."""

RELEASE_INTERRUPTED = (
    "the release was interrupted while it was going out, so the arm may be limp, all of it "
    "or part of it: hold it as though nothing holds it, and put it down"
)
"""Said when a Ctrl-C lands on the release after Enter, once it has gone out. The person
pressed Enter because they were holding the arm, the release may have reached some motors
and not others, and the only safe reading of that is the limp one."""

RELEASE_NOT_SENT = (
    "the release was interrupted before anything was sent, so torque is as the rest move left it"
)
"""Said when a Ctrl-C lands on the release after Enter and before it went out, on the read
the release begins with: the arm's own backend says so (`in_hand` still False). Nothing
reached a motor, which is all this line knows, so it says that and no more.

It used to go on to say the arm "still holds itself up", and nothing read after the
interrupt said so. On the arm the read the Ctrl-C landed on can still be out on the bus when
the close comes straight after it, and then the close's own read is refused and its line
says quackd cannot tell whether the arm is holding itself up: two lines in a row saying
opposite things to a person deciding whether to let go. Whether the arm holds itself up is
the close's line to say, from its own read, and it follows this one."""

TO_FOLD_AGAIN = "Enter, to fold it once more"
WAIT_INTERRUPTED = "interrupted while waiting"
NO_KEYS = "no key could be read"
NOBODY = "nobody pressed Enter"
FOLD_INTERRUPTED = "interrupted during the fold"
FOLD_UNANSWERED = "the arm did not answer the second fold"
NOT_SENT = "interrupted before the release was sent"
DURING_RELEASE = "interrupted during the release"
DECLINED = "answered no"
TORQUE_READS_OFF = "torque reads off"
"""The record's reasons, one per way a missed fold is settled, as the `release` event's
`reason` and `Settled.reason` carry them. `answered no` is a line with something typed on it,
`n` the commonest: only a bare Enter is a yes to a question that moves or drops the arm."""

ENTER = "enter"
"""What `ask` answers when Enter came. Every other answer is one of the record's reasons."""

_KEPT_NOTES = {
    WAIT_INTERRUPTED: "torque stays on, and the arm holds itself up where it stopped",
    NO_KEYS: "no key could be read, so torque stays on and the arm holds itself up",
    NOBODY: "nobody pressed Enter, so torque stays on and the arm holds itself up",
    DECLINED: "answered no, so torque stays on and the arm holds itself up",
}
"""The log's line for each way a question ends without Enter. Said of an arm holding itself up
where its fold stopped, which every question about a missed fold is put over."""


async def ask(
    person: Person,
    question: str,
    seconds: float,
    *,
    asked: Hook = _nothing,
) -> str:
    """Put one question and wait for Enter: `ENTER`, or the record's reason it ended without.

    Only a bare Enter, read after the question was put, is a yes (`deliberate`): every question
    here moves the arm or drops it, so a line with anything typed on it is a no, `n` the
    commonest, and an Enter pressed before anybody could have read the question is passed by.
    A press of the kill switch is never a yes either, though `q` arrives with an Enter. The wait
    watches a fresh key press rather than the abort flag (`until_abort=False`): the flag is set
    on every run a person ended, which are the runs most likely to have missed their fold, and
    watching it would skip the question on exactly those. A Ctrl-C that reaches here as an
    interrupt rather than through the switch, which is every one after the first, ends the wait
    too, and is caught: it means "skip this", and whatever comes after the question, a close
    above all, still has to happen. Never raises.

    `asked(question, agreed)` is called once the wait answers, and not when an interrupt ended
    it, so the record holds the exchanges a person actually had."""
    try:
        agreed = await person.wait(question, timeout_s=seconds, until_abort=False, deliberate=True)
    except (asyncio.CancelledError, KeyboardInterrupt):
        return WAIT_INTERRUPTED
    with contextlib.suppress(Exception):
        asked(question, agreed)
    if agreed:
        return ENTER
    # why the wait ended, where the person can say: a first Ctrl-C ends it without raising, and
    # "nobody pressed Enter" is not what happened
    ended = getattr(person, "ended", None)
    if ended == "kill switch":
        return WAIT_INTERRUPTED
    if ended == "no keys":
        return NO_KEYS
    if ended == "declined":
        return DECLINED
    return NOBODY


def kept_at_a_wait(why: str, *, event: Hook = _nothing, note: Hook = _nothing) -> None:
    """Record and note a question that ended without Enter, over an arm holding itself up."""
    event(stage="kept", reason=why)
    note(_KEPT_NOTES.get(why, _KEPT_NOTES[NOBODY]))


STOPPED_FOLD_S = 2.0
"""How long a fold already told to stop is still waited for when the call that stopped it is
cancelled as well. A cancelled fold raises at its next await, a bus call's or a tick's, so in
practice it is gone at once; the bound is for one that would not go, and it keeps a cancellation
from being held up for ever by something this module did not write."""


async def cancel_and_wait(*tasks: asyncio.Future[Any]) -> None:
    """Cancel `tasks` and wait until every one has finished, whatever each ends with.

    `asyncio.wait` rather than awaiting each: it raises nothing a task raised. A cancellation of
    the task doing the waiting, a later Ctrl-C, does not end the wait at once: the tasks have
    been told to stop, and a fold left to finish on its own would go on sending goals behind
    whatever comes next, so they are waited out a little longer (`STOPPED_FOLD_S`) before the
    cancellation goes on up. What a task raised is read, so one that failed as it was stopped is
    not reported later as an exception nobody retrieved."""
    for task in tasks:
        task.cancel()
    try:
        await asyncio.wait(tasks)
    except asyncio.CancelledError:
        with contextlib.suppress(asyncio.CancelledError):
            await asyncio.wait(tasks, timeout=STOPPED_FOLD_S)
        raise
    for task in tasks:
        if not task.cancelled():
            task.exception()


async def fold_unless_stopped(
    fold: Callable[[], Awaitable[RestResult | None]],
    stopped: Callable[[], Awaitable[Any]] | None,
) -> RestResult | None:
    """Fold, unless the person stops it with a fresh Ctrl-C or `q` first: then `FoldStopped`.

    The fold is the arm's own rest move, which runs until it arrives, stalls or runs out of
    time, and never looks at the kill switch: in a teardown the abort flag is already set and
    would end the move that puts the arm down. A fold a person asked for is different. They
    are standing next to it, and a press is them saying stop, so the fold runs in its own task
    raced against `stopped`, and the press cancels it at its next await. A goal already sent is
    one step cap from where the arm was, so that is as far as it goes on.

    The fold task never outlives this call. If the task awaiting it is cancelled, which is what
    a later Ctrl-C does once the switch has handed the signal back, the fold is cancelled and
    waited for before the cancellation goes on up: a fold left running would go on sending goals
    while the close decides whether to let go. A `stopped` that fails is no stop, and the fold
    then goes on alone. A person with no `stopped` (a stand-in, a terminal that cannot say) gets
    the fold awaited directly."""
    if stopped is None:
        return await fold()
    # the watch first: a person's `stopped` counts the presses so far as it is called, and a
    # press made as the fold starts has to be one it has not counted yet
    watching = asyncio.ensure_future(stopped())
    folding = asyncio.ensure_future(fold())
    try:
        await asyncio.wait({folding, watching}, return_when=asyncio.FIRST_COMPLETED)
    except BaseException:
        # this call was cancelled or interrupted while both ran: neither may outlive it
        await cancel_and_wait(folding, watching)
        raise
    if folding.done():
        # the fold ended first, or both at once: it is what happened, whatever was pressed
        await cancel_and_wait(watching)
        return folding.result()
    if not watching.cancelled() and watching.exception() is None:
        await cancel_and_wait(folding)
        raise FoldStopped
    # a `stopped` that failed is no stop, so the fold goes on alone. Awaited directly, so a
    # cancellation of this call is passed on to the fold and waited out with it
    return await folding


async def release_where_it_stands(
    transport: Any, *, event: Hook = _nothing, note: Hook = _nothing
) -> Settled:
    """Take torque off wherever the arm stands, into the hands of a person who pressed Enter.

    The second door (`let_go_if_any(..., anywhere=True)`), read back as it always is, and a
    Ctrl-C landing on it told apart by the arm's own backend: before the release went out, on the
    read it begins with, nothing was sent (`in_hand` still False), and after it part of the arm
    may already be limp. A body that does not say gets the limp reading, which is the one that
    never drops an arm. Never raises."""
    try:
        released = await let_go_if_any(transport, anywhere=True)
    except (asyncio.CancelledError, KeyboardInterrupt):
        if getattr(transport, "in_hand", None) is False:
            event(stage="kept", reason=NOT_SENT)
            note(RELEASE_NOT_SENT)
            return Settled("kept", NOT_SENT, said=RELEASE_NOT_SENT)
        event(stage="interrupted", reason=DURING_RELEASE)
        note(RELEASE_INTERRUPTED)
        return Settled("interrupted", DURING_RELEASE, said=RELEASE_INTERRUPTED)
    event(
        stage="released",
        how=released.how,
        reason=released.reason,
        joints=released.joints,
        torque_on=list(released.torque_on) if released.torque_on is not None else None,
    )
    # Said to the person rather than only logged: they are holding the arm and act on this
    # line, and a run with its log off prints no note at all
    if released.ok and released.torque_on:
        said = (
            f"{released.reason}: the arm is in your hands, so put it down before you let go of "
            "it, and cut its power to let go of what still holds"
        )
    elif released.ok:
        said = (
            f"{released.reason}: the arm is in your hands, so put it down before you let go of it"
        )
    elif released.torque_on:
        # read back: every motor said torque on, so the arm holding itself up is a reading
        note(f"torque was not released: {released.reason}")
        said = (
            f"torque was not released ({released.reason}): the arm is still holding itself up, "
            "so keep hold of it and cut its power"
        )
    else:
        # nothing was read back, so nothing is known about torque either way: an arm whose
        # supply was cut reads exactly like one whose cable came out in front of live servos
        note(f"torque was not released: {released.reason}")
        said = (
            f"torque was not released ({released.reason}), and quackd cannot tell whether "
            "torque is on: keep holding the arm, and cut its power"
        )
    return Settled("released", released.reason, released=released, said=said)


def _limp(missed: RestResult, *, event: Hook, note: Hook) -> Settled:
    """`kept`, over a fold that missed with joints whose torque read found them off."""
    said = LIMP_AFTER_A_MISS.format(why=missed.reason, limp=", ".join(missed.limp))
    event(stage="kept", reason=TORQUE_READS_OFF)
    note(said)
    return Settled("kept", TORQUE_READS_OFF, said=said)


async def settle_missed_fold(
    transport: Any,
    person: Person,
    missed: RestResult,
    *,
    fold: Callable[[], Awaitable[RestResult | None]],
    stop: Callable[[], Awaitable[Any]],
    event: Hook = _nothing,
    note: Hook = _nothing,
    asked: Hook = _nothing,
) -> Settled:
    """The two questions over an arm whose fold missed and which answered while it missed.

    The caller has already checked that this is that arm (a pose recorded, the fold missed, the
    arm answered, nobody holding it). `fold` is the caller's own narrated rest move, `stop` its
    own stop, so both reach the record the way every other move does. Never raises: every
    interrupt it can meet is caught where it lands and settled as `kept` or `interrupted`, so
    the close that comes after it always happens.

    1. `CLEAR_THE_WAY`. Anything but Enter: `kept`.
    2. The second fold, which a fresh press stops. Stopped or interrupted: the caller's stop is
       sent, and `kept`. Arrived: `folded`. Missed with the arm silent: `kept`, and nothing more
       is asked, because nothing it says about itself can be believed.
    3. `DROP_AFTER_TWO_FOLDS`. Anything but Enter: `kept`. Enter: the release where it stands.

    Neither question is put over joints whose own torque read found them off after a fold
    (`RestResult.limp`): `LIMP_AFTER_A_MISS` says so instead, and `kept`."""
    if missed.limp:
        return _limp(missed, event=event, note=note)
    first = CLEAR_THE_WAY.format(why=missed.reason, seconds=CLEAR_THE_WAY_S)
    answer = await ask(person, first, CLEAR_THE_WAY_S, asked=asked)
    if answer != ENTER:
        kept_at_a_wait(answer, event=event, note=note)
        return Settled("kept", answer, asked="clear")
    event(stage="refold", reason=TO_FOLD_AGAIN)
    interrupted = False
    again: RestResult | None
    try:
        again = await fold_unless_stopped(fold, getattr(person, "stopped", None))
    except FoldStopped:
        interrupted, again = True, None
    except (asyncio.CancelledError, KeyboardInterrupt):
        interrupted, again = True, None
    except Exception as e:
        # a fold that failed in a way nothing here can read: settled as one over an arm that
        # stopped answering, with nothing more asked, since a drop offered over an arm nobody
        # can read is offered blind
        again = RestResult("refused", f"{type(e).__name__}: {e}", answered=False)
    if interrupted:
        event(stage="kept", reason=FOLD_INTERRUPTED)
        note(FOLD_STOPPED)
        with contextlib.suppress(Exception, asyncio.CancelledError, KeyboardInterrupt):
            # The fold was cancelled at an await, and a goal it sent may still be on its way:
            # the stop holds the arm where it is now. A bus still busy with the call the cancel
            # landed on refuses it, and the arm then holds that last goal, a step cap away. A
            # second press landing here, as a person pressing twice does, is caught as well:
            # the close that comes after this has to happen, and it keeps the torque on
            await stop()
        return Settled("kept", FOLD_INTERRUPTED, asked="clear", said=FOLD_STOPPED)
    if again is None or not again.recorded:
        # nothing to fold to after all, which a caller that checked cannot reach: the arm is
        # left as the first fold left it, holding, and nothing more is asked
        reason = again.reason if again is not None else "there is no rest pose to fold to"
        event(stage="kept", reason=reason)
        return Settled("kept", reason, asked="clear", fold=again)
    if again.reached:
        event(stage="folded", how=again.how, reason=again.reason)
        return Settled("folded", again.reason, asked="clear", fold=again, said=REFOLDED)
    if again.limp:
        return replace(_limp(again, event=event, note=note), asked="clear", fold=again)
    if not again.answered:
        said = REFOLD_UNANSWERED.format(why=again.reason)
        event(stage="kept", reason=FOLD_UNANSWERED)
        note(said)
        return Settled("kept", FOLD_UNANSWERED, asked="clear", fold=again, said=said)
    second = DROP_AFTER_TWO_FOLDS.format(why=again.reason, seconds=ENTER_DROPS_S)
    answer = await ask(person, second, ENTER_DROPS_S, asked=asked)
    if answer != ENTER:
        kept_at_a_wait(answer, event=event, note=note)
        return Settled("kept", answer, asked="drop", fold=again)
    settled = await release_where_it_stands(transport, event=event, note=note)
    return replace(settled, asked="drop", fold=again)
