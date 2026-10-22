"""quackd with a Nemotron pilot on Token Factory: `quackd run`, unchanged, with its model
provider pointed at Nebius.

quackd's `local` provider speaks to any OpenAI-compatible server, accepts any model id, asks
with `tool_choice` auto, and reads a tool call a model writes as JSON text. So the pilot is a
command line, not code. This module checks the id against the key's live list, then hands quackd
the address on its command line and the key in the environment, where no process list shows it.
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from .nebius import KEY_ENV, Model, NebiusError, Settings

#: quackd's console script, run by this interpreter, so the quackd it runs is the one installed
#: beside this edition. quackd has no `python -m quackd`, and `-P` keeps the folder a command
#: is run from off the import path, so a `quackd/` there (the repository root has one) is never
#: imported in place of the installed one.
QUACKD = (sys.executable, "-P", "-c", "from quackd.cli import main; main()")
#: quackd's own flags this edition sets, refused when given again so the two never disagree.
OWNED = ("--llm", "-l", "--base-url", "--api-key")
#: Where quackd's local provider reads its key when `--api-key` is absent.
KEY_VARIABLE = "LOCAL_API_KEY"


@dataclass(frozen=True)
class Launch:
    """A quackd command and the variables it needs on top of this process's own."""

    argv: tuple[str, ...]
    env: Mapping[str, str] = field(repr=False)

    def shown(self) -> str:
        """The command for a person to read: quackd's part only, with the key never in it."""
        return "quackd " + " ".join(_quoted(a) for a in self.argv[len(QUACKD) :])


def _quoted(arg: str) -> str:
    return f'"{arg}"' if not arg or any(c in arg for c in " \t'\"") else arg


def child_env(extra: Mapping[str, str]) -> dict[str, str]:
    """This process's environment for a quackd it starts, less the Nebius key, which quackd
    never reads under that name, plus `extra`."""
    env = {name: value for name, value in os.environ.items() if name != KEY_ENV}
    env.update(extra)
    return env


def owned_flags(args: Sequence[str]) -> list[str]:
    """Which of `args` set what this edition sets: the pilot, its address, its key."""
    return [a for a in args if a.split("=", 1)[0] in OWNED]


def quackd_launch(args: Sequence[str], settings: Settings) -> Launch:
    """Any quackd command, with the Token Factory key where the local provider reads it."""
    return Launch((*QUACKD, *args), {KEY_VARIABLE: settings.api_key})


def run_launch(
    settings: Settings, model: Model, run_args: Sequence[str], *, vision: bool = False
) -> Launch:
    """`quackd run <run_args>` piloted by `model` on Token Factory."""
    clash = owned_flags(run_args)
    if clash:
        raise NebiusError(f"{', '.join(clash)} is set by passport-nebius: leave it out")
    if vision and model.takes_images is False:
        raise NebiusError(
            f"{model.id} does not read images, by its own modality on the list ({model.modality})"
        )
    argv = ["run", *run_args, "--llm", f"local:{model.id}", "--base-url", settings.base_url]
    if vision:
        argv.append("--vision")
    return quackd_launch(argv, settings)


def unpriced(run_args: Sequence[str]) -> bool:
    """Whether quackd would record this run at $0: a local preset costs nothing unless told."""
    given = any(a.split("=", 1)[0] == "--price" for a in run_args)
    return not given and not os.environ.get("QUACKD_PRICE")


def start(launch: Launch, **popen: object) -> subprocess.Popen[bytes]:
    return subprocess.Popen(launch.argv, env=child_env(launch.env), **popen)  # type: ignore[call-overload]


def wait(process: subprocess.Popen[bytes]) -> int:
    """The child's exit code. A Ctrl+C reaches the child too, and quackd brings its robot to a
    stop before it exits, so this keeps waiting for that instead of leaving first."""
    while True:
        try:
            return process.wait()
        except KeyboardInterrupt:
            continue


def run(launch: Launch) -> int:
    return wait(start(launch))
