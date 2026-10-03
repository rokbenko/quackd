"""An arm's own verbs: joints, a gripper, place, and pick and manipulate as a LeRobot policy.

The thesis holds here too: `pick` and `manipulate` are each one skill intent, and the robot's
own controller (a LeRobot policy) moves the arm; quackd never writes a grasp control law.
`place` is the one thing that needs no policy: open the gripper where it is. Joint names are
the SO-101 follower's six motors, verified upstream (`upstream_api.SO_MOTORS`).

Every verb that moves a joint watches the measurement rather than timing the travel. Two
upstream facts make that necessary rather than tidy. One `send_action` moves a joint at most
the configured step (`upstream_api.SO_ACTION_CLAMP`), so a goal takes as many sends as it
takes, and the same cap applies to the gripper in its own 0..100 units. And nothing reports
whether a goal was reached, so an arm that stalled against an obstacle and an arm that
arrived look identical unless somebody compares the goal with the position.

`move_joints` also paces its travel, because the step cap is a ceiling on speed and never a
speed anybody chose. Re-sending the final goal every tick moves every joint at the cap, which
made "move it slowly" a request the arm could not honour, and a model on the bench read the
verb's text correctly and declined. So the goal it sends walks from where the arm is to where it
was asked to be across `duration_s` (`_drive`), and the cap only decides when a move asked to
be quicker than the arm allows ends later than asked.
"""

from __future__ import annotations

import asyncio
import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from quackd.adapters.base import CONNECTING_TAKES_TORQUE_OFF
from quackd.duckfile.schema import DEFAULT_SEGMENT_S, INSTRUCTION_MAX_CHARS, instruction_line
from quackd.transport.base import DuckState, Intent
from quackd.verbs.core import send_or_fail
from quackd.verbs.registry import NoParams, Precondition, Verb, VerbContext, VerbResult

JOINTS: tuple[str, ...] = (
    "shoulder_pan",
    "shoulder_lift",
    "elbow_flex",
    "wrist_flex",
    "wrist_roll",
    "gripper",
)
"""The SO-101 follower's motors, in bus order (upstream_api.SO_MOTORS)."""
GRIPPER_OPEN = 100.0
GRIPPER_CLOSED = 0.0

TICK_S = 0.1
"""How often a goal is re-sent while a verb runs, matching the core `move` verb's cadence."""
TOL_DEG = 5.0
"""Close enough, for a body joint. The arm's controller is P-only by default (I is 0), so a
loaded joint settles a little short of its goal and never closes the last degree."""
GRIPPER_TOL = 5.0
STALL_TICKS = 5
STALL_DEG = 0.5
"""No joint moved this far for this many ticks, and it is not going to."""
GRIPPER_S = 6.0
"""How long to give the gripper. The step cap applies to its 0..100 range too, so a full
open takes 100 divided by the step, times the tick."""

MOVE_MIN_S = 0.2
MOVE_MAX_S = 12.0
"""The shortest and the longest motion a pilot may ask `move_joints` for. The shortest is two
ticks, the least a goal can be walked across; the longest has to leave the move its settle
inside the executor's timeout (`MOVE_JOINTS_TIMEOUT_S`), and a test holds the two together."""
RAMP_DECIMALS = 1
"""A ramp's targets on the way are sent to a tenth of a degree (of a unit, on the gripper), the
resolution the arm reports its readings in. One encoder tick of the servo is 360/4096 of a
degree, so a finer target moves nothing further, and the record of a slow move is read by a
person: a column of goals like 0.5000000000000002 says nothing a tenth does not."""
RAMP_RESOLUTION = 10.0**-RAMP_DECIMALS
"""The step a ramp's targets are rounded to, and so the least it can walk a joint by. A goal
nearer than this to where the joint starts has no target between the two: a ramp would hold
the start and then send the goal, which is a move with nothing to walk."""
MOVE_SLACK_S = 2.0
"""Slack a move gets on top of the time its ramp and the step cap say it needs. The transport's
clock runs on between ticks, because every send and every read is a bus transaction with its
own latency, and a loaded joint on a P-only controller closes the last degrees of its gap more
slowly than it crossed the rest."""
MOVE_SETTLE_S = STALL_TICKS * TICK_S + MOVE_SLACK_S
"""How long a move is watched after its ramp has handed the servo the final goal: long enough
for a joint that has stopped to be called stalled (`STALL_TICKS` ticks), plus the slack."""
MOVE_JOINTS_TIMEOUT_S = 20.0
"""The executor's timeout for `move_joints`: past it the executor cancels the verb, stops the arm
itself and says only that the verb timed out. The verb's own budget is computed to end before
it (`move_budget_s`), so that a move that runs out of time is reported by the verb, with the
joint that fell short and where it stopped. One constant for both, so they cannot drift."""
MOVE_HEADROOM_S = 2.0
"""How far inside `MOVE_JOINTS_TIMEOUT_S` the verb's budget must end. The budget is checked
between ticks, and after it the verb still sends, reads and stops the arm; on a real bus each of
those is a call with its own deadline, and all of it has to land before the executor's clock
does."""

PICK_POLL_S = 0.5
PICK_SETTLE_S = STALL_TICKS * TICK_S
"""After the policy stops, how long to let the gripper come to rest before reading `holding`
one last time. `_holding` needs two gripper samples at least `SETTLE_GAP_S` apart that agree,
so the read that catches the policy going idle can be too early to see a grasp that is still
closing. This is the same window `_drive` calls a stall, which is this file's own definition
of a joint that has stopped moving, and it is comfortably wider than that gap."""


MANIPULATE_S = DEFAULT_SEGMENT_S
"""How long one `manipulate` segment runs, in the transport's time, unless something ends it
sooner, on a backend nobody has told otherwise: the task file's own default
(`quackd.duckfile.schema.DEFAULT_SEGMENT_S`), and nothing a policy declares. A run tells the
backend its task's `policy.segment_s` through `set_segment_s` (`quackd.duckfile.narrow`), and
the verb runs what the backend was told."""
MANIPULATE_TIMEOUT_S = 70.0
"""The executor's timeout for `manipulate` as the arm registers it, before a task narrows it to
its own segment. It covers the longest segment a task may ask for
(`quackd.duckfile.schema.SEGMENT_MAX_S`) and the headroom every segment is given past its own
seconds (`SEGMENT_HEADROOM_S`), as `pick` has ten seconds past its longest `max_s`, and a test
holds the three together."""

SegmentHow = Literal[
    "holding", "finished", "time", "chunks", "stall", "guard", "starved", "error", "refused"
]
"""The ways a policy segment ends (`SegmentEnd`)."""
RAN = frozenset({"time", "chunks", "stall"})
"""The endings of a segment that ran: `manipulate` is ok on these and on nothing else. It never
says the task was done, which only a fresh look at the arm can say."""
NO_INSTRUCTION = "manipulate needs an instruction: say in a few words what the policy is to do"
"""What every backend says to a `manipulate` with nothing but blanks for its instruction, the
same words on the bench, the simulator and the mock, so a rehearsal fails where the arm would."""


@dataclass(frozen=True)
class SegmentStats:
    """What a segment counted: its ticks, the ticks the pacer skipped, the chunks it took in, the
    ticks it had nothing to send, the chunks it threw away (another segment's, another tick's, or
    all for ticks already played), the goals clipped into the travel, and the ticks a second it
    achieved on the transport's clock. A report, and never a rate anything is set from."""

    ticks: int = 0
    skipped: int = 0
    chunks: int = 0
    starved: int = 0
    stale: int = 0
    clips: int = 0
    hz: float | None = None


@dataclass(frozen=True)
class SegmentEnd:
    """How a policy segment ended, as the loop that ran it says (`policy/loop.py`).

    `holding` is the gripper closed and settled on something, which is what `pick` is for.
    `finished` is the policy saying it was done and the grasp not settling on anything in the
    settle after. `time` is the segment's time run out, `chunks` its chunks played or a policy
    that said it was done, and `stall` the arm no longer moving under `manipulate`'s policy.
    `guard` is the loop ending the segment itself on a reading or an action it would not send,
    with the arm held where it is: `reason` says which. `starved` is a policy that gave nothing
    to send for too long, with the arm held too. `error` is the policy raising. `refused` is the
    segment's start ruling it out before anything was sent, which the `do` that started it turns
    into a refusal, so no verb ever waits on it. A segment stopped from outside, by a stop, a
    release or the close, ends as a cancelled task and has none of these."""

    how: SegmentHow
    reason: str
    stats: SegmentStats = field(default_factory=SegmentStats)


class MoveJointsParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    positions: dict[str, float] = Field(
        ...,
        description="Joint -> goal in degrees (the gripper in 0..100). Only the joints given move.",
    )
    duration_s: float = Field(
        default=5.0,
        ge=MOVE_MIN_S,
        le=MOVE_MAX_S,
        description=(
            "How long the motion should take, in seconds: every joint given travels from "
            "where it is to its goal across this time, so a longer time is a slower move. The "
            "arm never moves faster than its speed cap, so a time too short for a long move "
            "runs at the cap and takes as long as the cap needs. The verb waits until every "
            "joint has arrived."
        ),
    )

    @field_validator("positions")
    @classmethod
    def _known_joints(cls, value: dict[str, float]) -> dict[str, float]:
        if not value:
            raise ValueError("give at least one joint")
        unknown = sorted(set(value) - set(JOINTS))
        if unknown:
            raise ValueError(f"unknown joints {unknown}; this arm has {', '.join(JOINTS)}")
        for joint, goal in value.items():
            lo, hi = (0.0, 100.0) if joint == "gripper" else (-180.0, 180.0)
            if not lo <= goal <= hi:
                raise ValueError(f"{joint}={goal} is outside {lo}..{hi}")
        return value


class GripperParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    open: bool = Field(default=True, description="True opens the gripper, False closes it.")


class PickParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target: str = Field(
        default="object",
        max_length=INSTRUCTION_MAX_CHARS,
        description="What to pick, as the policy's task text.",
    )
    max_s: float = Field(default=20.0, ge=1, le=60, description="Give up after this long.")

    @field_validator("target")
    @classmethod
    def _one_line(cls, value: str) -> str:
        # the words `pick` hands the policy are held to what `manipulate`'s are, one line of
        # plain text and no longer (`instruction_line`). A blank one is left as it always was:
        # the backend tells a pick's policy its target and refuses no blank one
        if value.strip():
            instruction_line(value, what="target")
        return value


class ManipulateParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    instruction: str = Field(
        ...,
        min_length=1,
        max_length=INSTRUCTION_MAX_CHARS,
        description=(
            "One short subtask for the arm's learned policy, in the words it was trained on, "
            "such as 'pick up the red block'."
        ),
    )

    @field_validator("instruction")
    @classmethod
    def _said(cls, value: str) -> str:
        # blanks pass min_length and tell a policy nothing; every backend's `do` refuses them
        # too, for an intent that never came through here
        if not value.strip():
            raise ValueError(NO_INSTRUCTION)
        # and the pilot's own words are held to what a task file's list is, one line of plain
        # text and no longer (`instruction_line`), whether a task lists instructions or not
        instruction_line(value)
        return value


# ── preconditions the manifest references by name ───────────────────────────────────────


def _torque_on(state: DuckState) -> str | None:
    if state.extras.get("torque", True):
        return None
    return (
        "the arm's torque is off, so a goal would reach a limp servo. A servo that has tripped "
        "its own overload protection reads this way too, and so does an arm quackd released "
        "into somebody's hands for `--by-hand`, which it takes hold of again before the first "
        "turn. No verb can toggle torque either way"
    )


def _holding(state: DuckState) -> str | None:
    return None if state.holding else "nothing is held: pick something first"


def _not_hot(state: DuckState) -> str | None:
    """Only on the verbs that move the five body joints. LeRobot caps the gripper's torque
    and current and caps nothing else (`upstream_api.SO_BODY_HAS_NO_TORQUE_CAP`), so this is
    the only thing standing between a stalled elbow and a servo cooking itself. A backend
    that cannot read a temperature says nothing and is believed."""
    hot = [str(j) for j in state.extras.get("hot", [])]
    if not hot:
        return None
    temperatures: dict[str, Any] = state.extras.get("temperature_c", {})
    worst = max(hot, key=lambda joint: temperatures.get(joint, 0))
    reading = temperatures.get(worst, "?")
    return (
        f"{worst} reads {reading}°C: let the arm cool before moving it. The servo's own "
        "cut-off is 70°C and a joint that trips it goes slack without announcing it"
    )


def lerobot_conditions() -> dict[str, Precondition]:
    return {"torque_on": _torque_on, "holding": _holding, "not_hot": _not_hot}


# ── watching a goal arrive ──────────────────────────────────────────────────────────────


def _joints_of(state: DuckState) -> dict[str, float]:
    return {str(k): float(v) for k, v in dict(state.extras.get("joints", {})).items()}


def shortfall(
    goal: dict[str, float],
    joints: dict[str, float],
    recorded: dict[str, float] | None = None,
) -> str:
    """The joint furthest from where it was asked to be, in words.

    Public because the rest move says it too, and it reads from the transport rather
    than through a verb. With the recorded rest pose beside a reachable goal, a joint that is
    at rest by the half-line rule (`joint_at_rest`) is never the one named, however far it
    reads from its goal: an arm folded past its travel is not short of anything, and naming
    it would send somebody to look at the one joint that is fine."""
    behind = {k: abs(joints[k] - v) for k, v in goal.items() if k in joints}
    if not behind:
        return "the arm reported no joint positions"
    rec = recorded or {}
    short = {
        k: gap for k, gap in behind.items() if not joint_at_rest(goal[k], joints[k], rec.get(k))
    } or behind
    worst = max(short, key=lambda joint: short[joint])
    return f"{worst} is at {joints[worst]:.0f} with a goal of {goal[worst]:.0f}"


def _stall_threshold(ctx: VerbContext) -> float:
    """How little a joint may move per tick before it counts as stopped. Scaled to the step
    cap: an arm told to move half a degree a tick is moving at full speed at half a degree
    a tick, and calling that a stall would fail every legitimate move."""
    step = (ctx.manifest.limits.get("step_deg") if ctx.manifest is not None else None) or 0.0
    return min(STALL_DEG, step / 2) if step > 0 else STALL_DEG


def _travel(ctx: VerbContext) -> dict[str, Any]:
    """Each joint's calibrated travel as the connected arm's manifest publishes it, or nothing
    where it is not known (a manifest read before the arm answered)."""
    return (ctx.manifest.extras.get("joint_range_deg") if ctx.manifest is not None else None) or {}


def ramp_start(
    goal: dict[str, float], joints: dict[str, float], travel: dict[str, Any]
) -> dict[str, float]:
    """Where a move's ramp begins: each goal joint's reading, clipped into its travel.

    Clipped, because the servo clamps every goal it is written to the travel its calibration
    put in it (`upstream_api.POSITION_LIMITS_CLAMP_GOALS`), while a reading is not clamped. A
    joint folded or placed past its travel with torque off therefore reads outside it, and any
    goal between that reading and the limit is, to the servo, the limit. The first thing such a
    joint does under any move is rise to its limit at the servo's own speed, whatever quackd
    sends, and quackd cannot pace that stretch. So the ramp is paced from the limit, the first
    place quackd's goals mean what they say. It is also the only start the backend accepts: a
    target outside the travel is refused before it reaches the bus.

    A joint with no known travel starts from its reading as read. A goal joint the arm did not
    report has no start at all and is sent its goal whole, because a ramp from an unknown place
    is not a ramp."""
    start: dict[str, float] = {}
    for joint in goal:
        if joint not in joints:
            continue
        reading = joints[joint]
        span = travel.get(joint)
        if span:
            reading = min(float(span[1]), max(float(span[0]), reading))
        start[joint] = reading
    return start


def published_travel(lo: float, hi: float) -> list[float]:
    """A joint's travel as the manifest publishes it: to a tenth of a degree, rounded inward.

    Inward, never to nearest, because the published figure is a promise two readers act on.
    The pilot is told it as the travel, and asks for its ends; `move_joints` clips the start of
    a ramp into it. The backend refuses any goal outside the travel it computed exactly, and
    rounding to nearest can move an end outward by up to a twentieth of a degree: the pilot
    asks for the edge it was shown and is refused, and a ramp from a joint folded past its
    travel begins a hair outside it and is refused before it has moved. Inward, every published
    angle is one the backend takes.

    Each end is checked against the float it came from rather than trusted to the arithmetic,
    because that float is what the backend compares a goal with: ten times a float can round
    to a whole number the float itself lies a hair short of, and then the tenth it gives is a
    hair outside. Such an end moves one tenth further in. A travel published a tenth narrower
    costs nothing; one published a hair wider is the refusal this exists to prevent.

    Here rather than beside the manifest it publishes, because `range_refusal` prints a travel
    the same way and the manifest's module imports this one."""
    low = math.ceil(lo * 10.0)
    if low / 10.0 < lo:
        low += 1
    high = math.floor(hi * 10.0)
    if high / 10.0 > hi:
        high -= 1
    return [low / 10.0 + 0.0, high / 10.0 + 0.0]


def said_past(value: float, shown_lo: float, shown_hi: float) -> str:
    """A number outside a travel printed as `shown_lo..shown_hi`, as the sentence saying so
    prints it: to a tenth, unless a tenth would round it onto that travel, and then as it is.

    A value a few hundredths past an edge is outside it, and its tenth is the edge itself, so a
    sentence printing the tenth would name a number inside the range it says the value lies
    outside. The travel printed beside it is `published_travel`, rounded inward, which is never
    wider than the travel the value was judged against, so the value as it is always lies
    outside what is printed."""
    said = f"{float(value):.1f}"
    if shown_lo <= float(said) <= shown_hi:
        said = repr(float(value))
    return said


