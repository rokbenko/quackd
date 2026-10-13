"""The datasheet a body carries, and the prompt section it renders into.

The numbers themselves are read from makers' pages and one paper, not measured here, so what
these tests pin is the shape of the honesty: every figure names a source, an unpublished one
says so rather than defaulting to zero, and the same body reads the same on every backend.
"""

from __future__ import annotations

import pytest

from quackd.adapters.factory import ADAPTER_NAMES, BACKENDS, RobotSpec, describe
from quackd.adapters.manifest import Datasheet, Figure, RobotManifest, VerbSpec
from quackd.agent.prompts import BODY_HEADING, body_lines, body_section, body_summary
from quackd.verbs.core import speed_limits
from quackd_microduck import MICRODUCK_VERBS, microduck_manifest
from quackd_microduck import upstream_api as up
from quackd_xlerobot import xlerobot_manifest

SPECS = [RobotSpec(adapter, backend) for adapter in ADAPTER_NAMES for backend in BACKENDS[adapter]]


def _bare(**over: object) -> RobotManifest:
    base: dict[str, object] = {
        "id": "bot-01",
        "vendor": "acme",
        "model": "bot",
        "embodiment": "wheeled",
        "mobility": "wheeled",
        "intents": ["twist"],
        "verbs": [VerbSpec(name="move", core=True)],
    }
    return RobotManifest(**{**base, **over})  # type: ignore[arg-type]


@pytest.mark.parametrize("spec", SPECS, ids=lambda s: f"{s.adapter}:{s.backend}")
def test_every_shipped_body_publishes_a_datasheet_that_renders(spec: RobotSpec) -> None:
    manifest = describe(spec)
    sheet = manifest.datasheet
    assert sheet is not None, f"{spec.adapter}:{spec.backend} has no datasheet"
    for label, figure, _unit in sheet.known():
        assert figure.source, f"{label} is a rumour: no source"
        assert figure.confidence in ("official", "estimate", "measured")
    lines = body_lines(manifest)
    assert lines
    for line in lines:
        # the prompt spells an offered verb as "- `name`: ...", and four adapter tests read the
        # offered verbs back out of it by that shape; a fact must never look like one
        assert not line.startswith("- `"), line
    summary = body_summary(manifest)
    assert "\n" not in summary and summary.startswith(manifest.model)


@pytest.mark.parametrize("adapter", [a for a in ADAPTER_NAMES if a != "rosbridge"])
def test_a_body_reads_the_same_on_every_backend(adapter: str) -> None:
    """sim2d, mock or real, the body is the body. This is also what keeps the digests equal.

    rosbridge is the exception and is tested below: its sheet is whatever the bridge said,
    so it is not a constant of the adapter at all."""
    sheets = {
        backend: describe(RobotSpec(adapter, backend)).datasheet for backend in BACKENDS[adapter]
    }
    dumps = {backend: sheet.model_dump_json() for backend, sheet in sheets.items() if sheet}
    assert len(set(dumps.values())) == 1, dumps


def test_the_one_body_whose_datasheet_is_not_a_constant_is_the_one_that_is_a_name() -> None:
    """`rosbridge` names a transport. What is on the other side is whatever answered, so the
    mock (which serves a canned description) and the ws backend (which has asked nobody yet)
    describe different bodies on purpose."""
    mock = describe(RobotSpec("rosbridge", "mock")).datasheet
    blind = describe(RobotSpec("rosbridge", "ws")).datasheet
    assert mock is not None and blind is not None
    assert mock != blind
    assert mock.mass_kg is not None and blind.mass_kg is None
    assert blind.manipulator == mock.manipulator == "none"


def test_the_microduck_says_what_it_knows_and_what_nobody_published() -> None:
    text = body_section(microduck_manifest("sim2d"))
    assert BODY_HEADING in text
    assert "0.8 kg (official: the Pollen Robotics README)" in text
    assert "Not published: payload, reach, endurance." in text
    assert "a beak, no arms" in text
    assert "carry, hold or push anything" in text


def test_the_microduck_does_not_promise_to_get_itself_up() -> None:
    """The sheet said the duck could get itself upright again, and that was robotd's
    `limp_fall`, which is off by default on daemon-v0.16.1, where a fall changes nothing in
    robotd. The sheet is what the pilot judges a task against, and `stand_up`'s description is
    what it reads before calling the verb, so neither may promise a recovery the robot does
    not run.

    From daemon-v0.16.2 `limp_fall` is on by default, and robotd hands the duck back to a
    walking policy upstream says gets it up (`up.LIMP_FALL`), which nobody has seen. The sheet
    said only what daemon-v0.16.1 does, so it says what each release does now, the get-up as
    upstream's word."""
    text = body_section(microduck_manifest("sim2d"))
    assert "may not get up again by itself" in text and "get itself upright" not in text
    assert "may not manage" in MICRODUCK_VERBS["stand_up"].description
    said = " ".join(text.split())
    assert f"robotd on {up.RELEASE} does nothing about a fall but report it" in said
    assert "from daemon-v0.16.2 it goes limp in the fall, poses the duck and hands it back" in said
    assert "which upstream says gets it up" in said


