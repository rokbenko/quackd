"""quackd's passport module against the conformance vectors in docs/passport/vectors/.

The vectors are written by scripts/passport_vectors.py, which signs with `cryptography` directly
and never imports `quackd.passport`, so every row here checks quackd against a second
implementation of the same rules. The expected results are vectors/README.md's, row for row.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from quackd.passport import PassportError, document, jws
from quackd.passport.talk import MAX_HELLO_BYTES, read_message
from tests.passport_helpers import KEYS, REFERENCE

REPO = Path(__file__).resolve().parents[1]
VECTORS = REPO / "docs" / "passport" / "vectors"

#: Every passport vector and what a reader must do with it: a reading, or the rule it breaks.
PASSPORTS = {
    "minimal.json": "valid",
    "humanoid.json": "valid",
    "unknown-member.json": "valid",
    "unknown-enum.json": "valid",
    "unknown-safety.json": "valid",
    "required-extension.json": "valid",
    "minor-version.json": "valid",
    "missing-name.json": "rule 3",
    "missing-owner.json": "rule 3",
    "missing-expires.json": "rule 3",
    "major-version.json": "rule 4",
    "old-version.json": "rule 4",
    "duplicate-member.json": "rule 2",
    "long-name.json": "rule 3",
    "oversized.json": "rule 1",
    "bad-id.json": "rule 3",
    "too-long-validity.json": "rule 5",
    "humanoid.jws": "valid",
    "eddsa-label.jws": "valid",
    "tampered.jws": "4.4",
    "wrong-key.jws": "4.4",
    "kid-mismatch.jws": "4.4",
    "robot-signed.jws": "4.4",
    "same-key.jws": "4.4",
    "expired.jws": "4.5",
    "not-yet-valid.jws": "4.5",
}

#: Every talk vector and what a receiver makes of it: read, or the rule it breaks.
MESSAGES = {
    "hello-1.jws": "valid",
    "hello-2.jws": "valid",
    "hello-3.jws": "valid",
    "say-in-session.jws": "valid",
    "say-replayed.jws": "valid",
    "ask-pause.jws": "valid",
    "hello-foreign-passport.jws": "valid",
    "unsigned-say.json": "6.1",
}


def test_the_vectors_are_what_the_script_writes() -> None:
    """A vector edited by hand, or a script changed without re-running it, fails here."""
    done = subprocess.run(
        [sys.executable, str(REPO / "scripts" / "passport_vectors.py"), "--check"],
        capture_output=True,
        text=True,
    )
    assert done.returncode == 0, done.stdout + done.stderr


def _not_mine() -> set[str]:
    """Vectors other tests hold the module to (the Bluetooth advert and link frames)."""
    return {p.name for p in VECTORS.iterdir() if p.name.startswith("ble-")}


def test_every_vector_file_has_an_expected_result_here() -> None:
    covered = set(PASSPORTS) | set(MESSAGES) | {"keys.json", "README.md"}
    present = {p.name for p in VECTORS.iterdir()}
    assert present - _not_mine() == covered


@pytest.mark.parametrize("name", sorted(PASSPORTS))
def test_a_passport_vector_reads_or_is_refused_as_its_row_says(name: str) -> None:
    expected = PASSPORTS[name]
    data = (VECTORS / name).read_bytes()
    if expected == "valid":
        reading = document.read(data, now=REFERENCE)
        assert isinstance(reading, document.Reading)
        assert reading.signed == name.endswith(".jws")
        return
    with pytest.raises(PassportError) as refused:
        document.read(data, now=REFERENCE)
    assert refused.value.rule == expected, str(refused.value)


@pytest.mark.parametrize("name", sorted(MESSAGES))
def test_a_talk_vector_reads_or_is_refused_as_its_row_says(name: str) -> None:
    expected = MESSAGES[name]
    data = (VECTORS / name).read_bytes().strip()
    if expected == "valid":
        assert read_message(data).sender in {k["did"] for k in KEYS.values()}
        return
    with pytest.raises(PassportError) as refused:
        read_message(data)
    assert refused.value.rule == expected, str(refused.value)


def test_the_signed_humanoid_is_signed_by_its_owner_and_names_its_robot() -> None:
    reading = document.read((VECTORS / "humanoid.jws").read_bytes(), now=REFERENCE)
    assert reading.signed
    assert reading.owner_id == KEYS["owner"]["did"]
    assert reading.short_id == KEYS["robot-a"]["short_id"]
    assert reading.board() == {"kind": "sim", "model": "a computer standing in for a robot's own"}


def test_an_owner_signature_holds_only_between_its_dates() -> None:
    """The same expired passport is refused at the reference time and read before it ended."""
    token = (VECTORS / "expired.jws").read_text(encoding="utf-8").strip()
    reading = document.read_jws(token, now=document.epoch("2025-06-01T00:00:00Z", "now"))
    assert reading.signed
    with pytest.raises(PassportError, match="expired"):
        document.read_jws(token, now=REFERENCE)


def test_a_passport_issued_ahead_of_the_clock_is_refused_until_the_skew_allows_it() -> None:
    token = (VECTORS / "not-yet-valid.jws").read_text(encoding="utf-8").strip()
    issued = document.epoch("2026-11-01T00:00:00Z", "issued")
    with pytest.raises(PassportError, match="ahead of this clock"):
        document.read_jws(token, now=issued - document.CLOCK_SKEW_S - 1)
    assert document.read_jws(token, now=issued - document.CLOCK_SKEW_S).signed


def test_unknown_values_are_read_rather_than_refused() -> None:
    hexapod = document.read((VECTORS / "unknown-enum.json").read_bytes(), now=REFERENCE)
    assert hexapod.body()["embodiment"] == "other"
    assert "sonar" in hexapod.body()["sensors"]
    assert hexapod.board()["kind"] == "other"
    garbled = document.read((VECTORS / "unknown-safety.json").read_bytes(), now=REFERENCE)
    assert garbled.safety() == document.Safety(False, "none", None)
    assert len(garbled.notes) == 2


def test_a_required_extension_nobody_understands_stops_talk_but_not_reading() -> None:
    reading = document.read((VECTORS / "required-extension.json").read_bytes(), now=REFERENCE)
    assert reading.talk_allowed is False
    assert any("org.example.lift" in note for note in reading.notes)


def test_a_0x_passport_is_refused_with_what_it_was() -> None:
    with pytest.raises(PassportError, match="before owners signed passports") as refused:
        document.read((VECTORS / "old-version.json").read_bytes())
    assert refused.value.rule == "rule 4"


def test_a_signature_made_for_one_purpose_is_refused_for_another() -> None:
    """The typ in the header is the whole reason one key can sign two kinds of thing."""
    message = (VECTORS / "say-in-session.jws").read_text(encoding="utf-8").strip()
    with pytest.raises(PassportError, match="typ"):
        document.read_jws(message)
    passport = (VECTORS / "humanoid.jws").read_text(encoding="utf-8").strip()
    with pytest.raises(PassportError, match="typ"):
        read_message(passport)


@pytest.mark.parametrize("name", ["hello-1.jws", "hello-2.jws", "hello-3.jws", "ask-pause.jws"])
def test_every_signed_talk_vector_verifies_as_a_message(name: str) -> None:
    token = (VECTORS / name).read_text(encoding="utf-8").strip()
    verified = jws.verify_compact(
        token, "robot-passport-msg+jws", rule="6.1", payload_limit=MAX_HELLO_BYTES
    )
    assert json.loads(verified.payload)["from"] == verified.kid
