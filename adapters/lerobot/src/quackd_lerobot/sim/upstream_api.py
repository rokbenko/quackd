"""The only file in quackd allowed to spell an SO-ARM100 name (ADR-0022).

Every constant is tagged VERIFIED (read from an upstream file at the pin, link given) or
UNVERIFIED (an assumption of ours, with what quackd does about it).
`docs/adapters/lerobot/README.md` is the human-readable version; `tests/test_upstream_api.py` proves
UNVERIFIED names are only reachable from the simulator's own files.

The upstream is https://github.com/TheRobotStudio/SO-ARM100, the SO-101's maker's own
repository, at commit 5f6d2b876a53a4872e405b991dd925556c9e38a4 (`main`, 2026-09-23; read
2026-09-26). What quackd takes from it is one MuJoCo model of the follower arm and the meshes
that model names. It is the maker's model, and its joints carry the names LeRobot gives the
motors, so a reading reaches its joint by name with nothing of quackd's in between.

quackd never ships these files. GitHub puts the repository at about 200 MB, far too much to
fetch for one directory, so `assets.py` fetches each file on its own from raw.githubusercontent
at the pin, into a user cache, and checks every one against the sha256 in `FILES` before
anything loads it.

Nothing here has been run against an arm. The model's dynamics are a calculation and
properties carried over from another robot, not a measurement of an SO-101 (SERVO_DYNAMICS).
"""

from __future__ import annotations

from quackd.upstream import UpstreamRef

REPO = "https://github.com/TheRobotStudio/SO-ARM100"
PIN = "5f6d2b876a53a4872e405b991dd925556c9e38a4"  # main, 2026-09-23
READ_ON = "2026-09-26"


def src(path: str, line: int | None = None) -> str:
    return f"{REPO}/blob/{PIN}/{path}" + (f"#L{line}" if line else "")


def raw(path: str) -> str:
    return f"https://raw.githubusercontent.com/TheRobotStudio/SO-ARM100/{PIN}/{path}"


SIM_DIR = "Simulation/SO101"
"""The directory of the repository every path in `FILES` is relative to."""

MODEL_FILE = "so101_new_calib_camera.xml"
"""The model quackd loads: new_calib, the default calibration, with the wrist camera mount."""

#: sha256 of every file quackd fetches, keyed by its path under `SIM_DIR`: the model, and each
#: mesh it names. `assets.py` installs nothing unless every one of them matches.
FILES: dict[str, str] = {
    MODEL_FILE: "67fa9fb658242a3d3fe0984a0401a6782c215e658880087a42914d7b9addd65c",
    "assets/base_motor_holder_so101_v1.stl": (
        "8cd2f241037ea377af1191fffe0dd9d9006beea6dcc48543660ed41647072424"
    ),
    "assets/base_so101_v2.stl": "bb12b7026575e1f70ccc7240051f9d943553bf34e5128537de6cd86fae33924d",
    "assets/motor_holder_so101_base_v1.stl": (
        "31242ae6fb59d8b15c66617b88ad8e9bded62d57c35d11c0c43a70d2f4caa95b"
    ),
    "assets/motor_holder_so101_wrist_v1.stl": (
        "887f92e6013cb64ea3a1ab8675e92da1e0beacfd5e001f972523540545e08011"
    ),
    "assets/moving_jaw_so101_v1.stl": (
        "785a9dded2f474bc1d869e0d3dae398a3dcd9c0c345640040472210d2861fa9d"
    ),
    "assets/rotation_pitch_so101_v1.stl": (
        "9be900cc2a2bf718102841ef82ef8d2873842427648092c8ed2ca1e2ef4ffa34"
    ),
    "assets/sts3215_03a_no_horn_v1.stl": (
        "75ef3781b752e4065891aea855e34dc161a38a549549cd0970cedd07eae6f887"
    ),
    "assets/sts3215_03a_v1.stl": "a37c871fb502483ab96c256baf457d36f2e97afc9205313d9c5ab275ef941cd0",
    "assets/under_arm_so101_v1.stl": (
        "d01d1f2de365651dcad9d6669e94ff87ff7652b5bb2d10752a66a456a86dbc71"
    ),
    "assets/upper_arm_so101_v1.stl": (
        "475056e03a17e71919b82fd88ab9a0b898ab50164f2a7943652a6b2941bb2d4f"
    ),
    "assets/waveshare_mounting_plate_so101_v2.stl": (
        "e197e24005a07d01bbc06a8c42311664eaeda415bf859f68fa247884d0f1a6e9"
    ),
    "assets/wrist_camera_mount_so101_v1.stl": (
        "e17a626158951ac8cdf8d960962c1a3c8bf23b64635a02f88b62016fe895cef8"
    ),
    "assets/wrist_camera_so101_v1.stl": (
        "1147857bb136876c81f98d18b533342bae2383f0e23bec216064c9f093bd3577"
    ),
    "assets/wrist_roll_follower_so101_v1.stl": (
        "4b17b410a12d64ec39554abc3e8054d8a97384b2dc4a8d95a5ecb2a93670f5f4"
    ),
    "assets/wrist_roll_pitch_so101_v2.stl": (
        "6c7ec5525b4d8b9e397a30ab4bb0037156a5d5f38a4adf2c7d943d6c56eda5ae"
    ),
}

