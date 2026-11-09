"""The SO-101 follower, simulated: the object `lerobot:real` drives, over a physics world.

`lerobot:real` reaches an arm through one object, a LeRobot follower, and through that
follower's motors bus for the two registers LeRobot's own observation leaves out. This is that
object for the simulator. It carries exactly the surface the real backend touches, the one
`FakeArm` in `tests/test_lerobot_adapter.py` carries, so the connect retries, the travel read
off a calibration, the refusals past it, the rest move, the hold, the release and the
take-hold all run over it unchanged. And it behaves under that surface as the arm does. Part of
that is LeRobot's code, and part is the servo's firmware, which LeRobot's source cannot show,
and a follower that did only what the source says would get the firmware's part wrong:

- **Units.** A body joint reads in degrees and the gripper from 0 to 100, through the
  calibration's ticks exactly as LeRobot converts them (`up.DEGREES_FORMULA`,
  `up.DEGREES_NO_CLAMP`): every reading is a whole encoder tick, the degrees are unbounded and
  the gripper is bounded. `model.py`'s maps then put LeRobot's units on the model.
- **The step cap.** Each send is capped at `config.max_relative_target` from the present
  reading, read off the config as the send is made, by a copy of LeRobot's own function
  (`ensure_safe_goal_position`), its two errors included.
- **The firmware clamp.** The goal is then clamped to the calibrated travel, silently, as the
  servo clamps it (`up.POSITION_LIMITS_CLAMP_GOALS`). A reading is not, so an arm folded or
  started past its travel reads past it.
- **A limp joint's goal.** A goal written to a joint with torque off is kept, and the joint
  drives to it when torque comes back on: the worst case of `up.TORQUE_ENABLE_HOLDS_PRESENT`,
  which the world gives.
- **Only the keys given.** A send writes the goals it names and leaves every other joint's
  standing, which is what lets a hold leave the gripper's squeeze alone.
- **The disconnect.** It drops torque when `config.disable_torque_on_disconnect` says so at the
  moment it runs (`up.SO_DISCONNECT_READS_ITS_CONFIG_LATE`), and a follower collected while
  still connected is disconnected by the same flag, as LeRobot's is (`up.ROBOT_DEL`). What the
  flag starts at is the real backend's to say, so the config has no default for it.
- **The connect.** It opens the port, runs the handshake, and configures, which switches torque
  off on every motor and back on. A handshake that raises closes the port behind it
  (`up.HANDSHAKE_CLOSES_THE_PORT`). A configure that raises leaves it open, and every connect
  after it is refused as already connected until the port is closed through the bus
  (`up.SO_CONNECT_REFUSES_WHILE_OPEN`), which is what the real backend's retry does.
- **The torque calls.** Each tries every motor it names whichever fails, and raises once,
  naming each motor whose write got no reply (`up.TORQUE_TRIED_ON_EVERY_MOTOR`), and a
  disconnect releases the port whatever its torque-off did (`up.DISCONNECT_RELEASES_THE_PORT`).

The faults a bus can be told to have are `faults.py`'s to draw, and they are raised here, from
functions named as upstream's and in upstream's words, so the real backend reads each one as it
would the arm's.

Every read and write holds the world's lock while it touches the world and no longer. Nothing
here steps the physics or renders: a call here is a transaction on the bus, and time is the
clock's. Nothing here imports `mujoco`, and nothing imports LeRobot at all: LeRobot needs
Python 3.12 and torch, and the simulator runs without either.
"""

from __future__ import annotations

import contextlib
import functools
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from pprint import pformat
from typing import Any, TypeVar

from quackd_lerobot import upstream_api as up
from quackd_lerobot.arms import SO_ENCODER_TICKS
from quackd_lerobot.sim.faults import (
    CONFIGURE,
    HANDSHAKE,
    TEMPERATURE_READ,
    TORQUE,
    TORQUE_READ,
    WRITE,
    Fault,
    FaultPlan,
    Faults,
)
from quackd_lerobot.sim.model import LABEL, MotorCalibration
from quackd_lerobot.sim.world import ArmWorld
from quackd_lerobot.verbs import JOINTS

