"""The command line is the product's front door.

`uvx quackd run find-and-kick --provider anthropic --robot microduck:sim2d` is the
north-star demo; every command here exists to make that line, and the debugging around it,
boring. Commands are thin: they parse, load `.env`, wire objects together, and hand off.
"""

from __future__ import annotations

import asyncio
import contextlib
import glob
import sys
from typing import Any

import typer
from dotenv import load_dotenv
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from quackd import __version__

app = typer.Typer(
    name="quackd",
    help="Give your small robot a brain. Any LLM, one .duck file. 🦆🧠",
    no_args_is_help=True,
    rich_markup_mode="rich",
)


def _tolerate_narrow_encodings() -> None:
    """Stop a non-UTF-8 stdout turning quackd's own output into a crash.

    Windows uses the ANSI codepage when Python writes to a pipe, and quackd prints ✓ and 🦆 and
    the status emoji in `doctor`. On cp1252 those raise UnicodeEncodeError and take the command
    with them — `quackd doctor` and `quackd validate`, which are the first two commands
    docs/microduck-hardware-checklist.md puts in front of a Windows user, and the last step of
    CI's own Windows job. Replacing what the codepage cannot carry costs a glyph; raising costs
    the command.
    """
    for stream in (sys.stdout, sys.stderr):
        encoding = (getattr(stream, "encoding", "") or "").lower().replace("-", "")
        if encoding.startswith("utf") or not hasattr(stream, "reconfigure"):
            continue
        with contextlib.suppress(Exception):
            stream.reconfigure(errors="replace")


_tolerate_narrow_encodings()
console = Console()
err_console = Console(stderr=True)


def _version_callback(value: bool) -> None:
    if value:
        console.print(f"quackd {__version__}")
        raise typer.Exit()


@app.callback()
def _main(
    version: bool = typer.Option(
        False, "--version", "-V", callback=_version_callback, is_eager=True, help="Show version."
    ),
) -> None:
    """quackd — pilot a small robot (real or simulated) with any LLM."""
    load_dotenv()


def _expand(patterns: list[str]) -> list[str]:
    out: list[str] = []
    for pat in patterns:
        matches = sorted(glob.glob(pat))
        out.extend(matches if matches else [pat])
    return out


def _fail(msg: str, code: int = 1) -> None:
    # escape: messages contain things like quackd[anthropic], which Rich would eat as markup
    err_console.print(f"[red]error:[/red] {escape(msg)}")
    raise typer.Exit(code=code)


def _robot_specs(robot: str | None, robots: str | None, duck: Any) -> list:
    """The robots a command talks about: --robots, else --robot, else the duck's own
    `robots:` default, else the Microduck simulator."""
    from quackd.adapters.factory import RobotSpec, parse_robot_spec, parse_robots, resolve_robot

    if robots:
        return parse_robots(robots)
    default = duck.frontmatter.robots if duck is not None else None
    if isinstance(default, dict):
        if robot:
            return [resolve_robot(robot)]
        # the member names become the robot ids, as `--robots name=spec` would make them
        specs = []
        for name, text in default.items():
            parsed = parse_robot_spec(text)
            specs.append(RobotSpec(parsed.adapter, parsed.backend, name))
        return specs
    return [resolve_robot(robot, duck_default=default)]


# ── validate ────────────────────────────────────────────────────────────────────────────


