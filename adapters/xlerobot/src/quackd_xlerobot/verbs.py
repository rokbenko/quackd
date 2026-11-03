"""An XLeRobot's own verbs: two arms' joints, and a gripper that names which hand.

The arms are the first bimanual pair in quackd, and nothing in the manifest vocabulary names
an arm. So the side lives in the joint name (`left_arm_*`, `right_arm_*`, which are upstream's
own keys, `upstream_api.STATE_FEATURES`) and in a `side` parameter on `gripper`. Per-hand
holding lives in `DuckState.extras`, because `DuckState.holding` is one bool for the whole
body.

Positions are normalised, not degrees: upstream's `use_degrees` defaults to False, so a body
joint is -100..100 and a gripper is 0..100 (`upstream_api.USE_DEGREES_DEFAULT`). That is a
different contract from the SO-101 arm in `adapters/lerobot`, which sets degrees, and the two
must not be confused: the same number means a different angle.

The head is deliberately absent. `head_motor_1` and `head_motor_2` exist on the wire, but
which one is yaw and which is pitch is stated nowhere upstream, so quackd does not offer them
and `search_scan` turns the body instead.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from quackd.transport.base import DuckState, Intent
from quackd.verbs.core import _ended_by, _waited_out, send_or_fail
from quackd.verbs.registry import Precondition, Verb, VerbContext, VerbResult

ARMS: tuple[str, ...] = ("left", "right")
"""The two arms, as upstream's key prefixes spell them (`left_arm_`, `right_arm_`)."""

_JOINTS_PER_ARM: tuple[str, ...] = (
    "shoulder_pan",
    "shoulder_lift",
    "elbow_flex",
    "wrist_flex",
    "wrist_roll",
    "gripper",
)
JOINTS: tuple[str, ...] = tuple(f"{arm}_arm_{joint}" for arm in ARMS for joint in _JOINTS_PER_ARM)
"""The twelve arm motors, in upstream's own naming (`upstream_api.STATE_FEATURES`).

The head's two motors are on the wire but not here, on purpose: see the module docstring."""

JOINT_NORM = 100.0
"""Body joints are -100..100 and grippers 0..100. Never degrees."""
GRIPPER_OPEN = 100.0
GRIPPER_CLOSED = 0.0
GRIPPER_S = 0.8
"""How long to let a gripper travel before reporting. Upstream offers no completion signal."""


def is_gripper(joint: str) -> bool:
    return joint.endswith("_gripper")


def joint_range(joint: str) -> tuple[float, float]:
    """The normalised range of one joint: grippers are 0..100, everything else -100..100."""
    return (0.0, JOINT_NORM) if is_gripper(joint) else (-JOINT_NORM, JOINT_NORM)


class MoveJointsParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    positions: dict[str, float] = Field(
        ...,
        description=(
            "Joint -> goal in normalised units, NOT degrees: -100..100 for a body joint, "
            "0..100 for a gripper. Only the joints you name move."
        ),
    )
    duration_s: float = Field(
        default=1.0, ge=0.2, le=10, description="How long to give the motion before reporting."
    )

    @field_validator("positions")
    @classmethod
    def _known_joints(cls, value: dict[str, float]) -> dict[str, float]:
        if not value:
            raise ValueError("give at least one joint")
        unknown = sorted(set(value) - set(JOINTS))
        if unknown:
            raise ValueError(
                f"unknown joints {unknown}; this robot has {', '.join(JOINTS)} "
                "(the head is not driveable from quackd)"
            )
        for joint, goal in value.items():
            lo, hi = joint_range(joint)
            if not lo <= goal <= hi:
                raise ValueError(f"{joint}={goal} is outside {lo}..{hi}")
        return value


class GripperParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    side: Literal["left", "right", "both"] = Field(
        default="right", description="Which hand: left, right, or both at once."
    )
    open: bool = Field(
        ...,
        description=(
            "True opens the gripper, False closes it. Say which: opening lets go of what it holds."
        ),
    )


# ── preconditions the manifest references by name ───────────────────────────────────────


def _link_fresh(state: DuckState) -> str | None:
    """The host publishes at 30 Hz and stamps nothing, so quackd stamps on arrival.

    A stale reading means the host is gone: it exits by itself after an hour
    (`upstream_api.CONNECTION_TIME_S`), and its 500 ms watchdog has already stopped the base.
    Refusing here is what makes that legible instead of a verb that claims a move nobody made.
    """
    stale_ms = state.extras.get("stale_ms")
    if stale_ms is None:
        return None
    window = float(state.extras.get("stale_limit_ms") or 500.0)
    if float(stale_ms) <= window:
        return None
    return (
        f"no observation from the robot for {float(stale_ms):.0f} ms (limit {window:.0f} ms): "
        "the host is not answering. It exits by itself an hour after it was started, so it "
        "probably needs restarting on the robot."
    )


