"""The Microduck itself: upstream's MuJoCo model, driven by upstream's own walk policy.

This is the first quackd body whose legs are not a fiction. The model, the fourteen servos
and the two perpetual policies (`alpha_walking`, `alpha_stand`) are Pollen's, fetched at run
time by `assets.py`; the 50 Hz loop around them is `scripts/infer_policy.py`'s, cited fact by
fact in `upstream_api.py`. quackd supplies one thing: a twist and a head pose, which is
exactly what a gamepad supplies on the real robot.

Two things a reader should know before trusting a run.

**The gait has a floor.** Under the model's own PD actuators the walking policy does not step
below about 0.23 m/s or 1.0 rad/s: it stands and shifts its weight. quackd's `move` defaults
to 0.15 m/s and `go_to` creeps at 0.05, so passing those through unchanged would give a duck
that reports walking and does not move — the worst kind of failure. A non-zero twist is
therefore raised to the floor, a very small one is dropped to zero rather than amplified into
a lurch, and both the state and the prompt say so.

**Four skills are stand-ins.** `kick` and `grab` use the cartoon's contact rules rather than
upstream's episodic policies, which did nothing from a standing pose when tried; `sit` is
refused, because the sit-stand policy put the duck on its back; and a fall is recovered by
standing the model up again, because upstream has no get-up policy. Every one of them is
named in `state.extras.assumptions`, so a transcript never implies more than happened.
"""

from __future__ import annotations

import logging
import math
from typing import Any

import mujoco
import numpy as np
from numpy.typing import NDArray

from quackd.transport.base import TransportError
from quackd_microduck.sim3d import upstream_api as up
from quackd_microduck.sim3d.assets import MicroduckAssets, ensure_microduck
from quackd_microduck.sim3d.gait import (
    ACHIEVED_FRACTION,
    GAIT_FLOOR_VX,
    GAIT_FLOOR_VY,
    GAIT_FLOOR_WZ,
    tilt_deg,
    usable_twist,
)
from quackd_microduck.sim3d.world import NotSupported, Posture

log = logging.getLogger("quackd_microduck.sim3d")

CONTROL_DT = 0.02  # 50 Hz, upstream's control rate
PHYSICS_DT = 0.005  # upstream's timestep; four substeps per control tick
OBS_LEN = 61
ACTION_LEN = 14
STAND_SWITCH = 0.05  # below this twist norm the standing policy runs (upstream's default)

FALL_TILT = -0.5  # projected gravity z above this (upright is -1) is on its side
FALL_HEIGHT = 0.06  # trunk metres above the floor
FALL_DEBOUNCE_TICKS = 10  # 0.2 s, as robotd debounces

HEAD_YAW_LIMIT = math.radians(60)  # quackd's gaze limit, inside upstream's ±1.40 rad
HEAD_PITCH_LIMIT = math.radians(35)

ASSUMPTIONS = (
    "walking and standing are upstream's own ONNX policies at 50 Hz",
    "kick is a scripted impulse in the cartoon's cone, not upstream's kick policy",
    "grab is the cartoon's 60% scoop, not upstream's ground_pick policy",
    "sit is refused: upstream's sit-stand policy put the duck on its back when tried",
    "stand_up stands the model up again; upstream ships no get-up policy",
    f"a non-zero twist is raised to the gait floor (vx {GAIT_FLOOR_VX}, wz {GAIT_FLOOR_WZ}), "
    "and one below a third of it is dropped to zero rather than lurched",
    f"it achieves about {ACHIEVED_FRACTION} of the twist it is sent: read `pose` after every "
    "move and correct from it, rather than trusting the numbers you sent",
    "the sideways floor is not measured, unlike the forward and turning ones, so any lateral "
    "request is sent at full scale",
    "the head camera sees a grey floor and no sky, while every other view shows upstream's "
    "blue scene: its checker and its skybox are the blue the detector calls a person, and it "
    "cannot tell them apart",
    "nobody is in this arena: there is no person to find here, so a `person` detection is the "
    "scenery and not somebody standing there",
)


class PolicyError(TransportError):
    """A policy could not be loaded or does not have the shape upstream documents."""


def _session(path: Any) -> Any:
    import onnxruntime as ort

    options = ort.SessionOptions()
    options.intra_op_num_threads = 1  # one duck, 200k parameters: threads only add latency
    options.log_severity_level = 3
    try:
        return ort.InferenceSession(str(path), options, providers=["CPUExecutionProvider"])
    except Exception as e:
        raise PolicyError(f"could not load {path}: {e}") from e


