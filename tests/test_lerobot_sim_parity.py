"""The real backend over two arms: the fake its tests are written against, and the simulator.

`lerobot:mujoco` runs `lerobot:real`'s own code over `SimFollower`, so whatever that backend
promises about an arm has to hold over the simulated follower as it holds over `FakeArm`. Each
test here runs once over each, with the same synthetic calibration, the same step cap (the
fake's `step`, the follower's `max_relative_target`), the same starting pose and a clock that
moves only when it is slept, which for the simulator also steps the physics by the time slept.
A behaviour that holds over one and not the other is a gap between the fake and the arm the
simulator models, and it is found here rather than on the bench.

One behaviour differs on purpose. What a servo does with its stored goal when torque comes back
is unverified (`upstream_api.TORQUE_ENABLE_HOLDS_PRESENT`): the fake holds where it stands, the
best case, and the simulator drives to the goal it was last written, the worst. The test of it
runs over the simulator alone, and shows the take-hold's order is what keeps the worst case off
a person's hand.

The seeded faults run over the simulator alone. A fake is told what to raise; the point of a
seed is that nobody tells the simulator, and the real backend still says what happened.

No number here comes off an arm. The travel is a share of the stand-in's own stops, which are
the manifest's joint limit, the cap is quackd's own default step, and the motor ids are
upstream's bus table in another order. Nothing is fetched: the arm is the stand-in.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

import pytest

from quackd.transport.base import Intent, TransportError
from quackd_lerobot import JOINTS, LeRobotAdapter
from quackd_lerobot import upstream_api as lr
from quackd_lerobot.arms import SO_ENCODER_TICKS as ENCODER_TICKS
from quackd_lerobot.real import (
    CONNECT_ATTEMPTS,
    MAX_STEP_DEG,
    TORQUE_RETRIES,
    LeRobotReal,
)
from quackd_lerobot.sim import standin
from quackd_lerobot.sim.faults import CONFIGURE, HANDSHAKE, Fault, FaultPlan
from quackd_lerobot.sim.follower import NO_STATUS, SimFollower, SimFollowerConfig
from quackd_lerobot.sim.model import ArmModel, MotorCalibration, load
from quackd_lerobot.sim.world import ArmWorld
from quackd_lerobot.verbs import (
    GRIPPER_CLOSED,
    GRIPPER_OPEN,
    TOL_DEG,
    reachable_rest_goal,
    rest_clip_note,
    worth_saying,
)
from tests.test_lerobot_adapter import FakeArm, FakeBus, SteppedClock, _executor

mujoco = pytest.importorskip("mujoco")

BODY = JOINTS[:-1]
GRIPPER = JOINTS[-1]
STEP = MAX_STEP_DEG
"""The cap on one send for both arms: quackd's own default step."""
TRAVEL_SHARE = 0.5
"""Each body joint's calibrated travel, as a share of the stand-in's stops either side of zero:
narrower than the stops, as a calibration that never saw the arm folded is, so there is room
past the travel for a joint to be placed or recorded in."""
TICK_DEG = 360.0 / (ENCODER_TICKS - 1)
"""One encoder tick in LeRobot's degrees (`upstream_api.DEGREES_FORMULA`)."""
SETTLE_S = 1.0
"""Time enough for a joint of the stand-in to reach a goal a step away and stop there."""
SEEDS = 1000
"""How far to look for a seed that puts a fault where a test needs it."""
PAN, ROLL = "shoulder_pan", "wrist_roll"
"""The two joints placed or recorded past their travel, one past the floor and one past the
ceiling. Both turn about an axis gravity does not pull on with the stand-in upright, so the
simulated arm holds them where it is put, and neither swings a link into the table."""
START = {**dict.fromkeys(BODY, 0.0), GRIPPER: GRIPPER_OPEN}
"""Where both arms stand before connecting: upright, the gripper open."""


@pytest.fixture(scope="module")
def arm() -> ArmModel:
    return load(standin.mjcf(), seed=0)


