"""The layer that does not trust the LLM.

Upstream's `robotd` is the safety authority for the robot's body. This module is the
authority for the *conversation*: every verb call — from the agent loop or an MCP client —
passes through `Executor`, which checks the `.glot` allowlist, asks a human when the
contract says so, counts budgets, and can run with the transport disconnected (`dry_run`).
`Heartbeat` and the kill switch make "the LLM stalled" and "the human panicked" both end in
a `stop` intent.
"""

from __future__ import annotations

import asyncio
import atexit
import contextlib
import contextvars
import copy
import dataclasses
import enum
import functools
import math
import os
import queue
import signal
import sys
import threading
import time
from collections import deque
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any, Literal, NamedTuple, NoReturn, cast

from pydantic import ValidationError

from roboglot.answers import answered, confirm_question
from roboglot.glotfile.schema import (
    DEFAULT_POLICY_TOTAL_S,
    POLICY_VERBS,
    Budgets,
    GlotFrontmatter,
)
from roboglot.keys import ConsoleKeys, console_keys, in_the_background
from roboglot.log import (
    A_PERSON,
    A_PIPE,
    EventLog,
    LoggedTransport,
    Tally,
    a_person_was_asked,
    asked_on,
    counting,
    who_answered,
)
from roboglot.perception.base import Detector, detect_times
from roboglot.space.places import PlaceBook, PlaceStore
from roboglot.transport.base import (
    Ack,
    CameraFrame,
    Intent,
    RobotState,
    RobotTransport,
    TransportError,
    spatial_of,
)
from roboglot.verbs.core import CORE, HOLD_TTL_S
from roboglot.verbs.registry import (
    STOP_ENDED,
    STOPPED,
    Verb,
    VerbContext,
    VerbNotFound,
    VerbRegistry,
    VerbResult,
)
from roboglot.verdict import BEFORE_VERDICT, MOVES_THE_BODY, Verdict

if TYPE_CHECKING:
    from roboglot.adapters.manifest import RobotManifest

CANCELLED_BY_ABORT = "abort"
CANCELLED_BY_TIMEOUT = "timeout"
CANCELLED_FROM_OUTSIDE = "cancel"
CANCELLED_BY_PAUSE = "pause"
CANCELLED_BY_PREEMPT = "preempt"
PAUSE_ENDED = "pause"
"""`ended_by` in the result of a verb a granted pause ended (`Executor._ended_by_pause`), which
`abort_when`'s count of failures in a row passes over."""
"""Why the executor cancelled a running verb, as the message of the `CancelledError` the verb is
handed (`Executor._execute`): the abort it races, a pause granted to a robot met, a newer
command (`Executor.preempt`), the verb's own timeout, or the caller going away. A verb that keeps
a record of its own names its ending by it."""

Source = Literal["agent", "mcp", "cli", "person", "decision", "peer"]
"""Who asked for this verb. `decision` is the discrete stepper answering a turn the pilot LLM
never saw, whichever decision LLM was behind it: which one that was is recorded once for the
run (`run_start.decision_llm`) rather than on every verb, so swapping Jev for a server you run
yourself changes nothing here. The log already prints `from <source>` for anything that is not
the agent, so the record says who chose a verb without a renderer knowing the word. `peer` is
a robot met over Robot Passport, through the courtesy handler and nothing else
(`roboglot.passport_courtesy`), and the `peer` gate holds it to the two verbs a courtesy runs.
`person` is somebody at `roboglot drive`'s keyboard naming one call, and `cli` a line drive read
off a pipe or a file (`PERSONS`)."""

PERSONS = frozenset({"person", "cli"})
"""The sources whose own call clears the verdict gate by itself. Somebody who named one call of
this body's, at the keyboard or in a file they piped in, has made the judgement the gate waits
for, for that call and the verbs it runs, and for nothing after it: `verdict` is never written,
so the pilot's next call is gated as it always was. A composite's calls inherit its source, so a
nested call from one of these can only come from such a person's own command. Only
`roboglot/drive` passes either, and a test holds every other caller to that."""

PEER_VERBS = ("stop", "say")
"""The verbs a peer's courtesy may run: `stop`, which a granted pause begins with, and `say`,
which a body identifies itself by sound with. A peer never asks for either by name; the handler
runs them for it, and the `peer` gate refuses anything else a peer might reach."""

WHILE_PAUSED = frozenset(
    {
        "stop",
        "observe",
        "report_state",
        "say",
        "quack",
        "introspect",
        "map",
        "mark_place",
        "forget_place",
    }
)
"""What still runs while a pause this robot granted holds: the brake, and the verbs Roboglot knows
move no part of the body. It is not `BEFORE_VERDICT`, which is what a pilot needs before it
judges: `gaze`, `look` and `watch` turn a head and `express` moves antennas, and a robot that
answered that it is holding still holds still. A verb this set does not name waits, a sensor a
third party marked `read_only` among them, since a flag on a verb Roboglot has never seen is no
promise that nothing turns, and a learned verb waits whatever it is called."""


class SafetyStop(Exception):
    """Base for every reason the run must end now, regardless of what the LLM wants."""

    outcome = "aborted"
    """The word `verb_end` records. A layer above the executor that ends a verb for a reason
    of its own overrides this, so the log says `PREEMPTED` and not the red `ERROR` that
    means a bug."""


class Preempted(SafetyStop):
    """A verb a newer command ended before it was done, the body stopped after it, or one a
    newer command came in time to stop from starting (`Executor.preempt`). Raised rather than
    returned, so `abort_when`'s count of failures in a row never sees it: moving on to the next
    command is neither the verb's failure nor anybody's."""

    outcome = "preempted"
    """Drive's word for a command it moved on from."""


class BudgetExceeded(SafetyStop):
    pass


class Aborted(SafetyStop):
    """An `abort_when` condition or the kill switch fired."""


class QuestionTakenBack(Aborted):
    """A call whose confirm question the run's own abort took back before anybody answered it.
    It ends as the abort ends any call, and whoever knows why the abort was set says so in the
    run's reason (`AgentLoop`), as for a pilot's doubt taken back the same way."""


class _TimedOut(TimeoutError):
    """A verb's own `timeout_s` ran out, and the executor has sent the stop owed after it, which
    failed for `unstopped`, or None when it did not (`Executor._execute`)."""

    def __init__(self, unstopped: str | None) -> None:
        super().__init__()
        self.unstopped = unstopped


class _HeldOnADryRun(Exception):
    """A method of its transport a verb called on a dry run that is not a read (`DRY_RUN_READS`),
    refused there and recorded (`_Leash`)."""


class VerbNotAllowed(PermissionError):
    """Refused, but the run continues — the LLM is told and may choose differently."""


class VerdictRequired(VerbNotAllowed):
    """A verb that moves the body, before the pilot has said the task fits this body.

    Refused like any other gate, and the run goes on: the pilot is told which tool records a
    verdict, and may record one and try again."""


class ConfirmDenied(PermissionError):
    """A human said no."""


def _invalid(e: ValidationError) -> str:
    return "; ".join(f"{'.'.join(map(str, err['loc']))}: {err['msg']}" for err in e.errors())


@dataclass
class Budget:
    limits: Budgets
    now: Callable[[], float] = time.monotonic
    steps: int = 0
    llm_calls: int = 0
    stepper_calls: int = 0
    """Turns the discrete stepper answered (`roboglot run --decision-mode on`).

    Not a limit and never checked. A stepper turn runs a verb, and the verb charges a step
    like every other one, so `max_steps` already bounds it and a second number in the `.glot`
    would bound nothing the first two do not. This exists so `status()` can say where the
    turns went."""
    started_at: float | None = None
    policy_total_s: float = DEFAULT_POLICY_TOTAL_S
    """How many seconds of policy segments, `pick`'s and `manipulate`'s alike
    (`POLICY_VERBS`), the run may spend: the task file's `policy.total_s`, or the named default
    for a task with none and a session with no task."""
    policy_s: float = 0.0
    """The seconds of segments spent so far, each as its verb said it ran. A segment is one
    step against `max_steps` like any verb; this is the other half of its cost."""
    calls_word: str = "LLM calls"
    """What `status()` calls `llm_calls`. A VLA pilot's run says `turns`, since no LLM is asked in
    it, and `max_llm_calls` holds its turns all the same."""

    def start(self) -> None:
        self.started_at = self.now()

    @property
    def elapsed_s(self) -> float:
        return 0.0 if self.started_at is None else self.now() - self.started_at

    def check(self) -> None:
        if self.steps >= self.limits.max_steps:
            raise BudgetExceeded(f"max_steps ({self.limits.max_steps}) reached")
        if self.llm_calls >= self.limits.max_llm_calls:
            # the key a person raises, by its name, which on a VLA pilot's run holds its turns
            here = (
                ""
                if self.calls_word == Budget.calls_word
                else f", which counts {self.calls_word} here"
            )
            raise BudgetExceeded(f"max_llm_calls ({self.limits.max_llm_calls}) reached{here}")
        self.check_time()

    def check_time(self) -> None:
        if self.elapsed_s > self.limits.max_minutes * 60:
            raise BudgetExceeded(f"max_minutes ({self.limits.max_minutes:g}) exceeded")

    def check_step(self) -> None:
        """Whether a verb may take a step now: the steps and the clock, and never the LLM calls.
        A verb's step ran the whole of `check`, so once the loop had charged the last allowed
        call, the verb that call chose was refused for `max_llm_calls`, billed and for nothing,
        and so was a stepper's turn after it, which charges no call. The calls are read where
        one is about to be charged (`note_llm_call`), and the run ends when it would make one
        more."""
        if self.steps >= self.limits.max_steps:
            raise BudgetExceeded(f"max_steps ({self.limits.max_steps}) reached")
        self.check_time()

    def note_step(self) -> None:
        self.check_step()
        self.steps += 1

    def note_llm_call(self) -> None:
        self.check()
        self.llm_calls += 1

    def note_stepper_call(self) -> None:
        """A turn the stepper answered: no pilot LLM call to charge, and the verb charges the step.

        The clock is the one budget a turn with no pilot LLM call can still hit, so it is the one
        that is checked here."""
        self.check_time()
        self.stepper_calls += 1

    def check_policy(self) -> None:
        """Before a `pick` or a `manipulate`: refuse it once the segments have spent
        `policy_total_s`.
        Checked before each segment rather than inside one, so the last may run past the
        total by at most its own seconds, and never starts with nothing left."""
        if self.policy_s >= self.policy_total_s:
            raise BudgetExceeded(
                f"policy total_s ({self.policy_total_s:g} s of segments) spent: "
                f"{self.policy_s:.1f} s have run"
            )

    def note_policy(self, seconds: float) -> None:
        """After a `pick` or a `manipulate`: charge the seconds its segment ran."""
        self.policy_s += seconds

    def status(self) -> str:
        return (
            f"step {self.steps}/{self.limits.max_steps}, "
            f"{self.calls_word} {self.llm_calls}/{self.limits.max_llm_calls}, "
            # absent until the stepper has answered once, so every run without one reads
            # exactly as it always has: this string is in every observation the pilot LLM is
            # handed, and `roboglot log` parses it back out
            + (f"{self.stepper_calls} by the stepper, " if self.stepper_calls else "")
            # the same rule, for the seconds a policy has driven the body
            + (f"policy {self.policy_s:.1f}/{self.policy_total_s:g} s, " if self.policy_s else "")
            + f"{self.elapsed_s / 60:.1f}/{self.limits.max_minutes:g} min"
        )


def _segment_seconds(result: VerbResult | None, before: float | None, after: float | None) -> float:
    """What one policy segment charges against `policy_total_s`: the seconds its result says it
    ran. A result that does not say, or a call that ended with none, is charged the robot's
    clock across the call (`before` to `after`), since its segment may have run all of it: a
    call the executor ended itself, a timeout, a verb that raised, a cancellation or an abort.
    A `do` refused before its segment began says it ran 0 s, however long the refusal took on
    the wall. A call nobody could time charges nothing, and a number that is not one, a bool or
    a NaN, is never believed as one."""
    said = None if result is None else result.data.get("seconds")
    if isinstance(said, int | float) and not isinstance(said, bool) and math.isfinite(said):
        return max(0.0, float(said))
    if before is not None and after is not None and math.isfinite(after - before):
        return max(0.0, after - before)
    return 0.0


ConfirmFn = Callable[[str, dict[str, Any]], bool | Awaitable[bool]]
"""Asked before a gated verb runs. Return True to proceed: at once, as a terminal's prompt and
every standing answer do, or later, from a coroutine, as a drive session's goal asks the person
driving (`roboglot.answers`)."""


def deny_all(_name: str, _params: dict[str, Any]) -> bool:
    return False


def allow_all(_name: str, _params: dict[str, Any]) -> bool:
    return True


ENDING_S = HOLD_TTL_S
"""How long a verb the executor has cancelled is given to end, twice over, before it is left
behind and the stop sent without it (`Executor._end_verb`). One deadman window, the daemon's
300 ms (`HOLD_TTL_S`): a verb that has not ended that long after its cancel is stalled, as a
steering loop is that has had no frame for that long, and nothing it sends meanwhile is passed on
(`_Leash`), so a body with a deadman has stopped by then without the stop."""


DRY_RUN_SENDS_NOTHING = "a dry run sends nothing"
"""Why a send a verb made on a dry run went nowhere (`_Leash`), in the record and in the answer
the verb is handed."""

DRY_RUN_READS = frozenset({"get_state", "get_frame", "get_frames", "introspect", "now", "sleep"})
"""The methods of its transport a verb may call on a dry run, each one a read. Every other method
is refused there, recorded, and fails the verb (`_Leash`), since a dry run connects the real body
and a transport may carry the body's own motion methods, as the arm's `go_to_rest` and `let_go`.

The list is what the read-only verbs Roboglot ships call on their transports, on every body
(`observe`, `report_state`, `map`, the rosbridge base's `introspect`, and each adapter's own;
`tests/test_executor.py` runs each on every adapter's mock), and the clock: `get_state` reads
the body's state, `get_frame` and `get_frames` its cameras, `introspect` the bridge's topic list
and the robot's own description, `now` the transport's clock, and `sleep` lets its time pass.
Beside these a verb gets plain data off its transport (`_plain`), and nothing else."""

_PLAIN_SCALARS = (bool, int, float, complex, str, bytes, type(None))
"""What plain data is made of on a dry run: these types exactly, never a subclass, which could
carry methods of its own (`_plain`)."""

_NOT_PLAIN = object()
"""`_plain`'s answer for a value that is not plain data."""


def _plain(value: Any, seen: frozenset[int] = frozenset()) -> Any:
    """`value` as a verb may have it off its transport on a dry run, or `_NOT_PLAIN`.

    Plain data acts on nothing: a number, a string, a boolean, bytes or None (`_PLAIN_SCALARS`),
    an enum member whose value is plain, and a tuple, a frozen set, a list, a set, a dict or a
    dataclass whose contents are plain, all checked to the bottom. One that cannot change is
    handed on as it is, a tuple or a frozen dataclass of numbers such as a body's limits; one that
    can, a list, a set, a dict or a dataclass that is not frozen, as a copy, so a verb that reads
    it changes nothing of the transport's. Anything else is not plain, the adapter's backend above
    all, which every shipped adapter keeps public as `transport` and which a verb on a dry run used
    to reach and move the body through, and a container that holds itself (`seen`)."""
    if type(value) in _PLAIN_SCALARS:
        return value
    if isinstance(value, enum.Enum):
        return value if _plain(value.value, seen) is not _NOT_PLAIN else _NOT_PLAIN
    if id(value) in seen:
        return _NOT_PLAIN
    seen = seen | {id(value)}
    if type(value) in (tuple, frozenset, list, set):
        items = [_plain(item, seen) for item in value]
        if any(item is _NOT_PLAIN for item in items):
            return _NOT_PLAIN
        same = all(a is b for a, b in zip(items, value, strict=True))
        if type(value) in (tuple, frozenset) and same:
            return value
        return type(value)(items)
    if type(value) is dict:
        pairs = [(_plain(k, seen), _plain(v, seen)) for k, v in value.items()]
        if any(k is _NOT_PLAIN or v is _NOT_PLAIN for k, v in pairs):
            return _NOT_PLAIN
        return dict(pairs)
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        fields = {f.name: _plain(getattr(value, f.name), seen) for f in dataclasses.fields(value)}
        # and nothing beside its fields, which a copy would carry on unchecked
        if any(v is _NOT_PLAIN for v in fields.values()) or set(
            getattr(value, "__dict__", {})
        ) - set(fields):
            return _NOT_PLAIN
        frozen = type(value).__dataclass_params__.frozen  # type: ignore[attr-defined]
        if frozen and all(fields[n] is getattr(value, n) for n in fields):
            return value
        copied = copy.copy(value)
        for n, v in fields.items():
            object.__setattr__(copied, n, v)
        return copied
    return _NOT_PLAIN


_STOPS_UNDER_WAY: set[asyncio.Future[Any]] = set()
"""Stops still going out after the call that owed them stopped waiting (`Executor._answer_of`),
and the heartbeat's own (`Heartbeat._run`), held here so nothing collects one before it has
gone."""

STOP_ON_ITS_WAY = "still on its way as the call ended"
"""What became of an owed stop whose answer a cancel of the call cut short: it goes on by
itself (`_STOPS_UNDER_WAY`), and the `cancelled` gate says it was sent and not yet answered."""

_LEFT_BEHIND: set[asyncio.Task[Any]] = set()
"""Verbs the executor left behind (`Executor._end_verb`), held here until each ends."""


