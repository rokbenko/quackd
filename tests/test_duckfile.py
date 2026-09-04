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
        "reachy-spotter",
        "reachy-spots-duck-kicks",
        "open-duck-scout",
        "open-duck-lookout",
        "microduck-lookout",
    }


def test_resolve_by_bundled_name() -> None:
    assert resolve_duck_path("hello-world").name == "hello-world.duck"
    assert resolve_duck_path("hello-world.duck").name == "hello-world.duck"
    with pytest.raises(DuckParseError):
        resolve_duck_path("no-such-duck")


def test_leading_comments_allowed() -> None:
    duck = parse_duck_text("# a comment\n\n# another\n" + VALID)
    assert duck.name == "t"


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
        (lambda s: s.replace("duck: 0", "duck: 2"), "duck"),
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
robots: {reachy-01: reachy_mini:sim2d, duck-01: microduck:sim2d}
verbs:
  allow: [observe, gaze, go_to, kick, stop]
success: [x]
flock:
  members: [reachy-01, duck-01]
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
    assert fm.robots == {"reachy-01": "reachy_mini:sim2d", "duck-01": "microduck:sim2d"}
    assert fm.flock is not None and fm.flock.roles is not None
    assert fm.flock.roles["kicker"].requires == ["go_to", "kick"]
    assert fm.flock.frame_hints == "auto"
    solo = parse_duck_text(
        V1.replace(
            "robots: {reachy-01: reachy_mini:sim2d, duck-01: microduck:sim2d}",
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
        (lambda s: s.replace("members: [reachy-01, duck-01]", "members: 2"), "name the members"),
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