def range_refusal(goals: Mapping[str, float], travel: Mapping[str, Any]) -> str | None:
    """Why a joint goal cannot be sent, in the first joint's words, or None when every joint
    with a known travel has its goal inside it.

    One sentence for every place that refuses a goal outside the travel: both backends, against
    the travel they read off the calibration, and `move_joints`, against the travel the
    manifest published, before anything moves. LeRobot does not clamp a degrees goal and the
    servo clamps it silently (`upstream_api.POSITION_LIMITS_CLAMP_GOALS`), so a goal let through
    would be one the arm quietly stops short of. Joints in name order, so the same goals are
    always refused over the same joint.

    The numbers are to a tenth of a degree, the resolution the manifest publishes the travel
    in. They were whole degrees, and a whole degree can put a refused goal inside the range the
    same sentence names: a goal a hair past a whole-degree edge read as that edge, "outside" a
    range ending on the same number, and Python rounds a half to even, so a published edge
    ending in .5 on an even degree was printed a degree short of goals the arm took. The travel
    is printed rounded inward (`published_travel`), the promise the manifest makes, so on the
    verb's side it is the published travel exactly and on a backend's it is never wider than
    what that backend takes. The goal is printed to a tenth unless a tenth would round it onto
    the range it is refused from, and then as it was given (`said_past`): a goal a few
    hundredths past an edge is refused, and its tenth would be the edge itself."""
    for joint, goal in sorted(goals.items()):
        span = travel.get(joint)
        if not span:
            continue
        lo, hi = float(span[0]), float(span[1])
        if not lo <= float(goal) <= hi:
            shown_lo, shown_hi = published_travel(lo, hi)
            said = said_past(goal, shown_lo, shown_hi)
            return (
                f"{joint}={said} is outside this arm's calibrated range "
                f"{shown_lo:.1f}..{shown_hi:.1f}; LeRobot does not clamp a degrees goal, so "
                "quackd refuses it"
            )
    return None


def ramp_target(start: dict[str, float], goal: dict[str, float], share: float) -> dict[str, float]:
    """The goal to send `share` of the way along a ramp, for every joint of the goal.

    Straight-line interpolation per joint, so every joint starts together and arrives
    together, which is what one `duration_s` for several joints means. Each target on the way
    is rounded to `RAMP_DECIMALS` and then held between its start and its goal, so that neither
    the rounding nor floating point can carry it past either end. At the end of the ramp it is
    the goal exactly, never the goal plus the rounding of `start + gap * 1.0`, because a goal
    at the very edge of the travel must not be sent a hair past it and refused. A joint with no
    start is sent its goal whole (`ramp_start`)."""
    if share >= 1.0:
        return dict(goal)
    target: dict[str, float] = {}
    for joint, end in goal.items():
        begin = start.get(joint)
        if begin is None:
            target[joint] = end
            continue
        along = round(begin + (end - begin) * max(0.0, share), RAMP_DECIMALS)
        target[joint] = min(max(begin, end), max(min(begin, end), along))
    return target


def move_budget_s(distance: float, duration_s: float, step_deg: float | None) -> float:
    """How long `move_joints` watches a move before calling it out of time.

    The ramp asks for `duration_s`. The step cap is the ceiling on speed: one send moves a joint
    at most `step_deg`, one send goes out every `TICK_S`, so the furthest joint cannot cover
    `distance` in less than `distance / (step_deg / TICK_S)`. Whichever is longer is when the
    arm can first be there; the ramp is sent on schedule either way, and the servo simply lags
    behind a ramp faster than it may go, then closes the rest of the gap at the cap. After that
    the move gets `MOVE_SETTLE_S` to arrive or be called stalled.

    Bounded by `MOVE_HEADROOM_S` inside `MOVE_JOINTS_TIMEOUT_S`, so the verb always ends on its
    own verdict, with where the joint stopped, rather than on the executor's. A move the cap
    says needs longer than that (a step lowered through `QUACKD_LEROBOT_MAX_STEP_DEG`, a long
    reach) runs out of time and says how far it got. Where no step is known, as on the mock,
    whose goals land at once, the cap adds nothing."""
    cap_s = distance / (step_deg / TICK_S) if step_deg and step_deg > 0 else 0.0
    return min(max(duration_s, cap_s) + MOVE_SETTLE_S, MOVE_JOINTS_TIMEOUT_S - MOVE_HEADROOM_S)


async def _drive(
    ctx: VerbContext,
    goal: dict[str, float],
    intent_for: Callable[[dict[str, float]], Intent],
    *,
    budget_s: float,
    tolerance: Callable[[str], float],
    start: dict[str, float] | None = None,
    ramp_s: float = 0.0,
) -> tuple[dict[str, float], DuckState | None, str, str | None]:
    """Send a goal until the arm is there, stops moving, or the budget runs out.

    With a `start` and a `ramp_s`, the goal is walked there: each tick sends `intent_for` the
    target as far from `start` toward `goal` as the time gone is through `ramp_s`
    (`ramp_target`), and once `ramp_s` has passed it sends the goal itself until the arm
    arrives. Without them, as for the gripper, every tick sends the goal whole, as this always
    did. The step cap stays the ceiling throughout: LeRobot clips each send to within one step
    of where the joint is, so a ramp faster than the cap is a ramp the servo lags behind.

    Arrival and stalls are judged only once the final goal is what is being sent. Arrival,
    because the ramp's targets are not where the pilot asked the arm to be, and a move ended
    within tolerance of its goal while its goal was still some degrees on would leave the arm
    that far short. Stalls, because a slow ramp legitimately moves a joint less per tick than
    the stall threshold: several seconds for a few degrees is a fraction of a degree a tick, and
    that is the arm doing exactly what it was asked. A joint blocked mid-ramp is therefore
    found when the ramp ends, not before; what it pushes with meanwhile is what the step cap
    allows, the same as it always was.

    Returns the last joint reading, the last state, how it ended (`arrived`, `stalled`,
    `timeout` or `refused`) and a reason when it did not arrive. A failure stops the arm
    first: `stop` holds the present position and deliberately leaves the gripper's goal
    alone, so stopping mid-move never drops what is held."""
    started = ctx.transport.now()
    stall = _stall_threshold(ctx)
    ramping = bool(start) and ramp_s > 0
    joints: dict[str, float] = {}
    previous: dict[str, float] = {}
    state: DuckState | None = None
    still = 0
    while (elapsed := ctx.transport.now() - started) < budget_s:
        share = min(1.0, elapsed / ramp_s) if ramping else 1.0
        target = ramp_target(start or {}, goal, share)
        if (fail := await send_or_fail(ctx, intent_for(target))) is not None:
            await ctx.transport.stop()
            return joints, state, "refused", fail.summary
        await ctx.transport.sleep(TICK_S)
        state = await ctx.transport.get_state()
        joints = _joints_of(state)
        moved = [abs(joints[k] - previous[k]) for k in previous if k in joints]
        previous = {k: joints[k] for k in goal if k in joints}
        if share < 1.0:
            continue  # still on the ramp: neither arrived nor stalled yet
        error = {k: abs(joints[k] - v) for k, v in goal.items() if k in joints}
        if error and all(gap <= tolerance(joint) for joint, gap in error.items()):
            return joints, state, "arrived", None
        still = still + 1 if moved and max(moved) <= stall else 0
        if still >= STALL_TICKS:
            await ctx.transport.stop()
            return (
                joints,
                state,
                "stalled",
                f"{shortfall(goal, joints)}, and it has stopped moving",
            )
    await ctx.transport.stop()
    return joints, state, "timeout", f"{shortfall(goal, joints)} when the time ran out"


# ── the rest pose, shared by both backends ──────────────────────────────────────────────

REST_MARGIN_S = 2.0
"""Slack on top of the time the step cap says the move needs, for the reads between sends."""
REST_MIN_S = 2.0
REST_MAX_S = 30.0
"""A teardown is bounded. `QUACKD_LEROBOT_MAX_STEP_DEG` can be lowered until a long move
would take minutes, and an arm nobody is watching must not hold a run open that long."""

TORQUE_LEFT_ON = (
    "the arm is not at its rest pose ({why}), so torque was left on and it will not fall as it "
    f"stands: hold it first, because {CONNECTING_TAKES_TORQUE_OFF}, then run quackd robot "
    "release {name}, or quackd doctor --robot {name} to park it, or cut its power"
)
"""Said once, by whichever caller closed the arm. The transport records it and prints
nothing itself: a library that writes to a terminal has picked one, and quackd has four.

It names the three ways out, because an arm left holding itself up stays that way until
somebody does one of them, and the power switch was the only one it used to name: on the
bench of 2026-09-23 every run that got to its end finished there. `quackd robot release` takes
torque off where the arm stands while a person holds it; `quackd doctor --robot` connects,
tries the rest move again from wherever the arm now is, and lets go at the pose if it gets
there. Both are said with the name the arm was registered under, through `torque_left_on`,
because a command with the wrong name in it is a command that fails or, worse, reaches another
arm.

The hold comes before all three, and "it will not fall" is said of the arm as it stands and
no further. Both commands begin by connecting, and connecting takes torque off every motor
while LeRobot configures them (`CONNECTING_TAKES_TORQUE_OFF`), so an arm held up by torque
alone is limp for that moment whichever command reaches it. The line used to tie the hold to
the release alone and offer `doctor` as a separate route, which read as though the arm could
be left to hold itself while `doctor` connected to it: the one moment it cannot."""

UNNAMED = "NAME"
"""What `torque_left_on` and `rest_clip_note` say in place of a name nobody told the
transport. A placeholder a person can see is one, rather than a guess at the name that reads
like the right one."""