def _leave_behind(task: asyncio.Task[Any]) -> bool:
    """Hold a verb that would not end, and take it out of asyncio's own list of tasks, so the
    loop's close does not wait on it, and say whether it could. `asyncio.run` cancels every task
    left at its close and then waits for all of them with no bound, so such a verb held the
    process open after the run had ended, until it ended by itself or for good. It still runs
    while the loop does, sending nothing (`_Leash`), and is dropped with the loop. A thread it
    waits on is another matter: nothing in Python ends a thread, and `asyncio.run` waits for the
    default executor's threads at its close, so a verb stuck in a blocking call in
    `asyncio.to_thread` still holds the process until that call returns.

    `asyncio.tasks._unregister_task` is private. A Python without it holds the process for such
    a verb as before, which the caller says, and a test turns CI red on such a Python."""
    _LEFT_BEHIND.add(task)
    task.add_done_callback(_left_behind_ended)
    unregister = getattr(asyncio.tasks, "_unregister_task", None)
    if not callable(unregister):
        return False
    unregister(task)
    return True


def _left_behind_ended(task: asyncio.Task[Any]) -> None:
    _LEFT_BEHIND.discard(task)
    if not task.cancelled():
        task.exception()  # read, so asyncio does not report it as never retrieved


_PROBING: contextvars.ContextVar[bool] = contextvars.ContextVar("roboglot_probing", default=False)
"""Whether the gates running in this task are a probe (`Executor.refusal_before_the_question`):
the gates a call meets before its question, run to learn whether one would refuse it, which
write none of the call's rows and count nothing of it, since no call is made."""

_RUNNING_LEASH: contextvars.ContextVar[_Leash | None] = contextvars.ContextVar(
    "roboglot_running_leash", default=None
)
"""The leash of the verb whose task this code runs in, which a nested call it makes is held by
as well (`_Leash.above`), or None outside every verb."""


def _stop_gone(stopping: asyncio.Future[Any]) -> None:
    """A heartbeat's stop has gone, or failed as its link had: its record is the transport's
    own (`LoggedTransport`), and it is read here so asyncio does not report it again."""
    _STOPS_UNDER_WAY.discard(stopping)
    if not stopping.cancelled():
        stopping.exception()


async def _waited(tasks: set[asyncio.Future[Any]], seconds: float | None) -> bool:
    """Wait at most `seconds` for `tasks` to end, and say whether the task doing the waiting was
    cancelled meanwhile. `asyncio.wait` hands such a cancel to none of `tasks`, so a verb's own
    clean-up is not cut short by its caller going, and the wait ends there rather than lose the
    cancel: the caller raises it once what it owes is done (`Executor._execute`)."""
    try:
        await asyncio.wait(tasks, timeout=seconds)
    except asyncio.CancelledError:
        return True
    return False


class _Leash:
    """The transport, and the way to other verbs, that a running verb is handed (`_execute`),
    which carry nothing more once the executor has ended the verb.

    A verb is ended by a cancel, and a cancel can be lost: Python 3.11's `asyncio.wait_for`, which
    many transports wait for their answers through, drops one that lands as its call returns
    (`Heartbeat._run`), and a third party's verb may catch its own. Such a verb went on sending
    twists after the abort, or after its own timeout, until it ended by itself. Through this an
    intent it sends once it has been ended raises the cancel again, and so does a call to another
    verb, a stop excepted either way. An intent sent after the abort and before the executor has
    ended the verb, which takes it a turn or two of the loop, waits for that rather than race it
    to the robot; a call to another verb meanwhile is refused by the abort's own gate. Everything
    else is the transport's own, delegated as `LoggedTransport` delegates it.

    A nested call's leash hangs below its composite's (`above`), and is ended with it and with
    every verb above it, whenever the nested call was made: one still reading the body's state
    when its composite was ended used to start on a leash nobody had ended, and a kick went out
    after the composite's own timeout."""

    def __init__(
        self,
        inner: Any,
        abort: asyncio.Event | None,
        above: _Leash | None = None,
        *,
        dry: Callable[[str], None] | None = None,
    ) -> None:
        self._inner = inner
        self._abort = abort
        self._ended = asyncio.Event()
        self._above = above
        self._below: list[_Leash] = []
        self._sealed = False
        self._sending = 0
        self._dry = dry
        self.cut_off = False
        """A send of the verb's that passed this was cut off in flight once it had closed: a write
        a thread was doing for it goes on whatever the cancel, so it may land at any time."""
        self.why: str | None = None
        """Why the executor ended the verb, or one above it (`CANCELLED_BY_ABORT` and the rest),
        or None."""
        self.not_sent: list[str] = []
        """What the verb tried to send on a dry run, which this passed on to nothing (`dry`)."""
        if above is not None:
            above._below.append(self)
            if above.why is not None:
                self.end(above.why)

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):  # never delegate our own privates (copy, pickle, half-init)
            raise AttributeError(name)
        value = getattr(self._inner, name)
        if self._dry is None or name in DRY_RUN_READS:
            return value
        if callable(value):
            # On a dry run a method that is not a read is refused, as a send is: it passed on to
            # the transport, so a verb flagged `read_only` that called the arm's own `go_to_rest`
            # or `let_go` moved the body on a run that promised to send nothing
            return self._dry_call(name)
        # and anything else but plain data, at once: a value went on as it was, and the
        # adapter's own backend, public as `transport`, moved the arm on a dry run through its
        # `go_to_rest`, one attribute further than the method above looked
        plain = _plain(value)
        if plain is _NOT_PLAIN:
            self._dry_refusal(name)
            raise _HeldOnADryRun(f"{name}: {DRY_RUN_SENDS_NOTHING}")
        return plain

    def _dry_call(self, name: str) -> Callable[..., Any]:
        """What a verb gets on a dry run for a method of the transport that is not a read: a call
        that is refused and recorded, and raises, since what the method would have answered
        cannot be made up. The verb fails on it whatever it does next (`Executor._execute`)."""

        def refused(*_args: Any, **_kwargs: Any) -> NoReturn:
            self._dry_refusal(f"{name}()")
            raise _HeldOnADryRun(f"{name}(): {DRY_RUN_SENDS_NOTHING}")

        return refused

    def end(self, why: str) -> None:
        if self.why is None:
            self.why = why
            self._ended.set()
        for below in list(self._below):
            below.end(why)

    def detach(self) -> None:
        """Off the leash above, once its own call is over."""
        if self._above is not None and self in self._above._below:
            self._above._below.remove(self)

    async def ended(self) -> str | None:
        await self._ended.wait()
        return self.why

    def seal(self) -> None:
        """Let nothing more through, a stop included, once a stop for the ended verb is known to
        have landed (`Executor._end_verb`). A stop of its own until then can only meet the
        executor's; after it, from a verb left behind that ended at last, it landed in whatever
        ran then, the next verb or the run's fold. Never before one has landed: a stop of the
        verb's own just after one that failed on a link still busy is the stop that lands."""
        self._sealed = True

    async def stop(self) -> None:
        if self._sealed:
            raise asyncio.CancelledError(self.why)
        if self._dry is not None:
            self._dry_refusal("stop")
            return
        await self._inner.stop()

    def _refuse_if_ended(self) -> None:
        if self.why is not None:
            raise asyncio.CancelledError(self.why)

    def _dry_refusal(self, what: str) -> Ack:
        """A send refused on a dry run, recorded through `dry`, and answered as a body answers one
        it did not take, so a verb that reads the answer sees that nothing was sent."""
        self.not_sent.append(what)
        if self._dry is not None:
            self._dry(what)
        return Ack(accepted=False, reason=DRY_RUN_SENDS_NOTHING)

    async def send_intent(self, intent: Intent) -> Ack:
        if intent.kind != "stop" or self._sealed:
            if self.why is None and self._abort is not None and self._abort.is_set():
                await self._ended.wait()
            self._refuse_if_ended()
        if self._dry is not None:
            # A dry run promises to send nothing, and it connects the real body: a verb flagged
            # `read_only` ran for real on one, and the flag is the verb's own word, so a third
            # party's verb that carried it and sent an intent moved the body. Nothing a verb
            # sends reaches the body on a dry run now, whatever the verb is called or flagged.
            return self._dry_refusal(intent.describe())
        if intent.kind == "stop":
            return cast(Ack, await self._inner.send_intent(intent))
        self._sending += 1  # what could reach the wire, whatever it later returns
        try:
            return cast(Ack, await self._inner.send_intent(intent))
        except asyncio.CancelledError:
            if self.why is not None:
                self.cut_off = True
            raise
        finally:
            self._sending -= 1

    @property
    def sending(self) -> bool:
        """Whether a send of the verb's, other than a stop, has passed this and not finished."""
        return self._sending > 0

    def calls(
        self,
        run_verb: Callable[[str, dict[str, Any]], Awaitable[VerbResult]],
        canonical: Callable[[str], str],
    ) -> Callable[[str, dict[str, Any]], Awaitable[VerbResult]]:
        async def leashed(name: str, params: dict[str, Any]) -> VerbResult:
            if canonical(name) != "stop" or self._sealed:
                self._refuse_if_ended()
            return await run_verb(name, params)

        return leashed