T = TypeVar("T")
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
FULL_SCALE = 100.0
"""The top of that range."""
POSITION = "Present_Position"
GOAL = "Goal_Position"
TORQUE_ENABLE = "Torque_Enable"
TEMPERATURE = "Present_Temperature"
LOCK = "Lock"
"""The registers the simulated bus has: the two LeRobot normalises (`up.BUS_NORMALIZED_DATA`),
the two quackd reads off the bus itself (`up.STS3215_REGISTERS`), and the lock every torque
write is followed by (`up.BUS_ENABLE_TORQUE`)."""
Lost = tuple[str, str]
"""Where a torque fault lands: a motor's name and the register whose write got no reply."""


class DeviceNotConnectedError(ConnectionError):
    """LeRobot's refusal of a call through a port that is not open (`up.NOT_CONNECTED`)."""


class DeviceAlreadyConnectedError(ConnectionError):
    """LeRobot's refusal of a connect through a port that is open
    (`up.SO_CONNECT_REFUSES_WHILE_OPEN`)."""


@dataclass
class SimFollowerConfig:
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
    cap caps nothing. LeRobot also logs every goal it moved; nothing in quackd reads that log,
    and a rehearsal would print it on every step of every ramp, so it is left out."""
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
class SimMotor:
    """One row of the bus's motor table (`up.BUS_MOTORS`): the id is the one field quackd
    reads, to name the joint a bus error is about."""

    id: int


class SimBus:
    """The follower's motors bus: the port, the motor table, the torque writes and register
    reads the real backend makes through it, and the conversion between the calibration's
    ticks and LeRobot's units, which is LeRobot's bus's own."""

    def __init__(
        self,
        world: ArmWorld,
        calibration: dict[str, MotorCalibration],
        motor_ids: Mapping[str, int],
        port: str,
        faults: Faults,
    ) -> None:
        self.world = world
        self.calibration = calibration
        """The follower's own dict, as LeRobot hands its follower's to the bus."""
        self.motors = {name: SimMotor(int(motor_ids[name])) for name in JOINTS}
        self.port = port
        self._open = False
        """Whether the port is open, which is all LeRobot's bus knows of it
        (`up.BUS_IS_CONNECTED`): there is no port handler and no busy flag (`up.NO_BUSY_FLAG`)."""
        self._faults = faults

    @property
    def is_connected(self) -> bool:
        return self._open

    def _require_open(self) -> None:
        if not self.is_connected:
            raise DeviceNotConnectedError(f"{BUS_CLASS} is not connected. Run `.connect()` first.")

    # ── the port ────────────────────────────────────────────────────────────────────────

    def connect(self, handshake: bool = True) -> None:
        """Open the port, then run the handshake, and close the port again behind a handshake
        that raises before raising it as it was (`up.HANDSHAKE_CLOSES_THE_PORT`)."""
        if self.is_connected:
            raise DeviceAlreadyConnectedError(f"{BUS_CLASS} is already connected.")
        self._open = True
        if not handshake:
            return
        try:
            self._handshake()
        except Exception:
            self._open = False
            raise

    def _handshake(self) -> None:
        """`up.BUS_HANDSHAKE`, under upstream's name, which is how the real backend tells a
        failure in here, where nothing is written, from one in `configure()`: a ping per motor.

        A handshake fault loses one motor's ping, which upstream's check reports as that motor
        missing, and an arm whose reads are lost has dropped off the bus, so every motor is
        missing."""
        fault = self._faults.next(HANDSHAKE)
        names = list(self.motors)
        if self._faults.reads_lost:
            missing = names
        elif fault is not None:
            missing = [fault.motor(names)]
        else:
            return
        raise RuntimeError(self._motor_check_failed(missing))

    def _motor_check_failed(self, missing: Sequence[str]) -> str:
        """What upstream's motor check raises, line for line (`up.HANDSHAKE_NAMES_THE_ID`), with
        the model number an SO-101's servos answer with (`up.STS3215_MODEL_NUMBER`)."""
        expected = {motor.id: up.STS3215_MODEL for motor in self.motors.values()}
        lost = {self.motors[name].id for name in missing}
        found = {n: model for n, model in expected.items() if n not in lost}
        lines = [f"{BUS_CLASS} motor check failed on port '{self.port}':", "\nMissing motor IDs:"]
        lines.extend(f"  - {n} (expected model: {expected[n]})" for n in expected if n in lost)
        lines.append("\nFull expected motor list (id: model_number):")
        lines.append(pformat(expected, indent=4, sort_dicts=False))
        lines.append("\nFull found motor list (id: model_number):")
        lines.append(pformat(found, indent=4, sort_dicts=False))
        return "\n".join(lines)

    def disconnect(self, disable_torque: bool = True) -> None:
        """`up.BUS_DISCONNECT`: torque off on every motor first only when asked, then the port
        shut whatever the torque-off did (`up.DISCONNECT_RELEASES_THE_PORT`). A port that is not
        open is passed over without a word, as upstream's is."""
        if not self.is_connected:
            return
        try:
            if disable_torque:
                self.disable_torque(num_retry=DISCONNECT_RETRIES)
        finally:
            self._open = False

    @property
    def is_calibrated(self) -> bool:
        """`up.BUS_IS_CALIBRATED`: the motors' limits read back against the calibration. The
        simulated motors hold whatever calibration the follower was built with, so it is
        calibrated when that names every motor."""
        self._require_open()
        return set(self.calibration) == set(self.motors)

    # ── torque ──────────────────────────────────────────────────────────────────────────

    def enable_torque(
        self, motors: NameOrID | Sequence[NameOrID] | None = None, num_retry: int = 0
    ) -> None:
        """`up.BUS_ENABLE_TORQUE`, how a take-hold picks the arm back up."""
        self._require_open()
        names = self._names(motors)
        self._torque_writes(names, True, num_retry, self._lost_torque_write(names))

    def disable_torque(
        self, motors: NameOrID | Sequence[NameOrID] | None = None, num_retry: int = 0
    ) -> None:
        """`up.BUS_DISABLE_TORQUE`, how a release lets go, and a disconnect that was asked to."""
        self._require_open()
        names = self._names(motors)
        self._torque_writes(names, False, num_retry, self._lost_torque_write(names))

    def _lost_torque_write(self, names: Sequence[str]) -> Lost | None:
        """Where a torque fault lands: the `Torque_Enable` write of one of the motors named,
        lost on the wire, so no reply comes and the write does not take. Every other motor named
        takes its write, since a torque call tries them all (`up.TORQUE_TRIED_ON_EVERY_MOTOR`), so
        that motor alone is left as it was, which is one of the states the real backend allows
        for when a torque call raises. A call that names no motor makes no transaction, so it is
        not counted."""
        if not names:
            return None
        fault = self._faults.next(TORQUE)
        return None if fault is None else (fault.motor(names), TORQUE_ENABLE)

    @contextlib.contextmanager
    def torque_disabled(
        self,
        motors: NameOrID | Sequence[NameOrID] | None = None,
        *,
        lost: Lost | None = None,
    ) -> Iterator[None]:
        """Upstream's context of the same name (`up.CONFIGURE_TORQUE_WRITES_ONCE`): torque off
        on the motors named on the way in and back on in its `finally`, each call tried once. A
        switch off that raises leaves before the `try`, so nothing switches the motors back on.
        `lost` is the one write of the switch back on, if any, whose reply does not come back."""
        self._require_open()
        names = self._names(motors)
        self._torque_writes(names, False, 0)
        try:
            yield
        finally:
            self._torque_writes(names, True, 0, lost)

    def _torque_writes(
        self,
        names: Sequence[str],
        on: bool,
        num_retry: int,
        lost: Lost | None = None,
    ) -> None:
        """`Torque_Enable` and then `Lock` on each motor, in the order named, as rustypot's
        `set_torque` writes them, every motor tried whichever fails, and then the one error
        upstream raises, naming each motor whose write got no reply in the order of their ids
        (`up.TORQUE_TRIED_ON_EVERY_MOTOR`).

        `lost` is the one write, if any, whose reply does not come back. A lost `Torque_Enable`
        leaves that motor as it was; a lost `Lock` comes after its `Torque_Enable` went through.
        Each of these writes waits for its status packet, as a ping and a read do, so an arm
        whose reads are lost answers none of them: every motor fails, and nothing is written.
        A name the table does not know is a KeyError before anything is written, as upstream's
        id lookup is."""
        ids = {name: self.motors[name].id for name in names}
        failed: list[str] = []
        for name in names:
            if self._faults.reads_lost or lost == (name, TORQUE_ENABLE):
                failed.append(name)
                continue
            self.world.set_torque(name, on)
            if lost == (name, LOCK):
                failed.append(name)
        if failed:
            details = ", ".join(
                f"'{name}' (motor ID {ids[name]}): {NO_STATUS}"
                for name in sorted(set(failed), key=lambda name: ids[name])
            )
            raise ConnectionError(
                f"Failed to {'enable' if on else 'disable'} torque after {num_retry + 1} tries on "
                f"{details}."
            )

    # ── registers ───────────────────────────────────────────────────────────────────────

    def _names(self, motors: NameOrID | Sequence[NameOrID] | None) -> list[str]:
        """The motors a call names, resolved as upstream resolves them (`up.BUS_MOTORS`): None
        is every motor in the table's order, a name is itself, an id is the motor the table
        gives that id, a sequence is each of those in its own order, and anything else is a
        TypeError."""
        if motors is None:
            return list(self.motors)
        if isinstance(motors, str):
            return [motors]
        if isinstance(motors, int):
            return [self._id_to_name(motors)]
        if isinstance(motors, Sequence):
            return [m if isinstance(m, str) else self._id_to_name(m) for m in motors]
        raise TypeError(motors)

    def _id_to_name(self, motor_id: int) -> str:
        """The motor the table gives `motor_id`, and a KeyError for an id it gives none."""
        return {motor.id: name for name, motor in self.motors.items()}[motor_id]

    def sync_read(
        self,
        data_name: str,
        motors: NameOrID | Sequence[NameOrID] | None = None,
        *,
        normalize: bool = True,
        num_retry: int = 0,
    ) -> dict[str, Any]:
        """`up.BUS_SYNC_READ`, one transaction for every motor named: the positions, normalised
        or as ticks, or the torque or temperature register, raw whatever `normalize` says
        (`up.BUS_NORMALIZED_DATA`).

        A read that gets no reply raises upstream's sync read error, every motor it read listed
        by id and then the first of them, whose reply is the first that failed when nothing at
        all came back (`up.BUS_SYNC_READ_ERROR`): a register fault on its own read, and every
        read once the reads are lost."""
        self._require_open()
        names = self._names(motors)
        kind = {TORQUE_ENABLE: TORQUE_READ, TEMPERATURE: TEMPERATURE_READ}.get(data_name)
        if data_name != POSITION and kind is None:
            raise ValueError(
                f"{LABEL} the simulated bus reads {POSITION}, {TORQUE_ENABLE} and {TEMPERATURE}, "
                f"not {data_name!r}."
            )
        fault = self._faults.next(kind) if kind is not None else None
        if fault is not None or self._faults.reads_lost:
            ids = [self.motors[name].id for name in names]
            first = f"motor {ids[0]}: " if ids else ""
            raise ConnectionError(
                f"Failed to sync read '{data_name}' from motor IDs {ids} after {num_retry + 1} "
                f"tries. {first}{NO_STATUS}"
            )
        if data_name == TORQUE_ENABLE:
            torque = self.world.torques()
            return {name: int(torque[name]) for name in names}
        if data_name == TEMPERATURE:
            return {name: self.world.temperature(name) for name in names}
        positions = self.world.positions()
        ticks = {name: self._tick(name, positions[name]) for name in names}
        if not normalize:
            return ticks
        return {name: self._normalize(name, tick) for name, tick in ticks.items()}

    def sync_write(
        self,
        data_name: str,
        values: Mapping[str, float],
        *,
        normalize: bool = True,
        num_retry: int = 0,
    ) -> None:
        """The goal positions, all in one packet, as ticks through LeRobot's conversion, then
        each clamped to its calibrated travel as the servo's firmware clamps it
        (`up.POSITION_LIMITS_CLAMP_GOALS`), and written to the world together.

        A write fault is a packet that never went out, so nothing is written, and upstream's
        sync write error says so (`up.BUS_SYNC_READ_ERROR`): a sync write waits for no reply,
        so a lost reply cannot fail it. That is also why it raises nothing once the reads are
        lost: the packet goes out, and no motor on a bus that answers nothing takes it."""
        self._require_open()
        if data_name != GOAL:
            raise ValueError(f"{LABEL} the simulated bus writes {GOAL}, not {data_name!r}.")
        ticks = {
            name: self._unnormalize(name, value) if normalize else int(value)
            for name, value in values.items()
        }
        if self._faults.next(WRITE) is not None:
            ids = [self.motors[name].id for name in ticks]
            raise ConnectionError(
                f"Failed to sync write '{data_name}' to motor IDs {ids} after {num_retry + 1} "
                f"tries. {NOT_SENT}"
            )
        if self._faults.reads_lost:
            return
        self.world.set_goals({name: self._goal(name, tick) for name, tick in ticks.items()})

    # ── units ───────────────────────────────────────────────────────────────────────────

    def _range(self, name: str) -> tuple[int, int]:
        cal = self.calibration.get(name)
        if cal is None:
            raise RuntimeError(
                f"{LABEL} the simulated arm has no calibration for {name}, so nothing converts "
                "its ticks. Build it with a calibration that names all six motors."
            )
        if cal.range_min == cal.range_max:  # up.CALIBRATION_EQUAL_RANGE
            raise ValueError(f"Invalid calibration for motor '{name}': min and max are equal.")
        return cal.range_min, cal.range_max

    def _normalize(self, name: str, tick: int) -> float:
        """A tick in LeRobot's units, as `MotorsBus._normalize` computes it
        (`up.DEGREES_FORMULA`): degrees from the middle of the travel, unbounded, for a body
        joint, and the gripper's share of its travel, bounded to it and turned round by its
        drive mode."""
        lo, hi = self._range(name)
        if name == GRIPPER:
            bounded = min(hi, max(lo, tick))
            norm = (bounded - lo) / (hi - lo) * FULL_SCALE
            return FULL_SCALE - norm if self.calibration[name].drive_mode else norm
        return (tick - (lo + hi) / 2) * 360 / MAX_RES

    def _unnormalize(self, name: str, value: float) -> int:
        """A goal in LeRobot's units as the tick `MotorsBus._unnormalize` writes: the gripper
        bounded to 0..100 first, a body joint not at all (`up.DEGREES_NO_CLAMP`), and both
        truncated to a whole tick by `int()` as upstream truncates them."""
        lo, hi = self._range(name)
        if name == GRIPPER:
            val = FULL_SCALE - value if self.calibration[name].drive_mode else value
            bounded = min(FULL_SCALE, max(0.0, val))
            return int((bounded / FULL_SCALE) * (hi - lo) + lo)
        return int((value * MAX_RES / 360) + (lo + hi) / 2)

    def _tick(self, name: str, q: float) -> int:
        """The tick a joint at `q` radians on the model reads: the model's angle in LeRobot's
        units (`model.py`'s maps), back through `_normalize`'s formula, to the nearest whole
        tick. The gripper's map is bounded, so its tick is inside its travel."""
        lo, hi = self._range(name)
        value = self.world.arm.joints[name].to_lerobot(q)
        if name == GRIPPER:
            share = value / FULL_SCALE
            if self.calibration[name].drive_mode:
                share = 1.0 - share
            return round(lo + share * (hi - lo))
        return round(value * MAX_RES / 360 + (lo + hi) / 2)

    def _goal(self, name: str, tick: int) -> float:
        """Where a goal tick drives the joint on the model, in radians, once the firmware has
        clamped it to the two limits calibration wrote into the servo."""
        lo, hi = self._range(name)
        clamped = min(max(lo, hi), max(min(lo, hi), tick))
        return self.world.arm.joints[name].to_model(self._normalize(name, clamped))


