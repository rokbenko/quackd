"""What a pilot that speaks OpenAI's API sends besides the request itself: the key, the headers
OpenAI's SDK adds by itself, and the address all of it goes to.

The SDK reads `OPENAI_CUSTOM_HEADERS`, `OPENAI_ORG_ID` and `OPENAI_PROJECT_ID` out of the
environment and adds them to every request, wherever the client points. A gateway's
`Authorization` line in the first replaced the vendor's key, and it, the gateway's other lines and
OpenAI's account headers went to every vendor built on the SDK and to every model server you run.
`quackd.agent.providers.openai_headers` holds quackd's answer to each, and
`OpenAIProvider._build_client` hands it to every one of those clients.

Nothing here needs the `openai` package: CI installs no provider SDK, and every test that builds a
client builds `tests/fake_openai_sdk.py` in its place, whose model of each kind of release the
extras allow is what holds the rule to all of them. `tests/test_openai_headers_wire.py` drives
the real SDK wherever it is installed.
"""

from __future__ import annotations

import ast
from typing import Any

import pytest

from quackd.agent.providers import openai_headers
from quackd.agent.providers.base import ProviderError, ProviderNotInstalled
from quackd.agent.providers.catalogue import LOCAL_NAMES
from quackd.agent.providers.factory import KEY_ENV, OPENAI_COMPATIBLE, make_provider
from quackd.agent.providers.openai import DEFAULT_BASE_URL
from quackd.agent.providers.openai_headers import (
    OPENAI_HOSTS,
    ambient_headers,
    foreign_authorization,
    is_openai_host,
    request_headers,
)
from quackd.agent.providers.openrouter import ATTRIBUTION
from tests import fake_openai_sdk as fake_sdk
from tests.conftest import REPO

KEY = "sk-test-not-a-real-key"
"""Never a real one: nothing here leaves the machine, and a fake key is what proves it."""
GATEWAY_AUTH = "Bearer gw-auth-secret"
"""The authorization a gateway's `OPENAI_CUSTOM_HEADERS` carries, which must never replace the
vendor's key on its way out."""
GATEWAY_KEY = "gk-gateway-secret"
"""A gateway's own credential under a name of its own, which is that gateway's."""
ORG = "org-x"
PROJECT = "proj-y"

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

LOCAL_URL = "http://127.0.0.1:8000/v1"
"""Where `--llm local` is pointed, the one preset with no address of its own."""


@pytest.fixture(autouse=True)
def _no_ambient_openai(monkeypatch: pytest.MonkeyPatch) -> None:
    """A developer who pilots with OpenAI has these in a `.env`, and the CLI loads it. Empty
    rather than deleted, because an empty value survives `load_dotenv` and a deleted one does
    not; the tests that mean one to be unset delete it themselves."""
    for name in SDK_VARIABLES:
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
    """The variables as somebody who reaches OpenAI through a gateway might have them."""
    monkeypatch.setenv(
        "OPENAI_CUSTOM_HEADERS", f"{spelling}: {GATEWAY_AUTH}\nX-Gateway-Key: {GATEWAY_KEY}"
    )
    monkeypatch.setenv("OPENAI_ORG_ID", ORG)
    monkeypatch.setenv("OPENAI_PROJECT_ID", PROJECT)


# ── every preset, under every kind of release ───────────────────────────────────────────────


