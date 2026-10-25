"""Claude as the duck's brain, via the official `anthropic` SDK (optional extra).

Written against `anthropic` 1.x: adaptive thinking is the model's default on Claude Opus 5 and
later, and the request asks for `display: "summarized"` because the default display leaves
every thinking block's text empty, which would make the log's "what it thought" a blank line.
`display` changes what is shown, never what is thought or billed. A model that rejects the
`thinking` parameter (one older than Claude 4.6) gets one retry without it, and the run goes
on without thinking text. `output_config.effort` is sent to every model that takes it, which is
every row but Haiku 4.5 and Sonnet 4.5 (`ModelSpec.effort`).

`tool_choice={"type": "any", "disable_parallel_tool_use": True}` guarantees exactly one tool
call per turn on every model that accepts a forced call. Claude
Opus 5.5 and Claude Fable 5.1 do not, and answer one with a 400, so the catalogue marks them
(`ModelSpec.forced_tools`) and they are asked with `auto` instead, still one call per turn; a
model the catalogue does not mark gets one retry with `auto` when it says the same thing, and
keeps it. Images ride as base64 PNG blocks, and the assistant's raw content blocks (including
thinking) are replayed verbatim on the next turn. Server-side refusal fallbacks are on by
default and drop out automatically if the installed SDK predates them; a turn one of them took
records the model that answered it (`ProviderTurn.served_by`).
"""

from __future__ import annotations

import base64
import os
import re
from typing import Any

from quackd.agent.providers.base import (
    NO_SDK_RETRIES,
    Decision,
    Exchange,
    ProviderError,
    ProviderNotInstalled,
    ProviderTurn,
    ToolCall,
    Usage,
    picture_parts,
)
from quackd.agent.providers.catalogue import default_model_for, find_model

FALLBACK_BETA = "server-side-fallback-2026-07-01"

#: What lets a request ask the API to drop a replayed thinking block whose history changed,
#: rather than refuse the request (`thinking.block_binding.prefix_mismatch_behavior`).
BINDING_BETA = "thinking-binding-controls-2026-08-01"

#: How many exchanges the loop lets pass between two trims of old camera frames, for a model
#: that binds its thinking blocks. A trim that takes a frame away edits an earlier message and
#: so invalidates the blocks produced while that frame was still sent. From the trim call on the
#: loop leaves those out, all but the latest turn's, which may not be left out and which
#: `drop_block` has the API discard instead, so the blocks produced since the trim stay valid
#: until the next one. Eight keeps a request to at most nine exchanges' frames with the default
#: window of two, against two for every other model.
BINDING_TRIM_PERIOD = 8

CACHE_ENV = "QUACKD_ANTHROPIC_CACHE"
"""Set to 0, false or no to send the system prompt unmarked, so nothing is cached (`_system`)."""


def _image_block(png: bytes) -> dict[str, Any]:
    return {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": "image/png",
            "data": base64.standard_b64encode(png).decode("ascii"),
        },
    }


def render_messages(history: list[Exchange]) -> list[dict[str, Any]]:
    """quackd's vendor-neutral history → Messages API `messages`."""
    messages: list[dict[str, Any]] = []
    for ex in history:
        obs = ex.observation
        pictures = picture_parts(obs, _image_block, lambda text: {"type": "text", "text": text})
        if obs.tool_call_id:
            inner: list[dict[str, Any]] = [{"type": "text", "text": obs.text}, *pictures]
            content: list[dict[str, Any]] = [
                {"type": "tool_result", "tool_use_id": obs.tool_call_id, "content": inner}
            ]
        else:
            content = [*pictures, {"type": "text", "text": obs.text}]
        messages.append({"role": "user", "content": content})
        if ex.decision is not None:
            if isinstance(ex.decision.raw, list) and ex.decision.raw:
                blocks = ex.decision.raw  # replay thinking + tool_use blocks unchanged
            else:
                tc = ex.decision.tool_call
                blocks = []
                if ex.decision.text:
                    blocks.append({"type": "text", "text": ex.decision.text})
                blocks.append(
                    {"type": "tool_use", "id": tc.id, "name": tc.name, "input": tc.arguments}
                )
            messages.append({"role": "assistant", "content": blocks})
    return messages


def render_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {"name": t["name"], "description": t["description"], "input_schema": t["input_schema"]}
        for t in tools
    ]


def _block_to_dict(block: Any) -> dict[str, Any]:
    if isinstance(block, dict):
        return block
    for attr in ("model_dump", "to_dict"):
        fn = getattr(block, attr, None)
        if callable(fn):
            out = fn()
            if isinstance(out, dict):
                return {k: v for k, v in out.items() if v is not None}
    return {"type": getattr(block, "type", "text"), "text": str(block)}


