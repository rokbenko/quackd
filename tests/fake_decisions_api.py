"""OpenAI's SDK as `quackd.agent.decision.decisions_api` reaches it, never leaving the machine.

Installed into `sys.modules` as `openai`, it is the module `decisions_api.py` imports lazily and
would otherwise find in whatever state a developer's machine happens to be in, and not at all
on CI, which installs no provider SDK: two different suites. It stands in for exactly the things
the backend touches -- the client class, the client's generic `post` and the headers it reads
back, the `Omit` marker, the HTTP client the SDK is handed and the release it calls itself --
and records what each was handed, so a test can say what the client was built with, which
header was omitted and what went out in the body.

Because CI runs this and never the SDK, the fake refuses every call to `post` that a release
the extra allows would refuse, rather than taking whatever it is handed: otherwise a call the
real client turns down passes every test on CI, and on a real run each stepper turn ends on
`gate: error` while the run exits 0.

The answers are in the shapes OpenAI's API reference publishes for `POST /v1/decisions`, read
2026-10-08: a decision is `{answers, model, usage}`, every answer names the question it
answers, a `predicate` carries a `probability`, a `choice` its `choice`, `confidence` and a list
of `{value, probability}`, and a `refusal` carries its name and nothing else. Nothing here is a
model: the answers are whatever the test scripted, which is the point.

What one installed SDK does with the headers it is handed is a property of that SDK rather than
of quackd, and only a test that drives the real one can show it:
`tests/test_decisions_api_wire.py`, wherever the SDK is installed, which is the locked release
and no other. So this fake also carries a model of how each kind of release the extra allows
merges the headers quackd hands it with the lines of `OPENAI_CUSTOM_HEADERS` (`RELEASES`), read
from the releases themselves, so that CI can hold quackd's rule to all of them and not only to
the one a developer happens to have.
"""

from __future__ import annotations

import os
import sys
import types
from collections.abc import Callable, Iterable, Mapping
from types import MappingProxyType
from typing import Any

__version__ = "0.0.0+fake"
"""What the module says its release is, which a refusal that names the release quotes."""


class Omit:
    """The SDK's marker for "send no header by this name", as far as the backend uses it."""

    def __bool__(self) -> bool:
        return False

    def __repr__(self) -> str:
        return "Omit()"


class DefaultAsyncHttpxClient:
    """The HTTP client the SDK offers with its own defaults, as far as the backend uses it:
    built with whatever it was handed, which a test reads back. The real one follows redirects
    unless it is told not to."""

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.follow_redirects = bool(kwargs.get("follow_redirects", True))


# ── how a release merges the headers it is handed ───────────────────────────────────────
#
# Read from the releases in the uv cache on 2026-10-10 (`openai/_client.py` and
# `openai/_base_client.py` in each). Every release that reads `OPENAI_CUSTOM_HEADERS` parses it
# the same way, and they differ in what they do next, which is all a model here has to say.


def variable() -> dict[str, str] | None:
    """`OPENAI_CUSTOM_HEADERS` as every release that reads it parses it: split on newlines, the
    name before the first colon and the value after it, both trimmed, a later line with the same
    name replacing an earlier one. None when the variable is unset, which every release reads as
    no lines at all."""
    raw = os.environ.get("OPENAI_CUSTOM_HEADERS")
    if raw is None:
        return None
    parsed: dict[str, str] = {}
    for line in raw.split("\n"):
        colon = line.find(":")
        if colon >= 0:
            parsed[line[:colon].strip()] = line[colon + 1 :].strip()
    return parsed


def _without_authorization(parsed: Mapping[str, str], handed: Mapping[str, Any]) -> dict[str, str]:
    """3.6.0 and later: a caller who names an authorization header, in any spelling and whatever
    its value, makes the release drop every authorization line of the variable."""
    if any(name.lower() == "authorization" for name in handed):
        return {name: value for name, value in parsed.items() if name.lower() != "authorization"}
    return dict(parsed)


