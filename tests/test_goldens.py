"""Goldens: two promises turned into machine checks. The five v0 starter tasks that are left are
unchanged, byte for byte, and the plain log lines an MCP tool result carries render as they were
recorded. Regenerate ONLY on purpose: `uv run python -m tests.golden.generate` (and say why in the
commit).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
GOLDEN = REPO / "tests" / "golden"
V0_DUCKS = ("hello-world", "find-and-kick", "patrol-and-quack", "follow-me", "fetch")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def glot_hashes() -> dict[str, str]:
    return {name: _sha((REPO / "tasks" / f"{name}.glot").read_bytes()) for name in V0_DUCKS}


def _load(name: str) -> Any:
    return json.loads((GOLDEN / name).read_text(encoding="utf-8"))


def test_v0_starter_ducks_are_unchanged() -> None:
    assert glot_hashes() == _load("glot_hashes.json")


def test_the_plain_log_lines_are_unchanged() -> None:
    """The MCP tool result carries these bytes and an LLM reads them (docs/guides/mcp.md), so the
    terminal view may be redrawn but the renderer under it may not move."""
    from tests.golden.log_cases import golden

    want = _load("log_lines.json")
    got = golden()
    assert sorted(got) == sorted(want), "a case appeared or vanished: regenerate on purpose"
    for name in sorted(want):
        assert got[name] == want[name], f"{name} drifted"
