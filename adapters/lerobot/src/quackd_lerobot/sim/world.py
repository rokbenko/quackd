"""The arm's physics world: one model and its state, behind one lock.

What the simulated follower reads and writes lives here, in the model's own units: joint
positions and goals in radians, torque on or off per joint, and a temperature per motor. The
maps between those and what LeRobot reads are `model.py`'s. So is the scene; this module steps
it, starts the arm in its pose, and keeps the truth about the objects on the table.

Every access takes the lock. `mj_step` releases the GIL, and the follower's reads and writes
run on the transport's worker thread while the clock steps the world on the event loop's, so
without it a reader could see a state halfway through a step, or write a goal into one. A step
runs whole physics substeps under it and never a part of one.

Torque off makes that joint's actuator limp and leaves its goal register as it was, so turning
torque back on drives the joint to the goal it held before, wherever the joint has fallen to
since. Whether a real servo does that is the unverified TORQUE_ENABLE_HOLDS_PRESENT, and the
simulator gives the worst case, which is the one quackd's take-hold is written against. Limp
means the actuator's gains are zeroed in the model, so each world steps its own copy of the
loaded model: a joint one world let go of is never limp in the next world built from it.

Ground truth is what a rehearsal is checked against and never what the pilot sees: where each
object is, whether the gripper touches it, holds it between both fingers, or has it up off the
table, now and at its peak over the run. `latch()` snapshots it, so a check reads the scene as
it was when the run ended, before any teardown moved the arm through it.

Nothing here renders, and nothing here imports `mujoco` when the module is imported.
"""

from __future__ import annotations

import contextlib
import copy
import math
import threading
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

import numpy as np

from quackd.transport.base import TransportError
from quackd_lerobot.arms import SO_ENCODER_TICKS
from quackd_lerobot.real import GRIPPER_SETTLE, MAX_STEP_DEG, SETTLE_GAP_S
from quackd_lerobot.sim import upstream_api as so
from quackd_lerobot.sim.model import (
    LABEL,
    PLACE_GAP_M,
    PLACE_TRIES,
    ArmModel,
    GripperMap,
    ModelError,
    object_poses,
    table_spot,
)
from quackd_lerobot.verbs import (
    GRIPPER_S,
    JOINTS,
    TICK_S,
    TOL_DEG,
    UNNAMED,
    joint_at_rest,
    reachable_rest_goal,
)

ROOM_TEMPERATURE_C = 25
"""What every simulated motor's temperature register reads. The model has no heat in it, so
each servo reads the room it would stand in when idle, well under the heat quackd refuses to
move a joint at, and a fault that needs a hot servo has to say so itself. One byte, as the
register is."""
LIFT_MIN_M = 0.01
"""How far an object's centre must rise above where it was laid, off the table and touching
the gripper, before it counts as lifted. Closing on an object can nudge it up a few
millimetres, and that is not a lift."""

START_CLEAR_M = 0.001
"""How far a part of the arm may lie inside the table or another of its own links as the arm
starts, before the start is settled out of it, and how far it may still lie in once settled
before the start is refused: a millimetre. A contact in MuJoCo is soft and always sinks a
little under load, so a part resting on the table or on its neighbour sits a fraction of a
millimetre in, and that is touching, not inside."""
START_SETTLE_S = 1.0
"""How long a start that puts the arm into the table or into itself is settled for, in sim
time, before the world's clock starts: the physics steps with every goal held where the pose
put it, the contacts push the parts out, and the arm comes to rest against them the way it
would against anything. The same second a take-hold lets a released arm fall for
(`transport.PLACE_SETTLE_S`). Settling longer does not help a start still in after it: a part
held against a stop, or pinned by a joint it cannot move, stays where the second left it."""

JAWS_CLEARANCE = 0.2
"""How far off the fixed finger's inner face an object laid between the jaws starts, as a share
of its own half width: clear of the finger, so it starts untouched, and near enough that the
moving finger, closing, presses it into the fixed one rather than pushing it out of the jaws."""

_TABLE, _GRIPPER, _FIXED, _MOVING = 1, 2, 4, 8
"""What a geom is to an object touching it, as bits: the table, anything on the gripper, and
the fixed and the moving finger's pads."""
_PINCH = _FIXED | _MOVING


def _pinched(flags: int) -> bool:
    """Both fingers on it in the same physics step. One finger now and the other a moment
    later is two pushes, not a pinch."""
    return flags & _PINCH == _PINCH


def _lifted(flags: int, lift: float) -> bool:
    return not flags & _TABLE and bool(flags & _GRIPPER) and lift >= LIFT_MIN_M


def _jaws_start(name: str) -> str:
    """What a refusal to lay `name` between the jaws says to do about it: the start that works,
    which a gripper merely left open does not give."""
    return (
        f"Give the robot a rest pose whose open jaws point down at the table around where {name} "
        f"goes, or lay {name} on the table instead."
    )


