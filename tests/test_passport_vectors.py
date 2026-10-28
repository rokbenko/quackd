"""quackd's passport module against the conformance vectors in docs/passport/vectors/.

The vectors are written by scripts/passport_vectors.py, which signs with `cryptography` directly
and never imports `quackd.passport`, so every row here checks quackd against a second
implementation of the same rules. The expected results are vectors/README.md's, row for row.
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from quackd.passport import PassportError, document, jws, stamps
from quackd.passport.document import Entry

REPO = Path(__file__).resolve().parents[1]
VECTORS = REPO / "docs" / "passport" / "vectors"
NOW = int(datetime(2026, 10, 21, tzinfo=UTC).timestamp())
KEYS = json.loads((VECTORS / "keys.json").read_text(encoding="utf-8"))["keys"]
ROBOT_A = KEYS["robot-a"]["did"]

#: Every passport vector and what a reader must do with it: a reading, or the rule it breaks.
PASSPORTS = {
    "minimal.json": "valid",
    "microduck.json": "valid",
    "unknown-member.json": "valid",
    "unknown-enum.json": "valid",
    "unknown-safety.json": "valid",
    "required-extension.json": "valid",
    "minor-version.json": "valid",
    "missing-name.json": "rule 3",
    "major-version.json": "rule 4",
    "duplicate-member.json": "rule 2",
    "long-name.json": "rule 3",
    "oversized.json": "rule 1",
    "bad-id.json": "rule 3",
    "microduck.jws": "valid",
    "eddsa-label.jws": "valid",
    "tampered.jws": "4.4",
    "wrong-key.jws": "4.4",
    "kid-mismatch.jws": "4.4",
    "uuid-signed.jws": "4.4",
}

STAMPS = {
    "stamp-owner.jwt": True,
    "stamp-expired.jwt": False,
    "stamp-wrong-subject.jwt": False,
    "stamp-bad-signature.jwt": False,
}


def test_the_vectors_are_what_the_script_writes() -> None:
    """A vector edited by hand, or a script changed without re-running it, fails here."""
    done = subprocess.run(
        [sys.executable, str(REPO / "scripts" / "passport_vectors.py"), "--check"],
        capture_output=True,
        text=True,
    )
    assert done.returncode == 0, done.stdout + done.stderr


def test_every_vector_file_has_an_expected_result_here() -> None:
    covered = set(PASSPORTS) | set(STAMPS) | {"index.json", "keys.json", "README.md"}
    talk = {p.name for p in VECTORS.iterdir() if p.name.startswith(("hello", "say", "ask"))}
    # the Bluetooth advert and link frames, whose expected results tests/test_passport_ble.py
    # and tests/test_passport_link_frames.py hold
    ble = {p.name for p in VECTORS.iterdir() if p.name.startswith("ble-")}
    assert {p.name for p in VECTORS.iterdir()} == covered | talk | ble


@pytest.mark.parametrize("name", sorted(PASSPORTS))
def test_a_passport_vector_reads_or_is_refused_as_its_row_says(name: str) -> None:
    expected = PASSPORTS[name]
    data = (VECTORS / name).read_bytes()
    if expected == "valid":
        reading = document.read(data, now=NOW)
        assert isinstance(reading, document.Reading)
        assert reading.signed == name.endswith(".jws")
        return
    with pytest.raises(PassportError) as refused:
        document.read(data, now=NOW)
    assert refused.value.rule == expected, str(refused.value)


def test_the_index_vector_lists_two_robots_on_one_host() -> None:
    entries = document.read((VECTORS / "index.json").read_bytes())
    assert entries == [
        Entry("/robots/duck-01/robot-passport.json", "duck-01", ROBOT_A),
        Entry("/robots/cart-01/robot-passport.json", "cart-01", KEYS["robot-b"]["did"]),
    ]


def test_unknown_values_are_read_rather_than_refused() -> None:
    hexapod = document.read((VECTORS / "unknown-enum.json").read_bytes())
    assert isinstance(hexapod, document.Reading)
    assert hexapod.body()["embodiment"] == "other"
    assert "sonar" in hexapod.body()["sensors"]
    garbled = document.read((VECTORS / "unknown-safety.json").read_bytes())
    assert isinstance(garbled, document.Reading)
    assert garbled.safety() == document.Safety(False, "none", None)
    assert len(garbled.notes) == 2


def test_a_required_extension_nobody_understands_stops_talk_but_not_reading() -> None:
    reading = document.read((VECTORS / "required-extension.json").read_bytes())
    assert isinstance(reading, document.Reading)
    assert reading.talk_allowed is False
    assert any("org.example.lift" in note for note in reading.notes)


def test_the_signed_microduck_carries_one_stamp_that_counts() -> None:
    reading = document.read((VECTORS / "microduck.jws").read_text(encoding="utf-8"), now=NOW)
    assert isinstance(reading, document.Reading)
    assert [s.counts for s in reading.stamps] == [True]
    assert reading.stamps[0].iss == KEYS["owner"]["did"]
    assert reading.short_id == KEYS["robot-a"]["short_id"]


@pytest.mark.parametrize("name", sorted(STAMPS))
def test_a_stamp_vector_counts_or_not_as_its_row_says(name: str) -> None:
    token = (VECTORS / name).read_text(encoding="utf-8").strip()
    judged = stamps.check(token, subject=ROBOT_A, now=NOW)
    assert judged.counts is STAMPS[name], judged.reason


def test_a_signature_made_for_one_purpose_is_refused_for_another() -> None:
    """The typ in the header is the whole reason a key can sign three kinds of thing."""
    message = (VECTORS / "say-in-session.jws").read_text(encoding="utf-8").strip()
    with pytest.raises(PassportError, match="typ"):
        document.read_jws(message)
    stamp = (VECTORS / "stamp-owner.jwt").read_text(encoding="utf-8").strip()
    with pytest.raises(PassportError, match="typ"):
        document.read_jws(stamp)
    passport = (VECTORS / "microduck.jws").read_text(encoding="utf-8").strip()
    assert stamps.check(passport, subject=ROBOT_A, now=NOW).counts is False


@pytest.mark.parametrize("name", ["hello-1.jws", "hello-2.jws", "hello-3.jws", "ask-pause.jws"])
def test_every_signed_talk_vector_verifies_as_a_message(name: str) -> None:
    token = (VECTORS / name).read_text(encoding="utf-8").strip()
    verified = jws.verify_compact(token, "robot-passport-msg+jws", rule="6.1", payload_limit=4096)
    assert json.loads(verified.payload)["from"] == verified.kid
