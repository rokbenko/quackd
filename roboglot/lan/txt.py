"""The mDNS TXT record a roboglot robot advertises, and how one is read back.

Pure: no zeroconf import, so the format is unit-tested without the extra. Identity only
(`v mid sha adp vend mdl emb nverbs`); a manifest is never squeezed into TXT, it is
obtained out of band and verified by `sha`, the manifest digest. Every key-value pair is
checked under 200 bytes because zeroconf performs no validation and dies with a bare
`ValueError` at the protocol's 255 (ADR-0021). These keys are wire protocol once shipped.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass
from typing import Any

from roboglot.adapters.manifest import RobotManifest

SERVICE_TYPE = "_roboglot._tcp.local."
TXT_VERSION = "1"
MAX_PAIR_BYTES = 200
KEYS = ("v", "mid", "sha", "adp", "vend", "mdl", "emb", "nverbs")


class TxtError(ValueError):
    """A record that would not survive the wire."""


def check_pair(key: str, value: str) -> None:
    if not key or "=" in key:
        raise TxtError(f"TXT key {key!r} must be non-empty and contain no '='")
    n = len(f"{key}={value}".encode())
    if n >= MAX_PAIR_BYTES:
        raise TxtError(
            f"TXT pair {key}= is {n} bytes; the limit is {MAX_PAIR_BYTES} "
            "(mDNS caps a pair at 255 and zeroconf does not check)"
        )


def txt_record(manifest: RobotManifest, *, adapter: str) -> dict[str, str]:
    """What a robot advertises about itself, every pair validated."""
    record = {
        "v": TXT_VERSION,
        "mid": manifest.id,
        "sha": manifest.digest(),
        "adp": adapter,
        "vend": manifest.vendor,
        "mdl": manifest.model,
        "emb": manifest.embodiment,
        "nverbs": str(len(manifest.verbs)),
    }
    for key, value in record.items():
        check_pair(key, value)
    return record


def instance_name(manifest_id: str, *, service_type: str = SERVICE_TYPE) -> str:
    """`<manifest id>._roboglot._tcp.local.`: the id is a slug, so it needs no escaping."""
    return f"{manifest_id}.{service_type}"


def parse_txt(properties: Mapping[Any, Any]) -> dict[str, str]:
    """zeroconf hands back bytes keys and bytes-or-None values; plain strings are fine too.
    A key without a value is present on the wire but carries nothing, so it is dropped."""
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
class DiscoveredRobot:
    """One answer on the LAN, identity only. `matches()` checks a manifest fetched out of
    band against the advertised digest."""

    instance: str
    manifest_id: str
    digest: str
    adapter: str
    vendor: str
    model: str
    embodiment: str
    n_verbs: int
    host: str
    port: int
    addresses: tuple[str, ...]

    def matches(self, manifest: RobotManifest) -> bool:
        return manifest.digest() == self.digest

    def row(self) -> dict[str, Any]:
        return asdict(self)


def robot_from_txt(
    txt: Mapping[str, str], *, instance: str, host: str, port: int, addresses: tuple[str, ...]
) -> DiscoveredRobot | None:
    """A record of another version, or one without an identity, is not a roboglot robot."""
    if txt.get("v") != TXT_VERSION or not txt.get("mid") or not txt.get("sha"):
        return None
    try:
        n_verbs = int(txt.get("nverbs", "0"))
    except ValueError:
        n_verbs = 0
    return DiscoveredRobot(
        instance=instance,
        manifest_id=txt["mid"],
        digest=txt["sha"],
        adapter=txt.get("adp", ""),
        vendor=txt.get("vend", ""),
        model=txt.get("mdl", ""),
        embodiment=txt.get("emb", ""),
        n_verbs=n_verbs,
        host=host,
        port=port,
        addresses=addresses,
    )


__all__ = [
    "KEYS",
    "MAX_PAIR_BYTES",
    "SERVICE_TYPE",
    "TXT_VERSION",
    "DiscoveredRobot",
    "TxtError",
    "check_pair",
    "instance_name",
    "parse_txt",
    "robot_from_txt",
    "txt_record",
]