def torque_left_on(why: str, name: str | None) -> str:
    """`TORQUE_LEFT_ON` for this arm, with its registered name where the caller knew it.

    The name is the one the arm was built with (`make(robot_id=...)`), which is the registered
    name on every path quackd builds a registered arm on: a run, an MCP session, a flock,
    `doctor --robot` and the `robot` commands resolve a registered robot to a spec carrying its
    name. Anything that calls the backend directly, or names a bare spec such as
    `lerobot:real`, builds the arm with no name, and those get `NAME` rather than an id that
    may not be what the arm is registered under."""
    return TORQUE_LEFT_ON.format(why=why, name=name or UNNAMED)


TORQUE_KEPT_AFTER_REFUSAL = (
    "the arm is not at its rest pose ({why}) and the release did not take, so torque was left "
    "on and it will not fall as it stands: hold it and cut its power"
)
"""The close's line for an arm a person just asked to have released, and whose release was
refused: every motor still read torque on afterwards, or the arm did not answer before it.

`TORQUE_LEFT_ON` would be wrong here in the one clause that matters. It sends the person to
`quackd robot release`, which is the command that has just failed, or which is what the
end-of-run offer they just answered does, in the same output. So this names the one way out
the arm has not already refused, which is the switch, and the hold that has to come first."""


def released_by_the_close(at_rest: bool) -> str:
    """The close's line for an arm whose release was refused and which the close then let go.

    A close lets go of an arm at its rest pose, or of one with no rest pose recorded, by asking
    LeRobot's own `disconnect()` for the release that is upstream's default, and says nothing,
    because that is every session's ending. After a refused release it is not every session's
    ending: the person was just told the release did not take, and the same `Torque_Enable` 0
    then went out again with the disconnect, with nothing to read it back. What they were told
    has to match what quackd did, so it is said, with the one thing they can do if the servos
    ignored it a second time."""
    where = (
        "at the rest pose, as every close there does"
        if at_rest
        else "where the arm stands, as every close does on an arm with no rest pose recorded"
    )
    return (
        f"the release did not take, and the close then took torque off {where}, with nothing "
        "to read it back: hold the arm, and cut its power if it still holds itself up"
    )


TORQUE_UNKNOWN_AT_CLOSE = (
    "quackd cannot tell whether the arm is holding itself up ({why}), so it kept whatever "
    "torque the arm has: hold it, and cut its power"
)
"""The close's line for an arm that did not answer the read the close decides by.

`TORQUE_LEFT_ON` says the arm will not fall, which is a thing only an arm that answered can be
said to be doing. An arm that stopped answering is, as often as not, one whose servo supply was
cut at the switch, which is the stop `docs/concepts/safety.md` names, and that arm is limp; one
whose cable came out is still holding. The close cannot tell them apart, so it keeps what torque
there may be, which costs nothing on a limp arm, and says it does not know."""


def rest_goal(rest_pose: dict[str, float]) -> dict[str, float]:
    """The part of a recorded pose that is ever driven: the five body joints.

    The gripper is left out for the reason `_hold()` leaves it out. LeRobot writes only the
    keys it is given, so omitting it keeps whatever squeeze is already commanded, and a rest
    move that re-sent the gripper would open a hand that is holding something."""
    return {j: float(v) for j, v in rest_pose.items() if j in JOINTS and j != "gripper"}


Clip = tuple[str, float, float]
"""`(joint, recorded, reachable)`: a joint of the rest pose that lies past its travel."""


def reachable_rest_goal(
    rest_pose: dict[str, float], ranges: dict[str, tuple[float, float]]
) -> tuple[dict[str, float], tuple[Clip, ...]]:
    """The rest goal the servos can actually be driven to, and the joints it had to move.

    A pose is recorded off an arm that was folded by hand with torque off, and nothing stops a
    hand folding a joint past the travel its calibration recorded. A goal is a different
    matter. LeRobot's calibration writes each joint's travel into the servo itself as its two
    position limits, and the servo clamps every goal it is written to them
    (`upstream_api.POSITION_LIMITS_CLAMP_GOALS`). So a goal past the travel is never refused
    and never reached: the servo drives to the limit and stops there, and a rest move watching
    for the recorded angle watches a joint that will not come, stalls, and calls the arm lost.

    So each body joint of `rest_goal` is clipped into its travel from `ranges`, which is what
    `joint_ranges()` read off this arm's calibration, whichever end it is past and however
    many joints are. A joint with no known range passes through unchanged, because there is
    nothing to clip it to and inventing a range would be worse. The gripper is never in it,
    for `rest_goal`'s reason.

    The second value names every joint that was moved at all, as `(joint, recorded,
    reachable)` in the pose's own order. Callers decide which of them are worth saying
    (`worth_saying`); this one only records what happened."""
    goal: dict[str, float] = {}
    clipped: list[Clip] = []
    for joint, recorded in rest_goal(rest_pose).items():
        span = ranges.get(joint)
        if span is None:
            goal[joint] = recorded
            continue
        lo, hi = span
        reachable = min(hi, max(lo, recorded))
        goal[joint] = reachable
        if reachable != recorded:
            clipped.append((joint, recorded, reachable))
    return goal, tuple(clipped)


def worth_saying(clipped: tuple[Clip, ...]) -> tuple[Clip, ...]:
    """The clipped joints a person needs to hear about: those further past their travel than
    `TOL_DEG`. A joint clipped by less is parked inside the tolerance any reached pose is
    allowed to miss by, so its fold and its parked angle are the same pose as far as the rest
    move is concerned, and a sentence about it would be a sentence about nothing. The
    half-line rule in `joint_at_rest` still applies to it."""
    return tuple(c for c in clipped if abs(c[1] - c[2]) > TOL_DEG)