@dataclass
class Executor:
    """allowlist → verdict → arguments → segment and budget → abort rules and preconditions on
    the body's state → confirm gate → all that can change while it asked, on a new reading →
    (dry-run |) execute with timeout."""

    registry: VerbRegistry
    transport: RobotTransport
    contract: GlotFrontmatter | None = None
    budget: Budget | None = None
    detector: Detector | None = None
    dry_run: bool = False
    confirm: ConfirmFn = deny_all
    log: Callable[[str], None] = lambda _m: None
    on_frame: Callable[[Any, str], None] = lambda _i, _c: None
    on_frames: Callable[[Sequence[CameraFrame], str], None] = lambda _f, _c: None
    """Beside `on_frame`, because a body with several cameras has no single picture: whoever
    records or returns what a verb saw wants all of them, and the steering loops want one."""
    abort: asyncio.Event = field(default_factory=asyncio.Event)
    consecutive_failures: dict[str, int] = field(default_factory=dict)
    history: list[tuple[str, dict[str, Any], VerbResult]] = field(default_factory=list)
    manifest: RobotManifest | None = None
    """The connected robot's manifest, handed to verbs so composites can pick a strategy."""
    verdict: Verdict | None = None
    """The pilot's latest word on whether this body can do the task it is on."""
    paused_until: float = 0.0
    """The `time.monotonic()` until which a pause this robot granted a peer holds (`pause_for`):
    the `paused` gate admits nothing that moves the body before then, whoever asks, the pilot
    included. 0 is no pause, which is every run no peer ever asked."""
    open_to_peers: bool = False
    """Whether a robot met may have a courtesy run now (`roboglot.passport_courtesy`). A run opens
    it once its pilot has the body and closes it before its teardown, and an MCP session once it
    has connected and until it closes. Outside those windows the body is moved by its rest move
    and its hand-over, which no gate sees, so a pause granted then could not be kept."""
    _moving: set[asyncio.Task[VerbResult]] = field(default_factory=set, init=False, repr=False)
    """The verbs moving the body now, which a granted pause or a newer command ends (`pause_for`,
    `preempt`)."""
    _leashes: dict[asyncio.Task[VerbResult], _Leash] = field(
        default_factory=dict, init=False, repr=False
    )
    """The leash each of those sends through, which a pause or a newer command ends with it."""
    _halted: dict[asyncio.Task[VerbResult], tuple[str, str]] = field(
        default_factory=dict, init=False, repr=False
    )
    """Those a pause or a newer command ended, with which it was and why, so `_execute` answers
    for them rather than raising their cancel (`CANCELLED_BY_PAUSE`, `CANCELLED_BY_PREEMPT`)."""
    _stops_out: set[asyncio.Future[Any]] = field(default_factory=set, init=False, repr=False)
    """The stops this executor no longer waited for, still going out (`_keep_going`)."""
    preempts: int = 0
    """How many times a newer command has ended the verbs moving the body (`preempt`). A verb
    compares it with the count from before its gates once its state is read, so one admitted
    while a newer command came does not start after it (`_run_admitted`)."""
    preempted_by: str = ""
    """Why the last of those happened, in the newer command's words."""
    require_verdict: bool = False
    """On where an `assess_task` tool was offered: the agent loop and every MCP session. A
    team member is a state machine with no pilot to ask, so its executor never does."""
    event_log: EventLog | None = None
    """Where the executor narrates itself: `verb_start`, every `gate` that fires, every
    `intent` a verb sends, `verb_end`. None is silent, which is what tests get."""
    eyes: Any = None
    """What `watch` looks through, handed to every verb's context (`VerbContext.eyes`), or None
    on every run that did not ask for frames."""
    stops: int = 0
    """How many times `stop` has been asked for through this executor, from any caller. A
    verb's context compares it with the count when the verb began (`VerbContext.interrupted`),
    so a verb that runs for a while ends on a brake pulled from beside it."""
    first_motion_at: float | None = None
    """When the first verb that moved the body (`_moves_now`) was handed to the transport, on
    `time.monotonic()`'s clock, or None until one has come back having done so: it answered ok, or
    the body took an intent of it. Never on a dry run, which hands nothing over, and never a read.
    The loop writes it as `first_motion_s` on the record's own
    clock, which is how long somebody standing by the robot waited for it to do anything at all:
    on the README's arm run two LLM calls and the serial port's ten seconds came first."""
    first_motion_llm_calls: int | None = None
    """How many LLM calls the run had made when that verb went, the one that chose it
    included, or None until one has gone."""
    segment: str | None = field(default=None, init=False)
    """The policy verb (`POLICY_VERBS`), as it was called, whose segment is between its check
    against `policy_total_s` and its charge, or None. Another is refused meanwhile."""
    places: PlaceStore = field(default_factory=lambda: PlaceStore("session", persist=False))
    """This robot's places (`roboglot.space.places`). A run or an MCP session with memory on hands
    in the store beside its memory file; every other keeps one that writes nothing, so places
    marked where nothing is remembered last as long as the executor does. Verbs read them against
    where the robot is now through `place_book`."""

    def place_book(self) -> PlaceBook | None:
        """The robot's places read against where it is now, or None on a body that does not say
        where it is (`roboglot.transport.base.spatial_of`)."""
        source = spatial_of(self.transport)
        return PlaceBook(self.places, source) if source is not None else None

    # ── narration ───────────────────────────────────────────────────────────────────

    def _emit(self, kind: str, **data: Any) -> None:
        # a probe makes no call, so it writes none of a call's rows (`_PROBING`)
        if not _PROBING.get():
            self._emit_anyway(kind, **data)

    def _emit_anyway(self, kind: str, **data: Any) -> None:
        """`_emit`, a probe's included: for what a probe sets off for the run itself, an abort
        rule that fired, which the record must say whoever read the state."""
        if self.event_log is not None:
            self.event_log.emit(kind, **data)

    def _note(self, text: str) -> None:
        """A free-text line for both audiences: `log` is a contract other callers rely on
        (the team's `member_log`, the MCP logger, tests), and the event log observes it."""
        self.log(text)
        self._emit("note", text=text)

    def _robot_now(self) -> float | None:
        """The robot's own clock, or None when it has none to give (a transport that raises,
        or one not connected yet). Never a reason to lose the verb."""
        with contextlib.suppress(Exception):
            return float(self.transport.now())
        return None

    def _timed_out(self, name: str, verb: Verb, *, unstopped: str | None = None) -> str:
        """What a verb this executor's own timeout ended says, asked after the stop that follows
        it, and why that stop failed when it did (`unstopped`, from `_stop_after`)."""
        stopped = "stopped" if unstopped is None else f"the stop sent after it failed: {unstopped}"
        return f"{name} timed out after {verb.timeout_s:g}s; {stopped}"

    def logged_transport(self) -> Any:
        """The transport as verbs see it: the real one, or a wrapper that narrates every
        intent. Also what the executor itself sends its safety stops through."""
        if self.event_log is None:
            return self.transport
        return LoggedTransport(self.transport, self.event_log)

    async def _stop_after(self) -> str | None:
        """Send the stop the executor owes a verb it ended, or one that ended in an error, and
        return why it failed, or None when it did not. Never raises.

        These stops run on the way out of a failure, and a link that has dropped is the commonest
        failure there is, so the stop is the call most likely to fail too. A transport whose stop
        raised took the whole call with it: `run_verb` raised the stop's own error from inside
        the handler that exists to turn the verb's into a result, and the pilot got a traceback
        for a verb the result would have explained. `LoggedTransport.stop` caught it already; a
        run without an event log had no such cover.

        A transport that knows its stop did not land says so in `stop_error` and does not raise,
        which is how the core `stop` verb hears it. This stop is the same call, so it reads the
        same attribute: without it, an abort said "a stop was sent" over an arm whose hold never
        reached it.

        A cancel of the caller is the one thing it raises, and never by cancelling the stop. A
        cancel that landed while the stop waited its turn on the bus used to cancel the stop with
        it, so a verb the executor had ended was left with no stop at all: an MCP call given up on
        in that window, over an arm, whose lock holds a stop until the call ahead of it is done.
        The stop runs in a task of its own now, goes out when its turn comes, and is said on the
        log if it then fails (`_late_stop`). Its answer is waited for a bounded while
        (`_answer_of`)."""
        unstopped, cancelled = await self._answer_of(self._start_stop())
        if cancelled:
            raise asyncio.CancelledError
        return unstopped

    async def last_stop(self) -> str | None:
        """The stop a run's teardown sends first, narrated like every other, and waited for as an
        owed stop is (`_answer_of`): over a link that had died it was waited for with no end, and
        the fold, the release, the close and `run_end` never came. Returns the sentence saying it
        was not answered in time and goes on by itself, or None once it was answered, whatever
        the answer, which its own record row says. A cancel of the caller goes on up, and the
        stop goes on without it."""
        stopping = self._start_stop()
        unstopped, cancelled = await self._answer_of(stopping)
        if cancelled:
            raise asyncio.CancelledError
        return None if stopping.done() else unstopped

    def _start_stop(self) -> asyncio.Future[Any]:
        """The owed stop, set going in a task of its own, so no cancel of the caller cuts it off
        half sent. One that raises as it is called is a failed stop, as one that raises later."""
        try:
            return asyncio.ensure_future(self.logged_transport().stop())
        except Exception as e:
            failed: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
            failed.set_exception(e)
            return failed

    def _stop_wait_s(self) -> float:
        """How long an owed stop's answer is waited for: as long as this body gives its own `stop`
        verb (its `timeout_s`), and the core `stop`'s where the registry has none. A link that has
        just failed its heartbeat can hold a stop for good, and the call that owed it, with the
        run's teardown behind it, used to wait for good too."""
        try:
            return float(self.registry.get("stop").timeout_s)
        except VerbNotFound:
            return float(CORE["stop"].timeout_s)

    async def _answer_of(
        self, stopping: asyncio.Future[Any], *, cancelled: bool = False
    ) -> tuple[str | None, bool]:
        """Why an owed stop failed, or None when it landed, and whether a cancel of the caller
        came while it was waited for. A stop not answered within `_stop_wait_s`, or by the time
        such a cancel came (or had come already, `cancelled`), is not waited for any longer: it
        goes on by itself, and is said to be unconfirmed rather than sent."""
        bound = self._stop_wait_s()
        if not cancelled:
            cancelled = await _waited({stopping}, bound)
        if not stopping.done():
            self._keep_going(stopping, bound)
            if cancelled:
                return STOP_ON_ITS_WAY, True
            return f"it was not answered within {bound:g} s, and goes on by itself", False
        if stopping.cancelled():
            return "it was cancelled before it went out", cancelled
        if (error := stopping.exception()) is not None:
            return f"{type(error).__name__}: {error}", cancelled
        undelivered = getattr(self.transport, "stop_error", None)
        return (str(undelivered) if undelivered else None), cancelled

    def _keep_going(self, stopping: asyncio.Future[Any], bound: float) -> None:
        """A stop no longer waited for, which goes on by itself: held (`_STOPS_UNDER_WAY`), said
        on the log if it then fails (`_late_stop`), and seen out (`_see_out`). `asyncio.run`'s
        close cancels every task it still has, and cancelled such a stop before it went out,
        where the run had been told it goes on by itself; so the stop is taken out of asyncio's
        list (`_unregister_task`, as `_leave_behind` does) and a task that keeps the loop open for
        it, a bounded while, takes the close's cancel in its place."""
        _STOPS_UNDER_WAY.add(stopping)
        self._stops_out.add(stopping)
        stopping.add_done_callback(self._late_stop)
        unregister = getattr(asyncio.tasks, "_unregister_task", None)
        if isinstance(stopping, asyncio.Task) and callable(unregister):
            unregister(stopping)
            keeper = asyncio.ensure_future(self._see_out(stopping, bound))
            _STOPS_UNDER_WAY.add(keeper)
            keeper.add_done_callback(_STOPS_UNDER_WAY.discard)

    async def _see_out(self, stopping: asyncio.Future[Any], bound: float) -> None:
        """Wait for a stop that goes on by itself for as long as the loop lives, and at the loop's
        close, whose cancel lands here, `bound` more; one still not answered then is cancelled,
        and said plainly, in the record where it is still open and on the log."""
        try:
            await asyncio.wait({stopping})
        except asyncio.CancelledError:
            with contextlib.suppress(asyncio.CancelledError):
                await asyncio.wait({stopping}, timeout=bound)
            if not stopping.done():
                stopping.cancel()
                text = (
                    f"a stop still on its way as the run ended was given {bound:g} s more and was "
                    "not answered: it was cancelled, and Roboglot cannot tell whether it reached "
                    "the robot"
                )
                self.log(text)
                self._emit_on_the_way_out("note", text=text)
            raise

    async def see_out_stops(self) -> None:
        """At the end of a run, while its record is open: the stops this executor no longer
        waited for (`_keep_going`) are waited for `_stop_wait_s` more, and one still not answered
        is said plainly, as `_see_out` says it at the loop's close."""
        out = {s for s in self._stops_out if not s.done()}
        if not out:
            return
        cancelled = await _waited(out, self._stop_wait_s())
        if still := [s for s in out if not s.done()]:
            text = (
                f"{len(still)} stop(s) sent during the run had still not been answered as it "
                "ended: Roboglot cannot tell whether they reached the robot"
            )
            self.log(text)
            self._emit_on_the_way_out("note", text=text)
        if cancelled:
            raise asyncio.CancelledError

    def _late_stop(self, stopping: asyncio.Future[Any]) -> None:
        """How a stop that was no longer waited for ended, said on the log when it did not land,
        since nobody is waiting on it to say so (`_answer_of`). One cancelled at the loop's close
        is said by `_see_out`."""
        _STOPS_UNDER_WAY.discard(stopping)
        self._stops_out.discard(stopping)
        if stopping.cancelled():
            return
        if (error := stopping.exception()) is not None:
            failed = f"{type(error).__name__}: {error}"
            self.log(f"an owed stop that was no longer waited for failed: {failed}")
        elif undelivered := getattr(self.transport, "stop_error", None):
            self.log(f"an owed stop that was no longer waited for did not land: {undelivered}")

    # ── policy ──────────────────────────────────────────────────────────────────────

    @property
    def allowed(self) -> list[str]:
        if self.contract is not None:
            return list(self.contract.verbs.allow)
        # No contract (MCP without a loaded task file): every non-dangerous verb.
        return [v.name for v in self.registry.verbs() if v.safety_class != "dangerous"]

    def is_allowed(self, name: str) -> bool:
        """Alias-aware: a task that allows `walk_to` also allows `go_to`, and vice versa."""
        canonical = self.registry.canonical(name)
        if canonical == "stop":
            return True
        return canonical in {self.registry.canonical(a) for a in self.allowed}

    @property
    def cleared(self) -> bool:
        """Whether a verb that moves the body may run."""
        return self.verdict is not None and self.verdict.go

    def needs_confirm(self, verb: Verb) -> bool:
        if self.registry.canonical(verb.name) == "stop":
            return False  # never gated, whatever a contract or a manifest says
        if self.contract is not None:
            gated = {self.registry.canonical(c) for c in self.contract.verbs.confirm}
            if self.registry.canonical(verb.name) in gated:
                return True
        return verb.safety_class in ("confirm", "dangerous")

    def _may_move(self, canonical: str, verb: Verb) -> bool:
        """Whether a verb may move the body, which a pause refuses and ends: anything outside
        `WHILE_PAUSED`, and a learned verb whatever it is called, since it is an unproven policy."""
        return canonical not in WHILE_PAUSED or verb.kind == "learned"

    def _moves_now(self, canonical: str, verb: Verb) -> bool:
        """Whether a verb about to run is the robot moving, for `first_motion_at`: one that may
        move the body (`_may_move`), less a sensor marked `read_only` that Roboglot has not named
        as motion, as the verdict gate reads it, and never on a dry run.

        Not `_may_move` alone, which is the pause gate's caution: it holds a third party's
        `read_only` verb still because the flag is no promise, and the record took a `locate`
        that reads where things are for the robot moving, on a dry run as well, where such a
        verb runs for real since it sends nothing."""
        if self.dry_run:
            return False
        if verb.read_only and canonical not in MOVES_THE_BODY:
            return False
        return self._may_move(canonical, verb)

    def _parsed(self, name: str, canonical: str, verb: Verb, params: dict[str, Any]) -> Any:
        """A call's arguments as its verb reads them. `stop` is never refused for them: it takes
        none, and a pilot in trouble that sends one anyway has reached for the brake, which was
        counted before any gate (`stops`), so a verb beside it had already ended on a stop that
        then never left. Only a third party's stop that cannot run without its own is refused."""
        try:
            return verb.params.model_validate(params)
        except ValidationError as e:
            if canonical != "stop":
                raise
            parsed = verb.params.model_validate({})
            self._emit("gate", name=name, gate="params", outcome="ignored", reason=_invalid(e))
            return parsed

    def _looks(self, canonical: str, verb: Verb) -> bool:
        """Whether a verb runs before any verdict, as one that only looks does.

        A verb declared `read_only` by whoever wrote it is a sensor, whatever body it came
        with: a third-party `locate` that reads where things are must run before the
        verdict for the same reason `observe` does, or the pilot judges feasibility blind.
        `BEFORE_VERDICT` is matched by name, and a learned verb is excluded from that half
        on purpose: it is an unproven policy, so a `.glot` that named one `observe` on a
        body with no camera verb would otherwise have it run before any verdict. The
        confirm gate would still stop it, and `--yes` answers the confirm gate.
        `MOVES_THE_BODY` wins over the flag: where Roboglot has said a name is motion, a verb
        arriving under that name and carrying `read_only` is saying two contradictory things
        about itself, and the gate believes Roboglot's own record rather than the newcomer."""
        return (canonical in BEFORE_VERDICT and verb.kind != "learned") or (
            verb.read_only and canonical not in MOVES_THE_BODY
        )

    def _no_verdict(self, name: str, after: dict[str, str] | None = None) -> VerdictRequired:
        """The `verdict` gate's refusal of a verb that moves the body, recorded, with what a
        refusal after a confirm's yes adds (`after`)."""
        why = (
            self.verdict.blocking_reason()
            if self.verdict is not None
            else "no feasibility verdict has been recorded for this task yet"
        )
        reason = (
            f"{name} moves the body, and {why}: record a verdict first "
            "(feasible, infeasible or uncertain)"
        )
        self._emit(
            "gate", name=name, gate="verdict", outcome="refused", reason=reason, **(after or {})
        )
        return VerdictRequired(reason)

    def _hold_still_for(
        self, name: str, canonical: str, verb: Verb, after: dict[str, str] | None = None
    ) -> None:
        """The `paused` gate: refuse a verb that may move the body while a pause holds."""
        held = self.paused_until - time.monotonic()
        if held > 0 and self._may_move(canonical, verb):
            reason = (
                f"{name} waits: a pause this robot granted a robot it met holds {held:.1f} s "
                "more, and until it ends only stop and the verbs that move nothing run"
            )
            self._emit(
                "gate", name=name, gate="paused", outcome="refused", reason=reason, **(after or {})
            )
            raise VerbNotAllowed(reason)

    def pause_for(self, seconds: float) -> None:
        """Hold the body still for `seconds` (docs/passport/talk.md#what-roboglot-does-with-an-ask):
        every verb already moving it is ended now, as an abort ends one, and the `paused` gate
        admits nothing that moves it until the hold is over. Without the first half a `move`
        that began a second earlier went on sending its twists for the rest of its duration,
        and a `go_to` for up to its whole timeout, past a robot told this one was holding
        still. A second pause only ever lengthens the hold, never shortens one already running."""
        self.paused_until = max(self.paused_until, time.monotonic() + seconds)
        # Cancelled here, not through something they wait on: a task cancelled before its next
        # step never takes it, and its leash passes on nothing it sends after a cancel it lost,
        # so none of them sends another intent after this returns, and the stop the courtesy
        # sends next is the last thing the body is told. Ending the leash wakes the verb's
        # `_execute`, which ends it as the abort ends one (`_end_verb`): its stop at once, a
        # second cancel and a bound, where it used to wait for the verb with no bound but its
        # own timeout.
        for task in list(self._moving):
            if not task.done():
                self._halted[task] = (CANCELLED_BY_PAUSE, "")
                if (leash := self._leashes.get(task)) is not None:
                    leash.end(CANCELLED_BY_PAUSE)
                task.cancel(CANCELLED_BY_PAUSE)

    def preempt(self, reason: str) -> int:
        """End every verb moving the body now, for a newer command, and say how many there were.

        `pause_for` with no hold after it: the command that asks for this starts as soon as they
        have stopped, and nothing is refused after it but a verb already admitted and still
        reading the body's state, which would otherwise start after the newer one had begun
        (`_run_admitted`). Each verb it ends answers with `Preempted`, in `reason`'s words, once
        a stop has been sent after it. A read runs on beside it, as it runs on beside a pause,
        and `stop` is never in its way."""
        self.preempts += 1
        self.preempted_by = reason
        ended = 0
        # cancelled here, for the reason `pause_for` gives: a task cancelled before its next step
        # never takes it, so nothing it would have sent goes out after this returns
        for task in list(self._moving):
            if not task.done():
                self._halted[task] = (CANCELLED_BY_PREEMPT, reason)
                if (leash := self._leashes.get(task)) is not None:
                    leash.end(CANCELLED_BY_PREEMPT)
                task.cancel(CANCELLED_BY_PREEMPT)
                ended += 1
        return ended

    def context(
        self, source: Source = "agent", *, stops: int | None = None, name: str | None = None
    ) -> VerbContext:
        """`source` is the outer call's, so a composite's nested verbs are narrated as coming
        from the same pilot (an MCP session's `approach_and` runs an MCP `go_to`). `stops` is
        the count of stops the verb answers to, the one taken when it was asked for, so a stop
        that came while it was still being admitted ends it; now, by default. `name` is the verb
        the context is for, which a rule that fires mid-verb names in its gate record.

        A composite's nested calls answer to that same count. Each used to take a count of its
        own when it was admitted, so a stop sent between two of a composite's calls was already
        in the count the second one started from: it walked, or kicked, as if nobody had braked,
        unless the composite happened to check between its calls. A `stop` the composite sends
        through `run_verb` itself counts as well, as it always did for `interrupted`: a call that
        would move the body after it is not started (`_overtaken`)."""
        stops_then = self.stops if stops is None else stops
        return VerbContext(
            transport=self.logged_transport(),
            detector=self.detector,
            dry_run=self.dry_run,
            takes_no_arguments=self._takes_no_arguments,
            # a verb's own log line is a `note` as well, so it is not the one thing the log
            # cannot see. Not the executor's own arrows: those would double `verb_start`.
            log=self._note,
            on_frame=self.on_frame,
            on_frames=self.on_frames,
            run_verb=lambda verb, params: self.run_verb(
                verb, params, source=source, nested=True, outer_stops=stops_then
            ),
            # an adapter carries its manifest after connect; a bare transport has none
            manifest=self.manifest or getattr(self.transport, "manifest", None),
            eyes=self.eyes,
            interrupted=self._interruption(stops_then),
            abort_when=self._abort_rule_reader(name),
            places=self.place_book(),
        )

    def _abort_rule_reader(self, name: str | None) -> Callable[[RobotState], Awaitable[None]]:
        """`VerbContext.abort_when` for a verb beginning now: the rules an admission reads
        (`_check_abort_conditions`), read against a state the verb took mid-way. One that fires
        is recorded as the admission's `abort_when` gate is, the body is stopped, and only then is
        the run aborted, so the verb that read the rule leaves with its own `Aborted` rather than
        the abort racing it and cancelling the stop half sent. The abort is set however the stop
        ends, a timeout, a pause or a newer command landing in it included: the rule fired, and
        the run is over whatever else ends the verb. A composite whose nested call read the rule
        is ended by that abort in its turn, with a stop of its own after it."""

        async def abort_when(state: RobotState) -> None:
            reason = self._abort_rule_fired(state)
            if reason is None:
                return
            self._emit(
                "gate",
                name=name or "verb",
                gate="abort_when",
                outcome="fired",
                reason=reason,
                state=state.summary(),
            )
            try:
                unstopped = await self._stop_after()
            finally:
                self.abort.set()
            if unstopped is not None:
                raise Aborted(f"{reason}; the stop sent mid-verb failed: {unstopped}")
            raise Aborted(f"{reason}; the verb stopped where it stood")

        return abort_when

    def _takes_no_arguments(self, name: str) -> bool:
        """`VerbContext.takes_no_arguments`. A verb that does not exist is for the gates that
        refuse it to say so."""
        try:
            self.registry.get(name).params.model_validate({})
        except VerbNotFound:
            return True
        except ValidationError:
            return False
        return True

    def _interruption(self, stops_then: int) -> Callable[[], str | None]:
        """`VerbContext.interrupted` for a verb beginning now: `STOPPED` once a `stop` has been
        asked for since, or the budget's own sentence once the run's minutes are spent, which
        `check_time` is the one reading of, and None otherwise. Neither is an abort, which
        cancels the verb from outside and needs no asking."""

        def interrupted() -> str | None:
            if self.stops != stops_then:
                return STOPPED
            if self.budget is not None:
                try:
                    self.budget.check_time()
                except BudgetExceeded as e:
                    return str(e)
            return None

        return interrupted

    # ── the one entry point ─────────────────────────────────────────────────────────

    async def run_verb(
        self,
        name: str,
        params: dict[str, Any] | None = None,
        *,
        source: Source = "agent",
        nested: bool = False,
        picked_by: str | None = None,
        outer_stops: int | None = None,
    ) -> VerbResult:
        """Every verb call, from the agent loop, an MCP client or a composite.

        The narration wraps the gates: exactly one `verb_start` and, whatever happens after
        it, exactly one `verb_end` with an `outcome`, so a reader of the log never sees a
        verb that started and vanished. `ok`/`fail` are the verb's own verdict; `refused`,
        `denied`, `budget` and `aborted` are the executor's, and `error` is a bug.

        `picked_by` names the decision LLM that read a person's own words as this call, where
        one did (`roboglot drive`'s fast lane): the call is still theirs for the verdict gate,
        and the record says who chose it. `outer_stops` is a composite's count of stops, which
        its nested call answers to in place of one of its own (`context`)."""
        params = params or {}
        canonical = self.registry.canonical(name)
        started = time.perf_counter()
        robot_started = self._robot_now()
        outcome, summary, ok = "error", "verb exited unexpectedly", False
        data_keys: list[str] = []
        # `after_confirm` from a confirm's yes until the verb starts (`_run_verb`), so a call
        # that ends in between, on a refusal, a read that failed or a cancel, says on its
        # `verb_end` that it had been agreed to
        marks: dict[str, str] = {}
        with counting() as counter:
            try:
                # inside the try: a record sink that raises here must still leave a verb_end
                self._emit(
                    "verb_start",
                    name=name,
                    canonical=canonical,
                    params=params,
                    source=source,
                    nested=nested,
                    **({"picked_by": picked_by} if picked_by else {}),
                )
                result = await self._run_verb(
                    name,
                    canonical,
                    params,
                    sent=counter,
                    source=source,
                    nested=nested,
                    picked_by=picked_by,
                    outer_stops=outer_stops,
                    marks=marks,
                )
            except VerbNotAllowed as e:
                outcome, summary = "refused", str(e)
                raise
            except ConfirmDenied as e:
                outcome, summary = "denied", str(e)
                raise
            except BudgetExceeded as e:
                outcome, summary = "budget", str(e)
                raise
            except Aborted as e:
                outcome, summary = "aborted", str(e)
                raise
            except SafetyStop as e:
                # another layer ended the verb on purpose (a team role change): its own word,
                # so the console does not call a routine handover an error
                outcome, summary = e.outcome, str(e)
                raise
            except asyncio.CancelledError:
                # the caller went away (an MCP client, the CLI's second Ctrl-C). `_execute`
                # has already cancelled the verb and sent a stop, and said so in a gate.
                outcome, summary = "aborted", "cancelled from outside"
                raise
            except BaseException as e:  # a cancelled task ends the verb too, and says so
                outcome, summary = "error", f"{type(e).__name__}: {e}"
                raise
            else:
                ok = result.ok
                outcome, summary, data_keys = (
                    ("ok" if ok else "fail"),
                    result.summary,
                    list(result.data),
                )
                return result
            finally:
                clocks: dict[str, Any] = {}
                robot_now = self._robot_now()
                if robot_started is not None and robot_now is not None:
                    clocks["transport_s"] = round(robot_now - robot_started, 3)
                self._emit(
                    "verb_end",
                    name=name,
                    canonical=canonical,
                    ok=ok,
                    outcome=outcome,
                    summary=summary,
                    data_keys=data_keys,
                    elapsed_s=round(time.perf_counter() - started, 3),
                    intents=dict(counter),
                    source=source,
                    nested=nested,
                    **clocks,
                    **detect_times(self.detector),
                    **marks,
                )

    async def _run_verb(
        self,
        name: str,
        canonical: str,
        params: dict[str, Any],
        *,
        sent: Tally,
        source: Source,
        nested: bool,
        picked_by: str | None = None,
        outer_stops: int | None = None,
        marks: dict[str, str] | None = None,
    ) -> VerbResult:
        # before any gate, so a newer command that comes while this one is still being admitted
        # stops it starting after that command has (`_run_admitted`)
        preempts = self.preempts
        if canonical == "stop":
            # counted before any gate, since a brake asked for is a brake pulled, whoever asked:
            # a verb running beside this call ends on it (`_interruption`)
            self.stops += 1
        # and the stops this verb has to answer to are counted from here, not from when it
        # starts: one asked for while it was still reading the body's state used to land in
        # the count it started from, so it walked the whole walk the stop was meant to end. A
        # composite's nested call counts from where the composite did (`context`).
        stops = self.stops if outer_stops is None else outer_stops
        # What a refusal adds once a confirm has agreed to the call (`after_confirm`), and the
        # call's `verb_end` until it starts (`marks`, `run_verb`). A confirm that put its question
        # before the call, as `roboglot drive` puts it while it routes the line, has agreed
        # already, and every refusal of the call comes after that yes.
        after = marks if marks is not None else {}
        after.update(self._agreed_already(name))
        checked = await self._before_the_question(
            name,
            canonical,
            params,
            source=source,
            nested=nested,
            picked_by=picked_by,
            preempts=preempts,
            stops=stops,
            after=after,
        )
        if isinstance(checked, VerbResult):
            return checked
        verb, parsed, timed, asks = checked
        return await self._asked_and_admitted(
            name,
            canonical,
            params,
            verb,
            parsed,
            timed=timed,
            asks=asks,
            sent=sent,
            source=source,
            nested=nested,
            preempts=preempts,
            stops=stops,
            after=after,
        )

    def _agreed_already(self, name: str) -> dict[str, str]:
        """`after_confirm` for a call whose confirm put its question before the call and was
        answered yes (`Answered.agreed_already`, `roboglot drive`), or nothing."""
        agreed = getattr(self.confirm, "agreed_already", None)
        said = agreed(name) if callable(agreed) else None
        return {"after_confirm": said} if isinstance(said, str) and said else {}

    async def refusal_before_the_question(
        self, name: str, params: dict[str, Any] | None = None, *, source: Source = "agent"
    ) -> str | None:
        """Why a call would be refused before its confirm question is put, in the words its
        refusal says, or None: every gate the call meets before the question
        (`_before_the_question`), on a reading of the body's state taken now. For a caller that
        puts the question to a person itself, `roboglot drive`, so that everything that would
        refuse the call is checked first and the person is asked after. It makes no call: it
        writes none of a call's rows, counts and charges nothing, and asks nobody. What a gate
        sets off for the body is set off as in a call, the stop after a read that failed and the
        abort a rule fires, which the record says. The call itself meets every gate again when it
        is made, after the answer."""
        token = _PROBING.set(True)
        try:
            checked = await self._before_the_question(
                name,
                self.registry.canonical(name),
                params or {},
                source=source,
                nested=False,
                picked_by=None,
                preempts=self.preempts,
                stops=self.stops,
                after={},
            )
        except (VerbNotAllowed, SafetyStop) as e:
            return str(e)
        except Exception as e:  # a read of the body's state that failed, its stop sent
            return f"{type(e).__name__}: {e}"
        finally:
            _PROBING.reset(token)
        return checked.summary if isinstance(checked, VerbResult) else None

    async def _before_the_question(
        self,
        name: str,
        canonical: str,
        params: dict[str, Any],
        *,
        source: Source,
        nested: bool,
        picked_by: str | None,
        preempts: int,
        stops: int,
        after: dict[str, str],
    ) -> tuple[Verb, Any, bool, bool] | VerbResult:
        """Every gate a call meets before its confirm question, in order: the abort, the
        allowlist, the verb itself, the peer, a pause, the verdict, the arguments, a stop that
        overtook it, the segment and the budget, the confirm's refusals of a standing answer, and,
        for a call that will put a question, the checks on a reading of the body's state. A
        refusal is raised, or comes back as the call's result; otherwise this gives the verb, its
        arguments as it reads them, whether it is a policy segment and whether a question will be
        put. On a probe (`refusal_before_the_question`, `_PROBING`) the same gates run, charge no
        step, and leave out the confirm's refusals of a standing answer, which turn on who answers
        and not on the call: the prober puts the question to a person itself."""
        probing = _PROBING.get()
        # `stop` is the one verb an aborted session must still be able to run. The abort is
        # set precisely when something has gone wrong — a failed heartbeat, a kill switch —
        # which is the moment the pilot reaches for the brake, and refusing it here closed
        # the panic button exactly when it was needed.
        if self.abort.is_set() and canonical != "stop":
            self._emit(
                "gate", name=name, gate="abort", outcome="refused", reason="run aborted", **after
            )
            raise Aborted("run aborted")
        if not self.is_allowed(name):
            reason = f"verb {name!r} is not in this task's allowlist ({', '.join(self.allowed)})"
            self._emit(
                "gate", name=name, gate="allowlist", outcome="refused", reason=reason, **after
            )
            raise VerbNotAllowed(reason)
        try:
            verb = self.registry.get(name)
        except VerbNotFound:
            self._emit(
                "gate",
                name=name,
                gate="unknown",
                outcome="refused",
                reason=f"unknown verb {name!r}",
                **after,
            )
            raise VerbNotAllowed(f"unknown verb {name!r}") from None
        if source == "peer" and canonical not in PEER_VERBS:
            reason = (
                f"{name} is not a verb a courtesy runs: a robot met over Robot Passport can have "
                "this one pause or identify itself, and nothing else"
            )
            self._emit("gate", name=name, gate="peer", outcome="refused", reason=reason, **after)
            raise VerbNotAllowed(reason)

        looks = self._looks(canonical, verb)
        self._hold_still_for(name, canonical, verb, after)
        if self.require_verdict and not looks and not self.cleared and source in PERSONS:
            # the person's own command is the verdict for its own call, and the record says
            # whose word it was, the way an answer to a confirm says it (`who_answered`), and
            # who read their words as this call where somebody did. A verb that call runs is
            # covered by its row, as a pilot's nested verbs are by its verdict, and has none of
            # its own: each used to say a person had named it, which nobody had.
            if not nested:
                said = A_PERSON if source == "person" else A_PIPE
                whose = (
                    f"{picked_by} read what {said} typed as this call"
                    if picked_by
                    else f"{said} named this call"
                )
                self._emit(
                    "gate",
                    name=name,
                    gate="verdict",
                    outcome="allowed",
                    reason=f"{whose}, which stands for the verdict for it alone",
                )
        elif self.require_verdict and not looks and not self.cleared:
            raise self._no_verdict(name, after)

        try:
            parsed = self._parsed(name, canonical, verb, params)
        except ValidationError as e:
            msgs = _invalid(e)
            self._emit("gate", name=name, gate="params", outcome="refused", reason=msgs, **after)
            # A failure of the call's own, recorded, so `abort_when`'s count of failures in a row
            # sees it: it returned unrecorded, and a pilot stuck on a speed past the clamp sent it
            # again and again until `max_llm_calls` or `max_minutes` ended the run. It runs
            # nothing, so it is charged no step, being refused before the budget. A pilot's call
            # counts, in a run, a drive goal or over MCP; a person's own line in `roboglot drive`
            # does not (`PERSONS`), since a person who mistypes three times has not asked for the
            # session to end, as the session's own rules already hold.
            return self._record(
                name,
                params,
                VerbResult.fail(f"invalid params for {name}: {msgs}"),
                counts=source not in PERSONS,
            )

        # before a person is asked anything: a call a stop has overtaken is not going to start,
        # and a question about it would be answered for nothing
        if overtaken := self._overtaken(name, canonical, params, verb, stops, after):
            return overtaken

        # And every other gate that can refuse the call with what is known now, for the same
        # reason: the question came before the segment gate and the budget, so a person said
        # yes to a call they then refused, and the record kept a question and an allowed confirm
        # for a verb that never ran. Checked here and charged after the answer, so a call
        # somebody declines still costs no step.
        timed = canonical in POLICY_VERBS
        asks = self.needs_confirm(verb)
        self._segment_and_budget(
            name,
            canonical,
            timed=timed,
            nested=nested,
            source=source,
            charge=not asks and not probing,
            after=after,
        )
        if asks and not probing:
            # A `dangerous` verb is the one class a standing answer may not clear. `--yes`, a
            # team's standing answer, the MCP server and a pipe on stdin all answer without
            # asking anybody, so the verb is refused before its confirm is put, and only a
            # person at a terminal can run it (ADR-0056). Refused rather than denied: nobody
            # said no, and the fix is a terminal, not `--yes`.
            if verb.safety_class == "dangerous" and not a_person_was_asked(self.confirm):
                reason = (
                    f"{name} is a dangerous verb, and only a person can clear it, while nobody "
                    "is asked here (--yes, a team's standing answer, the MCP server or a "
                    "pipe): run it from a terminal, without --yes, and answer its confirm "
                    "yourself"
                )
                self._emit("gate", name=name, gate="confirm", outcome="refused", reason=reason)
                raise VerbNotAllowed(reason)
            # A backend may decline a standing answer for one of its verbs: `--yes`, a team's
            # standing answer and the MCP server answer nobody, so the verb is refused before
            # its confirm is put (`Verb.no_standing_answer`, ADR-0078). The CLI's own question on
            # a pipe is left as it is, and so is what a person answers.
            if verb.no_standing_answer and who_answered(self.confirm) not in (A_PERSON, A_PIPE):
                reason = (
                    f"{name} is answered by a person on this robot, each time, while nobody is "
                    "asked here (--yes, a team's standing answer or the MCP server): run it "
                    "from a terminal, with roboglot run or roboglot drive and without --yes, and "
                    "answer its confirm yourself"
                )
                self._emit("gate", name=name, gate="confirm", outcome="refused", reason=reason)
                raise VerbNotAllowed(reason)
        if asks or probing:
            # The checks that read the body's state, on a reading taken before the question: they
            # ran only after it, so a person said yes to a call the body's own preconditions or a
            # flat battery then refused. The state is read again after the answer
            # (`_run_admitted`), so a call that puts a question reads it twice, and what the wait
            # changed refuses there. A read that fails here fails the call, and nobody is asked.
            refused = self._on_the_reading(
                name,
                canonical,
                params,
                verb,
                await self._read_the_state(),
                source=source,
                preempts=preempts,
                stops=stops,
                after=after,
            )
            if refused is not None:
                return refused
        return verb, parsed, timed, asks

    async def _asked_and_admitted(
        self,
        name: str,
        canonical: str,
        params: dict[str, Any],
        verb: Verb,
        parsed: Any,
        *,
        timed: bool,
        asks: bool,
        sent: Tally,
        source: Source,
        nested: bool,
        preempts: int,
        stops: int,
        after: dict[str, str],
    ) -> VerbResult:
        """A call every gate before its question has let through: the question where it puts
        one, what can change while it is open, then the call (`_run_admitted`)."""
        if asks:
            try:
                answer = await answered(self.confirm, name, parsed.model_dump(), abort=self.abort)
            except asyncio.CancelledError:
                # cancelled while the question waited, by the composite that called this verb
                # running out of time or by a cancel from outside: nobody answered, the gate says
                # so, and the cancellation goes on up
                self._emit(
                    "gate",
                    name=name,
                    gate="confirm",
                    outcome="denied",
                    answer=None,
                    reason="the prompt raised CancelledError",
                )
                raise
            except Exception as e:
                if self.abort.is_set():
                    # taken back by the run's own abort, a kill switch, a failed heartbeat or a
                    # drive session's brake: the call ends as the abort ends any call, and the
                    # run on why the abort was set, as a pilot's doubt the abort took back does
                    # (`AgentLoop._assess`). It ended denied, nobody having answered, which put
                    # the question in the place of the stop.
                    self._emit(
                        "gate", name=name, gate="abort", outcome="refused", reason="run aborted"
                    )
                    raise QuestionTakenBack("run aborted") from e
                # typer's y/N prompt raises click's `Abort` (a RuntimeError) on Ctrl-C or
                # EOF. An asker that raised has not said yes, and the run deserves to be told
                # so in the gate's words rather than as `verb_end error "Abort: "`.
                why = f"the prompt raised {type(e).__name__}" + (f": {e}" if str(e) else "")
                # unanswered, never declined: nobody said no, so neither the gate's `answer`
                # nor the words the pilot reads may say somebody did
                self._emit(
                    "gate", name=name, gate="confirm", outcome="denied", answer=None, reason=why
                )
                raise ConfirmDenied(f"nobody answered whether to run {name} ({why})") from e
            asked = a_person_was_asked(self.confirm)
            if asked:
                # the exchange, beside the gate's record of what it decided: only where a
                # person was really asked, never for `--yes` or a team's standing answer
                self._emit(
                    "prompt",
                    what="confirm",
                    question=confirm_question(name, parsed.model_dump()),
                    answer=answer,
                    **asked_on(self.confirm),
                )
            # The same care in the gate's own reason, which said `a human said yes` however the
            # answer arrived. Under `--yes`, a team's standing answer or a pipe on stdin the
            # gate really did open, and the record should say that and not name a witness.
            said = ("yes" if answer else "no") if asked else ("allowed" if answer else "denied")
            agreed = f"a human said {said}" if asked else f"the confirm gate was {said}"
            self._emit(
                "gate",
                name=name,
                gate="confirm",
                outcome="allowed" if answer else "denied",
                answer=answer,
                reason=agreed,
            )
            if not answer:
                raise ConfirmDenied(f"human declined {name}")
            # What can change while the question is open is read again after the answer: a
            # second call over MCP may have taken the last step or started a segment, and the
            # run's minutes may have run out, as a pause, a newer command, the abort, a verdict
            # withdrawn or a stop may have come, which the gates after the state read look at
            # for every call. A refusal from here on says the confirm had agreed to the call,
            # beside its own reason, so nobody reads the yes above it as a verb that ran.
            after["after_confirm"] = agreed
            self._segment_and_budget(
                name,
                canonical,
                timed=timed,
                nested=nested,
                source=source,
                charge=True,
                after=after,
            )
        if not timed:
            return await self._run_admitted(
                name,
                canonical,
                params,
                verb,
                parsed,
                sent=sent,
                source=source,
                preempts=preempts,
                stops=stops,
                nested=nested,
                after=after,
            )
        # held from the check above, with nothing awaited since it, to the segment's charge
        self.segment = name
        try:
            return await self._run_admitted(
                name,
                canonical,
                params,
                verb,
                parsed,
                sent=sent,
                source=source,
                timed=True,
                preempts=preempts,
                stops=stops,
                nested=nested,
                after=after,
            )
        finally:
            self.segment = None

    def _segment_and_budget(
        self,
        name: str,
        canonical: str,
        *,
        timed: bool,
        nested: bool,
        source: Source,
        charge: bool,
        after: dict[str, str] | None = None,
    ) -> None:
        """The `segment` and `budget` gates: refuse what they refuse, and charge the call's step
        where `charge` says to. A call nobody is asked about passes them once, charged. One that
        is asked passes them twice: before the question, checked and not charged, so nobody is
        asked about a call these would refuse, and after the yes, charged. `after` is what a
        refusal's record adds once the confirm has agreed to the call."""
        after = after or {}
        # A segment of the body's learned policy has a budget of its own seconds as well as
        # its step, checked first so a segment refused for time charges no step. Nested or
        # not: the seconds are the body's, whoever asked for them. `pick` hands the arm to the
        # same policy as `manipulate` does, so its seconds are the same budget's.
        if timed and self.segment is not None:
            # One segment at a time, from its check to its charge. Over MCP every call is a
            # task of its own, and a second one let in while the first ran was checked against
            # seconds nobody had charged yet, then cancelled the first on the arm and ran its
            # own, so the arm could run past `total_s` by as many segments as calls came in.
            # Refused rather than queued, so the pilot hears it now and decides.
            reason = (
                f"{self.segment} is still running a segment of the body's learned policy: wait "
                f"for it to end, or send stop, before {name}"
            )
            self._emit("gate", name=name, gate="segment", outcome="refused", reason=reason, **after)
            raise VerbNotAllowed(reason)
        if self.budget is not None and (timed or not nested):
            try:
                if timed:
                    self.budget.check_policy()
                # A peer's courtesy draws on a budget of its own (`roboglot.passport_courtesy`),
                # never the pilot's: an identify every ten seconds used to spend six of a run's
                # forty steps a minute. And `stop` is charged while there is a step left and
                # never refused for want of one, as no other gate refuses it: a session whose
                # steps were spent used to have its brake refused with them.
                if not nested and source != "peer":
                    try:
                        if charge:
                            self.budget.note_step()
                        else:
                            self.budget.check_step()
                    except BudgetExceeded:
                        if canonical != "stop":
                            raise
            except BudgetExceeded as e:
                self._emit(
                    "gate", name=name, gate="budget", outcome="exceeded", reason=str(e), **after
                )
                raise

    async def _run_admitted(
        self,
        name: str,
        canonical: str,
        params: dict[str, Any],
        verb: Verb,
        parsed: Any,
        *,
        sent: Tally,
        source: Source,
        timed: bool = False,
        preempts: int | None = None,
        stops: int | None = None,
        nested: bool = False,
        after: dict[str, str] | None = None,
    ) -> VerbResult:
        """A verb every gate has let through: its state, its abort conditions and its
        preconditions, then the verb itself, or the dry run's word for it; the brake reads its
        state after it has gone out, for the abort conditions alone. `timed` is a policy
        segment's, whose seconds are charged on every way out. `preempts` is the executor's count
        of newer commands from before the gates, which a verb that may move the body is refused
        on once it has changed (`preempt`), and `stops` its count of stops from then, which the
        verb ends on once that has changed (`_interruption`). A `nested` call's `stops` is its
        composite's, and one that has changed by the time it is admitted is not started.
        `after` is what the record of a refusal here adds once a confirm agreed to the call
        (`_run_verb`)."""
        after = after or {}
        # The brake goes out first and reads the body's state after, for the run's `abort_when`
        # rules alone: it waited on that read, abort or not, so a read that did not come back
        # held the brake for good, and every brake went out a read's round trip late. A dry run
        # sends no brake, and reads first as every other call does.
        brake = canonical == "stop" and not self.dry_run
        state: RobotState | None = None
        if not brake:
            try:
                state = await self.transport.get_state()
            except Exception as e:
                await self._stop_after_a_failed_read(e)
                raise
        refused = self._on_the_reading(
            name,
            canonical,
            params,
            verb,
            state,
            source=source,
            preempts=preempts,
            stops=stops,
            after=after,
        )
        if refused is not None:
            return refused
        # past every check: the call starts here, and from here on what ends it says so itself
        # (`marks`, `run_verb`)
        after.clear()
        return await self._handed_over(
            name,
            canonical,
            params,
            verb,
            parsed,
            sent=sent,
            source=source,
            timed=timed,
            stops=stops,
        )

    async def _read_the_state(self) -> RobotState:
        """The body's state for a call's checks, with the stop owed after a read that raised sent
        and said in its error, which goes up as the call's (`_stop_after_a_failed_read`)."""
        try:
            return await self.transport.get_state()
        except Exception as e:
            # The stop is the body's whoever read, and runs, with every task it starts, outside a
            # probe's mark (`_PROBING`): a keeper it left to see the stop out, started marked,
            # wrote nothing when it cancelled the stop at the run's end, where a call's writes it.
            token = _PROBING.set(False)
            try:
                await self._stop_after_a_failed_read(e)
            finally:
                _PROBING.reset(token)
            raise

    def _on_the_reading(
        self,
        name: str,
        canonical: str,
        params: dict[str, Any],
        verb: Verb,
        state: RobotState | None,
        *,
        source: Source,
        preempts: int | None,
        stops: int | None,
        after: dict[str, str] | None = None,
    ) -> VerbResult | None:
        """The checks a call meets on a reading of the body's state, `state`, or None for the
        brake, which reads after it has gone out: what can have changed since its gates while it
        waited, its abort conditions and its preconditions. A refusal comes back as the call's
        result or is raised, and None lets it start. A call that puts a question meets them on a
        reading taken before it, so nobody is asked about a call they refuse, and again after the
        answer on a new one, where `after` marks a refusal as following the confirm's yes; every
        other call, once (`_run_admitted`)."""
        after = after or {}
        # A cancel of this call that Python 3.11's `wait_for` lost as that read, or a person's
        # confirm before it, answered is still asked for (`Task.cancelling`), and the call does
        # not start its verb: one MCP cancel, or the second Ctrl-C, ran the walk to its end. The
        # heartbeat's loop reads it the same way. Never the brake's.
        me = asyncio.current_task()
        if me is not None and me.cancelling() and canonical != "stop":
            raise asyncio.CancelledError
        # Read again after the last await before the verb is tracked as moving. A pause
        # granted while this verb waited on that state read, or on a person's confirm before
        # it, found nothing to end, and the gate it had already passed is not asked twice, so
        # the verb used to run in full after the stop a robot met was promised.
        self._hold_still_for(name, canonical, verb, after)
        # and a newer command that came meanwhile found nothing moving to end, so this verb, which
        # it replaces, would start after it had: refused here instead, before anything is sent
        if preempts is not None and self.preempts != preempts and self._may_move(canonical, verb):
            reason = f"{name} did not start: {self.preempted_by}"
            self._emit(
                "gate", name=name, gate="preempted", outcome="refused", reason=reason, **after
            )
            raise Preempted(reason)
        # and the abort, for the same reason: a kill switch, a failed heartbeat or a drive
        # session's brake set while this verb read the body's state found it in nothing it could
        # cancel, and the verb sent its first twist after the abort that was meant to end it
        if self.abort.is_set() and canonical != "stop":
            self._emit(
                "gate", name=name, gate="abort", outcome="refused", reason="run aborted", **after
            )
            raise Aborted("run aborted")
        # and a nested call whose composite, or a verb above that, the executor ended while it
        # read the body's state: the composite's cancel can be lost in that read, and the call
        # would start on a leash of its own that nothing had ended (`_Leash`)
        above = _RUNNING_LEASH.get()
        if above is not None and above.why is not None and canonical != "stop":
            raise asyncio.CancelledError(above.why)
        # and the verdict, for the same reason: one recorded as infeasible, or withdrawn, while
        # this verb read the body's state (two MCP calls at once) found nothing to refuse, and a
        # walk the pilot had just judged the body could not do started after that judgement.
        # A person's own command stands for its verdict whatever was recorded meanwhile.
        if (
            self.require_verdict
            and source not in PERSONS
            and not self.cleared
            and not self._looks(canonical, verb)
        ):
            raise self._no_verdict(name, after)
        # and a call that a stop sent while it read the body's state has overtaken, for the reason
        # the gate before the confirm gives (`_overtaken`)
        if overtaken := self._overtaken(name, canonical, params, verb, stops, after):
            return overtaken
        if state is not None:
            self._read_abort_rules(name, canonical, state, after)
            # No precondition holds back the brake, whatever a manifest lists for it: every other
            # gate lets `stop` through on purpose, and a body a precondition refuses, or a flat
            # battery, is where it is wanted most. One listed for it answered `cannot stop` and
            # sent nothing.
            for pre in verb.preconditions if canonical != "stop" else ():
                refusal = pre(state)
                if refusal:
                    self._emit(
                        "gate",
                        name=name,
                        gate="precondition",
                        outcome="refused",
                        reason=refusal,
                        state=state.summary(),
                        **after,
                    )
                    return self._record(name, params, VerbResult.fail(f"cannot {name}: {refusal}"))
        return None

    async def _handed_over(
        self,
        name: str,
        canonical: str,
        params: dict[str, Any],
        verb: Verb,
        parsed: Any,
        *,
        sent: Tally,
        source: Source,
        timed: bool,
        stops: int | None,
    ) -> VerbResult:
        """A call past every check: the dry run's word for it, or the verb itself, and the
        brake's reading after it has gone out (`_run_admitted`)."""
        brake = canonical == "stop" and not self.dry_run
        if self.dry_run and not verb.read_only:
            self.log(f"[dry-run] would run {name}({parsed.model_dump()})")
            self._emit(
                "gate",
                name=name,
                gate="dry_run",
                outcome="skipped",
                reason=f"would run {name}, sent nothing",
                params=parsed.model_dump(),
            )
            return self._record(
                name, params, VerbResult.success(f"[dry-run] {name} not sent", dry_run=True)
            )

        self.log(f"→ {name}({parsed.model_dump()})")
        # Here, after every gate, the state read, the preconditions and the dry run's branch, so
        # a verb that was refused, a dry run and a read never count as the robot moving.
        may_be_first = self.first_motion_at is None and self._moves_now(canonical, verb)
        handed_over = time.monotonic()
        calls_by_then = self.budget.llm_calls if self.budget is not None else None
        robot_before = self._robot_now() if timed else None
        result: VerbResult | None = None
        try:
            result = await self._execute(
                verb,
                parsed,
                interruptible=canonical != "stop",
                source=source,
                name=name,
                stops=stops,
            )
        except TimeoutError as e:
            # the executor's own timeout has sent its stop already; a verb that raised one has not
            unstopped = e.unstopped if isinstance(e, _TimedOut) else await self._stop_after()
            result = VerbResult.fail(self._timed_out(name, verb, unstopped=unstopped))
        except SafetyStop:
            raise
        except Exception as e:  # a buggy verb must not take the run down un-stopped
            unstopped = await self._stop_after()
            stopped = (
                "stopped" if unstopped is None else f"the stop sent after it failed: {unstopped}"
            )
            said = f"{name} raised {type(e).__name__}: {e}"
            # a nested verb's failed read has said already that its stop did not land, and a
            # verb and its stop that raised alike are still two failures, each said
            once = unstopped is not None and "the stop sent after it failed" in said
            result = VerbResult.fail(said if once else f"{said}; {stopped}")
        finally:
            # Charged however the call ends. A call cancelled from outside, an MCP client that
            # went, or one aborted mid-segment leaves with no result, and its segment ran all
            # the while: without this a client that cancelled every call just short of its end
            # ran the policy for as long as it liked with nothing charged.
            if timed and self.budget is not None:
                self.budget.note_policy(_segment_seconds(result, robot_before, self._robot_now()))
            # And only once it is known: a verb past every gate can still send nothing, the way
            # the arm's `move_joints` answers a goal off its travel with the range. A verb that
            # says it worked is believed, since only `send_intent` is tallied and a rest move or
            # a skill goes by a method of its own.
            if (
                may_be_first
                and self.first_motion_at is None
                and ((result is not None and result.ok) or sent.accepted)
            ):
                self.first_motion_at = handed_over
                self.first_motion_llm_calls = calls_by_then
        if brake:
            # the read the brake did not wait for, which a run's `abort_when` rules still get
            self._read_abort_rules(name, canonical, await self._read_the_state())
        self.log(f"← {name}: {'ok' if result.ok else 'FAIL'} {result.summary}")
        return self._record(name, params, result)

    async def _stop_after_a_failed_read(self, e: Exception) -> None:
        """The stop after an admission's state read that raised `e`, and its failure said in
        `e`, which the caller raises."""
        # the one in-verb transport failure that used to send no stop. A link that
        # cannot report state cannot be trusted to be holding a zero twist either.
        unstopped = await self._stop_after()
        said = str(e)
        if (
            unstopped is not None
            and isinstance(e, TransportError)
            and len(e.args) == 1
            # once: an error a link raises again for every read would gain a clause each time
            and "the stop sent after it failed" not in said
        ):
            # and a stop that did not land is said beside the read, which is all a pilot or
            # a chat is told: the arm reads before it writes its hold, so over one gone
            # silent the stop held nothing. The error itself is kept, its class and code. A
            # wedged bus answers the read and the stop with one sentence, said once
            why = " the same way" if unstopped in said else f": {unstopped}"
            e.args = (f"{said}; the stop sent after it failed{why}",)

    def _read_abort_rules(
        self, name: str, canonical: str, state: RobotState, after: dict[str, str] | None = None
    ) -> None:
        """The run's `abort_when` rules read against an admission's `state`: one that fires is
        recorded and aborts the run, and refuses every verb but the brake."""
        try:
            self._check_abort_conditions(state)
        except Aborted as e:
            # a probe's too: the rule has aborted the run, and the record says why
            self._emit_anyway(
                "gate",
                name=name,
                gate="abort_when",
                outcome="fired",
                reason=str(e),
                state=state.summary(),
                **(after or {}),
            )
            # The brake is the one verb a rule that fired must still let through: the rule is
            # there to stop the body, and a `stop` it refused was a stop never sent, on a body
            # whose battery had just been read as too low to trust with anything else.
            if canonical != "stop":
                raise

    async def _execute(
        self,
        verb: Verb,
        parsed: Any,
        *,
        interruptible: bool,
        source: Source = "agent",
        name: str | None = None,
        stops: int | None = None,
    ) -> VerbResult:
        """Run one verb, racing it against the abort event as well as the clock.

        `asyncio.wait_for` knows only about the clock, so a kill switch, a Ctrl-C or a failed
        heartbeat used to set a flag that nothing looked at until the verb returned on its
        own. On a robot that meant the legs kept moving for the rest of a `go_to` — up to its
        whole timeout — after the human had already reached for the brake, and the verb's own
        10 Hz resend kept feeding the deadman throughout, so nothing else stopped it either.

        The `finally` owns both tasks, because `asyncio.wait` cancels nothing when it is
        itself cancelled. Without it an outer cancellation — an MCP client dropping the call,
        the second Ctrl-C this CLI documents — left the verb running with no stop: the log
        recorded that the verb had ended and then went on recording the intents it kept
        sending. `finished` says the block was left normally, so it is exactly the signal for
        "interrupted from outside" without catching `BaseException`.

        `stop` is never interruptible: it is what the abort is trying to achieve."""
        called = name or verb.name
        ctx = self.context(source, stops=stops, name=called)
        # below the leash of every verb this call runs inside, so it is ended with them; never
        # the brake's, which has to go out whatever was ended above it
        above = _RUNNING_LEASH.get() if interruptible else None
        leash = _Leash(
            ctx.transport,
            self.abort if interruptible else None,
            above,
            dry=self._dry_send(called) if self.dry_run else None,
        )
        leashed: Any = leash  # a transport by delegation, as `LoggedTransport` is
        inside = _RUNNING_LEASH.set(leash)  # the verb's task, and what it calls, run under it
        try:
            verb_task: asyncio.Task[VerbResult] = asyncio.ensure_future(
                verb.execute(
                    replace(
                        ctx,
                        transport=leashed,
                        run_verb=leash.calls(ctx.run_verb, self.registry.canonical)
                        if ctx.run_verb is not None
                        else None,
                    ),
                    parsed,
                )
            )
        finally:
            _RUNNING_LEASH.reset(inside)
        abort_task: asyncio.Task[bool] | None = None
        # the leash ended from elsewhere, by a pause, a newer command or a verb above this one,
        # is the executor ending the verb as well
        ended_task: asyncio.Task[str | None] = asyncio.ensure_future(leash.ended())
        waiting: set[asyncio.Future[Any]] = {verb_task, ended_task}
        if interruptible:
            abort_task = asyncio.ensure_future(self.abort.wait())
            waiting.add(abort_task)
            if self._may_move(self.registry.canonical(verb.name), verb):
                self._moving.add(verb_task)
                self._leashes[verb_task] = leash
        done: set[asyncio.Future[Any]] = set()
        finished = False
        unstopped: str | None = None
        try:
            done, _ = await asyncio.wait(
                waiting, return_when=asyncio.FIRST_COMPLETED, timeout=verb.timeout_s
            )
            finished = True
        finally:
            # the losers are always cancelled: a waiter left behind would otherwise accumulate
            # one task per verb for the life of the run
            self._moving.discard(verb_task)
            self._leashes.pop(verb_task, None)
            leash.detach()
            halted = self._halted.pop(verb_task, None)
            # A cancel of this call that lands while the verb is being ended (a second Ctrl-C, an
            # MCP client that gave up, a composite's own timeout) is kept, and raised once the
            # owed stop is out. These awaits used to sit in a suppress that ate it with the
            # verb's own cancel, and the caller went on as if it had not been cancelled: a
            # composite took its next step after its own timeout.
            cancelled = False
            for waiter in (abort_task, ended_task):
                if waiter is not None and not waiter.done():
                    waiter.cancel()
                    cancelled = await _waited({waiter}, None) or cancelled
            # ended by a verb above this one, and not by anything of its own: a cancel from above
            from_above = (
                halted is None
                and ended_task in done
                and not (abort_task is not None and abort_task in done)
            )
            # A verb a pause or a newer command ended is owed its stop even when it ended at once,
            # and one that ended by itself is owed none, even by a call cancelled after it.
            # Nothing the verb does from here can be trusted to end, so take the legs back. The
            # cancel says why, which a verb that keeps a record of its own reads off the
            # `CancelledError` it is handed (`watch`'s `ended`).
            owes_a_stop = verb_task not in done or halted is not None
            if owes_a_stop:
                if halted is not None:
                    why = halted[0]
                elif abort_task is not None and abort_task in done:
                    why = CANCELLED_BY_ABORT
                elif from_above:
                    why = leash.why or CANCELLED_FROM_OUTSIDE
                elif finished:
                    why = CANCELLED_BY_TIMEOUT
                else:
                    why = CANCELLED_FROM_OUTSIDE
                left, unstopped = await self._end_verb(
                    verb_task, leash, why, called, cancelled=cancelled
                )
                cancelled = cancelled or left
            # The gate after the stop, saying how it went: written before it, it said "a stop was
            # sent" over a stop that then failed unread, and a record that could not be written in
            # its place turned the cancelled call into a failed one. However the wait on the stop
            # ended, a cancel cutting it short included, it is written. One a pause or a newer
            # command ended is said once, below.
            if owes_a_stop and (cancelled or not finished or from_above) and halted is None:
                if unstopped is None:
                    stopped = "the verb was cancelled and a stop was sent"
                elif unstopped == STOP_ON_ITS_WAY:
                    stopped = f"the verb was cancelled and a stop was sent, {unstopped}"
                else:
                    stopped = (
                        f"the verb was cancelled, and the stop sent after it failed: {unstopped}"
                    )
                self._emit_on_the_way_out(
                    "gate",
                    name=called,
                    gate="cancelled",
                    outcome="fired",
                    reason=f"the call was cancelled; {stopped}",
                )
        if cancelled or from_above:
            raise asyncio.CancelledError(leash.why)
        if halted is not None:
            if self.abort.is_set():  # an abort that landed with the pause is what leaves
                self._aborted_mid_verb(called, unstopped)
            kind, why = halted
            if kind == CANCELLED_BY_PREEMPT:
                self._preempted_mid_verb(called, why, unstopped)
            return self._ended_by_pause(called, unstopped)
        if verb_task in done:
            if leash.not_sent and not verb_task.cancelled():
                # read, and set aside: a verb that raised on a call refused on a dry run, or on
                # what such a call answered, failed for the dry run and not for itself
                verb_task.exception()
            else:
                result = verb_task.result()
            if leash.not_sent:
                # what the verb says of itself would be about sends that went nowhere
                sent = leash.not_sent
                listed = sent[0] if len(sent) == 1 else ", ".join(sent[:-1]) + " and " + sent[-1]
                return VerbResult.fail(
                    f"[dry-run] {called} tried to send {listed}, and {DRY_RUN_SENDS_NOTHING} to "
                    "the body",
                    dry_run=True,
                    not_sent=list(sent),
                )
            return result
        if abort_task is not None and abort_task in done:
            self._aborted_mid_verb(called, unstopped)
        raise _TimedOut(unstopped)

    def _dry_send(self, called: str) -> Callable[[str], None]:
        """What records a send `called` made on a dry run, which went nowhere (`_Leash`)."""

        def refused(what: str) -> None:
            self._emit(
                "gate",
                name=called,
                gate="dry_run",
                outcome="refused",
                reason=f"{called} sent {what}, and {DRY_RUN_SENDS_NOTHING}",
            )

        return refused

    async def _end_verb(
        self,
        verb_task: asyncio.Task[VerbResult],
        leash: _Leash,
        why: str,
        called: str,
        *,
        cancelled: bool = False,
    ) -> tuple[bool, str | None]:
        """End a verb the executor is done with, for `why`, and send the stop owed after it.
        Returns whether a cancel of this call came meanwhile (`_waited`), and why the stop failed
        or is unconfirmed (`_answer_of`), or None.

        It used to be one cancel and then a wait with no end: a verb whose cancel was lost went on
        sending for as long as it ran, and the stop went out only once it had ended by itself.
        Then the stop waited up to twice `ENDING_S` for the verb, which protected nothing, since
        from `leash.end` nothing the verb sends but a stop is passed on, while a body with no
        deadman of its own (a rosbridge base, a ToddlerBot whose keepalive feeds its own) drove on
        its last twist. Now the stop goes out at once, and only then is the verb waited for, for
        the record and the call's return: cancelled again if it has not ended within `ENDING_S`,
        and left behind, saying so, if it has not ended `ENDING_S` after that (`_leave_behind`).
        A cancel of this call, or one that had come already (`cancelled`), ends the waiting:
        everything owed has been set going, and the caller raises the cancel."""
        leash.end(why)
        if not verb_task.cancelling():  # a pause or a newer command has cancelled it already
            verb_task.cancel(why)
        clock = asyncio.get_running_loop()
        stopped_at = clock.time()
        stopping = self._start_stop()
        # a send that had passed the leash and not finished as the stop went may write after it
        sending = leash.sending
        unstopped, cancelled = await self._answer_of(stopping, cancelled=cancelled)
        if unstopped is None:
            # a stop has landed: one of the verb's own after it could only land in whatever runs
            # next, so nothing of it goes through from here, a stop included. Not while that
            # stop failed or is unconfirmed, when the verb's own may be the one that lands.
            leash.seal()
        for again in (False, True):
            if again:
                verb_task.cancel(why)
            # on a clock of its own, so a wait that ended early does not cut the verb's time short
            until = clock.time() + ENDING_S
            while not cancelled and not verb_task.done() and (left := until - clock.time()) > 0:
                cancelled = await _waited({verb_task}, left)
            if cancelled or verb_task.done():
                break
        if verb_task.done():
            if not verb_task.cancelled():
                verb_task.exception()  # read, so nothing reports it later as never retrieved
        else:
            self._left_behind(verb_task, why, called, cancelled=cancelled)
        if sending:
            # A send that had passed the leash and not finished when the stop went may have
            # written after it, whatever it then returned, so the stop is sent again after it, and
            # a stop is the last thing sent. One that came back has written; one cut off in flight
            # (`cut_off`), whose write a thread may still be doing, or one still out, is given one
            # deadman window from the first stop, `ENDING_S`, before the stop goes again.
            unsure = leash.cut_off or leash.sending
            while unsure and not cancelled and (left := stopped_at + ENDING_S - clock.time()) > 0:
                cancelled = await _waited({asyncio.ensure_future(asyncio.sleep(left))}, left)
            unstopped, more = await self._answer_of(self._start_stop(), cancelled=cancelled)
            cancelled = cancelled or more
            if unstopped is None:
                leash.seal()
        return cancelled, unstopped

    def _left_behind(
        self, verb_task: asyncio.Task[VerbResult], why: str, called: str, *, cancelled: bool
    ) -> None:
        """A verb that has not ended: held until it does (`_leave_behind`), and said."""
        if cancelled:
            # the caller is going: told once more, and held until it ends, as one left behind is
            verb_task.cancel(why)
        else:
            text = (
                f"{called} had not ended {2 * ENDING_S:g} s after it was cancelled twice: it is "
                "left behind, no intent it sends is passed on, and the stop was sent without it"
            )
            self.log(text)
            self._emit_on_the_way_out("note", text=text)
        if not _leave_behind(verb_task):
            text = (
                f"{called} was left behind, and this Python cannot keep the run's end from waiting "
                "for it (asyncio has no tasks._unregister_task): the process may not exit until "
                "it ends by itself"
            )
            self.log(text)
            self._emit_on_the_way_out("note", text=text)

    def _emit_on_the_way_out(self, kind: str, **data: Any) -> None:
        """`_emit` where something is already on its way out, a cancel or a stop still owed: a
        record that cannot be written is said on the log, and never takes the place of that."""
        try:
            self._emit(kind, **data)
        except Exception as e:
            self.log(f"the record could not be written: {type(e).__name__}: {e}")

    # What a verb the executor ended comes to, once `_end_verb` has sent the stop owed after it
    # (`unstopped`, why that stop failed). The gate is written after the stop and can never take
    # its place: written first and unguarded, a record that could not be written (a full disk)
    # held the stop back and turned the abort, the newer command or the pause into an ordinary
    # failure of the verb's, which `abort_when` counts.

    def _aborted_mid_verb(self, called: str, unstopped: str | None) -> NoReturn:
        """What a verb the abort cancelled comes to: `Aborted`, the stop already sent, and the
        gate saying whether it landed."""
        self._emit_on_the_way_out(
            "gate",
            name=called,
            gate="abort",
            outcome="fired",
            reason="aborted mid-verb: the verb was cancelled and a stop was sent"
            if unstopped is None
            else f"aborted mid-verb: the verb was cancelled, and the stop sent after it failed: "
            f"{unstopped}",
        )
        # the abort is what leaves here, even when the stop after it failed: a heartbeat that
        # lost the link sets it, and that stop's own error used to leave in its place
        if unstopped is not None:
            raise Aborted(
                f"aborted mid-verb; the verb was cancelled, and the stop sent after it failed: "
                f"{unstopped}"
            )
        raise Aborted("aborted mid-verb; the verb was cancelled and a stop was sent")

    def _preempted_mid_verb(self, called: str, why: str, unstopped: str | None) -> NoReturn:
        """What a verb a newer command ended comes to: `Preempted` in the newer command's words,
        which the caller that asked for the verb reads as the verb's end and goes on from
        (`preempt`), the stop already sent."""
        self._emit_on_the_way_out(
            "gate",
            name=called,
            gate="preempted",
            outcome="fired",
            reason=f"ended by a newer command, {why}; a stop was sent"
            + (f", and it failed: {unstopped}" if unstopped else ""),
        )
        raise Preempted(
            f"{called} ended: {why}; a stop was sent"
            + (f", and it failed: {unstopped}" if unstopped else "")
        )

    def _ended_by_pause(self, called: str, unstopped: str | None) -> VerbResult:
        """What a verb a granted pause ended comes to: a result saying why, which the pilot reads
        as it reads any other verb's, the stop already sent."""
        held = max(0.0, self.paused_until - time.monotonic())
        self._emit_on_the_way_out(
            "gate",
            name=called,
            gate="paused",
            outcome="fired",
            reason="ended by a pause this robot granted a robot it met; a stop was sent"
            + (f", and it failed: {unstopped}" if unstopped else ""),
        )
        return VerbResult.fail(
            f"{called} ended: a robot this one met asked it to pause, and it is holding still "
            f"for {held:.0f} s more"
            + (f"; the stop sent after it failed: {unstopped}" if unstopped else ""),
            ended_by=PAUSE_ENDED,
        )

    # ── abort conditions the executor enforces itself ───────────────────────────────

    def _record(
        self, name: str, params: dict[str, Any], result: VerbResult, *, counts: bool = True
    ) -> VerbResult:
        """`result` into the history, and its failure into `abort_when`'s count of failures in a
        row, unless `counts` says it is not one of those (a person's typo in `roboglot drive`).
        A probe makes no call and records none (`_PROBING`)."""
        if _PROBING.get():
            return result
        self.history.append((name, params, result))
        key = self.registry.canonical(name)  # `walk` and `move` failures count together
        if result.ok:
            self.consecutive_failures[key] = 0
        elif result.data.get("ended_by") in (PAUSE_ENDED, STOP_ENDED) or not counts:
            # not the verb's failure, nor the pilot's: two pauses a robot met asked for used
            # to make two in a row of `move`, and `abort_when` ended the run on them, and a stop
            # from beside a verb that ends it is the brake working by the same rule
            pass
        else:
            n = self.consecutive_failures.get(key, 0) + 1
            self.consecutive_failures[key] = n
            limit = self.contract.repeat_failure_abort if self.contract else None
            if limit is not None and n >= limit:
                self.abort.set()
                self._emit(
                    "gate",
                    name=name,
                    gate="abort_when",
                    outcome="fired",
                    reason=f"{name} failed {n} times in a row",
                    last=result.summary,
                )
                raise Aborted(f"abort_when: {name} failed {n} times in a row")
        return result

    def _overtaken(
        self,
        name: str,
        canonical: str,
        params: dict[str, Any],
        verb: Verb,
        stops: int | None,
        after: dict[str, str] | None = None,
    ) -> VerbResult | None:
        """The result of a call that would move the body and that a stop has overtaken, or None.
        A stop that came while it waited on its gates or read the body's state, as a second MCP
        call does, ends it. A composite's call answers to the stops its composite answers to
        (`context`), so a stop sent between two of the composite's calls is the brake pulled on the
        whole of it. A verb that polls for it would end at its first check, but a kick or a scoop
        or a gripper sends its one intent without looking, so it is ended here, before it sends
        anything. A call that only looks or speaks runs, as it runs beside a pause or a newer
        command."""
        if (
            stops is None
            or self.stops == stops
            or canonical == "stop"
            or not self._may_move(canonical, verb)
        ):
            return None
        reason = f"{name} did not start: {STOPPED}"
        self._emit(
            "gate", name=name, gate="stopped", outcome="refused", reason=reason, **(after or {})
        )
        return self._record(
            name, params, VerbResult.fail(reason, ended_by=STOP_ENDED, interrupted=STOPPED)
        )

    def _check_abort_conditions(self, state: RobotState) -> None:
        if (reason := self._abort_rule_fired(state)) is not None:
            self.abort.set()
            raise Aborted(reason)

    def _abort_rule_fired(self, state: RobotState) -> str | None:
        """The `abort_when` rule `state` fires, in the words an abort says, or None. A reading
        and nothing else: the admission's gate and a verb's own reading mid-way
        (`_abort_rule_reader`) each decide when to set the abort."""
        if self.contract is None:
            return None
        threshold = self.contract.battery_abort_percent
        if (
            threshold is not None
            and state.battery_percent is not None
            and state.battery_percent < threshold
        ):
            return f"abort_when: battery {state.battery_percent:.0f}% below {threshold:g}%"
        return None


