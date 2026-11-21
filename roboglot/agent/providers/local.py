"""Local and open-source LLMs as the robot's brain: llama.cpp, vLLM, Ollama, LM Studio.

They all speak OpenAI's Chat Completions format, so this is the OpenAI provider with the
knobs turned for servers that are pickier and LLMs that are weaker at tool calling:
no key needed, a preset base URL per server, `tool_choice="auto"` and no
`parallel_tool_calls` field, vision off unless asked, LLM discovery from `/v1/models`,
and a text fallback that rescues a tool call a small LLM wrote as plain JSON.

A preset's address can move to another machine with `--host` (`on_host`), which is how an
LLM served on a Jetson's GPU is reached from the laptop without typing its URL.
"""

from __future__ import annotations

import json
import os
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from roboglot.agent.providers.base import ProviderError, ProviderTurn, ToolCall, tool_arguments
from roboglot.agent.providers.openai import OpenAIProvider, _quoted
from roboglot.host import HOST_ENV, host_of, netloc

PRESETS: dict[str, str | None] = {
    "local": None,  # needs --base-url or ROBOGLOT_BASE_URL
    "ollama": "http://localhost:11434/v1",
    "vllm": "http://localhost:8000/v1",
    "llamacpp": "http://localhost:8080/v1",
    "lmstudio": "http://localhost:1234/v1",
}
LOCAL_NAMES = tuple(PRESETS)

_BARE_CALL = "exactly one JSON object and nothing else"
"""What text has to be to count as a call: the rule the hint asks for and the fallback holds
an answer to (`parse_tool_call_from_text`), read by both from here."""
_CALL_SHAPE = '{"name": "<verb>", "arguments": {<parameters>}}'

TOOL_HINT = (
    "\n## If you cannot call tools natively\n"
    f"Reply with {_BARE_CALL}, in this shape:\n"
    f"{_CALL_SHAPE}\n"
)

NOT_A_BARE_CALL = (
    f"Your last answer was not a call. Text is a call only when it is {_BARE_CALL}, "
    f"in this shape: {_CALL_SHAPE}."
)
"""What the LLM is told when its text was read and was no call, in front of the loop's own
re-prompt (`ProviderTurn.not_a_call`): a call quoted in prose, or one of several listed, is not
one, and an LLM that thought it had answered is told what would have been."""

_LOCAL_NEEDS_A_URL = (
    "--llm local needs the server address: --base-url http://host:port/v1 "
    "(or ROBOGLOT_BASE_URL). Or use a preset: --llm ollama, vllm, llamacpp, lmstudio."
)
_A_HOST_MOVES_ONLY_A_PRESET = (
    "--llm local has no preset address, and a host only moves a preset's: give the "
    "server's own with --base-url http://host:port/v1 (or ROBOGLOT_BASE_URL), or use a "
    "preset: --llm ollama, vllm, llamacpp, lmstudio."
)

MAX_CALL_CHARS = 16 * 1024
"""The most text the fallback reads as a call, its fence and the space around it included.

Chosen from what a call can need: the largest Roboglot offers is `assess_task`, and one written
out in full, with a reason of several sentences, an estimate for every quantity it names twice
over, the nine datasheet fields consulted and the nine needs, is about 3.3 kB, or 4.2 kB laid out
with an indent. This is four times that. Anything longer is no call: a local server is sent no
output limit, so a repetition loop runs to the end of its context, and none of that is decoded."""

_FENCE = "```"
_FENCE_LANGUAGE = "json"
_THINK_OPEN = "<think>"
_THINK_CLOSE = "</think>"
_NAME_KEYS = ("name", "tool", "function", "verb")
_ARGS_KEYS = ("arguments", "parameters", "params", "args", "input")


def on_host(url: str, host: str) -> str:
    """A preset's address on another machine: its `localhost` replaced by `host`, and the
    scheme, the port and the path kept. `on_host("http://localhost:11434/v1", "jetson.local")`
    is `http://jetson.local:11434/v1`.

    `host` is anything `--host` takes: the machine alone, `machine:port`, or `[v6]:port`.
    The port in it belongs to Roboglot's daemon on the board, never to the LLM server, so it
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
    Studio with separation off, vLLM without a parser) leaves the LLM's thoughts inline in
    `content`. Left there they would be shown as the answer and replayed to the LLM next
    turn as something it said. An opening tag with no closing one is thinking to the end of
    the reply: a max_tokens cut mid-thought must not turn a half-finished thought into the
    answer.

    Found with plain searches in one pass: each opening, then the first closing after it. A lazy
    pattern looked for a closing from every opening to the end of the reply, so thousands of
    openings never closed cost the square of their number on the event loop's thread. The first
    opening with no closing after it ends the pass, since no later one can have one either."""
    thoughts: list[str] = []
    kept: list[str] = []
    at = 0
    while (opened := _find(text, _THINK_OPEN, at)) != -1:
        inside = opened + len(_THINK_OPEN)
        closed = _find(text, _THINK_CLOSE, inside)
        if closed == -1:
            break
        kept.append(text[at:opened])
        thoughts.append(text[inside:closed].strip())
        at = closed + len(_THINK_CLOSE)
    kept.append(text[at:])
    rest = "".join(kept)
    if (cut := _find(rest, _THINK_OPEN, 0)) != -1:
        thoughts.append(rest[cut + len(_THINK_OPEN) :].strip())
        rest = rest[:cut]
    return "\n\n".join(t for t in thoughts if t) or None, rest.strip()