FILES_BYTES = 16_447_766
"""Everything in `FILES` on disk, in bytes, for the log line that announces the fetch.

A number rather than prose in a ref's name, like the Microduck's mesh total, so the line can
say how much is about to land in someone's cache before it lands."""

_MODEL = f"{SIM_DIR}/{MODEL_FILE}"
_README = f"{SIM_DIR}/README.md"

# ── licence ─────────────────────────────────────────────────────────────────────────────

CODE_LICENSE = UpstreamRef(
    "the repository's LICENSE is the Apache License 2.0",
    "VERIFIED",
    src("LICENSE"),
    "the unmodified text, with the appendix's copyright line left as the template's "
    "placeholder. No other licence file sits in Simulation/SO101 or its assets, and neither "
    "README names one, so the model and its meshes are under it too. quackd fetches them at "
    "run time rather than shipping them all the same, as it does every upstream asset",
)

# ── the model ───────────────────────────────────────────────────────────────────────────

MODEL = UpstreamRef(
    MODEL_FILE,
    "VERIFIED",
    src(_MODEL, 4),
    "derived from so101_new_calib.xml, in the file's own words, by adding a wrist camera "
    "mount and a wrist camera. Nine bodies from base to moving jaw, six hinge joints, six "
    "<position> actuators, and no <include>: it loads on its own",
)
MODEL_HAS_NO_SCENE = UpstreamRef(
    "no <option>, <camera>, <light> or table in the model",
    "VERIFIED",
    src(_MODEL),
    "the file is the arm and nothing around it: no timestep, no camera element, no light, "
    "no floor, no table, no keyframe and no sensor. So the physics settings, the scene and "
    "every camera the simulator renders from are quackd's own overlay and not upstream's. "
    "Upstream's scene.xml adds a floor and a light around so101_new_calib.xml, not this "
    "variant, and quackd does not use it",
)
COMPILER = UpstreamRef(
    '<compiler angle="radian" meshdir="assets" autolimits="true"/>',
    "VERIFIED",
    src(_MODEL, 6),
    "every joint range and ctrlrange in the file is in radians, and the meshes resolve "
    "under assets/, which is where assets.py puts them. quackd reads each range from the "
    "loaded model rather than copying a number out of the file",
)
MESHES = UpstreamRef(
    f"{len(FILES) - 1} STL meshes under {SIM_DIR}/assets",
    "VERIFIED",
    src(f"{SIM_DIR}/assets"),
    "every <mesh> the model names, and nothing else from a directory that also holds the "
    "leader arm's meshes and the CAD's .part files",
)
WRIST_CAMERA_MOUNT = UpstreamRef(
    "wrist_camera_mount and wrist_camera",
    "VERIFIED",
    src(_MODEL, 114),
    "two bodies hung under the gripper body, a printed mount and the shell of what the file "
    "calls a 32x32 UVC module, each with a visual and a collision mesh and neither with a "
    "<camera> element. Where a rendered wrist view comes from, and which way it looks, is "
    "quackd's overlay",
)
WRIST_CAMERA_BODY = "wrist_camera"
"""The camera shell's body, which quackd's wrist view is mounted on (WRIST_CAMERA_MOUNT)."""
FIXED_FINGER_MESH = "wrist_roll_follower_so101_v1"
MOVING_JAW_MESH = "moving_jaw_so101_v1"
"""The two meshes FINGER_MESHES says the fingers are part of."""
FINGER_MESHES = UpstreamRef(
    f"the fingers are part of the meshes {FIXED_FINGER_MESH} and {MOVING_JAW_MESH}",
    "VERIFIED",
    src(_MODEL, 110),
    "the gripper body has no finger of its own. The fixed finger is one printed part with the "
    "housing the wrist_roll servo turns, the mesh at this line, and the moving finger is the "
    "far end of the moving jaw's mesh (line 134), on the body the gripper joint turns. Each "
    "part has a collision copy of the whole of it, class collision, and MuJoCo collides a mesh "
    "as its convex hull, so the fixed part's hull reaches from the housing to the fingertip and "
    "fills the opening the moving finger closes into. Read from the model and those two meshes "
    "at the pin: quackd turns both copies off and collides the fingers and the palm as boxes "
    "cut from the meshes' own vertices when the model loads, finding the two by these names",
)
JOINT_NAMES = UpstreamRef(
    "joints and actuators named shoulder_pan, shoulder_lift, elbow_flex, wrist_flex, "
    "wrist_roll, gripper",
    "VERIFIED",
    src(_MODEL, 175),
    "the six hinge joints and the six <position> actuators carry exactly the names LeRobot "
    "gives the SO-101's motors (SO_MOTORS), in the same order, so a '<motor>.pos' key reaches "
    "its joint and its actuator by name, with no table of quackd's in between",
)
NEW_CALIB_ZERO = UpstreamRef(
    "new_calib: each joint's zero is the middle of its range",
    "VERIFIED",
    src(_README, 15),
    "the README, for the default of its two calibrations and the one this model derives "
    "from. The file bears it out for the five arm joints: four ranges are symmetric about "
    "zero, and wrist_roll's middle is a few degrees from it. It does not for the gripper, "
    "whose hinge has its zero near one end of its range, so for that joint the README's "
    "convention is not this file's, and GRIPPER_MAP covers it rather than JOINT_ZERO. "
    "LeRobot's degrees for the five arm joints are centred on the middle of the travel a "
    "calibration recorded (DEGREES_FORMULA), which is why the two zeros are expected to meet, "
    "and JOINT_ZERO is the assumption that they do. LeRobot's gripper is 0..100 instead "
    "(SO_GRIPPER_RANGE)",
)
GAINS = UpstreamRef(
    '<position kp="998.22" kv="2.731" forcerange="-2.94 2.94"/>',
    "VERIFIED",
    src(_MODEL, 28),
    "the sts3215 default class, which every joint and actuator uses, with joint damping 0.60, "
    "frictionloss 0.052 and armature 0.028; each of the six actuators overrides the "
    "forcerange with -3.35 3.35. The file's comment says the gains 'were calculated according "
    "to https://github.com/Gregory119/RBE501-RL-arm-project/blob/main/gymnasium_env/README.md, "
    "assuming that the servo proportional gain is set to 16', and that they are 'not a 1-to-1 "
    "mapping of the servo gains used in Lerobot'. 16 is the default position_p_coefficient of "
    "LeRobot's SO101FollowerConfig (SO_CONFIG). quackd uses them as written and tunes nothing",
)
MOTOR_PROPERTIES = UpstreamRef(
    "STS3215 motor properties adapted from the Open Duck Mini project",
    "VERIFIED",
    src(_README, 32),
    "the README's own account of where the servo properties in the model came from: another "
    "robot, not a measurement of an SO-101",
)
GRIPPER_NOT_MAPPED = UpstreamRef(
    "LeRobot's gripper 0..100 is not yet reflected in the URDF and MuJoCo files",
    "VERIFIED",
    src(_README, 41),
    "the README's gripper note: LeRobot treats the gripper as a linear joint, 0 fully closed "
    "and 100 fully open, while the model's gripper is a hinge in radians. The map between the "
    "two is quackd's (GRIPPER_MAP)",
)

