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
translator wants anyway. That request, that pass-through, the `Omit` marker and the
`DefaultAsyncHttpxClient` the client is built on are all in 1.66.0, the floor, as well as in
3.22.1 (read 2026-10-09 and 2026-10-10). What a release does with the headers it is handed is
another matter, and is not the same across that range, so nothing below rests on any one
release's way of doing it (`request_headers` says how they differ).

Where the key goes is decided by `is_openai_host` and by nothing else. `OPENAI_API_KEY` is sent
to OpenAI's own API, at one of the hosts OpenAI document as serving this endpoint, over https,
and every other address is sent `NO_KEY`, the word every server you run is sent:
`--decision-url http://127.0.0.1:8765` is a server that speaks this format on your own machine,
and a company's key has no business there. The SDK reads more than the key out of the
environment, and every piece of it is answered here rather than left to its defaults:

- `OPENAI_BASE_URL`, which it follows whenever it is handed no address. quackd's own local
  presets read the same variable as the address of a model server on your own machine
  (`providers/local.py`, and the address order in `docs/guides/local-llms.md`), so it may well
  be set on the very machine that names this row, and the hosted key would follow it there. So
  an address is handed over on every build, the one the row or the flag says, with `/v1` on the
  end because the SDK appends paths to it.
- `OPENAI_CUSTOM_HEADERS`, whose lines it adds to every request in the releases that read it,
  and `OPENAI_ORG_ID` and `OPENAI_PROJECT_ID`, which it sends as `OpenAI-Organization` and
  `OpenAI-Project`. They go where the pilot's go, by the one rule `providers/openai_headers.py`
  keeps for every client quackd builds on this SDK (ADR-0010's amendment of 2026-10-12): the
  SDK's own variables follow the SDK's own address, and this row never takes its address from
  `OPENAI_BASE_URL`, so OpenAI's own API is the one place they go. There the client is built as
  the SDK builds one by itself, and all three go exactly as the SDK sends them, a gateway's
  `Authorization` line over the key included. At any other address, a server of your own, none
  of them goes: the client is handed `NO_KEY` as the one authorization and an answer for every
  name the variable carries (`openai_headers.request_headers`), and once it is built its headers
  are read back, and one that would still send what quackd did not write stops the build, naming
  the release. Everywhere, its address is read back too.

And one thing it does with no variable at all: it follows redirects. Followed, a redirect from
anything that answers for the address would carry the account headers, and on a 307 or a 308
the whole request with the robot's state in it, to wherever it pointed, plain http included.
No answer OpenAI document for `POST /v1/decisions` is a redirect, so the client is built to
follow none, and a redirect is a turn that failed.

Two more are a matter of time rather than of where things go. The SDK's own timeout is a limit
on each phase of a request rather than on the whole of it, so the clock that ends a stepper turn
is the stepper's, and the client's sits above it (`base.client_timeout`). And before its first
request the SDK asks the operating system which platform it is running on, which can take a
good part of a second; the client asks it while it is built instead (`warm_platform`), so no
turn ever waits for it.

This is the one client quackd may hand a camera frame (`frames.may_send_frames`), and only when
a run asks with `--decision-frames`. Built with `frames`, it is refused at any address that is
not OpenAI's, a server of your own included, by the same `is_openai_host` that decides the key;
handed a frame, it refuses before sending anything unless it was built so and is at one of
OpenAI's hosts. A frame goes beside the state as OpenAI's reference has an image go: `input` is
then one user message whose content is the state as `input_text` and the frame as `input_image`,
an inline data URL with its `detail`, rather than the state alone as a string (`to_input`).

Imported only when a run has named this row. Nothing at module scope touches the SDK, and the
address rules are the standard library alone, so `factory` asks them on a machine without it.
"""

from __future__ import annotations

import asyncio
import math
import os
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit, urlunsplit

from quackd.agent.decision.base import (
    NO_KEY,
    TIMEOUT_S,
    DecisionError,
    DecisionFrame,
    DecisionMissingKey,
    DecisionNotInstalled,
    UnreadableReply,
    client_timeout,
    state_text,
)
from quackd.agent.providers.openai_headers import (
    CUSTOM_HEADERS_ENV,
    SDKParts,
    ambient_headers,
    foreign_authorization,
    foreign_headers,
    is_openai_host,
    is_sdk_own_header,
    own_headers,
    request_headers,
    sdk_parts,
)
from quackd.agent.providers.sdk_warm import ANYIO_BACKEND as ANYIO_BACKEND
from quackd.agent.providers.sdk_warm import warm_client
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

MAX_RETRIES = 0
"""None. The SDK's default is two, and the first of them sleeps between three eighths and a
half of a second before it starts, or however long a `Retry-After` asks, up to two minutes: a
good part of `TIMEOUT_S` spent asleep at best. Within one second a 429 or a 5xx is worth more
as a turn the record calls an error than as a retry that cannot finish."""

MAX_IMAGES = 128
"""The most images one request may carry across all its messages, by OpenAI's reference for
`POST /v1/decisions` and openai-python's request types, read 2026-10-08. The stepper sends one at
most; a caller who hands `decide` more than this is refused before anything is sent, rather than
told so by a 400 with the frames already on the wire."""


def client_address(url: str) -> str:
    """The address the client is handed: `url`'s scheme and authority, exactly as
    `is_openai_host` read them, with `API_PREFIX` on the end. Built from those parts rather than
    from the string as typed, so the host the key was decided for is the host the client is
    given."""
    parts = urlsplit(url.strip())
    return urlunsplit((parts.scheme, parts.netloc, API_PREFIX, "", ""))


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


def _sdk_parts(spec: DecisionSpec) -> SDKParts:
    """What this backend takes from the SDK (`openai_headers.sdk_parts`), or the sentence that
    says why not.

    A missing SDK and one too old to have these are the same sentence, because the same install
    fixes both. The client has to have `post` as well, the generic request this backend sends,
    which is asked here, on the path of a run that would otherwise go ahead without the stepper,
    rather than met as an `AttributeError` on its first turn."""
    sdk = sdk_parts()
    if sdk is None or not callable(getattr(sdk.client, "post", None)):
        raise DecisionNotInstalled(spec.name, spec.extra or SDK)
    return sdk


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


def warm_platform(client: Any) -> None:
    """Ask now what OpenAI's SDK asks before its first request, while this client is built and
    the robot is not yet connected, so no stepper turn spends its second on it.

    The question is which platform it runs on, and on Windows that is a WMI query or `cmd /c
    ver` in a subprocess, which used to be paid inside the first stepper turn and against that
    turn's one second: on a loaded machine most of it, and now and then all of it, when the turn
    was cancelled before anything was sent and was recorded and billed as a request that had
    left. Reading a built client's headers back, as `DecisionsAPILLM` does at a server of your
    own, happens to ask it as well, through `default_headers`, but leaves the client without the
    answer, so its first request still stopped to ask again. `sdk_warm.warm_client` is where the
    asking lives, since the pilot's client is warmed the same way, and it never raises."""
    warm_client(client, SDK)


# ── the translation ─────────────────────────────────────────────────────────────────────


def _field(question: Any, name: str) -> Any:
    """One field of a question, whether it is a dict or somebody's object: the tolerance
    `stepper._read` applies to what goes out, applied here to the same thing."""
    if isinstance(question, Mapping):
        return question.get(name)
    return getattr(question, name, None)


def to_input(state: Mapping[str, str], frames: Sequence[DecisionFrame] = ()) -> Any:
    """The state as OpenAI's `input`: the same JSON object the stepper measures as
    `state_chars`, character for character, so that number is the size of what was sent.

    With its letters as letters (`base.state_text`). The `input` is text the API hands its model as
    it is and never decodes, so JSON's escapes went to `gpt-6-luna` as written, a goal in Slovenian
    as runs of `\\u017e` and every `last` field's dash as `\\u2014`, longer to bill and stranger
    to read than the letters the state holds.

    With no frame it is that string, as it has always been. With frames it is the other shape
    the reference takes, a list of user messages, here exactly one: `role` `user`, which the
    reference requires, and a content list of the same string as an `input_text` part and each
    frame after it as an `input_image` part, its bytes as an inline data URL and its `detail`
    beside them. `type: "message"` is optional there and not sent, as the guide's own example
    with an image sends none. The frame is never in the state: the text measured as
    `state_chars` is the text sent, with or without a picture beside it."""
    text = state_text(state)
    if not frames:
        return text
    content: list[dict[str, str]] = [{"type": "input_text", "text": text}]
    content += [
        {"type": "input_image", "image_url": frame.data_url(), "detail": frame.detail}
        for frame in frames
    ]
    return [{"role": "user", "content": content}]


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
    reason `stepper._finite` gives. Nor is a NaN or an infinity, which the SDK's JSON reader
    takes from a server as readily as a number and a record cannot hold: `json.dumps` writes it
    as a bare `NaN` that no strict JSON reader accepts."""
    spread: dict[str, float] = {}
    if not isinstance(entries, list | tuple):
        return spread
    for entry in entries:
        if not isinstance(entry, Mapping) or key not in entry:
            continue
        probability = entry.get("probability")
        if isinstance(probability, bool) or not isinstance(probability, int | float):
            continue
        if not math.isfinite(probability):
            continue
        spread[str(entry[key])] = float(probability)
    return spread


def _kept(item: Mapping[str, Any], *fields: str) -> dict[str, Any]:
    """The fields of an answer that are there. One that is missing stays missing rather than
    arriving as `None`, so the stepper reads it exactly as it reads a field a System One server
    left out."""
    return {field: item[field] for field in fields if field in item}


def _one_answer(item: Mapping[str, Any], kind: str) -> dict[str, Any] | None:
    """One answer in the shape the stepper reads, or None where it answers nothing: an answer of
    another type than the one its question went out as, or one without the field its type
    answers with.

    Each of those used to come back as something it was not. A `score` under `next_verb` and a
    `choice` with no `choice` in it both carried a confidence, and the stepper filled the
    missing choice in with `escalate`, so a malformed answer was recorded as a confident
    decision to hand the turn back. A `choice` renamed in the beta would have read that way on
    every turn, hidden from anybody counting gates or measuring agreement in shadow mode. A
    choice has to be a string because every value quackd offers is one: the API types its
    values, so a `true` or a `null` is no label quackd sent."""
    if item.get("type") != kind:
        return None
    if kind == "predicate":
        return {"noul": item["probability"]} if "probability" in item else None
    if kind == "choice":
        if not isinstance(item.get("choice"), str):
            return None
        return {
            **_kept(item, "choice", "confidence"),
            "probabilities": _spread(item.get("probabilities"), "value"),
        }
    if kind == "score":
        if "score" not in item:
            return None
        return {
            **_kept(item, "score", "confidence"),
            "probabilities": _spread(item.get("probabilities"), "label"),
        }
    return None


def to_result(data: Mapping[str, Any], asked: Mapping[str, str]) -> dict[str, Any]:
    """OpenAI's answers in the shape `stepper._answer` reads: a mapping keyed by question name.

    `asked` is each question's name and the type it went out as (`to_questions`). Answers are
    read strictly by name and never by position. The API returns answers in the order the
    questions were asked and its schema makes the name on each answer optional, and of those
    two only the name cannot attribute `need_human`'s probability to `done`. So an answer with
    no name, or a name quackd did not ask, is dropped.

    Every answer under a name is gathered before any of them is read, because the first one
    used to win and each case that rule settled was settled wrongly. A refusal among them
    declines the question, wherever it came: kept first, an answer ahead of it hid it. Two
    answers or more, none of them a refusal, answer nothing: OpenAI echo the unique names quackd
    sends, so a second answer is a server that does not, and keeping the first settled a
    contradiction, a `done` of 0.0 and then 0.99, in whichever direction came first, which could
    be the one that lets a verb through. One answer is kept only if it is the type its question
    went out as and carries the field that type answers with (`_one_answer`).

    - A `predicate` becomes `{"noul": probability}`.
    - A `choice` becomes `{"choice", "confidence", "probabilities"}`, the distribution turned
      from a list into a mapping. The vendor's `confidence` is carried as the confidence, field
      to field, as Jev's is: OpenAI do not define it, and their own examples show it is not the
      top probability, so it is neither replaced with that nor blended into a number nobody
      published.
    - A `score` becomes `{"score", "confidence", "probabilities"}`, keyed by level label.
    - A `refusal` answers nothing. Its name goes in `refused`, beside the answers rather than
      among them, so a question the vendor declined can be told from one that went missing.

    What every number says is the stepper's to judge, for this row as for every other
    (`stepper._probability`), so the numbers pass through as they came. A question with nothing
    kept for it is a question nobody answered. Where the stepper routes on it, `next_verb`,
    `done` or `need_human`, that hands the turn to the model on `unreadable`; `feasible` it only
    records, so a missing `feasible` is recorded as nothing and the turn goes on, as it does on
    every row. `usage` and `model` are passed through untouched: `stepper._usage` already reads
    `input_tokens` off a mapping, which is the field OpenAI bills by."""
    answers_in = data.get("answers")
    if not isinstance(answers_in, list):
        # quackd's own words, naming the type of what came where the list belongs and nothing
        # it said, so the record keeps them (`base.UnreadableReply`).
        raise UnreadableReply(
            f"POST {API_PREFIX}{PATH} answered without a list of answers "
            f"({type(answers_in).__name__})"
        )
    under: dict[str, list[Mapping[str, Any]]] = {name: [] for name in asked}
    for item in answers_in:
        name = item.get("name") if isinstance(item, Mapping) else None
        if isinstance(name, str) and name in under:
            under[name].append(item)
    answers: dict[str, Any] = {}
    refused: list[str] = []
    for name, given in under.items():
        if any(item.get("type") == "refusal" for item in given):
            refused.append(name)
        elif len(given) == 1 and (answer := _one_answer(given[0], asked[name])) is not None:
            answers[name] = answer
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
    here, once, so nothing a turn does can move either, and so is what the SDK would otherwise
    do on the first turn before sending anything (`warm_platform`).

    `frames` builds it to be shown the robot's camera beside the state, which only a run that
    says `--decision-frames` asks for. It is refused at an address that is not OpenAI's own, a
    server of your own included, so whatever a client built with it is shown goes to OpenAI and
    nowhere else.
    """

    def __init__(
        self,
        spec: DecisionSpec,
        *,
        model: str | None = None,
        url: str | None = None,
        frames: bool = False,
    ) -> None:
        self.name = spec.name
        self.model: str = model or spec.model or ""
        if not self.model:
            raise DecisionError(
                f"--decision-llm {spec.name} names no model: say which, as {spec.name}:MODEL"
            )
        # The whitespace around it taken off, as the CLI takes it off, and blank read as unset,
        # as `factory.resolve_decision_url` reads it: the address every rule below decides on
        # is then the one string the price and the key are both decided on.
        address = (url or "").strip() or (spec.url or "").strip()
        if not address:
            # The row always has one, and `factory` always resolves it. Refused rather than
            # guessed for somebody building this directly from a spec of their own.
            raise DecisionError(
                f"--decision-llm {spec.name} has no address to send to: give one with "
                "--decision-url https://host"
            )
        check_address(address)
        self.url: str | None = address
        at_openai = is_openai_host(address)
        if frames and not at_openai:
            # Before the SDK is asked for anything, so the refusal is the same sentence on a
            # machine without it. Rok's rule: a frame goes to OpenAI's own hosts and nowhere
            # else, a server of your own on this machine included.
            raise DecisionError(
                f"--decision-llm {spec.name} sends the camera to OpenAI's own API alone, and "
                f"{redacted_url(address)} is not one of its hosts"
            )
        self.frames: bool = bool(frames)
        """Whether it was built to be shown the camera, at OpenAI's own address."""
        sdk = _sdk_parts(spec)
        if at_openai:
            key = os.environ.get(spec.key_env) if spec.key_env else None
            if not key:
                raise DecisionMissingKey(spec.name, spec.key_env or "OPENAI_API_KEY")
        else:
            key = NO_KEY
        bearer = f"Bearer {key}"
        handed: dict[str, Any] | None = None
        built: dict[str, Any] = {}
        try:
            settings: dict[str, Any] = {
                "api_key": key,
                "base_url": client_address(address),
                # Above the stepper's own second rather than equal to it, so the stepper's
                # bound is the one clock that ends a turn (`base.client_timeout`).
                "timeout": client_timeout(TIMEOUT_S),
                "max_retries": MAX_RETRIES,
                # Told here rather than per request, because the request option that says it
                # is not in every release the extra allows (1.66.0 has none) and a release
                # without it drops it silently. Every release reads the HTTP client's own.
                "http_client": sdk.http_client(follow_redirects=False),
            }
            # The SDK's own variables follow the SDK's own address
            # (`openai_headers.sdks_own_address`), and this client is always handed one, so it
            # is never bound where the SDK found an address for itself. OpenAI's own API is then
            # the one address it is built as the SDK builds one by itself, sending the SDK's own
            # variables as the SDK sends them, and the one answer that decides that is the one
            # that decided the key.
            if not at_openai:
                own: dict[str, str] = {}
                named = ambient_headers(os.environ.get(CUSTOM_HEADERS_ENV))
                if any(is_sdk_own_header(name) for name in named):
                    # Read off a client built as the SDK builds one, which sends nothing and is
                    # dropped: the values are the installed release's own.
                    own = own_headers(sdk.client(**settings))
                handed = request_headers(bearer, at_openai=False, omit=sdk.omit, own=own)
                settings["default_headers"] = handed
            self._client: Any = sdk.client(**settings)
            if handed is not None:
                built = dict(self._client.default_headers)
            used = str(self._client.base_url)
        except Exception as e:
            # The type and not the message: the message may quote the address, and this one
            # can carry a password the redacted spelling beside it has already taken out.
            raise DecisionError(
                f"--decision-llm {spec.name} could not build its client for "
                f"{redacted_url(address)}: {type(e).__name__}"
            ) from e
        if is_openai_host(used) != at_openai:
            # A client that reads its address on the other side of OpenAI's own API from the
            # one it was handed would send what was decided for one to the other: the key and
            # OpenAI's account headers away from OpenAI, or the word to OpenAI.
            raise DecisionError(
                f"--decision-llm {spec.name} would send to {redacted_url(used)} rather than "
                f"{redacted_url(address)} with openai {sdk.version}, so it is not built"
            )
        if handed is not None and foreign_authorization(built, bearer, sdk.omit):
            # Read back rather than trusted, because `request_headers` answers the releases
            # somebody read, and the extra allows ones nobody has. The value is never quoted:
            # it is the credential this exists to keep at home.
            raise DecisionError(
                f"--decision-llm {spec.name} would send an authorization header from "
                f"{CUSTOM_HEADERS_ENV} beside its own with openai {sdk.version}, so it is not "
                f"built: take that line out of {CUSTOM_HEADERS_ENV} for this run, or upgrade the "
                'SDK (uv pip install -U "quackd[openai]")'
            )
        if handed is not None and foreign_headers(built, handed, sdk.omit):
            raise DecisionError(
                f"--decision-llm {spec.name} would send a line of {CUSTOM_HEADERS_ENV}, or "
                f"OPENAI_ORG_ID or OPENAI_PROJECT_ID, to {redacted_url(address)} with openai "
                f"{sdk.version}, which none of them is for, so it is not built: take them out of "
                "the environment for this run, or upgrade the SDK "
                '(uv pip install -U "quackd[openai]")'
            )
        warm_platform(self._client)

    async def decide(
        self,
        state: Mapping[str, str],
        questions: Mapping[str, Mapping[str, Any]],
        *,
        frames: Sequence[DecisionFrame] = (),
        timeout_s: float | None = None,
    ) -> Any:
        """One request carrying every question, translated out and the answers translated back.

        `frames` go beside the state (`to_input`), and only from a client built to be shown them
        at one of OpenAI's own hosts: handed any otherwise, or more than the `MAX_IMAGES` one
        request may carry, it raises a `ValueError` before anything is sent. The stepper never
        hands it one it may not send (`frames.may_send_frames`); this is the same rule kept by the
        last thing that could break it, for whoever calls `decide` directly.

        `timeout_s`, where it is given, bounds the whole request, connecting, sending and every
        byte of the answer, with a `wait_for` of its own, and a request that runs out raises
        `TimeoutError`. The SDK's timeout could not be that bound: httpx2 applies it to each phase
        of a request on its own, the connect, each read, each write, the wait for a pooled
        connection, so a server that sends its answer a piece at a time, each piece inside the
        limit, or a slow connect followed by a slow first byte, holds a request open well past it.
        So the SDK's clock for that one request is set above the bound (`base.client_timeout`),
        where it can only fire after the bound has ended the request.

        Without `timeout_s` nothing in here bounds the whole request, and the client's own clock,
        per phase and above `TIMEOUT_S`, is a backstop. That is the stepper's case: its
        `asyncio.timeout` around this call, at `TIMEOUT_S`, is the bound, and the one clock that
        ends its turn."""
        if frames and not (self.frames and is_openai_host(self.url or "")):
            # A `ValueError` rather than a `DecisionError`, which is a sentence while the CLI is
            # still reading its flags: this is a caller's mistake met mid-run, and the stepper
            # records a turn that raises it by its type and hands the turn to the model.
            raise ValueError(
                f"--decision-llm {self.name} was not built to be shown the camera at OpenAI's own "
                "address, so it is handed no frame and nothing was sent"
            )
        if len(frames) > MAX_IMAGES:
            raise ValueError(
                f"{len(frames)} frames, and one request to {API_PREFIX}{PATH} carries at most "
                f"{MAX_IMAGES}, so nothing was sent"
            )
        asked = to_questions(questions)
        body = {"model": self.model, "input": to_input(state, frames), "questions": asked}
        if timeout_s is None:
            data = await self._post(body, None)
        else:
            data = await asyncio.wait_for(self._post(body, client_timeout(timeout_s)), timeout_s)
        if not isinstance(data, Mapping):
            # The SDK hands back the body as text when it is not JSON. Not quoted: a page of
            # somebody's HTML is not a reason a reader of the record needs to wade through, and
            # it could quote the request. Named by its type, in quackd's own words, which the
            # record keeps (`base.UnreadableReply`).
            raise UnreadableReply(
                f"POST {API_PREFIX}{PATH} answered {type(data).__name__} rather than a JSON object"
            )
        return to_result(data, {question["name"]: question["type"] for question in asked})

    async def _post(self, body: Mapping[str, Any], timeout_s: float | None) -> Any:
        """The one place a request leaves, so a test can stand where the network would be.

        `timeout_s` is the SDK's own clock for this one request, per phase as the SDK keeps it,
        or None for the one the client was built with. `decide` hands it the clock above its
        bound, never the bound itself."""
        options: dict[str, Any] = {} if timeout_s is None else {"timeout": timeout_s}
        return await self._client.post(PATH, cast_to=object, body=body, options=options)
