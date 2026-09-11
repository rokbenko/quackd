"""Gemini as the duck's brain, via `google-genai` (optional extra).

Function calling with `mode="ANY"` so a turn always yields a call; images ride as inline
PNG parts; tool results go back as `function_response` parts. Gemini's schema dialect
rejects a few JSON-Schema keywords, so `render_tools` strips them. Thought summaries are
asked for (`thinking_config.include_thoughts`) so the trace can show them; a model that
rejects the field gets one retry without it, and `QUACKD_GEMINI_THOUGHTS=0` never asks.
"""

from __future__ import annotations

import base64
import os
from typing import Any

from quackd.agent.providers.base import (
    Exchange,
    ProviderError,
    ProviderMissingKey,
    ProviderNotInstalled,
    ProviderTurn,
    ToolCall,
    Usage,
)

DEFAULT_MODEL = "gemini-pro-latest"
"""An alias, on purpose: `gemini-2.5-pro` went "no longer available to new users" within
weeks of being the default here, and a default that 404s is worse than one that moves."""
UNSUPPORTED_SCHEMA_KEYS = {
    "additionalProperties",
    "title",
    "default",
    "$schema",
    "$id",
    # pydantic writes `gt=0` as exclusiveMinimum; google-genai >= 2 validates the schema and
    # refuses the keyword outright. The executor still enforces the bound on the way in.
    "exclusiveMinimum",
    "exclusiveMaximum",
}


def clean_schema(schema: Any) -> Any:
    """Drop keywords Gemini's function-declaration schema does not accept."""
    if isinstance(schema, dict):
        return {k: clean_schema(v) for k, v in schema.items() if k not in UNSUPPORTED_SCHEMA_KEYS}
    if isinstance(schema, list):
        return [clean_schema(v) for v in schema]
    return schema


