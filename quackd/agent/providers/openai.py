"""OpenAI as the duck's brain, via the `openai` SDK (optional extra).

Also the base class for every vendor that speaks OpenAI's API rather than one of its own,
which by now is most of them: Grok, Mistral, DeepSeek, Cohere, Qwen, Kimi, GLM, Meta, OpenRouter
and the local servers. They change the class knobs, and OpenRouter the few hooks below as well.

Chat Completions with function tools, `tool_choice="required"` and
`parallel_tool_calls=False` for one call per turn. Tool results go back as `tool` messages;
because a `tool` message cannot carry an image, the frame follows in a `user` message.

Some newer reasoning models will not take function tools on Chat Completions at all, at any
reasoning effort, and say so in a 400 that names `/v1/responses` as the way through. Every
verb quackd has is a function tool, so for those models Chat Completions is not a degraded
path, it is no path. `step` reads that 400, switches this provider to the Responses API and
keeps it there for the rest of the run. The two APIs disagree about nearly every field name,
so each gets its own renderer and its own parser below, and the provider owns which pair it
is using. `QUACKD_OPENAI_API=responses` starts there without waiting to be told.

Reading the 400 is the fallback, not the plan. The catalogue marks the models that need Responses
and they start there, which saves the failed call and is the only way in for a model that is
Responses *only*: that one is refused for being itself rather than for asking for tools, so it
says something else entirely and the reader below does not match it.
"""

from __future__ import annotations

import base64
import json
from collections.abc import Callable, Mapping
from typing import Any

from quackd.agent.providers.base import (
    Decision,
    Exchange,
    Observation,
    ProviderError,
    ProviderMissingKey,
    ProviderNotInstalled,
    ProviderTurn,
    ToolCall,
    Usage,
    picture_parts,
)
from quackd.agent.providers.catalogue import default_model_for, find_model


def _image_part(png: bytes) -> dict[str, Any]:
    data = base64.standard_b64encode(png).decode("ascii")
    return {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{data}"}}


def _frame_lead(obs: Observation) -> str:
    """The sentence in front of the pictures that follow a tool result, which cannot carry
    an image itself. Plural only when there is more than one camera to be plural about.

    A task picture in the same message is not a camera frame, so where one is present the
    sentence says only that pictures follow and the labels say which each one is. The loop
    attaches those to the first observation alone, which is never a tool result, so in
    practice this is the sentence a one or two camera body has always sent."""
    if obs.attachments:
        return "The task's pictures, and the current camera frames:"
    return "Current camera frame:" if len(obs.images) == 1 else "Current camera frames:"


def render_messages(
    system: str,
    history: list[Exchange],
    *,
    assistant_fields: Callable[[Decision], Mapping[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """The conversation as Chat Completions messages.

    `assistant_fields` adds a vendor's own keys to each assistant message, from the decision
    that message replays: OpenRouter hands back the `reasoning_details` that came with the call.
    None, or a hook that answers `{}`, leaves every message as it always was."""
    messages: list[dict[str, Any]] = [{"role": "system", "content": system}]
    for ex in history:
        obs = ex.observation
        pictures = picture_parts(obs, _image_part, lambda text: {"type": "text", "text": text})
        if obs.tool_call_id:
            messages.append({"role": "tool", "tool_call_id": obs.tool_call_id, "content": obs.text})
            if pictures:
                messages.append(
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": _frame_lead(obs)},
                            *pictures,
                        ],
                    }
                )
        else:
            parts: list[dict[str, Any]] = [{"type": "text", "text": obs.text}, *pictures]
            messages.append({"role": "user", "content": parts})
        if ex.decision is not None:
            tc = ex.decision.tool_call
            assistant: dict[str, Any] = {
                "role": "assistant",
                "content": ex.decision.text,
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {"name": tc.name, "arguments": json.dumps(tc.arguments)},
                    }
                ],
            }
            if assistant_fields is not None:
                assistant.update(assistant_fields(ex.decision))
            messages.append(assistant)
    return messages


