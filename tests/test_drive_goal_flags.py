"""A drive session's goals flown as `quackd run` flies one: their questions put to the person
driving, a VLA pilot's lines with the session's policy seconds, and the flags given once for every
goal (`GoalSettings`)."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

import quackd.agent.loop as loop_module
from quackd.adapters.factory import RobotSpec, make_adapter
from quackd.agent.providers.base import ToolCall
from quackd.agent.providers.fake import FakeProvider
from quackd.agent.providers.vla import VlaProvider
from quackd.drive.attached import AttachedBody
from quackd.drive.run import SESSION_POLICY_S, _given_lens, session_duck
from quackd.drive.session import Answered, DriveSession, GoalSettings
from quackd.duckfile.parser import duck_from_goal
from quackd.duckfile.schema import Budgets
from quackd.log import EventLog
from quackd.mcp_server import RobotSession
from quackd.memory import RobotMemory
from quackd.safety import Budget, Executor, Heartbeat
from quackd.verbs.registry import default_registry
from tests.test_drive_session import _drive, _settled

VERBS = ["report_state", "observe", "search_scan", "go_to", "kick", "move", "stop"]


def _goal_events(goals: Path) -> list[dict[str, Any]]:
    (goal_dir,) = goals.iterdir()
    lines = (goal_dir / "transcript.jsonl").read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines]


def _verdict(word: str, reason: str) -> ToolCall:
    return ToolCall(name="assess_task", arguments={"verdict": word, "reason": reason})


class _Person:
    """The person driving, as the keyboard's asker reaches them: `answers` given in turn, then
    `then` to every question after, and None for nobody answering at all."""

    asks_a_person = True

    def __init__(self, *answers: bool, then: bool | None = False) -> None:
        self.answers = list(answers)
        self.then = then
        self.asked: list[tuple[str, str, str | None]] = []
        self.put = asyncio.Event()

    async def __call__(
        self, question: str, *, detail: str | None = None, kind: str = "drive"
    ) -> bool:
        self.asked.append((kind, question, detail))
        self.put.set()
        answer = self.answers.pop(0) if self.answers else self.then
        if answer is None:
            await asyncio.Event().wait()
        return bool(answer)

    def kinds(self) -> list[str]:
        return [kind for kind, _question, _detail in self.asked]


async def _gated(tmp_path: Path, person: Any) -> tuple[DriveSession, Any]:
    """The mock duck under a `.duck` that gates `move`, so the scripted patrol's walk asks."""
    drive, body, _records = await _drive(tmp_path)
    drive.robot.adopt(duck_from_goal("drive", VERBS, confirm=["move"]))
    drive.ask = person
    return drive, body


async def _done(drive: DriveSession) -> None:
    await drive.close()
    await drive.robot.close()


def _walked(body: Any) -> list[Any]:
    """The walks the body was sent: the patrol's `search_scan` turns in place, which is no walk."""
    return [i for i in body.intents_of("move") if i.params.get("vx")]


# ── a goal's questions reach the person driving ────────────────────────────────────────


async def test_a_goals_confirm_is_put_to_the_person_driving_and_a_no_stops_the_walk(
    tmp_path: Path,
) -> None:
    """A gated verb in a goal was answered by the standing rule, a no without --yes, whoever sat
    at the keyboard: the person who typed the goal was never asked. Now they are, in the words
    the gate's record quotes."""
    person = _Person(then=False)
    drive, body = await _gated(tmp_path, person)
    goal = drive.submit("patrol the room")
    await _settled(goal)
    await _done(drive)
    assert "confirm" in person.kinds()
    question = next(q for kind, q, _d in person.asked if kind == "confirm")
    assert question.startswith("run walk(")
    assert _walked(body) == [], "the walk went ahead on a no"
    events = _goal_events(tmp_path / "goals")
    prompts = [e for e in events if e["kind"] == "prompt"]
    assert prompts and {(p["what"], p["answer"]) for p in prompts} == {("confirm", False)}
    assert prompts[0]["question"] == question
    assert "channel" not in prompts[0], "the keyboard's rows read as a terminal's always have"
    gates = [e for e in events if e["kind"] == "gate" and e["gate"] == "confirm"]
    assert gates and {g["reason"] for g in gates} == {"a human said no"}


