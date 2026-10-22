"""`passport-nebius`, the edition's one command.

Nothing here prints with `print`: the repository's lint rules hold this folder to the same
console as quackd's own commands, so output goes through rich, and every string a server or a
robot wrote is escaped before a terminal sees it.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import NoReturn

import typer
from dotenv import load_dotenv
from rich.console import Console
from rich.table import Table
from rich.text import Text

from quackd.ui import printable

from . import __version__, nebius

app = typer.Typer(
    name="passport-nebius",
    help="Robot Passport with NVIDIA Nemotron on Nebius Token Factory: quackd's hackathon "
    "edition. Every model call goes to Token Factory, and every id is read from its live list.",
    no_args_is_help=True,
    add_completion=False,
    rich_markup_mode=None,
)
console = Console(highlight=False)
err = Console(stderr=True, highlight=False)

BASE_URL_OPTION = typer.Option(
    None,
    "--base-url",
    help=f"Token Factory's address (default: ${nebius.BASE_URL_ENV}, else "
    f"{nebius.DEFAULT_BASE_URL}).",
)


def fail(message: str) -> NoReturn:
    err.print(Text("error: ", style="bold red") + Text(printable(message)))
    raise typer.Exit(1)


def warn(message: str) -> None:
    err.print(Text("note: ", style="yellow") + Text(printable(message)))


def _yes_no(value: bool | None) -> str:
    return "not said" if value is None else ("yes" if value else "no")


@app.command()
def version() -> None:
    """Print this edition's version."""
    console.print(Text(f"passport-nebius {__version__}"))


@app.command()
def models(
    show_all: bool = typer.Option(
        False, "--all", help="Every model on the list, not only NVIDIA's."
    ),
    as_json: bool = typer.Option(False, "--json", help="The rows as JSON, for a script."),
    base_url: str | None = BASE_URL_OPTION,
) -> None:
    """List the models this key can use on Token Factory, NVIDIA's by default.

    Whether a model reads images comes from the list's own modality; what it costs is shown as
    the list states it, since Token Factory does not document the unit."""
    try:
        settings = nebius.Settings.from_env(base_url)
        rows = nebius.list_models(settings)
    except nebius.NebiusError as e:
        fail(str(e))
    shown = rows if show_all else nebius.nvidia_models(rows)
    if as_json:
        typer.echo(
            json.dumps(
                [
                    {
                        "id": m.id,
                        "name": m.name,
                        "modality": m.modality,
                        "takes_images": m.takes_images,
                        "context_length": m.context_length,
                        "features": list(m.features),
                        "pricing": dict(m.pricing),
                    }
                    for m in shown
                ],
                indent=2,
            )
        )
        return
    table = Table(title="Token Factory models" + ("" if show_all else ", NVIDIA's"))
    for column in ("id", "modality", "reads images", "context", "features"):
        table.add_column(column, overflow="fold")
    for m in shown:
        table.add_row(
            Text(printable(m.id)),
            Text(printable(m.modality or "not said")),
            _yes_no(m.takes_images),
            str(m.context_length) if m.context_length else "not said",
            Text(printable(", ".join(m.features) or "not said")),
        )
    console.print(table)
    console.print(Text(f"{len(shown)} shown of {len(rows)} on the list", style="dim"))
    if not shown and not show_all:
        warn("no NVIDIA model on this key's list: --all shows what is there")


def main() -> None:
    """The console script: a .env beside the command is read first, as quackd reads one."""
    load_dotenv(Path.cwd() / ".env")
    app()
