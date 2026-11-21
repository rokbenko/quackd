"""Local and open-source providers: presets, no key, relaxed tool calling, text fallback."""

from __future__ import annotations

import json
import sys
import threading
from pathlib import Path
from types import SimpleNamespace as NS
from typing import Any

import pytest

from roboglot.agent.loop import REPROMPT, RunConfig, run_glot
from roboglot.agent.providers import local
from roboglot.agent.providers.base import (
    Exchange,
    NamedPng,
    Observation,
    ProviderError,
    ToolCall,
)
from roboglot.agent.providers.factory import LOCAL_NAMES, PROVIDER_NAMES, make_provider
from roboglot.agent.providers.fake import AUTO_VERDICT
from roboglot.agent.providers.local import (
    MAX_CALL_CHARS,
    NOT_A_BARE_CALL,
    NOT_OFFERED,
    PRESETS,
    TOOL_HINT,
    LocalProvider,
    on_host,
    parse_tool_call_from_text,
    read_call,
    split_thinking,
)
from roboglot.agent.providers.openai import OpenAIProvider
from roboglot.agent.transcript import Transcript
from roboglot.glotfile.schema import GlotFile
from roboglot.transport.mock import MockTransport

TOOLS = [
    {"name": "walk_to", "description": "d", "input_schema": {"type": "object", "properties": {}}},
    {"name": "kick", "description": "d", "input_schema": {"type": "object", "properties": {}}},
]
NAMES = {"walk_to", "kick"}
WARM_UP_ENDS_S = 60.0
"""How long a test that runs the loop waits for the pilot's warm-up thread to end. Generous on
purpose: it waits for the end it needs, and only a warm-up that never ends uses all of it."""


class FakeClient:
    def __init__(self, response: Any = None, llms: list[str] | None = None) -> None:
        self.kwargs: dict[str, Any] = {}
        self.listed = 0

        async def create(**kwargs: Any) -> Any:
            self.kwargs = kwargs
            return response

        async def list_llms() -> Any:
            self.listed += 1
            return NS(data=[NS(id=m) for m in (llms or [])])

        self.chat = NS(completions=NS(create=create))
        self.models = NS(list=list_llms)


def reply(text: str | None = None, tool_calls: list[Any] | None = None) -> Any:
    return NS(
        choices=[NS(message=NS(content=text, tool_calls=tool_calls), finish_reason="stop")],
        usage=NS(prompt_tokens=10, completion_tokens=5),
    )


def history() -> list[Exchange]:
    return [
        Exchange(observation=Observation(text="obs", images=[NamedPng(name="camera", png=b"png")]))
    ]


# ── presets and construction ────────────────────────────────────────────────────────────


def test_presets_and_names() -> None:
    assert set(LOCAL_NAMES) == set(PRESETS) and all(n in PROVIDER_NAMES for n in LOCAL_NAMES)
    assert LocalProvider("m", preset="ollama", client=FakeClient()).base_url.endswith(":11434/v1")
    assert LocalProvider("m", preset="vllm", client=FakeClient()).base_url.endswith(":8000/v1")
    assert LocalProvider("m", preset="llamacpp", client=FakeClient()).base_url.endswith(":8080/v1")
    assert LocalProvider("m", preset="lmstudio", client=FakeClient()).base_url.endswith(":1234/v1")