def _tool_choice(*, forced: bool) -> dict[str, Any]:
    """One call per turn either way: `any` insists on it, `auto` only allows it, and
    `disable_parallel_tool_use` works with both."""
    return {"type": "any" if forced else "auto", "disable_parallel_tool_use": True}


def _served_by(response: Any) -> str | None:
    """The model that answered, when a server-side fallback re-ran the turn on another one.

    `usage.iterations` carries one `fallback_message` entry per model a declined turn was
    handed to, and it is there on a sticky turn too, where the fallback model answered from
    the start and the content carries no `fallback` block to say so. `response.model` is then
    the model that served it. A turn with no such entry was answered by the model asked for,
    and `response.model` is not compared with the request at all, because an alias such as
    `claude-haiku-4-5` comes back as its dated snapshot and would read as a different model."""
    usage = getattr(response, "usage", None)
    iterations = getattr(usage, "iterations", None) or []
    kinds = [
        it.get("type") if isinstance(it, dict) else getattr(it, "type", None) for it in iterations
    ]
    if "fallback_message" not in kinds:
        return None
    model = getattr(response, "model", None)
    return model if isinstance(model, str) and model else None


def parse_response(response: Any) -> ProviderTurn:
    tool_calls: list[ToolCall] = []
    texts: list[str] = []
    thoughts: list[str] = []
    for block in response.content:
        kind = getattr(block, "type", None)
        if kind == "tool_use":
            args = block.input if isinstance(block.input, dict) else dict(block.input)
            tool_calls.append(ToolCall(id=str(block.id), name=str(block.name), arguments=args))
        elif kind == "text":
            texts.append(block.text)
        elif kind == "thinking":
            # zero or more per response, and any of them can be empty (a progress block, or
            # the default display); an empty one renders nothing rather than a blank line
            thought = str(getattr(block, "thinking", "") or "").strip()
            if thought:
                thoughts.append(thought)
        elif kind == "redacted_thinking":
            thoughts.append("[redacted thinking]")
    stop_reason = getattr(response, "stop_reason", None)
    if stop_reason == "refusal":
        details = getattr(response, "stop_details", None)
        explanation = getattr(details, "explanation", None) or "the model refused"
        texts.append(f"[refusal] {explanation}")
        tool_calls = []
    usage = getattr(response, "usage", None)
    # Anthropic is the one vendor that reports its three input buckets DISJOINT: `input_tokens`
    # is what was neither read from a cache nor written to one. quackd's `input_tokens` is the
    # whole prompt (`providers.base.Usage`), so the three are added back up here and the two
    # cache numbers ride along beside it for the rates they are actually billed at. quackd marks
    # the system prompt for the cache on every request (`_system`), so from a run's second call
    # the read bucket carries it, and the first call's write is billed at the write rate.
    cache_read = int(getattr(usage, "cache_read_input_tokens", 0) or 0)
    # `cache_creation_input_tokens`, not the `cache_creation` object beside it: that object is
    # a breakdown of this same number by cache lifetime (5 minute and 1 hour), and adding both
    # would charge every cached prompt twice.
    cache_write = int(getattr(usage, "cache_creation_input_tokens", 0) or 0)
    # Thinking is inside `output_tokens`, which Anthropic calls "the inclusive, authoritative
    # total used for billing"; `output_tokens_details` is a read-only decomposition of it. So
    # this is recorded for the reader and never priced again (`providers.base.Usage`).
    out_details = getattr(usage, "output_tokens_details", None)
    return ProviderTurn(
        tool_calls=tool_calls,
        text="\n".join(texts) or None,
        usage=Usage(
            input_tokens=int(getattr(usage, "input_tokens", 0) or 0) + cache_read + cache_write,
            output_tokens=int(getattr(usage, "output_tokens", 0) or 0),
            reasoning_tokens=int(getattr(out_details, "thinking_tokens", 0) or 0),
            cache_read_tokens=cache_read,
            cache_write_tokens=cache_write,
        ),
        stop_reason=stop_reason,
        refused=stop_reason == "refusal",
        raw=[_block_to_dict(b) for b in response.content],
        thinking="\n\n".join(thoughts) or None,
        served_by=_served_by(response),
    )


