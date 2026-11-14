"""`scripts/upstream_drift.py`: the nightly that says when an upstream moved under a ref.

Every case here runs the script's own `run()` over synthetic ref modules and a dict in place of
the network, so no line of any upstream's source is in this file, which CONTRIBUTING forbids,
and nothing here reaches the network. The script's walk over the real modules is held to the
suite's own list, and the workflow that runs it nightly is read back as YAML.
"""

from __future__ import annotations

import http.client
import importlib
import importlib.util
import io
import json
import re
import sys
import types
import urllib.error
from collections.abc import Mapping
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
import yaml

from quackd.upstream import Status, UpstreamRef
from tests.test_upstream_api import UPSTREAMS as SUITE_ROWS

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "upstream_drift.py"
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "upstream-drift.yml"
TODDLERBOT_CONTRACT = REPO_ROOT / ".github" / "workflows" / "toddlerbot-contract.yml"

PIN = "a" * 40
HEAD = "b" * 40
WIDGET = "acme/widget"
GITHUB = f"https://github.com/{WIDGET}"

PINNED = b"import os\ndef read():\n\n    return 1\nclass Bus:\n"
"""The file at the pin: five lines, the third blank, so a ref may cite 2 and 5 but not 3."""
CHANGED = PINNED.replace(b"def read():", b"def read(self):")


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
    source = f"{repo}/blob/{PIN}/{path}" + (f"#L{line}" if line is not None else "")
    return UpstreamRef(name, "VERIFIED", source, "a note")


def _module(refs: list[UpstreamRef], name: str = "fake_api", **attrs: Any) -> ModuleType:
    """A ref module shaped as every `upstream_api.py` is: `REPO`, `PIN`, `READ_ON`, `all_refs`."""
    module: Any = types.ModuleType(name)
    module.REPO = GITHUB
    module.PIN = PIN
    module.READ_ON = "2026-11-05"
    for key, value in attrs.items():
        setattr(module, key, value)
    module.all_refs = lambda: list(refs)
    module.refs_by_status = lambda status: [r for r in refs if r.status == status]
    return module  # type: ignore[no-any-return]


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
    newest: str | None = None,
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
        urls[f"{script.API}/repos/{repo}/tags?per_page=100"] = _tag_page(tags)
        urls[f"{script.API}/repos/{repo}/commits/{newest or tags[0]}"] = json.dumps(
            {"sha": head}
        ).encode()
    for path, (pinned, current) in files.items():
        urls[f"{script.RAW}/{repo}/{PIN}/{path}"] = pinned
        urls[f"{script.RAW}/{repo}/{head}/{path}"] = current
    return urls


def _tag_page(tags: list[str]) -> bytes:
    return json.dumps([{"name": tag, "commit": {"sha": "c" * 40}} for tag in tags]).encode()


def _contents(script: ModuleType, path: str, answer: Any, *, repo: str = WIDGET) -> dict[str, Any]:
    """What the API says is at `path` at the pin: a list for a directory, a dict for a file,
    None for nothing."""
    body = None if answer is None else json.dumps(answer).encode()
    return {f"{script.API}/repos/{repo}/contents/{path}?ref={PIN}": body}


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
    assert report.drift is False and report.broken is False
    assert script.exit_code([report]) == 0

    same = Store(_github(script, {"src/thing.py": (PINNED, PINNED)}))
    report = _one(script, module, same)
    assert report.counts == {"not a file": 0, "same": 2}
    assert report.rows == [] and report.weak == []


def test_a_line_that_no_longer_reads_the_same_is_drift() -> None:
    script = _script()
    module = _module([_ref("READ", "src/thing.py", 2)])
    report = _one(script, module, Store(_github(script, {"src/thing.py": (PINNED, CHANGED)})))
    assert report.counts == {"not a file": 0, "changed": 1}
    assert report.rows[0].status == "changed"
    assert "no line reads `def read():`" in report.rows[0].now
    assert HEAD[:7] in report.rows[0].now
    assert report.drift is True and report.broken is False
    assert script.exit_code([report]) == 1


def test_a_file_gone_at_the_baseline_is_drift() -> None:
    script = _script()
    module = _module([_ref("READ", "src/thing.py", 2)])
    report = _one(script, module, Store(_github(script, {"src/thing.py": (PINNED, None)})))
    assert report.rows[0].status == "gone" and report.drift is True


def test_nothing_at_the_pin_is_a_broken_ref_only_when_the_api_agrees() -> None:
    """The raw host answers a request it does not like with a 404, so its 404 alone is not an
    absence. The API is asked: nothing there either is a ref of ours that cannot be followed,
    which is work for somebody here; a file there is the raw host refusing, which is an outage."""
    script = _script()
    module = _module([_ref("READ", "src/thing.py", 2)])
    files = _github(script, {"src/thing.py": (None, PINNED)})

    absent = Store({**files, **_contents(script, "src/thing.py", None)})
    report = _one(script, module, absent)
    assert [(r.status, r.now) for r in report.rows] == [
        ("nothing at the pin", "neither the raw host nor the API has it")
    ]
    assert report.broken is True and report.drift is False and report.blocked == {}
    assert script.exit_code([report]) == 1

    refused = Store({**files, **_contents(script, "src/thing.py", {"type": "file"})})
    report = _one(script, module, refused)
    assert report.rows == [] and report.broken is False
    assert report.unchecked == [
        "raw.githubusercontent.com: said 404 for a file the API has at the pin (READ)"
    ]
    assert script.exit_code([report]) == 2

    # a lined ref whose path is a directory cannot be followed either
    directory = Store({**files, **_contents(script, "src/thing.py", [{"name": "a.py"}])})
    report = _one(script, module, directory)
    assert [(r.status, r.now) for r in report.rows] == [
        ("bad anchor", "the path is a directory at the pin")
    ]
    assert report.broken is True


def test_a_ref_anchored_on_a_blank_line_or_past_the_end_is_a_bad_anchor() -> None:
    script = _script()
    module = _module(
        [
            _ref("BLANK", "src/thing.py", 3),
            _ref("PAST", "src/thing.py", 99),
            _ref("ZERO", "src/thing.py", 0),
        ]
    )
    report = _one(script, module, Store(_github(script, {"src/thing.py": (PINNED, PINNED)})))
    assert report.counts == {"not a file": 0, "bad anchor": 3}
    assert report.rows[0].now == "the line is blank"
    assert report.rows[1].now == "no such line in the file at the pin"
    assert report.rows[2].now == "no such line in the file at the pin"
    assert report.drift is False and report.broken is True
    assert script.exit_code([report]) == 1, "a ref nobody can follow is work for somebody here"


def test_a_ref_without_a_line_is_compared_by_hash_and_a_changed_page_is_not_drift() -> None:
    script = _script()
    raw = f"https://raw.githubusercontent.com/{WIDGET}/{PIN}/assets/arm.xml"
    module = _module(
        [
            _ref("CODE", "src/code.py"),
            _ref("PAGE", "README.md"),
            _ref("SAME", "src/same.py"),
            _ref("LICENCE", "LICENSE"),
            UpstreamRef("RAW", "VERIFIED", raw, "a raw url, no line"),
        ]
    )
    files = {
        "src/code.py": (b"x = 1\n", b"x = 2\n"),
        "README.md": (b"# Widget\n", b"# Widget, renamed\n"),
        "src/same.py": (b"y = 1\n", b"y = 1\n"),
        "LICENSE": (b"Apache 2.0\n", b"All rights reserved\n"),
        "assets/arm.xml": (b"<a/>", b"<b/>"),
    }
    report = _one(script, module, Store(_github(script, files)))
    statuses = {row.ref: row.status for row in report.rows}
    assert statuses == {
        "CODE": "file changed",
        "PAGE": "page changed",
        # a file with no extension is a licence, a Dockerfile, a Makefile: never a page
        "LICENCE": "file changed",
        "RAW": "file changed",
    }
    assert report.counts["same"] == 1
    assert report.drift is True

    only_page = _module([_ref("PAGE", "README.md")])
    report = _one(script, only_page, Store(_github(script, {"README.md": files["README.md"]})))
    assert report.drift is False and script.exit_code([report]) == 0


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


