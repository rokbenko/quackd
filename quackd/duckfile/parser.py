"""Turns a `.duck` file into a `DuckFile`, or fails loudly with a path and a reason.

The format is deliberately SKILL.md-shaped — YAML frontmatter between `---` fences, then a
Markdown body — so that people who write agent skills already know how to write a duck.
Leading `#` comment lines above the first fence are allowed (see `ducks/fetch.duck`).
"""

from __future__ import annotations

from collections.abc import Sequence
from importlib import resources
from pathlib import Path

import yaml
from pydantic import ValidationError

from quackd.duckfile.schema import DuckFile, DuckFrontmatter

FENCE = "---"


class DuckParseError(ValueError):
    """A `.duck` file that cannot be loaded. `path` is for the human, `reason` for the log."""

    def __init__(self, reason: str, path: str | None = None) -> None:
        self.reason = reason
        self.path = path
        super().__init__(f"{path or '<text>'}: {reason}")


def _format_validation_error(exc: ValidationError) -> str:
    lines = []
    for err in exc.errors():
        loc = ".".join(str(p) for p in err["loc"]) or "<root>"
        lines.append(f"{loc}: {err['msg']}")
    return "; ".join(lines)


def split_frontmatter(text: str) -> tuple[str, str]:
    """Return (yaml_text, body). A byte-order mark, and leading blank and `#` comment lines,
    are skipped."""
    # Notepad saves UTF-8 with a byte-order mark, which plain UTF-8 decodes to a character in
    # front of the first fence that `strip()` keeps, so a valid file was refused as having no
    # frontmatter. One mark at the very start is how the file was encoded, not what it says, and
    # it is dropped here rather than in `load_duck` because every caller comes through here.
    lines = text.removeprefix("﻿").splitlines()
    i = 0
    while i < len(lines) and (not lines[i].strip() or lines[i].lstrip().startswith("#")):
        i += 1
    if i >= len(lines) or lines[i].strip() != FENCE:
        raise DuckParseError("missing frontmatter: file must start with a '---' fence")
    try:
        end = next(j for j in range(i + 1, len(lines)) if lines[j].strip() == FENCE)
    except StopIteration as e:
        raise DuckParseError("unterminated frontmatter: no closing '---' fence") from e
    yaml_text = "\n".join(lines[i + 1 : end])
    body = "\n".join(lines[end + 1 :]).strip("\n")
    return yaml_text, body


def parse_duck_text(text: str, path: str | None = None) -> DuckFile:
    try:
        yaml_text, body = split_frontmatter(text)
    except DuckParseError as e:
        raise DuckParseError(e.reason, path) from None
    try:
        data = yaml.safe_load(yaml_text)
    except yaml.YAMLError as e:
        raise DuckParseError(f"frontmatter is not valid YAML: {e}", path) from None
    if not isinstance(data, dict):
        raise DuckParseError("frontmatter must be a YAML mapping", path)
    try:
        frontmatter = DuckFrontmatter.model_validate(data)
    except ValidationError as e:
        raise DuckParseError(_format_validation_error(e), path) from None
    if not body.strip():
        raise DuckParseError("body is empty: the LLM needs task instructions", path)
    return DuckFile(frontmatter=frontmatter, body=body, path=path)


def bundled_ducks_dir() -> Path | None:
    """Where the starter ducks live: inside the wheel, or at the repo root in a checkout."""
    try:
        pkg_dir = Path(str(resources.files("quackd"))) / "ducks"
    except (ModuleNotFoundError, TypeError):
        pkg_dir = None
    if pkg_dir and pkg_dir.is_dir():
        return pkg_dir
    repo_dir = Path(__file__).resolve().parents[2] / "ducks"
    return repo_dir if repo_dir.is_dir() else None


def resolve_duck_path(name_or_path: str) -> Path:
    """`ducks/x.duck`, `x.duck`, or just `x` — the last two fall back to the bundled set."""
    p = Path(name_or_path)
    if p.is_file():
        return p
    bundled = bundled_ducks_dir()
    if bundled is not None:
        for candidate in (bundled / p.name, bundled / f"{p.name}.duck"):
            if candidate.is_file():
                return candidate
    raise DuckParseError("file not found (also not a bundled starter duck)", name_or_path)


def load_duck(name_or_path: str) -> DuckFile:
    path = resolve_duck_path(name_or_path)
    return parse_duck_text(path.read_text(encoding="utf-8"), str(path))


def list_bundled_ducks() -> list[Path]:
    bundled = bundled_ducks_dir()
    return sorted(bundled.glob("*.duck")) if bundled else []


def duck_from_goal(goal: str, allow: list[str], *, confirm: Sequence[str] = ()) -> DuckFile:
    """An ad-hoc duck for `quackd run --goal "..."`: the goal is the body, the contract is
    permissive-but-safe (the given allowlist, default budgets, the standard abort rules).

    `confirm` is verbs the goal allows only behind the confirm gate, which asks a person at a
    terminal before each call unless `--yes`, or a pipe or file on stdin, answers for them.
    `quackd run --goal` passes `manipulate` there when the arm has a policy server to hand it
    to (`--policy-url`), and nothing otherwise. A learned policy driving the arm for a segment
    is no safe verb, and without this a goal run given a policy could never use it: so each
    segment goes through the confirm gate instead, a deliberate exception to the rule that a
    goal allows safe verbs alone. Empty is every goal run without a policy, whose contract is
    what it was."""
    goal = goal.strip()
    if not goal:
        raise DuckParseError("--goal must not be empty", "<goal>")
    if "stop" not in allow:
        allow = [*allow, "stop"]
    gated = [verb for verb in dict.fromkeys(confirm) if verb]
    allow = [*allow, *(verb for verb in gated if verb not in allow)]
    frontmatter = DuckFrontmatter(
        duck=0,
        name="goal",
        description=goal[:80],
        verbs={"allow": allow, "confirm": gated},  # type: ignore[arg-type]
        success=[
            "The goal as stated is achieved, as best you can verify from the camera and state."
        ],
        abort_when=["Battery below 15%", "Same verb fails 3 times in a row"],
        persona="Practical and honest: say so when you cannot do something.",
    )
    # the words the model reads live in one module, and this paragraph names verbs, so it is
    # written from the allowlist granted above rather than from the duck this used to assume
    from quackd.agent.prompts import goal_strategy

    body = (
        f"# Task\n{goal}\n\n## Strategy\n"
        f"{goal_strategy(allow)}\n\n"
        "## Memory\n"
        "Before you declare success or failure, call `remember` once with one short fact a "
        "future run can use (where something was, what worked, what to avoid), unless the "
        "prompt already remembers it. It costs no step."
    )
    return DuckFile(frontmatter=frontmatter, body=body, path="<goal>")
