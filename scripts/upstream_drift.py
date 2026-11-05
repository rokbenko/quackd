"""Say when an upstream a quackd adapter was read against has moved: the nightly's script.

Every adapter cites what it reads from upstream as `UpstreamRef`s in its `upstream_api.py`,
each a permalink to a commit and, nearly always, a line (ADR-0022). Nothing re-read them when
upstream moved: LeRobot merged a new motor library on 2026-10-16, and the first thing here to
notice was a person reading the pull request three weeks later. This script fetches every cited
file at its pin and at the branch or tag the pin tracks, compares the cited lines, and says which
refs no longer point at what they were read as. `.github/workflows/upstream-drift.yml` runs it
every night and keeps one issue open per upstream that needs somebody.

    uv run --no-sync python scripts/upstream_drift.py                 # every upstream, to stdout
    uv run --no-sync python scripts/upstream_drift.py --only lerobot  # one, by its doctor name
    uv run --no-sync python scripts/upstream_drift.py --offline DIR   # fixtures on disk, no network
    uv run --no-sync python scripts/upstream_drift.py --report out.md --json drift.json

What it compares each module against is the module's own declaration: `BRANCH` when the pin
sits on a branch that is not the default (`BRANCHES`, a mapping of `owner/repo` to a branch,
for the other repositories a module cites), the newest release tag when the module names the
release it read (`TAG`, `RELEASE`, `VERSION`, `HUB_VERSION`), and the repository's default
branch otherwise. A tag with words before its number, `daemon-v0.16.1`, is compared with the
newest tag that starts with the same words. A release-pinned module compared with `main` would
be red by design, and a branch-pinned one compared with `main` would be red by accident.

Three things can be wrong, and they are told apart because each asks something different of
whoever reads the report.

**Drift** is upstream having moved. A cited line that still reads the same where it was is
`same`; found once elsewhere in the file it is `moved`, which is reported and is not drift;
otherwise it is `changed`. A file or a directory no longer at the baseline is `gone`, and so is
a baseline itself, a branch or a repository deleted or a release tag no longer there, which is
`baseline gone`. A ref whose source has no line number is compared by the whole file's hash, and
a page (`.md`, `.rst`, `.txt`) that changed is reported without counting as drift, since prose
moves for reasons that are nobody's concern. A newer release on PyPI than the one a module read
is drift too.

**A broken ref** is one of quackd's own that cannot be followed: its line is blank or only a
bracket (`bad anchor`), or nothing is at its path at the pin (`nothing at the pin`, asked of the
API as well as the raw host, so a refusal by one of them is not read as an absence). That is a
defect here, not a move there.

**Unchecked** is a night something did not answer as it should: an outage, a rate limit, or a
404 from a page a module's version is read off. A URL that failed is not asked again that night,
and a host that has cost three slow failures running, with nothing answering between them, is
asked nothing more.

A cited line that reads like others in its own file at the pin is a `weak anchor`: it compares
as `same` while it stays on its line number, but once it leaves it cannot be followed and reads
as `changed`. Those are listed under each upstream for whoever re-reads it next.

Exit 0 when every ref reads as it was read, 1 when something drifted or a ref is broken, which
is work for somebody in this repository, and 2 when nothing did and something could not be
checked, so an outage never reads as drift and never closes an issue.

It reaches api.github.com, raw.githubusercontent.com and pypi.org, and for a module that names
an API document, that document's own address, with the standard library alone. `GITHUB_TOKEN`,
when set, is sent to the API for its rate limit and to nothing else.
"""

from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
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

BLOB = re.compile(
    r"^https://github\.com/([^/]+/[^/]+)/blob/([0-9a-f]{40})/([^#?]+)(?:\?[^#]*)?(?:#L(\d+))?$"
)
RAW_FILE = re.compile(r"^https://raw\.githubusercontent\.com/([^/]+/[^/]+)/([0-9a-f]{40})/(.+)$")
VERSION_TAIL = re.compile(r"^(.*?)\d+(?:\.\d+){0,2}$")
PYPROJECT_VERSION = re.compile(r'^version\s*=\s*"([^"]+)"', re.M)
_HOST = re.compile(r"^[a-z]+://([^/]+)")

