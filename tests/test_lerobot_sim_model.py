"""The arm's simulator on the SO-101's own model, the one a rehearsal runs on: whether it can
hold what a task asks it to hold, and whether it runs a task end to end.

Every test here is marked `so101_model`. The maker's model is fetched at run time and never
shipped, and CI's gating jobs fetch nothing, so these skip wherever it is not already fetched.
The nightly `lerobot-sim-assets` job fetches it at its pin the way a first run would and runs
them there; a developer runs them against the cache, or against a checkout named by
`ROBOGLOT_LEROBOT_SIM_ASSETS`.

- **The grasp sweep.** On each seed a cube is laid somewhere on the table, and the arm is driven
  through the real backend's own verbs to open over it, come down around it, close and lift.
  Where to put the joints is worked out from where the cube lies, through the loaded model's own
  kinematics, since only the model knows where its fingers are. The world's truth is the judge:
  the cube off the table and touching both pads. Roboglot's own verdict, the gripper settling
  inside `HOLD_MIN` and `HOLD_MAX`, is recorded beside it and printed, and never asserted:
  whether it agrees with a real gripper is a question for the bench.
- **The acceptance sweep.** The bundled arm lookout and a grasp task with a sidecar, each
  rehearsed seed after seed through `roboglot preflight`'s own loop.
- **The cameras.** The real meshes drawn from the front and from the wrist.
- **The physics against the wall.** With nothing rendering, the model steps faster than real
  time, which is what lets a rehearsal cost less than the run it rehearses.

No number here comes off an arm. Where an object lies comes from the seed, where the fingers are
from the model's own meshes, and the travel from the generic arm's calibration, which is built
from the model's own ranges.
"""

from __future__ import annotations

import asyncio
import math
import os
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from PIL import Image

from roboglot.adapters.factory import describe, parse_robot_spec
from roboglot.agent.providers.base import ToolCall
from roboglot.agent.providers.fake import FakeProvider
from roboglot.perception.color_blob import ColorBlobDetector
from roboglot.preflight import FIRST_SEED, FileReport, Rehearsal
from roboglot.safety import allow_all
from roboglot.verbs.registry import VerbResult
from roboglot_lerobot.real import HOLD_MAX, HOLD_MIN, joint_ranges
from roboglot_lerobot.sim.camera import open_renderer
from roboglot_lerobot.sim.clock import SUBSTEPS
from roboglot_lerobot.sim.model import (
    CUBE,
    MOUNTS,
    PEN,
    PLACE_FAR,
    PLACE_NEAR,
    ArmModel,
    ModelError,
    SceneObject,
    generic_calibration,
    load,
)
from roboglot_lerobot.sim.transport import GL_CHECK_SIZE
from roboglot_lerobot.sim.world import JAWS_CLEARANCE, LIFT_MIN_M, ArmWorld, WorldError
from roboglot_lerobot.verbs import GRIPPER_OPEN, GRIPPER_S, JOINTS, MOVE_MIN_S, published_travel
from tests.test_lerobot_adapter import _executor
from tests.test_lerobot_sim import JAWS_START, WALL_S, _camera, _closes_onto, _needs_gl, _sim_arm
from tests.test_preflight import GRASP_TASK, LOOKOUT, _duck

mujoco = pytest.importorskip("mujoco")

pytestmark = pytest.mark.so101_model

SEEDS = range(10)
MIN_SUCCESSES = 10 if os.environ.get("ROBOGLOT_STRICT_SEEDS") == "1" else 8
"""Seeds of ten a sweep must pass: all ten where `ROBOGLOT_STRICT_SEEDS=1` asks, as the nightly
job does, and eight on a developer's machine, as `test_acceptance_mujoco.py` asks of the
Microduck."""
BODY = JOINTS[:-1]

# ── where the fingers go, from the model ────────────────────────────────────────────────

