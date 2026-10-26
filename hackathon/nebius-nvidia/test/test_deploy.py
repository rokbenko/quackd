"""The Serverless Job: its scripts parse, a job that could not work is never created, the
container serves the postman's brain before any act and plays both acts on it, and a job's log
carries both robots' runs.

The scripts run against stand-in commands on a PATH of their own (`nebius`, `vllm`, `curl`, `uv`,
`hf`), so nothing here creates a job, starts a GPU or downloads a model. What they prove is what
the scripts ask of those commands, not that Nebius or vLLM answers as the stand-ins do.
"""

from __future__ import annotations

import io
import os
import re
import shlex
import shutil
import subprocess
import tarfile
from pathlib import Path

import pytest
from fake_tokenfactory import ONBOARD_MODEL, TEXT_MODEL, FakeTokenFactory
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
needs_bash = pytest.mark.skipif(BASH is None, reason="needs a bash that runs")
#: vLLM's flags as a person gives them, JSON with no space in it among them.
FLAGS = [
    "--enable-auto-tool-choice",
    "--tool-call-parser",
    "qwen3_coder",
    "--default-chat-template-kwargs",
    '{"enable_thinking":false}',
]


def _stubs(tmp_path: Path, commands: dict[str, str]) -> Path:
    """Each command a bash script of its own in one folder, with LF line ends."""
    folder = tmp_path / "bin"
    folder.mkdir()
    for name, body in commands.items():
        path = folder / name
        path.write_bytes(("#!/usr/bin/env bash\n" + body).encode("utf-8"))
        path.chmod(0o755)
    return folder


def _run(
    script: str, args: list[str], env: dict[str, str], stubs: Path
) -> subprocess.CompletedProcess[str]:
    """`script` run with `args`, which reach it through a script of their own in bash's quoting:
    on Windows a command line is split by Windows' rules first, and JSON's quotes do not survive
    that, while a person's shell on the way to a real job never does it."""
    assert BASH is not None
    driver = stubs.parent / "driver.sh"
    words = " ".join(shlex.quote(word) for word in [(DEPLOY / script).as_posix(), *args])
    driver.write_bytes(f'exec "$BASH" {words}\n'.encode())
    full = {**os.environ, **env, "PATH": f"{stubs}{os.pathsep}{os.environ['PATH']}"}
    return subprocess.run(
        [BASH, str(driver)],
        env=full,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
        check=False,
    )


@needs_bash
@pytest.mark.parametrize("script", sorted(p.name for p in DEPLOY.glob("*.sh")))
def test_each_script_parses(script: str) -> None:
    assert BASH is not None
    checked = subprocess.run([BASH, "-n", str(DEPLOY / script)], capture_output=True, text=True)
    assert checked.returncode == 0, checked.stderr


def test_no_model_id_is_written_into_the_scripts() -> None:
    for script in DEPLOY.glob("*.sh"):
        text = script.read_text(encoding="utf-8")
        assert not re.search(r"nvidia/[A-Za-z0-9]", text), f"{script.name} names a model"


def test_the_injected_script_fits_what_a_job_takes() -> None:
    """Nebius injects a file of 64 KiB at most into a job."""
    assert (DEPLOY / "doorstep-job.sh").stat().st_size < 64 * 1024


#: The Nebius CLI's stand-in: it records what it was asked, one argument a line.
NEBIUS = r"""printf '%s\n' "$@" > "$STUB_DIR/nebius.txt"
"""


def _create(tmp_path: Path, onboard: str, flags: list[str]) -> subprocess.CompletedProcess[str]:
    args = [TEXT_MODEL, onboard, "gpu-h100-sxm", "1gpu-16vcpu-200gb", *flags]
    stubs = _stubs(tmp_path, {"nebius": NEBIUS})
    return _run("serverless-job.sh", args, {"STUB_DIR": tmp_path.as_posix()}, stubs)


@needs_bash
def test_the_job_takes_the_gpu_given_and_the_key_from_mysterybox(tmp_path: Path) -> None:
    result = _create(tmp_path, ONBOARD_MODEL, FLAGS)
    assert result.returncode == 0, result.stderr
    called = (tmp_path / "nebius.txt").read_text(encoding="utf-8").splitlines()
    assert called[:3] == ["ai", "job", "create"]
    given = list(zip(called[3::2], called[4::2], strict=True))
    for pair in [
        ("--platform", "gpu-h100-sxm"),
        ("--preset", "1gpu-16vcpu-200gb"),
        ("--inject-file", "deploy/doorstep-job.sh:/job/doorstep-job.sh"),
        ("--container-command", "bash"),
        ("--args", "/job/doorstep-job.sh"),
        ("--env-secret", "NEBIUS_API_KEY=nebius-api-key"),
    ]:
        assert pair in given, pair
    assert dict(given)["--image"].startswith("vllm/vllm-openai:v"), "an image of a known version"
    envs = [value for flag, value in given if flag == "--env"]
    assert f"NEBIUS_MODEL={TEXT_MODEL}" in envs and f"ONBOARD_MODEL={ONBOARD_MODEL}" in envs
    assert "ONBOARD_VLLM_ARGS=" + " ".join(FLAGS) in envs, "the JSON arrives as it was given"
    assert not any(v.startswith("NEBIUS_API_KEY=") for v in envs), "never as a plain variable"