def _calibration(arm: ArmModel) -> dict[str, MotorCalibration]:
    """A calibration for the stand-in: each body joint's travel `TRAVEL_SHARE` of its stops, off
    centre as a recorded one is, some spans odd so the middle falls between two ticks, and ids
    that are upstream's bus table in another order."""
    ids = list(lr.SO_MOTOR_IDS.values())
    ids = ids[2:] + ids[:2]
    per_deg = (ENCODER_TICKS - 1) / 360.0
    out = {}
    for k, (name, joint) in enumerate(arm.joints.items(), start=1):
        lo, hi = joint.stops
        if name == GRIPPER:
            half = ENCODER_TICKS // 8  # the gripper's travel is its own: 0..100 whatever it is
        else:
            half = math.floor(TRAVEL_SHARE * min(-lo, hi) * per_deg)
        middle = ENCODER_TICKS // 2 + k
        out[name] = MotorCalibration(
            id=ids[k - 1],
            drive_mode=0,
            homing_offset=-k,
            range_min=middle - half,
            range_max=middle + half + k % 2,
        )
    return out


def _travel(cal: MotorCalibration) -> tuple[float, float]:
    """A body joint's travel as LeRobot's degrees formula puts it, centred on zero."""
    half = (cal.range_max - cal.range_min) / 2 * TICK_DEG
    return -half, half


def _past(
    arm: ArmModel, calibration: Mapping[str, MotorCalibration], joint: str, side: int
) -> float:
    """Halfway from the travel's edge to the model's stop on `side` (-1 the floor, +1 the
    ceiling): past the travel, and somewhere the simulated arm can be."""
    edge = _travel(calibration[joint])[side > 0]
    stop = arm.joints[joint].stops[side > 0]
    return (edge + stop) / 2


class PhysicsClock(SteppedClock):
    """A `SteppedClock` whose sleep also steps the world by the time slept: every wait the
    backend makes is time the simulated arm moves in, and no other time passes. The world takes
    its own lock for a step. A test helper: the simulator's own clock is the transport's."""

    def __init__(self, world: ArmWorld) -> None:
        super().__init__()
        self.world = world

    async def sleep(self, seconds: float) -> None:
        if seconds >= self.world.timestep / 2:
            self.world.step(seconds)
        await super().sleep(seconds)


@dataclass
class Rig:
    """One arm, the real backend over it, and the arm's own state to check it against."""

    kind: str
    transport: LeRobotReal
    robot: Any
    clock: SteppedClock
    calibration: dict[str, MotorCalibration]
    world: ArmWorld | None = None
    asked: list[dict[str, float]] = field(default_factory=list)
    """Every action the backend sent, as it asked for it."""
    sent: list[dict[str, float]] = field(default_factory=list)
    """What each send said it wrote (`up.SO_SEND_ACTION_RETURN`)."""

    def reading(self, joint: str) -> float:
        """Where the joint is, from the arm itself rather than through its bus."""
        if self.world is None:
            return float(self.robot.positions[joint])
        return self.world.arm.joints[joint].to_lerobot(self.world.position(joint))

    def torque(self) -> dict[str, bool]:
        if self.world is None:
            return {j: bool(self.robot.torque) or j in self.robot.torque_holdouts for j in JOINTS}
        return self.world.torques()

    def place(self, joint: str, value: float) -> None:
        """Put a limp joint somewhere by hand, as a person does, with no time passing."""
        if self.world is None:
            self.robot.positions[joint] = value
            return
        q = self.world.arm.joints[joint].to_model(value)
        with self.world.locked() as (model, data):
            data.qpos[self.world.arm.joints[joint].qpos] = q
            data.qvel[:] = 0.0
            mujoco.mj_forward(model, data)


def _record(rig: Rig) -> Rig:
    """Wrap the arm's `send_action` so the test reads what was asked and what went out."""
    send = rig.robot.send_action

    def recorded(action: dict[str, float]) -> Any:
        rig.asked.append(dict(action))
        out = send(action)
        rig.sent.append(dict(out))
        return out

    rig.robot.send_action = recorded
    return rig


def _fake(arm: ArmModel, start: Mapping[str, float], **kw: Any) -> Rig:
    calibration = _calibration(arm)
    fake = FakeArm(step=STEP)
    fake.calibration = dict(calibration)
    fake.bus = FakeBus(fake, {j: SimpleNamespace(id=c.id) for j, c in calibration.items()})
    fake.positions.update(start)
    clock = SteppedClock()
    transport = LeRobotReal("COM5", robot=fake, clock=clock, **kw)
    return _record(Rig("fake", transport, fake, clock, calibration))


