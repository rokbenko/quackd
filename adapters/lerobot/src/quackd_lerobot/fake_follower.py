"""The SO-101 follower in memory: what `lerobot:mock` drives, with no physics under it.

`lerobot:mock` is the real backend over this follower, as `lerobot:mujoco` is the real backend
over a simulated one (`sim/follower.py`). It carries the surface `LeRobotReal` touches, the one
`SimFollower` and the tests' `FakeArm` carry, so the connect, the travel read off a calibration,
the refusals past it, the rest move, the hold, the release, the take-hold, a policy segment and
the close all run over it as they run over an arm. Under that surface it is an arm in memory, as
plain as the backend's code allows:

- **The travel.** A calibration in ticks, which the real backend reads the travel off with
  LeRobot's degrees formula (`real.joint_ranges`), so the travel it refuses past is one it
  computed. A goal is clamped into that travel as the servo clamps it
  (`up.POSITION_LIMITS_CLAMP_GOALS`), and a reading is not: a joint placed past its travel reads
  past it.
- **The step cap.** Each send is capped at `config.max_relative_target` from the present
  reading, by LeRobot's own function (`sim.follower.ensure_safe_goal_position`), read off the
  config as the send is made, so a policy segment's cap governs its sends and the verbs' cap
  theirs.
- **A goal lands by the next read.** A goal within the cap is where the joint reads next, as a
  servo with a tick to spare gets there. There are no dynamics, and no whole ticks: a reading
  is the goal that was sent, to the bit.
- **A limp joint does not move.** A goal written with torque off is taken and not driven to,
  then or once torque comes back on: the best case of `up.TORQUE_ENABLE_HOLDS_PRESENT`, as the
  tests' `FakeArm` has it, where the simulator gives the worst. A goal written to a joint in
  `stuck`, a joint seized or held back, is not driven to either.
- **Only the keys given.** A send moves the joints it names and leaves the others where they
  are, which is what lets a hold leave the gripper's squeeze alone.
- **The jaws stop on the object.** Closing with the arm within `near_deg` of where the object
  is, in joint space, stops the gripper at `grip_deg`, short of shut, which is the whole of what
  a real arm knows about holding something. The object goes where the arm goes until the jaws
  open past it.
- **Torque per motor**, written and read on the bus as the backend writes and reads it, with
  the turns a test gives the hand-off to end the ways an arm's can: motors that keep their
  torque through a release (`holdouts`), joints that sag as torque comes on (`slips`), and a
  torque register that stops answering after a release or after a take-hold.
- **The disconnect** drops torque when the config says so as it runs
  (`up.SO_DISCONNECT_READS_ITS_CONFIG_LATE`).

One thing here is the mock's and not the arm's: a read, a send and a torque write answer whether
or not the port is open, where LeRobot's refuse them on a closed one. The mock has always answered
before it was connected and after it was closed, tests drive it that way, and an arm in memory has
no cable to come out. A second connect while the port is open is refused as upstream's is, and so
is closing the bus's port when it is not open.

`FakeCamera` is the view the mock offers, read as the real backend reads a webcam. Nothing here
imports LeRobot or MuJoCo.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np
from PIL import Image, ImageDraw

from quackd.sim2d.render import BALL, FLOOR, HORIZON, SKY, focal_px
from quackd_lerobot import upstream_api as up
from quackd_lerobot.sim.follower import (
    BUS_CLASS,
    FOLLOWER_CLASS,
    GRIPPER,
    MAX_RES,
    TEMPERATURE,
    TORQUE_ENABLE,
    DeviceAlreadyConnectedError,
    DeviceNotConnectedError,
    NameOrID,
    SimFollowerConfig,
    SimMotor,
    SimPortHandler,
    ensure_safe_goal_position,
)
from quackd_lerobot.verbs import GRIPPER_CLOSED, GRIPPER_OPEN, JOINTS

MIDDLE = MAX_RES / 2
"""The tick a calibration's travel is centred on here: the middle of a turn, so the widest travel
there is, a full turn either way, is the whole of the encoder."""


@dataclass(frozen=True)
class FakeCalibration:
    """One motor's entry in a calibration (`up.MOTOR_CALIBRATION`): its id and the two ends of
    its recorded travel, in encoder ticks. As fine as the travel needs, where a file holds whole
    ticks: the mock's travel is a round number of degrees, and whole ticks would put its edges a
    few hundredths of a degree off it."""

    id: int
    range_min: float
    range_max: float
    drive_mode: int = 0
    homing_offset: int = 0


def calibration_for(travel: Mapping[str, tuple[float, float]]) -> dict[str, FakeCalibration]:
    """The calibration whose ticks give each body joint `travel` through LeRobot's degrees
    formula (`up.DEGREES_FORMULA`, `real.joint_ranges`), centred on `MIDDLE`, with the ids of
    the SO follower's bus table (`up.SO_MOTOR_IDS`).

    The formula centres a joint on the middle of the travel calibration recorded, so no
    calibration gives a travel that is not the same either side of zero, and one asked for is a
    ValueError rather than a travel the backend would read differently. The gripper's is the
    whole encoder: LeRobot reads it as 0 to 100 whatever its ticks are."""
    out: dict[str, FakeCalibration] = {}
    for joint in JOINTS:
        motor_id = up.SO_MOTOR_IDS[joint]
        if joint == GRIPPER:
            out[joint] = FakeCalibration(motor_id, 0.0, float(MAX_RES))
            continue
        lo, hi = travel[joint]
        if not (hi > 0 and math.isclose(lo, -hi)):
            raise ValueError(
                f"{joint}'s travel {lo:g}..{hi:g} is not the same either side of zero, and "
                "LeRobot's degrees formula centres every calibrated travel on zero"
            )
        half = hi * MAX_RES / 360.0
        if half > MIDDLE:
            raise ValueError(f"{joint}'s travel {lo:g}..{hi:g} is more than a turn")
        out[joint] = FakeCalibration(motor_id, MIDDLE - half, MIDDLE + half)
    return out


class FakeBus:
    """The follower's motors bus: the port's flags, the motor table, and the torque writes and
    register reads the real backend makes through it (`up.BUS_ENABLE_TORQUE`,
    `up.BUS_DISABLE_TORQUE`, `up.STS3215_REGISTERS`). What a write does to the arm is the
    follower's."""

    def __init__(self, arm: FakeFollower) -> None:
        self.arm = arm
        self.motors = {name: SimMotor(up.SO_MOTOR_IDS[name]) for name in JOINTS}
        self.port_handler = SimPortHandler()

    @property
    def is_connected(self) -> bool:
        """The port's open flag (`up.BUS_IS_CONNECTED`)."""
        return self.port_handler.is_open

    def disconnect(self, disable_torque: bool = True) -> None:
        """`up.BUS_DISCONNECT`: torque off on every motor first only when asked, then the port
        shut, and a port that is not open refused, as upstream's is. The real backend closes the
        port this way between two connect attempts."""
        if not self.is_connected:
            raise DeviceNotConnectedError(f"{BUS_CLASS} is not connected. Run `.connect()` first.")
        if disable_torque:
            self.port_handler.is_using = False
            self.arm.set_torque(JOINTS, on=False)
        self.port_handler.is_open = False

    def enable_torque(
        self, motors: NameOrID | Sequence[NameOrID] | None = None, num_retry: int = 0
    ) -> None:
        """`up.BUS_ENABLE_TORQUE`, how a take-hold picks the arm back up."""
        self.arm.take_hold(self._names(motors))

    def disable_torque(
        self, motors: NameOrID | Sequence[NameOrID] | None = None, num_retry: int = 0
    ) -> None:
        """`up.BUS_DISABLE_TORQUE`, how a release lets go."""
        self.arm.let_go(self._names(motors))

    def sync_read(
        self,
        data_name: str,
        motors: NameOrID | Sequence[NameOrID] | None = None,
        *,
        normalize: bool = True,
        num_retry: int = 0,
    ) -> dict[str, Any]:
        """`up.BUS_SYNC_READ` of the two registers the real backend reads off the bus itself,
        raw whatever `normalize` says (`up.BUS_NORMALIZED_DATA`). A torque register told to go
        quiet raises a ConnectionError with the reason it was given."""
        names = self._names(motors)
        if data_name == TORQUE_ENABLE:
            if self.arm.unread is not None:
                raise ConnectionError(self.arm.unread)
            return {name: int(self.arm.torque[name]) for name in names}
        if data_name == TEMPERATURE:
            return {name: int(self.arm.temperature[name]) for name in names}
        raise ValueError(
            f"the mock's bus reads {TORQUE_ENABLE} and {TEMPERATURE}, not {data_name!r}"
        )

    def _names(self, motors: NameOrID | Sequence[NameOrID] | None) -> list[str]:
        """The motors a call names, as upstream resolves them (`up.BUS_MOTORS`): None is every
        motor in the table's order, a name is itself, an id is the motor the table gives it."""
        if motors is None:
            return list(self.motors)
        if isinstance(motors, str | int):
            motors = [motors]
        ids = {motor.id: name for name, motor in self.motors.items()}
        return [m if isinstance(m, str) else ids[m] for m in motors]


