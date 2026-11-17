"""`scripts/nightly_issue.py`: the one issue that says the nightly fresh install is red.

As `tests/test_upstream_drift.py` does for its own issues, every case hands the script a fake
`gh` that records each call and answers `issue list` from a list, so nothing here reaches the
network or GitHub. The workflow job that runs the script is read back as YAML.
"""

from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "nightly_issue.py"
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "fresh-install.yml"

REPO = "someone/project"
RUN = f"https://github.com/{REPO}/actions/runs/4242"
RED = {
    "fresh": {"result": "failure", "outputs": {}},
    "wheels": {"result": "success", "outputs": {}},
}
GREEN = {
    "fresh": {"result": "success", "outputs": {}},
    "wheels": {"result": "success", "outputs": {}},
}


def _script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("nightly_issue", SCRIPT)
    assert spec and spec.loader, f"{SCRIPT} could not be loaded as a module"
    module = importlib.util.module_from_spec(spec)
    sys.modules["nightly_issue"] = module
    spec.loader.exec_module(module)
    return module


class FakeGh:
    """`gh` as a list of calls, with canned answers to `issue list` and `issue create`."""

    def __init__(self, listed: list[dict[str, Any]]) -> None:
        self.listed = listed
        self.calls: list[list[str]] = []

    def __call__(self, args: Any) -> str:
        self.calls.append(list(args))
        if list(args[:2]) == ["issue", "list"]:
            return json.dumps(self.listed)
        if list(args[:2]) == ["issue", "create"]:
            return f"https://github.com/{REPO}/issues/31\n"
        return ""

    def verbs(self) -> list[str]:
        return [" ".join(call[:2]) for call in self.calls]

    def text_of(self, verb: str) -> str:
        """The body or the comment the call of this verb carried."""
        (call,) = [call for call in self.calls if " ".join(call[:2]) == verb]
        flag = "--body" if "--body" in call else "--comment"
        return call[call.index(flag) + 1]


def _ours(script: ModuleType, number: int) -> dict[str, Any]:
    return {"number": number, "title": script.TITLE, "body": f"a red night\n\n{script.OPENED_BY}"}


def test_a_red_night_with_no_issue_open_opens_one_naming_the_run_and_the_failed_jobs() -> None:
    """A red nightly said so only on a page nobody opens: the first red night opens an issue,
    and it names the run and which jobs failed, so whoever reads it knows where to look."""
    script = _script()
    gh = FakeGh([])
    done = script.sync(RED, REPO, RUN, gh)
    assert gh.verbs() == ["issue list", "issue create"]
    create = gh.calls[1]
    assert create[create.index("--title") + 1] == script.TITLE
    assert create[create.index("--repo") + 1] == REPO
    body = gh.text_of("issue create")
    assert RUN in body and "`fresh` (failure)" in body
    assert "wheels" not in body, "a job that passed is not named"
    assert script.OPENED_BY in body
    assert done == [f"issue opened (https://github.com/{REPO}/issues/31)"]


def test_a_red_night_with_the_issue_open_adds_one_comment_and_opens_no_other() -> None:
    """A second red night that opened a second issue would leave one open for ever once the
    first green night closed the other: it comments on the one there is."""
    script = _script()
    gh = FakeGh([_ours(script, 12)])
    both = {**RED, "wheels": {"result": "cancelled", "outputs": {}}}
    done = script.sync(both, REPO, RUN, gh)
    assert gh.verbs() == ["issue list", "issue comment"]
    assert gh.calls[1][2] == "12"
    comment = gh.text_of("issue comment")
    assert RUN in comment and "`fresh` (failure) and `wheels` (cancelled)" in comment
    assert done == ["issue #12: still red, commented"]


def test_a_green_night_closes_the_issue_with_a_comment_and_does_nothing_without_one() -> None:
    """The issue is a signal only while it is true: the first night every job passes closes it,
    saying which run passed, and a green night with no issue open makes no call that writes."""
    script = _script()
    gh = FakeGh([_ours(script, 12)])
    done = script.sync(GREEN, REPO, RUN, gh)
    assert gh.verbs() == ["issue list", "issue close"]
    assert gh.calls[1][2] == "12" and RUN in gh.text_of("issue close")
    assert done == ["issue #12 closed"]

    gh = FakeGh([])
    assert script.sync(GREEN, REPO, RUN, gh) == []
    assert gh.verbs() == ["issue list"]


def test_the_issue_is_found_by_its_fixed_title_and_a_persons_own_is_left_alone() -> None:
    """The search matches words, so it lists other titles too, and a person may open an issue
    with the same words: only the exact title with the line the workflow writes is the one it
    keeps, the oldest first when a repeat is open, and every repeat closes on a green night."""
    script = _script()
    persons = {"number": 3, "title": script.TITLE, "body": "I saw it go red too"}
    other = {"number": 4, "title": f"{script.TITLE} again?", "body": script.OPENED_BY}
    listed = [_ours(script, 20), other, persons, _ours(script, 9)]
    gh = FakeGh(listed)
    script.sync(RED, REPO, RUN, gh)
    assert gh.verbs() == ["issue list", "issue comment"] and gh.calls[1][2] == "9"
    search = gh.calls[0][gh.calls[0].index("--search") + 1]
    assert script.TITLE in search and "in:title" in search

    gh = FakeGh(listed)
    done = script.sync(GREEN, REPO, RUN, gh)
    assert [call[2] for call in gh.calls[1:]] == ["9", "20"], "never 3, the person's, nor 4"
    assert done == ["issue #9 closed", "issue #20 closed"]

    gh = FakeGh([persons])
    script.sync(RED, REPO, RUN, gh)
    assert gh.verbs() == ["issue list", "issue create"]