@app.command()
def validate(
    duckfiles: list[str] = typer.Argument(..., help=".duck files, globs, or bundled names."),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Only print failures."),
    robot: list[str] | None = typer.Option(
        None,
        "--robot",
        "-r",
        help="Check the files against this robot's manifest (<adapter>:<backend>; repeatable).",
    ),
    robots: str | None = typer.Option(
        None, "--robots", help="Check against a fleet: name=<adapter>:<backend>,..."
    ),
) -> None:
    """Validate .duck files against the spec and a robot's verbs. Exits 1 on any failure."""
    from quackd.adapters.base import AdapterError
    from quackd.adapters.factory import describe, parse_robot_spec
    from quackd.duckfile.parser import DuckParseError, load_duck
    from quackd.duckfile.validate import validate_duck
    from quackd.verbs.registry import default_registry

    registry = default_registry()
    table = Table(title="quackd validate", show_lines=False)
    table.add_column("file")
    table.add_column("name")
    table.add_column("verbs", justify="right")
    table.add_column("result")
    failures = 0
    details: list[str] = []  # one plain line per problem, so long messages survive any width
    for path in _expand(duckfiles):
        try:
            duck = load_duck(path)
        except DuckParseError as e:
            failures += 1
            table.add_row(path, "—", "—", f"[red]✗ {escape(e.reason)}[/red]")
            details.append(f"{path}: {e.reason}")
            continue
        try:
            if robot or robots:
                specs = (
                    [parse_robot_spec(r) for r in robot]
                    if robot
                    else _robot_specs(None, robots, duck)
                )
            elif duck.frontmatter.robots is not None:
                specs = _robot_specs(None, None, duck)
            else:
                specs = []
            manifests = [describe(spec) for spec in specs]
        except AdapterError as e:
            failures += 1
            table.add_row(path, duck.name, "—", f"[red]✗ {escape(str(e))}[/red]")
            continue
        problems = validate_duck(duck, manifests, registry=registry)
        if problems:
            failures += 1
            table.add_row(
                path,
                duck.name,
                str(len(duck.frontmatter.verbs.allow)),
                "[red]✗ " + escape("; ".join(p.message for p in problems)) + "[/red]",
            )
            details.extend(f"{path}: {p}" for p in problems)
            continue
        if not quiet:
            verdict = "[green]✓ valid[/green]"
            if duck.frontmatter.flock is not None:
                verdict = (
                    f"[green]✓ valid (flock of {len(duck.frontmatter.flock.member_names)})[/green]"
                )
            if manifests:
                verdict += f" [dim]for {', '.join(m.id for m in manifests)}[/dim]"
            table.add_row(path, duck.name, str(len(duck.frontmatter.verbs.allow)), verdict)
    if not quiet or failures:
        console.print(table)
    if failures:
        for line in details:
            console.print(escape(line), soft_wrap=True)
        raise typer.Exit(code=1)
    console.print(f"[green]{len(_expand(duckfiles))} file(s) valid.[/green]")


# ── list-verbs ──────────────────────────────────────────────────────────────────────────


@app.command("list-verbs")
def list_verbs(
    robot: str | None = typer.Option(
        None, "--robot", "-r", help="A robot's vocabulary (<adapter>:<backend>); default Microduck."
    ),
) -> None:
    """List every verb a robot provides, with params and safety class."""
    from quackd.adapters.base import AdapterError
    from quackd.adapters.factory import parse_robot_spec, registry_for
    from quackd.verbs.registry import default_registry

    try:
        registry = registry_for(parse_robot_spec(robot)) if robot else default_registry()
    except AdapterError as e:
        _fail(str(e))
        return
    aliases: dict[str, list[str]] = {}
    for alias, target in registry.aliases().items():
        aliases.setdefault(target, []).append(alias)
    table = Table(title=f"verbs ({robot or 'microduck'})")
    table.add_column("name", style="bold")
    table.add_column("aliases")
    table.add_column("kind")
    table.add_column("safety")
    table.add_column("params")
    table.add_column("description")
    for v in registry.verbs():
        table.add_row(
            v.name,
            ", ".join(aliases.get(v.name, [])),
            f"{v.kind}{' (core)' if v.core else ''}",
            v.safety_class,
            v.param_summary(),
            v.description,
        )
    console.print(table)


@app.command("list-adapters")
def list_adapters_cmd() -> None:
    """List the robot adapters this build knows, their backends and status."""
    from quackd.adapters.factory import list_adapters

    table = Table(title="adapters (--robot <adapter>:<backend>)")
    table.add_column("adapter", style="bold")
    table.add_column("backends")
    table.add_column("status")
    table.add_column("extra")
    for row in list_adapters():
        # escape: an extra reads quackd[reachy], which Rich would eat as markup
        extra = escape(row["extra"])
        if row["extra"] != "built-in":
            extra += (
                " [green]installed[/green]" if row["installed"] else " [dim]not installed[/dim]"
            )
        table.add_row(row["name"], " · ".join(row["backends"]), row["status"], extra)
    console.print(table)


# ── run / record ────────────────────────────────────────────────────────────────────────


def _confirm_prompt(name: str, params: dict[str, Any]) -> bool:
    return typer.confirm(f"⚠️  run {name}({params})?", default=False)


