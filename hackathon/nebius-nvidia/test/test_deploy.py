"""The Serverless Job: its scripts parse, they call only what exists, and a job's log carries
both robots' runs."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from fake_tokenfactory import TEXT_MODEL, FakeTokenFactory
from passport_nebius import doorstep
from passport_nebius.cli import app
from typer.testing import CliRunner

runner = CliRunner()
DEPLOY = Path(__file__).resolve().parents[1] / "deploy"


def _bash() -> str | None:
    """A bash that runs. On Windows the first `bash` on the path can be WSL's launcher with no
    distribution behind it, so Git's own is tried too."""
    for candidate in (shutil.which("bash"), r"C:\Program Files\Git\bin\bash.exe"):
        if candidate and Path(candidate).exists():
            works = subprocess.run([candidate, "-c", "exit 0"], capture_output=True, check=False)
            if works.returncode == 0:
                return candidate
    return None


BASH = _bash()


@pytest.mark.skipif(BASH is None, reason="needs a bash that runs")
@pytest.mark.parametrize("script", sorted(p.name for p in DEPLOY.glob("*.sh")))
def test_each_script_parses(script: str) -> None:
    assert BASH is not None
    checked = subprocess.run([BASH, "-n", str(DEPLOY / script)], capture_output=True, text=True)
    assert checked.returncode == 0, checked.stderr


def test_the_job_injects_the_script_the_folder_holds() -> None:
    creating = (DEPLOY / "serverless-job.sh").read_text(encoding="utf-8")
    assert '--inject-file "deploy/doorstep-job.sh:/work/doorstep-job.sh"' in creating
    assert "--args /work/doorstep-job.sh" in creating
    assert '--env-secret "NEBIUS_API_KEY=' in creating, "the key comes from MysteryBox"
    assert '--env "NEBIUS_API_KEY=' not in creating, "and never as a plain variable"


def test_what_the_job_runs_exists() -> None:
    running = (DEPLOY / "doorstep-job.sh").read_text(encoding="utf-8")
    assert "passport-nebius doorstep run --act" in running and "--print-logs" in running
    shown = runner.invoke(app, ["doorstep", "run", "--help"]).output
    assert "--print-logs" in shown and "--act" in shown


def test_a_jobs_log_gets_both_runs_the_courier_first(tmp_path: Path) -> None:
    (tmp_path / "home-01.log").write_text("home robot heard it\n", encoding="utf-8")
    (tmp_path / "courier-01.log").write_text("courier told it\n", encoding="utf-8")
    assert doorstep.logged(tmp_path) == [
        ("courier-01", "courier told it\n"),
        ("home-01", "home robot heard it\n"),
    ]
    assert doorstep.logged(tmp_path / "nothing-here") == []


def test_print_logs_needs_the_logs_only_role_both_writes(tokenfactory: FakeTokenFactory) -> None:
    args = ["doorstep", "run", "--role", "home", "--print-logs", "--model", TEXT_MODEL]
    result = runner.invoke(app, args)
    assert result.exit_code == 1
    assert "--print-logs prints the log files that only --role both writes" in result.output
