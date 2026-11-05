"""Mistral as the duck's brain, over its OpenAI-compatible endpoint.

One difference worth the file: Mistral's function-calling guide documents `any` as its word for
"you must call a tool", so that is what quackd sends, although its API spec lists OpenAI's
`required` as well. Sending nothing is an LLM free to answer in prose, which for quackd is a
turn thrown away.
`parallel_tool_calls` it does accept, so that stays.
"""

from __future__ import annotations

from quackd.agent.providers.openai import OpenAIProvider


class MistralProvider(OpenAIProvider):
    name = "mistral"
    key_env = "MISTRAL_API_KEY"
    extra = "mistral"
    base_url = "https://api.mistral.ai/v1"
    default_tool_choice = "any"
