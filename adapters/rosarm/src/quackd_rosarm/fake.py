"""A rosbridge with an industrial arm behind it, for `rosarm:mock` and the suite, from reading.

`rosarm:mock` is `rosarm:ws` with roslibpy's four objects replaced by these (`FakeBridge.roslibpy`)
and the wall's clock by one of its own (`FakeClock`), so every line from the verbs down to the calls
roslibpy would make runs as it would against a bridge, and in CI it runs in fake time. What the fake
does is what the sources in `upstream_api.py` say a bridge and each arm's driver do, and never
kinder: a bridge that runs each goal on the thread that reads its client, one that drops a goal its
glob does not match, one with no action server; a JointTrajectoryController with a Humble and a
Jazzy switch for the UR5e, the iisy and the iiwa, whose action server latches the status of its
goals, and MotoROS2's own server for the MotoMini, which publishes none; and the faults a run
meets: readings that stop, a socket that drops, a driver that stops the arm itself, and quackd
going quiet in the middle of a move, which leaves the arm finishing it (`up.ONE_MOVE_WHEN_QUIET`).
It has met no bridge, no controller and no arm (`up.FAKE_FROM_READING`).

Everything here runs on the event loop's thread: a callback roslibpy would hand over on its own
thread is called from inside `FakeClock.sleep`, which is where fake time passes. Nothing passes
while nothing sleeps, so a pilot thinking costs the arm no time at all.
"""

from __future__ import annotations

import asyncio
import base64
import fnmatch
import itertools
import json
import math
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any, Literal

from quackd_rosarm import upstream_api as up
from quackd_rosarm.bodies import ArmBody
from quackd_rosarm.transport import DEFAULT_BRIDGE_NODE
from quackd_rosbridge.ws import Roslibpy

Distro = Literal["humble", "jazzy"]
Callback = Callable[[Any], None]

STEP_S = 0.01
"""One step of fake time: each step moves the arm and publishes what is due."""
YIELD_EVERY = 20
"""Steps between two chances for the event loop to run something else."""
SERVER_WAIT_S = 1.0
"""How long the bridge waits for an action server before it answers that there is none
(`up.NO_ACTION_SERVER`)."""
START_TOLERANCE_RAD = 0.002
"""How far a MotoROS2 goal's first point may lie from where the arm is commanded. MotoROS2 counts
it as 30 pulses (`up.MOTO_START_POINT`), and a pulse's size in radians is the arm's own, which the
pages read do not give: this is the fake's stand-in (`up.MOTO_FIRST_POINT`)."""


class GoalStatus(IntEnum):
    """roslibpy's `GoalStatus`, as the result callbacks hand it over (`up.GOAL_STATUS`)."""

    UNKNOWN = 0
    ACCEPTED = 1
    EXECUTING = 2
    CANCELING = 3
    SUCCEEDED = 4
    CANCELED = 5
    ABORTED = 6


INVALID_GOAL, GOAL_TOLERANCE_VIOLATED = -1, -5
"""`up.MSG_FOLLOW_JOINT_TRAJECTORY`'s two codes the fake answers with."""


def moto_code(ros_code: int, motoros2_code: int) -> int:
    """MotoROS2's error_code: the ROS code times 100000 less its own
    (`up.MOTO_ACCEPTS_THEN_ABORTS`)."""
    return ros_code * 100000 - motoros2_code


MOTO_INIT_TRAJ = {
    201: "Trajectory must contain at least two points.",
    204: "The first point must match the robot's current position.",
    206: "Invalid joint name specified. Check motoros2_config.yaml.",
    207: "Trajectory must contain data for all joints.",
    208: "Invalid time in trajectory.",
    209: "Must call start_traj_mode service",
    210: "Trajectory message contains waypoints that are not strictly increasing in time.",
    211: "Trajectory did not contain position data for all axes.",
    212: "Trajectory did not contain velocity data for all axes.",
    213: "The final point in the trajectory must have zero velocity.",
}
"""`up.MOTO_INIT_TRAJ_CODES`, the ones the fake meets."""
MOTO_NOT_READY = {
    101: "The controller has an active Alarm",
    103: "The controller is in E-Stop",
    106: "Servo power is OFF. Please call start_traj_mode or start_point_queue_mode service",
}
"""`up.MOTO_NOT_READY_CODES`, the ones the fake meets."""
FAIL_TRAJ_CANCEL, FAIL_TRAJ_POSITION, FAIL_TRAJ_TIME, FAIL_TRAJ_ALARM = 300, 301, 302, 303


def minimum_jerk(share: float) -> float:
    """How far along a quintic segment that starts and ends at rest one is, at `share` of its time:
    the shape a JointTrajectoryController gives two points with zero velocities and accelerations
    (`trajectory_goal`)."""
    s = min(1.0, max(0.0, share))
    return s * s * s * (10.0 - 15.0 * s + 6.0 * s * s)


def seconds_of(duration: Mapping[str, Any]) -> float:
    return float(duration.get("sec", 0)) + float(duration.get("nanosec", 0)) * 1e-9