def _find(text: str, needle: str, start: int) -> int:
    """`str.find`, the one way `split_thinking` reads the reply, so a test can count what it
    read rather than time it."""
    return text.find(needle, start)


def _bare(text: str) -> str | None:
    """The one JSON object an answer is nothing but, as text, or None: the whole answer once
    trimmed, or the inside of one code fence that is the whole answer, its `json` tag allowed.
    Whether that is one object and nothing after it is the decode's to say, which takes nothing
    after the value."""
    body = text.strip()
    if body.startswith(_FENCE) and body.endswith(_FENCE) and len(body) >= 2 * len(_FENCE):
        body = body[len(_FENCE) : -len(_FENCE)]
        if body.startswith(_FENCE_LANGUAGE):
            body = body[len(_FENCE_LANGUAGE) :]
        body = body.strip()
    return body if body.startswith("{") else None


def _as_call(obj: dict[str, Any], tool_names: set[str]) -> ToolCall | None:
    fn = obj.get("function")
    if isinstance(fn, dict):  # OpenAI style {"function": {"name": .., "arguments": ..}}
        obj = {**obj, **fn}
    name = next((obj[k] for k in _NAME_KEYS if isinstance(obj.get(k), str)), None)
    if name is None or name not in tool_names:
        return None
    # Not `{}` for what cannot be read, nor for arguments never written: that is a call with its
    # defaults, on nothing the LLM said. The hint asks for an arguments object, so an object
    # without one is not the call it describes, and the LLM is asked again.
    key = next((k for k in _ARGS_KEYS if k in obj), None)
    if key is None:
        return None
    args: Any = obj[key]
    return ToolCall(name=name, arguments=tool_arguments("null" if args is None else args))


def parse_tool_call_from_text(text: str, tool_names: set[str]) -> ToolCall | None:
    """A tool call an LLM wrote as JSON text instead of a native tool call, or None.

    Text is a call only when the answer is nothing but the call (`_BARE_CALL`): one JSON object,
    optionally inside one code fence, after the thinking `_normalise` split off. The first object
    anywhere in the answer used to be taken, so a call the LLM quoted in order to decline it, or
    the first of several it listed as options, ran as its choice.

    At most `MAX_CALL_CHARS` is read, and decoded once. Text the decoder raises on is no call,
    and the LLM is told so (`NOT_A_BARE_CALL`): a `JSONDecodeError`, and the two it raised past
    the one this caught, `ValueError` for an integer longer than the interpreter converts and
    `RecursionError` for nesting deeper than it follows. This runs outside the provider's try,
    so either used to leave it raw and end the run on one odd answer."""
    if text is None or len(text) > MAX_CALL_CHARS:
        return None
    body = _bare(text)
    if body is None:
        return None
    try:
        obj = json.loads(body)
        # arguments written as a string are decoded again here (`tool_arguments`)
        return _as_call(obj, tool_names) if isinstance(obj, dict) else None
    except (ValueError, RecursionError):
        return None


