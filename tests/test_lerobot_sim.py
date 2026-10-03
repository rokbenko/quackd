"""The arm simulator on the stand-in arm, from its model to `quackd run`: no network and
nothing fetched.

`quackd_lerobot/sim/model.py` sets an arm in quackd's scene and maps LeRobot's units onto it;
`sim/world.py` steps it, holds or drops its joints and keeps the truth about the table;
`sim/follower.py` is the follower the real backend drives over it, and `sim/faults.py` the
seeded bus faults that follower can be told to have. `sim/clock.py` is the world's time,
`sim/camera.py` its cameras, and `sim/transport.py` the backend they make, `lerobot:mujoco`.
What the real backend makes of the follower is `tests/test_lerobot_sim_parity.py`'s; here is
what the follower does on its own, and what the backend adds to the real one's code. The tests
that render skip where no GL context can be made, unless `QUACKD_REQUIRE_GL=1` says they must
not (`tests/gl.py`).
Every test here runs on the primitives-only stand-in, which is what CI's physics job has, except
the one marked `so101_model`, which needs the maker's model already fetched and skips without
it.

No number here comes off an arm. Ranges come from the loaded model, travel from calibrations
built in the test out of the model's own ranges, and motor ids from upstream's bus table.
"""

from __future__ import annotations

import asyncio
import colorsys
import contextlib
import gc
import itertools
import json
import math
import os
import re
import sys
import threading
import time
import weakref
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from PIL import Image

from quackd.adapters.base import AdapterError, AdapterNotInstalled, RestResult
from quackd.perception.color_blob import DEFAULT_FOV_DEG, DEFAULT_TARGETS, ColorBlobDetector
from quackd.preflight import load_sidecar
from quackd.safety import Heartbeat, allow_all
from quackd.transport.base import HeartbeatError, TransportError
from quackd_lerobot import REACH, LeRobotAdapter, lerobot_manifest, make
from quackd_lerobot import upstream_api as lr
from quackd_lerobot.real import (
    ENCODER_TICKS,
    MAX_STEP_DEG,
    POLICY_HZ,
    TORQUE_RETRIES,
    joint_ranges,
    may_have_written_torque,
    motor_in_error,
    parse_camera_url,
)
from quackd_lerobot.sim import standin
from quackd_lerobot.sim import upstream_api as up
from quackd_lerobot.sim.camera import CAMERA_HINT, RenderError, SimCamera, vertical_fov
from quackd_lerobot.sim.clock import SUBSTEPS, SimClock
from quackd_lerobot.sim.faults import EXAMPLE, TORQUE_READ, FaultPlan
from quackd_lerobot.sim.follower import (
    NO_STATUS,
    NOT_SENT,
    DeviceAlreadyConnectedError,
    DeviceNotConnectedError,
    SimFollower,
    SimFollowerConfig,
    ensure_safe_goal_position,
)
from quackd_lerobot.sim.model import (
    CUBE,
    DEFAULT_OBJECTS,
    FIXED_PAD,
    MOUNTS,
    MOVING_PAD,
    OBJECT_CONDIM,
    PAD_CONDIM,
    PAD_SOLREF,
    PALM_PAD,
    PLACE_FAR,
    PLACE_NEAR,
    PLACE_SPREAD_DEG,
    VIEW_ASPECT,
    VIEW_HFOV_DEG,
    ArmModel,
    CalibrationError,
    GripperMap,
    JointMap,
    ModelError,
    MotorCalibration,
    SceneObject,
    calibration_path,
    generic_calibration,
    load,
    parse_scene,
    read_calibration,
)
from quackd_lerobot.sim.transport import GENERIC_ARM, SIM_EXTRA, LeRobotSim
from quackd_lerobot.sim.world import (
    LIFT_MIN_M,
    ROOM_TEMPERATURE_C,
    START_CLEAR_M,
    ArmWorld,
    WorldError,
)
from quackd_lerobot.verbs import (
    GRIPPER_CLOSED,
    GRIPPER_OPEN,
    GRIPPER_S,
    JOINTS,
    MOVE_MIN_S,
    PICK_POLL_S,
    TICK_S,
    TOL_DEG,
    shortfall,
)
from tests.gl import REQUIRE_ENV
from tests.test_lerobot_adapter import Scripted, _executor, _until
from tests.test_robot_twin import PORTS, guard_ports

mujoco = pytest.importorskip("mujoco")

BODY = JOINTS[:-1]
TICK_DEG = 360.0 / (ENCODER_TICKS - 1)
"""One encoder tick in LeRobot's degrees (`upstream_api.DEGREES_FORMULA`)."""


@pytest.fixture(scope="module")
def mjcf() -> str:
    return standin.mjcf()


def _arm(mjcf: str, seed: int = 0) -> ArmModel:
    return load(mjcf, seed=seed)


def _deg(world: ArmWorld, joint: str) -> float:
    return world.arm.joints[joint].to_lerobot(world.position(joint))


# ── the stand-in and the scene ──────────────────────────────────────────────────────────


def test_the_stand_in_has_lerobots_joints_and_the_manifests_ranges(mjcf: str) -> None:
    arm = _arm(mjcf)
    model = arm.model
    limit = lerobot_manifest("mujoco").limits["joint_deg"]
    assert list(arm.joints) == list(JOINTS)
    for name in JOINTS:
        assert model.joint(name).id >= 0 and model.actuator(name).id >= 0
    for name in BODY:
        joint = arm.joints[name]
        assert isinstance(joint, JointMap)
        assert joint.stops == pytest.approx((-limit, limit))
    assert isinstance(arm.joints[JOINTS[-1]], GripperMap)
    assert model.body(up.WRIST_CAMERA_BODY).id >= 0
    assert {model.camera(i).name for i in range(model.ncam)} == set(MOUNTS)
    assert int(model.cam_bodyid[model.camera("wrist").id]) == model.body(up.WRIST_CAMERA_BODY).id
    assert model.npair == 1  # the fingers are a body and its parent, and collide only by it


def test_the_stand_in_reaches_as_far_as_the_datasheet_says(mjcf: str) -> None:
    """From the shoulder_lift axis up to the fingertips, with the arm standing straight."""
    arm = _arm(mjcf)
    model, data = arm.model, mujoco.MjData(arm.model)
    mujoco.mj_kinematics(model, data)
    shoulder = data.xanchor[model.joint(JOINTS[1]).id][2]
    fixed = model.geom(FIXED_PAD).id
    tip = data.geom_xpos[fixed][2] + model.geom_size[fixed][2]
    assert tip - shoulder == pytest.approx(float(REACH.value))


def test_the_scene_is_laid_out_from_the_model(mjcf: str) -> None:
    arm = _arm(mjcf)
    model = arm.model
    assert model.opt.cone == mujoco.mjtCone.mjCONE_ELLIPTIC and model.opt.impratio > 1
    assert model.opt.noslip_iterations > 0
    table = arm.table
    top = model.geom_pos[table][2] + model.geom_size[table][2]
    assert top == pytest.approx(arm.workspace.table_top)
    # the views are laid out for the lens quackd's detector assumes by default
    from quackd.perception.color_blob import DEFAULT_FOV_DEG

    assert VIEW_HFOV_DEG == DEFAULT_FOV_DEG


def _in_view(model: Any, data: Any, camera: str, point: Any) -> tuple[float, float] | None:
    """Where a point in the world falls across the camera's frame, from -1 to 1 either way, at
    the shape the views are laid out for, or None if it is behind the camera or out of frame.
    MuJoCo's cameras look down their own -z, with x to the right of the frame and y up it."""
    cam = model.camera(camera).id
    x, y, z = data.cam_xmat[cam].reshape(3, 3).T @ (np.asarray(point) - data.cam_xpos[cam])
    if z >= 0:
        return None
    half_high = math.tan(math.radians(model.cam_fovy[cam]) / 2) * -z
    across, up_ = x / (half_high * VIEW_ASPECT), y / half_high
    return (across, up_) if abs(across) <= 1 and abs(up_) <= 1 else None


def test_every_view_sees_what_it_is_for(mjcf: str) -> None:
    """The table views take in every place an object can be laid, and the wrist view looks
    between the fingers, one to either side, with the gripper half open."""
    arm = _arm(mjcf)
    model = arm.model
    data = mujoco.MjData(model)
    lo, hi = model.jnt_range[model.joint(JOINTS[-1]).id]
    data.qpos[arm.gripper.qpos] = (lo + hi) / 2
    mujoco.mj_forward(model, data)
    workspace = arm.workspace
    cx, cy = workspace.center
    for view in ("front", "top"):
        for share in (PLACE_NEAR, PLACE_FAR):
            for bearing in (-PLACE_SPREAD_DEG, 0.0, PLACE_SPREAD_DEG):
                r, a = share * workspace.reach, math.radians(bearing)
                spot = (cx + r * math.cos(a), cy + r * math.sin(a), workspace.table_top)
                assert _in_view(model, data, view, spot) is not None, (view, share, bearing)
    fixed = _in_view(model, data, "wrist", data.geom_xpos[arm.fixed_pad])
    moving = _in_view(model, data, "wrist", data.geom_xpos[arm.moving_pad])
    assert fixed is not None and moving is not None
    assert fixed[0] * moving[0] < 0  # one finger either side of the frame


def test_a_pad_meets_everything_with_its_own_contact_settings(mjcf: str) -> None:
    """MuJoCo mixes two touching geoms' settings unless one outranks the other, so the pads
    outrank everything, and the pair between the fingers carries the same settings."""
    arm = _arm(mjcf)
    model = arm.model
    pads = [model.geom(name).id for name in (FIXED_PAD, MOVING_PAD, PALM_PAD)]
    others = [g for g in range(model.ngeom) if g not in pads]
    for g in pads:
        assert model.geom_condim[g] == PAD_CONDIM
        assert tuple(model.geom_solref[g]) == pytest.approx(PAD_SOLREF)
        assert model.geom_priority[g] > max(model.geom_priority[others])
    assert {int(model.geom_condim[g]) for geoms in arm.object_geoms for g in geoms} == {
        OBJECT_CONDIM
    }
    assert model.pair_dim[0] == PAD_CONDIM
    assert tuple(model.pair_solref[0]) == pytest.approx(PAD_SOLREF)
    # and each setting does what its reason says: rolling friction takes all six dimensions and
    # torsional four, and a pad is stiffer than MuJoCo's default yet above refsafe's floor
    assert OBJECT_CONDIM == 6 and PAD_CONDIM >= 4
    bare = mujoco.MjModel.from_xml_string(
        "<mujoco><worldbody><geom size='1'/></worldbody></mujoco>"
    )
    assert 2 * model.opt.timestep < PAD_SOLREF[0] < bare.geom_solref[0][0]


def test_the_gripper_gets_the_share_of_its_force_lerobot_writes(mjcf: str) -> None:
    raw = mujoco.MjSpec.from_string(mjcf).compile()
    arm = _arm(mjcf)
    share = lr.GRIPPER_MAX_TORQUE_LIMIT / lr.MAX_TORQUE_LIMIT_FULL
    gripper = arm.joints[JOINTS[-1]].actuator
    assert arm.model.actuator_forcerange[gripper] == pytest.approx(
        raw.actuator_forcerange[gripper] * share
    )
    for name in BODY:
        a = arm.joints[name].actuator
        assert arm.model.actuator_forcerange[a] == pytest.approx(raw.actuator_forcerange[a])


def test_a_model_without_lerobots_joints_or_fingers_is_refused(mjcf: str) -> None:
    spec = mujoco.MjSpec.from_string(mjcf)
    spec.delete(spec.actuator("wrist_flex"))
    with pytest.raises(ModelError, match="actuator wrist_flex"):
        load(spec.to_xml(), seed=0)
    spec = mujoco.MjSpec.from_string(mjcf)
    spec.delete(spec.geom(PALM_PAD))
    with pytest.raises(ModelError, match="all three"):
        load(spec.to_xml(), seed=0)


# ── the maps ────────────────────────────────────────────────────────────────────────────


def test_the_maps_round_trip_with_lerobots_sign_and_zero(mjcf: str) -> None:
    arm = _arm(mjcf)
    for name in BODY:
        joint = arm.joints[name]
        lo, hi = joint.stops
        for deg in (lo, lo / 3, 0.0, hi / 2, hi):
            q = joint.to_model(deg)
            assert q == pytest.approx(math.radians(deg))  # JOINT_SIGN +1, JOINT_ZERO 0
            assert joint.to_lerobot(q) == pytest.approx(deg)
    gripper = arm.gripper
    for value in (GRIPPER_CLOSED, 12.5, 50.0, 99.0, GRIPPER_OPEN):
        assert gripper.to_lerobot(gripper.to_model(value)) == pytest.approx(value)
    assert gripper.to_model(GRIPPER_CLOSED) == gripper.closed
    assert gripper.to_model(GRIPPER_OPEN) == gripper.open
    # both ways bounded to 0..100, as LeRobot bounds a RANGE_0_100 motor
    assert gripper.to_model(GRIPPER_OPEN + 20) == gripper.open
    assert gripper.to_model(GRIPPER_CLOSED - 20) == gripper.closed
    beyond = gripper.open + (gripper.open - gripper.closed)
    assert gripper.to_lerobot(beyond) == GRIPPER_OPEN


def test_the_grippers_closed_end_is_found_from_the_fingers(mjcf: str) -> None:
    arm = _arm(mjcf)
    gripper = arm.gripper
    assert gripper.closed == gripper.lo and gripper.open == gripper.hi
    # the same hand with its hinge turned the other way round closes at the top of its range
    spec = mujoco.MjSpec.from_string(mjcf)
    joint, actuator = spec.joint(JOINTS[-1]), spec.actuator(JOINTS[-1])
    joint.axis = [-x for x in joint.axis]
    joint.range = [-joint.range[1], -joint.range[0]]
    actuator.ctrlrange = [-actuator.ctrlrange[1], -actuator.ctrlrange[0]]
    mirrored = load(spec.to_xml(), seed=0).gripper
    assert mirrored.closed == mirrored.hi and mirrored.open == mirrored.lo
    assert mirrored.closed == pytest.approx(-gripper.closed)


# ── calibration ─────────────────────────────────────────────────────────────────────────


def test_the_generic_arm_gives_the_real_backend_the_models_travel(mjcf: str) -> None:
    arm = _arm(mjcf)
    calibration = generic_calibration(arm)
    ranges = joint_ranges(calibration)
    for name in BODY:
        lo, hi = arm.joints[name].stops
        widest = min(-lo, hi)
        got_lo, got_hi = ranges[name]
        assert got_lo == -got_hi
        assert widest - TICK_DEG < got_hi <= widest  # within a tick, never past a stop
    assert ranges[JOINTS[-1]] == (GRIPPER_CLOSED, GRIPPER_OPEN)
    for name, cal in calibration.items():
        assert cal.id == lr.SO_MOTOR_IDS[name]
        assert 0 <= cal.range_min < cal.range_max <= ENCODER_TICKS - 1
        assert cal.drive_mode == 0 and cal.homing_offset == 0


def _synthetic(arm: ArmModel, share: float = 0.5) -> dict[str, dict[str, int]]:
    """A calibration file's contents: each joint's travel a share of the model's, off centre
    the way a recorded one is, with ids that are not in the bus table's order."""
    ids = list(reversed(list(lr.SO_MOTOR_IDS.values())))
    per_deg = (ENCODER_TICKS - 1) / 360.0
    out = {}
    for offset, (name, joint) in enumerate(arm.joints.items(), start=1):
        lo, hi = joint.stops
        half = math.floor(share * min(-lo, hi) * per_deg) if name != JOINTS[-1] else offset * 10
        middle = ENCODER_TICKS // 2 + offset
        out[name] = {
            "id": ids[offset - 1],
            "drive_mode": 0,
            "homing_offset": -offset,
            "range_min": middle - half,
            "range_max": middle + half,
        }
    return out


def test_a_calibration_file_is_read_as_lerobot_reads_it(mjcf: str, tmp_path: Path) -> None:
    arm = _arm(mjcf)
    raw = _synthetic(arm)
    path = tmp_path / "arm.json"
    path.write_text(json.dumps(raw, indent=4), encoding="utf-8")
    calibration = read_calibration(path)
    assert list(calibration) == list(JOINTS)
    for name, cal in calibration.items():
        assert cal.__dict__ == raw[name]
    assert [c.id for c in calibration.values()] != sorted(c.id for c in calibration.values())
    ranges = joint_ranges(calibration)
    for name in BODY:
        span = raw[name]["range_max"] - raw[name]["range_min"]
        assert ranges[name] == pytest.approx((-span / 2 * TICK_DEG, span / 2 * TICK_DEG))


@pytest.mark.parametrize(
    ("change", "match"),
    [
        (lambda raw: raw.pop("wrist_roll"), "not the SO-101's six motors"),
        (lambda raw: raw.update(elbow=raw["elbow_flex"]), "not the SO-101's six motors"),
        (lambda raw: raw["gripper"].pop("range_max"), "not LeRobot's"),
        (lambda raw: raw["gripper"].update(offset=0), "not LeRobot's"),
        (lambda raw: raw["shoulder_lift"].update(range_min=1.5), "whole number"),
        (lambda raw: raw["shoulder_lift"].update(range_max=4000.0), "whole number"),
        (lambda raw: raw["elbow_flex"].update(homing_offset="three"), "whole number"),
        (
            lambda raw: raw["wrist_flex"].update(range_min=raw["wrist_flex"]["range_max"]),
            "equal to its range_max",
        ),
    ],
)
def test_a_calibration_file_lerobot_would_refuse_is_refused(
    mjcf: str, tmp_path: Path, change: Any, match: str
) -> None:
    raw = _synthetic(_arm(mjcf))
    change(raw)
    path = tmp_path / "arm.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(CalibrationError, match=match):
        read_calibration(path)


def test_each_field_is_decoded_as_draccus_decodes_it_for_lerobot(mjcf: str, tmp_path: Path) -> None:
    """`int()` of anything but a float (`upstream_api.CALIBRATION_INTS`): a twin loads the file
    its arm loads, however the file spells a whole number. A null draccus lets through, and the
    simulator, which builds the travel from every field, refuses it."""
    raw = _synthetic(_arm(mjcf))
    wanted = {k: raw["wrist_flex"][k] for k in ("id", "range_min", "homing_offset")}
    raw["gripper"]["drive_mode"] = True
    raw["wrist_flex"].update(
        id=str(wanted["id"]),
        range_min=f" {wanted['range_min']} ",
        homing_offset=str(wanted["homing_offset"]),
    )
    path = tmp_path / "arm.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    calibration = read_calibration(path)
    assert calibration["gripper"].drive_mode == 1
    assert {k: getattr(calibration["wrist_flex"], k) for k in wanted} == wanted
    raw["gripper"]["id"] = None
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(CalibrationError, match="gives gripper a value for id that is not"):
        read_calibration(path)