def _run_impl(
    duckfile: str | None,
    goal: str | None,
    provider: str,
    model: str | None,
    seed: int | None,
    dry_run: bool,
    max_steps: int | None,
    runs_dir: str,
    yes: bool,
    live: bool,
    address: str | None,
    camera_url: str | None,
    token: str | None,
    gif: bool,
    gif_size: int,
    verbose: bool,
    base_url: str | None = None,
    api_key: str | None = None,
    vision: bool | None = None,
    flock: int | None = None,
    *,
    robot: str | None = None,
    robots: str | None = None,
    memory: bool = True,
    memory_dir: str | None = None,
) -> None:
    from quackd.adapters.factory import describe, make_adapter, registry_for
    from quackd.agent.loop import RunConfig, run_duck
    from quackd.agent.providers.base import ProviderError
    from quackd.agent.providers.factory import make_provider
    from quackd.duckfile.parser import DuckParseError, duck_from_goal, load_duck
    from quackd.duckfile.validate import validate_duck
    from quackd.perception import detector_for
    from quackd.safety import KillSwitch, allow_all
    from quackd.transport.base import TransportError

    if (duckfile is None) == (goal is None):
        _fail('give either a .duck file (or bundled name) or --goal "...", not both')
        return
    try:
        duck = load_duck(duckfile) if duckfile is not None else None
        specs = _robot_specs(robot, robots, duck)
        spec = specs[0]
        if goal is not None:
            safe = [v.name for v in registry_for(spec).verbs() if v.safety_class == "safe"]
            duck = duck_from_goal(goal, safe)
        assert duck is not None
        # Refuse before connecting, with the validator's words. `serve-mcp` has always done
        # this; `run` never did, and reached the loop's tool_schemas and died on a raw
        # VerbNotFound with the robot already connected and a run directory already made.
        manifests = [describe(s) for s in specs]
        problems = validate_duck(duck, manifests)
    except (DuckParseError, TransportError) as e:
        _fail(str(e))
        return
    if problems:
        _fail(
            f"{duck.name} cannot run on {', '.join(s.key for s in specs)}: "
            + "; ".join(p.message for p in problems)
        )
        return
    if flock is not None and not 2 <= flock <= 4:
        _fail("a flock needs 2 to 4 ducks (drop --flock for a single run)")
        return
    if flock is not None or duck.frontmatter.flock is not None:
        _run_flock_impl(
            duck,
            provider=provider,
            specs=specs,
            model=model,
            seed=seed,
            dry_run=dry_run,
            runs_dir=runs_dir,
            yes=yes,
            live=live,
            gif=gif,
            gif_size=gif_size,
            verbose=verbose,
            goal=goal,
            base_url=base_url,
            api_key=api_key,
            vision=vision,
            n_override=flock,
            max_steps=max_steps,
        )
        return
    try:
        llm = make_provider(
            provider,
            model=model,
            duck_name=duck.name,
            goal=goal,
            base_url=base_url,
            api_key=api_key,
            vision=vision,
        )
        duck_transport = make_adapter(
            spec,
            seed=seed,
            address=address,
            live=live,
            camera_url=camera_url,
            token=token,
        )
    except (ProviderError, TransportError, ImportError) as e:
        _fail(str(e))
        return

    recorder = None
    # Any robot with a camera needs something to look at its frames with, not just the
    # simulator. This is the static manifest, so it is only a head start: the loop asks
    # again with the live one at connect, where a robot may report a camera this does not
    # know about (a rosbridge base) or lack one this promises (a duck built without a head).
    detector = detector_for(manifests[0].sensors)
    # the recorder is sim2d only: it draws the world, and only the simulator has one
    if spec.backend == "sim2d" and gif:
        from quackd.sim2d.recorder import FrameRecorder

        recorder = FrameRecorder(duck_transport, size=gif_size)

    def log(msg: str) -> None:
        if verbose:
            err_console.print(f"[dim]{msg}[/dim]")

    robot_memory = None
    if memory:
        from quackd.memory import RobotMemory

        # keyed by adapter:backend, so a simulated duck never inherits a real one's notes
        robot_memory = RobotMemory(spec.key, memory_dir)
    cfg = RunConfig(
        duck=duck,
        provider=llm,
        transport=duck_transport,
        detector=detector,
        dry_run=dry_run,
        confirm=allow_all if yes else _confirm_prompt,
        runs_dir=runs_dir,
        max_steps=max_steps,
        log=log,
        on_frame=recorder.capture if recorder is not None else None,
        memory=robot_memory,
    )
    console.print(
        f"🦆 [bold]{duck.name}[/bold] · provider=[cyan]{llm.name}[/cyan] "
        f"({llm.model or 'model: first served'}) · "
        f"robot=[cyan]{spec.key}[/cyan]"
        + (f" · seed={seed}" if seed is not None else "")
        + (" · [yellow]DRY RUN[/yellow]" if dry_run else "")
    )
    if robot_memory is not None:
        m = robot_memory.summary()
        console.print(
            f"[dim]memory: {m['notes']} notes, {m['episodes']} earlier runs "
            f"({m['path']}) · --no-memory to run fresh[/dim]"
        )
    console.print("[dim]Ctrl-C or q stops the duck. Press it twice to quit at once.[/dim]")

    def killed(msg: str) -> None:
        """Always printed, unlike `log`, which is --verbose only. Someone who has just hit
        Ctrl-C on a walking robot needs to see that it registered."""
        err_console.print(f"[yellow]{msg}[/yellow]")

    async def main() -> Any:
        from quackd.agent.loop import AgentLoop

        loop = AgentLoop(cfg)
        ks = KillSwitch(loop.executor.abort, log=killed)
        ks.install()
        try:
            return await loop.run()
        finally:
            ks.uninstall()

    _ = run_duck  # imported for symmetry; AgentLoop is used directly so the kill switch can bind
    try:
        result = asyncio.run(main())
    except TransportError as e:
        _fail(str(e))
        return
    if recorder is not None:
        gif_path = recorder.save_gif(result.run_dir / "run.gif")
        result.gif_path = gif_path
    colour = {
        "success": "green",
        "failure": "red",
        "budget": "yellow",
        "aborted": "red",
        "error": "red",
    }[result.outcome]
    console.print(f"[{colour}]{result.outcome.upper()}[/{colour}] — {result.reason}")
    usage = result.usage
    console.print(
        f"steps={result.steps} llm_calls={result.llm_calls} "
        f"tokens={usage.input_tokens}+{usage.output_tokens}"
    )
    console.print(
        f"run dir: {result.run_dir}" + (f" · gif: {result.gif_path}" if result.gif_path else "")
    )
    if result.outcome != "success":
        raise typer.Exit(code=1)


