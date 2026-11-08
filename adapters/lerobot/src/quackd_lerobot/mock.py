"""`lerobot:mock`: the real backend, driving an arm in memory.

`LeRobotMock` is `LeRobotReal` with three things changed underneath it, as `lerobot:mujoco` is
(`sim/transport.py`). Its follower and its camera are built in memory (`fake_follower.py`), its
clock moves only when something sleeps on it, and its policy is a script. Nearly every line
between those is the real backend's own: the connect, the travel read off a calibration and the
refusals past it, the rest move, the hold, the release and the take-hold, the policy loop a
segment runs in, and the close and what it says. Two things above it are the mock's, both on
purpose. Its close lets go of an arm with no rest pose recorded, as the simulator's does, where
the arm's keeps its torque and says so (`KEEPS_TORQUE_WITHOUT_A_POSE`). And a rest move told to
fail (`rest_fails`) stalls with no hold sent, where the arm's holds where it stalled. A fix to
anything else is a fix on the mock too, and a rehearsal on it says what the arm would say. It was
a hand-written imitation of that code, its hand-off kept in step with the arm's by hand, and every
fix had to be made twice.

What it adds is what a test needs and an arm does not have:

- **An arm in memory.** Six joints starting at `REST`, a travel of `MOCK_RANGES`, an object at
  `OBJECT_AT` in joint space and a camera that draws the table: enough to run every arm verb, the
  executor's gates and the detector offline, in no wall time.
- **A scripted policy** (`MockPolicy`). `pick` reaches for the object and closes the jaws on it,
  and the segment ends once the arm reads as holding, as the arm's does. `manipulate` walks the
  arm toward the object, so a segment ends on its time with the arm partway there.
- **The turns a test gives it.** `hot_joints` read hot. `hold_slips`, `release_holdouts`,
  `release_unread` and `hold_unread` are the follower's turns for the hand-off, and a joint in
  the follower's `stuck` takes its goal and does not move, which the real rest move then calls a
  stall in its own words. `rest_fails` is the one outcome scripted rather than made: a rest move
  that has to drive stalls at once, sends nothing, not even the hold an arm's stall ends on, and
  says the reason it was given, so a test can say why a fold missed, which every caller of the
  rest move has to handle. An arm already at its pose reads `already` whatever it says, since the
  real move reads the arm before it drives.
  `refuse_kinds` and `fail_heartbeat_after` are what the mock transport always took.
- **What a test reads.** `joints`, `actions`, `torque`, `holding`, `intents`, `stops`,
  `heartbeats` and `policy_runs`, and `sequence`, the order the stop, the rest move, the release,
  the take-hold and the close were called in.
"""

from __future__ import annotations

import asyncio
import math
from collections.abc import Mapping

from quackd.adapters.base import HandResult, RestResult
from quackd.transport.base import Ack, HeartbeatError, Intent
from quackd_lerobot.arms import ROBOT_TYPE
from quackd_lerobot.fake_follower import FakeCamera, FakeFollower
from quackd_lerobot.policy.loop import Segment
from quackd_lerobot.policy.runner import Chunk, Features, Observation
from quackd_lerobot.policy.scripted import SWEEP_DEG, SWEEP_JOINT, SWEEP_PERIOD_S
from quackd_lerobot.real import (
    CAMERA_NAME,
    MAX_STEP_DEG,
    POLICY_HZ,
    CameraSpec,
    LeRobotReal,
    joint_ranges,
)
from quackd_lerobot.sim.follower import SimFollowerConfig
from quackd_lerobot.verbs import (
    GRIPPER_CLOSED,
    GRIPPER_OPEN,
    GRIPPER_TOL,
    JOINTS,
    MANIPULATE_S,
    TOL_DEG,
    SegmentEnd,
)

