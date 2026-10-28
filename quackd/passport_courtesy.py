"""The courtesy handler (docs/passport/talk.md#what-quackd-does-with-an-ask): what a robot met over
Robot Passport may have one of quackd's robots do, decided by fixed code and never by the pilot.

Two courtesies exist, `pause` and `identify`. Each tier gets a short list of them with a limit,
drawn from one budget every peer shares: at most one pause a minute and one identify every ten
seconds, across all of them together, because new keys cost nothing and a limit per peer would
stop nobody (docs/passport/trust.md#the-tiers). A peer can never have the robot `stop`: it
asks for a pause, which is bounded and ends on its own.

What is granted runs through the executor with the source `peer`, so every gate the executor has
still applies, and two of its own: a peer can reach no verb but the two a courtesy runs (`peer`),
and while a pause holds nothing that moves the body is admitted, whoever asks (`paused`). A pause
ends whatever verb is moving the body, then sends the executor's own stop and opens that window,
and an identify is the body's voice. Neither costs the pilot a step. Courtesies are taken only
while the executor is open to them (`Executor.open_to_peers`), never while a run parks its arm or
hands it to a person, and a pause is never granted over an arm in a person's hands.

The handler runs on whatever thread a message arrived on, an HTTP server's or a link's, and
hands the verb to the run's event loop, answering within the five seconds the specification
allows (spec 6.5), or saying it could not.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import math
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

#: The longest pause each tier may ask for, in seconds, and how each may ask to be identified.
#: `same owner` takes the owner's own list, at a known robot's limits.
PAUSE_S = {"stranger": 5, "known": 10, "same owner": 10}
IDENTIFY_BY = {
    "stranger": ("light", "sound"),
    "known": ("light", "sound", "motion"),
    "same owner": ("light", "sound", "motion"),
}
#: The one budget every peer draws from.
PAUSE_EVERY_S = 60.0
IDENTIFY_EVERY_S = 10.0
#: How long the handler waits for the executor before it answers that it could not.
ANSWER_WITHIN_S = 4.0


@dataclass(frozen=True)
class Decision:
    """What an `ask` came to: `ok`, `no` or `later`, and one line saying why."""

    result: str
    reason: str


def _no(reason: str) -> Decision:
    return Decision("no", reason)


def _why_not(e: Exception) -> str:
    """A refusal as a robot met reads it: this robot's own words, never an exception's, which
    can name a port, a host or the task's allowlist."""
    from quackd.safety import ConfirmDenied, SafetyStop, VerbNotAllowed

    if isinstance(e, VerbNotAllowed):
        return "the task this robot is on does not allow it"
    if isinstance(e, ConfirmDenied):
        return "a person here did not allow it"
    if isinstance(e, SafetyStop):
        return "this robot has stopped its run"
    return "it could not do it just now"


CLOSED = Decision("later", "this robot is not taking courtesies while it starts or stops")


