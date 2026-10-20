"""UNVERIFIED upstream assumptions must not leak past the experimental backends.

One row per upstream (ADR-0006, extended by ADR-0022): the module that spells its names,
the only files allowed to touch its UNVERIFIED refs, and the source prefixes every ref must
link to.
"""

from __future__ import annotations

import re
from pathlib import Path
from types import ModuleType

import pytest

from quackd_alohamini import upstream_api as alohamini_api
from quackd_huebot import upstream_api as huebot_api
from quackd_lerobot import upstream_api as lerobot_api
from quackd_lerobot.policy import upstream_api as lerobot_policy_api
from quackd_lerobot.sim import upstream_api as so_arm100_api
from quackd_microduck import upstream_api
from quackd_microduck.sim3d import upstream_api as microduck_rl_api
from quackd_open_duck import upstream_api as open_duck_api
from quackd_rosbridge import upstream_api as rosbridge_api
from quackd_toddlerbot import upstream_api as toddlerbot_api
from quackd_xlerobot import upstream_api as xlerobot_api
from tests.adapter_layout import canonical_rel, source_roots

PKG = Path(__file__).resolve().parents[1] / "quackd"

UPSTREAMS: list[tuple[ModuleType, set[str], tuple[str, ...]]] = [
    (
        upstream_api,
        {
            "adapters/microduck/upstream_api.py",
            "adapters/microduck/transports/jsonrpc_unix.py",
        },
        ("https://github.com/pollen-robotics/microduck",),
    ),
    (
        lerobot_api,
        {
            "adapters/lerobot/upstream_api.py",
            "adapters/lerobot/real.py",
            # the arm simulator's model, world and follower, which reproduce what these
            # assumptions say of a real arm: the gripper's open end, and a goal kept through
            # torque off
            "adapters/lerobot/sim/model.py",
            "adapters/lerobot/sim/world.py",
            "adapters/lerobot/sim/follower.py",
        },
        ("https://github.com/huggingface/lerobot",),
    ),
    (
        lerobot_policy_api,
        {
            "adapters/lerobot/policy/upstream_api.py",
            # the pipeline that loads a checkpoint in the policy server, which says what it does
            # about SmolVLA and pi05 (VLA_PIPELINE) and about tick mode (TICK_MODE), the server
            # beside it, and the arm's backend, whose untested load_policy() LOAD_POLICY is
            "adapters/lerobot/policy/pipeline.py",
            "adapters/lerobot/policy/server.py",
            "adapters/lerobot/real.py",
        },
        ("https://github.com/huggingface/lerobot",),
    ),
    (
        rosbridge_api,
        {
            "adapters/rosbridge/upstream_api.py",
            "adapters/rosbridge/ws.py",
        },
        (
            "https://github.com/gramaziokohler/roslibpy",
            "https://github.com/RobotWebTools/rosbridge_suite",
            "https://github.com/ros2/common_interfaces",
            # introspection: what a description says, and where it usually is
            "https://github.com/ros/urdfdom",
            "https://github.com/ros/robot_state_publisher",
        ),
    ),
    (
        open_duck_api,
        {
            "adapters/open_duck/upstream_api.py",
            "adapters/open_duck/bridge.py",
        },
        (
            "https://github.com/apirrone/Open_Duck_Mini_Runtime",
            "https://github.com/apirrone/Open_Duck_Mini",
        ),
    ),
    (
        xlerobot_api,
        {
            "adapters/xlerobot/upstream_api.py",
            "adapters/xlerobot/zmq_host.py",
        },
        ("https://github.com/Vector-Wangel/XLeRobot",),
    ),
    (
        alohamini_api,
        {
            "adapters/alohamini/upstream_api.py",
            "adapters/alohamini/zmq_host.py",
        },
        (
            "https://github.com/liyiteng/lerobot_alohamini",
            "https://github.com/liyiteng/AlohaMini",
        ),
    ),
    (
        toddlerbot_api,
        {
            "adapters/toddlerbot/upstream_api.py",
            "adapters/toddlerbot/bridge.py",
        },
        ("https://github.com/hshi74/toddlerbot",),
    ),
    (
        huebot_api,
        {
            "adapters/huebot/upstream_api.py",
            "adapters/huebot/transports/jsonrpc_unix.py",
        },
        # robotd's protocol, read in the Microduck's source, and the maker's page for every claim
        # about Huebot's side
        ("https://github.com/pollen-robotics/microduck", "https://ce-net.com/huebot"),
    ),
    (
        microduck_rl_api,
        {
            "adapters/microduck/sim3d/upstream_api.py",
            "adapters/microduck/sim3d/assets.py",
            "adapters/microduck/sim3d/microduck.py",
            # the measured gait envelope `GAIT_THRESHOLD` documents; it moved out of
            # `microduck.py` so it could be tested without the extra, and cites its source
            "adapters/microduck/sim3d/gait.py",
        },
        (
            "https://github.com/pollen-robotics/microduck_rl",
            "https://huggingface.co/pollen-robotics/microduck-policies",
        ),
    ),
    (
        so_arm100_api,
        {
            "adapters/lerobot/sim/upstream_api.py",
            "adapters/lerobot/sim/assets.py",
            # the arm simulator's model and world, which map LeRobot's degrees and gripper
            # onto the model through the assumptions this module names, and its transport,
            # which lists them among the state's assumptions
            "adapters/lerobot/sim/model.py",
            "adapters/lerobot/sim/world.py",
            "adapters/lerobot/sim/transport.py",
        },
        (
            "https://github.com/TheRobotStudio/SO-ARM100",
            "https://raw.githubusercontent.com/TheRobotStudio/SO-ARM100",
        ),
    ),
]
IDS = [
    "microduck",
    "lerobot",
    "lerobot_policy",
    "rosbridge",
    "open_duck",
    "xlerobot",
    "alohamini",
    "toddlerbot",
    "huebot",
    "microduck_rl",
    "so_arm100",
]


