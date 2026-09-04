"""The Microduck's own verbs: one per behaviour the robot actually ships.

Each maps to an intent the transport understands (and, on hardware, to a VERIFIED upstream
method; see `transport/upstream_api.py`). These are the 0.3 built-ins that are not core
verbs, moved here unchanged, plus the Microduck's `say`: upstream has seven duck sounds and
no TTS, so text is mapped to the tone that fits the mood.
"""

from __future__ import annotations

import math
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from quackd.transport.base import DuckState, Intent
from quackd.verbs.core import SayParams, send_or_fail
from quackd.verbs.registry import NoParams, Precondition, Verb, VerbContext, VerbResult


class KickParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    leg: Literal["left", "right"] = Field(default="right", description="Which leg kicks.")


class QuackParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str | None = Field(
        default=None,
        max_length=200,
        description="What to say. The robot only has duck sounds; text is mapped to a tone.",
    )


GazeDirection = Literal["center", "left", "right", "up", "down"]


class GazeParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    direction: GazeDirection = Field(default="center", description="Where to point the head.")
    bearing_deg: float | None = Field(
        default=None,
        ge=-90,
        le=90,
        description="Optional exact bearing (+ = left). Overrides direction.",
    )


# ── preconditions the manifest references by name ───────────────────────────────────────


def _blind(state: DuckState) -> bool:
    """True when nothing is reporting falls, so `state.fallen` is silence and not a verdict.

    `fall_detection` is absent on the backends that always know (the simulator, the mock), and
    False on a real robot whose state stream has stopped or never started.
    """
    return state.extras.get("fall_detection") is False


def _not_fallen(state: DuckState) -> str | None:
    if _blind(state):
        return "nothing is reporting falls, so quackd cannot tell whether the duck is upright"
    return "the duck has fallen; run stand_up first" if state.fallen else None


def _standing(state: DuckState) -> str | None:
    if _blind(state):
        return "nothing is reporting posture, so quackd cannot tell whether the duck is standing"
    if state.fallen:
        return "the duck has fallen; run stand_up first"
    if state.posture == "sitting":
        return "the duck is sitting; run stand first"
    return None


def microduck_conditions() -> dict[str, Precondition]:
    return {"not_fallen": _not_fallen, "standing": _standing}


# ── the verbs ───────────────────────────────────────────────────────────────────────────


async def _sit_toggle(ctx: VerbContext, want: Literal["sitting", "standing"]) -> VerbResult:
    """One upstream toggle, so posture is the only thing telling `sit` and `stand` apart.

    Upstream has `sit_toggle` and nothing that means "sit" or "stand" on its own: which way the
    robot goes is decided by which way it is currently facing. Firing it without reading posture
    is therefore a coin flip, and the losing side is `stand` sitting a standing duck down and
    reporting success. Refuse instead — a toggle nobody can aim is not a verb.
    """
    state = await ctx.transport.get_state()
    if state.posture == want:
        return VerbResult.success(f"already {want}")
    if state.posture == "unknown":
        return VerbResult.fail(
            f"cannot {('sit', 'stand')[want == 'standing']}: posture is unknown, and upstream "
            "has one sit_toggle rather than a sit and a stand, so quackd would be guessing "
            "which way it goes. Use the gamepad, or a backend that reports posture."
        )
    if (fail := await send_or_fail(ctx, Intent.do("sit_toggle"))) is not None:
        return fail
    await ctx.transport.sleep(2.0)
    state = await ctx.transport.get_state()
    if state.posture == want:
        return VerbResult.success(f"now {want}")
    return VerbResult.fail(f"asked to be {want} but posture is {state.posture}")


async def sit(ctx: VerbContext, _: NoParams) -> VerbResult:
    return await _sit_toggle(ctx, "sitting")


async def stand(ctx: VerbContext, _: NoParams) -> VerbResult:
    return await _sit_toggle(ctx, "standing")


async def kick(ctx: VerbContext, p: KickParams) -> VerbResult:
    skill: Literal["kick_left", "kick_right"] = "kick_left" if p.leg == "left" else "kick_right"
    ack = await ctx.transport.send_intent(Intent.do(skill))
    if not ack.accepted:
        return VerbResult.fail(f"kick missed or refused: {ack.reason or 'no reason given'}")
    await ctx.transport.sleep(1.5)
    state = await ctx.transport.get_state()
    moved = state.extras.get("last_kick_ball_moved_m")
    if moved is not None:
        return VerbResult.success(
            f"kicked with {p.leg} leg, ball moved {moved:.2f} m", ball_moved_m=moved
        )
    return VerbResult.success(f"kicked with {p.leg} leg", ball_moved_m=None)


