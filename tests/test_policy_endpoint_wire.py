"""A policy server on a Hugging Face Inference Endpoint, over the wire: the client's bearer, the
proxy in front, and what each token reaches.

The server here is the real one serving a scripted policy, as in
`tests/test_policy_contract.py`, behind `tests/fake_hugging_face.py`'s stand-in for the proxy,
on 127.0.0.1. The client's rule for where a Hugging Face token may go (`is_endpoint_host`) is
tested on hostile addresses as it is, and replaced by one that also takes the stand-in's address
wherever a test needs the stand-in reached. Nothing here reaches Hugging Face, and the suite's
guard fails any test that tries (`tests/conftest.py`).
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from typing import Any

import pytest

from quackd_lerobot.policy import client as C
from quackd_lerobot.policy import protocol as wire
from quackd_lerobot.policy import server as S
from quackd_lerobot.policy.client import PolicyServerError, RemoteRunner, is_endpoint_host
from quackd_lerobot.verbs import JOINTS
from tests.fake_hugging_face import FakeProxy, Recorded
from tests.test_policy_contract import TOKEN, Serving, _serving

HF_TOKEN = "hf_" + "Sx" * 17
"""Shaped like a Hugging Face token, and nobody's."""
ENDPOINT = "hf:arm"
GOOD = "https://f3b5c6ee5c9dcc0e0a01.endpoints.huggingface.cloud"
"""An endpoint's address, shaped like the one Hugging Face's API documents."""


@pytest.fixture
def served() -> Iterator[Serving]:
    serving = _serving(S.ServeOptions(policy="scripted:sweep"))
    try:
        yield serving
    finally:
        serving.http.shutdown()
        serving.http.server_close()
        serving.app.close()


def _as_endpoint(monkeypatch: pytest.MonkeyPatch, proxy: FakeProxy) -> None:
    """Tell the client's rule that the stand-in's address is an endpoint's, so the bearer goes to
    it as it would to Hugging Face's proxy. Compared on the whole address, so nothing else on
    127.0.0.1 is taken for one."""
    real = C.is_endpoint_host
    monkeypatch.setattr(
        C, "is_endpoint_host", lambda url: url.strip().rstrip("/") == proxy.url or real(url)
    )


def _runner(url: str, **kw: Any) -> RemoteRunner:
    return RemoteRunner(
        url,
        token=kw.pop("token", TOKEN),
        motors=JOINTS,
        bearer=kw.pop("bearer", HF_TOKEN),
        endpoint=kw.pop("endpoint", ENDPOINT),
        **kw,
    )


# ── where the Hugging Face token may go ─────────────────────────────────────────────────


@pytest.mark.parametrize(
    "url",
    [GOOD, f"{GOOD}:443", f" {GOOD}/ ", "https://a-1.b.endpoints.huggingface.cloud"],
)
def test_an_endpoints_own_https_address_takes_the_bearer(url: str) -> None:
    assert is_endpoint_host(url)
    runner = _runner(url)  # built, and nothing is sent until it is asked
    assert runner.host.endswith(".endpoints.huggingface.cloud") and runner.port == 443


@pytest.mark.parametrize(
    "url",
    [
        f"http://{GOOD.removeprefix('https://')}",  # plain http shows every header on the way
        f"{GOOD}:8443",  # another port is somebody's tunnel
        f"{GOOD}.",  # a trailing dot
        f"{GOOD}.evil.example",  # a host that only starts like one
        "https://evil.example\\@f3b5.endpoints.huggingface.cloud",  # a backslash
        "https://evil.example[f3b5.endpoints.huggingface.cloud]",  # brackets
        "https://rok@f3b5.endpoints.huggingface.cloud",  # a user
        "https://endpoints.huggingface.cloud",  # the domain itself
        "https://evilendpoints.huggingface.cloud",  # a host that only ends like one
        "https://F3B5.endpoints.huggingface.cloud",  # not as Hugging Face writes one
        "https://huggingface.co",
        "https://gpu.example.com",
        "http://127.0.0.1:9875",
    ],
)
def test_the_bearer_is_refused_for_any_address_not_wholly_an_endpoints(url: str) -> None:
    """The rule is on the whole authority, as the client is handed it: an address that only
    starts or ends like an endpoint's, one with a user, a backslash or a bracket in it, a
    trailing dot, another port or plain http is somebody else's, and the client refuses a
    bearer for it before it connects, in a sentence without the token."""
    assert not is_endpoint_host(url)
    with pytest.raises(ValueError) as refused:
        _runner(url)
    assert HF_TOKEN not in str(refused.value) and TOKEN not in str(refused.value)


def test_plain_http_off_loopback_is_refused_with_a_bearer_or_without() -> None:
    for kw in ({}, {"bearer": None, "endpoint": None}):
        with pytest.raises(ValueError, match="plain http"):
            _runner("http://10.0.0.5:9875", **kw)


def test_a_bearer_comes_with_its_endpoints_name_and_a_token_a_header_can_carry() -> None:
    with pytest.raises(ValueError, match="together"):
        _runner(GOOD, endpoint=None)
    with pytest.raises(ValueError, match="together"):
        _runner(GOOD, bearer=None)
    with pytest.raises(ValueError, match="hf:NAME"):
        _runner(GOOD, endpoint="hf:Not_A_Name")
    for bad in ("hf_short", "hf_" + "x" * 20 + "\nInjected: 1", "hf_" + "x" * 20 + " y"):
        with pytest.raises(ValueError) as refused:
            _runner(GOOD, bearer=bad)
        assert "hf_" not in str(refused.value)


