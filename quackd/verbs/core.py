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
import math
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from PIL import Image
from pydantic import BaseModel, ConfigDict, Field

from quackd.perception.base import Detection, detect_off_loop, summarize_detections
from quackd.transport.base import (
    Intent,
    camera_names_of,
    frame_period_of,
    frames_of,
    frames_taken_since,
    primary_of,
    twist_shown_since_of,
)
from quackd.verbs.registry import STOP_ENDED, STOPPED, NoParams, Verb, VerbContext, VerbResult

if TYPE_CHECKING:
    from quackd.adapters.manifest import RobotManifest

MOVE_RESEND_S = 0.1
#: How long a closed loop may hold its last twist while waiting for the next frame. One
#: deadman window: past that the steering loop is stalled, and a duck that stops is right.
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
        default=0.25, ge=0.1, le=1.5, description="Stop this far away (m)."
    )
    timeout_s: float = Field(default=20.0, gt=0, le=60)


class ApproachAndParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target: str = Field(default="ball")
    stop_distance: float = Field(default=0.25, ge=0.1, le=1.5)
    then: str = Field(..., description="Verb to run once close (e.g. kick, grab).")


# ── helpers shared with adapter verbs ───────────────────────────────────────────────────


async def send_or_fail(ctx: VerbContext, intent: Intent) -> VerbResult | None:
    """Send an intent; return a failure result if the robot refused it."""
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


async def _twist(ctx: VerbContext, vx: float, vy: float, wz: float) -> None:
    """Send one twist, and raise `_Refused` with the body's own reason when it refuses it.

    `move` has always ended at a refused twist, through `send_or_fail`. `go_to`, `search_scan`
    and the helpers they steer through threw the answer away, so a body that would not take a
    twist was steered on regardless: `go_to` ran out its whole timeout and said only that it
    had timed out, and `search_scan` reported a full sweep of a duck that never turned, which a
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
    await ctx.transport.stop()
    return VerbResult.fail(
        f"{verb}: a twist was refused, so {stopped}: {refused}", move_refused=str(refused), **data
    )


async def _ended_by(ctx: VerbContext, verb: str, why: str, stopped: str, **data: Any) -> VerbResult:
    """Stop the body for a verb told to end from beside it, and fail saying why and how far it
    got, as `watch` does.

    The `stop` that ended it has stopped the body already, and this stops it again, after the
    last twist the verb sent rather than before it. A `stop` is marked `ended_by` (`STOP_ENDED`),
    so `abort_when`'s count of failures in a row passes over it: a person braking a walk three
    times would otherwise end the run on the same verb failing three times in a row."""
    await ctx.transport.stop()
    if why == STOPPED:
        data["ended_by"] = STOP_ENDED
    return VerbResult.fail(f"{verb}: {why}, so {stopped}", interrupted=why, **data)


def _head_fade_s(ctx: VerbContext) -> float:
    """How long the body's head takes to let go of a glance of its own once a look arrives
    (`head_fade_s`), or 0 on a body whose head moves only when it is asked to."""
    fade = getattr(ctx.transport, "head_fade_s", 0.0)
    if isinstance(fade, int | float) and not isinstance(fade, bool) and fade > 0:
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
    say hands out the newest frame, as before, and on the simulators that is drawn when it is
    asked for.
    """
    await ctx.transport.send_intent(Intent.look(1.0, 0.0, 0.0))
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
    schema bounds, which are the Microduck's, so its runs stay byte-identical."""
    limits = manifest.limits if manifest is not None else {}
    return (
        float(limits.get("max_vx", MAX_VX)),
        float(limits.get("max_vy", MAX_VY)),
        float(limits.get("max_wz", MAX_WZ)),
    )


def _clamped(ctx: VerbContext, vx: float, vy: float, wz: float) -> tuple[float, float, float]:
    max_vx, max_vy, max_wz = speed_limits(ctx.manifest)
    return (
        max(-max_vx, min(max_vx, vx)),
        max(-max_vy, min(max_vy, vy)),
        max(-max_wz, min(max_wz, wz)),
    )


async def _turn(ctx: VerbContext, radians: float) -> None:
    """Rotate in place by `radians`, feeding the deadman every tick. A twist the body refuses
    ends the turn where it is, with `_Refused` for the verb to stop on."""
    rate = min(TURN_RATE, speed_limits(ctx.manifest)[2])
    wz = rate if radians >= 0 else -rate
    duration = abs(radians) / rate
    ticks = max(1, round(duration / TICK_S))
    for _ in range(ticks):
        await _twist(ctx, 0.0, 0.0, wz)
        await ctx.transport.sleep(duration / ticks)
    await ctx.transport.stop()


# ── the verbs ───────────────────────────────────────────────────────────────────────────


