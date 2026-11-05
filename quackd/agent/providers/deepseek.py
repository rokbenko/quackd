"""DeepSeek as the duck's brain, over its OpenAI-compatible endpoint.

DeepSeek thinks by default, and thinking mode refuses both halves of how quackd asks. Its
request reference says "`required` and named tool choices are not supported in thinking mode;
the API returns a `400` error", and its thinking-mode guide says that a request carrying `tools`
must send every earlier turn's `reasoning_content` back, or it is a 400 too. So quackd turns
thinking off (`thinking: {"type": "disabled"}`, sent through the request body the way
`--extra-body` is, and overridable from there) and asks for its one call with `required`, which
non-thinking mode accepts. Turning thinking back on with `--extra-body` brings both refusals
back, and nothing here replays `reasoning_content`. `parallel_tool_calls` is not in DeepSeek's
request reference, and an unknown field is a 400 on some gateways, so it is not sent: the loop
already takes the first call when an LLM sends more.
"""

from __future__ import annotations

from typing import Any

from quackd.agent.providers.openai import OpenAIProvider

#: What every DeepSeek request carries unless the caller's own body says otherwise.
THINKING_OFF: dict[str, Any] = {"thinking": {"type": "disabled"}}


class DeepSeekProvider(OpenAIProvider):
    name = "deepseek"
    key_env = "DEEPSEEK_API_KEY"
    extra = "deepseek"
    base_url = "https://api.deepseek.com"
    send_parallel_flag = False

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        # Underneath whatever the caller sent rather than instead of it, so `--extra-body`
        # can still turn thinking on, and every other key it carries goes through untouched.
        self.extra_body = {**THINKING_OFF, **(getattr(self, "extra_body", None) or {})}
