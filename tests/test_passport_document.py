"""The reading rules of docs/passport/spec.md section 3, one at a time, on documents built here.

The vectors check the cases the spec names; these check the edges a vector would be clumsy for:
a byte order mark, NaN, a duplicate three objects deep, nesting that would blow the stack, a
did:key spelled two ways, the dates an owner's signature holds between, and every member a
writer can get the type of wrong.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from quackd.passport import PassportError, SigningKey, document, jws, strict
from quackd.passport.keys import b58decode, b58encode, did_from_public, public_from_did

KEY = SigningKey(bytes(range(32)))
OWNER = SigningKey(bytes(range(32, 64)))
INTRUDER = SigningKey(bytes(range(96, 128)))
ISSUED = "2026-10-20T00:00:00Z"
EXPIRES = "2027-10-20T00:00:00Z"
#: A day after the passports here were signed, well inside their year.
NOW = document.epoch("2026-10-21T00:00:00Z", "now")


def passport(**members: Any) -> dict[str, Any]:
    return {
        "passport": document.VERSION,
        "id": KEY.did,
        "name": "g1-edu-01",
        "owner": {"id": OWNER.did},
        "issued": ISSUED,
        "expires": EXPIRES,
        **members,
    }


def raw(value: Any) -> bytes:
    return json.dumps(value).encode("utf-8")


def refused(data: bytes | str) -> PassportError:
    with pytest.raises(PassportError) as caught:
        document.read(data, now=NOW)
    return caught.value


# ── rules 1 and 2: the bytes ────────────────────────────────────────────────────────────


def test_a_document_of_exactly_the_limit_is_read_and_one_byte_more_is_not() -> None:
    base = raw(passport(padding=""))
    pad = document.MAX_BYTES - len(base)
    assert len(raw(passport(padding="x" * pad))) == document.MAX_BYTES
    assert isinstance(document.read(raw(passport(padding="x" * pad)), now=NOW), document.Reading)
    assert refused(raw(passport(padding="x" * (pad + 1)))).rule == "rule 1"


def test_a_byte_order_mark_nan_and_non_utf8_are_refused() -> None:
    assert refused(b"\xef\xbb\xbf" + raw(passport())).rule == "rule 2"
    assert refused(raw(passport())[:-1] + b',"x":NaN}').rule == "rule 2"
    assert refused(b'{"passport":"1.0","name":"\xff"}').rule == "rule 2"


def test_a_duplicate_member_is_refused_at_any_depth() -> None:
    nested = raw(passport(body={"sensors": []}))[:-1] + b',"body":{"sensors":[]}}'
    error = refused(nested)
    assert error.rule == "rule 2" and "body" in str(error)
    deep = raw(passport())[:-1] + b',"board":{"kind":"sim","kind":"jetson"}}'
    assert refused(deep).rule == "rule 2"


def test_nesting_deep_enough_to_blow_the_stack_is_refused_not_crashed() -> None:
    assert refused(b"[" * 4000 + b"]" * 4000).rule == "rule 2"  # 8000 bytes: under rule 1


def test_something_that_is_not_an_object_is_refused() -> None:
    assert refused(b"[1, 2]").rule == "rule 2"


# ── rules 3, 4, 5 and 7: the members ────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("members", "rule"),
    [
        ({"passport": "1"}, "rule 3"),
        ({"passport": "v1.0"}, "rule 3"),
        ({"passport": "2.0"}, "rule 4"),
        ({"passport": "0.1"}, "rule 4"),
        ({"name": ""}, "rule 3"),
        ({"name": 7}, "rule 3"),
        ({"id": "did:key:zQ3sharpXYZ"}, "rule 7"),
        ({"id": "urn:uuid:4f1c2a1e-9d4b-4c8e-8f3a-2b6d5e7a9c10"}, "rule 3"),
        ({"owner": {}}, "rule 3"),
        ({"owner": "the lab"}, "rule 5"),
        ({"owner": {"id": "urn:uuid:4f1c2a1e-9d4b-4c8e-8f3a-2b6d5e7a9c10"}}, "rule 3"),
        ({"owner": {"id": OWNER.did, "contact": "c" * 201}}, "rule 5"),
        ({"maker": {"vendor": "v" * 65}}, "rule 5"),
        ({"board": {"kind": "k" * 33}}, "rule 5"),
        ({"board": {"model": 7}}, "rule 5"),
        ({"body": {"mass_kg": -1}}, "rule 5"),
        ({"body": {"mass_kg": True}}, "rule 5"),
        ({"body": {"mass_kg": [1, "official", "s", "extra"]}}, "rule 5"),
        ({"body": {"tethered": "yes"}}, "rule 5"),
        ({"skills": ["Kick"]}, "rule 5"),
        ({"skills": ["a" * 33]}, "rule 5"),
        ({"cannot": [""]}, "rule 5"),
        ({"cannot": ["x"] * 9}, "rule 5"),
        ({"ext": {"NoDots": {}}}, "rule 5"),
        ({"issued": "yesterday"}, "rule 5"),
        ({"issued": "2026-13-45T00:00:00Z"}, "rule 5"),
        ({"issued": "2026-10-20T00:00:00"}, "rule 5"),
        ({"expires": "2026-10-19T00:00:00Z"}, "rule 5"),
        ({"expires": ISSUED}, "rule 5"),
        ({"expires": "2027-12-31T00:00:00Z"}, "rule 5"),
    ],
)
def test_a_member_of_the_wrong_shape_is_refused_under_its_rule(
    members: dict[str, Any], rule: str
) -> None:
    error = refused(raw(passport(**members)))
    assert error.rule == rule, str(error)


@pytest.mark.parametrize("member", document.REQUIRED)
def test_every_required_member_is_required(member: str) -> None:
    doc = passport()
    del doc[member]
    assert refused(raw(doc)).rule == "rule 3"


def test_a_signature_holds_for_no_longer_than_its_owner_may_say() -> None:
    longest = document.epoch(ISSUED, "issued") + document.MAX_VALIDITY_S
    edge = datetime.fromtimestamp(longest, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    assert document.read(raw(passport(expires=edge)), now=NOW).document["expires"] == edge
    later = datetime.fromtimestamp(longest + 1, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    assert refused(raw(passport(expires=later))).rule == "rule 5"


def test_one_key_has_one_spelling() -> None:
    """A did:key that decodes to the same key but is written differently is refused, so two
    ids compared as strings are two keys compared."""
    assert public_from_did(KEY.did) == KEY.public
    padded = "did:key:z1" + KEY.did[len("did:key:z") :]
    with pytest.raises(PassportError) as caught:
        public_from_did(padded)
    assert caught.value.rule == "rule 7"


def test_base58_round_trips_leading_zero_bytes() -> None:
    for data in (b"", b"\0", b"\0\0\x01", bytes(range(34))):
        assert b58decode(b58encode(data)) == data
    assert did_from_public(KEY.public) == KEY.did


# ── rules 8 to 10: readings ─────────────────────────────────────────────────────────────


def test_safety_always_reads_as_the_worst_case_and_never_refuses() -> None:
    for safety, expected in [
        (None, document.Safety(False, "none", None)),
        ("power", document.Safety(False, "none", None)),
        ({"stops_on_silence": 1}, document.Safety(False, "none", None)),
        ({"estop": "big red lever"}, document.Safety(False, "none", None)),
        ({"how_to_stop": "x" * 121}, document.Safety(False, "none", None)),
        (
            {"stops_on_silence": True, "estop": "button", "how_to_stop": "press it"},
            document.Safety(True, "button", "press it"),
        ),
    ]:
        members = {} if safety is None else {"safety": safety}
        reading = document.read(raw(passport(**members)), now=NOW)
        assert reading.safety() == expected


def test_unknown_members_courtesies_and_boards_are_ignored_with_a_note() -> None:
    reading = document.read(
        raw(
            passport(
                aura="calm",
                maker={"vendor": "v", "colour": "teal"},
                courtesy=["pause", "dance"],
                board={"kind": "quantum", "model": "a robot's own"},
            )
        ),
        now=NOW,
    )
    assert reading.courtesy() == ["pause"]
    assert reading.board() == {"kind": "other", "model": "a robot's own"}
    notes = " ".join(reading.notes)
    for word in ("aura", "maker.colour", "dance", "quantum"):
        assert word in notes


def test_quackds_own_required_extension_does_not_stop_talk() -> None:
    reading = document.read(
        raw(passport(ext={"org.quackd": {"required": True, "sha": "x"}})), now=NOW
    )
    assert reading.talk_allowed is True


def test_an_index_of_passports_is_no_passport() -> None:
    index = {"passport": document.VERSION, "robots": [{"url": "/r/a.json", "name": "a"}]}
    assert refused(raw(index)).rule == "rule 3"


def test_an_unsigned_passport_past_its_dates_is_read_for_a_person_with_a_note() -> None:
    """Unsigned, it is a claim a person may look at, never one talk takes, so its dates say
    something rather than refusing it outright."""
    late = document.epoch(EXPIRES, "expires") + 1
    reading = document.read(raw(passport()), now=late)
    assert not reading.signed
    assert any("expired" in note for note in reading.notes)


# ── signatures ──────────────────────────────────────────────────────────────────────────


def test_a_passport_signed_by_its_owner_reads_as_signed_and_keeps_its_bytes() -> None:
    doc = passport()
    reading = document.read(document.sign(doc, OWNER), now=NOW)
    assert reading.signed and reading.raw == strict.dumps(doc)
    assert reading.owner_id == OWNER.did and reading.owner_short_id == OWNER.short_id
    assert reading.provenance == "signed by its owner's key"
    assert (reading.issued, reading.expires) == (
        document.epoch(ISSUED, "issued"),
        document.epoch(EXPIRES, "expires"),
    )


def test_any_change_after_the_owner_signed_breaks_the_signature() -> None:
    """What Rok asked of a passport: nobody, the robot included, edits a signed one unnoticed."""
    head, _body, sig = document.sign(passport(), OWNER).split(".")
    for edit in (
        {"name": "g1-edu-02"},
        {"safety": {"estop": "button"}},
        {"cannot": []},
        {"expires": "2027-10-21T00:00:00Z"},
    ):
        bent = jws.b64url(strict.dumps(passport(**edit)))
        assert refused(f"{head}.{bent}.{sig}").rule == "4.4"


def test_a_passport_signed_by_its_robots_own_key_is_refused() -> None:
    """0.1 signed with the robot's key, which anybody holding the robot holds too."""
    token = jws.sign(strict.dumps(passport()), KEY, document.PASSPORT_TYP)
    error = refused(token)
    assert error.rule == "4.4" and "robot's own key" in str(error)
    yours = jws.sign(strict.dumps(passport(owner={"id": KEY.did})), KEY, document.PASSPORT_TYP)
    assert refused(yours).rule == "4.4"


