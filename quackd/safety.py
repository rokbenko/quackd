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
import signal
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

from pydantic import ValidationError

from quackd.duckfile.schema import Budgets, DuckFrontmatter
from quackd.perception.base import Detector
from quackd.transport.base import DuckState, DuckTransport
from quackd.verbs.registry import Verb, VerbContext, VerbNotFound, VerbRegistry, VerbResult

if TYPE_CHECKING:
    from quackd.adapters.manifest import RobotManifest

Source = Literal["agent", "mcp", "cli"]


class SafetyStop(Exception):
    """Base for every reason the run must end now, regardless of what the LLM wants."""


class BudgetExceeded(SafetyStop):
    pass


class Aborted(SafetyStop):
    """An `abort_when` condition or the kill switch fired."""


class VerbNotAllowed(PermissionError):
    """Refused, but the run continues — the LLM is told and may choose differently."""


class ConfirmDenied(PermissionError):
    """A human said no."""


@dataclass
class Budget:
    limits: Budgets
    now: Callable[[], float] = time.monotonic
    steps: int = 0
    llm_calls: int = 0
    started_at: float | None = None

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

    def status(self) -> str:
        return (
            f"step {self.steps}/{self.limits.max_steps}, "
            f"llm calls {self.llm_calls}/{self.limits.max_llm_calls}, "
            f"{self.elapsed_s / 60:.1f}/{self.limits.max_minutes:g} min"
        )


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
    abort: asyncio.Event = field(default_factory=asyncio.Event)
    consecutive_failures: dict[str, int] = field(default_factory=dict)
    history: list[tuple[str, dict[str, Any], VerbResult]] = field(default_factory=list)
    manifest: RobotManifest | None = None
    """The connected robot's manifest, handed to verbs so composites can pick a strategy."""

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

    def needs_confirm(self, verb: Verb) -> bool:
        if self.registry.canonical(verb.name) == "stop":
            return False  # never gated, whatever a contract or a manifest says
        if self.contract is not None:
            gated = {self.registry.canonical(c) for c in self.contract.verbs.confirm}
            if self.registry.canonical(verb.name) in gated:
                return True
        return verb.safety_class in ("confirm", "dangerous")

    def context(self) -> VerbContext:
        return VerbContext(
            transport=self.transport,
            detector=self.detector,
            dry_run=self.dry_run,
            log=self.log,
            on_frame=self.on_frame,
            run_verb=lambda name, params: self.run_verb(name, params, source="agent", nested=True),
            # an adapter carries its manifest after connect; a bare transport has none
            manifest=self.manifest or getattr(self.transport, "manifest", None),
        )

    # ── the one entry point ─────────────────────────────────────────────────────────

    async def run_verb(
        self,
        name: str,
        params: dict[str, Any] | None = None,
        *,
        source: Source = "agent",
        nested: bool = False,
    ) -> VerbResult:
        params = params or {}
        if self.abort.is_set():
            raise Aborted("run aborted")
        if not self.is_allowed(name):
            raise VerbNotAllowed(
                f"verb {name!r} is not in this duck's allowlist ({', '.join(self.allowed)})"
            )
        try:
            verb = self.registry.get(name)
        except VerbNotFound:
            raise VerbNotAllowed(f"unknown verb {name!r}") from None

        try:
            parsed = verb.params.model_validate(params)
        except ValidationError as e:
            msgs = "; ".join(
                f"{'.'.join(map(str, err['loc']))}: {err['msg']}" for err in e.errors()
            )
            return VerbResult.fail(f"invalid params for {name}: {msgs}")

        if self.needs_confirm(verb) and not self.confirm(name, parsed.model_dump()):
            raise ConfirmDenied(f"human declined {name}")

        if self.budget is not None and not nested:
            self.budget.note_step()

        state = await self.transport.get_state()
        self._check_abort_conditions(state)
        for pre in verb.preconditions:
            reason = pre(state)
            if reason:
                return self._record(name, params, VerbResult.fail(f"cannot {name}: {reason}"))

        if self.dry_run and not verb.read_only:
            self.log(f"[dry-run] would run {name}({parsed.model_dump()})")
            return self._record(
                name, params, VerbResult.success(f"[dry-run] {name} not sent", dry_run=True)
            )

        self.log(f"→ {name}({parsed.model_dump()})")
        try:
            result = await asyncio.wait_for(
                verb.execute(self.context(), parsed), timeout=verb.timeout_s
            )
        except TimeoutError:
            await self.transport.stop()
            result = VerbResult.fail(f"{name} timed out after {verb.timeout_s:g}s; stopped")
        except SafetyStop:
            raise
        except Exception as e:  # a buggy verb must not take the run down un-stopped
            await self.transport.stop()
            result = VerbResult.fail(f"{name} raised {type(e).__name__}: {e}; stopped")
        self.log(f"← {name}: {'ok' if result.ok else 'FAIL'} {result.summary}")
        return self._record(name, params, result)

    # ── abort conditions the executor enforces itself ───────────────────────────────

    def _record(self, name: str, params: dict[str, Any], result: VerbResult) -> VerbResult:
        self.history.append((name, params, result))
        key = self.registry.canonical(name)  # `walk` and `move` failures count together
        if result.ok:
            self.consecutive_failures[key] = 0
        else:
            n = self.consecutive_failures.get(key, 0) + 1
            self.consecutive_failures[key] = n
            limit = self.contract.repeat_failure_abort if self.contract else None
            if limit is not None and n >= limit:
                self.abort.set()
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
    ) -> None:
        self.transport = transport
        self.abort = abort
        self.period_s = period_s
        self.log = log
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
                # "sending stop", not "stopping the duck": the heartbeat fails precisely when
                # the link is in doubt, which is when a stop is least likely to arrive. What
                # actually stops a body whose deadman we cannot reach is the deadman itself.
                self.log(f"heartbeat failed: {e} — sending stop")
                with contextlib.suppress(Exception):
                    await self.transport.stop()
                self.abort.set()
                return
            await asyncio.sleep(self.period_s)

    def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name="quackd-heartbeat")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None


class KillSwitch:
    """Ctrl-C or `q` → abort. Works on Windows too (no loop.add_signal_handler there)."""

    def __init__(self, abort: asyncio.Event, log: Callable[[str], None] = lambda _m: None) -> None:
        self.abort = abort
        self.log = log
        self._loop: asyncio.AbstractEventLoop | None = None
        self._previous: Any = None
        self._thread: threading.Thread | None = None

    def _fire(self, why: str) -> None:
        self.log(f"kill switch: {why}")
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self.abort.set)

    def _on_sigint(self, _signum: int, _frame: Any) -> None:
        self._fire("Ctrl-C")

    def _watch_keys(self) -> None:
        try:
            while not self.abort.is_set():
                ch = sys.stdin.read(1)
                if not ch:
                    return
                if ch.strip().lower() == "q":
                    self._fire("'q' pressed")
                    return
        except Exception:
            return

    def install(self, *, keys: bool = True) -> None:
        self._loop = asyncio.get_running_loop()
        with contextlib.suppress(ValueError):  # not the main thread
            self._previous = signal.signal(signal.SIGINT, self._on_sigint)
        if keys and sys.stdin is not None and sys.stdin.isatty():
            self._thread = threading.Thread(
                target=self._watch_keys, name="quackd-keys", daemon=True
            )
            self._thread.start()

    def uninstall(self) -> None:
        if self._previous is not None:
            with contextlib.suppress(ValueError):
                signal.signal(signal.SIGINT, self._previous)
            self._previous = None
