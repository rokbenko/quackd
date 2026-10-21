"""Where a passport is found (docs/passport/discovery.md): the beacon, the HTTP server, DNS-SD,
MQTT, a QR sticker and a ROS topic read through rosbridge.

Every binding runs here on what proves it best: the server on a real socket, DNS-SD and MQTT on
fakes the way the flock bus and `quackd announce` are tested, the sticker through OpenCV's own
encoder and decoder, and the ROS read on the fakes the rosbridge adapter's tests use.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import pytest

from quackd.lan.passport_mqtt import PassportPublisher, key_for, read_passport, topics
from quackd.lan.passports import (
    announce_passport,
    beacon_from_info,
    discover_passports,
    passport_record,
)
from quackd.passport import PassportError, SigningKey, document, strict
from quackd.passport.beacon import (
    SERVICE_TYPE,
    WELL_KNOWN_JSON,
    WELL_KNOWN_JWS,
    Beacon,
    beacon_from_txt,
    sticker_url,
    txt_record,
)
from quackd.passport.serve import INBOX_PATH, PassportServer, Served, robot_paths
from quackd.passport_qr import read_qr, write_qr

KEY = SigningKey(bytes(range(32)))
OTHER = SigningKey(bytes(range(64, 96)))


def _passport(key: SigningKey, name: str) -> bytes:
    return strict.dumps({"passport": "0.1", "id": key.did, "name": name})


# ── the beacon ──────────────────────────────────────────────────────────────────────────


def test_the_txt_record_carries_version_short_id_and_digest() -> None:
    assert txt_record(short_id=KEY.short_id, digest="ab" * 32) == {
        "v": "1",
        "sha": "ab" * 8,
        "id": KEY.short_id,
    }
    no_key = txt_record(short_id=None, digest="cd" * 32, path="/robots/a/robot-passport.json")
    assert "id" not in no_key and no_key["path"] == "/robots/a/robot-passport.json"


def test_a_beacon_of_another_version_on_no_port_or_with_a_relative_path_is_ignored() -> None:
    def beacon(txt: dict[str, str], port: int = 8790) -> Beacon | None:
        return beacon_from_txt(
            txt, instance="a", host="h.local.", port=port, addresses=("1.2.3.4",)
        )

    assert beacon({"v": "1", "sha": "x" * 16}) is not None
    assert beacon({"v": "2", "sha": "x" * 16}) is None
    assert beacon({"v": "1"}) is None
    assert beacon({"v": "1", "sha": "x" * 16}, port=0) is None
    assert beacon({"v": "1", "sha": "x" * 16, "path": "elsewhere"}) is None
    found = beacon({"v": "1", "sha": "x" * 16})
    assert found is not None and found.url() == f"http://1.2.3.4:8790{WELL_KNOWN_JSON}"


def test_a_sticker_url_carries_the_short_id_in_its_fragment() -> None:
    assert sticker_url("https://example.org/r.json", KEY.short_id) == (
        f"https://example.org/r.json#rp={KEY.short_id}"
    )
    assert sticker_url("https://example.org/r.json#old", None) == "https://example.org/r.json"
    with pytest.raises(PassportError):
        sticker_url("ftp://example.org/r.json", KEY.short_id)


# ── the HTTP server ─────────────────────────────────────────────────────────────────────


def _get(url: str) -> tuple[int, bytes, str]:
    try:
        with urllib.request.urlopen(url, timeout=5) as response:
            return response.status, response.read(), response.headers.get("Content-Type", "")
    except urllib.error.HTTPError as e:
        return e.code, b"", ""


def test_one_robot_is_served_at_the_well_known_url_byte_for_byte() -> None:
    raw = _passport(KEY, "duck-01")
    server = PassportServer([Served("duck-01", raw)], host="127.0.0.1").start()
    try:
        base = f"http://127.0.0.1:{server.port}"
        assert _get(base + WELL_KNOWN_JSON) == (200, raw, "application/json")
        assert _get(base + robot_paths("duck-01")[0])[1] == raw
        assert _get(base + WELL_KNOWN_JWS)[0] == 404
        assert _get(base + "/etc/passwd")[0] == 404
    finally:
        server.close()


def test_several_robots_get_an_index_on_the_same_host() -> None:
    robots = [
        Served("duck-01", _passport(KEY, "duck-01")),
        Served("cart-01", _passport(OTHER, "cart-01")),
    ]
    server = PassportServer(robots, host="127.0.0.1").start()
    try:
        status, body, _kind = _get(f"http://127.0.0.1:{server.port}{WELL_KNOWN_JSON}")
        entries = document.read(body)
        assert status == 200 and isinstance(entries, list)
        assert [(e.name, e.id, e.url) for e in entries] == [
            ("duck-01", KEY.did, robot_paths("duck-01")[0]),
            ("cart-01", OTHER.did, robot_paths("cart-01")[0]),
        ]
    finally:
        server.close()


def test_a_signed_robot_is_served_its_jws_and_an_update_swaps_what_is_served() -> None:
    raw = _passport(KEY, "duck-01")
    token = document.sign(json.loads(raw), KEY)
    server = PassportServer([Served("duck-01", raw)], host="127.0.0.1").start()
    try:
        url = f"http://127.0.0.1:{server.port}{WELL_KNOWN_JWS}"
        assert _get(url)[0] == 404
        server.update([Served("duck-01", raw, token)])
        assert _get(url) == (200, token.encode(), "application/jose")
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

    silent = PassportServer(
        [Served("duck-01", _passport(KEY, "duck-01"))], host="127.0.0.1"
    ).start()
    try:
        assert post(silent.port, b"{}") == 404
    finally:
        silent.close()
    seen: list[tuple[bytes, str]] = []

    def inbox(body: bytes, kind: str) -> tuple[int, bytes | None, str]:
        seen.append((body, kind))
        return 204, None, "text/plain"

    listening = PassportServer(
        [Served("duck-01", _passport(KEY, "duck-01"))],
        host="127.0.0.1",
        inbox=inbox,
        inbox_limit=64,
    ).start()
    try:
        assert post(listening.port, b'{"hi":1}') == 204
        assert seen == [(b'{"hi":1}', "application/json")]
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


def test_a_passport_is_announced_and_discovered_beside_quackds_own_record() -> None:
    record = passport_record(
        instance="duck-01",
        port=8790,
        short_id=KEY.short_id,
        digest="ef" * 32,
        host="lab-pc.local",
        addresses=("192.0.2.10",),
    )
    assert record.type == SERVICE_TYPE == "_robot-passport._tcp.local."
    registrar = FakeRegistrar()
    announcement = announce_passport(record, zc=registrar, info_factory=lambda r: r)
    assert registrar.registered == [record]
    found = discover_passports(
        1.0, browse=lambda _zc, kind, _t: registrar.registered if kind == SERVICE_TYPE else []
    )
    assert [(b.short_id, b.url()) for b in found] == [
        (KEY.short_id, f"http://192.0.2.10:8790{WELL_KNOWN_JSON}")
    ]
    announcement.close()
    assert registrar.registered == []


def test_a_beacon_on_no_port_is_refused_before_it_is_announced() -> None:
    with pytest.raises(ValueError):
        passport_record(instance="a", port=0, short_id=None, digest="0" * 64)
    assert beacon_from_info(object()) is None


# ── MQTT ────────────────────────────────────────────────────────────────────────────────


class FakeMessage:
    def __init__(self, topic: str, payload: bytes) -> None:
        self.topic, self.payload = topic, payload


class FakeBroker:
    """Keeps retained messages and hands them to a subscriber the moment it subscribes."""

    def __init__(self) -> None:
        self.retained: dict[str, bytes] = {}
        self.clients: list[FakeClient] = []


class FakeClient:
    def __init__(self, broker: FakeBroker) -> None:
        self.broker = broker
        self.calls: list[str] = []
        self.will: tuple[str, bytes, int, bool] | None = None
        self.on_message: Any = None

    def will_set(self, topic: str, payload: bytes, qos: int = 0, retain: bool = False) -> None:
        self.calls.append("will_set")
        self.will = (topic, payload, qos, retain)

    def connect(self, host: str, port: int = 1883, keepalive: int = 60) -> None:
        self.calls.append("connect")
        self.broker.clients.append(self)

    def loop_start(self) -> None:
        self.calls.append("loop_start")

    def loop_stop(self) -> None:
        self.calls.append("loop_stop")

    def disconnect(self) -> None:
        self.calls.append("disconnect")

    def publish(self, topic: str, payload: bytes, qos: int = 0, retain: bool = False) -> None:
        self.calls.append(f"publish {topic} {payload!r} {qos} {retain}")
        if retain:
            self.broker.retained[topic] = payload

    def subscribe(self, topic: str, qos: int = 0) -> None:
        if topic in self.broker.retained:
            self.on_message(self, None, FakeMessage(topic, self.broker.retained[topic]))


def test_a_published_passport_is_retained_with_a_last_will_set_before_connecting() -> None:
    broker = FakeBroker()
    client = FakeClient(broker)
    raw = _passport(KEY, "duck-01")
    publisher = PassportPublisher(KEY.short_id, raw, host="broker.local", client=client).start()
    passport_topic, status_topic, _inbox = topics(KEY.short_id)
    assert client.calls.index("will_set") < client.calls.index("connect")
    assert client.will == (status_topic, b"offline", 1, True)
    assert broker.retained == {passport_topic: raw, status_topic: b"online"}
    publisher.close()
    assert broker.retained[status_topic] == b"offline"
    assert read_passport("broker.local", KEY.short_id, client=FakeClient(broker)) == raw


def test_nothing_retained_is_a_refusal_not_a_hang() -> None:
    with pytest.raises(PassportError) as refused:
        read_passport("broker.local", "0" * 16, client=FakeClient(FakeBroker()), timeout_s=0.05)
    assert refused.value.rule == "5.4"


def test_a_robot_with_no_key_is_keyed_by_its_uuid() -> None:
    uuid = "urn:uuid:4F1C2A1E-9D4B-4C8E-8F3A-2B6D5E7A9C10"
    assert key_for(uuid, None) == "4f1c2a1e9d4b4c8e8f3a2b6d5e7a9c10"
    assert key_for(KEY.did, KEY.short_id) == KEY.short_id


# ── a QR sticker ────────────────────────────────────────────────────────────────────────


def test_a_sticker_round_trips_through_opencv(tmp_path: Path) -> None:
    text = sticker_url("https://example.org/robots/duck-01/robot-passport.json", KEY.short_id)
    modules, pixels = write_qr(text, tmp_path / "sticker.png")
    assert modules == 37, "a sticker URL this long fits a version 5 code, as discovery.md says"
    assert pixels == (37 + 8) * 10
    assert read_qr(tmp_path / "sticker.png") == text


def test_a_picture_with_no_code_is_refused(tmp_path: Path) -> None:
    import cv2
    import numpy as np

    blank = tmp_path / "blank.png"
    cv2.imwrite(str(blank), np.full((200, 200), 255, dtype=np.uint8))
    with pytest.raises(PassportError, match="no QR code"):
        read_qr(blank)
    with pytest.raises(PassportError):
        read_qr(tmp_path / "missing.png")


# ── ROS 2, through rosbridge ────────────────────────────────────────────────────────────


class FakeRos:
    def __init__(self) -> None:
        self.is_connected = False
        self.terminated = False

    def run(self, timeout: float = 5.0) -> None:
        self.is_connected = True

    def terminate(self) -> None:
        self.terminated = True


class FakeTopic:
    def __init__(self, name: str, message_type: str, data: str | None) -> None:
        self.name, self.message_type, self.data = name, message_type, data
        self.published: list[Any] = []
        self.unsubscribed = False

    def subscribe(self, callback: Any) -> None:
        if self.data is not None:
            callback({"data": self.data})

    def unsubscribe(self) -> None:
        self.unsubscribed = True

    def publish(self, message: Any) -> None:
        self.published.append(message)

    def unadvertise(self) -> None:
        pass


async def test_a_ros_robots_passport_is_read_without_publishing_a_thing() -> None:
    from quackd_rosbridge.ws import read_passport as read_ros

    ros = FakeRos()
    made: list[FakeTopic] = []

    def factory(_client: Any, name: str, message_type: str) -> FakeTopic:
        made.append(FakeTopic(name, message_type, '{"passport":"0.1"}'))
        return made[-1]

    text = await read_ros("ws://cart.local:9090", ros=ros, topic_factory=factory, timeout_s=0.5)
    assert text == '{"passport":"0.1"}'
    assert [(t.name, t.message_type) for t in made] == [("/robot_passport", "std_msgs/msg/String")]
    assert not made[0].published and made[0].unsubscribed and ros.terminated


async def test_a_ros_robot_with_no_passport_says_so() -> None:
    from quackd.transport.base import TransportError
    from quackd_rosbridge.ws import read_passport as read_ros

    with pytest.raises(TransportError, match="robot_passport"):
        await read_ros(
            "ws://cart.local:9090",
            ros=FakeRos(),
            topic_factory=lambda _c, n, t: FakeTopic(n, t, None),
            timeout_s=0.2,
        )


def test_a_beacons_url_prefers_ipv4_and_brackets_ipv6() -> None:
    def at(*addresses: str) -> str:
        return Beacon(
            instance="a",
            short_id=None,
            digest="0" * 16,
            path="/.well-known/robot-passport.json",
            host="h.local.",
            port=8790,
            addresses=addresses,
        ).url()

    assert at("fe80::1c2b:3aff:fe4d:5e6f", "192.0.2.10") == (
        "http://192.0.2.10:8790/.well-known/robot-passport.json"
    )
    assert at("2001:db8::5") == "http://[2001:db8::5]:8790/.well-known/robot-passport.json"
