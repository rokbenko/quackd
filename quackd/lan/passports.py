"""Robot Passport beacons on a LAN: `_robot-passport._tcp.local.`, beside `_quackd._tcp`.

The quackd record tells other quackd instances which manifest a host fronts; this one tells
anything at all that a robot with a passport is here, and on which port it serves it
(docs/passport/discovery.md). Both registrations go through the same plumbing as
`announce.py` and `discover.py`, so the same fakes stand in for zeroconf in the tests, and the
TXT record itself is `quackd.passport.beacon`'s, which has no network library in it.
"""

from __future__ import annotations

import socket
import threading
from collections.abc import Callable, Sequence
from typing import Any
from urllib.parse import urlsplit

from quackd.lan import require_zeroconf
from quackd.lan.announce import Announcement, ServiceRecord, _service_info, local_ip
from quackd.lan.discover import Browse, _browse_zeroconf
from quackd.lan.lookup import LanDirectory, LanRecord
from quackd.passport.beacon import (
    SERVICE_TYPE,
    WELL_KNOWN_JSON,
    Beacon,
    beacon_from_txt,
    parse_txt,
    txt_record,
)

#: What DNS-SD allows an instance label to be, in bytes.
_LABEL_BYTES = 63


def passport_record(
    *,
    instance: str,
    port: int,
    short_id: str,
    digest: str,
    beacon: str | None = None,
    path: str = WELL_KNOWN_JSON,
    host: str | None = None,
    addresses: Sequence[str] = (),
) -> ServiceRecord:
    """The registration for one passport served on `port`. The instance name is a label a
    reader never trusts (spec 5.3), so it is the robot's name cut to what DNS-SD allows."""
    if port <= 0:
        raise ValueError("a passport beacon names the port that serves it")
    server = host or f"{socket.gethostname().split('.')[0]}.local."
    if not server.endswith("."):
        server += "."
    label = instance.encode("utf-8")[:_LABEL_BYTES].decode("utf-8", "ignore") or "robot"
    return ServiceRecord(
        type=SERVICE_TYPE,
        name=f"{label}.{SERVICE_TYPE}",
        port=port,
        properties=txt_record(short_id=short_id, digest=digest, beacon=beacon, path=path),
        server=server,
        addresses=tuple(addresses) or (local_ip(),),
    )


def announce_passport(
    record: ServiceRecord,
    *,
    zc: Any = None,
    info_factory: Callable[[ServiceRecord], Any] | None = None,
) -> Announcement:
    """Register `record`; `close()` the result to withdraw it. Fakes as `lan.announce` takes."""
    owns = False
    if info_factory is None:
        zeroconf = require_zeroconf("advertising a robot passport")
        info = _service_info(zeroconf, record)
        if zc is None:
            zc = zeroconf.Zeroconf()
            owns = True
    else:
        if zc is None:
            raise ValueError("info_factory needs a registrar: pass zc=")
        info = info_factory(record)
    zc.register_service(info)
    return Announcement(record=record, info=info, zc=zc, owns_zc=owns)


def beacon_from_info(info: Any) -> Beacon | None:
    """A `zeroconf.ServiceInfo`, or anything shaped like one (a `ServiceRecord` is)."""
    txt = parse_txt(getattr(info, "properties", None) or {})
    parsed = getattr(info, "parsed_addresses", None)
    addresses = tuple(parsed()) if callable(parsed) else tuple(getattr(info, "addresses", ()))
    return beacon_from_txt(
        txt,
        instance=str(getattr(info, "name", "")),
        host=str(getattr(info, "server", "") or ""),
        port=int(getattr(info, "port", 0) or 0),
        addresses=tuple(str(a) for a in addresses),
    )


