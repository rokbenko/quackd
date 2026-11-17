"""The five workflows under `.github/workflows/`, held to how they were hardened.

Each rule here is one a later edit could quietly undo: an action named by a tag a force push can
move, a uv release that differs from one job to the next, a sync that rewrites a stale lock, a
token left in `.git/config`, a write granted to every job of a workflow. The files are read as
text where a comment is the point (a version beside a pin) and as YAML everywhere else. What
the drift and the nightly's report jobs do is held beside their scripts' tests.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

REPO = Path(__file__).resolve().parents[1]
WORKFLOWS = REPO / ".github" / "workflows"
RUN_SH = REPO / "tests" / "rosarm_contract" / "run.sh"

#: Every workflow there is, and every job each one has: a job added or dropped is a change to
#: what CI proves, made here on purpose or not at all.
JOBS = {
    "ci.yml": {"test", "packaging", "ui", "onboard", "policy", "workflows"},
    "contributors.yml": {"regenerate"},
    "fresh-install.yml": {"fresh", "wheels", "report"},
    "rosarm-contract.yml": {"contract"},
    "upstream-drift.yml": {"drift"},
}

#: The jobs that ask for more than the workflow's read only token, and what they ask for.
WRITES = {
    ("contributors.yml", "regenerate"): {"contents": "write"},
    ("fresh-install.yml", "report"): {"contents": "read", "issues": "write"},
    ("upstream-drift.yml", "drift"): {"contents": "read", "issues": "write"},
}

_USES = re.compile(r"^\s*(?:-\s+)?uses:\s*(\S+)(.*)$")


def _workflow(name: str) -> dict[str, Any]:
    loaded: dict[str, Any] = yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))
    return loaded


def _steps() -> list[tuple[str, str, dict[str, Any]]]:
    """(file, job id, step) for every step of every workflow."""
    return [
        (name, job_id, step)
        for name in JOBS
        for job_id, job in _workflow(name)["jobs"].items()
        for step in job["steps"]
    ]


def _uv_version() -> str:
    match = re.search(r"^uv_version=(\d+\.\d+\.\d+)$", RUN_SH.read_text(encoding="utf-8"), re.M)
    assert match, "tests/rosarm_contract/run.sh pins no uv_version"
    return match.group(1)


def _piped_into_a_shell(text: str) -> bool:
    """Whether a pipe in `text` feeds `sh` or `bash`, with or without `env NAME=VALUE` words
    before it. Read word by word: a pattern with a repeated group for the `env` words can take
    exponential time on a long line of near misses."""
    for line in text.splitlines():
        for after in line.split("|")[1:]:
            words = after.split()
            if words and words[0] == "env":
                words = words[1:]
                while words and "=" in words[0]:
                    words = words[1:]
            if words and words[0].rstrip(";)&") in ("sh", "bash"):
                return True
    return False


def test_the_workflows_and_their_jobs_are_the_ones_this_file_names() -> None:
    """A job added without a line here would be held to none of the rules below."""
    assert {path.name for path in WORKFLOWS.glob("*.yml")} == set(JOBS)
    assert not list(WORKFLOWS.glob("*.yaml"))
    for name, jobs in JOBS.items():
        assert set(_workflow(name)["jobs"]) == jobs, name


def test_every_action_is_pinned_by_the_commit_of_a_release_named_beside_it() -> None:
    """A tag is a pointer its owner, or whoever takes over its repository, can move to any
    commit, and the next run would execute it with the job's token. Each action is named by the
    full sha of a released commit, with the release as a comment so a person and Dependabot can
    read which, and one action is the same commit in every file."""
    pins: dict[str, set[tuple[str, str]]] = {}
    found = 0
    for path in sorted(WORKFLOWS.glob("*.yml")):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            match = _USES.match(line)
            if not match:
                continue
            found += 1
            where = f"{path.name}:{number}"
            action, _, ref = match.group(1).partition("@")
            assert re.fullmatch(r"[0-9a-f]{40}", ref), f"{where}: {action} is not pinned by a sha"
            comment = re.match(r"\s+# (v\d+\.\d+\.\d+)\b", match.group(2))
            assert comment, f"{where}: {action} has no `# vX.Y.Z` beside its sha"
            pins.setdefault(action, set()).add((ref, comment.group(1)))
    assert found >= len(JOBS), "no `uses:` line was read"
    for action, seen in pins.items():
        assert len(seen) == 1, f"{action} is pinned differently across the workflows: {seen}"


def test_every_setup_uv_step_installs_the_uv_the_ros_container_pins() -> None:
    """With no version each job took whatever uv was newest that morning, so two jobs of one run
    could resolve with two releases. Every setup-uv step installs the release
    `tests/rosarm_contract/run.sh` pins, so a bump there moves them all or fails here."""
    pinned = _uv_version()
    steps = [step for _, _, step in _steps() if "setup-uv" in step.get("uses", "")]
    assert len(steps) >= len(JOBS)
    for step in steps:
        assert step.get("with", {}).get("version") == pinned, step


def test_ci_never_pipes_an_installer_into_a_shell() -> None:
    """The ROS container used to fetch uv's installer and run it. In CI it runs the runner's own
    uv, mounted read only, and the script refuses to fetch the installer when it is in CI; the
    installer stays for a person running the script by hand."""
    assert _piped_into_a_shell("curl -LsSf https://example.org/install.sh | sh")
    assert _piped_into_a_shell("curl x \\\n  | env UV_INSTALL_DIR=/usr/local/bin bash -s")
    assert not _piped_into_a_shell("uv run pytest | tee log.txt")
    for path in WORKFLOWS.glob("*.yml"):
        assert not _piped_into_a_shell(path.read_text(encoding="utf-8")), path.name
    (job,) = _workflow("rosarm-contract.yml")["jobs"].values()
    runs = " ".join(step.get("run", "") for step in job["steps"])
    assert '-v "$(command -v uv)":/usr/local/bin/uv:ro' in runs and "-e GITHUB_ACTIONS" in runs
    script = RUN_SH.read_text(encoding="utf-8")
    refusal = script.index('if [ -n "${GITHUB_ACTIONS:-}" ]; then')
    assert refusal < script.index("curl -LsSf") < script.index("| env UV_INSTALL_DIR")
    assert "https://astral.sh/uv/${uv_version}/install.sh" in script


def test_every_job_that_installs_from_the_lock_refuses_a_stale_one() -> None:
    """`uv sync` and `uv run` write a new uv.lock when a pyproject.toml has moved, so a job could
    test a lock that nobody committed. Each job that runs either does so with UV_LOCKED, and the
    ROS container's sync is `--locked`. The nightly fresh install is the one that installs with
    no lock, on purpose, and sets none."""
    for name in JOBS:
        workflow = _workflow(name)
        for job_id, job in workflow["jobs"].items():
            env = {**workflow.get("env", {}), **job.get("env", {})}
            runs = [step.get("run", "") for step in job["steps"]]
            from_lock = any(re.search(r"\buv (?:sync|run)\b(?! --no-project)", run) for run in runs)
            if from_lock:
                assert env.get("UV_LOCKED") == "1", f"{name} {job_id} syncs without UV_LOCKED"
    fresh = _workflow("fresh-install.yml")
    assert "UV_LOCKED" not in fresh.get("env", {})
    for job in fresh["jobs"].values():
        assert not any("uv sync" in step.get("run", "") for step in job["steps"])
    script = RUN_SH.read_text(encoding="utf-8")
    assert "uv sync --locked " in script and "--frozen" not in script


def test_every_checkout_drops_its_token_but_the_one_that_pushes() -> None:
    """actions/checkout leaves the job's token in `.git/config` unless told not to, where every
    later step can read it. Only the contributors job pushes, and it is the one checkout that
    keeps it."""
    kept = []
    for name, job_id, step in _steps():
        if "actions/checkout" not in step.get("uses", ""):
            continue
        if step.get("with", {}).get("persist-credentials") is not False:
            kept.append((name, job_id))
    assert kept == [("contributors.yml", "regenerate")]
    pushes = [(name, job) for name, job, step in _steps() if "git push" in step.get("run", "")]
    assert pushes == kept


def test_each_workflow_reads_and_only_the_jobs_that_write_ask_for_it() -> None:
    """A write granted at the top of a workflow is every job's, so the contributors workflow's
    `contents: write` was its checkout's and its script's too. Each workflow grants
    `contents: read` and nothing more, and a job that writes asks for it, saying what for."""
    asked = {}
    for name in JOBS:
        workflow = _workflow(name)
        assert workflow["permissions"] == {"contents": "read"}, name
        for job_id, job in workflow["jobs"].items():
            if "permissions" in job:
                asked[(name, job_id)] = job["permissions"]
    assert asked == WRITES
    for name in {name for name, _job in WRITES}:
        text = (WORKFLOWS / name).read_text(encoding="utf-8")
        for line in text.splitlines():
            if re.match(r"\s+(?:issues|contents): write\b", line):
                assert "#" in line, f"{name}: {line.strip()} says not what it is for"


def test_each_pytest_step_writes_junit_and_the_job_puts_its_failures_in_its_summary() -> None:
    """A red job said which tests failed only in a log of thousands of lines. Every pytest step
    of ci.yml and the nightly writes a JUnit file, and the job's last step, which runs whenever
    the job was not cancelled, hands every one of them to `scripts/ci_failed_tests.py`."""
    for name in ("ci.yml", "fresh-install.yml"):
        for job_id, job in _workflow(name)["jobs"].items():
            runs = [step.get("run", "") for step in job["steps"]]
            tests = [run for run in runs if re.search(r"(?:-m|uv run(?: --no-sync)?) pytest", run)]
            if not tests:
                continue
            files = []
            for run in tests:
                match = re.search(r'--junitxml="([^"]+)"', run)
                assert match, f"{name} {job_id}: a pytest step writes no JUnit file: {run}"
                files.append(match.group(1))
            last = job["steps"][-1]
            assert last.get("if") == "${{ !cancelled() }}", f"{name} {job_id}"
            assert "scripts/ci_failed_tests.py" in last["run"], f"{name} {job_id}"
            assert re.findall(r'"([^"]+\.xml)"', last["run"]) == files, f"{name} {job_id}"


def test_the_workflows_are_linted_at_pinned_releases_on_every_pull_request() -> None:
    """A workflow is code that runs with a token, and nothing read it but a person. ci.yml's
    `workflows` job runs actionlint, from its release and checked against that release's sha256,
    and zizmor at an exact version, over every workflow and the Dependabot configuration."""
    job = _workflow("ci.yml")["jobs"]["workflows"]
    assert "actionlint" in job["name"] and "zizmor" in job["name"]
    (lint,) = [step for step in job["steps"] if "actionlint" in step.get("run", "")]
    assert re.fullmatch(r"\d+\.\d+\.\d+", lint["env"]["ACTIONLINT_VERSION"])
    assert re.fullmatch(r"[0-9a-f]{64}", lint["env"]["ACTIONLINT_SHA256"])
    assert "sha256sum --check" in lint["run"]
    (zizmor,) = [step for step in job["steps"] if "zizmor" in step.get("run", "")]
    assert re.search(r"\buvx zizmor==\d+\.\d+\.\d+ ", zizmor["run"])
    assert ".github/workflows" in zizmor["run"] and ".github/dependabot.yml" in zizmor["run"]


def test_no_workflow_names_the_repository_it_runs_in() -> None:
    """The slug written into a workflow is a line that breaks the day the repository is renamed:
    every workflow names its repository by `github.repository`."""
    for path in WORKFLOWS.glob("*.yml"):
        assert "rokbenko/" not in path.read_text(encoding="utf-8"), path.name
