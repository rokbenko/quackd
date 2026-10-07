"""Provider request/response mapping against stubbed SDK clients. No network, no SDKs."""

from __future__ import annotations

import base64
import json
import re
from collections.abc import Callable
from types import SimpleNamespace as NS
from typing import Any

import pytest

from quackd.agent.providers.anthropic import (
    ADAPTIVE_REFUSED,
    BINDING_BETA,
    BINDING_TRIM_PERIOD,
    FALLBACK_BETA,
    AnthropicProvider,
)
from quackd.agent.providers.anthropic import render_messages as a_messages
from quackd.agent.providers.base import (
    Decision,
    Exchange,
    NamedPng,
    Observation,
    ProviderError,
    ToolCall,
)
from quackd.agent.providers.catalogue import default_model_for, find_model, models_for
from quackd.agent.providers.factory import make_provider
from quackd.agent.providers.gemini import (
    UNSUPPORTED_SCHEMA_KEYS,
    GeminiProvider,
    clean_schema,
    render_contents,
)
from quackd.agent.providers.grok import GrokProvider
from quackd.agent.providers.openai import OpenAIProvider, parse_extra_body
from quackd.agent.providers.openai import render_input as o_input
from quackd.agent.providers.openai import render_messages as o_messages
from quackd.agent.providers.openrouter import OpenRouterProvider
from quackd.verbs.registry import default_registry

PNG = b"\x89PNG\r\n\x1a\nfake"
PNG_TOP = b"\x89PNG\r\n\x1a\ntop"
PNG_SIDE = b"\x89PNG\r\n\x1a\nside"
#: Pictures that came with the task (`quackd run --image`) rather than with a step. Distinct
#: from every camera's bytes so a label in front of the wrong picture fails an assertion.
PNG_SKETCH = b"\x89PNG\r\n\x1a\nsketch"
PNG_PLAN = b"\x89PNG\r\n\x1a\nplan"
TOOLS = [
    {
        "name": "walk",
        "description": "Walk.",
        "input_schema": {
            "type": "object",
            "properties": {"vx": {"type": "number", "default": 0.1, "title": "Vx"}},
            "additionalProperties": False,
        },
    }
]


def history() -> list[Exchange]:
    first = Exchange(
        observation=Observation(text="obs 1", images=[NamedPng(name="camera", png=PNG)]),
        decision=Decision(
            tool_call=ToolCall(id="call-1", name="walk", arguments={"vx": 0.1}), text="going"
        ),
    )
    second = Exchange(
        observation=Observation(
            text="obs 2 (result)",
            images=[NamedPng(name="camera", png=PNG)],
            tool_call_id="call-1",
        )
    )
    return [first, second]


def two_camera_history() -> list[Exchange]:
    """The same two turns as `history`, from a body whose cameras are `top` and `side`.

    The two PNGs differ so a renderer that put the right labels in front of the wrong
    pictures is caught: swapping them would leave every type and every word in place.
    """
    images = [NamedPng(name="top", png=PNG_TOP), NamedPng(name="side", png=PNG_SIDE)]
    first = Exchange(
        observation=Observation(text="obs 1", images=images),
        decision=Decision(
            tool_call=ToolCall(id="call-1", name="walk", arguments={"vx": 0.1}), text="going"
        ),
    )
    second = Exchange(
        observation=Observation(text="obs 2 (result)", images=images, tool_call_id="call-1")
    )
    return [first, second]


def task_picture_history(*attachments: NamedPng) -> list[Exchange]:
    """One turn from a one-camera body that was also handed pictures with the task.

    Every PNG in here is different, the camera's included, so an assertion on the bytes catches
    a renderer that put the right label in front of the wrong picture: the sketch and the frame
    swapped would leave every part type and every word in place.

    `cameras` is left empty, as it is for a body with one lens, because that is the case the
    labels have to survive. `picture_parts` names the frame anyway once there are attachments,
    so that a bare picture never ends up sitting under `task picture sketch.png:`.
    """
    given = list(attachments) or [NamedPng(name="sketch.png", png=PNG_SKETCH)]
    return [
        Exchange(
            observation=Observation(
                text="obs 1", images=[NamedPng(name="front", png=PNG)], attachments=given
            ),
            decision=Decision(
                tool_call=ToolCall(id="call-1", name="walk", arguments={"vx": 0.1}), text="going"
            ),
        )
    ]


def b64(png: bytes) -> str:
    return base64.standard_b64encode(png).decode("ascii")


def data_url(png: bytes) -> str:
    return f"data:image/png;base64,{b64(png)}"


# ── anthropic ───────────────────────────────────────────────────────────────────────────


class FakeAnthropic:
    """`beta_ok=False` is an SDK that predates the keyword `predates` names on the beta
    endpoint, and refuses it the way Python does, only when it is passed."""

    def __init__(self, response: Any, *, beta_ok: bool = True, predates: str = "fallbacks") -> None:
        self.kwargs: dict[str, Any] = {}
        self.beta_used = False
        self.beta_calls = 0
        self._response = response

        async def plain(**kwargs: Any) -> Any:
            self.kwargs = kwargs
            return self._response

        async def beta(**kwargs: Any) -> Any:
            self.beta_calls += 1
            if not beta_ok and predates in kwargs:
                raise TypeError(f"create() got an unexpected keyword argument '{predates}'")
            self.beta_used = True
            self.kwargs = kwargs
            return self._response

        self.messages = NS(create=plain)
        self.beta = NS(messages=NS(create=beta))


def anthropic_response(*blocks: Any, stop_reason: str = "tool_use", usage: Any = None) -> Any:
    """The default `usage` is the two plain counts an older SDK reports and nothing else.

    A test about the cache buckets hands over a usage object of its own rather than setting a
    field on this one, because Anthropic's three input numbers are read together and what is
    being proved is the arithmetic between them.
    """
    return NS(
        content=list(blocks),
        stop_reason=stop_reason,
        usage=usage if usage is not None else NS(input_tokens=120, output_tokens=30),
        stop_details=None,
    )


async def test_anthropic_request_and_response_mapping() -> None:
    client = FakeAnthropic(
        anthropic_response(
            NS(
                type="thinking",
                thinking="",
                model_dump=lambda: {"type": "thinking", "thinking": "", "signature": "sig"},
            ),
            NS(
                type="tool_use",
                id="toolu_1",
                name="walk",
                input={"vx": 0.2},
                model_dump=lambda: {
                    "type": "tool_use",
                    "id": "toolu_1",
                    "name": "walk",
                    "input": {"vx": 0.2},
                },
            ),
        )
    )
    p = AnthropicProvider(model="claude-opus-5", client=client, effort="medium")
    turn = await p.step("SYS", history(), TOOLS)
    assert client.beta_used
    kw = client.kwargs
    assert kw["model"] == "claude-opus-5" and kw["system"] == "SYS"
    assert kw["tool_choice"] == {"type": "any", "disable_parallel_tool_use": True}
    assert kw["output_config"] == {"effort": "medium"}
    # adaptive is the model's default; `display` is what makes the blocks carry text at all,
    # and without it the log's "what it thought" would be blank on every turn
    assert kw["thinking"] == {"type": "adaptive", "display": "summarized"}
    assert kw["betas"] == ["server-side-fallback-2026-07-01"] and kw["fallbacks"] == "default"
    assert kw["tools"][0]["input_schema"]["additionalProperties"] is False
    msgs = kw["messages"]
    assert [m["role"] for m in msgs] == ["user", "assistant", "user"]
    assert msgs[0]["content"][0]["type"] == "image" and msgs[0]["content"][1]["text"] == "obs 1"
    assert msgs[1]["content"][-1] == {
        "type": "tool_use",
        "id": "call-1",
        "name": "walk",
        "input": {"vx": 0.1},
    }
    result = msgs[2]["content"][0]
    assert result["type"] == "tool_result" and result["tool_use_id"] == "call-1"
    assert [c["type"] for c in result["content"]] == ["text", "image"]
    assert turn.tool_calls == [ToolCall(id="toolu_1", name="walk", arguments={"vx": 0.2})]
    assert turn.usage.input_tokens == 120 and turn.stop_reason == "tool_use"
    assert turn.raw[0]["type"] == "thinking"  # replayed verbatim next turn


def test_anthropic_names_each_camera_before_its_picture_in_both_kinds_of_turn() -> None:
    """Two pictures in one message are two views of a room with nothing to say which is which.

    Each label sits in front of the picture it names, so the order of the blocks is the whole
    guarantee: a renderer that sent both labels and then both pictures would carry every right
    word and still be wrong. Both branches are checked because a tool result nests its blocks a
    level deeper and builds them in the other order, the text first rather than last.
    """
    msgs = a_messages(two_camera_history())
    plain = msgs[0]["content"]
    assert [b["type"] for b in plain] == ["text", "image", "text", "image", "text"]
    assert plain[0]["text"] == "camera top:" and plain[2]["text"] == "camera side:"
    assert plain[1]["source"]["data"] == b64(PNG_TOP)
    assert plain[3]["source"]["data"] == b64(PNG_SIDE), "the side label kept the side picture"
    assert plain[4]["text"] == "obs 1", "the observation text still closes a plain turn"

    result = msgs[2]["content"][0]
    assert result["type"] == "tool_result" and result["tool_use_id"] == "call-1"
    inner = result["content"]
    assert [b["type"] for b in inner] == ["text", "text", "image", "text", "image"]
    assert inner[0]["text"] == "obs 2 (result)", "the result text still opens the tool result"
    assert inner[1]["text"] == "camera top:" and inner[2]["source"]["data"] == b64(PNG_TOP)
    assert inner[3]["text"] == "camera side:" and inner[4]["source"]["data"] == b64(PNG_SIDE)


def test_anthropic_replays_raw_blocks() -> None:
    ex = Exchange(
        observation=Observation(text="o"),
        decision=Decision(
            tool_call=ToolCall(id="t", name="walk"),
            raw=[
                {"type": "thinking", "thinking": "", "signature": "s"},
                {"type": "tool_use", "id": "t", "name": "walk", "input": {}},
            ],
        ),
    )
    msgs = a_messages([ex])
    assert msgs[1]["content"][0]["type"] == "thinking"


async def test_anthropic_falls_back_to_plain_endpoint_on_old_sdk() -> None:
    client = FakeAnthropic(
        anthropic_response(NS(type="tool_use", id="1", name="walk", input={})), beta_ok=False
    )
    p = AnthropicProvider(model="claude-opus-5", client=client)
    await p.step("S", history()[:1], TOOLS)
    assert not client.beta_used and p.fallbacks is False
    assert "fallbacks" not in client.kwargs


async def test_anthropic_refusal_yields_no_tool_call() -> None:
    resp = anthropic_response(
        NS(type="text", text="no", model_dump=lambda: {"type": "text", "text": "no"}),
        stop_reason="refusal",
    )
    resp.stop_details = NS(category="x", explanation="policy")
    p = AnthropicProvider(client=FakeAnthropic(resp))
    turn = await p.step("S", history()[:1], TOOLS)
    assert turn.tool_calls == [] and turn.stop_reason == "refusal" and "policy" in (turn.text or "")


async def test_anthropic_errors_are_classified() -> None:
    class RateLimitError(Exception):
        response = NS(headers={"retry-after": "7"})

    async def boom(**_: Any) -> Any:
        raise RateLimitError("429")

    client = NS(messages=NS(create=boom), beta=NS(messages=NS(create=boom)))
    with pytest.raises(ProviderError, match=r"rate limited \(retry-after 7s\)"):
        await AnthropicProvider(client=client).step("S", history()[:1], TOOLS)


# ── openai / grok ───────────────────────────────────────────────────────────────────────


class FakeOpenAI:
    def __init__(self, response: Any) -> None:
        self.kwargs: dict[str, Any] = {}

        async def create(**kwargs: Any) -> Any:
            self.kwargs = kwargs
            return response

        self.chat = NS(completions=NS(create=create))


def openai_response(name: str, args: str, content: str | None = None) -> Any:
    tc = NS(id="call_9", function=NS(name=name, arguments=args))
    return NS(
        choices=[NS(message=NS(content=content, tool_calls=[tc]), finish_reason="tool_calls")],
        usage=NS(prompt_tokens=50, completion_tokens=9),
    )


async def test_openai_request_and_response_mapping() -> None:
    client = FakeOpenAI(openai_response("walk", json.dumps({"vx": 0.3})))
    p = OpenAIProvider(model="gpt-5", client=client)
    turn = await p.step("SYS", history(), TOOLS)
    kw = client.kwargs
    assert kw["tool_choice"] == "required" and kw["parallel_tool_calls"] is False
    assert kw["tools"][0]["type"] == "function" and kw["tools"][0]["function"]["name"] == "walk"
    roles = [m["role"] for m in kw["messages"]]
    assert roles == ["system", "user", "assistant", "tool", "user"]  # image after tool result
    assert kw["messages"][2]["tool_calls"][0]["function"]["arguments"] == '{"vx": 0.1}'
    assert kw["messages"][3]["tool_call_id"] == "call-1"
    assert kw["messages"][4]["content"][1]["type"] == "image_url"
    assert turn.tool_calls == [ToolCall(id="call_9", name="walk", arguments={"vx": 0.3})]
    assert turn.usage.input_tokens == 50 and turn.stop_reason == "tool_calls"


def test_openai_names_each_camera_and_says_frames_only_when_there_are_several() -> None:
    """A `tool` message cannot carry an image, so the pictures follow in a user message of
    their own, and the sentence leading that message is the one place the count is spoken.

    One camera still goes out the way it did before a body could have two: the singular lead,
    and one bare picture with no label in front of it. A pilot told its only view is called
    `camera` would start naming it in sentences nobody asked for.
    """
    msgs = o_messages("SYS", two_camera_history())
    assert [m["role"] for m in msgs] == ["system", "user", "assistant", "tool", "user"]
    plain = msgs[1]["content"]
    assert [p["type"] for p in plain] == ["text", "text", "image_url", "text", "image_url"]
    assert plain[0]["text"] == "obs 1", "the observation text still opens a plain turn"
    assert plain[1]["text"] == "camera top:" and plain[2]["image_url"]["url"] == data_url(PNG_TOP)
    assert plain[3]["text"] == "camera side:" and plain[4]["image_url"]["url"] == data_url(PNG_SIDE)

    after = msgs[4]["content"]
    assert after[0] == {"type": "text", "text": "Current camera frames:"}
    assert [p["type"] for p in after] == ["text", "text", "image_url", "text", "image_url"]
    assert after[1]["text"] == "camera top:" and after[2]["image_url"]["url"] == data_url(PNG_TOP)
    assert after[3]["text"] == "camera side:" and after[4]["image_url"]["url"] == data_url(PNG_SIDE)

    one = o_messages("SYS", history())
    assert one[4]["content"][0] == {"type": "text", "text": "Current camera frame:"}
    assert [p["type"] for p in one[4]["content"]] == ["text", "image_url"], "no label for one"
    assert [p["type"] for p in one[1]["content"]] == ["text", "image_url"]


