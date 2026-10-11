"""Every pilot that speaks OpenAI's API, through the real `openai` SDK, with the network shut.

Skipped wherever the SDK is not installed, which includes CI: nothing in the suite installs a
provider SDK. To run it, sync an environment with the extra and run pytest there:

    UV_PROJECT_ENVIRONMENT=<scratch> uv sync --frozen --extra dev --extra openai

It has run, all of it, against three releases of the SDK, on 2026-10-11, and against no other:
3.22.1, which the lock carries and that sync installs; 3.1.0, the first release in uv's cache that
reads `OPENAI_CUSTOM_HEADERS`; and 1.66.0, the extras' floor, which sends through `httpx` rather
than `httpx2`. The last two ran from uv's cache in scratch environments of their own.
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
is the one thing that decides what goes there, and the request goes to the stand-in.

Every test runs with the variables the SDK reads for itself set the way somebody who reaches
OpenAI through a gateway might have them in a `.env`, so every test shows what quackd does about
each of them.
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
from quackd.agent.providers.base import ProviderError
from quackd.agent.providers.catalogue import LOCAL_NAMES
from quackd.agent.providers.factory import KEY_ENV, OPENAI_COMPATIBLE, make_provider
from quackd.cli import app
from tests import fake_openai_sdk as fake_sdk
from tests.fake_openrouter import SCRIPTS, completion

KEY = "sk-test-not-a-real-key"
"""Never a real one: nothing here leaves the machine, and a fake key is what proves it."""
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

PRESETS = ("openai", *OPENAI_COMPATIBLE, *LOCAL_NAMES)
"""Every pilot built on OpenAI's SDK, read from the factory."""

LOCAL_URL = "http://127.0.0.1:8000/v1"
"""Where `--llm local` is pointed, the one preset with no address of its own."""

TOOLS = [{"name": "stop", "description": "Stop.", "input_schema": {"type": "object"}}]

runner = CliRunner()

KIND = fake_sdk.READ.get(openai.__version__)
READS_THE_VARIABLE = KIND != "1.66" if KIND else not openai.__version__.startswith("1.")
"""Whether the installed release adds `OPENAI_CUSTOM_HEADERS` to a request at all. 1.66.0 and
1.83.0 do not, and every release read after them does. One that was not read is taken to read it
unless it is a 1.x release, which decides only what OpenAI's own API is expected to be sent of the
gateway's other line, and nothing about what any other address is sent."""


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
    """The variables as somebody who reaches OpenAI through a gateway might have them."""
    monkeypatch.setenv("OPENAI_API_KEY", KEY)
    monkeypatch.setenv("OPENAI_ADMIN_KEY", ADMIN)
    monkeypatch.setenv(
        "OPENAI_CUSTOM_HEADERS", f"Authorization: {GATEWAY_AUTH}\nX-Gateway-Key: {GATEWAY_KEY}"
    )
    monkeypatch.setenv("OPENAI_ORG_ID", ORG)
    monkeypatch.setenv("OPENAI_PROJECT_ID", PROJECT)
    monkeypatch.setenv("OPENAI_BASE_URL", "")