#: Lines a ref may not be anchored on: they name nothing, so a match says nothing.
BARE = {"", ")", "]", "}", "),", "],", "},", "):", '"""', "'''", "pass", "else:", "try:"}
#: Files whose change is reported but is not drift: prose moves for its own reasons. A file
#: with no extension is not one of them: it is a LICENSE, a Dockerfile, a Makefile.
PAGES = (".md", ".rst", ".txt")

USER_AGENT = "quackd-upstream-drift"
TIMEOUT_S = 30
RETRY_AFTER_S = 20
RETRY_AFTER_MAX_S = 60
"""The longest a `Retry-After` is waited out: the job has fifteen minutes and writes its report
at the end, so a host asking for an hour is a host that did not answer tonight."""
HOST_FAILURES = 3
"""How many slow failures running a host may cost before nothing more is asked of it that night.
An outage with a retry and a wait on every URL is two hours of waiting for three hundred files,
and the job that runs this has fifteen minutes. A refusal that answers at once costs nothing, so
it does not count, and anything answering anywhere starts the count again."""
TAG_PAGES = 5
"""How many pages of a hundred tags are read to find the newest release."""
NAMED = 5
"""How many refs a line of the report names before it counts the rest."""


class Unreachable(RuntimeError):
    """A fetch that neither answered nor said 404: an outage, a rate limit, a refusal. `slow` says
    it cost a timeout or a retry's wait, which is what `Cache` counts against a host."""

    def __init__(self, message: str, *, slow: bool = False) -> None:
        super().__init__(message)
        self.slow = slow


class BaselineGone(Exception):
    """What a module's pin tracks answers 404 or lists nothing: a branch or a repository deleted,
    or no release tag of its kind left. That is upstream having moved and not an outage."""


class Fetch(Protocol):
    """One GET: the body, or None for a 404. Anything else raises `Unreachable`."""

    def __call__(self, url: str) -> bytes | None: ...


# ── fetching ────────────────────────────────────────────────────────────────────────────


def web_fetch(url: str) -> bytes | None:
    """The real thing: one retry on a rate limit or a server error. `GITHUB_TOKEN`, when set,
    goes to the API and nowhere else: the raw host answers a token it does not like with a 404,
    which here would read as a file that is not there."""
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    token = os.environ.get("GITHUB_TOKEN")
    if url.startswith(API):
        request.add_header("Accept", "application/vnd.github+json")
        if token:
            request.add_header("Authorization", f"Bearer {token}")
    for attempt in (1, 2):
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:
                return bytes(response.read())
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            if e.code not in (429, 500, 502, 503, 504):
                raise Unreachable(f"{_host(url)}: HTTP {e.code}") from e
            wait = e.headers.get("Retry-After")
            asked = float(wait) if wait and wait.isdigit() else RETRY_AFTER_S
            if attempt == 2 or asked > RETRY_AFTER_MAX_S:
                raise Unreachable(f"{_host(url)}: HTTP {e.code}", slow=True) from e
            time.sleep(asked)
        except (urllib.error.URLError, TimeoutError, OSError, http.client.HTTPException) as e:
            raise Unreachable(f"{_host(url)}: {e}", slow=True) from e
    raise Unreachable(f"{_host(url)}: gave up after a retry", slow=True)


def _host(url: str) -> str:
    match = _HOST.match(url)
    return match.group(1) if match else url


def offline_fetch(root: Path) -> Fetch:
    """Fixtures on disk in place of the network, laid out by what each URL is:
    `raw/<owner>/<repo>/<sha>/<path>` for a file, `api/<owner>/<repo>/repo.json`,
    `api/<owner>/<repo>/commits/<ref>.json`, `api/<owner>/<repo>/tags.json` (`tags.2.json` for
    the second page), `api/<owner>/<repo>/contents/<sha>/<path>.json`, `pypi/<project>.json`,
    and `url/<sha256 of the url, 16 hex>` for anything else."""

    def fetch(url: str) -> bytes | None:
        path = _offline_path(root, url)
        return path.read_bytes() if path.is_file() else None

    return fetch


