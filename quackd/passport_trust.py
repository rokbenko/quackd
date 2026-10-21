"""How this quackd knows a robot it read: its tier (docs/passport/trust.md#the-four-tiers).

A passport says what a robot claims and a signature says who wrote it; neither says whether to
believe them. That comes from here, from what a person decided: the keys they pinned, the owner
key that stamps their own robots, and the robots this quackd drives itself. Nothing in a
passport can raise its own tier, and an unsigned passport is always a stranger's, whatever id it
names, because anybody can copy an id.

Names are another matter. A robot names itself, so a name proves nothing, and the warnings here
are for a person about to trust one: a robot that calls itself what a pinned robot, one of this
quackd's own, or a pinned owner is called, with a key that is not theirs.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass

from quackd.passport.document import Reading
from quackd.passport_store import PassportStore, Pin

#: Lowest first. `flock` is a robot this quackd drives itself.
TIERS = ("stranger", "known", "same owner", "flock")

#: Letters of other scripts drawn like Latin ones, and the digits drawn like letters, read as
#: the letter they look like, so a name spelled with them warns like the name it imitates. The
#: usual suspects rather than Unicode's whole confusables table, which nothing here ships.
_LOOKALIKES = str.maketrans(
    # twenty Cyrillic letters, eleven Greek ones, then two digits and a bar, each written in the
    # second string as the Latin letter it is drawn like
    "\u0430\u0432\u0435\u043a\u043c\u043d\u043e\u0440\u0441\u0442\u0443\u0445\u0456\u0458"
    "\u0455\u0501\u04bb\u051b\u051d\u04cf"
    "\u03b1\u03b2\u03b5\u03b9\u03ba\u03bd\u03bf\u03c1\u03c4\u03c5\u03c7"
    "01|",
    "abekmhopctyxijsdhqwlabeikvoptuxoll",
)


@dataclass(frozen=True)
class Judgement:
    """A reading's tier, the one line that says why, and anything a person should be told."""

    tier: str
    why: str
    warnings: tuple[str, ...] = ()


def fold(name: str) -> str:
    """A name as a person sees it, to tell whether two would look alike: NFKC and casefolded,
    with no character that shows nothing, its spaces collapsed, and lookalikes read as Latin."""
    text = unicodedata.normalize("NFKC", name).casefold()
    shown = "".join(c for c in text if unicodedata.category(c) not in ("Cc", "Cf", "Zl", "Zp"))
    return " ".join(shown.split()).translate(_LOOKALIKES)


def issuer_names(store: PassportStore) -> dict[str, str]:
    """What this quackd knows each key it trusts as, so a stamp can say who made it."""
    names = {pin.id: f"pinned as {pin.name}" for pin in store.known()}
    own = store.owner().key
    if own is not None:
        names[own.did] = "your own owner key"
    return names


def judge(reading: Reading, store: PassportStore) -> Judgement:
    pins = store.known()
    robots = {pin.id: pin for pin in pins if pin.kind == "robot"}
    owners = {pin.id: pin for pin in pins if pin.kind == "owner"}
    owner = store.owner()
    own_owner = owner.key
    own_robots = {name: record.key.did for name, record in store.robots().items()}
    warnings = _lookalikes(
        reading,
        robots=robots.values(),
        owners=owners.values(),
        own_robots=own_robots,
        own_label=owner.label,
        own_key=own_owner.did if own_owner is not None else None,
    )
    if not reading.signed:
        if reading.id in robots or reading.id in own_robots.values():
            warnings.append(
                "this id is pinned or one of yours, but the passport is unsigned, so it is read as "
                "a stranger's claim: anybody can copy an id"
            )
        return Judgement("stranger", "unsigned, so a claim", tuple(warnings))
    if reading.id in own_robots.values():
        return Judgement("flock", "one of the robots this quackd drives", tuple(warnings))
    for stamp in reading.counted_stamps():
        if own_owner is not None and stamp.iss == own_owner.did:
            return Judgement("same owner", "stamped by your own owner key", tuple(warnings))
        if stamp.iss in owners:
            pinned = owners[stamp.iss]
            return Judgement(
                "same owner", f"stamped by {pinned.name}, an owner you pinned", tuple(warnings)
            )
    if reading.id in robots:
        return Judgement("known", f"pinned on {robots[reading.id].pinned[:10]}", tuple(warnings))
    return Judgement("stranger", "signed by a key nobody here pinned", tuple(warnings))


def _lookalikes(
    reading: Reading,
    *,
    robots: Iterable[Pin],
    owners: Iterable[Pin],
    own_robots: dict[str, str],
    own_label: str | None,
    own_key: str | None,
) -> list[str]:
    """What a person should be told before trusting a name: SSH's "the key changed", for a
    name that looks like one this quackd already knows under another key."""
    warnings: list[str] = []
    name = fold(reading.name)
    for pin in robots:
        if fold(pin.name) == name and pin.id != reading.id:
            warnings.append(
                f"a robot named {pin.name} was pinned with another key on {pin.pinned[:10]}: "
                "this is not that key, so nothing is trusted automatically"
            )
    for own_name, did in own_robots.items():
        if fold(own_name) == name and did != reading.id:
            warnings.append(f"this quackd drives a robot named {own_name}, and this is not its key")
    label = reading.document.get("owner", {}).get("label")
    if isinstance(label, str) and label:
        stamped_by = {s.iss for s in reading.counted_stamps()}
        # an unsigned passport's stamps never count, which is a different thing to tell a person
        # from an owner who did not stamp it at all
        unsigned = "it is unsigned, so no stamp on it counts"
        for pin in owners:
            if fold(pin.name) == fold(label) and pin.id not in stamped_by:
                why = unsigned if not reading.signed else f"{pin.name}'s key did not stamp it"
                warnings.append(f"it says its owner is {label}, an owner you pinned, and {why}")
        if own_label and fold(own_label) == fold(label) and own_key not in stamped_by:
            why = unsigned if not reading.signed else "your owner key did not stamp it"
            warnings.append(
                f"it says its owner is {label}, which is what you call yourself, and {why}"
            )
    return warnings
