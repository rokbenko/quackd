"""Say when an upstream a quackd adapter was read against has moved: the nightly's script.

Every adapter cites what it reads from upstream as `UpstreamRef`s in its `upstream_api.py`,
each a permalink to a commit and, nearly always, a line (ADR-0022). Nothing re-read them when
upstream moved: LeRobot merged a new motor library on 2026-10-16, and the first thing here to
notice was a person reading the pull request three weeks later. This script fetches every cited
file at its pin and at the branch or tag the pin tracks, compares the cited lines, and says which
refs no longer point at what they were read as. `.github/workflows/upstream-drift.yml` runs it
every night and opens one issue per upstream that moved.

    uv run --no-sync python scripts/upstream_drift.py                 # every upstream, to stdout
    uv run --no-sync python scripts/upstream_drift.py --only lerobot  # one, by its doctor name
    uv run --no-sync python scripts/upstream_drift.py --offline DIR   # fixtures on disk, no network
    uv run --no-sync python scripts/upstream_drift.py --report out.md --json drift.json

What it compares each module against is the module's own declaration: `BRANCH` when the pin
sits on a branch that is not the default (`BRANCHES`, a mapping of `owner/repo` to a branch,
for the other repositories a module cites), the newest release tag when the module names the
version it read (`VERSION`, `HUB_VERSION`, `TAG`), and the repository's default branch
otherwise. A release-pinned module compared with `main` would be red by design, and a
branch-pinned one compared with `main` would be red by accident.

A cited line that still reads the same where it was is `same`; found elsewhere in the file it
is `moved`, which is reported and is not drift; otherwise it is `changed`. A file no longer at
the baseline is `gone`. A ref whose source has no line number is compared by the whole file's
hash, and a page (`.md`, a document with no extension) that changed is reported without
counting as drift, since prose moves for reasons that are nobody's concern. A pinned line that
is blank or only a bracket is a `bad anchor`: the ref cannot be followed and should point at
the identifying line.
A line-less ref whose path the raw host does not serve at the pin cites a directory, and is
reported as `no file at the pin` and left alone.

Exit 0 when nothing drifted, 1 when something did, 2 when something could not be checked (an
outage, a rate limit) and nothing drifted, so an outage never reads as drift.

It reaches github.com, raw.githubusercontent.com and pypi.org and nothing else, with the
standard library alone, and reads `GITHUB_TOKEN` for the API's rate limit when it is set.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Any, Protocol

REPO_ROOT = Path(__file__).resolve().parents[1]

API = "https://api.github.com"
RAW = "https://raw.githubusercontent.com"
PYPI = "https://pypi.org/pypi"

BLOB = re.compile(r"^https://github\.com/([^/]+/[^/]+)/blob/([0-9a-f]{40})/([^#]+?)(?:#L(\d+))?$")
RAW_FILE = re.compile(r"^https://raw\.githubusercontent\.com/([^/]+/[^/]+)/([0-9a-f]{40})/(.+)$")
RELEASE_TAG = re.compile(r"^v?(\d+)(?:\.(\d+))?(?:\.(\d+))?$")
PYPROJECT_VERSION = re.compile(r'^version\s*=\s*"([^"]+)"', re.M)

#: Lines a ref may not be anchored on: they name nothing, so a match says nothing.
BARE = {"", ")", "]", "}", "),", "],", "},", "):", '"""', "'''", "pass", "else:", "try:"}
#: Files whose change is reported but is not drift: prose moves for its own reasons.
PAGES = (".md", ".rst", ".txt", "")

USER_AGENT = "quackd-upstream-drift"
TIMEOUT_S = 30
RETRY_AFTER_S = 20


class Unreachable(RuntimeError):
    """A fetch that neither answered nor said 404: an outage, a rate limit, a refusal."""


class Fetch(Protocol):
    """One GET: the body, or None for a 404. Anything else raises `Unreachable`."""

    def __call__(self, url: str) -> bytes | None: ...