def _offline_path(root: Path, url: str) -> Path:
    if url.startswith(RAW + "/"):
        return root / "raw" / urllib.parse.unquote(url[len(RAW) + 1 :])
    if url.startswith(API + "/repos/"):
        rest = url[len(API) + len("/repos/") :]
        owner, repo, *tail = rest.split("/")
        base = root / "api" / owner / repo
        if not tail:
            return base / "repo.json"
        if tail[0] == "commits" and len(tail) == 2:
            return base / "commits" / f"{tail[1]}.json"
        if tail[0].startswith("tags"):
            page = re.search(r"[?&]page=(\d+)", tail[0])
            return base / (f"tags.{page.group(1)}.json" if page else "tags.json")
        if tail[0] == "contents":
            path, _, ref = "/".join(tail[1:]).partition("?ref=")
            return base / "contents" / ref / f"{urllib.parse.unquote(path)}.json"
    if url.startswith(PYPI + "/"):
        return root / "pypi" / f"{url[len(PYPI) + 1 :].split('/')[0]}.json"
    return root / "url" / hashlib.sha256(url.encode()).hexdigest()[:16]


class Cache:
    """Every URL asked once per run, whichever modules cite it, and whatever it answered:
    huebot's refs are the Microduck's files, a file cited forty times is one request, and a
    URL that failed is not asked forty times more. A host that costs `HOST_FAILURES` slow
    failures running, with nothing answering anywhere between them, is asked nothing else that
    night, and the refs it holds up say what the last of them was."""

    def __init__(self, fetch: Fetch) -> None:
        self._fetch = fetch
        self._seen: dict[str, bytes | None] = {}
        self._failed: dict[str, Unreachable] = {}
        self._strikes: dict[str, tuple[int, str]] = {}
        self.requests = 0

    def get(self, url: str) -> bytes | None:
        if url in self._seen:
            return self._seen[url]
        if url in self._failed:
            raise self._failed[url]
        host = _host(url)
        strikes, last = self._strikes.get(host, (0, ""))
        if strikes >= HOST_FAILURES:
            raise Unreachable(
                f"{host}: {HOST_FAILURES} slow failures running, so nothing more was asked of it; "
                f"the last was {last}"
            )
        self.requests += 1
        try:
            body = self._fetch(url)
        except Unreachable as e:
            self._failed[url] = e
            if e.slow:
                self._strikes[host] = (strikes + 1, str(e))
            raise
        self._strikes.clear()
        self._seen[url] = body
        return body

    def json(self, url: str) -> Any:
        body = self.get(url)
        if body is None:
            return None
        try:
            return json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as e:
            raise Unreachable(f"{_host(url)}: not JSON: {e}") from e


def raw_url(repo: str, sha: str, path: str) -> str:
    return f"{RAW}/{repo}/{sha}/{_quoted(path)}"


def contents_url(repo: str, path: str, sha: str) -> str:
    return f"{API}/repos/{repo}/contents/{_quoted(path)}?ref={sha}"


def _quoted(path: str) -> str:
    """A path from a permalink, as a URL carries it: a space in a file's name would otherwise
    raise out of `urllib`, and a path the permalink already quoted is not quoted twice."""
    return urllib.parse.quote(urllib.parse.unquote(path), safe="/")


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
    prefix: str = ""
    """For a tag: the words before its number, `daemon-v` of `daemon-v0.16.1`, so the newest
    release is looked for among the tags that start the same way."""


@dataclass
class Row:
    ref: str
    cited: str
    status: str
    now: str = ""
    weak: bool = False
    """The cited line reads like others in its own file at the pin."""


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
    weak: list[Row] = field(default_factory=list)
    """Refs whose line still reads the same and is not the only one that does."""
    versions: list[tuple[str, bool]] = field(default_factory=list)
    """Each version the module wrote down against what is published, and whether it moved."""
    blocked: dict[str, list[str]] = field(default_factory=dict)
    """What could not be checked, by cause, with the refs each cause held up."""
    drift: bool = False
    """Upstream moved under a ref, or published a newer release than the one read."""
    broken: bool = False
    """A ref of quackd's own cannot be followed: a bad anchor, or nothing at its pin."""

    def count(self, status: str) -> None:
        self.counts[status] = self.counts.get(status, 0) + 1

    def block(self, cause: str, ref: str) -> None:
        self.blocked.setdefault(cause, []).append(ref)
        self.count("unchecked")

    @property
    def unchecked(self) -> list[str]:
        """One line per cause, naming the first few refs it held up."""
        return [f"{cause} ({_named(refs)})" for cause, refs in self.blocked.items()]

    @property
    def needs_somebody(self) -> bool:
        return self.drift or self.broken


