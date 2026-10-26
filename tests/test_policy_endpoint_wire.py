"""A policy server on a Hugging Face Inference Endpoint, over the wire: the client's bearer, the
proxy in front, what each token reaches, and `quackd policy endpoint` end to end.

The server here is the real one serving a scripted policy, as in
`tests/test_policy_contract.py`, behind `tests/fake_hugging_face.py`'s stand-in for the proxy,
on 127.0.0.1, and the commands run against its stand-ins for the API, the Hub and PyPI, with a
stand-in container that serves `scripted:sweep` in place of the checkpoint. The client's rule
for where a Hugging Face token may go (`is_endpoint_host`) is tested on hostile addresses as it
is, and replaced by one that also takes a stand-in's address wherever a test needs the stand-in
reached. Nothing here reaches Hugging Face, and the suite's guard fails any test that tries
(`tests/conftest.py`).
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner, Result

from quackd.cli import app
from quackd_lerobot.policy import client as C
from quackd_lerobot.policy import endpoint as E
from quackd_lerobot.policy import protocol as wire
from quackd_lerobot.policy import server as S
from quackd_lerobot.policy.client import PolicyServerError, RemoteRunner, is_endpoint_host
from quackd_lerobot.verbs import JOINTS
from tests.fake_hugging_face import ACCOUNT_TOKEN, FakeAPI, FakeProxy, Recorded, point
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


# ── quackd policy endpoint, end to end against the stand-ins ────────────────────────────

ACT = "natsuki0000/act-so101-bluecap@82f75fe40a311026b4f7cacdea7bf14cadc44ccd"
ACT_REPO = "natsuki0000/act-so101-bluecap"
UP = ["policy", "endpoint", "up", "arm", "--policy", ACT, "--fps", "30", "--latency-s", "0.25"]


@pytest.fixture
def hf(monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeAPI]:
    """The stand-ins, with quackd pointed at them and the stand-in account's token in
    `HF_TOKEN`, and a registry of the test's own, which the suite already gives every test."""
    with FakeAPI() as api:
        point(monkeypatch, api)
        monkeypatch.setenv("HF_TOKEN", ACCOUNT_TOKEN)
        yield api


def _cli(*args: str, given: str | None = None) -> Result:
    return CliRunner().invoke(app, list(args), input=given, env={"COLUMNS": "200"})


def _flat(text: str) -> str:
    return " ".join(text.split())


def _posts(hf: FakeAPI) -> list[Recorded]:
    return [r for r in hf.requests if r.method == "POST" and r.path.startswith("/v2/endpoint/")]


def _store() -> E.EndpointStore:
    return E.EndpointStore()


def test_up_creates_an_endpoint_waits_until_it_serves_and_says_how_a_run_reaches_it(
    hf: FakeAPI,
) -> None:
    """Every row as it happens: what was created and what it bills, each state with its time,
    the address, where the token is kept and what the server says it serves. One endpoint was
    created, `authenticated`, with quackd's token as its one secret, and the account's token
    went nowhere but to the API, the Hub and the endpoint's proxy, never into the body, the
    output, the listing or PyPI."""
    result = _cli(*UP, "--yes")
    assert result.exit_code == 0, result.output
    said = _flat(result.output)
    for needle in (
        "created arm on rok's account, nvidia-l4 x1 in aws us-east-1, $0.80 an hour while it runs",
        "state pending at 0 s",
        "state initializing at",
        "state running at",
        "url http://127.0.0.1:",
        "token kept in",
        "endpoints.json, for hf:arm",
        "serving scripted:sweep (quackd-policy 1, quackd ",
        "gpu yes",
        "Run with --policy-url hf:arm. quackd policy endpoint pause arm stops the bill.",
    ):
        assert needle in said, needle
    record = _store().get("arm")
    assert record is not None and record.url in hf.proxies and record.policy == ACT
    assert record.flags == ("--fps", "30.0", "--latency-s", "0.25")
    (created,) = _posts(hf)
    body = created.json()
    assert body["type"] == "authenticated" and body["compute"]["scaling"]["maxReplica"] == 1
    assert body["model"]["secrets"] == {wire.TOKEN_ENV: record.token}
    assert ACCOUNT_TOKEN not in created.body.decode("utf-8")
    assert ACCOUNT_TOKEN not in result.output and record.token not in result.output
    for seen in hf.requests:
        public = seen.path == "/v2/provider" or seen.path.startswith("/pypi/")
        assert ("authorization" in seen.headers) != public, seen.path


