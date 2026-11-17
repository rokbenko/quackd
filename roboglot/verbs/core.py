"""Core verbs: the vocabulary any robot can carry, and what each one requires.

`observe`, `report_state`, `stop`, `say`, `move`, `go_to`, `search_scan` and
`approach_and` exist on every robot whose manifest meets their requirement (a camera, a
`twist` intent, a `sound` intent). The composite ones are the steering loop, written as
ordinary Python: `go_to` closes the approach on detections at ~10 Hz so the LLM decides
*that* the robot should go to the ball and never *how*; `search_scan` turns in place on a
robot that can move and sweeps the head on one that can only look (ADR-0018).

The bodies of `move`, `go_to`, `search_scan` and `approach_and` are the 0.3 `walk`,
`walk_to`, `search_scan` and `approach_and`, renamed and otherwise untouched.
"""

from __future__ import annotations

import asyncio
import decimal
import math
import numbers
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from PIL import Image
from pydantic import BaseModel, ConfigDict, Field

from roboglot.perception.base import (
    NETWORK_INPUT_PX,
    Detection,
    detect_off_loop,
    nearest_measurable,
    summarize_detections,
    usable_m,
)
from roboglot.perception.yolo import GUESSED_FOV_DEG, HEIGHT_M, WIDEST_FRAME, nearest_box_m
from roboglot.transport.base import (
    Intent,
    camera_names_of,
    frame_period_of,
    frames_of,
    frames_taken_since,
    primary_of,
    twist_shown_since_of,
)
from roboglot.verbs.places import PLACE_VERBS
from roboglot.verbs.registry import STOP_ENDED, STOPPED, NoParams, Verb, VerbContext, VerbResult

if TYPE_CHECKING:
    from roboglot.adapters.manifest import RobotManifest

MOVE_RESEND_S = 0.1
#: How long a closed loop may hold its last twist while waiting for the next frame. One
#: deadman window: past that the steering loop is stalled, and a robot that stops is right.
HOLD_TTL_S = 0.3
MAX_VX = 0.3
MAX_VY = 0.2
MAX_WZ = 1.5
TICK_S = 0.1
TURN_RATE = 1.0  # rad/s used for scanning
DEFAULT_GAZE_LIMIT_DEG = 60.0
#: How hard `go_to` turns toward a target it sees: radians a second of turn for every degree the
#: target is off the camera's axis, held until the next frame is measured, and the fastest turn.
STEER_GAIN = 0.05
STEER_MAX_WZ = 1.0
#: How long `go_to`'s turn takes to cover a whole bearing it is held for: STEER_GAIN radians a
#: second for every degree, so a degree's radians over the gain, about a third of a second, for
#: every bearing the turn is not clamped on (`_measures_first`).
STEER_TURN_S = math.radians(1.0) / STEER_GAIN
#: How fast `go_to` turns toward the side a target it has lost was last seen on, and how many
#: ticks of that turn it gives the target to come back into view before it gives up.
LOST_WZ = 0.8
LOST_TICKS = 30
#: How near its stop distance `go_to` slows to a creep, and the speed it creeps at.
CREEP_BAND_M = 0.15
CREEP_VX = 0.1
#: How fast `go_to` walks at a target near the camera's axis, and slows to past the bearing it
#: turns on the spot at.
WALK_VX = 0.2
TURN_VX = 0.05
WALK_BEARING_DEG = 25.0
#: How finely the detectors Roboglot ships give a bearing (`round(..., 1)`) and a distance
#: (`round(..., 3)`): two readings of a target that has not moved can differ by that much.
BEARING_STEP_DEG = 0.1
DISTANCE_STEP_M = 0.001


def _cm_up(metres: float) -> float:
    """`metres` rounded up to the centimetre, the float product's last bits rounded off first,
    so that 1.775 comes to 1.78 and 0.12 stays 0.12."""
    return math.ceil(round(metres * 100, 6)) / 100


def _askable(nearest_m: float) -> float:
    """The stop distance a refusal names for a target the camera measures no nearer than
    `nearest_m`: that, one creeping step farther, rounded up to the centimetre. A frame comes
    once a tick, and the body creeps `CREEP_VX * TICK_S` between two, so a stop distance within a
    step of the nearest it measures can fall between two frames, the one before it reading
    farther and the one after it cut by the frame's edge, and the walk ends as near as the camera
    can tell where it was asked to arrive by measurement."""
    return _cm_up(nearest_m + CREEP_VX * TICK_S)


#: The farthest `stop_distance` `go_to` takes: the one a refusal names for the tallest label a box
#: detector knows (`HEIGHT_M`, a person, 1.6 m) on the widest frame Roboglot expects
#: (`WIDEST_FRAME`, 16:9, taken at the network's own size; the edge band is a share of the frame's
#: height there, as on any 16:9 frame) through the lens it guesses when nobody named one
#: (`GUESSED_FOV_DEG`): 2.466 m, a creeping step more, rounded up (`_askable`). `go_to` refuses a
#: stop distance nearer than its camera can measure the target and names one it can, and this is
#: as far as the bound has to reach for that distance to be one a pilot can ask for on such a
#: camera. It was 1.5 m, under the 1.775 m a person reads at the nearest on a 4:3 frame through
#: that lens. A larger stop distance is the safe side.
MAX_STOP_DISTANCE_M = _askable(
    nearest_box_m(
        max(HEIGHT_M, key=HEIGHT_M.__getitem__),
        (NETWORK_INPUT_PX, NETWORK_INPUT_PX / WIDEST_FRAME),
        GUESSED_FOV_DEG,
    )
    or 0.0
)

# ── parameters ──────────────────────────────────────────────────────────────────────────


class MoveParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    vx: float = Field(
        default=0.15, ge=-MAX_VX, le=MAX_VX, description="Forward m/s (negative = back)."
    )
    vy: float = Field(default=0.0, ge=-0.2, le=0.2, description="Left m/s (negative = right).")
    wz: float = Field(default=0.0, ge=-MAX_WZ, le=MAX_WZ, description="Turn rate rad/s (+ = left).")
    duration_s: float = Field(
        default=1.0, gt=0, le=10, description="How long to hold this velocity."
    )


class SayParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(..., min_length=1, max_length=200, description="What to say.")


#: The most steps one `search_scan` takes, the bound its `max_steps` is held to.
MAX_SCAN_STEPS = 16


class SearchScanParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target: str = Field(default="ball", description="Label to look for (e.g. ball, person).")
    step_deg: float = Field(
        default=45.0,
        ge=15,
        le=120,
        description="Rotation per step. Left out, 45, or less through a lens no wider than that, "
        "so that each view overlaps the next.",
    )
    max_steps: int = Field(
        default=8,
        ge=1,
        le=MAX_SCAN_STEPS,
        description="Steps before giving up. Left out, as many as make a full turn, 8 at 45°.",
    )


class GoToParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target: str = Field(default="ball", description="Label to approach.")
    stop_distance: float = Field(
        default=0.25,
        ge=0.1,
        le=MAX_STOP_DISTANCE_M,
        description="Stop this far away (m). One nearer than the camera can measure the target "
        "is refused, naming the nearest it can.",
    )
    timeout_s: float = Field(default=20.0, gt=0, le=60)


class ApproachAndParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target: str = Field(default="ball")
    stop_distance: float = Field(default=0.25, ge=0.1, le=MAX_STOP_DISTANCE_M)
    then: str = Field(..., description="Verb to run once close (e.g. kick, grab).")


# ── helpers shared with adapter verbs ───────────────────────────────────────────────────


@dataclass(frozen=True)
class _Unlanded:
    """A stop that the transport does not say reached the body: why, in the transport's words
    (`stop_error`), and whether it went out at all. A transport that sent it and cannot tell
    whether it arrived says so (`stop_unconfirmed`), and is not said to have failed to send it."""

    why: str
    sent: bool

    def said(self, stop: str) -> str:
        """What became of `stop`, in no more words than the transport gave."""
        if self.sent:
            return f"{stop} went out but is not known to have reached the body"
        return f"{stop} could not be delivered"


async def _stop(ctx: VerbContext) -> _Unlanded | None:
    """Stop the body, and return what the transport says of a stop that did not reach it, or None
    when nothing says so.

    Every stop a verb here sends goes through this. A transport's stop does not raise, since it
    runs on the way out of every failure: one that knows it did not land says why in
    `stop_error`, and only the `stop` verb read it. So a link that went in the last slice of a
    walk took every twist and not the stop, and `move` said it walked, `go_to` that it arrived,
    and `search_scan` turned on as if its turn had ended, over a body still moving. A transport
    that cannot tell says nothing, and then nothing here changes (`_unstopped`)."""
    await ctx.transport.stop()
    undelivered = getattr(ctx.transport, "stop_error", None)
    if not undelivered:
        return None
    return _Unlanded(str(undelivered), getattr(ctx.transport, "stop_unconfirmed", False) is True)


