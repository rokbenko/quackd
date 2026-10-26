"""The provider protocol: what quackd needs from an LLM and nothing more.

quackd keeps its own vendor-neutral history (`Exchange` = an observation and the decision
it produced). Each provider renders that into its wire format and returns one
`ProviderTurn`. Tools are described once, as JSON Schema, in the Anthropic shape
(`name`, `description`, `input_schema`); other providers translate.
"""

from __future__ import annotations

import contextlib
import email.utils
import math
import re
import time
from collections.abc import Callable, Sequence
from typing import Any, Literal, NamedTuple, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

CAMERA_LABEL = "camera {name}:"
"""How a picture is introduced when a body has several. Short on purpose: it sits in front
of every frame of every step, and the observation text already says which is the primary."""

TASK_PICTURE_LABEL = "task picture {name}:"
"""How a picture that came with the task is introduced. Always said, even for a single one,
because it shares a message with the camera and a pilot has to know which is which: one is
what it was asked about and the other is what the robot can see right now."""


class ToolCall(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = ""
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    signature: str = ""
    """Opaque and provider-owned, base64 text. Gemini 3 signs every function call it makes
    and refuses the next turn unless the signature is handed back on that same call; other
    providers leave it empty and nothing reads it."""


class Usage(BaseModel):
    """What one call spent, in the buckets a bill is itemised by.

    Every vendor reports these differently and quackd normalises them here, because a number
    that means one thing on Anthropic and another on Gemini cannot be added up, compared
    across a run, or multiplied by a rate. The convention, which every adapter converts into
    and `providers.pricing` prices:

    - `input_tokens` is the WHOLE prompt, cached or not. The two cache fields are parts of
      it, not additions to it, so `input_tokens` stays the same number a reader has always
      seen whether or not a cache was in play that turn.
    - `output_tokens` is everything generated, thinking included, because that is what every
      vendor charges the output rate for.
    - `reasoning_tokens` is the slice of the output the vendor counts apart. A subset, never
      an addition: pricing it again would bill thinking twice.
    """

    input_tokens: int = 0
    """Every prompt token the request consumed, `cache_read_tokens` and `cache_write_tokens`
    among them. Anthropic reports its three disjoint and the adapter adds them up; OpenAI and
    Gemini report a total that already contains the cached part, and it is passed through."""
    output_tokens: int = 0
    """Everything generated, thinking included."""
    reasoning_tokens: int = 0
    """Tokens spent thinking, where the vendor counts them apart from the answer.

    A slice of `output_tokens` rather than an addition to it, so it is recorded and never
    priced a second time. OpenAI reports it under `completion_tokens_details`, Anthropic as a
    read-only decomposition of the output it bills, and Gemini as `thoughts_token_count`
    beside the answer, which is the one that has to be added in to get the billed total."""
    cache_read_tokens: int = 0
    """The part of `input_tokens` served from a prompt cache, billed at the cache read rate,
    which is a tenth of the full one on most vendors. 0 on a vendor with no cache and on every
    run recorded before there was a field for it."""
    cache_write_tokens: int = 0
    """The part of `input_tokens` written INTO a prompt cache, billed at the write rate where
    the vendor has one. Anthropic charges for it when a request marks a cache, which quackd's
    does on every call (`AnthropicProvider._system`), and OpenAI on GPT-5.6 and later and Kimi on
    K3 charge for the cache they write by themselves. Elsewhere a cache costs nothing to create
    and this stays 0."""

    def __add__(self, other: Usage) -> Usage:
        return Usage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            reasoning_tokens=self.reasoning_tokens + other.reasoning_tokens,
            cache_read_tokens=self.cache_read_tokens + other.cache_read_tokens,
            cache_write_tokens=self.cache_write_tokens + other.cache_write_tokens,
        )


class NamedPng(BaseModel):
    """One camera's picture, and which camera took it.

    The name is only ever spoken to a model when a body has more than one camera: a pilot
    told its single view is called `front` would start naming it in sentences nobody needs."""

    model_config = ConfigDict(extra="forbid")

    name: str
    png: bytes


