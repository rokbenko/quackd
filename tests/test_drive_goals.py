"""A goal in a drive session: flown on the session's body, under the session's contract and the
session's own flags, and ended by whatever ends the session's own calls.

Each test here was a goal escaping the session it ran in."""

from __future__ import annotations

import asyncio
import io
import json
import sys
from pathlib import Path
from typing import Any

from quackd.adapters.factory import RobotSpec, make_adapter
from quackd.agent.providers.fake import FakeProvider
from quackd.drive.run import DriveArgs, run_drive
from quackd.drive.session import Answered, DriveSession
from quackd.duckfile.parser import duck_from_goal, parse_duck_text
from quackd.duckfile.schema import Budgets
from quackd.log import EventLog
from quackd.mcp_server import RobotSession
from quackd.safety import Budget, Executor, Heartbeat
from quackd.transport.base import DuckState
from quackd.verbs.registry import default_registry
from tests.test_drive_session import _drive, _settled

VERBS = ["report_state", "observe", "search_scan", "go_to", "kick", "move", "stop"]


def _goal_records(run_dir: Path) -> list[dict[str, Any]]:
    (goal_dir,) = (run_dir / "goals").iterdir()
    lines = (goal_dir / "transcript.jsonl").read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines]


async def test_a_dry_run_sessions_goal_sends_nothing(tmp_path: Path) -> None:
    """`--dry-run` stopped at the session's own executor, and a goal's executor ran every verb
    on the body for real: the scripted pilot's patrol sent its walk to the robot."""
    drive, body, _records = await _drive(tmp_path, dry_run=True)
    goal = drive.submit("patrol the room", by="cli", channel="pipe")
    await _settled(goal)
    await drive.close()
    await drive.robot.close()
    assert body.intents_of("move") == [], "a dry-run session's goal moved the body"


async def test_a_dry_run_goal_says_so_in_its_own_record(tmp_path: Path) -> None:
    args = DriveArgs(robot="microduck:mock", llm="fake", dry_run=True, runs_dir=str(tmp_path))
    run_dir = await run_drive(
        args, say=lambda _line: None, stream=io.StringIO("patrol the room\nquit\n"), keyboard=False
    )
    events = _goal_records(run_dir)
    start = next(e for e in events if e["kind"] == "run_start")
    moved = [e for e in events if e["kind"] == "intent" and e.get("intent") == "move"]
    assert start["dry_run"] is True and moved == []


async def test_a_goal_does_not_start_once_the_sessions_budget_is_spent(tmp_path: Path) -> None:
    """The session refused a typed walk with its steps spent, and walked a goal straight after:
    the goal's own contract was a fresh one with steps of its own."""
    drive, body, _records = await _drive(tmp_path)
    duck = duck_from_goal("drive", VERBS)
    budgets = duck.frontmatter.budgets.model_copy(update={"max_steps": 1})
    drive.robot.adopt(
        duck.model_copy(
            update={"frontmatter": duck.frontmatter.model_copy(update={"budgets": budgets})}
        )
    )
    await _settled(drive.submit("report state"))
    walked = drive.submit("forward")
    await _settled(walked)
    assert (await walked.ended).outcome != "ok", "control: the session's budget is spent"
    goal = drive.submit("patrol the room")
    await _settled(goal)
    await drive.robot.close()
    assert (await goal.ended).outcome == "refused"
    assert "budget is spent" in (await goal.ended).summary
    assert body.intents_of("move") == []