def _named(refs: Sequence[str]) -> str:
    shown = ", ".join(refs[:NAMED])
    return shown if len(refs) <= NAMED else f"{shown} and {len(refs) - NAMED} more"


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
    them, `BRANCH` or a release for its primary one, the default branch for the rest."""
    branches = getattr(module, "BRANCHES", None)
    if isinstance(branches, dict) and isinstance(branches.get(repo), str):
        return Baseline("branch", str(branches[repo]))
    primary = _repo_of(str(getattr(module, "REPO", "") or getattr(module, "HUB_REPO", "")))
    if repo != primary:
        return Baseline("default", "?")
    branch = getattr(module, "BRANCH", None)
    if isinstance(branch, str) and branch:
        return Baseline("branch", branch)
    for name in ("TAG", "RELEASE", "VERSION", "HUB_VERSION"):
        release = getattr(module, name, None)
        if isinstance(release, str) and (match := VERSION_TAIL.match(release)):
            return Baseline("tag", "?", prefix=match.group(1))
    return Baseline("default", "?")


def _repo_of(url: str) -> str:
    match = re.match(r"^https://github\.com/([^/]+/[^/]+?)(?:\.git)?/?$", url)
    return match.group(1) if match else ""


# ── the comparison ──────────────────────────────────────────────────────────────────────


def resolve(cache: Cache, repo: str, baseline: Baseline) -> Baseline:
    """The baseline's name and head sha, asked of the API once per repository."""
    if baseline.kind == "default":
        meta = cache.json(f"{API}/repos/{repo}")
        if meta is None:
            raise BaselineGone(f"{repo} answers 404: the repository is gone or private")
        if not isinstance(meta, dict) or not meta.get("default_branch"):
            raise Unreachable(f"{repo}: the API did not name a default branch")
        baseline = Baseline("default", str(meta["default_branch"]))
    elif baseline.kind == "tag":
        newest = newest_release(_tags(cache, repo), baseline.prefix)
        if newest is None:
            what = f"starting {baseline.prefix!r}" if baseline.prefix else "with a plain version"
            raise BaselineGone(f"{repo} lists no release tag {what}")
        baseline = Baseline("tag", newest, prefix=baseline.prefix)
    head = cache.json(f"{API}/repos/{repo}/commits/{baseline.name}")
    if head is None:
        raise BaselineGone(f"{repo} has no {baseline.kind} called {baseline.name} any more")
    if not isinstance(head, dict) or not head.get("sha"):
        raise Unreachable(f"{repo}: {baseline.name} did not resolve to a commit")
    baseline.sha = str(head["sha"])
    return baseline


def _tags(cache: Cache, repo: str) -> list[Any]:
    """The repository's tags, a hundred to a page, until a page comes back short."""
    tags: list[Any] = []
    for page in range(1, TAG_PAGES + 1):
        suffix = "" if page == 1 else f"&page={page}"
        listed = cache.json(f"{API}/repos/{repo}/tags?per_page=100{suffix}")
        if not isinstance(listed, list):
            break
        tags.extend(listed)
        if len(listed) < 100:
            break
    return tags


def newest_release(tags: Iterable[Any], prefix: str = "") -> str | None:
    """The highest version among the tags that start as `prefix` does: `v1.2.3` or `1.2.3`
    with none, `daemon-v0.18.5` for `daemon-v`. Pre-releases and anything with a suffix are
    passed over, since a pin tracks releases."""
    words = prefix[:-1] if prefix.endswith("v") else prefix
    release = re.compile(rf"^{re.escape(words)}v?(\d+)(?:\.(\d+))?(?:\.(\d+))?$")
    best: tuple[tuple[int, int, int], str] | None = None
    for tag in tags:
        name = str(tag.get("name", "")) if isinstance(tag, dict) else str(tag)
        match = release.match(name)
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
        return body.decode("utf-8-sig").split("\n")
    except UnicodeDecodeError:
        return None


def is_page(path: str) -> bool:
    return Path(path).suffix.lower() in PAGES


def _digest(body: bytes) -> bytes:
    return hashlib.sha256(body).digest()


def _code(text: str) -> str:
    """Upstream's words in a code span, wherever a person reads them: a line of XML is otherwise
    taken for markup and read as nothing, and `@contextmanager` for a mention of somebody."""
    fence = "``" if "`" in text else "`"
    pad = " " if text.startswith("`") or text.endswith("`") else ""
    return f"{fence}{pad}{text}{pad}{fence}"


