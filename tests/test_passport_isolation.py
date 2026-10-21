"""`quackd/passport/` imports nothing else from quackd.

The protocol is written to leave quackd one day, and this package is meant to leave with it
whole (ADR-0057). An import of `quackd.registry` or `quackd.ui` slipped in for convenience is
exactly what would make that a rewrite instead of a move, so it fails here first.
"""

from __future__ import annotations

import ast
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[1] / "quackd" / "passport"
ALLOWED_THIRD_PARTY = {"cryptography"}


def _imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names += [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.append(node.module)
    return names


def test_the_passport_package_imports_only_itself_the_stdlib_and_cryptography() -> None:
    import sys

    files = sorted(PACKAGE.glob("*.py"))
    assert files, "quackd/passport/ moved, so this test checks nothing"
    stray = []
    for path in files:
        for name in _imports(path):
            top = name.split(".")[0]
            if name.startswith("quackd.passport") or name == "__future__":
                continue
            outside = top not in sys.stdlib_module_names and top not in ALLOWED_THIRD_PARTY
            if top == "quackd" or outside:
                stray.append(f"{path.name}: {name}")
    assert not stray, f"quackd/passport/ must stand on its own: {stray}"