def test_the_record_is_kept_before_the_endpoint_is_created_and_dropped_if_it_is_refused(
    hf: FakeAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An endpoint that exists is never one this machine holds no token for: the record is kept
    first, and forgotten again when Hugging Face refuses the create."""
    real = E.HFEndpoints.create

    def create(self: E.HFEndpoints, namespace: str, payload: dict[str, Any]) -> dict[str, Any]:
        kept = _store().get(str(payload["name"]))
        assert kept is not None and payload["model"]["secrets"][wire.TOKEN_ENV] == kept.token
        return real(self, namespace, payload)

    monkeypatch.setattr(E.HFEndpoints, "create", create)
    hf.refuse_create = 402
    refused = _cli(*UP, "--yes")
    said = _flat(refused.output)
    assert refused.exit_code == 1 and "without a way to pay (HTTP 402" in said, refused.output
    assert _store().get("arm") is None
    hf.refuse_create = None
    assert _cli(*UP, "--yes").exit_code == 0 and _store().get("arm") is not None


@pytest.mark.parametrize("made", ["made", "not made", "unreadable"])
def test_a_create_whose_answer_is_lost_keeps_the_record_while_the_endpoint_may_exist(
    hf: FakeAPI, monkeypatch: pytest.MonkeyPatch, made: str
) -> None:
    """A 504, or no answer at all, may come after the endpoint was made, and forgetting the
    record then would leave one billing that this machine has no token for. So the endpoint is
    read: made, the record is kept and up goes on; not made, the record is forgotten; and when
    that read fails too, the record is kept and up says how to find out."""
    real = FakeAPI._create
    after = {"create": False}

    def lost(self: FakeAPI, namespace: str, body: Any) -> Any:
        if made != "not made":
            real(self, namespace, body)
        after["create"] = True
        return 504, {"Content-Type": "text/plain"}, b"Gateway Timeout"

    read = FakeAPI._endpoint

    def unreadable(self: FakeAPI, method: str, path: list[str], seen: Any, query: Any) -> Any:
        if made == "unreadable" and after["create"] and method == "GET" and len(path) == 2:
            return 502, {"Content-Type": "text/plain"}, b"Bad Gateway"
        return read(self, method, path, seen, query)

    monkeypatch.setattr(FakeAPI, "_create", lost)
    monkeypatch.setattr(FakeAPI, "_endpoint", unreadable)
    result = _cli(*UP, "--yes")
    said = _flat(result.output)
    kept = _store().get("arm")
    if made == "made":
        assert result.exit_code == 0 and "gpu yes" in said, said
        assert (
            "Hugging Face's answer was lost (Hugging Face would not create arm (HTTP 504)" in said
        )
        assert kept is not None and kept.url in hf.proxies
        secrets = hf.endpoints[("rok", "arm")].payload["model"]["secrets"]
        assert secrets[wire.TOKEN_ENV] == kept.token
    elif made == "not made":
        assert result.exit_code == 1 and "would not create arm (HTTP 504)" in said, said
        assert kept is None and ("rok", "arm") not in hf.endpoints
    else:
        assert result.exit_code == 1, said
        assert "whether arm was created could not be read either" in said
        assert "quackd policy endpoint up arm finds out" in said
        assert kept is not None and ("rok", "arm") in hf.endpoints
    assert ACCOUNT_TOKEN not in result.output


def test_a_refused_create_that_quotes_the_body_back_shows_no_token(
    hf: FakeAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Hugging Face may quote what it refused, and the body holds the server's token as a secret:
    the refusal is said with it taken out, and the record is forgotten."""

    def quotes(self: FakeAPI, namespace: str, body: Any) -> Any:
        secret = body["model"]["secrets"][wire.TOKEN_ENV]
        said = {"error": f"secrets.{wire.TOKEN_ENV} {secret} is not allowed"}
        return 422, {"Content-Type": "application/json"}, json.dumps(said).encode("utf-8")

    monkeypatch.setattr(FakeAPI, "_create", quotes)
    result = _cli(*UP, "--yes")
    said = _flat(result.output)
    assert result.exit_code == 1 and "would not create arm (HTTP 422)" in said, said
    assert f"secrets.{wire.TOKEN_ENV} <token> is not allowed" in said
    (created,) = _posts(hf)
    assert created.json()["model"]["secrets"][wire.TOKEN_ENV] not in result.output
    assert _store().get("arm") is None


def test_up_with_a_token_where_the_hub_tokens_variable_belongs_does_not_say_it(
    hf: FakeAPI,
) -> None:
    hub = "hf_" + "HubReadToken" * 3  # a name as far as the rule goes, and set nowhere
    result = _cli(*UP, "--yes", "--hub-token-from", hub)
    assert result.exit_code == 1 and "is not set" in _flat(result.output), result.output
    assert hub not in result.output and not _posts(hf)


def test_up_with_no_wait_creates_it_and_returns_saying_so(hf: FakeAPI) -> None:
    result = _cli(*UP, "--yes", "--no-wait")
    said = _flat(result.output)
    assert result.exit_code == 0 and "created arm on rok's account" in said, result.output
    assert "Not waiting: quackd policy endpoint up arm waits until it serves" in said
    assert "state " not in said
    record = _store().get("arm")
    assert record is not None and record.url is None and len(_posts(hf)) == 1
    waited = _cli("policy", "endpoint", "up", "arm")
    assert waited.exit_code == 0 and "gpu yes" in _flat(waited.output), waited.output


def test_up_with_no_terminal_and_no_yes_creates_nothing(hf: FakeAPI) -> None:
    result = _cli(*UP)
    assert result.exit_code == 1 and "no terminal to ask" in _flat(result.output), result.output
    assert not _posts(hf) and _store().get("arm") is None


def test_up_asks_at_a_terminal_and_a_no_creates_nothing(
    hf: FakeAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    from quackd import cli

    monkeypatch.setattr(cli, "_can_prompt", lambda: True)
    refused = _cli(*UP, given="n\n")
    said = _flat(refused.output)
    assert refused.exit_code == 1 and "This starts a Hugging Face Inference Endpoint" in said
    assert "It bills $0.80 an hour while it runs (Hugging Face's price, read just now)" in said
    assert not _posts(hf) and _store().get("arm") is None
    agreed = _cli(*UP, given="y\n")
    assert agreed.exit_code == 0 and len(_posts(hf)) == 1, agreed.output


@pytest.mark.parametrize(
    ("args", "setup", "needle"),
    [
        (["--policy", "scripted:sweep"], {}, "scripted:sweep needs no GPU: serve it on the laptop"),
        ([], {}, "--policy REPO@REVISION is needed the first time arm is started"),
        (["--policy", ACT, "--idle-minutes", "5"], {}, "--idle-minutes 5 is outside 15 to 720"),
        (["--policy", ACT, "--latency-s", "9"], {}, "--latency-s"),
        (["--policy", ACT, "--instance", "inf2"], {}, "inf2 is a neuron instance"),
        (["--policy", ACT], {"releases": {}}, "is not a release on PyPI"),
        (
            ["--policy", ACT],
            {"models": {ACT_REPO: {"private": False, "gated": "manual"}}},
            "is gated or private on the Hub",
        ),
        (["--policy", ACT], {"quota": 0}, "has no nvidia-l4 left in its quota"),
        (["--policy", ACT, "--hub-token-from", "QUACKD_TEST_NOWHERE"], {}, "is not set"),
    ],
)
def test_up_refuses_before_anything_is_created(
    hf: FakeAPI, args: list[str], setup: dict[str, Any], needle: str
) -> None:
    for key, value in setup.items():
        setattr(hf, key, value)
    result = _cli("policy", "endpoint", "up", "arm", *args, "--yes")
    assert result.exit_code == 1 and needle in _flat(result.output), result.output
    assert not _posts(hf) and _store().get("arm") is None
    assert ACCOUNT_TOKEN not in result.output


def test_up_given_a_quota_listing_not_the_shape_documented_says_so_and_creates_nothing(
    hf: FakeAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        FakeAPI, "_quotas", lambda self: {"vendors": [{"name": "aws", "quotas": 5}]}
    )
    result = _cli(*UP, "--yes")
    said = _flat(result.output)
    assert result.exit_code == 1 and isinstance(result.exception, SystemExit), result.exception
    assert "Hugging Face's quota listing for rok is not the shape it documents" in said, said
    assert not _posts(hf) and _store().get("arm") is None


def test_up_with_no_token_or_a_name_hugging_face_would_refuse_asks_nothing(
    hf: FakeAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    named = _cli("policy", "endpoint", "up", "Arm_1", "--policy", ACT, "--yes")
    assert named.exit_code == 1 and "is not an endpoint's name" in _flat(named.output)
    monkeypatch.setenv("HF_TOKEN", "")
    tokenless = _cli(*UP, "--yes")
    assert tokenless.exit_code == 1 and "no Hugging Face token" in _flat(tokenless.output)
    assert not hf.requests


def test_an_endpoint_whose_server_has_no_gpu_is_paused_and_its_log_quoted(hf: FakeAPI) -> None:
    hf.gpu = False
    result = _cli(*UP, "--yes")
    said = _flat(result.output)
    assert result.exit_code == 1 and "serves with no GPU" in said, result.output
    assert "so it was paused, and bills nothing now" in said
    assert "stand-in container: serving scripted:sweep in place of" in said
    assert hf.endpoints[("rok", "arm")].state == "paused"


def test_an_endpoint_that_fails_is_paused_and_its_log_quoted_with_no_token_in_it(
    hf: FakeAPI,
) -> None:
    hf.fail = True
    hf.log = [f"the image would not start, holding {ACCOUNT_TOKEN} in its environment"]
    result = _cli(*UP, "--yes")
    said = _flat(result.output)
    assert result.exit_code == 1 and "hf:arm reached failed" in said, result.output
    assert "the image would not start, holding <token> in its environment" in said
    assert ACCOUNT_TOKEN not in result.output
    assert hf.endpoints[("rok", "arm")].state == "paused"


@pytest.mark.parametrize("fault", ["the api", "the record", "the server"])
def test_up_pauses_an_endpoint_whatever_stops_its_wait(
    hf: FakeAPI, monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    """An endpoint left starting bills until it idles to zero, so a wait that stops for any
    reason pauses it: a 502 from the API on a read of it, a record that cannot be written once
    its address is known, or a refusal from the proxy when the server is asked what it serves."""
    if fault == "the api":
        real = FakeAPI._endpoint
        reads = {"n": 0}

        def flaky(self: FakeAPI, method: str, path: list[str], seen: Any, query: Any) -> Any:
            if method == "GET" and len(path) == 2:
                reads["n"] += 1  # the first is up's look for a name already taken
                if reads["n"] == 3:
                    return 502, {"Content-Type": "text/plain"}, b"Bad Gateway"
            return real(self, method, path, seen, query)

        monkeypatch.setattr(FakeAPI, "_endpoint", flaky)
    elif fault == "the record":
        save = E.EndpointStore.save

        def unwritable(self: E.EndpointStore, record: E.EndpointRecord) -> None:
            if record.url is not None:
                raise E.EndpointError(f"cannot write {self.path}: the disk is full")
            save(self, record)

        monkeypatch.setattr(E.EndpointStore, "save", unwritable)
    else:
        answer = FakeProxy._reply

        def refusing(self: FakeProxy, seen: Recorded) -> Any:
            if seen.path == wire.POLICY_PATH:
                return 502, {"Content-Type": "text/plain"}, b"Bad Gateway"
            return answer(self, seen)

        monkeypatch.setattr(FakeProxy, "_reply", refusing)
    result = _cli(*UP, "--yes")
    said = _flat(result.output)
    assert result.exit_code == 1 and "so it was paused, and bills nothing now" in said, said
    assert hf.endpoints[("rok", "arm")].state == "paused"
    assert [r for r in hf.requests if r.path.endswith("/arm/pause")]


def test_a_404_from_the_proxy_before_it_has_a_route_is_waited_out_and_not_fatal(
    hf: FakeAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A proxy may answer 404 before it has a route to a replica that is starting, and whether
    the endpoint is gone is the API's to say: the wait asks again, and up serves."""
    answer = FakeProxy._reply
    asked = {"n": 0}

    def unrouted(self: FakeProxy, seen: Recorded) -> Any:
        if seen.path == wire.HEALTH_PATH:
            asked["n"] += 1
            if asked["n"] == 1:
                return 404, {"Content-Type": "application/json"}, b'{"error":"not found yet"}'
        return answer(self, seen)

    monkeypatch.setattr(FakeProxy, "_reply", unrouted)
    result = _cli(*UP, "--yes")
    assert result.exit_code == 0 and "gpu yes" in _flat(result.output), result.output
    assert asked["n"] >= 2 and hf.endpoints[("rok", "arm")].state == "running"
    assert not [r for r in hf.requests if r.path.endswith("/pause")]


def test_an_endpoint_that_does_not_serve_in_time_is_paused(hf: FakeAPI) -> None:
    hf.stuck = True
    result = _cli(*UP, "--yes", "--wait-minutes", "0.001")
    said = _flat(result.output)
    assert result.exit_code == 1 and "did not serve within 0.001 minutes" in said, result.output
    assert hf.endpoints[("rok", "arm")].state == "paused"


def test_a_paused_endpoint_is_resumed_by_up_after_asking_again(hf: FakeAPI) -> None:
    assert _cli(*UP, "--yes").exit_code == 0
    paused = _cli("policy", "endpoint", "pause", "arm")
    assert paused.exit_code == 0 and "paused arm, which bills nothing now" in _flat(paused.output)
    assert hf.endpoints[("rok", "arm")].state == "paused"
    unasked = _cli("policy", "endpoint", "up", "arm")
    assert unasked.exit_code == 1 and "no terminal to ask" in _flat(unasked.output)
    assert hf.endpoints[("rok", "arm")].state == "paused"
    resumed = _cli("policy", "endpoint", "up", "arm", "--yes")
    said = _flat(resumed.output)
    assert resumed.exit_code == 0 and "resumed arm, $0.80 an hour while it runs" in said, said
    assert "gpu yes" in said and hf.endpoints[("rok", "arm")].state == "running"


def test_an_endpoint_at_zero_is_woken_by_up_only_after_asking(
    hf: FakeAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Waking one that scaled to zero bills again, as resuming a paused one does, so up asks
    first, with the price, and with no terminal it needs --yes. A yes wakes it with one request,
    with --no-wait too, which sends no other."""
    from quackd import cli

    assert _cli(*UP, "--yes").exit_code == 0
    ep = hf.endpoints[("rok", "arm")]

    def to_zero() -> None:
        E._request("POST", E.API, "/v2/endpoint/rok/arm/scale-to-zero", bearer=ACCOUNT_TOKEN)
        assert ep.state == "scaledToZero"

    to_zero()
    before = len(ep.proxy.requests)
    unasked = _cli("policy", "endpoint", "up", "arm")
    assert unasked.exit_code == 1 and "no terminal to ask" in _flat(unasked.output)
    monkeypatch.setattr(cli, "_can_prompt", lambda: True)
    refused = _cli("policy", "endpoint", "up", "arm", given="n\n")
    said = _flat(refused.output)
    assert refused.exit_code == 1 and "This wakes a Hugging Face Inference Endpoint" in said
    assert "It bills $0.80 an hour while it runs" in said and "Wake it?" in said
    assert ep.state == "scaledToZero" and len(ep.proxy.requests) == before
    woken = _cli("policy", "endpoint", "up", "arm", "--yes")
    said = _flat(woken.output)
    assert woken.exit_code == 0 and "waking arm, $0.80 an hour while it runs" in said, said
    assert "gpu yes" in said and ep.state == "running"
    to_zero()
    unwaited = _cli("policy", "endpoint", "up", "arm", "--yes", "--no-wait")
    assert unwaited.exit_code == 0 and "waking arm" in _flat(unwaited.output)
    assert ep.state != "scaledToZero"


def test_up_wakes_one_at_zero_at_its_address_when_the_record_had_none_yet(hf: FakeAPI) -> None:
    """A record `up --no-wait` left has no address, and its endpoint may run and idle to zero
    before anybody asks: the wake records the address Hugging Face gives it first, and wakes it
    there, with --no-wait too."""
    assert _cli(*UP, "--yes", "--no-wait").exit_code == 0
    kept = _store().get("arm")
    assert kept is not None and kept.url is None
    ep = hf.endpoints[("rok", "arm")]
    ep.state, ep.ahead = "scaledToZero", []
    woken = _cli("policy", "endpoint", "up", "arm", "--yes", "--no-wait")
    assert woken.exit_code == 0 and "waking arm" in _flat(woken.output), woken.output
    kept = _store().get("arm")
    assert kept is not None and kept.url == ep.proxy.url and ep.state != "scaledToZero"


def test_an_endpoint_whose_address_moves_while_up_waits_is_sent_nothing_and_paused(
    hf: FakeAPI,
) -> None:
    """The address is recorded once and the account's token goes to no other: one Hugging Face
    gives the endpoint later is refused, and the endpoint, which carries this record's tag and so
    is this machine's, is paused rather than left starting. Nothing is looked up at the new
    address, which the suite's guard would have written down."""
    assert _cli(*UP, "--yes").exit_code == 0
    assert _cli("policy", "endpoint", "pause", "arm").exit_code == 0
    recorded = _store().get("arm")
    assert recorded is not None
    ep = hf.endpoints[("rok", "arm")]
    ep.url = "https://0a1b2c3d4e5f.endpoints.huggingface.cloud"
    result = _cli("policy", "endpoint", "up", "arm", "--yes", "--wait-minutes", "0.05")
    said = _flat(result.output)
    assert result.exit_code == 1, said
    assert "Hugging Face gives hf:arm another address than the one this machine recorded" in said
    assert "so it was paused, and bills nothing now" in said and ep.state == "paused"
    assert _store().get("arm") == recorded


def test_up_on_a_failed_endpoint_quotes_its_log_and_message_with_no_token(
    hf: FakeAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One that failed is said with the last of its log and its message, and either may hold the
    server's token, a container's argv being what it is, or what a terminal would act on: both
    are taken out, and the message is cut."""
    assert _cli(*UP, "--yes").exit_code == 0
    record = _store().get("arm")
    assert record is not None
    ep = hf.endpoints[("rok", "arm")]
    hf._stop(ep)
    ep.state, ep.ahead = "failed", []
    ep.log = [f"Traceback: argv held {record.token}"]
    real = FakeAPI._status

    def status(self: FakeAPI, e: Any) -> dict[str, Any]:
        said = real(self, e)
        said["status"]["message"] = f"exited 1 with {record.token} \x1b]0;owned\x07 " + "x" * 600
        return said

    monkeypatch.setattr(FakeAPI, "_status", status)
    result = _cli("policy", "endpoint", "up", "arm")
    said = _flat(result.output)
    assert result.exit_code == 1 and "hf:arm is failed: exited 1 with <token>" in said, said
    assert "Traceback: argv held <token>" in said
    assert record.token not in result.output and "\x1b" not in result.output
    assert "x" * 400 not in said.replace(" ", ""), "the message was not cut"


def test_up_again_with_anything_other_than_what_it_was_started_with_is_refused(
    hf: FakeAPI,
) -> None:
    """There is no update: an endpoint serves what it was created with. The same flags again are
    no change, and are taken."""
    assert _cli(*UP, "--yes").exit_code == 0
    for args, flag in (
        (["--policy", "other/policy@abc123"], "--policy"),
        (["--fps", "15"], "--fps"),
        (["--region", "eu-west-1"], "--region"),
        (["--idle-minutes", "60"], "--idle-minutes"),
    ):
        result = _cli("policy", "endpoint", "up", "arm", *args, "--yes")
        said = _flat(result.output)
        assert result.exit_code == 1 and f"{flag} would change that" in said, said
    again = _cli(*UP, "--yes")
    assert again.exit_code == 0 and len(_posts(hf)) == 1, again.output


def test_list_says_what_this_machine_started_and_what_hugging_face_says_of_it(
    hf: FakeAPI,
) -> None:
    assert _cli(*UP, "--yes").exit_code == 0
    E.HFEndpoints(ACCOUNT_TOKEN).create(
        "rok",
        {
            "name": "elsewhere",
            "type": "authenticated",
            "provider": {},
            "compute": {},
            "model": {},
            "tags": [E.TAG],
        },
    )
    listed = _cli("policy", "endpoint", "list")
    said = _flat(listed.output)
    assert listed.exit_code == 0 and "arm" in said and "running" in said, listed.output
    assert "elsewhere" in said and "not started from this machine" in said
    rows = [
        json.loads(line)
        for line in _cli("policy", "endpoint", "list", "--json").output.splitlines()
    ]
    assert {row["name"] for row in rows} == {"arm", "elsewhere"}
    record = _store().get("arm")
    assert record is not None and all(record.token not in json.dumps(row) for row in rows)


def _made_elsewhere(hf: FakeAPI, monkeypatch: pytest.MonkeyPatch, *, tagged: bool) -> Any:
    """quackd's arm deleted on huggingface.co, and another endpoint called arm made there in its
    place, running at an address of its own: untagged, as one made on huggingface.co would be,
    or with quackd's tag and the one another machine's record gives it, as that machine's `up`
    would make it. The address the record holds, if it holds one, stays one the client's rule
    takes, as a real endpoint's would once it is gone."""
    taken = set(hf.proxies)
    monkeypatch.setattr(
        C,
        "is_endpoint_host",
        lambda url: url.strip().rstrip("/") in taken | set(hf.proxies) or is_endpoint_host(url),
    )
    E._request("DELETE", E.API, "/v2/endpoint/rok/arm", bearer=ACCOUNT_TOKEN)
    body = {
        "name": "arm",
        "type": "authenticated",
        "provider": {"vendor": "aws", "region": "us-east-1"},
        "compute": {"accelerator": "gpu", "instanceType": "nvidia-a10g", "instanceSize": "x1"},
        "model": {"repository": "someone/llm", "framework": "custom", "image": {}},
        "tags": [E.TAG, "quackd-policy-0123456789ab"] if tagged else ["production"],
    }
    E._request("POST", E.API, "/v2/endpoint/rok", bearer=ACCOUNT_TOKEN, body=body)
    other = hf.endpoints[("rok", "arm")]
    other.state, other.ahead = "running", []
    return other


@pytest.mark.parametrize("addressed", [True, False], ids=["with an address", "without one"])
@pytest.mark.parametrize("tagged", [False, True], ids=["untagged", "another machine's"])
@pytest.mark.parametrize(
    "command",
    [
        ["policy", "endpoint", "up", "arm", "--wait-minutes", "0.001"],
        ["policy", "endpoint", "pause", "arm"],
        ["policy", "endpoint", "delete", "arm", "--yes"],
    ],
    ids=["up", "pause", "delete"],
)
def test_a_record_outlived_by_its_endpoint_never_resumes_pauses_or_deletes_another(
    hf: FakeAPI,
    monkeypatch: pytest.MonkeyPatch,
    command: list[str],
    tagged: bool,
    addressed: bool,
) -> None:
    """An endpoint deleted on huggingface.co leaves this machine's record behind, and another
    made by that name is not quackd's to wake, pause or delete. Each command reads it first and
    leaves it as it is when it carries no quackd tag, or not the one this record gave its own,
    whether or not the record has an address yet, as one `up --no-wait` left has not, saying
    how to forget the record: nothing is changed on it, nothing is sent to it, and the record
    is kept."""
    started = _cli(*UP, "--yes") if addressed else _cli(*UP, "--yes", "--no-wait")
    assert started.exit_code == 0, started.output
    other = _made_elsewhere(hf, monkeypatch, tagged=tagged)
    before = len(hf.requests)
    result = _cli(*command)
    said = _flat(result.output)
    needle = (
        "carries quackd's tag but not the one this machine's up gave hf:arm"
        if tagged
        else "carries no quackd-policy tag, so quackd did not start it"
    )
    assert result.exit_code == 1 and needle in said, said
    assert "quackd policy endpoint delete arm --forget forgets the record" in said
    assert hf.endpoints.get(("rok", "arm")) is other and other.state == "running"
    changed = [r for r in hf.requests[before:] if r.method in ("POST", "DELETE")]
    assert not changed and not other.proxy.requests, changed
    assert _store().get("arm") is not None


def test_delete_forget_forgets_the_record_and_leaves_hugging_face_as_it_is(
    hf: FakeAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The way out of a record whose endpoint is not quackd's any more: it asks, or needs --yes,
    since the server's token is kept nowhere else, needs no Hugging Face token, and asks
    Hugging Face nothing."""
    assert _cli(*UP, "--yes").exit_code == 0
    other = _made_elsewhere(hf, monkeypatch, tagged=False)
    monkeypatch.setenv("HF_TOKEN", "")
    before = len(hf.requests)
    unasked = _cli("policy", "endpoint", "delete", "arm", "--forget")
    assert unasked.exit_code == 1, unasked.output
    assert "forgetting hf:arm cannot be undone" in _flat(unasked.output)
    assert _store().get("arm") is not None
    forgot = _cli("policy", "endpoint", "delete", "arm", "--forget", "--yes")
    said = _flat(forgot.output)
    assert forgot.exit_code == 0 and "forgot arm in" in said, said
    assert "deleted" not in said and "Hugging Face had no" not in said
    assert _store().get("arm") is None and hf.endpoints.get(("rok", "arm")) is other
    assert len(hf.requests) == before


def test_an_endpoint_the_wait_finds_is_not_the_recorded_one_is_not_paused(
    hf: FakeAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every other reason a wait stops pauses the endpoint, and this one does not: an endpoint by
    that name that is not the one recorded is not quackd's to pause."""

    def swapped(*args: Any, **kw: Any) -> Any:
        raise E.Unconfirmed(
            "rok's endpoint called arm is not at the address this machine recorded for it"
        )

    monkeypatch.setattr(E, "wait_until_serving", swapped)
    result = _cli(*UP, "--yes")
    said = _flat(result.output)
    assert result.exit_code == 1 and "not at the address this machine recorded" in said, said
    assert "paused" not in said and not [r for r in hf.requests if r.path.endswith("/pause")]
    assert hf.endpoints[("rok", "arm")].state != "paused"


def test_delete_asks_or_needs_yes_and_forgets_the_record(hf: FakeAPI) -> None:
    assert _cli(*UP, "--yes").exit_code == 0
    unasked = _cli("policy", "endpoint", "delete", "arm")
    assert unasked.exit_code == 1 and "cannot be undone" in _flat(unasked.output)
    assert ("rok", "arm") in hf.endpoints
    deleted = _cli("policy", "endpoint", "delete", "arm", "--yes")
    assert deleted.exit_code == 0 and "deleted arm from rok's account" in _flat(deleted.output)
    assert ("rok", "arm") not in hf.endpoints and _store().get("arm") is None
    gone = _cli("policy", "endpoint", "pause", "arm")
    assert gone.exit_code == 1 and "no endpoint called arm is recorded" in _flat(gone.output)


# ── a run's client, by --policy-url hf:NAME ─────────────────────────────────────────────


@pytest.mark.parametrize("answer", [404, 403])
def test_a_record_a_dotenv_plants_sends_the_account_token_nowhere(
    hf: FakeAPI,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    _no_request_reaches_hugging_face: list[str],
    answer: int,
) -> None:
    """The CLI loads the working directory's `.env`, which can name another registry, so a
    project cloned from somebody else can plant a record of hf:arm naming an endpoint of theirs.
    A run reads the endpoint from the API with the account's token before the bearer goes
    anywhere, and Hugging Face has no such endpoint of the account's, or refuses the token for
    an account it is not: the run is refused, and the planted address is never even looked up,
    which the suite's guard would have written down."""
    planted = tmp_path / "planted"
    attacker = replace(
        E.new_record(
            "arm",
            namespace="attacker",
            policy=ACT,
            instance="nvidia-l4",
            vendor="aws",
            region="us-east-1",
            idle_minutes=15,
        ),
        url="https://a7tacker00.endpoints.huggingface.cloud",
    )
    E.EndpointStore(planted).save(attacker)
    project = tmp_path / "cloned-project"
    project.mkdir()
    (project / ".env").write_text(f"QUACKD_REGISTRY_DIR={planted}\n", encoding="utf-8")
    monkeypatch.delenv("QUACKD_REGISTRY_DIR", raising=False)
    monkeypatch.chdir(project)
    if answer == 403:
        real = FakeAPI._endpoint

        def foreign(self: FakeAPI, method: str, path: list[str], seen: Any, query: Any) -> Any:
            if path[:1] == ["attacker"]:
                body = b'{"error":"You do not have access to this namespace"}'
                return 403, {"Content-Type": "application/json"}, body
            return real(self, method, path, seen, query)

        monkeypatch.setattr(FakeAPI, "_endpoint", foreign)
    result = _cli("policy", "check", "--policy-url", "hf:arm")
    said = _flat(result.output)
    needle = {
        404: "Hugging Face has no endpoint called arm on attacker's account, so nothing was sent",
        403: "Hugging Face refused the token when asked to say how arm is (HTTP 403",
    }[answer]
    assert result.exit_code == 1 and needle in said, said
    (read,) = hf.sent("/v2/endpoint/attacker/arm")
    assert read.headers["authorization"] == f"Bearer {ACCOUNT_TOKEN}"
    assert not _no_request_reaches_hugging_face, "the planted address was looked up"
    assert ACCOUNT_TOKEN not in result.output and attacker.token not in result.output


@pytest.mark.parametrize("forwarded", [True, False])
def test_check_benches_an_endpoint_by_its_name_through_the_proxy(
    hf: FakeAPI, forwarded: bool
) -> None:
    """`up`, then `quackd policy check --policy-url hf:arm --bench`, end to end through the
    stand-in proxy to the server in the stand-in container. Every request to the proxy carries
    the account's token as the bearer and the record's token for the server, and nothing reaches
    the server with the account's token unless the proxy passes it on, which the server's log
    then says once."""
    hf.forward_authorization = forwarded
    assert _cli(*UP, "--yes").exit_code == 0
    record = _store().get("arm")
    assert record is not None
    ep = hf.endpoints[("rok", "arm")]
    ep.proxy.requests.clear()
    result = _cli("policy", "check", "--policy-url", "hf:arm", "--bench", "--seconds", "1")
    said = _flat(result.output)
    assert result.exit_code == 0, result.output
    assert f"hf:arm at {record.url}" in said and "policy scripted:sweep" in said
    assert "achieved" in said and "round trip" in said
    assert ep.proxy.requests and all(
        r.headers.get("authorization") == f"Bearer {ACCOUNT_TOKEN}"
        and r.headers.get(wire.TOKEN_HEADER.lower()) == record.token
        for r in ep.proxy.requests
    )
    # said once in the server's own log, the first time up's wait reached it, and never after
    told = [line for line in ep.log if "Authorization header" in line]
    assert len(told) == (1 if forwarded else 0), ep.log
    assert ACCOUNT_TOKEN not in result.output and record.token not in result.output


def test_a_paused_endpoint_is_not_serving_and_says_how_to_wake_it(hf: FakeAPI) -> None:
    assert _cli(*UP, "--yes").exit_code == 0
    assert _cli("policy", "endpoint", "pause", "arm").exit_code == 0
    result = _cli("policy", "check", "--policy-url", "hf:arm")
    said = _flat(result.output)
    assert result.exit_code == 1, result.output
    assert "hf:arm is not serving (HTTP 503 from Hugging Face, not from quackd's server)" in said
    assert "quackd policy endpoint up arm wakes it and waits until it answers" in said


def test_check_of_an_endpoint_with_no_record_or_no_account_token_refuses_before_it_asks(
    hf: FakeAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    unknown = _cli("policy", "check", "--policy-url", "hf:arm")
    assert unknown.exit_code == 1 and "no endpoint called arm is recorded" in _flat(unknown.output)
    assert _cli(*UP, "--yes").exit_code == 0
    monkeypatch.setenv("HF_TOKEN", "")
    before = len(hf.endpoints[("rok", "arm")].proxy.requests)
    tokenless = _cli("policy", "check", "--policy-url", "hf:arm")
    said = _flat(tokenless.output)
    assert tokenless.exit_code == 1 and "no Hugging Face token to reach hf:arm" in said
    assert len(hf.endpoints[("rok", "arm")].proxy.requests) == before


def test_a_run_reaches_an_endpoint_by_its_name_and_the_record_names_both(
    hf: FakeAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The arm a run builds asks the endpoint what it serves, as `quackd run` asks before the arm
    connects, and what the record will say names the server by its address and the endpoint by
    its name, and holds neither token."""
    from quackd.adapters.base import PolicyChoice
    from quackd.adapters.factory import make_adapter

    assert _cli(*UP, "--yes").exit_code == 0
    record = _store().get("arm")
    assert record is not None
    adapter = make_adapter("lerobot:mujoco", policy=PolicyChoice("hf:arm"), seed=0)
    said = adapter.ask_policy()  # type: ignore[attr-defined]
    assert said is not None and said["server"] == record.url and said["endpoint"] == "hf:arm"
    assert said["policy"] == "scripted:sweep"
    assert ACCOUNT_TOKEN not in json.dumps(said) and record.token not in json.dumps(said)
