"""Every option of `quackd run` and `quackd drive`, read off the commands themselves, and the
command a set of choices makes.

The page keeps no list of flags of its own. It walks the Typer app, so a flag quackd gains is on
the page the day it lands, with its own help, default and panel, and a flag quackd drops is gone
from it the same day. The page offers each command's options as the command defines them, grouped
by the panel `--help` puts them in, and builds the argv from the ones a person changed, which is
also the "same as" line it shows: the command to type at a terminal for the same run.

**Secrets never go on a command line.** `--api-key`, `--token`, `--host-token` and
`--policy-token` are offered as session keys instead, and reach the run through its environment,
under the variable quackd reads for each (`SECRET_ENV`, and the vendor's or the robot's own for
the first two). A process's command line is readable by every user on a Linux machine.
"""

from __future__ import annotations

import functools
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from quackd.command import SECRET_FLAGS, URL_FLAGS

COMMANDS = ("run", "drive")
"""The two commands the page drives: a goal is `run`, a chat is `drive`."""

PANELS = ("Task", "Robot", "Model", "Output", "Host", "Memory")
"""The order the page shows the panels in, `--help`'s own. Any other panel follows them."""

LEFT_OUT = frozenset(
    {"front", "live", "verbose", "log", "log_prompt", "flock", "robots", "by_hand"}
)
"""Options the page does not offer, each for a reason. `--front` is the page's own. `--live`
opens a window on the page's computer rather than in the page, and `--verbose`, `--log` and
`--log-prompt` shape a terminal's narration, which the page draws its own way. `--flock` and
`--robots` drive several robots, and `--by-hand` waits for Enter from somebody at the arm: the
page refuses all three (ADR-0069), so it does not offer them."""

SECRET_ENV = {"host_token": "QUACKD_HOST_TOKEN", "policy_token": "QUACKD_POLICY_TOKEN"}
"""The variable a secret flag's value reaches a run through, where it has one fixed name. The
pilot's key and a robot's token are named by the vendor and the robot (`catalog`)."""

KINDS = ("task", "switch", "either", "list", "integer", "number", "text")
"""`switch` is on or off; `either` is on, off, or left to the task file or the environment,
which is a flag whose default is None (`--play/--no-play`)."""


@dataclass(frozen=True)
class Option:
    """One of a command's options, as the page offers it."""

    name: str
    flag: str | None
    """The flag that turns it on or takes its value: None for the task argument."""
    off_flag: str | None
    """The flag that turns it off, where it has one (`--no-gif`)."""
    kind: str
    default: Any
    help: str
    panel: str
    secret: bool
    url: bool
    choices: tuple[str, ...] = ()


def _choices() -> dict[str, tuple[str, ...]]:
    """The words an option takes where quackd checks them itself rather than through Click, read
    from where quackd defines them."""
    from quackd.agent.decision.factory import MODES
    from quackd.agent.providers.factory import EFFORTS
    from quackd.cli import CONTROLLERS
    from quackd.perception import DETECTOR_CHOICES

    return {
        "effort": tuple(EFFORTS),
        "decision_mode": tuple(MODES),
        "detector": tuple(DETECTOR_CHOICES),
        "controller": tuple(CONTROLLERS),
    }


def _command(command: str) -> Any:
    import typer

    from quackd.cli import app

    if command not in COMMANDS:
        raise ValueError(f"the page drives {' and '.join(COMMANDS)}, not {command!r}")
    return typer.main.get_command(app).commands[command]  # type: ignore[attr-defined]


def _kind(param: Any) -> str:
    """By the names Click gives its parameters and types, never by their classes: Typer carries
    a Click of its own (`typer._click`), whose classes are not the installed `click`'s."""
    if getattr(param, "param_type_name", "") == "argument":
        return "task"
    if getattr(param, "is_flag", False):
        if param.secondary_opts and param.default is None:
            return "either"
        return "switch"
    if getattr(param, "multiple", False):
        return "list"
    type_name = str(getattr(param.type, "name", "")).lower()
    if type_name.startswith("int"):  # `int` and `int range` in Typer's, `integer` in click's
        return "integer"
    if type_name.startswith("float"):
        return "number"
    return "text"


