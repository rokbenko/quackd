"""A drive session on a mock Microduck: lanes, one mover at a time, the brake, goals and stats."""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from quackd.adapters.factory import RobotSpec, make_adapter
from quackd.agent.decision.commands import CommandChooser, offers_for
from quackd.agent.providers.fake import FakeProvider
from quackd.drive.run import session_duck
from quackd.drive.session import Answered, DriveSession
from quackd.duckfile.parser import duck_from_goal
from quackd.duckfile.schema import Budgets
from quackd.log import EventLog, LogEvent
from quackd.mcp_server import RobotSession
from quackd.safety import Budget, Executor, Heartbeat
from quackd.transport.base import DuckState
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


async def _until(done: Callable[[], bool], within_s: float = 10.0) -> None:
    """Wait until `done()`: a bound on a wait, never a figure a test checks."""
    deadline = time.monotonic() + within_s
    while not done():
        if time.monotonic() > deadline:
            raise AssertionError("it never came")
        await asyncio.sleep(0.001)


def _hold(body: MockTransport, name: str) -> asyncio.Event:
    """Hold every `sleep` or `stop` the body is asked for until the event returned is set. A walk
    held at its first sleep is still under way when the next line comes, and one held at its stop
    has been ended and is not over yet. The mock's own clock would end a walk within a few turns
    of the loop."""
    released = asyncio.Event()
    real = getattr(body, name)

    async def held(*args: Any) -> None:
        await released.wait()
        await real(*args)

    setattr(body, name, held)
    return released


def _turned(body: MockTransport) -> list[Any]:
    return [i for i in body.intents_of("move") if i.params.get("wz")]


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
    """Without `--yes`, a gated call from a pipe is denied, since drive reads a pipe's lines as
    commands and never as answers, and the record says the line came from a pipe."""
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


async def test_two_commands_that_come_while_a_walk_is_stopped_never_both_start(
    tmp_path: Path,
) -> None:
    """Only the newest runs. "turn left" waits for the walk it ended to stop, and "step left",
    typed while it waits, drops it. Each had waited on the walk alone, so both began the moment
    it ended: two movers at once on one body."""
    drive, body, _records = await _drive(tmp_path)
    walking = _hold(body, "sleep")
    forward = drive.submit("forward")
    await _until(lambda: bool(body.intents_of("move")))
    stopping = _hold(body, "stop")
    turn = drive.submit("turn left")
    await _until(turn.routed.done)
    step = drive.submit("step left")
    await _until(step.routed.done)
    stopping.set()
    walking.set()
    await _settled(forward, turn, step)
    assert (await forward.ended).outcome == "preempted"
    assert (await turn.ended).outcome == "dropped"
    assert (await step.ended).outcome == "ok"
    assert _turned(body) == [], "the turn ran beside the step"
    await drive.robot.close()


async def test_the_brake_drops_a_command_still_waiting_its_turn(tmp_path: Path) -> None:
    """A line that came before the brake, and was waiting for the walk it had ended to stop,
    started once the walk had: after the brake, which was meant to end it too."""
    drive, body, _records = await _drive(tmp_path)
    walking = _hold(body, "sleep")
    forward = drive.submit("forward")
    await _until(lambda: bool(body.intents_of("move")))
    stopping = _hold(body, "stop")
    turn = drive.submit("turn left")
    await _until(turn.routed.done)
    braked = drive.submit("")
    stopping.set()
    walking.set()
    await _settled(forward, turn, braked)
    await asyncio.gather(*drive._brakes)
    assert (await turn.ended).outcome == "dropped"
    assert _turned(body) == [], "the turn ran after the brake"
    await drive.robot.close()


async def test_closing_the_session_ends_a_walk_still_going(tmp_path: Path) -> None:
    """A session that waited its commands out on close left the body walking for as long as the
    walk had left, after the person had quit."""
    drive, body, _records = await _drive(tmp_path)
    walking = _hold(body, "sleep")
    forward = drive.submit("forward")
    await _until(lambda: bool(body.intents_of("move")))
    await asyncio.wait_for(drive.close(), 10)
    assert (await forward.ended).outcome == "preempted"
    walking.set()
    await drive.robot.close()


