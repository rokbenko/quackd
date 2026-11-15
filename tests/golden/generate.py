"""Regenerate the golden fixtures in this directory from the CURRENT code.

Run `uv run python -m tests.golden.generate`. Only do this when a change to a task file or to
what the log prints is intended; the commit message must say why the goldens moved. The
manifests' digests are `manifest_digests.py`'s, on their own.
"""

from __future__ import annotations

import json
import sys

from tests.golden.log_cases import golden as log_lines_golden
from tests.test_goldens import GOLDEN, glot_hashes


def _dump(name: str, data: object) -> None:
    (GOLDEN / name).write_text(json.dumps(data, indent=1) + "\n", encoding="utf-8")


def main() -> int:
    GOLDEN.mkdir(exist_ok=True)
    _dump("glot_hashes.json", glot_hashes())
    _dump("log_lines.json", log_lines_golden())
    print(f"wrote goldens to {GOLDEN}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