class Observation(BaseModel):
    """What the LLM sees this turn: text, a picture per camera, optionally structured features
    (used by the fake provider and by tests, never rendered to a real model)."""

    model_config = ConfigDict(extra="forbid")

    text: str
    images: list[NamedPng] = Field(default_factory=list)
    """One per camera that gave a frame this step, the primary first. Empty when the body has
    no camera, when the camera gave nothing, or when the model cannot see."""
    cameras: list[str] = Field(default_factory=list)
    """Every camera the body has, answering or not, which is not the same list as `images`.

    Whether a picture is named on the wire is decided from this rather than from how many
    arrived: a two-camera arm whose top lens stalls sends one frame, and sending it bare
    would put the side view under the primary's detections with nothing saying so. Empty for
    a body with one camera or none, which is every request quackd made before an arm could
    have two, so those go out unchanged."""
    attachments: list[NamedPng] = Field(default_factory=list)
    """Pictures that came with the task rather than with this step (`quackd run --image`).

    The loop puts them on the first observation and nowhere else, and the trim that drops old
    camera frames never touches them, so they stay at the top of every request for the whole
    run. A task that says "draw this" has to still mean something on turn twenty, and the
    verdict gate on turn one has to be able to see what it is being asked about."""
    features: dict[str, Any] = Field(default_factory=dict)
    tool_call_id: str | None = Field(
        default=None, description="Set when this is the result of a tool call."
    )


def name_cameras(obs: Observation) -> bool:
    """Whether this body's pictures are named on the wire. One camera needs no label."""
    return len(obs.cameras) > 1


def labelled(
    images: Sequence[NamedPng],
    image_part: Callable[[bytes], Any],
    text_part: Callable[[str], Any],
    *,
    name_them: bool = False,
) -> list[Any]:
    """A turn's pictures as wire parts, in order, each named when the body has several cameras.

    With a one-camera body this is the single part every provider sent back when a body could
    only have one camera, so a one-camera request goes out unchanged. With several, each
    picture is preceded by a text part naming its camera: two unlabelled images in one message
    are two views of a room with nothing to say which is which.

    `name_them` comes from the body's camera list and not from `len(images)`, because the
    dangerous case is the one where they disagree. A two-camera arm whose primary stalls sends
    exactly one picture, and that picture is the one that most needs saying which lens it is:
    it lands under a detections line measured off the lens that died."""
    if len(images) == 1 and not name_them:
        return [image_part(images[0].png)]
    parts: list[Any] = []
    for image in images:
        parts.append(text_part(CAMERA_LABEL.format(name=image.name)))
        parts.append(image_part(image.png))
    return parts


def attached(
    images: Sequence[NamedPng],
    image_part: Callable[[bytes], Any],
    text_part: Callable[[str], Any],
) -> list[Any]:
    """The task's own pictures as wire parts, each named, in the order they were given.

    Unlike a camera frame, one of these is never sent bare. It is the file the person named on
    the command line, the task refers to it by what it shows, and it arrives in the same
    message as a photograph of a table. Two unlabelled pictures there are a picture of the
    thing and a picture of the room with nothing to say which was which."""
    parts: list[Any] = []
    for image in images:
        parts.append(text_part(TASK_PICTURE_LABEL.format(name=image.name)))
        parts.append(image_part(image.png))
    return parts


def picture_parts(
    obs: Observation,
    image_part: Callable[[bytes], Any],
    text_part: Callable[[str], Any],
) -> list[Any]:
    """Every picture in one observation, task pictures first, then this step's camera frames.

    The task's come first because they are the fixed thing the request is about, and because a
    label reading `task picture sketch.png:` in front of a camera frame would name a lens that
    does not exist. When there are any, the frames are named too even on a one-camera body:
    the alternative is a bare picture sitting under someone else's caption."""
    return [
        *attached(obs.attachments, image_part, text_part),
        *labelled(
            obs.images,
            image_part,
            text_part,
            name_them=name_cameras(obs) or bool(obs.attachments),
        ),
    ]


class Decision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool_call: ToolCall
    text: str | None = None
    raw: Any = Field(
        default=None,
        description="Provider-specific replay payload (Anthropic content blocks incl. thinking).",
    )


class Exchange(BaseModel):
    observation: Observation
    decision: Decision | None = None
    cache_points: tuple[Literal["observation", "decision"], ...] = ()
    """Which halves of this exchange end a cached part of the request, for a provider that marks
    them for a prompt cache (`AgentLoop._with_cache_point`): where the next call will still match
    this one, and where the last call wrote its entry. Set only on the copy a request is built
    from, never on the run's own history, and read by no provider but Anthropic's."""