REST = {
    "shoulder_pan": 0.0,
    "shoulder_lift": -90.0,
    "elbow_flex": 90.0,
    "wrist_flex": 0.0,
    "wrist_roll": 0.0,
    "gripper": GRIPPER_OPEN,
}
"""Where the arm starts."""
OBJECT_AT = {"shoulder_pan": 30.0, "shoulder_lift": -20.0, "elbow_flex": 40.0}
"""Where the object is, in joint space: the arm is "there" when these three are close."""
SEGMENT_SHARE = 0.5
"""How much of the way to the object a scripted `manipulate` segment of the default length
(`MANIPULATE_S`) walks the arm: some of it, so a rehearsal sees the arm move and still has to
look at where it ended."""
NEAR_DEG = 10.0
"""How close those three joints have to be for the jaws to close round the object."""
MOCK_CAM_HEIGHT_M = 0.3
MOCK_OBJECT_R = 0.03

RANGE_DEG = 100.0
"""A plausible travel for a calibrated body joint. A real one is read off the arm's own
calibration file at connect and is different on every arm."""
FULL_TURN_DEG = 180.0
"""`wrist_roll` is the joint LeRobot's calibration leaves as a full turn."""
MOCK_RANGES: dict[str, tuple[float, float]] = {
    joint: (
        (GRIPPER_CLOSED, GRIPPER_OPEN)
        if joint == "gripper"
        else (-FULL_TURN_DEG, FULL_TURN_DEG)
        if joint == "wrist_roll"
        else (-RANGE_DEG, RANGE_DEG)
    )
    for joint in JOINTS
}
"""The travel the follower's calibration gives each joint, which the real backend reads back
off it at connect. Read as each mock is built, so a test can narrow it, the same either side of
zero, as every calibrated travel is."""

GRIP_ON_OBJECT = 30.0
"""Where the gripper stops when something is between the jaws."""
MOCK_TEMPERATURE_C = 30.0
"""A fixed reading, so the heat refusal has something to refuse offline. A mock has no
servos and this number measures nothing."""
HOT_JOINT_C = 65.0
"""What a joint named in `hot_joints` reads: above the real backend's `HOT_C`, below the
servo's own cut-off."""
SCRIPT_RATE = "lerobot:mock's script, at the verbs' tick"
"""Where the scripted policy's rate comes from (`POLICY_HZ`), as a refusal quotes a rate's
source."""
CALL_S = 10.0
"""How long one call to the mock's bus may take before the real backend calls it wedged
(`LeRobotReal.timeout_s`, a second on the arm). An answer from memory takes microseconds, and a
budget that long keeps a machine busy with other work from ever wedging the mock, while a call of
quackd's own that never comes back is still found."""


class MockClock:
    """The mock's time (`real.Clock`): seconds that pass only when something sleeps on them, as
    a mock's always have, so a ramp or a segment of many seconds costs no wall time. Every sleep
    moves it on by what it asked for, and `sleeps` keeps them in order.

    `lockstep`: time stands still while nobody sleeps on it, so a policy segment waits for its
    script's answer in its own turn, as it does on the simulator's clock, and a busy machine
    cannot starve a segment whose script answers at once."""

    lockstep = True

    def __init__(self) -> None:
        self.t = 0.0
        self.sleeps: list[float] = []

    def now(self) -> float:
        return self.t

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        if seconds > 0:
            self.t += seconds
        await asyncio.sleep(0)  # yield so the heartbeat and anything beside it get a turn