TIP_CLEARANCE = 0.5
"""How far above the table the fingertips stop, as a share of the object's rest height: low
enough that the pads close on the lower half of it, and clear of the table they would drag
along. The stand-in's grasp stops them the same share up (`test_lerobot_sim._pointing_down`)."""
RAISE = 1.0
"""How far above the grasp the hand hovers before it comes down, and lifts the object to, in
heights of the object: enough to carry the fingertips over its top. Not much more is in reach
with the fingers pointing straight down near the far edge of the ring objects are laid in,
where the wrist runs out of flex first."""
APPROACH_STEPS = 4
"""Waypoints between the hover and the grasp, so that a move from one to the next, which is
straight in joint space and not in the room, keeps the fingers close to the vertical line
through the object."""
IK_ITERATIONS = 400
IK_DAMPING = 1e-3
"""The damping of the least squares step, in the step's own units: small, because a target
the planner asks for is one the arm can reach, and it only has to keep a step finite near a
singular pose."""
IK_ANGLE_WEIGHT_M = 0.05
"""How many metres of position a radian of turn counts as, so that one step weighs the two
errors together."""
IK_POSITION_M = 5e-4
IK_ANGLE_RAD = 0.01
"""How close a solution has to come: half a millimetre, and about half a degree."""

TRANSIT_S = 30 * MOVE_MIN_S
"""The first move, from wherever the arm starts to the hover: long, because it is the far one."""
STEP_S = 5 * MOVE_MIN_S
"""Each move between two waypoints."""


@dataclass(frozen=True)
class Grip:
    """How the gripper holds an object, read off the loaded model with the fingers shut, in the
    frame of the hand, the body the fixed finger is part of."""

    body: int
    point: np.ndarray
    """Where the object's centre sits: just clear of the fixed finger's inner face, toward the
    moving one, as `ArmWorld.place_between_jaws` lays one, and far enough up the fingers that
    the fingertips stop `TIP_CLEARANCE` of its rest height above the table."""
    along: np.ndarray
    """From the palm toward the fingertips."""
    across: np.ndarray
    """From the fixed finger toward the moving one."""


def _grip(arm: ArmModel, obj: SceneObject) -> Grip:
    model = arm.model
    data = mujoco.MjData(model)
    data.qpos[:] = model.qpos0
    data.qpos[arm.gripper.qpos] = arm.gripper.closed
    mujoco.mj_kinematics(model, data)
    fixed = arm.fixed_pad
    body = int(model.geom_bodyid[fixed])
    to_hand = data.xmat[body].reshape(3, 3).T
    axes = to_hand @ data.geom_xmat[fixed].reshape(3, 3)  # the pad's own axes, in the hand's
    size = np.asarray(model.geom_size[fixed], dtype=float)
    centre = np.asarray(model.geom_pos[fixed], dtype=float)
    length = int(np.argmax(size))  # a pad runs from the palm to the fingertip
    along = axes[:, length] * np.sign(axes[:, length] @ (centre - model.geom_pos[arm.palm_pad]))
    across = to_hand @ (data.geom_xpos[arm.moving_pad] - data.xpos[body]) - centre
    across -= (across @ along) * along
    across /= np.linalg.norm(across)
    face = float(np.abs(axes.T @ across) @ size)  # from the pad's centre to its inner face
    tip = centre + along * size[length]
    point = (
        tip
        - along * obj.rest_height * (1 - TIP_CLEARANCE)
        + across * (face + obj.size[0] * (1 + JAWS_CLEARANCE))
    )
    return Grip(body, point, along, across)


def _solve(
    arm: ArmModel,
    grip: Grip,
    target: np.ndarray,
    side: np.ndarray,
    start: np.ndarray,
    travel: np.ndarray,
) -> tuple[np.ndarray, bool]:
    """The body joints, in the model's radians, that put the grip's point at `target` with the
    fingers pointing straight down and the moving finger on the `side` of the fixed one: damped
    least squares on the model's own Jacobian, from `start` and kept inside `travel`. Says
    whether it got there."""
    model = arm.model
    data = mujoco.MjData(model)
    joints = [arm.joints[name] for name in BODY]
    qpos = [j.qpos for j in joints]
    dofs = [j.dof for j in joints]
    down = np.array([0.0, 0.0, -1.0])
    jacp, jacr = np.zeros((3, model.nv)), np.zeros((3, model.nv))
    q = np.array(start, dtype=float)
    for _ in range(IK_ITERATIONS):
        data.qpos[:] = model.qpos0
        data.qpos[qpos] = q
        mujoco.mj_kinematics(model, data)
        mujoco.mj_comPos(model, data)
        rot = data.xmat[grip.body].reshape(3, 3)
        point = data.xpos[grip.body] + rot @ grip.point
        miss = target - point
        turn = (np.cross(rot @ grip.along, down) + np.cross(rot @ grip.across, side)) / 2
        if np.linalg.norm(miss) < IK_POSITION_M and np.linalg.norm(turn) < IK_ANGLE_RAD:
            return q, True
        mujoco.mj_jac(model, data, jacp, jacr, point, grip.body)
        jac = np.vstack([jacp[:, dofs], IK_ANGLE_WEIGHT_M * jacr[:, dofs]])
        error = np.concatenate([miss, IK_ANGLE_WEIGHT_M * turn])
        q = q + jac.T @ np.linalg.solve(jac @ jac.T + IK_DAMPING**2 * np.eye(6), error)
        q = np.clip(q, travel[:, 0], travel[:, 1])
    return q, False