def compare(cited: Cited, pinned: bytes, current: bytes | None, head_sha: str) -> Row:
    """One ref whose file is there at the pin, against its baseline, as a row."""
    where = f"{cited.path}:{cited.line}" if cited.line is not None else cited.path
    if current is None:
        return Row(cited.ref, where, "gone")
    pinned_lines = text_of(pinned)
    current_lines = text_of(current)
    if cited.line is None or pinned_lines is None or current_lines is None:
        # a checkout's line endings are no change to the file, and a binary one has its own bytes
        same = (
            _digest(pinned) == _digest(current)
            if pinned_lines is None or current_lines is None
            else _digest(pinned.replace(b"\r\n", b"\n")) == _digest(current.replace(b"\r\n", b"\n"))
        )
        if same:
            return Row(cited.ref, where, "same")
        if pinned_lines is None or current_lines is None:
            return Row(cited.ref, where, "file changed")
        return Row(cited.ref, where, "page changed" if is_page(cited.path) else "file changed")
    if cited.line < 1 or cited.line > len(pinned_lines):
        return Row(cited.ref, where, "bad anchor", "no such line in the file at the pin")
    wanted = pinned_lines[cited.line - 1].strip()
    if not wanted:
        return Row(cited.ref, where, "bad anchor", "the line is blank")
    if wanted in BARE or len(wanted) <= 2:
        return Row(cited.ref, where, "bad anchor", f"the line reads {_code(wanted)}")
    alike = sum(1 for line in pinned_lines if line.strip() == wanted)
    if cited.line <= len(current_lines) and current_lines[cited.line - 1].strip() == wanted:
        return Row(cited.ref, where, "same", weak=alike > 1)
    if alike > 1:
        # one of several lines that read alike where it was read: wherever those words are
        # now, nothing says which of them the ref meant, so it cannot be called moved
        return Row(
            cited.ref,
            where,
            "changed",
            f"one of {alike} lines that read alike at the pin, so it cannot be followed to "
            f"{head_sha[:7]}: re-anchor it on a line of its own",
            weak=True,
        )
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
    return Row(cited.ref, where, "changed", f"no line reads {_code(wanted[:60])} at {head_sha[:7]}")


def absent_at_the_pin(cited: Cited, cache: Cache, head_sha: str) -> Row | None:
    """A ref the raw host has nothing for at the pin, asked of the API: a directory or a
    submodule, which the raw host never serves, is no fault while the baseline still has it and
    `gone` once it does not; nothing there either is a broken ref; and a file the API has is the
    raw host refusing, which is no finding at all (None)."""
    where = f"{cited.path}:{cited.line}" if cited.line is not None else cited.path
    listing = cache.json(contents_url(cited.repo, cited.path, cited.sha))
    if isinstance(listing, list) or (
        isinstance(listing, dict) and listing.get("type") == "submodule"
    ):
        if cited.line is not None:
            return Row(cited.ref, where, "bad anchor", "the path is a directory at the pin")
        if cache.json(contents_url(cited.repo, cited.path, head_sha)) is None:
            return Row(cited.ref, where, "gone", f"no such directory at {head_sha[:7]}")
        return Row(cited.ref, where, "a directory")
    if listing is None:
        neither = "neither the raw host nor the API has it"
        return Row(cited.ref, where, "nothing at the pin", neither)
    return None


DRIFT = {"changed", "gone", "file changed", "baseline gone"}
BROKEN = {"bad anchor", "nothing at the pin"}
QUIET = {"same", "a directory"}
"""Statuses that are counted and are no row: nothing in them is for anybody to read."""


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
    gone_baselines: set[str] = set()
    for cited in files:
        owner = seen.get(cited.key)
        if owner is not None and owner != name:
            report.count("shared")
            continue
        seen.setdefault(cited.key, name)
        if cited.repo in gone_baselines:
            report.count("baseline gone")
            continue
        try:
            baseline = report.baselines.get(cited.repo)
            if baseline is None:
                baseline = resolve(cache, cited.repo, declared_baseline(module, cited.repo))
                report.baselines[cited.repo] = baseline
            pinned = cache.get(raw_url(cited.repo, cited.sha, cited.path))
            if pinned is None:
                row = absent_at_the_pin(cited, cache, baseline.sha)
                if row is None:
                    report.block(
                        "raw.githubusercontent.com: said 404 for a file the API has at the pin",
                        cited.ref,
                    )
                    continue
            else:
                current = cache.get(raw_url(cited.repo, baseline.sha, cited.path))
                row = compare(cited, pinned, current, baseline.sha)
        except BaselineGone as e:
            gone_baselines.add(cited.repo)
            where = f"{cited.path}:{cited.line}" if cited.line is not None else cited.path
            row = Row(cited.ref, where, "baseline gone", str(e))
        except Unreachable as e:
            report.block(str(e), cited.ref)
            continue
        report.count(row.status)
        if row.weak:
            report.count("weak anchor")
        if row.status not in QUIET:
            report.rows.append(row)
        elif row.weak:
            report.weak.append(row)
        report.drift = report.drift or row.status in DRIFT
        report.broken = report.broken or row.status in BROKEN
    report.versions, unknown = check_versions(module, cache, report.baselines)
    report.drift = report.drift or any(moved for _line, moved in report.versions)
    for cause in unknown:
        report.block(cause, "the versions it names")
    return report