def _check_io(session: Any, path: Any) -> str:
    """Check a policy is the shape upstream documents, and return the input's real name.

    `OBS_LEN` was a number in a comment: nothing compared it to the model, and the input name
    and output index were written in by hand. A re-export with one more observation would have
    failed somewhere inside onnxruntime on the first tick rather than here, where the file
    that is wrong can be named.
    """
    inputs, outputs = session.get_inputs(), session.get_outputs()
    if len(inputs) != 1 or len(outputs) != 1:
        raise PolicyError(
            f"{path}: {len(inputs)} inputs and {len(outputs)} outputs; upstream's contract "
            f"is one of each ({up.WALK_POLICY.note})"
        )
    if list(inputs[0].shape)[-1:] != [OBS_LEN]:
        raise PolicyError(
            f"{path}: takes {inputs[0].shape}, and quackd builds {OBS_LEN} observations"
        )
    if list(outputs[0].shape)[-1:] != [ACTION_LEN]:
        raise PolicyError(
            f"{path}: returns {outputs[0].shape}, and this body has {ACTION_LEN} actuators"
        )
    return str(inputs[0].name)


class MicroduckBody:
    """Upstream's model and policies behind the `Body` protocol `MujocoWorld` steps."""

    name = "microduck"

    def __init__(self, assets: MicroduckAssets | None = None) -> None:
        try:
            import onnxruntime  # noqa: F401
        except ImportError as e:
            raise PolicyError(
                "the Microduck body needs the physics extra: uv pip install 'quackd[mujoco]'"
            ) from e
        self.assets = assets if assets is not None else ensure_microduck()
        walk_path = self.assets.policies_dir / up.WALK_POLICY.name
        stand_path = self.assets.policies_dir / up.STAND_POLICY.name
        self.walk = _session(walk_path)
        self.stand = _session(stand_path)
        self.obs_name = _check_io(self.walk, walk_path)
        _check_io(self.stand, stand_path)
        meta = self.walk.get_modelmeta().custom_metadata_map
        try:
            self.joint_names = [n.strip() for n in meta["joint_names"].split(",")]
            self.default_pose = np.array(
                [float(v) for v in meta["default_joint_pos"].split(",")], dtype=np.float64
            )
            self.action_scale = float(meta.get("action_scale", "1.0"))
        except (KeyError, ValueError) as e:
            raise PolicyError(
                f"{walk_path}: bad or missing ONNX metadata ({e!r}). Upstream writes "
                "joint_names, default_joint_pos and action_scale when it exports"
            ) from e
        if len(self.joint_names) != ACTION_LEN or self.default_pose.shape != (ACTION_LEN,):
            raise PolicyError(
                f"{up.WALK_POLICY.name} declares {len(self.joint_names)} joints; "
                f"upstream's contract is {ACTION_LEN}"
            )
        self.posture: Posture = "standing"
        self.walking = False
        # Annotated shape-free on purpose: numpy 2.2's stubs, which the lock resolves on
        # 3.12, infer a fixed 1-D shape from `np.zeros` and then refuse the `asarray` the
        # policy's output is stored through.
        self.last_action: NDArray[np.float32] = np.zeros(ACTION_LEN, dtype=np.float32)
        self.head = (0.0, 0.0)
        self.commanded: tuple[float, float, float] = (0.0, 0.0, 0.0)
        self.sent: tuple[float, float, float] = (0.0, 0.0, 0.0)
        self._model: Any = None
        self._data: Any = None
        self._posture_ticks = 0

    # ── the scene ───────────────────────────────────────────────────────────────────

    @property
    def include_xml(self) -> str:
        return f'  <include file="{up.ROBOT_XML.name}"/>'

    @property
    def xml(self) -> str:
        return ""  # its worldbody arrives through the include

    def mujoco_assets(self) -> dict[str, bytes]:
        return self.assets.mujoco_assets()

    # ── the model ───────────────────────────────────────────────────────────────────

    def attach(self, model: Any, data: Any) -> None:
        self._model, self._data = model, data
        name2id = mujoco.mj_name2id
        self.trunk = name2id(model, mujoco.mjtObj.mjOBJ_BODY, up.TRUNK_BODY.name)
        free = name2id(model, mujoco.mjtObj.mjOBJ_JOINT, up.TRUNK_FREEJOINT.name)
        if self.trunk < 0 or free < 0:
            raise PolicyError(
                f"{up.ROBOT_XML.name} has no {up.TRUNK_BODY.name}/{up.TRUNK_FREEJOINT.name}: "
                "the cached model is not the one quackd was written against"
            )
        self.free_q = int(model.jnt_qposadr[free])
        self.free_v = int(model.jnt_dofadr[free])
        joints = [name2id(model, mujoco.mjtObj.mjOBJ_JOINT, n) for n in self.joint_names]
        if any(j < 0 for j in joints):
            missing = [n for n, j in zip(self.joint_names, joints, strict=True) if j < 0]
            raise PolicyError(f"the model has no joint(s) {', '.join(missing)}")
        self.qadr = np.array([model.jnt_qposadr[j] for j in joints])
        self.vadr = np.array([model.jnt_dofadr[j] for j in joints])
        gyro = name2id(model, mujoco.mjtObj.mjOBJ_SENSOR, up.GYRO_SENSOR.name)
        if gyro < 0:
            raise PolicyError(f"the model has no {up.GYRO_SENSOR.name} sensor")
        self.gyro_adr = int(model.sensor_adr[gyro])
        self.camera = name2id(model, mujoco.mjtObj.mjOBJ_CAMERA, up.HEAD_CAMERA.name)
        # the actuators must be the policy's joints, in the policy's order
        actuated = [model.joint(model.actuator_trnid[i][0]).name for i in range(model.nu)]
        if actuated != self.joint_names:
            raise PolicyError(
                "the model's actuator order is not the policy's joint order: "
                f"{actuated} != {self.joint_names}"
            )

    def reset(self, x: float, y: float, theta: float) -> None:
        d, m = self._data, self._model
        d.qpos[self.free_q : self.free_q + 3] = (x, y, float(up.STAND_HEIGHT.name.split()[0]))
        d.qpos[self.free_q + 3 : self.free_q + 7] = (
            math.cos(theta / 2),
            0.0,
            0.0,
            math.sin(theta / 2),
        )
        d.qvel[self.free_v : self.free_v + 6] = 0.0
        d.qpos[self.qadr] = self.default_pose
        d.qvel[self.vadr] = 0.0
        d.ctrl[:] = self.default_pose
        self.last_action[:] = 0.0
        self.posture = "standing"
        self._posture_ticks = 0
        mujoco.mj_forward(m, d)

    # ── one control tick ────────────────────────────────────────────────────────────

    def set_head(self, head: tuple[float, float]) -> None:
        """A real neck: the yaw and pitch become slots in the policy's command, and the
        camera follows because it is bolted to the head the policy is moving."""
        self.head = head

    def control(
        self, cmd: tuple[float, float, float], _dt: float, _rng: np.random.Generator
    ) -> None:
        self.commanded = cmd
        twist = usable_twist(cmd, standing=self.posture == "standing")
        self.sent = twist
        obs = self._observe(twist, self.head)
        policy = self.stand if float(np.linalg.norm(twist)) <= STAND_SWITCH else self.walk
        self.walking = policy is self.walk
        action = policy.run(None, {self.obs_name: obs[None]})[0][0]
        if not np.isfinite(action).all():
            # A NaN here becomes a NaN servo target, and one tick later MuJoCo gives up on the
            # state and silently resets the world. Stop at the policy, where the cause is
            # still legible, rather than at the physics, where it is not.
            raise PolicyError(
                f"{up.WALK_POLICY.name if policy is self.walk else up.STAND_POLICY.name} "
                f"returned a non-finite action at t={self._data.time:.2f}s"
            )
        self.last_action = np.asarray(action, dtype=np.float32)
        self._data.ctrl[:] = self.default_pose + self.last_action * self.action_scale
        self._update_posture()

    def _observe(self, twist: tuple[float, float, float], head: tuple[float, float]) -> Any:
        d = self._data
        rotation = d.xmat[self.trunk].reshape(3, 3)
        command = np.zeros(13, dtype=np.float64)
        command[0:3] = twist
        # [3] neck pitch, [4] head pitch, [5] head yaw, [6] head roll — deltas from home.
        # The sign is `up.HEAD_PITCH_SIGN`: measured here rather than read anywhere, and
        # tagged UNVERIFIED for that reason, so `quackd doctor` and the Microduck's page,
        # docs/adapters/microduck/README.md, say so.
        # [3] and [6] stay zero because quackd's gaze has one pitch and no roll.
        command[4] = -head[1]
        command[5] = head[0]
        return np.concatenate(
            [
                d.sensordata[self.gyro_adr : self.gyro_adr + 3],
                -rotation[2, :],  # the world's -z in the trunk frame: projected gravity
                d.qpos[self.qadr] - self.default_pose,
                d.qvel[self.vadr],
                self.last_action,
                command,
            ]
        ).astype(np.float32)

    def _update_posture(self) -> None:
        if self.posture == "sitting":
            return
        d = self._data
        down = self.gravity_z > FALL_TILT or d.qpos[self.free_q + 2] < FALL_HEIGHT
        # Debounced both ways. Going down took ten ticks and coming back up took one, so a
        # duck hovering at the threshold flapped between postures every other tick, and each
        # flap is a verb refused or allowed on the strength of one noisy frame.
        if down == (self.posture == "fallen"):
            self._posture_ticks = 0
            return
        self._posture_ticks += 1
        if self._posture_ticks >= FALL_DEBOUNCE_TICKS:
            self.posture = "fallen" if down else "standing"
            self._posture_ticks = 0
            if down:
                self.walking = False

    # ── what the world asks ─────────────────────────────────────────────────────────

    @property
    def gravity_z(self) -> float:
        """Projected gravity's z in the trunk frame: -1 upright, 0 on its side."""
        return float(-self._data.xmat[self.trunk].reshape(3, 3)[2, 2])

    def pose(self) -> tuple[float, float, float]:
        d = self._data
        w, qx, qy, qz = d.qpos[self.free_q + 3 : self.free_q + 7]
        yaw = math.atan2(2 * (w * qz + qx * qy), 1 - 2 * (qy * qy + qz * qz))
        return float(d.qpos[self.free_q]), float(d.qpos[self.free_q + 1]), yaw

    def head_pose(self) -> tuple[float, float, float, float, float]:
        """The head camera, where it really is. Upstream's camera quaternion is not
        MuJoCo's viewing convention, so forward is the camera frame's +z, not its -z."""
        d = self._data
        x, y, z = d.cam_xpos[self.camera]
        forward = d.cam_xmat[self.camera].reshape(3, 3)[:, 2]
        yaw = math.atan2(forward[1], forward[0])
        pitch = math.asin(float(np.clip(forward[2], -1.0, 1.0)))
        return float(x), float(y), float(z), yaw, pitch

    def sit_toggle(self) -> Posture:
        raise NotSupported(
            "this duck's sit-stand policy is not wired: it put the model on its back when "
            "tried, and quackd will not ship a sit that falls over"
        )

    def enable(self) -> None:
        """`stand_up`: upstream has no get-up policy, so the model is stood up again."""
        if self.posture != "fallen":
            return
        x, y, _yaw = self.pose()
        self.reset(x, y, self.heading())

    def heading(self) -> float:
        """Which way the duck is facing, in a way that survives being on its face.

        `pose()` reads yaw from the trunk quaternion, which is the right answer while the duck
        is upright and an arbitrary one where it usually is not: face-down and on its back are
        both at the gimbal degeneracy, and that is exactly when `stand_up` asks. Take the
        trunk's own forward axis projected on the floor, and when the duck is nose-down or
        belly-up and that projection vanishes, fall back to where its underside points.
        """
        r = self._data.xmat[self.trunk].reshape(3, 3)
        forward = (float(r[0, 0]), float(r[1, 0]))
        if math.hypot(*forward) < 0.1:
            # nose-down or nose-up: the trunk's z-axis is the only thing still lying flat
            up_axis = (float(r[0, 2]), float(r[1, 2]))
            sign = -1.0 if r[2, 0] > 0 else 1.0
            forward = (sign * up_axis[0], sign * up_axis[1])
        if math.hypot(*forward) < 1e-6:
            return self.pose()[2]  # nothing to read: keep whatever it had
        return math.atan2(forward[1], forward[0])

    def assumptions(self) -> list[str]:
        return list(ASSUMPTIONS)

    def close(self) -> None:
        """Drop the two inference sessions.

        `MujocoWorld.close()` used to close renderers only, so a process that built several
        worlds — `docs/assets/hero3d.py` builds two — kept every onnxruntime session alive
        until the interpreter exited.

        The model and data handles deliberately stay. They are plain memory that goes when the
        world does, and reading the final pose after a run is ordinary: the loop closes the
        transport before its caller looks at where the duck ended up.
        """
        self.walk = self.stand = None

    def extras(self) -> dict[str, Any]:
        return {
            "policy": "walk" if self.walking else "stand",
            "twist_commanded": [round(v, 3) for v in self.commanded],
            "twist_sent": [round(v, 3) for v in self.sent],
            "gait_floor": {"vx": GAIT_FLOOR_VX, "vy": GAIT_FLOOR_VY, "wz": GAIT_FLOOR_WZ},
            "achieved_fraction": ACHIEVED_FRACTION,
            "tilt_deg": round(tilt_deg(self.gravity_z), 1),
            "trunk_z": round(float(self._data.qpos[self.free_q + 2]), 3),
            "assumptions": self.assumptions(),
            "model_pinned": self.assets.pinned,
        }