def _unverified_identifiers(module: ModuleType) -> list[str]:
    return [
        name
        for name, value in vars(module).items()
        if isinstance(value, upstream_api.UpstreamRef) and value.status == "UNVERIFIED"
    ]


@pytest.mark.parametrize(("module", "allowed", "prefixes"), UPSTREAMS, ids=IDS)
def test_every_ref_has_a_source_link(
    module: ModuleType, allowed: set[str], prefixes: tuple[str, ...]
) -> None:
    for ref in module.all_refs():
        assert ref.source.startswith(prefixes), ref
        assert ref.status in ("VERIFIED", "UNVERIFIED")


@pytest.mark.parametrize(("module", "allowed", "prefixes"), UPSTREAMS, ids=IDS)
def test_unverified_refs_only_used_in_experimental_backends(
    module: ModuleType, allowed: set[str], prefixes: tuple[str, ...]
) -> None:
    idents = _unverified_identifiers(module)
    assert idents, "expected at least one UNVERIFIED ref"
    pattern = re.compile(r"\b(" + "|".join(map(re.escape, idents)) + r")\b")
    # an adapter's assumptions are its own vocabulary: another adapter may name its own
    # THREAD_SAFETY, so an adapter row scans its package and the core, never a sibling
    # `canonical_rel` gives one name per file whichever layout it is in, so `allowed` reads
    # the same whether an adapter is still in the core wheel or already its own package.
    # The package is the whole adapter even when the module sits in a subpackage, so a
    # simulator's assumptions are scanned for in the real backend beside it too.
    owner = canonical_rel(Path(str(module.__file__)))
    own_pkg = "/".join(owner.split("/")[:2]) if owner.startswith("adapters/") else None
    offenders = []
    for root in source_roots():
        for path in root.rglob("*.py"):
            rel = canonical_rel(path)
            if rel in allowed:
                continue
            if own_pkg and rel.startswith("adapters/") and not rel.startswith(own_pkg + "/"):
                continue
            for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if pattern.search(line):
                    offenders.append(f"{rel}:{lineno}: {line.strip()}")
    assert not offenders, "\n".join(offenders)


