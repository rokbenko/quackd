"""Huebot's own verbs: the Microduck's, copied and renamed, less `grab` and the two that turn
the head.

Each maps to an intent the transport understands and, on `jsonrpc`, to a call the protocol names
(`quackd_huebot/upstream_api.py`). `gaze` and `watch` are not here: they send `robot.look`, which
Huebot's shim does not answer yet, so a later release adds them once its maker says it does.
`grab` is not here either: it sends `ground_pick`, and it waits for Huebot's maker to say what
`ground_pick` does on Huebot and how its state reports that the robot holds something. The
three that send `robot.do` are offered on a connected Huebot only for the skills its shim lists
(`SKILLS_OF`). `say` is `quack` with its text required: the protocol has seven tones and no TTS,
so text is mapped to the tone that fits the mood.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from quackd.transport.base import DuckState, Intent
from quackd.verbs.core import SayParams, send_or_fail
from quackd.verbs.registry import NoParams, Precondition, Verb, VerbContext, VerbResult
from quackd_huebot import upstream_api as up

#: The skills `sit`, `stand` and `kick` send through `robot.do`, by the names robotd
#: answers to (`up.SKILLS`).
SIT_TOGGLE = "sit_toggle"
KICK_SKILLS: dict[str, Literal["kick_left", "kick_right"]] = {
    "left": "kick_left",
    "right": "kick_right",
}

SKILLS_OF: dict[str, tuple[str, ...]] = {
    "sit": (SIT_TOGGLE,),
    "stand": (SIT_TOGGLE,),
    "kick": tuple(KICK_SKILLS.values()),
}
"""Each verb that sends `robot.do`, and every skill it can send. A connected Huebot offers one
only when its shim's `robot.subscribe` answer lists every skill the verb sends, so `kick` needs
both legs' kicks, since either may be asked for (`quackd_huebot.huebot_manifest`)."""

STAND_UP_POLL_S = 0.25
"""How often `stand_up` reads the robot again while it waits for it to stand."""
STAND_UP_WAIT_S = 10.0
"""The longest `stand_up` waits for the robot to read standing after its request, inside the
verb's own 15 s timeout, before it says the robot may need a hand."""


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


# ── preconditions the manifest references by name ───────────────────────────────────────


def _blind(state: DuckState) -> bool:
    """True when nothing is reporting falls, so `state.fallen` is silence and not a verdict.

    `fall_detection` is absent on the mock, which always knows, and False on a real robot whose
    state stream has stopped or never started.
    """
    return state.extras.get("fall_detection") is False


def _why_unknown(state: DuckState) -> str:
    """` (why)`, to follow the word `unknown`, when the transport said why posture is unknown;
    otherwise nothing. Only `jsonrpc` says, in `posture_reason`."""
    why = state.extras.get("posture_reason") if state.posture == "unknown" else None
    return f" ({why})" if why else ""


def _held(state: DuckState) -> str | None:
    """Why nothing may move a robot that is in somebody's hands, or None when it is not.

    Read before `fallen` everywhere. A robot held at an angle reads as fallen, and "fallen"
    points the pilot at `stand_up`, which asks the robot's software to drive legs a person is
    holding. Only a robot whose state says so sets `picked_up` (`jsonrpc`); the mock leaves it
    out, which reads as nobody holding it.
    """
    if state.extras.get("picked_up") is True:
        return "somebody is holding Huebot, and nothing that moves it is sent until it is down"
    return None


def _unread(state: DuckState) -> str | None:
    """Why the robot's own safety flags cannot be read just now, or None when they can. Only
    `jsonrpc` says, in `safety_unread`: a flag the shim sent that is neither a JSON boolean
    nor null, which also makes posture `unknown`, so every verb that walks refuses on it."""
    unread = state.extras.get("safety_unread")
    return str(unread) if unread else None


def _walk_held(state: DuckState) -> str | None:
    """Why a walk is held back for a verb that is still running, or None when none is.

    `jsonrpc` holds `robot.move` back for a verb that was walking when the robot stopped
    standing or the shim stopped taking its twists, until that verb stops, and says so in
    `move_hold`. The robot may be standing again by then, and posture reads `unknown` for as
    long, so the refusal names the hold rather than a posture nobody could tell."""
    hold = state.extras.get("move_hold")
    if not hold:
        return None
    return f"a verb that was walking is still running, and quackd holds its walk back ({hold})"


