"""`quackd discover`: list the quackd robots answering on the LAN.

Browsing is the one step that needs zeroconf, and it is injectable (`browse=`), so the
tests hand in records from a fake registrar and the default install never imports the
library. What comes back is identity only (`DiscoveredRobot`); a manifest is fetched out of
band and verified with `matches()` (ADR-0021).
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from typing import Any

from quackd.lan import require_zeroconf
from quackd.lan.txt import SERVICE_TYPE, DiscoveredRobot, parse_txt, robot_from_txt

Browse = Callable[[Any, str, float], Sequence[Any]]
"""`browse(zc, service_type, timeout_s)` -> info-like objects (`name`, `server`, `port`,
`properties`, and `parsed_addresses()` or `addresses`)."""


def robot_from_info(info: Any) -> DiscoveredRobot | None:
    """A `zeroconf.ServiceInfo`, or anything shaped like one (a `ServiceRecord` is)."""
    txt = parse_txt(getattr(info, "properties", None) or {})
    parsed = getattr(info, "parsed_addresses", None)
    addresses = tuple(parsed()) if callable(parsed) else tuple(getattr(info, "addresses", ()))
    return robot_from_txt(
        txt,
        instance=str(getattr(info, "name", "")),
        host=str(getattr(info, "server", "") or ""),
        port=int(getattr(info, "port", 0) or 0),
        addresses=tuple(str(a) for a in addresses),
    )


def _browse_zeroconf(
    zeroconf: Any, zc: Any, timeout_s: float, service_type: str = SERVICE_TYPE
) -> list[Any]:
    """Collect every instance of `service_type`, `_quackd._tcp.local.` unless told otherwise,
    seen within the window."""
    found: dict[str, Any] = {}
    wanted = {zeroconf.ServiceStateChange.Added, zeroconf.ServiceStateChange.Updated}

    def on_change(
        zeroconf: Any = None, service_type: str = "", name: str = "", state_change: Any = None
    ) -> None:
        if state_change in wanted:
            info = zc.get_service_info(service_type, name, timeout=int(timeout_s * 1000))
            if info is not None:
                found[name] = info

    browser = zeroconf.ServiceBrowser(zc, service_type, handlers=[on_change])
    try:
        time.sleep(timeout_s)
    finally:
        browser.cancel()
    return list(found.values())


def discover(
    timeout_s: float = 3.0, *, zc: Any = None, browse: Browse | None = None
) -> list[DiscoveredRobot]:
    """Every quackd robot answering within `timeout_s`, sorted by instance name."""
    if browse is None:
        zeroconf = require_zeroconf("quackd discover")
        owns = zc is None
        zc = zc or zeroconf.Zeroconf()
        try:
            infos = _browse_zeroconf(zeroconf, zc, timeout_s)
        finally:
            if owns:
                zc.close()
    else:
        infos = list(browse(zc, SERVICE_TYPE, timeout_s))
    robots = [r for r in (robot_from_info(i) for i in infos) if r is not None]
    return sorted(robots, key=lambda r: r.instance)


__all__ = ["Browse", "discover", "robot_from_info"]