def _listed(items: list[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def rest_clip_note(clipped: tuple[Clip, ...], name: str | None = None) -> str | None:
    """What a clipped rest pose means, in the numbers of this arm, or None when nothing was.

    One sentence that the rest move's narrator, `doctor` and `quackd robot rest-pose` all say,
    so a person hears the same thing wherever they first meet it. It says what happens rather
    than what went wrong, because nothing did: the arm parks at the edge of its travel, torque
    is released there, and the joint is free to settle toward the fold on its own. And it says
    how to make the fold itself reachable, which is a calibration that saw the arm folded.

    The command in it carries the name the arm was registered under, for `torque_left_on`'s
    reason, and `NAME` where the caller built the arm without one."""
    if not clipped:
        return None
    if len(clipped) == 1:
        joint, recorded, reachable = clipped[0]
        said = (
            f"{joint} is recorded at {recorded:.0f} in the rest pose and this calibration lets "
            f"its servo be driven to {reachable:.0f} and no further, so it parks there"
        )
    else:
        where = _listed([f"{joint} at {recorded:.0f}" for joint, recorded, _ in clipped])
        limits = _listed([f"{reachable:.0f}" for _, _, reachable in clipped])
        said = (
            f"the rest pose records {where}, and this calibration lets their servos be driven "
            f"to {limits} and no further, so each parks there"
        )
    return (
        f"{said} and is let go of there, free to settle the rest of the way on its own. "
        "Calibrate again with the arm folded (lerobot-calibrate) and record the pose again "
        f"(quackd robot rest-pose {name or UNNAMED}) to make the fold reachable"
    )


TORQUE_COULD_NOT_BE_KEPT = (
    "the arm is not at its rest pose ({why}), and quackd could not keep torque on, so the "
    "arm was released where it stood: check whether it is still where you left it"
)
"""When the one seam that holds torque did not take.

`close()` keeps an arm up by writing a flag on LeRobot's config object just before the
disconnect that reads it. If that write raises, the disconnect does whatever the config already
held. A follower quackd built holds, because it is built asking to, but one handed in may still
carry upstream's default, and then the disconnect releases torque anyway. So `close()` reads
the flag back and says this where it still asks for the release, or cannot be read: the note
promising the opposite would be the worst line quackd could print, because somebody reads that
the arm is being held and walks away from an arm that is not."""

LIMP_IN_HAND = (
    "the arm is limp and in your hands ({why}): put it down before you let go of it, because "
    "nothing is holding it up"
)
"""When the run ends with the arm still released into a person's hands.

`--by-hand` takes torque off at the rest pose and takes hold again before the first turn, so
the only way to reach a close in this state is a run that ended in the gap between: a Ctrl-C
during the wait, a heartbeat that died, a `take_hold` the arm refused. Whoever is holding the
arm is the one reading this, and the opposite note, the one about torque being left on, would
tell them the arm is holding itself up while it hangs off their hand.

The other way here is on purpose: `quackd robot release`, and the offer a run makes when its
rest move missed, end every release they make with this line, because it is the right last
thing to tell somebody holding an arm with nothing else holding it up. Only where a read of the
torque register after the release found every motor off, though: a release some motors ignored
ends on `still_holding_in_hand`, and one nothing read back on `UNREAD_IN_HAND`."""


def still_holding_in_hand(holding: tuple[str, ...], why: str) -> str:
    """`LIMP_IN_HAND` for an arm limp in part: the last read of its torque register, after the
    release went out, found `holding` still on.

    `LIMP_IN_HAND` ends "nothing is holding it up", and here something is. Said as the last
    line to somebody holding the arm it tells them the one thing that is not so, over a joint
    that is still energised and stays energised past the close, which keeps whatever torque
    there is rather than drop it on a partly limp arm. So it names those joints, in the bus's
    order, and says that the switch is what lets go of them."""
    one = len(holding) == 1
    return (
        f"the arm is in your hands ({why}), but {_listed(list(holding))} still "
        f"{'reads' if one else 'read'} torque on and {'holds' if one else 'hold'}: keep hold of "
        f"the arm, put it down, and cut its power to let go of {'it' if one else 'them'}"
    )


UNREAD_IN_HAND = (
    "the arm is in your hands ({why}), but nothing read its torque back after the release went "
    "out, so it may be limp only in part: hold it as though nothing holds it, put it down, and "
    "cut its power to be sure"
)
"""The close's line for an arm in a person's hands whose release no torque read has answered
for: `LIMP_IN_HAND` and `still_holding_in_hand` each say what a read found, and here none did.

Two ways reach it. A release whose call raised part way, or that a Ctrl-C landed on while it
was on the wire, has gone out to some motors and maybe not to the rest, and upstream's
`disable_torque` writes one motor at a time and stops at the first write that fails
(`upstream_api.BUS_DISABLE_TORQUE`), so the motors after it keep their torque. And a release
that returned and whose read-back then failed, or found the torque register silent. Unless the
close's own read of the arm answers first, which on an arm with no rest pose it never makes,
and which a bus still wedged by the release refuses. "Nothing is holding it up" would then be a
sentence about motors nobody asked, over an arm that may have some still holding, and that arm
is also kept energised past the close. So this says the release went out, that nothing read it
back, and the one way to be certain, which is the switch. And it tells the person to hold the
arm as though it were limp, which is the reading that never drops one."""


LET_GO_TO_PLACE = "it was let go of for you to place and never taken hold of again"
"""`LIMP_IN_HAND`'s parenthesis after a `--by-hand` release, when nothing more specific is
known: the arm was released at its fold for somebody to lift and set a pose with."""

LET_GO_WHERE_IT_STOOD = "torque was taken off where it stood, because you asked for it"
"""`LIMP_IN_HAND`'s parenthesis after a release through the second door (`let_go(anywhere=
True)`). Not the shortfall from the rest pose, which is what the close would otherwise put
there: that sentence ends "nothing moved it there" when no rest move ran, which is false of an
arm a person is holding, and the person already knows where it stands, having just asked for
it to be let go of there."""

LIMP_AT_REST = (
    "the arm is limp at its rest pose, where it was let go of for you to place and where it "
    "still reads: it rests there with no torque, as it does at the end of every run"
)
"""The close's line for an arm a `--by-hand` release let go of at its rest pose and nothing
took hold of again, when the close's own read finds it at that pose by the half-line rule and a
read of the torque register found every motor off.

`LIMP_IN_HAND` says "put it down before you let go of it, because nothing is holding it up",
and here the arm is down. It is where a placement wait that ended unanswered or on a Ctrl-C
leaves an arm whose fold lies past its travel: the take-hold at the start of the teardown is
refused over the folded joint, and the close then ended the run telling somebody who may never
have touched the arm, lying in its fold, that it was in their hands. It is where Enter pressed
over that fold leaves it too, and the take-hold at Enter is refused in the same terms
(`unlifted_from_rest`), so no line before this one tells the person the arm is up and needs
holding. Only after the first door, which releases nowhere but the rest pose: an arm a person
asked to have released where it stood (`LET_GO_WHERE_IT_STOOD`) was released into their hands
on purpose, since `quackd robot release` and the end-of-run offer both tell them to hold it
before it goes, and it keeps the line for one. Nothing here claims what no read said: the pose
and the torque are both this close's own readings, and whether somebody lifted the arm and put
it back is not a thing quackd says."""

IN_HAND_NOT_MOVED = "the arm is in a person's hands, so it is not moved"
"""The rest move's refusal while the arm is in somebody's hands (`in_hand`), which writes
nothing to it. Past a hand-off the arm is only still in a hand because a take-hold was refused,
and then a rest move has nothing good to do. Over a limp arm it folds nothing: a limp servo
takes a goal into its register and does not move to it, and that goal is still there when
torque next comes on, which is the very stale goal the take-hold refused to leave. Over an arm
whose take-hold was refused after its torque write, which may be energised, it is a fold under
somebody's hands. An arm already at its rest pose is answered `already`, which it is, and is
still sent nothing."""

UNCONFIRMED_IN_HAND = (
    "the arm is in your hands, and quackd asked its motors for torque to take hold of it and "
    "nothing read back what they did, so it may hold itself up or be limp, all of it or part of "
    "it: keep hold of it as though it may move or drop, and cut its power to be sure"
)
"""The close's line for an arm in a person's hands whose take-hold was refused after its torque
write went out, when no read of the torque register has answered since: a register that did
not answer, or a call that raised with the write on the wire.

`UNREAD_IN_HAND` is the same silence after a release, and it tells the person to hold the arm as
though nothing holds it, which is the safe reading of a release: the motors it reached are
limp. After a torque write the motors it reached may be energised, and "nothing holds it" is
the claim the take-hold refused to confirm. So this says quackd does not know which, to hold
the arm against both, and the switch, which settles it. The close keeps whatever torque there
is, as every close of an arm in a hand does."""

HOLD_NOT_CONFIRMED = (
    "quackd asked its motors for torque to take hold of it, and the hold was refused"
)
"""`still_holding_in_hand`'s parenthesis when the joints it names read on after a take-hold's
torque write rather than after a release: `LET_GO_TO_PLACE` would say the arm was "never taken
hold of again" in the same sentence that names joints holding."""


def held_in_part(on: tuple[str, ...]) -> str:
    """Why a take-hold refused an arm whose read after the torque write found `on` holding and
    every other motor off: torque came back on in part, so the arm is still in a hand, and those
    joints hold in it.

    The motors are joined the way every other sentence here joins them (`_listed`). They used
    to be joined with commas and then run straight into ", and the rest of the arm still reads
    off", which with two of them read as three things on a list, the last of them the whole
    rest of the arm."""
    return (
        f"torque came back on only on {_listed(list(on))}, and the rest of the arm still reads off"
    )


def placed_past_travel(outside: Mapping[str, float], travel: Mapping[str, Any]) -> str:
    """Why `take_hold` left torque off: the body joints a person placed outside their
    calibrated travel, each with its reading and its travel in this arm's numbers, and what to
    do about it. Every joint in `outside` has its travel in `travel`, since that is how it was
    found to be outside it.

    It says why in the person's terms because the person is holding the arm and has just been
    told it would be taken from them. Nothing quackd can do keeps such a joint where it was
    put. A goal written where it is lies past the travel, and the servo clamps a goal past the
    travel to the end of it (`upstream_api.POSITION_LIMITS_CLAMP_GOALS`), so torque would pull
    the joint there. With no goal written, the servo keeps the last one it was given, which
    after a hand-off is the rest move's, written before the arm was lifted and possibly the far
    end of the travel from where it was placed, and whether torque coming on holds the joint
    where it is instead is unverified (the row the real backend's `take_hold` cites). Either way
    the joint may move with a hand on it.

    The sentence names the goal it means, the one written where the joint reads. It used to say
    the servo pulls "any goal written for that joint" to the end of its travel, and a goal
    inside the travel is not clamped at all: the clause after it is about exactly such a goal.

    It ends on the rule and not on a promise. It used to end "before the arm is taken hold of",
    which read as a take-hold still to come once the joint was moved, and nothing in the run
    that meets this refusal takes hold again: its teardown writes nothing to an arm in a hand
    (`LeRobotReal._hold`), so a person who moved the joint inside and waited would be waiting
    for nothing.

    Each reading is `said_past` beside `published_travel`, as the range refusal prints a goal,
    so a joint a hair past an edge is never named inside the travel the sentence gives."""
    joints = list(outside)
    one = len(joints) == 1
    its, them = ("its", "it") if one else ("their", "them")
    written = (
        "a goal written where that joint is lies"
        if one
        else "goals written where those joints are lie"
    )
    last = "the last goal it was given" if one else "the last goals they were given"
    return (
        f"{_read_outside(outside, travel)}, so quackd left torque off: {written} past {its} "
        f"travel and the servo would pull {them} to the end of {its} travel, and with none "
        f"written the servo may drive {them} to {last}, with a hand on the arm either way. "
        f"quackd takes hold of the arm only with {_listed(joints)} inside {its} travel"
    )


def unlifted_from_rest(outside: Mapping[str, float], travel: Mapping[str, Any]) -> str:
    """Why `take_hold` left torque off over an arm its own read found lying at its rest pose,
    by the half-line rule, with every motor off: the joints outside their travel are a fold
    recorded past it that nobody lifted the arm out of before pressing Enter.

    `placed_past_travel` is said to somebody holding a joint they put past its travel, and it
    is about that hand: what torque would do to the joint under it. Here there is no such hand
    to speak to. The arm lies where the placing release let it go, the same fold every run
    ends in, and telling the person it is in their hands and to keep hold of it tells them it
    is up and needs holding, which no read said. So this names each folded joint with its
    reading and its travel, in this arm's numbers and printed as `placed_past_travel` prints
    them, and says what quackd needs before it takes hold: every one of them lifted inside."""
    joints = list(outside)
    one = len(joints) == 1
    return (
        f"{_read_outside(outside, travel)}, and quackd takes hold of the arm only once "
        f"{_listed(joints)} {'is' if one else 'are'} lifted inside {'its' if one else 'their'} "
        "travel"
    )


def policy_past_travel(outside: Mapping[str, float], travel: Mapping[str, Any]) -> str:
    """Why a policy segment was not started: body joints reading further outside their
    calibrated travel than `real.OUT_OF_RANGE_DEG`, each with its reading and its travel,
    opening with the clause `placed_past_travel` opens with.

    The consequence is the segment's own rather than the take-hold's, because torque is on and
    nobody's hand is on the arm. A joint reading past its travel is left out of every goal a
    policy sends, since the one goal its servo would take there is the end of the travel and it
    would haul the joint to it (`LeRobotReal._hold` leaves it out for the same reason), so a
    policy started there could never move that joint. That far out, the arm and its
    calibration disagree, which is worth putting right before anything learned drives it."""
    joints = list(outside)
    it, its = ("it", "its") if len(joints) == 1 else ("them", "their")
    return (
        f"{_read_outside(outside, travel)}, so the policy was not started. A joint outside its "
        "travel is left out of every goal a policy sends, because the one goal its servo takes "
        f"there is the end of its travel, and the policy could never move {it}. Move "
        f"{_listed(joints)} inside {its} travel first"
    )


def _read_outside(outside: Mapping[str, float], travel: Mapping[str, Any]) -> str:
    """Each joint in `outside` with its reading, then the travel they are outside of: the
    opening clause of both take-hold refusals over joints outside their travel. Every joint
    in `outside` has its travel in `travel`, since that is how it was found to be outside it."""
    joints = list(outside)
    readings: list[str] = []
    spans: list[str] = []
    for joint in joints:
        span = travel[joint]
        lo, hi = published_travel(float(span[0]), float(span[1]))
        readings.append(f"{joint} reads {said_past(outside[joint], lo, hi)}")
        spans.append(f"{lo:.1f}..{hi:.1f}")
    its = "its" if len(joints) == 1 else "their"
    return f"{_listed(readings)}, outside {its} calibrated travel of {_listed(spans)}"


NO_DRIVABLE_JOINT = (
    "the recorded rest pose names no joint this arm drives ({named}). A pose is only kept "
    "for the five body joints ({drivable}), because the gripper is never re-sent: "
    "quackd robot rest-pose NAME re-reads it off the arm, or --clear forgets it"
)
"""Why a pose that survived the registry is still refused here.

The registry checks that a pose names a joint and that its angles are numbers; it does not
know this arm's motors, and nothing should teach it. So a hand-edited `robots.json` can name
`elbow` where the arm says `elbow_flex`, and that pose drives nothing. Refusing it is the
registry's own rule about a file that says something untrue: the alternative is an arm that
reports a rest pose, ignores it, and lets go where it stands."""


def drivable_rest_joints() -> tuple[str, ...]:
    """The joints a rest pose may name, for the refusal that lists them."""
    return tuple(j for j in JOINTS if j != "gripper")


def joint_at_rest(goal: float, reading: float, recorded: float | None = None) -> bool:
    """One joint of a rest pose, judged against its reachable goal.

    A joint whose recorded angle is its goal, which is every joint whose fold is inside its
    travel, is at rest within `TOL_DEG` of it, as it always was.

    A joint whose recorded angle lies past its goal was clipped (`reachable_rest_goal`), and
    for that one the rule is a half-line rather than a point: it is at rest anywhere from
    `TOL_DEG` short of its goal out past it, on the side the recorded angle lies. Below the
    floor that is `reading <= goal + TOL_DEG`, above the ceiling `reading >= goal - TOL_DEG`.
    The reason is what the servo does. It clamps every goal to its travel, so past the limit
    nothing quackd can send moves it there: the only ways a joint gets past it are settling
    with torque off and being placed there by hand, and that is what a fold is. A joint
    reading past its limit is therefore folded, never lost, and driving it "to rest" would
    haul it up to the limit and hold it there against its own weight. Which side is read off
    the sign of `recorded - goal`, so a fold past the ceiling works exactly like one past the
    floor."""
    if recorded is not None and recorded < goal:
        return reading <= goal + TOL_DEG
    if recorded is not None and recorded > goal:
        return reading >= goal - TOL_DEG
    return abs(reading - goal) <= TOL_DEG


def past_reach(goal: float, reading: float, recorded: float | None) -> bool:
    """The joint reads beyond its reachable goal, on the side its recorded angle lies.

    That goal is the servo's limit, so writing it to this joint is writing a goal the servo
    moves *away* from the fold to reach: the rest move leaves such a joint out of what it
    sends rather than haul a folded arm up. A joint whose recorded angle is its goal is never
    past it, and a joint exactly at its goal is not past it either: that goal moves nothing."""
    if recorded is None:
        return False
    return (recorded < goal and reading < goal) or (recorded > goal and reading > goal)


def at_rest(
    goal: dict[str, float],
    joints: dict[str, float],
    recorded: dict[str, float] | None = None,
) -> bool:
    """Every joint of the goal is reported, and every one of them is at rest.

    `goal` is the reachable goal and `recorded` the pose as it was recorded; without it every
    joint is judged by the point rule, which is also what a pose inside its travel gets with
    it. `joint_at_rest` is the rule. A joint the arm did not report is never at rest."""
    if not goal or any(j not in joints for j in goal):
        return False
    rec = recorded or {}
    return all(joint_at_rest(v, joints[j], rec.get(j)) for j, v in goal.items())


def rest_budget_s(distance_deg: float, step_deg: float) -> float:
    """How long to give the rest move: the travel at the step cap, plus slack, bounded.

    One `send_action` moves a joint at most the step cap and they go out every `TICK_S`, so
    the fastest the arm can cross a gap is that distance divided by that rate."""
    rate = max(step_deg, 0.01) / TICK_S
    return min(REST_MAX_S, max(REST_MIN_S, distance_deg / rate + REST_MARGIN_S))


# ── the verbs ───────────────────────────────────────────────────────────────────────────


def _past_travel(ctx: VerbContext, extras: dict[str, Any], joints: dict[str, float]) -> list[str]:
    """One clause per joint the arm reports outside its calibrated travel.

    A pilot handed a joint reading beyond the end of the travel line in its prompt has been
    handed a contradiction, and a careful one refuses to move an arm whose state it cannot
    explain: on the bench one did exactly that. The explanation is the servo's: its goals are
    clamped to the travel, and its readings are not, so an arm folded or placed with torque
    off can read past the end. The clause says that much and no more. It does not say how the
    joint got there, because `out_of_range` carries a margin, and a joint the servo parked at
    its limit that then sagged under its own weight qualifies too.

    The travel is the connected arm's, from the manifest; where it is not known the clause
    says only that the joint reads outside it."""
    ranges = _travel(ctx)
    said = []
    for name in extras.get("out_of_range") or []:
        joint = str(name)
        reading = joints.get(joint)
        if reading is None:
            continue
        span = ranges.get(joint)
        if not span:
            said.append(f"{joint} reads {reading:.0f}, outside its calibrated travel")
            continue
        lo, hi = float(span[0]), float(span[1])
        limit = lo if reading < lo else hi
        said.append(
            f"{joint} reads {reading:.0f}, past the {limit:g} its servo can be driven to; "
            "goals are still limited to its travel"
        )
    return said


async def report_state(ctx: VerbContext, _: NoParams) -> VerbResult:
    """What this arm knows, in the sentence rather than in the data.

    A pilot reads a verb's summary text and never its data: the dump goes to the transcript
    and to an MCP client, and the observation the model is handed carries the summary. The
    core verb's summary is a posture and a policy name, which on a bolted-down arm is two
    facts it has not got and none of the four it has. So this one says them."""
    state = await ctx.transport.get_state()
    extras = state.extras
    joints = _joints_of(state)
    where = ", ".join(f"{name} {value:.0f}" for name, value in joints.items())
    torque = "torque on" if extras.get("torque", True) else "TORQUE OFF"
    temperatures = {str(k): float(v) for k, v in (extras.get("temperature_c") or {}).items()}
    if hot := [str(joint) for joint in extras.get("hot", [])]:
        heat = f"TOO HOT TO MOVE: {', '.join(hot)}"
    elif temperatures:
        hottest = max(temperatures, key=lambda joint: temperatures[joint])
        heat = f"hottest {hottest} {temperatures[hottest]:.0f}°C"
    else:
        heat = "no temperature reported"
    held = "holding something" if state.holding else "holding nothing"
    parts = [where or "no joints reported", torque, heat, held]
    parts += _past_travel(ctx, extras, joints)
    # a camera earns a clause only when a read has actually failed. Not when it is merely
    # unread: `ok` is false until the first frame, and a camera that opened and has not been
    # asked yet is not news. A working one is already in every observation as detections,
    # and a dead one is otherwise silent on a run that cannot call `observe`.
    camera = extras.get("camera")
    if isinstance(camera, dict):
        # with several cameras each is named, because "CAMERA DOWN" over two views does not
        # say which eye closed, and the arm keeps working with the other one
        rows = camera.get("cameras")
        if isinstance(rows, list):
            dead = [r for r in rows if isinstance(r, dict) and r.get("error")]
            if dead:
                named = "; ".join(f"{r.get('name')}: {r['error']}" for r in dead)
                parts.append(f"CAMERA DOWN: {named}")
        elif camera.get("error"):
            parts.append(f"CAMERA DOWN: {camera['error']}")
    return VerbResult.success("; ".join(parts), state=state.model_dump())


def _joint_tolerance(joint: str) -> float:
    return GRIPPER_TOL if joint == "gripper" else TOL_DEG


async def move_joints(ctx: VerbContext, p: MoveJointsParams) -> VerbResult:
    """Walk the joints given to their goals across `duration_s`, then wait for them to arrive.

    The arm is read once first, for where each joint's ramp starts (`ramp_start`) and for how
    far the furthest one has to go, which is what the budget is made of (`move_budget_s`).
    Every tick's intent is `Intent.joint(target, duration_s)`, so the record of each send still
    carries the time the pilot asked for.

    Every move is ramped, a small one included, and is judged arrived only once its ramp has
    finished, like any other. A move of a few degrees used to go out whole and be judged after
    one tick whenever every joint already read within the arrival tolerance of its goal, which
    made `duration_s`, the one thing the pilot is told about pace, untrue for any move that
    size, and with a lowered step cap left the joint a step along and reported it moved. The one
    move still sent whole is one with nothing to walk: every joint already within a ramp's
    resolution of its goal (`RAMP_RESOLUTION`), where a ramp could only hold the start and then
    send the goal.

    A goal outside the travel the pilot was shown, the manifest's published one, is refused
    here, before the arm is read or anything is sent, in the words the backend's own refusal
    uses (`range_refusal`). Ramped, the arm would travel to the edge first and be refused there,
    holding a pose nobody asked for. And sent whole for the backend to refuse, a goal in the
    sliver between the published edge, rounded inward, and the exact edge the backend checks
    was never refused: it went out whole, at the step cap, whatever `duration_s` said."""
    goal = dict(p.positions)
    travel = _travel(ctx)
    if (refusal := range_refusal(goal, travel)) is not None:
        return VerbResult.fail(f"move_joints: {refusal}", goal=goal)
    joints = _joints_of(await ctx.transport.get_state())
    start = ramp_start(goal, joints, travel)
    whole = all(abs(goal[joint] - begin) < RAMP_RESOLUTION for joint, begin in start.items())
    distance = max((abs(goal[joint] - begin) for joint, begin in start.items()), default=0.0)
    step = ctx.manifest.limits.get("step_deg") if ctx.manifest is not None else None
    joints, _state, _how, why = await _drive(
        ctx,
        goal,
        lambda target: Intent.joint(target, p.duration_s),
        budget_s=move_budget_s(distance, p.duration_s, step),
        tolerance=_joint_tolerance,
        start=start,
        ramp_s=0.0 if whole else p.duration_s,
    )
    if why is not None:
        return VerbResult.fail(f"move_joints: {why}", goal=goal, joints=joints)
    return VerbResult.success(
        "moved " + ", ".join(f"{k}={joints.get(k, v):.0f}" for k, v in goal.items()),
        goal=goal,
        joints=joints,
    )


async def _drive_gripper(
    ctx: VerbContext, *, open_: bool
) -> tuple[float | None, DuckState | None, str, str | None]:
    """The gripper is a joint like the others: the step cap moves it a few units per send,
    and where it stops is the whole of what quackd knows about holding something.

    It is not ramped. What it sends is `Intent.gripper(open)`, a yes or a no that each backend
    turns into its own fully open or fully shut, and that boolean is what the mock reads to
    decide whether the jaws closed on something; a ramp would have to invent the numbers in
    between. A gripper named in `move_joints` is a joint with a goal, and ramps like one."""
    goal = {"gripper": GRIPPER_OPEN if open_ else GRIPPER_CLOSED}
    joints, state, how, why = await _drive(
        ctx,
        goal,
        lambda _target: Intent.gripper(open_),
        budget_s=GRIPPER_S,
        tolerance=lambda _: GRIPPER_TOL,
    )
    return joints.get("gripper"), state, how, why


async def gripper(ctx: VerbContext, p: GripperParams) -> VerbResult:
    position, state, how, why = await _drive_gripper(ctx, open_=p.open)
    where = "" if position is None else f" (stopped at {position:.0f}/100)"
    if p.open:
        if how != "arrived":
            return VerbResult.fail(f"gripper did not open: {why}", open=True, position=position)
        return VerbResult.success(f"gripper open{where}", open=True, position=position)
    # closing on something is the one move that is supposed to stop short
    holding = bool(state is not None and state.holding)
    if how == "arrived" or (how == "stalled" and holding):
        grasped = "on something" if holding else "on nothing"
        return VerbResult.success(
            f"gripper closed {grasped}{where}", open=False, position=position, holding=holding
        )
    return VerbResult.fail(
        f"gripper did not close: {why}", open=False, position=position, holding=holding
    )


async def pick(ctx: VerbContext, p: PickParams) -> VerbResult:
    """One skill intent; the policy drives, and the segment it runs in is the backend's.

    The `do` intent carries `max_s`, because the loop is what ends a segment on its time: on a
    backend that runs one (`policy_segment`), the verb waits for that task and sleeps on no
    clock meanwhile. The loop is the one sleeper, which on the simulator is what lets time run
    for it, and it is also what reads `holding` each tick and ends the segment on it, so a
    grasp stops the policy the moment it settles rather than leaving it driving the arm through
    the pilot's thinking.

    The wait is `asyncio.wait`, never a bare `await` on the task. Stops arrive from other tasks,
    the heartbeat's, an MCP `stop`, a concurrent verb whose send is refused and which then stops
    the arm, and each cancels the segment first. Awaiting a task cancelled elsewhere raises
    `CancelledError` in the awaiting task, which would end this verb as cancelled rather than
    say what stopped it. A segment cancelled that way is a failed pick naming the stop, with no
    stop of its own: whatever cancelled it has already dealt with the arm, and a release or a
    close must not be followed by a hold. This verb's own cancellation, an executor timeout or
    an abort, cancels the segment on its way out.

    A backend with no segment task, the mock, whose scripted policy is done by the time its
    `do` is acknowledged, is watched by polling as `pick` always did (`_pick_polled`)."""
    intent = Intent(kind="do", params={"skill": f"policy:pick:{p.target}", "max_s": p.max_s})
    if (fail := await send_or_fail(ctx, intent)) is not None:
        return _unstarted(fail)
    t0 = ctx.transport.now()
    # read now, with nothing awaited since the acknowledgement: it is this pick's segment
    segment: asyncio.Task[SegmentEnd] | None = getattr(ctx.transport, "policy_segment", None)
    if segment is None:
        return await _pick_polled(ctx, p, t0)
    try:
        await asyncio.wait({segment})
    finally:
        if not segment.done():
            segment.cancel()
    seconds = round(ctx.transport.now() - t0, 1)
    if segment.cancelled():
        by = getattr(ctx.transport, "policy_stopped_by", None) or "something stopped the policy"
        return VerbResult.fail(
            f"pick {p.target!r} stopped: {by}", target=p.target, seconds=seconds, ended="stopped"
        )
    ended = segment.result()
    if ended.how == "holding":
        return VerbResult.success(
            f"picked {p.target}",
            target=p.target,
            seconds=seconds,
            ended=ended.how,
            timing=await _timing(ctx),
        )
    await ctx.transport.stop()
    held = ended.how in ("guard", "starved")
    said = "was stopped" if held else "did not end with something held"
    return VerbResult.fail(
        f"pick {p.target!r} {said}: {ended.reason}",
        target=p.target,
        seconds=seconds,
        ended=ended.how,
        error=ended.reason if ended.how == "error" else None,
        timing=await _timing(ctx),
    )


def _unstarted(refused: VerbResult) -> VerbResult:
    """A `pick` or a `manipulate` whose `do` was refused, which says it ran 0 s: no segment
    began, so nothing of `policy.total_s` is spent, however long the refusal took on the wall's
    clock. A reset that raises is refused once it has, and one that hangs once the start gives
    up on it (`policy.loop.RESET_S`), and without a word of its own the executor would charge
    that wait as the robot's clock across the call."""
    return VerbResult.fail(refused.summary, **{**refused.data, "seconds": 0.0})


async def _timing(ctx: VerbContext) -> Any:
    """How long the bus calls and the policy's ticks took (`extras.timing`), for the pick's
    record, or None when the arm will not say. A report and never a reason to fail the verb."""
    try:
        return (await ctx.transport.get_state()).extras.get("timing")
    except Exception:
        return None


async def _pick_polled(ctx: VerbContext, p: PickParams, t0: float) -> VerbResult:
    """`pick` on a backend that runs no segment task: poll `holding` until the time is up."""

    def picked() -> VerbResult:
        return VerbResult.success(
            f"picked {p.target}", target=p.target, seconds=round(ctx.transport.now() - t0, 1)
        )

    state = await ctx.transport.get_state()
    while ctx.transport.now() - t0 < p.max_s:
        await ctx.transport.sleep(PICK_POLL_S)
        state = await ctx.transport.get_state()
        if state.holding:
            return picked()
        if not str(state.policy).startswith("policy:"):
            # The policy has stopped and its last grasp may still be closing. `holding` is
            # inferred from the gripper coming to rest short of shut, which is only knowable
            # once two readings a real interval apart agree, so the read that caught the
            # policy going idle can be one sample too early. Give it one settle and look
            # again: without this a policy that grasps and finishes inside a single poll is
            # reported as a failed pick while the object is in the jaws.
            await ctx.transport.sleep(PICK_SETTLE_S)
            state = await ctx.transport.get_state()
            if state.holding:
                return picked()
            break  # the policy finished without a grasp
    await ctx.transport.stop()
    broke = state.extras.get("policy_error")
    why = f" (the policy raised {broke})" if broke else ""
    return VerbResult.fail(
        f"pick {p.target!r} did not end with something held{why}", target=p.target, error=broke
    )


async def manipulate(ctx: VerbContext, p: ManipulateParams) -> VerbResult:
    """One segment of the arm's learned policy, told `instruction`: the policy drives, the
    backend's loop paces it and holds it to the arm's rules, and the verb waits for it as `pick`
    does (`asyncio.wait`, a stop from elsewhere said as `stopped:`, its own cancellation passed
    on to the segment).

    The segment ends on its time (`segment_seconds`), on its chunks, on the arm no longer moving
    under the policy, on a starved policy, on a guard, or on a stop. The first three are a
    segment that ran and are ok, and a stall holds the arm where it stopped, since what stopped
    it may be in its way. The verb's own stop holds it again, as it does after every ending
    that held, and a stall whose last hold did not reach the arm is not ok. The others end with
    the arm held and are not ok either, so a run that keeps failing is still stopped by the
    executor's repeat-failure rule. Ok is never a claim that the task is done: nothing on this
    arm can tell, and the pilot judges it from a fresh look at the arm.

    A backend with no segment task, the mock, whose scripted policy has moved by the time its
    `do` is acknowledged, runs the segment on its own clock and is watched by polling
    (`_manipulate_polled`)."""
    max_s = segment_seconds(ctx.transport)
    intent = Intent(
        kind="do",
        params={"skill": f"policy:manipulate:{p.instruction}", "max_s": max_s},
    )
    if (fail := await send_or_fail(ctx, intent)) is not None:
        return _unstarted(fail)
    t0 = ctx.transport.now()
    # read now, with nothing awaited since the acknowledgement: it is this call's segment
    segment: asyncio.Task[SegmentEnd] | None = getattr(ctx.transport, "policy_segment", None)
    if segment is None:
        return await _manipulate_polled(ctx, p, t0, max_s)
    try:
        await asyncio.wait({segment})
    finally:
        if not segment.done():
            segment.cancel()
    seconds = round(ctx.transport.now() - t0, 1)
    told = p.instruction
    if segment.cancelled():
        by = getattr(ctx.transport, "policy_stopped_by", None) or "something stopped the policy"
        return VerbResult.fail(
            f"manipulate {told!r} stopped: {by}", instruction=told, seconds=seconds, ended="stopped"
        )
    ended = segment.result()
    stats = ended.stats
    data: dict[str, Any] = {
        "instruction": told,
        "seconds": seconds,
        "ended": ended.how,
        "chunks": stats.chunks,
        "clips": stats.clips,
        "hz": stats.hz,
        "skipped": stats.skipped,
        "starved": stats.starved,
        "timing": await _timing(ctx),
    }
    counts = _segment_counts(stats)
    if ended.how == "stall":
        # held again, as every ending the segment held is: its own hold may have lost a packet,
        # which a Feetech bus does, and the policy's last goal is past what stopped the arm
        await ctx.transport.stop()
        if missed := getattr(ctx.transport, "stop_error", None):
            return VerbResult.fail(
                f"manipulate {told!r} ended because {ended.reason}, and the last hold sent to "
                f"the arm did not reach it ({missed}), so the arm may still be pushing toward "
                "the policy's last goal: send stop, and look at the arm before the next step",
                **data,
            )
    if ended.how in RAN:
        return VerbResult.success(_ran(told, seconds, ended.reason, counts), **data)
    await ctx.transport.stop()
    if ended.how == "error":
        data["error"] = ended.reason
    return VerbResult.fail(f"manipulate {told!r} was stopped: {ended.reason} ({counts})", **data)


def _segment_counts(stats: SegmentStats) -> str:
    hz = "no ticks" if stats.hz is None else f"{stats.hz:g} ticks a second"
    return f"{stats.chunks} chunks, {stats.clips} goals clipped to the travel, {hz}"


def _ran(instruction: str, seconds: float, reason: str, counts: str | None) -> str:
    """What an ok `manipulate` says: that the segment ran and why it ended, and never that the
    task is done."""
    said = f" ({counts})" if counts else ""
    return (
        f"manipulate {instruction!r} ran {seconds:g} s and ended because {reason}{said}. "
        "Nothing on the arm says whether it did the task: look at it before the next step"
    )


def segment_seconds(transport: Any) -> float:
    """How long a `manipulate` segment runs on `transport`: what the run told it
    (`set_segment_s`), or `MANIPULATE_S` for one nobody told, or that says something that is not
    a finite number of seconds above 0."""
    said = getattr(transport, "segment_s", None)
    if isinstance(said, bool) or not isinstance(said, int | float):
        return MANIPULATE_S
    return float(said) if math.isfinite(said) and said > 0 else MANIPULATE_S


def checked_segment_s(seconds: Any) -> float:
    """`seconds` as a segment's length, for a backend's `set_segment_s`, or a ValueError that
    says why it is not one: a finite number of seconds above 0, and never a bool."""
    if isinstance(seconds, bool) or not isinstance(seconds, int | float):
        raise ValueError(f"segment_s={seconds!r} must be a number of seconds above 0")
    if not (math.isfinite(seconds) and seconds > 0):
        raise ValueError(f"segment_s={seconds!r} must be a number of seconds above 0")
    return float(seconds)


async def _manipulate_polled(
    ctx: VerbContext, p: ManipulateParams, t0: float, max_s: float
) -> VerbResult:
    """`manipulate` on a backend that runs no segment task, the mock: its segment runs until
    its `max_s` is up on the transport's clock, and the verb looks at it every `PICK_POLL_S`
    meanwhile. Something that ends it first, a stop, a release, a rest move or the next
    segment, is a failed `manipulate` naming it (`policy_stopped_by`), as on the arm, so a
    rehearsal fails where the arm would. The backend's state says whose segment is running,
    its policy by name and its `segment` number where it keeps one, which tells this segment
    from a later one told the same thing."""
    told = p.instruction
    name = f"policy:manipulate:{told}"
    ours = (await ctx.transport.get_state()).extras.get("segment")
    end = t0 + max_s
    while (left := end - ctx.transport.now()) > 0:
        await ctx.transport.sleep(min(PICK_POLL_S, left))
        if ctx.transport.now() >= end:
            break
        state = await ctx.transport.get_state()
        if state.policy != name or state.extras.get("segment") != ours:
            by = getattr(ctx.transport, "policy_stopped_by", None) or "something stopped the policy"
            return VerbResult.fail(
                f"manipulate {told!r} stopped: {by}",
                instruction=told,
                seconds=round(ctx.transport.now() - t0, 1),
                ended="stopped",
            )
    seconds = round(ctx.transport.now() - t0, 1)
    reason = f"its {max_s:g} s ran out"
    return VerbResult.success(
        _ran(told, seconds, reason, None), instruction=told, seconds=seconds, ended="time"
    )


async def place(ctx: VerbContext, _: NoParams) -> VerbResult:
    position, _state, how, why = await _drive_gripper(ctx, open_=True)
    if how != "arrived":
        return VerbResult.fail(f"place: the gripper did not open: {why}", position=position)
    return VerbResult.success("placed: gripper opened where the arm is", position=position)


def lerobot_verbs(*, policy: bool) -> dict[str, Verb]:
    verbs = [
        Verb(
            "report_state",
            "Report the arm: every joint in degrees, whether torque is on, how warm the "
            "servos are, and whether anything is held.",
            report_state,
            NoParams,
            timeout_s=5,
            read_only=True,
            core=True,
        ),
        Verb(
            "move_joints",
            "Move one or more joints to goal angles in degrees (gripper 0..100) over "
            "duration_s seconds, and wait for them to arrive.",
            move_joints,
            MoveJointsParams,
            timeout_s=MOVE_JOINTS_TIMEOUT_S,
            done_condition="every joint given is within a few degrees of its goal",
        ),
        Verb("gripper", "Open or close the gripper.", gripper, GripperParams, timeout_s=10),
        Verb(
            "place",
            "Release what is held by opening the gripper where the arm is.",
            place,
            NoParams,
            timeout_s=10,
        ),
    ]
    if policy:
        verbs.append(
            Verb(
                "pick",
                "Pick the target with the arm's own learned policy. Ends when something is "
                "held or the time is up.",
                pick,
                PickParams,
                timeout_s=70,
                safety_class="confirm",
            )
        )
        verbs.append(
            Verb(
                "manipulate",
                "Hand the arm to its own learned policy for one short subtask, told in a few "
                "words. Runs one segment and ends early if the arm stops moving. It never says "
                "the task is done: look at the arm afterwards to judge.",
                manipulate,
                ManipulateParams,
                timeout_s=MANIPULATE_TIMEOUT_S,
                safety_class="confirm",
            )
        )
    return {v.name: v for v in verbs}


__all__ = [
    "GRIPPER_CLOSED",
    "GRIPPER_OPEN",
    "JOINTS",
    "GripperParams",
    "ManipulateParams",
    "MoveJointsParams",
    "PickParams",
    "lerobot_conditions",
    "lerobot_verbs",
]
