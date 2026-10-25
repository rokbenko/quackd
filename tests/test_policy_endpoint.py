"""`quackd policy endpoint`'s module: the account's token, the record this machine keeps, the
body an endpoint is created from, the checks before one is, the wait for one to serve, and how
a run reaches one (`quackd_lerobot.policy.endpoint`).

Everything that reaches a server here reaches `tests/fake_hugging_face.py`'s stand-ins on
127.0.0.1, with the module's three addresses and the client's rule for where the account's token
may go pointed there (`hf`). Nothing here reaches Hugging Face or PyPI, and the suite's guard
fails any test that tries (`tests/conftest.py`, and its own proof at the foot of this file).
"""

from __future__ import annotations

import itertools
import json
import socket
import sys
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from quackd_lerobot import __version__
from quackd_lerobot.policy import client as C
from quackd_lerobot.policy import endpoint as E
from quackd_lerobot.policy import endpoint_upstream_api as up
from quackd_lerobot.policy import protocol as wire
from quackd_lerobot.policy import server as S
from quackd_lerobot.policy.client import RemoteRunner
from quackd_lerobot.verbs import JOINTS
from tests.fake_hugging_face import (
    ACCOUNT_TOKEN,
    ACT_CONFIG,
    UPLOADED,
    FakeAPI,
    FakeContainer,
    point,
    provider_listing,
)

ACT = "natsuki0000/act-so101-bluecap@82f75fe40a311026b4f7cacdea7bf14cadc44ccd"
ACT_REPO = "natsuki0000/act-so101-bluecap"
PIN = "HuggingFaceTB/SmolVLM2-500M-Video-Instruct@7b375e1b73b11138ff12fe22c8f2822d8fe03467"
HUB_TOKEN = "hf_" + "HubReadToken" * 3
"""A second stand-in token, the kind `--hub-token-from` names for a gated checkpoint."""
GOOD_URL = "https://f3b5c6ee5c9dcc0e0a01.endpoints.huggingface.cloud"
IS_ENDPOINT_HOST = C.is_endpoint_host
"""The client's rule as it ships, kept before any test replaces it."""
AFTER = "2026-10-25T14:08:57Z"
"""An hour after `UPLOADED`, to the second, which is the container's `--exclude-newer`."""


@pytest.fixture
def hf(monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeAPI]:
    with FakeAPI() as api:
        point(monkeypatch, api)
        monkeypatch.setenv("HF_TOKEN", ACCOUNT_TOKEN)
        yield api


def _record(**kw: Any) -> E.EndpointRecord:
    return replace(
        E.new_record(
            "arm",
            namespace="rok",
            policy=ACT,
            instance="nvidia-l4",
            vendor="aws",
            region="us-east-1",
            idle_minutes=15,
        ),
        **kw,
    )


def _created(hf: FakeAPI, record: E.EndpointRecord, **kw: Any) -> E.HFEndpoints:
    api = E.HFEndpoints(ACCOUNT_TOKEN)
    api.create(record.namespace, E.create_payload(record, accelerator="gpu", exclude_newer=AFTER))
    return api


# ── the account's token ─────────────────────────────────────────────────────────────────