class Heartbeat:
    """Pings the transport every `period_s`. One miss → stop intent + abort."""

    def __init__(
        self,
        transport: RobotTransport,
        abort: asyncio.Event,
        *,
        period_s: float = 0.5,
        log: Callable[[str], None] = lambda _m: None,
        event_log: EventLog | None = None,
    ) -> None:
        self.transport = transport
        self.abort = abort
        self.period_s = period_s
        self.log = log
        self.event_log = event_log
        self.beats = 0
        self.failure: Exception | None = None
        self._task: asyncio.Task[None] | None = None

    async def _run(self) -> None:
        me = asyncio.current_task()
        while not self.abort.is_set():
            try:
                await self.transport.heartbeat()
                self.beats += 1
            except Exception as e:
                self.failure = e
                # Everything in here is best effort and the abort is in a `finally`, because
                # setting it is the one thing that must happen: a log call or an event sink that
                # raised used to kill this task outright, leaving the abort unset and the run
                # with no idea the link had gone. And the stop is only started in here, never
                # waited on: a `finally` after it was safe from a stop that raised but not from
                # one that hung on the link that had just failed, and until that stop came back
                # the running verb went on sending and a new verb passed the abort's gate.
                stopping: asyncio.Future[Any] | None = None
                try:
                    # "sending stop", not "stopping the robot": the heartbeat fails precisely
                    # when the link is in doubt, which is when a stop is least likely to
                    # arrive. What actually stops a body whose deadman we cannot reach is the
                    # deadman itself.
                    message = f"heartbeat failed: {e} — sending stop"
                    with contextlib.suppress(Exception):
                        self.log(message)
                    stopper: Any = self.transport
                    if self.event_log is not None:
                        with contextlib.suppress(Exception):
                            self.event_log.emit("note", text=message)
                        stopper = LoggedTransport(self.transport, self.event_log)
                    with contextlib.suppress(Exception):
                        stopping = asyncio.ensure_future(stopper.stop())
                        _STOPS_UNDER_WAY.add(stopping)
                        stopping.add_done_callback(_stop_gone)
                finally:
                    self.abort.set()
                if stopping is not None:
                    # Waited for no longer than a beat, the time this promises to notice a dead
                    # link in: nothing else waits on this stop once the abort is out, and one
                    # stuck on that link goes out when it can, in a task of its own.
                    await asyncio.wait({stopping}, timeout=self.period_s)
                return
            if me is not None and me.cancelling():
                # Python 3.11's wait_for, which a transport's wait for its answer goes through,
                # loses a cancel that lands as its call returns, and the cancel is still asked for.
                # Nothing else ends this loop, so it beat on for good and `stop`, the first line
                # of the run's teardown, waited on it
                raise asyncio.CancelledError
            await asyncio.sleep(self.period_s)

    def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name="roboglot-heartbeat")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            # a task that already died of its own exception re-raises it here, and this is
            # the first line of the loop's teardown: it must not take the stop, the final
            # state and the transcript's close down with it
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._task
            self._task = None


