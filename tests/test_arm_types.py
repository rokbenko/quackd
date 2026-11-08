"""The arm's robot types: one adapter, one SDK, three of LeRobot's arms as data.

The code that drives an arm reads LeRobot's `Robot` and `MotorsBus` and nothing of one arm, so a
type is a record (`quackd_lerobot.arms`). What this holds is that the record is the only thing that
changes: the SO-101 is described exactly as it was, a type nobody answers for is never built, and
a registered robot's type reaches every place that describes or builds it.
"""

from __future__ import annotations

import itertools
import json
import sys
import types
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from quackd.adapters.base import AdapterError
from quackd.adapters.catalogue import BY_NAME
from quackd.adapters.factory import RobotSpec, describe, make_adapter
from quackd.registry import Registry, RegistryError, RobotEntry
from quackd.transport.base import TransportError
from quackd_lerobot import BACKENDS, ROBOT_TYPE, lerobot_manifest, make
from quackd_lerobot import describe as describe_arm
from quackd_lerobot.arms import ARMS
from quackd_lerobot.real import LeRobotReal
from quackd_lerobot.verbs import JOINTS

SO101_DIGESTS = {
    (False, False): "0ccd3bf9097cd348",
    (False, True): "382363112ab7a70a",
    (True, False): "5283a93a65bd3a5c",
    (True, True): "a72c88eaaa121ec6",
}
"""An SO-101's description by camera and policy, on every backend, as it was before there were
types: an arm registered before 0.33.0 says what it said, to its pilot and to a peer."""


@pytest.mark.parametrize(
    ("backend", "camera", "policy"), itertools.product(BACKENDS, *[(False, True)] * 2)
)
def test_an_so101_is_described_exactly_as_before_there_were_types(
    backend: str, camera: bool, policy: bool
) -> None:
    manifest = lerobot_manifest(backend, camera=camera, policy=policy)
    assert manifest.digest() == SO101_DIGESTS[(camera, policy)]
    assert manifest.extras["robot_type"] == ROBOT_TYPE == "so101_follower"


def test_the_catalogue_lists_the_types_the_adapter_builds_in_its_order() -> None:
    """The catalogue is strings, read on a machine with no adapter, so `quackd robot add` can
    refuse a type before anything is installed. It lists what the adapter builds, so a type the
    adapter only describes is never registered, and then refused in words about a rest pose."""
    assert BY_NAME["lerobot"].robot_types == tuple(t for t, arm in ARMS.items() if arm.drivable)
    assert next(iter(ARMS)) == ROBOT_TYPE
    assert "koch_follower" in ARMS and "koch_follower" not in BY_NAME["lerobot"].robot_types
    # the industrial arms are the other adapter with types of its own (tests/test_rosarm_adapter.py)
    typed = ("lerobot", "rosarm")
    assert all(not row.robot_types for row in BY_NAME.values() if row.name not in typed)


def test_every_type_that_is_built_names_who_answers_for_it_and_when() -> None:
    """The owner rule, for a type as for a backend (ADR-0076): what can be built names a person
    and a date, a verified one a date already past, and what names neither is described."""
    for arm in ARMS.values():
        tier = arm.tier
        if arm.drivable:
            assert tier.name in ("bound", "verified"), arm.robot_type
            assert tier.owner and date.fromisoformat(str(tier.date)), arm.robot_type
            if tier.name == "verified":
                assert date.fromisoformat(str(tier.date)) <= date.today()
        else:
            assert tier.name == "described" and tier.owner is None, arm.robot_type
    assert [a.robot_type for a in ARMS.values() if a.drivable] == [
        "so101_follower",
        "so100_follower",
    ]


@pytest.mark.parametrize("backend", BACKENDS)
def test_a_type_nobody_answers_for_is_refused_as_it_is_built_on_every_backend(
    backend: str,
) -> None:
    """Described, and so listed, with its maker's figures, and never built: not even the mock,
    which would rehearse an arm nobody has said quackd drives."""
    assert describe_arm("real", robot_type="koch_follower").model == "lerobot-koch"
    # the simulator refuses it as an SO-101's physics model before the owner rule: either refuses
    with pytest.raises(AdapterError, match=r"described and not driven|physics model of an so101"):
        make(backend, robot_type="koch_follower")


def test_the_simulator_rehearses_the_arm_it_is_a_model_of_and_no_other() -> None:
    with pytest.raises(AdapterError, match="physics model of an so101_follower"):
        describe_arm("mujoco", robot_type="so100_follower")
    with pytest.raises(AdapterError, match="physics model of an so101_follower"):
        make("mujoco", robot_type="so100_follower")
    assert describe_arm("mujoco", robot_type="so101_follower").model == "lerobot-so101"