def options(command: str) -> list[Option]:
    """Every option of `command` the page offers, in the command's own order."""
    return list(_options(command))


@functools.cache
def _options(command: str) -> tuple[Option, ...]:
    """Read from the Typer app once per command: its options do not change while the page runs,
    and Setup asks for them a dozen times each time it is drawn."""
    choices = _choices()
    out = []
    for param in _command(command).params:
        if getattr(param, "hidden", False) or param.name in LEFT_OUT:
            continue
        kind = _kind(param)
        flag = None if kind == "task" else param.opts[0]
        out.append(
            Option(
                name=param.name,
                flag=flag,
                off_flag=param.secondary_opts[0]
                if getattr(param, "secondary_opts", None)
                else None,
                kind=kind,
                default=list(param.default) if kind == "list" else param.default,
                help=" ".join(str(getattr(param, "help", "") or "").split()),
                panel=getattr(param, "rich_help_panel", None)
                or ("Task" if kind == "task" else "Other"),
                secret=flag in SECRET_FLAGS,
                url=flag in URL_FLAGS,
                choices=choices.get(param.name, ()),
            )
        )
    return tuple(out)


def by_panel(command: str) -> dict[str, list[Option]]:
    """`options(command)` grouped by panel, the panels in `PANELS` order and any other after."""
    grouped: dict[str, list[Option]] = {}
    for option in options(command):
        grouped.setdefault(option.panel, []).append(option)
    order = [p for p in PANELS if p in grouped] + [p for p in grouped if p not in PANELS]
    return {panel: grouped[panel] for panel in order}


MODEL_OPTIONS = ("llm", "base_url", "api_key", "extra_body", "vision", "effort", "price", "image")
"""What a model takes, which a VLA pilot asks none of."""

DECISION_OPTIONS = ("decision_llm", "decision_url", "decision_mode", "decision_frames")


def pilot_rule(values: Mapping[str, Any]) -> dict[str, Any]:
    """`values` with what `--controller vla` refuses taken out, whoever set it: a scripted pilot
    asks no model and no decision LLM, and `quackd run` refuses a model's flag, a picture and
    `--play` beside it rather than drop them without a word."""
    out = dict(values)
    if out.get("controller") == "vla":
        for name in (*MODEL_OPTIONS, *DECISION_OPTIONS):
            out.pop(name, None)
        if out.get("play") is True:
            out.pop("play")
    return out


def argv_for(command: str, values: Mapping[str, Any]) -> list[str]:
    """`[command, ...]`: the task argument where there is one, then every option whose value in
    `values` differs from its default, in the command's own order. A secret never: it goes by the
    environment. An option `values` does not name is left at its default, as is one the command
    does not have, which is how one set of choices makes both commands. What a VLA pilot refuses
    is left out (`pilot_rule`)."""
    values = pilot_rule(values)
    argv = [command]
    for option in options(command):
        if option.secret or option.name not in values:
            continue
        value = values[option.name]
        if option.kind == "task":
            if value:
                argv.insert(1, str(value))
        elif option.kind == "switch":
            if bool(value) != bool(option.default):
                if value:
                    argv.append(str(option.flag))
                elif option.off_flag:
                    argv.append(option.off_flag)
        elif option.kind == "either":
            if value is True:
                argv.append(str(option.flag))
            elif value is False and option.off_flag:
                argv.append(option.off_flag)
        elif option.kind == "list":
            for item in value or ():
                if str(item).strip():
                    argv += [str(option.flag), str(item).strip()]
        elif value not in (None, "") and value != option.default:
            argv += [str(option.flag), str(value)]
    return argv


def parsed(argv: Sequence[str]) -> dict[str, Any]:
    """What quackd's own parser makes of `argv`, `[command, ...]`: the check that the page never
    builds a command quackd would refuse to read. Raises click's `UsageError` where it would."""
    command = _command(argv[0])
    context = command.make_context(f"quackd {argv[0]}", list(argv[1:]))
    return dict(context.params)