def test_local_needs_an_address(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ROBOGLOT_BASE_URL", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    with pytest.raises(ProviderError, match="--base-url"):
        LocalProvider("m", preset="local", client=FakeClient())
    p = LocalProvider("m", preset="local", base_url="http://gpu:8000/v1", client=FakeClient())
    assert p.base_url == "http://gpu:8000/v1" and p.name == "local"


def test_env_base_url_overrides_preset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ROBOGLOT_BASE_URL", "http://box:9999/v1")
    assert LocalProvider("m", preset="ollama", client=FakeClient()).base_url == "http://box:9999/v1"


# ── where the server is: the address ladder, and --host moving a preset ─────────────────

_ADDRESS_ENV = ("ROBOGLOT_BASE_URL", "OPENAI_BASE_URL", "ROBOGLOT_HOST")


def _address(
    monkeypatch: pytest.MonkeyPatch,
    *,
    preset: str = "ollama",
    base_url: str | None = None,
    host: str | None = None,
    **env: str,
) -> str:
    """The address a local provider settles on, with every variable it reads set here and
    nowhere else, so a developer's shell cannot decide a rung for the test."""
    for name in _ADDRESS_ENV:
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    provider = LocalProvider("m", preset=preset, base_url=base_url, host=host, client=FakeClient())
    return str(provider.base_url)


#: `LocalProvider`'s ladder, top rung first: where each rung is given, what it is given, and
#: the address the Ollama preset then settles on. The two hosts move `localhost` and keep the
#: preset's own port and path; the three URLs are used exactly as given.
LADDER: list[tuple[str, str, str]] = [
    ("base_url", "http://given:1/v1", "http://given:1/v1"),
    ("host", "flag-board", "http://flag-board:11434/v1"),
    ("ROBOGLOT_BASE_URL", "http://roboglot-env:2/v1", "http://roboglot-env:2/v1"),
    ("OPENAI_BASE_URL", "http://openai-env:3/v1", "http://openai-env:3/v1"),
    ("ROBOGLOT_HOST", "env-board", "http://env-board:11434/v1"),
]


@pytest.mark.parametrize("top", range(len(LADDER) + 1), ids=[r[0] for r in LADDER] + ["preset"])
def test_each_rung_of_the_address_ladder_beats_every_rung_below_it(
    monkeypatch: pytest.MonkeyPatch, top: int
) -> None:
    """Every rung from `top` down is given at once, and the one at `top` has to win. Across
    the parametrisation that is each rung against all of the rungs below it together, which
    catches a rung swapped with any lower one, not only with its neighbour. The last case gives
    nothing and gets the preset itself."""
    given = {name: value for name, value, _ in LADDER[top:]}
    env = {name: value for name, value in given.items() if name.isupper()}
    url = _address(monkeypatch, base_url=given.get("base_url"), host=given.get("host"), **env)
    assert url == (LADDER[top][2] if top < len(LADDER) else "http://localhost:11434/v1")


def test_a_base_url_on_the_line_beats_the_one_in_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The oldest two rungs, and until the ladder grew a host nothing had ever put them against
    each other: `test_env_base_url_overrides_preset` only proves the variable beats a preset."""
    url = _address(monkeypatch, base_url="http://typed:1/v1", ROBOGLOT_BASE_URL="http://env:2/v1")
    assert url == "http://typed:1/v1"


def test_a_host_named_for_the_run_beats_roboglot_base_url_and_one_from_the_environment_does_not(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The two hosts sit on different rungs on purpose. `--host` (or the robot's own) is a
    decision about this run and moves the preset past an LLM server's URL in `.env`;
    `ROBOGLOT_HOST` is the board you usually use, and a URL in the same `.env` is more exact."""
    typed = _address(monkeypatch, host="jetson.local", ROBOGLOT_BASE_URL="http://env:2/v1")
    assert typed == "http://jetson.local:11434/v1"
    usual = _address(monkeypatch, ROBOGLOT_HOST="jetson.local", ROBOGLOT_BASE_URL="http://env:2/v1")
    assert usual == "http://env:2/v1"


def test_a_blank_rung_is_no_rung(monkeypatch: pytest.MonkeyPatch) -> None:
    """`ROBOGLOT_HOST=` in a `.env` is a shell saying unset, and a blank flag is nothing typed.
    Neither may become a machine named "" in the LLM server's URL."""
    assert _address(monkeypatch, host="  ", ROBOGLOT_HOST="") == "http://localhost:11434/v1"


@pytest.mark.parametrize(
    "blank",
    [{"base_url": " "}, {"ROBOGLOT_BASE_URL": " "}, {"OPENAI_BASE_URL": " "}],
    ids=["--base-url", "ROBOGLOT_BASE_URL", "OPENAI_BASE_URL"],
)
def test_a_url_of_spaces_is_no_rung_either(
    blank: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A URL rung was taken as it came, so one of spaces won it, beat `ROBOGLOT_HOST` below it,
    and was then read as no address at all and sent on to wherever the SDK would have gone:
    `OPENAI_BASE_URL`, or OpenAI's own API with `OPENAI_API_KEY`. It is nothing typed, and the
    board below it is where the server is."""
    env = {name: value for name, value in blank.items() if name.isupper()}
    url = _address(monkeypatch, base_url=blank.get("base_url"), ROBOGLOT_HOST="jetson.local", **env)
    assert url == "http://jetson.local:11434/v1"


def test_every_preset_moves_to_the_host_with_its_own_port_and_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The port in `--host` is the daemon's. An LLM server on the board listens where it
    always does, so each preset keeps its own, whatever port the daemon was given."""
    for preset, url in PRESETS.items():
        if url is None:
            continue
        moved = _address(monkeypatch, preset=preset, host="jetson.local:9000")
        assert moved == url.replace("localhost", "jetson.local"), preset


@pytest.mark.parametrize(
    ("host", "moved"),
    [
        ("jetson.local", "http://jetson.local:11434/v1"),
        ("jetson.local:9874", "http://jetson.local:11434/v1"),
        ("192.168.1.5:9000", "http://192.168.1.5:11434/v1"),
        ("[::1]:9874", "http://[::1]:11434/v1"),
        ("[2001:db8::5]", "http://[2001:db8::5]:11434/v1"),
        ("fe80::1", "http://[fe80::1]:11434/v1"),
    ],
)
def test_on_host_moves_localhost_and_keeps_the_scheme_the_port_and_the_path(
    host: str, moved: str
) -> None:
    """Every shape `--host` takes, and an IPv6 address comes out in brackets whether or not it
    went in with them: a URL cannot carry one any other way."""
    assert on_host(PRESETS["ollama"] or "", host) == moved


def test_on_host_keeps_a_port_that_is_not_a_presets_and_a_url_with_no_port() -> None:
    assert on_host("https://localhost:8443/api/v1", "[::1]:9874") == "https://[::1]:8443/api/v1"
    assert on_host("http://localhost/v1", "fe80::1") == "http://[fe80::1]/v1"
    assert on_host("http://localhost:1234/v1", "jetson.local") == "http://jetson.local:1234/v1"


def test_on_host_refuses_what_is_not_a_machine_in_parse_hosts_words() -> None:
    with pytest.raises(ValueError, match="not a URL"):
        on_host(PRESETS["ollama"] or "", "http://jetson.local")


def test_a_bad_roboglot_host_is_one_line_that_names_the_variable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every `parse_host` sentence says `--host`, and somebody who never typed one would look
    for it on the wrong line. A ProviderError, because that is what every caller of a
    provider's constructor prints as one line rather than a traceback."""
    with pytest.raises(ProviderError, match=r"^ROBOGLOT_HOST: --host takes a machine, not a URL"):
        _address(monkeypatch, ROBOGLOT_HOST="http://jetson.local")
    with pytest.raises(ProviderError, match=r"^--host takes a machine, not a URL"):
        _address(monkeypatch, host="http://jetson.local")


def test_local_with_only_a_host_is_refused_and_told_a_host_moves_only_a_preset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`local` has no preset address, so there is no `localhost` for a host to replace, and
    guessing a port on the board would be guessing which server the reader meant."""
    with pytest.raises(ProviderError, match="a host only moves a preset's") as typed:
        _address(monkeypatch, preset="local", host="jetson.local")
    assert "--base-url" in str(typed.value)
    with pytest.raises(ProviderError, match="a host only moves a preset's"):
        _address(monkeypatch, preset="local", ROBOGLOT_HOST="jetson.local")
    given = _address(
        monkeypatch, preset="local", host="jetson.local", base_url="http://gpu:8000/v1"
    )
    assert given == "http://gpu:8000/v1", "a URL given anywhere is used as given"


def test_make_provider_hands_the_host_to_the_local_presets_and_to_no_vendor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The factory's half of the wiring, read off the constructors' own arguments, because
    building either provider for real wants the OpenAI SDK and CI installs no provider SDK."""
    seen: dict[str, dict[str, Any]] = {}

    def recorder(name: str) -> Any:
        def build(*args: Any, **kwargs: Any) -> Any:
            seen[name] = kwargs
            return NS(name=name)

        return build

    monkeypatch.setattr("roboglot.agent.providers.local.LocalProvider", recorder("local"))
    monkeypatch.setattr("roboglot.agent.providers.openai.OpenAIProvider", recorder("openai"))
    make_provider("ollama", llm_id="m", host="jetson.local")
    assert seen["local"]["host"] == "jetson.local"
    make_provider("openai", host="jetson.local")
    assert "host" not in seen["openai"], "a vendor's API has no localhost to move"


def test_no_key_needed(monkeypatch: pytest.MonkeyPatch) -> None:
    """With no `LOCAL_API_KEY` a server you run is sent a placeholder, and never `OPENAI_API_KEY`,
    which used to be its fallback and so reached every local server it shared a `.env` with."""
    monkeypatch.delenv("LOCAL_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai-not-a-real-key")
    p = LocalProvider("m", preset="ollama", client=FakeClient())
    assert p._fallback_key() == "not-needed"
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(ProviderError):
        OpenAIProvider("m")  # cloud still requires a key


# ── request params ──────────────────────────────────────────────────────────────────────


async def test_local_sends_relaxed_tool_params(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ROBOGLOT_TOOL_CHOICE", raising=False)
    client = FakeClient(reply(tool_calls=[NS(id="c1", function=NS(name="kick", arguments="{}"))]))
    turn = await LocalProvider("m", preset="ollama", client=client).step("S", history(), TOOLS)
    assert client.kwargs["tool_choice"] == "auto"
    assert "parallel_tool_calls" not in client.kwargs
    assert turn.tool_calls[0].name == "kick"


async def test_tool_choice_none_omits_the_field(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ROBOGLOT_TOOL_CHOICE", "none")
    client = FakeClient(reply(text="{}"))
    await LocalProvider("m", preset="ollama", client=client).step("S", history(), TOOLS)
    assert "tool_choice" not in client.kwargs


async def test_local_forwards_extra_body() -> None:
    """The presets are where #12 came from: a vLLM server wants a field in the body and
    `LocalProvider` has to carry it up to the base class, which is what sends it."""
    body = {"chat_template_kwargs": {"enable_thinking": False}}
    client = FakeClient(reply(text="{}"))
    await LocalProvider("m", preset="vllm", client=client, extra_body=body).step(
        "S", history(), TOOLS
    )
    assert client.kwargs["extra_body"] == body


async def test_cloud_openai_keeps_strict_params() -> None:
    client = FakeClient(reply(tool_calls=[NS(id="c1", function=NS(name="kick", arguments="{}"))]))
    await OpenAIProvider("gpt-5", client=client).step("S", history(), TOOLS)
    assert client.kwargs["tool_choice"] == "required"
    assert client.kwargs["parallel_tool_calls"] is False


# ── vision ──────────────────────────────────────────────────────────────────────────────


def test_vision_off_by_default_on_for_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ROBOGLOT_VISION", raising=False)
    assert LocalProvider("m", preset="ollama", client=FakeClient()).supports_vision is False
    assert LocalProvider("m", preset="ollama", client=FakeClient(), vision=True).supports_vision
    monkeypatch.setenv("ROBOGLOT_VISION", "1")
    assert LocalProvider("m", preset="ollama", client=FakeClient()).supports_vision is True


async def test_every_camera_reaches_a_local_server_in_one_message() -> None:
    """A local server speaks the OpenAI wire format, so it inherits multi-camera rendering
    without a line of its own here. That inheritance is the thing worth pinning: the one
    provider Roboglot cannot test against a real endpoint is the one most likely to be handed
    two pictures by somebody with a webcam on each side of an arm.

    It is also where the cost lands. Two cameras is two image parts in a single message, and
    `docs/guides/local-llms.md` warns that a server or an LLM may accept only one. This says what
    Roboglot sends; whether a given server takes it is that server's business."""
    client = FakeClient(reply(text="{}"))
    p = LocalProvider("m", preset="ollama", client=client, vision=True)
    two = [
        Exchange(
            observation=Observation(
                text="obs",
                images=[NamedPng(name="top", png=b"a"), NamedPng(name="side", png=b"b")],
            )
        )
    ]
    await p.step("system", two, [])
    parts = client.kwargs["messages"][1]["content"]  # type: ignore[attr-defined]
    assert [part["type"] for part in parts] == ["text", "text", "image_url", "text", "image_url"]
    assert parts[1]["text"] == "camera top:" and parts[3]["text"] == "camera side:"

    # and one camera is the single part it has always been, with no label in front of it
    client_one = FakeClient(reply(text="{}"))
    await LocalProvider("m", preset="ollama", client=client_one, vision=True).step(
        "system", history(), []
    )
    one = client_one.kwargs["messages"][1]["content"]  # type: ignore[attr-defined]
    assert [part["type"] for part in one] == ["text", "image_url"], one

    # the case the two above cannot tell apart, and the only one that needed a change: ONE
    # picture from a body that has TWO cameras. Counting the images that arrived makes this
    # look exactly like the single-camera message above, and sends the side view out bare
    # under a detections line measured off the lens that died.
    client_lost = FakeClient(reply(text="{}"))
    lost = [
        Exchange(
            observation=Observation(
                text="obs",
                images=[NamedPng(name="side", png=b"b")],
                cameras=["top", "side"],
            )
        )
    ]
    await LocalProvider("m", preset="ollama", client=client_lost, vision=True).step(
        "system", lost, []
    )
    survivor = client_lost.kwargs["messages"][1]["content"]  # type: ignore[attr-defined]
    assert [part["type"] for part in survivor] == ["text", "text", "image_url"], survivor
    assert survivor[1]["text"] == "camera side:", "the one lens left went out unnamed"


# ── LLM discovery ─────────────────────────────────────────────────────────────────────


async def test_llm_discovery_takes_first_served_llm() -> None:
    client = FakeClient(reply(text="{}"), llms=["qwen3:8b", "llama3.2"])
    p = LocalProvider(None, preset="ollama", client=client)
    await p.step("S", history(), TOOLS)
    assert p.llm_id == "qwen3:8b" and client.kwargs["model"] == "qwen3:8b"
    await p.step("S", history(), TOOLS)
    assert client.listed == 1  # discovered once


async def test_llm_discovery_with_empty_server_is_clear() -> None:
    p = LocalProvider(None, preset="ollama", client=FakeClient(reply(text="{}"), llms=[]))
    with pytest.raises(ProviderError, match="lists no LLMs"):
        await p.step("S", history(), TOOLS)


async def test_llm_discovery_names_the_server_without_the_password_in_its_address(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Both of discovery's failures name the server, and they named it as this provider was
    given it, a password in it included. They name the address the client was handed, as a
    refusal may quote it: the password is a `***`."""
    for name in _ADDRESS_ENV:
        monkeypatch.delenv(name, raising=False)
    url = "http://rok:pw-SECRET@box:8000/v1"
    empty = LocalProvider(None, preset="vllm", base_url=url, client=FakeClient(llms=[]))
    with pytest.raises(ProviderError) as e:
        await empty.ensure_llm()
    assert "the server at http://rok:***@box:8000/v1 lists no LLMs" in str(e.value)
    assert "pw-SECRET" not in str(e.value)

    async def refuse() -> Any:
        raise ConnectionError("refused")

    down = FakeClient()
    down.models = NS(list=refuse)
    stopped = LocalProvider(None, preset="vllm", base_url=url, client=down)
    with pytest.raises(ProviderError) as e:
        await stopped.ensure_llm()
    assert "cannot list LLMs at http://rok:***@box:8000/v1" in str(e.value)
    assert "pw-SECRET" not in str(e.value)


# ── text fallback ───────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "text",
    [
        '{"name": "walk_to", "arguments": {"target": "ball"}}',
        '```json\n{"name": "walk_to", "arguments": {"target": "ball"}}\n```',
        '\n```\n{"name": "walk_to", "arguments": {"target": "ball"}}\n```\n',
        '{"tool": "walk_to", "parameters": {"target": "ball"}}',
        '{"function": {"name": "walk_to", "arguments": "{\\"target\\": \\"ball\\"}"}}',
        '  {"name":"walk_to","args":{"target":"ball"}}\n',
    ],
)
def test_parse_tool_call_from_text_variants(text: str) -> None:
    call = parse_tool_call_from_text(text, NAMES)
    assert call == ToolCall(name="walk_to", arguments={"target": "ball"})


@pytest.mark.parametrize(
    "text",
    ["no json here", '{"name": "fly", "arguments": {}}', "{broken", "", '{"foo": 1}'],
)
def test_parse_tool_call_from_text_rejects(text: str) -> None:
    assert parse_tool_call_from_text(text, NAMES) is None


@pytest.mark.parametrize(
    "text",
    [
        'I should not use {"name": "kick", "arguments": {"leg": "right"}} with the child this '
        "close, so I will wait.",
        'I could do {"name": "kick", "arguments": {}} or {"name": "walk_to", "arguments": {}}; '
        "which do you want?",
        '{"name": "kick", "arguments": {}}\n{"name": "walk_to", "arguments": {}}',
        'Sure! ```json\n{"name": "kick", "arguments": {}}\n```',
        '```json\n{"name": "kick", "arguments": {}}\n```\nThen I will look again.',
        '```json\n{"name": "kick", "arguments": {}}\n```\n```json\n{"name": "walk_to", '
        '"arguments": {}}\n```',
        'I will approach. {"name":"walk_to","args":{"target":"ball"}} Then kick.',
        'Here is my call: {"name": "kick", "arguments": {}}',
        '{"name": "kick", "arguments": {}}<think>no, wait: the child is too close</think>',
        '<think>plan it</think>{"name": "kick", "arguments": {}}<think>no, too close</think>',
        '<think>first</think><think>second</think>{"name": "kick", "arguments": {}}',
        '{"name": "kick", "arguments": {}}\n<think>hmm',
        '<tool_call>{"name": "kick", "arguments": {}}</tool_call>\n'
        '<tool_call>{"name": "walk_to", "arguments": {}}</tool_call>',
        'Sure. <tool_call>{"name": "kick", "arguments": {}}</tool_call>',
        '```json I will kick {"name": "kick", "arguments": {}}```',
        '```json\n{"name": "kick", "arguments": {}}\nThen I will look again.',
        '<tool_call>{"name": "kick", "arguments": {}}\nThen I will look again.',
    ],
    ids=[
        "declined",
        "options",
        "two-objects",
        "prose-then-fence",
        "fence-then-prose",
        "two-fences",
        "inline",
        "prose-before-only",
        "call-then-thinking",
        "call-between-two-thinkings",
        "two-thinkings-first",
        "call-then-cut-thought",
        "two-tool-call-blocks",
        "prose-then-tool-call",
        "words-on-the-fence-line",
        "open-fence-then-prose",
        "open-tool-call-then-prose",
    ],
)
def test_text_is_a_call_only_when_it_is_nothing_but_the_call(text: str) -> None:
    """The fallback took the first JSON object anywhere in the answer that named an offered tool,
    so a call the LLM quoted in order to decline it, or the first of several it listed, ran as
    its choice. Text is a call only when the answer is nothing but the call: one JSON object,
    optionally inside one code fence or one `<tool_call>` block, after an optional thinking block.
    Anything else is no call, a thought after the call or a second one before it among them, and
    the LLM is told so and asked again. Prose before the call with nothing after it is a case of
    its own: the decode refuses whatever follows an object, and only the check on what comes
    before it refuses this one."""
    assert parse_tool_call_from_text(text, NAMES) is None


CALL = '{"name": "walk_to", "arguments": {"target": "ball"}}'


@pytest.mark.parametrize(
    "text",
    [
        f"```JSON\n{CALL}\n```",
        f"``` json\n{CALL}\n```",
        f"```jsonc\n{CALL}\n```",
        f"```js\n{CALL}\n```",
        f"```javascript\n{CALL}\n```",
        f"```json{CALL}```",
        f"<think>plan it</think>\n{CALL}",
        'Walk there, since {"name": "kick", "arguments": {}} is too close.\n</think>\n\n' + CALL,
        f"<tool_call>\n{CALL}\n</tool_call>",
        f"<think>plan it</think><tool_call>{CALL}</tool_call>",
        chr(0xFEFF) + CALL,
        chr(0x200B) + CALL + chr(0x200B) + "\n",
    ],
    ids=[
        "fence-JSON",
        "fence-space-json",
        "fence-jsonc",
        "fence-js",
        "fence-javascript",
        "fence-json-on-its-line",
        "thinking-before",
        "thinking-the-template-opened",
        "tool-call-block",
        "thinking-then-tool-call-block",
        "byte-order-mark",
        "zero-width-spaces",
    ],
)
def test_one_call_alone_is_a_call_whatever_it_is_wrapped_in(text: str) -> None:
    """The rule allows one fence and sets no language, so a fence whose opening line says `JSON`,
    ` json`, `jsonc` or `js` holds the call as one saying `json` does: only `json` was taken. A
    template that writes `<think>` itself leaves only the closing tag in the answer, and
    everything up to it is the thinking block, so the call after it is read, and an object the
    reasoning quoted is not. Hermes and Qwen templates wrap a call in one `<tool_call>` block when
    the server's tool parser does not take it, the strongest mark of intent there is, which main
    took and round one refused. And a byte order mark or a zero-width character round the call is
    trimmed as whitespace is."""
    assert parse_tool_call_from_text(text, NAMES) == ToolCall(
        name="walk_to", arguments={"target": "ball"}
    )


@pytest.mark.parametrize(
    "text",
    [
        f"````json\n{CALL}\n````",
        f"~~~\n{CALL}\n~~~",
        f"```{{.json}}\n{CALL}\n```",
        f"```json\n{CALL}",
        f"<tool_call>\n{CALL}",
    ],
    ids=["four-backticks", "tildes", "pandoc-info-string", "open-fence", "open-tool-call-block"],
)
def test_a_rare_wrapping_of_one_call_alone_is_a_call(text: str) -> None:
    """Forms main ran that both rounds refused, rare ones: a fence of four backticks or of
    tildes, which Markdown allows, an opening line in pandoc's attribute form, and a fence or a
    `<tool_call>` block a server left open at the very end of the answer, cut at its stop word.
    Each is still nothing but the call, and the decode still refuses anything after the object."""
    assert parse_tool_call_from_text(text, NAMES) == ToolCall(
        name="walk_to", arguments={"target": "ball"}
    )


@pytest.mark.parametrize(
    "said", ["the tag is </think>, then the answer", "</think>"], ids=["in-a-sentence", "alone"]
)
async def test_a_bare_call_whose_string_holds_a_closing_think_tag_is_a_call_shown_whole(
    said: str,
) -> None:
    """A template-opened thinking block is everything up to a first `</think>` with no `<think>`
    before it, and that rule cut a bare call in two where one of its strings held the tag, a pilot
    asked to say what the tags are, and refused it: main and round one ran it. The whole answer is
    read as a call first now, and only an answer that is not one is read past such a tag. The
    display leaves it whole too: an answer that opens as a call is no thinking."""
    text = '{"name": "say", "arguments": {"text": "' + said + '"}}'
    tools = [
        {
            "name": "say",
            "description": "d",
            "input_schema": {"type": "object", "properties": {"text": {"type": "string"}}},
        }
    ]
    p = LocalProvider("m", preset="llamacpp", client=FakeClient(reply(text=text)))
    turn = await p.step("S", history(), tools)
    assert turn.tool_calls == [ToolCall(id="text-1", name="say", arguments={"text": said})]
    assert turn.text == text and turn.thinking is None


async def test_a_thinking_block_the_template_opened_is_split_off_the_answer() -> None:
    """An answer whose thinking the chat template opened has only the closing tag. Split there,
    the reasoning goes to `thinking` and the call stays as the answer; left in the text it is
    recorded as what the LLM said and handed back to it as its own words next turn, which is the
    thing the split exists to stop."""
    text = "The ball is ahead, so walk to it.\n</think>\n\n" + CALL
    p = LocalProvider("m", preset="llamacpp", client=FakeClient(reply(text=text)))
    turn = await p.step("S", history(), TOOLS)
    assert turn.thinking == "The ball is ahead, so walk to it."
    assert turn.text == CALL
    assert turn.tool_calls == [ToolCall(id="text-1", name="walk_to", arguments={"target": "ball"})]


@pytest.mark.parametrize(
    "text",
    [
        CALL + "<think>no: the ball is past the wall</think>",
        "<think>a</think><think>b</think>" + CALL,
    ],
    ids=["thought-after-the-call", "two-thoughts-before-it"],
)
async def test_a_call_with_more_thinking_round_it_than_one_block_before_is_not_run(
    text: str,
) -> None:
    """The provider split every thinking block out of the answer, wherever it stood, before the
    fallback read what was left, so a call with a thought after it, perhaps the thought that
    rejects it, or with a second thought before it, came back as the bare call and ran. The
    fallback reads the answer as it came now, and the display still gets its thinking split."""
    p = LocalProvider("m", preset="llamacpp", client=FakeClient(reply(text=text)))
    turn = await p.step("S", history(), TOOLS)
    assert turn.tool_calls == [] and p.text_fallbacks == 0
    assert turn.not_a_call == NOT_A_BARE_CALL and turn.thinking


async def test_a_verb_that_takes_no_parameters_is_called_with_none_without_arguments() -> None:
    """`{"name": "stop"}` for a verb whose schema has no parameters leaves nothing for a default
    to fill in, and main ran it; round one asked it again, as it rightly does a verb that takes
    some. Through the provider, the tools' own schemas say which is which."""
    tools = [
        {
            "name": "walk_to",
            "description": "d",
            "input_schema": {"type": "object", "properties": {"target": {"type": "string"}}},
        },
        {"name": "stop", "description": "d", "input_schema": {"type": "object", "properties": {}}},
    ]
    stop, walk = [
        await LocalProvider("m", preset="llamacpp", client=FakeClient(reply(text=text))).step(
            "S", history(), tools
        )
        for text in ('{"name": "stop"}', '{"name": "walk_to"}')
    ]
    assert stop.tool_calls == [ToolCall(id="text-1", name="stop", arguments={})]
    assert walk.tool_calls == [] and walk.not_a_call == NOT_A_BARE_CALL


def test_one_object_naming_a_tool_not_offered_is_told_so_in_its_own_words() -> None:
    """`{"name": "fly", "arguments": {}}` is one object in the hint's shape, naming nothing the LLM
    was given. It was told it was not one object in that shape, which it was, so it sent the same
    object again and the run ended on its second answer. It is told the tool is not one of its
    own now, and the record keeps those words as the reason."""
    told = read_call('{"name": "fly", "arguments": {}}', NAMES)
    assert told == NOT_OFFERED.format(name='"fly"')
    assert read_call('{"name": "fly\\nlast verb", "arguments": {}}', NAMES) == (
        NOT_OFFERED.format(name='"fly\\nlast verb"')
    ), "the name is quoted, so nothing in it starts a line"


class CountingJson:
    """`json` as local.py reads it, counting every decode and the characters each was handed, so
    a test asserts on the work done rather than on how fast this machine does it."""

    JSONDecodeError = json.JSONDecodeError

    def __init__(self) -> None:
        self.decodes = 0
        self.chars = 0

    def loads(self, s: str, *args: Any, **kwargs: Any) -> Any:
        self.decodes += 1
        self.chars += len(s)
        return json.loads(s, *args, **kwargs)

    def JSONDecoder(self) -> json.JSONDecoder:
        counter = self

        class Counting(json.JSONDecoder):
            def raw_decode(self, s: str, idx: int = 0) -> tuple[Any, int]:
                counter.decodes += 1
                counter.chars += len(s) - idx
                return super().raw_decode(s, idx)

        return Counting()


DEEP = 3 * sys.getrecursionlimit()
"""Nesting the decoder refuses on both Pythons: 3.11 counts against the recursion limit and
3.12 against its own C limit, which depth 3000 passes on both."""


@pytest.mark.parametrize(
    "text",
    [
        "Options: "
        + " or ".join(f'{{"name": "kick", "arguments": {{"n": {i}}}}}' for i in range(3000)),
        "".join(f'{{"name": "kick", "arguments": {{"n": {i}}}}}' for i in range(3000)),
        '{"a":' * DEEP,
        "```{" * 3000,
    ],
    ids=["objects-in-prose", "objects-back-to-back", "unclosed-nesting", "unclosed-fences"],
)
def test_the_fallback_decodes_once_at_most_and_never_past_its_bound(
    text: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The fallback decoded from every `{` in the answer and checked each object against a list of
    all those before it, so a repetition loop's thousands of objects, or thousands of openings
    never closed, held the event loop for seconds, growing with the square of the answer. Now an
    answer is decoded once at most, and never more of it than `MAX_CALL_CHARS`."""
    counter = CountingJson()
    monkeypatch.setattr(local, "json", counter)
    assert parse_tool_call_from_text(text, NAMES) is None
    assert counter.decodes <= 1 and counter.chars <= MAX_CALL_CHARS, (
        f"{counter.decodes} decodes over {counter.chars} characters of {len(text)} in the answer"
    )


def test_a_call_longer_than_any_call_needs_is_not_read(monkeypatch: pytest.MonkeyPatch) -> None:
    """The bound is on what a call can need, so a bare call within it is read and one past it is
    not even decoded: it is no call, and the LLM is asked again."""
    counter = CountingJson()
    monkeypatch.setattr(local, "json", counter)
    head = '{"name": "walk_to", "arguments": {"target": "'
    fits = head + "x" * (MAX_CALL_CHARS - len(head) - 3) + '"}}'
    assert len(fits) == MAX_CALL_CHARS
    assert parse_tool_call_from_text(fits, NAMES) is not None
    assert parse_tool_call_from_text(" " + fits[:-2] + 'x"}}', NAMES) is None
    assert counter.decodes == 1


@pytest.mark.parametrize(
    "text",
    [
        '{"a": ' + "1" * (sys.get_int_max_str_digits() + 1) + "}",
        '{"name": "walk_to", "arguments": {"a":' + "[" * DEEP + "]" * DEEP + "}}",
        '{"a":' * DEEP,
    ],
    ids=["long-integer", "deep-nesting", "deep-unclosed"],
)
async def test_text_the_decoder_raises_on_is_no_call_and_never_leaves_the_provider(
    text: str,
) -> None:
    """Python's json raises `ValueError` for an integer past the interpreter's digit limit and
    `RecursionError` for nesting past its recursion limit, and the fallback caught only
    `JSONDecodeError`. It runs outside the provider's try, so one odd answer from a local LLM
    left the provider raw and ended the run in an error. Now such text is no call, and the LLM
    is told so and asked again."""
    assert parse_tool_call_from_text(text, NAMES) is None
    p = LocalProvider("m", preset="llamacpp", client=FakeClient(reply(text=text)))
    turn = await p.step("S", history(), TOOLS)
    assert turn.tool_calls == [] and turn.not_a_call == NOT_A_BARE_CALL


def test_the_thinking_is_split_off_in_one_pass_over_the_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A lazy pattern looked for `</think>` from every `<think>` to the end of the answer, twice,
    so thousands of openings never closed cost the square of their number. The split now reads the
    answer a bounded number of times whatever is in it, and splits exactly as it did."""
    scanned = [0]

    def counting(text: str, needle: str, start: int) -> int:
        found = text.find(needle, start)
        scanned[0] += (len(text) if found == -1 else found + len(needle)) - start
        return found

    monkeypatch.setattr(local, "_find", counting)
    openings = "<think>" * 3000
    mixed = "<think>a</think>b" * 1000 + "<think>" * 2000
    for text in (openings, mixed):
        scanned[0] = 0
        split_thinking(text)
        assert scanned[0] <= 3 * len(text), f"{scanned[0]} characters read for {len(text)}"
    assert split_thinking("<think>" * 3) == ("<think><think>", "")
    assert split_thinking("<think>a</think>b<think>c") == ("a\n\nc", "b")
    # a closing tag with no opening before it ends a block the chat template opened
    assert split_thinking("x</think>y<think>z</think>") == ("x\n\nz", "y")


@pytest.mark.parametrize(
    "args",
    [
        "[1, 2]",
        '"[1, 2]"',
        '"{broken"',
        "7",
        '"text"',
        "null",
        '"{\\"a\\": ' + "1" * (sys.get_int_max_str_digits() + 1) + '}"',
    ],
)
def test_arguments_it_cannot_read_are_kept_for_the_gate(args: str) -> None:
    """Not `{}`, which is the call with its defaults, on nothing the LLM said. The gate refuses
    them and tells the pilot, as for every other vendor. An argument string the decoder raises on,
    an integer past the interpreter's digit limit, goes the same way (`tool_arguments`)."""
    call = parse_tool_call_from_text(f'{{"name": "walk_to", "arguments": {args}}}', NAMES)
    assert call is not None and list(call.arguments) == ["_unparsed"], call


@pytest.mark.parametrize("text", ['{"name": "walk_to"}', '{"tool": "kick"}'])
def test_an_object_with_no_arguments_is_no_call_with_its_defaults(text: str) -> None:
    """With no arguments key the call came back with `{}`, which is the call with its defaults,
    on nothing the LLM said, against the comment written beside it: `walk_to` went with whatever
    its defaults are. The hint asks for an `arguments` object, so an object without one is not
    the call it describes, and the LLM is asked again. An empty object written out is a call with
    none, as it is on every vendor."""
    assert parse_tool_call_from_text(text, NAMES) is None
    written = '{"name": "kick", "arguments": {}}'
    assert parse_tool_call_from_text(written, NAMES) == ToolCall(name="kick", arguments={})


async def test_text_fallback_synthesizes_a_tool_call() -> None:
    text = '```json\n{"name": "walk_to", "arguments": {"target": "ball"}}\n```'
    p = LocalProvider("m", preset="llamacpp", client=FakeClient(reply(text=text)))
    turn = await p.step("S", history(), TOOLS)
    assert turn.stop_reason == "text_fallback" and p.text_fallbacks == 1
    assert turn.tool_calls == [ToolCall(id="text-1", name="walk_to", arguments={"target": "ball"})]


async def test_no_fallback_when_nothing_parses() -> None:
    p = LocalProvider("m", preset="llamacpp", client=FakeClient(reply(text="I am a duck.")))
    turn = await p.step("S", history(), TOOLS)
    assert turn.tool_calls == [] and turn.stop_reason == "stop"


async def test_a_walk_the_local_llm_quoted_to_decline_it_is_not_run_and_it_is_told_why(
    hello_glot: GlotFile, tmp_path: Path
) -> None:
    """A local LLM answers in prose that quotes a walk in order to decline it. The fallback took
    the quote for its choice and the body walked. Now nothing runs, the LLM is told that its text
    was not a call and what one is, in the hint's own words, and is asked again, and the record
    keeps the reason beside the re-prompt. Its native calls run as they always did."""
    declined = 'I should not use {"name": "walk", "arguments": {"vx": 0.3}} here, so I stop.'
    verdict = NS(
        id="v1", function=NS(name=AUTO_VERDICT.name, arguments=json.dumps(AUTO_VERDICT.arguments))
    )
    done = NS(id="d1", function=NS(name="declare_failure", arguments='{"reason": "declined"}'))
    replies = [reply(tool_calls=[verdict]), reply(text=declined), reply(tool_calls=[done])]

    async def create(**kwargs: Any) -> Any:
        return replies.pop(0)

    client = FakeClient()
    client.chat = NS(completions=NS(create=create))
    provider = LocalProvider("m", preset="llamacpp", client=client)
    transport = MockTransport()
    result = await run_glot(
        RunConfig(glot=hello_glot, provider=provider, transport=transport, runs_dir=tmp_path)
    )
    # The pilot is warmed in a daemon thread of its own (`loop._Warming`), and on Windows its
    # platform question is a WMI query: one still running when the interpreter exits crashed it,
    # after every test had passed. So this test waits for it to end before it returns.
    for thread in threading.enumerate():
        if thread.name == "roboglot-pilot-warm":
            thread.join(timeout=WARM_UP_ENDS_S)
            assert not thread.is_alive(), "the pilot's warm-up never ended"
    assert not transport.intents_of("move"), "the walk the LLM declined moved the body"
    assert provider.text_fallbacks == 0
    assert result.outcome == "failure" and result.reason == "declined", result.reason
    events = Transcript.read(result.run_dir / "transcript.jsonl")
    enforce = [(e["issue"], e["text"], e["reason"]) for e in events if e["kind"] == "enforce"]
    assert enforce == [("no_tool_call", f"{NOT_A_BARE_CALL} {REPROMPT}", NOT_A_BARE_CALL)]
    assert "exactly one JSON object and nothing else" in NOT_A_BARE_CALL
    assert "exactly one JSON object and nothing else" in TOOL_HINT


async def test_a_tool_call_the_llm_only_contemplated_in_its_thinking_is_not_executed() -> None:
    """A small LLM weighs a verb out loud and rejects it. The fallback scans plain text, so
    unless the reasoning is stripped first it finds the JSON in the discarded thought and the
    duck kicks."""
    text = '<think>maybe {"name": "kick", "arguments": {}} but no</think>I will wait.'
    p = LocalProvider("m", preset="llamacpp", client=FakeClient(reply(text=text)))
    turn = await p.step("S", history(), TOOLS)
    assert turn.tool_calls == [] and p.text_fallbacks == 0
    assert turn.text == "I will wait."
    assert turn.thinking == 'maybe {"name": "kick", "arguments": {}} but no'


@pytest.mark.parametrize(
    ("content", "refusal", "finish"),
    [
        # the refusal's words are the bare call, which the fallback would take if it read them
        (None, '{"name": "kick", "arguments": {}}', "stop"),
        (None, '{"name": "kick", "arguments": {}}', "content_filter"),
        # a refusal stops on whatever the vendor named, and is still a refusal
        (None, '{"name": "kick", "arguments": {}}', "length"),
        (None, '{"name": "kick", "arguments": {}}', "tool_calls"),
        ('{"name": "kick", "arguments": {}}', None, "content_filter"),
    ],
    ids=["refusal", "refusal-filtered", "refusal-cut-short", "refusal-says-calls", "filtered"],
)
async def test_a_call_written_into_a_vendors_no_is_not_executed(
    content: str | None, refusal: str | None, finish: str
) -> None:
    """A refusal's words reach the turn's text, which the fallback scans: a call the vendor
    quoted while saying no, or that a filter cut off, would otherwise be parsed out and run."""
    message = NS(content=content, tool_calls=None, refusal=refusal)
    response = NS(
        choices=[NS(message=message, finish_reason=finish)],
        usage=NS(prompt_tokens=10, completion_tokens=5),
    )
    assert parse_tool_call_from_text(content or refusal or "", NAMES) is not None
    p = LocalProvider("m", preset="llamacpp", client=FakeClient(response))
    turn = await p.step("S", history(), TOOLS)
    assert turn.tool_calls == [] and p.text_fallbacks == 0


async def test_a_local_answer_the_output_limit_cut_off_is_not_read_for_a_call() -> None:
    """A local server ends an answer the output limit cut off on `length`. Its text was read for a
    call like any other, so a call written before the cut ran as a finished choice. Now it is
    not read, however whole its JSON looks: the turn reaches the loop as cut off, with no call,
    and the loop asks again."""
    response = NS(
        choices=[
            NS(
                message=NS(content='{"name": "kick", "arguments": {}}', tool_calls=None),
                finish_reason="length",
            )
        ],
        usage=NS(prompt_tokens=10, completion_tokens=5),
    )
    p = LocalProvider("m", preset="llamacpp", client=FakeClient(response))
    turn = await p.step("S", history(), TOOLS)
    assert turn.tool_calls == [] and p.text_fallbacks == 0
    assert turn.cut_off and turn.stop_reason == "length"


async def test_the_json_fallback_reads_the_answer_after_the_thinking() -> None:
    text = '<think>kick? no</think>{"name": "walk_to", "arguments": {"target": "ball"}}'
    p = LocalProvider("m", preset="llamacpp", client=FakeClient(reply(text=text)))
    turn = await p.step("S", history(), TOOLS)
    assert turn.tool_calls == [ToolCall(id="text-1", name="walk_to", arguments={"target": "ball"})]
    assert turn.thinking == "kick? no"


async def test_an_unterminated_think_tag_is_thinking_to_the_end_not_the_answer() -> None:
    """max_tokens can cut a reply mid-thought, leaving no `</think>`. Everything after the
    opening tag is still reasoning: shown as the answer it would be replayed next turn as
    something the LLM said, and any verb it was still weighing would be executed."""
    assert split_thinking("<think>the ball is") == ("the ball is", "")
    assert split_thinking("ok<think>cut") == ("cut", "ok")
    assert split_thinking("<think>a</think>b<think>c") == ("a\n\nc", "b")
    text = '<think>I could {"name": "kick", "arguments": {}}'
    p = LocalProvider("m", preset="llamacpp", client=FakeClient(reply(text=text)))
    turn = await p.step("S", history(), TOOLS)
    assert turn.tool_calls == [] and turn.text is None
    assert turn.thinking == 'I could {"name": "kick", "arguments": {}}'


def test_prompt_hint_only_for_local() -> None:
    assert "JSON object" in LocalProvider("m", preset="ollama", client=FakeClient()).prompt_hint
    assert OpenAIProvider("gpt-5", client=FakeClient()).prompt_hint == ""


# ── factory ─────────────────────────────────────────────────────────────────────────────


def test_factory_builds_local_presets(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("roboglot.agent.providers.local.LocalProvider.__init__", _record_init)
    p = make_provider(
        "vllm",
        llm_id="Qwen/Qwen3-8B",
        base_url="http://gpu:8000/v1",
        vision=True,
        extra_body='{"chat_template_kwargs": {"enable_thinking": false}}',
    )
    assert p.recorded == {  # type: ignore[attr-defined]
        "model": "Qwen/Qwen3-8B",
        "preset": "vllm",
        "base_url": "http://gpu:8000/v1",
        "api_key": None,
        "vision": True,
        # the flag arrives as text and the factory hands the provider the parsed object
        "extra_body": {"chat_template_kwargs": {"enable_thinking": False}},
    }


def _record_init(self: Any, llm_id: Any = None, **kw: Any) -> None:
    self.recorded = {
        "model": llm_id,
        **{k: kw.get(k) for k in ("preset", "base_url", "api_key", "vision", "extra_body")},
    }
    self.name = kw.get("preset", "local")
    self.llm_id = llm_id or ""
    self.supports_vision = bool(kw.get("vision"))


def test_openai_history_json_roundtrip_unchanged() -> None:
    # sanity: the local provider still renders OpenAI-shaped messages
    client = FakeClient(reply(text="{}"))
    p = LocalProvider("m", preset="ollama", client=client)
    params = p._params("SYS", history(), TOOLS)
    assert params["messages"][0] == {"role": "system", "content": "SYS"}
    assert json.dumps(params["tools"])  # serialisable
