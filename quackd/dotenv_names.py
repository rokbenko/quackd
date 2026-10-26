"""The variables quackd's own `.env` loading put in the environment, as opposed to the shell that
started it.

`quackd`'s root command loads the `.env` beside where it runs, then the one a walk up from
quackd's installed directory finds (`cli._main`), and neither overrides a variable the shell
already set. So a variable those files set is one nobody exported, and one that came with the
directory: a project cloned from somebody else brings its `.env` along. Most settings may come
from there. The few that decide where a credential goes, or which server drives an arm, are read
by code that asks here whether the value in effect is the shell's or a file's: `endpoint.reach`
reads an endpoint's record only from a registry the shell or `--registry-dir` names.

Kept as each name with the value the file gave it, so a value the shell, or a test, sets
afterwards is the shell's again, and replaced on every load, so one command's files never speak
for the next in the same process. It imports the standard library alone, so the arm's adapter
can ask it without importing the CLI."""

from __future__ import annotations

import os
from collections.abc import Collection

_LOADED: dict[str, str] = {}


def remember(before: Collection[str]) -> None:
    """Note what the `.env` files just loaded put in the environment: every variable set now that
    was not among `before`, the names it held before they were loaded, with its value."""
    _LOADED.clear()
    _LOADED.update({name: value for name, value in os.environ.items() if name not in before})


def from_dotenv(name: str) -> bool:
    """Whether the value `name` has in the environment now is the one a `.env` file gave it."""
    value = os.environ.get(name)
    return value is not None and _LOADED.get(name) == value


__all__ = ["from_dotenv", "remember"]
