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
from . import explain as explaining
from . import whois as seeing
from .pilot import run as run_quackd
from .pilot import run_launch, unpriced

app = typer.Typer(
    name="passport-nebius",
    help="Robot Passport with NVIDIA Nemotron on Nebius Token Factory: quackd's hackathon "
    "edition. Every model call goes to Token Factory, and every id is read from its live list.",
    no_args_is_help=True,
    add_completion=False,
    rich_markup_mode=None,
    pretty_exceptions_show_locals=False,
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
        console.print(Text(printable(launch.shown())), soft_wrap=True)
        return
    _notes(chosen, list(ctx.args), vision)
    raise typer.Exit(run_quackd(launch))


@app.command()
def explain(
    source: str = typer.Argument(
        ...,
        help="Where the robot's passport is: a URL, a HOST[:PORT], a picture of its QR "
        "sticker, or a file.",
    ),
    question: str = typer.Option(
        explaining.DEFAULT_QUESTION, "--ask", "-q", help="What you want to know about the robot."
    ),
    model: str | None = MODEL_OPTION,
    registry_dir: str | None = typer.Option(
        None,
        "--registry-dir",
        help="Whose pins decide how far the robot is trusted (default: $QUACKD_REGISTRY_DIR or "
        "~/.quackd).",
    ),
    base_url: str | None = BASE_URL_OPTION,
) -> None:
    """Read a robot's passport the way quackd reads one, then ask Nemotron about it.

    How far the robot is trusted is quackd's decision, printed first. A stranger's own words
    never reach the model: you read them below its answer, as the robot's claim."""
    from quackd.passport.errors import PassportError
    from quackd.passport_store import PassportStore
    from quackd.passport_trust import judge

    settings = _settings(base_url)
    chosen = _chosen(settings, model)
    try:
        reading = explaining.read(explaining.find(source))
    except PassportError as e:
        fail(f"refused under {e.rule}: {e}")
    except OSError as e:
        fail(f"could not read {source}: {e}")
    judgement = judge(reading, PassportStore(registry_dir))
    view = explaining.model_view(reading, judgement.tier)
    console.print(
        Text(printable(f"{view['name']} ({reading.short_id or 'no key'})"), style="bold")
        + Text(printable(f"  {judgement.tier}: {judgement.why}"))
    )
    for warning in judgement.warnings:
        warn(warning)
    try:
        said = explaining.answer(settings, chosen.id, view, judgement.why, question)
    except nebius.NebiusError as e:
        fail(str(e))
    console.print(Text(printable(f"{chosen.id} on Token Factory:"), style="dim"))
    console.print(Text(printable(said)))
    heading = (
        "in its own words, which the model never saw"
        if judgement.tier == "stranger"
        else "in its own words"
    )
    console.print(Text(f"{heading} (a claim, not a fact):", style="dim"))
    for label, words in explaining.own_words(reading):
        console.print(Text(printable(f"  {label}: {words}")))


@app.command()
def whois(
    meet: list[str] = typer.Option(
        [],
        "--meet",
        help="A passport to compare: a URL, a HOST[:PORT], a picture of its QR sticker, or a "
        "file. Repeat it for each robot.",
    ),
    lan: float = typer.Option(
        0.0,
        "--lan",
        help="Also find the passports robots announce on this network, listening this many "
        "seconds. 0 does not look.",
    ),
    camera: int | None = typer.Option(
        None, "--camera", help="Take the frame from this camera on this machine (0 is the first)."
    ),
    image: Path | None = typer.Option(None, "--image", help="Or read the frame from a picture."),
    model: str | None = MODEL_OPTION,
    registry_dir: str | None = typer.Option(
        None,
        "--registry-dir",
        help="Whose pins decide each robot's tier (default: $QUACKD_REGISTRY_DIR or ~/.quackd).",
    ),
    base_url: str | None = BASE_URL_OPTION,
) -> None:
    """Which passport belongs to which robot in view, by an NVIDIA vision model on Token Factory.

    A signature proves who wrote a passport, never which body holds its key. This compares a
    camera frame with the bodies the passports declare. It is advice for a person: it pins
    nothing, stamps nothing and changes no tier."""
    from quackd.passport.errors import PassportError
    from quackd.passport_store import PassportStore
    from quackd.passport_trust import judge

    if (camera is None) == (image is None):
        fail("one picture: --camera N or --image FILE")
    settings = _settings(base_url)
    chosen = _chosen(settings, model)
    if chosen.takes_images is False:
        fail(
            f"{chosen.id} does not read images, by its own modality on the list: pick one "
            "passport-nebius models says reads images"
        )
    if chosen.takes_images is None:
        warn(f"the model list does not say whether {chosen.id} reads images")
    sources = list(meet)
    if lan > 0:
        from quackd.lan.passports import discover_passports

        sources += seeing.lan_sources(discover_passports(lan))
    if not sources:
        fail("no passport to compare: --meet SOURCE for each robot, or --lan SECONDS")
    readings = []
    for source in sources:
        try:
            readings.append(explaining.read(explaining.find(source)))
        except PassportError as e:
            warn(f"{source}: refused under {e.rule}: {e}")
        except OSError as e:
            warn(f"{source}: {e}")
    if not readings:
        fail("no passport could be read")
    refs = seeing.candidates(readings)
    try:
        if camera is not None:
            picture = seeing.from_camera(camera)
        else:
            assert image is not None
            picture = seeing.from_file(image)
        found = seeing.whois(settings, chosen.id, picture, refs)
    except nebius.NebiusError as e:
        fail(str(e))
    store = PassportStore(registry_dir)
    table = Table(title=f"who is who, by {printable(chosen.id)}")
    for column in ("in view", "where", "passport", "tier", "confidence", "why"):
        table.add_column(column, overflow="fold")
    matched = set()
    for match in found:
        passport, tier = "none fits", ""
        if match.ref is not None:
            reading = readings[int(match.ref[1:]) - 1]
            matched.add(match.ref)
            passport = f"{reading.name} ({reading.short_id or 'no key'})"
            tier = judge(reading, store).tier
        why = match.why + (f" (it {match.dropped})" if match.dropped else "")
        table.add_row(
            Text(printable(match.seen)),
            Text(printable(match.where)),
            Text(printable(passport)),
            tier,
            match.confidence,
            Text(printable(why)),
        )
    console.print(table)
    unseen = [r for ref, r in zip(refs, readings, strict=True) if ref not in matched]
    if unseen:
        names = ", ".join(f"{r.name} ({r.short_id or 'no key'})" for r in unseen)
        console.print(Text(printable(f"no robot in view fits: {names}")))
    console.print(
        Text(
            "advice from a camera and a model: nothing was pinned, stamped or re-tiered",
            style="dim",
        )
    )


doorstep_app = typer.Typer(
    help="The doorstep: a postman robot and a household robot that have never met agree how a "
    "letter changes hands by "
    "Robot Passport, each piloted by Nemotron on Token Factory.",
    no_args_is_help=True,
    rich_markup_mode=None,
    pretty_exceptions_show_locals=False,
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
    console.print(Text(f"ready for the {act} act in {state.as_posix()}", style="dim"))


@doorstep_app.command("run")
def doorstep_run(
    act: str = ACT_OPTION,
    role: str = typer.Option(
        "both",
        "--role",
        help="both: the two runs here, their output to log files. postman or duck: one run in "
        "this terminal, for two terminals side by side, the postman's started first.",
    ),
    model: str | None = MODEL_OPTION,
    postman_at: str | None = typer.Option(
        None,
        "--postman-at",
        help="HOST:PORT where the postman serves its passport (default: this machine's LAN "
        "address, port 8790). It must be the address the postman's run names itself by.",
    ),
    vision: bool = VISION_OPTION,
    pause_after: float = typer.Option(
        0.0,
        "--pause-after",
        help="Seconds after the duck starts at which the postman asks it for a pause; 0 never.",
    ),
    pause_seconds: int = typer.Option(5, "--pause-seconds", help="How long that pause is."),
    show: bool = typer.Option(False, "--show", help="Print the quackd commands and stop."),
    print_logs: bool = typer.Option(
        False,
        "--print-logs",
        help="With --role both, print each run's log once the act ends, for a place where "
        "nobody reads the files, such as a Serverless Job's log.",
    ),
    state: Path = STATE_OPTION,
    base_url: str | None = BASE_URL_OPTION,
) -> None:
    """Play one act of the doorstep with both pilots on Token Factory."""
    from quackd.lan.announce import local_ip

    if pause_after > 0 and role == "postman":
        fail("--pause-after pauses the duck, which runs here only with --role both or duck")
    if print_logs and role != "both":
        fail("--print-logs prints the log files that only --role both writes")
    settings = _settings(base_url)
    chosen = _chosen(settings, model)
    here = local_ip()
    postman_at = postman_at or f"{here}:{doorstep.POSTMAN.port}"
    try:
        started = doorstep.launches(
            settings, chosen, state, act, role, postman_at=postman_at, vision=vision
        )
    except nebius.NebiusError as e:
        fail(str(e))
    if show:
        for robot, launch in started:
            console.print(Text(printable(f"{robot.name}: {launch.shown()}")), soft_wrap=True)
        return
    try:
        if role in ("both", "postman"):
            for line in doorstep.setup(state, act):
                console.print(Text(printable(line)))
        elif not doorstep.is_set_up(state):
            fail(
                f"nothing is set up in {state.as_posix()}: "
                f"passport-nebius doorstep setup --act {act}"
            )
    except nebius.NebiusError as e:
        fail(str(e))
    _notes(chosen, [], vision)
    timers: list[threading.Timer] = []

    def ask_for_pause() -> None:
        duck_at = f"{here}:{doorstep.DUCK.port}"
        try:
            # counted from the duck's start, which a slow start can outlast
            doorstep.served(duck_at)
        except nebius.NebiusError as e:
            warn(f"the postman asked for no pause: {e}")
            return
        result = doorstep.pause(state, duck_at, pause_seconds)
        answer = doorstep.outcome(result, marker=" asked ")
        console.print(Text(printable(f"the postman asked for a {pause_seconds} s pause: {answer}")))

    def duck_started() -> None:
        if pause_after > 0:
            timer = threading.Timer(pause_after, ask_for_pause)
            timer.daemon = True
            timer.start()
            timers.append(timer)

    logs = state / "logs" if role == "both" else None
    if logs is not None:
        console.print(Text(f"each run's output goes to {logs.as_posix()}", style="dim"))
    try:
        codes = doorstep.play(
            started, postman_at=postman_at, logs=logs, on_duck_started=duck_started
        )
    except nebius.NebiusError as e:
        fail(str(e))
    finally:
        for timer in timers:
            timer.cancel()
        if print_logs and logs is not None:
            for name, text in doorstep.logged(logs):
                console.print(Text(f"== {name}'s run ==", style="bold"))
                for line in text.splitlines():
                    console.print(Text(printable(line)), soft_wrap=True)
    for name, code in codes.items():
        console.print(Text(f"{name}: its run ended with {code}"))
    console.print(
        Text(
            f"the runs, transcripts and pictures are in {(state / 'runs').as_posix()}", style="dim"
        )
    )
    if any(codes.values()):
        raise typer.Exit(1)


@doorstep_app.command("pause")
def doorstep_pause(
    seconds: int = typer.Option(5, "--seconds", help="How long a pause to ask for."),
    duck_at: str | None = typer.Option(
        None,
        "--duck-at",
        help="HOST:PORT of the duck's passport (default: this machine, port 8791).",
    ),
    state: Path = STATE_OPTION,
) -> None:
    """Ask the duck for a pause, as the postman: the protocol's robot-to-robot brake.

    A peer can ask for a pause, which the duck's owner policy grants or refuses by fixed
    code, and a granted pause brakes the body at once. No peer can make a robot stop outright."""
    from quackd.lan.announce import local_ip

    address = duck_at or f"{local_ip()}:{doorstep.DUCK.port}"
    result = doorstep.pause(state, address, seconds)
    for line in (result.stdout + result.stderr).strip().splitlines():
        console.print(Text(printable(line)))
    if result.returncode != 0:
        raise typer.Exit(result.returncode)


def main() -> None:
    """The console script: a .env beside the command is read first, as quackd reads one."""
    load_dotenv(Path.cwd() / ".env")
    app()
