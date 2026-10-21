"""LAN discovery on fakes: the TXT wire format, announce, discover. No sockets, no extra."""

from __future__ import annotations

import importlib.util
import json
from typing import Any

import pytest
from typer.testing import CliRunner

from quackd.adapters.factory import RobotSpec, describe
from quackd.adapters.manifest import RobotManifest, VerbSpec
from quackd.cli import app
from quackd.lan import LanNotInstalled
from quackd.lan import announce as lan_announce
from quackd.lan import discover as lan_discover
from quackd.lan import passports as lan_passports
from quackd.lan.announce import ServiceRecord, announce, service_record
from quackd.lan.discover import discover, robot_from_info
from quackd.lan.txt import (
    KEYS,
    MAX_PAIR_BYTES,
    SERVICE_TYPE,
    TxtError,
    check_pair,
    instance_name,
    parse_txt,
    robot_from_txt,
    txt_record,
)

DUCK = describe(RobotSpec("microduck", "mock", "duck-01"))
# A hand-rolled, deliberately different-capability manifest: LAN identity/discovery is
# adapter-agnostic, so a second real robot isn't needed, just a second, distinct one.
HEAD = RobotManifest(
    id="head-01",
    vendor="acme",
    model="fixed-cam",
    embodiment="arm",
    mobility="none",
    intents=["gaze", "sound"],
    sensors=["camera"],
    verbs=[
        VerbSpec(name="observe", core=True),
        VerbSpec(name="report_state", core=True),
        VerbSpec(name="say", core=True),
    ],
)


class FakeZeroconf:
    """Records registrations; `discover(browse=...)` reads them back."""

    def __init__(self) -> None:
        self.registered: list[ServiceRecord] = []
        self.closed = False

    def register_service(self, info: ServiceRecord) -> None:
        self.registered.append(info)

    def unregister_service(self, info: ServiceRecord) -> None:
        self.registered.remove(info)

    def close(self) -> None:
        self.closed = True


def browse(zc: FakeZeroconf, service_type: str, _timeout_s: float) -> list[Any]:
    return [r for r in zc.registered if r.type == service_type]


# ── txt ─────────────────────────────────────────────────────────────────────────────


def test_txt_record_is_identity_only_and_validated() -> None:
    txt = txt_record(DUCK, adapter="microduck")
    assert tuple(txt) == KEYS
    assert txt["v"] == "1" and txt["mid"] == "duck-01" and txt["sha"] == DUCK.digest()
    assert txt["adp"] == "microduck" and txt["mdl"] == "microduck" and txt["emb"] == "biped"
    assert txt["nverbs"] == str(len(DUCK.verbs))
    for key, value in txt.items():
        assert len(f"{key}={value}".encode()) < MAX_PAIR_BYTES


def test_pairs_over_the_limit_are_refused_before_zeroconf_sees_them() -> None:
    check_pair("mid", "x" * 150)
    with pytest.raises(TxtError, match="200"):
        check_pair("mid", "x" * MAX_PAIR_BYTES)
    with pytest.raises(TxtError, match="'='"):
        check_pair("a=b", "x")
    long_id = DUCK.model_copy(update={"id": "d" * 64})  # the id validator allows 64
    txt_record(long_id, adapter="microduck")  # still well under 200 bytes


def test_parse_txt_takes_bytes_and_drops_valueless_keys() -> None:
    parsed = parse_txt({b"v": b"1", b"mid": b"duck-01", b"flag": None, "sha": "abc"})
    assert parsed == {"v": "1", "mid": "duck-01", "sha": "abc"}


def test_robot_from_txt_rejects_other_versions_and_missing_identity() -> None:
    good = txt_record(HEAD, adapter="head")
    robot = robot_from_txt(good, instance="x", host="h.local.", port=0, addresses=("10.0.0.2",))
    assert robot is not None and robot.manifest_id == "head-01" and robot.matches(HEAD)
    assert not robot.matches(DUCK) and robot.n_verbs == len(HEAD.verbs)
    for broken in ({**good, "v": "2"}, {k: v for k, v in good.items() if k != "mid"}):
        assert robot_from_txt(broken, instance="x", host="h", port=0, addresses=()) is None
    odd = robot_from_txt({**good, "nverbs": "many"}, instance="x", host="h", port=0, addresses=())
    assert odd is not None and odd.n_verbs == 0
    assert instance_name("duck-01") == "duck-01._quackd._tcp.local."
    assert SERVICE_TYPE == "_quackd._tcp.local."


# ── announce / discover on fakes ───────────────────────────────────────────────────


def test_announce_registers_a_record_and_close_withdraws_it() -> None:
    zc = FakeZeroconf()
    ann = announce(
        DUCK, adapter="microduck", zc=zc, info_factory=lambda r: r, addresses=["10.0.0.7"]
    )
    assert zc.registered == [ann.record]
    assert ann.record.name == "duck-01._quackd._tcp.local." and ann.record.port == 0
    assert ann.record.addresses == ("10.0.0.7",) and ann.record.server.endswith(".local.")
    assert ann.record.properties == txt_record(DUCK, adapter="microduck")
    ann.close()
    assert zc.registered == [] and zc.closed is False  # we did not own the registrar


