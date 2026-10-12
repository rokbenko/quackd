"""What a pilot that speaks OpenAI's API sends besides the request itself: the key, the headers
OpenAI's SDK adds by itself, and the address all of it goes to.

The SDK reads `OPENAI_CUSTOM_HEADERS`, `OPENAI_ORG_ID` and `OPENAI_PROJECT_ID` out of the
environment and adds them to every request, wherever the client points. A gateway's
`Authorization` line in the first replaced the vendor's key, and it, the gateway's other lines and
OpenAI's account headers went to every vendor built on the SDK and to every model server you run.
They go now where the SDK itself would send them and nowhere else: OpenAI's own API, and the
address `OPENAI_BASE_URL` gives `--llm openai` when it is asked for no other.
`quackd.agent.providers.openai_headers` holds the rule, and `OpenAIProvider._build_client` builds
every one of those clients by it.

Nothing here needs the `openai` package: CI installs no provider SDK, and every test that builds a
client builds `tests/fake_openai_sdk.py` in its place, whose model of each kind of release the
extras allow is what holds the rule to all of them. `tests/test_openai_headers_wire.py` drives
the real SDK wherever it is installed.
"""

from __future__ import annotations

import ast
import importlib.abc
import sys
from types import SimpleNamespace as NS
from typing import Any

import pytest

from quackd.agent.providers import openai_headers
from quackd.agent.providers.base import ProviderError, ProviderNotInstalled
from quackd.agent.providers.catalogue import LOCAL_NAMES
from quackd.agent.providers.factory import KEY_ENV, OPENAI_COMPATIBLE, make_provider
from quackd.agent.providers.openai import DEFAULT_BASE_URL, _where_it_points
from quackd.agent.providers.openai_headers import (
    OPENAI_HOSTS,
    SENDS_JSON,
    ambient_headers,
    foreign_authorization,
    foreign_headers,
    is_openai_host,
    request_headers,
    sdks_own_address,
)
from quackd.agent.providers.openrouter import ATTRIBUTION
from tests import fake_openai_sdk as fake_sdk
from tests.conftest import REPO

KEY = "sk-test-not-a-real-key"
"""Never a real one: nothing here leaves the machine, and a fake key is what proves it. The key
of whichever preset a test builds."""
OPENAI_KEY = "sk-openai-not-a-real-key"
"""What `OPENAI_API_KEY` holds wherever the preset a test builds is not OpenAI's own, so a client
that sent OpenAI's key in place of its own vendor's would show."""
GATEWAY_AUTH = "Bearer gw-auth-secret"
"""The authorization a gateway's `OPENAI_CUSTOM_HEADERS` carries, which must never replace the
vendor's key on its way to anywhere but the gateway."""
GATEWAY_KEY = "gk-gateway-secret"
"""A gateway's own credential under a name of its own. It goes where the SDK sends it, which is
OpenAI's own API and the gateway `OPENAI_BASE_URL` names, and nowhere else."""
ORG = "org-x"
PROJECT = "proj-y"
GATEWAY = "https://gateway.stand-in.invalid/v1"
"""A gateway's address, as `OPENAI_BASE_URL` or `--base-url` gives it. `.invalid` is reserved, so
no name under it resolves anywhere."""

#: Every variable the SDK reads for itself that the rule has an answer for.
SDK_VARIABLES = (
    "OPENAI_API_KEY",
    "OPENAI_BASE_URL",
    "OPENAI_CUSTOM_HEADERS",
    "OPENAI_ORG_ID",
    "OPENAI_PROJECT_ID",
)

PRESETS = ("openai", *OPENAI_COMPATIBLE, *LOCAL_NAMES)
"""Every pilot built on OpenAI's SDK: OpenAI's own, every vendor that speaks its API, and every
local preset. Read from the factory, so a vendor added there is held to this file on the day it
arrives."""

ELSEWHERE = tuple(preset for preset in PRESETS if preset != "openai")
"""Every pilot whose own address is not OpenAI's: a vendor's API or a server you run."""

LOCAL_URL = "http://127.0.0.1:8000/v1"
"""Where `--llm local` is pointed, the one preset with no address of its own."""


@pytest.fixture(autouse=True)
def _no_ambient_openai(monkeypatch: pytest.MonkeyPatch) -> None:
    """A developer who pilots with OpenAI has these in a `.env`, and the CLI loads it. Empty
    rather than deleted, because an empty value survives `load_dotenv` and a deleted one does
    not; the tests that mean one to be unset delete it themselves. `LOCAL_API_KEY` too, which a
    local preset reads before anything else."""
    for name in (*SDK_VARIABLES, "LOCAL_API_KEY"):
        monkeypatch.setenv(name, "")


def _built(
    monkeypatch: pytest.MonkeyPatch,
    preset: str,
    *,
    release: str | None = None,
    **kwargs: Any,
) -> tuple[Any, fake_sdk.FakeOpenAI]:
    """`preset` built the way the CLI builds it, with its key set, against the fake SDK."""
    fake = fake_sdk.install(monkeypatch, fake_sdk.FakeOpenAI(release=release))
    monkeypatch.setenv(KEY_ENV[preset], KEY)
    if preset == "local":
        kwargs.setdefault("base_url", LOCAL_URL)
    return make_provider(preset, **kwargs), fake


def _gateway(monkeypatch: pytest.MonkeyPatch, spelling: str = "Authorization") -> None:
    """The variables as somebody who reaches OpenAI through a gateway might have them, beside an
    OpenAI key of their own that no other vendor may be sent."""
    monkeypatch.setenv(
        "OPENAI_CUSTOM_HEADERS", f"{spelling}: {GATEWAY_AUTH}\nX-Gateway-Key: {GATEWAY_KEY}"
    )
    monkeypatch.setenv("OPENAI_ORG_ID", ORG)
    monkeypatch.setenv("OPENAI_PROJECT_ID", PROJECT)
    monkeypatch.setenv("OPENAI_API_KEY", OPENAI_KEY)


def _alone(fake: fake_sdk.FakeOpenAI, release: str) -> list[tuple[str, str]]:
    """What `release` sends from a client built with the same key and address and nothing else,
    which is what the SDK sends by itself."""
    alone = fake_sdk.FakeOpenAI(release=release)(
        api_key=fake.kwargs["api_key"], base_url=fake.kwargs["base_url"]
    )
    return fake_sdk.sent(alone, release)


def _carries(lines: list[tuple[str, str]], secret: str) -> list[str]:
    """Every header that carries `secret` anywhere in its value."""
    return [name for name, value in lines if secret in value]


# ── every preset, under every kind of release ───────────────────────────────────────────────