class LocalProvider(OpenAIProvider):
    """An LLM on a server you run: one of the four presets, or `local` at any address.

    Where the server is, the first rung that is set wins, and nothing is probed:

    1. `base_url`, which is `--base-url`: a URL given for this run is used exactly as given.
    2. `host`, which is `--host` or the host a registered robot was stored with: the preset's
       address moved to that machine (`on_host`), port and path kept.
    3. `ROBOGLOT_BASE_URL`, then 4. `OPENAI_BASE_URL`, each used exactly as given.
    5. `ROBOGLOT_HOST`: the preset's address moved to that machine.
    6. The preset's own address, on localhost.

    A URL given anywhere is used as given, and a host only ever moves a preset. That is why
    `local`, which has no preset address, refuses a host on its own and asks for
    `--base-url`. The two hosts sit on different rungs on purpose. One typed for this run or
    registered with this robot is a decision about this run, and beats a `.env` line naming an
    LLM server's URL; `ROBOGLOT_HOST` is the board you usually use, and must not.

    `OPENAI_BASE_URL` is a rung of this ladder and nothing more: an address Roboglot chose to read
    for a server you run, not the address the SDK found for itself, so the SDK's own variables
    never go there (`openai_headers`, whose module docstring says why).

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
        llm_id: str | None = None,
        *,
        preset: str = "local",
        base_url: str | None = None,
        host: str | None = None,
        client: Any = None,
        api_key: str | None = None,
        tool_choice: str | None = None,
        vision: bool | None = None,
        extra_body: dict[str, Any] | None = None,
        reasoning_effort: str | None = None,
    ) -> None:
        if preset not in PRESETS:
            raise ProviderError(f"unknown local preset {preset!r}; one of {', '.join(LOCAL_NAMES)}")
        self.name = preset
        self.preset = preset
        url = self._address(PRESETS[preset], base_url, host)
        choice = tool_choice or os.environ.get("ROBOGLOT_TOOL_CHOICE") or "auto"
        if vision is None:
            vision = os.environ.get("ROBOGLOT_VISION", "0").lower() not in ("", "0", "false", "no")
        super().__init__(
            llm_id or "",
            client=client,
            api_key=api_key,
            base_url=url,
            tool_choice=choice,
            vision=vision,
            extra_body=extra_body,
            reasoning_effort=reasoning_effort,
        )
        self.text_fallbacks = 0

    @staticmethod
    def _address(preset: str | None, base_url: str | None, host: str | None) -> str:
        """The class docstring's ladder, rung by rung. Blank is absent at every rung, as it is
        everywhere Roboglot reads a setting: `ROBOGLOT_HOST=` in a `.env` is a shell saying unset,
        and a URL of spaces is nothing typed. A URL used to be taken as it came, so one of spaces
        won its rung, beat `--host` and `ROBOGLOT_HOST` below it, and was handed to the SDK, which
        kept it as the address, so every request failed."""
        host = (host or "").strip() or None
        env_host = (os.environ.get(HOST_ENV) or "").strip() or None
        given = (base_url or "").strip()
        if given:
            return given
        if host and preset:
            return _moved(preset, host, source="--host")
        for name in ("ROBOGLOT_BASE_URL", "OPENAI_BASE_URL"):
            url = (os.environ.get(name) or "").strip()
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

    async def ensure_llm(self) -> str:
        """Discover the served LLM when none was given (`/v1/models`, first entry).

        A failure names the address the client was handed, as a refusal may quote it, rather than
        the one this provider was given, since that is where the request went."""
        if self.llm_id:
            return self.llm_id
        try:
            page = await self.client.models.list()
        except Exception as e:
            redirected = self._redirected(e)
            if redirected is not None:
                raise redirected from e
            raise ProviderError(
                f"{self.name}: cannot list LLMs at {_quoted(self.client_address())} "
                f"({type(e).__name__}: {e}). Is the server running? Pass --llm <preset>:<llm> "
                "to skip discovery."
            ) from e
        ids = [str(getattr(m, "id", "")) for m in (getattr(page, "data", None) or [])]
        ids = [i for i in ids if i]
        if not ids:
            raise ProviderError(
                f"{self.name}: the server at {_quoted(self.client_address())} lists no LLMs. "
                "Load one (e.g. `ollama pull qwen3:8b`) or pass --llm <preset>:<llm>."
            )
        self.llm_id = ids[0]
        return self.llm_id

    async def step(
        self, system: str, history: list[Any], tools: list[dict[str, Any]]
    ) -> ProviderTurn:
        await self.ensure_llm()
        return await super().step(system, history, tools)

    def _normalise(self, turn: ProviderTurn) -> ProviderTurn:
        """Split inline `<think>` off before anything else reads the text.

        It runs here, not after `super().step()`, because the JSON text fallback reads
        `turn.text`: an LLM that weighs a verb in its reasoning and then rejects it
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
            # text that was read and was no call: the LLM is told so when it is asked again, and
            # an answer with no text is asked again in the loop's own words, as it always was
            if (turn.text or "").strip():
                return turn.model_copy(update={"not_a_call": NOT_A_BARE_CALL})
            return turn
        self.text_fallbacks += 1
        call = call.model_copy(update={"id": f"text-{self.text_fallbacks}"})
        return turn.model_copy(update={"tool_calls": [call], "stop_reason": "text_fallback"})