def responses_result(name: str, args: str, text: str | None = None) -> Any:
    """What `/v1/responses` returns: a flat list of output items, not one message."""
    output: list[Any] = [NS(type="reasoning", summary=[NS(text="thinking")])]
    output.append(NS(type="function_call", call_id="call_r1", name=name, arguments=args))
    if text:
        output.append(NS(type="message", content=[NS(type="output_text", text=text)]))
    return NS(
        output=output,
        status="completed",
        usage=NS(input_tokens=11, output_tokens=4, output_tokens_details=NS(reasoning_tokens=3)),
    )


class RefusesToolsOnChat:
    """Chat Completions 400s the way `gpt-6-astra` does; Responses answers.

    The message is quoted rather than paraphrased, because the switch matches on its words.
    """

    MESSAGE = (
        "Function tools with reasoning_effort are not supported for gpt-6-astra in "
        "/v1/chat/completions. To use function tools, use /v1/responses or set "
        "reasoning_effort to 'none'."
    )

    def __init__(self, response: Any) -> None:
        self.chat_calls: list[dict[str, Any]] = []
        self.responses_calls: list[dict[str, Any]] = []

        async def chat_create(**kwargs: Any) -> Any:
            self.chat_calls.append(kwargs)
            raise RuntimeError(self.MESSAGE)

        async def responses_create(**kwargs: Any) -> Any:
            self.responses_calls.append(kwargs)
            return response

        self.chat = NS(completions=NS(create=chat_create))
        self.responses = NS(create=responses_create)


@pytest.fixture
def _no_effort_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """The knob is also an env var, and a developer who has set it must not fail this file."""
    monkeypatch.delenv("QUACKD_OPENAI_REASONING_EFFORT", raising=False)


#: An OpenAI model the catalogue does not route to Responses, so the 400 reader is what moves
#: a run that uses it. Spelled once because the test below depends on that staying true.
UNHINTED = "gpt-5.6-sol"


async def test_openai_switches_to_responses_when_chat_refuses_function_tools(
    _no_effort_env: None,
) -> None:
    """A model that will not take function tools on Chat Completions at any effort.

    Every verb quackd has is a function tool, so this is not a degraded path, it is no path.
    The provider must take the API at its word, move to Responses, and STAY there: retrying
    chat every turn would pay a failed call per step for the length of the run.

    The other remedy that 400 offers is a dead end and is deliberately not taken: measured on
    gpt-6-astra, `reasoning_effort="none"` comes back as unsupported for that model, and the
    tools are refused at low, medium, high and xhigh alike.

    The model here must be one the catalogue does NOT mark `responses`, or the provider would
    start there and this would assert nothing. That is the point of the pair: a hinted model
    skips the failed call, and an unhinted one still learns from the 400.
    """
    client = RefusesToolsOnChat(responses_result("walk", json.dumps({"vx": 0.2})))
    assert find_model("openai", UNHINTED).api is None, "this test needs a model that starts on chat"
    p = OpenAIProvider(model=UNHINTED, client=client)
    turn = await p.step("SYS", history(), TOOLS)
    assert turn.tool_calls == [ToolCall(id="call_r1", name="walk", arguments={"vx": 0.2})]
    assert p.api == "responses", "the switch must stick for the rest of the run"
    assert len(client.chat_calls) == 1 and len(client.responses_calls) == 1
    await p.step("SYS", history(), TOOLS)
    assert len(client.chat_calls) == 1, "it went back to the API that refuses it"
    assert len(client.responses_calls) == 2
    # and the request is the Responses shape, not the Chat one
    kw = client.responses_calls[0]
    assert kw["instructions"] == "SYS" and "messages" not in kw
    assert kw["tools"][0]["name"] == "walk", "Responses tools are flat, not nested"


async def test_a_catalogue_hinted_model_starts_on_responses_and_pays_no_failed_call(
    _no_effort_env: None,
) -> None:
    """The other half of the pair above: what the catalogue already knows, nobody pays to learn.

    `gpt-6-astra` is marked `responses`, so the run must open on Responses. The stub raises the
    refusal on chat, so a provider that guessed wrong would leave a chat call behind, and the
    assertion below is that it leaves none. For a model that is Responses *only* this is not an
    optimisation at all: its refusal is worded differently, `_wants_the_responses_api` does not
    match it, and reading the 400 would never get there.
    """
    assert find_model("openai", "gpt-6-astra").api == "responses"
    client = RefusesToolsOnChat(responses_result("walk", json.dumps({"vx": 0.2})))
    p = OpenAIProvider(model="gpt-6-astra", client=client)
    turn = await p.step("SYS", history(), TOOLS)
    assert turn.tool_calls == [ToolCall(id="call_r1", name="walk", arguments={"vx": 0.2})]
    assert client.chat_calls == [], "the catalogue said so, nothing had to be spent finding out"
    assert len(client.responses_calls) == 1


