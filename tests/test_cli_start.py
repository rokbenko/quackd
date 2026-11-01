"""What a command waits for before it does anything.

Help needs none of numpy, asyncio, pydantic or YAML, and they were most of what it waited for. Every
real command still gets them before it runs, for a reason that has nothing to do with speed: on
Windows a DLL loading behind a read pending on a pipe waits for the next line, so nothing may first
load them after a command has started reading stdin (`front._waiting_reader`)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from quackd import cli, preflight, preflight_defaults

REPO = Path(__file__).resolve().parents[1]
HEAVY = ("numpy", "asyncio", "pydantic", "yaml", "quackd.preflight")


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
        "import json, sys\nimport quackd.cli\n"
        f"print(json.dumps({{m: m in sys.modules for m in {HEAVY!r}}}))"
    )
    assert not any(loaded.values()), f"quackd.cli pulled in {loaded}"


@pytest.mark.parametrize(
    "argv", [["--help"], ["run", "--help"], ["doctor", "--help"], ["--version"], []]
)
def test_asking_for_help_loads_none_of_it(argv: list[str]) -> None:
    script = (
        "import json, sys\nimport quackd.cli\n"
        "sys.argv = ['quackd', *sys.argv[1:]]\n"
        "try:\n    quackd.cli.main()\nexcept SystemExit:\n    pass\n"
        f"print('\\n' + json.dumps({{m: m in sys.modules for m in {HEAVY!r}}}))"
    )
    loaded = _inside(script, *argv)
    assert not any(loaded.values()), f"quackd {' '.join(argv)} pulled in {loaded}"


@pytest.mark.parametrize(
    "argv", [["doctor"], ["run", "hello-world"], ["serve-mcp"], ["list-verbs"], ["drive"]]
)
def test_a_real_command_has_all_of_it_before_the_command_starts(argv: list[str]) -> None:
    """Measured where it matters: at the moment `app()` would begin, which is before anything can
    read stdin. `app` is replaced so that nothing actually runs."""
    script = (
        "import json, sys\nimport quackd.cli\n"
        "sys.argv = ['quackd', *sys.argv[1:]]\n"
        "seen = {}\n"
        f"quackd.cli.app = lambda: seen.update({{m: m in sys.modules for m in {HEAVY!r}}})\n"
        "quackd.cli.main()\n"
        "print(json.dumps(seen))"
    )
    loaded = _inside(script, *argv)
    assert set(loaded) == set(HEAVY) and all(loaded.values()), loaded


@pytest.mark.parametrize(
    ("argv", "only_help"),
    [
        ([], True),
        (["--help"], True),
        (["--version"], True),
        (["-V"], True),
        (["run", "--help"], True),
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
    monkeypatch.delenv("_QUACKD_COMPLETE", raising=False)
    assert cli._asks_only_for_help(argv) is only_help


def test_the_shell_completing_a_word_skips_them_too(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("_QUACKD_COMPLETE", "complete_bash")
    assert cli._asks_only_for_help(["run"]) is True


def test_preflights_defaults_are_still_where_they_were() -> None:
    assert preflight.DEFAULT_SEEDS == preflight_defaults.DEFAULT_SEEDS == 3
    assert preflight.DEFAULT_CONNECT_CYCLES == preflight_defaults.DEFAULT_CONNECT_CYCLES == 3
