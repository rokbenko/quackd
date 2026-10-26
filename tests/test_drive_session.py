"""A drive session on a mock Microduck: lanes, one mover at a time, the brake, goals and stats."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from quackd.adapters.factory import RobotSpec, make_adapter
from quackd.agent.decision.commands import CommandChooser, offers_for
from quackd.agent.providers.fake import FakeProvider
from quackd.drive.session import Answered, DriveSession
from quackd.duckfile.schema import Budgets
from quackd.log import EventLog, LogEvent
from quackd.mcp_server import RobotSession
from quackd.safety import Budget, Executor, Heartbeat
from quackd.transport.mock import MockTransport
from quackd.verbs.registry import default_registry


class _Clock:
    """A clock that moves a millisecond each time it is read, so every figure is exact."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        self.now += 0.001
        return self.now


class _Llm:
    """A decision LLM that answers the fast lane's one question as told, after `wait`."""

    name = "scripted"
    model = "test"
    url = None

    def __init__(self, choice: str, confidence: float = 0.99, wait: asyncio.Event | None = None):
        self.choice, self.confidence, self.wait = choice, confidence, wait
        self.calls = 0

    async def decide(self, state: Any, questions: Any) -> Any:
        self.calls += 1
        if self.wait is not None:
            await self.wait.wait()
        return {"answers": {"command": {"choice": self.choice, "confidence": self.confidence}}}


async def _drive(
    tmp_path: Path, *, llm: Any = None, yes: bool = False, clock: Any = None
) -> tuple[DriveSession, MockTransport, list[dict[str, Any]]]:
    adapter = make_adapter(RobotSpec("microduck", "mock", "duck"))
    records: list[dict[str, Any]] = []
    sessions: list[DriveSession] = []

    def observe(event: LogEvent) -> None:
        if event.kind == "intent" and sessions:
            sessions[0].saw_intent(str(event.data["intent"]), bool(event.data.get("accepted")))

    event_log = EventLog(observers=[observe])
    executor = Executor(
        registry=default_registry(),
        transport=adapter,
        contract=None,
        budget=Budget(Budgets(), now=adapter.now),
        confirm=Answered(yes=yes),
        event_log=event_log,
    )
    executor.require_verdict = True
    heartbeat = Heartbeat(adapter, executor.abort, period_s=0.5, event_log=event_log)
    robot = RobotSession(
        name="duck",
        transport=adapter,
        registry=executor.registry,
        executor=executor,
        heartbeat=heartbeat,
    )
    await robot.connect()
    chooser = None
    if llm is not None:
        verbs = {n: executor.registry.get(n) for n in executor.registry.names()}
        from quackd.drive.presets import presets_for

        chooser = CommandChooser(llm, offers_for(verbs, presets_for(executor.manifest, verbs)), "")
    drive = DriveSession(
        robot,
        record=lambda kind, **data: records.append({"kind": kind, **data}),
        chooser=chooser,
        pilot=lambda goal: FakeProvider.for_duck("goal", goal),
        goals=tmp_path / "goals",
        goal_verbs=["report_state", "observe", "search_scan", "go_to", "kick", "move", "stop"],
        yes=yes,
        **({"clock": clock} if clock is not None else {}),
    )
    sessions.append(drive)
    return drive, adapter.transport, records  # type: ignore[return-value]


async def _settled(*commands: Any) -> None:
    await asyncio.wait_for(asyncio.gather(*(c.ended for c in commands)), 30)


async def test_a_line_that_names_a_call_runs_it_and_asks_no_decision_llm(tmp_path: Path) -> None:
    """A person's "forward" is the verdict for its own call and needs nobody's interpretation."""
    llm = _Llm("stand")
    drive, _body, _records = await _drive(tmp_path, llm=llm)
    walked = drive.submit("forward")
    await _settled(walked)
    assert (await walked.routed).lane == "exact" and (await walked.ended).outcome == "ok"
    assert _body.intents_of("move"), "the walk never reached the body"
    assert llm.calls == 0
    assert drive.robot.executor.verdict is None, "a person's call recorded a verdict"
    await drive.robot.close()


