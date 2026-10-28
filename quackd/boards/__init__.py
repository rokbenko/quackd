"""The computer `quackd onboard` runs on: which one this is, and whether quackd supports it.

`quackd onboard` runs on a robot's own computer, and only on one it knows: an NVIDIA Jetson
(`jetson.py`), which finds itself from the files every Jetson carries. The sim board (`sim.py`)
stands in for one on a laptop, and only when asked for by name, so a laptop never passes for a
robot, and the passport it signs says `sim`, so no robot that reads it is fooled either.

A board says whether this computer is one (`detect`), what goes into the passport and what is
only shown (`BoardFacts`), which radio it has (`radio_kind`, the name `quackd.radio` opens it
by) and whether that radio is there (`radio`). Doctor never asks any of it: doctor reads no
file of the machine it runs on (ADR-0046, and a test that parses doctor.py), which is why this
lives outside doctor, and the two share only `l4t.py`, the L4T table and its parser.

Another computer arrives as a plugin, through the `quackd.boards` entry point group, the way a
third party's robot arrives through `quackd.adapters`. A plugin may not take `jetson` or `sim`:
those are quackd's own, and a package on PyPI cannot become what `--board jetson` means.
"""

from __future__ import annotations

import importlib.metadata
import platform
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

ENTRY_POINT_GROUP = "quackd.boards"
"""Where an installed board announces itself. One entry per board, its name to a module that
carries `make()`, which returns the `Board`."""

OFFICIAL = ("jetson",)
"""The boards quackd ships and supports, each found by itself."""

SIM = "sim"
RESERVED = (*OFFICIAL, SIM)
"""The names no plugin may take."""

KINDS = ("jetson", "sim", "other")
"""What a passport's `board.kind` may say. A plugin's board is `other`."""

MODEL_LIMIT = 64
SOC_LIMIT = 32
"""The most characters a passport's `board.model` and `board.soc` may hold."""

NOT_A_ROBOT = (
    "quackd onboard runs on a robot's own computer, and this is not one it supports: {found}. "
    "To try it here as a simulation, which its passport will say, run quackd onboard --board sim."
)


@dataclass(frozen=True)
class BoardFacts:
    """What a board found about the computer it is, in the words its passport uses."""

    kind: str
    model: str
    """`NVIDIA Jetson Orin Nano Developer Kit`: what the board calls itself."""
    soc: str | None = None
    """The chip, `tegra234`."""
    release: str | None = None
    """`L4T 36.4.3 (JetPack 6.2)`: shown, and never signed. It changes with every update, and a
    passport is signed for up to a year."""

    def __post_init__(self) -> None:
        if self.kind not in KINDS:
            raise ValueError(f"a board's kind is one of {', '.join(KINDS)}, not {self.kind!r}")
        if not self.model.strip():
            raise ValueError("a board's model names it, and this one is empty")

    def passport(self) -> dict[str, str]:
        """The passport's `board` member: kind, model and chip, cut to what a passport may hold.
        A device tree can name a board at any length, and a model cut at a word still says which
        board."""
        out = {"kind": self.kind, "model": _cut(self.model, MODEL_LIMIT)}
        if self.soc:
            out["soc"] = _cut(self.soc, SOC_LIMIT)
        return out


@dataclass(frozen=True)
class RadioCheck:
    """Whether the board's radio is there, as preflight and status report it."""

    ok: bool
    adapter: str | None = None
    """`hci0` on a Jetson, `sim` for the simulated air."""
    why: str | None = None
    """Why there is no radio, where there is none, in words a person can act on."""


class Unsupported(Exception):
    """A computer a board recognises and quackd cannot run on, with the reason: a Xavier, or an
    Orin still on JetPack 5."""


class NotARobot(Exception):
    """No board knows this computer: a laptop, or a computer quackd has never met."""


class UnknownBoard(LookupError):
    """`--board` names a board nothing installed here provides."""


@runtime_checkable
class Board(Protocol):
    """A kind of computer `quackd onboard` can run on."""

    name: str
    explicit_only: bool
    """Found only when asked for by name (`--board`), never by `detect()`: the sim board."""
    radio_kind: str
    """The radio `quackd.radio` opens on it: `bluez` on a Jetson, `sim` on the sim board."""

    def detect(self) -> BoardFacts | None:
        """This computer's facts when it is one of these, None when it is not, and `Unsupported`
        when it is one quackd cannot run on."""
        ...

    def radio(self) -> RadioCheck: ...


def boards() -> dict[str, Board]:
    """Every board `--board` can name here: quackd's own first, then the plugins, alphabetically."""
    return _installed()[0]


def named(name: str, *, among: Mapping[str, Board] | None = None) -> Board:
    """The board `--board NAME` means. `among` stands in for `boards()`, for tests."""
    wanted = name.strip().lower()
    found, broken = _installed() if among is None else (dict(among), {})
    if wanted in found:
        return found[wanted]
    if wanted in broken:
        raise UnknownBoard(
            f"the {wanted} board is installed here, and loading it failed: {broken[wanted]}"
        )
    raise UnknownBoard(f"no board called {wanted!r} here: --board takes {', '.join(found)}")


