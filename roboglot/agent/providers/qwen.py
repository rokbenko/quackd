"""Qwen as the robot's brain, over Alibaba Cloud Model Studio's OpenAI-compatible endpoint.

The base URL here is Model Studio's international one. Alibaba also issues per-workspace regional
hosts, and a reader on one of those passes `--base-url`, which is why that flag reaches this
provider at all.

`tool_choice` is left at `auto`: Model Studio's function calling guide says "The Qwen series
models do not support setting `tool_choice` to "required"", nor "in thinking mode". Nor is
`parallel_tool_calls` sent, which that guide says defaults to true: a turn with several calls
has its first one run and the rest left, which the loop records (`multiple_tool_calls`).
"""

from __future__ import annotations

from roboglot.agent.providers.openai import OpenAIProvider


class QwenProvider(OpenAIProvider):
    name = "qwen"
    key_env = "DASHSCOPE_API_KEY"
    extra = "qwen"
    base_url = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"
    default_tool_choice = "auto"
    send_parallel_flag = False
