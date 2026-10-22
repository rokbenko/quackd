"""How long the README's Robot Passport section is against docs/passport/, which it summarises.

The section mentions every aspect of the protocol and links each page, at between 20% and 30% of
the folder's length, counting words outside code blocks on both sides: a quoted output is the
same length wherever it is pasted, and counting it would let either side grow without saying more.
Run it after any edit to either: `uv run --no-sync python scripts/passport_readme_ratio.py`.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
LOW, HIGH = 0.20, 0.30


def words(text: str) -> int:
    """The words of `text` outside its fenced code blocks."""
    kept, fenced = [], False
    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            fenced = not fenced
            continue
        if not fenced:
            kept.append(line)
    return len(re.findall(r"\S+", "\n".join(kept)))


def main() -> int:
    readme = (REPO / "README.md").read_text(encoding="utf-8")
    start = readme.index("\n## Robot Passport\n")
    end = readme.index("\n## ", start + 5)
    section = words(readme[start:end])
    pages = sorted((REPO / "docs" / "passport").rglob("*.md"))
    docs = sum(words(page.read_text(encoding="utf-8")) for page in pages)
    ratio = section / docs
    print(
        f"README section {section} words, docs/passport {docs} words in {len(pages)} pages, "
        f"ratio {ratio:.1%} (keep it between {LOW:.0%} and {HIGH:.0%})"
    )
    return 0 if LOW <= ratio <= HIGH else 1


if __name__ == "__main__":
    sys.exit(main())
