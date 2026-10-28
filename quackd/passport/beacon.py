"""The beacon: a pointer and two fingerprints, carried by whatever a robot has (spec section 5).

Everything here is plain data with no network library, so the DNS-SD record is tested without
zeroconf. A beacon proves nothing: anybody can claim any short id or beacon id, and only the
passport a hello carries, and the signatures on it and on the hello, settle anything.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass
from typing import Any

from quackd.passport.errors import PassportError

SERVICE_TYPE = "_robot-passport._tcp.local."
TXT_VERSION = "2"
#: Every pair under 200 bytes, the bound quackd's own `_quackd._tcp` record keeps (ADR-0021).
MAX_PAIR_BYTES = 200
WELL_KNOWN_JSON = "/.well-known/robot-passport.json"
WELL_KNOWN_JWS = "/.well-known/robot-passport.jws"
DIGEST_CHARS = 16


def txt_record(
    *, short_id: str, digest: str, beacon: str | None = None, path: str = WELL_KNOWN_JSON
) -> dict[str, str]:
    """The TXT record of spec 5.3: `v`, `id`, `sha`, the beacon id `bt` the robot's radio
    advertises now, which rotates, and `path`, every pair checked."""
    record = {"v": TXT_VERSION, "id": short_id, "sha": digest[:DIGEST_CHARS]}
    if beacon:
        record["bt"] = beacon
    if path != WELL_KNOWN_JSON:
        record["path"] = path
    for key, value in record.items():
        if len(f"{key}={value}".encode()) >= MAX_PAIR_BYTES:
            raise PassportError("5.3", f"the TXT pair {key} is {MAX_PAIR_BYTES} bytes or more")
    return record


def parse_txt(properties: Mapping[Any, Any]) -> dict[str, str]:
    """zeroconf hands back bytes keys and bytes-or-None values; plain strings are fine too."""
    out: dict[str, str] = {}
    for raw_key, raw_value in properties.items():
        if raw_value is None:
            continue
        key = raw_key.decode("utf-8", "replace") if isinstance(raw_key, bytes) else str(raw_key)
        value = (
            raw_value.decode("utf-8", "replace") if isinstance(raw_value, bytes) else str(raw_value)
        )
        out[key] = value
    return out


@dataclass(frozen=True)
class Beacon:
    """One robot announcing a passport: where it is, and the fingerprints it claims."""

    instance: str
    short_id: str | None
    digest: str
    path: str
    host: str
    port: int
    addresses: tuple[str, ...]
    beacon_id: str | None = None

    def url(self) -> str:
        """The passport's URL at an address the beacon came from (spec 5.7): an IPv4 one when
        there is one, since an IPv6 link-local address is reached only through an interface,
        and an IPv6 one in brackets, the only way a URL can carry it."""
        v4 = [a for a in self.addresses if ":" not in a]
        where = (v4 or list(self.addresses) or [self.host.rstrip(".")])[0]
        if ":" in where:
            where = f"[{where}]"
        return f"http://{where}:{self.port}{self.path}"

    def row(self) -> dict[str, Any]:
        return {"kind": "passport", **asdict(self), "url": self.url()}


def beacon_from_txt(
    txt: Mapping[str, str], *, instance: str, host: str, port: int, addresses: tuple[str, ...]
) -> Beacon | None:
    """A record of another version, with no digest, or on no port is not a passport beacon."""
    if txt.get("v") != TXT_VERSION or not txt.get("sha") or port <= 0:
        return None
    path = txt.get("path") or WELL_KNOWN_JSON
    if not path.startswith("/"):
        return None
    return Beacon(
        instance=instance,
        short_id=txt.get("id") or None,
        digest=txt["sha"],
        path=path,
        host=host,
        port=port,
        addresses=addresses,
        beacon_id=txt.get("bt") or None,
    )
