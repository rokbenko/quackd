"""A real arm with no rest pose is refused a run, a dry run, an MCP session and a flock.

With no pose there is nothing to fold the arm to at the end, and its close keeps its torque rather
than let it fall where it stands, so each would leave the arm holding itself up wherever it
stopped (ADR-0058); the close used to let go of it there, and the arm fell. The refusal comes
before anything is built or connected, so these patch the arm's builder to fail the test if a
refused arm is ever built, and check that no run directory was made. The mock and the simulator
have no arm to drop and still run without a pose, and `doctor` and `rest-pose` never ask."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from quackd.adapters.factory import RobotSpec, needs_rest_pose
from quackd.agent.providers.fake import FakeProvider
from quackd.cli import app
from quackd.duckfile.parser import load_duck
from quackd.flock.pilots import roster_from_specs, run_pilot_flock
from quackd.registry import Registry, Resolved, StoredFlock, no_rest_pose_refusal

runner = CliRunner()


def _reg(tmp_path: Path) -> list[str]:
    return ["--registry-dir", str(tmp_path / "registry")]


def _add(tmp_path: Path, name: str, spec: str, *extra: str) -> None:
    added = runner.invoke(app, ["robot", "add", name, spec, *extra, *_reg(tmp_path)])
    assert added.exit_code == 0, added.output


@pytest.fixture
def never_built(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """The arm's builder, made to fail the test if it is asked for a real arm at all."""
    import quackd_lerobot

    built: list[str] = []
    make = quackd_lerobot.make

    def recording(backend: str, **kwargs: Any) -> Any:
        built.append(backend)
        if backend == "real":
            raise AssertionError("a real arm with no rest pose was built")
        return make(backend, **kwargs)

    monkeypatch.setattr(quackd_lerobot, "make", recording)
    return built


@pytest.mark.parametrize(
    ("spec", "needs"),
    [
        ("lerobot:real", True),
        ("lerobot:mock", False),
        ("lerobot:mujoco", False),
        ("microduck:mock", False),
        ("microduck:sim2d", False),
        ("no-such-adapter:real", False),
        ("not a spec at all", False),
    ],
)
def test_only_the_real_arm_needs_a_rest_pose(spec: str, needs: bool) -> None:
    """Read off the adapter's own declaration (`REST_POSE_BACKENDS`), never a name the core
    knows, and a spec that does not parse or names nothing installed needs none here: it fails
    where it always did, when it is built."""
    assert needs_rest_pose(spec) is needs


def test_the_refusal_names_the_arm_and_the_way_out(tmp_path: Path) -> None:
    """A registered arm is sent to record its pose; a bare spec names no arm to record one for,
    so it is sent to register one first, with the address it was given. An arm with a pose, and
    any body that needs none, is not refused."""
    _add(tmp_path, "arm-r", "lerobot:real", "--address", "COM99")
    registry = Registry(tmp_path / "registry")
    entry = registry.robot("arm-r")
    message, hint = no_rest_pose_refusal(Resolved(entry.robot_spec, entry), what="a run") or (
        "",
        "",
    )
    assert message.startswith("arm-r (lerobot:real) has no rest pose recorded"), message
    assert "so at the end of a run, there is nothing to fold it to" in message, message
    assert message.endswith("would be left holding itself up wherever it stopped"), message
    assert hint == "quackd robot rest-pose arm-r"

    bare = Resolved(RobotSpec("lerobot", "real"))
    message, hint = no_rest_pose_refusal(bare, what="a run", address="COM7") or ("", "")
    assert message.startswith("lerobot:real names no registered arm, so it has no rest pose")
    assert (
        hint
        == "quackd robot add NAME lerobot:real --address COM7, then quackd robot rest-pose NAME"
    )

    registry.update_robot("arm-r", {"rest_pose": {"shoulder_pan": 0.0, "shoulder_lift": -90.0}})
    posed = registry.robot("arm-r")
    assert no_rest_pose_refusal(Resolved(posed.robot_spec, posed), what="a run") is None
    assert no_rest_pose_refusal(Resolved(RobotSpec("lerobot", "mock")), what="a run") is None


