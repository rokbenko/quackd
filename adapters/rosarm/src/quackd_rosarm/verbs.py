"""An industrial arm's own verbs: where its joints are, and one move to joint goals.

`move_joints` takes the SO-101's parameters exactly, `positions` in degrees by joint name and
`duration_s`, so a pilot that has driven one arm reads the other, and the move under them is the
arm's own controller's: one trajectory goal, sent once, and watched until it ends.

- Every joint, always: a joint the pilot did not name is sent at its present reading, since the
  JointTrajectoryController refuses a goal that leaves one out by default and MotoROS2 always does
  (`up.JTC_ALL_JOINTS`, `up.MOTO_EVERY_JOINT`).
- Never faster than a tenth of each joint's rated speed (`SPEED_SHARE`), from the robot's own
  description, at the fastest point of the segment the controller plays and not on average
  (`PEAK_OVER_MEAN`): a `duration_s` shorter than that is stretched, and the result says by how
  much, and a move that would need longer than `MOVE_MAX_S` is refused, so the pilot splits it.
- Never a second goal over a running one: MotoROS2 has one goal handle and no replacing, and one
  rule for four arms is the simpler one.
- A move is timed from its start, the first feedback its controller sends, and not from the goal
  leaving quackd: a bridge makes an action client for each goal and finds the controller's server
  before it sends the goal on, which the first goal through a bridge that has just started waits
  seconds for. One that shows no start within `START_SLACK_S` is cancelled as one nobody answered.
- Arrival is quackd's judgement and not the action's: after the result, every joint must read
  within `TOL_DEG` of its goal on `/joint_states`, or the verb fails naming the joint furthest off,
  as the SO-101's `shortfall` does. A SUCCEEDED proves little where the tolerances are left at 0
  (`up.JTC_ZERO_TOLERANCE`), and on the iiwa the readings are the commands (`up.IIWA_OPEN_LOOP`).
  A joint within `TOL_DEG` of its goal is at it, so a goal that near every reading is arrived at
  whether or not the arm moves: the check cannot tell a move that small from none, and the verb's
  description says so.

Every move asks the person first (`safety_class="confirm"`, on the mock as on `ws`, since a fake is
never kinder): a seven to twenty-four kilogram arm is not moved on a pilot's word alone. And it
runs only while the readings are fresh and the driver has not said the arm cannot move.
"""

from __future__ import annotations

import asyncio
import math
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from quackd.adapters.base import published_travel, said_past
from quackd.transport.base import DuckState, Intent
from quackd.verbs.core import send_or_fail
from quackd.verbs.registry import (
    STOP_ENDED,
    STOPPED,
    NoParams,
    Precondition,
    Verb,
    VerbContext,
    VerbResult,
)