class FakeClock:
    """Fake time, which passes only while something sleeps on it.

    `pace` holds each step for that share of its own length on the wall's clock: 0, the default,
    runs as fast as the loop can, and 1 keeps fake time beside the wall's, for a test whose
    watchers run on the wall's clock, as the run's heartbeat does."""

    def __init__(self, step_s: float = STEP_S, *, pace: float = 0.0) -> None:
        self.t = 0.0
        self.step_s = step_s
        self.pace = pace
        self._hooks: list[Callable[[float, float], None]] = []

    def now(self) -> float:
        return self.t

    def add_hook(self, hook: Callable[[float, float], None]) -> None:
        self._hooks.append(hook)

    def advance(self, seconds: float) -> None:
        """Let `seconds` pass at once, as a sleep would, without the event loop: for a test that
        watches the arm while nobody on the loop is sleeping, quackd gone quiet among them."""
        remaining = seconds
        while remaining > 1e-9:
            dt = min(self.step_s, remaining)
            self.t += dt
            for hook in list(self._hooks):
                hook(self.t, dt)
            remaining -= dt

    async def sleep(self, seconds: float) -> None:
        remaining = seconds
        steps = itertools.count(1)
        while remaining > 1e-9:
            dt = min(self.step_s, remaining)
            self.advance(dt)
            remaining -= dt
            if self.pace > 0:
                await asyncio.sleep(dt * self.pace)
            elif next(steps) % YIELD_EVERY == 0:
                await asyncio.sleep(0)
        await asyncio.sleep(0)


@dataclass
class _Goal:
    """A goal the arm is running, and the callbacks its result goes back through."""

    id: str
    names: list[str]
    start: dict[str, float]
    target: dict[str, float]
    duration_s: float
    resultback: Callback
    feedback: Callback
    elapsed: float = 0.0
    """Trajectory time, which a speed scaling of 0 stops (the UR's pause)."""
    wall: float = 0.0
    """Time since it started, which nothing stops."""


@dataclass
class FakeArm:
    """What every fake arm shares: joints by the controller's names, a goal at most, readings."""

    body: ArmBody
    prefix: str = ""
    lag_s: float = 0.0
    """How far the arm trails its command, as a time constant: a lagging arm arrives late."""
    short_rad: float = 0.0
    """How far short of every goal the arm stops: one that does is one whose own controller says
    it arrived and whose readings say it did not, which quackd's arrival check is for."""
    positions: dict[str, float] = field(default_factory=dict)
    commanded: dict[str, float] = field(default_factory=dict)
    goal: _Goal | None = None
    results: list[tuple[str, int, int, str]] = field(default_factory=list)
    """Every goal that ended: its id, its status, its error_code and its error_string."""
    changed: Callable[[], None] = field(default=lambda: None, repr=False, compare=False)
    """Told when the driver's own status changes, so the bridge publishes it then, as a driver
    publishes a change the moment it has one, and not at the next step of the clock."""

    def __post_init__(self) -> None:
        names = [f"{self.prefix}{joint}" for joint in self.body.joints]
        if not self.positions:
            # mid-travel on every joint, where the description puts its middle
            self.positions = {
                name: (joint.lower + joint.upper) / 2.0
                for name, joint in zip(names, self.body.description, strict=True)
            }
        self.commanded = dict(self.positions)

    @property
    def names(self) -> list[str]:
        return [f"{self.prefix}{joint}" for joint in self.body.joints]

    def readings(self) -> dict[str, float]:
        """What `/joint_states` says: where the arm is, or on the iiwa under its stock open_loop
        what it was last commanded (`up.IIWA_OPEN_LOOP`)."""
        if self.body.readings_are_commands:
            return dict(self.commanded)
        return dict(self.positions)

    def _end(self, status: GoalStatus, code: int, why: str) -> None:
        goal, self.goal = self.goal, None
        if goal is None:
            return
        self.results.append((goal.id, int(status), code, why))
        goal.resultback({"status": status, "values": {"error_code": code, "error_string": why}})

    def _follow(self, dt: float) -> None:
        """The arm after `dt`, following its command: at once, or trailing it by `lag_s`."""
        for name, target in self.commanded.items():
            if self.lag_s <= 0:
                self.positions[name] = target
            else:
                here = self.positions[name]
                self.positions[name] = here + (target - here) * min(1.0, dt / self.lag_s)

    def _command_along(self, goal: _Goal) -> None:
        along = minimum_jerk(goal.elapsed / goal.duration_s if goal.duration_s > 0 else 1.0)
        for name in goal.names:
            start, end = goal.start[name], goal.target[name]
            if self.short_rad and end != start:
                end -= math.copysign(min(self.short_rad, abs(end - start)), end - start)
            self.commanded[name] = start + (end - start) * along

    def hold(self) -> None:
        """Hold where the arm is, as a cancel does on every one of these controllers."""
        self.commanded = dict(self.positions)

    def receive(
        self, goal_id: str, message: Mapping[str, Any], resultback: Callback, feedback: Callback
    ) -> str | None:
        """Take a goal, or say why it is rejected outright. Overridden by each controller."""
        raise NotImplementedError

    def cancel(self, goal_id: str) -> None:
        raise NotImplementedError

    def tick(self, dt: float) -> None:
        raise NotImplementedError

    def status_messages(self) -> dict[str, dict[str, Any]]:
        """What the driver publishes about itself now, by topic."""
        return {}