def test_verified_vocabulary_matches_upstream_enums() -> None:
    assert upstream_api.SOUND_TAG_LIST == (
        "alarm",
        "greet",
        "inquire",
        "peck",
        "chirp",
        "coo",
        "wheee",
    )
    assert "kick_left" in upstream_api.SKILLS.name and "sit_toggle" in upstream_api.SKILLS.name
    assert (
        upstream_api.ROBOT_MOVE.name == "robot.move"
        and "NOTIFICATION" in upstream_api.ROBOT_MOVE.note
    )


def test_microduck_refs_are_pinned_to_a_commit() -> None:
    """The Microduck was the one upstream cited at `main` rather than at a hash.

    ADR-0022 asked every adapter for a pin and grandfathered this one, and in the week that
    followed upstream went from API v16 to v23 with nothing here to show it. A pin in the URL
    is what makes that visible next time. Then robotd's releases went on to v41 while the pin
    stayed at v23, so every ref cites the one pin, the ones that quote a design doc included:
    a ref left at an older commit is one the last re-read skipped.
    """
    assert len(upstream_api.PIN) == 40 and upstream_api.PIN.isalnum()
    assert upstream_api.PIN in upstream_api.API_VERSION.source
    assert upstream_api.PIN in upstream_api.ROBOT_SUBSCRIBE.source
    assert "/blob/main/" not in upstream_api.IPC_PROTO
    for ref in upstream_api.all_refs():
        assert "/blob/main/" not in ref.source, ref
        assert upstream_api.PIN in ref.source, ref


def test_the_skew_rule_agrees_with_the_refs_it_rests_on() -> None:
    """The floor and the version read are numbers the handshake compares, and the frame route is
    a path the transport matches `--camera-url` against, so each has to agree with the ref beside
    it: a floor above the version read refuses the very robot quackd read, and a path that is not
    the route's leaves `extras.robotd.mediad_frame` saying nothing about it. What the rule rests
    on, upstream's own word on a skew and the two facts the echo reads, was read, not assumed."""
    assert int(upstream_api.API_FLOOR.name) <= int(upstream_api.API_VERSION.name)
    assert upstream_api.MEDIAD_FRAME_HTTP.name.endswith(upstream_api.MEDIAD_FRAME_PATH)
    for ref in (
        upstream_api.API_FLOOR,
        upstream_api.VERSION_SKEW,
        upstream_api.MOVE_ECHO,
        upstream_api.PAD_DRIVES,
    ):
        assert ref.status == "VERIFIED" and upstream_api.PIN in ref.source, ref


def test_the_posture_labels_are_the_ones_robotd_names() -> None:
    """quackd sorts `robot.state.policy` into standing, sitting and everything else, and the
    labels it sorts were an UNVERIFIED assumption until they were read in robotd's source at the
    pin. The sort has to cover exactly what the two VERIFIED refs name: a label quackd sorts
    that robotd never reports is one read wrong, and one robotd reports that quackd does not
    sort is one nobody read."""
    driven, not_driven = upstream_api.POLICY_LABELS_DRIVEN, upstream_api.POLICY_LABELS_NOT_DRIVEN
    named = [label for ref in (driven, not_driven) for label in ref.name.split(", ")]
    # a configured skill reports its own name, and that name with the suffix while it unwinds
    placeholders = ["<skill>", "<skill>" + upstream_api.UNWIND_SUFFIX]
    assert all(p in named for p in placeholders), named
    sorted_labels = [
        *upstream_api.STANDING_LABELS,
        *upstream_api.SITTING_LABELS,
        *upstream_api.NOT_STANDING_LABELS,
    ]
    assert len(set(sorted_labels)) == len(sorted_labels), "a label is sorted twice"
    assert sorted(set(named) - set(placeholders)) == sorted(sorted_labels)
    for ref in (driven, not_driven):
        assert ref.status == "VERIFIED" and upstream_api.PIN in ref.source, ref


def test_the_labels_robotd_never_counts_still_are_labels_it_names() -> None:
    """The gaze hold goes on judging the head while it waits only as long as the frames it reads
    come from ticks robotd does not count the duck still on, since only then can no glance of
    robotd's own be in them, and it knows those ticks by their labels. A label there that
    robotd never reports is one read wrong. One that stands or seats the duck, or `held`, which
    robotd also gives a tick whose policy failed while it went on driving, is a tick robotd may
    count still, and trusting it would take a glance for somebody else's head."""
    driven, not_driven = upstream_api.POLICY_LABELS_DRIVEN, upstream_api.POLICY_LABELS_NOT_DRIVEN
    named = {label for ref in (driven, not_driven) for label in ref.name.split(", ")}
    never_still = set(upstream_api.NOT_STILL_LABELS)
    assert never_still <= named, never_still - named
    assert not never_still & {*upstream_api.STANDING_LABELS, *upstream_api.SITTING_LABELS, "held"}


