"""`run_pilot_team`: N agent loops on wall-clock time, and what happens when one of them
does not come back.

Everything here runs on `mock` backends, which is what a pilot team has ever run on.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from roboglot.adapters.factory import RobotSpec, describe
from roboglot.agent.providers.base import Exchange, ProviderTurn, ToolCall, Usage
from roboglot.agent.providers.fake import FakeProvider
from roboglot.agent.transcript import Transcript
from roboglot.glotfile.parser import load_glot, parse_glot_text
from roboglot.memory import RobotMemory
from roboglot.team import pilots
from roboglot.team.pilots import (
    MAX_MEMBERS,
    SpecEntry,
    aggregate_outcome,
    roster_from_specs,
    run_pilot_team,
    trim_contract,
    union_problems,
)
from roboglot.transport.base import RobotState
from roboglot_lerobot.mock import REST
from roboglot_lerobot.verbs import at_rest, rest_goal
from roboglot_open_duck import OpenDuckAdapter
from roboglot_open_duck.mock import OpenDuckMock
from tests.support import until_async

MIXED = {
    "duck": RobotSpec("microduck", "mock", "duck"),
    "arm": RobotSpec("lerobot", "mock", "arm"),
}


def _roster(specs: dict[str, RobotSpec] | None = None) -> dict[str, SpecEntry]:
    return roster_from_specs(specs or MIXED)


def _providers(roster: dict[str, Any], task_name: str = "team-hello") -> dict[str, Any]:
    # one instance per member: a shared strategy closure would be shared state
    return {name: FakeProvider.for_glot(task_name) for name in roster}


async def _run(tmp_path: Path, roster: dict[str, Any] | None = None, **kw: Any) -> Any:
    roster = roster if roster is not None else _roster()
    return await asyncio.wait_for(
        run_pilot_team(
            load_glot("team-hello"),
            roster,
            providers=kw.pop("providers", None) or _providers(roster),
            runs_dir=tmp_path,
            **kw,
        ),
        timeout=120,
    )


def _summary(result: Any) -> dict[str, Any]:
    return json.loads((result.run_dir / "summary.json").read_text(encoding="utf-8"))


def _team_lines(result: Any) -> list[dict[str, Any]]:
    text = (result.run_dir / "team.jsonl").read_text(encoding="utf-8")
    return [json.loads(line) for line in text.splitlines() if line.strip()]


async def test_a_pilot_team_takes_the_name_you_gave_the_run(tmp_path: Path) -> None:
    """`--run-name` is for the afternoon somebody runs a hundred examples on one bench, and a
    team is as much a run as a solo pilot is: it gets the same label on the same directory and
    the name as typed in the same summary field."""
    result = await _run(tmp_path, run_name="Bench 7")
    assert result.run_dir.name.endswith("-team-hello-bench-7"), result.run_dir.name
    assert _summary(result)["run_name"] == "Bench 7"


async def test_a_pilot_team_without_a_name_is_named_as_it_always_was(tmp_path: Path) -> None:
    """Every team recorded before there was a flag for it, and every one that does not ask,
    keeps exactly the directory name it had."""
    result = await _run(tmp_path)
    assert result.run_dir.name.endswith("-team-hello"), result.run_dir.name
    assert _summary(result)["run_name"] is None


async def test_a_pilot_team_adds_up_what_its_members_cost(tmp_path: Path) -> None:
    """The team sums money where it already sums tokens. Its members are scripted here, so
    each of them is free and the total is a real zero rather than a missing number."""
    result = await _run(tmp_path)
    summary = _summary(result)
    assert summary["cost_usd"] == 0.0
    assert result.cost_usd == 0.0
    per_member = summary["per_member"]
    assert all(m["cost_usd"] == 0.0 for m in per_member.values())
    assert sum(m["cost_usd"] for m in per_member.values()) == summary["cost_usd"]


async def test_the_price_you_passed_reaches_every_member(tmp_path: Path) -> None:
    """`--price` is validated on `roboglot run` whether or not there is a team, so a team that
    quietly ignored it would be a flag that checks your typing and then does nothing. Each
    member is costed at the one rate: it is a rate for the run, not one per robot."""
    result = await _run(tmp_path, price="in=1000000,out=0")
    summary = _summary(result)
    for member in summary["per_member"].values():
        assert member["cost_usd"] == member["usage"]["input_tokens"], (
            "a million dollars per million input tokens makes the bill the token count, "
            "which is the cheapest way to prove the rate arrived rather than a default"
        )
    assert summary["cost_usd"] == sum(m["cost_usd"] for m in summary["per_member"].values())


async def test_one_unpriced_member_makes_the_whole_team_bill_unpriced(tmp_path: Path) -> None:
    """A total that quietly left a robot out would read as a cheaper team rather than an
    incomplete one, so one member Roboglot cannot price makes the team figure None. The member
    is made unpriceable the way a real one would be: a real vendor name and an LLM id no
    catalogue has ever listed."""
    roster = _roster()
    providers = _providers(roster)
    first = next(iter(providers.values()))
    first.name = "openai"
    first.llm_id = "an-llm-no-catalogue-lists"
    result = await _run(tmp_path, roster=roster, providers=providers)
    summary = _summary(result)
    assert summary["cost_usd"] is None
    assert result.cost_usd is None
    assert any(m["cost_usd"] is None for m in summary["per_member"].values())


# ── the contract, per body ──────────────────────────────────────────────────────────────


def test_each_member_is_handed_the_half_of_the_contract_its_body_can_answer_for() -> None:
    fm = load_glot("team-hello").frontmatter
    duck = trim_contract(fm, describe(MIXED["duck"]))
    arm = trim_contract(fm, describe(MIXED["arm"]))
    assert "say" in duck.verbs.allow and "say" not in arm.verbs.allow
    assert "move_joints" in arm.verbs.allow and "move_joints" not in duck.verbs.allow
    assert "report_state" in duck.verbs.allow and "report_state" in arm.verbs.allow
    assert duck.requires == ["report_state"] and arm.requires == ["report_state"]
    assert fm.verbs.allow == load_glot("team-hello").frontmatter.verbs.allow, "the file is not"


def test_a_trimmed_contract_is_still_a_contract() -> None:
    """Rebuilt through the model, so every cross-field rule in the schema still holds."""
    text = (
        "---\nglot: 1\nname: t\ndescription: d\nverbs:\n  allow: [stop, say, move_joints]\n"
        "  confirm: [move_joints]\nsuccess: [x]\nrequires: [say]\n---\n# Task\nx\n"
    )
    fm = parse_glot_text(text).frontmatter
    duck = trim_contract(fm, describe(MIXED["duck"]))
    assert duck.verbs.confirm == [], "a gated verb this body lacks is not gated on it"
    assert duck.requires == ["say"]
    arm = trim_contract(fm, describe(MIXED["arm"]))
    assert arm.verbs.confirm == ["move_joints"] and arm.requires == []


def test_a_body_that_can_do_none_of_it_still_gets_stop() -> None:
    text = (
        "---\nglot: 1\nname: t\ndescription: d\nverbs:\n  allow: [move_joints]\n"
        "success: [x]\n---\n# Task\nx\n"
    )
    trimmed = trim_contract(parse_glot_text(text).frontmatter, describe(MIXED["duck"]))
    assert trimmed.verbs.allow == ["stop"]


def test_what_no_body_can_do_is_refused_and_what_one_can_is_not() -> None:
    text = (
        "---\nglot: 1\nname: t\ndescription: d\nverbs:\n  allow: [stop, kick, move_joints]\n"
        "success: [x]\nrequires: [kick]\n---\n# Task\nx\n"
    )
    manifests = {n: describe(s) for n, s in MIXED.items()}
    assert union_problems(parse_glot_text(text), manifests) == []
    nobody = text.replace("requires: [kick]", "requires: [kick, move_joints]").replace(
        "allow: [stop, kick, move_joints]", "allow: [stop, kick, move_joints, grab]"
    )
    assert union_problems(parse_glot_text(nobody), manifests) == []
    lacking = (
        "---\nglot: 1\nname: t\ndescription: d\nverbs:\n  allow: [stop, pick]\n"
        "success: [x]\nrequires: [pick]\n---\n# Task\nx\n"
    )
    only_duck = {"duck": manifests["duck"]}
    assert "requires pick" in union_problems(parse_glot_text(lacking), only_duck)[0]


# ── the outcome ─────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("members", "outcome"),
    [
        ({"a": ("success", "done"), "b": ("success", "done")}, "success"),
        ({"a": ("success", "done"), "b": ("failure", "no")}, "failure"),
        ({"a": ("budget", "out"), "b": ("failure", "no")}, "budget"),
        ({"a": ("infeasible", "too heavy"), "b": ("budget", "out")}, "infeasible"),
        ({"a": ("error", "boom"), "b": ("infeasible", "too heavy")}, "error"),
        # one member raised and the rest were stopped because it did: the cause is the headline
        ({"a": ("aborted", "stopped"), "b": ("error", "boom")}, "error"),
        # a person pressed something: nothing errored and everything aborted
        ({"a": ("aborted", "stopped"), "b": ("aborted", "stopped")}, "aborted"),
        ({}, "error"),
    ],
)
def test_the_worst_outcome_wins(members: dict[str, Any], outcome: str) -> None:
    assert aggregate_outcome(members)[0] == outcome


def test_the_reason_names_every_member_that_did_not_succeed_worst_first() -> None:
    _, reason = aggregate_outcome(
        {"a": ("failure", "no ball"), "b": ("success", "done"), "c": ("aborted", "stopped")}
    )
    assert reason == "c aborted: stopped; a failure: no ball"
    assert not reason.startswith("b "), "a member that succeeded is not in the reason"


def test_the_member_that_stopped_the_team_is_named_before_the_members_it_stopped() -> None:
    """A person's no ends that member `aborted`, as every stop it causes ends the others, so the
    reason named them in the roster's order and could open on a member that was only stopped. The
    member whose own end stopped the team comes first of those that ended alike; worse ones still
    come before it."""
    stopped = "stopped by the team: duck ended aborted: nobody watched"
    _, reason = aggregate_outcome(
        {"buddy": ("aborted", stopped), "duck": ("aborted", "nobody watched")}, first="duck"
    )
    assert reason == f"duck aborted: nobody watched; buddy aborted: {stopped}", reason
    _, reason = aggregate_outcome(
        {"arm": ("error", "boom"), "buddy": ("aborted", stopped), "duck": ("aborted", "no")},
        first="duck",
    )
    assert reason == f"arm error: boom; duck aborted: no; buddy aborted: {stopped}", reason


def test_success_names_everyone() -> None:
    outcome, reason = aggregate_outcome({"a": ("success", "x"), "b": ("success", "y")})
    assert outcome == "success" and reason == "every member declared success: a, b"


# ── a whole run ─────────────────────────────────────────────────────────────────────────


async def test_two_different_bodies_talk_and_both_declare(tmp_path: Path) -> None:
    result = await _run(tmp_path)
    assert result.outcome == "success", result.reason
    assert result.messages == 2, "each said one thing"
    assert result.notices == 2, "and the runner said when each of them ended"
    assert {r["outcome"] for r in result.per_member.values()} == {"success"}
    assert result.per_member["arm"]["robot"] == "lerobot:mock"


async def test_two_bodies_of_one_kind_are_a_team_too(tmp_path: Path) -> None:
    """Same body, twice."""
    pair = _roster(
        {
            "duck-a": RobotSpec("microduck", "mock", "duck-a"),
            "duck-b": RobotSpec("microduck", "mock", "duck-b"),
        }
    )
    result = await _run(tmp_path, pair)
    assert result.outcome == "success", result.reason
    assert list(result.per_member) == ["duck-a", "duck-b"]


AFTER_HELLO_S = 0.1
"""How long after the duck says hello the arm starts connecting. A scripted check on a mock
body takes a few milliseconds, so with no wait between checks the duck spends all three of
them inside this and gives up on an arm that is about to answer."""


async def test_a_member_that_connects_late_is_still_heard(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CI's macOS runners failed two team tests with "nobody answered after 3 checks": one
    member had told the team and spent all of the scripted pilot's checks before a slower peer
    had said anything. An arm that connects AFTER_HELLO_S after the duck's hello does that on
    any machine, and the scripted pilot's checks wait on the wall clock now, so the duck still
    hears the arm."""
    from roboglot.adapters import factory
    from roboglot.team.bus import InProcessBus

    hello = asyncio.Event()

    def bus_factory(tap: Any) -> InProcessBus:
        bus = InProcessBus(tap=tap)
        publish = bus.publish

        def spied(msg: Any) -> None:
            publish(msg)
            if getattr(msg, "src", None) == "duck":
                hello.set()

        monkeypatch.setattr(bus, "publish", spied)
        return bus

    real = factory.make_adapter

    def late(spec: Any, **kw: Any) -> Any:
        adapter = real(spec, **kw)
        if spec.name == "arm":
            connect = adapter.connect

            async def after_hello(*args: Any, **kwargs: Any) -> Any:
                await asyncio.wait_for(hello.wait(), timeout=30)
                await asyncio.sleep(AFTER_HELLO_S)
                return await connect(*args, **kwargs)

            monkeypatch.setattr(adapter, "connect", after_hello)
        return adapter

    monkeypatch.setattr("roboglot.team.pilots.make_adapter", late)
    result = await _run(tmp_path, bus_factory=bus_factory)
    assert result.outcome == "success", result.reason
    assert result.per_member["duck"]["reason"].endswith("heard back from arm")