def _unstopped(verb: str, missed: _Unlanded | None, result: VerbResult) -> VerbResult:
    """`result`, when the stop `verb` ended on reached the body (`missed` is None, `_stop`), or
    else a failure that says what `result` said, then what became of the stop as the transport
    put it (`_Unlanded.said`) and that the body may still be moving, and why, with the reason as
    `stop_error` in its data. A `verb` of "" is a stop named by no verb. A result that has said
    so already is not said twice, nor a reason it has given already, as a twist refused for the
    same reason the stop did not go gives it. It is no longer marked `ended_by`: a brake that
    did not reach the body is not the brake working, and `abort_when` counts it."""
    if missed is None or "stop_error" in result.data:
        return result
    why = missed.why
    said = result.summary[:-1] if result.summary.endswith(".") else result.summary
    unstopped = missed.said(f"{verb}'s stop" if verb else "the stop")
    if why in said:
        told = f"{unstopped} either, for the same reason, so the body may still be moving"
    else:
        # what is known, then the body's own words, which say what to do about it where the
        # body has something true to say (`stop`): the core advises nothing physical
        told = f"{unstopped}, so the body may still be moving: {why}"
    data = {key: value for key, value in result.data.items() if key != "ended_by"}
    if missed.sent:
        data["stop_unconfirmed"] = True
    return VerbResult.fail(f"{said}. But {told}", **{**data, "stop_error": why})


class _Unstopped(Exception):
    """A stop sent partway through a verb, the end of one of `search_scan`'s turns or a walk's
    stop to look, that did not reach the body (`_stop`), carried with what was said of it out of
    the helper that sent it to the verb that ends on it (`_unstopped`), as `_Refused` carries a
    twist's."""

    def __init__(self, missed: _Unlanded) -> None:
        super().__init__(missed.why)
        self.missed = missed


async def send_or_fail(
    ctx: VerbContext, intent: Intent, verb: str | None = None
) -> VerbResult | None:
    """Send an intent; return a failure result if the robot refused it, or if the verb has been
    told to end (`VerbContext.interrupted`), when nothing goes out. A stop that came while a verb
    read the body's state was followed by the goal that verb went on to send. What only speaks
    goes out all the same, as it runs beside a stop (`Executor._overtaken`).

    `verb` is the verb that sends it, which a stop that did not land is said of; a caller that
    names none has its intent lead the sentence, as it always did, and the stop is the verb's
    unnamed (`_unstopped`), not the intent's: `joint's stop` named no verb a pilot called."""
    if intent.kind != "sound" and (why := ctx.interrupted()) is not None:
        return await _ended_by(ctx, intent.kind, why, "nothing more was sent", stop_of=verb or "")
    ack = await ctx.transport.send_intent(intent)
    if not ack.accepted:
        return VerbResult.fail(f"{intent.kind} refused: {ack.reason or 'no reason given'}")
    return None


class _Refused(Exception):
    """A twist the body refused, carried with its reason out of the helper that sent it, deep
    in a steering loop, to the verb that stops on it (`_stopped_by`)."""


class _Interrupted(Exception):
    """A verb told to end from beside it (`VerbContext.interrupted`), carried with the reason
    out of the helper that would have sent the next twist, deep in a steering loop, to the verb
    that ends on it (`_ended_by`)."""

    def __init__(self, why: str) -> None:
        super().__init__(why)
        self.why = why


class _LookRefused(Exception):
    """The look that centres the head, refused by a body with a head, carried with the body's
    reason out of `_look_ahead` to the verb that ends on it (`_unlooked`)."""


async def _unlooked(ctx: VerbContext, verb: str, refused: _LookRefused) -> VerbResult:
    """Stop and fail a verb whose centring look the body refused, before it moved anything."""
    unstopped = await _stop(ctx)
    return _unstopped(
        verb,
        unstopped,
        VerbResult.fail(
            f"{verb}: the look that centres the head was refused, so nothing moved: {refused}",
            look_refused=str(refused),
        ),
    )


async def _twist(ctx: VerbContext, vx: float, vy: float, wz: float) -> None:
    """Send one twist, and raise `_Refused` with the body's own reason when it refuses it.

    `move` has always ended at a refused twist, through `send_or_fail`. `go_to`, `search_scan`
    and the helpers they steer through threw the answer away, so a body that would not take a
    twist was steered on regardless: `go_to` ran out its whole timeout and said only that it
    had timed out, and `search_scan` reported a full sweep of a robot that never turned, which a
    pilot reads as the target not being in the room. A Microduck's transport refuses a twist
    once the duck has stopped standing or robotd has stopped showing the twists it was sent, and
    every body refuses one it cannot deliver, a dropped link above all, so each of those reached
    the pilot as a timeout or a target not found.

    Nor is a twist sent once the verb has been told to end (`VerbContext.interrupted`): a `stop`
    from beside it, or the run's minutes spent. `_Interrupted` says why instead. A stop asked for
    over MCP while `go_to` steered used to send its stop, and the next tick's twist walked the
    body on, so the brake held for a tenth of a second."""
    if (why := ctx.interrupted()) is not None:
        raise _Interrupted(why)
    ack = await ctx.transport.send_intent(Intent.move(vx, vy, wz))
    if not ack.accepted:
        raise _Refused(ack.reason or "no reason given")


async def _stopped_by(
    ctx: VerbContext, verb: str, stopped: str, refused: _Refused, **data: Any
) -> VerbResult:
    """Stop the body after a twist it refused, and fail saying what was refused and why, the
    way `detector_failed` says a frame the detector could not read stopped it."""
    unstopped = await _stop(ctx)
    return _unstopped(
        verb,
        unstopped,
        VerbResult.fail(
            f"{verb}: a twist was refused, so {stopped}: {refused}",
            move_refused=str(refused),
            **data,
        ),
    )


async def _ended_by(
    ctx: VerbContext,
    verb: str,
    why: str,
    stopped: str,
    *,
    stop_of: str | None = None,
    **data: Any,
) -> VerbResult:
    """Stop the body for a verb told to end from beside it, and fail saying why and how far it
    got, as `watch` does. `stop_of` is the verb a stop that did not land is said of, when it is
    not `verb` (`send_or_fail`, whose `verb` is its intent).

    The `stop` that ended it has stopped the body already, and this stops it again, after the
    last twist the verb sent rather than before it. A `stop` is marked `ended_by` (`STOP_ENDED`),
    so `abort_when`'s count of failures in a row passes over it: a person braking a walk three
    times would otherwise end the run on the same verb failing three times in a row."""
    unstopped = await _stop(ctx)
    if why == STOPPED:
        data["ended_by"] = STOP_ENDED
    return _unstopped(
        verb if stop_of is None else stop_of,
        unstopped,
        VerbResult.fail(f"{verb}: {why}, so {stopped}", interrupted=why, **data),
    )


async def _waited_out(ctx: VerbContext, seconds: float) -> str | None:
    """`seconds` on the body's clock for a verb that has sent what it sends and is waiting on it, or
    why it was told to end before they were up (`VerbContext.interrupted`), looked at every tick.
    A wait that heard nothing reported the move it was waiting on as done, with a stop sent beside
    it, and a verb with another goal to send after the wait sent it, which undid the stop."""
    slices = max(1, round(seconds / TICK_S))
    for _ in range(slices):
        if (why := ctx.interrupted()) is not None:
            return why
        await ctx.transport.sleep(seconds / slices)
    return ctx.interrupted()


def _head_fade_s(ctx: VerbContext) -> float:
    """How long the body's head takes to let go of a glance of its own once a look arrives
    (`head_fade_s`), or 0 on a body whose head moves only when it is asked to. A fade that is not
    finite is no time a verb can wait out, and is not waited for: the sleep had no end before
    the executor's timeout, and on a mock it sent the clock to infinity."""
    fade = getattr(ctx.transport, "head_fade_s", 0.0)
    if isinstance(fade, int | float) and not isinstance(fade, bool) and 0 < fade < math.inf:
        return float(fade)
    return 0.0


async def _look_ahead(ctx: VerbContext) -> float:
    """Centre the head before a verb that steers on what the camera sees, give it the time to
    get there, and return the moment after which a frame shows it there: the first frame the
    verb acts on has to have been taken no earlier (`_see`).

    A body whose robot turns the head on its own while it stands still says, as `head_fade_s`,
    how long that takes to wear off once a look arrives. A Microduck's robotd has glanced around
    since daemon-v0.16.1, and lets go of a glance by fading it out over a second and a half
    rather than at once, so the first frame `go_to` and `search_scan` acted on, taken straight
    after the look, could come from a head still most of a glance off centre: a bearing off by
    that much, and `go_to` turning toward it at full rate. The wait comes first, with the body
    still: nothing is sent that moves it. A body without the attribute waits for nothing, as
    before.

    The wait alone did not make the frame after it a frame taken after it. A body may hand out
    a frame it took a while before, as a Microduck on mediad's `/frame` does: the newest frame
    it holds was asked for as much as a frame period and a fetch earlier, so the first frame
    acted on could still be from mid-glance, or from before the look, with the head where a
    `gaze` had left it. The moment returned is when the wait ended, and on a body that says when
    its frames were taken the first frame acted on is one taken after it. A body that does not
    say hands out the newest frame, as before, and on a mock that is drawn when it is asked
    for.

    A body with a head to look with (`gaze` among its manifest's intents) that refuses the look
    ends the verb with `_LookRefused`, before anything moves: its answer was thrown away, and the
    verb steered on bearings taken through a head pointing wherever it was left. A body with no
    head refuses a look as a matter of course, a wheeled base over rosbridge saying it cannot
    look, and goes on as before, as does a bare transport, which names no intents.
    """
    ack = await ctx.transport.send_intent(Intent.look(1.0, 0.0, 0.0))
    if not ack.accepted and ctx.manifest is not None and "gaze" in ctx.manifest.intents:
        raise _LookRefused(ack.reason or "no reason given")
    fade = _head_fade_s(ctx)
    if fade > 0:
        await ctx.transport.sleep(fade)
    return ctx.transport.now()


