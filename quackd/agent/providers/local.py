"""Local and open-source models as the duck's brain: llama.cpp, vLLM, Ollama, LM Studio.

They all speak OpenAI's Chat Completions format, so this is the OpenAI provider with the
knobs turned for servers that are pickier and models that are weaker at tool calling:
no key needed, a preset base URL per server, `tool_choice="auto"` and no
`parallel_tool_calls` field, vision off unless asked, model discovery from `/v1/models`,
and a text fallback that rescues a tool call a small model wrote as plain JSON.

A preset's address can move to another machine with `--host` (`on_host`), which is how a
model served on a Jetson's GPU is reached from the laptop without typing its URL.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from quackd.agent.providers.base import ProviderError, ProviderTurn, ToolCall
from quackd.agent.providers.openai import OpenAIProvider
from quackd.host import HOST_ENV, host_of, netloc

PRESETS: dict[str, str | None] = {
    "local": None,  # needs --base-url or QUACKD_BASE_URL
    "ollama": "http://localhost:11434/v1",
    "vllm": "http://localhost:8000/v1",
    "llamacpp": "http://localhost:8080/v1",
    "lmstudio": "http://localhost:1234/v1",
}
LOCAL_NAMES = tuple(PRESETS)

TOOL_HINT = (
    "\n## If you cannot call tools natively\n"
    "Reply with exactly one JSON object and nothing else, in this shape:\n"
    '{"name": "<verb>", "arguments": {<parameters>}}\n'
)

_LOCAL_NEEDS_A_URL = (
    "--llm local needs the server address: --base-url http://host:port/v1 "
    "(or QUACKD_BASE_URL). Or use a preset: --llm ollama, vllm, llamacpp, lmstudio."
)
_A_HOST_MOVES_ONLY_A_PRESET = (
    "--llm local has no preset address, and a host only moves a preset's: give the "
    "server's own with --base-url http://host:port/v1 (or QUACKD_BASE_URL), or use a "
    "preset: --llm ollama, vllm, llamacpp, lmstudio."
)

_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.S)
_THINK_RE = re.compile(r"<think>(.*?)</think>", re.S)
_NAME_KEYS = ("name", "tool", "function", "verb")
_ARGS_KEYS = ("arguments", "parameters", "params", "args", "input")


def on_host(url: str, host: str) -> str:
    """A preset's address on another machine: its `localhost` replaced by `host`, and the
    scheme, the port and the path kept. `on_host("http://localhost:11434/v1", "jetson.local")`
    is `http://jetson.local:11434/v1`.

    `host` is anything `--host` takes: the machine alone, `machine:port`, or `[v6]:port`.
    The port in it belongs to quackd's daemon on the board, never to the model server, so it
    is dropped: Ollama on a Jetson listens on 11434 whatever port the daemon was given. An
    IPv6 address comes back in brackets, the only way a URL can carry one. A userinfo in
    `url` is kept, though no preset has one.

    Raises ValueError in `parse_host`'s words for anything that is not a machine."""
    parts = urlsplit(url)
    machine = host_of(host)
    userinfo, at, _ = parts.netloc.rpartition("@")
    where = (
        netloc(machine, parts.port)
        if parts.port is not None
        else (f"[{machine}]" if ":" in machine else machine)
    )
    return urlunsplit(parts._replace(netloc=f"{userinfo}{at}{where}"))


def _moved(url: str, host: str, *, source: str) -> str:
    """`on_host` with its refusal as a ProviderError, which is what every caller of a
    provider's constructor catches and prints as one line. `parse_host`'s sentences all say
    `--host`, so a host from anywhere else gets its own name in front."""
    try:
        return on_host(url, host)
    except ValueError as e:
        raise ProviderError(str(e) if source == "--host" else f"{source}: {e}") from e


