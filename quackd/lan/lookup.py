"""Where a robot heard by radio can be reached on the LAN, as a record and a lookup.

Plain data with no import beyond the standard library, so the radio, the sim air and the tests
use it without loading what DNS-SD and quackd's own LAN records need. `quackd.lan.passports`
re-exports both and holds `PassportBrowser`, the lookup a robot uses on a real network.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class LanRecord:
    """A robot's passport record as a LAN lookup found it: the host and port that serve its
    passport and its talk inbox, its static short id, and the beacon id it says it advertises
    now (`bt` in a TXT record of version 2). That last is a claim like any other: a robot that
    answers the hello at this address proves who it is, and the beacon id it carries in that
    hello has to be the one this robot heard on its radio."""

    host: str
    port: int
    short_id: str
    beacon_id: str | None


class LanDirectory(Protocol):
    """Whatever finds a robot's LAN record by the beacon id its advert carried: DNS-SD on a
    robot (`PassportBrowser`), a shared directory in the sim, a dict in a test."""

    def lookup(self, beacon_id: str) -> LanRecord | None: ...


__all__ = ["LanDirectory", "LanRecord"]