TYPED_AHEAD_S = 0.5
"""How long after a deliberate question is put an Enter is taken for one typed before it
(`KillSwitch.wait_for_enter(deliberate=True)`). The key thread reads whatever was already in the
terminal the moment it starts, and an Enter pressed twice, or pressed while the command was
still starting, used to answer the question that followed it before anybody could have read it:
one of those questions connects an arm and lets it drop. Nobody reads a sentence about a falling
arm and decides in half a second, and an Enter that comes too soon is passed by rather than
taken, so a person who did mean it presses it again, and is told when (`ENTER_TOO_SOON`)."""
ENTER_ALONE_S = 0.45
"""How long a bare Enter has to stand with no other Enter behind it to say yes to a question that
moves or drops an arm. A key pressed and held is a stream of Enters, and its first repeat can come
a second after the press, past any wait a person could be asked to sit through, so a wait before
it proves nothing. What gives it away is the next Enter. An answer takes this long to be sure of.
A no needs no such care."""
ENTER_QUIET_S = 2.0
"""How long since the last Enter a bare one has to come to say yes to the same question. Enter held
down from before the question is a stream with the key's own repeat between each, which is 2.5 a
second at the slowest Windows' keyboard settings go, and slower where somebody has asked for it.
A terminal says nothing of a key coming up, so a key set to repeat slower than this cannot be told
from somebody pressing it, and nothing that reads a terminal can do better. Somebody who stops and
presses it once more gets through, and whoever does not is told (`ENTER_TOO_SOON`)."""
ENTER_TOO_SOON = (
    "that Enter answered nothing, because it came too soon after the question or too close to "
    f"another: take your finger off it, wait {ENTER_QUIET_S:.0f} s and press it once"
)
"""Said once a wait, at the first Enter it passes by. A person who presses it again and again, as
somebody at an arm's jaws will, would otherwise never get through and never know why."""
TOO_SOON_ONLY: Literal["too soon only"] = "too soon only"
"""`wait_for_enter(deliberate=TOO_SOON_ONLY)`: the rules for an Enter too soon (`TYPED_AHEAD_S`,
`ENTER_ALONE_S`, `ENTER_QUIET_S`, `ENTER_TOO_SOON`) without the one that makes a line with
something typed on it a no. For `--by-hand`'s start, whose Enter takes hold of the arm where it
is and which has no no in it: there a line typed after the question starts the run, whatever it
holds, as it always did, and one typed ahead or held is passed by."""
Deliberate = bool | Literal["too soon only"]
"""What `deliberate` may be: False, True (both halves), or `TOO_SOON_ONLY`."""
ENTER_BEHIND_Q_S = 2.0
"""How long after a `q` at a console an Enter is taken for the old habit of finishing the line,
and answers nothing (`KillSwitch._watch_keys`). The press ends the run and the question that
opens the gripper is up a moment later, so this errs long: an Enter passed by costs a second
press of it, and one taken costs what was in the jaws."""
ASK_POLL_S = 0.05
"""How often a question put on the loop's thread looks for what else may have ended it
(`KillSwitch.ask`). An untimed wait there cannot be interrupted on Windows, Ctrl-C included."""
SIGNAL_LOOK_S = 0.25
"""The longest a signal waits to be acted on (`KillSwitch._look_for_a_signal`). Python acts on one
on the main thread between bytecodes, so one landing in the instant before the event loop goes to
sleep interrupts nothing: a Ctrl-C or a SIGTERM waited for the loop's next timer, however far off,
and under load that was seen at twenty seconds."""


