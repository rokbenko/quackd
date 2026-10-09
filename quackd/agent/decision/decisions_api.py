"""OpenAI's Decisions API, behind `--decision-llm openai`: the second wire format quackd speaks.

Every other decision LLM quackd names that sits behind a server speaks System One, which is why
they share `systemone.py` and differ only in their rows. OpenAI's speaks its own.
`POST /v1/decisions` asks the same kind of question -- typed questions about a piece of text,
answered with probabilities rather than prose -- in another shape: the questions go as a list
rather than a mapping, a yes/no question is a `predicate` rather than a `noul`, a choice's
options are a list of values rather than a mapping of label to description, and the answers come
back as a list in which each one names the question it answers. So this is a second transport
rather than a second address, and this module is a translation both ways around one HTTP call.
The stepper above it asks the same four questions it asks everybody and reads the answers the
way it reads everybody's.

The call is the SDK's own generic request, `client.post("/decisions", cast_to=object, ...)`,
rather than its `decisions` resource. That resource arrived in openai 3.26.0 and the lock
carries 3.22.1, so reaching it would mean raising the floor in every extra that installs the
SDK, and making everybody who pilots with an OpenAI-shaped vendor upgrade for a stepper they may
never name. `cast_to=object` hands back the decoded JSON body untouched, which is what a
translator wants anyway. That request, that pass-through and the `Omit` marker the headers below
rely on are all in 1.66.0, the floor, as well as in 3.22.1 (both read 2026-10-09).

Where the key goes is decided by `is_openai_host` and by nothing else. `OPENAI_API_KEY` is sent
to OpenAI's own API at one of the four hosts the SDK itself names, over https, and every other
address is sent `NO_KEY`, the word every server you run is sent: `--decision-url
http://127.0.0.1:8765` is a server that speaks this format on your own machine, and a company's
key has no business there. The SDK reads more than the key out of the environment, and every
piece of it is answered here rather than left to its defaults:

- `OPENAI_BASE_URL`, which it follows whenever it is handed no address, and which `.env.example`
  tells a reader to set for a model on their own machine. So an address is handed over on every
  build, the one the row or the flag says, with `/v1` on the end because the SDK appends paths
  to it.
- `OPENAI_CUSTOM_HEADERS`, whose lines it adds to every request it makes. That is where a
  gateway's own credential lives for somebody whose pilot goes through one, and it is that
  gateway's, not OpenAI's and not your server's. So every header it names is omitted here, read
  the way the SDK reads it, except `Authorization`, which is named outright instead, because
  that alone makes the SDK drop that variable's own `Authorization` line for this one.
- `OPENAI_ORG_ID` and `OPENAI_PROJECT_ID`, which it sends as headers. They are identifiers
  rather than secrets, and they are OpenAI's, so they go to OpenAI and are omitted everywhere
  else.

Imported only when a run has named this row. Nothing at module scope touches the SDK, and the
address rules are the standard library alone, so `factory` asks them on a machine without it.
"""

from __future__ import annotations

import importlib
import json
import os
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit, urlunsplit

from quackd.agent.decision.base import (
    NO_KEY,
    TIMEOUT_S,
    DecisionError,
    DecisionMissingKey,
    DecisionNotInstalled,
)
from quackd.command import readable_url, redacted_url

if TYPE_CHECKING:
    from quackd.agent.decision.catalogue import DecisionSpec

SDK = "openai"
"""The client's import name, which is the pilot's own SDK and the row's `sdk`."""

API_PREFIX = "/v1"
"""What the SDK expects on the end of its address. It appends a request's path to whatever it
was given after making sure that ends in a slash, so `https://api.openai.com/v1` and `PATH` reach
`/v1/decisions`, and an address typed with `/v1` already on it would reach `/v1/v1/decisions`,
which is why `check_address` refuses one."""

PATH = "/decisions"
"""The request, relative to `API_PREFIX`: the path the SDK's own `decisions` resource posts to
in the releases that have one."""

OPENAI_HOSTS = frozenset(
    {"api.openai.com", "us.api.openai.com", "eu.api.openai.com", "ae.api.openai.com"}
)
"""Where `OPENAI_API_KEY` may go: OpenAI's global API and its three regional ones, exactly the
four the SDK itself names for its `data_residency` choice (`openai/_data_residency.py`, read
2026-10-09). A host is compared after `urlsplit` has lowercased it, and as a whole, so
`api.openai.com.evil.example` is not one and neither is `api.openai.com.` with its trailing
dot."""

