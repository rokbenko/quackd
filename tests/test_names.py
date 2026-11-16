"""The names this project left behind stay left behind.

Roboglot began with one duck robot, and for a while every robot in the code was a duck, a task
file was named after one and several robots were a fleet. Those words were replaced in the code,
in what a person reads and in the records a run keeps, and ADR-0017, ADR-0035 and ADR-0080 carry
dated notes that say so. A rename nothing guards comes back one paste at a time. The history
keeps the old words on purpose: CHANGELOG.md, docs/adr/ and docs/design/ record what was true
when they were written, and this file has to spell what it looks for."""

from __future__ import annotations

import functools
import json
import re
import shutil
import subprocess
import tomllib
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from tests.test_docs import _living_docs

REPO = Path(__file__).resolve().parents[1]

HISTORY = re.compile(r"CHANGELOG\.md|docs/(?:adr|design)/.+|tests/test_names\.py")
"""What may keep a retired word: the records of what was true when they were written, and this
file, which names each word it looks for."""

EARLIER = "qua" + "ckd"
"""The project's earlier name, in two pieces so that this file does not carry it as one word."""


@functools.cache
def _tracked_texts() -> tuple[tuple[str, str], ...]:
    """Every file git tracks here that reads as UTF-8, by its path from the root.

    Tracked rather than walked, so a virtual environment, a build and a cache are never read,
    and an unpacked sdist, which is no checkout and holds no list of its own, skips."""
    if shutil.which("git") is None:
        pytest.skip("git is not installed, so nothing says which files are the project's")
    listed = subprocess.run(["git", "ls-files", "-z"], cwd=REPO, capture_output=True)
    if listed.returncode != 0:
        pytest.skip("not a git checkout, so nothing says which files are the project's")
    texts = []
    for path in sorted(p for p in listed.stdout.decode("utf-8").split("\0") if p):
        try:
            texts.append((path, (REPO / path).read_bytes().decode("utf-8")))
        except (FileNotFoundError, UnicodeDecodeError):
            continue  # deleted and not yet staged, or a picture
    assert len(texts) > 500, f"only {len(texts)} tracked files read"
    return tuple(texts)


