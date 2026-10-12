"""Every pilot that speaks OpenAI's API, through the real `openai` SDK, with the network shut.

Skipped wherever the SDK is not installed, which includes CI: nothing in the suite installs a
provider SDK. To run it, sync an environment with the extra and run pytest there:

    UV_PROJECT_ENVIRONMENT=<scratch> uv sync --frozen --extra dev --extra openai

It has run, all of it, against the eleven releases of the SDK in uv's cache, on 2026-10-12, and
against no other: 1.66.0, the extras' floor, which sends through `httpx` rather than `httpx2`, and
1.83.0; 3.1.0, the first that reads `OPENAI_CUSTOM_HEADERS`; 3.6.0, 3.8.0, 3.10.0, 3.13.0, 3.14.0,
3.18.0 and 3.19.0; and 3.22.1, which the lock carries and that sync installs. All but the last ran
from uv's cache in scratch environments of their own.
`tests/test_openai_headers.py` holds the rule to every kind of release the extras allow, through a
model of each, and the last test here holds that model to whichever release is installed.

No key and no network. The SDK's own `DefaultAsyncHttpxClient` is handed a mock transport that
records every request and answers it, so a request is built and sent by the real SDK and the real
HTTP client exactly as it would be, and stops where the socket would have been; every lookup and
every connection to another machine raises besides. So what this proves is what quackd puts on the
wire through the SDK, and what the SDK adds to a request or leaves off it once quackd has told it.
Nothing here proves anything about any vendor.

No request here is built for a host of OpenAI's, not even into the recorder. Where a test needs
OpenAI's own API, quackd's rule is told that a stand-in address is OpenAI's (`_as_openai`), which
is the one thing that decides what goes there, and the request goes to the stand-in. Where the SDK's
own variables go, the request is held to what the SDK sends by itself, a client built as 0.17.1
built every pilot's, `AsyncOpenAI(api_key=key, base_url=address)`, sending the same request
(`_as_the_sdk_sends`).

Every test runs with the variables the SDK reads for itself set the way somebody who reaches
OpenAI through a gateway might have them in a `.env`, beside an OpenAI key of their own, so every
test shows what quackd does about each of them.
"""

from __future__ import annotations

import importlib
import json
import socket
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("openai")

import openai
from typer.testing import CliRunner

from quackd.agent.providers import openai as openai_provider
from quackd.agent.providers import openai_headers
from quackd.agent.providers.base import ProviderError
from quackd.agent.providers.catalogue import LOCAL_NAMES
from quackd.agent.providers.factory import KEY_ENV, OPENAI_COMPATIBLE, make_provider
from quackd.agent.providers.openrouter import ATTRIBUTION
from quackd.cli import app
from tests import fake_openai_sdk as fake_sdk
from tests.fake_openrouter import SCRIPTS, completion

KEY = "sk-test-not-a-real-key"
"""Never a real one: nothing here leaves the machine, and a fake key is what proves it. The key
of whichever preset a test builds."""
OPENAI_KEY = "sk-openai-not-a-real-key"
"""What `OPENAI_API_KEY` holds wherever the preset a test builds is not OpenAI's own, so a request
that carried OpenAI's key in place of its own vendor's would show."""
GATEWAY_AUTH = "Bearer gw-auth-secret"
GATEWAY_KEY = "gk-gateway-secret"
ORG = "org-x"
PROJECT = "proj-y"
ADMIN = "sk-admin-not-a-real-key"
"""What `OPENAI_ADMIN_KEY` holds, which the SDK reads for itself and keeps for OpenAI's admin
endpoints. No request a pilot makes is to one, and none may carry it."""

STAND_IN = "https://openai.stand-in.invalid/v1"
"""Where a test that needs OpenAI's own API sends its requests, with quackd's rule told that this
is OpenAI's (`_as_openai`). `.invalid` is reserved, so no name under it resolves anywhere."""
GATEWAY = "https://gateway.stand-in.invalid/v1"
"""A gateway's address, as `OPENAI_BASE_URL` or `--base-url` gives it."""
BOARD = "http://jetson.local:11434/v1"
"""Ollama on a Jetson, over plain HTTP, which is where the leak this answers was found."""