def _api_message(e: Exception) -> str:
    """The API's own sentence. The SDK's `message` is `Error code: 400 - {whole body}`;
    `body["error"]["message"]` is the part that names the parameter path."""
    body = getattr(e, "body", None)
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict) and isinstance(error.get("message"), str):
            return error["message"]
        if isinstance(body.get("message"), str):
            return str(body["message"])
    return str(getattr(e, "message", None) or e)


#: What Claude 4.5 and earlier answer `thinking: {"type": "adaptive"}` with, word for word from
#: Anthropic's API errors page (read 2026-09-23). It opens with "adaptive" rather than with the
#: parameter's name, so the anchored match below never saw it, and Haiku 4.5, Sonnet 4.5 and
#: Opus 4.5 failed their first call on every run instead of going on without thinking.
ADAPTIVE_REFUSED = "adaptive thinking is not supported on this model"


def _rejects_thinking(e: Exception) -> bool:
    """A 400 refusing the top-level `thinking` parameter: a model too old for adaptive thinking.

    Two shapes, both about the parameter itself. One opens with its name (`thinking: Extra
    inputs are not permitted`), and that match is anchored because a 400 about a *replayed*
    thinking block names a path (`messages.3.content.0.thinking.signature: Invalid
    signature`) rather than the parameter. Treating that as "this model has no thinking" would
    retry the identical request — the messages are what it objected to — fail again, and leave
    thinking off for the whole run. The other is the sentence the 4.5 models actually send,
    matched as a whole sentence rather than on a word, for the same reason.
    """
    if type(e).__name__ != "BadRequestError":
        return False
    message = _api_message(e)
    return re.match(r"\s*thinking\b", message) is not None or ADAPTIVE_REFUSED in message


def _refuses_forced_tools(e: Exception) -> bool:
    """A 400 refusing `tool_choice` `any`: a model that will not be made to call a tool.

    Anchored at the parameter the way `_rejects_thinking` is, and it has to say the type is
    not supported as well. A 400 about a tool the request does not declare also opens with
    `tool_choice`, and asking again with `auto` would not fix that one: it would only hide it
    behind a second request."""
    if type(e).__name__ != "BadRequestError":
        return False
    message = _api_message(e)
    return re.match(r"\s*tool_choice\b", message) is not None and "not supported" in message