async def test_a_brake_from_a_pipe_is_the_pipes_and_never_a_persons(tmp_path: Path) -> None:
    """The `stop` a brake runs for the record names whoever braked: a pipe's was run as a
    person's."""
    drive, _body, records = await _drive(tmp_path)
    event_log = drive.robot.executor.event_log
    assert event_log is not None
    stops: list[LogEvent] = []
    event_log.add(
        lambda e: stops.append(e) if e.kind == "verb_start" and e.data["name"] == "stop" else None
    )
    braked = drive.submit("stop", by="cli", channel="pipe")
    await _settled(braked)
    await asyncio.gather(*drive._brakes)
    assert [e.data["source"] for e in stops] == ["cli"]
    assert [r["by"] for r in records if r["kind"] == "brake"] == ["cli"]
    await drive.robot.close()


async def test_a_lines_latency_leaves_out_the_time_a_person_took_to_answer(
    tmp_path: Path,
) -> None:
    """The record's latency is the lane's: five seconds a person spent over "run it?" are in the
    wait to the call's start, and were in how long the lane took to read "kick left" as well."""
    clock = _Clock()
    drive, _body, _records = await _drive(tmp_path, clock=clock)
    drive.robot.executor.registry.get("kick").safety_class = "confirm"

    async def thinks(question: str) -> bool:
        clock.now += 5.0
        return True

    drive.ask = thinks
    kicked = drive.submit("kick left")
    await _settled(kicked)
    assert (await kicked.ended).outcome == "ok"
    assert (await kicked.routed).latency_ms < 1000
    assert drive.stats.block()["to_start_ms"]["exact"]["p50"] >= 5000
    await drive.robot.close()


async def test_a_line_dropped_before_it_ever_ran_still_settles(tmp_path: Path) -> None:
    """Two lines in one turn of the loop: the first is dropped before its task has taken a step,
    which then never reached the handler that settles a dropped line, and a front end waiting on
    it waited for good."""
    drive, _body, _records = await _drive(tmp_path)
    first = drive.submit("stand")
    second = drive.submit("report state")
    await _settled(first, second)
    assert (await first.ended).outcome == "dropped"
    assert (await first.ended).summary == "a newer line came first"
    assert (await second.ended).outcome == "ok"
    await drive.robot.close()


async def test_a_low_battery_ends_a_persons_command_as_it_ends_a_goals(tmp_path: Path) -> None:
    """The battery rule a goal is held to holds a session without a `.duck` too."""
    drive, body, _records = await _drive(tmp_path)
    registry = drive.robot.executor.registry
    drive.robot.adopt(
        session_duck([v.name for v in registry.verbs() if v.safety_class != "dangerous"])
    )
    body._states = [DuckState(policy="mock", posture="standing", battery_percent=1)]
    walked = drive.submit("forward")
    await _settled(walked)
    assert (await walked.ended).outcome == "aborted"
    assert body.intents_of("move") == []
    await drive.robot.close()


async def test_a_goal_keeps_the_confirm_gates_of_the_sessions_duck(tmp_path: Path) -> None:
    """A goal is a contract of its own, written from its line, and one written without the
    session's gates let the pilot run `search_scan` that the `.duck` asks a yes for."""
    drive, body, _records = await _drive(tmp_path)
    verbs = ["report_state", "observe", "search_scan", "go_to", "kick", "move", "stop"]
    drive.robot.adopt(duck_from_goal("drive", verbs, confirm=["search_scan"]))
    goal = drive.submit("look around and tell me what you see")
    await _settled(goal)
    (run,) = (tmp_path / "goals").iterdir()
    gates = [
        (e["name"], e["outcome"])
        for e in (json.loads(line) for line in (run / "transcript.jsonl").open(encoding="utf-8"))
        if e["kind"] == "gate" and e.get("gate") == "confirm"
    ]
    assert ("search_scan", "denied") in gates, gates
    assert _turned(body) == [], "the goal turned the body without the yes its .duck asks for"
    await drive.robot.close()


async def test_the_sessions_abort_ends_a_goal_it_is_running(tmp_path: Path) -> None:
    """A goal's own heartbeat watches nothing, since the session's watches the link, and a goal
    nobody told went on with its verbs after the session's heartbeat had failed."""
    drive, body, _records = await _drive(tmp_path)
    walking = _hold(body, "sleep")
    goal = drive.submit("look around and tell me what you see")
    await _until(lambda: bool(_turned(body)))
    drive.robot.executor.abort.set()
    await _settled(goal)
    walking.set()
    assert "the session was aborted" in (await goal.ended).summary
    (run,) = (tmp_path / "goals").iterdir()
    ends = [
        e
        for e in (json.loads(line) for line in (run / "transcript.jsonl").open(encoding="utf-8"))
        if e["kind"] == "run_end"
    ]
    assert "the session was aborted" in ends[0]["reason"], "the goal's own record says no why"
    await drive.robot.close()
