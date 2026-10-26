"""A passport for every body quackd publishes, built from its manifest by `passport_from_manifest`.

The docs show a Microduck's passport as the one quackd really builds, and the example is a
vector, so the vector is held equal to what the converter makes: the page cannot drift into a
sketch of the code.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from quackd.adapters.factory import ADAPTER_NAMES, BACKENDS, RobotSpec, describe
from quackd.adapters.passport import CANNOT_CHARS, cannot_line, courtesy, passport_from_manifest
from quackd.passport import SigningKey, document, strict

REPO = Path(__file__).resolve().parents[1]
VECTORS = REPO / "docs" / "passport" / "vectors"
KEY = SigningKey(bytes(range(32)))


def _manifest(adapter: str):  # type: ignore[no-untyped-def]
    backend = "mock" if "mock" in BACKENDS[adapter] else BACKENDS[adapter][0]
    return describe(RobotSpec(adapter, backend, adapter))


def _built(adapter: str) -> dict[str, object]:
    return passport_from_manifest(
        _manifest(adapter),
        robot_id=KEY.did,
        name=adapter.replace("_", "-"),
        software_version="0.23.0",
        owner={"label": "Example lab", "contact": "https://example.org/robots"},
        estop="power",
        how_to_stop="the power switch",
        talk=[{"via": "http", "url": "http://192.0.2.10:8790/robot-passport/inbox"}],
    )


@pytest.mark.parametrize("adapter", ADAPTER_NAMES)
def test_every_body_gets_a_passport_a_reader_accepts_and_a_writer_may_send(adapter: str) -> None:
    doc = _built(adapter)
    raw = strict.dumps(doc)
    reading = document.read_json(raw)
    assert reading.notes == (), reading.notes
    assert len(raw) <= document.WRITER_BYTES, f"{adapter}: {len(raw)} bytes"
    manifest = _manifest(adapter)
    assert reading.skills() == [verb.name for verb in manifest.verbs]
    assert "stop" in reading.skills()
    assert reading.safety().stops_on_silence is manifest.safety_authority.deadman


def test_the_microduck_vector_is_what_the_converter_builds() -> None:
    expected = json.loads((VECTORS / "microduck.json").read_text(encoding="utf-8"))
    built = passport_from_manifest(
        # a backend that says nothing of where the duck is, as every Microduck backend did when
        # the vector was frozen: the mock and a robotd now add the place verbs to its skills, and
        # the published vectors stay what they were
        describe(RobotSpec("microduck", "sim2d", "duck-01")),
        robot_id=expected["id"],
        name="duck-01",
        software_version="0.23.0",
        owner=expected["owner"],
        estop="power",
        how_to_stop="the power switch",
        talk=expected["talk"],
        issued=datetime(2026, 10, 21, tzinfo=UTC),
    )
    assert built == expected


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
        passport_from_manifest(
            _manifest("microduck"),
            robot_id=KEY.did,
            name="d",
            software_version="0",
            estop="lever",
        )


def test_no_owner_leaves_owner_out_and_no_estop_reads_as_the_worst_case() -> None:
    doc = passport_from_manifest(
        _manifest("lerobot"), robot_id=KEY.did, name="arm", software_version="0.23.0"
    )
    assert "owner" not in doc
    reading = document.read_json(strict.dumps(doc))
    assert reading.safety() == document.Safety(False, "none", None)


def test_the_same_robot_builds_the_same_bytes_every_time() -> None:
    """A reader caches by digest (spec 5.8), so a passport whose robot has not changed must not
    change either: no build time inside it, nothing that differs from one build to the next."""
    first = strict.dumps(_built("microduck"))
    assert strict.dumps(_built("microduck")) == first
    assert "issued" not in json.loads(first)


def test_a_verb_name_a_passport_cannot_carry_is_written_as_one_or_left_out() -> None:
    """A manifest allows a hyphen, a leading digit and 64 characters, and a passport's skills
    allow none of them: quackd writes the first as an underscore and leaves the rest out, so it
    never serves a passport its own reader refuses."""
    manifest = _manifest("microduck")
    first = manifest.verbs[0]
    odd = [first.model_copy(update={"name": n}) for n in ("look-around", "2step", "x" * 40, "stop")]
    built = passport_from_manifest(
        manifest.model_copy(update={"verbs": odd}),
        robot_id=KEY.did,
        name="duck-01",
        software_version="0.23.0",
    )
    assert built["skills"] == ["look_around", "stop"]
