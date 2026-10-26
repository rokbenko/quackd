"""The manifest is the source of truth for what a robot can do; these tests pin its rules."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

import quackd.adapters
from quackd.adapters.manifest import (
    Datasheet,
    Figure,
    RobotManifest,
    Span,
    VerbSpec,
    apply_datasheet_override,
    manifest_json_schema,
)
from quackd.verbs.registry import ManifestError, default_registry, registry_from_manifest
from quackd_microduck import microduck_manifest


def _manifest(**over: Any) -> RobotManifest:
    base: dict[str, Any] = {
        "id": "bot-01",
        "vendor": "acme",
        "model": "bot",
        "embodiment": "wheeled",
        "mobility": "wheeled",
        "intents": ["twist", "sound"],
        "sensors": ["camera"],
        "verbs": [VerbSpec(name="observe", core=True), VerbSpec(name="move", core=True)],
    }
    base.update(over)
    return RobotManifest(**base)


def test_round_trips_through_json() -> None:
    m = microduck_manifest("sim2d")
    assert RobotManifest.model_validate_json(m.model_dump_json()) == m


def test_stop_is_always_present_and_never_gated() -> None:
    m = _manifest()
    stop = m.verb("stop")
    assert stop is not None and stop.core and stop.safety_class == "safe"
    with pytest.raises(ValidationError, match="never be gated"):
        _manifest(verbs=[VerbSpec(name="stop", safety_class="confirm")])


def test_alias_names_are_rejected() -> None:
    with pytest.raises(ValidationError, match="not its alias"):
        _manifest(verbs=[VerbSpec(name="walk")])


def test_core_requirements_are_checked_on_the_manifest() -> None:
    with pytest.raises(ValidationError, match="needs mobility"):
        _manifest(mobility="none", verbs=[VerbSpec(name="go_to", core=True)])
    with pytest.raises(ValidationError, match="needs a camera"):
        _manifest(sensors=[], verbs=[VerbSpec(name="observe", core=True)])
    with pytest.raises(ValidationError, match="needs the sound intent"):
        _manifest(intents=["twist"], verbs=[VerbSpec(name="say", core=True)])
    with pytest.raises(ValidationError, match="not a core verb"):
        _manifest(verbs=[VerbSpec(name="dance", core=True)])
    # an extension may be called anything; a precondition may not name an undeclared verb
    _manifest(verbs=[VerbSpec(name="dance")])
    with pytest.raises(ValidationError, match="undeclared verb"):
        _manifest(preconditions={"fly": ["standing"]})


def test_extra_keys_and_bad_ids_are_rejected() -> None:
    with pytest.raises(ValidationError):
        _manifest(colour="red")
    with pytest.raises(ValidationError, match="id must match"):
        _manifest(id="Not A Slug")
    with pytest.raises(ValidationError, match="duplicates"):
        _manifest(intents=["twist", "twist"])


def test_digest_is_a_capability_fingerprint() -> None:
    # the backend and the id are not capabilities: two backends that offer the same verbs match
    a = microduck_manifest("sim2d", "duck-a")
    b = microduck_manifest("mujoco", "duck-b")
    assert a.digest() == b.digest() and len(a.digest()) == 16
    assert _manifest().digest() != a.digest()
    # and one that says where the duck is offers more, which the fingerprint shows
    assert microduck_manifest("mock", "duck-c").digest() != a.digest()


def test_provides_is_alias_aware() -> None:
    m = microduck_manifest("sim2d")
    assert m.provides("walk") and m.provides("move") and not m.provides("fly")
    spec = m.verb("get_frame")
    assert spec is not None and spec.name == "observe" and spec.core


def test_microduck_manifest_matches_the_default_registry() -> None:
    assert set(microduck_manifest("sim2d").verb_names()) == set(default_registry().names())


def test_registry_from_manifest_only_builds_declared_verbs() -> None:
    wheeled = registry_from_manifest(_manifest(intents=["twist"]))
    assert wheeled.names() == ["observe", "move", "stop"]  # no say without sound
    legless = registry_from_manifest(
        _manifest(
            mobility="none",
            intents=["gaze"],
            verbs=[VerbSpec(name="observe", core=True), VerbSpec(name="search_scan", core=True)],
        )
    )
    assert legless.names() == ["observe", "search_scan", "stop"]
    assert "move" not in legless and "walk_to" not in legless


def test_registry_from_manifest_refuses_what_no_code_implements() -> None:
    with pytest.raises(ManifestError, match="no implementation"):
        registry_from_manifest(_manifest(verbs=[VerbSpec(name="dance")]))
    with pytest.raises(ManifestError, match="no predicate"):
        registry_from_manifest(_manifest(preconditions={"move": ["upright"]}))


def test_manifest_schema_on_disk_is_current() -> None:
    on_disk = json.loads(
        (Path(quackd.adapters.__file__).with_name("manifest.schema.json")).read_text(
            encoding="utf-8"
        )
    )
    assert on_disk == manifest_json_schema(), "run: uv run python -m quackd.adapters.export"


# ── the datasheet ───────────────────────────────────────────────────────────────────────


def test_a_figure_needs_a_source_and_a_known_confidence() -> None:
    with pytest.raises(ValidationError, match="at least 1 character"):
        Figure(value=1.0, confidence="official", source="")
    with pytest.raises(ValidationError, match="confidence"):
        Figure(value=1.0, confidence="hearsay", source="somebody")  # type: ignore[arg-type]
    with pytest.raises(ValidationError, match="greater than or equal to 0"):
        Figure(value=-1.0, confidence="official", source="somebody")


def test_a_span_runs_low_to_high() -> None:
    with pytest.raises(ValidationError, match="exceeds"):
        Span(low=2.0, high=1.0, confidence="official", source="the docs")
    assert (
        Span(low=0.5, high=1.25, confidence="official", source="the docs")
        .text("m")
        .startswith("0.5 to 1.25 m")
    )


def test_datasheet_sentences_start_with_a_word() -> None:
    # a leading backtick renders as the shape the prompt spells an offered verb with
    with pytest.raises(ValidationError, match="start with a word"):
        Datasheet(manipulator="none", cannot=["`move` is not for you"])
    with pytest.raises(ValidationError, match="an empty sentence"):
        Datasheet(manipulator="none", notes=["   "])
    with pytest.raises(ValidationError, match="longer than 300 characters"):
        Datasheet(manipulator="none", notes=["x " * 200])


def test_hands_and_payload_have_to_agree() -> None:
    with pytest.raises(ValidationError, match="no payload"):
        Datasheet(
            manipulator="none",
            payload_kg=Figure(value=1.0, confidence="official", source="the docs"),
        )
    with pytest.raises(ValidationError, match="needs arms of at least 1"):
        Datasheet(manipulator="gripper", arms=0)
    with pytest.raises(ValidationError, match="arms without a manipulator"):
        Datasheet(manipulator="none", arms=2)


def test_the_datasheet_is_part_of_the_capability_fingerprint() -> None:
    light = _manifest(datasheet=Datasheet(manipulator="none"))
    heavy = _manifest(
        datasheet=Datasheet(
            manipulator="gripper",
            arms=1,
            payload_kg=Figure(value=1.0, confidence="official", source="the docs"),
        )
    )
    assert light.digest() != heavy.digest()


def test_the_task_file_replaces_figures_and_only_ever_adds_sentences() -> None:
    from quackd.duckfile.schema import DatasheetOverride

    base = microduck_manifest("sim2d")
    assert base.datasheet is not None
    merged = apply_datasheet_override(
        base,
        DatasheetOverride.model_validate(
            {"payload_kg": 0.1, "cannot": ["lift the lid off anything"]}
        ),
    )
    assert merged.datasheet is not None
    assert merged.datasheet.payload_kg is not None
    assert merged.datasheet.payload_kg.value == 0.1
    assert merged.datasheet.payload_kg.source == "the task file"
    assert merged.datasheet.payload_kg.confidence == "estimate"  # the file did not say
    assert merged.datasheet.cannot == [*base.datasheet.cannot, "lift the lid off anything"]
    assert merged.datasheet.mass_kg == base.datasheet.mass_kg  # untouched
    assert base.datasheet.payload_kg is None, "the robot's own sheet is not mutated"
    assert apply_datasheet_override(base, None) is base


def test_a_task_file_cannot_hand_an_armless_body_a_payload() -> None:
    from quackd.duckfile.schema import DatasheetOverride

    with pytest.raises(ValidationError, match="no payload"):
        apply_datasheet_override(
            _manifest(datasheet=Datasheet(manipulator="none")),
            DatasheetOverride.model_validate({"payload_kg": 3.0}),
        )