def test_the_account_token_is_found_where_huggingface_hub_finds_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`HF_TOKEN`, then `HUGGING_FACE_HUB_TOKEN`, then the file `HF_TOKEN_PATH` names, else
    `$HF_HOME/token`, an empty variable counting as unset, and a file's line breaks and spaces
    taken off (`endpoint_upstream_api.TOKEN_LOOKUP`)."""
    home = tmp_path / "hf-home"
    home.mkdir()
    (home / "token").write_text("hf_" + "F" * 34 + "\n", encoding="utf-8")
    monkeypatch.setenv("HF_HOME", str(home))
    assert E.hf_token() == ("hf_" + "F" * 34, str(home / "token"))
    named = tmp_path / "named.token"
    named.write_text(" hf_" + "P" * 34 + " \r\n", encoding="utf-8")
    monkeypatch.setenv("HF_TOKEN_PATH", str(named))
    assert E.hf_token() == ("hf_" + "P" * 34, str(named))
    monkeypatch.setenv("HUGGING_FACE_HUB_TOKEN", "hf_" + "O" * 34)
    assert E.hf_token() == ("hf_" + "O" * 34, "$HUGGING_FACE_HUB_TOKEN")
    monkeypatch.setenv("HF_TOKEN", "hf_" + "N" * 34)
    assert E.hf_token() == ("hf_" + "N" * 34, "$HF_TOKEN")
    monkeypatch.setenv("HF_TOKEN", "  ")
    assert E.hf_token() == ("hf_" + "O" * 34, "$HUGGING_FACE_HUB_TOKEN")
    for name in ("HF_TOKEN", "HUGGING_FACE_HUB_TOKEN"):
        monkeypatch.setenv(name, "")
    monkeypatch.delenv("HF_TOKEN_PATH")
    (home / "token").unlink()
    assert E.hf_token() is None
    monkeypatch.delenv("HF_HOME")
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg"))
    assert E.token_path() == tmp_path / "xdg" / "huggingface" / "token"


def test_an_account_token_no_header_can_carry_is_refused_without_being_quoted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for bad in ("hf_" + "S" * 20 + "\nInjected: yes", "hf_" + "S" * 20 + " x", "hf_SHORT"):
        monkeypatch.setenv("HF_TOKEN", bad)
        with pytest.raises(ValueError) as refused:
            E.hf_token()
        assert "SSSS" not in str(refused.value) and "SHORT" not in str(refused.value)


def test_no_variable_moves_where_the_account_token_goes(
    hf: FakeAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """huggingface_hub sends the token wherever `HF_ENDPOINT` says; quackd sends it to its own
    constants and nowhere a variable could move it, and those are Hugging Face's own, over
    https."""
    with FakeAPI() as elsewhere:
        monkeypatch.setenv("HF_ENDPOINT", elsewhere.url)
        E.HFEndpoints(ACCOUNT_TOKEN).whoami()
        assert hf.sent("/api/whoami-v2") and not elsewhere.requests
    source = Path(E.__file__).read_text(encoding="utf-8")
    assert 'environ.get("HF_ENDPOINT")' not in source and "getenv(" not in source


def test_the_addresses_are_hugging_faces_and_pypis_own_over_https() -> None:
    assert (E.API, E.HUB, E.PYPI) == (
        "https://api.endpoints.huggingface.cloud",
        "https://huggingface.co",
        "https://pypi.org",
    )


def test_the_account_token_goes_to_the_api_and_the_hub_and_never_to_pypi_or_the_listing(
    hf: FakeAPI,
) -> None:
    """The provider listing is the one route that wants no token, and PyPI is no business of the
    account's: neither is sent it."""
    api = E.HFEndpoints(ACCOUNT_TOKEN)
    api.provider()
    api.release("quackd", __version__)
    api.whoami()
    api.quotas("rok")
    api.model(ACT_REPO, "main", token=api.token)
    assert len(hf.requests) == 5
    for seen in hf.requests:
        public = seen.path == up.PROVIDER_PATH or seen.path.startswith("/pypi/")
        given = seen.headers.get("authorization")
        assert given == (None if public else f"Bearer {ACCOUNT_TOKEN}"), seen.path


@pytest.mark.parametrize(
    ("address", "needle"),
    [
        ("http://10.0.0.5:8080", "https alone"),
        ("http://api.endpoints.huggingface.cloud", "https alone"),
        ("ftp://api.endpoints.huggingface.cloud", "https alone"),
        ("https://rok:pw@api.example", "sends a token to"),
    ],
)
def test_management_traffic_is_https_or_plain_http_to_loopback_alone(
    monkeypatch: pytest.MonkeyPatch, address: str, needle: str
) -> None:
    monkeypatch.setattr(E, "API", address)
    with pytest.raises(E.EndpointError, match=needle) as refused:
        E.HFEndpoints(ACCOUNT_TOKEN).provider()
    assert ACCOUNT_TOKEN not in str(refused.value)