# ── fetching ────────────────────────────────────────────────────────────────────────────


def web_fetch(url: str) -> bytes | None:
    """The real thing: one retry on a rate limit or a server error, `GITHUB_TOKEN` when set."""
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    token = os.environ.get("GITHUB_TOKEN")
    if token and (url.startswith(API) or url.startswith(RAW)):
        request.add_header("Authorization", f"Bearer {token}")
    if url.startswith(API):
        request.add_header("Accept", "application/vnd.github+json")
    for attempt in (1, 2):
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:
                return bytes(response.read())
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            if attempt == 1 and e.code in (429, 500, 502, 503, 504):
                wait = e.headers.get("Retry-After")
                time.sleep(float(wait) if wait and wait.isdigit() else RETRY_AFTER_S)
                continue
            raise Unreachable(f"{url}: HTTP {e.code}") from e
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise Unreachable(f"{url}: {e}") from e
    raise Unreachable(f"{url}: gave up after a retry")


def offline_fetch(root: Path) -> Fetch:
    """Fixtures on disk in place of the network, laid out by what each URL is:
    `raw/<owner>/<repo>/<sha>/<path>` for a file, `api/<owner>/<repo>/repo.json`,
    `api/<owner>/<repo>/commits/<ref>.json`, `api/<owner>/<repo>/tags.json`,
    `pypi/<project>.json`, and `url/<sha256 of the url, 16 hex>` for anything else."""

    def fetch(url: str) -> bytes | None:
        path = _offline_path(root, url)
        return path.read_bytes() if path.is_file() else None

    return fetch


def _offline_path(root: Path, url: str) -> Path:
    if url.startswith(RAW + "/"):
        return root / "raw" / url[len(RAW) + 1 :]
    if url.startswith(API + "/repos/"):
        rest = url[len(API) + len("/repos/") :]
        owner, repo, *tail = rest.split("/")
        if not tail:
            return root / "api" / owner / repo / "repo.json"
        if tail[0] == "commits" and len(tail) == 2:
            return root / "api" / owner / repo / "commits" / f"{tail[1]}.json"
        if tail[0].startswith("tags"):
            return root / "api" / owner / repo / "tags.json"
    if url.startswith(PYPI + "/"):
        return root / "pypi" / f"{url[len(PYPI) + 1 :].split('/')[0]}.json"
    return root / "url" / hashlib.sha256(url.encode()).hexdigest()[:16]


class Cache:
    """Every URL fetched once per run, whichever modules cite it: huebot's refs are the
    Microduck's files, and a file cited forty times is one request."""

    def __init__(self, fetch: Fetch) -> None:
        self._fetch = fetch
        self._seen: dict[str, bytes | None] = {}
        self.requests = 0

    def get(self, url: str) -> bytes | None:
        if url not in self._seen:
            self.requests += 1
            self._seen[url] = self._fetch(url)
        return self._seen[url]

    def json(self, url: str) -> Any:
        body = self.get(url)
        if body is None:
            return None
        try:
            return json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as e:
            raise Unreachable(f"{url}: not JSON: {e}") from e


# ── what a module declares ──────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Cited:
    """One ref's source, read as a file in a repository."""

    ref: str
    repo: str
    sha: str
    path: str
    line: int | None

    @property
    def key(self) -> tuple[str, str, str, int | None]:
        return (self.repo, self.sha, self.path, self.line)


@dataclass
class Baseline:
    """What a module's pin is compared against: the branch or tag it tracks, resolved."""

    kind: str
    """`branch`, `tag` or `default`."""
    name: str
    """The branch or tag name, once resolved; `?` until the API answered."""
    sha: str = ""


@dataclass
class Row:
    ref: str
    cited: str
    status: str
    now: str = ""