async def test_an_explicit_api_outranks_the_catalogue_and_the_environment(
    _no_effort_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`api=` wins over the hint, and the hint wins over the environment.

    That order is not arbitrary. `QUACKD_OPENAI_API=chat` set for one model must not send a model
    with no Chat Completions at all to an endpoint that will refuse it, so the environment loses
    to the catalogue. A caller who passes `api=` is holding something more specific than either.
    """
    monkeypatch.setenv("QUACKD_OPENAI_API", "chat")
    assert OpenAIProvider(model="gpt-6-astra", client=FakeOpenAI(None)).api == "responses"
    assert OpenAIProvider(model="gpt-6-astra", client=FakeOpenAI(None), api="chat").api == "chat"
    assert OpenAIProvider(model=UNHINTED, client=FakeOpenAI(None)).api == "chat"
    monkeypatch.setenv("QUACKD_OPENAI_API", "responses")
    assert OpenAIProvider(model=UNHINTED, client=FakeOpenAI(None)).api == "responses"


async def test_openai_responses_round_trip_shapes(_no_effort_env: None) -> None:
    """The Responses renderer and parser, together: history in, a turn out.

    `call_id` is the handle a `function_call_output` must quote, so it is what lands in
    `ToolCall.id` and what the loop hands back. Sending the item `id` instead is a 400.
    """
    client = RefusesToolsOnChat(responses_result("walk", '{"vx": 0.3}', text="going"))
    p = OpenAIProvider(model="gpt-6-astra", client=client)
    turn = await p.step("SYS", history(), TOOLS)
    items = client.responses_calls[0]["input"]
    kinds = [i.get("type") or i.get("role") for i in items]
    assert kinds == ["user", "function_call", "function_call_output", "user"]
    assert items[1]["call_id"] == "call-1", "the assistant's call goes back by call_id"
    assert items[2]["call_id"] == "call-1", "and its result quotes the same one"
    assert items[3]["content"][1]["type"] == "input_image", "the frame follows the result"
    assert items[0]["content"][0]["type"] == "input_text"
    assert turn.text == "going" and turn.thinking == "thinking"
    assert turn.usage.input_tokens == 11 and turn.usage.reasoning_tokens == 3
    assert turn.stop_reason == "tool_calls"


def test_openai_responses_names_each_camera_before_its_picture() -> None:
    """Responses builds the same two shapes out of `input_text` and `input_image`, in a
    renderer of its own: a label added to `render_messages` is not added here for free, and a
    run that switched API mid-way would otherwise lose the labels from that turn onwards.
    """
    items = o_input(two_camera_history())
    plain = items[0]["content"]
    kinds = ["input_text", "input_text", "input_image", "input_text", "input_image"]
    assert [p["type"] for p in plain] == kinds
    assert plain[0]["text"] == "obs 1", "the observation text still opens a plain item"
    assert plain[1]["text"] == "camera top:" and plain[2]["image_url"] == data_url(PNG_TOP)
    assert plain[3]["text"] == "camera side:" and plain[4]["image_url"] == data_url(PNG_SIDE)

    after = items[3]["content"]
    assert after[0] == {"type": "input_text", "text": "Current camera frames:"}
    assert [p["type"] for p in after] == kinds
    assert after[1]["text"] == "camera top:" and after[2]["image_url"] == data_url(PNG_TOP)
    assert after[3]["text"] == "camera side:" and after[4]["image_url"] == data_url(PNG_SIDE)

    one = o_input(history())
    assert one[3]["content"][0] == {"type": "input_text", "text": "Current camera frame:"}
    assert [p["type"] for p in one[3]["content"]] == ["input_text", "input_image"]
    assert [p["type"] for p in one[0]["content"]] == ["input_text", "input_image"]


async def test_openai_responses_bad_json_arguments_do_not_crash(_no_effort_env: None) -> None:
    client = RefusesToolsOnChat(responses_result("walk", "{not json"))
    p = OpenAIProvider(model="gpt-6-astra", client=client)
    turn = await p.step("SYS", history()[:1], TOOLS)
    assert "_unparsed" in turn.tool_calls[0].arguments


async def test_openai_does_not_swallow_an_unrelated_bad_request(_no_effort_env: None) -> None:
    """Only the 400 that names both halves switches API, so a real error still surfaces."""

    class Broken:
        def __init__(self) -> None:
            async def create(**kwargs: Any) -> Any:
                raise RuntimeError("context_length_exceeded: too many tokens")

            self.chat = NS(completions=NS(create=create))

    p = OpenAIProvider(model="gpt-5", client=Broken())
    with pytest.raises(ProviderError, match="context_length_exceeded"):
        await p.step("SYS", history(), TOOLS)
    assert p.api == "chat", "an unrelated failure must not move the run to another API"


async def test_openai_reasoning_effort_can_be_set_by_hand() -> None:
    client = FakeOpenAI(openai_response("stop", "{}"))
    p = OpenAIProvider(model="gpt-5", client=client, reasoning_effort="low")
    await p.step("SYS", history(), TOOLS)
    assert client.kwargs["reasoning_effort"] == "low"


# ── extra_body: a field the server wants and quackd never sends (#12) ────────────────────

BODY = {"chat_template_kwargs": {"enable_thinking": False}}
BODY_JSON = '{"chat_template_kwargs": {"enable_thinking": false}}'


async def test_extra_body_reaches_the_chat_body() -> None:
    """Nothing is added unless it was asked for, and what was asked for arrives as the SDK's
    own `extra_body`, which merges it into the top level of the request."""
    client = FakeOpenAI(openai_response("stop", "{}"))
    await OpenAIProvider(model="gpt-5", client=client).step("SYS", history(), TOOLS)
    assert "extra_body" not in client.kwargs

    await OpenAIProvider(model="gpt-5", client=client, extra_body=BODY).step(
        "SYS", history(), TOOLS
    )
    assert client.kwargs["extra_body"] == BODY


async def test_extra_body_survives_the_switch_to_responses(_no_effort_env: None) -> None:
    """`step` moves a run from Chat Completions to Responses when the API asks for it, and the
    passthrough has to still be there afterwards. A knob that quietly stops working halfway
    through a run is worse than one the server ignores, which is why it is sent on both."""
    client = RefusesToolsOnChat(responses_result("walk", '{"vx": 0.2}'))
    p = OpenAIProvider(model=UNHINTED, client=client, extra_body=BODY)
    turn = await p.step("SYS", history(), TOOLS)
    assert p.api == "responses" and turn.tool_calls[0].name == "walk"
    assert client.chat_calls[0]["extra_body"] == BODY
    assert client.responses_calls[0]["extra_body"] == BODY


async def test_extra_body_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("QUACKD_EXTRA_BODY", BODY_JSON)
    client = FakeOpenAI(openai_response("stop", "{}"))
    await OpenAIProvider(model="gpt-5", client=client).step("SYS", history(), TOOLS)
    assert client.kwargs["extra_body"] == BODY

    # blanked rather than deleted, which is what a commented-out `.env` line leaves behind
    monkeypatch.setenv("QUACKD_EXTRA_BODY", "   ")
    client = FakeOpenAI(openai_response("stop", "{}"))
    await OpenAIProvider(model="gpt-5", client=client).step("SYS", history(), TOOLS)
    assert "extra_body" not in client.kwargs


async def test_the_flag_outranks_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """The flag reaches a provider as the dict the factory parsed, so a dict beats the
    variable. An empty one is not None, so it skips the variable and sends nothing: that is
    how a `.env` line is silenced for a single run."""
    monkeypatch.setenv("QUACKD_EXTRA_BODY", '{"from": "the environment"}')
    client = FakeOpenAI(openai_response("stop", "{}"))
    await OpenAIProvider(model="gpt-5", client=client, extra_body=BODY).step(
        "SYS", history(), TOOLS
    )
    assert client.kwargs["extra_body"] == BODY

    client = FakeOpenAI(openai_response("stop", "{}"))
    await OpenAIProvider(model="gpt-5", client=client, extra_body={}).step("SYS", history(), TOOLS)
    assert "extra_body" not in client.kwargs


@pytest.mark.parametrize("bad", ["{not json", "[1, 2]", '"text"', "42", "null"])
def test_extra_body_must_be_a_json_object(bad: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """Both doors refuse it, and each says which one it was: a flag somebody has just typed
    and a line in a `.env` they have forgotten want different answers."""
    monkeypatch.setenv("QUACKD_EXTRA_BODY", bad)
    with pytest.raises(ProviderError, match="QUACKD_EXTRA_BODY"):
        OpenAIProvider(model="gpt-5", client=FakeOpenAI(None))

    monkeypatch.setenv("QUACKD_EXTRA_BODY", "")
    with pytest.raises(ProviderError, match="--extra-body"):
        # the parse is what fails, before the SDK this machine does not have is imported
        make_provider("vllm", model="m", base_url="http://gpu:8000/v1", extra_body=bad)


@pytest.mark.parametrize("provider", ["openai", "grok", "vllm", "openrouter"])
def test_extra_body_reaches_every_provider_that_speaks_openais_api(
    provider: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The scope of this knob is the whole OpenAI-compatible family, and every other test of
    it happens to use a local preset. Without this one the cloud branches of the factory could
    stop forwarding it and nothing would go red."""
    seen: dict[str, Any] = {}

    def record(self: Any, *a: Any, **kw: Any) -> None:
        seen.update(kw)
        self.name, self.model, self.supports_vision = provider, "m", False

    monkeypatch.setattr(OpenAIProvider, "__init__", record)
    make_provider(provider, model=None, base_url="http://host:8000/v1", extra_body=BODY_JSON)
    assert seen["extra_body"] == BODY, f"{provider} was not handed the parsed object"


@pytest.mark.parametrize("key", ["model", "messages", "input", "instructions", "tools", "stream"])
def test_extra_body_refuses_the_keys_quackd_owns(key: str) -> None:
    """`model` would put a model on the wire that `run_start` does not name, and `stream`
    changes the shape of the reply without telling the SDK, which then fails on the content
    type long after the robot has connected."""
    with pytest.raises(ProviderError, match=re.escape(repr(key))):
        make_provider(
            "vllm", model="m", base_url="http://gpu:8000/v1", extra_body=json.dumps({key: "x"})
        )


def test_extra_body_lets_you_override_what_quackd_sends() -> None:
    """The refusal list is short on purpose. Replacing `tool_choice` is the escape hatch the
    passthrough exists to be, and the SDK merges last, so it wins."""
    body = parse_extra_body('{"tool_choice": "auto"}', source="--extra-body")
    assert body == {"tool_choice": "auto"}


async def test_openai_bad_json_arguments_do_not_crash() -> None:
    p = OpenAIProvider(model=UNHINTED, client=FakeOpenAI(openai_response("walk", "{not json")))
    turn = await p.step("S", history()[:1], TOOLS)
    assert "_unparsed" in turn.tool_calls[0].arguments


def test_openai_history_without_images_has_no_extra_user_turn() -> None:
    ex = Exchange(
        observation=Observation(text="o"),
        decision=Decision(tool_call=ToolCall(id="c", name="stop")),
    )
    msgs = o_messages("S", [ex, Exchange(observation=Observation(text="r", tool_call_id="c"))])
    assert [m["role"] for m in msgs] == ["system", "user", "assistant", "tool"]


def test_grok_is_openai_with_xai_endpoint() -> None:
    p = GrokProvider(client=FakeOpenAI(None))
    assert p.name == "grok" and p.base_url == "https://api.x.ai/v1"
    assert p.model == default_model_for("grok"), "no model means the catalogue's first entry"


def test_missing_keys_are_clear(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    with pytest.raises(ProviderError, match="OPENAI_API_KEY"):
        OpenAIProvider()
    with pytest.raises(ProviderError, match="XAI_API_KEY"):
        GrokProvider()
    with pytest.raises(ProviderError, match="GEMINI_API_KEY"):
        GeminiProvider()


# ── gemini ──────────────────────────────────────────────────────────────────────────────


class FakeGemini:
    def __init__(self, response: Any) -> None:
        self.kwargs: dict[str, Any] = {}

        async def generate_content(**kwargs: Any) -> Any:
            self.kwargs = kwargs
            return response

        self.aio = NS(models=NS(generate_content=generate_content))


def gemini_response(**usage: Any) -> Any:
    """One `walk` call, and whatever counts the caller cares about on `usage_metadata`.

    Only the fields a test names exist on the namespace, which is the point: google-genai
    leaves a count off the object entirely when the model has no thoughts or no cache, so a
    parser that reached for one directly would fail on the ordinary response rather than on
    an exotic one.
    """
    part = NS(function_call=NS(name="walk", args={}), text=None)
    return NS(
        candidates=[NS(content=NS(parts=[part]), finish_reason="STOP")],
        usage_metadata=NS(**usage),
    )


async def test_gemini_request_and_response_mapping() -> None:
    part = NS(function_call=NS(name="walk", args={"vx": 0.25}), text=None)
    response = NS(
        candidates=[NS(content=NS(parts=[part]), finish_reason="STOP")],
        usage_metadata=NS(prompt_token_count=70, candidates_token_count=12),
    )
    client = FakeGemini(response)
    turn = await GeminiProvider(client=client).step("SYS", history(), TOOLS)
    kw = client.kwargs
    assert kw["model"] == default_model_for("gemini")
    assert kw["config"]["system_instruction"] == "SYS"
    assert kw["config"]["tool_config"] == {"function_calling_config": {"mode": "ANY"}}
    decl = kw["config"]["tools"][0]["function_declarations"][0]
    assert decl["name"] == "walk"
    assert "additionalProperties" not in decl["parameters"]
    assert "title" not in decl["parameters"]["properties"]["vx"]
    contents = kw["contents"]
    assert [c["role"] for c in contents] == ["user", "model", "user"]
    assert contents[1]["parts"][-1]["function_call"] == {"name": "walk", "args": {"vx": 0.1}}
    assert contents[2]["parts"][0]["function_response"]["name"] == "walk"
    assert contents[2]["parts"][1]["inline_data"]["mime_type"] == "image/png"
    assert turn.tool_calls == [ToolCall(id="gemini-0", name="walk", arguments={"vx": 0.25})]
    assert turn.usage.input_tokens == 70


def test_gemini_names_each_camera_before_its_inline_data() -> None:
    """google-genai reads the parts in order, so the label has to be the part immediately
    before the `inline_data` it introduces, not a preamble that lists both cameras first."""
    contents = render_contents(two_camera_history())
    plain = contents[0]["parts"]
    assert plain[0] == {"text": "obs 1"}
    assert plain[1] == {"text": "camera top:"}
    assert plain[2]["inline_data"] == {"mime_type": "image/png", "data": PNG_TOP}
    assert plain[3] == {"text": "camera side:"}
    assert plain[4]["inline_data"] == {"mime_type": "image/png", "data": PNG_SIDE}

    after = contents[2]["parts"]
    assert after[0]["function_response"]["name"] == "walk", "the result still leads the turn"
    assert [p.get("text") for p in after[1:]] == ["camera top:", None, "camera side:", None]
    assert after[2]["inline_data"]["data"] == PNG_TOP
    assert after[4]["inline_data"]["data"] == PNG_SIDE, "the side label kept the side picture"

    one = render_contents(history())
    assert [next(iter(p)) for p in one[2]["parts"]] == ["function_response", "inline_data"], (
        "one camera is one unlabelled picture, the way it was before a body could have two"
    )


def test_gemini_clean_schema_is_recursive() -> None:
    schema = {
        "type": "object",
        "title": "T",
        "properties": {"a": {"type": "array", "items": {"default": 1, "type": "integer"}}},
    }
    cleaned = clean_schema(schema)
    assert "title" not in cleaned and "default" not in cleaned["properties"]["a"]["items"]


def test_gemini_first_turn_has_no_function_response() -> None:
    contents = render_contents([Exchange(observation=Observation(text="hi", tool_call_id="x"))])
    assert contents[0]["parts"][0] == {"text": "hi"}


def test_gemini_drops_the_bounds_google_genai_refuses() -> None:
    """pydantic writes `gt=0` as exclusiveMinimum; google-genai >= 2 validates the schema and
    raises on the keyword. Every verb with a timeout_s or a duration_s carries one."""
    schema = {
        "type": "object",
        "properties": {
            "timeout_s": {"type": "number", "exclusiveMinimum": 0, "description": "seconds"},
            "n": {"type": "integer", "exclusiveMaximum": 10, "minimum": 1},
        },
    }
    cleaned = clean_schema(schema)
    assert cleaned["properties"]["timeout_s"] == {"type": "number", "description": "seconds"}
    assert cleaned["properties"]["n"] == {"type": "integer", "minimum": 1}


def test_gemini_cleans_the_real_verbs_not_only_a_written_one() -> None:
    """The test above proves `clean_schema` works on a schema written here. This one proves it
    on the schemas quackd actually sends, which is where the bug was: `move` and `go_to` are
    core verbs, both bound with `gt=0`, so the 400 was every robot rather than some of them.

    The first assertion is the one that matters. Without it this test passes for the wrong
    reason the day no verb carries a bound any more, and stops guarding the agreement between
    `quackd/verbs/core.py` and `UNSUPPORTED_SCHEMA_KEYS` that it exists to guard.
    """
    schemas = default_registry().tool_schemas()
    carriers = [t["name"] for t in schemas if "exclusiveM" in json.dumps(t)]
    assert carriers, "no verb carries a bound any more — this test now proves nothing, fix it"

    cleaned = json.dumps([clean_schema(t) for t in schemas])
    for key in UNSUPPORTED_SCHEMA_KEYS:
        assert key not in cleaned, f"{key} survived clean_schema and google-genai will refuse it"


async def test_gemini_hands_the_thought_signature_back() -> None:
    """Gemini 3 signs each function call and refuses the next turn without the signature on
    that same call. It arrives as bytes on the part; it goes into the transcript as text and
    comes back out as bytes."""
    part = NS(
        function_call=NS(name="walk", args={"vx": 0.25}), text=None, thought_signature=b"\x01sig"
    )
    response = NS(
        candidates=[NS(content=NS(parts=[part]), finish_reason="STOP")],
        usage_metadata=NS(prompt_token_count=1, candidates_token_count=1),
    )
    turn = await GeminiProvider(client=FakeGemini(response)).step("SYS", history(), TOOLS)
    (tc,) = turn.tool_calls
    assert tc.signature == base64.b64encode(b"\x01sig").decode()

    replay = render_contents(
        [
            Exchange(
                observation=Observation(text="go"),
                decision=Decision(tool_call=tc, text=None),
            ),
            Exchange(observation=Observation(text="walked", tool_call_id=tc.id)),
        ]
    )
    call = replay[1]["parts"][-1]
    assert call["function_call"] == {"name": "walk", "args": {"vx": 0.25}}
    assert call["thought_signature"] == b"\x01sig"


def test_gemini_an_unsigned_call_is_replayed_without_a_signature() -> None:
    """Gemini 2.x signs nothing, and a part with no signature must not grow an empty one."""
    tc = ToolCall(id="gemini-0", name="walk", arguments={})
    replay = render_contents(
        [
            Exchange(observation=Observation(text="go"), decision=Decision(tool_call=tc)),
            Exchange(observation=Observation(text="ok", tool_call_id=tc.id)),
        ]
    )
    assert "thought_signature" not in replay[1]["parts"][-1]


# ── factory ─────────────────────────────────────────────────────────────────────────────


def test_factory_fake_and_unknown() -> None:
    assert make_provider("fake", duck_name="hello-world").name == "fake"
    with pytest.raises(ProviderError, match="unknown provider"):
        make_provider("hal")


def test_factory_reports_missing_extra_or_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(ProviderError):
        make_provider("openai")


# ── what the model thought: the `thinking` field the log shows ──────────────────────────


class BadRequestError(Exception):
    """Named as the SDK names it, because `_classify` and the thinking retry match on that."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def thinking_block(text: str) -> Any:
    dump = {"type": "thinking", "thinking": text, "signature": "sig"}
    return NS(type="thinking", thinking=text, model_dump=lambda: dump)


def tool_use_block(name: str = "walk") -> Any:
    dump = {"type": "tool_use", "id": "t1", "name": name, "input": {}}
    return NS(type="tool_use", id="t1", name=name, input={}, model_dump=lambda: dump)


async def test_anthropic_thinking_skips_empty_blocks_and_names_redacted_ones() -> None:
    """A response carries zero or more thinking blocks and any of them can be empty (a
    progress block, or a display that returns none). An empty one renders nothing."""
    redacted = NS(
        type="redacted_thinking", data="xx", model_dump=lambda: {"type": "redacted_thinking"}
    )
    client = FakeAnthropic(
        anthropic_response(
            thinking_block(""),
            thinking_block("the ball is left, so turn first"),
            redacted,
            thinking_block("   "),
            tool_use_block(),
        )
    )
    turn = await AnthropicProvider(client=client).step("S", history()[:1], TOOLS)
    assert turn.thinking == "the ball is left, so turn first\n\n[redacted thinking]"


async def test_anthropic_all_empty_thinking_is_none_not_a_blank_line() -> None:
    client = FakeAnthropic(anthropic_response(thinking_block(""), tool_use_block()))
    turn = await AnthropicProvider(client=client).step("S", history()[:1], TOOLS)
    assert turn.thinking is None


async def test_anthropic_retries_once_without_thinking_on_an_older_model() -> None:
    """A model older than Claude 4.6 rejects the parameter. Losing the thinking text is
    acceptable; losing the run because the log asked for it is not."""
    calls: list[dict[str, Any]] = []

    async def create(**kwargs: Any) -> Any:
        calls.append(kwargs)
        if "thinking" in kwargs:
            raise BadRequestError("thinking: unsupported parameter for this model")
        return anthropic_response(tool_use_block())

    client = NS(messages=NS(create=create), beta=NS(messages=NS(create=create)))
    p = AnthropicProvider(model="claude-opus-4-5", client=client, fallbacks=False)
    turn = await p.step("S", history()[:1], TOOLS)
    assert [tc.name for tc in turn.tool_calls] == ["walk"]
    assert len(calls) == 2 and "thinking" in calls[0] and "thinking" not in calls[1]
    assert p.thinking_display is None
    await p.step("S", history()[:1], TOOLS)  # and no later turn asks again
    assert len(calls) == 3 and "thinking" not in calls[2]


async def test_anthropic_other_bad_requests_still_raise() -> None:
    async def boom(**_: Any) -> Any:
        raise BadRequestError("max_tokens is too large")

    client = NS(messages=NS(create=boom), beta=NS(messages=NS(create=boom)))
    with pytest.raises(ProviderError, match="bad request"):
        await AnthropicProvider(client=client, fallbacks=False).step("S", history()[:1], TOOLS)


def sdk_bad_request(api_message: str) -> BadRequestError:
    """A 400 in the shape the real SDK raises: `message` is `Error code: 400 - {whole body}`,
    and the sentence the API itself wrote is one level down, in `body["error"]["message"]`."""
    body: dict[str, Any] = {
        "type": "error",
        "error": {"type": "invalid_request_error", "message": api_message},
    }
    e = BadRequestError(f"Error code: 400 - {body}")
    e.body = body  # type: ignore[attr-defined]
    return e


async def test_a_400_about_a_replayed_thinking_block_does_not_disable_thinking() -> None:
    """The signature of a thinking block replayed from an earlier turn can be rejected. That
    complaint names a message path, not the parameter: retrying without `thinking` sends the
    identical messages, fails identically, and would cost the rest of the run its thoughts."""
    calls: list[dict[str, Any]] = []

    async def create(**kwargs: Any) -> Any:
        calls.append(kwargs)
        raise sdk_bad_request("messages.3.content.0.thinking.signature: Invalid signature")

    client = NS(messages=NS(create=create), beta=NS(messages=NS(create=create)))
    p = AnthropicProvider(client=client, fallbacks=False)
    with pytest.raises(ProviderError, match="bad request"):
        await p.step("S", history()[:1], TOOLS)
    assert len(calls) == 1  # no pointless second request
    assert p.thinking_display == "summarized"  # and the next turn still asks to think


async def test_the_real_sdk_message_shape_still_matches_a_top_level_thinking_complaint() -> None:
    """The same wrapping, but the API's sentence names the `thinking` parameter itself: this
    one is the old model the retry was written for."""
    calls: list[dict[str, Any]] = []

    async def create(**kwargs: Any) -> Any:
        calls.append(kwargs)
        if "thinking" in kwargs:
            raise sdk_bad_request("thinking: Extra inputs are not permitted")
        return anthropic_response(tool_use_block())

    client = NS(messages=NS(create=create), beta=NS(messages=NS(create=create)))
    p = AnthropicProvider(model="claude-opus-4-5", client=client, fallbacks=False)
    turn = await p.step("S", history()[:1], TOOLS)
    assert [tc.name for tc in turn.tool_calls] == ["walk"]
    assert len(calls) == 2 and p.thinking_display is None


async def test_a_type_error_inside_the_request_is_not_an_old_sdk() -> None:
    """Only a TypeError about the two fallback keywords means the SDK predates them. One
    raised inside the request is a real failure: retrying on the plain endpoint would hide it
    and drop server-side fallbacks for the rest of the run."""
    plain_calls: list[dict[str, Any]] = []

    async def plain(**kwargs: Any) -> Any:
        plain_calls.append(kwargs)
        return anthropic_response(tool_use_block())

    async def beta(**_: Any) -> Any:
        raise TypeError("'NoneType' object is not subscriptable")

    client = NS(messages=NS(create=plain), beta=NS(messages=NS(create=beta)))
    p = AnthropicProvider(client=client)
    with pytest.raises(ProviderError, match="TypeError"):
        await p.step("S", history()[:1], TOOLS)
    assert p.fallbacks is True and plain_calls == []


def test_anthropic_thinking_display_is_configurable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("QUACKD_THINKING_DISPLAY", "")
    assert AnthropicProvider(client=FakeAnthropic(None)).thinking_display is None
    monkeypatch.setenv("QUACKD_THINKING_DISPLAY", "omitted")
    assert AnthropicProvider(client=FakeAnthropic(None)).thinking_display == "omitted"


# ── a Claude model that will not be forced to call a tool ───────────────────────────────

#: The sentence the API answers a forced call with on Claude Opus 5.5 and Claude Fable 5.1,
#: copied from Anthropic's Opus 5.5 migration guide on 2026-09-23. Quoted rather than
#: paraphrased, because `_refuses_forced_tools` matches on its words.
REFUSES_FORCED = 'tool_choice: type "tool" and "any" are not supported for this model.'


@pytest.mark.parametrize("spec", models_for("anthropic"), ids=lambda m: m.id)
async def test_each_claude_row_is_asked_the_way_the_catalogue_says(spec: Any) -> None:
    """A row the catalogue marks is asked with `auto` from its first turn, and pays no failed
    call to be told; every other row is still made to call a tool. One call per turn both ways."""
    client = FakeAnthropic(anthropic_response(tool_use_block()))
    await AnthropicProvider(model=spec.id, client=client).step("S", history()[:1], TOOLS)
    expected = "any" if spec.forced_tools else "auto"
    assert client.kwargs["tool_choice"] == {"type": expected, "disable_parallel_tool_use": True}


def test_the_claude_models_that_refuse_a_forced_call_are_marked() -> None:
    """Both are documented by the vendor, and one of them is the default: left unmarked, every
    bare `--llm anthropic` run would open on a failed call."""
    assert default_model_for("anthropic") == "claude-opus-5-5"
    for model_id in ("claude-opus-5-5", "claude-fable-5-1"):
        spec = find_model("anthropic", model_id)
        assert spec is not None and spec.forced_tools is False, model_id


@pytest.mark.parametrize("spec", models_for("anthropic"), ids=lambda m: m.id)
async def test_effort_goes_only_to_the_claude_rows_that_take_it(spec: Any) -> None:
    """Haiku 4.5 and Sonnet 4.5 are not on Anthropic's list of models that take
    `output_config.effort`, and quackd sent it to them on every call."""
    client = FakeAnthropic(anthropic_response(tool_use_block()))
    await AnthropicProvider(model=spec.id, client=client, effort="medium").step(
        "S", history()[:1], TOOLS
    )
    assert ("output_config" in client.kwargs) is spec.effort
    assert {"claude-haiku-4-5", "claude-sonnet-4-5"} == {
        m.id for m in models_for("anthropic") if not m.effort
    }


async def test_the_sentence_the_4_5_models_send_about_thinking_is_read_as_a_refusal() -> None:
    """Anthropic's errors page gives it word for word, and it opens with "adaptive". The
    anchored match written for the older wording never saw it, so the one retry that exists for
    exactly these models did not fire and their first call failed on every run."""
    client, calls = _recording(
        lambda kw: "adaptive thinking is not supported on this model" if "thinking" in kw else None
    )
    p = AnthropicProvider(model="claude-haiku-4-5", client=client, fallbacks=False)
    turn = await p.step("S", history()[:1], TOOLS)
    assert [tc.name for tc in turn.tool_calls] == ["walk"]
    assert len(calls) == 2 and "thinking" not in calls[1]
    assert p.thinking_display is None


def test_the_claude_models_that_bind_their_thinking_are_trimmed_in_steps() -> None:
    """Opus 5.5 and Fable 5.1 refuse a replayed thinking block once anything before it has
    changed, and the loop's frame trim changes earlier messages from the third call on. Those
    two, and only those, ask the loop to trim in steps rather than every call."""
    marked = {m.id for m in models_for("anthropic") if m.binds_thinking}
    assert marked == {"claude-opus-5-5", "claude-fable-5-1"}
    for spec in models_for("anthropic"):
        p = AnthropicProvider(model=spec.id, client=FakeAnthropic(None))
        assert p.binds_thinking is spec.binds_thinking, spec.id
        assert p.frame_trim_period == (BINDING_TRIM_PERIOD if spec.binds_thinking else 1)
    unknown = AnthropicProvider(model="claude-not-in-this-build", client=FakeAnthropic(None))
    assert unknown.frame_trim_period == 1


@pytest.mark.parametrize("fallbacks", [True, False], ids=["fallbacks", "no-fallbacks"])
async def test_a_binding_model_asks_for_a_stale_block_to_be_dropped_not_refused(
    fallbacks: bool,
) -> None:
    """Each step of the trim invalidates the blocks replayed after it. `drop_block` has the API
    discard those rather than answer the request with a 400, and it only exists behind its own
    beta, so a binding model goes to the beta endpoint even with fallbacks off."""
    client = FakeAnthropic(anthropic_response(tool_use_block()))
    p = AnthropicProvider(model="claude-opus-5-5", client=client, fallbacks=fallbacks)
    await p.step("S", history()[:1], TOOLS)
    assert client.beta_used
    kw = client.kwargs
    assert kw["thinking"]["block_binding"] == {"prefix_mismatch_behavior": "drop_block"}
    expected = [FALLBACK_BETA, BINDING_BETA] if fallbacks else [BINDING_BETA]
    assert kw["betas"] == expected
    assert ("fallbacks" in kw) is fallbacks

    plain = FakeAnthropic(anthropic_response(tool_use_block()))
    await AnthropicProvider(model="claude-opus-5", client=plain, fallbacks=fallbacks).step(
        "S", history()[:1], TOOLS
    )
    assert "block_binding" not in plain.kwargs["thinking"]
    assert BINDING_BETA not in plain.kwargs.get("betas", [])


async def test_a_binding_model_whose_thinking_is_refused_is_told_not_quietly_unbound() -> None:
    """Opus 5.5 and Fable 5.1 think whether they are asked to or not, so asking again without
    `thinking` would keep the bound blocks and lose only `block_binding`, and the first trim of
    an old frame would be a 400 nine turns later. The refusal is raised as it came, once, and
    the other models keep their retry."""
    client, calls = _recording(lambda kw: ADAPTIVE_REFUSED if "thinking" in kw else None)
    p = AnthropicProvider(model="claude-opus-5-5", client=client, fallbacks=False)
    with pytest.raises(ProviderError, match="adaptive thinking is not supported"):
        await p.step("S", history()[:1], TOOLS)
    assert len(calls) == 1 and p.binds_thinking is True

    client, calls = _recording(lambda kw: ADAPTIVE_REFUSED if "thinking" in kw else None)
    q = AnthropicProvider(model="claude-opus-4-5", client=client, fallbacks=False)
    await q.step("S", history()[:1], TOOLS)
    assert len(calls) == 2 and "thinking" not in calls[1]


async def test_a_binding_model_asks_to_bind_even_with_no_thinking_display() -> None:
    """`QUACKD_THINKING_DISPLAY=` sends no display, and the model thinks anyway; the field that
    keeps a stale block from being a 400 has to go out regardless."""
    client = FakeAnthropic(anthropic_response(tool_use_block()))
    p = AnthropicProvider(model="claude-opus-5-5", client=client, thinking_display="")
    await p.step("S", history()[:1], TOOLS)
    assert client.kwargs["thinking"] == {
        "type": "adaptive",
        "block_binding": {"prefix_mismatch_behavior": "drop_block"},
    }


def test_leaving_a_turn_s_thinking_out_keeps_everything_else_it_said() -> None:
    """What the loop hands a binding model for a turn a trim invalidated: the same turn with
    both kinds of thinking block gone, and the call and the text where they were, in order.
    A turn with no block list to filter, as a provider other than this one records, passes
    through as it came."""
    p = AnthropicProvider(model="claude-opus-5-5", client=FakeAnthropic(None))
    call = ToolCall(id="t1", name="walk", arguments={"vx": 0.1})
    raw = [
        {"type": "thinking", "thinking": "the bench is left", "signature": "s1"},
        {"type": "redacted_thinking", "data": "opaque"},
        {"type": "text", "text": "walking"},
        {"type": "tool_use", "id": "t1", "name": "walk", "input": {"vx": 0.1}},
    ]
    turn = Decision(tool_call=call, text="walking", raw=raw)
    kept = p.without_thinking(turn)
    assert kept.raw == raw[2:] and kept.tool_call == call and kept.text == "walking"
    assert turn.raw == raw and len(raw) == 4, "the run's own history was edited"
    for other in (None, {"content": raw}):
        assert p.without_thinking(Decision(tool_call=call, raw=other)).raw == other


async def test_an_sdk_too_old_for_fallbacks_keeps_the_binding_beta() -> None:
    """An SDK that predates `fallbacks` still sends betas. A binding model keeps its own, and
    with it `block_binding`: without them the first trim of an old frame is a 400."""
    client = FakeAnthropic(anthropic_response(tool_use_block()), beta_ok=False)
    p = AnthropicProvider(model="claude-opus-5-5", client=client)
    await p.step("S", history()[:1], TOOLS)
    assert client.beta_used and client.kwargs["betas"] == [BINDING_BETA]
    assert "fallbacks" not in client.kwargs
    assert client.kwargs["thinking"]["block_binding"] == {"prefix_mismatch_behavior": "drop_block"}
    assert p.binds_thinking is True and p.fallbacks is False
    await p.step("S", history()[:1], TOOLS)
    assert client.beta_calls == 3, "the second turn paid a failed call"


async def test_a_type_error_naming_fallbacks_once_they_are_off_is_raised_not_retried() -> None:
    """The fallbacks retry switches fallbacks off before it asks again, and that is the only
    thing that ends it: a TypeError that still names them after that came from somewhere else,
    and asking again would ask forever."""
    beta_calls: list[dict[str, Any]] = []

    async def beta(**kwargs: Any) -> Any:
        beta_calls.append(kwargs)
        raise TypeError("fallbacks: 'NoneType' object is not iterable")

    async def plain(**_: Any) -> Any:
        raise AssertionError("a binding model reached the plain endpoint")

    client = NS(messages=NS(create=plain), beta=NS(messages=NS(create=beta)))
    p = AnthropicProvider(model="claude-opus-5-5", client=client)
    with pytest.raises(ProviderError, match="TypeError"):
        await p.step("S", history()[:1], TOOLS)
    assert len(beta_calls) == 2, "asked again more than once"
    assert "fallbacks" in beta_calls[0] and "fallbacks" not in beta_calls[1]


async def test_an_sdk_too_old_for_betas_refuses_a_binding_model_up_front() -> None:
    """Without betas there is no `block_binding`, and a binding model's first trim of an old
    frame would be a 400 nine turns in. It is said on the first call instead."""
    client = FakeAnthropic(anthropic_response(tool_use_block()), beta_ok=False, predates="betas")
    p = AnthropicProvider(model="claude-opus-5-5", client=client)
    with pytest.raises(ProviderError, match="sends no betas"):
        await p.step("S", history()[:1], TOOLS)
    assert client.kwargs == {}, "nothing reached the plain endpoint"

    plain = FakeAnthropic(anthropic_response(tool_use_block()), beta_ok=False, predates="betas")
    q = AnthropicProvider(model="claude-opus-5", client=plain)
    await q.step("S", history()[:1], TOOLS)
    assert not plain.beta_used and "fallbacks" not in plain.kwargs and q.fallbacks is False


async def test_deepseek_turns_thinking_off_and_insists_on_its_one_call() -> None:
    """Thinking mode refuses `required` and wants every earlier turn's reasoning sent back, and
    it is DeepSeek's default. With thinking off the forced call is accepted again."""
    from quackd.agent.providers.deepseek import DeepSeekProvider

    client = FakeOpenAI(openai_response("walk", "{}"))
    await DeepSeekProvider(model="deepseek-flash", client=client).step("S", history(), TOOLS)
    assert client.kwargs["tool_choice"] == "required"
    assert client.kwargs["extra_body"] == {"thinking": {"type": "disabled"}}
    assert "parallel_tool_calls" not in client.kwargs


async def test_a_deepseek_extra_body_goes_over_the_thinking_default_not_instead_of_it() -> None:
    from quackd.agent.providers.deepseek import DeepSeekProvider

    client = FakeOpenAI(openai_response("walk", "{}"))
    p = DeepSeekProvider(model="deepseek-flash", client=client, extra_body={"top_p": 0.5})
    await p.step("S", history()[:1], TOOLS)
    assert client.kwargs["extra_body"] == {"thinking": {"type": "disabled"}, "top_p": 0.5}
    p = DeepSeekProvider(
        model="deepseek-flash", client=client, extra_body={"thinking": {"type": "enabled"}}
    )
    await p.step("S", history()[:1], TOOLS)
    assert client.kwargs["extra_body"] == {"thinking": {"type": "enabled"}}, (
        "an explicit --extra-body is the caller's to set, refusals and all"
    )


async def test_meta_is_asked_for_one_call_per_turn() -> None:
    """Meta documents `parallel_tool_calls` and defaults it to allowing several."""
    from quackd.agent.providers.meta import MetaProvider

    client = FakeOpenAI(openai_response("walk", "{}"))
    await MetaProvider(model="muse-spark-1.3", client=client).step("S", history()[:1], TOOLS)
    assert client.kwargs["parallel_tool_calls"] is False
    assert client.kwargs["tool_choice"] == "auto"


def _recording(fail_when: Callable[[dict[str, Any]], str | None]) -> tuple[Any, list[Any]]:
    """A client that records every request and raises the 400 `fail_when` names, if any."""
    calls: list[dict[str, Any]] = []

    async def create(**kwargs: Any) -> Any:
        calls.append(kwargs)
        complaint = fail_when(kwargs)
        if complaint:
            raise sdk_bad_request(complaint)
        return anthropic_response(tool_use_block())

    return NS(messages=NS(create=create), beta=NS(messages=NS(create=create))), calls


async def test_a_refused_forced_call_is_asked_again_with_auto_and_stays_asked() -> None:
    """A model the catalogue does not mark learns it from the 400, once, for the whole run."""
    client, calls = _recording(
        lambda kw: REFUSES_FORCED if kw["tool_choice"]["type"] == "any" else None
    )
    p = AnthropicProvider(model="claude-a-model-this-build-has-not-heard-of", client=client)
    turn = await p.step("S", history()[:1], TOOLS)
    assert [tc.name for tc in turn.tool_calls] == ["walk"]
    assert [c["tool_choice"]["type"] for c in calls] == ["any", "auto"]
    assert p.forced_tools is False
    assert "thinking" in calls[1], "the other repair fired on a complaint that was not its own"
    await p.step("S", history()[:1], TOOLS)
    assert [c["tool_choice"]["type"] for c in calls] == ["any", "auto", "auto"]


async def test_a_tool_choice_complaint_auto_cannot_fix_is_raised_after_one_request() -> None:
    """It opens with the parameter too, and says nothing about the type being unsupported."""
    client, calls = _recording(
        lambda kw: "tool_choice.name: Input should be the name of a tool the request declares"
    )
    p = AnthropicProvider(model="claude-opus-5", client=client)
    with pytest.raises(ProviderError, match="bad request"):
        await p.step("S", history()[:1], TOOLS)
    assert len(calls) == 1 and p.forced_tools is True


async def test_a_model_that_refuses_auto_as_well_ends_the_turn_rather_than_looping() -> None:
    client, calls = _recording(lambda kw: REFUSES_FORCED)
    p = AnthropicProvider(model="claude-opus-5", client=client, fallbacks=False)
    with pytest.raises(ProviderError, match="not supported"):
        await p.step("S", history()[:1], TOOLS)
    assert [c["tool_choice"]["type"] for c in calls] == ["any", "auto"]


async def test_both_repairs_can_happen_on_one_turn_and_each_happens_once() -> None:
    """An old model refuses `thinking` and a new one refuses a forced call; nothing ships that
    does both, and the retry must still end rather than go round again."""

    def complaint(kw: dict[str, Any]) -> str | None:
        if "thinking" in kw:
            return "thinking: Extra inputs are not permitted"
        return REFUSES_FORCED if kw["tool_choice"]["type"] == "any" else None

    client, calls = _recording(complaint)
    p = AnthropicProvider(model="claude-opus-5", client=client, fallbacks=False)
    turn = await p.step("S", history()[:1], TOOLS)
    assert [tc.name for tc in turn.tool_calls] == ["walk"]
    assert len(calls) == 3
    assert "thinking" not in calls[2] and calls[2]["tool_choice"]["type"] == "auto"


# ── which model answered, when a refusal fallback took the turn ─────────────────────────


def _answered(*kinds: str, model: str, as_dicts: bool = False) -> Any:
    entries = [{"type": k} if as_dicts else NS(type=k) for k in kinds]
    usage = NS(input_tokens=10, output_tokens=5, iterations=entries)
    response = anthropic_response(tool_use_block(), usage=usage)
    response.model = model
    return response


@pytest.mark.parametrize("as_dicts", [False, True], ids=["objects", "dicts"])
async def test_a_turn_a_fallback_answered_names_the_model_that_answered_it(as_dicts: bool) -> None:
    client = FakeAnthropic(
        _answered("message", "fallback_message", model="claude-opus-4-8", as_dicts=as_dicts)
    )
    turn = await AnthropicProvider(model="claude-opus-5-5", client=client).step(
        "S", history()[:1], TOOLS
    )
    assert turn.served_by == "claude-opus-4-8"


async def test_a_turn_the_model_asked_for_took_names_nobody_even_when_the_id_differs() -> None:
    """An alias comes back as its dated snapshot, which is the same model and no fallback."""
    client = FakeAnthropic(_answered("message", model="claude-haiku-4-5-20251001"))
    turn = await AnthropicProvider(model="claude-haiku-4-5", client=client).step(
        "S", history()[:1], TOOLS
    )
    assert turn.served_by is None
    plain = FakeAnthropic(anthropic_response(tool_use_block()))  # an SDK that reports neither
    turn = await AnthropicProvider(client=plain).step("S", history()[:1], TOOLS)
    assert turn.served_by is None


@pytest.mark.parametrize("field", ["reasoning_content", "reasoning"])
async def test_openai_reads_reasoning_from_either_field(field: str) -> None:
    """DeepSeek, vLLM, llama.cpp, LM Studio and xAI answer with `reasoning_content`; Ollama's
    OpenAI-compatible endpoint and OpenRouter with `reasoning`."""
    response = openai_response("walk", "{}")
    setattr(response.choices[0].message, field, "  I should walk  ")
    response.usage.completion_tokens_details = NS(reasoning_tokens=44)
    turn = await OpenAIProvider(model=UNHINTED, client=FakeOpenAI(response)).step(
        "S", history()[:1], TOOLS
    )
    assert turn.thinking == "I should walk" and turn.usage.reasoning_tokens == 44


async def test_openai_without_reasoning_reports_none() -> None:
    client = FakeOpenAI(openai_response("walk", "{}"))
    turn = await OpenAIProvider(model=UNHINTED, client=client).step("S", history()[:1], TOOLS)
    assert turn.thinking is None and turn.usage.reasoning_tokens == 0


async def test_openai_ignores_a_non_string_reasoning_field() -> None:
    response = openai_response("walk", "{}")
    response.choices[0].message.reasoning = {"summary": "an object, not text"}
    turn = await OpenAIProvider(model=UNHINTED, client=FakeOpenAI(response)).step(
        "S", history()[:1], TOOLS
    )
    assert turn.thinking is None


async def test_local_splits_inline_think_tags_out_of_the_answer() -> None:
    """A server with no reasoning separation leaves `<think>` in `content`, where it would be
    shown as the answer and replayed to the model next turn as something it said."""
    from quackd.agent.providers.local import LocalProvider

    response = openai_response("walk", "{}", content="<think>ball is left</think>turning left")
    p = LocalProvider("m", preset="ollama", client=FakeOpenAI(response))
    turn = await p.step("S", history()[:1], TOOLS)
    assert turn.thinking == "ball is left" and turn.text == "turning left"


async def test_gemini_asks_for_thoughts_and_keeps_them_out_of_the_text() -> None:
    thought = NS(function_call=None, text="I will walk", thought=True)
    answer = NS(function_call=None, text="walking now", thought=False)
    call = NS(function_call=NS(name="walk", args={}), text=None)
    response = NS(
        candidates=[NS(content=NS(parts=[thought, answer, call]), finish_reason="STOP")],
        usage_metadata=NS(prompt_token_count=1, candidates_token_count=2, thoughts_token_count=9),
    )
    client = FakeGemini(response)
    turn = await GeminiProvider(client=client).step("SYS", history()[:1], TOOLS)
    assert client.kwargs["config"]["thinking_config"] == {"include_thoughts": True}
    assert turn.thinking == "I will walk"
    assert turn.text == "walking now"  # a thought is not something the model said
    assert turn.usage.reasoning_tokens == 9


async def test_gemini_retries_once_without_thoughts_on_a_model_without_them() -> None:
    seen: list[dict[str, Any]] = []
    answered = NS(
        candidates=[
            NS(
                content=NS(parts=[NS(function_call=NS(name="walk", args={}), text=None)]),
                finish_reason="STOP",
            )
        ],
        usage_metadata=NS(prompt_token_count=1, candidates_token_count=1),
    )

    async def generate_content(**kwargs: Any) -> Any:
        seen.append(kwargs)
        if "thinking_config" in kwargs["config"]:
            raise ValueError("INVALID_ARGUMENT: thinking_config is not supported by this model")
        return answered

    client = NS(aio=NS(models=NS(generate_content=generate_content)))
    p = GeminiProvider(model="gemini-2.0-flash", client=client)
    turn = await p.step("S", history()[:1], TOOLS)
    assert [tc.name for tc in turn.tool_calls] == ["walk"]
    assert len(seen) == 2 and not p.include_thoughts


async def test_gemini_other_errors_still_raise() -> None:
    async def boom(**_: Any) -> Any:
        raise ValueError("quota exhausted")

    client = NS(aio=NS(models=NS(generate_content=boom)))
    with pytest.raises(ProviderError, match="quota exhausted"):
        await GeminiProvider(client=client).step("S", history()[:1], TOOLS)


async def test_gemini_does_not_retry_a_rate_limit_that_mentions_thinking() -> None:
    """Only INVALID_ARGUMENT means "this model has no thoughts". A 429 that happens to say
    the word would otherwise buy a second, equally rate-limited request and give up thought
    summaries for the rest of the run."""
    calls: list[dict[str, Any]] = []

    async def generate_content(**kwargs: Any) -> Any:
        calls.append(kwargs)
        raise ValueError("429 RESOURCE_EXHAUSTED. quota exceeded for thinking tokens")

    client = NS(aio=NS(models=NS(generate_content=generate_content)))
    p = GeminiProvider(client=client)
    with pytest.raises(ProviderError, match="RESOURCE_EXHAUSTED"):
        await p.step("S", history()[:1], TOOLS)
    assert len(calls) == 1 and p.include_thoughts is True


def test_gemini_thoughts_can_be_turned_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("QUACKD_GEMINI_THOUGHTS", "0")
    p = GeminiProvider(client=NS())
    assert not p.include_thoughts and "thinking_config" not in p._config("S", TOOLS)


# ── a malformed response is an error, not a traceback ───────────────────────────────────


@pytest.mark.parametrize(
    "build",
    [
        pytest.param(
            lambda: OpenAIProvider(model=UNHINTED, client=FakeOpenAI(NS(choices=[], usage=None))),
            id="openai",
        ),
        pytest.param(
            lambda: AnthropicProvider(
                client=FakeAnthropic(
                    NS(content=None, stop_reason=None, usage=None, stop_details=None)
                )
            ),
            id="anthropic",
        ),
        pytest.param(
            lambda: GeminiProvider(
                client=FakeGemini(NS(candidates=[], usage_metadata=NS(prompt_token_count="lots")))
            ),
            id="gemini",
        ),
        pytest.param(
            lambda: OpenRouterProvider(client=FakeOpenAI(NS(choices=[], usage=None))),
            id="openrouter",
        ),
        pytest.param(
            # a 200 whose body is not a completion at all: no `choices` attribute, no `error`
            lambda: OpenRouterProvider(client=FakeOpenAI(NS(usage=None))),
            id="openrouter-no-choices-field",
        ),
    ],
)
async def test_a_malformed_response_is_a_provider_error_not_a_traceback(
    build: Callable[[], Any],
) -> None:
    """A gateway that answers a content filter with no choices, or a usage field that is not
    a number, must reach the CLI as a ProviderError: parsing is inside the classifying try,
    and the CLI catches nothing else."""
    with pytest.raises(ProviderError):
        await build().step("S", history()[:1], TOOLS)


def test_usage_adds_reasoning_tokens_too() -> None:
    """A run's totals are these objects summed turn by turn, so a bucket left out of `__add__`
    reads as 0 for the whole run however many tokens went through it. For the cache buckets
    that is not a cosmetic loss: `pricing.cost_usd` subtracts them from the prompt to find what
    was billed at the full rate, so a cache read that failed to add gets charged as if it had
    never been cached, roughly ten times over.

    The whole `model_dump` is compared rather than a field at a time because it is the dict
    that reaches `summary.json`, and a sixth bucket added later without a line in `__add__`
    should fail here rather than quietly report zero for the length of a run.
    """
    from quackd.agent.providers.base import Usage

    total = Usage(
        input_tokens=1,
        output_tokens=2,
        reasoning_tokens=3,
        cache_read_tokens=4,
        cache_write_tokens=5,
    ) + Usage(
        input_tokens=10,
        output_tokens=20,
        reasoning_tokens=30,
        cache_read_tokens=40,
        cache_write_tokens=50,
    )
    assert total.model_dump() == {
        "input_tokens": 11,
        "output_tokens": 22,
        "reasoning_tokens": 33,
        "cache_read_tokens": 44,
        "cache_write_tokens": 55,
    }


# ── the token buckets a bill is itemised by ─────────────────────────────────────────────
#
# Every vendor reports the same spend in a different shape, and the adapters normalise them
# into one convention (`providers.base.Usage`): `input_tokens` is the WHOLE prompt with the
# cached slices inside it, `output_tokens` is everything generated with the thinking inside
# it. Anthropic is the single adapter that has to add, because it is the single vendor that
# reports its input buckets disjoint. Each parser is checked on its own: they are separate
# functions over separate attribute names, and a run that switched API mid-way would
# otherwise lose the numbers from that turn onwards.


async def test_anthropic_adds_its_three_disjoint_input_buckets_into_one_prompt_total() -> None:
    """Anthropic reports the three apart: `input_tokens` counts only what was neither read
    from a cache nor written to one, and the two cached slices are named beside it. Everyone
    else reports one inclusive total that already contains the cached part, so this is the one
    adapter that adds, and reading its three the way OpenAI's are read would under-report the
    prompt by everything that was cached, which from the second turn of a run is most of it.

    `cache_creation` is on the response too and is deliberately not summed: it is the same
    number as `cache_creation_input_tokens` broken down by cache lifetime, so an adapter that
    added both would bill every cached prompt twice.
    """
    usage = NS(
        input_tokens=200,
        cache_read_input_tokens=1000,
        cache_creation_input_tokens=50,
        cache_creation=NS(ephemeral_5m_input_tokens=50, ephemeral_1h_input_tokens=0),
        output_tokens=30,
    )
    client = FakeAnthropic(anthropic_response(tool_use_block(), usage=usage))
    turn = await AnthropicProvider(client=client).step("S", history()[:1], TOOLS)
    assert turn.usage.input_tokens == 1250, "the whole prompt, both cached slices included"
    assert turn.usage.cache_read_tokens == 1000 and turn.usage.cache_write_tokens == 50
    assert turn.usage.output_tokens == 30


async def test_anthropic_reasoning_tokens_are_a_slice_of_an_output_total_already_whole() -> None:
    """`output_tokens` is what Anthropic bills and calls inclusive; `output_tokens_details` is
    a read-only decomposition of that same number. So the thinking count is recorded for the
    reader and added to nothing: folding it into the output the way Gemini's has to be folded
    would charge the output rate twice for every thought the model had.
    """
    usage = NS(input_tokens=120, output_tokens=30, output_tokens_details=NS(thinking_tokens=18))
    client = FakeAnthropic(anthropic_response(tool_use_block(), usage=usage))
    turn = await AnthropicProvider(client=client).step("S", history()[:1], TOOLS)
    assert turn.usage.reasoning_tokens == 18
    assert turn.usage.output_tokens == 30, "a decomposition of a total, not an addition to it"


async def test_openai_chat_keeps_the_prompt_total_and_names_its_cached_slices() -> None:
    """`prompt_tokens_details` is a breakdown of `prompt_tokens`, not an addition to it, so the
    total passes through as quackd's whole prompt and only the slices are new. Adding them the
    way the Anthropic adapter must would count every cached token twice.

    The slices are what make the bill come out right: from the second turn of a run most of the
    prompt is the cached part at a tenth of the rate, and `pricing.cost_usd` can only take that
    tenth off a run that said which tokens it applied to.
    """
    response = openai_response("walk", "{}")
    response.usage.prompt_tokens_details = NS(cached_tokens=40, cache_write_tokens=8)
    turn = await OpenAIProvider(model=UNHINTED, client=FakeOpenAI(response)).step(
        "S", history()[:1], TOOLS
    )
    assert turn.usage.input_tokens == 50, "unchanged: the cached part was always inside it"
    assert turn.usage.cache_read_tokens == 40 and turn.usage.cache_write_tokens == 8


async def test_openai_responses_names_the_cached_slices_under_its_own_spelling(
    _no_effort_env: None,
) -> None:
    """Responses calls them `input_tokens` and `input_tokens_details` and means them exactly as
    Chat Completions means `prompt_tokens`, so the total passes through here too. It is a
    parser of its own, though, which is why it is proved on its own: the cache numbers added to
    one of the two are not added to the other for free.
    """
    result = responses_result("walk", '{"vx": 0.2}')
    result.usage.input_tokens_details = NS(cached_tokens=7, cache_write_tokens=2)
    p = OpenAIProvider(model="gpt-6-astra", client=RefusesToolsOnChat(result))
    turn = await p.step("SYS", history()[:1], TOOLS)
    assert turn.usage.input_tokens == 11, "the prompt total already contains the cached part"
    assert turn.usage.cache_read_tokens == 7 and turn.usage.cache_write_tokens == 2


async def test_gemini_bills_the_thoughts_beside_the_answer_and_names_its_cached_prompt() -> None:
    """Google's own reference defines the total as prompt plus thoughts plus response
    candidates, three addends, so `candidates_token_count` cannot already contain the thinking.
    quackd's `output_tokens` is everything generated at the output rate, so the two are summed
    here and nowhere else: before that, a thinking Gemini run under-reported its output by
    however much it thought, which on a reasoning model is most of what it generated.

    `reasoning_tokens` stays the slice of that sum the vendor counted apart, never a third
    number to price. The prompt side needs no such addition: `prompt_token_count` is the
    effective prompt size with the cached content already in it, and Google charges nothing
    per token to create a cache, so there is no write bucket to fill.
    """
    response = gemini_response(
        prompt_token_count=800,
        candidates_token_count=12,
        thoughts_token_count=64,
        cached_content_token_count=700,
    )
    turn = await GeminiProvider(client=FakeGemini(response)).step("SYS", history()[:1], TOOLS)
    assert turn.usage.output_tokens == 76, "the answer plus the thinking that produced it"
    assert turn.usage.reasoning_tokens == 64, "and the same thinking, named, never priced twice"
    assert turn.usage.input_tokens == 800, "the prompt total already contains the cached part"
    assert turn.usage.cache_read_tokens == 700
    assert turn.usage.cache_write_tokens == 0, "Google has no per-token charge for writing one"


@pytest.mark.parametrize(
    "build",
    [
        pytest.param(
            lambda: AnthropicProvider(client=FakeAnthropic(anthropic_response(tool_use_block()))),
            id="anthropic",
        ),
        pytest.param(
            lambda: OpenAIProvider(
                model=UNHINTED, client=FakeOpenAI(openai_response("walk", "{}"))
            ),
            id="openai",
        ),
        pytest.param(
            lambda: OpenAIProvider(
                model="gpt-6-astra", client=RefusesToolsOnChat(responses_result("walk", "{}"))
            ),
            id="openai-responses",
        ),
        pytest.param(
            lambda: GeminiProvider(
                client=FakeGemini(gemini_response(prompt_token_count=5, candidates_token_count=6))
            ),
            id="gemini",
        ),
    ],
)
async def test_a_usage_object_with_none_of_the_cache_fields_still_parses(
    build: Callable[[], Any], _no_effort_env: None
) -> None:
    """The ordinary answer has none of these attributes on it: a vendor with no prompt cache,
    an SDK older than the fields, a gateway that forwards the two counts it knows about. The
    buckets have to read as 0 there, the way the malformed-response test above asks for an
    error rather than a traceback, because a `getattr` that reached for one directly would take
    the run down at its first model call on the common case rather than on a rare one.
    """
    turn = await build().step("S", history()[:1], TOOLS)
    assert turn.usage.cache_read_tokens == 0 and turn.usage.cache_write_tokens == 0
    assert turn.usage.input_tokens > 0, "and the counts that were reported still arrived"


# ── task pictures (`--image`) in front of the frames ────────────────────────────────────
#
# A picture that came with the task shares a message with a photograph the robot just took,
# and the two mean opposite things: one is what the pilot was asked about, the other is what
# is in front of it now. So the task's go first, each one named, and the frame behind them is
# named too even on a body with a single camera. The four renderers build that order out of
# four different part vocabularies, which is why each is checked on its own.


def test_anthropic_sends_the_task_picture_first_and_names_the_frame_behind_it() -> None:
    """The label has to be the block immediately before the picture it introduces, so the
    order of the blocks is the whole guarantee: a renderer that sent both labels and then both
    pictures would carry every right word and still hand the model the sketch as its view of
    the room."""
    content = a_messages(task_picture_history())[0]["content"]
    assert [b["type"] for b in content] == ["text", "image", "text", "image", "text"]
    assert content[0]["text"] == "task picture sketch.png:"
    assert content[1]["source"]["data"] == b64(PNG_SKETCH)
    assert content[2]["text"] == "camera front:", "one camera, and named anyway"
    assert content[3]["source"]["data"] == b64(PNG), "the frame's own bytes, behind the sketch"
    assert content[4]["text"] == "obs 1", "the observation text still closes a plain turn"


def test_openai_sends_the_task_picture_first_and_names_the_frame_behind_it() -> None:
    """Chat Completions leads a plain turn with the observation text, and the pictures follow
    it in the same user message."""
    content = o_messages("SYS", task_picture_history())[1]["content"]
    assert [p["type"] for p in content] == ["text", "text", "image_url", "text", "image_url"]
    assert content[0]["text"] == "obs 1", "the observation text still opens a plain turn"
    assert content[1]["text"] == "task picture sketch.png:"
    assert content[2]["image_url"]["url"] == data_url(PNG_SKETCH)
    assert content[3]["text"] == "camera front:", "one camera, and named anyway"
    assert content[4]["image_url"]["url"] == data_url(PNG), "the frame's bytes, behind the sketch"


def test_openai_responses_sends_the_task_picture_first_and_names_the_frame_behind_it() -> None:
    """Responses has a renderer of its own, so an order fixed in `render_messages` is not
    fixed here for free, and a run that switched API mid-way would otherwise lose the task's
    pictures from that turn onwards."""
    content = o_input(task_picture_history())[0]["content"]
    kinds = ["input_text", "input_text", "input_image", "input_text", "input_image"]
    assert [p["type"] for p in content] == kinds
    assert content[0]["text"] == "obs 1", "the observation text still opens a plain item"
    assert content[1]["text"] == "task picture sketch.png:"
    assert content[2]["image_url"] == data_url(PNG_SKETCH)
    assert content[3]["text"] == "camera front:", "one camera, and named anyway"
    assert content[4]["image_url"] == data_url(PNG), "the frame's bytes, behind the sketch"


def test_gemini_sends_the_task_picture_first_and_names_the_frame_behind_it() -> None:
    """google-genai reads the parts in order, and its pictures are raw bytes rather than
    base64 text, so the wrong picture under the right label is a byte comparison here."""
    parts = render_contents(task_picture_history())[0]["parts"]
    assert parts[0] == {"text": "obs 1"}
    assert parts[1] == {"text": "task picture sketch.png:"}
    assert parts[2]["inline_data"] == {"mime_type": "image/png", "data": PNG_SKETCH}
    assert parts[3] == {"text": "camera front:"}, "one camera, and named anyway"
    assert parts[4]["inline_data"] == {"mime_type": "image/png", "data": PNG}


def test_two_task_pictures_keep_their_order_and_their_own_names() -> None:
    """`--image` is repeatable, and the task refers to the pictures by what they show. Two of
    them reordered, or given each other's names, would be a request about the wrong file."""
    two = task_picture_history(
        NamedPng(name="sketch.png", png=PNG_SKETCH), NamedPng(name="plan.png", png=PNG_PLAN)
    )
    labels = ["task picture sketch.png:", "task picture plan.png:", "camera front:"]
    pngs = [PNG_SKETCH, PNG_PLAN, PNG]

    content = a_messages(two)[0]["content"]
    assert [b["type"] for b in content] == ["text", "image"] * 3 + ["text"]
    assert [content[i]["text"] for i in (0, 2, 4)] == labels
    assert [content[i]["source"]["data"] for i in (1, 3, 5)] == [b64(p) for p in pngs]

    msg = o_messages("SYS", two)[1]["content"]
    assert [p["type"] for p in msg] == ["text"] + ["text", "image_url"] * 3
    assert [msg[i]["text"] for i in (1, 3, 5)] == labels
    assert [msg[i]["image_url"]["url"] for i in (2, 4, 6)] == [data_url(p) for p in pngs]

    items = o_input(two)[0]["content"]
    assert [p["type"] for p in items] == ["input_text"] + ["input_text", "input_image"] * 3
    assert [items[i]["text"] for i in (1, 3, 5)] == labels
    assert [items[i]["image_url"] for i in (2, 4, 6)] == [data_url(p) for p in pngs]

    parts = render_contents(two)[0]["parts"]
    said = ["obs 1", labels[0], None, labels[1], None, labels[2], None]
    assert [p.get("text") for p in parts] == said
    assert [parts[i]["inline_data"]["data"] for i in (2, 4, 6)] == pngs


def test_one_camera_and_no_task_picture_still_sends_one_bare_frame() -> None:
    """The regression that matters most: every run that does not pass `--image` has to go out
    exactly as it did before there was a flag, byte for byte and part for part.

    `picture_parts` decides to name a frame from `cameras` or from the attachments, and both
    are empty here, so the single picture is the one bare part every provider has always sent.
    A label added to it would be quackd telling a pilot with one lens that the lens is called
    `camera`, in a sentence nobody asked for.
    """
    assert a_messages(history())[0]["content"] == [
        {
            "type": "image",
            "source": {"type": "base64", "media_type": "image/png", "data": b64(PNG)},
        },
        {"type": "text", "text": "obs 1"},
    ]
    assert o_messages("SYS", history())[1]["content"] == [
        {"type": "text", "text": "obs 1"},
        {"type": "image_url", "image_url": {"url": data_url(PNG)}},
    ]
    assert o_input(history())[0]["content"] == [
        {"type": "input_text", "text": "obs 1"},
        {"type": "input_image", "image_url": data_url(PNG)},
    ]
    assert render_contents(history())[0]["parts"] == [
        {"text": "obs 1"},
        {"inline_data": {"mime_type": "image/png", "data": PNG}},
    ]


def test_the_tool_result_turn_is_also_unchanged_without_a_task_picture() -> None:
    """The other branch of all four renderers, which nests or trails its pictures differently.

    The loop attaches the task's pictures to the first observation alone, and that one is
    never a tool result, so this shape is the one an `--image` run keeps sending from step two
    onwards. It has to be the shape it was.
    """
    result = a_messages(history())[2]["content"][0]
    assert result["type"] == "tool_result" and result["tool_use_id"] == "call-1"
    assert result["content"] == [
        {"type": "text", "text": "obs 2 (result)"},
        {
            "type": "image",
            "source": {"type": "base64", "media_type": "image/png", "data": b64(PNG)},
        },
    ]
    assert o_messages("SYS", history())[4]["content"] == [
        {"type": "text", "text": "Current camera frame:"},
        {"type": "image_url", "image_url": {"url": data_url(PNG)}},
    ]
    assert o_input(history())[3]["content"] == [
        {"type": "input_text", "text": "Current camera frame:"},
        {"type": "input_image", "image_url": data_url(PNG)},
    ]
    assert render_contents(history())[2]["parts"] == [
        {"function_response": {"name": "walk", "response": {"result": "obs 2 (result)"}}},
        {"inline_data": {"mime_type": "image/png", "data": PNG}},
    ]


async def test_a_task_picture_reaches_the_wire_through_a_provider_step() -> None:
    """The renderer tests above call the renderers directly. This one goes the way a run does,
    through `step`, so a provider that rendered the messages and then sent something else
    (an older cached body, a second render without the observation's attachments) is caught."""
    client = FakeAnthropic(anthropic_response(tool_use_block()))
    await AnthropicProvider(client=client).step("SYS", task_picture_history(), TOOLS)
    content = client.kwargs["messages"][0]["content"]
    assert content[0]["text"] == "task picture sketch.png:"
    assert content[1]["source"]["data"] == b64(PNG_SKETCH)
    assert content[2]["text"] == "camera front:"
    assert content[3]["source"]["data"] == b64(PNG)


# ── openrouter: a router over OpenAI's API (ADR-0050) ──────────────────────────────────


def openrouter_response(
    name: str | None = "walk",
    *,
    details: Any = None,
    reasoning: str | None = None,
    refusal: str | None = None,
    finish: str = "tool_calls",
    **usage: Any,
) -> Any:
    """A completion shaped the way OpenRouter answers: OpenAI's fields, and its own beside them."""
    calls = [NS(id="call_or", function=NS(name=name, arguments="{}"))] if name else []
    message = NS(content=None, tool_calls=calls, refusal=refusal)
    if details is not None:
        message.reasoning_details = details
    if reasoning is not None:
        message.reasoning = reasoning
    return NS(
        choices=[NS(message=message, finish_reason=finish)],
        usage=NS(prompt_tokens=50, completion_tokens=9, **usage),
    )


def or_entry(model_id: str = "qwen/qwen3.8-flash", **over: Any) -> dict[str, Any]:
    """One entry of OpenRouter's model list, for an id the catalogue does not carry."""
    entry: dict[str, Any] = {
        "id": model_id,
        "architecture": {"input_modalities": ["text", "image"]},
        "pricing": {"prompt": "0.0000001", "completion": "0.00000047", "input_cache_read": "1e-8"},
        "supported_parameters": ["tools", "tool_choice", "max_tokens"],
        "expiration_date": None,
    }
    entry.update(over)
    return entry


class Listing(list[dict[str, Any]]):
    """OpenRouter's model list as a test hands it in, which remembers every address that asked.

    Change it before the first provider is built: the list is read once per process."""

    def __init__(self, *entries: dict[str, Any]) -> None:
        super().__init__(entries)
        self.fetched: list[str] = []


@pytest.fixture
def or_list(monkeypatch: pytest.MonkeyPatch) -> Listing:
    from quackd.agent.providers import openrouter

    entries = Listing(or_entry())

    def fetch(base_url: str, **kwargs: Any) -> list[dict[str, Any]]:
        entries.fetched.append(base_url)
        return list(entries)

    monkeypatch.setattr(openrouter, "fetch_models", fetch)
    return entries


def test_openrouter_builds_its_client_with_quackds_attribution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The two headers OpenRouter credits an app by, on the client itself so every request
    carries them. The SDK is not installed here, so a stand-in module records what it is built
    with, and Grok built the same way must get nothing: the hook is inert for every other vendor.
    """
    import sys
    import types

    from quackd.agent.providers.openrouter import ATTRIBUTION, BASE_URL

    built: list[dict[str, Any]] = []

    class AsyncOpenAI:
        def __init__(self, **kwargs: Any) -> None:
            built.append(kwargs)

    monkeypatch.setitem(sys.modules, "openai", types.SimpleNamespace(AsyncOpenAI=AsyncOpenAI))
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-test")
    monkeypatch.setenv("XAI_API_KEY", "xai-test")
    OpenRouterProvider()
    GrokProvider()
    assert built[0]["default_headers"] == ATTRIBUTION
    assert built[0]["base_url"] == BASE_URL == "https://openrouter.ai/api/v1"
    assert ATTRIBUTION == {
        "HTTP-Referer": "https://github.com/rokbenko/quackd",
        "X-OpenRouter-Title": "quackd",
    }
    assert "default_headers" not in built[1], "Grok was built with headers it never had"


@pytest.mark.parametrize("spec", models_for("openrouter"), ids=lambda m: m.id)
def test_a_curated_openrouter_row_never_asks_the_network(spec: Any) -> None:
    """The six rows the catalogue carries are priced and described already. The conftest guard
    fails a fetch from openrouter.ai, so building one here proves it reached for nothing."""
    p = OpenRouterProvider(model=spec.id, client=FakeOpenAI(None))
    assert p.listed is None and p.listed_price is None
    assert p.supports_vision is spec.vision


def test_an_unlisted_id_takes_its_images_and_its_price_from_the_list(or_list: Listing) -> None:
    or_list.append(or_entry("quackd-stub/text-only", architecture={"input_modalities": ["text"]}))
    p = OpenRouterProvider(model="qwen/qwen3.8-flash", client=FakeOpenAI(None))
    assert p.supports_vision is True
    price = p.listed_price
    assert price is not None and price.source == "openrouter"
    # Decimal, not float: float("0.0000001") * 1e6 is 0.09999999999999999
    assert (price.input, price.output, price.cache_read) == (0.1, 0.47, 0.01)
    assert price.cache_write is None, "the list names no cache write, so neither does the price"
    assert price.checked is not None and len(price.checked) == 10, "today's date, ISO"
    assert or_list.fetched == ["https://openrouter.ai/api/v1"]

    blind = OpenRouterProvider(model="quackd-stub/text-only", client=FakeOpenAI(None))
    assert blind.supports_vision is False, "a text-only entry is sent no frames"
    assert OpenRouterProvider(
        model="quackd-stub/text-only", client=FakeOpenAI(None), vision=True
    ).supports_vision, "--vision still wins over the list"


def test_an_unlisted_id_the_list_lacks_is_refused_with_the_nearest_it_has(
    or_list: Listing, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Before the key: a model that does not exist is the reader's to fix first."""
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    with pytest.raises(ProviderError) as e:
        OpenRouterProvider(model="qwen/qwen3.8-flsh", source="QUACKD_LLM")
    message = str(e.value)
    assert "'qwen/qwen3.8-flsh' from QUACKD_LLM is not on OpenRouter's model list" in message
    assert "Nearest on the list: qwen/qwen3.8-flash" in message
    assert "OPENROUTER_API_KEY" not in message


@pytest.mark.parametrize(
    ("entry", "why"),
    [
        (or_entry(supported_parameters=["max_tokens"]), "without tool calling"),
        (or_entry(expiration_date="2000-01-01"), "expired on OpenRouter on 2000-01-01"),
    ],
    ids=["no-tools", "expired"],
)
def test_an_unlisted_id_the_list_says_cannot_pilot_is_refused_before_the_key(
    entry: dict[str, Any], why: str, or_list: Listing, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    or_list[:] = [entry]
    with pytest.raises(ProviderError, match=why):
        OpenRouterProvider(model="qwen/qwen3.8-flash")


def test_an_expiry_still_ahead_is_taken(or_list: Listing) -> None:
    """The model answers today and the run is today. The catalogue keeps such a model out of
    its own rows; the list is not the catalogue."""
    or_list[:] = [or_entry(expiration_date="2999-01-01")]
    assert OpenRouterProvider(model="qwen/qwen3.8-flash", client=FakeOpenAI(None))


def test_an_unreachable_list_names_the_host_before_any_key_is_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A real fetch, at a port nothing listens on: refused, never read as "no such model"."""
    import socket

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    with pytest.raises(ProviderError) as e:
        OpenRouterProvider(model="qwen/qwen3.8-flash", base_url=f"http://127.0.0.1:{port}/api/v1")
    assert f"could not reach 127.0.0.1:{port}" in str(e.value)
    assert "not on OpenRouter's model list" not in str(e.value)


def test_the_list_is_read_once_per_process_and_per_address(or_list: Listing) -> None:
    """Preflight builds a provider for every file and seed, and a flock one per member."""
    for _ in range(3):
        OpenRouterProvider(model="qwen/qwen3.8-flash", client=FakeOpenAI(None))
    OpenRouterProvider(
        model="qwen/qwen3.8-flash", client=FakeOpenAI(None), base_url="http://elsewhere/api/v1/"
    )
    assert or_list.fetched == ["https://openrouter.ai/api/v1", "http://elsewhere/api/v1"]


def test_the_list_is_asked_for_by_quackd_with_no_key() -> None:
    """A real request to a stand-in. quackd's own User-Agent, because OpenRouter answers one that
    looks like Anthropic's client with a list in Anthropic's shape; the attribution headers; and
    no key, which has not been read yet and which the list does not need."""
    from quackd import __version__
    from quackd.agent.providers.openrouter import fetch_models
    from tests.fake_openrouter import FakeOpenRouter

    with FakeOpenRouter() as stand_in:
        models = fetch_models(stand_in.base_url)
    assert models[0]["id"] == "quackd-stub/tool-model"
    (asked,) = stand_in.gets()
    assert asked.path == "/api/v1/models"
    assert asked.headers["user-agent"] == f"quackd/{__version__}"
    assert asked.headers["http-referer"] == "https://github.com/rokbenko/quackd"
    assert asked.headers["x-openrouter-title"] == "quackd"
    assert "authorization" not in asked.headers and "anthropic-version" not in asked.headers


@pytest.mark.parametrize(
    ("body", "why"),
    [
        (
            # what OpenRouter served a client sending `anthropic-version`, 2026-10-06
            {"data": [{"id": "anthropic/x-ai/grok-4.7", "type": "model"}], "has_more": False},
            "no entry says which parameters it supports",
        ),
        ({"data": []}, "no models in it"),
        ({"type": "list", "models": [{"id": "a/b"}]}, "no models in it"),
        ([{"id": "a/b", "supported_parameters": ["tools"]}], "no models in it"),
        ({"data": [{"name": "no id", "supported_parameters": []}]}, "an entry with no id"),
    ],
    ids=["anthropic-shaped", "empty", "no-data", "a-bare-list", "an-entry-with-no-id"],
)
def test_a_list_in_another_shape_is_refused_not_read_as_absent(body: Any, why: str) -> None:
    """Read as OpenRouter's, any of these would turn "the list is wrong" into "your model does
    not exist" or "your model has no tool calling", and send the reader to fix the wrong thing."""
    from quackd.agent.providers.openrouter import fetch_models
    from tests.fake_openrouter import FakeOpenRouter

    with (
        FakeOpenRouter(models_body=body) as stand_in,
        pytest.raises(ProviderError, match=f"did not answer with OpenRouter's model list: {why}"),
    ):
        fetch_models(stand_in.base_url)


def test_a_variable_price_on_the_list_is_unpriced_not_negative(or_list: Listing) -> None:
    from quackd.agent.providers.openrouter import listed_price

    assert listed_price({"prompt": "-1", "completion": "-1"}, checked="2026-10-06") is None
    assert listed_price({"prompt": "0.000002"}, checked="2026-10-06") is None
    assert listed_price({"prompt": "lots", "completion": "1"}, checked="2026-10-06") is None
    free = listed_price({"prompt": "0", "completion": "0"}, checked="2026-10-06")
    assert free is not None and (free.input, free.output) == (0.0, 0.0), "free is $0, not None"
    or_list[:] = [or_entry(pricing={"prompt": "-1", "completion": "-1"})]
    p = OpenRouterProvider(model="qwen/qwen3.8-flash", client=FakeOpenAI(None))
    assert p.listed_price is None


# ── what an OpenRouter request carries ──


@pytest.mark.parametrize("spec", models_for("openrouter"), ids=lambda m: m.id)
async def test_openrouter_insists_on_one_call_except_on_the_rows_that_refuse_one(
    spec: Any,
) -> None:
    client = FakeOpenAI(openrouter_response())
    await OpenRouterProvider(model=spec.id, client=client).step("S", history(), TOOLS)
    assert client.kwargs["tool_choice"] == ("required" if spec.forced_tools else "auto")
    assert "parallel_tool_calls" not in client.kwargs, "13 of 465 models listed it"
    assert client.kwargs["extra_body"] == {"provider": {"require_parameters": True}}
    assert client.kwargs["model"] == spec.id


@pytest.mark.parametrize(
    ("supported", "tool_choice"),
    [(["tools", "tool_choice"], "auto"), (["tools"], None)],
    ids=["asked", "no-field-at-all"],
)
async def test_an_unlisted_model_is_asked_rather_than_told(
    supported: list[str], tool_choice: str | None, or_list: Listing
) -> None:
    """Nobody here has seen how each upstream words a refusal of a forced call, so an id quackd
    does not carry is asked, the way a local model is. Where its entry lists no `tool_choice`,
    the field is not sent: `require_parameters` would route a request carrying it nowhere."""
    or_list[:] = [or_entry(supported_parameters=supported)]
    client = FakeOpenAI(openrouter_response())
    await OpenRouterProvider(model="qwen/qwen3.8-flash", client=client).step("S", history(), TOOLS)
    assert client.kwargs.get("tool_choice") == tool_choice
    assert ("tool_choice" in client.kwargs) is (tool_choice is not None)


async def test_a_provider_preference_goes_beside_require_parameters_not_instead_of_it() -> None:
    client = FakeOpenAI(openrouter_response())
    body = {"provider": {"sort": "price", "data_collection": "deny"}, "top_p": 0.5}
    await OpenRouterProvider(client=client, extra_body=body).step("S", history(), TOOLS)
    assert client.kwargs["extra_body"] == {
        "provider": {"require_parameters": True, "sort": "price", "data_collection": "deny"},
        "top_p": 0.5,
    }
    assert body == {"provider": {"sort": "price", "data_collection": "deny"}, "top_p": 0.5}, (
        "the caller's own dict was changed underneath it"
    )
    off = {"provider": {"require_parameters": False}}
    await OpenRouterProvider(client=client, extra_body=off).step("S", history(), TOOLS)
    assert client.kwargs["extra_body"] == off, "an explicit false is the caller's to set"
    await OpenRouterProvider(client=client, extra_body={}).step("S", history(), TOOLS)
    assert client.kwargs["extra_body"] == {"provider": {"require_parameters": True}}


async def test_an_extra_body_can_still_replace_the_tool_choice() -> None:
    """The escape hatch `--extra-body` exists to be: the SDK merges it last."""
    client = FakeOpenAI(openrouter_response())
    p = OpenRouterProvider(client=client, extra_body={"tool_choice": "auto"})
    await p.step("S", history(), TOOLS)
    assert client.kwargs["extra_body"]["tool_choice"] == "auto"


def test_openrouter_refuses_a_model_fallback_list_from_either_door(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`models` would let OpenRouter answer with a model `run_start` does not name. Refused
    before the key, and each door says which it was."""
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    with pytest.raises(ProviderError, match=r"--extra-body: 'models' is refused on openrouter"):
        make_provider("openrouter", extra_body='{"models": ["a/b"]}')
    monkeypatch.setenv("QUACKD_EXTRA_BODY", '{"models": ["a/b"]}')
    with pytest.raises(ProviderError, match=r"QUACKD_EXTRA_BODY: 'models' is refused"):
        OpenRouterProvider()
    # and nowhere else: every other OpenAI-shaped vendor still forwards it untouched
    assert GrokProvider(client=FakeOpenAI(None)).extra_body == {"models": ["a/b"]}


async def test_openrouter_stays_on_chat_completions_whatever_it_is_told(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """OpenRouter relaying OpenAI's refusal of function tools does not move the run to the
    Responses API: everything this provider adds is chat-shaped. It says why instead."""
    from quackd.agent.providers.openrouter import RESPONSES_HINT

    monkeypatch.setenv("QUACKD_OPENAI_API", "responses")
    client = RefusesToolsOnChat(responses_result("walk", "{}"))
    p = OpenRouterProvider(client=client)
    assert p.api == "chat"
    with pytest.raises(ProviderError) as e:
        await p.step("S", history(), TOOLS)
    assert RESPONSES_HINT in str(e.value) and RefusesToolsOnChat.MESSAGE in str(e.value)
    assert client.responses_calls == [] and p.api == "chat"
    assert len(client.chat_calls) == 1, "and no retry either"


async def test_openrouter_ignores_the_openai_reasoning_effort_variable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A stray parameter is worse here than anywhere: with `require_parameters` it can leave no
    endpoint at all. The variable is OpenAI's; an argument still goes through."""
    monkeypatch.setenv("QUACKD_OPENAI_REASONING_EFFORT", "high")
    client = FakeOpenAI(openrouter_response())
    await OpenRouterProvider(client=client).step("S", history(), TOOLS)
    assert "reasoning_effort" not in client.kwargs
    await OpenRouterProvider(client=client, reasoning_effort="low").step("S", history(), TOOLS)
    assert client.kwargs["reasoning_effort"] == "low"


# ── what an OpenRouter answer is read as ──

DETAILS = [
    {
        "type": "reasoning.text",
        "text": "walk",
        "signature": "sig-1",
        "format": "anthropic-claude-v1",
    },
    {"type": "reasoning.encrypted", "data": "enc-1", "id": "rd-1", "format": "google-gemini-v1"},
]


async def test_openrouter_keeps_reasoning_details_for_the_next_turn() -> None:
    response = openrouter_response(details=[NS(**d) for d in DETAILS], reasoning="  walk now  ")
    turn = await OpenRouterProvider(client=FakeOpenAI(response)).step("S", history(), TOOLS)
    assert turn.raw == {"reasoning_details": DETAILS}, "kept as plain data, every field"
    assert turn.thinking == "walk now", "the text OpenRouter shows still reaches the log"


def test_reasoning_details_go_back_on_their_own_assistant_message_unchanged() -> None:
    """On the message that made the call, in the order they came, and on no other: a base
    OpenAI provider given the same history sends none."""
    first = Exchange(
        observation=Observation(text="obs 1"),
        decision=Decision(
            tool_call=ToolCall(id="call-1", name="walk"), raw={"reasoning_details": DETAILS}
        ),
    )
    second = Exchange(
        observation=Observation(text="obs 2", tool_call_id="call-1"),
        decision=Decision(tool_call=ToolCall(id="call-2", name="walk")),
    )
    third = Exchange(observation=Observation(text="obs 3", tool_call_id="call-2"))
    p = OpenRouterProvider(client=FakeOpenAI(None))
    messages = p._params("S", [first, second, third], TOOLS)["messages"]
    assistants = [m for m in messages if m["role"] == "assistant"]
    assert assistants[0]["reasoning_details"] == DETAILS
    assert assistants[0]["tool_calls"][0]["id"] == "call-1"
    assert "reasoning_details" not in assistants[1], "a turn that brought none is sent none"
    base = OpenAIProvider(model=UNHINTED, client=FakeOpenAI(None))
    plain = base._params("S", [first, second, third], TOOLS)["messages"]
    assert all("reasoning_details" not in m for m in plain)
    assert plain == o_messages("S", [first, second, third]), "the hook changes nothing elsewhere"


async def test_a_200_carrying_only_an_error_is_a_provider_error_in_openrouters_words() -> None:
    """ "Check the body for an `error` field even on a `200`", OpenRouter's errors page says."""
    error = {
        "code": 502,
        "message": "Provider returned error",
        "metadata": {"provider_name": "Anthropic", "raw": '{"type":"overloaded_error"}'},
    }
    p = OpenRouterProvider(client=FakeOpenAI(NS(error=error, usage=None)))
    with pytest.raises(ProviderError) as e:
        await p.step("S", history(), TOOLS)
    message = str(e.value)
    assert "502: Provider returned error (from Anthropic)" in message
    assert "overloaded_error" in message


async def test_a_choice_that_finished_with_an_error_is_a_provider_error() -> None:
    p = OpenRouterProvider(client=FakeOpenAI(openrouter_response(finish="error")))
    with pytest.raises(ProviderError, match="failed partway through the answer"):
        await p.step("S", history(), TOOLS)


async def test_a_refusal_is_a_turn_with_no_call_that_says_so() -> None:
    response = openrouter_response(None, refusal="I can't help with that.", finish="content_filter")
    turn = await OpenRouterProvider(client=FakeOpenAI(response)).step("S", history(), TOOLS)
    assert turn.tool_calls == [] and turn.text == "[refusal] I can't help with that."


@pytest.mark.parametrize(
    ("usage", "billed"),
    [
        ({"cost": 0.0123}, 0.0123),
        (
            {"cost": 0.0002, "is_byok": True, "cost_details": {"upstream_inference_cost": 0.003}},
            0.0032,
        ),
        ({"cost": 0.0002, "is_byok": True}, None),
        (
            {"cost": 0.0002, "is_byok": True, "cost_details": {"upstream_inference_cost": None}},
            None,
        ),
        ({"cost": 0.0013, "cost_details": {"upstream_inference_cost": 0.5}}, 0.0013),
        ({"cost": 0}, 0.0),
        ({}, None),
        ({"cost": "0.01"}, None),
        ({"cost": True}, None),
        ({"cost": -0.01}, None),
        ({"cost": float("nan")}, None),
        ({"cost": float("inf")}, None),
    ],
    ids=[
        "the-bill",
        "byok-adds-the-upstream-charge",
        "byok-without-it-is-not-billed-at-the-fee",
        "byok-with-a-null-upstream-is-not-billed-either",
        "an-upstream-figure-off-byok-is-not-added",
        "a-free-call-is-billed-at-zero",
        "no-cost-bills-nothing",
        "text-is-not-a-number",
        "nor-is-a-bool",
        "nor-a-negative",
        "nor-nan",
        "nor-infinity",
    ],
)
async def test_the_billed_cost_is_read_off_the_usage(usage: dict[str, Any], billed: Any) -> None:
    turn = await OpenRouterProvider(client=FakeOpenAI(openrouter_response(**usage))).step(
        "S", history(), TOOLS
    )
    assert turn.billed_usd == billed
    if billed is not None:
        assert isinstance(turn.billed_usd, float)


async def test_no_other_vendor_reports_a_bill() -> None:
    """`billed_usd` is OpenRouter's alone: the same usage read by the OpenAI provider is tokens."""
    response = openrouter_response(cost=0.5)
    turn = await OpenAIProvider(model=UNHINTED, client=FakeOpenAI(response)).step(
        "S", history(), TOOLS
    )
    assert turn.billed_usd is None


@pytest.mark.parametrize("as_dicts", [False, True], ids=["objects", "dicts"])
@pytest.mark.parametrize(
    "response",
    [
        lambda usage: NS(error={"code": 502, "message": "Provider returned error"}, usage=usage),
        lambda usage: NS(
            choices=[NS(message=NS(content=None, tool_calls=[]), finish_reason="error")],
            usage=usage,
        ),
    ],
    ids=["a-200-carrying-an-error", "a-choice-that-ended-in-one"],
)
async def test_an_error_that_came_with_a_bill_carries_the_bill_and_the_tokens(
    response: Callable[[Any], Any], as_dicts: bool
) -> None:
    """A provider that gave out partway can still be billed for what it used, and the error is
    where that bill has to travel, or the run's total leaves it out. The SDK keeps a usage block
    it could not build its own model for as plain dicts, so both shapes are read."""
    usage: Any = {
        "prompt_tokens": 40,
        "completion_tokens": 3,
        "prompt_tokens_details": {"cached_tokens": 10},
        "cost": 0.25,
    }
    if not as_dicts:
        usage = NS(**{**usage, "prompt_tokens_details": NS(cached_tokens=10)})
    p = OpenRouterProvider(client=FakeOpenAI(response(usage)))
    with pytest.raises(ProviderError) as e:
        await p.step("S", history(), TOOLS)
    assert e.value.billed_usd == 0.25
    assert e.value.usage is not None
    assert (e.value.usage.input_tokens, e.value.usage.output_tokens) == (40, 3)
    assert e.value.usage.cache_read_tokens == 10


async def test_an_error_with_no_usage_carries_nothing() -> None:
    p = OpenRouterProvider(client=FakeOpenAI(NS(error={"code": 400, "message": "no"})))
    with pytest.raises(ProviderError) as e:
        await p.step("S", history(), TOOLS)
    assert e.value.billed_usd is None and e.value.usage is None


class RawServer:
    """A TCP server on 127.0.0.1 that answers every connection with `answer(conn, stop)`: the
    replies no HTTP server would send, which is the point. `stop` is set when it shuts down."""

    def __init__(self, answer: Callable[[Any, Any], None]) -> None:
        import socket
        import threading

        self.stop = threading.Event()
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen()
        self.sock.settimeout(0.2)
        self.port = self.sock.getsockname()[1]

        def serve() -> None:
            while not self.stop.is_set():
                try:
                    conn, _ = self.sock.accept()
                except OSError:
                    continue
                conn.recv(65536)
                try:
                    answer(conn, self.stop)
                except OSError:
                    pass
                finally:
                    conn.close()

        threading.Thread(target=serve, daemon=True).start()

    def __enter__(self) -> RawServer:
        return self

    def __exit__(self, *exc: object) -> None:
        self.stop.set()
        self.sock.close()


def _not_http(conn: Any, stop: Any) -> None:
    conn.sendall(b"<html>captive portal</html>\r\n\r\n")


def _cut_short(conn: Any, stop: Any) -> None:
    conn.sendall(
        b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: 5000\r\n\r\n"
    )
    conn.sendall(b'{"data": [')


def _drip(conn: Any, stop: Any) -> None:
    conn.sendall(
        b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: 5000\r\n\r\n"
    )
    while not stop.wait(0.3):
        conn.sendall(b" ")


@pytest.mark.parametrize("answer", [_not_http, _cut_short], ids=["not-http", "cut-short"])
def test_a_list_reply_that_is_not_one_is_refused_not_a_traceback(
    answer: Callable[[Any, Any], None],
) -> None:
    """A captive portal and a connection cut partway are what a list fetched over a hotel's
    network looks like. Both stop the run in a sentence, before any key or robot."""
    from quackd.agent.providers.openrouter import fetch_models

    with RawServer(answer) as server, pytest.raises(ProviderError) as e:
        fetch_models(f"http://127.0.0.1:{server.port}/api/v1", timeout_s=5.0)
    assert f"could not read the model list from 127.0.0.1:{server.port}" in str(e.value)


def test_a_list_that_trickles_in_is_refused_when_its_time_is_up() -> None:
    """The timeout bounds the whole fetch, not each read: one byte every 0.3 s never trips a
    per-read socket timeout and would otherwise hold a run as long as the server liked."""
    import time

    from quackd.agent.providers.openrouter import fetch_models

    started = time.monotonic()
    with RawServer(_drip) as server, pytest.raises(ProviderError) as e:
        fetch_models(f"http://127.0.0.1:{server.port}/api/v1", timeout_s=1.0)
    assert time.monotonic() - started < 3.0
    assert "did not answer the model list within 1 s" in str(e.value)


def test_a_base_url_with_no_scheme_is_refused_not_a_traceback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    with pytest.raises(ProviderError, match="could not read the model list"):
        OpenRouterProvider(model="qwen/qwen3.8-flash", base_url="example.invalid/api/v1")


def test_a_model_that_expires_today_is_refused_and_one_that_expires_tomorrow_is_taken(
    or_list: Listing,
) -> None:
    """The line itself, on a day the test names: OpenRouter's expiry date is the first day a
    model is gone, so a run on that day is refused, and a run the day before it is not."""
    import datetime as dt

    from quackd.agent.providers.openrouter import admit

    today = dt.date(2026, 10, 6)
    or_list[:] = [
        or_entry("quackd-stub/today", expiration_date="2026-10-06"),
        or_entry("quackd-stub/tomorrow", expiration_date="2026-10-07"),
    ]
    with pytest.raises(ProviderError, match="expired on OpenRouter on 2026-10-06"):
        admit("quackd-stub/today", today=today)
    assert admit("quackd-stub/tomorrow", today=today).id == "quackd-stub/tomorrow"


def test_a_list_that_could_not_be_read_is_asked_for_again(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed fetch is not kept. Kept, it would turn "the host was down" into "your model does
    not exist" for every later provider in the process: a preflight sweep, a flock's members."""
    from quackd.agent.providers import openrouter

    asked: list[str] = []

    def fetch(base_url: str, **kwargs: Any) -> list[dict[str, Any]]:
        asked.append(base_url)
        if len(asked) == 1:
            raise ProviderError("openrouter: could not reach openrouter.ai (down for a moment)")
        return [or_entry()]

    monkeypatch.setattr(openrouter, "fetch_models", fetch)
    with pytest.raises(ProviderError, match="could not reach"):
        OpenRouterProvider(model="qwen/qwen3.8-flash", client=FakeOpenAI(None))
    again = OpenRouterProvider(model="qwen/qwen3.8-flash", client=FakeOpenAI(None))
    assert again.listed is not None and len(asked) == 2


def test_the_factory_tells_the_list_check_where_the_id_came_from(
    or_list: Listing, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Through `make_provider`, the way a run builds it: an id pinned in `QUACKD_LLM` that the
    list lacks is refused as coming from there, not from a flag nobody typed."""
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    with pytest.raises(ProviderError, match="from QUACKD_LLM is not on OpenRouter's model list"):
        make_provider("openrouter", model="qwen/qwen3.8-flsh", source="QUACKD_LLM")
