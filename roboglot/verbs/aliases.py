"""Every verb alias Roboglot knows, in one place: old name -> canonical name.

0.4 renamed the duck-shaped core verbs (`get_frame`, `walk_to`, `walk`) to names any body
can carry (`observe`, `go_to`, `move`). The old spellings stay valid forever: shipped and
community `.glot` files use them, and a rename that breaks a task file is not a rename, it
is a regression. An alias exists in a registry if and only if its canonical verb does, and
nothing else in Roboglot may spell an alias mapping (ADR-0018).
"""

from __future__ import annotations

ALIASES: dict[str, str] = {
    "get_frame": "observe",
    "walk_to": "go_to",
    "walk": "move",
}


def canonical(name: str) -> str:
    """The registry name for `name`; identity for anything that is not an alias."""
    return ALIASES.get(name, name)


def aliases_of(name: str) -> list[str]:
    """Every alias that resolves to the canonical `name`, in table order."""
    return [alias for alias, target in ALIASES.items() if target == name]
