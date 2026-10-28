"""Where a passport is found on a network (docs/passport/discovery.md): the DNS-SD beacon, and the
HTTP server a robot serves its own passport and its talk inbox from.

The server runs on a real socket, and DNS-SD on the fakes the flock bus and `quackd announce` are
tested with. A robot carries its own passport now, so a server serves one robot and no index.
"""

from __future__ import annotations

import urllib.error
import urllib.request
from dataclasses import replace
from typing import Any

import pytest

from quackd.lan.passports import (
    announce_passport,
    beacon_from_info,
    discover_passports,
    passport_record,
)
from quackd.passport import strict
from quackd.passport.beacon import (
    DIGEST_CHARS,
    SERVICE_TYPE,
    TXT_VERSION,
    WELL_KNOWN_JSON,
    WELL_KNOWN_JWS,
    Beacon,
    beacon_from_txt,
    txt_record,
)
from quackd.passport.serve import INBOX_PATH, PassportServer, Served
from tests.passport_helpers import OWNER, RUNTIME, A, B, doc, passport

BEACON = RUNTIME["robot-a"]["beacon"]


def _passport(name: str = "duck-01") -> bytes:
    return strict.dumps(doc(A, OWNER, name))


# ── the beacon ──────────────────────────────────────────────────────────────────────────


def test_the_txt_record_carries_version_short_id_digest_and_the_beacon_id_heard_by_radio() -> None:
    assert txt_record(short_id=A.short_id, digest="ab" * 32, beacon=BEACON) == {
        "v": TXT_VERSION,
        "id": A.short_id,
        "sha": ("ab" * 32)[:DIGEST_CHARS],
        "bt": BEACON,
    }
    # a robot with no radio advertising yet names no beacon id; a path is named when not the
    # well-known one
    quiet = txt_record(short_id=A.short_id, digest="cd" * 32, path="/passport.json")
    assert "bt" not in quiet and quiet["path"] == "/passport.json"
    assert "path" not in txt_record(short_id=A.short_id, digest="cd" * 32)


def test_a_beacon_of_another_version_on_no_port_or_with_a_relative_path_is_ignored() -> None:
    def beacon(txt: dict[str, str], port: int = 8790) -> Beacon | None:
        return beacon_from_txt(
            txt, instance="a", host="h.local.", port=port, addresses=("1.2.3.4",)
        )

    good = {"v": TXT_VERSION, "id": A.short_id, "sha": "x" * DIGEST_CHARS, "bt": BEACON}
    found = beacon(good)
    assert found is not None and found.url() == f"http://1.2.3.4:8790{WELL_KNOWN_JSON}"
    assert (found.short_id, found.beacon_id) == (A.short_id, BEACON)
    # a quackd 0.23 to 0.28 laptop's record, version 1, points at what a 1.0 reader refuses
    assert beacon({**good, "v": "1"}) is None
    assert beacon({k: v for k, v in good.items() if k != "sha"}) is None
    assert beacon(good, port=0) is None
    assert beacon({**good, "path": "elsewhere"}) is None
    no_radio = beacon({k: v for k, v in good.items() if k != "bt"})
    assert no_radio is not None and no_radio.beacon_id is None


def test_a_beacons_url_prefers_ipv4_and_brackets_ipv6() -> None:
    def at(*addresses: str) -> str:
        return Beacon(
            instance="a",
            short_id=A.short_id,
            digest="0" * DIGEST_CHARS,
            path=WELL_KNOWN_JSON,
            host="h.local.",
            port=8790,
            addresses=addresses,
        ).url()

    assert at("fe80::1c2b:3aff:fe4d:5e6f", "192.0.2.10") == (
        "http://192.0.2.10:8790/.well-known/robot-passport.json"
    )
    assert at("2001:db8::5") == "http://[2001:db8::5]:8790/.well-known/robot-passport.json"


# ── the HTTP server ─────────────────────────────────────────────────────────────────────


def _get(url: str) -> tuple[int, bytes, str]:
    try:
        with urllib.request.urlopen(url, timeout=5) as response:
            return response.status, response.read(), response.headers.get("Content-Type", "")
    except urllib.error.HTTPError as e:
        return e.code, b"", ""


