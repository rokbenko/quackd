"""Kimi as the duck's brain, over Moonshot's OpenAI-compatible endpoint.

Moonshot's Tool Choice guide documents `auto`, `none`, `required` and a named function, and
says a named function is refused whenever thinking is on. Its Model Parameter Reference says
`required` works on K3 alone, and that thinking is always on for K3 and K2.7 Code and on by
default for K2.6. So quackd sends `auto`, the one value no Kimi LLM refuses, and not
`parallel_tool_calls`. Moonshot says the LLM may return several calls at once, which the loop
already handles by taking the first.
"""

from __future__ import annotations

from quackd.agent.providers.openai import OpenAIProvider


class KimiProvider(OpenAIProvider):
    name = "kimi"
    key_env = "MOONSHOT_API_KEY"
    extra = "kimi"
    base_url = "https://api.moonshot.ai/v1"
    default_tool_choice = "auto"
    send_parallel_flag = False
