"""`roboglot announce --robot <spec>`: advertise a robot's identity on the LAN.

What goes on the wire is a `ServiceRecord` built here with no zeroconf import, so the
record is testable and a fake registrar can receive it. Only the last step (turning the
record into a `zeroconf.ServiceInfo` and registering it) touches the library, and both the
registrar and that step are injectable. Announcing holds no robot connection: it is a
static manifest's identity, which is all a TXT record carries (ADR-0021).
"""

from __future__ import annotations

import socket
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from roboglot.adapters.manifest import RobotManifest
from roboglot.lan import require_zeroconf
from roboglot.lan.txt import SERVICE_TYPE, instance_name, txt_record


@dataclass(frozen=True)
class ServiceRecord:
    """Everything a registration needs, in plain Python."""

    type: str
    name: str
    port: int
    properties: dict[str, str]
    server: str
    addresses: tuple[str, ...]


def local_ip() -> str:
    """The interface a LAN peer would see; a UDP connect sends nothing on the wire."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            return str(s.getsockname()[0])
    except OSError:
        return "127.0.0.1"


def service_record(
    manifest: RobotManifest,
    *,
    adapter: str,
    instance: str | None = None,
    port: int = 0,
    host: str | None = None,
    addresses: Sequence[str] = (),
) -> ServiceRecord:
    """`port=0` means "no service port": identity only, nothing listens."""
    server = host or f"{socket.gethostname().split('.')[0]}.local."
    if not server.endswith("."):
        server += "."
    return ServiceRecord(
        type=SERVICE_TYPE,
        name=instance_name(instance or manifest.id),
        port=port,
        properties=txt_record(manifest, adapter=adapter),
        server=server,
        addresses=tuple(addresses) or (local_ip(),),
    )


def _service_info(zeroconf: Any, record: ServiceRecord) -> Any:
    return zeroconf.ServiceInfo(
        record.type,
        record.name,
        addresses=[socket.inet_aton(a) for a in record.addresses],
        port=record.port,
        properties=dict(record.properties),
        server=record.server,
    )


def _register(zc: Any, info: Any, *, owns: bool) -> None:
    """`zc.register_service(info)`, closing `zc` when that fails and the announcing call made it.
    A name another host on the LAN already holds is enough to fail, and an instance left open
    keeps its multicast sockets and threads for as long as the process runs."""
    try:
        zc.register_service(info)
    except BaseException:
        if owns:
            zc.close()
        raise


@dataclass
class Announcement:
    record: ServiceRecord
    info: Any
    zc: Any
    owns_zc: bool

    def close(self) -> None:
        self.zc.unregister_service(self.info)
        if self.owns_zc:
            self.zc.close()


def announce(
    manifest: RobotManifest,
    *,
    adapter: str,
    instance: str | None = None,
    port: int = 0,
    host: str | None = None,
    addresses: Sequence[str] = (),
    zc: Any = None,
    info_factory: Callable[[ServiceRecord], Any] | None = None,
) -> Announcement:
    """Register the robot; `close()` the result to withdraw it.

    `zc` is a `zeroconf.Zeroconf` (one is created and owned when omitted) or a fake with
    `register_service`/`unregister_service`; `info_factory` turns the record into what
    that registrar accepts (the real `ServiceInfo` by default)."""
    record = service_record(
        manifest, adapter=adapter, instance=instance, port=port, host=host, addresses=addresses
    )
    owns = False
    if info_factory is None:
        zeroconf = require_zeroconf("roboglot announce")
        info = _service_info(zeroconf, record)
        if zc is None:
            zc = zeroconf.Zeroconf()
            owns = True
    else:
        if zc is None:
            raise ValueError("info_factory needs a registrar: pass zc=")
        info = info_factory(record)
    _register(zc, info, owns=owns)
    return Announcement(record=record, info=info, zc=zc, owns_zc=owns)


__all__ = ["Announcement", "ServiceRecord", "announce", "local_ip", "service_record"]