def test_the_limp_fall_labels_are_the_ones_robotd_gives_its_sequence() -> None:
    """`stand_up` tells a pilot whose duck robotd still has in its own answer to a fall so, by
    the two labels robotd gives that sequence's ticks, and says what each means. A label there
    that robotd never reports would never match, one that stands or seats the duck would tell a
    duck on its feet it is being posed, and one with no words would leave the pilot none."""
    labels = set(upstream_api.LIMP_FALL_LABELS)
    assert labels <= set(upstream_api.POLICY_LABELS_NOT_DRIVEN.name.split(", "))
    assert labels <= upstream_api.NOT_STANDING_LABELS.keys()
    assert labels <= set(upstream_api.NOT_STILL_LABELS)
    assert not labels & {*upstream_api.STANDING_LABELS, *upstream_api.SITTING_LABELS}
    ref = upstream_api.LIMP_FALL
    assert ref.status == "VERIFIED" and upstream_api.PIN in ref.source, ref


def test_the_head_vocabulary_and_the_idle_numbers_are_the_ones_read() -> None:
    """The gaze hold sends `HEAD_PARAMS` back to robotd, the head pose finds the head_yaw joint
    by its place in `JOINT_NAMES`, and the verbs that steer on the camera wait `IDLE_FADE_S` on a
    robotd from `IDLE_HEAD_SINCE_API` on. Each is a name or a number read upstream at the pin,
    and each has to agree with the ref that says where it was read."""
    assert upstream_api.HEAD_PARAMS == ("neck_pitch", "head_pitch", "head_yaw", "head_roll")
    assert "{" + ", ".join(upstream_api.HEAD_PARAMS) + "}" in upstream_api.ROBOT_HEAD.note
    assert upstream_api.JOINT_NAMES == (
        "left_hip_yaw",
        "left_hip_roll",
        "left_hip_pitch",
        "left_knee",
        "left_ankle",
        "neck_pitch",
        "head_pitch",
        "head_yaw",
        "head_roll",
        "mouth",
        "right_hip_yaw",
        "right_hip_roll",
        "right_hip_pitch",
        "right_knee",
        "right_ankle",
    )
    note = upstream_api.IDLE_HEAD.note
    assert f"IDLE_AFTER ({upstream_api.IDLE_AFTER_S:g} s)" in note
    assert f"FADE ({upstream_api.IDLE_FADE_S:g} s)" in note
    assert f"on API {upstream_api.IDLE_HEAD_SINCE_API}" in note
    # IDLE_HEAD was read at the pin, so the release the pin is has to be one that glances
    assert int(upstream_api.API_VERSION.name) >= upstream_api.IDLE_HEAD_SINCE_API
    for ref in (upstream_api.IDLE_HEAD, upstream_api.HEAD_YAW_SIGN, upstream_api.STATE_FRAMES):
        assert ref.status == "VERIFIED" and upstream_api.PIN in ref.source, ref


def test_the_numbers_that_stop_a_walk_are_the_ones_read() -> None:
    """The safety page, the hardware checklist and the Microduck page say when robotd's deadman
    zeroes a twist, how often `padd` re-sends a still pad's stop, and how long after a driving pad
    goes it asks robotd to sit the duck, and `tests/test_docs.py` takes each number from these
    constants. Each has to say what the VERIFIED ref that read it says, at the pin."""
    assert f"{upstream_api.DEADMAN_S * 1000:g} ms by default" in upstream_api.DEADMAN.note
    heartbeat = f"HEARTBEAT, {upstream_api.PAD_HEARTBEAT_S * 1000:g} ms"
    assert heartbeat in upstream_api.PAD_DRIVES.note
    assert f"PAD_LOST_SIT, {upstream_api.PAD_LOST_SIT_S:g} s" in upstream_api.PAD_DRIVES.note
    for ref in (upstream_api.DEADMAN, upstream_api.PAD_DRIVES):
        assert ref.status == "VERIFIED" and upstream_api.PIN in ref.source, ref


