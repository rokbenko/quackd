"""Render the table of the bodies quackd only describes into `docs/adapters/status.md`, from the
body files under `quackd/adapters/bodies/`, or check that the page still holds what they say.

A described body (ADR-0077) is one YAML and no code, so nothing but this table would ever put
its numbers on a page. The table sits between two markers this script owns. A figure is changed
in the body file and this script run, never in the table, and a test runs it on every commit and
fails when the page says something the files no longer do.

    uv run --no-sync python scripts/bodies_docs.py          # rewrite the page
    uv run --no-sync python scripts/bodies_docs.py --check  # exit 1 if the page would change

It imports the described module and the catalogue and nothing else of quackd's, and reads no
environment.
"""

from __future__ import annotations

import re
import sys
from collections.abc import Sequence
from pathlib import Path

from quackd.adapters.catalogue import DESCRIBED, DESCRIBED_BODIES
from quackd.adapters.described import DRIVES_NOTHING, describe
from quackd.adapters.manifest import Figure, RobotManifest

REPO = Path(__file__).resolve().parents[1]
PAGE = REPO / "docs" / "adapters" / "status.md"
BLOCK = "described-bodies"

_START = re.compile(rf"^<!-- generated: {BLOCK}\b[^\n]*-->$", re.M)
_END = re.compile(rf"^<!-- end generated: {BLOCK} -->$", re.M)
START_MARKER = (
    f"<!-- generated: {BLOCK}, by scripts/bodies_docs.py from quackd/adapters/bodies/*.yaml. "
    "Change a body file and run the script, not this table. -->"
)
_READ = re.compile(r"read (\d{4}-\d{2}-\d{2})")


def _cell(text: str) -> str:
    return " ".join(text.split()).replace("|", "\\|")


def _figure(fig: Figure | None, unit: str, *, absent: str = "not published") -> str:
    """`60 kg (official)`, with the note where the figure carries one; `not published` where the
    maker did not say, which is what the prompt tells the pilot too, for every figure the prompt
    lists as a gap. A back load is the one it does not: there the words are the verdict gate's."""
    if fig is None:
        return absent
    amount = f"{fig.value:g} {unit}".rstrip()
    note = f", {fig.note}" if fig.note else ""
    return _cell(f"{amount} ({fig.confidence}{note})")


def _sources(manifest: RobotManifest) -> str:
    """Who the figures are read from, each source named once by its head, and the dates read."""
    sheet = manifest.datasheet
    assert sheet is not None
    heads: list[str] = []
    dates: set[str] = set()
    for _label, fig, _unit in sheet.known():
        head = fig.source.split(":", 1)[0]
        if head not in heads:
            heads.append(head)
        dates.update(_READ.findall(fig.source))
    when = ", ".join(sorted(dates))
    return _cell(f"{'; '.join(heads)} (read {when})" if when else "; ".join(heads))


def _what(manifest: RobotManifest) -> str:
    """The blurb without the clause the section's heading already says of every row."""
    blurb = manifest.blurb
    tail = f"; {DRIVES_NOTHING}"
    if blurb.endswith(tail):
        blurb = blurb[: -len(tail)]
    return _cell(blurb)


def rows() -> str:
    """One row per described body, in the catalogue's order."""
    lines = [
        "| `--robot` | What it is | Mass | Height | Joints | Payload | Back load | Reach "
        "| Endurance | Figures from |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for name in DESCRIBED_BODIES:
        m = describe(name)
        sheet = m.datasheet
        assert sheet is not None, name
        tethered = "mains powered" if sheet.tethered else "not published"
        held = "none to hold with" if sheet.manipulator == "none" else "not published"
        rides = "it does not move" if m.mobility == "none" else "not published"
        lines.append(
            f"| `{DESCRIBED}:{name}` | {_what(m)} "
            f"| {_figure(sheet.mass_kg, 'kg')} | {_figure(sheet.height_m, 'm')} "
            f"| {_figure(sheet.dof, '')} | {_figure(sheet.payload_kg, 'kg', absent=held)} "
            f"| {_figure(sheet.carries_kg, 'kg', absent=rides)} "
            f"| {_figure(sheet.reach_m, 'm', absent=held)} "
            f"| {_figure(sheet.endurance_min, 'min', absent=tethered)} | {_sources(m)} |"
        )
    return "\n".join(lines)


def rewrite(text: str) -> str:
    """`text` with the table rendered afresh between its markers. Refused rather than guessed: a
    page with no block, two of them, or one with no end marker."""
    text = text.replace("\r\n", "\n")
    starts = list(_START.finditer(text))
    if len(starts) != 1:
        raise ValueError(f"docs/adapters/status.md carries {len(starts)} {BLOCK} blocks, not one")
    start = starts[0]
    end = _END.search(text, start.end())
    if end is None:
        raise ValueError(f"docs/adapters/status.md's {BLOCK} block has no end marker")
    return text[: start.start()] + START_MARKER + "\n\n" + rows() + "\n\n" + text[end.start() :]


def main(argv: Sequence[str]) -> int:
    with open(PAGE, encoding="utf-8", newline="") as f:
        old = f.read()
    new = rewrite(old)
    if old.replace("\r\n", "\n") == new:
        return 0
    if "--check" in argv:
        print(
            "docs/adapters/status.md is stale: run uv run --no-sync python scripts/bodies_docs.py"
        )
        return 1
    with open(PAGE, "w", encoding="utf-8", newline="") as f:
        f.write(new)
    print("rewrote docs/adapters/status.md")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