@pytest.mark.parametrize(
    "how",
    ["registered", "bare", "dry run", "robots", "stored flock"],
)
def test_a_run_on_a_real_arm_with_no_rest_pose_is_refused_before_anything_is_built(
    tmp_path: Path, never_built: list[str], how: str
) -> None:
    _add(tmp_path, "arm-r", "lerobot:real", "--address", "COM99")
    _add(tmp_path, "arm-m", "lerobot:mock")
    runs = tmp_path / "runs"
    common = ["--llm", "fake", "--runs-dir", str(runs), "--no-gif", *_reg(tmp_path)]
    if how == "registered":
        args = ["run", "lerobot-lookout", "--robot", "arm-r", *common]
    elif how == "bare":
        args = ["run", "lerobot-lookout", "--robot", "lerobot:real", "--address", "COM99", *common]
    elif how == "dry run":
        args = ["run", "lerobot-lookout", "--robot", "arm-r", "--dry-run", *common]
    elif how == "robots":
        # the file's own members, so a run that got past the refusal would build the arm
        args = ["run", "flock-hello", "--robots", "duck=microduck:mock,arm=lerobot:real", *common]
    else:
        Registry(tmp_path / "registry").add_flock(
            StoredFlock(name="pair", members=["arm-r", "arm-m"])
        )
        args = ["run", "flock-hello", "--flock", "pair", *common]
    result = runner.invoke(app, args)
    flat = " ".join(result.output.split())
    assert result.exit_code == 1, result.output
    assert "no rest pose" in flat and "holding itself up wherever it stopped" in flat, flat
    assert "quackd robot rest-pose" in flat, "the refusal names no way out"
    assert "real" not in never_built, never_built
    assert not runs.exists() or not any(runs.iterdir()), "a run directory was made"
    # refused by the run itself, before a header or a provider: a flock's own backstop would
    # refuse it too, but only once the header was printed and every pilot built
    assert flat.startswith(("x error", "✗ error")), flat[:300]


def test_a_mock_arm_with_no_rest_pose_still_runs(tmp_path: Path) -> None:
    """The mock has no arm to drop, so a run on it without a pose is a rehearsal, and runs."""
    runs = tmp_path / "runs"
    result = runner.invoke(
        app,
        [
            "run",
            "lerobot-lookout",
            "--robot",
            "lerobot:mock",
            "--llm",
            "fake",
            "--runs-dir",
            str(runs),
            "--no-gif",
            "--no-memory",
        ],
    )
    assert "no rest pose recorded" not in result.output, result.output
    assert any(runs.rglob("summary.json")), result.output


@pytest.mark.parametrize("how", ["robot", "robots", "flock"])
def test_an_mcp_session_on_a_real_arm_with_no_rest_pose_is_refused(
    tmp_path: Path, never_built: list[str], how: str
) -> None:
    from quackd.mcp_server import fleet_from_flags

    _add(tmp_path, "arm-r", "lerobot:real", "--address", "COM99")
    _add(tmp_path, "arm-m", "lerobot:mock")
    reg = str(tmp_path / "registry")
    if how == "robot":
        kwargs: dict[str, Any] = {"robot": "arm-r"}
    elif how == "robots":
        kwargs = {"robots": "a=lerobot:real,b=lerobot:mock"}
    else:
        Registry(reg).add_flock(StoredFlock(name="pair", members=["arm-r", "arm-m"]))
        kwargs = {"flock": "pair"}
    with pytest.raises(SystemExit) as refused:
        fleet_from_flags(registry_dir=reg, **kwargs)
    said = str(refused.value)
    assert "no rest pose" in said and "at the end of an MCP session" in said, said
    assert "quackd robot rest-pose" in said
    assert "real" not in never_built, never_built


def test_an_mcp_session_on_a_mock_arm_with_no_rest_pose_is_served(tmp_path: Path) -> None:
    from quackd.mcp_server import fleet_from_flags

    _add(tmp_path, "arm-m", "lerobot:mock")
    plan = fleet_from_flags(robot="arm-m", registry_dir=str(tmp_path / "registry"))
    assert [spec.key for spec in plan.specs.values()] == ["lerobot:mock"], plan.specs