def render_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One `Tool` with all function declarations, as plain dicts (the SDK accepts dicts)."""
    return [
        {
            "function_declarations": [
                {
                    "name": t["name"],
                    "description": t["description"],
                    "parameters": clean_schema(t["input_schema"]),
                }
                for t in tools
            ]
        }
    ]


def render_contents(history: list[Exchange]) -> list[dict[str, Any]]:
    contents: list[dict[str, Any]] = []
    for ex in history:
        obs = ex.observation
        parts: list[dict[str, Any]] = []
        if obs.tool_call_id and ex is not history[0]:
            prev = _previous_decision(history, ex)
            if prev is not None:
                parts.append(
                    {
                        "function_response": {
                            "name": prev.tool_call.name,
                            "response": {"result": obs.text},
                        }
                    }
                )
            else:
                parts.append({"text": obs.text})
        else:
            parts.append({"text": obs.text})
        if obs.image_png:
            parts.append({"inline_data": {"mime_type": "image/png", "data": obs.image_png}})
        contents.append({"role": "user", "parts": parts})
        if ex.decision is not None:
            tc = ex.decision.tool_call
            model_parts: list[dict[str, Any]] = []
            if ex.decision.text:
                model_parts.append({"text": ex.decision.text})
            call: dict[str, Any] = {"function_call": {"name": tc.name, "args": tc.arguments}}
            if tc.signature:
                call["thought_signature"] = base64.b64decode(tc.signature)
            model_parts.append(call)
            contents.append({"role": "model", "parts": model_parts})
    return contents


def _previous_decision(history: list[Exchange], current: Exchange) -> Any:
    idx = history.index(current)
    for ex in reversed(history[:idx]):
        if ex.decision is not None:
            return ex.decision
    return None


def parse_response(response: Any) -> ProviderTurn:
    tool_calls: list[ToolCall] = []
    texts: list[str] = []
    thoughts: list[str] = []
    candidates = getattr(response, "candidates", None) or []
    parts = (
        getattr(getattr(candidates[0], "content", None), "parts", None) or [] if candidates else []
    )
    for i, part in enumerate(parts):
        fc = getattr(part, "function_call", None)
        if fc is not None and getattr(fc, "name", None):
            args = dict(getattr(fc, "args", None) or {})
            # Gemini 3 signs the call; the signature rides on the part, not on the call, and
            # the next request is refused unless it comes back on the same function_call
            sig = getattr(part, "thought_signature", None)
            tool_calls.append(
                ToolCall(
                    id=f"gemini-{i}",
                    name=str(fc.name),
                    arguments=args,
                    signature=base64.b64encode(sig).decode() if sig else "",
                )
            )
        elif getattr(part, "text", None):
            # a thought part is the model's reasoning, not its answer: kept out of `text`, or
            # it would be shown as the reply and replayed to the model as something it said
            if getattr(part, "thought", False) is True:
                thoughts.append(str(part.text).strip())
            else:
                texts.append(part.text)
    meta = getattr(response, "usage_metadata", None)
    finish = getattr(candidates[0], "finish_reason", None) if candidates else None
    return ProviderTurn(
        tool_calls=tool_calls,
        text="\n".join(texts) or None,
        usage=Usage(
            input_tokens=int(getattr(meta, "prompt_token_count", 0) or 0),
            output_tokens=int(getattr(meta, "candidates_token_count", 0) or 0),
            reasoning_tokens=int(getattr(meta, "thoughts_token_count", 0) or 0),
        ),
        stop_reason=str(finish) if finish is not None else None,
        raw=None,
        thinking="\n\n".join(t for t in thoughts if t) or None,
    )


def _rejects_thoughts(e: Exception) -> bool:
    """The API's INVALID_ARGUMENT for a model without thinking names the config it refused.

    Both halves are needed. `APIError` carries `.code`/`.status` and stringifies as
    `400 INVALID_ARGUMENT. {details}`; without that check, a 429 or a 503 whose text merely
    mentions thinking would trigger a second (billed) request that fails the same way, and
    would turn thought summaries off for the rest of the run.
    """
    text = str(e).lower()
    invalid_argument = (
        getattr(e, "code", None) == 400
        or getattr(e, "status", None) == "INVALID_ARGUMENT"
        or "invalid_argument" in text
    )
    return invalid_argument and "thinking" in text


class GeminiProvider:
    name = "gemini"
    supports_vision = True

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        *,
        client: Any = None,
        api_key: str | None = None,
        include_thoughts: bool | None = None,
    ) -> None:
        self.model = model
        self.calls = 0
        if include_thoughts is None:
            include_thoughts = os.environ.get(
                "QUACKD_GEMINI_THOUGHTS", "1"
            ).strip().lower() not in (
                "0",
                "false",
                "no",
                "off",
            )
        self.include_thoughts = include_thoughts
        if client is None:
            key = api_key or os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
            if not key:
                raise ProviderMissingKey("gemini", "GEMINI_API_KEY")
            try:
                from google import genai
            except ImportError as e:
                raise ProviderNotInstalled("gemini", "gemini") from e
            client = genai.Client(api_key=key)
        self.client = client

    def _config(self, system: str, tools: list[dict[str, Any]]) -> dict[str, Any]:
        config: dict[str, Any] = {
            "system_instruction": system,
            "tools": render_tools(tools),
            "tool_config": {"function_calling_config": {"mode": "ANY"}},
        }
        if self.include_thoughts:
            config["thinking_config"] = {"include_thoughts": True}
        return config

    async def _generate(
        self, system: str, history: list[Exchange], tools: list[dict[str, Any]]
    ) -> Any:
        return await self.client.aio.models.generate_content(
            model=self.model,
            contents=render_contents(history),
            config=self._config(system, tools),
        )

    async def step(
        self, system: str, history: list[Exchange], tools: list[dict[str, Any]]
    ) -> ProviderTurn:
        self.calls += 1
        # parse_response is inside the classifying try on purpose: a response with no
        # candidates, or a usage field that is not a number, would otherwise escape the
        # provider as a raw traceback — the CLI only catches TransportError and ProviderError.
        try:
            try:
                response = await self._generate(system, history, tools)
            except Exception as e:
                if not (self.include_thoughts and _rejects_thoughts(e)):
                    raise
                # a model without thinking (gemini-1.5, gemini-2.0-flash): one retry without
                # the field, and no later turn asks again. The run loses the thoughts, not
                # itself.
                self.include_thoughts = False
                response = await self._generate(system, history, tools)
            return parse_response(response)
        except ProviderError:
            raise
        except Exception as e:
            raise ProviderError(f"gemini: {type(e).__name__}: {e}") from e