class AnthropicProvider:
    name = "anthropic"
    supports_vision = True

    def __init__(
        self,
        model: str | None = None,
        *,
        client: Any = None,
        max_tokens: int | None = None,
        effort: str | None = None,
        fallbacks: bool | None = None,
        thinking_display: str | None = None,
        vision: bool | None = None,
    ) -> None:
        # No model means whatever the catalogue lists first for this vendor.
        self.model = model or default_model_for(self.name) or ""
        spec = find_model(self.name, self.model)
        #: Whether this model accepts a forced tool call. The catalogue says so up front for
        #: the models it knows refuse one, and `step` learns it from the 400 for the rest.
        self.forced_tools = spec.forced_tools if spec is not None else True
        #: Whether this model takes `output_config.effort` at all. Only the catalogue says so:
        #: Anthropic's effort page lists the models that take it and prints no error for the rest.
        self.sends_effort = spec.effort if spec is not None else True
        #: A model that binds each replayed thinking block to everything before it
        #: (`ModelSpec.binds_thinking`). It is asked to have a block whose history changed
        #: dropped rather than refused, and the loop reads `frame_trim_period` to trim its old
        #: camera frames in steps instead of on every call.
        self.binds_thinking = spec.binds_thinking if spec is not None else False
        self.frame_trim_period = BINDING_TRIM_PERIOD if self.binds_thinking else 1
        # `--no-vision` has to reach every vendor, not most of them: the catalogue promises
        # the flag overrides it in both directions, and a reader who declined the frames must
        # not be billed for them anyway. The catalogue's own per-model flag is not consulted
        # here, because every model this vendor lists takes an image.
        if vision is not None:
            self.supports_vision = vision
        self.max_tokens = max_tokens or int(os.environ.get("QUACKD_MAX_TOKENS", "16000"))
        self.effort = effort or os.environ.get("QUACKD_EFFORT", "medium")
        #: Where `effort` came from, for the record (`asked_effort`): whoever built this provider,
        #: which the factory renames `--effort`, then `QUACKD_EFFORT`, then quackd's own medium.
        self.effort_from = "the caller"
        if not effort:
            self.effort_from = "QUACKD_EFFORT" if os.environ.get("QUACKD_EFFORT") else "default"
        env_fb = os.environ.get("QUACKD_ANTHROPIC_FALLBACKS", "1") not in ("0", "false", "no")
        #: Whether requests mark the prompt for Anthropic's cache (`_system`). On unless
        #: `QUACKD_ANTHROPIC_CACHE` says 0, false or no, beside the fallbacks switch above: a
        #: marked prompt is read at a tenth of the input rate from the second call on and
        #: prefilled sooner, and the one write it costs is a quarter more than an unmarked call.
        self.caches = os.environ.get(CACHE_ENV, "1").strip().lower() not in ("0", "false", "no")
        self.fallbacks = env_fb if fallbacks is None else fallbacks
        # `summarized` is what makes the thinking blocks carry text. `omitted` keeps the
        # request but hides the text; an empty value sends no `thinking` parameter at all.
        display = (
            os.environ.get("QUACKD_THINKING_DISPLAY", "summarized")
            if thinking_display is None
            else thinking_display
        )
        self.thinking_display: str | None = display.strip() or None
        self.calls = 0
        if client is None:
            try:
                import anthropic
            except ImportError as e:
                raise ProviderNotInstalled("anthropic", "anthropic") from e
            # It resolves the key or the `ant auth` profile itself. No retries of its own: the
            # SDK sits out a `retry-after` of any length inside the call, where a first Ctrl-C
            # cannot reach and nothing is recorded, and the loop asks again on its own clock,
            # stoppable and on the record (ADR-0059).
            client = anthropic.AsyncAnthropic(max_retries=NO_SDK_RETRIES)
        self.client = client

    def _params(
        self, system: str, history: list[Exchange], tools: list[dict[str, Any]]
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "system": self._system(system),
            "messages": render_messages(history),
            "tools": render_tools(tools),
            "tool_choice": _tool_choice(forced=self.forced_tools),
        }
        if self.effort and self.sends_effort:
            params["output_config"] = {"effort": self.effort}
        if self.thinking_display or self.binds_thinking:
            thinking: dict[str, Any] = {"type": "adaptive"}
            if self.thinking_display:
                thinking["display"] = self.thinking_display
            if self.binds_thinking:
                # The loop leaves out the blocks a trim of an old frame invalidates, all but the
                # latest turn's, which may not be left out; this has the API discard that one
                # instead of answering the whole request with a 400.
                thinking["block_binding"] = {"prefix_mismatch_behavior": "drop_block"}
            params["thinking"] = thinking
        return params

    def _system(self, system: str) -> str | list[dict[str, Any]]:
        """The system prompt as it is sent: one text block marked for the cache, or the string.

        The prompt is built once per run and the tools are the same list every call, and
        Anthropic orders a request tools, then system, then messages, so one breakpoint on the
        system block caches both, which is the bulk of every request quackd makes: the rules,
        the verbs, the datasheet and the task. Five minutes is the cache's own lifetime, longer
        than any pause between two turns of a run, and the write rate in every catalogue row is
        that lifetime's (`catalogue.py`). A prompt below the model's smallest cacheable size is
        sent marked and simply not cached, which Anthropic answers with no error and a write of
        0. A marker sits outside what a model that binds its thinking binds a block to, and one
        text block is the same system prompt as the string it holds, so the trim's arithmetic
        (`loop._history_for_provider`) is untouched. With `QUACKD_ANTHROPIC_CACHE=0` it is the
        string as it always was."""
        if not self.caches or not system:
            return system
        return [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]

    @property
    def asked_effort(self) -> dict[str, str] | None:
        """What every request of this run asks the model to reason at, and where that came from,
        as `run_start.effort` records it, or None for a model that is sent no effort at all
        (`ModelSpec.effort`, or `QUACKD_EFFORT` set blank)."""
        if not (self.effort and self.sends_effort):
            return None
        return {"value": self.effort, "from": self.effort_from}

    def warm(self) -> None:
        """What the SDK's client asks before its first request, asked now, while the robot
        connects (`sdk_warm.warm_client`). Sends nothing and never raises."""
        from quackd.agent.providers.sdk_warm import warm_client

        warm_client(self.client, "anthropic")

    def without_thinking(self, decision: Decision) -> Decision:
        """The same turn with its thinking and redacted thinking blocks left out.

        The loop calls this for a model that binds its thinking (`frame_trim_period` above 1),
        on the turns whose blocks a trim of old frames invalidated. Anthropic allows leaving out
        a leading run of thinking blocks, oldest first, and never the latest assistant
        message's, which the loop does not pass here. The tool call and any text stay."""
        if not isinstance(decision.raw, list):
            return decision
        kept = [
            b
            for b in decision.raw
            if not (isinstance(b, dict) and b.get("type") in ("thinking", "redacted_thinking"))
        ]
        return decision.model_copy(update={"raw": kept})

    async def _create(self, params: dict[str, Any]) -> Any:
        betas = [FALLBACK_BETA] if self.fallbacks else []
        if self.binds_thinking:
            betas.append(BINDING_BETA)
        if betas:
            extra: dict[str, Any] = {"fallbacks": "default"} if self.fallbacks else {}
            try:
                return await self.client.beta.messages.create(**params, betas=betas, **extra)
            except TypeError as e:
                # Only a TypeError naming one of the two keywords means "this SDK predates
                # the beta endpoint's arguments". Any other one comes from inside the request (a
                # serialisation bug, a stub) and swallowing it would silently drop fallbacks
                # for the rest of the run and hide the real failure behind a second request.
                if "betas" in str(e):
                    if self.binds_thinking:
                        # `block_binding` without its header is a 400 of its own, and without
                        # it the first trim of an old frame is one: better said now than there
                        raise ProviderError(
                            f"anthropic: {self.model} binds its thinking to everything before "
                            "it, and this anthropic SDK sends no betas, so quackd cannot ask "
                            "for a block an old frame's trim invalidated to be dropped. "
                            "Upgrade the anthropic package, or pick a model that does not."
                        ) from e
                    # the plain endpoint from now on
                    self.fallbacks = False
                elif "fallbacks" in str(e) and self.fallbacks:
                    # An SDK that predates `fallbacks` alone still sends betas, and a binding
                    # model keeps its own: the beta endpoint without fallbacks from now on.
                    self.fallbacks = False
                    return await self._create(params)
                else:
                    raise
        return await self.client.messages.create(**params)

    async def _create_repairing(self, params: dict[str, Any]) -> Any:
        """The request, repaired at most once for each of the two things a model refuses.

        A model that predates adaptive thinking refuses the `thinking` parameter: it is asked
        again without it, and every later turn goes without it too. The run loses the thinking
        text, not itself. A model that will not be forced to call a tool refuses `tool_choice`
        `any`: it is asked again with `auto`, and every later turn asks that way too. Each
        repair switches off the flag that guards it, so neither can fire twice and this cannot
        loop: a refusal that was already repaired once, or any other error, is raised as it
        came."""
        while True:
            try:
                return await self._create(params)
            except Exception as e:
                if self.thinking_display and not self.binds_thinking and _rejects_thinking(e):
                    # Not for a model that binds its thinking: it thinks whether it is asked to
                    # or not, so going without the field would only take `block_binding` with
                    # it and make the first trim of an old frame a 400. It gets the refusal.
                    self.thinking_display = None
                    params.pop("thinking", None)
                elif self.forced_tools and _refuses_forced_tools(e):
                    self.forced_tools = False
                    params["tool_choice"] = _tool_choice(forced=False)
                else:
                    raise

    async def step(
        self, system: str, history: list[Exchange], tools: list[dict[str, Any]]
    ) -> ProviderTurn:
        params = self._params(system, history, tools)
        self.calls += 1
        # parse_response is inside the classifying try on purpose: a response whose content
        # blocks or usage fields are not the shape it expects would otherwise escape the
        # provider as a raw traceback — the CLI only catches TransportError and ProviderError.
        try:
            return parse_response(await self._create_repairing(params))
        except ProviderError:
            raise
        except Exception as e:
            raise _classify(e) from e


def _classify(e: Exception) -> ProviderError:
    """Map SDK exceptions (most specific first) to one ProviderError with a useful message."""
    name = type(e).__name__
    status = getattr(e, "status_code", None)
    if name == "AuthenticationError":
        return ProviderError(
            "anthropic: invalid or missing API key (ANTHROPIC_API_KEY, or `ant auth login`)"
        )
    if name == "PermissionDeniedError":
        return ProviderError("anthropic: this key lacks permission for the requested model")
    if name == "NotFoundError":
        return ProviderError("anthropic: model not found — check --llm / QUACKD_LLM")
    if name == "RateLimitError":
        retry = getattr(getattr(e, "response", None), "headers", {}).get("retry-after", "?")
        return ProviderError(f"anthropic: rate limited (retry-after {retry}s)")
    if name == "BadRequestError":
        return ProviderError(f"anthropic: bad request — {getattr(e, 'message', e)}")
    if name == "APIStatusError" or (status is not None and int(status) >= 500):
        return ProviderError(f"anthropic: API error {status}: {getattr(e, 'message', e)}")
    if name in ("APIConnectionError", "APITimeoutError"):
        return ProviderError(f"anthropic: network error — {e}")
    return ProviderError(f"anthropic: {name}: {e}")
