"""UNVERIFIED upstream assumptions must not leak past the experimental backends.

One row per upstream (ADR-0006, extended by ADR-0022): the module that spells its names,
the only files allowed to touch its UNVERIFIED refs, and the source prefixes every ref must
link to.
"""

from __future__ import annotations

import math
import re
from pathlib import Path
from types import ModuleType

import pytest

from quackd_alohamini import upstream_api as alohamini_api
from quackd_huebot import upstream_api as huebot_api
from quackd_lerobot import upstream_api as lerobot_api
from quackd_lerobot.policy import endpoint_upstream_api as lerobot_endpoint_api
from quackd_lerobot.policy import upstream_api as lerobot_policy_api
from quackd_lerobot.sim import upstream_api as so_arm100_api
from quackd_microduck import mapd_upstream_api as mapd_api
from quackd_microduck import upstream_api
from quackd_microduck.sim3d import upstream_api as microduck_rl_api
from quackd_moss import upstream_api as moss_api
from quackd_open_duck import upstream_api as open_duck_api
from quackd_rosarm import upstream_api as rosarm_api
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
            "adapters/microduck/body.py",
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
            # and the mock's follower, which takes the best case of the goal kept through
            # torque off where the simulator takes the worst
            "adapters/lerobot/fake_follower.py",
        },
        # rustypot is the library under LeRobot 0.7.0's bus, and its tables are its own
        ("https://github.com/huggingface/lerobot", "https://github.com/pollen-robotics/rustypot"),
    ),
    (
        lerobot_policy_api,
        {
            "adapters/lerobot/policy/upstream_api.py",
            # the pipeline that loads a checkpoint in the policy server, which says what it does
            # about SmolVLA and pi05 (VLA_PIPELINE) and about tick mode (TICK_MODE)
            "adapters/lerobot/policy/pipeline.py",
        },
        ("https://github.com/huggingface/lerobot",),
    ),
    (
        lerobot_endpoint_api,
        {
            "adapters/lerobot/policy/endpoint_upstream_api.py",
            # the endpoint's own module, which says what it does about each, the client, which
            # says what the proxy in front answers as these rows read it, and the server, which
            # says once in its log when a client's credentials reach it
            "adapters/lerobot/policy/endpoint.py",
            "adapters/lerobot/policy/client.py",
            "adapters/lerobot/policy/server.py",
        },
        (
            "https://api.endpoints.huggingface.cloud",
            "https://huggingface.co",
            "https://github.com/huggingface/huggingface_hub",
            "https://docs.pypi.org",
            "https://docs.astral.sh/uv",
        ),
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
        rosarm_api,
        {
            "adapters/rosarm/upstream_api.py",
            # the transport under both backends, which lists them among the state's assumptions,
            # the body records they qualify, and the fake, which reproduces what they say
            "adapters/rosarm/transport.py",
            "adapters/rosarm/bodies.py",
            "adapters/rosarm/fake.py",
        },
        (
            # the binding's refs, imported by name from quackd_rosbridge.upstream_api
            "https://github.com/gramaziokohler/roslibpy",
            "https://github.com/RobotWebTools/rosbridge_suite",
            "https://github.com/ros2/common_interfaces",
            # the controller, the action and its statuses, and where an action's server
            # publishes them
            "https://github.com/ros-controls/ros2_controllers",
            "https://github.com/ros-controls/control_msgs",
            "https://github.com/ros2/rcl_interfaces",
            "https://github.com/ros2/rcl/",
            "https://github.com/ros2/rclcpp/",
            # the four drivers and the descriptions they read
            "https://github.com/UniversalRobots/Universal_Robots_ROS2_Driver",
            "https://github.com/UniversalRobots/Universal_Robots_ROS2_Description",
            "https://github.com/kuka-ros/kuka_drivers",
            "https://github.com/kuka-ros/kuka_robot_descriptions",
            "https://github.com/lbr-stack/lbr_fri_ros2_stack",
            "https://github.com/Yaskawa-Global/motoros2",
            "https://github.com/Yaskawa-Global/motoros2_interfaces",
            "https://github.com/Yaskawa-Global/motoman_ros2_support_packages",
            "https://github.com/ros-industrial/industrial_core",
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
            "adapters/huebot/body.py",
        },
        # robotd's protocol, read in the Microduck's source, and the maker's page for every claim
        # about Huebot's side
        ("https://github.com/pollen-robotics/microduck", "https://ce-net.com/huebot"),
    ),
    (
        moss_api,
        {
            "adapters/moss/upstream_api.py",
            "adapters/moss/cockpit.py",
        },
        # MOSS at its pin, its maker's issue for the masses, and dimOS at the commit MOSS's own
        # Software.md installs
        ("https://github.com/metrox-eth/moss", "https://github.com/dimensionalOS/dimos"),
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
        mapd_api,
        {
            "adapters/microduck/mapd_upstream_api.py",
            # the client that speaks to it, which also hands the simulated map its numbers
            "adapters/microduck/transports/mapd.py",
        },
        ("https://github.com/pollen-robotics/microduck",),
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
    "lerobot_endpoint",
    "rosbridge",
    "rosarm",
    "open_duck",
    "xlerobot",
    "alohamini",
    "toddlerbot",
    "huebot",
    "moss",
    "microduck_rl",
    "mapd",
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


def test_the_release_that_plays_emotions_is_the_one_read() -> None:
    """A verdict that robotd has not taken a twist names an emotion as a cause only on a robotd
    from `EMOTIONS_SINCE` on, so that has to be the release the VERIFIED ref that read the
    emotions names, and the release quackd read has to be one of them."""
    since = "daemon-v" + ".".join(str(n) for n in upstream_api.EMOTIONS_SINCE)
    skills = upstream_api.SKILLS
    assert f"From {since} robot.do plays an emotion" in skills.note
    assert skills.status == "VERIFIED" and upstream_api.PIN in skills.source
    read = tuple(int(n) for n in upstream_api.RELEASE.removeprefix("daemon-v").split("."))
    assert read >= upstream_api.EMOTIONS_SINCE
    assert "EMOTIONS_SINCE" in upstream_api.MOVE_ECHO.note


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


def test_the_mapd_refs_are_assumptions_read_at_one_branch_commit() -> None:
    """`mapd` is on an upstream branch and in no release, so every name quackd spells for it is
    an assumption until a release carries it, and every one cites the one commit it was read at:
    the re-read at that release starts from one place, as robotd's does."""
    assert len(mapd_api.PIN) == 40 and mapd_api.PIN.isalnum() and mapd_api.BRANCH == "maploc-v2"
    assert mapd_api.refs_by_status("VERIFIED") == [], "no release carries it to verify against"
    assert len(mapd_api.all_refs()) == len(mapd_api.refs_by_status("UNVERIFIED")) > 0
    for ref in mapd_api.all_refs():
        assert mapd_api.PIN in ref.source, ref


@pytest.mark.parametrize(
    ("ref", "quoted"),
    [
        (mapd_api.MAPD_SETTLE, f"settle_s {mapd_api.SETTLE_S:g}"),
        (mapd_api.MAPD_SETTLE, f"{mapd_api.STILL_SPEED_MPS:g} m/s"),
        (mapd_api.MAPD_SETTLE, f"{mapd_api.STILL_TURN_RPS:g} rad/s"),
        (mapd_api.MAPD_STREAM, f"every {mapd_api.STREAM_PERIOD_S * 1000:g} ms"),
        (mapd_api.MAPD_AGREE, {2: "two consecutive stops"}.get(mapd_api.AGREE_STOPS, "?")),
        (mapd_api.MAPD_CORRECTION_GATE, f"track_max_correction_m {mapd_api.CORRECTION_M:.2f}"),
        (mapd_api.MAPD_CORRECTION_GATE, f"{mapd_api.CORRECTION_DEG:g} degrees"),
        (mapd_api.MAPD_GRID, f"res_m is {mapd_api.GRID_RES_M:g} when not given"),
        (mapd_api.MAPD_TOF_RANGE, f"min_range_m {mapd_api.TOF_MIN_M:g}"),
        (mapd_api.MAPD_TOF_RANGE, f"max_range_m {mapd_api.TOF_MAX_M:.1f}"),
        (upstream_api.TOF_FOV, f"is {upstream_api.TOF_FOV_DEG:g} by {upstream_api.TOF_FOV_DEG:g}"),
    ],
)
def test_each_number_the_simulated_map_keeps_to_is_the_one_its_ref_quotes(
    ref: object, quoted: str
) -> None:
    """The numbers are typed once, beside the refs, for the code that keeps to them, and each has
    to be the figure its ref quotes from upstream: a number changed on one side alone is a stand-in
    that no longer stands in for what quackd read."""
    assert quoted in str(getattr(ref, "note", "")), (ref, quoted)


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


def beta_bearing_shortfalls(
    shares: tuple[float, ...] = (0.5, 0.75, 0.95),
) -> list[tuple[float, float]]:
    """How far, in degrees, the bearing quackd's detectors take on a beta's frame falls short of
    the one the beta family's solve gives, at each share of the way from the frame's centre to
    its left edge and to its right, along the principal row: (left, right) for each share.

    quackd's is the pinhole through `GC2093_FOV_DEG` from the frame's centre. The solve's undoes
    `BETA_DISTORTION`, OpenCV's model with y at 0, from `BETA_SOLVE`'s principal point. Near the
    very edge the model stops rising and cannot be undone, so the shares stop short of it."""
    solve = upstream_api.BETA_SOLVE
    k1, k2, _p1, p2, k3 = upstream_api.BETA_DISTORTION
    width, fx, cx = solve["width"], solve["fx"], solve["cx"]
    focal = width / 2 / math.tan(math.radians(upstream_api.GC2093_FOV_DEG) / 2)
    centre = (width - 1) / 2

    def distorted(xu: float) -> float:
        r2 = xu * xu
        return xu * (1 + k1 * r2 + k2 * r2**2 + k3 * r2**3) + 3 * p2 * r2

    def undistorted(xd: float) -> float:
        sign, top = math.copysign(1.0, xd), 0.0
        while abs(distorted(sign * (top + 1e-3))) > abs(distorted(sign * top)):
            top += 1e-3
        assert abs(xd) <= abs(distorted(sign * top)), f"{xd} is past where the model stops rising"
        low, high = 0.0, top
        for _ in range(60):
            mid = (low + high) / 2
            low, high = (mid, high) if abs(distorted(sign * mid)) < abs(xd) else (low, mid)
        return sign * (low + high) / 2

    shortfalls = []
    for share in shares:
        left, right = (
            math.degrees(math.atan(abs(undistorted((u - cx) / fx))))
            - math.degrees(math.atan(abs(u - centre) / focal))
            for u in (centre - share * centre, centre + share * centre)
        )
        shortfalls.append((left, right))
    return shortfalls


def test_the_camera_numbers_are_the_ones_read() -> None:
    """The lens of a tall `/frame` is worked out from the IMX219's field of view, the lens of a
    16:9 one wider than tall is the GC2093's, and the lens of an upright WebRTC frame comes from
    the calibration mediad sends, the alpha family's on an alpha. Each number sits beside the
    VERIFIED ref that read it and says what that ref says, the GC2093's worked out from the beta
    family's solve as mediad works it out, and that a tall frame is an alpha's and a 16:9 wide one
    a beta's are the two things assumed."""
    fov = upstream_api.IMX219_FOV_DEG
    assert f"{fov:g} degrees" in upstream_api.IMX219_FOV.name
    assert f"{fov:g} degrees" in upstream_api.ALPHA_UPRIGHT_FOV.name
    assert f"tan({fov / 2:g} degrees)" in upstream_api.ALPHA_UPRIGHT_FOV.note
    assert upstream_api.ALPHA_UPRIGHT_FOV.status == "UNVERIFIED"
    beta, solved = upstream_api.GC2093_FOV_DEG, upstream_api.BETA_SOLVE
    assert set(solved) == {"width", "height", "fx", "fy", "cx", "cy"}
    width, height, fx = solved["width"], solved["height"], solved["fx"]
    # mediad's hfov_deg is 2·atan(640 / fx) on the 1280-wide solve (sensor.rs:151-152)
    assert round(math.degrees(2 * math.atan(width / 2 / fx)), 1) == beta
    assert f"an hfov_deg of {beta:g}" in upstream_api.IMX219_FOV.note
    assert "its numbers BETA_SOLVE" in upstream_api.IMX219_FOV.note
    note = upstream_api.BETA_UPRIGHT_FOV.note
    assert f"{beta:g} degrees" in upstream_api.BETA_UPRIGHT_FOV.name
    assert f"hfov_deg of {beta:g} is 2*atan({width / 2:g}/fx)" in note
    assert f"made on the {width:g} by {height:g} frame" in note
    # how far the solve's principal point sits from the centre, and what that does to a bearing
    # taken from the centre at the middle of the picture and at its edge
    centre = (width - 1) / 2
    off_px = centre - solved["cx"]
    middle = math.degrees(math.atan(off_px / fx))
    edge = math.degrees(math.atan((centre + off_px) / fx) - math.atan(centre / fx))
    assert round(edge / middle, 1) == 0.5
    assert f"about {off_px:.0f} pixels left of the centre of a {width:g} by {height:g}" in note
    assert (
        f"about {middle:.0f} degree left of the one from the optical axis at the middle of the "
        "picture, and about half that at its edges"
    ) in note
    # and with the solve's distortion as well, half way out, three quarters and near the edges
    (half_l, half_r), (most_l, most_r), (edge_l, edge_r) = beta_bearing_shortfalls()
    assert half_l < half_r and most_l < most_r and edge_l < edge_r, "the more on the right"
    assert (
        f"by about {half_l:.0f} to {half_r:.0f} degrees half way from the centre to an edge, "
        f"{most_l:.0f} to {most_r:.0f} three quarters of the way and {edge_l:.0f} to "
        f"{edge_r:.0f} near the edges, the more on the right"
    ) in note
    assert upstream_api.BETA_UPRIGHT_FOV.status == "UNVERIFIED"
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
    from quackd_lerobot.sim import follower as simulated

    sync = lerobot_api.BUS_SYNC_READ_ERROR
    assert sync.name.startswith("Failed to sync read '") and "sync write" in sync.note
    # serialport-rs's words for a reply that never came, and rustypot's for a packet that never
    # went out, which the simulated bus fails in
    assert (simulated.NO_STATUS, simulated.NOT_SENT) == ("Operation timed out", "Timeout error")
    for result in (simulated.NO_STATUS, simulated.NOT_SENT):
        assert f"`{result}`" in sync.note, result
    assert "motor <M>: <error>" in sync.name
    torque = lerobot_api.TORQUE_TRIED_ON_EVERY_MOTOR
    assert torque.name.startswith("Failed to <enable|disable> torque after <k> tries on '<joint>'")
    assert f"SOFollower.bus is a {simulated.BUS_CLASS}" == lerobot_api.SO_BUS.name
    assert "is not connected. Run `.connect()` first." in lerobot_api.NOT_CONNECTED.note
    assert "is already connected." in lerobot_api.NOT_CONNECTED.note
    # the finger meshes the simulator cuts pads from are files the pin fetches
    for mesh in (so_arm100_api.FIXED_FINGER_MESH, so_arm100_api.MOVING_JAW_MESH):
        assert mesh in so_arm100_api.FINGER_MESHES.name
        assert f"assets/{mesh}.stl" in so_arm100_api.FILES


def test_the_policy_refs_are_read_at_the_release_a_policy_server_installs() -> None:
    """LeRobot's policy names are read against the release a policy server installs, at the
    commit its tag names, which since 0.7.0 is the commit the arm's own refs are pinned to as
    well: the arm's bus and the server's policies come from one release. Every one of them
    cites that commit, none of them is left in the arm's file, and the file says which version
    it read."""
    pin = lerobot_policy_api.PIN
    assert len(pin) == 40 and pin.isalnum()
    assert lerobot_policy_api.VERSION == lerobot_api.VERSION and pin == lerobot_api.PIN
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
    assert unverified == {"VLA_PIPELINE", "TICK_MODE"}


def test_the_pipeline_is_verified_for_what_the_torch_job_runs_and_no_more() -> None:
    """POLICY_PIPELINE moved to VERIFIED when CI's torch job began running a tiny ACT through
    the real server, and it says it was exercised, on ACT alone. The names the pipeline reads
    as data sit beside the refs that read them and say what those refs say."""
    api = lerobot_policy_api
    assert api.POLICY_PIPELINE.status == "VERIFIED"
    assert "tests/test_policy_pipeline.py" in api.POLICY_PIPELINE.note
    assert "a tiny ACT and a tiny X-VLA and nothing else" in api.POLICY_PIPELINE.note
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
    for ref, names in _FAMILY_STEPS:
        assert ref.name.split(", ") == list(names), ref.name
        assert ref.status == "VERIFIED", ref
    assert api.DEVICE_STEP in api.DEFAULT_STEP_NAMES and api.DEVICE_STEP in api.DEVICE_OVERRIDE.name
    assert api.POLICY_TYPES.name == ", ".join(f'"{t}"' for t in api.SERVED_TYPES)
    # LeRobot 0.6.1's async server listed no X-VLA, and 0.7.0 replaced it with a remote server
    # that takes any type it can build (ASYNC_SUPPORTED_POLICIES). quackd runs neither
    # (ASYNC_PICKLE), so the old list says only which served types that server took
    served_there = set(re.findall(r'"(\w+)"', api.ASYNC_SUPPORTED_POLICIES.name))
    assert set(api.SERVED_TYPES) - served_there == {"xvla"}
    assert "takes any type it can build" in api.ASYNC_SUPPORTED_POLICIES.name
    # the X-VLA facts the pipeline reads as data, beside the refs that read them
    assert api.XVLA_SERVED_MODE in api.XVLA_ACTION_MODES.name.split(": ", 1)[1].split(", ")
    assert api.XVLA_DEFAULT_MODE in api.XVLA_ACTION_MODES.name
    assert api.XVLA_DOMAIN_STEP in api.XVLA_STEP_NAMES
    assert f"`num_domains` is {api.XVLA_DEFAULT_DOMAINS} unless" in api.XVLA_PROCESSOR_STEPS.note
    assert f"`domain_id`, {api.XVLA_DEFAULT_DOMAIN} unless" in api.XVLA_PROCESSOR_STEPS.note
    # and GR00T's
    assert f"`{api.GROOT_BACKBONE}`" in api.GROOT_BASE_MODEL.note
    for name in api.GROOT_N1_5_STEP_NAMES:
        assert f"`{name}`" in api.GROOT_N1_5_REMOVED.note, name
    steps = (api.GROOT_PACK_STEP, api.GROOT_VLM_STEP, api.GROOT_DECODE_STEP)
    assert set(steps) | {api.GROOT_DATASET_DECODE_STEP} <= set(api.GROOT_STEP_NAMES)
    assert api.GROOT_DECODE_STEP in api.GROOT_DECODE.name
    assert api.GROOT_DATASET_DECODE_STEP in api.GROOT_DECODE.note
    for word in ("RELATIVE NON_EEF", "EEF", "still normalised", "LIBERO"):
        assert word in api.GROOT_DECODE.note, word
    assert f"`{api.GROOT_LIBERO_EMBODIMENT}`" in api.GROOT_DECODE.note
    keys = api.FEATURE_KEYS.name
    assert api.STATE_KEY in keys and api.IMAGES_PREFIX in keys and api.ACTION_KEY in keys
    for stat in api.QUANTILE_STATS:
        assert stat in api.NORMALIZER_STATS.note, stat
    # the action tokenizer, which trusts remote code by default, is not a step the server allows
    assert "action_tokenizer_processor" in api.TOKENIZER_TRUSTS_REMOTE_CODE.note
    for _, names in _FAMILY_STEPS:
        assert "action_tokenizer_processor" not in api.DEFAULT_STEP_NAMES + names


#: Each served type's own processor steps, as the ref that read them names them and as the tuple
#: the pipeline allows them from.
_FAMILY_STEPS = (
    (lerobot_policy_api.SMOLVLA_PROCESSOR_STEPS, lerobot_policy_api.SMOLVLA_STEP_NAMES),
    (lerobot_policy_api.PI05_PROCESSOR_STEPS, lerobot_policy_api.PI05_STEP_NAMES),
    (lerobot_policy_api.PI0_PROCESSOR_STEPS, lerobot_policy_api.PI0_STEP_NAMES),
    (lerobot_policy_api.XVLA_PROCESSOR_STEPS, lerobot_policy_api.XVLA_STEP_NAMES),
    (lerobot_policy_api.GROOT_PROCESSOR_STEPS, lerobot_policy_api.GROOT_STEP_NAMES),
)


def test_every_served_type_allows_the_steps_read_for_it_and_no_other_types() -> None:
    """The pipeline's allowlist is by type: the steps every policy is built from and the ones
    that type's own processors add, read at the pin. A type served with no allowlist could not
    load, and an allowlist for a type nobody serves would be steps read for nothing. Every step
    a type allows past the defaults is one its own ref names."""
    from quackd_lerobot.policy import pipeline

    api = lerobot_policy_api
    assert set(pipeline.STEPS_BY_TYPE) == set(api.SERVED_TYPES)
    assert pipeline.STEPS_BY_TYPE["act"] == frozenset(api.DEFAULT_STEP_NAMES)
    own = {name for _, names in _FAMILY_STEPS for name in names}
    for policy_type, allowed in pipeline.STEPS_BY_TYPE.items():
        assert set(api.DEFAULT_STEP_NAMES) <= allowed, policy_type
        assert allowed - set(api.DEFAULT_STEP_NAMES) <= own, policy_type
        assert not allowed & set(pipeline.REFUSED_STEPS), policy_type
    # a type read and refused is no type served, and the core says why it is not
    from quackd.adapters import policies

    assert not set(pipeline.REFUSED_TYPES) & set(api.SERVED_TYPES)
    not_served = {row.type for row in policies.NOT_SERVED}
    assert set(pipeline.REFUSED_TYPES) <= not_served, set(pipeline.REFUSED_TYPES) - not_served


def test_the_policy_families_are_the_types_the_server_serves() -> None:
    """`quackd.adapters.policies` says, in the core and as data, which learned policies quackd
    serves and which it does not and why, for the pages and the commands that print them on a
    machine with no LeRobot. Its rows are the served types in the server's order, none of the
    ones it does not serve is a type it does, and importing it costs nothing."""
    import subprocess
    import sys

    from quackd.adapters import policies

    assert tuple(f.type for f in policies.FAMILIES) == lerobot_policy_api.SERVED_TYPES
    assert len({f.label for f in policies.FAMILIES}) == len(policies.FAMILIES)
    refused = [row.type for row in policies.NOT_SERVED if row.type is not None]
    assert not set(refused) & set(lerobot_policy_api.SERVED_TYPES), refused
    for row in policies.NOT_SERVED:
        assert row.why and row.would_change, row.name
    heavy = ("torch", "lerobot", "transformers", "huggingface_hub", "quackd_lerobot", "pydantic")
    probe = (
        f"import sys, quackd.adapters.policies\nprint([m for m in {heavy!r} if m in sys.modules])"
    )
    done = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, timeout=60, check=True
    )
    assert done.stdout.strip() == "[]", done.stdout + done.stderr