def test_the_microduck_says_its_speed_caps_are_quackds_own_clamp() -> None:
    """The sheet said the speed caps were a software clamp in robotd. robotd clamps no twist at
    any release quackd has read (`up.NO_TWIST_CLAMP`): the caps are the manifest's own limits,
    which quackd holds every twist a verb sends to and the prompt renders as quackd's clamp, so
    the note contradicted a line above it. It says whose clamp it is now, and the numbers
    are as they were."""
    manifest = microduck_manifest("sim2d")
    text = body_section(manifest)
    assert "software clamp in robotd" not in text
    assert "The speed caps are quackd's own clamp on the twists it sends" in text
    assert up.NO_TWIST_CLAMP.name in text
    assert "quackd clamps you to 0.3 m/s forward, 0.2 m/s sideways, 1.5 rad/s turning." in text
    assert speed_limits(manifest) == (0.3, 0.2, 1.5)


def test_an_unpublished_figure_is_a_question_for_a_person_not_a_refusal() -> None:
    """The line under "Not published" used to say "Decline any task that hinges on any of
    them", and on an arm whose reach nobody had published that forbids reaching for anything.
    The verdict gate already refuses a `feasible` that names an unpublished figure, and what it
    offers the pilot is `uncertain`, which asks a person who may know. So the line points the
    same way the gate does, for one unpublished figure or several.

    A body with no datasheet keeps its own sentence, which still says decline: this is about a
    figure missing from a sheet, not a body nobody has described at all."""
    many = body_section(microduck_manifest("sim2d"))
    assert (
        "Not published: payload, reach, endurance. Where a task turns on one of them, say "
        "uncertain and name it rather than guessing; do not decline on it alone." in many
    )
    assert "Decline" not in many and "hinges" not in many

    figure = Figure(value=1.0, confidence="measured", source="a test")
    for missing in ("mass_kg", "height_m", "payload_kg"):
        sheet = Datasheet(
            manipulator="gripper",
            arms=1,
            tethered=True,
            **{field: figure for field, _l, _u in Datasheet.FIGURES if field != missing},
        )
        (label,) = sheet.unknown()
        text = body_section(_bare(datasheet=sheet))
        assert f"Not published: {label}. Where a task turns on it, say uncertain" in text
        assert "Decline" not in text and "hinges" not in text

    assert "decline any task that hinges on one" in body_section(_bare())


def test_the_arm_says_it_cannot_go_anywhere_and_the_bridge_says_it_knows_nothing() -> None:
    arm = body_section(describe(RobotSpec("lerobot", "mock")))
    assert "0.5 kg (estimate: one vendor's listing)" in arm
    assert "It does not move: no base and no legs" in arm
    assert "Mains powered, so nothing runs down" in arm
    assert "Endurance" not in arm, "a mains-powered arm has no endurance to publish"
    assert "- Reach: " in arm and "(estimate: the maker's URDF" in arm
    assert "Not published: mass. Where a task turns on it" in arm

    bridge = body_section(describe(RobotSpec("rosbridge", "ws")))
    assert "Not published: mass, height, actuated joints, endurance" in bridge
    assert "Terrain not published" in bridge
    assert "unknown, not zero" in bridge


def test_speed_clamps_come_from_the_limits_never_from_the_datasheet() -> None:
    """`limits` is quackd's own rule about what it sends. The datasheet never repeats it."""
    duck = body_section(microduck_manifest("sim2d"))
    assert "quackd clamps you to 0.3 m/s forward, 0.2 m/s sideways, 1.5 rad/s turning." in duck

    strafing = body_section(xlerobot_manifest("mock"))
    assert "0.2 m/s sideways" in strafing
    straight = body_section(xlerobot_manifest("mock", variant="diff2"))
    assert "no sideways motion" in straight

    lift = body_section(describe(RobotSpec("alohamini", "mock")))
    assert "lift 5 to 600 mm" in lift


def test_the_working_height_band_is_an_extra_where_known() -> None:
    cart = body_section(xlerobot_manifest("mock"))
    assert "Working height: 0.5 to 1.25 m (official: the XLeRobot docs" in cart
    assert "the torso does not lift" in cart
    assert "Working height" not in body_section(microduck_manifest("sim2d"))


def test_a_manifest_without_a_datasheet_says_so_rather_than_guessing() -> None:
    bare = RobotManifest(
        id="bot-01",
        vendor="acme",
        model="bot",
        embodiment="wheeled",
        mobility="wheeled",
        intents=["twist"],
        verbs=[VerbSpec(name="move", core=True)],
    )
    assert bare.datasheet is None
    text = body_section(bare)
    assert "has published no datasheet" in text
    assert "decline any task that hinges on one" in text
    assert "no datasheet published" in body_summary(bare)


def test_the_section_states_facts_and_leaves_the_rules_to_the_rules_block() -> None:
    """The prompt has one place where enforced rules are stated, and this is not it."""
    text = body_section(microduck_manifest("sim2d"))
    assert "assess_task" not in text


def test_the_unknown_list_skips_what_cannot_apply() -> None:
    armless = Datasheet(manipulator="none")
    assert "payload" not in armless.unknown() and "reach" not in armless.unknown()
    mains = Datasheet(manipulator="gripper", arms=1, tethered=True)
    assert "endurance" not in mains.unknown()
    battery = Datasheet(manipulator="gripper", arms=1, tethered=False)
    assert "endurance" in battery.unknown()


def test_a_figures_text_carries_its_confidence_source_and_note() -> None:
    plain = Figure(value=1.484, confidence="official", source="arXiv:2502.00893")
    assert plain.text("kg") == "1.484 kg (official: arXiv:2502.00893)"
    noted = Figure(value=15, confidence="official", source="the README", note="XL330 class")
    assert noted.text("") == "15 (official: the README; XL330 class)"