def test_a_hub_token_is_named_by_its_variable_and_is_never_the_account_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A gated checkpoint is fetched by the endpoint with a token of its own, and the account's
    token never goes into an endpoint: `--hub-token-from` holding it is refused, in a sentence
    that does not quote it."""
    with pytest.raises(E.EndpointError, match="not a variable's name"):
        E.hub_token_from("hf_abc def", ACCOUNT_TOKEN)
    monkeypatch.delenv("HUB_READ", raising=False)
    with pytest.raises(E.EndpointError, match="is not set"):
        E.hub_token_from("HUB_READ", ACCOUNT_TOKEN)
    monkeypatch.setenv("HUB_READ", f" {ACCOUNT_TOKEN} ")
    with pytest.raises(E.EndpointError, match="the account's own token") as refused:
        E.hub_token_from("HUB_READ", ACCOUNT_TOKEN)
    assert ACCOUNT_TOKEN not in str(refused.value)
    monkeypatch.setenv("HUB_READ", HUB_TOKEN)
    assert E.hub_token_from("HUB_READ", ACCOUNT_TOKEN) == HUB_TOKEN


def test_a_refusal_from_hugging_face_says_what_was_asked_and_holds_no_token(
    hf: FakeAPI,
) -> None:
    """A token the API refuses is said with what it was asked to do and where the token came
    from, and a refusal of anything else with what Hugging Face said, scrubbed."""
    hf.token = "hf_" + "SomebodyElse" * 3
    with pytest.raises(E.EndpointError) as refused:
        E.HFEndpoints(ACCOUNT_TOKEN).whoami()
    said = str(refused.value)
    assert refused.value.status == 401 and ACCOUNT_TOKEN not in said
    assert "refused the token when asked to say whose token it is (HTTP 401" in said
    assert "HF_TOKEN or the file hf auth login writes" in said
    hf.token = ACCOUNT_TOKEN
    hf.refuse_create = 409
    with pytest.raises(E.EndpointError, match="would not create arm \\(HTTP 409\\)"):
        E.HFEndpoints(ACCOUNT_TOKEN).create("rok", {"name": "arm"})


# ── this machine's record ───────────────────────────────────────────────────────────────


def test_the_record_is_kept_for_its_owner_alone_and_read_back_whole(tmp_path: Path) -> None:
    """It holds each endpoint's token, so it goes through the registry's own write, readable by
    its owner alone where the OS allows (on Windows it takes the profile's permissions)."""
    store = E.EndpointStore(tmp_path)
    record = _record(pins=(PIN,), flags=("--fps", "30.0"), url=GOOD_URL)
    store.save(record)
    assert store.path == tmp_path / E.STORE_FILE
    if sys.platform != "win32":
        assert store.path.stat().st_mode & 0o777 == 0o600
    assert store.get("arm") == record
    saved = json.loads(store.path.read_text(encoding="utf-8"))
    assert (
        saved["version"] == E.STORE_VERSION and saved["endpoints"]["arm"]["token"] == record.token
    )
    assert len(record.token) == 64 and set(record.token) <= set("0123456789abcdef")
    assert "token" not in record.public() and record.public()["pins"] == [PIN]
    assert store.forget("arm") and store.get("arm") is None and not store.forget("arm")


@pytest.mark.parametrize(
    ("change", "needle"),
    [
        ({"url": "https://evil.example"}, "not a Hugging Face Endpoint's"),
        ({"url": "http://f3b5.endpoints.huggingface.cloud"}, "not a Hugging Face Endpoint's"),
        ({"token": "short"}, "no token its server could take"),
        ({"idle_minutes": "15"}, "has no idle_minutes"),
        ({"name": "another"}, "is not a record"),
    ],
)
def test_a_record_that_is_not_what_up_writes_is_refused_naming_it(
    tmp_path: Path, change: dict[str, Any], needle: str
) -> None:
    """An edited record could otherwise send the account's token somewhere that is not an
    endpoint, or hand the server a token it would refuse."""
    store = E.EndpointStore(tmp_path)
    store.save(_record())
    saved = json.loads(store.path.read_text(encoding="utf-8"))
    saved["endpoints"]["arm"].update(change)
    store.path.write_text(json.dumps(saved), encoding="utf-8")
    with pytest.raises(E.EndpointError, match=needle) as refused:
        store.read()
    assert "endpoint delete arm" in str(refused.value)
    store.path.write_text("{not json", encoding="utf-8")
    with pytest.raises(E.EndpointError, match="cannot be read"):
        store.read()
    store.path.write_text(json.dumps({"version": 99, "endpoints": {}}), encoding="utf-8")
    with pytest.raises(E.EndpointError, match="upgrade quackd"):
        store.read()


# ── what an endpoint is created from ────────────────────────────────────────────────────


def test_an_endpoint_is_created_authenticated_on_one_replica_with_quackds_token_alone() -> None:
    """`authenticated` and nothing else, one GPU and one replica, since a session lives in one
    process, scaled to zero after the idle minutes, uv's image by its digest, the server's own
    health route, quackd's token as the one secret, and quackd's tag. The account's token is
    nowhere in it, and every string in it is a POSIX one, whatever this machine is."""
    flags = E.serve_flags(fps=30.0, cameras="front=observation.images.front", latency_s=0.25)
    record = _record(pins=(PIN,), flags=flags)
    payload = E.create_payload(record, accelerator="gpu", exclude_newer=AFTER)
    assert payload["type"] == up.CREATED_TYPE == "authenticated"
    assert payload["provider"] == {"vendor": "aws", "region": "us-east-1"}
    compute = payload["compute"]
    assert (compute["accelerator"], compute["instanceType"], compute["instanceSize"]) == (
        "gpu",
        "nvidia-l4",
        "x1",
    )
    assert compute["scaling"] == {"minReplica": 0, "maxReplica": 1, "scaleToZeroTimeout": 15}
    model = payload["model"]
    assert (model["repository"], model["revision"], model["framework"]) == (
        ACT_REPO,
        "82f75fe40a311026b4f7cacdea7bf14cadc44ccd",
        "custom",
    )
    assert model["image"] == {
        "custom": {"url": up.IMAGE, "port": wire.DEFAULT_PORT, "healthRoute": wire.HEALTH_PATH}
    }
    assert model["env"] == {"PYTHONUNBUFFERED": "1"}
    assert model["secrets"] == {wire.TOKEN_ENV: record.token}
    assert model["command"] == [
        "uvx",
        "--python",
        "3.12",
        "--exclude-newer",
        AFTER,
        "--from",
        f"quackd[lerobot-vla]=={__version__}",
        "--with",
        f"quackd-lerobot[vla]=={__version__}",
        "quackd",
        "policy",
        "serve",
    ]
    assert model["args"] == [
        "--policy",
        ACT,
        "--pin",
        PIN,
        "--fps",
        "30.0",
        "--cameras",
        "front=observation.images.front",
        "--latency-s",
        "0.25",
        "--bind",
        "0.0.0.0",
        "--port",
        str(wire.DEFAULT_PORT),
        "--behind-tls",
        "--token-env",
        wire.TOKEN_ENV,
    ]
    assert payload["tags"] == [E.TAG]
    text = json.dumps(payload)
    assert ACCOUNT_TOKEN not in text and "\\\\" not in text
    gated = E.create_payload(record, accelerator="gpu", exclude_newer=AFTER, hub_token=HUB_TOKEN)
    assert gated["model"]["secrets"] == {wire.TOKEN_ENV: record.token, "HF_TOKEN": HUB_TOKEN}


def test_the_container_reads_its_arguments_as_quackd_policy_serve_reads_them() -> None:
    """The stand-in container parses the arguments with the serve command's own parser and builds
    its options as the command does, so the flags `up` was given reach the server as typed, and
    what it serves in place of the checkpoint answers with them: behind TLS, so JPEG frames at
    90, and its declared latency."""
    flags = E.serve_flags(fps=30.0, cameras="front=observation.images.front", latency_s=0.25)
    record = _record(pins=(PIN,), flags=flags)
    container = FakeContainer(E.create_payload(record, accelerator="gpu", exclude_newer=AFTER))
    try:
        assert container.options == S.ServeOptions(
            policy=ACT,
            fps=30.0,
            cameras="front=observation.images.front",
            latency_s=0.25,
            bind="0.0.0.0",
            port=wire.DEFAULT_PORT,
            token_env=wire.TOKEN_ENV,
            behind_tls=True,
            pins=(PIN,),
        )
        assert container.exclude_newer == AFTER
        info = RemoteRunner(container.url, token=record.token, motors=JOINTS).policy()
        assert (info.policy, info.gpu, info.jpeg_quality, info.latency_s) == (
            "scripted:sweep",
            True,
            wire.DEFAULT_JPEG_QUALITY,
            0.25,
        )
        assert info.cameras == {"front": "observation.images.front"} and info.rate_hz == 30
    finally:
        container.close()


def test_the_container_installs_nothing_uploaded_after_an_hour_past_the_release() -> None:
    """Every start of an endpoint, from zero a month later as on its first, resolves the same
    torch and transformers: nothing uploaded after an hour past the release's last file."""
    core = {"urls": [{"upload_time_iso_8601": "2026-10-25T13:08:28.105954Z"}]}
    adapter = {"urls": [{"upload_time_iso_8601": UPLOADED}, {"upload_time_iso_8601": "garbage"}]}
    assert E.exclude_newer(core, adapter) == AFTER
    with pytest.raises(E.EndpointError, match="no upload time"):
        E.exclude_newer({"urls": []})


@pytest.mark.parametrize(
    ("vendor", "region", "instance", "needle"),
    [
        ("aws", "us-east-1", "inf2", "is a neuron instance"),
        ("aws", "us-east-1", "intel-spr", "is a cpu instance"),
        ("gcp", "us-east-1", "nvidia-l4", "no vendor called gcp: --vendor is one of aws"),
        ("aws", "us-west-2", "nvidia-l4", "no region us-west-2"),
        ("aws", "eu-west-1", "nvidia-l4", "the GPUs there are nvidia-a100, nvidia-a10g, nvidia-t4"),
        ("aws", "eu-west-1", "nvidia-a100", "is not_available"),
    ],
)
def test_an_instance_that_is_no_gpu_or_not_offered_is_refused_naming_what_is(
    vendor: str, region: str, instance: str, needle: str
) -> None:
    with pytest.raises(E.EndpointError, match=needle):
        E.find_compute(provider_listing(), vendor, region, instance)


def test_the_gpu_asked_for_is_found_with_its_price_on_the_day() -> None:
    compute = E.find_compute(provider_listing(), "aws", "us-east-1", "nvidia-l4")
    assert (compute.accelerator, compute.price_per_hour, compute.gpu_memory_gb) == ("gpu", 0.8, 24)
    europe = E.find_compute(provider_listing(), "aws", "eu-west-1", "nvidia-a10g")
    assert europe.price_per_hour == 1.0


def test_the_question_says_whose_account_what_where_and_what_it_bills() -> None:
    record = _record()
    compute = E.find_compute(provider_listing(), "aws", "us-east-1", "nvidia-l4")
    assert E.confirmation(record, compute) == (
        "This starts a Hugging Face Inference Endpoint on rok's account: arm, serving "
        f"{ACT} on nvidia-l4 x1 in aws us-east-1. It bills $0.80 an hour while it runs "
        "(Hugging Face's price, read just now), scales to zero after 15 minutes with no "
        "request, and bills nothing then or while paused. Start it?"
    )
    resumed = E.confirmation(record, compute, resuming=True)
    assert resumed.startswith("This resumes a Hugging Face") and resumed.endswith("Resume it?")


# ── before anything is created ──────────────────────────────────────────────────────────


def _preflight(**kw: Any) -> E.Preflight:
    return E.preflight(
        E.HFEndpoints(ACCOUNT_TOKEN),
        policy=kw.pop("policy", ACT),
        pins=kw.pop("pins", ()),
        instance=kw.pop("instance", "nvidia-l4"),
        vendor="aws",
        region="us-east-1",
        **kw,
    )


def test_preflight_reads_what_it_needs_and_changes_nothing(hf: FakeAPI) -> None:
    check = _preflight(pins=(PIN,))
    assert (check.namespace, check.account, check.compute.price_per_hour) == ("rok", "rok", 0.8)
    assert check.exclude_newer == AFTER and check.restricted == ()
    assert all(r.method == "GET" for r in hf.requests), "nothing is created or changed"
    assert hf.sent(f"/{ACT_REPO}/raw/") and hf.sent(f"/api/models/{ACT_REPO}/revision/")


@pytest.mark.parametrize(
    ("setup", "needle"),
    [
        ({"releases": {"quackd": [], "quackd-lerobot": []}}, "is not a release on PyPI"),
        ({"models": {ACT_REPO: {"private": False, "gated": "manual"}}}, "gated or private"),
        ({"models": {ACT_REPO: {"private": True, "gated": False}}}, "gated or private"),
        (
            {"models": {PIN.partition("@")[0]: {"private": False, "gated": "auto"}}},
            f"{PIN} is gated or private",
        ),
        ({"models": {ACT_REPO: None}}, "the Hub has no natsuki0000/act-so101-bluecap"),
        ({"quota": 0}, "has no nvidia-l4 left in its quota"),
        ({"account_type": "org"}, "--namespace"),
        ({"configs": {ACT_REPO: {**ACT_CONFIG, "type": "diffusion"}}}, "quackd serves"),
    ],
)
def test_preflight_refuses_before_anything_is_created(
    hf: FakeAPI, setup: dict[str, Any], needle: str
) -> None:
    for key, value in setup.items():
        setattr(hf, key, value)
    with pytest.raises(E.EndpointError, match=needle) as refused:
        _preflight(pins=(PIN,))
    assert ACCOUNT_TOKEN not in str(refused.value)
    assert all(r.method == "GET" for r in hf.requests), "nothing was created"


def test_a_gated_checkpoint_is_read_with_the_hub_token_the_endpoint_will_fetch_it_with(
    hf: FakeAPI,
) -> None:
    """With `--hub-token-from`, the gated checkpoint's config is read with the token the endpoint
    will fetch it with, which proves that token can, and its visibility with the account's."""
    hf.models[ACT_REPO] = {"private": False, "gated": "manual"}
    hf.hub_token = HUB_TOKEN
    check = _preflight(hub_token=HUB_TOKEN)
    assert check.restricted == (ACT,)
    (raw,) = hf.sent(f"/{ACT_REPO}/raw/")
    assert raw.headers["authorization"] == f"Bearer {HUB_TOKEN}"
    (info,) = hf.sent(f"/api/models/{ACT_REPO}/")
    assert info.headers["authorization"] == f"Bearer {ACCOUNT_TOKEN}"


# ── waiting for it to serve ─────────────────────────────────────────────────────────────


def test_up_waits_through_each_state_and_sends_the_bearer_only_to_an_address_it_recorded(
    hf: FakeAPI, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Each state is said once, with the seconds since the wait began. The address Hugging Face
    gives is recorded before the account's token goes there, and the wait ends once the server
    there answers ready."""
    store = E.EndpointStore(tmp_path)
    record = _record()
    store.save(record)
    api = _created(hf, record)
    real = api.health

    def health(url: str) -> bool:
        kept = store.get("arm")
        assert kept is not None and kept.url == url, "the bearer went to an address not recorded"
        return real(url)

    monkeypatch.setattr(api, "health", health)
    said: list[str] = []
    done = E.wait_until_serving(
        api, record, minutes=1, store=store, said=lambda s, t: said.append(s)
    )
    assert said == ["pending", "initializing", "running"]
    (proxy,) = hf.proxies
    assert done.url == proxy and store.get("arm") == done
    ep = hf.endpoints[("rok", "arm")]
    assert ep.proxy.requests and all(
        r.headers.get("authorization") == f"Bearer {ACCOUNT_TOKEN}" for r in ep.proxy.requests
    )


def test_an_address_that_is_not_an_endpoints_gets_no_bearer(
    hf: FakeAPI, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The stand-in's proxy is on 127.0.0.1, which the client's own rule does not take for an
    endpoint's: with that rule as it is, the wait refuses the address before anything is sent
    there, and the record does not keep it."""
    monkeypatch.setattr(C, "is_endpoint_host", IS_ENDPOINT_HOST)
    store = E.EndpointStore(tmp_path)
    record = _record()
    store.save(record)
    api = _created(hf, record)
    with pytest.raises(E.EndpointError, match="an address that is not an endpoint's") as refused:
        E.wait_until_serving(api, record, minutes=1, store=store)
    assert ACCOUNT_TOKEN not in str(refused.value)
    assert not hf.endpoints[("rok", "arm")].proxy.requests
    assert store.get("arm") == record


def test_one_that_scaled_to_zero_is_woken_by_the_wait_and_waited_for(
    hf: FakeAPI, tmp_path: Path
) -> None:
    store = E.EndpointStore(tmp_path)
    record = _record()
    store.save(record)
    api = _created(hf, record)
    record = E.wait_until_serving(api, record, minutes=1, store=store)
    api._api("POST", api._path("rok", "arm", "scale-to-zero"), "scale it to zero")
    said: list[str] = []
    E.wait_until_serving(api, record, minutes=1, store=store, said=lambda s, t: said.append(s))
    assert said == ["scaledToZero", "initializing", "running"]


def test_an_endpoint_that_fails_is_paused_and_its_log_quoted_without_a_token(
    hf: FakeAPI, tmp_path: Path
) -> None:
    hf.fail = True
    store = E.EndpointStore(tmp_path)
    record = _record()
    store.save(record)
    api = _created(hf, record)
    with pytest.raises(E.EndpointFailed, match="hf:arm reached failed"):
        E.wait_until_serving(api, record, minutes=1, store=store)
    ep = hf.endpoints[("rok", "arm")]
    ep.log += [f"QUACKD_POLICY_TOKEN={record.token}", f"Authorization: Bearer {ACCOUNT_TOKEN}"]
    lines, bill = E.pause_and_quote(api, record)
    assert bill == "so it was paused, and bills nothing now" and ep.state == "paused"
    assert lines[-2:] == ["QUACKD_POLICY_TOKEN=<token>", "Authorization: Bearer <token>"]
    assert not any(record.token in line or ACCOUNT_TOKEN in line for line in lines)


def test_an_endpoint_that_does_not_serve_in_time_is_a_timeout(hf: FakeAPI, tmp_path: Path) -> None:
    store = E.EndpointStore(tmp_path)
    record = _record()
    store.save(record)
    api = _created(hf, record)
    hf.endpoints[("rok", "arm")].ahead = []  # it stays pending
    ticks = itertools.count(0, 25)
    with pytest.raises(E.EndpointTimeout, match="did not serve within 1 minutes"):
        E.wait_until_serving(
            api,
            record,
            minutes=1,
            store=store,
            clock=lambda: float(next(ticks)),
            sleep=lambda s: None,
        )


# ── how a run reaches one ───────────────────────────────────────────────────────────────


def test_hf_name_reaches_the_recorded_address_with_its_token_and_the_accounts_bearer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The token is `--policy-token` when one was typed, else the record's, and never
    `QUACKD_POLICY_TOKEN` or the file `quackd policy serve` writes, which are other servers'.
    The recorded https address reaches the same endpoint, and any other address is not one."""
    store = E.EndpointStore(tmp_path)
    record = _record(url=GOOD_URL)
    store.save(record)
    monkeypatch.setenv("HF_TOKEN", ACCOUNT_TOKEN)
    monkeypatch.setenv(wire.TOKEN_ENV, "e" * 64)
    reached = E.reach("hf:arm", registry_dir=tmp_path)
    assert reached == E.Reached(GOOD_URL, record.token, ACCOUNT_TOKEN, "hf:arm")
    assert E.reach(" hf:arm ", policy_token="f" * 64, registry_dir=tmp_path).token == "f" * 64  # type: ignore[union-attr]
    assert E.reach(GOOD_URL + "/", registry_dir=tmp_path) == reached
    assert E.reach("https://gpu.example.com", registry_dir=tmp_path) is None
    assert E.reach("http://127.0.0.1:9875", registry_dir=tmp_path) is None


@pytest.mark.parametrize("case", ["no record", "no address", "no account token", "unreadable"])
def test_hf_name_with_nothing_to_reach_it_by_is_refused_before_anything_connects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    store = E.EndpointStore(tmp_path)
    monkeypatch.setenv("HF_TOKEN", ACCOUNT_TOKEN)
    needle = {
        "no record": "no endpoint called arm is recorded on this machine",
        "no address": "hf:arm has no address yet",
        "no account token": "no Hugging Face token to reach hf:arm",
        "unreadable": "cannot be read",
    }[case]
    if case == "no address":
        store.save(_record())
    elif case == "no account token":
        store.save(_record(url=GOOD_URL))
        monkeypatch.setenv("HF_TOKEN", "")
    elif case == "unreadable":
        store.path.parent.mkdir(parents=True, exist_ok=True)
        store.path.write_text("{", encoding="utf-8")
        assert E.reach(GOOD_URL, registry_dir=tmp_path) is None, "an address is reached as any"
    with pytest.raises(ValueError, match=needle):
        E.reach("hf:arm", registry_dir=tmp_path)


# ── the suite's guard ───────────────────────────────────────────────────────────────────


def test_the_suite_stops_any_lookup_of_hugging_face_or_pypi(
    _no_request_reaches_hugging_face: list[str],
) -> None:
    """The guard in `tests/conftest.py` stops a lookup of any host of Hugging Face's or PyPI's,
    the module's own unpatched addresses among them, and lets anything else through. What it
    wrote down is taken back here, or this test's own teardown would fail it."""
    reached = _no_request_reaches_hugging_face
    for host in ("api.endpoints.huggingface.cloud", "x.endpoints.huggingface.cloud", "PyPI.org."):
        with pytest.raises(OSError):
            socket.getaddrinfo(host, 443)
    with pytest.raises(E.EndpointError, match="could not be reached"):
        E.HFEndpoints(ACCOUNT_TOKEN).provider()
    assert set(reached) == {
        "api.endpoints.huggingface.cloud",
        "x.endpoints.huggingface.cloud",
        "pypi.org",
    }
    reached.clear()
    assert socket.getaddrinfo("127.0.0.1", 80)