def check_versions(
    module: ModuleType, cache: Cache, baselines: dict[str, Baseline]
) -> tuple[list[tuple[str, bool]], list[str]]:
    """The versions a module wrote down against what is published today: the package on PyPI
    where the module names the one it read, and upstream's own `pyproject.toml` at the baseline
    where the module recorded the version at its pin. Each answer is a line and whether it is
    drift, and newer on PyPI is drift: it is the release that lands on a fresh install. Each one
    that could not be read is a cause: a page a module's version is read off that answers 404
    says nothing about that version, so it holds the upstream's issue open rather than closing it
    as though the version still stood."""
    lines: list[tuple[str, bool]] = []
    unknown: list[str] = []

    def against(label: str, read: str, url: str, reader: Callable[[Any], str]) -> None:
        try:
            latest = reader(cache.json(url))
        except Unreachable as e:
            unknown.append(str(e))
            return
        if not latest:
            unknown.append(f"{url} names no version")
        elif latest != read:
            lines.append((f"{label} is {latest}; quackd read {read}", True))
        else:
            lines.append((f"{label} is still {read}", False))

    package = getattr(module, "PACKAGE", None)
    pypi_name = str(getattr(package, "name", "") or "").strip()
    read = getattr(module, "PYPI_VERSION_READ", None) or getattr(module, "VERSION", None)
    if pypi_name and isinstance(read, str):
        against(f"PyPI {pypi_name}", read, f"{PYPI}/{pypi_name}/json", _pypi_version)
    hub = getattr(module, "HUB_VERSION", None)
    if isinstance(hub, str):
        against("PyPI huggingface_hub", hub, f"{PYPI}/huggingface_hub/json", _pypi_version)
    at_pin = getattr(module, "VERSION_AT_PIN", None)
    primary = _repo_of(str(getattr(module, "REPO", "")))
    baseline = baselines.get(primary)
    if at_pin is not None and baseline is not None:
        url = raw_url(primary, baseline.sha, "pyproject.toml")
        was = str(getattr(at_pin, "name", at_pin))
        try:
            body = cache.get(url)
        except Unreachable as e:
            unknown.append(str(e))
        else:
            match = PYPROJECT_VERSION.search(body.decode("utf-8", "replace") if body else "")
            now = match.group(1) if match else ""
            if not now:
                unknown.append(f"{url} names no version")
            elif now != was:
                lines.append(
                    (f"pyproject.toml says {now} at {baseline.name}; {was} at the pin", True)
                )
            else:
                lines.append((f"pyproject.toml still says {was} at {baseline.name}", False))
    api_doc = getattr(module, "API_DOC", None)
    api_version = getattr(module, "API_VERSION", None)
    if isinstance(api_doc, str) and isinstance(api_version, str):
        against(api_doc, api_version, api_doc, _document_version)
    return lines, unknown


def _pypi_version(published: Any) -> str:
    info = published.get("info") if isinstance(published, dict) else None
    version = info.get("version") if isinstance(info, dict) else None
    return str(version) if version else ""


def _document_version(document: Any) -> str:
    info = document.get("info") if isinstance(document, dict) else None
    version = info.get("version") if isinstance(info, dict) else None
    return str(version) if version else ""


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


ORDER = (
    "same",
    "moved",
    "changed",
    "gone",
    "file changed",
    "page changed",
    "baseline gone",
    "bad anchor",
    "nothing at the pin",
    "a directory",
    "unchecked",
    "shared",
    "not a file",
    "weak anchor",
)