async def grab(ctx: VerbContext, _: NoParams) -> VerbResult:
    if (fail := await send_or_fail(ctx, Intent.do("ground_pick"))) is not None:
        return fail
    await ctx.transport.sleep(3.0)
    state = await ctx.transport.get_state()
    if state.holding:
        return VerbResult.success("scooped something up — it is in the beak", holding=True)
    return VerbResult.fail(
        "scooped at the floor but the beak is empty; reposition and retry", holding=False
    )


async def stand_up(ctx: VerbContext, _: NoParams) -> VerbResult:
    if (fail := await send_or_fail(ctx, Intent.enable(True))) is not None:
        return fail
    await ctx.transport.sleep(3.0)
    state = await ctx.transport.get_state()
    if state.fallen:
        return VerbResult.fail("still down; the onboard recovery has not finished")
    if _blind(state):
        # Reporting "upright" here is how a face-down duck used to pass: nothing was reporting
        # falls, so `fallen` was False, so this said it worked.
        return VerbResult.fail("asked the policy to run, but nothing reports whether it is upright")
    return VerbResult.success("upright")


def quack_tag_for(text: str | None) -> str:
    """Upstream has seven duck sounds and no TTS. Pick the one that fits the mood."""
    if not text:
        return "chirp"
    t = text.lower()
    if any(w in t for w in ("hello", "hi ", "hi!", "hey", "greet")):
        return "greet"
    if "?" in t:
        return "inquire"
    if any(w in t for w in ("alarm", "intruder", "stop!", "warning", "alert")):
        return "alarm"
    if any(w in t for w in ("yay", "whee", "wooo", "success", "did it", "got it")):
        return "wheee"
    if any(w in t for w in ("hmm", "sad", "sorry", "oh no")):
        return "coo"
    if "!" in t:
        return "peck"
    return "chirp"


async def quack(ctx: VerbContext, p: QuackParams) -> VerbResult:
    tag = quack_tag_for(p.text)
    if (fail := await send_or_fail(ctx, Intent.sound(tag, p.text))) is not None:
        return fail
    shown = f" ({p.text!r})" if p.text else ""
    return VerbResult.success(f"quacked [{tag}]{shown}", tag=tag, text=p.text)


async def say(ctx: VerbContext, p: SayParams) -> VerbResult:
    """The Microduck's `say`: the same seven tones as `quack`, text required."""
    tag = quack_tag_for(p.text)
    if (fail := await send_or_fail(ctx, Intent.sound(tag, p.text))) is not None:
        return fail
    return VerbResult.success(f"said {p.text!r} as [{tag}]", tag=tag, text=p.text)


async def gaze(ctx: VerbContext, p: GazeParams) -> VerbResult:
    bearing = p.bearing_deg
    pitch = 0.0
    if bearing is None:
        bearing = {"center": 0.0, "left": 45.0, "right": -45.0, "up": 0.0, "down": 0.0}[p.direction]
        pitch = {"up": 0.25, "down": -0.15}.get(p.direction, 0.0)
    rad = math.radians(bearing)
    point = (math.cos(rad), math.sin(rad), pitch)
    if (fail := await send_or_fail(ctx, Intent.look(*point))) is not None:
        return fail
    return VerbResult.success(
        f"looking {p.direction if p.bearing_deg is None else f'{bearing:+.0f}°'}"
    )


MICRODUCK_VERBS: dict[str, Verb] = {
    v.name: v
    for v in (
        Verb("sit", "Sit down.", sit, NoParams, timeout_s=10),
        Verb("stand", "Stand up from sitting.", stand, NoParams, timeout_s=10),
        Verb(
            "kick",
            "Kick forward with one leg. Only connects if the ball is < 0.3 m away and "
            "roughly ahead.",
            kick,
            KickParams,
            timeout_s=10,
            done_condition="the kick animation finished; the result says whether the ball moved.",
        ),
        Verb(
            "grab",
            "Scoop at the floor with the beak (open-loop). Works only with the object right "
            "under the beak.",
            grab,
            NoParams,
            timeout_s=10,
            done_condition="the scoop finished; the result says whether the beak holds something.",
        ),
        Verb("stand_up", "Recover to standing after a fall.", stand_up, NoParams, timeout_s=15),
        Verb(
            "quack",
            "Make a duck sound, optionally with text (mapped to a tone).",
            quack,
            QuackParams,
            timeout_s=5,
        ),
        Verb(
            "gaze", "Point the head in a direction or at a bearing.", gaze, GazeParams, timeout_s=5
        ),
        Verb(
            "say",
            "Say something: the text is mapped to one of the robot's seven duck tones.",
            say,
            SayParams,
            timeout_s=5,
            core=True,
        ),
    )
}