async def test_a_newer_command_stops_the_walk_before_its_own_first_twist(tmp_path: Path) -> None:
    drive, body, _records = await _drive(tmp_path)
    forward = drive.submit("forward")
    for _ in range(1000):
        if len(body.intents_of("move")) >= 2:
            break
        await asyncio.sleep(0)
    turn = drive.submit("turn left")
    await _settled(forward, turn)
    assert (await forward.ended).outcome == "preempted"
    assert (await turn.ended).outcome == "ok"
    kinds = [(i.kind, i.params.get("wz", 0)) for i in body.intents]
    first_turn = next(k for k, (kind, wz) in enumerate(kinds) if kind == "move" and wz)
    assert ("stop", 0) in kinds[:first_turn], "the walk was not stopped before the turn began"
    last_walk = max(k for k, (kind, wz) in enumerate(kinds[:first_turn]) if kind == "move")
    assert any(kind == "stop" for kind, _wz in kinds[last_walk:first_turn])
    await drive.robot.close()


async def test_a_fast_answer_that_comes_after_the_brake_moves_nothing(tmp_path: Path) -> None:
    held = asyncio.Event()
    drive, body, records = await _drive(tmp_path, llm=_Llm("forward", wait=held))
    asked = drive.submit("go on ahead")
    await asyncio.sleep(0)
    braked = drive.submit("")
    held.set()
    await _settled(asked, braked)
    assert (await asked.ended).outcome == "dropped"
    await asyncio.gather(*drive._brakes)
    assert body.intents_of("move") == [], "the answer that came after the brake moved the body"
    assert any(r["kind"] == "brake" for r in records)
    await drive.robot.close()


async def test_a_goal_runs_on_the_sessions_body_and_keeps_its_own_record(tmp_path: Path) -> None:
    """The pilot takes a line that is more than one call, as a whole run of its own under the
    session's `goals/`, on the body the session holds, and the session's own executor is never
    given a verdict by it."""
    drive, body, records = await _drive(tmp_path)
    goal = drive.submit("look around and tell me what you see")
    await _settled(goal)
    routed = await goal.routed
    assert routed.lane == "pilot" and routed.label == "goal 1"
    (run,) = (tmp_path / "goals").iterdir()
    assert run.name.startswith("01-look-around") and (run / "transcript.jsonl").exists()
    assert [r["kind"] for r in records if r["kind"].startswith("goal_")] == [
        "goal_start",
        "goal_end",
    ]
    assert body.connected and drive.robot.executor.verdict is None
    await drive.robot.close()


async def test_a_pipe_is_never_a_person_and_its_confirm_is_answered_no(tmp_path: Path) -> None:
    """Without `--yes`, a gated call from a pipe is denied, as `quackd run` denies one, and the
    record says the line came from a pipe."""
    drive, _body, records = await _drive(tmp_path)
    drive.robot.executor.registry.get("kick").safety_class = "confirm"
    kicked = drive.submit("kick left", by="cli", channel="pipe")
    await _settled(kicked)
    assert (await kicked.ended).outcome == "refused"
    assert {
        "kind": "command",
        "n": 1,
        "text": "kick left",
        "by": "cli",
        "channel": "pipe",
    } in records
    await drive.robot.close()


async def test_the_waits_are_counted_per_lane_off_the_sessions_own_clock(tmp_path: Path) -> None:
    drive, _body, _records = await _drive(tmp_path, clock=_Clock())
    for line in ("stand", "report state"):
        await _settled(drive.submit(line))
    block = drive.stats.block()
    assert block["lanes"] == {"exact": 2}
    assert block["to_start_ms"]["exact"]["n"] == 2
    assert block["outcomes"] == {"ok": 2}
    await drive.robot.close()