class MockPolicy:
    """The mock's policy: a runner (`policy.runner.PolicyRunner`) that answers each tick with one
    action, at the verbs' own rate, as a policy object handed to the real backend is run
    (`real.POLICY_HZ`).

    A learned policy is told an instruction and nothing else. This one is also told which verb
    its segment is for (`LeRobotMock._run_policy`), because it answers each with its own script:

    - **pick** reaches for the object, sending the three joints of `OBJECT_AT` and the gripper
      open, then closes the jaws once it is there with them open. The follower's jaws stop on the
      object and the segment ends when the arm reads as holding (`LeRobotReal._holding`). Jaws
      that shut on nothing end it as done.
    - **manipulate** walks each of those three joints from where the segment found it toward the
      object, `SEGMENT_SHARE` of the way over `MANIPULATE_S` and on at that pace until it is
      there, and sways the wrist either side of where it was (`scripted.SWEEP_JOINT`). The real
      loop ends a segment early once the arm stops moving under it (`policy.loop.STALL_S`), and
      the sway keeps it moving from anywhere, the object already in the jaws included, so a
      segment runs to its time as the old mock's always did.

    `runs` is the instruction every segment was told at its start, in order."""

    def __init__(self) -> None:
        self.verb = "pick"
        self.runs: list[str] = []
        self._start: dict[str, float] | None = None
        self._closing = False

    def reset(self, instruction: str) -> None:
        self.runs.append(instruction)
        self._start = None
        self._closing = False

    def features(self) -> Features:
        return Features(POLICY_HZ, SCRIPT_RATE)

    def latency_s(self) -> float:
        return 0.0

    def close(self) -> None:
        pass

    def next_chunk(self, observation: Observation, sent: Mapping[str, float]) -> Chunk:
        here = {
            key.removesuffix(".pos"): float(value)
            for key, value in observation.reading.items()
            if key.endswith(".pos")
        }
        if self.verb == "manipulate":
            action: dict[str, float] | None = self._toward(here, observation.tick)
        else:
            action = self._pick(here)
        if action is None:
            return Chunk(observation.tick, done=True)
        return Chunk(observation.tick, (action,))

    def _pick(self, here: dict[str, float]) -> dict[str, float] | None:
        reach = dict(OBJECT_AT)
        if not self._closing:
            there = all(abs(here[joint] - angle) <= TOL_DEG for joint, angle in reach.items())
            if not (there and here["gripper"] >= GRIPPER_OPEN - GRIPPER_TOL):
                return {**reach, "gripper": GRIPPER_OPEN}
            self._closing = True
        if here["gripper"] <= GRIPPER_CLOSED + GRIPPER_TOL:
            return None  # shut on nothing: the script has done all it can
        return {**reach, "gripper": GRIPPER_CLOSED}

    def _toward(self, here: dict[str, float], tick: int) -> dict[str, float]:
        if self._start is None:
            self._start = {joint: here[joint] for joint in (*OBJECT_AT, SWEEP_JOINT)}
        seconds = tick / POLICY_HZ
        share = min(1.0, seconds * SEGMENT_SHARE / MANIPULATE_S)
        goals = {
            joint: begin + (OBJECT_AT[joint] - begin) * share
            for joint, begin in self._start.items()
            if joint in OBJECT_AT
        }
        sway = SWEEP_DEG * math.sin(2 * math.pi * seconds / SWEEP_PERIOD_S)
        goals[SWEEP_JOINT] = self._start[SWEEP_JOINT] + sway
        return goals