@dataclass
class FakeJtcArm(FakeArm):
    """A JointTrajectoryController with its stock defaults (`up.JTC_*`), Humble's or Jazzy's, and
    the driver around it: the UR's program, safety mode and speed scaling, KUKA's events."""

    distro: Distro = "jazzy"
    active: bool = True
    """Whether the controller is active: a UR's program playing, a KUKA's robot_manager active."""
    allow_nonzero_end: bool | None = None
    goal_tolerance: float | None = None
    speed_scaling: float = 1.0
    program_running: bool = True
    safety_mode: int = 1
    event: int = 3

    def __post_init__(self) -> None:
        super().__post_init__()
        if self.allow_nonzero_end is None:
            # Humble's default, and KUKA's own config on either (`up.JTC_END_AT_REST`, IISY_JOINTS)
            iisy = self.body.robot_type == "kuka-lbr-iisy-3-r760"
            self.allow_nonzero_end = self.distro == "humble" or iisy
        if self.goal_tolerance is None:
            # the UR's 0.1 rad (`up.UR_TOLERANCES`); every other stock bringup leaves it at 0
            self.goal_tolerance = 0.1 if self.body.robot_type == "ur5e" else 0.0

    def _rejects(self, message: Mapping[str, Any]) -> str | None:
        """Why the controller would reject the goal, in its own log's words, or None."""
        if not self.active:
            return up.JTC_INACTIVE_REJECTS.name
        trajectory = dict(message.get("trajectory") or {})
        names = list(trajectory.get("joint_names") or [])
        points = list(trajectory.get("points") or [])
        if len(names) != len(self.names):
            return "Joints on incoming trajectory don't match the controller joints."
        if not points:
            return "Empty trajectory received."
        if any(name not in self.names for name in names):
            return "Incoming joint doesn't match the controller's joints."
        last = dict(points[-1])
        if not self.allow_nonzero_end and any(
            abs(float(v)) > 1e-7 for v in last.get("velocities") or []
        ):
            return "Velocity of last trajectory point is not zero"
        stamp = dict(dict(trajectory.get("header") or {}).get("stamp") or {})
        if seconds_of(stamp) != 0.0:
            return "Received trajectory with non-zero start time that ends in the past"
        previous = None
        for point in points:
            at = seconds_of(dict(point.get("time_from_start") or {}))
            if previous is not None and at <= previous:
                return "Time between points is not strictly increasing"
            previous = at
            if len(point.get("positions") or []) != len(names):
                return "Mismatch between joint_names size and positions"
            if point.get("effort"):
                return "Trajectories with effort fields are only supported for effort control"
        return None

    def receive(
        self, goal_id: str, message: Mapping[str, Any], resultback: Callback, feedback: Callback
    ) -> str | None:
        if (why := self._rejects(message)) is not None:
            return why
        if self.goal is not None:
            # a new goal ends the running one, as each distribution ends it (`up.JTC_PREEMPTS`)
            if self.distro == "humble":
                self._end(
                    GoalStatus.CANCELED,
                    INVALID_GOAL,
                    "Current goal cancelled due to new incoming action.",
                )
            else:
                self._end(
                    GoalStatus.ABORTED,
                    INVALID_GOAL,
                    "Current goal preempted by new incoming action.",
                )
        trajectory = dict(message["trajectory"])
        names = list(trajectory["joint_names"])
        last = dict(trajectory["points"][-1])
        target = dict(zip(names, (float(v) for v in last["positions"]), strict=True))
        self.goal = _Goal(
            id=goal_id,
            names=names,
            start=dict(self.positions),
            target=target,
            duration_s=seconds_of(dict(last["time_from_start"])),
            resultback=resultback,
            feedback=feedback,
        )
        return None

    def cancel(self, goal_id: str) -> None:
        if self.goal is not None and self.goal.id == goal_id:
            self.hold()
            self._end(GoalStatus.CANCELED, 0, "")

    def deactivate(self) -> None:
        """What a UR program that stops, or a KUKA robot_manager deactivated, does to a goal
        (`up.JTC_DEACTIVATE_ABORTS`), and to every goal after it (`up.JTC_INACTIVE_REJECTS`)."""
        self.active = False
        self.hold()
        if self.goal is not None:
            self._end(GoalStatus.ABORTED, INVALID_GOAL, up.JTC_DEACTIVATE_ABORTS.name)

    def protective_stop(self) -> None:
        """A UR's protective stop: the safety mode changes and the program stops, which deactivates
        the controller. How it reaches the controller is not settled (`up.UR_PROTECTIVE_STOP`);
        this is the reading in which the goal is aborted."""
        self.safety_mode = 3
        self.program_running = False
        self.changed()
        self.deactivate()

    def control_stopped(self) -> None:
        """A KUKA arm's driver stopping external control: CONTROL_STOPPED, then deactivation."""
        self.event = 4
        self.changed()
        self.deactivate()

    def tick(self, dt: float) -> None:
        goal = self.goal
        if goal is not None and self.active:
            goal.elapsed = min(goal.duration_s, goal.elapsed + dt * self.speed_scaling)
            goal.wall += dt
            self._command_along(goal)
        self._follow(dt)
        if goal is None or not self.active:
            return
        goal.feedback(
            {
                "joint_names": goal.names,
                "actual": {"positions": [self.positions[n] for n in goal.names]},
            }
        )
        if goal.elapsed < goal.duration_s:
            return
        # SUCCEEDED at the end time, whatever the joints read where the tolerance is 0, and only
        # within it where it is not: the UR's waits for ever otherwise (`up.JTC_GOAL_TIME`)
        tolerance = self.goal_tolerance or 0.0
        if tolerance <= 0 or all(
            abs(self.positions[n] - goal.target[n]) <= tolerance for n in goal.names
        ):
            self._end(GoalStatus.SUCCEEDED, 0, up.JTC_SUCCEEDS_AT_THE_END.name)

    def status_messages(self) -> dict[str, dict[str, Any]]:
        if self.body.status is None:
            return {}
        if self.body.status.reader == "ur":
            (running, _), (mode, _) = self.body.status.topics
            return {running: {"data": self.program_running}, mode: {"mode": self.safety_mode}}
        topic = self.body.status.topics[0][0]
        return {topic: {"robot_name": "", "event": self.event}}


