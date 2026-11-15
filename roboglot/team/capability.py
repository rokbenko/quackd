"""The capability term: a robot bids only for a role its manifest can fill (ADR-0020).

Names are compared canonically, so a robot that provides `observe` satisfies a role that
requires `get_frame`. Both the member (before bidding) and the coordinator (before
counting a bid) apply the same check; the second is defence in depth for the day bids
arrive over a LAN from a robot we do not run.

A v2 role can also ask for a body rather than a vocabulary: enough payload, enough reach, a
gripper rather than a beak. That half is `missing_needs` in `roboglot/verdict.py`, where the
same words are read by the pilot's own verdict, so a role and a refusal say the same thing
(ADR-0032). It is re-exported here because this is where the team looks for it.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import TYPE_CHECKING

from roboglot.glotfile.schema import TeamRole
from roboglot.verbs.aliases import canonical
from roboglot.verdict import missing_needs, missing_needs_in

if TYPE_CHECKING:
    from roboglot.adapters.manifest import RobotManifest

__all__ = ["eligible_roles", "missing", "missing_needs", "missing_needs_in"]


def missing(requires: Iterable[str], provides: Iterable[str]) -> list[str]:
    """The required verbs `provides` lacks, canonical and sorted; empty means satisfied."""
    have = {canonical(v) for v in provides}
    return sorted({canonical(r) for r in requires} - have)


def eligible_roles(
    roles: Mapping[str, TeamRole],
    provides: Iterable[str],
    manifest: RobotManifest | None = None,
) -> list[str]:
    """The roles a robot may bid for, sorted by name: the verbs it has, and for a v2 role,
    what its datasheet says it can do.

    Without a manifest a role with `needs` is dropped rather than allowed: an unanswered
    question about a body is not a yes."""
    have = list(provides)
    return sorted(
        name
        for name, role in roles.items()
        if not missing(role.requires, have)
        and (not role.needs or (manifest is not None and not missing_needs(role.needs, manifest)))
    )