def _sim(
    arm: ArmModel, start: Mapping[str, float], faults: FaultPlan | None = None, **kw: Any
) -> Rig:
    calibration = _calibration(arm)
    world = ArmWorld(arm, rest_pose=start)
    config = SimFollowerConfig(disable_torque_on_disconnect=True, max_relative_target=STEP)
    follower = SimFollower(
        world,
        calibration,
        None,
        config,
        faults,
        motor_ids={j: c.id for j, c in calibration.items()},
    )
    clock = PhysicsClock(world)
    transport = LeRobotReal("COM5", robot=follower, clock=clock, **kw)
    transport.connect_pause_s = 0.0
    return _record(Rig("sim", transport, follower, clock, calibration, world))


@pytest.fixture(params=["fake", "sim"])
def rig(request: pytest.FixtureRequest, arm: ArmModel) -> Callable[..., Rig]:
    """Build one arm of the kind this run is for, standing at `start`, with the backend over it
    on a stepped clock; any other keyword goes to `LeRobotReal`."""

    def build(start: Mapping[str, float] | None = None, **kw: Any) -> Rig:
        pose = {**START, **(start or {})}
        return (_fake if request.param == "fake" else _sim)(arm, pose, **kw)

    return build


# ── what both arms do ───────────────────────────────────────────────────────────────────


async def test_connect_reads_the_travel_from_the_calibration(rig: Callable[..., Rig]) -> None:
    r = rig()
    manifest = await LeRobotAdapter(r.transport).connect()
    assert manifest.extras["joint_range_deg"]
    for joint, cal in r.calibration.items():
        want = (GRIPPER_CLOSED, GRIPPER_OPEN) if joint == GRIPPER else _travel(cal)
        assert r.transport.joint_range_deg[joint] == pytest.approx(want), joint
    assert all(r.torque().values()), "connecting left a motor limp"
    for joint in BODY:  # nothing moved, and the reading is within a tick of where it stands
        assert r.transport._joints[joint] == pytest.approx(START[joint], abs=TICK_DEG)
    assert r.asked == []


async def test_a_goal_outside_the_travel_is_refused_and_nothing_is_sent(
    rig: Callable[..., Rig],
) -> None:
    r = rig()
    adapter = LeRobotAdapter(r.transport)
    manifest = await adapter.connect()
    past = r.transport.joint_range_deg["elbow_flex"][1] + STEP
    moved = await _executor(adapter, manifest).run_verb(
        "move_joints", {"positions": {"elbow_flex": past}}
    )
    assert not moved.ok and "is outside this arm's calibrated range" in moved.summary, moved.summary
    ack = await r.transport.send_intent(
        Intent(kind="joint", params={"positions": {"elbow_flex": past}})
    )
    assert not ack.accepted and "is outside this arm's calibrated range" in (ack.reason or "")
    assert r.asked == []


async def test_the_step_cap_limits_one_send(rig: Callable[..., Rig]) -> None:
    r = rig()
    await r.transport.connect()
    present = r.transport._joints[PAN]
    goal = r.transport.joint_range_deg[PAN][1] / 2  # many steps away, inside the travel
    ack = await r.transport.send_intent(Intent(kind="joint", params={"positions": {PAN: goal}}))
    assert ack.accepted, ack.reason
    assert r.asked == [{f"{PAN}.pos": goal}], "the backend asked for the whole way"
    assert r.sent == [{f"{PAN}.pos": pytest.approx(present + STEP)}], "one step went out"
    await r.clock.sleep(SETTLE_S)
    assert r.reading(PAN) == pytest.approx(present + STEP, abs=2 * TICK_DEG)
    assert r.reading(GRIPPER) == pytest.approx(START[GRIPPER], abs=TOL_DEG), "the gripper moved"


async def test_a_goal_past_the_travel_stops_at_its_limit_and_the_send_does_not_say_so(
    rig: Callable[..., Rig], arm: ArmModel
) -> None:
    """quackd never sends one (`_send` clips, a hold skips, take-hold refuses), because the
    servo would clamp it without a word. Both arms have to clamp it all the same, so that a
    goal that slips past quackd lands where it would on the arm. The cap is lifted, as
    upstream's default None lifts it, so one send asks for the whole way."""
    r = rig()
    await r.transport.connect()
    r.robot.config.max_relative_target = None
    lo, _ = r.transport.joint_range_deg[PAN]
    past = _past(arm, r.calibration, PAN, -1)
    sent = await r.transport._send({PAN: past}, clip=False)
    assert sent == {PAN: pytest.approx(past)}, "the send reported the servo's clamp"
    await r.clock.sleep(SETTLE_S)
    assert r.reading(PAN) == pytest.approx(lo, abs=TICK_DEG)


