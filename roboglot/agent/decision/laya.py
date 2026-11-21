"""Laya, in this process: a decision LLM with no server, no network and no key.

The other presets are servers and share a client. This one is an encoder you import, and it
earns its own file for one reason: it is the proof that the seam here is a protocol rather
than an HTTP call. Everything Roboglot asks of a decision LLM it asks of this one too, and the
questions that go to a hosted API go to this unchanged, because Laya's own `predict` takes the
same `{"type": ..., "instructions": ..., "criteria": ...}` mapping the wire format does.

What it returns beside the answers is a real token count, `int(attention_mask.sum())` for
the input and a hardcoded zero for the output, so a turn it answers is recorded measured
rather than estimated. It still costs nothing, because a checkpoint in this process is
charged at the self-hosted rate: it billed you in electricity.

Imported only when a run has named it. `torch` is not something `roboglot --help` may pay for.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import importlib
import inspect
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from roboglot.agent.decision.base import (
    LOAD_STILL_RUNNING,
    PASS_STILL_RUNNING,
    Busy,
    DecisionNotInstalled,
)

if TYPE_CHECKING:
    from roboglot.agent.decision.catalogue import DecisionSpec


@dataclass
class _Work:
    """A load or a forward pass on its thread, and how many turns are still waiting for it."""

    done: concurrent.futures.Future[Any]
    waiting: int = 0
    """Turns awaiting it now. Nought while it still runs means the turn that started it gave up
    on it at its bound, and so did every turn that joined it, and a turn that comes along
    meanwhile is refused."""


def _on_its_own_thread(work: Callable[[], Any], name: str) -> concurrent.futures.Future[Any]:
    """`work` on a thread of its own, its result or its exception in the future returned.

    Not `asyncio.to_thread`. A turn's bound cancels the await and never the work, and the work
    has to go on to its end and be kept, which a cancelled `to_thread` does not promise: a call
    still queued in the default executor is dropped by the cancel. And that executor is shared
    with the frame save, the detector and name lookups, which a load of seconds or a slow pass
    should not hold up. The future is marked running before the thread starts, so cancelling an
    await of it cancels the wait alone. Not a daemon, as the executor's threads are not: a
    process that ends waits for a pass to finish rather than tearing down its weights under it."""
    done: concurrent.futures.Future[Any] = concurrent.futures.Future()
    done.set_running_or_notify_cancel()

    def run() -> None:
        try:
            done.set_result(work())
        except BaseException as e:  # handed to whichever turn awaits it, which raises it
            done.set_exception(e)

    threading.Thread(target=run, name=name).start()
    return done


class LayaLLM:
    """Laya's router, loaded on the turn that first needs it.

    The one preset built lazily, and deliberately so: the others construct an HTTP client in
    microseconds, and this one loads weights -- seven seconds cold, and a download the first
    time ever. Paying that while the CLI parses would stall every run that never reaches a
    turn. A load that fails is remembered, so a broken install costs one attempt rather than
    one per turn.

    The turn's own bound (one second in the stepper and the fast lane) cancels the wait, not the
    load or the pass, which go on in their threads. So the load is kept when it ends, whoever is
    still waiting for it, and the first turn after that is answered; and while a load or a pass
    no turn is waiting for any more is still running, a turn starts nothing behind it and is
    refused with `base.Busy`. A turn that comes while another is still waiting joins it: two
    callers at once, a drive session's fast lane and a goal's stepper, are each answered in turn.
    At most one load and one pass exist at any moment.
    """

    def __init__(self, spec: DecisionSpec, *, llm_id: str | None = None):
        self.name = spec.name
        self.llm_id = llm_id or spec.llm_id or "laya"
        self.url: str | None = None
        self._extra = spec.extra or "laya"
        self._router: Any = None
        self._broken: Exception | None = None
        self._passes = threading.Lock()
        self._state = threading.Lock()
        """Held only to read and change the three fields below, never across a wait."""
        self._loading: _Work | None = None
        self._passing: _Work | None = None

    def _load(self) -> Any:
        try:
            laya = importlib.import_module("laya")
        except ImportError as e:
            raise DecisionNotInstalled(self.name, self._extra) from e
        # `preload=True` because the alternative is rebuilding the LLM per call, which Laya's
        # own README measures at 7.4 seconds on a CPU against a timeout of one second.
        #
        # `default` so that what is preloaded is the checkpoint this run will actually ask for.
        # Laya holds one LLM at a time unless told otherwise, and its default is the plain
        # English one, so preloading blind and then asking for `typed-decisions` pays the load
        # twice and throws the first one away. Tried and then dropped rather than assumed,
        # because the keyword is younger than the class and Roboglot would rather load the wrong
        # checkpoint once than refuse to run at all.
        # Asked of the signature rather than discovered by catching `TypeError`, because a
        # `TypeError` raised *inside* somebody's `Router.__init__` is indistinguishable from
        # one raised by the call not matching it -- and swallowing the first would silently
        # load the wrong checkpoint, which is the outcome this is here to avoid.
        if "default" in inspect.signature(laya.Router).parameters:
            return laya.Router(preload=True, default=self.llm_id)
        return laya.Router(preload=True, max_loaded=2)

    async def warm(self) -> None:
        """Load the weights now, off the loop, rather than on the first turn that asks.

        `roboglot drive` calls this before its prompt opens, so the first line a person types is
        not the one that pays the load. Never raises: a load that fails is remembered as one in
        `decide` is, and `decide` raises it, so the session goes on and its first question
        reaches the pilot the way any failed question does. It joins a load already running,
        whoever started it."""
        try:
            await self._loaded(join=True)
        except Exception as e:
            # the load keeps its own failure as `_broken` already (`_load_and_keep`)
            self._broken = self._broken or e

    def _load_and_keep(self) -> Any:
        """The load, on its own thread, keeping what it comes to whether or not any turn is
        still waiting for it."""
        try:
            router = self._load()
        except Exception as e:
            self._broken = e
            raise
        self._router = router
        return router

    async def _loaded(self, *, join: bool = False) -> None:
        """Return once the router is loaded, starting the load if none has started.

        A load already running is joined where a turn is still waiting for it, or where `join`
        says so, and otherwise refused, because the turn that started it gave up at its bound."""
        with self._state:
            if self._broken is not None:
                raise self._broken
            if self._router is not None:
                return
            load = self._loading
            if load is None or load.done.done():
                load = self._loading = _Work(_on_its_own_thread(self._load_and_keep, "laya-load"))
            elif load.waiting == 0 and not join:
                raise Busy(LOAD_STILL_RUNNING)
            load.waiting += 1
        try:
            await asyncio.wrap_future(load.done)
        finally:
            with self._state:
                load.waiting -= 1

    async def decide(
        self, state: Mapping[str, str], questions: Mapping[str, Mapping[str, Any]]
    ) -> Any:
        await self._loaded()
        asked, said = dict(state), dict(questions)
        # Its own thread because `predict` is a blocking forward pass and the loop it would
        # block is the one holding the robot's deadman.
        while True:
            with self._state:
                running = self._passing
                if running is None or running.done.done():
                    mine = self._passing = _Work(
                        _on_its_own_thread(lambda: self._predict(asked, said), "laya-pass"),
                        waiting=1,
                    )
                    break
                if running.waiting == 0:
                    raise Busy(PASS_STILL_RUNNING)
                running.waiting += 1
            # Another caller's pass, which that caller is still waiting for: this one waits its
            # turn and then runs its own. `asyncio.wait` because that pass's answer or failure
            # is the other caller's to read, not this one's.
            waited = asyncio.wrap_future(running.done)
            try:
                await asyncio.wait([waited])
            finally:
                with self._state:
                    running.waiting -= 1
                if waited.done() and not waited.cancelled():
                    # read, so that asyncio does not log the other caller's failure as one
                    # nobody retrieved; that caller reads it from its own future
                    waited.exception()
                else:
                    waited.cancel()
        try:
            return await asyncio.wrap_future(mine.done)
        finally:
            with self._state:
                mine.waiting -= 1

    def _predict(self, state: dict[str, str], questions: dict[str, Any]) -> Any:
        """One forward pass at a time: a drive session's fast lane and a goal's stepper ask the
        one router, each from a thread of its own, and nothing says its weights may be run from
        two threads at once."""
        with self._passes:
            return self._router.predict(state, questions, model=self.llm_id)
