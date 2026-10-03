"""An AlohaMini's own verbs: two arms, two grippers, and a lift that carries both.

The second bimanual body in quackd and the first with a vertical axis. As on the XLeRobot,
nothing in the manifest vocabulary names an arm, so the side lives in the joint keys
(`arm_left_*`, `arm_right_*`, upstream's own spelling) and in a `side` parameter on `gripper`
and `move_joints`. Per-hand holding lives in `DuckState.extras`.

Which joints exist depends on the SKU, and quackd derives that from the observed key set
rather than from a config: an `alohamini1` has six joints per arm, the two 6dof models have
seven, inserting `wrist_yaw` between `wrist_flex` and `wrist_roll`
(`upstream_api.ARM_PROFILE_JOINTS`). Positions are normalised, not degrees.

The lift is the reason this adapter exists in the shape it does. It is a velocity servo that
latches: an action carrying neither lift key writes nothing, so the servo keeps travelling
while the command itself refreshes the watchdog that would have stopped it. Every payload
quackd sends therefore carries a lift key, and `payload()` in the backend is the only place
that is allowed to build one.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from quackd.transport.base import DuckState, Intent
from quackd.verbs.core import send_or_fail
from quackd.verbs.registry import NoParams, Precondition, Verb, VerbContext, VerbResult

ARMS: tuple[str, ...] = ("left", "right")
"""Upstream's key prefixes are `arm_left_` and `arm_right_`."""

JOINTS_5DOF: tuple[str, ...] = (
    "shoulder_pan",
    "shoulder_lift",
    "elbow_flex",
    "wrist_flex",
    "wrist_roll",
    "gripper",
)
JOINTS_6DOF: tuple[str, ...] = (
    "shoulder_pan",
    "shoulder_lift",
    "elbow_flex",
    "wrist_flex",
    "wrist_yaw",
    "wrist_roll",
    "gripper",
)
MODEL_JOINTS: dict[str, tuple[str, ...]] = {
    "alohamini1": JOINTS_5DOF,
    "alohamini2": JOINTS_6DOF,
    "alohamini2pro": JOINTS_6DOF,
}
DEFAULT_MODEL = "alohamini2"
MODELS: tuple[str, ...] = tuple(MODEL_JOINTS)

JOINT_NORM = 100.0
"""Body joints are -100..100 and grippers 0..100. Never degrees."""
GRIPPER_OPEN = 100.0
GRIPPER_CLOSED = 0.0
GRIPPER_S = 0.8

LIFT_MIN_MM = 5.0
"""Not 0: upstream refuses downward motion below its descent floor rather than clamping."""
LIFT_MAX_MM = 600.0
LIFT_TIMEOUT_S = 40.0
"""Generous on purpose. Nothing upstream states the lift's speed in mm/s."""
LIFT_TICK_S = 0.1
"""Ten hertz, well inside the robot's one second watchdog, and the rate its own proportional
controller expects: it takes one step per command received."""


def joints_for(model: str) -> tuple[str, ...]:
    """Every joint key this SKU has, both arms, in upstream's own spelling."""
    per_arm = MODEL_JOINTS.get(model, JOINTS_6DOF)
    return tuple(f"arm_{arm}_{joint}" for arm in ARMS for joint in per_arm)


def is_gripper(joint: str) -> bool:
    return joint.endswith("_gripper")


def joint_range(joint: str) -> tuple[float, float]:
    return (0.0, JOINT_NORM) if is_gripper(joint) else (-JOINT_NORM, JOINT_NORM)


def model_from_keys(keys: Iterable[object] | None) -> str:
    """Which SKU the host is running, from the observation's own key set.

    The host defaults to alohamini2 and upstream's client defaults to alohamini1, and nothing
    cross-checks them (`upstream_api.ROBOT_MODEL_DEFAULTS_DISAGREE`), so a mismatch would
    silently zero-fill two joints. Reading the wire instead makes that impossible."""
    names = {str(k) for k in (keys or ())}
    return "alohamini2" if any("wrist_yaw" in n for n in names) else "alohamini1"


class MoveJointsParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    arm: Literal["left", "right"] = Field(..., description="Which arm to move.")
    positions: dict[str, float] = Field(
        ...,
        description=(
            "Joint -> goal in normalised units, NOT degrees: -100..100 for a body joint, "
            "0..100 for a gripper. Name joints without the arm prefix, e.g. shoulder_pan."
        ),
    )
    duration_s: float = Field(default=1.5, ge=0.2, le=10)

    @field_validator("positions")
    @classmethod
    def _nonempty(cls, value: dict[str, float]) -> dict[str, float]:
        if not value:
            raise ValueError("give at least one joint")
        return value


class GripperParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    side: Literal["left", "right", "both"] = Field(default="right")
    open: bool = Field(default=True, description="True opens the gripper, False closes it.")


class LiftParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    height_mm: float = Field(
        ...,
        ge=LIFT_MIN_MM,
        le=LIFT_MAX_MM,
        description=(
            "Absolute height of the lift in millimetres, 5 to 600. The lift carries both arms, "
            "so it can pin things above and below itself."
        ),
    )


# ── preconditions the manifest references by name ───────────────────────────────────────


def _host_fresh(state: DuckState) -> str | None:
    """The host publishes only when asked and stamps nothing, so quackd stamps on arrival.

    It also exits by itself after 6000 seconds, and kills its own process after twenty
    consecutive over-current reads. Both look identical from here: the observation stops
    advancing. Saying so beats a verb that claims a move nobody made."""
    stale_ms = state.extras.get("stale_ms")
    if stale_ms is None:
        return None
    limit = float(state.extras.get("stale_limit_ms") or 1000.0)
    if float(stale_ms) <= limit:
        return None
    return (
        f"no fresh observation from the robot for {float(stale_ms):.0f} ms (limit {limit:.0f}): "
        "the host is not answering. It exits by itself after about 100 minutes, and it also "
        "exits on an over-current trip, so it may need restarting on the robot."
    )


def _calibrated(state: DuckState) -> str | None:
    """An uncalibrated robot never had its lift put into velocity mode, because upstream's
    `lift.configure()` is commented out and only `home()` sets it, and `home()` runs only when
    calibrated. A velocity write to a servo that is not in velocity mode means nothing."""
    if state.extras.get("calibrated", True):
        return None
    return (
        "this robot is not calibrated, so its lift was never put into velocity mode and its "
        "joint numbers have no fixed meaning. Run alohamini_calibrate on the robot first"
    )


def _arm_torque(state: DuckState) -> str | None:
    """As shipped, the arms are limp: upstream disables torque and both re-enable calls are
    commented out. quackd's own host wrapper turns it back on and says so in the state."""
    if state.extras.get("arm_torque", False):
        return None
    return (
        "the arms have no torque, so a joint command would move nothing and a stop could not "
        "hold them. This robot's stock host never enables arm torque; run quackd's own host "
        "wrapper on the robot instead (see docs/adapters/alohamini/README.md)"
    )


def alohamini_conditions() -> dict[str, Precondition]:
    return {"host_fresh": _host_fresh, "calibrated": _calibrated, "arm_torque": _arm_torque}


# ── the verbs ───────────────────────────────────────────────────────────────────────────


async def move_joints(ctx: VerbContext, p: MoveJointsParams) -> VerbResult:
    """Absolute joint goals for one arm. Also stops the base, as upstream does."""
    goals = {f"arm_{p.arm}_{joint}": value for joint, value in p.positions.items()}
    intent = Intent.joint(goals, p.duration_s)
    if (fail := await send_or_fail(ctx, intent)) is not None:
        return fail
    await ctx.transport.sleep(p.duration_s)
    state = await ctx.transport.get_state()
    joints: dict[str, Any] = state.extras.get("joints", {})
    return VerbResult.success(
        f"moved the {p.arm} arm: "
        + ", ".join(f"{k}={v:.0f}" for k, v in sorted(p.positions.items())),
        arm=p.arm,
        goal=dict(p.positions),
        joints=joints,
        base_stopped=True,
    )