@needs_bash
@pytest.mark.parametrize(
    ("onboard", "flags", "said"),
    [
        ("someone/model", FLAGS, "the postman's brain is an NVIDIA model"),
        (ONBOARD_MODEL, [*FLAGS[:3], "--x", '{"a": 1}'], "holds a space"),
        (ONBOARD_MODEL, [*FLAGS, "--port", "9000"], "--port is the job's own"),
        (ONBOARD_MODEL, FLAGS[1:], "need --enable-auto-tool-choice"),
        (ONBOARD_MODEL, FLAGS[:1], "need --tool-call-parser"),
    ],
    ids=["not-nvidia", "a-space", "owned-flag", "no-auto-tool-choice", "no-parser"],
)
def test_a_job_that_could_not_work_is_never_created(
    tmp_path: Path, onboard: str, flags: list[str], said: str
) -> None:
    result = _create(tmp_path, onboard, flags)
    assert result.returncode == 2
    assert said in result.stderr
    assert not (tmp_path / "nebius.txt").exists(), "a job was created, and is billed"


#: The container's own commands, as stand-ins. `vllm` records how it was started, then serves
#: (its health check answers), dies at once, or never gets ready, as STUB_VLLM says.
CONTAINER = {
    "nvidia-smi": 'echo "NVIDIA H100 80GB HBM3, 580.65.06, 81559 MiB"\n',
    "python3": "exit 0\n",
    "curl": r"""case "$*" in
  *codeload.github.com*) cat "$STUB_TARBALL" ;;
  */health*) [ -f "$STUB_DIR/ready" ] ;;
  *) exit 22 ;;
esac
""",
    "hf": r"""while [ $# -gt 0 ]; do
  if [ "$1" = --local-dir ]; then mkdir -p "$2"; fi
  shift
done
""",
    "uv": r"""key="${ONBOARD_API_KEY:-}" nebius="${NEBIUS_API_KEY:+set}"
printf '%s | key=%s | nebius=%s\n' "$*" "$key" "$nebius" >> "$STUB_DIR/uv.txt"
""",
    "vllm": r"""printf 'args=%s\ncwd=%s\nkey=%s\nnebius=%s\n' "$*" "$(pwd)" "${VLLM_API_KEY:-}" \
  "${NEBIUS_API_KEY:+set}" > "$STUB_DIR/vllm.txt"
case "${STUB_VLLM:-serve}" in
  die) exit 3 ;;
  serve) touch "$STUB_DIR/ready" ;;
esac
exec sleep 30
""",
}


def _tarball(tmp_path: Path) -> Path:
    """quackd's repository as codeload serves it: one folder named for the ref, holding this
    folder and the rest of the repository beside it."""
    path = tmp_path / "quackd.tar.gz"
    with tarfile.open(path, "w:gz") as tar:
        for name in ("quackd-trial", "quackd-trial/quackd", "quackd-trial/hackathon"):
            folder = tarfile.TarInfo(name)
            folder.type, folder.mode = tarfile.DIRTYPE, 0o755
            tar.addfile(folder)
        for name in (
            "quackd-trial/README.md",
            "quackd-trial/quackd/__init__.py",
            "quackd-trial/hackathon/nebius-nvidia/pyproject.toml",
            "quackd-trial/hackathon/nebius-nvidia/src/passport_nebius/__init__.py",
        ):
            data = f"{name}\n".encode()
            file = tarfile.TarInfo(name)
            file.size, file.mode = len(data), 0o644
            tar.addfile(file, io.BytesIO(data))
    return path