async def _see(
    ctx: VerbContext, label: str, caption: str, since: float | None = None
) -> tuple[Image.Image | None, list[Detection]]:
    """The newest frame and what the detector finds of `label` in it, on a body that says when
    its frames were taken one taken no earlier than `since` on the body's own clock, waited for
    a tick at a time (`frames_taken_since`)."""
    if since is not None:
        await frames_taken_since(ctx.transport, since, TICK_S)
    img = await ctx.transport.get_frame()
    if img is None:
        return None, []
    ctx.on_frame(img, caption)
    # off the loop when the detector waits on a board, so `_see_holding` keeps re-sending the
    # twist while it does: a detector that blocked here would starve the very resend that
    # exists to outlast a slow frame
    dets = await detect_off_loop(ctx.detector, img) if ctx.detector else []
    return img, [d for d in dets if d.label == label]


def speed_limits(manifest: RobotManifest | None) -> tuple[float, float, float]:
    """(max_vx, max_vy, max_wz): the manifest's `limits` when it names them, else the
    schema bounds, which are the Microduck's, so its runs stay byte-identical.

    A limit that is not a finite number at or above nought says nothing a clamp can hold to, and
    reads as one the manifest did not name: the schema's bound. The manifest refuses such a
    limit when it is validated (`RobotManifest._magnitudes`), and this holds for one that got
    past it, set on a manifest afterwards: a negative `max_vx` turned a request to stand still
    into the full speed forward, a negative `max_wz` turned the body the other way, and NaN went
    out as the twist."""
    limits = manifest.limits if manifest is not None else {}
    return (
        _magnitude(limits.get("max_vx"), MAX_VX),
        _magnitude(limits.get("max_vy"), MAX_VY),
        _magnitude(limits.get("max_wz"), MAX_WZ),
    )


def _magnitude(limit: object, bound: float) -> float:
    """`limit` as a float when it is a finite number at or above nought, else `bound`
    (`speed_limits`). Any real number is read, a numpy number or a Decimal as much as a float,
    since a limit computed from an array is one: read as only an `int` or a `float`, such a limit
    counted as none, and a base that cannot strafe was allowed the schema's speed sideways."""
    if isinstance(limit, bool) or not isinstance(limit, numbers.Real | decimal.Decimal):
        return bound
    value = float(limit)
    return value if 0 <= value < math.inf else bound


def _clamped(ctx: VerbContext, vx: float, vy: float, wz: float) -> tuple[float, float, float]:
    max_vx, max_vy, max_wz = speed_limits(ctx.manifest)
    return (
        max(-max_vx, min(max_vx, vx)),
        max(-max_vy, min(max_vy, vy)),
        max(-max_wz, min(max_wz, wz)),
    )


async def _turn(ctx: VerbContext, radians: float) -> None:
    """Rotate in place by `radians`, feeding the deadman every tick. A twist the body refuses
    ends the turn where it is, with `_Refused` for the verb to stop on, and a stop at its end
    that did not reach the body ends it with `_Unstopped`."""
    rate = min(TURN_RATE, speed_limits(ctx.manifest)[2])
    wz = rate if radians >= 0 else -rate
    duration = abs(radians) / rate
    ticks = max(1, round(duration / TICK_S))
    for _ in range(ticks):
        await _twist(ctx, 0.0, 0.0, wz)
        await ctx.transport.sleep(duration / ticks)
    if (missed := await _stop(ctx)) is not None:
        raise _Unstopped(missed)


# ── the verbs ───────────────────────────────────────────────────────────────────────────


async def _no_frame(ctx: VerbContext, verb: str) -> VerbResult:
    """A frame that did not arrive is not the same as a robot with no camera, and neither
    is a reason to keep walking. The `img is None` branch used to return without stopping."""
    unstopped = await _stop(ctx)
    why = getattr(ctx.transport, "camera_error", None)
    return _unstopped(
        verb,
        unstopped,
        VerbResult.fail(f"{verb}: {why}" if why else f"{verb} needs a camera and got no frame"),
    )


async def detector_failed(ctx: VerbContext, verb: str, stopped: str) -> VerbResult | None:
    """Stop the body and fail when the detector could not read the frame just looked at, or
    None when it could.

    The board's detector turns a failed call into no detections and keeps the reason in
    `error`, which it sets or clears on every call, so read right after `_see` it is about that
    frame. No detections is what every steering loop here reads as a target out of view:
    `go_to` turned toward where it last saw the target on each such frame, and `search_scan`
    turned a full circle and said the target was not there, when nothing had been looked at.
    A board that hangs costs every frame its whole two second timeout, so `go_to`'s count of
    empty frames never ran out before its own timeout did. The body stops on the first such
    frame instead, and the verb says why. A detector with no `error` never fails here."""
    why = getattr(ctx.detector, "error", None)
    if not isinstance(why, str) or not why:
        return None
    unstopped = await _stop(ctx)
    name = getattr(ctx.detector, "name", "the detector")
    return _unstopped(
        verb,
        unstopped,
        VerbResult.fail(f"{verb}: {name} failed, so {stopped}: {why}", detector_error=why),
    )


async def _see_holding(
    ctx: VerbContext,
    label: str,
    caption: str,
    twist: tuple[float, float, float] | None,
    since: float | None = None,
) -> tuple[Image.Image | None, list[Detection]]:
    """Fetch a frame while keeping the twist the last frame justified alive, a frame taken no
    earlier than `since` on a body that says when its frames were taken (`_see`).

    `_see` is a network round trip to a camera server, and `go_to` used to await it and only
    then send its next command — so the gap between two commands was `TICK_S + fetch +
    decode + detect`. The daemon's deadman is 300 ms and its comment sizes it on "Roboglot
    re-sends at 10 Hz", which is true of `move` and `_turn` and false of the one verb that
    walks while looking. Over the ssh tunnel the checklist mandates, a single latency spike
    past ~200 ms zeroed the velocity mid-stride with nothing in the log to say why.

    A free-running pump inside the transport would be worse: it would keep the robot walking
    blind for the whole fetch timeout, turning the deadman's meaning from "the steering loop
    is alive" into "the socket is up". This holds only the last justified twist, and only
    for one deadman window — after that the steering loop really is stalled, and a robot that
    stops is the right outcome.

    So when the window closes with the frame still out, this sends one zero twist itself
    rather than leave the stop to the body's deadman. Not every body has one that would fire:
    a rosbridge base has none, and a ToddlerBot's is fed by the adapter's own keepalive with
    WALK latched. Both kept going on the last twist for as long as the frame took, which
    through a board is up to two seconds for the detection and one more for a snapshot from
    its camera, all of it driving on a decision made from an older frame. A body with a
    deadman loses nothing, because it was about to stop anyway.

    A re-send the body refuses ends the wait there, with `_Refused`, and the frame still out
    is let go of: the verb is over, and nothing it would have seen in it changes that."""
    task = asyncio.ensure_future(_see(ctx, label, caption, since))
    deadline = ctx.transport.now() + HOLD_TTL_S
    try:
        while True:
            done, _ = await asyncio.wait({task}, timeout=MOVE_RESEND_S)
            if done:
                return task.result()
            if twist is None:
                continue
            if ctx.transport.now() < deadline:
                await _twist(ctx, *twist)
            else:
                # a zero twist, not `stop()`: on a ToddlerBot `stop` leaves the walk policy for
                # a held pose, and this stop is one the next frame may undo a moment later
                await _twist(ctx, 0.0, 0.0, 0.0)
                twist = None
    finally:
        if not task.done():  # on cancellation, and on a twist the body refused
            task.cancel()


