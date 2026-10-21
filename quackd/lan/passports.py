"""Robot Passport beacons on a LAN: `_robot-passport._tcp.local.`, beside `_quackd._tcp`.

The quackd record tells other quackd instances which manifest a host fronts; this one tells
anything at all that a robot with a passport is here, and on which port it serves it
(docs/passport/discovery.md). Both registrations go through the same plumbing as
`announce.py` and `discover.py`, so the same fakes stand in for zeroconf in the tests, and the
TXT record itself is `quackd.passport.beacon`'s, which has no network library in it.
"""

from __future__ import annotations

import socket
from collections.abc import Callable, Sequence
from typing import Any

from quackd.lan import require_zeroconf
from quackd.lan.announce import Announcement, ServiceRecord, _service_info, local_ip
from quackd.lan.discover import Browse, _browse_zeroconf
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
    short_id: str | None,
    digest: str,
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
        properties=txt_record(short_id=short_id, digest=digest, path=path),
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


__all__ = ["announce_passport", "beacon_from_info", "discover_passports", "passport_record"]
