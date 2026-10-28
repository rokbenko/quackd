"""robot.yaml, the passport it describes, the body quackd only talks for, and the meeting task.

The file is the one thing an owner writes, so it is read strictly and every refusal names where
it is. What the owner signs is built from it here, the board from what quackd found and never from
the file, and a change to the file after signing is named member by member.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from quackd.agent.providers.fake import STRATEGIES
from quackd.onboard import config, facts
from quackd.onboard.body import VERBS, described_manifest
from quackd.onboard.meeting import NAME, meeting_duck
from quackd.passport import document, strict
from tests.passport_helpers import OWNER, A

DEMO = Path(__file__).resolve().parents[1] / "quackd" / "onboard" / "demo"
SIM = {"kind": "sim", "model": "a computer standing in for a robot's own"}
NOW = datetime.now(UTC).replace(microsecond=0)


def template(**overrides: object) -> str:
    fields: dict[str, object] = {
        "name": "g1-edu-01",
        "vendor": "unitree",
        "model": "g1-edu",
        "owner_label": "Example lab",
        "owner_contact": "https://example.org/robots",
        "embodiment": "humanoid",
        "mobility": "legged",
        "manipulator": "arms",
        "how_to_stop": "the red button on its remote",
        "estop": "remote",
        "llm": "local:qwen3-8b",
        "base_url": "http://127.0.0.1:8000/v1",
    }
    fields.update(overrides)
    return config.template(**fields)  # type: ignore[arg-type]


def signed(file: config.RobotFile) -> document.Reading:
    doc = facts.passport_document(
        file,
        robot_did=A.did,
        owner_did=OWNER.did,
        board=SIM,
        issued=NOW,
        expires=NOW + timedelta(days=document.DEFAULT_VALIDITY_DAYS),
    )
    return document.read(document.sign(doc, OWNER))


# ── the file ────────────────────────────────────────────────────────────────────────────


def test_the_template_the_wizard_writes_reads_back_as_a_robot_file() -> None:
    file = config.loads(template())
    assert file.passport.name == "g1-edu-01"
    assert file.brain.llm == "local:qwen3-8b"
    assert file.meeting.task == config.MEETING_TASK
    assert file.trust.pins == [] and file.robot is None
    assert file.lan.port == config.Lan().port and file.radio == config.Radio()
    no_brain = config.loads(template(llm=None, base_url=None))  # its brain section all comments
    assert no_brain.brain == config.Brain()
    # robots on one laptop share no port, so the sim board's file asks for any free one
    assert config.loads(template(port=0)).lan.port == 0


@pytest.mark.parametrize("name", ["g1-edu-mock", "agile-one-sn-mock"])
def test_each_demo_robot_file_reads_and_signs_into_a_1_0_passport(name: str) -> None:
    file = config.load(DEMO / f"{name}.yaml")
    reading = signed(file)
    assert reading.signed and reading.name == name
    assert reading.board() == SIM
    assert len(strict.dumps(reading.document)) <= document.WRITER_BYTES
    figures = reading.body()
    assert all(figures[f]["source"] for f in ("mass_kg", "height_m", "payload_kg"))
    assert any("mock" in line for line in reading.document["cannot"])


def test_an_arm_is_refused_with_the_reason_it_carries_no_passport() -> None:
    with pytest.raises(config.FileError, match="fixed in place"):
        config.loads(template(embodiment="arm"))


@pytest.mark.parametrize(
    ("edit", "where"),
    [
        (("  port: 8790", "  port: 8790\n  colour: teal"), "lan.colour"),
        (("    embodiment: humanoid", "    embodiment: hexapod"), "passport.body.embodiment"),
        (("  name: g1-edu-01", "  name: G1 EDU"), "passport.name"),
        (
            ("    manipulator: arms", "    manipulator: arms\n    mass_kg: [35, official]"),
            "kg",
        ),
        (("  name: g1-edu-01", "  name: g1-edu-01\n  board: {kind: jetson}"), "passport.board"),
    ],
)
def test_a_wrong_key_or_value_is_refused_by_its_path(edit: tuple[str, str], where: str) -> None:
    """The board is what quackd finds, never what a file claims, so the file cannot set it."""
    old, new = edit
    text = template()
    assert old in text
    with pytest.raises(config.FileError, match=where.replace(".", r"\.")):
        config.loads(text.replace(old, new))


# ── what the owner signs ────────────────────────────────────────────────────────────────


def test_a_change_after_signing_is_named_member_by_member() -> None:
    file = config.loads(template())
    reading = signed(file)
    assert facts.unsigned_changes(file, reading, board=SIM) == []
    edited = config.loads(
        template(how_to_stop="pull the cord", owner_label="Another lab", manipulator="gripper")
    )
    assert facts.unsigned_changes(edited, reading, board=SIM) == ["body", "owner", "safety"]


def test_a_robot_quackd_only_talks_for_offers_no_courtesy() -> None:
    """A peer's pause stops what quackd moves, and quackd moves nothing of a described body."""
    reading = signed(config.loads(template()))
    assert reading.courtesy() == []
    assert reading.document["owner"] == {
        "id": OWNER.did,
        "label": "Example lab",
        "contact": "https://example.org/robots",
    }


