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
from quackd.duckfile.parser import duck_from_goal, load_duck
from quackd.duckfile.schema import Budgets
from quackd.log import EventLog
from quackd.mcp_server import RobotSession
from quackd.memory import RobotMemory
from quackd.safety import Budget, Executor, Heartbeat
from quackd.verbs.registry import default_registry
from tests.test_drive_session import _drive, _settled, _until

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
        self.labels: list[str | None] = []
        self.put = asyncio.Event()

    async def __call__(
        self,
        question: str,
        *,
        detail: str | None = None,
        kind: str = "drive",
        behind: bool = False,
        label: str | None = None,
    ) -> bool:
        self.asked.append((kind, question, detail))
        self.labels.append(label)
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
    tmp_path: Path,
    person: Any,
    settings: GoalSettings | None = None,
    *,
    duck: Path | None = None,
    yes: bool = False,
    pilot: Any = None,
) -> tuple[DriveSession, list[dict[str, Any]]]:
    """A session on the LeRobot mock, whose scripted policy answers `manipulate`, its goals
    flown by the VLA pilot unless `pilot` says otherwise. Contractless as `run_drive` leaves one,
    or under `duck`, adopted before the connect as `run_drive` adopts a `--duckfile`."""
    adapter = make_adapter(RobotSpec("lerobot", "mock", "arm"))
    event_log = EventLog()
    executor = Executor(
        registry=default_registry(),
        transport=adapter,
        contract=None,
        budget=Budget(Budgets(), now=adapter.now),
        confirm=Answered(yes=yes),
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
    if duck is not None:
        robot.adopt(load_duck(str(duck)))
    await robot.connect()
    if duck is None:
        registry = executor.registry
        robot.adopt(
            session_duck([v.name for v in registry.verbs() if v.safety_class != "dangerous"])
        )
        assert executor.budget is not None
        executor.budget.policy_total_s = SESSION_POLICY_S
    records: list[dict[str, Any]] = []
    drive = DriveSession(
        robot,
        record=lambda kind, **data: records.append({"kind": kind, **data}),
        pilot=pilot or (lambda goal: VlaProvider([goal], label="goal")),
        goals=tmp_path / "goals",
        goal_verbs=["report_state", "observe", "stop"],
        ask=person,
        yes=yes,
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
            self, question: str, *, detail: str | None = None, kind: str = "drive", **more: Any
        ) -> bool:
            if kind == "confirm" and drive._goal is not None:
                seen.append(drive._goal.budget.policy_total_s)
            return await super().__call__(question, detail=detail, kind=kind, **more)

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


async def test_a_goal_reads_the_places_the_session_keeps(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Each goal kept places of its own, empty, that died with it, so a place a person marked
    between goals was nowhere for the next goal to walk to."""
    from quackd.space.places import Place

    built: list[Any] = []
    real = loop_module.AgentLoop

    class Kept(real):  # type: ignore[misc, valid-type]
        def __init__(self, cfg: Any) -> None:
            super().__init__(cfg)
            built.append(self)

    monkeypatch.setattr(loop_module, "AgentLoop", Kept)
    drive, _body, _records = await _drive(tmp_path)
    drive.robot.executor.places.mark(Place("dock", 0.5, 0.0, 0.0, "odom-1", "odometry", 0.0))
    await _settled(drive.submit("patrol the room", by="cli", channel="pipe"))
    await _done(drive)
    (goal,) = built
    assert goal.executor.places.get("dock") is not None


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


# ── what the review of this branch found ───────────────────────────────────────────────

BOWL = "put the block in the bowl"


def _bowl(
    tmp_path: Path, *, allow: str = "manipulate, report_state, stop", policy: str = ""
) -> Path:
    """A `.duck` whose policy may be told one thing alone."""
    path = tmp_path / "bowl.duck"
    path.write_text(
        "---\nduck: 3\nname: bowl\ndescription: Only the bowl.\nverbs:\n"
        f"  allow: [{allow}]\n  confirm: [manipulate]\nsuccess: [the block is in the bowl]\n"
        f"policy:\n  instructions: [{BOWL}]\n{policy}"
        "---\nPut the block in the bowl, and nothing else.\n",
        encoding="utf-8",
    )
    return path


def _policy_runs(drive: DriveSession) -> list[str]:
    return list(drive.robot.transport.transport.policy_runs)


async def test_a_goal_tells_the_arms_policy_only_what_the_sessions_duck_lists(
    tmp_path: Path,
) -> None:
    """A goal held `manipulate` to its own contract, which lists nothing, so a session whose
    `.duck` lists one instruction let a goal's pilot tell the policy anything, unasked under
    `--yes`, where `quackd run` and a typed `manipulate` refuse every word off the list."""

    def pilot(goal: str) -> Any:
        return FakeProvider(
            script=[
                _verdict("feasible", "an arm with a policy"),
                ToolCall(name="manipulate", arguments={"instruction": "sweep the table clear"}),
                ToolCall(name="manipulate", arguments={"instruction": BOWL}),
                ToolCall(name="declare_success", arguments={"reason": "in the bowl"}),
            ]
        )

    drive, _records = await _arm(
        tmp_path, None, GoalSettings(policy=True), duck=_bowl(tmp_path), yes=True, pilot=pilot
    )
    goal = drive.submit("clear the table", by="cli", channel="pipe")
    await _settled(goal)
    await _done(drive)
    assert _policy_runs(drive) == [BOWL], "the policy was told words the .duck never listed"


async def test_a_vla_line_the_sessions_duck_does_not_list_is_refused_before_the_arm_moves(
    tmp_path: Path,
) -> None:
    person = _Person(then=True)
    drive, _records = await _arm(tmp_path, person, duck=_bowl(tmp_path))
    refused = drive.submit("wave the knife around")
    await _settled(refused)
    listed = drive.submit(BOWL)
    await _settled(listed)
    await _done(drive)
    ended = await refused.ended
    assert ended.outcome == "refused"
    assert f"bowl lists the only instructions it may be told: '{BOWL}'" in ended.summary
    assert (await listed.ended).outcome == "success"
    assert _policy_runs(drive) == [BOWL]


async def test_a_goal_typed_over_a_goal_spends_what_the_first_left(tmp_path: Path) -> None:
    """The second goal was built before the first was stopped and charged, so it was given the
    seconds the first had spent and the session ran a segment past its own total."""
    seen: list[float] = []
    confirms = {"n": 0}
    held = asyncio.Event()

    class Holding(_Person):
        async def __call__(self, question: str, **more: Any) -> bool:
            if more.get("kind") == "confirm":
                confirms["n"] += 1
                if drive._goal is not None:
                    seen.append(drive._goal.budget.policy_total_s)
                if confirms["n"] == 3:  # the first goal's third segment: nobody answers it
                    held.set()
                    await asyncio.Event().wait()
            return await super().__call__(question, **more)

    def three(goal: str) -> Any:
        return FakeProvider(
            script=[
                _verdict("feasible", "an arm"),
                *[
                    ToolCall(name="manipulate", arguments={"instruction": f"step {i}"})
                    for i in range(3)
                ],
                ToolCall(name="declare_success", arguments={"reason": "done"}),
            ]
        )

    duck = tmp_path / "arm.duck"
    duck.write_text(
        "---\nduck: 3\nname: arm\ndescription: The arm.\nverbs:\n"
        "  allow: [manipulate, report_state, stop]\n  confirm: [manipulate]\n"
        "success: [done]\npolicy:\n  segment_s: 10\n  total_s: 30\n---\nUse the arm.\n",
        encoding="utf-8",
    )
    drive, _records = await _arm(
        tmp_path, Holding(then=True), GoalSettings(policy=True), duck=duck, pilot=three
    )
    budget = drive.robot.executor.budget
    assert budget is not None
    first = drive.submit("work the arm")
    await asyncio.wait_for(held.wait(), 30)
    second = drive.submit("work the arm again")
    await asyncio.wait_for(asyncio.gather(first.ended, second.ended), 60)
    await _done(drive)
    assert seen[:2] == [30.0, 30.0], "the first goal is held to the session's total"
    assert seen[3] == 10.0, "the second was given what the first left, not the whole total"
    assert budget.policy_s <= budget.policy_total_s, (
        f"{budget.policy_s} s of {budget.policy_total_s}"
    )


async def test_a_vla_judge_taken_back_by_the_brake_ends_the_goal_for_the_brake(
    tmp_path: Path,
) -> None:
    """A question about the arm a stop took back is nobody's answer, so the goal ended as a
    failure the person never declared, and the brake was nowhere in its reason."""
    judging = asyncio.Event()

    class NeverJudges(_Person):
        async def __call__(self, question: str, **more: Any) -> bool:
            if more.get("kind") == "judge":
                judging.set()
                await asyncio.Event().wait()
            return await super().__call__(question, **more)

    drive, _records = await _arm(tmp_path, NeverJudges(then=True))
    goal = drive.submit("pick up the red cube")
    await asyncio.wait_for(judging.wait(), 20)
    drive.submit("")
    await _settled(goal)
    await _done(drive)
    ended = await goal.ended
    assert ended.outcome == "aborted" and "the brake" in ended.summary, ended
    events = _goal_events(tmp_path / "goals")
    assert not [e for e in events if e["kind"] == "declare"], "a failure nobody declared"
    assert ("judge", True) not in [
        (e["what"], e.get("answer")) for e in events if e["kind"] == "prompt"
    ]


@pytest.mark.parametrize("memory", [False, True], ids=["memory off", "memory on"])
async def test_a_goal_changes_the_kept_places_only_where_the_session_keeps_a_memory(
    tmp_path: Path, memory: bool
) -> None:
    """Off, as drive's default is, a goal's `forget_place` deleted the robot's kept place, which
    `quackd run --no-memory` could never do: what a goal marks or forgets lasts as long as it."""
    from quackd.space.places import Place, PlaceStore

    drive, _body, _records = await _drive(tmp_path)
    kept = PlaceStore("microduck:mock", tmp_path / "home")
    kept.mark(Place("kitchen", 1.0, 0.0, 0.0, "odom-1", "odometry", 0.0))
    drive.robot.executor.places = kept
    drive.goal_verbs = [*drive.goal_verbs, "forget_place"]
    drive.pilot = lambda goal: FakeProvider(
        script=[
            _verdict("feasible", "it moves nothing"),
            ToolCall(name="forget_place", arguments={"name": "kitchen"}),
            ToolCall(name="declare_success", arguments={"reason": "tidied"}),
        ]
    )
    if memory:
        drive.settings = GoalSettings(memory=RobotMemory("microduck:mock", tmp_path / "home"))
    await _settled(drive.submit("tidy up the map", by="cli", channel="pipe"))
    await _done(drive)
    assert [p.name for p in kept.places()] == ([] if memory else ["kitchen"])


async def test_a_goal_measures_through_the_sessions_lens_after_its_own_connect(
    tmp_path: Path,
) -> None:
    """Each goal recalibrates the session's detector as it connects, so one flown without the
    session's `--fov-deg` set its lens back to the body's own."""
    from quackd.perception import detector_for

    drive, _body, _records = await _drive(tmp_path)
    robot = drive.robot
    manifest = robot.executor.manifest
    assert manifest is not None
    detector = detector_for(manifest.sensors, None, fov_deg=None, backend="mock")
    assert detector is not None
    robot.detector = detector
    robot.executor.detector = detector
    _given_lens(robot, 62.0)
    drive.settings = GoalSettings(fov_deg=62.0)
    await _settled(drive.submit("patrol the room", by="cli", channel="pipe"))
    await _done(drive)
    assert getattr(detector, "fov_deg", None) == 62.0


async def test_a_y_after_a_gated_line_typed_over_a_goals_question_answers_that_line(
    tmp_path: Path,
) -> None:
    """Shown oldest first, `kick left` typed over a goal's question was queued unseen behind it,
    and the `y` the person meant for the kick cleared the goal's walk instead."""
    from quackd.drive.terminal import Asker

    said: list[str] = []
    drive, body, _records = await _drive(tmp_path)
    drive.robot.adopt(duck_from_goal("drive", VERBS, confirm=["move", "kick"]))
    drive.exact = drive._exact()
    asker = Asker(said.append)
    drive.ask = asker
    goal = drive.submit("patrol the room")
    await _until(lambda: asker.pending is not None)
    assert said[-1].startswith("ask      goal 1: run walk("), said
    kick = drive.submit("kick left")
    await _until(lambda: said[-1].startswith("ask      run kick("))
    assert asker.answers("y")
    await asyncio.wait_for(asyncio.gather(goal.ended, kick.ended), 20)
    await _done(drive)
    assert (await kick.ended).outcome == "ok", (await kick.ended).summary
    assert (await goal.ended).outcome == "aborted", "the kick took the body over from the goal"
    assert _walked(body) == [], "the y meant for the kick cleared the goal's walk"
    assert asker.pending is None


async def test_a_goals_question_never_takes_the_screen_from_one_showing() -> None:
    """A goal asks when it gets there, and a question of its own that took the screen just
    before a keystroke would take the answer meant for the one the person was reading."""
    from quackd.answers import confirm_question
    from quackd.drive.session import GoalAsker
    from quackd.drive.terminal import Asker

    said: list[str] = []
    asker = Asker(said.append)
    kick = asyncio.ensure_future(asker("run kick(leg=left)?"))
    await asyncio.sleep(0)
    confirm = GoalAsker(
        asker,
        "confirm",
        lambda name, params: (confirm_question(name, params), None),
        label="goal 1",
    )
    walk = asyncio.ensure_future(confirm("walk", {"vx": 0.12}))
    await asyncio.sleep(0)
    assert said[-1] == "ask      run kick(leg=left)? [y/N]", "the goal's took the screen"
    assert asker.answers("y") and await asyncio.wait_for(kick, 2) is True
    await asyncio.sleep(0)
    assert said[-1] == "ask      goal 1: run walk(vx=0.12)? [y/N]"
    assert asker.answers("n") and await asyncio.wait_for(walk, 2) is False


async def test_a_vla_line_typed_over_a_goal_that_spent_the_last_seconds_is_refused(
    tmp_path: Path,
) -> None:
    """Checked again once the goal it takes over has been charged: the second line passed while
    the first goal's segment was still uncharged, and started with no seconds left to run."""
    judging = asyncio.Event()

    class NeverJudges(_Person):
        async def __call__(self, question: str, **more: Any) -> bool:
            if more.get("kind") == "judge":
                judging.set()
                await asyncio.Event().wait()
            return await super().__call__(question, **more)

    duck = tmp_path / "one.duck"
    duck.write_text(
        "---\nduck: 3\nname: one\ndescription: One segment.\nverbs:\n"
        "  allow: [manipulate, report_state, stop]\n  confirm: [manipulate]\n"
        "success: [done]\npolicy:\n  segment_s: 10\n  total_s: 10\n---\nOne segment.\n",
        encoding="utf-8",
    )
    person = NeverJudges(then=True)
    drive, _records = await _arm(tmp_path, person, duck=duck)
    first = drive.submit("pick up the red cube")
    await asyncio.wait_for(judging.wait(), 20)
    asked_before = len(person.asked)
    second = drive.submit("put it in the bowl")
    await _settled(first, second)
    await _done(drive)
    ended = await second.ended
    assert ended.outcome == "refused" and "policy seconds are spent" in ended.summary, ended
    assert len(person.asked) == asked_before, "the second line asked something"