class FakeFollower:
    """What `lerobot:mock` drives: the follower and its bus, over an arm in memory.

    Built with a config (`sim.follower.SimFollowerConfig`, the fields the real backend builds a
    follower with), the travel of each body joint in degrees, which becomes its calibration
    (`calibration_for`), the pose it starts in, where the object is in joint space (`object_at`,
    the three joints that put the jaws round it, or None for nothing to hold), how near those
    joints have to be (`near_deg`), where the jaws stop on it (`grip_deg`) and what every motor's
    temperature reads.

    Everything a test reads or turns is an attribute: `positions` is the arm, in degrees and the
    gripper's 0 to 100, which a test may set as a person places a limp arm; `torque` and
    `temperature` are per motor; `held` is whether the object is between the jaws; `actions` is
    every goal sent, in order, by joint name, as asked for and before the step cap. The turns:
    `stuck` (joints that take a goal and do not move), `holdouts` (motors that keep their torque
    through a release), `slips` (degrees a joint sags by as torque comes on, which the goal
    written straight after does not undo), and `unread_after_release` and `unread_after_hold`
    (the reason the torque register gives for not answering, from that write on)."""

    def __init__(
        self,
        config: SimFollowerConfig,
        *,
        travel: Mapping[str, tuple[float, float]],
        pose: Mapping[str, float],
        object_at: Mapping[str, float] | None,
        near_deg: float,
        grip_deg: float,
        temperature_c: float,
    ) -> None:
        self.config = config
        self.calibration = calibration_for(travel)
        self.calibration_fpath: str | None = None
        """No file: the calibration is the mock's own, and the manifest names none."""
        self.bus = FakeBus(self)
        self.positions: dict[str, float] = {joint: float(pose[joint]) for joint in JOINTS}
        self.torque: dict[str, bool] = dict.fromkeys(JOINTS, True)
        self.temperature: dict[str, float] = dict.fromkeys(JOINTS, float(temperature_c))
        self.object_at = dict(object_at) if object_at is not None else None
        self.near_deg = near_deg
        self.grip_deg = grip_deg
        self.held = False
        self.actions: list[dict[str, float]] = []
        self.stuck: set[str] = set()
        self.holdouts: tuple[str, ...] = ()
        self.slips: dict[str, float] = {}
        self.unread_after_release: str | None = None
        self.unread_after_hold: str | None = None
        self.unread: str | None = None
        """Why the torque register is not answering now, or None while it answers."""
        self._sagged: set[str] = set()
        """Joints that slipped as torque last came on, whose next goal does not reach them."""

    # ── what the real backend reads ─────────────────────────────────────────────────────

    @property
    def is_connected(self) -> bool:
        """The port's open flag, and not a reply from any motor (`up.SO_IS_CONNECTED`)."""
        return self.bus.is_connected

    @property
    def is_calibrated(self) -> bool:
        """Every motor has a calibration, which is the whole of what upstream's check reads back
        (`up.BUS_IS_CALIBRATED`)."""
        return set(self.calibration) == set(self.bus.motors)

    def travel(self, joint: str) -> tuple[float, float]:
        """The limits the servo clamps a goal to, in degrees: the calibration's ticks through
        LeRobot's formula, `(tick - mid) * 360 / 4095` with `mid` halfway between them. The
        gripper's is its 0 to 100."""
        if joint == GRIPPER:
            return GRIPPER_CLOSED, GRIPPER_OPEN
        cal = self.calibration[joint]
        middle = (cal.range_min + cal.range_max) / 2
        return (cal.range_min - middle) * 360 / MAX_RES, (cal.range_max - middle) * 360 / MAX_RES

    def object_near(self) -> bool:
        """The arm is where the object is, near enough for the jaws to close round it."""
        at = self.object_at
        return at is not None and all(
            abs(self.positions[joint] - angle) <= self.near_deg for joint, angle in at.items()
        )

    # ── the connect ─────────────────────────────────────────────────────────────────────

    def connect(self, calibrate: bool = True) -> None:
        """`SOFollower.connect`: refused while the port is open
        (`up.SO_CONNECT_REFUSES_WHILE_OPEN`), then the port, and `configure()`, which switches
        torque off on every motor and back on and so leaves every one of them on. Nothing to
        calibrate: the calibration is here."""
        if self.is_connected:
            raise DeviceAlreadyConnectedError(f"{FOLLOWER_CLASS} is already connected.")
        self.bus.port_handler.is_open = True
        self.set_torque(JOINTS, on=True)

    def disconnect(self) -> None:
        """`SOFollower.disconnect`: torque off on every motor first when the config says so as
        this runs (`up.SO_DISCONNECT_READS_ITS_CONFIG_LATE`), then the port shut. A port that was
        never opened is shut all the same, since the mock answers without one."""
        if self.config.disable_torque_on_disconnect:
            self.set_torque(JOINTS, on=False)
        self.bus.port_handler.is_open = False

    # ── reads and writes ────────────────────────────────────────────────────────────────

    def get_observation(self) -> dict[str, Any]:
        """Every joint as `'<joint>.pos'` (`up.SO_OBSERVATION_KEYS`), and nothing else."""
        return {f"{joint}.pos": angle for joint, angle in self.positions.items()}

    def send_action(self, action: Mapping[str, Any]) -> dict[str, float]:
        """`SOFollower.send_action`: the `'.pos'` goals it is given and no others, capped at
        `config.max_relative_target` from where each joint reads when there is a cap
        (`up.SO_ACTION_CLAMP`), and returned as they went out (`up.SO_SEND_ACTION_RETURN`):
        capped, and not clamped, which the servo does and nobody reports."""
        goals = {
            key.removesuffix(".pos"): float(value)
            for key, value in action.items()
            if key.endswith(".pos")
        }
        self.actions.append(dict(goals))
        cap = self.config.max_relative_target
        if cap is not None:
            goals = ensure_safe_goal_position(
                {joint: (goal, self.positions[joint]) for joint, goal in goals.items()}, cap
            )
        for joint, goal in goals.items():
            self._drive(joint, goal)
        return {f"{joint}.pos": goal for joint, goal in goals.items()}

    def _drive(self, joint: str, goal: float) -> None:
        """One joint toward one goal: nowhere when it is limp, stuck or has just slipped, the jaws
        onto the object where it is between them, and otherwise the goal clamped into the
        travel."""
        if not self.torque[joint] or joint in self.stuck:
            return
        if joint in self._sagged:
            self._sagged.discard(joint)
            return
        lo, hi = self.travel(joint)
        goal = min(hi, max(lo, goal))
        if joint == GRIPPER:
            self._grip(goal)
            return
        self.positions[joint] = goal

    def _grip(self, goal: float) -> None:
        """The gripper toward `goal`: open past `grip_deg` lets go of whatever the jaws held, and
        closing past it with the object between them stops on it, short of shut."""
        if goal > self.grip_deg:
            self.held = False
        elif self.held or (self.positions[GRIPPER] > self.grip_deg and self.object_near()):
            self.held = True
            goal = self.grip_deg
        self.positions[GRIPPER] = goal

    # ── torque ──────────────────────────────────────────────────────────────────────────

    def set_torque(self, names: Sequence[str], *, on: bool) -> None:
        """Torque on or off on every motor named, as a connect's `configure()` and a disconnect
        write it, which nothing here holds out against."""
        for name in names:
            self.torque[name] = on

    def let_go(self, names: Sequence[str]) -> None:
        """A release: torque off on every motor named but the `holdouts`, which keep theirs, and
        the torque register quiet from here on where it was told to be."""
        for name in names:
            if name not in self.holdouts:
                self.torque[name] = False
        self._sagged.clear()
        self.unread = self.unread_after_release

    def take_hold(self, names: Sequence[str]) -> None:
        """A take-hold's torque write: torque on every motor named, each joint in `slips` sagging
        by its offset as it comes on, and the torque register quiet from here on where it was told
        to be. A joint that sagged does not take the next goal written to it, as a servo that
        could not hold the pose the first time does not reach it on the second ask either."""
        for name in names:
            self.torque[name] = True
        for joint, gap in self.slips.items():
            self.positions[joint] += gap
            self._sagged.add(joint)
        self.unread = self.unread_after_hold