async def observe(ctx: VerbContext, _: NoParams) -> VerbResult:
    frames = await frames_of(ctx.transport)
    if not frames:
        # a backend that knows why it has no picture says so. A camera Roboglot was told to
        # open and then lost is a different problem from a body that never had one, and
        # only the first is worth going to look at (the LeRobot arm sets this today)
        why = getattr(ctx.transport, "camera_error", None)
        return VerbResult.fail(
            f"the camera gave no frame: {why}" if why else "this transport has no camera"
        )
    # the primary camera and only it: a detection's bearing is read off the lens --fov-deg
    # measured, so the same angle from a second view would point somewhere else. A body whose
    # primary lens gave nothing this time still shows its other views and reports nothing
    # seen, which is what a one-camera body does when its only camera fails.
    primary = primary_of(frames)
    ctx.on_frame(primary if primary is not None else frames[0].image, "observe")
    ctx.on_frames(frames, "observe")
    detections = (
        await detect_off_loop(ctx.detector, primary)
        if (ctx.detector and primary is not None)
        else []
    )
    dumped = [d.model_dump() for d in detections]
    seen = summarize_detections(detections)
    # A detector on another machine can fail, and a failed call is a frame with no detections,
    # which reads exactly like an empty room. The board's detector keeps its reason, and the
    # pilot is told it beside "nothing detected" rather than being left to search a room that
    # may have the target in plain view.
    failed = getattr(ctx.detector, "error", None)
    if primary is not None and isinstance(failed, str) and failed:
        seen += f" (host detector: {failed})"
    # the body's cameras, not this step's frames: a two-camera arm down to one lens still has
    # two views to tell apart, and the picture that did arrive is the one that most needs
    # saying which lens it came off
    if len(frames) == 1 and len(camera_names_of(ctx.transport)) < 2:
        return VerbResult.success(f"frame captured; {seen}", detections=dumped)
    # the camera names only where there are views to tell apart, so a one-camera body reads
    # back exactly the line and the payload it did before there were several
    names = [frame.name for frame in frames]
    return VerbResult.success(
        f"frames captured from {', '.join(names)}; {seen}", detections=dumped, cameras=names
    )


async def report_state(ctx: VerbContext, _: NoParams) -> VerbResult:
    state = await ctx.transport.get_state()
    return VerbResult.success(state.summary(), state=state.model_dump())


async def stop(ctx: VerbContext, _: NoParams) -> VerbResult:
    # `stop` is the verb the pilot is told to reach for when anything looks wrong, and it is
    # asked for most often when the link is the thing that is wrong. A transport that knows it
    # could not deliver says so; reporting success anyway would be the one reassuring line in
    # the log that was not true. Transports that cannot tell report nothing and are unchanged.
    # It says what is known and advises nothing: the hardware switch it used to send everyone
    # to is a fall on a body that stands and a drop on an arm whose torque goes with it, so what
    # to do about it is the body's own to say, in its `stop_error`, where it has something true
    # and it says no more than the transport did: a stop sent that is not known to have arrived
    # is not one that could not be delivered
    if (missed := await _stop(ctx)) is not None:
        return VerbResult.fail(
            f"{missed.said('stop')}, so the body may still be moving: {missed.why}"
        )
    # A body that left joints out of its hold says which (the arm, for a joint read past its
    # travel, where "stay here" would reach the servo as "go to the limit"). Without the clause
    # a stop that held every joint and one that held none read the same, to the pilot and in
    # the record. Read with getattr, so every body that holds all of itself is unchanged.
    skipped = [str(j) for j in getattr(ctx.transport, "stop_skipped", ()) or ()]
    if skipped:
        one = len(skipped) == 1
        named = skipped[0] if one else ", ".join(skipped[:-1]) + " and " + skipped[-1]
        return VerbResult.success(
            f"stopped (velocity zeroed); {named} {'reads' if one else 'read'} past "
            f"{'its' if one else 'their'} travel, so no goal was written for "
            f"{'it' if one else 'them'}",
            not_held=skipped,
        )
    # A body that could not hold what was still moving, for want of a recent reading of it (the
    # XLeRobot's and the AlohaMini's arms), and whose stop stopped the rest.
    going = [str(j) for j in getattr(ctx.transport, "stop_left_going", ()) or ()]
    if going:
        one = len(going) == 1
        named = going[0] if one else ", ".join(going[:-1]) + " and " + going[-1]
        return VerbResult.success(
            f"stopped (velocity zeroed); there is no recent reading to hold {named} from, so "
            f"{'it goes' if one else 'they go'} on to the goal sent",
            not_held=going,
        )
    return VerbResult.success("stopped (velocity zeroed)")


async def say(ctx: VerbContext, p: SayParams) -> VerbResult:
    """The generic voice: hand the text to the robot's sound channel. Robots without a voice
    override this (the Microduck picks one of its seven tones)."""
    if (fail := await send_or_fail(ctx, Intent.sound("say", p.text))) is not None:
        return fail
    return VerbResult.success(f"said {p.text!r}", text=p.text)


async def move(ctx: VerbContext, p: MoveParams) -> VerbResult:
    vx, vy, wz = _clamped(ctx, p.vx, p.vy, p.wz)  # the manifest's limits, if any
    slices = max(1, round(p.duration_s / MOVE_RESEND_S))
    step = p.duration_s / slices
    for k in range(slices):
        # read before each resend, so a `stop` from beside the walk is the last word the body
        # hears rather than the next slice's twist (`_twist`)
        if (why := ctx.interrupted()) is not None:
            walked = f"it stopped after {k * step:.1f} of {p.duration_s:.1f} s"
            return await _ended_by(ctx, "move", why, walked, duration_s=round(k * step, 3))
        if (fail := await send_or_fail(ctx, Intent.move(vx, vy, wz))) is not None:
            unstopped = await _stop(ctx)
            return _unstopped("move", unstopped, fail)
        await ctx.transport.sleep(step)
    unstopped = await _stop(ctx)
    clamped = (vx, vy, wz) != (p.vx, p.vy, p.wz)
    return _unstopped(
        "move",
        unstopped,
        VerbResult.success(
            f"walked vx={vx:.2f} vy={vy:.2f} wz={wz:.2f} for {p.duration_s:.1f}s"
            + (" (clamped to this robot's limits)" if clamped else ""),
            duration_s=p.duration_s,
        ),
    )


ScanMode = Literal["turn", "gaze", "none"]


def scan_mode(manifest: RobotManifest | None) -> ScanMode:
    """How this robot looks around: a bare transport and any mobile robot turn in place."""
    if manifest is None or (manifest.mobility != "none" and "twist" in manifest.intents):
        return "turn"
    if "gaze" in manifest.intents:
        return "gaze"
    return "none"


def _wrap_deg(deg: float) -> float:
    return (deg + 180.0) % 360.0 - 180.0


def gaze_sweep_yaws(
    centre_deg: float, step_deg: float, max_steps: int, limit_deg: float
) -> list[float]:
    """The head-sweep schedule: the current yaw, then outward alternation (+s, -s, +2s, ...),
    wrapped to (-180, 180], clipped to the head's range, duplicates dropped."""
    yaws: list[float] = []
    for k in range(max_steps + 1):
        n = (k + 1) // 2
        offset = 0.0 if k == 0 else (n * step_deg if k % 2 == 1 else -n * step_deg)
        yaw = _wrap_deg(centre_deg + offset)
        if abs(yaw) > limit_deg + 1e-9:
            continue
        if any(abs(_wrap_deg(yaw - seen)) < 1e-6 for seen in yaws):
            continue
        yaws.append(yaw)
    return yaws


def _lens_deg(ctx: VerbContext) -> float | None:
    """How wide the view the detector measures bearings through is, in degrees, when it says:
    the lens `--fov-deg` gave, the body's own, or the guess a detector falls back on."""
    fov = getattr(ctx.detector, "fov_deg", None)
    if isinstance(fov, int | float) and not isinstance(fov, bool) and 0 < fov < 360:
        return float(fov)
    return None


def views_round(width_deg: float) -> tuple[float, int]:
    """The step between the views of a sensor `width_deg` wide that turns a whole circle in whole
    steps, each view overlapping the next, and how many views make the circle: one more than fit
    into it edge to edge. `scan_steps` turns a camera by this rule."""
    per_turn = math.floor(360.0 / width_deg) + 1
    return 360.0 / per_turn, per_turn


def scan_steps(p: SearchScanParams, lens_deg: float | None) -> tuple[float, int]:
    """The step `search_scan` turns by and how many it takes, for a lens `lens_deg` wide.

    The step a caller leaves out was 45°, which through a lens narrower than that leaves a wedge
    between each view and the next that no view sees: on an alpha's upright `/frame`, 37° wide,
    a sixth of every turn, and a target standing in one of them was not there as far as the
    scan could say. With the lens known, a step left out is the widest that turns a whole circle
    in whole steps with each view overlapping the next, a circle over one more step than the lens
    fits into it, and never wider than 45°. So a lens wider than 45° scans as it always did, and
    so does a body whose detector names no lens, while one exactly 45° wide, whose views 45°
    apart would only meet edge to edge, turns 40° at a time. A count of steps left out is then
    as many as make the circle, as 8 does at 45°, up to `MAX_SCAN_STEPS`. A step or a count the
    caller gave is kept as given."""
    if lens_deg is None or "step_deg" in p.model_fields_set:
        return p.step_deg, p.max_steps
    step, per_turn = views_round(lens_deg)
    if step >= p.step_deg:
        return p.step_deg, p.max_steps
    steps = p.max_steps if "max_steps" in p.model_fields_set else min(MAX_SCAN_STEPS, per_turn)
    return step, steps