def _descent(
    arm: ArmModel,
    grip: Grip,
    centre: Sequence[float],
    yaw: float,
    rise: float,
    travel: np.ndarray,
) -> list[np.ndarray] | None:
    """The joints on the way down onto a box whose centre is at `centre`, turned `yaw` about
    the vertical: from `rise` above it down to around it, in `APPROACH_STEPS`. None where no way
    down is in reach.

    Solved from the grasp upward, each waypoint from the one below, so the whole path keeps to
    one way of bending the arm. The fingers may close across any of the box's four sides, and
    the path taken is the one whose largest move of any joint between two waypoints is the
    smallest, the least turn of the wrist settling a tie."""
    where = np.asarray(centre, dtype=float)
    bearing = math.atan2(where[1] - arm.workspace.center[1], where[0] - arm.workspace.center[0])
    lift, elbow = (BODY.index(name) for name in ("shoulder_lift", "elbow_flex"))
    starts = []
    for bend in (0.0, 0.5, -0.5):  # straight, and half the travel of the elbow either way
        guess = np.zeros(len(BODY))
        guess[0] = bearing
        guess[lift] = bend * travel[lift, 1]
        guess[elbow] = -bend * travel[elbow, 1]
        starts.append(np.clip(guess, travel[:, 0], travel[:, 1]))
    best: tuple[tuple[float, float], list[np.ndarray]] | None = None
    for quarter in range(4):
        face = yaw + quarter * math.pi / 2
        side = np.array([math.cos(face), math.sin(face), 0.0])
        for start in starts:
            path: list[np.ndarray] = []
            q = start
            for i in range(APPROACH_STEPS + 1):
                up = np.array([0.0, 0.0, rise * i / APPROACH_STEPS])
                q, reached = _solve(arm, grip, where + up, side, q, travel)
                if not reached:
                    break
                path.append(q)
            else:
                path.reverse()
                steepest = float(np.abs(np.diff(np.array(path), axis=0)).max())
                score = (steepest, abs(float(path[-1][BODY.index("wrist_roll")])))
                if best is None or score < best[0]:
                    best = (score, path)
    return None if best is None else best[1]


def _yaw(quat: Sequence[float]) -> float:
    w, x, y, z = quat
    return math.atan2(2 * (w * z + x * y), 1 - 2 * (y * y + z * z))


def _radians(arm: ArmModel, travel: dict[str, Any]) -> np.ndarray:
    """A travel in LeRobot's degrees, as the model's radians, low end first."""
    return np.array(
        [sorted(arm.joints[name].to_model(float(v)) for v in travel[name]) for name in BODY]
    )


def _degrees(arm: ArmModel, q: np.ndarray) -> dict[str, float]:
    """Body joints in the model's radians, as the degrees `move_joints` takes, cut to a tenth
    toward zero. A solution clipped to the published travel can come back from radians a hair
    past it, and is refused there; toward zero is inward on a travel either side of zero, which
    is how every travel is read (`real.joint_ranges`)."""
    out = {}
    for name, value in zip(BODY, q, strict=True):
        deg = arm.joints[name].to_lerobot(float(value))
        out[name] = math.trunc(deg * 10) / 10
    return out


def _so101() -> Path:
    from roboglot_lerobot.sim.assets import AssetError, ensure_so101

    try:
        return ensure_so101(offline=True).model_path
    except AssetError as e:
        pytest.skip(f"the SO-101's model is not fetched: {e}")


def _gl_or_skip(model: Path) -> None:
    """A rehearsal renders once at every connect, so a machine that cannot draw fails every
    cycle rather than skipping. Asked here first, as `test_acceptance_mujoco.py` asks."""
    world = ArmWorld(load(model, seed=FIRST_SEED))
    try:
        with _needs_gl():
            open_renderer(world, *GL_CHECK_SIZE).close()
    finally:
        world.close()