PRESETS = ("openai", *OPENAI_COMPATIBLE, *LOCAL_NAMES)
"""Every pilot built on OpenAI's SDK, read from the factory."""
ELSEWHERE = tuple(preset for preset in PRESETS if preset != "openai")
"""Every pilot whose own address is not OpenAI's."""

LOCAL_URL = "http://127.0.0.1:8000/v1"
"""Where `--llm local` is pointed, the one preset with no address of its own."""

TOOLS = [{"name": "stop", "description": "Stop.", "input_schema": {"type": "object"}}]
SYSTEM = "You fly a mock duck."

runner = CliRunner()

KIND = fake_sdk.READ.get(openai.__version__)
READS_THE_VARIABLE = KIND != "1.66" if KIND else not openai.__version__.startswith("1.")
"""Whether the installed release adds `OPENAI_CUSTOM_HEADERS` to a request at all. 1.66.0 and
1.83.0 do not, and every release read after them does. One that was not read is taken to read it
unless it is a 1.x release."""


def _http_library() -> Any:
    """The HTTP library this release of the SDK is built on, httpx2 or, before 3.0, httpx."""
    for cls in openai.DefaultAsyncHttpxClient.__mro__:
        root = cls.__module__.split(".")[0]
        if root in ("httpx", "httpx2"):
            return importlib.import_module(root)
    raise AssertionError("the SDK's HTTP client is built on neither httpx nor httpx2")


HTTP = _http_library()
"""Read once, before any test puts a recorder where the SDK's HTTP client class was."""


def _answer(path: str) -> dict[str, Any]:
    """A turn that calls `stop`, in the shape of whichever API the request asked."""
    if path.endswith("/responses"):
        return {
            "id": "resp_1",
            "object": "response",
            "created_at": 0,
            "status": "completed",
            "model": "m",
            "output": [
                {
                    "type": "function_call",
                    "id": "fc_1",
                    "call_id": "call_1",
                    "name": "stop",
                    "arguments": "{}",
                    "status": "completed",
                }
            ],
            "parallel_tool_calls": False,
            "tool_choice": "required",
            "tools": [],
            "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
        }
    if path.endswith("/models"):
        return {"object": "list", "data": [{"id": "served-model", "object": "model"}]}
    return completion(1, "stop", {}, "m", {})


class Wire:
    """Where the socket would have been: every request the SDK sent, as it was sent, and an
    answer to each. `script` answers a request turn by turn where it is set, and `respond`
    answers it whole."""

    def __init__(self) -> None:
        self.requests: list[Any] = []
        self.script: Any = None
        self.respond: Any = None

    def handle(self, request: Any) -> Any:
        self.requests.append(request)
        if self.respond is not None:
            return self.respond(request)
        if self.script is not None:
            answer = self.script(len(self.requests))
        else:
            answer = _answer(request.url.path)
        return HTTP.Response(200, json=answer, request=request)

    def every(self, name: str, index: int = -1) -> list[str]:
        """Every value one request carried under `name`, in any spelling, in order."""
        return [v for k, v in self.requests[index].headers.multi_items() if k.lower() == name]

    def carrying(self, secret: str, index: int = -1) -> list[str]:
        """Every header of one request that carries `secret` anywhere in its value."""
        return [k for k, v in self.requests[index].headers.multi_items() if secret in v]