def test_a_type_is_its_own_sheet_and_the_same_verbs() -> None:
    """What differs is what a pilot judges a task against, and nothing it can do."""
    so100 = describe_arm("real", robot_type="so100_follower")
    so101 = describe_arm("real")
    assert (so100.model, so101.model) == ("lerobot-so100", "lerobot-so101")
    assert so100.verb_names() == so101.verb_names()
    assert so100.datasheet.payload_kg is None, "nobody publishes what an SO-100 lifts"
    assert so100.datasheet.reach_m is not None and so100.datasheet.reach_m.value == 0.33
    assert "No SO-100 has run quackd" in " ".join(so100.datasheet.notes)
    with pytest.raises(AdapterError, match=r"choose one of so101_follower, so100_follower$"):
        describe_arm("real", robot_type="so102_follower")


async def test_the_mock_stands_in_for_the_type_it_was_built_as() -> None:
    adapter = make("mock", robot_type="so100_follower")
    manifest = await adapter.connect()
    assert manifest.model == "lerobot-so100" and manifest.extras["robot_type"] == "so100_follower"
    await adapter.close()


# ── the registry and the factory ────────────────────────────────────────────────────


def test_a_robot_type_is_stored_read_back_and_left_out_when_there_is_none(tmp_path: Path) -> None:
    """Left out of the file when absent, so an older quackd still reads it: robots.json is a
    file nobody re-saves on upgrade, and an older release refuses a key it does not know."""
    registry = Registry(tmp_path)
    registry.add_robot(RobotEntry(name="old", spec="lerobot:real", address="COM5"))
    registry.add_robot(
        RobotEntry(name="new", spec="lerobot:real", address="COM6", robot_type="so100_follower")
    )
    stored = json.loads((tmp_path / "robots.json").read_text(encoding="utf-8"))["robots"]
    assert "robot_type" not in stored["old"]
    assert stored["new"]["robot_type"] == "so100_follower"
    entry = Registry(tmp_path).get_robot("new")
    assert entry is not None and entry.robot_spec.robot_type == "so100_follower"
    assert entry.robot_spec.key == "lerobot:real", "an SO-100 is lerobot:real like any arm"
    assert describe(entry.robot_spec).model == "lerobot-so100"
    old = Registry(tmp_path).get_robot("old")
    assert old is not None and describe(old.robot_spec).model == "lerobot-so101"


def test_the_default_type_is_stored_as_none(tmp_path: Path) -> None:
    """Saying the SO-101 changes nothing, and stored it would make the file one 0.32 and older
    refuse whole, since they read it with no key they have not heard of."""
    registry = Registry(tmp_path)
    registry.add_robot(RobotEntry(name="a", spec="lerobot:real", robot_type="so101_follower"))
    stored = json.loads((tmp_path / "robots.json").read_text(encoding="utf-8"))["robots"]
    assert "robot_type" not in stored["a"]
    registry.update_robot("a", {"robot_type": "so100_follower"})
    registry.update_robot("a", {"robot_type": "so101_follower"})
    entry = registry.get_robot("a")
    assert entry is not None and entry.robot_type is None


