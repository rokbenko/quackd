"""The walks a word names in drive, derived from each body's clamps (`quackd.drive.presets`)."""

from __future__ import annotations

import math
import re
from pathlib import Path

import pytest

from quackd.adapters.factory import ADAPTER_NAMES, BACKENDS, RobotSpec, describe
from quackd.drive.presets import SHARE, TURN_DEG, presets_for
from quackd.drive.run import SESSION_BUDGETS, session_duck
from quackd.drive.terminal import QUIT_AGAIN_S
from quackd.duckfile.schema import Budgets
from quackd.verbs.aliases import ALIASES
from quackd.verbs.core import MAX_VX, MAX_WZ, MoveParams, speed_limits

BODIES = [RobotSpec(a, b) for a in ADAPTER_NAMES for b in BACKENDS.get(a, ())]


def test_the_share_and_the_turn_are_moves_own_defaults() -> None:
    """No number of the presets' own: the share is `move`'s default speed over its bound, and the
    turn `search_scan`'s default step."""
    assert SHARE == 0.5 and TURN_DEG == 45.0


@pytest.mark.parametrize("spec", BODIES, ids=lambda s: f"{s.adapter}:{s.backend}")
def test_every_shipped_body_gets_presets_inside_its_clamps_or_none(spec: RobotSpec) -> None:
    """A body that walks gets them, each inside its own clamps and every turn the same angle; an
    arm, and anything that does not move about, gets none; and no preset is spelled as a verb or
    an alias of the body, so a word means one thing there."""
    manifest = describe(spec)
    verbs = {v.name for v in manifest.verbs}
    presets = presets_for(manifest, verbs)
    if "move" not in verbs or manifest.mobility == "none":
        assert presets == {}, "a body that does not walk was given walks"
        return
    max_vx, max_vy, max_wz = speed_limits(manifest)
    assert {"forward", "back", "turn left", "turn right"} <= set(presets)
    assert ("step left" in presets) == (max_vy > 0)
    for preset in presets.values():
        p = preset.params
        assert abs(p.vx) <= max_vx and abs(p.vy) <= max_vy and abs(p.wz) <= max_wz, preset
        if p.wz:
            turned = math.degrees(abs(p.wz) * p.duration_s)
            assert turned == pytest.approx(TURN_DEG, abs=0.2), preset
    assert not set(presets) & (verbs | set(ALIASES)), "a preset is spelled as a verb"


def test_on_the_microduck_forward_is_move_with_its_own_defaults() -> None:
    manifest = describe(RobotSpec("microduck", "mock"))
    presets = presets_for(manifest, {v.name for v in manifest.verbs})
    assert presets["forward"].params == MoveParams()
    assert presets["forward"].says() == "forward: move(duration_s=1, vx=0.15)"
    assert presets["turn left"].says() == "turn left: move(duration_s=1.571, vx=0, wz=0.5)"


@pytest.mark.parametrize("spec", BODIES, ids=lambda s: f"{s.adapter}:{s.backend}")
def test_what_a_preset_hands_move_is_the_walk_it_means(spec: RobotSpec) -> None:
    """The call is what `move` is handed, and `move` fills in what is left out with its own
    defaults, a speed of 0.15 among them. A turn handed without its zero speed turned and walked
    forward at once, which a captured session caught as `walked vx=0.15 ... wz=0.50`."""
    manifest = describe(spec)
    for preset in presets_for(manifest, {v.name for v in manifest.verbs}).values():
        assert MoveParams(**preset.call) == preset.params, preset.name


def test_a_preset_move_would_refuse_is_left_out() -> None:
    """A third party's body faster than the schema's bounds would be handed a walk `move` itself
    refuses, so that walk is not offered rather than offered and refused."""
    manifest = describe(RobotSpec("microduck", "mock")).model_copy(
        update={"limits": {"max_vx": 2.0, "max_vy": 0.2, "max_wz": 1.0}}
    )
    presets = presets_for(manifest, {v.name for v in manifest.verbs})
    assert "forward" not in presets and "back" not in presets
    assert "turn left" in presets and "step left" in presets


GUIDE = Path(__file__).resolve().parents[1] / "docs" / "guides" / "drive.md"


def test_the_drive_guide_quotes_the_codes_own_numbers() -> None:
    """The guide's presets table is the Microduck's presets as the code derives them, row for
    row, and every other number it gives is the code's: the turn and the Microduck's clamp on
    it, the battery rule, the budgets of a session and of a task, and the second Ctrl-C. The
    guide once said the Microduck's clamps were the schema's, which its turn's is not."""
    text = GUIDE.read_text(encoding="utf-8")
    manifest = describe(RobotSpec("microduck", "mock"))
    presets = presets_for(manifest, {v.name for v in manifest.verbs})
    rows = re.findall(r"^\| `([a-z ]+)` \| `(move\([^`]*\))` \|$", text, flags=re.MULTILINE)
    assert rows == [(p.name, p.says().split(": ", 1)[1]) for p in presets.values()]
    max_vx, _max_vy, max_wz = speed_limits(manifest)
    assert max_vx == MAX_VX and max_wz != MAX_WZ, "the guide's sentence on the clamps is stale"
    front = session_duck(["move", "stop"]).frontmatter
    assert front.battery_abort_percent is not None
    for needle in (
        f"turns at most {max_wz:g} rad/s",
        f"{TURN_DEG:g} degrees",
        f"below {front.battery_abort_percent:g}%",
        f"`max_steps: {SESSION_BUDGETS.max_steps}`",
        f"`max_minutes: {SESSION_BUDGETS.max_minutes:g}`",
        f"{Budgets().max_steps} steps and {Budgets().max_minutes:g} minutes",
        f"within {QUIT_AGAIN_S:g} seconds",
    ):
        assert needle in text, needle
    assert SHARE == 0.5 and "which is half" in text
