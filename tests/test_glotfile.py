"""The .glot contract: parses what it should, refuses what it must, schema stays in sync."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from roboglot.glotfile.parser import (
    GlotParseError,
    list_bundled_tasks,
    load_glot,
    parse_glot_text,
    resolve_glot_path,
)
from roboglot.glotfile.schema import GLOT_SPEC_VERSION, json_schema

from .conftest import TASKS

VALID = """\
---
glot: 0
name: t
description: d
verbs:
  allow: [quack]
success: [x]
---
# Task
Do it.
"""


@pytest.mark.parametrize("path", sorted(TASKS.glob("*.glot")), ids=lambda p: p.stem)
def test_starter_ducks_parse(path: Path) -> None:
    duck = load_glot(str(path))
    assert duck.name == path.stem
    assert duck.body.strip().startswith("# Task")
    assert duck.frontmatter.learned_verbs == []


def test_bundled_list() -> None:
    assert {p.stem for p in list_bundled_tasks()} == {
        "hello-world",
        "find-and-kick",
        "patrol-and-quack",
        "follow-me",
        "fetch",
        "team-hello",
        "open-duck-scout",
        "open-duck-lookout",
        "microduck-lookout",
        "microduck-watch",
        "xlerobot-lookout",
        "alohamini-lookout",
        "toddlerbot-lookout",
        "lerobot-lookout",
        "arm-grip-check",
        "moss-lookout",
        "rosarm-lookout",
    }


def test_resolve_by_bundled_name() -> None:
    assert resolve_glot_path("hello-world").name == "hello-world.glot"
    assert resolve_glot_path("hello-world.glot").name == "hello-world.glot"
    with pytest.raises(GlotParseError):
        resolve_glot_path("no-such-duck")


def test_leading_comments_allowed() -> None:
    duck = parse_glot_text("# a comment\n\n# another\n" + VALID)
    assert duck.name == "t"


@pytest.mark.parametrize("starter", ["hello-world", "fetch"])
def test_a_duck_saved_with_a_byte_order_mark_is_read_like_one_without(
    starter: str, tmp_path: Path
) -> None:
    """Notepad can save UTF-8 with a byte-order mark, and every way into a `.glot` refused a file
    saved so as missing its frontmatter: the mark decoded to a character in front of the first
    line, and the fence check never got past it. hello-world opens on its fence and fetch on
    comment lines, so the mark lands in front of both shapes a file may open with."""
    from typer.testing import CliRunner

    from roboglot.cli import app
    from roboglot.mcp_server import build_server
    from roboglot.transport.mock import MockTransport

    plain = TASKS / f"{starter}.glot"
    marked = tmp_path / plain.name
    marked.write_bytes(b"\xef\xbb\xbf" + plain.read_bytes())
    want = load_glot(str(plain))

    parsed = parse_glot_text("\ufeff" + plain.read_text(encoding="utf-8"))
    loaded = load_glot(str(marked))
    for got in (parsed, loaded):
        assert (got.frontmatter, got.body) == (want.frontmatter, want.body)

    checked = CliRunner().invoke(app, ["validate", str(marked)])
    assert checked.exit_code == 0, checked.output

    # what `robot_load_glotfile` calls
    _server, session = build_server(MockTransport(), memory=False)
    adopted = session.load(str(marked))
    assert adopted["ok"], adopted
    assert adopted["instructions"] == want.body


def test_a_duck_that_is_not_utf8_is_refused_in_a_sentence_wherever_it_is_read(
    tmp_path: Path,
) -> None:
    """Windows PowerShell 5.1 saves UTF-16 with `>`, and every way into a `.glot` read such a file
    into a UnicodeDecodeError traceback, `roboglot validate` and what `robot_load_glotfile` calls
    among them. The one place that opens a file refuses it now as a task file it cannot read,
    which every caller already says in a sentence, and says what to do."""
    from typer.testing import CliRunner

    from roboglot.cli import app
    from roboglot.mcp_server import build_server
    from roboglot.transport.mock import MockTransport

    wide = tmp_path / "hello-world.glot"
    wide.write_bytes((TASKS / "hello-world.glot").read_text(encoding="utf-8").encode("utf-16"))
    with pytest.raises(GlotParseError, match="saved as UTF-16"):
        load_glot(str(wide))
    checked = CliRunner().invoke(app, ["validate", str(wide)])
    assert checked.exit_code == 1 and isinstance(checked.exception, SystemExit), checked.exception
    assert "saved as UTF-16" in " ".join(checked.output.split()), checked.output
    _server, session = build_server(MockTransport(), memory=False)
    adopted = session.load(str(wide))
    assert not adopted["ok"] and "saved as UTF-16" in adopted["error"], adopted

    latin = tmp_path / "latin.glot"
    latin.write_bytes("---\nname: caf\u00e9\n---\n".encode("latin-1"))
    with pytest.raises(GlotParseError, match="not UTF-8"):
        load_glot(str(latin))


def test_machine_enforced_abort_conditions() -> None:
    duck = load_glot(str(TASKS / "find-and-kick.glot"))
    fm = duck.frontmatter
    assert fm.battery_abort_percent == 15
    assert fm.repeat_failure_abort == 3
    assert fm.advisory_abort_conditions == []


def test_advisory_abort_conditions_pass_through() -> None:
    text = VALID.replace(
        "success: [x]", "success: [x]\nabort_when: [the cat appears, Battery below 20%]"
    )
    fm = parse_glot_text(text).frontmatter
    assert fm.battery_abort_percent == 20
    assert fm.repeat_failure_abort is None
    assert fm.advisory_abort_conditions == ["the cat appears"]


@pytest.mark.parametrize(
    ("mutation", "needle"),
    [
        (lambda s: s.replace("---\nglot", "glot", 1), "missing frontmatter"),
        (lambda s: s.replace("---\n# Task", "# Task"), "unterminated"),
        # one past the newest, so a new spec version needs no edit here
        (lambda s: s.replace("glot: 0", f"glot: {GLOT_SPEC_VERSION + 1}"), "glot"),
        (lambda s: s.replace("success: [x]", "success: [x]\nrequires: [quack]"), "needs glot: 1"),
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
        (lambda s: s.replace("glot: 0", "glot: [0"), "YAML"),
    ],
)
def test_invalid_ducks_fail_fast(mutation, needle: str) -> None:
    with pytest.raises(GlotParseError) as exc:
        parse_glot_text(mutation(VALID), path="x.glot")
    assert needle.lower() in str(exc.value).lower()
    assert "x.glot" in str(exc.value)


def test_schema_json_in_sync() -> None:
    on_disk = json.loads(
        (Path("roboglot") / "glotfile" / "schema.json").read_text(encoding="utf-8")
    )
    assert on_disk == json_schema(), "run: uv run python -m roboglot.glotfile.export"


def test_defaults_applied() -> None:
    fm = parse_glot_text(VALID).frontmatter
    assert fm.budgets.max_steps == 40
    assert fm.budgets.max_minutes == 5
    assert fm.verbs.confirm == []
    assert fm.providers == []
    assert fm.requires == [] and fm.robots is None
    assert fm.effective_requires == ["quack"]  # a v0 task needs everything it allows


V1 = """\
---
glot: 1
name: t
description: d
requires: [observe, kick]
robots: {lerobot-01: lerobot:mock, duck-01: microduck:mock}
verbs:
  allow: [observe, gaze, go_to, kick, stop]
