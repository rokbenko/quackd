"""The walks a word names in drive, each derived from the body's own clamps with no number of its
own.

"walk forward" and "turn left" mean something on every body that walks, and nothing a person
types says how fast or how far. So the word names a preset and quackd supplies every number:
the share of each clamp a preset moves at is the share `move`'s own default speed is of the
schema's bound, a walk lasts `move`'s own default second, and a turn covers `search_scan`'s own
default step. On the Microduck, whose clamps are the schema's, `forward` is `move()` exactly.
"""

from __future__ import annotations

import math
from collections.abc import Collection
from dataclasses import dataclass

from pydantic import ValidationError

from quackd.adapters.manifest import RobotManifest
from quackd.verbs.core import MAX_VX, MoveParams, SearchScanParams, speed_limits

SHARE = MoveParams().vx / MAX_VX
"""How much of a body's clamp a preset moves at: the share `move`'s own default speed is of the
schema's bound on it, half."""

WALK_S = MoveParams().duration_s
"""How long a walk lasts: `move`'s own default."""

TURN_DEG = SearchScanParams().step_deg
"""How far a turn turns: `search_scan`'s own default step, the turn a body makes to look at the
next part of a room."""


@dataclass(frozen=True)
class Preset:
    """One word's walk on one body: the `move` it runs, every number in it quackd's."""

    name: str
    params: MoveParams

    @property
    def call(self) -> dict[str, float]:
        """The arguments as `move` is handed them, for the record and the line drive prints
        before the walk starts: every value but a zero `move` would fill in itself.

        A zero `move` would not fill in stays: its own default speed is 0.15, so a turn handed
        without its `vx=0` turned and walked forward at once, the zero having been left out as
        nothing worth saying."""
        defaults = MoveParams().model_dump()
        return {k: v for k, v in self.params.model_dump().items() if v != 0 or v != defaults[k]}

    def says(self) -> str:
        args = ", ".join(f"{k}={v:g}" for k, v in sorted(self.call.items()))
        return f"{self.name}: move({args})"


def presets_for(manifest: RobotManifest | None, verbs: Collection[str]) -> dict[str, Preset]:
    """Every preset this body has, by the word that names it: none for a body without `move`, or
    that does not move about, an arm among them, `step left` and `step right` only where the body
    can step sideways, and none whose numbers `move` itself would refuse, a third party's body
    faster than the schema's bounds or a turn slower than `move` may last."""
    if "move" not in verbs or manifest is None or manifest.mobility == "none":
        return {}
    max_vx, max_vy, max_wz = speed_limits(manifest)
    wanted: dict[str, dict[str, float]] = {
        "forward": {"vx": SHARE * max_vx, "duration_s": WALK_S},
        "back": {"vx": -SHARE * max_vx, "duration_s": WALK_S},
    }
    turn = SHARE * max_wz
    if turn > 0:
        turn_s = math.radians(TURN_DEG) / turn
        wanted["turn left"] = {"vx": 0.0, "wz": turn, "duration_s": turn_s}
        wanted["turn right"] = {"vx": 0.0, "wz": -turn, "duration_s": turn_s}
    if max_vy > 0:
        wanted["step left"] = {"vx": 0.0, "vy": SHARE * max_vy, "duration_s": WALK_S}
        wanted["step right"] = {"vx": 0.0, "vy": -SHARE * max_vy, "duration_s": WALK_S}
    presets: dict[str, Preset] = {}
    for name, values in wanted.items():
        try:
            params = MoveParams(**{k: round(v, 3) for k, v in values.items()})
        except ValidationError:
            continue
        presets[name] = Preset(name, params)
    return presets
