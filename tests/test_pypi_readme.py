"""The README is the PyPI long description, and PyPI cannot follow a relative link.

`README.md`'s links are relative on purpose: that is what is correct on GitHub, where a
relative link follows the branch you are reading. On the PyPI project page there is no
branch, so every one of them 404s. `hatch_build.py` rewrites them at build time.

That rewrite is invisible when it breaks: nothing in a normal run reads the built metadata,
and the only symptom is dead links on a page nobody on the team opens. So this test asserts
the property directly, without building anything. It checks both ways this README writes a
link, because the first cut of the hook handled only Markdown and left four raw `<a href>`
links 404ing while a narrower version of this test passed.
"""

from __future__ import annotations

import re
import tomllib

import pytest

from hatch_build import BLOB, ReadmeHook, absolutise, pypi_readme, relative_links
from tests.conftest import REPO


def test_the_readme_itself_keeps_its_relative_links() -> None:
    """The repository's own README must NOT be absolutised: relative is right on GitHub."""
    readme = (REPO / "README.md").read_text(encoding="utf-8")
    assert relative_links(readme), "README has no relative links left to rewrite"


def test_the_built_long_description_has_no_relative_links() -> None:
    left = relative_links(pypi_readme(REPO))
    assert not left, f"these would 404 on the PyPI project page: {left}"


def test_both_link_syntaxes_are_covered() -> None:
    """Markdown and raw HTML, because this README uses both and only one was handled."""
    readme = (REPO / "README.md").read_text(encoding="utf-8")
    md = re.compile(r"\]\((?!https?://|#|mailto:)([^)]+)\)").findall(readme)
    html = re.compile(r'<a\s[^>]*href="(?!https?://|#|mailto:)([^"]+)"', re.I).findall(readme)
    assert md and html, "expected both link styles in the README"
    built = pypi_readme(REPO)
    for target in set(md) | set(html):
        assert f"{BLOB}{target}" in built, f"{target} was not rewritten"


def test_only_the_link_targets_change() -> None:
    readme = (REPO / "README.md").read_text(encoding="utf-8")
    built = pypi_readme(REPO)
    n = len(relative_links(readme))
    assert n > 0
    assert len(built) == len(readme) + n * len(BLOB), (
        "the rewrite inserted or dropped something besides the URL prefixes"
    )
    # images are already absolute, and PyPI renders the whole file on one page, so
    # in-page anchors must survive untouched
    assert built.count("raw.githubusercontent.com") == readme.count("raw.githubusercontent.com")
    for anchor in re.findall(r"\]\((#[^)]+)\)", readme):
        assert f"]({anchor})" in built, f"in-page anchor {anchor} was rewritten"


def test_absolutise_leaves_alone_what_pypi_can_already_resolve() -> None:
    keep = '[a](https://x.dev) [c](#anchor) [d](mailto:x@y.z) <a href="https://x.dev">e</a>'
    assert absolutise(keep) == keep
    assert absolutise("[e](docs/faq.md)", base="B/") == "[e](B/docs/faq.md)"
    assert absolutise('<a href="LICENSE">L</a>', base="B/") == '<a href="B/LICENSE">L</a>'


def test_the_hook_hatchling_actually_calls_produces_that_description() -> None:
    """The helpers above could be right while the hook wires them up wrongly."""
    metadata: dict[str, object] = {}
    ReadmeHook(str(REPO), {}).update(metadata)
    readme = metadata["readme"]
    assert isinstance(readme, dict)
    assert readme["content-type"] == "text/markdown"
    assert not relative_links(str(readme["text"]))


def test_pyproject_declares_the_hook_and_ships_it() -> None:
    """Half-applying this fails the build loudly, but a later edit could half-undo it."""
    cfg = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))
    assert "readme" in cfg["project"]["dynamic"]
    assert "readme" not in cfg["project"], "a static readme= would win over the hook"
    assert cfg["tool"]["hatch"]["metadata"]["hooks"]["custom"]["path"] == "hatch_build.py"
    # the sdist must carry the hook, or building a wheel from it cannot run this
    assert "hatch_build.py" in cfg["tool"]["hatch"]["build"]["targets"]["sdist"]["include"]


def test_no_image_under_docs_assets_ships_in_the_sdist() -> None:
    """The one rule keeping a non-Apache asset out of a published package.

    `docs/assets/quackd-on-off.gif` renders upstream's model and carries its CC BY-NC-SA
    terms, and `docs/reference/licenses.md` promises that neither the wheel nor the repository's
    published artefacts carry a byte of it. The README serves every image from
    raw.githubusercontent, so no artefact needed any of them anyway. Nothing checked this.
    """
    import fnmatch

    cfg = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))
    patterns = cfg["tool"]["hatch"]["build"]["targets"]["sdist"]["exclude"]
    images = [
        p
        for p in (REPO / "docs" / "assets").iterdir()
        if p.suffix.lower() in {".gif", ".png", ".svg", ".jpg", ".jpeg", ".webp"}
    ]
    assert images, "docs/assets has no images, so this test is not testing anything"
    for image in images:
        relative = image.relative_to(REPO).as_posix()
        assert any(fnmatch.fnmatch(relative, pattern) for pattern in patterns), (
            f"{relative} would ship in the sdist. The README serves images from "
            f"raw.githubusercontent and the simulator figure renders a CC BY-NC-SA model "
            f"(docs/reference/licenses.md), so add its suffix to the sdist exclude in "
            f"pyproject.toml."
        )


