"""OpenAI's SDK as `quackd.agent.decision.decisions_api` reaches it, never leaving the machine.

Installed into `sys.modules` as `openai`, it is the module `decisions_api.py` imports lazily and
would otherwise find in whatever state a developer's machine happens to be in, and not at all
on CI, which installs no provider SDK: two different suites. It stands in for exactly the three
things the backend touches -- the client class, the client's generic `post`, and the `Omit`
marker -- and records what each was handed, so a test can say what the client was built with,
which header was omitted and what went out in the body.

The answers are in the shapes OpenAI's API reference publishes for `POST /v1/decisions`, read
2026-10-08: a decision is `{answers, model, usage}`, every answer names the question it
answers, a `predicate` carries a `probability`, a `choice` its `choice`, `confidence` and a list
of `{value, probability}`, and a `refusal` carries its name and nothing else. Nothing here is a
model: the answers are whatever the test scripted, which is the point.

What this cannot prove is what the real SDK does with the headers it is handed. That is a
property of the SDK rather than of quackd, and a test that drives the real one is the only
thing that can show it: `tests/test_decisions_api_wire.py`, wherever the SDK is installed.
"""

from __future__ import annotations

import sys
import types
from collections.abc import Callable, Iterable, Mapping
from typing import Any


class Omit:
    """The SDK's marker for "send no header by this name", as far as the backend uses it."""

    def __bool__(self) -> bool:
        return False

    def __repr__(self) -> str:
        return "Omit()"


class APIStatusError(Exception):
    """An HTTP error the way the SDK raises one, for the tests that want a call to fail: the
    status on the exception, and the body the server sent back on it as well."""

    def __init__(self, message: str, *, status_code: int, body: Any = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.body = body


class BadRequestError(APIStatusError):
    pass


class RateLimitError(APIStatusError):
    pass


def status_error(cls: type[APIStatusError], status: int, body: Any) -> APIStatusError:
    """`cls` the way the SDK builds one from a response it was sent back: the decoded body goes
    in the message after the status, `Error code: 400 - {body}` (`openai/_base_client.py`,
    `_make_status_error_from_response`, 3.22.1), so whatever the body says, the message says."""
    return cls(f"Error code: {status} - {body}", status_code=status, body=body)


USAGE: dict[str, Any] = {
    "input_tokens": 42,
    "input_tokens_details": {"cached_tokens": 0, "cache_write_tokens": 0},
    "output_tokens": 0,
    "output_tokens_details": {"reasoning_tokens": 0},
    "total_tokens": 42,
}
"""The usage block of the API reference's own example response, field for field: a count of
input tokens and no output, because a decision bills only what it reads."""


def predicate(name: str | None, probability: Any) -> dict[str, Any]:
    return {"type": "predicate", "name": name, "probability": probability}


def choice(
    name: str | None,
    value: Any,
    confidence: Any = 0.99,
    others: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """A choice answer whose distribution puts `confidence` on `value` and whatever `others`
    says on the rest, as the list of `{value, probability}` the API returns."""
    spread = [{"value": value, "probability": confidence}]
    spread += [{"value": other, "probability": p} for other, p in (others or {}).items()]
    return {
        "type": "choice",
        "name": name,
        "choice": value,
        "confidence": confidence,
        "probabilities": spread,
    }


def refusal(name: str | None) -> dict[str, Any]:
    return {"type": "refusal", "name": name}


def decision(
    answers: Iterable[Mapping[str, Any]],
    *,
    usage: Mapping[str, Any] | None = USAGE,
    model: str = "gpt-6-luna",
) -> dict[str, Any]:
    """A whole response body, in the order the answers were given."""
    return {"answers": [dict(a) for a in answers], "model": model, "usage": usage}


def turn(
    verb: str,
    confidence: float = 0.99,
    *,
    done: float = 0.0,
    need_human: float = 0.0,
    feasible: str = "feasible",
    refuse: Iterable[str] = (),
) -> list[dict[str, Any]]:
    """One turn's four answers in the shape the API sends them, any of them refused by name."""
    declined = set(refuse)
    answers = [
        choice("next_verb", verb, confidence),
        predicate("done", done),
        predicate("need_human", need_human),
        choice("feasible", feasible, 0.9),
    ]
    return [refusal(a["name"]) if a["name"] in declined else a for a in answers]


class FakeOpenAI:
    """One scripted OpenAI client, standing where `openai.AsyncOpenAI` would.

    `answers` is what every request returns unless `script` has a whole response left, which is
    how a test says "answer this first and that next". `raises` makes a request fail, which is
    the path that has to cost a turn rather than a run: an exception, raised as it is, or a
    function of the request's body that builds one, for a server whose error quotes the
    request back.
    """

    def __init__(
        self,
        *,
        answers: Iterable[Mapping[str, Any]] = (),
        script: Iterable[Any] = (),
        raises: Exception | Callable[[Any], Exception] | None = None,
        usage: Mapping[str, Any] | None = USAGE,
    ) -> None:
        self.answers = [dict(a) for a in answers]
        self.script = list(script)
        self.raises = raises
        self.usage = usage
        self.kwargs: dict[str, Any] = {}
        """What the client class was called with, as the SDK's constructor would have been."""
        self.posts: list[dict[str, Any]] = []
        """Every request, as `{path, cast_to, body, options}`."""

    def __call__(self, **kwargs: Any) -> FakeOpenAI:
        """The constructor's shape, so `DecisionsAPILLM` builds this the way it builds the real
        one, and the test keeps hold of what it was handed."""
        self.kwargs = kwargs
        return self

    async def post(
        self,
        path: str,
        *,
        cast_to: Any,
        body: Any = None,
        options: Mapping[str, Any] | None = None,
        **rest: Any,
    ) -> Any:
        self.posts.append(
            {"path": path, "cast_to": cast_to, "body": body, "options": dict(options or {})}
        )
        if isinstance(self.raises, BaseException):
            raise self.raises
        if self.raises is not None:
            raise self.raises(body)
        if self.script:
            return self.script.pop(0)
        return decision(self.answers, usage=self.usage)

    @property
    def headers(self) -> dict[str, Any]:
        """The `default_headers` the client was built with."""
        return dict(self.kwargs.get("default_headers") or {})


class SDKLookalike(FakeOpenAI):
    """The fake under the real SDK's module name and with the real client's `base_url`, which
    is how the suite's guard (`tests/conftest.py`, `openai_address`) knows a real client when it
    sees one. Proving that guard fires takes a client it mistakes for the real one, on a machine
    without the SDK, and one that still never leaves the process if the guard does not fire."""

    @property
    def base_url(self) -> str:
        """As the SDK gives it: the address it was built with, ending in a slash."""
        return str(self.kwargs.get("base_url") or "").rstrip("/") + "/"


SDKLookalike.__module__ = "openai"


def install(monkeypatch: Any, fake: FakeOpenAI) -> FakeOpenAI:
    """Put `fake` where `importlib.import_module("openai")` will find it, for one test."""
    module = types.ModuleType("openai")
    module.AsyncOpenAI = fake  # type: ignore[attr-defined]
    module.Omit = Omit  # type: ignore[attr-defined]
    module.APIStatusError = APIStatusError  # type: ignore[attr-defined]
    module.BadRequestError = BadRequestError  # type: ignore[attr-defined]
    module.RateLimitError = RateLimitError  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "openai", module)
    return fake
