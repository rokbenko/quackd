"""Set quackd's version, in the ten places that have to agree.

The core and each adapter carry their own `__version__`, because an adapter's sdist contains
only its own source and cannot read the core's. They are released together and must not
drift, so this writes all of them and the dependency windows that tie them together.

A window starts at the release itself and ends before the next minor, `>=X.Y.Z,<X.Y+1`. So a
patch raises every window's floor to itself, and a minor or a major moves the whole window. An
adapter from a patch can never be installed beside a core from before it, so its fix may need
what the core gained in the same patch, while a core from a patch still installs beside an
adapter from earlier in its minor, whose window admits it (RELEASING.md). A window written
before 0.16.1, from the major and the minor alone (`>=X.Y,<X.Y+1`), is rewritten the same way.

    uv run python scripts/set_version.py 0.16.1
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
VERSION = re.compile(r'^__version__ = "\d+\.\d+\.\d+"$', re.M)
WINDOW = re.compile(r'"quackd(-[a-z-]+)?(\[[a-z]+\])?>=\d+\.\d+(?:\.\d+)?,<\d+\.\d+"')


def version_files() -> list[Path]:
    """Every file holding a `__version__` this owns: the core, then each adapter package."""
    return [REPO / "quackd" / "__init__.py", *sorted(REPO.glob("adapters/*/src/*/__init__.py"))]


def main(argv: list[str]) -> int:
    if len(argv) != 2 or not re.fullmatch(r"\d+\.\d+\.\d+", argv[1]):
        print("usage: set_version.py X.Y.Z", file=sys.stderr)
        return 2
    new = argv[1]
    major, minor_num, _ = new.split(".")
    nxt = f"{major}.{int(minor_num) + 1}"

    for path in version_files():
        text = path.read_text(encoding="utf-8")
        if not VERSION.search(text):
            print(f"no __version__ in {path.relative_to(REPO)}", file=sys.stderr)
            return 1
        path.write_text(
            VERSION.sub(f'__version__ = "{new}"', text, count=1), encoding="utf-8", newline="\n"
        )
        print(f"{path.relative_to(REPO)}: {new}")

    for path in [REPO / "pyproject.toml", *sorted(REPO.glob("adapters/*/pyproject.toml"))]:
        text = path.read_text(encoding="utf-8")
        fixed = WINDOW.sub(
            lambda m: f'"quackd{m.group(1) or ""}{m.group(2) or ""}>={new},<{nxt}"', text
        )
        if fixed != text:
            path.write_text(fixed, encoding="utf-8", newline="\n")
            print(f"{path.relative_to(REPO)}: pins now >={new},<{nxt}")
    print("\nnow run: uv lock")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
