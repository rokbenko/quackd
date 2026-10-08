"""`quackd preflight`: task files rehearsed on the arm's simulator, and judged by its truth.

What is refused before anything is built comes first, and needs no physics: a robot that is not
a simulator, a registered real arm and the mock among them, and a pilot nobody named. Then the
sidecar, `<task>.sim.yaml`, read and refused as a person would write it, and each check judged
against synthetic truths and transcripts. The runs themselves are last, on the stand-in arm with
the scripted pilot typed in full: a run that passes, one whose predicates fail, the JSON the
same report makes, and the same seed giving the same report twice. Those skip without the
physics extra or a GL context, unless `QUACKD_REQUIRE_GL=1` says they must not.

No number here comes off an arm. The calibration is synthetic, built from the stand-in's own
ranges with ids out of the bus table's order, a joint's move is a share of the travel that
calibration gives it, and each threshold is set against what the run itself can reach.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from typer.testing import CliRunner

from quackd.adapters.base import RestResult
from quackd.adapters.factory import describe, parse_robot_spec
from quackd.cli import app
from quackd.preflight import (
    FIRST_SEED,
    SIDECAR_SUFFIX,
    CycleReport,
    JointMoved,
    ObjectCheck,
    Rehearsal,
    SidecarError,
    joint_series,
    judge_close,
    judge_joint,
    judge_object,
    load_sidecar,
    refuse_default_pilot,
    sidecar_path,
)
from quackd.registry import Registry, RobotEntry
from quackd.transport.base import TransportError
from quackd_lerobot.verbs import JOINTS
from tests.gl import REQUIRE_ENV
from tests.test_robot_twin import PORTS, guard_ports

runner = CliRunner()
REPO = Path(__file__).resolve().parents[1]
LOOKOUT = REPO / "ducks" / "lerobot-lookout.duck"
PAN = "shoulder_pan"


def _flat(text: str) -> str:
    return " ".join(text.split())


PAN_TASK = """---
duck: 1
name: preflight-pan
description: "Turn the base a little and say so."
requires: [move_joints]
verbs:
  allow: [report_state, move_joints, stop]
budgets:
  max_steps: 6
  max_minutes: 2
  max_llm_calls: 12
success:
  - "The base has turned."
---

# Task

Turn your base a little, then say what you did.
"""
"""A task the scripted pilot is taught below (`pan_pilot`), since the bundled arm lookout
allows no verb that moves a joint."""

GRASP_TASK = """---
duck: 1
name: preflight-grasp
description: "Close the gripper on the block between the jaws and lift it."
requires: [gripper, move_joints]
verbs:
  allow: [report_state, gripper, move_joints, stop]
budgets:
  max_steps: 6
  max_minutes: 2
  max_llm_calls: 12
success:
  - "The block is off the table."
---

# Task

Close the gripper on the block between your jaws, lift it off the table, and say so.
"""
"""A task the scripted pilot is taught below (`grasp_pilot`): the jaws start down at the table
around a block, as a sidecar lays one there."""


def _duck(
    tmp_path: Path, sidecar: str | None = None, *, name: str = "lookout", text: str | None = None
) -> Path:
    """A task file in tmp, the bundled arm lookout unless `text` is given, with a sidecar beside
    it when one is given."""
    duck = tmp_path / f"{name}.duck"
    duck.write_text(text or LOOKOUT.read_text(encoding="utf-8"), encoding="utf-8")
    if sidecar is not None:
        (tmp_path / f"{name}{SIDECAR_SUFFIX}").write_text(sidecar, encoding="utf-8")
    return duck


# ── refused before anything is built ────────────────────────────────────────────────────


@pytest.fixture
def nothing_is_built(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every way a robot gets built, made to fail the test if it is reached."""

    def built(*_a: Any, **_k: Any) -> Any:
        raise AssertionError("preflight built a robot it should have refused")

    monkeypatch.setattr("quackd.adapters.factory.make_adapter", built)
    monkeypatch.setattr("quackd_lerobot.make", built)


