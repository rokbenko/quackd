"""The .duck contract: parses what it should, refuses what it must, schema stays in sync."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from quackd.duckfile.parser import (
    DuckParseError,
    list_bundled_ducks,
    load_duck,
    parse_duck_text,
    resolve_duck_path,
)
from quackd.duckfile.schema import json_schema

from .conftest import DUCKS

VALID = """\
---
duck: 0
name: t
description: d
verbs:
  allow: [quack]
success: [x]
---
# Task
Do it.
"""


@pytest.mark.parametrize("path", sorted(DUCKS.glob("*.duck")), ids=lambda p: p.stem)
def test_starter_ducks_parse(path: Path) -> None:
    duck = load_duck(str(path))
    assert duck.name == path.stem
    assert duck.body.strip().startswith("# Task")
    assert duck.frontmatter.learned_verbs == []


def test_bundled_list() -> None:
    assert {p.stem for p in list_bundled_ducks()} == {
        "hello-world",
        "find-and-kick",
        "patrol-and-quack",
        "follow-me",
        "fetch",
        "flock-kick",
        "flock-hello",
        "open-duck-scout",
        "open-duck-lookout",
        "microduck-lookout",
        "xlerobot-lookout",
        "alohamini-lookout",
        "toddlerbot-lookout",
        "lerobot-lookout",
        "arm-grip-check",
    }


def test_resolve_by_bundled_name() -> None:
    assert resolve_duck_path("hello-world").name == "hello-world.duck"
    assert resolve_duck_path("hello-world.duck").name == "hello-world.duck"
    with pytest.raises(DuckParseError):
        resolve_duck_path("no-such-duck")


def test_leading_comments_allowed() -> None:
    duck = parse_duck_text("# a comment\n\n# another\n" + VALID)
    assert duck.name == "t"


@pytest.mark.parametrize("starter", ["hello-world", "fetch"])
def test_a_duck_saved_with_a_byte_order_mark_is_read_like_one_without(
    starter: str, tmp_path: Path
) -> None:
    """Notepad can save UTF-8 with a byte-order mark, and every way into a `.duck` refused a file
    saved so as missing its frontmatter: the mark decoded to a character in front of the first
    line, and the fence check never got past it. hello-world opens on its fence and fetch on
    comment lines, so the mark lands in front of both shapes a file may open with."""
    from typer.testing import CliRunner

    from quackd.cli import app
    from quackd.mcp_server import build_server
    from quackd.transport.sim2d import Sim2DTransport

    plain = DUCKS / f"{starter}.duck"
    marked = tmp_path / plain.name
    marked.write_bytes(b"\xef\xbb\xbf" + plain.read_bytes())
    want = load_duck(str(plain))

    parsed = parse_duck_text("\ufeff" + plain.read_text(encoding="utf-8"))
    loaded = load_duck(str(marked))
    for got in (parsed, loaded):
        assert (got.frontmatter, got.body) == (want.frontmatter, want.body)

    checked = CliRunner().invoke(app, ["validate", str(marked)])
    assert checked.exit_code == 0, checked.output

    # what `robot_load_duckfile` calls
    _server, session = build_server(Sim2DTransport(seed=1), memory=False)
    adopted = session.load(str(marked))
    assert adopted["ok"], adopted
    assert adopted["instructions"] == want.body


def test_machine_enforced_abort_conditions() -> None:
    duck = load_duck(str(DUCKS / "find-and-kick.duck"))
    fm = duck.frontmatter
    assert fm.battery_abort_percent == 15
    assert fm.repeat_failure_abort == 3
    assert fm.advisory_abort_conditions == []


def test_advisory_abort_conditions_pass_through() -> None:
    text = VALID.replace(
        "success: [x]", "success: [x]\nabort_when: [the cat appears, Battery below 20%]"
    )
    fm = parse_duck_text(text).frontmatter
    assert fm.battery_abort_percent == 20
    assert fm.repeat_failure_abort is None
    assert fm.advisory_abort_conditions == ["the cat appears"]


@pytest.mark.parametrize(
    ("mutation", "needle"),
    [
        (lambda s: s.replace("---\nduck", "duck", 1), "missing frontmatter"),
        (lambda s: s.replace("---\n# Task", "# Task"), "unterminated"),
        (lambda s: s.replace("duck: 0", "duck: 4"), "duck"),
        (lambda s: s.replace("success: [x]", "success: [x]\nrequires: [quack]"), "needs duck: 1"),
        (lambda s: s.replace("name: t", "name: Not A Slug"), "name"),
        (lambda s: s.replace("success: [x]", "success: [x]\nbogus: 1"), "bogus"),
        (lambda s: s.replace("allow: [quack]", "allow: [quack]\n  confirm: [kick]"), "not allowed"),
        (lambda s: s.replace("allow: [quack]", "allow: []"), "allow"),
        (lambda s: s.replace("allow: [quack]", "allow: [quack, quack]"), "duplicate"),
        (lambda s: s.replace("# Task\nDo it.\n", ""), "body is empty"),
        (
            lambda s: s.replace("success: [x]", "success: [x]\nbudgets:\n  max_steps: 0"),
            "max_steps",
        ),
        (lambda s: s.replace("duck: 0", "duck: [0"), "YAML"),
    ],
)
def test_invalid_ducks_fail_fast(mutation, needle: str) -> None:
    with pytest.raises(DuckParseError) as exc:
        parse_duck_text(mutation(VALID), path="x.duck")
    assert needle.lower() in str(exc.value).lower()
    assert "x.duck" in str(exc.value)


def test_schema_json_in_sync() -> None:
    on_disk = json.loads((Path("quackd") / "duckfile" / "schema.json").read_text(encoding="utf-8"))
    assert on_disk == json_schema(), "run: uv run python -m quackd.duckfile.export"


def test_defaults_applied() -> None:
    fm = parse_duck_text(VALID).frontmatter
    assert fm.budgets.max_steps == 40
    assert fm.budgets.max_minutes == 5
    assert fm.verbs.confirm == []
    assert fm.providers == []
    assert fm.requires == [] and fm.robots is None
    assert fm.effective_requires == ["quack"]  # a v0 task needs everything it allows


V1 = """\
---
duck: 1
name: t
description: d
requires: [observe, kick]
robots: {lerobot-01: lerobot:mock, duck-01: microduck:sim2d}
verbs:
  allow: [observe, gaze, go_to, kick, stop]
