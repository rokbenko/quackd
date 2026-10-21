"""The layer that does not trust the LLM.

Upstream's `robotd` is the safety authority for the robot's body. This module is the
authority for the *conversation*: every verb call — from the agent loop or an MCP client —
passes through `Executor`, which checks the `.duck` allowlist, asks a human when the
contract says so, counts budgets, and can run with the transport disconnected (`dry_run`).
`Heartbeat` and the kill switch make "the LLM stalled" and "the human panicked" both end in
a `stop` intent.
"""

from __future__ import annotations

import asyncio
import contextlib
import math
import queue
import signal
import sys
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal, NoReturn

from pydantic import ValidationError

from quackd.adapters.base import backend_name
from quackd.duckfile.schema import (
    DEFAULT_POLICY_TOTAL_S,
    POLICY_VERBS,
    Budgets,
    DuckFrontmatter,
)
from quackd.log import (
    EventLog,
    LoggedTransport,
    a_person_was_asked,
    counting,
    fmt_params,
)
from quackd.perception.base import Detector, detect_times
from quackd.transport.base import CameraFrame, DuckState, DuckTransport
from quackd.verbs.registry import (
    STOPPED,
    Verb,
    VerbContext,
    VerbNotFound,
    VerbRegistry,
    VerbResult,
)
from quackd.verdict import BEFORE_VERDICT, MOVES_THE_BODY, Verdict

if TYPE_CHECKING:
    from quackd.adapters.manifest import RobotManifest

CANCELLED_BY_ABORT = "abort"
CANCELLED_BY_TIMEOUT = "timeout"
CANCELLED_FROM_OUTSIDE = "cancel"
CANCELLED_BY_PAUSE = "pause"
PAUSE_ENDED = "pause"
"""`ended_by` in the result of a verb a granted pause ended (`Executor._ended_by_pause`), which
`abort_when`'s count of failures in a row passes over."""
"""Why the executor cancelled a running verb, as the message of the `CancelledError` the verb is
handed (`Executor._execute`): the abort it races, a pause granted to a robot met, the verb's own
timeout, or the caller going
away. A verb that keeps a record of its own names its ending by it."""

Source = Literal["agent", "mcp", "cli", "decision", "peer"]
"""Who asked for this verb. `decision` is the discrete stepper answering a turn the model
never saw, whichever decision LLM was behind it: which one that was is recorded once for the
run (`run_start.decision_llm`) rather than on every verb, so swapping Jev for a server you run
yourself changes nothing here. The log already prints `from <source>` for anything that is not
the agent, so the record says who chose a verb without a renderer knowing the word. `peer` is
a robot met over Robot Passport, through the courtesy handler and nothing else
(`quackd.passport_courtesy`), and the `peer` gate holds it to the two verbs a courtesy runs."""

PEER_VERBS = ("stop", "say")
"""The verbs a peer's courtesy may run: `stop`, which a granted pause begins with, and `say`,
which a body identifies itself by sound with. A peer never asks for either by name; the handler
runs them for it, and the `peer` gate refuses anything else a peer might reach."""

WHILE_PAUSED = frozenset({"stop", "observe", "report_state", "say", "quack", "introspect"})
"""What still runs while a pause this robot granted holds: the brake, and the verbs quackd knows
move no part of the body. It is not `BEFORE_VERDICT`, which is what a pilot needs before it
judges: `gaze`, `look` and `watch` turn a head and `express` moves antennas, and a robot that
answered that it is holding still holds still. A verb this set does not name waits, a sensor a
third party marked `read_only` among them, since a flag on a verb quackd has never seen is no
promise that nothing turns, and a learned verb waits whatever it is called."""


class SafetyStop(Exception):
    """Base for every reason the run must end now, regardless of what the LLM wants."""

    outcome = "aborted"
    """The word `verb_end` records. A layer above the executor that ends a verb for a reason
    of its own overrides this, so the log says `PREEMPTED` and not the red `ERROR` that
    means a bug."""


class BudgetExceeded(SafetyStop):
    pass


class Aborted(SafetyStop):
    """An `abort_when` condition or the kill switch fired."""


class VerbNotAllowed(PermissionError):
    """Refused, but the run continues — the LLM is told and may choose differently."""


class VerdictRequired(VerbNotAllowed):
    """A verb that moves the body, before the pilot has said the task fits this body.

    Refused like any other gate, and the run goes on: the pilot is told which tool records a
    verdict, and may record one and try again."""


class ConfirmDenied(PermissionError):
    """A human said no."""


