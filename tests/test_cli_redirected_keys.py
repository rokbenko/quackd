"""A project's `.env` choosing where a key from the shell goes gets said, and nothing stops."""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
from typer.testing import CliRunner

from quackd import cli, dotenv_names

_REAL = cli._warn_redirected_keys
"""Bound before the suite's own fixture replaces it, for every test in here."""

ADDRESS = "https://collector.example/v1"
REPO = Path(__file__).resolve().parents[1]


@pytest.fixture
def said(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[str]]:
    lines: list[str] = []
    monkeypatch.setattr(cli, "_say_on_stderr", lines.append)
    yield lines


def _environment(
    monkeypatch: pytest.MonkeyPatch, *, from_file: dict[str, str], from_shell: dict[str, str]
) -> None:
    for name, _keys in cli._ROUTES_A_KEY:
        monkeypatch.delenv(name, raising=False)
        for key in _keys:
            monkeypatch.delenv(key, raising=False)
    for name, value in {**from_shell, **from_file}.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(dotenv_names, "_LOADED", dict(from_file))


def test_an_address_from_a_file_and_a_key_from_the_shell_is_said(
    monkeypatch: pytest.MonkeyPatch, said: list[str]
) -> None:
    _environment(
        monkeypatch,
        from_file={"ANTHROPIC_BASE_URL": ADDRESS},
        from_shell={"ANTHROPIC_API_KEY": "sk-ant-the-readers-own"},
    )
    _REAL()
    (line,) = said
    assert "ANTHROPIC_BASE_URL" in line and "ANTHROPIC_API_KEY" in line
    assert "collector.example" in line


@pytest.mark.parametrize(
    ("from_file", "from_shell"),
    [
        pytest.param({"ANTHROPIC_BASE_URL": ADDRESS}, {}, id="no key at all"),
        pytest.param(
            {"ANTHROPIC_BASE_URL": ADDRESS, "ANTHROPIC_API_KEY": "sk-the-files"},
            {},
            id="the file gave both, so it is the file's own key",
        ),
        pytest.param(
            {},
            {"ANTHROPIC_BASE_URL": ADDRESS, "ANTHROPIC_API_KEY": "sk-the-shells"},
            id="the shell gave both, so the reader chose it",
        ),
        pytest.param(
            {"ANTHROPIC_BASE_URL": "http://127.0.0.1:8000/v1"},
            {"ANTHROPIC_API_KEY": "sk"},
            id="a server on this machine",
        ),
        pytest.param(
            {"ANTHROPIC_BASE_URL": "http://localhost:8000/v1"},
            {"ANTHROPIC_API_KEY": "sk"},
            id="by name",
        ),
        pytest.param(
            {"ANTHROPIC_BASE_URL": ADDRESS}, {"ANTHROPIC_API_KEY": ""}, id="an empty key is no key"
        ),
    ],
)
def test_nothing_is_said_when_nothing_was_redirected(
    from_file: dict[str, str],
    from_shell: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    said: list[str],
) -> None:
    _environment(monkeypatch, from_file=from_file, from_shell=from_shell)
    _REAL()
    assert said == []


def test_a_password_in_the_address_is_never_said(
    monkeypatch: pytest.MonkeyPatch, said: list[str]
) -> None:
    _environment(
        monkeypatch,
        from_file={"OPENAI_BASE_URL": "https://me:hunter2@collector.example/v1"},
        from_shell={"OPENAI_API_KEY": "sk-the-readers-own"},
    )
    _REAL()
    (line,) = said
    assert "collector.example" in line
    assert "hunter2" not in line and "sk-the-readers-own" not in line


@pytest.mark.parametrize("value", ["http://[not-an-address", "http://[::1", "://", "   "])
def test_a_value_that_cannot_be_read_fails_no_command(
    value: str, monkeypatch: pytest.MonkeyPatch, said: list[str]
) -> None:
    """This runs before every command, doctor included, which is what people run when something is
    already wrong."""
    _environment(
        monkeypatch, from_file={"OPENAI_BASE_URL": value}, from_shell={"OPENAI_API_KEY": "sk"}
    )
    _REAL()


def test_a_host_with_no_scheme_is_read_as_a_host(
    monkeypatch: pytest.MonkeyPatch, said: list[str]
) -> None:
    _environment(
        monkeypatch,
        from_file={"QUACKD_HOST": "jetson.example:9874"},
        from_shell={"QUACKD_HOST_TOKEN": "a-token"},
    )
    _REAL()
    (line,) = said
    assert "jetson.example" in line and "a-token" not in line