def test_a_signed_passport_outside_its_dates_is_refused() -> None:
    token = document.sign(passport(), OWNER)
    assert document.read(token, now=document.epoch(EXPIRES, "expires") - 1).signed
    with pytest.raises(PassportError) as caught:
        document.read(token, now=document.epoch(EXPIRES, "expires"))
    assert caught.value.rule == "4.5"
    early = document.epoch(ISSUED, "issued") - document.CLOCK_SKEW_S - 1
    with pytest.raises(PassportError) as caught:
        document.read(token, now=early)
    assert caught.value.rule == "4.5"


def test_a_header_with_crit_or_another_alg_is_refused() -> None:
    body = jws.b64url(strict.dumps(passport()))
    for header in (
        {"alg": "Ed25519", "kid": OWNER.did, "typ": document.PASSPORT_TYP, "crit": ["b64"]},
        {"alg": "HS256", "kid": OWNER.did, "typ": document.PASSPORT_TYP},
        {"alg": "Ed25519", "typ": document.PASSPORT_TYP},
    ):
        head = jws.b64url(strict.dumps(header))
        sig = jws.b64url(OWNER.sign(f"{head}.{body}".encode()))
        assert refused(f"{head}.{body}.{sig}").rule == "4.4"


def test_sign_refuses_any_key_but_the_owner_the_passport_names() -> None:
    with pytest.raises(ValueError, match=r"owner\.id"):
        document.sign(passport(), INTRUDER)
    with pytest.raises(ValueError, match="robot's own key"):
        document.sign(passport(owner={"id": KEY.did}), KEY)