def test_the_endpoint_refs_say_what_the_code_takes_and_cite_huggingface_hub_at_its_tag() -> None:
    """Hugging Face's Inference Endpoints are read from the API's own document, its pages and
    huggingface_hub at the commit its tag names, and again at the version `uv.lock` holds, where
    diffusers keeps the hub below the newest, so a lock that moves huggingface_hub is a re-read
    somebody has to make on purpose. The names the endpoint's code takes as data sit beside the
    refs that read them and say what those refs say, the image is pinned by its digest on both
    registries, and the UNVERIFIED rows are the ones a first real endpoint settles."""
    from quackd_lerobot.policy import client

    api = lerobot_endpoint_api
    for pin in (api.PIN, api.LOCK_PIN):
        assert len(pin) == 40 and pin.isalnum()
    assert (api.LOCK_PIN == api.PIN) == (api.LOCK_HUB_VERSION == api.HUB_VERSION)
    for ref in api.all_refs():
        if ref.source.startswith(api.HUB_REPO):
            assert api.PIN in ref.source, ref
    lock = (PKG.parent / "uv.lock").read_text(encoding="utf-8")
    locked = set(re.findall(r'name = "huggingface-hub"\nversion = "([^"]+)"', lock))
    assert locked == {api.LOCK_HUB_VERSION}, f"uv.lock moved huggingface_hub: re-read ({locked})"
    assert api.ENDPOINT_STATES.name == ", ".join(f'"{s}"' for s in api.STATES)
    assert api.ENDPOINT_TYPES.name == ", ".join(f'"{t}"' for t in api.TYPES)
    assert api.CREATED_TYPE == "authenticated" and api.CREATED_TYPE in api.TYPES
    assert api.NAME_PATTERN == r"^[a-z0-9-]{1,32}$" and "32 characters" in api.ENDPOINT_NAME.name
    assert api.IDLE_MINUTES == (15, 12 * 60) and "15 minutes to 12 hours" in api.SCALE_TO_ZERO.note
    assert api.POLL_S == 5 and "refresh_every=5" in api.WAIT_POLL.name
    for path, ref in (
        (api.PROVIDER_PATH, api.PROVIDER_LISTING),
        (api.ENDPOINT_PATH, api.ENDPOINT_ROUTES),
        (api.QUOTAS_PATH, api.QUOTAS),
        (api.WHOAMI_PATH, api.WHOAMI),
    ):
        assert path in ref.name, path
    for name in (*api.TOKEN_VARIABLES, api.TOKEN_PATH_VARIABLE, api.HOME_VARIABLE):
        assert name in api.TOKEN_LOOKUP.name, name
    tag, digest = api.IMAGE.split("@", 1)
    assert tag == f"ghcr.io/astral-sh/uv:{api.UV_VERSION}-python3.12-trixie-slim"
    assert re.fullmatch(r"sha256:[0-9a-f]{64}", digest)
    assert (
        f"docker.io/astral/uv:{api.UV_VERSION}-python3.12-trixie-slim@{digest}"
        == api.IMAGE_FALLBACK
    )
    assert api.UV_IMAGE.name.endswith("-python3.12-trixie-slim")
    # the host a bearer may go to is the shape the API documents an endpoint's address in
    example = re.search(r"https://([0-9a-z]+\.endpoints\.huggingface\.cloud)", api.STATUS_URL.note)
    assert example and client.ENDPOINT_AUTHORITY.fullmatch(example.group(1))
    assert {502, 503} <= client.PROXY_UNSERVED
    assert "503" in api.PROXY_WHILE_STARTING.note and "502" in api.PROXY_WHILE_STARTING.note
    unverified = {r.name for r in api.refs_by_status("UNVERIFIED")}
    assert unverified == {
        "AUTHORIZATION_FORWARDED",
        "GHCR_PULLS",
        "PROXY_WHILE_STARTING",
        "PRIVATE_TYPE",
        "PAUSED_ANSWERS",
        "LOGS_BODY",
        "QUOTA_LISTING",
        "NAMESPACE_READS",
        "ADDRESS_KEPT",
        "GPU_IN_CONTAINER",
    }
    assert "never created an Inference Endpoint" in (api.__doc__ or "")


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
    no robot.look yet, the joint order, the deadman, the skills, that its own behaviour code is
    off while the shim is driven, and robot.enable on the floor."""
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
        "SHIM_BEHAVIOURS",
        "STAND_UP_RPC",
    }
    for name in ("hello", "robot.move", "robot.stop", "robot.head", "robot.state", "robot.health"):
        assert name in api.SHIM_CALLS.note, f"the maker named {name}"
    assert "index 7" in api.JOINT_ORDER.name
    assert api.JOINT_NAMES.index("head_yaw") == 7
    assert f"{api.DEADMAN_S * 1000:g} ms by default" in api.DEADMAN.note
    assert "email of 2026-10-15" in api.SHIM_BEHAVIOURS.note
    assert "needs, an LED face, coming to your hand" in api.SHIM_BEHAVIOURS.note


def test_huebot_and_the_microduck_read_one_protocol() -> None:
    """Huebot's module imports the protocol's refs from the Microduck's by name (ADR-0075), so a
    re-pin moves both. Every ref of that name is the same object, and every vocabulary and number
    the same value, so the module cannot have grown a copy of its own."""
    assert huebot_api.PIN == upstream_api.PIN
    shared = [
        name
        for name, value in vars(huebot_api).items()
        if isinstance(value, huebot_api.UpstreamRef)
        and value.status == "VERIFIED"
        and isinstance(getattr(upstream_api, name, None), huebot_api.UpstreamRef)
    ]
    assert len(shared) >= 20, shared
    for name in shared:
        assert getattr(huebot_api, name) is getattr(upstream_api, name), name
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


def test_the_industrial_arms_read_the_protocol_through_the_binding() -> None:
    """The family speaks rosbridge through roslibpy, so every protocol ref it relies on is the
    rosbridge adapter's own object, imported by name, and a re-pin there moves both (ADR-0075).
    Its own refs are pinned to full commits, and its UNVERIFIED ones are the ones its page names."""
    shared = rosarm_api.PROTOCOL_REFS
    assert len(shared) >= 25
    for ref in shared:
        found = [v for v in vars(rosbridge_api).values() if v is ref]
        assert found, f"{ref.name} is not the binding's own object"
    own = [ref for ref in rosarm_api.all_refs() if ref not in shared]
    for ref in own:
        assert re.search(r"/blob/[0-9a-f]{40}/", ref.source), ref
    pins = {rosarm_api.PIN, rosarm_api.PIN_UR, rosarm_api.PIN_KUKA, rosarm_api.PIN_LBR}
    assert all(len(pin) == 40 for pin in pins)
    assert rosarm_api.PIN_HUMBLE not in " ".join(r.source for r in rosarm_api.all_refs()), (
        "humble is cited in notes only: the nightly follows one branch per repository"
    )
    unverified = {r.name for r in rosarm_api.refs_by_status("UNVERIFIED")}
    assert unverified == {
        "STOCK_NAMES",
        "BRIDGE_NODE_NAME",
        "STATUS_TYPES_LOADED",
        "UR_PROTECTIVE_STOP",
        "IIWA_STATUS",
        "IIWA_READINGS",
        "MOTO_FIRST_POINT",
        "MOTO_SINGLE_GOAL",
        "ACTION_STATUS_HEARD",
        "MOTO_NO_ACTION_STATUS",
        "ONE_MOVE_WHEN_QUIET",
        "FAKE_FROM_READING",
    }


def test_the_arm_bodies_take_their_names_from_the_refs_that_read_them() -> None:
    """A body record's action and joints are the names its driver's files give, as the VERIFIED
    ref beside them says them: a record changed on one side alone is a name nobody read."""
    from quackd_rosarm.bodies import BODIES

    ur, iisy, iiwa, moto = (BODIES[t] for t in BODIES)
    assert ur.action == rosarm_api.UR_ACTION.name
    assert ", ".join(ur.joints) == rosarm_api.UR_JOINTS.name
    assert ", ".join(iisy.joints) == rosarm_api.IISY_JOINTS.name
    assert ", ".join(iiwa.joints) == rosarm_api.IIWA_JOINTS.name
    assert iiwa.action.startswith(rosarm_api.IIWA_NAMESPACE.name + "/")
    assert moto.action == "/" + rosarm_api.MOTO_ACTION.name
    assert moto.joints == tuple(rosarm_api.MOTO_JOINTS.name % n for n in range(1, 7))
    assert rosbridge_api.GOAL_THREAD_PARAM.name == "send_action_goals_in_new_thread"
    assert rosbridge_api.BRIDGE_NODE_PARAMS.name == "/rosbridge_websocket"
    assert rosbridge_api.SVC_ACTION_SERVERS.name == "/rosapi/action_servers"
    assert (
        rosarm_api.MSG_FOLLOW_JOINT_TRAJECTORY.name == "control_msgs/action/FollowJointTrajectory"
    )
    assert rosarm_api.ACTION_STATUS_TOPIC.name == "<action>/_action/status"
    assert rosarm_api.MSG_GOAL_STATUS_ARRAY.name == "action_msgs/msg/GoalStatusArray"