def _run_flock_impl(
    duck: Any,
    *,
    provider: str,
    specs: list[Any],
    model: str | None,
    seed: int | None,
    dry_run: bool,
    runs_dir: str,
    yes: bool,
    live: bool,
    gif: bool,
    gif_size: int,
    verbose: bool,
    goal: str | None,
    base_url: str | None,
    api_key: str | None,
    vision: bool | None,
    n_override: int | None,
    max_steps: int | None,
) -> None:
    from quackd.agent.providers.base import ProviderError
    from quackd.agent.providers.factory import make_provider
    from quackd.flock.runner import run_flock
    from quackd.safety import KillSwitch
    from quackd.sim2d.recorder import FrameRecorder

    if any(spec.backend != "sim2d" for spec in specs):
        _fail(
            "flock mode is simulator only (docs/flock.md); "
            "every member must be an <adapter>:sim2d robot"
        )
        return
    if duck.frontmatter.verbs.confirm and not yes:
        _fail("a flock cannot prompt y/N per duck: empty verbs.confirm or pass --yes")
        return
    roles = duck.frontmatter.flock.roles if duck.frontmatter.flock is not None else None
    if n_override is not None and roles:
        _fail("--flock N cannot be combined with flock.roles; the task file names its members")
        return
    robots = {spec.name: spec.key for spec in specs if spec.name} or None
    try:
        llm = make_provider(
            provider,
            model=model,
            duck_name=duck.name,
            goal=goal,
            base_url=base_url,
            api_key=api_key,
            vision=vision,
        )
    except (ProviderError, ImportError) as e:
        _fail(str(e))
        return

    def log(msg: str) -> None:
        if verbose:
            err_console.print(f"[dim]{msg}[/dim]")

    holder: dict[str, Any] = {}

    def on_ready(transport0: Any, coordinator: Any) -> None:
        ks = KillSwitch(coordinator.abort, log=log)
        ks.install()
        holder["ks"] = ks
        if not gif:
            return
        rec = FrameRecorder(transport0, size=gif_size)
        holder["rec"] = rec
        names = sorted(coordinator.members)

        def on_event(kind: str, data: dict[str, Any]) -> None:
            if kind == "claim":
                entity = data.get("entity")
                if entity:
                    rec.set_focus(entity[1], entity[0])
                else:
                    rec.set_focus(names.index(data["kicker"]))
                spotter = f", spotter {data['spotter']}" if data.get("spotter") else ""
                rec.set_caption(f"CLAIM {data['kicker']} ({data['dist']:.2f} m){spotter}")
            elif kind == "auction":
                rec.set_caption(f"AUCTION first bid {data['first_bid']} {data['dist']:.2f} m")
            elif kind == "miss":
                rec.set_caption(f"MISS {data['duck']}, re-searching")
            elif kind == "kick_done":
                rec.set_caption(f"KICKED by {data['kicker']}, the spotter judges")
            elif kind == "verdict":
                moved = f" {data['moved_m']:.2f} m" if data.get("moved_m") is not None else ""
                rec.set_caption(f"VERDICT {data['verdict']}{moved} by {data['spotter']}")

        coordinator.on_event = on_event

    if n_override is not None:
        count = n_override
    elif duck.frontmatter.flock is not None:
        count = len(duck.frontmatter.flock.member_names)
    else:
        count = 3
    console.print(
        f"🦆x{count} [bold]{duck.name}[/bold] · provider=[cyan]{llm.name}[/cyan] "
        f"({llm.model or 'model: first served'}) · flock (sim2d, EXPERIMENTAL)"
        + (f" · seed={seed}" if seed is not None else "")
        + (" · [yellow]DRY RUN[/yellow]" if dry_run else "")
    )
    console.print("[dim]Ctrl-C or q stops every duck.[/dim]")
    try:
        result = asyncio.run(
            run_flock(
                duck,
                provider=llm,
                seed=seed if seed is not None else 0,
                runs_dir=runs_dir,
                n_override=n_override,
                dry_run=dry_run,
                max_steps=max_steps,
                live=live,
                gif_size=gif_size,
                on_recorder=on_ready,
                log=log,
                robots=robots,
            )
        )
    except ValueError as e:
        _fail(str(e))
        return
    finally:
        if "ks" in holder:
            holder["ks"].uninstall()
    if "rec" in holder:
        result.gif_path = holder["rec"].save_gif(result.run_dir / "run.gif")
    colour = {"success": "green", "failure": "red", "budget": "yellow", "aborted": "red"}.get(
        result.outcome, "red"
    )
    console.print(f"[{colour}]{result.outcome.upper()}[/{colour}] — {result.reason}")
    spotter = f"spotter={result.spotter} " if result.spotter else ""
    console.print(
        f"{spotter}kicker={result.kicker} auctions={result.auctions} bids={result.bids} "
        f"ball moved {result.ball_displacement_m:.2f} m in {result.sim_elapsed_s:.1f}s sim"
    )
    console.print(
        f"run dir: {result.run_dir}" + (f" · gif: {result.gif_path}" if result.gif_path else "")
    )
    if result.outcome != "success":
        raise typer.Exit(code=1)