@pytest.mark.parametrize("robot", ["lerobot:real", "lerobot:mock", "arm-01"])
def test_anything_but_a_simulator_is_refused_before_it_is_built(
    robot: str, tmp_path: Path, nothing_is_built: None
) -> None:
    Registry().add_robot(RobotEntry(name="arm-01", spec="lerobot:real", address="COM5"))
    result = runner.invoke(
        app, ["preflight", str(_duck(tmp_path)), "--robot", robot, "--llm", "fake"]
    )
    said = _flat(result.output)
    assert result.exit_code == 1, said
    assert "is not a simulator, and preflight runs only on one" in said, said
    assert "quackd robot twin NAME" in said, said
    assert "AssertionError" not in said and "Traceback" not in said, said
    assert not (tmp_path / "runs").exists()


@pytest.mark.parametrize("port", PORTS)
def test_a_simulator_whose_address_is_a_port_is_refused_before_anything_opens_it(
    port: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """For every other LeRobot robot `--address` is the port, so a simulator registered on one
    is an easy slip, and preflight connects what it is given. The simulator refuses the port as
    it is built, on its shape, so the arm's port is never opened, read or even looked for."""
    guard_ports(monkeypatch)

    def read(*_a: Any, **_k: Any) -> Any:
        raise AssertionError("a calibration was read from a port")

    monkeypatch.setattr("quackd_lerobot.sim.transport.read_calibration", read)
    Registry().add_robot(RobotEntry(name="bench-sim", spec="lerobot:mujoco", address=port))
    runs = tmp_path / "runs"
    args = ["preflight", str(_duck(tmp_path)), "--robot", "bench-sim", "--llm", "fake"]
    result = runner.invoke(app, [*args, "--runs-dir", str(runs)])
    said = _flat(result.output)
    assert result.exit_code == 1, said
    assert f"--address '{port}' is a serial port" in said, said
    assert "never the arm's port, so nothing was opened there" in said, said
    assert "AssertionError" not in said and not runs.exists(), said


def test_a_pilot_nobody_named_is_refused_and_fake_runs_only_when_typed(
    tmp_path: Path, nothing_is_built: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`resolve_llm` falls back to the scripted pilot when nothing names one, and a rehearsal
    with it would pass for a rehearsal of the model. The conftest empties QUACKD_LLM."""
    Registry().add_robot(RobotEntry(name="arm-sim", spec="lerobot:mujoco"))
    for robot in ("lerobot:mujoco", "arm-sim"):
        result = runner.invoke(app, ["preflight", str(_duck(tmp_path)), "--robot", robot])
        said = _flat(result.output)
        assert result.exit_code == 1, said
        assert "none was named" in said and "--llm fake" in said, said
    refuse_default_pilot("--llm")
    refuse_default_pilot("QUACKD_LLM")
    # named anywhere, it is past the refusal and on to building the robot, which here fails
    monkeypatch.setenv("QUACKD_LLM", "fake")
    result = runner.invoke(app, ["preflight", str(_duck(tmp_path)), "--robot", "arm-sim"])
    assert "none was named" not in _flat(result.output)
    assert "preflight built a robot" in str(result.exception), result.output


# ── the sidecar ─────────────────────────────────────────────────────────────────────────

SIDECAR = """\
scene:
  objects:
    - {name: block, kind: box, size: [0.0125, 0.0125, 0.0125]}
    - {name: stick, kind: capsule, size: [0.004, 0.05], place: jaws, rgba: [1, 0.5, 0, 1]}
checks:
  - at_rest: true
  - joint_moved: {joint: shoulder_pan, min_deg: 5}
  - lifted: {object: stick, min_m: 0.02, when: peak}
  - moved: {object: block, min_m: 0.05, when: latched}
"""


def test_a_sidecar_is_read_beside_its_task_file(tmp_path: Path) -> None:
    duck = _duck(tmp_path, SIDECAR)
    assert sidecar_path(duck) == tmp_path / "lookout.sim.yaml"
    sidecar = load_sidecar(duck, joints=JOINTS)
    assert sidecar is not None and sidecar.at_rest is True
    assert [c.model_dump(exclude_none=True) for c in sidecar.checks][1] == {
        "joint_moved": {"joint": PAN, "min_deg": 5.0}
    }
    items = sidecar.scene_items()
    assert items is not None and [i["name"] for i in items] == ["block", "stick"]
    assert items[0]["place"] == "table" and "mass_kg" not in items[0]
    assert load_sidecar(_duck(tmp_path, None, name="bare")) is None


@pytest.mark.parametrize(
    ("text", "needle"),
    [
        (
            "checks: [{at_rest: true, lifted: {object: a, min_m: 1, when: peak}}]",
            "at_rest and lifted",
        ),
        ("checks: [{moved: {object: a, min_m: 1}}]", "when"),
        ("checks: [{moved: {object: a, min_m: -1, when: peak}}]", "min_m"),
        ("checks: [{joint_moved: {joint: elbow, min_deg: 3}}]", "joint_moved names 'elbow'"),
        ("scene: {objects: [{name: a, kind: box, size: [1, 1]}]}", "a box is 3 positive numbers"),
        ("scene: {objects: [{name: a, kind: cone, size: [1]}]}", "kind"),
        (
            "scene: {objects: [{name: a, kind: box, size: [1, 1, 1], place: jaws}, "
            "{name: b, kind: box, size: [1, 1, 1], place: jaws}]}",
            "a and b are both between",
        ),
        (
            "scene: {objects: [{name: a, kind: box, size: [1, 1, 1]}]}\n"
            "checks: [{moved: {object: b, min_m: 1, when: latched}}]",
            "b is not in the scene",
        ),
        ("checks: [{at_rest: true}, {at_rest: false}]", "at_rest is said once"),
        ("frontmatter: true", "frontmatter"),
        ("- just a list", "a mapping with scene and checks"),
        ("checks: [", "not a YAML file"),
    ],
)
def test_a_sidecar_that_cannot_be_right_is_refused_by_its_path(
    tmp_path: Path, text: str, needle: str
) -> None:
    duck = _duck(tmp_path, text)
    with pytest.raises(SidecarError) as refused:
        load_sidecar(duck, joints=JOINTS)
    said = str(refused.value)
    assert said.startswith(str(sidecar_path(duck))), said
    assert needle in said, said


# ── judging ─────────────────────────────────────────────────────────────────────────────


def test_the_close_passes_at_rest_and_a_refusal_only_where_one_is_expected() -> None:
    reached = RestResult("arrived", "moved to the rest pose")
    refused = RestResult("refused", "a joint reads past its travel")
    assert judge_close(reached, None).ok and judge_close(reached, True).ok
    assert not judge_close(reached, False).ok
    assert not judge_close(refused, None).ok and not judge_close(refused, True).ok
    assert judge_close(refused, False).ok
    assert "a joint reads past its travel" in judge_close(refused, None).detail
    # no rest pose: nothing to return to, which only a sidecar asking for one fails
    assert judge_close(None, None).ok and not judge_close(None, True).ok


def test_at_rest_false_passes_a_robot_that_made_no_rest_move() -> None:
    """`at_rest: false` asks that a rest move that was made be refused. A robot with no rest
    pose makes none, whether no move was tried or the transport said there was nothing to go
    to, and its arm stays where the run left it, which is what such a task asks: it passes, as
    the docs say, and only `at_rest: true` fails it."""
    for rest in (None, RestResult.none()):
        for expect in (None, False):
            verdict = judge_close(rest, expect)
            assert verdict.ok and verdict.detail == "no rest pose to return to", verdict
        assert not judge_close(rest, True).ok


@pytest.mark.parametrize(
    ("how", "said"),
    [
        ("refused", "the rest move was refused: "),
        ("stalled", "the rest move stalled: "),
        ("timeout", "the rest move ran out of time: "),
    ],
)
def test_a_close_that_missed_the_rest_pose_is_named_for_how_it_missed(how: str, said: str) -> None:
    """Every miss used to read as refused, a stall against the table included. Only a refusal
    is what `at_rest: false` asks for: a move that set off and stopped short fails whatever the
    sidecar expects."""
    reason = "shoulder_pan is at 10 with a goal of 30" if how != "refused" else "in a hand"
    missed = RestResult(how, reason)  # type: ignore[arg-type]
    for expect in (None, True, False):
        verdict = judge_close(missed, expect)
        assert verdict.detail == said + reason, verdict
        assert verdict.ok is (how == "refused" and expect is False), (expect, verdict)


def _observation(**joints: float) -> dict[str, Any]:
    return {"kind": "observation", "features": {"state": {"extras": {"joints": joints}}}}


def test_a_joint_is_judged_from_its_first_observed_reading() -> None:
    """From the run's own start, never from zero, and at whichever turn it went furthest."""
    start = 30.0
    events = [
        {"kind": "run_start"},
        _observation(shoulder_pan=start),
        {"kind": "verb_end"},
        _observation(shoulder_pan=start - 12.0),
        _observation(shoulder_pan=start - 4.0),
    ]
    series = joint_series(events)
    assert [s[PAN] for s in series] == [start, start - 12.0, start - 4.0]
    held = judge_joint(JointMoved(joint=PAN, min_deg=10.0), series)
    assert held.ok and "moved 12.0 deg" in held.detail
    assert not judge_joint(JointMoved(joint=PAN, min_deg=13.0), series).ok
    assert not judge_joint(JointMoved(joint="wrist_roll", min_deg=1.0), series).ok


def _truth(**objects: tuple[Any, Any]) -> Any:
    return SimpleNamespace(
        objects={k: v[0] for k, v in objects.items()}, peaks={k: v[1] for k, v in objects.items()}
    )


def test_an_object_is_judged_as_latched_or_at_its_peak_and_never_live() -> None:
    lift = 0.03
    down = SimpleNamespace(lifted=False, lift_m=0.0, moved_m=lift / 3)
    peak = SimpleNamespace(lifted=True, lift_m=lift, moved_m=lift * 2)
    truth = _truth(block=(down, peak))
    asked = ObjectCheck(object="block", min_m=lift / 2, when="peak")
    assert judge_object("lifted", asked, truth).ok
    assert not judge_object("lifted", asked.model_copy(update={"when": "latched"}), truth).ok
    assert judge_object("moved", asked, truth).ok
    moved_now = asked.model_copy(update={"when": "latched", "min_m": lift / 2})
    assert not judge_object("moved", moved_now, truth).ok
    missing = judge_object("moved", asked.model_copy(update={"object": "cube"}), truth)
    assert not missing.ok and "no cube on the table, only block" in missing.detail
    assert not judge_object("lifted", asked, None).ok


# ── the connect cycles ──────────────────────────────────────────────────────────────────


class _Connects:
    """An adapter whose connect raises `error`, or succeeds, and whose bus is left `wedged`."""

    def __init__(self, error: Exception | None = None, *, wedged: bool = False) -> None:
        self.error, self.wedged, self.closed = error, wedged, False
        self.transport = self

    async def connect(self) -> None:
        if self.error is not None:
            raise self.error

    async def close(self) -> None:
        self.closed = True


async def _cycle(adapter: _Connects, faults: str | None) -> CycleReport:
    spec = parse_robot_spec("lerobot:mujoco")
    rehearsal = Rehearsal(spec=spec, manifest=describe(spec), pilot=lambda _duck: None)
    rehearsal.faults = faults
    rehearsal._build = lambda *_a: adapter  # type: ignore[method-assign]
    return await rehearsal._cycle(FIRST_SEED, None)


async def test_a_connect_that_gives_up_on_injected_faults_is_noted_and_nothing_else_is() -> None:
    """The arm's connect retries, then gives up in words when a bus drops too much, and a fault
    plan is there to make it do so: that is the connect working, and it fails nothing. The same
    refusal with no plan is the robot's own and fails the file, and so does anything that
    escapes as something other than a refusal, or a bus left hanging."""
    plan = "handshake=1"
    words = TransportError("lerobot mujoco: connect failed on every attempt. Check the cable.")
    noted = await _cycle(_Connects(words), plan)
    assert noted.ok and noted.error is None and noted.gave_up == str(words)
    assert noted.public()["gave_up"] == str(words)
    unplanned = await _cycle(_Connects(words), None)
    assert not unplanned.ok and unplanned.gave_up is None
    assert unplanned.error is not None and "connect failed on every attempt" in unplanned.error
    crashed = await _cycle(_Connects(RuntimeError("a bug")), plan)
    assert not crashed.ok and crashed.error == "the connect raised RuntimeError: a bug"
    hung = _Connects(wedged=True)
    wedged = await _cycle(hung, plan)
    assert hung.closed and not wedged.ok and wedged.error is not None
    assert "never came back" in wedged.error
    assert (await _cycle(_Connects(), None)).ok


# ── runs, on the stand-in ───────────────────────────────────────────────────────────────


def _twin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, rest_pose: dict[str, float]
) -> dict[str, Any]:
    """arm-01, a real arm with a synthetic calibration where LeRobot keeps it and `rest_pose`,
    twinned as arm-01-sim on the stand-in model. Returns every joint's travel in degrees, from
    that calibration, and the pan's on its own."""
    pytest.importorskip("mujoco")
    from quackd_lerobot.real import joint_ranges
    from quackd_lerobot.sim import standin
    from quackd_lerobot.sim.model import calibration_path, load, read_calibration
    from tests.test_lerobot_sim import _synthetic

    monkeypatch.setattr("quackd_lerobot.sim.transport.default_model", standin.mjcf)
    path = calibration_path("arm-01")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_synthetic(load(standin.mjcf(), seed=0))), encoding="utf-8")
    registry = Registry()
    registry.add_robot(RobotEntry(name="arm-01", spec="lerobot:real", address="COM5"))
    registry.update_robot("arm-01", {"rest_pose": rest_pose})
    made = runner.invoke(app, ["robot", "twin", "arm-01"])
    assert made.exit_code == 0, made.output
    travel = joint_ranges(read_calibration(path))
    return {"travel": travel, "pan": travel[PAN], "runs": tmp_path / "runs"}