@dataclass
class Budget:
    limits: Budgets
    now: Callable[[], float] = time.monotonic
    steps: int = 0
    llm_calls: int = 0
    stepper_calls: int = 0
    """Turns the discrete stepper answered (`quackd run --decision-mode on`).

    Not a limit and never checked. A stepper turn runs a verb, and the verb charges a step
    like every other one, so `max_steps` already bounds it and a second number in the `.duck`
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

    def start(self) -> None:
        self.started_at = self.now()

    @property
    def elapsed_s(self) -> float:
        return 0.0 if self.started_at is None else self.now() - self.started_at

    def check(self) -> None:
        if self.steps >= self.limits.max_steps:
            raise BudgetExceeded(f"max_steps ({self.limits.max_steps}) reached")
        if self.llm_calls >= self.limits.max_llm_calls:
            raise BudgetExceeded(f"max_llm_calls ({self.limits.max_llm_calls}) reached")
        self.check_time()

    def check_time(self) -> None:
        if self.elapsed_s > self.limits.max_minutes * 60:
            raise BudgetExceeded(f"max_minutes ({self.limits.max_minutes:g}) exceeded")

    def note_step(self) -> None:
        self.check()
        self.steps += 1

    def note_llm_call(self) -> None:
        self.check()
        self.llm_calls += 1

    def note_stepper_call(self) -> None:
        """A turn the stepper answered: no LLM call to charge, and the verb charges the step.

        The clock is the one budget a turn with no model call can still hit, so it is the one
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
            f"llm calls {self.llm_calls}/{self.limits.max_llm_calls}, "
            # absent until the stepper has answered once, so every run without one reads
            # exactly as it always has: this string is in every observation the model is
            # handed, and `quackd log` parses it back out
            + (f"{self.stepper_calls} by the stepper, " if self.stepper_calls else "")
            # the same rule, for the seconds a policy has driven the body
            + (f"policy {self.policy_s:.1f}/{self.policy_total_s:g} s, " if self.policy_s else "")
            + f"{self.elapsed_s / 60:.1f}/{self.limits.max_minutes:g} min"
        )


def _segment_seconds(result: VerbResult | None, before: float | None, after: float | None) -> float:
    """What one policy segment charges against `policy_total_s`: the seconds its result says it
    ran. A result that does not say, or a call that ended with none, is charged the robot's
    clock across the call (`before` to `after`), since its segment may have run all of it: a
    call the executor ended itself, a timeout, a verb that raised, a cancellation or an abort,
    and the mock's `pick` that ends with nothing held. A `do` refused before its segment began
    says it ran 0 s, however long the refusal took on the wall. A call nobody could time
    charges nothing, and a number that is not one, a bool or a NaN, is never believed as one."""
    said = None if result is None else result.data.get("seconds")
    if isinstance(said, int | float) and not isinstance(said, bool) and math.isfinite(said):
        return max(0.0, float(said))
    if before is not None and after is not None and math.isfinite(after - before):
        return max(0.0, after - before)
    return 0.0


ConfirmFn = Callable[[str, dict[str, Any]], bool]
"""Asked before a gated verb runs. Return True to proceed."""


def deny_all(_name: str, _params: dict[str, Any]) -> bool:
    return False


def allow_all(_name: str, _params: dict[str, Any]) -> bool:
    return True