async def test_a_goals_steps_are_the_sessions_too(tmp_path: Path) -> None:
    """Goals spent steps the session never counted, so its own budget bounded none of them."""
    drive, _body, _records = await _drive(tmp_path)
    budget = drive.robot.executor.budget
    assert budget is not None
    before = budget.steps
    goal = drive.submit("look around and tell me what you see")
    await _settled(goal)
    await drive.robot.close()
    (run,) = (tmp_path / "goals").iterdir()
    events = [
        json.loads(line)
        for line in (run / "transcript.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    spent = next(e for e in events if e["kind"] == "run_end")["steps"]
    assert spent > 0 and budget.steps == before + spent


async def test_a_goal_keeps_the_battery_rule_of_the_sessions_duck(tmp_path: Path) -> None:
    """The session's `.duck` ends every call below half its battery, and at a third of it a goal
    walked, under the goal's own rule of 15%."""
    drive, body, _records = await _drive(tmp_path)
    duck = duck_from_goal("drive", VERBS)
    front = duck.frontmatter.model_copy(update={"abort_when": ["Battery below 50%"]})
    drive.robot.adopt(duck.model_copy(update={"frontmatter": front}))
    body._states = [DuckState(policy="mock", posture="standing", battery_percent=30)]
    goal = drive.submit("patrol the room")
    await _settled(goal)
    await drive.robot.close()
    assert body.intents_of("move") == [], f"the goal walked: {(await goal.ended).summary}"


ARM_DUCK = """---
duck: 3
name: arm-session
description: drive an arm with short segments
verbs:
  allow: [report_state, observe, manipulate, stop]
  confirm: []
success: [x]
abort_when: []
policy:
  segment_s: 1.5
---
# Task
x
"""


async def test_a_goal_leaves_the_arms_segment_length_as_the_session_set_it(
    tmp_path: Path,
) -> None:
    """A goal's own contract set its default segment length on the session's arm, and it
    outlived the goal, under the session's `manipulate` timeout for its own."""
    adapter = make_adapter(RobotSpec("lerobot", "mock", "arm"))
    event_log = EventLog()
    executor = Executor(
        registry=default_registry(),
        transport=adapter,
        contract=None,
        budget=Budget(Budgets(), now=adapter.now),
        confirm=Answered(yes=False),
        event_log=event_log,
    )
    executor.require_verdict = True
    robot = RobotSession(
        name="arm",
        transport=adapter,
        registry=executor.registry,
        executor=executor,
        heartbeat=Heartbeat(adapter, executor.abort, period_s=0.5, event_log=event_log),
    )
    robot.adopt(parse_duck_text(ARM_DUCK))
    await robot.connect()
    assert adapter.segment_s == 1.5, "control: the session's contract set the segment"
    drive = DriveSession(
        robot,
        record=lambda kind, **data: None,
        pilot=lambda goal: FakeProvider.for_duck("goal", goal),
        goals=tmp_path / "goals",
        goal_verbs=["report_state", "observe", "stop"],
    )
    goal = drive.submit("tell me what you see", by="cli", channel="pipe")
    await _settled(goal)
    after = adapter.segment_s
    await drive.close()
    await robot.close()
    assert after == 1.5


def _raises(obs: Any, step: int, history: Any) -> Any:
    raise RuntimeError("the pilot's API answered with garbage")


async def test_a_goal_whose_pilot_raises_still_ends_its_record(tmp_path: Path) -> None:
    drive, _body, records = await _drive(tmp_path)
    drive.pilot = lambda goal: FakeProvider(strategy=_raises, model="scripted:raises")
    goal = drive.submit("look around and tell me what you see")
    await _settled(goal)
    await drive.robot.close()
    assert [r["kind"] for r in records if r["kind"].startswith("goal_")] == [
        "goal_start",
        "goal_end",
    ]
    assert (await goal.ended).outcome == "error"


async def test_a_goals_verb_admitted_before_the_brake_sends_nothing_after_it(
    tmp_path: Path,
) -> None:
    """A goal's own executor, reached by the brake through its abort alone: a walk of the goal's
    that was reading the body's state as Enter was pressed twisted the body after the stop."""
    drive, body, _records = await _drive(tmp_path)
    adapter = drive.robot.transport
    real = adapter.get_state
    reading = asyncio.Event()
    answer = asyncio.Event()
    armed = {"on": True}

    async def get_state(*args: Any) -> Any:
        caller = sys._getframe(1)
        if (
            armed["on"]
            and caller.f_code.co_name == "_run_admitted"
            and caller.f_locals.get("canonical") == "move"
            and caller.f_locals.get("self") is not drive.robot.executor
        ):
            armed["on"] = False
            reading.set()
            await answer.wait()
        return await real(*args)

    adapter.get_state = get_state  # type: ignore[method-assign]
    goal = drive.submit("patrol the room")  # the scripted pilot walks first
    await asyncio.wait_for(reading.wait(), 10)
    assert body.intents_of("move") == []
    await _settled(drive.submit(""))
    await asyncio.wait_for(asyncio.gather(*drive._brakes), 10)
    after = len(body.intents)
    answer.set()
    await _settled(goal)
    await drive.robot.close()
    late = [i for i in body.intents[after:] if i.kind == "move" and any(i.params.values())]
    assert late == [], "the goal's walk twisted the body after the brake"
