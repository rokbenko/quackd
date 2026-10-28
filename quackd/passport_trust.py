"""How a robot knows a robot it met: its tier (docs/passport/trust.md#the-tiers).

A passport says what a robot claims and its owner's signature says who vouches for that; neither
says whether to believe the owner, since anybody can make an owner key. That comes from here,
from what this robot's own owner decided: the robots and owners they pinned in robot.yaml, and
their own owner key, which signs this robot's passport. Nothing in a passport can raise its own
tier, and an unsigned passport is always a stranger's, whatever it says.

Names are another matter. A robot names itself and its owner, so a name proves nothing, and the
warnings here are for a person about to trust one: a robot that calls itself what a pinned robot
or this robot is called, or names an owner this robot's owner pinned, with a key that is not
theirs.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass

from quackd.passport.document import Reading

#: Lowest first. `same owner` is a robot this robot's own owner key signed.
TIERS = ("stranger", "known", "same owner")


@dataclass(frozen=True)
class Pin:
    """A robot or an owner a person said to trust, by its key: `kind` is robot or owner."""

    kind: str
    id: str
    name: str
    pinned: str


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


def judge(
    reading: Reading,
    *,
    own_owner: str | None,
    pins: Iterable[Pin] = (),
    own_name: str | None = None,
    own_label: str | None = None,
) -> Judgement:
    """The tier this robot places `reading` in: `same owner` when its own owner key signed it,
    `known` when a person pinned the robot or the owner that signed it, and `stranger` for
    anything else, an unsigned passport above all."""
    pins = list(pins)
    robots = {pin.id: pin for pin in pins if pin.kind == "robot"}
    owners = {pin.id: pin for pin in pins if pin.kind == "owner"}
    warnings = _lookalikes(
        reading,
        robots=robots.values(),
        owners=owners.values(),
        own_name=own_name,
        own_label=own_label,
        own_owner=own_owner,
    )
    if not reading.signed:
        trusted = {*robots, *owners, *([own_owner] if own_owner else [])}
        if reading.id in trusted or reading.owner_id in trusted:
            warnings.append(
                "it names a key you trust, but nobody signed it, so it is read as a stranger's "
                "claim: anybody can copy an id"
            )
        return Judgement("stranger", "unsigned, so a claim", tuple(warnings))
    if own_owner is not None and reading.owner_id == own_owner:
        return Judgement("same owner", "signed by your own owner key", tuple(warnings))
    if reading.owner_id in owners:
        pinned = owners[reading.owner_id]
        return Judgement("known", f"signed by {pinned.name}, an owner you pinned", tuple(warnings))
    if reading.id in robots:
        return Judgement("known", f"pinned on {robots[reading.id].pinned[:10]}", tuple(warnings))
    return Judgement("stranger", "signed by an owner nobody here pinned", tuple(warnings))


def _lookalikes(
    reading: Reading,
    *,
    robots: Iterable[Pin],
    owners: Iterable[Pin],
    own_name: str | None,
    own_label: str | None,
    own_owner: str | None,
) -> list[str]:
    """What a person should be told before trusting a name: SSH's "the key changed", for a
    name that looks like one this robot already knows under another key."""
    warnings: list[str] = []
    name = fold(reading.name)
    for pin in robots:
        if fold(pin.name) == name and pin.id != reading.id:
            warnings.append(
                f"a robot named {pin.name} was pinned with another key on {pin.pinned[:10]}: "
                "this is not that key, so nothing is trusted automatically"
            )
    if own_name and fold(own_name) == name:
        warnings.append(f"it calls itself {own_name}, which is what this robot is called")
    label = reading.document.get("owner", {}).get("label")
    if isinstance(label, str) and label:
        for pin in owners:
            if fold(pin.name) == fold(label) and pin.id != reading.owner_id:
                warnings.append(
                    f"it says its owner is {label}, an owner you pinned, and another key signed it"
                )
        if own_label and fold(own_label) == fold(label) and reading.owner_id != own_owner:
            warnings.append(
                f"it says its owner is {label}, which is what your owner calls itself, and "
                "another key signed it"
            )
    return warnings


__all__ = ["TIERS", "Judgement", "Pin", "fold", "judge"]