@dataclass
class FakeMotoRos2Arm(FakeArm):
    """MotoROS2's own action server (`up.MOTO_*`): every goal accepted, an invalid one aborted with
    its code, one goal at a time, and the robot's status a hundred times a second."""

    traj_mode: bool = True
    servo_on: bool = True
    e_stopped: bool = False
    in_error: bool = False
    waiting: list[tuple[str, Mapping[str, Any], Callback, Callback]] = field(default_factory=list)
    """Goals sent while one runs, unanswered until it has ended (`up.MOTO_SINGLE_GOAL`)."""

    @property
    def motion_possible(self) -> bool:
        return self.traj_mode and self.servo_on and not self.e_stopped and not self.in_error

    def _invalid(self, message: Mapping[str, Any]) -> tuple[int, str] | None:
        if not self.motion_possible:
            code = 103 if self.e_stopped else 101 if self.in_error else 106
            if not self.traj_mode and self.servo_on:
                return 209, MOTO_INIT_TRAJ[209]
            return code, MOTO_NOT_READY[code]
        trajectory = dict(message.get("trajectory") or {})
        names = list(trajectory.get("joint_names") or [])
        points = list(trajectory.get("points") or [])
        if len(points) < 2:
            return 201, MOTO_INIT_TRAJ[201]
        if len(names) != len(self.names):
            return 207, MOTO_INIT_TRAJ[207]
        if any(name not in self.names for name in names):
            return 206, MOTO_INIT_TRAJ[206]
        previous = None
        for index, point in enumerate(points):
            if len(point.get("positions") or []) != len(names):
                return 211, MOTO_INIT_TRAJ[211]
            if len(point.get("velocities") or []) != len(names):
                return 212, MOTO_INIT_TRAJ[212]
            at = seconds_of(dict(point.get("time_from_start") or {}))
            if at < 0 or (at == 0 and index != 0):
                return 208, MOTO_INIT_TRAJ[208]
            if previous is not None and at < previous:
                return 210, MOTO_INIT_TRAJ[210]
            previous = at
        if any(abs(float(v)) > 1e-12 for v in points[-1].get("velocities") or []):
            return 213, MOTO_INIT_TRAJ[213]
        first = dict(zip(names, (float(v) for v in points[0]["positions"]), strict=True))
        if any(abs(first[n] - self.commanded[n]) > START_TOLERANCE_RAD for n in names):
            return 204, MOTO_INIT_TRAJ[204]
        return None

    def receive(
        self, goal_id: str, message: Mapping[str, Any], resultback: Callback, feedback: Callback
    ) -> str | None:
        if self.goal is not None:
            self.waiting.append((goal_id, message, resultback, feedback))
            return None
        trajectory = dict(message.get("trajectory") or {})
        self.goal = _Goal(
            goal_id,
            list(trajectory.get("joint_names") or []),
            dict(self.positions),
            {},
            0.0,
            resultback,
            feedback,
        )
        invalid = self._invalid(message)
        if invalid is not None:
            # accepted, and then aborted with why (`up.MOTO_ACCEPTS_THEN_ABORTS`)
            code, why = invalid
            self._end(GoalStatus.ABORTED, moto_code(INVALID_GOAL, code), why)
            return None
        last = dict(trajectory["points"][-1])
        self.goal.target = dict(
            zip(self.goal.names, (float(v) for v in last["positions"]), strict=True)
        )
        self.goal.duration_s = seconds_of(dict(last["time_from_start"]))
        return None

    def cancel(self, goal_id: str) -> None:
        if self.goal is not None and self.goal.id == goal_id:
            self.hold()
            self._end(
                GoalStatus.CANCELED,
                moto_code(GOAL_TOLERANCE_VIOLATED, FAIL_TRAJ_CANCEL),
                up.MOTO_CANCEL.name,
            )

    def alarm(self) -> None:
        """An alarm mid-move: the motion stops and the goal is aborted (`up.MOTO_ALARM_ABORTS`)."""
        self.in_error = True
        self.changed()
        self.hold()
        if self.goal is not None:
            self._end(
                GoalStatus.ABORTED,
                moto_code(INVALID_GOAL, FAIL_TRAJ_ALARM),
                up.MOTO_ALARM_ABORTS.name,
            )

    def _end(self, status: GoalStatus, code: int, why: str) -> None:
        super()._end(status, code, why)
        if self.waiting:
            goal_id, message, resultback, feedback = self.waiting.pop(0)
            self.receive(goal_id, message, resultback, feedback)

    def tick(self, dt: float) -> None:
        goal = self.goal
        if goal is not None and goal.target:
            goal.elapsed = min(goal.duration_s, goal.elapsed + dt)
            goal.wall += dt
            self._command_along(goal)
        self._follow(dt)
        if goal is None or not goal.target:
            return
        goal.feedback(
            {
                "joint_names": goal.names,
                "actual": {"positions": [self.positions[n] for n in goal.names]},
            }
        )
        if goal.elapsed < goal.duration_s or any(
            abs(self.positions[n] - self.commanded[n]) > 1e-4 for n in goal.names
        ):
            return
        tolerance, time_tolerance = 0.01, 0.5  # `up.MOTO_GOAL_TOLERANCE`
        off = {n: abs(self.positions[n] - goal.target[n]) for n in goal.names}
        if any(gap > tolerance for gap in off.values()):
            said = "".join(
                f" [{n}: {gap:.5f} deviation]" for n, gap in off.items() if gap > tolerance
            )
            self._end(
                GoalStatus.ABORTED,
                moto_code(GOAL_TOLERANCE_VIOLATED, FAIL_TRAJ_POSITION),
                "Final position was outside tolerance. Check robot safety-limits that could "
                f"be inhibiting motion.{said}",
            )
        elif abs(goal.wall - goal.duration_s) > time_tolerance:
            self._end(
                GoalStatus.ABORTED,
                moto_code(GOAL_TOLERANCE_VIOLATED, FAIL_TRAJ_TIME),
                "Execution time was outside tolerance.",
            )
        else:
            self._end(GoalStatus.SUCCEEDED, 0, "Trajectory completed successfully!")

    def status_messages(self) -> dict[str, dict[str, Any]]:
        def tri(value: bool) -> dict[str, int]:
            return {"val": 1 if value else 0}  # `up.TRI_STATE`

        topic = "/robot_status"
        return {
            topic: {
                "mode": {"val": 2},
                "e_stopped": tri(self.e_stopped),
                "drives_powered": tri(self.servo_on),
                "motion_possible": tri(self.motion_possible),
                "in_motion": tri(self.goal is not None),
                "in_error": tri(self.in_error),
                "error_codes": [4000] if self.in_error else [],
            }
        }