def test_the_camera_numbers_are_the_ones_read() -> None:
    """The lens of a tall `/frame` is worked out from the IMX219's field of view, and the lens of
    an upright WebRTC frame comes from the calibration mediad sends, the alpha family's on an
    alpha. Each number sits beside the VERIFIED ref that read it and says what that ref says, and
    that a tall frame is an alpha's is the one thing assumed."""
    fov = upstream_api.IMX219_FOV_DEG
    assert f"{fov:g} degrees" in upstream_api.IMX219_FOV.name
    assert f"{fov:g} degrees" in upstream_api.ALPHA_UPRIGHT_FOV.name
    assert f"tan({fov / 2:g} degrees)" in upstream_api.ALPHA_UPRIGHT_FOV.note
    assert upstream_api.ALPHA_UPRIGHT_FOV.status == "UNVERIFIED"
    solve = upstream_api.ALPHA_SOLVE
    assert set(solve) == {"width", "height", "fx", "fy", "cx", "cy"}
    assert f"{solve['width']:g} by {solve['height']:g} frame" in upstream_api.ALPHA_INTRINSICS.note
    for ref in (
        upstream_api.IMX219_FOV,
        upstream_api.ALPHA_INTRINSICS,
        upstream_api.CAMERA_MOUNT,
        upstream_api.MEDIA_VIDEO,
    ):
        assert ref.status == "VERIFIED" and upstream_api.PIN in ref.source, ref


def test_lerobot_and_rosbridge_vocabularies_match_what_was_read() -> None:
    assert lerobot_api.ROBOT_TYPE_SO101.name == "so101_follower"
    assert lerobot_api.SO_MOTORS.name.split(", ") == [
        "shoulder_pan",
        "shoulder_lift",
        "elbow_flex",
        "wrist_flex",
        "wrist_roll",
        "gripper",
    ]
    assert lerobot_api.PYTHON.name == ">=3.12" and lerobot_api.PIN in lerobot_api.ROBOT_BASE.source
    assert "NEVER" in lerobot_api.BUS_DISABLE_TORQUE.note
    assert "input()" in lerobot_api.ROBOT_CALIBRATE.note  # why quackd never calibrates
    assert len(lerobot_api.refs_by_status("VERIFIED")) >= 30
    assert rosbridge_api.MSG_TWIST.name == "geometry_msgs/msg/Twist"
    assert rosbridge_api.MSG_COMPRESSED_IMAGE.name == "sensor_msgs/msg/CompressedImage"
    assert rosbridge_api.MSG_ODOMETRY.name == "nav_msgs/msg/Odometry"
    assert "base64" in rosbridge_api.BINARY_BASE64.name
    assert rosbridge_api.PIN in rosbridge_api.TOPIC.source
    assert rosbridge_api.PIN_ROSBRIDGE in rosbridge_api.OP_PUBLISH.source
    assert rosbridge_api.PIN_INTERFACES in rosbridge_api.MSG_TWIST.source
    assert len(rosbridge_api.refs_by_status("VERIFIED")) >= 25


def test_the_names_introspection_asks_a_bridge_for_are_the_ones_upstream_registers() -> None:
    """A wrong service name is a bridge that answers nothing and a body that stays unknown."""
    assert rosbridge_api.SVC_TOPICS.name == "/rosapi/topics"
    assert rosbridge_api.SVC_GET_PARAM.name == "/rosapi/get_param"
    assert rosbridge_api.SRV_GET_PARAM.name == "rosapi_msgs/srv/GetParam"
    assert rosbridge_api.PARAM_NAME_FORM.name == "node:parameter"
    assert rosbridge_api.DESCRIPTION_PARAM.name == "/robot_state_publisher:robot_description"
    assert rosbridge_api.PIN_URDFDOM in rosbridge_api.URDF_MASS.source
    assert rosbridge_api.PIN_RSP in rosbridge_api.DESCRIPTION_TOPIC.source


