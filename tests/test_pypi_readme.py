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

from hatch_build import BLOB, ReadmeHook, absolutise, pypi_readme, relative_links
from tests.conftest import REPO


def test_the_readme_itself_keeps_its_relative_links() -> None:
    """The repository's own README must NOT be absolutised: relative is right on GitHub."""
    readme = (REPO / "README.md").read_text(encoding="utf-8")
    assert relative_links(readme), "README has no relative links left to rewrite"


def test_the_built_long_description_has_no_relative_links() -> None:
    left = relative_links(pypi_readme(REPO))
    assert not left, f"these would 404 on the PyPI project page: {left}"


def test_the_built_long_description_keeps_the_layer_picture() -> None:
    """PyPI's renderer drops `<picture>` and keeps the `<img>` inside it, so the built description
    has to name the light file by an address PyPI can reach, inside the Architecture section."""
    built = pypi_readme(REPO)
    section = built[built.index("## Architecture") :]
    assert re.search(r'<img src="https://[^"]+/docs/assets/architecture-light\.svg"', section), (
        "the layer picture is not in what PyPI renders"
    )


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
    """The one rule keeping an image out of a published package.

    The README serves every image from raw.githubusercontent, so no artefact needs any of them,
    and one that shipped would carry whatever terms it was made under into the sdist with no word
    said. Nothing checked this.
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
            f"raw.githubusercontent, so add its suffix to the sdist exclude in pyproject.toml."
        )


def _hook_cap() -> tuple[int, re.Pattern[str]]:
    """The general cap in KB, and the pattern of the paths the hook leaves to caps of their own,
    which pre-commit matches with `re.search` against each path, as this is used here."""
    import yaml

    config = yaml.safe_load((REPO / ".pre-commit-config.yaml").read_text(encoding="utf-8"))
    (hook,) = [
        hook
        for repo in config["repos"]
        for hook in repo["hooks"]
        if hook["id"] == "check-added-large-files"
    ]
    (maxkb,) = [int(a.removeprefix("--maxkb=")) for a in hook["args"] if a.startswith("--maxkb=")]
    return maxkb, re.compile(hook.get("exclude", "(?!)"))


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

    In `docs/assets` the exception has to be exactly one file and its own number has to be
    somewhere a person can read it, or "excluded from the cap" quietly becomes "no cap". Both are
    checked here, and the general cap still applies to every other asset (the test below)."""
    maxkb, excluded = _hook_cap()
    exempt = sorted(
        path.relative_to(REPO).as_posix()
        for path in (REPO / "docs" / "assets").iterdir()
        if excluded.search(path.relative_to(REPO).as_posix())
    )
    assert exempt == ["docs/assets/lerobot.gif"], (
        f"the large-file hook excludes {exempt} in docs/assets. The hero is the only asset meant "
        "to be over the cap, and a second exception needs its own reason and its own number."
    )
    (excluded_hero,) = exempt
    hero = REPO / excluded_hero
    assert hero.stat().st_size // 1024 > maxkb, (
        f"{excluded_hero} is under the {maxkb} KB cap now, so it needs no exception: drop it "
        "from the exclude in .pre-commit-config.yaml and let the hook hold it like the rest."
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
        f"{excluded_hero} is {hero.stat().st_size} bytes against its own cap of {found[0]}. "
        "Lower --width, then --fps, then --colours."
    )


def test_every_recorded_asset_is_under_that_cap_today() -> None:
    maxkb, excluded = _hook_cap()
    for asset in (REPO / "docs" / "assets").iterdir():
        if asset.is_file() and asset.suffix.lower() != ".py":
            if excluded.search(asset.relative_to(REPO).as_posix()):
                continue  # the hero, held to its own cap by the test above
            kb = asset.stat().st_size // 1024
            assert kb <= maxkb, f"docs/assets/{asset.name} is {kb} KB; the cap is {maxkb} KB"


def test_the_example_gifs_the_hook_leaves_out_are_held_to_a_cap_of_their_own() -> None:
    """An example's log carries the whole video of its run on a real arm as a GIF, which weighs
    megabytes at a width worth watching, so the large-file hook leaves those out and
    `scripts/example_log.py` holds them to `GIF_LIMIT` instead: it makes each GIF to fit, and its
    `check_all`, which `tests/test_example_logs.py` runs over every log, refuses one that does not.

    So the exclude has to reach every GIF under `docs/examples` and nothing else there. Short of
    one, a new example's log is refused by a hook it was never meant to meet; past the GIFs, a
    file no cap holds goes past the hook unweighed."""
    import importlib.util

    maxkb, excluded = _hook_cap()
    spec = importlib.util.spec_from_file_location(
        "example_log", REPO / "scripts" / "example_log.py"
    )
    assert spec is not None and spec.loader is not None
    script = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(script)
    limit = script.GIF_LIMIT
    assert limit > maxkb * 1024, (
        f"GIF_LIMIT is {limit} bytes, inside the hook's {maxkb} KB, so the examples' GIFs need "
        "no exception: drop them from the exclude in .pre-commit-config.yaml."
    )
    files = sorted(path for path in (REPO / "docs" / "examples").rglob("*") if path.is_file())
    assert any(path.suffix == ".gif" for path in files), (
        "docs/examples holds no GIF, so this test is not testing anything"
    )
    for path in files:
        relative = path.relative_to(REPO).as_posix()
        if path.suffix == ".gif":
            assert excluded.search(relative), (
                f"{relative} is an example's GIF, held to GIF_LIMIT, and the large-file hook "
                f"would refuse it past {maxkb} KB: widen the exclude in .pre-commit-config.yaml"
            )
            assert path.stat().st_size <= limit, (
                f"{relative} is {path.stat().st_size} bytes, over GIF_LIMIT"
            )
        else:
            assert not excluded.search(relative), (
                f"the large-file hook leaves out {relative}, which no cap of its own holds"
            )
    # and a GIF anywhere else, which nothing caps, is weighed by the hook like any other file
    for elsewhere in ("docs/assets/next.gif", "docs/guides/run.gif", "web/run.gif", "run.gif"):
        assert not excluded.search(elsewhere), (
            f"the large-file hook would leave out {elsewhere}, which no cap of its own holds"
        )