def test_a_permalink_with_a_query_is_still_the_file_it_names() -> None:
    """`?plain=1` is what GitHub adds to a link to a Markdown file's source lines."""
    script = _script()
    source = f"{GITHUB}/blob/{PIN}/docs/page.md?plain=1#L2"
    module = _module([UpstreamRef("PLAIN", "VERIFIED", source, "a note")])
    (cited,), elsewhere = script.cited_of(module)
    assert (cited.path, cited.line) == ("docs/page.md", 2) and elsewhere == []


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
    store = Store(_github(script, {"src/thing.py": (PINNED, CHANGED)}))
    first, second = script.run([("microduck", owner), ("huebot", copy)], store)
    assert first.drift is True and first.counts["changed"] == 1
    assert second.drift is False and second.counts == {"not a file": 0, "shared": 1}
    assert second.rows == []


# ── weak anchors ────────────────────────────────────────────────────────────────────────

TWINS = b"def a():\n    return self.bus\ndef b():\n    return self.bus\n"
"""A file whose second and fourth lines read alike: a ref on either names neither."""


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


def test_a_line_that_reads_like_another_at_the_pin_is_a_weak_anchor() -> None:
    """While the file stands still it compares as same and is listed for whoever re-reads the
    upstream next. Once the file changes it cannot be followed: one of the twins deleted leaves
    the other, which used to read as the cited line having moved."""
    script = _script()
    module = _module([_ref("BUS", "src/thing.py", 4), _ref("B", "src/thing.py", 3)])
    report = _one(script, module, Store(_github(script, {"src/thing.py": (TWINS, TWINS)})))
    assert report.counts == {"not a file": 0, "same": 2, "weak anchor": 1}
    assert report.rows == [] and [row.ref for row in report.weak] == ["BUS"]
    assert report.drift is False and script.exit_code([report]) == 0
    assert "1 weak anchors" in script.section(report) and "`BUS` (`src/thing.py:4`)" in (
        script.section(report)
    )

    deleted = b"def a():\n    return self.bus\ndef b():\n"
    report = _one(script, module, Store(_github(script, {"src/thing.py": (TWINS, deleted)})))
    (row,) = [row for row in report.rows if row.ref == "BUS"]
    assert row.status == "changed" and row.weak is True
    assert row.now.startswith("one of 2 lines that read alike at the pin")
    assert report.drift is True


# ── stray anchors ───────────────────────────────────────────────────────────────────────


def _cite(name: str, path: str, line: int, note: str, status: Status = "VERIFIED") -> UpstreamRef:
    """A ref with a note of its own, where `_ref`'s note names nothing."""
    return UpstreamRef(name, status, f"{GITHUB}/blob/{PIN}/{path}#L{line}", note)


def test_a_verified_ref_whose_line_names_nothing_of_it_is_a_stray_anchor() -> None:
    """Fourteen of the rosbridge refs cited lines that held something else, a line or a function
    away from what each said it read, and read as the same, since those lines had not moved. A
    VERIFIED ref whose line holds no word of its name or its note is listed under its upstream,
    as a weak anchor is, and fails nothing by itself."""
    script = _script()
    module = _module(
        [
            _ref("READ", "src/thing.py", 2),
            _ref("Bus", "src/thing.py", 2),
            _cite("Port", "src/thing.py", 2, "opened in read()"),
            _cite("Bus.close", "src/thing.py", 2, "a guess", status="UNVERIFIED"),
            _ref("Bus", "src/other.py", 1),
        ]
    )
    files = {"src/thing.py": (PINNED, PINNED), "src/other.py": (b"x = 1\n", b"x = 2\n")}
    report = _one(script, module, Store(_github(script, files)))
    assert [(row.ref, row.cited) for row in report.stray] == [
        ("Bus", "src/thing.py:2"),
        ("Bus", "src/other.py:1"),
    ]
    assert report.counts == {"not a file": 0, "same": 4, "changed": 1, "stray anchor": 2}
    (changed,) = report.rows
    assert changed.status == "changed" and changed.stray is True, "a drifted row is listed too"
    assert report.drift is True and report.broken is False

    clean = _module([_ref("READ", "src/thing.py", 2), _ref("Bus", "src/thing.py", 2)])
    report = _one(script, clean, Store(_github(script, {"src/thing.py": (PINNED, PINNED)})))
    assert report.rows == [] and report.drift is False and report.broken is False
    assert script.exit_code([report]) == 0, "a stray anchor alone fails nothing"
    section = script.section(report)
    assert "1 stray anchors: each is VERIFIED" in section
    assert "`Bus` (`src/thing.py:2`)" in section
    assert "1 stray anchor" in script.summary_line(report)
    assert script._needing(section) == set(), "and needs nobody, so it earns no comment"
    (upstream,) = script.as_json([report])["upstreams"]
    assert [row["ref"] for row in upstream["stray"]] == ["Bus"]


def test_a_line_is_read_with_what_it_opens_and_a_path_named_ref_names_its_file() -> None:
    """`def __init__(` names a constructor's parameters on the lines under it, `@dataclass`
    names nothing until its class does, and `std_msgs/msg/String` cites the whole of
    `String.msg`. A word of one character counts from the ref's name and not from its note, and
    a keyword counts when the note quotes it."""
    script = _script()
    source = (
        b"@dataclass\n"
        b"class Obs:\n"
        b"    def __init__(\n"
        b"        self, port, baud\n"
        b"    ):\n"
        b"        self.K = port\n"
        b"    finally:\n"
        b"x = 1\n"
    )
    refs = [
        _ref("Obs", "src/obs.py", 1),
        _ref("Bus", "src/obs.py", 1),
        _ref("Obs(port, baud)", "src/obs.py", 3),
        _ref("Obs.K", "src/obs.py", 6),
        _cite("the port closes", "src/obs.py", 7, "in a `finally`"),
        _cite("Point", "src/obs.py", 8, "{x, y}"),
        _ref("std_msgs/msg/String", "msg/String.msg", 1),
    ]
    files = {"src/obs.py": (source, source), "msg/String.msg": (b"# a comment\n", b"# a comment\n")}
    report = _one(script, _module(refs), Store(_github(script, files)))
    assert [(row.ref, row.cited) for row in report.stray] == [
        ("Bus", "src/obs.py:1"),
        ("Point", "src/obs.py:8"),
    ]

    lines = source.decode().split("\n")
    assert {"robotstate", "robot", "state"} <= script.words("RobotState")
    assert {"get_param", "get", "param", "45", "v23", "23"} <= script.words("get_param 45.0 v23")
    assert script.words("Services") == {"services", "service"}, "a part loses its plural s"
    assert script._read_on(lines, 0) == "@dataclass\nclass Obs:"
    assert script._read_on(lines, 2).endswith("    ):")
    assert script._read_on(lines, 5) == "        self.K = port"