def test_the_arm_models_joints_are_lerobots_motors_and_every_ref_is_pinned() -> None:
    """The simulator maps a `'<motor>.pos'` key to a joint and an actuator by name, with no
    table in between, which is only sound while the model's names are LeRobot's, in its order.
    """
    assert lerobot_api.SO_MOTORS.name in so_arm100_api.JOINT_NAMES.name
    assert len(so_arm100_api.PIN) == 40 and so_arm100_api.PIN.isalnum()
    for ref in so_arm100_api.all_refs():
        assert so_arm100_api.PIN in ref.source, ref
    assert so_arm100_api.MODEL.name == so_arm100_api.MODEL_FILE
    assert so_arm100_api.MESHES.name.startswith(f"{len(so_arm100_api.FILES) - 1} STL meshes")
    unverified = {r.name for r in so_arm100_api.refs_by_status("UNVERIFIED")}
    assert unverified == {
        "SERVO_DYNAMICS",
        "JOINT_ZERO",
        "JOINT_SIGN",
        "GRIPPER_MAP",
        "WRIST_CAMERA_POSE",
    }


def test_the_simulators_lerobot_facts_are_the_ones_its_refs_read() -> None:
    """The arm simulator takes a few numbers and names from LeRobot as data rather than prose:
    the bus table's ids, the gripper's torque limit and its full scale, the calibration search.
    Each sits beside the VERIFIED ref that read it, and has to say what that ref says."""
    assert list(lerobot_api.SO_MOTOR_IDS) == lerobot_api.SO_MOTORS.name.split(", ")
    assert len(set(lerobot_api.SO_MOTOR_IDS.values())) == len(lerobot_api.SO_MOTOR_IDS)
    assert f"Max_Torque_Limit {lerobot_api.GRIPPER_MAX_TORQUE_LIMIT} " in (
        lerobot_api.SO_GRIPPER_TORQUE_LIMIT.name + " "
    )
    assert lerobot_api.GRIPPER_MAX_TORQUE_LIMIT / lerobot_api.MAX_TORQUE_LIMIT_FULL == 0.5
    assert "50%" in lerobot_api.SO_GRIPPER_TORQUE_LIMIT.note
    chain = lerobot_api.CALIBRATION_DIR
    assert chain.name.startswith(lerobot_api.CALIBRATION_ENV)
    assert f"/{lerobot_api.ROBOTS_SUBDIR}/{lerobot_api.SO_FOLLOWER_NAME}/" in chain.name
    for name in (lerobot_api.LEROBOT_HOME_ENV, lerobot_api.HF_HOME_ENV, lerobot_api.XDG_CACHE_ENV):
        assert name in chain.note, name
    assert lerobot_api.SO_FOLLOWER_NAME in lerobot_api.SO_NAME.name
    step_cap = lerobot_api.ENSURE_SAFE_GOAL_POSITION
    assert step_cap.status == "VERIFIED" and lerobot_api.PIN in step_cap.source
    for word in ("ValueError", "TypeError", "present", "NaN"):
        assert word in step_cap.note, word
    # the words the simulated bus fails in, and the model number its handshake prints
    assert lerobot_api.STS3215_MODEL_NUMBER.name.endswith(f" {lerobot_api.STS3215_MODEL}")
    sync = lerobot_api.BUS_SYNC_READ_ERROR
    assert sync.name.startswith("Failed to sync read '") and "sync write" in sync.note
    for result in ("There is no status packet!", "Failed transmit instruction packet!"):
        assert result in sync.note, result
    assert "is not connected. Run `.connect()` first." in lerobot_api.NOT_CONNECTED.note
    assert "is already connected." in lerobot_api.NOT_CONNECTED.note
    # the finger meshes the simulator cuts pads from are files the pin fetches
    for mesh in (so_arm100_api.FIXED_FINGER_MESH, so_arm100_api.MOVING_JAW_MESH):
        assert mesh in so_arm100_api.FINGER_MESHES.name
        assert f"assets/{mesh}.stl" in so_arm100_api.FILES