def _as_openai(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tell quackd's rule that `STAND_IN` is OpenAI's own API, so what goes to OpenAI goes there,
    and nothing is ever built for a host of OpenAI's."""
    real = openai_provider.is_openai_host
    monkeypatch.setattr(
        openai_provider,
        "is_openai_host",
        lambda url: url.strip().rstrip("/") == STAND_IN or real(url),
    )


def _built(monkeypatch: pytest.MonkeyPatch, preset: str, **kwargs: Any) -> Any:
    """`preset` built the way the CLI builds it, with its key set. OpenAI's own is pointed at the
    stand-in and told it is OpenAI's."""
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
    turn = await provider.step("You fly a mock duck.", [], TOOLS)
    assert [call.name for call in turn.tool_calls] == ["stop"]


@pytest.mark.parametrize("spelling", ["Authorization", "authorization", "AUTHORIZATION"])
@pytest.mark.parametrize("preset", PRESETS)
async def test_one_authorization_leaves_and_nothing_ambient_reaches_a_vendor_that_is_not_openai(
    preset: str, spelling: str, wire: Wire, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every line of the request read as it was sent, so a second authorization beside the first
    would show. The gateway's line in each spelling, and the vendor's key is the one value that
    goes, never `OPENAI_ADMIN_KEY` either. Away from OpenAI's own API nothing else the SDK reads
    for itself rides along: not the gateway's other line, and not OpenAI's account headers. At
    OpenAI's own API the account headers go as the SDK fills them, and the gateway's other line as
    the SDK sends it."""
    monkeypatch.setenv(
        "OPENAI_CUSTOM_HEADERS", f"{spelling}: {GATEWAY_AUTH}\nX-Gateway-Key: {GATEWAY_KEY}"
    )
    provider = _built(monkeypatch, preset)
    await _one_turn(provider)
    (request,) = wire.requests
    assert str(request.url).startswith(provider.client_address().rstrip("/") + "/")
    assert wire.every("authorization") == [f"Bearer {KEY}"]
    if preset == "openai":
        assert wire.every("openai-organization") == [ORG]
        assert wire.every("openai-project") == [PROJECT]
        assert wire.every("x-gateway-key") == ([GATEWAY_KEY] if READS_THE_VARIABLE else [])
    else:
        for name in ("x-gateway-key", "openai-organization", "openai-project"):
            assert wire.every(name) == [], name


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
    assert str(request.url) == "http://jetson.local:11434/v1/chat/completions"
    assert wire.every("authorization") == [f"Bearer {KEY}"]
    for name in ("x-gateway-key", "openai-organization", "openai-project"):
        assert wire.every(name) == [], name


@pytest.mark.parametrize("at_openai", [False, True], ids=["groq", "openai"])
@pytest.mark.parametrize(
    "lines",
    [
        "OpenAI-Organization: gw-org\nOpenAI-Project: gw-proj",
        "openai-organization: gw-org",
        "OpenAI-Organization: gw-org\nopenai-organization: gw-org",
    ],
    ids=["the sdk's own spelling", "another spelling", "both spellings"],
)
async def test_the_account_headers_carry_the_variables_at_openai_and_go_nowhere_else(
    lines: str, at_openai: bool, wire: Wire, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A gateway's line named in the SDK's own spelling took the place of the entry the SDK fills
    from `OPENAI_ORG_ID`. At OpenAI each account header carries its variable's value and no other,
    whatever the gateway's line says, and at another vendor reached with `--llm openai
    --base-url`, neither goes. A release that keeps two spellings of one name sends the variable's
    value under both, and the locked one, which merges them, sends it once."""
    monkeypatch.setenv("OPENAI_CUSTOM_HEADERS", lines)
    base_url = {} if at_openai else {"base_url": "https://api.groq.com/openai/v1"}
    await _one_turn(_built(monkeypatch, "openai", **base_url))
    for name, value in (("openai-organization", ORG), ("openai-project", PROJECT)):
        sent = wire.every(name)
        assert set(sent) == ({value} if at_openai else set()), name
        if KIND == "3.22":
            assert len(sent) == (1 if at_openai else 0), name


async def test_openais_own_preset_is_decided_where_the_sdks_address_variable_sends_it(
    wire: Wire, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`OPENAI_BASE_URL` is what quackd's local presets read as a model server on your own
    machine, and `--llm openai` follows it too. The headers are decided on it, and nothing
    ambient goes there."""
    monkeypatch.setenv("OPENAI_BASE_URL", "http://jetson.local:11434/v1")
    await _one_turn(_built(monkeypatch, "openai", base_url=None))
    (request,) = wire.requests
    assert str(request.url).startswith("http://jetson.local:11434/v1/")
    assert wire.every("authorization") == [f"Bearer {KEY}"]
    for name in ("x-gateway-key", "openai-organization", "openai-project"):
        assert wire.every(name) == [], name


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
async def test_a_redirect_is_never_followed_so_nothing_reaches_where_it_points(
    status: int, wire: Wire, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The SDK's HTTP client follows a redirect unless it is told not to, and on a 307 or a 308
    it sends the same request again, the robot's state and its camera frames in it, to wherever
    the redirect points, anybody's host in the world. So the vendor answers with one, exactly one
    request is sent, and the turn is an error that says what came back."""

    def redirect(request: Any) -> Any:
        return HTTP.Response(
            status,
            headers={"Location": "http://elsewhere.example/v1/chat/completions"},
            request=request,
        )

    wire.respond = redirect
    provider = _built(monkeypatch, "grok")
    with pytest.raises(ProviderError, match=str(status)):
        await provider.step("You fly a mock duck.", [], TOOLS)
    assert len(wire.requests) == 1, "the redirect was followed"
    assert wire.requests[0].url.host == "api.x.ai"


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
            "http://jetson.local:11434/v1",
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
        assert str(request.url) == "http://jetson.local:11434/v1/chat/completions"
        assert wire.every("authorization", index) == [f"Bearer {KEY}"]
        for name in ("x-gateway-key", "openai-organization", "openai-project"):
            assert wire.every(name, index) == [], name
        assert json.loads(request.content)["model"] == "qwen3"


@pytest.mark.parametrize(
    "lines",
    [
        f"Authorization: {GATEWAY_AUTH}\nX-Gateway-Key: {GATEWAY_KEY}",
        f"authorization: {GATEWAY_AUTH}\nAUTHORIZATION: {GATEWAY_AUTH}",
        "OpenAI-Organization: gw-org\nopenai-project: gw-proj",
        "HTTP-Referer: https://elsewhere.example\nx-openrouter-title: x",
    ],
)
@pytest.mark.parametrize("preset", ["openai", "openrouter", "ollama"])
async def test_the_suites_model_of_the_installed_release_is_the_installed_release(
    preset: str, lines: str, wire: Wire, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`tests/fake_openai_sdk.py` is what CI holds the rule to, release by release, and this is
    where its word on the installed release is checked against the real client: handed the same
    headers, the model says the same lines go as the real SDK sent."""
    kind = fake_sdk.READ.get(openai.__version__)
    if kind is None:
        pytest.skip(f"openai {openai.__version__} has not been read, so nothing models it")
    handed: dict[str, Any] = {}
    real = openai.AsyncOpenAI

    def recorded(**kwargs: Any) -> Any:
        handed.update(kwargs)
        return real(**kwargs)

    monkeypatch.setattr(openai, "AsyncOpenAI", recorded)
    monkeypatch.setenv("OPENAI_CUSTOM_HEADERS", lines)
    await _one_turn(_built(monkeypatch, preset))
    (request,) = wire.requests
    modelled = {
        name: fake_sdk.Omit() if isinstance(value, openai.Omit) else value
        for name, value in handed["default_headers"].items()
    }
    fake = fake_sdk.FakeOpenAI()(api_key=handed["api_key"], default_headers=modelled)
    names = {"authorization", "openai-organization", "openai-project"}
    names |= {name.lower() for name in modelled}
    names |= {name.lower() for name in fake_sdk.variable() or {}}
    sent = sorted((k.lower(), v) for k, v in request.headers.multi_items() if k.lower() in names)
    model = sorted((k.lower(), v) for k, v in fake_sdk.sent(fake, kind) if k.lower() in names)
    assert sent == model