async def test_the_artifacts_are_what_the_docs_say(tmp_path: Path) -> None:
    result = await _run(tmp_path)
    kinds = [line["kind"] for line in _team_lines(result)]
    assert kinds[0] == "team_start" and kinds[-1] == "team_end"
    assert kinds.count("member_end") == 2
    talks = [
        line["msg"] for line in _team_lines(result) if line.get("msg", {}).get("kind") == "TALK"
    ]
    assert len(talks) == 4, "two between the members, two from the runner"
    assert all("t" in line for line in _team_lines(result)), "every line carries its time"
    for name in ("duck", "arm"):
        member = result.run_dir / "robots" / name
        assert (member / "transcript.jsonl").exists()
        assert not (member / "summary.json").exists(), "a member is not a solo run"
    summary = _summary(result)
    assert summary["team"] == {"members": ["duck", "arm"], "method": "pilots", "name": None}
    assert summary["transport"] == "mock"
    assert summary["messages"] == 2 and summary["notices"] == 2
    assert set(summary["per_member"]["duck"]) == {
        "outcome",
        "reason",
        "steps",
        "llm_calls",
        "usage",
        # what this member cost and how long it took, beside the tokens it already reported
        "cost_usd",
        "wall_s",
        "llm_latency_s",
        "provider",
        "llm_id",
        "robot",
        "run_dir",
        "log_dropped",
    }
    start = _team_lines(result)[0]
    assert start["clock"] == "wall"
    assert start["contracts"]["arm"]["allow"] == ["report_state", "observe", "stop", "move_joints"]