def _report(capsys: pytest.CaptureFixture[str], lines: Sequence[str]) -> None:
    """Into the job's log whether or not the test passes, since the numbers are what the
    nightly job is read for."""
    with capsys.disabled():
        print("\n" + "\n".join(lines))


# ── the grasp sweep ─────────────────────────────────────────────────────────────────────

ALONE = [
    {
        "name": CUBE.name,
        "kind": CUBE.shape,
        "size": list(CUBE.size),
        "mass_kg": CUBE.mass_kg,
        "rgba": list(CUBE.rgba),
    }
]
"""The simulator's own cube and nothing else on the table, so that a seed the sweep fails is
the grasp failing, and never the hand meeting the pen on its way down."""


@dataclass(frozen=True)
class Grasp:
    seed: int
    lifted: bool
    """The world's truth after the lift: the cube off the table and touching the gripper."""
    pinched: bool
    """Both pads on it, at the same instant."""
    lift_m: float
    holding: bool | None
    """Roboglot's verdict as the gripper closed, None where the arm never got that far."""
    position: float | None
    """Where the gripper settled, 0 shut to 100 open."""
    why: str = ""
    """Every verb that failed on the way, which the truth is judged after all the same."""

    @property
    def ok(self) -> bool:
        return self.lifted and self.pinched

    @staticmethod
    def verdict(closed: VerbResult) -> tuple[bool | None, float | None]:
        """Roboglot's verdict on a close, and where the gripper settled. A close the executor
        refused or cut short, a timeout say, never reached the verb that gives a verdict, so it
        has none, and counting it as `on nothing` would have it agree with the grasp it failed."""
        holding = closed.data.get("holding")
        return None if holding is None else bool(holding), closed.data.get("position")

    def line(self) -> str:
        truth = (
            f"lifted {self.lift_m:.3f} m between both pads{self.why}"
            if self.ok
            else f"not held (lifted={self.lifted}, pinched={self.pinched}){self.why}"
        )
        if self.holding is None or self.position is None:
            return f"seed {self.seed}: {truth}"
        verdict = "on something" if self.holding else "on nothing"
        agrees = "agrees" if self.holding == self.ok else "DISAGREES"
        return (
            f"seed {self.seed}: {truth}; Roboglot said closed {verdict} at "
            f"{self.position:.1f}/100, which {agrees}"
        )


async def _grasp(model: Path, seed: int) -> Grasp:
    """Open over the cube, come down around it, close and lift, through the real backend."""
    adapter, transport = await _sim_arm(model, seed=seed, scene=ALONE)
    try:
        world = transport.sim_world
        manifest = adapter.manifest
        assert world is not None and manifest is not None
        arm = world.arm
        cube = world.truth().objects[CUBE.name]
        travel = _radians(arm, manifest.extras["joint_range_deg"])
        rise = RAISE * 2 * CUBE.rest_height
        path = _descent(arm, _grip(arm, CUBE), cube.position, _yaw(cube.quat), rise, travel)
        if path is None:
            return Grasp(seed, False, False, 0.0, None, None, ", with no way down to it in reach")
        executor = _executor(adapter, manifest, confirm=allow_all)
        failed: list[str] = []

        async def move(q: np.ndarray, duration_s: float) -> None:
            moved = await executor.run_verb(
                "move_joints", {"positions": _degrees(arm, q), "duration_s": duration_s}
            )
            if not moved.ok:
                failed.append(moved.summary)

        # a move that fails is a grasp that fails, and the sweep counts it as one rather than
        # stopping at it: the truth after the lift says what came of it either way
        opened = await executor.run_verb("gripper", {"open": True})
        if not opened.ok:
            failed.append(opened.summary)
        await move(path[0], TRANSIT_S)
        for q in path[1:]:
            await move(q, STEP_S)
        closed = await executor.run_verb("gripper", {"open": False})
        for q in reversed(path[:-1]):
            await move(q, STEP_S)
        after = world.truth().objects[CUBE.name]
        holding, position = Grasp.verdict(closed)
        return Grasp(
            seed,
            lifted=after.lifted,
            pinched=after.pinched,
            lift_m=after.lift_m,
            holding=holding,
            position=position,
            why="".join(f", after {summary}" for summary in failed),
        )
    finally:
        await adapter.close()


