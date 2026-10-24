"""A ToddlerBot's own verbs: a neck, a stand, a keyframe and maybe a gripper.

The first full humanoid in quackd, and the manifest is mostly a list of things it will not be
asked to do. Three absences drive it, and all three are upstream's rather than ours.

There is no text to speech anywhere at this pin, so `say` does not exist. There is no walk
policy in the repository - the checkpoint comes from a wandb artifact nobody publishes - so
`move`, `go_to` and `approach_and` exist only when the daemon reports one staged. And there is
no `reset` in the whole sim package, so quackd builds `stand` itself out of upstream's own
default pose, reached at upstream's own reset rate, with upstream's own waist rule first.

What is deliberately not here is a raw joint verb. The body is in multi-turn mode with the
firmware position limits off, `set_motor_target` clamps nothing at all, and nothing reports a
current limit to Python. Handing thirty unclamped radians to a language model on that machine
is the exact failure this project exists to prevent, so the daemon owns every trajectory and
quackd offers named moves instead.
"""

from __future__ import annotations

import math
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, create_model

from quackd.transport.base import DuckState, Intent
from quackd.verbs.core import (
    SearchScanParams,
    _see,
    detector_failed,
    gaze_sweep_yaws,
    send_or_fail,
)
from quackd.verbs.registry import NoParams, Precondition, Verb, VerbContext, VerbResult

#: The motions that ship as keyframes in the repository, and therefore the only motion that
#: works with no downloads at all (`upstream_api.MOTION_KEYFRAMES`).
SHIPPED_MOTIONS: tuple[str, ...] = (
    "cartwheel",
    "crawl",
    "cuddle",
    "hold",
    "kneel",
    "pull_up_grasp",
    "pull_up_pull",
    "push_up",
    "walk_zmp",
)

#: What quackd will actually let a model ask for, and why the rest are not here.
#:
#: `pull_up_grasp` and `pull_up_pull` need a bar the robot is hanging from, so asking for one
#: on a robot standing on a table is a fall. `walk_zmp` is a gait reference trajectory rather
#: than a performance, and locomotion belongs to `move` where the deadman covers it.
#: `cartwheel` is excluded on judgement: this body has no fall recovery, and a cartwheel is
#: not something to discover an LLM can trigger. None of these is a capability claim - the
#: robot can do all of them - so the docs say plainly that quackd curates this list.
MOTIONS: tuple[str, ...] = ("hold", "kneel", "cuddle", "push_up", "crawl")

NECK_FRACTION = 0.8
NECK_YAW_LIMIT_DEG = 90.0
"""How far the head sweeps either side of centre, matching `LookParams.yaw_deg`."""
"""How much of the neck's own range `look` will use. The range is not what breaks a neck
linkage; a step command arriving from a network is, so the daemon also rate limits."""
LOOK_S = 1.2
STAND_TIMEOUT_S = 20.0
PERFORM_TIMEOUT_S = 45.0
GRIP_S = 1.5


class LookParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    yaw_deg: float = Field(
        default=0.0,
        ge=-90.0,
        le=90.0,
        description="Turn the head left (positive) or right, in degrees from centre.",
    )
    pitch_deg: float = Field(
        default=0.0,
        ge=-45.0,
        le=45.0,
        description="Tilt the head up (positive) or down, in degrees from centre.",
    )


class PerformParams(BaseModel):
    """Every motion upstream ships. Also the type `perform` is written against: a narrowed
    build returns a model of the same shape, so what the function reads off it is unchanged."""

    model_config = ConfigDict(extra="forbid")

    motion: Literal["hold", "kneel", "cuddle", "push_up", "crawl"] = Field(
        ...,
        description=(
            "Which shipped motion to play. These are recorded keyframes, not balanced "
            "policies: the robot needs clear space and a flat surface."
        ),
    )