success: [x]
team:
  members: [lerobot-01, duck-01]
---
# Task
Do it.
"""
MEMBERS = "  members: [lerobot-01, duck-01]\n"
COORDINATOR_GONE = "Roboglot no longer has the coordinator team it was for"


def test_duck_v1_parses_requires_and_robots() -> None:
    fm = parse_glot_text(V1).frontmatter
    assert fm.glot == 1 and fm.effective_requires == ["observe", "kick"]
    assert fm.robots == {"lerobot-01": "lerobot:mock", "duck-01": "microduck:mock"}
    assert fm.team is not None and fm.team.member_names == ["lerobot-01", "duck-01"]
    solo = parse_glot_text(
        V1.replace(
            "robots: {lerobot-01: lerobot:mock, duck-01: microduck:mock}",
            "robots: microduck:mock",
        ).split("team:")[0]
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
        (lambda s: s.replace("microduck:mock", "Micro Duck"), "<adapter>"),
        # what only the coordinator team read is refused by its key, never dropped
        (
            lambda s: s.replace(
                MEMBERS, MEMBERS + "  roles:\n    spotter: {requires: [observe]}\n"
            ),
            f"team.roles: {COORDINATOR_GONE}",
        ),
        (
            lambda s: s.replace(MEMBERS, MEMBERS + "  frame_hints: auto\n"),
            f"team.frame_hints: {COORDINATOR_GONE}",
        ),
        (
            lambda s: s.replace(MEMBERS, MEMBERS + "  allocation: {method: auction}\n"),
            f"team.allocation.method: auction: {COORDINATOR_GONE}",
        ),
        (lambda s: s.replace("glot: 1", "glot: 0"), "a glot: 0 team was a coordinator team"),
    ],
)
def test_invalid_v1_ducks_fail_fast(mutation, needle: str) -> None:
    with pytest.raises(GlotParseError) as exc:
        parse_glot_text(mutation(V1), path="x.glot")
    assert needle.lower() in str(exc.value).lower()


# ── v2: the datasheet a task file corrects ──────────────────────────────────────────────

V2 = """\
---
glot: 2
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
    fm = parse_glot_text(V2).frontmatter
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
        (lambda s: s.replace("glot: 2", "glot: 1"), "needs glot: 2"),
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
            lambda s: s.replace("robots: lerobot:mock", "team:\n  members: 2"),
            "a team task cannot carry one",
        ),
    ],
)
def test_invalid_v2_ducks_fail_fast(mutation, needle: str) -> None:
    with pytest.raises(GlotParseError) as exc:
        parse_glot_text(mutation(V2), path="x.glot")
    assert needle.lower() in str(exc.value).lower()


