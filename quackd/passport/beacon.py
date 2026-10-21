"""The beacon: a pointer and two fingerprints, carried by whatever a robot has (spec section 5).

Everything here is plain data with no network library, so the DNS-SD record and the sticker URL
are tested without zeroconf. A beacon proves nothing: anybody can claim any short id, and only
the passport it points to, and its signature, settle anything.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from quackd.passport.errors import PassportError

SERVICE_TYPE = "_robot-passport._tcp.local."
TXT_VERSION = "1"
#: Every pair under 200 bytes, the bound quackd's own `_quackd._tcp` record keeps (ADR-0021).
MAX_PAIR_BYTES = 200
WELL_KNOWN_JSON = "/.well-known/robot-passport.json"
WELL_KNOWN_JWS = "/.well-known/robot-passport.jws"
DIGEST_CHARS = 16
#: The ROS 2 topic a robot publishes its passport on, latched, in its own namespace (spec 5.5).
ROS_TOPIC = "/robot_passport"


def txt_record(*, short_id: str | None, digest: str, path: str = WELL_KNOWN_JSON) -> dict[str, str]:
    """The TXT record of spec 5.3: `v`, `id`, `sha` and `path`, every pair checked."""
    record = {"v": TXT_VERSION, "sha": digest[:DIGEST_CHARS]}
    if short_id:
        record["id"] = short_id
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

    def url(self) -> str:
        """The passport's URL at the first address the beacon came from (spec 5.7)."""
        where = self.addresses[0] if self.addresses else self.host.rstrip(".")
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
    )


def sticker_url(url: str, short_id: str | None) -> str:
    """The URL a QR sticker holds: the passport's, with `#rp=` and the short id (spec 5.2).

    The fragment never travels to the server, so a static host needs nothing configured, and a
    reader that finds it checks the passport it fetched has that short id."""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise PassportError("5.2", f"{url!r} is not an http or https URL")
    fragment = f"rp={short_id}" if short_id else ""
    return urlunsplit((parts.scheme, parts.netloc, parts.path or "/", parts.query, fragment))