async def test_a_rest_pose_past_the_travel_is_clipped_and_the_move_parks_at_its_edge(
    rig: Callable[..., Rig], arm: ArmModel
) -> None:
    calibration = _calibration(arm)
    rest = {
        PAN: _past(arm, calibration, PAN, -1),
        ROLL: _past(arm, calibration, ROLL, +1),
        "elbow_flex": _travel(calibration["elbow_flex"])[1] / 4,
    }
    r = rig(rest_pose=rest)
    await r.transport.connect()
    reachable, clipped = reachable_rest_goal(rest, r.transport.joint_range_deg)
    assert [joint for joint, _, _ in clipped] == [PAN, ROLL]
    result = await r.transport.go_to_rest()
    assert result.how == "arrived", result.reason
    assert result.clipped == worth_saying(clipped)
    assert result.note == rest_clip_note(worth_saying(clipped)) and result.note
    await r.clock.sleep(SETTLE_S)
    for joint, goal in reachable.items():
        assert r.reading(joint) == pytest.approx(goal, abs=TOL_DEG), joint
    # the servo parks at its limit and never past it, whatever the pose asked for
    lo, hi = r.transport.joint_range_deg[PAN]
    assert r.reading(PAN) >= lo - TICK_DEG
    lo, hi = r.transport.joint_range_deg[ROLL]
    assert r.reading(ROLL) <= hi + TICK_DEG


async def test_a_hold_writes_no_goal_for_a_joint_reading_past_its_travel(
    rig: Callable[..., Rig], arm: ArmModel
) -> None:
    folded = _past(arm, _calibration(arm), PAN, -1)
    r = rig(start={PAN: folded})
    await r.transport.connect()
    assert r.transport._joints[PAN] == pytest.approx(folded, abs=TICK_DEG), "a reading is unclamped"
    await r.transport.stop()
    assert r.transport.stop_error is None
    assert r.transport.stop_skipped == (PAN,)
    assert set(r.asked[-1]) == {f"{j}.pos" for j in BODY if j != PAN}, r.asked[-1]
    await r.clock.sleep(SETTLE_S)
    assert r.reading(PAN) == pytest.approx(folded, abs=TICK_DEG), "the hold hauled it to its limit"


async def test_take_hold_refuses_a_joint_placed_past_its_travel(
    rig: Callable[..., Rig], arm: ArmModel
) -> None:
    r = rig()
    await r.transport.connect()
    released = await r.transport.let_go(anywhere=True)
    assert released.how == "released" and released.torque_on == (), released.reason
    assert not any(r.torque().values())
    placed = _past(arm, r.calibration, ROLL, +1)
    r.place(ROLL, placed)
    sends = len(r.asked)
    held = await r.transport.take_hold()
    assert held.how == "refused" and held.outside == (ROLL,), held.reason
    assert held.energised is False
    assert not any(r.torque().values()), "torque came on under a hand"
    assert len(r.asked) == sends, "a goal was written"
    assert r.transport.in_hand
    assert r.reading(ROLL) == pytest.approx(placed)


async def test_a_close_at_rest_lets_go_and_one_away_from_it_keeps_torque(
    rig: Callable[..., Rig], arm: ArmModel
) -> None:
    calibration = _calibration(arm)
    rest = {
        PAN: _travel(calibration[PAN])[0] / 3,
        "elbow_flex": _travel(calibration["elbow_flex"])[1] / 4,
    }
    resting = rig(start=rest, rest_pose=rest)
    await resting.transport.connect()
    await resting.transport.close()
    assert resting.transport.close_note is None
    assert not any(resting.torque().values()), "an arm at rest was left holding"

    away = rig(rest_pose=rest)
    await away.transport.connect()
    await away.transport.close()
    note = away.transport.close_note or ""
    assert "torque was left on" in note and "it will not fall as it stands" in note, note
    assert all(away.torque().values()), "an arm away from rest was let go"


@pytest.mark.parametrize("releases", [False, True])
async def test_the_torque_flag_is_read_when_the_disconnect_runs(
    rig: Callable[..., Rig], releases: bool
) -> None:
    r = rig()
    await r.transport.connect()
    assert r.robot.config.disable_torque_on_disconnect is False, "connect asks for the hold"
    r.robot.config.disable_torque_on_disconnect = releases
    r.robot.disconnect()
    assert not r.robot.is_connected
    assert all(r.torque().values()) is not releases


