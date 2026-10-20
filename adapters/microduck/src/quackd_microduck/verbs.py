"""The Microduck's own verbs: one per behaviour the robot actually ships.

Each maps to an intent the transport understands (and, on hardware, to a VERIFIED upstream
method; see `quackd_microduck/upstream_api.py`). These are the 0.3 built-ins that are not core
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
from quackd.verbs.watch import HeadSpec, watch_verb
from quackd_microduck import upstream_api as up

MICRODUCK_HEAD = HeadSpec(
    # `gaze`'s own `left` and `right`, half the 90 its `bearing_deg` may ask for, and inside both
    # simulators' 60.
    yaw_deg=45.0,
    # Up as far as `gaze`'s `up` goes (atan 0.25), and down as far as robotd's own glances tilt it,
    # 0.2 rad (`up.IDLE_HEAD`). Wider than `gaze` downward, and nothing more until a duck is
    # measured: nobody has commanded a real Microduck's head further than either.
    pitch_up_deg=14.0,
    pitch_down_deg=11.5,
    step_yaw_deg=15.0,
    step_pitch_deg=8.0,
    # About 0.3 s for the head to get there, and 0.1 s more because mediad's PNG carries no
    # capture time, so a frame is taken for after the move only once its request is.
    settle_s=0.4,
    # Sent again as often as the transport holds a gaze (`GAZE_HOLD_S`), half of robotd's wait
    # before it counts the duck still and glances (`up.IDLE_HEAD`).
    hold_s=up.IDLE_AFTER_S / 2,
    idle_after_s=up.IDLE_AFTER_S,
    source=(
        "gaze's own reach and robotd's idle glances, until a Microduck's head is measured "
        f"(up.IDLE_HEAD, read {up.READ_ON})"
    ),
)
"""The Microduck's head as `watch` holds it: starting values, until a duck is measured."""


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


def _why_unknown(state: DuckState) -> str:
    """` (why)`, to follow the word `unknown`, when the transport said why posture is unknown;
    otherwise nothing. Only `jsonrpc` says, in `posture_reason`."""
    why = state.extras.get("posture_reason") if state.posture == "unknown" else None
    return f" ({why})" if why else ""


def _held(state: DuckState) -> str | None:
    """Why nothing may move a duck that is in somebody's hands, or None when it is not.

    Read before `fallen` everywhere. A duck held at an angle reads as fallen, and "fallen"
    points the pilot at `stand_up`, which asks the policy to drive legs a person is holding.
    Only a robot whose pick-up detector says so sets `picked_up` (`jsonrpc`); every other
    backend leaves it out, which reads as nobody holding the duck, as it always has.
    """
    if state.extras.get("picked_up") is True:
        return "somebody is holding the duck, and nothing that moves it is sent until it is down"
    return None


def _walk_held(state: DuckState) -> str | None:
    """Why a walk is held back for a verb that is still running, or None when none is.

    `jsonrpc` holds `robot.move` back for a verb that was walking when the duck stopped standing
    or robotd stopped taking its twists, until that verb stops, and says so in `move_hold`. The
    duck may be standing again by then, and posture reads `unknown` for as long, so the refusal
    names the hold rather than a posture nobody could tell."""
    hold = state.extras.get("move_hold")
    if not hold:
        return None
    return f"a verb that was walking is still running, and quackd holds its walk back ({hold})"


def _robotds_fall(state: DuckState) -> str | None:
    """What robotd's own answer to a fall is doing with the duck, or None when it has none.

    A robotd whose `limp_fall` is on, as it is by default again from daemon-v0.16.2, takes a
    falling duck off its policy, lets it land limp, ramps it to its standing pose and only then
    hands it back (`up.LIMP_FALL`), and it labels every tick of that with one of two labels. The
    label is read whatever the posture says, since a duck that has landed reads `fallen` before
    any label is looked at. safety.limp is true on the first half's ticks and on no others, so
    the label covers it too. The simulators and the mock never report either label."""
    if state.policy not in up.LIMP_FALL_LABELS:
        return None
    return f"policy {state.policy!r}: {up.NOT_STANDING_LABELS[state.policy]}"


