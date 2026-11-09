"""What rides on a body's back: the figure, the need that reads it, and what a peer sees of it.

`carries_kg` is a figure a pilot can be held to, and the one figure a datasheet leaves out of
its dump when nobody said it, so a body with none serialises as it did before the field
existed (ADR-0079). What these tests hold: it is allowed with nothing to hold with and
payload's rule is left alone, the prompt shows it and never lists it as a gap, a task that
needs it is judged against it and a silence is not a yes, a peer on an older release is
handed nothing new and refuses only what it cannot read, a passport stays as the spec wrote
it, and the two quadrupeds say what their maker's page said on the day.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import ValidationError

from quackd import verdict as verdict_module
from quackd.adapters.catalogue import DESCRIBED, DESCRIBED_BODIES
from quackd.adapters.factory import ADAPTER_NAMES, BACKENDS, RobotSpec, describe
from quackd.adapters.manifest import (
    TASK_FILE,
    Datasheet,
    Figure,
    RobotManifest,
    Span,
    VerbSpec,
    apply_datasheet_override,
)
from quackd.adapters.passport import passport_from_manifest
from quackd.agent.prompts import ASSESS_TASK, body_lines, body_summary
from quackd.duckfile.parser import parse_duck_text
from quackd.duckfile.schema import DatasheetOverride, FlockRole
from quackd.flock.messages import BidMsg, FlockTask, TaskMsg
from quackd.flock.mqtt_bus import MqttBus, decode, encode
from quackd.mcp_server import NEEDS_WORDS_TEXT
from quackd.onboard import config
from quackd.passport import document
from quackd.verdict import (
    Verdict,
    check_needs,
    missing_needs,
    missing_needs_in,
    needs_properties,
    own_sheet_objection,
    solo_hint,
)
from tests.passport_helpers import OWNER, A

BACK = Figure(value=8.0, confidence="official", source="the maker's page", note="12 kg at most")
HANDS = Figure(value=1.0, confidence="official", source="the maker's page")
SPECS = [RobotSpec(adapter, backend) for adapter in ADAPTER_NAMES for backend in BACKENDS[adapter]]
SPECS += [RobotSpec(DESCRIBED, body) for body in DESCRIBED_BODIES]


def _sheet(**over: Any) -> Datasheet:
    return Datasheet(manipulator="none", tethered=False, **over)


def _hound(sheet: Datasheet | None = None) -> RobotManifest:
    return RobotManifest(
        id="hound-01",
        vendor="acme",
        model="hound",
        embodiment="quadruped",
        mobility="legged",
        intents=["twist"],
        verbs=[VerbSpec(name="move", core=True)],
        datasheet=sheet if sheet is not None else _sheet(),
    )


# ── the figure ─────────────────────────────────────────────────────────────────────────────────


def test_a_body_with_nothing_to_hold_with_can_say_what_rides_on_it() -> None:
    sheet = _sheet(carries_kg=BACK)
    assert sheet.manipulator == "none" and sheet.carries_kg == BACK
    # a body with hands can say it too: what a hand holds and what rides on the back are two
    # questions, and a body may answer one, the other or both
    both = Datasheet(manipulator="gripper", arms=1, payload_kg=HANDS, carries_kg=BACK)
    assert both.payload_kg == HANDS and both.carries_kg == BACK


@pytest.mark.parametrize("field", ["payload_kg", "reach_m"])
def test_payloads_rule_with_no_manipulator_is_what_it_was(field: str) -> None:
    with pytest.raises(ValidationError, match="no payload and no reach"):
        _sheet(**{field: HANDS})


@pytest.mark.parametrize("key", ["carries_kgs", "carry_kg", "carried_kg"])
def test_a_misspelled_key_is_refused_where_a_datasheet_is_written(key: str) -> None:
    over: dict[str, Any] = {key: BACK}
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        Datasheet(manipulator="none", **over)
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        DatasheetOverride(**{key: 5})
    with pytest.raises(ValueError, match="datasheet vocabulary"):
        check_needs({key: 5})


# ── the prompt ─────────────────────────────────────────────────────────────────────────────────


def test_the_prompt_shows_it_where_a_body_says_it() -> None:
    manifest = _hound(_sheet(carries_kg=BACK))
    line = "- Load on its back or deck: 8 kg (official: the maker's page; 12 kg at most)"
    assert line in body_lines(manifest)
    summary = body_summary(manifest)
    assert "load on its back or deck 8 kg (official: the maker's page; 12 kg at most)" in summary


def test_it_is_never_listed_as_a_gap_where_a_body_does_not() -> None:
    """Working height's rule, for the reason ADR-0079 gives: most bodies have no back to speak
    of, so a silence is only a gap where a task asks for one, and then the gate says so."""
    silent = _sheet()
    assert not any("back" in label or "load" in label for label in silent.unknown())
    assert silent.known() == []
    assert "Load on its back" not in "\n".join(body_lines(_hound(silent)))
    # payload and reach are still left out with nothing to hold with, and endurance still shown
    assert silent.unknown() == ["mass", "height", "actuated joints", "endurance"]


def test_it_comes_after_the_figures_and_before_the_working_height() -> None:
    sheet = Datasheet(
        manipulator="gripper",
        arms=1,
        mass_kg=HANDS,
        carries_kg=BACK,
        workspace_height_m=Span(low=0.5, high=1.2, confidence="official", source="the page"),
    )
    labels = [label for label, _figure, _unit in sheet.known()]
    assert labels == ["Mass", "Load on its back or deck", "Working height"]


# ── the need that reads it ─────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("want", "figure", "lacking"),
    [
        (10.0, BACK, ["carries_kg >= 10 (has 8)"]),
        (8.0, BACK, []),
        (5.0, BACK, []),
        (10.0, None, ["carries_kg >= 10 (not published)"]),
        (0.0, None, []),  # a zero asks for nothing, on every number
    ],
)
def test_a_need_for_a_back_load_is_judged_against_the_figure(
    want: float, figure: Figure | None, lacking: list[str]
) -> None:
    hound = _hound(_sheet(carries_kg=figure))
    assert missing_needs({"carries_kg": want}, hound) == lacking
    # the pilot's own sheet is read no more kindly: nothing else on a sheet speaks to a back,
    # so a silence here is an answer nobody gave, in the verdict gate as in the matcher
    assert missing_needs({"carries_kg": want}, hound, own_sheet=True) == lacking


def test_a_feasible_on_a_back_nobody_rated_is_refused_and_named() -> None:
    objection = own_sheet_objection({"carries_kg": 10.0}, _hound())
    assert objection is not None and "carries_kg >= 10 (not published)" in objection
    assert own_sheet_objection({"carries_kg": 8.0}, _hound(_sheet(carries_kg=BACK))) is None


def test_what_a_hand_holds_is_no_answer_for_the_back_and_the_back_none_for_a_hand() -> None:
    only_hands = _hound(Datasheet(manipulator="gripper", arms=1, payload_kg=HANDS))
    assert missing_needs({"carries_kg": 1.0}, only_hands) == ["carries_kg >= 1 (not published)"]
    only_back = _hound(_sheet(carries_kg=BACK))
    assert missing_needs({"payload_kg": 1.0}, only_back) == ["payload_kg >= 1 (not published)"]
    assert missing_needs({"manipulator": "any"}, only_back) == ["manipulator = any (has none)"]


def test_the_word_is_in_the_vocabulary_a_pilot_is_shown() -> None:
    assert check_needs({"carries_kg": 10}) == {"carries_kg": 10.0}
    verdict = Verdict(verdict="uncertain", reason="the crate", needs={"carries_kg": 8})
    assert verdict.needs == {"carries_kg": 8.0}
    with pytest.raises(ValueError, match="must be a number"):
        check_needs({"carries_kg": "a lot"})
    with pytest.raises(ValueError, match="0 or more"):
        check_needs({"carries_kg": -1})
    spec = needs_properties()["carries_kg"]
    assert spec["type"] == "number" and "back or deck" in spec["description"]
    tool: dict[str, Any] = ASSESS_TASK
    props = tool["input_schema"]["properties"]
    assert "carries_kg" in props["limits_consulted"]["description"]
    assert "carries_kg" in props["needs"]["properties"]
    # MCP types `needs` as a bare dict, so this sentence is the only place its client learns it
    assert "carries_kg" in NEEDS_WORDS_TEXT and "back or deck" in NEEDS_WORDS_TEXT


def test_a_task_file_can_say_it_for_the_build_in_front_of_it() -> None:
    duck = parse_duck_text(
        "---\n"
        "duck: 2\n"
        "name: haul\n"
        "description: Carry the crate.\n"
        "robots: lerobot:mock\n"
        "requires: [report_state]\n"
        "verbs:\n"
        "  allow: [report_state, stop]\n"
        "success: [The crate is on the far shelf.]\n"
        "datasheet:\n"
        "  carries_kg: 5\n"
        "---\n"
        "# Task\n"
        "Carry it.\n"
    )
    assert duck.frontmatter.datasheet is not None
    merged = apply_datasheet_override(_hound(), duck.frontmatter.datasheet)
    assert merged.datasheet is not None and merged.datasheet.carries_kg is not None
    assert merged.datasheet.carries_kg.value == 5
    assert merged.datasheet.carries_kg.source == TASK_FILE
    assert not missing_needs({"carries_kg": 5.0}, merged)


# ── what a peer sees ───────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("sheet", [_sheet(), _sheet(carries_kg=None)], ids=["unset", "none"])
def test_a_sheet_that_says_nothing_dumps_no_such_key(sheet: Datasheet) -> None:
    assert "carries_kg" not in sheet.model_dump()
    assert "carries_kg" not in sheet.model_dump(mode="json")
    assert "carries_kg" not in json.loads(sheet.model_dump_json())
    manifest = _hound(sheet)
    assert "carries_kg" not in manifest.model_dump()["datasheet"]
    assert "carries_kg" not in manifest.model_dump(mode="json")["datasheet"]
    # the other unset figures keep their null, which is what every digest has always hashed
    assert sheet.model_dump()["payload_kg"] is None and sheet.model_dump()["reach_m"] is None


@pytest.mark.parametrize("spec", SPECS, ids=lambda s: f"{s.adapter}:{s.backend}")
def test_no_shipped_body_dumps_a_null_back_load(spec: RobotSpec) -> None:
    """The goldens in test_microduck_body.py and test_arm_types.py pin the digests that have
    one; this holds every body to it, so a body that leaves the figure out is never `null`."""
    sheet = describe(spec).model_dump(mode="json").get("datasheet")
    if sheet is not None:
        assert sheet.get("carries_kg", "absent") is not None


def test_a_body_that_says_it_dumps_the_figure_and_its_digest_is_its_own() -> None:
    said = _sheet(carries_kg=BACK)
    assert json.loads(said.model_dump_json())["carries_kg"]["value"] == 8
    assert _hound(said).digest() != _hound().digest()
    # and the round trip an in-process merge does (the host camera, a task file) keeps it
    assert Datasheet.model_validate(said.model_dump()).carries_kg == BACK


def _bid(sheet: Datasheet) -> BidMsg:
    return BidMsg(
        t=0.0,
        src="hound-01",
        task_id="t",
        ball_dist_m=1.0,
        role="hauler",
        provides=["move"],
        datasheet=sheet.model_dump(mode="json"),
        mobility="legged",
    )


def test_a_bid_from_a_body_that_says_nothing_carries_nothing_new() -> None:
    assert b"carries_kg" not in encode(_bid(_sheet()))


def test_a_bid_that_says_it_reads_fine_at_a_coordinator_that_never_heard_of_it() -> None:
    """A bid's `datasheet` is a dict: no release runs it back through `Datasheet`, so its
    `extra="forbid"` has never judged a peer, and a key the reader does not know is read past."""
    bid = _bid(_sheet(carries_kg=BACK))
    assert bid.datasheet is not None
    bid.datasheet["a_figure_from_next_year"] = 1.0
    back = decode(encode(bid))
    assert isinstance(back, BidMsg) and back.datasheet == bid.datasheet
    lacking = missing_needs_in({"payload_kg": 1.0}, back.datasheet or {}, back.mobility)
    assert lacking == ["payload_kg >= 1 (not published)"], "read as any bid has always been read"


def test_a_bid_from_an_older_peer_is_a_body_that_said_nothing() -> None:
    older_sheet = {"manipulator": "none", "tethered": False, "mass_kg": None}
    assert missing_needs_in({"carries_kg": 5.0}, older_sheet, "legged") == [
        "carries_kg >= 5 (not published)"
    ]


def test_a_task_naming_the_need_is_dropped_by_a_release_that_cannot_read_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The one thing that does reach `extra="forbid"`'s vocabulary on the wire is a role's
    `needs`, through `check_needs`. A release that does not know the word cannot decode the
    TASK, so the bus counts it and drops it, as any message it cannot read, and that member
    takes no part in a task it could not have judged."""
    role = FlockRole(requires=["move"], needs={"carries_kg": 5})
    payload = encode(
        TaskMsg(
            t=0.0,
            src="coordinator",
            task_id="t",
            members=["a", "b"],
            task=FlockTask(task_id="t", name="haul", goal="carry it", roles={"hauler": role}),
        )
    )
    older = tuple(key for key in verdict_module.NEEDS_NUMBERS if key != "carries_kg")
    monkeypatch.setattr(verdict_module, "NEEDS_NUMBERS", older)
    with pytest.raises(ValidationError, match="datasheet vocabulary"):
        decode(payload)
    bus = MqttBus("t", client=SimpleNamespace())  # type: ignore[arg-type]
    bus._on_message(None, None, SimpleNamespace(topic="quackd/t/ctl", payload=payload))
    assert bus.rejected == 1 and bus.received == 0


