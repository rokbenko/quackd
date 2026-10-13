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
import contextlib
import json
import os
from collections.abc import Callable, Mapping
from typing import Any
from urllib.parse import urljoin, urlsplit, urlunsplit

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
from quackd.agent.providers.openai_headers import (
    CUSTOM_HEADERS_ENV,
    ambient_headers,
    foreign_authorization,
    foreign_headers,
    is_openai_host,
    is_sdk_own_header,
    own_headers,
    request_headers,
    sdk_parts,
    sdks_own_address,
)
from quackd.command import readable_url, redacted_url

BASE_URL_ENV = "OPENAI_BASE_URL"
"""The SDK's own variable for where its client goes when it is handed no address."""

DEFAULT_BASE_URL = "https://api.openai.com/v1"
"""Where the SDK goes when it is handed no address and `OPENAI_BASE_URL` is unset, in every
release from 1.66.0 to 3.22.1 that the uv cache held (`openai/_client.py`, read 2026-10-11)."""


def _quoted(url: str) -> str:
    """`url` as a refusal may quote it: its password and any credential-named query parameter
    taken out, or not quoted at all where redaction cannot read it, since an address it cannot
    read may still carry a password it could not find."""
    return redacted_url(url) if readable_url(url) else "an address quackd cannot read"