async def _gaze_sweep(
    ctx: VerbContext, p: SearchScanParams, limit_deg: float, step: float, steps: int
) -> VerbResult:
    state = await ctx.transport.get_state()
    centre = float(state.extras.get("head_yaw_deg") or 0.0)
    yaws = gaze_sweep_yaws(centre, step, steps, limit_deg)
    for i, yaw in enumerate(yaws):
        if (why := ctx.interrupted()) is not None:
            swept = f"the sweep stopped after {i} of {len(yaws)} looks"
            return await _ended_by(ctx, "search_scan", why, swept, steps=i)
        rad = math.radians(yaw)
        ack = await ctx.transport.send_intent(Intent.look(math.cos(rad), math.sin(rad), 0.0))
        if not ack.accepted:
            # the answer was thrown away, so a body that refused every look was reported as
            # having looked across the whole sweep and not found the target there
            unstopped = await _stop(ctx)
            why = ack.reason or "no reason given"
            return _unstopped(
                "search_scan",
                unstopped,
                VerbResult.fail(
                    f"search_scan: a look was refused, so the sweep stopped after {i} of "
                    f"{len(yaws)} looks: {why}",
                    look_refused=why,
                    steps=i,
                ),
            )
        await ctx.transport.sleep(TICK_S)  # let the head settle; a mock's clock advances
        img, hits = await _see(ctx, p.target, f"search_scan gaze {yaw:+.0f}")
        if img is None:
            return await _no_frame(ctx, "search_scan")
        if (failed := await detector_failed(ctx, "search_scan", "the sweep stopped")) is not None:
            return failed
        if hits:
            best = hits[0]
            return VerbResult.success(
                f"{p.target} found: {best.summary()} (gaze {yaw:+.0f}°)",
                detections=[d.model_dump() for d in hits],
                steps=i,
                gaze_yaw_deg=yaw,
            )
    span = (len(yaws) - 1) * step
    return VerbResult.fail(
        f"{p.target} not found in a gaze sweep of {len(yaws)} looks ({span:.0f}°)"
    )


def _turns_taken(ctx: VerbContext, starts: list[float]) -> int | None:
    """How many of the turns `search_scan` began at `starts`, on the body's clock, the body's own
    state showed it took, or None on a body that does not say (`twist_shown_since_of`).

    The turns are counted in order and the first not shown ends the count, since a state that
    shows a later turn's twist shows that every one before it was taken too."""
    taken = 0
    for start in starts:
        shown = twist_shown_since_of(ctx.transport, start)
        if shown is None:
            return None
        if not shown:
            break
        taken += 1
    return taken


def _none_confirmed(sent: int) -> str:
    """Says that a body's own state showed none of the `sent` turns a scan sent it. One turn is
    named, not counted: the count read "none of the 1 turn sent to it", and one turn is what a slow
    link's verdict usually lets out before it stops a scan, so that is the sentence a pilot meets
    most often."""
    if sent == 1:
        return "the robot's own state did not confirm the turn sent to it"
    return f"the robot's own state confirmed none of the {sent} turns sent to it"


async def search_scan(ctx: VerbContext, p: SearchScanParams) -> VerbResult:
    """Turn in steps, or sweep the head, looking for the target after each step.

    The turns it says it made are the turns the body took. On most bodies that is every turn
    it sent, since a body that cannot take a twist refuses it. A Microduck's robotd answers no
    twist, so a scan of one that took none of them used to say it had turned all the way round
    and found nothing, which a pilot reads as the target not being in the room, and, once a
    refused twist stopped it, that it had turned as far as it had sent. On a body whose own
    state says which twists it took (`twist_shown_since_of`), a turn counts only once that state
    has shown it, and a result whose turns the state did not show says so, in place of the sweep
    it would otherwise claim."""
    if ctx.detector is None:
        return VerbResult.fail("search_scan needs a detector (none configured)")
    mode = scan_mode(ctx.manifest)
    step, steps = scan_steps(p, _lens_deg(ctx))
    if mode == "gaze":
        limit = (ctx.manifest.limits.get("gaze_yaw_deg") if ctx.manifest else None) or (
            DEFAULT_GAZE_LIMIT_DEG
        )
        return await _gaze_sweep(ctx, p, float(limit), step, steps)
    if mode == "none":
        return VerbResult.fail("search_scan needs locomotion or gaze; this robot has neither")
    try:
        since = await _look_ahead(ctx)
    except _LookRefused as refused:
        return await _unlooked(ctx, "search_scan", refused)
    glances = _head_fade_s(ctx) > 0
    starts: list[float] = []  # when each turn that ended began, on the body's clock
    for i in range(steps + 1):
        if (why := ctx.interrupted()) is not None:
            so, turned = _scan_stopped(ctx, starts, steps, step)
            return await _ended_by(ctx, "search_scan", why, so, steps=turned)
        # each view is a frame taken after the turn before it had settled, at the heading the
        # result names, on a body that says when its frames were taken: one handed out from
        # memory after a tenth of a second of settling could show the view from before the
        # turn, or from halfway through it, and the target found in it was reported a step late
        img, hits = await _see(ctx, p.target, f"search_scan {i}", since)
        if img is None:
            return await _no_frame(ctx, "search_scan")
        if (failed := await detector_failed(ctx, "search_scan", "the scan stopped")) is not None:
            return failed
        if hits:
            best = hits[0]
            taken = _turns_taken(ctx, starts)
            after = f"after {i} turn steps"
            if taken is not None and taken < i:
                after += f", of which the robot's own state confirmed {taken}"
            return VerbResult.success(
                f"{p.target} found: {best.summary()} ({after})",
                detections=[d.model_dump() for d in hits],
                steps=i if taken is None else taken,
            )
        if i == steps:
            break
        begun = ctx.transport.now()
        try:
            await _turn(ctx, math.radians(step))
        except _Refused as refused:
            # how far it really turned, the turn it was refused in not counted: a scan of a robot
            # that never turned used to report the whole sweep, the target not in it
            stopped, taken = _scan_stopped(ctx, starts, steps, step)
            return await _stopped_by(ctx, "search_scan", stopped, refused, steps=taken)
        except _Unstopped as unstopped:
            # every twist of the turn was taken and its stop was not, so the turn counts, and
            # the scan ends here rather than look from a body that may still be turning
            starts.append(begun)
            so, turned = _scan_stopped(ctx, starts, steps, step)
            return _unstopped(
                "search_scan", unstopped.missed, VerbResult.fail(f"search_scan: {so}", steps=turned)
            )
        except _Interrupted as told:
            # the turn it was told in not counted either, since it was cut short
            so, turned = _scan_stopped(ctx, starts, steps, step)
            return await _ended_by(ctx, "search_scan", told.why, so, steps=turned)
        starts.append(begun)
        if (why := ctx.interrupted()) is not None:
            # a stop that came while the turn ended, before the look that centres the head again:
            # the head was turned to that look after the stop had been counted
            so, turned = _scan_stopped(ctx, starts, steps, step)
            return await _ended_by(ctx, "search_scan", why, so, steps=turned)
        if glances:
            # The turn ends with a stop, and on a Microduck a stop ends the gaze hold the look
            # that centred the head started, so the head is nobody's again, and robotd glances
            # once the duck has stood still long enough while it waits for its next frame.
            # Centred again straight after the turn, when no glance can be under way since the
            # duck was turning until then, the head is held for this view as for the first.
            await ctx.transport.send_intent(Intent.look(1.0, 0.0, 0.0))
        await ctx.transport.sleep(TICK_S)  # let the view settle
        since = ctx.transport.now()
    taken = _turns_taken(ctx, starts)
    if taken is None or taken == steps:
        return VerbResult.fail(f"{p.target} not found after {steps} steps ({steps * step:.0f}°)")
    if taken == 0:
        return VerbResult.fail(
            f"{p.target} not found, but {_none_confirmed(steps)}, so it may not have turned at all "
            "and every look may have been at the same view",
            steps=0,
        )
    # "may not have", as for a scan it confirmed none of: a turn the state did not show can still
    # have been partly made, when robotd held its twist for less than one frame before the stop
    return VerbResult.fail(
        f"{p.target} not found, but the robot's own state confirmed only {taken} of the "
        f"{steps} turns sent to it ({taken * step:.0f}°), so the scan may not have seen all the "
        f"{steps * step:.0f}° it was meant to",
        steps=taken,
    )


def _scan_stopped(
    ctx: VerbContext, starts: list[float], steps: int, step: float
) -> tuple[str, int]:
    """How far `search_scan` had turned when a twist was refused, in the words its result says
    it in, and the count of turns it reports as `steps`: the turns that ended before the one
    refused, or, on a body whose own state says which twists it took, the ones it confirmed,
    which are none at all on a robot that took no twist and said nothing."""
    made = len(starts)
    taken = _turns_taken(ctx, starts)
    if taken is None or taken == made:
        turned = made if taken is None else taken
        return f"the scan stopped after {turned} of {steps} turns ({turned * step:.0f}°)", turned
    if taken == 0:
        return (
            f"the scan stopped, and {_none_confirmed(made)}, so it may not have turned at all",
            0,
        )
    return (
        f"the scan stopped after {taken} of {steps} turns ({taken * step:.0f}°) the robot's own "
        f"state confirmed, of the {made} sent to it",
        taken,
    )