def test_a_decorator_over_several_lines_is_read_through_to_what_it_decorates() -> None:
    """A decorator's own brackets used to end the read at its second line, so the function under
    it, the thing a ref on the decorator names, was never read."""
    script = _script()
    route = (
        b"@router.get(\n"
        b'    "/state",\n'
        b"    response_model=State,\n"
        b")\n"
        b"@cached\n"
        b"async def read_joints(request):\n"
        b"    return state\n"
    )
    module = _module([_ref("read_joints", "src/api.py", 1), _ref("Bus", "src/api.py", 1)])
    report = _one(script, module, Store(_github(script, {"src/api.py": (route, route)})))
    assert [row.ref for row in report.stray] == ["Bus"]
    lines = route.decode().split("\n")
    assert script._read_on(lines, 0).endswith("async def read_joints(request):")


def test_a_word_that_names_nothing_keeps_its_s() -> None:
    """Without its s, `this` read as `thi`, a word no list of common ones holds, so a note saying
    what `this` does named every C++ line that calls a method on `this`."""
    script = _script()
    assert not script.words("this does") & {"thi", "doe"}
    module = _module([_cite("Port", "src/bus.cpp", 1, "this is what it does")])
    pinned = b"this->bus_->close();\n"
    report = _one(script, module, Store(_github(script, {"src/bus.cpp": (pinned, pinned)})))
    assert [row.ref for row in report.stray] == ["Port"]


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


def test_a_release_tag_with_words_before_its_number_is_followed_among_its_own_kind() -> None:
    """A repository that tags `daemon-v0.16.1` beside `app-v2.0.0` has no plain version tag at
    all, and a module pinned at a daemon release, compared with `main`, is red by design."""
    script = _script()
    module = _module([_ref("READ", "src/thing.py", 2)], RELEASE="daemon-v0.16.1")
    assert script.declared_baseline(module, WIDGET).prefix == "daemon-v"
    tags = ["app-v9.0.0", "daemon-v0.18.5", "daemon-v0.16.1", "daemon-v0.19.0-rc1", "v3.0.0"]
    urls = _github(script, {"src/thing.py": (PINNED, PINNED)}, tags=tags, newest="daemon-v0.18.5")
    del urls[f"{script.API}/repos/{WIDGET}"]
    report = _one(script, module, Store(urls))
    assert report.baselines[WIDGET].kind == "tag"
    assert report.baselines[WIDGET].name == "daemon-v0.18.5"

    named = [{"name": tag} for tag in tags]
    assert script.newest_release(named, "daemon-v") == "daemon-v0.18.5"
    assert script.newest_release(named, "app-v") == "app-v9.0.0"
    assert script.newest_release(named) == "v3.0.0", "a plain version never matches a prefixed tag"
    assert script.newest_release(named, "v") == "v3.0.0"
    assert script.newest_release(named, "firmware-") is None

    nothing = dict(urls)
    nothing[f"{script.API}/repos/{WIDGET}/tags?per_page=100"] = _tag_page(["app-v9.0.0"])
    report = _one(script, module, Store(nothing))
    assert report.rows[0].status == "baseline gone"
    assert report.rows[0].now == f"{WIDGET} lists no release tag starting 'daemon-v'"


def test_the_tags_are_read_a_page_at_a_time_until_one_comes_back_short() -> None:
    script = _script()
    module = _module([_ref("READ", "src/thing.py", 2)], TAG="v2.0.0")
    first = [f"v1.0.{n}" for n in range(100)]
    urls = _github(script, {"src/thing.py": (PINNED, PINNED)}, tags=first, newest="v2.1.0")
    del urls[f"{script.API}/repos/{WIDGET}"]
    urls[f"{script.API}/repos/{WIDGET}/tags?per_page=100&page=2"] = _tag_page(["v2.1.0", "v2.0.0"])
    store = Store(urls)
    report = _one(script, module, store)
    assert report.baselines[WIDGET].name == "v2.1.0"
    assert sum("/tags?" in url for url in store.calls) == 2, "a short page is the last"


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
        ("PyPI widget is 1.1.0; quackd read 1.0.0", True),
        ("pyproject.toml says 1.2.0 at main; 1.0.1 at the pin", True),
    ]
    assert report.drift is True
    assert "- **moved:** PyPI widget is 1.1.0; quackd read 1.0.0" in script.section(report)

    urls[f"{script.PYPI}/widget/json"] = json.dumps({"info": {"version": "1.0.0"}}).encode()
    urls[f"{script.RAW}/{WIDGET}/{HEAD}/pyproject.toml"] = b'version = "1.0.1"\n'
    report = _one(script, module, Store(urls))
    assert report.versions == [
        ("PyPI widget is still 1.0.0", False),
        ("pyproject.toml still says 1.0.1 at main", False),
    ]
    assert report.drift is False
    assert "- PyPI widget is still 1.0.0" in script.section(report)


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
        ("PyPI huggingface_hub is 1.30.0; quackd read 1.29.0", True),
        (f"{api_doc} is 2.1.0; quackd read 2.0.0", True),
    ]
    assert report.drift is True


# ── outages, reports, exit codes ────────────────────────────────────────────────────────


def test_an_outage_is_unchecked_and_exit_2_unless_something_else_drifted() -> None:
    script = _script()
    module = _module([_ref("READ", "src/thing.py", 2), _ref("BUS", "src/thing.py", 5)])
    urls = _github(script, {"src/thing.py": (PINNED, PINNED)})
    urls[f"{script.API}/repos/{WIDGET}"] = script.Unreachable("api.github.com: HTTP 503")
    report = _one(script, module, Store(urls))
    assert report.counts == {"not a file": 0, "unchecked": 2}
    assert report.unchecked == ["api.github.com: HTTP 503 (READ, BUS)"], "one line per cause"
    assert report.drift is False and report.broken is False
    assert script.exit_code([report]) == 2
    assert script.headline([report]) == (
        "Could not be checked tonight: widget. Nothing drifted among what answered."
    )

    drifted = _module([_ref("READ", "src/thing.py", 2)], name="other_api")
    other = _one(script, drifted, Store(_github(script, {"src/thing.py": (PINNED, CHANGED)})))
    assert script.exit_code([report, other]) == 1
    assert script.headline([other]) == "Drifted: widget."
    assert script.headline([]) == "Nothing drifted."


def test_a_url_that_failed_is_not_asked_again_and_a_failing_host_is_asked_nothing_more() -> None:
    """An outage with a retry and a wait on every URL is hours of waiting, and the job has
    fifteen minutes: one failure per URL, and after three running a host is left alone."""
    script = _script()
    refs = [_ref(f"R{n}", f"src/f{n}.py", 1) for n in range(6)]
    refs.append(_ref("AGAIN", "src/f0.py", 2))
    module = _module(refs)
    urls = _github(script, {})
    for n in range(6):
        urls[f"{script.RAW}/{WIDGET}/{PIN}/src/f{n}.py"] = script.Unreachable(
            "raw: HTTP 503", slow=True
        )
    store = Store(urls)
    report = _one(script, module, store)
    raw = [url for url in store.calls if url.startswith(script.RAW)]
    assert len(raw) == script.HOST_FAILURES == 3, raw
    assert report.counts["unchecked"] == 7
    breaker = (
        "raw.githubusercontent.com: 3 slow failures running, so nothing more was asked of it; "
        "the last was raw: HTTP 503"
    )
    # AGAIN cites the first file, whose failure is remembered, so it is held up by that failure
    # and not by the host's being left alone
    assert report.blocked == {
        "raw: HTTP 503": ["R0", "R1", "R2", "AGAIN"],
        breaker: ["R3", "R4", "R5"],
    }
    assert report.unchecked == [
        "raw: HTTP 503 (R0, R1, R2, AGAIN)",
        f"{breaker} (R3, R4, R5)",
    ]
    assert script.exit_code([report]) == 2

    # a refusal that answers at once costs nothing, so it does not count
    refusing = Store({f"https://a.test/{n}": script.Unreachable("HTTP 403") for n in range(6)})
    quick = script.Cache(refusing)
    for n in range(6):
        with pytest.raises(script.Unreachable, match="HTTP 403"):
            quick.get(f"https://a.test/{n}")
    assert quick.requests == 6

    # a success between failures starts the count again
    cache = script.Cache(
        Store({"https://a.test/1": script.Unreachable("x"), "https://a.test/2": b""})
    )
    for url, raises in (("https://a.test/1", True), ("https://a.test/2", False)) * 4:
        if raises:
            with pytest.raises(script.Unreachable, match=r"^x$"):
                cache.get(url)
        else:
            assert cache.get(url) == b""
    assert cache.requests == 2


