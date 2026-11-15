"""`roboglot/passport/` imports nothing else from Roboglot.

The protocol is written to leave Roboglot one day, and this package is meant to leave with it
whole (ADR-0057). An import of `roboglot.registry` or `roboglot.ui` slipped in for convenience is
exactly what would make that a rewrite instead of a move, so it fails here first.
"""

from __future__ import annotations

import ast
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[1] / "roboglot" / "passport"
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
    assert files, "roboglot/passport/ moved, so this test checks nothing"
    stray = []
    for path in files:
        for name in _imports(path):
            top = name.split(".")[0]
            if name.startswith("roboglot.passport") or name == "__future__":
                continue
            outside = top not in sys.stdlib_module_names and top not in ALLOWED_THIRD_PARTY
            if top == "roboglot" or outside:
                stray.append(f"{path.name}: {name}")
    assert not stray, f"roboglot/passport/ must stand on its own: {stray}"