async def go_to(ctx: VerbContext, p: GoToParams) -> VerbResult:
    """`_go_to`, ended at the first twist the body refuses: stopped, and failed saying why.

    It used to steer on without the twist until its timeout and then say only that it had timed
    out, the hold, the pad or the link that refused it in a log line nobody piloting reads."""
    try:
        return await _go_to(ctx, p)
    except _Refused as refused:
        return await _stopped_by(ctx, "go_to", "the body stopped", refused)
    except _Interrupted as told:
        return await _ended_by(ctx, "go_to", told.why, "the body stopped")
    except _LookRefused as refused:
        return await _unlooked(ctx, "go_to", refused)


def _measures_first(ctx: VerbContext) -> bool:
    """Whether `go_to` measures, then moves, on this body's camera, because its frames come too
    seldom for the steering law.

    The law turns STEER_GAIN radians a second for every degree of bearing and holds that turn
    until the next tick, when it measures again, on the newest frame. It was sized for a camera
    with a new frame most ticks. On a slower one the same frame is measured tick after tick and
    the same turn held, and a frame steers from the moment it was taken until a newer one
    replaces it, which on a camera whose fetch can take as long as its period is up to two
    periods. The law covers a whole bearing in STEER_TURN_S of holding one, so where two periods
    are longer than that, one measurement turns the robot past what it measured, and the next,
    taken before the turn, says to go on turning. On mediad's `/frame`, asked at two frames a
    second, a model of the duck turned a ball out of an alpha's 37° view that way, and the lost
    branch then turned away from it. On such a camera `go_to` turns no further than each
    measurement calls for, and measures again only on a frame taken once the turn had settled.

    A body that does not say how often its frames come keeps the law as it was: a mock, which
    draws a frame when it is asked for, and a stream, which comes faster than a tick."""
    period = frame_period_of(ctx.transport)
    return period is not None and 2 * period > STEER_TURN_S


async def _hold(ctx: VerbContext, twist: tuple[float, float, float], seconds: float) -> None:
    """Hold `twist` for `seconds`, sent again every MOVE_RESEND_S so the deadman stays fed. A
    twist the body refuses ends it with `_Refused`, as every twist here does."""
    slices = max(1, round(seconds / MOVE_RESEND_S))
    for _ in range(slices):
        await _twist(ctx, *twist)
        await ctx.transport.sleep(seconds / slices)


async def _settle(ctx: VerbContext, vx: float) -> tuple[float, tuple[float, float, float] | None]:
    """End a turn of `go_to`'s: walk on straight at `vx`, or stand, for a tick while the turn
    dies down, and return the moment after which a frame shows where the turn left the robot,
    with the twist to hold while that frame comes, which `_see_holding` holds for no more than
    a deadman window."""
    straight = _clamped(ctx, vx, 0.0, 0.0)
    await _twist(ctx, *straight)
    await ctx.transport.sleep(TICK_S)
    return ctx.transport.now(), (straight if straight[0] else None)


#: What a refusal before the approach says went out: `_look_ahead` centres the head first, and
#: the refusal is decided on the frame taken after it, so that look has been sent, and nothing else.
_ONLY_THE_LOOK = "nothing was sent but the look that centres the head"


def _out_of_range(ctx: VerbContext, p: GoToParams, size: tuple[int, int]) -> VerbResult | None:
    """A refusal of a stop distance nearer than the camera can measure the target at on frames
    of `size`, naming one it can, or None. Asked of the first frame, before anything but the look
    that centres the head has been sent.

    A detector that reads distance off a box's size reads no nearer than a box as big as the
    frame, and the target only fills the frame more from there, so such a stop distance was never
    reached and the body walked on into it until the timeout: a person, 1.775 m at the nearest on
    a 4:3 frame through the guessed lens, was out of reach at every stop distance the verb took.
    The detector says how near it can measure (`nearest_measurable`); one that does not say is
    not guessed for. The distance named is that a creeping step farther, rounded up to the
    centimetre (`_askable`), so that asked for, it is arrived at by measurement."""
    nearest = nearest_measurable(ctx.detector, p.target, size)
    if nearest is None or p.stop_distance >= nearest:
        return None
    need = _askable(nearest)
    cannot = (
        f"go_to: the camera cannot measure the {p.target} near enough to stop at "
        f"{p.stop_distance:g} m: the nearest stop_distance it can is {need:.2f} m"
    )
    if need > MAX_STOP_DISTANCE_M:
        return VerbResult.fail(
            f"{cannot}, farther than the {MAX_STOP_DISTANCE_M:g} m go_to takes, so it cannot "
            f"approach the {p.target} with this camera; {_ONLY_THE_LOOK}.",
            nearest_m=need,
        )
    return VerbResult.fail(
        f"{cannot}; {_ONLY_THE_LOOK}. Ask for {need:.2f} m or more.", nearest_m=need
    )


def _stopper(hits: list[Detection], d: Detection) -> Detection | None:
    """The detection of the target that ends the approach, or None: one the frame cuts
    (`Detection.clipped`), or one with no distance a body can be at (`usable_m`), the one steered
    by first. `go_to` judged the one it steered by alone, so a person the frame cut a metre
    ahead went unread while it walked toward a whole one five metres off, named first."""
    for hit in (d, *hits):
        if hit.clipped or usable_m(hit.est_distance_m) is None:
            return hit
    return None


def _same_target(
    before: Detection,
    hits: list[Detection],
    seconds: float,
    size: tuple[int, int],
    lens_deg: float | None,
) -> tuple[Detection | None, bool]:
    """The detection in `hits` that is the target steered by `seconds` before (`before`), on
    frames of `size` through a lens `lens_deg` wide, and whether nothing else could be, or None
    when no detection could be it.

    `go_to` took its target to be whichever box a frame listed first, and a network lists the most
    confident first and scores a box the frame cuts lower: once the frame cut the ball walked
    toward, a whole ball three metres off came first, and was taken for it. A target is known by
    where it can have got to instead. Between two frames the body walks at most `WALK_VX` and
    turns at most `STEER_MAX_WZ`, or `LOST_WZ` looking for it, for as long as passed between them,
    so a still target's distance can change by no more than the walk, and its bearing by no more
    than the turn and the walk's parallax at the nearer of its two readings. Each reading is also
    only as fine as a pixel: a bearing by a pixel across the frame's middle, a distance by a pixel
    of the size it is ranged by (taken as the radius of a disc of the detection's area, which is
    never more than a box's height or a blob's radius, so the allowance errs wide), and each by
    the step the detectors round it to. A detection outside those bounds is not the target, and
    two inside them cannot be told apart, which is `False`. A target that walks off itself,
    faster than that, cannot be followed, and is not."""
    w, h = size
    pixel_deg = (
        math.degrees(math.atan(math.tan(math.radians(lens_deg) / 2) / (w / 2)))
        if lens_deg is not None
        else 0.0
    )

    def pixel_m(d: Detection, m: float) -> float:
        # a reading `m` off a target `r` pixels across moves by `m / r` for one pixel of `r`
        return m / max(1.0, math.sqrt(d.area * w * h / math.pi))

    walked = WALK_VX * seconds
    turned = math.degrees(max(STEER_MAX_WZ, LOST_WZ) * seconds)
    was_m = usable_m(before.est_distance_m)
    fits: list[tuple[float, Detection]] = []
    for hit in hits:
        is_m = usable_m(hit.est_distance_m)
        known = [m for m in (was_m, is_m) if m is not None]
        parallax = math.degrees(math.atan2(walked, min(known))) if known else 0.0
        turn = abs((hit.bearing_deg or 0.0) - (before.bearing_deg or 0.0))
        may_turn = turned + parallax + 2 * (pixel_deg + BEARING_STEP_DEG)
        if turn > may_turn:
            continue
        moved, may_move = 0.0, walked
        if is_m is not None and was_m is not None:
            moved = abs(is_m - was_m)
            may_move += pixel_m(before, was_m) + pixel_m(hit, is_m) + 2 * DISTANCE_STEP_M
            if moved > may_move:
                continue
        fits.append((turn / may_turn + (moved / may_move if may_move else 0.0), hit))
    if not fits:
        return None, False
    fits.sort(key=lambda fit: fit[0])
    return fits[0][1], len(fits) == 1


