"""`quackd meet`, `quackd passport serve`, `quackd passport qr` and `quackd discover`'s
passports: the commands a person meets another robot with."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from quackd.cli import app
from quackd.passport import SigningKey, strict
from quackd.passport.beacon import Beacon, sticker_url
from quackd.passport.serve import PassportServer, Served
from quackd.passport_qr import write_qr

runner = CliRunner()
KEY = SigningKey(bytes(range(32)))
OTHER = SigningKey(bytes(range(64, 96)))


def _passport(key: SigningKey, name: str) -> bytes:
    return strict.dumps({"passport": "0.1", "id": key.did, "name": name})


@pytest.fixture
def served() -> Any:
    server = PassportServer([Served("duck-01", _passport(KEY, "duck-01"))], host="127.0.0.1")
    server.start()
    yield server
    server.close()


def _flat(output: str) -> str:
    return " ".join(output.split())


def test_meet_reads_a_url_and_a_bare_host(served: PassportServer) -> None:
    by_url = runner.invoke(app, ["meet", f"http://127.0.0.1:{served.port}"])
    assert by_url.exit_code == 0, by_url.output
    assert "met duck-01" in by_url.output and "unsigned: a claim" in by_url.output
    by_host = runner.invoke(app, ["meet", f"127.0.0.1:{served.port}", "--json"])
    assert json.loads(by_host.stdout)["id"] == KEY.did


def test_meet_reads_a_picture_of_a_sticker_and_checks_its_short_id(
    served: PassportServer, tmp_path: Path
) -> None:
    url = f"http://127.0.0.1:{served.port}/.well-known/robot-passport.json"
    write_qr(sticker_url(url, KEY.short_id), tmp_path / "good.png")
    good = runner.invoke(app, ["meet", str(tmp_path / "good.png")])
    assert good.exit_code == 0, good.output
    write_qr(sticker_url(url, OTHER.short_id), tmp_path / "swapped.png")
    swapped = runner.invoke(app, ["meet", str(tmp_path / "swapped.png")])
    assert swapped.exit_code == 1
    assert "refused under 5.2" in _flat(swapped.output)


def test_meet_lists_an_index_rather_than_guessing() -> None:
    robots = [
        Served("duck-01", _passport(KEY, "duck-01")),
        Served("cart-01", _passport(OTHER, "cart-01")),
    ]
    server = PassportServer(robots, host="127.0.0.1").start()
    try:
        listed = runner.invoke(app, ["meet", f"127.0.0.1:{server.port}"])
    finally:
        server.close()
    assert listed.exit_code == 0, listed.output
    assert "serves 2 passports" in listed.output and "cart-01" in listed.output


def test_meet_reads_mqtt_and_ros_through_their_bindings(monkeypatch: pytest.MonkeyPatch) -> None:
    import quackd.lan.passport_mqtt as mqtt
    import quackd_rosbridge.ws as ws

    asked: list[Any] = []

    def from_broker(host: str, key: str, *, port: int = 1883, **_kw: Any) -> bytes:
        asked.append((host, key, port))
        return _passport(KEY, "duck-01")

    async def from_ros(address: str, **_kw: Any) -> str:
        asked.append(address)
        return _passport(OTHER, "cart-01").decode()

    monkeypatch.setattr(mqtt, "read_passport", from_broker)
    monkeypatch.setattr(ws, "read_passport", from_ros)
    by_mqtt = runner.invoke(app, ["meet", f"mqtt://broker.local:1884/{KEY.short_id}"])
    assert by_mqtt.exit_code == 0, by_mqtt.output
    by_ros = runner.invoke(app, ["meet", "ros://cart.local:9090"])
    assert by_ros.exit_code == 0, by_ros.output
    assert asked == [("broker.local", KEY.short_id, 1884), "ws://cart.local:9090"]
    assert "met cart-01" in by_ros.output


def test_meet_says_when_nothing_answers() -> None:
    nothing = runner.invoke(app, ["meet", "127.0.0.1:9"])
    assert nothing.exit_code == 1
    assert "could not be reached" in _flat(nothing.output)


def test_serve_says_where_each_passport_is_and_stops(tmp_path: Path) -> None:
    served = runner.invoke(
        app,
        [
            "passport",
            "serve",
            "--robot",
            "microduck:mock",
            "--robot",
            "lerobot:mock",
            "--bind",
            "127.0.0.1",
            "--port",
            "0",
            "--for",
            "0.1",
            "--registry-dir",
            str(tmp_path),
        ],
    )
    assert served.exit_code == 0, served.output
    flat = _flat(served.output)
    assert "serving 2 passports" in flat
    # the panel wraps a long URL inside its border, so the names are what is checked
    assert "microduck-mock" in flat and "lerobot-mock" in flat and "index" in flat
    assert "stopped" in flat


def test_serve_announces_and_retains_when_asked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import quackd.lan.passport_mqtt as mqtt
    import quackd.lan.passports as passports

    announced: list[Any] = []
    retained: list[Any] = []

    class Closer:
        def close(self) -> None:
            pass

    class Publisher:
        def __init__(self, key: str, payload: bytes, *, host: str, port: int = 1883) -> None:
            from quackd.passport import document

            # what goes on a broker is the signed passport, so a subscriber can verify it
            retained.append((key, host, port, document.read_jws(payload.decode()).name))

        def start(self) -> Closer:
            return Closer()

    monkeypatch.setattr(
        passports, "announce_passport", lambda record: announced.append(record) or Closer()
    )
    monkeypatch.setattr(mqtt, "PassportPublisher", Publisher)
    served = runner.invoke(
        app,
        [
            "passport",
            "serve",
            "--robot",
            "microduck:mock",
            "--bind",
            "127.0.0.1",
            "--port",
            "0",
            "--announce",
            "--mqtt",
            "broker.local:1884",
            "--for",
            "0.1",
            "--registry-dir",
            str(tmp_path),
        ],
    )
    assert served.exit_code == 0, served.output
    assert [r.properties["v"] for r in announced] == ["1"]
    assert announced[0].port > 0
    assert retained == [(retained[0][0], "broker.local", 1884, "microduck-mock")]


def test_qr_writes_a_sticker_that_leads_back_to_the_robot(tmp_path: Path) -> None:
    from quackd.passport_qr import read_qr
    from quackd.passport_store import PassportStore

    out = tmp_path / "duck.png"
    made = runner.invoke(
        app,
        [
            "passport",
            "qr",
            "--robot",
            "microduck:mock",
            "--url",
            "https://example.org/duck/robot-passport.json",
            "--out",
            str(out),
            "--registry-dir",
            str(tmp_path),
        ],
    )
    assert made.exit_code == 0, made.output
    key = PassportStore(tmp_path).robot("microduck-mock")
    assert key is not None
    assert read_qr(out) == f"https://example.org/duck/robot-passport.json#rp={key.key.short_id}"


def test_discover_lists_passports_beside_quackd_robots(monkeypatch: pytest.MonkeyPatch) -> None:
    import quackd.lan.discover as lan_discover
    import quackd.lan.passports as passports

    beacon = Beacon(
        instance="cart-01._robot-passport._tcp.local.",
        short_id=OTHER.short_id,
        digest="ab" * 8,
        path="/.well-known/robot-passport.json",
        host="cart.local.",
        port=8790,
        addresses=("192.0.2.20",),
    )
    monkeypatch.setattr(lan_discover, "discover", lambda timeout: [])
    monkeypatch.setattr(passports, "discover_passports", lambda timeout: [beacon])
    shown = runner.invoke(app, ["discover", "--timeout", "0.1"])
    assert shown.exit_code == 0, shown.output
    assert "robot passports on the LAN (1)" in shown.output and "cart-01" in shown.output
    as_json = runner.invoke(app, ["discover", "--timeout", "0.1", "--json"])
    row = json.loads(as_json.stdout)
    assert (
        row["kind"] == "passport"
        and row["url"] == "http://192.0.2.20:8790/.well-known/robot-passport.json"
    )
