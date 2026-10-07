"""The provider protocol: what quackd needs from an LLM and nothing more.

quackd keeps its own vendor-neutral history (`Exchange` = an observation and the decision
it produced). Each provider renders that into its wire format and returns one
`ProviderTurn`. Tools are described once, as JSON Schema, in the Anthropic shape
(`name`, `description`, `input_schema`); other providers translate.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any, Protocol, runtime_checkable

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
    the vendor has one. Anthropic charges for it when a request marks a cache, which quackd
    never does, and OpenAI on GPT-5.6 and later and Kimi on K3 charge for the cache they write by
    themselves. Elsewhere a cache costs nothing to create and this stays 0."""

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


class ProviderTurn(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    tool_calls: list[ToolCall] = Field(default_factory=list)
    text: str | None = None
    usage: Usage = Field(default_factory=Usage)
    stop_reason: str | None = None
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