MAX_RETRIES = 0
"""None. The SDK's default is two, and the first of them sleeps between three eighths and a
half of a second before it starts, or however long a `Retry-After` asks, up to two minutes: a
good part of `TIMEOUT_S` spent asleep at best. Within one second a 429 or a 5xx is worth more
as a turn the record calls an error than as a retry that cannot finish."""

CUSTOM_HEADERS_ENV = "OPENAI_CUSTOM_HEADERS"
"""The SDK's own variable for headers to add to every request, one `Name: value` per line."""

ACCOUNT_HEADERS = ("OpenAI-Organization", "OpenAI-Project")
"""The headers the SDK fills from `OPENAI_ORG_ID` and `OPENAI_PROJECT_ID`, spelled as it spells
them, so that omitting one here replaces that very entry."""


def is_openai_host(url: str) -> bool:
    """Whether `url` is OpenAI's own API, the only place `OPENAI_API_KEY` is ever sent.

    All four conditions, and each is a case that would otherwise send the key somewhere else.
    `https`, because the key is a bearer token and plain HTTP shows it to the path between. One
    of `OPENAI_HOSTS`, whole. Port 443 or none, because `api.openai.com:8443` is no address
    OpenAI publish and may be somebody's tunnel. And no userinfo at all, which is less
    obvious and load-bearing: `urlsplit` and the HTTP client do not have to agree on where a
    host starts, and `https://evil.example\\@api.openai.com` is read here as `api.openai.com`
    with a user called `evil.example\\`, while a client that follows the WHATWG URL standard
    reads a backslash as the end of the host and goes to `evil.example`. A URL with an `@` in
    its authority is never OpenAI's, whatever is after the `@`, so the two readings never have
    to agree."""
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        return False
    return (
        parts.scheme == "https"
        and parts.username is None
        and parts.password is None
        and port in (None, 443)
        and (parts.hostname or "") in OPENAI_HOSTS
    )


def check_address(url: str) -> None:
    """Refuse an address this backend cannot be handed, before anything is built from it.

    The scheme, the host and a port, and nothing more, because `API_PREFIX` and `PATH` are
    quackd's to add. A path is refused rather than kept or quietly cut: the likeliest one is
    `/v1`, copied from somewhere that writes OpenAI's address with it, and kept it would ask for
    `/v1/v1/decisions` and be told 404 on every turn of the run. A query or a fragment has
    nowhere to go either. `/` alone is the address with a slash on the end and is fine.

    `factory.resolve_decision_address` calls this while the CLI is still reading its flags, so a
    typo stops the run on every machine, with or without the SDK and the key; the client calls
    it again for whoever builds one directly. An address `redacted_url` cannot read is refused
    without being quoted, because it may carry a password redaction could not find."""
    if not readable_url(url):
        raise DecisionError(
            "--decision-url is not an address: give it as https://host or http://host:port, "
            "and quackd adds /v1/decisions itself"
        )
    parts = urlsplit(url)
    shown = redacted_url(url)
    if parts.scheme not in ("http", "https"):
        raise DecisionError(
            f"--decision-url {shown} is not an http:// or https:// address: give it as "
            "https://host or http://host:port, and quackd adds /v1/decisions itself"
        )
    try:
        _ = parts.port  # read for the ValueError a port that is not a number raises
    except ValueError:
        raise DecisionError(
            f"--decision-url {shown} has a port that is not a number: give it as "
            "https://host or http://host:port"
        ) from None
    if not parts.hostname:
        raise DecisionError(
            f"--decision-url {shown} has no host: give it as https://host or http://host:port"
        )
    extra = [
        what
        for what, there in (
            ("a path", parts.path not in ("", "/")),
            ("a query", "?" in url),
            ("a fragment", "#" in url),
        )
        if there
    ]
    if extra:
        alone = urlunsplit((parts.scheme, urlsplit(shown).netloc, "", "", ""))
        raise DecisionError(
            f"--decision-url {shown} has {' and '.join(extra)}: give the address alone, {alone}, "
            f"because quackd adds {API_PREFIX}{PATH} itself"
        )


