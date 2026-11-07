"""The family's verbs: one per behaviour a robot of it ships, and a body takes the ones it has.

Each maps to an intent the transport understands (and, on hardware, to a VERIFIED upstream
method; see `quackd_microduck/upstream_api.py`). These are the 0.3 built-ins that are not core
verbs, moved here unchanged, plus the Microduck's `say`: upstream has seven duck sounds and
no TTS, so text is mapped to the tone that fits the mood.

Each function takes the `Body` it speaks for first, and `verbs_for` and `conditions_for` bind it,
so a line a pilot acts on names the robot it is about, and two robots of the family cannot drift
apart in what a verb does. The names below the functions are the Microduck's.
"""

from __future__ import annotations

import math
from functools import cache, partial
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from quackd.transport.base import DuckState, Intent
from quackd.verbs.core import SayParams, _ended_by, _waited_out, send_or_fail
from quackd.verbs.explore import ExploreSpec, explore_verb, find_verb
from quackd.verbs.registry import STOPPED, NoParams, Precondition, Verb, VerbContext, VerbResult
from quackd.verbs.watch import HeadSpec, watch_verb
from quackd_microduck import upstream_api as up
from quackd_microduck.body import KICK_SKILLS, MICRODUCK, SIT_TOGGLE, Body

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

MICRODUCK_EXPLORE = ExploreSpec(
    # a look turns the head as far as `watch` may turn it, and the body past that, and waits for
    # the head as `watch` does; how wide a look is and how far it reaches are the map's own
    # (`SpatialSource.view`)
    head_yaw_deg=MICRODUCK_HEAD.yaw_deg,
    head_settle_s=MICRODUCK_HEAD.settle_s,
    source="the head's reach and settle as watch holds them (MICRODUCK_HEAD), and the map's own "
    "view",
)
"""The Microduck's numbers for `explore` and `find`, beside what its map says of itself."""


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


def _held(body: Body, state: DuckState) -> str | None:
    """Why nothing may move a duck that is in somebody's hands, or None when it is not.

    Read before `fallen` everywhere. A duck held at an angle reads as fallen, and "fallen"
    points the pilot at `stand_up`, which asks the policy to drive legs a person is holding.
    Only a robot whose pick-up detector says so sets `picked_up` (`jsonrpc`); every other
    backend leaves it out, which reads as nobody holding the duck, as it always has.
    """
    if state.extras.get("picked_up") is True:
        noun = body.words.noun
        return f"somebody is holding {noun}, and nothing that moves it is sent until it is down"
    return None


def _unread(state: DuckState) -> str | None:
    """Why the robot's own safety flags cannot be read just now, or None when they can. Only
    `jsonrpc` says, in `safety_unread`: a flag its peer sent that is neither a JSON boolean nor
    null, which also makes posture `unknown`, so every verb that walks refuses on it."""
    unread = state.extras.get("safety_unread")
    return str(unread) if unread else None


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


def _answering_a_fall(body: Body, state: DuckState) -> str | None:
    """What the robot's own answer to a fall is doing with it, or None when it has none.

    A robotd whose `limp_fall` is on, as it is by default again from daemon-v0.16.2, takes a
    falling duck off its policy, lets it land limp, ramps it to its standing pose and only then
    hands it back (`up.LIMP_FALL`), and it labels every tick of that with one of two labels. The
    label is read whatever the posture says, since a duck that has landed reads `fallen` before
    any label is looked at. safety.limp is true on the first half's ticks and on no others, so
    the label covers it too. The simulators and the mock never report either label."""
    if state.policy not in up.LIMP_FALL_LABELS:
        return None
    return f"policy {state.policy!r}: {body.not_standing[state.policy]}"


def _not_fallen(body: Body, state: DuckState) -> str | None:
    noun = body.words.noun
    if _blind(state):
        return f"nothing is reporting falls, so quackd cannot tell whether {noun} is upright"
    if (held := _held(body, state)) is not None:
        return held
    if (unread := _unread(state)) is not None:
        # The flag nobody can read may be the one saying somebody holds it, which `_held` reads
        # only from a boolean. `gaze` and `watch` stand on this, and turned the head on one.
        return f"{unread}, so quackd cannot tell whether somebody is holding {noun}"
    return f"{noun} has fallen; run stand_up first" if state.fallen else None


