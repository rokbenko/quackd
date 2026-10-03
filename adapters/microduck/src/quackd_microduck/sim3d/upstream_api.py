"""The only file in quackd allowed to spell a `microduck_rl` name (ADR-0022).

Every constant is tagged VERIFIED (read from upstream source or an upstream file, link
given) or UNVERIFIED (measured here, or an assumption of ours, with what quackd does about
it). `docs/adapters/microduck/README.md` is the human-readable version; `tests/test_upstream_api.py`
proves UNVERIFIED names are only reachable from the `mujoco` backend.

Two upstreams meet here. The robot's MuJoCo model and its meshes come from
https://github.com/pollen-robotics/microduck_rl at commit
2b25a48b08f1f17bc38c90bb03144c81fbd9ed07 (`develop`, the default branch, 2026-09-06; read
2026-09-07). The policies it trains are published to the Hugging Face Hub as
https://huggingface.co/pollen-robotics/microduck-policies, read at revision
088524a64e2557dc453256b6071dbb9d23888802 (`main`, 2026-09-07).

quackd never imports `microduck_rl`: it requires Python 3.12 exactly, pulls torch, warp
and mjlab, and needs a CUDA GPU to train. What quackd needs from it is three files' worth
of facts (the model, the walking policy's contract, the control loop of its own CPU
rehearsal script `scripts/infer_policy.py`) and the files themselves, fetched at run time
into a user cache and never shipped, because the meshes are CC BY-NC-SA and the code is not.

Nothing here has been run against a physical Microduck.
"""

from __future__ import annotations

from quackd.upstream import UpstreamRef

REPO = "https://github.com/pollen-robotics/microduck_rl"
PIN = "2b25a48b08f1f17bc38c90bb03144c81fbd9ed07"  # develop, 2026-09-06
READ_ON = "2026-09-07"

POLICIES_REPO = "https://huggingface.co/pollen-robotics/microduck-policies"
POLICIES_PIN = "088524a64e2557dc453256b6071dbb9d23888802"  # main, 2026-09-07


def src(path: str, line: int | None = None) -> str:
    return f"{REPO}/blob/{PIN}/{path}" + (f"#L{line}" if line else "")


def raw(path: str) -> str:
    return f"https://raw.githubusercontent.com/pollen-robotics/microduck_rl/{PIN}/{path}"


def policy_url(name: str) -> str:
    return f"{POLICIES_REPO}/resolve/{POLICIES_PIN}/{name}"


TARBALL = f"https://codeload.github.com/pollen-robotics/microduck_rl/tar.gz/{PIN}"

_ROBOT = "src/mjlab_microduck/robot/microduck"
_INFER = "scripts/infer_policy.py"
_README = "README.md"

# ── licences ────────────────────────────────────────────────────────────────────────────

CODE_LICENSE = UpstreamRef(
    "Apache-2.0",
    "VERIFIED",
    src("LICENSE"),
    "the unmodified Apache-2.0 text, 'Copyright 2026 Pollen Robotics'; pyproject declares "
    "license = 'Apache-2.0'",
)
MESH_LICENSE = UpstreamRef(
    "3D model files are licensed under Creative Commons BY-SA-NC",
    "VERIFIED",
    src(_README),
    "one sentence in the README's License section: no CC version, no file headers, no "
    "LICENSE in assets/. Whether the MJCF XML counts as a '3D model file' is not stated, so "
    "quackd treats the XML and the meshes alike: fetched at run time, never shipped",
)
POLICY_LICENSE = UpstreamRef(
    "apache-2.0",
    "VERIFIED",
    f"{POLICIES_REPO}/blob/{POLICIES_PIN}/README.md",
    "the model card's front matter",
)

# ── the model ───────────────────────────────────────────────────────────────────────────