def _custom(release: str, handed: Mapping[str, Any]) -> dict[str, Any]:
    """The custom headers a release keeps: the variable's lines, then the caller's over them."""
    parsed = variable()
    if release == "1.66" or parsed is None:
        return dict(handed)
    if release == "backwards":
        return {**handed, **parsed}
    if release != "3.1":
        parsed = _without_authorization(parsed, handed)
    return {**parsed, **handed}


RELEASES = ("1.66", "3.1", "3.6", "3.22")
"""Four kinds of release inside the extra's range, by the first cached release of each.
`1.66`, and 1.83.0 with it, never reads the variable. `3.1` adds every line of it and lets a
header the caller names replace a line only when the two are spelled exactly alike. `3.6`, and
every cached release up to 3.19.0, also drops the variable's authorization lines when the caller
names one. `3.22`, the locked release, merges every name without regard to case besides, the
last spelling winning. A fifth, `backwards`, is no release at all: the variable's lines over the
caller's, which is the SDK a backstop exists for."""


def sent(fake: FakeOpenAI, release: str) -> list[tuple[str, str]]:
    """The header lines `release` would put on a request from this client, in order, a name
    sent twice in two spellings showing twice. The SDK's own `Authorization` from the key comes
    first, its default headers after it, and an `Omit` takes out what it names: by exact name in
    every release but `3.22`, which merges without regard to case."""
    own: dict[str, Any] = {"Authorization": f"Bearer {fake.kwargs.get('api_key')}"}
    custom = _custom(release, fake.headers)
    if release != "3.22":
        merged = {**own, **custom}
        return [(name, value) for name, value in merged.items() if not isinstance(value, Omit)]
    lines: dict[str, tuple[str, str]] = {}
    for name, value in [*own.items(), *custom.items()]:
        if isinstance(value, Omit):
            lines.pop(name.lower(), None)
        else:
            lines[name.lower()] = (name, value)
    return list(lines.values())


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


# ── how a release takes a request ───────────────────────────────────────────────────────
#
# Read from `AsyncAPIClient.post` and `RequestOptions` in the eleven releases uv had cached, from
# 1.66.0, the extra's floor, to 3.22.1, the locked one (`openai/_base_client.py` and
# `openai/_types.py` in each, 2026-10-11), and run in three of them, 1.66.0, 3.1.0 and 3.22.1, by
# `tests/test_decisions_api_wire.py`.

OPTIONS = frozenset(
    {"headers", "max_retries", "timeout", "params", "extra_json", "idempotency_key"}
)
"""The per-request options 1.66.0 lists in `RequestOptions`, and so every release read lists:
1.83.0 adds `follow_redirects`, and 3.1.0 and the releases after it `security` and
`synthesize_event_and_data` besides. The three that were run, 1.66.0, 3.1.0 and 3.22.1, all build
a request's options with pydantic's `model_construct`, which leaves out a key its model has no
field for and says nothing, so an option outside this set is one that some release the extra
allows would quietly not apply: the per-request `follow_redirects` is the case already met,
which 1.66.0 drops. The fake refuses such an option rather than pretend it took effect."""

_NO_OPTIONS: Mapping[str, Any] = MappingProxyType({})
"""What `options` is when a caller hands none: empty, as the SDK's own default is, and read-only,
so no call can leave anything in it for the next."""


REFUSED: dict[str, dict[str, Any]] = {
    "options=None": {"options": None},
    "options naming a field post fills itself": {"options": {"url": "/elsewhere"}},
    "a keyword only a resource method takes": {"extra_headers": {}},
    "the clock as a keyword of its own": {"timeout": 1.0},
}
"""Calls to the generic request, beside a path, a `cast_to` and a body, that each release read
refuses with a `TypeError` before anything is sent, by the keyword each adds: `post` takes no
keyword it does not name, and unpacks `options` beside the fields it fills itself.
`tests/test_decisions_api_wire.py` holds the real client to the same four wherever it is
installed, which has been 1.66.0, 3.1.0 and 3.22.1, and `tests/test_decisions_api.py` holds the
fake."""

REFUSED_BY_THE_FLOOR: dict[str, dict[str, Any]] = {
    "content, which 1.66.0 has not got": {"content": b"{}"},
    "an option 1.66.0 drops without a word": {"options": {"follow_redirects": False}},
}
"""Calls that 3.22.1 takes and that 1.66.0, the floor, would not carry out: a keyword it has not
got, which is a `TypeError` there, and an option it leaves out of the request without saying so,
which the fake refuses with one rather than pretend it took effect."""