def test_a_signing_keys_repr_never_shows_its_seed() -> None:
    assert KEY.seed.hex() not in repr(KEY)
    assert KEY.did in repr(KEY)


# ── what the reviews found ──────────────────────────────────────────────────────────────

VECTORS = Path(__file__).resolve().parents[1] / "docs" / "passport" / "vectors"
_B64 = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"


@pytest.mark.parametrize(
    "public_hex",
    [
        "0100000000000000000000000000000000000000000000000000000000000000",  # the identity
        "ecffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff7f",  # order 2
        "0000000000000000000000000000000000000000000000000000000000000000",  # order 4
        "c7176a703d4dd84fba3c0b760d10670f2a2053fa2c39ccc64ec7fd7792ac037a",  # order 8
        "edffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff7f",  # y = p, spelled twice
        "0200000000000000000000000000000000000000000000000000000000000000",  # y = 2, on no curve
    ],
)
def test_a_key_nobody_could_hold_is_refused(public_hex: str) -> None:
    """A small-order "key" verifies a signature nobody made, which libsodium refuses: a passport
    whose owner held one could be signed by anybody."""
    with pytest.raises(PassportError) as caught:
        public_from_did(did_from_public(bytes.fromhex(public_hex)))
    assert caught.value.rule == "rule 7"