def test_a_missing_or_unreadable_calibration_file_says_how_to_go_on(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with pytest.raises(CalibrationError, match=r"no calibration file .*generic arm"):
        read_calibration(tmp_path / "nope.json")
    # a directory is not a file, whatever reading one raises on this system
    with pytest.raises(CalibrationError, match=r"no calibration file .*generic arm"):
        read_calibration(tmp_path)
    # a port is refused on its shape, and neither opened nor looked for
    guard_ports(monkeypatch)
    for port in PORTS:
        with pytest.raises(CalibrationError, match=r"is a serial port, .* nothing was opened"):
            read_calibration(Path(port))
        with pytest.raises(AdapterError, match="is a serial port"):
            make("mujoco", address=port)
    monkeypatch.undo()
    (tmp_path / "bad.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(CalibrationError, match="not a calibration file LeRobot could read"):
        read_calibration(tmp_path / "bad.json")
    (tmp_path / "list.json").write_text("[]", encoding="utf-8")
    with pytest.raises(CalibrationError, match="names no motors"):
        read_calibration(tmp_path / "list.json")


def test_the_calibration_search_is_lerobots(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Each variable wins over every one after it, and one set to nothing still counts."""
    tail = Path(lr.ROBOTS_SUBDIR) / lr.SO_FOLLOWER_NAME / "arm-7.json"
    for name in (lr.CALIBRATION_ENV, lr.LEROBOT_HOME_ENV, lr.HF_HOME_ENV, lr.XDG_CACHE_ENV):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    cache = tmp_path / "home" / ".cache"
    hub = Path(lr.HF_SUBDIR) / lr.LEROBOT_SUBDIR / lr.CALIBRATION_SUBDIR
    assert calibration_path("arm-7") == cache / hub / tail

    monkeypatch.setenv(lr.XDG_CACHE_ENV, str(tmp_path / "xdg"))
    assert calibration_path("arm-7") == tmp_path / "xdg" / hub / tail

    monkeypatch.setenv(lr.HF_HOME_ENV, str(tmp_path / "hf"))
    below_hf = Path(lr.LEROBOT_SUBDIR) / lr.CALIBRATION_SUBDIR
    assert calibration_path("arm-7") == tmp_path / "hf" / below_hf / tail

    monkeypatch.setenv(lr.LEROBOT_HOME_ENV, str(tmp_path / "lerobot"))
    assert calibration_path("arm-7") == tmp_path / "lerobot" / lr.CALIBRATION_SUBDIR / tail

    monkeypatch.setenv(lr.CALIBRATION_ENV, str(tmp_path / "calibration"))
    assert calibration_path("arm-7") == tmp_path / "calibration" / tail

    monkeypatch.setenv(lr.CALIBRATION_ENV, "")
    assert calibration_path("arm-7") == tail  # os.getenv hands back the empty string


# ── the world ───────────────────────────────────────────────────────────────────────────


def test_a_rest_pose_past_a_stop_starts_at_the_stop_and_says_so(mjcf: str) -> None:
    """Two joints that turn about their own links, so the stops are all that moves the pose.
    The elbow folded back to its floor would fold the stand-in into itself, which no settle
    clears (`test_a_rest_pose_the_settle_cannot_clear_is_refused`)."""
    arm = _arm(mjcf)
    _, pan_hi = arm.joints["shoulder_pan"].stops
    roll_lo, _ = arm.joints["wrist_roll"].stops
    _, wrist_hi = arm.joints["wrist_flex"].stops
    rest = {
        "shoulder_pan": pan_hi + 10.0,  # past the ceiling
        "wrist_roll": roll_lo - 10.0,  # past the floor
        "wrist_flex": wrist_hi / 2,
        "gripper": GRIPPER_OPEN + 5.0,
    }
    world = ArmWorld(arm, rest_pose=rest)
    assert _deg(world, "shoulder_pan") == pytest.approx(pan_hi)
    assert _deg(world, "wrist_roll") == pytest.approx(roll_lo)
    assert _deg(world, "wrist_flex") == pytest.approx(wrist_hi / 2)
    assert _deg(world, "gripper") == pytest.approx(GRIPPER_OPEN)
    assert _deg(world, "shoulder_lift") == 0.0  # not in the pose: the model's zero
    notes = " ".join(world.notes)
    assert len(world.notes) == 3 and world.settled == {}, world.notes
    assert "shoulder_pan" in notes and "wrist_roll" in notes and "gripper" in notes
    assert "wrist_flex" not in notes
    assert all(world.goal(j) == pytest.approx(world.position(j)) for j in JOINTS)


def _into_the_table(arm: ArmModel) -> dict[str, float]:
    """A stand-in rest pose with its hand pointing down and half its fingers' length into the
    table, half way out across the ring objects are laid in: a fold the model cannot hold,
    from the stand-in's own proportions. Every body joint is in it, so a close judges them
    all."""
    finger = standin.FINGER * standin.HAND * float(REACH.value)
    down = _pointing_down(arm, (PLACE_NEAR + PLACE_FAR) / 2, -finger / 2)
    return {"shoulder_pan": 0.0, "wrist_roll": 0.0, **down}


def _in_the_table(world: ArmWorld) -> float:
    """How deep the deepest part of the arm is in the table, in metres, or 0."""
    with world.locked() as (_, data):
        objects = {g for geoms in world.arm.object_geoms for g in geoms}
        depths = [
            -float(dist)
            for (a, b), dist in zip(
                data.contact.geom[: data.ncon].tolist(),
                data.contact.dist[: data.ncon].tolist(),
                strict=True,
            )
            if world.arm.table in (a, b) and not {a, b} & objects
        ]
    return max([0.0, *depths])


def test_a_rest_pose_into_the_table_starts_clear_of_it(mjcf: str) -> None:
    """A fold read off a real arm can put the model into its table, because the model's frame
    is an assumption (`JOINT_ZERO`, `JOINT_SIGN`). Started there, the first physics step threw
    the arm out and it never came back to the pose it started at. The world settles it out
    before the clock starts, and takes where it came to rest as its start, its goals and its
    rest, and says so."""
    arm = _arm(mjcf)
    rest = _into_the_table(arm)
    world = ArmWorld(arm, rest_pose=rest)
    try:
        assert world.t == 0.0, "the settle is not the run's time"
        assert _in_the_table(world) <= START_CLEAR_M
        assert world.settled, "nothing moved out of the table"
        for name, angle in world.settled.items():
            assert _deg(world, name) == pytest.approx(angle)
            assert world.goal(name) == pytest.approx(world.position(name)), name
        (note,) = world.notes
        assert "mm into the table" in note and "clear of them" in note, note
        assert up.JOINT_ZERO.name in note and up.JOINT_SIGN.name in note, note
        moved = [j for j in world.settled if abs(world.settled[j] - rest.get(j, 0.0)) > TOL_DEG]
        assert moved and all(f"{j} at " in note for j in moved), note
        start = {j: _deg(world, j) for j in BODY}
        world.step(2.0)
        for name in BODY:
            assert _deg(world, name) == pytest.approx(start[name], abs=TOL_DEG), name
        assert _in_the_table(world) <= START_CLEAR_M
    finally:
        world.close()


def test_a_rest_pose_the_settle_cannot_clear_is_refused(mjcf: str) -> None:
    """The elbow folded back to its floor folds the stand-in's forearm and hand into its own
    base, and the stop that holds the elbow there is the one way out a contact could push it.
    Started anyway, the arm was jammed: a note said what was still in, and every move from it
    stalled. It is refused, naming what the pose put where and what the settle left, and says
    what to do, with the robot's own name in the command."""
    arm = _arm(mjcf)
    elbow_lo, _ = arm.joints["elbow_flex"].stops
    with pytest.raises(WorldError) as refused:
        ArmWorld(arm, rest_pose={"elbow_flex": elbow_lo}, name="bench-twin")
    said = str(refused.value)
    assert said.startswith("lerobot mujoco: the rest pose puts "), said
    assert re.search(r"of settling still leaves \w+ \d+ mm into ", said), said
    assert "so the simulated arm cannot start there" in said, said
    assert up.JOINT_ZERO.name in said and up.JOINT_SIGN.name in said, said
    assert "quackd robot rest-pose bench-twin records the model's zero" in said, said


def test_a_joint_the_settle_pushes_past_its_stop_starts_at_the_stop(mjcf: str) -> None:
    """A stop in MuJoCo is soft, so the contacts that push a start out of the table can push a
    joint past one. Taken as it lay, that joint's goal, its rest and what the follower reads of
    it were past the stop, which no arm reaches, and the note said the arm starts at an angle
    beyond it. Here the stand-in's wrist stops where the pose puts it, on the side the settle
    turns it, and the arm starts with the wrist at that stop, clear of the table."""
    base = _arm(mjcf)
    rest = _into_the_table(base)
    free = ArmWorld(base, rest_pose=rest)
    wrist = base.joints["wrist_flex"]
    at, settles_to = wrist.to_model(rest["wrist_flex"]), wrist.to_model(free.settled["wrist_flex"])
    free.close()
    span = (wrist.lo, at) if settles_to > at else (at, wrist.hi)
    stopped, n = re.subn(
        r'(name="wrist_flex" range=)"[^"]*"', rf'\1"{span[0]!r} {span[1]!r}"', mjcf
    )
    assert n == 1
    arm = _arm(stopped)
    world = ArmWorld(arm, rest_pose=rest)
    try:
        assert _in_the_table(world) <= START_CLEAR_M
        for name, joint in arm.joints.items():
            lo, hi = joint.stops
            goal = joint.to_lerobot(world.goal(name))
            for angle in (_deg(world, name), goal, world.settled.get(name, lo)):
                assert lo - 1e-9 <= angle <= hi + 1e-9, (name, angle, joint.stops)
        assert world.settled, "nothing moved out of the table"
        assert all(world.goal(j) == world.position(j) for j in world.settled), world.settled
        assert _deg(world, "wrist_flex") == pytest.approx(rest["wrist_flex"])
        assert "wrist_flex" not in world.settled, world.settled
        assert "wrist_flex at" not in " ".join(world.notes), world.notes
    finally:
        world.close()


def test_every_joint_follows_its_goal(mjcf: str) -> None:
    world = ArmWorld(_arm(mjcf))
    goals = {}
    for name in BODY:
        goals[name] = (
            world.arm.joints[name].stops[1] / 9
        )  # a few tens of degrees, whatever the manifest's limit
        world.set_goal(name, world.arm.joints[name].to_model(goals[name]))
    world.set_goal(JOINTS[-1], world.arm.gripper.to_model(GRIPPER_OPEN))
    world.step(2.0)
    for name, goal in goals.items():
        assert _deg(world, name) == pytest.approx(goal, abs=TOL_DEG), name
    assert _deg(world, JOINTS[-1]) == pytest.approx(GRIPPER_OPEN, abs=TOL_DEG)
    assert world.t == pytest.approx(2.0)


def test_a_limp_joint_falls_and_torque_drives_it_back_to_its_stored_goal(mjcf: str) -> None:
    """The worst case of TORQUE_ENABLE_HOLDS_PRESENT: the goal register outlives the torque."""
    world = ArmWorld(_arm(mjcf))
    joint = world.arm.joints["shoulder_lift"]
    tilt = joint.stops[1] / 6  # tipped forward, so gravity pulls it further over
    world.set_goal("shoulder_lift", joint.to_model(tilt))
    world.step(2.0)
    held = _deg(world, "shoulder_lift")
    assert held == pytest.approx(tilt, abs=TOL_DEG)

    world.set_torque("shoulder_lift", False)
    assert not world.torque("shoulder_lift") and world.torque("elbow_flex")
    world.step(1.0)
    fallen = _deg(world, "shoulder_lift")
    assert fallen > held + TOL_DEG  # it fell the way gravity pulls it
    assert world.goal("shoulder_lift") == pytest.approx(joint.to_model(tilt))
    assert _deg(world, "elbow_flex") == pytest.approx(0.0, abs=TOL_DEG)  # the rest still hold

    world.set_torque("shoulder_lift", True)
    world.step(2.0)
    assert _deg(world, "shoulder_lift") == pytest.approx(tilt, abs=TOL_DEG)


def test_a_joint_let_go_in_one_world_holds_in_the_next(mjcf: str) -> None:
    """LeRobot lets every joint go at a disconnect by default, and a reconnect builds a new
    world from the same loaded model, whose joints must hold and not only say they do."""
    arm = _arm(mjcf)
    joint = arm.joints["shoulder_lift"]
    tilt = joint.stops[1] / 6
    gains = arm.model.actuator_gainprm.copy()
    first = ArmWorld(arm, rest_pose={"shoulder_lift": tilt})
    first.set_torque("shoulder_lift", False)
    first.close()
    assert (arm.model.actuator_gainprm == gains).all()
    again = ArmWorld(arm, rest_pose={"shoulder_lift": tilt})
    assert again.torque("shoulder_lift")
    again.step(2.0)
    assert _deg(again, "shoulder_lift") == pytest.approx(tilt, abs=TOL_DEG)


def test_every_motor_reads_the_room(mjcf: str) -> None:
    world = ArmWorld(_arm(mjcf))
    assert {world.temperature(j) for j in JOINTS} == {ROOM_TEMPERATURE_C}
    with pytest.raises(ValueError, match="no joint 'elbow'"):
        world.temperature("elbow")


def test_objects_are_laid_out_by_the_seed_within_reach(mjcf: str) -> None:
    def places(world: ArmWorld) -> list[tuple[float, float, float]]:
        return [o.position for o in world.truth().objects.values()]

    first, again, other = (ArmWorld(_arm(mjcf, seed)) for seed in (5, 5, 6))
    assert places(first) == places(again)
    assert places(first) != places(other)
    workspace = first.arm.workspace
    for obj, (x, y, z) in zip(first.arm.objects, places(first), strict=True):
        r = math.dist((x, y), workspace.center)
        assert PLACE_NEAR * workspace.reach <= r <= PLACE_FAR * workspace.reach
        assert z == pytest.approx(workspace.table_top + obj.rest_height, abs=1e-3)
    other.place_objects(5)
    assert places(other) == pytest.approx(places(first), abs=1e-9)


def _pointing_down(arm: ArmModel, reach_share: float, tip_height: float) -> dict[str, float]:
    """The stand-in's joints, in LeRobot's units, with its hand pointing straight down and its
    fingertips `tip_height` above the table, `reach_share` of the reach out: two-link inverse
    kinematics on the stand-in's own proportions."""
    reach = float(REACH.value)
    shares = (standin.UPPER_ARM, standin.FOREARM, standin.WRIST, standin.HAND)
    upper, fore, wrist, hand = (share * reach for share in shares)
    x = reach_share * arm.workspace.reach
    z = tip_height + hand + wrist - standin.PEDESTAL * reach  # the wrist above the shoulder
    elbow = math.acos((x * x + z * z - upper**2 - fore**2) / (2 * upper * fore))
    shoulder = math.atan2(x, z) - math.atan2(fore * math.sin(elbow), upper + fore * math.cos(elbow))
    return {
        "shoulder_lift": math.degrees(shoulder),
        "elbow_flex": math.degrees(elbow),
        "wrist_flex": math.degrees(math.pi - shoulder - elbow),
        "gripper": GRIPPER_OPEN,
    }


def test_the_truth_follows_a_grasp_and_a_latch_keeps_what_it_saw(mjcf: str) -> None:
    arm = _arm(mjcf)
    half = CUBE.size[0]
    world = ArmWorld(arm, rest_pose=_pointing_down(arm, (PLACE_NEAR + PLACE_FAR) / 2, half / 2))
    with world.locked() as (model, data):
        fixed = arm.fixed_pad
        face = data.geom_xpos[fixed] + data.geom_xmat[fixed].reshape(3, 3) @ [
            model.geom_size[fixed][0],
            0.0,
            0.0,
        ]
        inward = data.geom_xpos[arm.moving_pad] - face
    inward[2] = 0.0
    inward /= float(math.hypot(*inward))
    cube = face + inward * (half + half / 5)
    world.set_object_pose(
        CUBE.name, (float(cube[0]), float(cube[1]), arm.workspace.table_top + half)
    )
    world.step(0.5)
    assert world.truth().objects[CUBE.name].on_table

    world.set_goal(JOINTS[-1], arm.gripper.to_model(GRIPPER_CLOSED))
    world.step(1.0)
    held = world.truth().objects[CUBE.name]
    assert held.pinched and held.touching and not held.lifted

    lift = arm.joints["shoulder_lift"]
    world.set_goal("shoulder_lift", world.goal("shoulder_lift") - lift.to_model(lift.stops[1] / 9))
    world.step(1.5)
    up_ = world.truth().objects[CUBE.name]
    assert up_.lifted and not up_.on_table and up_.pinched

    latched = world.latch("stop")
    world.set_goal(JOINTS[-1], arm.gripper.to_model(GRIPPER_OPEN))
    world.step(1.0)
    now = world.truth()
    assert not now.objects[CUBE.name].lifted and now.objects[CUBE.name].on_table
    peaks = now.peaks[CUBE.name]
    assert peaks.lifted and peaks.pinched and peaks.touched
    assert peaks.lift_m >= up_.lift_m - 1e-9
    assert peaks.moved_m >= up_.moved_m - 1e-9 and peaks.moved_m >= LIFT_MIN_M
    assert world.latched("stop") is latched and latched.objects[CUBE.name].lifted
    assert world.latched("rest") is None


def _jaws_scene(**block: Any) -> list[dict[str, Any]]:
    """A scene as `quackd preflight` reads one from a sidecar: a box between the jaws, the size
    of the simulator's own cube, and a capsule on the table, both under names of their own."""
    return [
        {"name": "block", "kind": "box", "size": list(CUBE.size), "place": "jaws", **block},
        {"name": "stick", "kind": "capsule", "size": [CUBE.size[0] / 3, CUBE.size[0] * 4]},
    ]


def test_a_scene_lays_its_own_objects_and_one_between_the_jaws(mjcf: str) -> None:
    """A scene's objects replace the cube and the pen. The one it puts between the jaws starts
    on the table against the inside of the fixed finger, found from the model as the arm starts,
    untouched, and closing the gripper pinches it there. An object given no mass weighs what
    MuJoCo makes of its volume."""
    scene = parse_scene(_jaws_scene())
    assert scene.jaws == "block" and [o.name for o in scene.objects] == ["block", "stick"]
    assert all(o.mass_kg is None for o in scene.objects)
    arm = load(mjcf, seed=0, objects=scene.objects)
    assert [o.name for o in arm.objects] == ["block", "stick"]
    half = CUBE.size[0]
    world = ArmWorld(arm, rest_pose=_pointing_down(arm, (PLACE_NEAR + PLACE_FAR) / 2, half / 2))
    world.place_between_jaws("block")
    laid = world.truth().objects["block"]
    assert laid.on_table and not laid.touching and laid.moved_m == 0.0
    assert laid.position[2] == pytest.approx(arm.workspace.table_top + half, abs=1e-6)
    with world.locked() as (model, data):
        assert model.body_mass[arm.object_bodies[0]] > 0
        fixed, moving = (np.array(data.geom_xpos[g][:2]) for g in (arm.fixed_pad, arm.moving_pad))
        # on the line from one pad to the other, and between them
        across = moving - fixed
        share = float((np.array(laid.position[:2]) - fixed) @ across) / float(across @ across)
        assert 0.0 < share < 1.0, share
    world.set_goal(JOINTS[-1], arm.gripper.to_model(GRIPPER_CLOSED))
    world.step(1.0)
    held = world.truth()
    assert held.objects["block"].pinched and held.peaks["block"].pinched


def test_the_jaws_are_refused_where_nothing_on_the_table_is_between_them(mjcf: str) -> None:
    arm = load(mjcf, seed=0, objects=parse_scene(_jaws_scene()).objects)
    down = _pointing_down(arm, (PLACE_NEAR + PLACE_FAR) / 2, CUBE.size[0] / 2)
    # at the model's zero the hand is nowhere near the table
    with pytest.raises(ModelError, match=r"fixed finger ends .* above the table, over the top"):
        ArmWorld(arm).place_between_jaws("block")
    # down at the table with the gripper shut, the block would start inside a finger
    with pytest.raises(ModelError, match="open narrower than block"):
        ArmWorld(arm, rest_pose={**down, JOINTS[-1]: GRIPPER_CLOSED}).place_between_jaws("block")
    with pytest.raises(ValueError, match="no object 'cube'"):
        ArmWorld(arm, rest_pose=down).place_between_jaws("cube")


JAWS_START = "open jaws point down at the table around where block goes"
"""What every refusal to lay the block between the jaws says to do: the start that works."""


def _closes_onto(world: ArmWorld, name: str, seconds: float) -> bool:
    """Close the gripper for `seconds` of physics, a step at a time, and say whether the
    moving finger's pad touched `name` on any step."""
    arm = world.arm
    geoms = arm.object_geoms[[o.name for o in arm.objects].index(name)]
    world.set_goal(JOINTS[-1], arm.gripper.to_model(GRIPPER_CLOSED))
    for _ in range(math.ceil(seconds / world.timestep)):
        world.step(world.timestep)
        with world.locked() as (_, data):
            pairs = data.contact.geom[: data.ncon].tolist()
        if any(arm.moving_pad in pair and geoms & set(pair) for pair in pairs):
            return True
    return False


def test_the_jaws_are_refused_where_closing_them_never_reaches_what_is_between_them(
    mjcf: str,
) -> None:
    """The fixed finger down at the block's side and the jaws open wider than it is not enough:
    closing the gripper has to bring the moving finger onto it. Here the stand-in's moving
    finger is cut short from its tip by more than the block is tall, so it closes over the top
    of a block the fixed finger stands beside, which a close would never pick up. That start is
    refused, with how near the moving finger comes, and the physics agrees: closing from there
    never brings that finger onto the block. The same hand with its finger whole is laid out,
    and its close does.
    Every refusal says the start that works, which a gripper merely left open does not give."""
    half = CUBE.size[0]
    outcomes = {}
    for cut in (False, True):
        arm = load(mjcf, seed=0, objects=parse_scene(_jaws_scene()).objects)
        if cut:
            # the pad runs from the jaw's hinge along its body's z: keep the hinge end, and
            # cut the tip back past the block's top by as much again as its half size
            model, pad = arm.model, arm.moving_pad
            length = 2 * float(model.geom_size[pad][2]) - 3 * half
            assert length > 0, "the stand-in's finger is too short to cut"
            model.geom_size[pad][2] = length / 2
            model.geom_pos[pad][2] = length / 2
        down = _pointing_down(arm, (PLACE_NEAR + PLACE_FAR) / 2, half / 2)
        world = ArmWorld(arm, rest_pose={**down, JOINTS[-1]: GRIPPER_OPEN})
        try:
            try:
                world.place_between_jaws("block")
                said = None
            except ModelError as e:
                said = str(e)
            outcomes[cut] = (said, _closes_onto(world, "block", GRIPPER_S))
        finally:
            world.close()
    assert outcomes[False] == (None, True), outcomes[False]
    said, onto = outcomes[True]
    assert said is not None and "closing the gripper stops its moving finger" in said, said
    assert re.search(r"\d+\.\d mm clear of block, which it never touches", said), said
    assert JAWS_START in said, said
    assert not onto, "the physics closed the finger onto what the refusal said it never would"
    arm = load(mjcf, seed=0, objects=parse_scene(_jaws_scene()).objects)
    for pose in (None, {**down, JOINTS[-1]: GRIPPER_CLOSED}):
        with pytest.raises(ModelError, match=JAWS_START):
            ArmWorld(arm, rest_pose=pose).place_between_jaws("block")


CLEAR_SEEDS = 10
"""Tables laid out below. The seed draws each one knowing nothing of the arm, so a hand down at
the table, and the block between its jaws, land on another object on most of them."""


def test_a_scene_starts_with_nothing_but_the_table_touching_anything(
    mjcf: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The seed lays the table out knowing nothing of the arm, and the block goes between the
    jaws after it, so the hand lowered to the table, or the block itself, can land on another
    object. The simulator lays those again clear of both, as it builds the world a connect
    starts in, so physics moves nothing before the pilot does, on any seed, and a check on how
    far an object moved measures the pilot."""
    half = CUBE.size[0]
    scene = [
        *_jaws_scene(),
        {"name": "slab", "kind": "box", "size": [half * 2.4, half * 2.4, half]},
    ]
    objects = parse_scene(scene).objects
    down = _pointing_down(_arm(mjcf), (PLACE_NEAR + PLACE_FAR) / 2, half / 2)
    crowded: list[int] = []
    for seed in range(CLEAR_SEEDS):
        arm = load(mjcf, seed=seed, objects=objects)
        sim = LeRobotSim(model=mjcf, seed=seed, rest_pose=down, scene=scene)
        # with no draws to spare, a table the seed laid on the arm or the block is refused,
        # which is how the ones below that needed laying again are known to have been met
        with monkeypatch.context() as m:
            m.setattr("quackd_lerobot.sim.world.PLACE_TRIES", 0)
            try:
                sim._lay_out(arm).close()
            except ModelError as e:
                assert "no room on the table for" in str(e) and "Ask for fewer" in str(e)
                crowded.append(seed)
        world = sim._lay_out(arm)
        try:
            start = world.truth()
            assert not any(o.touching for o in start.objects.values()), seed
            world.step(1.0)
            peaks = world.truth().peaks
            for obj in objects:
                # a share of its own smallest half size: settling onto the table, not a shove
                assert peaks[obj.name].moved_m < min(obj.size) / 4, (seed, obj.name, peaks)
        finally:
            world.close()
    assert crowded, "no seed laid anything on the arm, so nothing here was tested"


@pytest.mark.parametrize(
    ("scene", "needle"),
    [
        ([{"name": "a", "kind": "cone", "size": [0.01]}], "the kind 'cone'"),
        ([{"kind": "box", "size": [0.01, 0.01, 0.01]}], "no name"),
        ([{"name": "a", "kind": "box", "size": [0.01, 0.01]}], "3 positive numbers"),
        ([{"name": "a", "kind": "capsule", "size": [0.01, "long"]}], "2 positive numbers"),
        ([{"name": "a", "kind": "box", "size": [0.01, 0.01, 0.01], "place": "shelf"}], "shelf"),
        ([{"name": "a", "kind": "box", "size": [0.01, 0.01, 0.01], "mass_kg": 0}], "a mass is"),
        ([{"name": "a", "kind": "box", "size": [0.01, 0.01, 0.01], "rgba": [2, 0, 0, 1]}], "rgba"),
        ([*_jaws_scene(), {**_jaws_scene()[0], "name": "other"}], "room there for one"),
        ([*_jaws_scene(), _jaws_scene()[1]], "a name of its own"),
        ([], "one or more objects"),
    ],
)
def test_a_scene_is_refused_whole_as_the_robot_is_built(
    scene: list[dict[str, Any]], needle: str
) -> None:
    with pytest.raises(AdapterError, match=re.escape(needle)):
        make("mujoco", scene=scene)


def test_only_the_simulator_takes_a_scene() -> None:
    """The table in front of a real arm has on it what somebody put there."""
    for backend in ("real", "mock"):
        with pytest.raises(AdapterError, match="only the simulator, lerobot:mujoco, takes one"):
            make(backend, scene=_jaws_scene())
    assert make("mujoco", scene=_jaws_scene()).transport.scene.jaws == "block"  # type: ignore[attr-defined]


def test_the_sidecar_the_arms_page_shows_lays_out_on_a_bare_lerobot_mujoco(
    mjcf: str, tmp_path: Path
) -> None:
    """The one sidecar docs/adapters/lerobot/README.md shows is the one a reader copies, and the
    same page starts that reader on a bare `--robot lerobot:mujoco`. So its table has to lay out on
    the generic arm, as `quackd preflight` would build it. An object it put between the jaws
    could not: the generic arm starts at the model's zero, its hand nowhere near the table, and
    every connect of every rehearsal refused it."""
    page = Path(__file__).resolve().parents[1] / "docs" / "adapters" / "lerobot" / "README.md"
    section = page.read_text(encoding="utf-8").split("\n### The sidecar\n", 1)[1]
    sidecar_yaml = section.split("```yaml\n", 1)[1].split("```", 1)[0]
    (tmp_path / "task.sim.yaml").write_text(sidecar_yaml, encoding="utf-8")
    sidecar = load_sidecar(tmp_path / "task.duck", joints=JOINTS)
    assert sidecar is not None and sidecar.scene is not None
    transport = make("mujoco", scene=sidecar.scene_items()).transport
    assert isinstance(transport, LeRobotSim)
    transport.model_source = mjcf
    arm, notes = transport._load()
    assert notes == [GENERIC_ARM]
    transport._lay_out(arm).close()


def _pressed_into(world: ArmWorld, pad: int, other: int) -> Any:
    """Where the cube's centre goes to sink a millimetre into the face of `pad` that looks
    toward `other`: the inside of one finger."""
    with world.locked() as (model, data):
        centre = data.geom_xpos[pad].copy()
        rot = data.geom_xmat[pad].reshape(3, 3)
        toward = rot.T @ (data.geom_xpos[other] - centre)
        axis = int(np.argmax(np.abs(toward)))
        normal = rot[:, axis] * np.sign(toward[axis])
        return centre + normal * (model.geom_size[pad][axis] + CUBE.size[0] - 0.001)


def test_one_finger_and_then_the_other_is_not_a_pinch(mjcf: str) -> None:
    """A pinch is both fingers on an object in the same instant. The cube touches the inside
    of one finger, then of the other, and at its peak it was touched but never pinched. Each
    touch is at the pad's own contact settings."""
    arm = _arm(mjcf)
    world = ArmWorld(arm, rest_pose={JOINTS[-1]: GRIPPER_OPEN})
    cube = [obj.name for obj in arm.objects].index(CUBE.name)
    for pad, other in ((arm.fixed_pad, arm.moving_pad), (arm.moving_pad, arm.fixed_pad)):
        where = _pressed_into(world, pad, other)
        with world.locked() as (model, data):
            # not set_object_pose, which is a scene's setup and starts the peaks over
            q, v = arm.object_qpos[cube], arm.object_dofs[cube]
            data.qpos[q : q + 7] = [*where, 1.0, 0.0, 0.0, 0.0]
            data.qvel[v : v + 6] = 0.0
            mujoco.mj_forward(model, data)
        world.step(world.timestep)
        now = world.truth().objects[CUBE.name]
        assert now.touching and not now.pinched
        with world.locked() as (model, data):
            touches = [
                c
                for c in data.contact[: data.ncon]
                if pad in (c.geom1, c.geom2) and {c.geom1, c.geom2} & arm.object_geoms[cube]
            ]
            assert touches
            for c in touches:
                assert c.dim == PAD_CONDIM and tuple(c.solref) == pytest.approx(PAD_SOLREF)
    peaks = world.truth().peaks[CUBE.name]
    assert peaks.touched and not peaks.pinched


def test_a_closed_world_refuses_everything_but_its_latches(mjcf: str) -> None:
    world = ArmWorld(_arm(mjcf))
    world.latch("stop")
    world.close()
    assert world.closed
    for call in (
        lambda: world.step(0.1),
        lambda: world.positions(),
        lambda: world.set_goal("gripper", 0.0),
        lambda: world.truth(),
        lambda: world.t,
    ):
        with pytest.raises(WorldError, match="closed"):
            call()
    assert world.latched("stop") is not None


def test_physics_that_diverge_stop_the_world_rather_than_teleport_the_arm(mjcf: str) -> None:
    """MuJoCo answers a state gone bad by resetting it to the model's zero and carrying on,
    which would move the arm in one step to a pose it never drove to."""
    world = ArmWorld(_arm(mjcf))
    world.step(0.1)
    with world.locked() as (_, data):
        data.qvel[world.arm.joints["elbow_flex"].dof] = math.nan
    with pytest.raises(WorldError, match="diverged"):
        world.step(0.1)


def test_a_step_shorter_than_the_physics_is_refused(mjcf: str) -> None:
    world = ArmWorld(_arm(mjcf))
    with pytest.raises(ValueError, match="advance nothing"):
        world.step(world.timestep / 4)
    assert world.step(world.timestep * 2.4) == pytest.approx(world.timestep * 2)


# ── the follower ────────────────────────────────────────────────────────────────────────

SETTLE_S = 1.0
"""Time enough for a joint of the stand-in to reach a goal a step or two away and stop there."""
PAN = "shoulder_pan"
"""The joint most tests move: it turns about the vertical, so gravity does not load it and a
limp one stays where it is."""


def _calibration(arm: ArmModel) -> dict[str, MotorCalibration]:
    """`_synthetic`'s calibration as LeRobot's dataclass holds it."""
    return {name: MotorCalibration(**fields) for name, fields in _synthetic(arm).items()}


def _follower(
    arm: ArmModel,
    *,
    cap: float | None = MAX_STEP_DEG,
    faults: FaultPlan | None = None,
    start: dict[str, float] | None = None,
    releases: bool = True,
) -> SimFollower:
    """A follower over a fresh world, on `_synthetic`'s calibration and a bus table with its ids,
    which are not in the table's order, so a joint named by its place in the table is named
    wrong."""
    calibration = _calibration(arm)
    config = SimFollowerConfig(disable_torque_on_disconnect=releases, max_relative_target=cap)
    return SimFollower(
        ArmWorld(arm, rest_pose=start),
        calibration,
        None,
        config,
        faults,
        motor_ids={name: cal.id for name, cal in calibration.items()},
    )


def test_the_step_cap_is_lerobots_errors_and_all() -> None:
    goals = {"a": (10.0, 0.0), "b": (-10.0, 0.0), "c": (1.0, 0.0)}
    assert ensure_safe_goal_position(goals, 2.0) == {"a": 2.0, "b": -2.0, "c": 1.0}
    per_motor = {"a": 1.0, "b": 3.0, "c": 0.5}
    assert ensure_safe_goal_position(goals, per_motor) == {"a": 1.0, "b": -3.0, "c": 0.5}
    with pytest.raises(TypeError):
        ensure_safe_goal_position(goals, 2)  # type: ignore[arg-type]  # an int raises
    with pytest.raises(ValueError, match="keys must match"):
        ensure_safe_goal_position(goals, {"a": 1.0})
    assert ensure_safe_goal_position(goals, math.nan) == {k: g for k, (g, _) in goals.items()}


def test_a_reading_is_a_whole_tick_and_is_never_clamped(mjcf: str) -> None:
    arm = _arm(mjcf)
    _, hi = joint_ranges(_calibration(arm))["elbow_flex"]
    folded = (hi + arm.joints["elbow_flex"].stops[1]) / 2
    follower = _follower(arm, start={"elbow_flex": folded, JOINTS[-1]: GRIPPER_OPEN / 3})
    follower.connect(False)
    obs = follower.get_observation()
    assert list(obs) == [f"{name}.pos" for name in JOINTS]
    for name, cal in follower.calibration.items():
        value = obs[f"{name}.pos"]
        if name == JOINTS[-1]:
            tick = cal.range_min + value / GRIPPER_OPEN * (cal.range_max - cal.range_min)
        else:
            tick = value / TICK_DEG + (cal.range_min + cal.range_max) / 2
        assert tick == pytest.approx(round(tick), abs=1e-6), name
    assert obs["elbow_flex.pos"] > hi, "a reading past the travel was clamped"
    assert obs["elbow_flex.pos"] == pytest.approx(folded, abs=TICK_DEG)
    raw = follower.bus.sync_read("Present_Position", normalize=False)
    assert all(isinstance(tick, int) for tick in raw.values())


def test_a_goal_past_the_travel_stops_at_the_limit_and_nothing_says_so(mjcf: str) -> None:
    """The servo clamps a goal to the limits calibration wrote into it, and LeRobot reports the
    goal it wrote, so the send says the whole way and the joint stops at the limit."""
    arm = _arm(mjcf)
    follower = _follower(arm, cap=None)
    follower.connect(False)
    _, hi = joint_ranges(follower.calibration)[PAN]
    past = (hi + arm.joints[PAN].stops[1]) / 2
    assert follower.send_action({f"{PAN}.pos": past}) == {f"{PAN}.pos": past}
    follower.world.step(SETTLE_S)
    assert _deg(follower.world, PAN) == pytest.approx(hi, abs=TICK_DEG)


def test_the_cap_is_read_off_the_config_at_every_send(mjcf: str) -> None:
    follower = _follower(_arm(mjcf))
    follower.connect(False)
    far = joint_ranges(follower.calibration)[PAN][1] / 2
    for cap in (MAX_STEP_DEG, 2 * MAX_STEP_DEG):
        follower.config.max_relative_target = cap
        present = follower.get_observation()[f"{PAN}.pos"]
        sent = follower.send_action({f"{PAN}.pos": far})
        assert sent[f"{PAN}.pos"] == pytest.approx(present + cap)
        follower.world.step(SETTLE_S)
        assert _deg(follower.world, PAN) == pytest.approx(present + cap, abs=2 * TICK_DEG)


def test_a_send_writes_only_the_goals_it_is_given(mjcf: str) -> None:
    follower = _follower(_arm(mjcf))
    follower.connect(False)
    world = follower.world
    before = {name: world.goal(name) for name in JOINTS}
    follower.send_action({"elbow_flex.pos": 1.0, "gripper.pos": GRIPPER_OPEN, "front": None})
    changed = {name for name in JOINTS if world.goal(name) != before[name]}
    assert changed == {"elbow_flex", "gripper"}


def test_a_torque_call_switches_only_the_motors_it_names(mjcf: str) -> None:
    """Named as upstream's bus names them (`upstream_api.BUS_MOTORS`): by name, by the id the
    table gives, or a sequence of either, and no other motor is written. A torque fault lands
    on one of the motors named."""
    arm = _arm(mjcf)
    follower = _follower(arm)
    follower.connect(False)
    bus, world = follower.bus, follower.world

    def limp() -> set[str]:
        return {name for name, on in world.torques().items() if not on}

    gripper = JOINTS[-1]
    bus.disable_torque(gripper, num_retry=TORQUE_RETRIES)
    assert limp() == {gripper}, "releasing the gripper let go of the arm"
    bus.disable_torque([bus.motors[PAN].id, "elbow_flex"])
    assert limp() == {gripper, PAN, "elbow_flex"}
    bus.enable_torque(bus.motors[gripper].id)
    assert limp() == {PAN, "elbow_flex"}
    with pytest.raises(TypeError):
        bus.enable_torque(1.5)  # type: ignore[arg-type]  # neither a name nor an id
    with pytest.raises(KeyError):
        bus.enable_torque(max(motor.id for motor in bus.motors.values()) + 1)
    assert limp() == {PAN, "elbow_flex"}, "a call the table could not resolve wrote torque"

    faulty = _follower(arm, faults=FaultPlan.parse("torque=1", seed=0))
    faulty.connect(False)
    with pytest.raises(ConnectionError, match=rf"on id_={faulty.bus.motors[gripper].id} "):
        faulty.bus.disable_torque(gripper)
    assert all(faulty.world.torques().values())


def test_a_limp_joint_keeps_its_goal_until_torque_drives_it_there(mjcf: str) -> None:
    """The worst case of TORQUE_ENABLE_HOLDS_PRESENT, through the bus the real backend uses."""
    follower = _follower(_arm(mjcf))
    follower.connect(False)
    present = follower.get_observation()[f"{PAN}.pos"]
    follower.bus.disable_torque(num_retry=TORQUE_RETRIES)
    follower.send_action({f"{PAN}.pos": present + MAX_STEP_DEG})
    follower.world.step(SETTLE_S)
    assert _deg(follower.world, PAN) == pytest.approx(present, abs=TICK_DEG), "a limp joint moved"
    follower.bus.enable_torque(num_retry=TORQUE_RETRIES)
    follower.world.step(SETTLE_S)
    assert _deg(follower.world, PAN) == pytest.approx(present + MAX_STEP_DEG, abs=2 * TICK_DEG)


def test_a_failed_connect_leaves_the_port_open_until_the_bus_closes_it(mjcf: str) -> None:
    """Upstream's order: nothing closes the port when configure() raises, every connect after
    it is refused as already connected, and a close through the bus that writes nothing is
    what lets the next one through. The torque writes stopped where the reply went missing."""
    follower = _follower(_arm(mjcf), faults=FaultPlan.parse("configure=1", seed=0))
    with pytest.raises(ConnectionError, match=r"^Failed to write 'Lock' on id_=\d+ with '1'"):
        follower.connect(False)
    assert follower.is_connected
    with pytest.raises(DeviceAlreadyConnectedError, match=r"^SOFollower is already connected\.$"):
        follower.connect(False)
    (fault,) = follower.faults.injected
    at = JOINTS.index(fault.motor(list(JOINTS)))
    torque = follower.world.torques()
    assert [torque[name] for name in JOINTS] == [k <= at for k in range(len(JOINTS))]
    follower.bus.disconnect(disable_torque=False)
    assert not follower.is_connected
    assert follower.world.torques() == torque, "closing the port wrote torque"


def test_a_closed_port_refuses_in_lerobots_words(mjcf: str) -> None:
    follower = _follower(_arm(mjcf))
    said = r"^SOFollower is not connected\. Run `\.connect\(\)` first\.$"
    for call in (
        follower.get_observation,
        lambda: follower.send_action({f"{PAN}.pos": 0.0}),
        follower.disconnect,
    ):
        with pytest.raises(DeviceNotConnectedError, match=said):
            call()
    with pytest.raises(DeviceNotConnectedError, match=r"^FeetechMotorsBus is not connected"):
        follower.bus.sync_read("Torque_Enable", normalize=False)


@pytest.mark.parametrize("releases", [False, True])
def test_a_follower_collected_while_connected_goes_by_its_flag(mjcf: str, releases: bool) -> None:
    """LeRobot disconnects a follower nobody closed as it is collected, by whatever its config
    says by then (`upstream_api.ROBOT_DEL`): the arm is left as a run that skipped its close
    leaves it."""
    follower = _follower(_arm(mjcf), releases=not releases)
    world = follower.world
    follower.connect(False)
    follower.config.disable_torque_on_disconnect = releases
    del follower
    gc.collect()
    assert all(world.torques().values()) is not releases


def test_every_injected_fault_is_sorted_as_lerobots_own_would_be(mjcf: str) -> None:
    """The real backend decides what a failed connect may have left energised, and which joint
    to send a person to, from LeRobot's words and the frames they were raised in
    (`real.may_have_written_torque`, `real.motor_in_error`). A fault the simulator injects has
    to be read the same way, or a rehearsal says something the arm never would."""
    arm = _arm(mjcf)

    def raised(spec: str, call: Any) -> tuple[SimFollower, BaseException]:
        follower = _follower(arm, faults=FaultPlan.parse(spec, seed=0))
        with pytest.raises(Exception) as caught:
            call(follower)
        return follower, caught.value

    def named(follower: SimFollower) -> tuple[str, str]:
        joint = follower.faults.injected[-1].motor(list(JOINTS))
        return f"{joint} (id {follower.bus.motors[joint].id})", joint

    def connected(f: SimFollower) -> SimFollower:
        f.connect(False)
        return f

    follower, error = raised("handshake=1", lambda f: f.connect(False))
    assert isinstance(error, RuntimeError) and "motor check failed on port" in str(error)
    assert not may_have_written_torque(error), "a ping wrote nothing"
    assert motor_in_error(str(error), follower.bus.motors) == named(follower)
    assert all(follower.world.torques().values()), "the handshake touched torque"

    follower, error = raised("configure=1", lambda f: f.connect(False))
    assert may_have_written_torque(error)
    assert motor_in_error(str(error), follower.bus.motors) == named(follower)
    assert str(error).endswith(f"after 1 tries. {NO_STATUS}")

    follower, error = raised(
        "torque=1", lambda f: connected(f).bus.disable_torque(num_retry=TORQUE_RETRIES)
    )
    assert f"with '0' after {TORQUE_RETRIES + 1} tries. {NO_STATUS}" in str(error)
    assert str(error).startswith("Failed to write 'Torque_Enable' on id_=")
    assert motor_in_error(str(error), follower.bus.motors) == named(follower)
    at = JOINTS.index(named(follower)[1])
    torque = follower.world.torques()
    assert [torque[name] for name in JOINTS] == [k >= at for k in range(len(JOINTS))]

    for spec, register in (
        ("torque_read=1", "Torque_Enable"),
        ("temperature_read=1", "Present_Temperature"),
    ):
        follower, error = raised(
            spec, lambda f, r=register: connected(f).bus.sync_read(r, normalize=False, num_retry=2)
        )
        ids = [motor.id for motor in follower.bus.motors.values()]
        assert (
            str(error)
            == f"Failed to sync read '{register}' on ids={ids} after 3 tries. {NO_STATUS}"
        )
        assert motor_in_error(str(error), follower.bus.motors) is None, "a sync read names no joint"

    follower, error = raised("write=1", lambda f: connected(f).send_action({f"{PAN}.pos": 1.0}))
    assert str(error).startswith("Failed to sync write 'Goal_Position' with ids_values={")
    assert str(error).endswith(f"after 1 tries. {NOT_SENT}")
    assert motor_in_error(str(error), follower.bus.motors) is None
    assert follower.world.goal(PAN) == pytest.approx(0.0), "a packet that never went out moved it"


def test_reads_lost_stay_lost_for_everyone_the_heartbeat_included(mjcf: str) -> None:
    """The heartbeat never starts the loss, because its reads are not counted, and it meets
    the loss like everyone else once it has started, as a pulled cable fails every read."""
    follower = _follower(_arm(mjcf), faults=FaultPlan.parse("read_loss_from=3", seed=0))
    follower.connect(False)
    heartbeat = follower.as_heartbeat(follower.get_observation)
    assert heartbeat.__name__ == "get_observation", "a wedge is reported by the call's name"
    follower.get_observation()
    heartbeat()  # not the second observation: the heartbeat's are not counted
    follower.get_observation()
    assert heartbeat()[f"{PAN}.pos"] == pytest.approx(0.0, abs=TICK_DEG)
    lost = r"^Failed to sync read 'Present_Position' on ids=\[[\d, ]+\] after 3 tries\. "
    with pytest.raises(ConnectionError, match=lost):
        follower.get_observation()
    with pytest.raises(ConnectionError, match="Failed to sync read 'Torque_Enable'"):
        follower.bus.sync_read("Torque_Enable", normalize=False)
    with pytest.raises(ConnectionError, match="Failed to sync read 'Present_Position'"):
        follower.send_action({f"{PAN}.pos": 1.0})  # the cap reads the positions first
    with pytest.raises(ConnectionError, match=lost):
        heartbeat()
    with pytest.raises(ConnectionError, match="Failed to sync read 'Torque_Enable'"):
        follower.as_heartbeat(follower.bus.sync_read)("Torque_Enable", normalize=False)
    # an arm that answers nothing is every motor missing, which names no joint: it is the
    # arm's cables or its power, and a ping writes nothing
    follower.bus.disconnect(disable_torque=False)
    with pytest.raises(RuntimeError, match="motor check failed") as unanswered:
        follower.connect(False)
    assert motor_in_error(str(unanswered.value), follower.bus.motors) is None
    assert not may_have_written_torque(unanswered.value)


def test_a_lost_arm_answers_no_torque_write_and_takes_no_goal(mjcf: str) -> None:
    """An arm whose reads are lost has dropped off the bus. A torque write waits for its status
    packet as a read does, so the first one raises in upstream's words with nothing written,
    and a disconnect asked to drop torque leaves the port open, as upstream's does
    (`upstream_api.BUS_DISCONNECT`). A goal's sync write waits for no reply, so it raises
    nothing, and no motor takes it."""
    follower = _follower(_arm(mjcf), cap=None, faults=FaultPlan.parse("read_loss_from=1", seed=0))
    follower.connect(False)
    with pytest.raises(ConnectionError, match="Failed to sync read 'Present_Position'"):
        follower.get_observation()
    bus, world = follower.bus, follower.world
    holding = world.torques()
    assert all(holding.values())
    first = next(iter(bus.motors))
    for call, value in ((bus.disable_torque, 0), (bus.enable_torque, 1)):
        with pytest.raises(ConnectionError) as unanswered:
            call(num_retry=TORQUE_RETRIES)
        assert str(unanswered.value) == (
            f"Failed to write 'Torque_Enable' on id_={bus.motors[first].id} with '{value}' "
            f"after {TORQUE_RETRIES + 1} tries. {NO_STATUS}"
        )
    assert world.torques() == holding, "a motor on a bus that answers nothing took a torque write"
    goal = world.goal(PAN)
    sent = follower.send_action({f"{PAN}.pos": MAX_STEP_DEG})
    assert sent == {f"{PAN}.pos": MAX_STEP_DEG}
    assert world.goal(PAN) == goal, "a motor on a bus that answers nothing took a goal"
    assert follower.config.disable_torque_on_disconnect
    with pytest.raises(ConnectionError, match=r"^Failed to write 'Torque_Enable' on id_="):
        follower.disconnect()
    assert follower.is_connected, "a disconnect whose torque-off raised closed the port"
    assert world.torques() == holding
    follower.bus.disconnect(disable_torque=False)
    assert not follower.is_connected


def test_a_fault_lands_on_the_same_call_of_its_kind_whatever_else_is_called(mjcf: str) -> None:
    """Keyed on the call's ordinal among its own kind: reads of another register, observations,
    and the heartbeat's reads from the worker thread a transport runs them in, do not move it."""
    arm = _arm(mjcf)
    plan = FaultPlan.parse("torque_read=0.5,temperature_read=0.5", seed=0)
    calls = 16
    expected = [plan.fires(TORQUE_READ, n) for n in range(1, calls + 1)]
    assert any(expected) and not all(expected)

    async def torque_failures(noisy: bool) -> list[bool]:
        follower = _follower(arm, faults=plan)
        follower.connect(False)
        loop = asyncio.get_running_loop()
        heartbeat = follower.as_heartbeat(follower.bus.sync_read)
        failed = []
        for _ in range(calls):
            if noisy:
                with contextlib.suppress(ConnectionError):
                    follower.bus.sync_read("Present_Temperature", normalize=False)
                follower.get_observation()
                await loop.run_in_executor(None, heartbeat, "Torque_Enable")
            try:
                follower.bus.sync_read("Torque_Enable", normalize=False)
            except ConnectionError:
                failed.append(True)
            else:
                failed.append(False)
        return failed

    assert asyncio.run(torque_failures(False)) == expected
    assert asyncio.run(torque_failures(True)) == expected


def test_the_heartbeat_mark_stays_in_its_own_thread(mjcf: str) -> None:
    follower = _follower(_arm(mjcf), faults=FaultPlan.parse("torque_read=1", seed=0))
    follower.connect(False)
    with follower.heartbeat(), ThreadPoolExecutor(1) as pool:
        follower.bus.sync_read("Torque_Enable", normalize=False)  # this thread's: exempt
        other = pool.submit(follower.bus.sync_read, "Torque_Enable", normalize=False)
        with pytest.raises(ConnectionError):
            other.result()
    assert [fault.ordinal for fault in follower.faults.injected] == [1]


def test_a_fault_spec_is_read_strictly() -> None:
    plan = FaultPlan.parse(" handshake = 0.2, configure=0.3 ,read_loss_from=40", seed=7)
    assert dict(plan.rates) == {"handshake": 0.2, "configure": 0.3}
    assert plan.read_loss_from == 40 and plan.seed == 7
    assert FaultPlan.parse(plan.spec, seed=7) == plan
    assert FaultPlan.parse(EXAMPLE, seed=0).spec == EXAMPLE
    # a record shows the plan that ran, every digit of it, so replaying it meets the same faults
    precise = FaultPlan.parse("configure=0.30000000000000004,write=1e-05", seed=7)
    assert FaultPlan.parse(precise.spec, seed=7) == precise, precise.spec
    off = FaultPlan.parse("", seed=0)
    assert not off.rates and off.read_loss_from is None
    for spec, why in (
        ("handshake", "which is not name=value"),
        ("handshake=", "which is not name=value"),
        ("handshake=1.5", "not a rate from 0 to 1"),
        ("handshake=nan", "not a rate from 0 to 1"),
        ("handshake=often", "not a rate from 0 to 1"),
        ("configure=0.1,configure=0.2", "gives configure twice"),
        ("connect=0.3", "names 'connect', which is not a fault the simulator has"),
        ("read_loss_from=0", "not a whole number from 1"),
        ("read_loss_from=2.5", "not a whole number from 1"),
        ("write=0.1,,torque=0.1", "an empty entry between two commas"),
    ):
        with pytest.raises(AdapterError, match=re.escape(why)) as refused:
            FaultPlan.parse(spec, seed=0)
        said = str(refused.value)
        assert said.startswith(f"lerobot mujoco: the fault spec {spec!r} "), said
        assert said.endswith(f"such as {EXAMPLE}."), said


def test_a_follower_is_built_only_the_way_quackd_builds_one(mjcf: str) -> None:
    arm = _arm(mjcf)
    calibration = _calibration(arm)
    for config, why in (
        (SimFollowerConfig(True, MAX_STEP_DEG, use_degrees=False), "use_degrees=True"),
        (SimFollowerConfig(True, MAX_STEP_DEG, cameras={"front": object()}), "cameras={}"),
    ):
        with pytest.raises(ValueError, match=re.escape(why)):
            SimFollower(ArmWorld(arm), calibration, None, config)
    config = SimFollowerConfig(True, MAX_STEP_DEG)
    for table in ({**lr.SO_MOTOR_IDS, "elbow": 9}, dict.fromkeys(JOINTS, 1)):
        with pytest.raises(ValueError, match="an id of its own"):
            SimFollower(ArmWorld(arm), calibration, None, config, motor_ids=table)
    follower = SimFollower(ArmWorld(arm), calibration, None, config)
    assert {name: motor.id for name, motor in follower.bus.motors.items()} == lr.SO_MOTOR_IDS


# ── the clock ───────────────────────────────────────────────────────────────────────────

WALL_S = 60.0
"""How long a test waits, in the wall's time, for something the simulator does in a fraction of
it: long enough for a slow runner, and a hang still ends in a failure rather than a stuck job."""
LONG_S = 3600.0
"""A sleep, in sim time, that nothing in a test waits out."""
WALL_TURNS = 100
"""How many turns of the event loop a test gives something that settles in a few."""


def test_a_clock_step_divides_every_wait_the_verbs_make(mjcf: str) -> None:
    world = ArmWorld(_arm(mjcf))
    clock = SimClock(world)
    assert clock.dt == pytest.approx(world.timestep * SUBSTEPS)
    for period in (TICK_S, PICK_POLL_S):
        steps = period / clock.dt
        assert steps == pytest.approx(round(steps)) and steps >= 1
    arm = _arm(mjcf)
    arm.model.opt.timestep = TICK_S / (SUBSTEPS * 1.5)  # a clock step of two thirds of a tick
    with pytest.raises(ValueError, match="does not divide TICK_S"):
        SimClock(ArmWorld(arm))


async def test_a_wait_of_nothing_registers_nothing(mjcf: str) -> None:
    """The flock clock registers an id even for a zero wait and never lets it go, and a
    registered id that nobody parks stops time for good: the one sleep after these would never
    wake."""
    clock = SimClock(ArmWorld(_arm(mjcf)))
    try:
        await clock.sleep(0)
        await clock.sleep(-TICK_S)
        assert clock.now() == 0.0
        await asyncio.wait_for(clock.sleep(TICK_S), WALL_S)
        assert clock.now() == pytest.approx(TICK_S)
    finally:
        await clock.close()


async def test_time_stands_still_while_nobody_sleeps_and_runs_for_every_sleeper(
    mjcf: str,
) -> None:
    """Think time and a lone task's work between two sleeps cost nothing, and two sleeps at
    once overlap rather than queue: the shorter wakes at its time and the longer at its own."""
    clock = SimClock(ArmWorld(_arm(mjcf)))
    try:
        await asyncio.to_thread(lambda: None)  # a bus call, say
        await asyncio.sleep(TICK_S)  # a pilot thinking, on the wall's clock
        assert clock.now() == 0.0
        woke: list[float] = []

        async def sleeper(seconds: float) -> None:
            await clock.sleep(seconds)
            woke.append(clock.now())

        short, long_ = 3 * TICK_S, 5 * TICK_S
        await asyncio.wait_for(asyncio.gather(sleeper(long_), sleeper(short)), WALL_S)
        assert woke == pytest.approx([short, long_])
        assert clock.now() == pytest.approx(long_)
    finally:
        await clock.close()


async def test_a_close_ends_a_parked_sleep_and_refuses_the_next(mjcf: str) -> None:
    clock = SimClock(ArmWorld(_arm(mjcf)))
    ticked = asyncio.Event()
    clock.add_tick_hook(lambda _: ticked.set())
    parked = asyncio.create_task(clock.sleep(LONG_S))
    await asyncio.wait_for(ticked.wait(), WALL_S)  # parked, and time running for it
    await clock.close()
    with pytest.raises(TransportError, match="closed during a wait"):
        await asyncio.wait_for(parked, WALL_S)
    assert clock.now() < LONG_S
    with pytest.raises(TransportError, match=r"^lerobot mujoco: the simulator is closed\.$"):
        await clock.sleep(TICK_S)
    await clock.close()  # twice is nothing


async def test_the_viewer_closing_reaches_a_direct_sleep_as_an_abort(mjcf: str) -> None:
    """The rest move, the take-hold and the policy loop sleep on the clock itself rather than
    through the transport, and a person closing the viewer must stop them as it stops a verb.
    Time goes on afterwards, because the teardown still has to move the arm."""
    from quackd.safety import Aborted

    clock = SimClock(ArmWorld(_arm(mjcf)))

    def window_closed(_: Any) -> None:
        raise KeyboardInterrupt

    try:
        clock.add_tick_hook(window_closed)
        with pytest.raises(Aborted):
            await asyncio.wait_for(clock.sleep(LONG_S), WALL_S)
        before = clock.now()
        await asyncio.wait_for(clock.sleep(TICK_S), WALL_S)
        assert clock.now() == pytest.approx(before + TICK_S)
    finally:
        await clock.close()


async def test_physics_that_cannot_step_on_fail_every_sleep_after(mjcf: str) -> None:
    world = ArmWorld(_arm(mjcf))
    clock = SimClock(world)
    try:
        await clock.sleep(TICK_S)
        with world.locked() as (_, data):
            data.qvel[world.arm.joints["elbow_flex"].dof] = math.nan
        for _ in range(2):
            with pytest.raises(TransportError, match="diverged"):
                await asyncio.wait_for(clock.sleep(TICK_S), WALL_S)
        assert clock.failure is not None
    finally:
        await clock.close()


# ── the cameras ─────────────────────────────────────────────────────────────────────────


@contextlib.contextmanager
def _needs_gl() -> Iterator[None]:
    """Skip where no GL context can be made, unless the job says it must be (`tests/gl.py`)."""
    try:
        yield
    except RenderError as e:
        if os.environ.get(REQUIRE_ENV) == "1":
            raise
        pytest.skip(f"no OpenGL context for offscreen rendering: {e!r}")


async def _camera(world: ArmWorld, url: str) -> SimCamera:
    spec = parse_camera_url(url, label="mujoco", hint=CAMERA_HINT)
    camera = SimCamera(spec, world, spec.name, asyncio.get_running_loop())
    with _needs_gl():
        await asyncio.to_thread(camera.connect)
    return camera


async def test_every_mount_renders_for_a_read_from_a_worker_thread(mjcf: str) -> None:
    """The real backend reads a camera from a worker thread; the render happens on the loop's,
    and a frame is drawn again only once the world has stepped since the last one."""
    world = ArmWorld(_arm(mjcf))
    size = (160, 120)
    frames = {}
    for mount in ("front", "wrist"):
        camera = await _camera(world, f"opencv://0?name={mount}&width={size[0]}&height={size[1]}")
        try:
            frame = await asyncio.to_thread(camera.read_latest)
            assert frame.shape == (size[1], size[0], 3) and frame.dtype == np.uint8
            assert frame.std() > 0, f"{mount} rendered a blank"
            assert await asyncio.to_thread(camera.read_latest) is frame, "nothing had stepped"
            world.step(world.timestep)
            assert await asyncio.to_thread(camera.read_latest) is not frame
            frames[mount] = frame
        finally:
            await asyncio.to_thread(camera.disconnect)
        with pytest.raises(RuntimeError, match="not connected"):
            await asyncio.to_thread(camera.read_latest)
    assert not np.array_equal(frames["front"], frames["wrist"])
    turned = await _camera(world, "opencv://0?name=wrist&width=96&height=128&rotation=90")
    try:
        assert (await asyncio.to_thread(turned.read_latest)).shape == (128, 96, 3)
    finally:
        await asyncio.to_thread(turned.disconnect)
    # a url with no size gets the camera's own mode, which a quarter turn hands over on its
    # side, as LeRobot turns a webcam's (`upstream_api.OPENCV_MODE_DEFAULTS_TO_THE_CAMERA`)
    mode = world.arm.model.vis.global_
    wide, high = int(mode.offwidth), int(mode.offheight)
    for rotation, shape in ((0, (high, wide)), (90, (wide, high))):
        own = await _camera(world, f"opencv://0?name=front&rotation={rotation}")
        try:
            assert (await asyncio.to_thread(own.read_latest)).shape[:2] == shape, rotation
        finally:
            await asyncio.to_thread(own.disconnect)


async def test_a_camera_the_scene_does_not_have_is_refused_with_the_ones_it_does(
    mjcf: str,
) -> None:
    with pytest.raises(AdapterError) as refused:
        make("mujoco", camera_url="opencv://0?name=side")
    said = str(refused.value)
    assert said.startswith("lerobot mujoco: --camera-url 'opencv://0?name=side'"), said
    assert all(mount in said for mount in MOUNTS), said
    spec = parse_camera_url("opencv://0?name=side")
    with pytest.raises(RenderError, match="no camera called 'side'") as unknown:
        SimCamera(spec, ArmWorld(_arm(mjcf)), "side", asyncio.get_running_loop())
    assert all(mount in str(unknown.value) for mount in MOUNTS)
    # a url the parser refuses says whose refusal it is and what a camera is there
    with pytest.raises(AdapterError) as bad:
        make("mujoco", camera_url="opencv://0?rotation=45")
    assert str(bad.value).startswith("lerobot mujoco: --camera-url"), str(bad.value)
    assert str(bad.value).endswith(CAMERA_HINT)


async def test_the_field_of_view_is_published_horizontal_and_rendered_vertical(
    mjcf: str,
) -> None:
    """MuJoCo's fovy is vertical and the detector's is horizontal: a camera is rendered at the
    vertical angle that shows the asked-for horizontal one across its frame, and the manifest
    publishes the horizontal one, the detector's default where the url gave none."""
    width, height = 160, 90  # a wide frame, so the two angles differ a great deal
    asked = DEFAULT_FOV_DEG / 2
    assert vertical_fov(asked, width, height, 0) < asked
    assert vertical_fov(asked, width, height, 90) == asked  # a quarter turn swaps the axes
    adapter = make(
        "mujoco",
        camera_url=[
            f"opencv://0?name=front&width={width}&height={height}&fov={asked:g}",
            "opencv://1?name=top",
        ],
    )
    transport = adapter.transport
    assert isinstance(transport, LeRobotSim)
    assert [s.fov_deg for s in transport.camera_specs] == [asked, DEFAULT_FOV_DEG]
    transport.model_source = mjcf
    with _needs_gl():
        manifest = await adapter.connect()
    try:
        assert manifest.limits["camera_fov_deg"] == asked
        world = transport.sim_world
        assert world is not None
        with world.locked() as (model, _):
            fovy = float(model.cam_fovy[model.camera("front").id])
        # the frame's half width over its depth is the tangent of half the angle asked for
        half_high = math.tan(math.radians(fovy) / 2)
        assert half_high * width / height == pytest.approx(math.tan(math.radians(asked) / 2))
    finally:
        await adapter.close()


@pytest.mark.parametrize(
    ("objects", "seeds"), [((), (0,)), (DEFAULT_OBJECTS, (0, 1, 2))], ids=["empty", "default"]
)
async def test_nothing_the_scene_brings_reads_as_a_target(
    mjcf: str, objects: tuple[SceneObject, ...], seeds: tuple[int, ...]
) -> None:
    """The table, the lights, the arm and the objects the scene lays out by default are all
    colours the detector looks for none of, so a blob it finds in a rehearsal is an object
    someone put there. A blue pen read as a person on every run."""
    detector = ColorBlobDetector()
    for seed in seeds:
        world = ArmWorld(load(mjcf, seed=seed, objects=objects))
        for mount in MOUNTS:
            camera = await _camera(world, f"opencv://0?name={mount}")
            try:
                frame = await asyncio.to_thread(camera.read_latest)
            finally:
                await asyncio.to_thread(camera.disconnect)
            assert detector.detect(Image.fromarray(frame)) == [], (seed, mount)


OPENCV_HUES = 180
"""OpenCV's hue runs from 0 to 179, half a degree each, which is how a target's band is given."""


async def test_every_target_reads_as_itself_from_both_views_of_the_table(mjcf: str) -> None:
    """The lighting never clips a colour, which changes its hue, and never leaves one too dark
    or too pale for the detector: a box in the middle of each target's band of hue, halfway
    between the band's floors and full, reads as that target and nothing else from the front
    and from the top. Too much light read the ball as a duck from the top, and saw neither a
    person nor a pet there."""
    probe = SceneObject("probe", "box", CUBE.size, CUBE.mass_kg, (1.0, 1.0, 1.0, 1.0))
    world = ArmWorld(load(mjcf, seed=0, objects=(probe,)))
    detector = ColorBlobDetector()
    views = ("front", "top")
    cameras = [await _camera(world, f"opencv://0?name={m}&width=320&height=240") for m in views]
    try:
        for target in DEFAULT_TARGETS:
            band = target.hsv
            hue = (band.h_lo + band.h_hi) / 2 / OPENCV_HUES
            saturation, value = ((floor / 255 + 1) / 2 for floor in (band.s_lo, band.v_lo))
            with world.locked() as (model, _):
                model.geom_rgba[model.geom(probe.name).id] = [
                    *colorsys.hsv_to_rgb(hue, saturation, value),
                    1.0,
                ]
            world.step(world.timestep)  # so each camera draws the box anew
            for view, camera in zip(views, cameras, strict=True):
                frame = await asyncio.to_thread(camera.read_latest)
                seen = [d.label for d in detector.detect(Image.fromarray(frame))]
                assert seen == [target.label], (target, view, seen)
    finally:
        for camera in cameras:
            await asyncio.to_thread(camera.disconnect)


# ── the backend ─────────────────────────────────────────────────────────────────────────


async def _sim_arm(mjcf: str, **kw: Any) -> tuple[LeRobotAdapter, LeRobotSim]:
    """The real backend over the stand-in, connected, and the adapter over it."""
    transport = LeRobotSim(model=mjcf, **kw)
    transport.connect_pause_s = 0.0
    adapter = LeRobotAdapter(transport)
    with _needs_gl():
        await adapter.connect()
    return adapter, transport


def _hand_height(world: ArmWorld) -> float:
    with world.locked() as (_, data):
        return float(data.geom_xpos[world.arm.fixed_pad][2])


async def test_a_machine_without_the_physics_is_told_the_extra_before_anything_is_fetched(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The simulator needs MuJoCo, which only its extra installs, and a connect would otherwise
    fetch the SO-101's model first, which is no use with nothing to load it in."""

    def fetched() -> str:
        raise AssertionError("the model was fetched with no physics to load it in")

    monkeypatch.setattr("quackd_lerobot.sim.transport.default_model", fetched)
    monkeypatch.setitem(sys.modules, "mujoco", None)  # any import of it now fails
    adapter = make("mujoco")
    with pytest.raises(AdapterNotInstalled, match=re.escape(SIM_EXTRA)):
        await adapter.connect()
    # the extra doctor's row names for MuJoCo on this adapter, the same words
    from quackd.doctor import EXTRAS

    assert ("mujoco", SIM_EXTRA) in EXTRAS.values()


def test_the_simulator_is_known_before_anything_is_built(mjcf: str) -> None:
    from quackd.adapters.factory import describe, is_simulator, make_adapter, parse_robot_spec

    assert is_simulator("lerobot:mujoco")
    assert not any(is_simulator(s) for s in ("lerobot:real", "lerobot:mock", "microduck:mujoco"))
    adapter = make_adapter("lerobot:mujoco")
    assert isinstance(adapter, LeRobotAdapter) and adapter.is_simulator
    assert isinstance(adapter.transport, LeRobotSim) and adapter.transport.sim_world is None
    assert adapter.transport.faults is None
    mock = make_adapter("lerobot:mock")
    assert isinstance(mock, LeRobotAdapter) and not mock.is_simulator
    real, sim = (describe(parse_robot_spec(f"lerobot:{b}")) for b in ("real", "mujoco"))
    assert sim.backend == "mujoco"
    assert sim.model_dump(exclude={"backend"}) == real.model_dump(exclude={"backend"})
    seeded = make_adapter("lerobot:mujoco", seed=3, faults=EXAMPLE)
    assert isinstance(seeded, LeRobotAdapter) and isinstance(seeded.transport, LeRobotSim)
    assert seeded.transport.faults == FaultPlan.parse(EXAMPLE, seed=3)
    with pytest.raises(AdapterError, match="only the simulator, lerobot:mujoco, takes a fault"):
        make_adapter("lerobot:real", address="COM5", faults=EXAMPLE)


async def test_the_calibration_is_the_one_named_and_the_generic_arm_otherwise(
    mjcf: str, tmp_path: Path
) -> None:
    """`--address` names a file, a registered name finds its file where LeRobot would, and a
    run that names no arm gets the generic arm and says so, rather than LeRobot's default id
    finding the file of whatever arm this machine last calibrated."""
    arm = _arm(mjcf)
    adapter, transport = await _sim_arm(mjcf)
    try:
        assert transport.calibration_file is None
        assert any("generic arm" in note for note in transport.connect_notes)
        assert transport.joint_range_deg == joint_ranges(generic_calibration(arm))
    finally:
        await adapter.close()

    given = tmp_path / "given.json"
    given.write_text(json.dumps(_synthetic(arm)), encoding="utf-8")
    adapter, transport = await _sim_arm(mjcf, address=str(given))
    try:
        assert transport.calibration_file == str(given)
        assert transport.joint_range_deg == joint_ranges(read_calibration(given))
        assert not any("generic arm" in note for note in transport.connect_notes)
    finally:
        await adapter.close()

    lost = LeRobotSim(model=mjcf, robot_id="arm-9", registered_name="arm-9")
    with pytest.raises(CalibrationError, match="arm-9 has no calibration file where LeRobot"):
        await lost.connect()
    found = calibration_path("arm-9")
    found.parent.mkdir(parents=True)
    found.write_text(json.dumps(_synthetic(arm, share=0.25)), encoding="utf-8")
    adapter, transport = await _sim_arm(mjcf, robot_id="arm-9", registered_name="arm-9")
    try:
        assert transport.calibration_file == str(found)
    finally:
        await adapter.close()


async def test_two_moves_at_once_on_one_arm_both_finish_in_their_own_time(mjcf: str) -> None:
    """Two tool calls over MCP run at once. Each tick of each sleeps under an id of its own, so
    time runs while both are parked, and each move reaches its goal in about the time it was
    asked for rather than the two taking turns."""
    adapter, transport = await _sim_arm(mjcf)
    try:
        assert adapter.manifest is not None
        executor = _executor(adapter, adapter.manifest)
        goals = {joint: transport.joint_range_deg[joint][1] / 4 for joint in (PAN, "wrist_roll")}
        duration = 20 * TICK_S
        start = transport.now()
        moved = await asyncio.wait_for(
            asyncio.gather(
                *(
                    executor.run_verb(
                        "move_joints", {"positions": {joint: goal}, "duration_s": duration}
                    )
                    for joint, goal in goals.items()
                )
            ),
            WALL_S,
        )
        assert all(result.ok for result in moved), [result.summary for result in moved]
        took = transport.now() - start
        assert duration <= took < 2 * duration, took
        state = await adapter.get_state()
        for joint, goal in goals.items():
            assert state.extras["joints"][joint] == pytest.approx(goal, abs=TOL_DEG), joint
    finally:
        await adapter.close()


async def test_an_mcp_sessions_minutes_start_at_its_connect_on_the_simulators_clock(
    mjcf: str,
) -> None:
    """An MCP session's budget is built with the server, before the connect, on the transport's
    clock, and the simulator's clock is the wall's until it connects and the world's after. The
    minutes were one taken from the other, ran negative and never reached `max_minutes`. They
    start at 0 at the connect and count the world's seconds, so `max_minutes` trips on them."""
    from quackd.duckfile.schema import Budgets
    from quackd.mcp_server import build_fleet_server

    transport = LeRobotSim(model=mjcf)
    transport.connect_pause_s = 0.0
    adapter = LeRobotAdapter(transport)
    _, fleet = build_fleet_server({"arm": adapter}, memory=False, log=False)
    session = fleet.sessions["arm"]
    with _needs_gl():
        await session.connect()
    try:
        budget = session.executor.budget
        assert budget is not None
        first = await session.run("report_state", {})
        assert first["ok"], first
        assert 0.0 <= budget.elapsed_s < TICK_S, budget.status()
        assert budget.status().endswith(f"0.0/{budget.limits.max_minutes:g} min"), budget.status()
        # a limit a few ticks of the world's time long, then more than that slept on its clock
        budget.limits = Budgets(max_minutes=3 * TICK_S / 60)
        await adapter.sleep(4 * TICK_S)
        tripped = await session.run("report_state", {})
        assert not tripped["ok"] and "max_minutes" in tripped["summary"], tripped
    finally:
        await session.close()


async def test_a_rest_move_a_hand_off_and_a_verb_all_run_in_one_task(mjcf: str) -> None:
    """One task sleeps through all of it, one sleep at a time, and time runs for each: the rest
    move, the settle before the take-hold, and the verb after it."""
    rest = dict.fromkeys(BODY, 0.0)
    adapter, transport = await _sim_arm(mjcf, rest_pose=rest)
    try:
        assert adapter.manifest is not None
        executor = _executor(adapter, adapter.manifest)
        away = transport.joint_range_deg[PAN][1] / 8

        async def session() -> None:
            first = await executor.run_verb("move_joints", {"positions": {PAN: away}})
            assert first.ok, first.summary
            parked = await adapter.go_to_rest()
            assert parked.how == "arrived", parked.reason
            released = await adapter.let_go()
            assert released.how == "released", released.reason
            before = transport.now()
            held = await adapter.take_hold()
            assert held.how == "held", held.reason
            assert transport.now() - before >= transport.place_settle_s
            again = await executor.run_verb("move_joints", {"positions": {PAN: away}})
            assert again.ok, again.summary

        await asyncio.wait_for(session(), WALL_S)
    finally:
        await adapter.close()


async def test_a_twin_whose_fold_is_in_the_table_ends_at_rest_after_time_has_passed(
    mjcf: str,
) -> None:
    """The first real pilot on a twin found every run that let sim time pass closing stalled,
    with torque left on: the fold put the model into its table, the first physics step threw
    the arm out, and the rest move drove it back at a pose inside the table until it stopped.
    A run that never let time pass, the scripted pilot's, found it already there. The twin now
    starts settled clear of the table, holds that, and its close finds it there after a verb
    and a wait as well."""
    adapter, transport = await _sim_arm(mjcf, rest_pose=_into_the_table(_arm(mjcf)))
    try:
        world = transport.sim_world
        assert world is not None and world.settled
        assert any("mm into the table" in n for n in transport.connect_notes)
        assert adapter.manifest is not None
        executor = _executor(adapter, adapter.manifest)
        away = transport.joint_range_deg[PAN][1] / 8

        async def session() -> None:
            moved = await executor.run_verb("move_joints", {"positions": {PAN: away}})
            assert moved.ok, moved.summary
            await transport.sleep(transport.place_settle_s)
            # the run's teardown: a stop, the rest move, then the close
            await adapter.stop()
            parked = await adapter.go_to_rest()
            assert parked.how == "arrived", parked.reason

        await asyncio.wait_for(session(), WALL_S)
        assert transport.now() > transport.place_settle_s
    finally:
        await asyncio.wait_for(adapter.close(), WALL_S)
    assert transport.close_note is None, transport.close_note


def _folded_past_its_travel(arm: ArmModel) -> tuple[dict[str, float], dict[str, dict[str, int]]]:
    """A stand-in fold that starts clear of the table and is in it where the close parks it,
    and a calibration file's contents for it, from the stand-in's own proportions.

    The parked pose is the hand pointing down a quarter of its fingers' length into the table,
    near enough the base that the shoulder is tilted back, which is how the lab arm folds. The
    calibration's shoulder_lift travel ends at that shoulder, and the fold is the parked pose
    with the shoulder turned back from it, away from zero, until it is clear: past the floor of
    the travel, as a fold recorded with the arm tucked in is. shoulder_pan lies past the
    ceiling of its travel and inside its stops, which turns the arm and moves nothing up or
    down."""
    finger = standin.FINGER * standin.HAND * float(REACH.value)
    down = _pointing_down(arm, PLACE_NEAR / 2, -finger / 4)
    parked = {name: down[name] for name in BODY if name in down}
    assert parked["shoulder_lift"] < 0, "near the base the stand-in's shoulder tilts back"
    probe = ArmWorld(arm)
    try:
        assert probe._intrusions(probe._posed(parked)), "the parked pose is not in the table"
        fold = dict(parked)
        while probe._intrusions(probe._posed(fold)):
            fold["shoulder_lift"] -= TOL_DEG
    finally:
        probe.close()
    raw = _synthetic(arm, share=1.0)
    per_deg = (ENCODER_TICKS - 1) / 360.0
    lift = raw["shoulder_lift"]
    middle = (lift["range_min"] + lift["range_max"]) // 2
    half = math.floor(-parked["shoulder_lift"] * per_deg)
    lift["range_min"], lift["range_max"] = middle - half, middle + half
    raw["shoulder_pan"] = _synthetic(arm)["shoulder_pan"]
    ceiling = (raw["shoulder_pan"]["range_max"] - raw["shoulder_pan"]["range_min"]) / 2 / per_deg
    fold["shoulder_pan"] = (ceiling + arm.joints["shoulder_pan"].stops[1]) / 2
    return fold, raw


@pytest.mark.parametrize("move", ["the shoulder alone", "the arm upright"])
async def test_a_twin_folded_past_its_travel_ends_at_rest_after_that_joint_moved(
    mjcf: str, tmp_path: Path, move: str
) -> None:
    """A fold recorded past a joint's travel is parked at the edge of that travel by every close
    after the joint moved, and a fold that starts clear can be in the table there: the lab arm's
    twin's was, and every such close stalled short of its rest pose with torque on, though its
    start had been settled clear. The world settles the parked pose as well, and the joints
    beside the edge take where it came to rest, so the close arrives."""
    arm = _arm(mjcf)
    fold, raw = _folded_past_its_travel(arm)
    path = tmp_path / "arm.json"
    path.write_text(json.dumps(raw, indent=4), encoding="utf-8")
    adapter, transport = await _sim_arm(mjcf, address=str(path), rest_pose=fold)
    try:
        lo, hi = transport.joint_range_deg["shoulder_lift"]
        assert fold["shoulder_lift"] < lo, "the fold is not past the floor of its travel"
        assert fold["shoulder_pan"] > transport.joint_range_deg["shoulder_pan"][1]
        world = transport.sim_world
        assert world is not None and world.settled
        (note,) = [n for n in transport.connect_notes if "where the close's rest move parks" in n]
        assert "shoulder_lift" in note and "into the table" in note, note
        assert (await adapter.go_to_rest()).how == "already", "the start is not the rest pose"
        assert adapter.manifest is not None
        executor = _executor(adapter, adapter.manifest)
        goal = {"shoulder_lift": lo + (hi - lo) / 4}
        if move == "the arm upright":
            goal = {"shoulder_lift": (lo + hi) / 2, "elbow_flex": 0.0}

        async def session() -> None:
            await executor.run_verb("move_joints", {"positions": goal})
            await transport.sleep(transport.place_settle_s)
            await adapter.stop()
            parked = await adapter.go_to_rest()
            assert parked.how == "arrived", parked.reason

        await asyncio.wait_for(session(), WALL_S)
    finally:
        await asyncio.wait_for(adapter.close(), WALL_S)
    assert transport.close_note is None, transport.close_note


def test_a_parked_pose_no_close_could_call_at_rest_is_refused(
    mjcf: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Where the settle of the parked pose leaves a joint at the edge where no close could call
    it at rest, every run that moved it would end stalled, so the world is not built, and the
    refusal names the joint, the contacts and the command that gives the robot a pose."""
    arm = _arm(mjcf)
    fold, raw = _folded_past_its_travel(arm)
    travel = joint_ranges({name: MotorCalibration(**fields) for name, fields in raw.items()})
    monkeypatch.setattr("quackd_lerobot.sim.world.joint_at_rest", lambda *_: False)
    with pytest.raises(WorldError) as refused:
        ArmWorld(arm, rest_pose=fold, name="bench-twin", travel=travel)
    said = str(refused.value)
    assert "with shoulder_pan and shoulder_lift at the edges of the travel" in said, said
    assert "shoulder_lift pushed back into its travel" in said and "into the table" in said, said
    assert "would end with its rest move stalled" in said, said
    assert "quackd robot rest-pose bench-twin" in said, said


async def test_a_twin_the_settle_cannot_clear_is_refused_at_connect(mjcf: str) -> None:
    """A twin whose fold the settle cannot clear connected with a note and then stalled on its
    first move, with the close reporting it at rest only because it never moved. The connect is
    refused instead, before anything is opened, in the simulator's words."""
    elbow_lo, _ = _arm(mjcf).joints["elbow_flex"].stops
    transport = LeRobotSim(model=mjcf, rest_pose={"elbow_flex": elbow_lo})
    transport.connect_pause_s = 0.0
    with pytest.raises(TransportError) as refused:
        await LeRobotAdapter(transport).connect()
    said = str(refused.value)
    assert "so the simulated arm cannot start there" in said, said
    assert "quackd robot rest-pose NAME" in said, said
    assert transport.sim_world is None


async def test_a_pick_ends_on_the_grasp_its_own_loop_reads_and_stops_the_policy(
    mjcf: str,
) -> None:
    """The jaws start down at the table around a block, and the policy closes them and never
    says it is done. Only `holding`, judged on the loop's own reads mid segment, can end the
    pick, and it stops the policy there, so the next move is not refused. The verb waits on the
    segment and sleeps on no clock, so the loop's ticks are all the time that passed."""
    down = _pointing_down(_arm(mjcf), (PLACE_NEAR + PLACE_FAR) / 2, CUBE.size[0] / 2)
    policy = Scripted(lambda n: {JOINTS[-1]: GRIPPER_CLOSED})
    adapter, transport = await _sim_arm(
        mjcf, scene=_jaws_scene(), rest_pose={**dict.fromkeys(BODY, 0.0), **down}, policy=policy
    )
    try:
        assert adapter.manifest is not None
        executor = _executor(adapter, adapter.manifest, confirm=allow_all)
        start = transport.now()
        picked = await asyncio.wait_for(
            executor.run_verb("pick", {"target": "block", "max_s": 10}), WALL_S
        )
        assert picked.ok and picked.data["ended"] == "holding", picked.summary
        assert transport.now() - start == pytest.approx(policy.n / POLICY_HZ)
        assert not transport.policy_running
        # every tick's read fed the gripper's trace, not the registers' reads alone, which is
        # what let the grasp be seen on the tick it settled
        stamps = sorted({round(at, 6) for at, _ in transport._gripper_trace if at >= start})
        gaps = {round(later - at, 6) for at, later in itertools.pairwise(stamps)}
        assert gaps == {round(1.0 / POLICY_HZ, 6)}, stamps
        world = transport.sim_world
        assert world is not None and world.truth().objects["block"].pinched
        acted = policy.n
        lift = JOINTS[1]
        moved = await executor.run_verb(
            "move_joints",
            {"positions": {lift: down[lift] - TOL_DEG}, "duration_s": MOVE_MIN_S},
        )
        assert moved.ok, moved.summary
        assert policy.n == acted, "the policy was asked for another goal after the pick"
    finally:
        await adapter.close()


async def test_a_pick_on_the_simulator_ends_on_its_time_or_on_a_stop_from_another_task(
    mjcf: str,
) -> None:
    """The loop keeps `max_s` on the simulator's clock, which runs for it because it is the one
    sleeper. A stop from a task that sleeps on the same clock meanwhile ends the pick as
    stopped, with the stop named."""
    swing: dict[str, float] = {}
    policy = Scripted(lambda n: {PAN: swing[PAN] * (n % 2)})
    adapter, transport = await _sim_arm(mjcf, policy=policy)
    swing[PAN] = transport.joint_range_deg[PAN][1] / 8
    try:
        assert adapter.manifest is not None
        executor = _executor(adapter, adapter.manifest, confirm=allow_all)
        start = transport.now()
        max_s = 20 * TICK_S
        timed = await asyncio.wait_for(
            executor.run_verb("pick", {"target": "cup", "max_s": max_s}), WALL_S
        )
        assert timed.data["ended"] == "time", timed.summary
        # to within half a step of the clock, which the float sum of its steps can miss by
        half = (transport.sim_dt or TICK_S) / 2
        assert max_s - half < transport.now() - start < max_s + 2 * TICK_S

        # The stop is for a running segment, so it counts its ticks from the policy's first goal.
        # Counted from the `do`, it raced the segment's start on a slow runner: the clock runs
        # for the one sleeper, a start awaits the arm and the runner on the wall's time, and ten
        # ticks could pass before it was over, which refuses the `do` rather than stopping it.
        asked = policy.n

        async def stops_later() -> Any:
            await _until(lambda: policy.n > asked)
            await transport.sleep(10 * TICK_S)
            return await executor.run_verb("stop")

        start = transport.now()
        picked, stopped = await asyncio.wait_for(
            asyncio.gather(
                executor.run_verb("pick", {"target": "cup", "max_s": 60}), stops_later()
            ),
            WALL_S,
        )
        assert stopped.ok, stopped.summary
        assert picked.summary == "pick 'cup' stopped: a stop was sent to the arm", picked.summary
        assert 10 * TICK_S - half < transport.now() - start < 60
        assert not transport.policy_running
    finally:
        await adapter.close()


async def test_a_policy_thinks_in_no_sim_time_and_its_chunk_lands_its_latency_later(
    mjcf: str,
) -> None:
    """The simulator's clock is lockstep: while a runner thinks, in its own thread and for as
    long as the wall likes, no sim time passes, and the chunk it computed at a tick is played the
    runner's declared latency later, where it would have landed on the arm."""
    import time as wall

    from quackd.transport.base import Intent
    from quackd_lerobot.policy.runner import Observation
    from quackd_lerobot.policy.scripted import ScriptedRunner

    rate = 2 / TICK_S  # a whole number of the clock's steps a tick
    k = 3
    swing: dict[str, float] = {}
    thought: list[tuple[float, float]] = []
    clock: list[Any] = []

    def thinks(observation: Observation, _sent: Any) -> list[dict[str, float]]:
        before = clock[0].now()
        wall.sleep(0.02)
        thought.append((before, clock[0].now()))
        return [{PAN: swing[PAN] * ((observation.tick + i) % 2)} for i in range(2 * k)]

    runner = ScriptedRunner(thinks, rate_hz=rate, latency_ticks=k)
    adapter, transport = await _sim_arm(mjcf, policy=runner)
    swing[PAN] = transport.joint_range_deg[PAN][1] / 8
    clock.append(transport.clock)
    assert isinstance(transport.clock, SimClock) and transport.clock.lockstep
    robot = transport._robot
    sent_at: list[float] = []
    send = robot.send_action

    def stamped(action: dict[str, float]) -> Any:
        sent_at.append(transport.now())
        return send(action)

    robot.send_action = stamped
    try:
        start = transport.now()
        ack = await transport.send_intent(
            Intent(kind="do", params={"skill": "policy:manipulate:wave", "max_s": 20 / rate})
        )
        assert ack.accepted, ack.reason
        segment = transport.policy_segment
        assert segment is not None
        await asyncio.wait_for(asyncio.wait({segment}), WALL_S)
        ended = segment.result()
        assert ended.how == "time", ended.reason
        assert thought and all(before == after for before, after in thought), thought
        half = (transport.sim_dt or TICK_S) / 2
        assert sent_at[0] - start == pytest.approx(k / rate, abs=half), sent_at[0] - start
    finally:
        await adapter.close()


async def test_manipulates_timeout_covers_the_thinking_the_simulators_clock_waits_for(
    mjcf: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A slow policy on the simulator: its thinking costs no sim time and all of the executor's,
    which runs on the wall's clock. Asked every tick and taking a tick of wall time to answer, as
    it declares, it thinks for as long as the segment runs, so with the headroom taken away a
    timeout sized for the segment alone would end the verb early. The narrowed timeout adds what
    the backend says its clock stands still for, from the latency the policy declares, and the
    segment runs out on its own time. Each answer takes what is left of a tick for every answer
    so far, counted as the loop counts its thinking, so a busy machine that wakes one late has
    it made up by the next, as the pacer makes up a late tick, and never adds up to a policy
    slower than it declares, while the answers together still take the segment's length."""
    import time as wall

    from quackd.duckfile import narrow
    from quackd.duckfile.schema import DuckFrontmatter
    from quackd_lerobot.policy.runner import Observation
    from quackd_lerobot.policy.scripted import ScriptedRunner

    rate = 1 / TICK_S
    swing: dict[str, float] = {}

    def thinks(observation: Observation, _sent: Any) -> list[dict[str, float]]:
        loop = transport._policy_loop
        assert loop is not None
        wall.sleep(max(0.0, (loop.waited + 1) / rate - loop.thinking_s))
        # this tick's goal and the next, since the answer is let go a tick later
        return [{PAN: swing[PAN] * ((observation.tick + i) % 2)} for i in range(2)]

    runner = ScriptedRunner(thinks, rate_hz=rate, latency_ticks=1, per_tick=True)
    adapter, transport = await _sim_arm(mjcf, policy=runner)
    try:
        swing[PAN] = transport.joint_range_deg[PAN][1] / 8
        segment_s = 10 / rate
        contract = DuckFrontmatter.model_validate(
            {
                "duck": 3,
                "name": "slow",
                "description": "d",
                "verbs": {"allow": ["manipulate"]},
                "success": ["x"],
                "policy": {"segment_s": segment_s, "total_s": segment_s},
            }
        )
        monkeypatch.setattr(narrow, "SEGMENT_HEADROOM_S", 0.0)
        assert adapter.manifest is not None
        ex = _executor(adapter, adapter.manifest, confirm=allow_all)
        narrowed = narrow.narrow_policy_verb(ex.registry, contract, adapter)
        frozen = transport.frozen_inference_s(segment_s)
        assert frozen >= segment_s, "a tick of thinking every tick is the segment's length"
        assert narrowed is not None and narrowed.timeout_s == pytest.approx(segment_s + frozen)
        started = wall.perf_counter()
        ran = await asyncio.wait_for(ex.run_verb("manipulate", {"instruction": "wave"}), WALL_S)
        took = wall.perf_counter() - started
        assert ran.ok and ran.data["ended"] == "time", ran.summary
        assert ran.data["seconds"] == pytest.approx(segment_s, abs=TICK_S)
        assert took > segment_s, "the thinking never outlasted the segment, so this proves nothing"
        assert adapter.slow_policy() is None, "a policy as fast as it declares is no reason"
    finally:
        await adapter.close()


async def test_a_policy_slower_than_it_declares_is_named_when_manipulate_times_out(
    mjcf: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The other side of that timeout: a policy that answers more slowly on the wall than the
    latency it declares outruns what the simulator counted for its thinking, and the executor's
    timeout ends the segment. That used to say only that `manipulate` timed out, which reads as
    a simulator that hung. It says the policy answered slower than it declared, with the time
    one request took and the latency declared, and to bench it."""
    import time as wall

    from quackd.duckfile import narrow
    from quackd.duckfile.schema import DuckFrontmatter
    from quackd_lerobot.policy.runner import Observation
    from quackd_lerobot.policy.scripted import ScriptedRunner

    rate = 1 / TICK_S
    slower = 3  # times the latency it declares
    swing: dict[str, float] = {}

    def thinks(observation: Observation, _sent: Any) -> list[dict[str, float]]:
        wall.sleep(slower / rate)
        return [{PAN: swing[PAN] * ((observation.tick + i) % 2)} for i in range(2)]

    runner = ScriptedRunner(thinks, rate_hz=rate, latency_ticks=1, per_tick=True)
    adapter, transport = await _sim_arm(mjcf, policy=runner)
    try:
        swing[PAN] = transport.joint_range_deg[PAN][1] / 8
        segment_s = 10 / rate
        contract = DuckFrontmatter.model_validate(
            {
                "duck": 3,
                "name": "slow",
                "description": "d",
                "verbs": {"allow": ["manipulate"]},
                "success": ["x"],
                "policy": {"segment_s": segment_s, "total_s": segment_s},
            }
        )
        monkeypatch.setattr(narrow, "SEGMENT_HEADROOM_S", 0.0)
        assert adapter.manifest is not None
        ex = _executor(adapter, adapter.manifest, confirm=allow_all)
        narrowed = narrow.narrow_policy_verb(ex.registry, contract, adapter)
        assert narrowed is not None
        ran = await asyncio.wait_for(ex.run_verb("manipulate", {"instruction": "wave"}), WALL_S)
        said = ran.summary
        assert not ran.ok and said.startswith(
            f"manipulate timed out after {narrowed.timeout_s:g}s; stopped. The policy answered "
            "slower than the latency it declared"
        ), said
        took = re.search(r"one request took (\d+\.\d+) s on the wall's clock", said)
        assert took and float(took[1]) >= slower / rate - TICK_S / 2, said
        assert f"against the {runner.latency_s():g} s it declares" in said, said
        assert "quackd policy check --bench" in said, said
    finally:
        await adapter.close()


async def test_a_rate_or_a_latency_the_loop_would_not_run_leaves_no_thinking_to_wait_for(
    mjcf: str,
) -> None:
    """A runner whose declared rate the loop refuses has its segments refused at their start, and
    one whose declared latency is `FIRST_CHUNK_S` or more has them starve before a chunk lands,
    so neither has thinking the timeout must cover. Their numbers are left out of the bound,
    which a rate past any policy's made too large to count requests in, and which raised out of
    the narrowing, at connect over MCP or after a task file was adopted."""
    from quackd.duckfile.narrow import (
        FROZEN_INFERENCE_MAX_S,
        frozen_inference_s,
        narrow_policy_verb,
    )
    from quackd.duckfile.schema import SEGMENT_HEADROOM_S, SEGMENT_MAX_S, DuckFrontmatter
    from quackd_lerobot.policy.loop import FIRST_CHUNK_S
    from quackd_lerobot.policy.scripted import ScriptedRunner

    rate = 1 / TICK_S
    runner = ScriptedRunner(lambda _o, _s: None, rate_hz=rate, latency_ticks=1)
    adapter, transport = await _sim_arm(mjcf, policy=runner)
    try:
        assert transport.frozen_inference_s(SEGMENT_MAX_S) > 0, "nothing to take away"
        # a segment too long to count its ticks in is bounded by nothing, and says so
        assert transport.frozen_inference_s(sys.float_info.max) == math.inf
        assert frozen_inference_s(adapter, sys.float_info.max) == FROZEN_INFERENCE_MAX_S
        contract = DuckFrontmatter.model_validate(
            {
                "duck": 3,
                "name": "long",
                "description": "d",
                "verbs": {"allow": ["manipulate"]},
                "success": ["x"],
                "policy": {"segment_s": SEGMENT_MAX_S, "total_s": SEGMENT_MAX_S},
            }
        )
        assert adapter.manifest is not None
        registry = _executor(adapter, adapter.manifest).registry
        slow = math.ceil(FIRST_CHUNK_S * rate)
        for declared in ({"rate_hz": sys.float_info.max}, {"latency_ticks": slow}):
            for key, value in declared.items():
                setattr(runner, key, value)
            await transport._time_thinking()
            assert transport.frozen_inference_s(SEGMENT_MAX_S) == 0.0, declared
            narrowed = narrow_policy_verb(registry, contract, adapter)
            assert narrowed is not None
            assert narrowed.timeout_s == SEGMENT_MAX_S + SEGMENT_HEADROOM_S, declared
            runner.rate_hz, runner.latency_ticks = rate, 1
    finally:
        await adapter.close()


async def test_a_policy_whose_period_is_no_whole_number_of_steps_still_sends_once_a_period(
    mjcf: str,
) -> None:
    """The simulator's clock wakes on its own steps, the nearest number of them to what a sleep
    asks for, and a policy's period need not be a whole number of them. Every tick is still sent
    inside its own period, never before its deadline and never two to a period."""
    from quackd.transport.base import Intent
    from quackd_lerobot.policy.scripted import ScriptedRunner

    rate = 3 / TICK_S
    swing: dict[str, float] = {}
    runner = ScriptedRunner(
        lambda o, _s: [{PAN: swing[PAN] if o.tick % 2 else -swing[PAN]}], rate_hz=rate
    )
    adapter, transport = await _sim_arm(mjcf, policy=runner)
    swing[PAN] = transport.joint_range_deg[PAN][1] / 8
    period, dt = 1 / rate, transport.sim_dt
    assert dt is not None and dt < period
    steps = period / dt
    assert abs(steps - round(steps)) > 1e-6, "a period of whole steps tests nothing here"
    robot = transport._robot
    sent_at: list[float] = []
    send = robot.send_action

    def stamped(action: dict[str, float]) -> Any:
        sent_at.append(transport.now())
        return send(action)

    robot.send_action = stamped
    ticks = 20
    try:
        ack = await transport.send_intent(
            Intent(kind="do", params={"skill": "policy:pick:wave", "max_s": ticks * period})
        )
        assert ack.accepted, ack.reason
        segment = transport.policy_segment
        assert segment is not None
        await asyncio.wait_for(asyncio.wait({segment}), WALL_S)
        ended = segment.result()
        assert ended.how == "time" and ended.stats.skipped == 0, ended
        first = sent_at[0]
        periods = [math.floor((at - first) / period + 1e-9) for at in sent_at]
        assert periods == list(range(len(sent_at))), periods
        assert len(sent_at) == ticks
    finally:
        await adapter.close()


LONG_CHUNK_S = 2.0
"""How long a long chunk lasts, at the rate the test below plays it at."""


async def test_a_long_chunk_lands_as_the_last_runs_out_and_a_segment_plays_its_own_ticks(
    mjcf: str,
) -> None:
    """On the simulator's own clock, a runner of chunks `LONG_CHUNK_S` long at a named rate that
    declares half a chunk of latency, the most `quackd policy serve` takes. It is asked again as
    each chunk runs down to that half, so every chunk after the first lands as the last runs
    out and the arm is starved only while a segment's first is on its way. Two segments each
    play exactly the ticks their seconds hold, at a period that is no whole number of the
    clock's steps, and the run's record adds up what they said they played."""
    from quackd.transport.base import Intent
    from quackd_lerobot.policy.scripted import ScriptedRunner

    rate = 3 / TICK_S
    chunk = round(LONG_CHUNK_S * rate)
    k = chunk // 2
    swing: dict[str, float] = {}

    def script(observation: Any, _sent: Any) -> list[dict[str, float]]:
        return [{PAN: swing[PAN] * ((observation.tick + i) % 2)} for i in range(chunk)]

    runner = ScriptedRunner(script, rate_hz=rate, latency_ticks=k)
    adapter, transport = await _sim_arm(mjcf, policy=runner)
    swing[PAN] = transport.joint_range_deg[PAN][1] / 8
    dt = transport.sim_dt
    assert dt is not None and abs(1 / rate / dt - round(1 / rate / dt)) > 1e-6
    periods = 3 * chunk
    try:
        ends = []
        for _ in range(2):
            ack = await transport.send_intent(
                Intent(kind="do", params={"skill": "policy:pick:wave", "max_s": periods / rate})
            )
            assert ack.accepted, ack.reason
            segment = transport.policy_segment
            assert segment is not None
            await asyncio.wait_for(asyncio.wait({segment}), WALL_S)
            ends.append(segment.result())
        assert [(e.how, e.stats.ticks, e.stats.starved) for e in ends] == [
            ("time", periods, k)
        ] * 2, ends
        loop = transport._policy_loop
        assert loop is not None
        record = loop.record()
        assert record["ticks"] == 2 * periods and record["starved_ticks"] == 2 * k, record
        assert record["seconds"] == pytest.approx(2 * periods / rate, abs=0.01), record
    finally:
        await adapter.close()


async def test_a_released_arm_falls_before_it_is_taken_hold_of(mjcf: str) -> None:
    """Nobody places a simulated arm, so the take-hold lets gravity do it first: an arm let go
    tipped forward reads lower after the settle than at the release, and is taken hold of
    where it came to rest."""
    tilt = _arm(mjcf).joints["shoulder_lift"].stops[1] / 6  # forward, so gravity pulls it over
    adapter, transport = await _sim_arm(mjcf, rest_pose={"shoulder_lift": tilt})
    try:
        world = transport.sim_world
        assert world is not None
        released = await transport.let_go(anywhere=True)
        assert released.how == "released", released.reason
        height, lift = _hand_height(world), released.joints["shoulder_lift"]
        held = await transport.take_hold()
        assert held.how == "held", held.reason
        assert _hand_height(world) < height - LIFT_MIN_M
        assert held.joints["shoulder_lift"] > lift + TOL_DEG  # the way gravity pulls it
    finally:
        await adapter.close()


async def test_an_arm_that_falls_past_its_travel_is_left_in_the_hand(
    mjcf: str, tmp_path: Path
) -> None:
    """The fall is what places the arm, so it can place a joint past the travel its calibration
    records, and the take-hold refuses that before it writes anything: torque stays off."""
    arm = _arm(mjcf)
    calibration = tmp_path / "arm.json"
    calibration.write_text(json.dumps(_synthetic(arm, share=0.25)), encoding="utf-8")
    tilt = arm.joints["shoulder_lift"].stops[1] / 6
    adapter, transport = await _sim_arm(
        mjcf, address=str(calibration), rest_pose={"shoulder_lift": tilt}
    )
    try:
        lo, hi = transport.joint_range_deg["shoulder_lift"]
        assert lo < tilt < hi, "the arm must start inside its travel"
        await transport.let_go(anywhere=True)
        held = await transport.take_hold()
        assert held.how == "refused" and held.energised is False, held.reason
        assert "shoulder_lift" in held.outside and held.joints["shoulder_lift"] > hi
        assert transport.in_hand
        assert transport.sim_world is not None
        assert not any(transport.sim_world.torques().values()), "torque came on"
    finally:
        await adapter.close()


async def test_a_take_hold_that_meets_a_falling_arm_refuses_on_the_slip(mjcf: str) -> None:
    """A person who lets go before the arm has come to rest, of an arm whose shoulder can hold
    it where it was let go and no lower: torque comes on while it falls, the arm goes on
    falling, and the take-hold says it is not holding the pose it was given."""
    tilt = _arm(mjcf).joints["shoulder_lift"].stops[1] / 6
    adapter, transport = await _sim_arm(mjcf, rest_pose={"shoulder_lift": tilt})
    try:
        world = transport.sim_world
        assert world is not None
        lift = world.arm.joints["shoulder_lift"]
        with world.locked() as (model, data):
            mujoco.mj_forward(model, data)
            holding = abs(float(data.qfrc_bias[lift.dof]))  # gravity, where it stands
            model.actuator_forcerange[lift.actuator] = [-holding, holding]
        transport.place_settle_s = 3 * TICK_S  # too soon: it is still on its way down
        await transport.let_go(anywhere=True)
        held = await transport.take_hold()
        assert held.how == "refused" and "moved as torque came on" in held.reason, held.reason
        assert "shoulder_lift" in held.reason and held.energised is True
        assert not transport.in_hand, "an energised arm is not limp in anybody's hands"
    finally:
        await adapter.close()


async def test_the_viewer_closing_while_the_arm_falls_stops_the_take_hold(mjcf: str) -> None:
    """Closing the viewer during the settle is a person's stop, as it is during a verb. The
    take-hold ends there with torque still off, and the run's own take-hold reports it as a
    refusal, rather than going on to energise an arm nobody is watching any more."""
    from quackd.adapters.base import take_hold_if_any
    from quackd.safety import Aborted

    tilt = _arm(mjcf).joints["shoulder_lift"].stops[1] / 6
    adapter, transport = await _sim_arm(mjcf, rest_pose={"shoulder_lift": tilt})
    world, clock = transport.sim_world, transport.clock
    assert world is not None and isinstance(clock, SimClock)

    def window_closed(_: Any) -> None:
        raise KeyboardInterrupt  # what the live viewer's tick hook raises

    try:
        await transport.let_go(anywhere=True)
        clock.add_tick_hook(window_closed)
        with pytest.raises(Aborted):
            await asyncio.wait_for(transport.take_hold(), WALL_S)
        assert transport.in_hand
        assert not any(world.torques().values()), "torque came on after the stop"
        clock.add_tick_hook(window_closed)
        refused = await asyncio.wait_for(take_hold_if_any(transport), WALL_S)
        assert refused.how == "refused" and "Aborted" in refused.reason, refused.reason
        assert not any(world.torques().values()), "torque came on after the stop"
    finally:
        await adapter.close()


async def test_the_truth_is_latched_on_the_way_into_a_stop_and_a_rest_move(mjcf: str) -> None:
    rest = dict.fromkeys(BODY, 0.0)
    adapter, transport = await _sim_arm(mjcf, rest_pose=rest)
    world = transport.sim_world
    assert world is not None
    try:
        assert world.latched("stop") is None and world.latched("rest") is None
        at = transport.now()
        await adapter.stop()
        stopped = world.latched("stop")
        assert stopped is not None and stopped.t == pytest.approx(at)
        assert adapter.manifest is not None
        away = transport.joint_range_deg[PAN][1] / 4
        moved = await _executor(adapter, adapter.manifest).run_verb(
            "move_joints", {"positions": {PAN: away}}
        )
        assert moved.ok, moved.summary
        at = transport.now()
        parked = await adapter.go_to_rest()
        assert parked.how == "arrived", parked.reason
        latched = world.latched("rest")
        assert latched is not None and latched.t == pytest.approx(at)
        assert transport.now() > at, "the rest move took no time, so the latch proves nothing"
        state = await adapter.get_state()
        said = json.dumps(state.extras)
        assert not any(obj.name in said for obj in world.arm.objects), "the truth reached extras"
    finally:
        await adapter.close()
    assert world.closed and world.latched("rest") is latched, "a latch outlives the close"


async def test_the_heartbeat_draws_no_fault_and_feeds_no_grasp_but_meets_a_lost_arm(
    mjcf: str,
) -> None:
    """Its reads run on the wall's clock, so a seeded fault on them would land on a different
    call every run, and a grasp judged on them would be judged differently every run. A lost
    arm is lost to it too, and it stops the run as a pulled cable would."""
    plan = FaultPlan.parse("torque_read=1,temperature_read=1,read_loss_from=2", seed=0)
    adapter, transport = await _sim_arm(mjcf, faults=plan)
    try:
        follower = transport._robot
        assert isinstance(follower, SimFollower)
        assert transport._register_error is not None, "the connect's own read drew the faults"
        drawn, trace = len(follower.faults.injected), len(transport._gripper_trace)
        await transport.heartbeat()
        assert transport._register_error is None, "the heartbeat's register reads failed"
        assert len(follower.faults.injected) == drawn
        assert len(transport._gripper_trace) == trace
        with pytest.raises(ConnectionError, match="Failed to sync read"):
            await transport.get_state()  # the second observation: the arm drops off the bus
        with pytest.raises(HeartbeatError, match="did not answer"):
            await transport.heartbeat()
    finally:
        await adapter.close()


BUSY_BEAT = 3
"""The heartbeat whose probe the event loop's thread is blocked behind: a few in, so the beats
before it show the heartbeat was running."""


@pytest.mark.parametrize("how", ["the loop is busy", "a step stalls", "the read hangs"])
async def test_a_heartbeat_the_arm_answered_in_time_is_kept_while_the_loop_was_busy(
    mjcf: str, how: str
) -> None:
    """A run's pilot can hold the event loop's thread for a second or more, parsing its first
    response or encoding a frame. A heartbeat probe out at that moment came back in well under a
    millisecond, and the deadline fired first when the loop resumed, so an arm that answered in
    time was reported as one that did not, and the run was stopped. A verb's step can hold the
    thread the same way just after its own read came back, with a probe queued behind that read:
    the probe was handed the bus late and cancelled as it went out, and the run was stopped over
    a bus that was free. The answer is kept now, both ways. A read that really does not come back
    in time still stops the run, and says which call it was and its budget."""
    adapter, transport = await _sim_arm(mjcf)
    loop = asyncio.get_running_loop()
    deadline = transport.timeout_s / 4  # every heartbeat probe's budget, kept short for the test
    busy = 2 * deadline
    transport.timeout_s = deadline
    release = threading.Event()
    probes = 0
    reads = transport._heartbeat_reads

    def heartbeat_reads() -> Callable[[], Any]:
        nonlocal probes
        read = reads()
        probes += 1
        if probes != BUSY_BEAT or how == "a step stalls":
            return read
        if how == "the read hangs":

            def hung() -> Any:
                # out until the test lets it go, so it has not come back however late a loaded
                # runner's loop is to judge it
                release.wait(WALL_S)
                return read()

            return hung
        # runs as soon as `_call` hands the read to its worker and waits: the loop's thread
        # sleeps through the deadline while the worker answers
        loop.call_soon(time.sleep, busy)
        return read

    async def step() -> None:
        """A verb's read, out on the bus until a probe waits behind it, and then the rest of the
        verb's step, which holds the loop's thread while the probe the release woke waits."""
        asked = probes  # any probe asked after this read is queued behind it

        def sync_read() -> None:
            started = time.monotonic()
            while probes == asked and time.monotonic() - started < WALL_S:
                time.sleep(deadline / 100)

        await transport._call(sync_read, deadline_s=WALL_S)
        time.sleep(busy)  # noqa: ASYNC251  (in this step, which the woken probe waits on)

    transport._heartbeat_reads = heartbeat_reads  # type: ignore[method-assign]
    abort = asyncio.Event()
    said: list[str] = []
    beat = Heartbeat(transport, abort, period_s=deadline / 4, log=said.append)
    stepped = False
    try:
        beat.start()
        started = time.monotonic()
        while probes <= BUSY_BEAT + 2 and not abort.is_set():
            assert time.monotonic() - started < WALL_S
            if how == "a step stalls" and probes >= BUSY_BEAT - 1 and not stepped:
                stepped = True
                await step()
            await asyncio.sleep(deadline / 4)
        await beat.stop()
        if how == "the read hangs":
            assert abort.is_set() and beat.failure is not None, said
            failure = str(beat.failure)
            assert "the arm did not answer: TimeoutError: " in failure, failure
            assert f"(hung) has not come back within {deadline:g} s" in failure, failure
        else:
            assert not abort.is_set(), said
            assert beat.failure is None and beat.beats > BUSY_BEAT, said
            assert stepped or how != "a step stalls"
    finally:
        release.set()
        await beat.stop()
        if (hung := transport._wedged) is not None:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(asyncio.shield(hung), WALL_S)
        await adapter.close()


async def test_a_refusal_from_the_simulator_says_it_is_the_simulator(mjcf: str) -> None:
    with pytest.raises(TransportError) as refused:
        await _sim_arm(mjcf, faults=FaultPlan.parse("handshake=1", seed=0))
    said = str(refused.value)
    assert said.startswith("lerobot mujoco: connect failed"), said
    assert "lerobot real" not in said


async def test_a_close_away_from_rest_says_what_that_means_on_a_simulator(mjcf: str) -> None:
    """The real close keeps torque on an arm away from its rest pose, and tells the person to
    hold it, release it, park it with doctor or cut its power. On the simulator it keeps the
    same torque, since it is the same code, and says what applies to a simulated arm: nothing
    is left holding, and the shortfall is what to look at."""
    adapter, transport = await _sim_arm(mjcf, rest_pose={PAN: 0.0})
    try:
        assert adapter.manifest is not None
        away = transport.joint_range_deg[PAN][1] / 8
        moved = await _executor(adapter, adapter.manifest).run_verb(
            "move_joints", {"positions": {PAN: away}}
        )
        assert moved.ok, moved.summary
    finally:
        await adapter.close()  # no rest move first: the close finds the arm away from it
    note = transport.close_note or ""
    assert note.startswith(f"the simulated arm is not at its rest pose ({PAN} is at "), note
    assert "torque was left on" in note and "ends with the run" in note, note
    for real in ("quackd robot release", "doctor --robot", "cut its power", "hold it first"):
        assert real not in note, note


async def test_a_run_on_the_simulator_is_not_asked_to_hold_the_arm(
    mjcf: str, tmp_path: Path
) -> None:
    """A run at a terminal whose rest move missed offers the person torque off, and tells them
    to hold the arm first, because the release drops it. On the simulator that asked them to
    hold an arm that is not there, and the close then said there was nothing to hold. No offer
    is made on a simulator, and the close's own line is what is said."""
    from quackd.agent.loop import RunConfig, run_duck
    from quackd.agent.providers.base import ToolCall
    from quackd.agent.providers.fake import FakeProvider
    from tests.test_loop import ARM_REST, ScriptedPerson, _arm_duck

    transport = LeRobotSim(model=mjcf, rest_pose=ARM_REST)
    transport.connect_pause_s = 0.0
    arrive = transport.go_to_rest
    moves = 0

    async def go_to_rest(*args: Any, **kwargs: Any) -> RestResult:
        nonlocal moves
        moves += 1
        if moves == 1:  # the run's start
            return await arrive(*args, **kwargs)
        # the teardown's, stopped short of the pose, as a move against the table would
        goal, recorded = transport._rest_target()
        why = shortfall(goal, dict(transport._joints), recorded)
        missed = RestResult("stalled", f"{why}, and it has stopped moving")
        transport._rest_result = missed
        return missed

    transport.go_to_rest = go_to_rest  # type: ignore[method-assign]
    duck = _arm_duck()
    duck.frontmatter.verbs.allow = [*duck.frontmatter.verbs.allow, "move_joints"]
    script = [
        ToolCall(name="move_joints", arguments={"positions": {PAN: ARM_REST[PAN] / 2}}),
        ToolCall(name="declare_success", arguments={"reason": "moved"}),
    ]
    person = ScriptedPerson(None, answers=[False])  # type: ignore[arg-type]
    person.asks_a_person = True
    config = RunConfig(
        duck=duck,
        provider=FakeProvider(script=script),
        transport=LeRobotAdapter(transport),
        runs_dir=tmp_path,
        person=person,
    )
    with _needs_gl():
        await asyncio.wait_for(run_duck(config), WALL_S)
    assert moves == 2, "the teardown made no rest move"
    assert person.asked == [], person.asked
    note = transport.close_note or ""
    assert note.startswith("the simulated arm is not at its rest pose ("), note


@pytest.mark.parametrize(
    ("names", "said"),
    [
        (("front", "wrist"), "the front camera is quackd's default view of the table"),
        (("top",), "the top camera is quackd's default view of the table"),
        (("front", "top"), "the front and top cameras are quackd's default views of the table"),
        (("wrist",), None),
    ],
)
async def test_the_default_views_note_names_the_views_the_run_has(
    mjcf: str, names: tuple[str, ...], said: str | None
) -> None:
    """A run on front and wrist, the lab's layout, was told the front and top cameras were
    quackd's views, naming a camera it never opened. The note and the state's assumptions name
    the ones open, and a run with neither says nothing about them."""
    adapter = make("mujoco", camera_url=[f"opencv://{i}?name={n}" for i, n in enumerate(names)])
    transport = adapter.transport
    assert isinstance(transport, LeRobotSim)
    transport.model_source = mjcf
    with _needs_gl():
        await adapter.connect()
    try:
        views = [n for n in transport.connect_notes if "default view" in n]
        assumptions = (await transport.get_state()).extras["assumptions"]
        if said is None:
            assert views == [] and not any("default view" in a for a in assumptions)
        else:
            assert len(views) == 1 and views[0].startswith(said), views
            assert views[0] in assumptions
            unopened = {"front", "top"} - set(names)
            assert not any(f" {n} " in views[0] for n in unopened), views[0]
    finally:
        await adapter.close()


async def test_physics_that_cannot_step_on_stop_the_heartbeat(mjcf: str) -> None:
    adapter, transport = await _sim_arm(mjcf)
    try:
        world = transport.sim_world
        assert world is not None
        with world.locked() as (_, data):
            data.qvel[world.arm.joints["elbow_flex"].dof] = math.nan
        with pytest.raises(TransportError, match="diverged"):
            await asyncio.wait_for(transport.sleep(TICK_S), WALL_S)
        with pytest.raises(HeartbeatError, match="could not step"):
            await transport.heartbeat()
    finally:
        await adapter.close()


async def test_an_exit_that_skips_the_close_leaves_the_simulated_arm_holding(mjcf: str) -> None:
    """ADR-0036's second Ctrl-C: a rest move under way, then the process ends before the close.
    LeRobot disconnects a follower nobody closed as it is collected, by whatever its config
    says by then (`upstream_api.ROBOT_DEL`), and the real backend builds it asking to keep the
    torque, so the arm is left holding rather than dropped where it stood."""
    adapter, transport = await _sim_arm(mjcf, rest_pose=dict.fromkeys(BODY, 0.0))
    world, clock = transport.sim_world, transport.clock
    assert world is not None and isinstance(clock, SimClock)
    assert adapter.manifest is not None
    away = transport.joint_range_deg[PAN][1] / 4
    moved = await _executor(adapter, adapter.manifest).run_verb(
        "move_joints", {"positions": {PAN: away}}
    )
    assert moved.ok, moved.summary
    under_way = asyncio.Event()
    start = clock.now()
    clock.add_tick_hook(lambda s: under_way.set() if s.t >= start + 2 * TICK_S else None)
    resting = asyncio.create_task(transport.go_to_rest())
    await asyncio.wait_for(under_way.wait(), WALL_S)
    resting.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await resting
    if (wedged := transport._wedged) is not None:
        await asyncio.wait([wedged])  # a call cut off on the wire still holds the follower
    collected = weakref.ref(transport._robot)
    del adapter, transport, resting
    for _ in range(WALL_TURNS):  # the cancelled task lets go of its frames a loop turn later
        gc.collect()
        if collected() is None:
            break
        await asyncio.sleep(0)
    assert collected() is None, "the follower was not collected"
    assert all(world.torques().values()), "a follower nobody closed dropped the arm"
    await clock.close()
    world.close()


def test_the_gif_flag_and_the_readme_say_the_arm_simulator_writes_no_gif() -> None:
    """`--gif` is on by default and said that simulators write run.gif, and the arm's
    simulator is one that writes none, because the recorder draws only the 2D world and the
    Microduck's. A person who reads the help and finds no GIF has to be told why somewhere,
    and the help and the README's list of what a run writes are the two places they look."""
    from tests.conftest import help_text

    run_help = help_text(["run", "--help"])
    assert "Simulators: write run.gif into the run dir. The arm's simulator," in run_help
    assert "lerobot:mujoco, writes none." in run_help
    readme = (Path(__file__).resolve().parents[1] / "README.md").read_text(encoding="utf-8")
    assert "the arm's simulator, `lerobot:mujoco`, writes none" in readme


def test_the_lookout_runs_on_the_simulated_arm_end_to_end(
    mjcf: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`quackd run` as a person types it, on the stand-in, with the scripted pilot and memory
    off: the task connects, reads the arm back, succeeds and closes. With the GIF left on, as
    it is by default: the recorder draws a world the arm's simulator does not have, so no GIF
    is written, and the run used to finish and then crash writing a GIF of no frames. The
    pilot is told about the arm's simulator, and not about the Microduck's arena and ball,
    which it was while both notes keyed on the backend name alone."""
    from typer.testing import CliRunner

    from quackd.agent.transcript import Transcript
    from quackd.cli import app

    monkeypatch.setattr("quackd_lerobot.sim.transport.default_model", lambda: mjcf)
    runs = tmp_path / "runs"
    result = CliRunner().invoke(
        app,
        [
            "run",
            "lerobot-lookout",
            "--llm",
            "fake",
            "--robot",
            "lerobot:mujoco",
            "--no-memory",
            "--runs-dir",
            str(runs),
            "--no-log",
        ],
    )
    if "no OpenGL context" in result.output and os.environ.get(REQUIRE_ENV) != "1":
        pytest.skip("no OpenGL context for offscreen rendering")
    assert result.exit_code == 0, result.output
    assert "SUCCESS" in result.output, result.output
    assert not list(runs.rglob("run.gif")), "a GIF was written of a world this does not have"
    events = Transcript.read(next(runs.rglob("transcript.jsonl")))
    verbs = [e.get("name") for e in events if e["kind"] == "verb_end"]
    assert "report_state" in verbs, verbs
    system = next(e["system_prompt"] for e in events if e["kind"] == "run_start")
    assert "the arm's physics simulator (MuJoCo)" in system
    assert "arena" not in system and "orange ball" not in system
    # run with no camera, which the note says rather than pointing at cameras it has not got
    assert "This run has no camera" in system and "what the cameras show" not in system


def test_doctor_and_release_on_a_registered_simulated_arm_say_it_is_the_simulator(
    mjcf: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The arm's simulator is handed to people as the arm is, so `doctor --robot` and
    `robot release` warn before they connect it. What they used to say was the arm's: support
    it, hold it now. Nobody can hold a simulated arm, so each says it is the simulator instead.
    `doctor --robot NAME` also reaches the simulator through the calibration the robot was
    registered with, and its connect, which renders once, is the check that this machine
    can draw the scene."""
    from typer.testing import CliRunner

    from quackd import doctor
    from quackd.cli import app

    monkeypatch.setattr("quackd_lerobot.sim.transport.default_model", lambda: mjcf)
    monkeypatch.setattr(doctor, "_probe_models", lambda url, timeout_s=1.5: ("down", "not running"))
    given = tmp_path / "arm-sim.json"
    given.write_text(json.dumps(_synthetic(_arm(mjcf))), encoding="utf-8")
    reg = ["--registry-dir", str(tmp_path / "registry")]
    runner = CliRunner()
    added = runner.invoke(
        app, ["robot", "add", "arm-sim", "lerobot:mujoco", "--address", str(given), *reg]
    )
    assert added.exit_code == 0, added.output

    probed = runner.invoke(app, ["doctor", "--robot", "arm-sim", *reg])
    flat = " ".join(probed.output.split())
    if "no OpenGL context" in flat and os.environ.get(REQUIRE_ENV) != "1":
        pytest.skip("no OpenGL context for offscreen rendering")
    assert (
        "this is the arm's simulator: connecting takes torque off every simulated motor for a "
        "moment, as LeRobot's connect does on a real arm, and there is no arm to support"
    ) in flat, flat
    assert "support the arm until doctor" not in flat, flat
    report = json.loads(runner.invoke(app, ["doctor", "--robot", "arm-sim", "--json", *reg]).stdout)
    probe = report["robot"]["probe"]
    assert probe["ok"] is True, probe

    released = runner.invoke(app, ["robot", "release", "arm-sim", "--yes", *reg])
    flat = " ".join(released.output.split())
    assert (
        "this is the arm's simulator: connecting takes torque off every simulated motor for a "
        "moment, as LeRobot's connect does on a real arm, and the release then lets the "
        "simulated arm fall from wherever it is, with no arm to hold"
    ) in flat, flat
    assert "hold it now" not in flat, flat
    assert released.exit_code == 0, flat
    # and it does not end, as a real arm's release does, by putting the arm in somebody's hands
    assert "in your hands" not in flat and "put it down before" not in flat, flat
    assert (
        "this was the arm's simulator: the simulated arm settled where the model's physics "
        "left it when it closed, and there is nothing to put down"
    ) in flat, flat


def test_doctor_connects_a_registered_simulator_that_was_given_no_address(
    mjcf: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A simulator registered without `--address` runs on the calibration LeRobot keeps under
    its name, and doctor only ever connected a robot that had an address. So it reported a
    machine healthy on which `quackd run --robot NAME` was refused at connect, and never made
    the one render that says this machine can draw the scene. It connects the simulator now,
    and a real body with no address is still never guessed at."""
    from typer.testing import CliRunner

    from quackd import doctor
    from quackd.cli import app

    monkeypatch.setattr("quackd_lerobot.sim.transport.default_model", lambda: mjcf)
    monkeypatch.setattr(doctor, "_probe_models", lambda url, timeout_s=1.5: ("down", "not running"))
    reg = ["--registry-dir", str(tmp_path / "registry")]
    runner = CliRunner()
    added = runner.invoke(app, ["robot", "add", "arm-sim", "lerobot:mujoco", *reg])
    assert added.exit_code == 0, added.output

    def report() -> dict[str, Any]:
        out = runner.invoke(app, ["doctor", "--robot", "arm-sim", "--json", *reg]).stdout
        return dict(json.loads(out))

    missing = report()
    probe = missing["robot"]["probe"]
    assert probe is not None, "doctor never connected the simulator"
    assert probe["ok"] is False and missing["ok"] is False
    assert "arm-sim has no calibration file where LeRobot would look" in probe["error"]
    assert " at :" not in probe["error"], probe["error"]

    found = calibration_path("arm-sim")
    found.parent.mkdir(parents=True, exist_ok=True)
    found.write_text(json.dumps(_synthetic(_arm(mjcf))), encoding="utf-8")
    probed = runner.invoke(app, ["doctor", "--robot", "arm-sim", *reg])
    flat = " ".join(probed.output.split())
    if "no OpenGL context" in flat and os.environ.get(REQUIRE_ENV) != "1":
        pytest.skip("no OpenGL context for offscreen rendering")
    assert "what the robot itself reported" in flat and "at : what" not in flat, flat
    probe = report()["robot"]["probe"]
    assert probe["ok"] is True, probe

    # the same registry, a real arm with no address: described, and never connected
    real = runner.invoke(app, ["robot", "add", "arm-real", "lerobot:real", *reg])
    assert real.exit_code == 0, real.output
    out = runner.invoke(app, ["doctor", "--robot", "arm-real", "--json", *reg]).stdout
    assert json.loads(out)["robot"]["probe"] is None


# ── the real model ──────────────────────────────────────────────────────────────────────


@pytest.mark.so101_model
def test_the_so101_model_loads_with_its_fingers_cut_to_pads() -> None:
    from quackd_lerobot.sim.assets import AssetError, ensure_so101

    try:
        so101 = ensure_so101(offline=True)
    except AssetError as e:
        pytest.skip(f"the SO-101's model is not fetched: {e}")
    raw = mujoco.MjSpec.from_file(str(so101.model_path)).compile()
    arm = load(so101.model_path, seed=0)
    model = arm.model
    # drawn in greys, however upstream colours it, so the colour detector finds nothing on it
    assert raw.nmat and any(len(set(rgba[:3])) > 1 for rgba in raw.mat_rgba)
    for rgba in model.mat_rgba:
        assert rgba[0] == pytest.approx(rgba[1]) and rgba[1] == pytest.approx(rgba[2]), rgba
    for pad in (FIXED_PAD, MOVING_PAD, PALM_PAD):
        g = model.geom(pad).id
        assert model.geom_type[g] == mujoco.mjtGeom.mjGEOM_BOX and (model.geom_size[g] > 0).all()
    for g in range(model.ngeom):
        if model.geom_type[g] == mujoco.mjtGeom.mjGEOM_MESH:
            mesh = model.mesh(model.geom_dataid[g]).name
            if mesh in (up.FIXED_FINGER_MESH, up.MOVING_JAW_MESH):
                assert not model.geom_contype[g] and not model.geom_conaffinity[g], mesh
    gripper = arm.gripper
    share = lr.GRIPPER_MAX_TORQUE_LIMIT / lr.MAX_TORQUE_LIMIT_FULL
    assert model.actuator_forcerange[gripper.actuator] == pytest.approx(
        raw.actuator_forcerange[gripper.actuator] * share
    )
    data = mujoco.MjData(model)
    apart = {}
    for end in (gripper.closed, gripper.open):
        data.qpos[:] = model.qpos0
        data.qpos[gripper.qpos] = end
        mujoco.mj_forward(model, data)
        apart[end] = mujoco.mj_geomDistance(model, data, arm.fixed_pad, arm.moving_pad, 1.0, None)
    assert 0 < apart[gripper.closed] < apart[gripper.open]
    world = ArmWorld(arm)
    start = world.positions()
    world.step(1.0)
    for name in BODY:
        assert _deg(world, name) == pytest.approx(
            arm.joints[name].to_lerobot(start[name]), abs=TOL_DEG
        )
    assert all(not o.touching for o in world.truth().objects.values())
