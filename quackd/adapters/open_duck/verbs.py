"""The Open Duck Mini v2's own verbs: a duck that walks, looks and chirps, and nothing else.

This robot has no beak, no gripper and no kick, sit or get-up policy, so it declares far
fewer verbs than the Microduck. What it does have is a four axis head (neck pitch, head
pitch, head yaw, head roll) and, on some builds, a speaker and two antenna servos.

Two vocabularies here are quackd's own rather than upstream's, on purpose. Upstream's
`Sounds` class plays whatever `.wav` files it finds in a directory, so the file names vary
per build and cannot be spelled honestly at `describe()` time; quackd sends a *mood* and
the bridge picks a file. Antenna gestures are the same: upstream exposes servo positions,
not named gestures. Both are documented as UNVERIFIED-by-construction in `upstream_api.py`.
"""

from __future__ import annotations

import math
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from quackd.transport.base import DuckState, Intent
from quackd.verbs.core import SayParams, send_or_fail
from quackd.verbs.registry import Precondition, Verb, VerbContext, VerbResult

# Upstream's own runtime clamps, read from XBoxController (see upstream_api.py). quackd
# never asks for more than these, and for the head it stays inside them by HEAD_SAFETY:
# upstream's README calls head control "very experimental" and warns it can break the head.
MAX_VX = 0.15
MAX_VY = 0.2
MAX_WZ = 1.0
HEAD_SAFETY = 0.8
NECK_PITCH_RANGE = (-0.34, 1.1)
HEAD_PITCH_RANGE = (-0.78, 0.3)
HEAD_YAW_RANGE = (-0.5, 0.5)
HEAD_ROLL_RANGE = (-0.5, 0.5)

#: Our mood vocabulary. The bridge maps each to a real `.wav` on the robot, or to a random
#: one when nothing matches. Never an upstream file name.
MOODS: tuple[str, ...] = ("greet", "inquire", "alert", "happy", "sad", "chirp")

#: Our antenna gestures. The bridge turns each into servo positions on GPIO D12 and D13.
GESTURES: tuple[str, ...] = ("perk", "droop", "wiggle")

GAZE_YAW_DEG = math.degrees(HEAD_YAW_RANGE[1] * HEAD_SAFETY)
GAZE_PITCH_DEG = math.degrees(-HEAD_PITCH_RANGE[0] * HEAD_SAFETY)
EXPRESS_S = 1.0

GazeDirection = Literal["center", "left", "right", "up", "down"]
_GAZE_BEARING: dict[str, float] = {
    "center": 0.0,
    "left": 20.0,
    "right": -20.0,
    "up": 0.0,
    "down": 0.0,
}
_GAZE_PITCH: dict[str, float] = {"up": 12.0, "down": -12.0}


class OpenDuckGazeParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    direction: GazeDirection = Field(default="center", description="Where to point the head.")
    bearing_deg: float | None = Field(
        default=None,
        ge=-GAZE_YAW_DEG,
        le=GAZE_YAW_DEG,
        description="Exact yaw (+ = left). Overrides direction. The neck travel is small.",
    )
    pitch_deg: float | None = Field(
        default=None,
        ge=-GAZE_PITCH_DEG,
        le=GAZE_PITCH_DEG,
        description="Exact pitch (+ = up). Overrides direction.",
    )


class QuackParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str | None = Field(
        default=None,
        max_length=200,
        description="What to say. The robot has no voice; text is mapped to a mood sound.",
    )


class ExpressParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    gesture: Literal["perk", "droop", "wiggle"] = Field(
        default="wiggle", description="An antenna gesture."
    )


# ── preconditions the manifest references by name ───────────────────────────────────────


def _stale(state: DuckState) -> str | None:
    """A state read that failed is silence, not a verdict.

    The bridge used to swallow the error and hand back a default frame, so `fallen` was
    False, `policy_running` was None, both preconditions passed, and quackd would start a
    `move` into a link that was not there. Refusing is the fail-closed answer, and it is
    bounded: the heartbeat ends the run a beat later anyway."""
    why = state.extras.get("state_stale")
    if not why:
        return None
    return f"the duck's state could not be read ({why}), so quackd cannot tell what it is doing"


def _not_fallen(state: DuckState) -> str | None:
    """The one precondition that matters on this body. There is no get-up policy for the
    Open Duck Mini v2, so a fallen duck is a job for a human, not for another verb.

    Deliberately NOT blind-refusing the way the Microduck's does. There, `fall_detection`
    goes quiet and comes back; here it is a constant False on the only hardware backend, so
    refusing on it would refuse every locomotion verb forever and decommission the robot.
    The blindness is surfaced instead — in `summary()`, in the manifest, in `doctor` and in
    a warning before the first run — and the human on the stand is the guard."""
    if stale := _stale(state):
        return stale
    if not state.fallen:
        return None
    return "the duck has fallen and this robot has no get-up policy; stand it up by hand"


