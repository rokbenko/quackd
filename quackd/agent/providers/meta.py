"""Meta as the duck's brain, over the Meta Model API.

Not Llama. Meta retired the hosted Llama API in July 2026 and what stands at
`https://api.meta.ai/v1` now serves Muse Spark, drop-in compatible with the OpenAI SDK. The
catalogue carries the model names, including the contributor-tier ones, whose labels say plainly
that the discount is paid for by letting Meta train on what you send.

`tool_choice` is left at `auto`, the only value Meta's tool-calling page accepts (`none`,
`required` and a named choice each return a 400), so the model is allowed a call rather than
made to make one. `parallel_tool_calls=False` is sent, which the same page documents as the way
to get one call per turn; its default is to allow several.

The key is read from `META_API_KEY`, then `MODEL_API_KEY`, which is the name Meta's own examples
export (`KEY_FALLBACKS`).
"""

from __future__ import annotations

import os

from quackd.agent.providers.catalogue import KEY_FALLBACKS
from quackd.agent.providers.openai import OpenAIProvider


class MetaProvider(OpenAIProvider):
    name = "meta"
    key_env = "META_API_KEY"
    extra = "meta"
    base_url = "https://api.meta.ai/v1"
    default_tool_choice = "auto"

    def _fallback_key(self) -> str | None:
        return os.environ.get(KEY_FALLBACKS[self.name])
