"""Whole tasks on the industrial arm family, through the agent loop and the scripted pilot.

The mock is the real transport over a fake bridge and a fake arm, so these are the rehearsals the
family can give before an arm: the lookout on all four bodies, a slow wave a person confirms, a stop
from beside a move, a heartbeat that fails in the middle of one, a protective stop and an alarm
mid-move, and a run that ends with a move in flight, whose teardown cancels it and parks nothing.
"""

from __future__ import annotations

import asyncio
import math
from pathlib import Path
from typing import Any

import pytest

from quackd.adapters.described import describe as described
from quackd.agent.loop import RunConfig, run_duck
from quackd.agent.providers.base import ToolCall
from quackd.agent.providers.fake import FakeProvider
from quackd.agent.transcript import Transcript
from quackd.duckfile.parser import load_duck, parse_duck_text
from quackd.safety import Executor, allow_all
from quackd.verbs.registry import STOP_ENDED, registry_from_manifest
from quackd_rosarm import RosArmAdapter, make
from quackd_rosarm.bodies import BODIES, arm_body
from quackd_rosarm.fake import FakeBridge, FakeClock
from quackd_rosarm.transport import RosArmWs

TYPES = list(BODIES)

WAVE = """---
duck: 1
name: rosarm-wave
description: Wave one joint ten degrees and back, slowly.
robots: rosarm:mock
requires: [report_state, move_joints]
verbs:
  allow: [report_state, move_joints, stop]
  confirm: []
budgets:
  max_steps: 10
  max_minutes: 3
  max_llm_calls: 10
success:
  - The first joint went ten degrees out and came back.
---
# Task
Wave the first joint ten degrees and back, slowly.
"""


def _wave(joint: str, *, out: float = 10.0, seconds: float = 4.0) -> list[ToolCall]:
    return [
        ToolCall(name="report_state", arguments={}),
        ToolCall(name="move_joints", arguments={"positions": {joint: out}, "duration_s": seconds}),
        ToolCall(name="move_joints", arguments={"positions": {joint: 0.0}, "duration_s": seconds}),
        ToolCall(name="declare_success", arguments={"reason": "waved and came back"}),
    ]


def _adapter(robot_type: str, *, pace: float = 0.0) -> RosArmAdapter:
    body = arm_body(robot_type)
    bridge = FakeBridge(body, clock=FakeClock(pace=pace))
    transport = RosArmWs(
        None,
        body=body,
        roslibpy=bridge.roslibpy(),
        clock=bridge.clock,
        name="mock",
        assumptions=bridge.assumptions,
    )
    return RosArmAdapter(transport, bridge=bridge)


def _events(result: Any) -> list[dict[str, Any]]:
    return Transcript.read(result.run_dir / "transcript.jsonl")


def _verbs(result: Any) -> list[dict[str, Any]]:
    return [e for e in _events(result) if e["kind"] == "verb"]


def _when_moving(adapter: RosArmAdapter, after_s: float, fault: Any) -> None:
    """Call `fault` once, `after_s` into the first goal the arm runs, on the fake's own clock."""
    bridge = adapter.bridge
    fired: list[bool] = []

    def hook(_now: float, _dt: float) -> None:
        goal = bridge.arm.goal
        if not fired and goal is not None and goal.wall >= after_s:
            fired.append(True)
            fault()

    bridge.clock.add_hook(hook)


@pytest.mark.parametrize("robot_type", TYPES)
async def test_the_lookout_reads_every_arm_back_and_moves_nothing(
    robot_type: str, tmp_path: Path
) -> None:
    adapter = make("mock", robot_type=robot_type)
    result = await run_duck(
        RunConfig(
            duck=load_duck("rosarm-lookout"),
            provider=FakeProvider.for_duck("rosarm-lookout"),
            transport=adapter,
            runs_dir=tmp_path,
        )
    )
    assert result.outcome == "success", result.reason
    assert "no move running" in result.reason
    events = _events(result)
    assert events[0]["robot"]["model"] == described(robot_type).model
    assert events[0]["transport"] == "mock"
    assert {e["name"] for e in _verbs(result)} <= {"report_state", "stop"}
    assert "send_action_goal" not in adapter.bridge.operations()


@pytest.mark.parametrize("robot_type", TYPES)
async def test_a_slow_wave_a_person_confirms_runs_on_every_arm(
    robot_type: str, tmp_path: Path
) -> None:
    adapter = make("mock", robot_type=robot_type)
    joint = BODIES[robot_type].joints[0]
    asked: list[str] = []

    def confirm(name: str, params: dict[str, Any]) -> bool:
        asked.append(name)
        return True

    result = await run_duck(
        RunConfig(
            duck=parse_duck_text(WAVE),
            provider=FakeProvider(script=_wave(joint)),
            transport=adapter,
            runs_dir=tmp_path,
            confirm=confirm,
        )
    )
    assert result.outcome == "success", result.reason
    assert asked == ["move_joints", "move_joints"], "every move was put to a person"
    moves = [e for e in _verbs(result) if e["name"] == "move_joints"]
    assert [e["ok"] for e in moves] == [True, True], moves
    assert adapter.bridge.operations().count("send_action_goal") == 2
    assert math.degrees(adapter.bridge.arm.positions[joint]) == pytest.approx(0.0, abs=0.01)