def _line(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def _repository() -> tuple[str, str]:
    """The owner and the last segment of the repository's address in pyproject.toml."""
    project = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    owner, name = urlsplit(project["urls"]["Repository"]).path.strip("/").split("/")[:2]
    return owner, name


def test_the_earlier_name_is_only_ever_part_of_the_repository_s_address() -> None:
    """Everything but the GitHub repository took the new name, and the repository keeps the old
    one until its owner renames it, because people know the project by that link (ADR-0080).
    Anywhere else the earlier name is a leftover: a word a person reads, a variable, a key on disk
    that nothing reads any more. The history is held to this as well, since the rename rewrote it.

    The address that may carry it is built from the URL, so on the day the repository is renamed
    nothing carries it legitimately any more, and every occurrence left fails here."""
    owner, name = _repository()
    allowed = re.compile(re.escape(owner) + "(?:/|%2F)" + re.escape(name), re.IGNORECASE)
    needle = re.compile(re.escape(EARLIER), re.IGNORECASE)
    found = []
    for path, text in _tracked_texts():
        inside = [m.span() for m in allowed.finditer(text)]
        found += [
            f"{path}:{_line(text, m.start())}"
            for m in needle.finditer(text)
            if not any(start <= m.start() and m.end() <= end for start, end in inside)
        ]
    assert not found, "the earlier name outside the repository's address:\n" + "\n".join(found)


#: Each retired word: what it was, its pattern, and which tracked files may not say it, the
#: history aside.
RETIRED: dict[str, tuple[str, re.Pattern[str], re.Pattern[str]]] = {
    # the protocol and the state every adapter is built on, the 0.3 name of RobotSession, and
    # what the MCP server called the robots it serves: no alias was kept, so a reader that finds
    # one of these in a page or a comment is sent to a name that does not import
    "old-api-names": (
        "an old name of a class or a function",
        re.compile(
            r"\b(?:DuckTransport|DuckState|DuckSession|FleetPlan|build_fleet_server"
            r"|fleet_from_flags|FLEET_INSTRUCTIONS|FLEET_MEMORY)\b"
        ),
        re.compile(r".+"),
    ),
    # `ServedTeam` now; in prose a "Fleet" is the word ADR-0035 retired, which the guard in
    # test_docs.py reads for, so only the code is held to the class name here
    "fleet-class": (
        "the class the MCP server's robots were",
        re.compile(r"\bFleet\b"),
        re.compile(r".+\.py"),
    ),
    # the task file's old extension at the end of a name, which also catches an attribute
    # spelled the same, the old name of `RunConfig.glot` and its kin; `duck.` and `open_duck`
    # are something else and pass
    "task-file-extension": (
        "the task file's old extension",
        re.compile(r"\.duck(?![\w-])", re.IGNORECASE),
        re.compile(r".+"),
    ),
    "task-file-name": (
        "the task file's old name",
        re.compile("duckfile", re.IGNORECASE),
        re.compile(r".+"),
    ),
}


@pytest.mark.parametrize("word", list(RETIRED))
def test_a_retired_name_is_said_nowhere_but_in_the_history(word: str) -> None:
    """A renamed class with no alias, or a file extension nothing reads, is worse than stale
    when it comes back: a page that names it sends a person to an import that fails or a file
    Roboglot ignores. Only the history may say these, because it records what was true then."""
    what, pattern, scope = RETIRED[word]
    found = [
        f"{path}:{_line(text, m.start())}: {m.group()}"
        for path, text in _tracked_texts()
        if scope.fullmatch(path) and not HISTORY.fullmatch(path)
        for m in pattern.finditer(text)
    ]
    assert not found, f"{what} is back:\n" + "\n".join(found)


DEFAULT_MEMBER = re.compile(r"(?<![\w-])duck-(?:\d(?!\d)|\{)")
"""`duck-0`, `duck-1` and so on, and the f-string that made them: what a team that counts its
members called them. `duck-01`, a robot's own name, and `open-duck-0`, are not this."""


def test_a_counted_team_s_members_are_robots_in_the_pages_and_the_code() -> None:
    """`members: 3` names a team's members `robot-0` to `robot-2`, and a page or a message that
    still says `duck-0` has a person write a `robots:` entry no member reads, so that member
    takes another body. Tests may call their members anything, so this reads the living pages
    and `roboglot/` only, the published schema included."""
    sources = [p for p in (REPO / "roboglot").rglob("*") if p.is_file()]
    found = []
    for path in _living_docs() + sources:
        if "__pycache__" in path.parts:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        found += [
            f"{path.relative_to(REPO).as_posix()}:{_line(text, m.start())}: {m.group()}"
            for m in DEFAULT_MEMBER.finditer(text)
        ]
    assert len(sources) > 100, "roboglot/ was not read"
    assert not found, "a counted member is still a duck:\n" + "\n".join(found)


DUCK_BODIES = ("microduck", "open_duck", "huebot")
"""The adapters whose body is a duck: the Microduck, the Open Duck Mini and Huebot."""

DUCK = re.compile(r"\bducks?\b", re.IGNORECASE)


def _bodies_that_are_not_ducks() -> list[str]:
    """Every installed adapter with a mock whose body is not a duck, read from the factory so
    that an adapter added later is held to this without anyone remembering to list it."""
    from roboglot.adapters.factory import adapter_names, info

    return [
        name
        for name in adapter_names()
        if name not in DUCK_BODIES and "mock" in info(name).backends
    ]


@pytest.mark.parametrize("adapter", _bodies_that_are_not_ducks())
def test_a_run_on_a_body_that_is_no_duck_never_calls_it_one(tmp_path: Path, adapter: str) -> None:
    """For a long time Roboglot said duck to whatever it drove: the glyph on the run's panel, the
    hint under it, a team's default members, the MCP server's name for a robot. An arm told it is
    a duck, or a person told the duck has stopped while a rover drives on, is a wrong word on the
    one screen that has to be right. The smallest scripted run on each such body, its own lookout
    where it ships one, with the log and the system prompt on screen, and then every record the
    run kept, the system prompt included, say robot or the body's own name."""
    from typer.testing import CliRunner

    from roboglot.cli import app

    lookout = f"{adapter.replace('_', '-')}-lookout"
    task = [lookout] if (REPO / "tasks" / f"{lookout}.glot").exists() else ["--goal", "look around"]
    argv = ["run", *task, "--robot", f"{adapter}:mock", "--llm", "fake", "--no-memory"]
    result = CliRunner().invoke(
        app,
        [*argv, "--log", "--log-prompt", "--runs-dir", str(tmp_path)],
        env={"COLUMNS": "200"},
    )
    assert result.exit_code == 0, result.output
    said = [m.group() for m in DUCK.finditer(result.output)]
    assert not said, f"{adapter}:mock was called a duck on screen:\n{result.output}"
    (run_dir,) = [d for d in tmp_path.iterdir() if d.is_dir()]
    lines = (run_dir / "transcript.jsonl").read_text(encoding="utf-8").splitlines()
    records = [json.loads(line) for line in lines if line.strip()]
    (start,) = [r for r in records if r.get("kind") == "run_start"]
    assert len(start.get("system_prompt") or "") > 500, "the run kept no system prompt to read"
    for record in records:
        text = json.dumps(record, ensure_ascii=False)
        assert not DUCK.search(text), f"{adapter}:mock was called a duck in its record: {text}"


def test_a_body_whose_manifest_has_no_blurb_is_introduced_as_a_robot() -> None:
    """The system prompt's first sentence introduces the body by its manifest's blurb, and for a
    manifest with none it borrowed the Microduck's, so an adapter of somebody's own that left
    the field empty had its arm or its rover told it is a small biped duck. Every body Roboglot
    ships has a blurb, which is why no run above can show this: a described arm's manifest is
    used here with its blurb taken out."""
    from roboglot.adapters.described import body_manifest
    from roboglot.agent.prompts import DUCK_BLURB, ROBOT_BLURB, build_system_prompt
    from roboglot.glotfile.parser import load_glot
    from roboglot.verbs.registry import default_registry

    registry = default_registry()
    glot = load_glot(str(REPO / "tasks" / "hello-world.glot"))
    verbs = [registry.view(name) for name in glot.frontmatter.verbs.allow if name in registry]
    arm = body_manifest("ur5e")
    assert arm.blurb, "the described arm has a blurb of its own to take out"
    prompt = build_system_prompt(glot, verbs, manifest=arm.model_copy(update={"blurb": ""}))
    assert f"You are the brain of {ROBOT_BLURB}." in prompt
    assert DUCK_BLURB not in prompt
    assert not DUCK.search(ROBOT_BLURB)
