"""Turns a `.glot` file into a `GlotFile`, or fails loudly with a path and a reason.

The format is deliberately SKILL.md-shaped — YAML frontmatter between `---` fences, then a
Markdown body — so that people who write agent skills already know how to write a glot file.
Leading `#` comment lines above the first fence are allowed (see `tasks/fetch.glot`).
"""

from __future__ import annotations

from collections.abc import Sequence
from importlib import resources
from pathlib import Path

import yaml
from pydantic import ValidationError

from roboglot.glotfile.schema import GlotFile, GlotFrontmatter

FENCE = "---"


class GlotParseError(ValueError):
    """A `.glot` file that cannot be loaded. `path` is for the human, `reason` for the log."""

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
    # Notepad can save UTF-8 with a byte-order mark, which plain UTF-8 decodes to a character
    # in front of the first line that `strip()` keeps, so a valid file was refused as having no
    # frontmatter. One mark at the very start is how the file was encoded, not what it says, and
    # it is dropped here rather than in `load_glot` because every caller comes through here.
    lines = text.removeprefix("\ufeff").splitlines()
    i = 0
    while i < len(lines) and (not lines[i].strip() or lines[i].lstrip().startswith("#")):
        i += 1
    if i >= len(lines) or lines[i].strip() != FENCE:
        raise GlotParseError("missing frontmatter: file must start with a '---' fence")
    try:
        end = next(j for j in range(i + 1, len(lines)) if lines[j].strip() == FENCE)
    except StopIteration as e:
        raise GlotParseError("unterminated frontmatter: no closing '---' fence") from e
    yaml_text = "\n".join(lines[i + 1 : end])
    body = "\n".join(lines[end + 1 :]).strip("\n")
    return yaml_text, body


def parse_glot_text(text: str, path: str | None = None) -> GlotFile:
    try:
        yaml_text, body = split_frontmatter(text)
    except GlotParseError as e:
        raise GlotParseError(e.reason, path) from None
    try:
        data = yaml.safe_load(yaml_text)
    except yaml.YAMLError as e:
        raise GlotParseError(f"frontmatter is not valid YAML: {e}", path) from None
    if not isinstance(data, dict):
        raise GlotParseError("frontmatter must be a YAML mapping", path)
    try:
        frontmatter = GlotFrontmatter.model_validate(data)
    except ValidationError as e:
        raise GlotParseError(_format_validation_error(e), path) from None
    if not body.strip():
        raise GlotParseError("body is empty: a task file needs task instructions", path)
    return GlotFile(frontmatter=frontmatter, body=body, path=path)


#: A task file's endings: `.glot`, the one the repository uses, and `.task` and `.brief`,
#: which are read the same way.
SUFFIXES = (".glot", ".task", ".brief")


def bundled_tasks_dir() -> Path | None:
    """Where the starter tasks live: inside the wheel, or at the repo root in a checkout."""
    try:
        pkg_dir = Path(str(resources.files("roboglot"))) / "tasks"
    except (ModuleNotFoundError, TypeError):
        pkg_dir = None
    if pkg_dir and pkg_dir.is_dir():
        return pkg_dir
    repo_dir = Path(__file__).resolve().parents[2] / "tasks"
    return repo_dir if repo_dir.is_dir() else None


def resolve_glot_path(name_or_path: str) -> Path:
    """`tasks/x.glot`, `x.glot`, or just `x` — the last two fall back to the bundled set."""
    p = Path(name_or_path)
    if p.is_file():
        return p
    bundled = bundled_tasks_dir()
    if bundled is not None:
        for candidate in (bundled / p.name, *(bundled / f"{p.name}{s}" for s in SUFFIXES)):
            if candidate.is_file():
                return candidate
    raise GlotParseError("file not found (also not a bundled starter task)", name_or_path)


def load_glot(name_or_path: str) -> GlotFile:
    path = resolve_glot_path(name_or_path)
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as e:
        # Windows PowerShell 5.1 saves UTF-16 with `>` and `Out-File`, and the decode error
        # escaped every command that reads a .glot as a traceback rather than a sentence.
        if path.read_bytes()[:2] in (b"\xff\xfe", b"\xfe\xff"):
            reason = (
                "saved as UTF-16, which Roboglot does not read: save it as UTF-8 (Windows "
                "PowerShell 5.1's > writes UTF-16, and Set-Content -Encoding utf8 writes UTF-8)"
            )
        else:
            reason = f"not UTF-8 (byte {e.start} is not): save it as UTF-8"
        raise GlotParseError(reason, str(path)) from None
    return parse_glot_text(text, str(path))


def list_bundled_tasks() -> list[Path]:
    bundled = bundled_tasks_dir()
    return sorted(f for s in SUFFIXES for f in bundled.glob(f"*{s}")) if bundled else []


def glot_from_goal(
    goal: str, allow: list[str], *, confirm: Sequence[str] = (), memory: bool = False
) -> GlotFile:
    """An ad-hoc task for `roboglot run --goal "..."`: the goal is the body, the contract is
    permissive-but-safe (the given allowlist, default budgets, the standard abort rules).

    `confirm` is verbs the goal allows only behind the confirm gate, which asks a person at a
    terminal before each call unless `--yes`, or a pipe or file on stdin, answers for them.
    `roboglot run --goal` passes `manipulate` there when the arm has a policy server to hand it
    to (`--policy-url`), and nothing otherwise. A learned policy driving the arm for a segment
    is no safe verb, and without this a goal run given a policy could never use it: so each
    segment goes through the confirm gate instead, a deliberate exception to the rule that a
    goal allows safe verbs alone. Empty is every goal run without a policy, whose contract is
    what it was.

    `memory` is whether the run keeps a memory, and only then does the body ask for a
    `remember`. A `--goal --no-memory` run offered its pilot no such tool and told it twice to
    call one, which at best spends a call on a refusal. A task file is its author's words and
    is left as written (ADR-0025), but this body is Roboglot's own. False is the default because
    a run's memory is off until it is handed one (`RunConfig.memory`), so a caller that forgets
    to say loses a nudge rather than sending its pilot after a tool it was never offered."""
    goal = goal.strip()
    if not goal:
        raise GlotParseError("--goal must not be empty", "<goal>")
    if "stop" not in allow:
        allow = [*allow, "stop"]
    gated = [verb for verb in dict.fromkeys(confirm) if verb]
    allow = [*allow, *(verb for verb in gated if verb not in allow)]
    frontmatter = GlotFrontmatter(
        glot=0,
        name="goal",
        description=goal[:80],
        verbs={"allow": allow, "confirm": gated},  # type: ignore[arg-type]
        success=[
            "The goal as stated is achieved, as best you can verify from the camera and state."
        ],
        abort_when=["Battery below 15%", "Same verb fails 3 times in a row"],
        persona="Practical and honest: say so when you cannot do something.",
    )
    # the words the LLM reads live in one module, and this paragraph names verbs, so it is
    # written from the allowlist granted above rather than from the duck this used to assume
    from roboglot.agent.prompts import goal_strategy

    body = f"# Task\n{goal}\n\n## Strategy\n{goal_strategy(allow, memory=memory)}"
    if memory:
        body += (
            "\n\n## Memory\n"
            "Before you declare success or failure, call `remember` once with one short fact a "
            "future run can use (where something was, what worked, what to avoid), unless the "
            "prompt already remembers it. It costs no step."
        )
    return GlotFile(frontmatter=frontmatter, body=body, path="<goal>")
