"""`scripts/upstream_drift.py`: the nightly that says when an upstream moved under a ref.

Every case here runs the script's own `run()` over synthetic ref modules and a dict in place of
the network, so no line of any upstream's source is in this file, which CONTRIBUTING forbids,
and nothing here reaches the network. The script's walk over the real modules is held to the
suite's own list, and the workflow that runs it nightly is read back as YAML.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import types
from collections.abc import Mapping
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
import yaml

from quackd.upstream import UpstreamRef
from tests.test_upstream_api import UPSTREAMS as SUITE_ROWS

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "upstream_drift.py"
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "upstream-drift.yml"

PIN = "a" * 40
HEAD = "b" * 40
WIDGET = "acme/widget"
GITHUB = f"https://github.com/{WIDGET}"

PINNED = b"import os\ndef read():\n\n    return 1\nclass Bus:\n"
"""The file at the pin: five lines, the third blank, so a ref may cite 2 and 5 but not 3."""


def _script() -> ModuleType:
    """The script loaded by path, as `tests/test_docs.py` loads `scripts/models_docs.py`, and
    registered under its name first: its dataclasses carry string annotations, which the
    standard library resolves through `sys.modules` and cannot for a module that is not there."""
    spec = importlib.util.spec_from_file_location("upstream_drift", SCRIPT)
    assert spec and spec.loader, f"{SCRIPT} could not be loaded as a module"
    module = importlib.util.module_from_spec(spec)
    sys.modules["upstream_drift"] = module
    spec.loader.exec_module(module)
    return module


def _ref(name: str, path: str, line: int | None = None, *, repo: str = GITHUB) -> UpstreamRef:
    source = f"{repo}/blob/{PIN}/{path}" + (f"#L{line}" if line else "")
    return UpstreamRef(name, "VERIFIED", source, "a note")


def _module(refs: list[UpstreamRef], name: str = "fake_api", **attrs: Any) -> ModuleType:
    """A ref module shaped as every `upstream_api.py` is: `REPO`, `PIN`, `READ_ON`, `all_refs`."""
    module = types.ModuleType(name)
    module.REPO = GITHUB  # type: ignore[attr-defined]
    module.PIN = PIN  # type: ignore[attr-defined]
    module.READ_ON = "2026-11-05"  # type: ignore[attr-defined]
    for key, value in attrs.items():
        setattr(module, key, value)
    module.all_refs = lambda: list(refs)  # type: ignore[attr-defined]
    module.refs_by_status = lambda status: [r for r in refs if r.status == status]  # type: ignore[attr-defined]
    return module


class Store:
    """The network as a dict: a URL to its body, None for a 404, an exception to raise. A URL
    the test did not foresee fails the test, which is how a fetch nobody expected shows up."""

    def __init__(self, urls: Mapping[str, Any]) -> None:
        self.urls = dict(urls)
        self.calls: list[str] = []

    def __call__(self, url: str) -> bytes | None:
        self.calls.append(url)
        assert url in self.urls, f"the script fetched {url}, which the test did not foresee"
        value = self.urls[url]
        if isinstance(value, Exception):
            raise value
        assert value is None or isinstance(value, bytes)
        return value


def _github(
    script: ModuleType,
    files: Mapping[str, tuple[bytes | None, bytes | None]],
    *,
    repo: str = WIDGET,
    default: str = "main",
    head: str = HEAD,
    branch: str | None = None,
    tags: list[str] | None = None,
) -> dict[str, Any]:
    """The URLs one repository answers: its metadata, its head, and each file at the pin and
    at the head. `files` maps a path to (the body at the pin, the body at the head)."""
    urls: dict[str, Any] = {
        f"{script.API}/repos/{repo}": json.dumps({"default_branch": default}).encode(),
        f"{script.API}/repos/{repo}/commits/{branch or default}": json.dumps(
            {"sha": head}
        ).encode(),
    }
    if tags is not None:
        urls[f"{script.API}/repos/{repo}/tags?per_page=100"] = json.dumps(
            [{"name": tag, "commit": {"sha": "c" * 40}} for tag in tags]
        ).encode()
        urls[f"{script.API}/repos/{repo}/commits/{tags[0]}"] = json.dumps({"sha": head}).encode()
    for path, (pinned, current) in files.items():
        urls[f"{script.RAW}/{repo}/{PIN}/{path}"] = pinned
        urls[f"{script.RAW}/{repo}/{head}/{path}"] = current
    return urls


def _one(script: ModuleType, module: ModuleType, store: Store) -> Any:
    reports = script.run([("widget", module)], store)
    assert len(reports) == 1
    return reports[0]


# ── one ref at a time ───────────────────────────────────────────────────────────────────


def test_a_line_that_still_reads_the_same_is_same_and_one_that_moved_is_not_drift() -> None:
    script = _script()
    module = _module([_ref("READ", "src/thing.py", 2), _ref("BUS", "src/thing.py", 5)])
    moved = b"# a comment pushed everything down\n" + PINNED
    store = Store(_github(script, {"src/thing.py": (PINNED, moved)}))
    report = _one(script, module, store)
    assert report.counts == {"not a file": 0, "moved": 2}
    assert [(r.ref, r.status) for r in report.rows] == [("READ", "moved"), ("BUS", "moved")]
    assert report.rows[0].now == f"line 3 at {HEAD[:7]}"
    assert report.drift is False
    assert script.exit_code([report]) == 0

    same = Store(_github(script, {"src/thing.py": (PINNED, PINNED)}))
    report = _one(script, module, same)
    assert report.counts == {"not a file": 0, "same": 2}
    assert report.rows == []


def test_a_line_that_no_longer_reads_the_same_is_drift() -> None:
    script = _script()
    module = _module([_ref("READ", "src/thing.py", 2)])
    changed = PINNED.replace(b"def read():", b"def read(self):")
    report = _one(script, module, Store(_github(script, {"src/thing.py": (PINNED, changed)})))
    assert report.counts == {"not a file": 0, "changed": 1}
    assert report.rows[0].status == "changed"
    assert "no line reads 'def read():'" in report.rows[0].now
    assert HEAD[:7] in report.rows[0].now
    assert report.drift is True
    assert script.exit_code([report]) == 1


def test_a_file_gone_at_the_baseline_is_drift_and_one_gone_at_the_pin_is_unreachable() -> None:
    script = _script()
    module = _module([_ref("READ", "src/thing.py", 2)])
    report = _one(script, module, Store(_github(script, {"src/thing.py": (PINNED, None)})))
    assert report.rows[0].status == "gone" and report.drift is True

    report = _one(script, module, Store(_github(script, {"src/thing.py": (None, PINNED)})))
    assert report.rows[0].status == "pin unreachable" and report.drift is False


def test_a_ref_anchored_on_a_blank_line_or_past_the_end_is_a_bad_anchor() -> None:
    script = _script()
    module = _module([_ref("BLANK", "src/thing.py", 3), _ref("PAST", "src/thing.py", 99)])
    report = _one(script, module, Store(_github(script, {"src/thing.py": (PINNED, PINNED)})))
    assert report.counts == {"not a file": 0, "bad anchor": 2}
    assert report.rows[0].now == "the line reads ''"
    assert report.rows[1].now == "past the end of the file at the pin"
    assert report.drift is False


def test_a_ref_without_a_line_is_compared_by_hash_and_a_changed_page_is_not_drift() -> None:
    script = _script()
    raw = f"https://raw.githubusercontent.com/{WIDGET}/{PIN}/assets/model.xml"
    module = _module(
        [
            _ref("CODE", "src/code.py"),
            _ref("PAGE", "README.md"),
            _ref("SAME", "src/same.py"),
            UpstreamRef("RAW", "VERIFIED", raw, "a raw url, no line"),
        ]
    )
    files = {
        "src/code.py": (b"x = 1\n", b"x = 2\n"),
        "README.md": (b"# Widget\n", b"# Widget, renamed\n"),
        "src/same.py": (b"y = 1\n", b"y = 1\n"),
        "assets/model.xml": (b"<a/>", b"<b/>"),
    }
    report = _one(script, module, Store(_github(script, files)))
    statuses = {row.ref: row.status for row in report.rows}
    assert statuses == {"CODE": "file changed", "PAGE": "page changed", "RAW": "file changed"}
    assert report.counts["same"] == 1
    assert report.drift is True

    only_page = _module([_ref("PAGE", "README.md")])
    report = _one(script, only_page, Store(_github(script, {"README.md": files["README.md"]})))
    assert report.drift is False


def test_a_binary_file_is_compared_by_its_hash_alone() -> None:
    script = _script()
    module = _module([_ref("WEIGHTS", "policy.onnx", 4)])
    blob = b"\x00\x01\x02" * 100
    report = _one(script, module, Store(_github(script, {"policy.onnx": (blob, blob)})))
    assert report.counts == {"not a file": 0, "same": 1}
    report = _one(script, module, Store(_github(script, {"policy.onnx": (blob, blob + b"\x03")})))
    assert report.rows[0].status == "file changed"


def test_a_source_that_is_not_a_github_file_is_counted_and_never_fetched() -> None:
    script = _script()
    module = _module(
        [
            UpstreamRef("SHIM", "UNVERIFIED", "https://ce-net.com/huebot", "the maker's page"),
            UpstreamRef("DOC", "VERIFIED", "https://huggingface.co/docs/x/y", "a page"),
            _ref("READ", "src/thing.py", 2),
        ]
    )
    store = Store(_github(script, {"src/thing.py": (PINNED, PINNED)}))
    report = _one(script, module, store)
    assert report.counts == {"not a file": 2, "same": 1}
    assert not any("ce-net" in url or "huggingface" in url for url in store.calls)


def test_every_file_is_fetched_once_however_many_refs_cite_it() -> None:
    script = _script()
    module = _module([_ref(f"R{n}", "src/thing.py", n) for n in (1, 2, 4, 5)])
    store = Store(_github(script, {"src/thing.py": (PINNED, PINNED)}))
    report = _one(script, module, store)
    assert report.counts["same"] == 4
    raw = [url for url in store.calls if url.startswith(script.RAW)]
    assert len(raw) == 2, raw
    api = [url for url in store.calls if url.startswith(script.API)]
    assert len(api) == 2, api


def test_a_second_module_citing_the_same_lines_reports_them_as_shared() -> None:
    """Huebot's refs are the Microduck's at the Microduck's pin: judged once, under the
    module that owns them, so one drift opens one issue and not two."""
    script = _script()
    refs = [_ref("READ", "src/thing.py", 2)]
    owner = _module(refs, name="duck_api")
    copy = _module(refs, name="huebot_api")
    changed = PINNED.replace(b"def read():", b"def read(self):")
    store = Store(_github(script, {"src/thing.py": (PINNED, changed)}))
    first, second = script.run([("microduck", owner), ("huebot", copy)], store)
    assert first.drift is True and first.counts["changed"] == 1
    assert second.drift is False and second.counts == {"not a file": 0, "shared": 1}
    assert second.rows == []


# ── which baseline ──────────────────────────────────────────────────────────────────────


def test_a_branch_pinned_module_is_compared_against_its_branch_not_the_default() -> None:
    script = _script()
    module = _module([_ref("READ", "src/thing.py", 2)], BRANCH="v2")
    urls = _github(script, {"src/thing.py": (PINNED, PINNED)}, branch="v2")
    del urls[f"{script.API}/repos/{WIDGET}"]  # the default branch is never asked for
    report = _one(script, module, Store(urls))
    assert report.baselines[WIDGET].kind == "branch"
    assert report.baselines[WIDGET].name == "v2"
    assert report.baselines[WIDGET].sha == HEAD


def test_a_version_pinned_module_is_compared_against_the_newest_release_tag() -> None:
    script = _script()
    module = _module([_ref("READ", "src/thing.py", 2)], VERSION="0.6.1")
    tags = ["v0.6.1", "v0.6.2rc1", "v0.6.0", "0.5.9", "nightly"]
    urls = _github(script, {"src/thing.py": (PINNED, PINNED)}, tags=tags)
    del urls[f"{script.API}/repos/{WIDGET}"]
    report = _one(script, module, Store(urls))
    assert report.baselines[WIDGET].kind == "tag"
    assert report.baselines[WIDGET].name == "v0.6.1"
    assert script.newest_release([{"name": t} for t in tags]) == "v0.6.1"
    assert script.newest_release([{"name": "v1.10.0"}, {"name": "v1.9.9"}]) == "v1.10.0"
    assert script.newest_release([{"name": "nightly"}]) is None


def test_a_second_repository_takes_its_default_branch_unless_branches_names_one() -> None:
    script = _script()
    other = "acme/widget-hub"
    module = _module(
        [
            _ref("READ", "src/thing.py", 2),
            _ref("HUB", "docs/hub.py", 1, repo=f"https://github.com/{other}"),
        ],
        BRANCH="v2",
        BRANCHES={other: "v2"},
    )
    urls = _github(script, {"src/thing.py": (PINNED, PINNED)}, branch="v2")
    urls.update(
        _github(script, {"docs/hub.py": (b"hub = 1\n", b"hub = 1\n")}, repo=other, branch="v2")
    )
    for repo in (WIDGET, other):
        del urls[f"{script.API}/repos/{repo}"]
    report = _one(script, module, Store(urls))
    assert {repo: b.name for repo, b in report.baselines.items()} == {WIDGET: "v2", other: "v2"}

    plain = _module([_ref("HUB", "docs/hub.py", 1, repo=f"https://github.com/{other}")])
    urls = _github(
        script, {"docs/hub.py": (b"hub = 1\n", b"hub = 1\n")}, repo=other, default="trunk"
    )
    report = _one(script, plain, Store(urls))
    assert report.baselines[other].kind == "default" and report.baselines[other].name == "trunk"


# ── versions ────────────────────────────────────────────────────────────────────────────


def test_a_newer_release_on_pypi_or_at_the_baseline_is_drift() -> None:
    script = _script()
    module = _module(
        [_ref("READ", "src/thing.py", 2)],
        PACKAGE=UpstreamRef("widget", "VERIFIED", f"{GITHUB}/blob/{PIN}/pyproject.toml#L3", ""),
        PYPI_VERSION_READ="1.0.0",
        VERSION_AT_PIN=UpstreamRef(
            "1.0.1", "VERIFIED", f"{GITHUB}/blob/{PIN}/pyproject.toml#L4", ""
        ),
    )
    urls = _github(script, {"src/thing.py": (PINNED, PINNED)})
    urls[f"{script.PYPI}/widget/json"] = json.dumps({"info": {"version": "1.1.0"}}).encode()
    urls[f"{script.RAW}/{WIDGET}/{HEAD}/pyproject.toml"] = (
        b'[project]\nname = "widget"\nversion = "1.2.0"\n'
    )
    report = _one(script, module, Store(urls))
    assert report.versions == [
        "PyPI widget is 1.1.0; quackd read 1.0.0",
        "pyproject.toml says 1.2.0 at main; 1.0.1 at the pin",
    ]
    assert report.drift is True

    urls[f"{script.PYPI}/widget/json"] = json.dumps({"info": {"version": "1.0.0"}}).encode()
    urls[f"{script.RAW}/{WIDGET}/{HEAD}/pyproject.toml"] = b'version = "1.0.1"\n'
    report = _one(script, module, Store(urls))
    assert report.versions == [
        "PyPI widget is still 1.0.0",
        "pyproject.toml still says 1.0.1 at main",
    ]
    assert report.drift is False


def test_the_hub_version_and_an_api_document_are_checked_the_same_way() -> None:
    script = _script()
    api_doc = "https://api.example.test/openapi.json"
    module = _module(
        [_ref("READ", "src/thing.py", 2)],
        HUB_VERSION="1.29.0",
        API_DOC=api_doc,
        API_VERSION="2.0.0",
    )
    # a module that names the hub version it read is compared with the newest release tag
    urls = _github(script, {"src/thing.py": (PINNED, PINNED)}, tags=["v1.29.0", "v1.28.0"])
    del urls[f"{script.API}/repos/{WIDGET}"]
    urls[f"{script.PYPI}/huggingface_hub/json"] = json.dumps(
        {"info": {"version": "1.30.0"}}
    ).encode()
    urls[api_doc] = json.dumps({"info": {"version": "2.1.0"}}).encode()
    report = _one(script, module, Store(urls))
    assert report.baselines[WIDGET].kind == "tag"
    assert report.versions == [
        "PyPI huggingface_hub is 1.30.0; quackd read 1.29.0",
        f"{api_doc} is version 2.1.0; quackd read 2.0.0",
    ]
    assert report.drift is True


# ── outages, reports, exit codes ────────────────────────────────────────────────────────


def test_an_outage_is_unchecked_and_exit_2_unless_something_else_drifted() -> None:
    script = _script()
    module = _module([_ref("READ", "src/thing.py", 2)])
    urls = _github(script, {"src/thing.py": (PINNED, PINNED)})
    urls[f"{script.API}/repos/{WIDGET}"] = script.Unreachable("HTTP 503")
    report = _one(script, module, Store(urls))
    assert report.counts == {"not a file": 0, "unchecked": 1}
    assert report.unchecked == ["READ: HTTP 503"]
    assert report.drift is False
    assert script.exit_code([report]) == 2

    drifted = _module([_ref("READ", "src/thing.py", 2)], name="other_api")
    changed = PINNED.replace(b"def read():", b"def read(self):")
    other = _one(script, drifted, Store(_github(script, {"src/thing.py": (PINNED, changed)})))
    assert script.exit_code([report, other]) == 1


def test_the_markdown_report_and_the_json_carry_the_same_rows() -> None:
    script = _script()
    module = _module([_ref("READ", "src/thing.py", 2), _ref("BUS", "src/thing.py", 5)])
    changed = PINNED.replace(b"def read():", b"def read(self):")
    report = _one(script, module, Store(_github(script, {"src/thing.py": (PINNED, changed)})))
    text = script.markdown([report])
    assert text.startswith("# Upstream drift\n\nDrifted: widget.")
    assert "## widget (`fake_api`)" in text
    assert (
        f"pin {PIN[:7]} read 2026-11-05 vs {WIDGET} default main at {HEAD[:7]}: 1 same, 1 changed"
        in text
    )
    assert "| `READ` | `src/thing.py:2` | changed |" in text
    assert "`BUS`" not in text, "a ref that reads the same is not a row"
    payload = script.as_json([report])
    assert payload["drifted"] == ["widget"]
    assert payload["upstreams"][0]["rows"] == [vars(row) for row in report.rows]
    assert payload["upstreams"][0]["baselines"][WIDGET] == {
        "kind": "default",
        "name": "main",
        "sha": HEAD,
    }
    assert payload["upstreams"][0]["summary"] == script.summary_line(report)


def test_offline_fixtures_map_each_kind_of_url_to_a_file(tmp_path: Path) -> None:
    script = _script()
    root = tmp_path / "fixtures"
    expected = {
        f"{script.RAW}/{WIDGET}/{PIN}/src/thing.py": root
        / "raw"
        / "acme"
        / "widget"
        / PIN
        / "src"
        / "thing.py",
        f"{script.API}/repos/{WIDGET}": root / "api" / "acme" / "widget" / "repo.json",
        f"{script.API}/repos/{WIDGET}/commits/main": root
        / "api"
        / "acme"
        / "widget"
        / "commits"
        / "main.json",
        f"{script.API}/repos/{WIDGET}/tags?per_page=100": root
        / "api"
        / "acme"
        / "widget"
        / "tags.json",
        f"{script.PYPI}/widget/json": root / "pypi" / "widget.json",
    }
    for url, path in expected.items():
        assert script._offline_path(root, url) == path, url
    other = script._offline_path(root, "https://api.example.test/openapi.json")
    assert other.parent == root / "url" and len(other.name) == 16

    path = expected[f"{script.RAW}/{WIDGET}/{PIN}/src/thing.py"]
    path.parent.mkdir(parents=True)
    path.write_bytes(PINNED)
    fetch = script.offline_fetch(root)
    assert fetch(f"{script.RAW}/{WIDGET}/{PIN}/src/thing.py") == PINNED
    assert fetch(f"{script.RAW}/{WIDGET}/{HEAD}/src/thing.py") is None


# ── issues ──────────────────────────────────────────────────────────────────────────────


class FakeGh:
    """`gh` as a list of calls and canned answers to `issue list`."""

    def __init__(self, listed: list[dict[str, Any]]) -> None:
        self.listed = listed
        self.calls: list[list[str]] = []

    def __call__(self, args: Any) -> str:
        self.calls.append(list(args))
        if list(args[:2]) == ["issue", "list"]:
            return json.dumps(self.listed)
        if list(args[:2]) == ["issue", "create"]:
            return "https://github.com/rokbenko/quackd/issues/99\n"
        return ""

    def verbs(self) -> list[str]:
        return [" ".join(call[:2]) for call in self.calls]


def _drifted_report(script: ModuleType) -> Any:
    module = _module([_ref("READ", "src/thing.py", 2), _ref("BUS", "src/thing.py", 5)])
    changed = PINNED.replace(b"def read():", b"def read(self):")
    return _one(script, module, Store(_github(script, {"src/thing.py": (PINNED, changed)})))


def test_a_drifted_upstream_opens_one_issue_and_a_clean_night_closes_it() -> None:
    script = _script()
    report = _drifted_report(script)
    gh = FakeGh([])
    done = script.sync_issues([report], "rokbenko/quackd", gh)
    assert gh.verbs() == ["label create", "issue list", "issue create"]
    create = gh.calls[2]
    assert create[create.index("--title") + 1] == "upstream drift: widget"
    assert create[create.index("--label") + 1] == script.LABEL
    body = create[create.index("--body") + 1]
    assert "| `READ` | `src/thing.py:2` | changed |" in body
    assert "upstream-drift.yml" in body and "fake_api" in body
    assert done == ["widget: issue opened (https://github.com/rokbenko/quackd/issues/99)"]

    clean = _one(
        script,
        _module([_ref("READ", "src/thing.py", 2)]),
        Store(_github(script, {"src/thing.py": (PINNED, PINNED)})),
    )
    gh = FakeGh([{"number": 99, "title": "upstream drift: widget", "body": body}])
    done = script.sync_issues([clean], "rokbenko/quackd", gh)
    assert gh.verbs() == ["label create", "issue list", "issue close"]
    assert gh.calls[2][2] == "99"
    assert done == ["widget: issue #99 closed"]


def test_an_open_issue_is_edited_in_place_and_commented_only_when_the_refs_change() -> None:
    script = _script()
    report = _drifted_report(script)
    first_body = script.sync_issues.__globals__["markdown"]([report]).split("\n", 2)[2]
    same_set = FakeGh([{"number": 7, "title": "upstream drift: widget", "body": first_body}])
    done = script.sync_issues([report], "rokbenko/quackd", same_set)
    assert same_set.verbs() == ["label create", "issue list", "issue edit"]
    assert done == ["widget: issue #7 updated"]

    older = first_body.replace("| `READ` |", "| `OTHER` |")
    changed_set = FakeGh([{"number": 7, "title": "upstream drift: widget", "body": older}])
    script.sync_issues([report], "rokbenko/quackd", changed_set)
    assert changed_set.verbs() == ["label create", "issue list", "issue edit", "issue comment"]

    unrelated = FakeGh([{"number": 8, "title": "upstream drift: other", "body": ""}])
    script.sync_issues([report], "rokbenko/quackd", unrelated)
    assert unrelated.verbs() == ["label create", "issue list", "issue create"]


# ── the real modules, and the workflow ──────────────────────────────────────────────────


def test_the_walk_covers_every_module_the_suite_knows() -> None:
    """The script reads what `quackd doctor` walks, so a module doctor does not know is one the
    nightly never reads: the arm's endpoint module was that until this test."""
    script = _script()
    walked = {module.__name__ for _name, module in script.upstream_rows()}
    known = {module.__name__ for module, _allowed, _prefixes in SUITE_ROWS}
    assert walked == known, {"missing": known - walked, "extra": walked - known}


