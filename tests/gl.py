"""Make an offscreen OpenGL context, or skip saying why.

Every rendering test in the physics suite needs one, and a bare CI runner has no GPU, no
display and no `/dev/dri`. Skipping is right on a developer's machine. It is wrong in the job
that exists to run these: a missing system library would turn that job green having proved
nothing, which is the state it was added to end. `ROBOGLOT_REQUIRE_GL=1` turns the skip into a
failure, and the exception text goes into the skip reason either way, so a real renderer
regression names itself instead of disappearing.
"""

from __future__ import annotations

import os
from typing import Any

import pytest

REQUIRE_ENV = "ROBOGLOT_REQUIRE_GL"


def require_render(world: Any, size: int = 64) -> None:
    try:
        world.renderer(size)
    except Exception as e:
        if os.environ.get(REQUIRE_ENV) == "1":
            raise
        pytest.skip(f"no OpenGL context for offscreen rendering: {e!r}")