def test_a_type_is_taken_at_the_door_where_the_adapter_is_not_installed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The catalogue's list is the word there: a robot is registered on a machine that cannot
    build it, as it is with no type."""
    from quackd.adapters import factory
    from quackd.adapters.base import AdapterNotInstalled

    def not_here(adapter: str) -> Any:
        raise AdapterNotInstalled(adapter, f"quackd[{adapter}]")

    monkeypatch.setattr(factory, "_module", not_here)
    registry = Registry(tmp_path)
    registry.add_robot(RobotEntry(name="a", spec="lerobot:real", robot_type="so100_follower"))
    entry = registry.get_robot("a")
    assert entry is not None and entry.robot_type == "so100_follower"
    with pytest.raises(RegistryError, match="choose one of so101_follower"):
        registry.add_robot(RobotEntry(name="b", spec="lerobot:real", robot_type="koch_follower"))


def test_a_type_the_adapter_does_not_list_is_refused_at_the_door(tmp_path: Path) -> None:
    registry = Registry(tmp_path)
    for kind in ("so102_follower", "koch_follower"):
        with pytest.raises(RegistryError, match="choose one of so101_follower, so100_follower"):
            registry.add_robot(RobotEntry(name="a", spec="lerobot:real", robot_type=kind))
    with pytest.raises(RegistryError, match="physics model of an so101_follower"):
        registry.add_robot(RobotEntry(name="a", spec="lerobot:mujoco", robot_type="so100_follower"))
    with pytest.raises(RegistryError, match="takes no --robot-type"):
        registry.add_robot(
            RobotEntry(name="b", spec="microduck:sim2d", robot_type="so100_follower")
        )
    registry.add_robot(RobotEntry(name="c", spec="lerobot:mock", robot_type="so100_follower"))
    with pytest.raises(RegistryError, match="takes no --robot-type"):
        registry.update_robot("c", {"spec": "microduck:sim2d"})
    registry.update_robot("c", {"robot_type": None})
    entry = registry.get_robot("c")
    assert entry is not None and entry.robot_type is None


def test_a_spec_with_a_type_its_adapter_does_not_take_is_refused_rather_than_dropped() -> None:
    """A robot registered as another arm would otherwise be built as the default one."""
    takers = "takes no robot type, which only lerobot and rosarm have"
    with pytest.raises(AdapterError, match=takers):
        describe(RobotSpec("microduck", "sim2d", robot_type="so100_follower"))
    with pytest.raises(AdapterError, match=takers):
        make_adapter(RobotSpec("microduck", "sim2d", robot_type="so100_follower"))


def test_a_registered_type_reaches_the_arm_it_builds() -> None:
    adapter: Any = make_adapter(RobotSpec("lerobot", "mock", "desk", robot_type="so100_follower"))
    assert adapter.transport.robot_type == "so100_follower"


# ── the real backend ────────────────────────────────────────────────────────────────


def _fake_lerobot(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    """LeRobot as far as `_build_robot` reaches it: a package, a factory and the SO configs,
    each config recording what it was built as."""
    built: list[Any] = []

    class Config:
        def __init__(self, **kwargs: Any) -> None:
            self.kwargs = kwargs

    class SO100(Config):
        pass

    class SO101(Config):
        pass

    lerobot = types.ModuleType("lerobot")
    lerobot.__version__ = "0.6.1"  # type: ignore[attr-defined]
    robots = types.ModuleType("lerobot.robots")

    def make_robot_from_config(config: Any) -> Any:
        built.append(config)
        return config

    robots.make_robot_from_config = make_robot_from_config  # type: ignore[attr-defined]
    so = types.ModuleType("lerobot.robots.so_follower")
    so.SO100FollowerConfig = SO100  # type: ignore[attr-defined]
    so.SO101FollowerConfig = SO101  # type: ignore[attr-defined]
    for module in (lerobot, robots, so):
        monkeypatch.setitem(sys.modules, module.__name__, module)
    return built


@pytest.mark.parametrize(
    ("robot_type", "built_as"),
    [("so101_follower", "SO101"), ("so100_follower", "SO100")],
)
def test_the_real_backend_builds_the_config_its_type_names(
    monkeypatch: pytest.MonkeyPatch, robot_type: str, built_as: str
) -> None:
    built = _fake_lerobot(monkeypatch)
    arm = LeRobotReal(address="COM5", robot_type=robot_type)
    arm._build_robot()
    (config,) = built
    assert type(config).__name__ == built_as
    assert config.kwargs["use_degrees"] is True and config.kwargs["port"] == "COM5"


def test_the_real_backend_will_not_build_a_type_nobody_answers_for(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`make()` refuses it first. This is the backend's own word, for one built some other way."""
    _fake_lerobot(monkeypatch)
    arm = LeRobotReal(address="COM5", robot_type="koch_follower")
    with pytest.raises(TransportError, match="only so101_follower and so100_follower are wired"):
        arm._build_robot()


class _ArmOfOtherJoints:
    """A follower whose bus names other motors: nothing on it may be touched."""

    def __init__(self) -> None:
        self.bus = types.SimpleNamespace(motors={name: None for name in ("base", "elbow")})
        self.touched: list[str] = []

    def connect(self, calibrate: bool = True) -> None:
        self.touched.append("connect")

    def __getattr__(self, name: str) -> Any:
        raise AttributeError(name)


async def test_an_arm_whose_bus_names_other_joints_is_refused_before_anything_is_opened() -> None:
    """Every verb moves `JOINTS` by name and the manifest publishes them, so an arm of other
    joints would be told to move ones it has not got, in a verb already under way."""
    robot = _ArmOfOtherJoints()
    arm = LeRobotReal(address="COM5", robot=robot)
    with pytest.raises(TransportError, match="Nothing was energised") as refused:
        await arm.connect()
    assert "base, elbow" in str(refused.value) and ", ".join(JOINTS) in str(refused.value)
    assert robot.touched == []