async def test_a_yes_from_the_person_driving_lets_the_goal_walk(tmp_path: Path) -> None:
    person = _Person(then=True)
    drive, body = await _gated(tmp_path, person)
    await _settled(drive.submit("patrol the room"))
    await _done(drive)
    assert _walked(body), "the walk never went on a yes"
    events = _goal_events(tmp_path / "goals")
    gates = [e for e in events if e["kind"] == "gate" and e["gate"] == "confirm"]
    assert gates and {g["reason"] for g in gates} == {"a human said yes"}


async def test_a_pipes_goal_keeps_the_standing_rule_and_asks_nobody(tmp_path: Path) -> None:
    """Nobody reads a pipe's questions, so its goal's gated verb is answered no, as it always
    was, and nothing is put to an asker the session happens to hold."""
    person = _Person(then=True)
    drive, body = await _gated(tmp_path, person)
    await _settled(drive.submit("patrol the room", by="cli", channel="pipe"))
    await _done(drive)
    assert person.asked == []
    assert _walked(body) == []
    events = _goal_events(tmp_path / "goals")
    assert [e for e in events if e["kind"] == "prompt"] == []
    gate = next(e for e in events if e["kind"] == "gate" and e["gate"] == "confirm")
    assert gate["reason"] == "the confirm gate was denied"


async def test_a_goals_doubt_is_put_to_the_person_driving(tmp_path: Path) -> None:
    """The pilot's doubt was left to the pilot in a session, which told it to decide for itself
    with a person right there at the keyboard. Asked now, under the pilot's own reason."""
    person = _Person(False)
    drive, body, _records = await _drive(tmp_path)
    drive.ask = person
    drive.pilot = lambda goal: FakeProvider(
        script=[
            _verdict("uncertain", "the far wall is out of frame, so the distance is a guess"),
            ToolCall(name="walk", arguments={"vx": 0.1, "duration_s": 1.0}),
        ]
    )
    goal = drive.submit("walk to the far wall")
    await _settled(goal)
    await _done(drive)
    ((kind, question, detail),) = person.asked
    assert (kind, question) == ("decide", "Go ahead anyway?")
    assert detail is not None and "out of frame" in detail
    ended = await goal.ended
    assert ended.outcome == "aborted" and "a person said no" in ended.summary
    assert body.intents_of("move") == []


async def test_yes_answers_a_goals_doubt_go_as_it_answers_a_run(tmp_path: Path) -> None:
    drive, body, _records = await _drive(tmp_path, yes=True)
    drive.pilot = lambda goal: FakeProvider(
        script=[
            _verdict("uncertain", "the far wall is out of frame"),
            ToolCall(name="walk", arguments={"vx": 0.1, "duration_s": 1.0}),
            ToolCall(name="declare_success", arguments={"reason": "walked"}),
        ]
    )
    goal = drive.submit("walk to the far wall", by="cli", channel="pipe")
    await _settled(goal)
    await _done(drive)
    assert (await goal.ended).outcome == "success"
    assess = next(e for e in _goal_events(tmp_path / "goals") if e["kind"] == "assess")
    assert assess["human"] == "go" and assess["answered_by"] == "--yes"
    assert body.intents_of("move")


async def test_a_brake_takes_a_goals_question_back_and_nobody_said_no(tmp_path: Path) -> None:
    """The goal waits for its answer while the session goes on reading lines, and a brake ends
    the goal: the question is withdrawn, the gate says nobody answered, and nothing moves."""
    person = _Person(then=None)
    drive, body = await _gated(tmp_path, person)
    goal = drive.submit("patrol the room")
    await asyncio.wait_for(person.put.wait(), 10)
    drive.submit("")
    await _settled(goal)
    await _done(drive)
    ended = await goal.ended
    assert ended.outcome == "aborted" and "the brake" in ended.summary
    assert _walked(body) == []
    events = _goal_events(tmp_path / "goals")
    assert [e for e in events if e["kind"] == "prompt"] == [], "nobody said no"
    gate = next(e for e in events if e["kind"] == "gate" and e["gate"] == "confirm")
    assert gate["answer"] is None
    assert gate["reason"] == "the prompt raised QuestionWithdrawn: the run was stopped"


# ── a VLA pilot, a line at a time ──────────────────────────────────────────────────────