@pytest.fixture(autouse=True)
def _nothing_leaves_the_machine(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every lookup of a name and every connection to another machine raises, so a request that
    got past the transport would end here rather than reach anybody. Loopback is left alone,
    because the event loop on Windows connects a pair of its own sockets there."""
    connect = socket.socket.connect

    def refuse(*args: Any, **kwargs: Any) -> Any:
        raise OSError("this test lets nothing leave the machine")

    def loopback_only(self: socket.socket, address: Any) -> Any:
        if isinstance(address, tuple) and address and address[0] in ("127.0.0.1", "::1"):
            return connect(self, address)
        raise OSError("this test lets nothing leave the machine")

    monkeypatch.setattr(socket, "getaddrinfo", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket.socket, "connect", loopback_only)


@pytest.fixture
def wire(monkeypatch: pytest.MonkeyPatch) -> Wire:
    """The SDK's own HTTP client, built as the provider asks for it, on a transport that records.
    Built from the real class with the provider's own arguments, so `follow_redirects=False` is
    the provider's to say."""
    recorder = Wire()
    real = openai.DefaultAsyncHttpxClient

    def built(**kwargs: Any) -> Any:
        return real(transport=HTTP.MockTransport(recorder.handle), **kwargs)

    monkeypatch.setattr(openai, "DefaultAsyncHttpxClient", built)
    return recorder


@pytest.fixture(autouse=True)
def _a_gateway(monkeypatch: pytest.MonkeyPatch) -> None:
    """The variables as somebody who reaches OpenAI through a gateway might have them, beside an
    OpenAI key no other vendor may be sent and no local key at all."""
    monkeypatch.setenv("OPENAI_API_KEY", OPENAI_KEY)
    monkeypatch.setenv("OPENAI_ADMIN_KEY", ADMIN)
    monkeypatch.setenv(
        "OPENAI_CUSTOM_HEADERS", f"Authorization: {GATEWAY_AUTH}\nX-Gateway-Key: {GATEWAY_KEY}"
    )
    monkeypatch.setenv("OPENAI_ORG_ID", ORG)
    monkeypatch.setenv("OPENAI_PROJECT_ID", PROJECT)
    monkeypatch.setenv("OPENAI_BASE_URL", "")
    monkeypatch.setenv("QUACKD_BASE_URL", "")
    monkeypatch.setenv("LOCAL_API_KEY", "")


def _as_openai(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tell quackd's rule that `STAND_IN` is OpenAI's own API, so what goes to OpenAI goes there,
    and nothing is ever built for a host of OpenAI's. Told where the rule decides which address
    is the SDK's own and where the provider reads its client's address back."""
    real = openai_headers.is_openai_host

    def told(url: str) -> bool:
        return url.strip().rstrip("/") == STAND_IN or real(url)

    monkeypatch.setattr(openai_headers, "is_openai_host", told)
    monkeypatch.setattr(openai_provider, "is_openai_host", told)


def _built(monkeypatch: pytest.MonkeyPatch, preset: str, **kwargs: Any) -> Any:
    """`preset` built the way the CLI builds it, with its key set. OpenAI's own is pointed at the
    stand-in and told it is OpenAI's, unless the test gives it an address or leaves it to find
    one."""
    monkeypatch.setenv(KEY_ENV[preset], KEY)
    if preset == "openai" and "base_url" not in kwargs:
        _as_openai(monkeypatch)
        kwargs["base_url"] = STAND_IN
    if preset == "local":
        kwargs.setdefault("base_url", LOCAL_URL)
    if preset in LOCAL_NAMES:
        kwargs.setdefault("model", "served-model")
    return make_provider(preset, **kwargs)


async def _one_turn(provider: Any) -> None:
    turn = await provider.step(SYSTEM, [], TOOLS)
    assert [call.name for call in turn.tool_calls] == ["stop"]


async def _as_the_sdk_sends(provider: Any, key: str, wire: Wire) -> Any:
    """The same request from a client built as 0.17.1 built every pilot's, `AsyncOpenAI` with the
    key and the address and nothing else, which is what the SDK sends by itself."""
    alone = openai.AsyncOpenAI(
        api_key=key,
        base_url=provider.client_address(),
        http_client=openai.DefaultAsyncHttpxClient(),
    )
    if provider.api == "responses":
        await alone.responses.create(**provider._params_responses(SYSTEM, [], TOOLS))
    else:
        await alone.chat.completions.create(**provider._params(SYSTEM, [], TOOLS))
    return wire.requests[-1]


def _lines(request: Any) -> list[tuple[str, str]]:
    return sorted((k.lower(), v) for k, v in request.headers.multi_items())


# ── where the SDK's own variables do not go ─────────────────────────────────────────────────


@pytest.mark.parametrize("spelling", ["Authorization", "authorization", "AUTHORIZATION"])
@pytest.mark.parametrize("preset", ELSEWHERE)
async def test_away_from_the_sdks_own_address_one_authorization_leaves_and_nothing_ambient(
    preset: str, spelling: str, wire: Wire, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every line of the request read as it was sent, so a second authorization beside the first
    would show. The gateway's line in each spelling, and the vendor's own key is the one value
    that goes: never the gateway's, never `OPENAI_API_KEY` and never `OPENAI_ADMIN_KEY`. Nothing
    else the SDK reads for itself rides along to a vendor or a server you run: not the gateway's
    other line, and not OpenAI's account headers."""
    monkeypatch.setenv(
        "OPENAI_CUSTOM_HEADERS", f"{spelling}: {GATEWAY_AUTH}\nX-Gateway-Key: {GATEWAY_KEY}"
    )
    provider = _built(monkeypatch, preset)
    await _one_turn(provider)
    (request,) = wire.requests
    assert str(request.url).startswith(provider.client_address().rstrip("/") + "/")
    assert wire.every("authorization") == [f"Bearer {KEY}"]
    for name in ("x-gateway-key", "openai-organization", "openai-project"):
        assert wire.every(name) == [], name
    for secret in (OPENAI_KEY, ADMIN, GATEWAY_KEY, "gw-auth-secret"):
        assert wire.carrying(secret) == [], secret


@pytest.mark.parametrize("spelling", ["Authorization", "authorization", "AUTHORIZATION"])
async def test_at_openais_own_api_the_pilot_sends_exactly_what_the_sdk_sends(
    spelling: str, wire: Wire, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The variables are OpenAI's SDK's own, and at OpenAI's own API they go exactly as that SDK
    sends them by itself: the request carries every line the SDK alone puts on it, the gateway's
    `Authorization` wherever the release puts it, and nothing of quackd's beside. Never the admin
    key, which the SDK keeps for endpoints no pilot asks."""
    monkeypatch.setenv(
        "OPENAI_CUSTOM_HEADERS", f"{spelling}: {GATEWAY_AUTH}\nX-Gateway-Key: {GATEWAY_KEY}"
    )
    provider = _built(monkeypatch, "openai")
    await _one_turn(provider)
    (request,) = wire.requests
    alone = await _as_the_sdk_sends(provider, KEY, wire)
    assert _lines(request) == _lines(alone)
    assert wire.carrying(ADMIN, 0) == []
    assert wire.every("openai-organization", 0) == [ORG]


@pytest.mark.parametrize(
    ("lines", "in_the_sdks_spelling"),
    [
        ("OpenAI-Organization: gw-org\nOpenAI-Project: gw-proj", True),
        ("openai-organization: gw-org", False),
        ("OpenAI-Organization: gw-org\nopenai-organization: gw-org", False),
    ],
    ids=["the sdk's own spelling", "another spelling", "both spellings"],
)
@pytest.mark.parametrize("unset", [False, True], ids=["variables set", "variables unset"])
async def test_the_account_headers_go_as_the_sdk_sends_them_at_openai_and_nowhere_else(
    lines: str,
    in_the_sdks_spelling: bool,
    unset: bool,
    wire: Wire,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A gateway's line in the SDK's own spelling takes the place of the entry the SDK fills from
    `OPENAI_ORG_ID`, and the SDK sends it where the variable is unset. At OpenAI that is what
    goes, as the SDK sends it, where the rule as first built gave way to the variable or dropped
    the line. At another vendor reached with `--llm openai --base-url` neither goes, in any
    spelling."""
    monkeypatch.setenv("OPENAI_CUSTOM_HEADERS", lines)
    if unset:
        monkeypatch.delenv("OPENAI_ORG_ID")
        monkeypatch.delenv("OPENAI_PROJECT_ID")
    provider = _built(monkeypatch, "openai")
    await _one_turn(provider)
    alone = await _as_the_sdk_sends(provider, KEY, wire)
    assert _lines(wire.requests[0]) == _lines(alone)
    if READS_THE_VARIABLE and in_the_sdks_spelling:
        assert wire.every("openai-organization", 0) == ["gw-org"]
        assert wire.every("openai-project", 0) == ["gw-proj"]
    wire.requests.clear()
    await _one_turn(_built(monkeypatch, "openai", base_url="https://api.groq.com/openai/v1"))
    for name in ("openai-organization", "openai-project"):
        assert wire.every(name) == [], name


# ── the address `OPENAI_BASE_URL` gives ─────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "lines",
    [
        f"Authorization: {GATEWAY_AUTH}\nX-Gateway-Key: {GATEWAY_KEY}",
        "Helicone-Auth: Bearer hk-test-not-real",
        "cf-aig-authorization: Bearer cf-test-not-real",
    ],
    ids=["its own authorization", "helicone", "cloudflare"],
)
async def test_through_openai_base_url_a_gateway_is_sent_what_the_sdk_sends_it(
    lines: str, wire: Wire, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--llm openai` without `--base-url` follows `OPENAI_BASE_URL`, as the SDK does, and the
    variables set beside it go there as the SDK sends them: the gateway's own credential, under
    whatever name it takes it, and OpenAI's account headers for a gateway that passes them on.
    The rule as first built sent such a gateway OpenAI's key and nothing of its own, and the run
    stopped at its first turn. With the gateway's `Authorization`, the locked release sends that
    and not OpenAI's key."""
    monkeypatch.setenv("OPENAI_BASE_URL", GATEWAY)
    monkeypatch.setenv("OPENAI_CUSTOM_HEADERS", lines)
    provider = _built(monkeypatch, "openai", base_url=None)
    await _one_turn(provider)
    (request,) = wire.requests
    assert str(request.url).startswith(GATEWAY + "/")
    alone = await _as_the_sdk_sends(provider, KEY, wire)
    assert _lines(request) == _lines(alone)
    if READS_THE_VARIABLE:
        name, _, value = lines.split("\n")[0].partition(": ")
        assert wire.every(name.lower(), 0) == [value]
    if KIND == "3.22" and lines.startswith("Authorization"):
        assert wire.carrying(KEY, 0) == [], "OpenAI's key went to the gateway"


async def test_a_local_preset_that_reads_openai_base_url_is_sent_nothing_ambient(
    wire: Wire, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A local preset reads `OPENAI_BASE_URL` as one rung of quackd's own ladder, for a server you
    run, and it is not the SDK's own address: a `.env` that names a board there is sent the board's
    placeholder key and nothing the SDK reads for itself, never the gateway's credential in the
    clear."""
    monkeypatch.setenv("OPENAI_BASE_URL", BOARD)
    provider = make_provider("ollama", model="qwen3:8b")
    await _one_turn(provider)
    (request,) = wire.requests
    assert str(request.url) == f"{BOARD}/chat/completions"
    assert wire.every("authorization") == ["Bearer not-needed"]
    for name in ("x-gateway-key", "openai-organization", "openai-project"):
        assert wire.every(name) == [], name
    for secret in (OPENAI_KEY, GATEWAY_KEY, "gw-auth-secret"):
        assert wire.carrying(secret) == [], secret


@pytest.mark.parametrize(
    ("preset", "base_url"),
    [("openai", GATEWAY), ("vllm", "https://llm.stand-in.invalid/v1")],
    ids=["a gateway", "an access proxy"],
)
async def test_an_address_given_with_base_url_is_sent_the_key_alone(
    preset: str, base_url: str, wire: Wire, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--base-url` is an address quackd was told, not the SDK's own, so whatever is behind it is
    sent the key and none of the variable's lines: not a gateway's own authorization, and not an
    access proxy's pair of headers, which a server behind Cloudflare Access wants. This is the
    limitation the change leaves, and the way round it is `OPENAI_BASE_URL` with `--llm openai`,
    or the gateway's token as the key where it takes it as the authorization."""
    monkeypatch.setenv(
        "OPENAI_CUSTOM_HEADERS",
        f"Authorization: {GATEWAY_AUTH}\n"
        "CF-Access-Client-Id: id-not-real\nCF-Access-Client-Secret: secret-not-real",
    )
    await _one_turn(_built(monkeypatch, preset, base_url=base_url))
    (request,) = wire.requests
    assert str(request.url).startswith(base_url + "/")
    assert wire.every("authorization") == [f"Bearer {KEY}"]
    for name in ("cf-access-client-id", "cf-access-client-secret", "openai-organization"):
        assert wire.every(name) == [], name


# ── a server you run ────────────────────────────────────────────────────────────────────────


async def test_a_local_model_on_a_board_over_plain_http_is_sent_the_key_and_nothing_else(
    wire: Wire, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The case this was found on: `--llm ollama --host jetson.local`, a model server on a Jetson,
    reached over plain HTTP, where every header crosses the network in the clear. Before, it was
    sent the gateway's authorization in place of the key, the gateway's own credential, and
    OpenAI's account headers."""
    monkeypatch.setenv("LOCAL_API_KEY", KEY)
    provider = make_provider("ollama", model="qwen3:8b", host="jetson.local")
    await _one_turn(provider)
    (request,) = wire.requests
    assert str(request.url) == f"{BOARD}/chat/completions"
    assert wire.every("authorization") == [f"Bearer {KEY}"]
    for name in ("x-gateway-key", "openai-organization", "openai-project"):
        assert wire.every(name) == [], name


async def test_a_board_with_no_local_key_is_sent_the_placeholder_and_never_openais_key(
    wire: Wire, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With no `LOCAL_API_KEY`, a server you run is sent `not-needed`. It used to be sent
    `OPENAI_API_KEY` whenever that was set, so the board above got a hosted key in the clear."""
    provider = make_provider("ollama", model="qwen3:8b", host="jetson.local")
    await _one_turn(provider)
    assert wire.every("authorization") == ["Bearer not-needed"]
    assert wire.carrying(OPENAI_KEY) == []


def test_a_whole_run_on_a_board_sends_the_key_on_every_turn_and_nothing_ambient(
    wire: Wire, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`quackd run` end to end, through the CLI, with a local model on a Jetson over plain HTTP:
    every turn the run asked carried the key once and nothing the SDK read for itself."""
    script = SCRIPTS["hello-world"]
    wire.script = lambda n: completion(n, *script[n - 1], "qwen3", {})
    monkeypatch.setenv("LOCAL_API_KEY", KEY)
    result = runner.invoke(
        app,
        [
            "--no-color",
            "run",
            "hello-world",
            "--llm",
            "local:qwen3",
            "--base-url",
            BOARD,
            "--robot",
            "microduck:mock",
            "--runs-dir",
            str(tmp_path),
            "--no-gif",
            "--no-memory",
        ],
    )
    assert result.exit_code == 0, result.output
    assert len(wire.requests) == len(script)
    for index, request in enumerate(wire.requests):
        assert str(request.url) == f"{BOARD}/chat/completions"
        assert wire.every("authorization", index) == [f"Bearer {KEY}"]
        for name in ("x-gateway-key", "openai-organization", "openai-project"):
            assert wire.every(name, index) == [], name
        assert wire.carrying(OPENAI_KEY, index) == []
        assert json.loads(request.content)["model"] == "qwen3"


# ── the SDK's own headers ───────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("preset", ["grok", "vllm", "openrouter"])
async def test_a_line_naming_a_header_the_sdk_sends_itself_changes_nothing_on_the_wire(
    preset: str, wire: Wire, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A line of the variable that names one of the SDK's own headers is somebody else's, so its
    value goes nowhere, and it used to take the SDK's own value with it: the HTTP library's
    `User-Agent` in place of the SDK's, and a JSON body with no `Content-Type` at all. The request
    is now the one the same provider sends with no such line, header for header."""
    monkeypatch.setenv("OPENAI_CUSTOM_HEADERS", "")
    url = {"base_url": "https://llm.stand-in.invalid/v1"} if preset == "vllm" else {}
    await _one_turn(_built(monkeypatch, preset, **url))
    monkeypatch.setenv(
        "OPENAI_CUSTOM_HEADERS",
        "User-Agent: corp-allowlisted/1.0\ncontent-type: application/json; charset=utf-8\n"
        "ACCEPT: text/html\nAccept-Encoding: identity\nX-Stainless-Lang: cobol",
    )
    await _one_turn(_built(monkeypatch, preset, **url))
    plain, lined = wire.requests
    assert _lines(lined) == _lines(plain)


# ── a vendor's own headers ──────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "spellings",
    [
        "its own spelling alone",
        "another spelling alone",
        "its own spelling, then another",
        "another spelling, then its own",
    ],
)
@pytest.mark.parametrize("name", list(ATTRIBUTION))
async def test_a_line_naming_one_of_openrouters_own_headers_leaves_openrouters_value(
    name: str, spellings: str, wire: Wire, monkeypatch: pytest.MonkeyPatch
) -> None:
    """OpenRouter's two headers are OpenRouter's, and a line of the variable that names either is
    somebody else's. Named in OpenRouter's own spelling and then in another, the locked release
    sent neither the line nor OpenRouter's value: it merges in the variable's order without regard
    to case, and the `Omit` quackd handed under the second spelling took OpenRouter's value away
    from the first. Every spelling the variable gives now carries OpenRouter's value, so the
    request is the one sent with no such line, header for header, wherever the variable names the
    header once or the release merges spellings, and a release that keeps them apart sends
    OpenRouter's value under each of the two."""
    other = name.lower()
    named = {
        "its own spelling alone": [name],
        "another spelling alone": [other],
        "its own spelling, then another": [name, other],
        "another spelling, then its own": [other, name],
    }[spellings]
    monkeypatch.setenv("OPENAI_CUSTOM_HEADERS", "")
    await _one_turn(_built(monkeypatch, "openrouter"))
    monkeypatch.setenv(
        "OPENAI_CUSTOM_HEADERS", "\n".join(f"{n}: elsewhere-{i}" for i, n in enumerate(named))
    )
    await _one_turn(_built(monkeypatch, "openrouter"))
    plain, lined = wire.requests
    assert wire.carrying("elsewhere") == []
    values = wire.every(other)
    assert values and set(values) == {ATTRIBUTION[name]}
    if len(named) == 1 or KIND == "3.22":
        assert _lines(lined) == _lines(plain)
    elif KIND is not None:
        assert values == [ATTRIBUTION[name]] * 2


# ── a turn answered with a redirect ─────────────────────────────────────────────────────────


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
async def test_a_redirect_is_never_followed_so_nothing_reaches_where_it_points(
    status: int, wire: Wire, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The SDK's HTTP client follows a redirect unless it is told not to, and on a 307 or a 308
    it sends the same request again, the robot's state and its camera frames in it, to wherever
    the redirect points, anybody's host in the world. So the vendor answers with one, exactly one
    request is sent, and the turn is an error that says it was a redirect, where it pointed, and
    that it was not followed."""

    def redirect(request: Any) -> Any:
        return HTTP.Response(
            status,
            headers={"Location": "http://elsewhere.example/v1/chat/completions"},
            request=request,
        )

    wire.respond = redirect
    provider = _built(monkeypatch, "grok")
    with pytest.raises(ProviderError) as e:
        await provider.step(SYSTEM, [], TOOLS)
    assert len(wire.requests) == 1, "the redirect was followed"
    assert wire.requests[0].url.host == "api.x.ai"
    assert str(e.value) == (
        f"grok: https://api.x.ai/v1 answered with a redirect ({status}) to "
        "http://elsewhere.example/v1/chat/completions, so it was not followed: if the server is "
        "there now, pass --base-url http://elsewhere.example/v1"
    )


@pytest.mark.parametrize("model", ["served-model", None], ids=["a turn", "discovery"])
async def test_a_server_that_moved_to_https_is_named_with_the_address_to_give(
    model: str | None, wire: Wire, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A model server behind a proxy that sends every plain-HTTP request on to its HTTPS twin
    used to be followed there, quietly. It is not now, so the sentence says where it pointed and
    the `--base-url` that reaches it, whether the first request was a turn or the model list."""

    def upgrade(request: Any) -> Any:
        if request.url.scheme == "http":
            target = str(request.url).replace("http://", "https://", 1)
            return HTTP.Response(308, headers={"Location": target}, request=request)
        return HTTP.Response(200, json=_answer(request.url.path), request=request)

    wire.respond = upgrade
    monkeypatch.setenv("LOCAL_API_KEY", KEY)
    provider = make_provider("vllm", model=model, base_url="http://llm.stand-in.invalid/v1")
    with pytest.raises(ProviderError) as e:
        await provider.step(SYSTEM, [], TOOLS)
    assert len(wire.requests) == 1
    path = "chat/completions" if model else "models"
    assert str(e.value) == (
        "vllm: http://llm.stand-in.invalid/v1 answered with a redirect (308) to "
        f"https://llm.stand-in.invalid/v1/{path}, so it was not followed: if the server is there "
        "now, pass --base-url https://llm.stand-in.invalid/v1"
    )


# ── the model of the installed release ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "lines",
    [
        f"Authorization: {GATEWAY_AUTH}\nX-Gateway-Key: {GATEWAY_KEY}",
        f"authorization: {GATEWAY_AUTH}\nAUTHORIZATION: {GATEWAY_AUTH}",
        "OpenAI-Organization: gw-org\nopenai-project: gw-proj",
        "HTTP-Referer: https://elsewhere.example\nx-openrouter-title: x",
        "X-OpenRouter-Title: x\nx-openrouter-title: y\nhttp-referer: a\nHTTP-Referer: b",
        "User-Agent: corp\ncontent-type: text/plain\nx-stainless-os: plan9",
    ],
)
@pytest.mark.parametrize("preset", ["openai", "openrouter", "ollama"])
async def test_the_suites_model_of_the_installed_release_is_the_installed_release(
    preset: str, lines: str, wire: Wire, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`tests/fake_openai_sdk.py` is what CI holds the rule to, release by release, and this is
    where its word on the installed release is checked against the real client: handed the same
    headers, and saying the same about itself, the model says the same lines go as the real SDK
    sent."""
    kind = fake_sdk.READ.get(openai.__version__)
    if kind is None:
        pytest.skip(f"openai {openai.__version__} has not been read, so nothing models it")
    handed: dict[str, Any] = {}
    clients: list[Any] = []
    real = openai.AsyncOpenAI

    def recorded(**kwargs: Any) -> Any:
        handed.clear()
        handed.update(kwargs)
        clients.append(real(**kwargs))
        return clients[-1]

    monkeypatch.setattr(openai, "AsyncOpenAI", recorded)
    monkeypatch.setenv("OPENAI_CUSTOM_HEADERS", lines)
    await _one_turn(_built(monkeypatch, preset))
    (request,) = wire.requests
    modelled = {
        name: fake_sdk.Omit() if isinstance(value, openai.Omit) else value
        for name, value in (handed.get("default_headers") or {}).items()
    }
    client = clients[-1]
    fake = fake_sdk.FakeOpenAI(user_agent=client.user_agent, platform=client.platform_headers())
    fake(api_key=handed["api_key"], default_headers=modelled)
    names = {"authorization", "openai-organization", "openai-project", "user-agent"}
    names |= {"accept", "content-type"} | {name.lower() for name in client.platform_headers()}
    names |= {name.lower() for name in modelled}
    names |= {name.lower() for name in fake_sdk.variable() or {}}
    sent = sorted((k.lower(), v) for k, v in request.headers.multi_items() if k.lower() in names)
    model = sorted((k.lower(), v) for k, v in fake_sdk.sent(fake, kind) if k.lower() in names)
    assert sent == model
