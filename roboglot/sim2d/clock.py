"""One clock for N ducks: the world advances only when every participant is asleep.

With a single duck, whoever called `transport.sleep()` could just step the world. With a
team of concurrent tasks that breaks: time would advance once per sleeper and in racy
order. This clock makes sim time a shared, deterministic resource: participants park with
an INTEGER remaining-step count (exactly reproducing the single-duck step arithmetic), an
advancer task steps the world one `dt` at a time only while *everyone* is parked, and due
sleepers are woken in sorted-participant order. The world is frozen while any participant
is awake, so LLM latency costs zero sim time — same semantics a solo run has today.

Rule for participants: every await in your loop must bottom out in `sleep()` here, and you
must `unregister()` when you finish (or you wedge time for everyone else).

The clock asks nothing of the world beyond a time and a way to advance it, so the cartoon, the
Microduck's MuJoCo world (`roboglot_microduck.sim3d`) and the arm's (`roboglot_lerobot.sim.clock`)
share it, each at its own `dt`: 50 ms for the cartoon's kinematics, 20 ms for a walking policy
that runs at 50 Hz, and for the arm the model's timestep times its `SUBSTEPS`. The arm's makes
each sleep a participant of its own, because two tool calls over MCP are two tasks sleeping at
once (ADR-0047).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from roboglot.sim2d.world import DT

logger = logging.getLogger("roboglot.sim2d")


class SteppableWorld(Protocol):
    """What the clock needs of a world: where time stands, and one step forward."""

    t: float

    def step(self, dt: float) -> None: ...


class HookInterrupt(Exception):
    """A tick hook raised KeyboardInterrupt (the live window's close button). Raised into
    every parked sleeper, because a raw KeyboardInterrupt inside an asyncio task would be
    re-raised into the event loop itself and take the whole process down messily."""


class WorldStepError(Exception):
    """`world.step()` raised, so simulated time cannot go on.

    Raised into every parked participant and out of every later `sleep()`. The advancer is a
    task nobody awaits until `stop()`, so without this its exception sat unretrieved while
    every sleeper waited on a future that would never resolve: the run hung until a verb
    timed out, and the reason it hung was collected by the garbage collector.
    """


@dataclass
class _Waiter:
    steps_left: int
    future: asyncio.Future[None]


class TeamClock:
    def __init__(
        self,
        world: SteppableWorld,
        *,
        dt: float = DT,
        realtime: bool = False,
        yield_every: int = 4,
    ) -> None:
        self.world = world
        self.dt = dt
        self.realtime = realtime
        self.yield_every = yield_every
        self._waiters: dict[str, _Waiter | None] = {}  # pid -> parked waiter, or None if awake
        self._tick_hooks: list[Callable[[Any], None]] = []
        self._kick: asyncio.Event | None = None
        self._advancer: asyncio.Task[None] | None = None
        self._stopped = False
        self.hook_errors: list[Exception] = []
        """Exceptions raised by tick hooks. The offending hook is removed and time goes on."""
        self.failure: WorldStepError | None = None
        """Why time stopped, if it did. Set once; every `sleep()` after it raises this."""

    # ── hooks (recorder, live window) ───────────────────────────────────────────────

    def add_tick_hook(self, hook: Callable[[Any], None]) -> None:
        self._tick_hooks.append(hook)

    def remove_tick_hook(self, hook: Callable[[Any], None]) -> None:
        if hook in self._tick_hooks:
            self._tick_hooks.remove(hook)

    # ── participants ────────────────────────────────────────────────────────────────

    def register(self, pid: str) -> None:
        self._waiters.setdefault(pid, None)

    def unregister(self, pid: str) -> None:
        waiter = self._waiters.pop(pid, None)
        if waiter is not None and not waiter.future.done():
            waiter.future.cancel()
        self._nudge()

    def now(self) -> float:
        return self.world.t

    # ── sleeping ────────────────────────────────────────────────────────────────────

    async def sleep(self, pid: str, seconds: float) -> None:
        if self.failure is not None:
            # Before `_ensure_advancer`, which would otherwise start a fresh advancer over a
            # world that has already failed and bury the original cause.
            raise self.failure
        self._ensure_advancer()
        self.register(pid)
        if seconds <= 0:
            await asyncio.sleep(0)
            return
        if self._waiters.get(pid) is not None:
            # One parked waiter per participant, so a second task sleeping under the same id
            # would overwrite the first and leave its future unresolved for good. That is a
            # deadlock with no error in it, and the two tasks are usually a subscription and
            # a verb, so say which id and stop rather than hang.
            raise RuntimeError(
                f"two tasks are sleeping as {pid!r}: one participant is one task, and the "
                "second would strand the first"
            )
        steps = max(1, round(seconds / self.dt))
        future: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        self._waiters[pid] = _Waiter(steps, future)
        self._nudge()
        try:
            await future
        finally:
            # Normal wake: the advancer already marked us awake. Cancellation: do it here. Only
            # ever our own waiter, though. Between the clock letting go of this pid (a wake, an
            # interrupt or an unregister) and this task resuming, another task sleeping as the
            # same pid can park, because the slot reads awake and the guard above lets it in.
            # Clearing that waiter would leave its future unresolved and that task hung for good.
            waiter = self._waiters.get(pid)
            if waiter is not None and waiter.future is future:
                self._waiters[pid] = None
                self._nudge()

    # ── the advancer ────────────────────────────────────────────────────────────────

    def _ensure_advancer(self) -> None:
        if self._advancer is None or self._advancer.done():
            self._kick = asyncio.Event()
            self._stopped = False
            self._advancer = asyncio.create_task(self._run(), name="roboglot-team-clock")

    def _nudge(self) -> None:
        if self._kick is not None:
            self._kick.set()

    def _all_parked(self) -> bool:
        return bool(self._waiters) and all(w is not None for w in self._waiters.values())

    async def _run(self) -> None:
        assert self._kick is not None
        steps_since_yield = 0
        while not self._stopped:
            await self._kick.wait()
            self._kick.clear()
            while self._all_parked() and not self._stopped:
                try:
                    self.world.step(self.dt)
                except Exception as e:
                    self.failure = WorldStepError(
                        f"the world could not step at t={self.world.t:.2f}s: "
                        f"{type(e).__name__}: {e}"
                    )
                    self.failure.__cause__ = e
                    self._fail_sleepers(self.failure)
                    return
                for hook in list(self._tick_hooks):
                    try:
                        hook(self.world)
                    except KeyboardInterrupt:
                        # e.g. the live window's close button: abort every sleeper loudly
                        self.remove_tick_hook(hook)
                        self._interrupt_sleepers()
                        return
                    except Exception as e:  # a broken hook must never freeze sim time
                        self.remove_tick_hook(hook)
                        self.hook_errors.append(e)
                        # Logged as well as collected: `hook_errors` is read by almost
                        # nobody, so a recorder that could not render used to fail silently
                        # and surface much later as "no frames recorded".
                        logger.warning(
                            "tick hook %s raised and was removed: %r",
                            getattr(hook, "__qualname__", hook),
                            e,
                        )
                due: list[str] = []
                for pid in sorted(self._waiters):
                    waiter = self._waiters[pid]
                    if waiter is None:
                        continue
                    waiter.steps_left -= 1
                    if waiter.steps_left <= 0:
                        due.append(pid)
                for pid in due:  # sorted: deterministic wake order
                    waiter = self._waiters[pid]
                    if waiter is not None:
                        self._waiters[pid] = None  # mark awake BEFORE resolving
                        if not waiter.future.done():
                            waiter.future.set_result(None)
                if self.realtime:
                    await asyncio.sleep(self.dt)
                else:
                    steps_since_yield += 1
                    if steps_since_yield >= self.yield_every:
                        steps_since_yield = 0
                        await asyncio.sleep(0)

    def _fail_sleepers(self, error: Exception) -> None:
        """Wake every parked participant with the reason time stopped."""
        for pid in sorted(self._waiters):
            waiter = self._waiters[pid]
            if waiter is not None:
                self._waiters[pid] = None
                if not waiter.future.done():
                    waiter.future.set_exception(error)

    def _interrupt_sleepers(self) -> None:
        """Wake every parked participant with HookInterrupt (the human said stop)."""
        for pid in sorted(self._waiters):
            waiter = self._waiters[pid]
            if waiter is not None:
                self._waiters[pid] = None
                if not waiter.future.done():
                    waiter.future.set_exception(HookInterrupt("live window closed"))

    async def stop(self) -> None:
        self._stopped = True
        self._nudge()
        if self._advancer is not None:
            self._advancer.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._advancer
            self._advancer = None
