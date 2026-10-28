"""Regenerate the golden fixtures in this directory from the CURRENT code.

Run `uv run python -m tests.golden.generate`. Only do this when a change to the simulator
or the flock is intended; the commit message must say why the goldens moved.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

from quackd.space.room import BUILT_IN
from tests.golden.log_cases import golden as log_lines_golden
from tests.test_goldens import (
    GOLDEN,
    duck_hashes,
    explore_golden,
    flock_golden,
    room_case,
    sim_case,
)


def _dump(name: str, data: object) -> None:
    (GOLDEN / name).write_text(json.dumps(data, indent=1) + "\n", encoding="utf-8")


def main() -> int:
    GOLDEN.mkdir(exist_ok=True)
    _dump(
        "sim2d_worlds.json",
        [sim_case(seed, n) for seed in range(10) for n in (1, 3)],
    )
    _dump("sim2d_rooms.json", [room_case(room, seed) for room in BUILT_IN for seed in range(5)])
    _dump("duck_hashes.json", duck_hashes())
    _dump("log_lines.json", log_lines_golden())
    with tempfile.TemporaryDirectory() as tmp:
        _dump("flock_kick_seed3.json", flock_golden(3, Path(tmp)))
        _dump("explore_seed3.json", explore_golden(3, Path(tmp)))
    print(f"wrote goldens to {GOLDEN}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