class SimFollower:
    """What `lerobot:real` drives, over an `ArmWorld`: the follower and its bus.

    Built with the world it moves, the calibration the travel comes from, the path that
    calibration was read from (None for one built from the model), a config
    (`SimFollowerConfig`), and a fault plan, or None for a bus with no faults. The motor table
    is upstream's bus table (`up.SO_MOTOR_IDS`); `motor_ids` swaps in another, as a test does
    to show a joint is named through the table and not by its place in it."""

    def __init__(
        self,
        world: ArmWorld,
        calibration: Mapping[str, MotorCalibration],
        calibration_fpath: str | Path | None,
        config: SimFollowerConfig,
        faults: FaultPlan | None = None,
        *,
        motor_ids: Mapping[str, int] | None = None,
    ) -> None:
        if not config.use_degrees:
            raise ValueError(
                f"{LABEL} the simulated follower reads its body joints in degrees, as quackd "
                "builds every follower; build its config with use_degrees=True."
            )
        if config.cameras:
            raise ValueError(
                f"{LABEL} the simulated follower owns no camera, as quackd builds every "
                "follower; the simulator's cameras stand beside it, so build its config with "
                "cameras={}."
            )
        table = dict(motor_ids if motor_ids is not None else up.SO_MOTOR_IDS)
        if set(table) != set(JOINTS) or len(set(table.values())) != len(table):
            raise ValueError(
                f"{LABEL} a motor table gives each of {', '.join(JOINTS)} an id of its own; "
                f"this one gives {table}."
            )
        self.world = world
        self.config = config
        self.calibration: dict[str, MotorCalibration] = dict(calibration)
        self.calibration_fpath = calibration_fpath
        self.faults = Faults(faults)
        """The calls counted so far and the faults that landed on them."""
        self.bus = SimBus(world, self.calibration, table, config.port, self.faults)

    # ── what the real backend reads ─────────────────────────────────────────────────────

    @property
    def is_connected(self) -> bool:
        """The port's open flag, and not a reply from any motor (`up.SO_IS_CONNECTED`)."""
        return self.bus.is_connected

    @property
    def is_calibrated(self) -> bool:
        return self.bus.is_calibrated

    def _require_open(self) -> None:
        if not self.is_connected:
            raise DeviceNotConnectedError(
                f"{FOLLOWER_CLASS} is not connected. Run `.connect()` first."
            )

    # ── the connect ─────────────────────────────────────────────────────────────────────

    def connect(self, calibrate: bool = True) -> None:
        """`SOFollower.connect` in upstream's order: refused while the port is open, then the
        port and the handshake, which closes the port again behind it when it raises
        (`up.HANDSHAKE_CLOSES_THE_PORT`), then the calibration check, which reads and writes
        nothing here, then `configure()`. Nothing closes the port when the configure raises."""
        if self.is_connected:
            raise DeviceAlreadyConnectedError(f"{FOLLOWER_CLASS} is already connected.")
        self.bus.connect()
        if not self.is_calibrated and calibrate:
            # upstream would ask for a calibration at the keyboard (up.ROBOT_CALIBRATE)
            raise RuntimeError(
                f"{LABEL} the simulated arm cannot be calibrated at the keyboard. Build it with "
                "a calibration that names all six motors."
            )
        self.configure()

    def configure(self) -> None:
        """The one place a connect writes to the motors: torque off on every motor, the
        settings, and torque back on one motor after another, each write tried once
        (`up.CONFIGURE_TORQUE_WRITES_ONCE`). The settings are the model's own, so none is
        written here.

        A configure fault loses the reply to one motor's `Lock` write on the way back on. Every
        motor is tried (`up.TORQUE_TRIED_ON_EVERY_MOTOR`), and that motor's `Torque_Enable` went
        through before its `Lock`, so every motor is holding when the connect fails and LeRobot
        names the one it could not confirm. The real backend cannot tell that from a lost
        `Torque_Enable`, which would leave that motor limp, so it warns a person about either
        (`real.SPLIT_TORQUE`)."""
        fault: Fault | None = self.faults.next(CONFIGURE)
        lost = None if fault is None else (fault.motor(list(self.bus.motors)), LOCK)
        with self.bus.torque_disabled(lost=lost):
            pass

    # ── reads and writes ────────────────────────────────────────────────────────────────

    def get_observation(self) -> dict[str, Any]:
        """Every joint as `'<joint>.pos'` (`up.SO_OBSERVATION_KEYS`), one read of the positions
        with upstream's retries, and nothing else (`up.SO_OBSERVATION_IS_POSITION_ONLY`)."""
        self._require_open()
        self.faults.observed()
        positions = self.bus.sync_read(POSITION, num_retry=self.config.num_read_retries)
        return {f"{name}.pos": value for name, value in positions.items()}

    def send_action(self, action: Mapping[str, Any]) -> dict[str, float]:
        """`SOFollower.send_action`: the `'.pos'` goals it is given and no others, capped at
        `config.max_relative_target` from a fresh read of the positions when there is a cap
        (`up.SO_ACTION_CLAMP`), written in one packet, and returned as they went out
        (`up.SO_SEND_ACTION_RETURN`): capped, and neither clamped nor rounded to a tick, which
        the servo does and nobody reports."""
        self._require_open()
        goal_pos = {
            key.removesuffix(".pos"): val for key, val in action.items() if key.endswith(".pos")
        }
        cap = self.config.max_relative_target
        if cap is not None:
            present_pos = self.bus.sync_read(POSITION, num_retry=self.config.num_read_retries)
            goal_present_pos = {key: (g_pos, present_pos[key]) for key, g_pos in goal_pos.items()}
            goal_pos = ensure_safe_goal_position(goal_present_pos, cap)
        self.bus.sync_write(GOAL, goal_pos)
        return {f"{motor}.pos": val for motor, val in goal_pos.items()}

    def disconnect(self) -> None:
        """`SOFollower.disconnect`: the bus's, dropping torque first when the config says so as
        this runs (`up.SO_DISCONNECT_READS_ITS_CONFIG_LATE`)."""
        self._require_open()
        self.bus.disconnect(self.config.disable_torque_on_disconnect)

    def __del__(self) -> None:
        # up.ROBOT_DEL, as upstream has it: a follower collected while its port is open is
        # disconnected by whatever its config says by then, and anything that raises is
        # swallowed. So a run that never reached its close leaves the simulated arm as it
        # would leave the arm.
        with contextlib.suppress(Exception):
            if self.is_connected:
                self.disconnect()

    # ── the heartbeat ───────────────────────────────────────────────────────────────────

    def heartbeat(self) -> contextlib.AbstractContextManager[None]:
        """Mark every call made inside this, in this thread, as the heartbeat's, which no fault
        a rate draws lands on and no ordinal counts (`faults.py` says why). Once the arm has
        dropped off the bus its reads fail too, as a pulled cable fails every read: the
        observation the loss starts at is seeded, and whether the heartbeat or a verb meets it
        first is timing. Entered in the thread that makes the calls, which for the real
        backend is the worker thread `_call` runs them in."""
        return self.faults.heartbeat()

    def as_heartbeat(self, fn: Callable[..., T]) -> Callable[..., T]:
        """`fn`, with every call it makes on this follower marked as the heartbeat's, in
        whichever thread it runs: what a transport hands its worker thread for a heartbeat's
        reads. Its name is `fn`'s, which is what a wedged call is reported by."""

        @functools.wraps(fn)
        def heartbeat(*args: Any, **kwargs: Any) -> T:
            with self.faults.heartbeat():
                return fn(*args, **kwargs)

        return heartbeat