#: The recorders that render a simulator into `docs/assets` and check their own output against
#: the general cap: the duck's physics figure and the arm simulator's recording.
RENDER_RECORDERS = ("hero3d.py", "lerobot_sim.py")


@pytest.mark.parametrize("script", RENDER_RECORDERS)
def test_each_simulator_recorder_uses_the_cap_the_pre_commit_hook_is_configured_with(
    script: str,
) -> None:
    """`check-added-large-files` only inspects files being *added*, so regenerating a
    simulator recording in place past the cap is invisible to it. The script's own check is the
    one that fires, and `hero3d.py`'s was once set 97 KB tighter than the hook it claimed to
    mirror.

    Both keep the general cap. The one file over it is the README hero, which is a photograph
    rather than a render and is held to its own number by the test below."""
    maxkb, _ = _hook_cap()
    found = _script_constant(script, "MAX_BYTES")
    assert found == [maxkb * 1024], (
        f"docs/assets/{script} caps its output at {found}, and the pre-commit hook refuses "
        f"anything over {maxkb} KB ({maxkb * 1024} bytes). Move one of the two."
    )


def _hook_cap() -> tuple[int, str]:
    """The general cap in KB, and the one path the hook excludes from it."""
    hook = (REPO / ".pre-commit-config.yaml").read_text(encoding="utf-8")
    maxkb = int(re.search(r"--maxkb=(\d+)", hook).group(1))  # type: ignore[union-attr]
    excluded = re.search(r"exclude: \^(\S+?)\$", hook)
    return maxkb, (excluded.group(1).replace("\\", "") if excluded else "")


def _script_constant(script: str, name: str) -> list[int]:
    """Every module-level assignment of `name` in `docs/assets/<script>`, evaluated."""
    import ast

    tree = ast.parse((REPO / "docs" / "assets" / script).read_text(encoding="utf-8"))
    return [
        ast.literal_eval(node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and any(getattr(t, "id", None) == name for t in node.targets)
    ]


def test_the_one_asset_over_the_general_cap_is_the_hero_and_its_own_cap_is_written_down() -> None:
    """The README hero is a phone recording of a real arm, and a photograph does not compress
    the way a render does: the same seconds of bench cost megabytes at any width worth leading
    a page with. So it is excluded from the hook's cap and carries one of its own.

    The exception has to be exactly one file and its own number has to be somewhere a person
    can read it, or "excluded from the cap" quietly becomes "no cap". Both are checked here,
    and the general cap still applies to everything else (the test below)."""
    maxkb, excluded = _hook_cap()
    assert excluded == "docs/assets/lerobot.gif", (
        f"the large-file hook excludes {excluded!r}. The hero is the only file meant to be "
        "over the cap, and a second exception needs its own reason and its own number."
    )
    hero = REPO / excluded
    assert hero.exists(), f"{excluded} is excluded from the cap and is not there"
    assert hero.stat().st_size // 1024 > maxkb, (
        f"{excluded} is under the {maxkb} KB cap now, so it needs no exception: drop the "
        "exclude from .pre-commit-config.yaml and let the hook hold it like the rest."
    )
    found = _script_constant("lerobot_hero.py", "HERO_MAX_BYTES")
    assert len(found) == 1, f"docs/assets/lerobot_hero.py names HERO_MAX_BYTES {len(found)} times"
    # and its other cap, the one for the frames sheet, still claims to mirror the hook
    general = _script_constant("lerobot_hero.py", "MAX_BYTES")
    assert general == [maxkb * 1024], (
        f"docs/assets/lerobot_hero.py caps everything but the hero at {general}, and the hook "
        f"refuses anything over {maxkb} KB ({maxkb * 1024} bytes). Move one of the two."
    )
    assert hero.stat().st_size <= found[0], (
        f"{excluded} is {hero.stat().st_size} bytes against its own cap of {found[0]}. "
        "Lower --width, then --fps, then --colours."
    )


def test_every_recorded_asset_is_under_that_cap_today() -> None:
    maxkb, excluded = _hook_cap()
    for asset in (REPO / "docs" / "assets").iterdir():
        if asset.is_file() and asset.suffix.lower() != ".py":
            if asset.relative_to(REPO).as_posix() == excluded:
                continue  # the hero, held to its own cap by the test above
            kb = asset.stat().st_size // 1024
            assert kb <= maxkb, f"docs/assets/{asset.name} is {kb} KB; the cap is {maxkb} KB"