@pytest.mark.parametrize("release", fake_sdk.RELEASES)
@pytest.mark.parametrize("spelling", ["Authorization", "authorization", "AUTHORIZATION"])
@pytest.mark.parametrize("preset", ELSEWHERE)
def test_away_from_the_sdks_own_address_one_authorization_leaves_and_nothing_ambient(
    preset: str, spelling: str, release: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One authorization line leaves, and it is the vendor's own key, whichever way the release
    merges and whichever spelling the gateway used: never the gateway's, and never OpenAI's key
    either. Nothing else the SDK reads for itself goes to a vendor or to a server you run: not the
    gateway's other lines, and not OpenAI's account headers.

    Before, the release the lock carries sent the gateway's authorization in place of the key on
    every request, to every vendor and every server. The releases read from 3.1.0 to 3.19.0 did
    that for `Authorization` and sent any other spelling beside the key, and every release read
    sent the account headers everywhere."""
    _gateway(monkeypatch, spelling)
    provider, fake = _built(monkeypatch, preset, release=release)
    assert not is_openai_host(provider.client_address())
    lines = fake_sdk.sent(fake, release)
    assert fake_sdk.every(lines, "authorization") == [f"Bearer {KEY}"]
    for name in ("x-gateway-key", "openai-organization", "openai-project"):
        assert fake_sdk.every(lines, name) == [], name
    for secret in (OPENAI_KEY, GATEWAY_KEY, "gw-auth-secret"):
        assert _carries(lines, secret) == [], secret


@pytest.mark.parametrize("release", fake_sdk.RELEASES)
@pytest.mark.parametrize("spelling", ["Authorization", "authorization", "AUTHORIZATION"])
def test_at_openais_own_api_the_client_is_built_as_the_sdk_builds_it(
    spelling: str, release: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The variables are OpenAI's SDK's own, and at OpenAI's own API they go exactly as that SDK
    sends them by itself: the client is handed no header of quackd's, so a gateway's
    `Authorization` line goes where the release puts it, and the account headers and the other
    lines with it. That is what every release did before quackd answered any of it, and what the
    rule as first built took away: the account headers' lines in the SDK's own spelling, and the
    gateway's own `Authorization`, which quackd named the key over."""
    _gateway(monkeypatch, spelling)
    provider, fake = _built(monkeypatch, "openai", release=release)
    assert provider.client_address() == DEFAULT_BASE_URL
    assert "default_headers" not in fake.kwargs, "quackd named a header at OpenAI's own API"
    assert fake_sdk.sent(fake, release) == _alone(fake, release)


def test_only_openais_own_preset_at_its_own_address_is_at_openai(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The two tests above split the presets by the address each client is handed. So they say
    something about both halves only if both halves are there: OpenAI's own, and every other."""
    at_openai = [
        preset
        for preset in PRESETS
        if is_openai_host(_built(monkeypatch, preset)[0].client_address())
    ]
    assert at_openai == ["openai"]


def test_only_openais_own_preset_takes_the_sdks_address(monkeypatch: pytest.MonkeyPatch) -> None:
    """The SDK's own variables follow the SDK's own address variable, and only a provider asked
    for no address of its own follows that: OpenAI's, without `--base-url`. Every vendor has an
    address of its own, and every local preset one off its own ladder, so a vendor added without
    one would take `OPENAI_BASE_URL` for its own and send its key and the SDK's variables there.
    This finds one on the day it arrives."""
    takers = [
        preset for preset in PRESETS if _built(monkeypatch, preset)[0].takes_the_sdks_address()
    ]
    assert takers == ["openai"]
    assert not _built(monkeypatch, "openai", base_url=GATEWAY)[0].takes_the_sdks_address()


VENDOR_SPELLINGS = (
    "its own spelling alone",
    "another spelling alone",
    "its own spelling, then another",
    "another spelling, then its own",
)
"""How lines of the variable may name one of a vendor's own headers: once, in the vendor's
spelling or in another, or twice, in both and in either order."""


def _spelled(name: str, spellings: str) -> list[str]:
    """`name` as `spellings`, one of `VENDOR_SPELLINGS`, gives it, in the variable's order."""
    other = name.lower()
    return {
        "its own spelling alone": [name],
        "another spelling alone": [other],
        "its own spelling, then another": [name, other],
        "another spelling, then its own": [other, name],
    }[spellings]


@pytest.mark.parametrize("release", fake_sdk.RELEASES)
@pytest.mark.parametrize("spellings", VENDOR_SPELLINGS)
@pytest.mark.parametrize("name", list(ATTRIBUTION))
def test_a_vendors_own_headers_still_go_whatever_the_variable_says_about_them(
    name: str, spellings: str, release: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """OpenRouter credits an app by two headers, and they are OpenRouter's to receive. A line of
    the variable that names either, in OpenRouter's spelling or another, is somebody else's, so
    its value goes nowhere and OpenRouter's own goes in its place.

    Named in OpenRouter's own spelling and then in another, the header went nowhere at all in
    3.22.1, which merges the variable's slots in the variable's order without regard to case: the
    `Omit` quackd handed under the second spelling took OpenRouter's value away from the first.
    Every spelling the variable gives now carries OpenRouter's value, so a release that merges
    spellings sends it once, and a release that keeps them apart sends it once for each spelling
    the variable gave."""
    named = _spelled(name, spellings)
    monkeypatch.setenv(
        "OPENAI_CUSTOM_HEADERS", "\n".join(f"{n}: elsewhere-{i}" for i, n in enumerate(named))
    )
    _provider, fake = _built(monkeypatch, "openrouter", release=release)
    lines = fake_sdk.sent(fake, release)
    twice = len(named) == 2 and release != "3.22"
    assert fake_sdk.every(lines, name) == [ATTRIBUTION[name]] * (2 if twice else 1)
    assert _carries(lines, "elsewhere") == []
    for other, value in ATTRIBUTION.items():
        if other != name:
            assert fake_sdk.every(lines, other) == [value], other


@pytest.mark.parametrize("preset", PRESETS)
def test_every_client_follows_no_redirect(preset: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """Followed, a redirect sends the request on with every header but the authorization, and on
    a 307 or a 308 the robot's state and its camera frames with it, to wherever it pointed. Told on
    the HTTP client the SDK is built on, which every release the extras allow reads, rather than
    per request, which 1.66.0 drops without a word. The SDK's own address is no exception."""
    _provider, fake = _built(monkeypatch, preset)
    http_client = fake.kwargs["http_client"]
    assert isinstance(http_client, fake_sdk.DefaultAsyncHttpxClient)
    assert http_client.follow_redirects is False


# ── the SDK's own headers ───────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("release", fake_sdk.RELEASES)
@pytest.mark.parametrize(
    "lines",
    [
        "User-Agent: corp-allowlisted/1.0\nContent-Type: text/plain\nX-Stainless-Lang: cobol",
        "user-agent: corp-allowlisted/1.0\ncontent-type: text/plain\nACCEPT: text/html",
        "User-Agent: corp-allowlisted/1.0\nuser-agent: corp-allowlisted/1.0",
    ],
    ids=["the sdk's own spelling", "another spelling", "both spellings"],
)
def test_a_line_naming_a_header_the_sdk_sends_itself_keeps_the_sdks_own_value(
    lines: str, release: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An `Omit` under one of the SDK's own names took the SDK's own value away with the line, in
    any spelling in 3.22.1 and in the SDK's own in every release: a request went out with the HTTP
    library's `User-Agent` in place of the SDK's, and a JSON body with no `Content-Type` at all.
    Such a line now carries the SDK's own value, read off a client of the same release, so what
    goes is what the SDK sends when the variable is not there, and the line's own value goes
    nowhere. A release that keeps two spellings sends the SDK's value twice where the variable
    named it twice, and once otherwise."""
    monkeypatch.setenv("OPENAI_CUSTOM_HEADERS", lines)
    _provider, fake = _built(monkeypatch, "grok", release=release)
    sent = fake_sdk.sent(fake, release)
    twice = "\nuser-agent:" in lines and "User-Agent:" in lines and release != "3.22"
    assert fake_sdk.every(sent, "user-agent") == [fake_sdk.USER_AGENT] * (2 if twice else 1)
    assert fake_sdk.every(sent, "content-type") == [SENDS_JSON]
    assert fake_sdk.every(sent, "accept") == [SENDS_JSON]
    assert fake_sdk.every(sent, "x-stainless-lang") == ["python"]
    assert _carries(sent, "corp-allowlisted") == [] and _carries(sent, "cobol") == []


def test_the_sdks_own_values_are_asked_for_only_where_the_variable_names_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reading them means building a client to read them off, so it is done only for a variable
    that names one of the SDK's own headers. Every other build is one client, as it always was."""
    monkeypatch.setenv("OPENAI_CUSTOM_HEADERS", f"X-Gateway-Key: {GATEWAY_KEY}")
    _provider, fake = _built(monkeypatch, "grok")
    assert fake.built == 1
    monkeypatch.setenv("OPENAI_CUSTOM_HEADERS", "x-stainless-os: plan9")
    _provider, fake = _built(monkeypatch, "grok")
    assert fake.built == 2 and "default_headers" not in fake.calls[0]


# ── the address the rule is decided on ──────────────────────────────────────────────────────


@pytest.mark.parametrize("release", fake_sdk.RELEASES)
def test_through_openai_base_url_a_gateway_is_sent_what_the_sdk_sends_it(
    release: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Handed no address, the SDK follows `OPENAI_BASE_URL`, and the variables beside it were set
    for the same SDK: a gateway reached that way is what `OPENAI_CUSTOM_HEADERS` exists for. So
    `--llm openai` without `--base-url` is built there as the SDK builds it, the gateway's own
    `Authorization` going as the release puts it, its other lines and the account headers with it.
    The rule as first built sent the gateway the OpenAI key and nothing of its own, and a gateway
    that authenticates by its lines refused the run."""
    monkeypatch.setenv("OPENAI_BASE_URL", GATEWAY)
    _gateway(monkeypatch)
    provider, fake = _built(monkeypatch, "openai", release=release)
    assert fake.kwargs["base_url"] == GATEWAY == provider.client_address()
    assert "default_headers" not in fake.kwargs
    lines = fake_sdk.sent(fake, release)
    assert lines == _alone(fake, release)
    if release == "3.22":
        assert fake_sdk.every(lines, "authorization") == [GATEWAY_AUTH]
        assert fake_sdk.every(lines, "x-gateway-key") == [GATEWAY_KEY]
        assert fake_sdk.every(lines, "openai-organization") == [ORG]


@pytest.mark.parametrize("preset", LOCAL_NAMES)
def test_a_local_preset_that_reads_openai_base_url_is_not_the_sdks_own_address(
    preset: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A local preset reads `OPENAI_BASE_URL` too, as one rung of quackd's own ladder, for a
    server you run. That is quackd's choice and not the SDK's, the server is sent no hosted key,
    and the leak this answers was a board over plain HTTP, so none of the SDK's variables go there
    either: the server's own key and nothing ambient, as at any other rung."""
    board = "http://jetson.local:11434/v1"
    monkeypatch.setenv("OPENAI_BASE_URL", board)
    _gateway(monkeypatch)
    provider, fake = _built(monkeypatch, preset, release="3.22", base_url=None)
    assert provider.client_address() == board and not provider.takes_the_sdks_address()
    lines = fake_sdk.sent(fake, "3.22")
    assert fake_sdk.every(lines, "authorization") == [f"Bearer {KEY}"]
    for name in ("x-gateway-key", "openai-organization", "openai-project"):
        assert fake_sdk.every(lines, name) == [], name
    assert _carries(lines, OPENAI_KEY) == []


@pytest.mark.parametrize("release", fake_sdk.RELEASES)
def test_a_gateway_reached_with_base_url_is_sent_the_key_alone(
    release: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--base-url` is an address quackd was told for this run, not the one the SDK found, so it
    is held to the rule as built, whatever is behind it: the key quackd was given and nothing the
    environment holds. A gateway that wants the variable's lines is reached through
    `OPENAI_BASE_URL` instead, and one that takes its token as the authorization is given it as
    the key."""
    _gateway(monkeypatch)
    _provider, fake = _built(monkeypatch, "openai", release=release, base_url=GATEWAY)
    lines = fake_sdk.sent(fake, release)
    assert fake_sdk.every(lines, "authorization") == [f"Bearer {KEY}"]
    for name in ("x-gateway-key", "openai-organization", "openai-project"):
        assert fake_sdk.every(lines, name) == [], name


@pytest.mark.parametrize("unset", [False, True], ids=["blank", "unset"])
def test_with_no_address_variable_openais_own_preset_goes_to_openai(
    unset: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Blank is absent, as it is everywhere quackd reads a setting. The SDK took the empty string
    for an address, and the run failed on its first turn."""
    if unset:
        monkeypatch.delenv("OPENAI_BASE_URL")
    _provider, fake = _built(monkeypatch, "openai")
    assert fake.kwargs["base_url"] == DEFAULT_BASE_URL == "https://api.openai.com/v1"


@pytest.mark.parametrize("given", ["base_url", "OPENAI_BASE_URL"])
def test_an_address_with_space_around_it_is_handed_over_without(
    given: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`is_openai_host` takes the whitespace off before it decides, so the client is handed the
    address with it off as well: the string the rule read is the string the client is given."""
    padded = " https://eu.api.openai.com/v1 "
    if given == "base_url":
        _provider, fake = _built(monkeypatch, "openai", base_url=padded)
    else:
        monkeypatch.setenv("OPENAI_BASE_URL", padded)
        _provider, fake = _built(monkeypatch, "openai")
    assert fake.kwargs["base_url"] == padded.strip()


@pytest.mark.parametrize("blank", ["", " "], ids=["empty", "spaces"])
@pytest.mark.parametrize("preset", PRESETS)
def test_a_blank_base_url_is_no_base_url(
    preset: str, blank: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--base-url ""`, or one of spaces, is nothing typed, so every preset lands where it lands
    with no `--base-url` at all: a vendor at its own API, a local preset on its own ladder, and
    OpenAI's own where the SDK would go. A vendor's address used to be replaced with the blank,
    which then fell through to wherever `OPENAI_BASE_URL` pointed, a board over plain HTTP here,
    or to OpenAI's API, with this vendor's key and the whole first turn."""
    board = "http://jetson.local:11434/v1"
    monkeypatch.setenv("OPENAI_BASE_URL", board)
    absent = _built(monkeypatch, preset, base_url=None)[0].client_address()
    provider, fake = _built(monkeypatch, preset, base_url=blank)
    assert provider.client_address() == fake.kwargs["base_url"] == absent
    if preset in OPENAI_COMPATIBLE:
        assert absent == type(provider).base_url
        assert absent not in (board, DEFAULT_BASE_URL)


def test_a_provider_left_holding_a_blank_address_is_refused_not_sent_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Nothing quackd builds can hold one any more. A blank that got there anyway is refused
    rather than read as no address, which only OpenAI's own provider may have and which falls
    through to the SDK's."""
    provider, _fake = _built(monkeypatch, "grok")
    provider.base_url = "  "
    with pytest.raises(ProviderError, match="no address to send to"):
        provider.client_address()


@pytest.mark.parametrize(
    ("base_url", "at_openai"),
    [
        ("https://eu.api.openai.com/v1", True),
        ("https://api.openai.com.evil.example/v1", False),
        ("http://api.openai.com/v1", False),
        ("https://api.groq.com/openai/v1", False),
    ],
)
def test_base_url_moves_the_rule_with_the_address(
    base_url: str, at_openai: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    _gateway(monkeypatch)
    _provider, fake = _built(monkeypatch, "openai", release="3.22", base_url=base_url)
    assert fake.kwargs["base_url"] == base_url
    lines = fake_sdk.sent(fake, "3.22")
    assert fake_sdk.every(lines, "openai-organization") == ([ORG] if at_openai else [])


def test_a_preset_moved_to_a_board_is_judged_where_it_was_moved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`--host` moves a preset's model server to another machine, a Jetson over plain HTTP, and
    that is the address the headers are decided on."""
    _gateway(monkeypatch)
    _provider, fake = _built(monkeypatch, "ollama", release="3.22", host="jetson.local")
    assert fake.kwargs["base_url"] == "http://jetson.local:11434/v1"
    lines = fake_sdk.sent(fake, "3.22")
    assert fake_sdk.every(lines, "authorization") == [f"Bearer {KEY}"]
    for name in ("x-gateway-key", "openai-organization", "openai-project"):
        assert fake_sdk.every(lines, name) == [], name


# ── the key a server you run is sent ────────────────────────────────────────────────────────


@pytest.mark.parametrize("preset", LOCAL_NAMES)
def test_a_local_preset_is_sent_its_own_key_or_none_and_never_openais(
    preset: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A server you run is sent `LOCAL_API_KEY` where it is set and the word `not-needed`
    otherwise. It used to be sent `OPENAI_API_KEY` whenever that sat in the same `.env`, on a board
    over plain HTTP too, where it crossed the network in the clear."""
    monkeypatch.setenv("OPENAI_API_KEY", OPENAI_KEY)
    monkeypatch.delenv("LOCAL_API_KEY")
    fake = fake_sdk.install(monkeypatch, fake_sdk.FakeOpenAI(release="3.22"))
    url = {"base_url": LOCAL_URL} if preset == "local" else {}
    make_provider(preset, **url)
    assert fake.kwargs["api_key"] == "not-needed"
    assert fake_sdk.every(fake_sdk.sent(fake, "3.22"), "authorization") == ["Bearer not-needed"]
    monkeypatch.setenv("LOCAL_API_KEY", "local-key-not-real")
    make_provider(preset, **url)
    assert fake.kwargs["api_key"] == "local-key-not-real"


@pytest.mark.parametrize("preset", OPENAI_COMPATIBLE)
def test_a_vendor_is_sent_its_own_key_and_never_openais(
    preset: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every vendor reads its own variable, and `OPENAI_API_KEY` beside it is somebody else's."""
    monkeypatch.setenv("OPENAI_API_KEY", OPENAI_KEY)
    _provider, fake = _built(monkeypatch, preset, release="3.22")
    assert fake.kwargs["api_key"] == KEY
    assert _carries(fake_sdk.sent(fake, "3.22"), OPENAI_KEY) == []


# ── the read-back ───────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("handed", "reported"),
    [
        ("http://jetson.local:11434/v1", "https://api.openai.com/v1/"),
        ("https://api.openai.com/v1", "http://jetson.local:11434/v1/"),
    ],
    ids=["moved to openai", "moved away from openai"],
)
def test_a_client_that_would_send_somewhere_else_is_refused(
    handed: str, reported: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """What the client is built with is decided on the address it is handed, and a release that
    would send somewhere else would have had it decided for the wrong address: OpenAI's account
    headers and the gateway's lines would go to whoever that is. So the address is read back off
    the built client, and one that lands on the other side of OpenAI's own API stops the build."""
    monkeypatch.setenv("OPENAI_BASE_URL", handed)
    fake = fake_sdk.install(monkeypatch, fake_sdk.FakeOpenAI(moves_to=reported))
    monkeypatch.setenv("OPENAI_API_KEY", KEY)
    with pytest.raises(ProviderError) as e:
        make_provider("openai")
    said = str(e.value)
    assert f"openai {fake_sdk.__version__}" in said and "is not built" in said
    assert fake.built == 1


def test_an_address_read_back_on_the_same_side_of_the_rule_is_taken(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The SDK ends the address with a slash, and may write it in other ways of its own. Only the
    side of the rule it lands on decides anything."""
    fake = fake_sdk.install(
        monkeypatch, fake_sdk.FakeOpenAI(moves_to="https://API.OpenAI.com:443/v1/")
    )
    monkeypatch.setenv("OPENAI_API_KEY", KEY)
    assert make_provider("openai").client is fake


def test_a_refusal_never_quotes_a_password_in_the_address(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_BASE_URL", "https://rok:hunter2@api.openai.com/v1")
    fake_sdk.install(monkeypatch, fake_sdk.FakeOpenAI(moves_to="https://api.openai.com/v1/"))
    monkeypatch.setenv("OPENAI_API_KEY", KEY)
    with pytest.raises(ProviderError) as e:
        make_provider("openai")
    assert "hunter2" not in str(e.value) and "rok:***@api.openai.com" in str(e.value)


def test_a_refusal_never_quotes_an_address_redaction_cannot_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`http://rok:pw@[::1` has a password in it and no host redaction can find, so it would be
    quoted as it is. A refusal names it as an address quackd cannot read instead."""
    monkeypatch.setenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
    fake_sdk.install(monkeypatch, fake_sdk.FakeOpenAI(moves_to="http://rok:pw-SECRET@[::1"))
    monkeypatch.setenv("OPENAI_API_KEY", KEY)
    with pytest.raises(ProviderError) as e:
        make_provider("openai")
    assert "an address quackd cannot read" in str(e.value)
    assert "pw-SECRET" not in str(e.value)


@pytest.mark.parametrize("preset", ["grok", "openrouter", "ollama"])
def test_a_release_that_would_still_send_the_variables_authorization_is_refused_by_name(
    preset: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The backstop for a release nobody has read: once the client is built its own headers are
    read back, and an authorization quackd did not write stops the build, naming the release and
    the variable and never the value, which is the credential this exists to keep at home."""
    monkeypatch.setenv("OPENAI_CUSTOM_HEADERS", f"authorization: {GATEWAY_AUTH}")
    with pytest.raises(ProviderError) as e:
        _built(monkeypatch, preset, release=fake_sdk.BACKWARDS)
    said = str(e.value)
    assert f"openai {fake_sdk.__version__}" in said and "OPENAI_CUSTOM_HEADERS" in said
    assert "gw-auth-secret" not in said


@pytest.mark.parametrize(
    "lines",
    [
        f"X-Gateway-Key: {GATEWAY_KEY}",
        "OpenAI-Organization: org-gateway-secret",
        "x-openrouter-title: title-gateway-secret",
    ],
    ids=["a line of its own", "an account header", "a header openrouter sends"],
)
@pytest.mark.parametrize("preset", ["grok", "openrouter", "ollama"])
def test_a_release_that_would_send_any_other_line_of_the_variable_is_refused_by_name(
    preset: str, lines: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The backstop reads every name quackd answered, not the authorization alone. A release that
    put the variable's lines back over quackd's `Omit` would send a gateway's own credential, or
    an account the request should not be billed to, to a vendor or a server you run, and the read
    -back used to let it through because the authorization was quackd's. A line naming one of
    OpenRouter's own headers is answered with OpenRouter's value, and a release that put the
    line's own value back over that is refused the same way."""
    monkeypatch.setenv("OPENAI_CUSTOM_HEADERS", lines)
    with pytest.raises(ProviderError) as e:
        _built(monkeypatch, preset, release=fake_sdk.BACKWARDS)
    said = str(e.value)
    assert f"openai {fake_sdk.__version__}" in said and "OPENAI_CUSTOM_HEADERS" in said
    assert lines.partition(": ")[2] not in said


@pytest.mark.parametrize("release", fake_sdk.RELEASES)
def test_every_release_read_is_built_without_a_refusal(
    release: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The read-back refuses what a release does that the rule did not foresee, and nothing that
    a release somebody read does with the variable: its authorization lines in two spellings, a
    line of its own, an account header, and two of the SDK's own headers in two spellings."""
    monkeypatch.setenv(
        "OPENAI_CUSTOM_HEADERS",
        f"authorization: {GATEWAY_AUTH}\nAUTHORIZATION: {GATEWAY_AUTH}\nX-Gw: 1\n"
        "openai-organization: gw-org\nUser-Agent: corp\ncontent-type: text/plain",
    )
    monkeypatch.setenv("OPENAI_ORG_ID", ORG)
    for preset in ("grok", "openrouter", "ollama"):
        provider, fake = _built(monkeypatch, preset, release=release)
        assert provider.client is fake


class _Unreadable(fake_sdk.FakeOpenAI):
    """A client whose own headers cannot be read once it is built."""

    @property
    def default_headers(self) -> dict[str, Any]:
        raise RuntimeError(f"nothing to read, and not {GATEWAY_AUTH}")


def test_a_client_that_cannot_be_read_back_is_not_used(monkeypatch: pytest.MonkeyPatch) -> None:
    """A client whose headers nobody can read is a client nobody can say anything about, so it
    is refused the way one that would send another authorization is, by the release, and with
    nothing of what it raised but its type, which may quote what it was reading."""
    fake_sdk.install(monkeypatch, _Unreadable())
    monkeypatch.setenv("XAI_API_KEY", KEY)
    with pytest.raises(ProviderError) as e:
        make_provider("grok")
    said = str(e.value)
    assert f"openai {fake_sdk.__version__}" in said and "RuntimeError" in said
    assert "gw-auth-secret" not in said


def test_the_read_back_takes_quackds_own_value_and_omits_and_nothing_else() -> None:
    bearer = f"Bearer {KEY}"
    omit = fake_sdk.Omit
    assert not foreign_authorization({"Authorization": bearer}, bearer, omit)
    assert not foreign_authorization(
        {"Authorization": omit(), "authorization": bearer}, bearer, omit
    )
    assert not foreign_authorization({"X-Gateway-Key": GATEWAY_KEY}, bearer, omit)
    assert foreign_authorization({"Authorization": bearer, "AUTHORIZATION": "x"}, bearer, omit)
    assert foreign_authorization({"authorization": None}, bearer, omit), "an unreadable value"


def test_the_read_back_of_every_other_name_takes_what_was_handed_and_nothing_else() -> None:
    omit = fake_sdk.Omit
    handed = {
        "Authorization": "Bearer k",
        "X-Gw": omit(),
        "User-Agent": omit(),
        "user-agent": "ua",
        "OpenAI-Organization": omit(),
    }
    taken = {
        "Accept": "application/json",
        "User-Agent": omit(),
        "user-agent": "ua",
        "x-gw": omit(),
        "OpenAI-Organization": omit(),
        "Authorization": "Bearer someone-else's",
    }
    assert not foreign_headers(taken, handed, omit), "the authorization is not this one's to read"
    assert foreign_headers({**taken, "X-GW": "1"}, handed, omit)
    assert foreign_headers({**taken, "User-Agent": "corp"}, handed, omit)
    assert foreign_headers({**taken, "openai-organization": "org"}, handed, omit)

    class Uncomparable:
        def __eq__(self, other: object) -> bool:
            raise RuntimeError("no")

        __hash__ = None  # type: ignore[assignment]

    assert foreign_headers({"X-Gw": Uncomparable()}, {"X-Gw": "1"}, omit)


@pytest.mark.parametrize("missing", ["AsyncOpenAI", "Omit", "DefaultAsyncHttpxClient"])
def test_an_sdk_without_what_the_client_is_built_from_is_the_extras_sentence(
    missing: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Before the extras' floor, or half installed: the same sentence a missing SDK gets, rather
    than an `AttributeError` out of the CLI, which catches only a `ProviderError`."""
    fake_sdk.install(monkeypatch, fake_sdk.FakeOpenAI(), without=(missing,))
    monkeypatch.setenv("XAI_API_KEY", KEY)
    with pytest.raises(ProviderNotInstalled, match=r"quackd\[grok\]"):
        make_provider("grok")


class _Broken(importlib.abc.MetaPathFinder):
    """A half-installed SDK: importing it raises something other than `ImportError`, as a package
    whose own imports fail part of the way through is free to."""

    def find_spec(self, name: str, path: Any = None, target: Any = None) -> Any:
        if name == "openai":
            raise RuntimeError("the SDK is half installed")
        return None


def test_an_sdk_that_raises_anything_while_it_is_imported_is_the_extras_sentence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Not only `ImportError`: whatever an import raises is the same answer as a missing SDK, the
    sentence that names the extra, rather than a traceback out of the CLI."""
    monkeypatch.delitem(sys.modules, "openai", raising=False)
    monkeypatch.setattr(sys, "meta_path", [_Broken(), *sys.meta_path])
    monkeypatch.setenv("XAI_API_KEY", KEY)
    with pytest.raises(ProviderNotInstalled, match=r"quackd\[grok\]"):
        make_provider("grok")


# ── a turn answered with a redirect ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("location", "requested", "address", "shown", "suggested"),
    [
        (
            "https://llm.example/v1/chat/completions",
            "http://llm.example/v1/chat/completions",
            "http://llm.example/v1",
            "https://llm.example/v1/chat/completions",
            "https://llm.example/v1",
        ),
        # relative to the request, and the user, the query and the fragment never quoted
        (
            "/v2/models?token=abc#x",
            "http://rok:pw@box:8000/v1/models",
            "http://rok:pw@box:8000/v1",
            "http://box:8000/v2/models",
            "http://box:8000/v2",
        ),
        (
            "https://rok:pw@elsewhere.example/login",
            "https://api.x.ai/v1/chat/completions",
            "https://api.x.ai/v1",
            "https://elsewhere.example/login",
            None,
        ),
        (
            "https://evil.example/\x1b[2Jv1",
            "https://api.x.ai/v1/chat/completions",
            "https://api.x.ai/v1",
            "an address quackd will not quote",
            None,
        ),
        (
            "ftp://files.example/v1/models",
            "http://box:8000/v1/models",
            "http://box:8000/v1",
            "an address quackd will not quote",
            None,
        ),
        (
            "http://[::1",
            "http://box:8000/v1/models",
            "http://box:8000/v1",
            "an address quackd will not quote",
            None,
        ),
    ],
    ids=["http to https", "relative", "elsewhere", "an escape", "not http", "unreadable"],
)
def test_a_redirect_is_quoted_by_its_scheme_host_and_path_and_suggests_its_api(
    location: str, requested: str, address: str, shown: str, suggested: str | None
) -> None:
    assert _where_it_points(location, requested, address) == (shown, suggested)


def _redirect(status: int, location: str | None, url: str) -> Exception:
    """An error the way the SDK raises a 3xx once the client follows none: a status code, and the
    response it came with, which carries the request."""
    headers = {} if location is None else {"location": location}
    error = Exception(f"Error code: {status}")
    error.status_code = status  # type: ignore[attr-defined]
    error.response = NS(headers=headers, request=NS(url=url))  # type: ignore[attr-defined]
    return error


def test_a_redirect_says_where_it_pointed_and_how_to_go_there(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The SDK's own words for a redirect it was told not to follow are `Error code: 308`, which
    names neither a redirect nor where it pointed. The sentence says both, and the way to give
    the address it implies: `--base-url`, or `OPENAI_BASE_URL` for the provider that took its
    address from there. Any other failure keeps the SDK's words."""
    vllm, _fake = _built(monkeypatch, "vllm", base_url="http://llm.example/v1", model="m")
    error = _redirect(
        308, "https://llm.example/v1/chat/completions", "http://llm.example/v1/chat/completions"
    )
    assert str(vllm._failed(error)) == (
        "vllm: http://llm.example/v1 answered with a redirect (308) to "
        "https://llm.example/v1/chat/completions, so it was not followed: if the server is there "
        "now, pass --base-url https://llm.example/v1"
    )
    monkeypatch.setenv("OPENAI_BASE_URL", "http://gateway.example/v1")
    openai, _fake = _built(monkeypatch, "openai")
    error = _redirect(
        301, "https://gateway.example/v1/responses", "http://gateway.example/v1/responses"
    )
    assert str(openai._failed(error)).endswith(
        "if the server is there now, set OPENAI_BASE_URL=https://gateway.example/v1"
    )
    nowhere = _redirect(302, None, "http://llm.example/v1/chat/completions")
    assert str(vllm._failed(nowhere)) == (
        "vllm: http://llm.example/v1 answered with a redirect (302) that names no address, so it "
        "was not followed"
    )
    refused = _redirect(401, None, "http://llm.example/v1/chat/completions")
    assert str(vllm._failed(refused)) == "vllm: Exception: Error code: 401"

    class Unasked:
        """A response that cannot say which request it answered, and still says where to."""

        headers = {"location": "https://llm.example/v1/chat/completions"}

        @property
        def request(self) -> Any:
            raise RuntimeError("no request on this response")

    unasked = Exception("Error code: 307")
    unasked.status_code = 307  # type: ignore[attr-defined]
    unasked.response = Unasked()  # type: ignore[attr-defined]
    assert str(vllm._failed(unasked)) == (
        "vllm: http://llm.example/v1 answered with a redirect (307) to "
        "https://llm.example/v1/chat/completions, so it was not followed"
    )


# ── the rule itself ─────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("release", fake_sdk.RELEASES)
@pytest.mark.parametrize("unset", [False, True], ids=["variables set", "variables unset"])
@pytest.mark.parametrize(
    ("lines", "twice"),
    [
        ("OpenAI-Organization: gw-org\nOpenAI-Project: gw-proj", ()),
        ("openai-organization: gw-org\nOPENAI-PROJECT: gw-proj", ()),
        (
            "OpenAI-Organization: gw-org\nopenai-organization: gw-org\nOpenAI-Project: gw-proj",
            ("1.66", "3.1", "3.6"),
        ),
    ],
    ids=["the sdk's own spelling", "another spelling", "both spellings"],
)
def test_at_openai_the_rule_gives_the_account_headers_what_the_two_variables_say(
    lines: str,
    twice: tuple[str, ...],
    unset: bool,
    release: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The rule for a client that sends nothing ambient anywhere, which the pilot is built with
    away from the SDK's own address and another client may be built with everywhere. At OpenAI a
    gateway's line in the SDK's own spelling takes the place of the entry the SDK fills from
    `OPENAI_ORG_ID`, and a request was billed to whatever the line said, so each account header
    carries its variable's value whatever spelling the gateway gave its lines, and nothing where
    the variables are unset, as the SDK sends nothing then.

    Named in two spellings, the value goes once where a release merges them, and twice where it
    keeps both, the variable's two lines in 3.1.0 and 3.6.0 and in 1.66.0, which never reads the
    variable, the SDK's own entry and the one quackd handed under the other spelling."""
    monkeypatch.setenv("OPENAI_CUSTOM_HEADERS", lines)
    if unset:
        monkeypatch.delenv("OPENAI_ORG_ID")
        monkeypatch.delenv("OPENAI_PROJECT_ID")
    else:
        monkeypatch.setenv("OPENAI_ORG_ID", ORG)
        monkeypatch.setenv("OPENAI_PROJECT_ID", PROJECT)
    handed = request_headers(f"Bearer {KEY}", at_openai=True, omit=fake_sdk.Omit)
    fake = fake_sdk.FakeOpenAI(release=release)(api_key=KEY, default_headers=handed)
    sent = fake_sdk.sent(fake, release)
    count = 2 if release in twice else 1
    assert fake_sdk.every(sent, "openai-organization") == ([] if unset else [ORG] * count)
    assert fake_sdk.every(sent, "openai-project") == ([] if unset else [PROJECT])


def test_away_from_openai_every_line_is_omitted() -> None:
    omit = fake_sdk.Omit
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("OPENAI_CUSTOM_HEADERS", "X-Gw: 1\nOpenAI-Organization: 2\nauthorization: 3")
        headers = request_headers("Bearer k", at_openai=False, omit=omit)
    assert headers["authorization"] == "Bearer k" and isinstance(headers["Authorization"], omit)
    for name in ("X-Gw", "OpenAI-Organization", "OpenAI-Project"):
        assert isinstance(headers[name], omit), name


def test_at_openai_the_rule_omits_every_other_line_and_leaves_the_sdks_account_entry() -> None:
    """The rule a client that sends nothing ambient anywhere is built with: at OpenAI too, a line
    of the variable that is neither an authorization nor an account header is omitted. The pilot
    is built at OpenAI as the SDK builds it instead (`sdks_own_address`)."""
    omit = fake_sdk.Omit
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("OPENAI_CUSTOM_HEADERS", "X-Gw: 1\nAuthorization: 3")
        patch.setenv("OPENAI_ORG_ID", ORG)
        headers = request_headers("Bearer k", at_openai=True, omit=omit)
    assert isinstance(headers["X-Gw"], omit)
    assert headers["Authorization"] == "Bearer k"
    assert "OpenAI-Organization" not in headers, "the SDK's own entry was touched"


def test_a_line_naming_one_of_the_sdks_own_headers_is_handed_the_sdks_value() -> None:
    omit = fake_sdk.Omit
    own = {"User-Agent": "AsyncOpenAI/Python 9", "Accept": SENDS_JSON}
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("OPENAI_CUSTOM_HEADERS", "user-agent: corp\nAccept: text/html\nX-Gw: 1")
        headers = request_headers("Bearer k", at_openai=False, omit=omit, own=own)
    assert isinstance(headers["User-Agent"], omit), "the SDK's own spelling would go beside"
    assert headers["user-agent"] == "AsyncOpenAI/Python 9"
    assert headers["Accept"] == SENDS_JSON
    assert isinstance(headers["X-Gw"], omit)


def test_a_line_naming_one_of_a_vendors_own_headers_is_handed_the_vendors_value() -> None:
    """Under every spelling the variable gives it, so whichever slot a release applies last
    carries the vendor's value. Where the variable gives the name only in other spellings, the
    vendor's own is not handed at all: beside them, a release that keeps spellings apart would
    send it once more, and an `Omit` under it would come after the variable's slots and take the
    value away."""
    omit = fake_sdk.Omit
    referer, title = ATTRIBUTION["HTTP-Referer"], ATTRIBUTION["X-OpenRouter-Title"]
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv(
            "OPENAI_CUSTOM_HEADERS",
            "X-OpenRouter-Title: a\nx-openrouter-title: b\nhttp-referer: c\nX-Gw: 1",
        )
        headers = request_headers("Bearer k", at_openai=False, omit=omit, vendor=ATTRIBUTION)
    assert headers["X-OpenRouter-Title"] == headers["x-openrouter-title"] == title
    assert headers["http-referer"] == referer and "HTTP-Referer" not in headers
    assert isinstance(headers["X-Gw"], omit)
    with pytest.MonkeyPatch.context() as patch:
        patch.delenv("OPENAI_CUSTOM_HEADERS", raising=False)
        headers = request_headers("Bearer k", at_openai=False, omit=omit, vendor=ATTRIBUTION)
    assert headers["HTTP-Referer"] == referer and headers["X-OpenRouter-Title"] == title


def test_with_the_variable_unset_only_the_key_is_named() -> None:
    with pytest.MonkeyPatch.context() as patch:
        patch.delenv("OPENAI_CUSTOM_HEADERS", raising=False)
        at_openai = request_headers("Bearer k", at_openai=True, omit=fake_sdk.Omit)
        away = request_headers("Bearer k", at_openai=False, omit=fake_sdk.Omit)
    assert at_openai == {"Authorization": "Bearer k"}
    assert away["Authorization"] == "Bearer k" and set(away) == {
        "Authorization",
        "OpenAI-Organization",
        "OpenAI-Project",
    }


def test_the_variable_is_read_exactly_as_the_sdk_reads_it() -> None:
    """Split on newlines, the name before the first colon, trimmed, its case kept, and a line with
    no colon not a header, so each name answered is the very entry the SDK would add."""
    raw = "A: 1\nB:2\nno colon here\n: nameless\n  C-d : 3:4 \r"
    assert ambient_headers(raw) == ["A", "B", "", "C-d"]
    assert ambient_headers(None) == [] and ambient_headers("") == []


@pytest.mark.parametrize(
    ("url", "from_its_variable", "sdks"),
    [
        ("https://api.openai.com/v1", False, True),
        ("https://eu.api.openai.com/v1", False, True),
        ("https://gateway.example/v1", True, True),
        ("http://jetson.local:11434/v1", True, True),
        ("https://gateway.example/v1", False, False),
        ("http://jetson.local:11434/v1", False, False),
        ("http://api.openai.com/v1", False, False),
    ],
)
def test_the_sdks_own_address_is_openais_api_or_the_one_its_variable_gave(
    url: str, from_its_variable: bool, sdks: bool
) -> None:
    assert sdks_own_address(url, from_its_variable=from_its_variable) is sdks


@pytest.mark.parametrize(
    ("url", "openais"),
    [
        ("https://api.openai.com/v1", True),
        ("https://api.openai.com", True),
        # every regional host OpenAI's data-controls page lists (2026-10-10)
        *[(f"https://{host}/v1", True) for host in sorted(OPENAI_HOSTS)],
        ("https://api.openai.com:443/v1", True),
        # a host is case-insensitive
        ("https://API.OpenAI.com/v1", True),
        # whitespace around the address comes off first. `urlsplit` takes off the leading half
        # by itself since Python 3.11.4, and never the trailing half, which is in the authority
        # wherever there is no path, so the last row is the one only the rule's own strip passes
        (" https://api.openai.com/v1 ", True),
        ("https://api.openai.com ", True),
        ("http://127.0.0.1:11434/v1", False),
        ("http://api.openai.com/v1", False),
        ("https://api.openai.com:8443/v1", False),
        # the same port to `urlsplit`, and not an authority OpenAI publish
        ("https://api.openai.com:0443/v1", False),
        # a region OpenAI never named, however much it looks like the ones they did
        ("https://fr.api.openai.com/v1", False),
        # the trailing dot names the same machine in DNS, and is still not a host named here
        ("https://api.openai.com./v1", False),
        ("https://api.openai.com.evil.example/v1", False),
        ("https://user:pw@api.openai.com/v1", False),
        # read by `urlsplit` as api.openai.com with a user, and by a WHATWG client as evil.example
        ("https://evil.example\\@api.openai.com/v1", False),
        ("https://openrouter.ai/api/v1", False),
        ("not an address", False),
        ("http://[::1", False),
    ],
)
def test_openais_own_api_is_its_own_hosts_over_https_and_nothing_else(
    url: str, openais: bool
) -> None:
    assert is_openai_host(url) is openais


def test_the_hosts_are_the_ones_openai_document_and_no_others() -> None:
    """The global API and every regional host OpenAI's data-controls page lists, read 2026-10-10.
    A change here is a change to where OpenAI's account headers go, so it is spelled out rather
    than derived: a region OpenAI add is a host somebody has read about, not one that fits."""
    regions = ("us", "eu", "au", "ca", "jp", "in", "sg", "kr", "gb", "ae")
    assert {"api.openai.com", *(f"{region}.api.openai.com" for region in regions)} == OPENAI_HOSTS
    with_the_port_https_uses = {*OPENAI_HOSTS, *(f"{host}:443" for host in OPENAI_HOSTS)}
    assert with_the_port_https_uses == openai_headers.OPENAI_AUTHORITIES


def test_the_sdks_own_headers_are_read_off_the_client_and_what_cannot_be_read_is_left_out() -> None:
    client = fake_sdk.FakeOpenAI(user_agent="AsyncOpenAI/Python 9")
    assert openai_headers.own_headers(client) == {
        "Accept": SENDS_JSON,
        "Content-Type": SENDS_JSON,
        "User-Agent": "AsyncOpenAI/Python 9",
        **fake_sdk.PLATFORM,
    }
    assert openai_headers.own_headers(object()) == {
        "Accept": SENDS_JSON,
        "Content-Type": SENDS_JSON,
    }


# ── nothing goes round it ───────────────────────────────────────────────────────────────────

CLIENT_CLASSES = frozenset({"OpenAI", "AsyncOpenAI", "AzureOpenAI", "AsyncAzureOpenAI"})
"""The SDK's clients, by the names it exports them under, read in 3.22.1."""


def _reaches_the_sdk(tree: ast.AST) -> list[str]:
    """Every place a module reaches OpenAI's SDK: importing it under its own name, importing it by
    `importlib` with its name written out, or naming one of its client classes, as code or as the
    string a `getattr` would take."""
    found: list[str] = []
    for node in ast.walk(tree):
        line = getattr(node, "lineno", 0)
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            names = [node.module or ""]
        elif isinstance(node, ast.Call) and node.args:
            func = node.func
            called = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            first = node.args[0]
            names = []
            if called == "import_module" and isinstance(first, ast.Constant):
                names = [str(first.value)]
        elif isinstance(node, ast.Name | ast.Attribute):
            named = node.id if isinstance(node, ast.Name) else node.attr
            if named in CLIENT_CLASSES:
                found.append(f"{line}: {named}")
            continue
        elif isinstance(node, ast.Constant) and node.value in CLIENT_CLASSES:
            found.append(f"{line}: {node.value!r}")
            continue
        else:
            continue
        found += [f"{line}: {name}" for name in names if name.split(".")[0] == "openai"]
    return found


def test_no_client_is_built_on_openais_sdk_anywhere_but_through_the_rule() -> None:
    """SECURITY.md calls a client built on OpenAI's SDK that does not go through `openai_headers`
    a security issue, because whatever builds one by itself sends whatever the environment put
    there to wherever it points. So every module quackd ships is read for a way to reach the SDK,
    and `openai_headers.sdk_parts` is the one place that may: every client is built from what it
    hands back, and by its rule."""
    roots = [REPO / "quackd", REPO / "bridge", *sorted((REPO / "adapters").glob("*/src"))]
    reached = {
        path.relative_to(REPO).as_posix(): where
        for root in roots
        for path in sorted(root.rglob("*.py"))
        if (where := _reaches_the_sdk(ast.parse(path.read_text(encoding="utf-8"))))
    }
    assert set(reached) == {"quackd/agent/providers/openai_headers.py"}, reached


def test_the_guard_finds_each_way_of_reaching_the_sdk() -> None:
    """The guard above passes as readily when it reads nothing, so each way it looks for is shown
    to be found."""
    for source in (
        "import openai",
        "import openai.lib.azure",
        "from openai import AsyncOpenAI",
        "from openai._base_client import BaseClient",
        "importlib.import_module('openai')",
        "client = sdk.AsyncOpenAI(api_key=k)",
        "client = getattr(sdk, 'OpenAI')",
    ):
        assert _reaches_the_sdk(ast.parse(source)), source
    for source in ("from quackd.agent.providers.openai import OpenAIProvider", "x = 'openai'"):
        assert not _reaches_the_sdk(ast.parse(source)), source