def summary_line(report: Report) -> str:
    counts = ", ".join(f"{report.counts[k]} {k}" for k in ORDER if report.counts.get(k))
    against = "; ".join(
        f"{repo} {b.kind} {b.name} at {b.sha[:7] or '?'}" for repo, b in report.baselines.items()
    )
    return f"pin {report.pin} read {report.read_on} vs {against or 'nothing resolved'}: {counts}"


def headline(reports: Sequence[Report]) -> str:
    """What the night found, in the order somebody has to act on it."""
    said: list[str] = []
    if drifted := [r.name for r in reports if r.drift]:
        said.append(f"Drifted: {', '.join(drifted)}.")
    if broken := [r.name for r in reports if r.broken]:
        said.append(f"Refs that cannot be followed: {', '.join(broken)}.")
    if unchecked := [r.name for r in reports if r.blocked]:
        said.append(f"Could not be checked tonight: {', '.join(unchecked)}.")
    if not said:
        said.append("Nothing drifted.")
    elif not drifted and not broken:
        said.append("Nothing drifted among what answered.")
    return " ".join(said)


def _cell(text: str) -> str:
    """A table cell's text, on one line and with its spaces as they are, since it may quote a
    line of upstream's: a pipe splits a GitHub table even inside a code span."""
    return text.replace("\r", " ").replace("\n", " ").replace("|", "\\|")


def section(report: Report) -> str:
    """One upstream's part of the report, which is also the body of its issue."""
    out = [f"## {report.name} (`{report.module}`)\n\n{summary_line(report)}"]
    for line, moved in report.versions:
        out.append(f"- **moved:** {line}" if moved else f"- {line}")
    for cause, refs in report.blocked.items():
        out.append(f"- could not check: {_code(cause)} ({_named(refs)})")
    if report.rows:
        out.append("\n| ref | cited | status | now |\n| --- | --- | --- | --- |")
        for row in report.rows:
            out.append(
                f"| `{_cell(row.ref)}` | `{_cell(row.cited)}` | {row.status} | {_cell(row.now)} |"
            )
    if report.weak:
        names = ", ".join(f"`{_cell(row.ref)}` (`{_cell(row.cited)}`)" for row in report.weak)
        out.append(
            f"\n<details><summary>{len(report.weak)} weak anchors: each reads the same tonight "
            "and like other lines of its own file, so a change there could not be followed"
            f"</summary>\n\n{names}\n\n</details>"
        )
    return "\n".join(out)


def markdown(reports: Sequence[Report]) -> str:
    """The report a person reads: one section per upstream, its rows, its versions."""
    out = [
        f"# Upstream drift\n\n{headline(reports)} A line that moved within its file is not "
        "drift; a page that changed is not drift."
    ]
    out.extend(f"\n{section(report)}" for report in reports)
    return "\n".join(out) + "\n"


def as_json(reports: Sequence[Report]) -> dict[str, Any]:
    return {
        "drifted": [r.name for r in reports if r.drift],
        "broken": [r.name for r in reports if r.broken],
        "unchecked": [r.name for r in reports if r.blocked],
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
                "broken": r.broken,
                "versions": [{"line": line, "moved": moved} for line, moved in r.versions],
                "unchecked": r.unchecked,
                "rows": [vars(row) for row in r.rows],
                "weak": [vars(row) for row in r.weak],
                "summary": summary_line(r),
            }
            for r in reports
        ],
    }


def exit_code(reports: Sequence[Report]) -> int:
    if any(r.needs_somebody for r in reports):
        return 1
    if any(r.blocked for r in reports):
        return 2
    return 0


# ── issues, one per upstream that needs somebody ────────────────────────────────────────

LABEL = "upstream-drift"
Gh = Callable[[Sequence[str]], str]


def gh_run(args: Sequence[str]) -> str:
    """`gh` with these arguments, its stdout; a non-zero exit, or no `gh` here at all, is an
    `Unreachable`."""
    try:
        result = subprocess.run(["gh", *args], capture_output=True, text=True, check=False)
    except OSError as e:
        raise Unreachable(f"gh could not be run: {e}") from e
    if result.returncode != 0:
        raise Unreachable(f"gh {' '.join(args[:2])}: {result.stderr.strip()}")
    return result.stdout