def _container(
    tmp_path: Path, vllm: str, *, ready_within: int = 60
) -> tuple[Path, subprocess.CompletedProcess[str]]:
    work = tmp_path / "work"
    env = {
        "STUB_DIR": tmp_path.as_posix(),
        "STUB_TARBALL": _tarball(tmp_path).as_posix(),
        "STUB_VLLM": vllm,
        "DOORSTEP_WORK": work.as_posix(),
        "ONBOARD_READY_WITHIN": str(ready_within),
        "NEBIUS_API_KEY": "token-factory-key",
        "NEBIUS_MODEL": TEXT_MODEL,
        "ONBOARD_MODEL": ONBOARD_MODEL,
        "ONBOARD_VLLM_ARGS": " ".join(FLAGS),
        "QUACKD_BRANCH": "trial",
    }
    return work, _run("doorstep-job.sh", [], env, _stubs(tmp_path, CONTAINER))


def _uv(tmp_path: Path) -> list[str]:
    path = tmp_path / "uv.txt"
    return path.read_text(encoding="utf-8").splitlines() if path.is_file() else []


@needs_bash
def test_the_job_serves_the_postmans_brain_then_plays_both_acts_on_it(tmp_path: Path) -> None:
    work, result = _container(tmp_path, "serve")
    assert result.returncode == 0, result.stdout + result.stderr
    # only this folder came from quackd's repository
    assert (work / "folder" / "pyproject.toml").is_file()
    assert (work / "folder" / "src" / "passport_nebius" / "__init__.py").is_file()
    assert (
        not (work / "folder" / "README.md").exists() and not (work / "folder" / "quackd").exists()
    )
    lines = (tmp_path / "vllm.txt").read_text(encoding="utf-8").splitlines()
    served = dict(line.split("=", 1) for line in lines)
    tail = f"--served-model-name {ONBOARD_MODEL} --host 127.0.0.1 --port 8000 {' '.join(FLAGS)}"
    assert served["args"].startswith("serve ") and served["args"].endswith(tail)
    assert served["cwd"].endswith("/work/model"), "started from the model's own folder"
    assert served["nebius"] == "", "the Token Factory key reached the postman's brain"
    key = served["key"]
    assert re.fullmatch(r"[0-9a-f]{48}", key), f"a key made for this job, not {key!r}"
    calls = _uv(tmp_path)
    onboard = f"--onboard http://127.0.0.1:8000/v1 --onboard-model {ONBOARD_MODEL} --print-logs"
    assert [call.split(" | ")[0] for call in calls] == [
        "sync --frozen --quiet",
        "run passport-nebius models",
        "run passport-nebius doorstep run --role duck --show --act appointment",
        f"run passport-nebius doorstep run --act stranger {onboard}",
        f"run passport-nebius doorstep run --act appointment {onboard} --pause-after 20",
    ]
    for act in calls[-2:]:
        assert act.endswith(f" | key={key} | nebius=set"), "each act holds both robots' keys"
    assert "vLLM serves" in result.stdout and "== vLLM's last lines ==" in result.stdout


@needs_bash
def test_a_brain_that_dies_ends_the_job_before_any_act(tmp_path: Path) -> None:
    _work, result = _container(tmp_path, "die")
    assert result.returncode == 1
    assert "vLLM ended before it was ready" in result.stdout
    assert not any(" --act stranger " in call for call in _uv(tmp_path))


@needs_bash
def test_a_brain_never_ready_ends_the_job_at_its_deadline(tmp_path: Path) -> None:
    _work, result = _container(tmp_path, "hang", ready_within=1)
    assert result.returncode == 1
    assert "vLLM was not ready within 1 s" in result.stdout
    assert not any(" --act stranger " in call for call in _uv(tmp_path))


def test_what_the_job_runs_exists() -> None:
    running = (DEPLOY / "doorstep-job.sh").read_text(encoding="utf-8")
    assert "passport-nebius doorstep run --act" in running and "--print-logs" in running
    shown = runner.invoke(app, ["doorstep", "run", "--help"]).output
    for flag in ("--print-logs", "--act", "--onboard", "--onboard-model", "--pause-after"):
        assert flag in shown, flag


def test_a_jobs_log_gets_both_runs_the_postman_first(tmp_path: Path) -> None:
    (tmp_path / "duck-01.log").write_text("the duck heard it\n", encoding="utf-8")
    (tmp_path / "postman-01.log").write_text("the postman told it\n", encoding="utf-8")
    assert doorstep.logged(tmp_path) == [
        ("postman-01", "the postman told it\n"),
        ("duck-01", "the duck heard it\n"),
    ]
    assert doorstep.logged(tmp_path / "nothing-here") == []


def test_print_logs_needs_the_logs_only_role_both_writes(tokenfactory: FakeTokenFactory) -> None:
    args = ["doorstep", "run", "--role", "duck", "--print-logs", "--model", TEXT_MODEL]
    result = runner.invoke(app, args)
    assert result.exit_code == 1
    assert "--print-logs prints the log files that only --role both writes" in result.output
