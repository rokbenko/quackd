"""Trust in Robot Passport (docs/passport/trust.md): the tier a robot puts a robot it met in.

A 1.0 passport is signed by its owner, so a signature says which owner vouches for a robot and
nothing about whether to believe that owner. What a robot believes comes from its own owner's
decisions alone: their own owner key, which signs every robot of theirs, and the robots and owners
they pinned in robot.yaml. Each tier is reached here the way a person reaches it, and the warnings
are the ones a person needs before trusting a name: a robot names itself and its owner, so a name
proves nothing.
"""

from __future__ import annotations

import pytest

from quackd.passport import SigningKey, document, strict
from quackd.passport_trust import TIERS, Pin, judge
from tests.passport_helpers import INTRUDER, OWNER, OWNER_B, B, doc, passport

#: A pin's date, as robot.yaml writes it.
PINNED = "2026-10-29"


def read(token: str) -> document.Reading:
    return document.read_jws(token)


def labelled(robot: SigningKey, owner: SigningKey, name: str, label: str) -> str:
    """A passport whose owner goes by `label`, signed by `owner`'s key: anybody can write any
    label, and only the key that signs is the owner's."""
    unsigned = doc(robot, owner, name)
    unsigned["owner"]["label"] = label
    return document.sign(unsigned, owner)


# ── the tiers ───────────────────────────────────────────────────────────────────────────


def test_a_robot_its_own_owner_key_signed_is_same_owner() -> None:
    judged = judge(read(passport(B, OWNER, "cart-01")), own_owner=OWNER.did)
    assert judged.tier == "same owner"
    assert judged.warnings == ()


def test_every_robot_a_pinned_owner_signed_is_known_and_no_robot_another_owner_signed() -> None:
    pins = [Pin("owner", OWNER_B.did, "Courier company", PINNED)]
    for robot, name in ((B, "cart-01"), (INTRUDER, "van-07")):  # neither robot was ever pinned
        judged = judge(read(passport(robot, OWNER_B, name)), own_owner=OWNER.did, pins=pins)
        assert judged.tier == "known", name
        assert "Courier company" in judged.why
    # the same robot signed by an owner nobody pinned is nobody this robot knows
    other = judge(read(passport(B, INTRUDER, "cart-01")), own_owner=OWNER.did, pins=pins)
    assert other.tier == "stranger"


def test_a_pinned_robot_is_known_whichever_owner_signed_it() -> None:
    pins = [Pin("robot", B.did, "cart-01", PINNED)]
    judged = judge(read(passport(B, OWNER_B, "cart-01")), own_owner=OWNER.did, pins=pins)
    assert judged.tier == "known"
    assert PINNED in judged.why


def test_an_owner_nobody_pinned_raises_nothing() -> None:
    """Anybody can make an owner key and sign a passport with it, so a signature that checks
    out is a stranger's until a person pins the robot or its owner."""
    pins = [Pin("robot", INTRUDER.did, "van-07", PINNED), Pin("owner", OWNER.did, "Lab", PINNED)]
    judged = judge(read(passport(B, OWNER_B, "cart-01")), own_owner=OWNER.did, pins=pins)
    assert judged.tier == "stranger"
    assert judge(read(passport(B, OWNER_B, "cart-01")), own_owner=None).tier == "stranger"


def test_the_highest_tier_that_applies_wins_and_every_tier_is_reached() -> None:
    everything = [
        Pin("robot", B.did, "cart-01", PINNED),
        Pin("owner", OWNER.did, "Lab", PINNED),
        Pin("owner", OWNER_B.did, "Courier company", PINNED),
    ]
    reached = {
        # signed by this robot's own owner, and pinned twice over besides
        judge(read(passport(B, OWNER, "cart-01")), own_owner=OWNER.did, pins=everything).tier,
        # pinned as a robot and through its owner: known, and said to be known through the owner
        judge(read(passport(B, OWNER_B, "cart-01")), own_owner=OWNER.did, pins=everything).tier,
        judge(read(passport(B, INTRUDER, "cart-01")), own_owner=OWNER.did).tier,
    }
    assert reached == set(TIERS)
    via_owner = judge(read(passport(B, OWNER_B, "cart-01")), own_owner=OWNER.did, pins=everything)
    assert via_owner.tier == "known" and "Courier company" in via_owner.why