# ── UNVERIFIED: our assumptions, and what quackd does about each ────────────────────────

SERVO_DYNAMICS = UpstreamRef(
    "SERVO_DYNAMICS",
    "UNVERIFIED",
    src(_MODEL, 21),
    "whether the model's gains, damping, friction and force limits behave like an SO-101's "
    "servos. They are a calculation (GAINS) and properties adapted from another robot "
    "(MOTOR_PROPERTIES), and nobody has measured an SO-101 against them. quackd treats the "
    "simulated dynamics as the model's and never as the arm's, and says so: how fast a joint "
    "settles, how hard it pushes and whether a grasp holds in the simulator are evidence "
    "about the model, and only the bench can say them about an arm",
)
JOINT_ZERO = UpstreamRef(
    "JOINT_ZERO",
    "UNVERIFIED",
    src(_README, 15),
    "whether a real arm's calibrated middle of travel, which is LeRobot's zero degrees, is the "
    "model's zero, on the five arm joints. Both are meant to be the middle (NEW_CALIB_ZERO), "
    "but a calibration records the travel one person swept on one arm, and nothing says that "
    "matches the CAD. quackd assumes an offset of zero on each of the five until the bench "
    "measures one, as LeRobot's own kinematics helper does when it sets a URDF joint to the "
    "reading in radians (KINEMATICS_DEG2RAD). The gripper is not a degree reading at all, "
    "and GRIPPER_MAP places it",
)
JOINT_SIGN = UpstreamRef(
    "JOINT_SIGN",
    "UNVERIFIED",
    src(_MODEL, 52),
    "whether a positive LeRobot degree turns the model's joint the positive way about its "
    "axis. Which way a servo counts is how it was mounted and what its calibration's "
    "drive_mode says, and the model knows neither. quackd assumes it does on the five arm "
    "joints, as LeRobot's own kinematics helper does when it hands a reading to the URDF "
    "unnegated (KINEMATICS_DEG2RAD). Which end of the gripper's hinge is closed is "
    "GRIPPER_MAP's, found from the model",
)
GRIPPER_MAP = UpstreamRef(
    "GRIPPER_MAP",
    "UNVERIFIED",
    src(_README, 41),
    "how LeRobot's gripper 0..100 lands on the model's gripper hinge, which upstream has not "
    "mapped (GRIPPER_NOT_MAPPED). quackd maps it linearly over the hinge's range as the loaded "
    "model states it, with the closed end found from the model rather than assumed, and clips "
    "a reading to 0..100",
)
WRIST_CAMERA_POSE = UpstreamRef(
    "WRIST_CAMERA_POSE",
    "UNVERIFIED",
    src(_MODEL, 114),
    "whether upstream's wrist camera mount sits where a given arm's real wrist camera sits. "
    "The mount is one printed design, and an owner's camera may be another module on another "
    "bracket, turned another way. quackd renders the wrist view from the mount as the model "
    "places it and never claims it is the view the arm's own camera has",
)


def all_refs() -> list[UpstreamRef]:
    return [v for v in globals().values() if isinstance(v, UpstreamRef)]


def refs_by_status(status: str) -> list[UpstreamRef]:
    return [r for r in all_refs() if r.status == status]