def test_a_passport_keeps_to_the_specs_own_list() -> None:
    """Spec 2.3 names a body's members, and a member the spec has not named is what rule 8 makes
    a reader note and ignore. A back load stays out until a minor of the spec names it."""
    issued = datetime(2026, 11, 9, 10, 0, tzinfo=UTC)
    doc = passport_from_manifest(
        _hound(_sheet(carries_kg=BACK)),
        robot_id=A.did,
        name="hound-01",
        owner={"id": OWNER.did, "label": "Example lab"},
        board={"kind": "sim", "model": "a computer standing in for a robot's own"},
        issued=issued,
        expires=issued + timedelta(days=30),
    )
    assert "carries_kg" not in doc["body"]
    assert "carries_kg" not in document.FIGURES


def test_a_robot_yaml_cannot_say_it_until_a_passport_can() -> None:
    """`passport.body` is what the owner signs, so it stays the passport's own list."""
    over: dict[str, Any] = {
        "embodiment": "quadruped",
        "mobility": "legged",
        "carries_kg": [8, "official", "the maker's page"],
    }
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        config.Body(**over)


# ── the two bodies that had it as a note ───────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("body", "value", "page", "kept"),
    [
        ("unitree-b2", 40, "unitree.com/b2: Load(Walking) over 40 kg", "120 kg or more standing"),
        ("unitree-go2-edu", 8, "unitree.com/go2: Payload about 8 kg on the EDU", "12 kg at most"),
    ],
)
def test_the_two_quadrupeds_say_what_their_makers_page_said_and_when(
    body: str, value: float, page: str, kept: str
) -> None:
    manifest = describe(RobotSpec(DESCRIBED, body))
    sheet = manifest.datasheet
    assert sheet is not None and sheet.carries_kg is not None
    figure = sheet.carries_kg
    assert figure.value == value and figure.confidence == "official"
    assert figure.source.startswith(page) and "read 2026-11-09" in figure.source
    assert kept in figure.note
    # moved out of the notes, not copied: the sentence that called it a note is gone
    assert not any("rates the back" in note or "so it is a note" in note for note in sheet.notes)
    assert any(line.startswith("- Load on its back or deck") for line in body_lines(manifest))


def test_their_figures_answer_a_task_that_needs_a_back_load() -> None:
    go2 = describe(RobotSpec(DESCRIBED, "unitree-go2-edu"))
    b2 = describe(RobotSpec(DESCRIBED, "unitree-b2"))
    assert missing_needs({"carries_kg": 10}, go2) == ["carries_kg >= 10 (has 8)"]
    assert missing_needs({"carries_kg": 8}, go2) == []
    assert missing_needs({"carries_kg": 40}, b2) == []
    assert missing_needs({"carries_kg": 41}, b2) == ["carries_kg >= 41 (has 40)"]
    # the matcher reads the same figure, and says whose figure it is
    hint = solo_hint({"carries_kg": 10}, None)
    assert "unitree-b2" in hint and "unitree-go2-edu" not in hint, hint
    assert "quackd drives no part of" in hint