def _wants_the_responses_api(error: Exception) -> bool:
    """Is this the 400 that refuses function tools on Chat Completions and names Responses?

    Matched on what the API says rather than on a model name, because the list of models that
    behave this way is not ours to keep and gets longer. Observed on `gpt-6-astra`:

        Function tools with reasoning_effort are not supported for gpt-6-astra in
        /v1/chat/completions. To use function tools, use /v1/responses or set
        reasoning_effort to 'none'.

    Both halves are required so an unrelated 400 that happens to name one of the two does not
    move a run onto a different API.

    The second remedy that message offers is a dead end on the model that produced it, which
    is worth recording: `reasoning_effort="none"` comes back as *"does not support 'none' with
    this model. Supported values are: 'low', 'medium', 'high', and 'xhigh'"*, and the tools are
    refused at every one of those. Measured 2026-09-09. So the fix is the API, not the effort.
    """
    if getattr(error, "status_code", None) not in (400, None):
        return False
    text = str(error).lower()
    return "function tools" in text and "responses" in text


def render_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t["description"],
                "parameters": t["input_schema"],
            },
        }
        for t in tools
    ]


def render_tools_responses(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The same tools, flat. Responses drops the `function` wrapper Chat Completions nests."""
    return [
        {
            "type": "function",
            "name": t["name"],
            "description": t["description"],
            "parameters": t["input_schema"],
        }
        for t in tools
    ]


def _image_part_responses(png: bytes) -> dict[str, Any]:
    data = base64.standard_b64encode(png).decode("ascii")
    return {"type": "input_image", "image_url": f"data:image/png;base64,{data}"}


def render_input(history: list[Exchange]) -> list[dict[str, Any]]:
    """History as Responses input items. The system prompt is not one: it goes in
    `instructions`, which is why this takes no `system` where `render_messages` does.

    A turn is up to three items rather than a message with a `tool_calls` field: what the
    model called, what came back, and the frame. `function_call_output` carries text only, as
    a `tool` message does, so the image follows it in a user item for the same reason.

    `call_id` is the handle, not the item `id`. Sending the wrong one is a 400 that says the
    output refers to a call that does not exist, so `parse_responses` reads `call_id` into
    `ToolCall.id` and the loop hands it straight back here.
    """
    items: list[dict[str, Any]] = []
    for ex in history:
        obs = ex.observation
        pictures = picture_parts(
            obs, _image_part_responses, lambda text: {"type": "input_text", "text": text}
        )
        if obs.tool_call_id:
            items.append(
                {
                    "type": "function_call_output",
                    "call_id": obs.tool_call_id,
                    "output": obs.text,
                }
            )
            if pictures:
                items.append(
                    {
                        "role": "user",
                        "content": [
                            {"type": "input_text", "text": _frame_lead(obs)},
                            *pictures,
                        ],
                    }
                )
        else:
            parts: list[dict[str, Any]] = [{"type": "input_text", "text": obs.text}, *pictures]
            items.append({"role": "user", "content": parts})
        if ex.decision is not None:
            tc = ex.decision.tool_call
            items.append(
                {
                    "type": "function_call",
                    "call_id": tc.id,
                    "name": tc.name,
                    "arguments": json.dumps(tc.arguments),
                }
            )
    return items


def parse_responses(response: Any) -> ProviderTurn:
    """A Responses result into the same `ProviderTurn` the Chat Completions parser returns.

    `output` is a flat list of items rather than one message: reasoning, then any calls, then
    any text. Reasoning arrives as a summary (a list of parts) and is often empty even when
    the token count is not, because the model is not obliged to show its work.
    """
    tool_calls: list[ToolCall] = []
    texts: list[str] = []
    thoughts: list[str] = []
    for item in getattr(response, "output", None) or []:
        kind = getattr(item, "type", None)
        if kind == "function_call":
            raw_args = getattr(item, "arguments", None)
            try:
                args = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args or {})
            except json.JSONDecodeError:
                args = {"_unparsed": raw_args}
            # call_id, not id: it is what a later function_call_output must quote.
            tool_calls.append(
                ToolCall(
                    id=str(getattr(item, "call_id", None) or getattr(item, "id", "")),
                    name=str(getattr(item, "name", "")),
                    arguments=args,
                )
            )
        elif kind == "message":
            for part in getattr(item, "content", None) or []:
                text = getattr(part, "text", None)
                if isinstance(text, str) and text.strip():
                    texts.append(text)
        elif kind == "reasoning":
            for part in getattr(item, "summary", None) or []:
                text = getattr(part, "text", None)
                if isinstance(text, str) and text.strip():
                    thoughts.append(text)
    usage = getattr(response, "usage", None)
    details = getattr(usage, "output_tokens_details", None)
    # the Responses API spells the same things `input_tokens` and `input_tokens_details`, and
    # means them the same way: the total already contains both cached slices
    input_details = getattr(usage, "input_tokens_details", None)
    return ProviderTurn(
        tool_calls=tool_calls,
        text="\n".join(texts) or None,
        usage=Usage(
            input_tokens=int(getattr(usage, "input_tokens", 0) or 0),
            output_tokens=int(getattr(usage, "output_tokens", 0) or 0),
            reasoning_tokens=int(getattr(details, "reasoning_tokens", 0) or 0),
            cache_read_tokens=int(getattr(input_details, "cached_tokens", 0) or 0),
            cache_write_tokens=int(getattr(input_details, "cache_write_tokens", 0) or 0),
        ),
        # No finish_reason here. `status` is "completed" or "incomplete", and the loop only
        # reads this for the log, so say the same words the other parser would.
        stop_reason="tool_calls" if tool_calls else getattr(response, "status", None),
        raw=None,
        thinking="\n".join(thoughts) or None,
    )


def parse_response(response: Any) -> ProviderTurn:
    choice = response.choices[0]
    message = choice.message
    tool_calls: list[ToolCall] = []
    for tc in getattr(message, "tool_calls", None) or []:
        fn = tc.function
        raw_args = fn.arguments
        try:
            args = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args or {})
        except json.JSONDecodeError:
            args = {"_unparsed": raw_args}
        tool_calls.append(ToolCall(id=str(tc.id), name=str(fn.name), arguments=args))
    usage = getattr(response, "usage", None)
    # OpenAI's own Chat Completions returns no reasoning text, only a count of the tokens it
    # spent on it. DeepSeek, vLLM's reasoning parsers, llama.cpp, LM Studio and xAI put the
    # text in `reasoning_content`; Ollama's /v1 and OpenRouter in `reasoning`. The SDK's
    # message model keeps unknown wire fields as attributes, so both reads work on the real
    # object and on a stub alike.
    reasoning = next(
        (
            v
            for v in (
                getattr(message, "reasoning_content", None),
                getattr(message, "reasoning", None),
            )
            if isinstance(v, str) and v.strip()
        ),
        None,
    )
    return ProviderTurn(
        tool_calls=tool_calls,
        text=getattr(message, "content", None) or None,
        usage=read_usage(usage),
        stop_reason=getattr(choice, "finish_reason", None),
        raw=None,
        thinking=reasoning.strip() if reasoning else None,
    )


def read_usage(usage: Any) -> Usage:
    """A Chat Completions `usage` object as quackd's buckets.

    `prompt_tokens` already CONTAINS both cached slices, because `prompt_tokens_details` is
    a breakdown of the prompt rather than an addition to it. So the total passes through as
    quackd's whole prompt and the two slices are named beside it. OpenAI's own guide puts it
    exactly the way `pricing.cost_usd` reads it: "input tokens use the uncached-input,
    cached-input, or cache-write rate", one rate per token and never two.

    From the second turn of a run most of the input is the cached slice, at a tenth of the
    rate, so a cost computed without this would be wrong by most of the bill. Cache writes
    are charged only on GPT-5.6 and later and are 0 on everything older."""
    details = getattr(usage, "completion_tokens_details", None)
    prompt_details = getattr(usage, "prompt_tokens_details", None)
    return Usage(
        input_tokens=int(getattr(usage, "prompt_tokens", 0) or 0),
        output_tokens=int(getattr(usage, "completion_tokens", 0) or 0),
        reasoning_tokens=int(getattr(details, "reasoning_tokens", 0) or 0),
        cache_read_tokens=int(getattr(prompt_details, "cached_tokens", 0) or 0),
        cache_write_tokens=int(getattr(prompt_details, "cache_write_tokens", 0) or 0),
    )


REFUSED_EXTRA_BODY_KEYS = frozenset(
    {"model", "messages", "input", "instructions", "tools", "stream"}
)
"""Keys quackd owns, and will not let a passthrough replace. `model` would walk past the
catalogue and put a model on the wire that `run_start` does not name. `messages`, `input`,
`instructions` and `tools` are the conversation itself, and `instructions` is the one that is
easy to miss: on Chat Completions the system prompt is the first of `messages`, but on
Responses it is a field of its own, so leaving it out would have let a passthrough quietly
replace the whole contract on one API and not the other. `stream` changes the shape of the
reply without telling the SDK, which then fails on the content type long after the robot has
connected. Everything else goes through, `tool_choice` and `reasoning_effort` included:
overriding those is the point."""

_EXTRA_BODY_EXAMPLE = '{"chat_template_kwargs": {"enable_thinking": false}}'


def parse_extra_body(text: str | None, *, source: str) -> dict[str, Any] | None:
    """`--extra-body` or `QUACKD_EXTRA_BODY` as the object the SDK merges into the request.

    One JSON object and nothing else: a list or a bare string has no top level to merge into.
    The error names the flag or the variable and echoes what arrived, because python-dotenv
    truncates an unquoted value at a `  #` and drops a double-quoted one entirely, and both
    look fine in the file. Empty is unset, so a `.env` line can be blanked rather than deleted,
    and `--extra-body '{}'` silences one for a single run.
    """
    if text is None or not text.strip():
        return None
    try:
        body = json.loads(text)
    except json.JSONDecodeError as e:
        raise ProviderError(
            f"{source}: not valid JSON ({e.msg} at column {e.colno}) in {text.strip()[:60]!r}: "
            f"one object, e.g. {_EXTRA_BODY_EXAMPLE}"
        ) from e
    if not isinstance(body, dict):
        kind = "null" if body is None else type(body).__name__
        raise ProviderError(
            f"{source}: a JSON object was expected, not {kind}: e.g. {_EXTRA_BODY_EXAMPLE}"
        )
    for key in sorted(REFUSED_EXTRA_BODY_KEYS & body.keys()):
        raise ProviderError(
            f"{source}: {key!r} is quackd's to send and cannot be replaced here. "
            f"Anything the server wants beside it can: e.g. {_EXTRA_BODY_EXAMPLE}"
        )
    return body


class OpenAIProvider:
    """OpenAI's own API. Subclasses (Grok, the local servers) only change the class knobs."""

    name = "openai"
    supports_vision = True
    key_env = "OPENAI_API_KEY"
    extra = "openai"
    """The `quackd[...]` extra a missing SDK should name. Every vendor here installs the
    same wheel, so the extra is the only part of that message that differs."""
    base_url: str | None = None
    default_tool_choice: str | None = "required"
    """`required` forces a call on OpenAI. `auto` for servers that reject `required`,
    `none` to omit the field entirely."""
    send_parallel_flag = True
    """OpenAI accepts `parallel_tool_calls=False`. Some local servers 400 on unknown fields."""
    prompt_hint = ""
    """Extra system-prompt text a provider wants (the local one explains the JSON fallback)."""
    default_headers: Mapping[str, str] = {}
    """Headers sent with every request beside the SDK's own. Empty for every vendor but
    OpenRouter, which credits an app on its public rankings by `HTTP-Referer` and
    `X-OpenRouter-Title`. Empty builds the client exactly as it was always built."""
    switches_api = True
    """Whether the 400 that refuses function tools on Chat Completions moves this run to the
    Responses API. True for OpenAI's own API; OpenRouter is asked on Chat Completions only."""
    refused_body_keys: Mapping[str, str] = {}
    """Keys this vendor refuses in `--extra-body` beside `REFUSED_EXTRA_BODY_KEYS`, each with
    the reason its refusal gives. Checked before a key is read, like the shared ones."""

    def __init__(
        self,
        model: str | None = None,
        *,
        client: Any = None,
        api_key: str | None = None,
        base_url: str | None = None,
        tool_choice: str | None = None,
        vision: bool | None = None,
        reasoning_effort: str | None = None,
        api: str | None = None,
        extra_body: dict[str, Any] | None = None,
    ) -> None:
        # No model means the catalogue's default. The empty string is what the local presets
        # pass up, and it means the opposite: ask the server (`LocalProvider.ensure_model`).
        self.model = model or default_model_for(self.name) or ""
        spec = find_model(self.name, self.model)
        self.calls = 0
        import os as _os

        #: Sent only when set, and spelled differently by each API: `reasoning_effort` on Chat
        #: Completions, `reasoning={"effort": ...}` on Responses.
        self.reasoning_effort = reasoning_effort or _os.environ.get(
            "QUACKD_OPENAI_REASONING_EFFORT"
        )
        #: Fields the server wants and quackd never sends, merged into the top level of every
        #: request body by the SDK's own `extra_body`, on either API. `{"chat_template_kwargs":
        #: {"enable_thinking": false}}` is how Qwen3's thinking is turned off on vLLM (#12).
        #: A dict from the caller wins over the environment, which is how `--extra-body` beats
        #: `QUACKD_EXTRA_BODY`: the factory parses the flag and hands it down. An empty dict is
        #: not None, so it skips the environment and sends nothing.
        self.extra_body = (
            extra_body
            if extra_body is not None
            else parse_extra_body(_os.environ.get("QUACKD_EXTRA_BODY"), source="QUACKD_EXTRA_BODY")
        )
        for refused in sorted(self.refused_body_keys.keys() & (self.extra_body or {}).keys()):
            door = "--extra-body" if extra_body is not None else "QUACKD_EXTRA_BODY"
            raise ProviderError(
                f"{door}: {refused!r} is refused on {self.name}: {self.refused_body_keys[refused]}"
            )
        #: "chat" or "responses". A model the catalogue marks `responses` starts there, which
        #: saves the failed call `step` would otherwise pay to learn it, and is the only way in
        #: for a model that is Responses only: its refusal is worded differently and
        #: `_wants_the_responses_api` does not match it. Otherwise `step` still switches on its
        #: own when the API says so, and stays switched. An explicit `api=` outranks both, and
        #: both outrank the environment: `QUACKD_OPENAI_API=chat` must not send a model that
        #: has no Chat Completions to one anyway.
        self.api = (
            (api or (spec.api if spec else None) or _os.environ.get("QUACKD_OPENAI_API") or "chat")
            .strip()
            .lower()
        )
        if base_url is not None:
            self.base_url = base_url
        # The catalogue knows which models take an image. A model that does not gets the
        # detections as text instead of a 400, the way a local model already does.
        if vision is not None:
            self.supports_vision = vision
        elif spec is not None:
            self.supports_vision = spec.vision
        self.tool_choice = tool_choice if tool_choice is not None else self.default_tool_choice
        if client is None:
            import os

            key = api_key or os.environ.get(self.key_env) or self._fallback_key()
            if not key:
                raise ProviderMissingKey(self.name, self.key_env)
            try:
                from openai import AsyncOpenAI
            except ImportError as e:
                raise ProviderNotInstalled(self.name, self.extra) from e
            sent = {"default_headers": dict(self.default_headers)} if self.default_headers else {}
            client = AsyncOpenAI(api_key=key, base_url=self.base_url, **sent)
        self.client = client

    def _fallback_key(self) -> str | None:
        """What to use when no key is configured. Cloud: nothing (error). Local: a dummy."""
        return None

    def _params(
        self, system: str, history: list[Exchange], tools: list[dict[str, Any]]
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "model": self.model,
            "messages": render_messages(system, history, assistant_fields=self._assistant_fields),
            "tools": render_tools(tools),
        }
        if self.tool_choice and self.tool_choice != "none":
            params["tool_choice"] = self.tool_choice
        if self.send_parallel_flag:
            params["parallel_tool_calls"] = False
        if self.reasoning_effort:
            params["reasoning_effort"] = self.reasoning_effort
        if self.extra_body:
            params["extra_body"] = self.extra_body
        return params

    def _params_responses(
        self, system: str, history: list[Exchange], tools: list[dict[str, Any]]
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "model": self.model,
            "instructions": system,
            "input": render_input(history),
            "tools": render_tools_responses(tools),
        }
        if self.tool_choice and self.tool_choice != "none":
            params["tool_choice"] = self.tool_choice
        if self.send_parallel_flag:
            params["parallel_tool_calls"] = False
        if self.reasoning_effort:
            params["reasoning"] = {"effort": self.reasoning_effort}
        if self.extra_body:
            params["extra_body"] = self.extra_body
        return params

    async def _call(self, system: str, history: list[Exchange], tools: list[dict[str, Any]]):
        """One request on whichever API this provider is currently using."""
        if self.api == "responses":
            response = await self.client.responses.create(
                **self._params_responses(system, history, tools)
            )
            return self._normalise(parse_responses(response))
        response = await self.client.chat.completions.create(**self._params(system, history, tools))
        return self._normalise(self._parse_chat(response))

    def _assistant_fields(self, decision: Decision) -> Mapping[str, Any]:
        """Hook: a vendor's own keys on a replayed assistant message. Base: none."""
        return {}

    def _parse_chat(self, response: Any) -> ProviderTurn:
        """Hook: read one Chat Completions response. Runs inside `step`'s try, so whatever a
        subclass raises here still reaches the loop as a ProviderError. Base: `parse_response`."""
        return parse_response(response)

    def _normalise(self, turn: ProviderTurn) -> ProviderTurn:
        """Hook: tidy a parsed turn before anything reads its text. Base: nothing."""
        return turn

    def _fallback(self, turn: ProviderTurn, tools: list[dict[str, Any]]) -> ProviderTurn:
        """Hook for providers that can rescue a tool call from plain text. Base: nothing."""
        return turn

    async def step(
        self, system: str, history: list[Exchange], tools: list[dict[str, Any]]
    ) -> ProviderTurn:
        self.calls += 1
        # parse_response is inside the try on purpose: a gateway that answers a content
        # filter with `choices: []`, or a usage field that is not a number, would otherwise
        # escape the provider as a raw traceback — the CLI only catches TransportError and
        # ProviderError. The fallback stays outside: it is quackd's code, not the SDK's.
        try:
            turn = await self._call(system, history, tools)
        except ProviderError:
            raise
        except Exception as e:
            # A model that will not take function tools on Chat Completions. Every verb quackd
            # has is a function tool, so this is not a degraded path, it is no path: take the
            # API at its word, move to Responses, and stay there rather than paying a failed
            # call every turn. Only once, and only from chat, so a genuine Responses failure
            # still surfaces instead of looping.
            if self.switches_api and self.api != "responses" and _wants_the_responses_api(e):
                self.api = "responses"
                try:
                    turn = await self._call(system, history, tools)
                except ProviderError:
                    raise
                except Exception as retry:
                    raise ProviderError(f"{self.name}: {type(retry).__name__}: {retry}") from retry
            else:
                raise ProviderError(f"{self.name}: {type(e).__name__}: {e}") from e
        if not turn.tool_calls:
            turn = self._fallback(turn, tools)
        return turn