class _Enter(NamedTuple):
    """One Enter the key thread read."""

    at: float
    """When it was read, which may be well after it came in (`waited`)."""
    quiet: float
    """How long since the Enter before it, whatever became of that one: the least it can have
    been, counted from the earliest this one can have come in."""
    text: str
    """What was typed on its line."""
    answers: bool
    """False for one that goes to no wait: behind a `q`, or ending the line of a `[y/N]`."""
    waited: float = 0.0
    """How long it may have been in the terminal before it was read: back to the last time the key
    thread looked at the console and found nothing there (`ConsoleKeys.read`). A key thread held up
    reads what came in meanwhile once it gets going again, and stamps all of it then. Nothing where
    it reads a stream, which cannot be looked at without a read, and is taken to keep up."""

    @property
    def earliest(self) -> float:
        """The earliest it can have come in."""
        return self.at - self.waited


OUTSTAYED_S = 5.0
"""How long after its close a process has for what is left, which is saying how it went. Past this
it is stuck on something, and a press gets out (`AfterTheClose`). Far longer than the milliseconds
that takes, and short enough for somebody still pressing to find the way out."""
EXIT_PRESSED = 130
"""The exit code of a process a Ctrl-C ended or had a say in: 128 and the signal, as a shell has."""
OUTSTAYED = (
    "\nRoboglot has finished with the robot, and something it was waiting on has not come back, so "
    "this Ctrl-C ended it. What a run did is in its folder.\n"
)