def _sdk_parts(spec: DecisionSpec) -> tuple[Any, Any]:
    """The SDK's client class and its `Omit` marker, or the sentence that says why not.

    A missing SDK and one too old to have these are the same sentence, because the same install
    fixes both: before 1.0 the package had no `AsyncOpenAI` at all, and the extra's floor is
    well past that. Every exception counts as missing and not only `ImportError`, because a
    half-installed package is free to raise anything while it is imported, and this is asked on
    the path of a run that would otherwise go ahead without the stepper."""
    extra = spec.extra or SDK
    try:
        sdk = importlib.import_module(SDK)
    except Exception as e:
        raise DecisionNotInstalled(spec.name, extra) from e
    client = getattr(sdk, "AsyncOpenAI", None)
    omit = getattr(sdk, "Omit", None)
    if client is None or omit is None or not callable(getattr(client, "post", None)):
        raise DecisionNotInstalled(spec.name, extra)
    return client, omit


def client_problem(spec: DecisionSpec) -> str | None:
    """Why this row's client cannot be built here, in the sentence a run prints, or None.

    `factory.decision_llm_is_available` asks this before the robot connects, so an SDK that
    imports and still lacks the client is the soft "running without it" warning rather than an
    `AttributeError` out of the CLI, which catches only a `DecisionError`."""
    try:
        _sdk_parts(spec)
    except DecisionError as e:
        return str(e)
    return None


def ambient_headers(raw: str | None) -> list[str]:
    """The header names `OPENAI_CUSTOM_HEADERS` adds to every request, read as the SDK reads it.

    Split on newlines, the name is everything before the first colon with the whitespace around
    it taken off and its case kept, and a line with no colon is not a header (`openai/_client.py`,
    3.22.1, read 2026-10-09). The same reading, so that each name omitted here is the very
    entry the SDK would have added; reading it more cleverly would leave a header the SDK reads
    and this does not."""
    if raw is None:
        return []
    names: list[str] = []
    for line in raw.split("\n"):
        colon = line.find(":")
        if colon >= 0:
            names.append(line[:colon].strip())
    return names


# ── the translation ─────────────────────────────────────────────────────────────────────


def _field(question: Any, name: str) -> Any:
    """One field of a question, whether it is a dict or somebody's object: the tolerance
    `stepper._read` applies to what goes out, applied here to the same thing."""
    if isinstance(question, Mapping):
        return question.get(name)
    return getattr(question, name, None)


def to_input(state: Mapping[str, str]) -> str:
    """The state as OpenAI's `input`: the same JSON object the stepper measures as
    `state_chars`, character for character, so that number is the size of what was sent."""
    return json.dumps(dict(state))


def _options(criteria: Any, key: str, name: str) -> list[dict[str, str]]:
    """A question's criteria as OpenAI's list of options, in the order they were written.

    A mapping is label to description, which is what `build_questions` writes; a list is labels
    alone. Every label goes as a string: values are typed on this API, so the string `"true"`
    and the boolean `true` are different answers, and every label quackd offers is a string."""
    if isinstance(criteria, Mapping):
        return [{key: str(label), "description": str(text)} for label, text in criteria.items()]
    if isinstance(criteria, list | tuple):
        return [{key: str(label)} for label in criteria]
    raise ValueError(f"the question {name!r} has no criteria to offer")


def to_questions(questions: Mapping[str, Any]) -> list[dict[str, Any]]:
    """quackd's questions in OpenAI's shape, in the order they were asked, each one named.

    Named every time although the schema makes a name optional, because the name is the only
    thing an answer is matched back by (`to_result`).

    - A `choice` keeps its criteria as `choices`, in their order, so `escalate` is last because
      `build_questions` put it last.
    - A `noul` is a `predicate`, the same question by another name: the probability that a
      statement is true.
    - A `score` keeps its criteria as `levels`, in the order they were written.

    Anything else raises a `ValueError` naming it, which `Stepper.advise` records as the turn's
    error, and nothing is sent."""
    out: list[dict[str, Any]] = []
    for name, question in questions.items():
        kind = _field(question, "type")
        asked: dict[str, Any] = {
            "name": str(name),
            "instructions": str(_field(question, "instructions") or ""),
        }
        if kind == "choice":
            asked = {"type": "choice", **asked}
            asked["choices"] = _options(_field(question, "criteria"), "value", str(name))
        elif kind == "noul":
            asked = {"type": "predicate", **asked}
        elif kind == "score":
            asked = {"type": "score", **asked}
            asked["levels"] = _options(_field(question, "criteria"), "label", str(name))
        else:
            raise ValueError(f"POST {API_PREFIX}{PATH} has no question of type {kind!r} ({name!r})")
        out.append(asked)
    return out