def test_nothing_it_writes_has_dash_punctuation() -> None:
    """The owner's rule for every text posted under the project's name: no em dash, no en dash
    and no hyphen with a space on either side, which reads as written by a machine."""
    script = _script()
    texts = []
    for results, listed in ((RED, []), (RED, [_ours(script, 1)]), (GREEN, [_ours(script, 1)])):
        gh = FakeGh(listed)
        script.sync(results, REPO, RUN, gh)
        texts += [gh.text_of(verb) for verb in gh.verbs() if verb != "issue list"]
    texts.append(script.TITLE)
    assert len(texts) == 4
    # the figure dash, the en dash, the em dash and the horizontal bar, U+2012 to U+2015
    dashes = f"[{chr(0x2012)}-{chr(0x2015)}]"
    for text in texts:
        assert not re.search(rf"{dashes}|\s-\s|\s-$|^-\s", text, flags=re.M), text


def test_what_reaches_the_issue_comes_from_this_run_and_nothing_else(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The issue names the run and the jobs, and both come from the runner: the address is
    built from GitHub's own variables and refused unless it is an https one with a run's number,
    and a job's id or result that is not one GitHub writes is refused rather than posted."""
    script = _script()
    gh = FakeGh([])
    monkeypatch.setattr(script, "gh_run", gh)
    env = {
        "RESULTS": json.dumps(RED),
        "GITHUB_REPOSITORY": REPO,
        "GITHUB_SERVER_URL": "https://github.com",
        "GITHUB_RUN_ID": "4242",
    }
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    assert script.main([]) == 0
    assert RUN in gh.text_of("issue create")

    for key, value in (
        ("GITHUB_SERVER_URL", "http://github.com"),
        ("GITHUB_RUN_ID", "42 @someone"),
        ("GITHUB_REPOSITORY", "someone/project](https://example.org"),
        ("RESULTS", json.dumps({"fresh @someone": {"result": "failure"}})),
        ("RESULTS", json.dumps({"fresh": {"result": "![x](https://example.org/x.png)"}})),
        ("RESULTS", "not json"),
        ("RESULTS", "{}"),
    ):
        gh = FakeGh([])
        monkeypatch.setattr(script, "gh_run", gh)
        monkeypatch.setenv(key, value)
        assert script.main([]) == 2, (key, value)
        assert gh.calls == [], f"{key}={value!r} reached gh"
        monkeypatch.setenv(key, env[key])


def test_a_gh_that_refuses_or_hangs_is_said_and_the_job_goes_red(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A refusal said nowhere is a red night nobody hears of, the thing this job is for: what was
    done before it stays said, the job exits 1, and `gh` runs under a timeout so a hung call
    ends the step rather than the job's own limit."""
    script = _script()
    real_gh = script.gh_run

    class Refuses(FakeGh):
        def __call__(self, args: Any) -> str:
            if list(args[:2]) == ["issue", "close"] and args[2] == "20":
                raise script.GhFailed("gh issue close: HTTP 403")
            return super().__call__(args)

    gh = Refuses([_ours(script, 9), _ours(script, 20)])
    monkeypatch.setattr(script, "gh_run", gh)
    monkeypatch.setenv("RESULTS", json.dumps(GREEN))
    monkeypatch.setenv("GITHUB_REPOSITORY", REPO)
    monkeypatch.setenv("GITHUB_SERVER_URL", "https://github.com")
    monkeypatch.setenv("GITHUB_RUN_ID", "4242")
    assert script.main([]) == 1
    out = capsys.readouterr().out
    assert "issue #9 closed" in out and "not synced: gh issue close: HTTP 403" in out

    seen: dict[str, Any] = {}

    def hangs(*args: Any, **kwargs: Any) -> Any:
        seen.update(kwargs)
        raise subprocess.TimeoutExpired(args[0], kwargs.get("timeout", 0))

    monkeypatch.setattr(script.subprocess, "run", hangs)
    with pytest.raises(script.GhFailed, match="did not answer"):
        real_gh(["issue", "list"])
    assert seen["timeout"] == script.GH_TIMEOUT_S

    def missing(*args: Any, **kwargs: Any) -> Any:
        raise FileNotFoundError("gh")

    monkeypatch.setattr(script.subprocess, "run", missing)
    with pytest.raises(script.GhFailed, match="gh could not be run"):
        real_gh(["issue", "list"])


def test_the_report_job_runs_after_the_others_on_a_scheduled_night_only() -> None:
    """Started by hand, the run has somebody watching it, and an issue opened then would be
    closed by whichever night came next. The job waits for every other job, runs whether they
    passed or failed but not when the run was cancelled, and holds `issues: write` itself, where
    the rest of the workflow can only read."""
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    jobs = workflow["jobs"]
    report = jobs["report"]
    assert set(report["needs"]) == set(jobs) - {"report"}
    assert report["if"] == "${{ !cancelled() && github.event_name == 'schedule' }}"
    assert report["permissions"] == {"contents": "read", "issues": "write"}
    assert workflow["permissions"] == {"contents": "read"}
    (step,) = [step for step in report["steps"] if "run" in step]
    assert step["run"] == "python scripts/nightly_issue.py"
    assert step["env"] == {"GH_TOKEN": "${{ github.token }}", "RESULTS": "${{ toJSON(needs) }}"}
    assert "timeout-minutes" in report