@dataclass
class Report:
    """One upstream module's night."""

    name: str
    module: str
    pin: str
    read_on: str
    baselines: dict[str, Baseline] = field(default_factory=dict)
    counts: dict[str, int] = field(default_factory=dict)
    rows: list[Row] = field(default_factory=list)
    versions: list[str] = field(default_factory=list)
    unchecked: list[str] = field(default_factory=list)
    drift: bool = False

    def count(self, status: str) -> None:
        self.counts[status] = self.counts.get(status, 0) + 1


def cited_of(module: ModuleType) -> tuple[list[Cited], list[str]]:
    """Every ref of the module read as a GitHub file, and the names of those that are not one
    (a maker's page, a Hugging Face document, an OpenAPI file), which are never fetched."""
    files: list[Cited] = []
    elsewhere: list[str] = []
    for ref in module.all_refs():
        source = str(ref.source)
        if match := BLOB.match(source):
            repo, sha, path, line = match.groups()
            files.append(Cited(ref.name, repo, sha, path, int(line) if line else None))
        elif match := RAW_FILE.match(source):
            repo, sha, path = match.groups()
            files.append(Cited(ref.name, repo, sha, path, None))
        else:
            elsewhere.append(ref.name)
    return files, elsewhere


def declared_baseline(module: ModuleType, repo: str) -> Baseline:
    """How this module says a repository it cites should be compared: `BRANCHES` for any of
    them, `BRANCH` or a version for its primary one, the default branch for the rest."""
    branches = getattr(module, "BRANCHES", None)
    if isinstance(branches, dict) and isinstance(branches.get(repo), str):
        return Baseline("branch", str(branches[repo]))
    primary = _repo_of(str(getattr(module, "REPO", "") or getattr(module, "HUB_REPO", "")))
    if repo != primary:
        return Baseline("default", "?")
    branch = getattr(module, "BRANCH", None)
    if isinstance(branch, str) and branch:
        return Baseline("branch", branch)
    versioned = ("TAG", "VERSION", "HUB_VERSION")
    if any(isinstance(getattr(module, name, None), str) for name in versioned):
        return Baseline("tag", "?")
    return Baseline("default", "?")


def _repo_of(url: str) -> str:
    match = re.match(r"^https://github\.com/([^/]+/[^/]+?)(?:\.git)?/?$", url)
    return match.group(1) if match else ""


# ── the comparison ──────────────────────────────────────────────────────────────────────


def resolve(cache: Cache, repo: str, baseline: Baseline) -> Baseline:
    """The baseline's name and head sha, asked of the API once per repository."""
    if baseline.kind == "default":
        meta = cache.json(f"{API}/repos/{repo}")
        if not isinstance(meta, dict) or not meta.get("default_branch"):
            raise Unreachable(f"{repo}: the API did not name a default branch")
        baseline = Baseline("default", str(meta["default_branch"]))
    elif baseline.kind == "tag":
        tags = cache.json(f"{API}/repos/{repo}/tags?per_page=100")
        newest = newest_release(tags if isinstance(tags, list) else [])
        if newest is None:
            raise Unreachable(f"{repo}: the API listed no release tag")
        baseline = Baseline("tag", newest)
    head = cache.json(f"{API}/repos/{repo}/commits/{baseline.name}")
    if not isinstance(head, dict) or not head.get("sha"):
        raise Unreachable(f"{repo}: {baseline.name} did not resolve to a commit")
    baseline.sha = str(head["sha"])
    return baseline


def newest_release(tags: Iterable[Any]) -> str | None:
    """The highest plain version tag, `v1.2.3` or `1.2.3`; pre-releases and anything with a
    suffix are passed over, since a pin tracks releases."""
    best: tuple[tuple[int, int, int], str] | None = None
    for tag in tags:
        name = str(tag.get("name", "")) if isinstance(tag, dict) else str(tag)
        match = RELEASE_TAG.match(name)
        if not match:
            continue
        parts = tuple(int(group or 0) for group in match.groups())
        version = (parts[0], parts[1], parts[2])
        if best is None or version > best[0]:
            best = (version, name)
    return best[1] if best else None