# ── through the proxy ───────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("forwarded", [True, False])
def test_the_bearer_goes_to_the_proxy_and_the_token_to_the_server(
    served: Serving,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    forwarded: bool,
) -> None:
    """Every request carries the bearer for the proxy and the server's own token, and is served
    end to end. Whether the bearer reaches the server is the proxy's to decide: one that passes
    it on is said once in the server's log, and one that strips it leaves nothing to say."""
    caplog.set_level(logging.WARNING, logger="quackd.policy")
    with FakeProxy(HF_TOKEN, served.url, forward_authorization=forwarded) as proxy:
        _as_endpoint(monkeypatch, proxy)
        runner = _runner(proxy.url)
        try:
            runner.reset("wave")
            chunk = runner.next_chunk(_observation(), {})
            assert chunk.actions and runner.record()["endpoint"] == ENDPOINT
        finally:
            runner.close()
        assert proxy.requests and all(
            r.headers.get("authorization") == f"Bearer {HF_TOKEN}"
            and r.headers.get(wire.TOKEN_HEADER.lower()) == TOKEN
            for r in proxy.requests
        )
    warned = [r for r in caplog.records if "Authorization" in r.getMessage()]
    assert len(warned) == (1 if forwarded else 0)
    assert served.app.steps == 1


def _observation() -> Any:
    from quackd_lerobot.policy.runner import Observation

    return Observation(0, {f"{motor}.pos": 0.0 for motor in JOINTS})


@pytest.mark.parametrize(
    ("status", "needle"),
    [
        (503, "hf:arm is not serving (HTTP 503 from Hugging Face, not from quackd's server)"),
        (502, "hf:arm is not serving (HTTP 502 from Hugging Face, not from quackd's server)"),
        (500, "is not serving (HTTP 500 from Hugging Face"),
        (504, "is not serving (HTTP 504 from Hugging Face"),
        (401, "Hugging Face refused the token hf:arm was reached with (HTTP 401"),
        (403, "Hugging Face refused the token hf:arm was reached with (HTTP 403"),
        (404, "Hugging Face has no endpoint at"),
    ],
)
def test_what_the_proxy_answers_is_said_as_the_proxys(
    served: Serving, monkeypatch: pytest.MonkeyPatch, status: int, needle: str
) -> None:
    """A reply that is not one of the server's refusals is the proxy's, and is said as what it
    is, with what to do: an endpoint starting, paused or scaled to zero is not a server that is
    busy, and a token Hugging Face refused is not the server's token."""

    def proxy_says(seen: Recorded) -> tuple[int, dict[str, str], bytes]:
        return status, {"Content-Type": "text/html"}, b"<html>an error page</html>"

    with FakeProxy(HF_TOKEN, served.url, answer=proxy_says) as proxy:
        _as_endpoint(monkeypatch, proxy)
        with pytest.raises(PolicyServerError) as refused:
            _runner(proxy.url).policy()
    said = str(refused.value)
    assert needle in said and refused.value.status == status, said
    if status in C.PROXY_UNSERVED:
        assert "quackd policy endpoint up arm wakes it" in said
    assert HF_TOKEN not in said and TOKEN not in said


def test_an_endpoint_with_no_replica_is_not_serving_and_a_wrong_bearer_is_hugging_faces(
    served: Serving, monkeypatch: pytest.MonkeyPatch
) -> None:
    with FakeProxy(HF_TOKEN, None) as proxy:
        _as_endpoint(monkeypatch, proxy)
        with pytest.raises(PolicyServerError, match="is not serving \\(HTTP 503"):
            _runner(proxy.url).policy()
        proxy.backend = served.url
        with pytest.raises(PolicyServerError, match="Hugging Face refused the token") as refused:
            _runner(proxy.url, bearer="hf_" + "W" * 34).policy()
        assert "W" * 34 not in str(refused.value)
        assert _runner(proxy.url).policy().policy == "scripted:sweep"


def test_a_refusal_from_the_server_behind_the_proxy_is_still_the_servers(
    served: Serving, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The server's own refusals cross the proxy as they are: a server that is stopping says so,
    and a token it does not take is this machine's record of it, not Hugging Face's."""
    with FakeProxy(HF_TOKEN, served.url) as proxy:
        _as_endpoint(monkeypatch, proxy)
        with pytest.raises(PolicyServerError) as refused:
            _runner(proxy.url, token="not-the-token-" + TOKEN[14:]).policy()
        assert refused.value.status == 401 and "the token this machine keeps" in str(refused.value)
        assert "Hugging Face" not in str(refused.value)
        runner = _runner(proxy.url)
        runner.policy()
        served.app.close()
        with pytest.raises(PolicyServerError) as refused:
            runner.reset("wave")
        assert refused.value.status == 503 and "stopping" in str(refused.value)
        assert "Hugging Face" not in str(refused.value)


def test_both_tokens_are_scrubbed_from_whatever_comes_back(
    served: Serving, monkeypatch: pytest.MonkeyPatch
) -> None:
    def echoes(seen: Recorded) -> tuple[int, dict[str, str], bytes]:
        reason = f"you sent {seen.headers.get('authorization')} and {TOKEN}"
        return 400, {"Content-Type": "application/json"}, wire.dumps(wire.Refusal(reason=reason))

    with FakeProxy(HF_TOKEN, served.url, answer=echoes) as proxy:
        _as_endpoint(monkeypatch, proxy)
        with pytest.raises(PolicyServerError) as refused:
            _runner(proxy.url).policy()
    said = str(refused.value)
    assert "you sent Bearer <token> and <token>" in said
    assert HF_TOKEN not in said and TOKEN not in said
