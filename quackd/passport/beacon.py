"""The beacon: a pointer and two fingerprints, carried by whatever a robot has (spec section 5).

Everything here is plain data with no network library, so the DNS-SD record and the sticker URL
are tested without zeroconf. A beacon proves nothing: anybody can claim any short id, and only
the passport it points to, and its signature, settle anything.
"""

from __future__ import annotations

import re
import secrets
import uuid
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


# ── Bluetooth LE: the advert two robots hear each other by ─────────────────────────────────
#
# Ten bytes of service data under a UUID of the passport's own, and nothing else: no name, no
# key, no TX power. The beacon id in it is drawn at random and replaced every quarter of an hour,
# so an advert says only that some robot that speaks Robot Passport is here, and a listener who
# records them cannot tell one robot's adverts from another's across a rotation. It proves
# nothing either: anybody can send any beacon id, and only the passport behind it, signed by its
# owner and checked in the hello, settles who a robot is.

#: The three UUIDs of the passport's Bluetooth half, each `uuid5(NAMESPACE_URL, SPEC_URL + "#"
#: + name)` for `ble-service`, `ble-rx` and `ble-tx`, so anybody can recompute them, and a
#: version-5 UUID cannot fall in the Bluetooth SIG's base range, whose UUIDs are version 1.
SPEC_URL = "https://github.com/rokbenko/quackd/blob/main/docs/passport/spec.md"
BLE_SERVICE_UUID = "b68a53ea-46e1-50d4-81a3-039a0500a15d"
#: written by a central that dialled this robot: link frames in
BLE_RX_UUID = "ba6288b5-e97c-51d2-a7b4-9cd04f5ad64c"
#: notified to it: link frames out
BLE_TX_UUID = "f5d0526f-21e9-599f-8454-cda57ec39c1b"

BLE_VERSION = 1
#: Look this robot up by DNS-SD: it has a LAN record whose `bt` is this beacon id.
FLAG_LAN = 0x01
#: It accepts a Bluetooth link now.
FLAG_LINK = 0x02
#: It is at a link or session limit: dialling it now would be refused.
FLAG_BUSY = 0x04
_FLAGS = FLAG_LAN | FLAG_LINK | FLAG_BUSY
BEACON_ID_BYTES = 8
SERVICE_DATA_BYTES = 2 + BEACON_ID_BYTES
#: AD types (Bluetooth Core Supplement, part A): Flags, and Service Data with a 128-bit UUID.
AD_FLAGS = 0x01
AD_SERVICE_DATA_128 = 0x21
#: LE General Discoverable Mode, and BR/EDR Not Supported: a robot is only ever an LE device.
_GENERAL_DISCOVERABLE = 0x02
_NO_BR_EDR = 0x04
#: A legacy advert's payload, all of it: the flags AD BlueZ puts in front of a peripheral's
#: advert (3 bytes) and the service data AD (1 + 1 + 16 + 10 bytes) fill it exactly.
LEGACY_AD_BYTES = 31
#: The most of an advert `read_advertising_data` walks: an extended advert's single PDU. More
#: is not an advert any radio sent.
MAX_AD_BYTES = 254
_HEX_ID = re.compile(r"^[0-9a-f]{16}$")


@dataclass(frozen=True)
class BleBeacon:
    """An advert as a listener read it: its version, its flags and the beacon id it claims."""

    version: int
    flags: int
    beacon_id: str

    @property
    def lan(self) -> bool:
        return bool(self.flags & FLAG_LAN)

    @property
    def link(self) -> bool:
        return bool(self.flags & FLAG_LINK)

    @property
    def busy(self) -> bool:
        return bool(self.flags & FLAG_BUSY)


def new_beacon_id() -> str:
    """A fresh beacon id: 8 random bytes as 16 lower-case hex characters, derived from no key,
    so that nothing links it to the robot's id or to the beacon id it replaces."""
    return secrets.token_bytes(BEACON_ID_BYTES).hex()


def is_beacon_id(value: object) -> bool:
    return isinstance(value, str) and _HEX_ID.match(value) is not None


def ble_service_data(beacon_id: str, *, lan: bool, link: bool, busy: bool = False) -> bytes:
    """The ten bytes a robot advertises under `BLE_SERVICE_UUID`: version, flags, beacon id."""
    if not is_beacon_id(beacon_id):
        raise ValueError(f"{beacon_id!r} is not a beacon id: 16 lower-case hex characters")
    flags = (FLAG_LAN if lan else 0) | (FLAG_LINK if link else 0) | (FLAG_BUSY if busy else 0)
    return bytes((BLE_VERSION, flags)) + bytes.fromhex(beacon_id)


def read_ble_service_data(data: bytes) -> BleBeacon | None:
    """The beacon in a robot's service data, or None for data too short or of another version.
    Bytes past the tenth are ignored, as are flag bits this version does not define, so a later
    minor revision can add either without older readers losing the robot."""
    if len(data) < SERVICE_DATA_BYTES or data[0] != BLE_VERSION:
        return None
    return BleBeacon(BLE_VERSION, data[1] & _FLAGS, bytes(data[2:SERVICE_DATA_BYTES]).hex())


def _uuid_on_air(text: str) -> bytes:
    # an AD structure carries a 128-bit UUID little-endian, the reverse of how it is written
    return uuid.UUID(text).bytes[::-1]


def advertising_data(service_data: bytes, *, discoverable: bool = True) -> bytes:
    """The whole legacy advert a robot sends, as it goes on the air: the flags AD, then the
    service data AD. BlueZ builds this itself from the service data; the sim and the vectors
    build it here, so that what they carry is byte for byte what a radio would."""
    if len(service_data) != SERVICE_DATA_BYTES:
        raise ValueError(f"service data is {SERVICE_DATA_BYTES} bytes, not {len(service_data)}")
    flags = (_GENERAL_DISCOVERABLE if discoverable else 0) | _NO_BR_EDR
    service = _uuid_on_air(BLE_SERVICE_UUID) + service_data
    return bytes((2, AD_FLAGS, flags, 1 + len(service), AD_SERVICE_DATA_128)) + service


def read_advertising_data(ad: bytes) -> BleBeacon | None:
    """The passport beacon in an advert, or None when it carries none. Walks the AD structures
    one length byte at a time and stops at the first that runs past the end, so no advert,
    however it is built, makes it read outside what it was handed or loop more than once a
    byte."""
    if len(ad) > MAX_AD_BYTES:
        return None
    ours = _uuid_on_air(BLE_SERVICE_UUID)
    at = 0
    while at < len(ad):
        length = ad[at]
        if length == 0 or at + 1 + length > len(ad):
            return None  # an early end, or a structure that claims more than is there
        kind, value = ad[at + 1], ad[at + 2 : at + 1 + length]
        if kind == AD_SERVICE_DATA_128 and value[:16] == ours:
            return read_ble_service_data(value[16:])
        at += 1 + length
    return None


def initiates(mine: str, theirs: str) -> bool:
    """Whether this robot dials: the lower beacon id does, so two robots that meet make one
    link and not two. Equal ids, which two random draws of 8 bytes all but never give, mean
    neither dials until one of them rotates."""
    return mine < theirs
