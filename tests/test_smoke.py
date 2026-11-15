"""The package imports and the CLI answers --help. Everything else builds on this."""

from typer.testing import CliRunner

import roboglot
from roboglot.cli import app


def test_version_string() -> None:
    assert roboglot.__version__.count(".") == 2


def test_cli_help() -> None:
    result = CliRunner().invoke(app, ["--help"])
    assert result.exit_code == 0
    for command in (
        "run",
        "validate",
        "doctor",
        "serve-mcp",
        "list-verbs",
        "list-adapters",
        "list-models",
        "record",
        "log",
        "discover",
        "announce",
        "memory",
        "robot",
        "team",
    ):
        assert command in result.output


def test_cli_version_flag() -> None:
    result = CliRunner().invoke(app, ["--version"])
    assert result.exit_code == 0
    assert roboglot.__version__ in result.output