async def _no_nearer(
    ctx: VerbContext,
    p: GoToParams,
    stopper: Detection,
    steered: Detection,
    last_m: float | None,
    tick: int,
    tracked: bool = True,
) -> VerbResult:
    """Stop the body where the camera cannot range a detection of the target, and say what is
    known: which one ended the approach (the one steered by, or another beside it), that the
    frame's edge cuts it (`Detection.clipped`) so its distance cannot be measured, or that the
    detector gives no distance for it (`usable_m`), the reading as the farthest it can be where
    there is one, and, when nothing had moved yet, that the approach did not start and what the
    pilot can do. With no distance for the target steered by from its first sighting there is
    nothing to approach by, and the verb is refused.

    `go_to` used to walk on through all of these until its timeout, at up to 0.2 m/s, into a
    person, a pet or a ball under the nose. Stopping is not an error and not an arrival: a target
    the frame's edge cuts is the normal end of a walk toward a person, so the result is not a
    failure that `abort_when` counts, and it never says "reached" with a distance nobody
    measured. Its data has `at_most_m` and `cannot_range` where an arrival has `distance_m`,
    `started`, and `another`, with the steered one's `steered_m`, when it was not the target
    steered by. Nor does it say the target fills the frame, which a pilot reads as under the nose:
    a ball cut by the frame's bottom 0.4 m off, or a picture cut by its top, fills none of it. A
    reading is the farthest it can be only if the target is the size the detector takes it for, so
    the result says that rather than claiming the bound outright."""
    unstopped = await _stop(ctx)
    name = getattr(ctx.detector, "name", "the detector")
    another = stopper is not steered
    started = tick > 1
    reading = usable_m(stopper.est_distance_m)
    bearing = stopper.bearing_deg or 0.0
    which = f"another {p.target}, bearing {bearing:+.0f}°," if another else f"the {p.target},"
    at_most: float | None
    if reading is not None:
        at_most, why = reading, "cut by the frame"
        what = (
            f"the frame's edge cuts {which} so its distance cannot be measured; it reads "
            f"~{reading:.2f} m, the farthest it can be if it is the size the detector takes it for"
        )
    elif another:
        at_most, why = None, "no distance"
        what = f"{name} gives no distance for {which} so how near it is cannot be told"
    elif last_m is not None:
        at_most, why = last_m, "no distance"
        what = (
            f"{name} gives no distance for the {p.target} now; its last reading was ~{last_m:.2f} m"
        )
    else:
        return _unstopped(
            "go_to",
            unstopped,
            VerbResult.fail(
                f"go_to: {name} gives no distance for the {p.target}, so it cannot tell when to "
                "stop, and did not approach it"
            ),
        )
    await ctx.transport.sleep(TICK_S)
    guess, guessed = _guessed(stopper)
    if not another:
        what += f", bearing {bearing:+.0f}°"
    summary = f"go_to {'stopped' if started else 'did not start'}: {what}{guess}"
    extra: dict[str, Any] = {}
    if another:
        steered_m = usable_m(steered.est_distance_m)
        steered_bearing = steered.bearing_deg or 0.0
        extra = {"another": True, "steered_m": steered_m, "steered_bearing_deg": steered_bearing}
        if steered_m is not None:
            summary += (
                f". The {p.target} it steered by reads ~{steered_m:.2f} m, bearing "
                f"{steered_bearing:+.0f}°"
            )
    if not tracked:
        summary += f". {_UNTRACKED.format(target=p.target)}"
    if not started:
        if not another and at_most is not None and at_most <= p.stop_distance:
            summary += ". Nothing moved: by that reading it is within the stop_distance already."
        else:
            that = f"that {p.target}" if another else "it"
            until = "whole in the frame or out of view" if reading is not None else "out of view"
            summary += f". Nothing moved: look or turn until {that} is {until}, then ask again."
    return _unstopped(
        "go_to",
        unstopped,
        VerbResult.success(
            summary,
            at_most_m=at_most,
            bearing_deg=bearing,
            ticks=tick,
            cannot_range=why,
            started=started,
            tracked=tracked,
            **extra,
            **guessed,
        ),
    )


#: What `go_to` says when it cannot vouch that the detection it steered by at the end is the one
#: it set out toward (`_same_target`).
_UNTRACKED = (
    "It cannot tell which {target} it walked toward: another of its kind was where that one "
    "could have moved to, or none was"
)


def _guessed(d: Detection) -> tuple[str, dict[str, Any]]:
    """What `go_to`'s result adds for a distance measured through a lens nobody measured: the
    clause `Detection.summary` says it in, and `calibrated: False` in the data. Nothing for a
    measured lens, so such a result reads as it always did.

    The arrival dropped the detection's flag, so 'reached the ball: ~0.25 m away' read as measured
    where `observe` had said the distance was a rough guess, and on a guessed lens that is the
    difference between arriving and announcing it from half a metre away."""
    if d.calibrated:
        return "", {}
    return " (uncalibrated: distance is a rough guess)", {"calibrated": False}


async def _go_to(ctx: VerbContext, p: GoToParams) -> VerbResult:
    """Steer toward the target on what the camera sees until it is close enough.

    On a camera that keeps up with the steering law a frame is measured every tick and the turn
    it calls for held for that tick (STEER_GAIN). On a slower one (`_measures_first`) each turn
    is held only for as long as it takes to cover the bearing the frame measured, then the robot
    walks on straight and the next frame acted on is one taken once the turn had settled, so no
    measurement turns it past what it measured, and none taken before a turn steers after it.

    A target lost from view is looked for by turning toward the side it was last seen on, as
    before. Measured first, no turn goes further than the bearing that called for it, so that
    side is still where the target should be, as it is on a camera that keeps up with the law.
    It was the law's overshoot on a slow camera that sent the robot the wrong way. Measuring
    first, the lost branch too turns only between frames taken once its last turn had settled,
    each turn lasting STEER_TURN_S and counting for as many ticks, so it turns as far before it
    gives up as on a camera that keeps up.

    It walks only toward a target it can range nearer: a stop distance the camera cannot measure
    is refused on the first frame (`_out_of_range`), and a detection of the target the frame's
    edge cuts, or that the detector gives no distance for, ends the approach (`_no_nearer`)."""
    if ctx.detector is None:
        return VerbResult.fail("go_to needs a detector (none configured)")
    # the first frame acted on is one taken once the head has settled (`_look_ahead`)
    since: float | None = await _look_ahead(ctx)
    t0 = ctx.transport.now()
    lost = 0.0  # ticks' worth of the lost branch's turn since the target was last seen
    last_bearing = 0.0
    last_m: float | None = None  # the last distance a detection of the target gave
    # the detection steered by on the last frame that showed the target, and when; and whether
    # every frame since the first could tell the target from the rest (`_same_target`)
    steered: Detection | None = None
    steered_at = t0
    tracked = True
    tick = 0
    held: tuple[float, float, float] | None = None
    while ctx.transport.now() - t0 < p.timeout_s:
        tick += 1
        img, hits = await _see_holding(ctx, p.target, f"go_to {p.target}", held, since)
        seen_at = ctx.transport.now()
        since = None
        if img is None:
            return await _no_frame(ctx, "go_to")
        if (failed := await detector_failed(ctx, "go_to", "the body stopped")) is not None:
            return failed
        if tick == 1 and (refused := _out_of_range(ctx, p, img.size)) is not None:
            return refused
        measures_first = _measures_first(ctx)
        if not hits:
            hold = STEER_TURN_S if measures_first else TICK_S
            lost += hold / TICK_S
            if lost > LOST_TICKS:
                unstopped = await _stop(ctx)
                return _unstopped(
                    "go_to", unstopped, VerbResult.fail(f"lost the {p.target}; try search_scan")
                )
            # turn toward where it was last seen
            held = _clamped(ctx, 0.0, 0.0, LOST_WZ if last_bearing >= 0 else -LOST_WZ)
        else:
            lost = 0.0
            # the target is the detection where the one steered by last could have got to; on
            # the first sighting, and when none could be it, the one listed first, as it always
            # was, but then go_to can no longer vouch for which target it is walking toward
            d = hits[0]
            if steered is not None:
                same, alone = _same_target(
                    steered, hits, seen_at - steered_at, img.size, _lens_deg(ctx)
                )
                d = same if same is not None else d
                tracked = tracked and same is not None and alone
            steered, steered_at = d, seen_at
            bearing = d.bearing_deg or 0.0
            dist = usable_m(d.est_distance_m)
            last_bearing = bearing
            if (stopper := _stopper(hits, d)) is not None or dist is None:
                # no reading nearer than this one will come, of this target or one the body
                # would pass on its way, so walking on would be walking into it on a number that
                # has stopped falling
                stopper = stopper or d
                return await _no_nearer(ctx, p, stopper, d, last_m, tick, tracked)
            if dist <= p.stop_distance:
                held = None
                unstopped = await _stop(ctx)
                await ctx.transport.sleep(TICK_S)
                guess, guessed = _guessed(d)
                doubt = "" if tracked else f". {_UNTRACKED.format(target=p.target)}"
                return _unstopped(
                    "go_to",
                    unstopped,
                    VerbResult.success(
                        f"reached the {p.target}: ~{dist:.2f} m away, bearing "
                        f"{bearing:+.0f}°{guess}" + doubt,
                        distance_m=dist,
                        bearing_deg=bearing,
                        ticks=tick,
                        **guessed,
                        **({} if tracked else {"tracked": False}),
                    ),
                )
            last_m = dist
            wz = max(-STEER_MAX_WZ, min(STEER_MAX_WZ, bearing * STEER_GAIN))
            vx = WALK_VX if abs(bearing) < WALK_BEARING_DEG else TURN_VX
            if dist < p.stop_distance + CREEP_BAND_M:
                vx = min(vx, CREEP_VX)  # creep in
            held = _clamped(ctx, vx, 0.0, wz)
            # measuring first, the turn is held for as long as it takes to cover the bearing this
            # frame measured and no longer, or, with no turn to make, for as long as the law takes
            # to cover one
            hold = TICK_S
            if measures_first:
                hold = abs(math.radians(bearing) / held[2]) if held[2] else STEER_TURN_S
        if measures_first:
            await _hold(ctx, held, hold)
            since, held = await _settle(ctx, held[0])
        else:
            await _twist(ctx, *held)
            await ctx.transport.sleep(TICK_S)
    unstopped = await _stop(ctx)
    return _unstopped(
        "go_to",
        unstopped,
        VerbResult.fail(f"go_to timed out after {p.timeout_s:g}s without reaching the {p.target}"),
    )