def text_of(body: bytes) -> list[str] | None:
    """The file's lines, or None for a binary one (a NUL in its first 8 KiB, or not UTF-8)."""
    if b"\x00" in body[:8192]:
        return None
    try:
        return body.decode("utf-8").split("\n")
    except UnicodeDecodeError:
        return None


def is_page(path: str) -> bool:
    return Path(path).suffix.lower() in PAGES


def compare(cited: Cited, pinned: bytes | None, current: bytes | None, head_sha: str) -> Row:
    """One ref against its baseline, as a row: what was cited, what it is now."""
    where = f"{cited.path}:{cited.line}" if cited.line else cited.path
    if pinned is None:
        # a line-less ref whose path answers nothing at the pin cites a directory, which the
        # repository has and the raw host does not serve; a lined one cites a file that should
        # be there, and is not
        status = "pin unreachable" if cited.line else "no file at the pin"
        return Row(cited.ref, where, status)
    if current is None:
        return Row(cited.ref, where, "gone")
    pinned_lines = text_of(pinned)
    current_lines = text_of(current)
    if cited.line is None or pinned_lines is None or current_lines is None:
        same = hashlib.sha256(pinned).digest() == hashlib.sha256(current).digest()
        if same:
            return Row(cited.ref, where, "same")
        if pinned_lines is None or current_lines is None:
            return Row(cited.ref, where, "file changed")
        return Row(cited.ref, where, "page changed" if is_page(cited.path) else "file changed")
    if cited.line > len(pinned_lines):
        return Row(cited.ref, where, "bad anchor", "past the end of the file at the pin")
    wanted = pinned_lines[cited.line - 1].strip()
    if wanted in BARE or len(wanted) <= 2:
        return Row(cited.ref, where, "bad anchor", f"the line reads {wanted!r}")
    if cited.line <= len(current_lines) and current_lines[cited.line - 1].strip() == wanted:
        return Row(cited.ref, where, "same")
    found = [number for number, line in enumerate(current_lines, start=1) if line.strip() == wanted]
    if len(found) == 1:
        return Row(cited.ref, where, "moved", f"line {found[0]} at {head_sha[:7]}")
    if found:
        # the same words in several places are no evidence the cited one survived: a ref that
        # reads `return self._bus` could be any method's, and a re-anchor on a line of its own
        # is what settles it
        places = ", ".join(str(number) for number in found[:6])
        return Row(
            cited.ref, where, "changed", f"reads the same on lines {places} at {head_sha[:7]}"
        )
    return Row(cited.ref, where, "changed", f"no line reads {wanted[:60]!r} at {head_sha[:7]}")


DRIFT = {"changed", "gone", "file changed"}


def check_module(
    name: str, module: ModuleType, cache: Cache, seen: dict[tuple[str, str, str, int | None], str]
) -> Report:
    """Every ref of one module against its baseline, with the versions it names checked too.

    `seen` is the refs earlier modules already judged, keyed by file and line, so a module whose
    refs are another's (Huebot reads the Microduck's protocol at the Microduck's pin) reports
    them once, under the module that owns them."""
    report = Report(
        name=name,
        module=module.__name__,
        pin=str(getattr(module, "PIN", ""))[:7],
        read_on=str(getattr(module, "READ_ON", "")),
    )
    files, elsewhere = cited_of(module)
    report.counts["not a file"] = len(elsewhere)
    for cited in files:
        owner = seen.get(cited.key)
        if owner is not None and owner != name:
            report.count("shared")
            continue
        seen.setdefault(cited.key, name)
        try:
            baseline = report.baselines.get(cited.repo)
            if baseline is None:
                baseline = resolve(cache, cited.repo, declared_baseline(module, cited.repo))
                report.baselines[cited.repo] = baseline
            pinned = cache.get(f"{RAW}/{cited.repo}/{cited.sha}/{cited.path}")
            current = cache.get(f"{RAW}/{cited.repo}/{baseline.sha}/{cited.path}")
        except Unreachable as e:
            report.unchecked.append(f"{cited.ref}: {e}")
            report.count("unchecked")
            continue
        row = compare(cited, pinned, current, baseline.sha)
        report.count(row.status)
        if row.status != "same":
            report.rows.append(row)
        if row.status in DRIFT:
            report.drift = True
    try:
        for line, moved in check_versions(module, cache, report.baselines):
            report.versions.append(line)
            report.drift = report.drift or moved
    except Unreachable as e:
        report.unchecked.append(f"versions: {e}")
    return report