def test_a_signature_has_one_spelling() -> None:
    """The last of a signature's 86 characters carries two bits of it and four spare ones, which
    a decoder that ignored them would let anybody flip into fifteen more spellings."""
    head, body, sig = (VECTORS / "humanoid.jws").read_text(encoding="utf-8").strip().split(".")
    bent = sig[:-1] + _B64[_B64.index(sig[-1]) ^ 1]
    with pytest.raises(PassportError) as caught:
        document.read(f"{head}.{body}.{bent}", now=document.epoch("2026-10-29T00:00:00Z", "now"))
    assert caught.value.rule == "4.4"


def test_a_typ_written_with_its_media_type_prefix_is_the_same_typ() -> None:
    """RFC 7515 4.1.9: `application/robot-passport+jws` and `robot-passport+jws` are one value."""
    header = {"alg": "Ed25519", "kid": OWNER.did, "typ": "application/robot-passport+jws"}
    signing_input = jws.b64url(strict.dumps(header)) + "." + jws.b64url(strict.dumps(passport()))
    token = signing_input + "." + jws.b64url(OWNER.sign(signing_input.encode("ascii")))
    assert document.read_jws(token, now=NOW).signed


def test_a_padded_jws_is_refused_as_a_jws_rather_than_as_json() -> None:
    assert refused(document.sign(passport(), OWNER) + "==").rule == "4.4"


def test_numbers_and_versions_no_reader_could_hold_are_refused_not_crashed() -> None:
    assert refused(raw(passport())[:-1] + b',"x":1' + b"0" * 400 + b"}").rule == "rule 2"
    assert refused(raw(passport(body={"mass_kg": 10**30}))).rule == "rule 2"
    assert refused(raw(passport(passport="1." + "9" * 5000))).rule == "rule 3"


def test_rfc_3339_allows_a_lowercase_t_and_z_and_a_leap_second() -> None:
    assert document.epoch("2016-12-31T23:59:60Z", "issued") == document.epoch(
        "2016-12-31T23:59:59Z", "issued"
    )
    reading = document.read(raw(passport(issued="2026-10-20t00:00:00z")), now=NOW)
    assert reading.issued == document.epoch(ISSUED, "issued")


def test_a_required_that_is_not_true_reads_as_not_required() -> None:
    """Spec 2.6 acts on `"required": true` alone, so anything else leaves the extension ignored,
    and the passport is read rather than refused over it."""
    reading = document.read(raw(passport(ext={"org.example": {"required": "yes"}})), now=NOW)
    assert reading.talk_allowed
    assert any("read as not required" in note for note in reading.notes)


def test_the_view_shows_the_members_a_reader_knows_and_none_it_ignored() -> None:
    reading = document.read(
        raw(
            passport(
                owner={"id": OWNER.did, "label": "Lab", "vouched_by": "ACME safety board"},
                board={"kind": "jetson", "firmware": "certified"},
            )
        ),
        now=NOW,
    )
    view = reading.view()
    assert view["owner"] == {"id": OWNER.did, "label": "Lab"}
    assert view["board"] == {"kind": "jetson"}
    assert view["owner_short_id"] == OWNER.short_id
    assert (view["issued"], view["expires"]) == (ISSUED, EXPIRES)


def test_a_did_too_long_to_be_an_ed25519_key_is_refused_before_it_is_decoded() -> None:
    """Decoding base58 costs the square of the length, so a kid of 16 KB in a hello would cost a
    robot real time; one longer than any Ed25519 did:key is refused unread, in no time."""
    import time

    from quackd.passport.keys import MAX_DID

    assert len(KEY.did) <= MAX_DID
    started = time.perf_counter()
    with pytest.raises(PassportError) as refused:
        public_from_did("did:key:z" + "6" * 100_000)
    assert refused.value.rule == "rule 7" and "too long" in str(refused.value)
    assert time.perf_counter() - started < 0.5
