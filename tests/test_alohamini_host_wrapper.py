"""Roboglot's AlohaMini host wrapper: the thing that makes the arms real.

Upstream's own host leaves the arms limp, so Roboglot ships a wrapper that turns torque back on
and says so in every observation. The adapter refuses the arm verbs unless it can see that
field, so this file is what makes that refusal meaningful rather than superstitious.

The wrapper runs on the robot, in upstream's environment, and must never import roboglot. These
tests drive its two pure functions with fakes, and prove it imports with Roboglot absent.
"""

from __future__ import annotations

import importlib.util
import logging
import subprocess
import sys
from types import ModuleType

import pytest

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


class _OrderedBus:
    """A bus that writes down when its torque came on, in one list with the lift's zero."""

    def __init__(self, name: str, calls: list[str], *, fails: bool = False) -> None:
        self.name, self.calls, self.fails = name, calls, fails

    def enable_torque(self) -> None:
        if self.fails:  # one lost packet, and upstream retries none on this bus
            self.calls.append(f"{self.name} raised")
            raise ConnectionError(f"there is no status packet on the {self.name}")
        self.calls.append(f"{self.name} torque")


class _OrderedLift:
    def __init__(self, calls: list[str], *, fails: bool = False) -> None:
        self.calls, self.fails = calls, fails

    def stop(self) -> None:
        if self.fails:
            self.calls.append("lift zero raised")
            raise ConnectionError("there is no status packet on the left_bus")
        self.calls.append("lift zero")


class _OrderedRobot:
    """`_FakeRobot`, with every call written down in the order it happened."""

    is_calibrated = True

    def __init__(
        self,
        *,
        left: bool = True,
        lift: bool = True,
        right_fails: bool = False,
        lift_fails: bool = False,
    ) -> None:
        self.calls: list[str] = []
        self.left_bus = _OrderedBus("left_bus", self.calls) if left else None
        self.right_bus = _OrderedBus("right_bus", self.calls, fails=right_fails)
        self.lift = _OrderedLift(self.calls, fails=lift_fails) if lift else None

    def connect(self) -> str:
        return "connected"

    def get_observation(self) -> dict[str, float]:
        return {}


def test_the_lift_is_zeroed_before_any_torque_and_the_log_says_only_what_was_done(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """`home()` leaves full-speed descent in the lift's Goal_Velocity, and the lift shares the
    left bus. The wrapper energised both buses and only then wrote the lift's zero, so a raise
    from the right bus skipped the zero altogether, and with no left bus it returned before
    the zero while the log said "lift stopped". The zero must go out before any torque on
    every path, nothing may be energised when the zero itself fails, and the log says only
    what was done.

    What this cannot show: what the lift's register holds on a real robot when torque comes
    on, or that `enable_torque()` with no argument reaches the lift's motor. Both rest on the
    notes in the adapter's `upstream_api.py`, not on a robot."""
    module = load_wrapper()

    robot = _OrderedRobot()
    assert module.enable_arm_torque(robot) is True
    assert robot.calls == ["lift zero", "left_bus torque", "right_bus torque"]

    robot = _OrderedRobot(right_fails=True)
    with pytest.raises(ConnectionError):
        module.enable_arm_torque(robot)
    assert robot.calls == ["lift zero", "left_bus torque", "right_bus raised"]

    robot = _OrderedRobot(lift_fails=True)
    with pytest.raises(ConnectionError):
        module.enable_arm_torque(robot)
    assert robot.calls == ["lift zero raised"], "a failed zero must energise nothing"

    def connect_says(**shape: bool) -> tuple[list[str], list[str]]:
        cls = type("_Robot", (_OrderedRobot,), {})
        module.install(cls)
        robot = cls(**shape)
        caplog.clear()
        with caplog.at_level(logging.INFO):
            robot.connect()
        return robot.calls, [r.getMessage() for r in caplog.records]

    assert connect_says() == (
        ["lift zero", "left_bus torque", "right_bus torque"],
        ["Roboglot host: arm torque enabled, lift stopped"],
    ), "the line a working robot logs is unchanged"
    assert connect_says(left=False) == (
        ["lift zero"],
        ["Roboglot host: arm torque NOT enabled, lift stopped"],
    )
    calls, said = connect_says(lift=False)
    assert calls == ["left_bus torque", "right_bus torque"]
    assert len(said) == 1 and "lift stopped" not in said[0], said


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


def test_it_starts_under_python_dash_oo_which_strips_its_docstring() -> None:
    """`--help` was built from `__doc__.splitlines()`, and `python -OO` strips docstrings, so
    `__doc__` was None and the wrapper died on an AttributeError before it had parsed a flag.
    The description it shows is the docstring's first line still, spelled out once."""
    result = subprocess.run(
        [sys.executable, "-OO", str(WRAPPER), "--help"], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
    module = load_wrapper()
    assert module.__doc__ is not None and module.__doc__.splitlines()[0] == module.DESCRIPTION
    assert module.DESCRIPTION in " ".join(result.stdout.split())


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