def _not_fallen(state: DuckState) -> str | None:
    if _blind(state):
        return "nothing is reporting falls, so quackd cannot tell whether the duck is upright"
    if (held := _held(state)) is not None:
        return held
    return "the duck has fallen; run stand_up first" if state.fallen else None


def _standing(state: DuckState) -> str | None:
    """Standing, and known to be: `unknown` refuses, where it used to pass.

    The simulators and the mock always know their posture. On a real robot `unknown` is
    everything quackd cannot vouch for, a duck mid-skill, rising off its seat, limp, or
    reporting a policy label quackd has never read, and the transport says which in
    `posture_reason`, so the refusal can too.
    """
    if _blind(state):
        return "nothing is reporting posture, so quackd cannot tell whether the duck is standing"
    if (held := _held(state)) is not None:
        return held
    if state.fallen or state.posture == "fallen":
        return "the duck has fallen; run stand_up first"
    if state.posture == "sitting":
        return "the duck is sitting; run stand first"
    if state.posture == "unknown" and (walk := _walk_held(state)) is not None:
        return f"{walk}; wait for it to end"
    if state.posture == "unknown":
        return f"posture is unknown{_why_unknown(state)}, so quackd cannot tell it is standing"
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
            f"cannot {('sit', 'stand')[want == 'standing']}: posture is unknown"
            f"{_why_unknown(state)}, and upstream has one sit_toggle rather than a sit and a "
            "stand, so quackd would be guessing which way it goes. Use the gamepad, or a "
            "backend that reports posture."
        )
    if (fail := await send_or_fail(ctx, Intent.do("sit_toggle"))) is not None:
        return fail
    await ctx.transport.sleep(2.0)
    state = await ctx.transport.get_state()
    if state.posture == want:
        return VerbResult.success(f"now {want}")
    return VerbResult.fail(
        f"asked to be {want} but posture is {state.posture}{_why_unknown(state)}"
    )


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


STAND_UP_POLL_S = 0.25
"""How often `stand_up` reads the duck while it waits for it to stand."""

STAND_UP_WAIT_S = 10.0
"""How long `stand_up` waits for the duck to read as standing after its request: inside the verb's
own 15 s timeout, and well past the about 2.1 s robotd's own answer to a fall takes by default
(`up.LIMP_FALL`). How long the policy then takes to get a duck up nobody has measured under
quackd, which is why the verb reads the duck as it goes rather than once at a guessed moment."""