class FakeCamera:
    """The view `lerobot:mock` offers, read as the real backend reads a webcam: `connect()`,
    `read_latest()` and `disconnect()` (`up.CAMERA_READ_LATEST`), each from a worker thread.

    A drawing of the table from where the arm is now, `size` pixels a side: sky, floor and the
    orange object `bearing_deg` from straight ahead and `distance_m` away, seen from
    `height_m` up, `radius_m` across. It slides across the frame as the shoulder pans, leaves it
    past 45 degrees either side, and is not drawn while the jaws hold it, nor at all where
    `bearing_deg` is None."""

    def __init__(
        self,
        arm: FakeFollower,
        *,
        size: int,
        bearing_deg: float | None,
        distance_m: float,
        height_m: float,
        radius_m: float,
    ) -> None:
        self.arm = arm
        self.size = size
        self.bearing_deg = bearing_deg
        self.distance_m = distance_m
        self.height_m = height_m
        self.radius_m = radius_m
        self.is_connected = False

    def connect(self) -> None:
        self.is_connected = True

    def disconnect(self) -> None:
        self.is_connected = False

    def read_latest(self) -> np.ndarray:
        """The frame as a webcam hands it over, HxWx3 uint8 RGB."""
        return np.asarray(self.draw())

    def draw(self) -> Image.Image:
        size = self.size
        img = Image.new("RGB", (size, size), SKY)
        draw = ImageDraw.Draw(img)
        horizon = int(size * HORIZON)
        draw.rectangle([0, horizon, size, size], fill=FLOOR)
        if self.bearing_deg is None or self.arm.held:
            return img  # nothing on the table, or it is in the gripper
        rel = math.radians(self.bearing_deg - self.arm.positions["shoulder_pan"])
        if abs(rel) > math.radians(45):
            return img
        f = focal_px(size)
        cx = size / 2 - math.tan(rel) * f
        ground_y = horizon + f * self.height_m / self.distance_m
        r = f * self.radius_m / self.distance_m
        draw.ellipse([cx - r, ground_y - 2 * r, cx + r, ground_y], fill=BALL)
        return img


__all__ = [
    "FakeBus",
    "FakeCalibration",
    "FakeCamera",
    "FakeFollower",
    "calibration_for",
]
