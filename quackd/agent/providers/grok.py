"""Grok as the duck's brain: xAI's OpenAI-compatible endpoint, so it is the OpenAI provider with
a different base URL, key and LLM list. Nothing else changes.

xAI documents both `tool_choice="required"` and `parallel_tool_calls`, so the strict settings the
base class uses for OpenAI are right here too, and quackd gets its one call per turn asked for
rather than hoped for.
"""

from __future__ import annotations

from quackd.agent.providers.openai import OpenAIProvider


class GrokProvider(OpenAIProvider):
    name = "grok"
    key_env = "XAI_API_KEY"
    extra = "grok"
    base_url = "https://api.x.ai/v1"