@pytest.mark.parametrize(
    ("registered", "said"),
    [
        (False, r"arm \(lerobot:real\) names no registered arm, so it has no rest pose"),
        (True, r"arm \(lerobot:real\) has no rest pose recorded.*quackd robot rest-pose arm$"),
    ],
    ids=["spec", "registered"],
)
async def test_a_flock_reached_without_the_cli_refuses_the_arm_too(
    tmp_path: Path, never_built: list[str], registered: bool, said: str
) -> None:
    """`run_pilot_flock` is a library call as well as the CLI's, so it holds the same rule
    itself: a member that is a real arm with no rest pose is refused before any body is built.
    A member from a spec is worded as one, since recording a pose for its name would change
    nothing, and a registered member is sent to record its pose."""
    roster: dict[str, Any] = roster_from_specs(
        {"duck": RobotSpec("microduck", "mock", "duck"), "arm": RobotSpec("lerobot", "real", "arm")}
    )
    if registered:
        _add(tmp_path, "arm", "lerobot:real", "--address", "COM99")
        roster["arm"] = Registry(tmp_path / "registry").robot("arm")
    providers = {name: FakeProvider.for_duck("flock-hello") for name in roster}
    runs = tmp_path / "runs"
    with pytest.raises(ValueError, match=said):
        await asyncio.wait_for(
            run_pilot_flock(load_duck("flock-hello"), roster, providers=providers, runs_dir=runs),
            timeout=60,
        )
    assert "real" not in never_built, never_built
    made = await asyncio.to_thread(lambda: runs.exists() and any(runs.iterdir()))
    assert not made, "a run directory was made"


def test_clearing_a_real_arm_s_pose_says_its_runs_are_refused_now(tmp_path: Path) -> None:
    """`rest-pose --clear` used to say a run now leaves the arm where it stands, which is what a
    run did with a real arm and no pose: it let go of it there. Such a run is refused now, and
    the line says so, with the way back. A mock's line is what it was."""
    _add(tmp_path, "arm-r", "lerobot:real", "--address", "COM99")
    _add(tmp_path, "arm-m", "lerobot:mock")
    registry = Registry(tmp_path / "registry")
    for name in ("arm-r", "arm-m"):
        registry.update_robot(name, {"rest_pose": {"shoulder_pan": 0.0, "shoulder_lift": -90.0}})
    real = runner.invoke(app, ["robot", "rest-pose", "arm-r", "--clear", *_reg(tmp_path)])
    assert real.exit_code == 0, real.output
    flat = " ".join(real.output.split())
    assert "a run, a dry run or an MCP session of arm-r is now refused until a pose is" in flat
    assert "quackd robot rest-pose arm-r" in flat and "torque drops there" not in flat, flat
    mock = runner.invoke(app, ["robot", "rest-pose", "arm-m", "--clear", *_reg(tmp_path)])
    assert "a run now leaves the arm where it stands, and torque drops there" in mock.output


FULL_POSE = {
    "shoulder_pan": 0.0,
    "shoulder_lift": -90.0,
    "elbow_flex": 90.0,
    "wrist_flex": 0.0,
    "wrist_roll": 0.0,
    "gripper": 100.0,
}


