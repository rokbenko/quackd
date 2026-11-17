"""Keep one issue open while the nightly fresh install is red, and close it the night it is not.

`.github/workflows/fresh-install.yml` installs the newest release of everything Roboglot asks
for, with no lock, every night, and a red night used to be said only on the Actions page, which
nobody opens for a job that gates nothing. Its `report` job runs this after the others, on a
scheduled night only. A night any job did not pass opens one issue titled `TITLE`, naming the
run and the jobs that did not pass, or adds one comment to that issue when it is already open.
The first night every job passes closes it with a comment.

The issue is found by its exact title among the open ones, and only when its body carries
`OPENED_BY`, so an issue a person opened with the same words is never commented on or closed.
Everything it writes is built here from GitHub's own variables for the run and the jobs' ids
and results, each checked for its shape first: nothing from any other host reaches the issue.

It reads `RESULTS`, the workflow's `toJSON(needs)`, and the runner's `GITHUB_REPOSITORY`,
`GITHUB_SERVER_URL` and `GITHUB_RUN_ID`, and writes through `gh`, which takes its token from
`GH_TOKEN`. Exit 0 when the issue is in step with the night, 1 when `gh` refused or did not
answer, and 2 when what it was given is not what the report job gives it. Standard library
only, since it runs on the runner's own Python.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from typing import Any

TITLE = "The nightly fresh install is red"
"""The one title the script writes and looks for."""
OPENED_BY = "Opened by `.github/workflows/fresh-install.yml`."
"""What the body of the issue says, which tells it from one a person opened with the title."""
ISSUES_LISTED = 50
"""How many open issues the search for the title lists: one, and the odd repeat, with room."""
GH_TIMEOUT_S = 60
"""How long one `gh` call may take: a call answers in a second or two, and the job has five
minutes, so a call that hangs ends the step with a reason rather than the job without one."""

RESULTS = ("success", "failure", "cancelled", "skipped")
"""What GitHub writes as a job's `result` in the `needs` context."""
_JOB_ID = re.compile(r"[A-Za-z_][A-Za-z0-9_-]*")
"""A job's id as GitHub's workflow syntax allows it: a letter or `_`, then letters, digits, `-`
and `_`."""
_REPOSITORY = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
_RUN_ID = re.compile(r"[0-9]+")

Gh = Callable[[Sequence[str]], str]


class GhFailed(RuntimeError):
    """`gh` exited non-zero, did not answer in time, or is not on the machine."""


class Refused(ValueError):
    """What the script was given is not what the report job gives it."""


def gh_run(args: Sequence[str]) -> str:
    """`gh` with these arguments, its stdout."""
    try:
        result = subprocess.run(
            ["gh", *args], capture_output=True, text=True, check=False, timeout=GH_TIMEOUT_S
        )
    except subprocess.TimeoutExpired as e:
        raise GhFailed(f"gh {' '.join(args[:2])} did not answer in {GH_TIMEOUT_S} s") from e
    except OSError as e:
        raise GhFailed(f"gh could not be run: {e}") from e
    if result.returncode != 0:
        raise GhFailed(f"gh {' '.join(args[:2])}: {result.stderr.strip()}")
    return result.stdout


def not_passed(results: Mapping[str, Any]) -> list[tuple[str, str]]:
    """Each job that did not pass, by its id, with its result. Raises `Refused` for a mapping
    that is empty, an id that is not a job's, or a result GitHub does not write."""
    if not results:
        raise Refused("RESULTS names no job: it is the report job's `toJSON(needs)`")
    found: list[tuple[str, str]] = []
    for job, value in results.items():
        result = value.get("result") if isinstance(value, Mapping) else None
        if not isinstance(job, str) or not _JOB_ID.fullmatch(job):
            raise Refused(f"RESULTS names a job whose id is not one: {job!r}")
        if result not in RESULTS:
            raise Refused(f"RESULTS gives {job} a result GitHub does not write: {result!r}")
        if result != "success":
            found.append((job, str(result)))
    return found


