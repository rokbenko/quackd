"""Render the table of what connecting each real backend takes into `docs/guides/web-ui.md`, from
the connect blocks in `quackd/adapters/catalogue.py`, or check that the page still holds what the
catalogue says.

The table sits between two markers this script owns. A block is changed in the catalogue and this
script run, never in the table, and a test runs it on every commit and fails when the guide says
something the catalogue no longer does. The page `quackd ui` reads the same blocks, so the guide
and the Setup screen cannot disagree.

    uv run --no-sync python scripts/connect_docs.py          # rewrite the guide
    uv run --no-sync python scripts/connect_docs.py --check  # exit 1 if the guide would change

It imports the catalogue and nothing else of quackd's, and reads no environment.
"""

from __future__ import annotations

import re
import sys
from collections.abc import Sequence
from pathlib import Path

from quackd.adapters.catalogue import OFFICIAL

REPO = Path(__file__).resolve().parents[1]
PAGE = REPO / "docs" / "guides" / "web-ui.md"
BLOCK = "connect-rows"

_START = re.compile(rf"^<!-- generated: {BLOCK}\b[^\n]*-->$", re.M)
_END = re.compile(rf"^<!-- end generated: {BLOCK} -->$", re.M)
START_MARKER = (
    f"<!-- generated: {BLOCK}, by scripts/connect_docs.py from quackd/adapters/catalogue.py. "
    "Change a block there and run the script, not this table. -->"
)


def _cell(text: str) -> str:
    return " ".join(text.split()).replace("|", "\\|")


def _code(text: str) -> str:
    """One value in backticks, and prose that names several left as prose."""
    return f"`{text}`" if " " not in text else _cell(text)


def rows() -> str:
    """One row per real backend of the nine, in the catalogue's order."""
    lines = [
        "| Robot | `--address` | For example | With none given | Token | More |",
        "|---|---|---|---|---|---|",
    ]
    for info in OFFICIAL:
        for backend, block in info.connect.items():
            default = _code(block.default) if block.default else "refused: one must be given"
            token = f"`--token` or `{block.token_env}`" if block.token_env else "none"
            docs = f"[{info.name}](../{block.docs.removeprefix('docs/')})" if block.docs else ""
            lines.append(
                f"| `{info.name}:{backend}` | {_cell(block.address)} | {_code(block.example)} "
                f"| {default} | {token} | {docs} |"
            )
    return "\n".join(lines)


def rewrite(text: str) -> str:
    """`text` with the table rendered afresh between its markers. Refused rather than guessed: a
    page with no block, two of them, or one with no end marker."""
    text = text.replace("\r\n", "\n")
    starts = list(_START.finditer(text))
    if len(starts) != 1:
        raise ValueError(f"docs/guides/web-ui.md carries {len(starts)} {BLOCK} blocks, not one")
    start = starts[0]
    end = _END.search(text, start.end())
    if end is None:
        raise ValueError(f"docs/guides/web-ui.md's {BLOCK} block has no end marker")
    return text[: start.start()] + START_MARKER + "\n\n" + rows() + "\n\n" + text[end.start() :]


def main(argv: Sequence[str]) -> int:
    with open(PAGE, encoding="utf-8", newline="") as f:
        old = f.read()
    new = rewrite(old)
    if old.replace("\r\n", "\n") == new:
        return 0
    if "--check" in argv:
        print("docs/guides/web-ui.md is stale: run uv run --no-sync python scripts/connect_docs.py")
        return 1
    with open(PAGE, "w", encoding="utf-8", newline="") as f:
        f.write(new)
    print("rewrote docs/guides/web-ui.md")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
