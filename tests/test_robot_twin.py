"""`quackd robot twin`: a simulator of a registered arm, registered beside it.

The twin is `lerobot:mujoco` on the calibration file the arm's own runs read, found where
LeRobot keeps it under the arm's registered name, with the arm's rest pose, pilot and camera
urls copied as they were recorded, except a camera the simulator does not render, which is left
out and said. What it is refused for, each in a sentence: a name that is the source's own, a
source that is not a registered LeRobot arm, a name already taken, which `--force` replaces only
where it is already a simulator, no calibration file to read, and a serial port where the
calibration file goes, which nothing opens or looks for.

Nothing here connects anything or needs the physics. The registry and LeRobot's calibration
directory are the suite's temporary ones (`tests/conftest.py`), and no number here comes off an
arm: the calibration is built from the encoder's tick count and upstream's bus table, with ids
out of the table's order, and the rest pose puts one joint below its travel and one above it,
which a twin copies as recorded rather than clipped.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from quackd.adapters.factory import make_adapter
from quackd.cli import app
from quackd.registry import Registry, RobotEntry
from quackd_lerobot import upstream_api as lr
from quackd_lerobot.arms import SO_ENCODER_TICKS as ENCODER_TICKS
from quackd_lerobot.real import joint_ranges
from quackd_lerobot.sim.model import MOUNTS, calibration_path, read_calibration
from quackd_lerobot.verbs import JOINTS

runner = CliRunner()
CAMERAS = ["opencv://1?name=front", "opencv://2?name=wrist"]
PORTS = ("COM5", "/dev/ttyACM0")
"""An arm's serial port as `--address` names one on Windows and elsewhere."""