ROBOT_DIR = UpstreamRef(_ROBOT, "VERIFIED", src(_ROBOT), "XMLs, config JSON and assets/")
ROBOT_XML = UpstreamRef(
    "robot_walk.xml",
    "VERIFIED",
    src(f"{_ROBOT}/robot_walk.xml"),
    "the walking model: one free joint, 14 hinge joints, 38 STL meshes under "
    "meshdir='assets', position actuators, IMU sensors, a head camera. No <option>, no "
    "keyframe and no <include>: the scene around it is quackd's",
)
ROBOT_XML_SHA256 = "80fc4424ed71e430ec4919110dfdb5c714dc1c4e1138e1832a16865cf31eb8c8"
COMPILER = UpstreamRef(
    '<compiler angle="radian" meshdir="assets" autolimits="true"/>',
    "VERIFIED",
    src(f"{_ROBOT}/robot_walk.xml"),
)
MESH_BYTES = 21_593_292
"""The meshes on disk, in bytes, as a number rather than as prose inside a ref's name.

The tarball over the wire is about a tenth of this, and saying only the smaller figure while
writing the larger one into someone's cache is the kind of surprise a log line should not
spring."""
MESH_COUNT = UpstreamRef(
    f"38 STL meshes, {MESH_BYTES:,} bytes",
    "VERIFIED",
    src(f"{_ROBOT}/assets"),
    "every <mesh> the walking model references; all 75 mesh geoms are class 'visual' "
    "(contype 0, group 2) except the two soles, so hiding group 2 hides the whole shell",
)
#: sha256 of every mesh `robot_walk.xml` references, keyed by file name under assets/.
MESH_SHA256: dict[str, str] = {
    "right_shell.stl": "3efba3e193cc6aa49d49a9c92cdded0504917397c9e2c5b8fe002a958e2f2d2b",
    "speaker.stl": "75a35f9bcf94cd8e52f99b3fd6ae667b48f52503f9075ee2f699eebe8e7b3ddf",
    "banana_pcb_locker.stl": "42ddde9d01bf5d91f88b99847017f14d97d2537ee27383c6d41dd8bf30a51134",
    "top_head_shell.stl": "6d01f396f514c9d2185cf6b8250b1d8382ac3fa0702eb525bcb284f706196291",
    "bearing_roll.stl": "b5603a64b2d965ad75ea847767b5798130852e12a658fde7b5068fc457e2adcc",
    "ankle_left.stl": "2ec1bd89205b407efebaa9a8898ad9fea741389d87b80ecc5c96b21d1668119e",
    "upper_leg_rigidity_plate.stl": (
        "231b3e5f1e24d146d735dc158cbcfec0c95a0d0ca8c500f61f04d15d0b665652"
    ),
    "neck_pitch.stl": "a685ce65348ae4d0d49417c69a7a8871f8bbd54a6e3419362be9c334abd5d2ae",
    "noenoeil.stl": "e1167b3d5ad7fbbe6e63296bcd0c6b61556611e6bc1367b15ecfd27e3abb72f5",
    "face_part.stl": "d0e89a173e7bdc8038c4d413f458140d8f42f1a2f098f67a887c2388eff31718",
    "pcb__raspberry_pi_zero_2_w.stl": (
        "802cc66fc5447c67fc642d1339e55160df10f2ad04a65efd130e35fb7b2d3c90"
    ),
    "foot_right.stl": "54f97fa1f0e0e611d31329df9f3c3b17ffcca2d695c579d1d32cbe99e8a6c7ef",
    "seeed_bearing__configuration__22x16x4.stl": (
        "295ee68ed9766c91fe06d3a2a49d39f7c2f56cc943395d293afdeb013123435e"
    ),
    "elec_rpi_robot_hat_pcb.stl": (
        "1a61582759d82b5b4df9b7d11099bf1bb3944fc77e6b74d3237adf24dd5d03d7"
    ),
    "xl330.stl": "96238ed0d020ce0009cf1a5aa4b6926176c800840318ee65e4375be2298d2744",
    "jaw_soft.stl": "2d2ade4e86df4b37ce313bb9b96023109dd04bab39dba08b154e76d332fbce2e",
    "hip_l.stl": "e175896932d76dc7648cf939aa39cb7b1d188b783d91745adb9a4cec65e9052c",
    "bottom_head_shell.stl": "1820f7cf96646b0fc0c431f07da87b91b13111bfa9857ab1bf2b1fc3388f8da2",
    "yaw2roll.stl": "6c8f193ec7cb40be0f48eeb5fba81bce2c87f12e1aab3f77f1bc4981386e1495",
    "seeed_bearing__configuration_default.stl": (
        "e815f3483a2e7b80844f3c0938a47e53db38b12faed9b2d364414c19e4322da4"
    ),
    "ankle_right.stl": "f83f388ae00787415e76b8ecaab769f08f25b8c4e90c1937ff0fc4d68d81722e",
    "motor_support.stl": "ce0aa5bef82c7474512d302568c341b9769b3005e0bdb91a19c2eaaba6dad480",
    "foot_left.stl": "a1f5d43456d0f522bd60870153795f1e8112c1adf9b8b4373b59608d9545c044",
    "upper_leg_right.stl": "5caf996cc3e151b01f393cdc6a6a6a940c1d86179d8b7c7f5f85cf1f1ac2fb63",
    "power_support.stl": "ee45e397d3f3422f34940a4b61156c3c11c7e8c2b240451ce5f9ddd1622a130e",
    "np_f970.stl": "0e55a48281331b3b7b1a363d2e93b6012f37c52045b6dc922a149ca7c1b38388",
    "neck.stl": "b6e86b886153be8a8a776c1cb219c074ee9e89c8bd431117662a2d9e504d7cb5",
    "yaw_roll_motion.stl": "b0c9902dee19f06cdb7fa930292e0e1c91e2dc59baa971211495b58ff76c3c60",
    "lens.stl": "b1c717432ff7666398de58adec6a1b7e03a7e45aa155dbfe27a6152ef19663e5",
    "sole_left.stl": "e028c8b09fecd4cb354d042bf7fd95a1a1bc2d524d760d62137fa8d74093fc23",
    "upper_leg_left.stl": "8cb1c12dfa6d366a318bb1ec16f585427b9a185afeb7738d0a6f41f5db46a07d",
    "trunk_base.stl": "aa4f853ae555a8b765e585ce4519c0bbff325ce298ec15a1437792c52a0beac1",
    "left_shell.stl": "2abde8e2b79c9b011cdb11957c67ab4287b45d7301affd4235ab81141118e331",
    "sole_right.stl": "3f5068872f3add10eb967327651445bdfca635f5a8a01befa0ef05e462b72b4c",
    "jaw.stl": "45cdbd319a4583095d8daa0db07dee726522a743d59acb82d27d82ded500a1cf",
    "leg.stl": "9a91cc1ae31ecd9e0c1ba93f3bb2ad8a12e29bff24ec27d1ac66c0c3037d9fcf",
    "soft_mouth_top.stl": "3c2b52b0b6e2ce944a1e1b5a0838e1216be1ad3a34c8cdf91be186e634aec57b",
    "m12_lens_holder.stl": "031d5ffaf6247328cf6d020f6b6695ffb3f8d3f22cf923b85811351d89ecfd7c",
}