def _answering_a_fall(state: DuckState) -> str | None:
    """What the robot's own answer to a fall is doing with it, or None when it has none.

    robotd labels the ticks of its limp-fall sequence, which takes a falling robot off its
    policy, lets it land limp and poses it before handing it back, with one of two labels
    (`up.LIMP_FALL_LABELS`). Nobody has said whether Huebot's shim has such a sequence. One that
    reports either label is in one, whatever the posture says, since a robot that has landed
    reads `fallen` before any label is looked at. The mock never reports either."""
    if state.policy not in up.LIMP_FALL_LABELS:
        return None
    return f"policy {state.policy!r}: {up.NOT_STANDING_LABELS[state.policy]}"


def _not_fallen(state: DuckState) -> str | None:
    if _blind(state):
        return "nothing is reporting falls, so quackd cannot tell whether Huebot is upright"
    if (held := _held(state)) is not None:
        return held
    return "Huebot has fallen; run stand_up first" if state.fallen else None


def _standing(state: DuckState) -> str | None:
    """Standing, and known to be: `unknown` refuses.

    The mock always knows its posture. On a real robot `unknown` is everything quackd cannot
    vouch for, a robot mid-skill, rising off its seat, limp, or reporting a policy label quackd
    has never read, and the transport says which in `posture_reason`, so the refusal can too.
    """
    if _blind(state):
        return "nothing is reporting posture, so quackd cannot tell whether Huebot is standing"
    if (held := _held(state)) is not None:
        return held
    if state.fallen or state.posture == "fallen":
        return "Huebot has fallen; run stand_up first"
    if state.posture == "sitting":
        return "Huebot is sitting; run stand first"
    if state.posture == "unknown" and (walk := _walk_held(state)) is not None:
        return f"{walk}; wait for it to end"
    if state.posture == "unknown":
        return f"posture is unknown{_why_unknown(state)}, so quackd cannot tell it is standing"
    return None


def huebot_conditions() -> dict[str, Precondition]:
    return {"not_fallen": _not_fallen, "standing": _standing}


# ── the verbs ───────────────────────────────────────────────────────────────────────────


async def _sit_toggle(ctx: VerbContext, want: Literal["sitting", "standing"]) -> VerbResult:
    """One toggle, so posture is the only thing telling `sit` and `stand` apart.

    The protocol has `sit_toggle` and nothing that means "sit" or "stand" on its own: which way
    the robot goes is decided by which way it is currently facing. Firing it without reading
    posture is therefore a coin flip, and the losing side is `stand` sitting a standing robot
    down and reporting success. Refuse instead: a toggle nobody can aim is not a verb.
    """
    state = await ctx.transport.get_state()
    if state.posture == want:
        return VerbResult.success(f"already {want}")
    if state.posture == "unknown":
        return VerbResult.fail(
            f"cannot {('sit', 'stand')[want == 'standing']}: posture is unknown"
            f"{_why_unknown(state)}, and the protocol has one sit_toggle rather than a sit and a "
            "stand, so quackd would be guessing which way it goes."
        )
    if (fail := await send_or_fail(ctx, Intent.do(SIT_TOGGLE))) is not None:
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
    ack = await ctx.transport.send_intent(Intent.do(KICK_SKILLS[p.leg]))
    if not ack.accepted:
        return VerbResult.fail(f"kick missed or refused: {ack.reason or 'no reason given'}")
    await ctx.transport.sleep(1.5)
    return VerbResult.success(f"kicked with {p.leg} leg")


def _upright(state: DuckState) -> bool:
    """Whether a reading says the robot stands, and can be believed: something reports falls,
    nobody holds it, it has not fallen, and posture reads `standing`, which on `jsonrpc` is a
    fresh frame whose label is `walk` or `stand` and whose walk nothing holds back."""
    return (
        not _blind(state)
        and _held(state) is None
        and not state.fallen
        and state.posture == "standing"
    )