async def test_a_mixed_team_reports_its_backends_as_mixed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every body a test can build offline is a `mock`, so the duck is named on its real
    backend and built over the core's mock: the summary reads the backends the roster names."""
    from roboglot.adapters import factory

    real = factory.make_adapter

    def on_a_mock(spec: Any, **kw: Any) -> Any:
        if spec.key == "microduck:jsonrpc":
            spec = RobotSpec(spec.adapter, "mock", spec.name)
        return real(spec, **kw)

    monkeypatch.setattr("roboglot.team.pilots.make_adapter", on_a_mock)
    roster = _roster(
        {
            "duck": RobotSpec("microduck", "jsonrpc", "duck"),
            "arm": RobotSpec("lerobot", "mock", "arm"),
        }
    )
    assert _summary(await _run(tmp_path, roster))["transport"] == "mixed"


async def test_the_runners_notice_reaches_the_survivors(tmp_path: Path) -> None:
    """A pilot waiting on somebody who has stopped has to be told, or it waits forever."""
    result = await _run(tmp_path)
    heard = [
        line["msg"]
        for line in _team_lines(result)
        if line.get("msg", {}).get("kind") == "TALK" and line["msg"]["src"] == "team"
    ]
    assert len(heard) == 2
    assert all("declared success" in m["text"] for m in heard)