def split_thinking(text: str) -> tuple[str | None, str]:
    """`<think>...</think>` out of a reply, as (thinking, the rest).

    A server that does not separate reasoning (llama.cpp without `--reasoning-format`, LM
    Studio with separation off, vLLM without a parser) leaves the model's thoughts inline in
    `content`. Left there they would be shown as the answer and replayed to the model next
    turn as something it said. An opening tag with no closing one is thinking to the end of
    the reply: a max_tokens cut mid-thought must not turn a half-finished thought into the
    answer."""
    thoughts = [m.group(1).strip() for m in _THINK_RE.finditer(text)]
    rest = _THINK_RE.sub("", text)
    if (cut := rest.find("<think>")) != -1:
        thoughts.append(rest[cut + len("<think>") :].strip())
        rest = rest[:cut]
    return "\n\n".join(t for t in thoughts if t) or None, rest.strip()


def _candidates(text: str) -> list[dict[str, Any]]:
    """Every JSON object we can find in the text: fenced blocks first, then bare ones."""
    found: list[dict[str, Any]] = []
    for m in _FENCE_RE.finditer(text):
        try:
            obj = json.loads(m.group(1))
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            found.append(obj)
    decoder = json.JSONDecoder()
    for i, ch in enumerate(text):
        if ch != "{":
            continue
        try:
            obj, _end = decoder.raw_decode(text, i)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and obj not in found:
            found.append(obj)
    return found


def _as_call(obj: dict[str, Any], tool_names: set[str]) -> ToolCall | None:
    fn = obj.get("function")
    if isinstance(fn, dict):  # OpenAI style {"function": {"name": .., "arguments": ..}}
        obj = {**obj, **fn}
    name = next((obj[k] for k in _NAME_KEYS if isinstance(obj.get(k), str)), None)
    if name is None or name not in tool_names:
        return None
    args: Any = next((obj[k] for k in _ARGS_KEYS if k in obj), {})
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except json.JSONDecodeError:
            args = {}
    if not isinstance(args, dict):
        args = {}
    return ToolCall(name=name, arguments=args)


def parse_tool_call_from_text(text: str, tool_names: set[str]) -> ToolCall | None:
    """A tool call a model wrote as JSON text instead of a native tool call, or None."""
    for obj in _candidates(text or ""):
        call = _as_call(obj, tool_names)
        if call is not None:
            return call
    return None