class ProviderTurn(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    tool_calls: list[ToolCall] = Field(default_factory=list)
    text: str | None = None
    usage: Usage = Field(default_factory=Usage)
    stop_reason: str | None = None
    refused: bool = Field(
        default=False,
        description="The model said no through the API's own refusal channel: a refusal part, "
        "with words or without, a refusal field that has words in it, or Anthropic's `refusal` "
        "stop. Its words, where it gave any, are in the turn's text as `[refusal] ...`, an "
        "Anthropic stop that gave none as `[refusal] the model refused`, and a call written out "
        "in them is never read as one, whatever `stop_reason` says.",
    )
    raw: Any = None
    thinking: str | None = Field(
        default=None,
        description="What the model reasoned before answering, as the vendor shows it: a "
        "summary on Claude, the reasoning field of an OpenAI-compatible server, Gemini's "
        "thought parts. None when the provider returned nothing of the kind.",
    )
    served_by: str | None = Field(
        default=None,
        description="The model that took the turn, where that is not the one asked for: a "
        "server-side refusal fallback on Claude re-runs a declined turn on another model and "
        "says so in the response. What that model returned may itself be a refusal, which "
        "`stop_reason` says. None when the model asked for took the turn, or when the vendor "
        "does not say.",
    )
    billed_usd: float | None = Field(
        default=None,
        description="What the vendor says this call was billed, in dollars, where it says so: "
        "OpenRouter's `usage.cost`, with the upstream charge added on a bring-your-own-key "
        "turn. The loop records it as the turn's cost in place of rate times tokens, unless "
        "`--price` or `QUACKD_PRICE` named a rate. None for every vendor that reports tokens "
        "and leaves the arithmetic to the caller, which is all of them but one.",
    )


class ProviderError(RuntimeError):
    """A turn the model's API did not give, in this provider's words.

    Every provider that talks to an SDK raises this `from` the SDK's own exception, and that
    chain is how the loop tells a failure worth asking again from one that is not
    (`transient_failure`): the SDK's status code and class name are read, never this message."""

    billed_usd: float | None = None
    """What the vendor billed for the call that failed, where it said so: OpenRouter can answer
    with an error and the bill for what the provider had already used. None almost always."""
    usage: Usage | None = None
    """The tokens that call spent, where the failed answer reported them. With `billed_usd`, the
    loop costs and counts the call like any other, rather than leaving it out of the total."""


class ProviderNotInstalled(ProviderError):
    def __init__(self, provider: str, extra: str) -> None:
        super().__init__(
            f"provider {provider!r} needs the optional extra quackd[{extra}] — "
            f'run: uvx --from "quackd[{extra}]" quackd ...  '
            f'or: uv pip install "quackd[{extra}]"'
        )


class ProviderMissingKey(ProviderError):
    def __init__(self, provider: str, env_var: str) -> None:
        super().__init__(
            f"provider {provider!r} needs {env_var} (set it in .env or the environment)"
        )


# ── a turn the API could not answer, and whether it is worth asking again ──────────────────

NO_SDK_RETRIES = 0
"""The retries every SDK client quackd builds is told to make of its own: none. The anthropic and
openai SDKs retry a failed call twice by default, inside the call, and sit out a `retry-after`
there, openai's up to two minutes and anthropic's of any length, where a first Ctrl-C does not
reach and nothing is recorded. The loop asks a turn again itself instead, on a clock it bounds
and a stop ends, with every try on the record (ADR-0059). google-genai retries nothing by
default and is built as it is."""

ASK_AGAIN_STATUSES = frozenset({408, 429})
"""The HTTP statuses below 500 that say "not now" rather than "no": a request that timed out at
the server (408) and a rate limit (429). Every 5xx is one too, Anthropic's 529 "overloaded"
among them. 409 is left out on purpose, though both SDKs retry it themselves: it is a conflict
over the request, and asking the same request again a minute later is not what resolves one.
Read off anthropic 1.9.0 and openai 3.22.1 on 2026-10-23, whose own retries use the same set."""

TIMED_OUT_ERRORS = frozenset({"APITimeoutError", "TimeoutException", "TimeoutError"})
"""Class names, anywhere in a failure's MRO, of a request that got no answer in time: the
anthropic and openai SDKs' `APITimeoutError`, httpx's `TimeoutException` (its `ReadTimeout`,
`ConnectTimeout` and `PoolTimeout` among them) and Python's own `TimeoutError`. Names, not
imports, because no SDK is imported here and CI installs none."""

NO_CONNECTION_ERRORS = frozenset(
    {
        "APIConnectionError",
        "NetworkError",
        "RemoteProtocolError",
        "ClientConnectionError",
        "ConnectionError",
    }
)
"""Class names of a request that never got a connection, or lost it: the SDKs'
`APIConnectionError`, httpx's `NetworkError` (`ConnectError`, `ReadError`, `WriteError`) and
`RemoteProtocolError` (a server that hung up mid-answer), aiohttp's `ClientConnectionError`,
and Python's own `ConnectionError`. A request quackd built wrong is not one of them, though the
SDKs raise it as one (`BUILT_WRONG_ERRORS`)."""

BUILT_WRONG_ERRORS = frozenset({"LocalProtocolError", "UnsupportedProtocol"})
"""Class names of a request quackd built wrong, which asking again sends wrong again: httpx's
`LocalProtocolError`, a request the protocol layer would not send, and `UnsupportedProtocol`, a
URL that is not http or https. The anthropic and openai SDKs wrap every failure inside their
transport as their own `APIConnectionError`, raised from it, so these are looked for beneath a
connection error as well as on their own."""

SHOULD_RETRY_HEADER = "x-should-retry"
"""A header an API sets to say whether a failure is worth retrying, which both SDKs obey, and so
does this: `false` makes any status permanent, and `true` makes any status worth asking again.
Read here because the SDKs are built to retry nothing of their own (`NO_SDK_RETRIES`), so what
the header says would otherwise go unheard."""

RETRY_INFO_TYPE = "type.googleapis.com/google.rpc.RetryInfo"
"""The detail Google's APIs put in an error body to say how long to wait (`retryDelay`, like
`"39s"`), where the others use a `retry-after` header."""

STATUS_WORDS = {408: "the request timed out at the server", 429: "rate limited", 529: "overloaded"}
"""What a person reads for the statuses that have a word of their own; every other 5xx is
`SERVER_ERROR`."""

SERVER_ERROR = "a server error"
ASKED_TO_ASK_AGAIN = "the API asked to be asked again"
"""What a person reads for a status worth nothing by itself that the API said to retry."""
NO_ANSWER_IN_TIME = "no answer in time"
NO_CONNECTION = "no connection"


class Transient(NamedTuple):
    """A turn the API could not answer for now, rather than one it said no to."""

    why: str
    """The words a person reads: `rate limited (429)`, `overloaded (529)`, `no connection`."""
    status: int | None
    """The HTTP status the failure carried, or None for one that never got an answer at all,
    which a caller can tell apart: a connection that never answered is as often a wrong address
    as a dropped one."""
    retry_after_s: float | None
    """How long the API asked to wait, where it said: None almost always."""


def transient_failure(error: BaseException) -> Transient | None:
    """Whether a provider's failure is worth asking again, read off the SDK's own exception.

    It follows `__cause__` from `error` down, because every provider raises its `ProviderError`
    `from` the SDK's exception, and only that: an implicit `__context__` says a failure happened
    while handling another, which is no reason to ask again. The first status found decides,
    the status codes and headers being the SDK's own attributes, so a 529 reads the same through
    anthropic's `OverloadedError` and openai's `InternalServerError`. With no status, the class
    names in the MRO decide (`TIMED_OUT_ERRORS`, `NO_CONNECTION_ERRORS`). The message is never
    read, so a `ProviderError` quackd raised itself, a refused redirect, a missing key, a reply
    that did not parse, is never asked again, whatever its words say.

    Never raises: it runs inside the handler that writes the failed call's record."""
    try:
        seen: set[int] = set()
        link: BaseException | None = error
        while link is not None and id(link) not in seen:
            seen.add(id(link))
            status = _status_of(link)
            if status is not None:
                return _by_status(link, status)
            names = {cls.__name__ for cls in type(link).__mro__}
            if names & TIMED_OUT_ERRORS:
                return Transient(NO_ANSWER_IN_TIME, None, None)
            if names & NO_CONNECTION_ERRORS:
                return None if _built_wrong(link) else Transient(NO_CONNECTION, None, None)
            if names & BUILT_WRONG_ERRORS:
                return None
            link = link.__cause__
    except Exception:
        return None
    return None


def _built_wrong(link: BaseException) -> bool:
    """Whether a connection error was raised from a request quackd built wrong
    (`BUILT_WRONG_ERRORS`), down its `__cause__` chain."""
    seen: set[int] = set()
    cause: BaseException | None = link
    while cause is not None and id(cause) not in seen:
        seen.add(id(cause))
        if {cls.__name__ for cls in type(cause).__mro__} & BUILT_WRONG_ERRORS:
            return True
        cause = cause.__cause__
    return False


def _status_of(link: BaseException) -> int | None:
    """The HTTP status a failure carries: `status_code` (anthropic, openai, httpx) or `code`
    (google-genai), on the failure or on its `response`. Only an int in 100..599 counts, and
    never a bool, which is an int to Python and a status to nobody."""
    for holder in (link, getattr(link, "response", None)):
        for name in ("status_code", "code"):
            value = getattr(holder, name, None)
            if isinstance(value, int) and not isinstance(value, bool) and 100 <= value <= 599:
                return value
    return None


def _by_status(link: BaseException, status: int) -> Transient | None:
    headers = getattr(getattr(link, "response", None), "headers", None)
    should = _header(headers, SHOULD_RETRY_HEADER)
    said = should.strip().lower() if should is not None else None
    if said == "false":
        return None
    worth = status in ASK_AGAIN_STATUSES or 500 <= status <= 599
    if not worth and said != "true":
        return None
    word = STATUS_WORDS.get(status, SERVER_ERROR if worth else ASKED_TO_ASK_AGAIN)
    return Transient(f"{word} ({status})", status, _asked_to_wait_s(link, headers))


def _header(headers: Any, name: str) -> str | None:
    if headers is None:
        return None
    try:
        value = headers.get(name)
    except Exception:
        return None
    return value if isinstance(value, str) else None


def _asked_to_wait_s(link: BaseException, headers: Any) -> float | None:
    """How long the API asked to wait, read the way openai 3.22.1 reads it: `retry-after-ms`,
    then `retry-after` in seconds, then `retry-after` as an HTTP date; and for Google's APIs,
    the `RetryInfo` detail in the error body. Only a finite, positive number of seconds counts."""
    milliseconds = _seconds(_header(headers, "retry-after-ms"))
    if milliseconds is not None:
        return milliseconds / 1000.0
    after = _header(headers, "retry-after")
    if after is not None:
        seconds = _seconds(after)
        if seconds is not None:
            return seconds
        with contextlib.suppress(Exception):
            when = email.utils.parsedate_to_datetime(after)
            return _positive(when.timestamp() - time.time())
    return _retry_info_s(getattr(link, "details", None))


def _seconds(text: str | None) -> float | None:
    if text is None:
        return None
    try:
        return _positive(float(text.strip()))
    except ValueError:
        return None


def _positive(value: float) -> float | None:
    return value if math.isfinite(value) and value > 0 else None


def _retry_info_s(details: Any) -> float | None:
    """google-genai's `APIError.details` is the error body, `{"error": {"details": [...]}}`, one
    entry of which may be a `RetryInfo` with `retryDelay` in seconds, like `"39s"`."""
    if not isinstance(details, dict):
        return None
    error = details.get("error")
    entries = error.get("details") if isinstance(error, dict) else None
    for entry in entries if isinstance(entries, list) else ():
        if isinstance(entry, dict) and entry.get("@type") == RETRY_INFO_TYPE:
            delay = entry.get("retryDelay")
            match = re.fullmatch(r"(\d+(?:\.\d+)?)s", delay) if isinstance(delay, str) else None
            if match:
                return _positive(float(match.group(1)))
    return None


@runtime_checkable
class LLMProvider(Protocol):
    name: str
    model: str
    supports_vision: bool

    async def step(
        self, system: str, history: list[Exchange], tools: list[dict[str, Any]]
    ) -> ProviderTurn: ...


JUDGE_FEATURE = "judge"
"""Where the loop puts a person's word on whether the arm did the task, on the observation a
`JudgedPilot` reads next: `question`, `answer` (True, False, or None when there was nobody to
put it to or the prompt raised), `asked` (`quackd.log.a_person_was_asked` of whoever was
asked) and, only when the prompt raised before anybody answered, `raised`, the name of what it
raised."""


@runtime_checkable
class JudgedPilot(Protocol):
    """A pilot that cannot tell whether its task was done, so a person says (`--controller vla`,
    `providers.vla`).

    The loop asks `judge_question` before each turn, puts the question to `RunConfig.judge`
    once it names one, and hands the answer back under `JUDGE_FEATURE`. It also holds the
    pilot's `declare_success` to a yes from a person really asked, whatever the pilot says."""

    def judge_question(
        self, history: Sequence[Exchange], *, cut_short: str | None = None
    ) -> str | None:
        """The question to put to a person now, or None when it is not yet time.

        `history` ends with the observation about to be shown. `cut_short` is why the run is
        ending before the pilot finished, a spent budget, and the question is then about what
        did run, or None when nothing did."""
        ...