def _spread(entries: Any, key: str) -> dict[str, float]:
    """A distribution as a mapping, which is how the stepper reads one.

    OpenAI's is a list of `{value, probability}`, or `{label, probability}` on a score, and the
    stepper calls `.items()` on what it is given, so a list there would cost every turn as an
    error. Numbers only: an entry whose probability is not one is dropped rather than carried
    into a record that is read as a distribution, and a boolean is not a number here, for the
    reason `stepper._finite` gives."""
    spread: dict[str, float] = {}
    if not isinstance(entries, list | tuple):
        return spread
    for entry in entries:
        if not isinstance(entry, Mapping) or key not in entry:
            continue
        probability = entry.get("probability")
        if isinstance(probability, bool) or not isinstance(probability, int | float):
            continue
        spread[str(entry[key])] = float(probability)
    return spread


def _kept(item: Mapping[str, Any], *fields: str) -> dict[str, Any]:
    """The fields of an answer that are there. One that is missing stays missing rather than
    arriving as `None`, so the stepper reads it exactly as it reads a field a System One server
    left out."""
    return {field: item[field] for field in fields if field in item}


def to_result(data: Mapping[str, Any], asked: Sequence[str]) -> dict[str, Any]:
    """OpenAI's answers in the shape `stepper._answer` reads: a mapping keyed by question name.

    Read strictly by name and never by position. The API returns answers in the order the
    questions were asked and its schema makes the name on each answer optional, and of those
    two only the name cannot attribute `need_human`'s probability to `done`. So an answer is
    kept only under a name quackd asked, the first answer under a name is the one kept, and an
    answer with no name or another one is dropped. A question with nothing kept for it is a
    question nobody answered, which the stepper already hands to the model rather than reading
    as no.

    - A `predicate` becomes `{"noul": probability}`.
    - A `choice` becomes `{"choice", "confidence", "probabilities"}`, the distribution turned
      from a list into a mapping. The vendor's `confidence` is carried as the confidence, field
      to field, as Jev's is: OpenAI do not define it, and their own examples show it is not the
      top probability, so it is neither replaced with that nor blended into a number nobody
      published.
    - A `score` becomes `{"score", "confidence", "probabilities"}`, keyed by level label.
    - A `refusal` answers nothing. Its name goes in `refused`, beside the answers rather than
      among them, so a question the vendor declined can be told from one that went missing.

    An answer of any other type is left unanswered. `usage` and `model` are passed through
    untouched: `stepper._usage` already reads `input_tokens` off a mapping, which is the field
    OpenAI bills by."""
    answers_in = data.get("answers")
    if not isinstance(answers_in, list):
        raise ValueError(
            f"POST {API_PREFIX}{PATH} answered without a list of answers "
            f"({type(answers_in).__name__})"
        )
    wanted = set(asked)
    seen: set[str] = set()
    answers: dict[str, Any] = {}
    refused: list[str] = []
    for item in answers_in:
        if not isinstance(item, Mapping):
            continue
        name = item.get("name")
        if not isinstance(name, str) or name not in wanted or name in seen:
            continue
        seen.add(name)
        kind = item.get("type")
        if kind == "refusal":
            refused.append(name)
        elif kind == "predicate":
            if "probability" in item:
                answers[name] = {"noul": item["probability"]}
        elif kind == "choice":
            answers[name] = {
                **_kept(item, "choice", "confidence"),
                "probabilities": _spread(item.get("probabilities"), "value"),
            }
        elif kind == "score":
            answers[name] = {
                **_kept(item, "score", "confidence"),
                "probabilities": _spread(item.get("probabilities"), "label"),
            }
    return {
        "answers": answers,
        "refused": refused,
        "model": data.get("model"),
        "usage": data.get("usage"),
    }