def test_every_real_ref_is_a_file_or_a_page_the_script_knows_how_to_skip() -> None:
    """Every GitHub permalink to a file parses, and every ref the script will not fetch cites
    something that is not a file: a repository at its pin (`blob/<sha>/` with no path), a
    directory (`tree/`), an issue, a maker's page, a document on another host."""
    import re

    script = _script()
    a_file = re.compile(r"^https://github\.com/[^/]+/[^/]+/blob/[0-9a-f]{40}/[^#]+")
    for module, _allowed, _prefixes in SUITE_ROWS:
        files, elsewhere = script.cited_of(module)
        assert files, module.__name__
        for cited in files:
            assert len(cited.sha) == 40 and cited.path and not cited.path.startswith("/"), cited
        for name in elsewhere:
            ref = next(r for r in module.all_refs() if r.name == name)
            assert not a_file.match(ref.source), f"{module.__name__}.{name}: {ref.source}"


@pytest.mark.parametrize(
    ("module_name", "attribute", "expected"),
    [
        ("quackd_open_duck.upstream_api", "BRANCH", "v2"),
        ("quackd_open_duck.upstream_api", "BRANCHES", {"apirrone/Open_Duck_Mini": "v2"}),
        ("quackd_microduck.sim3d.upstream_api", "BRANCH", "develop"),
        ("quackd_microduck.mapd_upstream_api", "BRANCH", "maploc-v2"),
    ],
)
def test_a_pin_on_a_branch_that_is_not_the_default_says_which(
    module_name: str, attribute: str, expected: Any
) -> None:
    """Compared against the default branch, every file of a branch-pinned module reads as gone
    or changed, which is drift by accident: the module says which branch its pin tracks."""
    import importlib

    module = importlib.import_module(module_name)
    assert getattr(module, attribute) == expected
    script = _script()
    repo = script._repo_of(module.REPO)
    if attribute == "BRANCH":
        assert script.declared_baseline(module, repo).name == expected


