"""What a command waits for before it does anything.

Help needs none of numpy, asyncio, pydantic or YAML, and they were over a third of what it waited
for. Typer 0.29 loads pydantic for a command's help by itself, which Roboglot cannot spare it, so
help is held to loading none of them that Typer's own help does not. Every real command still gets
them before it runs, for a reason that has nothing to do with speed: on Windows a DLL loading
behind a read pending on a pipe waits for the next line, so nothing may first load them after a
command has started reading stdin (`front._waiting_reader`)."""

from __future__ import annotations

import functools
import json
import subprocess
import sys
from pathlib import Path

import pytest

from roboglot import cli

REPO = Path(__file__).resolve().parents[1]
HEAVY = ("numpy", "asyncio", "pydantic", "yaml")


def _inside(script: str, *argv: str) -> dict:
    """Run `script` in a fresh interpreter, whose last line of output is the JSON it reports."""
    done = subprocess.run(
        [sys.executable, "-c", script, *argv],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=REPO,
        timeout=120,
    )
    assert done.returncode == 0, done.stderr[-1500:]
    return json.loads(done.stdout.strip().splitlines()[-1])


def test_importing_the_cli_loads_none_of_what_only_a_command_needs() -> None:
    loaded = _inside(
        "import json, sys\nimport roboglot.cli\n"
        f"print(json.dumps({{m: m in sys.modules for m in {HEAVY!r}}}))"
    )
    assert not any(loaded.values()), f"roboglot.cli pulled in {loaded}"


CONTROL = (
    "import json, sys\nimport typer\n"
    "app = typer.Typer()\n"
    "@app.callback()\ndef root(name: str = 'x') -> None:\n    pass\n"
    "@app.command()\ndef command(count: int = 1) -> None:\n    pass\n"
    "sys.argv = ['control', *sys.argv[1:]]\n"
    "try:\n    app()\nexcept SystemExit:\n    pass\n"
    f"print('\\n' + json.dumps({{m: m in sys.modules for m in {HEAVY!r}}}))"
)
"""A Typer app of two options, one before its command and one after, which loads nothing of its
own: what its help loads, Typer loaded."""


@functools.cache
def _typer_alone(argv: tuple[str, ...]) -> frozenset[str]:
    """What of `HEAVY` Typer itself loads to print the help `argv` asks for, the control's own or
    its command's. None of it on Typer 0.28. Typer 0.29 checks a value with pydantic, so a
    command's help, which reads the root's options first, loads pydantic whoever asks for it."""
    return frozenset(m for m, on in _inside(CONTROL, *argv).items() if on)


@pytest.mark.parametrize(
    "argv",
    [["--help"], ["-h"], ["run", "--help"], ["run", "-h"], ["doctor", "--help"], ["--version"], []],
)
def test_asking_for_help_loads_none_of_it(argv: list[str]) -> None:
    """None of it beyond what Typer's own help loads, in a control app asked the same way."""
    script = (
        "import json, sys\nimport roboglot.cli\n"
        "sys.argv = ['roboglot', *sys.argv[1:]]\n"
        "try:\n    roboglot.cli.main()\nexcept SystemExit:\n    pass\n"
        f"print('\\n' + json.dumps({{m: m in sys.modules for m in {HEAVY!r}}}))"
    )
    loaded = _inside(script, *argv)
    asks_a_command = bool(argv) and not argv[0].startswith("-")
    typer_alone = _typer_alone(("command", "--help") if asks_a_command else ("--help",))
    beyond = sorted(m for m, on in loaded.items() if on and m not in typer_alone)
    assert not beyond, (
        f"roboglot {' '.join(argv)} pulled in {beyond}, which Typer's own help, "
        f"loading {sorted(typer_alone)}, does not"
    )


@pytest.mark.parametrize(
    "argv", [["doctor"], ["run", "hello-world"], ["serve-mcp"], ["list-verbs"], ["drive"]]
)
def test_a_real_command_has_all_of_it_before_the_command_starts(argv: list[str]) -> None:
    """Measured where it matters: at the moment `app()` would begin, which is before anything can
    read stdin. `app` is replaced so that nothing actually runs."""
    script = (
        "import json, sys\nimport roboglot.cli\n"
        "sys.argv = ['roboglot', *sys.argv[1:]]\n"
        "seen = {}\n"
        f"roboglot.cli.app = lambda: seen.update({{m: m in sys.modules for m in {HEAVY!r}}})\n"
        "roboglot.cli.main()\n"
        "print(json.dumps(seen))"
    )
    loaded = _inside(script, *argv)
    assert set(loaded) == set(HEAVY) and all(loaded.values()), loaded


@pytest.mark.parametrize(
    ("argv", "only_help"),
    [
        ([], True),
        (["--help"], True),
        (["-h"], True),
        (["--version"], True),
        (["-V"], True),
        (["run", "--help"], True),
        (["run", "-h"], True),
        (["policy", "endpoint", "--help"], True),
        (["run"], False),
        (["run", "hello-world", "--goal", "help me"], False),
        (["doctor"], False),
        (["serve-mcp"], False),
    ],
)
def test_only_an_invocation_that_can_only_print_skips_the_imports(
    argv: list[str], only_help: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("_ROBOGLOT_COMPLETE", raising=False)
    assert cli._asks_only_for_help(argv) is only_help


def test_the_shell_completing_a_word_skips_them_too(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("_ROBOGLOT_COMPLETE", "complete_bash")
    assert cli._asks_only_for_help(["run"]) is True