async def _arm(
    tmp_path: Path, person: Any, settings: GoalSettings | None = None
) -> tuple[DriveSession, list[dict[str, Any]]]:
    """A session on the LeRobot mock, whose scripted policy answers `manipulate`, contractless
    as `run_drive` leaves one, its goals flown by the VLA pilot."""
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
    await robot.connect()
    registry = executor.registry
    robot.adopt(session_duck([v.name for v in registry.verbs() if v.safety_class != "dangerous"]))
    assert executor.budget is not None
    executor.budget.policy_total_s = SESSION_POLICY_S
    records: list[dict[str, Any]] = []
    drive = DriveSession(
        robot,
        record=lambda kind, **data: records.append({"kind": kind, **data}),
        pilot=lambda goal: VlaProvider([goal], label="goal"),
        goals=tmp_path / "goals",
        goal_verbs=["report_state", "observe", "stop"],
        ask=person,
        settings=settings or GoalSettings(vla=True, policy=True),
    )
    return drive, records


async def test_a_vla_line_is_one_instruction_the_person_driving_clears_and_judges(
    tmp_path: Path,
) -> None:
    """As `quackd run --controller vla --goal` asks at its terminal: the doubt before the first
    segment, the segment itself behind its gate, and whether the arm did it after. The seconds
    it ran are the session's too."""
    person = _Person(then=True)
    drive, _records = await _arm(tmp_path, person)
    goal = drive.submit("pick up the red cube")
    await _settled(goal)
    budget = drive.robot.executor.budget
    await _done(drive)
    assert person.kinds() == ["decide", "confirm", "judge"], person.asked
    assert person.asked[1][1] == "run manipulate(instruction='pick up the red cube')?"
    assert person.asked[2][1] == "Did the arm do it?"
    ended = await goal.ended
    assert ended.outcome == "success", ended.summary
    assert budget is not None and budget.policy_s > 0, "the segment's seconds were not charged"
    prompts = [e["what"] for e in _goal_events(tmp_path / "goals") if e["kind"] == "prompt"]
    assert prompts == ["decide", "confirm", "judge"]


async def test_a_no_from_the_person_who_watched_the_arm_fails_the_goal(tmp_path: Path) -> None:
    person = _Person(True, True, False)
    drive, _records = await _arm(tmp_path, person)
    goal = drive.submit("pick up the red cube")
    await _settled(goal)
    await _done(drive)
    ended = await goal.ended
    assert ended.outcome == "failure"
    assert "a person watched the arm and said it did not do the task" in ended.summary


async def test_a_line_that_is_no_instruction_is_refused_before_the_arm_moves(
    tmp_path: Path,
) -> None:
    person = _Person(then=True)
    drive, _records = await _arm(tmp_path, person)
    goal = drive.submit("pick up the red cube and then " * 10)
    await _settled(goal)
    await _done(drive)
    ended = await goal.ended
    assert ended.outcome == "refused"
    assert "--controller vla tells the arm's policy each line word for word" in ended.summary
    assert person.asked == [] and not (tmp_path / "goals").exists()


async def test_a_session_whose_policy_seconds_are_spent_refuses_the_next_line(
    tmp_path: Path,
) -> None:
    person = _Person(then=True)
    drive, _records = await _arm(tmp_path, person)
    budget = drive.robot.executor.budget
    assert budget is not None
    budget.policy_s = budget.policy_total_s
    goal = drive.submit("pick up the red cube")
    await _settled(goal)
    await _done(drive)
    ended = await goal.ended
    assert ended.outcome == "refused" and "policy seconds are spent" in ended.summary
    assert person.asked == []


async def test_each_goal_spends_from_what_the_session_has_left_of_its_policy_seconds(
    tmp_path: Path,
) -> None:
    """A goal's own default is two minutes of segments, and the session held to no total at all
    let goals between them run past it."""
    seen: list[float] = []

    class Watching(_Person):
        async def __call__(
            self, question: str, *, detail: str | None = None, kind: str = "drive"
        ) -> bool:
            if kind == "confirm" and drive._goal is not None:
                seen.append(drive._goal.budget.policy_total_s)
            return await super().__call__(question, detail=detail, kind=kind)

    drive, _records = await _arm(tmp_path, Watching(then=True))
    budget = drive.robot.executor.budget
    assert budget is not None
    budget.policy_s = budget.policy_total_s - 5.0
    await _settled(drive.submit("pick up the red cube"))
    await _done(drive)
    assert seen == [5.0]


# ── the flags given once, for every goal ───────────────────────────────────────────────


