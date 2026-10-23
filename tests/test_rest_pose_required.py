"""A real arm with no rest pose is refused a run, a dry run, an MCP session and a flock.

Its close lets go of an arm with none wherever it stands, and an SO-101 has no brake, so each
of those would end with the arm falling (ADR-0058). The refusal comes before anything is built
or connected, so these patch the arm's builder to fail the test if a refused arm is ever built,
and check that no run directory was made. The mock and the simulator have no arm to drop and
still run without a pose, and `doctor` and `rest-pose` never ask."""

from __future__ import annotations

import asyncio
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
    assert "lets go of an arm with none wherever it stands at the end of a run" in message
    assert message.endswith("so it would fall there"), message
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
        args = ["run", "flock-hello", "--robots", "a=lerobot:real,b=microduck:mock", *common]
    else:
        Registry(tmp_path / "registry").add_flock(
            StoredFlock(name="pair", members=["arm-r", "arm-m"])
        )
        args = ["run", "flock-hello", "--flock", "pair", *common]
    result = runner.invoke(app, args)
    flat = " ".join(result.output.split())
    assert result.exit_code == 1, result.output
    assert "no rest pose" in flat and "so it would fall there" in flat, flat
    assert "quackd robot rest-pose" in flat, "the refusal names no way out"
    assert "real" not in never_built, never_built
    assert not runs.exists() or not any(runs.iterdir()), "a run directory was made"


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
    assert plan is not None


async def test_a_flock_reached_without_the_cli_refuses_the_arm_too(
    tmp_path: Path, never_built: list[str]
) -> None:
    """`run_pilot_flock` is a library call as well as the CLI's, so it holds the same rule
    itself: a member that is a real arm with no rest pose is refused before any body is built."""
    roster = roster_from_specs(
        {"duck": RobotSpec("microduck", "mock", "duck"), "arm": RobotSpec("lerobot", "real", "arm")}
    )
    providers = {name: FakeProvider.for_duck("flock-hello") for name in roster}
    with pytest.raises(ValueError, match=r"arm \(lerobot:real\) has no rest pose recorded"):
        await asyncio.wait_for(
            run_pilot_flock(
                load_duck("flock-hello"), roster, providers=providers, runs_dir=tmp_path
            ),
            timeout=60,
        )
    assert "real" not in never_built, never_built
    made = await asyncio.to_thread(lambda: any(tmp_path.iterdir()))
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