async def _no_frame(ctx: VerbContext, verb: str) -> VerbResult:
    """A frame that did not arrive is not the same as a robot with no camera, and neither
    is a reason to keep walking. The `img is None` branch used to return without stopping."""
    await ctx.transport.stop()
    why = getattr(ctx.transport, "camera_error", None)
    return VerbResult.fail(f"{verb}: {why}" if why else f"{verb} needs a camera and got no frame")


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
    await ctx.transport.stop()
    name = getattr(ctx.detector, "name", "the detector")
    return VerbResult.fail(f"{verb}: {name} failed, so {stopped}: {why}", detector_error=why)


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
    decode + detect`. The daemon's deadman is 300 ms and its comment sizes it on "quackd
    re-sends at 10 Hz", which is true of `move` and `_turn` and false of the one verb that
    walks while looking. Over the ssh tunnel the checklist mandates, a single latency spike
    past ~200 ms zeroed the velocity mid-stride with nothing in the log to say why.

    A free-running pump inside the transport would be worse: it would keep the duck walking
    blind for the whole fetch timeout, turning the deadman's meaning from "the steering loop
    is alive" into "the socket is up". This holds only the last justified twist, and only
    for one deadman window — after that the steering loop really is stalled, and a duck that
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
        # a backend that knows why it has no picture says so. A camera quackd was told to
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
    await ctx.transport.stop()
    # `stop` is the verb the pilot is told to reach for when anything looks wrong, and it is
    # asked for most often when the link is the thing that is wrong. A transport that knows it
    # could not deliver says so; reporting success anyway would be the one reassuring line in
    # the log that was not true. Transports that cannot tell report nothing and are unchanged.
    if undelivered := getattr(ctx.transport, "stop_error", None):
        return VerbResult.fail(
            f"stop could not be delivered: {undelivered}. If this robot has a deadman it is "
            "what stops the legs now; otherwise use the hardware switch."
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
            await ctx.transport.stop()
            return fail
        await ctx.transport.sleep(step)
    await ctx.transport.stop()
    clamped = (vx, vy, wz) != (p.vx, p.vy, p.wz)
    return VerbResult.success(
        f"walked vx={vx:.2f} vy={vy:.2f} wz={wz:.2f} for {p.duration_s:.1f}s"
        + (" (clamped to this robot's limits)" if clamped else ""),
        duration_s=p.duration_s,
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
    per_turn = math.floor(360.0 / lens_deg) + 1
    if 360.0 / per_turn >= p.step_deg:
        return p.step_deg, p.max_steps
    steps = p.max_steps if "max_steps" in p.model_fields_set else min(MAX_SCAN_STEPS, per_turn)
    return 360.0 / per_turn, steps


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
        await ctx.transport.send_intent(Intent.look(math.cos(rad), math.sin(rad), 0.0))
        await ctx.transport.sleep(TICK_S)  # let the head settle; the sim clock advances
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
    since = await _look_ahead(ctx)
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
            # how far it really turned, the turn it was refused in not counted: a scan of a duck
            # that never turned used to report the whole sweep, the target not in it
            stopped, taken = _scan_stopped(ctx, starts, steps, step)
            return await _stopped_by(ctx, "search_scan", stopped, refused, steps=taken)
        except _Interrupted as told:
            # the turn it was told in not counted either, since it was cut short
            so, turned = _scan_stopped(ctx, starts, steps, step)
            return await _ended_by(ctx, "search_scan", told.why, so, steps=turned)
        starts.append(begun)
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


def _measures_first(ctx: VerbContext) -> bool:
    """Whether `go_to` measures, then moves, on this body's camera, because its frames come too
    seldom for the steering law.

    The law turns STEER_GAIN radians a second for every degree of bearing and holds that turn
    until the next tick, when it measures again, on the newest frame. It was sized for a camera
    with a new frame most ticks. On a slower one the same frame is measured tick after tick and
    the same turn held, and a frame steers from the moment it was taken until a newer one
    replaces it, which on a camera whose fetch can take as long as its period is up to two
    periods. The law covers a whole bearing in STEER_TURN_S of holding one, so where two periods
    are longer than that, one measurement turns the duck past what it measured, and the next,
    taken before the turn, says to go on turning. On mediad's `/frame`, asked at two frames a
    second, a model of the duck turned a ball out of an alpha's 37° view that way, and the lost
    branch then turned away from it. On such a camera `go_to` turns no further than each
    measurement calls for, and measures again only on a frame taken once the turn had settled.

    A body that does not say how often its frames come keeps the law as it was: the simulators,
    which draw a frame when it is asked for, and a stream, which comes faster than a tick."""
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
    dies down, and return the moment after which a frame shows where the turn left the duck,
    with the twist to hold while that frame comes, which `_see_holding` holds for no more than
    a deadman window."""
    straight = _clamped(ctx, vx, 0.0, 0.0)
    await _twist(ctx, *straight)
    await ctx.transport.sleep(TICK_S)
    return ctx.transport.now(), (straight if straight[0] else None)


async def _go_to(ctx: VerbContext, p: GoToParams) -> VerbResult:
    """Steer toward the target on what the camera sees until it is close enough.

    On a camera that keeps up with the steering law a frame is measured every tick and the turn
    it calls for held for that tick (STEER_GAIN). On a slower one (`_measures_first`) each turn
    is held only for as long as it takes to cover the bearing the frame measured, then the duck
    walks on straight and the next frame acted on is one taken once the turn had settled, so no
    measurement turns it past what it measured, and none taken before a turn steers after it.

    A target lost from view is looked for by turning toward the side it was last seen on, as
    before. Measured first, no turn goes further than the bearing that called for it, so that
    side is still where the target should be, as it is on a camera that keeps up with the law.
    It was the law's overshoot on a slow camera that sent the duck the wrong way. Measuring
    first, the lost branch too turns only between frames taken once its last turn had settled,
    each turn lasting STEER_TURN_S and counting for as many ticks, so it turns as far before it
    gives up as on a camera that keeps up."""
    if ctx.detector is None:
        return VerbResult.fail("go_to needs a detector (none configured)")
    # the first frame acted on is one taken once the head has settled (`_look_ahead`)
    since: float | None = await _look_ahead(ctx)
    t0 = ctx.transport.now()
    lost = 0.0  # ticks' worth of the lost branch's turn since the target was last seen
    last_bearing = 0.0
    tick = 0
    held: tuple[float, float, float] | None = None
    while ctx.transport.now() - t0 < p.timeout_s:
        tick += 1
        img, hits = await _see_holding(ctx, p.target, f"go_to {p.target}", held, since)
        since = None
        if img is None:
            return await _no_frame(ctx, "go_to")
        if (failed := await detector_failed(ctx, "go_to", "the body stopped")) is not None:
            return failed
        measures_first = _measures_first(ctx)
        if not hits:
            hold = STEER_TURN_S if measures_first else TICK_S
            lost += hold / TICK_S
            if lost > LOST_TICKS:
                await ctx.transport.stop()
                return VerbResult.fail(f"lost the {p.target}; try search_scan")
            # turn toward where it was last seen
            held = _clamped(ctx, 0.0, 0.0, LOST_WZ if last_bearing >= 0 else -LOST_WZ)
        else:
            lost = 0.0
            d = hits[0]
            bearing = d.bearing_deg or 0.0
            dist = d.est_distance_m
            last_bearing = bearing
            if dist is not None and dist <= p.stop_distance:
                held = None
                await ctx.transport.stop()
                await ctx.transport.sleep(TICK_S)
                return VerbResult.success(
                    f"reached the {p.target}: ~{dist:.2f} m away, bearing {bearing:+.0f}°",
                    distance_m=dist,
                    bearing_deg=bearing,
                    ticks=tick,
                )
            wz = max(-STEER_MAX_WZ, min(STEER_MAX_WZ, bearing * STEER_GAIN))
            vx = 0.2 if abs(bearing) < 25 else 0.05
            if dist is not None and dist < p.stop_distance + 0.15:
                vx = min(vx, 0.1)  # creep in
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
    await ctx.transport.stop()
    return VerbResult.fail(
        f"go_to timed out after {p.timeout_s:g}s without reaching the {p.target}"
    )


async def approach_and(ctx: VerbContext, p: ApproachAndParams) -> VerbResult:
    if ctx.run_verb is None:
        return VerbResult.fail("approach_and needs an executor")
    first = await ctx.run_verb("go_to", {"target": p.target, "stop_distance": p.stop_distance})
    if not first.ok:
        return first
    # a stop between the walk and the `then` verb ends it there, rather than kicking anyway
    if (why := ctx.interrupted()) is not None:
        short = f"it stopped before {p.then}: {first.summary}"
        return await _ended_by(ctx, "approach_and", why, short, go_to=first.data)
    second = await ctx.run_verb(p.then, {})
    return VerbResult(
        ok=second.ok,
        summary=f"{first.summary}; then {p.then}: {second.summary}",
        data={"go_to": first.data, p.then: second.data},
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


REQUIREMENTS: dict[str, Requirement] = {
    "observe": Requirement(camera=True),
    "report_state": Requirement(),
    "stop": Requirement(),
    "say": Requirement(intents=frozenset({"sound"})),
    "move": Requirement(intents=frozenset({"twist"}), mobility=True),
    "go_to": Requirement(intents=frozenset({"twist"}), mobility=True, camera=True),
    "search_scan": Requirement(camera=True, any_intents=frozenset({"twist", "gaze"})),
    "approach_and": Requirement(intents=frozenset({"twist"}), mobility=True, camera=True),
}


def core_requirements_unmet(name: str, manifest: RobotManifest) -> str | None:
    """Why `name` cannot be a core verb of `manifest`, or None when it can."""
    req = REQUIREMENTS.get(name)
    if req is None:
        return "is not a core verb quackd knows (declare it with core: false)"
    missing: list[str] = []
    if req.camera and "camera" not in manifest.sensors:
        missing.append("a camera")
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
            "camera itself.",
            go_to,
            GoToParams,
            timeout_s=70,
            kind="composite",
            core=True,
            done_condition="within stop_distance of the target, the target was lost, or timed out.",
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
            "go_to a target, then run another verb (kick, grab).",
            approach_and,
            ApproachAndParams,
            timeout_s=90,
            kind="composite",
            core=True,
        ),
    )
}