def _standing(body: Body, state: DuckState) -> str | None:
    """Standing, and known to be: `unknown` refuses, where it used to pass.

    The simulators and the mock always know their posture. On a real robot `unknown` is
    everything quackd cannot vouch for, a duck mid-skill, rising off its seat, limp, or
    reporting a policy label quackd has never read, and the transport says which in
    `posture_reason`, so the refusal can too.
    """
    noun = body.words.noun
    if _blind(state):
        return f"nothing is reporting posture, so quackd cannot tell whether {noun} is standing"
    if (held := _held(body, state)) is not None:
        return held
    if state.fallen or state.posture == "fallen":
        return f"{noun} has fallen; run stand_up first"
    if state.posture == "sitting":
        return f"{noun} is sitting; run stand first"
    if state.posture == "unknown" and (walk := _walk_held(state)) is not None:
        return f"{walk}; wait for it to end"
    if state.posture == "unknown":
        return f"posture is unknown{_why_unknown(state)}, so quackd cannot tell it is standing"
    return None


def conditions_for(body: Body) -> dict[str, Precondition]:
    """The two preconditions a manifest of this family names, in that body's words."""
    return {"not_fallen": partial(_not_fallen, body), "standing": partial(_standing, body)}


def microduck_conditions() -> dict[str, Precondition]:
    return conditions_for(MICRODUCK)


# ── the verbs ───────────────────────────────────────────────────────────────────────────


async def _sit_toggle(
    body: Body, ctx: VerbContext, want: Literal["sitting", "standing"]
) -> VerbResult:
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
            f"{_why_unknown(state)}, and {body.words.sit_unknown}"
        )
    if (fail := await send_or_fail(ctx, Intent.do(SIT_TOGGLE))) is not None:
        return fail
    if (why := await _waited_out(ctx, 2.0)) is not None:
        stopped = f"the robot plays out the toggle it was sent, on its way to {want}"
        return await _ended_by(ctx, ("sit", "stand")[want == "standing"], why, stopped)
    state = await ctx.transport.get_state()
    if state.posture == want:
        return VerbResult.success(f"now {want}")
    return VerbResult.fail(
        f"asked to be {want} but posture is {state.posture}{_why_unknown(state)}"
    )


async def _sit(body: Body, ctx: VerbContext, _: NoParams) -> VerbResult:
    return await _sit_toggle(body, ctx, "sitting")


async def _stand(body: Body, ctx: VerbContext, _: NoParams) -> VerbResult:
    return await _sit_toggle(body, ctx, "standing")


async def _kick(body: Body, ctx: VerbContext, p: KickParams) -> VerbResult:
    ack = await ctx.transport.send_intent(Intent.do(KICK_SKILLS[p.leg]))
    if not ack.accepted:
        return VerbResult.fail(f"kick missed or refused: {ack.reason or 'no reason given'}")
    if (why := await _waited_out(ctx, 1.5)) is not None:
        stopped = f"the robot plays out the kick it was sent with its {p.leg} leg"
        unread: dict[str, Any] = {"ball_moved_m": None} if body.reads_the_ball else {}
        return await _ended_by(ctx, "kick", why, stopped, **unread)
    if not body.reads_the_ball:
        return VerbResult.success(f"kicked with {p.leg} leg")
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
    if (why := await _waited_out(ctx, 3.0)) is not None:
        stopped = "the robot plays out the scoop it was sent, and nothing has read the beak"
        return await _ended_by(ctx, "grab", why, stopped)
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


def _upright(body: Body, state: DuckState) -> bool:
    """Whether a reading says the robot stands, and can be believed: something reports falls,
    nobody holds it, it has not fallen, and posture reads `standing`, which on `jsonrpc` is a
    fresh frame whose label is `walk` or `stand` and whose walk nothing holds back."""
    return (
        not _blind(state)
        and _held(body, state) is None
        and not state.fallen
        and state.posture == "standing"
    )


