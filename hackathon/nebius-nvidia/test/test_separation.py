"""This folder stays deletable in one commit: nothing of it reaches quackd's release, and nothing
outside it names it.

quackd's sdist lists what it includes as gitignore patterns, and a pattern without a slash
matches at any depth. So a `README.md`, a `tests` or a `docs` in here would ship inside the
next quackd release on PyPI. These tests read the root's own patterns, so a pattern added there
later is checked too.
"""

from __future__ import annotations

import fnmatch
import os
import shutil
import subprocess
import tomllib
from pathlib import Path

import pytest

FOLDER = Path(__file__).resolve().parents[1]
REPO = FOLDER.parents[1]
#: Directories that are this checkout's own, never the folder's: gitignored, so never shipped.
_LOCAL = {".venv", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", "runs"}


def _root() -> dict:
    with (REPO / "pyproject.toml").open("rb") as f:
        return tomllib.load(f)


def _ours() -> dict:
    with (FOLDER / "pyproject.toml").open("rb") as f:
        return tomllib.load(f)


def _paths() -> list[Path]:
    """Every file and directory in the folder that git could track, relative to the repo."""
    found: list[Path] = []
    for root, dirs, files in os.walk(FOLDER):
        dirs[:] = [d for d in dirs if d not in _LOCAL]
        here = Path(root)
        found += [(here / d).relative_to(REPO) for d in dirs]
        found += [(here / f).relative_to(REPO) for f in files if not f.startswith(".env")]
    return found


def test_quackd_is_where_this_folder_expects_it() -> None:
    assert _root()["project"]["name"] == "quackd"
    assert FOLDER.relative_to(REPO).parts == ("hackathon", "nebius-nvidia")


def _sdist_patterns() -> list[str]:
    return list(_root()["tool"]["hatch"]["build"]["targets"]["sdist"]["include"])


def _caught(paths: list[Path], patterns: list[str]) -> list[str]:
    """The paths a gitignore-style include list takes: a pattern with no slash inside it matches
    any one part of a path, at any depth; one with a slash is anchored at the repository root."""
    bare = [p.strip("/") for p in patterns if "/" not in p.strip("/")]
    anchored = [p.strip("/") for p in patterns if "/" in p.strip("/")]
    caught = []
    for path in paths:
        posix = path.as_posix()
        caught += [
            f"{posix} matches {p!r}"
            for p in bare
            if any(fnmatch.fnmatchcase(part, p) for part in path.parts)
        ]
        caught += [
            f"{posix} matches /{p}"
            for p in anchored
            if fnmatch.fnmatchcase(posix, p) or posix.startswith(p + "/")
        ]
    return caught


def test_the_check_catches_what_it_is_for() -> None:
    here = Path("hackathon", "nebius-nvidia")
    patterns = _sdist_patterns()
    assert _caught([here / "README.md"], patterns)
    assert _caught([here / "tests" / "test_x.py"], patterns)
    assert _caught([here / "ducks" / "a.duck"], patterns)
    assert not _caught([here / "README.markdown", here / "test" / "test_x.py"], patterns)


def test_no_path_here_matches_what_the_quackd_sdist_includes() -> None:
    caught = _caught(_paths(), _sdist_patterns())
    assert not caught, "these would ship in quackd's sdist:\n" + "\n".join(caught)


def test_the_folder_is_not_a_member_of_quackds_workspace() -> None:
    members = _root()["tool"]["uv"]["workspace"]["members"]
    here = FOLDER.relative_to(REPO).as_posix()
    assert not any(fnmatch.fnmatchcase(here, m) for m in members)


def test_it_can_never_be_uploaded() -> None:
    assert "Private :: Do Not Upload" in _ours()["project"]["classifiers"]


def test_ruff_reads_quackds_rules_here() -> None:
    """With no [tool.ruff] of its own, ruff lints this folder by the root's configuration."""
    assert "ruff" not in _ours().get("tool", {})


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git to search tracked files")
def test_nothing_outside_the_folder_names_it() -> None:
    """`git grep` exits 1 when nothing matches, 0 with the files that do, above 1 on a failure."""
    found = subprocess.run(
        ["git", "grep", "-I", "-i", "-l", "nebius", "--", ".", ":(exclude)hackathon"],
        cwd=REPO,
        capture_output=True,
        check=False,
    )
    if found.returncode > 1:
        pytest.skip("not a git checkout")
    naming = found.stdout.decode("utf-8").split()
    assert not naming, f"these name the edition, so deleting the folder leaves them: {naming}"