async def stand_up(ctx: VerbContext, _: NoParams) -> VerbResult:
    """Ask the robot's software to drive again, for a robot that is down and that nobody holds,
    and wait for it to stand.

    `robot.enable` is policy execution rather than a flag on robotd, which says it can bring a
    limp robot up as a side effect of being asked to drive, so it is motion, and the state is
    read before it goes out instead of only after. A robot in somebody's hands is refused
    first: held at an angle it reads as fallen, which is the very robot this verb is for, and
    enabling the policy then drives legs a person is holding. One already standing is told so
    and sent nothing, and one upright on its seat is pointed at `stand`. One nothing reports on
    is refused, since nobody can see whether it is held, and so is one whose robot does not say
    whether it is held at all, and one whose walk is held back for a verb still running.

    After the request it reads the robot every `STAND_UP_POLL_S`, sending nothing more, and
    calls it upright the first time a reading says it stands. The wait ends at once on a robot
    somebody picks up or nothing reports on, rather than running on to judge legs a person is
    holding. Only when `STAND_UP_WAIT_S` has passed without either is the last reading judged: a
    robot still down may need a hand, and one still in its own answer to a fall is told to run
    the verb again in a moment. A single read three seconds after the request, as the
    Microduck's verb made before 0.21.0, told a robot that took four seconds to get up that it
    might need a hand."""
    state = await ctx.transport.get_state()
    if (held := _held(state)) is not None:
        return VerbResult.fail(f"cannot stand_up: {held}")
    if _blind(state):
        return VerbResult.fail(
            "cannot stand_up: nothing is reporting whether Huebot is down or in somebody's "
            "hands, so quackd will not ask it to drive"
        )
    if (unread := _unread(state)) is not None:
        return VerbResult.fail(f"cannot stand_up: {unread}, so quackd will not ask it to drive")
    if state.posture == "standing":
        return VerbResult.success("already upright")
    if state.posture == "sitting":
        return VerbResult.fail("Huebot is upright and seated: run stand")
    if (walk := _walk_held(state)) is not None:
        return VerbResult.fail(f"cannot stand_up: {walk}; wait for it to end")
    if state.extras.get("pickup_detection") is False:
        # `jsonrpc` alone says this, from a fresh frame that has no `safety.picked_up`
        return VerbResult.fail(
            "cannot stand_up: this robot does not report whether somebody is holding it (no "
            "safety.picked_up), so quackd will not ask it to drive legs that may be in "
            "somebody's hands. Stand it up by hand"
        )
    if (fail := await send_or_fail(ctx, Intent.enable(True))) is not None:
        return fail
    asked = ctx.transport.now()
    while True:
        await ctx.transport.sleep(STAND_UP_POLL_S)
        state = await ctx.transport.get_state()
        waited = ctx.transport.now() - asked
        if _blind(state):
            # Reporting "upright" here is how a face-down robot could pass: nothing was
            # reporting falls, so `fallen` was False, so this would have said it worked.
            return VerbResult.fail(
                "asked Huebot to drive, but nothing reports whether it is upright"
            )
        if (held := _held(state)) is not None:
            return VerbResult.fail(f"asked Huebot to drive, but {held}")
        if (unread := _unread(state)) is not None:
            return VerbResult.fail(f"asked Huebot to drive, but {unread}")
        if _upright(state):
            return VerbResult.success(f"upright after {waited:.1f} s", waited_s=round(waited, 2))
        if waited >= STAND_UP_WAIT_S:
            break
    if (fall := _answering_a_fall(state)) is not None:
        # Read before `fallen`, which a robot being posed on the floor reads too.
        return VerbResult.fail(
            f"asked Huebot to drive, but it is still answering a fall itself ({fall}), "
            f"{waited:.0f} s on. Run stand_up again in a moment to see whether it is up"
        )
    if state.fallen:
        return VerbResult.fail(
            f"asked Huebot to drive, and it is still down {waited:.0f} s later: nobody has said "
            "it gets up from a fall by itself, so it may need a hand"
        )
    return VerbResult.fail(
        f"asked Huebot to drive, and {waited:.0f} s later posture is "
        f"{state.posture}{_why_unknown(state)}"
    )


def quack_tag_for(text: str | None) -> str:
    """The protocol has seven duck sounds and no TTS. Pick the one that fits the mood."""
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
    """Huebot's `say`: the same seven tones as `quack`, text required."""
    tag = quack_tag_for(p.text)
    if (fail := await send_or_fail(ctx, Intent.sound(tag, p.text))) is not None:
        return fail
    return VerbResult.success(f"said {p.text!r} as [{tag}]", tag=tag, text=p.text)


HUEBOT_VERBS: dict[str, Verb] = {
    v.name: v
    for v in (
        Verb("sit", "Sit down.", sit, NoParams, timeout_s=10),
        Verb("stand", "Stand up from sitting.", stand, NoParams, timeout_s=10),
        Verb(
            "kick",
            "Kick forward with one leg, at a ball close and roughly ahead. Nobody has measured "
            "how close on a Huebot: a Microduck's kick connects under 0.3 m.",
            kick,
            KickParams,
            timeout_s=10,
            done_condition="the kick finished; the robot reports nothing about the ball.",
        ),
        Verb(
            "stand_up",
            "Ask the robot to get up after a fall. It may not manage: the result says whether "
            "it is upright within 10 s, or that it is still answering the fall itself.",
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
            "say",
            "Say something: the text is mapped to one of the robot's seven duck tones.",
            say,
            SayParams,
            timeout_s=5,
            core=True,
        ),
    )
}