success: [x]
flock:
  members: [lerobot-01, duck-01]
  roles:
    spotter: {requires: [observe, gaze]}
    kicker: {requires: [go_to, kick]}
  frame_hints: auto
---
# Task
Do it.
"""


def test_duck_v1_parses_requires_robots_and_roles() -> None:
    fm = parse_duck_text(V1).frontmatter
    assert fm.duck == 1 and fm.effective_requires == ["observe", "kick"]
    assert fm.robots == {"lerobot-01": "lerobot:mock", "duck-01": "microduck:sim2d"}
    assert fm.flock is not None and fm.flock.roles is not None
    assert fm.flock.roles["kicker"].requires == ["go_to", "kick"]
    assert fm.flock.frame_hints == "auto"
    solo = parse_duck_text(
        V1.replace(
            "robots: {lerobot-01: lerobot:mock, duck-01: microduck:sim2d}",
            "robots: microduck:mock",
        ).split("flock:")[0]
        + "---\n# Task\nx\n"
    )
    assert solo.frontmatter.robots == "microduck:mock"


@pytest.mark.parametrize(
    ("mutation", "needle"),
    [
        (
            lambda s: s.replace("requires: [observe, kick]", "requires: [observe, fly]"),
            "not allowed",
        ),
        (
            lambda s: s.replace("requires: [observe, kick]", "requires: [observe, get_frame]"),
            "same verb",
        ),
        (lambda s: s.replace("spotter:", "judge:"), "unknown flock role"),
        (
            lambda s: s.replace("    kicker: {requires: [go_to, kick]}\n", ""),
            "both spotter and kicker",
        ),
        (lambda s: s.replace("members: [lerobot-01, duck-01]", "members: 2"), "name the members"),
        (
            lambda s: s.replace("kicker: {requires: [go_to, kick]}", "kicker: {requires: [fly]}"),
            "not allowed",
        ),
        (
            lambda s: s.replace("duck-01: microduck:sim2d", "duck-02: microduck:sim2d"),
            "does not have",
        ),
        (lambda s: s.replace("microduck:sim2d", "Micro Duck"), "<adapter>"),
        (lambda s: s.replace("frame_hints: auto", "frame_hints: maybe"), "frame_hints"),
    ],
)
def test_invalid_v1_ducks_fail_fast(mutation, needle: str) -> None:
    with pytest.raises(DuckParseError) as exc:
        parse_duck_text(mutation(V1), path="x.duck")
    assert needle.lower() in str(exc.value).lower()


# ── v2: the datasheet a task file corrects ──────────────────────────────────────────────

V2 = """\
---
duck: 2
name: lift-the-mug
description: Pick up the mug and put it on the shelf.
robots: lerobot:mock
requires: [gripper]
verbs:
  allow: [observe, report_state, gripper, stop]