async def stand_up(ctx: VerbContext, _: NoParams) -> VerbResult:
    """Ask the robot's policy to drive again, for a duck that is down and that nobody holds.

    `robot.enable` is policy execution rather than a flag, and upstream says it can bring a
    limp robot up as a side effect of being asked to drive, so it is motion, and the state is
    read before it goes out instead of only after. A duck in somebody's hands is refused first:
    held at an angle it reads as fallen, which is the very duck this verb is for, and enabling
    the policy then drives legs a person is holding. A duck already standing is told so and sent
    nothing. One upright on its seat is pointed at `stand`, the toggle that gets it up, which
    enabling the policy is not. A duck nothing reports on is refused too, since nobody can see
    whether it is held. Before the request goes out, two more are refused. One on a robot that
    does not report being held at all: a robotd from before its pick-up detector leaves
    `safety.picked_up` out of every frame, and an unknown pick-up is refused here as an unknown
    posture is refused a walk, where it used to read as nobody holding the duck. And one while a
    walk is held back for a verb still running, since the duck reads `unknown` for that while
    it may well be standing. A fallen duck, and one whose posture is `unknown` for any other
    reason, gets the request, and afterwards only a duck that reads standing is called upright.

    After the request the duck is read every `STAND_UP_POLL_S`, and nothing more is sent. It is
    called upright the moment it reads standing, and the wait ends at once on a duck somebody
    picks up or nothing reports on. It used to be read once, three seconds after the request,
    and a duck still down then was told it may need a hand even while the policy was getting it
    up. Only once `STAND_UP_WAIT_S` has passed is a duck that never stood judged: one that
    robotd's own answer to a fall still has (`_robotds_fall`) is told so, and to run the verb
    again in a moment, since robotd hands the duck back to its policy once it has posed it, and
    one still down is told it may need a hand.
    """
    state = await ctx.transport.get_state()
    if (held := _held(state)) is not None:
        return VerbResult.fail(f"cannot stand_up: {held}")
    if _blind(state):
        return VerbResult.fail(
            "cannot stand_up: nothing is reporting whether the duck is down or in somebody's "
            "hands, so quackd will not ask the policy to drive it"
        )
    if state.posture == "standing":
        return VerbResult.success("already upright")
    if state.posture == "sitting":
        return VerbResult.fail("the duck is upright and seated: run stand")
    if (walk := _walk_held(state)) is not None:
        return VerbResult.fail(f"cannot stand_up: {walk}; wait for it to end")
    if state.extras.get("pickup_detection") is False:
        # `jsonrpc` alone says this, from a fresh frame that has no `safety.picked_up`
        return VerbResult.fail(
            "cannot stand_up: this robot does not report whether somebody is holding the duck "
            "(no safety.picked_up), so quackd will not ask the policy to drive legs that may be "
            "in somebody's hands. Stand it up by hand"
        )
    if (fail := await send_or_fail(ctx, Intent.enable(True))) is not None:
        return fail
    waited = 0.0
    while True:
        await ctx.transport.sleep(STAND_UP_POLL_S)
        waited += STAND_UP_POLL_S
        state = await ctx.transport.get_state()
        if _blind(state):
            # Reporting "upright" here is how a face-down duck used to pass: nothing was
            # reporting falls, so `fallen` was False, so this said it worked.
            return VerbResult.fail(
                "asked the policy to run, but nothing reports whether it is upright"
            )
        if (held := _held(state)) is not None:
            return VerbResult.fail(f"asked the policy to run, but {held}")
        if state.posture == "standing":
            return VerbResult.success("upright")
        if waited >= STAND_UP_WAIT_S:
            break
    if (fall := _robotds_fall(state)) is not None:
        # Read before `fallen`, which a duck robotd is posing on the floor reads too. robotd has
        # not handed it back to the policy yet, so it is too soon to say whether it gets up.
        return VerbResult.fail(
            f"asked the policy to run, but robotd is still answering the fall itself ({fall}). "
            "It hands the duck back to the policy once it has posed it, so run stand_up again in "
            "a moment to see whether the duck is up"
        )
    if state.fallen:
        # It used to say an onboard recovery had not finished, which sent the pilot to wait for
        # one. robotd runs none on daemon-v0.16.1 by default, and from daemon-v0.16.2 a duck it
        # caught falling is back with the policy once it has posed it (`_robotds_fall`), which
        # upstream says gets it up and nobody has seen do so under quackd, so one still down may
        # need a hand.
        return VerbResult.fail(
            f"asked the policy to run, and the duck is still down {STAND_UP_WAIT_S:g} s later: "
            "robotd does not promise to get a fallen duck up, so it may need a hand"
        )
    # Never standing by now, or the wait above would have said so.
    return VerbResult.fail(
        f"asked the policy to run, and posture is {state.posture}{_why_unknown(state)}"
    )


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
        Verb(
            "stand_up",
            "Ask the robot's policy to get a fallen duck up. It may not manage, and the result "
            "says whether the duck is upright, or that the robot is still answering the fall "
            "itself.",
            stand_up,
            NoParams,
            timeout_s=15,
        ),
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
        # Declared only on a duck with a camera (`microduck_manifest`), and offered only on a run
        # that can look through a decision LLM (`quackd.verbs.watch.withdraw_watch`).
        watch_verb(MICRODUCK_HEAD),
    )
}