_DUCK_ARG = typer.Argument(
    None, help="Path to a .duck file, or a bundled name (hello-world, find-and-kick, ...)."
)
_GOAL = typer.Option(
    None,
    "--goal",
    "-g",
    help='A plain-language goal instead of a .duck file, e.g. --goal "find the ball and kick it".',
)
_GIFSIZE = typer.Option(256, "--gif-size", help="sim2d: pixel size of each GIF pane.")
_FLOCK = typer.Option(
    None,
    "--flock",
    help="EXPERIMENTAL: run N cooperating ducks (2-4) in sim2d. Overrides the file's flock block.",
)
_MEMORY = typer.Option(
    True,
    "--memory/--no-memory",
    help="Carry notes and run outcomes between runs of the same robot (see `quackd memory`).",
)
_MEMORY_DIR = typer.Option(
    None,
    "--memory-dir",
    help="Where memory files live (default: $QUACKD_MEMORY_DIR or ~/.quackd/memory).",
)
_PROVIDER = typer.Option(
    "fake",
    "--provider",
    "-p",
    help="fake · anthropic · openai · gemini · grok · local · ollama · vllm · llamacpp · lmstudio",
)
_BASEURL = typer.Option(
    None,
    "--base-url",
    help="OpenAI-compatible server, e.g. http://localhost:8000/v1 (local presets).",
)
_APIKEY = typer.Option(None, "--api-key", help="API key override (local servers do not need one).")
_VISION = typer.Option(
    None,
    "--vision/--no-vision",
    help="Send camera frames to the model (default: on for cloud, off for local).",
)
_ROBOT = typer.Option(
    None,
    "--robot",
    "-r",
    help="<adapter>:<backend>, e.g. microduck:sim2d (default) · microduck:mock · "
    "microduck:jsonrpc. See `quackd list-adapters`.",
)
_ROBOTS = typer.Option(
    None,
    "--robots",
    help="A flock or fleet: name=<adapter>:<backend>,... (simulator only for flocks).",
)
_MODEL = typer.Option(None, "--model", "-m", help="Override the provider's model.")
_SEED = typer.Option(None, "--seed", help="Simulator seed (deterministic runs).")
_DRY = typer.Option(False, "--dry-run", help="Print every intent, send nothing.")
_MAXSTEPS = typer.Option(None, "--max-steps", help="Override the duck's max_steps budget.")
_RUNS = typer.Option("runs", "--runs-dir", help="Where run directories go.")
_YES = typer.Option(False, "--yes", "-y", help="Auto-confirm gated verbs (careful on hardware).")
_LIVE = typer.Option(False, "--live", help="sim2d: open a live pygame window (needs quackd[live]).")
_ADDR = typer.Option(None, "--address", help="jsonrpc: unix:///run/robotd.sock or tcp://host:port")
_TOKEN = typer.Option(
    None,
    "--token",
    help="The bridge token for a robot that wants one. Its installer writes one on the "
    "robot. Reads QUACKD_DUCK_TOKEN when the flag is absent.",
)
_CAMERA_URL = typer.Option(
    None,
    "--camera-url",
    help="Where frames come from, overriding whatever the robot advertises. An HTTP snapshot "
    "(http://host:9872/snapshot.jpg), or webrtc://host:8443 to pull mediad's video track off a "
    "Microduck, which is the only camera upstream offers and needs quackd[microduck-camera]. "
    "Needed when you reach the robot through a tunnel and its own URL is not routable.",
)
_VERBOSE = typer.Option(False, "--verbose", "-v", help="Log every intent to stderr.")


