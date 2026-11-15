"""`validate_glot`: one wording for "this robot cannot do this task", used everywhere."""

from __future__ import annotations

from roboglot.adapters.manifest import RobotManifest, VerbSpec
from roboglot.glotfile.parser import load_glot, parse_glot_text
from roboglot.glotfile.validate import validate_glot
from roboglot_microduck import microduck_manifest

# A fixed camera with a voice, no legs: a hypothetical bolted-down body, used only to
# exercise validate_glot against a robot that cannot move or kick.
HEAD = RobotManifest(
    id="head-01",
    vendor="acme",
    model="fixed-cam",
    embodiment="arm",
    mobility="none",
    intents=["gaze", "sound"],
    sensors=["camera"],
    verbs=[
        VerbSpec(name="observe", core=True),
        VerbSpec(name="report_state", core=True),
        VerbSpec(name="say", core=True),
        VerbSpec(name="search_scan", core=True),
        VerbSpec(name="gaze"),
        VerbSpec(name="express"),
    ],
)
DUCK = microduck_manifest("mock", "duck-01")


def test_find_and_kick_against_a_head_names_the_missing_verbs() -> None:
    problems = validate_glot(load_glot("find-and-kick"), [HEAD])
    messages = [p.message for p in problems]
    assert "requires kick, but head-01 (fixed-cam) does not provide it" in messages
    assert "requires walk_to, but head-01 (fixed-cam) does not provide it" in messages
    assert all(p.field == "requires" for p in problems)
    assert {p.verb for p in problems} == {
        "walk_to",
        "kick",
        "quack",
    }  # get_frame -> observe is fine


def test_bundled_ducks_validate_against_the_microduck() -> None:
    for name in ("hello-world", "find-and-kick", "patrol-and-quack", "follow-me", "fetch"):
        assert validate_glot(load_glot(name), [DUCK]) == [], name
    assert validate_glot(load_glot("find-and-kick")) == []  # registry vocabulary, no manifests


def test_v1_requires_is_what_is_checked_and_allow_is_advisory() -> None:
    duck = parse_glot_text(
        "---\nglot: 1\nname: t\ndescription: d\nrequires: [observe, say]\n"
        "verbs:\n  allow: [observe, say, gaze, kick, stop]\nsuccess: [x]\n---\n# T\nx\n"
    )
    problems = validate_glot(duck, [HEAD])
    assert [(p.field, p.verb) for p in problems] == [("verbs.allow", "kick")]
    assert "kick is not provided by head-01" in problems[0].message


def test_team_requires_is_a_union() -> None:
    duck = parse_glot_text(
        "---\nglot: 1\nname: t\ndescription: d\nrequires: [observe, kick]\n"
        "verbs:\n  allow: [observe, gaze, go_to, kick, stop, express]\nsuccess: [x]\n"
        "team:\n  members: [head-01, duck-01]\n---\n# T\nx\n"
    )
    assert validate_glot(duck, [HEAD, DUCK]) == []
    only_heads = validate_glot(duck, [HEAD])
    assert any(p.field == "requires" and p.verb == "kick" for p in only_heads)
    two_ducks = validate_glot(duck, [DUCK, microduck_manifest("mock", "duck-02")])
    assert [p.field for p in two_ducks] == ["verbs.allow"]  # all it requires; no head to express


def test_the_registry_vocabulary_rules_stay_worded_as_before() -> None:
    duck = parse_glot_text(
        "---\nglot: 0\nname: t\ndescription: d\nverbs:\n  allow: [fly, kick]\n  confirm: [kick]\n"
        "success: [x]\n---\n# T\nx\n"
    )
    messages = [str(p) for p in validate_glot(duck)]
    assert messages == ["verbs.allow: unknown verbs: fly"]


PAYLOAD_DUCK = """\
---
glot: 2
name: fetch-the-box
description: Bring the box over.
verbs:
  allow: [observe, stop]
success: [The box is here.]
datasheet:
  payload_kg: 3.0
---
# Task
Do it.
"""


def test_a_task_file_cannot_give_an_armless_body_a_payload() -> None:
    """The merge is where a correction meets the body's own invariants, and `validate` is
    where that is said before the robot is ever asked to connect."""
    duck = parse_glot_text(PAYLOAD_DUCK)
    problems = validate_glot(duck, [HEAD])
    assert [p.field for p in problems] == ["datasheet"]
    assert problems[0].robot == "head-01"
    assert "no payload and no reach" in problems[0].message
    assert "fixed-cam" in problems[0].message


def test_a_correction_a_body_can_carry_is_no_problem() -> None:
    duck = parse_glot_text(PAYLOAD_DUCK)
    assert validate_glot(duck, [DUCK]) == []  # a beak is a manipulator, so a payload is sayable


def test_the_team_keyword_forces_union_semantics() -> None:
    """A task file with no `team:` block, run against a stored team, is a team all the
    same. Checked robot by robot it would refuse the camera for having no legs."""
    duck = load_glot("hello-world")
    alone = validate_glot(duck, [HEAD, DUCK])
    assert [p.field for p in alone] == ["requires", "requires"]
    assert validate_glot(duck, [HEAD, DUCK], team=True) == []
    # and the override works the other way: a team task judged one body at a time
    team_duck = parse_glot_text(
        "---\nglot: 1\nname: t\ndescription: d\nrequires: [walk, quack]\n"
        "verbs:\n  allow: [walk, quack, stop]\nsuccess: [x]\n"
        "team:\n  members: [head-01, duck-01]\n---\n# T\nx\n"
    )
    assert validate_glot(team_duck, [HEAD, DUCK]) == []
    assert [p.field for p in validate_glot(team_duck, [HEAD, DUCK], team=False)] == [
        "requires",
        "requires",
    ]


def test_a_pilot_team_may_gate_a_verb() -> None:
    """A pilot team has pilots, and `--yes` answers for all of them at the run, so a gated verb
    is no problem in one, whether its allocation is left out or says pilots."""
    text = (
        "---"
        + chr(10)
        + "glot: 1"
        + chr(10)
        + "name: t"
        + chr(10)
        + "description: d"
        + chr(10)
        + "verbs:"
        + chr(10)
        + "  allow: [stop, kick]"
        + chr(10)
        + "  confirm: [kick]"
        + chr(10)
        + "success: [x]"
        + chr(10)
        + "team:"
        + chr(10)
        + "  members: [ada, grace]"
        + chr(10)
        + "{alloc}---"
        + chr(10)
        + "# T"
        + chr(10)
        + "x"
        + chr(10)
    )
    left_out = parse_glot_text(text.format(alloc=""))
    assert [p.field for p in validate_glot(left_out)] == []
    pilots_alloc = "  allocation:" + chr(10) + "    method: pilots" + chr(10)
    pilots = parse_glot_text(text.format(alloc=pilots_alloc))
    assert [p.field for p in validate_glot(pilots)] == []