# ── the strategy paragraph a `--goal` run writes ────────────────────────────────────────


def _goal_allowlist(spec: str) -> list[str]:
    """As `roboglot run --goal` builds it: the safe verbs of the STATIC manifest, plus `stop`.

    Static because a description is all the CLI has before it connects: the arm claims no
    camera until `connect()` finds one, which is why `observe` is absent from its goal
    allowlist and why the paragraph under test used to promise it anyway."""
    from roboglot.adapters.factory import RobotSpec, registry_for

    adapter, backend = spec.split(":")
    registry = registry_for(RobotSpec(adapter, backend))
    return sorted(verb.name for verb in registry.verbs() if verb.safety_class == "safe")


@pytest.mark.parametrize("spec", ["lerobot:real", "lerobot:mock", "microduck:mock"])
def test_a_goal_duck_names_no_verb_the_body_was_not_granted(spec: str) -> None:
    """The strategy paragraph `--goal` writes used to name `observe`, `search_scan` and
    `go_to` whatever the body was. On 2026-09-15 an SO-101 arm read all three in a prompt
    whose allowlist listed none of them, and looked with `report_state` on its own initiative.

    Every backticked verb in the body has to be one the contract allows. `remember` is the
    exception: it is a meta tool the loop offers beside the verbs rather than a verb."""
    import re

    from roboglot.glotfile.parser import glot_from_goal

    duck = glot_from_goal("wave to me", _goal_allowlist(spec), memory=True)
    named = set(re.findall(r"`([a-z_]+)`", duck.body))
    assert named, "the body names its verbs in backticks"
    allowed = set(duck.frontmatter.verbs.allow) | {"remember"}
    assert named <= allowed, f"{spec} is promised {sorted(named - allowed)}"


def test_an_arm_is_told_to_look_with_report_state_and_a_duck_keeps_its_composites() -> None:
    """The other half: narrowing the advice must not empty it. An arm is pointed at the verb
    it does have, and a Microduck reads the sentence it has always read, byte for byte."""
    from roboglot.glotfile.parser import glot_from_goal

    arm = glot_from_goal("wave to me", _goal_allowlist("lerobot:real")).body
    assert "Look before you act (`report_state`)" in arm
    assert "verify with a fresh reading" in arm, "an arm with no camera verb has no frame"
    assert "composite" not in arm and "go_to" not in arm

    duck = glot_from_goal("find the ball", _goal_allowlist("microduck:mock"), memory=True).body
    assert "Look before you act (`observe` or `search_scan`)" in duck
    assert "prefer composite verbs like `go_to`" in duck
    assert "verify with a fresh frame" in duck