def test_a_robot_serves_its_own_passport_at_the_well_known_url_and_nothing_else() -> None:
    raw = _passport()
    server = PassportServer(Served("duck-01", raw), host="127.0.0.1").start()
    try:
        base = f"http://127.0.0.1:{server.port}"
        assert _get(base + WELL_KNOWN_JSON) == (200, raw, "application/json")
        assert _get(base + WELL_KNOWN_JWS)[0] == 404  # not signed yet
        # where a laptop serving several robots put each one: no robot serves another's
        assert _get(base + "/robots/duck-01/robot-passport.json")[0] == 404
        assert _get(base + "/etc/passwd")[0] == 404
    finally:
        server.close()


def test_a_signed_passport_is_served_beside_it_and_an_update_swaps_or_withdraws_it() -> None:
    token = passport(A, OWNER, "duck-01")
    server = PassportServer(Served("duck-01", _passport()), host="127.0.0.1").start()
    try:
        url = f"http://127.0.0.1:{server.port}{WELL_KNOWN_JWS}"
        assert _get(url)[0] == 404
        server.update(Served("duck-01", _passport(), token))
        assert _get(url) == (200, token.encode(), "application/jose")
        # a robot with no valid passport serves none, rather than one its owner no longer signs
        server.update(None)
        assert _get(url)[0] == 404
        assert _get(f"http://127.0.0.1:{server.port}{WELL_KNOWN_JSON}")[0] == 404
    finally:
        server.close()


def test_with_no_inbox_a_post_is_404_and_with_one_it_is_handed_the_body() -> None:
    def post(port: int, body: bytes) -> int:
        request = urllib.request.Request(
            f"http://127.0.0.1:{port}{INBOX_PATH}",
            data=body,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return int(response.status)
        except urllib.error.HTTPError as e:
            return e.code

    silent = PassportServer(Served("duck-01", _passport()), host="127.0.0.1").start()
    try:
        assert post(silent.port, b"{}") == 404
    finally:
        silent.close()
    seen: list[tuple[bytes, str, str]] = []

    def inbox(body: bytes, kind: str, address: str) -> tuple[int, bytes | None, str]:
        seen.append((body, kind, address))
        return 204, None, "text/plain"

    listening = PassportServer(
        Served("duck-01", _passport()),
        host="127.0.0.1",
        inbox=inbox,
        inbox_limit=64,
    ).start()
    try:
        assert post(listening.port, b'{"hi":1}') == 204
        # the address it came from goes with it: where a reply to a hello may go back to
        assert seen == [(b'{"hi":1}', "application/json", "127.0.0.1")]
        assert post(listening.port, b"x" * 65) == 413
    finally:
        listening.close()


# ── DNS-SD ──────────────────────────────────────────────────────────────────────────────


class FakeRegistrar:
    def __init__(self) -> None:
        self.registered: list[Any] = []

    def register_service(self, info: Any) -> None:
        self.registered.append(info)

    def unregister_service(self, info: Any) -> None:
        self.registered.remove(info)


def test_a_passport_is_announced_and_discovered_with_the_beacon_id_its_radio_sends() -> None:
    record = passport_record(
        instance="duck-01",
        port=8790,
        short_id=A.short_id,
        digest="ef" * 32,
        beacon=BEACON,
        host="duck-01.local",
        addresses=("192.0.2.10",),
    )
    assert record.type == SERVICE_TYPE == "_robot-passport._tcp.local."
    registrar = FakeRegistrar()
    announcement = announce_passport(record, zc=registrar, info_factory=lambda r: r)
    assert registrar.registered == [record]
    found = discover_passports(
        1.0, browse=lambda _zc, kind, _t: registrar.registered if kind == SERVICE_TYPE else []
    )
    assert [(b.short_id, b.beacon_id, b.url()) for b in found] == [
        (A.short_id, BEACON, f"http://192.0.2.10:8790{WELL_KNOWN_JSON}")
    ]
    announcement.close()
    assert registrar.registered == []


def test_discovery_skips_a_record_of_the_version_before_owners_signed_passports() -> None:
    old = passport_record(
        instance="cart-01", port=8790, short_id=B.short_id, digest="12" * 32, host="lab-pc"
    )
    old = replace(old, properties={**old.properties, "v": "1"})  # what quackd 0.23 to 0.28 sent
    new = passport_record(
        instance="duck-01", port=8790, short_id=A.short_id, digest="34" * 32, host="duck-01"
    )
    found = discover_passports(1.0, browse=lambda _zc, _kind, _t: [old, new])
    assert [b.short_id for b in found] == [A.short_id]


def test_a_beacon_on_no_port_is_refused_before_it_is_announced() -> None:
    with pytest.raises(ValueError):
        passport_record(instance="a", port=0, short_id=A.short_id, digest="0" * 64)
    assert beacon_from_info(object()) is None