def perform_params(motions: tuple[str, ...] = MOTIONS) -> type[BaseModel]:
    """`perform`'s parameters, narrowed to the motions this build actually loaded.

    The enum used to be the fixed five above while the verb's *description* named only what
    the daemon reported, so a robot with two keyframes still advertised five in its tool
    schema. A pilot that reads the schema rather than the prose asks for a motion this robot
    has not got, pydantic accepts it because the schema said it was legal, and the refusal
    arrives from the daemon a step later. Now the enum is built from the same tuple the
    sentence is, so the two cannot disagree."""
    if tuple(motions) == MOTIONS:
        return PerformParams
    # A Literal built at run time from what the daemon reported, which no type checker can read.
    motion: Any = Literal[motions]
    return create_model(
        "PerformParams",
        __config__=ConfigDict(extra="forbid"),
        motion=(motion, Field(..., description=PerformParams.model_fields["motion"].description)),
    )


class GripParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    side: Literal["left", "right", "both"] = Field(default="right")
    close: bool = Field(default=True, description="True closes the gripper, False opens it.")


# ── preconditions the manifest references by name ───────────────────────────────────────


def _link_fresh(state: DuckState) -> str | None:
    """The daemon owns the fifty hertz loop, so quackd going quiet is not what stops this
    robot: the daemon's own deadman is. What quackd must not do is claim a move it never
    delivered, so a verb refuses when the daemon has stopped answering."""
    stale_ms = state.extras.get("stale_ms")
    if stale_ms is None:
        return None
    limit = float(state.extras.get("stale_limit_ms") or 1000.0)
    if float(stale_ms) <= limit:
        return None
    return (
        f"no state from the robot's daemon for {float(stale_ms):.0f} ms (limit {limit:.0f}): "
        "it is not answering. Its own deadman will have slewed the robot to a safe pose and "
        "be holding it there, so the robot is standing, not falling"
    )


def _not_fallen(state: DuckState) -> str | None:
    """There is no fall recovery on this robot and no get-up policy in the repository, so the
    refusal names no verb: a human has to stand it up. Saying otherwise would invite a model
    to thrash a fallen humanoid against the floor."""
    if not state.fallen:
        return None
    return (
        "the robot has fallen and cannot get itself up: there is no recovery policy for this "
        "body. A human needs to stand it up and check it before anything else is asked"
    )


def _calibrated(state: DuckState) -> str | None:
    """Zero calibration is per-instance, gitignored and absent from a fresh clone, so an
    uncalibrated robot's every commanded angle is offset by however it was assembled."""
    if state.extras.get("calibrated", True):
        return None
    return (
        "this robot has no zero calibration on it, so every commanded angle means something "
        "different from what it says. Run upstream's calibrate_zero on the robot first"
    )


def toddlerbot_conditions() -> dict[str, Precondition]:
    return {"link_fresh": _link_fresh, "not_fallen": _not_fallen, "calibrated": _calibrated}


# ── the verbs ───────────────────────────────────────────────────────────────────────────


def look_point(yaw_deg: float, pitch_deg: float) -> tuple[float, float, float]:
    """quackd's `look` intent carries a direction, not two angles.

    Every other gaze body in this repository reads it with `atan2`, and core's own
    `search_scan` gaze sweep sends `Intent.look(cos, sin, 0)`. This robot's *verb* takes
    degrees, because two neck servos are what it has, so the conversion belongs here.
    Putting degrees straight into y and z made a 45 degree sweep arrive as 0.7 degrees.
    """
    yaw, pitch = math.radians(yaw_deg), math.radians(pitch_deg)
    return (
        math.cos(yaw) * math.cos(pitch),
        math.sin(yaw) * math.cos(pitch),
        math.sin(pitch),
    )


def look_degrees(x: float, y: float, z: float) -> tuple[float, float]:
    """The inverse, for whoever has to turn it back into two servo angles."""
    return (
        math.degrees(math.atan2(y, x)),
        math.degrees(math.atan2(z, math.hypot(x, y))),
    )


async def look(ctx: VerbContext, p: LookParams) -> VerbResult:
    """Point the head. The daemon interpolates and rate limits; quackd sends a target."""
    x, y, z = look_point(p.yaw_deg, p.pitch_deg)
    intent = Intent.look(x=x, y=y, z=z)
    if (fail := await send_or_fail(ctx, intent)) is not None:
        return fail
    await ctx.transport.sleep(LOOK_S)
    state = await ctx.transport.get_state()
    neck = state.extras.get("neck", {})
    return VerbResult.success(
        f"looking {p.yaw_deg:.0f} degrees {'left' if p.yaw_deg >= 0 else 'right'}, "
        f"{abs(p.pitch_deg):.0f} {'up' if p.pitch_deg >= 0 else 'down'}",
        yaw_deg=p.yaw_deg,
        pitch_deg=p.pitch_deg,
        neck=neck,
    )


