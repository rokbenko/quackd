"""Nine packages are released together, so nothing about them may drift apart.

quackd is one repository and nine distributions now: the core and one per robot. That buys
an install with no robot in it, and it costs a set of facts that have to agree and that no
single file owns. A version in nine places, a catalogue in the core that names eight
adapters none of which it imports, an entry point in each of those eight, and a dependency
window in both directions. Every one of those is a thing somebody can half-update.
"""

from __future__ import annotations

import importlib
import importlib.metadata
import importlib.util
import re
import shutil
import tomllib
from pathlib import Path

import pytest

import quackd
from quackd.adapters.catalogue import BY_NAME, ENTRY_POINT_GROUP, OFFICIAL
from quackd.adapters.factory import adapter_names, info, is_installed

REPO = Path(__file__).resolve().parents[1]
MEMBERS = sorted(p for p in (REPO / "adapters").glob("*/pyproject.toml"))
NAMES = [p.parent.name for p in MEMBERS]


def _toml(path: Path) -> dict:
    return tomllib.loads(path.read_text(encoding="utf-8"))


def _dist(requirement: str) -> str:
    """The distribution a requirement names, without its extras or its version window."""
    return re.split(r"[\[><=]", requirement, maxsplit=1)[0]


WINDOW = re.compile(
    r"(?P<dist>quackd(?:-[a-z-]+)?)(?:\[[a-z]+\])?"
    r">=(?P<floor>\d+\.\d+(?:\.\d+)?),<(?P<ceiling>\d+\.\d+)"
)
"""A window one of the nine packages pins another with, as `scripts/set_version.py` writes it."""


def _release(text: str) -> tuple[int, ...]:
    """A version's numbers, with the zeros PEP 440 reads into a shorter one: 0.16 is 0.16.0."""
    numbers = tuple(int(n) for n in text.split("."))
    return numbers + (0,) * (3 - len(numbers))


def _window_breach(requirement: str, version: str) -> str | None:
    """What is wrong with the window `requirement` pins for packages released as `version`, or
    None. RELEASING.md: a window starts at the release it ships in and ends before the next
    minor, `>=X.Y.Z,<X.Y+1`, so a patch raises every floor to itself and a minor moves the
    whole window. A window written before 0.16.1, from the minor alone (`>=0.16`), starts at
    the same release, since PEP 440 reads 0.16 as 0.16.0."""
    window = WINDOW.fullmatch(requirement)
    if window is None:
        return f"{requirement} has no window scripts/set_version.py writes"
    major, minor, _ = _release(version)
    if _release(window["floor"]) != _release(version):
        return f"{requirement} does not start at {version}, the release it ships in"
    if _release(window["ceiling"]) != (major, minor + 1, 0):
        return f"{requirement} does not end before {major}.{minor + 1}, the next minor"
    return None


def test_every_adapter_directory_is_a_workspace_member() -> None:
    """`uv` resolves a member from the glob in the root, so a package that is not matched by
    it installs from PyPI instead of from the checkout, and a contributor's change to it is
    silently not the one under test."""
    root = _toml(REPO / "pyproject.toml")
    assert root["tool"]["uv"]["workspace"]["members"] == ["adapters/*"]
    sources = root["tool"]["uv"]["sources"]
    for name in NAMES:
        dist = f"quackd-{name.replace('_', '-')}"
        assert sources.get(dist) == {"workspace": True}, dist


@pytest.mark.parametrize("member", MEMBERS, ids=NAMES)
def test_an_adapter_carries_the_same_version_as_the_core(member: Path) -> None:
    """Each package carries its own `__version__`, because an adapter's sdist holds only its
    own source and cannot read the core's. They ship together, so they must not differ.
    `scripts/set_version.py` writes all nine at once for exactly this reason."""
    src = next((member.parent / "src").glob("quackd_*/__init__.py"))
    found = re.search(r'^__version__ = "([^"]+)"$', src.read_text(encoding="utf-8"), re.M)
    assert found, f"{src} has no __version__"
    assert found.group(1) == quackd.__version__


@pytest.mark.parametrize("member", MEMBERS, ids=NAMES)
def test_an_adapter_allows_the_core_it_is_released_with_and_none_before_it(member: Path) -> None:
    """The window an adapter allows has to admit the core it ships beside, or the release
    resolves to nothing on the day it is published. It admits no core from before it either,
    so an adapter from a patch never installs beside a core that lacks what the patch gave it,
    and it ends before the next minor, where the interface moves (RELEASING.md)."""
    data = _toml(member)
    pin = next(d for d in data["project"]["dependencies"] if d.startswith("quackd>="))
    breach = _window_breach(pin, quackd.__version__)
    assert breach is None, breach


@pytest.mark.parametrize("member", MEMBERS, ids=NAMES)
def test_an_adapter_declares_the_entry_point_that_makes_it_findable(member: Path) -> None:
    """The entry point is the whole mechanism: an adapter that does not declare one is
    installed and invisible, which looks exactly like an adapter that is not installed."""
    name = member.parent.name
    declared = _toml(member)["project"]["entry-points"][ENTRY_POINT_GROUP]
    assert declared == {name: f"quackd_{name}"}, declared