def check_versions(
    module: ModuleType, cache: Cache, baselines: dict[str, Baseline]
) -> list[tuple[str, bool]]:
    """The versions a module wrote down against what is published today: the package on PyPI
    where the module names the one it read, and upstream's own `pyproject.toml` at the baseline
    where the module recorded the version at its pin. Each answer is a line and whether it is
    drift. Newer on PyPI is drift: it is the release that lands on a fresh install."""
    lines: list[tuple[str, bool]] = []
    package = getattr(module, "PACKAGE", None)
    pypi_name = str(getattr(package, "name", "") or "").strip()
    read = getattr(module, "PYPI_VERSION_READ", None) or getattr(module, "VERSION", None)
    if pypi_name and isinstance(read, str):
        published = cache.json(f"{PYPI}/{pypi_name}/json")
        latest = str(published["info"]["version"]) if isinstance(published, dict) else ""
        if latest and latest != read:
            lines.append((f"PyPI {pypi_name} is {latest}; quackd read {read}", True))
        elif latest:
            lines.append((f"PyPI {pypi_name} is still {read}", False))
    hub = getattr(module, "HUB_VERSION", None)
    if isinstance(hub, str):
        published = cache.json(f"{PYPI}/huggingface_hub/json")
        latest = str(published["info"]["version"]) if isinstance(published, dict) else ""
        if latest and latest != hub:
            lines.append((f"PyPI huggingface_hub is {latest}; quackd read {hub}", True))
        elif latest:
            lines.append((f"PyPI huggingface_hub is still {hub}", False))
    at_pin = getattr(module, "VERSION_AT_PIN", None)
    primary = _repo_of(str(getattr(module, "REPO", "")))
    baseline = baselines.get(primary)
    if at_pin is not None and baseline is not None:
        body = cache.get(f"{RAW}/{primary}/{baseline.sha}/pyproject.toml")
        text = body.decode("utf-8", "replace") if body else ""
        match = PYPROJECT_VERSION.search(text)
        now = match.group(1) if match else ""
        was = str(getattr(at_pin, "name", at_pin))
        if now and now != was:
            lines.append((f"pyproject.toml says {now} at {baseline.name}; {was} at the pin", True))
        elif now:
            lines.append((f"pyproject.toml still says {was} at {baseline.name}", False))
    api_doc = getattr(module, "API_DOC", None)
    api_version = getattr(module, "API_VERSION", None)
    if isinstance(api_doc, str) and isinstance(api_version, str):
        document = cache.json(api_doc)
        now = str(document.get("info", {}).get("version", "")) if isinstance(document, dict) else ""
        if now and now != api_version:
            lines.append((f"{api_doc} is version {now}; quackd read {api_version}", True))
        elif now:
            lines.append((f"{api_doc} is still version {api_version}", False))
    return lines


# ── the run ─────────────────────────────────────────────────────────────────────────────