def run_url(env: Mapping[str, str]) -> tuple[str, str]:
    """The repository and the address of this run, from the runner's own variables."""
    repository = env.get("GITHUB_REPOSITORY", "")
    server = env.get("GITHUB_SERVER_URL", "")
    run_id = env.get("GITHUB_RUN_ID", "")
    if not _REPOSITORY.fullmatch(repository):
        raise Refused(f"GITHUB_REPOSITORY is not owner/name: {repository!r}")
    if not re.fullmatch(r"https://[A-Za-z0-9.-]+", server):
        raise Refused(f"GITHUB_SERVER_URL is not an https address of a host: {server!r}")
    if not _RUN_ID.fullmatch(run_id):
        raise Refused(f"GITHUB_RUN_ID is not a run's number: {run_id!r}")
    return repository, f"{server}/{repository}/actions/runs/{run_id}"


def _jobs(failed: Sequence[tuple[str, str]]) -> str:
    named = [f"`{job}` ({result})" for job, result in failed]
    return named[0] if len(named) == 1 else ", ".join(named[:-1]) + " and " + named[-1]


def _open_issues(repo: str, gh: Gh) -> list[dict[str, Any]]:
    """The open issues the script wrote, oldest first."""
    listed = gh(
        [
            "issue",
            "list",
            "--repo",
            repo,
            "--state",
            "open",
            "--search",
            f'"{TITLE}" in:title',
            "--limit",
            str(ISSUES_LISTED),
            "--json",
            "number,title,body",
        ]
    )
    issues = [
        issue
        for issue in json.loads(listed or "[]")
        if issue.get("title") == TITLE and OPENED_BY in str(issue.get("body", ""))
    ]
    return sorted(issues, key=lambda issue: int(issue["number"]))


def sync(
    results: Mapping[str, Any], repo: str, run: str, gh: Gh, done: list[str] | None = None
) -> list[str]:
    """Open, comment on or close the issue for one night. Returns what was done, a line at a
    time, appended to `done` as each is done, so a refusal partway through leaves the lines
    before it there."""
    failed = not_passed(results)
    issues = _open_issues(repo, gh)
    done = [] if done is None else done
    if not failed:
        for issue in issues:
            number = str(issue["number"])
            comment = f"Every job passed in {run}, so this closes."
            gh(["issue", "close", number, "--repo", repo, "--comment", comment])
            done.append(f"issue #{number} closed")
        return done
    if issues:
        number = str(issues[0]["number"])
        comment = f"Still red in {run}: {_jobs(failed)}."
        gh(["issue", "comment", number, "--repo", repo, "--body", comment])
        done.append(f"issue #{number}: still red, commented")
        return done
    body = (
        f"The nightly fresh install did not pass in {run}: {_jobs(failed)}.\n\n"
        "It installs the newest release of everything Roboglot asks for, with no lock, so what "
        "broke may be another project's new release rather than a change here. Each night it "
        "stays red adds a comment here, and the first night every job passes closes this issue."
        f"\n\n{OPENED_BY}"
    )
    created = gh(["issue", "create", "--repo", repo, "--title", TITLE, "--body", body])
    done.append(f"issue opened ({created.strip().split()[-1] if created.strip() else '?'})")
    return done


def main(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    parser.parse_args(argv)
    try:
        results = json.loads(os.environ.get("RESULTS", ""))
        if not isinstance(results, dict):
            raise Refused("RESULTS is not a mapping of job ids")
        repo, run = run_url(os.environ)
        not_passed(results)
    except (json.JSONDecodeError, Refused) as e:
        print(f"nothing synced: {e}")
        return 2
    done: list[str] = []
    try:
        sync(results, repo, run, gh_run, done)
    except GhFailed as e:
        done.append(f"not synced: {e}")
    for line in done:
        print(line)
    return 1 if any(line.startswith("not synced") for line in done) else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