def _request_options(**values: Any) -> dict[str, Any]:
    """The keywords the SDK's `post` hands `FinalRequestOptions.construct`: the method, the path,
    the body and the files it fills itself, with the caller's `options` unpacked beside them.
    Called the same way, it fails the same way, and for the same two reasons: an `options` that
    is not a mapping, None included, cannot be unpacked, and one that names a field `post` fills
    hands that field a second value. Both are a `TypeError` raised before anything is sent."""
    return values


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
    request back. `release` is which of `RELEASES` its headers are merged like, and None, the
    default, keeps exactly what it was handed.
    """

    def __init__(
        self,
        *,
        answers: Iterable[Mapping[str, Any]] = (),
        script: Iterable[Any] = (),
        raises: Exception | Callable[[Any], Exception] | None = None,
        usage: Mapping[str, Any] | None = USAGE,
        release: str | None = None,
    ) -> None:
        self.answers = [dict(a) for a in answers]
        self.script = list(script)
        self.raises = raises
        self.usage = usage
        self.release = release
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
        files: Any = None,
        options: Mapping[str, Any] = _NO_OPTIONS,
        stream: bool = False,
        stream_cls: Any = None,
    ) -> Any:
        """The SDK's generic request, refusing every call that a release the extra allows would
        refuse, and answering the rest.

        It used to take anything at all, the keywords it did not name in a catch-all, and to
        record `options or {}`. So a call that handed the SDK `options=None`, which the SDK
        unpacks and fails on, read here exactly as a call with no options, and so did a call
        with `extra_headers=` or `timeout=`, which a resource method takes and this request
        does not: CI runs this fake and never the SDK, so the suite passed calls that would
        have ended every real stepper turn on `gate: error`.

        The keywords are the ones `post` takes in every release read, which are 3.22.1's
        without `content`, a keyword 1.66.0 and 1.83.0 have not got, and nothing else, so any
        other is the `TypeError` it is in the SDK. `options` goes through what the SDK does
        with it (`_request_options`), and a key the floor does not list is refused besides
        (`OPTIONS`). A request to stream is refused as well: the SDK hands such a call a stream
        rather than the decoded body the backend reads, and a fake that answered it with a body
        would hide that."""
        if stream or stream_cls is not None:
            raise NotImplementedError(
                "the fake answers with a decoded body and never streams, and the SDK would hand "
                "a streamed request a stream rather than the body the backend reads"
            )
        _request_options(method="post", url=path, json_data=body, files=files, **options)
        unknown = sorted(set(options) - OPTIONS)
        if unknown:
            raise TypeError(
                f"post() options {unknown} are not among the RequestOptions of openai 1.66.0, the "
                "extra's floor, which leaves an option it does not know out of the request "
                "without a word"
            )
        return await self._send(path, cast_to, body, dict(options))

    async def _send(self, path: str, cast_to: Any, body: Any, options: dict[str, Any]) -> Any:
        """A request that passed every check the SDK makes before it sends, recorded and
        answered."""
        self.posts.append({"path": path, "cast_to": cast_to, "body": body, "options": options})
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

    @property
    def default_headers(self) -> dict[str, Any]:
        """What the real client's own `default_headers` holds once it is built: what it was
        handed, merged with the variable's lines the way `release` merges them."""
        if self.release is None:
            return self.headers
        return _custom(self.release, self.headers)


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
    module.__version__ = __version__  # type: ignore[attr-defined]
    module.AsyncOpenAI = fake  # type: ignore[attr-defined]
    module.Omit = Omit  # type: ignore[attr-defined]
    module.DefaultAsyncHttpxClient = DefaultAsyncHttpxClient  # type: ignore[attr-defined]
    module.APIStatusError = APIStatusError  # type: ignore[attr-defined]
    module.BadRequestError = BadRequestError  # type: ignore[attr-defined]
    module.RateLimitError = RateLimitError  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "openai", module)
    return fake