@pytest.mark.parametrize("release", fake_sdk.RELEASES)
@pytest.mark.parametrize("spelling", ["Authorization", "authorization", "AUTHORIZATION"])
@pytest.mark.parametrize("preset", PRESETS)
def test_a_gateways_headers_and_openais_account_never_reach_a_vendor_that_is_not_openai(
    preset: str, spelling: str, release: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One authorization line leaves, and it is the vendor's key, whichever way the release
    merges and whichever spelling the gateway used. Nothing else the SDK reads for itself goes
    anywhere but OpenAI's own API: not the gateway's other lines, and not OpenAI's account
    headers. At OpenAI's own API, the account headers go as the SDK fills them and the gateway's
    other lines as the SDK sends them, which the release that does not read the variable never
    did.

    Before, the release the lock carries sent the gateway's authorization in place of the key on
    every request, to every vendor and every server. The releases read from 3.1.0 to 3.19.0 did
    that for `Authorization` and sent any other spelling beside the key, and every release read
    sent the account headers everywhere."""
    _gateway(monkeypatch, spelling)
    provider, fake = _built(monkeypatch, preset, release=release)
    lines = fake_sdk.sent(fake, release)
    assert fake_sdk.every(lines, "authorization") == [f"Bearer {KEY}"]
    if is_openai_host(provider.client_address()):
        assert fake_sdk.every(lines, "openai-organization") == [ORG]
        assert fake_sdk.every(lines, "openai-project") == [PROJECT]
        reads_the_variable = release != "1.66"
        assert fake_sdk.every(lines, "x-gateway-key") == (
            [GATEWAY_KEY] if reads_the_variable else []
        )
    else:
        for name in ("x-gateway-key", "openai-organization", "openai-project"):
            assert fake_sdk.every(lines, name) == [], name


def test_only_openais_own_preset_at_its_own_address_is_at_openai(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The test above treats a preset as OpenAI's by the address its client is handed. So it says
    something about both halves only if both halves are there: OpenAI's own, and every other."""
    at_openai = [
        preset
        for preset in PRESETS
        if is_openai_host(_built(monkeypatch, preset)[0].client_address())
    ]
    assert at_openai == ["openai"]


@pytest.mark.parametrize("release", fake_sdk.RELEASES)
def test_a_vendors_own_headers_still_go_whatever_the_variable_says_about_them(
    release: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """OpenRouter credits an app by two headers, and they are OpenRouter's to receive. A line of
    the variable that names either, in the same spelling or another, is somebody else's, so it is
    omitted and OpenRouter's own value goes, once."""
    monkeypatch.setenv(
        "OPENAI_CUSTOM_HEADERS", "HTTP-Referer: https://elsewhere.example\nx-openrouter-title: x"
    )
    _provider, fake = _built(monkeypatch, "openrouter", release=release)
    lines = fake_sdk.sent(fake, release)
    for name, value in ATTRIBUTION.items():
        assert fake_sdk.every(lines, name) == [value], name


@pytest.mark.parametrize("preset", PRESETS)
def test_every_client_follows_no_redirect(preset: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """Followed, a redirect sends the request on with every header but the authorization, and on
    a 307 or a 308 the robot's state and its camera frames with it, to wherever it pointed. Told on
    the HTTP client the SDK is built on, which every release the extras allow reads, rather than
    per request, which 1.66.0 drops without a word."""
    _provider, fake = _built(monkeypatch, preset)
    http_client = fake.kwargs["http_client"]
    assert isinstance(http_client, fake_sdk.DefaultAsyncHttpxClient)
    assert http_client.follow_redirects is False


# ── the address the rule is decided on ──────────────────────────────────────────────────────


def test_openais_own_preset_is_handed_the_address_the_sdk_would_have_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Handed no address, the SDK follows `OPENAI_BASE_URL`, and quackd's own local presets read
    the same variable as the address of a model server on your own machine. The client used to be
    handed None and left to find it, after the headers were decided; it is handed the address now,
    and the headers are decided on it."""
    monkeypatch.setenv("OPENAI_BASE_URL", "http://jetson.local:11434/v1")
    _gateway(monkeypatch)
    provider, fake = _built(monkeypatch, "openai", release="3.22")
    assert fake.kwargs["base_url"] == "http://jetson.local:11434/v1" == provider.client_address()
    lines = fake_sdk.sent(fake, "3.22")
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
    """The headers are decided on the address the client is handed, and a release that would send
    somewhere else would have had them decided for the wrong address: away from OpenAI, OpenAI's
    account headers and the gateway's lines would go to whoever that is. So the address is read
    back off the built client, and one that lands on the other side of the rule stops the build."""
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


# ── the read-back ───────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("preset", ["openai", "openrouter", "ollama"])
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


@pytest.mark.parametrize("release", fake_sdk.RELEASES)
def test_every_release_read_is_built_without_a_refusal(
    release: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The read-back refuses what a release does that the rule did not foresee, and nothing that
    a release somebody read does with the variable's authorization lines."""
    monkeypatch.setenv(
        "OPENAI_CUSTOM_HEADERS", f"authorization: {GATEWAY_AUTH}\nAUTHORIZATION: {GATEWAY_AUTH}"
    )
    provider, fake = _built(monkeypatch, "grok", release=release)
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


# ── the rule itself ─────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("release", fake_sdk.RELEASES)
@pytest.mark.parametrize("unset", [False, True], ids=["variables set", "variables unset"])
@pytest.mark.parametrize(
    "lines",
    [
        "OpenAI-Organization: gw-org\nOpenAI-Project: gw-proj",
        "openai-organization: gw-org\nOPENAI-PROJECT: gw-proj",
        "OpenAI-Organization: gw-org\nopenai-organization: gw-org\nOpenAI-Project: gw-proj",
    ],
    ids=["the sdk's own spelling", "another spelling", "both spellings"],
)
def test_at_openai_the_account_headers_are_what_the_two_variables_say(
    lines: str, unset: bool, release: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A gateway's line in the SDK's own spelling takes the place of the entry the SDK fills from
    `OPENAI_ORG_ID`, and a request to OpenAI was billed to whatever the line said. At OpenAI the
    account headers carry the two variables' values, whatever spelling the gateway gave its lines,
    and nothing where the variables are unset, as the SDK sends nothing then."""
    monkeypatch.setenv("OPENAI_CUSTOM_HEADERS", lines)
    if unset:
        monkeypatch.delenv("OPENAI_ORG_ID")
        monkeypatch.delenv("OPENAI_PROJECT_ID")
    else:
        monkeypatch.setenv("OPENAI_ORG_ID", ORG)
        monkeypatch.setenv("OPENAI_PROJECT_ID", PROJECT)
    _provider, fake = _built(monkeypatch, "openai", release=release)
    sent = fake_sdk.sent(fake, release)
    for name, value in (("openai-organization", ORG), ("openai-project", PROJECT)):
        assert set(fake_sdk.every(sent, name)) == (set() if unset else {value}), name


@pytest.mark.parametrize("keep_at_openai", [False, True])
def test_away_from_openai_every_line_is_omitted_whatever_keep_says(keep_at_openai: bool) -> None:
    omit = fake_sdk.Omit
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("OPENAI_CUSTOM_HEADERS", "X-Gw: 1\nOpenAI-Organization: 2\nauthorization: 3")
        headers = request_headers(
            "Bearer k", at_openai=False, omit=omit, keep_at_openai=keep_at_openai
        )
    assert headers["authorization"] == "Bearer k" and isinstance(headers["Authorization"], omit)
    for name in ("X-Gw", "OpenAI-Organization", "OpenAI-Project"):
        assert isinstance(headers[name], omit), name


def test_at_openai_other_lines_are_omitted_unless_kept() -> None:
    """Omitted by default, which is the rule for a client that sends nothing ambient anywhere, and
    left to the SDK where the caller keeps them, which is the pilot's: at OpenAI's own API the
    variable's other lines are what the SDK has always sent there."""
    omit = fake_sdk.Omit
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("OPENAI_CUSTOM_HEADERS", "X-Gw: 1\nAuthorization: 3")
        patch.setenv("OPENAI_ORG_ID", ORG)
        omitted = request_headers("Bearer k", at_openai=True, omit=omit)
        kept = request_headers("Bearer k", at_openai=True, omit=omit, keep_at_openai=True)
    assert isinstance(omitted["X-Gw"], omit)
    assert "X-Gw" not in kept
    for headers in (omitted, kept):
        assert headers["Authorization"] == "Bearer k"
        assert "OpenAI-Organization" not in headers, "the SDK's own entry was touched"


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
    ("url", "openais"),
    [
        ("https://api.openai.com/v1", True),
        ("https://api.openai.com", True),
        # every regional host OpenAI's data-controls page lists (2026-10-10)
        *[(f"https://{host}/v1", True) for host in sorted(OPENAI_HOSTS)],
        ("https://api.openai.com:443/v1", True),
        # a host is case-insensitive, and whitespace around the address comes off first
        ("https://API.OpenAI.com/v1", True),
        (" https://api.openai.com/v1 ", True),
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
    hands back, and through `request_headers`."""
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
