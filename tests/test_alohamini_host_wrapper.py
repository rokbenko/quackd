"""Roboglot's AlohaMini host wrapper: the thing that makes the arms real.

Upstream's own host leaves the arms limp, so Roboglot ships a wrapper that turns torque back on
and says so in every observation. The adapter refuses the arm verbs unless it can see that
field, so this file is what makes that refusal meaningful rather than superstitious.

The wrapper runs on the robot, in upstream's environment, and must never import roboglot. These
tests drive its two pure functions with fakes, and prove it imports with Roboglot absent.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from types import ModuleType

from tests.conftest import REPO

WRAPPER = REPO / "bridge" / "alohamini" / "roboglot_alohamini_host.py"


def load_wrapper() -> ModuleType:
    """It lives outside the package on purpose: Roboglot's core must never import it."""
    spec = importlib.util.spec_from_file_location("roboglot_alohamini_host", WRAPPER)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class _FakeBus:
    def __init__(self) -> None:
        self.torque = False

    def enable_torque(self) -> None:
        self.torque = True


class _FakeLift:
    def __init__(self) -> None:
        self.stopped = 0

    def stop(self) -> None:
        self.stopped += 1


class _FakeRobot:
    """Everything the wrapper touches, and nothing else."""

    is_calibrated = True

    def __init__(self, *, right: bool = True) -> None:
        self.left_bus = _FakeBus()
        self.right_bus = _FakeBus() if right else None
        self.lift = _FakeLift()

    def connect(self, calibrate: bool = True) -> str:
        return "connected"

    def get_observation(self) -> dict[str, float]:
        return {"x.vel": 0.0, "arm_left_gripper.pos": 12.0}


def test_it_enables_torque_on_both_arms_and_stops_the_lift() -> None:
    """The lift matters as much as the torque: homing left full-speed descent in its register
    and upstream's zeroing write is commented out, so the robot is travelling until told."""
    module = load_wrapper()
    robot = _FakeRobot()
    assert module.enable_arm_torque(robot) is True
    assert robot.left_bus.torque and robot.right_bus is not None and robot.right_bus.torque
    assert robot.lift.stopped == 1


def test_a_no_follower_robot_has_one_bus_and_that_is_fine() -> None:
    module = load_wrapper()
    robot = _FakeRobot(right=False)
    assert module.enable_arm_torque(robot) is True
    assert robot.left_bus.torque


def test_a_robot_with_no_bus_at_all_reports_failure_rather_than_raising() -> None:
    module = load_wrapper()

    class _Bare:
        pass

    assert module.enable_arm_torque(_Bare()) is False


def test_every_observation_carries_the_three_fields_roboglot_looks_for() -> None:
    module = load_wrapper()
    robot = _FakeRobot()
    obs = module.annotate({"x.vel": 0.0}, robot, torque=True)
    assert obs[module.FIELD_TORQUE] is True
    assert obs[module.FIELD_CALIBRATED] is True
    assert obs[module.FIELD_VERSION] == module.VERSION
    assert obs["x.vel"] == 0.0, "upstream's own keys are untouched"


def _fresh_class() -> type:
    """A new subclass per test: `install` patches a class in place, and restoring the original
    afterwards is fiddlier than never sharing one."""
    return type("_Robot", (_FakeRobot,), {})


def test_installing_wraps_connect_and_observation_without_replacing_them() -> None:
    module = load_wrapper()
    cls = _fresh_class()
    module.install(cls)
    robot = cls()
    assert robot.connect() == "connected", "upstream's own return value survives"
    assert robot.left_bus.torque, "connect enabled torque on the way through"
    obs = robot.get_observation()
    assert obs["arm_left_gripper.pos"] == 12.0, "upstream's own observation survives"
    assert obs[module.FIELD_TORQUE] is True


def test_installing_twice_does_not_stack_wrappers() -> None:
    module = load_wrapper()
    cls = _fresh_class()
    module.install(cls)
    module.install(cls)
    robot = cls()
    robot.connect()
    assert robot.lift.stopped == 1, "the lift was stopped once, not twice"


def test_it_says_what_to_install_when_upstream_is_absent() -> None:
    """It runs on the robot, where a missing upstream is the likely first failure."""
    script = (
        "import sys\n"
        "sys.modules['lerobot'] = None\n"
        "import importlib.util\n"
        f"spec = importlib.util.spec_from_file_location('w', r'{WRAPPER}')\n"
        "m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)\n"
        "raise SystemExit(m.main([]))\n"
    )
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True)
    assert result.returncode == 2
    assert "lerobot_alohamini" in result.stderr


def test_the_wrapper_never_imports_roboglot() -> None:
    """Roboglot's dependencies do not belong on a robot, and this file is the proof."""
    source = WRAPPER.read_text(encoding="utf-8")
    for line in source.splitlines():
        stripped = line.strip()
        if stripped.startswith(("import ", "from ")):
            assert "roboglot" not in stripped, f"the wrapper imports Roboglot: {stripped}"


def test_it_ships_in_the_sdist_and_never_in_the_wheel() -> None:
    import tomllib

    pyproject = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))
    build = pyproject["tool"]["hatch"]["build"]["targets"]
    assert build["wheel"]["packages"] == ["roboglot"], "the wheel carries the package only"
    assert "bridge" in build["sdist"]["include"]