class LocalProvider(OpenAIProvider):
    """A model on a server you run: one of the four presets, or `local` at any address.

    Where the server is, the first rung that is set wins, and nothing is probed:

    1. `base_url`, which is `--base-url`: a URL given for this run is used exactly as given.
    2. `host`, which is `--host` or the host a registered robot was stored with: the preset's
       address moved to that machine (`on_host`), port and path kept.
    3. `QUACKD_BASE_URL`, then 4. `OPENAI_BASE_URL`, each used exactly as given.
    5. `QUACKD_HOST`: the preset's address moved to that machine.
    6. The preset's own address, on localhost.

    A URL given anywhere is used as given, and a host only ever moves a preset. That is why
    `local`, which has no preset address, refuses a host on its own and asks for
    `--base-url`. The two hosts sit on different rungs on purpose. One typed for this run or
    registered with this robot is a decision about this run, and beats a `.env` line naming a
    model server's URL; `QUACKD_HOST` is the board you usually use, and must not.

    The key is `--api-key`, then `LOCAL_API_KEY`, then the word `not-needed`, and never
    `OPENAI_API_KEY`, which is a key to OpenAI's API and no business of a server you run, on a
    board over plain HTTP least of all. A server that wants a key is given it as
    `LOCAL_API_KEY`.
    """

    supports_vision = False
    key_env = "LOCAL_API_KEY"
    default_tool_choice = "auto"
    send_parallel_flag = False
    prompt_hint = TOOL_HINT

    def __init__(
        self,
        model: str | None = None,
        *,
        preset: str = "local",
        base_url: str | None = None,
        host: str | None = None,
        client: Any = None,
        api_key: str | None = None,
        tool_choice: str | None = None,
        vision: bool | None = None,
        extra_body: dict[str, Any] | None = None,
    ) -> None:
        if preset not in PRESETS:
            raise ProviderError(f"unknown local preset {preset!r}; one of {', '.join(LOCAL_NAMES)}")
        self.name = preset
        self.preset = preset
        url = self._address(PRESETS[preset], base_url, host)
        choice = tool_choice or os.environ.get("QUACKD_TOOL_CHOICE") or "auto"
        if vision is None:
            vision = os.environ.get("QUACKD_VISION", "0").lower() not in ("", "0", "false", "no")
        super().__init__(
            model or "",
            client=client,
            api_key=api_key,
            base_url=url,
            tool_choice=choice,
            vision=vision,
            extra_body=extra_body,
        )
        self.text_fallbacks = 0

    @staticmethod
    def _address(preset: str | None, base_url: str | None, host: str | None) -> str:
        """The class docstring's ladder, rung by rung. Blank is absent at every rung, as it is
        everywhere quackd reads a setting: `QUACKD_HOST=` in a `.env` is a shell saying unset."""
        host = (host or "").strip() or None
        env_host = (os.environ.get(HOST_ENV) or "").strip() or None
        if base_url:
            return base_url
        if host and preset:
            return _moved(preset, host, source="--host")
        url = os.environ.get("QUACKD_BASE_URL") or os.environ.get("OPENAI_BASE_URL")
        if url:
            return url
        if env_host and preset:
            return _moved(preset, env_host, source=HOST_ENV)
        if preset:
            return preset
        raise ProviderError(_A_HOST_MOVES_ONLY_A_PRESET if host or env_host else _LOCAL_NEEDS_A_URL)

    def _fallback_key(self) -> str | None:
        """The SDK insists on a key, and a local server ignores it, so a placeholder. Never
        `OPENAI_API_KEY`, which used to go here and so reached every local server, a board over
        plain HTTP included, whenever it sat in the same `.env`."""
        return "not-needed"

    async def ensure_model(self) -> str:
        """Discover the served model when none was given (`/v1/models`, first entry)."""
        if self.model:
            return self.model
        try:
            page = await self.client.models.list()
        except Exception as e:
            raise ProviderError(
                f"{self.name}: cannot list models at {self.base_url} ({type(e).__name__}: {e}). "
                "Is the server running? Pass --llm <preset>:<model> to skip discovery."
            ) from e
        ids = [str(getattr(m, "id", "")) for m in (getattr(page, "data", None) or [])]
        ids = [i for i in ids if i]
        if not ids:
            raise ProviderError(
                f"{self.name}: the server at {self.base_url} lists no models. "
                "Load one (e.g. `ollama pull qwen3:8b`) or pass --llm <preset>:<model>."
            )
        self.model = ids[0]
        return self.model

    async def step(
        self, system: str, history: list[Any], tools: list[dict[str, Any]]
    ) -> ProviderTurn:
        await self.ensure_model()
        return await super().step(system, history, tools)

    def _normalise(self, turn: ProviderTurn) -> ProviderTurn:
        """Split inline `<think>` off before anything else reads the text.

        It runs here, not after `super().step()`, because the JSON text fallback reads
        `turn.text`: a model that weighs a verb in its reasoning and then rejects it
        (`<think>maybe {"name": "kick"} but no</think>I will wait.`) would otherwise have
        that call parsed out of the discarded thought and executed.
        """
        if turn.thinking is None and turn.text and "<think>" in turn.text:
            thinking, rest = split_thinking(turn.text)
            turn = turn.model_copy(update={"thinking": thinking, "text": rest or None})
        return turn

    def _fallback(self, turn: ProviderTurn, tools: list[dict[str, Any]]) -> ProviderTurn:
        call = parse_tool_call_from_text(turn.text or "", {t["name"] for t in tools})
        if call is None:
            return turn
        self.text_fallbacks += 1
        call = call.model_copy(update={"id": f"text-{self.text_fallbacks}"})
        return turn.model_copy(update={"tool_calls": [call], "stop_reason": "text_fallback"})