def test_the_nightly_runs_the_script_with_what_it_needs_and_nothing_more() -> None:
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    triggers = workflow.get("on", workflow.get(True))  # YAML 1.1 reads a bare `on:` key as true
    assert triggers["schedule"] == [{"cron": "23 5 * * *"}]
    assert "workflow_dispatch" in triggers
    assert workflow["permissions"] == {"contents": "read", "issues": "write"}
    (job,) = workflow["jobs"].values()
    assert job["timeout-minutes"] == 15
    assert "continue-on-error" not in job
    steps = job["steps"]
    assert all("continue-on-error" not in step for step in steps)
    run = next(step["run"] for step in steps if "upstream_drift.py" in step.get("run", ""))
    assert '--report "$GITHUB_STEP_SUMMARY"' in run and "--json drift.json" in run
    assert "github.repository == 'rokbenko/quackd'" in run, "a fork's schedule must open no issue"
    assert "--issues rokbenko/quackd" in run
    env = next(step.get("env", {}) for step in steps if "upstream_drift.py" in step.get("run", ""))
    assert env == {"GITHUB_TOKEN": "${{ github.token }}"}


def test_the_script_is_type_checked_and_its_offline_mode_takes_no_network() -> None:
    import tomllib

    config = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert "scripts/upstream_drift.py" in config["tool"]["mypy"]["files"]
    source = SCRIPT.read_text(encoding="utf-8")
    assert "def offline_fetch(" in source
    imports = [line for line in source.splitlines() if "urllib" in line and "import" in line]
    assert len(imports) == 2, "urllib is imported at the top and used only by web_fetch"