def test_a_registered_arm_named_in_a_pilot_flock_is_built_with_its_pose_and_address(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--robot NAME` on a pilot flock file names the member called NAME, and that member is
    the registered robot: built with its address and its rest pose. It used to be built as a
    bare spec of its kind with neither, which the refusal then took for an arm with no pose,
    and which its close would otherwise have let go of wherever it stood."""
    import quackd_lerobot
    from quackd.adapters.base import AdapterError

    _add(tmp_path, "arm", "lerobot:real", "--address", "COM99")
    Registry(tmp_path / "registry").update_robot("arm", {"rest_pose": FULL_POSE})
    built: dict[str, Any] = {}
    make = quackd_lerobot.make

    def stop_at_the_arm(backend: str, **kwargs: Any) -> Any:
        if backend == "real":
            built.update(kwargs)
            raise AdapterError("stopped where the arm is built")
        return make(backend, **kwargs)

    monkeypatch.setattr(quackd_lerobot, "make", stop_at_the_arm)
    result = runner.invoke(
        app,
        [
            "run",
            "flock-hello",
            "--robot",
            "arm",
            "--llm",
            "fake",
            "--yes",
            "--no-memory",
            "--no-gif",
            "--runs-dir",
            str(tmp_path / "runs"),
            *_reg(tmp_path),
        ],
    )
    assert "no rest pose" not in result.output, result.output
    assert built.get("address") == "COM99" and built.get("rest_pose") == FULL_POSE, built


def test_a_spec_in_a_fleet_is_sent_to_a_stored_flock_and_a_solo_one_keeps_its_cameras(
    tmp_path: Path, never_built: list[str]
) -> None:
    """`--robots` and a task file's `robots:` never read the registry, so registering the arm
    and recording its pose would change nothing those spellings run: the way out goes on to a
    stored flock. A solo spec's way out keeps the cameras it was given, so the command it
    prints registers the arm the run was asked for."""
    common = ["--llm", "fake", "--no-gif", "--runs-dir", str(tmp_path / "runs"), *_reg(tmp_path)]
    fleet = runner.invoke(
        app, ["run", "flock-hello", "--robots", "duck=microduck:mock,arm=lerobot:real", *common]
    )
    flat = " ".join(fleet.output.split())
    assert fleet.exit_code == 1, fleet.output
    assert "quackd flock create FLOCK --robot NAME" in flat and "--flock FLOCK" in flat, flat
    solo = runner.invoke(
        app,
        [
            "run",
            "lerobot-lookout",
            "--robot",
            "lerobot:real",
            "--address",
            "COM99",
            "--camera-url",
            "opencv://2",
            *common,
        ],
    )
    flat = " ".join(solo.output.split())
    hint = 'quackd robot add NAME lerobot:real --address COM99 --camera-url "opencv://2"'
    assert hint in flat and "flock create" not in flat, flat
    assert "real" not in never_built, never_built


def test_clearing_the_pose_of_an_entry_this_build_cannot_make_says_what_it_did(
    tmp_path: Path,
) -> None:
    """A stored spec this build cannot make, one hand-written or retired since, still has its
    pose cleared and the line said, with no traceback after it: the line is decided from the
    stored string, which is read without ever raising."""
    _add(tmp_path, "armx", "lerobot:mock")
    path = tmp_path / "registry" / "robots.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["robots"]["armx"]["spec"] = "lerobot:reel"
    data["robots"]["armx"]["rest_pose"] = FULL_POSE
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    cleared = runner.invoke(app, ["robot", "rest-pose", "armx", "--clear", *_reg(tmp_path)])
    assert cleared.exit_code == 0, cleared.output
    assert "cleared armx's rest pose" in cleared.output and "Traceback" not in cleared.output


def test_a_task_that_cannot_run_on_the_arm_says_so_before_the_pose_is_asked_for(
    tmp_path: Path, never_built: list[str]
) -> None:
    """A task the body cannot run is refused for that first: sending a person to fold the arm
    and record a pose, only for the run to refuse the task on its next try, is a detour."""
    _add(tmp_path, "arm-r", "lerobot:real", "--address", "COM99")
    result = runner.invoke(
        app,
        [
            "run",
            "microduck-lookout",
            "--robot",
            "arm-r",
            "--llm",
            "fake",
            "--no-gif",
            "--runs-dir",
            str(tmp_path / "runs"),
            *_reg(tmp_path),
        ],
    )
    flat = " ".join(result.output.split())
    assert result.exit_code == 1 and "cannot run on lerobot:real" in flat, flat
    assert "no rest pose" not in flat, flat
    assert "real" not in never_built, never_built


def test_an_adapter_from_another_release_is_refused_before_it_is_described(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, never_built: list[str]
) -> None:
    """A core beside an adapter from an older release reads every declaration the older one
    does not make as none, so the rest-pose refusal read `lerobot:real` as needing no pose and
    the run went ahead. quackd's own adapters are released with the core, so another major or
    minor is refused wherever a body is described or built, with what to reinstall. A patch
    of the same minor is the same release, and a third party's adapter is never asked."""
    import types

    import quackd_lerobot
    from quackd.adapters import factory
    from quackd.adapters.base import AdapterError

    major, minor, *_ = quackd_lerobot.__version__.split(".")
    monkeypatch.setattr(quackd_lerobot, "__version__", f"{major}.{int(minor) - 1}.0")
    with pytest.raises(AdapterError, match=r"reinstall them together: uv pip install -U"):
        factory.describe(factory.parse_robot_spec("lerobot:mock"))
    _add(tmp_path, "arm-r", "lerobot:real", "--address", "COM99")
    result = runner.invoke(
        app,
        [
            "run",
            "lerobot-lookout",
            "--robot",
            "arm-r",
            "--llm",
            "fake",
            "--no-gif",
            *_reg(tmp_path),
        ],
    )
    flat = " ".join(result.output.split())
    assert result.exit_code == 1 and "is from another release than quackd" in flat, flat
    assert "real" not in never_built, never_built
    monkeypatch.setattr(quackd_lerobot, "__version__", f"{major}.{minor}.99")
    factory.describe(factory.parse_robot_spec("lerobot:mock"))
    stranger = types.SimpleNamespace(__version__="9.9.9")
    factory._same_release("a-third-partys-arm", stranger)