class Courtesy:
    """One robot's courtesies, over the executor that runs its verbs and the loop it runs on."""

    def __init__(
        self,
        executor: Any,
        loop: asyncio.AbstractEventLoop,
        *,
        stranger_pauses: bool = True,
        same_owner: Sequence[str] = ("pause", "identify"),
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.executor = executor
        self.loop = loop
        self.stranger_pauses = stranger_pauses
        self.same_owner = tuple(same_owner)
        self.clock = clock
        self._lock = threading.Lock()
        self._last_pause = -math.inf
        self._last_identify = -math.inf

    def allowed(self, tier: str) -> tuple[str, ...]:
        """The courtesies `tier` may ask for."""
        if tier == "stranger":
            return ("pause", "identify") if self.stranger_pauses else ("identify",)
        if tier == "known":
            return ("pause", "identify")
        return self.same_owner

    def decide(self, tier: str, skill: str, args: dict[str, Any]) -> Decision:
        """The answer to one `ask`, the courtesy done first when it is granted."""
        if skill == "stop":
            return _no("a robot met never stops this one: it can ask for a pause, which ends")
        allowed = self.allowed(tier)
        if skill not in allowed:
            listed = " and ".join(allowed) if allowed else "nothing"
            return _no(f"a robot in the {tier} tier may ask this one for {listed}")
        if not getattr(self.executor, "open_to_peers", False):
            # a run that is parking its arm, or handing it to a person, moves it where no gate
            # sees, so a pause granted then could not be kept (`Executor.open_to_peers`)
            return CLOSED
        if skill == "pause":
            return self._pause(tier, args)
        return self._identify(tier, args)

    def _pause(self, tier: str, args: dict[str, Any]) -> Decision:
        seconds = args.get("seconds")
        if not isinstance(seconds, int) or isinstance(seconds, bool) or not 1 <= seconds <= 10:
            return _no("a pause takes seconds, a whole number from 1 to 10")
        if seconds > PAUSE_S[tier]:
            return _no(
                f"a robot in the {tier} tier may ask for a pause of {PAUSE_S[tier]} s at most"
            )
        if getattr(getattr(self.executor, "transport", None), "in_hand", None) is True:
            # an arm's stop takes it back from the hands it was given to (the LeRobot arm's
            # `take_hold`), which is not a robot met's to ask
            return _no("a person is holding this robot, and a stop would take it from them")
        with self._lock:
            wait = self._last_pause + PAUSE_EVERY_S - self.clock()
            if wait > 0:
                return Decision(
                    "later", f"this robot pauses once a minute, the next in {wait:.0f} s"
                )
            self._last_pause = self.clock()
        return self._run(self._paused(seconds))

    def _identify(self, tier: str, args: dict[str, Any]) -> Decision:
        how = args.get("how", "sound")
        if how not in IDENTIFY_BY[tier]:
            ways = " or ".join(IDENTIFY_BY[tier])
            return _no(f"a robot in the {tier} tier may ask this one to identify itself by {ways}")
        if how != "sound":
            return _no(f"this body has no way to identify itself by {how}, only by sound")
        with self._lock:
            wait = self._last_identify + IDENTIFY_EVERY_S - self.clock()
            if wait > 0:
                return Decision("later", "this robot identifies itself once every ten seconds")
            self._last_identify = self.clock()
        return self._run(self._identified())

    def _run(self, coroutine: Any) -> Decision:
        """The courtesy done on the executor's loop, and its answer, whatever happens: a loop
        that has closed, a verb that was cancelled or raised, and a clock that ran out each
        answer `later`, rather than leaving the asker with a dropped connection."""
        try:
            on_the_loop = asyncio.get_running_loop() is self.loop
        except RuntimeError:
            on_the_loop = False
        if on_the_loop:
            # waiting here would stall the very loop that has to run the verb, its stop among
            # everything else, so a handler wired to the wrong thread answers rather than waits
            coroutine.close()
            return Decision("later", "this robot could not do it in time")
        try:
            future = asyncio.run_coroutine_threadsafe(coroutine, self.loop)
        except RuntimeError:  # the loop has closed: the run is over
            coroutine.close()
            return CLOSED
        try:
            result: Decision = future.result(timeout=ANSWER_WITHIN_S)
        except concurrent.futures.TimeoutError:
            future.cancel()
            return Decision("later", "this robot could not do it in time")
        except (concurrent.futures.CancelledError, Exception):
            return Decision("later", "this robot could not do it just now")
        return result

    async def _paused(self, seconds: int) -> Decision:
        # The window read again here, on the loop: the handler read it on the thread the ask
        # came on, and a run that closed it since has begun to fold its arm where no gate sees.
        if not self.executor.open_to_peers:
            return CLOSED
        # The hold first: it ends whatever verb is moving the body and refuses the next, so the
        # stop after it is the last intent the body gets. In the other order a verb still
        # running sent its next twist after the stop and started the body again.
        self.executor.pause_for(seconds)
        try:
            result = await self.executor.run_verb("stop", source="peer")
        except Exception as e:  # a refusal, or a link that dropped
            return _no(f"it could not stop: {_why_not(e)}")
        if not result.ok:
            return _no("it could not stop")
        return Decision("ok", f"stopped, and holding still for {seconds} s")

    async def _identified(self) -> Decision:
        if not self.executor.open_to_peers:
            return CLOSED
        try:
            result = await self.executor.run_verb("say", {"text": "here"}, source="peer")
        except Exception as e:  # a refusal, or a link that dropped
            return _no(_why_not(e))
        if not result.ok:
            return _no("it could not make a sound")
        return Decision("ok", "identified itself by sound")


__all__ = ["IDENTIFY_BY", "PAUSE_S", "Courtesy", "Decision"]