TICK_S = 0.1
"""How often a running move is looked at."""
TOL_DEG = 0.5
"""Close enough, on an industrial arm. MotoROS2's own default goal tolerance is 0.01 rad, about
0.57 degrees (`up.MOTO_GOAL_TOLERANCE`), and UR's 0.1 rad (`up.UR_TOLERANCES`). A joint already
this near its goal counts as at it, so a move smaller than this counts as arrived whether or not
the arm moved."""
SPEED_SHARE = 0.1
"""The share of each joint's rated speed a move may use at most: 18 degrees a second on a UR5e
joint rated at 180. quackd's own ceiling, a courtesy and not a safeguard."""
PEAK_OVER_MEAN = 15.0 / 8.0
"""How much faster than its mean a joint goes at the middle of a segment that starts and ends at
rest: 15/8 on the quintic a JointTrajectoryController plays between two points with velocities and
accelerations (`up.JTC_QUINTIC`), and 3/2 on MotoROS2's cubic (`up.MOTO_CUBIC`). The ceiling is
held at the peak, and every arm's move is timed for the larger of the two."""
MOVE_MIN_S = 0.2
MOVE_MAX_S = 12.0
"""The shortest and the longest move a pilot may ask for, the SO-101's. The longest is also the
bound on what runs on when quackd goes quiet in the middle of one, since nothing ends a goal
then (`up.JTC_NO_CLIENT_TIMEOUT`, `up.CLOSED_CLIENT_CANCELS_NOTHING`)."""
SETTLE_S = 1.0
"""How long the readings are watched after the result, for an arm that arrives a little after its
controller said so."""
RESULT_SLACK_S = 2.0
"""How long past the move's own end a result may take before quackd stops waiting, cancels it and
says no answer came. The end is counted from the move's start, its first feedback."""
START_SLACK_S = 8.0
"""How long after a goal is sent quackd waits for its first feedback, the sign that the controller
has started it (`up.GOAL_FEEDBACK`), before it cancels the goal as one nobody answered: a goal its
bridge dropped never answers (`up.GOAL_DROPPED_BY_GLOB`). The bridge makes an action client for
every goal and waits up to a second for the controller's server before it sends the goal on
(`up.NO_ACTION_SERVER`), and the first goal through a bridge that has just started waits besides
while that client loads the action's types, which took about two seconds for Jazzy's rosbridge
2.7.1 in its official image; and a goal that wakes a MotoMini waits up to five seconds for its
servos (`up.MOTO_ECO_REENABLES`)."""
MOVE_JOINTS_TIMEOUT_S = 30.0
"""The executor's timeout for `move_joints`, which the verb's own deadlines end inside: the wait
for the start, the longest move, the slack, the settle and a cancel's wait
(`transport.CANCEL_WAIT_S`), with room."""
NOTHING_TO_MOVE_DEG = 0.05
"""A goal this near every reading is one the arm is already at: nothing is sent."""

NO_TOOL = (
    "no gripper or tool is fitted: what goes on its flange is the integrator's choice, and quackd "
    "drives none"
)
NOT_OFFERED = {verb: NO_TOOL for verb in ("gripper", "place", "pick", "manipulate")}
"""The verbs a bare industrial arm has none of, with why, for `extras.not_offered` (ADR-0077's
bare wrist that holds nothing)."""


class MoveJointsParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    positions: dict[str, float] = Field(
        ...,
        description=(
            "Joint -> goal in degrees, by the names report_state gives. Every joint not given is "
            "held where it reads now."
        ),
    )
    duration_s: float = Field(
        default=5.0,
        ge=MOVE_MIN_S,
        le=MOVE_MAX_S,
        description=(
            "How long the motion should take, in seconds: every joint travels from where it is "
            "to its goal across this time, so a longer time is a slower move. The arm never "
            "moves faster than a tenth of each joint's rated speed, so a time too short for a "
            "long move is stretched to what that allows. The verb waits until the arm has arrived."
        ),
    )

    @field_validator("positions")
    @classmethod
    def _goals(cls, value: dict[str, float]) -> dict[str, float]:
        if not value:
            raise ValueError("give at least one joint")
        for joint, goal in value.items():
            if not math.isfinite(goal):
                raise ValueError(f"{joint}={goal} is not a number of degrees")
        return value


# ── preconditions the manifest references by name ───────────────────────────────────────────


def _fresh_state(state: DuckState) -> str | None:
    """The readings must be fresh: a goal starts from where the arm reads, and so does the check
    that it arrived."""
    stale = state.extras.get("stale")
    return None if not stale else f"the arm's readings are stale ({stale}): nothing is sent on them"


def _can_move(state: DuckState) -> str | None:
    """The driver's own word where it publishes one; a driver that publishes none, or one quackd
    could not read, is believed, as the SO-101's `not_hot` believes a backend with no thermometer.
    quackd never powers, enables, un-brakes or un-stops an arm, so this says who has to."""
    driver = state.extras.get("driver") or {}
    if driver.get("can_move") is not False:
        return None
    return (
        f"the arm's driver says it cannot move ({driver.get('says') or 'stopped'}): the person at "
        "the cell brings it back, and quackd never does"
    )


def rosarm_conditions() -> dict[str, Precondition]:
    return {"fresh_state": _fresh_state, "can_move": _can_move}


