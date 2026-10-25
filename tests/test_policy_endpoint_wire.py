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