# ── the client ──────────────────────────────────────────────────────────────────────────


class DecisionsAPILLM:
    """OpenAI's Decisions API, or a server of your own that answers it, reached where the run
    says.

    Built while the CLI is still parsing rather than on the first turn, as `SystemOneLLM` is:
    constructing a client can fail, and that is a sentence before the robot connects rather than
    a phantom cost on a turn that never left the machine. The address and the key are settled
    here, once, so nothing a turn does can move either.
    """

    def __init__(
        self, spec: DecisionSpec, *, model: str | None = None, url: str | None = None
    ) -> None:
        self.name = spec.name
        self.model: str = model or spec.model or ""
        if not self.model:
            raise DecisionError(
                f"--decision-llm {spec.name} names no model: say which, as {spec.name}:MODEL"
            )
        address = url or spec.url
        if address is None:
            # The row always has one, and `factory` always resolves it. Refused rather than
            # guessed for somebody building this directly from a spec of their own.
            raise DecisionError(
                f"--decision-llm {spec.name} has no address to send to: give one with "
                "--decision-url https://host"
            )
        check_address(address)
        self.url: str | None = address
        client, omit = _sdk_parts(spec)
        at_openai = is_openai_host(address)
        if at_openai:
            key = os.environ.get(spec.key_env) if spec.key_env else None
            if not key:
                raise DecisionMissingKey(spec.name, spec.key_env or "OPENAI_API_KEY")
        else:
            key = NO_KEY
        # Named outright, and the reason is the SDK's rule rather than tidiness: it drops the
        # `Authorization` line of `OPENAI_CUSTOM_HEADERS` only when the caller names one, and
        # left in, that line would replace the word on its way to a server you run.
        headers: dict[str, Any] = {"Authorization": f"Bearer {key}"}
        for name in ambient_headers(os.environ.get(CUSTOM_HEADERS_ENV)):
            # Not an omitted `Authorization`, in any case: the SDK merges headers without
            # regard to case, so that would take out the line above, and the SDK has already
            # dropped the variable's own.
            if name.lower() != "authorization":
                headers[name] = omit()
        if not at_openai:
            for name in ACCOUNT_HEADERS:
                headers[name] = omit()
        parts = urlsplit(address)
        try:
            self._client: Any = client(
                api_key=key,
                # Built from the parts `is_openai_host` read rather than from the string as
                # typed, so the host the key was decided for is the host the client is given.
                base_url=urlunsplit((parts.scheme, parts.netloc, API_PREFIX, "", "")),
                timeout=TIMEOUT_S,
                max_retries=MAX_RETRIES,
                default_headers=headers,
            )
        except Exception as e:
            # The type and not the message: the message may quote the address, and this one
            # can carry a password the redacted spelling beside it has already taken out.
            raise DecisionError(
                f"--decision-llm {spec.name} could not build its client for "
                f"{redacted_url(address)}: {type(e).__name__}"
            ) from e

    async def decide(
        self,
        state: Mapping[str, str],
        questions: Mapping[str, Mapping[str, Any]],
        *,
        timeout_s: float | None = None,
    ) -> Any:
        """One request carrying every question, translated out and the answers translated back.

        `timeout_s` bounds this one request where it is given and the client's `TIMEOUT_S`
        bounds it where it is not, which is the stepper's case: its own `wait_for` around this
        call is the bound that holds whatever the client says."""
        asked = to_questions(questions)
        body = {"model": self.model, "input": to_input(state), "questions": asked}
        data = await self._post(body, timeout_s)
        if not isinstance(data, Mapping):
            # The SDK hands back the body as text when it is not JSON. Not quoted: a page of
            # somebody's HTML is not a reason a reader of the record needs to wade through.
            raise ValueError(
                f"POST {API_PREFIX}{PATH} answered {type(data).__name__} rather than a JSON object"
            )
        return to_result(data, [question["name"] for question in asked])

    async def _post(self, body: Mapping[str, Any], timeout_s: float | None) -> Any:
        """The one place a request leaves, so a test can stand where the network would be."""
        options: dict[str, Any] = {} if timeout_s is None else {"timeout": timeout_s}
        return await self._client.post(PATH, cast_to=object, body=body, options=options)