TRUNK_BODY = UpstreamRef("trunk_base", "VERIFIED", src(f"{_ROBOT}/robot_walk.xml"))
TRUNK_FREEJOINT = UpstreamRef("trunk_base_freejoint", "VERIFIED", src(f"{_ROBOT}/robot_walk.xml"))
GYRO_SENSOR = UpstreamRef(
    "imu_ang_vel",
    "VERIFIED",
    src(f"{_ROBOT}/robot_walk.xml"),
    "a gyro on site 'imu'; the first three observation floats",
)
HEAD_CAMERA = UpstreamRef(
    "head_camera",
    "VERIFIED",
    src(f"{_ROBOT}/robot_walk.xml"),
    "the one <camera>, on the head body, fovy 45. Its quaternion is not MuJoCo's viewing "
    "convention (rendered as-is it looks into the shell), so quackd renders from its "
    "position along the head body's forward axis instead",
)
ACTUATORS = UpstreamRef(
    "<position> kp=0.55 kv=0 forcerange=±0.96 ctrlrange=±10, joints damping 0.053 "
    "frictionloss 0.0048 armature 0.0018",
    "VERIFIED",
    src(f"{_ROBOT}/robot_walk.xml"),
    "the XML's own actuators. Upstream trains and rehearses with the BAM XL330 model "
    "(better-actuator-models, Python 3.12 only); `infer_policy.py --no-bam` uses these, "
    "as does Pollen's own browser simulator",
)
VISUAL_GROUP = UpstreamRef("2", "VERIFIED", src(f"{_ROBOT}/robot_walk.xml"), "class 'visual'")

