"""Cohere as the duck's brain, over its OpenAI compatibility endpoint.

Cohere's compatibility layer takes `tools` but documents neither `tool_choice` nor
`parallel_tool_calls` among the parameters it supports, so quackd sends neither. That makes Cohere
the one vendor here that cannot be *told* to call a tool, only asked: a turn that comes back as
prose costs the loop its one re-prompt. It is the vendor's surface, not a setting to tune.

The key is read from `COHERE_API_KEY`, and from `CO_API_KEY` after that, because Cohere's own SDK
and its docs disagree about which one a reader will already have exported (`KEY_FALLBACKS`).
"""

from __future__ import annotations

import os

from quackd.agent.providers.catalogue import KEY_FALLBACKS
from quackd.agent.providers.openai import OpenAIProvider


class CohereProvider(OpenAIProvider):
    name = "cohere"
    key_env = "COHERE_API_KEY"
    extra = "cohere"
    base_url = "https://api.cohere.ai/compatibility/v1"
    default_tool_choice = None
    send_parallel_flag = False

    def _fallback_key(self) -> str | None:
        return os.environ.get(KEY_FALLBACKS[self.name])