async def stand(ctx: VerbContext, _: NoParams) -> VerbResult:
    """Slew to the default standing pose and hold it.

    This is quackd's own verb: there is no reset anywhere in upstream's sim package. The
    daemon does the work, at upstream's reset rate and with its waist rule first, because a
    position command on this body is a full-torque snap rather than a request."""
    if (fail := await send_or_fail(ctx, Intent.do("stand"))) is not None:
        return fail
    t0 = ctx.transport.now()
    while ctx.transport.now() - t0 < STAND_TIMEOUT_S:
        await ctx.transport.sleep(0.2)
        state = await ctx.transport.get_state()
        if state.fallen:
            await ctx.transport.stop()
            return VerbResult.fail("the robot fell while standing up; a human is needed")
        if state.posture == "standing" and not state.extras.get("moving", False):
            return VerbResult.success("standing in the default pose")
    await ctx.transport.stop()
    return VerbResult.fail(f"did not reach the standing pose in {STAND_TIMEOUT_S:.0f}s")


async def perform(ctx: VerbContext, p: PerformParams) -> VerbResult:
    """Play one of the motions that ship with the robot.

    These are recorded keyframes rather than balanced policies, so the robot follows them
    open loop and needs clear space. The daemon still clamps and rate limits every frame."""
    if (fail := await send_or_fail(ctx, Intent.do(f"motion:{p.motion}"))) is not None:
        return fail
    t0 = ctx.transport.now()
    while ctx.transport.now() - t0 < PERFORM_TIMEOUT_S:
        await ctx.transport.sleep(0.25)
        state = await ctx.transport.get_state()
        if state.fallen:
            await ctx.transport.stop()
            return VerbResult.fail(f"the robot fell during {p.motion}; a human is needed")
        if not state.extras.get("moving", False):
            return VerbResult.success(f"performed {p.motion}", motion=p.motion)
    await ctx.transport.stop()
    return VerbResult.fail(f"{p.motion} did not finish in {PERFORM_TIMEOUT_S:.0f}s")


async def grip(ctx: VerbContext, p: GripParams) -> VerbResult:
    """Open or close a hand. Only the gripper builds have these two extra motors."""
    sides = ["left", "right"] if p.side == "both" else [p.side]
    intent = Intent(kind="gripper", params={"open": not p.close, "side": p.side})
    if (fail := await send_or_fail(ctx, intent)) is not None:
        return fail
    await ctx.transport.sleep(GRIP_S)
    state = await ctx.transport.get_state()
    what = "both hands" if p.side == "both" else f"the {p.side} hand"
    return VerbResult.success(
        f"{what} {'closed' if p.close else 'opened'}",
        side=p.side,
        close=p.close,
        holding=state.extras.get("holding", {}),
        sensed=False,
        sides=sides,
    )