def _listed(items: list[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


FRAME_ASSUMED = (
    f"The model's joint zeros and signs are an assumption ({so.JOINT_ZERO.name}, "
    f"{so.JOINT_SIGN.name}) until the bench checks them, so the pose may be right on the arm "
    "and the model's frame wrong"
)
"""Why a start in the table or in the arm itself is said and not blamed on the pose: a fold is
read off a real arm, and only the bench can say whether the arm or the model is wrong."""


class WorldError(TransportError):
    """The simulated world cannot go on: its physics diverged, or it has been closed."""


@dataclass(frozen=True)
class ObjectTruth:
    """One object, now."""

    position: tuple[float, float, float]
    quat: tuple[float, float, float, float]
    moved_m: float
    """How far its centre is from where it was laid."""
    lift_m: float
    """How far its centre is above where it was laid; negative if it has fallen off the
    table."""
    on_table: bool
    touching: bool
    """Anything on the gripper touches it."""
    pinched: bool
    """Both fingers touch it."""
    lifted: bool
    """Off the table, touching the gripper, and up at least `LIFT_MIN_M`."""


@dataclass(frozen=True)
class ObjectPeaks:
    """One object, at its most over the run so far."""

    moved_m: float
    lift_m: float
    """The highest it has been while it counted as lifted, and 0 if it never has."""
    touched: bool
    pinched: bool
    """Both fingers touched it at once, at some instant: a finger on each side in turn does
    not count."""
    lifted: bool


@dataclass(frozen=True)
class Truth:
    """What was so at sim time `t`: every object now, and at its peak so far."""

    t: float
    objects: Mapping[str, ObjectTruth]
    peaks: Mapping[str, ObjectPeaks]


class ArmWorld:
    """An arm in its scene, stepping.

    Built from a loaded model, which it copies and never changes, and a rest pose in LeRobot's
    units: the arm starts at that pose held under torque, limited by the model's hard stops, and
    at the model's zero where no pose is given. A stop that truncates the pose leaves a sentence
    in `notes`, and so does a pose that puts the arm into the table or into itself, as it
    starts or where the close parks it at the edge of `travel`, which is settled clear of them
    before the clock starts, or refused where settling cannot clear it (`_settle`). Objects
    start where the model's seed laid them. `travel` is each joint's travel from the arm's
    calibration, in LeRobot's units, and `name` the robot's registered name, which that
    refusal's command carries."""

    def __init__(
        self,
        arm: ArmModel,
        *,
        rest_pose: Mapping[str, float] | None = None,
        name: str | None = None,
        travel: Mapping[str, tuple[float, float]] | None = None,
    ) -> None:
        import mujoco

        self.arm = arm
        self.notes: list[str] = []
        self._mj = mujoco
        self._lock = threading.Lock()
        # Torque off edits the gains in place, so this world's model is its own: a disconnect
        # that let a joint go, as LeRobot's does by default, must not leave the next world
        # built from the same loaded model with a joint that says it holds and falls.
        self._model: Any = copy.copy(arm.model)
        self._data: Any = mujoco.MjData(self._model)
        self._closed = False
        self._gain = np.array(self._model.actuator_gainprm, copy=True)
        self._bias = np.array(self._model.actuator_biasprm, copy=True)
        self._torque = dict.fromkeys(JOINTS, True)
        self._goal: dict[str, float] = {}
        self._diverged = self._warnings()
        self.settled: dict[str, float] = {}
        """Each joint the settle moved (`_settle`), at the angle it came to rest at, in LeRobot's
        units: where the arm starts on it, the goal it holds there, and what the simulator's
        close drives it back to in place of the rest pose, at the edge of its travel where that
        angle is past it. Empty where the pose left the arm clear of the table and of itself."""

        start = self._start(rest_pose)
        for joint_name, q in start.items():
            joint = arm.joints[joint_name]
            self._data.qpos[joint.qpos] = q
            self._data.ctrl[joint.actuator] = q
            self._goal[joint_name] = q
        # The truth is folded in after every physics step, so it is kept in plain lists: for a
        # handful of objects and contacts, numpy's cost per call would be most of the work.
        n = len(arm.objects)
        self._role = [0] * arm.model.ngeom
        self._role[arm.table] |= _TABLE
        for g in arm.gripper_geoms:
            self._role[g] |= _GRIPPER
        self._role[arm.fixed_pad] |= _FIXED
        self._role[arm.moving_pad] |= _MOVING
        self._owner = [-1] * arm.model.ngeom
        for i, geoms in enumerate(arm.object_geoms):
            for g in geoms:
                self._owner[g] = i
        self._bodies = list(arm.object_bodies)
        self._flags = [0] * n
        self._laid = [(0.0, 0.0, 0.0)] * n
        self._peak_moved = [0.0] * n
        self._peak_lift = [0.0] * n
        self._ever_touched = [False] * n
        self._ever_pinched = [False] * n
        self._ever_lifted = [False] * n
        self._latched: dict[str, Truth] = {}
        mujoco.mj_forward(self._model, self._data)
        self._settle(start, name, travel or {})
        self._lay()

    # ── the starting pose ───────────────────────────────────────────────────────────────

    def _start(self, rest_pose: Mapping[str, float] | None) -> dict[str, float]:
        """Each joint's starting angle in radians: the rest pose where it names the joint,
        limited only by the model's stops, and the model's zero, within its stops, elsewhere."""
        pose = dict(rest_pose or {})
        start: dict[str, float] = {}
        for name, joint in self.arm.joints.items():
            if name not in pose:
                start[name] = min(max(0.0, joint.lo), joint.hi)
                continue
            value = float(pose[name])
            if not math.isfinite(value):
                raise ValueError(f"{LABEL} the rest pose's {name} is {value}, not a number.")
            lo, hi = joint.stops
            if not lo <= value <= hi:
                reachable = min(max(value, lo), hi)
                unit = "" if isinstance(joint, GripperMap) else " degrees"
                self.notes.append(
                    f"the rest pose puts {name} at {value:.1f}{unit}, past the model's stop at "
                    f"{reachable:.1f}{unit}, so the simulated arm starts at the stop instead"
                )
            start[name] = min(max(joint.to_model(value), joint.lo), joint.hi)
        return start

    def _settle(
        self,
        start: Mapping[str, float],
        name: str | None,
        travel: Mapping[str, tuple[float, float]],
    ) -> None:
        """Settle an arm that starts inside the table or inside itself out of them, and the pose
        its close parks it in with it (`_settle_parked`), before the clock starts, or refuse it
        where it cannot be. After a forward pass, before the objects are laid.

        A pose is read off a real arm folded on a real table, and the model's frame is an
        assumption (`JOINT_ZERO`, `JOINT_SIGN`), so a fold the arm really rests in can put the
        model's fingers into the table and one link into the next. Started there, the first
        physics step throws the parts apart, a joint pushing back at a goal inside the table
        runs out of force, and the arm never gets back to the pose it started at: every close
        after time had passed stalled short of it and left torque on. So where any part is in
        by more than `START_CLEAR_M`, the physics steps for `START_SETTLE_S` with every goal
        held and the objects out of the way, the contacts push the arm out, and it comes to
        rest against them. Each joint that moved by more than an encoder tick then takes the
        angle it came to rest at, within the model's stops, as its start, its goal and its rest
        (`settled`), the way the real backend parks a joint recorded past its travel at the
        edge of it (`LeRobotReal.rest_reachable`): the arm starts where it can be, and a close
        drives it back there rather than into the table. The objects go back where they were,
        and the clock starts at zero. A note names the contacts and the joints, because the
        fold and the model disagree and only the bench can say which is wrong.

        A start still in by more than `START_CLEAR_M` after the settle is refused, with the
        contacts named. A part held against a stop or pinned by a joint that cannot move stays
        in however long it settles, and an arm started there is jammed: its first move stalls,
        and so does every move after it. A search for some other pose clear of the table would
        start the arm where neither the arm nor the person put it, so the person is asked for
        a pose the model can start at instead."""
        before = self._intrusions()
        if before:
            data = self._data
            self._step_clear(data, before, "as it starts")
            for joint_name, joint in self.arm.joints.items():
                # A contact can push a joint past its stop, which in MuJoCo is soft. The arm
                # starts at the stop then, as a pose recorded past one does (`_start`), so its
                # goal, its rest and the register the follower reads are never past it.
                self._adopt(joint_name, float(data.qpos[joint.qpos]), start)
            self._at_rest()
            if after := self._intrusions():
                raise WorldError(
                    f"{LABEL} the rest pose puts {self._parts(before)} on the model, and "
                    f"{START_SETTLE_S:g} s of settling still leaves {self._parts(after)}, so the "
                    "simulated arm cannot start there: every move from it would stall. "
                    f"{FRAME_ASSUMED}. {self._ask_for_a_pose(name)}"
                )
        parked, edge = self._settle_parked(start, name, travel)
        if before or parked:
            self.notes.append(self._settle_note(before, parked, edge, start))

    def _settle_parked(
        self,
        start: Mapping[str, float],
        name: str | None,
        travel: Mapping[str, tuple[float, float]],
    ) -> tuple[dict[tuple[int, int], float], list[str]]:
        """Settle the pose the close parks the arm in as `_settle` settles the start, and give
        what it put where and the joints it parks at the edge of their travel. Nothing where
        no joint is parked there or the pose is clear.

        A fold is often recorded past the travel the calibration recorded, and the close's rest
        move cannot follow it there: the servo clamps every goal to the travel, so the real
        backend parks such a joint at the edge of it and judges it by the half-line rule
        (`verbs.reachable_rest_goal`), and the simulator runs that code. The fold the arm
        starts in can be clear of the table while the same pose with one joint at that edge is
        not, and then every run that moved that joint ended with its rest move stalled against
        the table, the start's settle notwithstanding. So that pose is settled too, on a copy
        of the state: the edge joints are driven from the fold to the edge at the rest move's
        pace with every other goal held, so the arm meets the table as a rest move would rather
        than starting inside it and being thrown out, and each other joint takes the angle it
        came to rest at, within its travel and the model's stops, as its start, its goal and
        its rest (`settled`): the arm starts where it can be both folded and parked, and the
        close drives it back there.

        The edge joints keep their start and the half-line rule, which is also why the parked
        pose is judged where it came to rest and not with the edge joints at the edge: a
        contact that stops an edge joint short of the edge, on the side of its fold, leaves it
        at rest by that rule, and the close arrives there. Refused where an edge joint came to
        rest on the other side, further than a reached pose may miss by, so that no close could
        call it at rest, or where the start with the angles taken here is in the table or in
        the arm."""
        now = {n: j.to_lerobot(float(self._data.qpos[j.qpos])) for n, j in self.arm.joints.items()}
        goal, clipped = reachable_rest_goal(now, dict(travel))
        edge = [joint for joint, _, _ in clipped]
        if not edge:
            return {}, edge
        parked = self._intrusions(self._posed(goal))
        if not parked:
            return parked, edge
        data = copy.copy(self._data)
        drive = {joint: goal[joint] for joint in edge}
        self._step_clear(data, parked, f"{self._at_the_edge(edge)} as the close parks it", drive)
        came = {n: j.to_lerobot(float(data.qpos[j.qpos])) for n, j in self.arm.joints.items()}
        for joint_name, joint in self.arm.joints.items():
            if joint_name not in goal or joint_name in edge:
                continue
            lo, hi = travel.get(joint_name, (came[joint_name], came[joint_name]))
            self._adopt(joint_name, joint.to_model(min(max(came[joint_name], lo), hi)), start)
        self._at_rest()
        short = [
            f"{joint} pushed back into its travel, at {came[joint]:.1f} degrees against an edge "
            f"at {goal[joint]:.1f}"
            for joint in edge
            if not joint_at_rest(goal[joint], came[joint], now[joint])
        ]
        still = self._intrusions()
        if short or still:
            left = _listed(short) if short else f"{self._parts(still)} where it starts"
            raise WorldError(
                f"{LABEL} {self._at_the_edge(edge)}, where the close's rest move parks the arm, "
                f"the rest pose puts {self._parts(parked)} on the model, and "
                f"{START_SETTLE_S:g} s of settling leaves {left}, so the simulated arm could not "
                f"come back to rest: every run that moved {_listed(edge)} would end with its "
                f"rest move stalled. {FRAME_ASSUMED}. {self._ask_for_a_pose(name)}"
            )
        return parked, edge

    @staticmethod
    def _at_the_edge(edge: list[str]) -> str:
        """`with shoulder_lift at the edge of the travel its calibration recorded`."""
        if len(edge) == 1:
            return f"with {edge[0]} at the edge of the travel its calibration recorded"
        return f"with {_listed(edge)} at the edges of the travel their calibration recorded"

    def _posed(self, pose: Mapping[str, float]) -> Any:
        """A copy of the world's state with the joints of `pose`, in LeRobot's units, at those
        angles within the model's stops and held there, after a forward pass: somewhere to try
        a pose without moving the arm."""
        data = copy.copy(self._data)
        for joint_name, value in pose.items():
            joint = self.arm.joints[joint_name]
            q = min(max(joint.to_model(value), joint.lo), joint.hi)
            data.qpos[joint.qpos] = q
            data.ctrl[joint.actuator] = q
        data.qvel[:] = 0.0
        self._mj.mj_forward(self._model, data)
        return data

    def _step_clear(
        self,
        data: Any,
        parts: Mapping[tuple[int, int], float],
        where: str,
        drive: Mapping[str, float] | None = None,
    ) -> None:
        """Step `data` for `START_SETTLE_S` with every goal held, so the contacts push the arm
        out and it comes to rest against them. Each joint of `drive` is first driven to its
        angle there, in LeRobot's units, at the rest move's pace, `MAX_STEP_DEG` a `TICK_S`, so
        that it meets what is in its way as a rest move does rather than starting inside it.
        The objects are taken out of the scene while it settles, so none of them props it up or
        is thrown by it, and each goes back afterwards where the seed laid it."""
        mj, model = self._mj, self._model
        per_tick = max(1, round(TICK_S / self.timestep))
        ramps = []
        for joint_name, value in (drive or {}).items():
            joint = self.arm.joints[joint_name]
            was = joint.to_lerobot(float(data.ctrl[joint.actuator]))
            steps = max(1, math.ceil(abs(value - was) / MAX_STEP_DEG))
            ramps.append((joint, was, value, steps))
        ticks = max((steps for _, _, _, steps in ramps), default=0)
        objects = sorted({g for geoms in self.arm.object_geoms for g in geoms})
        laid = [
            (np.array(data.qpos[q : q + 7]), v)
            for q, v in zip(self.arm.object_qpos, self.arm.object_dofs, strict=True)
        ]
        contype = np.array(model.geom_contype[objects])
        conaffinity = np.array(model.geom_conaffinity[objects])
        model.geom_contype[objects] = 0
        model.geom_conaffinity[objects] = 0
        try:
            for tick in range(1, ticks + 1):
                for joint, was, value, steps in ramps:
                    at = was + (value - was) * min(1.0, tick / steps)
                    data.ctrl[joint.actuator] = min(max(joint.to_model(at), joint.lo), joint.hi)
                for _ in range(per_tick):
                    mj.mj_step(model, data)
            for _ in range(max(1, round(START_SETTLE_S / self.timestep))):
                mj.mj_step(model, data)
        finally:
            model.geom_contype[objects] = contype
            model.geom_conaffinity[objects] = conaffinity
            for (qpos, v), at in zip(laid, self.arm.object_qpos, strict=True):
                data.qpos[at : at + 7] = qpos
                data.qvel[v : v + 6] = 0.0
        if self._warnings(data) != self._diverged:
            raise WorldError(
                f"{LABEL} the physics diverged settling the arm out of {self._parts(parts)} "
                f"{where}, so the simulator cannot start. Record a rest pose with the arm clear "
                "of the table, or report it with the robot's rest pose."
            )

    def _adopt(self, joint_name: str, q: float, start: Mapping[str, float]) -> None:
        """Start a joint at `q`, within the model's stops. Where that is more than an encoder
        tick from where the pose started it, it is settled there, with its goal there too, and
        otherwise its goal stays where the pose put it."""
        joint = self.arm.joints[joint_name]
        q = min(max(q, joint.lo), joint.hi)
        self._data.qpos[joint.qpos] = q
        moved = abs(q - start[joint_name]) > 2 * math.pi / (SO_ENCODER_TICKS - 1)
        self._goal[joint_name] = self._data.ctrl[joint.actuator] = q if moved else start[joint_name]
        if moved:
            self.settled[joint_name] = joint.to_lerobot(q)
        else:
            self.settled.pop(joint_name, None)

    def _at_rest(self) -> None:
        """The arm at rest where it settled, on a clock that has not started."""
        data = self._data
        data.qvel[:] = 0.0
        data.qacc_warmstart[:] = 0.0
        data.time = 0.0
        self._mj.mj_forward(self._model, data)

    @staticmethod
    def _ask_for_a_pose(name: str | None) -> str:
        return (
            "Give this robot a rest pose the model can start at: quackd robot rest-pose "
            f"{name or UNNAMED} records the model's zero, where the simulated arm starts "
            "without one."
        )

    def _intrusions(self, data: Any = None) -> dict[tuple[int, int], float]:
        """Each part of the arm that is inside the table or inside another of its links by
        more than `START_CLEAR_M`, as `(part, what it is in)` bodies, the table being the world
        body, to how deep the deepest contact between the two goes, in metres, deepest first.
        Of two links, the part is the one further down the arm. The objects are not the arm.
        In the world's state or `data`, after a forward pass."""
        data = self._data if data is None else data
        model = self._model
        n = data.ncon
        objects = {g for geoms in self.arm.object_geoms for g in geoms}
        bodies = model.geom_bodyid
        deepest: dict[tuple[int, int], float] = {}
        for (a, b), dist in zip(
            data.contact.geom[:n].tolist(), data.contact.dist[:n].tolist(), strict=True
        ):
            depth = -float(dist)
            if depth <= START_CLEAR_M or a in objects or b in objects:
                continue
            one, other = int(bodies[a]), int(bodies[b])
            pair = (max(one, other), min(one, other))  # a body's descendants come after it
            deepest[pair] = max(deepest.get(pair, 0.0), depth)
        return dict(sorted(deepest.items(), key=lambda item: -item[1]))

    def _parts(self, intrusions: Mapping[tuple[int, int], float]) -> str:
        """The contacts in words, each body by its name in the model: `hand 30 mm into the
        table and jaw 3 mm into the table` on the stand-in."""
        model = self._model

        def called(body: int) -> str:
            return "the table" if body == 0 else model.body(body).name or f"body {body}"

        return _listed(
            [
                f"{called(part)} {depth * 1000:.0f} mm into {called(into)}"
                for (part, into), depth in intrusions.items()
            ]
        )

    def _settle_note(
        self,
        before: Mapping[tuple[int, int], float],
        parked: Mapping[tuple[int, int], float],
        edge: list[str],
        start: Mapping[str, float],
    ) -> str:
        """The settle in the words of the stop's note: what the pose put where, as it starts
        (`before`) and parked with the `edge` joints at the edge of their travel (`parked`),
        where the arm starts and rests instead, and why nobody can yet say whether the pose or
        the model is wrong.

        Every joint that moved is adopted (`settled`), and the note names those that moved by
        more than a reached pose may miss by (`TOL_DEG`), as the real backend names only the
        clipped joints worth saying (`verbs.worth_saying`)."""
        said = []
        for name, value in self.settled.items():
            joint = self.arm.joints[name]
            was = joint.to_lerobot(start[name])
            if abs(value - was) > TOL_DEG:
                unit = "" if isinstance(joint, GripperMap) else " degrees"
                said.append(f"{name} at {value:.1f}{unit} in place of {was:.1f}")
        where = (
            f"with {_listed(said)}"
            if said
            else f"with every joint within {TOL_DEG:g} degrees of the pose"
        )
        if not parked:
            return (
                f"the rest pose puts {self._parts(before)} on the model, so the simulated arm "
                f"starts where it settles clear of them instead, {where}. {FRAME_ASSUMED}"
            )
        at_edge = (
            f"with {edge[0]} at the edge of its travel"
            if len(edge) == 1
            else f"with {_listed(edge)} at the edges of their travel"
        ) + ", where the close's rest move parks the arm"
        puts = (
            f"the rest pose puts {self._parts(before)} on the model, and {at_edge}, "
            f"{self._parts(parked)}"
            if before
            else f"the rest pose, {at_edge}, puts {self._parts(parked)} on the model"
        )
        return (
            f"{puts}, so the simulated arm starts and rests where it settles against them "
            f"instead, {where}. {FRAME_ASSUMED}"
        )

    # ── time ────────────────────────────────────────────────────────────────────────────

    @property
    def t(self) -> float:
        """Sim time in seconds."""
        with self._lock:
            self._open()
            return float(self._data.time)

    @property
    def timestep(self) -> float:
        """One physics step, in seconds: the finest a step can advance by."""
        return float(self.arm.model.opt.timestep)

    def step(self, dt: float) -> float:
        """Advance by `dt` rounded to whole physics steps, at least one, and say by how much.

        Raises `WorldError` if MuJoCo had to reset a state gone bad, rather than carry on in the
        state it reset to, which is the model's zero with the arm teleported there. A reset
        counts its warning and puts the clock back to zero, and either one gives it away."""
        timestep = self.timestep
        n = round(dt / timestep)
        if n < 1:
            raise ValueError(
                f"{LABEL} a step of {dt} s is shorter than half the model's timestep of "
                f"{timestep} s, so it would advance nothing."
            )
        with self._lock:
            self._open()
            due = float(self._data.time) + (n - 0.5) * timestep
            for _ in range(n):
                self._mj.mj_step(self._model, self._data)
                self._track()
            seen = self._warnings()
            if seen != self._diverged or self._data.time < due:
                self._diverged = seen
                raise WorldError(
                    f"{LABEL} the physics diverged at t={self._data.time:.3f} s and MuJoCo reset "
                    "the arm, so the run cannot go on. Something drove the model past what it "
                    "can take; report it with the task file."
                )
        return n * timestep

    def _warnings(self, data: Any = None) -> tuple[int, ...]:
        w = self._mj.mjtWarning
        kinds = (w.mjWARN_BADQACC, w.mjWARN_BADQPOS, w.mjWARN_BADQVEL)
        data = self._data if data is None else data
        return tuple(int(data.warning[k].number) for k in kinds)

    # ── the joints ──────────────────────────────────────────────────────────────────────

    def _joint(self, name: str) -> Any:
        try:
            return self.arm.joints[name]
        except KeyError:
            raise ValueError(
                f"{LABEL} there is no joint {name!r}; the arm has {', '.join(JOINTS)}."
            ) from None

    def positions(self) -> dict[str, float]:
        """Every joint's angle in radians, in LeRobot's order."""
        with self._lock:
            self._open()
            return {n: float(self._data.qpos[j.qpos]) for n, j in self.arm.joints.items()}

    def position(self, name: str) -> float:
        joint = self._joint(name)
        with self._lock:
            self._open()
            return float(self._data.qpos[joint.qpos])

    def goal(self, name: str) -> float:
        """The joint's goal register in radians, kept whether its torque is on or off."""
        self._joint(name)
        with self._lock:
            self._open()
            return self._goal[name]

    def set_goal(self, name: str, q: float) -> None:
        """Write the joint's goal register. A limp joint keeps it and goes there when its
        torque comes back on."""
        joint = self._joint(name)
        q = float(q)
        if not math.isfinite(q):
            raise ValueError(f"{LABEL} a goal for {name} must be a number, not {q}.")
        with self._lock:
            self._open()
            self._goal[name] = q
            self._data.ctrl[joint.actuator] = q

    def set_goals(self, goals: Mapping[str, float]) -> None:
        """Write several goal registers at once, between two physics steps and never across
        one, as one packet reaches every servo on the bus together."""
        written: dict[str, tuple[Any, float]] = {}
        for name, q in goals.items():
            value = float(q)
            if not math.isfinite(value):
                raise ValueError(f"{LABEL} a goal for {name} must be a number, not {value}.")
            written[name] = (self._joint(name), value)
        with self._lock:
            self._open()
            for name, (joint, value) in written.items():
                self._goal[name] = value
                self._data.ctrl[joint.actuator] = value

    def torque(self, name: str) -> bool:
        self._joint(name)
        with self._lock:
            self._open()
            return self._torque[name]

    def torques(self) -> dict[str, bool]:
        """Every joint's torque, on or off, in LeRobot's order, read at one instant."""
        with self._lock:
            self._open()
            return dict(self._torque)

    def set_torque(self, name: str, on: bool) -> None:
        """Torque on or off for one joint. Off zeroes its actuator's gains, so it pushes on
        nothing and the joint moves only as gravity and whatever touches it move it; on puts
        them back, and the joint drives to its goal register."""
        joint = self._joint(name)
        a = joint.actuator
        with self._lock:
            self._open()
            self._torque[name] = bool(on)
            if on:
                self._model.actuator_gainprm[a] = self._gain[a]
                self._model.actuator_biasprm[a] = self._bias[a]
            else:
                self._model.actuator_gainprm[a] = 0.0
                self._model.actuator_biasprm[a] = 0.0

    def temperature(self, name: str) -> int:
        self._joint(name)
        with self._lock:
            self._open()
            return ROOM_TEMPERATURE_C

    # ── the objects ─────────────────────────────────────────────────────────────────────

    def place_objects(self, seed: int) -> None:
        """Lay every object out again as `seed` lays it, at rest, and start its truth over."""
        poses = object_poses(self.arm.workspace, self.arm.objects, seed)
        with self._lock:
            self._open()
            for i, (x, y, z, yaw) in enumerate(poses):
                self._put(i, (x, y, z), (math.cos(yaw / 2), 0.0, 0.0, math.sin(yaw / 2)))
            self._mj.mj_forward(self._model, self._data)
            self._lay()

    def set_object_pose(
        self,
        name: str,
        position: tuple[float, float, float],
        quat: tuple[float, float, float, float] = (1.0, 0.0, 0.0, 0.0),
    ) -> None:
        """Put one object somewhere, at rest, and make that where it was laid: a scene's setup,
        such as a cube between the jaws, rather than something the arm did."""
        names = [obj.name for obj in self.arm.objects]
        if name not in names:
            raise ValueError(
                f"{LABEL} there is no object {name!r} on the table; there is "
                f"{', '.join(names) or 'nothing'}."
            )
        with self._lock:
            self._open()
            i = names.index(name)
            self._put(i, position, quat)
            self._mj.mj_forward(self._model, self._data)
            self._lay(only=i)

    def place_between_jaws(self, name: str) -> None:
        """Lay one object on the table between the gripper's fingers as they are now, and make
        that where it was laid: the cube a task says the jaws are open around.

        Where is read off the model. The object stands on the table just clear of the fixed
        finger's inner face (`JAWS_CLEARANCE`), toward the moving finger, a box turned square to
        the fingers and a capsule lying across them, so that closing the gripper swings the
        moving finger onto it and presses it into the fixed one. It is refused, saying why,
        where the fixed finger ends above the top of the object, which is not between the jaws
        at all, where the object laid there would touch the gripper, which is jaws open
        narrower than it: the first step of physics would throw it, and where closing the
        gripper never brings the moving finger onto it (`_closing_miss`): jaws that do not
        point down around it, the moving finger swinging over it or past it, which no close
        would ever pick up. Each refusal says the start that works, an arm whose open jaws
        point down at the table around the object."""
        names = [obj.name for obj in self.arm.objects]
        if name not in names:
            raise ValueError(
                f"{LABEL} there is no object {name!r} on the table; there is "
                f"{', '.join(names) or 'nothing'}."
            )
        i = names.index(name)
        obj = self.arm.objects[i]
        table = self.arm.workspace.table_top
        fixed, moving = self.arm.fixed_pad, self.arm.moving_pad
        with self._lock:
            self._open()
            data = self._data
            here = np.array(data.geom_xpos[fixed], dtype=float)
            across = np.array(data.geom_xpos[moving], dtype=float) - here
            across[2] = 0.0
            span = float(np.linalg.norm(across))
            tall = 2 * obj.rest_height
            lowest = float(here[2]) - self._reach(fixed, np.array([0.0, 0.0, 1.0]))
            if lowest - table > tall:
                raise ModelError(
                    f"{LABEL} the scene lays {name} between the jaws, and as the arm starts its "
                    f"fixed finger ends {(lowest - table) * 1000:.0f} mm above the table, over "
                    f"the top of {name}, which stands {tall * 1000:.0f} mm tall. "
                    + _jaws_start(name)
                )
            u = across / span if span > 0 else np.array([1.0, 0.0, 0.0])
            half = obj.size[0]  # a box's half size along u, a capsule's radius across it
            x, y = (
                float(v)
                for v in (here + u * (self._reach(fixed, u) + half * (1 + JAWS_CLEARANCE)))[:2]
            )
            # a box's x axis runs from finger to finger; a capsule lies along its body's x
            yaw = math.atan2(float(u[1]), float(u[0])) + (
                0.0 if obj.shape == "box" else math.pi / 2
            )
            self._put(
                i,
                (x, y, table + obj.rest_height),
                (math.cos(yaw / 2), 0.0, 0.0, math.sin(yaw / 2)),
            )
            self._mj.mj_forward(self._model, self._data)
            self._lay(only=i)
            if span == 0 or self._flags[i] & _GRIPPER:
                raise ModelError(
                    f"{LABEL} the scene lays {name} between the jaws, and as the arm starts they "
                    f"are open narrower than {name}, which would start inside a finger. "
                    + _jaws_start(name)
                )
            if (miss := self._closing_miss(i)) is not None:
                raise ModelError(
                    f"{LABEL} the scene lays {name} between the jaws, and as the arm starts, "
                    f"closing the gripper stops its moving finger {miss * 1000:.1f} mm clear of "
                    f"{name}, which it never touches on the way. " + _jaws_start(name)
                )

    def _closing_miss(self, i: int) -> float | None:
        """How near the moving finger comes to object `i` when the gripper closes from where
        the arm starts, in metres, where it never touches it, or None where it does. Under the
        lock, after a forward pass.

        The close is rehearsed in the physics, on a copy of the state, so nothing in the world
        moves, and with the object alone on the table. The gripper is driven to closed with
        every other goal held, as the `gripper` verb drives it, and stepped until the moving
        finger's pad touches the object, or until the gripper has moved and stopped, two
        readings `SETTLE_GAP_S` apart within `GRIPPER_SETTLE` of each other as the backend
        judges a gripper at rest, and for no longer than the verb gives it (`GRIPPER_S`).
        Physics and not the pad's path swept at the start pose, since the hand gives a little
        under a closing grip: a path that passes a millimetre clear can still pinch, and one
        that passes closer can still miss. The distance said is the pad's from the object as the
        rehearsal stopped, off the model's own collision geometry."""
        mj, model = self._mj, self._model
        gripper = self.arm.gripper
        pad, geoms = self.arm.moving_pad, self.arm.object_geoms[i]
        data = copy.copy(self._data)
        # the jaws and this object alone: the table is laid out clear of both after this
        # (`lay_clear`), so whatever else the seed laid is put out of reach on this copy, each
        # a few of the model's own extents off along x, where nothing of the arm can meet it
        extent, centre = float(model.stat.extent), np.asarray(model.stat.center, dtype=float)
        for j, (q, v) in enumerate(zip(self.arm.object_qpos, self.arm.object_dofs, strict=True)):
            if j != i:
                data.qpos[q : q + 3] = centre + np.array([4 * extent * (j + 1), 0.0, 0.0])
                data.qvel[v : v + 6] = 0.0
        mj.mj_forward(model, data)
        data.ctrl[gripper.actuator] = gripper.closed
        gap = max(1, round(SETTLE_GAP_S / self.timestep))
        first = last = gripper.to_lerobot(float(data.qpos[gripper.qpos]))
        for _ in range(max(1, math.ceil(GRIPPER_S / (gap * self.timestep)))):
            for _ in range(gap):
                mj.mj_step(model, data)
                pairs = data.contact.geom[: data.ncon].tolist()
                if any(pad in pair and bool(geoms & set(pair)) for pair in pairs):
                    return None
            now = gripper.to_lerobot(float(data.qpos[gripper.qpos]))
            # at rest once it has moved, so a slow start is not taken for a stop
            if abs(now - first) >= GRIPPER_SETTLE and abs(now - last) < GRIPPER_SETTLE:
                break
            last = now
        far = float(model.stat.extent)  # no reading is cut short: the model's own size
        return min(float(mj.mj_geomDistance(model, data, pad, g, far, None)) for g in geoms)

    def lay_clear(self, seed: int, *, keep: str | None = None) -> None:
        """Lay again, drawn from `seed`, each object that as the arm starts touches anything but
        the table or lies closer to another than `PLACE_GAP_M`, then make where every object
        lies now where it was laid: the table a run starts on.

        The seed lays the table out knowing nothing of the arm, and an object between the jaws
        is put there after it. So a hand that starts down at the table, or the object moved to
        the jaws, can find another already where it is. The first step of physics would shove
        that one before the pilot did anything, and a check on how far it moved would pass on
        some seeds and not on others. `keep` stays where it is, the object between the jaws,
        and the others make room for it. Refused, naming the object, where `PLACE_TRIES` draws
        find no spot in reach clear of the arm and the rest."""
        names = [obj.name for obj in self.arm.objects]
        if keep is not None and keep not in names:
            raise ValueError(
                f"{LABEL} there is no object {keep!r} on the table; there is "
                f"{', '.join(names) or 'nothing'}."
            )
        rng = np.random.default_rng(seed)
        table = self.arm.workspace.table_top
        with self._lock:
            self._open()
            self._mj.mj_forward(self._model, self._data)
            for i, obj in enumerate(self.arm.objects):
                if obj.name == keep:
                    continue
                tries = 0
                while not self._clear(i):
                    if tries == PLACE_TRIES:
                        raise ModelError(
                            f"{LABEL} there is no room on the table for {obj.name} clear of the "
                            "arm as it starts and of the other objects. Ask for fewer or smaller "
                            "objects."
                        )
                    tries += 1
                    x, y = table_spot(self.arm.workspace, rng)
                    yaw = float(rng.uniform(0.0, math.pi))
                    self._put(
                        i,
                        (x, y, table + obj.rest_height),
                        (math.cos(yaw / 2), 0.0, 0.0, math.sin(yaw / 2)),
                    )
                    self._mj.mj_forward(self._model, self._data)
            self._lay()

    def _clear(self, i: int) -> bool:
        """One object touches nothing but the table, and lies at least `PLACE_GAP_M` from every
        other object's footprint, as the seed lays them. Under the lock, after a forward pass."""
        table, owner = self.arm.table, self._owner
        for a, b in self._data.contact.geom[: self._data.ncon].tolist():
            if (owner[a] == i and b != table) or (owner[b] == i and a != table):
                return False
        positions = self._data.xpos[self._bodies].tolist()
        x, y = positions[i][:2]
        obj = self.arm.objects[i]
        return all(
            math.hypot(x - p[0], y - p[1]) >= obj.footprint + other.footprint + PLACE_GAP_M
            for j, (p, other) in enumerate(zip(positions, self.arm.objects, strict=True))
            if j != i
        )

    def _reach(self, geom: int, direction: np.ndarray) -> float:
        """How far a box geom reaches from its centre along a unit direction: half its extent
        that way, from its half sizes and its rotation now. Under the lock."""
        rot = np.asarray(self._data.geom_xmat[geom], dtype=float).reshape(3, 3)
        return float(np.abs(rot.T @ direction) @ np.asarray(self._model.geom_size[geom]))

    def _put(
        self,
        i: int,
        position: tuple[float, float, float],
        quat: tuple[float, float, float, float],
    ) -> None:
        q, v = self.arm.object_qpos[i], self.arm.object_dofs[i]
        self._data.qpos[q : q + 3] = position
        self._data.qpos[q + 3 : q + 7] = np.asarray(quat, dtype=float) / np.linalg.norm(quat)
        self._data.qvel[v : v + 6] = 0.0

    def _lay(self, only: int | None = None) -> None:
        """Take the objects' present places as where they were laid, and start their peaks
        over. Under the lock, after a forward pass."""
        for i in range(len(self._bodies)) if only is None else (only,):
            x, y, z = (float(v) for v in self._data.xpos[self._bodies[i]])
            self._laid[i] = (x, y, z)
            self._peak_moved[i] = 0.0
            self._peak_lift[i] = 0.0
            self._ever_touched[i] = False
            self._ever_pinched[i] = False
            self._ever_lifted[i] = False
        self._track()

    # ── the truth ───────────────────────────────────────────────────────────────────────

    def _track(self) -> None:
        """Read the objects' state off the data and fold it into the peaks. Under the lock,
        after every physics step."""
        data = self._data
        flags = [0] * len(self._bodies)
        if ncon := data.ncon:
            owner, role = self._owner, self._role
            for a, b in data.contact.geom[:ncon].tolist():
                if (i := owner[a]) >= 0:
                    flags[i] |= role[b]
                if (j := owner[b]) >= 0:
                    flags[j] |= role[a]
        self._flags = flags
        for i, (moved, lift) in enumerate(self._offsets(data.xpos[self._bodies].tolist())):
            if moved > self._peak_moved[i]:
                self._peak_moved[i] = moved
            if flags[i] & _GRIPPER:
                self._ever_touched[i] = True
            if _pinched(flags[i]):
                self._ever_pinched[i] = True
            if _lifted(flags[i], lift):
                self._ever_lifted[i] = True
                if lift > self._peak_lift[i]:
                    self._peak_lift[i] = lift

    def _offsets(self, positions: list[list[float]]) -> list[tuple[float, float]]:
        """How far each object is from where it was laid, and how far above it."""
        return [
            (math.dist(p, laid), p[2] - laid[2])
            for p, laid in zip(positions, self._laid, strict=True)
        ]

    def truth(self) -> Truth:
        """Every object now, and at its peak over the run so far."""
        with self._lock:
            self._open()
            return self._truth()

    def _truth(self) -> Truth:
        data = self._data
        positions = data.xpos[self._bodies].tolist()
        quats = data.xquat[self._bodies].tolist()
        objects: dict[str, ObjectTruth] = {}
        peaks: dict[str, ObjectPeaks] = {}
        for i, (obj, (moved, lift)) in enumerate(
            zip(self.arm.objects, self._offsets(positions), strict=True)
        ):
            f = self._flags[i]
            x, y, z = positions[i]
            qw, qx, qy, qz = quats[i]
            objects[obj.name] = ObjectTruth(
                position=(x, y, z),
                quat=(qw, qx, qy, qz),
                moved_m=moved,
                lift_m=lift,
                on_table=bool(f & _TABLE),
                touching=bool(f & _GRIPPER),
                pinched=_pinched(f),
                lifted=_lifted(f, lift),
            )
            peaks[obj.name] = ObjectPeaks(
                moved_m=self._peak_moved[i],
                lift_m=self._peak_lift[i],
                touched=self._ever_touched[i],
                pinched=self._ever_pinched[i],
                lifted=self._ever_lifted[i],
            )
        return Truth(
            t=float(data.time), objects=MappingProxyType(objects), peaks=MappingProxyType(peaks)
        )

    def latch(self, label: str) -> Truth:
        """Snapshot the truth under `label`, replacing any earlier snapshot of that name.

        Called on the way into a stop or a rest move, before either moves anything, so that a
        check reads the scene as the run left it and not as the teardown did."""
        with self._lock:
            self._open()
            snapshot = self._truth()
            self._latched[label] = snapshot
            return snapshot

    def latched(self, label: str) -> Truth | None:
        with self._lock:
            return self._latched.get(label)

    # ── the rest ────────────────────────────────────────────────────────────────────────

    @contextlib.contextmanager
    def locked(self) -> Iterator[tuple[Any, Any]]:
        """The model and its data, held still, for whatever reads them wholesale: a renderer."""
        with self._lock:
            self._open()
            yield self._model, self._data

    def _open(self) -> None:
        if self._closed:
            raise WorldError(f"{LABEL} the simulator is closed.")

    @property
    def closed(self) -> bool:
        return self._closed

    def close(self) -> None:
        """Let go of the model's state. Anything asked of the world afterwards is refused; its
        latched truth stays readable, because that is what a check reads after the close."""
        with self._lock:
            self._closed = True
            self._data = None
            self._model = None
