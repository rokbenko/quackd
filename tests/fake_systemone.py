"""A System One server that never leaves the machine.

Two faces, because the seam it stands in for is now two seams. Installed into `sys.modules` as
`typesafe_sdk`, it is the SDK `quackd.agent.decision.systemone` imports lazily and would
otherwise find whatever state a developer's machine happens to be in and nothing at all on CI,
which is two different suites. Handed straight to `Stepper.build` as `llm=`, it is a
`DecisionLLM` and the HTTP half is never involved at all -- which is what most of the stepper's
own tests want, because what they are testing is the gates rather than the transport.

The shapes are the ones docs.typesafe.ai publishes and every compatible server implements:
`system_one` takes a state and a mapping of question name to question, and the result has an
`answers` mapping whose members carry `choice`, `confidence` and `probabilities`, or a bare
`noul`. Nothing here is a model: the answers are whatever the test scripted, which is the
point.
"""

from __future__ import annotations

import sys
import types
from dataclasses import dataclass, field
from typing import Any


class TypeSafeError(Exception):
    pass


class APITimeoutError(TypeSafeError):
    pass


class APIConnectionError(TypeSafeError):
    pass


class RateLimitError(TypeSafeError):
    pass


@dataclass
class RetryPolicy:
    max_retries: int = 1
    backoff_max: float = 0.2
    timeout: float = 1.0


@dataclass
class ChoiceAnswer:
    choice: str
    confidence: float = 1.0
    probabilities: dict[str, float] = field(default_factory=dict)


@dataclass
class NoulAnswer:
    noul: float = 0.0


@dataclass
class Usage:
    """What the server says the call cost. Both counts are `int | None` in the real SDK, which
    documents None as "when the API did not report it", and that None is the difference between
    a cost quackd measured and one it estimated. A model running in this process reports
    neither, so it is the common case rather than the odd one."""

    input_tokens: int | None = None
    output_tokens: int | None = None


@dataclass
class SystemOneResult:
    answers: dict[str, Any]
    model: str = ""
    usage: Usage | None = None


class FakeDecisionLLM:
    """One scripted decision LLM.

    `answers` is what every turn returns unless `script` has something left, which is how a
    test says "choose `report_state` first and then be done". `raises` makes the call fail,
    which is the path that has to cost a turn rather than a run.
    """

    def __init__(
        self,
        *,
        answers: dict[str, Any] | None = None,
        script: list[dict[str, Any]] | None = None,
        raises: Exception | None = None,
        usage: Usage | None = None,
        name: str = "fake",
        model: str = "",
        url: str | None = None,
    ) -> None:
        self.answers = answers or {}
        self.script = list(script or [])
        self.raises = raises
        self.usage = usage
        """What every turn reports spending, or None for a backend that reported nothing,
        which is the path where quackd has to estimate the size of its own question."""
        self.calls: list[tuple[dict[str, Any], dict[str, Any]]] = []
        """(state, questions) per turn, so a test can assert what was and was not sent."""
        # What the protocol asks of a decision LLM. A test that hands this straight to
        # `Stepper.build` never touches the three constructor fields below.
        self.name = name
        self.model = model
        self.url = url
        # What the SDK constructor was called with, for the tests that care that a preset
        # reached the client as its row says: a server that wants no key gets a word rather
        # than whatever hosted key is in the environment, and a preset's port is its own.
        self.api_key: str | None = None
        self.base_url: str | None = None
        self.retry: RetryPolicy | None = None
        self.timeout: float | None = None
        """The client's own clock on each operation of a request, as the constructor was handed
        it: the one that would race the stepper's second if it sat on it."""

    def __call__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str = "",
        retry: RetryPolicy | None = None,
        timeout: float | None = None,
        **rest: Any,
    ) -> FakeDecisionLLM:
        """The SDK's own constructor shape, so `SystemOneLLM` builds this the same way."""
        self.api_key = api_key
        self.base_url = base_url
        self.model = model or self.model
        self.retry = retry
        self.timeout = timeout
        return self

    async def system_one(self, state: Any, questions: dict[str, Any]) -> SystemOneResult:
        self.calls.append((dict(state), dict(questions)))
        if self.raises is not None:
            raise self.raises
        answers = self.script.pop(0) if self.script else self.answers
        return SystemOneResult(answers=dict(answers), model=self.model, usage=self.usage)

    async def decide(self, state: Any, questions: dict[str, Any]) -> SystemOneResult:
        """The protocol's own method. Both faces give the same scripted answer, so a test can
        hand this to a `Stepper` and never build a client at all."""
        return await self.system_one(state, questions)


def choice(label: str, confidence: float = 0.99, **rest: float) -> ChoiceAnswer:
    spread = {label: confidence, **rest}
    return ChoiceAnswer(choice=label, confidence=confidence, probabilities=spread)


def turn(
    verb: str,
    confidence: float = 0.99,
    *,
    done: float = 0.0,
    need_human: float = 0.0,
    feasible: str = "feasible",
) -> dict[str, Any]:
    """One turn's four answers, in the shape `Stepper._route` reads them."""
    return {
        "next_verb": choice(verb, confidence),
        "done": NoulAnswer(done),
        "need_human": NoulAnswer(need_human),
        "feasible": choice(feasible, 0.9),
    }


def wire(answers: dict[str, Any], usage: dict[str, Any] | None = None) -> dict[str, Any]:
    """The same turn as a server's own JSON rather than as the SDK's objects.

    A server's response body and an in-process model's return value are both plain mappings,
    and `_answer` has to read one as readily as it reads the other. This is how a test says
    "the same answers, off the wire".
    """
    return {"answers": answers, **({"usage": usage} if usage is not None else {})}


def install(monkeypatch: Any, fake: FakeDecisionLLM) -> FakeDecisionLLM:
    """Put `fake` where `importlib.import_module("typesafe_sdk")` will find it, for one test."""
    module = types.ModuleType("typesafe_sdk")
    module.AsyncTypeSafeClient = fake  # type: ignore[attr-defined]
    module.TypeSafeClient = fake  # type: ignore[attr-defined]
    module.Usage = Usage  # type: ignore[attr-defined]
    module.RetryPolicy = RetryPolicy  # type: ignore[attr-defined]
    module.TypeSafeError = TypeSafeError  # type: ignore[attr-defined]
    module.APITimeoutError = APITimeoutError  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "typesafe_sdk", module)
    return fake