async def gripper(ctx: VerbContext, p: GripperParams) -> VerbResult:
    """Open or close one hand, or both.

    A 500 mA force limiter on the robot freezes a gripper that meets resistance, so closing on
    an object is safe. Nothing reports contact back, so `holding` is what quackd commanded."""
    sides = list(ARMS) if p.side == "both" else [p.side]
    goal = GRIPPER_OPEN if p.open else GRIPPER_CLOSED
    positions = {f"arm_{side}_gripper": goal for side in sides}
    intent = Intent(kind="gripper", params={"open": p.open, "side": p.side, "positions": positions})
    if (fail := await send_or_fail(ctx, intent)) is not None:
        return fail
    await ctx.transport.sleep(GRIPPER_S)
    state = await ctx.transport.get_state()
    what = "both grippers" if p.side == "both" else f"the {p.side} gripper"
    return VerbResult.success(
        f"{what} {'open' if p.open else 'closed'}",
        side=p.side,
        open=p.open,
        holding=state.extras.get("holding", {}),
        sensed=False,
    )


async def lift(ctx: VerbContext, p: LiftParams) -> VerbResult:
    """Drive the vertical axis to an absolute height and wait for it to settle.

    The robot closes this loop itself, but only **one proportional step per command received**,
    and its watchdog stops the lift after a second of silence. So a single command moves the
    lift a little and then the deadman ends it. quackd re-sends the target every tick for the
    same reason `move` re-sends a velocity, and watches the height rather than timing the
    travel: nothing upstream says how fast the lift moves."""
    intent = Intent(kind="pose", params={"lift_height_mm": p.height_mm})
    if (fail := await send_or_fail(ctx, intent)) is not None:
        return fail
    t0 = ctx.transport.now()
    height = None
    while ctx.transport.now() - t0 < LIFT_TIMEOUT_S:
        await ctx.transport.sleep(LIFT_TICK_S)
        if (fail := await send_or_fail(ctx, intent)) is not None:
            return fail
        state = await ctx.transport.get_state()
        height = state.extras.get("lift_height_mm")
        if height is not None and abs(float(height) - p.height_mm) <= 2.0:
            return VerbResult.success(
                f"lift at {float(height):.0f} mm",
                height_mm=round(float(height), 1),
                goal_mm=p.height_mm,
            )
    await ctx.transport.stop()
    where = f"{float(height):.0f} mm" if height is not None else "an unknown height"
    return VerbResult.fail(
        f"the lift did not reach {p.height_mm:.0f} mm; it stopped at {where}",
        goal_mm=p.height_mm,
    )


async def home_arms(ctx: VerbContext, _: NoParams) -> VerbResult:
    """Return both arms to their rest pose, one arm at a time."""
    for arm in ARMS:
        goals = {
            f"arm_{arm}_{joint}": 0.0 for joint in ("shoulder_pan", "shoulder_lift", "elbow_flex")
        }
        if (fail := await send_or_fail(ctx, Intent.joint(goals, 2.0))) is not None:
            return fail
        await ctx.transport.sleep(2.0)
    return VerbResult.success("both arms returned to rest")


def alohamini_verbs(*, arms: bool = True) -> dict[str, Verb]:
    """`arms` is False on a `--no_follower` host, which runs the base and the lift alone."""
    verbs = [
        Verb(
            "lift",
            "Raise or lower the vertical axis to an absolute height in millimetres (5 to 600). "
            "It carries both arms.",
            lift,
            LiftParams,
            timeout_s=LIFT_TIMEOUT_S + 10,
            safety_class="confirm",
        ),
    ]
    if arms:
        verbs += [
            Verb(
                "move_joints",
                "Move one arm's joints to goal positions in normalised units (-100..100, "
                "grippers 0..100 - these are NOT degrees). Say which arm. This also stops the "
                "base.",
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
            Verb(
                "home_arms",
                "Return both arms to their rest pose.",
                home_arms,
                NoParams,
                timeout_s=15,
                safety_class="confirm",
            ),
        ]
    return {v.name: v for v in verbs}


__all__ = [
    "ARMS",
    "DEFAULT_MODEL",
    "GRIPPER_CLOSED",
    "GRIPPER_OPEN",
    "JOINTS_5DOF",
    "JOINTS_6DOF",
    "JOINT_NORM",
    "LIFT_MAX_MM",
    "LIFT_MIN_MM",
    "MODELS",
    "MODEL_JOINTS",
    "GripperParams",
    "LiftParams",
    "MoveJointsParams",
    "alohamini_conditions",
    "alohamini_verbs",
    "is_gripper",
    "joint_range",
    "joints_for",
    "model_from_keys",
]
