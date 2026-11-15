"""An Open Duck Mini v2 in the cartoon world.

The body is close enough to the Microduck's that the simulator needs no new entity, so this
is `Sim2DTransport` with three differences. It understands the antenna gestures this robot
has and the Microduck does not; it refuses the skills this robot cannot do, so a bug that
somehow sent one fails loudly in sim rather than only on hardware; and it stops reporting a
battery and kick telemetry, because nothing in the Open Duck runtime reports either.

The tighter velocity limits are not enforced here. They live in the manifest, and
`speed_limits()` clamps every twist before it arrives.
"""

from __future__ import annotations

from roboglot.transport.base import Ack, DuckState
from roboglot.transport.sim2d import Sim2DTransport
from roboglot_open_duck.verbs import GESTURES

#: Telemetry the shared cartoon duck reports and this robot cannot.
_NOT_ON_THIS_ROBOT = ("kicks", "kicks_connected", "last_kick_ball_moved_m", "holding")

_CANNOT = ("kick_left", "kick_right", "ground_pick", "sit_toggle", "roulade")


class OpenDuckSim2D(Sim2DTransport):
    name = "sim2d"
    mobility = "legged"
    features = {"speaker": True, "camera": True, "antennas": True, "microphone": False}

    def __init__(self, seed: int = 0, **kwargs: object) -> None:
        super().__init__(seed, **kwargs)  # type: ignore[arg-type]
        self.gestures: list[str] = []

    async def get_state(self) -> DuckState:
        state = await super().get_state()
        extras = {k: v for k, v in state.extras.items() if k not in _NOT_ON_THIS_ROBOT}
        return state.model_copy(
            update={
                # nothing in the Open Duck runtime reports a battery, so neither do we
                "battery_percent": None,
                "extras": {**extras, "policy_running": True, "gestures": len(self.gestures)},
            }
        )

    def _do(self, skill: str) -> Ack:
        kind, _, arg = skill.partition(":")
        if kind == "antennas":
            if self.world.ducks[self.duck_index].posture == "fallen":
                return Ack(accepted=False, reason="the duck has fallen")
            if arg not in GESTURES:
                return Ack(accepted=False, reason=f"unknown antenna gesture {arg!r}")
            self.gestures.append(arg)
            return Ack()
        if skill in _CANNOT:
            return Ack(accepted=False, reason=f"an Open Duck Mini v2 cannot {skill}")
        return super()._do(skill)