def xlerobot_conditions() -> dict[str, Precondition]:
    return {"link_fresh": _link_fresh}


# ── the verbs ───────────────────────────────────────────────────────────────────────────


async def move_joints(ctx: VerbContext, p: MoveJointsParams) -> VerbResult:
    """Absolute joint goals. The arm's own controller does the motion; quackd only waits.

    Sending arm goals also commands zero base velocity, because upstream writes the wheels on
    every action (`upstream_api.SEND_ACTION_ALWAYS_WRITES_BASE`). That is upstream's design
    and it is safe, but it means this verb stops a driving base, so the result says so.

    A stop from beside it ends the wait and says what became of the arm, which it holds where it
    stands where a recent reading says where that is (`zmq_host`). "moved" was all anybody heard.
    """
    intent = Intent.joint(dict(p.positions), p.duration_s)
    if (fail := await send_or_fail(ctx, intent)) is not None:
        return fail
    if (why := await _waited_out(ctx, p.duration_s)) is not None:
        # asked here as well as in `_ended_by`: a stop that had no recent reading tries again, and
        # the words have to be about the last one
        await ctx.transport.stop()
        going = getattr(ctx.transport, "stop_left_going", ())
        stopped = "the wheels are stopped and the arm " + (
            "goes on to the goal it was sent, with no recent reading to hold it from"
            if going
            else "is held where it stood"
        )
        return await _ended_by(
            ctx, "move_joints", why, stopped, goal=dict(p.positions), base_stopped=True
        )
    state = await ctx.transport.get_state()
    joints: dict[str, Any] = state.extras.get("joints", {})
    return VerbResult.success(
        "moved " + ", ".join(f"{k}={v:.0f}" for k, v in p.positions.items()),
        goal=dict(p.positions),
        joints=joints,
        base_stopped=True,
    )


async def gripper(ctx: VerbContext, p: GripperParams) -> VerbResult:
    """Open or close one hand, or both.

    Nothing on the wire reports grip force or contact, so `holding` is what quackd commanded,
    never what the robot felt. The result says which, and `extras.assumptions` repeats it.
    """
    sides = list(ARMS) if p.side == "both" else [p.side]
    goal = GRIPPER_OPEN if p.open else GRIPPER_CLOSED
    positions = {f"{side}_arm_gripper": goal for side in sides}
    intent = Intent(kind="gripper", params={"open": p.open, "side": p.side, "positions": positions})
    if (fail := await send_or_fail(ctx, intent)) is not None:
        return fail
    if (why := await _waited_out(ctx, GRIPPER_S)) is not None:
        stopped = "the wheels are stopped and the gripper goes on to where it was sent"
        return await _ended_by(ctx, "gripper", why, stopped, side=p.side, open=p.open)
    state = await ctx.transport.get_state()
    held = state.extras.get("holding", {})
    what = "both grippers" if p.side == "both" else f"the {p.side} gripper"
    return VerbResult.success(
        f"{what} {'open' if p.open else 'closed'}",
        side=p.side,
        open=p.open,
        holding=held,
        sensed=False,
    )


def xlerobot_verbs() -> dict[str, Verb]:
    verbs = [
        Verb(
            "move_joints",
            "Move one or more arm joints to goal positions in normalised units (-100..100, "
            "grippers 0..100 - these are NOT degrees). Name joints as left_arm_* or "
            "right_arm_*. This also stops the base.",
            move_joints,
            MoveJointsParams,
            timeout_s=15,
            safety_class="confirm",
        ),
        Verb(
            "gripper",
            "Open or close a gripper. Say which side: left, right, or both.",
            gripper,
            GripperParams,
            timeout_s=5,
        ),
    ]
    return {v.name: v for v in verbs}


__all__ = [
    "ARMS",
    "GRIPPER_CLOSED",
    "GRIPPER_OPEN",
    "JOINTS",
    "JOINT_NORM",
    "GripperParams",
    "MoveJointsParams",
    "is_gripper",
    "joint_range",
    "xlerobot_conditions",
    "xlerobot_verbs",
]