async def search_scan(ctx: VerbContext, p: SearchScanParams) -> VerbResult:
    """Sweep the head, never the body, and wait for the head to arrive before looking.

    Two things differ from the shared verb.

    `scan_mode` turns any robot that has both mobility and the twist intent, which is the
    right answer for a duck and the wrong one here: with a walk checkpoint staged this body
    is mobile, so the shared verb would pirouette a fall-prone 3 kg humanoid to look for a
    ball, and there is no get-up policy if it goes over. The neck is right there.

    And the shared sweep waits `TICK_S` between commanding a gaze angle and taking the frame,
    which is a tenth of a second. This robot's daemon rate limits every joint, the neck
    included, so a 45 degree step takes the better part of a second to arrive. Looking after
    a tenth of it means photographing where the head used to be and reporting the ball
    missing, so this waits the same `LOOK_S` the `look` verb waits.

    The manifest declares `search_scan` only when there is both a camera and a neck, so what
    this needs always exists by the time it runs.
    """
    state = await ctx.transport.get_state()
    centre = float(state.extras.get("head_yaw_deg") or 0.0)
    yaws = gaze_sweep_yaws(centre, p.step_deg, p.max_steps, NECK_YAW_LIMIT_DEG)
    for i, yaw in enumerate(yaws):
        x, y, z = look_point(yaw, 0.0)
        await ctx.transport.send_intent(Intent.look(x=x, y=y, z=z))
        await ctx.transport.sleep(LOOK_S)  # the neck is rate limited; let it get there
        img, hits = await _see(ctx, p.target, f"search_scan gaze {yaw:+.0f}")
        if img is None:
            return VerbResult.fail("this transport has no camera")
        if (failed := await detector_failed(ctx, "search_scan", "the sweep stopped")) is not None:
            return failed
        if hits:
            best = hits[0]
            return VerbResult.success(
                f"{p.target} found: {best.summary()} (gaze {yaw:+.0f} degrees)",
                detections=[d.model_dump() for d in hits],
                steps=i,
                gaze_yaw_deg=yaw,
            )
    span = (len(yaws) - 1) * p.step_deg
    return VerbResult.fail(
        f"{p.target} not found in a gaze sweep of {len(yaws)} looks ({span:.0f} degrees)"
    )


def toddlerbot_verbs(
    *,
    neck: bool = True,
    gripper: bool = False,
    motions: tuple[str, ...] = MOTIONS,
) -> dict[str, Verb]:
    """Every argument is what the daemon reported this build actually has.

    `motions` is the list it managed to load. A robot whose keyframe files are missing or
    unreadable gets no `perform` at all rather than a verb that refuses every motion: a
    verb that is not in the manifest does not exist."""
    verbs: list[Verb] = [
        Verb(
            "stand",
            "Slew to the default standing pose and hold it. Slow and deliberate: this robot "
            "moves to a pose rather than snapping to it.",
            stand,
            NoParams,
            timeout_s=STAND_TIMEOUT_S + 10,
            safety_class="confirm",
        ),
    ]
    if motions:
        verbs.append(
            Verb(
                "perform",
                "Play one of the motions this robot has loaded: "
                + ", ".join(motions)
                + ". It needs clear space and a flat surface.",
                perform,
                perform_params(motions),
                timeout_s=PERFORM_TIMEOUT_S + 10,
                safety_class="confirm",
            )
        )
    if neck:
        verbs.append(
            Verb(
                "search_scan",
                "Look around for something by sweeping the head, without turning the body.",
                search_scan,
                SearchScanParams,
                timeout_s=LOOK_S * 20 + 10,
                safety_class="safe",
            )
        )
        verbs.append(
            Verb(
                "look",
                "Point the head: yaw is left and right, pitch is up and down, both in degrees "
                "from centre. The body does not move.",
                look,
                LookParams,
                timeout_s=10,
            )
        )
    if gripper:
        verbs.append(
            Verb(
                "grip",
                "Open or close a hand. Say which side: left, right, or both.",
                grip,
                GripParams,
                timeout_s=10,
            )
        )
    return {v.name: v for v in verbs}


def neck_limits(motor_limits: dict[str, Any] | None) -> dict[str, tuple[float, float]]:
    """The neck's own travel, shrunk to a fraction of it.

    Which neck motor is yaw and which is pitch is inferred from the motor names rather than
    stated anywhere upstream, so this is an assumption, and it is named as one in the
    adapter's own list of them."""
    limits: dict[str, tuple[float, float]] = {}
    for name, span in (motor_limits or {}).items():
        if "neck" not in name:
            continue
        try:
            lo, hi = float(span[0]), float(span[1])
        except (TypeError, ValueError, IndexError):
            continue
        limits[name] = (lo * NECK_FRACTION, hi * NECK_FRACTION)
    return limits


__all__ = [
    "GRIP_S",
    "MOTIONS",
    "NECK_FRACTION",
    "SHIPPED_MOTIONS",
    "GripParams",
    "LookParams",
    "PerformParams",
    "look_degrees",
    "look_point",
    "neck_limits",
    "perform_params",
    "toddlerbot_conditions",
    "toddlerbot_verbs",
]