# ── when one member does not come back ──────────────────────────────────────────────────


class _Boom:
    """A transport that will not connect."""

    name = "microduck"
    backend = "mock"

    async def connect(self) -> None:
        raise RuntimeError("no socket")

    async def close(self) -> None:
        return None

    def now(self) -> float:
        return 0.0


async def test_the_first_exception_stops_the_others_and_names_itself(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from roboglot.adapters import factory

    real = factory.make_adapter

    def maybe_boom(spec: Any, **kw: Any) -> Any:
        return _Boom() if spec.name == "arm" else real(spec, **kw)

    monkeypatch.setattr(factory, "make_adapter", maybe_boom)
    monkeypatch.setattr("roboglot.team.pilots.make_adapter", maybe_boom)
    result = await _run(tmp_path)
    assert result.outcome == "error", result.reason
    assert result.per_member["arm"]["outcome"] == "error"
    assert "no socket" in result.per_member["arm"]["reason"]
    assert "arm" in result.reason
    others = [n for n in result.per_member if n != "arm"]
    assert all(result.per_member[n]["outcome"] in ("aborted", "success") for n in others)
    assert (result.run_dir / "robots" / "arm" / "transcript.jsonl").exists()


async def test_a_member_the_teams_stop_cut_before_its_first_step_reads_aborted_in_the_team(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The arm's connect fails while the duck's first state read is out, and the team's stop cuts
    that read (`AgentLoop._read_state`), which ends the duck before its first step by raising
    `Aborted`. Its own record said `aborted`, and the team's said `error`, `Aborted: stopped by
    the team: ...`, so the team's reason opened on the duck, which was only stopped. The team
    says what the member's own record says now, and the reason opens on the arm, on a task none
    of whose members can walk as on any other (the refuter's test, held to the newer record)."""
    from roboglot.adapters import factory

    real = factory.make_adapter
    reading = asyncio.Event()

    def build(spec: Any, **kwargs: Any) -> Any:
        adapter = real(spec, **kwargs)
        if spec.name == "duck":
            first = adapter.get_state
            calls = {"n": 0}

            async def quiet() -> Any:
                calls["n"] += 1
                if calls["n"] == 1:
                    reading.set()
                    await asyncio.Event().wait()  # a link gone quiet: ended only by the stop
                return await first()

            adapter.get_state = quiet
        if spec.name == "arm":

            async def refused() -> Any:
                await reading.wait()
                raise RuntimeError("no socket")

            adapter.connect = refused
        return adapter

    monkeypatch.setattr("roboglot.team.pilots.make_adapter", build)
    result = await _run(tmp_path)
    own = Transcript.read(result.run_dir / "robots" / "duck" / "transcript.jsonl")[-1]
    stopped = "stopped by the team: arm raised RuntimeError: no socket"
    assert (own["kind"], own["outcome"], own["reason"]) == ("run_end", "aborted", stopped), own
    duck = result.per_member["duck"]
    assert (duck["outcome"], duck["reason"]) == ("aborted", stopped), duck
    assert result.per_member["arm"]["outcome"] == "error"
    assert result.reason.startswith("arm error: RuntimeError: no socket"), result.reason


async def test_a_provider_that_raises_is_that_members_error(tmp_path: Path) -> None:
    class _Angry:
        name, llm_id, supports_vision = "angry", "none", False

        async def step(self, system: str, history: list[Exchange], tools: list[Any]) -> Any:
            raise RuntimeError("429 from the vendor")

    roster = _roster()
    providers: dict[str, Any] = _providers(roster)
    providers["arm"] = _Angry()
    result = await _run(tmp_path, roster, providers=providers, price="in=1000000,out=0")
    assert result.outcome == "error"
    assert "429 from the vendor" in result.per_member["arm"]["reason"]
    # A member that raised never reaches its own `return RunResult(...)`, and the team used
    # to throw away the wall clock, the LLM seconds and the bill that member had already
    # measured. A team of priced LLMs then read `cost_usd: null` on the strength of one of
    # them being interrupted, which is a bill going missing rather than a bill being unknown.
    for name in ("duck", "arm"):
        member = result.per_member[name]
        assert member["cost_usd"] is not None, f"{name} measured a bill before it stopped"
        assert member["wall_s"] is not None and member["llm_latency_s"] is not None
    assert result.cost_usd is not None


async def test_the_kill_switch_reaches_every_member(tmp_path: Path) -> None:
    """One event, every executor: Ctrl-C stops every body, not the one in front.

    A pilot mid-verb is cancelled and sent a stop; one waiting on its LLM notices at its
    next turn, exactly as a solo run does."""

    roster = _roster()
    master = asyncio.Event()
    master.set()  # already pressed: mock bodies finish faster than any sleep could race
    result = await _run(
        tmp_path,
        roster,
        providers={n: FakeProvider(strategy=_always_report) for n in roster},
        abort=master,
    )
    assert result.outcome == "aborted", result.reason
    assert {r["outcome"] for r in result.per_member.values()} == {"aborted"}
    assert "kill switch" in result.reason
    assert all(r["steps"] == 0 for r in result.per_member.values()), "nothing moved"


# ── a body that cannot see a fall ───────────────────────────────────────────────────────

BLIND_TASK = (
    "---\nglot: 0\nname: blind\ndescription: d\nverbs:\n"
    "  allow: [move, report_state, stop]\nsuccess: [x]\n---\n# Task\nx\n"
)
"""A task that can make a body walk, which is the whole of what turns the warning on."""

WALK = {"vx": 0.1, "wz": 0.0, "duration_s": 0.5}

NOBODY_WATCHED = "nobody confirmed they were watching a robot that cannot see a fall"
"""The reason a solo run ends on when nobody said they watch (`AgentLoop.run`)."""


class _FallBlindDuck(OpenDuckMock):
    """The Open Duck as its bridge backend reports itself, as tests/test_loop.py's stand-in:
    `fall_detection` a constant False, and no `stand_up` in its manifest."""

    async def get_state(self) -> RobotState:
        state = await super().get_state()
        return state.model_copy(update={"extras": {**state.extras, "fall_detection": False}})


def _walker() -> FakeProvider:
    """A pilot that says the task suits it, walks twice and declares."""
    return FakeProvider(
        script=[
            ToolCall(name="assess_task", arguments={"verdict": "feasible", "reason": "flat"}),
            ToolCall(name="move", arguments=dict(WALK)),
            ToolCall(name="move", arguments=dict(WALK)),
            ToolCall(name="declare_success", arguments={"reason": "walked"}),
        ]
    )


class _Person:
    """Somebody answering the team's questions, an answer a question in turn, who notes whom they
    were asked about and how many questions were up at once. They answer from a coroutine, as a
    person on a pipe does, so the event loop goes on while a question is up and a body nothing
    holds moves meanwhile. `before(member)` is awaited first; an answer that is an exception is
    raised, as a prompt nobody answered raises."""

    asks_a_person = True

    def __init__(self, *answers: bool | BaseException, before: Any = None) -> None:
        self.answers = list(answers)
        self.before = before
        self.asked: list[str] = []
        self.most = 0
        self._up = 0

    async def __call__(self, member: str, why: str) -> bool:
        self._up += 1
        self.most = max(self.most, self._up)
        self.asked.append(member)
        try:
            if self.before is not None:
                await self.before(member)
            answer = self.answers.pop(0)
            if isinstance(answer, BaseException):
                raise answer
            return answer
        finally:
            self._up -= 1


class _BlindTeam:
    """Two Open Ducks that cannot see a fall, `d1` and `d2`, and `buddy`, a Microduck, which can,
    each with a pilot that walks. What every member's view saw, and which members have reached the
    team's start and wait there (`_Together.hold`, spied on)."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from roboglot.adapters import factory

        real = factory.make_adapter
        self.ducks = {name: _FallBlindDuck() for name in ("d1", "d2")}
        self.events: list[tuple[str, str]] = []
        self.held: set[str] = set()

        def build(spec: Any, **kwargs: Any) -> Any:
            if spec.name in self.ducks:
                return OpenDuckAdapter(self.ducks[spec.name])
            return real(spec, **kwargs)

        hold = pilots._Together.hold

        async def spied(together: Any, name: str, abort: asyncio.Event) -> None:
            self.held.add(name)
            await hold(together, name, abort)

        monkeypatch.setattr("roboglot.team.pilots.make_adapter", build)
        monkeypatch.setattr(pilots._Together, "hold", spied)

    def moved(self) -> list[str]:
        """Every member whose body has moved or that has started a verb."""
        walked = [name for name, duck in self.ducks.items() if (duck.x, duck.y) != (0.0, 0.0)]
        return walked + [name for name, kind in self.events if kind == "verb_start"]

    def view(self, name: str) -> Any:
        return lambda event: self.events.append((name, str(event.kind)))

    async def run(
        self, tmp_path: Path, order: tuple[str, ...] = ("d1", "d2", "buddy"), **kw: Any
    ) -> Any:
        """The team's run, its roster in `order`."""
        roster = _roster(
            {
                name: RobotSpec("microduck" if name == "buddy" else "open_duck", "mock", name)
                for name in order
            }
        )
        return await asyncio.wait_for(
            run_pilot_team(
                parse_glot_text(BLIND_TASK),
                roster,
                providers={name: _walker() for name in roster},
                runs_dir=tmp_path,
                view=self.view,
                **kw,
            ),
            timeout=120,
        )


def _rows(result: Any, member: str) -> list[dict[str, Any]]:
    return Transcript.read(result.run_dir / "robots" / member / "transcript.jsonl")


def _asked(rows: list[dict[str, Any]]) -> list[tuple[str, bool]]:
    return [(row["what"], row["answer"]) for row in rows if row["kind"] == "prompt"]


async def test_no_body_moves_until_every_member_that_cannot_see_a_fall_has_said_yes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A team handed every member `acknowledge=None`, so a duck that cannot see a fall walked after
    a note where a solo run asks first; and asked at each member's own start, the question was up
    while the other members moved, so the `q` meant to stop them went into its answer. Now each
    such duck is asked, one at a time, and no body moves, the one that can see a fall included,
    until the last of them has said yes. Before each answer the person waits until the members
    already answered and the Microduck are waiting at the team's start, or one of them has moved,
    and notes what had moved by then."""
    team = _BlindTeam(monkeypatch)
    seen: dict[str, list[str]] = {}
    answered: list[str] = []

    async def before(member: str) -> None:
        others = {"buddy", *answered}
        await until_async(
            lambda: others <= team.held or team.moved(), what=f"{others} at the team's start"
        )
        seen[member] = team.moved()
        answered.append(member)

    person = _Person(True, True, before=before)
    result = await team.run(tmp_path, acknowledge=person)
    assert seen == {"d1": [], "d2": []}, f"what had moved while each question was up: {seen}"
    assert sorted(person.asked) == ["d1", "d2"] and person.most == 1, (person.asked, person.most)
    assert result.outcome == "success", result.reason
    assert all(team.ducks[name].x != 0.0 for name in ("d1", "d2")), "and then both walked"
    for name in ("d1", "d2"):
        assert _asked(_rows(result, name)) == [("acknowledge", True)], name
    assert _asked(_rows(result, "buddy")) == []


@pytest.mark.parametrize(
    ("answer", "why"),
    [(False, NOBODY_WATCHED), (EOFError(), f"{NOBODY_WATCHED}: the question went unanswered")],
    ids=["no", "unanswered"],
)
async def test_a_no_ends_the_team_before_any_body_moved_and_every_record_says_why(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, answer: bool | BaseException, why: str
) -> None:
    """A no to the first question, or a question nobody answered, ends the team before any body
    moved: no other question is put, the member asked ends as a solo run on its body ends, and
    every other member ends on the stop that names it. The team records the member's `aborted`,
    as its own record says, where it wrote `error` for any member that raised."""
    team = _BlindTeam(monkeypatch)

    async def before(_member: str) -> None:
        await until_async(
            lambda: "buddy" in team.held or team.moved(), what="the buddy at the team's start"
        )

    person = _Person(answer, before=before)
    result = await team.run(tmp_path, acknowledge=person)
    assert team.moved() == [] and len(person.asked) == 1, (team.moved(), person.asked)
    (asked,) = person.asked
    own = _rows(result, asked)[-1]
    assert own["kind"] == "run_end" and own["outcome"] == "aborted", own
    assert own["reason"].startswith(why), own["reason"]
    assert _asked(_rows(result, asked)) == ([("acknowledge", False)] if answer is False else [])
    for name in result.per_member:
        end = _rows(result, name)[-1]
        assert end["kind"] == "run_end" and end["outcome"] == "aborted", (name, end)
        assert result.per_member[name]["outcome"] == "aborted", (name, result.per_member)
        if name != asked:
            assert _asked(_rows(result, name)) == [], name
            assert end["reason"] == f"stopped by the team: {asked} ended aborted: {own['reason']}"
    assert result.per_member[asked]["reason"] == own["reason"]
    assert result.outcome == "aborted", result.reason


async def test_a_no_names_the_member_that_said_it_first_in_the_teams_reason(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With the Microduck listed first, the team's reason after a no opened on its `stopped by
    the team`, and the member whose no it was came after the members it had stopped, because all
    of them end `aborted` and the roster broke the tie. It opens on the member that said no now,
    whatever the roster's order (the refuter's test, on the team that waits)."""
    team = _BlindTeam(monkeypatch)
    person = _Person(False)
    result = await team.run(tmp_path, order=("buddy", "d2", "d1"), acknowledge=person)
    (asked,) = person.asked
    assert result.reason.startswith(f"{asked} aborted: {NOBODY_WATCHED}"), result.reason


async def test_a_stop_while_a_question_is_up_ends_the_team_before_any_body_moved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The kill switch pressed while the first question waits for its answer takes it back and
    ends every member on the switch, before any body moved, with no question answered and none
    recorded."""
    team = _BlindTeam(monkeypatch)
    master = asyncio.Event()

    async def press(_member: str) -> None:
        master.set()
        await asyncio.Event().wait()  # the person never answers: the press takes it back

    result = await team.run(tmp_path, acknowledge=_Person(True, before=press), abort=master)
    assert team.moved() == []
    assert result.outcome == "aborted" and "kill switch" in result.reason, result.reason
    for name in result.per_member:
        assert result.per_member[name]["outcome"] == "aborted", result.per_member
        assert _asked(_rows(result, name)) == [], name


@pytest.mark.parametrize("yes", [False, True], ids=["neither", "yes"])
async def test_with_nobody_to_ask_the_team_ends_before_any_body_moved_whatever_yes_says(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, yes: bool
) -> None:
    """A caller that hands the team nobody to ask (`acknowledge`) has said nobody watches, and a
    member whose body cannot see a fall ends the team before anything moved, with no question
    recorded, as a solo run that nobody answers ends. `yes` is the team's standing answer to its
    confirm gates and its pilots' doubts, and it answered this question too: `roboglot run --yes`
    hands in an answer of its own (`tests/test_cli_pilots.py`)."""
    team = _BlindTeam(monkeypatch)
    result = await team.run(tmp_path, yes=yes)
    assert team.moved() == [], team.moved()
    assert result.outcome == "aborted", result.reason
    assert NOBODY_WATCHED in result.reason, result.reason
    for name in result.per_member:
        assert _asked(_rows(result, name)) == [], name


# ── the flags ───────────────────────────────────────────────────────────────────────────


async def test_dry_run_reaches_every_member(tmp_path: Path) -> None:
    result = await _run(tmp_path, dry_run=True)
    assert _summary(result)["dry_run"] is True
    assert result.outcome == "success", result.reason


async def test_max_steps_applies_to_each_member(tmp_path: Path) -> None:
    roster = _roster()
    providers = {n: FakeProvider(strategy=_always_report) for n in roster}
    result = await _run(tmp_path, roster, providers=providers, max_steps=1)
    assert result.outcome == "budget", result.reason
    assert all(r["steps"] <= 1 for r in result.per_member.values())


def _always_report(obs: Any, step: int, history: list[Exchange]) -> ToolCall:
    return ToolCall(name="report_state", arguments={})


async def test_memory_is_keyed_by_the_name_or_the_body_and_can_be_off(tmp_path: Path) -> None:
    roster = _roster()
    memories = {name: RobotMemory(name, tmp_path / "mem") for name in roster}
    await _run(tmp_path, roster, memories=memories)
    assert (tmp_path / "mem" / "duck.jsonl").exists()
    assert (tmp_path / "mem" / "arm.jsonl").exists()
    off = tmp_path / "mem2"
    await _run(tmp_path, _roster(), memories=None)
    assert not off.exists()


async def test_a_team_of_one_or_of_nine_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="2 to 8 members"):
        await _run(tmp_path, _roster({"duck": MIXED["duck"]}))
    big = {f"duck-{i}": RobotSpec("microduck", "mock", f"duck-{i}") for i in range(MAX_MEMBERS + 1)}
    with pytest.raises(ValueError, match="2 to 8 members"):
        await _run(tmp_path, _roster(big))


async def test_a_member_with_no_pilot_is_refused(tmp_path: Path) -> None:
    roster = _roster()
    with pytest.raises(ValueError, match="no pilot for arm"):
        await _run(tmp_path, roster, providers={"duck": FakeProvider()})


async def test_a_task_no_body_can_do_refuses_before_anything_connects(tmp_path: Path) -> None:
    """Two arms asked to kick. Neither can, so nobody connects and nothing is written."""
    text = (
        "---\nglot: 1\nname: impossible\ndescription: d\nverbs:\n"
        "  allow: [stop, kick]\nsuccess: [x]\nrequires: [kick]\n"
        "team:\n  members: [left, right]\n  allocation:\n    method: pilots\n"
        "---\n# Task\nx\n"
    )
    roster = _roster(
        {
            "left": RobotSpec("lerobot", "mock", "left"),
            "right": RobotSpec("lerobot", "mock", "right"),
        }
    )
    with pytest.raises(ValueError, match="requires kick"):
        await asyncio.wait_for(
            run_pilot_team(
                parse_glot_text(text), roster, providers=_providers(roster), runs_dir=tmp_path
            ),
            timeout=30,
        )
    # checked off the event loop: the point is that the directory is empty, not when
    made = await asyncio.to_thread(lambda: list(tmp_path.glob("*team*")))
    assert not made, "nothing on disk to explain away"


# ── the views ───────────────────────────────────────────────────────────────────────────


async def test_each_member_gets_its_own_view_and_the_team_gets_one(tmp_path: Path) -> None:
    seen: dict[str, list[str]] = {}

    def view(name: str) -> Any:
        seen.setdefault(name, [])
        return lambda event: seen[name].append(event.kind)

    await _run(tmp_path, view=view)
    assert set(seen) == {"duck", "arm", "team"}
    assert "run_start" in seen["duck"] and "run_start" not in seen["team"]
    assert seen["team"] == ["talk", "talk"], "the team view carries the runner's own notices"


def test_usage_is_summed_across_the_members(tmp_path: Path) -> None:
    class _Costly:
        name, llm_id, supports_vision = "costly", "none", False

        async def step(self, system: str, history: list[Exchange], tools: list[Any]) -> Any:
            return ProviderTurn(
                tool_calls=[ToolCall(id="x", name="declare_success", arguments={"reason": "ok"})],
                usage=Usage(input_tokens=10, output_tokens=1),
                stop_reason="tool_use",
            )

    roster = _roster()
    result = asyncio.run(
        asyncio.wait_for(
            run_pilot_team(
                load_glot("team-hello"),
                roster,
                providers={n: _Costly() for n in roster},
                runs_dir=tmp_path,
            ),
            timeout=60,
        )
    )
    assert result.usage.input_tokens == 20 and result.usage.output_tokens == 2
    assert result.llm_calls == 2


async def test_a_cancelled_team_records_every_member_rather_than_crashing(
    tmp_path: Path,
) -> None:
    """The second Ctrl-C: `asyncio.run` cancels the task. A member that never returned a
    result used to leave a `KeyError` where the interrupt should have been."""

    class _Slow:
        name, llm_id, supports_vision = "slow", "none", False

        async def step(self, system: str, history: list[Exchange], tools: list[Any]) -> Any:
            await asyncio.sleep(30)
            raise AssertionError("never")

    roster = _roster()
    task = asyncio.ensure_future(_run(tmp_path, roster, providers={n: _Slow() for n in roster}))
    await asyncio.sleep(0.4)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    # the record survives the interrupt, which is the whole point of writing it first
    run_dir = await asyncio.to_thread(lambda: sorted(tmp_path.glob("*team-hello"))[-1])
    summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
    assert summary["outcome"] == "aborted"
    assert set(summary["per_member"]) == set(roster)
    assert all(m["outcome"] == "aborted" for m in summary["per_member"].values())


@pytest.mark.parametrize("fails", ["the bus's start", "the links", "a member's loop"])
async def test_a_team_that_fails_to_start_leaves_nothing_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fails: str
) -> None:
    """`team.jsonl` was opened and the bus started before the `try` whose `finally` closes both,
    so a team that raised in between, a broker that refused it or a member that could not be
    built, left the file open, every member's transcript built so far with it, and an MQTT
    bus's network thread running for the rest of the process. All of it is closed now, and the
    failure reaches the caller as it was raised."""
    from roboglot.team.bus import InProcessBus

    teams: list[Any] = []
    loops: list[Any] = []
    buses: list[Any] = []

    class Kept(pilots.TeamTranscript):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            teams.append(self)

    class Started(InProcessBus):
        closed = False

        def start(self) -> None:
            if fails == "the bus's start":
                raise ConnectionRefusedError("the broker refused")

        def close(self) -> None:
            self.closed = True

    def factory(tap: Any) -> Started:
        buses.append(Started(tap=tap))
        return buses[-1]

    def no_links(*_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError("no links")

    real = pilots.AgentLoop

    def one_loop(cfg: Any) -> Any:
        if loops:
            raise RuntimeError("the second member could not be built")
        loops.append(real(cfg))
        return loops[-1]

    monkeypatch.setattr(pilots, "TeamTranscript", Kept)
    if fails == "the links":
        monkeypatch.setattr(pilots, "make_links", no_links)
    if fails == "a member's loop":
        monkeypatch.setattr(pilots, "AgentLoop", one_loop)
    with pytest.raises((ConnectionRefusedError, RuntimeError)) as raised:
        await _run(tmp_path, bus_factory=factory)
    assert str(raised.value) in (
        "the broker refused",
        "no links",
        "the second member could not be built",
    )
    assert [team._fh.closed for team in teams] == [True], "team.jsonl was left open"
    assert [bus.closed for bus in buses] == [True], "the bus was left running"
    assert all(loop.transcript._fh.closed for loop in loops), "a member's transcript was left open"


# ── the rest pose ───────────────────────────────────────────────────────────────────────

ARM_REST = {
    "shoulder_pan": 12.0,
    "shoulder_lift": -80.0,
    "elbow_flex": 78.0,
    "wrist_flex": 6.0,
    "wrist_roll": 0.0,
}


def _built(monkeypatch: pytest.MonkeyPatch, member: str, **kwargs: Any) -> dict[str, Any]:
    """Keep every adapter a run builds, and hand `member`'s `make()` some extra kwargs.

    The same seam the connect failure above uses: `run_pilot_team` builds its adapters
    itself, from a roster that carries no rest pose, so this is where one is put on one
    member's body and where the object is caught to read afterwards."""
    from roboglot.adapters import factory

    real = factory.make_adapter
    built: dict[str, Any] = {}

    def capture(spec: Any, **kw: Any) -> Any:
        built[spec.name] = real(spec, **(kw | kwargs)) if spec.name == member else real(spec, **kw)
        return built[spec.name]

    monkeypatch.setattr("roboglot.team.pilots.make_adapter", capture)
    return built


async def test_a_member_with_a_rest_pose_is_put_down_before_its_torque_is_released(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A LeRobot arm goes limp the moment it is disconnected, so a member that ended with
    its arm in the air dropped it on the table. The move belongs between the stop, which
    holds the arm where it is, and the close, which is what lets go: that window is the only
    one in which putting the arm down changes whether it falls.

    Each member tears itself down, so this has to hold for every one of them and not only
    for a solo run."""
    built = _built(monkeypatch, "arm", rest_pose=ARM_REST)
    result = await _run(tmp_path)
    assert result.outcome == "success", result.reason

    arm = built["arm"].transport
    # the first `rest` is the start of the run, so a member acts from the same arm every
    # time; the second is the teardown, in the window the docstring names
    assert arm.sequence == ["rest", "stop", "rest", "close"], arm.sequence
    # one move, sent a tick at a time under the step cap, and the stop's hold where it ended:
    # nothing was sent anywhere but the rest pose, to within the move's tolerance
    assert arm.actions, "the arm was never moved"
    for sent in arm.actions:
        assert at_rest({joint: ARM_REST[joint] for joint in sent}, sent), sent
    assert at_rest(rest_goal(ARM_REST), arm.joints), f"it really went there: {arm.joints}"
    assert arm.torque is False, "an arm at its rest pose may be let go of"
    assert arm.close_note is None, "so there is nothing to warn about"


async def test_a_dry_run_never_moves_the_arm_to_its_rest_pose(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--dry-run` promises that nothing reaches the body. The rest move is sent outside the
    executor, which is where the dry-run gate lives, so it has to refuse for itself."""
    built = _built(monkeypatch, "arm", rest_pose=ARM_REST)
    result = await _run(tmp_path, dry_run=True)
    assert result.outcome == "success", result.reason

    arm = built["arm"].transport
    assert "rest" not in arm.sequence, arm.sequence
    # the teardown's stop still holds the arm where it stands, which writes the present
    # position as the goal, drives nothing anywhere, and leaves the gripper out of it
    held = {j: v for j, v in REST.items() if j != "gripper"}
    assert all(a == held for a in arm.actions), arm.actions
    assert arm.joints["shoulder_lift"] == -90.0, "still where the mock was built"
