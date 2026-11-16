"""GLM as the robot's brain, over Z.ai's OpenAI-compatible endpoint.

Z.ai's function-calling page is explicit where the others are silent: `tool_choice` defaults to
`auto` and `auto` is the only value it supports. So `auto` is not a cautious guess here, it is the
whole vocabulary, and Roboglot asks for a tool call rather than requiring one.
"""

from __future__ import annotations

from roboglot.agent.providers.openai import OpenAIProvider


class GLMProvider(OpenAIProvider):
    name = "glm"
    key_env = "ZAI_API_KEY"
    extra = "glm"
    base_url = "https://api.z.ai/api/paas/v4"
    default_tool_choice = "auto"
    send_parallel_flag = False