class LeRobotMock(LeRobotReal):
    """The real backend over an arm in memory: `--robot lerobot:mock`.

    `object_bearing_deg` and `object_distance_m` place the object for the camera, whose frames
    are `frame_size` pixels a side, and None for the bearing is a table with nothing on it.
    `robot_type` is which arm this stands in for (`arms.ARMS`), which the manifest takes its
    sheet from. `rest_pose`, `registered_name` and `max_step_deg` are the real backend's, and
    `make("mock")` reads the step off `QUACKD_LEROBOT_MAX_STEP_DEG`, as it does for the arm. The
    rest are the turns the module's docstring lists."""

    name = "mock"
    label = "mock"
    frames_upright = True
    """Its frames are drawn, and drawn the right way up."""

    KEEPS_TORQUE_WITHOUT_A_POSE = False
    """The mock's arm drops on nothing, and a run of it needs no rest pose (it is not among
    `REST_POSE_BACKENDS`), so its close lets go of an arm with no pose recorded, as the
    simulator's does and as the mock's always has, where the arm's keeps its torque and says so
    (`LeRobotReal.KEEPS_TORQUE_WITHOUT_A_POSE`)."""

    def __init__(
        self,
        *,
        object_bearing_deg: float | None = 30.0,
        object_distance_m: float = 0.35,
        frame_size: int = 128,
        fail_heartbeat_after: int | None = None,
        refuse_kinds: set[str] | None = None,
        hot_joints: tuple[str, ...] = (),
        robot_type: str = ROBOT_TYPE,
        rest_pose: dict[str, float] | None = None,
        rest_fails: str | None = None,
        registered_name: str | None = None,
        max_step_deg: float = MAX_STEP_DEG,
    ) -> None:
        self.script = MockPolicy()
        # the camera is the mock's own drawing and no url opens it: an empty one, which the
        # manifest names no camera url for, as it never has
        view = CameraSpec(
            url="",
            name=CAMERA_NAME,
            index_or_path=0,
            width=frame_size,
            height=frame_size,
            fps=None,
            fourcc=None,
            backend="any",
            rotation=0,
            fov_deg=None,
        )
        super().__init__(
            None,
            policy=self.script,
            robot_type=robot_type,
            timeout_s=CALL_S,
            cameras=(view,),
            rest_pose=rest_pose,
            clock=MockClock(),
            registered_name=registered_name,
            max_step_deg=max_step_deg,
        )
        self.follower = FakeFollower(
            SimFollowerConfig(**self._config_kwargs()),
            travel=MOCK_RANGES,
            pose=REST,
            object_at=OBJECT_AT,
            near_deg=NEAR_DEG,
            grip_deg=GRIP_ON_OBJECT,
            temperature_c=MOCK_TEMPERATURE_C,
        )
        for joint in hot_joints:
            self.follower.temperature[joint] = HOT_JOINT_C
        self._robot = self.follower
        self.camera = FakeCamera(
            self.follower,
            size=frame_size,
            bearing_deg=object_bearing_deg,
            distance_m=object_distance_m,
            height_m=MOCK_CAM_HEIGHT_M,
            radius_m=MOCK_OBJECT_R,
        )
        self._cameras[view.name] = self.camera
        # known before any connect, as the mock's travel always was: the calibration is its own,
        # and the connect reads the same travel back off it
        self.joint_range_deg = joint_ranges(dict(self.follower.calibration))
        self.rest_fails = rest_fails
        """Set to a reason and a rest move that has to drive stalls at once, sends nothing and says
        that reason (`_drive_to_rest`)."""
        self.sequence: list[str] = []
        """`stop`, `rest`, `let_go`, `take_hold` and `close` in the order they were called. A
        run's teardown is an order as much as a set, and this is what a test reads to check it."""
        self.intents: list[Intent] = []
        """Every intent sent, and a stop's, in order, as the mock transport keeps them."""
        self.stops = 0
        self.heartbeats = 0
        self._fail_after = fail_heartbeat_after
        self._refuse = set(refuse_kinds or ())

    # ── what a test reads and turns ─────────────────────────────────────────────────────

    @property
    def joints(self) -> dict[str, float]:
        """The arm's joints, the follower's own dict: set one and the arm is there, as a person
        places a limp arm."""
        return self.follower.positions

    @property
    def actions(self) -> list[dict[str, float]]:
        """Every goal sent to the arm, in order, by joint name, as asked for."""
        return self.follower.actions

    @property
    def torque(self) -> bool:
        """Every motor has torque on."""
        return all(self.follower.torque.values())

    @property
    def holding(self) -> bool:
        """The object is between the jaws, which is the follower's truth; the state's `holding`
        is the real backend's inference from the gripper's readings."""
        return self.follower.held

    @property
    def temperature_c(self) -> dict[str, float]:
        return self.follower.temperature

    @property
    def connected(self) -> bool:
        return self.follower.is_connected

    @property
    def sleeps(self) -> list[float]:
        return self.mock_clock.sleeps

    @property
    def mock_clock(self) -> MockClock:
        clock = self.clock
        assert isinstance(clock, MockClock)
        return clock

    @property
    def policy_runs(self) -> list[str]:
        """Every instruction a segment's policy was given, in order."""
        return self.script.runs

    @property
    def hold_refusal(self) -> HandResult | None:
        """The real backend's `refused_hold`, under the name the mock always gave it."""
        return self.refused_hold

    @property
    def hold_slips(self) -> dict[str, float] | None:
        """Degrees each joint sags by as torque comes on in a take-hold, which is how an offline
        arm stands in for one that moved as torque came on (`FakeFollower.slips`)."""
        return dict(self.follower.slips) or None

    @hold_slips.setter
    def hold_slips(self, slips: dict[str, float] | None) -> None:
        self.follower.slips = dict(slips or {})

    @property
    def release_holdouts(self) -> tuple[str, ...]:
        """Motors that take the release and keep their torque anyway, as a real read-back can
        find them: some of them is an arm limp in part and still in a hand, all of them is a
        release that was refused (`FakeFollower.holdouts`)."""
        return self.follower.holdouts

    @release_holdouts.setter
    def release_holdouts(self, motors: tuple[str, ...]) -> None:
        self.follower.holdouts = tuple(motors)

    @property
    def release_unread(self) -> str | None:
        """Set to a reason and the torque register stops answering once a release has gone out,
        with that reason, so the release is said to be off with nothing to confirm it."""
        return self.follower.unread_after_release

    @release_unread.setter
    def release_unread(self, why: str | None) -> None:
        self.follower.unread_after_release = why

    @property
    def hold_unread(self) -> str | None:
        """Set to a reason and the torque register stops answering once a take-hold's torque
        write has gone out, with that reason. The follower takes the write, as a servo that
        answers nothing may well have, so the arm is energised while quackd says it cannot tell."""
        return self.follower.unread_after_hold

    @hold_unread.setter
    def hold_unread(self, why: str | None) -> None:
        self.follower.unread_after_hold = why

    def intents_of(self, kind: str) -> list[Intent]:
        return [i for i in self.intents if i.kind == kind]

    # ── the transport's own turns ───────────────────────────────────────────────────────

    async def send_intent(self, intent: Intent) -> Ack:
        self.intents.append(intent)
        if intent.kind in self._refuse:
            return Ack(accepted=False, reason=f"mock refuses {intent.kind}")
        return await super().send_intent(intent)

    async def heartbeat(self) -> None:
        self.heartbeats += 1
        if self._fail_after is not None and self.heartbeats > self._fail_after:
            raise HeartbeatError("mock heartbeat failure (scripted)")
        await super().heartbeat()

    # ── the order a test reads, around the real backend's own calls ─────────────────────

    async def stop(self) -> None:
        await super().stop()
        self.stops += 1
        self.intents.append(Intent.stop())
        self.sequence.append("stop")

    async def go_to_rest(self) -> RestResult:
        self.sequence.append("rest")
        return await super().go_to_rest()

    async def let_go(self, *, anywhere: bool = False, asked: bool = False) -> HandResult:
        self.sequence.append("let_go")
        return await super().let_go(anywhere=anywhere, asked=asked)

    async def take_hold(self) -> HandResult:
        self.sequence.append("take_hold")
        return await super().take_hold()

    async def close(self) -> None:
        self.sequence.append("close")
        await super().close()

    async def _drive_to_rest(
        self, goal: dict[str, float], recorded: dict[str, float]
    ) -> RestResult:
        """The real rest move's drive, or, with `rest_fails` set, a stall that sends nothing and
        says that reason. Everything round the drive is the real move's either way: the cancel,
        the reading, `already` and an arm in a hand, the clip note, and the result the close
        reads."""
        if self.rest_fails is not None:
            return RestResult("stalled", self.rest_fails)
        return await super()._drive_to_rest(goal, recorded)

    async def _run_policy(self, segment: Segment, ready: asyncio.Future[None]) -> SegmentEnd:
        """The real segment, with the script told which verb it is for (`MockPolicy`)."""
        self.script.verb = segment.verb
        return await super()._run_policy(segment, ready)