def _where_it_points(location: str, requested: str, address: str) -> tuple[str, str | None]:
    """Where a redirect pointed, as a sentence may quote it, and the address it suggests the
    client be given instead, or None where it suggests none.

    `location` is the redirect's own `Location`, which may be relative, so it is read against
    `requested`, the request it answered. Quoted with its scheme, its host and its path and
    nothing else: no user and password, since a server may hand back a credential nobody typed,
    and no query or fragment. Not quoted at all where it is not plain printable text, because it
    is a server's words on its way to a terminal, or where redaction cannot read it.

    The address suggested is the one the redirect implies for the client as a whole: the part of
    the request's path that the client added to `address`, `/chat/completions` or `/models`, taken
    off the end of the redirect's path, so an `http` server that sends every request on to its
    `https` twin suggests that twin's `/v1`. A redirect whose path does not end that way suggests
    nothing, since nothing then says where the rest of the API lives."""
    unquoted = "an address quackd will not quote"
    try:
        target = urljoin(requested, location)
        if not (target.isascii() and target.isprintable()) or " " in target:
            return unquoted, None
        parts = urlsplit(target)
        if parts.scheme not in ("http", "https") or not readable_url(target):
            return unquoted, None
        authority = parts.netloc.rpartition("@")[2]
        shown = urlunsplit((parts.scheme, authority, parts.path, "", ""))
        prefix = urlsplit(address.strip()).path.rstrip("/")
        asked = urlsplit(requested).path
    except ValueError:
        return unquoted, None
    added = asked[len(prefix) :] if asked.startswith(prefix + "/") else ""
    if not added or not parts.path.endswith(added):
        return shown, None
    return shown, urlunsplit((parts.scheme, authority, parts.path[: -len(added)], "", ""))


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
    """Headers sent with every request beside the SDK's own and quackd's authorization. Empty for
    every vendor but OpenRouter, which credits an app on its public rankings by `HTTP-Referer` and
    `X-OpenRouter-Title`. They go whatever `OPENAI_CUSTOM_HEADERS` says about the same names,
    because they are this vendor's and the variable's lines are somebody else's: every spelling
    the variable gives one carries this vendor's value (`openai_headers.request_headers`). That
    holds everywhere but the SDK's own address, which a vendor reaches only where `--base-url`
    names OpenAI's own API, and where the variable's lines go as the SDK sends them."""
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
        # Blank is absent, as it is everywhere quackd reads a setting, so `--base-url ""` leaves a
        # vendor at its own address and OpenAI's own provider at the one the SDK would find. It used
        # to replace the vendor's address with nothing, which the SDK kept as the address, so every
        # turn failed (`client_address`).
        if base_url is not None and base_url.strip():
            self.base_url = base_url.strip()
        # The catalogue knows which models take an image. A model that does not gets the
        # detections as text instead of a 400, the way a local model already does.
        if vision is not None:
            self.supports_vision = vision
        elif spec is not None:
            self.supports_vision = spec.vision
        self.tool_choice = tool_choice if tool_choice is not None else self.default_tool_choice
        if client is None:
            key = api_key or os.environ.get(self.key_env) or self._fallback_key()
            if not key:
                raise ProviderMissingKey(self.name, self.key_env)
            client = self._build_client(key)
        self.client = client

    def _fallback_key(self) -> str | None:
        """What to use when no key is configured. Cloud: nothing (error). Local: a dummy."""
        return None

    def client_address(self) -> str:
        """The address the client is handed, which is where every request goes.

        The provider's own, from `--base-url`, the vendor's class or a local preset's ladder.
        Where it has none, which is OpenAI's own provider without `--base-url` and no other, the
        address the SDK would have found for itself: `OPENAI_BASE_URL`, then OpenAI's API
        (`DEFAULT_BASE_URL`). Found here and handed over rather than left to the SDK, because what
        the client is built with is decided on this address (`openai_headers.sdks_own_address`),
        and an address the SDK went looking for after that was decided is an address nobody
        judged. Blank is absent at every step, as it is everywhere quackd reads a setting:
        `OPENAI_BASE_URL=` in a `.env` is a shell saying unset, where the SDK would have taken the
        empty string for an address and failed on the first turn.

        Only a provider with no address at all falls through to the SDK's. A vendor or a local
        preset always has one, since a blank `--base-url` is never stored over it (`__init__`), and
        one that somehow came to hold a blank is refused, where the SDK would keep the blank as the
        address and fail every turn on it, which is what a blank used to do."""
        if self.base_url is None:
            return (os.environ.get(BASE_URL_ENV) or "").strip() or DEFAULT_BASE_URL
        given = self.base_url.strip()
        if not given:
            raise ProviderError(
                f"{self.name}: no address to send to: give the server's own with --base-url"
            )
        return given

    def takes_the_sdks_address(self) -> bool:
        """Whether this provider's address is the one the SDK would have found for itself, which
        is true of OpenAI's own provider without `--base-url` and of no other: every vendor has an
        address of its own, and every local preset one from its own ladder, `OPENAI_BASE_URL`
        among its rungs (`openai_headers` says why that rung is quackd's and not the SDK's)."""
        return self.base_url is None

    def _build_client(self, key: str) -> Any:
        """The SDK's client, with the key, at `client_address`, sending what the environment put
        there only where the environment meant it to go.

        The SDK adds `OPENAI_CUSTOM_HEADERS`, `OPENAI_ORG_ID` and `OPENAI_PROJECT_ID` to every
        request by itself, wherever the client points, and a gateway's `Authorization` line in the
        first replaced this vendor's key on every request in 3.22.1. Where they may go is
        `openai_headers.sdks_own_address`: OpenAI's own API, and the address `OPENAI_BASE_URL` gives
        this provider when it was asked for none of its own. A client bound there is built as the
        SDK builds one by itself, handed no header of quackd's but this vendor's own
        `default_headers`, so the three variables go exactly as the SDK sends them, a gateway's
        `Authorization` line over the key included. Anywhere else it is handed quackd's own answer
        to each (`openai_headers.request_headers`): this provider's key as the one authorization,
        under every spelling the variable gives one, and none of the rest, except that a line
        naming one of the headers the SDK sends by itself carries the SDK's own value, read off a
        client of the same release (`openai_headers.own_headers`), and a line naming one of this
        vendor's own `default_headers` carries this vendor's value, so neither is taken off with
        the line.

        It is built on the SDK's HTTP client told to follow no redirect. Followed, a redirect from
        anything that answers for the address sends the request on to wherever it points, plain
        http included, with every header the client was built with but the authorization, which the
        HTTP client takes off a request that leaves for another host: at OpenAI the account headers
        and the variable's other lines, and on a 307 or a 308 the whole body besides, the task, the
        robot's state and its camera frames. A turn answered with a redirect is a turn that failed,
        and its error says where the redirect pointed (`_redirected`). The request option that says
        the same is not in 1.66.0, which drops an option it does not know without a word, so it is
        said where every release reads it.

        Once built, the client is read back. Away from the SDK's own address, an authorization it
        would still send that quackd did not write stops the build, naming the release and the
        variable and never the value, which is the credential this exists to keep at home, and so
        does any other value it would send under a name quackd answered. Everywhere, so does an
        address the client reads on the other side of OpenAI's own API from the one it was handed,
        and so does a client whose headers or address cannot be read at all, since nothing then
        says what it would send: `openai_headers` answers the releases somebody read, and the
        extras allow ones nobody has.
        """
        sdk = sdk_parts()
        if sdk is None:
            raise ProviderNotInstalled(self.name, self.extra)
        address = self.client_address()
        http_client = sdk.http_client(follow_redirects=False)
        if sdks_own_address(address, from_its_variable=self.takes_the_sdks_address()):
            client = sdk.client(
                api_key=key,
                base_url=address,
                http_client=http_client,
                **({"default_headers": dict(self.default_headers)} if self.default_headers else {}),
            )
            self._read_back(client, sdk, address)
            return client
        bearer = f"Bearer {key}"
        own: dict[str, str] = {}
        if any(is_sdk_own_header(n) for n in ambient_headers(os.environ.get(CUSTOM_HEADERS_ENV))):
            # Read off a client built as the SDK builds one, which sends nothing and is dropped:
            # the values are the installed release's own, and neither depends on the environment.
            own = own_headers(sdk.client(api_key=key, base_url=address, http_client=http_client))
        # Never OpenAI's own API here, since that is the SDK's own address, so both account
        # headers are always among the names answered.
        handed = request_headers(
            bearer, at_openai=False, omit=sdk.omit, own=own, vendor=self.default_headers
        )
        client = sdk.client(
            api_key=key, base_url=address, default_headers=handed, http_client=http_client
        )
        built = self._read_back(client, sdk, address, headers=True)
        if foreign_authorization(built, bearer, sdk.omit):
            raise ProviderError(
                f"{self.name}: openai {sdk.version} would send an authorization header from "
                f"{CUSTOM_HEADERS_ENV} in place of or beside the key quackd sends, so the client "
                f"is not built: take that line out of {CUSTOM_HEADERS_ENV} for this run, or "
                f'upgrade the SDK (uv pip install -U "quackd[{self.extra}]")'
            )
        if foreign_headers(built, handed, sdk.omit):
            raise ProviderError(
                f"{self.name}: openai {sdk.version} would send a line of {CUSTOM_HEADERS_ENV}, or "
                f"OPENAI_ORG_ID or OPENAI_PROJECT_ID, to {_quoted(address)}, which none of them is "
                "for, so the client is not built: take them out of the environment for this run, "
                f'or upgrade the SDK (uv pip install -U "quackd[{self.extra}]")'
            )
        return client

    def _read_back(
        self, client: Any, sdk: Any, address: str, *, headers: bool = False
    ) -> dict[str, Any]:
        """What a built client says it will do: its default headers where `headers` asks for
        them, and its address, which has to be on the same side of OpenAI's own API as `address`,
        the one it was handed and everything it was built with was decided on. A release that
        would send somewhere else would send what was decided for one address to another: OpenAI's
        account headers and a gateway's lines away from OpenAI, or OpenAI's own requests without
        them. A client that cannot say either is not used, and the refusal names what it raised by
        its type alone, which may quote what it was reading."""
        try:
            built = dict(client.default_headers) if headers else {}
            used = str(client.base_url)
        except Exception as e:
            raise ProviderError(
                f"{self.name}: openai {sdk.version} built a client whose headers and address "
                f"could not be read back ({type(e).__name__}), so it is not used"
            ) from e
        if is_openai_host(used) != is_openai_host(address):
            raise ProviderError(
                f"{self.name}: openai {sdk.version} would send to {_quoted(used)} rather than "
                f"{_quoted(address)}, the address its headers were decided on, so the client is "
                "not built"
            )
        return built

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

    def _redirected(self, error: Exception) -> ProviderError | None:
        """The sentence for a request the address answered with a redirect, or None for any other
        failure.

        The client follows no redirect (`_build_client`), so the SDK raises the 3xx as it raises a
        4xx, and its own words, `Error code: 308`, name neither a redirect nor where it pointed.
        This says both, in the words quackd's host daemon client uses for the same thing, and where
        the redirect implies another address for the whole API, which is what an `http` server
        that sends everything on to `https` does, it names it and the way to give it: `--base-url`,
        or `OPENAI_BASE_URL` for a provider that took its address from there. The client is not
        moved there for you, because the point of following none is that a run's requests go only
        where somebody chose."""
        status = getattr(error, "status_code", None)
        if not isinstance(status, int) or isinstance(status, bool) or not 300 <= status < 400:
            return None
        response: Any = getattr(error, "response", None)
        location: Any = None
        requested = ""
        with contextlib.suppress(Exception):
            location = response.headers.get("location")
        with contextlib.suppress(Exception):
            requested = str(response.request.url)
        address = self.client_address()
        here = f"{self.name}: {_quoted(address)} answered with a redirect ({status})"
        if not isinstance(location, str) or not location.strip():
            return ProviderError(f"{here} that names no address, so it was not followed")
        shown, suggested = _where_it_points(location.strip(), requested or address, address)
        if suggested is None:
            return ProviderError(f"{here} to {shown}, so it was not followed")
        give = (
            f"set {BASE_URL_ENV}={suggested}"
            if self.takes_the_sdks_address()
            else f"pass --base-url {suggested}"
        )
        return ProviderError(
            f"{here} to {shown}, so it was not followed: if the server is there now, {give}"
        )

    def _failed(self, error: Exception) -> ProviderError:
        """What a turn the SDK raised on becomes: the redirect's own sentence for a redirect, and
        the SDK's error under this provider's name for everything else."""
        return self._redirected(error) or ProviderError(
            f"{self.name}: {type(error).__name__}: {error}"
        )

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
                    raise self._failed(retry) from retry
            else:
                raise self._failed(e) from e
        if not turn.tool_calls:
            turn = self._fallback(turn, tools)
        return turn