def description_urdf(body: ArmBody, prefix: str = "") -> str:
    """The robot's description as a bridge would serve it: a chain of revolute joints with the
    limits the arm's own description package gives (`ArmBody.description`)."""
    links = [f'  <link name="{prefix}base_link"/>']
    joints = []
    parent = f"{prefix}base_link"
    for index, joint in enumerate(body.description, 1):
        child = f"{prefix}link_{index}"
        links.append(f'  <link name="{child}"/>')
        joints.append(
            f'  <joint name="{prefix}{joint.name}" type="revolute">\n'
            f'    <parent link="{parent}"/><child link="{child}"/>\n'
            f'    <limit lower="{joint.lower!r}" upper="{joint.upper!r}" effort="10" '
            f'velocity="{joint.velocity!r}"/>\n'
            "  </joint>"
        )
        parent = child
    return f'<robot name="{body.robot_type}">\n' + "\n".join(links + joints) + "\n</robot>\n"


class FakeBridge:
    """rosbridge and rosapi as a client sees them, with one arm behind them.

    Its knobs are the bridges the sources describe: `new_thread` the goal thread parameter, True,
    False (`up.GOAL_THREAD_PARAM`) or None for an answer nobody can read; `actions` False for a
    bridge from before 2.0.0, which answers no goal (`up.ACTIONS_SINCE`); `actions_glob`, which
    drops a goal it does not match without a word (`up.GOAL_DROPPED_BY_GLOB`); `server` False for
    no action server, answered after a second (`up.NO_ACTION_SERVER`); `description` where the
    robot's description is served, on the parameter, on the latched topic or nowhere. `sent` is
    every operation quackd sent, as the bridge saw it."""

    def __init__(
        self,
        body: ArmBody,
        *,
        distro: Distro = "jazzy",
        prefix: str = "",
        new_thread: bool | None = True,
        actions: bool = True,
        actions_glob: tuple[str, ...] | None = None,
        server: bool = True,
        description: Literal["param", "topic"] | None = "param",
        urdf: str | None = None,
        rosapi: bool = True,
        reachable: bool = True,
        clock: FakeClock | None = None,
        **arm: Any,
    ) -> None:
        self.body = body
        self.clock = clock or FakeClock()
        self.new_thread = new_thread
        self.actions = actions
        self.actions_glob = actions_glob
        self.server = server
        self.description = description
        self.urdf = urdf if urdf is not None else description_urdf(body, prefix)
        self.rosapi = rosapi
        self.reachable = reachable
        self.arm: FakeArm = (
            FakeJtcArm(body, prefix=prefix, distro=distro, **arm)
            if body.controller == "jtc"
            else FakeMotoRos2Arm(body, prefix=prefix, **arm)
        )
        self.arm.changed = self._publish_status
        self.connected = False
        self.terminated = False
        """Whether the client was let go of for good: its event loop is over, and it neither
        reconnects nor sends what it held back."""
        self.queued: list[Callable[[], None]] = []
        """What the client was handed while the socket was down, which roslibpy sends once it
        has reconnected by itself (`up.QUEUED_UNTIL_READY`)."""
        self.publishing = True
        self.sent: list[dict[str, Any]] = []
        self._subscribers: dict[str, list[Callback]] = {}
        self._status_heard: dict[str, dict[str, Any]] = {}
        self._held: list[tuple[str, str]] = []
        """Cancels a blocking bridge has not reached yet: they wait for the running goal."""
        self._accepted: dict[str, float] = {}
        """Every goal the arm's server took, and when, for the status it publishes."""
        self._goal_statuses: dict[str, Any] | None = None
        """The status array the server last published, which it latches."""
        self._pending: list[tuple[float, Callback, Any]] = []
        self._ids = itertools.count(1)
        self._since_reading = math.inf
        self.assumptions: tuple[str, ...] = (up.FAKE_FROM_READING.name,)
        """What a transport over this bridge lists among its state's assumptions."""
        self.clock.add_hook(self._tick)

    # ── what roslibpy would build ───────────────────────────────────────────────────────

    def roslibpy(self) -> Roslibpy:
        bridge = self

        class _Ros:
            @property
            def is_connected(self) -> bool:
                return bridge.connected

            def run(self, timeout: float = 5.0) -> None:
                if bridge.terminated:
                    raise RuntimeError("this client's event loop has ended")
                if not bridge.reachable:
                    raise ConnectionRefusedError("nothing answers on the bridge's port")
                bridge.reconnect()

            def close(self, timeout: float = 5.0) -> None:
                bridge.client_gone()

            def terminate(self) -> None:
                bridge.terminated = True
                bridge.queued.clear()
                bridge.client_gone()

        class _Topic:
            def __init__(self, name: str, message_type: str) -> None:
                self.name, self.message_type = name, message_type
                self._callback: Callback | None = None

            def subscribe(self, callback: Callback) -> None:
                bridge.sent.append(
                    {"op": "subscribe", "topic": self.name, "type": self.message_type}
                )
                self._callback = callback
                bridge._subscribers.setdefault(self.name, []).append(callback)
                bridge._latched(self.name, callback)

            def unsubscribe(self) -> None:
                bridge.sent.append({"op": "unsubscribe", "topic": self.name})
                if self._callback in bridge._subscribers.get(self.name, []):
                    bridge._subscribers[self.name].remove(self._callback)

            def publish(self, message: Any) -> None:
                bridge.sent.append({"op": "publish", "topic": self.name, "msg": dict(message)})

            def unadvertise(self) -> None:
                pass

        class _Service:
            def __init__(self, name: str, service_type: str) -> None:
                self.name, self.service_type = name, service_type

            def call(self, args: dict[str, Any], *, timeout: float) -> dict[str, Any]:
                bridge.sent.append({"op": "call_service", "service": self.name, "args": dict(args)})
                return bridge._service(self.name, dict(args))

        class _Actions:
            def __init__(self, name: str, action_type: str) -> None:
                self.name, self.action_type = name, action_type

            def send_goal(
                self,
                goal: dict[str, Any],
                resultback: Callback,
                feedback: Callback,
                errback: Callback,
            ) -> str:
                goal_id = f"send_action_goal:{self.name}:{next(bridge._ids)}"

                def send() -> None:
                    bridge.sent.append(
                        {
                            "op": "send_action_goal",
                            "id": goal_id,
                            "action": self.name,
                            "action_type": self.action_type,
                            "args": json.loads(json.dumps(goal)),
                        }
                    )
                    bridge._goal(self.name, goal_id, goal, resultback, feedback, errback)

                # roslibpy hands the goal over at once, and holds it until the socket is back
                # when it is down (`up.QUEUED_UNTIL_READY`)
                if bridge.connected:
                    send()
                elif not bridge.terminated:
                    bridge.queued.append(send)
                return goal_id

            def cancel_goal(self, goal_id: str) -> None:
                def send() -> None:
                    bridge.sent.append(
                        {"op": "cancel_action_goal", "id": goal_id, "action": self.name}
                    )
                    bridge._cancel(self.name, goal_id)

                if bridge.connected:
                    send()
                elif not bridge.terminated:
                    bridge.queued.append(send)

        return Roslibpy(
            ros=_Ros(),
            topic=lambda _ros, name, message_type: _Topic(name, message_type),
            service=lambda _ros, name, service_type: _Service(name, service_type),
            action=lambda _ros, name, action_type: _Actions(name, action_type),
            version=None,  # no roslibpy here, which is what a manifest's roslibpy_version says
        )

    # ── the bridge ──────────────────────────────────────────────────────────────────────

    def _deliver(self, callback: Callback, payload: Any) -> None:
        if self.connected:
            callback(payload)

    @property
    def goal_status_topic(self) -> str:
        """Where the arm's action server publishes its goals' statuses
        (`up.ACTION_STATUS_TOPIC`)."""
        return self.body.action + up.ACTION_STATUS_TOPIC.name.removeprefix("<action>")

    def _latched(self, topic: str, callback: Callback) -> None:
        """A transient-local publisher's last message, to a subscription made after it: the
        description, the UR's status (`up.UR_STATUS`), and the goals' statuses an action server
        last published (`up.ACTION_STATUS_TOPIC`)."""
        if self.description == "topic" and topic == self.body.description_topic:
            self._deliver(callback, {"data": self.urdf})
        if topic == self.goal_status_topic and self._goal_statuses is not None:
            self._deliver(callback, json.loads(json.dumps(self._goal_statuses)))
        if self.body.status is not None and self.body.status.reader == "ur":
            heard = self._status_heard.get(topic)
            if heard is not None:
                self._deliver(callback, dict(heard))

    def _service(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        if not self.rosapi:
            raise RuntimeError(f"Service {name} does not exist")
        if name == up.SVC_GET_PARAM.name:
            param = str(args.get("name", ""))
            if param == f"{DEFAULT_BRIDGE_NODE}:{up.GOAL_THREAD_PARAM.name}":
                if self.new_thread is None:
                    return {
                        "value": '""',
                        "successful": False,
                        "reason": f"Service {DEFAULT_BRIDGE_NODE}/get_parameters is not available",
                    }
                return {"value": json.dumps(self.new_thread), "successful": True, "reason": ""}
            if self.description == "param" and param == self.body.description_param:
                return {"value": json.dumps(self.urdf), "successful": True, "reason": ""}
            return {"value": '""', "successful": False, "reason": f"Parameter {param} not found"}
        if name == up.SVC_ACTION_SERVERS.name:
            return {"action_servers": [self.body.action] if self.server else []}
        raise RuntimeError(f"Service {name} does not exist")

    def _goal(
        self,
        action: str,
        goal_id: str,
        goal: dict[str, Any],
        resultback: Callback,
        feedback: Callback,
        errback: Callback,
    ) -> None:
        if not self.actions:
            return  # a bridge with no actions answers nothing at all
        if self.actions_glob is not None and not any(
            fnmatch.fnmatch(action, glob) for glob in self.actions_glob
        ):
            return  # dropped with a warning in the bridge's own log, and no result
        if not self.server or action != self.body.action:
            self._later(
                SERVER_WAIT_S,
                errback,
                {"status": GoalStatus.UNKNOWN, "values": up.NO_ACTION_SERVER.name},
            )
            return
        why = self.arm.receive(
            goal_id,
            goal,
            lambda result: self._deliver(resultback, result),
            lambda fb: self._deliver(feedback, fb),
        )
        if why is not None:
            self._deliver(errback, {"status": GoalStatus.UNKNOWN, "values": up.GOAL_REJECTED.name})
            return
        self._accepted.setdefault(goal_id, self.clock.now())
        self._publish_goal_statuses()

    def _cancel(self, action: str, goal_id: str) -> None:
        running = self.arm.goal is not None and self.arm.goal.id == goal_id
        if self.new_thread is False and running:
            # the reader is still inside the goal, so the cancel waits behind it
            # (`up.GOAL_THREAD_PARAM`)
            self._held.append((action, goal_id))
            return
        self.arm.cancel(goal_id)
        self._publish_goal_statuses()

    def _publish_goal_statuses(self) -> None:
        """The status of every goal the arm's server keeps, published whenever one changes and
        latched, as the JointTrajectoryController's server does: the running goal EXECUTING, and
        each ended one as it ended (`up.ACTION_SERVER_PUBLISHES_STATUS`). A server that has taken
        no goal has published nothing, and MotoROS2 publishes none (`up.MOTO_NO_ACTION_STATUS`).
        A cancel ends a goal CANCELED at once here, where the controller shows it CANCELING
        until its next look at the goal, up to a twentieth of a second at its stock
        action_monitor_rate."""
        if self.body.controller != "jtc" or not self._accepted:
            return
        ended = {goal_id: status for goal_id, status, _code, _why in self.arm.results}
        running = self.arm.goal.id if self.arm.goal is not None else None
        statuses = []
        for goal_id, accepted_at in self._accepted.items():
            status = int(GoalStatus.EXECUTING) if goal_id == running else ended.get(goal_id)
            if status is None:
                continue
            uuid_bytes = uuid.uuid5(uuid.NAMESPACE_URL, goal_id).bytes
            statuses.append(
                {
                    "goal_info": {
                        # a uint8[16], which rosbridge hands over in base64 (BINARY_BASE64 in
                        # the binding's refs)
                        "goal_id": {"uuid": base64.b64encode(uuid_bytes).decode("ascii")},
                        "stamp": {
                            "sec": int(accepted_at),
                            "nanosec": int((accepted_at % 1.0) * 1e9),
                        },
                    },
                    "status": status,
                }
            )
        message = {"status_list": statuses}
        if message == self._goal_statuses:
            return
        self._goal_statuses = message
        for callback in list(self._subscribers.get(self.goal_status_topic, [])):
            self._deliver(callback, json.loads(json.dumps(message)))

    def _later(self, seconds: float, callback: Callback, payload: Any) -> None:
        self._pending.append((self.clock.now() + seconds, callback, payload))

    def _tick(self, now: float, dt: float) -> None:
        self.arm.tick(dt)
        if self._held and self.arm.goal is None:
            held, self._held = self._held, []
            for _action, goal_id in held:
                self.arm.cancel(goal_id)  # the goal has ended: there is nothing left to cancel
        self._publish_goal_statuses()
        due = [entry for entry in self._pending if entry[0] <= now]
        self._pending = [entry for entry in self._pending if entry[0] > now]
        for _at, callback, payload in due:
            self._deliver(callback, payload)
        self._since_reading += dt
        if self.publishing and self._since_reading >= 1.0 / self.body.rate_hz - 1e-9:
            self._since_reading = 0.0
            self._publish_readings(now)
        self._publish_status()

    def _publish_readings(self, now: float) -> None:
        readings = self.arm.readings()
        names = list(reversed(list(readings)))  # an order other than the controller's
        nan = self.body.robot_type == "kuka-lbr-iisy-3-r760"  # `up.IISY_NAN_READINGS`
        message = {
            "header": {
                "stamp": {"sec": int(now), "nanosec": int((now % 1.0) * 1e9)},
                "frame_id": "",
            },
            "name": names,
            "position": [readings[n] for n in names],
            "velocity": [math.nan if nan else 0.0 for _ in names],
            "effort": [math.nan if nan else 0.0 for _ in names],
        }
        for callback in list(self._subscribers.get(self.body.joint_states, [])):
            self._deliver(callback, message)

    def _publish_status(self) -> None:
        status = self.body.status
        if status is None:
            return
        for topic, message in self.arm.status_messages().items():
            on_change = status.reader in ("ur", "kuka_events")
            if on_change and self._status_heard.get(topic) == message:
                continue  # published only when it changes (`up.UR_STATUS`, `up.IISY_EVENTS`)
            self._status_heard[topic] = dict(message)
            for callback in list(self._subscribers.get(topic, [])):
                self._deliver(callback, dict(message))

    # ── faults ──────────────────────────────────────────────────────────────────────────

    def quiet(self) -> None:
        """`/joint_states` stops: the driver hangs, or its link to the controller does."""
        self.publishing = False

    def drop(self) -> None:
        """The socket drops: nothing more reaches quackd, and the arm goes on as the bridge's own
        client keeps the goal (`up.CLOSED_CLIENT_CANCELS_NOTHING`)."""
        self.connected = False

    def reconnect(self) -> None:
        """The socket is back, as roslibpy's factory brings it back by itself after a close it
        did not ask for: what was queued meanwhile goes out now (`up.QUEUED_UNTIL_READY`)."""
        if self.terminated:
            return
        self.connected = True
        queued, self.queued = self.queued, []
        for send in queued:
            send()

    def client_gone(self) -> None:
        """quackd goes away, by closing or by vanishing: the bridge finishes the client and
        cancels nothing, so a goal it was running runs to its end (`up.ONE_MOVE_WHEN_QUIET`).
        Called from a worker thread by a close, so it changes one flag and nothing else."""
        self.connected = False

    def operations(self) -> list[str]:
        return [str(entry["op"]) for entry in self.sent]