def upstream_rows() -> list[tuple[str, ModuleType]]:
    """(doctor's name, module) for every upstream an installed adapter declares, each module
    once: the walk `quackd doctor` makes, so a module doctor does not know is one this script
    does not read either, and a test holds that walk to the suite's list."""
    from quackd.doctor import _upstreams

    rows: list[tuple[str, ModuleType]] = []
    names: set[str] = set()
    for name, module, _doc, _never in _upstreams():
        if module.__name__ in names:
            continue
        names.add(module.__name__)
        rows.append((name, module))
    return rows


def run(rows: Sequence[tuple[str, ModuleType]], fetch: Fetch) -> list[Report]:
    cache = Cache(fetch)
    seen: dict[tuple[str, str, str, int | None], str] = {}
    return [check_module(name, module, cache, seen) for name, module in rows]


def summary_line(report: Report) -> str:
    order = (
        "same",
        "moved",
        "changed",
        "gone",
        "file changed",
        "page changed",
        "bad anchor",
        "pin unreachable",
        "no file at the pin",
        "unchecked",
        "shared",
        "not a file",
    )
    counts = ", ".join(f"{report.counts[k]} {k}" for k in order if report.counts.get(k))
    against = "; ".join(
        f"{repo} {b.kind} {b.name} at {b.sha[:7] or '?'}" for repo, b in report.baselines.items()
    )
    return f"pin {report.pin} read {report.read_on} vs {against or 'nothing resolved'}: {counts}"


def markdown(reports: Sequence[Report]) -> str:
    """The report a person reads: one section per upstream, its rows, its versions."""
    out: list[str] = []
    drifted = [r.name for r in reports if r.drift]
    out.append(
        "# Upstream drift\n\n"
        + (f"Drifted: {', '.join(drifted)}." if drifted else "Nothing drifted.")
        + " A line that moved within its file is not drift; a page that changed is not drift."
    )
    for report in reports:
        out.append(f"\n## {report.name} (`{report.module}`)\n\n{summary_line(report)}")
        for line in report.versions:
            out.append(f"- {line}")
        for line in report.unchecked:
            out.append(f"- could not check {line}")
        if report.rows:
            out.append("\n| ref | cited | status | now |\n| --- | --- | --- | --- |")
            for row in report.rows:
                out.append(f"| `{row.ref}` | `{row.cited}` | {row.status} | {row.now} |")
    return "\n".join(out) + "\n"


def as_json(reports: Sequence[Report]) -> dict[str, Any]:
    return {
        "drifted": [r.name for r in reports if r.drift],
        "unchecked": [r.name for r in reports if r.unchecked],
        "upstreams": [
            {
                "name": r.name,
                "module": r.module,
                "pin": r.pin,
                "read_on": r.read_on,
                "baselines": {
                    repo: {"kind": b.kind, "name": b.name, "sha": b.sha}
                    for repo, b in r.baselines.items()
                },
                "counts": r.counts,
                "drift": r.drift,
                "versions": r.versions,
                "unchecked": r.unchecked,
                "rows": [vars(row) for row in r.rows],
                "summary": summary_line(r),
            }
            for r in reports
        ],
    }


def exit_code(reports: Sequence[Report]) -> int:
    if any(r.drift for r in reports):
        return 1
    if any(r.unchecked for r in reports):
        return 2
    return 0


# ── issues, one per upstream that moved ─────────────────────────────────────────────────

LABEL = "upstream-drift"
Gh = Callable[[Sequence[str]], str]