# ── the verbs ───────────────────────────────────────────────────────────────────────────────


def _joints_of(state: DuckState) -> dict[str, float]:
    return {str(k): float(v) for k, v in dict(state.extras.get("joints") or {}).items()}


def _extras(ctx: VerbContext) -> dict[str, Any]:
    return dict(ctx.manifest.extras) if ctx.manifest is not None else {}


async def report_state(ctx: VerbContext, _: NoParams) -> VerbResult:
    """What this arm knows, in the sentence rather than in the data: the core one says a posture
    and a policy, which a bolted arm has neither of."""
    state = await ctx.transport.get_state()
    extras = state.extras
    joints = _joints_of(state)
    where = ", ".join(f"{name} {value:.1f}" for name, value in joints.items())
    parts = [f"{where} (degrees)" if where else "no joints read"]
    move = extras.get("move") or {}
    if move.get("status") == "running":
        parts.append(f"a move is running ({move['elapsed_s']:.1f} s of {move['duration_s']:.1f})")
    else:
        parts.append("no move running")
    driver = extras.get("driver") or {}
    if driver.get("can_move") is False:
        parts.append(f"THE DRIVER SAYS IT CANNOT MOVE: {driver.get('says')}")
    elif driver.get("says"):
        parts.append(f"the driver says {driver['says']}")
    elif driver.get("read"):
        parts.append("the driver has said nothing that settles whether it can move")
    else:
        parts.append("nothing read from the driver about itself")
    if stale := extras.get("stale"):
        parts.append(f"READINGS STALE: {stale}")
    elif (age := extras.get("state_age_s")) is not None:
        parts.append(f"readings {age:.2f} s old")
    if readings := extras.get("readings"):
        parts.append(f"readings are {readings}")
    parts.append(f"holds {extras.get('holds') or 'nothing'}")
    return VerbResult.success("; ".join(parts), state=state.model_dump())


def range_refusal(goals: dict[str, float], travel: dict[str, Any]) -> str | None:
    """Why a goal cannot be sent, in the first joint's words, or None when every one lies inside
    the travel the arm's own description gives. The travel is printed as the manifest publishes
    it, rounded inward (`published_travel`), and the goal so that it never lands inside it
    (`said_past`)."""
    for joint, goal in sorted(goals.items()):
        span = travel.get(joint)
        if not span:
            return f"{joint} has no travel in the arm's description, so no goal for it is sent"
        lo, hi = float(span[0]), float(span[1])
        if not lo <= float(goal) <= hi:
            shown_lo, shown_hi = published_travel(lo, hi)
            return (
                f"{joint}={said_past(goal, shown_lo, shown_hi)} is outside its travel "
                f"{shown_lo:.1f}..{shown_hi:.1f} in the arm's own description"
            )
    return None


def furthest_off(goal: dict[str, float], joints: dict[str, float]) -> tuple[str, float]:
    gaps = {joint: abs(joints[joint] - value) for joint, value in goal.items() if joint in joints}
    worst = max(gaps, key=lambda joint: gaps[joint])
    return worst, gaps[worst]


def _ended(why: str) -> dict[str, Any]:
    """What a result carries when it was ended from beside it, as the core verbs' do
    (`quackd.verbs.core._ended_by`): a stop is marked `STOP_ENDED`, which `abort_when`'s count of
    failures in a row passes over, and a budget that ran out is no stop."""
    return {"interrupted": why, **({"ended_by": STOP_ENDED} if why == STOPPED else {})}


async def move_joints(ctx: VerbContext, p: MoveJointsParams) -> VerbResult:
    """One goal to every joint, at most at the ceiling's speed, watched until it ends and the
    readings say the arm is there. One at a time: two calls at once, as an MCP client's two tool
    calls can be, would read one goal's record as each other's, so a second is refused while
    the first is still being watched (`RosArmAdapter.move_lock`)."""
    lock = getattr(ctx.transport, "move_lock", None)
    if not isinstance(lock, asyncio.Lock):
        return await _move(ctx, p)
    if lock.locked():
        return VerbResult.fail(
            "move_joints: another move is still being watched to its end, and quackd moves an "
            "arm one goal at a time"
        )
    async with lock:
        return await _move(ctx, p)


