"""Local and open-source providers: presets, no key, relaxed tool calling, text fallback."""

from __future__ import annotations

import json
from types import SimpleNamespace as NS
from typing import Any

import pytest

from quackd.agent.providers.base import (
    Exchange,
    NamedPng,
    Observation,
    ProviderError,
    ToolCall,
)
from quackd.agent.providers.factory import LOCAL_NAMES, PROVIDER_NAMES, make_provider
from quackd.agent.providers.local import (
    PRESETS,
    LocalProvider,
    on_host,
    parse_tool_call_from_text,
    split_thinking,
)
from quackd.agent.providers.openai import OpenAIProvider

TOOLS = [
    {"name": "walk_to", "description": "d", "input_schema": {"type": "object", "properties": {}}},
    {"name": "kick", "description": "d", "input_schema": {"type": "object", "properties": {}}},
]
NAMES = {"walk_to", "kick"}


class FakeClient:
    def __init__(self, response: Any = None, models: list[str] | None = None) -> None:
        self.kwargs: dict[str, Any] = {}
        self.listed = 0

        async def create(**kwargs: Any) -> Any:
            self.kwargs = kwargs
            return response

        async def list_models() -> Any:
            self.listed += 1
            return NS(data=[NS(id=m) for m in (models or [])])

        self.chat = NS(completions=NS(create=create))
        self.models = NS(list=list_models)


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
    monkeypatch.delenv("QUACKD_BASE_URL", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    with pytest.raises(ProviderError, match="--base-url"):
        LocalProvider("m", preset="local", client=FakeClient())
    p = LocalProvider("m", preset="local", base_url="http://gpu:8000/v1", client=FakeClient())
    assert p.base_url == "http://gpu:8000/v1" and p.name == "local"


def test_env_base_url_overrides_preset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("QUACKD_BASE_URL", "http://box:9999/v1")
    assert LocalProvider("m", preset="ollama", client=FakeClient()).base_url == "http://box:9999/v1"


# ── where the server is: the address ladder, and --host moving a preset ─────────────────

_ADDRESS_ENV = ("QUACKD_BASE_URL", "OPENAI_BASE_URL", "QUACKD_HOST")


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
    ("QUACKD_BASE_URL", "http://quackd-env:2/v1", "http://quackd-env:2/v1"),
    ("OPENAI_BASE_URL", "http://openai-env:3/v1", "http://openai-env:3/v1"),
    ("QUACKD_HOST", "env-board", "http://env-board:11434/v1"),
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
    url = _address(monkeypatch, base_url="http://typed:1/v1", QUACKD_BASE_URL="http://env:2/v1")
    assert url == "http://typed:1/v1"


def test_a_host_named_for_the_run_beats_quackd_base_url_and_one_from_the_environment_does_not(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The two hosts sit on different rungs on purpose. `--host` (or the robot's own) is a
    decision about this run and moves the preset past a model server's URL in `.env`;
    `QUACKD_HOST` is the board you usually use, and a URL in the same `.env` is more exact."""
    typed = _address(monkeypatch, host="jetson.local", QUACKD_BASE_URL="http://env:2/v1")
    assert typed == "http://jetson.local:11434/v1"
    usual = _address(monkeypatch, QUACKD_HOST="jetson.local", QUACKD_BASE_URL="http://env:2/v1")
    assert usual == "http://env:2/v1"


def test_a_blank_rung_is_no_rung(monkeypatch: pytest.MonkeyPatch) -> None:
    """`QUACKD_HOST=` in a `.env` is a shell saying unset, and a blank flag is nothing typed.
    Neither may become a machine named "" in the model server's URL."""
    assert _address(monkeypatch, host="  ", QUACKD_HOST="") == "http://localhost:11434/v1"


def test_every_preset_moves_to_the_host_with_its_own_port_and_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The port in `--host` is the daemon's. A model server on the board listens where it
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


def test_a_bad_quackd_host_is_one_line_that_names_the_variable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every `parse_host` sentence says `--host`, and somebody who never typed one would look
    for it on the wrong line. A ProviderError, because that is what every caller of a
    provider's constructor prints as one line rather than a traceback."""
    with pytest.raises(ProviderError, match=r"^QUACKD_HOST: --host takes a machine, not a URL"):
        _address(monkeypatch, QUACKD_HOST="http://jetson.local")
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
        _address(monkeypatch, preset="local", QUACKD_HOST="jetson.local")
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

    monkeypatch.setattr("quackd.agent.providers.local.LocalProvider", recorder("local"))
    monkeypatch.setattr("quackd.agent.providers.openai.OpenAIProvider", recorder("openai"))
    make_provider("ollama", model="m", host="jetson.local")
    assert seen["local"]["host"] == "jetson.local"
    make_provider("openai", host="jetson.local")
    assert "host" not in seen["openai"], "a vendor's API has no localhost to move"


def test_no_key_needed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LOCAL_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    p = LocalProvider("m", preset="ollama", client=FakeClient())
    assert p._fallback_key() == "not-needed"
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(ProviderError):
        OpenAIProvider("m")  # cloud still requires a key


# ── request params ──────────────────────────────────────────────────────────────────────


async def test_local_sends_relaxed_tool_params(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("QUACKD_TOOL_CHOICE", raising=False)
    client = FakeClient(reply(tool_calls=[NS(id="c1", function=NS(name="kick", arguments="{}"))]))
    turn = await LocalProvider("m", preset="ollama", client=client).step("S", history(), TOOLS)
    assert client.kwargs["tool_choice"] == "auto"
    assert "parallel_tool_calls" not in client.kwargs
    assert turn.tool_calls[0].name == "kick"


async def test_tool_choice_none_omits_the_field(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("QUACKD_TOOL_CHOICE", "none")
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
    monkeypatch.delenv("QUACKD_VISION", raising=False)
    assert LocalProvider("m", preset="ollama", client=FakeClient()).supports_vision is False
    assert LocalProvider("m", preset="ollama", client=FakeClient(), vision=True).supports_vision
    monkeypatch.setenv("QUACKD_VISION", "1")
    assert LocalProvider("m", preset="ollama", client=FakeClient()).supports_vision is True


async def test_every_camera_reaches_a_local_server_in_one_message() -> None:
    """A local server speaks the OpenAI wire format, so it inherits multi-camera rendering
    without a line of its own here. That inheritance is the thing worth pinning: the one
    provider quackd cannot test against a real endpoint is the one most likely to be handed
    two pictures by somebody with a webcam on each side of an arm.

    It is also where the cost lands. Two cameras is two image parts in a single message, and
    `docs/guides/local-llms.md` warns that a server or a model may accept only one. This says what
    quackd sends; whether a given server takes it is that server's business."""
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


# ── model discovery ─────────────────────────────────────────────────────────────────────


async def test_model_discovery_takes_first_served_model() -> None:
    client = FakeClient(reply(text="{}"), models=["qwen3:8b", "llama3.2"])
    p = LocalProvider(None, preset="ollama", client=client)
    await p.step("S", history(), TOOLS)
    assert p.model == "qwen3:8b" and client.kwargs["model"] == "qwen3:8b"
    await p.step("S", history(), TOOLS)
    assert client.listed == 1  # discovered once


async def test_model_discovery_with_empty_server_is_clear() -> None:
    p = LocalProvider(None, preset="ollama", client=FakeClient(reply(text="{}"), models=[]))
    with pytest.raises(ProviderError, match="lists no models"):
        await p.step("S", history(), TOOLS)


# ── text fallback ───────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "text",
    [
        '{"name": "walk_to", "arguments": {"target": "ball"}}',
        'Sure! ```json\n{"name": "walk_to", "arguments": {"target": "ball"}}\n```',
        '{"tool": "walk_to", "parameters": {"target": "ball"}}',
        '{"function": {"name": "walk_to", "arguments": "{\\"target\\": \\"ball\\"}"}}',
        'I will approach. {"name":"walk_to","args":{"target":"ball"}} Then kick.',
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


async def test_text_fallback_synthesizes_a_tool_call() -> None:
    text = 'Let me walk there.\n```json\n{"name": "walk_to", "arguments": {"target": "ball"}}\n```'
    p = LocalProvider("m", preset="llamacpp", client=FakeClient(reply(text=text)))
    turn = await p.step("S", history(), TOOLS)
    assert turn.stop_reason == "text_fallback" and p.text_fallbacks == 1
    assert turn.tool_calls == [ToolCall(id="text-1", name="walk_to", arguments={"target": "ball"})]


async def test_no_fallback_when_nothing_parses() -> None:
    p = LocalProvider("m", preset="llamacpp", client=FakeClient(reply(text="I am a duck.")))
    turn = await p.step("S", history(), TOOLS)
    assert turn.tool_calls == [] and turn.stop_reason == "stop"


async def test_a_tool_call_the_model_only_contemplated_in_its_thinking_is_not_executed() -> None:
    """A small model weighs a verb out loud and rejects it. The fallback scans plain text, so
    unless the reasoning is stripped first it finds the JSON in the discarded thought and the
    duck kicks."""
    text = '<think>maybe {"name": "kick", "arguments": {}} but no</think>I will wait.'
    p = LocalProvider("m", preset="llamacpp", client=FakeClient(reply(text=text)))
    turn = await p.step("S", history(), TOOLS)
    assert turn.tool_calls == [] and p.text_fallbacks == 0
    assert turn.text == "I will wait."
    assert turn.thinking == 'maybe {"name": "kick", "arguments": {}} but no'


async def test_the_json_fallback_reads_the_answer_after_the_thinking() -> None:
    text = '<think>kick? no</think>{"name": "walk_to", "arguments": {"target": "ball"}}'
    p = LocalProvider("m", preset="llamacpp", client=FakeClient(reply(text=text)))
    turn = await p.step("S", history(), TOOLS)
    assert turn.tool_calls == [ToolCall(id="text-1", name="walk_to", arguments={"target": "ball"})]
    assert turn.thinking == "kick? no"


async def test_an_unterminated_think_tag_is_thinking_to_the_end_not_the_answer() -> None:
    """max_tokens can cut a reply mid-thought, leaving no `</think>`. Everything after the
    opening tag is still reasoning: shown as the answer it would be replayed next turn as
    something the model said, and any verb it was still weighing would be executed."""
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
    monkeypatch.setattr("quackd.agent.providers.local.LocalProvider.__init__", _record_init)
    p = make_provider(
        "vllm",
        model="Qwen/Qwen3-8B",
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


def _record_init(self: Any, model: Any = None, **kw: Any) -> None:
    self.recorded = {
        "model": model,
        **{k: kw.get(k) for k in ("preset", "base_url", "api_key", "vision", "extra_body")},
    }
    self.name = kw.get("preset", "local")
    self.model = model or ""
    self.supports_vision = bool(kw.get("vision"))


def test_openai_history_json_roundtrip_unchanged() -> None:
    # sanity: the local provider still renders OpenAI-shaped messages
    client = FakeClient(reply(text="{}"))
    p = LocalProvider("m", preset="ollama", client=client)
    params = p._params("SYS", history(), TOOLS)
    assert params["messages"][0] == {"role": "system", "content": "SYS"}
    assert json.dumps(params["tools"])  # serialisable