@app.command()
def run(
    duckfile: str | None = _DUCK_ARG,
    goal: str | None = _GOAL,
    provider: str = _PROVIDER,
    robot: str | None = _ROBOT,
    robots: str | None = _ROBOTS,
    model: str | None = _MODEL,
    seed: int | None = _SEED,
    dry_run: bool = _DRY,
    max_steps: int | None = _MAXSTEPS,
    runs_dir: str = _RUNS,
    yes: bool = _YES,
    live: bool = _LIVE,
    address: str | None = _ADDR,
    camera_url: str | None = _CAMERA_URL,
    token: str | None = _TOKEN,
    gif: bool = typer.Option(True, "--gif/--no-gif", help="sim2d: write run.gif into the run dir."),
    gif_size: int = _GIFSIZE,
    verbose: bool = _VERBOSE,
    base_url: str | None = _BASEURL,
    api_key: str | None = _APIKEY,
    vision: bool | None = _VISION,
    flock: int | None = _FLOCK,
    memory: bool = _MEMORY,
    memory_dir: str | None = _MEMORY_DIR,
) -> None:
    """Run a .duck file (or a --goal): the LLM picks verbs, quackd enforces the contract."""
    _run_impl(
        duckfile,
        goal,
        provider,
        model,
        seed,
        dry_run,
        max_steps,
        runs_dir,
        yes,
        live,
        address,
        camera_url,
        token,
        gif,
        gif_size,
        verbose,
        base_url=base_url,
        api_key=api_key,
        vision=vision,
        flock=flock,
        robot=robot,
        robots=robots,
        memory=memory,
        memory_dir=memory_dir,
    )


@app.command()
def record(
    duckfile: str | None = _DUCK_ARG,
    goal: str | None = _GOAL,
    provider: str = _PROVIDER,
    model: str | None = _MODEL,
    seed: int | None = typer.Option(0, "--seed"),
    max_steps: int | None = _MAXSTEPS,
    runs_dir: str = _RUNS,
    gif_size: int = _GIFSIZE,
    verbose: bool = _VERBOSE,
    base_url: str | None = _BASEURL,
    api_key: str | None = _APIKEY,
    vision: bool | None = _VISION,
    flock: int | None = _FLOCK,
) -> None:
    """Like `run` on sim2d, but always writes a GIF (for READMEs and launches)."""
    _run_impl(
        duckfile,
        goal,
        provider,
        model=model,
        seed=seed,
        dry_run=False,
        max_steps=max_steps,
        runs_dir=runs_dir,
        yes=True,
        live=False,
        address=None,
        camera_url=None,
        token=None,
        gif=True,
        gif_size=gif_size,
        verbose=verbose,
        base_url=base_url,
        api_key=api_key,
        vision=vision,
        flock=flock,
        robot="microduck:sim2d",
    )