async def _stand_up(body: Body, ctx: VerbContext, _: NoParams) -> VerbResult:
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
    called upright the moment a reading says it stands (`_upright`), and the wait ends at once on
    a duck somebody picks up or nothing reports on. It used to be read once, three seconds after
    the request, and a duck still down then was told it may need a hand even while the policy was
    getting it up. The wait is timed by the transport's clock, since a slow link stretches a read
    and a count of polls would then run past what the verb said it waited. Only once
    `STAND_UP_WAIT_S` has passed is a duck that never stood judged: one that the robot's own answer
    to a fall still has (`_answering_a_fall`) is told so, and to run the verb again in a moment,
    since robotd hands the duck back to its policy once it has posed it, and one still down is told
    it may need a hand. A safety flag that cannot be read ends it as well, before the request and
    after, since nothing then says whether legs that may be in somebody's hands are being driven.
    """
    words = body.words
    state = await ctx.transport.get_state()
    if (held := _held(body, state)) is not None:
        return VerbResult.fail(f"cannot stand_up: {held}")
    if _blind(state):
        return VerbResult.fail(
            f"cannot stand_up: nothing is reporting whether {words.noun} is down or in somebody's "
            f"hands, so quackd will not {words.ask}"
        )
    if (unread := _unread(state)) is not None:
        return VerbResult.fail(f"cannot stand_up: {unread}, so quackd will not {words.ask}")
    if state.posture == "standing":
        return VerbResult.success("already upright")
    if state.posture == "sitting":
        return VerbResult.fail(f"{words.noun} is upright and seated: run stand")
    if (walk := _walk_held(state)) is not None:
        return VerbResult.fail(f"cannot stand_up: {walk}; wait for it to end")
    if state.extras.get("pickup_detection") is False:
        # `jsonrpc` alone says this, from a fresh frame that has no `safety.picked_up`
        return VerbResult.fail(words.no_pickup)
    if (fail := await send_or_fail(ctx, Intent.enable(True))) is not None:
        return fail
    asked = ctx.transport.now()
    while True:
        await ctx.transport.sleep(STAND_UP_POLL_S)
        if ctx.interrupted() == STOPPED:
            what = "this stopped waiting for it to get up"
            return await _ended_by(ctx, "stand_up", STOPPED, what)
        state = await ctx.transport.get_state()
        waited = ctx.transport.now() - asked
        if _blind(state):
            # Reporting "upright" here is how a face-down duck used to pass: nothing was
            # reporting falls, so `fallen` was False, so this said it worked.
            return VerbResult.fail(f"{words.asked}, but nothing reports whether it is upright")
        if (held := _held(body, state)) is not None:
            return VerbResult.fail(f"{words.asked}, but {held}")
        if (unread := _unread(state)) is not None:
            return VerbResult.fail(f"{words.asked}, but {unread}")
        if _upright(body, state):
            return VerbResult.success(f"upright after {waited:.1f} s", waited_s=round(waited, 2))
        if waited >= STAND_UP_WAIT_S:
            break
    if (fall := _answering_a_fall(body, state)) is not None:
        # Read before `fallen`, which a duck robotd is posing on the floor reads too. robotd has
        # not handed it back to the policy yet, so it is too soon to say whether it gets up.
        return VerbResult.fail(words.fall.format(fall=fall, waited=f"{waited:.0f}"))
    if state.fallen:
        return VerbResult.fail(words.down.format(waited=f"{waited:.0f}"))
    # Never standing by now, or the wait above would have said so.
    return VerbResult.fail(
        f"{words.asked}, and {waited:.0f} s later posture is {state.posture}{_why_unknown(state)}"
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


_WATCH = watch_verb(MICRODUCK_HEAD)
_EXPLORE = explore_verb(MICRODUCK_EXPLORE)
_FIND = find_verb(MICRODUCK_EXPLORE)


@cache
def verbs_for(body: Body) -> dict[str, Verb]:
    """The family's verbs this body has, in the family's order, which is the order its manifest
    declares them in, each speaking for it. One dict per body, shared: copy it to change it."""
    words = body.words
    family = (
        Verb("sit", "Sit down.", partial(_sit, body), NoParams, timeout_s=10),
        Verb("stand", "Stand up from sitting.", partial(_stand, body), NoParams, timeout_s=10),
        Verb(
            "kick",
            words.kick_text,
            partial(_kick, body),
            KickParams,
            timeout_s=10,
            done_condition=words.kick_done,
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
            words.stand_up_text,
            partial(_stand_up, body),
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
        _WATCH,
        # Declared only where the duck's reading keeps a map and knows the body's size, and find
        # only on a duck with a camera besides (`microduck_manifest`).
        _EXPLORE,
        _FIND,
    )
    return {verb.name: verb for verb in family if verb.name in body.verbs}


MICRODUCK_VERBS: dict[str, Verb] = verbs_for(MICRODUCK)

sit = partial(_sit, MICRODUCK)
stand = partial(_stand, MICRODUCK)
kick = partial(_kick, MICRODUCK)
stand_up = partial(_stand_up, MICRODUCK)
