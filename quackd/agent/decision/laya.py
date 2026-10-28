"""Laya, in this process: a decision LLM with no server, no network and no key.

The other presets are servers and share a client. This one is an encoder you import, and it
earns its own file for one reason: it is the proof that the seam here is a protocol rather
than an HTTP call. Everything quackd asks of a decision LLM it asks of this one too, and the
questions that go to a hosted API go to this unchanged, because Laya's own `predict` takes the
same `{"type": ..., "instructions": ..., "criteria": ...}` mapping the wire format does.

What it returns beside the answers is a real token count, `int(attention_mask.sum())` for
the input and a hardcoded zero for the output, so a turn it answers is recorded measured
rather than estimated. It still costs nothing, because a checkpoint in this process is
charged at the self-hosted rate: it billed you in electricity.

Imported only when a run has named it. `torch` is not something `quackd --help` may pay for.
"""

from __future__ import annotations

import asyncio
import importlib
import inspect
import threading
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from quackd.agent.decision.base import DecisionNotInstalled

if TYPE_CHECKING:
    from quackd.agent.decision.catalogue import DecisionSpec


class LayaLLM:
    """Laya's router, loaded on the turn that first needs it.

    The one preset built lazily, and deliberately so: the others construct an HTTP client in
    microseconds, and this one loads weights -- seven seconds cold, and a download the first
    time ever. Paying that while the CLI parses would stall every run that never reaches a
    turn. A load that fails is remembered, so a broken install costs one attempt rather than
    one per turn.
    """

    def __init__(self, spec: DecisionSpec, *, model: str | None = None):
        self.name = spec.name
        self.model = model or spec.model or "laya"
        self.url: str | None = None
        self._extra = spec.extra or "laya"
        self._router: Any = None
        self._broken: Exception | None = None
        self._passes = threading.Lock()

    def _load(self) -> Any:
        try:
            laya = importlib.import_module("laya")
        except ImportError as e:
            raise DecisionNotInstalled(self.name, self._extra) from e
        # `preload=True` because the alternative is rebuilding the model per call, which Laya's
        # own README measures at 7.4 seconds on a CPU against a timeout of one second.
        #
        # `default` so that what is preloaded is the checkpoint this run will actually ask for.
        # Laya holds one model at a time unless told otherwise, and its default is the plain
        # English one, so preloading blind and then asking for `typed-decisions` pays the load
        # twice and throws the first one away. Tried and then dropped rather than assumed,
        # because the keyword is younger than the class and quackd would rather load the wrong
        # checkpoint once than refuse to run at all.
        # Asked of the signature rather than discovered by catching `TypeError`, because a
        # `TypeError` raised *inside* somebody's `Router.__init__` is indistinguishable from
        # one raised by the call not matching it -- and swallowing the first would silently
        # load the wrong checkpoint, which is the outcome this is here to avoid.
        if "default" in inspect.signature(laya.Router).parameters:
            return laya.Router(preload=True, default=self.model)
        return laya.Router(preload=True, max_loaded=2)

    async def warm(self) -> None:
        """Load the weights now, off the loop, rather than on the first turn that asks.

        `quackd drive` calls this before its prompt opens, so the first line a person types is
        not the one that pays the load. Never raises: a load that fails is remembered as one in
        `decide` is, and `decide` raises it, so the session goes on and its first question
        reaches the pilot the way any failed question does."""
        if self._router is not None or self._broken is not None:
            return
        try:
            self._router = await asyncio.to_thread(self._load)
        except Exception as e:
            self._broken = e

    async def decide(
        self, state: Mapping[str, str], questions: Mapping[str, Mapping[str, Any]]
    ) -> Any:
        if self._broken is not None:
            raise self._broken
        if self._router is None:
            try:
                self._router = await asyncio.to_thread(self._load)
            except Exception as e:
                self._broken = e
                raise
        # `to_thread` because `predict` is a blocking forward pass and the loop it would block
        # is the one holding the robot's deadman.
        return await asyncio.to_thread(self._predict, dict(state), dict(questions))

    def _predict(self, state: dict[str, str], questions: dict[str, Any]) -> Any:
        """One forward pass at a time: a drive session's fast lane and a goal's stepper ask the
        one router, each from a thread of its own, and nothing says its weights may be run from
        two threads at once."""
        with self._passes:
            return self._router.predict(state, questions, model=self.model)