success: [The mug is on the shelf.]
datasheet:
  payload_kg: {value: 0.3, confidence: measured, source: weighed with the printed gripper}
  reach_m: 0.35
  cannot: [lift anything wider than the printed gripper's 60 mm opening]
---
# Task
Do it.
"""


def test_a_v2_task_file_corrects_the_datasheet_for_its_own_build() -> None:
    fm = parse_duck_text(V2).frontmatter
    sheet = fm.datasheet
    assert sheet is not None
    assert sheet.payload_kg is not None
    assert (sheet.payload_kg.value, sheet.payload_kg.confidence) == (0.3, "measured")
    assert sheet.payload_kg.source == "weighed with the printed gripper"
    assert sheet.reach_m is not None  # a bare number is shorthand for one
    assert (sheet.reach_m.value, sheet.reach_m.confidence) == (0.35, "estimate")
    assert sheet.reach_m.source == ""
    assert len(sheet.cannot) == 1
    assert fm.effective_requires == ["gripper"]


@pytest.mark.parametrize(
    ("mutation", "needle"),
    [
        (lambda s: s.replace("duck: 2", "duck: 1"), "needs duck: 2"),
        (lambda s: s.replace("confidence: measured", "confidence: hearsay"), "confidence"),
        (lambda s: s.replace("reach_m: 0.35", "reach_m: -1"), "greater than or equal"),
        (lambda s: s.replace("reach_m: 0.35", "bogus_m: 0.35"), "bogus_m"),
        # a backtick reads as an offered verb in the prompt, so the sentence is refused. It
        # has to be quoted to get past YAML at all, which is where the plain form would die
        (
            lambda s: s.replace(
                "cannot: [lift anything wider than the printed gripper's 60 mm opening]",
                'cannot: ["`gripper` is not for you"]',
            ),
            "start with a word",
        ),
        (lambda s: s.replace("  reach_m: 0.35\n", "  manipulator: claw\n"), "manipulator"),
        (
            lambda s: s.replace("robots: lerobot:mock", "flock:\n  members: 2"),
            "a flock duck cannot carry one",
        ),
    ],
)
def test_invalid_v2_ducks_fail_fast(mutation, needle: str) -> None:
    with pytest.raises(DuckParseError) as exc:
        parse_duck_text(mutation(V2), path="x.duck")
    assert needle.lower() in str(exc.value).lower()


V2_FLOCK = """---
duck: 2
name: two-body-kick
description: One spots, the one that can carry kicks.
verbs:
  allow: [observe, gaze, go_to, kick, stop]
success: [The ball moved.]
flock:
  members: [eye, arm]
  roles:
    spotter: {requires: [observe, gaze]}
    kicker:
      requires: [go_to, kick]
      needs: {payload_kg: 1.0, manipulator: gripper}
---
# Task
Do it.
"""


def test_a_v2_role_can_ask_for_a_body_not_only_a_vocabulary() -> None:
    fm = parse_duck_text(V2_FLOCK).frontmatter
    roles = (fm.flock.roles or {}) if fm.flock else {}
    assert roles["kicker"].needs == {"payload_kg": 1.0, "manipulator": "gripper"}
    assert roles["spotter"].needs == {}, "a role that asks for nothing is still a role"


@pytest.mark.parametrize(
    ("mutation", "needle"),
    [
        (lambda s: s.replace("duck: 2", "duck: 1"), "flock.roles.kicker.needs needs duck: 2"),
        (lambda s: s.replace("payload_kg: 1.0", "oomph: 1.0"), "datasheet vocabulary"),
        (lambda s: s.replace("payload_kg: 1.0", "payload_kg: -1"), "0 or more"),
        (lambda s: s.replace("payload_kg: 1.0", "payload_kg: heavy"), "must be a number"),
        (lambda s: s.replace("manipulator: gripper", "manipulator: claw"), "must be one of"),
        (lambda s: s.replace("payload_kg: 1.0", "mobility: flying"), "must be one of"),
    ],
)
def test_invalid_v2_role_needs_fail_fast(mutation, needle: str) -> None:
    with pytest.raises(DuckParseError) as exc:
        parse_duck_text(mutation(V2_FLOCK), path="x.duck")
    assert needle.lower() in str(exc.value).lower()


# ── the strategy paragraph a `--goal` run writes ────────────────────────────────────────


def _goal_allowlist(spec: str) -> list[str]:
    """As `quackd run --goal` builds it: the safe verbs of the STATIC manifest, plus `stop`.

    Static because a description is all the CLI has before it connects: the arm claims no
    camera until `connect()` finds one, which is why `observe` is absent from its goal
    allowlist and why the paragraph under test used to promise it anyway."""
    from quackd.adapters.factory import RobotSpec, registry_for

    adapter, backend = spec.split(":")
    registry = registry_for(RobotSpec(adapter, backend))
    return sorted(verb.name for verb in registry.verbs() if verb.safety_class == "safe")


@pytest.mark.parametrize("spec", ["lerobot:real", "lerobot:mock", "microduck:sim2d"])
def test_a_goal_duck_names_no_verb_the_body_was_not_granted(spec: str) -> None:
    """The strategy paragraph `--goal` writes used to name `observe`, `search_scan` and
    `go_to` whatever the body was. On 2026-09-15 an SO-101 arm read all three in a prompt
    whose allowlist listed none of them, and looked with `report_state` on its own initiative.

    Every backticked verb in the body has to be one the contract allows. `remember` is the
    exception: it is a meta tool the loop offers beside the verbs rather than a verb."""
    import re

    from quackd.duckfile.parser import duck_from_goal

    duck = duck_from_goal("wave to me", _goal_allowlist(spec), memory=True)
    named = set(re.findall(r"`([a-z_]+)`", duck.body))
    assert named, "the body names its verbs in backticks"
    allowed = set(duck.frontmatter.verbs.allow) | {"remember"}
    assert named <= allowed, f"{spec} is promised {sorted(named - allowed)}"


def test_an_arm_is_told_to_look_with_report_state_and_a_duck_keeps_its_composites() -> None:
    """The other half: narrowing the advice must not empty it. An arm is pointed at the verb
    it does have, and a Microduck reads the sentence it has always read, byte for byte."""
    from quackd.duckfile.parser import duck_from_goal

    arm = duck_from_goal("wave to me", _goal_allowlist("lerobot:real")).body
    assert "Look before you act (`report_state`)" in arm
    assert "verify with a fresh reading" in arm, "an arm with no camera verb has no frame"
    assert "composite" not in arm and "go_to" not in arm

    duck = duck_from_goal("find the ball", _goal_allowlist("microduck:sim2d"), memory=True).body
    assert "Look before you act (`observe` or `search_scan`)" in duck
    assert "prefer composite verbs like `go_to`" in duck
    assert "verify with a fresh frame" in duck
