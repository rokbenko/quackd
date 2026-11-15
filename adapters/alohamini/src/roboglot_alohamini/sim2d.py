"""An AlohaMini in the cartoon world.

The base is the one part of this robot the shared simulator already models: it integrates
`vx`, `vy` and `wz` holonomically, which is exactly what three omniwheels do. So this is
`Sim2DTransport` with the parts the world has never had bolted on in memory: a vertical axis
and two arms.

The arms are not simulated, they are remembered. A joint goal lands instantly and a gripper
opens and closes, which is enough to run every verb, every gate and the detector offline, and
honest because the manifest already says `holding` is commanded rather than sensed. What the
simulator does buy is `go_to`, `search_scan` and `approach_and` closing a real loop on a real
frame, which is the thing a mock cannot prove.

The velocity limits are not enforced here. They live in the manifest, and `speed_limits()`
clamps every twist before it arrives.
"""

from __future__ import annotations

from roboglot.transport.base import Ack, DuckState, Intent
from roboglot.transport.sim2d import Sim2DTransport
from roboglot_alohamini.verbs import (
    ARMS,
    DEFAULT_MODEL,
    GRIPPER_CLOSED,
    GRIPPER_OPEN,
    LIFT_MAX_MM,
    LIFT_MIN_MM,
    joint_range,
    joints_for,
)

LIFT_MM_PER_S = 60.0
"""How fast the simulated lift travels. Roboglot's own number: upstream states none."""
LIFT_DEADBAND_MM = 1.0
LIFT_START_MM = 120.0

#: Telemetry the shared cartoon duck reports and this robot cannot.
_NOT_ON_THIS_ROBOT = ("kicks", "kicks_connected", "last_kick_ball_moved_m")


class AlohaMiniSim2D(Sim2DTransport):
    name = "sim2d"
    mobility = "wheeled"
    camera_available = True
    cameras = ("forward", "wrist_right")
    arms_available = True
    arm_torque = True
    """The simulated robot is the one whose arms work: Roboglot's host wrapper is assumed."""
    robot_model = DEFAULT_MODEL
    host = "sim2d"

    def __init__(self, seed: int = 0, **kwargs: object) -> None:
        super().__init__(seed, **kwargs)  # type: ignore[arg-type]
        self.joints: dict[str, float] = dict.fromkeys(joints_for(DEFAULT_MODEL), 0.0) | {
            f"arm_{arm}_gripper": GRIPPER_OPEN for arm in ARMS
        }
        self.holding: dict[str, bool] = dict.fromkeys(ARMS, False)
        self.lift_height_mm = LIFT_START_MM
        self.lift_target_mm: float | None = None

    # ── the parts the world does not have ───────────────────────────────────────────

    def _advance_lift(self, dt: float) -> None:
        if self.lift_target_mm is None:
            return
        err = self.lift_target_mm - self.lift_height_mm
        if abs(err) <= LIFT_DEADBAND_MM:
            self.lift_target_mm = None
            return
        step = min(abs(err), LIFT_MM_PER_S * dt)
        self.lift_height_mm += step if err > 0 else -step
        self.lift_height_mm = max(LIFT_MIN_MM, min(LIFT_MAX_MM, self.lift_height_mm))

    async def sleep(self, seconds: float) -> None:
        self._advance_lift(seconds)
        await super().sleep(seconds)

    async def get_state(self) -> DuckState:
        state = await super().get_state()
        extras = {k: v for k, v in state.extras.items() if k not in _NOT_ON_THIS_ROBOT}
        return state.model_copy(
            update={
                # nothing on this robot reports a battery
                "battery_percent": None,
                # No pose. Neither robot has odometry, so the real backend and the mock
                # both report None, and a simulator that knows more than the robot is a
                # task that passes here and fails there.
                "x": None,
                "y": None,
                "theta": None,
                "posture": "unknown",
                "holding": any(self.holding.values()),
                "extras": {
                    **extras,
                    "joints": {k: round(v, 1) for k, v in self.joints.items()},
                    "holding": dict(self.holding),
                    "lift_height_mm": round(self.lift_height_mm, 1),
                    "robot_model": self.robot_model,
                    "arm_torque": self.arm_torque,
                    "calibrated": True,
                    "cameras": list(self.cameras),
                    "stale_ms": 0.0,
                    "stale_limit_ms": 1000.0,
                },
            }
        )

    def _goto(self, goals: dict[str, float]) -> None:
        for joint, goal in goals.items():
            if joint in self.joints:
                lo, hi = joint_range(joint)
                self.joints[joint] = min(hi, max(lo, float(goal)))

    async def send_intent(self, intent: Intent) -> Ack:
        p = intent.params
        match intent.kind:
            case "pose":
                target = p.get("lift_height_mm")
                if target is None:
                    return Ack(accepted=False, reason="pose needs a lift_height_mm")
                self.lift_target_mm = max(LIFT_MIN_MM, min(LIFT_MAX_MM, float(target)))
                return Ack()
            case "joint":
                self._goto({str(k): float(v) for k, v in dict(p.get("positions", {})).items()})
                # upstream writes the wheels on every action, so an arm command stops the base
                self.world.stop(duck_index=self.duck_index)
                return Ack()
            case "gripper":
                open_ = bool(p.get("open", True))
                sides = list(ARMS) if p.get("side") == "both" else [str(p.get("side", "right"))]
                for side in sides:
                    if side in ARMS:
                        self._goto(
                            {f"arm_{side}_gripper": GRIPPER_OPEN if open_ else GRIPPER_CLOSED}
                        )
                        self.holding[side] = not open_
                self.world.stop(duck_index=self.duck_index)
                return Ack()
            case "stop":
                self.lift_target_mm = None
                return await super().send_intent(intent)
            case "sound" | "look" | "do":
                return Ack(accepted=False, reason=f"an AlohaMini cannot {intent.kind}")
        return await super().send_intent(intent)