def sync_issues(reports: Sequence[Report], repo: str, gh: Gh = gh_run) -> list[str]:
    """One open issue per upstream that drifted or has a ref that cannot be followed, titled
    `upstream drift: <name>`, its body the upstream's section of the report, edited in place
    while that lasts and closed the night every ref reads as it was read.

    A night that could not check an upstream closes nothing and rewrites nothing for it: an
    outage is not a clean night, and a table missing the rows nobody could fetch would read as
    drift that went away. It still opens an issue for what it did find. A comment is added only
    when what needs somebody changed, so a watcher is not pinged every night with the same
    table. Returns what was done, one line per upstream."""
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
        try:
            line = _sync_one(report, repo, gh)
        except Unreachable as e:
            line = f"{report.name}: issue not synced: {e}"
        if line:
            done.append(line)
    return done


def _sync_one(report: Report, repo: str, gh: Gh) -> str | None:
    """One upstream's issue, and what was done about it, or None when nothing was."""
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
    number = str(issues[0]["number"]) if issues else ""
    if report.blocked and issues:
        return f"{report.name}: not all of it could be checked, issue #{number} left"
    if not report.needs_somebody:
        if not issues:
            return None
        gh(
            [
                "issue",
                "close",
                number,
                "--repo",
                repo,
                "--comment",
                "Every ref reads as it was read tonight, or has moved within its file.",
            ]
        )
        return f"{report.name}: issue #{number} closed"
    body = (
        f"{section(report)}\n\nOpened by `.github/workflows/upstream-drift.yml`. For a ref "
        "that changed, re-read it at the new baseline and move the pin and `READ_ON` in "
        f"`{report.module}`. For one that cannot be followed, anchor it on the line that "
        "names what it cites. The issue closes itself the night after."
    )
    if issues:
        before = _needing(str(issues[0].get("body", "")))
        gh(["issue", "edit", number, "--repo", repo, "--body", body])
        if before != _needing(body):
            gh(
                [
                    "issue",
                    "comment",
                    number,
                    "--repo",
                    repo,
                    "--body",
                    "What needs somebody changed tonight; the body above is current.",
                ]
            )
        return f"{report.name}: issue #{number} updated"
    create = ["issue", "create", "--repo", repo, "--title", title, "--label", LABEL]
    created = gh([*create, "--body", body])
    return f"{report.name}: issue opened ({created.strip().split()[-1]})"


_NEEDING_ROW = re.compile(
    r"^\| `(.+?)` \| `.+?` \| "
    r"(changed|gone|file changed|baseline gone|bad anchor|nothing at the pin) \|",
    re.M,
)
_MOVED_VERSION = re.compile(r"^- \*\*moved:\*\* (.+)$", re.M)


def _needing(body: str) -> set[str]:
    """What an issue's body says needs somebody: the refs that drifted or cannot be followed, each
    with what became of it, so a line that changed and then went earns a word, and the versions
    that moved."""
    rows = {f"{ref}: {status}" for ref, status in _NEEDING_ROW.findall(body)}
    return rows | set(_MOVED_VERSION.findall(body))


# ── the command ─────────────────────────────────────────────────────────────────────────


def _say(text: str) -> None:
    """Print, on whatever the stream can carry: an upstream's line may hold a character a
    Windows console's code page has not got, and the report is already in its files."""
    try:
        print(text, end="")
    except UnicodeEncodeError:
        encoding = getattr(sys.stdout, "encoding", None) or "ascii"
        print(text.encode(encoding, "replace").decode(encoding), end="")


def main(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
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
            _say(f"no upstream is called {args.only!r}; doctor names them\n")
            return 2
    fetch: Fetch = offline_fetch(args.offline) if args.offline else web_fetch
    reports = run(rows, fetch)
    text = markdown(reports)
    if args.report:
        args.report.write_text(text, encoding="utf-8", newline="\n")
    if args.json:
        args.json.write_text(json.dumps(as_json(reports), indent=2), encoding="utf-8", newline="\n")
    _say(text)
    code = exit_code(reports)
    if args.issues:
        try:
            lines = sync_issues(reports, args.issues)
        except Unreachable as e:
            lines = [f"issues not synced: {e}"]
        _say("".join(line + "\n" for line in lines))
        if any("not synced" in line for line in lines):
            code = code or 2
        if args.report and lines:
            # in the run's summary, since a job red every night hides a refusal in its log
            with args.report.open("a", encoding="utf-8", newline="\n") as f:
                f.write("\n## Issues\n\n" + "".join(f"- {_code(line)}\n" for line in lines))
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