def test_discover_reads_back_what_was_announced_sorted_by_name() -> None:
    zc = FakeZeroconf()
    announce(HEAD, adapter="head", zc=zc, info_factory=lambda r: r, addresses=["10.0.0.2"])
    announce(DUCK, adapter="microduck", zc=zc, info_factory=lambda r: r, addresses=["10.0.0.3"])
    zc.register_service(  # a stranger on the same service type, another record version
        ServiceRecord(SERVICE_TYPE, "x." + SERVICE_TYPE, 0, {"v": "9", "mid": "x"}, "s.", ())
    )
    robots = discover(0.0, zc=zc, browse=browse)
    assert [r.manifest_id for r in robots] == ["duck-01", "head-01"]
    assert robots[1].matches(HEAD) and robots[1].addresses == ("10.0.0.2",)
    assert robots[0].row()["adapter"] == "microduck"
    assert robots[1].host.endswith(".local.") and robots[1].port == 0


def test_robot_from_info_accepts_zeroconf_style_objects() -> None:
    class Info:
        name = "duck-01._quackd._tcp.local."
        server = "desk.local."
        port = 0
        properties = {
            k.encode(): v.encode() for k, v in txt_record(DUCK, adapter="microduck").items()
        }

        def parsed_addresses(self) -> list[str]:
            return ["192.168.1.5"]

    robot = robot_from_info(Info())
    assert robot is not None and robot.addresses == ("192.168.1.5",) and robot.matches(DUCK)


def test_service_record_needs_a_registrar_with_a_factory() -> None:
    with pytest.raises(ValueError, match="zc="):
        announce(DUCK, adapter="microduck", info_factory=lambda r: r)
    rec = service_record(DUCK, adapter="microduck", host="desk", addresses=["1.2.3.4"])
    assert rec.server == "desk." and rec.addresses == ("1.2.3.4",)


# ── the commands, on the same fakes ────────────────────────────────────────────────


@pytest.fixture
def fake_lan(monkeypatch: pytest.MonkeyPatch) -> FakeZeroconf:
    zc = FakeZeroconf()

    def fake_discover(timeout_s: float = 3.0, **_: Any) -> list[Any]:
        return discover(timeout_s, zc=zc, browse=browse)

    def fake_announce(manifest: Any, **kw: Any) -> Any:
        return announce(
            manifest,
            adapter=kw["adapter"],
            port=kw.get("port", 0),
            zc=zc,
            info_factory=lambda r: r,
            addresses=["10.0.0.9"],
        )

    # `quackd discover` browses Robot Passport beacons too, on the same fake registrar
    real_discover_passports = lan_passports.discover_passports

    def fake_discover_passports(timeout_s: float = 3.0, **_: Any) -> list[Any]:
        return real_discover_passports(timeout_s, zc=zc, browse=browse)

    monkeypatch.setattr(lan_discover, "discover", fake_discover)
    monkeypatch.setattr(lan_announce, "announce", fake_announce)
    monkeypatch.setattr(lan_passports, "discover_passports", fake_discover_passports)
    return zc


def test_cli_discover_prints_a_table_or_json(fake_lan: FakeZeroconf) -> None:
    runner = CliRunner()
    empty = runner.invoke(app, ["discover", "--timeout", "0"])
    assert empty.exit_code == 0 and "no quackd robots answered" in empty.output
    announce(HEAD, adapter="head", zc=fake_lan, info_factory=lambda r: r, addresses=["10.0.0.2"])
    table = runner.invoke(app, ["discover", "--timeout", "0"])
    assert table.exit_code == 0, table.output
    # the rich table wraps in an 80-column terminal; the exact fields are checked as JSON
    assert "quackd robots on the LAN (1)" in table.output and "head-01" in table.output
    as_json = runner.invoke(app, ["discover", "--timeout", "0", "--json"])
    rows = [json.loads(line) for line in as_json.output.splitlines() if line.startswith("{")]
    assert len(rows) == 1 and rows[0]["manifest_id"] == "head-01"
    assert rows[0]["adapter"] == "head" and rows[0]["embodiment"] == "arm"
    assert rows[0]["addresses"] == ["10.0.0.2"] and rows[0]["digest"] == HEAD.digest()


def test_cli_announce_advertises_a_static_manifest_then_withdraws(fake_lan: FakeZeroconf) -> None:
    runner = CliRunner()
    res = runner.invoke(
        app, ["announce", "--robot", "microduck:mock", "--name", "duck-7", "--for", "0"]
    )
    assert res.exit_code == 0, res.output
    assert "duck-7._quackd._tcp.local." in res.output and "10.0.0.9" in res.output
    assert "withdrawn" in res.output and fake_lan.registered == []  # closed on the way out
    bad = runner.invoke(app, ["announce", "--robot", "nope:x", "--for", "0"])
    assert bad.exit_code == 1 and "unknown adapter" in bad.output


@pytest.mark.skipif(
    importlib.util.find_spec("zeroconf") is not None, reason="zeroconf is installed here"
)
def test_without_the_extra_the_message_names_it() -> None:
    with pytest.raises(LanNotInstalled, match=r"quackd\[lan\]"):
        announce(DUCK, adapter="microduck")
    with pytest.raises(LanNotInstalled, match=r"quackd\[lan\]"):
        discover(0.0)