async def test_a_wave_nobody_confirmed_never_moves(tmp_path: Path) -> None:
    adapter = make("mock")
    result = await run_duck(
        RunConfig(
            duck=parse_duck_text(WAVE),
            provider=FakeProvider(script=_wave("shoulder_pan_joint")),
            transport=adapter,
            runs_dir=tmp_path,
        )
    )
    assert "send_action_goal" not in adapter.bridge.operations(), result.reason


async def test_a_stop_from_beside_a_move_cancels_it_and_says_who() -> None:
    adapter = make("mock")
    manifest = await adapter.connect()
    executor = Executor(
        registry_from_manifest(manifest, adapter), adapter, contract=None, confirm=allow_all
    )
    moving = asyncio.create_task(
        executor.run_verb(
            "move_joints", {"positions": {"shoulder_pan_joint": 40.0}, "duration_s": 8}
        )
    )
    for _ in range(100):  # the move's goal goes out within its first few turns of the loop
        if (await adapter.get_state()).extras["move"] is not None:
            break
        await asyncio.sleep(0)
    await adapter.sleep(1.0)
    stopped = await executor.run_verb("stop", {})
    moved = await moving
    assert stopped.ok, stopped.summary
    assert not moved.ok and moved.data.get("ended_by") == STOP_ENDED, moved
    assert adapter.bridge.operations().count("cancel_action_goal") == 1
    assert (await adapter.get_state()).extras["move"]["status"] == "canceled"
    await adapter.close()


@pytest.mark.parametrize("robot_type", ["ur5e", "yaskawa-motomini"])
async def test_a_heartbeat_that_fails_mid_move_ends_the_run_and_its_teardown_parks_nothing(
    robot_type: str, tmp_path: Path
) -> None:
    """The readings stop half a second into a three second move. The heartbeat, on the wall's
    clock, ends the run; its stop cancels the goal; the teardown parks nothing; the arm holds short
    of the goal it was given. This is also the run that ends with a move in flight."""
    adapter = _adapter(robot_type, pace=1.0)
    joint = BODIES[robot_type].joints[0]
    _when_moving(adapter, 0.5, adapter.bridge.quiet)
    result = await run_duck(
        RunConfig(
            duck=parse_duck_text(WAVE),
            provider=FakeProvider(script=_wave(joint, out=30.0, seconds=3.0)),
            transport=adapter,
            runs_dir=tmp_path,
            confirm=allow_all,
            heartbeat_period_s=0.1,
        )
    )
    assert result.outcome == "aborted", (result.outcome, result.reason)
    notes = " ".join(str(e.get("text")) for e in _events(result) if e["kind"] == "note")
    assert "heartbeat failed: no new /joint_states" in notes, notes
    ops = adapter.bridge.operations()
    assert ops.count("send_action_goal") == 1 and "cancel_action_goal" in ops, ops
    assert ops.index("cancel_action_goal") > ops.index("send_action_goal")
    assert ops[ops.index("cancel_action_goal") :].count("send_action_goal") == 0, "nothing parked"
    reached = math.degrees(adapter.bridge.arm.positions[joint])
    assert 0.0 < reached < 30.0, reached


@pytest.mark.parametrize(
    ("robot_type", "fault", "words"),
    [
        ("ur5e", "protective_stop", "Current goal cancelled during deactivate transition."),
        ("kuka-lbr-iisy-3-r760", "control_stopped", "Current goal cancelled during deactivate"),
        ("yaskawa-motomini", "alarm", "Goal was aborted due to an alarm or error"),
    ],
)
async def test_an_arm_that_stops_itself_mid_move_fails_the_move_and_refuses_the_next(
    robot_type: str, fault: str, words: str, tmp_path: Path
) -> None:
    adapter = make("mock", robot_type=robot_type)
    joint = BODIES[robot_type].joints[0]
    _when_moving(adapter, 1.0, getattr(adapter.bridge.arm, fault))
    result = await run_duck(
        RunConfig(
            duck=parse_duck_text(WAVE),
            provider=FakeProvider(
                script=[
                    *_wave(joint, out=20.0, seconds=4.0)[:3],
                    ToolCall(name="declare_failure", arguments={"reason": "the arm stopped"}),
                ]
            ),
            transport=adapter,
            runs_dir=tmp_path,
            confirm=allow_all,
            heartbeat_period_s=30.0,
        )
    )
    moves = [e for e in _verbs(result) if e["name"] == "move_joints"]
    assert moves and not moves[0]["ok"] and words in moves[0]["summary"], moves
    assert adapter.bridge.operations().count("send_action_goal") == 1, "nothing after the stop"
    # the driver's own word that it stopped reached quackd, and the next move was refused for it
    said = " ".join(str(e) for e in _events(result))
    assert "the arm's driver says it cannot move" in said