def _policy_running(state: DuckState) -> str | None:
    """The walk policy can be paused by `start_paused` in duck_config.json. A backend that
    does not report it is assumed running.

    Upstream's own unpause is its gamepad's A button, and the bridge replaced that pad, so
    there is no A button in the process to press — which is why this names the config file
    and not a control the operator no longer has."""
    if stale := _stale(state):
        return stale
    running = state.extras.get("policy_running")
    if running in (None, True):
        return None
    return (
        "the walk policy is paused. quackd cannot unpause it: upstream's only unpause is its "
        "gamepad's A button, and the bridge replaced that pad. Set start_paused false in "
        "duck_config.json on the robot and restart quackd-duck-bridge"
    )


def open_duck_conditions() -> dict[str, Precondition]:
    return {"not_fallen": _not_fallen, "policy_running": _policy_running}


# ── the verbs ───────────────────────────────────────────────────────────────────────────


def mood_for(text: str | None) -> str:
    """Pick the mood that fits a phrase. The duck cannot pronounce words (ADR-0024)."""
    if not text:
        return "chirp"
    t = text.lower()
    if any(w in t for w in ("hello", "hi ", "hi!", "hey", "welcome", "greet")):
        return "greet"
    if "?" in t:
        return "inquire"
    if any(w in t for w in ("alarm", "intruder", "stop!", "warning", "alert", "careful")):
        return "alert"
    if any(w in t for w in ("yay", "success", "did it", "got it", "found", "well done")):
        return "happy"
    if any(w in t for w in ("sad", "sorry", "oh no", "lost", "missed", "hmm")):
        return "sad"
    if "!" in t:
        return "happy"
    return "chirp"


async def quack(ctx: VerbContext, p: QuackParams) -> VerbResult:
    mood = mood_for(p.text)
    if (fail := await send_or_fail(ctx, Intent.sound(mood, p.text))) is not None:
        return fail
    shown = f" ({p.text!r})" if p.text else ""
    return VerbResult.success(f"quacked [{mood}]{shown}", mood=mood, text=p.text)


async def say(ctx: VerbContext, p: SayParams) -> VerbResult:
    """No text to speech on this robot: the text is logged and voiced as a mood sound."""
    mood = mood_for(p.text)
    if (fail := await send_or_fail(ctx, Intent.sound(mood, p.text))) is not None:
        return fail
    return VerbResult.success(f"said {p.text!r} as [{mood}]", mood=mood, text=p.text)


async def gaze(ctx: VerbContext, p: OpenDuckGazeParams) -> VerbResult:
    bearing = p.bearing_deg if p.bearing_deg is not None else _GAZE_BEARING[p.direction]
    pitch = p.pitch_deg if p.pitch_deg is not None else _GAZE_PITCH.get(p.direction, 0.0)
    yaw = math.radians(bearing)
    # a unit vector in the body frame; the backends recover yaw and pitch from it
    point = (math.cos(yaw), math.sin(yaw), math.tan(math.radians(pitch)))
    ack = await ctx.transport.send_intent(Intent.look(*point))
    if not ack.accepted:
        return VerbResult.fail(f"look refused: {ack.reason or 'no reason given'}")
    shown = p.direction if p.bearing_deg is None and p.pitch_deg is None else f"{bearing:+.0f}°"
    return VerbResult.success(
        f"looking {shown}" + (" (clamped)" if ack.reason else ""),
        head_yaw_deg=bearing,
        head_pitch_deg=pitch,
        clamped=bool(ack.reason),
    )


async def express(ctx: VerbContext, p: ExpressParams) -> VerbResult:
    if (fail := await send_or_fail(ctx, Intent.do(f"antennas:{p.gesture}"))) is not None:
        return fail
    await ctx.transport.sleep(EXPRESS_S)
    return VerbResult.success(f"antennas: {p.gesture}", gesture=p.gesture)


def open_duck_verbs() -> dict[str, Verb]:
    verbs = [
        Verb(
            "say",
            "Say something. This robot has no voice, so the text is voiced as the closest "
            "mood sound and logged verbatim.",
            say,
            SayParams,
            timeout_s=10,
            core=True,
        ),
        Verb(
            "quack",
            "Make a duck sound, optionally with text (mapped to a mood).",
            quack,
            QuackParams,
            timeout_s=10,
        ),
        Verb(
            "gaze",
            f"Point the head: a direction, or an exact yaw and pitch. The neck travel is "
            f"small (about {GAZE_YAW_DEG:.0f}° either way).",
            gaze,
            OpenDuckGazeParams,
            timeout_s=5,
        ),
        Verb(
            "express",
            "Move the antennas: perk them up, let them droop, or wiggle them.",
            express,
            ExpressParams,
            timeout_s=10,
        ),
    ]
    return {v.name: v for v in verbs}