def guard_ports(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail the test if anything asks the filesystem about a port in `PORTS`: on Windows `COM5`
    is the device itself wherever it is looked for, and reading one would hold the arm's port
    open waiting for an end a serial port never sends."""
    names = {Path(port).name for port in PORTS}

    def guarded(method: str) -> Any:
        real = getattr(Path, method)

        def call(self: Path, *args: Any, **kwargs: Any) -> Any:
            if self.name in names:
                raise AssertionError(f"Path.{method} reached for the port {self}")
            return real(self, *args, **kwargs)

        return call

    for method in ("stat", "open", "read_text", "resolve"):
        monkeypatch.setattr(Path, method, guarded(method))


def _calibration(share: float = 0.25) -> dict[str, dict[str, int]]:
    """A calibration file's contents: each joint's travel a share of the encoder's turn, off
    centre the way a recorded one is, with ids in the reverse of the bus table's order."""
    ids = list(reversed(list(lr.SO_MOTOR_IDS.values())))
    half = int(share * (ENCODER_TICKS - 1) / 2)
    return {
        name: {
            "id": ids[i],
            "drive_mode": 0,
            "homing_offset": -(i + 1),
            "range_min": ENCODER_TICKS // 2 + i - half,
            "range_max": ENCODER_TICKS // 2 + i + half,
        }
        for i, name in enumerate(JOINTS)
    }


def _write(path: Path, raw: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(raw), encoding="utf-8")
    return path


def _rest_pose(calibration: Path) -> dict[str, float]:
    """A pose with one joint past the floor of its travel and one past its ceiling, and every
    other body joint in the middle, from the calibration's own ranges."""
    ranges = joint_ranges(read_calibration(calibration))
    lo_joint, hi_joint = JOINTS[1], JOINTS[2]
    pose = dict.fromkeys(JOINTS[:-1], 0.0)
    pose[lo_joint] = ranges[lo_joint][0] - 1.0
    pose[hi_joint] = ranges[hi_joint][1] + 1.0
    return pose


def _arm(registry: Registry, raw: dict[str, dict[str, int]]) -> RobotEntry:
    """arm-01 on a port, with a pilot, two cameras and a rest pose, and its calibration where
    LeRobot keeps it."""
    registry.add_robot(
        RobotEntry(
            name="arm-01",
            spec="lerobot:real",
            address="/dev/ttyACM0",
            llm="anthropic",
            camera_url=CAMERAS,
        )
    )
    written = _write(calibration_path("arm-01"), raw)
    return registry.update_robot("arm-01", {"rest_pose": _rest_pose(written)})


def _flat(text: str) -> str:
    return " ".join(text.split())


def test_a_twin_is_the_simulator_on_the_arms_calibration_with_what_it_recorded() -> None:
    registry = Registry()
    raw = _calibration()
    source = _arm(registry, raw)
    result = runner.invoke(app, ["robot", "twin", "arm-01"])
    assert result.exit_code == 0, result.output
    twin = registry.robot("arm-01-sim")
    assert twin.key == "lerobot:mujoco"
    assert twin.address == str(calibration_path("arm-01").resolve())
    assert json.loads(Path(twin.address).read_text(encoding="utf-8")) == raw
    # copied as recorded, the joints past their travel included: clipping is the backend's,
    # at connect, where it says so
    assert twin.rest_pose == source.rest_pose
    assert twin.llm == source.llm
    assert list(twin.camera_urls) == CAMERAS
    # a twin has no port, token or board of the arm's, and remembers under its own name
    assert twin.token is None and twin.host is None and twin.memory_key == "arm-01-sim"
    # the source is not touched
    assert registry.robot("arm-01") == source

    said = _flat(result.output)
    assert "added arm-01-sim: lerobot:mujoco, a simulator of arm-01" in said, said
    assert "copied from arm-01: rest pose, pilot anthropic, 2 camera urls" in said, said
    assert "quackd 0.14 and earlier cannot read the file" in said, said
    assert "quackd preflight <duck> --robot arm-01-sim" in said, said


def test_a_twin_named_for_itself_says_what_its_source_had_not_got() -> None:
    registry = Registry()
    registry.add_robot(RobotEntry(name="bench", spec="lerobot:real", address="COM5"))
    _write(calibration_path("bench"), _calibration())
    result = runner.invoke(app, ["robot", "twin", "bench", "rehearsal"])
    assert result.exit_code == 0, result.output
    twin = registry.robot("rehearsal")
    assert twin.key == "lerobot:mujoco" and twin.rest_pose is None and twin.llm is None
    said = _flat(result.output)
    assert "bench has no rest pose or pilot or camera to copy" in said, said
    # with no pilot to copy, the next command names one
    assert "--robot rehearsal --llm VENDOR[:LLM]" in said, said


def test_address_names_a_calibration_kept_under_another_id(tmp_path: Path) -> None:
    registry = Registry()
    registry.add_robot(RobotEntry(name="arm-01", spec="lerobot:real", address="COM5"))
    elsewhere = _write(tmp_path / "calibrated-as-arm-02.json", _calibration())
    result = runner.invoke(app, ["robot", "twin", "arm-01", "--address", str(elsewhere)])
    assert result.exit_code == 0, result.output
    assert registry.robot("arm-01-sim").address == str(elsewhere.resolve())


def _refused(args: list[str], *needles: str) -> str:
    registry = Registry()
    before = (
        registry.robots_path.read_text(encoding="utf-8") if registry.robots_path.exists() else ""
    )
    result = runner.invoke(app, ["robot", "twin", *args])
    said = _flat(result.output)
    assert result.exit_code == 1, said
    assert "Traceback" not in said, said
    for needle in needles:
        assert needle in said, said
    after = (
        registry.robots_path.read_text(encoding="utf-8") if registry.robots_path.exists() else ""
    )
    assert after == before, "a refused twin changed robots.json"
    return said


def test_a_twin_is_never_registered_over_its_own_source() -> None:
    _arm(Registry(), _calibration())
    _refused(["arm-01", "arm-01"], "arm-01 cannot be its own twin", "leave NAME out")


def test_only_a_registered_lerobot_arm_has_a_twin() -> None:
    _refused(["ghost"], "no robot called 'ghost' is registered")
    Registry().add_robot(RobotEntry(name="duck-a", spec="microduck:mock"))
    _write(calibration_path("duck-a"), _calibration())
    _refused(["duck-a"], "duck-a is microduck:mock", "only a LeRobot arm")


def test_an_arm_of_another_type_has_no_twin_on_the_so101s_physics() -> None:
    """The simulator is an SO-101's physics, which an SO-100 registered with `--robot-type` is not,
    so a twin would rehearse its task on another arm's reach and payload, and copied the spec and
    left the type behind."""
    Registry().add_robot(
        RobotEntry(
            name="desk",
            spec="lerobot:real",
            address="/dev/ttyACM0",
            robot_type="so100_follower",
        )
    )
    _write(calibration_path("desk"), _calibration())
    _refused(["desk"], "desk is an so100_follower", "physics model of an so101_follower")


def test_a_taken_name_is_refused_and_force_replaces_only_a_simulator() -> None:
    registry = Registry()
    _arm(registry, _calibration())
    registry.add_robot(RobotEntry(name="duck-a", spec="microduck:mock", note="the cream one"))
    # without --force, as `robot add` refuses a name that is taken
    _refused(["arm-01", "duck-a"], "'duck-a' is already registered (microduck:mock)")
    # and --force never replaces anything but a simulator
    _refused(
        ["arm-01", "duck-a", "--force"],
        "duck-a is registered as microduck:mock",
        "--force replaces only a lerobot:mujoco robot",
    )
    assert registry.robot("duck-a").note == "the cream one"

    assert runner.invoke(app, ["robot", "twin", "arm-01"]).exit_code == 0
    registry.update_robot("arm-01-sim", {"note": "an old twin", "camera_url": ["opencv://0"]})
    _refused(["arm-01"], "'arm-01-sim' is already registered (lerobot:mujoco)")
    replaced = runner.invoke(app, ["robot", "twin", "arm-01", "--force"])
    assert replaced.exit_code == 0, replaced.output
    assert "replaced arm-01-sim" in _flat(replaced.output)
    twin = registry.robot("arm-01-sim")
    assert twin.note is None and list(twin.camera_urls) == CAMERAS


def test_no_calibration_file_is_refused_with_where_to_point() -> None:
    registry = Registry()
    registry.add_robot(RobotEntry(name="arm-01", spec="lerobot:real", address="COM5"))
    _refused(
        ["arm-01"],
        "arm-01 has no calibration file for its twin to read",
        "LeRobot would keep one for it at",
        "--address PATH",
    )
    _refused(["arm-01", "--address", "nowhere.json"], "--address names", "nothing is there")


@pytest.mark.parametrize("port", PORTS)
def test_a_port_is_never_taken_for_a_twins_calibration(
    port: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """For every other LeRobot robot the address is the port, so a port is an easy slip here.
    It is refused on its shape, before anything opens it or even looks for it, whether typed as
    --address or stored as the address of a simulator given as the source."""
    guard_ports(monkeypatch)
    registry = Registry()
    registry.add_robot(RobotEntry(name="arm-01", spec="lerobot:real", address="COM5"))
    registry.add_robot(RobotEntry(name="bench-sim", spec="lerobot:mujoco", address=port))
    _refused(
        ["arm-01", "--address", port],
        f"--address {port} is a serial port",
        "never its port, so nothing was opened there",
        "--address PATH",
    )
    _refused(["bench-sim", "b2"], f"bench-sim's own address {port} is a serial port")


def test_a_twin_keeps_only_the_cameras_the_simulator_renders() -> None:
    """The simulator renders the views its scene mounts and no other, and refuses a robot that
    names one it has not got, so a camera it cannot render is left out and said, rather than
    copied into a twin that every run and preflight on it would refuse."""
    registry = Registry()
    shown, other = f"opencv://1?name={MOUNTS[1]}", "opencv://2?name=side"
    assert "side" not in MOUNTS
    registry.add_robot(
        RobotEntry(name="arm-01", spec="lerobot:real", address="COM5", camera_url=[shown, other])
    )
    _write(calibration_path("arm-01"), _calibration())
    result = runner.invoke(app, ["robot", "twin", "arm-01"])
    assert result.exit_code == 0, result.output
    twin = registry.robot("arm-01-sim")
    assert list(twin.camera_urls) == [shown]
    # what a run or preflight on the twin builds, which refused the side camera before
    make_adapter(twin.robot_spec, camera_url=twin.camera_urls)
    said = _flat(result.output)
    assert "copied from arm-01: 1 camera url" in said, said
    assert (
        f"arm-01's camera {other} was not copied: it is named side, and the simulator renders "
        f"only {', '.join(MOUNTS[:-1])} and {MOUNTS[-1]}"
    ) in said, said
    assert "quackd robot edit arm-01-sim --camera-url" in said, said


def test_the_warning_names_every_simulator_that_old_releases_cannot_read() -> None:
    """One lerobot:mujoco robot is enough for quackd 0.14 and earlier to refuse the whole file,
    so removing only the twin just made would leave it unreadable to them still."""
    registry = Registry()
    for name in ("arm-01", "arm-02"):
        registry.add_robot(RobotEntry(name=name, spec="lerobot:real", address="COM5"))
        _write(calibration_path(name), _calibration())
    first = _flat(runner.invoke(app, ["robot", "twin", "arm-01"]).output)
    assert "quackd robot remove arm-01-sim before going back to one" in first, first
    second = _flat(runner.invoke(app, ["robot", "twin", "arm-02"]).output)
    assert "now holds 2 lerobot:mujoco robots, arm-01-sim and arm-02-sim" in second, second
    assert "quackd robot remove each of them before going back to one" in second, second