async def test_max_steps_is_each_goals_own_within_what_the_session_has_left(
    tmp_path: Path,
) -> None:
    drive, _body, _records = await _drive(tmp_path)
    drive.settings = GoalSettings(max_steps=3)
    await _settled(drive.submit("patrol the room", by="cli", channel="pipe"))
    budget = drive.robot.executor.budget
    assert budget is not None
    budget.steps = budget.limits.max_steps - 2
    await _settled(drive.submit("patrol the room", by="cli", channel="pipe"))
    await _done(drive)
    starts = []
    for goal_dir in sorted((tmp_path / "goals").iterdir()):
        lines = (goal_dir / "transcript.jsonl").read_text(encoding="utf-8").splitlines()
        starts.append(next(json.loads(x) for x in lines if '"run_start"' in x))
    assert [s["contract"]["budgets"]["max_steps"] for s in starts] == [3, 2]


async def test_memory_is_each_goals_to_recall_and_to_add_to(tmp_path: Path) -> None:
    """Off in drive, where a run keeps it on, and handed to every goal where it was asked for."""
    drive, _body, _records = await _drive(tmp_path)
    memory = RobotMemory("microduck:mock", tmp_path / "memory")
    drive.settings = GoalSettings(memory=memory)
    await _settled(drive.submit("patrol the room", by="cli", channel="pipe"))
    await _done(drive)
    start = next(e for e in _goal_events(tmp_path / "goals") if e["kind"] == "run_start")
    assert start["memory"] is not None and start["memory"]["robot"] == "microduck:mock"
    assert "remember" in start["tools"]
    assert memory.summary()["episodes"] == 1


async def test_without_memory_a_goal_keeps_none_as_it_always_did(tmp_path: Path) -> None:
    drive, _body, _records = await _drive(tmp_path)
    await _settled(drive.submit("patrol the room", by="cli", channel="pipe"))
    await _done(drive)
    start = next(e for e in _goal_events(tmp_path / "goals") if e["kind"] == "run_start")
    assert start["memory"] is None and "remember" not in start["tools"]
    assert start["play"] == {"on": False, "from": "task file"}


async def test_play_is_given_to_every_goal(tmp_path: Path) -> None:
    drive, _body, _records = await _drive(tmp_path)
    drive.settings = GoalSettings(play=True)
    await _settled(drive.submit("patrol the room", by="cli", channel="pipe"))
    await _done(drive)
    start = next(e for e in _goal_events(tmp_path / "goals") if e["kind"] == "run_start")
    assert start["play"] == {"on": True, "from": "--play"}


async def test_a_goal_marks_and_reads_the_sessions_own_places(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Each goal kept places of its own that died with it, so a place a person marked between
    goals was nowhere for the next goal to walk to."""
    built: list[Any] = []
    real = loop_module.AgentLoop

    class Kept(real):  # type: ignore[misc, valid-type]
        def __init__(self, cfg: Any) -> None:
            super().__init__(cfg)
            built.append(self)

    monkeypatch.setattr(loop_module, "AgentLoop", Kept)
    drive, _body, _records = await _drive(tmp_path)
    await _settled(drive.submit("patrol the room", by="cli", channel="pipe"))
    await _done(drive)
    (goal,) = built
    assert goal.executor.places is drive.robot.executor.places


def test_the_lens_is_told_to_the_body_once_by_the_session_and_never_by_a_goal() -> None:
    told: list[float] = []
    calibrated: list[float] = []

    class Body:
        def lens_given(self, fov: float) -> None:
            told.append(fov)

    class Detector:
        lens_at_connect = True

        def calibrate(self, fov: float, **_: Any) -> None:
            calibrated.append(fov)

    class Manifest:
        sensors = ["camera"]

    class Robot:
        transport = Body()
        detector = Detector()

        class executor:
            manifest = Manifest()

    _given_lens(Robot(), 62.0)
    assert (told, calibrated) == ([62.0], [62.0])
    lent = AttachedBody(Body(), Manifest())  # type: ignore[arg-type]
    assert lent.lens_given is None, "a goal would tell the session's body its lens again"


def test_the_sessions_record_names_the_policy_server_as_a_runs_does() -> None:
    from quackd.drive.run import _served

    class Arm:
        policy_served = {"server": "http://127.0.0.1:8080", "async_chunks": False}

    class Broken:
        @property
        def policy_served(self) -> Any:
            raise RuntimeError("the server went away")

    assert _served(Arm()) == {"server": "http://127.0.0.1:8080", "async_chunks": False}
    assert _served(Broken()) is None and _served(object()) is None
