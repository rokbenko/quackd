"""How this quackd knows a robot it read: its tier (docs/passport/trust.md#the-four-tiers).

A passport says what a robot claims and a signature says who wrote it; neither says whether to
believe them. That comes from here, from what a person decided: the keys they pinned, the owner
key that stamps their own robots, and the robots this quackd drives itself. Nothing in a
passport can raise its own tier, and an unsigned passport is always a stranger's, whatever id it
names, because anybody can copy an id.
"""

from __future__ import annotations

from dataclasses import dataclass

from quackd.passport.document import Reading
from quackd.passport_store import PassportStore

#: Lowest first. `flock` is a robot this quackd drives itself.
TIERS = ("stranger", "known", "same owner", "flock")


@dataclass(frozen=True)
class Judgement:
    """A reading's tier, the one line that says why, and anything a person should be told."""

    tier: str
    why: str
    warnings: tuple[str, ...] = ()


def judge(reading: Reading, store: PassportStore) -> Judgement:
    pins = store.known()
    robots = {pin.id: pin for pin in pins if pin.kind == "robot"}
    owners = {pin.id: pin for pin in pins if pin.kind == "owner"}
    own_owner = store.owner().key
    own_robots = {record.key.did for record in store.robots().values()}
    warnings: list[str] = []
    for pin in robots.values():
        if pin.name == reading.name and pin.id != reading.id:
            warnings.append(
                f"a robot named {pin.name} was pinned with another key on {pin.pinned[:10]}: "
                "this is not that key, so nothing is trusted automatically"
            )
    if not reading.signed:
        if reading.id in robots or reading.id in own_robots:
            warnings.append(
                "this id is pinned or one of yours, but the passport is unsigned, so it is read as "
                "a stranger's claim: anybody can copy an id"
            )
        return Judgement("stranger", "unsigned, so a claim", tuple(warnings))
    if reading.id in own_robots:
        return Judgement("flock", "one of the robots this quackd drives", tuple(warnings))
    for stamp in reading.counted_stamps():
        if own_owner is not None and stamp.iss == own_owner.did:
            return Judgement("same owner", "stamped by your own owner key", tuple(warnings))
        if stamp.iss in owners:
            owner = owners[stamp.iss]
            return Judgement(
                "same owner", f"stamped by {owner.name}, an owner you pinned", tuple(warnings)
            )
    if reading.id in robots:
        return Judgement("known", f"pinned on {robots[reading.id].pinned[:10]}", tuple(warnings))
    return Judgement("stranger", "signed by a key nobody here pinned", tuple(warnings))