def test_an_unsigned_passport_is_a_stranger_whatever_it_names() -> None:
    """Anybody can copy an id, a pinned robot's or an owner's, into a passport nobody signed."""
    pins = [
        Pin("robot", B.did, "cart-01", PINNED),
        Pin("owner", OWNER_B.did, "Courier company", PINNED),
    ]
    for owner in (OWNER, OWNER_B):
        copy = document.read_json(strict.dumps(doc(B, owner, "cart-01")))
        assert not copy.signed
        judged = judge(copy, own_owner=OWNER.did, pins=pins)
        assert judged.tier == "stranger"
        assert judged.why == "unsigned, so a claim"
        assert any("nobody signed it" in warning for warning in judged.warnings)
    unknown = document.read_json(strict.dumps(doc(INTRUDER, OWNER_B, "cart-09")))
    plain = judge(unknown, own_owner=OWNER.did, pins=pins[:1])
    assert plain.tier == "stranger" and not plain.warnings


# ── names that look like ones this robot knows ──────────────────────────────────────────


def test_a_pinned_name_with_another_key_is_warned_about_like_ssh() -> None:
    pins = [Pin("robot", B.did, "duck-01", PINNED)]
    impostor = read(passport(INTRUDER, OWNER_B, "duck-01"))
    judged = judge(impostor, own_owner=OWNER.did, pins=pins)
    assert judged.tier == "stranger"
    assert any("pinned with another key" in w for w in judged.warnings)
    # the pinned robot itself, under its own key, is no lookalike of itself
    assert (
        judge(read(passport(B, OWNER_B, "duck-01")), own_owner=OWNER.did, pins=pins).warnings == ()
    )


@pytest.mark.parametrize(
    "lookalike",
    [
        "duck-01\N{ZERO WIDTH SPACE}",
        "du\N{CYRILLIC SMALL LETTER ES}k-01",
        "DUCK-01 ",
        "duck-0l",
    ],
)
def test_a_name_that_only_looks_like_a_pinned_ones_is_warned_about(lookalike: str) -> None:
    pins = [Pin("robot", B.did, "duck-01", PINNED)]
    judged = judge(read(passport(INTRUDER, OWNER_B, lookalike)), own_owner=OWNER.did, pins=pins)
    assert judged.tier == "stranger"
    assert any("pinned with another key" in w for w in judged.warnings)


@pytest.mark.parametrize("label", ["Example lab", "Examp1e lab", "EXAMPLE  LAB"])
def test_an_owner_label_a_pinned_owner_goes_by_on_another_keys_passport_is_warned_about(
    label: str,
) -> None:
    pins = [Pin("owner", OWNER_B.did, "Example lab", PINNED)]
    claimed = labelled(B, INTRUDER, "cart-01", label)
    judged = judge(read(claimed), own_owner=OWNER.did, pins=pins)
    assert judged.tier == "stranger"
    assert any("an owner you pinned, and another key signed it" in w for w in judged.warnings)
    genuine = labelled(B, OWNER_B, "cart-01", label)
    trusted = judge(read(genuine), own_owner=OWNER.did, pins=pins)
    assert trusted.tier == "known" and trusted.warnings == ()


def test_a_robot_that_takes_this_robots_name_or_its_owners_is_warned_about() -> None:
    own = {"own_owner": OWNER.did, "own_name": "duck-01", "own_label": "Rok's lab"}
    posing = labelled(B, INTRUDER, "duck-01", "Rok's lab")
    judged = judge(read(posing), **own)
    assert judged.tier == "stranger"
    assert any("which is what this robot is called" in w for w in judged.warnings)
    assert any("which is what your owner calls itself" in w for w in judged.warnings)
    # a robot of the same owner says so honestly, and its owner key bears it out
    sibling = labelled(B, OWNER, "cart-01", "Rok's lab")
    honest = judge(read(sibling), **own)
    assert honest.tier == "same owner" and honest.warnings == ()