async def _move(ctx: VerbContext, p: MoveJointsParams) -> VerbResult:
    extras = _extras(ctx)
    names = [str(joint) for joint in extras.get("joints") or []]
    unknown = sorted(set(p.positions) - set(names))
    if unknown:
        return VerbResult.fail(
            f"move_joints: unknown joints {unknown}; this arm has {', '.join(names)}"
        )
    travel = dict(extras.get("joint_range_deg") or {})
    speeds = {str(k): float(v) for k, v in dict(extras.get("joint_speed_deg_s") or {}).items()}
    if (refusal := range_refusal(dict(p.positions), travel)) is not None:
        return VerbResult.fail(f"move_joints: {refusal}", goal=dict(p.positions))
    state = await ctx.transport.get_state()
    readings = _joints_of(state)
    unread = [joint for joint in names if joint not in readings]
    if unread:
        return VerbResult.fail(
            f"move_joints: no reading of {', '.join(unread)}, so nothing is sent"
        )
    # a joint the pilot did not name is held where it reads, so a reading past the travel would
    # be a goal past it: nothing is sent from there, and the person at the pendant brings it back
    for joint in names:
        if joint in travel:
            lo, hi = (float(v) for v in travel[joint])
            if not lo <= readings[joint] <= hi:
                return VerbResult.fail(
                    f"move_joints: {joint} reads {said_past(readings[joint], lo, hi)}, past its "
                    f"travel of {lo:g} to {hi:g}, so quackd sends no goal from there: the person "
                    "at the pendant brings it back inside",
                    joints=readings,
                )
    goal = {joint: float(p.positions.get(joint, readings[joint])) for joint in names}
    distance = {joint: abs(goal[joint] - readings[joint]) for joint in names}
    if max(distance.values(), default=0.0) < NOTHING_TO_MOVE_DEG:
        return VerbResult.success("already there: nothing was sent", goal=goal, joints=readings)
    missing_speed = [
        joint
        for joint in names
        if not math.isfinite(speeds.get(joint, 0.0)) or speeds.get(joint, 0.0) <= 0
    ]
    if missing_speed:
        return VerbResult.fail(
            f"move_joints: no rated speed for {', '.join(missing_speed)} in the arm's description, "
            "so quackd cannot keep the move under its ceiling and sends nothing"
        )
    # the peak of the segment, not its mean, is what the ceiling holds
    need = max(PEAK_OVER_MEAN * distance[joint] / (SPEED_SHARE * speeds[joint]) for joint in names)
    if need > MOVE_MAX_S:
        slowest = max(names, key=lambda joint: distance[joint] / speeds[joint])
        return VerbResult.fail(
            f"move_joints: at a tenth of its rated speed {slowest} needs {need:.1f} s for this, "
            f"past the {MOVE_MAX_S:g} s a move may take: split it into shorter moves",
            goal=goal,
        )
    duration = max(p.duration_s, need)
    if (fail := await send_or_fail(ctx, Intent.joint(goal, duration))) is not None:
        return VerbResult.fail(f"move_joints: {fail.summary}", goal=goal)
    sent = ctx.transport.now()
    began: float | None = None
    move: dict[str, Any] = {}
    while True:
        if (told := ctx.interrupted()) is not None:
            await ctx.transport.stop()
            return VerbResult.fail(f"move_joints: {told}", goal=goal, **_ended(told))
        state = await ctx.transport.get_state()
        move = dict(state.extras.get("move") or {})
        if move.get("status") != "running":
            break
        now = ctx.transport.now()
        if began is None and move.get("started"):
            began = now
        if began is None and now >= sent + START_SLACK_S:
            waited = f"move_joints: the goal showed no sign of starting within {START_SLACK_S:g} s"
            dropped = "; the bridge may have dropped it"
        elif began is not None and now >= began + duration + RESULT_SLACK_S:
            waited = (
                f"move_joints: no result came within {duration + RESULT_SLACK_S:.1f} s of the "
                "move starting"
            )
            dropped = ""
        else:
            await ctx.transport.sleep(TICK_S)
            continue
        await ctx.transport.stop()
        if undelivered := getattr(ctx.transport, "stop_error", None):
            # a cancel nobody answers is how a goal the bridge dropped looks from here
            said = f"{waited}, and quackd could not cancel the goal: {undelivered}{dropped}"
        else:
            said = f"{waited}, so quackd cancelled the goal"
        return VerbResult.fail(said, goal=goal, joints=_joints_of(state))
    status = str(move.get("status"))
    said = str(move.get("error_string") or "")
    code = move.get("error_code")
    if status == "refused":
        return VerbResult.fail(f"move_joints: the goal was refused: {said}", goal=goal)
    if status != "succeeded":
        tail = f" (error_code {code})" if code not in (None, 0) else ""
        return VerbResult.fail(
            f"move_joints: the goal was {status}{': ' + said if said else ''}{tail}",
            goal=goal,
            joints=_joints_of(state),
            status=status,
        )
    # arrival is quackd's own judgement: a SUCCEEDED where the tolerances are 0 says only that the
    # time ran out
    settle_until = ctx.transport.now() + SETTLE_S
    joints = _joints_of(state)
    worst, gap = furthest_off(goal, joints)
    while (gap > TOL_DEG or state.extras.get("stale")) and ctx.transport.now() < settle_until:
        await ctx.transport.sleep(TICK_S)
        state = await ctx.transport.get_state()
        joints = _joints_of(state)
        worst, gap = furthest_off(goal, joints)
    if stale := state.extras.get("stale"):
        # arrival is read off the arm, and a reading that is not fresh says nothing of it
        return VerbResult.fail(
            f"move_joints: the controller said it succeeded, and the readings are stale "
            f"({stale}), so quackd cannot say the arm is there",
            goal=goal,
            joints=joints,
        )
    if gap > TOL_DEG:
        return VerbResult.fail(
            f"move_joints: the controller said it succeeded, and {worst} reads "
            f"{joints[worst]:.1f} with a goal of {goal[worst]:.1f}",
            goal=goal,
            joints=joints,
        )
    moved = ", ".join(f"{joint}={joints[joint]:.1f}" for joint in p.positions)
    if duration > p.duration_s + 1e-9:
        moved += (
            f"; it took {duration:.1f} s rather than {p.duration_s:g}, since no joint moves faster "
            "than a tenth of its rated speed"
        )
    if readings_note := state.extras.get("readings"):
        moved += f"; readings are {readings_note}"
    return VerbResult.success(f"moved {moved}", goal=goal, joints=joints, duration_s=duration)


def rosarm_verbs() -> dict[str, Verb]:
    verbs = [
        Verb(
            "report_state",
            "Report the arm: every joint in degrees, whether a move is running, what its driver "
            "says about itself, and how fresh the readings are.",
            report_state,
            NoParams,
            timeout_s=5,
            read_only=True,
            core=True,
        ),
        Verb(
            "move_joints",
            "Move the arm's joints to goal angles in degrees over duration_s seconds, as one "
            "trajectory its own controller runs, and wait for it to arrive. Every joint not "
            f"named stays where it is. Arrived means every joint reads within {TOL_DEG:g} "
            "degrees of its goal, so a goal that close to where the joints already read counts "
            "as arrived whether or not the arm moved. A person confirms every move first.",
            move_joints,
            MoveJointsParams,
            timeout_s=MOVE_JOINTS_TIMEOUT_S,
            safety_class="confirm",
            done_condition=f"every joint reads within {TOL_DEG:g} degrees of its goal",
        ),
    ]
    return {verb.name: verb for verb in verbs}


__all__ = [
    "MOVE_MAX_S",
    "NOT_OFFERED",
    "SPEED_SHARE",
    "TOL_DEG",
    "MoveJointsParams",
    "rosarm_conditions",
    "rosarm_verbs",
]
