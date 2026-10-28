"""The LAN half of meeting: a browse that keeps every robot's passport record and finds one by the
beacon id its advert carries, through rotations and departures. zeroconf is a stand-in here, as
in test_lan.py; the records are the ones `passport_record` registers."""

from __future__ import annotations

import enum
from typing import Any

import pytest

from quackd.lan.lookup import LanRecord
from quackd.lan.passports import PassportBrowser, passport_record, record_from_info
from quackd.passport.beacon import SERVICE_TYPE, new_beacon_id

SHORT_ID = "0123456789abcdef"


class Change(enum.Enum):
    Added = 1
    Removed = 2
    Updated = 3


class Browser:
    def __init__(self, handlers: list[Any]) -> None:
        self.handlers = handlers
        self.cancelled = False

    def cancel(self) -> None:
        self.cancelled = True


class Zc:
    def __init__(self) -> None:
        self.infos: dict[str, Any] = {}
        self.closed = False

    def get_service_info(self, service_type: str, name: str, timeout: int = 0) -> Any:
        info = self.infos.get(name)
        if isinstance(info, Exception):
            raise info
        return info

    def close(self) -> None:
        self.closed = True


class Zeroconf:
    """The three names of the zeroconf module the browser uses."""

    ServiceStateChange = Change

    def __init__(self) -> None:
        self.browsers: list[Browser] = []
        self.made: list[Zc] = []

    def ServiceBrowser(self, zc: Any, service_type: str, handlers: list[Any]) -> Browser:
        assert service_type == SERVICE_TYPE
        browser = Browser(handlers)
        self.browsers.append(browser)
        return browser

    def Zeroconf(self) -> Zc:
        self.made.append(Zc())
        return self.made[-1]


def announce(
    zc: Zc, name: str, beacon: str | None, *, address: str = "192.0.2.7", short_id: str = SHORT_ID
) -> str:
    record = passport_record(
        instance=name,
        port=8790,
        short_id=short_id,
        digest="ab" * 8,
        beacon=beacon,
        host="robot.local.",
        addresses=[address],
    )
    zc.infos[record.name] = record
    return record.name


def changed(module: Zeroconf, name: str, change: Change) -> None:
    for handler in module.browsers[-1].handlers:
        handler(zeroconf=None, service_type=SERVICE_TYPE, name=name, state_change=change)


def test_a_robot_announced_is_found_by_the_beacon_id_it_advertises() -> None:
    module, zc = Zeroconf(), Zc()
    browser = PassportBrowser(zc=zc, zeroconf=module).start()
    beacon = new_beacon_id()
    name = announce(zc, "courier", beacon)
    assert browser.lookup(beacon) is None
    changed(module, name, Change.Added)
    assert browser.lookup(beacon) == LanRecord("192.0.2.7", 8790, SHORT_ID, beacon)
    assert browser.lookup(new_beacon_id()) is None
    assert browser.records() == [LanRecord("192.0.2.7", 8790, SHORT_ID, beacon)]


def test_a_rotation_replaces_the_record_and_a_departure_drops_it() -> None:
    module, zc = Zeroconf(), Zc()
    browser = PassportBrowser(zc=zc, zeroconf=module).start()
    first, second = new_beacon_id(), new_beacon_id()
    name = announce(zc, "courier", first)
    changed(module, name, Change.Added)
    announce(zc, "courier", second)
    changed(module, name, Change.Updated)
    assert browser.lookup(first) is None
    found = browser.lookup(second)
    assert found is not None and found.short_id == SHORT_ID
    changed(module, name, Change.Removed)
    assert browser.lookup(second) is None and browser.records() == []


def test_a_record_with_no_beacon_id_or_that_will_not_resolve_is_no_robot_found() -> None:
    module, zc = Zeroconf(), Zc()
    browser = PassportBrowser(zc=zc, zeroconf=module).start()
    changed(module, announce(zc, "quiet", None), Change.Added)
    zc.infos["broken._robot-passport._tcp.local."] = OSError("no answer")
    changed(module, "broken._robot-passport._tcp.local.", Change.Added)
    changed(module, "gone._robot-passport._tcp.local.", Change.Added)
    assert browser.records() == []
    # a robot that stops carrying a beacon id loses the record it had
    beacon = new_beacon_id()
    name = announce(zc, "courier", beacon)
    changed(module, name, Change.Added)
    announce(zc, "courier", None)
    changed(module, name, Change.Updated)
    assert browser.lookup(beacon) is None


def test_an_ipv6_only_robot_is_reached_at_its_address_and_the_table_stays_bounded() -> None:
    module, zc = Zeroconf(), Zc()
    browser = PassportBrowser(zc=zc, zeroconf=module, max_records=3).start()
    beacon = new_beacon_id()
    changed(module, announce(zc, "six", beacon, address="2001:db8::7"), Change.Added)
    found = browser.lookup(beacon)
    assert found is not None and found.host == "2001:db8::7"
    for i in range(10):
        changed(
            module, announce(zc, f"robot{i}", new_beacon_id(), short_id=f"{i:016x}"), Change.Added
        )
    assert len(browser.records()) == 3


def test_closing_cancels_the_browse_and_closes_a_zeroconf_it_made() -> None:
    module = Zeroconf()
    browser = PassportBrowser(zeroconf=module).start()
    browser.close()
    assert module.browsers[-1].cancelled and module.made[-1].closed
    given = Zc()
    kept = PassportBrowser(zc=given, zeroconf=module).start()
    kept.close()
    assert not given.closed


def test_record_from_info_needs_the_version_two_fields() -> None:
    beacon = new_beacon_id()
    record = passport_record(
        instance="x",
        port=1,
        short_id=SHORT_ID,
        digest="ab" * 8,
        beacon=beacon,
        addresses=["10.0.0.9"],
    )
    assert record_from_info(record) == LanRecord("10.0.0.9", 1, SHORT_ID, beacon)
    old = passport_record(
        instance="x", port=1, short_id=SHORT_ID, digest="ab" * 8, addresses=["10.0.0.9"]
    )
    assert record_from_info(old) is None


def test_without_zeroconf_installed_the_browser_names_the_extra(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import sys

    from quackd.lan import LanNotInstalled

    monkeypatch.setitem(sys.modules, "zeroconf", None)
    with pytest.raises(LanNotInstalled):
        PassportBrowser().start()


def test_with_real_zeroconf_a_robot_announced_on_loopback_is_found_by_its_beacon_id() -> None:
    """The library itself, where the onboard extra installed it: one robot announced and found
    again on the loopback interface, with no network beyond this machine."""
    zeroconf = pytest.importorskip("zeroconf", reason="the onboard and lan extras install it")
    import time

    from quackd.lan.passports import announce_passport

    zc = zeroconf.Zeroconf(interfaces=["127.0.0.1"])
    browser = PassportBrowser(zc=zc).start()
    beacon = new_beacon_id()
    record = passport_record(
        instance="loopback-robot",
        port=8790,
        short_id=SHORT_ID,
        digest="ab" * 8,
        beacon=beacon,
        addresses=["127.0.0.1"],
    )
    announcement = announce_passport(record, zc=zc)
    try:
        deadline = time.monotonic() + 10
        while browser.lookup(beacon) is None and time.monotonic() < deadline:
            time.sleep(0.1)
        assert browser.lookup(beacon) == LanRecord("127.0.0.1", 8790, SHORT_ID, beacon)
    finally:
        announcement.close()
        browser.close()
        zc.close()
