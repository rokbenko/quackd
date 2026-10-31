"""The command line is the product's front door.

`uvx --from "quackd[microduck]" quackd run find-and-kick --llm anthropic --robot
microduck:sim2d` is the north-star demo; every command here exists to make that line, and the
debugging around it, boring. The `--from` is there because the core ships no robot and the
demo needs one. Commands are thin: they parse, load `.env`, wire objects together, hand off.
"""

from __future__ import annotations

import asyncio
import contextlib
import errno
import glob
import json
import os
import re
import sys
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import typer
from dotenv import load_dotenv
from pydantic import ValidationError
from rich.text import Text

from quackd import __version__, dotenv_names, ui
from quackd.agent.decision.frames import MAX_SIDE_PX as DECISION_FRAME_PX
from quackd.agent.decision.notices import MODE_ON_UNMEASURED as _DECISION_MODE_ON_UNMEASURED
from quackd.agent.decision.notices import frames_notice as _frames_notice
from quackd.agent.providers.catalogue import (
    CLOUD_NAMES,
    DEFAULT_LLM,
    LLM_ENV,
    LOCAL_NAMES,
    OPEN_ENDED,
    PROVIDER_NAMES,
    default_model_for,
    models_for,
    vendor_of,
)
from quackd.answers import DID_IT, GO_AHEAD, WATCHING
from quackd.command import command_text
from quackd.preflight import DEFAULT_CONNECT_CYCLES, DEFAULT_SEEDS
from quackd.space.room import BUILT_IN as BUILT_IN_ROOMS

if TYPE_CHECKING:  # every heavy module is imported inside the command that needs it
    from quackd.adapters.base import RestResult
    from quackd.adapters.factory import RobotSpec
    from quackd.front import Front
    from quackd.safety import KillSwitch

app = typer.Typer(
    name="quackd",
    # the emoji only where the stream can carry it: a cp1252 pipe on Windows renders them as
    # `??`, and the front door is the worst place to look broken
    help="One CLI for all your robots. Connect them, command them, and let them work "
    "together, each with an LLM for a brain."
    + (" 🦆🧠" if ui.glyphs_for(ui.console) is ui.UNICODE else ""),
    no_args_is_help=True,
    rich_markup_mode="rich",
    context_settings={"help_option_names": ["-h", "--help"]},
    # A crash must not print this process's local variables: they hold an API key, a robot's
    # address and its bridge token. `main` installs a Rich traceback without them instead.
    pretty_exceptions_enable=False,
    # blank lines rather than spaces: Typer renders the epilog as paragraphs and would
    # otherwise run three examples together into one
    epilog=(
        # `doctor` first: it is the one command that works before a robot is installed, and it
        # says which are. The install is on the line that follows it and covers both run
        # examples below, because the core on its own carries no robot and a first example
        # that refuses is a bad one.
        "[bold]Try[/bold]" + "\n\n"
        "quackd doctor  |  quackd list-adapters  |  quackd log" + "\n\n"
        # the backslash is Rich's escape: an unescaped [microduck] is a style tag, and Typer
        # renders this epilog as markup, so it would print the install line without the extra
        # that makes it work
        r"uv pip install 'quackd\[microduck]' && quackd run find-and-kick --llm fake" + "\n\n"
        "quackd run --goal 'walk in a square' --llm anthropic --robot microduck:mujoco"
    ),
)


_DEPRECATIONS: list[str] = []
"""Every deprecation line this process printed, in order, kept for the saved terminal.

These are printed from the root callback, which Click runs before the subcommand body and so
before there is a capture to forward them into. Rather than move the warning later, where a
reader would meet it after the header panel instead of before it, the text is kept here and
`_terminal_header` puts it back at the top of the file where it was on the screen."""


def _deprecated(msg: str) -> None:
    """One yellow line on stderr, the shape ADR-0017 used to retire a flag over a release.

    `soft_wrap` because the sentence is an instruction a script may grep for and the longest
    of them is 99 characters, which a default 80-column stderr would fold in the middle of
    the new spelling."""
    _DEPRECATIONS.append(msg)
    ui.err_console.print(
        Text(msg, style=ui.STYLES["warn"]), markup=False, highlight=False, soft_wrap=True
    )


def _deprecated_backends(specs: Sequence[RobotSpec | str]) -> None:
    """`_deprecated`'s line for each backend these robots are on that its adapter is retiring
    (`deprecated_backend`), said once in a command however many of its robots are on the backend
    and however many times this is asked, so a fleet of two ducks on one such backend is told
    once.

    Asked just before a command connects the robots it is handed, and never earlier, because
    the line tells a reader that the command is connecting through the backend. A robot named
    on the command line that the command never drives says nothing, and neither does a command
    refused before it connects. `run` asks for its one robot once the last thing that can refuse
    the run is behind it, just before its header panel. A pilot flock asks for every member
    once all of them are built, the last thing that can refuse it before they connect together
    (`run_pilot_flock`'s `before_connect`), which is after its header. A coordinator flock
    connects nothing but `microduck:sim2d` bodies, refusing any other, and that backend is not
    one the Microduck retires, so it has nothing to ask. `doctor` asks just before its probe
    connects, and `robot list --probe` just before it connects each robot it probes.
    `serve-mcp` connects its robots inside `quackd.mcp_server`, which says the line through the
    `_deprecated` it is handed, just before its start connects each robot. The line goes to
    stderr in all of them, so `doctor --json`, `robot list --json` and the MCP wire on
    `serve-mcp`'s stdout stay what they were."""
    from quackd.adapters.factory import deprecated_backend

    for line in dict.fromkeys(filter(None, (deprecated_backend(spec) for spec in specs))):
        # a line already said in this command, for a robot connected earlier, is not said
        # again: `robot list --probe` asks once for each robot it probes
        if line not in _DEPRECATIONS:
            _deprecated(line)


def _warn_old_spellings() -> None:
    """Say it once per process, for each name this release stopped reading and finds set.

    Only variables are left here. A flag or a subcommand that is gone fails loudly: Click
    refuses it, names it, and nothing runs. A variable that is gone goes quiet, and the quiet
    is the failure. `QUACKD_MODEL` is the kind of line that sits in a `.env` for a year;
    unread, it does not stop the run, it lets the run bill a model nobody chose.
    `QUACKD_TRACE=0` is the same line with the opposite sign: unread, it switches the log back
    on for the one reader who had deliberately turned it off, which is why 0.11 went on
    reading it for a release. 0.12 stops, as promised, and says so instead.

    Read here rather than where each value used to be, so a `.env` is told about every old
    line in it and not only the one this run would have consulted: 0.11 could warn about a
    name only on a run that read it, and said so.

    The kept lines are cleared first. One process is one command when a person runs quackd,
    but this module is also imported and driven twice in a row by tests, by a wrapper and by
    `quackd.cli.app(...)`, and a second run whose saved terminal opened with the first run's
    deprecations would be a header describing a command nobody typed."""
    _DEPRECATIONS.clear()
    for gone, now in (
        ("QUACKD_MODEL", "QUACKD_LLM=vendor:model"),
        ("QUACKD_JEV", "QUACKD_DECISION_LLM"),
        ("QUACKD_TRACE", "QUACKD_LOG"),
        ("QUACKD_TRACE_THINKING", "QUACKD_LOG_THINKING"),
        ("QUACKD_TRACE_PROMPT", "QUACKD_LOG_PROMPT"),
    ):
        if os.environ.get(gone):
            _deprecated(f"{gone} is not read any more and this run ignores it; set {now} instead")


def _terminal_header() -> list[str]:
    """What the saved terminal opens with: the command, then what ran it and when.

    Two lines and a blank one, followed by any deprecation the root callback already printed,
    which is the one thing said on screen before there was anywhere to write it down."""
    started = datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
    return [
        f"$ {command_text()}",
        f"quackd {__version__}, started {started}, cwd {Path.cwd()}",
        "",
        *_DEPRECATIONS,
        *([""] if _DEPRECATIONS else []),
    ]


@contextlib.contextmanager
def _terminal_record() -> Iterator[None]:
    """Keep what this run puts on the terminal, and write it into the run directory.

    Around the whole command rather than inside `_run_impl`, so the pre-flight refusals are
    in it too: a run that dies on a bad flag prints one sentence and that sentence is part of
    the story. Nothing is written until there is a run directory to write into, which is what
    keeps a refused run from leaving one behind.

    The three exits are told apart because the file should say which happened. `typer.Exit`
    is a refusal that has already printed its own line. A `KeyboardInterrupt` is somebody
    pressing Ctrl-C twice, which prints nothing at all. Anything else is a crash, and the
    traceback for it is drawn by `sys.excepthook` after this has closed, so the file would
    otherwise end mid-sentence with no sign of why."""
    capture = ui.begin_capture(header=_terminal_header())
    try:
        yield
    except typer.Exit:
        raise
    except KeyboardInterrupt:
        ui.note("^C")
        raise
    except BaseException as e:
        ui.note(f"quackd crashed: {type(e).__name__}: {e}")
        raise
    finally:
        capture.close()


def _version_callback(value: bool) -> None:
    if value:
        ui.console.print(f"quackd {__version__}")
        raise typer.Exit()


def _no_color_callback(value: bool) -> bool:
    """Eager, and it sets the variable rather than only the consoles.

    Typer builds a console of its own for every `--help` it renders and reads `NO_COLOR`
    when it does, so the variable is what makes `quackd --no-color run --help` plain as
    well. Eager means it has run by the time the subcommand is parsed."""
    if value:
        os.environ["NO_COLOR"] = "1"
    return value


@app.callback()
def _main(
    version: bool = typer.Option(
        False, "--version", "-V", callback=_version_callback, is_eager=True, help="Show version."
    ),
    no_color: bool = typer.Option(
        False,
        "--no-color",
        is_eager=True,
        callback=_no_color_callback,
        help="Plain output with no colour. NO_COLOR=1 does the same, and FORCE_COLOR=1 keeps "
        "the colour when the output is a pipe.",
    ),
) -> None:
    """quackd — one CLI for all your robots, real or simulated, piloted by any LLM."""
    # `.env` first, so a NO_COLOR line in it counts, and then the consoles: Rich reads the
    # environment and the stream's encoding when a console is built, and quackd's are built
    # at import, which is before any of this was known.
    #
    # The one beside where you run, then the bare call, which walks up from quackd's own
    # installed directory and so finds the `.env` a `uv venv` user put in their venv root.
    # Neither overrides a variable already in the environment, and the first file to define
    # a name wins, so the file next to the command you typed is the one that counts. What they
    # added is kept apart from what the shell exported (`dotenv_names`), for the few settings
    # a project's own `.env` may not decide, such as the registry `--policy-url hf:NAME` reads.
    before = set(os.environ)
    load_dotenv(Path.cwd() / ".env")
    load_dotenv()
    dotenv_names.remember(before)
    ui.configure(no_color=no_color)
    _warn_old_spellings()


_JSON = typer.Option(
    False,
    "--json",
    help="One JSON object per line on stdout, and nothing else: for a script rather than "
    "for a person. Exit codes are unchanged.",
    rich_help_panel="Output",
)

_REGISTRY_DIR = typer.Option(
    None,
    "--registry-dir",
    help="Where robots.json, flocks.json and endpoints.json live (default: $QUACKD_REGISTRY_DIR "
    "or ~/.quackd).",
    rich_help_panel="Robot",
)

NEWLINE = "\n"

_ADAPTER_HINT = "quackd list-adapters shows the nine that ship and their backends"


def _expand(patterns: list[str]) -> list[str]:
    """Each pattern's matches, sorted, or the pattern itself where nothing matches, so that
    PowerShell, which globs nothing, gets what a POSIX shell would have handed over.

    On Windows `glob` joins what a wildcard matched with a backslash and leaves the part typed
    before it as it was typed, so `docs/examples/lerobot/e00[1-5]/*.duck` came back as
    `docs/examples/lerobot\\e001\\circle.duck` and a table of files read in two slash styles.
    A pattern written with forward slashes gets its matches back written that way too."""
    out: list[str] = []
    for pat in patterns:
        matches = glob.glob(pat)
        if "/" in pat and os.sep != "/":
            matches = [m.replace(os.sep, "/") for m in matches]
        out.extend(sorted(matches) if matches else [pat])
    return out


def _verbose_line(msg: str) -> None:
    """A `--verbose` line, as plain text. A message can carry brackets Rich reads as markup:
    the executor's own `[dry-run] would run ...`, and the flock planner logging a model's raw
    tool arguments. Rich deletes `[bold]` silently and raises on an unpaired `[/think]`."""
    ui.err_console.print(msg, style="dim", markup=False, highlight=False, soft_wrap=True)


EXIT_INFEASIBLE = 3
"""`quackd run` when the pilot judged the task beyond this body and nothing moved."""


def _print_outcome(
    outcome: str,
    reason: str,
    *,
    counters: Sequence[str],
    run_dir: Path | str,
    gif_path: Path | str | None = None,
    log_dropped: int = 0,
) -> None:
    """How the run ended, in the one place a person looks after looking away.

    `quackd log` prints it from the transcript too, so a replay ends exactly the way the
    run itself did rather than in a second dialect somebody has to keep in step. `counters`
    is a list because a flock counts different things than a solo run does."""
    ui.console.print(
        ui.verdict(outcome, reason, counters=counters, run_dir=run_dir, gif_path=gif_path)
    )
    if log_dropped:
        # a console that raised on every event produced a silent log and no sign of it
        ui.err_console.print(
            f"log: {log_dropped} line(s) could not be shown (the console raised); "
            "transcript.jsonl has them",
            style="yellow",
            markup=False,
        )


def _number(value: Any) -> float | None:
    """A figure out of a record, or None where there is not one.

    `quackd log` is pointed at files people hand-edit, truncate and copy between machines,
    and the renderers beside this one already shrug at a field that is not what it should be.
    A counter line is not worth a traceback."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return None if value != value else float(value)  # NaN is not a figure either


def run_counters(end: Mapping[str, Any]) -> list[str]:
    """What a finished run cost, in the line under the verdict.

    Built from the summary dict, which is what `run_end` carries and what `summary.json`
    holds, so the live panel and `quackd log` print one list rather than two that drift.

    Every field is optional on purpose. A SOLO run recorded before any of this existed
    replays with exactly the three counters it always had. A flock is the one deliberate
    exception: it has kept a wall clock of its own since long before a solo run had one, so
    an old flock record does gain a `time` counter from the number it was already writing.
    """
    from quackd.agent.providers.pricing import fmt_usd
    from quackd.log import fmt_duration

    usage = end.get("usage") or {}
    # the tries quackd sent again for turns the API could not answer, which `llm_calls` does
    # not count, each being a turn it counted already (ADR-0059): only where a run had one
    retries = int(_number(end.get("llm_retries")) or 0)
    counters = [
        f"steps {int(_number(end.get('steps')) or 0)}",
        f"llm calls {int(_number(end.get('llm_calls')) or 0)}"
        + (f" (+{retries} asked again)" if retries else ""),
        f"tokens {usage.get('input_tokens', 0)}+{usage.get('output_tokens', 0)}",
    ]
    wall = _number(end.get("wall_s"))
    if wall is None:
        wall = _number(end.get("wall_elapsed_s"))
    if wall is not None:
        # The split, not just the total: on the one hardware run this project has, 62.1 of
        # 78.8 seconds were spent waiting on the model, and that ratio is the single most
        # useful number a run produces. The stepper's seconds join it when one ran.
        spent = []
        if (llm := _number(end.get("llm_latency_s"))) is not None:
            spent.append(f"model {fmt_duration(llm)}")
        if (stepper := _number((end.get("decision") or {}).get("latency_s"))) is not None:
            spent.append(f"stepper {fmt_duration(stepper)}")
        # and the seconds `watch` waited on its answers, a share of its verbs' own time rather
        # than of the turns between them, only where a run had it
        if (
            watched := _number(_block(_block(end, "decision"), "watch").get("latency_s"))
        ) is not None:
            spent.append(f"watch {fmt_duration(watched)}")
        # and the seconds the arm's policy drove it, when it had one and it ran, on the wall's
        # clock as the rest of the split is: on the simulator the policy's own `seconds` are
        # the simulator's, and a split can never be larger than the whole
        if policy_s := _number(_block(end, "policy").get("wall_s")):
            spent.append(f"policy {fmt_duration(policy_s)}")
        # and the seconds spent waiting to ask the API again, in which no model was asked
        # anything and so are not the model's, only where a run waited
        if waited := _number(end.get("llm_retry_wait_s")):
            spent.append(f"waits {fmt_duration(waited)}")
        where = f" ({', '.join(spent)})" if spent else ""
        counters.append(f"time {fmt_duration(wall)}{where}")
    # The typical answer and the bad one, which a total cannot tell apart, and how long the robot
    # took to do anything at all: each only where its keys are, so a record from before them
    # replays with exactly the counters it always had (`quackd.latency`).
    if (calls := _spread_counter("model calls", end, "llm_latency")) is not None:
        counters.append(calls)
    if (asked := _spread_counter("stepper", _block(end, "decision"), "latency")) is not None:
        counters.append(asked)
    if (first := _number(end.get("first_motion_s"))) is not None:
        made = _number(end.get("first_motion_llm_calls"))
        after = "" if made is None else f", after {int(made)} model call{'' if made == 1 else 's'}"
        counters.append(f"first motion at {fmt_duration(first)}{after}")
    if (policy := _policy_counter(end.get("policy"))) is not None:
        counters.append(policy)
    decision_block = end.get("decision") or {}
    decision_cost = _number(decision_block.get("cost_usd"))
    # On a cost KEY, not on the presence of a stepper block. A stepper block recorded before
    # the costing existed carries no `cost_usd`, and gating on the block gave those records a
    # counter reading `cost unpriced` that they never had and that says nothing true about
    # them: nobody tried to price them.
    if "cost_usd" in end or decision_cost is not None:
        model_cost = _number(end.get("cost_usd")) if end.get("cost_usd") is not None else None
        estimated = bool(decision_block.get("cost_estimated")) and bool(decision_cost)
        if model_cost is None:
            # A model quackd has no rate for must never read as a free one, and the two
            # halves are reported separately rather than summed away: "unpriced" plus a
            # stepper figure is the truth, and a bare "unpriced" would throw away the half
            # that IS known.
            unpriced = "cost unpriced"
            if decision_cost:
                # the stepper's bill, and `watch`'s with it where a run had it, which is then the
                # decision LLM's whole bill rather than the stepper's
                payer = (
                    "decision LLM"
                    if isinstance(decision_block.get("watch"), Mapping)
                    else "stepper"
                )
                unpriced += f" ({payer} {'~' if estimated else ''}{fmt_usd(decision_cost)})"
            counters.append(unpriced)
        else:
            total = model_cost + (decision_cost or 0.0)
            counters.append(f"cost {'~' if estimated else ''}{fmt_usd(total)}")
    return counters


def _spread_counter(label: str, block: Mapping[str, Any], prefix: str) -> str | None:
    """`model calls p50 6.2 s, p90 8.2 s, max 8.2 s` from a block's `<prefix>_p50_s` and its
    two siblings, or None unless all three are numbers: a half-written spread is no spread."""
    from quackd.latency import SPREAD
    from quackd.log import fmt_duration

    figures = [(name, _number(block.get(f"{prefix}_{name}_s"))) for name in SPREAD]
    if any(seconds is None for _name, seconds in figures):
        return None
    return f"{label} " + ", ".join(
        f"{name} {fmt_duration(seconds)}" for name, seconds in figures if seconds is not None
    )


def _block(end: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    """A block of a record, or an empty one where it is missing or is not a block at all: a
    `quackd log` of a hand-edited file is a counter line short, never a traceback."""
    block = end.get(key)
    return block if isinstance(block, Mapping) else {}


def _policy_counter(block: Any) -> str | None:
    """What the arm's policy did in a run, as one counter: its segments, the chunks it sent and
    the rate its ticks were achieved at, the ticks that went wrong and the goals clipped when
    there were any, and the mean round trip to its server. None for a run with no policy block,
    which is every run whose arm had no policy, so their counters are what they were, and the
    segments alone for a run whose policy was never handed the arm.

    On the simulator the rate is the simulator clock's, which the ticks were paced on, so it is
    said with the simulator's seconds and the clock's name (`20.1 s sim at 10 Hz`), as a verb's
    seconds are (`quackd.log`), and the wall's share of them is in the time split."""
    from quackd.log import fmt_duration

    if not isinstance(block, Mapping):
        return None
    segments = int(_number(block.get("segments")) or 0)
    parts = [f"policy {_plural(segments, 'segment')}"]
    if not segments:
        return parts[0]
    if (chunks := _number(block.get("chunks"))) is not None:
        parts.append(_plural(int(chunks), "chunk"))
    if (hz := _number(block.get("hz"))) is not None:
        clock, seconds = block.get("clock"), _number(block.get("seconds"))
        if isinstance(clock, str) and clock and seconds is not None:
            parts.append(f"{fmt_duration(seconds)} {clock} at {hz:g} Hz")
        else:
            parts.append(f"{hz:g} Hz")
    for key, noun, how in (
        ("starved_ticks", "tick", "starved"),
        ("late_ticks", "tick", "late"),
        ("clips", "goal", "clipped"),
    ):
        if n := int(_number(block.get(key)) or 0):
            parts.append(f"{_plural(n, noun)} {how}")
    trips = block.get("round_trip_ms")
    if isinstance(trips, Mapping) and (mean := _number(trips.get("mean"))) is not None:
        parts.append(f"round trip {mean:g} ms")
    return ", ".join(parts)


def _detector_row(detector: Any, hello: Any, backend: str | None, *, sees: bool = True) -> str:
    """Which detector reads the frames, and where it runs, in one line. The board's is named
    with the board and what the daemon said it runs, because "yolo" alone would not say whether
    the laptop or the Jetson is doing the work.

    None is the colour detector, which the loop builds at connect for a body that reports a
    camera (`detector_for`) when nothing was chosen before. The row names it rather than
    leaving the question open beside a host row that says "detect".

    `sees` is whether the body, with the board's camera if it has one, is described with a
    camera. A body described without one may report a camera when it connects, and the row
    says that its detector reads nothing until then."""
    from quackd.perception import is_simulated
    from quackd.perception.color_blob import ColorBlobDetector
    from quackd.perception.host import HostDetector

    if detector is None:
        text = f"{ColorBlobDetector.name} on this machine"
    elif isinstance(detector, HostDetector):
        text = f"{detector.name}  {detector.address}"
        if hello is not None and (label := hello.label()):
            text += f"  {label}"
        if is_simulated(backend):
            # never chosen by itself on a simulator, so a reader seeing it there is told it
            # was asked for, and that it is not what this simulator's colours are tuned for
            text += "  (asked for on a simulator)"
    else:
        text = f"{getattr(detector, 'name', type(detector).__name__)} on this machine"
    if not sees:
        text += "  (once the body reports a camera)"
    return text


def _host_row(board: Any, hello: Any, host_camera: dict[str, Any] | None) -> str:
    """The board: where it is, which daemon answered, and what it has. The camera says whether
    it is the view the run steers by or one it is only shown.

    That is settled at connect, from what the body reports (`HostCameraAdapter.connect`), and
    this is written before. A body described with a camera keeps it, so the board's is an
    extra view. For one described without, the board's is the primary view only if the body
    does not report a camera of its own when it connects (an arm given --camera-url, a
    rosbridge base), so the row says so; `run_start` records which it was."""
    has: list[str] = []
    if hello.has_camera:
        primary = bool((host_camera or {}).get("primary"))
        has.append(
            "camera as the primary view unless the body reports its own"
            if primary
            else "camera as an extra view"
        )
    if hello.can_detect:
        has.append("detect")
    if hello.is_tegra:
        has.append("tegra")
    return f"{board.address}  daemon {hello.daemon_version}  " + (
        ", ".join(has) if has else "health only"
    )


def _host_doctor(choice: Any) -> str:
    """The doctor command that asks the same board with the same token. `--robot NAME` when the
    token is the one the robot keeps, because `doctor --host` alone sends no stored token and
    would report "wants a token" in place of whatever the run met. Otherwise `--host`, which
    asks the board and leaves the body alone: `doctor --robot` connects to a robot that keeps
    an address."""
    if not choice.token_stored:
        return f"quackd doctor --host {choice.host}"
    typed = f" --host {choice.host}" if choice.source == "--host" else ""
    return f"quackd doctor --robot {choice.robot}{typed}"


def _host_unreached_hint(choice: Any) -> str:
    """Where to see what the board says, and how to run without it, from wherever it was named:
    the flag, the robot that stores it, or the environment."""
    from quackd.host import HOST_ENV

    if choice.source == "--host":
        undo = "drop --host"
    elif choice.source == HOST_ENV:
        undo = f"unset {HOST_ENV}"
    else:
        undo = f"quackd robot edit {choice.robot or 'NAME'} --clear host"
    return f"{_host_doctor(choice)} shows what the board says; {undo} to run without it"


def _board_not_asked(resolved: Any, manifest: Any, command: str) -> str | None:
    """A note for `validate` and `list-verbs` about the board a run of this robot would use.

    Both read the body's own description and ask no board anything. A run adds the board's
    camera to a body described without one before it judges the task (`with_host_camera`),
    so for such a body these two can refuse a camera task a run accepts, and leave out the
    verbs the camera unlocks. Saying so keeps their answer from reading as final. None when no
    board is named for this robot, or the body has a camera of its own, whose vocabulary the
    board's camera does not change. A stored setting `resolve_host` refuses is left for the
    run to refuse in its own words."""
    from quackd.host import resolve_host

    if "camera" in manifest.sensors:
        return None
    stored = resolved.host_kwargs()
    try:
        choice = resolve_host(
            None,
            stored["host"],
            stored_token=stored["host_token"],
            robot=resolved.entry.name if resolved.entry is not None else None,
        )
    except ValueError:
        return None
    if choice.host is None:
        return None
    return (
        f"{command} does not ask {choice.named}, so the board's camera is not counted: a run "
        f"adds it to {resolved.label}, with the verbs a camera unlocks, when the board has one; "
        f"{_host_doctor(choice)} shows whether it does"
    )


def _header_rows(
    *,
    provider: Any,
    robot: str,
    seed: int | None,
    dry_run: bool,
    memory: Any,
    detector: Any = None,
    board: Any = None,
    hello: Any = None,
    backend: str | None = None,
    host_camera: dict[str, Any] | None = None,
    sees: bool = True,
    policy: Mapping[str, Any] | None = None,
    play: str | None = None,
    effort: str | None = None,
) -> list[tuple[str, Any]]:
    """The things worth knowing before a run starts, and nothing else: who pilots, which body,
    what reads its frames, the board when there is one, and the policy server the arm hands its
    segments to when there is one, with the checkpoint it said it serves when it was asked.
    `play` is the row `quackd.log.play_row` words, or None for a run that says nothing about
    play, whose header is what it was."""
    from quackd.log import policy_row

    rows: list[tuple[str, Any]] = [
        ("provider", f"{provider.name} ({provider.model or 'the first model it serves'})"),
    ]
    # only when the flag asked for it, so a header quoted anywhere from a run without one stays
    # what it was; the record has the level and where it came from on every run (`run_start`)
    if effort is not None:
        rows.append(("effort", f"{effort.strip().lower()} (--effort)"))
    rows.append(("robot", robot + (f"  seed {seed}" if seed is not None else "")))
    rows.append(("detector", _detector_row(detector, hello, backend, sees=sees)))
    if board is not None and hello is not None:
        rows.append(("host", _host_row(board, hello, host_camera)))
    if policy is not None:
        rows.append(("policy", policy_row(policy)))
    if play is not None:
        rows.append(("play", play))
    if dry_run:
        rows.append(
            (
                "mode",
                Text(
                    "DRY RUN: every intent is printed and nothing is sent", style=ui.STYLES["warn"]
                ),
            )
        )
    if memory is not None:
        m = memory.summary()
        rows.append(
            (
                "memory",
                Text.assemble(
                    f"{m['notes']} notes, {m['episodes']} earlier runs  ",
                    (f"{m['path']}", ui.STYLES["muted"]),
                    ("  --no-memory to run fresh", ui.STYLES["muted"]),
                ),
            )
        )
    return rows


def _fail(msg: str, code: int = 1, *, hint: str | None = None) -> None:
    """One line saying what went wrong, and one dim line saying where to look next.

    The message routinely names an extra (`quackd[anthropic]`) or a model's own brackets, so
    it travels as text rather than as markup Rich would eat."""
    ui.err_console.print(ui.fail_line(msg, hint=hint))
    if _FRONT is not None and _FRONT.outcome is None:
        _FRONT.refused(msg, hint)
    raise typer.Exit(code=code)


def _refusals(
    group: BaseExceptionGroup[Any], kinds: tuple[type[BaseException], ...]
) -> list[BaseException] | None:
    """Every exception in `group`, the groups inside it opened, when each is one of `kinds`,
    and None when any is not: a group holding a fault quackd never words is a traceback worth
    keeping, and one holding only refusals is the sentences they already are."""
    found: list[BaseException] = []
    for e in group.exceptions:
        inner = _refusals(e, kinds) if isinstance(e, BaseExceptionGroup) else None
        if inner is not None:
            found.extend(inner)
        elif isinstance(e, kinds):
            found.append(e)
        else:
            return None
    return found


def _robot_specs(
    robot: str | None, robots: str | None, duck: Any, *, registry_dir: str | None = None
) -> list[Any]:
    """The robots a command talks about, as `Resolved`: --robots, else --robot (a registered
    name or a spec), else the duck's own `robots:` default, else the Microduck simulator."""
    from quackd.adapters.factory import RobotSpec, parse_robot_spec, parse_robots
    from quackd.registry import Registry, Resolved, resolve_robot_ref

    if robots:
        # `--robots name=spec` is ad hoc by design: the names are this run's, not the
        # registry's, and a stored flock is spelled `--flock NAME` instead
        return [Resolved(spec) for spec in parse_robots(robots)]
    registry = Registry(registry_dir)
    default = duck.frontmatter.robots if duck is not None else None
    if isinstance(default, dict):
        if robot:
            return [resolve_robot_ref(robot, registry)]
        # the member names become the robot ids, as `--robots name=spec` would make them
        out = []
        for name, text in default.items():
            parsed = parse_robot_spec(text)
            out.append(Resolved(RobotSpec(parsed.adapter, parsed.backend, name)))
        return out
    if isinstance(default, str) or default is None:
        return [resolve_robot_ref(robot, registry, duck_default=default)]
    return [resolve_robot_ref(robot, registry)]


# ── validate ────────────────────────────────────────────────────────────────────────────


@app.command(rich_help_panel="Inspect")
def validate(
    duckfiles: list[str] = typer.Argument(..., help=".duck files, globs, or bundled names."),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Only print failures."),
    as_json: bool = _JSON,
    robot: list[str] | None = typer.Option(
        None,
        "--robot",
        "-r",
        help="Check the files against this robot's manifest (a registered name or "
        "<adapter>:<backend>; repeatable).",
    ),
    robots: str | None = typer.Option(
        None, "--robots", help="Check against a flock: name=<adapter>:<backend>,..."
    ),
    room: str | None = typer.Option(
        None,
        "--room",
        help="Check against the robot's simulator laid out in this room, as `run --room` lays "
        "it out, which offers the place verbs, explore and find.",
    ),
    registry_dir: str | None = _REGISTRY_DIR,
) -> None:
    """Validate .duck files against the spec and a robot's verbs. Exits 1 on any failure."""
    from quackd.adapters.base import AdapterError, policy_hint
    from quackd.adapters.factory import describe, installed_vocabulary
    from quackd.duckfile.parser import DuckParseError, load_duck
    from quackd.duckfile.validate import validate_duck
    from quackd.registry import Registry, RegistryError, resolve_robot_ref
    from quackd.verbs.registry import VerbRegistry

    registry_ref = Registry(registry_dir)

    # What a file that names no robot is checked against: every body installed here, with what
    # each offers a policy server, built once for all of them. It was the Microduck's list,
    # which refused an arm's task for verbs the arm has.
    vocabulary: VerbRegistry | None = None
    rows: list[dict[str, Any]] = []
    for path in _expand(duckfiles):
        row: dict[str, Any] = {"file": path, "name": None, "verbs": None, "robots": [], "ok": True}
        rows.append(row)
        try:
            duck = load_duck(path)
        except DuckParseError as e:
            row.update(ok=False, problems=[e.reason], summary=[e.reason])
            continue
        row["name"] = duck.name
        row["verbs"] = len(duck.frontmatter.verbs.allow)
        if duck.frontmatter.flock is not None:
            row["flock"] = len(duck.frontmatter.flock.member_names)
        try:
            if robot or robots:
                resolved = (
                    [resolve_robot_ref(r, registry_ref) for r in robot]
                    if robot
                    else _robot_specs(None, robots, duck)
                )
            elif duck.frontmatter.robots is not None:
                resolved = _robot_specs(None, None, duck)
            else:
                resolved = []
            if room is not None and not resolved:
                raise AdapterError(
                    "--room lays out a room for a robot's simulator, and this file names no "
                    "robot: name one with --robot"
                )
            manifests = [describe(r.spec, room=room) for r in resolved]
        except (AdapterError, RegistryError) as e:
            # a registered name means reading robots.json, and a broken one refuses
            row.update(ok=False, problems=[str(e)], summary=[str(e)])
            continue
        row["robots"] = [m.id for m in manifests]
        if not manifests and vocabulary is None:
            vocabulary = installed_vocabulary()
        problems = validate_duck(duck, manifests, registry=vocabulary)
        if problems:
            # `str(p)` names the field it came from and is what the plain lines under the
            # table carry; `p.message` is the sentence, which is what fits in a cell
            row.update(
                ok=False,
                problems=[str(p) for p in problems],
                summary=[p.message for p in problems],
            )
            # one body, as `run` has a board only for one: a fleet takes none, and no policy
            # server either, which is the only way an arm has pick and manipulate
            if len(resolved) == 1 and not robots and duck.frontmatter.flock is None:
                notes = [
                    note
                    for note in (
                        _board_not_asked(resolved[0], manifests[0], "validate"),
                        policy_hint(
                            [p.verb for p in problems if p.verb],
                            [resolved[0].spec.key],
                            "quackd run",
                        ),
                    )
                    if note is not None
                ]
                if notes:
                    row["notes"] = notes

    failures = [row for row in rows if not row["ok"]]
    if as_json:
        for row in rows:
            print(json.dumps({**row, "problems": row.get("problems", [])}))
        raise typer.Exit(code=1 if failures else 0)

    shown = failures if quiet else rows
    if shown:
        table = ui.table("quackd validate")
        # nothing here is no_wrap: a path can be any length, and a column that refuses to
        # wrap takes the width out of the one column that carries the answer
        table.add_column("file", overflow="fold")
        table.add_column("name")
        table.add_column("verbs", justify="right")
        table.add_column("result", ratio=2)
        for row in shown:
            table.add_row(*_validate_row(row))
        ui.console.print(table)
    if failures:
        # under the table as plain lines, so a long message survives any terminal width
        for row in failures:
            for problem in row.get("problems", []):
                ui.console.print(Text(f"  {row['file']}: {problem}"), soft_wrap=True)
        # once each, since every file checked against one robot carries the same one
        for note in dict.fromkeys(n for row in failures for n in row.get("notes", [])):
            ui.console.print(_warn_line(note), soft_wrap=True)
        _fail(f"{len(failures)} of {len(rows)} {_files(len(rows))} failed", hint=_VALIDATE_HINT)
    ui.console.print(_ok_line(f"{len(rows)} {_files(len(rows))} valid"))


_VALIDATE_HINT = "quackd list-verbs --robot <adapter>:<backend> shows what a body can do"


def _files(n: int) -> str:
    return "file" if n == 1 else "files"


def _ok_line(message: str) -> Any:
    return ui.Deferred(
        lambda g: Text.assemble((f"{g.ok} ", ui.STYLES["ok"]), (message, ui.STYLES["ok"]))
    )


def _warn_line(message: str) -> Any:
    """Something the run carried on without. `_fail` is for what it cannot carry on without."""
    return ui.Deferred(
        lambda g: Text.assemble((f"{g.warn} ", ui.STYLES["warn"]), (message, ui.STYLES["warn"]))
    )


def _validate_row(row: dict[str, Any]) -> list[Any]:
    """One line of the table, with everything a manifest or a parser wrote kept as text."""
    verbs = "-" if row["verbs"] is None else str(row["verbs"])

    def result(g: ui.Glyphs) -> Text:
        if not row["ok"]:
            out = Text(f"{g.fail} ", style=ui.STYLES["fail"])
            out.append("; ".join(row.get("summary", [])) or "invalid", style=ui.STYLES["fail"])
            return out
        out = Text(f"{g.ok} valid", style=ui.STYLES["ok"])
        if row.get("flock"):
            out.append(f" (flock of {row['flock']})", style=ui.STYLES["ok"])
        if row["robots"]:
            out.append(f" for {', '.join(row['robots'])}", style=ui.STYLES["muted"])
        return out

    return [Text(row["file"]), Text(row["name"] or "-"), verbs, ui.Deferred(result)]


# ── list-verbs ──────────────────────────────────────────────────────────────────────────

_SAFETY_STYLE = {"safe": "ok", "confirm": "warn", "dangerous": "fail"}


@app.command("list-verbs", rich_help_panel="Inspect")
def list_verbs(
    robot: str | None = typer.Option(
        None,
        "--robot",
        "-r",
        help="A robot's vocabulary: <adapter>:<backend>, or a registered name. Default Microduck.",
    ),
    registry_dir: str | None = _REGISTRY_DIR,
    as_json: bool = _JSON,
) -> None:
    """List every verb a robot provides, with params and safety class."""
    from quackd.adapters.base import AdapterError
    from quackd.adapters.factory import describe, registry_for
    from quackd.registry import Registry, RegistryError, resolve_robot_ref
    from quackd.verbs.registry import default_registry

    note: str | None = None
    try:
        if robot:
            resolved = resolve_robot_ref(robot, Registry(registry_dir))
            registry = registry_for(resolved.spec)
            note = _board_not_asked(resolved, describe(resolved.spec), "list-verbs")
        else:
            registry = default_registry()
    except (AdapterError, RegistryError) as e:
        _fail(str(e), hint=_ADAPTER_HINT)
        return
    if note is not None and as_json:
        # on stderr under --json, whose stdout is one verb per line and nothing else
        ui.err_console.print(_warn_line(note), soft_wrap=True)
    aliases: dict[str, list[str]] = {}
    for alias, target in registry.aliases().items():
        aliases.setdefault(target, []).append(alias)
    verbs = registry.verbs()
    if as_json:
        for v in verbs:
            print(
                json.dumps(
                    {
                        "name": v.name,
                        "aliases": aliases.get(v.name, []),
                        "kind": v.kind,
                        "core": v.core,
                        "safety": v.safety_class,
                        "params": v.param_summary(),
                        "description": v.description,
                    }
                )
            )
        return
    table = ui.table(f"verbs ({robot or 'microduck'})")
    # no_wrap on the name: a narrow terminal must never elide the one column you look up
    table.add_column("name", style=ui.STYLES["key"], no_wrap=True)
    table.add_column("aliases")
    table.add_column("kind")
    table.add_column("safety")
    table.add_column("params")
    table.add_column("description")
    for v in verbs:
        kind = Text(v.kind)
        if v.core:
            kind.append(" core", style=ui.STYLES["muted"])
        table.add_row(
            Text(v.name),
            Text(", ".join(aliases.get(v.name, [])), style=ui.STYLES["muted"]),
            kind,
            Text(v.safety_class, style=ui.STYLES[_SAFETY_STYLE.get(v.safety_class, "muted")]),
            Text(v.param_summary(), style=ui.STYLES["muted"]),
            Text(v.description),
        )
    core = sum(1 for v in verbs if v.core)
    table.caption = Text(
        f"{len(verbs)} verbs, {core} core. --robot <adapter>:<backend> for another body",
        style=ui.STYLES["muted"],
    )
    table.caption_justify = "left"
    ui.console.print(table)
    if note is not None:
        ui.console.print(_warn_line(note), soft_wrap=True)


@app.command("list-adapters", rich_help_panel="Inspect")
def list_adapters_cmd(as_json: bool = _JSON) -> None:
    """List the robot adapters this build knows, their backends and status."""
    from quackd.adapters.factory import list_adapters

    rows = list_adapters()
    if as_json:
        for row in rows:
            print(json.dumps(row))
        return
    ui.console.print(ui.adapters_table(rows))


def _vendor_hint(vendor: str) -> str:
    """The half-line beside a vendor in shell completion: what taking it bare would mean."""
    if vendor == "fake":
        return "scripted, no key and no network"
    default = default_model_for(vendor)
    return f"the default, {default}" if default else "the first model the server serves"


def _complete_llm(ctx: typer.Context, incomplete: str) -> list[tuple[str, str]]:
    """`--llm` in the shell: vendors before the colon, that vendor's ids after it.

    Reads the catalogue and nothing else. Every press of TAB runs this, and the factory next
    door imports pydantic, so completion that reached for it would make the shell pay for a
    validator in order to spell a model id.

    A vendor is offered twice, bare and with a colon, so one TAB takes its default model and a
    second carries on into its list. After a colon the ids offered are that vendor's own, which
    is why `--llm grok:gpt` offers nothing: it would be refused, and offering it would be
    completion arguing with the parser. A bare id completes too, both because `--llm
    claude-opus-5` is a legal spec on its own and because bash breaks its words at the colon and
    hands this only the half after it.
    """
    head, colon, prefix = incomplete.partition(":")
    vendor = head.strip().lower()
    if colon:
        return [
            (f"{vendor}:{m.id}", m.label) for m in models_for(vendor) if m.id.startswith(prefix)
        ]
    found = [(v, _vendor_hint(v)) for v in PROVIDER_NAMES if v.startswith(vendor)]
    found += [
        (f"{v}:", "then a model id") for v in PROVIDER_NAMES if v != "fake" and v.startswith(vendor)
    ]
    if head:
        # Only once something is typed: a bare TAB should offer the seventeen vendors, not the
        # hundred-odd ids underneath them.
        found += [
            (m.id, f"{v}: {m.label}")
            for v in CLOUD_NAMES
            for m in models_for(v)
            if m.id.startswith(head)
        ]
    return found


# ── list-models ───────────────────────────────────────────────────────────────────────────


MODEL_KINDS = ("pilot", "decision", "policy", "all")
"""What `quackd list-models --kind` lists: the pilot ids `--llm` takes, the decision LLMs
`--decision-llm` names, the policy types `quackd policy serve` serves and the ones it refuses,
or all three. The first is the default, so the command prints what it always has."""


@app.command("list-models", rich_help_panel="Inspect")
def list_models_cmd(
    llm: str | None = typer.Option(
        None,
        "--llm",
        "-l",
        help="One vendor only, e.g. --llm openai. A whole spec or a model id is read for its "
        "vendor, so --llm claude-opus-5 lists anthropic. Omitted: every vendor.",
        autocompletion=_complete_llm,
    ),
    kind: str = typer.Option(
        MODEL_KINDS[0],
        "--kind",
        metavar="pilot|decision|policy|all",
        help="Which models. pilot, the default, is the ids --llm takes, decision the decision "
        "LLMs --decision-llm names, policy the policy types quackd policy serve serves and the "
        "ones it refuses, and all is the three.",
    ),
    as_json: bool = _JSON,
) -> None:
    """List the models quackd can use: the ids each cloud vendor offers for --llm VENDOR:MODEL,
    and with --kind the decision LLMs and the learned policies as well."""
    wanted = kind.strip().lower()
    if wanted not in MODEL_KINDS:
        _fail(f"--kind is {', '.join(MODEL_KINDS[:-1])} or {MODEL_KINDS[-1]}, not {kind!r}")
        return
    if llm is not None and wanted != MODEL_KINDS[0]:
        _fail(
            f"--llm narrows the pilot rows only, and --kind {wanted} lists more than those",
            hint="drop --llm, or drop --kind",
        )
        return
    if wanted in ("pilot", "all"):
        _list_pilots(llm, as_json)
    if wanted in ("decision", "all"):
        _list_decision_llms(as_json)
    if wanted in ("policy", "all"):
        _list_policies(as_json)


def _list_pilots(llm: str | None, as_json: bool) -> None:
    """The pilot rows: every id each cloud vendor offers, or one vendor's, and the notes that say
    what the table cannot."""
    from quackd.agent.providers.base import ProviderError
    from quackd.agent.providers.factory import parse_llm

    provider = None
    if llm is not None:
        # A spec, a bare vendor or a bare id: whichever it is, what this command wants out of
        # it is the vendor, so it is read the same way `--llm` itself is read. Folded once and
        # used for both lookups: folding it for the vendor test and not for the catalogue one
        # refused `--llm CLAUDE-OPUS-5` while quoting back a string that works, which reads as
        # quackd disagreeing with itself about its own shift key.
        head = llm.split(":", 1)[0].strip().lower()
        provider = head if head in PROVIDER_NAMES else vendor_of(head)
        if provider is None:
            # A slash before the first colon is an OpenRouter id typed without its vendor, the
            # same reading `--llm` itself gives it (`factory._unknown_llm`).
            slashed = "/" in head and "openrouter" in OPEN_ENDED
            _fail(
                f"unknown provider {head!r}",
                hint=(
                    "an id with a slash reads as OpenRouter's: --llm openrouter"
                    if slashed
                    else f"one of: {', '.join(PROVIDER_NAMES)}"
                ),
            )
            return
    vendors = [provider] if provider in CLOUD_NAMES else list(CLOUD_NAMES)
    rows = [
        {
            "kind": "pilot",
            "provider": str(name),
            "id": m.id,
            "label": m.label,
            "status": m.status,
            "default": i == 0,
            "api": m.api,
            "vision": m.vision,
        }
        for name in vendors
        if provider is None or provider in CLOUD_NAMES
        for i, m in enumerate(models_for(str(name)))
    ]
    if as_json:
        for row in rows:
            print(json.dumps(row))
        return

    if rows:
        table = ui.table("models (--llm VENDOR:MODEL, QUACKD_LLM)")
        table.add_column("provider", style=ui.STYLES["key"], no_wrap=True)
        # An id is meant to be copied in after the colon, so it may wrap but never elide:
        # Rich's default would put an ellipsis through the middle of the one column that has to
        # survive an 80 column pipe intact.
        table.add_column("id", style=ui.STYLES["key"], overflow="fold")
        table.add_column("label", overflow="fold")
        table.add_column("status")
        table.add_column("notes")
        last = ""
        for row in rows:
            # Words, not glyphs: this table is read through a cp1252 pipe on Windows, where a
            # tick mark is the difference between a column and a row of question marks.
            marks: list[str] = []
            if row["default"]:
                marks.append("default")
            if row["api"] == "responses":
                marks.append("Responses API")
            if not row["vision"]:
                marks.append("no frames")
            table.add_row(
                Text(str(row["provider"]) if row["provider"] != last else ""),
                Text(str(row["id"])),
                Text(str(row["label"])),
                Text(str(row["status"])),
                Text(", ".join(marks), style=ui.STYLES["muted"]),
            )
            last = str(row["provider"])
        ui.console.print(table)

    notes: list[str] = []
    if provider is None or provider in LOCAL_NAMES:
        notes.append(
            f"{', '.join(LOCAL_NAMES)}: no catalogue. `--llm PRESET:MODEL` takes any id the "
            "server serves, and without one quackd takes the first entry of /v1/models."
        )
    if provider is None or provider == "fake":
        notes.append("fake: scripted, and a model after the colon is ignored.")
    for vendor in OPEN_ENDED:
        if provider is None or provider == vendor:
            # Nothing is fetched to print this: the list is read when a run starts, not here.
            name = {"openrouter": "OpenRouter"}.get(vendor, vendor)
            notes.append(
                f"{vendor}: the rows above are a selection. An id quackd does not carry can be "
                f"named as --llm {vendor}:AUTHOR/MODEL (or AUTHOR/MODEL:free). A few shapes of "
                f"id are refused on their spelling alone. Any other is checked against {name}'s "
                "public model list when a run starts. That list has to carry the id with tool "
                "calling, and one quackd takes is priced from it."
            )
    if pinned := os.environ.get(LLM_ENV):
        try:
            vendor, model_id = parse_llm(pinned, source=LLM_ENV)
            named = f"{vendor}:{model_id}" if model_id else f"{vendor}, its default model"
            notes.append(f"{LLM_ENV}={pinned} pins {named}.")
        except ProviderError as e:
            notes.append(f"{LLM_ENV}={pinned} is refused: {e}")
    for note in notes:
        ui.console.print(Text(note, style=ui.STYLES["muted"]), soft_wrap=True)


def _list_decision_llms(as_json: bool) -> None:
    """Every decision LLM quackd names, in the order `quackd doctor` prints them."""
    from quackd.agent.decision.catalogue import PRESET_NAMES, PRESETS

    rows = [PRESETS[name] for name in PRESET_NAMES]
    if as_json:
        for spec in rows:
            print(
                json.dumps(
                    {
                        "kind": "decision",
                        "name": spec.name,
                        "backend": spec.backend,
                        "model": spec.model,
                        "key_env": spec.key_env,
                        "extra": spec.extra,
                        "url": spec.url,
                        "price": spec.price.record() if spec.price else None,
                        "sees": spec.sees,
                    }
                )
            )
        return
    table = ui.table("decision LLMs (--decision-llm NAME[:MODEL], QUACKD_DECISION_LLM)")
    table.add_column("name", style=ui.STYLES["key"], no_wrap=True)
    table.add_column("model", style=ui.STYLES["key"], overflow="fold")
    table.add_column("key")
    table.add_column("extra")
    table.add_column("what it is", overflow="fold")
    for spec in rows:
        table.add_row(
            Text(spec.name),
            Text(spec.model or "the server's own"),
            Text(spec.key_env or "none"),
            Text(f"quackd[{spec.extra}]" if spec.extra else "none"),
            Text(spec.summary, style=ui.STYLES["muted"]),
        )
    ui.console.print(table)
    ui.console.print(
        Text(
            "Off unless a run names one. --decision-mode shadow asks it every turn and changes "
            "nothing, which is where to start (docs/models/decision-llms.md).",
            style=ui.STYLES["muted"],
        ),
        soft_wrap=True,
    )


def _list_policies(as_json: bool) -> None:
    """The policy types `quackd policy serve` serves, and the ones people ask about that it does
    not, with why. Read from `quackd.adapters.policies`, which imports nothing from the arm's
    package, so this lists them on a machine with no LeRobot."""
    from quackd.adapters.policies import FAMILIES, NOT_SERVED

    if as_json:
        for family in FAMILIES:
            print(
                json.dumps(
                    {
                        "kind": "policy",
                        "served": True,
                        "type": family.type,
                        "label": family.label,
                        "maker": family.maker,
                        "takes_words": family.takes_words,
                        "extra": family.extra,
                        "needs": list(family.needs),
                        "gpu": family.gpu,
                        "licence": family.licence,
                        "licence_url": family.licence_url,
                        "ran": family.ran,
                    }
                )
            )
        for refused in NOT_SERVED:
            print(
                json.dumps(
                    {
                        "kind": "policy",
                        "served": False,
                        "name": refused.name,
                        "maker": refused.maker,
                        "type": refused.type,
                        "why": refused.why,
                        "would_change": refused.would_change,
                    }
                )
            )
        return
    table = ui.table("policy types (quackd policy serve --policy OWNER/NAME@REVISION)")
    table.add_column("type", style=ui.STYLES["key"], no_wrap=True)
    table.add_column("family")
    table.add_column("maker", overflow="fold")
    table.add_column("words")
    table.add_column("needs", overflow="fold")
    table.add_column("has run", overflow="fold")
    for family in FAMILIES:
        table.add_row(
            Text(family.type),
            Text(family.label),
            Text(family.maker),
            Text("yes" if family.takes_words else "no"),
            Text("; ".join(family.needs) if family.needs else "nothing more"),
            Text(family.ran, style=ui.STYLES["muted"]),
        )
    ui.console.print(table)
    for refused in NOT_SERVED:
        ui.console.print(
            Text(
                f"not served: {refused.name}, by {refused.maker}: {refused.why}.",
                style=ui.STYLES["muted"],
            ),
            soft_wrap=True,
        )
    ui.console.print(
        Text(
            f"Each type is served on Python 3.12 with {FAMILIES[0].extra}, in a process of its "
            "own and never the arm's: beside the arm, on a rented GPU or on a Hugging Face "
            "Endpoint (docs/models/vlas.md).",
            style=ui.STYLES["muted"],
        ),
        soft_wrap=True,
    )


# ── run / record ────────────────────────────────────────────────────────────────────────


def _yes_to_go(_why: str) -> bool:
    """`--yes` answers the pilot's doubt the way it answers a confirm gate: go."""
    return True


_yes_to_go.answers_as = "--yes"  # type: ignore[attr-defined]
"""What the record names as having said go (`quackd.log.who_answered`), and never a person."""


class _TerminalHandOff:
    """The person at the robot, as a terminal.

    Enter is read through the kill switch rather than with an `input()` of its own. The switch
    already runs the only thread reading stdin, and a second reader would race it for the same
    keystroke: whichever lost would sit on a line the other had taken."""

    asks_a_person = True
    """There is a person at the robot: what this asks goes in the record as a `prompt`.

    Flatly true, and not `_can_prompt` like the three above, because the hand-off is only
    wired in at all where `_can_prompt()` has already said yes.

    A flock member and a `--dry-run` have no hand-off at all, so this mark is never the thing
    that decides it; it is here so the loop does not have to know which of its callables
    reaches a terminal."""

    WAIT_ENDED = {
        "enter": "(Enter)",
        "kill switch": "(the kill switch ended the wait, without an Enter)",
        "no keys": "(no key could be read, so the wait ended without an Enter)",
        "stopped": "(the run was stopped, which ended the wait without an Enter)",
        "timeout": "(nobody answered: the wait ran out without an Enter)",
        "declined": "(a line was typed rather than a bare Enter, which is a no)",
    }
    """What the saved terminal says about each way a wait ends, since none of them printed."""

    def __init__(self) -> None:
        self.switch: KillSwitch | None = None
        self.ended: str | None = None
        """How the last wait ended: `enter`, `kill switch` (a Ctrl-C, or `q`), `no keys` (no
        key thread, or stdin finished), `stopped` (an abort nobody pressed, on a wait that
        watches the abort flag) or `timeout`. `wait` still answers with a bool, which is
        all the first `--by-hand` wait needs, since it watches the abort flag and reads that
        after a False. The waits that watch a fresh key press instead, the hand-back at the end
        of a `--by-hand` run and the two questions about a fold that missed (`quackd.release`),
        at the end of a run and in `quackd robot release`, read this as well, because the abort
        flag is set on every run a person ended and says nothing about this wait, and a record
        that files a Ctrl-C under "nobody answered" says the room was empty when somebody in it
        pressed a key."""

    def bind(self, switch: KillSwitch) -> None:
        """The switch is built from the loop's own abort event, which does not exist until the
        loop does, and the loop is built from the config this object is already in."""
        self.switch = switch

    def say(self, text: str) -> None:
        with ui.pause_status():
            ui.err_console.print(Text(text, style=ui.STYLES["warn"]))

    async def wait(
        self,
        text: str,
        *,
        timeout_s: float | None = None,
        until_abort: bool = True,
        deliberate: bool = False,
    ) -> bool:
        if self.switch is None:  # `bind` runs before the loop does, so this is a bug if hit
            raise RuntimeError("the hand-off has no kill switch to read Enter from")
        self.say(text)
        self.ended = None
        # counted rather than read off `pressed`, which the wait itself clears on the way in: a
        # press is counted on the signal's own thread before the loop is told of it, so one
        # that ended this wait has always been counted by the time it returns
        presses = self.switch.presses
        came = await self.switch.wait_for_enter(
            timeout_s=timeout_s, until_abort=until_abort, deliberate=deliberate
        )
        if came:
            self.ended = "enter"
        elif self.switch.presses > presses:
            self.ended = "kill switch"
        elif self.switch.declined:
            self.ended = "declined"
        elif self.switch.keys_ended.is_set():
            self.ended = "no keys"
        elif until_abort and self.switch.abort.is_set():
            # a heartbeat that failed, or a flock stopping its members: an abort nobody pressed
            self.ended = "stopped"
        else:
            self.ended = "timeout"
        # Enter is a keystroke nobody printed, and not pressing it is the more interesting
        # half: a hand-off that timed out is why the arm was left where it was.
        ui.note(self.WAIT_ENDED[self.ended])
        return came

    def stopped(self) -> Awaitable[None]:
        """What returns when the kill switch fires from now on: a fresh Ctrl-C or `q`.

        How a person stops a fold they asked for (`quackd.release.fold_unless_stopped`), at the
        end of a run and in `quackd robot release`. Counted the way `wait` counts presses, so a
        press from before this was asked is not taken for one made now, and counted when this is
        called rather than when what it returns first runs: the fold is started straight after,
        and a press made as it starts used to fall in between and be taken for an old one. After
        the switch's first press, SIGINT goes back to the event loop's own handler, and every
        later Ctrl-C reaches the fold as a cancellation instead, which its caller catches; `q`
        reaches the switch every time, so this is what hears it."""
        if self.switch is None:  # `bind` runs before anything is folded, so a bug if hit
            raise RuntimeError("the hand-off has no kill switch to watch")
        switch = self.switch
        presses = switch.presses

        async def fresh_press() -> None:
            while switch.presses == presses:
                switch.pressed.clear()
                await switch.pressed.wait()

        return fresh_press()


_FRONT: Front | None = None
"""The page's end of this command, under the hidden `--front jsonl` that `quackd ui` starts
its runs with, or None at a terminal (`quackd.front`). Module level for the reason `_SWITCH`
is: the refusals and the prompts it changes are plain functions with no handle to pass it."""

FRONTS = ("terminal", "jsonl")

_FRONT_OPTION = typer.Option(
    "terminal",
    "--front",
    hidden=True,
    help="Who the command talks to: terminal, or jsonl for a program on the other end of a "
    "pipe, which is how the page quackd ui drives it.",
)


def _fronted(command: str, front: str, body: Callable[[], None]) -> None:
    """Run `body` behind the front end `front` names: the terminal, as always, or the page.

    Behind the page, the protocol owns stdout from the first line, and whatever happens, a
    refusal, a run's end, a crash or a second Stop, the last line it gets is one `end` naming the
    outcome and the exit code the process is about to leave with."""
    global _FRONT
    choice = (front or "").strip().lower() or FRONTS[0]
    if choice not in FRONTS:
        _fail(f"--front is {' or '.join(FRONTS)}, not {front!r}")
        return
    if choice == "terminal":
        body()
        return
    from quackd.front import Front

    opened = Front.open(command)
    _FRONT = opened
    code = 0
    fallback: str | None = None
    try:
        body()
    except typer.Exit as e:
        code = e.exit_code
        raise
    except KeyboardInterrupt:
        # the page's second Stop, which quits at once as a second Ctrl-C does, and leaves with
        # the code a command a Ctrl-C ended leaves with
        code = 130
        if opened.outcome is None or opened.outcome.get("outcome") == "refused":
            opened.outcome = {"outcome": "aborted", "reason": "quit at once from the page"}
        raise
    except BaseException as e:
        code = 1
        opened.errored(f"{type(e).__name__}: {e}")
        fallback = f"{type(e).__name__}: {e}"
        raise
    finally:
        _FRONT = None
        opened.end(code, fallback=fallback)


_SWITCH: KillSwitch | None = None
"""The kill switch of the run that is going on, or None outside one.

Module level because the prompts below are plain callables handed to `RunConfig` long before
the loop they will be asked from exists, and the switch is built from that loop's own abort
event. `_ask` is the only reader."""


def _ask(question: str) -> bool:
    """A yes or no question, read off the terminal without racing the run for the keystroke.

    `typer.confirm` calls `input()`, and the kill switch's key thread is reading the same
    terminal: whichever of the two took a character first kept it, so a gate asked on a real
    terminal waited for a newline that had already been swallowed, with a robot mid-verb.
    Where a switch is running its own reader answers; where none is, this is `typer.confirm`
    as it always was.

    Both branches tell the saved terminal what was typed, because neither of them printed it:
    the question goes out raw and the answer is echoed by the terminal driver, so a tee on
    quackd's own output sees the asking and not the answering. A run with `--yes` never
    reaches here, which is why a recorded question always means a person was really asked."""
    if _FRONT is not None:
        # every question a run driven from the page puts goes to the page, and a prompt here
        # would read the page's own lines off stdin (`quackd.front`)
        raise RuntimeError("a run driven from the page puts no question to a terminal")
    switch = _SWITCH
    if switch is None:
        agreed = typer.confirm(question, default=False)
        ui.note(f"{question} [y/N]: {'y' if agreed else 'n'}")
        return agreed
    answer = switch.ask(f"{question} [y/N]: ")
    ui.note(f"{question} [y/N]: {answer.strip() or '(Enter)'}")
    return answer.strip().lower() in ("y", "yes")


def _confirm_prompt(name: str, params: dict[str, Any]) -> bool:
    # under a running status line the question is invisible: a live region redirects stdout
    # and a prompt writes without a newline, so it stays buffered until it is too late.
    # `confirm_question` and not the raw dict, because the executor writes the same sentence into
    # the record and the record has to quote the question in the words it was asked in.
    from quackd.answers import confirm_question

    with ui.pause_status():
        return _ask(confirm_question(name, params))


def _decide_prompt(why: str) -> bool:
    """Asked when the pilot says it is not sure this body can do the task at all."""
    with ui.pause_status():
        ui.err_console.print(Text(why, style=ui.STYLES["warn"]))
        return _ask(GO_AHEAD)


def _acknowledge_prompt(why: str) -> bool:
    """Asked once, before anything moves, when the human is the only safety left."""
    with ui.pause_status():
        ui.err_console.print(Text(why, style=ui.STYLES["warn"]))
        return _ask(WATCHING)


def _judge_prompt(why: str) -> bool:
    """Asked once, after the last segment of a `--controller vla` run: its pilot cannot tell
    whether the task was done, so the person who watched the arm says."""
    with ui.pause_status():
        ui.err_console.print(Text(why, style=ui.STYLES["warn"]))
        return _ask(DID_IT)


def _someone_to_ask() -> bool:
    """A person to put a run's questions to: at the page the run is driven from, or at a
    terminal."""
    return _FRONT is not None or _can_prompt()


def _front_refusal(
    *, by_hand: bool, flock: str | None, robots: str | None, duck: Any, specs: Sequence[Any]
) -> tuple[str, str] | None:
    """Why the page cannot drive this run, as a sentence and a hint, or None.

    The page drives one robot and puts its questions in the page. `--by-hand` waits for Enter
    from somebody at the arm, and a flock runs every member behind a kill switch of its own,
    which no Stop in the page would reach, so each is refused before anything is built."""
    if by_hand:
        return (
            "--by-hand waits for somebody at the arm to press Enter, and this run is driven "
            "from the page",
            "run it from a terminal, or drop --by-hand and start from the rest pose",
        )
    if flock is not None or robots:
        return (
            "the page drives one robot at a time, and "
            f"{'--flock' if flock is not None else '--robots'} names several",
            "run a flock from a terminal",
        )
    if duck is not None and duck.frontmatter.flock is not None:
        return (
            f"{duck.name} is a task for a flock, and the page drives one robot at a time",
            "run it from a terminal",
        )
    if len(specs) > 1:
        return (
            "the page drives one robot at a time, and this task names several",
            "run it from a terminal",
        )
    return None


def _warn(text: str) -> None:
    """A warning said before a run starts: on the terminal as always, and to the page as a
    notice where one is reading (`quackd.front`)."""
    ui.console.print(_warn_line(text), soft_wrap=True)
    if _FRONT is not None:
        _FRONT.notice(text)


def _frame_hook(recorder: Any, front: Front | None) -> Any:
    """The run's frame callback: the GIF recorder's, the page's, both, or none."""
    hooks = [
        hook
        for hook in (
            recorder.capture if recorder is not None else None,
            front.frame if front is not None else None,
        )
        if hook is not None
    ]
    if len(hooks) < 2:
        return hooks[0] if hooks else None

    def both(image: Any, caption: str) -> None:
        for hook in hooks:
            hook(image, caption)

    return both


def _a_person_is_there() -> bool:
    """`_can_prompt` looked up now rather than bound now, because it is the seam the tests
    replace and a reference taken at import would not see the replacement."""
    return _can_prompt()


_confirm_prompt.asks_a_person = _a_person_is_there  # type: ignore[attr-defined]
_decide_prompt.asks_a_person = _a_person_is_there  # type: ignore[attr-defined]
_acknowledge_prompt.asks_a_person = _a_person_is_there  # type: ignore[attr-defined]
_judge_prompt.asks_a_person = _a_person_is_there  # type: ignore[attr-defined]
"""These four are the ones that reach a terminal, and `allow_all`, `deny_all`, `_yes_to_go`
and the flock's standing answers are not. The mark is `_can_prompt` rather than `True` because
reaching a terminal is a thing to check at the moment of asking and not a property of the
function: these same four run under `yes | quackd run` and under `quackd run < answers.txt`,
where `input()` reads the pipe and returns a yes nobody said. The gate still opens, because
that is what the pipe asked for and it is what quackd has always done; what must not happen is
the record then testifying that a person cleared it. A judgement from a pipe is not even
believed: a `--controller vla` run succeeds only on a person's yes."""


def _vla_refusal(
    *,
    yes: bool,
    dry_run: bool,
    has_policy: bool,
    goal: str | None,
    ignored: Sequence[str],
    someone: bool,
    command: str = "run",
) -> tuple[str, str] | None:
    """Why `--controller vla` cannot fly this run, as a sentence and a hint, or None.

    Its pilot has no judgement of its own, so a person is its whole verdict: asked before the
    first segment whether the body should try, and after the last whether it did. Every flag
    that would take that person away, or leave them nothing real to judge, is refused here,
    before anything is built, connected or written down. `ignored` are the flags given for a
    model that this pilot would drop without a word, and `someone` whether a person can be asked
    at all: at the page, at the terminal, or for a drive session at its keyboard. `command` is
    the one refused, `run` or `drive`, which the words name."""
    from quackd.duckfile.schema import instruction_line

    if yes:
        return (
            "--controller vla leaves the verdict on the task to a person who watched the arm, "
            "and --yes answers every question without asking anybody",
            "drop --yes: a vla run asks you before its first segment and after its last",
        )
    if not someone:
        return (
            "--controller vla asks a person whether the arm did the task, and there is no "
            "terminal to ask on",
            "run it from a terminal, not through a pipe or a script",
        )
    if dry_run:
        return (
            "--controller vla asks whether the arm did the task, and --dry-run moves nothing "
            "for anybody to judge",
            "rehearse it on the arm's simulator instead: --robot lerobot:mujoco",
        )
    if ignored:
        return (
            "--controller vla is a scripted pilot that asks no model and sees no picture, so "
            f"{_and(list(ignored))} would do nothing",
            "drop it, or fly the run with --controller llm",
        )
    if not has_policy:
        return (
            "--controller vla hands each instruction to the arm's learned policy, and this "
            f"{'run' if command == 'run' else 'session'} names no policy server",
            f"start one with quackd policy serve, and give quackd {command} its address with "
            "--policy-url",
        )
    if goal is not None:
        try:
            instruction_line(goal)
        except ValueError as e:
            return (
                "--controller vla tells the arm's policy the goal word for word, as its one "
                f"instruction, and {e}",
                "give one short subtask as --goal, or list the subtasks under "
                "policy.instructions in a task file at duck: 3 or later",
            )
    return None


def _parse_flock_flag(flock: str | None, registry_dir: str | None) -> tuple[int | None, Any]:
    """`--flock` is either a count of simulated ducks or the name of a stored flock.

    All digits is the count, because that is what it has always meant and `check_name` refuses
    to register anything that could be read as one. Anything else is a name, and its roster is
    read now rather than at the first connection, so a flock with a hole in it refuses before
    a run directory exists."""
    from quackd.registry import Registry, RegistryError

    if flock is None:
        return None, None
    flock = flock.strip()
    if flock.isdigit():
        return int(flock), None
    try:
        return None, Registry(registry_dir).roster(flock)
    except RegistryError as e:
        _fail(str(e), hint="quackd flock list, or a number for N simulated ducks")
        raise


def _run_impl(
    duckfile: str | None,
    goal: str | None,
    llm: str | None,
    seed: int | None,
    dry_run: bool,
    max_steps: int | None,
    runs_dir: str,
    yes: bool,
    live: bool,
    address: str | None,
    camera_url: list[str],
    token: str | None,
    fov_deg: float | None,
    gif: bool,
    gif_size: int,
    verbose: bool,
    base_url: str | None = None,
    api_key: str | None = None,
    vision: bool | None = None,
    extra_body: str | None = None,
    flock: str | None = None,
    *,
    decision_llm: str | None = None,
    decision_url: str | None = None,
    decision_mode: str | None = None,
    decision_frames: bool | None = None,
    images: Sequence[str] = (),
    by_hand: bool = False,
    robot: str | None = None,
    robots: str | None = None,
    memory: bool = True,
    memory_dir: str | None = None,
    registry_dir: str | None = None,
    log_on: bool | None = None,
    log_prompt: bool | None = None,
    run_name: str | None = None,
    price: str | None = None,
    host: str | None = None,
    host_token: str | None = None,
    detector_choice: str | None = None,
    policy_url: str | None = None,
    policy_token: str | None = None,
    accept_other_frame: bool = False,
    async_chunks: bool = True,
    room: str | None = None,
    map_address: str | None = None,
    controller: str | None = None,
    play: bool | None = None,
    effort: str | None = None,
) -> None:
    from quackd.adapters.base import AdapterError as _AdapterError
    from quackd.adapters.base import policy_choice, policy_hint
    from quackd.adapters.factory import describe, make_adapter, registry_for
    from quackd.adapters.host_camera import EXTRAS_KEY as HOST_CAMERA_EXTRAS
    from quackd.adapters.host_camera import with_host_camera
    from quackd.agent.decision.base import DecisionError
    from quackd.agent.decision.factory import ENV_LLM as DECISION_LLM_ENV
    from quackd.agent.decision.factory import PRICE_ENV as DECISION_PRICE_ENV
    from quackd.agent.decision.factory import (
        decision_llm_is_available,
        make_decision_llm,
        parse_decision_llm,
        resolve_decision_address,
        resolve_decision_frames,
        resolve_decision_mode,
        resolve_decision_price,
    )
    from quackd.agent.images import TaskImageError, load_task_images
    from quackd.agent.loop import RunConfig, resolve_play, run_duck
    from quackd.agent.providers.base import LLMProvider, ProviderError
    from quackd.agent.providers.factory import make_provider, resolve_llm
    from quackd.agent.providers.pricing import parse_price as _parse_price
    from quackd.agent.transcript import run_label
    from quackd.duckfile.parser import DuckParseError, duck_from_goal, load_duck
    from quackd.duckfile.schema import AUCTION_MAX_MEMBERS, PILOTS_MAX_MEMBERS
    from quackd.duckfile.validate import validate_duck
    from quackd.flock.pilots import ADVISORY_FIELDS, roster_from_specs
    from quackd.flock.runner import member_specs
    from quackd.host import (
        HostChoice,
        HostClient,
        HostError,
        HostHello,
        reach_host,
        resolve_host,
        unreached,
    )
    from quackd.log import (
        ConsoleLog,
        fan_out,
        log_enabled_default,
        play_row,
        prompt_shown_default,
        thinking_limit_default,
    )
    from quackd.perception import DETECTOR_CHOICES, detector_for, explicit_detector
    from quackd.registry import RegistryError, Resolved, no_rest_pose_refusal
    from quackd.safety import Aborted, KillSwitch, allow_all
    from quackd.space.room import RoomError, load_room
    from quackd.transport.base import TransportError

    if (duckfile is None) == (goal is None):
        _fail('give either a .duck file (or bundled name) or --goal "...", not both')
        return
    # Both before anything is built, connected to or written down: a name that cannot be a
    # directory and a price nobody can parse are typing mistakes, and a typing mistake should
    # cost you one sentence rather than a robot moving and a run directory to clean up after.
    # `QUACKD_DECISION_PRICE` is here rather than beside the stepper for the same reason as
    # the other two: it has no flag of its own, so an unparseable line in a `.env` would
    # otherwise surface as a traceback out of the first turn that needed a rate.
    checks = (
        (run_name, run_label),
        (price, lambda t: _parse_price(t, source="--price")),
        (
            # `or None` because a blank variable is a shell saying unset, which is how the
            # suite clears it and how a `.env` line with nothing after the `=` reads.
            os.environ.get(DECISION_PRICE_ENV) or None,
            lambda t: _parse_price(t, source=DECISION_PRICE_ENV),
        ),
    )
    for text, check in checks:
        if text is None:
            continue
        try:
            check(text)
        except ValueError as e:
            _fail(str(e))
            return
    detector_choice = (detector_choice or "").strip().lower() or None
    if detector_choice is not None and detector_choice not in DETECTOR_CHOICES:
        _fail(f"--detector is one of {', '.join(DETECTOR_CHOICES)}, not {detector_choice!r}")
        return
    try:
        # the policy server the arm hands its segments to, from the flags alone: no variable
        # names one, so a policy drives the arm only on a run that says so
        policy = policy_choice(
            policy_url,
            policy_token,
            accept_other_frame=accept_other_frame,
            async_chunks=async_chunks,
            registry_dir=registry_dir,
        )
    except ValueError as e:
        _fail(str(e))
        return
    # A room is read before anything is built, so a name nobody ships costs one sentence.
    if room is not None:
        try:
            load_room(room)
        except RoomError as e:
            _fail(str(e))
            return
    # the policy server and the room, each passed to `describe` and `make_adapter` only when
    # there is one, so every other body is asked exactly as it always was
    body_kw: dict[str, Any] = {} if policy is None else {"policy": policy}
    if room is not None:
        body_kw["room"] = room
    # any spelling of nothing is the default, as a blank --detector is
    flown_by = (controller or "").strip().lower() or CONTROLLERS[0]
    if flown_by not in CONTROLLERS:
        _fail(f"--controller is {' or '.join(CONTROLLERS)}, not {controller!r}")
        return
    vla = flown_by == "vla"
    if vla:
        refused = _vla_refusal(
            yes=yes,
            dry_run=dry_run,
            has_policy=policy is not None,
            goal=goal,
            someone=_someone_to_ask(),
            ignored=[
                flag
                for flag, given in (
                    ("--llm", llm is not None),
                    ("--base-url", base_url is not None),
                    ("--api-key", api_key is not None),
                    ("--extra-body", extra_body is not None),
                    ("--vision", vision is not None),
                    ("--image", bool(images)),
                    # --no-play is not here: it turns a played file's part off, which is what
                    # lets that file's instructions fly, and with no part to turn off it says
                    # what is so, as it does under the default controller
                    ("--play", play is True),
                )
                if given
            ],
        )
        if refused is not None:
            _fail(refused[0], hint=refused[1])
            return
    flock_n, roster = _parse_flock_flag(flock, registry_dir)
    flock_name = flock if roster is not None else None
    if roster is not None and (robot or robots):
        _fail("--flock NAME brings its own robots: drop --robot and --robots")
        return
    if roster is not None and (address or camera_url or token or map_address):
        # they would be silently dropped: both flock paths read each member's own
        _fail(
            "--flock NAME takes every member's address, map address, token and camera from the "
            "registry",
            hint="quackd robot edit NAME to change one",
        )
        return
    try:
        duck = load_duck(duckfile) if duckfile is not None else None
        resolved = (
            [Resolved(entry.robot_spec, entry) for entry in roster.values()]
            if roster is not None
            else _robot_specs(robot, robots, duck, registry_dir=registry_dir)
        )
        specs = [r.spec for r in resolved]
        here = resolved[0]
        spec = here.spec
    except (DuckParseError, TransportError, RegistryError) as e:
        _fail(str(e))
        return
    if _FRONT is not None:
        refused = _front_refusal(
            by_hand=by_hand, flock=flock, robots=robots, duck=duck, specs=specs
        )
        if refused is not None:
            _fail(refused[0], hint=refused[1])
            return
    if vla and duck is not None and resolve_play(duck.frontmatter.play, play)[0]:
        # Here rather than with the flags above, which run before the file is read, and before
        # the instructions, which a played file would otherwise be sent to add first. The
        # policy flies the arm with no pilot to read a part, so a run recorded as played would
        # be one nobody played. --no-play is the way out only for a file with instructions.
        _fail(
            f"{duck.name} gives its pilot a part to play, and --controller vla flies the arm "
            "with no pilot to read it",
            hint="run it with --no-play, or without --controller vla"
            if duck.frontmatter.effective_policy.instructions
            else "run it without --controller vla, or list policy.instructions in it and run "
            "it with --no-play",
        )
        return
    if vla and duck is not None and not duck.frontmatter.effective_policy.instructions:
        # a goal is its own one instruction, and was held to the rule for one above
        _fail(
            "--controller vla tells the arm's policy the instructions a task file lists, and "
            f"{duck.name} lists none",
            hint="list them under policy.instructions in a task file at duck: 3 or later "
            "(docs/reference/duck-spec.md), or give --goal",
        )
        return
    # Before the dispatch below, because a flock takes neither of the two flags and dropping
    # one silently is the failure both of them exist to prevent: a task about a picture that
    # never arrived, and an arm nobody was asked to place. A goal is never a flock.
    several = (
        flock_n is not None
        or roster is not None
        or (duck is not None and duck.frontmatter.flock is not None)
        or len(specs) > 1
    )
    # A host is one machine: one camera, one detector, the health of one board. A fleet is
    # several bodies, and one --host would have to be every member's camera at once, so it is
    # refused here with --image and --by-hand, before anything connects. `--robots` counts even
    # with one member in it, because it is the fleet spelling. A host stored with a member is
    # the same claim made in robots.json, and is refused by that member's name. QUACKD_HOST is
    # not: it is the board you usually use rather than a claim about these bodies, and for a
    # fleet it moves only the local presets' model server, which LocalProvider reads itself.
    fleet = several or bool(robots)
    hosted = [r.entry.name for r in resolved if r.entry is not None and r.entry.host]
    if fleet and (host or "").strip():
        _fail(
            "--host names one machine's camera and detector, and a fleet has several bodies",
            hint="drop --host, or run the task on one body at a time",
        )
        return
    if fleet and hosted:
        _fail(
            f"{hosted[0]} has a host in robots.json, which names one machine's camera and "
            "detector, and a fleet has several bodies",
            hint=f"quackd robot edit {hosted[0]} --clear host, or run it on its own",
        )
        return
    if fleet and (host_token or "").strip():
        # refused as --host is, rather than dropped: `resolve_host` refuses a typed token with
        # no board to go to, and a fleet never reaches it
        _fail(
            "--host-token is one board's token, and a fleet has several bodies and no board",
            hint="drop --host-token, or run the task on one body at a time",
        )
        return
    if fleet and policy is not None:
        # one server, one session at a time, and one arm's calibration it was checked against:
        # every flock path builds its own members, so it would be dropped without a word
        _fail(
            "--policy-url is one arm's policy server, and a fleet has several bodies",
            hint="drop --policy-url, or run the task on one arm at a time",
        )
        return
    if fleet and room is not None:
        # every flock path builds its own members in the arena, so it would be dropped too
        _fail(
            "--room lays out one simulator's room, and a fleet has several bodies",
            hint="drop --room, or run the task on one duck",
        )
        return
    if fleet and map_address is not None:
        _fail(
            "--map-address is where one robot's map answers, and a fleet has several bodies",
            hint="drop --map-address, or keep each robot's own: quackd robot edit NAME "
            "--map-address ...",
        )
        return
    # The one place this run's board is settled, so everything that uses the board reads this
    # value rather than deriving its own. A fleet has none, whatever the environment says.
    stored = here.host_kwargs()
    try:
        host_choice = (
            HostChoice()
            if fleet
            else resolve_host(
                host,
                stored["host"],
                token=host_token,
                stored_token=stored["host_token"],
                robot=here.entry.name if here.entry is not None else None,
            )
        )
    except ValueError as e:
        _fail(str(e))
        return
    # The board is asked what it is before the task file is judged and before anything is
    # built, because both depend on the answer: a camera on the board is a camera the body
    # has, so a camera task on a blind body is not refused for a camera the run will have, and
    # a board that does not answer is a run refused while nothing is powered.
    board: HostClient | None = None
    hello: HostHello | None = None
    try:
        reached = reach_host(host_choice)
    except HostError as e:
        _fail(unreached(host_choice, e), hint=_host_unreached_hint(host_choice))
        return
    if reached is not None:
        board, hello = reached
    try:
        if goal is not None:
            # the union across the flock, so a goal run on mixed bodies allows what any of
            # them can do; each member is then trimmed to its own half of that. With a board,
            # the one body's vocabulary includes what the board's camera lets it do, and with a
            # policy server what the arm's policy does.
            vocabularies = []
            for one in specs:
                body = describe(one, **body_kw)
                vocabularies.append(
                    registry_for(one, with_host_camera(body, hello) if hello is not None else body)
                )
            safe = sorted(
                {
                    v.name
                    for vocabulary in vocabularies
                    for v in vocabulary.verbs()
                    if v.safety_class == "safe"
                    # `watch` only on a run that asked for frames, the one kind that can offer it
                    and (v.name != "watch" or _frames_asked(decision_frames, decision_llm))
                }
            )
            # and `manipulate` with a policy server, which is no safe verb: allowed, and asked
            # about before each segment rather than left out, or a goal could never use it
            gated = (
                ["manipulate"]
                if policy is not None and any("manipulate" in v for v in vocabularies)
                else []
            )
            duck = duck_from_goal(goal, safe, confirm=gated, memory=memory)
        assert duck is not None
        # Refuse before connecting, with the validator's words. `serve-mcp` has always done
        # this; `run` never did, and reached the loop's tool_schemas and died on a raw
        # VerbNotFound with the robot already connected and a run directory already made.
        manifests = [describe(s, **body_kw) for s in specs]
        # the body as its adapter describes it, before the board's camera joins it: all a
        # detector built before connect may know about the lens (below)
        described = manifests[0]
        if hello is not None:
            manifests[0] = with_host_camera(described, hello)
        section = duck.frontmatter.flock
        method = (
            section.allocation.method
            if section is not None
            else ("pilots" if roster is not None else None)
        )
        # a task file with no `flock:` block, run against a stored flock, is still a flock:
        # judged body by body the arm would be refused for not being able to walk. And a
        # pilot flock drops the advisory `verbs.allow` line, because trimming each member to
        # its own vocabulary is its answer to it (`pilots.ADVISORY_FIELDS`).
        problems = [
            p
            for p in validate_duck(duck, manifests, flock=True if roster is not None else None)
            if not (method == "pilots" and p.field in ADVISORY_FIELDS)
        ]
    except (DuckParseError, TransportError, RegistryError) as e:
        _fail(str(e))
        return
    if problems:
        _fail(
            f"{duck.name} cannot run on {', '.join(s.key for s in specs)}: "
            + "; ".join(p.message for p in problems),
            # an arm's pick and manipulate come only from --policy-url, which a fleet refuses
            hint=None
            if fleet or policy is not None
            else policy_hint(
                [p.verb for p in problems if p.verb], [s.key for s in specs], "quackd run"
            ),
        )
        return
    if flock_n is not None and not 2 <= flock_n <= 4:
        _fail("a flock needs 2 to 4 ducks (drop --flock for a single run)")
        return
    if detector_choice is not None and fleet:
        # every flock path builds its own members' detectors, so a choice made here would be
        # dropped without a word, which is the thing a flag must never do. `--robots` counts
        # even with one member, as it does for --host: serve-mcp serves one member as a fleet
        # and would drop the choice there, and the two commands keep one rule
        _fail(
            "--detector is for one robot, and a fleet has several bodies",
            hint="drop --detector, or run the task on one body at a time",
        )
        return
    task_images: list[Any] = []
    if images:
        if several:
            _fail(
                "--image is for one robot, and this run has several",
                hint="drop --flock and --robots, or run the task on one body at a time",
            )
            return
        try:
            task_images = load_task_images(list(images))
        except TaskImageError as e:
            _fail(str(e))
            return
    if by_hand:
        if several:
            _fail(
                "--by-hand is one person placing one arm, and this run has several robots",
                hint="drop --flock and --robots",
            )
            return
        if dry_run:
            # a dry run moves nothing at either end, and taking torque off an arm is the one
            # thing here that is not a command to the robot but a change to it
            _fail(
                "--by-hand and --dry-run ask for opposite things: one takes torque off the "
                "arm, the other moves nothing",
                hint="rehearse the task with --dry-run, then run it again with --by-hand",
            )
            return
        if not _can_prompt():
            _fail(
                "--by-hand waits for you to press Enter, and there is no terminal to ask on",
                hint="run it from a terminal, or drop the flag and start from the rest pose",
            )
            return
    # A real arm with no rest pose has nothing to be folded to at the end of a run, and its
    # close keeps its torque rather than let it fall where it stands, so the run would leave it
    # holding itself up wherever it stopped (ADR-0058). Refused once the task file has been
    # judged against the body, so a task that could never run on it says so first rather than
    # sending a person off to fold the arm, and before any provider, adapter or run directory
    # exists, a dry run among them, since a dry run connects the arm too.
    for each in resolved:
        refused = no_rest_pose_refusal(
            each,
            what="a run, a dry one included",
            address=address,
            camera_url=camera_url,
            fleet=fleet,
        )
        if refused is not None:
            _fail(refused[0], hint=refused[1])
            return
    # Resolved here rather than beside the solo run below, because both flock branches return
    # before that point: `--decision-mode maybe` on a flock used to run the robots anyway, and
    # a mode that was spelled right used to be accepted and silently do nothing.
    try:
        named_decision = parse_decision_llm(decision_llm)
        # Said on the line rather than merely absent. `--decision-llm off` is how one command
        # opts out of a `QUACKD_DECISION_LLM` in a `.env`, and refusing it because the same
        # `.env` also set a mode, or asked for frames, would answer "I do not want this" with a
        # demand to name one.
        said_off = (decision_llm or "").strip().lower() == "off"
        decision = resolve_decision_mode(
            decision_mode, named=named_decision is not None, refused=said_off
        )
        # Whether a camera frame may go, which the mode does not decide, so a run that asks for
        # frames builds its decision LLM whatever the mode. Refused here, before anything
        # connects and on every machine, for a run with no decision LLM named, one that cannot
        # see, or an address that is not OpenAI's own.
        frames = resolve_decision_frames(
            decision_frames,
            spec=named_decision[0] if named_decision is not None else None,
            url=decision_url,
            refused=said_off,
        )
        if named_decision is not None and (decision != "off" or frames):
            # Here rather than when the client is built below, because the client is built only
            # on a machine that has the SDK and the key, and everywhere else the run would go
            # ahead without its stepper. An address the named row can never be handed is a typo,
            # and a typo stops the run on every machine.
            resolve_decision_address(named_decision[0], decision_url)
    except DecisionError as e:
        _fail(str(e))
        return
    if vla and named_decision is not None and (decision != "off" or frames):
        # a stepper answers the turns a model would have been asked, and this pilot is no model:
        # it would take the scripted pilot's turns with nobody having asked it to
        _fail(
            "--controller vla runs no model for a decision LLM to step in front of, and "
            f"{'--decision-llm' if decision_llm is not None else DECISION_LLM_ENV} names one",
            hint="drop it, or pass --decision-llm off for this run",
        )
        return
    if flock_n is not None or roster is not None or duck.frontmatter.flock is not None:
        if method != "pilots":
            # A coordinator flock's members follow its plan, and none of them is a pilot a part
            # could reach, so a part, and either flag about one, is refused rather than dropped.
            # The file first, since no flag lifts its refusal, and both before the warning
            # below, which would otherwise speak of a run that never starts.
            if duck.frontmatter.play:
                _fail(
                    f"{duck.name} gives its pilot a part to play, and a coordinator flock has "
                    "no pilot to play it",
                    hint="run it on one robot, or as a pilot flock with "
                    "flock.allocation.method: pilots",
                )
                return
            if play is not None:
                _fail(
                    "a coordinator flock has no pilot for --play or --no-play to reach: its "
                    "members follow the flock's plan",
                    hint="drop the flag, or run the task as a pilot flock",
                )
                return
        if (decision != "off" or frames) and named_decision is not None:
            # One loop per member, each with its own executor and budget, and the stepper is
            # built per loop. Wiring it through a flock is a thing to do deliberately with a
            # measurement in hand, not a thing to leave half done and unsaid. No member builds a
            # decision LLM, so no member shows one a frame either, and the line says so where a
            # run asked for frames.
            ui.console.print(
                _warn_line(
                    f"--decision-llm {named_decision[0].name} does not apply to a flock: "
                    "every member is piloted by its model, as before"
                    + (", and no camera frame is sent" if frames else "")
                ),
                soft_wrap=True,
            )
        if method == "pilots":
            if roster is None and section is not None:
                # `flock.members` plus `robots:` or `--robots` names the bodies without a
                # registry; a stored flock names them with one
                try:
                    roster = roster_from_specs(
                        member_specs(
                            section.member_names,
                            {s.name: s.key for s in specs if s.name} or None,
                            duck.frontmatter.robots,
                            # a pilot flock is N bodies of any kind, so an unnamed member gets
                            # this machine's default rather than the simulated duck a
                            # coordinator flock is made of
                            fallback=None,
                        )
                    )
                except _AdapterError as e:
                    # a member nothing named, on a machine that will not guess: the refusal
                    # names what to install or what to type. It is caught here because this
                    # call sits past the validation block's own handler, and an uncaught
                    # NoRobotNamed reaches the user as a traceback rather than one line.
                    _fail(str(e), hint="name every member's body in the task file's robots:")
                    return
                # A member named after a registered robot, as `--robot NAME` names one, is that
                # robot: its address, its cameras and its rest pose are kept under the name, and
                # a bare spec of its kind has none of them. It used to run as the bare spec, so a
                # registered arm lost the pose its close would have folded it to.
                registered = {r.entry.name: r.entry for r in resolved if r.entry is not None}
                roster = {
                    name: (
                        registered[name]
                        if name in registered
                        and registered[name].robot_spec.key == member.robot_spec.key
                        else member
                    )
                    for name, member in roster.items()
                }
            if roster is None:
                _fail(
                    "allocation.method: pilots needs members: name them in flock.members, "
                    "or run a stored flock with --flock NAME"
                )
                return
            if flock_n is not None:
                _fail(
                    "--flock N is the coordinator, and this task file runs pilots",
                    hint="name the members in flock.members, or run a stored flock: --flock NAME",
                )
                return
            if not 2 <= len(roster) <= PILOTS_MAX_MEMBERS:
                _fail(
                    f"a pilot flock needs 2 to {PILOTS_MAX_MEMBERS} members; "
                    f"{flock_name or 'this task file'} names {len(roster)}"
                )
                return
            _run_pilots_impl(
                duck,
                roster,
                llm=llm,
                seed=seed,
                dry_run=dry_run,
                runs_dir=runs_dir,
                yes=yes,
                live=live,
                verbose=verbose,
                goal=goal,
                base_url=base_url,
                api_key=api_key,
                vision=vision,
                extra_body=extra_body,
                max_steps=max_steps,
                fov_deg=fov_deg,
                memory=memory,
                memory_dir=memory_dir,
                flock_name=flock_name,
                log_on=log_on,
                log_prompt=log_prompt,
                run_name=run_name,
                price=price,
                play=play,
                effort=effort,
            )
            return
        if roster is not None:
            wrong = [n for n, e in roster.items() if e.robot_spec.key != "microduck:sim2d"]
            if wrong:
                _fail(
                    f"a coordinator flock is sim2d Microducks only (docs/guides/flock.md): "
                    f"{wrong[0]} is {roster[wrong[0]].robot_spec.key}",
                    hint="set flock.allocation.method: pilots to run other bodies",
                )
                return
            if not 2 <= len(roster) <= AUCTION_MAX_MEMBERS:
                # both bounds, because the members are folded in with `model_copy`, which
                # skips the field validator that would otherwise have caught one of them
                _fail(
                    f"a coordinator flock is 2 to {AUCTION_MAX_MEMBERS} ducks "
                    f"(the arena holds {AUCTION_MAX_MEMBERS}): {flock_name} has "
                    f"{_plural(len(roster), 'robot')}"
                )
                return
        _run_flock_impl(
            duck,
            llm=llm,
            specs=specs,
            members=list(roster) if roster is not None else None,
            flock_name=flock_name,
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
            extra_body=extra_body,
            n_override=flock_n,
            max_steps=max_steps,
            log_on=log_on,
            log_prompt=log_prompt,
            run_name=run_name,
            price=price,
            effort=effort,
        )
        return
    # The lens a detector built now measures through: --fov-deg, else the body's own. Never
    # the board's yet: a body described without a camera may report one of its own when it
    # connects (an arm given --camera-url, a rosbridge base), and the board's camera is then
    # only an extra view, so its lens would measure the body's camera. The loop sets the lens
    # again from the live manifest at connect, which carries the board's when the board's
    # camera turned out to be the only one (`HostCameraAdapter.connect`).
    lens_fov = fov_deg or described.limits.get("camera_fov_deg")
    try:
        # chosen once, before anything is built: a detector asked for that cannot run here is
        # a sentence now, and the run never changes detector after it starts
        asked_detector = explicit_detector(
            detector_choice,
            client=board,
            hello=hello,
            fov_deg=lens_fov,
            backend=spec.backend,
            has_camera="camera" in described.sensors,
        )
    except (ValueError, ImportError) as e:
        _fail(str(e))
        return
    try:
        pilot: LLMProvider
        if vla and effort is not None:
            # the scripted VLA pilot asks no model anything, so a reasoning level has nobody to
            # reach, and the flag is a mistake to say rather than a setting to drop quietly
            _fail(
                "--effort says how hard a model reasons, and --controller vla asks no model: "
                "the policy server drives the arm and the scripted pilot only reads it the steps"
            )
            return
        if vla:
            from quackd.agent.providers.vla import VlaProvider

            # the task file's instructions, or the goal as the one, and a task file's own words
            # for done, which the person is shown when asked whether the arm did it. A goal's
            # are written for a model, and the goal itself is the question. The robot's
            # registered pilot and `QUACKD_LLM` name a model this run never asks.
            pilot = VlaProvider(
                [goal] if goal is not None else duck.frontmatter.effective_policy.instructions,
                success=duck.frontmatter.success if goal is None else (),
                label=duck.name,
            )
        else:
            # a registered robot may name the pilot that drives it; a flag on the line still
            # wins, and `QUACKD_LLM` sits behind both. One spec carries the vendor and the
            # model together, so there is no longer any way for half an answer to come from
            # each place: `--llm gemini` on a robot registered against OpenAI is Gemini's
            # default, full stop.
            vendor, model_id, llm_source = resolve_llm(
                llm, here.llm, robot=here.entry.name if here.entry is not None else None
            )
            pilot = make_provider(
                vendor,
                model=model_id,
                source=llm_source,
                duck_name=duck.name,
                goal=goal,
                base_url=base_url,
                api_key=api_key,
                vision=vision,
                extra_body=extra_body,
                # only a host a person named for this run or this robot: QUACKD_HOST sits
                # below QUACKD_BASE_URL, and the local provider reads it there for itself
                host=host_choice.explicit,
                effort=effort,
            )
        duck_transport = make_adapter(
            spec,
            seed=seed,
            live=live,
            # the board's camera joins the body here when it has one; the board has answered
            # its hello already, so building this asks it nothing
            host=board,
            **here.adapter_kwargs(
                address=address, camera_url=camera_url, token=token, map_address=map_address
            ),
            **body_kw,
        )
    except (ProviderError, TransportError, ImportError) as e:
        _fail(str(e))
        return
    # The policy server is asked what it serves before anything connects, as the board is:
    # the header names the checkpoint, and a server that is not there is a sentence now rather
    # than a connect refused after the cameras opened. The connect asks again, and checks what
    # it hears against the arm before any torque.
    served: dict[str, Any] | None = None
    if policy is not None:
        ask = getattr(duck_transport, "ask_policy", None)
        try:
            served = ask() if callable(ask) else None
        except RuntimeError as e:
            _fail(
                str(e),
                hint=f"quackd policy check --policy-url {policy.url} asks it what it serves",
            )
            return
        if served is None:
            served = {"server": policy.url}
    if by_hand:
        if not getattr(duck_transport, "supports_hand_off", False):
            _fail(
                f"{spec.key} is not a body a person places by hand: only the LeRobot arm is",
                hint="quackd list-adapters",
            )
            return
        if here.adapter_kwargs()["rest_pose"] is None:
            # the release refuses anywhere but the recorded pose, so an arm without one could
            # never be handed over at all: better said here than after it has connected
            named = here.entry.name if here.entry is not None else None
            _fail(
                "--by-hand releases the arm at its recorded rest pose, and this arm has none "
                "recorded",
                hint=(
                    f"quackd robot rest-pose {named}"
                    if named
                    else "quackd robot add NAME " + spec.key + ", then quackd robot rest-pose NAME"
                ),
            )
            return
    # A name or a mode nobody defined is a typo and stopped the run above. One that is
    # spelled right and cannot run here is a different thing: the stepper is an optimisation,
    # the model is the pilot either way, and a script that always names a decision LLM should
    # still drive the robot on a machine that has not installed it. So it says so once,
    # loudly, and carries on without it. Said before the robot is connected, so nothing is
    # energised while it is read.
    decision_pilot = None
    decision_price = None
    # Built when the mode is not off, and when frames are on whatever the mode, because whether
    # a frame may go is a question of its own (`resolve_decision_frames`).
    if (decision != "off" or frames) and named_decision is not None:
        # `chosen` rather than `spec`, which in this function is the robot's.
        chosen, decision_model = named_decision
        # With the address, because whether a key is wanted can depend on where it would go:
        # `openai` sends one only to OpenAI's own API, so a run at a server you run needs none.
        available, why = decision_llm_is_available(chosen, url=decision_url)
        if not available:
            # and with no decision LLM, no frame goes anywhere either
            _warn(f"--decision-llm {chosen.name} asked for, running without it: {why}")
            decision = "off"
            frames = False
        else:
            try:
                decision_pilot = make_decision_llm(
                    chosen, model=decision_model, url=decision_url, frames=frames
                )
            except DecisionError as e:
                _fail(str(e))
                return
            decision_price = resolve_decision_price(chosen, url=decision_url)
            if frames:
                # One line, where the person who asked for frames is looking, naming where they
                # go, and on a run whose mode asks the stepper nothing, that it sends none, with
                # what `watch` sends where the task lets the pilot call it on this body.
                watch = (
                    "watch" in described.verb_names() and "watch" in duck.frontmatter.verbs.allow
                )
                _warn(_frames_notice(decision, decision_pilot.url, watch=watch))
    if "watch" in duck.frontmatter.effective_requires and not frames:
        # A task that needs `watch` on a run with nothing for it to look through is refused here,
        # before the robot connects, rather than by the loop once it has (`withdraw_watch`).
        from quackd.verbs.watch import NO_EYES

        _fail(f"{duck.name} requires watch, and {NO_EYES}")
        return
    if decision == "on":
        # Nobody has run a stepper against a robot, so every speed and cost figure in
        # `docs/guides/decision-llms/README.md` is arithmetic from published numbers rather than a
        # result, and only for the rows something is published about: speed and cost for jev at
        # TypeSafe, cost alone for openai at OpenAI. Two of the four confidence floors are the 0.5
        # and 0.9 TypeSafe publish and two are quackd's own, and all four were shaped around Jev
        # and are inherited unmeasured by every other row.
        # Refusing the flag over that would be the wrong shape of gate, because the executor
        # binds a stepper-authored call exactly as it binds the model's. Saying it once, where
        # the person switching it on is looking, is the right size of one.
        _warn(_DECISION_MODE_ON_UNMEASURED)
    if task_images and not pilot.supports_vision:
        # Refused rather than dropped. A pilot that cannot see would be handed "draw what is
        # in the picture" with no picture, improvise something, and the only sign of why would
        # be a note in a transcript nobody reads twice.
        _fail(
            f"{pilot.name} {pilot.model} does not take images, so it cannot be given "
            f"{_plural(len(task_images), 'picture')}",
            hint="quackd list-models marks the models that take no frames; --vision overrides "
            "it where the vendor does take them, and a local model needs --vision",
        )
        return

    recorder = None
    # Any robot with a camera needs something to look at its frames with, not just the
    # simulator. This is the static manifest, so it is only a head start: the loop asks
    # again with the live one at connect, where a robot may report a camera this does not
    # know about (a rosbridge base) or lack one this promises (a duck built without a head).
    # A detector chosen above is kept as it is, here and at connect. The body's own
    # description, not the one with the board's camera: a body described without a camera
    # gets its colour detector at connect, measured through whichever lens is then primary.
    detector = detector_for(
        described.sensors, asked_detector, fov_deg=lens_fov, backend=spec.backend
    )
    # The recorder draws a simulator's world, so it needs a body that has one. The backend
    # name alone no longer says so, because the LeRobot arm's simulator is `mujoco` too and
    # keeps its world as `sim_world`, which is not a world this can draw. `hasattr` and not
    # the value: the Microduck's physics transport builds its world in connect(), after this.
    if spec.backend in ("sim2d", "mujoco") and gif and hasattr(duck_transport, "world"):
        from quackd.sim2d.recorder import FrameRecorder

        recorder = FrameRecorder(duck_transport, size=gif_size)

    # The flag wins; else QUACKD_LOG, read here rather than at import so a `.env` line
    # counts (the root callback loads it after the option defaults exist).
    log_on = log_on if log_on is not None else log_enabled_default()
    console_log = (
        ConsoleLog(
            ui.err_console,
            thinking_chars=thinking_limit_default(),
            prompt=log_prompt if log_prompt is not None else prompt_shown_default(),
        )
        if log_on
        else None
    )

    # on whether or not the log is: with --no-log this is the only thing between the
    # header and the verdict, and a model can think for a minute
    status = ui.RunStatus()
    ui.install_logging()

    def log(msg: str) -> None:
        # the compact view: one line per verb and the executor's notes. The log shows all
        # of that and more, so with it on this prints nothing rather than every verb twice.
        if verbose and console_log is None:
            _verbose_line(msg)

    hand_off = _TerminalHandOff() if by_hand else None
    # Whoever is at this terminal, for the two questions a run can put at its very end, when
    # the rest move missed: clear the way and fold once more, and only if that misses too,
    # drop it into their hands (`quackd.release`). Not `hand_off`,
    # which the loop reads as "this run is handed over by hand", but the same object when
    # there is one, so a run reads Enter through one reader. None without a terminal to ask on
    # and on a dry run, which moves nothing and so has no rest move to miss.
    person = hand_off or (_TerminalHandOff() if _can_prompt() and not dry_run else None)
    robot_memory = None
    if memory:
        from quackd.memory import RobotMemory

        # keyed by adapter:backend, so a simulated duck never inherits a real one's notes,
        # or by the registered name, so two ducks of one kind keep separate notes
        robot_memory = RobotMemory(here.memory_key, memory_dir)
    front = _FRONT
    cfg = RunConfig(
        duck=duck,
        provider=pilot,
        transport=duck_transport,
        detector=detector,
        dry_run=dry_run,
        confirm=allow_all if yes else (front.confirm if front is not None else _confirm_prompt),
        runs_dir=runs_dir,
        run_name=run_name,
        price=price,
        max_steps=max_steps,
        log=log,
        on_frame=_frame_hook(recorder, front),
        on_caption=recorder.set_caption if recorder is not None else None,
        memory=robot_memory,
        fov_deg=fov_deg,
        acknowledge=None
        if yes
        else (front.acknowledge if front is not None else _acknowledge_prompt),
        decide=_yes_to_go if yes else (front.decide if front is not None else _decide_prompt),
        # only the vla pilot asks, and it never runs with --yes, so there is no standing answer
        judge=(front.judge if front is not None else _judge_prompt) if vla else None,
        view=fan_out(console_log, status.sink, front.view if front is not None else None),
        task_images=task_images,
        hand_off=hand_off,
        person=person,
        decision=decision,
        decision_llm=decision_pilot,
        decision_price=decision_price,
        host=hello.record(board.address) if board is not None and hello is not None else None,
        play=play,
        decision_frames=frames,
        front="jsonl" if front is not None else None,
    )
    # Nothing is left that can refuse the run, and the body is connected next, so this is
    # where a backend its adapter is retiring is said, above the header as the root callback's
    # deprecations are, and only for the one robot this run drives: a `--robots` member that a
    # task with no flock never drives, and a run refused above, say nothing.
    _deprecated_backends([spec])
    ui.console.print(
        ui.run_header(
            duck.name,
            _header_rows(
                provider=pilot,
                robot=here.label,
                seed=seed,
                dry_run=dry_run,
                memory=robot_memory,
                detector=detector,
                board=board,
                hello=hello,
                backend=spec.backend,
                host_camera=manifests[0].extras.get(HOST_CAMERA_EXTRAS),
                sees="camera" in manifests[0].sensors,
                policy=served,
                play=play_row(
                    *resolve_play(duck.frontmatter.play, play), file_play=duck.frontmatter.play
                ),
                effort=effort,
            ),
            hint="Ctrl-C or q stops the duck. Press it twice to quit at once."
            if front is None
            else "Stop in the page stops the duck.",
        )
    )

    def killed(msg: str) -> None:
        """Always printed, unlike `log`, which is --verbose only. Someone who has just hit
        Ctrl-C on a walking robot needs to see that it registered."""
        ui.err_console.print(Text(msg, style=ui.STYLES["warn"]))

    ran: list[Any] = []
    """The loop, once built, so a run that ended in an error can still be read for what it
    spent: the loop keeps its summary however its run ends."""

    async def main() -> Any:
        from quackd.agent.loop import AgentLoop

        global _SWITCH
        if front is not None and front.stopped_before_start() is not None:
            # the page's Stop came before there was a run to stop: nothing connects, as nothing
            # does when a Ctrl-C at a terminal comes that early
            return None
        loop = AgentLoop(cfg)
        ran.append(loop)
        # The first moment there is a directory to write into. Everything printed before
        # now went into the capture's buffer and is carried across by `attach`.
        ui.attach_capture(loop.run_dir)
        if front is not None:
            # the page's Stop sets this loop's abort as the kill switch's first press does,
            # and nothing reads the terminal: no switch, no key thread
            front.run_dir(loop.run_dir)
            front.bind(loop, asyncio.get_running_loop())
            try:
                return await loop.run()
            finally:
                front.unbind()
        ks = KillSwitch(loop.executor.abort, log=killed)
        if hand_off is not None:
            hand_off.bind(ks)
        if person is not None and person is not hand_off:
            person.bind(ks)
        ks.install()
        _SWITCH = ks
        try:
            return await loop.run()
        finally:
            _SWITCH = None
            ks.uninstall()

    _ = run_duck  # imported for symmetry; AgentLoop is used directly so the kill switch can bind
    try:
        with status:
            status.update(f"connecting to {here.label}")
            result = asyncio.run(main())
    except Aborted as e:
        # Before the first step: nobody said they were watching a robot that cannot see a
        # fall, or nobody answered. The loop has parked the body; this is the one-line verdict.
        if front is not None:
            front.finished("aborted", str(e), run_dir=ran[0].run_dir if ran else None)
        _fail(str(e))
        return
    except (TransportError, ProviderError) as e:
        # the log has already shown the call that failed; this is the one-line verdict
        summary = getattr(ran[0], "summary", None) if ran else None
        if front is not None:
            front.errored(str(e))
        if isinstance(summary, Mapping) and "llm_retries" in summary:
            # A run that waited to ask the model's API again, and gave up, says beside its
            # verdict what the waiting cost, as a run that ended any other way does under its
            # panel: how many tries went out again, and the seconds waited (ADR-0059).
            ui.err_console.print(ui.fail_line(str(e)))
            counters = " · ".join(run_counters(summary))
            ui.err_console.print(Text(f"  {counters}", style=ui.STYLES["muted"]))
            raise typer.Exit(code=1) from None
        _fail(str(e))
        return
    if result is None and front is not None:
        stopped = front.stopped_before_start() or "stopped"
        front.finished("aborted", stopped)
        _fail(f"stopped before the robot connected ({stopped})")
        return
    if recorder is not None:
        # after the status line rather than under it: Rich 13.7 refuses a second live region
        # on one console, and a long run can be a thousand frames to quantise
        with ui.spinner(f"encoding {len(recorder.frames)} frames into run.gif"):
            gif_path = recorder.save_gif(result.run_dir / "run.gif")
        result.gif_path = gif_path
    if front is not None:
        front.finished(
            result.outcome, result.reason, run_dir=result.run_dir, summary=result.summary
        )
    _print_outcome(
        result.outcome,
        result.reason,
        counters=run_counters(result.summary),
        run_dir=result.run_dir,
        gif_path=result.gif_path,
        log_dropped=result.log_dropped,
    )
    if result.summary.get("cost_usd") is None and result.summary.get("provider") not in (
        "fake",
        None,
    ):
        # One line, in the style of the dropped-events warning above and in the same yellow:
        # a run that could not be costed should say why and how to fix it, once, rather than
        # leaving a reader to wonder whether the number is missing or zero.
        ui.err_console.print(
            f"cost: quackd has no published rate for {result.summary.get('provider')} "
            f"{result.summary.get('model')}; pass --price in=N,out=N to compute one",
            style="yellow",
            markup=False,
        )
    if result.outcome == "infeasible":
        # its own code: 1 means the run happened and did not succeed, and a script trying one
        # body after another branches on "this body could not, try the next"
        raise typer.Exit(code=EXIT_INFEASIBLE)
    if result.outcome != "success":
        raise typer.Exit(code=1)


def _member_views(
    member_names: list[str],
    *,
    log_on: bool,
    log_prompt: bool | None,
    status: Any,
) -> tuple[dict[str, Any], Any]:
    """One console view per member, coloured and prefixed by name, plus the flock's own.

    Shared by both kinds of flock, because a person reading either one needs the same thing:
    several robots narrating at once stay several readable columns rather than one
    interleaving. Returns the views (so the caller can flush them) and the `view(name)`
    factory the runner takes."""
    from quackd.flock.runner import FLOCK_LOG
    from quackd.log import (
        ConsoleLog,
        Sink,
        fan_out,
        prompt_shown_default,
        thinking_limit_default,
    )

    views: dict[str, ConsoleLog] = {}
    width = max(len(name) for name in [*member_names, FLOCK_LOG])

    def view_for(name: str) -> Sink | None:
        if name not in views:
            # a colour per member as well as a name, because robots moving at once interleave
            # and the eye finds a colour faster than it reads a prefix
            order = member_names.index(name) if name in member_names else -1
            views[name] = ConsoleLog(
                ui.err_console,
                thinking_chars=thinking_limit_default(),
                prompt=log_prompt if log_prompt is not None else prompt_shown_default(),
                prefix=f"{name:<{width}}  ",
                prefix_style=ui.MEMBER_STYLES[order % len(ui.MEMBER_STYLES)]
                if order >= 0
                else ui.STYLES["key"],
            )
        return fan_out(views[name], status.sink)

    def status_only(_name: str) -> Sink | None:
        """With --no-log nothing narrates, but the status line still has to say which robot
        is doing what, or a flock is a minute of nothing at all."""
        return status.sink

    return views, (view_for if log_on else status_only)


def _run_pilots_impl(
    duck: Any,
    roster: Any,
    *,
    llm: str | None,
    seed: int | None,
    dry_run: bool,
    runs_dir: str,
    yes: bool,
    live: bool,
    verbose: bool,
    goal: str | None,
    base_url: str | None,
    api_key: str | None,
    vision: bool | None,
    extra_body: str | None,
    max_steps: int | None,
    fov_deg: float | None,
    memory: bool,
    memory_dir: str | None,
    flock_name: str | None,
    log_on: bool | None = None,
    log_prompt: bool | None = None,
    run_name: str | None = None,
    price: str | None = None,
    play: bool | None = None,
    effort: str | None = None,
) -> None:
    """A pilot per body, all at once. The other flock is `_run_flock_impl`."""
    from quackd.agent.loop import resolve_play
    from quackd.agent.providers.base import ProviderError
    from quackd.agent.providers.factory import make_provider, resolve_llm
    from quackd.agent.providers.pricing import fmt_usd
    from quackd.flock.pilots import run_pilot_flock
    from quackd.log import fmt_duration, log_enabled_default, play_row
    from quackd.memory import RobotMemory
    from quackd.safety import KillSwitch
    from quackd.transport.base import TransportError

    members = list(roster)
    if duck.frontmatter.verbs.confirm and not yes:
        _fail("a pilot flock cannot prompt y/N per member: empty verbs.confirm or pass --yes")
        return
    try:
        providers = {}
        for name, entry in roster.items():
            vendor, model_id, llm_source = resolve_llm(llm, entry.llm, robot=name)
            providers[name] = make_provider(
                vendor,
                model=model_id,
                source=llm_source,
                duck_name=duck.name,
                goal=goal,
                base_url=base_url,
                api_key=api_key,
                vision=vision,
                extra_body=extra_body,
                effort=effort,
            )
    except (ProviderError, ImportError) as e:
        _fail(str(e))
        return
    memories = (
        {name: RobotMemory(entry.memory_key, memory_dir) for name, entry in roster.items()}
        if memory
        else None
    )

    log_on = log_on if log_on is not None else log_enabled_default()

    def log(msg: str) -> None:
        # the log says all of this and more, so two views of one line is noise
        if verbose and not log_on:
            _verbose_line(msg)

    status = ui.RunStatus()
    ui.install_logging()
    views, view_factory = _member_views(
        members, log_on=log_on, log_prompt=log_prompt, status=status
    )
    ui.console.print(
        ui.run_header(
            duck.name,
            _pilot_header_rows(
                roster,
                providers,
                memories,
                flock_name=flock_name,
                dry_run=dry_run,
                play=play_row(
                    *resolve_play(duck.frontmatter.play, play), file_play=duck.frontmatter.play
                ),
            ),
            hint="Ctrl-C or q stops every robot. Press it twice to quit at once.",
        )
    )

    def killed(msg: str) -> None:
        ui.err_console.print(Text(msg, style=ui.STYLES["warn"]))

    async def main() -> Any:
        master = asyncio.Event()
        ks = KillSwitch(master, log=killed)
        ks.install()
        try:
            return await run_pilot_flock(
                duck,
                roster,
                providers=providers,
                seed=seed,
                runs_dir=runs_dir,
                dry_run=dry_run,
                max_steps=max_steps,
                live=live,
                yes=yes,
                memories=memories,
                fov_deg=fov_deg,
                log=log,
                view=view_factory,
                abort=master,
                flock_name=flock_name,
                run_name=run_name,
                price=price,
                on_run_dir=ui.attach_capture,
                # once every member is built, just before they connect: a backend its adapter
                # is retiring is said for the members that are on it, wherever they come in
                # the roster, and for none when the flock is refused first
                before_connect=_deprecated_backends,
                play=play,
            )
        finally:
            ks.uninstall()

    try:
        with status:
            status.update(f"connecting {_plural(len(members), 'robot')}")
            result = asyncio.run(main())
    except (ValueError, TransportError, ProviderError, ImportError) as e:
        _fail(str(e))
        return
    finally:
        # a member's last event is the stop it was accepted for, and a pending burst is only
        # written by the next event that is not an intent: without this, never
        for pending in views.values():
            pending.flush()
    ok = sum(1 for row in result.per_member.values() if row["outcome"] == "success")
    _print_outcome(
        result.outcome,
        result.reason,
        counters=[
            f"members {ok}/{len(members)} succeeded",
            f"talk {result.messages}",
            f"steps {result.steps}",
            f"llm calls {result.llm_calls}",
            f"tokens {result.usage.input_tokens}+{result.usage.output_tokens}",
            f"time {fmt_duration(result.wall_elapsed_s)}",
            f"cost {fmt_usd(result.cost_usd)}",
        ],
        run_dir=result.run_dir,
        log_dropped=result.log_dropped,
    )
    if result.outcome == "infeasible":
        raise typer.Exit(code=EXIT_INFEASIBLE)
    if result.outcome != "success":
        raise typer.Exit(code=1)


def _pilot_header_rows(
    roster: Any,
    providers: dict[str, Any],
    memories: Any,
    *,
    flock_name: str | None,
    dry_run: bool,
    play: str | None = None,
) -> list[tuple[str, Any]]:
    """What is about to happen: which bodies, which pilots, and what is different about it.
    `play` is the row `quackd.log.play_row` words, as on a solo run's header."""
    pilots = {(p.name, p.model) for p in providers.values()}
    rows: list[tuple[str, Any]] = []
    if len(pilots) == 1:
        name, model = pilots.pop()
        rows.append(("provider", f"{name} ({model or 'the first model it serves'})"))
    else:
        rows.append(
            (
                "pilots",
                Text(
                    NEWLINE.join(
                        f"{n:<{max(len(m) for m in roster)}}  {providers[n].name} "
                        f"({providers[n].model or 'the first model it serves'})"
                        for n in roster
                    )
                ),
            )
        )
    width = max(len(n) for n in roster)
    rows.append(
        (
            "flock",
            Text(
                NEWLINE.join(f"{n:<{width}}  {entry.robot_spec.key}" for n, entry in roster.items())
            ),
        )
    )
    if flock_name:
        rows.append(("stored as", Text(flock_name, style=ui.STYLES["accent"])))
    if play is not None:
        rows.append(("play", play))
    rows.append(("status", Text("EXPERIMENTAL", style=ui.STYLES["warn"])))
    if dry_run:
        rows.append(("mode", Text("DRY RUN: nothing is sent", style=ui.STYLES["warn"])))
    if memories:
        total = sum(len(m.notes()) for m in memories.values())
        rows.append(
            (
                "memory",
                Text(
                    f"{_plural(total, 'note')} across {_plural(len(memories), 'robot')}"
                    "  --no-memory to run fresh",
                    style=ui.STYLES["muted"],
                ),
            )
        )
    return rows


def _run_flock_impl(
    duck: Any,
    *,
    llm: str | None,
    specs: list[Any],
    members: list[str] | None = None,
    flock_name: str | None = None,
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
    extra_body: str | None,
    n_override: int | None,
    max_steps: int | None,
    log_on: bool | None = None,
    log_prompt: bool | None = None,
    run_name: str | None = None,
    price: str | None = None,
    effort: str | None = None,
) -> None:
    from quackd.agent.providers.base import ProviderError
    from quackd.agent.providers.factory import make_provider, resolve_llm
    from quackd.flock.runner import FLOCK_LOG, run_flock
    from quackd.log import (
        ConsoleLog,
        Sink,
        fan_out,
        flock_caption,
        log_enabled_default,
        prompt_shown_default,
        thinking_limit_default,
    )
    from quackd.safety import KillSwitch
    from quackd.sim2d.recorder import FrameRecorder

    if any(spec.backend != "sim2d" for spec in specs):
        _fail(
            "flock mode is simulator only (docs/guides/flock.md); "
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
    if members is not None:
        # a stored flock supplies the members the way `--flock N` supplies the count. The
        # size and the bodies were checked by the caller, so this skips the field validator
        # rather than re-deriving a task file that never named them.
        from quackd.duckfile.schema import FlockSection

        section = (duck.frontmatter.flock or FlockSection()).model_copy(update={"members": members})
        duck = duck.model_copy(
            update={"frontmatter": duck.frontmatter.model_copy(update={"flock": section})}
        )
    robots = {spec.name: spec.key for spec in specs if spec.name} or None
    try:
        vendor, model_id, llm_source = resolve_llm(llm)
        pilot = make_provider(
            vendor,
            model=model_id,
            source=llm_source,
            duck_name=duck.name,
            goal=goal,
            base_url=base_url,
            api_key=api_key,
            vision=vision,
            extra_body=extra_body,
            effort=effort,
        )
    except (ProviderError, ImportError) as e:
        _fail(str(e))
        return

    if n_override is not None:
        count = n_override
    elif duck.frontmatter.flock is not None:
        count = len(duck.frontmatter.flock.member_names)
    else:
        count = 3
    member_names = (
        duck.frontmatter.flock.member_names[:count]
        if duck.frontmatter.flock is not None
        else [f"duck-{i}" for i in range(count)]
    )
    prefix_width = max(len(name) for name in [*member_names, FLOCK_LOG])
    log_on = log_on if log_on is not None else log_enabled_default()

    def log(msg: str) -> None:
        # the log says all of this and more, so two views of one line is noise
        if verbose and not log_on:
            _verbose_line(msg)

    views: dict[str, ConsoleLog] = {}

    def view_for(name: str) -> Sink | None:
        """One view per robot, its name on every line. A shared view would coalesce two
        robots' intents into one line and attribute them to whichever spoke last."""
        if name not in views:
            # a colour per member as well as a name, because three robots moving at once
            # interleave and the eye finds a colour faster than it reads a prefix
            order = member_names.index(name) if name in member_names else -1
            views[name] = ConsoleLog(
                ui.err_console,
                thinking_chars=thinking_limit_default(),
                prompt=log_prompt if log_prompt is not None else prompt_shown_default(),
                prefix=f"{name:<{prefix_width}}  ",
                prefix_style=ui.MEMBER_STYLES[order % len(ui.MEMBER_STYLES)]
                if order >= 0
                else ui.STYLES["key"],
            )
        return fan_out(views[name], status.sink)

    def status_only(_name: str) -> Sink | None:
        """With --no-log nothing narrates, but the status line still has to say which duck
        is doing what, or a flock is a minute of nothing at all."""
        return status.sink

    status = ui.RunStatus()
    ui.install_logging()
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
                    rec.set_focus(entity[1])
                else:
                    rec.set_focus(names.index(data["kicker"]))
            # the same function the terminal renders with, so a frame in the GIF and a
            # line on screen say the same thing about the same moment
            if kind in ("auction", "claim", "miss", "kick_done", "verdict"):
                caption = flock_caption(kind, data)
                if caption is not None:
                    rec.set_caption(f"{caption[0]} {caption[1]}")

        coordinator.on_event = on_event

    rows: list[tuple[str, Any]] = [
        ("provider", f"{pilot.name} ({pilot.model or 'the first model it serves'})"),
        (
            "flock",
            (f"{flock_name}: " if flock_name else "")
            + f"{count} ducks in sim2d"
            + (f"  seed {seed}" if seed is not None else ""),
        ),
        ("status", Text("EXPERIMENTAL", style=ui.STYLES["warn"])),
    ]
    if dry_run:
        rows.append(("mode", Text("DRY RUN: nothing is sent", style=ui.STYLES["warn"])))
    ui.console.print(ui.run_header(duck.name, rows, hint="Ctrl-C or q stops every duck."))
    try:
        with status:
            status.update(f"starting {count} ducks")
            result = asyncio.run(
                run_flock(
                    duck,
                    provider=pilot,
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
                    view=view_for if log_on else status_only,
                    run_name=run_name,
                    price=price,
                    on_run_dir=ui.attach_capture,
                )
            )
    except ValueError as e:
        _fail(str(e))
        return
    finally:
        if "ks" in holder:
            holder["ks"].uninstall()
        # a member's last event is the stop it was accepted for, and a pending burst is
        # only written by the next event that is not an intent: without this, never
        for pending in views.values():
            pending.flush()
    if "rec" in holder:
        rec = holder["rec"]
        with ui.spinner(f"encoding {len(rec.frames)} frames into run.gif"):
            result.gif_path = rec.save_gif(result.run_dir / "run.gif")
    counters = [f"kicker {result.kicker}"]
    if result.spotter:
        counters.insert(0, f"spotter {result.spotter}")
    counters += [
        f"auctions {result.auctions}",
        f"bids {result.bids}",
        f"ball moved {result.ball_displacement_m:.2f} m in {result.sim_elapsed_s:.1f} s sim",
    ]
    _print_outcome(
        result.outcome,
        result.reason,
        counters=counters,
        run_dir=result.run_dir,
        gif_path=result.gif_path,
        log_dropped=result.log_dropped,
    )
    if result.outcome == "infeasible":
        # its own code: 1 means the run happened and did not succeed, and a script trying one
        # body after another branches on "this body could not, try the next"
        raise typer.Exit(code=EXIT_INFEASIBLE)
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
    rich_help_panel="Task",
)
_IMAGE: list[str] = typer.Option(
    [],
    "--image",
    help='A picture to hand to the task, e.g. --goal "draw what is in the picture" --image '
    "sketch.png. The pilot gets it on its first turn, labelled with the file's name, and keeps "
    "it for the whole run, which is what makes it different from a camera frame. Repeatable. "
    "Needs a pilot that takes images: `quackd list-models` marks the ones that do not, and a "
    "local model needs --vision.",
    rich_help_panel="Task",
)
_GIFSIZE = typer.Option(
    256,
    "--gif-size",
    min=64,
    max=1024,  # `sim3d.scene.OFFSCREEN_PX`; spelled here because cli.py must not import sim3d
    help="Simulators: pixel size of each GIF pane, 64 to 1024.",
    rich_help_panel="Output",
)
_FLOCK = typer.Option(
    None,
    "--flock",
    help="EXPERIMENTAL: a number N runs N cooperating ducks (2-4) in sim2d under the "
    "deterministic coordinator; a name from `quackd flock list` runs that flock's registered "
    "robots, one LLM pilot each. Either overrides the file's flock members.",
    rich_help_panel="Robot",
)
_MEMORY = typer.Option(
    True,
    "--memory/--no-memory",
    help="Carry notes and run outcomes between runs of the same robot (see `quackd memory`).",
    rich_help_panel="Memory",
)
_DRIVE_MEMORY = typer.Option(
    False,
    "--memory/--no-memory",
    help="Let each goal the pilot flies recall what earlier runs of this robot learned, and keep "
    "one fact for the next, as quackd run does (see `quackd memory`). Off in drive, where run "
    "keeps it on. The places a person marks are kept either way, and those a goal marks or "
    "forgets only with it.",
    rich_help_panel="Memory",
)
_MEMORY_DIR = typer.Option(
    None,
    "--memory-dir",
    help="Where memory files live (default: $QUACKD_MEMORY_DIR or ~/.quackd/memory).",
    rich_help_panel="Memory",
)


_LLM = typer.Option(
    None,
    "--llm",
    "-l",
    help="Who pilots the robot, as VENDOR[:MODEL]. `anthropic` runs that vendor's default and "
    "`openai:gpt-6-sol` names one; a model id unique to its vendor is enough on its own, so "
    "`claude-opus-5-5` works. `ollama:qwen3:8b` is a local server (the split is at the first "
    "colon, so a tag keeps its own), `local` needs --base-url, and `fake` is a scripted pilot "
    "with no key and no network. Vendors: " + " · ".join(PROVIDER_NAMES) + ". `quackd "
    f"list-models` prints every id quackd carries. Default: the robot's own, then {LLM_ENV}, then "
    f"{DEFAULT_LLM}.",
    autocompletion=_complete_llm,
    rich_help_panel="Model",
)
_BASEURL = typer.Option(
    None,
    "--base-url",
    help="OpenAI-compatible server, e.g. http://localhost:8000/v1 (local presets).",
    rich_help_panel="Model",
)
# Declared once, like every option two commands share, so `run` and `serve-mcp` cannot come to
# spell the board differently. No `envvar=`: Typer would fold QUACKD_HOST_TOKEN
# into the flag, above a token stored with the robot, and quackd's order everywhere is the
# flag, then the robot, then the environment. `quackd.host.resolve_host` reads both variables.
_HOST = typer.Option(
    None,
    "--host",
    metavar="HOST[:PORT]",
    help="A machine quackd uses and never runs on: its model server, its camera, its detector "
    "and its health. --robot still names the body. The port is the one quackd's daemon on the "
    "board listens on, 9874 unless you changed it, and a local preset keeps its own port on "
    "that machine. Without the flag, a run uses the robot's registered host, then QUACKD_HOST.",
    rich_help_panel="Host",
)
_HOST_TOKEN = typer.Option(
    None,
    "--host-token",
    help="The token the --host daemon was started with. It travels in a header, never in a "
    "URL, and never reaches the run record. Without the flag, a run uses the robot's "
    "registered one, then QUACKD_HOST_TOKEN.",
    rich_help_panel="Host",
)
_DETECTOR = typer.Option(
    None,
    "--detector",
    metavar="color|host|yolo",
    help="What reads the camera's frames. color is the colour detector on this machine; host "
    r"is YOLO on the board --host names; yolo is YOLO on this machine and needs quackd\[yolo]. "
    "Default: the host's detector on a real body when --host names a daemon that can detect, "
    "else the colour detector on this machine. The run never changes detector once it starts.",
    rich_help_panel="Host",
)
# The same two flags for `quackd robot add` and `edit`, with help of their own for the reason
# `--llm` has its own there: those commands have no --robot, they are what does the
# registering, and the token they take goes into robots.json rather than into a run.
_ROBOT_HOST = typer.Option(
    None,
    "--host",
    metavar="HOST[:PORT]",
    help="The board this robot uses and quackd never runs on: its runs reach their model "
    "server, camera and detector there. The port is quackd's daemon's on the board, 9874 "
    "unless you changed it. --host on a run beats this, and this beats QUACKD_HOST.",
    rich_help_panel="Host",
)
_ROBOT_HOST_TOKEN = typer.Option(
    None,
    "--host-token",
    help="The token that board's daemon was started with. Kept in robots.json, as --token "
    "is. A run of this robot sends it in a header to that board, or to the --host the run "
    "names instead, and --host-token on the run beats it.",
    rich_help_panel="Host",
)
_APIKEY = typer.Option(
    None,
    "--api-key",
    help="API key override (local servers do not need one).",
    rich_help_panel="Model",
)
_EXTRA_BODY = typer.Option(
    None,
    "--extra-body",
    help="A JSON object merged into every request body on the OpenAI-compatible providers, for "
    "a field the server wants and quackd never sends. Qwen3 on vLLM stops thinking with "
    '\'{"chat_template_kwargs": {"enable_thinking": false}}\'. QUACKD_EXTRA_BODY does the '
    "same when the flag is absent, and spares you the shell quoting.",
    rich_help_panel="Model",
)
_EFFORT = typer.Option(
    None,
    "--effort",
    metavar="low|medium|high",
    help="How hard the pilot's model reasons before each answer: Anthropic's effort and "
    "OpenAI's reasoning effort, and a model server of your own's. Lower answers sooner. "
    "Beats QUACKD_EFFORT and QUACKD_OPENAI_REASONING_EFFORT, and is refused for a vendor "
    "or a model that takes no such setting. Default: medium on Claude, the model's own on "
    "OpenAI.",
    rich_help_panel="Model",
)
_VISION = typer.Option(
    None,
    "--vision/--no-vision",
    help="Send camera frames to the model (default: on for cloud, off for local).",
    rich_help_panel="Model",
)


def _complete_decision_llm(ctx: typer.Context, incomplete: str) -> list[tuple[str, str]]:
    """`--decision-llm` in the shell. The same shape as `--llm`: a name, then its own model.

    The names come from the table plus whatever is installed here, so a plugin a reader
    installed this morning completes without quackd having been rebuilt for it."""
    from quackd.agent.decision.catalogue import PRESETS
    from quackd.agent.decision.factory import preset_names

    head, colon, _prefix = incomplete.partition(":")
    name = head.strip().lower()
    if colon:
        spec = PRESETS.get(name)
        return [(f"{name}:{spec.model}", spec.summary)] if spec and spec.model else []
    return [
        (n, PRESETS[n].summary if n in PRESETS else "installed here")
        for n in preset_names()
        if n.startswith(name)
    ]


_DECISION_LLM = typer.Option(
    None,
    "--decision-llm",
    help="EXPERIMENTAL: put a discrete stepper in front of the model, as NAME[:MODEL]. A "
    "decision LLM generates no text at all: it answers the turns whose answer is a choice "
    "among calls this body can make, a read, the brake, a gripper, a gaze, and hands "
    "everything else to the model, including every pose and every sentence. `jev` is "
    "TypeSafe's and `openai` is OpenAI's Decisions API, both hosted; `kev`, `von`, `openjev` "
    "and `opendecision` are open ones you run yourself; `laya` runs inside this process; "
    "`local` is any other System One server, with --decision-url. Needs "
    r"quackd\[decision], or quackd\[laya] for laya and quackd\[openai] for openai,"
    " and whatever key the one you name asks for: TYPESAFE_API_KEY for jev, OPENAI_API_KEY "
    "for openai, and none for one you run yourself. Off unless you name one. "
    "QUACKD_DECISION_LLM does the same.",
    autocompletion=_complete_decision_llm,
    rich_help_panel="Model",
)
_DRIVE_DECISION_LLM = typer.Option(
    None,
    "--decision-llm",
    help="The fast lane: a line no verb names is put to this decision LLM, as NAME[:MODEL], as "
    "one closed question, which of the body's calls or presets it means, or whether it is a "
    "goal for the pilot. A call it is sure of runs, and one it is less sure of is asked about "
    "first. The same names, extras and keys as `quackd run --decision-llm`. Off unless you name "
    "one. QUACKD_DECISION_LLM does the same.",
    autocompletion=_complete_decision_llm,
    rich_help_panel="Model",
)
_DRIVE_DECISION_MODE = typer.Option(
    None,
    "--decision-mode",
    help="What the decision LLM may do with each goal's turns, as quackd run --decision-mode "
    "says: on lets it take the ones it is confident about, shadow asks it every turn and "
    "records what it would have chosen without changing anything, and off leaves the goals to "
    "the pilot. Without it every goal is flown by the pilot alone, and --decision-llm is the "
    "fast lane only. QUACKD_DECISION_MODE is not read here.",
    rich_help_panel="Model",
)
_DECISION_URL = typer.Option(
    None,
    "--decision-url",
    help="Where the decision LLM listens, e.g. http://localhost:8009. On a System One row a "
    "path is kept and the client adds /v1/systemone after it; `openai`'s client adds "
    "/v1/decisions to the host alone, and a path there stops the run. Required by "
    "--decision-llm local, and the way to move any other one off the address its row expects. "
    "`openai` sends OPENAI_API_KEY to OpenAI's own API alone, https://api.openai.com or a "
    "regional host OpenAI name for it (us., eu., au., ca., jp., in., sg., kr., gb. or ae. in "
    "front of api.openai.com), and the word local anywhere else. QUACKD_DECISION_URL does the "
    "same for the rows that want no key.",
    rich_help_panel="Model",
)
_DECISION_MODE = typer.Option(
    None,
    "--decision-mode",
    help="What the decision LLM's answer is allowed to do. `on`, the default once you have "
    "named one, lets it take the turns it is confident enough about. `shadow` asks it every "
    "turn, records what it would have chosen beside what the model did, and changes nothing "
    "about the run: it is how you find out whether to trust one before you do. `off` is quackd "
    "as it has always been. QUACKD_DECISION_MODE does the same.",
    rich_help_panel="Model",
)
_DECISION_FRAMES = typer.Option(
    None,
    "--decision-frames/--no-decision-frames",
    help="EXPERIMENTAL: show the decision LLM the robot's primary camera with each turn the "
    f"stepper asks about, as one JPEG at most {DECISION_FRAME_PX} pixels on its longest side, "
    "beside the state and never inside it, and keep each one sent as frames/NNNN-decision.jpg "
    "in the run directory. It also gives a Microduck the watch verb, which shows it one such "
    "frame a look, whatever --decision-mode says, kept as sent/watch-NNNN.jpg. "
    "Only `openai` may be shown one, and only at OpenAI's own address: "
    "with no decision LLM named, any other one, or a --decision-url that is not OpenAI's, the "
    "run stops before the robot connects. --vision is about the pilot and changes nothing here. "
    "Off unless you say so. QUACKD_DECISION_FRAMES=1 does the same.",
    rich_help_panel="Model",
)


_DRIVE_DECISION_FRAMES = typer.Option(
    None,
    "--decision-frames/--no-decision-frames",
    help="EXPERIMENTAL: show the decision LLM the robot's primary camera with each turn a "
    "goal's stepper asks about, and offer a Microduck's goals the watch verb, as quackd run "
    "--decision-frames does, each frame kept in that goal's own directory. Only `openai` may be "
    "shown one, and only at OpenAI's own address, or the session stops before the robot "
    "connects. The fast lane is never shown one. QUACKD_DECISION_FRAMES is not read here.",
    rich_help_panel="Model",
)


def _frames_asked(flag: bool | None, decision_llm: str | None) -> bool:
    """Whether a run asks for frames, read as `factory.resolve_decision_frames` reads it and
    never refusing: for a `--goal` run's allowlist, which is built before the flags are judged,
    and offers `watch` only to a run that asked. A run that asked and is then refused stops
    before anything connects, and one whose decision LLM turns out not to be here runs without
    `watch`, which the loop says when it withdraws it."""
    from quackd.agent.decision.factory import ENV_FRAMES, FRAMES_ON

    if (decision_llm or "").strip().lower() == "off":
        return False
    if flag is not None:
        return flag
    return (os.environ.get(ENV_FRAMES) or "").strip().lower() in FRAMES_ON


CONTROLLERS = ("llm", "vla")
"""What `--controller` takes: the model `--llm` names, or the scripted pilot of
`quackd.agent.providers.vla`."""
_CONTROLLER = typer.Option(
    None,
    "--controller",
    metavar="llm|vla",
    help="Who flies the run. llm, the default, is the model --llm names, and with --policy-url "
    "it hands the arm's learned policy one short subtask at a time. vla is a scripted pilot for "
    "a LeRobot arm with --policy-url: it tells the policy each instruction the task file lists, "
    "or the --goal as the only one, one segment each, then asks you whether the arm did it, and "
    "only your yes makes the run a success. It needs a terminal to ask on, and takes no --yes, "
    "--dry-run, --decision-llm or model flag.",
    rich_help_panel="Model",
)
_DRIVE_CONTROLLER = typer.Option(
    None,
    "--controller",
    metavar="llm|vla",
    help="Who flies each goal. llm, the default, is the model --llm names, and with --policy-url "
    "it hands the arm's learned policy one short subtask at a time. vla is a scripted pilot for "
    "a LeRobot arm with --policy-url: it tells the policy each goal word for word, as its one "
    "instruction, for one segment, then asks you whether the arm did it, and only your yes makes "
    "the goal a success. It needs the keyboard or the page to ask on, and takes no --yes, "
    "--dry-run, --decision-mode, --decision-frames or model flag. --decision-llm stays the "
    "fast lane.",
    rich_help_panel="Model",
)
_ROBOT = typer.Option(
    None,
    "--robot",
    "-r",
    help="<adapter>:<backend>, e.g. microduck:sim2d · lerobot:real · microduck:mock, or a "
    "name from `quackd robot add`, which brings its own address, token and camera. The core "
    "installs no robot, so the default is whatever is here: the only adapter installed, or "
    "microduck:sim2d where the duck is one of several. With several and no duck, name a "
    "body. See `quackd list-adapters` and `quackd robot list`.",
    rich_help_panel="Robot",
)
_ROBOTS = typer.Option(
    None,
    "--robots",
    help="A flock: name=<adapter>:<backend>,... A coordinator flock needs every "
    "member to be microduck:sim2d, and a pilot flock or serve-mcp takes any of them.",
    rich_help_panel="Robot",
)
_SEED = typer.Option(
    None, "--seed", help="Simulator seed (deterministic runs).", rich_help_panel="Task"
)
_DRY = typer.Option(
    False, "--dry-run", help="Print every intent, send nothing.", rich_help_panel="Task"
)
_MAXSTEPS = typer.Option(
    None, "--max-steps", help="Override the duck's max_steps budget.", rich_help_panel="Task"
)
_DRIVE_MAXSTEPS = typer.Option(
    None,
    "--max-steps",
    help="Each goal's max_steps budget, within what the session has left of its own.",
    rich_help_panel="Task",
)
# No variable, as `--policy-url` has none: a `.env` line or a robots.json field would make every
# run of one robot play, and the default prompt is the one this flag must never change.
_PLAY = typer.Option(
    None,
    "--play/--no-play",
    help="Let the pilot play a part the task gives it, an animal, a person, a character or a "
    "feeling, by acting it out with the body it has. It adds one section to the system prompt "
    "and changes no gate. --no-play turns a task file's play: true off for this run. Without "
    "either the task file decides, and a --goal run plays only with --play. No variable sets "
    "it, so a run plays only when its file or its command line says so.",
    rich_help_panel="Task",
)
_DRIVE_PLAY = typer.Option(
    None,
    "--play/--no-play",
    help="Let the pilot play a part a goal gives it, an animal, a person, a character or a "
    "feeling, by acting it out with the body it has, as quackd run --goal --play does. It adds "
    "one section to each goal's system prompt and changes no gate. A goal plays only with "
    "--play, and no variable sets it.",
    rich_help_panel="Task",
)
# preflight has no --goal, and rehearses many runs rather than one
_PLAY_PREFLIGHT = typer.Option(
    None,
    "--play/--no-play",
    help="--play rehearses every file with a part for its pilot to play, and --no-play without "
    "the one a file gives it, as a run would. Without either each file decides. It adds one "
    "section to the system prompt and changes no gate. No variable sets it.",
    rich_help_panel="Task",
)
_RUNS = typer.Option(
    "runs", "--runs-dir", help="Where run directories go.", rich_help_panel="Output"
)
_RUN_NAME = typer.Option(
    None,
    "--run-name",
    help="Name this run on disk: runs/<stamp>-<duck>-<name>/. Lowercased to a slug, so "
    '"Example 1" becomes example-1. Omitted, the directory is named as it always was.',
    rich_help_panel="Output",
)
_PRICE = typer.Option(
    None,
    "--price",
    help="What the model costs, in USD per million tokens: in=3,out=15[,cache_read=0.3,"
    "cache_write=3.75]. Beats QUACKD_PRICE and the built-in rates. Use it for a negotiated "
    "rate, a model quackd has no price for, or a paid server behind a local preset.",
    rich_help_panel="Model",
)
_YES = typer.Option(
    False,
    "--yes",
    "-y",
    help="Auto-confirm gated verbs (careful on hardware).",
    rich_help_panel="Task",
)
_LIVE = typer.Option(
    False,
    "--live",
    help="Simulators: watch the run in real time. sim2d opens a pygame window (needs "
    r"quackd\[live]); mujoco opens MuJoCo's own viewer.",
    rich_help_panel="Output",
)
_ADDR = typer.Option(
    None,
    "--address",
    help="Where the body is, in its own protocol's shape: a LeRobot arm's serial port "
    "(COM5 on Windows, /dev/ttyACM0 elsewhere), a rosbridge websocket "
    "(ws://host:9090), a ZeroMQ or bridge host (tcp://host:5555), or robotd's socket "
    "(unix:///run/robotd.sock, tcp://host:port).",
    rich_help_panel="Robot",
)
_MAP_ADDR = typer.Option(
    None,
    "--map-address",
    help="Where the Microduck's own map, mapd, answers beside its robotd: "
    "unix:///run/mapd/map.sock on the robot, or tcp://host:port through a tunnel. Without it, "
    "mapd's own socket is tried when robotd is on one. microduck:jsonrpc, one robot. "
    "UNVERIFIED: mapd was read from a branch, in no release.",
    rich_help_panel="Robot",
)
_TOKEN = typer.Option(
    None,
    "--token",
    help="The bridge token for a robot that wants one. The Open Duck's installer writes one "
    "on the robot and QUACKD_DUCK_TOKEN carries it when the flag is absent. The ToddlerBot's "
    "daemon has no installer and reads QUACKD_TODDLERBOT_TOKEN instead, and MOSS's bridge reads "
    "QUACKD_MOSS_TOKEN.",
    rich_help_panel="Robot",
)
_CAMERA_URL: list[str] = typer.Option(
    [],
    "--camera-url",
    help="Where frames come from, overriding whatever the robot advertises. An HTTP snapshot "
    "(http://host:9872/snapshot.jpg, or http://host:8080/frame, the PNG a Microduck's mediad "
    "serves), or webrtc://host:8443 to pull mediad's video track off a Microduck, which needs "
    r"quackd\[microduck-camera]. "
    "Needed when you reach the robot through a tunnel and its own URL is not routable. On a "
    "LeRobot arm it is a USB webcam by its OpenCV index, opencv://0, with ?width, ?height, "
    "?fps, ?fourcc, ?rotation, ?fov, ?name and ?backend=msmf for a Windows camera that lists "
    "and will not open. Find the index with lerobot-find-cameras opencv. Repeat the flag for "
    "several cameras: every frame reaches the model each step, and the first is the primary, "
    "the one --fov-deg describes and the one the detections and the steering verbs read. "
    "Only the LeRobot arm reads more than one.",
    rich_help_panel="Robot",
)
_BY_HAND = typer.Option(
    False,
    "--by-hand",
    help="Start from a pose you set yourself instead of the recorded rest pose. The arm goes "
    "to its rest pose, quackd takes torque off there, you lift it, load the gripper and press "
    "Enter, and it holds what you left while the model works. At the end it asks before the "
    "gripper opens. Needs a LeRobot arm with a rest pose recorded, and a terminal to ask on.",
    rich_help_panel="Robot",
)
_POLICY_URL = typer.Option(
    None,
    "--policy-url",
    help="The policy server the arm hands pick and manipulate to, as http://127.0.0.1:PORT, or "
    "https:// for one behind TLS: the address quackd policy serve printed, or hf:NAME for one "
    "quackd policy endpoint up started on a Hugging Face GPU. A LeRobot arm only, "
    "lerobot:real or lerobot:mujoco, and one robot only. It is asked what it serves before the "
    "arm connects, and the connect checks that the policy fits the arm before any torque. No "
    "variable sets it, so a policy drives the arm only when a command names one.",
    rich_help_panel="Robot",
)
_POLICY_TOKEN = typer.Option(
    None,
    "--policy-token",
    help="The token the --policy-url server wants. Without it, QUACKD_POLICY_TOKEN, then the "
    "one quackd policy serve wrote to ~/.quackd/policy.token.",
    rich_help_panel="Robot",
)
_ACCEPT_OTHER_FRAME = typer.Option(
    False,
    "--accept-other-frame",
    help="Let the --policy-url policy drive this arm although it learned from an arm calibrated "
    "another way, whose readings lie outside this arm's calibrated travel. It only lets the "
    "policy connect: every goal it answers is still clipped to this arm's travel, so it "
    "changes what drives the arm and never where the arm may go. Give it only if you know the "
    "two arms' frames match, and the run's record says it was given.",
    rich_help_panel="Robot",
)
_ASYNC_CHUNKS = typer.Option(
    True,
    "--async-chunks/--no-async-chunks",
    help="Ask the --policy-url policy for its next chunk while the arm still plays the "
    "last, the default. With --no-async-chunks the arm plays each chunk out, holds still while "
    "the next is on its way and plays that from its first action, as LeRobot's own loop plays "
    "chunks: slower by a round trip a chunk, and the run's record says it was given.",
    rich_help_panel="Robot",
)
_ROOM = typer.Option(
    None,
    "--room",
    help=f"A room for the duck's simulator to lay out: {', '.join(BUILT_IN_ROOMS)}, or a .yaml "
    "file of your own. The duck keeps a map of it the way its own mapd would, revealed where it "
    "stops and looks, and can mark places in it. microduck:sim2d or microduck:mujoco, and one "
    "robot only.",
    rich_help_panel="Robot",
)
_FOV = typer.Option(
    None,
    "--fov-deg",
    help="Horizontal field of view of the camera actually on your robot, in degrees. The "
    "default is the simulator's 90; a Pi Camera Module 2 is about 62. Getting it wrong "
    "scales every bearing and distance, so detections say so until you set it.",
    rich_help_panel="Robot",
)
_VERBOSE = typer.Option(
    False,
    "--verbose",
    "-v",
    help="The compact view on stderr: one line per verb plus the executor's notes. The log "
    "(on by default) shows all of that and more, so this only adds anything with --no-log.",
    rich_help_panel="Output",
)
_LOG = typer.Option(
    None,
    "--log/--no-log",
    help="Narrate the run on stderr as it happens: the prompt, each observation, what the "
    "model thought and answered, every executor decision, every intent sent to the robot, "
    "every result, tokens and timings. On by default; QUACKD_LOG=0 turns it off too. This "
    "is about what you WATCH: the run directory gets its log either way.",
    rich_help_panel="Output",
)
_LOG_MCP = typer.Option(
    None,
    "--log/--no-log",
    help="Carry a log of what happened on every tool result, and the uncapped version on "
    "stderr: the verb, every gate that fired, every intent sent to the robot, every result "
    "and the budget. Over MCP the pilot is the client, so its own reasoning is not quackd's "
    "to show. On by default; QUACKD_LOG=0 turns it off too.",
    rich_help_panel="Output",
)
_LOG_PROMPT = typer.Option(
    None,
    "--log-prompt/--no-log-prompt",
    help="Print the system prompt once at the start of the log. On by default; "
    "QUACKD_LOG_PROMPT=0 turns it off too. It is in the transcript either way.",
    rich_help_panel="Output",
)


@app.command(rich_help_panel="Run a duck")
def run(
    duckfile: str | None = _DUCK_ARG,
    goal: str | None = _GOAL,
    image: list[str] = _IMAGE,
    by_hand: bool = _BY_HAND,
    llm: str | None = _LLM,
    robot: str | None = _ROBOT,
    robots: str | None = _ROBOTS,
    seed: int | None = _SEED,
    dry_run: bool = _DRY,
    max_steps: int | None = _MAXSTEPS,
    play: bool | None = _PLAY,
    runs_dir: str = _RUNS,
    yes: bool = _YES,
    live: bool = _LIVE,
    address: str | None = _ADDR,
    camera_url: list[str] = _CAMERA_URL,
    token: str | None = _TOKEN,
    fov_deg: float | None = _FOV,
    host: str | None = _HOST,
    host_token: str | None = _HOST_TOKEN,
    detector: str | None = _DETECTOR,
    policy_url: str | None = _POLICY_URL,
    policy_token: str | None = _POLICY_TOKEN,
    accept_other_frame: bool = _ACCEPT_OTHER_FRAME,
    async_chunks: bool = _ASYNC_CHUNKS,
    room: str | None = _ROOM,
    map_address: str | None = _MAP_ADDR,
    gif: bool = typer.Option(
        True,
        "--gif/--no-gif",
        help="Simulators: write run.gif into the run dir. The arm's simulator, "
        "lerobot:mujoco, writes none.",
        rich_help_panel="Output",
    ),
    gif_size: int = _GIFSIZE,
    verbose: bool = _VERBOSE,
    base_url: str | None = _BASEURL,
    api_key: str | None = _APIKEY,
    vision: bool | None = _VISION,
    extra_body: str | None = _EXTRA_BODY,
    effort: str | None = _EFFORT,
    decision_llm: str | None = _DECISION_LLM,
    decision_url: str | None = _DECISION_URL,
    decision_mode: str | None = _DECISION_MODE,
    decision_frames: bool | None = _DECISION_FRAMES,
    controller: str | None = _CONTROLLER,
    flock: str | None = _FLOCK,
    run_name: str | None = _RUN_NAME,
    price: str | None = _PRICE,
    memory: bool = _MEMORY,
    memory_dir: str | None = _MEMORY_DIR,
    registry_dir: str | None = _REGISTRY_DIR,
    log: bool | None = _LOG,
    log_prompt: bool | None = _LOG_PROMPT,
    front: str = _FRONT_OPTION,
) -> None:
    """Run a .duck file (or a --goal): the LLM picks verbs, quackd enforces the contract."""
    _fronted(
        "run",
        front,
        lambda: _run_command(
            duckfile=duckfile,
            goal=goal,
            image=image,
            by_hand=by_hand,
            llm=llm,
            robot=robot,
            robots=robots,
            seed=seed,
            dry_run=dry_run,
            max_steps=max_steps,
            play=play,
            runs_dir=runs_dir,
            yes=yes,
            live=live,
            address=address,
            camera_url=camera_url,
            token=token,
            fov_deg=fov_deg,
            host=host,
            host_token=host_token,
            detector=detector,
            policy_url=policy_url,
            policy_token=policy_token,
            accept_other_frame=accept_other_frame,
            async_chunks=async_chunks,
            room=room,
            map_address=map_address,
            gif=gif,
            gif_size=gif_size,
            verbose=verbose,
            base_url=base_url,
            api_key=api_key,
            vision=vision,
            extra_body=extra_body,
            effort=effort,
            decision_llm=decision_llm,
            decision_url=decision_url,
            decision_mode=decision_mode,
            decision_frames=decision_frames,
            controller=controller,
            flock=flock,
            run_name=run_name,
            price=price,
            memory=memory,
            memory_dir=memory_dir,
            registry_dir=registry_dir,
            log=log,
            log_prompt=log_prompt,
        ),
    )


def _run_command(
    *,
    duckfile: str | None,
    goal: str | None,
    image: list[str],
    by_hand: bool,
    llm: str | None,
    robot: str | None,
    robots: str | None,
    seed: int | None,
    dry_run: bool,
    max_steps: int | None,
    play: bool | None,
    runs_dir: str,
    yes: bool,
    live: bool,
    address: str | None,
    camera_url: list[str],
    token: str | None,
    fov_deg: float | None,
    host: str | None,
    host_token: str | None,
    detector: str | None,
    policy_url: str | None,
    policy_token: str | None,
    accept_other_frame: bool,
    async_chunks: bool,
    room: str | None,
    map_address: str | None,
    gif: bool,
    gif_size: int,
    verbose: bool,
    base_url: str | None,
    api_key: str | None,
    vision: bool | None,
    extra_body: str | None,
    effort: str | None,
    decision_llm: str | None,
    decision_url: str | None,
    decision_mode: str | None,
    decision_frames: bool | None,
    controller: str | None,
    flock: str | None,
    run_name: str | None,
    price: str | None,
    memory: bool,
    memory_dir: str | None,
    registry_dir: str | None,
    log: bool | None,
    log_prompt: bool | None,
) -> None:
    """`quackd run`'s body, the same behind either front end."""
    with _terminal_record():
        _run_impl(
            duckfile,
            goal,
            llm,
            seed,
            dry_run,
            max_steps,
            runs_dir,
            yes,
            live,
            address,
            camera_url,
            token,
            fov_deg,
            gif,
            gif_size,
            verbose,
            base_url=base_url,
            api_key=api_key,
            vision=vision,
            extra_body=extra_body,
            decision_llm=decision_llm,
            decision_url=decision_url,
            decision_mode=decision_mode,
            decision_frames=decision_frames,
            flock=flock,
            robot=robot,
            robots=robots,
            memory=memory,
            memory_dir=memory_dir,
            registry_dir=registry_dir,
            log_on=log,
            log_prompt=log_prompt,
            images=image,
            by_hand=by_hand,
            run_name=run_name,
            price=price,
            host=host,
            host_token=host_token,
            detector_choice=detector,
            policy_url=policy_url,
            policy_token=policy_token,
            accept_other_frame=accept_other_frame,
            async_chunks=async_chunks,
            room=room,
            map_address=map_address,
            controller=controller,
            play=play,
            effort=effort,
        )


@app.command(rich_help_panel="Run a duck")
def record(
    duckfile: str | None = _DUCK_ARG,
    goal: str | None = _GOAL,
    llm: str | None = _LLM,
    seed: int | None = typer.Option(0, "--seed"),
    max_steps: int | None = _MAXSTEPS,
    play: bool | None = _PLAY,
    runs_dir: str = _RUNS,
    run_name: str | None = _RUN_NAME,
    gif_size: int = _GIFSIZE,
    verbose: bool = _VERBOSE,
    base_url: str | None = _BASEURL,
    api_key: str | None = _APIKEY,
    vision: bool | None = _VISION,
    extra_body: str | None = _EXTRA_BODY,
    flock: str | None = typer.Option(
        None,
        "--flock",
        help="EXPERIMENTAL: N cooperating ducks (2-4) in sim2d. A count only: this command "
        "pins the simulator, so a stored flock belongs to `quackd run`.",
        rich_help_panel="Robot",
    ),
    room: str | None = _ROOM,
    log: bool | None = _LOG,
    log_prompt: bool | None = _LOG_PROMPT,
) -> None:
    """Like `run` on sim2d, but always writes a GIF (for READMEs and launches)."""
    with _terminal_record():
        if flock is not None and not flock.strip().isdigit():
            _fail(
                "record pins the simulator: --flock takes a count here, not a stored flock",
                hint="quackd run <duck> --flock NAME",
            )
            return
        _run_impl(
            duckfile,
            goal,
            llm,
            seed=seed,
            dry_run=False,
            max_steps=max_steps,
            runs_dir=runs_dir,
            yes=True,
            live=False,
            address=None,
            camera_url=[],
            token=None,
            fov_deg=None,
            gif=True,
            gif_size=gif_size,
            verbose=verbose,
            base_url=base_url,
            api_key=api_key,
            vision=vision,
            extra_body=extra_body,
            flock=flock,
            robot="microduck:sim2d",
            # Pinned off, not merely absent. `record` makes the recordings in this repository and
            # has to be reproducible without a network call, and leaving this to default meant
            # `QUACKD_DECISION_LLM` in somebody's environment quietly switched one on. Frames
            # too, which the mode does not decide: a `QUACKD_DECISION_FRAMES=1` there would
            # otherwise build a decision LLM for a run that asks it nothing.
            decision_mode="off",
            decision_frames=False,
            log_on=log,
            log_prompt=log_prompt,
            run_name=run_name,
            play=play,
            room=room,
        )


# ── preflight: task files rehearsed on the arm's simulator ─────────────────────────────


@app.command(rich_help_panel="Run a duck")
def preflight(
    duckfiles: list[str] = typer.Argument(
        ...,
        help=".duck files or globs to rehearse. A <task>.sim.yaml beside a file, if there is "
        "one, lays out the table for it and says what has to be so when each run ends.",
    ),
    robot: str = typer.Option(
        ...,
        "--robot",
        "-r",
        help="The simulator to rehearse on: a robot registered as lerobot:mujoco, which "
        "`quackd robot twin` makes of a registered arm, or lerobot:mujoco itself for the "
        "generic arm. Anything else is refused before it is built.",
        rich_help_panel="Robot",
    ),
    llm: str | None = typer.Option(
        None,
        "--llm",
        "-l",
        help="The pilot to rehearse with, as VENDOR[:MODEL]. Default: the robot's own, then "
        f"{LLM_ENV}, and preflight refuses where neither names one: the scripted pilot runs "
        "only when typed as --llm fake.",
        autocompletion=_complete_llm,
        rich_help_panel="Model",
    ),
    effort: str | None = _EFFORT,
    camera_url: list[str] = typer.Option(
        [],
        "--camera-url",
        help="A camera the simulator renders, as opencv://N?name=front, top or wrist, with "
        "?width, ?height and ?fov. The index is ignored. Repeatable, and replaces the robot's "
        "registered cameras, as it does on run.",
        rich_help_panel="Robot",
    ),
    image: list[str] = _IMAGE,
    max_steps: int | None = _MAXSTEPS,
    play: bool | None = _PLAY_PREFLIGHT,
    seeds: int = typer.Option(
        DEFAULT_SEEDS,
        "--seeds",
        min=1,
        help="Runs per file, the first on seed 0 and each after it on the next.",
        rich_help_panel="Task",
    ),
    connect_cycles: int = typer.Option(
        DEFAULT_CONNECT_CYCLES,
        "--connect-cycles",
        min=0,
        help="Connects and closes per file before its first run, the first on seed 0. An error "
        "that escapes any of them fails the file and no run is made. A connect that gives up "
        "in words on the --faults it met is noted and fails nothing.",
        rich_help_panel="Task",
    ),
    faults: str | None = typer.Option(
        None,
        "--faults",
        metavar="SPEC",
        help="Bus faults for the connect cycles to meet, seeded by each cycle's seed: rates "
        "for handshake, configure, write, torque, torque_read and temperature_read, and "
        "read_loss_from=N, as handshake=0.2,configure=0.3. A connect that retries and then "
        "gives up on them, as the arm's does, is noted rather than failed, and the runs after "
        "the cycles meet no faults.",
        rich_help_panel="Task",
    ),
    policy_url: str | None = _POLICY_URL,
    policy_token: str | None = _POLICY_TOKEN,
    accept_other_frame: bool = _ACCEPT_OTHER_FRAME,
    async_chunks: bool = _ASYNC_CHUNKS,
    runs_dir: str = _RUNS,
    registry_dir: str | None = _REGISTRY_DIR,
    as_json: bool = _JSON,
) -> None:
    """Rehearse task files on the arm's simulator, and exit 1 unless every run passed."""
    _preflight_impl(
        duckfiles,
        robot=robot,
        llm=llm,
        camera_url=camera_url,
        images=image,
        max_steps=max_steps,
        seeds=seeds,
        connect_cycles=connect_cycles,
        faults=faults,
        runs_dir=runs_dir,
        registry_dir=registry_dir,
        as_json=as_json,
        policy_url=policy_url,
        policy_token=policy_token,
        accept_other_frame=accept_other_frame,
        async_chunks=async_chunks,
        play=play,
        effort=effort,
    )


def _preflight_impl(
    duckfiles: list[str],
    *,
    robot: str,
    llm: str | None,
    camera_url: list[str],
    images: Sequence[str],
    max_steps: int | None,
    seeds: int,
    connect_cycles: int,
    faults: str | None,
    runs_dir: str,
    registry_dir: str | None,
    as_json: bool,
    policy_url: str | None = None,
    policy_token: str | None = None,
    accept_other_frame: bool = False,
    async_chunks: bool = True,
    play: bool | None = None,
    effort: str | None = None,
) -> None:
    from quackd.adapters.base import AdapterError, policy_choice
    from quackd.adapters.factory import describe, make_adapter
    from quackd.agent.images import TaskImageError, load_task_images
    from quackd.agent.providers.base import ProviderError
    from quackd.agent.providers.factory import make_provider, resolve_llm
    from quackd.preflight import PreflightError, Rehearsal, refuse_default_pilot, refuse_real
    from quackd.registry import Registry, RegistryError, resolve_robot_ref
    from quackd.transport.base import TransportError

    try:
        policy = policy_choice(
            policy_url,
            policy_token,
            accept_other_frame=accept_other_frame,
            async_chunks=async_chunks,
            registry_dir=registry_dir,
        )
    except ValueError as e:
        _fail(str(e))
        return
    # Which robot, and that it is a simulator, before anything is built: a real arm refused
    # after it was made would already have had its port opened by somebody's typo.
    try:
        here = resolve_robot_ref(robot, Registry(registry_dir))
        refuse_real(here.spec, here.label)
    except PreflightError as e:
        _fail(str(e), hint="then quackd preflight FILES --robot NAME-sim")
        return
    except (AdapterError, RegistryError) as e:
        _fail(str(e))
        return
    try:
        vendor, model_id, source = resolve_llm(
            llm, here.llm, robot=here.entry.name if here.entry is not None else None
        )
        refuse_default_pilot(source)
        # one pilot built now and thrown away: a missing key or extra is a sentence before any
        # world is loaded, rather than the same error on every seed of every file
        probe = make_provider(vendor, model=model_id, source=source, effort=effort)
    except (PreflightError, ProviderError) as e:
        _fail(str(e))
        return
    task_images: list[Any] = []
    if images:
        try:
            task_images = load_task_images(list(images))
        except TaskImageError as e:
            _fail(str(e))
            return
        if not probe.supports_vision:
            _fail(
                f"{probe.name} {probe.model} does not take images, so it cannot be given "
                f"{_plural(len(task_images), 'picture')}",
                hint="quackd list-models marks the models that take no frames",
            )
            return
    kwargs = here.adapter_kwargs(camera_url=camera_url)
    if policy is not None:
        # every connect cycle and every run is built with it, and checks it at connect
        kwargs["policy"] = policy
    try:
        # built once and never connected, so a camera, a fault spec or a stored field the
        # simulator refuses is one sentence now rather than a failed connect on every file
        built = make_adapter(here.spec, seed=0, faults=faults, **kwargs)
        manifest = describe(here.spec, policy=policy)
    except (AdapterError, TransportError, ImportError, ValueError) as e:
        _fail(str(e))
        return
    if policy is not None:
        # and a policy server that is not there is one sentence now too, as it is for a run
        ask = getattr(built, "ask_policy", None)
        try:
            if callable(ask):
                ask()
        except RuntimeError as e:
            _fail(
                str(e),
                hint=f"quackd policy check --policy-url {policy.url} asks it what it serves",
            )
            return

    def pilot(duck: Any) -> Any:
        return make_provider(
            vendor, model=model_id, source=source, duck_name=duck.name, effort=effort
        )

    rehearsal = Rehearsal(
        spec=here.spec,
        manifest=manifest,
        pilot=pilot,
        adapter_kwargs=kwargs,
        seeds=seeds,
        connect_cycles=connect_cycles,
        faults=faults,
        max_steps=max_steps,
        runs_dir=runs_dir,
        task_images=task_images,
        play=play,
    )
    files = _expand(duckfiles)
    ui.install_logging()

    async def main() -> list[Any]:
        # one file at a time, and one run at a time within it: the simulator's clock is seeded
        # and deterministic only for runs that do not overlap
        return [await rehearsal.file(path) for path in files]

    with ui.spinner(f"rehearsing {_plural(len(files), 'file')} on {here.label}") as say:
        rehearsal.progress = say
        reports = asyncio.run(main())
    failed = [r for r in reports if not r.ok]
    if as_json:
        for report in reports:
            print(json.dumps(report.public(), ensure_ascii=False))
        raise typer.Exit(code=1 if failed else 0)
    for report in reports:
        _print_preflight(report)
    runs = [run for report in reports for run in report.runs]
    costs = [run.cost_usd for run in runs]
    if runs:
        from quackd.agent.providers.pricing import fmt_usd

        cost = (
            f"model cost {fmt_usd(sum(c for c in costs if c is not None))} over "
            f"{_plural(len(runs), 'run')}"
            if all(c is not None for c in costs)
            else f"model cost unpriced over {_plural(len(runs), 'run')}: quackd has no rate "
            f"for {probe.name} {probe.model}"
        )
        ui.console.print(Text(cost, style=ui.STYLES["muted"]), soft_wrap=True)
    dt = next((r.sim_dt_s for r in reports if r.sim_dt_s is not None), None)
    if dt is not None:
        ui.console.print(Text(f"sim dt {dt:g} s", style=ui.STYLES["muted"]))
    if failed:
        _fail(
            f"{len(failed)} of {len(reports)} {_files(len(reports))} failed preflight",
            hint="each run's directory has its transcript, and quackd log DIR replays it",
        )
    ui.console.print(_ok_line(f"{len(reports)} {_files(len(reports))} passed preflight"))


def _print_preflight(report: Any) -> None:
    """One file: a row per run, then every reason one of them failed, as plain lines."""
    from quackd.agent.providers.pricing import fmt_usd
    from quackd.preflight import sidecar_path

    title = f"quackd preflight {report.file}"
    if report.runs:
        table = ui.table(title)
        table.add_column("seed", justify="right")
        table.add_column("outcome")
        table.add_column("steps", justify="right")
        table.add_column("close", ratio=2, overflow="fold")
        table.add_column("checks", justify="right")
        table.add_column("cost", justify="right")
        table.add_column("result")
        for run in report.runs:
            checks = [v for v in run.verdicts if v.check != "close"]
            close = next((v for v in run.verdicts if v.check == "close"), None)
            ok = run.ok
            table.add_row(
                str(run.seed),
                Text(run.outcome),
                str(run.steps),
                Text(close.detail if close is not None else "-"),
                f"{sum(v.ok for v in checks)} of {len(checks)}" if checks else "-",
                fmt_usd(run.cost_usd) if run.cost_usd is not None else "unpriced",
                Text("pass" if ok else "FAIL", style=ui.STYLES["ok" if ok else "fail"]),
            )
        ui.console.print(table)
    else:
        ui.console.print(Text(title, style=ui.STYLES["key"]))
    lines = [f"{report.file}: {p}" for p in report.problems]
    lines += [f"connect on seed {c.seed}: {c.error}" for c in report.cycles if not c.ok]
    lines += [f"seed {run.seed}: {why}" for run in report.runs for why in run.failures]
    for line in lines:
        ui.console.print(Text(f"  {line}"), soft_wrap=True)
    for cycle in report.cycles:
        if cycle.gave_up is not None:
            ui.console.print(
                Text(
                    f"  connect on seed {cycle.seed} gave up on the faults, as it should: "
                    f"{cycle.gave_up}",
                    style=ui.STYLES["muted"],
                ),
                soft_wrap=True,
            )
    if report.sidecar is None and not report.problems:
        ui.console.print(
            Text(
                f"  no {sidecar_path(report.file).name} beside it, so only the close was judged",
                style=ui.STYLES["muted"],
            ),
            soft_wrap=True,
        )


# ── log: replay a finished run ──────────────────────────────────────────────────────────


_LABELLED = re.compile(r"^\d{8}-\d{6}-")
"""The stamp `new_run_dir` writes, so the rest of a directory name can be read on its own."""


def _ends_with_label(rest: str, label: str) -> bool:
    """Is `label` the name somebody gave this run?

    `-example-1` matches `find-and-kick-example-1` and also `find-and-kick-example-1-1`, which
    is the same run's collision counter and not a different name, but never
    `find-and-kick-example-19`, which is a different run entirely and the reason this pass
    exists at all."""
    return re.search(rf"-{re.escape(label)}(-\d+)?$", rest) is not None


def _resolve_run(run: str | None, runs_dir: str) -> Path:
    """A run directory from what the user typed. In order: a transcript file, a directory, an
    exact name under --runs-dir, the newest carrying that `--run-name`, a timestamp prefix,
    the newest whose name contains the text. Nothing at all means the newest run, which is
    what you want after `quackd run` ends."""
    from quackd.agent.transcript import run_label

    root = Path(runs_dir)
    runs = sorted((d for d in root.glob("*") if d.is_dir()), key=lambda d: d.name)
    if run:
        typed = Path(run)
        if typed.is_file():
            return typed
        if typed.is_dir():
            return typed
        exact = root / run
        if exact.is_dir():
            return exact
        # The `--run-name` pass, ABOVE the timestamp prefix rather than below it. A bench
        # session of a hundred runs has `-example-1` and `-example-19` in it, and a substring
        # match hands you whichever is newest, so an exact label is what somebody typing the
        # name they gave a run meant. It goes first because a label can be all digits: a run
        # named `20260921` was unreachable by its own name while the prefix pass, which every
        # directory's timestamp satisfies, got to answer first.
        with contextlib.suppress(ValueError):
            label = run_label(run)
            for d in reversed(runs):
                # Against what follows the timestamp, and only where something precedes the
                # label there. Otherwise `quackd log hello-world` would match the bare
                # `<stamp>-hello-world` and quietly prefer an older unnamed run of that duck
                # over a newer named one, which is not what typing a duck name asks for.
                rest = _LABELLED.sub("", d.name, count=1)
                if rest != d.name and rest != label and _ends_with_label(rest, label):
                    return d
        for d in reversed(runs):
            if d.name.startswith(run):
                return d
        for d in reversed(runs):
            if run in d.name:
                return d
        newest = ", ".join(d.name for d in runs[-5:]) or "none yet"
        _fail(f"no run matching {run!r} under {root} (newest: {newest})")
    if not runs:
        _fail(f"no runs under {root}: pass a path, or run `quackd run` first")
    return runs[-1]


def _replay(
    records: list[dict[str, Any]],
    view: Any,
    *,
    from_step: int | None,
    frames: bool,
) -> dict[str, Any] | None:
    """Records back through the same renderer that printed them live, and the `run_end`.

    A transcript written before the log existed has `verb` records and no `verb_end`; they
    carry the same fields, so they are shown under the name the renderer knows. `frame` is
    skipped unless asked: one line per camera frame buries everything else."""
    from quackd.log import LogEvent

    end: dict[str, Any] | None = None
    # `verb` and `verb_end` both name the verb that ended; a live run has both and the
    # renderer draws only the second, so promote `verb` only when there is no `verb_end`
    legacy = not any(r.get("kind") == "verb_end" for r in records)
    skipping = from_step is not None
    for rec in records:
        kind = str(rec.get("kind", ""))
        data = {k: v for k, v in rec.items() if k not in ("t", "kind")}
        if kind == "run_end":
            end = data
        if skipping:
            if kind == "observation" and data.get("step") == from_step:
                skipping = False
            elif kind != "run_start":
                continue
        if kind == "frame":
            if frames:
                ui.console.print(f"        frame {data.get('path', '')}", style="dim", markup=False)
            continue
        if kind == "verb" and legacy:
            kind = "verb_end"
        view(LogEvent(kind, float(rec.get("t") or 0.0), data))
    view.flush()
    return end


_LOG_RUN = typer.Argument(
    None, help="A run directory, a transcript file, a name or a prefix. Default: the newest."
)


@app.command("drive", rich_help_panel="Run a duck")
def drive_cmd(
    robot: str | None = _ROBOT,
    registry_dir: str | None = _REGISTRY_DIR,
    address: str | None = _ADDR,
    camera_url: list[str] = _CAMERA_URL,
    token: str | None = _TOKEN,
    fov_deg: float | None = _FOV,
    host: str | None = _HOST,
    host_token: str | None = _HOST_TOKEN,
    detector: str | None = _DETECTOR,
    policy_url: str | None = _POLICY_URL,
    policy_token: str | None = _POLICY_TOKEN,
    accept_other_frame: bool = _ACCEPT_OTHER_FRAME,
    async_chunks: bool = _ASYNC_CHUNKS,
    seed: int | None = _SEED,
    llm: str | None = _LLM,
    controller: str | None = _DRIVE_CONTROLLER,
    base_url: str | None = _BASEURL,
    api_key: str | None = _APIKEY,
    vision: bool | None = _VISION,
    extra_body: str | None = _EXTRA_BODY,
    effort: str | None = _EFFORT,
    price: str | None = _PRICE,
    decision_llm: str | None = _DRIVE_DECISION_LLM,
    decision_url: str | None = _DECISION_URL,
    decision_mode: str | None = _DRIVE_DECISION_MODE,
    decision_frames: bool | None = _DRIVE_DECISION_FRAMES,
    duckfile: str | None = typer.Option(
        None,
        "--duckfile",
        help="Adopt a .duck's contract for the session: its allowlist, gates and budgets. Its "
        "body is not run.",
    ),
    room: str | None = _ROOM,
    map_address: str | None = _MAP_ADDR,
    yes: bool = _YES,
    dry_run: bool = _DRY,
    max_steps: int | None = _DRIVE_MAXSTEPS,
    play: bool | None = _DRIVE_PLAY,
    memory: bool = _DRIVE_MEMORY,
    memory_dir: str | None = _MEMORY_DIR,
    runs_dir: str = _RUNS,
    run_name: str | None = _RUN_NAME,
    front: str = _FRONT_OPTION,
) -> None:
    """Drive one robot, one command at a time: a verb of its own, a preset such as `forward`
    or `turn left`, or a goal in your own words for the pilot. Enter on its own brakes."""
    _fronted(
        "drive",
        front,
        lambda: _drive_command(
            robot=robot,
            registry_dir=registry_dir,
            address=address,
            camera_url=camera_url,
            token=token,
            fov_deg=fov_deg,
            host=host,
            host_token=host_token,
            detector=detector,
            policy_url=policy_url,
            policy_token=policy_token,
            accept_other_frame=accept_other_frame,
            async_chunks=async_chunks,
            seed=seed,
            llm=llm,
            controller=controller,
            base_url=base_url,
            api_key=api_key,
            vision=vision,
            extra_body=extra_body,
            effort=effort,
            price=price,
            decision_llm=decision_llm,
            decision_url=decision_url,
            decision_mode=decision_mode,
            decision_frames=decision_frames,
            duckfile=duckfile,
            room=room,
            map_address=map_address,
            yes=yes,
            dry_run=dry_run,
            max_steps=max_steps,
            play=play,
            memory=memory,
            memory_dir=memory_dir,
            runs_dir=runs_dir,
            run_name=run_name,
        ),
    )


def _drive_refusal(
    *,
    controller: str | None,
    price: str | None,
    yes: bool,
    dry_run: bool,
    policy_url: str | None,
    policy_token: str | None,
    accept_other_frame: bool,
    async_chunks: bool,
    registry_dir: str | None,
    llm: str | None,
    base_url: str | None,
    api_key: str | None,
    extra_body: str | None,
    vision: bool | None,
    play: bool | None,
    effort: str | None,
    decision_llm: str | None,
    decision_mode: str | None,
    decision_frames: bool | None,
    someone: bool,
) -> tuple[str, str | None] | None:
    """Why `quackd drive` cannot start with these flags, as a sentence and a hint, or None:
    what `quackd run` refuses of the same flags, in its words, before anything is built.

    A VLA pilot is refused as a run refuses it (`_vla_refusal`), with a goal's own line held to
    the rule for an instruction as each one reaches the pilot instead (`DriveSession`). The
    decision LLM is refused with it only where a goal's stepper was asked for: `--decision-llm`
    alone is the fast lane, which takes typed lines and never a goal's turns."""
    from quackd.adapters.base import policy_choice
    from quackd.agent.decision.base import DecisionError
    from quackd.agent.decision.factory import ENV_LLM, parse_decision_llm
    from quackd.agent.providers.pricing import parse_price

    if price is not None:
        try:
            parse_price(price, source="--price")
        except ValueError as e:
            return str(e), None
    flown_by = (controller or "").strip().lower() or CONTROLLERS[0]
    if flown_by not in CONTROLLERS:
        return f"--controller is {' or '.join(CONTROLLERS)}, not {controller!r}", None
    if flown_by != "vla":
        return None
    try:
        policy = policy_choice(
            policy_url,
            policy_token,
            accept_other_frame=accept_other_frame,
            async_chunks=async_chunks,
            registry_dir=registry_dir,
        )
    except ValueError as e:
        return str(e), None
    refused = _vla_refusal(
        yes=yes,
        dry_run=dry_run,
        has_policy=policy is not None,
        goal=None,
        someone=someone,
        command="drive",
        ignored=[
            flag
            for flag, given in (
                ("--llm", llm is not None),
                ("--base-url", base_url is not None),
                ("--api-key", api_key is not None),
                ("--extra-body", extra_body is not None),
                ("--vision", vision is not None),
                ("--play", play is True),
            )
            if given
        ],
    )
    if refused is not None:
        return refused
    if effort is not None:
        return (
            "--effort says how hard a model reasons, and --controller vla asks no model: the "
            "policy server drives the arm and the scripted pilot only reads it the steps",
            None,
        )
    try:
        named = parse_decision_llm(decision_llm)
    except DecisionError as e:
        return str(e), None
    mode = (decision_mode or "").strip().lower()
    if named is not None and ((mode and mode != "off") or decision_frames):
        return (
            "--controller vla runs no model for a decision LLM to step in front of, and "
            f"{'--decision-mode' if mode and mode != 'off' else '--decision-frames'} asks for "
            f"{'--decision-llm' if decision_llm is not None else ENV_LLM} to",
            "drop it: --decision-llm alone is the fast lane, which takes typed lines and never "
            "a goal's turns",
        )
    return None


def _drive_command(
    *,
    robot: str | None,
    registry_dir: str | None,
    address: str | None,
    camera_url: list[str],
    token: str | None,
    fov_deg: float | None,
    host: str | None,
    host_token: str | None,
    detector: str | None,
    policy_url: str | None,
    policy_token: str | None,
    accept_other_frame: bool,
    async_chunks: bool,
    seed: int | None,
    llm: str | None,
    controller: str | None,
    base_url: str | None,
    api_key: str | None,
    vision: bool | None,
    extra_body: str | None,
    effort: str | None,
    price: str | None,
    decision_llm: str | None,
    decision_url: str | None,
    decision_mode: str | None,
    decision_frames: bool | None,
    duckfile: str | None,
    room: str | None,
    map_address: str | None,
    yes: bool,
    dry_run: bool,
    max_steps: int | None,
    play: bool | None,
    memory: bool,
    memory_dir: str | None,
    runs_dir: str,
    run_name: str | None,
) -> None:
    """`quackd drive`'s body, the same behind either front end."""
    from quackd.adapters.base import AdapterError
    from quackd.agent.decision.base import DecisionError
    from quackd.agent.providers.base import ProviderError
    from quackd.drive.run import DriveArgs, DriveError, run_drive
    from quackd.duckfile.parser import DuckParseError
    from quackd.registry import RegistryError
    from quackd.transport.base import TransportError

    front = _FRONT

    def say(line: str) -> None:
        ui.console.print(line, soft_wrap=True, highlight=False, markup=False)
        if front is not None:
            front.say(line)

    # `_can_prompt` and not `isatty` alone, which says yes to NUL on Windows: whether the
    # keyboard opens, and what it reads is recorded as a person's, is decided as every question
    # quackd puts to a person is. Its prompt needs a terminal to be drawn on as well.
    at_terminal = _can_prompt()
    keyboard = at_terminal and _can_draw_a_prompt()
    refused = _drive_refusal(
        controller=controller,
        price=price,
        yes=yes,
        dry_run=dry_run,
        policy_url=policy_url,
        policy_token=policy_token,
        accept_other_frame=accept_other_frame,
        async_chunks=async_chunks,
        registry_dir=registry_dir,
        llm=llm,
        base_url=base_url,
        api_key=api_key,
        extra_body=extra_body,
        vision=vision,
        play=play,
        effort=effort,
        decision_llm=decision_llm,
        decision_mode=decision_mode,
        decision_frames=decision_frames,
        # a person to put a goal's questions to: at the page, or at the keyboard, where lines
        # are not read one at a time as from a pipe
        someone=front is not None or keyboard,
    )
    if refused is not None:
        _fail(refused[0], hint=refused[1])
        return
    args = DriveArgs(
        robot=robot,
        registry_dir=registry_dir,
        address=address,
        camera_url=tuple(camera_url),
        token=token,
        host=host,
        host_token=host_token,
        detector=detector,
        seed=seed,
        llm=llm,
        decision_llm=decision_llm,
        decision_url=decision_url,
        duckfile=duckfile,
        room=room,
        map_address=map_address,
        yes=yes,
        dry_run=dry_run,
        runs_dir=runs_dir,
        run_name=run_name,
        max_steps=max_steps,
        play=play,
        controller=(controller or "").strip().lower() or CONTROLLERS[0],
        policy_url=policy_url,
        policy_token=policy_token,
        accept_other_frame=accept_other_frame,
        async_chunks=async_chunks,
        base_url=base_url,
        api_key=api_key,
        vision=vision,
        extra_body=extra_body,
        effort=effort,
        price=price,
        decision_mode=decision_mode,
        decision_frames=decision_frames,
        memory=memory,
        memory_dir=memory_dir,
        fov_deg=fov_deg,
    )
    if at_terminal and not keyboard:
        say(
            f"{'note':<8} the prompt needs a terminal for its output too, so lines are read one at "
            "a time and each waited for, as from a pipe"
        )
    try:
        run_dir = asyncio.run(run_drive(args, say=say, keyboard=keyboard, front=front))
    except (
        DriveError,
        ProviderError,
        DecisionError,
        RegistryError,
        AdapterError,
        TransportError,
        DuckParseError,
    ) as e:
        _fail(str(e))
        return
    except SystemExit as e:
        # `fleet_from_flags` refuses a robot it cannot build with a sentence of its own, which
        # behind the page is the page's refusal; at a terminal it leaves as it always has
        if front is not None and isinstance(e.code, str):
            _fail(e.code)
            return
        raise
    say(f"{'record':<8} {run_dir}")


@app.command("log", rich_help_panel="Run a duck")
def log_cmd(
    run: str | None = _LOG_RUN,
    runs_dir: str = _RUNS,
    prompt: bool | None = typer.Option(
        None, "--prompt/--no-prompt", help="Show the system prompt the run was given."
    ),
    thinking: str | None = typer.Option(
        None, "--thinking", help="Characters of thinking per turn: a number, or `all`."
    ),
    from_step: int | None = typer.Option(
        None, "--from-step", help="Start at this step, skipping the turns before it."
    ),
    frames: bool = typer.Option(False, "--frames", help="Also print one line per camera frame."),
) -> None:
    """Replay a finished run's log as the lines it printed while it ran.

    On stdout, because a replay is what you pipe to a pager or a file, and unaffected by
    QUACKD_LOG: that switch is about narrating live, and asking for a replay is asking."""
    from quackd.agent.transcript import Transcript
    from quackd.log import ConsoleLog, parse_thinking_limit
    from quackd.log import prompt_shown_default as _prompt_default
    from quackd.log import thinking_limit_default as _thinking_default

    target = _resolve_run(run, runs_dir)
    run_dir = target.parent if target.is_file() else target
    transcripts = (
        [target]
        if target.is_file()
        else sorted((run_dir / "ducks").glob("*/transcript.jsonl"))
        or [run_dir / "transcript.jsonl"]
    )
    if not transcripts[0].exists():
        _fail(f"{transcripts[0]} does not exist: that is not a run directory")

    width = max((len(p.parent.name) for p in transcripts), default=0) if len(transcripts) > 1 else 0
    end: dict[str, Any] | None = None
    cut = 0
    for i, path in enumerate(transcripts):
        records = Transcript.read(path, lenient=True)
        cut += int(records[-1].get("_skipped", 0)) if records else 0
        view = ConsoleLog(
            ui.console,
            thinking_chars=(
                parse_thinking_limit(thinking) if thinking is not None else _thinking_default()
            ),
            prompt=prompt if prompt is not None else _prompt_default(),
            progress_s=None,  # a replay is not live: one line per burst, as the record has it
            prefix=f"{path.parent.name:<{width}}  " if width else "",
            prefix_style=ui.MEMBER_STYLES[i % len(ui.MEMBER_STYLES)] if width else "",
            # a replay has no CLI header in front of it, so this is where the run says what
            # it was: which duck, which model, which robot, and how long connecting took
            header=True,
        )
        end = _replay(records, view, from_step=from_step, frames=frames) or end

    if cut:
        ui.err_console.print(
            f"log: {cut} unreadable line(s) skipped, the run was cut while it was writing",
            style="yellow",
            markup=False,
        )
    summary = run_dir / "summary.json"
    if summary.exists() and (end is None or len(transcripts) > 1):
        # A flock replays every member, so `end` is whichever member happened to finish last
        # and its wall clock and its bill are that ONE robot's. The flock's own summary is
        # sitting in the same directory and is the thing the counters are about.
        end = json.loads(summary.read_text(encoding="utf-8"))
    if end is None:
        _fail("no run_end: the run did not finish, or is still running")
        return
    if "kicker" in end:  # a flock counts different things, and its summary is the only source
        counters = [f"spotter {end['spotter']}"] if end.get("spotter") else []
        counters += [
            f"kicker {end.get('kicker')}",
            f"auctions {end.get('auctions')}",
            f"bids {end.get('bids')}",
        ]
    else:
        counters = run_counters(end)
    _print_outcome(
        str(end.get("outcome", "error")),
        str(end.get("reason", "")),
        counters=counters,
        run_dir=run_dir,
        gif_path=gif if (gif := run_dir / "run.gif").exists() else None,
        # `trace_dropped` is still read, so a run directory recorded before the rename
        # replays and its counter still reaches the panel. The value goes through `_number`
        # because this is the path an old run directory takes, and those are hand-edited,
        # truncated and copied between machines, so the field cannot be trusted to hold a
        # number at all.
        log_dropped=int(_number(end.get("log_dropped") or end.get("trace_dropped") or 0) or 0),
    )


# ── doctor / serve-mcp ──────────────────────────────────────────────────────────────────


@app.command(rich_help_panel="Inspect")
def doctor(
    robot: str | None = typer.Option(
        None,
        "--robot",
        "-r",
        help="Also show one robot's manifest (<adapter>:<backend>, or a registered name).",
    ),
    address: str | None = typer.Option(
        None,
        "--address",
        help="With --robot, connect to a real robot and report what it says about itself.",
    ),
    map_address: str | None = _MAP_ADDR,
    camera_url: list[str] = _CAMERA_URL,
    token: str | None = _TOKEN,
    host: str | None = _HOST,
    host_token: str | None = _HOST_TOKEN,
    registry_dir: str | None = _REGISTRY_DIR,
    as_json: bool = _JSON,
) -> None:
    """Check the environment: keys, optional extras, adapters, upstream assumptions.

    With `--robot X --address Y` it also connects, which is the only way to see what a
    robot actually reports before a run does. A registered simulator is connected without
    an address too, because nothing real moves. With `--host` it asks the daemon on that board
    what it is, reads the board's health over the network, and probes the local model
    presets there."""
    from quackd.doctor import HostReport, collect, refused_host, render
    from quackd.host import HOST_ENV, HostChoice, resolve_host
    from quackd.registry import RobotEntry

    if address and not robot:
        _fail("--address needs --robot, so quackd knows what it is connecting to")
        return
    rest_pose: dict[str, float] | None = None
    entry: RobotEntry | None = None
    name: str | None = None
    if robot:
        # a registered name is a robot too, and it brings the address you registered it with.
        # Only a name that resolves is substituted: anything else stays exactly as typed, so
        # `doctor --robot nope:x --json` still reports the bad spec inside its one JSON
        # document rather than dying with a line of prose before it.
        from quackd.registry import Registry, RegistryError

        with contextlib.suppress(RegistryError):
            entry = Registry(registry_dir).get_robot(robot)
            if entry is not None:
                # The spec for the report, and the name for the body, which is built under it
                # as a run builds it (`entry.robot_spec`): an arm looks its calibration up by
                # that id, and every line it writes names it. The name used to be dropped here,
                # so a probe of `arm-02` read the default id's calibration.
                robot, name = entry.key, entry.name
                where = entry.adapter_kwargs(
                    address=address, camera_url=camera_url, token=token, map_address=map_address
                )
                address, camera_url, token, map_address = (
                    where["address"],
                    where["camera_url"],
                    where["token"],
                    where["map_address"],
                )
                # only a robot you registered has one, because a rest pose is read off the arm
                # and kept under its name rather than typed on a command line
                rest_pose = where["rest_pose"]
    if map_address and not address:
        # only a robot doctor connects to has a map to ask, and a flag it would drop is a check
        # somebody believes passed
        _fail(
            "--map-address needs a robot doctor connects to: give --robot and --address, or "
            "register the robot with its address"
        )
        return
    # The board by the ladder `run` climbs, settled by the same function so the two cannot
    # disagree about which board a robot uses: the flag, then the host registered with the
    # robot --robot names, then QUACKD_HOST. A value that is no machine, a token no header
    # can carry, or a typed --host-token with no board to go to, is reported with the place it
    # came from rather than refused as `run` refuses it: doctor is where a person comes to find
    # a bad setting, one in QUACKD_HOST is there without anybody having typed --host, and
    # --json stays one document, as it does for a bad --robot above. Nothing is asked of it,
    # and it fails the report as a dead daemon does.
    stored = entry.host_kwargs() if entry is not None else {"host": None, "host_token": None}
    unusable: HostReport | None = None
    try:
        board = resolve_host(
            host,
            stored["host"],
            token=host_token,
            stored_token=stored["host_token"],
            robot=entry.name if entry is not None else None,
        )
    except ValueError as e:
        # the ladder stops at the first value that is not blank, so that is the one refused;
        # a token with no board has none, and the section is titled plain `host`
        named = (host, stored["host"], os.environ.get(HOST_ENV))
        text = next((t.strip() for t in named if t and t.strip()), "")
        board, unusable = HostChoice(), refused_host(text, str(e))

    from quackd.adapters.factory import needs_rest_pose

    # the person at an arm, at a terminal: hands away before its fold, and over one with no rest
    # pose, whether to drop it into their hands rather than leave it powered (ADR-0058)
    hands = _DoctorHands(
        name or robot or "",
        prompting=not as_json and _can_prompt(),
        unposed=bool(robot) and rest_pose is None and needs_rest_pose(robot or ""),
    )

    def warn(adapter: Any) -> None:
        # A backend its adapter is retiring is said first, in the line a run prints for it,
        # and only here, just before the probe connects through it. A real body is connected
        # only when there is an address to reach it at, typed or registered with it, so
        # without one nothing is said, and the adapters table says which backends are going.
        if robot:
            _deprecated_backends([robot])
        # Before the connect, and only for a body that is handed to people, which is the one
        # whose connect takes torque off: the arm's close note sends a person here when it is
        # holding itself up away from its fold. On stderr under --json, whose stdout is one
        # JSON document and nothing else.
        # A simulator of such a body says it is one instead of asking for a hand under it.
        if getattr(adapter, "supports_hand_off", False):
            line = _doctor_warning(
                simulator=_is_simulator(adapter),
                folds=getattr(adapter, "rest_pose", None) is not None,
                keeps=hands.unposed,
                asks=hands.prompting,
            )
            (ui.err_console if as_json else ui.console).print(_warn_line(line))
        hands.start(adapter)

    if as_json:
        try:
            report = collect(
                robot,
                address=address,
                map_address=map_address,
                camera_url=camera_url,
                token=token,
                rest_pose=rest_pose,
                robot_name=name,
                before_connect=warn,
                host=board.host,
                host_token=board.token,
                host_token_fix=board.token_fix,
                park=hands.park,
                after_close=hands.finish,
            )
        finally:
            hands.finish()
        report.host = unusable or report.host
        print(json.dumps(report.to_dict()))
        raise typer.Exit(code=0 if report.ok else 1)
    ui.install_logging()
    # the probes are the slow part: five local servers at 1.5 s each, and a real robot after
    # them. It used to sit silent for ten seconds with no sign it was doing anything.
    try:
        with ui.spinner("checking this machine") as say:
            report = collect(
                robot,
                address=address,
                map_address=map_address,
                camera_url=camera_url,
                token=token,
                rest_pose=rest_pose,
                host=board.host,
                host_token=board.token,
                host_token_fix=board.token_fix,
                progress=say,
                robot_name=name,
                before_connect=warn,
                park=hands.park,
                before_close=hands.before_close,
                after_close=hands.finish,
            )
    finally:
        hands.finish()
    report.host = unusable or report.host
    render(ui.console, report)
    if not report.ok:
        raise typer.Exit(code=1)


@app.command("ui", rich_help_panel="Serve")
def ui_cmd(
    bind: str = typer.Option(
        "127.0.0.1",
        "--bind",
        help="The address the page listens on. 127.0.0.1, the default, is this computer alone. "
        "0.0.0.0 is every network it is on, for a phone on the same Wi-Fi, and then the page asks "
        "for its token, which the printed address carries.",
    ),
    port: int = typer.Option(8501, "--port", help="The port the page listens on."),
    browser: bool = typer.Option(
        True, "--browser/--no-browser", help="Open the page in this computer's browser."
    ),
    new_token: bool = typer.Option(
        False,
        "--new-token",
        help="Make the page a new token, so an address saved with the old one, on a phone, "
        "stops working.",
    ),
) -> None:
    """Serve the page: choose a robot and its pilot, then give it a goal or chat with it, from a
    browser or a phone."""
    from quackd.registry import DEFAULT_DIR as REGISTRY_DEFAULT_DIR
    from quackd.registry import ENV_DIR as REGISTRY_ENV
    from quackd.registry import registry_dir
    from quackd.webui import launch
    from quackd.webui.catalog import installed

    if not installed("streamlit"):
        _fail(
            "quackd ui needs Streamlit, which comes with the ui extra",
            hint='uv pip install "quackd[ui]", or run uvx --from "quackd[ui]" quackd ui',
        )
        return
    if launch.port_taken(bind, port):
        _fail(
            f"port {port} is already in use: another quackd ui, or another program, listens there",
            hint=f"quackd ui --port {port + 1}",
        )
        return
    # The page's token and its saved setup live in the home the shell names, never one a `.env`
    # names: a cloned project's `.env` must not choose the token, nor a setup with a policy
    # server of its own in it. Streamlit's settings come from its command line, and none from a
    # `.env` either. The runs the page starts get the shell's environment alone and load the
    # working folder's `.env` themselves (`jobs._child_environment`).
    home = (
        Path(REGISTRY_DEFAULT_DIR).expanduser()
        if dotenv_names.from_dotenv(REGISTRY_ENV)
        else registry_dir()
    )
    for name in dotenv_names.loaded():
        if name.startswith("STREAMLIT_"):
            os.environ.pop(name, None)

    def say(line: str) -> None:
        ui.console.print(line, markup=False, highlight=False, soft_wrap=True)

    launch.serve(
        folder=Path.cwd(),
        bind=bind,
        port=port,
        browser=browser,
        token_dir=home,
        new_token=new_token,
        say=say,
    )


@app.command("serve-mcp", rich_help_panel="Serve")
def serve_mcp(
    robot: str | None = _ROBOT,
    robots: str | None = typer.Option(
        None,
        "--robots",
        help="A flock: name=<adapter>:<backend>,... (nine robot_* tools, one executor each).",
        rich_help_panel="Robot",
    ),
    flock: str | None = typer.Option(
        None,
        "--flock",
        help="A stored flock (`quackd flock list`): every member from the registry, each with "
        "its own address, token and camera. The same flock by another door.",
        rich_help_panel="Robot",
    ),
    registry_dir: str | None = _REGISTRY_DIR,
    duckfile: str | None = typer.Option(
        None, "--duckfile", help="Load a .duck contract at startup (on the default robot)."
    ),
    seed: int | None = _SEED,
    address: str | None = _ADDR,
    camera_url: list[str] = _CAMERA_URL,
    token: str | None = _TOKEN,
    host: str | None = _HOST,
    host_token: str | None = _HOST_TOKEN,
    detector: str | None = _DETECTOR,
    policy_url: str | None = _POLICY_URL,
    policy_token: str | None = _POLICY_TOKEN,
    accept_other_frame: bool = _ACCEPT_OTHER_FRAME,
    async_chunks: bool = _ASYNC_CHUNKS,
    room: str | None = _ROOM,
    map_address: str | None = _MAP_ADDR,
    dry_run: bool = _DRY,
    yes: bool = typer.Option(
        False, "--yes", "-y", help="Allow confirm-gated verbs (there is no terminal to ask)."
    ),
    memory: bool = _MEMORY,
    memory_dir: str | None = _MEMORY_DIR,
    log: bool | None = _LOG_MCP,
    controller: str | None = typer.Option(
        None,
        "--controller",
        hidden=True,
        help="Refused: over MCP the client is the pilot. quackd run takes it.",
    ),
) -> None:
    """Expose a robot, or a flock of them, as MCP tools over stdio (Claude Code /
    Claude Desktop)."""
    from quackd.duckfile.parser import DuckParseError
    from quackd.mcp_server import serve
    from quackd.registry import RegistryError
    from quackd.transport.base import TransportError

    if controller is not None:
        # parsed only to be refused in words, since a flag Typer does not know is refused in
        # its own, which say nothing about why or where it belongs
        _fail(
            "--controller picks who flies a quackd run, and over MCP the client flies, with no "
            "terminal for a vla run to ask whether the arm did the task",
            hint="quackd run --controller vla, from a terminal",
        )
    try:
        serve(
            robot=robot,
            robots=robots,
            flock=flock,
            registry_dir=registry_dir,
            duckfile=duckfile,
            seed=seed,
            address=address,
            camera_url=camera_url,
            token=token,
            host=host,
            host_token=host_token,
            detector=detector,
            dry_run=dry_run,
            yes=yes,
            memory=memory,
            memory_dir=memory_dir,
            log=log,
            policy_url=policy_url,
            policy_token=policy_token,
            accept_other_frame=accept_other_frame,
            async_chunks=async_chunks,
            room=room,
            map_address=map_address,
            # the robots are connected in there, so a retiring backend is said from there,
            # just before the server's start connects a robot on it, in the line `run` and
            # `doctor` print for it
            warn=_deprecated,
        )
    except (DuckParseError, TransportError, RegistryError) as e:
        # a task file the server cannot read is refused before anything connects, as `run`
        # refuses it, rather than as a traceback with the sentence at its foot
        _fail(str(e))
    except BaseExceptionGroup as group:
        # A refusal raised as the server starts, by a connect in its lifespan, reaches here
        # inside the task group the MCP SDK serves in. Left there it printed as a traceback
        # with the sentence buried in it, and the client saw only a closed connection: it is
        # said as `quackd run` says the same refusal, and anything else stays a traceback.
        refused = _refusals(group, (TransportError, RegistryError))
        if not refused:
            raise
        _fail("; ".join(dict.fromkeys(str(e) for e in refused)))


# ── policy (a policy server the user starts) ────────────────────────────────────────────

policy_app = typer.Typer(
    name="policy",
    help="A learned policy for the arm, in a process of its own: quackd policy serve starts "
    "one, quackd policy check asks one what it serves, and quackd policy endpoint starts one "
    "on a GPU rented from Hugging Face.",
    no_args_is_help=True,
)
app.add_typer(policy_app, name="policy", rich_help_panel="Serve")

_POLICY_HELP = (
    "What to serve: REPO@REVISION for a LeRobot checkpoint (ACT, SmolVLA or pi05, which need "
    # the backslash is Rich's escape, as in the app's epilog: Typer renders help as markup, and
    # an unescaped [lerobot-vla] is a style tag it drops, leaving "which need quackd)"
    r"quackd\[lerobot-vla]), or scripted:NAME for a scripted policy that needs no torch "
    "(scripted:hold holds the arm where it is, scripted:sweep swings its wrist)."
)
_POLICY_FPS = typer.Option(
    None,
    "--fps",
    help="The rate the policy runs at, in Hz. Without it a checkpoint's is the fps of the "
    "dataset its train_config.json names, at the commit or the tag it names, and a scripted one "
    "has its own.",
)
_POLICY_PINS = typer.Option(
    None,
    "--pin",
    help="REPO@REVISION of a model the checkpoint names inside itself, such as SmolVLA's "
    "backbone, fetched at that revision and never at whatever the Hub has that day. The "
    "revision is a whole commit or a tag, and never a branch, which moves. Once per model. A "
    "checkpoint that names one without a pin is refused.",
)
_POLICY_CAMERAS = typer.Option(
    None,
    "--cameras",
    help="Which of the arm's cameras is which of the policy's images: "
    "NAME=KEY,... such as front=observation.images.front.",
)
_POLICY_LATENCY = typer.Option(
    None,
    "--latency-s",
    help="How long the policy takes to answer a step, declared. The simulator holds each "
    "chunk back that long, and quackd policy check --bench measures the real one. It has to "
    "be shorter than the 5 s a segment waits for its first chunk, and no longer than half a "
    "chunk's actions, rounded down, take to play, since the next chunk is asked for only once "
    "the last has landed.",
)
_POLICY_THREADS = typer.Option(None, "--threads", help="The threads the policy may use.")
_POLICY_JPEG = typer.Option(
    None,
    "--jpeg-quality",
    help="Ask for frames as JPEG at this quality (50 to 100) rather than raw. Raw is the "
    "default on loopback. A tunnel looks like loopback to both ends, so give it one for a "
    "server reached through ssh -L.",
)


def _policy_server() -> Any:
    """`quackd_lerobot.policy.server`, imported only when a policy command runs, or a failure
    that names the extra that installs it."""
    try:
        from quackd_lerobot.policy import server
    except ImportError:
        from quackd.adapters.base import AdapterNotInstalled

        _fail(str(AdapterNotInstalled("lerobot", "quackd[lerobot]")))
    return server


def _serve_options(
    *,
    policy: str,
    fps: float | None = None,
    cameras: str | None = None,
    latency_s: float | None = None,
    bind: str = "127.0.0.1",
    port: int | None = None,
    token_file: str | None = None,
    token_env: str | None = None,
    behind_tls: bool = False,
    threads: int | None = None,
    jpeg_quality: int | None = None,
    pins: Sequence[str] | None = None,
) -> Any:
    """`quackd policy serve`'s flags as the `ServeOptions` the server is started with, built in
    one place. The command builds its own here, and so does anything that has to read a serve
    command's arguments the way the command reads them: the test suite's stand-in for an
    endpoint's container parses the arguments `quackd policy endpoint up` gives it with this
    command's own parser and builds its options here, so what it serves is what the command
    would. The keywords are the command's own parameters, by the same names."""
    server = _policy_server()
    return server.ServeOptions(
        policy=policy,
        fps=fps,
        cameras=cameras,
        latency_s=latency_s,
        bind=bind,
        port=server.wire.DEFAULT_PORT if port is None else port,
        token_file=token_file,
        token_env=token_env,
        behind_tls=behind_tls,
        threads=threads,
        jpeg_quality=jpeg_quality,
        pins=tuple(pins or ()),
    )


@policy_app.command("serve")
def policy_serve(
    policy: str = typer.Option(..., "--policy", help=_POLICY_HELP),
    fps: float | None = _POLICY_FPS,
    cameras: str | None = _POLICY_CAMERAS,
    latency_s: float | None = _POLICY_LATENCY,
    bind: str = typer.Option(
        "127.0.0.1",
        "--bind",
        help="The address to listen on: 127.0.0.1 or ::1, the addresses the client sends plain "
        "HTTP to, unless --behind-tls says a TLS proxy stands in front of it. Reach a server on "
        "another machine through ssh -L.",
    ),
    port: int | None = typer.Option(
        None, "--port", help="The port to listen on (default 9875, the one after 9874)."
    ),
    token_file: str | None = typer.Option(
        None,
        "--token-file",
        help="A file holding the token clients must send. Without it, the token in "
        "~/.quackd/policy.token, written there the first time.",
    ),
    token_env: str | None = typer.Option(
        None,
        "--token-env",
        help="The name of a variable holding the token clients must send, instead of a file: "
        "how a platform that keeps a container's secrets hands one over. Only the name is "
        "printed, never what it holds.",
    ),
    behind_tls: bool = typer.Option(
        False,
        "--behind-tls",
        help="A TLS proxy stands in front of this server, so it may bind beyond loopback, and "
        "it asks for JPEG frames unless --jpeg-quality says otherwise.",
    ),
    threads: int | None = _POLICY_THREADS,
    jpeg_quality: int | None = _POLICY_JPEG,
    pins: list[str] | None = _POLICY_PINS,
) -> None:
    """Serve a policy for the arm, in this terminal, until Ctrl+C."""
    server = _policy_server()
    options = _serve_options(
        policy=policy,
        fps=fps,
        cameras=cameras,
        latency_s=latency_s,
        bind=bind,
        port=port,
        token_file=token_file,
        token_env=token_env,
        behind_tls=behind_tls,
        threads=threads,
        jpeg_quality=jpeg_quality,
        pins=pins,
    )
    try:
        served = server.open_server(options)
    except server.ServeRefused as e:
        _fail(str(e))
        return
    how = "written now" if served.token_written else "read"
    info = served.app.info
    rows = [
        ("serving", f"{info.policy} at {served.url}"),
        (
            # the variable's name, and never what it holds
            ("token", f"from ${served.token_env}")
            if served.token_env
            else ("token", f"{served.token_path} ({how})")
        ),
        ("rate", f"{info.rate_hz:g} Hz, from {info.rate_source}"),
    ]
    if info.loaded:
        # every repository the server fetched, at the revision it fetched it at
        rows.append(("loaded", "; ".join(info.loaded)))
    ui.console.print(ui.kv_grid(rows), soft_wrap=True)
    # the client sends plain http to loopback alone, so the hint names a loopback address,
    # never the 0.0.0.0 a server behind a TLS proxy may bind
    local = served.local_url
    if local is not None:
        check = f"quackd policy check --policy-url {local} asks it what it serves."
    else:
        check = (
            "quackd policy check --policy-url https://PROXY asks it what it serves, through the "
            "TLS proxy in front of it."
        )
    if behind_tls and local is not None:
        check += " From another machine, give it the TLS proxy's https:// address."
    ui.console.print(
        Text(f"  {check} Ctrl+C stops it.", style=ui.STYLES["muted"]),
        soft_wrap=True,
    )
    try:
        served.wait()
    except KeyboardInterrupt:
        pass
    finally:
        served.close()


@policy_app.command("check")
def policy_check(
    policy: str | None = typer.Option(None, "--policy", help=_POLICY_HELP),
    policy_url: str | None = typer.Option(
        None,
        "--policy-url",
        help="A policy server that is running, as http://127.0.0.1:PORT, https:// for one "
        "behind TLS, or hf:NAME for one quackd policy endpoint up started. Instead of --policy.",
    ),
    policy_token: str | None = typer.Option(
        None,
        "--policy-token",
        help="The token the --policy-url server wants. Without it, QUACKD_POLICY_TOKEN, then "
        "the one quackd policy serve wrote to ~/.quackd/policy.token.",
    ),
    bench: bool = typer.Option(
        False,
        "--bench",
        help="Time one warm step, then stream synthetic observations at the policy's rate "
        "through the client, and say the rate it achieved, the ticks with nothing to send, the "
        "round trip, and the --latency-s to serve it with, read over every step it timed. "
        "Bench again served with that --latency-s.",
    ),
    seconds: float | None = typer.Option(
        None, "--seconds", help="How long --bench streams for (default 10)."
    ),
    async_chunks: bool = typer.Option(
        True,
        "--async-chunks/--no-async-chunks",
        help="Bench the policy as a segment plays it by default, asking for its next chunk "
        "while the last still plays. With --no-async-chunks the bench plays each chunk out "
        "before it asks for the next, as a run given --no-async-chunks does.",
    ),
    fps: float | None = _POLICY_FPS,
    cameras: str | None = _POLICY_CAMERAS,
    latency_s: float | None = _POLICY_LATENCY,
    threads: int | None = _POLICY_THREADS,
    jpeg_quality: int | None = _POLICY_JPEG,
    pins: list[str] | None = _POLICY_PINS,
    registry_dir: str | None = _REGISTRY_DIR,
) -> None:
    """Ask a policy what it serves, and with --bench how well it keeps up."""
    if (policy is None) == (policy_url is None):
        _fail(
            "give --policy to check a policy served here for the check, or --policy-url to "
            "check a server that is running, and not both",
            hint="quackd policy check --policy scripted:hold",
        )
        return
    given = {
        "--fps": fps,
        "--cameras": cameras,
        "--latency-s": latency_s,
        "--threads": threads,
        "--jpeg-quality": jpeg_quality,
        "--pin": pins or None,
    }
    if policy_url is not None and (named := [k for k, v in given.items() if v is not None]):
        _fail(
            f"{', '.join(named)} set how a policy is served, and the server at --policy-url "
            "was started with its own: give them to its quackd policy serve"
        )
        return
    if policy is not None and policy_token is not None:
        _fail("--policy-token goes with --policy-url: a policy served here gets its own token")
        return
    if seconds is not None and not (seconds > 0 and seconds < float("inf")):
        _fail(f"--seconds {seconds!r} is not a number of seconds to bench for")
        return
    if not async_chunks and not bench:
        _fail(
            "--no-async-chunks goes with --bench: it paces the bench's chunks, and a check "
            "without --bench asks for none"
        )
        return
    server = _policy_server()
    from quackd_lerobot.policy.client import (
        PolicyServerError,
        RemoteRunner,
        client_token,
        policy_address,
    )
    from quackd_lerobot.policy.endpoint import reach
    from quackd_lerobot.verbs import JOINTS

    served: Any = None
    runner: Any = None
    try:
        if policy is not None:
            import secrets

            served = server.open_server(
                server.ServeOptions(
                    policy=policy,
                    fps=fps,
                    cameras=cameras,
                    latency_s=latency_s,
                    port=0,
                    threads=threads,
                    jpeg_quality=jpeg_quality,
                    pins=tuple(pins or ()),
                ),
                token=secrets.token_hex(32),
            )
            url, token = served.url, served.token
            runner = RemoteRunner(url, token=token, motors=JOINTS)
        elif reached := reach(
            str(policy_url), policy_token=policy_token, registry_dir=registry_dir
        ):
            # an endpoint this machine started: its record's address and token, and the
            # account's token as the bearer for Hugging Face's proxy
            url = reached.url
            runner = RemoteRunner(
                url,
                token=reached.token,
                motors=JOINTS,
                bearer=reached.bearer,
                endpoint=reached.endpoint,
            )
        else:
            url = str(policy_url)
            policy_address(url)  # a URL that will be refused is said before a missing token
            runner = RemoteRunner(url, token=client_token(policy_token), motors=JOINTS)
        info = runner.policy()
        where = f"served here for the check, at {url}" if served is not None else runner.url
        if runner.endpoint is not None:
            where = f"{runner.endpoint} at {runner.url}"
        ui.console.print(Text(f"  {where}", style=ui.STYLES["muted"]), soft_wrap=True)
        ui.console.print(ui.kv_grid(server.describe(info)), soft_wrap=True)
        if bench:
            result = server.bench(
                runner,
                seconds=server.BENCH_S if seconds is None else seconds,
                async_chunks=async_chunks,
            )
            ui.console.print(ui.kv_grid(server.describe_bench(result)), soft_wrap=True)
    except (server.ServeRefused, PolicyServerError, ValueError) as e:
        _fail(str(e))
    finally:
        if runner is not None:
            runner.close()
        if served is not None:
            served.close()


# ── policy endpoint (the policy server on a GPU rented from Hugging Face) ───────────────

endpoint_app = typer.Typer(
    name="endpoint",
    help="The policy server on a Hugging Face Inference Endpoint, a GPU rented by the hour, "
    "which quackd starts for you: up starts or wakes one and waits until it serves, list says "
    "what this machine started, pause stops its bill, and delete removes it. A run reaches one "
    "with --policy-url hf:NAME. Your Hugging Face token is HF_TOKEN, or hf auth login's.",
    no_args_is_help=True,
)
policy_app.add_typer(endpoint_app, name="endpoint")

_ENDPOINT_DEFAULTS = {"instance": "nvidia-l4", "vendor": "aws", "region": "us-east-1"}
"""Where `up` rents its GPU unless told: an L4 with 24 GB in AWS's us-east-1, Rok's choice of
2026-10-25, at the price `/v2/provider` gives it on the day."""
_IDLE_MINUTES = 15
_WAIT_MINUTES = 30.0
_ENDPOINT_NAME = typer.Argument(
    ...,
    metavar="NAME",
    help="The endpoint's name, as Hugging Face names one: lower-case letters, digits and "
    "hyphens, 32 at most. A run reaches it as --policy-url hf:NAME.",
)
_ROW_KEY = 7
"""How wide `up`'s row keys are padded, the longest of them, so rows printed one at a time as
they happen line up as one grid would."""


def _endpoints() -> Any:
    """`quackd_lerobot.policy.endpoint`, imported only when an endpoint command runs, or a
    failure that names the extra that installs it."""
    try:
        from quackd_lerobot.policy import endpoint
    except ImportError:
        from quackd.adapters.base import AdapterNotInstalled

        _fail(str(AdapterNotInstalled("lerobot", "quackd[lerobot]")))
    return endpoint


def _endpoint_row(key: str, value: str) -> None:
    ui.console.print(ui.kv_grid([(key.ljust(_ROW_KEY), value)]), soft_wrap=True)


def _endpoint_note(text: str) -> None:
    ui.console.print(Text(f"  {text}", style=ui.STYLES["muted"]), soft_wrap=True)


def _home_relative(path: Path) -> str:
    """`path` as `~/...` when it is under the home directory, which is how the pages name the
    files quackd keeps there, and whole otherwise."""
    try:
        return "~/" + path.resolve().relative_to(Path.home().resolve()).as_posix()
    except (ValueError, OSError, RuntimeError):
        return str(path)


def _since(seconds: float) -> str:
    whole = max(0, round(seconds))
    if whole < 60:
        return f"{whole} s"
    return f"{whole // 60} min {whole % 60} s"


def _endpoint_client(ep: Any) -> Any:
    """The account's endpoint client, or a failure saying where to put the token."""
    try:
        found = ep.hf_token()
    except ValueError as e:
        _fail(str(e))
    if found is None:
        _fail(str(ep.no_token()))
    return ep.HFEndpoints(found[0])


def _ask_or_refuse(question: str, yes: bool) -> None:
    """`question` put to the person at the terminal, unless `--yes` answered it, and a refusal
    before anything is started where there is nobody to ask: an endpoint bills by the hour."""
    if yes:
        return
    if not _can_prompt():
        _fail(
            "quackd policy endpoint up starts a GPU that bills by the hour, and there is no "
            "terminal to ask before it does: run it at one, or pass --yes",
        )
    with ui.pause_status():
        if not typer.confirm(question, default=False):
            raise typer.Exit(code=1)


def _changed_flags(given: dict[str, tuple[Any, Any]]) -> list[str]:
    """The flags given to `up` for an endpoint that exists which say other than what it was
    created with, as (given, recorded) by flag, None where a flag was not given. There is no
    update: an endpoint serves what it was started with."""
    return [flag for flag, (now, then) in given.items() if now is not None and now != then]


def _paused_and_quoted(ep: Any, api: Any, record: Any, why: str) -> None:
    """Pause an endpoint that cannot serve, quote the last of its log, and fail saying why and
    how its bill stands (`endpoint.pause_and_quote`)."""
    lines, bill = ep.pause_and_quote(api, record)
    if lines:
        _endpoint_note("its log ended:")
        for line in lines:
            _endpoint_note(f"  {line}")
    above = " Its log is above." if lines else ""
    _fail(
        f"{why.rstrip('.')}, {bill}.{above} quackd policy endpoint up {record.name} tries "
        f"again, and delete {record.name} removes it"
    )


def _wait_and_say(ep: Any, api: Any, store: Any, record: Any, minutes: float) -> None:
    """Wait for `record`'s endpoint to serve, saying each state it passes as it does, then ask
    its server what it serves and say where it is and how a run reaches it. An endpoint that
    fails, that does not serve in time, whose server has no GPU, or that the wait stops on for
    any other reason, Hugging Face's answer, the record's write or the server's refusal, is
    paused, which stops its bill, and its log quoted (`endpoint.pause_and_quote`): one left
    starting would bill until it idled to zero. Ctrl-C leaves it, and says how to pause it, and
    an endpoint by that name that turns out not to be the one recorded (`endpoint.Unconfirmed`)
    is left as it is, since it is not quackd's to pause."""
    from quackd_lerobot.policy.client import PolicyServerError, RemoteRunner
    from quackd_lerobot.verbs import JOINTS

    def said(state: str, seconds: float) -> None:
        _endpoint_row("state", f"{state} at {_since(seconds)}")

    try:
        record = ep.wait_until_serving(api, record, minutes=minutes, store=store, said=said)
    except KeyboardInterrupt:
        _fail(
            f"stopped waiting, and {record.endpoint} goes on starting: it scales to zero after "
            f"{record.idle_minutes} minutes with no request, and quackd policy endpoint pause "
            f"{record.name} stops its bill now",
            code=130,
        )
    except ep.Unconfirmed as e:  # another endpoint by that name: nothing is sent or changed
        _fail(str(e))
    except ep.EndpointError as e:  # failed, timed out, or anything else on the way
        _paused_and_quoted(ep, api, record, str(e))
    _endpoint_row("url", str(record.url))
    _endpoint_row("token", f"kept in {_home_relative(store.path)}, for {record.endpoint}")
    runner: Any = None
    try:
        runner = RemoteRunner(
            record.url,
            token=record.token,
            bearer=api.token,
            endpoint=record.endpoint,
            motors=JOINTS,
        )
        info = runner.policy()
    except (PolicyServerError, ValueError) as e:
        _paused_and_quoted(ep, api, record, str(e))
    finally:
        if runner is not None:
            runner.close()
    gpu = "gpu yes" if info.gpu else "gpu no"
    _endpoint_row(
        "serving",
        f"{info.policy} ({info.protocol} {info.protocol_version}, quackd {info.server_version}), "
        f"{gpu}",
    )
    if not info.gpu:
        _paused_and_quoted(
            ep,
            api,
            record,
            f"{record.endpoint} serves with no GPU: its torch found none it could use, which a "
            "CUDA build newer than the machine's driver would do",
        )
    _endpoint_note(
        f"Run with --policy-url {record.endpoint}. quackd policy endpoint pause {record.name} "
        "stops the bill."
    )


@endpoint_app.command("up")
def endpoint_up(
    name: str = _ENDPOINT_NAME,
    policy: str | None = typer.Option(
        None,
        "--policy",
        help="REPO@REVISION of the LeRobot checkpoint to serve, needed the first time and "
        "refused after if it differs, since an endpoint serves what it was started with. A "
        "scripted policy needs no GPU: serve that on the laptop.",
    ),
    pins: list[str] | None = _POLICY_PINS,
    fps: float | None = _POLICY_FPS,
    cameras: str | None = _POLICY_CAMERAS,
    latency_s: float | None = _POLICY_LATENCY,
    threads: int | None = _POLICY_THREADS,
    jpeg_quality: int | None = _POLICY_JPEG,
    instance: str | None = typer.Option(
        None,
        "--instance",
        help="The GPU, by Hugging Face's instance type, one of it: nvidia-l4 unless told, and "
        "nvidia-t4 is the cheapest.",
    ),
    vendor: str | None = typer.Option(None, "--vendor", help="The cloud: aws unless told."),
    region: str | None = typer.Option(
        None,
        "--region",
        help="Its region: us-east-1 unless told. In Europe, eu-west-1 with --instance nvidia-a10g.",
    ),
    namespace: str | None = typer.Option(
        None,
        "--namespace",
        help="The account or organisation the endpoint goes on, and bills. Without it, the "
        "account your token is.",
    ),
    idle_minutes: int | None = typer.Option(
        None,
        "--idle-minutes",
        help="How long it waits with no request before it scales to zero and bills nothing, 15 "
        "to 720 (15 unless told). Never none: a GPU that bills until somebody remembers it is "
        "not one quackd starts.",
    ),
    hub_token_from: str | None = typer.Option(
        None,
        "--hub-token-from",
        help="The name of a variable holding a Hub read token for a gated or private checkpoint "
        "or --pin, which the endpoint fetches it with. Never your account's own token.",
    ),
    wait_minutes: float = typer.Option(
        _WAIT_MINUTES,
        "--wait-minutes",
        help="How long to wait for it to serve before it is paused (30 unless told).",
    ),
    no_wait: bool = typer.Option(
        False, "--no-wait", help="Start or wake it and return, without waiting for it to serve."
    ),
    yes: bool = typer.Option(
        False, "--yes", help="Start or resume it without asking. With no terminal, it is needed."
    ),
    registry_dir: str | None = _REGISTRY_DIR,
) -> None:
    """Start a policy server on a Hugging Face Endpoint, or wake one, and wait until it serves."""
    ep = _endpoints()
    server = _policy_server()
    if not ep.NAME.match(name):
        _fail(
            f"{name[:40]!r} is not an endpoint's name: lower-case letters, digits and hyphens, "
            "32 at most"
        )
    idle = _IDLE_MINUTES if idle_minutes is None else idle_minutes
    least, most = ep.up.IDLE_MINUTES
    if not least <= idle <= most:
        _fail(
            f"--idle-minutes {idle} is outside {least} to {most}: an endpoint scales to zero, "
            "and bills nothing, after that long with no request, and quackd starts none that "
            "never does"
        )
    if not (wait_minutes > 0 and wait_minutes < float("inf")):
        _fail(f"--wait-minutes {wait_minutes!r} is not a number of minutes to wait")
    api = _endpoint_client(ep)
    store = ep.EndpointStore(registry_dir)
    try:
        record = store.get(name)
        flags = ep.serve_flags(
            fps=fps,
            cameras=cameras,
            latency_s=latency_s,
            threads=threads,
            jpeg_quality=jpeg_quality,
        )
        if record is None:
            record = _endpoint_create(
                ep,
                server,
                api,
                store,
                name=name,
                policy=policy,
                pins=tuple(pins or ()),
                flags=flags,
                options={
                    "fps": fps,
                    "cameras": cameras,
                    "latency_s": latency_s,
                    "threads": threads,
                    "jpeg_quality": jpeg_quality,
                },
                where={
                    "instance": instance or _ENDPOINT_DEFAULTS["instance"],
                    "vendor": vendor or _ENDPOINT_DEFAULTS["vendor"],
                    "region": region or _ENDPOINT_DEFAULTS["region"],
                },
                namespace=namespace,
                idle=idle,
                hub_token_from=hub_token_from,
                yes=yes,
            )
        else:
            served = dict(zip(record.flags[::2], record.flags[1::2], strict=True))
            typed = dict(zip(flags[::2], flags[1::2], strict=True))
            given: dict[str, tuple[Any, Any]] = {
                "--policy": (policy.strip() if policy else None, record.policy),
                "--pin": (tuple(pins) if pins else None, record.pins),
                **{flag: (value, served.get(flag)) for flag, value in typed.items()},
                "--instance": (instance, record.instance),
                "--vendor": (vendor, record.vendor),
                "--region": (region, record.region),
                "--namespace": (namespace, record.namespace),
                "--idle-minutes": (idle_minutes, record.idle_minutes),
                "--hub-token-from": (hub_token_from, record.hub_token_from),
            }
            if changed := _changed_flags(given):
                _fail(
                    f"{record.endpoint} was started serving {record.policy} on "
                    f"{record.instance} in {record.vendor} {record.region}, and an endpoint "
                    f"serves what it was started with: {', '.join(changed)} would change that. "
                    f"Give up {name} none of them, or delete {name} and start it afresh"
                )
            record = _endpoint_resume(ep, api, store, record, yes=yes)
        if no_wait:
            _endpoint_note(
                f"Not waiting: quackd policy endpoint up {name} waits until it serves, and a run "
                "that reaches it before then is told it is not serving yet."
            )
            return
        _wait_and_say(ep, api, store, record, wait_minutes)
    except ValueError as e:  # an EndpointError, or a token no header could carry
        _fail(str(e))


def _endpoint_create(
    ep: Any,
    server: Any,
    api: Any,
    store: Any,
    *,
    name: str,
    policy: str | None,
    pins: tuple[str, ...],
    flags: tuple[str, ...],
    options: dict[str, Any],
    where: dict[str, str],
    namespace: str | None,
    idle: int,
    hub_token_from: str | None,
    yes: bool,
) -> Any:
    """Check everything that can be checked before an endpoint exists to bill, ask, and create
    it. The record is kept before the endpoint is created, so one that exists is never one this
    machine has no token for, forgotten again if the create is refused, and kept when the
    answer is lost and the endpoint is there all the same (`endpoint.create_endpoint`)."""
    if policy is None:
        _fail(
            f"--policy REPO@REVISION is needed the first time {name} is started: the LeRobot "
            "checkpoint it serves, at a commit or a tag"
        )
        return None
    if policy.strip().startswith("scripted:"):
        _fail(
            f"{policy.strip()} needs no GPU: serve it on the laptop with quackd policy serve "
            f"--policy {policy.strip()}"
        )
    try:
        checked = server.check_options(
            _serve_options(
                policy=policy,
                bind="0.0.0.0",
                token_env=ep.TOKEN_SECRET,
                behind_tls=True,
                pins=pins,
                **options,
            )
        )
    except server.ServeRefused as e:
        _fail(str(e))
    hub = None if hub_token_from is None else ep.hub_token_from(hub_token_from, api.token)
    api.also_scrub(hub)
    with ui.spinner("asking Hugging Face"):
        check = ep.preflight(
            api,
            policy=checked.spec,
            pins=pins,
            namespace=namespace,
            hub_token=hub,
            **where,
        )
        taken = api.get(check.namespace, name) is not None
    if taken:
        _fail(
            f"{check.namespace} already has an endpoint called {name}, which this machine did "
            "not start: give yours another name"
        )
    record = ep.new_record(
        name,
        namespace=check.namespace,
        policy=checked.spec,
        idle_minutes=idle,
        pins=pins,
        flags=flags,
        hub_token_from=hub_token_from,
        **where,
    )
    _ask_or_refuse(ep.confirmation(record, check), yes)
    payload = ep.create_payload(
        record,
        accelerator=check.compute.accelerator,
        exclude_newer=check.exclude_newer,
        hub_token=hub,
    )
    lost = ep.create_endpoint(api, store, record, payload)
    if lost is not None:
        _endpoint_note(f"Hugging Face's answer was lost ({lost}), and it has {name} all the same.")
    _endpoint_row(
        "created",
        f"{name} on {check.namespace}'s account, {record.instance} x1 in {record.vendor} "
        f"{record.region}, ${check.compute.price_per_hour:.2f} an hour while it runs",
    )
    return record


def _endpoint_resume(ep: Any, api: Any, store: Any, record: Any, *, yes: bool) -> Any:
    """Wake an endpoint this machine started: resume one that is paused, or wake one that
    scaled to zero with a request to its health route at its address, recorded first when the
    record holds none (`endpoint.hold_address`), each after asking, since it bills again, and
    leave one that is starting or running to the wait. One that failed is said with its log and
    its message, every token taken out, and not waited for. A resume whose answer is lost, with
    no answer or a 5xx, may have taken all the same, so the endpoint is paused again rather than
    left starting. It is read first and held to the record (`endpoint.confirm`), so one by that
    name that this machine's `up` did not start is left as it is. The record comes back, with
    any address it gained."""
    # the Hub token a gated checkpoint is fetched with sits in the endpoint's secrets too,
    # and a failed container's log may show it: scrubbed when this shell still holds it
    hub = os.environ.get(record.hub_token_from) if record.hub_token_from else None
    api.also_scrub(record.token, hub.strip() if hub else None)
    found = ep.confirm(api, record)
    if found is None:
        _fail(
            f"Hugging Face has no endpoint called {record.name} on {record.namespace} any "
            f"more: quackd policy endpoint delete {record.name} forgets it here, and up then "
            "starts it afresh"
        )
    state, url, message = ep.state_of(found)
    if state in ep.FAILED:
        for line in api.logs(record.namespace, record.name):
            _endpoint_note(f"  {line}")
        why = api.printable(message)
        _fail(
            f"{record.endpoint} is {state}{': ' + why if why else ''}. quackd policy endpoint "
            f"delete {record.name}, then up, starts it afresh"
        )
    if state in (ep.PAUSED, ep.ASLEEP):
        compute = ep.find_compute(api.provider(), record.vendor, record.region, record.instance)
        doing = "resume" if state == ep.PAUSED else "wake"
        _ask_or_refuse(ep.confirmation(record, compute, doing=doing), yes)
        if state == ep.PAUSED:
            try:
                api.resume(record.namespace, record.name)
            except ep.EndpointError as e:
                if e.status is not None and 400 <= e.status < 500:
                    raise  # refused, so nothing was resumed
                # no answer, or a 5xx, may come after Hugging Face resumed it all the same
                why = f"{str(e).rstrip('.')}, and it may have resumed all the same"
                _paused_and_quoted(ep, api, record, why)
        elif url is not None:
            record = ep.hold_address(store, record, url)
            api.health(url)  # a request wakes it, and --no-wait sends no other
        else:
            _endpoint_note(f"Hugging Face gives {record.name} no address yet, so nothing woke it.")
            return record
        _endpoint_row(
            "resumed" if state == ep.PAUSED else "waking",
            f"{record.name}, ${compute.price_per_hour:.2f} an hour while it runs",
        )
    return record


@endpoint_app.command("list")
def endpoint_list(as_json: bool = _JSON, registry_dir: str | None = _REGISTRY_DIR) -> None:
    """The endpoints this machine started, and how each is now."""
    ep = _endpoints()
    store = ep.EndpointStore(registry_dir)
    try:
        records = store.read()
        found = ep.hf_token()
    except ValueError as e:
        _fail(str(e))
    live: dict[tuple[str, str], dict[str, Any]] = {}
    asked = False
    if found is not None:
        api = ep.HFEndpoints(found[0])
        try:
            spaces = {r.namespace for r in records.values()}
            spaces.add(str(api.whoami().get("name")))
            for space in sorted(spaces):
                for item in api.list_endpoints(space):
                    live[(space, str(item.get("name")))] = item
            asked = True
        except ep.EndpointError as e:
            _fail(str(e))
    rows: list[dict[str, Any]] = []
    for record in records.values():
        item = live.pop((record.namespace, record.name), None)
        state = ep.state_of(item)[0] if item is not None else "gone" if asked else "not asked"
        rows.append({**record.public(), "state": state, "here": True})
    for (space, item_name), item in sorted(live.items()):
        state, url, _ = ep.state_of(item)
        rows.append(
            {"name": item_name, "namespace": space, "state": state, "url": url, "here": False}
        )
    if as_json:
        for row in rows:
            print(json.dumps(row))
        return
    if not rows:
        ui.console.print(
            Text(
                "no endpoints: quackd policy endpoint up NAME --policy REPO@REVISION starts one",
                style=ui.STYLES["muted"],
            ),
            soft_wrap=True,
        )
        return
    table = ui.table("endpoints (--policy-url hf:NAME)")
    table.add_column("name", style=ui.STYLES["key"], no_wrap=True)
    table.add_column("state", no_wrap=True)
    table.add_column("where")
    table.add_column("serving", overflow="fold")
    for row in rows:
        where = (
            f"{row['instance']} in {row['vendor']} {row['region']}, {row['namespace']}"
            if row["here"]
            else f"{row['namespace']}, not started from this machine"
        )
        table.add_row(
            Text(str(row["name"])),
            Text(str(row["state"])),
            Text(where),
            Text(str(row.get("policy") or "")),
        )
    ui.console.print(table)
    if found is None:
        _endpoint_note(f"{ep.no_token()}, to say how each is now.")


def _endpoint_record(ep: Any, store: Any, name: str) -> Any:
    try:
        record = store.get(name)
    except ep.EndpointError as e:
        _fail(str(e))
    if record is None:
        _fail(
            f"no endpoint called {name} is recorded on this machine: quackd policy endpoint "
            "list says what there is"
        )
    return record


@endpoint_app.command("pause")
def endpoint_pause(name: str = _ENDPOINT_NAME, registry_dir: str | None = _REGISTRY_DIR) -> None:
    """Pause an endpoint this machine started, which stops its bill until up resumes it."""
    ep = _endpoints()
    store = ep.EndpointStore(registry_dir)
    record = _endpoint_record(ep, store, name)
    api = _endpoint_client(ep)
    api.also_scrub(record.token)
    gone = (
        f"Hugging Face has no endpoint called {name} on {record.namespace} any more: quackd "
        f"policy endpoint delete {name} forgets it here"
    )
    paused: dict[str, Any] | None = None
    try:
        # read first, so an endpoint by that name that quackd did not start is left as it is
        if ep.confirm(api, record) is not None:
            paused = api.pause(record.namespace, name)
    except ep.EndpointError as e:
        _fail(gone if e.status == 404 else str(e))
    if paused is None:
        _fail(gone)
        return
    state = ep.state_of(paused)[0]
    said = (
        "which bills nothing now" if state == ep.PAUSED else f"which Hugging Face says is {state}"
    )
    _endpoint_row("paused", f"{name}, {said}")
    _endpoint_note(f"quackd policy endpoint up {name} resumes it.")


@endpoint_app.command("delete")
def endpoint_delete(
    name: str = _ENDPOINT_NAME,
    yes: bool = typer.Option(False, "--yes", help="Delete it without asking."),
    forget: bool = typer.Option(
        False,
        "--forget",
        help="Only forget this machine's record of it, and leave Hugging Face as it is: for a "
        "record whose endpoint was deleted on huggingface.co, or that quackd did not start.",
    ),
    registry_dir: str | None = _REGISTRY_DIR,
) -> None:
    """Delete an endpoint this machine started, and forget it here."""
    ep = _endpoints()
    store = ep.EndpointStore(registry_dir)
    try:
        recorded, (namespace, marked) = store.recorded(name), store.located(name)
    except ep.EndpointError as e:
        _fail(str(e))
        return
    if not recorded:
        _fail(
            f"no endpoint called {name} is recorded on this machine: quackd policy endpoint "
            "list says what there is"
        )
    if namespace is not None and marked is None and not forget:
        # quackd's own tag is all there is to go by then, and another machine's up gives that
        # to its endpoints too
        _fail(
            f"this machine's record of hf:{name} holds no token quackd can read, so the endpoint "
            f"by that name on {namespace}'s account cannot be told from another machine's, and "
            "quackd does not delete it: delete it on huggingface.co if it is yours, and quackd "
            f"policy endpoint delete {name} --forget forgets the record"
        )
    doing = "forgetting" if forget else "deleting"
    if not yes and not _can_prompt():
        _fail(
            f"{doing} hf:{name} cannot be undone, and there is no terminal to ask before it is: "
            "run it at one, or pass --yes"
        )
    where = f"from {namespace}'s account" if namespace else "from this machine's record"
    api: Any = None
    if namespace is not None and not forget:
        # read first, so an endpoint by that name that quackd did not start is left as it is
        api = _endpoint_client(ep)
        try:
            found = api.get(namespace, name)
            if found is not None:
                ep.confirmed(found, namespace=namespace, name=name, mark=marked)
            else:
                api = None  # gone already: nothing to delete
        except ValueError as e:
            _fail(str(e))
    question = (
        f"forget hf:{name} here, and leave whatever Hugging Face has by that name as it is?"
        if forget
        else f"delete hf:{name} {where}, and forget it here?"
    )
    if not yes:
        with ui.pause_status():
            if not typer.confirm(question, default=False):
                raise typer.Exit(code=1)
    existed = False
    if api is not None:
        try:
            existed = api.delete(namespace, name)
        except ValueError as e:
            _fail(str(e))
    try:
        store.forget(name)
    except ep.EndpointError as e:
        _fail(str(e))
    if existed:
        ui.console.print(_ok_line(f"deleted {name} {where}"))
    elif namespace is not None and not forget:
        ui.console.print(_ok_line(f"Hugging Face had no {name} on {namespace} any more"))
    _endpoint_note(f"forgot {name} in {_home_relative(store.path)}.")


# ── robot (the registry) ────────────────────────────────────────────────────────────────

robot_app = typer.Typer(
    name="robot",
    help="The robots you have named: which body, where it is, and who pilots it. Kept in "
    "~/.quackd/robots.json so --robot NAME means the same thing in every command.",
    no_args_is_help=True,
)
app.add_typer(robot_app, name="robot", rich_help_panel="Robots")


def _registry(registry_dir: str | None) -> Any:
    from quackd.registry import Registry

    return Registry(registry_dir)


def _registry_fail(e: Exception) -> None:
    """Every registry refusal is one line. A robot that is not there is not a traceback."""
    from quackd.adapters.base import AdapterError

    _fail(str(e), hint=_ADAPTER_HINT if isinstance(e, AdapterError) else None)


def _can_prompt() -> bool:
    """Whether there is a person at a terminal to ask. The seam tests replace.

    `isatty` alone is not that on Windows, where it says yes to NUL, because NUL is a character
    device: a script or a scheduled task started with its input from NUL was asked every
    question, answered each with end-of-input, and had its record name a person who answered.
    Only a console has a console mode, so on Windows that is asked as well.

    Behind the page it is never one, whatever stdin is: the page's lines arrive there, and the
    page puts a run's questions itself (`_someone_to_ask`)."""
    if _FRONT is not None:
        return False
    return _is_a_console(sys.stdin)


def _can_draw_a_prompt() -> bool:
    """Whether the output is a terminal a prompt can be drawn on, as `quackd drive`'s keyboard
    draws one. prompt_toolkit cannot draw into a file or a pipe, and on Windows a session whose
    output was piped to `Tee-Object` connected the robot and died before its first line. The
    seam tests replace."""
    return _is_a_console(sys.stdout)


def _is_a_console(stream: Any) -> bool:
    if stream is None or not stream.isatty():
        return False
    if sys.platform == "win32":
        import ctypes
        import msvcrt

        try:
            handle = msvcrt.get_osfhandle(stream.fileno())
        except (OSError, ValueError):
            return False
        mode = ctypes.c_ulong()
        return bool(ctypes.windll.kernel32.GetConsoleMode(handle, ctypes.byref(mode)))
    return True


def _rest_pose_text(pose: dict[str, float]) -> Any:
    """A recorded pose on one line per joint, in the arm's own bus order where it has one.

    Alphabetical when the arm's package is not installed, because a pose is worth printing
    whether or not the adapter that recorded it is still here: `quackd robot show` is how you
    read a registry on a machine that cannot drive half of it."""
    joints: tuple[str, ...] = ()
    with contextlib.suppress(ImportError):
        from quackd_lerobot import JOINTS

        joints = JOINTS
    order = {joint: i for i, joint in enumerate(joints)}
    listed = sorted(pose.items(), key=lambda kv: (order.get(kv[0], len(order)), kv[0]))
    return Text("\n".join(f"{joint} {value:.1f}" for joint, value in listed))


def _entry_rows(entry: Any, *, flocks: list[str]) -> list[tuple[str, Any]]:
    from quackd.adapters.factory import describe

    body: Any
    try:
        manifest = describe(entry.robot_spec)
        body = Text(manifest.summary())
    except Exception as e:  # an adapter whose extra is missing still has a name
        body = Text(str(e), style=ui.STYLES["muted"])
    dash = Text("-", style=ui.STYLES["muted"])
    pilot = Text(entry.llm) if entry.llm else dash
    return [
        ("name", Text(entry.name, style=ui.STYLES["key"])),
        # marked as `robot list` marks it when this quackd cannot build it, and the body row
        # says why, in the words a run that names it is refused with
        ("robot", Text(entry.key, style=ui.STYLES["fail" if entry.spec_problem else "accent"])),
        ("body", body),
        ("address", Text(entry.address) if entry.address else dash),
        ("map address", Text(entry.map_address) if entry.map_address else dash),
        # one per line, because two urls on one line is where a reader stops being able to
        # tell which camera is the primary
        ("camera", Text("\n".join(entry.camera_urls)) if entry.camera_urls else dash),
        ("rest pose", _rest_pose_text(entry.rest_pose) if entry.rest_pose else dash),
        ("token", Text("set") if entry.token else dash),
        ("host", Text(entry.host) if entry.host else dash),
        ("host token", Text("set") if entry.host_token else dash),
        ("pilot", pilot),
        ("note", Text(entry.note) if entry.note else dash),
        ("flocks", Text(", ".join(flocks)) if flocks else dash),
        ("added", Text(entry.added, style=ui.STYLES["muted"])),
        ("updated", Text(entry.updated, style=ui.STYLES["muted"])),
    ]


_ROBOT_NAME = typer.Argument(
    ...,
    help="A slug: lowercase letters, digits and hyphens. Not a number (--flock N already "
    "means N simulated ducks) and not an adapter name.",
)
_PROBE = typer.Option(
    False,
    "--probe",
    help="Connect to each robot and say whether it answered. Costs a connection per robot.",
)
_PROBE_TIMEOUT = typer.Option(
    5.0, "--timeout", min=0.1, max=120.0, help="Seconds to wait per robot when probing."
)


_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
"""A variable's name, as `--token-env` and `--host-token-env` take one."""

_TOKEN_ENV = typer.Option(
    None,
    "--token-env",
    help="The name of a variable holding the bridge token, instead of --token: how a program, "
    "the page quackd ui among them, hands one over without putting it on a command line. Only "
    "the name is ever printed, never what it holds.",
)
_HOST_TOKEN_ENV = typer.Option(
    None,
    "--host-token-env",
    help="The name of a variable holding the board's token, instead of --host-token, as "
    "--token-env is for the robot's.",
)


def _token_from(flag: str, name: str | None, given: str | None, *, instead_of: str) -> str | None:
    """The token the variable `flag` names holds, or `given` where `flag` is not used.

    No refusal quotes what `flag` was given, nor what the variable holds: what was typed there
    may be the token itself, put in the wrong place."""
    if name is None:
        return given
    if given is not None:
        _fail(f"{instead_of} and {flag} both give the token: give it one way")
    if not _ENV_NAME.match(name):
        _fail(
            f"{flag} takes the name of the variable that holds the token, and what it was "
            "given is not a variable's name"
        )
    value = os.environ.get(name)
    if value is None or not value.strip():
        _fail(f"the variable {flag} names {'is not set' if value is None else 'is empty'}")
        return None
    return value.strip()


@robot_app.command("add")
def robot_add(
    name: str = _ROBOT_NAME,
    spec: str = typer.Argument(
        ..., help="<adapter>[:<backend>], e.g. microduck:sim2d. See `quackd list-adapters`."
    ),
    address: str | None = _ADDR,
    map_address: str | None = _MAP_ADDR,
    camera_url: list[str] = _CAMERA_URL,
    token: str | None = _TOKEN,
    token_env: str | None = _TOKEN_ENV,
    host: str | None = _ROBOT_HOST,
    host_token: str | None = _ROBOT_HOST_TOKEN,
    host_token_env: str | None = _HOST_TOKEN_ENV,
    llm: str | None = typer.Option(
        None,
        "--llm",
        "-l",
        help="The pilot a run uses for this robot when --llm is absent, as VENDOR[:MODEL]. "
        "--llm on the run beats this, and this beats QUACKD_LLM.",
        autocompletion=_complete_llm,
        rich_help_panel="Model",
    ),
    note: str | None = typer.Option(None, "--note", help="One line for people: which one is it."),
    registry_dir: str | None = _REGISTRY_DIR,
) -> None:
    """Register a robot under a name, with how to reach it."""
    from quackd.adapters.base import AdapterError
    from quackd.agent.providers.base import ProviderError
    from quackd.agent.providers.factory import parse_llm
    from quackd.host import parse_host
    from quackd.registry import RegistryError, RobotEntry

    token = _token_from("--token-env", token_env, token, instead_of="--token")
    host_token = _token_from(
        "--host-token-env", host_token_env, host_token, instead_of="--host-token"
    )
    try:
        # Checked against the catalogue here and nowhere else. The shelf is deliberately
        # lenient -- an id a later catalogue retires must not make every `quackd robot`
        # command refuse, including the edit that would fix it -- so the door is the one
        # place a typo can still be caught while the person who made it is looking at it.
        if llm is not None:
            parse_llm(llm, source="--llm")
        # The board gets the same gate, and needs it here as well as in `add_robot`: the entry
        # reads a blank host as none, so `--host ""` would otherwise register a robot with no
        # board and report success.
        if host is not None:
            parse_host(host)
        entry = RobotEntry(
            name=name,
            spec=spec,
            address=address,
            map_address=map_address,
            camera_url=list(camera_url) or None,
            token=token,
            host=host,
            host_token=host_token,
            llm=llm,
            note=note,
        )
        _registry(registry_dir).add_robot(entry)
    except (RegistryError, AdapterError, ValueError, ProviderError) as e:
        # ValueError is parse_host's, and is also what pydantic's ValidationError is
        _registry_fail(_one_line(e))
        return
    where = f" at {entry.address}" if entry.address else ""
    board = f", host {entry.host}" if entry.host else ""
    ui.console.print(_ok_line(f"added {entry.name}: {entry.key}{where}{board}"))
    ui.console.print(Text(f"  quackd run <duck> --robot {entry.name}", style=ui.STYLES["muted"]))


def _one_line(e: Exception) -> Exception:
    """A pydantic error folded to the one sentence a person needs, keeping its own words."""
    if isinstance(e, ValidationError):
        return ValueError(
            "; ".join(
                str(err["msg"]).removeprefix("Value error, ")
                for err in e.errors()  # type: ignore[attr-defined]
            )
        )
    return e


_TWIN_SPEC = "lerobot:mujoco"
"""What `quackd robot twin` registers: the arm's simulator, the real backend's own code over a
physics model of the arm, which reads the arm's travel off the calibration file it is given."""


def _twin_address(entry: Any, address: str | None) -> str | None:
    """The calibration file a twin of this arm is told to read, as it was given: `--address`,
    else the file a simulator given as the source already reads, else None for the one LeRobot
    keeps under the arm's registered name."""
    if address:
        return address
    if entry.key == _TWIN_SPEC and entry.address:
        return str(entry.address)
    return None


def _twin_calibration(given: str | None, name: str) -> Path:
    """The calibration file a twin reads, as an absolute path so the twin reads it from any
    directory: the one given (`_twin_address`), else the one LeRobot keeps under the arm's
    registered name, found as the simulator finds it
    (`quackd_lerobot.sim.model.calibration_path`). Never called on an address shaped like a
    port, which resolving would already reach for."""
    if given:
        return Path(given).expanduser().resolve()
    from quackd_lerobot.sim.model import calibration_path

    return calibration_path(name).resolve()


def _twin_cameras(urls: Sequence[str]) -> tuple[list[str], list[tuple[str, str]]]:
    """The source's camera urls a simulator renders, and each one it does not, with why. The
    simulator renders only the views its scene mounts (`quackd_lerobot.sim.model.MOUNTS`), and
    a twin that stored another would be refused by every run and preflight on it, naming a
    --camera-url nobody typed."""
    from quackd.transport.base import TransportError
    from quackd_lerobot.real import parse_camera_url
    from quackd_lerobot.sim.model import MOUNTS

    kept: list[str] = []
    left: list[tuple[str, str]] = []
    for url in urls:
        try:
            view = parse_camera_url(url, label="mujoco").name
        except (TransportError, ValueError) as e:
            left.append((url, " ".join(str(e).split()).rstrip(".")))
            continue
        if view in MOUNTS:
            kept.append(url)
        else:
            left.append((url, f"it is named {view}, and the simulator renders only {_and(MOUNTS)}"))
    return kept, left


def _and(items: Sequence[str]) -> str:
    """`a`, `a and b`, `a, b and c`."""
    return items[0] if len(items) == 1 else f"{', '.join(items[:-1])} and {items[-1]}"


@robot_app.command("twin")
def robot_twin(
    source: str = typer.Argument(..., help="The registered LeRobot arm to make a simulator of."),
    name: str | None = typer.Argument(
        None, help="What to register the simulator as. Default: SOURCE-sim."
    ),
    address: str | None = typer.Option(
        None,
        "--address",
        help="The calibration file to give it, where it is not the one LeRobot keeps under "
        "SOURCE's name.",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        help="Replace NAME where it is already a lerobot:mujoco robot. Anything else "
        "registered under NAME is never replaced.",
    ),
    registry_dir: str | None = _REGISTRY_DIR,
) -> None:
    """Register a simulator of a registered arm, on its calibration, to rehearse its tasks on."""
    from quackd.adapters.base import AdapterError, AdapterNotInstalled
    from quackd.agent.providers.base import ProviderError
    from quackd.registry import RegistryError, RobotEntry

    target = name or f"{source}-sim"
    if target == source:
        _fail(
            f"{source} cannot be its own twin: the simulator is registered beside the arm it "
            "copies, never over it",
            hint=f"leave NAME out for {source}-sim, or name another",
        )
        return
    try:
        registry = _registry(registry_dir)
        entry = registry.get_robot(source)
        existing = registry.get_robot(target)
    except RegistryError as e:
        _registry_fail(e)
        return
    if entry is None:
        _fail(
            f"no robot called {source!r} is registered, so there is no arm to twin",
            hint="quackd robot list",
        )
        return
    if entry.adapter != "lerobot":
        _fail(
            f"{source} is {entry.key}, and only a LeRobot arm has a simulator to be twinned on",
            hint="quackd robot twin takes a robot registered as lerobot:real",
        )
        return
    if existing is not None and force and existing.key != _TWIN_SPEC:
        _fail(
            f"{target} is registered as {existing.key}, and --force replaces only a "
            f"{_TWIN_SPEC} robot",
            hint=f"quackd robot remove {target} first, if you mean to lose it",
        )
        return
    given = _twin_address(entry, address)
    try:
        from quackd_lerobot.sim.model import names_a_port

        if given and names_a_port(given):
            # for every other lerobot robot the address is the port, so this is an easy slip,
            # and it is refused on its shape before anything opens or even looks for the file
            whose = "--address" if address else f"{source}'s own address"
            _fail(
                f"{whose} {given} is a serial port, and a twin reads the arm's calibration "
                "file, never its port, so nothing was opened there",
                hint="give --address PATH the file lerobot-calibrate wrote for the arm",
            )
            return
        calibration = _twin_calibration(given, entry.name)
        cameras, left_out = _twin_cameras(entry.camera_urls)
    except ImportError:
        _fail(str(AdapterNotInstalled("lerobot", "quackd[lerobot-sim]")))
        return
    if not calibration.is_file():
        where = (
            ("--address names" if address else "its own address names")
            if given
            else "LeRobot would keep one for it at"
        )
        _fail(
            f"{source} has no calibration file for its twin to read: {where} {calibration}, "
            "and nothing is there",
            hint="give --address PATH the file lerobot-calibrate wrote for the arm",
        )
        return
    fields: dict[str, Any] = {
        "spec": _TWIN_SPEC,
        "address": str(calibration),
        "camera_url": cameras or None,
        "rest_pose": dict(entry.rest_pose) if entry.rest_pose else None,
        "llm": entry.llm,
    }
    try:
        if existing is not None and force:
            # every field, the ones the source leaves empty included, so what was under the
            # name before is gone rather than half kept
            registry.update_robot(
                target,
                {**fields, "token": None, "host": None, "host_token": None, "note": None},
            )
        else:
            registry.add_robot(RobotEntry(name=target, **fields))
    except (RegistryError, AdapterError, ValueError, ProviderError) as e:
        _registry_fail(_one_line(e))
        return
    kept = (
        ("rest pose", "rest pose", entry.rest_pose),
        ("pilot", f"pilot {entry.llm}", entry.llm),
        ("camera", _plural(len(cameras), "camera url"), cameras),
    )
    copied = [said for _, said, present in kept if present]
    # a source whose every camera was left out had cameras, and the lines below say why
    missing = [
        what for what, _, present in kept if not present and not (what == "camera" and left_out)
    ]
    replaced = existing is not None and force
    ui.console.print(
        _ok_line(
            f"{'replaced' if replaced else 'added'} {target}: {_TWIN_SPEC}, a simulator of "
            f"{source} on {calibration}"
        )
    )
    said = []
    if copied:
        said.append(f"copied from {source}: {', '.join(copied)}")
    if missing:
        said.append(f"{source} has no {' or '.join(missing)} to copy")
    for line in said:
        ui.console.print(Text(f"  {line}", style=ui.STYLES["muted"]), soft_wrap=True)
    for url, why in left_out:
        ui.console.print(
            _warn_line(
                f"{source}'s camera {url} was not copied: {why}. quackd robot edit {target} "
                "--camera-url sets the twin's cameras"
            ),
            soft_wrap=True,
        )
    # every simulator in the file, not only this one: one is enough for 0.14 to refuse it
    twins = [target]
    with contextlib.suppress(RegistryError):
        twins = sorted(n for n, e in registry.robots().items() if e.key == _TWIN_SPEC) or twins
    holds, remove = (
        (f"a {_TWIN_SPEC} robot", f"quackd robot remove {twins[0]}")
        if len(twins) == 1
        else (
            f"{len(twins)} {_TWIN_SPEC} robots, {_and(twins)}",
            "quackd robot remove each of them",
        )
    )
    ui.console.print(
        _warn_line(
            f"robots.json now holds {holds}, and quackd 0.14 and earlier cannot read the file "
            f"at all: {remove} before going back to one"
        ),
        soft_wrap=True,
    )
    ui.console.print(
        Text(
            f"  quackd preflight <duck> --robot {target}"
            + ("" if entry.llm else " --llm VENDOR[:MODEL]"),
            style=ui.STYLES["muted"],
        )
    )


@robot_app.command("list")
def robot_list(
    probe: bool = _PROBE,
    timeout: float = _PROBE_TIMEOUT,
    registry_dir: str | None = _REGISTRY_DIR,
    as_json: bool = _JSON,
) -> None:
    """Every registered robot. Static by default: --probe connects to each of them."""
    from quackd.registry import RegistryError, probe_all, unusable_spec

    try:
        registry = _registry(registry_dir)
        entries = registry.robots()
        holders = {name: registry.flocks_of(name) for name in entries}
    except RegistryError as e:
        _registry_fail(e)
        return
    probes: dict[str, Any] = {}
    if probe and entries:
        with ui.spinner(f"probing {_plural(len(entries), 'robot')} ({timeout:g} s each)"):
            probes = probe_all(
                entries.values(),
                timeout_s=timeout,
                # a probe connects, so a robot on a backend its adapter is retiring is said
                # just before its own connect, on stderr, once however many are on it
                before_connect=lambda entry: _deprecated_backends([entry.robot_spec]),
            )
    if as_json:
        for name, entry in entries.items():
            payload: dict[str, Any] = {**entry.public(), "flocks": holders[name]}
            if probe:
                result = probes.get(name)
                payload["reachable"] = result.reachable if result else None
                payload["probe"] = result.detail if result else ""
            print(json.dumps(payload, ensure_ascii=False))
        _exit_on_unreachable(probes)
        return
    if not entries:
        ui.console.print(
            Text(
                "no robots registered yet: quackd robot add NAME <adapter>:<backend>",
                style=ui.STYLES["muted"],
            )
        )
        return
    # a column nobody has filled is a column that only makes the rest narrower, and on an
    # 80-column terminal a probe's refusal needs every character it can get
    cells: dict[str, list[Any]] = {
        "address": [Text(e.address or "") for e in entries.values()],
        "host": [Text(e.host or "") for e in entries.values()],
        "pilot": [Text(e.llm or "") for e in entries.values()],
        "flocks": [Text(", ".join(holders[n])) for n in entries],
        "note": [Text(e.note or "") for e in entries.values()],
    }
    if probe:
        cells["reachable"] = [_probe_cell(probes.get(n)) for n in entries]
    shown = [
        name for name, column in cells.items() if name == "reachable" or any(str(c) for c in column)
    ]
    table = ui.table("robots (--robot NAME)")
    table.add_column("name", no_wrap=True, style=ui.STYLES["key"])
    table.add_column("robot", no_wrap=True)
    for name in shown:
        table.add_column(name, overflow="fold", ratio=1 if name in ("note", "reachable") else None)
    problems = {name: why for name, e in entries.items() if (why := e.spec_problem) is not None}
    for i, (name, entry) in enumerate(entries.items()):
        table.add_row(
            Text(name),
            Text(entry.key, style=ui.STYLES["fail" if name in problems else "accent"]),
            *(cells[column][i] for column in shown),
        )
    ui.console.print(table)
    # Listed rather than refused: a robot this quackd cannot build used to make the whole file
    # refuse, this command and the `edit` and `remove` that mend it among them.
    for name, why in problems.items():
        ui.console.print(_warn_line(unusable_spec(name, entries[name].spec, why)))
    _exit_on_unreachable(probes)


PROBE_DETAIL_CHARS = 48
"""A refusal from a socket can be a paragraph (Windows spells one in about 120 characters),
and a column that wide turns the table into a page. `quackd robot show` and `--json` carry
the whole of it; this says which robot to go and look at."""


def _probe_cell(result: Any) -> Any:
    if result is None:
        return Text("-", style=ui.STYLES["muted"])
    detail = " ".join(str(result.detail).split())
    if len(detail) > PROBE_DETAIL_CHARS:
        detail = detail[: PROBE_DETAIL_CHARS - 3].rstrip() + "..."
    if result.reachable is None:
        return Text(detail, style=ui.STYLES["muted"])
    style = ui.STYLES["ok"] if result.reachable else ui.STYLES["fail"]

    def build(g: ui.Glyphs) -> Any:
        mark = g.ok if result.reachable else g.fail
        return Text(f"{mark} {detail}", style=style)

    return ui.Deferred(build)


def _exit_on_unreachable(probes: dict[str, Any]) -> None:
    """A probe that found a robot down is a failing command, so a script can branch on it."""
    down = [name for name, result in probes.items() if result.reachable is False]
    if down:
        raise typer.Exit(code=1)


@robot_app.command("show")
def robot_show(
    name: str = _ROBOT_NAME,
    registry_dir: str | None = _REGISTRY_DIR,
    as_json: bool = _JSON,
) -> None:
    """Everything one registered robot says about itself, and what it remembers."""
    from quackd.memory import RobotMemory
    from quackd.registry import RegistryError

    try:
        registry = _registry(registry_dir)
        entry = registry.robot(name)
        flocks = registry.flocks_of(name)
    except RegistryError as e:
        _fail(str(e), hint="quackd robot list")
        return
    if as_json:
        print(json.dumps({**entry.public(), "flocks": flocks}, ensure_ascii=False))
        return
    rows = _entry_rows(entry, flocks=flocks)
    memory = RobotMemory(entry.memory_key).summary()
    rows.append(
        (
            "memory",
            Text(
                f"{_plural(int(memory['notes']), 'note')}, "
                f"{_plural(int(memory['episodes']), 'run')}  {memory['path']}",
                style=ui.STYLES["muted"],
            ),
        )
    )
    ui.console.print(ui.kv_grid(rows))


_CLEARABLE = (
    "address",
    "map-address",
    "token",
    "camera-url",
    "rest-pose",
    "host",
    "host-token",
    "llm",
    "note",
)


@robot_app.command("edit")
def robot_edit(
    name: str = _ROBOT_NAME,
    spec: str | None = typer.Option(None, "--spec", help="Move it to another <adapter>:<backend>."),
    address: str | None = _ADDR,
    map_address: str | None = _MAP_ADDR,
    camera_url: list[str] = _CAMERA_URL,
    token: str | None = _TOKEN,
    token_env: str | None = _TOKEN_ENV,
    host: str | None = _ROBOT_HOST,
    host_token: str | None = _ROBOT_HOST_TOKEN,
    host_token_env: str | None = _HOST_TOKEN_ENV,
    llm: str | None = typer.Option(
        None,
        "--llm",
        "-l",
        help="The pilot, as VENDOR[:MODEL]. Replaces whatever was stored.",
        autocompletion=_complete_llm,
        rich_help_panel="Model",
    ),
    note: str | None = typer.Option(None, "--note", help="One line for people."),
    clear: list[str] = typer.Option(
        [],
        "--clear",
        help=f"Empty one field: {', '.join(_CLEARABLE)}. Repeatable. Clearing host clears "
        "its token too.",
    ),
    registry_dir: str | None = _REGISTRY_DIR,
) -> None:
    """Change what a registered robot is or where it is."""
    from quackd.adapters.base import AdapterError
    from quackd.agent.providers.base import ProviderError
    from quackd.agent.providers.factory import parse_llm
    from quackd.registry import RegistryError

    token = _token_from("--token-env", token_env, token, instead_of="--token")
    host_token = _token_from(
        "--host-token-env", host_token_env, host_token, instead_of="--host-token"
    )
    given: dict[str, Any] = {
        "spec": spec,
        "address": address,
        "map_address": map_address,
        # every url given replaces the whole stored set: naming a camera today says where the
        # cameras are today, which is the rule --address and --token already follow
        "camera_url": list(camera_url) or None,
        "token": token,
        "host": host,
        "host_token": host_token,
        "llm": llm,
        "note": note,
    }
    if llm is not None and not llm.strip():
        # An empty value is not a pilot, and silently taking it as "forget the one you had"
        # loses a setting and reports success. `--clear llm` is the way to say that, and it is
        # a word rather than an absence.
        _fail("--llm needs a pilot: quackd robot edit NAME --clear llm forgets the stored one")
        return
    for flag, value, what in (("host", host, "a machine"), ("host-token", host_token, "a token")):
        if value is not None and not value.strip():
            # the same rule as --llm, for the same reason: an empty value would store nothing
            # and report success, and forgetting a board is `--clear`'s job
            _fail(
                f"--{flag} needs {what}: quackd robot edit NAME --clear {flag} forgets the "
                "stored one"
            )
            return
    changes: dict[str, Any] = {k: v for k, v in given.items() if v is not None}
    for field in clear:
        key = field.strip().lower().replace("-", "_")
        if key not in {c.replace("-", "_") for c in _CLEARABLE}:
            _fail(f"--clear {field}: empty one of {', '.join(_CLEARABLE)}")
            return
        if key in changes:
            _fail(f"--{key.replace('_', '-')} and --clear {field} contradict each other")
            return
        changes[key] = None
    if "host" in changes and changes["host"] is None:
        # A host token is its board's, and `resolve_host` sends a robot's token only when the
        # robot stores a board. Left behind, it would sit in robots.json as a secret for a board
        # this robot no longer names, and `robot show` would say `host token set` beside
        # `host -`. `--host-token` given alongside is left to the registry, which refuses it.
        changes.setdefault("host_token", None)
    if not changes:
        _fail("nothing to change: give a field to set, or --clear FIELD")
        return
    try:
        # The same gate `robot add` puts on the door, for the same reason: a spec typed here
        # is typed by a person who is looking at the answer.
        if llm is not None:
            parse_llm(llm, source="--llm")
        _registry(registry_dir).update_robot(name, changes)
    except (RegistryError, AdapterError, ValidationError, ProviderError) as e:
        _registry_fail(_one_line(e))
        return
    touched = ", ".join(sorted(key.replace("_", "-") for key in changes))
    ui.console.print(_ok_line(f"updated {name}: {touched}"))


@robot_app.command("rest-pose")
def robot_rest_pose(
    name: str = _ROBOT_NAME,
    clear: bool = typer.Option(False, "--clear", help="Forget the pose recorded for it."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Do not ask."),
    address: str | None = _ADDR,
    registry_dir: str | None = _REGISTRY_DIR,
    as_json: bool = _JSON,
) -> None:
    """Record where this arm rests: connect, read every joint, and keep the pose under NAME.

    A run starts from it and returns to it before torque is released, and a real arm with none
    is refused a run, which would let go of it wherever it ended. Fold the arm by hand first,
    with nothing connected, so the pose you record is one it can hold with torque off.
    """
    from quackd.adapters.base import AdapterError
    from quackd.adapters.factory import describe, make_adapter
    from quackd.registry import RegistryError
    from quackd.transport.base import TransportError

    registry = _registry(registry_dir)
    try:
        entry = registry.robot(name)
    except RegistryError as e:
        _fail(str(e), hint="quackd robot list")
        return

    if clear:
        if entry.rest_pose is None:
            _fail(
                f"{name} has no rest pose recorded",
                hint=f"quackd robot rest-pose {name}",
            )
            return
        try:
            entry = registry.update_robot(name, {"rest_pose": None})
        except (RegistryError, AdapterError, ValidationError) as e:
            _registry_fail(_one_line(e))
            return
        if as_json:
            print(json.dumps(entry.public()))
            return
        ui.console.print(_ok_line(f"cleared {name}'s rest pose"))
        from quackd.adapters.factory import needs_rest_pose

        # a real arm with no pose is refused a run altogether (ADR-0058), rather than being let
        # go of where it stands at the end of one, which is what a mock or a simulator still is.
        # The way back is a line of its own, as robot add's next command is, so that a wrap at
        # the terminal's width never splits the command a person copies.
        after = (
            (
                f"  a run, a dry run or an MCP session of {name} is now refused until a pose is "
                "recorded again",
                f"  quackd robot rest-pose {name}",
            )
            # the stored string, which `needs_rest_pose` reads without ever raising: the
            # parsed spec raises for one this build cannot make, after the pose is cleared
            if needs_rest_pose(entry.spec)
            else ("  a run now leaves the arm where it stands, and torque drops there",)
        )
        for line in after:
            ui.console.print(Text(line, style=ui.STYLES["muted"]))
        return

    if as_json and not yes:
        _fail("--json is for a script, and a script cannot answer a prompt: add --yes")
        return

    try:
        static = describe(entry.robot_spec)
    except AdapterError as e:
        _registry_fail(e)
        return
    if "joint" not in static.intents:
        _fail(
            f"{name} ({entry.key}) has no joints, so there is no rest pose to record",
            hint="a rest pose is for an arm: quackd list-adapters",
        )
        return

    kwargs = entry.adapter_kwargs(address=address)
    # no camera: reading joints needs none, and a webcam that will not open refuses the whole
    # connect. No rest pose either: the arm must be let go of at the pose you are choosing now,
    # not driven back to the one it is replacing.
    kwargs.update(camera_url=(), rest_pose=None)
    try:
        adapter = make_adapter(entry.robot_spec, **kwargs)
    except (AdapterError, ImportError) as e:
        _registry_fail(e if isinstance(e, AdapterError) else AdapterError(str(e)))
        return
    if not getattr(adapter, "supports_rest_pose", False):
        _fail(
            f"{name} ({entry.key}) has joints, and quackd does not drive it to a rest pose "
            "yet: only the LeRobot arm does today"
        )
        return

    if not yes and not _can_prompt():
        # Before anything connects, where it used to come after the read: the arm is let go of
        # where it lies only once the pose is answered for, so a read nobody can answer would
        # only leave it powered in its fold.
        _fail(
            "no terminal to ask on: pass --yes to record it",
            hint=f"quackd robot rest-pose {name} --yes",
        )
        return

    # the same shape `Resolved.label` prints, which is what every other line about a
    # registered robot says: the name you gave it, and the body under it
    label = f"{entry.name} ({entry.key})"

    async def read() -> dict[str, float]:
        await adapter.connect()
        state = await adapter.get_state()
        return {str(k): float(v) for k, v in dict(state.extras.get("joints", {})).items()}

    async def close(rests_here: bool) -> None:
        if rests_here:
            # The arm lies where a person folded it, and the pose read is the one now answered
            # for, so its close lets go of it there. Without the yes, a real arm's close keeps
            # torque on, as it does for any arm with no pose recorded (ADR-0058): an arm read
            # standing rather than folded is let go of only on a yes to the pose it reads.
            rests = getattr(adapter, "rests_where_it_reads", None)
            if callable(rests):
                rests()
        with contextlib.suppress(Exception):
            await adapter.close()

    # One event loop for the read and the close, with the question between them: the close
    # comes after the answer, which decides it.
    runner = asyncio.Runner()
    agreed = False
    try:
        try:
            with ui.spinner(f"reading {label}"):
                joints = runner.run(read())
        except (TransportError, OSError) as e:
            where = f" at {kwargs['address']}" if kwargs.get("address") else ""
            _fail(f"{entry.key}{where}: {e}")
            return
        if not joints:
            _fail(f"{label} reported no joint positions")
            return

        ui.console.print(Text(f"{label} is at", style=ui.STYLES["muted"]))
        ui.console.print(ui.kv_grid((j, f"{v:.1f}") for j, v in joints.items()))
        # A fold past the travel this arm's calibration recorded is still recorded: it is where
        # the arm rests, and a run parks at the edge of the travel and lets it settle there. But
        # the person folding it is the one who can fix it, so they hear it now, before
        # answering, in the words a run and `doctor` will use. The adapter owns the rule; this
        # only asks.
        note_for = getattr(adapter, "rest_pose_note", None)
        if callable(note_for) and (warning := note_for(joints)):
            ui.console.print(_warn_line(str(warning)))
        if not yes:
            already = ", replacing the one already recorded" if entry.rest_pose else ""
            with ui.pause_status():
                if not typer.confirm(f"record this as {name}'s rest pose{already}?"):
                    raise typer.Exit()
        agreed = True
    finally:
        with contextlib.suppress(Exception):
            runner.run(close(agreed))
        runner.close()
        # what the close did with the torque, on every way out: a read that failed or a no
        # leaves a real arm powered in its fold, and the line says how to put it down
        if close_note := getattr(adapter, "close_note", None):
            ui.console.print(_warn_line(str(close_note)))

    pose = {joint: round(value, 1) for joint, value in joints.items()}
    try:
        entry = registry.update_robot(name, {"rest_pose": pose})
    except (RegistryError, AdapterError, ValidationError) as e:
        _registry_fail(_one_line(e))
        return
    if as_json:
        print(json.dumps(entry.public()))
        return
    ui.console.print(_ok_line(f"recorded {name}'s rest pose ({_plural(len(pose), 'joint')})"))
    ui.console.print(
        Text(
            f"  quackd run <duck> --robot {name} starts from it and returns to it "
            "before letting go",
            style=ui.STYLES["muted"],
        )
    )


def _is_simulator(adapter: Any) -> bool:
    """Whether this body is a simulator of a real one (`lerobot:mujoco`), asked of the built
    adapter, which says so itself. A body that does not say is not one, so every real arm
    keeps the warnings it had."""
    return getattr(adapter, "is_simulator", False) is True


def _connect_warning(*, simulator: bool = False) -> str:
    """What connecting does to a body that is handed to a person, and why, in the one wording
    `quackd robot release`, `quackd doctor` and the arm's own close note share
    (`adapters.base.CONNECTING_TAKES_TORQUE_OFF`). Imported here rather than at the top, like
    every other `quackd.adapters` import in this file, so `quackd --help` stays quick.

    A simulator's connect does the same to its model, because it runs the real backend's
    connect, and says it is the simulator: a line telling somebody to hold an arm that is a
    picture on their screen teaches them to skim the line that matters when the arm is real."""
    if simulator:
        return (
            "this is the arm's simulator: connecting takes torque off every simulated motor "
            "for a moment, as LeRobot's connect does on a real arm"
        )
    from quackd.adapters.base import CONNECTING_TAKES_TORQUE_OFF

    return f"{CONNECTING_TAKES_TORQUE_OFF}, because LeRobot configures them with it off"


def _release_warning(*, simulator: bool = False, folds: bool = True) -> str:
    """Said before anything connects, and before the question, because the connect happens to
    an arm a person has to be supporting already. That half is upstream's (`configure()` runs
    inside `torque_disabled()`), and it is the reason this cannot wait until after the
    connect: by then the arm has already been limp once.

    The other half is what comes after the connect. An arm with a rest pose (`folds`) is folded
    to it and let go of there, so the person is told to take their hands away before it moves:
    a hand still under it is in the fold's path, and the fold is the part that moves. One with
    none has nothing to fold to, and its own question, which says it drops, comes before the
    connect. On a simulator the simulated arm folds, or falls, and nobody holds it."""
    if simulator:
        if folds:
            return (
                f"{_connect_warning(simulator=True)}, and the simulated arm then folds to its "
                "rest pose and is let go of there, with no arm to hold"
            )
        return (
            f"{_connect_warning(simulator=True)}, and the release then lets the simulated arm "
            "fall from wherever it is, with no arm to hold"
        )
    if folds:
        return (
            f"{_connect_warning()}, and the arm then folds to its rest pose and is let go of "
            "there: support it while it connects, then take your hands away when it says so, "
            "before it folds"
        )
    return (
        f"{_connect_warning()}, and with no rest pose recorded there is nothing to fold it to: "
        "you are asked before anything connects"
    )


def _doctor_warning(
    *, simulator: bool = False, folds: bool = True, keeps: bool = True, asks: bool = False
) -> str:
    """`_release_warning`'s first half, for `doctor --robot` on a body handed to people. A
    probe connects, and an arm left holding itself up is limp for that moment like any other,
    so the person is told to support it before the connect rather than finding out during it.

    What follows the connect is said as well, because the arms differ. One with a rest pose is
    folded to it before doctor lets go, and doctor says so first and waits for the hands to come
    away (`_DoctorHands.park`), terminal or not, so the hands that supported it come away when
    doctor says so: the line used to ask them to stay until doctor had finished, under the fold.
    A real arm with none is kept powered where it stands (`keeps`, ADR-0058), and the person is
    told doctor will ask whether to drop it into their hands only where it will (`asks`: a
    terminal, and no `--json`). The mock with none is let go of where it stands at the end, as
    every arm with none was until 0.24. A simulator has no arm to support, and the line says so
    rather than asking for a hand under one."""
    if simulator:
        return f"{_connect_warning(simulator=True)}, and there is no arm to support"
    if folds:
        return (
            f"{_connect_warning()}: support the arm while it connects, then take your hands away "
            "when doctor says so, because it folds the arm to its rest pose before it lets go"
        )
    if not keeps:
        return (
            f"{_connect_warning()}: support the arm until doctor has finished with it, because "
            "with no rest pose recorded it is let go of where it stands at the end"
        )
    kept = (
        f"{_connect_warning()}: support the arm while it connects. It has no rest pose recorded, "
        "so doctor keeps it powered where it stands at the end"
    )
    return f"{kept}, and asks first whether to drop it into your hands" if asks else kept


class _DoctorHands:
    """What `quackd doctor` asks of a person at a real arm handed to people.

    Before it folds an arm to its recorded rest pose, it says so and waits `HANDS_AWAY_S` for the
    hands that supported it through the connect to come away, as `quackd robot release` does,
    terminal or not, because the warning before the connect says it will. A press of the kill
    switch stops the fold before it moves or while it does, holding the arm where the press
    found it, and one made while the arm connected or was read stops it before anything moves:
    the switch goes in as the arm is about to connect, and doctor used to fold the arm after such
    a press, the switch having swallowed the Ctrl-C that used to end it. Over a real arm with no
    rest pose, whose close keeps its torque rather than let it go where it stands (ADR-0058), one
    question before the probe lets go of the bus, at a terminal and without `--json`: whether to
    drop it into the person's hands (`DROP_UNPOSED`), which at first contact is how an arm comes
    to be limp for folding by hand. A simulator and a body nobody holds get none of it, and keep
    the Ctrl-C that ends doctor."""

    def __init__(self, label: str, *, prompting: bool, unposed: bool) -> None:
        self.label = label
        self.prompting = prompting
        """Whether a question can be put: a terminal, and no `--json`."""
        self.unposed = unposed
        """An arm whose close will keep its torque: a real arm with no rest pose recorded."""
        self.switch: KillSwitch | None = None
        self.person: _TerminalHandOff | None = None
        self._stop_check: Callable[[Any], None] | None = None

    def start(self, adapter: Any) -> None:
        """Install the kill switch, from inside the probe's event loop, which it needs, for a real
        body handed to people: called as the body is about to connect. A press while a connect is
        failing ends its retries, as in `robot release`."""
        from quackd.safety import KillSwitch

        if self.switch is not None or not getattr(adapter, "supports_hand_off", False):
            return
        if _is_simulator(adapter):
            return
        switch = KillSwitch(asyncio.Event(), log=_switch_pressed)
        person = _TerminalHandOff()
        person.bind(switch)
        switch.install()
        self.switch, self.person = switch, person
        stop_check = getattr(adapter, "set_stop_check", None)
        if callable(stop_check):
            stop_check(switch.abort.is_set)
            self._stop_check = stop_check

    def finish(self) -> None:
        """Take the kill switch down again: inside the probe's event loop as the probe ends
        (`doctor.probe`'s `after_close`), where its handler is bound, and once more after it,
        which then does nothing."""
        if self._stop_check is not None:
            with contextlib.suppress(Exception):
                self._stop_check(None)
        if self.switch is not None:
            self.switch.uninstall()
        self.switch = self.person = None
        self._stop_check = None

    def pressed(self) -> bool:
        """A Ctrl-C or `q` since the switch went in: nothing is moved or released after one."""
        return self.switch is not None and self.switch.presses > 0

    async def park(self, adapter: Any) -> RestResult:
        """The probe's fold, with the hands away first and a press stopping it."""
        from quackd.adapters.base import RestResult, go_to_rest_if_any
        from quackd.release import (
            FOLD_STOPPED,
            FOLDING_IN,
            HANDS_AWAY_S,
            FoldStopped,
            fold_unless_stopped,
        )

        person = self.person
        if person is None or getattr(adapter, "rest_pose", None) is None:
            return await go_to_rest_if_any(adapter)
        if self.pressed():
            # a Ctrl-C or `q` while the arm connected or was read: nothing moves after one
            return RestResult("refused", "stopped before the fold: nothing was moved")
        with ui.pause_status():
            ui.err_console.print(
                Text(
                    FOLDING_IN.format(name=self.label, seconds=HANDS_AWAY_S),
                    style=ui.STYLES["warn"],
                )
            )
            try:
                await fold_unless_stopped(_hands_away, person.stopped)
            except (FoldStopped, asyncio.CancelledError, KeyboardInterrupt):
                return RestResult("refused", "stopped before the fold: nothing was moved")
            try:
                parked = await fold_unless_stopped(
                    lambda: go_to_rest_if_any(adapter), person.stopped
                )
                return parked if parked is not None else RestResult.none()
            except (FoldStopped, asyncio.CancelledError, KeyboardInterrupt):
                with contextlib.suppress(Exception, asyncio.CancelledError, KeyboardInterrupt):
                    # held where the stopped fold left it, a step cap on at most
                    await adapter.stop()
                return RestResult("refused", FOLD_STOPPED)

    async def before_close(self, adapter: Any) -> None:
        """Over an arm whose close keeps its torque, ask whether to drop it instead. Not after a
        press, which asked for nothing to move. The release is `release_where_it_stands`, which
        a second Ctrl-C landing on it cannot take past the close, and what it did is said."""
        from quackd.release import DROP_UNPOSED, ENTER, ENTER_DROPS_S, ask, release_where_it_stands

        person = self.person
        if person is None or not self.prompting or not self.unposed or self.pressed():
            return
        question = DROP_UNPOSED.format(name=self.label, seconds=ENTER_DROPS_S)
        with ui.pause_status():
            if await ask(person, question, ENTER_DROPS_S) == ENTER:
                settled = await release_where_it_stands(adapter)
                if settled.said:
                    ui.err_console.print(_warn_line(settled.said))


def _switch_pressed(_message: str) -> None:
    """The kill switch's line outside a run, which names no verb: only that the press took."""
    ui.err_console.print(Text("kill switch pressed", style=ui.STYLES["warn"]))


async def _hands_away() -> None:
    from quackd.release import HANDS_AWAY_S

    await asyncio.sleep(HANDS_AWAY_S)


@robot_app.command("release")
def robot_release(
    name: str = _ROBOT_NAME,
    yes: bool = typer.Option(
        False,
        "--yes",
        "-y",
        help=(
            "Do not ask before it connects and folds. A fold that misses still asks at a "
            "terminal, twice, before anything drops, and an arm with no rest pose is still asked "
            "about before it connects; with no terminal, a missed fold keeps torque on and exits "
            "1."
        ),
    ),
    address: str | None = _ADDR,
    registry_dir: str | None = _REGISTRY_DIR,
) -> None:
    """Fold this arm to its rest pose and take torque off there.

    For an arm left holding itself up, which is what a run does when it cannot get the arm back
    to its rest pose, and for any arm you want limp without cutting its power. It says what
    connecting does to the arm, asks, and only then connects, prints the joints, folds the arm
    to its recorded rest pose and lets go of it there, reading torque back. Where the fold
    misses, it asks you to clear the arm's way and folds once more, and only if that misses too
    does it offer to take torque off where the arm stands, which drops it, so you hold it first.
    An arm with no rest pose recorded has nothing to fold to, and is asked about before anything
    connects. Ctrl-C or q stops a fold. No verb and no MCP tool can do this: it is a command a
    person types.
    """
    from quackd.adapters.base import AdapterError, go_to_rest_if_any, let_go_if_any
    from quackd.adapters.factory import make_adapter
    from quackd.registry import RegistryError
    from quackd.release import (
        DROP_UNFOLDED,
        ENTER,
        ENTER_DROPS_S,
        FOLD_INTERRUPTED,
        FOLD_STOPPED,
        FOLDING_IN,
        HANDS_AWAY_S,
        LIMP_AFTER_A_MISS,
        TORQUE_READS_OFF,
        FoldStopped,
        Settled,
        ask,
        fold_unless_stopped,
        release_where_it_stands,
        settle_missed_fold,
    )
    from quackd.safety import KillSwitch
    from quackd.transport.base import TransportError

    registry = _registry(registry_dir)
    try:
        entry = registry.robot(name)
    except RegistryError as e:
        _fail(str(e), hint="quackd robot list")
        return

    kwargs = entry.adapter_kwargs(address=address)
    # No camera: releasing needs none, and a webcam that will not open refuses the whole
    # connect. The rest pose IS kept, unlike `rest-pose`: it is what the close judges the arm
    # against, so an arm this could not release closes under the ordinary torque rule.
    kwargs.update(camera_url=())
    try:
        adapter = make_adapter(entry.robot_spec, **kwargs)
    except (AdapterError, ImportError) as e:
        _registry_fail(e if isinstance(e, AdapterError) else AdapterError(str(e)))
        return
    if not getattr(adapter, "supports_hand_off", False):
        _fail(
            f"{name} ({entry.key}) is not a body quackd takes torque off: only the LeRobot arm is",
            hint="quackd list-adapters",
        )
        return

    label = f"{entry.name} ({entry.key})"
    # the arm's simulator releases a simulated arm, and every line said before its connect
    # says so rather than asking for a hand under an arm that is not there
    simulated = _is_simulator(adapter)
    rest = getattr(adapter, "rest_pose", None)
    prompting = _can_prompt()
    muted = ui.STYLES["muted"]
    if rest is None and not simulated and not prompting:
        # Nothing to fold to, so the one way this command could take torque off is where the
        # arm stands, which drops it, and that is Enter at a terminal or nothing. Refused before
        # the warning and whatever --yes says: its close would let go of the arm too.
        _fail(
            f"{name} has no rest pose recorded, so there is nothing to fold it to, and taking "
            "torque off where it stands drops it, which only Enter at a terminal may do",
            hint="run it from a terminal, or hold the arm and cut its power",
        )
        return
    if not yes and not prompting:
        # refused before the warning, because nothing is going to happen that a person has to
        # get hold of the arm for
        again = f"quackd robot release {name} --yes"
        _fail(
            "no terminal to ask on: pass --yes to fold it to its rest pose and let go there",
            hint=again,
        )
        return
    # Before anything connects: connecting is the first thing that takes torque off, so a
    # warning printed after it would arrive after the arm had already been limp once.
    ui.console.print(_warn_line(_release_warning(simulator=simulated, folds=rest is not None)))
    if not yes and (rest is not None or simulated):
        # Asked before the key thread exists, so nothing races `input()` for the keystroke. An
        # arm with no rest pose has its own question, inside, and it is the one that matters.
        question = (
            f"fold {name} to its rest pose and release torque there?"
            if rest is not None
            else f"release torque on {name}?"
        )
        with ui.pause_status():
            if not typer.confirm(question):
                ui.console.print(
                    Text("  nothing was connected, and the arm is as it was", style=muted)
                )
                raise typer.Exit()

    def pressed(_message: str) -> None:
        """The kill switch's line, which is the run's and names a verb: here it says only that
        the press registered, since every step says for itself what came of it."""
        ui.err_console.print(Text("kill switch pressed", style=ui.STYLES["warn"]))

    def fold() -> Any:
        return go_to_rest_if_any(adapter)

    async def hands_away() -> None:
        await asyncio.sleep(HANDS_AWAY_S)

    at_rest: list[bool] = []
    """Whether the release was the one asked for at the rest pose, which the close then says the arm
    rests limp after, rather than one where the arm stood, which leaves it in a person's hands."""

    async def settle(person: _TerminalHandOff, presses: Callable[[], int]) -> Settled:
        """Everything between the connect and the close, as one `Settled`."""
        joints: dict[str, float] = {}
        with contextlib.suppress(Exception):
            state = await adapter.get_state()
            joints = {str(k): float(v) for k, v in dict(state.extras.get("joints", {})).items()}
        if joints:
            # where it is before anything moves it, which is where the person is supporting it
            ui.console.print(Text(f"{label} is at", style=muted))
            ui.console.print(ui.kv_grid((j, f"{v:.1f}") for j, v in joints.items()))
        stopped_before = (
            "stopped before the fold: nothing was moved, and the close lets go of the arm only "
            "where it reads at its rest pose"
        )
        if presses():
            # a Ctrl-C or `q` while it connected: nothing is moved or released after one
            said = stopped_before
            if rest is None and not simulated:
                # and a real arm with no rest pose is kept powered by its close (ADR-0058): the
                # press came after the Enter that asked for the drop, and stopped it
                said = (
                    "stopped before anything was released: with no rest pose recorded the close "
                    "keeps torque on, so the arm holds where it stands. To put it down, hold it "
                    f"and cut its power, or run quackd robot release {name} again and press Enter "
                    "at its question"
                )
            return Settled("kept", "stopped before the fold", said=said)
        if rest is None:
            # nothing to fold to: the simulator's arm, or a real one after Enter at the question
            # that said it drops
            return await release_where_it_stands(adapter)
        if not simulated:
            # The hands that supported the arm through the connect come away before it moves:
            # the fold used to start a few milliseconds after the connect, under them. The
            # countdown is raced against a press the way the fold is, and one stops the command
            # with nothing moved.
            ui.err_console.print(
                Text(FOLDING_IN.format(name=name, seconds=HANDS_AWAY_S), style=ui.STYLES["warn"])
            )
            try:
                await fold_unless_stopped(hands_away, person.stopped)
            except (FoldStopped, asyncio.CancelledError, KeyboardInterrupt):
                return Settled("kept", "stopped before the fold", said=stopped_before)
        ui.console.print(
            Text(f"folding {name} to its rest pose: keep your hands out of its way", style=muted)
        )
        try:
            first = await fold_unless_stopped(fold, person.stopped)
        except (FoldStopped, asyncio.CancelledError, KeyboardInterrupt):
            with contextlib.suppress(Exception, asyncio.CancelledError, KeyboardInterrupt):
                # holds the arm where the cancelled fold left it, a step cap on at most, and a
                # second press landing here is caught too, so the close still comes
                await adapter.stop()
            return Settled("kept", FOLD_INTERRUPTED, said=FOLD_STOPPED)
        if first is not None and first.note:
            # what the body has to say about the pose it parked in, once, as a run says it
            ui.console.print(Text(first.note, style=muted))
        if first is not None and first.reached:
            # the first door, which lets go nowhere but this pose, so it can never drop the arm
            released = await let_go_if_any(adapter, asked=True)
            at_rest.append(True)
            return Settled("released", released.reason, released=released)
        why = first.reason if first is not None else "the arm has no rest move"
        if first is None or not first.answered:
            return Settled(
                "kept",
                why,
                said=(
                    f"the arm stopped answering during the fold ({why}), so quackd cannot tell "
                    "whether it is holding itself up: hold it, and cut its power"
                ),
            )
        if first.limp:
            # what its own read found, said without a question, as at the end of a run
            return Settled(
                "kept",
                TORQUE_READS_OFF,
                said=LIMP_AFTER_A_MISS.format(why=why, limp=", ".join(first.limp)),
            )
        if simulated or not prompting:
            whose = "the simulated arm" if simulated else "the arm"
            return Settled(
                "kept",
                why,
                said=(
                    f"{whose} did not reach its rest pose ({why}), so torque stays on"
                    + (
                        ""
                        if simulated
                        else ", and with no terminal to ask on nothing more is tried"
                    )
                ),
            )
        settled = await settle_missed_fold(adapter, person, first, fold=fold, stop=adapter.stop)
        if settled.how == "folded":
            released = await let_go_if_any(adapter, asked=True)
            at_rest.append(True)
            return Settled("released", released.reason, released=released, fold=settled.fold)
        return settled

    async def release() -> tuple[Settled | None, str | None]:
        """The release, and the close's note. None for an arm with no rest pose whose question
        was not answered with Enter, which is never connected to."""
        switch = KillSwitch(asyncio.Event(), log=pressed)
        person = _TerminalHandOff()
        person.bind(switch)
        switch.install()
        try:
            if rest is None and not simulated:
                question = DROP_UNFOLDED.format(name=name, seconds=ENTER_DROPS_S)
                if await ask(person, question, ENTER_DROPS_S) != ENTER:
                    return None, None
            # a Ctrl-C during a connect that is failing ends its retries, as in a run, each of
            # which would switch torque off on every motor again
            stop_check = getattr(adapter, "set_stop_check", None)
            if callable(stop_check):
                stop_check(switch.abort.is_set)
            try:
                await adapter.connect()
            finally:
                if callable(stop_check):
                    stop_check(None)
            settled: Settled | None = None
            try:
                settled = await settle(person, lambda: switch.presses)
            finally:
                with contextlib.suppress(Exception):
                    await adapter.close()
                # an interrupt went through everything above, so nothing after this runs: the
                # close's own line, what it did with the torque, is said here or never
                if settled is None and (close_note := getattr(adapter, "close_note", None)):
                    ui.console.print(_warn_line(str(close_note)))
            note = getattr(adapter, "close_note", None)
            return settled, str(note) if note else None
        finally:
            switch.uninstall()

    try:
        settled, note = asyncio.run(release())
    except (TransportError, OSError) as e:
        where = f" at {kwargs['address']}" if kwargs.get("address") else ""
        after = (
            "this was the arm's simulator, so there is no arm to hold"
            if simulated
            else "a connect that failed part way can leave some motors limp: keep hold of the arm"
        )
        _fail(f"{entry.key}{where}: {e}", hint=f"nothing was released by quackd, and {after}")
        return

    if settled is None:
        ui.console.print(Text("  nothing was connected, and the arm is as it was", style=muted))
        return
    released = settled.released
    failed = True
    if settled.how in ("kept", "interrupted") or released is None:
        again = f"clear its way and run quackd robot release {name} again"
        hint = None if settled.said is not None else f"{again}, or hold it and cut its power"
        line = settled.said or f"torque stays on and nothing was released: {settled.reason}"
        ui.err_console.print(ui.fail_line(line, hint=hint))
    elif released.ok and released.torque_on == ():
        failed = False
        ui.console.print(_ok_line(f"torque reads off on every joint of {name}"))
    elif released.torque_on:
        ui.err_console.print(
            ui.fail_line(
                f"torque still reads on for {', '.join(released.torque_on)}: cut the power",
                hint=released.reason,
            )
        )
    elif released.ok:
        # sent, and never read back: the arm is treated as limp, which is what keeps it up, and
        # the motors after one whose write failed may still hold, which only the switch settles
        ui.err_console.print(
            ui.fail_line(
                f"torque was taken off and could not be read back: {released.reason}",
                hint="hold the arm as though nothing holds it, and cut its power to be sure",
            )
        )
    else:
        ui.err_console.print(
            ui.fail_line(
                f"nothing was released: {released.reason}",
                # not "run it again": the close's line follows, and says what holds the arm now
                hint="hold the arm, and cut its power",
            )
        )
    if note and simulated and released is not None:
        # The close note is the real backend's, written for somebody holding a real arm, and
        # the warning above has just told this person that nobody is. The simulator's close
        # let the released arm settle under the model's gravity, so that is the line to end on.
        ui.console.print(
            Text(
                "  this was the arm's simulator: the simulated arm settled where the model's "
                "physics left it when it closed, and there is nothing to put down",
                style=muted,
            )
        )
    elif note and not failed and at_rest:
        # the close found it limp at its rest pose, as asked: said, but not as a warning
        ui.console.print(Text(f"  {note}", style=muted))
    elif note:
        # torque kept on away from the pose, or an arm limp in somebody's hands: the line to
        # end on, and the one a person acts on
        ui.console.print(_warn_line(note))
    if failed:
        raise typer.Exit(code=1)


@robot_app.command("remove")
def robot_remove(
    name: str = _ROBOT_NAME,
    yes: bool = typer.Option(False, "--yes", "-y", help="Do not ask."),
    force: bool = typer.Option(
        False, "--force", help="Also drop it from every flock that lists it."
    ),
    registry_dir: str | None = _REGISTRY_DIR,
) -> None:
    """Forget a registered robot. Its memory file stays, and after this only the path finds it,
    because `quackd memory` addresses a robot by a name that is no longer registered."""
    from quackd.memory import RobotMemory
    from quackd.registry import RegistryError

    try:
        registry = _registry(registry_dir)
        entry = registry.robot(name)
        holding = registry.flocks_of(name)
    except RegistryError as e:
        _fail(str(e), hint="quackd robot list")
        return
    if holding and not force:
        _registry_fail(_in_use(name, holding))
        return
    also = f" and drop it from {', '.join(holding)}" if holding else ""
    if not yes:
        with ui.pause_status():
            if not typer.confirm(f"remove {name} ({entry.key}){also}?"):
                raise typer.Exit()
    try:
        dropped = registry.remove_robot(name, force=force)
    except RegistryError as e:
        _registry_fail(e)
        return
    ui.console.print(_ok_line(f"removed {name}"))
    kept = RobotMemory(entry.memory_key).path
    if kept.exists():
        # `quackd memory clear --robot <name>` cannot reach it any more: the name is gone
        ui.console.print(Text(f"  its notes are still at {kept}", style=ui.STYLES["muted"]))
    if dropped:
        ui.console.print(Text(f"  dropped from {', '.join(dropped)}", style=ui.STYLES["muted"]))


def _in_use(name: str, flocks: list[str]) -> Exception:
    from quackd.registry import RobotInUse

    return RobotInUse(name, flocks)


# ── flock (the stored groups) ───────────────────────────────────────────────────────────

flock_app = typer.Typer(
    name="flock",
    help="Named groups of registered robots, for --flock NAME on run and serve-mcp. Not the "
    "flock: block of a .duck file, which says how a task is shared out: this says which "
    "bodies share it. Kept in ~/.quackd/flocks.json.",
    no_args_is_help=True,
)
app.add_typer(flock_app, name="flock", rich_help_panel="Robots")

_FLOCK_NAME = typer.Argument(..., help="A slug. Not a number: --flock N means N simulated ducks.")
_FLOCK_MEMBER = typer.Option(
    [], "--robot", "-r", help="A registered robot to include. Repeatable, order kept."
)
PROMPT_TRIES = 3
"""How many times the picker re-asks before giving up. Enough for a typo, not a loop."""


def _flock_status(flock: Any, missing: list[str]) -> Any:
    """One cell saying whether this flock could run, and what to do if not."""
    if missing:
        text, style = f"{', '.join(missing)} not registered", ui.STYLES["warn"]
    elif not flock.members:
        text, style = "empty", ui.STYLES["warn"]
    elif len(flock.members) < 2:
        text, style = "1 robot: running needs 2 to 8", ui.STYLES["warn"]
    else:
        return Text("ok", style=ui.STYLES["ok"])

    def build(g: ui.Glyphs) -> Any:
        return Text(f"{g.warn} {text}", style=style)

    return ui.Deferred(build)


def _members_cell(flock: Any, missing: list[str]) -> Any:
    def build(g: ui.Glyphs) -> Any:
        out = Text()
        for i, member in enumerate(flock.members):
            if i:
                out.append(f" {g.dot} ", style=ui.STYLES["muted"])
            if member in missing:
                out.append(f"{g.warn} {member}", style=ui.STYLES["warn"])
            else:
                out.append(member)
        return out or Text("-", style=ui.STYLES["muted"])

    return ui.Deferred(build)


def _pick_members(registry: Any) -> list[str]:
    """The numbered table, and the answer. Only reached when there is a terminal to ask on."""
    entries = registry.robots()
    table = ui.table("robots you have registered")
    table.add_column("#", no_wrap=True, justify="right", style=ui.STYLES["muted"])
    table.add_column("name", no_wrap=True, style=ui.STYLES["key"])
    table.add_column("robot", no_wrap=True)
    table.add_column("note", ratio=1)
    names = list(entries)
    for i, name in enumerate(names, 1):
        entry = entries[name]
        table.add_row(
            Text(str(i)),
            Text(name),
            Text(entry.key, style=ui.STYLES["accent"]),
            Text(entry.note or ""),
        )
    ui.console.print(table)
    for attempt in range(PROMPT_TRIES):
        answer = typer.prompt(
            "which robots? (numbers or names, comma separated, empty to cancel)",
            default="",
            show_default=False,
        )
        if not answer.strip():
            ui.console.print(Text("nothing created", style=ui.STYLES["muted"]))
            raise typer.Exit()
        chosen, problem = _read_picks(answer, names)
        if problem is None:
            return chosen
        ui.err_console.print(Text(problem, style=ui.STYLES["warn"]))
        if attempt == PROMPT_TRIES - 1:
            _fail(f"no valid answer in {PROMPT_TRIES} tries")
    return []


def _read_picks(answer: str, names: list[str]) -> tuple[list[str], str | None]:
    """`1, 3` or `duck-a, arm` or both. Returns the names, or what was wrong with the answer."""
    picked: list[str] = []
    tokens = [t for t in re.split(r"[,\s]+", answer.strip()) if t]
    for token in tokens:
        if token.isdigit():
            index = int(token)
            if not 1 <= index <= len(names):
                return [], f"there is no robot {token}: pick 1 to {len(names)}"
            name = names[index - 1]
        elif token in names:
            name = token
        else:
            return [], (
                f"no robot called {token!r}: pick 1 to {len(names)}, or a name from the table"
            )
        if name in picked:
            return [], f"{name} twice"
        picked.append(name)
    return picked, None


@flock_app.command("create")
def flock_create(
    name: str = _FLOCK_NAME,
    robot: list[str] = _FLOCK_MEMBER,
    description: str | None = typer.Option(None, "--description", help="One line for people."),
    registry_dir: str | None = _REGISTRY_DIR,
) -> None:
    """Name a group of registered robots. With no --robot it lists them and asks."""
    from quackd.registry import MAX_MEMBERS, RegistryError, StoredFlock

    try:
        registry = _registry(registry_dir)
        known = registry.robots()
    except RegistryError as e:
        _registry_fail(e)
        return
    members = list(robot)
    if not members:
        if not known:
            _fail(
                "no robots registered yet: quackd robot add NAME <adapter>:<backend> first",
                hint="quackd robot list",
            )
            return
        if not _can_prompt():
            _fail(
                "no terminal to ask on: name the robots yourself",
                hint="quackd flock create NAME --robot A --robot B",
            )
            return
        members = _pick_members(registry)
    try:
        flock = registry.add_flock(StoredFlock(name=name, members=members, description=description))
    except (RegistryError, ValidationError) as e:
        _registry_fail(_one_line(e))
        return
    ui.console.print(
        _ok_line(
            f"created flock {flock.name}: {', '.join(flock.members) or 'no robots'} "
            f"({_plural(len(flock.members), 'robot')})"
        )
    )
    if len(flock.members) < 2:
        ui.err_console.print(
            Text(
                f"  a flock runs with 2 to {MAX_MEMBERS}: "
                f"quackd flock edit {flock.name} --add NAME",
                style=ui.STYLES["warn"],
            )
        )
        return
    ui.console.print(Text(f"  quackd run <duck> --flock {flock.name}", style=ui.STYLES["muted"]))


@flock_app.command("list")
def flock_list(
    registry_dir: str | None = _REGISTRY_DIR,
    as_json: bool = _JSON,
) -> None:
    """Every flock you have made, and whether it could run."""
    from quackd.registry import RegistryError

    try:
        registry = _registry(registry_dir)
        flocks = registry.flocks()
        missing = {name: registry.missing_members(f) for name, f in flocks.items()}
    except RegistryError as e:
        _registry_fail(e)
        return
    if as_json:
        for name, flock in flocks.items():
            print(json.dumps(flock.public(missing[name]), ensure_ascii=False))
        return
    if not flocks:
        ui.console.print(
            Text(
                "no flocks yet: quackd flock create NAME --robot A --robot B",
                style=ui.STYLES["muted"],
            )
        )
        return
    table = ui.table("flocks (--flock NAME)")
    table.add_column("name", no_wrap=True, style=ui.STYLES["key"])
    table.add_column("robots", overflow="fold")
    table.add_column("status", overflow="fold")
    table.add_column("description", ratio=1)
    for name, flock in flocks.items():
        table.add_row(
            Text(name),
            _members_cell(flock, missing[name]),
            _flock_status(flock, missing[name]),
            Text(flock.description or ""),
        )
    ui.console.print(table)
    if any(missing.values()):
        ui.console.print(
            Text(
                "a robot marked as not registered was removed by hand: quackd robot add it "
                "back, or quackd flock edit NAME --remove it",
                style=ui.STYLES["muted"],
            )
        )


@flock_app.command("show")
def flock_show(
    name: str = _FLOCK_NAME,
    registry_dir: str | None = _REGISTRY_DIR,
    as_json: bool = _JSON,
) -> None:
    """One flock, and the robots in it."""
    from quackd.registry import RegistryError

    try:
        registry = _registry(registry_dir)
        flock = registry.flock(name)
        missing = registry.missing_members(flock)
        known = registry.robots()
    except RegistryError as e:
        _fail(str(e), hint="quackd flock list")
        return
    if as_json:
        payload = {
            **flock.public(missing),
            "robots": [known[m].public() for m in flock.members if m in known],
        }
        print(json.dumps(payload, ensure_ascii=False))
        return
    rows: list[tuple[str, Any]] = [
        ("name", Text(flock.name, style=ui.STYLES["key"])),
        ("robots", _members_cell(flock, missing)),
        ("status", _flock_status(flock, missing)),
    ]
    if flock.description:
        rows.append(("description", Text(flock.description)))
    rows += [
        ("created", Text(flock.created, style=ui.STYLES["muted"])),
        ("updated", Text(flock.updated, style=ui.STYLES["muted"])),
    ]
    ui.console.print(ui.kv_grid(rows))
    if not flock.members:
        return
    table = ui.table("its robots")
    table.add_column("#", no_wrap=True, justify="right", style=ui.STYLES["muted"])
    table.add_column("name", no_wrap=True, style=ui.STYLES["key"])
    table.add_column("robot", no_wrap=True)
    table.add_column("address", overflow="fold")
    table.add_column("note", ratio=1)
    for i, member in enumerate(flock.members, 1):
        entry = known.get(member)
        if entry is None:
            table.add_row(
                Text(str(i)),
                Text(member, style=ui.STYLES["warn"]),
                Text("not registered", style=ui.STYLES["warn"]),
                Text(""),
                Text(""),
            )
            continue
        table.add_row(
            Text(str(i)),
            Text(member),
            Text(entry.key, style=ui.STYLES["accent"]),
            Text(entry.address or "-", style="" if entry.address else ui.STYLES["muted"]),
            Text(entry.note or ""),
        )
    ui.console.print(table)


@flock_app.command("edit")
def flock_edit(
    name: str = _FLOCK_NAME,
    add: list[str] = typer.Option([], "--add", help="A registered robot to add. Repeatable."),
    remove: list[str] = typer.Option([], "--remove", help="A member to drop. Repeatable."),
    description: str | None = typer.Option(
        None, "--description", help="Change it. An empty string clears it."
    ),
    rename: str | None = typer.Option(None, "--rename", help="A new name for the flock."),
    registry_dir: str | None = _REGISTRY_DIR,
) -> None:
    """Add or drop members, change the description, or rename the flock."""
    from quackd.registry import RegistryError

    if not (add or remove or description is not None or rename):
        _fail("nothing to change: --add, --remove, --description or --rename")
        return
    try:
        flock = _registry(registry_dir).update_flock(
            name, add=add, remove=remove, description=description, rename=rename
        )
    except (RegistryError, ValidationError) as e:
        _registry_fail(_one_line(e))
        return
    done: list[str] = []
    if add:
        done.append(f"added {', '.join(add)}")
    if remove:
        done.append(f"removed {', '.join(remove)}")
    if description is not None:
        done.append("cleared the description" if not description else "changed the description")
    if rename:
        done.append(f"renamed to {rename}")
    ui.console.print(_ok_line(f"updated {name}: {'; '.join(done)}"))
    ui.console.print(
        Text(f"  {flock.name}: {', '.join(flock.members) or 'no robots'}", style=ui.STYLES["muted"])
    )


@flock_app.command("delete")
def flock_delete(
    name: str = _FLOCK_NAME,
    yes: bool = typer.Option(False, "--yes", "-y", help="Do not ask."),
    registry_dir: str | None = _REGISTRY_DIR,
) -> None:
    """Forget a flock. The robots in it stay registered."""
    from quackd.registry import RegistryError

    try:
        registry = _registry(registry_dir)
        flock = registry.flock(name)
    except RegistryError as e:
        _fail(str(e), hint="quackd flock list")
        return
    if not yes:
        with ui.pause_status():
            asked = typer.confirm(
                f"delete flock {name} ({_plural(len(flock.members), 'robot')})? "
                "the robots stay registered"
            )
            if not asked:
                raise typer.Exit()
    registry.delete_flock(name)
    ui.console.print(_ok_line(f"deleted flock {name}"))


# ── memory ──────────────────────────────────────────────────────────────────────────────

memory_app = typer.Typer(
    name="memory",
    help="What a robot remembers between runs: notes the pilot saved, and how runs ended.",
    no_args_is_help=True,
)
app.add_typer(memory_app, name="memory", rich_help_panel="Memory")


def _memory_for(robot: str | None, memory_dir: str | None, registry_dir: str | None = None) -> Any:
    from quackd.adapters.base import AdapterError
    from quackd.memory import RobotMemory
    from quackd.registry import Registry, RegistryError, resolve_robot_ref

    try:
        resolved = resolve_robot_ref(robot, Registry(registry_dir))
    except (AdapterError, RegistryError) as e:
        # every other --robot command answers in one line, not a traceback
        _fail(str(e))
    # a registered robot keys by its name, so two ducks of one kind keep separate notes
    return RobotMemory(resolved.memory_key, memory_dir)


@memory_app.command("show")
def memory_show(
    robot: str | None = _ROBOT,
    memory_dir: str | None = _MEMORY_DIR,
    registry_dir: str | None = _REGISTRY_DIR,
    raw: bool = typer.Option(False, "--raw", help="Print the JSONL file as is."),
) -> None:
    """Print what one robot remembers (default: the Microduck simulator)."""
    mem = _memory_for(robot, memory_dir, registry_dir)
    if raw:
        if mem.path.exists():
            # "as is" means as is: a note saying "the ball is [bold]behind[/bold] the sofa"
            # is markup Rich would silently eat, and an unpaired tag would raise.
            print(mem.path.read_text(encoding="utf-8"), end="")
        return
    info = mem.summary()
    ui.console.print(
        Text.assemble(
            (str(info["robot"]), ui.STYLES["key"]),
            (f"  {_plural(info['notes'], 'note')}, {_plural(info['episodes'], 'run')}  ", ""),
            (str(info["path"]), ui.STYLES["muted"]),
        )
    )
    notes, episodes = mem.notes(), mem.episodes()
    if not notes and not episodes:
        ui.console.print(Text("nothing remembered yet", style=ui.STYLES["muted"]))
        return
    if notes:
        table = ui.table("notes the pilot saved")
        table.add_column("date", no_wrap=True)
        table.add_column("tags", style=ui.STYLES["muted"])
        table.add_column("note", ratio=1)
        for entry in reversed(notes[-50:]):
            table.add_row(Text(entry.date), Text(", ".join(entry.tags)), Text(entry.text))
        ui.console.print(table)
    if episodes:
        table = ui.table("how recent runs ended")
        table.add_column("date", no_wrap=True)
        table.add_column("duck", no_wrap=True)
        table.add_column("outcome", no_wrap=True)
        table.add_column("what happened", ratio=1)
        for entry in reversed(episodes[-10:]):
            outcome = str(entry.outcome or "")
            what = Text(_episode_detail(entry))
            if entry.highlights:
                joined = "; ".join(entry.highlights)
                what.append(NEWLINE + joined, style=ui.STYLES["muted"])
            table.add_row(
                Text(entry.date),
                Text(str(entry.duck or "")),
                Text(outcome, style=ui.STYLES["ok" if outcome == "success" else "warn"]),
                what,
            )
        ui.console.print(table)


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def _episode_detail(entry: Any) -> str:
    """An episode's text without the duck and outcome it already has columns for."""
    prefix = f"{entry.duck}: {entry.outcome} — "
    if entry.duck and entry.outcome and entry.text.startswith(prefix):
        return str(entry.text[len(prefix) :])
    return str(entry.text)


@memory_app.command("add")
def memory_add(
    text: str = typer.Argument(..., help="One short fact, e.g. 'the ball lives by the sofa'."),
    robot: str | None = _ROBOT,
    memory_dir: str | None = _MEMORY_DIR,
    registry_dir: str | None = _REGISTRY_DIR,
    tag: list[str] = typer.Option([], "--tag", help="Optional label(s)."),
) -> None:
    """Save a note by hand, the same way the pilot's `remember` does."""
    mem = _memory_for(robot, memory_dir, registry_dir)
    entry = mem.remember(text, tags=tag)
    ui.console.print(
        Text.assemble(
            ("remembered for ", ""), (mem.robot_key, ui.STYLES["key"]), (": ", ""), entry.text
        )
    )


@memory_app.command("clear")
def memory_clear(
    robot: str | None = _ROBOT,
    memory_dir: str | None = _MEMORY_DIR,
    registry_dir: str | None = _REGISTRY_DIR,
    yes: bool = typer.Option(False, "--yes", "-y", help="Do not ask."),
) -> None:
    """Forget everything one robot remembers (deletes its memory file)."""
    mem = _memory_for(robot, memory_dir, registry_dir)
    n = len(mem.entries())
    if n == 0:
        ui.console.print(Text(f"{mem.robot_key}: nothing to forget", style=ui.STYLES["muted"]))
        return
    if not yes and not typer.confirm(f"forget {n} entries for {mem.robot_key}?"):
        raise typer.Exit()
    mem.clear()
    ui.console.print(
        Text.assemble((f"forgot {n} entries for ", ""), (mem.robot_key, ui.STYLES["key"]))
    )


# ── places and the robot's map ──────────────────────────────────────────────────────────

places_app = typer.Typer(
    name="places",
    help="The spots a robot keeps under names, each where it stood when it was marked: list "
    "them, mark one where the robot stands, forget one.",
    no_args_is_help=True,
)
app.add_typer(places_app, name="places", rich_help_panel="Memory")

MAP_WATCH_S = 1.0
"""How often `quackd map --watch` draws the map again, quackd's own number: often enough to
follow a walk, seldom enough to read."""

_CELLS = typer.Option(
    None,
    "--cells",
    help="How many columns wide to draw the map, when the robot keeps one: 10 to 80, and 40 when "
    "not given, as the map verb draws it.",
)


def _place_store_for(robot: str | None, memory_dir: str | None, registry_dir: str | None) -> Any:
    """One robot's places, keyed as its memory is, read with nothing connected."""
    from quackd.adapters.base import AdapterError
    from quackd.registry import Registry, RegistryError, resolve_robot_ref
    from quackd.space.places import PlaceStore

    try:
        resolved = resolve_robot_ref(robot, Registry(registry_dir))
    except (AdapterError, RegistryError) as e:
        # every other --robot command answers in one line, not a traceback
        _fail(str(e))
    return PlaceStore(resolved.memory_key, memory_dir)


def _room_hint(spec: Any, room: str | None) -> str | None:
    """Where a body with no places would have them: its simulator, given a room."""
    from quackd.adapters.base import AdapterError
    from quackd.adapters.factory import describe

    if room is not None:
        return None
    try:
        roomy = describe(spec, room=next(iter(BUILT_IN_ROOMS)))
    except AdapterError:
        return None  # its adapter lays out no room
    if "map" not in roomy.verb_names():
        return None
    rooms = ", ".join(BUILT_IN_ROOMS)
    return f"its simulator keeps a map, and places in it, in a room: --room {rooms}"


@contextlib.asynccontextmanager
async def _robot_places(
    robot: str | None,
    *,
    address: str | None,
    token: str | None,
    room: str | None,
    map_address: str | None,
    seed: int | None,
    memory_dir: str | None,
    registry_dir: str | None,
) -> AsyncIterator[tuple[str, Any, Any]]:
    """A robot connected for its map and its places and closed after, built from the robot
    flags `drive` takes too, with no camera: its name, the robot, and its places read against
    where it is now. A body that keeps no places is refused before anything connects."""
    from quackd.adapters.base import AdapterError
    from quackd.adapters.factory import describe, make_adapter
    from quackd.registry import Registry, RegistryError, resolve_robot_ref
    from quackd.space.places import PlaceBook, PlaceStore
    from quackd.space.room import RoomError, load_room
    from quackd.transport.base import spatial_of

    try:
        resolved = resolve_robot_ref(robot, Registry(registry_dir))
        if room is not None:
            load_room(room)
        static = describe(resolved.spec, room=room)
    except (AdapterError, RegistryError, RoomError) as e:
        _fail(str(e))
    name = resolved.entry.name if resolved.entry is not None else resolved.spec.key
    if "map" not in static.verb_names():
        _fail(
            f"{name} keeps no places: it does not say where it is",
            hint=_room_hint(resolved.spec, room),
        )
    kwargs = resolved.adapter_kwargs(address=address, token=token, map_address=map_address)
    # a map needs no camera, and a camera that will not open refuses the whole connect
    kwargs["camera_url"] = ()
    try:
        adapter = make_adapter(resolved.spec, seed=seed, room=room, **kwargs)
    except (AdapterError, ImportError) as e:
        _fail(str(e))
    try:
        await adapter.connect()
    except Exception as e:
        with contextlib.suppress(Exception):
            await adapter.close()
        _fail(f"{name} did not connect: {e}")
    try:
        source = spatial_of(adapter)
        if source is None:
            _fail(f"{name} does not say where it is on this connection, so it has no map or places")
        else:
            yield name, adapter, PlaceBook(PlaceStore(resolved.memory_key, memory_dir), source)
    finally:
        await adapter.close()


def _print_map(summary: str, *, again: bool = False) -> None:
    """The map verb's lines as they are: a grid is not wrapped, and a name is not markup."""
    if again and ui.console.is_terminal:
        ui.console.clear()
    elif again:
        ui.console.print()
    ui.console.print(Text(summary), soft_wrap=True)


async def _map_watch_pause() -> None:
    """The wait between two drawings of `quackd map --watch`. The seam tests replace."""
    await asyncio.sleep(MAP_WATCH_S)


async def _wipe_map(name: str, book: Any) -> None:
    """Forget the robot's map, once the person at the terminal has said yes to what it orphans.
    The question is asked off the event loop, so the robot's own links keep being read while
    the person thinks."""
    source = book.source
    if not source.can_wipe:
        _fail(f"{source.describe()} keeps no map to wipe")
    frame = book.frame()
    doomed = sorted(
        (p.name for p in book.store.places() if frame is not None and p.frame == frame.id),
        key=str.lower,
    )
    orphans = (
        f"and orphans {_plural(len(doomed), 'place')}: {', '.join(doomed)}"
        if doomed
        else "and orphans no place"
    )
    question = f"wipe {name}'s map? It forgets every stop it has looked from, {orphans}"
    if not await asyncio.to_thread(typer.confirm, question, default=False):
        ui.console.print(Text("nothing wiped", style=ui.STYLES["muted"]))
        return
    forgot = await source.wipe()
    book.frame()  # the store keeps the map that ended as wiped, so its places stay orphaned
    ui.console.print(
        _ok_line(
            f"wiped {name}'s map: it forgot {_plural(forgot, 'stop')}, and "
            f"{_plural(len(doomed), 'place')} {'is' if len(doomed) == 1 else 'are'} orphaned"
        )
    )


@app.command("map", rich_help_panel="Memory")
def map_command(
    robot: str | None = _ROBOT,
    address: str | None = _ADDR,
    token: str | None = _TOKEN,
    room: str | None = _ROOM,
    map_address: str | None = _MAP_ADDR,
    seed: int | None = _SEED,
    cells: int | None = _CELLS,
    watch: bool = typer.Option(
        False, "--watch", help="Draw it again every second, as the robot moves, until Ctrl+C."
    ),
    wipe: bool = typer.Option(
        False,
        "--wipe",
        help="Forget the robot's map first, every stop it has looked from, which orphans every "
        "place marked in it. Asked at the terminal, naming those places, and refused without "
        "one: a map is never wiped by a pilot or a script.",
    ),
    memory_dir: str | None = _MEMORY_DIR,
    registry_dir: str | None = _REGISTRY_DIR,
) -> None:
    """Where a robot is, its places with how far and which way, and its map as it has seen it.

    It connects to the robot, with no camera, and reads it as the `map` verb does: nothing
    moves. The places are the ones kept beside the robot's memory."""
    from quackd.verbs.places import MapParams, map_verb
    from quackd.verbs.registry import VerbContext

    try:
        params = MapParams() if cells is None else MapParams(cells_across=cells)
    except ValidationError as e:
        _fail(f"--cells: {e.errors()[0]['msg']}")
        return
    if wipe and not _can_prompt():
        # refused before anything connects, so nothing is half done
        _fail(
            "--wipe asks the person at the terminal first, and there is no terminal here: run it "
            "in one",
            hint="a map is never wiped by a pilot or a script",
        )
        return

    async def session() -> None:
        async with _robot_places(
            robot,
            address=address,
            token=token,
            room=room,
            map_address=map_address,
            seed=seed,
            memory_dir=memory_dir,
            registry_dir=registry_dir,
        ) as (name, adapter, book):
            ctx = VerbContext(transport=adapter, places=book)
            if wipe:
                await _wipe_map(name, book)
            result = await map_verb(ctx, params)
            if not result.ok:
                _fail(result.summary)
            _print_map(result.summary)
            if not watch:
                return
            try:
                while True:
                    await _map_watch_pause()
                    _print_map((await map_verb(ctx, params)).summary, again=True)
            except asyncio.CancelledError:
                # Ctrl+C: the watch ends here, and the robot is closed on the way out
                ui.console.print(Text("stopped watching", style=ui.STYLES["muted"]))

    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(session())


@places_app.command("list")
def places_list(
    robot: str | None = _ROBOT,
    memory_dir: str | None = _MEMORY_DIR,
    registry_dir: str | None = _REGISTRY_DIR,
) -> None:
    """Every place one robot keeps, with the frame it was marked in.

    It reads the file and connects nothing, so it says nothing of where the robot is now:
    `quackd map` says which places can be walked to."""
    import math

    store = _place_store_for(robot, memory_dir, registry_dir)
    places = sorted(store.places(), key=lambda p: p.name.lower())
    ui.console.print(
        Text.assemble(
            (store.robot_key, ui.STYLES["key"]),
            (f"  {_plural(len(places), 'place')}  ", ""),
            (str(store.path), ui.STYLES["muted"]),
        )
    )
    if not places:
        ui.console.print(Text("no places kept yet", style=ui.STYLES["muted"]))
        return
    wiped = {record.id for record in store.frames() if record.wiped}
    table = ui.table("places, each in the frame it was marked in")
    table.add_column("name", no_wrap=True)
    table.add_column("x, y (m)", no_wrap=True, justify="right")
    table.add_column("facing", no_wrap=True, justify="right")
    table.add_column("by", no_wrap=True)
    # an id is compared, never read for its parts, so a long one folds rather than losing its end
    table.add_column("frame", style=ui.STYLES["muted"], overflow="fold")
    table.add_column("marked", no_wrap=True)
    table.add_column("note", ratio=1)
    for place in places:
        table.add_row(
            Text(place.name),
            Text(f"{place.x:.2f}, {place.y:.2f}"),
            Text(f"{math.degrees(place.yaw):.0f}°"),
            Text(place.kind),
            Text(place.frame + (", its map wiped" if place.frame in wiped else "")),
            Text(datetime.fromtimestamp(place.ts).strftime("%Y-%m-%d")),
            Text(place.note or ""),
        )
    ui.console.print(table)
    ui.console.print(
        Text("quackd map says which of them can be walked to now", style=ui.STYLES["muted"])
    )


@places_app.command("add")
def places_add(
    name: str = typer.Argument(..., help="What to call the spot the robot stands on, e.g. sofa."),
    note: str | None = typer.Option(None, "--note", help="What is there, in a few words."),
    robot: str | None = _ROBOT,
    address: str | None = _ADDR,
    token: str | None = _TOKEN,
    room: str | None = _ROOM,
    map_address: str | None = _MAP_ADDR,
    seed: int | None = _SEED,
    memory_dir: str | None = _MEMORY_DIR,
    registry_dir: str | None = _REGISTRY_DIR,
) -> None:
    """Mark the spot the robot stands on now under a name, as the `mark_place` verb does.

    It connects to the robot and reads where it is from the robot's own reading, never from
    coordinates anybody types. Nothing moves."""
    from quackd.verbs.places import MarkPlaceParams, mark_place
    from quackd.verbs.registry import VerbContext

    try:
        params = MarkPlaceParams(name=name, note=note)
    except ValidationError as e:
        error = e.errors()[0]
        _fail(f"{'name' if error['loc'][0] == 'name' else '--note'}: {error['msg']}")
        return

    async def session() -> None:
        async with _robot_places(
            robot,
            address=address,
            token=token,
            room=room,
            map_address=map_address,
            seed=seed,
            memory_dir=memory_dir,
            registry_dir=registry_dir,
        ) as (who, adapter, book):
            result = await mark_place(VerbContext(transport=adapter, places=book), params)
            if not result.ok:
                _fail(result.summary)
            ui.console.print(_ok_line(f"{who}: {result.summary}"))

    asyncio.run(session())


@places_app.command("rm")
def places_rm(
    name: str = typer.Argument(..., help="The place to forget."),
    robot: str | None = _ROBOT,
    memory_dir: str | None = _MEMORY_DIR,
    registry_dir: str | None = _REGISTRY_DIR,
) -> None:
    """Forget one place by name. It reads and writes the file and connects nothing."""
    store = _place_store_for(robot, memory_dir, registry_dir)
    old = store.forget(name)
    if old is None:
        known = ", ".join(sorted((p.name for p in store.places()), key=str.lower)) or "none"
        _fail(f"{store.robot_key} keeps no place called '{name}' (places: {known})")
        return
    ui.console.print(_ok_line(f"{store.robot_key}: forgot '{old.name}'"))


# ── lan (quackd[lan]) ───────────────────────────────────────────────────────────────────


# ── Robot Passport (docs/passport/) ──────────────────────────────────────────────────────

passport_app = typer.Typer(
    name="passport",
    help="Robot Passport: what a robot tells a robot it has never met. Check anybody's "
    "passport the way a robot reads it. docs/passport/ has the protocol.",
    no_args_is_help=True,
)
app.add_typer(passport_app, name="passport", rich_help_panel="Robots")

#: Where a robot's onboard service listens for talk unless its robot.yaml says otherwise, and
#: where `quackd passport check HOST:PORT` looks when it is given no port.
PASSPORT_PORT = 8790


def _shown(value: Any) -> Any:
    """A view with every string in it safe to print (`ui.printable`), member names included,
    since a passport is text a stranger wrote."""
    if isinstance(value, str):
        return ui.printable(value)
    if isinstance(value, dict):
        return {ui.printable(str(k)): _shown(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_shown(v) for v in value]
    return value


def _figure_text(figure: dict[str, Any], unit: str) -> str:
    text = f"{figure['value']:g} {unit}"
    return f"{text} ({figure['confidence']})" if figure.get("confidence") else text


def _passport_rows(view: dict[str, Any]) -> list[tuple[str, Any]]:
    """A reading as the rows a person scans, each claim where a reader would look for it."""
    rows: list[tuple[str, Any]] = []
    short = view["short_id"] or "none: it holds no key"
    rows.append(("id", Text(f"{view['id']}  (short id {short})", style=ui.STYLES["muted"])))
    rows.append(("claim", view["provenance"]))
    if "tier" in view:
        rows.append(("tier", f"{view['tier']}: {view['tier_why']}"))
    maker = view["maker"]
    if maker:
        rows.append(("maker", " ".join(str(maker[k]) for k in ("vendor", "model") if k in maker)))
    owner = view["owner"]
    named = [str(owner[k]) for k in ("label", "contact") if k in owner]
    rows.append(("owner", ", ".join([*named, f"key {view['owner_short_id']}"])))
    board = view["board"]
    if board:
        rows.append(
            (
                "board",
                f"{board.get('kind')}: "
                + ", ".join(str(board[k]) for k in ("model", "soc") if k in board),
            )
        )
    body = view["body"]
    if body:
        worded = {"mobility": "fixed in place", "manipulator": "nothing to hold with"}
        parts = [
            worded[k] if body[k] == "none" and k in worded else body[k]
            for k in ("embodiment", "mobility", "manipulator")
            # a wheeled body that moves on wheels says wheeled once, not twice
            if k in body and not (k == "mobility" and body[k] == body.get("embodiment"))
        ]
        for name, unit in (("mass_kg", "kg"), ("height_m", "m"), ("payload_kg", "kg payload")):
            if name in body:
                parts.append(_figure_text(body[name], unit))
        if "max_speed_mps" in body:
            parts.append(f"{body['max_speed_mps']['value']:g} m/s at most")
        if body.get("tethered"):
            parts.append("tethered")
        rows.append(("body", ", ".join(str(p) for p in parts)))
        if body.get("sensors"):
            rows.append(("sensors", ", ".join(body["sensors"])))
    if view["skills"]:
        rows.append(("skills", ", ".join(view["skills"])))
    if view["courtesy"]:
        rows.append(("courtesy", ", ".join(view["courtesy"])))
    for i, line in enumerate(view["cannot"]):
        rows.append(("cannot" if i == 0 else "", line))
    safety = view["safety"]
    stops = "stops on silence" if safety["stops_on_silence"] else "does not stop on silence"
    said = f", {safety['how_to_stop']}" if safety["how_to_stop"] else ""
    rows.append(("safety", f"{stops}, e-stop: {safety['estop']}{said}"))
    rows.append(("valid", f"{view['issued']} to {view['expires']}"))
    rows.append(("digest", Text(view["digest"][:16], style=ui.STYLES["muted"])))
    return rows


def _print_reading(view: dict[str, Any], *, title: str) -> None:
    view = _shown(view)
    ui.console.print(ui.run_header(f"{title} {view['name']}", _passport_rows(view)))
    for warning in view.get("warnings", []):
        ui.console.print(_warn_line(warning), soft_wrap=True)
    if not view["talk_allowed"]:
        ui.console.print(_warn_line("a required extension is not understood, so no talk"))
    for note in view["notes"]:
        ui.console.print(Text(f"  {note}", style=ui.STYLES["muted"]), soft_wrap=True)


@passport_app.command("check")
def passport_check(
    source: str = typer.Argument(
        ...,
        help="A passport to check: a file, - for stdin, an http(s) URL, or a robot's "
        "address as HOST:PORT or an IP address.",
    ),
    as_json: bool = _JSON,
) -> None:
    """Check a passport the way a robot does, and show what it proves and what it claims.

    Works on anybody's: a file, stdin, a URL, or a robot on this network by its address. A
    refusal names the rule of the specification it broke, and exits 1."""
    from quackd.passport import PassportError, document, fetch

    try:
        url = _passport_url(source)
        if url is not None:
            data = fetch.fetch_passport(url)
        elif source == "-":
            data = sys.stdin.buffer.read(fetch.MAX_WIRE_BYTES + 1)
        else:
            path = Path(source)
            if not path.is_file():
                _fail(f"{ui.printable(source)}: no such file")
            with path.open("rb") as f:
                data = f.read(fetch.MAX_WIRE_BYTES + 1)
        reading = document.read(data)
    except PassportError as e:
        _fail(
            f"refused under {e.rule}: {ui.printable(str(e))}",
            hint="docs/passport/spec.md says what each rule asks",
        )
        return
    view = reading.view()
    if as_json:
        print(json.dumps(view))
        return
    _print_reading(view, title="checked")


#: A robot's address as `passport check` takes it: an IPv4 or bracketed IPv6 address, with or
#: without a port, or any host name with one. A bare name is read as a file.
_ROBOT_ADDRESS = re.compile(
    r"(?:\d{1,3}(?:\.\d{1,3}){3}|\[[0-9A-Fa-f:.]+\])(?::\d{1,5})?|[A-Za-z0-9.-]+:\d{1,5}"
)


def _passport_url(source: str) -> str | None:
    """The URL `passport check` fetches for `source`: a URL as typed, a robot's address as its
    well-known URL, and None for stdin or a file on this computer."""
    from quackd.passport import fetch

    if source.startswith(("http://", "https://")):
        return fetch.target(source)
    if source != "-" and not Path(source).exists() and _ROBOT_ADDRESS.fullmatch(source):
        has_port = source.rsplit("]", 1)[-1].count(":") == 1
        return fetch.target(f"http://{source}" if has_port else f"http://{source}:{PASSPORT_PORT}")
    return None


# ── quackd onboard: Robot Passport on the robot itself (docs/passport/quackd.md) ─────────

onboard_app = typer.Typer(
    name="onboard",
    help="Robot Passport on the robot itself: set this robot up once, and it introduces itself "
    "to robots that come close, checks theirs, and lets its brain talk to the ones you trust. "
    "docs/passport/quackd.md has all of it.",
)
app.add_typer(onboard_app, name="onboard", rich_help_panel="Robots")

_ONBOARD_DIR = typer.Option(
    None,
    "--dir",
    help="Where this robot's file, keys and records live (default ~/.quackd/onboard, or "
    "QUACKD_ONBOARD_DIR).",
)
_ONBOARD_BOARD = typer.Option(
    None,
    "--board",
    help="The computer this is: a Jetson is found by itself; sim stands in for one on a laptop, "
    "and its passport says so.",
)
_PASSPHRASE_FILE = typer.Option(
    None,
    "--passphrase-file",
    help="Read your owner passphrase from this file instead of asking, for a script. Delete the "
    "file afterwards.",
)
_DAYS = typer.Option(
    365, "--days", min=1, max=400, help="How long your signature holds, 1 to 400 days."
)


class _Prompts:
    """The wizard's questions at a terminal. `--yes` takes every default and asks nothing, and
    a passphrase is never a default: a script hands one over with `--passphrase-file`."""

    def __init__(self, yes: bool) -> None:
        self._yes = yes

    def text(self, question: str, default: str | None = None) -> str:
        if self._yes:
            return default or ""
        return str(typer.prompt(question, default=default or "", show_default=bool(default)))

    def secret(self, question: str, *, twice: bool = False) -> str:
        if self._yes or not sys.stdin.isatty():
            _fail(
                "your owner passphrase is asked for at a terminal, and this is none",
                hint="give --passphrase-file FILE, and delete the file afterwards",
            )
        return str(typer.prompt(question, hide_input=True, confirmation_prompt=twice))

    def yes(self, question: str, default: bool = False) -> bool:
        if self._yes:
            return True
        return bool(typer.confirm(question, default=default))


def _onboard_home(directory: str | None) -> Any:
    from quackd.onboard.store import OnboardDir, OnboardDirError

    try:
        return OnboardDir(directory)
    except OnboardDirError as e:
        _fail(str(e))
        raise  # unreachable: _fail exits


def _onboard_board(name: str | None) -> tuple[Any, Any]:
    from quackd.boards import NotARobot, UnknownBoard, Unsupported, detect

    try:
        return detect(name)
    except (NotARobot, Unsupported, UnknownBoard) as e:
        _fail(str(e))
        raise  # unreachable: _fail exits


def _onboard_file(home: Any) -> Any:
    from quackd.onboard import config

    try:
        return config.load(home.robot_yaml)
    except config.FileError as e:
        _fail(str(e), hint="quackd onboard writes it, and docs/passport/quackd.md says every key")
        raise  # unreachable: _fail exits


def _onboard_manifest(robot: Any) -> Any:
    """The manifest of the body quackd drives for this robot, or None for one it only talks for."""
    if robot.robot is None:
        return None
    from quackd.adapters.factory import describe, parse_robot_spec

    return describe(parse_robot_spec(robot.robot.adapter))


def _brain_key_variable(llm: str | None) -> str | None:
    """Which environment variable the brain's key goes in, or None for a brain that needs none."""
    from quackd.onboard.preflight import key_variable

    return key_variable(llm) if llm else None


def _refuse_root() -> None:
    from quackd.onboard.wizard import WizardError, refuse_root

    try:
        refuse_root()
    except WizardError as e:
        _fail(str(e))


def _tell_service(home: Any, op: dict[str, Any]) -> dict[str, Any] | None:
    """`op` to the running service, or None when none is running, which is fine here."""
    from quackd.onboard.control import ControlError, request

    try:
        return request(home.control_json, op)
    except ControlError:
        return None


@onboard_app.callback(invoke_without_command=True)
def onboard(
    ctx: typer.Context,
    board: str | None = _ONBOARD_BOARD,
    directory: str | None = _ONBOARD_DIR,
    file: Path | None = typer.Option(
        None, "--file", help="Start from this robot file instead of asking."
    ),
    owner_key: Path | None = typer.Option(
        None,
        "--owner-key",
        help="The owner key file from your first robot, so robots you own know each other; it "
        "stays sealed with your passphrase.",
    ),
    passphrase_file: Path | None = _PASSPHRASE_FILE,
    days: int = _DAYS,
    no_service: bool = typer.Option(
        False,
        "--no-service",
        help="Sign and write everything, install no service, and say how to start it.",
    ),
    yes: bool = typer.Option(False, "--yes", help="Take the default for every question."),
) -> None:
    """Put a passport on this robot: one file, its keys, its owner's signature and a service.

    Run with nothing after it, it asks what only you know, writes robot.yaml, makes the robot's
    key and your owner key (sealed with a passphrase you choose), signs, and starts the service
    that meets other robots. Run again, it signs only when robot.yaml changed or the signature
    runs out within 30 days."""
    if ctx.invoked_subcommand is not None:
        return
    from quackd.onboard import config, wizard
    from quackd.onboard.control import write_private

    _refuse_root()
    home = _onboard_home(directory)
    chosen, facts = _onboard_board(board)
    release = f", {facts.release}" if facts.release else ""
    if facts.kind == "sim":
        ui.console.print(_ok_line("the sim board: this computer stands in for a robot's own"))
    else:
        ui.console.print(_ok_line(f"this computer is {facts.model} ({facts.kind}{release})"))
    ask = _Prompts(yes)
    home.ensure()
    if file is not None:
        try:
            text = file.read_text(encoding="utf-8")
            robot = config.loads(text, str(file))
        except (OSError, config.FileError) as e:
            _fail(str(e))
            return
        write_private(home.robot_yaml, text)
    elif home.robot_yaml.exists():
        robot = _onboard_file(home)
    else:
        default_name = facts.kind if facts.kind != "sim" else "robot-01"
        text = wizard.ask_for_file(
            ask,
            default_name=f"{default_name}-01".replace("-01-01", "-01"),
            port=0 if facts.kind == "sim" else 8790,
        )
        try:
            robot = config.loads(text)
        except config.FileError as e:
            _fail(str(e))
            return
        write_private(home.robot_yaml, text)
        ui.console.print(_ok_line(f"wrote {home.robot_yaml}"))
    if owner_key is not None and not home.owner_key.exists():
        passphrase = wizard.owner_passphrase(ask, passphrase_file, new=False)
        home.adopt_owner_key(owner_key, passphrase)
        ui.console.print(_ok_line(f"took your owner key from {owner_key}"))
    variable = _brain_key_variable(robot.brain.llm)
    if variable and not home.robot_env.exists() and not yes:
        key = ask.secret(f"the key for its brain ({variable}); nothing, for a server needing none")
        if key:
            wizard.write_key_file(home.robot_env, variable, key)
    manifest = _onboard_manifest(robot)
    why = wizard.needs_signing(home, robot, facts.passport(), manifest=manifest)
    if why is None:
        ui.console.print(_ok_line("its passport is signed and current"))
    else:
        ui.console.print(Text(f"signing: {why}", style=ui.STYLES["muted"]))
        try:
            signed = wizard.sign(
                home,
                robot,
                facts.passport(),
                ask=ask,
                passphrase_file=passphrase_file,
                days=days,
                manifest=manifest,
            )
        except Exception as e:  # a wrong passphrase, a key file that is not one: said plainly
            _fail(str(e))
            return
        made = (
            " (a new owner key: keep its passphrase, nothing recovers it)"
            if signed.owner_key_made
            else ""
        )
        ui.console.print(_ok_line(f"signed {signed.path}{made}"))
        _tell_service(home, {"op": "reload"})
    on_a_robot = chosen.radio_kind == "bluez" and sys.platform.startswith("linux")
    if on_a_robot and not no_service:
        try:
            wizard.offer_privacy(
                say=lambda line: ui.console.print(line), ask=ask, run=wizard.run_command
            )
            installed = wizard.install_service(
                home, say=lambda line: ui.console.print(line), ask=ask, run=wizard.run_command
            )
        except wizard.WizardError as e:
            _fail(str(e))
            return
        if installed:
            ui.console.print(_ok_line("the service is running: quackd onboard status says how"))
        return
    sim = " --board sim" if chosen.name == "sim" else ""
    ui.console.print(
        Text(
            f"start it with: quackd onboard serve --dir {home.root}{sim}", style=ui.STYLES["muted"]
        )
    )


@onboard_app.command("sign")
def onboard_sign(
    board: str | None = _ONBOARD_BOARD,
    directory: str | None = _ONBOARD_DIR,
    passphrase_file: Path | None = _PASSPHRASE_FILE,
    days: int = _DAYS,
) -> None:
    """Sign the passport section of robot.yaml again, after a change or before it expires."""
    from quackd.onboard import wizard

    home = _onboard_home(directory)
    _chosen, facts = _onboard_board(board)
    robot = _onboard_file(home)
    try:
        signed = wizard.sign(
            home,
            robot,
            facts.passport(),
            ask=_Prompts(False),
            passphrase_file=passphrase_file,
            days=days,
            manifest=_onboard_manifest(robot),
        )
    except Exception as e:  # a wrong passphrase above all
        _fail(str(e))
        return
    ui.console.print(_ok_line(f"signed {signed.path}, good for {days} days"))
    told = _tell_service(home, {"op": "reload"})
    if told is not None:
        ui.console.print(_ok_line("the running service serves it from now on"))


@onboard_app.command("show")
def onboard_show(
    directory: str | None = _ONBOARD_DIR,
    as_json: bool = typer.Option(False, "--json", help="The passport's view, as one line of JSON."),
    as_jws: bool = typer.Option(False, "--jws", help="The signed passport, as its compact JWS."),
) -> None:
    """This robot's passport, read the way another robot reads it."""
    from quackd.passport import PassportError, document

    home = _onboard_home(directory)
    if not home.passport_jws.exists():
        _fail("this robot has no signed passport yet", hint="run quackd onboard")
        return
    token = home.passport_jws.read_text(encoding="utf-8").strip()
    if as_jws:
        print(token)
        return
    try:
        reading = document.read_jws(token)
    except PassportError as e:
        _fail(f"its passport does not check out: refused under {e.rule}: {e}")
        return
    if as_json:
        print(json.dumps(reading.view()))
        return
    _print_reading(reading.view(), title="passport")


@onboard_app.command("status")
def onboard_status(
    directory: str | None = _ONBOARD_DIR,
    as_json: bool = typer.Option(False, "--json", help="The status, as one line of JSON."),
) -> None:
    """What this robot's service is doing: its passport, its radio, the robots close by, and the
    run in hand."""
    home = _onboard_home(directory)
    reply = _tell_service(home, {"op": "status"})
    if reply is None:
        _fail(
            "no service is running for this robot",
            hint="sudo systemctl start quackd-onboard, or quackd onboard serve in a terminal; "
            "journalctl -u quackd-onboard says why one stopped",
        )
        return
    reply.pop("ev", None)
    if as_json:
        print(json.dumps(reply))
        return
    ui.console.print(ui.run_header(f"onboard {reply.get('robot')}", _status_rows(home, reply)))


def _radio_rows(radio: dict[str, Any]) -> list[tuple[str, Any]]:
    """The radio's lines, as `quackd.radio.statusview` words them, as this panel's rows; the
    privacy line in the warning's colour while the address is not private."""
    from quackd.radio.statusview import render_radio

    rows: list[tuple[str, Any]] = []
    for line in render_radio(radio):
        key, value = line[:10].strip(), line[10:].strip()
        if key == "privacy" and not (radio.get("privacy") or {}).get("private"):
            rows.append((key, Text(value, style=ui.STYLES["warn"])))
        elif key:
            rows.append((key, value))
        elif rows:
            rows.append(("", value))
    rows.append(("lan", "on the LAN, with a record" if radio.get("on_lan") else "no LAN record"))
    return rows


def _status_rows(home: Any, reply: dict[str, Any]) -> list[tuple[str, Any]]:
    """The service's status as a person reads it: the passport and what is not signed yet, the
    board and brain, the radio and every robot it hears, the sessions, the run, the last
    meetings."""
    from quackd.onboard import config, wizard

    passport = reply.get("passport", {})
    radio = reply.get("radio") or {}
    run = reply.get("run") or {}
    board = {k: v for k, v in (reply.get("board") or {}).items() if k != "release"}
    rows: list[tuple[str, Any]] = [
        (
            "passport",
            f"checks out, until {passport.get('expires')}"
            if passport.get("ok")
            else Text(f"OFF: {passport.get('why')}", style=ui.STYLES["warn"]),
        )
    ]
    with contextlib.suppress(config.FileError, OSError, ValueError):
        why = wizard.needs_signing(home, config.load(home.robot_yaml), board)
        if why is not None and passport.get("ok"):
            said = f"{why}, so other robots still see what was signed: quackd onboard sign"
            rows.append(("unsigned", Text(said, style=ui.STYLES["warn"])))
    shown_board = reply.get("board") or {}
    rows.append(
        (
            "board",
            f"{shown_board.get('kind')}: "
            + ", ".join(
                str(shown_board[k]) for k in ("model", "soc", "release") if k in shown_board
            ),
        )
    )
    rows.append(("brain", str((reply.get("brain") or {}).get("llm") or "none: no meetings")))
    rows += _radio_rows(radio)
    for robot in reply.get("nearby") or []:
        rows.append(("talking", f"{robot.get('shown')} ({robot.get('tier')})"))
    title = f", {run['title']}" if run.get("title") else ""
    rows.append(("run", f"{run.get('run', 'idle')}{title}"))
    for row in _met_rows(home, 5):
        if "refused" in row:
            rows.append(("refused", f"{row.get('at', '')} {ui.printable(str(row.get('why')))}"))
        else:
            name = ui.printable(str(row.get("name", "")))
            rows.append(("met", f"{row.get('at', '')} {name} ({row.get('tier')})"))
    return rows


@onboard_app.command("stop")
def onboard_stop(directory: str | None = _ONBOARD_DIR) -> None:
    """Stop the run in hand, a goal or a meeting; the service keeps running."""
    home = _onboard_home(directory)
    reply = _tell_service(home, {"op": "stop"})
    if reply is None:
        _fail("no service is running for this robot")
        return
    ui.console.print(
        _ok_line("stopped the run in hand" if reply.get("was") else "nothing was running")
    )


@onboard_app.command("met")
def onboard_met(
    directory: str | None = _ONBOARD_DIR,
    limit: int = typer.Option(20, "--limit", min=1, max=200, help="How many, newest last."),
) -> None:
    """The robots this one met lately, and the ones it refused and why."""
    home = _onboard_home(directory)
    rows = _met_rows(home, limit)
    t = ui.table(f"met by {home.root.name} ({len(rows)})")
    for column in ("when", "robot", "short id", "tier or refusal"):
        t.add_column(column)
    for row in rows:
        # a minute is what a person matches a meeting by, and 8 of a short id what pin takes
        when = str(row.get("at", "")).replace("T", " ")[:16]
        if "refused" in row:
            t.add_row(
                when,
                Text(ui.printable(str(row["refused"]))),
                "",
                Text(ui.printable(str(row.get("why", ""))), style=ui.STYLES["warn"]),
            )
        else:
            tier = Text(str(row.get("tier", "")))
            for warning in row.get("warn") or []:
                tier.append(f"\n{ui.printable(str(warning))}", style=ui.STYLES["warn"])
            t.add_row(
                when,
                Text(ui.printable(str(row.get("name", "")))),
                str(row.get("short_id", ""))[:8],
                tier,
            )
    ui.console.print(t)


def _met_rows(home: Any, limit: int) -> list[dict[str, Any]]:
    try:
        lines = home.met_log.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    rows: list[dict[str, Any]] = []
    for line in lines[-limit:]:
        with contextlib.suppress(ValueError):
            rows.append(json.loads(line))
    return rows


def _met_robot(home: Any, which: str) -> dict[str, Any]:
    """The robot `which` names among those met: a short-id prefix of four or more, a name it
    gave, or a did. Refused when it names none, or more than one."""
    wanted = which.strip()
    found: dict[str, dict[str, Any]] = {}
    for row in _met_rows(home, 10_000):
        if "id" not in row:
            continue
        short = str(row.get("short_id", ""))
        if wanted in (row["id"], row.get("name")) or (
            len(wanted) >= 4 and short.startswith(wanted)
        ):
            found[row["id"]] = row
    if not found:
        _fail(f"this robot met no robot called {wanted!r}", hint="quackd onboard met lists them")
    if len(found) > 1:
        _fail(f"{wanted!r} names {len(found)} robots met: give more of the short id")
    return next(iter(found.values()))


@onboard_app.command("pin")
def onboard_pin(
    which: str = typer.Argument(
        ..., help="A robot met: a short id of 4 or more, its name, or its did."
    ),
    owner: bool = typer.Option(
        False, "--owner", help="Trust every robot its owner signed, not only this one."
    ),
    directory: str | None = _ONBOARD_DIR,
    yes: bool = typer.Option(False, "--yes", help="Pin without asking first."),
) -> None:
    """Trust a robot this one met, so its words reach this robot's brain; --owner trusts every
    robot its owner signed."""
    from datetime import date

    from quackd.onboard import config

    home = _onboard_home(directory)
    robot = _met_robot(home, which)
    if owner:
        if not robot.get("owner_id"):
            _fail("this meeting kept no owner key; meet it again with this quackd")
        pin = config.Pin(
            kind="owner",
            id=robot["owner_id"],
            name=str(robot.get("owner_label") or robot.get("name"))[:64],
            pinned=date.today().isoformat(),
        )
    else:
        pin = config.Pin(
            kind="robot",
            id=robot["id"],
            name=str(robot.get("name"))[:64],
            pinned=date.today().isoformat(),
            owner=robot.get("owner_id") or None,
        )
    what = f"the owner {pin.name}" if owner else f"the robot {pin.name}"
    for warning in robot.get("warn") or []:
        ui.console.print(_warn_line(ui.printable(str(warning))))
    if not yes and not typer.confirm(
        f"trust {what}, so its words reach this robot's brain?", default=False
    ):
        ui.console.print(Text(f"not pinned: {what}", style=ui.STYLES["muted"]))
        return
    try:
        config.add_pin(home.robot_yaml, pin)
    except config.FileError as e:
        _fail(str(e))
        return
    ui.console.print(_ok_line(f"pinned {what} in {home.robot_yaml}"))
    if _tell_service(home, {"op": "reload"}) is not None:
        ui.console.print(_ok_line("the running service trusts it from now on"))


@onboard_app.command("forget")
def onboard_forget(
    which: str = typer.Argument(
        ..., help="A pinned robot or owner: its name, a short id of 4 or more, or its did."
    ),
    directory: str | None = _ONBOARD_DIR,
) -> None:
    """Stop trusting a pinned robot or owner."""
    from quackd.onboard import config
    from quackd.passport.keys import short_id_of

    home = _onboard_home(directory)
    robot = _onboard_file(home)
    wanted = which.strip()
    matches = [
        p
        for p in robot.trust.pins
        if wanted in (p.id, p.name)
        or (len(wanted) >= 4 and str(short_id_of(p.id)).startswith(wanted))
    ]
    if len(matches) != 1:
        _fail(f"{wanted!r} names {len(matches)} pins: give its did or more of its short id")
        return
    config.remove_pin(home.robot_yaml, matches[0].id)
    ui.console.print(_ok_line(f"forgot {matches[0].kind} {matches[0].name}"))
    _tell_service(home, {"op": "reload"})


@onboard_app.command("goal")
def onboard_goal(
    text: str = typer.Argument(..., help="The goal, in a sentence."),
    directory: str | None = _ONBOARD_DIR,
    yes: bool = typer.Option(False, "--yes", help="Answer go to every question the run asks."),
    max_steps: int | None = typer.Option(
        None, "--max-steps", min=1, max=500, help="At most this many steps for the goal."
    ),
) -> None:
    """Give this robot a goal. Its run is shown here, its questions are asked here, Ctrl-C stops
    it, and robots that come close meanwhile appear in it."""
    from quackd.onboard.control import ControlError, connect

    home = _onboard_home(directory)
    op: dict[str, Any] = {"op": "goal", "text": text, "yes": yes}
    if max_steps is not None:
        op["max_steps"] = max_steps
    try:
        with connect(home.control_json) as conn:
            conn.send(op)
            code = _follow_goal(conn, yes=yes)
    except ControlError as e:
        _fail(str(e), hint="sudo systemctl start quackd-onboard, or quackd onboard serve")
        return
    raise typer.Exit(code=code)


def _follow_goal(conn: Any, *, yes: bool) -> int:
    """The goal's lines, as they come, with its questions answered here, until it ends."""
    stops = 0
    while True:
        try:
            line = conn.read()
        except KeyboardInterrupt:
            stops += 1
            conn.send({"op": "stop", **({"now": True} if stops > 1 else {})})
            ui.err_console.print(_warn_line("stop: stopping the run"))
            continue
        if line is None:
            ui.err_console.print(_warn_line("the service closed the connection"))
            return 1
        ev = line.get("ev")
        if ev == "end":
            outcome = str(line.get("outcome", ""))
            said = f"{outcome}: {line.get('reason', '')}".strip(": ")
            ui.console.print(_ok_line(said) if outcome == "success" else _warn_line(said))
            return int(line.get("exit_code", 0 if outcome == "success" else 1))
        if ev == "refused":
            ui.err_console.print(_warn_line(str(line.get("reason"))))
            return 1
        if ev == "ask":
            question = str(line.get("question"))
            answer = True if yes else typer.confirm(question, default=False)
            conn.send({"op": "answer", "id": line.get("id"), "yes": answer})
        elif ev == "status" and line.get("text"):
            ui.err_console.print(Text(str(line["text"]), style=ui.STYLES["muted"]))
        elif ev in ("notice", "say") and line.get("text"):
            ui.console.print(Text(ui.printable(str(line["text"]))))
        elif ev == "run_dir":
            ui.err_console.print(Text(f"record: {line.get('path')}", style=ui.STYLES["muted"]))


@onboard_app.command("uninstall")
def onboard_uninstall(
    directory: str | None = _ONBOARD_DIR,
    purge: bool = typer.Option(
        False, "--purge", help="Delete this robot's file, keys and records too."
    ),
) -> None:
    """Stop and remove the service; --purge deletes this robot's file and keys too."""
    import shutil

    from quackd.onboard import systemd, wizard

    home = _onboard_home(directory)
    commands = systemd.uninstall_commands()
    for command in commands:
        ui.console.print(f"  {systemd.shown(command)}")
    if typer.confirm("run them now?", default=True):
        for command in commands:
            wizard.run_command(command)
    if sys.platform.startswith("linux"):
        try:
            wizard.restore_privacy(
                say=lambda line: ui.console.print(line),
                ask=_Prompts(False),
                run=wizard.run_command,
            )
        except wizard.WizardError as e:
            _fail(str(e))
    if purge and typer.confirm(f"delete {home.root}, with the owner key in it?", default=False):
        shutil.rmtree(home.root, ignore_errors=True)
        ui.console.print(_ok_line(f"deleted {home.root}"))


@onboard_app.command("serve")
def onboard_serve(
    board: str | None = _ONBOARD_BOARD,
    directory: str | None = _ONBOARD_DIR,
    air: Path | None = typer.Option(
        None,
        "--air",
        help="The sim board's air: a directory every robot on this computer shares (default "
        "~/.quackd/sim-air).",
    ),
    path: str = typer.Option(
        "0,0@0",
        "--path",
        help="Where a sim robot stands and walks: x,y in metres at seconds, as 7,0@0 1,0@10.",
    ),
    network: str = typer.Option(
        "home",
        "--network",
        help="The sim LAN a sim robot is on; none for no LAN, so robots meet over the link.",
    ),
    seed: int = typer.Option(0, "--seed", help="The sim radio's noise, by its seed."),
    meet_within: float | None = typer.Option(
        None,
        "--meet-within",
        min=0.1,
        max=20.0,
        help="How close, in metres, another robot must come to be met (robot.yaml's, else 2).",
    ),
    radio: str | None = typer.Option(
        None, "--radio", help="off: no radio at all, and robots are met over the LAN only."
    ),
) -> None:
    """Run this robot's service here: what systemd runs on a robot, and what a laptop runs with
    --board sim. It stops on Ctrl-C."""
    from quackd.boards import NotARobot, UnknownBoard, Unsupported
    from quackd.onboard import config
    from quackd.onboard.service import AlreadyRunning, SimPlace, serve

    if radio not in (None, "off", "bluez", "sim"):
        _fail(f"--radio takes off, bluez or sim, not {radio!r}")
    _refuse_root()
    home = _onboard_home(directory)
    if not home.robot_yaml.exists():
        _fail(f"no robot here: {home.robot_yaml} is missing", hint="run quackd onboard first")
    sim = None
    if air is not None or board == "sim":
        from quackd.radio.sim import default_air_dir

        sim = SimPlace(air=air or default_air_dir(), path=path, network=network, seed=seed)

    def log(line: str) -> None:
        import time

        stamp = time.strftime("%H:%M:%S")
        print(f"{stamp} {ui.printable(line)}", flush=True)

    try:
        serve(home, board=board, log=log, sim=sim, radio=radio, meet_within_m=meet_within)
    except (NotARobot, Unsupported, UnknownBoard, AlreadyRunning, config.FileError) as e:
        _fail(str(e))


@onboard_app.command("preflight")
def onboard_preflight(
    board: str | None = _ONBOARD_BOARD,
    directory: str | None = _ONBOARD_DIR,
    as_json: bool = typer.Option(False, "--json", help="Every check, as one line of JSON."),
    live: bool = typer.Option(
        False,
        "--live",
        help="Also serve the advert and the link's service for two seconds, then take them down.",
    ),
) -> None:
    """Check this computer for a robot's onboard side: the board, its radio and Bluetooth, the
    network, the clock, the robot's file, keys and passport, its brain, and the service. It
    changes nothing but with --live, and exits 1 when something must be fixed first."""
    from quackd.onboard.preflight import preflight

    home = _onboard_home(directory)
    checks = preflight(
        home, board, service=lambda: _tell_service(home, {"op": "status"}), live=live
    )
    failed = any(check.state == "fail" for check in checks)
    if as_json:
        print(json.dumps({"ok": not failed, "checks": [check.row() for check in checks]}))
    else:

        def table(g: Any) -> Any:
            marks = {"ok": (g.ok, "green"), "warn": (g.warn, "yellow"), "fail": (g.fail, "red")}
            t = ui.table(f"preflight for {home.root}")
            for column in ("", "check", "what it found"):
                t.add_column(column)
            for check in checks:
                mark, style = marks[check.state]
                said = Text(ui.printable(check.detail))
                if check.hint:
                    said.append(f"\n{ui.printable(check.hint)}", style=ui.STYLES["muted"])
                t.add_row(Text(mark, style=style), check.name, said)
            return t

        ui.console.print(ui.Deferred(table))
    if failed:
        raise typer.Exit(code=1)


@onboard_app.command("demo")
def onboard_demo(
    offline: bool = typer.Option(
        False, "--offline", help="Fly both robots on the scripted pilot: no key, no network."
    ),
    llm: str | None = typer.Option(
        None, "--llm", help="Both robots' brain, as --llm spells it, local:MODEL with --base-url."
    ),
    base_url: str | None = typer.Option(
        None, "--base-url", help="The OpenAI-compatible server both brains use."
    ),
    act: list[str] | None = typer.Option(
        None,
        "--act",
        help="Only this act: strangers, appointment, office, tampered or injection. Again for "
        "another.",
    ),
    fast: bool = typer.Option(False, "--fast", help="Each scene runs four times faster."),
    keep: Path | None = typer.Option(
        None, "--keep", help="Keep both robots' files and records in this folder."
    ),
    as_json: bool = typer.Option(False, "--json", help="What each act found, as one line."),
) -> None:
    """Two simulated robots on this computer meet, check each other's passports and talk: a
    mock Unitree G1 EDU as a courier and a mock Agile ONE SN as a household's robot, each a
    service of its own on the sim board. It ends with what each act expected beside what
    happened, and exits 0 only when every one matched."""
    from quackd.onboard import demo
    from quackd.onboard.preflight import key_variable

    if offline and llm:
        _fail("--offline flies the scripted pilot, so it takes no --llm")
    if not offline and not llm:
        _fail("say which brain both robots use: --llm, or --offline for the scripted pilot")
    acts = list(act or demo.ACTS)
    unknown = [name for name in acts if name not in demo.ACTS]
    if unknown:
        _fail(f"no act called {unknown[0]!r}: {', '.join(demo.ACTS)}")
    brain = "fake" if offline else str(llm)
    key_env = key_variable(brain)
    if key_env and not os.environ.get(key_env):
        _fail(f"{brain} needs {key_env} in the environment, and it is not set")
    workspace = demo.workspace_for(keep)

    def tell(line: str) -> None:
        if not as_json:
            act_line = line.startswith("act ")
            ui.console.print(Text(ui.printable(line), style="bold" if act_line else ""))

    try:
        acts_done = demo.run_demo(
            workspace=workspace,
            llm=brain,
            base_url=base_url,
            key_env=key_env,
            acts=acts,
            fast=fast,
            tell=tell,
        )
    finally:
        demo.clean(workspace, keep)
    ok = all(done.ok for done in acts_done)
    if as_json:
        print(json.dumps({"ok": ok, "acts": [done.row() for done in acts_done]}))
    else:

        def table(g: Any) -> Any:
            t = ui.table("what each act expected, and what happened")
            for column in ("act", "expected", "observed", ""):
                t.add_column(column)
            for done in acts_done:
                mark = Text(g.ok, style="green") if done.ok else Text(g.fail, style="red")
                t.add_row(done.name, done.expected, Text(ui.printable(done.observed)), mark)
            return t

        ui.console.print(ui.Deferred(table))
        if keep is not None:
            ui.console.print(Text(f"every record is in {keep}", style=ui.STYLES["muted"]))
    if not ok:
        raise typer.Exit(code=1)


@app.command(rich_help_panel="LAN")
def discover(
    timeout: float = typer.Option(3.0, "--timeout", help="Seconds to listen for answers."),
    as_json: bool = typer.Option(False, "--json", help="One JSON object per robot."),
) -> None:
    r"""List the quackd robots, and the robot passports, answering on the LAN (zeroconf, needs
    quackd\[lan])."""
    from concurrent.futures import ThreadPoolExecutor

    from quackd.lan import LanNotInstalled
    from quackd.lan import discover as lan_discover
    from quackd.lan.passports import discover_passports

    try:
        # it listens for the whole timeout whether anything answers or not, so say so; the two
        # service types are browsed side by side, so the second costs no extra time
        spinning = ui.spinner(f"listening for quackd robots and passports ({timeout:g} s)")
        with spinning, ThreadPoolExecutor(max_workers=2) as pool:
            quackd_robots = pool.submit(lan_discover.discover, timeout)
            passport_beacons = pool.submit(discover_passports, timeout)
            robots, beacons = quackd_robots.result(), passport_beacons.result()
    except LanNotInstalled as e:
        _fail(str(e), hint="pip install 'quackd[lan]' adds zeroconf")
    if as_json:
        for robot in robots:
            print(json.dumps(robot.row()))
        for beacon in beacons:
            print(json.dumps(beacon.row()))
        return
    if beacons:
        p = ui.table(f"robot passports on the LAN ({len(beacons)})")
        p.add_column("name", style=ui.STYLES["key"], no_wrap=True)
        p.add_column("short id")
        p.add_column("url", overflow="fold")  # the column a person copies, never cut short
        p.add_column("digest")
        for beacon in beacons:
            p.add_row(
                Text(ui.printable(beacon.instance.removesuffix("._robot-passport._tcp.local."))),
                Text(ui.printable(beacon.short_id or "none")),
                Text(ui.printable(beacon.url())),
                Text(ui.printable(beacon.digest), style=ui.STYLES["muted"]),
            )
        p.caption = Text("quackd passport check <url> reads one", style=ui.STYLES["muted"])
        p.caption_justify = "left"
        ui.console.print(p)
    if not robots:
        ui.console.print(
            Text(f"no quackd robots answered in {timeout:g} s", style=ui.STYLES["muted"])
        )
        return
    t = ui.table(f"quackd robots on the LAN ({len(robots)})")
    t.add_column("manifest id", style=ui.STYLES["key"], no_wrap=True)
    for column in ("adapter", "model", "embodiment", "verbs", "address", "digest"):
        t.add_column(column)
    for robot in robots:
        t.add_row(
            Text(ui.printable(robot.manifest_id)),
            Text(ui.printable(robot.adapter)),
            Text(ui.printable(robot.model)),
            Text(ui.printable(robot.embodiment)),
            Text(str(robot.n_verbs)),
            Text(ui.printable(", ".join(robot.addresses) or robot.host)),
            Text(ui.printable(robot.digest), style=ui.STYLES["muted"]),
        )
    ui.console.print(t)


@app.command(rich_help_panel="LAN")
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
    r"""Advertise a robot's identity on the LAN (zeroconf, needs quackd\[lan])."""
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
    ui.console.print(
        ui.run_header(
            ann.record.name,
            [
                ("robot", manifest.summary()),
                ("at", ", ".join(ann.record.addresses)),
                ("digest", manifest.digest()),
            ],
            hint="Ctrl-C to withdraw" if for_s is None else f"withdrawing in {for_s:g} s",
        )
    )
    try:
        with ui.spinner(f"announcing {ann.record.name}"):
            if for_s is None:
                while True:
                    time.sleep(1.0)
            else:
                time.sleep(for_s)
    except KeyboardInterrupt:
        pass
    finally:
        ann.close()
        ui.console.print(Text("withdrawn", style=ui.STYLES["muted"]))


def _stdout_alive() -> bool:
    """Whether stdout can still be written to. A closed pipe fails the flush."""
    try:
        sys.stdout.flush()
    except OSError:
        return False
    return not sys.stdout.closed


def _leave_quietly() -> None:
    """Stop, with nothing further to say.

    Python flushes stdout as it exits, which raises a second time on a pipe that has already
    gone, so stdout is pointed at the void before leaving."""
    with contextlib.suppress(Exception):
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
    raise SystemExit(0)


def _rich_traceback(kind: type[BaseException], exc: BaseException, tb: Any) -> None:
    """A crash, rendered, without this process's locals in it: they hold an API key, a
    robot's address and its bridge token.

    Built here rather than installed once, because the console `--no-color` rebuilt does not
    exist until the root callback has run and an excepthook fires long after that."""
    from rich.traceback import Traceback

    ui.err_console.print(
        Traceback.from_exception(kind, exc, tb, show_locals=False, suppress=[typer])
    )


def main() -> None:
    """The console entry point: `app()`, and the two things a command that prints for a
    living owes its terminal.

    A traceback must not spill this process's locals, because they hold an API key, a
    robot's address and its bridge token. And a reader is allowed to walk away: `quackd
    list-verbs | head` closes the pipe halfway down the table, and Python's answer to that
    is a second wall of text about a broken pipe on top of the output that was asked for."""
    sys.excepthook = _rich_traceback
    try:
        app()
    except BrokenPipeError:
        _leave_quietly()
    except OSError as e:
        # Windows answers a write to a pipe nobody is reading with EINVAL rather than EPIPE,
        # and EINVAL is far too common an errno to swallow on its own word: only when stdout
        # is the stream that has actually stopped accepting writes is this a reader leaving.
        if e.errno not in (errno.EPIPE, errno.EINVAL) or _stdout_alive():
            raise
        _leave_quietly()


if __name__ == "__main__":
    main()