def test_the_policy_refs_are_read_at_the_version_the_laptop_runs() -> None:
    """LeRobot's policy names are read against the release a policy server installs, 0.6.1,
    at the commit its tag names, and not at the `main` commit the arm's own refs are pinned
    to. Every one of them cites that commit, none of them is left in the arm's file, and the
    file says which version it read."""
    pin = lerobot_policy_api.PIN
    assert len(pin) == 40 and pin.isalnum() and pin != lerobot_api.PIN
    for ref in lerobot_policy_api.all_refs():
        assert pin in ref.source, ref
    assert lerobot_policy_api.VERSION == lerobot_api.PYPI_VERSION_READ
    assert f"lerobot {lerobot_policy_api.VERSION}" in (lerobot_policy_api.__doc__ or "")
    moved = {
        "POLICY_BASE",
        "PRETRAINED_CONFIG",
        "POLICY_FROM_PRETRAINED",
        "POLICY_SELECT_ACTION",
        "POLICY_RESET",
        "GET_POLICY_CLASS",
        "MAKE_PRE_POST_PROCESSORS",
        "MAKE_POLICY",
        "POLICY_PIPELINE",
    }
    for name in moved:
        assert isinstance(getattr(lerobot_policy_api, name), type(lerobot_api.PACKAGE)), name
        assert not hasattr(lerobot_api, name), f"{name} is still in the arm's own file"
    unverified = {r.name for r in lerobot_policy_api.refs_by_status("UNVERIFIED")}
    assert unverified == {"VLA_PIPELINE", "TICK_MODE", "LOAD_POLICY"}


def test_the_pipeline_is_verified_for_what_the_torch_job_runs_and_no_more() -> None:
    """POLICY_PIPELINE moved to VERIFIED when CI's torch job began running a tiny ACT through
    the real server, and it says it was exercised, on ACT alone. The names the pipeline reads
    as data sit beside the refs that read them and say what those refs say."""
    api = lerobot_policy_api
    assert api.POLICY_PIPELINE.status == "VERIFIED"
    assert "tests/test_policy_pipeline.py" in api.POLICY_PIPELINE.note
    assert "ACT and nothing else" in api.POLICY_PIPELINE.note
    for name in ("SmolVLA", "pi05"):
        assert name in api.VLA_PIPELINE.note, name
    assert api.CHECKPOINT_FILES.name.split(", ") == [
        api.CONFIG_FILE,
        api.WEIGHTS_FILE,
        api.PREPROCESSOR_FILE,
        api.POSTPROCESSOR_FILE,
        api.TRAIN_CONFIG_FILE,
    ]
    assert api.DATASET_INFO_FILE in api.TRAIN_DATASET.name
    assert api.DEFAULT_PROCESSOR_STEPS.name.split(", ") == list(api.DEFAULT_STEP_NAMES)
    assert api.VLA_PROCESSOR_STEPS.name.split(", ") == list(api.VLA_STEP_NAMES)
    assert api.DEVICE_STEP in api.DEFAULT_STEP_NAMES and api.DEVICE_STEP in api.DEVICE_OVERRIDE.name
    assert api.POLICY_TYPES.name == ", ".join(f'"{t}"' for t in api.SERVED_TYPES)
    assert set(api.SERVED_TYPES) <= set(re.findall(r'"(\w+)"', api.ASYNC_SUPPORTED_POLICIES.name))
    keys = api.FEATURE_KEYS.name
    assert api.STATE_KEY in keys and api.IMAGES_PREFIX in keys and api.ACTION_KEY in keys
    for stat in api.QUANTILE_STATS:
        assert stat in api.NORMALIZER_STATS.note, stat
    # the action tokenizer, which trusts remote code by default, is not a step the server allows
    assert "action_tokenizer_processor" in api.TOKENIZER_TRUSTS_REMOTE_CODE.note
    assert "action_tokenizer_processor" not in api.DEFAULT_STEP_NAMES + api.VLA_STEP_NAMES