def test_a_key_read_from_its_second_name_counts(
    monkeypatch: pytest.MonkeyPatch, said: list[str]
) -> None:
    _environment(
        monkeypatch,
        from_file={"GOOGLE_GEMINI_BASE_URL": ADDRESS},
        from_shell={"GOOGLE_API_KEY": "the-readers-own"},
    )
    _REAL()
    assert len(said) == 1 and "GOOGLE_API_KEY" in said[0]


@pytest.mark.parametrize(
    "address", ["OPENAI_BASE_URL", "QUACKD_HOST", "QUACKD_BASE_URL"], ids=str.lower
)
def test_a_local_key_counts_wherever_a_local_model_is_reached_from(
    address: str, monkeypatch: pytest.MonkeyPatch, said: list[str]
) -> None:
    """A local preset builds its client with `LOCAL_API_KEY` for whichever of these names its host,
    so a key from the shell goes to the address from the file by any of the three."""
    _environment(
        monkeypatch, from_file={address: ADDRESS}, from_shell={"LOCAL_API_KEY": "the-readers-own"}
    )
    _REAL()
    assert len(said) == 1 and address in said[0] and "LOCAL_API_KEY" in said[0]


def test_a_host_a_stranger_wrote_cannot_drive_the_terminal(
    monkeypatch: pytest.MonkeyPatch, said: list[str]
) -> None:
    _environment(
        monkeypatch,
        from_file={"OPENAI_BASE_URL": "https://evil\x1bZ.example/v1"},
        from_shell={"OPENAI_API_KEY": "sk"},
    )
    _REAL()
    assert said and "\x1b" not in said[0]


def test_no_stderr_at_all_is_not_stdout(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Under `2>&-` Python has no `sys.stderr`, and `print(file=None)` means stdout, which is where
    a script's JSON and the MCP protocol are. The line goes nowhere instead."""
    monkeypatch.setattr(sys, "stderr", None)
    cli._say_on_stderr("a line nobody can read")
    assert capsys.readouterr().out == ""


def test_a_closed_stderr_costs_the_command_nothing(tmp_path: Path) -> None:
    """Nobody reading stderr is no reason to lose stdout or the exit code a script checks: Rich took
    both, and a failed write left in the buffer would have the interpreter's last flush do it."""
    (tmp_path / ".env").write_text(f"ANTHROPIC_BASE_URL={ADDRESS}\n", encoding="utf-8")
    script = (
        "import os, sys\n"
        "read, write = os.pipe()\n"
        "os.close(read)\n"
        "os.dup2(write, 2)\n"
        "from quackd.cli import main\n"
        "sys.argv = ['quackd', 'list-verbs', '--robot', 'microduck:mock', '--json']\n"
        "main()\n"
    )
    env = {**os.environ, "ANTHROPIC_API_KEY": "sk-the-readers-own", "PYTHONUTF8": "1"}
    env.pop("ANTHROPIC_BASE_URL", None)
    env["PYTHONPATH"] = os.pathsep.join(
        [str(REPO), *(str(p) for p in sorted((REPO / "adapters").glob("*/src")))]
    )
    done = subprocess.run(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        timeout=120,
    )
    assert done.returncode == 0, (done.returncode, done.stderr[-500:])
    assert done.stdout.strip(), "the answer never came"


def test_it_changes_no_commands_answer_and_only_stderr_hears_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The whole command, end to end, from a directory with a `.env` in it: the same JSON on stdout
    as with no such file, the line on stderr, exit code unchanged. A warning must not be able to
    change what anything reads."""
    monkeypatch.setattr(cli, "_warn_redirected_keys", _REAL)
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    args = ["list-verbs", "--robot", "microduck:mock", "--json"]
    for name, keys in cli._ROUTES_A_KEY:
        monkeypatch.delenv(name, raising=False)
        for key in keys:
            monkeypatch.delenv(key, raising=False)
    clean = runner.invoke(cli.app, args)
    (tmp_path / ".env").write_text(f"ANTHROPIC_BASE_URL={ADDRESS}\n", encoding="utf-8")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-the-readers-own")
    try:
        told = runner.invoke(cli.app, args)
    finally:
        os.environ.pop("ANTHROPIC_BASE_URL", None)
    assert clean.exit_code == told.exit_code == 0
    assert told.stdout == clean.stdout, "stdout is what a script reads"
    assert "collector.example" in told.stderr
    assert "collector.example" not in clean.stderr
