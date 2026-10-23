"""This folder stays deletable in one commit: nothing of it reaches quackd's release, and nothing
outside it names it.

quackd's sdist lists what it includes as gitignore patterns, and a pattern without a slash
matches at any depth. So a `README.md`, a `tests` or a `docs` in here would ship inside the
next quackd release on PyPI. These tests read the root's own patterns, so a pattern added there
later is checked too, and every other path any of quackd's ten packages builds from.

One check is slow and needs `uv` and the network, so it runs only when asked: it does what a
quackd release does (RELEASING.md, step 10) on this checkout and opens every file of every
package it builds. Run it before any quackd release while this folder exists:

    PASSPORT_NEBIUS_BUILD_CHECK=1 uv run pytest test/test_separation.py
"""

from __future__ import annotations

import fnmatch
import io
import os
import shutil
import subprocess
import tarfile
import tomllib
import zipfile
from pathlib import Path
from typing import Any

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


def _built_from(config: dict[str, Any]) -> list[str]:
    """Every path a package's hatch configuration builds from: its packages, its include and
    only-include lists, and the sources of its force-include, for every target."""
    build = config.get("tool", {}).get("hatch", {}).get("build", {})
    paths: list[str] = []
    for section in (build, *build.get("targets", {}).values()):
        for key in ("packages", "include", "only-include"):
            paths += [str(p) for p in section.get(key, [])]
        paths += [str(p) for p in section.get("force-include", {})]
    return paths


def test_no_package_quackd_builds_names_a_path_into_this_folder() -> None:
    """The core and the nine robot packages: a path that climbs out of its own package or names
    this folder's parent would let it in. The core's bare sdist patterns, which match at any
    depth, are held to this folder's names above."""
    here = FOLDER.relative_to(REPO).parts[0]
    configs = [REPO / "pyproject.toml", *sorted(REPO.glob("adapters/*/pyproject.toml"))]
    assert len(configs) == 10, "quackd publishes ten packages"
    for pyproject in configs:
        with pyproject.open("rb") as f:
            paths = _built_from(tomllib.load(f))
        assert paths, f"{pyproject} names nothing it builds from"
        for path in paths:
            parts = Path(path.strip("/")).parts
            assert ".." not in parts, f"{pyproject}: {path} climbs out of its package"
            assert parts[:1] != (here,), f"{pyproject}: {path} names this folder"


def _members(archive: Path) -> list[tuple[str, bytes]]:
    if archive.suffix == ".whl":
        with zipfile.ZipFile(archive) as z:
            return [(n, z.read(n)) for n in z.namelist() if not n.endswith("/")]
    with tarfile.open(archive, "r:gz") as t:
        return [(m.name, t.extractfile(m).read()) for m in t.getmembers() if m.isfile()]  # type: ignore[union-attr]


@pytest.mark.skipif(
    os.environ.get("PASSPORT_NEBIUS_BUILD_CHECK") != "1",
    reason="slow, needs uv and the network: set PASSPORT_NEBIUS_BUILD_CHECK=1",
)
def test_every_package_a_quackd_release_builds_holds_nothing_of_this_folder(
    tmp_path: Path,
) -> None:
    """RELEASING.md's step 10 on this checkout: `git archive`, then `uv build --all-packages`
    from what it unpacked, then every file of every package opened, by name and by content."""
    uv, git = shutil.which("uv"), shutil.which("git")
    if uv is None or git is None:
        pytest.skip("needs uv and git")
    archived = subprocess.run(
        [git, "archive", "--format=tar", "HEAD"], cwd=REPO, capture_output=True
    )
    assert archived.returncode == 0, archived.stderr
    source, dist = tmp_path / "src", tmp_path / "dist"
    with tarfile.open(fileobj=io.BytesIO(archived.stdout)) as t:
        t.extractall(source, filter="data")
    assert (source / FOLDER.relative_to(REPO)).is_dir(), "the archive a release builds from has it"
    built = subprocess.run(
        [uv, "build", "--all-packages", "--out-dir", str(dist)],
        cwd=source,
        capture_output=True,
        text=True,
    )
    assert built.returncode == 0, built.stderr[-2000:]
    packages = sorted(p for p in dist.iterdir() if p.name.endswith((".whl", ".tar.gz")))
    assert len(packages) == 20, "a sdist and a wheel for each of quackd's ten packages, no more"
    assert not [p.name for p in packages if p.name.startswith("passport")]
    caught = []
    for package in packages:
        for name, data in _members(package):
            if "hackathon" in name.lower() or b"nebius" in data.lower():
                caught.append(f"{package.name}: {name}")
    assert not caught, "these ship something of this folder:\n" + "\n".join(caught)


def test_it_can_never_be_uploaded() -> None:
    assert "Private :: Do Not Upload" in _ours()["project"]["classifiers"]


def test_ruff_reads_quackds_rules_here() -> None:
    """With no [tool.ruff] of its own, ruff lints this folder by the root's configuration."""
    assert "ruff" not in _ours().get("tool", {})


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git to search tracked files")
def test_nothing_outside_the_folder_names_it() -> None:
    """`git grep` exits 1 when nothing matches, 0 with the files that do, above 1 on a failure."""
    found = subprocess.run(
        ["git", "grep", "-I", "-i", "-l", "nebius", "--", ".", ":(exclude)hackathon/nebius-nvidia"],
        cwd=REPO,
        capture_output=True,
        check=False,
    )
    if found.returncode > 1:
        pytest.skip("not a git checkout")
    naming = found.stdout.decode("utf-8").split()
    assert not naming, f"these name the edition, so deleting the folder leaves them: {naming}"
