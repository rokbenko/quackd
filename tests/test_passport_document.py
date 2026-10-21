"""The reading rules of docs/passport/spec.md section 3, one at a time, on documents built here.

The vectors check the cases the spec names; these check the edges a vector would be clumsy for:
a byte order mark, NaN, a duplicate three objects deep, nesting that would blow the stack, a
did:key spelled two ways, and every member a writer can get the type of wrong.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from quackd.passport import PassportError, SigningKey, document, jws, stamps, strict
from quackd.passport.keys import b58decode, b58encode, did_from_public, public_from_did

KEY = SigningKey(bytes(range(32)))
OWNER = SigningKey(bytes(range(32, 64)))
NOW = 1_792_540_800


def passport(**members: Any) -> dict[str, Any]:
    return {"passport": "0.1", "id": KEY.did, "name": "duck-01", **members}


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
    assert isinstance(document.read(raw(passport(padding="x" * pad))), document.Reading)
    assert refused(raw(passport(padding="x" * (pad + 1)))).rule == "rule 1"


def test_a_byte_order_mark_nan_and_non_utf8_are_refused() -> None:
    assert refused(b"\xef\xbb\xbf" + raw(passport())).rule == "rule 2"
    assert refused(raw(passport())[:-1] + b',"x":NaN}').rule == "rule 2"
    assert refused(b'{"passport":"0.1","name":"\xff"}').rule == "rule 2"


def test_a_duplicate_member_is_refused_at_any_depth() -> None:
    nested = b'{"passport":"0.1","id":"%s","name":"d","body":{"sensors":[],"sensors":[]}}' % (
        KEY.did.encode()
    )
    error = refused(nested)
    assert error.rule == "rule 2" and "sensors" in str(error)


def test_nesting_deep_enough_to_blow_the_stack_is_refused_not_crashed() -> None:
    assert refused(b"[" * 4000 + b"]" * 4000).rule == "rule 2"  # 8000 bytes: under rule 1


def test_something_that_is_not_an_object_is_refused() -> None:
    assert refused(b"[1, 2]").rule == "rule 2"


# ── rules 3, 4, 5 and 7: the members ────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("members", "rule"),
    [
        ({"passport": "0"}, "rule 3"),
        ({"passport": "v0.1"}, "rule 3"),
        ({"passport": "2.0"}, "rule 4"),
        ({"name": ""}, "rule 3"),
        ({"name": 7}, "rule 3"),
        ({"id": "did:key:zQ3sharpXYZ"}, "rule 7"),
        ({"id": "urn:uuid:not-a-uuid"}, "rule 3"),
        ({"maker": {"vendor": "v" * 65}}, "rule 5"),
        ({"owner": {"contact": "c" * 201}}, "rule 5"),
        ({"body": {"mass_kg": -1}}, "rule 5"),
        ({"body": {"mass_kg": True}}, "rule 5"),
        ({"body": {"mass_kg": [1, "official", "s", "extra"]}}, "rule 5"),
        ({"body": {"tethered": "yes"}}, "rule 5"),
        ({"skills": ["Kick"]}, "rule 5"),
        ({"skills": ["a" * 33]}, "rule 5"),
        ({"cannot": [""]}, "rule 5"),
        ({"cannot": ["x"] * 9}, "rule 5"),
        ({"talk": [{"via": "http"}]}, "rule 5"),
        ({"talk": [{"via": "http", "url": "u", "max_bytes": 100}]}, "rule 5"),
        ({"stamps": ["not.a"]}, "rule 5"),
        ({"ext": {"NoDots": {}}}, "rule 5"),
        ({"ext": {"org.example": {"required": "yes"}}}, "rule 5"),
        ({"issued": "yesterday"}, "rule 5"),
        ({"issued": "2026-13-45T00:00:00Z"}, "rule 5"),
        ({"ttl_s": 59}, "rule 5"),
        ({"ttl_s": True}, "rule 5"),
    ],
)
def test_a_member_of_the_wrong_shape_is_refused_under_its_rule(
    members: dict[str, Any], rule: str
) -> None:
    error = refused(raw(passport(**members)))
    assert error.rule == rule, str(error)


def test_a_urn_uuid_passport_is_read_and_has_no_short_id() -> None:
    reading = document.read(raw(passport(id="urn:uuid:4f1c2a1e-9d4b-4c8e-8f3a-2b6d5e7a9c10")))
    assert isinstance(reading, document.Reading)
    assert reading.short_id is None


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
        reading = document.read(raw(passport(**members)))
        assert isinstance(reading, document.Reading)
        assert reading.safety() == expected


def test_unknown_members_courtesies_and_channels_are_ignored_with_a_note() -> None:
    reading = document.read(
        raw(
            passport(
                aura="calm",
                maker={"vendor": "v", "colour": "teal"},
                courtesy=["pause", "dance"],
                talk=[{"via": "carrier-pigeon", "url": "coop://1"}, {"via": "http", "url": "h"}],
            )
        )
    )
    assert isinstance(reading, document.Reading)
    assert reading.courtesy() == ["pause"]
    assert [c["via"] for c in reading.channels()] == ["http"]
    notes = " ".join(reading.notes)
    for word in ("aura", "maker.colour", "dance", "carrier-pigeon"):
        assert word in notes


def test_quackds_own_required_extension_does_not_stop_talk() -> None:
    reading = document.read(raw(passport(ext={"org.quackd": {"required": True, "sha": "x"}})))
    assert isinstance(reading, document.Reading)
    assert reading.talk_allowed is True


def test_an_index_is_not_a_passport_and_its_urls_are_checked() -> None:
    index = {"passport": "0.1", "robots": [{"url": "/r/a.json", "name": "a"}]}
    assert document.read(raw(index)) == [document.Entry("/r/a.json", "a", None)]
    with pytest.raises(PassportError):
        document.read_json(raw(index))
    assert refused(raw({"passport": "0.1", "robots": []})).rule == "rule 5"
    assert refused(raw({"passport": "0.1", "robots": [{"name": "a"}]})).rule == "rule 5"


# ── signatures and stamps ───────────────────────────────────────────────────────────────


def test_a_passport_signed_by_its_own_key_reads_as_signed_and_keeps_its_bytes() -> None:
    doc = passport(stamps=[stamps.make(OWNER, KEY.did, now=NOW)])
    token = document.sign(doc, KEY)
    reading = document.read(token, now=NOW)
    assert isinstance(reading, document.Reading)
    assert reading.signed and reading.raw == strict.dumps(doc)
    assert [s.counts for s in reading.stamps] == [True]


def test_a_header_with_crit_or_another_alg_is_refused() -> None:
    body = jws.b64url(strict.dumps(passport()))
    for header in (
        {"alg": "Ed25519", "kid": KEY.did, "typ": document.PASSPORT_TYP, "crit": ["b64"]},
        {"alg": "HS256", "kid": KEY.did, "typ": document.PASSPORT_TYP},
        {"alg": "Ed25519", "typ": document.PASSPORT_TYP},
    ):
        head = jws.b64url(strict.dumps(header))
        sig = jws.b64url(KEY.sign(f"{head}.{body}".encode()))
        assert refused(f"{head}.{body}.{sig}").rule == "4.4"


def test_sign_refuses_a_key_the_passport_does_not_name() -> None:
    with pytest.raises(ValueError):
        document.sign(passport(), OWNER)


def test_a_stamp_issued_in_the_future_or_backwards_does_not_count() -> None:
    ahead = stamps.make(OWNER, KEY.did, now=NOW + 3600)
    assert stamps.check(ahead, subject=KEY.did, now=NOW).counts is False
    backwards = jws.sign(
        strict.dumps({"iss": OWNER.did, "sub": KEY.did, "rel": "owner", "iat": NOW, "exp": NOW}),
        OWNER,
        stamps.STAMP_TYP,
    )
    assert stamps.check(backwards, subject=KEY.did, now=NOW).counts is False
    maker = jws.sign(
        strict.dumps(
            {"iss": OWNER.did, "sub": KEY.did, "rel": "maker", "iat": NOW, "exp": NOW + 99}
        ),
        OWNER,
        stamps.STAMP_TYP,
    )
    assert "maker" in (stamps.check(maker, subject=KEY.did, now=NOW).reason or "")


def test_a_signing_keys_repr_never_shows_its_seed() -> None:
    assert KEY.seed.hex() not in repr(KEY)
    assert KEY.did in repr(KEY)


def test_a_stamp_on_an_unsigned_passport_never_counts() -> None:
    """The stamp checks out, but it vouches for a key, and nothing shows that key wrote an
    unsigned passport: anybody could have copied the id and the stamp into it."""
    stamp = stamps.make(OWNER, KEY.did, now=NOW)
    copied = document.read(raw(passport(stamps=[stamp])), now=NOW)
    assert isinstance(copied, document.Reading)
    assert [s.counts for s in copied.stamps] == [False]
    assert copied.stamps[0].reason == document.UNSIGNED_STAMP
    signed = document.read(document.sign(passport(stamps=[stamp]), KEY), now=NOW)
    assert isinstance(signed, document.Reading)
    assert [s.counts for s in signed.stamps] == [True]