# ── the stored goal, over the simulator ─────────────────────────────────────────────────


async def test_torque_drives_a_limp_joint_to_its_last_goal_so_take_hold_writes_the_pose_first(
    arm: ArmModel,
) -> None:
    """The worst case of `upstream_api.TORQUE_ENABLE_HOLDS_PRESENT`, through the bus the real
    backend drives. The goal a joint was last written before a release is where it stood then,
    not where a hand has put it since, so torque on its own swings it back out of the hand. A
    goal written while it is limp is the one torque drives to, and that is the take-hold's
    order: the pose where it was put, then torque."""

    async def lifted() -> tuple[Rig, float, float]:
        r = _sim(arm, START)
        await r.transport.connect()
        released = await r.transport.let_go(anywhere=True)
        assert released.how == "released", released.reason
        stood = r.reading(PAN)
        placed = stood + STEP  # a step away, inside the travel
        r.place(PAN, placed)
        return r, stood, placed

    r, stood, placed = await lifted()
    r.robot.bus.enable_torque(num_retry=TORQUE_RETRIES)
    await r.clock.sleep(SETTLE_S)
    assert r.reading(PAN) == pytest.approx(stood, abs=2 * TICK_DEG), "torque held the placed pose"

    r, stood, placed = await lifted()
    await r.transport._send({PAN: placed}, clip=False)
    r.robot.bus.enable_torque(num_retry=TORQUE_RETRIES)
    await r.clock.sleep(SETTLE_S)
    assert r.reading(PAN) == pytest.approx(placed, abs=2 * TICK_DEG), "a limp joint lost its goal"

    r, stood, placed = await lifted()
    held = await r.transport.take_hold()
    assert held.how == "held", held.reason
    await r.clock.sleep(SETTLE_S)
    assert r.reading(PAN) == pytest.approx(placed, abs=2 * TICK_DEG), "take-hold moved the arm"


# ── seeded faults, over the simulator ───────────────────────────────────────────────────


def _seed(spec: str, wanted: Callable[[FaultPlan], bool]) -> FaultPlan:
    """The first seed whose plan does what the test needs, rather than a seed typed in."""
    for seed in range(SEEDS):
        plan = FaultPlan.parse(spec, seed=seed)
        if wanted(plan):
            return plan
    raise AssertionError(f"no seed under {SEEDS} gives {spec} what the test needs")


async def test_a_seeded_configure_fault_fails_one_attempt_and_the_retry_goes_through(
    arm: ArmModel,
) -> None:
    plan = _seed("configure=0.5", lambda p: p.fires(CONFIGURE, 1) and not p.fires(CONFIGURE, 2))
    r = _sim(arm, START, plan)
    await r.transport.connect()
    fault = Fault(CONFIGURE, 1, plan.share(CONFIGURE, 1))
    joint = fault.motor(list(JOINTS))
    n = r.calibration[joint].id
    assert n != JOINTS.index(joint) + 1, "the table's order would name the joint by itself"
    notes = r.transport.connect_notes
    assert len(notes) == 1, notes
    assert notes[0].startswith(
        f"connect attempt 1 of {CONNECT_ATTEMPTS} failed on {joint} (id {n}): "
        f"Failed to enable torque after 1 tries on '{joint}' (motor ID {n}): {NO_STATUS}."
    ), notes[0]
    assert "without a write to any motor" in notes[0]
    assert r.robot.faults.injected == [fault]
    assert all(r.torque().values()), "the retry's configure left a motor limp"


async def test_the_same_seed_meets_the_same_faults_twice(arm: ArmModel) -> None:
    spec = "handshake=0.4,configure=0.4"
    plan = _seed(spec, lambda p: p.fires(HANDSHAKE, 1) and p.fires(CONFIGURE, 1))

    async def rehearse() -> tuple[list[str], str | None, list[Fault]]:
        r = _sim(arm, START, plan)
        refused = None
        try:
            await r.transport.connect()
        except TransportError as e:
            refused = str(e)
        return list(r.transport.connect_notes), refused, list(r.robot.faults.injected)

    first, again = await rehearse(), await rehearse()
    assert first == again
    notes, _, injected = first
    assert notes and injected
    assert notes[0].startswith(f"connect attempt 1 of {CONNECT_ATTEMPTS} failed on "), notes
    assert "motor check failed" in notes[0], "the handshake's fault is the first attempt's"
