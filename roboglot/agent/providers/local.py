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
from collections.abc import Collection
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
an answer to (`read_call`), read by both from here."""
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

NOT_OFFERED = "Your last answer called {name}, which is not one of your tools, so nothing was run."
"""What the LLM is told of one object in the hint's shape that names no tool it was given: it was
in the right shape, so being told it was not would have it send the same object again. The name
is quoted as JSON, so nothing in it can start a line of its own."""

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

_FENCE_MIN = 3
"""The fewest backticks or tildes that open a code fence, as CommonMark has it."""
_THINK_OPEN = "<think>"
_THINK_CLOSE = "</think>"
_TOOL_CALL_OPEN = "<tool_call>"
_TOOL_CALL_CLOSE = "</tool_call>"
"""The tags Hermes and Qwen chat templates wrap a call in, which reach the text whenever the
server's own tool parser does not take them: one block round one call is that call."""
_INVISIBLE = "".join(map(chr, (0xFEFF, 0x200B, 0x200C, 0x200D, 0x2060)))
"""What an answer can carry round its call that shows nothing and that `str.strip` keeps: a byte
order mark and the zero-width characters. Trimmed as whitespace is (`_trimmed`)."""
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
    opening with no closing after it ends the pass, since no later one can have one either.

    A chat template that writes the opening tag itself, at the end of the prompt, gets a reply
    whose thinking has only its closing tag: everything up to a first `</think>` with no
    `<think>` before it is that block (`_template_opened`), unless the reply opens as a call
    does, whose own strings may hold the tag (`_opens_as_a_call`)."""
    thoughts: list[str] = []
    kept: list[str] = []
    at = 0
    if not _opens_as_a_call(text) and (lead := _template_opened(text)) is not None:
        thoughts.append(text[:lead].strip())
        at = lead + len(_THINK_CLOSE)
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


def _template_opened(text: str) -> int | None:
    """Where a thinking block whose opening tag the chat template wrote ends: the first
    `</think>` when no `<think>` comes before it, or None."""
    closed = _find(text, _THINK_CLOSE, 0)
    if closed == -1:
        return None
    opened = _find(text, _THINK_OPEN, 0)
    return closed if opened == -1 or opened > closed else None


def _trimmed(text: str) -> str:
    """`text` without whitespace or `_INVISIBLE` characters at either end, read once from each."""
    start, end = 0, len(text)
    while start < end and (text[start].isspace() or text[start] in _INVISIBLE):
        start += 1
    while end > start and (text[end - 1].isspace() or text[end - 1] in _INVISIBLE):
        end -= 1
    return text[start:end]


def _opens_as_a_call(text: str) -> bool:
    """Whether an answer begins as a call does: an object, a fence or a `<tool_call>` block.
    Such an answer is no thinking, whatever its strings hold."""
    return _trimmed(text).startswith(("{", "`" * _FENCE_MIN, "~" * _FENCE_MIN, _TOOL_CALL_OPEN))


def _fenced(body: str) -> str | None:
    """The inside of the one code fence `body` opens with, or None where it opens with none.

    A fence is a run of at least `_FENCE_MIN` backticks or tildes, as Markdown has it, closed at
    the very end by a run of the same at least as long, or left open there, as an answer the
    server cut at its own stop word is. What it holds is still the decode's to judge, which takes
    nothing after the object, so an open fence with prose after its call is no call."""
    char = body[:1]
    if char not in ("`", "~"):
        return None
    run = len(body) - len(body.lstrip(char))
    if run < _FENCE_MIN:
        return None
    inner = body[run:]
    closing = len(inner) - len(inner.rstrip(char))
    return inner[:-closing] if closing >= run else inner


def _bare(text: str) -> str | None:
    """The one JSON object an answer is nothing but, as text, or None: the whole of it once
    trimmed, the inside of one code fence that is the whole of it (`_fenced`), whatever the
    fence's opening line says it holds, or the inside of one `<tool_call>` block that is the whole
    of it, closed at the end or left open there. Whether that is one object and nothing after it
    is the decode's to say, which takes nothing after the value."""
    body = _trimmed(text)
    if body.startswith(_TOOL_CALL_OPEN):
        inner = body[len(_TOOL_CALL_OPEN) :]
        if inner.endswith(_TOOL_CALL_CLOSE):
            inner = inner[: -len(_TOOL_CALL_CLOSE)]
        if _TOOL_CALL_OPEN in inner or _TOOL_CALL_CLOSE in inner:
            return None  # two blocks, and whatever is between them
        body = _trimmed(inner)
    elif (fenced := _fenced(body)) is not None:
        inner = fenced
        line, newline, below = inner.partition("\n")
        at = line.find("{")
        if newline and _trimmed(below).startswith("{"):
            # the opening line is the fence's info string, whatever it says (`json`, `JSON`,
            # `jsonc`, `js`, a pandoc `{.json}`, none at all), and it goes with the fence
            inner = below
        elif at == -1:
            inner = ""
        elif len(line[:at].split()) > 1:
            return None  # words before the object on the fence's own line
        else:
            inner = inner[at:]  # the object on the opening line, after one word or none
        body = _trimmed(inner)
    return body if body.startswith("{") else None