def test_the_core_publishes_an_extra_for_every_robot_it_names() -> None:
    """The catalogue is what `list-adapters` prints on a machine with nothing installed, so
    the extra it names has to be one that exists and that pulls in that adapter's package."""
    extras = _toml(REPO / "pyproject.toml")["project"]["optional-dependencies"]
    for row in OFFICIAL:
        assert row.extra, f"{row.name} names no extra to install it with"
        key = row.extra.removeprefix("quackd[").removesuffix("]")
        assert key in extras, f"{row.extra} is not an extra this package has"
        dist = f"quackd-{row.name.replace('_', '-')}"
        assert any(d.startswith(dist) for d in extras[key]), (key, extras[key])
    assert {_dist(d) for d in extras["robots"]} == {
        f"quackd-{r.name.replace('_', '-')}" for r in OFFICIAL
    }


def test_every_extra_pins_the_adapter_it_installs_to_this_release() -> None:
    """An extra that names an adapter and no window lets `quackd[microduck]==0.10.0` resolve an
    adapter from another release, which for the first release that publishes them means a later
    one, and the only thing that rejects that is the adapter's own back-pin, which arrives as a
    resolver error rather than as the right version. Both windows are written by
    `scripts/set_version.py` in one pass, so they have to agree: each starts at this release and
    ends before the next minor. `dev` is exempt: it is resolved from the workspace by
    `[tool.uv.sources]` and is never published to anybody."""
    extras = _toml(REPO / "pyproject.toml")["project"]["optional-dependencies"]
    breaches = []
    for key, requirements in extras.items():
        if key == "dev":
            continue
        for requirement in requirements:
            if not requirement.startswith("quackd-"):
                continue
            if (breach := _window_breach(requirement, quackd.__version__)) is not None:
                breaches.append(f"{key}: {breach}")
    assert not breaches, breaches


def _windows(root: Path) -> list[str]:
    """Every window in the nine `pyproject.toml` files under `root`, as written."""
    found = []
    for path in [root / "pyproject.toml", *sorted(root.glob("adapters/*/pyproject.toml"))]:
        found += re.findall(r'"(quackd[^"]*>=[^"]*)"', path.read_text(encoding="utf-8"))
    return found


def test_set_version_starts_every_window_at_the_release_for_a_patch_and_for_a_minor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`scripts/set_version.py`, run on a copy of the nine packages for the next patch and then
    the next minor, writes every window this suite holds the nine to: a patch raises each
    floor to itself, `>=X.Y.Z,<X.Y+1`, and a minor moves the whole window, `>=X.Y.0,<X.Y+1`. It
    has to read the windows written before 0.16.1, from the minor alone, as well as the ones it
    writes, or the release after would leave every window where it was."""
    spec = importlib.util.spec_from_file_location(
        "set_version", REPO / "scripts" / "set_version.py"
    )
    assert spec and spec.loader
    script = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(script)
    kept = [REPO / "pyproject.toml", *MEMBERS, *script.version_files()]
    for path in kept:
        copy = tmp_path / path.relative_to(REPO)
        copy.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, copy)
    monkeypatch.setattr(script, "REPO", tmp_path)
    count = len(_windows(REPO))
    assert count > len(MEMBERS), "the core's extras pin adapters too"
    major, minor, patch = _release(quackd.__version__)
    for version in (f"{major}.{minor}.{patch + 1}", f"{major}.{minor + 1}.0"):
        assert script.main(["set_version.py", version]) == 0
        windows = _windows(tmp_path)
        assert len(windows) == count, windows
        for window in windows:
            assert f">={version},<" in window, window
            breach = _window_breach(window, version)
            assert breach is None, breach
        for path in script.version_files():
            assert f'__version__ = "{version}"' in path.read_text(encoding="utf-8"), path


def test_the_catalogue_and_the_installed_adapter_agree_about_the_robot() -> None:
    """The catalogue describes a robot without importing it, which is the only way to list one
    that is not installed. Nothing checks that description against the adapter itself, so this
    does: a backend added to one and not the other is a `--robot` that parses and cannot run."""
    for name in adapter_names():
        if not is_installed(name):
            continue
        module = importlib.import_module(f"quackd_{name}")
        assert tuple(module.BACKENDS) == info(name).backends, name
        assert name in BY_NAME, f"{name} is installed but quackd does not publish it"


def test_every_published_adapter_is_installed_for_the_suite() -> None:
    """The dev extra carries all eight without their SDKs, so the suite drives every body
    against a mock. One missing would not fail loudly; its tests would quietly skip."""
    installed = {ep.name for ep in importlib.metadata.entry_points(group=ENTRY_POINT_GROUP)}
    assert set(BY_NAME) <= installed, set(BY_NAME) - installed