# ── the policies ────────────────────────────────────────────────────────────────────────

WALK_POLICY = UpstreamRef(
    "alpha_walking.onnx",
    "VERIFIED",
    policy_url("alpha_walking.onnx"),
    "793,705 bytes; opset 18, pytorch 2.9.1; input obs[1,61] float32, output actions[1,14]; "
    "the observation normaliser is baked in (Sub, Div), then an MLP 61-512-256-128-14 with ELU",
)
WALK_POLICY_SHA256 = "e36332d383997d51401897734cd3e79cf5038406feddb18b4d57ecfb141daa6c"
STAND_POLICY = UpstreamRef(
    "alpha_stand.onnx",
    "VERIFIED",
    policy_url("alpha_stand.onnx"),
    "same shape; what the daemon and the browser run while the twist is below 0.05",
)
STAND_POLICY_SHA256 = "1569268713e40deea795dd2922dba50d3621e15a872855408b6b1b125b1c094b"
POLICY_MANIFEST = UpstreamRef(
    "manifest.json",
    "VERIFIED",
    policy_url("manifest.json"),
    "schema 2: obs_len 61, action_len 14, control_hz 50, one entry per policy with its "
    "kind (perpetual, scripted, episodic) and command encoding",
)
POLICY_MANIFEST_SHA256 = "d0c36e7b71129dd617339c63bcb1d704eab282c8617ebf14c2013a01abfb2dda"
POLICY_METADATA = UpstreamRef(
    "joint_names, default_joint_pos, action_scale, observation_names, command_names",
    "VERIFIED",
    src("src/mjlab_microduck/export.py"),
    "ONNX metadata_props written at export. quackd reads joint order, the home pose and "
    "the action scale from the file rather than hard-coding them",
)
JOINT_ORDER = UpstreamRef(
    "left_hip_yaw, left_hip_roll, left_hip_pitch, left_knee, left_ankle, neck_pitch, "
    "head_pitch, head_yaw, head_roll, right_hip_yaw, right_hip_roll, right_hip_pitch, "
    "right_knee, right_ankle",
    "VERIFIED",
    src(_INFER),
    "DEFAULT_POSE order in infer_policy.py, the actuator order in the XML and the ONNX "
    "metadata agree; the mouth servo is not a policy joint",
)
DEFAULT_POSE = UpstreamRef(
    "0, -0.0873, -0.4579, -0.0049, 0.4530, 0.3491, 0.3491, 0, 0, 0, 0.0873, 0.4579, "
    "0.0049, -0.4530 rad",
    "VERIFIED",
    src(_INFER),
    "the STAND pose; the ONNX metadata carries the same numbers to three decimals",
)
STAND_HEIGHT = UpstreamRef(
    "0.125 m",
    "VERIFIED",
    src(_INFER),
    "trunk z at reset, upright; the STAND keyframe in scene.xml uses 0.12",
)

# ── the control loop ────────────────────────────────────────────────────────────────────

