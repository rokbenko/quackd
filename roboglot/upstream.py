"""How Roboglot cites an upstream project, in one place that belongs to no robot.

Every adapter records the names it relies on as `UpstreamRef`s, tagged VERIFIED (read from
upstream source at a pinned commit, link given) or UNVERIFIED (designed upstream but not
shipped, or an assumption of ours, with what Roboglot does about it). ADR-0022 is why each
adapter keeps its own file of them rather than sharing one list.

This type used to live in the Microduck's own `upstream_api.py`, which made every other
adapter import the Microduck to describe a robot that has nothing to do with it. It is the
one thing in that file that was never about ducks.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Status = Literal["VERIFIED", "UNVERIFIED"]


@dataclass(frozen=True)
class UpstreamRef:
    """A named upstream thing and how sure we are that it exists as described."""

    name: str
    status: Status
    source: str
    note: str = ""

    def __str__(self) -> str:
        return self.name