def test_the_arm_models_zero_and_sign_cite_lerobot_at_its_pin_and_leave_out_the_gripper() -> None:
    """JOINT_ZERO and JOINT_SIGN follow LeRobot's kinematics helper. That is an upstream fact,
    so it is a VERIFIED ref at LeRobot's pin (ADR-0022) and not a path in prose. Both are about
    the five arm joints only: the model's gripper hinge is not zeroed at its middle, LeRobot's
    gripper is 0..100, and GRIPPER_MAP is what places it."""
    kinematics = lerobot_api.KINEMATICS_DEG2RAD
    assert kinematics.status == "VERIFIED" and lerobot_api.PIN in kinematics.source
    for ref in (so_arm100_api.JOINT_ZERO, so_arm100_api.JOINT_SIGN):
        assert "KINEMATICS_DEG2RAD" in ref.note, ref
        assert "five arm joints" in ref.note and "GRIPPER_MAP" in ref.note, ref
        assert "every joint" not in ref.note, ref
    assert "GRIPPER_MAP" in so_arm100_api.NEW_CALIB_ZERO.note


def test_huebot_reads_robotds_protocol_at_the_microducks_pin_and_its_own_side_at_the_maker() -> (
    None
):
    """Huebot's shim answers the JSON-RPC robotd speaks, so the protocol refs are robotd's, read
    in the Microduck's source at the pin and VERIFIED as facts about the protocol. Every claim
    about Huebot's own side is the maker's word, UNVERIFIED and sourced to the maker, and each of
    the ones the design names has a ref of its own: that the shim answers the calls, that it has
    no robot.look yet, the joint order, the deadman, the skills and robot.enable on the floor."""
    api = huebot_api
    assert len(api.PIN) == 40 and api.PIN.isalnum()
    for ref in api.refs_by_status("VERIFIED"):
        assert ref.source.startswith(f"{api.REPO}/blob/{api.PIN}/"), ref
    for ref in api.refs_by_status("UNVERIFIED"):
        assert ref.source == api.MAKER, ref
    unverified = {
        name
        for name, value in vars(api).items()
        if isinstance(value, api.UpstreamRef) and value.status == "UNVERIFIED"
    }
    assert unverified == {
        "SHIM_CALLS",
        "SHIM_LOOK",
        "JOINT_ORDER",
        "SHIM_DEADMAN",
        "SHIM_SKILLS",
        "STAND_UP_RPC",
    }
    for name in ("hello", "robot.move", "robot.stop", "robot.head", "robot.state", "robot.health"):
        assert name in api.SHIM_CALLS.note, f"the maker named {name}"
    assert "index 7" in api.JOINT_ORDER.name
    assert api.JOINT_NAMES.index("head_yaw") == 7
    assert f"{api.DEADMAN_S * 1000:g} ms by default" in api.DEADMAN.note


def test_while_huebot_and_the_microduck_read_one_pin_they_agree_on_the_protocol() -> None:
    """The protocol refs are copied rather than imported, so that the two robots can diverge. A
    copy that went wrong is caught here: while both cite the same pin, every name, vocabulary and
    number they share reads the same. A re-pin of either is a decision this test makes somebody
    take on purpose."""
    if huebot_api.PIN != upstream_api.PIN:
        pytest.skip("the two adapters read robotd at different pins now")
    shared = [
        name
        for name, value in vars(huebot_api).items()
        if isinstance(value, huebot_api.UpstreamRef)
        and value.status == "VERIFIED"
        and isinstance(getattr(upstream_api, name, None), huebot_api.UpstreamRef)
    ]
    assert len(shared) >= 20, shared
    for name in shared:
        mine, theirs = getattr(huebot_api, name), getattr(upstream_api, name)
        assert (mine.name, mine.source) == (theirs.name, theirs.source), name
    for name in (
        "JSONRPC_VERSION",
        "RELEASE",
        "READ_ON",
        "HEAD_PARAMS",
        "JOINT_NAMES",
        "SOUND_TAG_LIST",
        "ERR_METHOD_NOT_FOUND",
        "ERR_INVALID_PARAMS",
        "DEADMAN_S",
        "STANDING_LABELS",
        "SITTING_LABELS",
        "UNWIND_SUFFIX",
        "LIMP_FALL_LABELS",
    ):
        assert getattr(huebot_api, name) == getattr(upstream_api, name), name
    assert set(huebot_api.NOT_STANDING_LABELS) == set(upstream_api.NOT_STANDING_LABELS)