@pytest.fixture
def twin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """The twin with a rest pose in the middle of its travel."""
    return _twin(tmp_path, monkeypatch, dict.fromkeys(JOINTS[:-1], 0.0))


@pytest.fixture
def pan_pilot(twin: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> float:
    """The scripted pilot, taught `PAN_TASK`: read the arm, turn the pan half of the way to the
    end of its travel, and say so. Returns how far it turns, in degrees."""
    from quackd.agent.providers import fake
    from quackd.agent.providers.base import ToolCall
    from quackd_lerobot.verbs import MOVE_MIN_S

    turn = twin["pan"][1] / 2

    def strategy(obs: Any, step: int, _history: Any) -> ToolCall:
        joints = ((obs.features.get("state") or {}).get("extras") or {}).get("joints") or {}
        if step == 0:
            return ToolCall(name="report_state", arguments={})
        if step == 1:
            goal = {PAN: joints[PAN] + turn}
            return ToolCall(
                name="move_joints", arguments={"positions": goal, "duration_s": MOVE_MIN_S * 5}
            )
        return ToolCall(name="declare_success", arguments={"reason": "turned the pan"})

    monkeypatch.setitem(fake.STRATEGIES, "preflight-pan", strategy)
    return turn


@pytest.fixture
def grasp_pilot(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """The twin with its jaws open down at the table, where a sidecar lays a block between them,
    and the scripted pilot taught `GRASP_TASK`: close the gripper, raise the shoulder a quarter
    of the way from where it starts to the end of its travel, and say so. The teardown then
    drives the arm back down to that pose, still holding the block, because a rest move never
    opens a hand (`verbs.rest_goal`)."""
    from quackd.agent.providers import fake
    from quackd.agent.providers.base import ToolCall
    from quackd_lerobot.sim import standin
    from quackd_lerobot.sim.model import CUBE, PLACE_FAR, PLACE_NEAR, load
    from quackd_lerobot.verbs import MOVE_MIN_S
    from tests.test_lerobot_sim import _pointing_down

    down = _pointing_down(
        load(standin.mjcf(), seed=0), (PLACE_NEAR + PLACE_FAR) / 2, CUBE.size[0] / 2
    )
    made = _twin(tmp_path, monkeypatch, {**dict.fromkeys(JOINTS[:-1], 0.0), **down})
    lift = JOINTS[1]
    raise_by = (down[lift] - made["travel"][lift][0]) / 4

    def strategy(obs: Any, step: int, _history: Any) -> ToolCall:
        joints = ((obs.features.get("state") or {}).get("extras") or {}).get("joints") or {}
        if step == 0:
            return ToolCall(name="gripper", arguments={"open": False})
        if step == 1:
            # lower on the shoulder is up for the hand, as the model's axes run
            goal = {lift: joints[lift] - raise_by}
            return ToolCall(
                name="move_joints", arguments={"positions": goal, "duration_s": MOVE_MIN_S * 5}
            )
        return ToolCall(name="declare_success", arguments={"reason": "lifted the block"})

    monkeypatch.setitem(fake.STRATEGIES, "preflight-grasp", strategy)
    return made


def _preflight(tmp_path: Path, duck: Path, runs: Path, *extra: str) -> Any:
    result = runner.invoke(
        app,
        [
            "preflight",
            str(duck),
            "--robot",
            "arm-01-sim",
            "--llm",
            "fake",
            "--seeds",
            "1",
            "--connect-cycles",
            "1",
            "--runs-dir",
            str(runs),
            *extra,
        ],
    )
    if "no OpenGL context" in result.output and os.environ.get(REQUIRE_ENV) != "1":
        pytest.skip("no OpenGL context for offscreen rendering")
    return result


def test_a_run_that_did_what_its_sidecar_asks_passes(
    tmp_path: Path, twin: dict[str, Any], pan_pilot: float
) -> None:
    sidecar = f"""\
scene:
  objects:
    - {{name: block, kind: box, size: [0.0125, 0.0125, 0.0125]}}
checks:
  - at_rest: true
  - joint_moved: {{joint: {PAN}, min_deg: {pan_pilot / 2}}}
"""
    result = _preflight(tmp_path, _duck(tmp_path, sidecar, text=PAN_TASK), twin["runs"])
    said = _flat(result.output)
    assert result.exit_code == 0, said
    assert "1 file passed preflight" in said, said
    assert "at the rest pose" in said and "1 of 1" in said and "pass" in said, said
    assert "sim dt" in said and "model cost" in said, said
    # the run's own directory, named for the seed, with its transcript: memory was off
    runs = list(twin["runs"].rglob("transcript.jsonl"))
    assert len(runs) == 1 and runs[0].parent.name.endswith("preflight-seed-0")
    start = next(
        json.loads(line)
        for line in runs[0].read_text(encoding="utf-8").splitlines()
        if json.loads(line)["kind"] == "run_start"
    )
    assert start["memory"] is None and "remember" not in start["tools"], start


def test_play_reaches_the_rehearsed_run(tmp_path: Path, twin: dict[str, Any]) -> None:
    """`--play` rehearses a file with the part a run of it would give its pilot, and its record
    says whose word that was, as a run's does."""
    _preflight(tmp_path, _duck(tmp_path), twin["runs"], "--play")
    (transcript,) = twin["runs"].rglob("transcript.jsonl")
    start = next(
        json.loads(line)
        for line in transcript.read_text(encoding="utf-8").splitlines()
        if json.loads(line)["kind"] == "run_start"
    )
    assert start["play"] == {"on": True, "from": "--play"}
    assert "\n## Playing a part\n" in start["system_prompt"]


def test_a_run_whose_predicates_do_not_hold_fails_saying_which(
    tmp_path: Path, twin: dict[str, Any], pan_pilot: float
) -> None:
    lo, hi = twin["pan"]
    sidecar = f"""\
scene:
  objects:
    - {{name: block, kind: box, size: [0.0125, 0.0125, 0.0125]}}
checks:
  - joint_moved: {{joint: {PAN}, min_deg: {hi - lo}}}
  - lifted: {{object: block, min_m: 0.01, when: peak}}
  - moved: {{object: block, min_m: 0.01, when: latched}}
"""
    result = _preflight(tmp_path, _duck(tmp_path, sidecar, text=PAN_TASK), twin["runs"])
    said = _flat(result.output)
    assert result.exit_code == 1, said
    assert "FAIL" in said and "0 of 3" in said, said
    assert f"seed 0: joint_moved {PAN}: moved" in said, said
    # the scene reached the world: the block is judged, and the cube and pen are not there
    assert "seed 0: lifted block (peak): not lifted at its highest" in said, said
    assert "seed 0: moved block (latched): 0.000 m from where it was laid" in said, said
    assert "1 of 1 file failed preflight" in said, said


def _report(
    tmp_path: Path, twin: dict[str, Any], sidecar: str, name: str, text: str = PAN_TASK
) -> dict[str, Any]:
    result = _preflight(
        tmp_path, _duck(tmp_path, sidecar, name=name, text=text), twin["runs"] / name, "--json"
    )
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    assert len(lines) == 1, result.output
    report = json.loads(lines[0])
    assert result.exit_code == (0 if report["ok"] else 1), result.output
    return dict(report)


TWO_CHECKS = """\
checks:
  - joint_moved: {{joint: shoulder_pan, min_deg: {min_deg}}}
  - moved: {{object: cube, min_m: 0.5, when: peak}}
"""


def test_json_is_the_same_report_one_line_per_file(
    tmp_path: Path, twin: dict[str, Any], pan_pilot: float
) -> None:
    report = _report(tmp_path, twin, TWO_CHECKS.format(min_deg=pan_pilot / 2), "json")
    assert report["name"] == "preflight-pan" and report["ok"] is False
    assert report["sidecar"].endswith("json.sim.yaml") and report["problems"] == []
    assert [c["ok"] for c in report["cycles"]] == [True]
    assert report["sim_dt_s"] > 0
    (run,) = report["runs"]
    assert run["seed"] == 0 and run["outcome"] == "success" and run["errors"] == []
    assert [(c["check"], c["ok"]) for c in run["checks"]] == [
        ("close", True),
        (f"joint_moved {PAN}", True),
        # no scene, so the simulator's own cube, which nothing moved
        ("moved cube (peak)", False),
    ]
    assert report["cost_usd"] == run["cost_usd"]


def test_the_same_seed_gives_the_same_report(
    tmp_path: Path, twin: dict[str, Any], pan_pilot: float
) -> None:
    sidecar = TWO_CHECKS.format(min_deg=pan_pilot / 2)
    first, again = (_report(tmp_path, twin, sidecar, name) for name in ("first", "again"))

    def seen(report: dict[str, Any]) -> list[Any]:
        return [
            (run["seed"], run["outcome"], run["steps"], run["checks"], run["errors"])
            for run in report["runs"]
        ] + [report["cycles"], report["sim_dt_s"]]

    assert seen(first) == seen(again)


def test_objects_are_judged_on_the_truth_latched_before_the_teardown_moved_them(
    tmp_path: Path, grasp_pilot: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The pilot pinches the block and lifts it, and the run ends there. The teardown's stop
    latches the truth, and its rest move then carries the arm back down to the table with the
    block still in its hand, so by the time the world is let go of the block is barely off the
    table. Every check here asks for the block's own height, which holds as the run left it and
    at its peak, and would not have held on the live world at the end."""
    from quackd_lerobot.sim.model import CUBE
    from quackd_lerobot.sim.world import ArmWorld

    tall = 2 * CUBE.size[2]
    live: list[Any] = []
    let_go = ArmWorld.close

    def close(world: ArmWorld) -> None:
        if not world.closed:
            live.append(world.truth().objects.get("block"))
        let_go(world)

    monkeypatch.setattr(ArmWorld, "close", close)
    sidecar = f"""\
scene:
  objects:
    - {{name: block, kind: box, size: {list(CUBE.size)}, place: jaws}}
checks:
  - lifted: {{object: block, min_m: {tall}, when: latched}}
  - lifted: {{object: block, min_m: {tall}, when: peak}}
  - moved: {{object: block, min_m: {tall}, when: latched}}
"""
    report = _report(tmp_path, grasp_pilot, sidecar, "grasp", text=GRASP_TASK)
    (run,) = report["runs"]
    assert report["ok"] and run["outcome"] == "success", report
    assert [(c["check"], c["ok"]) for c in run["checks"]] == [
        ("close", True),
        ("lifted block (latched)", True),
        ("lifted block (peak)", True),
        ("moved block (latched)", True),
    ], run["checks"]
    # the world as the run's close let it go, after the rest move: in the hand, and low
    after = live[-1]
    assert after is not None and after.touching, after
    assert after.lift_m < tall and after.moved_m < tall, after