CONTROL = UpstreamRef(
    "timestep 0.005 s, decimation 4, 50 Hz",
    "VERIFIED",
    src(_INFER),
    "every upstream CPU runner: one policy call, then four physics steps",
)
OBSERVATION = UpstreamRef(
    "[gyro(3), projected gravity(3), joint_pos - default(14), joint_vel(14), last action(14), "
    "command(13)] = 61",
    "VERIFIED",
    src(_INFER),
    "get_observations(); the same order in the daemon's obs.rs and in Pollen's browser "
    "simulator. Projected gravity is the world's -z axis in the trunk frame, which is minus "
    "the third row of the trunk's rotation matrix: get the sign wrong and the duck braces "
    "and stands still on every command, silently",
)
COMMAND = UpstreamRef(
    "[vx, vy, wz, neck_pitch, head_pitch, head_yaw, head_roll, body x, y, z, roll, pitch, yaw]",
    "VERIFIED",
    src(_INFER),
    "the 13-value command: a twist, four head joint deltas from home, and a body pose the "
    "walking task keeps at zero. quackd writes the twist and the head yaw and pitch",
)
ACTION = UpstreamRef(
    "ctrl = default_pose + action * action_scale",
    "VERIFIED",
    src(_INFER),
    "apply_action(); action_scale is 1.0 in the walking policy's metadata",
)
STAND_SWITCH = UpstreamRef(
    "0.05",
    "VERIFIED",
    src(_INFER),
    "--switch-threshold: below this twist norm the standing policy runs, above it the "
    "walking one. quackd does the same",
)
TRAINING_TWIST = UpstreamRef(
    "vx ±0.4, vy ±0.3, wz ±1.0 (rehearsal keyboard: ±0.3, ±0.3, ±1.5)",
    "VERIFIED",
    src("src/mjlab_microduck/tasks/microduck_velocity_env_cfg.py"),
    "the velocity command ranges the walking task trains with, and the rehearsal script's "
    "full-scale keys",
)
HEAD_RANGE = UpstreamRef(
    "neck_pitch ±1.10, head_pitch ±1.10, head_yaw ±1.40, head_roll ±0.31 rad",
    "VERIFIED",
    src("src/mjlab_microduck/tasks/microduck_velocity_env_cfg.py"),
    "the head command curriculum's final ranges; quackd's ±60° gaze sits inside them",
)

# ── what quackd measured, and assumes ───────────────────────────────────────────────────

GAIT_THRESHOLD = UpstreamRef(
    "no gait below vx 0.23 m/s or wz 1.0 rad/s; above it about 0.38x the commanded speed",
    "UNVERIFIED",
    src(_INFER),
    "re-measured 2026-09-17 with the XML's PD actuators on MuJoCo 3.13: vx 0.225 walks 1.3 cm "
    "in 10 s and 0.23 walks 0.81 to 0.89 m on all ten sweep seeds; wz 1.0 turns 2.2 rad in "
    "5 s. On MuJoCo 3.12 on 2026-09-07 the same measurement put the floor at 0.22 and the "
    "turn at 3.83 rad in 5 s, so this threshold is a property of the physics build and not "
    "only of the policy, and it is why the nightly asset job re-runs the sweep. Upstream "
    "trains and deploys with the BAM actuator model, so the real robot may track commands "
    "directly. quackd scales a non-zero twist up so the gait starts, and says so in the state",
)
HEAD_PITCH_SIGN = UpstreamRef(
    "a positive head_pitch in the command vector tilts the camera down",
    "UNVERIFIED",
    src(_INFER),
    "measured 2026-09-07 by driving the command and watching the rendered head camera, not "
    "read anywhere: quackd negates its own pitch so that looking up is a positive number. "
    "neck_pitch (command[3]) and head_roll (command[6]) are left at zero, because quackd's "
    "gaze has one pitch and no roll, so nothing here has ever exercised them",
)
KICK_STANDIN = UpstreamRef(
    "ball_kick_left.onnx, ball_kick_right.onnx, alpha_ground_pick.onnx, alpha_sitstand.onnx",
    "UNVERIFIED",
    policy_url("manifest.json"),
    "episodic policies quackd has not wired: kick and scoop use the cartoon's contact "
    "rules (a velocity on the ball inside 0.30 m and ±35°, a 60 % scoop inside 0.18 m), sit "
    "is refused, and a fall is recovered by resetting the pose. Each is listed in the "
    "state's `assumptions` so the pilot and the transcript know",
)


def all_refs() -> list[UpstreamRef]:
    return [v for v in globals().values() if isinstance(v, UpstreamRef)]


def refs_by_status(status: str) -> list[UpstreamRef]:
    return [r for r in all_refs() if r.status == status]
