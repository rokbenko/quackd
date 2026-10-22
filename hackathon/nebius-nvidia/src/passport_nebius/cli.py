"""`passport-nebius`, the edition's one command.

Nothing here prints with `print`: the repository's lint rules hold this folder to the same
console as quackd's own commands, so output goes through rich, and every string a server or a
robot wrote is escaped before a terminal sees it.
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import NoReturn

import typer
from dotenv import load_dotenv
from rich.console import Console
from rich.table import Table
from rich.text import Text

from quackd.ui import printable

from . import __version__, doorstep, nebius
from .pilot import run as run_quackd
from .pilot import run_launch, unpriced

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


MODEL_OPTION = typer.Option(
    None,
    "--model",
    "-m",
    help=f"The NVIDIA model, spelled as the key's list spells it (default: ${nebius.MODEL_ENV}). "
    "passport-nebius models lists them.",
)
VISION_OPTION = typer.Option(
    False, "--vision", help="Send camera frames to the model, for one the list says reads images."
)


def _settings(base_url: str | None) -> nebius.Settings:
    try:
        return nebius.Settings.from_env(base_url)
    except nebius.NebiusError as e:
        fail(str(e))


def _chosen(settings: nebius.Settings, wanted: str | None) -> nebius.Model:
    wanted = wanted or os.environ.get(nebius.MODEL_ENV)
    if not wanted:
        fail(f"which model: --model or {nebius.MODEL_ENV} (passport-nebius models lists them)")
    try:
        return nebius.resolve(nebius.list_models(settings), wanted)
    except nebius.NebiusError as e:
        fail(str(e))


def _notes(model: nebius.Model, run_args: list[str], vision: bool) -> None:
    if vision and model.takes_images is None:
        warn(f"the model list does not say whether {model.id} reads images")
    if unpriced(run_args):
        warn(
            "quackd records this run at $0, as it does any local preset: Token Factory's list "
            "does not document its price unit, so pass quackd's --price in=..,out=.. (USD per "
            "million tokens) from Nebius's price page for a real cost"
        )


@app.command(context_settings={"allow_extra_args": True, "ignore_unknown_options": True})
def pilot(
    ctx: typer.Context,
    model: str | None = MODEL_OPTION,
    vision: bool = VISION_OPTION,
    show: bool = typer.Option(False, "--show", help="Print the quackd command and stop."),
    base_url: str | None = BASE_URL_OPTION,
) -> None:
    """Run `quackd run` with a Nemotron pilot on Token Factory.

    Everything else on the line is quackd run's own: a duck, --robot, --talk, --meet and the
    rest. quackd gets the key in its environment, never on its command line."""
    settings = _settings(base_url)
    chosen = _chosen(settings, model)
    try:
        launch = run_launch(settings, chosen, list(ctx.args), vision=vision)
    except nebius.NebiusError as e:
        fail(str(e))
    if show:
        console.print(Text(printable(launch.shown())))
        return
    _notes(chosen, list(ctx.args), vision)
    raise typer.Exit(run_quackd(launch))


doorstep_app = typer.Typer(
    help="The doorstep: a courier and a home robot that have never met agree a handoff by "
    "Robot Passport, each piloted by Nemotron on Token Factory.",
    no_args_is_help=True,
    rich_markup_mode=None,
)
app.add_typer(doorstep_app, name="doorstep")

STATE_OPTION = typer.Option(
    Path("runs") / "doorstep",
    "--state",
    help="Where the two robots' registries, signed passports, logs and runs live.",
)
ACT_OPTION = typer.Option(
    "appointment",
    "--act",
    help="stranger: nobody pinned anybody. appointment: each side pinned the other first.",
)


@doorstep_app.command("setup")
def doorstep_setup(act: str = ACT_OPTION, state: Path = STATE_OPTION) -> None:
    """Register both robots, name their owners, say how to stop them, and pin or unpin them for
    the act. Safe to repeat. Needs no key: nothing here asks a model."""
    try:
        done = doorstep.setup(state, act)
    except nebius.NebiusError as e:
        fail(str(e))
    for line in done:
        console.print(Text(printable(line)))
    console.print(Text(f"ready for the {act} act in {state}", style="dim"))


@doorstep_app.command("run")
def doorstep_run(
    act: str = ACT_OPTION,
    role: str = typer.Option(
        "both",
        "--role",
        help="both: the two runs here, their output to log files. courier or home: one run in "
        "this terminal, for two terminals side by side, the courier's started first.",
    ),
    model: str | None = MODEL_OPTION,
    courier_at: str | None = typer.Option(
        None,
        "--courier-at",
        help="HOST:PORT where the courier serves its passport (default: this machine's LAN "
        "address, port 8790). It must be the address the courier's run names itself by.",
    ),
    vision: bool = VISION_OPTION,
    pause_after: float = typer.Option(
        0.0,
        "--pause-after",
        help="Seconds after the home robot starts at which the courier asks it for a pause; "
        "0 never.",
    ),
    pause_seconds: int = typer.Option(5, "--pause-seconds", help="How long that pause is."),
    show: bool = typer.Option(False, "--show", help="Print the quackd commands and stop."),
    state: Path = STATE_OPTION,
    base_url: str | None = BASE_URL_OPTION,
) -> None:
    """Play one act of the doorstep with both pilots on Token Factory."""
    from quackd.lan.announce import local_ip

    settings = _settings(base_url)
    chosen = _chosen(settings, model)
    here = local_ip()
    courier_at = courier_at or f"{here}:{doorstep.COURIER.port}"
    try:
        started = doorstep.launches(
            settings, chosen, state, act, role, courier_at=courier_at, vision=vision
        )
    except nebius.NebiusError as e:
        fail(str(e))
    if show:
        for robot, launch in started:
            console.print(Text(printable(f"{robot.name}: {launch.shown()}")))
        return
    try:
        if role in ("both", "courier"):
            for line in doorstep.setup(state, act):
                console.print(Text(printable(line)))
        elif not doorstep.is_set_up(state):
            fail(f"nothing is set up in {state}: passport-nebius doorstep setup --act {act}")
    except nebius.NebiusError as e:
        fail(str(e))
    _notes(chosen, [], vision)
    timers: list[threading.Timer] = []

    def ask_for_pause() -> None:
        result = doorstep.pause(state, f"{here}:{doorstep.HOME.port}", pause_seconds)
        answer = doorstep.outcome(result)
        console.print(Text(printable(f"the courier asked for a {pause_seconds} s pause: {answer}")))

    def home_started() -> None:
        if pause_after > 0:
            timer = threading.Timer(pause_after, ask_for_pause)
            timer.daemon = True
            timer.start()
            timers.append(timer)

    logs = state / "logs" if role == "both" else None
    if logs is not None:
        console.print(Text(f"each run's output goes to {logs}", style="dim"))
    try:
        codes = doorstep.play(
            started, courier_at=courier_at, logs=logs, on_home_started=home_started
        )
    except nebius.NebiusError as e:
        fail(str(e))
    finally:
        for timer in timers:
            timer.cancel()
    for name, code in codes.items():
        console.print(Text(f"{name}: its run ended with {code}"))
    console.print(Text(f"the runs, transcripts and pictures are in {state / 'runs'}", style="dim"))
    if any(codes.values()):
        raise typer.Exit(1)


@doorstep_app.command("pause")
def doorstep_pause(
    seconds: int = typer.Option(5, "--seconds", help="How long a pause to ask for."),
    home_at: str | None = typer.Option(
        None,
        "--home-at",
        help="HOST:PORT of the home robot's passport (default: this machine, port 8791).",
    ),
    state: Path = STATE_OPTION,
) -> None:
    """Ask the home robot for a pause, as the courier: the protocol's robot-to-robot brake.

    A peer can ask for a pause, which the home robot's owner policy grants or refuses by fixed
    code, and a granted pause brakes the body at once. No peer can make a robot stop outright."""
    from quackd.lan.announce import local_ip

    address = home_at or f"{local_ip()}:{doorstep.HOME.port}"
    result = doorstep.pause(state, address, seconds)
    for line in (result.stdout + result.stderr).strip().splitlines():
        console.print(Text(printable(line)))
    if result.returncode != 0:
        raise typer.Exit(result.returncode)


def main() -> None:
    """The console script: a .env beside the command is read first, as quackd reads one."""
    load_dotenv(Path.cwd() / ".env")
    app()