class AfterTheClose:
    """What a Ctrl-C does once a command has closed what it had open, for the rest of the process.

    A person still pressing has no way to tell the end of a run from its last moments, and a press
    past the switch used to raise through the printing of the verdict, or on Windows to end the
    process in the middle of its exit. So it does nothing to what is left, and it is remembered,
    since it was still somebody saying stop: `roboglot run a && roboglot run b` must not go on to
    move an arm after one (`exit_code`). What cannot be taken from them is the way out. asyncio's
    pool and the exit both wait for their threads, so a call on another body that never came back
    would otherwise leave nothing but `kill`: once the process has outstayed its close, a press ends
    it where it stands, with the record already written.

    On Windows a thread's join does not hear a signal at all, so the last stretch of an exit stuck
    that way still takes closing the window, as it always did. The interpreter puts back the
    default for a handler like this one as it ends, which is the `0xC000013A`, so `atexit`, which
    comes after those joins and before that, swaps it for one the interpreter leaves in place."""

    heard = 0
    """The presses since a close, in this process."""

    def __init__(self) -> None:
        self.since = time.monotonic()
        atexit.register(self._deaf_to_the_end)

    def __call__(self, _signum: int, _frame: Any) -> None:
        AfterTheClose.heard += 1
        if time.monotonic() - self.since >= OUTSTAYED_S:
            with contextlib.suppress(OSError):
                os.write(2, OUTSTAYED.encode())
            os._exit(EXIT_PRESSED)

    def _deaf_to_the_end(self) -> None:
        with contextlib.suppress(ValueError):
            if signal.getsignal(signal.SIGINT) is self:
                signal.signal(signal.SIGINT, signal.SIG_IGN)

    def took_its_time(self) -> None:
        """What was left was slow on purpose: no sign of being stuck."""
        self.since = time.monotonic()

    @classmethod
    def not_stuck_yet(cls) -> None:
        """`took_its_time`, on whichever one is in place, for what has more to say than a verdict
        panel and has got as far as saying it (`roboglot doctor`)."""
        handler = signal.getsignal(signal.SIGINT)
        if isinstance(handler, cls):
            handler.took_its_time()

    @classmethod
    def exit_code(cls, code: Any) -> Any:
        """`code`, unless it is a success with a press heard after the close."""
        return EXIT_PRESSED if cls.heard and code in (0, None) else code


class _StreamLines:
    """The one reader of a stdin that is no terminal, a pipe or a file: a line for each question.

    `input()` read those on the loop's own thread, with nothing to bound it, so while a question
    waited on a quiet pipe the heartbeat did not beat, nothing else ran, an abort could not take
    it back, and on Windows a Ctrl-C was not seen until a line or the end of input came. Here a
    thread reads, and only when a question asks for a line, so what the pipe delivers is read
    as `input()` read it: through `sys.stdin`'s own buffer, a line a question and in order, with
    nothing read ahead. A question taken back while its read is pending leaves that read to the
    next question, so no line is lost or read twice. The end of input, or a read that raised,
    is what every question after it gets, as `input()` raised it again each time.

    Lines go to whoever waits for one in the order they began to wait, two questions open at
    once included, and a read is asked of the thread for each of them. Once this reader exists
    it owns stdin: `sys.stdin` reads through it (`_ThroughTheReader`), so an `input()` later in
    the process gets the line a question taken back left pending, and not the one after it.

    A pending read on a pipe cannot be interrupted on Windows, so the thread is a daemon one,
    and the interpreter ends without waiting for it."""

    def __init__(self, stream: Any) -> None:
        self._stream = stream
        self._wanted = threading.Semaphore(0)
        self._read: queue.Queue[str | BaseException] = queue.Queue()
        self._lock = threading.Lock()
        self._asked = 0
        """Reads asked of the thread whose line nobody has taken yet, under the lock."""
        self._waiting: deque[object] = deque()
        """Whoever waits for a line, in the order they began to, under the lock."""
        self._rest = ""
        """What a read of a given size left of a line (`put_back`), the next reader's whoever
        that is, a question included, before any line the thread reads, under the lock."""
        self._ended: BaseException | None = None
        threading.Thread(target=self._read_lines, name="roboglot-stdin-lines", daemon=True).start()

    def _read_lines(self) -> None:
        while True:
            self._wanted.acquire()
            try:
                line = self._stream.readline()
            except BaseException as e:  # handed to the question waiting, which raises it
                self._read.put(e)
                return
            self._read.put(line if line else EOFError("EOF when reading a line"))
            if not line:
                return

    def _join(self, who: object) -> None:
        with self._lock:
            self._waiting.append(who)
            if self._asked + (1 if self._rest else 0) < len(self._waiting):
                self._asked += 1
                self._wanted.release()

    def put_back(self, rest: str) -> None:
        """What a read of a given size did not take of its line, for the next reader."""
        with self._lock:
            self._rest = rest + self._rest

    def _take(self, who: object) -> tuple[bool, str | BaseException]:
        """Whether `who` has its line, and the line (with its newline) or what it raises."""
        with self._lock:
            if self._rest and self._waiting and self._waiting[0] is who:
                rest, self._rest = self._rest, ""
                self._waiting.popleft()
                return True, rest
            if self._ended is not None:
                with contextlib.suppress(ValueError):
                    self._waiting.remove(who)
                return True, self._ended
            if not self._waiting or self._waiting[0] is not who:
                return False, ""
            try:
                got = self._read.get_nowait()
            except queue.Empty:
                return False, ""
            self._waiting.popleft()
            self._asked -= 1
            if isinstance(got, BaseException):
                self._ended = got
            return True, got

    def _leave(self, who: object) -> None:
        """`who` waits no more: taken back. A read asked for it stays asked, and its line is the
        next one's who waits."""
        with self._lock, contextlib.suppress(ValueError):
            self._waiting.remove(who)

    async def line(self) -> str:
        """The next line, without its newline, for a question on the run's loop, which goes on
        while it waits; `EOFError` at the end of input, as `input()` raises it."""
        who = object()
        self._join(who)
        try:
            while True:
                done, got = self._take(who)
                if done:
                    break
                await asyncio.sleep(ASK_POLL_S)
        except BaseException:
            self._leave(who)
            raise
        if isinstance(got, BaseException):
            raise got
        # as `input()` returns it: the line without its newline
        return got[:-1] if got.endswith("\n") else got

    def readline(self) -> str:
        """The next line, as a stream's `readline` gives it, for whatever reads stdin outside a
        question (`_ThroughTheReader`): its newline kept, and "" at the end of input."""
        who = object()
        self._join(who)
        try:
            while True:
                done, got = self._take(who)
                if done:
                    break
                time.sleep(ASK_POLL_S)
        except BaseException:
            self._leave(who)
            raise
        if isinstance(got, EOFError):
            return ""
        if isinstance(got, BaseException):
            raise got
        return got


class _ThroughTheReader:
    """`sys.stdin` once a question has read it through `_StreamLines`: every line read in this
    process from then on comes through that one reader, in order, `input()`'s and a prompt's
    included, and everything else is the stream's own.

    It reads as a text stream does every way a program reads one, `readline`, iteration,
    `read` and `readlines`, each through the reader, so the line a question taken back left
    with it is the next one read, however it is read. What a read of a given size leaves of a
    line goes back to the reader, the next reader's, a question included; a size of 0 reads
    nothing."""

    def __init__(self, stream: Any, lines: _StreamLines) -> None:
        self._stream = stream
        self.lines = lines

    def readline(self, size: int = -1) -> str:
        if size == 0:
            return ""
        line = self.lines.readline()
        if 0 < size < len(line):
            self.lines.put_back(line[size:])
            line = line[:size]
        return line

    def read(self, size: int = -1) -> str:
        got: list[str] = []
        have = 0
        while size < 0 or have < size:
            line = self.readline(-1 if size < 0 else size - have)
            if not line:
                break
            got.append(line)
            have += len(line)
        return "".join(got)

    def readlines(self, hint: int = -1) -> list[str]:
        lines: list[str] = []
        have = 0
        while hint <= 0 or have < hint:
            line = self.readline()
            if not line:
                break
            lines.append(line)
            have += len(line)
        return lines

    def __iter__(self) -> _ThroughTheReader:
        return self

    def __next__(self) -> str:
        line = self.readline()
        if not line:
            raise StopIteration
        return line

    def __getattr__(self, name: str) -> Any:
        return getattr(self._stream, name)


_STREAM_LINES: _StreamLines | None = None
"""The reader of the stream stdin was when a question was last put on one, kept for the process,
so a second run's switch in the same process reads through it and not through a reader of its
own beside the first's pending read."""


def _lines_of(stream: Any) -> _StreamLines:
    global _STREAM_LINES
    if isinstance(stream, _ThroughTheReader):
        return stream.lines
    if _STREAM_LINES is None or _STREAM_LINES._stream is not stream:
        _STREAM_LINES = _StreamLines(stream)
        if sys.stdin is stream:
            # from here on every read of stdin goes through the one reader
            sys.stdin = _ThroughTheReader(stream, _STREAM_LINES)  # type: ignore[assignment]
    return _STREAM_LINES