def test_the_markdown_report_and_the_json_carry_the_same_rows() -> None:
    script = _script()
    module = _module([_ref("READ", "src/thing.py", 2), _ref("BUS", "src/thing.py", 5)])
    report = _one(script, module, Store(_github(script, {"src/thing.py": (PINNED, CHANGED)})))
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
    assert payload["drifted"] == ["widget"] and payload["broken"] == []
    assert payload["upstreams"][0]["rows"] == [vars(row) for row in report.rows]
    assert payload["upstreams"][0]["baselines"][WIDGET] == {
        "kind": "default",
        "name": "main",
        "sha": HEAD,
    }
    assert payload["upstreams"][0]["summary"] == script.summary_line(report)
    json.dumps(payload)  # every value in it is JSON


def test_a_pipe_in_a_ref_or_a_line_does_not_split_the_table() -> None:
    """GitHub splits a table cell on a pipe even inside a code span, and a type annotation is
    full of them."""
    script = _script()
    pinned = b"import os\nlimit: int | None = None\n"
    changed = b"import os\nlimit: float | None = None\n"
    module = _module([_ref("limit: int | None", "src/thing.py", 2)])
    report = _one(script, module, Store(_github(script, {"src/thing.py": (pinned, changed)})))
    row = [line for line in script.section(report).splitlines() if line.startswith("| `limit")]
    assert row == [
        "| `limit: int \\| None` | `src/thing.py:2` | changed | "
        f"no line reads `limit: int \\| None = None` at {HEAD[:7]} |"
    ]
    assert len(re.findall(r"(?<!\\)\|", row[0])) == 5, "four cells"
    assert script._needing(script.section(report)) == {"limit: int \\| None: changed"}


def test_upstreams_words_are_read_as_upstreams_words() -> None:
    """A line of XML in a table cell is taken for markup and reads as nothing, `@contextmanager`
    mentions somebody, and a line's own spacing is part of what it says."""
    script = _script()
    pinned = b'<position kp="998.22"/>\n@contextmanager\nx  = 1\n'
    changed = b'<position kp="1.0"/>\n@cached\nx = 1\n'
    module = _module(
        [
            _ref("XML", "scene.xml", 1),
            _ref("DECORATOR", "scene.xml", 2),
            _ref("SPACED", "scene.xml", 3),
        ]
    )
    report = _one(script, module, Store(_github(script, {"scene.xml": (pinned, changed)})))
    now = {row.ref: row.now for row in report.rows}
    assert now["XML"].startswith('no line reads `<position kp="998.22"/>` at ')
    assert now["DECORATOR"].startswith("no line reads `@contextmanager` at ")
    assert now["SPACED"].startswith("no line reads `x  = 1` at ")
    assert "x  = 1" in script.section(report), "the two spaces are what changed"

    blocked = _module([_ref("READ", "src/thing.py", 2)])
    urls = _github(script, {})
    urls[f"{script.RAW}/{WIDGET}/{PIN}/src/thing.py"] = script.Unreachable(
        "raw: <urlopen error timed out>"
    )
    text = script.section(_one(script, blocked, Store(urls)))
    assert "- could not check: `raw: <urlopen error timed out>` (READ)" in text


def test_a_tick_of_a_backtick_in_a_line_does_not_end_its_code_span() -> None:
    script = _script()
    assert script._code("a `b` c") == "``a `b` c``"
    assert script._code("`edge`") == "`` `edge` ``"
    assert script._code("plain") == "`plain`"


def test_line_endings_and_a_byte_order_mark_are_no_change() -> None:
    script = _script()
    module = _module([_ref("WHOLE", "src/code.py"), _ref("FIRST", "src/first.py", 1)])
    files = {
        "src/code.py": (b"x = 1\ny = 2\n", b"x = 1\r\ny = 2\r\n"),
        "src/first.py": (b"import os\n", b"\xef\xbb\xbfimport os\n"),
    }
    report = _one(script, module, Store(_github(script, files)))
    assert report.rows == [] and report.counts["same"] == 2, report.rows


def test_a_path_with_a_space_is_quoted_once() -> None:
    script = _script()
    assert script.raw_url(WIDGET, PIN, "STL/SO101 Assembly.stl").endswith(
        "/STL/SO101%20Assembly.stl"
    )
    assert script.raw_url(WIDGET, PIN, "STL/SO101%20Assembly.stl").endswith(
        "/STL/SO101%20Assembly.stl"
    )
    assert script.contents_url(WIDGET, "a b", PIN).endswith(f"/contents/a%20b?ref={PIN}")


class _Answer(io.BytesIO):
    def __enter__(self) -> _Answer:
        return self

    def __exit__(self, *exc: object) -> None:
        return None


def _http_error(code: int, retry_after: str | None = None) -> Any:
    from email.message import Message

    headers = Message()
    if retry_after is not None:
        headers["Retry-After"] = retry_after
    return urllib.error.HTTPError("https://a.test/x", code, "said so", headers, None)


def test_a_retry_after_too_long_for_the_job_is_a_night_it_did_not_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    script = _script()
    slept: list[float] = []
    monkeypatch.setattr(script.time, "sleep", slept.append)

    def refuses(request: Any, timeout: float) -> Any:
        raise _http_error(429, "3600")

    monkeypatch.setattr(script.urllib.request, "urlopen", refuses)
    with pytest.raises(script.Unreachable, match="HTTP 429") as caught:
        script.web_fetch("https://a.test/x")
    assert slept == [] and caught.value.slow is True, "an hour is not waited for"

    answers = [_http_error(503, "2"), _Answer(b"body")]

    def recovers(request: Any, timeout: float) -> Any:
        answer = answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(script.urllib.request, "urlopen", recovers)
    assert script.web_fetch("https://a.test/x") == b"body"
    assert slept == [2.0]


def test_a_read_that_breaks_off_is_an_outage_and_not_a_traceback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    script = _script()

    def short(request: Any, timeout: float) -> Any:
        raise http.client.IncompleteRead(b"par", 40)

    monkeypatch.setattr(script.urllib.request, "urlopen", short)
    with pytest.raises(script.Unreachable) as caught:
        script.web_fetch("https://a.test/x")
    assert caught.value.slow is True


# ── a baseline that went, and versions nobody can read ──────────────────────────────────