async def approach_and(ctx: VerbContext, p: ApproachAndParams) -> VerbResult:
    if ctx.run_verb is None:
        return VerbResult.fail("approach_and needs an executor")
    if not ctx.takes_no_arguments(p.then):
        # `then` carries no arguments, so a verb that needs some would be refused after the walk,
        # with nothing the pilot could correct through this verb
        return VerbResult.fail(
            f"approach_and cannot end in {p.then}, which needs arguments that `then` has no way "
            f"to give it: call go_to, then {p.then}"
        )
    first = await ctx.run_verb("go_to", {"target": p.target, "stop_distance": p.stop_distance})
    if not first.ok:
        return first
    at_most = first.data.get("at_most_m")
    another = first.data.get("another") is True
    tracked = first.data.get("tracked") is not False
    within = not another and isinstance(at_most, int | float) and at_most <= p.stop_distance
    if "cannot_range" in first.data and not (within and tracked):
        # `then` is promised at the stop distance of the target walked toward, and where the
        # frame's edge cuts a detection its reading is only an upper bound, which can lie beyond
        # it: a ball whose box the frame cuts at the top while it is still out of reach. A bound
        # within the stop distance puts the target within it too, and `then` runs, as it did for
        # a ball filling the frame before this ending existed, but only the bound of the target
        # walked toward: another one's put a kick out straight ahead at a ball 1.5 m off, while
        # the cut one lay to the side. Nor does it run on a bound where go_to cannot tell which
        # detection is the target it walked toward (`_same_target`). An arrival is another
        # matter: measured within the stop distance, the body is there for a target of that
        # label, whichever one, which is all `then` was ever promised, and it runs as before.
        # The body stays where go_to stopped it, and the pilot decides
        if not tracked:
            reason = (
                f"go_to cannot tell which {p.target} it walked toward, so it is not known to be "
                "the one within the stop_distance"
            )
        elif another:
            steered = first.data.get("steered_m")
            reason = (
                f"go_to stopped for another {p.target}, not the {p.target} it walked toward"
                + (f", which reads ~{steered:.2f} m" if isinstance(steered, int | float) else "")
            )
        elif isinstance(at_most, int | float):
            reason = (
                f"go_to read the {p.target} at most ~{at_most:.3f} m away, which may be farther "
                f"than the stop_distance of {p.stop_distance:g} m"
            )
        else:
            reason = f"go_to has no distance for the {p.target}"
        return VerbResult.fail(
            f"approach_and did not run {p.then}: {reason}. go_to said: {first.summary}. Run "
            f"{p.then} yourself if it is near enough.",
            go_to=first.data,
            at_most_m=at_most,
            not_run=p.then,
        )
    # a stop between the walk and the `then` verb ends it there, rather than kicking anyway
    if (why := ctx.interrupted()) is not None:
        short = f"it stopped before {p.then}: {first.summary}"
        return await _ended_by(ctx, "approach_and", why, short, go_to=first.data)
    second = await ctx.run_verb(p.then, {})
    data: dict[str, Any] = {"go_to": first.data, p.then: second.data}
    if "ended_by" in second.data:
        # a `then` a stop ended, or kept from starting, is the brake working in approach_and as
        # it is in the verb itself, and `abort_when`'s failures in a row pass over it in both
        data["ended_by"] = second.data["ended_by"]
    return VerbResult(
        ok=second.ok,
        summary=f"{first.summary}; then {p.then}: {second.summary}",
        data=data,
    )


# ── requirements: what a manifest must offer for a core verb to exist ───────────────────


@dataclass(frozen=True)
class Requirement:
    intents: frozenset[str] = frozenset()
    """Every one of these intents."""
    any_intents: frozenset[str] = frozenset()
    """At least one of these intents."""
    mobility: bool = False
    """`mobility` must not be `none`."""
    camera: bool = False
    """`camera` must be among the sensors."""
    sensors: frozenset[str] = frozenset()
    """Every one of these sensors: `odometry` for a verb that reads where the robot is."""


REQUIREMENTS: dict[str, Requirement] = {
    "observe": Requirement(camera=True),
    "report_state": Requirement(),
    "stop": Requirement(),
    "say": Requirement(intents=frozenset({"sound"})),
    "move": Requirement(intents=frozenset({"twist"}), mobility=True),
    "go_to": Requirement(intents=frozenset({"twist"}), mobility=True, camera=True),
    "search_scan": Requirement(camera=True, any_intents=frozenset({"twist", "gaze"})),
    "approach_and": Requirement(intents=frozenset({"twist"}), mobility=True, camera=True),
    "map": Requirement(sensors=frozenset({"odometry"})),
    "mark_place": Requirement(sensors=frozenset({"odometry"})),
    "forget_place": Requirement(sensors=frozenset({"odometry"})),
    "go_to_place": Requirement(
        intents=frozenset({"twist"}), mobility=True, sensors=frozenset({"odometry"})
    ),
}


def core_requirements_unmet(name: str, manifest: RobotManifest) -> str | None:
    """Why `name` cannot be a core verb of `manifest`, or None when it can."""
    req = REQUIREMENTS.get(name)
    if req is None:
        return "is not a core verb Roboglot knows (declare it with core: false)"
    missing: list[str] = []
    if req.camera and "camera" not in manifest.sensors:
        missing.append("a camera")
    for sensor in sorted(req.sensors):
        if sensor not in manifest.sensors:
            missing.append(f"the {sensor} sensor")
    if req.mobility and manifest.mobility == "none":
        missing.append("mobility")
    for intent in sorted(req.intents):
        if intent not in manifest.intents:
            missing.append(f"the {intent} intent")
    if req.any_intents and not (set(req.any_intents) & set(manifest.intents)):
        missing.append("one of the " + " or ".join(sorted(req.any_intents)) + " intents")
    return None if not missing else "needs " + ", ".join(missing)


# ── templates: what `registry_from_manifest` instantiates ────────────────────────────────

CORE: dict[str, Verb] = {
    v.name: v
    for v in (
        Verb(
            "observe",
            "Capture a camera frame and report what is detected in it.",
            observe,
            NoParams,
            timeout_s=10,
            read_only=True,
            core=True,
        ),
        Verb(
            "report_state",
            "Report the robot's state: posture, battery, pose, whatever it knows.",
            report_state,
            NoParams,
            timeout_s=5,
            read_only=True,
            core=True,
        ),
        Verb(
            "stop",
            "Stop moving immediately (zero velocity). Always allowed.",
            stop,
            NoParams,
            timeout_s=5,
            core=True,
        ),
        Verb("say", "Say something out loud.", say, SayParams, timeout_s=5, core=True),
        Verb(
            "move",
            "Move with a velocity for a duration. Use small values.",
            move,
            MoveParams,
            timeout_s=15,
            core=True,
            done_condition="the duration has elapsed and the robot has stopped.",
        ),
        Verb(
            "go_to",
            "Go toward a detected target and stop at a distance. Closes the loop on the "
            "camera itself. Stops sooner, without arriving, where the frame's edge cuts the "
            "target, or another of its kind, so that the camera can no longer measure its "
            "distance.",
            go_to,
            GoToParams,
            timeout_s=70,
            kind="composite",
            core=True,
            done_condition="within stop_distance of the target, stopped where the frame's edge "
            "cuts a detection of it so that its distance cannot be measured, the target was "
            "lost, or timed out.",
        ),
        Verb(
            "search_scan",
            "Look around in steps for a target (turn in place, or sweep the head). Returns "
            "where it was seen.",
            search_scan,
            SearchScanParams,
            timeout_s=60,
            kind="composite",
            core=True,
            done_condition="the target was detected, or a full sweep found nothing.",
        ),
        Verb(
            "approach_and",
            "go_to a target, then run another verb (kick, grab). The other verb runs only once "
            "the camera puts the target within stop_distance.",
            approach_and,
            ApproachAndParams,
            timeout_s=90,
            kind="composite",
            core=True,
        ),
    )
}
# the verbs that name places and list them, on every body that says where it is
CORE |= PLACE_VERBS