def _as_call(
    obj: dict[str, Any], tool_names: set[str], no_parameters: Collection[str]
) -> ToolCall | str:
    """The call one object is, or the words the LLM is told about why it is none."""
    fn = obj.get("function")
    if isinstance(fn, dict):  # OpenAI style {"function": {"name": .., "arguments": ..}}
        obj = {**obj, **fn}
    name = next((obj[k] for k in _NAME_KEYS if isinstance(obj.get(k), str)), None)
    if name is None:
        return NOT_A_BARE_CALL
    if name not in tool_names:
        return NOT_OFFERED.format(name=json.dumps(name))
    # Not `{}` for what cannot be read, nor for arguments never written to a verb that takes
    # some: that is a call with its defaults, on nothing the LLM said. A verb that takes none
    # has no defaults to fill in, so it is called with none.
    key = next((k for k in _ARGS_KEYS if k in obj), None)
    if key is None:
        return ToolCall(name=name, arguments={}) if name in no_parameters else NOT_A_BARE_CALL
    args: Any = obj[key]
    return ToolCall(name=name, arguments=tool_arguments("null" if args is None else args))


def read_call(
    text: str, tool_names: set[str], *, no_parameters: Collection[str] = ()
) -> ToolCall | str | None:
    """The call an answer written as text is nothing but, the words the LLM is told about why it
    is none, or None for an answer with nothing after its thinking, which is asked again in the
    loop's own words.

    Text is a call only when the answer is nothing but the call (`_BARE_CALL`): one JSON object,
    optionally inside one code fence or one `<tool_call>` block, after an optional thinking block.
    The block is `<think>...</think>` at the very start, or everything up to a first `</think>`
    with no `<think>` before it, from a template that wrote the opening itself; the whole answer
    is read as a call before that second reading is tried, since a call's own string may hold the
    tag. What follows a block is read as it is, any further thinking included, so a call with a
    thought after it, or a second block before it, is not nothing but the call. The first object
    anywhere in the answer used to be taken, so a call the LLM quoted in order to decline it, or
    the first of several it listed as options, ran as its choice. `no_parameters` names the
    tools that take none, which are called with none when the object has no arguments.

    At most `MAX_CALL_CHARS` of each reading is read, and each is decoded once, so an answer is
    decoded twice at most. Text the decoder raises on is no call: a `JSONDecodeError`,
    `ValueError` for an integer longer than the interpreter converts and `RecursionError` for
    nesting deeper than it follows. This runs outside the provider's try, so either used to leave
    it raw and end the run on one odd answer."""
    text = text or ""
    opened = _find(text, _THINK_OPEN, 0)
    if opened != -1 and not _trimmed(text[:opened]):
        closed = _find(text, _THINK_CLOSE, opened + len(_THINK_OPEN))
        if closed == -1:
            return None  # thinking to the end
        return _read_one(text[closed + len(_THINK_CLOSE) :], tool_names, no_parameters)
    whole = _read_one(text, tool_names, no_parameters)
    if whole != NOT_A_BARE_CALL or (lead := _template_opened(text)) is None:
        return whole
    return _read_one(text[lead + len(_THINK_CLOSE) :], tool_names, no_parameters)


def _read_one(
    said: str, tool_names: set[str], no_parameters: Collection[str]
) -> ToolCall | str | None:
    """One reading of an answer as a call (`read_call`): the call, the words, or None for one
    with nothing in it."""
    if not _trimmed(said):
        return None
    if len(said) > MAX_CALL_CHARS:
        return NOT_A_BARE_CALL
    body = _bare(said)
    if body is None:
        return NOT_A_BARE_CALL
    try:
        obj = json.loads(body)
        # arguments written as a string are decoded again here (`tool_arguments`)
        if not isinstance(obj, dict):
            return NOT_A_BARE_CALL
        return _as_call(obj, tool_names, no_parameters)
    except (ValueError, RecursionError):
        return NOT_A_BARE_CALL


def parse_tool_call_from_text(
    text: str, tool_names: set[str], *, no_parameters: Collection[str] = ()
) -> ToolCall | None:
    """A tool call an LLM wrote as text instead of a native tool call, or None (`read_call`)."""
    read = read_call(text, tool_names, no_parameters=no_parameters)
    return read if isinstance(read, ToolCall) else None


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
        self._answer: str | None = None
        """The last answer's text as it came, before its thinking was split off (`_normalise`)."""

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

        The answer as it came is kept for the fallback (`_answer`), which reads the call from
        it and not from what is left once every block is split off: a call with a thought after
        it, or a second thought before it, is not nothing but the call.
        """
        text = turn.text
        self._answer = text
        if turn.thinking is None and text and (_THINK_OPEN in text or _THINK_CLOSE in text):
            thinking, rest = split_thinking(text)
            turn = turn.model_copy(update={"thinking": thinking, "text": rest or None})
        return turn

    def _fallback(self, turn: ProviderTurn, tools: list[dict[str, Any]]) -> ProviderTurn:
        # a tool whose schema has no parameters is called with none when no arguments were written
        bare = {t["name"] for t in tools if not (t.get("input_schema") or {}).get("properties")}
        read = read_call(self._answer or "", {t["name"] for t in tools}, no_parameters=bare)
        if read is None:
            # an answer with nothing after its thinking is asked again in the loop's own words
            return turn
        if isinstance(read, str):
            # text that was read and was no call: the LLM is told why when it is asked again
            return turn.model_copy(update={"not_a_call": read})
        call = read
        self.text_fallbacks += 1
        call = call.model_copy(update={"id": f"text-{self.text_fallbacks}"})
        return turn.model_copy(update={"tool_calls": [call], "stop_reason": "text_fallback"})
