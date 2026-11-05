"""A `laya` that loads no weights.

The in-process decision LLM is the one backend with no wire between quackd and the LLM, so
the only way to test the seam is to stand where the LLM would be. Installed into
`sys.modules` for the life of one test, because the real package pulls torch and a suite that
imported it would be testing a download.

What it stands in for is the shape rather than the answer: `Router(...)` takes its keywords and
remembers them, `predict(state, questions, model=...)` records what it was asked and hands back
whatever the test scripted, and the return is a plain mapping in the shape `laya.agent` really
builds -- a hardcoded `model`, the `answers`, a `usage` carrying a real input count and a
hardcoded zero output, and the `routing` block `Router.predict` adds. That count is the reason
this row is costed measured rather than estimated, so a fake without it would have the suite
proving the wrong branch.
"""

from __future__ import annotations

import sys
import types
from typing import Any


class FakeRouter:
    """Laya's router, scripted.

    One instance stands for the class: `Router(...)` returns it, so a test can assert that a
    second turn did not build a second one. `loads` counts constructions, which is the whole
    question the lazy loader exists to answer.
    """

    def __init__(
        self,
        *,
        answers: dict[str, Any] | None = None,
        script: list[dict[str, Any]] | None = None,
        raises: Exception | None = None,
        routing: dict[str, Any] | None = None,
        rejects: tuple[str, ...] = (),
        input_tokens: int = 128,
    ) -> None:
        self.answers = answers or {}
        self.script = list(script or [])
        self.raises = raises
        """Raised by `predict`, for the turn that has to become a gate rather than a crash."""
        self.routing = routing or {"model": "typed-decisions", "reason": "asked for"}
        self.input_tokens = input_tokens
        """What `usage.input_tokens` reports. Laya counts the tokens it actually read, so
        a positive number here is what puts a turn on quackd's measured path; a test wanting
        the estimated path passes 0, which `_usage` reads as a field never filled in."""
        self.rejects = rejects
        """Constructor keywords this pretend Laya has never heard of, so a test can play the
        older package the real loader falls back for."""
        self.loads = 0
        """How many times the class was constructed. The lazy loader promises once."""
        self.kwargs: dict[str, Any] = {}
        """What the last construction was handed, so a test can assert `preload=True`."""
        self.calls: list[tuple[dict[str, Any], dict[str, Any], Any]] = []
        """(state, questions, model) per turn."""

    def __call__(self, **kwargs: Any) -> FakeRouter:
        """`laya.Router(...)`, which is this instance whatever it is asked for."""
        for name in self.rejects:
            if name in kwargs:
                raise TypeError(f"Router() got an unexpected keyword argument {name!r}")
        self.loads += 1
        self.kwargs = dict(kwargs)
        return self

    def predict(
        self, state: dict[str, Any], questions: dict[str, Any], model: Any = None
    ) -> dict[str, Any]:
        self.calls.append((dict(state), dict(questions), model))
        if self.raises is not None:
            raise self.raises
        answers = self.script.pop(0) if self.script else self.answers
        # The real shape: `laya/agent.py` hardcodes the LLM string, counts the input with
        # `int(attention_mask.sum())` and hardcodes the output at zero, and `Router.predict`
        # adds `routing` on top. A fake without `usage` had quackd's estimate path under test
        # and its measured path untested, which is backwards for this backend.
        return {
            "model": "laya-rl-agent",
            "answers": dict(answers),
            "usage": {"input_tokens": self.input_tokens, "output_tokens": 0},
            "routing": dict(self.routing),
        }


def answer_choice(label: str, confidence: float = 0.99) -> dict[str, Any]:
    """One choice answer as Laya spells it: a mapping, `probabilities` included.

    `agent.py` emits a probability per option on a choice, which is what gives these turns
    the `decide?` runner-up line in the log. A noul carries none, and does not.
    """
    rest = round((1.0 - confidence) or 0.0, 4)
    return {
        "choice": label,
        "confidence": confidence,
        "probabilities": {label: confidence, "_other": rest},
    }


def answer_noul(p: float) -> dict[str, Any]:
    return {"noul": p}


def turn(
    verb: str,
    confidence: float = 0.99,
    *,
    done: float = 0.0,
    need_human: float = 0.0,
    feasible: str = "feasible",
) -> dict[str, Any]:
    """One turn's four answers, in Laya's own mapping shape."""
    return {
        "next_verb": answer_choice(verb, confidence),
        "done": answer_noul(done),
        "need_human": answer_noul(need_human),
        "feasible": answer_choice(feasible, 0.9),
    }


def install(monkeypatch: Any, router: FakeRouter) -> FakeRouter:
    """Put `router` where `importlib.import_module("laya")` will find it, for one test."""
    module = types.ModuleType("laya")
    module.Router = router  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "laya", module)
    return router