class KillSwitch:
    """Ctrl-C or `q` → abort, and Enter → the one thing a run ever waits for a person to do.

    Works on Windows too (no loop.add_signal_handler there). The key thread is the only
    reader of stdin Roboglot starts, so Enter is noticed here rather than with an `input()` of
    its own: two readers on one terminal would race for the same keystroke, and the one that
    lost would hang on a line the other had already taken.
    """

    keyless = 0
    """How many switches in this process are installed and read no key (`q_is_read`)."""

    def __init__(self, abort: asyncio.Event, log: Callable[[str], None] = lambda _m: None) -> None:
        self.abort = abort
        self._line_wanted = threading.Event()
        self._lines: queue.Queue[str] = queue.Queue()
        self._asking = threading.Lock()
        self.log = log
        self.presses = 0
        """How many times the switch has fired, for anyone counting rather than waiting."""
        self.pressed = asyncio.Event()
        """The switch fired. Unlike `abort`, which stays set for the rest of the run, this one
        is cleared by whoever waits on it, so a wait can end on a *fresh* Ctrl-C without being
        ended immediately by one that already happened."""
        self.entered = asyncio.Event()
        """Somebody pressed Enter. Set from the key thread, cleared by whoever waits on it."""
        self.keys_ended = asyncio.Event()
        """Stdin is finished, so no keystroke is ever coming.

        A wait for a person has to end on this or it never ends at all. The run that needs it
        is the one that has an arm limp in somebody's hands: without it, a terminal closed at
        the wrong moment leaves Roboglot waiting for ever, holding nothing."""
        self._loop: asyncio.AbstractEventLoop | None = None
        self._previous: Any = None
        self._thread: threading.Thread | None = None
        self._keys: ConsoleKeys | None = None
        """The console a key at a time, where there is one to read (`roboglot.keys`)."""
        self._uninstalled = False
        self._keyless = False
        """Installed with no key thread, and counted in `keyless`."""
        self.declined = False
        """The last deliberate wait ended on a line with something typed on it, which is a no:
        `n`, `no`, anything but a bare Enter (`wait_for_enter(deliberate=True)`)."""
        self._enters: deque[_Enter] = deque(maxlen=32)
        """Each Enter the key thread read, newest last. Written by the key thread and read by a
        deliberate wait, under the lock."""
        self._enters_lock = threading.Lock()
        self._last_enter = -math.inf
        self._looked: float | None = None
        """The last moment the key thread looked at the console and found no key there, under the
        lock: what it reads after that came in after it. None where it reads a stream, and then a
        bare Enter stands alone by the clock, as it always did there."""
        self._alone = asyncio.Event()
        """A look at the console has just shown the newest Enter standing alone long enough to say
        yes (`ENTER_ALONE_S`). Set from the key thread, so that a deliberate wait looks again then
        rather than at its next turn, and cleared by that wait."""
        self._alone_said = False
        """`_alone` has been set for the newest Enter, under the lock."""
        self._line: list[str] = []
        """What has been typed since the last Enter, under the lock."""
        self._last_key = -math.inf
        self._looking: asyncio.TimerHandle | None = None
        self.unanswered: str | None = None
        """Why the last `ask` came back with no line anybody typed, or None where one did."""

    def _heard_enter(self, text: str, *, answers: bool = True) -> None:
        """An Enter the key thread read, recorded with what was typed on its line and when, and
        only then announced, so a wait woken by it finds the line it answers already there. One
        that answers nothing is recorded too, since it is still the Enter before the next.

        Its quiet counts from the earliest it can have come in. Read late, the one repeat of a
        press held a moment too long looked like a press of its own after a quiet, and with nothing
        behind it, it said yes."""
        now = time.monotonic()
        with self._enters_lock:
            waited = 0.0 if self._looked is None else now - self._looked
            came = now - waited
            quiet = math.inf if self._last_enter == -math.inf else came - self._last_enter
            self._enters.append(_Enter(now, quiet, text, answers, waited))
            self._last_enter = now
            self._alone_said = False
        if answers:
            self._announce(self.entered)

    def _nothing_waiting(self, looked: float) -> None:
        """The key thread looked at the console at `looked` and found no key there, so what it reads
        next came in after that moment (`ConsoleKeys.read`). The first such look to show the newest
        Enter standing alone long enough to say yes wakes a deliberate wait (`_alone`)."""
        with self._enters_lock:
            self._looked = looked
            newest = self._enters[-1] if self._enters else None
            alone = (
                newest is not None
                and newest.answers
                and not newest.text
                and not self._alone_said
                and looked - newest.at >= ENTER_ALONE_S
            )
            if alone:
                self._alone_said = True
        if alone:
            self._announce(self._alone)

    def _line_since(self, since: float, *, typed_is_a_yes: bool = False) -> str | None:
        """What was typed on the first line from `since` on that says anything: one with something
        on it, a no, as soon as it is read, or a bare one that comes after quiet (`ENTER_QUIET_S`)
        and stands alone (`ENTER_ALONE_S`), a yes, once it is sure to have. None where neither has
        been read yet. Enter held down from before a question answered it the moment the question's
        half second was up, and a no that had to stand alone could be lost, to the next bare
        Enter's yes.

        Alone is what the key thread has seen, never the clock alone. Held up just after it read
        the first repeat of a key held down, it had read nothing more by the time the wait looked,
        or it read the next repeat late and stamped it then, and either way that repeat stood
        alone and said yes to the question that drops an arm. So at a console an Enter has stood
        alone once the key thread has looked and found nothing behind it `ENTER_ALONE_S` after it
        was read, and the Enter after it is measured from the earliest it can have come in. So is
        `since` for a bare one: typed too soon and read late, past the question's half second, it
        was taken for one read in good time. A line with something on it goes by when it was read,
        since a no needs none of this care: passed by for having perhaps come too soon, it was lost
        and said nothing, and the next bare Enter was a yes to what they had just refused."""
        now = time.monotonic()
        with self._enters_lock:
            enters = list(self._enters)
            looked = now if self._looked is None else self._looked
        for n, enter in enumerate(enters):
            # a typed line by when it was read, where it is a no; by the earliest it can have
            # come in where it is a yes (`typed_is_a_yes`), as a bare Enter always is, so a line
            # already waiting when the question was put never counts, however late it is read
            came = enter.at if enter.text and not typed_is_a_yes else enter.earliest
            if not enter.answers or came < since:
                continue
            after = enters[n + 1].earliest if n + 1 < len(enters) else looked
            alone = enter.quiet >= ENTER_QUIET_S and after - enter.at >= ENTER_ALONE_S
            if enter.text or alone:
                return enter.text
        return None

    def _passed_by(self, asked_at: float, *, lines: bool = False) -> bool:
        """Whether a bare Enter read since the question was put at `asked_at` has been passed by
        for good: too soon after the question or too close behind another, which is also how one
        with another close behind it is found. One still waiting to be sure of is not. With
        `lines`, a line with something typed on it read since the question and too soon after it,
        by the earliest it can have come in, counts as well, for a wait where such a line would
        have been a yes (`TOO_SOON_ONLY`)."""
        with self._enters_lock:
            enters = list(self._enters)
        for enter in enters:
            if not enter.answers or enter.at < asked_at:
                continue
            if enter.text:
                if lines and enter.earliest < asked_at + TYPED_AHEAD_S:
                    return True
                continue
            if enter.earliest < asked_at + TYPED_AHEAD_S or enter.quiet < ENTER_QUIET_S:
                return True
        return False

    def _fire(self, why: str) -> None:
        # the stop first and the words after: a line that cannot get out must not hold up the stop,
        # nor raise into what the signal interrupted, as a write to stderr it landed inside does
        self.presses += 1
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self.abort.set)
            self._loop.call_soon_threadsafe(self.pressed.set)
        with contextlib.suppress(Exception):
            self.log(f"kill switch: {why} — cancelling the verb and stopping the robot")

    def _on_sigint(self, _signum: int, _frame: Any) -> None:
        # The first Ctrl-C is the orderly one: it aborts, which cancels the running verb and
        # sends a stop. Handing the signal back means a second one is a plain KeyboardInterrupt
        # again, so a human who is not convinced the first worked is never stuck holding a key
        # that Roboglot has quietly swallowed.
        self._restore()
        self._fire("Ctrl-C")

    def _press_and_stay(self, _signum: int, _frame: Any) -> None:
        self._fire("Ctrl-C")

    def stay_to_the_close(self) -> None:
        """From here to `uninstall` a Ctrl-C does what `q` does and nothing else.

        For a run that has somebody at the arm: all of a `--by-hand` run, and any other from the
        question about a fold that missed on to the close. A hand near the jaws presses again and
        again, and a press past the first used to raise through whatever it landed on, past the
        close, `run_end` and the summary, and left an energised arm with no record of what became
        of it. Everything from here ends on a press or has a deadline of its own.
        `Ctrl-\\` where there is one, or closing the window, is what is left for somebody who has
        to kill it, and the power switch for the arm."""
        with contextlib.suppress(ValueError):  # not the main thread, so nothing to catch it on
            before = signal.signal(signal.SIGINT, self._press_and_stay)
            if self._previous is None:
                self._previous = before

    def _watch_keys(self) -> None:
        """Read stdin until it ends, whatever the run is doing.

        It used to stop at the first `q` and at the abort flag, which was enough when the only
        keystroke that meant anything was the one that ended the run. A run that hands the arm
        to a person waits for Enter after the abort may already be set, and again in its own
        teardown, so the reader has to outlive both.

        Every Enter is recorded with what was typed on its line (`_heard_enter`), because a
        question that drops an arm takes only a bare one, and a terminal that ends its lines
        with `\\r\\n` sends one Enter, not two.

        At a console it reads keys as they are pressed (`roboglot.keys`), so `q` fires the switch
        with no Enter behind it, which is what every line that names it has always promised. It
        is no part of the line then, or the Enter that answers a question a minute later would
        read as a line with something typed on it. An Enter close behind it is the old habit of
        finishing the line, and is passed by (`ENTER_BEHIND_Q_S`), or the question the press
        brought on would be answered yes by the hand still finishing it. Where it reads a stream
        and not a console, `q` comes with the rest of its line and is part of it, and a wait never
        takes that line's Enter for an answer (`wait_for_enter`).

        A console says when it has no key waiting (`_nothing_waiting`), which is what a deliberate
        question's yes is measured by. Until it first has, nothing read is known to have come in
        after anything: what was in it as the thread started may have been typed long before."""
        keys = self._keys
        read: Callable[[], str]
        if keys is not None:
            with self._enters_lock:
                self._looked = -math.inf
            read = functools.partial(keys.read, self._nothing_waiting)
        else:
            read = functools.partial(sys.stdin.read, 1)
        typed: list[str] = []
        line = self._line
        previous = ""
        q_at = -math.inf
        try:
            while True:
                ch = read()
                if not ch:
                    if not self._uninstalled:
                        self._announce(self.keys_ended)
                    return
                if keys is not None:
                    if ch in ("\b", "\x7f"):
                        with self._enters_lock:
                            edited = typed if self._line_wanted.is_set() else line
                            took = bool(edited)
                            if took:
                                edited.pop()
                        if took:
                            keys.erase()
                        continue
                    if ch == "\x03":  # Ctrl-C, should it land while the console is being read
                        self._fire("Ctrl-C")
                        continue
                if self._line_wanted.is_set():
                    # Somebody is being asked a question, so every key belongs to the answer.
                    # This reader cannot stand aside while a prompt runs its own `input()`: it
                    # is already blocked inside `read(1)` and would take the first character of
                    # the reply, and the prompt would then wait for a newline this thread had
                    # already swallowed. So it collects the line and hands it over instead.
                    if ch in ("\r", "\n"):
                        self._heard_enter("", answers=False)
                        self._lines.put("".join(typed))
                        typed.clear()
                    else:
                        typed.append(ch)
                    continue
                typed.clear()
                if ch in ("\r", "\n"):
                    with self._enters_lock:
                        text = "".join(line)
                        line.clear()
                        # behind the `q` unless it cannot have come in that soon after it: read
                        # late, the habit went to the question the `q` had brought on. A line with
                        # something on it goes by when it was read, as it does at the question
                        came = time.monotonic() if text or self._looked is None else self._looked
                    behind_a_q = keys is not None and came - q_at < ENTER_BEHIND_Q_S
                    if not (ch == "\n" and previous == "\r"):
                        self._heard_enter(text, answers=not behind_a_q)
                    previous = ch
                    continue
                previous = ch
                pressed_q = ch.strip().lower() == "q"
                with self._enters_lock:
                    self._last_key = time.monotonic()
                    if keys is None or not pressed_q:
                        line.append(ch)
                if pressed_q:
                    q_at = time.monotonic()
                    self._fire("'q' pressed")
                    if keys is not None:
                        keys.newline()
        except Exception:
            self._announce(self.keys_ended)
            return

    def ask(self, prompt: str) -> str:
        """Put a question to the person at the terminal and return the line they typed.

        Every prompt a run makes goes through here rather than through `input()`, because the
        key thread is reading the same terminal and whichever of the two took a character
        first kept it. A confirmation gate asked on a real terminal therefore waited for a
        newline the key thread had already swallowed, which is a hang with a robot mid-verb.

        Falls back to `input()` wherever no key thread is running, which is every caller with
        no terminal and every test, so nothing that was not already racing changes.

        A press answers no, and so does the key thread ending under the question. An untimed wait
        here sat on the loop's thread with nothing to wake it once the thread had gone, and on
        Windows it could not even be interrupted, so a question nobody could answer ended only
        when somebody killed the process. A process in the terminal's background answers no as
        well, since reading the terminal from there stops the whole process."""
        self.unanswered = None
        if in_the_background(sys.stdin):
            self.unanswered = "(this process is in the terminal's background: no)"
            return ""
        thread = self._thread
        if thread is None or not thread.is_alive():
            if self._keys is not None and not self._uninstalled:
                # a console the key thread had out of its line mode, and nothing reading it now
                self.unanswered = "(no key could be read: no)"
                return ""
            return input(prompt)
        with self._asking:
            while not self._lines.empty():  # anything typed before the question was asked
                self._lines.get_nowait()
            presses = self.presses
            self._line_wanted.set()
            try:
                sys.stderr.write(prompt)
                sys.stderr.flush()
                while True:
                    try:
                        return self._lines.get(timeout=ASK_POLL_S)
                    except queue.Empty:
                        if self.presses != presses:
                            self.unanswered = "(the kill switch ended the question: no)"
                            return ""
                        if not thread.is_alive():
                            self.unanswered = "(no key could be read: no)"
                            return ""
            finally:
                self._line_wanted.clear()

    def reads_a_stream(self) -> bool:
        """Whether a question this switch puts is read off a stdin that is no terminal, a pipe
        or a file, by `ask_async`: installed, with no key thread and no console under it. Where
        this is False `ask` reads it, as it always has."""
        stdin = sys.stdin
        return (
            self._loop is not None
            and not self._uninstalled
            and self._thread is None
            and self._keys is None
            and stdin is not None
            and not stdin.isatty()
        )

    async def ask_async(self, prompt: str) -> str:
        """`ask`, for a stdin that is a pipe or a file (`reads_a_stream`): the prompt written
        where `input()` writes it, and the line read by the stream's one reader while the loop
        goes on (`_StreamLines`). The end of input raises `EOFError`, as `input()` does. A
        question with no deadline, as one at a terminal has none, taken back by cancelling it,
        which the run's abort does (`roboglot.answers.answered`)."""
        self.unanswered = None
        lines = _lines_of(sys.stdin)
        sys.stdout.write(prompt)
        sys.stdout.flush()
        return await lines.line()

    def _announce(self, event: asyncio.Event) -> None:
        """Set an event from the key thread, which is not the loop's thread."""
        if self._loop is not None:
            with contextlib.suppress(RuntimeError):  # the loop has already closed
                self._loop.call_soon_threadsafe(event.set)

    async def wait_for_enter(
        self,
        *,
        timeout_s: float | None = None,
        until_abort: bool = True,
        deliberate: Deliberate = False,
        said: Callable[[str], None] | None = None,
    ) -> bool:
        """Wait for Enter. True if it came, False if anything else ended the wait.

        `until_abort` is the difference between the two waits one run can make. Before the
        first turn the abort flag is clear, so watching it is how Ctrl-C gets out of a wait for
        somebody who has walked away. In a teardown it is already set on every run a person
        ended, and watching it there would skip the wait on exactly the runs most likely to
        have something still in the gripper. That one watches `pressed` instead, which a
        waiter clears on the way in, so a fresh Ctrl-C ends it and the stale flag does not.

        Nothing here reads stdin: the key thread is the only reader, and this waits on what it
        sets. A wait on a machine with no key thread (no terminal) ends on its timeout, which
        is why a caller who needs an answer checks for a terminal before asking for one, and on
        `keys_ended` where the thread ran and stdin then finished under it.

        A press of the kill switch during the wait is never an Enter, whatever came with it. A
        stream that is no console hands `q` to the key thread with the Enter that ends its line,
        so the press and the Enter arrive together, and the wait used to answer True when both
        were in by the time it looked: `q`, the key that stops the run, taken for yes.

        `deliberate` is for a question whose Enter moves an arm or drops it (`roboglot.release`).
        Only a bare Enter answers it. A line with anything typed on it, `n` the commonest, is a
        no, and the wait ends saying so (`declined`), though not a key from before the question,
        which may be a stray press from minutes ago; an Enter read within `TYPED_AHEAD_S` of the
        question being put was typed before anybody could have read it, so the wait goes on past
        it; and so it does past one that does not stand alone (`ENTER_ALONE_S`) or that comes close
        behind another (`ENTER_QUIET_S`), a key held down or pressed twice. `said` is told of the
        first it passes by (`ENTER_TOO_SOON`).

        `TOO_SOON_ONLY` is the second half of that alone, for a question that has no no in it
        (`--by-hand`'s start): an Enter too soon is passed by in the same way, and a line with
        something typed on it is read as the Enter it always was there, a yes once it comes
        after the question's `TYPED_AHEAD_S` and passed by before that, never a no."""
        loop = asyncio.get_running_loop()
        asked_at = time.monotonic()
        presses = self.presses
        typed_is_a_no = deliberate is True
        self.declined = False
        if deliberate:
            with self._enters_lock:
                if asked_at - self._last_key >= TYPED_AHEAD_S:
                    self._line.clear()
        self.entered.clear()
        self.pressed.clear()
        self._alone.clear()
        deadline = None if timeout_s is None else loop.time() + timeout_s
        while True:
            answer = None
            if deliberate and typed_is_a_no:
                answer = self._line_since(asked_at + TYPED_AHEAD_S)
            elif deliberate:
                answer = self._line_since(asked_at + TYPED_AHEAD_S, typed_is_a_yes=True)
            if answer is not None:
                if self.presses > presses:
                    return False
                if answer and typed_is_a_no:
                    self.declined = True
                    return False
                return True
            if (
                deliberate
                and said is not None
                and self._passed_by(asked_at, lines=not typed_is_a_no)
            ):
                said(ENTER_TOO_SOON)
                said = None
            left = None if deadline is None else deadline - loop.time()
            if left is not None and left <= 0:
                return False
            # a deliberate wait comes back to see whether an Enter has stood alone for long enough
            wake = left
            if deliberate:
                wake = ENTER_ALONE_S if left is None else min(left, ENTER_ALONE_S)
            others = [
                asyncio.ensure_future((self.abort if until_abort else self.pressed).wait()),
                # nobody is going to press anything, so the caller is owed that answer rather
                # than a wait that never returns
                asyncio.ensure_future(self.keys_ended.wait()),
            ]
            watched = [asyncio.ensure_future(self.entered.wait()), *others]
            if deliberate:
                watched.append(asyncio.ensure_future(self._alone.wait()))
            try:
                await asyncio.wait(watched, timeout=wake, return_when=asyncio.FIRST_COMPLETED)
                ended = any(task.done() for task in others)
            finally:
                for task in watched:
                    task.cancel()
                    # awaited, so a cancelled task is never left for the loop to complain about
                    with contextlib.suppress(asyncio.CancelledError, Exception):
                        await task
            if self.presses > presses:
                return False
            if not deliberate:
                return self.entered.is_set()
            # the keys ending takes nothing back from a yes a look has just made sure of
            aborted = until_abort and self.abort.is_set()
            sure = self._alone.is_set() and self.keys_ended.is_set() and not aborted
            if ended and not self.entered.is_set() and not sure:
                return False
            # An Enter was read, one has stood alone, or the time came to look again. Whether one
            # answers is the check at the top: one read too soon after the question was put, or
            # that does not stand alone, is passed by, and the wait goes on for the next.
            self.entered.clear()
            self._alone.clear()

    def _look_for_a_signal(self) -> None:
        """Nothing to do but wake: Python runs a signal's handler on the way back to sleep."""
        if self._loop is not None and not self._uninstalled:
            self._looking = self._loop.call_later(SIGNAL_LOOK_S, self._look_for_a_signal)

    def install(self, *, keys: bool = True) -> None:
        self._loop = asyncio.get_running_loop()
        with contextlib.suppress(ValueError):  # not the main thread
            self._previous = signal.signal(signal.SIGINT, self._on_sigint)
        self._look_for_a_signal()
        if (
            keys
            and sys.stdin is not None
            and sys.stdin.isatty()
            and not in_the_background(sys.stdin)
        ):
            self._keys = console_keys(sys.stdin)
            self._thread = threading.Thread(
                target=self._watch_keys, name="roboglot-keys", daemon=True
            )
            self._thread.start()
        else:
            # Nobody is reading the keyboard, so no keystroke is ever coming, and a wait for
            # one has to know that rather than sit there. The caller that waits checks for a
            # terminal first, but the two checks are made at different moments and by different
            # code, and the cost of them disagreeing is a run stopped for ever with an arm limp
            # in somebody's hands.
            self.keys_ended.set()
            if not self._keyless:
                self._keyless = True
                KillSwitch.keyless += 1

    @classmethod
    def q_is_read(cls) -> bool:
        """Whether `q` stops a run in this process, for what a run says it can be stopped with:
        False while a switch is installed that reads no key, on a stdin that is a pipe or a file
        (`ASKING_AGAIN`). Where no switch is installed nothing is known, and what is said stays
        as it always was."""
        return cls.keyless == 0

    def _restore(self, *, nothing_left_to_stop: bool = False) -> None:
        if self._previous is not None:
            with contextlib.suppress(ValueError):
                signal.signal(
                    signal.SIGINT, AfterTheClose() if nothing_left_to_stop else self._previous
                )
            self._previous = None

    def uninstall(self, *, nothing_left_to_stop: bool = False) -> None:
        """`nothing_left_to_stop` is for a command with nothing after its body but saying how it
        went: `AfterTheClose` takes the signal from here, in one step, with no moment for a press
        to land in."""
        self._restore(nothing_left_to_stop=nothing_left_to_stop)
        self._uninstalled = True
        if self._keyless:
            self._keyless = False
            KillSwitch.keyless -= 1
        if self._looking is not None:
            self._looking.cancel()
        if self._keys is not None:
            self._keys.close()
