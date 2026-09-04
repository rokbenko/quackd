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

from quackd.adapters.lerobot import upstream_api as lerobot_api
from quackd.adapters.open_duck import upstream_api as open_duck_api
from quackd.adapters.reachy_mini import upstream_api as reachy_api
from quackd.adapters.rosbridge import upstream_api as rosbridge_api
from quackd.transport import upstream_api

PKG = Path(__file__).resolve().parents[1] / "quackd"

UPSTREAMS: list[tuple[ModuleType, set[str], tuple[str, ...]]] = [
    (
        upstream_api,
        {
            "transport/upstream_api.py",
            "transport/jsonrpc_unix.py",
            "transport/websocket_stub.py",
            "doctor.py",
        },
        ("https://github.com/pollen-robotics/microduck",),
    ),
    (
        reachy_api,
        {
            "adapters/reachy_mini/upstream_api.py",
            "adapters/reachy_mini/sdk.py",
            "doctor.py",
        },
        (
            "https://github.com/pollen-robotics/reachy_mini",
            "https://huggingface.co/datasets/pollen-robotics/",
        ),
    ),
    (
        lerobot_api,
        {"adapters/lerobot/upstream_api.py", "adapters/lerobot/real.py", "doctor.py"},
        ("https://github.com/huggingface/lerobot",),
    ),
    (
        rosbridge_api,
        {"adapters/rosbridge/upstream_api.py", "adapters/rosbridge/ws.py", "doctor.py"},
        (
            "https://github.com/gramaziokohler/roslibpy",
            "https://github.com/RobotWebTools/rosbridge_suite",
            "https://github.com/ros2/common_interfaces",
        ),
    ),
    (
        open_duck_api,
        {
            "adapters/open_duck/upstream_api.py",
            "adapters/open_duck/bridge.py",
            "doctor.py",
        },
        (
            "https://github.com/apirrone/Open_Duck_Mini_Runtime",
            "https://github.com/apirrone/Open_Duck_Mini",
        ),
    ),
]
IDS = ["microduck", "reachy_mini", "lerobot", "rosbridge", "open_duck"]


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
    owner = Path(str(module.__file__)).resolve().relative_to(PKG).as_posix()
    own_pkg = owner.rsplit("/", 1)[0] if owner.startswith("adapters/") else None
    offenders = []
    for path in PKG.rglob("*.py"):
        rel = path.relative_to(PKG).as_posix()
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
    is what makes that visible next time.
    """
    assert len(upstream_api.PIN) == 40 and upstream_api.PIN.isalnum()
    assert upstream_api.PIN in upstream_api.API_VERSION.source
    assert upstream_api.PIN in upstream_api.ROBOT_SUBSCRIBE.source
    assert "/blob/main/" not in upstream_api.IPC_PROTO
    for ref in upstream_api.all_refs():
        assert "/blob/main/" not in ref.source, ref


def test_reachy_verified_vocabulary_matches_the_sdk_read() -> None:
    # pinned to what was read; the sdk backend asserts the same strings at runtime
    assert reachy_api.MDNS_SERVICE.name == "_reachy-mini._tcp.local."
    assert reachy_api.WS_PATH.name == "/ws/sdk"
    assert reachy_api.EMOTIONS_DATASET.name == "pollen-robotics/reachy-mini-emotions-library"
    assert "no TTS" in reachy_api.MEDIA_PLAY_SOUND.note
    assert reachy_api.GET_STATUS.name == "client.get_status"  # not a ReachyMini method
    assert (
        reachy_api.DISABLE_MOTORS.status == "VERIFIED" and "NEVER" in reachy_api.DISABLE_MOTORS.note
    )
    assert reachy_api.PIN in reachy_api.LOOK_AT_WORLD.source
    assert len(reachy_api.refs_by_status("VERIFIED")) >= 40


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