def test_a_line_found_in_several_places_is_changed_not_moved() -> None:
    """The same words in two places are no evidence the cited line survived: a ref anchored on
    `return 1` could be any method's, so it is reported as changed with the places named, and
    the fix is a ref on a line of its own."""
    script = _script()
    module = _module([_ref("RET", "src/thing.py", 4)])
    twice = b"# c\n" + PINNED + b"    def go(self):\n        return 1\n"
    report = _one(script, module, Store(_github(script, {"src/thing.py": (PINNED, twice)})))
    assert report.rows[0].status == "changed"
    assert report.rows[0].now == f"reads the same on lines 5, 8 at {HEAD[:7]}"
    assert report.drift is True

    once = b"# a comment\n" + PINNED
    report = _one(script, module, Store(_github(script, {"src/thing.py": (PINNED, once)})))
    assert report.rows[0].status == "moved"


def test_a_line_less_ref_to_a_directory_is_reported_as_no_file_at_the_pin() -> None:
    """`src("src/package")` cites a directory the raw host does not serve: reported once, never
    drift, never an outage, and told apart from a lined ref whose file is missing."""
    script = _script()
    module = _module([_ref("DIR", "src/package"), _ref("FILE", "src/thing.py", 2)])
    urls = _github(script, {"src/package": (None, None), "src/thing.py": (None, PINNED)})
    report = _one(script, module, Store(urls))
    statuses = {row.ref: row.status for row in report.rows}
    assert statuses == {"DIR": "no file at the pin", "FILE": "pin unreachable"}
    assert report.drift is False and report.unchecked == []
    assert script.exit_code([report]) == 0