@dataclass
class Executor:
    """allowlist → confirm gate → budget → preconditions → (dry-run |) execute with timeout."""

    registry: VerbRegistry
    transport: DuckTransport
    contract: DuckFrontmatter | None = None
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
    """Whether a robot met may have a courtesy run now (`quackd.passport_courtesy`). A run opens
    it once its pilot has the body and closes it before its teardown, and an MCP session once it
    has connected and until it closes. Outside those windows the body is moved by its rest move
    and its hand-over, which no gate sees, so a pause granted then could not be kept."""
    _moving: set[asyncio.Task[VerbResult]] = field(default_factory=set, init=False, repr=False)
    """The verbs moving the body now, which a granted pause ends (`pause_for`)."""
    _halted: set[asyncio.Task[VerbResult]] = field(default_factory=set, init=False, repr=False)
    """Those a pause ended, so `_execute` answers for them rather than raising their cancel."""
    require_verdict: bool = False
    """On where an `assess_task` tool was offered: the agent loop and every MCP session. A
    flock member is a state machine with no pilot to ask, so its executor never does."""
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
    segment: str | None = field(default=None, init=False)
    """The policy verb (`POLICY_VERBS`), as it was called, whose segment is between its check
    against `policy_total_s` and its charge, or None. Another is refused meanwhile."""

    # ── narration ───────────────────────────────────────────────────────────────────

    def _emit(self, kind: str, **data: Any) -> None:
        if self.event_log is not None:
            self.event_log.emit(kind, **data)

    def _note(self, text: str) -> None:
        """A free-text line for both audiences: `log` is a contract other callers rely on
        (the flock's `member_log`, the MCP logger, tests), and the event log observes it."""
        self.log(text)
        self._emit("note", text=text)

    def _robot_now(self) -> float | None:
        """The robot's own clock, or None when it has none to give (a transport that raises,
        or a simulator not connected yet). Never a reason to lose the verb."""
        with contextlib.suppress(Exception):
            return float(self.transport.now())
        return None

    def _timed_out(
        self, name: str, verb: Verb, *, timed: bool, unstopped: str | None = None
    ) -> str:
        """What a verb this executor's own timeout ended says, asked after the stop that follows
        it, and why that stop failed when it did (`unstopped`, from `_stop_after`). A policy
        segment's adds why, where the robot can tell (`slow_policy`): a simulator's clock stands
        still while its policy thinks, so a policy slower than the latency it declared outruns a
        timeout sized from that latency, and the bare timeout read as a simulator that had hung.
        Never a reason to lose the result."""
        stopped = "stopped" if unstopped is None else f"the stop sent after it failed: {unstopped}"
        said = f"{name} timed out after {verb.timeout_s:g}s; {stopped}"
        why = None
        if timed:
            with contextlib.suppress(Exception):
                ask = getattr(self.transport, "slow_policy", None)
                why = ask() if callable(ask) else None
        return f"{said}. {why}" if isinstance(why, str) and why else said

    def _clock(self) -> str | None:
        """What to call the robot's clock when it is not the wall clock. A free-running
        simulator's seconds are the ones that mean something to a reader; on hardware `now()`
        is monotonic, so there is nothing to distinguish and the key is absent."""
        return "sim" if backend_name(self.transport) in ("sim2d", "mujoco") else None

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
        run without an event log had no such cover."""
        try:
            await self.logged_transport().stop()
        except Exception as e:
            return f"{type(e).__name__}: {e}"
        return None

    # ── policy ──────────────────────────────────────────────────────────────────────

    @property
    def allowed(self) -> list[str]:
        if self.contract is not None:
            return list(self.contract.verbs.allow)
        # No contract (MCP without a loaded duck): every non-dangerous verb.
        return [v.name for v in self.registry.verbs() if v.safety_class != "dangerous"]

    def is_allowed(self, name: str) -> bool:
        """Alias-aware: a duck that allows `walk_to` also allows `go_to`, and vice versa."""
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

    def _hold_still_for(self, name: str, canonical: str, verb: Verb) -> None:
        """The `paused` gate: refuse a verb that may move the body while a pause holds."""
        held = self.paused_until - time.monotonic()
        if held > 0 and self._may_move(canonical, verb):
            reason = (
                f"{name} waits: a pause this robot granted a robot it met holds {held:.1f} s "
                "more, and until it ends only stop and the verbs that move nothing run"
            )
            self._emit("gate", name=name, gate="paused", outcome="refused", reason=reason)
            raise VerbNotAllowed(reason)

    def pause_for(self, seconds: float) -> None:
        """Hold the body still for `seconds` (docs/passport/talk.md#what-quackd-does-with-an-ask):
        every verb already moving it is ended now, as an abort ends one, and the `paused` gate
        admits nothing that moves it until the hold is over. Without the first half a `move`
        that began a second earlier went on sending its twists for the rest of its duration,
        and a `go_to` for up to its whole timeout, past a robot told this one was holding
        still. A second pause only ever lengthens the hold, never shortens one already running."""
        self.paused_until = max(self.paused_until, time.monotonic() + seconds)
        # Cancelled here, not through something they wait on: a task cancelled before its next
        # step never takes it, so none of them sends another intent after this returns, and the
        # stop the courtesy sends next is the last thing the body is told.
        for task in list(self._moving):
            if not task.done():
                self._halted.add(task)
                task.cancel(CANCELLED_BY_PAUSE)

    def context(self, source: Source = "agent") -> VerbContext:
        """`source` is the outer call's, so a composite's nested verbs are narrated as coming
        from the same pilot (an MCP session's `approach_and` runs an MCP `go_to`)."""
        return VerbContext(
            transport=self.logged_transport(),
            detector=self.detector,
            dry_run=self.dry_run,
            # a verb's own log line is a `note` as well, so it is not the one thing the log
            # cannot see. Not the executor's own arrows: those would double `verb_start`.
            log=self._note,
            on_frame=self.on_frame,
            on_frames=self.on_frames,
            run_verb=lambda name, params: self.run_verb(name, params, source=source, nested=True),
            # an adapter carries its manifest after connect; a bare transport has none
            manifest=self.manifest or getattr(self.transport, "manifest", None),
            eyes=self.eyes,
            interrupted=self._interruption(self.stops),
        )

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
    ) -> VerbResult:
        """Every verb call, from the agent loop, an MCP client or a composite.

        The narration wraps the gates: exactly one `verb_start` and, whatever happens after
        it, exactly one `verb_end` with an `outcome`, so a reader of the log never sees a
        verb that started and vanished. `ok`/`fail` are the verb's own verdict; `refused`,
        `denied`, `budget` and `aborted` are the executor's, and `error` is a bug."""
        params = params or {}
        canonical = self.registry.canonical(name)
        started = time.perf_counter()
        robot_started = self._robot_now()
        outcome, summary, ok = "error", "verb exited unexpectedly", False
        data_keys: list[str] = []
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
                )
                result = await self._run_verb(name, canonical, params, source=source, nested=nested)
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
                # another layer ended the verb on purpose (a flock role change): its own word,
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
                    if (label := self._clock()) is not None:
                        clocks["clock"] = label
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
                )

    async def _run_verb(
        self,
        name: str,
        canonical: str,
        params: dict[str, Any],
        *,
        source: Source,
        nested: bool,
    ) -> VerbResult:
        if canonical == "stop":
            # counted before any gate, since a brake asked for is a brake pulled, whoever asked:
            # a verb running beside this call ends on it (`_interruption`)
            self.stops += 1
        # `stop` is the one verb an aborted session must still be able to run. The abort is
        # set precisely when something has gone wrong — a failed heartbeat, a kill switch —
        # which is the moment the pilot reaches for the brake, and refusing it here closed
        # the panic button exactly when it was needed.
        if self.abort.is_set() and canonical != "stop":
            self._emit("gate", name=name, gate="abort", outcome="refused", reason="run aborted")
            raise Aborted("run aborted")
        if not self.is_allowed(name):
            reason = f"verb {name!r} is not in this duck's allowlist ({', '.join(self.allowed)})"
            self._emit("gate", name=name, gate="allowlist", outcome="refused", reason=reason)
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
            )
            raise VerbNotAllowed(f"unknown verb {name!r}") from None
        if source == "peer" and canonical not in PEER_VERBS:
            reason = (
                f"{name} is not a verb a courtesy runs: a robot met over Robot Passport can have "
                "this one pause or identify itself, and nothing else"
            )
            self._emit("gate", name=name, gate="peer", outcome="refused", reason=reason)
            raise VerbNotAllowed(reason)

        # A verb declared `read_only` by whoever wrote it is a sensor, whatever body it came
        # with: a third-party `locate` that reads where things are must run before the
        # verdict for the same reason `observe` does, or the pilot judges feasibility blind.
        # `BEFORE_VERDICT` is matched by name, and a learned verb is excluded from that half
        # on purpose: it is an unproven policy, so a `.duck` that named one `observe` on a
        # body with no camera verb would otherwise have it run before any verdict. The
        # confirm gate below would still stop it, and `--yes` answers the confirm gate.
        # `MOVES_THE_BODY` wins over the flag: where quackd has said a name is motion, a verb
        # arriving under that name and carrying `read_only` is saying two contradictory things
        # about itself, and the gate believes quackd's own record rather than the newcomer.
        looks = (canonical in BEFORE_VERDICT and verb.kind != "learned") or (
            verb.read_only and canonical not in MOVES_THE_BODY
        )
        self._hold_still_for(name, canonical, verb)
        if self.require_verdict and not looks and not self.cleared:
            why = (
                self.verdict.blocking_reason()
                if self.verdict is not None
                else "no feasibility verdict has been recorded for this task yet"
            )
            reason = (
                f"{name} moves the body, and {why}: record a verdict first "
                "(feasible, infeasible or uncertain)"
            )
            self._emit("gate", name=name, gate="verdict", outcome="refused", reason=reason)
            raise VerdictRequired(reason)

        try:
            parsed = verb.params.model_validate(params)
        except ValidationError as e:
            msgs = "; ".join(
                f"{'.'.join(map(str, err['loc']))}: {err['msg']}" for err in e.errors()
            )
            self._emit("gate", name=name, gate="params", outcome="refused", reason=msgs)
            return VerbResult.fail(f"invalid params for {name}: {msgs}")

        if self.needs_confirm(verb):
            # A `dangerous` verb is the one class a standing answer may not clear. `--yes`, a
            # flock's standing answer, the MCP server and a pipe on stdin all answer without
            # asking anybody, so the verb is refused before its confirm is put, and only a
            # person at a terminal can run it (ADR-0056). Refused rather than denied: nobody
            # said no, and the fix is a terminal, not `--yes`.
            if verb.safety_class == "dangerous" and not a_person_was_asked(self.confirm):
                reason = (
                    f"{name} is a dangerous verb, and only a person can clear it, while nobody "
                    "is asked here (--yes, a flock's standing answer, the MCP server or a "
                    "pipe): run it from a terminal, without --yes, and answer its confirm "
                    "yourself"
                )
                self._emit("gate", name=name, gate="confirm", outcome="refused", reason=reason)
                raise VerbNotAllowed(reason)
            try:
                answer = bool(self.confirm(name, parsed.model_dump()))
            except Exception as e:
                # typer's y/N prompt raises click's `Abort` (a RuntimeError) on Ctrl-C or
                # EOF. An asker that raised has not said yes, and the run deserves to be told
                # so in the gate's words rather than as `verb_end error "Abort: "`.
                why = f"the prompt raised {type(e).__name__}" + (f": {e}" if str(e) else "")
                self._emit(
                    "gate", name=name, gate="confirm", outcome="denied", answer=False, reason=why
                )
                raise ConfirmDenied(f"human declined {name} ({why})") from e
            asked = a_person_was_asked(self.confirm)
            if asked:
                # the exchange, beside the gate's record of what it decided: only where a
                # person was really asked, never for `--yes` or a flock's standing answer
                self._emit(
                    "prompt",
                    what="confirm",
                    question=f"run {name}({fmt_params(parsed.model_dump())})?",
                    answer=answer,
                )
            # The same care in the gate's own reason, which said `a human said yes` however the
            # answer arrived. Under `--yes`, a flock's standing answer or a pipe on stdin the
            # gate really did open, and the record should say that and not name a witness.
            said = ("yes" if answer else "no") if asked else ("allowed" if answer else "denied")
            self._emit(
                "gate",
                name=name,
                gate="confirm",
                outcome="allowed" if answer else "denied",
                answer=answer,
                reason=f"a human said {said}" if asked else f"the confirm gate was {said}",
            )
            if not answer:
                raise ConfirmDenied(f"human declined {name}")

        # A segment of the body's learned policy has a budget of its own seconds as well as
        # its step, checked first so a segment refused for time charges no step. Nested or
        # not: the seconds are the body's, whoever asked for them. `pick` hands the arm to the
        # same policy as `manipulate` does, so its seconds are the same budget's.
        timed = canonical in POLICY_VERBS
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
            self._emit("gate", name=name, gate="segment", outcome="refused", reason=reason)
            raise VerbNotAllowed(reason)
        if self.budget is not None and (timed or not nested):
            try:
                if timed:
                    self.budget.check_policy()
                # A peer's courtesy draws on a budget of its own (`quackd.passport_courtesy`),
                # never the pilot's: an identify every ten seconds used to spend six of a run's
                # forty steps a minute. And `stop` is charged while there is a step left and
                # never refused for want of one, as no other gate refuses it: a session whose
                # steps were spent used to have its brake refused with them.
                if not nested and source != "peer":
                    try:
                        self.budget.note_step()
                    except BudgetExceeded:
                        if canonical != "stop":
                            raise
            except BudgetExceeded as e:
                self._emit("gate", name=name, gate="budget", outcome="exceeded", reason=str(e))
                raise
        if not timed:
            return await self._run_admitted(name, canonical, params, verb, parsed, source=source)
        # held from the check above, with nothing awaited since it, to the segment's charge
        self.segment = name
        try:
            return await self._run_admitted(
                name, canonical, params, verb, parsed, source=source, timed=True
            )
        finally:
            self.segment = None

    async def _run_admitted(
        self,
        name: str,
        canonical: str,
        params: dict[str, Any],
        verb: Verb,
        parsed: Any,
        *,
        source: Source,
        timed: bool = False,
    ) -> VerbResult:
        """A verb every gate has let through: its state, its abort conditions and its
        preconditions, then the verb itself, or the dry run's word for it. `timed` is a policy
        segment's, whose seconds are charged on every way out."""
        try:
            state = await self.transport.get_state()
        except Exception:
            # the one in-verb transport failure that used to send no stop. A link that
            # cannot report state cannot be trusted to be holding a zero twist either.
            with contextlib.suppress(Exception):
                await self.logged_transport().stop()
            raise
        # Read again after the last await before the verb is tracked as moving. A pause
        # granted while this verb waited on that state read, or on a person's confirm before
        # it, found nothing to end, and the gate it had already passed is not asked twice, so
        # the verb used to run in full after the stop a robot met was promised.
        self._hold_still_for(name, canonical, verb)
        try:
            self._check_abort_conditions(state)
        except Aborted as e:
            self._emit(
                "gate",
                name=name,
                gate="abort_when",
                outcome="fired",
                reason=str(e),
                state=state.summary(),
            )
            raise
        for pre in verb.preconditions:
            refusal = pre(state)
            if refusal:
                self._emit(
                    "gate",
                    name=name,
                    gate="precondition",
                    outcome="refused",
                    reason=refusal,
                    state=state.summary(),
                )
                return self._record(name, params, VerbResult.fail(f"cannot {name}: {refusal}"))

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
        robot_before = self._robot_now() if timed else None
        result: VerbResult | None = None
        try:
            result = await self._execute(
                verb, parsed, interruptible=canonical != "stop", source=source, name=name
            )
        except TimeoutError:
            unstopped = await self._stop_after()
            result = VerbResult.fail(self._timed_out(name, verb, timed=timed, unstopped=unstopped))
        except SafetyStop:
            raise
        except Exception as e:  # a buggy verb must not take the run down un-stopped
            unstopped = await self._stop_after()
            stopped = (
                "stopped" if unstopped is None else f"the stop sent after it failed: {unstopped}"
            )
            result = VerbResult.fail(f"{name} raised {type(e).__name__}: {e}; {stopped}")
        finally:
            # Charged however the call ends. A call cancelled from outside, an MCP client that
            # went, or one aborted mid-segment leaves with no result, and its segment ran all
            # the while: without this a client that cancelled every call just short of its end
            # ran the policy for as long as it liked with nothing charged.
            if timed and self.budget is not None:
                self.budget.note_policy(_segment_seconds(result, robot_before, self._robot_now()))
        self.log(f"← {name}: {'ok' if result.ok else 'FAIL'} {result.summary}")
        return self._record(name, params, result)

    async def _execute(
        self,
        verb: Verb,
        parsed: Any,
        *,
        interruptible: bool,
        source: Source = "agent",
        name: str | None = None,
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
        verb_task: asyncio.Task[VerbResult] = asyncio.ensure_future(
            verb.execute(self.context(source), parsed)
        )
        abort_task: asyncio.Task[bool] | None = None
        waiting: set[asyncio.Future[Any]] = {verb_task}
        if interruptible:
            abort_task = asyncio.ensure_future(self.abort.wait())
            waiting.add(abort_task)
            if self._may_move(self.registry.canonical(verb.name), verb):
                self._moving.add(verb_task)
        done: set[asyncio.Future[Any]] = set()
        finished = False
        try:
            done, _ = await asyncio.wait(
                waiting, return_when=asyncio.FIRST_COMPLETED, timeout=verb.timeout_s
            )
            finished = True
        finally:
            # the loser is always cancelled: an abort waiter left behind would otherwise
            # accumulate one task per verb for the life of the run
            self._moving.discard(verb_task)
            halted = verb_task in self._halted
            self._halted.discard(verb_task)
            if abort_task is not None and not abort_task.done():
                abort_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await abort_task
            # Nothing the verb does from here can be trusted to end, so take the legs back. The
            # cancel says why, which a verb that keeps a record of its own reads off the
            # `CancelledError` it is handed (`watch`'s `ended`): the abort, its timeout, or a
            # caller that went away.
            if verb_task not in done:
                if abort_task is not None and abort_task in done:
                    why = CANCELLED_BY_ABORT
                elif finished:
                    why = CANCELLED_BY_TIMEOUT
                else:
                    why = CANCELLED_FROM_OUTSIDE
                verb_task.cancel(why)
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await verb_task
            if not finished:
                if not halted:  # one a pause ended is said once, by the call that holds it
                    self._emit(
                        "gate",
                        name=called,
                        gate="cancelled",
                        outcome="fired",
                        reason="the call was cancelled; the verb was cancelled and a stop was sent",
                    )
                with contextlib.suppress(Exception):
                    await self.logged_transport().stop()
        if halted:
            if self.abort.is_set():  # an abort that landed with the pause is what leaves
                await self._aborted_mid_verb(called)
            return await self._ended_by_pause(called)
        if verb_task in done:
            return verb_task.result()
        if abort_task is not None and abort_task in done:
            await self._aborted_mid_verb(called)
        raise TimeoutError

    async def _aborted_mid_verb(self, called: str) -> NoReturn:
        """What a verb the abort cancelled comes to: a stop after it, and `Aborted`."""
        self._emit(
            "gate",
            name=called,
            gate="abort",
            outcome="fired",
            reason="aborted mid-verb: the verb was cancelled and a stop was sent",
        )
        # the abort is what leaves here, even when the stop after it failed: a heartbeat that
        # lost the link sets it, and that stop's own error used to leave in its place
        if (unstopped := await self._stop_after()) is not None:
            raise Aborted(
                f"aborted mid-verb; the verb was cancelled, and the stop sent after it failed: "
                f"{unstopped}"
            )
        raise Aborted("aborted mid-verb; the verb was cancelled and a stop was sent")

    async def _ended_by_pause(self, called: str) -> VerbResult:
        """What a verb a granted pause ended comes to: a stop after it, and a result saying why,
        which the pilot reads as it reads any other verb's."""
        held = max(0.0, self.paused_until - time.monotonic())
        self._emit(
            "gate",
            name=called,
            gate="paused",
            outcome="fired",
            reason="ended by a pause this robot granted a robot it met; a stop was sent",
        )
        unstopped = await self._stop_after()
        return VerbResult.fail(
            f"{called} ended: a robot this one met asked it to pause, and it is holding still "
            f"for {held:.0f} s more"
            + (f"; the stop sent after it failed: {unstopped}" if unstopped else ""),
            ended_by=PAUSE_ENDED,
        )

    # ── abort conditions the executor enforces itself ───────────────────────────────

    def _record(self, name: str, params: dict[str, Any], result: VerbResult) -> VerbResult:
        self.history.append((name, params, result))
        key = self.registry.canonical(name)  # `walk` and `move` failures count together
        if result.ok:
            self.consecutive_failures[key] = 0
        elif result.data.get("ended_by") == PAUSE_ENDED:
            # not the verb's failure, nor the pilot's: two pauses a robot met asked for used
            # to make two in a row of `move`, and `abort_when` ended the run on them
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

    def _check_abort_conditions(self, state: DuckState) -> None:
        if self.contract is None:
            return
        threshold = self.contract.battery_abort_percent
        if (
            threshold is not None
            and state.battery_percent is not None
            and state.battery_percent < threshold
        ):
            self.abort.set()
            raise Aborted(f"abort_when: battery {state.battery_percent:.0f}% below {threshold:g}%")


class Heartbeat:
    """Pings the transport every `period_s`. One miss → stop intent + abort."""

    def __init__(
        self,
        transport: DuckTransport,
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
        while not self.abort.is_set():
            try:
                await self.transport.heartbeat()
                self.beats += 1
            except Exception as e:
                self.failure = e
                # Everything in here is best effort and the abort is in a `finally`, because
                # setting it is the one thing that must happen: a log call or an event sink that
                # raised used to kill this task outright, leaving the abort unset and the run
                # with no idea the link had gone.
                try:
                    # "sending stop", not "stopping the duck": the heartbeat fails precisely
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
                        await stopper.stop()
                finally:
                    self.abort.set()
                return
            await asyncio.sleep(self.period_s)

    def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name="quackd-heartbeat")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            # a task that already died of its own exception re-raises it here, and this is
            # the first line of the loop's teardown: it must not take the stop, the final
            # state and the transcript's close down with it
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._task
            self._task = None


class KillSwitch:
    """Ctrl-C or `q` → abort, and Enter → the one thing a run ever waits for a person to do.

    Works on Windows too (no loop.add_signal_handler there). The key thread is the only
    reader of stdin quackd starts, so Enter is noticed here rather than with an `input()` of
    its own: two readers on one terminal would race for the same keystroke, and the one that
    lost would hang on a line the other had already taken.
    """

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
        is the one that has an arm limp in somebody's hands: without it, a terminal closed or a
        Ctrl-D typed at the wrong moment leaves quackd waiting for ever, holding nothing."""
        self._loop: asyncio.AbstractEventLoop | None = None
        self._previous: Any = None
        self._thread: threading.Thread | None = None

    def _fire(self, why: str) -> None:
        self.log(f"kill switch: {why} — cancelling the verb and stopping the robot")
        self.presses += 1
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self.abort.set)
            self._loop.call_soon_threadsafe(self.pressed.set)

    def _on_sigint(self, _signum: int, _frame: Any) -> None:
        # The first Ctrl-C is the orderly one: it aborts, which cancels the running verb and
        # sends a stop. Handing the signal back means a second one is a plain KeyboardInterrupt
        # again, so a human who is not convinced the first worked is never stuck holding a key
        # that quackd has quietly swallowed.
        self._restore()
        self._fire("Ctrl-C")

    def _watch_keys(self) -> None:
        """Read stdin until it ends, whatever the run is doing.

        It used to stop at the first `q` and at the abort flag, which was enough when the only
        keystroke that meant anything was the one that ended the run. A run that hands the arm
        to a person waits for Enter after the abort may already be set, and again in its own
        teardown, so the reader has to outlive both."""
        typed: list[str] = []
        try:
            while True:
                ch = sys.stdin.read(1)
                if not ch:
                    self._announce(self.keys_ended)
                    return
                if self._line_wanted.is_set():
                    # Somebody is being asked a question, so every key belongs to the answer.
                    # This reader cannot stand aside while a prompt runs its own `input()`: it
                    # is already blocked inside `read(1)` and would take the first character of
                    # the reply, and the prompt would then wait for a newline this thread had
                    # already swallowed. So it collects the line and hands it over instead.
                    if ch in ("\r", "\n"):
                        self._lines.put("".join(typed))
                        typed.clear()
                    else:
                        typed.append(ch)
                    continue
                typed.clear()
                if ch in ("\r", "\n"):
                    if self._loop is not None:
                        self._loop.call_soon_threadsafe(self.entered.set)
                    continue
                if ch.strip().lower() == "q":
                    self._fire("'q' pressed")
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
        no terminal and every test, so nothing that was not already racing changes."""
        if self._thread is None or not self._thread.is_alive():
            return input(prompt)
        with self._asking:
            while not self._lines.empty():  # anything typed before the question was asked
                self._lines.get_nowait()
            self._line_wanted.set()
            try:
                sys.stderr.write(prompt)
                sys.stderr.flush()
                return self._lines.get()
            finally:
                self._line_wanted.clear()

    def _announce(self, event: asyncio.Event) -> None:
        """Set an event from the key thread, which is not the loop's thread."""
        if self._loop is not None:
            with contextlib.suppress(RuntimeError):  # the loop has already closed
                self._loop.call_soon_threadsafe(event.set)

    async def wait_for_enter(
        self, *, timeout_s: float | None = None, until_abort: bool = True
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
        `keys_ended` where the thread ran and stdin then finished under it."""
        self.entered.clear()
        self.pressed.clear()
        watched = [
            asyncio.ensure_future(self.entered.wait()),
            asyncio.ensure_future((self.abort if until_abort else self.pressed).wait()),
            # nobody is going to press anything, so the caller is owed that answer rather than
            # a wait that never returns
            asyncio.ensure_future(self.keys_ended.wait()),
        ]
        try:
            await asyncio.wait(watched, timeout=timeout_s, return_when=asyncio.FIRST_COMPLETED)
            return self.entered.is_set()
        finally:
            for task in watched:
                task.cancel()
                # awaited, so a cancelled task is never left for the loop to complain about
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task

    def install(self, *, keys: bool = True) -> None:
        self._loop = asyncio.get_running_loop()
        with contextlib.suppress(ValueError):  # not the main thread
            self._previous = signal.signal(signal.SIGINT, self._on_sigint)
        if keys and sys.stdin is not None and sys.stdin.isatty():
            self._thread = threading.Thread(
                target=self._watch_keys, name="quackd-keys", daemon=True
            )
            self._thread.start()
        else:
            # Nobody is reading the keyboard, so no keystroke is ever coming, and a wait for
            # one has to know that rather than sit there. The caller that waits checks for a
            # terminal first, but the two checks are made at different moments and by different
            # code, and the cost of them disagreeing is a run stopped for ever with an arm limp
            # in somebody's hands.
            self.keys_ended.set()

    def _restore(self) -> None:
        if self._previous is not None:
            with contextlib.suppress(ValueError):
                signal.signal(signal.SIGINT, self._previous)
            self._previous = None

    def uninstall(self) -> None:
        self._restore()