def test_a_baseline_that_went_is_drift_and_not_an_outage() -> None:
    """A branch deleted, a repository gone and a release tag no longer there each read as an
    outage, which exits 2 and leaves an issue unopened for ever."""
    script = _script()
    refs = [_ref("READ", "src/thing.py", 2), _ref("BUS", "src/thing.py", 5)]

    branch = _github(script, {"src/thing.py": (PINNED, PINNED)}, branch="v2")
    branch[f"{script.API}/repos/{WIDGET}/commits/v2"] = None
    report = _one(script, _module(refs, BRANCH="v2"), Store(branch))
    assert [(r.ref, r.status) for r in report.rows] == [("READ", "baseline gone")]
    assert report.rows[0].now == f"{WIDGET} has no branch called v2 any more"
    assert report.counts["baseline gone"] == 2, "every ref it holds up is counted, on one row"
    assert report.drift is True and report.blocked == {}
    assert script.exit_code([report]) == 1
    gh = FakeGh([])
    script.sync_issues([report], "rokbenko/quackd", gh)
    assert gh.verbs() == ["label create", "issue list", "issue create"]

    repo = _github(script, {})
    repo[f"{script.API}/repos/{WIDGET}"] = None
    report = _one(script, _module(refs), Store(repo))
    assert report.rows[0].now == f"{WIDGET} answers 404: the repository is gone or private"

    tags = _github(script, {}, tags=["nightly"])
    report = _one(script, _module(refs, VERSION="0.6.1"), Store(tags))
    assert report.rows[0].now == f"{WIDGET} lists no release tag with a plain version"


def test_a_directory_gone_at_the_baseline_is_drift_and_a_submodule_is_a_directory() -> None:
    script = _script()
    module = _module([_ref("DIR", "src/package")])
    urls = _github(script, {"src/package": (None, None)})
    urls.update(_contents(script, "src/package", [{"name": "__init__.py"}]))
    urls[f"{script.API}/repos/{WIDGET}/contents/src/package?ref={HEAD}"] = None
    report = _one(script, module, Store(urls))
    assert [(r.status, r.now) for r in report.rows] == [
        ("gone", f"no such directory at {HEAD[:7]}")
    ]
    assert report.drift is True

    urls.update(_contents(script, "src/package", {"type": "submodule"}))
    urls[f"{script.API}/repos/{WIDGET}/contents/src/package?ref={HEAD}"] = b'{"type": "submodule"}'
    report = _one(script, module, Store(urls))
    assert report.rows == [] and report.counts["a directory"] == 1 and report.blocked == {}


def test_a_version_nobody_can_read_holds_the_issue_open() -> None:
    """A 404 from PyPI, a pyproject.toml or an API document used to write no line and block
    nothing, so a moved version's issue closed that night as though the version stood."""
    script = _script()
    api_doc = "https://api.example.test/openapi.json"
    module = _module(
        [_ref("READ", "src/thing.py", 2)],
        PACKAGE=UpstreamRef("widget", "VERIFIED", f"{GITHUB}/blob/{PIN}/pyproject.toml#L3", ""),
        PYPI_VERSION_READ="1.0.0",
        VERSION_AT_PIN=UpstreamRef(
            "1.0.1", "VERIFIED", f"{GITHUB}/blob/{PIN}/pyproject.toml#L4", ""
        ),
        API_DOC=api_doc,
        API_VERSION="2.0.0",
    )
    urls = _github(script, {"src/thing.py": (PINNED, PINNED)})
    urls[f"{script.PYPI}/widget/json"] = b'{"releases": {}}'
    urls[f"{script.RAW}/{WIDGET}/{HEAD}/pyproject.toml"] = None
    urls[api_doc] = None
    report = _one(script, module, Store(urls))
    assert report.versions == []
    assert set(report.blocked) == {
        f"{script.PYPI}/widget/json names no version",
        f"{script.RAW}/{WIDGET}/{HEAD}/pyproject.toml names no version",
        f"{api_doc} names no version",
    }
    assert script.exit_code([report]) == 2
    body = "- **moved:** PyPI widget is 1.1.0; quackd read 1.0.0"
    gh = FakeGh([{"number": 7, "title": "upstream drift: widget", "body": body}])
    script.sync_issues([report], "rokbenko/quackd", gh)
    assert gh.verbs() == ["label create", "issue list"], "neither closed nor edited"


def test_what_became_of_a_ref_earns_a_word_when_it_changes() -> None:
    script = _script()
    module = _module([_ref("READ", "src/thing.py", 2)])
    went = _one(script, module, Store(_github(script, {"src/thing.py": (PINNED, None)})))
    changed = script.section(
        _one(script, module, Store(_github(script, {"src/thing.py": (PINNED, CHANGED)})))
    )
    gh = FakeGh([{"number": 7, "title": "upstream drift: widget", "body": changed}])
    script.sync_issues([went], "rokbenko/quackd", gh)
    assert gh.verbs()[-1] == "issue comment", "a line that changed and then went"


def test_one_upstreams_refusal_is_said_and_stops_no_other() -> None:
    script = _script()
    first = _drifted_report(script)
    first.name = "first"
    second = _drifted_report(script)
    second.name = "second"

    class Refuses(FakeGh):
        def __call__(self, args: Any) -> str:
            if list(args[:2]) == ["issue", "create"] and "upstream drift: first" in args:
                raise script.Unreachable("gh issue: HTTP 403")
            return super().__call__(args)

    gh = Refuses([])
    done = script.sync_issues([first, second], "rokbenko/quackd", gh)
    assert done == [
        "first: issue not synced: gh issue: HTTP 403",
        "second: issue opened (https://github.com/rokbenko/quackd/issues/99)",
    ]


