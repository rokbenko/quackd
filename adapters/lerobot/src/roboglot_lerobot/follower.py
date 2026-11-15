"""The SO-101 follower's surface, as `lerobot:real` touches it: LeRobot's names and refusals.

`lerobot:real` reaches an arm through one object, a LeRobot follower, and through that
follower's motors bus for the two registers LeRobot's own observation leaves out. The arm in
memory that `lerobot:mock` drives (`fake_follower.py`) carries the same surface, so the real
backend's connect, travel, rest move, hold, release and take-hold run over it unchanged. What
both sides have to agree on lives here, in upstream's words: the class names LeRobot's
refusals name, the errors it raises, the registers, the config fields the real backend builds
a follower with, and a copy of LeRobot's step cap. Nothing here imports LeRobot, which needs
Python 3.12 and torch.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from roboglot_lerobot.arms import SO_ENCODER_TICKS
from roboglot_lerobot.verbs import JOINTS

NameOrID = str | int
"""How a bus call names one motor: by name, or by the id the table gives it (`up.BUS_MOTORS`)."""

FOLLOWER_CLASS = "SOFollower"
"""The class an SO-101 follower is (`up.SO_FOLLOWER`), which LeRobot's refusals name."""
BUS_CLASS = "SerialMotorsBus"
"""The class its bus is (`up.SO_BUS`), named the same way."""
NO_STATUS = "Operation timed out"
NOT_SENT = "Timeout error"
"""serialport-rs's words for a reply that never came, and rustypot's for a packet that could not go
out, which LeRobot ends its bus errors with (`up.BUS_SYNC_READ_ERROR`)."""
DISCONNECT_RETRIES = 5
"""The extra tries upstream's bus disconnect gives its torque-off (`up.BUS_DISCONNECT`)."""
MAX_RES = SO_ENCODER_TICKS - 1
"""What LeRobot divides a turn by to turn a tick into degrees (`up.DEGREES_FORMULA`)."""
GRIPPER = JOINTS[-1]
"""The one motor in LeRobot's RANGE_0_100 mode rather than degrees (`up.SO_GRIPPER_RANGE`)."""
TORQUE_ENABLE = "Torque_Enable"
TEMPERATURE = "Present_Temperature"
"""The two registers Roboglot reads off the bus itself (`up.STS3215_REGISTERS`)."""


class DeviceNotConnectedError(ConnectionError):
    """LeRobot's refusal of a call through a port that is not open (`up.NOT_CONNECTED`)."""


class DeviceAlreadyConnectedError(ConnectionError):
    """LeRobot's refusal of a connect through a port that is open
    (`up.SO_CONNECT_REFUSES_WHILE_OPEN`)."""


@dataclass
class FollowerConfig:
    """The fields of `up.SO_CONFIG` the real backend builds a follower with, by the same
    keywords (`LeRobotReal._config_kwargs`), so one dict of them builds either follower.

    Mutable, as LeRobot's config is: the real backend writes the torque flag on it just before
    each disconnect, and both fields are read when they are used and not when the follower is
    built."""

    disable_torque_on_disconnect: bool
    max_relative_target: float | dict[str, float] | None
    port: str = ""
    id: str = ""
    use_degrees: bool = True
    cameras: dict[str, Any] = field(default_factory=dict)
    num_read_retries: int = 2
    """Upstream's default (`up.SO_CONFIG`): the extra tries each read of the positions gets."""


def ensure_safe_goal_position(
    goal_present_pos: Mapping[str, tuple[float, float]],
    max_relative_target: float | dict[str, float],
) -> dict[str, float]:
    """LeRobot's step cap, line for line (`up.ENSURE_SAFE_GOAL_POSITION`), and not imported,
    because LeRobot needs Python 3.12 and torch.

    A float caps every goal, a dict must name exactly the goals it caps or it is a ValueError,
    and anything else, an int included, is a TypeError. Each goal is then the present reading
    moved toward it by at most the cap, with `min` before `max` as upstream has them, so a NaN
    cap caps nothing. LeRobot also logs every goal it moved; nothing in Roboglot reads that log,
    and it would be printed on every step of every ramp, so it is left out."""
    if isinstance(max_relative_target, float):
        diff_cap: Mapping[str, float] = dict.fromkeys(goal_present_pos, max_relative_target)
    elif isinstance(max_relative_target, dict):
        if not set(goal_present_pos) == set(max_relative_target):
            raise ValueError("max_relative_target keys must match those of goal_present_pos.")
        diff_cap = max_relative_target
    else:
        raise TypeError(max_relative_target)
    safe_goal_positions: dict[str, float] = {}
    for key, (goal_pos, present_pos) in goal_present_pos.items():
        diff = goal_pos - present_pos
        max_diff = diff_cap[key]
        safe_diff = min(diff, max_diff)
        safe_diff = max(safe_diff, -max_diff)
        safe_goal_positions[key] = present_pos + safe_diff
    return safe_goal_positions


@dataclass(frozen=True)
class BusMotor:
    """One row of the bus's motor table (`up.BUS_MOTORS`): the id is the one field Roboglot
    reads, to name the joint a bus error is about."""

    id: int