def test_the_board_comes_from_the_caller_and_the_file_cannot_say_jetson() -> None:
    file = config.loads(template())
    on_a_jetson = {"kind": "jetson", "model": "NVIDIA Jetson AGX Thor Developer Kit"}
    doc = facts.passport_document(
        file,
        robot_did=A.did,
        owner_did=OWNER.did,
        board=on_a_jetson,
        issued=NOW,
        expires=NOW + timedelta(days=1),
    )
    assert doc["board"] == on_a_jetson


# ── the described body and the meeting ──────────────────────────────────────────────────


def test_a_described_body_moves_nothing_and_says_so() -> None:
    manifest = described_manifest(config.load(DEMO / "g1-edu-mock.yaml"))
    assert [v.name for v in manifest.verbs] == list(VERBS)
    assert manifest.sensors == [] and manifest.intents == []
    assert "only talks for it" in manifest.blurb
    assert manifest.datasheet is not None and manifest.datasheet.arms == 2


def test_a_tracked_body_is_shown_to_a_pilot_as_wheeled_and_signed_as_tracked() -> None:
    file = config.loads(template(embodiment="tracked", mobility="tracked", manipulator="none"))
    manifest = described_manifest(file)
    assert (manifest.embodiment, manifest.mobility) == ("wheeled", "wheeled")
    assert signed(file).body()["embodiment"] == "tracked"


def test_the_meeting_task_moves_nothing_and_carries_the_owners_words() -> None:
    duck = meeting_duck("Say hello and that you deliver parcels.")
    assert duck.frontmatter.name == NAME
    assert duck.frontmatter.verbs.allow == ["report_state", "stop"]
    assert "deliver parcels" in duck.body
    assert NAME in STRATEGIES, "the scripted pilot can play a meeting offline"


# ── pins, as lines of text ──────────────────────────────────────────────────────────────


def test_a_pin_is_added_and_removed_as_a_line_and_every_comment_stays(tmp_path: Path) -> None:
    path = tmp_path / "robot.yaml"
    path.write_text(template(), encoding="utf-8")
    pin = config.Pin(kind="owner", id=OWNER.did, name="Courier company", pinned="2026-10-29")
    config.add_pin(path, pin)
    text = path.read_text(encoding="utf-8")
    assert config.pin_line(pin).strip() in text
    assert "# What your owner key signs." in text
    assert config.load(path).trust_pins()[0].id == OWNER.did
    assert config.remove_pin(path, OWNER.did)
    assert config.load(path).trust.pins == []
    assert not config.remove_pin(path, OWNER.did)
    assert path.read_text(encoding="utf-8") == template()


def test_a_file_laid_out_another_way_gets_the_line_to_paste(tmp_path: Path) -> None:
    path = tmp_path / "robot.yaml"
    start, end = template().index("trust:"), template().index("radio:")
    flow = "trust: {strangers_may_pause: true, pins: []}\n\n"
    path.write_text(template()[:start] + flow + template()[end:], encoding="utf-8")
    assert config.load(path).trust.pins == []
    pin = config.Pin(kind="robot", id=A.did, name="g1-edu-02", pinned="2026-10-29")
    with pytest.raises(config.FileError, match="add this line"):
        config.add_pin(path, pin)