def detect(
    name: str | None = None, *, among: Mapping[str, Board] | None = None
) -> tuple[Board, BoardFacts]:
    """The board this computer is, and its facts.

    With `name` (`--board`), that board alone is asked, and that is the only way the sim board is
    ever asked. Without, every other board is asked in turn, quackd's own first, and the first
    that knows this computer wins. One that knows it and cannot run quackd on it raises
    `Unsupported`, and a later board still has its turn, since a plugin may support what quackd
    does not; with none, that refusal stands. Nothing knowing it at all is `NotARobot`. A plugin
    that fails to look, or answers as only quackd's own boards may, counts as one that does not
    know this computer, unless it was asked for by name, when its failure is the `Unsupported`.

    `among` stands in for `boards()`, for tests."""
    if name is not None:
        wanted = name.strip().lower()
        board = named(wanted, among=among)
        facts = _asked(wanted, board)
        if facts is None:
            raise NotARobot(_not_a_robot(wanted, ()))
        return board, facts
    known = boards() if among is None else dict(among)
    refused: Unsupported | None = None
    for key, board in known.items():
        if board.explicit_only:
            continue
        try:
            facts = _asked(key, board)
        except _PluginFailed:
            continue  # a stranger's board does not take the sentence below away
        except Unsupported as e:
            refused = refused or e
            continue
        if facts is not None:
            return board, facts
    if refused is not None:
        raise refused
    plugins = [n for n, b in known.items() if n not in RESERVED and not b.explicit_only]
    raise NotARobot(_not_a_robot(None, plugins))


class _PluginFailed(Unsupported):
    """A plugin's board that failed to look, or answered as only quackd's own may."""


def _asked(name: str, board: Board) -> BoardFacts | None:
    """`board.detect()`. quackd's own boards are asked as they are; a plugin's answer is held
    to what a plugin may say. Its failure is `_PluginFailed`, naming it, and so is an answer that
    is not a `BoardFacts` of kind `other`: no plugin signs a passport that says jetson or sim."""
    if name in RESERVED:
        return board.detect()
    try:
        facts = board.detect()
    except Unsupported:
        raise
    except Exception as e:
        raise _PluginFailed(
            f"the {name} board failed to look at this computer: {type(e).__name__}: {e}"
        ) from e
    if facts is None:
        return None
    if not isinstance(facts, BoardFacts):
        raise _PluginFailed(
            f"the {name} board answered with a {type(facts).__name__}, not with a BoardFacts"
        )
    if facts.kind != "other":
        raise _PluginFailed(
            f"the {name} board says this is a {facts.kind} board, which only quackd's own may say"
        )
    return facts


def _cut(text: str, limit: int) -> str:
    """`text` in at most `limit` characters, cut at a space where there is one to cut at."""
    if len(text) <= limit:
        return text
    cut = text[:limit]
    if text[limit] != " " and " " in cut:
        cut = cut[: cut.rindex(" ")]
    return cut.rstrip()


def _not_a_robot(name: str | None, plugins: Iterable[str]) -> str:
    """The sentence for a computer nothing knows. A Jetson's says what was looked for, since
    "no Jetson" on a board somebody believes is one is the sentence they will paste into an
    issue."""
    if name is not None and name not in OFFICIAL:
        return NOT_A_ROBOT.format(found=f"it found no {name} board here")
    from quackd.boards.jetson import looked_for

    looked = looked_for({"Darwin": "macOS"}.get(platform.system(), platform.system()))
    others = list(plugins)
    if others:
        looked += f", and no board installed here ({', '.join(others)}) knows it"
    return NOT_A_ROBOT.format(found=f"it found no NVIDIA Jetson ({looked})")


def _installed() -> tuple[dict[str, Board], dict[str, str]]:
    """quackd's own boards and every plugin's, by name, and each plugin that failed to load, with
    why. Not cached: a command asks once, and a test installs plugins of its own."""
    from quackd.boards.jetson import JetsonBoard
    from quackd.boards.sim import SimBoard

    found: dict[str, Board] = {"jetson": JetsonBoard(), SIM: SimBoard()}
    broken: dict[str, str] = {}
    plugins = importlib.metadata.entry_points(group=ENTRY_POINT_GROUP)
    for ep in sorted(plugins, key=lambda e: e.name):
        name = ep.name.strip().lower()
        if name in RESERVED or name in found or name in broken:
            continue  # quackd's own are never a plugin's, and of two plugins the first keeps it
        # Every exception: a stranger's package may raise anything while it is imported, or
        # while it is asked what it is, and `quackd onboard` on a Jetson must not fail for a
        # plugin nobody asked for.
        try:
            made = ep.load().make()
            if not isinstance(made, Board):
                broken[name] = f"what its make() returned is not a board: {type(made).__name__}"
                continue
            calls_itself = str(made.name).strip().lower()
        except Exception as e:
            broken[name] = f"{type(e).__name__}: {e}"
            continue
        if calls_itself != name:
            broken[name] = f"it calls itself {made.name!r}, and is installed as {name!r}"
            continue
        found[name] = made
    return found, broken