# ── doctor / serve-mcp ──────────────────────────────────────────────────────────────────


@app.command()
def doctor(
    robot: str | None = typer.Option(
        None, "--robot", "-r", help="Also show one robot's manifest (<adapter>:<backend>)."
    ),
    address: str | None = typer.Option(
        None,
        "--address",
        help="With --robot, connect to a real robot and report what it says about itself.",
    ),
    camera_url: str | None = _CAMERA_URL,
    token: str | None = _TOKEN,
) -> None:
    """Check the environment: keys, optional extras, adapters, upstream assumptions.

    With `--robot X --address Y` it also connects, which is the only way to see what a
    robot actually reports before a run does."""
    from quackd.doctor import run_doctor

    if address and not robot:
        _fail("--address needs --robot, so quackd knows what it is connecting to")
        return
    ok = run_doctor(console, robot=robot, address=address, camera_url=camera_url, token=token)
    if not ok:
        raise typer.Exit(code=1)


@app.command("serve-mcp")
def serve_mcp(
    robot: str | None = _ROBOT,
    robots: str | None = typer.Option(
        None,
        "--robots",
        help="A fleet: name=<adapter>:<backend>,... (eight robot_* tools, one executor each).",
    ),
    duckfile: str | None = typer.Option(
        None, "--duckfile", help="Load a .duck contract at startup (on the default robot)."
    ),
    seed: int | None = _SEED,
    address: str | None = _ADDR,
    camera_url: str | None = _CAMERA_URL,
    token: str | None = _TOKEN,
    dry_run: bool = _DRY,
    yes: bool = typer.Option(
        False, "--yes", "-y", help="Allow confirm-gated verbs (there is no terminal to ask)."
    ),
    memory: bool = _MEMORY,
    memory_dir: str | None = _MEMORY_DIR,
) -> None:
    """Expose the robot as MCP tools over stdio (Claude Code / Claude Desktop)."""
    from quackd.adapters.base import AdapterError
    from quackd.mcp_server import serve

    try:
        serve(
            robot=robot,
            robots=robots,
            duckfile=duckfile,
            seed=seed,
            address=address,
            camera_url=camera_url,
            token=token,
            dry_run=dry_run,
            yes=yes,
            memory=memory,
            memory_dir=memory_dir,
        )
    except AdapterError as e:
        _fail(str(e))


# ── memory ──────────────────────────────────────────────────────────────────────────────

memory_app = typer.Typer(
    name="memory",
    help="What a robot remembers between runs: notes the pilot saved, and how runs ended.",
    no_args_is_help=True,
)
app.add_typer(memory_app, name="memory")


def _memory_for(robot: str | None, memory_dir: str | None) -> Any:
    from quackd.adapters.base import AdapterError
    from quackd.adapters.factory import resolve_robot
    from quackd.memory import RobotMemory

    try:
        spec = resolve_robot(robot)
    except AdapterError as e:  # every other --robot command answers in one line, not a traceback
        _fail(str(e))
    return RobotMemory(spec.key, memory_dir)


@memory_app.command("show")
def memory_show(
    robot: str | None = _ROBOT,
    memory_dir: str | None = _MEMORY_DIR,
    raw: bool = typer.Option(False, "--raw", help="Print the JSONL file as is."),
) -> None:
    """Print what one robot remembers (default: the Microduck simulator)."""
    mem = _memory_for(robot, memory_dir)
    if raw:
        if mem.path.exists():
            # "as is" means as is: a note saying "the ball is [bold]behind[/bold] the sofa"
            # is markup Rich would silently eat, and an unpaired tag would raise.
            print(mem.path.read_text(encoding="utf-8"), end="")
        return
    info = mem.summary()
    console.print(
        f"[bold]{info['robot']}[/bold] · {info['notes']} notes · {info['episodes']} runs · "
        f"[dim]{info['path']}[/dim]"
    )
    text = mem.recall(max_notes=50, max_episodes=10)
    console.print(escape(text) if text else "[dim](nothing remembered yet)[/dim]")


