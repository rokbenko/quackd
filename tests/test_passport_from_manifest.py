"""A 1.0 passport for every body quackd publishes, built from its manifest by
`passport_from_manifest`, for its owner to sign.

What a manifest cannot know comes from the robot's owner: who they are and which key signs, what
the robot runs on, how a person stops it, and the dates the signature holds between. What changes
while a robot runs, the software and where it listens, goes in its hello and never in what the
owner signs.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest

from quackd.adapters.factory import ADAPTER_NAMES, BACKENDS, RobotSpec, describe
from quackd.adapters.passport import CANNOT_CHARS, cannot_line, courtesy, passport_from_manifest
from quackd.passport import PassportError, document, strict
from tests.passport_helpers import OWNER, A

ISSUED = datetime(2026, 10, 29, 10, 0, tzinfo=UTC)
EXPIRES = ISSUED + timedelta(days=document.DEFAULT_VALIDITY_DAYS)
OWNED = {"id": OWNER.did, "label": "Example lab", "contact": "https://example.org/robots"}
BOARD = {"kind": "sim", "model": "a computer standing in for a robot's own"}


def _manifest(adapter: str):  # type: ignore[no-untyped-def]
    backend = "mock" if "mock" in BACKENDS[adapter] else BACKENDS[adapter][0]
    return describe(RobotSpec(adapter, backend, adapter))


def _built(adapter: str, **kw: object) -> dict[str, object]:
    given: dict[str, object] = {
        "robot_id": A.did,
        "name": adapter.replace("_", "-"),
        "owner": OWNED,
        "board": BOARD,
        "issued": ISSUED,
        "expires": EXPIRES,
        "estop": "power",
        "how_to_stop": "the power switch",
    }
    given.update(kw)
    return passport_from_manifest(_manifest(adapter), **given)  # type: ignore[arg-type]


@pytest.mark.parametrize("adapter", ADAPTER_NAMES)
def test_every_body_gets_a_passport_its_owner_can_sign_and_a_reader_accepts(adapter: str) -> None:
    doc = _built(adapter)
    raw = strict.dumps(doc)
    assert len(raw) <= document.WRITER_BYTES, f"{adapter}: {len(raw)} bytes"
    reading = document.read_jws(document.sign(doc, OWNER), now=int(ISSUED.timestamp()))
    assert reading.notes == (), reading.notes
    assert reading.signed and reading.owner_id == OWNER.did and reading.id == A.did
    assert reading.board() == BOARD
    manifest = _manifest(adapter)
    assert reading.skills() == [verb.name for verb in manifest.verbs]
    assert "stop" in reading.skills()
    assert reading.safety().stops_on_silence is manifest.safety_authority.deadman


def test_what_changes_while_a_robot_runs_is_not_in_what_its_owner_signs() -> None:
    """The software, its talk channels and a manifest digest changed without the owner, and a
    passport now changes only when its owner signs it again (spec 6.3 carries them instead)."""
    doc = _built("microduck")
    assert not {"software", "talk", "ext", "stamps", "ttl_s"} & set(doc)
    assert set(doc["owner"]) == set(OWNED)  # type: ignore[arg-type]


def test_the_dates_are_the_ones_given_in_utc_and_the_same_robot_builds_the_same_bytes() -> None:
    """A reader caches by digest (spec 5.5), so the same robot signed for the same dates builds
    the same bytes, with no build time inside them."""
    first = strict.dumps(_built("microduck"))
    assert strict.dumps(_built("microduck")) == first
    two_hours_east = timezone(timedelta(hours=2))
    doc = _built(
        "microduck",
        issued=ISSUED.astimezone(two_hours_east),
        expires=EXPIRES.astimezone(two_hours_east),
    )
    assert doc["issued"] == ISSUED.strftime("%Y-%m-%dT%H:%M:%SZ")
    assert doc["expires"] == EXPIRES.strftime("%Y-%m-%dT%H:%M:%SZ")
    assert strict.dumps(doc) == first


def test_dates_a_reader_would_refuse_are_refused_before_anything_is_signed() -> None:
    with pytest.raises(PassportError, match="expires is not after issued"):
        _built("microduck", expires=ISSUED)
    too_long = ISSUED + timedelta(seconds=document.MAX_VALIDITY_S + 86_400)
    with pytest.raises(PassportError) as refused:
        _built("microduck", expires=too_long)
    assert refused.value.rule == "rule 5"
    assert f"over {document.MAX_VALIDITY_S // 86_400}" in str(refused.value)
    longest = ISSUED + timedelta(seconds=document.MAX_VALIDITY_S)
    assert _built("microduck", expires=longest)["expires"] == longest.strftime("%Y-%m-%dT%H:%M:%SZ")


def test_an_owner_with_only_a_key_is_written_as_that_and_no_estop_reads_as_the_worst_case() -> None:
    doc = _built("lerobot", owner={"id": OWNER.did, "label": "", "contact": ""}, estop=None)
    assert doc["owner"] == {"id": OWNER.did}
    reading = document.read_jws(document.sign(doc, OWNER), now=int(ISSUED.timestamp()))
    assert reading.safety() == document.Safety(False, "none", "the power switch")
    bare = _built("lerobot", estop=None, how_to_stop=None)
    assert document.read_json(strict.dumps(bare), now=int(ISSUED.timestamp())).safety() == (
        document.Safety(False, "none", None)
    )


def test_a_body_that_cannot_pause_is_offered_as_one() -> None:
    assert _built("microduck")["courtesy"] == ["pause", "identify"]
    assert _built("microduck", courtesies=[])["courtesy"] == []
    assert _built("microduck", courtesies=["identify"])["courtesy"] == ["identify"]


def test_a_cannot_line_too_long_keeps_its_claim() -> None:
    long = "lift or hold more than about half a kilogram: " + "a pen, an empty cup, " * 6
    assert len(long) > CANNOT_CHARS
    assert cannot_line(long) == "lift or hold more than about half a kilogram"
    rambling = "x " * 100
    cut = cannot_line(rambling)
    assert len(cut) <= CANNOT_CHARS and cut.endswith("…")
    assert cannot_line("climb or descend a step") == "climb or descend a step"


def test_only_a_body_with_a_voice_offers_to_identify_itself() -> None:
    assert courtesy(_manifest("microduck")) == ["pause", "identify"]
    assert courtesy(_manifest("lerobot")) == ["pause"]


def test_an_unknown_estop_is_refused_rather_than_written() -> None:
    with pytest.raises(ValueError):
        _built("microduck", estop="lever")


def test_a_verb_name_a_passport_cannot_carry_is_written_as_one_or_left_out() -> None:
    """A manifest allows a hyphen, a leading digit and 64 characters, and a passport's skills
    allow none of them: quackd writes the first as an underscore and leaves the rest out, so it
    never signs a passport its own reader refuses."""
    manifest = _manifest("microduck")
    first = manifest.verbs[0]
    odd = [first.model_copy(update={"name": n}) for n in ("look-around", "2step", "x" * 40, "stop")]
    built = passport_from_manifest(
        manifest.model_copy(update={"verbs": odd}),
        robot_id=A.did,
        name="duck-01",
        owner=OWNED,
        board=BOARD,
        issued=ISSUED,
        expires=EXPIRES,
    )
    assert built["skills"] == ["look_around", "stop"]