def _tally(grasps: Sequence[Grasp]) -> str:
    """The sweep's last line: how many seeds the world says held, and how often Roboglot's
    verdict agreed, out of the closes that gave one."""
    said = [g for g in grasps if g.holding is not None]
    return (
        f"grasp sweep: {sum(g.ok for g in grasps)} of {len(grasps)} lifted between both pads; "
        f"Roboglot's holding verdict ({HOLD_MIN:g} < gripper < {HOLD_MAX:g}, settled) agreed on "
        f"{sum(g.holding == g.ok for g in said)} of {len(said)}"
    )


async def test_a_seeded_grasp_lifts_the_cube_between_both_pads(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`MIN_SUCCESSES` of ten seeds lift the cube clear of the table with both pads on it,
    judged by the world and not by Roboglot. That guards the slip settings in `sim/model.py`
    (`IMPRATIO` and `NOSLIP_ITERATIONS`): with both at MuJoCo's defaults the stiff pads let the
    cube go on every seed. It does not guard the pads' own settings: the cube is held with those
    at MuJoCo's defaults too, and what they change is where the gripper settles, which is
    Roboglot's verdict, printed here and never asserted."""
    model = _so101()
    grasps = [await _grasp(model, seed) for seed in SEEDS]
    successes = sum(g.ok for g in grasps)
    lines = [*(g.line() for g in grasps), _tally(grasps)]
    _report(capsys, lines)
    assert all(g.lift_m >= LIFT_MIN_M for g in grasps if g.ok), "\n".join(lines)
    assert successes >= MIN_SUCCESSES, "\n".join(lines)


def test_a_close_that_never_ran_gives_no_verdict_to_count() -> None:
    """A close the executor cut short carries no verdict, and the tally leaves it out. Read as
    `on nothing`, it would count as Roboglot agreeing with the grasp that failed because of it."""
    held = Grasp(0, True, True, LIFT_MIN_M, True, (HOLD_MIN + HOLD_MAX) / 2)
    holding, position = Grasp.verdict(VerbResult.fail("gripper timed out; stopped"))
    assert (holding, position) == (None, None)
    dropped = Grasp(1, False, False, 0.0, holding, position)
    assert "Roboglot said" not in dropped.line()
    assert _tally([held, dropped]).endswith("agreed on 1 of 1")


# ── the acceptance sweep, through preflight ─────────────────────────────────────────────


def _rehearsal(tmp_path: Path, pilot: Any, rest_pose: dict[str, float] | None = None) -> Rehearsal:
    spec = parse_robot_spec("lerobot:mujoco")
    return Rehearsal(
        spec=spec,
        manifest=describe(spec),
        pilot=pilot,
        adapter_kwargs={} if rest_pose is None else {"rest_pose": rest_pose},
        seeds=len(SEEDS),
        runs_dir=tmp_path / "runs",
    )


def _passed(report: FileReport, capsys: pytest.CaptureFixture[str]) -> None:
    lines = [f"{report.name}: sim dt {report.sim_dt_s} s"]
    lines += [f"  connect {c.seed}: {'ok' if c.ok else c.error}" for c in report.cycles]
    for run in report.runs:
        said = "; ".join(run.failures) or ", ".join(v.detail for v in run.verdicts)
        lines.append(f"  seed {run.seed}: {'pass' if run.ok else 'FAIL'} {run.outcome}: {said}")
    passed = sum(run.ok for run in report.runs)
    lines.append(f"{report.name}: {passed} of {len(report.runs)} seeds passed preflight")
    _report(capsys, lines)
    assert not report.problems, report.problems
    assert all(c.ok for c in report.cycles), "\n".join(lines)
    assert len(report.runs) == len(SEEDS) and passed >= MIN_SUCCESSES, "\n".join(lines)


async def test_the_lookout_passes_preflight_seed_after_seed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The bundled arm lookout as `roboglot preflight` rehearses it, connect cycles first, on the
    generic arm and the simulator's own table, with the scripted pilot it ships with."""
    model = _so101()
    _gl_or_skip(model)
    rehearsal = _rehearsal(tmp_path, lambda duck: FakeProvider.for_glot(duck.name))
    _passed(await rehearsal.file(str(LOOKOUT)), capsys)


BLOCK = SceneObject("block", "box", CUBE.size, CUBE.mass_kg, CUBE.rgba)
"""What the grasp task's sidecar lays between the jaws: the simulator's own cube, renamed, so
the check names the object the task text does."""


TILT_STEPS = 20
"""How finely the jaws test below tilts the wrist, in steps of its travel's far end: fine
enough that the few degrees where the moving finger swings clear of the block are stepped on."""


def test_the_jaws_are_laid_only_where_a_close_brings_the_moving_finger_onto_the_block() -> None:
    """`place: jaws` on the maker's model. With the fingers pointing straight down around the
    block and the fingertips a block's half height up, the block is laid and the close brings
    the moving finger onto it. Tilted at the wrist, a step at a time, with the fixed finger
    still down at the block's side, the moving finger comes to swing past it, as it did from
    the lab arm's fold with the gripper open, and that start is refused with the start that
    works, until the fixed finger too is lifted clear and that refusal takes over. What each
    says is what the physics does: from every start laid a close brings the moving finger onto
    the block, and from every start refused as a miss it never does."""
    model = _so101()
    arm = load(model, seed=FIRST_SEED, objects=(BLOCK,))
    travel = {
        name: published_travel(*span)
        for name, span in joint_ranges(generic_calibration(arm)).items()
    }
    ring = (PLACE_NEAR + PLACE_FAR) / 2 * arm.workspace.reach
    cx, cy = arm.workspace.center
    height = BLOCK.rest_height
    path = _descent(
        arm,
        _grip(arm, BLOCK),
        (cx + ring, cy, arm.workspace.table_top + 2 * height),
        0.0,
        0.0,
        _radians(arm, travel),
    )
    assert path is not None, "no way down to the middle of the ring straight ahead is in reach"
    down = {**_degrees(arm, path[-1]), JOINTS[-1]: GRIPPER_OPEN}
    tilts = {
        sign * travel["wrist_flex"][1] * k / TILT_STEPS
        for k in range(TILT_STEPS)
        for sign in (-1, 1)
    }
    laid: list[bool] = []
    missed: list[tuple[str, bool]] = []
    for tilt in sorted(tilts, key=abs):
        world = ArmWorld(arm, rest_pose={**down, "wrist_flex": down["wrist_flex"] + tilt})
        try:
            try:
                world.place_between_jaws(BLOCK.name)
            except ModelError as e:
                if "moving finger" in str(e):
                    missed.append((str(e), _closes_onto(world, BLOCK.name, GRIPPER_S)))
                continue
            laid.append(_closes_onto(world, BLOCK.name, GRIPPER_S))
        finally:
            world.close()
    assert laid and all(laid), laid
    assert missed, "no tilt of the wrist swung the moving finger clear of the block"
    for said, onto in missed:
        assert JAWS_START.replace("block", BLOCK.name) in said and not onto, (said, onto)


async def test_a_grasp_task_passes_preflight_on_the_truth_its_sidecar_checks(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The arm starts with its jaws open down at the table, straight ahead in the middle of the
    ring objects are laid in, which is where the sidecar lays the block between them, and the
    seed lays a pen somewhere clear of it. The scripted pilot closes the gripper, lifts the hand
    one block's height, and says so; the close drives it back down to the rest pose. Both poses
    are worked out through the model as the grasp sweep works its out, over the generic arm's
    travel, which is what a bare `lerobot:mujoco` rehearses on. The sidecar asks for the block
    up at least half the lift, at its peak and as the run ended, and for the close to reach the
    rest pose.

    A rehearsal takes one rest pose for all its seeds, so the block, and the grasp on it, are
    the same on every seed and only the pen moves. What the ten seeds show is that the
    rehearsal loop holds up seed after seed; the grasp sweep above is what moves the object."""
    model = _so101()
    _gl_or_skip(model)
    arm = load(model, seed=FIRST_SEED, objects=(BLOCK,))
    travel = {
        name: published_travel(*span)
        for name, span in joint_ranges(generic_calibration(arm)).items()
    }
    ring = (PLACE_NEAR + PLACE_FAR) / 2 * arm.workspace.reach
    cx, cy = arm.workspace.center
    rise = RAISE * 2 * BLOCK.rest_height
    path = _descent(
        arm,
        _grip(arm, BLOCK),
        (cx + ring, cy, arm.workspace.table_top + BLOCK.rest_height),
        0.0,
        rise,
        _radians(arm, travel),
    )
    assert path is not None, "no way down to the middle of the ring straight ahead is in reach"
    rest = {**_degrees(arm, path[-1]), JOINTS[-1]: GRIPPER_OPEN}
    lifted = _degrees(arm, path[0])

    def strategy(_obs: Any, step: int, _history: Any) -> ToolCall:
        if step == 0:
            return ToolCall(name="gripper", arguments={"open": False})
        if step == 1:
            return ToolCall(
                name="move_joints", arguments={"positions": lifted, "duration_s": STEP_S}
            )
        return ToolCall(name="declare_success", arguments={"reason": "lifted the block"})

    sidecar = f"""\
scene:
  objects:
    - name: {BLOCK.name}
      kind: box
      size: {list(BLOCK.size)}
      mass_kg: {BLOCK.mass_kg}
      place: jaws
    - {{name: {PEN.name}, kind: capsule, size: {list(PEN.size)}, mass_kg: {PEN.mass_kg}}}
checks:
  - at_rest: true
  - lifted: {{object: {BLOCK.name}, min_m: {rise / 2}, when: peak}}
  - lifted: {{object: {BLOCK.name}, min_m: {rise / 2}, when: latched}}
"""
    duck = _duck(tmp_path, sidecar, name="grasp", text=GRASP_TASK)
    rehearsal = _rehearsal(tmp_path, lambda _duck: FakeProvider(strategy=strategy), rest)
    _passed(await rehearsal.file(str(duck)), capsys)


# ── a fold the model cannot hold ────────────────────────────────────────────────────────

FOLD_STEPS = 20
"""How many steps the search raises the wrist in, from straight to its stop."""


def _table_depth(arm: ArmModel, data: Any, pose: dict[str, float]) -> float:
    """How far the arm goes into the table at a pose, read off the model's own contacts with
    nothing stepped, in metres."""
    data.qpos[:] = arm.model.qpos0
    for name, value in pose.items():
        joint = arm.joints[name]
        data.qpos[joint.qpos] = joint.to_model(value)
    mujoco.mj_forward(arm.model, data)
    objects = {g for geoms in arm.object_geoms for g in geoms}
    n = data.ncon
    return max(
        [
            0.0,
            *(
                -float(dist)
                for (a, b), dist in zip(
                    data.contact.geom[:n].tolist(), data.contact.dist[:n].tolist(), strict=True
                )
                if arm.table in (a, b) and not {a, b} & objects
            ),
        ]
    )


def _folded_into_the_table(arm: ArmModel) -> dict[str, float]:
    """A fold the model cannot hold, found on the model: the shoulder down to its floor and the
    elbow up to its ceiling, the arm folded onto itself, and the wrist raised from straight a
    step at a time until the fingers are in the table by more than the fixed pad is thick.
    Every joint is in it, so a close judges them all."""
    data = mujoco.MjData(arm.model)
    thick = float(min(arm.model.geom_size[arm.fixed_pad]))
    wrist_hi = arm.joints["wrist_flex"].stops[1]
    for k in range(FOLD_STEPS + 1):
        pose = {
            "shoulder_pan": 0.0,
            "shoulder_lift": arm.joints["shoulder_lift"].stops[0],
            "elbow_flex": arm.joints["elbow_flex"].stops[1],
            "wrist_flex": wrist_hi * k / FOLD_STEPS,
            "wrist_roll": 0.0,
        }
        if _table_depth(arm, data, pose) > thick:
            return pose
    pytest.fail("no fold of the model puts its fingers into the table, so nothing was tested")


async def test_a_fold_the_model_puts_into_its_table_ends_at_rest_after_time_has_passed() -> None:
    """The stand-in's test of the same name, on the SO-101's own model: a fold read off an arm
    can put the model into its table and into itself, because the model's frame is an
    assumption (`JOINT_ZERO`, `JOINT_SIGN`), and the twin used to start there, be thrown out by
    the first physics step, and close stalled with torque left on after any run that let sim
    time pass. It now starts settled clear of both, and a verb, a wait and the teardown later
    it is at rest and let go."""
    model = _so101()
    _gl_or_skip(model)
    rest = _folded_into_the_table(load(model, seed=FIRST_SEED))
    adapter, transport = await _sim_arm(model, rest_pose=rest)  # type: ignore[arg-type]
    try:
        world = transport.sim_world
        assert world is not None and world.settled
        assert any("mm into the table" in n for n in transport.connect_notes)
        assert adapter.manifest is not None
        executor = _executor(adapter, adapter.manifest)
        pan = BODY[0]
        away = transport.joint_range_deg[pan][1] / 8

        async def session() -> None:
            moved = await executor.run_verb("move_joints", {"positions": {pan: away}})
            assert moved.ok, moved.summary
            await transport.sleep(transport.place_settle_s)
            # the run's teardown: a stop, the rest move, then the close
            await adapter.stop()
            parked = await adapter.go_to_rest()
            assert parked.how == "arrived", parked.reason

        await asyncio.wait_for(session(), WALL_S)
    finally:
        await asyncio.wait_for(adapter.close(), WALL_S)
    assert transport.close_note is None, transport.close_note


def test_a_fold_the_model_cannot_settle_clear_is_refused() -> None:
    """The shoulder at its ceiling with the rest of the arm straight drives the lower arm into
    the table, and no contact pushes it out again: a second of settling leaves it there, and the
    twin used to connect with a note and stall on its first move. It is refused, naming what is
    still in."""
    arm = load(_so101(), seed=FIRST_SEED)
    rest = {joint: 0.0 for joint in BODY}
    rest["shoulder_lift"] = arm.joints["shoulder_lift"].stops[1]
    with pytest.raises(WorldError) as refused:
        ArmWorld(arm, rest_pose=rest, name="bench-twin")
    said = str(refused.value)
    assert "of settling still leaves lower_arm " in said and " mm into the table" in said, said
    assert "roboglot robot rest-pose bench-twin" in said, said


# ── the cameras and the clock ───────────────────────────────────────────────────────────


async def test_the_real_meshes_render_from_the_front_and_the_wrist() -> None:
    """Upstream's meshes, drawn in greys, from the front and from the wrist, where the lab has
    its two cameras: each frame is a picture rather than a blank, the two are different
    pictures, and nothing in either, the arm included, reads as anything the colour detector
    looks for."""
    model = _so101()
    world = ArmWorld(load(model, seed=FIRST_SEED))
    detector = ColorBlobDetector()
    frames = {}
    try:
        for mount in ("front", "wrist"):
            assert mount in MOUNTS
            camera = await _camera(world, f"opencv://0?name={mount}&width=320&height=240")
            try:
                frame = await asyncio.to_thread(camera.read_latest)
            finally:
                await asyncio.to_thread(camera.disconnect)
            assert frame.shape == (240, 320, 3) and frame.std() > 0, f"{mount} rendered a blank"
            assert detector.detect(Image.fromarray(frame)) == [], mount
            frames[mount] = frame
    finally:
        world.close()
    assert not np.array_equal(frames["front"], frames["wrist"])


RATE_SIM_S = 10.0
"""Sim time the physics is timed over: long enough that the wall time is not all overhead."""


def test_the_real_model_steps_faster_than_the_wall(capsys: pytest.CaptureFixture[str]) -> None:
    """Stepped as the clock steps it, a slice of `SUBSTEPS` physics steps at a time with the
    truth folded in after each, while the arm swings its pan out to half its stop and back,
    twice: more sim time passes than wall time. Nothing renders, and a rehearsal whose physics
    alone ran slower than the wall would cost more than the run it rehearses."""
    world = ArmWorld(load(_so101(), seed=FIRST_SEED))
    try:
        pan = world.arm.joints[BODY[0]]
        dt = world.timestep * SUBSTEPS
        slices = round(RATE_SIM_S / dt)
        start = time.perf_counter()
        for i in range(slices):
            if i % (slices // 4) == 0:  # at each quarter of the run: out, back, out, back
                world.set_goal(pan.name, pan.hi / 2 if (i // (slices // 4)) % 2 == 0 else 0.0)
            world.step(dt)
        wall = time.perf_counter() - start
        steps = slices * SUBSTEPS
        sim = steps * world.timestep
    finally:
        world.close()
    _report(
        capsys,
        [
            f"physics: {steps} steps of {world.timestep} s, {sim:.1f} s of sim in {wall:.2f} s "
            f"of wall, {steps / wall:.0f} steps per second, {sim / wall:.1f} times the wall"
        ],
    )
    assert sim > wall, f"{sim:.1f} s of sim took {wall:.2f} s of wall"