def gh_run(args: Sequence[str]) -> str:
    """`gh` with these arguments, its stdout; a non-zero exit is an `Unreachable`."""
    result = subprocess.run(["gh", *args], capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise Unreachable(f"gh {' '.join(args)}: {result.stderr.strip()}")
    return result.stdout


def sync_issues(reports: Sequence[Report], repo: str, gh: Gh = gh_run) -> list[str]:
    """One open issue per upstream that drifted, titled `upstream drift: <name>`, its body the
    upstream's section of the report, edited in place while the drift lasts and closed the
    night it is gone. A comment is added only when the set of drifted refs changed, so a
    watcher is not pinged every night with the same table. Returns what was done, one line
    per upstream."""
    done: list[str] = []
    gh(
        [
            "label",
            "create",
            LABEL,
            "--repo",
            repo,
            "--force",
            "--color",
            "D93F0B",
            "--description",
            "an upstream a quackd adapter was read against has moved",
        ]
    )
    for report in reports:
        title = f"upstream drift: {report.name}"
        listed = gh(
            [
                "issue",
                "list",
                "--repo",
                repo,
                "--label",
                LABEL,
                "--state",
                "open",
                "--search",
                f'"{title}" in:title',
                "--json",
                "number,title,body",
            ]
        )
        issues = [i for i in json.loads(listed or "[]") if i.get("title") == title]
        section = markdown([report]).split("\n", 2)[2]
        if report.drift:
            body = (
                f"{section}\n\nOpened by `.github/workflows/upstream-drift.yml`. Re-read the "
                f"refs above at the new baseline, move each pin and `READ_ON` in "
                f"`{report.module}`, and the issue closes itself the night after."
            )
            if issues:
                number = str(issues[0]["number"])
                before = _drifted_refs(str(issues[0].get("body", "")))
                gh(["issue", "edit", number, "--repo", repo, "--body", body])
                if before != _drifted_refs(body):
                    gh(
                        [
                            "issue",
                            "comment",
                            number,
                            "--repo",
                            repo,
                            "--body",
                            "The set of drifted refs changed tonight; the body above is current.",
                        ]
                    )
                done.append(f"{report.name}: issue #{number} updated")
            else:
                created = gh(
                    [
                        "issue",
                        "create",
                        "--repo",
                        repo,
                        "--title",
                        title,
                        "--label",
                        LABEL,
                        "--body",
                        body,
                    ]
                )
                done.append(f"{report.name}: issue opened ({created.strip().split()[-1]})")
        elif issues:
            number = str(issues[0]["number"])
            gh(
                [
                    "issue",
                    "close",
                    number,
                    "--repo",
                    repo,
                    "--comment",
                    "Nothing drifted tonight: every ref reads as it was read, or has moved within "
                    "its file.",
                ]
            )
            done.append(f"{report.name}: issue #{number} closed")
    return done


_DRIFTED_ROW = re.compile(r"^\| `([^`]+)` \| `[^`]*` \| (?:changed|gone|file changed) \|", re.M)


def _drifted_refs(body: str) -> set[str]:
    return set(_DRIFTED_ROW.findall(body))


# ── the command ─────────────────────────────────────────────────────────────────────────


def main(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--only", metavar="NAME", help="one upstream, by the name doctor prints")
    parser.add_argument("--offline", metavar="DIR", type=Path, help="fixtures on disk, no network")
    parser.add_argument("--report", metavar="PATH", type=Path, help="write the Markdown report")
    parser.add_argument("--json", metavar="PATH", type=Path, help="write the findings as JSON")
    parser.add_argument(
        "--issues",
        metavar="OWNER/REPO",
        help="open, update and close one issue per upstream in this repository, through gh",
    )
    args = parser.parse_args(argv)

    rows = upstream_rows()
    if args.only:
        rows = [(name, module) for name, module in rows if name == args.only]
        if not rows:
            print(f"no upstream is called {args.only!r}; doctor names them")
            return 2
    fetch: Fetch = offline_fetch(args.offline) if args.offline else web_fetch
    reports = run(rows, fetch)
    text = markdown(reports)
    if args.report:
        args.report.write_text(text, encoding="utf-8", newline="\n")
    if args.json:
        args.json.write_text(json.dumps(as_json(reports), indent=2), encoding="utf-8", newline="\n")
    print(text, end="")
    code = exit_code(reports)
    if args.issues:
        try:
            for line in sync_issues(reports, args.issues):
                print(line)
        except Unreachable as e:
            print(f"issues not synced: {e}")
            code = code or 2
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