def test_what_the_issues_did_goes_in_the_report_too(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A job red every night hides a refusal in its log, and the summary is what gets read."""
    script = _script()
    monkeypatch.setattr(
        script, "upstream_rows", lambda: [("widget", _module([_ref("READ", "src/thing.py", 2)]))]
    )
    monkeypatch.setattr(
        script, "web_fetch", Store(_github(script, {"src/thing.py": (PINNED, CHANGED)}))
    )
    monkeypatch.setattr(
        script,
        "sync_issues",
        lambda reports, repo, known=None: ["widget: issue not synced: HTTP 403"],
    )
    monkeypatch.setattr(script, "unread_adapters", lambda: [])
    report = tmp_path / "summary.md"
    assert script.main(["--report", str(report), "--issues", "rokbenko/quackd"]) == 1
    text = report.read_text(encoding="utf-8")
    assert text.endswith("\n## Issues\n\n- `widget: issue not synced: HTTP 403`\n"), text


def test_offline_fixtures_map_each_kind_of_url_to_a_file(tmp_path: Path) -> None:
    script = _script()
    root = tmp_path / "fixtures"
    api = root / "api" / "acme" / "widget"
    expected = {
        f"{script.RAW}/{WIDGET}/{PIN}/src/thing.py": root.joinpath(
            "raw", "acme", "widget", PIN, "src", "thing.py"
        ),
        f"{script.API}/repos/{WIDGET}": api / "repo.json",
        f"{script.API}/repos/{WIDGET}/commits/main": api / "commits" / "main.json",
        f"{script.API}/repos/{WIDGET}/tags?per_page=100": api / "tags.json",
        f"{script.API}/repos/{WIDGET}/tags?per_page=100&page=2": api / "tags.2.json",
        f"{script.API}/repos/{WIDGET}/contents/src/thing.py?ref={PIN}": api.joinpath(
            "contents", PIN, "src", "thing.py.json"
        ),
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


def test_a_line_less_ref_to_a_directory_is_counted_and_is_nobodys_fault() -> None:
    """`src("src/package")` cites a directory, which the repository has and the raw host does
    not serve: the API says it is one, so it is counted, never a row, never drift, never an
    outage."""
    script = _script()
    module = _module([_ref("DIR", "src/package"), _ref("READ", "src/thing.py", 2)])
    urls = _github(script, {"src/package": (None, None), "src/thing.py": (PINNED, PINNED)})
    urls.update(_contents(script, "src/package", [{"name": "__init__.py"}]))
    urls[f"{script.API}/repos/{WIDGET}/contents/src/package?ref={HEAD}"] = b'[{"name": "x.py"}]'
    report = _one(script, module, Store(urls))
    assert report.counts == {"not a file": 0, "a directory": 1, "same": 1}
    assert report.rows == []
    assert report.drift is False and report.broken is False and report.blocked == {}
    assert script.exit_code([report]) == 0

    # and a line-less ref to a path that is nothing at all is a broken ref
    urls.update(_contents(script, "src/package", None))
    report = _one(script, module, Store(urls))
    assert [(row.ref, row.status) for row in report.rows] == [("DIR", "nothing at the pin")]
    assert report.broken is True


# ── the command ─────────────────────────────────────────────────────────────────────────


def test_the_command_on_a_night_nothing_answers_says_unchecked_and_exits_2(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The real modules on a night nothing answers: nothing can be compared, and the night reads
    as unchecked and never as clean."""
    script = _script()

    def down(url: str) -> None:
        raise script.Unreachable("nothing answers")

    monkeypatch.setattr(script, "web_fetch", down)
    report, findings = tmp_path / "report.md", tmp_path / "drift.json"
    code = script.main(["--report", str(report), "--json", str(findings)])
    assert code == 2
    text = report.read_text(encoding="utf-8")
    assert "\r" not in text and text == capsys.readouterr().out
    assert text.startswith("# Upstream drift\n\nCould not be checked tonight: ")
    assert "Nothing drifted among what answered." in text.splitlines()[2]
    payload = json.loads(findings.read_text(encoding="utf-8"))
    assert payload["drifted"] == [] and payload["broken"] == []
    # every upstream but Huebot, whose files are the Microduck's and are judged under it
    names = {upstream["name"] for upstream in payload["upstreams"]}
    assert set(payload["unchecked"]) == names - {"huebot"} and len(names) >= 13

    assert script.main(["--only", "nope"]) == 2
    assert "no upstream is called 'nope'" in capsys.readouterr().out

    # fixtures on disk with nothing in them are every repository answering 404, which is drift
    assert script.main(["--offline", str(tmp_path / "empty"), "--only", "lerobot"]) == 1
    assert "baseline gone" in capsys.readouterr().out


def test_the_report_prints_on_a_console_that_cannot_carry_an_upstreams_line(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A Windows console's code page has no arrow, an upstream's Markdown heading does, and the
    report is already in its files by then: the print degrades and the command goes on."""
    script = _script()
    raw = io.BytesIO()
    console = io.TextIOWrapper(raw, encoding="cp1252", errors="strict", newline="")
    monkeypatch.setattr(sys, "stdout", console)
    script._say("call_service (C ↔ S)\n")
    sys.stdout.flush()
    assert raw.getvalue() == b"call_service (C ? S)\n"


def test_the_token_goes_to_the_api_and_to_nothing_else(monkeypatch: pytest.MonkeyPatch) -> None:
    """The raw host answers a token it does not like with a 404, which would read as a file
    that is not there, so it is never sent one."""
    script = _script()
    seen: list[tuple[str, dict[str, str]]] = []

    class Answer(io.BytesIO):
        def __enter__(self) -> Answer:
            return self

        def __exit__(self, *exc: object) -> None:
            return None

    def urlopen(request: Any, timeout: float) -> Answer:
        seen.append((request.full_url, dict(request.header_items())))
        return Answer(b"body")

    monkeypatch.setenv("GITHUB_TOKEN", "t0ken")
    monkeypatch.setattr(script.urllib.request, "urlopen", urlopen)
    for url in (
        f"{script.API}/repos/{WIDGET}",
        f"{script.RAW}/{WIDGET}/{PIN}/src/thing.py",
        f"{script.PYPI}/widget/json",
    ):
        assert script.web_fetch(url) == b"body"
    (api, raw, pypi) = (headers for _url, headers in seen)
    assert api.get("Authorization") == "Bearer t0ken"
    assert "Authorization" not in raw and "Authorization" not in pypi


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
    return _one(script, module, Store(_github(script, {"src/thing.py": (PINNED, CHANGED)})))


def _clean_report(script: ModuleType) -> Any:
    module = _module([_ref("READ", "src/thing.py", 2)])
    return _one(script, module, Store(_github(script, {"src/thing.py": (PINNED, PINNED)})))


def _outage_report(script: ModuleType) -> Any:
    module = _module([_ref("READ", "src/thing.py", 2)])
    urls = _github(script, {"src/thing.py": (PINNED, PINNED)})
    urls[f"{script.API}/repos/{WIDGET}"] = script.Unreachable("api.github.com: HTTP 503")
    return _one(script, module, Store(urls))


def _open(number: int, body: str) -> list[dict[str, Any]]:
    return [{"number": number, "title": "upstream drift: widget", "body": body}]


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

    gh = FakeGh(_open(99, body))
    done = script.sync_issues([_clean_report(script)], "rokbenko/quackd", gh)
    assert gh.verbs() == ["label create", "issue list", "issue close"]
    assert gh.calls[2][2] == "99"
    assert done == ["widget: issue #99 closed"]

    # and a clean night with no issue open does nothing at all
    gh = FakeGh([])
    assert script.sync_issues([_clean_report(script)], "rokbenko/quackd", gh) == []
    assert gh.verbs() == ["label create", "issue list"]


def test_a_night_that_could_not_check_closes_nothing_and_rewrites_nothing() -> None:
    """An outage is not a clean night. It used to close the issue, which the next good night
    reopened under a new number, and a table missing the rows nobody could fetch would read as
    drift that went away."""
    script = _script()
    body = script.section(_drifted_report(script))
    outage = _outage_report(script)
    assert outage.drift is False and outage.blocked

    gh = FakeGh(_open(7, body))
    done = script.sync_issues([outage], "rokbenko/quackd", gh)
    assert gh.verbs() == ["label create", "issue list"], "neither closed nor edited"
    assert done == ["widget: not all of it could be checked, issue #7 left"]

    # with no issue open there is nothing to leave, and nothing to open for an outage alone
    gh = FakeGh([])
    assert script.sync_issues([outage], "rokbenko/quackd", gh) == []
    assert gh.verbs() == ["label create", "issue list"]

    # what a half-checked night did find still opens one
    module = _module([_ref("READ", "src/thing.py", 2), _ref("HUB", "src/hub.py", 1)])
    urls = _github(script, {"src/thing.py": (PINNED, CHANGED)})
    urls[f"{script.RAW}/{WIDGET}/{PIN}/src/hub.py"] = script.Unreachable("raw: HTTP 503")
    half = _one(script, module, Store(urls))
    assert half.drift is True and half.blocked
    gh = FakeGh([])
    script.sync_issues([half], "rokbenko/quackd", gh)
    assert gh.verbs() == ["label create", "issue list", "issue create"]


def test_a_ref_that_cannot_be_followed_opens_an_issue_too() -> None:
    script = _script()
    module = _module([_ref("BLANK", "src/thing.py", 3)])
    report = _one(script, module, Store(_github(script, {"src/thing.py": (PINNED, PINNED)})))
    assert report.broken is True and report.drift is False
    gh = FakeGh([])
    script.sync_issues([report], "rokbenko/quackd", gh)
    assert gh.verbs() == ["label create", "issue list", "issue create"]
    body = gh.calls[2][gh.calls[2].index("--body") + 1]
    assert "| `BLANK` | `src/thing.py:3` | bad anchor |" in body
    assert "anchor it on the line that names what it cites" in body
    assert script._needing(body) == {"BLANK: bad anchor"}


def test_an_open_issue_is_edited_in_place_and_commented_only_when_what_needs_somebody_changes() -> (
    None
):
    script = _script()
    report = _drifted_report(script)
    first_body = script.section(report)
    same_set = FakeGh(_open(7, first_body))
    done = script.sync_issues([report], "rokbenko/quackd", same_set)
    assert same_set.verbs() == ["label create", "issue list", "issue edit"]
    assert done == ["widget: issue #7 updated"]

    older = first_body.replace("| `READ` |", "| `OTHER` |")
    changed_set = FakeGh(_open(7, older))
    script.sync_issues([report], "rokbenko/quackd", changed_set)
    assert changed_set.verbs() == ["label create", "issue list", "issue edit", "issue comment"]

    # a version that moved is part of what needs somebody, so a release on PyPI earns a comment
    earlier = first_body + "\n- **moved:** PyPI widget is 1.1.0; quackd read 1.0.0"
    versions = FakeGh(_open(7, earlier))
    script.sync_issues([report], "rokbenko/quackd", versions)
    assert versions.verbs()[-1] == "issue comment"

    unrelated = FakeGh([{"number": 8, "title": "upstream drift: other", "body": ""}])
    script.sync_issues([report], "rokbenko/quackd", unrelated)
    assert unrelated.verbs() == ["label create", "issue list", "issue create"]


def test_no_gh_on_the_machine_is_an_answer_and_not_a_traceback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    script = _script()

    def missing(*args: Any, **kwargs: Any) -> Any:
        raise FileNotFoundError("gh")

    monkeypatch.setattr(script.subprocess, "run", missing)
    with pytest.raises(script.Unreachable, match="gh could not be run"):
        script.gh_run(["issue", "list"])


def _comment(call: list[str]) -> str:
    return call[call.index("--comment") + 1]


def test_a_repeat_of_an_upstreams_issue_is_closed_for_the_oldest() -> None:
    """Two open issues with one title used to leave all but the first `gh` listed alone: edited
    or closed was whichever came first, and the other stayed open whatever the nights found."""
    script = _script()
    report = _drifted_report(script)
    body = script.section(report)
    # gh lists the newest first
    two = [
        {"number": 12, "title": "upstream drift: widget", "body": body},
        {"number": 7, "title": "upstream drift: widget", "body": body},
    ]

    gh = FakeGh(two)
    done = script.sync_issues([report], "rokbenko/quackd", gh)
    assert gh.verbs() == ["label create", "issue list", "issue edit", "issue close"]
    assert gh.calls[2][2] == "7", "the oldest is the one kept"
    assert gh.calls[3][2] == "12"
    assert "#7" in _comment(gh.calls[3]) and "carries on" in _comment(gh.calls[3])
    assert done == ["widget: issue #7 updated", "widget: issue #12 closed as a repeat of #7"]

    # a clean night closes both, and a night that could not check the upstream closes neither
    gh = FakeGh(two)
    done = script.sync_issues([_clean_report(script)], "rokbenko/quackd", gh)
    assert gh.verbs() == ["label create", "issue list", "issue close", "issue close"]
    assert [call[2] for call in gh.calls[2:]] == ["7", "12"]
    assert done == ["widget: issue #7 closed", "widget: issue #12 closed"]
    gh = FakeGh(two)
    done = script.sync_issues([_outage_report(script)], "rokbenko/quackd", gh)
    assert gh.verbs() == ["label create", "issue list"]
    assert done == ["widget: not all of it could be checked, issue #7 left"]


def test_an_issue_for_an_upstream_no_adapter_reads_is_closed_saying_why() -> None:
    """An upstream renamed or removed used to leave its issue open for ever: nothing the walk
    reads is called that, so nothing updated it or closed it."""
    script = _script()
    wrote = f"a table\n\n{script.OPENED_BY} For a ref that changed, read it again."
    listed = [
        {"number": 5, "title": "upstream drift: gadget", "body": wrote},
        {"number": 6, "title": "upstream drift is noisy", "body": ""},
        {"number": 7, "title": "upstream drift: lerobot moved its bus, read it again", "body": ""},
        {"number": 8, "title": "upstream drift: widget", "body": wrote},
    ]
    gh = FakeGh(listed)
    done = script.sync_issues(
        [_clean_report(script)], "rokbenko/quackd", gh, known=["widget", "lerobot"]
    )
    closed = [call for call in gh.calls if call[:2] == ["issue", "close"]]
    assert [call[2] for call in closed] == ["8", "5"], "an issue a person opened is left"
    assert "gadget" in _comment(closed[1]) and "any more" in _comment(closed[1])
    assert done == [
        "widget: issue #8 closed",
        "gadget: issue #5 closed, since no upstream is called that now",
    ]

    # an upstream the walk knows is not touched on a night it was not run, as under --only
    gh = FakeGh([{"number": 9, "title": "upstream drift: lerobot", "body": ""}])
    assert script.sync_issues([], "rokbenko/quackd", gh, known=["widget", "lerobot"]) == []
    assert gh.verbs() == ["label create", "issue list"]

    # without the walk, and on a night anything could not be checked, nothing is closed
    for reports, known in (([_clean_report(script)], None), ([_outage_report(script)], ["widget"])):
        gh = FakeGh(listed[:1])
        assert script.sync_issues(reports, "rokbenko/quackd", gh, known=known) == []
        assert gh.verbs() == ["label create", "issue list"]


def test_a_night_an_adapter_could_not_be_read_on_is_no_clean_night(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An adapter that did not import takes its upstreams out of the walk, and with nothing else
    wrong the night said it was clean. So it says who was not read, and exits as could not check."""
    script = _script()
    module = _module([_ref("READ", "src/thing.py", 2)])
    monkeypatch.setattr(script, "upstream_rows", lambda: [("widget", module)])
    monkeypatch.setattr(
        script, "web_fetch", Store(_github(script, {"src/thing.py": (PINNED, PINNED)}))
    )
    report, findings = tmp_path / "summary.md", tmp_path / "drift.json"
    argv = ["--report", str(report), "--json", str(findings)]

    monkeypatch.setattr(script, "unread_adapters", lambda: ["toddlerbot could not be read: boom"])
    assert script.main(argv) == 2
    assert "Not read tonight" in report.read_text(encoding="utf-8")
    assert "toddlerbot could not be read: boom" in report.read_text(encoding="utf-8")
    assert json.loads(findings.read_text(encoding="utf-8"))["unread"] == [
        "toddlerbot could not be read: boom"
    ]

    monkeypatch.setattr(script, "unread_adapters", lambda: [])
    assert script.main(argv) == 0
    assert "Not read tonight" not in report.read_text(encoding="utf-8")
    assert json.loads(findings.read_text(encoding="utf-8"))["unread"] == []

    def unasked() -> list[str]:
        raise AssertionError("a run of one upstream walks no adapter")

    monkeypatch.setattr(script, "unread_adapters", unasked)
    assert script.main(["--only", "widget"]) == 0


def test_the_command_says_which_upstreams_it_knows_only_after_a_walk_of_every_adapter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An adapter that did not import takes its upstreams out of the walk, and their issues would
    read as an upstream nobody reads any more: such a night closes none of them, and says why."""
    script = _script()
    module = _module([_ref("READ", "src/thing.py", 2)])
    monkeypatch.setattr(script, "upstream_rows", lambda: [("widget", module), ("other", module)])
    monkeypatch.setattr(
        script, "web_fetch", Store(_github(script, {"src/thing.py": (PINNED, PINNED)}))
    )
    asked: list[Any] = []

    def sync(reports: Any, repo: str, known: Any = None) -> list[str]:
        asked.append(known)
        return []

    monkeypatch.setattr(script, "sync_issues", sync)
    monkeypatch.setattr(script, "unread_adapters", lambda: [])
    report = tmp_path / "summary.md"
    assert script.main(["--report", str(report), "--issues", "rokbenko/quackd"]) == 0
    assert asked == [["widget", "other"]]

    monkeypatch.setattr(script, "unread_adapters", lambda: ["toddlerbot could not be read: boom"])
    script.main(["--report", str(report), "--issues", "rokbenko/quackd"])
    assert asked[-1] is None
    assert "since toddlerbot could not be read: boom" in report.read_text(encoding="utf-8")

    def unasked() -> list[str]:
        raise AssertionError("a run of one upstream walks no adapter for the issues")

    monkeypatch.setattr(script, "unread_adapters", unasked)
    assert script.main(["--only", "widget", "--issues", "rokbenko/quackd"]) == 0
    assert asked[-1] is None


def test_an_adapter_that_does_not_import_is_one_the_walk_could_not_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from quackd.adapters import factory

    script = _script()
    real = factory._module

    class Rows:
        """An `UPSTREAMS` that raises when it is read, which doctor's walk also passes over."""

        def __iter__(self) -> Any:
            raise RuntimeError("no rows tonight")

    def module(name: str) -> Any:
        if name == "toddlerbot":
            raise ImportError("no module named toddlerbot_sdk")
        if name == "xlerobot":
            return types.SimpleNamespace(UPSTREAMS=Rows())
        return real(name)

    monkeypatch.setattr(factory, "_module", module)
    monkeypatch.setattr(factory, "is_installed", lambda name: name != "moss")
    unread = script.unread_adapters()
    assert "toddlerbot could not be read: no module named toddlerbot_sdk" in unread
    assert "xlerobot could not be read: no rows tonight" in unread
    assert "moss is not installed" in unread
    assert len(unread) == 3, unread


def test_what_was_done_before_a_refusal_stays_in_the_summary() -> None:
    """One upstream's issues take several calls now. A refusal on the last of them used to be
    the only line, as though nothing before it had been done."""
    script = _script()
    report = _drifted_report(script)
    body = script.section(report)
    two = [
        {"number": 12, "title": "upstream drift: widget", "body": body},
        {"number": 7, "title": "upstream drift: widget", "body": body},
    ]

    class Refuses(FakeGh):
        def __call__(self, args: Any) -> str:
            if list(args[:3]) == ["issue", "close", "12"]:
                raise script.Unreachable("gh issue: HTTP 403")
            return super().__call__(args)

    done = script.sync_issues([report], "rokbenko/quackd", Refuses(two))
    assert done == ["widget: issue #7 updated", "widget: issue not synced: gh issue: HTTP 403"]
    done = script.sync_issues([_clean_report(script)], "rokbenko/quackd", Refuses(two))
    assert done == ["widget: issue #7 closed", "widget: issue not synced: gh issue: HTTP 403"]


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
    script = _script()
    a_file = re.compile(r"^https://github\.com/[^/]+/[^/]+/blob/[0-9a-f]{40}/[^#]+")
    for module, _allowed, _prefixes in SUITE_ROWS:
        files, elsewhere = script.cited_of(module)
        assert files, module.__name__
        for cited in files:
            assert len(cited.sha) == 40 and cited.path and not cited.path.startswith("/"), cited
            assert cited.line is None or cited.line >= 1, cited
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
    module = importlib.import_module(module_name)
    assert getattr(module, attribute) == expected
    script = _script()
    if attribute == "BRANCH":
        repo = script._repo_of(module.REPO)
        assert script.declared_baseline(module, repo).name == expected
    else:
        for repo, branch in expected.items():
            baseline = script.declared_baseline(module, repo)
            assert (baseline.kind, baseline.name) == ("branch", branch)


@pytest.mark.parametrize(
    ("module_name", "prefix"),
    [
        ("quackd_microduck.upstream_api", "daemon-v"),
        ("quackd_huebot.upstream_api", "daemon-v"),
        ("quackd_toddlerbot.upstream_api", "v"),
        ("quackd_lerobot.upstream_api", ""),
        ("quackd_lerobot.policy.upstream_api", ""),
    ],
)
def test_a_pin_on_a_release_is_compared_with_the_newest_release_of_its_kind(
    module_name: str, prefix: str
) -> None:
    """A release-pinned module compared with `main` is red the morning after upstream's next
    commit and stays red: the Microduck's was, 238 commits behind a branch it never tracked."""
    module = importlib.import_module(module_name)
    script = _script()
    baseline = script.declared_baseline(module, script._repo_of(module.REPO))
    assert (baseline.kind, baseline.prefix) == ("tag", prefix)


def test_another_repository_a_module_cites_at_a_release_is_compared_with_its_newest() -> None:
    """The arm's refs cite rustypot, the library under LeRobot 0.7.0's bus, at its v1.13.1 tag.
    Compared with rustypot's default branch, `develop`, they would read as drift the day it
    moved a cited line, a release or none: the module names the release it read for each other
    repository, as `BRANCHES` names a branch, and a repository it names neither way is still
    compared with its default branch."""
    module = importlib.import_module("quackd_lerobot.upstream_api")
    script = _script()
    assert module.RELEASES == {"pollen-robotics/rustypot": "v1.13.1"}
    files, _elsewhere = script.cited_of(module)
    cited = {f.repo for f in files}
    assert cited == {"huggingface/lerobot", "pollen-robotics/rustypot"}, cited
    for cite in files:
        if cite.repo == "pollen-robotics/rustypot":
            assert cite.sha == module.RUSTYPOT_PIN, cite
    baseline = script.declared_baseline(module, "pollen-robotics/rustypot")
    assert (baseline.kind, baseline.prefix) == ("tag", "v")
    other = script.declared_baseline(module, "serialport/serialport-rs")
    assert other.kind == "default"
    widget = _module([], RELEASES={"someone/thing": "thing-v2.1"})
    assert script.declared_baseline(widget, "someone/thing").prefix == "thing-v"
    assert script.declared_baseline(widget, "someone/else").kind == "default"


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
    assert '--report "$GITHUB_STEP_SUMMARY"' in run
    assert "github.repository == 'rokbenko/quackd'" in run, "a fork's schedule must open no issue"
    assert "--issues rokbenko/quackd" in run
    env = next(step.get("env", {}) for step in steps if "upstream_drift.py" in step.get("run", ""))
    assert env == {"GITHUB_TOKEN": "${{ github.token }}"}


def test_the_toddlerbot_contract_checks_out_upstream_at_the_adapters_pin() -> None:
    """The contract job clones ToddlerBot and checks out a commit it spells itself, and the
    adapter's refs were read at its own `PIN`: nothing held the two together, so a re-pin that
    moved one of them would run the contract against code the refs were never read at."""
    module = importlib.import_module("quackd_toddlerbot.upstream_api")
    workflow = yaml.safe_load(TODDLERBOT_CONTRACT.read_text(encoding="utf-8"))
    (job,) = workflow["jobs"].values()
    runs = "\n".join(step.get("run", "") for step in job["steps"]).replace("\\\n", " ")
    clones = re.findall(r"git clone\b.*?(https://\S+?)(?:\.git)?(?=\s)", runs)
    checkouts = re.findall(r"git checkout\s+(\S+)", runs)
    assert clones == [module.REPO], clones
    assert checkouts == [module.PIN], checkouts


def test_the_script_is_type_checked_and_its_offline_mode_takes_no_network() -> None:
    import tomllib

    config = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert "scripts/upstream_drift.py" in config["tool"]["mypy"]["files"]
    source = SCRIPT.read_text(encoding="utf-8")
    assert "def offline_fetch(" in source
    assert source.count("urllib.request.urlopen(") == 1, "only web_fetch reaches the network"