def discover_passports(
    timeout_s: float = 3.0, *, zc: Any = None, browse: Browse | None = None
) -> list[Beacon]:
    """Every passport beacon answering within `timeout_s`, sorted by instance name."""
    if browse is None:
        zeroconf = require_zeroconf("finding robot passports")
        owns = zc is None
        zc = zc or zeroconf.Zeroconf()
        try:
            infos = _browse_zeroconf(zeroconf, zc, timeout_s, SERVICE_TYPE)
        finally:
            if owns:
                zc.close()
    else:
        infos = list(browse(zc, SERVICE_TYPE, timeout_s))
    beacons = [b for b in (beacon_from_info(i) for i in infos) if b is not None]
    return sorted(beacons, key=lambda b: b.instance)


# ── the LAN half of meeting: every robot's record, kept, found by beacon id ───────────────────


class PassportBrowser:
    """Every passport record on this LAN, kept current by a DNS-SD browse that runs as long as
    the robot's service does: a record added when a robot announces itself, replaced when it
    re-announces with a rotated beacon id, and dropped when it withdraws or its TTL runs out.
    `lookup` finds the robot whose record says it advertises a beacon id: a `LanDirectory`.

    The browse is zeroconf's own threads; nothing here waits on the network to answer a
    lookup, which only reads what the browse has found so far. A record names a beacon id the
    robot claims, which the hello then has to bear out, so a record that lies gets a hello
    refused and nothing else. `zeroconf` and `zc` stand in for the library in tests."""

    def __init__(
        self,
        *,
        zc: Any = None,
        zeroconf: Any = None,
        resolve_ms: int = 3000,
        max_records: int = 256,
    ) -> None:
        self._zeroconf, self._zc, self._owns = zeroconf, zc, False
        self.resolve_ms = resolve_ms
        self.max_records = max_records
        self._records: dict[str, LanRecord] = {}
        self._lock = threading.Lock()
        self._browser: Any = None

    def start(self) -> PassportBrowser:
        module = self._zeroconf or require_zeroconf("finding robots' passports on the LAN")
        self._zeroconf = module
        if self._zc is None:
            self._zc, self._owns = module.Zeroconf(), True
        self._browser = module.ServiceBrowser(self._zc, SERVICE_TYPE, handlers=[self._on_change])
        return self

    def _on_change(
        self, zeroconf: Any = None, service_type: str = "", name: str = "", state_change: Any = None
    ) -> None:
        changes = self._zeroconf.ServiceStateChange
        if state_change == changes.Removed:
            with self._lock:
                self._records.pop(name, None)
            return
        if state_change not in (changes.Added, changes.Updated):
            return
        try:
            info = self._zc.get_service_info(
                service_type or SERVICE_TYPE, name, timeout=self.resolve_ms
            )
        except Exception:  # one record that would not resolve is one robot not found over the LAN
            info = None
        record = record_from_info(info) if info is not None else None
        with self._lock:
            if record is None:
                self._records.pop(name, None)
            elif name in self._records or len(self._records) < self.max_records:
                self._records[name] = record

    def lookup(self, beacon_id: str) -> LanRecord | None:
        with self._lock:
            return next((r for r in self._records.values() if r.beacon_id == beacon_id), None)

    def records(self) -> list[LanRecord]:
        with self._lock:
            return list(self._records.values())

    def close(self) -> None:
        if self._browser is not None:
            self._browser.cancel()
            self._browser = None
        if self._owns and self._zc is not None:
            self._zc.close()
            self._zc, self._owns = None, False


def record_from_info(info: Any) -> LanRecord | None:
    """The LAN record a resolved service names, or None for one that is not a version-2
    passport record carrying a beacon id: a robot that does not say which advert is its own
    cannot be told apart from any other on the LAN."""
    beacon = beacon_from_info(info)
    if beacon is None or not beacon.beacon_id or not beacon.short_id:
        return None
    host = urlsplit(beacon.url()).hostname
    if not host:
        return None
    return LanRecord(
        host, beacon.port, beacon.short_id, beacon.beacon_id, (host, *beacon.addresses)
    )


__all__ = [
    "LanDirectory",
    "LanRecord",
    "PassportBrowser",
    "announce_passport",
    "beacon_from_info",
    "discover_passports",
    "passport_record",
    "record_from_info",
]