@memory_app.command("add")
def memory_add(
    text: str = typer.Argument(..., help="One short fact, e.g. 'the ball lives by the sofa'."),
    robot: str | None = _ROBOT,
    memory_dir: str | None = _MEMORY_DIR,
    tag: list[str] = typer.Option([], "--tag", help="Optional label(s)."),
) -> None:
    """Save a note by hand, the same way the pilot's `remember` does."""
    mem = _memory_for(robot, memory_dir)
    entry = mem.remember(text, tags=tag)
    console.print(f"remembered for [bold]{mem.robot_key}[/bold]: {escape(entry.text)}")


@memory_app.command("clear")
def memory_clear(
    robot: str | None = _ROBOT,
    memory_dir: str | None = _MEMORY_DIR,
    yes: bool = typer.Option(False, "--yes", "-y", help="Do not ask."),
) -> None:
    """Forget everything one robot remembers (deletes its memory file)."""
    mem = _memory_for(robot, memory_dir)
    n = len(mem.entries())
    if n == 0:
        console.print(f"[dim]{mem.robot_key}: nothing to forget[/dim]")
        return
    if not yes and not typer.confirm(f"forget {n} entries for {mem.robot_key}?"):
        raise typer.Exit()
    mem.clear()
    console.print(f"forgot {n} entries for [bold]{mem.robot_key}[/bold]")


# ── lan (quackd[lan]) ───────────────────────────────────────────────────────────────────


@app.command()
def discover(
    timeout: float = typer.Option(3.0, "--timeout", help="Seconds to listen for answers."),
    as_json: bool = typer.Option(False, "--json", help="One JSON object per robot."),
) -> None:
    """List the quackd robots answering on the LAN (zeroconf, needs quackd[lan])."""
    import json

    from rich.table import Table

    from quackd.lan import LanNotInstalled
    from quackd.lan import discover as lan_discover

    try:
        robots = lan_discover.discover(timeout)
    except LanNotInstalled as e:
        _fail(str(e))
    if as_json:
        for robot in robots:
            print(json.dumps(robot.row()))
        return
    if not robots:
        console.print(f"[dim]no quackd robots answered in {timeout:g} s[/dim]")
        return
    t = Table(title=f"quackd robots on the LAN ({len(robots)})")
    for column in ("manifest id", "adapter", "model", "embodiment", "verbs", "address", "digest"):
        t.add_column(column)
    for robot in robots:
        t.add_row(
            robot.manifest_id,
            robot.adapter,
            robot.model,
            robot.embodiment,
            str(robot.n_verbs),
            ", ".join(robot.addresses) or robot.host,
            robot.digest,
        )
    console.print(t)


@app.command()
def announce(
    robot: str = typer.Option(
        ..., "--robot", "-r", help="<adapter>:<backend> to advertise (static manifest, no robot)."
    ),
    name: str | None = typer.Option(
        None, "--name", help="Manifest id to advertise (default: the adapter's own)."
    ),
    port: int = typer.Option(0, "--port", help="Service port to advertise; 0 = identity only."),
    for_s: float | None = typer.Option(
        None, "--for", help="Seconds to stay announced (default: until Ctrl-C)."
    ),
) -> None:
    """Advertise a robot's identity on the LAN (zeroconf, needs quackd[lan])."""
    import time

    from quackd.adapters.base import AdapterError
    from quackd.adapters.factory import RobotSpec, describe, parse_robot_spec
    from quackd.lan import LanNotInstalled
    from quackd.lan import announce as lan_announce

    try:
        parsed = parse_robot_spec(robot)
        spec = RobotSpec(parsed.adapter, parsed.backend, name)
        manifest = describe(spec)
        ann = lan_announce.announce(manifest, adapter=spec.adapter, port=port)
    except (AdapterError, LanNotInstalled, ValueError) as e:
        _fail(str(e))
    console.print(
        f"announcing {ann.record.name} ({manifest.summary()}) at "
        f"{', '.join(ann.record.addresses)} · digest {manifest.digest()}"
    )
    try:
        if for_s is None:
            console.print("[dim]Ctrl-C to withdraw[/dim]")
            while True:
                time.sleep(1.0)
        else:
            time.sleep(for_s)
    except KeyboardInterrupt:
        pass
    finally:
        ann.close()
        console.print("withdrawn")


if __name__ == "__main__":
    app()
