"""The executor is the layer that does not trust the LLM. Every rule gets a test."""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import sys
import threading
import time
from typing import Any

import pytest
from PIL import Image
from pydantic import BaseModel, ConfigDict

from roboglot import safety
from roboglot.adapters.factory import ADAPTER_NAMES, make_adapter
from roboglot.adapters.manifest import RobotManifest
from roboglot.glotfile.parser import parse_glot_text
from roboglot.glotfile.schema import Budgets, GlotFile
from roboglot.log import EventLog
from roboglot.perception.base import Detection
from roboglot.safety import (
    ENDING_S,
    PAUSE_ENDED,
    Aborted,
    Budget,
    BudgetExceeded,
    ConfirmDenied,
    Executor,
    Heartbeat,
    Preempted,
    QuestionTakenBack,
    SafetyStop,
    VerbNotAllowed,
    VerdictRequired,
    allow_all,
    deny_all,
)
from roboglot.transport.base import (
    DEFAULT_CAMERA_NAME,
    WENT_OUT,
    Ack,
    CameraFrame,
    HeartbeatError,
    Intent,
    RobotState,
    TransportError,
)
from roboglot.transport.mock import MockTransport
from roboglot.verbs.core import CORE, MOVE_RESEND_S
from roboglot.verbs.registry import (
    STOP_ENDED,
    STOPPED,
    NoParams,
    Verb,
    VerbContext,
    VerbRegistry,
    VerbResult,
    registry_from_manifest,
)
from roboglot.verdict import Verdict


def glot(allow: str, confirm: str = "", abort: str = "") -> GlotFile:
    return parse_glot_text(
        f"""---
glot: 0
name: t
description: d
verbs:
  allow: [{allow}]
  confirm: [{confirm}]
success: [x]
abort_when: [{abort}]
---
# Task
x
"""
    )


async def test_allowlist_is_enforced(registry: VerbRegistry, mock_transport: MockTransport) -> None:
    ex = Executor(registry, mock_transport, contract=glot("quack, walk").frontmatter)
    with pytest.raises(VerbNotAllowed):
        await ex.run_verb("kick")
    assert mock_transport.intents == []
    result = await ex.run_verb("quack", {"text": "hi"})
    assert result.ok and mock_transport.intents_of("sound")


async def test_stop_is_always_allowed(
    registry: VerbRegistry, mock_transport: MockTransport
) -> None:
    ex = Executor(registry, mock_transport, contract=glot("quack").frontmatter)
    assert (await ex.run_verb("stop")).ok
    assert mock_transport.stops == 1


async def test_confirm_gate(registry: VerbRegistry, mock_transport: MockTransport) -> None:
    fm = glot("quack, kick", confirm="kick").frontmatter
    ex = Executor(registry, mock_transport, contract=fm, confirm=deny_all)
    with pytest.raises(ConfirmDenied):
        await ex.run_verb("kick")
    assert mock_transport.intents_of("do") == []
    asked: list[tuple[str, dict]] = []

    def yes(name: str, params: dict) -> bool:
        asked.append((name, params))
        return True

    ex.confirm = yes
    assert (await ex.run_verb("kick", {"leg": "left"})).ok
    assert asked == [("kick", {"leg": "left"})]
    assert mock_transport.intents_of("do")[0].params == {"skill": "kick_left"}


class _Person:
    """A person at a terminal who says yes, and lets `meanwhile` happen while the question is
    open, as a second MCP call, a peer's pause or the run's clock would."""

    asks_a_person = True

    def __init__(self, meanwhile: Any = None) -> None:
        self.asked: list[str] = []
        self.meanwhile = meanwhile

    async def __call__(self, name: str, _params: dict[str, Any]) -> bool:
        self.asked.append(name)
        if self.meanwhile is not None:
            await self.meanwhile()
        return True


class _Instruction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    instruction: str


async def _one_segment(_ctx: VerbContext, _p: _Instruction) -> VerbResult:
    return VerbResult.success("the segment ran", seconds=1.0)


def _segment_verb() -> Verb:
    """A stand-in for an arm's `manipulate`, which is always confirmed."""
    return Verb(
        "manipulate",
        "a segment of a learned policy",
        _one_segment,
        _Instruction,
        safety_class="confirm",
    )


@pytest.mark.parametrize("spent", ["steps", "policy seconds", "a segment running"])
async def test_nobody_is_asked_about_a_call_a_later_gate_would_refuse(
    registry: VerbRegistry, mock_transport: MockTransport, spent: str
) -> None:
    """The confirm question came before the segment gate and the budget, so a person was asked,
    said yes, and the call was then refused: the record kept a `prompt` row and an allowed
    `confirm` gate for a verb that never ran. Everything that can refuse with what is known
    before the question is checked first, so the refusal here is the only gate written, and
    nobody is asked anything."""
    seen: list[Any] = []
    person = _Person()
    budget = Budget(Budgets(max_steps=1), now=mock_transport.now)
    registry.register(_segment_verb(), replace=True)
    ex = Executor(
        registry,
        mock_transport,
        contract=glot("kick", confirm="kick").frontmatter if spent == "steps" else None,
        budget=budget,
        confirm=person,
        event_log=EventLog(observers=[seen.append]),
    )
    segment = {"instruction": "pick up the red block"}
    if spent == "steps":
        budget.note_step()  # the last step is spent
        with pytest.raises(BudgetExceeded):
            await ex.run_verb("kick", {"leg": "right"})
    elif spent == "policy seconds":
        budget.policy_s = budget.policy_total_s
        with pytest.raises(BudgetExceeded):
            await ex.run_verb("manipulate", segment)
    else:
        ex.segment = "manipulate"  # a first segment runs, as a second MCP call finds it
        with pytest.raises(VerbNotAllowed):
            await ex.run_verb("manipulate", segment)
    assert person.asked == [], f"asked about {person.asked}, and then refused it"
    refused = ("segment", "refused") if spent == "a segment running" else ("budget", "exceeded")
    assert gates(seen) == [refused], gates(seen)
    assert not [e for e in seen if e.kind == "prompt"], "a prompt row for a call never put"
    assert mock_transport.intents == []


async def _spend_the_last_step(ex: Executor) -> None:
    assert ex.budget is not None
    while ex.budget.steps < ex.budget.limits.max_steps:
        ex.budget.note_step()  # as other calls would, each its own task over MCP


async def _run_out_of_minutes(ex: Executor) -> None:
    assert ex.budget is not None
    await ex.transport.sleep(ex.budget.limits.max_minutes * 60 + 1)


async def _grant_a_pause(ex: Executor) -> None:
    ex.pause_for(5.0)


async def _a_newer_command(ex: Executor) -> None:
    ex.preempt("a newer command came")


async def _abort(ex: Executor) -> None:
    ex.abort.set()


async def _withdraw_the_verdict(ex: Executor) -> None:
    ex.verdict = None


async def _brake(ex: Executor) -> None:
    await ex.run_verb("stop")


@pytest.mark.parametrize(
    ("meanwhile", "gate", "raised"),
    [
        (_spend_the_last_step, ("budget", "exceeded"), BudgetExceeded),
        (_run_out_of_minutes, ("budget", "exceeded"), BudgetExceeded),
        (_grant_a_pause, ("paused", "refused"), VerbNotAllowed),
        (_a_newer_command, ("preempted", "refused"), Preempted),
        (_abort, ("abort", "refused"), Aborted),
        (_withdraw_the_verdict, ("verdict", "refused"), VerdictRequired),
        (_brake, ("stopped", "refused"), None),
    ],
    ids=["steps", "minutes", "pause", "newer command", "abort", "verdict", "stop"],
)
async def test_a_refusal_after_a_yes_says_the_person_had_agreed(
    registry: VerbRegistry,
    mock_transport: MockTransport,
    meanwhile: Any,
    gate: tuple[str, str],
    raised: type[BaseException] | None,
) -> None:
    """What can change while the question is open is read again after the answer: a step a
    second call took, the run's minutes, a pause, a newer command, the abort, a verdict withdrawn
    or a stop. A call refused there was agreed to first, and its gate says so beside its own
    reason, which is the same sentence it always was, so a reader of the record does not take
    the yes in the `confirm` row above it for a verb that ran."""
    seen: list[Any] = []
    budget = Budget(Budgets(max_steps=5, max_minutes=0.1), now=mock_transport.now)
    budget.start()
    ex = Executor(
        registry,
        mock_transport,
        contract=glot("kick", confirm="kick").frontmatter,
        budget=budget,
        event_log=EventLog(observers=[seen.append]),
        require_verdict=True,
        verdict=Verdict(verdict="feasible", reason="a kick is what this body does"),
    )
    person = _Person(lambda: meanwhile(ex))
    ex.confirm = person
    if raised is None:
        result = await ex.run_verb("kick", {"leg": "right"})
        assert not result.ok and result.summary == f"kick did not start: {STOPPED}"
    else:
        with pytest.raises(raised):
            await ex.run_verb("kick", {"leg": "right"})
    assert person.asked == ["kick"]
    kicks = [e.data for e in seen if e.kind == "gate" and e.data["name"] == "kick"]
    assert [(g["gate"], g["outcome"]) for g in kicks] == [("confirm", "allowed"), gate]
    assert kicks[-1]["after_confirm"] == "a human said yes", kicks[-1]
    assert "after_confirm" not in kicks[0]
    assert mock_transport.intents_of("do") == [], "the kick went out"


def _not_fallen(state: RobotState) -> str | None:
    return "the body has fallen" if state.fallen else None


class _ReadFails(MockTransport):
    """A body whose state read raises, as a link that dropped does."""

    async def get_state(self) -> RobotState:
        raise TransportError("the body did not answer")


def _kick_held_to(registry: VerbRegistry, *preconditions: Any) -> None:
    registry.register(
        dataclasses.replace(registry.get("kick"), preconditions=list(preconditions)), replace=True
    )


STANDING = RobotState(policy="mock", posture="standing", battery_percent=88)
FALLEN = RobotState(policy="mock", posture="fallen", fallen=True, battery_percent=88)


@pytest.mark.parametrize("why", ["a precondition", "the battery", "the state read"])
async def test_a_call_the_bodys_state_would_refuse_puts_no_question(
    registry: VerbRegistry, why: str
) -> None:
    """The preconditions and the battery rule read the body's state, which was read only after
    the confirm question, so a person was asked and said yes about a kick a fallen body or a
    flat battery then refused, and the record kept a question for a verb that never ran. A call
    that will put a question reads the state first, and what refuses on that reading refuses with
    no question, no `prompt` row and no `confirm` gate. A read that fails fails the call as a
    failed read always has, with the stop sent after it, and nobody is asked."""
    seen: list[Any] = []
    person = _Person()
    transport = {
        "a precondition": MockTransport(states=[FALLEN]),
        "the battery": MockTransport(states=[STANDING.model_copy(update={"battery_percent": 10})]),
        "the state read": _ReadFails(),
    }[why]
    _kick_held_to(registry, _not_fallen)
    ex = Executor(
        registry,
        transport,
        contract=glot("kick", confirm="kick", abort="Battery below 15%").frontmatter,
        confirm=person,
        event_log=EventLog(observers=[seen.append]),
    )
    if why == "a precondition":
        result = await ex.run_verb("kick", {"leg": "right"})
        assert not result.ok and result.summary == "cannot kick: the body has fallen"
    elif why == "the battery":
        with pytest.raises(Aborted, match="battery 10% below 15%"):
            await ex.run_verb("kick", {"leg": "right"})
    else:
        with pytest.raises(TransportError, match="the body did not answer"):
            await ex.run_verb("kick", {"leg": "right"})
        assert transport.stops == 1, "the stop a failed read is owed did not go out"
    assert person.asked == [], f"asked about {person.asked}, and then refused it"
    assert not [e for e in seen if e.kind == "prompt"], "a prompt row for a call never put"
    expected = {
        "a precondition": [("precondition", "refused")],
        "the battery": [("abort_when", "fired")],
        "the state read": [],
    }[why]
    assert gates(seen) == expected, gates(seen)
    assert transport.intents_of("do") == []


async def test_a_precondition_that_turns_false_while_the_question_is_open_refuses_after_it(
    registry: VerbRegistry,
) -> None:
    """The state is read again after the answer, and what the question's wait changed refuses
    there: the body stood when the question was put and had fallen by the yes. The refusal says
    the person had agreed (`after_confirm`), and the kick is not sent. A call that puts a question
    reads the state twice, once before it and once after."""
    seen: list[Any] = []
    person = _Person()
    transport = MockTransport(states=[STANDING, FALLEN])
    _kick_held_to(registry, _not_fallen)
    ex = Executor(
        registry,
        transport,
        contract=glot("kick", confirm="kick").frontmatter,
        confirm=person,
        event_log=EventLog(observers=[seen.append]),
    )
    result = await ex.run_verb("kick", {"leg": "right"})
    assert not result.ok and result.summary == "cannot kick: the body has fallen"
    assert person.asked == ["kick"]
    assert gates(seen) == [("confirm", "allowed"), ("precondition", "refused")]
    refused = [e.data for e in seen if e.kind == "gate"][-1]
    assert refused["after_confirm"] == "a human said yes", refused
    assert transport.intents_of("do") == []
    assert transport._state_i == 2, "a confirmed call reads the state before and after the question"


class _SecondReadFails(MockTransport):
    """A body whose link drops while the person answers: its second state read raises."""

    def __init__(self) -> None:
        super().__init__(states=[STANDING])
        self.reads = 0

    async def get_state(self) -> RobotState:
        self.reads += 1
        if self.reads == 2:
            raise TransportError("the link dropped while the person answered")
        return await super().get_state()


async def test_a_call_that_ends_on_a_failed_read_after_its_yes_says_it_was_agreed_to(
    registry: VerbRegistry,
) -> None:
    """After the yes, the second state read raised: the stop it owes went out and the call ended
    on the read's error, and the record kept an allowed confirm for a verb that never ran, with
    no row after it saying the person had agreed. The call's end says so now (`after_confirm` on
    its `verb_end`), as every refusal after a yes does. A call that ran after its yes ends as it
    always did."""
    seen: list[Any] = []
    person = _Person()
    transport = _SecondReadFails()
    ex = Executor(
        registry,
        transport,
        contract=glot("kick", confirm="kick").frontmatter,
        confirm=person,
        event_log=EventLog(observers=[seen.append]),
    )
    with pytest.raises(TransportError, match="the link dropped"):
        await ex.run_verb("kick", {"leg": "right"})
    assert person.asked == ["kick"] and transport.stops == 1
    assert gates(seen) == [("confirm", "allowed")]
    (ended,) = ends(seen)
    assert ended["outcome"] == "error" and ended["after_confirm"] == "a human said yes", ended
    assert transport.intents_of("do") == []
    ran = await ex.run_verb("kick", {"leg": "right"})  # the third read answers
    assert ran.ok and "after_confirm" not in ends(seen)[-1], ends(seen)[-1]


class _DeadLink(MockTransport):
    """A link that has gone: every read raises, and a stop is sent and never answered."""

    async def get_state(self) -> RobotState:
        raise TransportError("no answer from the robot")

    async def stop(self) -> None:
        await asyncio.Event().wait()


async def test_what_a_probe_sets_off_for_the_body_is_on_the_record_as_a_calls_is(
    registry: VerbRegistry,
) -> None:
    """A probe (`refusal_before_the_question`) writes none of a call's rows, which a context
    variable marks, and a task started under it kept the mark: the stop a probe's failed read
    owes, and the keeper that sees it out once its allowance is spent, ran marked, so the
    keeper's line as the run ends, that the stop was never answered and was cancelled, never
    reached the record, where the same stop owed by a real call is recorded. The mark covers the
    probe's own checks and nothing it starts."""
    registry.register(dataclasses.replace(CORE["stop"], timeout_s=ENDING_S), replace=True)
    seen: list[Any] = []
    ex = Executor(
        registry,
        _DeadLink(),
        contract=glot("kick", confirm="kick").frontmatter,
        event_log=EventLog(observers=[seen.append]),
    )
    refusal = await ex.refusal_before_the_question("kick", {"leg": "right"}, source="person")
    assert refusal is not None and "no answer from the robot" in refusal, refusal
    await asyncio.sleep(0)  # the keeper takes its first step, as it has long before a close
    keepers = [t for t in asyncio.all_tasks() if t.get_coro().__name__ == "_see_out"]  # type: ignore[union-attr]
    assert keepers, "precondition: no stop was left to go on by itself"
    for keeper in keepers:
        keeper.cancel()  # as the loop's close cancels it
    # The wait ends as soon as the keepers do. Its bound is only there so that a keeper that
    # never ends fails the test, and it is generous because a slow runner is not a failure.
    await asyncio.wait(set(keepers), timeout=STUBBORN_S)
    notes = [str(e.data.get("text")) for e in seen if e.kind == "note"]
    assert any("still on its way as the run ended" in note for note in notes), notes
    assert not [e for e in seen if e.kind in ("gate", "verb_start", "verb_end")], "a call's rows"


async def test_budget_hard_stop(registry: VerbRegistry, mock_transport: MockTransport) -> None:
    budget = Budget(Budgets(max_steps=2), now=mock_transport.now)
    budget.start()
    ex = Executor(registry, mock_transport, contract=glot("quack").frontmatter, budget=budget)
    await ex.run_verb("quack")
    await ex.run_verb("quack")
    with pytest.raises(BudgetExceeded):
        await ex.run_verb("quack")
    assert budget.steps == 2


async def test_the_llm_call_budget_is_read_only_where_a_call_is_charged(
    registry: VerbRegistry, mock_transport: MockTransport
) -> None:
    """A verb's step ran the whole budget check, `max_llm_calls` included, so the verb the last
    allowed LLM call chose was refused with `max_llm_calls (2) reached`, billed and for nothing,
    and so was a stepper's turn after it, which charges no call at all. Steps and minutes are
    what a verb is held to; the calls are read where one is about to be charged."""
    budget = Budget(Budgets(max_llm_calls=2), now=mock_transport.now)
    budget.start()
    ex = Executor(registry, mock_transport, contract=glot("quack").frontmatter, budget=budget)
    budget.note_llm_call()
    budget.note_llm_call()  # the last call allowed, which chose the quack below
    assert (await ex.run_verb("quack", {"text": "hi"})).ok
    budget.note_stepper_call()  # a turn the stepper answered
    assert (await ex.run_verb("quack", {"text": "again"})).ok
    with pytest.raises(BudgetExceeded, match=r"^max_llm_calls \(2\) reached$"):
        budget.note_llm_call()
    assert (budget.steps, budget.llm_calls, budget.stepper_calls) == (2, 2, 1)
    assert len(mock_transport.intents_of("sound")) == 2


async def test_budget_minutes_uses_transport_clock(mock_transport: MockTransport) -> None:
    budget = Budget(Budgets(max_minutes=0.1), now=mock_transport.now)
    budget.start()
    budget.check()
    await mock_transport.sleep(7)
    with pytest.raises(BudgetExceeded):
        budget.check()


async def test_dry_run_sends_nothing(registry: VerbRegistry, mock_transport: MockTransport) -> None:
    ex = Executor(
        registry, mock_transport, contract=glot("walk, get_frame").frontmatter, dry_run=True
    )
    result = await ex.run_verb("walk", {"vx": 0.2})
    assert result.ok and result.data.get("dry_run") is True
    assert mock_transport.intents == []
    frame = await ex.run_verb("get_frame")  # read-only verbs still run
    assert frame.ok and "frame captured" in frame.summary


async def _says_it_only_reads(ctx: VerbContext, _p: NoParams) -> VerbResult:
    """A third party's verb flagged `read_only` that reads, and then sends all the same."""
    state = await ctx.transport.get_state()
    move = await ctx.transport.send_intent(Intent.move(0.1, 0.0, 0.0))
    brake = await ctx.transport.send_intent(Intent.stop())
    await ctx.transport.stop()
    return VerbResult.success(
        f"read {state.posture}, move accepted={move.accepted}, stop accepted={brake.accepted}"
    )


@pytest.mark.parametrize("name", ["move_joints", "gripper", "wave_arm"])
async def test_a_dry_run_sends_nothing_whatever_a_verb_says_of_itself(
    mock_transport: MockTransport, name: str
) -> None:
    """A dry run connects the real body and let any verb flagged `read_only` run for real, so a
    third party's verb that carried the flag and sent an intent moved the body in a run that
    promised to send nothing, and the close then took the run for one that had moved nothing.
    The flag is the verb's own word, and a check keyed on names would still let an unknown one
    through (`wave_arm`). Nothing a verb sends reaches the body in a dry run now, an intent, a
    stop intent or a stop: each is refused on its way to the transport and recorded, and the
    verb's result is a failure that says a dry run sends nothing. Its reads still read."""
    reg = VerbRegistry()
    reg.register(Verb(name, "says it only reads", _says_it_only_reads, read_only=True))
    seen: list[Any] = []
    ex = Executor(
        reg,
        mock_transport,
        contract=glot(name).frontmatter,
        dry_run=True,
        event_log=EventLog(observers=[seen.append]),
    )
    result = await ex.run_verb(name)
    assert mock_transport.intents == [] and mock_transport.stops == 0, mock_transport.intents
    assert not result.ok and result.data.get("dry_run") is True, result
    assert result.summary == (
        f"[dry-run] {name} tried to send move(vx=0.1, vy=0.0, wz=0.0), stop and stop, and a "
        "dry run sends nothing to the body"
    )
    refused = [e.data for e in seen if e.kind == "gate" and e.data["gate"] == "dry_run"]
    assert [(g["outcome"], g["reason"]) for g in refused] == [
        ("refused", f"{name} sent {what}, and a dry run sends nothing")
        for what in ("move(vx=0.1, vy=0.0, wz=0.0)", "stop", "stop")
    ]
    assert not [e for e in seen if e.kind == "intent"], "an intent reached the transport"


class _ArmWithMotions(MockTransport):
    """A body whose own motion methods are public on its transport, as the arm's are, each
    recording that it reached the body."""

    def __init__(self) -> None:
        super().__init__()
        self.reached: list[str] = []

    async def go_to_rest(self) -> str:
        self.reached.append("go_to_rest")
        return "arrived"

    async def close_gripper(self) -> str:
        self.reached.append("close_gripper")
        return "closed"

    async def let_go(self) -> None:
        self.reached.append("let_go")

    def take_hold(self) -> None:  # a plain method, as a transport may have one
        self.reached.append("take_hold")


@pytest.mark.parametrize("method", ["go_to_rest", "close_gripper", "let_go", "take_hold"])
async def test_a_dry_run_lets_a_verb_call_nothing_on_its_transport_but_the_reads(
    method: str,
) -> None:
    """The dry run held back `send_intent` and `stop`, and passed every other attribute of the
    transport on, so a verb flagged `read_only` that called one of the body's own motion methods
    through `ctx.transport`, the arm's `go_to_rest` or `let_go`, moved the body on a run that
    promised to send nothing, and nothing recorded it. On a dry run the transport a verb is
    handed lets through the named reads (`DRY_RUN_READS`) and nothing else: any other method is
    refused and recorded, and the verb fails, whatever it said. Its reads still read."""

    async def tidy(ctx: VerbContext, _p: NoParams) -> VerbResult:
        state = await ctx.transport.get_state()
        await ctx.transport.sleep(0.01)
        ctx.transport.now()
        await safety_call(getattr(ctx.transport, method))
        return VerbResult.success(f"tidied, {state.posture}")

    reg = VerbRegistry()
    reg.register(CORE["stop"])
    reg.register(Verb("tidy", "reads, it says", tidy, read_only=True))
    seen: list[Any] = []
    transport = _ArmWithMotions()
    ex = Executor(
        reg,
        transport,
        contract=glot("tidy").frontmatter,
        dry_run=True,
        event_log=EventLog(observers=[seen.append]),
    )
    result = await ex.run_verb("tidy")
    assert transport.reached == [], f"{method} reached the body on a dry run"
    assert not result.ok and result.data.get("dry_run") is True, result
    assert result.summary == (
        f"[dry-run] tidy tried to send {method}(), and a dry run sends nothing to the body"
    )
    assert gates(seen) == [("dry_run", "refused")]
    refused = next(e.data for e in seen if e.kind == "gate")
    assert refused["reason"] == f"tidy sent {method}(), and a dry run sends nothing"


async def safety_call(method: Any) -> Any:
    """Call `method` as a verb would, awaiting what an async one hands back."""
    said = method()
    return await said if asyncio.iscoroutine(said) else said


@pytest.mark.parametrize("adapter", ADAPTER_NAMES)
async def test_every_shipped_read_only_verb_does_on_a_dry_run_what_it_does_on_a_live_one(
    adapter: str,
) -> None:
    """The reads a dry run lets through are what the read-only verbs Roboglot ships call on
    their transport, on every body: `observe`, `report_state`, `map`, `introspect` and each
    adapter's own. On a dry run before this, such a verb was handed the body's transport as a
    live run hands it, and no verb reads the dry-run flag, so a live run is what it did then.
    Each one, on each adapter's mock, still answers exactly that, and nothing is refused."""
    answers: dict[str, list[tuple[bool, str, Any]]] = {}
    refused: list[Any] = []
    for dry in (False, True):
        body = make_adapter(f"{adapter}:mock")
        manifest = await body.connect()
        try:
            registry = registry_from_manifest(manifest, body)
            seen: list[Any] = []
            ex = Executor(registry, body, dry_run=dry, event_log=EventLog(observers=[seen.append]))
            for verb in sorted(v.name for v in registry.verbs() if v.read_only):
                result = await ex.run_verb(verb)
                # the data's keys and not its values: two mocks of one body differ by an odometry
                # frame's random name and the like, two live runs as much as these two
                answers.setdefault(verb, []).append(
                    (result.ok, result.summary, sorted(result.data))
                )
            refused += [e.data for e in seen if e.kind == "gate" and e.data["gate"] == "dry_run"]
        finally:
            await body.close()
    assert answers, f"{adapter} ships no read-only verb"
    for verb, (live, dry_run) in answers.items():
        assert dry_run == live, f"{adapter}'s {verb} on a dry run: {dry_run[:2]} against {live[:2]}"
    assert refused == [], refused


@dataclasses.dataclass(frozen=True)
class _Limits:
    max_vx: float = 0.3
    max_wz: float = 1.0


ROADS: dict[str, Any] = {
    "a public backend": lambda t: t.transport,
    "a dict holding it": lambda t: t.parts["arm"],
    "a tuple holding it": lambda t: t.pair[1],
    "a dataclass holding it": lambda t: t.holder.backend,
}


class _AdapterWithItsBackend(MockTransport):
    """An adapter that keeps its backend public, as the shipped ones keep `.transport`, and some
    plain data beside it."""

    @dataclasses.dataclass
    class _Holder:
        backend: Any

    def __init__(self) -> None:
        super().__init__()
        self.transport = _ArmWithMotions()
        self.parts = {"arm": self.transport, "name": "arm"}
        self.pair = ("arm", self.transport)
        self.holder = self._Holder(self.transport)
        self.camera_keys = ("front",)
        self.camera_error = None
        self.limits = _Limits()
        self.joint_names = ["shoulder_pan", "gripper"]
        self.offsets = {"shoulder_pan": 0.5}


@pytest.mark.parametrize("road", list(ROADS))
async def test_a_dry_run_hands_a_verb_nothing_from_its_transport_that_can_act(road: str) -> None:
    """The dry run held back the transport's own methods and handed every other value on as it
    was, so a verb flagged `read_only` went one attribute further: `ctx.transport.transport`, the
    backend every shipped adapter keeps public, is the real thing, and the arm's `go_to_rest` on
    it moved the shipped LeRobot mock on a dry run while the verb answered success. On a dry run
    the transport hands out the named reads and plain data, and refuses anything else, here the
    backend itself and any container or dataclass that holds it, in the words its other
    refusals use, and the verb fails."""

    async def tidy(ctx: VerbContext, _p: NoParams) -> VerbResult:
        await ROADS[road](ctx.transport).go_to_rest()
        return VerbResult.success("tidied")

    reg = VerbRegistry()
    reg.register(CORE["stop"])
    reg.register(Verb("tidy", "reads, it says", tidy, read_only=True))
    seen: list[Any] = []
    transport = _AdapterWithItsBackend()
    ex = Executor(
        reg,
        transport,
        contract=glot("tidy").frontmatter,
        dry_run=True,
        event_log=EventLog(observers=[seen.append]),
    )
    result = await ex.run_verb("tidy")
    assert transport.transport.reached == [], f"{road} reached the body on a dry run"
    attribute = {"a public backend": "transport", "a dict holding it": "parts"}.get(
        road, {"a tuple holding it": "pair", "a dataclass holding it": "holder"}.get(road)
    )
    assert not result.ok and result.summary == (
        f"[dry-run] tidy tried to send {attribute}, and a dry run sends nothing to the body"
    ), result.summary
    assert gates(seen) == [("dry_run", "refused")]


async def test_the_shipped_lerobot_backend_is_not_reached_through_its_adapter_on_a_dry_run() -> (
    None
):
    """The refuter's own case, on the shipped arm's mock: its adapter's public `.transport` is
    the mock arm, and `go_to_rest` on it moved the arm on a dry run."""

    async def tidy(ctx: VerbContext, _p: NoParams) -> VerbResult:
        await ctx.transport.transport.go_to_rest()
        return VerbResult.success("tidied")

    adapter = make_adapter("lerobot:mock")
    await adapter.connect()
    try:
        reg = VerbRegistry()
        reg.register(CORE["stop"])
        reg.register(Verb("tidy", "reads, it says", tidy, read_only=True))
        ex = Executor(reg, adapter, contract=glot("tidy").frontmatter, dry_run=True)
        arm = adapter.transport  # type: ignore[attr-defined]
        before = list(arm.sequence)
        result = await ex.run_verb("tidy")
        assert arm.sequence[len(before) :] == [], arm.sequence[len(before) :]
        assert not result.ok and "a dry run sends nothing" in result.summary
    finally:
        await adapter.close()


async def test_a_dry_run_hands_plain_data_on_as_it_is_or_as_a_copy() -> None:
    """Plain data passes on a dry run: numbers, strings, booleans, None, enums, and tuples, frozen
    sets and frozen dataclasses of those, as they are; a list, a set, a dict or a dataclass that
    is not frozen, of the same, as a copy, so a verb reads it and cannot change the transport's
    own. Nothing in any of them can act on the body."""
    from enum import Enum

    class Mode(Enum):
        IDLE = "idle"

    got: dict[str, Any] = {}

    async def look(ctx: VerbContext, _p: NoParams) -> VerbResult:
        t = ctx.transport
        got.update(
            keys=t.camera_keys,
            error=t.camera_error,
            limits=t.limits,
            joints=t.joint_names,
            offsets=t.offsets,
            mode=t.mode,
        )
        got["joints"].append("elbow")
        got["offsets"]["gripper"] = 1.0
        return VerbResult.success("looked")

    reg = VerbRegistry()
    reg.register(CORE["stop"])
    reg.register(Verb("look_around", "reads", look, read_only=True))
    transport = _AdapterWithItsBackend()
    transport.mode = Mode.IDLE  # type: ignore[attr-defined]
    ex = Executor(reg, transport, contract=glot("look_around").frontmatter, dry_run=True)
    result = await ex.run_verb("look_around")
    assert result.ok, result.summary
    assert got["keys"] is transport.camera_keys and got["limits"] is transport.limits
    assert got["mode"] is Mode.IDLE
    assert got["error"] is None
    assert got["joints"] == ["shoulder_pan", "gripper", "elbow"]
    assert transport.joint_names == ["shoulder_pan", "gripper"], "the verb changed the original"
    assert transport.offsets == {"shoulder_pan": 0.5}, "the verb changed the original"


async def test_a_read_and_a_dry_run_are_never_the_robots_first_motion(
    registry: VerbRegistry, mock_transport: MockTransport
) -> None:
    """`first_motion_at` is the robot moving. A third party's `read_only` sensor runs for real on
    a dry run, since it sends nothing, and the pause gate's caution, which holds such a verb
    still because the flag is no promise, had the record take it for the robot moving."""

    async def locate(_ctx: VerbContext, _p: NoParams) -> VerbResult:
        return VerbResult.success("the ball is 1 m ahead")

    registry.register(Verb("locate", "reads where things are", locate, read_only=True))
    contract = glot("locate, walk").frontmatter
    dry = Executor(registry, mock_transport, contract=contract, dry_run=True)
    assert (await dry.run_verb("locate")).ok
    assert (await dry.run_verb("walk", {"vx": 0.1, "duration_s": 0.2})).ok
    assert dry.first_motion_at is None and dry.first_motion_llm_calls is None

    live = Executor(registry, MockTransport(), contract=contract)
    assert (await live.run_verb("locate")).ok
    assert live.first_motion_at is None, "a read is not the robot moving"
    assert (await live.run_verb("walk", {"vx": 0.1, "duration_s": 0.2})).ok
    assert live.first_motion_at is not None


async def test_a_verb_that_refused_itself_is_never_the_robots_first_motion(
    registry: VerbRegistry, mock_transport: MockTransport
) -> None:
    """A verb past every gate can still send nothing: the arm's `move_joints` reads a goal off its
    travel and answers with the range. The record said the robot had moved at that moment, and
    the run's own numbers then counted a time to first motion for a body that had sat still."""

    async def refuses(_ctx: VerbContext, _p: NoParams) -> VerbResult:
        return VerbResult.fail("that is past the joint's travel")

    async def only_stops(ctx: VerbContext, _p: NoParams) -> VerbResult:
        await ctx.transport.stop()
        return VerbResult.fail("ended before it began")

    async def bounced(ctx: VerbContext, _p: NoParams) -> VerbResult:
        await ctx.transport.send_intent(Intent.move(0.1, 0.0, 0.0))
        return VerbResult.fail("the first send came back refused")

    registry.register(Verb("reach", "moves the body", refuses))
    registry.register(Verb("shy", "moves the body", only_stops))
    registry.register(Verb("bounce", "moves the body", bounced))
    allow = "reach, shy, bounce, walk"
    ex, _ = logged(registry, MockTransport(refuse_kinds={"move"}), allow=allow)
    assert not (await ex.run_verb("reach")).ok
    assert not (await ex.run_verb("shy")).ok
    assert not (await ex.run_verb("bounce")).ok
    assert ex.first_motion_at is None and ex.first_motion_llm_calls is None

    live, _ = logged(registry, MockTransport(), allow=allow)
    live.budget = budget = Budget(Budgets())
    budget.note_llm_call()
    assert not (await live.run_verb("reach")).ok
    budget.note_llm_call()
    assert (await live.run_verb("walk", {"vx": 0.1, "duration_s": 0.2})).ok
    assert live.first_motion_at is not None
    assert live.first_motion_llm_calls == 2, "the call that chose it included, the refusal's too"


async def test_a_verb_that_moved_and_failed_is_still_the_first_motion(
    registry: VerbRegistry, mock_transport: MockTransport
) -> None:
    """Whichever way it ends. And the time is when it was handed over, not when it came back:
    the seconds to first motion are how long the body sat still, not how long the move took."""

    async def stalls(ctx: VerbContext, _p: NoParams) -> VerbResult:
        await ctx.transport.send_intent(Intent.move(0.1, 0.0, 0.0))
        await asyncio.sleep(0.05)
        return VerbResult.fail("stalled")

    async def breaks(ctx: VerbContext, _p: NoParams) -> VerbResult:
        await ctx.transport.send_intent(Intent.move(0.1, 0.0, 0.0))
        raise RuntimeError("the bus went")

    async def sits(ctx: VerbContext, _p: NoParams) -> VerbResult:
        await asyncio.sleep(30)
        return VerbResult.success("never")

    registry.register(Verb("stalls", "moves the body", stalls))
    registry.register(Verb("breaks", "moves the body", breaks))
    registry.register(Verb("sits", "moves the body", sits, timeout_s=0.02))
    allow = "stalls, breaks, sits"

    ex, _ = logged(registry, MockTransport(), allow=allow)
    before = time.monotonic()
    assert not (await ex.run_verb("stalls")).ok
    assert ex.first_motion_at is not None and ex.first_motion_at - before < 0.05

    raised, _ = logged(registry, MockTransport(), allow=allow)
    assert not (await raised.run_verb("breaks")).ok
    assert raised.first_motion_at is not None, "it had sent a move before it raised"

    timed_out, _ = logged(registry, MockTransport(), allow=allow)
    assert not (await timed_out.run_verb("sits")).ok
    assert timed_out.first_motion_at is None, "its clock ran out and it had sent nothing"


async def test_a_verb_that_moves_some_other_way_still_counts_when_it_says_it_worked(
    registry: VerbRegistry, mock_transport: MockTransport
) -> None:
    """Only `send_intent` is tallied, and a body's rest move or a skill goes through a method of
    its own. A verb that answers ok is believed, or a run on such a body read as never moved."""

    async def rests(_ctx: VerbContext, _p: NoParams) -> VerbResult:
        return VerbResult.success("folded")

    registry.register(Verb("rests", "moves the body", rests))
    ex, _ = logged(registry, mock_transport, allow="rests")
    assert (await ex.run_verb("rests")).ok
    assert ex.first_motion_at is not None


@pytest.mark.parametrize("adapter", ADAPTER_NAMES)
async def test_a_read_only_verb_sends_nothing(adapter: str) -> None:
    """What the flag claims, on every body that ships. Two gates believe a read-only verb
    sends nothing: `--dry-run` runs one against real hardware, and since #26 the verdict gate
    lets one run before the pilot has judged the task. Both rest on the same fact and nothing
    checked it.

    Every adapter rather than `default_registry()` alone, because only two of the four shipped
    read-only verbs are in the default vocabulary. The arm's own `report_state` and the
    rosbridge base's `introspect` live in their own adapters, and `introspect` is the one that
    most deserves the check, being the only shipped read-only verb that talks to the robot at
    all: it asks the bridge what the body is.
    """
    transport = make_adapter(f"{adapter}:mock")
    sent: list[str] = []
    inner = transport.send_intent

    async def spy(intent: Intent) -> Any:
        sent.append(intent.kind)
        return await inner(intent)

    transport.send_intent = spy  # type: ignore[method-assign]
    manifest = await transport.connect()
    registry = registry_from_manifest(manifest, transport)
    read_only = [v.name for v in registry.verbs() if v.read_only]
    assert read_only, f"{adapter} ships no read-only verb: the flag went missing"
    try:
        ex = Executor(registry, transport, manifest=manifest)
        for name in read_only:
            assert (await ex.run_verb(name)).ok, f"{adapter}.{name}"
    finally:
        await transport.close()
    assert sent == [], f"a read-only verb on {adapter} sent {sent}"


async def test_invalid_params_are_feedback_not_crash(
    registry: VerbRegistry, mock_transport: MockTransport
) -> None:
    ex = Executor(registry, mock_transport, contract=glot("walk").frontmatter)
    result = await ex.run_verb("walk", {"vx": 5.0})
    assert not result.ok and "vx" in result.summary
    assert mock_transport.intents == []


async def test_preconditions_block_unsafe_verbs(registry: VerbRegistry) -> None:
    fallen = MockTransport(states=[RobotState(fallen=True, posture="fallen")])
    ex = Executor(registry, fallen, contract=glot("walk, stand_up").frontmatter)
    result = await ex.run_verb("walk")
    assert not result.ok and "fallen" in result.summary
    assert fallen.intents_of("move") == []


async def test_a_manifest_precondition_on_stop_does_not_hold_the_brake_back() -> None:
    """A manifest may list preconditions for `stop` like any verb's, and the executor applied
    them to the brake: on a fallen body `stop` answered `cannot stop` and sent nothing. No
    manifest Roboglot ships lists one, which the first assertion keeps true."""
    adapter = make_adapter("microduck:mock")
    shipped = await adapter.connect()
    try:
        assert "stop" not in shipped.preconditions, "a shipped manifest gates the brake"
        data = shipped.model_dump()
        data["preconditions"] = {**data["preconditions"], "stop": ["not_fallen"]}
        registry = registry_from_manifest(RobotManifest.model_validate(data), adapter)
    finally:
        await adapter.close()
    fallen = MockTransport(states=[RobotState(fallen=True, posture="fallen")])
    ex = Executor(registry, fallen, contract=glot("stop").frontmatter)
    result = await ex.run_verb("stop")
    assert fallen.stops == 1, f"the brake was refused and nothing was sent: {result.summary!r}"
    assert result.ok


@pytest.mark.parametrize("dry_run", [False, True])
async def test_a_stop_registered_with_a_precondition_is_never_refused_for_it(
    dry_run: bool,
) -> None:
    """The same with the precondition on the verb itself, and on a dry run, which reads the
    state first and answers for the brake without sending it."""
    reg = VerbRegistry()
    reg.register(dataclasses.replace(CORE["stop"], preconditions=[lambda _s: "low battery"]))
    transport = MockTransport()
    ex = Executor(reg, transport, contract=glot("stop").frontmatter, dry_run=dry_run)
    result = await ex.run_verb("stop")
    assert result.ok, f"the brake was refused: {result.summary!r}"
    assert transport.stops == (0 if dry_run else 1)


async def test_repeat_failure_abort(registry: VerbRegistry) -> None:
    refusing = MockTransport(refuse_kinds={"do"})
    ex = Executor(
        registry,
        refusing,
        contract=glot("kick", abort="Same verb fails 2 times in a row").frontmatter,
    )
    assert not (await ex.run_verb("kick")).ok
    with pytest.raises(Aborted):
        await ex.run_verb("kick")
    assert ex.abort.is_set()


async def test_battery_abort(registry: VerbRegistry) -> None:
    low = MockTransport(states=[RobotState(battery_percent=10, posture="standing")])
    ex = Executor(registry, low, contract=glot("quack", abort="Battery below 15%").frontmatter)
    with pytest.raises(Aborted):
        await ex.run_verb("quack")


async def test_verb_timeout_stops_the_duck(mock_transport: MockTransport) -> None:
    registry = VerbRegistry()

    async def slow(ctx: VerbContext, _: NoParams) -> VerbResult:
        await asyncio.sleep(1)
        return VerbResult.success("never")

    registry.register(Verb("slow", "slow", slow, timeout_s=0.05))
    ex = Executor(registry, mock_transport, contract=glot("slow").frontmatter)
    result = await ex.run_verb("slow")
    assert not result.ok and "timed out" in result.summary
    assert mock_transport.stops == 1


async def test_buggy_verb_stops_the_duck(mock_transport: MockTransport) -> None:
    registry = VerbRegistry()

    async def boom(ctx: VerbContext, _: NoParams) -> VerbResult:
        raise RuntimeError("kaboom")

    registry.register(Verb("boom", "boom", boom))
    ex = Executor(registry, mock_transport, contract=glot("boom").frontmatter)
    result = await ex.run_verb("boom")
    assert not result.ok and "kaboom" in result.summary
    assert mock_transport.stops == 1


class _DroppedLink(MockTransport):
    """A body whose link has dropped under a transport that lets the socket's own error out:
    every intent and every stop raises it."""

    async def send_intent(self, intent: Intent) -> Any:
        raise ConnectionResetError("Connection lost")

    async def stop(self) -> None:
        self.stops += 1
        raise ConnectionResetError("Connection lost")


#: What `_DroppedLink`'s stop says once the executor has caught it.
DROPPED = "the stop sent after it failed: ConnectionResetError: Connection lost"


async def test_a_stop_that_raises_after_a_verb_that_raised_is_said_and_not_raised(
    registry: VerbRegistry,
) -> None:
    """The executor answers a verb that raised with a stop, and a stop into a dropped socket
    raised too: out of `run_verb`, from inside the handler that exists to turn the verb's error
    into a result. A run with an event log had `LoggedTransport` to catch it, and one without
    had nothing. The result says both now, and that the stop did not land."""
    transport = _DroppedLink()
    ex = Executor(registry, transport, contract=glot("walk").frontmatter)
    result = await ex.run_verb("walk", {"vx": 0.1, "duration_s": 0.3})
    assert not result.ok and transport.stops == 1
    assert result.summary == f"walk raised ConnectionResetError: Connection lost; {DROPPED}"


async def test_a_verb_that_timed_out_whose_stop_raises_is_said_and_not_raised() -> None:
    registry = VerbRegistry()

    async def slow(ctx: VerbContext, _: NoParams) -> VerbResult:
        await asyncio.sleep(1)
        return VerbResult.success("never")

    registry.register(Verb("slow", "slow", slow, timeout_s=0.05))
    transport = _DroppedLink()
    ex = Executor(registry, transport, contract=glot("slow").frontmatter)
    result = await ex.run_verb("slow")
    assert not result.ok and transport.stops == 1
    assert result.summary == f"slow timed out after 0.05s; {DROPPED}"


async def test_an_abort_whose_stop_raises_is_still_the_abort() -> None:
    """An abort cancels the verb and sends a stop, and the abort is what the caller is told:
    the agent loop and the MCP server each answer `Aborted` in words of their own. A stop that
    raised left in its place, its own error where the abort should have been."""
    registry = VerbRegistry()
    started = asyncio.Event()

    async def waiting(ctx: VerbContext, _: NoParams) -> VerbResult:
        started.set()
        await asyncio.sleep(30)
        return VerbResult.success("never")

    registry.register(Verb("waiting", "waits", waiting, timeout_s=30))
    transport = _DroppedLink()
    ex = Executor(registry, transport, contract=glot("waiting").frontmatter)
    running = asyncio.create_task(ex.run_verb("waiting"))
    await asyncio.wait_for(started.wait(), timeout=5)
    ex.abort.set()
    with pytest.raises(Aborted, match=DROPPED):
        await asyncio.wait_for(running, timeout=5)
    assert transport.stops == 1


class _UnheardStop(MockTransport):
    """A body whose stop returns and did not land, and says so the way the arm's real backend
    does, in `stop_error`, without raising: the core `stop` verb reads it, and so must the stop
    the executor sends after a verb it ended."""

    async def stop(self) -> None:
        self.stops += 1
        self.stop_error = "the hold did not reach the arm"


#: What `_UnheardStop`'s stop says once the executor has read it.
UNHEARD = "the stop sent after it failed: the hold did not reach the arm"


async def test_a_verb_that_timed_out_whose_stop_did_not_land_says_so() -> None:
    """Said `stopped` before: only a stop that raised was heard as failed."""
    registry = VerbRegistry()

    async def slow(ctx: VerbContext, _: NoParams) -> VerbResult:
        await asyncio.sleep(1)
        return VerbResult.success("never")

    registry.register(Verb("slow", "slow", slow, timeout_s=0.05))
    transport = _UnheardStop()
    ex = Executor(registry, transport, contract=glot("slow").frontmatter)
    result = await ex.run_verb("slow")
    assert not result.ok and transport.stops == 1
    assert result.summary == f"slow timed out after 0.05s; {UNHEARD}"


async def test_an_abort_whose_stop_did_not_land_does_not_say_a_stop_was_sent() -> None:
    registry = VerbRegistry()
    started = asyncio.Event()

    async def waiting(ctx: VerbContext, _: NoParams) -> VerbResult:
        started.set()
        await asyncio.sleep(30)
        return VerbResult.success("never")

    registry.register(Verb("waiting", "waits", waiting, timeout_s=30))
    transport = _UnheardStop()
    ex = Executor(registry, transport, contract=glot("waiting").frontmatter)
    running = asyncio.create_task(ex.run_verb("waiting"))
    await asyncio.wait_for(started.wait(), timeout=5)
    ex.abort.set()
    with pytest.raises(Aborted, match=UNHEARD) as aborted:
        await asyncio.wait_for(running, timeout=5)
    assert "a stop was sent" not in str(aborted.value)
    assert transport.stops == 1


class _WentOutStop(MockTransport):
    """A body whose stop went out over a link that has given no sign of life since, and says so
    as the rosbridge, XLeRobot and AlohaMini transports do: why in `stop_error`, beside
    `stop_unconfirmed`, without raising. Its stop takes `slow_s` first, for one answered after
    the executor stopped waiting for it."""

    def __init__(self, slow_s: float = 0.0) -> None:
        super().__init__()
        self.slow_s = slow_s
        self.stop_error: str | None = None
        self.stop_unconfirmed = False

    async def stop(self) -> None:
        if self.slow_s:
            await asyncio.sleep(self.slow_s)
        await super().stop()
        self.stop_error, self.stop_unconfirmed = NOT_HEARD, True


#: Why `_WentOutStop`'s stop is not known to have arrived, as its transport puts it.
NOT_HEARD = "nothing has come over the link since its stop went out"


@pytest.mark.parametrize("ending", ["timeout", "raised", "abort", "preempt", "pause", "cancelled"])
async def test_an_owed_stop_that_went_out_unconfirmed_is_said_so_on_every_exit(
    ending: str,
) -> None:
    """The executor read `stop_error` alone, so every exit said `the stop sent after it failed`
    of a stop that went out over a link gone quiet and is not known to have arrived, where the
    core `stop` verb says of the same stop that it went out. Each exit says it in the core's
    words now (`WENT_OUT`), once, and none calls it a stop that failed: the result or the
    error the caller is handed, and the gate the record keeps."""
    started = asyncio.Event()

    async def waiting(ctx: VerbContext, _: NoParams) -> VerbResult:
        started.set()
        await asyncio.sleep(30)
        return VerbResult.success("never")

    async def broken(ctx: VerbContext, _: NoParams) -> VerbResult:
        raise RuntimeError("the twist could not be encoded")

    registry = VerbRegistry()
    registry.register(
        Verb("waiting", "waits", waiting, timeout_s=0.05 if ending == "timeout" else 30)
    )
    registry.register(Verb("broken", "raises", broken))
    seen: list[Any] = []
    ex = Executor(
        registry,
        _WentOutStop(),
        contract=glot("waiting, broken").frontmatter,
        event_log=EventLog(observers=[seen.append]),
    )
    told = ""
    if ending == "raised":
        told = (await ex.run_verb("broken")).summary
    else:
        running = asyncio.create_task(ex.run_verb("waiting"))
        await asyncio.wait_for(started.wait(), timeout=5)
        if ending == "abort":
            ex.abort.set()
        elif ending == "preempt":
            assert ex.preempt("a newer command") == 1
        elif ending == "pause":
            ex.pause_for(5.0)
        elif ending == "cancelled":
            running.cancel()
        try:
            told = (await asyncio.wait_for(running, timeout=5)).summary
        except (Aborted, Preempted) as e:
            told = str(e)
        except asyncio.CancelledError:
            assert ending == "cancelled"
    reasons = [
        e.data["reason"]
        for e in seen
        if e.kind == "gate" and e.data["outcome"] == "fired" and NOT_HEARD in e.data["reason"]
    ]
    texts = [t for t in (told, *reasons) if t]
    assert texts, f"nothing said the stop on the {ending} exit"
    if ending in ("abort", "preempt", "pause", "cancelled"):
        assert reasons, f"the {ending} gate said nothing of the stop: {gates(seen)}"
    for text in texts:
        assert text.count(f"{WENT_OUT}: {NOT_HEARD}") == 1, text
        assert "failed" not in text, text


async def test_an_owed_stop_answered_after_it_was_waited_for_says_it_went_out() -> None:
    """One no longer waited for that then went out unconfirmed was said on the log to have not
    landed (`_late_stop`), the words of a stop that never left."""
    started = asyncio.Event()

    async def waiting(ctx: VerbContext, _: NoParams) -> VerbResult:
        started.set()
        await asyncio.sleep(30)
        return VerbResult.success("never")

    registry = VerbRegistry()
    registry.register(Verb("waiting", "waits", waiting, timeout_s=0.05))
    registry.register(dataclasses.replace(CORE["stop"], timeout_s=0.05))
    said: list[str] = []
    transport = _WentOutStop(slow_s=0.3)
    ex = Executor(registry, transport, contract=glot("waiting").frontmatter, log=said.append)
    result = await ex.run_verb("waiting")
    assert "goes on by itself" in result.summary, result.summary
    for _ in range(100):
        if transport.stops:
            break
        await asyncio.sleep(0.05)
    await asyncio.sleep(0)
    late = [line for line in said if "no longer waited for" in line]
    assert late == [f"an owed stop that was no longer waited for {WENT_OUT}: {NOT_HEARD}"], said


class _StoppedAgainAsItEnds(MockTransport):
    """A body whose first stop did not land, and which another stop starts on just as that one
    ends, as the heartbeat's or a second caller's does: a stop begins by clearing what the last
    one said (`stop_error`), as every transport's does."""

    def __init__(self) -> None:
        super().__init__()
        self.stop_error: str | None = None
        self.again = asyncio.Event()

    async def stop(self) -> None:
        self.stop_error = None
        await super().stop()
        if self.stops > 1:
            self.again.set()
            await asyncio.sleep(30)  # still on its way
            return
        await asyncio.sleep(0.01)
        self.stop_error = "the first stop did not reach the body"
        loop = asyncio.get_running_loop()
        loop.call_soon(lambda: asyncio.ensure_future(self.stop()))  # starts as this one ends


async def test_an_owed_stop_is_judged_by_its_own_word_and_not_by_one_that_started_after_it() -> (
    None
):
    """The executor read the transport's `stop_error` a turn of the loop after its owed stop had
    ended, and a stop that began in that turn had cleared it, so an abort said `a stop was sent`
    of a stop that had said it did not reach the body. The owed stop's own word is read as it
    ends, in the same step, and nothing that starts after it can take it back."""
    started = asyncio.Event()

    async def waiting(ctx: VerbContext, _: NoParams) -> VerbResult:
        started.set()
        await asyncio.sleep(30)
        return VerbResult.success("never")

    registry = VerbRegistry()
    registry.register(Verb("waiting", "waits", waiting, timeout_s=30))
    transport = _StoppedAgainAsItEnds()
    ex = Executor(registry, transport, contract=glot("waiting").frontmatter)
    running = asyncio.create_task(ex.run_verb("waiting"))
    await asyncio.wait_for(started.wait(), timeout=5)
    ex.abort.set()
    with pytest.raises(Aborted) as aborted:
        await asyncio.wait_for(running, timeout=5)
    assert transport.again.is_set(), "the second stop never started: the race was not run"
    assert "the first stop did not reach the body" in str(aborted.value), str(aborted.value)
    assert "a stop was sent" not in str(aborted.value)


class _SilentBody(_UnheardStop):
    """A body gone silent: its reads fail, and its stop, which on the arm reads before it holds,
    holds nothing and says so in `stop_error`, as the arm's real backend does."""

    async def get_state(self) -> RobotState:
        raise TransportError("the arm did not answer: ConnectionError: no status packet", code=7)


def _still() -> VerbRegistry:
    async def still(ctx: VerbContext, _: NoParams) -> VerbResult:
        return VerbResult.success("still")

    registry = VerbRegistry()
    registry.register(Verb("still", "still", still))
    return registry


async def test_a_read_that_failed_says_when_the_stop_sent_after_it_did_not_land() -> None:
    """A verb's gates read the body first, and a read that failed sent its stop and said only that
    the read had failed, on `stop` too: neither the pilot nor a chat heard that nothing held the
    arm. The error is the read's own, its class and its code kept."""
    transport = _SilentBody()
    ex = Executor(_still(), transport, contract=glot("still").frontmatter)
    with pytest.raises(TransportError) as failed:
        await ex.run_verb("still")
    read = "the arm did not answer: ConnectionError: no status packet"
    assert str(failed.value) == f"{read}; {UNHEARD}"
    assert failed.value.code == 7 and transport.stops == 1


async def test_a_read_that_failed_over_a_stop_that_landed_says_only_the_read() -> None:
    class Unread(MockTransport):
        async def get_state(self) -> RobotState:
            raise TransportError("the link is down")

    transport = Unread()
    ex = Executor(_still(), transport, contract=glot("still").frontmatter)
    with pytest.raises(TransportError) as failed:
        await ex.run_verb("still")
    assert str(failed.value) == "the link is down" and transport.stops == 1


async def test_a_stop_whose_reason_the_read_already_gave_is_not_said_twice() -> None:
    """A wedged bus answers the read and the stop with one sentence, which read twice over. The
    stop's failure is still said, since it is what tells a pilot nothing holds the arm."""

    class Wedged(MockTransport):
        stop_error: str | None = None

        async def get_state(self) -> RobotState:
            raise TransportError("the bus is wedged")

        async def stop(self) -> None:
            self.stops += 1
            self.stop_error = "the bus is wedged"

    ex = Executor(_still(), Wedged(), contract=glot("still").frontmatter)
    with pytest.raises(TransportError) as failed:
        await ex.run_verb("still")
    assert str(failed.value) == "the bus is wedged; the stop sent after it failed the same way"


async def test_an_error_a_link_raises_again_gains_the_stop_once() -> None:
    """A link that keeps the one error it died of raised it again for every read, and each verb
    added the clause again."""
    kept = TransportError("the arm did not answer: ConnectionError: no status packet")

    class Kept(_UnheardStop):
        async def get_state(self) -> RobotState:
            raise kept

    ex = Executor(_still(), Kept(), contract=glot("still").frontmatter)
    for _ in range(3):
        with pytest.raises(TransportError):
            await ex.run_verb("still")
    assert str(kept).count("the stop sent after it failed") == 1, str(kept)


async def test_a_composite_whose_nested_read_failed_says_the_stop_once() -> None:
    """The nested verb's read said why its stop did not land, and the composite's own handler
    said it again after it."""

    class SecondReadFails(_UnheardStop):
        reads = 0

        async def get_state(self) -> RobotState:
            self.reads += 1
            if self.reads > 1:
                raise TransportError("the arm did not answer: ConnectionError: no status packet")
            return await super().get_state()

    async def outer(ctx: VerbContext, _: NoParams) -> VerbResult:
        assert ctx.run_verb is not None
        return await ctx.run_verb("still", {})

    registry = _still()
    registry.register(Verb("outer", "outer", outer))
    ex = Executor(registry, SecondReadFails(), contract=glot("outer, still").frontmatter)
    result = await ex.run_verb("outer")
    assert not result.ok
    assert result.summary.count("the stop sent after it failed") == 1, result.summary


async def test_no_contract_allows_safe_verbs_only(
    registry: VerbRegistry, mock_transport: MockTransport
) -> None:
    registry.register(Verb("nuke", "dangerous", lambda c, p: None, safety_class="dangerous"))  # type: ignore[arg-type]
    ex = Executor(registry, mock_transport, contract=None, confirm=allow_all)
    assert "move" in ex.allowed and "nuke" not in ex.allowed
    assert ex.is_allowed("walk") and not ex.is_allowed("nuke")


async def test_walk_feeds_the_deadman(
    registry: VerbRegistry, mock_transport: MockTransport
) -> None:
    ex = Executor(registry, mock_transport, contract=glot("walk").frontmatter)
    await ex.run_verb("walk", {"vx": 0.1, "duration_s": 1.0})
    moves = mock_transport.intents_of("move")
    assert len(moves) == 10  # re-sent every 100 ms
    assert mock_transport.intents[-1].kind == "stop"


async def test_heartbeat_failure_stops_and_aborts() -> None:
    transport = MockTransport(fail_heartbeat_after=1)
    abort = asyncio.Event()
    hb = Heartbeat(transport, abort, period_s=0.01)
    hb.start()
    await asyncio.wait_for(abort.wait(), timeout=2)
    await hb.stop()
    assert transport.stops >= 1
    assert hb.failure is not None


#: The beat of the heartbeats below, and how long after a failed one its abort may take: the
#: abort is one `Event.set` away, so a few beats is ample.
FAST_BEAT_S = 0.01
ABORT_WITHIN_S = 10 * FAST_BEAT_S


class _StalledLink(MockTransport):
    """The beat fails on its own reply timeout, and the stop over the same link does not come
    back until the test lets it (`link_back`)."""

    def __init__(self) -> None:
        super().__init__()
        self.stop_began = asyncio.Event()
        self.link_back = asyncio.Event()
        self.intents_at_stop: int | None = None

    async def heartbeat(self) -> None:
        self.heartbeats += 1
        raise HeartbeatError("no reply within the beat's timeout")

    async def stop(self) -> None:
        if not self.stop_began.is_set():
            self.intents_at_stop = len(self.intents)
            self.stop_began.set()
            await self.link_back.wait()
        await super().stop()


async def test_a_failed_beat_aborts_the_run_though_its_stop_has_not_come_back() -> None:
    """The heartbeat set the abort in a `finally` after its own stop, which a stop that raised
    reached and one that hung on the stalled link did not: the run was not aborted for as long
    as the stop took. And the heartbeat waited on that stop with no end."""
    transport = _StalledLink()
    abort = asyncio.Event()
    hb = Heartbeat(transport, abort, period_s=FAST_BEAT_S)
    hb.start()
    try:
        await asyncio.wait_for(transport.stop_began.wait(), timeout=2.0)
        await asyncio.sleep(ABORT_WITHIN_S)
        assert abort.is_set(), "the abort waits on the heartbeat's own stop"
        assert hb._task is not None and hb._task.done(), "the heartbeat waits on its stop"
    finally:
        transport.link_back.set()
        await asyncio.wait_for(hb.stop(), timeout=2.0)


async def test_no_twist_goes_out_after_the_heartbeat_s_stop_began() -> None:
    """While the heartbeat's stop was out and the abort not yet set, a walk beside it went on
    sending twists, and on a body whose twists are notifications they reached it after the
    stop."""
    transport = _StalledLink()

    async def long_walk(ctx: VerbContext, _p: NoParams) -> VerbResult:
        for _ in range(500):
            await ctx.transport.send_intent(Intent.move(0.1, 0.0, 0.0))
            await asyncio.sleep(FAST_BEAT_S)
        return VerbResult.success("finished on its own")

    reg = VerbRegistry()
    reg.register(Verb("long_walk", "walks", long_walk, timeout_s=30))
    ex = Executor(reg, transport, contract=glot("long_walk").frontmatter)
    running = asyncio.create_task(ex.run_verb("long_walk"))
    await asyncio.sleep(3 * FAST_BEAT_S)
    hb = Heartbeat(transport, ex.abort, period_s=FAST_BEAT_S)
    hb.start()
    try:
        await asyncio.wait_for(transport.stop_began.wait(), timeout=2.0)
        await asyncio.sleep(ABORT_WITHIN_S)
        assert transport.intents_at_stop is not None
        after = transport.intents[transport.intents_at_stop :]
        moves_after = sum(1 for i in after if i.kind == "move")
        assert moves_after == 0, f"{moves_after} twists went out after the heartbeat's stop began"
    finally:
        transport.link_back.set()
        await asyncio.wait_for(hb.stop(), timeout=2.0)
        await _ended(running)


async def test_a_verb_asked_for_while_the_heartbeat_s_stop_is_out_is_refused() -> None:
    """A new verb passed the abort's gate while the heartbeat's stop was out, and sent."""
    transport = _StalledLink()

    async def kick(ctx: VerbContext, _p: NoParams) -> VerbResult:
        await ctx.transport.send_intent(Intent.move(0.1, 0.0, 0.0))
        return VerbResult.success("kicked")

    reg = VerbRegistry()
    reg.register(Verb("kick_now", "k", kick))
    ex = Executor(reg, transport, contract=glot("kick_now").frontmatter)
    hb = Heartbeat(transport, ex.abort, period_s=FAST_BEAT_S)
    hb.start()
    try:
        await asyncio.wait_for(transport.stop_began.wait(), timeout=2.0)
        with pytest.raises(Aborted):
            await asyncio.wait_for(ex.run_verb("kick_now"), timeout=2.0)
        assert transport.intents_of("move") == []
    finally:
        transport.link_back.set()
        await asyncio.wait_for(hb.stop(), timeout=2.0)


def _block_the_loop(seconds: float) -> None:
    """Nothing on the event loop runs meanwhile, so what lands in that time queues up in order."""
    time.sleep(seconds)


async def test_a_heartbeat_stop_ends_though_python_3_11_lost_its_cancel() -> None:
    """A transport waits for its answers through `asyncio.wait_for`, and Python 3.11's loses a
    cancel that lands as its call returns. The loop then beat on for good and `stop`, the first
    line of a run's teardown, waited on it, which held a macOS job to its limit. The cancel is
    still asked for, and the loop honours it at its next turn. On 3.12 no cancel is lost, so only
    a 3.11 job can fail this."""
    loop = asyncio.get_running_loop()
    fetched, go = threading.Event(), threading.Event()
    stopping: list[asyncio.Task[None]] = []
    beats: list[None] = []

    def answer() -> None:
        beats.append(None)
        if not fetched.is_set():
            fetched.set()
            go.wait(10.0)
            # the stop lands in the turn after this call's own answer, before wait_for has
            # resumed the loop: the turn it loses a cancel in
            loop.call_soon_threadsafe(lambda: loop.call_soon(begin))

    class Beating(MockTransport):
        async def heartbeat(self) -> None:
            await asyncio.wait_for(asyncio.to_thread(answer), timeout=5.0)

    abort = asyncio.Event()
    beat = Heartbeat(Beating(), abort, period_s=0.01)

    def begin() -> None:
        stopping.append(asyncio.create_task(beat.stop()))

    beat.start()
    assert await asyncio.to_thread(fetched.wait, 5.0), "a beat is out"
    go.set()
    _block_the_loop(0.3)  # the stop and the beat's answer queue up in that order
    await asyncio.sleep(0.05)
    done, _ = await asyncio.wait(stopping, timeout=5.0)
    abort.set()  # ends a loop that did not stop, so a failure here is a failure and no hang
    await asyncio.wait(stopping, timeout=5.0)
    assert done, f"the heartbeat beat on past its cancel: {len(beats)} beats"


# ── the abort has to reach the verb that is already moving ──────────────────────────────


async def test_an_abort_cancels_the_running_verb_and_stops(
    registry: VerbRegistry, mock_transport: MockTransport
) -> None:
    """Setting the flag was never enough. `asyncio.wait_for` only watches the clock, so a
    kill switch, a Ctrl-C or a failed heartbeat left the legs moving until the verb finished
    on its own — up to a `go_to`'s whole timeout — and the verb's own 10 Hz resend kept
    feeding the daemon's deadman the entire time, so nothing else stopped it either."""

    # MockTransport.sleep advances a virtual clock and returns, so a normal `move` finishes
    # before anything could interrupt it. A verb that takes real wall-clock time is the
    # honest model of the closed loop this is about.
    async def long_walk(ctx: VerbContext, _p: NoParams) -> VerbResult:
        for _ in range(500):
            await ctx.transport.send_intent(Intent.move(0.1, 0.0, 0.0))
            await asyncio.sleep(0.01)
        return VerbResult.success("finished on its own")

    registry.register(Verb("long_walk", "walks for a long time", long_walk, timeout_s=30))
    ex = Executor(registry, mock_transport, contract=glot("long_walk").frontmatter)
    running = asyncio.create_task(ex.run_verb("long_walk"))
    await asyncio.sleep(0.15)  # let it get going
    assert len(mock_transport.intents_of("move")) >= 1

    ex.abort.set()
    with pytest.raises(Aborted):
        await asyncio.wait_for(running, timeout=1.0)

    assert mock_transport.intents[-1].kind == "stop", "the abort must leave a stop behind"
    # and it must actually stop commanding, not merely have sent one stop on the way past
    settled = len(mock_transport.intents_of("move"))
    await asyncio.sleep(0.2)
    assert len(mock_transport.intents_of("move")) == settled


#: A walk of ten resends, so a verb whose cancel was lost has a second of twists left to send.
LONG_WALK_S = 10 * MOVE_RESEND_S
#: What an abort or a timeout may take to end such a walk and stop it: a few resends.
ENDS_WITHIN_S = 3 * MOVE_RESEND_S


class _RealTime(MockTransport):
    """A body whose clock is the wall's, as every real transport's is."""

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        await asyncio.sleep(seconds)


class _SwallowsOneCancel(_RealTime):
    """The first twist's answer is still on its way when the verb is cancelled, and the call that
    waits for it returns the answer instead of raising the cancel: Python 3.11's `wait_for` with
    its call done, or a third party's send that catches it."""

    def __init__(self) -> None:
        super().__init__()
        self.in_flight = asyncio.Event()
        self.swallowed = 0

    async def send_intent(self, intent: Intent) -> Ack:
        self.intents.append(intent)
        if intent.kind == "move" and not self.in_flight.is_set():
            self.in_flight.set()
            try:
                await asyncio.sleep(5.0)
            except asyncio.CancelledError:
                self.swallowed += 1
                return Ack()
        return Ack()


async def _ended(running: asyncio.Task[Any]) -> None:
    """End a run the test is done with, with a second cancel and a bounded wait, so a verb that
    did not end is a failure and never a run left going."""
    if not running.done():
        running.cancel()
    await asyncio.wait({running}, timeout=2.0)
    if running.done() and not running.cancelled():
        running.exception()


async def test_an_abort_ends_a_walk_whose_cancel_a_send_swallowed(
    registry: VerbRegistry,
) -> None:
    """The executor cancelled a verb once and then waited for it with no end. A cancel the
    transport's wait lost left the walk sending its twists after the kill switch, every resend
    to the end of the walk, and the stop went out only then."""
    transport = _SwallowsOneCancel()
    ex = Executor(registry, transport, contract=glot("walk").frontmatter)
    running = asyncio.create_task(ex.run_verb("walk", {"vx": 0.1, "duration_s": LONG_WALK_S}))
    try:
        await asyncio.wait_for(transport.in_flight.wait(), timeout=2.0)
        at_abort = len(transport.intents_of("move"))
        ex.abort.set()
        await asyncio.wait({running}, timeout=ENDS_WITHIN_S)
        moves_after = len(transport.intents_of("move")) - at_abort
        assert transport.swallowed == 1, "the stand-in did not get the cancel it swallows"
        assert running.done(), f"the abort did not end the walk: {moves_after} twists after it"
        assert moves_after == 0, f"{moves_after} twists went out after the abort"
        assert isinstance(running.exception(), Aborted)
        assert transport.intents[-1].kind == "stop"
    finally:
        await _ended(running)


async def test_a_timeout_ends_a_verb_whose_cancel_a_send_swallowed() -> None:
    """The verb's own `timeout_s` was the same single cancel, and it bounded nothing once the
    cancel was lost: a verb that never looks at anything sent on to its own end."""
    transport = _SwallowsOneCancel()

    async def long_walk(ctx: VerbContext, _p: NoParams) -> VerbResult:
        for _ in range(round(LONG_WALK_S / MOVE_RESEND_S)):
            await ctx.transport.send_intent(Intent.move(0.1, 0.0, 0.0))
            await asyncio.sleep(MOVE_RESEND_S)
        return VerbResult.success("finished on its own")

    reg = VerbRegistry()
    reg.register(Verb("long_walk", "walks", long_walk, timeout_s=MOVE_RESEND_S / 2))
    ex = Executor(reg, transport, contract=glot("long_walk").frontmatter)
    running = asyncio.create_task(ex.run_verb("long_walk"))
    try:
        await asyncio.wait({running}, timeout=MOVE_RESEND_S / 2 + ENDS_WITHIN_S)
        assert transport.swallowed == 1, "the stand-in did not get the cancel it swallows"
        moves = len(transport.intents_of("move"))
        assert running.done(), f"the timeout bounded nothing: {moves} twists so far"
        assert moves == 1, f"{moves - 1} twists went out after the timeout"
        result = running.result()
        assert not result.ok and "timed out" in result.summary
        assert transport.intents[-1].kind == "stop"
    finally:
        await _ended(running)


async def test_an_abort_ends_a_walk_over_a_transport_that_waits_through_wait_for(
    registry: VerbRegistry,
) -> None:
    """No stand-in swallows anything here: `asyncio.wait_for` does, on Python 3.11, when the
    abort's cancel lands in the turn the twist's answer does, as it can over the zmq hosts and
    the bridges that wait for every answer through it. On 3.12 no cancel is lost, so only a 3.11
    job can fail this."""
    loop = asyncio.get_running_loop()
    fetched, go = threading.Event(), threading.Event()
    ex_box: list[Executor] = []

    def answer() -> None:
        if not fetched.is_set():
            fetched.set()
            go.wait(10.0)
            # the abort lands in the turn this call's own answer does, ahead of it
            loop.call_soon_threadsafe(ex_box[0].abort.set)

    class ThroughWaitFor(_RealTime):
        async def send_intent(self, intent: Intent) -> Ack:
            self.intents.append(intent)
            await asyncio.wait_for(asyncio.to_thread(answer), timeout=5.0)
            return Ack()

    transport = ThroughWaitFor()
    ex = Executor(registry, transport, contract=glot("walk").frontmatter)
    ex_box.append(ex)
    running = asyncio.create_task(ex.run_verb("walk", {"vx": 0.1, "duration_s": LONG_WALK_S}))
    try:
        assert await asyncio.to_thread(fetched.wait, 5.0), "a twist is out"
        at_abort = len(transport.intents_of("move"))
        go.set()
        _block_the_loop(0.3)  # the abort and the twist's answer queue up in that order
        await asyncio.wait({running}, timeout=ENDS_WITHIN_S)
        moves_after = len(transport.intents_of("move")) - at_abort
        assert running.done(), (
            f"python {sys.version_info[:2]}: the abort did not end the walk; "
            f"{moves_after} twists went out after it"
        )
        assert moves_after == 0, f"{moves_after} twists went out after the abort"
        assert isinstance(running.exception(), Aborted)
        assert transport.intents[-1].kind == "stop"
    finally:
        go.set()
        await _ended(running)


async def test_the_same_walk_with_no_cancel_lost_ends_within_the_same_bound(
    registry: VerbRegistry,
) -> None:
    """The control for the three above: over a transport that loses nothing, the walk ends
    within `ENDS_WITHIN_S`, so that bound is not what fails them."""
    transport = _RealTime()
    ex = Executor(registry, transport, contract=glot("walk").frontmatter)
    running = asyncio.create_task(ex.run_verb("walk", {"vx": 0.1, "duration_s": LONG_WALK_S}))
    try:
        await asyncio.sleep(MOVE_RESEND_S * 1.5)
        at_abort = len(transport.intents_of("move"))
        ex.abort.set()
        await asyncio.wait({running}, timeout=ENDS_WITHIN_S)
        assert running.done() and isinstance(running.exception(), Aborted)
        assert len(transport.intents_of("move")) == at_abort
        assert transport.intents[-1].kind == "stop"
    finally:
        await _ended(running)


async def test_a_verb_that_will_not_end_is_left_behind_and_the_stop_goes_out_without_it(
    mock_transport: MockTransport,
) -> None:
    """A verb that catches every cancel never ended, and the abort's stop waited on it for good.
    It is cancelled twice now, left behind `ENDING_S` after the second, said to be, and the stop
    goes out without it; nothing it tries to send meanwhile reaches the robot."""
    quit_now = asyncio.Event()
    tries: list[None] = []

    async def stubborn(ctx: VerbContext, _p: NoParams) -> VerbResult:
        while not quit_now.is_set():
            try:
                await asyncio.sleep(MOVE_RESEND_S / 10)
                tries.append(None)
                await ctx.transport.send_intent(Intent.move(0.1, 0.0, 0.0))
            except asyncio.CancelledError:
                continue
        return VerbResult.success("ended by the test")

    reg = VerbRegistry()
    reg.register(Verb("stubborn", "will not end", stubborn, timeout_s=30))
    ex, seen = logged(reg, mock_transport, allow="stubborn")
    running = asyncio.create_task(ex.run_verb("stubborn"))
    try:
        await asyncio.sleep(MOVE_RESEND_S)
        at_abort, tries_at_abort = len(mock_transport.intents_of("move")), len(tries)
        ex.abort.set()
        await asyncio.wait({running}, timeout=2 * ENDING_S + ENDS_WITHIN_S)
        assert running.done(), "the executor waited on a verb that will not end"
        assert isinstance(running.exception(), Aborted)
        assert len(tries) > tries_at_abort, "the stand-in stopped trying to send"
        reached = len(mock_transport.intents_of("move")) - at_abort
        assert reached == 0, f"{reached} twists reached the robot after the abort"
        assert mock_transport.intents[-1].kind == "stop"
        notes = [e.data["text"] for e in seen if e.kind == "note"]
        assert any("had not ended" in n and "left behind" in n for n in notes), notes
    finally:
        quit_now.set()
        await _ended(running)


async def test_stop_still_runs_on_an_aborted_executor(
    registry: VerbRegistry, mock_transport: MockTransport
) -> None:
    """The abort is set exactly when the pilot reaches for the brake — a failed heartbeat, a
    kill switch — so refusing `stop` closed the panic button at the only moment it mattered.
    Everything else stays refused."""
    ex = Executor(registry, mock_transport, contract=glot("walk, stop").frontmatter)
    ex.abort.set()

    before = mock_transport.stops
    result = await ex.run_verb("stop")
    assert result.ok and mock_transport.stops == before + 1

    with pytest.raises(Aborted):
        await ex.run_verb("walk", {"vx": 0.1, "duration_s": 0.2})


class _WedgedRead(MockTransport):
    """A body whose state read does not come back until the test lets it, and whose stop does."""

    def __init__(self) -> None:
        super().__init__()
        self.read_back = asyncio.Event()

    async def get_state(self) -> RobotState:
        await self.read_back.wait()
        return await super().get_state()


#: A brake has to go out within a few ticks of the 10 Hz resend it is meant to beat.
BRAKE_WITHIN_S = 5 * MOVE_RESEND_S


@pytest.mark.parametrize("aborted", [False, True])
async def test_the_brake_goes_out_though_the_state_read_before_it_hangs(
    registry: VerbRegistry, aborted: bool
) -> None:
    """Every verb read the body's state before it ran, `stop` too, with no end to the wait and
    outside the abort's race: a read that did not come back held the brake for good, and an
    abort set meanwhile did not get it past."""
    transport = _WedgedRead()
    ex = Executor(registry, transport, contract=glot("walk").frontmatter)
    braking = asyncio.create_task(ex.run_verb("stop"))
    try:
        if aborted:
            await asyncio.sleep(MOVE_RESEND_S)
            ex.abort.set()
        await asyncio.sleep(BRAKE_WITHIN_S)
        assert transport.stops == 1, "the brake waits on the state read before it"
    finally:
        transport.read_back.set()
        await asyncio.wait({braking}, timeout=2.0)
    assert braking.result().ok


async def test_the_brake_goes_out_once_a_slow_read_returns_as_well(
    registry: VerbRegistry,
) -> None:
    """The control for the test above: with the read let through, the brake is what it was."""
    transport = _WedgedRead()
    transport.read_back.set()
    ex = Executor(registry, transport, contract=glot("walk").frontmatter)
    result = await asyncio.wait_for(ex.run_verb("stop"), timeout=2.0)
    assert result.ok and transport.stops == 1


async def test_a_stop_that_never_left_is_not_reported_as_a_stop(
    registry: VerbRegistry, mock_transport: MockTransport
) -> None:
    """`stop` is asked for most often when the link is the thing that is wrong. The guard in
    verbs/core.py reads `stop_error` off whatever it was handed, which in a real run is the
    adapter — and no adapter forwarded it, so the check was dead everywhere and an
    undeliverable stop was written into the transcript as a success."""
    ex = Executor(registry, mock_transport, contract=glot("stop").frontmatter)
    assert (await ex.run_verb("stop")).ok

    mock_transport.stop_error = "duck.stop: no answer within 2s"
    result = await ex.run_verb("stop")
    assert not result.ok
    assert "could not be delivered" in result.summary
    assert "may still be moving" in result.summary


@pytest.mark.parametrize("sent_with_it", [{"now": True}, {"_unparsed": "{not json"}])
async def test_a_stop_is_never_refused_for_what_came_with_it(
    registry: VerbRegistry, mock_transport: MockTransport, sent_with_it: dict[str, Any]
) -> None:
    """`stop` takes no arguments, and the moment somebody reaches for the brake is not the moment
    to teach them that. A call with anything in it was refused at the `params` gate, past the
    point where `stops` had counted it, so a verb beside it ended on a brake and no stop left
    the transport at all. The record says what was ignored, and the pilot is not told off."""
    ex, seen = logged(registry, mock_transport, allow="quack")
    result = await ex.run_verb("stop", sent_with_it)
    assert result.ok, result.summary
    assert mock_transport.stops == 1
    assert ("params", "ignored") in gates(seen)
    assert ends(seen)[0]["outcome"] == "ok"


async def test_only_a_stop_is_let_off_its_arguments(
    registry: VerbRegistry, mock_transport: MockTransport
) -> None:
    ex, seen = logged(registry, mock_transport, allow="walk")
    result = await ex.run_verb("walk", {"vx": 0.1, "duration_s": 0.2, "now": True})
    assert not result.ok and "invalid params" in result.summary
    assert ("params", "refused") in gates(seen)
    assert mock_transport.intents_of("move") == []


async def test_invalid_arguments_are_a_failure_that_counts_in_a_row_and_costs_no_step(
    registry: VerbRegistry, mock_transport: MockTransport
) -> None:
    """A call refused for its arguments returned before it was recorded, so the task's own "same
    verb fails 3 times in a row" never saw it, and a pilot stuck on a speed past the clamp sent it
    again and again until `max_llm_calls` or `max_minutes` ended the run. It is a recorded failure
    now, which that rule counts, and it still uses no step. The pilot is told what it always was."""
    ex, seen = logged(
        registry, mock_transport, allow="walk", abort="Same verb fails 3 times in a row"
    )
    ex.budget = Budget(Budgets(max_steps=40), now=mock_transport.now)
    told: list[VerbResult] = []
    with pytest.raises(Aborted, match="walk failed 3 times in a row"):
        for _ in range(3):
            told.append(await ex.run_verb("walk", {"vx": 0.5, "duration_s": 1.0}))
    assert len(told) == 2 and all(
        not r.ok and r.summary.startswith("invalid params for walk: vx: ") for r in told
    ), told
    assert ex.budget.steps == 0, "a call that ran nothing used a step"
    assert [name for name, _params, _result in ex.history] == ["walk"] * 3
    assert gates(seen) == [("params", "refused")] * 3 + [("abort_when", "fired")]
    assert mock_transport.intents == []


async def test_a_persons_typo_in_drive_is_recorded_and_counts_toward_no_failures_in_a_row(
    registry: VerbRegistry, mock_transport: MockTransport
) -> None:
    """A call refused for its arguments counts toward "the same verb fails N times in a row"
    when a pilot made it, in a run, a drive goal or over MCP. A line a person typed in `roboglot
    drive` (`source="person"`, or `"cli"` off a pipe) is recorded as the failure it is and counts
    toward nothing: a person who mistypes three times has not asked for the session to end, so a
    drive session under a task file with that rule no longer ends on three typos."""
    ex, seen = logged(
        registry, mock_transport, allow="walk", abort="Same verb fails 3 times in a row"
    )
    for source in ("person", "cli", "person", "person"):
        typo = await ex.run_verb("walk", {"vx": 0.5, "duration_s": 1.0}, source=source)
        assert not typo.ok and typo.summary.startswith("invalid params for walk: vx: ")
    assert len(ex.history) == 4 and not ex.abort.is_set()
    assert ex.consecutive_failures.get("move", 0) == 0
    with pytest.raises(Aborted, match="walk failed 3 times in a row"):
        for _ in range(3):  # the pilot's own count is untouched by the person's
            await ex.run_verb("walk", {"vx": 0.5, "duration_s": 1.0})
    assert gates(seen)[-1] == ("abort_when", "fired")


async def test_a_stop_that_cannot_run_without_its_arguments_is_still_refused(
    registry: VerbRegistry, mock_transport: MockTransport
) -> None:
    """A third party's `stop` may ask for something of its own, and there is nothing to run it
    with then: the refusal is the answer, rather than a traceback out of the gate."""

    class Hard(BaseModel):
        model_config = ConfigDict(extra="forbid")
        how: str

    async def hard_stop(_ctx: VerbContext, p: Hard) -> VerbResult:
        return VerbResult.success(f"stopped {p.how}")

    registry.register(Verb("stop", "stops", hard_stop, Hard), replace=True)
    ex, seen = logged(registry, mock_transport, allow="quack")
    assert (await ex.run_verb("stop", {"how": "now"})).summary == "stopped now"
    refused = await ex.run_verb("stop", {"now": True})
    assert not refused.ok and "invalid params" in refused.summary
    assert ("params", "refused") in gates(seen)


# ── the log: the executor says what it decided and what it sent ─────────────────────────


def logged(
    registry: VerbRegistry,
    transport: MockTransport,
    allow: str = "quack, walk, kick",
    **kwargs: str,
) -> tuple[Executor, list[Any]]:
    """An executor whose narration is collected, for the tests below."""
    from roboglot.log import EventLog

    seen: list[Any] = []
    ex = Executor(
        registry,
        transport,
        contract=glot(allow, **kwargs).frontmatter,
        event_log=EventLog(observers=[seen.append]),
    )
    return ex, seen


def gates(seen: list[Any]) -> list[tuple[str, str]]:
    return [(e.data["gate"], e.data["outcome"]) for e in seen if e.kind == "gate"]


def ends(seen: list[Any]) -> list[dict[str, Any]]:
    return [e.data for e in seen if e.kind == "verb_end"]


async def test_a_verb_that_ran_is_bracketed_by_a_start_and_an_end(
    registry: VerbRegistry, mock_transport: MockTransport
) -> None:
    ex, seen = logged(registry, mock_transport)
    await ex.run_verb("quack", {"text": "hi"})
    assert [e.kind for e in seen] == ["verb_start", "intent", "verb_end"]
    assert seen[0].data["name"] == "quack" and seen[0].data["params"] == {"text": "hi"}
    assert seen[1].data["intent"] == "sound"
    assert ends(seen)[0]["outcome"] == "ok" and ends(seen)[0]["intents"] == {"sound": 1}


@pytest.mark.parametrize(
    ("verb", "params", "gate"),
    [("kick", {}, "allowlist"), ("fly", {}, "allowlist"), ("walk", {"vx": 99.0}, "params")],
)
async def test_every_refusal_names_the_rule_that_refused(
    registry: VerbRegistry,
    mock_transport: MockTransport,
    verb: str,
    params: dict[str, Any],
    gate: str,
) -> None:
    """A refusal is a rule, not a bug, and the log has to say which rule."""
    ex, seen = logged(registry, mock_transport, allow="quack, walk")
    with contextlib.suppress(VerbNotAllowed):
        await ex.run_verb(verb, params)
    assert gates(seen) == [(gate, "refused")]
    assert len(ends(seen)) == 1, "a verb that started must always end"


async def test_a_refused_verb_still_ends(
    registry: VerbRegistry, mock_transport: MockTransport
) -> None:
    ex, seen = logged(registry, mock_transport, allow="quack")
    with pytest.raises(VerbNotAllowed):
        await ex.run_verb("kick")
    assert ends(seen)[0]["outcome"] == "refused" and "allowlist" in ends(seen)[0]["summary"]


async def test_the_confirm_gate_records_the_answer(
    registry: VerbRegistry, mock_transport: MockTransport
) -> None:
    ex, seen = logged(registry, mock_transport, allow="quack, kick", confirm="kick")
    ex.confirm = deny_all
    with pytest.raises(ConfirmDenied):
        await ex.run_verb("kick")
    assert gates(seen) == [("confirm", "denied")]
    assert seen[1].data["answer"] is False
    assert ends(seen)[0]["outcome"] == "denied"

    ex2, seen2 = logged(registry, mock_transport, allow="quack, kick", confirm="kick")
    ex2.confirm = allow_all
    assert (await ex2.run_verb("kick")).ok
    assert [e.data["answer"] for e in seen2 if e.kind == "gate"] == [True]
    assert gates(seen2) == [("confirm", "allowed")]


async def test_a_confirm_a_person_answered_is_written_down_beside_the_gate(
    registry: VerbRegistry, mock_transport: MockTransport
) -> None:
    """The gate row holds what was decided. The prompt row holds the question as the person
    saw it, verb and parameters together, which is the only place the record says what they
    were agreeing to rather than what it did to the robot."""
    asked: list[tuple[str, dict[str, Any]]] = []

    def says_yes(name: str, params: dict[str, Any]) -> bool:
        asked.append((name, params))
        return True

    says_yes.asks_a_person = True  # type: ignore[attr-defined]

    ex, seen = logged(registry, mock_transport, allow="quack, kick", confirm="kick")
    ex.confirm = says_yes
    assert (await ex.run_verb("kick", {"leg": "left"})).ok
    assert asked == [("kick", {"leg": "left"})]
    kinds = [e.kind for e in seen]
    assert kinds.index("prompt") < kinds.index("gate"), "asked first, decided after"
    assert [e.data for e in seen if e.kind == "prompt"] == [
        {"what": "confirm", "question": "run kick(leg='left')?", "answer": True}
    ]
    assert gates(seen) == [("confirm", "allowed")]

    def says_no(_name: str, _params: dict[str, Any]) -> bool:
        return False

    says_no.asks_a_person = True  # type: ignore[attr-defined]

    ex2, seen2 = logged(registry, mock_transport, allow="quack, kick", confirm="kick")
    ex2.confirm = says_no
    with pytest.raises(ConfirmDenied):
        await ex2.run_verb("kick")
    assert [e.data["answer"] for e in seen2 if e.kind == "prompt"] == [False]
    assert gates(seen2) == [("confirm", "denied")]


async def test_a_standing_yes_records_the_gate_and_no_question(
    registry: VerbRegistry, mock_transport: MockTransport
) -> None:
    """`--yes` and every team member answer their own confirm gates with nobody in the room.
    The gate row is still true: the verb was allowed. A prompt row would not be, and a record
    that invents a person who said yes to a kick is worse than one that says nothing."""
    ex, seen = logged(registry, mock_transport, allow="quack, kick", confirm="kick")
    ex.confirm = allow_all
    assert (await ex.run_verb("kick")).ok
    assert gates(seen) == [("confirm", "allowed")]
    assert not [e for e in seen if e.kind == "prompt"]


def _confirm_asker(answer: bool, *, person: bool | None = None) -> Any:
    """A confirm callable that answers `answer`, marked the way the CLI marks the three of
    its own that reach a terminal.

    `person` is what that mark says when the question is put: None leaves the asker unmarked,
    which is `--yes`, a team's standing answer and the MCP server."""

    def says(_name: str, _params: dict[str, Any]) -> bool:
        return answer

    def anybody_there() -> bool:
        return bool(person)

    if person is not None:
        says.asks_a_person = anybody_there  # type: ignore[attr-defined]
    return says


def _confirm_gate(seen: list[Any]) -> dict[str, Any]:
    return next(e.data for e in seen if e.kind == "gate" and e.data["gate"] == "confirm")


async def test_a_pipe_on_stdin_opens_the_gate_and_is_not_recorded_as_a_person(
    registry: VerbRegistry, mock_transport: MockTransport
) -> None:
    """`yes | roboglot run` and `roboglot run < answers.txt` reach the same callable a person at
    a keyboard reaches, and `input()` reads a pipe as happily as it reads a person.

    The gate still opens on a yes and still closes on a no, because that is what the pipe
    asked for and what Roboglot has always done. What is withheld is the testimony: a row here
    would be the record saying somebody cleared a kick on a robot nobody was standing next
    to."""
    ex, seen = logged(registry, mock_transport, allow="quack, kick", confirm="kick")
    ex.confirm = _confirm_asker(True, person=False)
    assert (await ex.run_verb("kick")).ok, "the gate opens on a pipe as it always did"
    assert mock_transport.intents_of("do"), "and the kick was really sent"
    assert gates(seen) == [("confirm", "allowed")]
    assert not [e for e in seen if e.kind == "prompt"]

    ex2, seen2 = logged(registry, mock_transport, allow="quack, kick", confirm="kick")
    ex2.confirm = _confirm_asker(False, person=False)
    with pytest.raises(ConfirmDenied):
        await ex2.run_verb("kick")
    assert gates(seen2) == [("confirm", "denied")]
    assert ends(seen2)[0]["outcome"] == "denied", "the consequence is recorded either way"
    assert not [e for e in seen2 if e.kind == "prompt"]


async def test_the_confirm_gate_names_a_human_only_where_one_was_asked(
    registry: VerbRegistry, mock_transport: MockTransport
) -> None:
    """What the gate DID is not in question: it allowed or denied the same verb, and
    `answer` holds the same yes or no, whoever it came from. Only the witness changes.

    `a human said yes` under `--yes`, under a team's standing answer or under a pipe is the
    record naming somebody who was never in the room, and a gate row is exactly what gets
    read back when a run has to be accounted for."""
    rows: dict[tuple[bool, bool], dict[str, Any]] = {}
    for answer in (True, False):
        for person in (True, False):
            ex, seen = logged(registry, mock_transport, allow="quack, kick", confirm="kick")
            ex.confirm = _confirm_asker(answer, person=person)
            with contextlib.suppress(ConfirmDenied):
                await ex.run_verb("kick")
            rows[(answer, person)] = _confirm_gate(seen)

    assert rows[(True, True)]["reason"] == "a human said yes"
    assert rows[(False, True)]["reason"] == "a human said no"
    assert rows[(True, False)]["reason"] == "the confirm gate was allowed"
    assert rows[(False, False)]["reason"] == "the confirm gate was denied"
    for answer in (True, False):
        witnessed, alone = rows[(answer, True)], rows[(answer, False)]
        assert witnessed["outcome"] == alone["outcome"]
        assert witnessed["answer"] == alone["answer"] == answer


async def test_whether_anybody_is_there_is_asked_when_the_question_is_put(
    registry: VerbRegistry, mock_transport: MockTransport
) -> None:
    """The mark is a callable and not a flag because a terminal is not a property of a
    function. The CLI marks one `_confirm_prompt` with `_can_prompt`, and that one function
    is both the one a person answers and the one a pipe answers.

    So one asker, two kicks, and the terminal goes away in between: the first is a person's
    yes and the second is nobody's. A mark read once and kept would have written the same row
    twice."""
    there = [True]

    def anybody_there() -> bool:
        return there[0]

    def says_yes(_name: str, _params: dict[str, Any]) -> bool:
        return True

    says_yes.asks_a_person = anybody_there  # type: ignore[attr-defined]

    ex, seen = logged(registry, mock_transport, allow="quack, kick", confirm="kick")
    ex.confirm = says_yes
    assert (await ex.run_verb("kick")).ok
    there[0] = False
    assert (await ex.run_verb("kick")).ok
    assert gates(seen) == [("confirm", "allowed"), ("confirm", "allowed")], "both opened"
    assert [e.data["answer"] for e in seen if e.kind == "prompt"] == [True], "the first only"
    assert [e.data["reason"] for e in seen if e.kind == "gate"] == [
        "a human said yes",
        "the confirm gate was allowed",
    ]


def _later(answer: bool, put: asyncio.Event | None = None) -> Any:
    """A confirm that answers from a coroutine, as a drive session's goal asks the person
    driving, marked as one that reaches a person. With `put`, it never answers: it sets `put`
    and waits."""

    async def says(_name: str, _params: dict[str, Any]) -> bool:
        await asyncio.sleep(0)
        if put is not None:
            put.set()
            await asyncio.Event().wait()
        return answer

    says.asks_a_person = True  # type: ignore[attr-defined]
    return says


async def test_a_confirm_answered_later_is_waited_for(
    registry: VerbRegistry, mock_transport: MockTransport
) -> None:
    """A coroutine is truthy: read without waiting, a no given later was a yes nobody gave, and
    the kick ran. The executor waits for it, a yes opens the gate and a no shuts it."""
    ex, seen = logged(registry, mock_transport, allow="quack, kick", confirm="kick")
    ex.confirm = _later(True)
    assert (await ex.run_verb("kick")).ok
    assert mock_transport.intents_of("do"), "the kick was sent on the yes"
    assert _confirm_gate(seen)["reason"] == "a human said yes"
    assert [e.data["answer"] for e in seen if e.kind == "prompt"] == [True]

    transport = MockTransport()
    ex2, seen2 = logged(registry, transport, allow="quack, kick", confirm="kick")
    ex2.confirm = _later(False)
    with pytest.raises(ConfirmDenied, match="human declined kick"):
        await ex2.run_verb("kick")
    assert not transport.intents_of("do"), "and nothing was sent on the no"
    assert _confirm_gate(seen2)["reason"] == "a human said no"


async def test_a_confirm_still_waiting_when_the_run_is_stopped_was_answered_by_nobody(
    registry: VerbRegistry, mock_transport: MockTransport
) -> None:
    """A brake, a newer line or the page's Stop while the question waits takes it back. Nobody
    answered, so no `prompt` row says somebody did, and nothing moves. The call ends as the
    abort ends any call, `run aborted` at the `abort` gate (`QuestionTakenBack`, an `Aborted`),
    and the run on why the abort was set, as a pilot's doubt taken back the same way does. It
    used to end denied, which put the question in the place of the stop."""
    put = asyncio.Event()
    ex, seen = logged(registry, mock_transport, allow="quack, kick", confirm="kick")
    ex.confirm = _later(True, put)
    running = asyncio.ensure_future(ex.run_verb("kick"))
    await asyncio.wait_for(put.wait(), timeout=2)
    ex.abort.set()
    with pytest.raises(QuestionTakenBack, match=r"^run aborted$"):
        await asyncio.wait_for(running, timeout=2)
    assert gates(seen) == [("abort", "refused")]
    assert ends(seen)[0]["outcome"] == "aborted"
    assert not [e for e in seen if e.kind == "prompt"]
    assert not mock_transport.intents_of("do")


async def test_a_confirm_its_composite_ran_out_of_time_on_was_answered_by_nobody(
    registry: VerbRegistry, mock_transport: MockTransport
) -> None:
    """A nested verb's question waits inside its composite's own time, and a composite that ran
    out of it cancelled the question with no gate row at all: the record said a kick ended,
    never that nobody had been asked to clear it, or had answered."""

    async def combo(ctx: VerbContext, _p: NoParams) -> VerbResult:
        assert ctx.run_verb is not None
        return await ctx.run_verb("kick", {"leg": "left"})

    registry.register(Verb("combo", "walk up and kick", combo, NoParams, timeout_s=0.2))
    ex, seen = logged(registry, mock_transport, allow="combo, kick", confirm="kick")
    ex.confirm = _later(True, asyncio.Event())
    result = await asyncio.wait_for(ex.run_verb("combo"), timeout=10)
    assert not result.ok
    gate = _confirm_gate(seen)
    assert (gate["outcome"], gate["answer"]) == ("denied", None)
    assert gate["reason"] == "the prompt raised CancelledError"
    assert not mock_transport.intents_of("do")


# ── a dangerous verb needs a person (ADR-0056) ──────────────────────────────────────────


def _dangerous(registry: VerbRegistry) -> list[str]:
    """A verb in the `dangerous` class, which records each time it really runs."""
    ran: list[str] = []

    async def nuke(_ctx: VerbContext, _p: NoParams) -> VerbResult:
        ran.append("nuke")
        return VerbResult.success("nuked")

    registry.register(Verb("nuke", "dangerous", nuke, safety_class="dangerous"))
    return ran


def _counting(asker: Any, calls: list[str]) -> Any:
    """`asker`, with its mark, recording that it was put the question at all."""

    def asks(name: str, params: dict[str, Any]) -> bool:
        calls.append(name)
        return bool(asker(name, params))

    if hasattr(asker, "asks_a_person"):
        asks.asks_a_person = asker.asks_a_person  # type: ignore[attr-defined]
    return asks


@pytest.mark.parametrize(
    "asker",
    [allow_all, deny_all, _confirm_asker(True), _confirm_asker(True, person=False)],
    ids=[
        "--yes and a team's standing answer",
        "the MCP server without --yes",
        "an asker nobody marked",
        "a pipe on stdin",
    ],
)
async def test_a_dangerous_verb_is_refused_where_nobody_is_asked(
    registry: VerbRegistry, mock_transport: MockTransport, asker: Any
) -> None:
    """`--yes`, a team's standing answer, the MCP server and a pipe answer without anybody
    there, so a dangerous verb is refused before its question is even put, with a gate that
    names the fix, and it never runs."""
    ran = _dangerous(registry)
    calls: list[str] = []
    ex, seen = logged(registry, mock_transport, allow="nuke")
    ex.confirm = _counting(asker, calls)
    with pytest.raises(VerbNotAllowed, match="only a person can clear it") as refusal:
        await ex.run_verb("nuke")
    assert "run it from a terminal, without --yes" in str(refusal.value)
    assert ran == [] and calls == [], "refused before the question was put"
    assert gates(seen) == [("confirm", "refused")]
    assert not [e for e in seen if e.kind == "prompt"], "nobody was asked, so nothing says so"


async def test_a_dangerous_verb_runs_when_a_person_answers_yes_and_not_when_they_say_no(
    registry: VerbRegistry, mock_transport: MockTransport
) -> None:
    ran = _dangerous(registry)
    ex, seen = logged(registry, mock_transport, allow="nuke")
    ex.confirm = _confirm_asker(True, person=True)
    assert (await ex.run_verb("nuke")).ok
    assert ran == ["nuke"]
    assert gates(seen) == [("confirm", "allowed")]
    assert [e.data["answer"] for e in seen if e.kind == "prompt"] == [True]

    ex2, seen2 = logged(registry, mock_transport, allow="nuke")
    ex2.confirm = _confirm_asker(False, person=True)
    with pytest.raises(ConfirmDenied):
        await ex2.run_verb("nuke")
    assert ran == ["nuke"], "a person who says no is still a no"
    assert gates(seen2) == [("confirm", "denied")]


async def test_a_confirm_verb_still_opens_on_a_standing_yes(
    registry: VerbRegistry, mock_transport: MockTransport
) -> None:
    """The rule is the dangerous class's alone: a `confirm` verb under `--yes` runs as it did."""
    ex, seen = logged(registry, mock_transport, allow="quack, kick", confirm="kick")
    ex.confirm = allow_all
    assert (await ex.run_verb("kick")).ok
    assert gates(seen) == [("confirm", "allowed")]


async def test_the_budget_gate_fires_before_anything_is_sent(
    registry: VerbRegistry, mock_transport: MockTransport
) -> None:
    ex, seen = logged(registry, mock_transport)
    ex.budget = Budget(Budgets(max_steps=1))
    ex.budget.start()
    assert (await ex.run_verb("quack")).ok
    with pytest.raises(BudgetExceeded):
        await ex.run_verb("quack")
    assert ("budget", "exceeded") in gates(seen)
    assert ends(seen)[-1]["outcome"] == "budget"
    assert len(mock_transport.intents_of("sound")) == 1


async def test_the_dry_run_gate_shows_what_it_would_have_sent(
    registry: VerbRegistry, mock_transport: MockTransport
) -> None:
    """The docs have always promised --dry-run shows every intent an LLM would send. The
    intent is never built, so the gate carrying the parsed params is what makes that true."""
    ex, seen = logged(registry, mock_transport)
    ex.dry_run = True
    assert (await ex.run_verb("walk", {"vx": 0.2, "duration_s": 1.0})).ok
    gate = next(e for e in seen if e.kind == "gate")
    assert gate.data["gate"] == "dry_run" and gate.data["params"]["vx"] == 0.2
    assert mock_transport.intents == []
    assert ends(seen)[0]["intents"] == {}


async def test_a_precondition_refusal_carries_the_state_that_caused_it(
    registry: VerbRegistry,
) -> None:
    transport = MockTransport(states=[RobotState(policy="p", posture="fallen", fallen=True)])
    ex, seen = logged(registry, transport)
    result = await ex.run_verb("walk", {"vx": 0.1})
    assert not result.ok
    gate = next(e for e in seen if e.kind == "gate")
    assert gate.data["gate"] == "precondition" and "fallen" in gate.data["state"]
    assert ends(seen)[0]["outcome"] == "fail"


async def test_the_repeat_failure_abort_names_the_last_failure(registry: VerbRegistry) -> None:
    transport = MockTransport(refuse_kinds={"sound"})
    ex, seen = logged(registry, transport, allow="quack", abort="Same verb fails 2 times in a row")
    await ex.run_verb("quack")
    with pytest.raises(Aborted):
        await ex.run_verb("quack")
    fired = [e for e in seen if e.kind == "gate" and e.data["gate"] == "abort_when"]
    assert fired and "failed 2 times" in fired[0].data["reason"] and fired[0].data["last"]
    assert len(ends(seen)) == 2, "both attempts started and both ended"


async def test_a_verb_aborted_mid_flight_still_ends_and_the_stop_is_logged(
    registry: VerbRegistry, mock_transport: MockTransport
) -> None:
    """A heartbeat failure or a kill switch cancels the running verb. That is the case a
    reader most needs to see, and the stop it sends is the most important intent there is."""

    async def long_walk(ctx: VerbContext, _p: NoParams) -> VerbResult:
        for _ in range(500):
            await ctx.transport.send_intent(Intent.move(0.1, 0.0, 0.0))
            await asyncio.sleep(0.01)
        return VerbResult.success("finished on its own")

    registry.register(Verb("long_walk", "walks a long way", long_walk, timeout_s=30))
    ex, seen = logged(registry, mock_transport, allow="long_walk")
    running = asyncio.create_task(ex.run_verb("long_walk"))
    await asyncio.sleep(0.1)
    ex.abort.set()
    with pytest.raises(Aborted):
        await asyncio.wait_for(running, timeout=2.0)

    assert ends(seen)[0]["outcome"] == "aborted"
    assert ("abort", "fired") in gates(seen)
    assert [e for e in seen if e.kind == "intent" and e.data["intent"] == "stop"], (
        "the stop that took the legs back has to be in the log"
    )


async def test_a_verb_that_times_out_ends_and_stops(
    registry: VerbRegistry, mock_transport: MockTransport
) -> None:
    async def hangs(ctx: VerbContext, _p: NoParams) -> VerbResult:
        await asyncio.sleep(10)
        return VerbResult.success("never")

    registry.register(Verb("hangs", "hangs", hangs, timeout_s=0.05))
    ex, seen = logged(registry, mock_transport, allow="hangs")
    result = await ex.run_verb("hangs")
    assert not result.ok and "timed out" in result.summary
    assert ends(seen)[0]["outcome"] == "fail"
    assert [e.data["intent"] for e in seen if e.kind == "intent"] == ["stop"]


async def test_a_composite_reports_the_intents_its_nested_verbs_sent(
    registry: VerbRegistry, mock_transport: MockTransport
) -> None:
    """`approach_and` sends nothing itself. A parent that reported zero intents would be the
    misleading kind of true."""

    async def both(ctx: VerbContext, _p: NoParams) -> VerbResult:
        assert ctx.run_verb is not None
        await ctx.run_verb("quack", {"text": "one"})
        await ctx.run_verb("quack", {"text": "two"})
        return VerbResult.success("did both")

    registry.register(Verb("both", "runs two verbs", both))
    ex, seen = logged(registry, mock_transport, allow="both, quack")
    assert (await ex.run_verb("both", {}, source="mcp")).ok
    starts = [e.data for e in seen if e.kind == "verb_start"]
    assert [s["name"] for s in starts] == ["both", "quack", "quack"]
    assert [s["nested"] for s in starts] == [False, True, True]
    # a nested call inside an MCP session is still an MCP call, not an agent one
    assert {s["source"] for s in starts} == {"mcp"}
    parent = next(e for e in ends(seen) if e["name"] == "both")
    assert parent["intents"] == {"sound": 2}


async def test_the_heartbeats_stop_is_logged_too() -> None:
    from roboglot.log import EventLog

    seen: list[Any] = []
    transport = MockTransport(fail_heartbeat_after=0)
    abort = asyncio.Event()
    beat = Heartbeat(transport, abort, period_s=0.001, event_log=EventLog(observers=[seen.append]))
    beat.start()
    await asyncio.wait_for(abort.wait(), timeout=2.0)
    await beat.stop()
    assert any(e.kind == "note" and "heartbeat failed" in e.data["text"] for e in seen)
    assert any(e.kind == "intent" and e.data["intent"] == "stop" for e in seen)


async def test_verb_end_carries_the_robots_clock_beside_the_wall_clock(
    registry: VerbRegistry, mock_transport: MockTransport
) -> None:
    """The mock's clock advances only on `sleep`, so a one second walk is one robot second
    and almost no wall time."""
    ex, seen = logged(registry, mock_transport, allow="walk")
    await ex.run_verb("walk", {"vx": 0.1, "duration_s": 1.0})
    end = ends(seen)[0]
    assert end["transport_s"] == pytest.approx(1.0)
    assert end["elapsed_s"] < 0.5
    assert "clock" not in end, "the record carries both clocks and labels neither"


async def test_a_verb_that_logs_is_a_note_in_the_log_and_still_reaches_log(
    registry: VerbRegistry, mock_transport: MockTransport
) -> None:
    """`ctx.log` was wired straight to `ex.log`, so a verb that logged was the one thing the
    log could not see. `ex.log` is a contract other callers read, so the note is additional."""
    lines: list[str] = []

    async def looks(ctx: VerbContext, _p: NoParams) -> VerbResult:
        ctx.log("looking left")
        return VerbResult.success("looked")

    registry.register(Verb("looks", "looks around", looks))
    ex, seen = logged(registry, mock_transport, allow="looks")
    ex.log = lines.append
    assert (await ex.run_verb("looks")).ok
    assert "looking left" in lines
    notes = [e for e in seen if e.kind == "note"]
    assert [n.data["text"] for n in notes] == ["looking left"]
    assert seen.index(notes[0]) > 0 and seen[-1].kind == "verb_end"


async def test_a_confirm_prompt_that_raises_is_a_denial_that_names_the_exception(
    registry: VerbRegistry, mock_transport: MockTransport
) -> None:
    """typer's y/N prompt raises click's `Abort` on Ctrl-C, whose `str()` is empty. That used
    to escape as `verb_end error "Abort: "` with no gate at all, so the log never recorded
    that a human had been asked. Denied, and answered by nobody: the gate's `answer` is None,
    never False, and the pilot is told nobody answered rather than that a human declined."""

    class Abort(RuntimeError):  # the shape click's own Abort has
        pass

    def raises(_name: str, _params: dict[str, Any]) -> bool:
        raise Abort

    ex, seen = logged(registry, mock_transport, allow="quack, kick", confirm="kick")
    ex.confirm = raises
    with pytest.raises(ConfirmDenied, match=r"^nobody answered whether to run kick \(.*Abort"):
        await ex.run_verb("kick")
    assert gates(seen) == [("confirm", "denied")]
    assert "Abort" in seen[1].data["reason"] and seen[1].data["answer"] is None
    assert ends(seen)[0]["outcome"] == "denied"
    assert mock_transport.intents_of("do") == []


async def test_a_state_read_that_fails_still_sends_a_stop(registry: VerbRegistry) -> None:
    """Every other in-verb failure stops the robot. A link that died one line earlier, while
    reading the state the gates need, was the one that did not."""

    class Dead(MockTransport):
        async def get_state(self) -> RobotState:
            raise ConnectionError("the link is gone")

    transport = Dead()
    ex, seen = logged(registry, transport, allow="quack")
    with pytest.raises(ConnectionError):
        await ex.run_verb("quack")
    assert transport.stops == 1
    assert ends(seen)[0]["outcome"] == "error"


async def test_a_broken_record_sink_cannot_stop_the_heartbeat_from_aborting() -> None:
    """The abort is the one thing that must happen when the link dies. A log sink that
    raised inside the failure handler used to kill the heartbeat task with the abort unset,
    and then explode again at the first line of the loop's teardown."""
    from roboglot.log import EventLog

    def broken(_event: Any) -> None:
        raise OSError("disk full")

    transport = MockTransport(fail_heartbeat_after=0)
    abort = asyncio.Event()
    beat = Heartbeat(transport, abort, period_s=0.001, event_log=EventLog(record=broken))
    beat.start()
    await asyncio.wait_for(abort.wait(), timeout=2.0)
    await beat.stop()  # must not re-raise the sink's error
    assert transport.stops >= 1


async def test_cancelling_the_call_cancels_the_verb_and_sends_a_stop(
    registry: VerbRegistry, mock_transport: MockTransport
) -> None:
    """An MCP client that drops a call, or the second Ctrl-C this CLI documents, cancels the
    caller. `asyncio.wait` cancels nothing when it is itself cancelled, so the verb used to
    keep running: the log said the verb had ended and then went on recording the intents it
    kept sending, with no stop anywhere."""

    async def long_walk(ctx: VerbContext, _p: NoParams) -> VerbResult:
        for _ in range(500):
            await ctx.transport.send_intent(Intent.move(0.1, 0.0, 0.0))
            await asyncio.sleep(0.01)
        return VerbResult.success("finished on its own")

    registry.register(Verb("long_walk", "walks a long way", long_walk, timeout_s=30))
    ex, seen = logged(registry, mock_transport, allow="long_walk")
    running = asyncio.create_task(ex.run_verb("long_walk"))
    await asyncio.sleep(0.1)
    assert len(mock_transport.intents_of("move")) >= 1

    running.cancel()
    with pytest.raises(asyncio.CancelledError):
        await running

    assert mock_transport.intents[-1].kind == "stop", "a cancelled call must leave a stop"
    assert ("cancelled", "fired") in gates(seen)
    assert ends(seen)[0]["outcome"] == "aborted"
    assert seen[-1].kind == "verb_end", "no intent may arrive after the verb ended"
    settled = len(mock_transport.intents_of("move"))
    await asyncio.sleep(0.2)
    assert len(mock_transport.intents_of("move")) == settled, "the verb must really be gone"


def _walking(registry: VerbRegistry) -> VerbRegistry:
    async def long_walk(ctx: VerbContext, _p: NoParams) -> VerbResult:
        for _ in range(500):
            await ctx.transport.send_intent(Intent.move(0.1, 0.0, 0.0))
            await asyncio.sleep(0.01)
        return VerbResult.success("finished on its own")

    registry.register(Verb("long_walk", "walks a long way", long_walk, timeout_s=30))
    return registry


async def _cancelled_while_walking(ex: Executor) -> asyncio.Task[Any]:
    running = asyncio.create_task(ex.run_verb("long_walk"))
    await asyncio.sleep(0.05)
    running.cancel()
    await asyncio.wait({running}, timeout=2.0)
    return running


async def test_a_cancelled_call_says_whether_its_stop_landed_and_only_after_sending_it() -> None:
    """The gate of a call cancelled from outside said "a stop was sent" before the stop went,
    and then never read whether it had landed: the record said a stop was sent beside a stop
    row that says it was not."""
    seen: list[Any] = []
    ex = Executor(
        _walking(VerbRegistry()),
        _UnheardStop(),
        contract=glot("long_walk").frontmatter,
        event_log=EventLog(observers=[seen.append]),
    )
    running = await _cancelled_while_walking(ex)
    assert running.cancelled()
    order = [
        "gate"
        if e.kind == "gate" and e.data["gate"] == "cancelled"
        else "stop"
        if e.kind == "intent" and e.data["intent"] == "stop"
        else None
        for e in seen
    ]
    order = [o for o in order if o]
    assert order == ["stop", "gate"], f"the gate is not written after the stop: {order}"
    gate = next(e.data for e in seen if e.kind == "gate" and e.data["gate"] == "cancelled")
    assert gate["reason"] == f"the call was cancelled; the verb was cancelled, and {UNHEARD}"


async def test_a_record_that_cannot_be_written_for_a_cancelled_call_keeps_it_cancelled() -> None:
    """A record sink that raised on the cancelled gate (a full disk) replaced the cancel with its
    own error, and the run went on from a failed result that blamed the verb for it."""

    def record(event: Any) -> None:
        if event.kind == "gate" and event.data.get("gate") == "cancelled":
            raise OSError(28, "No space left on device")

    transport = MockTransport()
    said: list[str] = []
    ex = Executor(
        _walking(VerbRegistry()),
        transport,
        contract=glot("long_walk").frontmatter,
        event_log=EventLog(record=record),
        log=said.append,
    )
    running = await _cancelled_while_walking(ex)
    assert running.done()
    assert running.cancelled(), f"the cancelled call ended in {_how_it_ended(running)}"
    assert transport.intents[-1].kind == "stop"
    assert any("No space left on device" in s for s in said), said


#: A verb's own timeout well inside every bound the tests below wait on.
CLEANUP_TIMEOUT_S = 0.05


def _cleaning_verb(cleaning: asyncio.Event, release: asyncio.Event) -> Any:
    """A verb whose cancel takes a round trip of its own, a stop of its own or a record, and
    which is in that round trip when its caller is cancelled."""

    async def careful(ctx: VerbContext, _p: NoParams) -> VerbResult:
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            cleaning.set()
            await release.wait()
            raise
        return VerbResult.success("never")

    return careful


def _how_it_ended(running: asyncio.Task[Any]) -> str:
    if running.cancelled():
        return "cancelled"
    if running.exception() is not None:
        return repr(running.exception())
    return repr(running.result().summary)


@pytest.mark.parametrize("ended_by", ["abort", "timeout"])
async def test_a_cancel_of_the_caller_while_the_verb_is_being_ended_is_not_eaten(
    ended_by: str,
) -> None:
    """Once the abort or the verb's own timeout had ended the wait, the executor awaited the verb
    it cancelled inside a suppress that also took a cancel aimed at its caller. An MCP client that
    gave up on the call, or a second Ctrl-C, in that window was lost: the caller got `Aborted` or
    an ordinary timed out result and carried on."""
    cleaning, release = asyncio.Event(), asyncio.Event()
    timeout_s = CLEANUP_TIMEOUT_S if ended_by == "timeout" else 30
    reg = VerbRegistry()
    reg.register(Verb("careful", "c", _cleaning_verb(cleaning, release), timeout_s=timeout_s))
    transport = MockTransport()
    ex = Executor(reg, transport, contract=glot("careful").frontmatter)
    running = asyncio.create_task(ex.run_verb("careful"))
    try:
        if ended_by == "abort":
            await asyncio.sleep(0.01)
            ex.abort.set()
        await asyncio.wait_for(cleaning.wait(), timeout=2.0)
        running.cancel()
        release.set()
        await asyncio.wait({running}, timeout=2.0)
        assert running.done()
        assert running.cancelled(), f"the caller's cancel was eaten: {_how_it_ended(running)}"
        assert transport.intents[-1].kind == "stop"
    finally:
        await _ended(running)


async def test_a_cancel_of_the_caller_in_the_turn_a_plain_verb_ends_is_not_eaten() -> None:
    """A verb with no clean-up of its own opens the same window for one turn of the loop, between
    its cancel and its end."""
    box: list[asyncio.Task[Any]] = []

    async def plain(ctx: VerbContext, _p: NoParams) -> VerbResult:
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            box[0].cancel()  # the caller's cancel arrives in this turn, nothing awaited
            raise
        return VerbResult.success("never")

    reg = VerbRegistry()
    reg.register(Verb("plain", "p", plain, timeout_s=CLEANUP_TIMEOUT_S))
    ex = Executor(reg, MockTransport(), contract=glot("plain").frontmatter)
    running = asyncio.create_task(ex.run_verb("plain"))
    box.append(running)
    try:
        await asyncio.wait({running}, timeout=2.0)
        assert running.done()
        assert running.cancelled(), f"the caller's cancel was eaten: {_how_it_ended(running)}"
    finally:
        await _ended(running)


async def test_a_composite_takes_no_step_after_its_own_timeout_ended_its_nested_call() -> None:
    """The same window inside the project: a composite's nested call was in its own timeout's
    clean-up when the composite's timeout cancelled it. The nested call ate that cancel, and the
    composite went on to its next step and sent a twist after its own timeout."""
    cleaning, release = asyncio.Event(), asyncio.Event()
    transport = MockTransport()

    async def outer(ctx: VerbContext, _p: NoParams) -> VerbResult:
        assert ctx.run_verb is not None
        await ctx.run_verb("careful", {})
        await ctx.transport.send_intent(Intent.move(0.1, 0.0, 0.0))  # the composite's next step
        return VerbResult.success("went on")

    reg = VerbRegistry()
    reg.register(
        Verb("careful", "c", _cleaning_verb(cleaning, release), timeout_s=CLEANUP_TIMEOUT_S)
    )
    reg.register(Verb("outer", "o", outer, timeout_s=2 * CLEANUP_TIMEOUT_S))
    ex = Executor(reg, transport, contract=glot("outer, careful").frontmatter)
    running = asyncio.create_task(ex.run_verb("outer"))
    try:
        await asyncio.wait_for(cleaning.wait(), timeout=2.0)
        await asyncio.sleep(2 * CLEANUP_TIMEOUT_S)  # the composite's own timeout passes
        release.set()
        await asyncio.wait({running}, timeout=2.0)
        sent = [i.kind for i in transport.intents]
        assert "move" not in sent, f"the composite went on after its own timeout: {sent}"
        result = running.result()
        assert not result.ok and "timed out" in result.summary
    finally:
        await _ended(running)


class _QueuedStop(MockTransport):
    """A stop that waits for the bus before it writes, as one queued on the arm's lock behind
    the cancelled verb's own call does; the write is what `intents` records."""

    def __init__(self) -> None:
        super().__init__()
        self.asked = asyncio.Event()
        self.bus_free = asyncio.Event()

    async def stop(self) -> None:
        self.asked.set()
        await self.bus_free.wait()
        await super().stop()


@pytest.mark.parametrize("ended_by", ["abort", "timeout"])
async def test_the_stop_owed_after_an_ended_verb_survives_a_cancel_of_the_caller(
    ended_by: str,
) -> None:
    """The stop the executor owes a verb it ended caught `Exception` only, so a cancel of the
    caller while that stop waited its turn cancelled the stop too: the verb had been ended and
    no stop went out at all, which an MCP session has no teardown stop to make up for."""
    transport = _QueuedStop()

    async def slow(ctx: VerbContext, _p: NoParams) -> VerbResult:
        await asyncio.sleep(30)
        return VerbResult.success("never")

    reg = VerbRegistry()
    timeout_s = CLEANUP_TIMEOUT_S if ended_by == "timeout" else 30
    reg.register(Verb("slow", "s", slow, timeout_s=timeout_s))
    said: list[str] = []
    ex = Executor(reg, transport, contract=glot("slow").frontmatter, log=said.append)
    running = asyncio.create_task(ex.run_verb("slow"))
    try:
        if ended_by == "abort":
            await asyncio.sleep(0.01)
            ex.abort.set()
        await asyncio.wait_for(transport.asked.wait(), timeout=2.0)
        running.cancel()  # an MCP client gives up on the call while the stop waits its turn
        await asyncio.wait({running}, timeout=2.0)
        assert running.cancelled(), f"the caller's cancel was lost: {_how_it_ended(running)}"
        transport.bus_free.set()
        for _ in range(5):
            await asyncio.sleep(0)
        sent = [i.kind for i in transport.intents]
        assert sent == ["stop"], f"the stop owed after the {ended_by} never went out: {sent}"
        assert not [s for s in said if "stop owed" in s], said
    finally:
        transport.bus_free.set()
        await _ended(running)


async def test_a_cancel_of_the_caller_while_the_verb_still_runs_is_kept() -> None:
    """The control for the three above: a cancel before the abort or the timeout was always a
    cancel."""

    async def slow(ctx: VerbContext, _p: NoParams) -> VerbResult:
        await asyncio.sleep(30)
        return VerbResult.success("never")

    reg = VerbRegistry()
    reg.register(Verb("slow", "s", slow, timeout_s=30))
    ex = Executor(reg, MockTransport(), contract=glot("slow").frontmatter)
    running = asyncio.create_task(ex.run_verb("slow"))
    await asyncio.sleep(0.01)
    running.cancel()
    await asyncio.wait({running}, timeout=2.0)
    assert running.cancelled()


async def test_the_mid_verb_abort_gate_names_the_verb_as_it_was_called(
    registry: VerbRegistry, mock_transport: MockTransport
) -> None:
    """`verb_start` says `walk`, so the gate that ends it must not say `move`."""

    async def long_move(ctx: VerbContext, _p: NoParams) -> VerbResult:
        for _ in range(500):
            await ctx.transport.send_intent(Intent.move(0.1, 0.0, 0.0))
            await asyncio.sleep(0.01)
        return VerbResult.success("finished on its own")

    registry.register(Verb("move", "walks", long_move, timeout_s=30), replace=True)
    ex, seen = logged(registry, mock_transport, allow="walk")
    running = asyncio.create_task(ex.run_verb("walk"))
    await asyncio.sleep(0.1)
    ex.abort.set()
    with pytest.raises(Aborted):
        await asyncio.wait_for(running, timeout=2.0)

    fired = next(e for e in seen if e.kind == "gate" and e.data["gate"] == "abort")
    started = next(e for e in seen if e.kind == "verb_start")
    assert fired.data["name"] == started.data["name"] == "walk"


async def test_two_verbs_running_at_once_on_one_executor_count_only_their_own_intents(
    registry: VerbRegistry, mock_transport: MockTransport
) -> None:
    """The MCP SDK runs every tool call as its own task on one robot's executor. With a tally
    stack on the executor the two calls credited each other: a `quack` that overlapped a
    `move` reported the move's resends as its own, and the move reported neither its ten
    resends nor its stop."""
    ex, seen = logged(registry, mock_transport, allow="quack, walk")
    await asyncio.gather(
        ex.run_verb("walk", {"vx": 0.1, "duration_s": 1.0}),
        ex.run_verb("quack", {"text": "hi"}),
    )
    by_name = {e["name"]: e["intents"] for e in ends(seen)}
    assert by_name["quack"] == {"sound": 1}
    assert by_name["walk"] == {"move": 10, "stop": 1}


async def test_a_record_sink_that_fails_on_verb_start_still_ends_the_verb(
    registry: VerbRegistry, mock_transport: MockTransport
) -> None:
    """The record's failure is the run's, but it must not also leave a verb that started and
    never ended (the tally frame used to leak onto a shared stack too)."""
    from roboglot.log import EventLog

    seen: list[Any] = []

    def record(event: Any) -> None:
        if event.kind == "verb_start":
            raise OSError("disk full")

    ex = Executor(
        registry,
        mock_transport,
        contract=glot("quack").frontmatter,
        event_log=EventLog(record=record, observers=[seen.append]),
    )
    with pytest.raises(OSError, match="disk full"):
        await ex.run_verb("quack")
    assert [e.data["outcome"] for e in seen if e.kind == "verb_end"] == ["error"]


async def test_without_a_event_log_the_executor_is_silent_and_hands_over_the_real_transport(
    registry: VerbRegistry, mock_transport: MockTransport
) -> None:
    """The team and every direct user build an Executor with no event log, and get exactly
    what they got before."""
    ex = Executor(registry, mock_transport, contract=glot("quack").frontmatter)
    assert ex.logged_transport() is mock_transport
    assert ex.context().transport is mock_transport
    assert (await ex.run_verb("quack")).ok


async def test_a_safety_stop_from_another_layer_ends_the_verb_with_its_own_word(
    registry: VerbRegistry, mock_transport: MockTransport
) -> None:
    """A newer command preempts an in-flight verb by raising a `SafetyStop` subclass, as drive
    does. Before this the log called that `error`, which the docs define as a bug, and every
    handover printed a red line about nothing being wrong."""

    class Preempted(SafetyStop):
        outcome = "preempted"

    async def handover(_ctx: VerbContext, _p: NoParams) -> VerbResult:
        raise Preempted("stopped for a new goal")

    registry.register(Verb("handover", "gives way", handover, timeout_s=5))
    event_log = EventLog()
    seen: list[Any] = []
    event_log.add(seen.append)
    ex = Executor(
        registry, mock_transport, contract=glot("handover").frontmatter, event_log=event_log
    )
    with pytest.raises(Preempted):
        await ex.run_verb("handover")

    (end,) = [e for e in seen if e.kind == "verb_end"]
    assert end.data["outcome"] == "preempted"
    assert end.data["summary"] == "stopped for a new goal"


# ── what a verb saw, when the body has more than one lens ───────────────────────────────


class _TwoCameras(MockTransport):
    """A body whose views are called `top` and `side`, with `top` the one bearings come off.

    Injected rather than patched onto the mock: `frames_of` asks a transport for
    `get_frames`, and a transport that has one is the whole of what makes a body
    multi-camera.
    """

    def __init__(self) -> None:
        super().__init__()
        self.top = Image.new("RGB", (8, 8), (200, 60, 30))
        self.side = Image.new("RGB", (8, 8), (30, 60, 200))

    async def get_frame(self) -> Image.Image:
        return self.top

    async def get_frames(self) -> list[CameraFrame]:
        return [CameraFrame("top", self.top, primary=True), CameraFrame("side", self.side)]


async def test_observe_hands_the_primary_to_on_frame_and_every_frame_to_on_frames(
    registry: VerbRegistry,
) -> None:
    """Two hooks because a two-camera body has no single picture. Whoever records what the
    verb saw wants all of them; whoever steers wants the one the detections describe, and a
    bearing is only meaningful off the lens `--fov-deg` measured, so handing the steering
    hook whichever view was read last would point it somewhere nobody looked.
    """
    transport = _TwoCameras()
    steered: list[tuple[Any, str]] = []
    saved: list[tuple[list[str], str]] = []
    ex = Executor(
        registry,
        transport,
        contract=glot("observe").frontmatter,
        on_frame=lambda img, caption: steered.append((img, caption)),
        on_frames=lambda frames, caption: saved.append(([f.name for f in frames], caption)),
    )
    result = await ex.run_verb("observe")
    assert result.ok, result.summary
    assert steered == [(transport.top, "observe")], "the steering hook gets the primary alone"
    assert saved == [(["top", "side"], "observe")], "and the recorder gets both, primary first"
    assert result.summary == "frames captured from top, side; nothing detected"
    assert result.data["cameras"] == ["top", "side"]


async def test_a_single_camera_observe_says_exactly_what_it_said_before_there_were_several(
    registry: VerbRegistry, mock_transport: MockTransport
) -> None:
    """The one-camera wording is what every pilot, every transcript and every golden line
    has read since 0.1, and a body with one lens is still almost every body. No camera name
    in the summary and no `cameras` key in the payload: the default name exists so the code
    has one to carry, and is never spoken to an LLM."""
    saved: list[list[str]] = []
    ex = Executor(
        registry,
        mock_transport,
        contract=glot("observe").frontmatter,
        on_frames=lambda frames, _caption: saved.append([f.name for f in frames]),
    )
    result = await ex.run_verb("observe")
    assert result.summary == "frame captured; nothing detected"
    assert "cameras" not in result.data
    assert saved == [[DEFAULT_CAMERA_NAME]]


async def _walk_braked(ex: Executor, transport: MockTransport, twists: int) -> VerbResult:
    """A walk with `stop` asked for beside it, through the same executor, once it has sent
    `twists` twists, as an MCP client or a person driving the robot would ask for it. Every
    intent sent after that stop has returned is checked to be no twist."""
    sent = len(transport.intents_of("move"))
    walking = asyncio.create_task(ex.run_verb("walk", {"vx": 0.1, "duration_s": 1.0}))
    for _ in range(1000):
        if len(transport.intents_of("move")) >= sent + twists:
            break
        await asyncio.sleep(0)
    braked = await ex.run_verb("stop")
    assert braked.ok, braked.summary
    after = len(transport.intents)
    result = await walking
    assert not [i for i in transport.intents[after:] if i.kind == "move"], "a twist after the stop"
    return result


async def test_a_stop_from_beside_a_walk_ends_it_before_its_next_resend(
    registry: VerbRegistry, mock_transport: MockTransport
) -> None:
    """`move` resends its twist every tenth of a second, and a stop asked for beside it was
    undone by the next resend, the walk going on for the rest of its duration. It ends at its
    next slice now without sending it, and says how far it got."""
    ex = Executor(registry, mock_transport, contract=glot("walk").frontmatter)
    result = await _walk_braked(ex, mock_transport, twists=3)
    assert not result.ok
    assert result.summary.startswith(f"move: {STOPPED}, so it stopped after ")
    assert result.data["ended_by"] == STOP_ENDED and result.data["interrupted"] == STOPPED
    assert result.data["duration_s"] < 1.0


async def test_a_walk_a_stop_ended_is_no_failure_for_abort_when_to_count(
    registry: VerbRegistry, mock_transport: MockTransport
) -> None:
    """A person braking a walk three times is the brake working three times, and `abort_when`
    passes over it as it passes over a pause, where it would have ended the run on the same verb
    failing twice in a row."""
    contract = glot("walk", abort="the same verb fails 2 times in a row").frontmatter
    ex = Executor(registry, mock_transport, contract=contract)
    for _ in range(3):
        result = await _walk_braked(ex, mock_transport, twists=2)
        assert result.data["ended_by"] == STOP_ENDED, result.summary
    assert ex.consecutive_failures.get("move", 0) == 0


class _SlowState(MockTransport):
    """A body whose first state read waits until the test lets it answer, so a verb can be held
    admitted, past every gate, and not yet moving."""

    def __init__(self) -> None:
        super().__init__()
        self.reading = asyncio.Event()
        self.answer = asyncio.Event()
        self._held = False

    async def get_state(self) -> RobotState:
        if not self._held:
            self._held = True
            self.reading.set()
            await self.answer.wait()
        return await super().get_state()


async def test_a_newer_command_ends_the_walk_it_finds_moving(
    registry: VerbRegistry, mock_transport: MockTransport
) -> None:
    """`preempt` is a granted pause with no hold after it: the walk moving the body is ended
    before its next twist, a stop is sent after it, and the walk's caller is told it was a newer
    command, which `abort_when` never counts."""
    ex, seen = logged(
        registry, mock_transport, allow="walk", abort="the same verb fails 1 time in a row"
    )
    walking = asyncio.create_task(ex.run_verb("walk", {"vx": 0.1, "duration_s": 1.0}))
    for _ in range(1000):
        if len(mock_transport.intents_of("move")) >= 2:
            break
        await asyncio.sleep(0)
    assert ex.preempt("a newer command, turn left") == 1
    after = len(mock_transport.intents)
    with pytest.raises(Preempted, match="walk ended: a newer command, turn left"):
        await walking
    assert [i.kind for i in mock_transport.intents[after:]] == ["stop"], "it moved again"
    (end,) = ends(seen)
    assert end["outcome"] == "preempted"
    assert ex.consecutive_failures.get("move", 0) == 0


async def test_a_newer_command_leaves_a_read_running_beside_it(registry: VerbRegistry) -> None:
    """A read moves nothing, so it is not the newer command's to end, as it is not a pause's."""
    reading, answer = asyncio.Event(), asyncio.Event()

    async def slow_state(_ctx: VerbContext, _p: NoParams) -> VerbResult:
        reading.set()
        await answer.wait()
        return VerbResult.success("standing, battery 88%")

    registry.register(Verb("report_state", "reads the body", slow_state), replace=True)
    ex = Executor(registry, MockTransport(), contract=glot("report_state").frontmatter)
    read = asyncio.create_task(ex.run_verb("report_state"))
    await asyncio.wait_for(reading.wait(), 5)
    assert ex.preempt("a newer command") == 0
    answer.set()
    assert (await read).summary == "standing, battery 88%"


async def test_a_walk_admitted_before_a_newer_command_does_not_start_after_it(
    registry: VerbRegistry,
) -> None:
    """A walk past every gate and still reading the body's state is in nothing `preempt` can end,
    and would start after the command that replaced it had. It is refused once the read returns,
    before it sends anything."""
    body = _SlowState()
    ex = Executor(registry, body, contract=glot("walk").frontmatter)
    walking = asyncio.create_task(ex.run_verb("walk", {"vx": 0.1, "duration_s": 1.0}))
    await asyncio.wait_for(body.reading.wait(), 5)
    assert ex.preempt("a newer command, sit") == 0
    body.answer.set()
    with pytest.raises(Preempted, match="walk did not start: a newer command, sit"):
        await walking
    assert body.intents_of("move") == [], "it started after the newer command"


async def test_a_stop_asked_for_while_a_walk_reads_the_bodys_state_ends_the_walk(
    registry: VerbRegistry,
) -> None:
    """Over MCP a stop that came while a walk was past its gates and still reading the body's
    state landed in the count the walk started from, so the walk ran its whole second after
    the stop had come back ok."""
    body = _SlowState()
    ex = Executor(registry, body, contract=glot("walk").frontmatter)
    walking = asyncio.create_task(ex.run_verb("walk", {"vx": 0.1, "duration_s": 1.0}))
    await asyncio.wait_for(body.reading.wait(), 5)
    assert (await ex.run_verb("stop")).ok
    after = len(body.intents)
    body.answer.set()
    await walking
    twists = [i for i in body.intents[after:] if i.kind == "move" and any(i.params.values())]
    assert twists == [], "the walk went on after the stop"


async def test_an_abort_set_while_a_walk_reads_the_bodys_state_refuses_the_walk(
    registry: VerbRegistry,
) -> None:
    """A kill switch, a failed heartbeat, or a drive session's brake on a goal, set while a walk
    read the body's state, found nothing it could cancel, and the walk sent its first twist
    after the abort."""
    body = _SlowState()
    ex = Executor(registry, body, contract=glot("walk").frontmatter)
    walking = asyncio.create_task(ex.run_verb("walk", {"vx": 0.1, "duration_s": 1.0}))
    await asyncio.wait_for(body.reading.wait(), 5)
    ex.abort.set()
    body.answer.set()
    with pytest.raises(Aborted):
        await walking
    assert body.intents_of("move") == []


async def test_a_verdict_withdrawn_while_a_walk_reads_the_bodys_state_refuses_the_walk(
    registry: VerbRegistry,
) -> None:
    """After the state read the executor looked again at a pause, a newer command, the abort and
    the stops, which can each change during that read, but not at the verdict: one recorded as
    infeasible meanwhile, as a second MCP call can, let the walk through."""
    body = _SlowState()
    ex = Executor(registry, body, require_verdict=True)
    ex.verdict = Verdict(verdict="feasible", reason="it walks")
    walking = asyncio.create_task(ex.run_verb("walk", {"vx": 0.1, "duration_s": 0.2}))
    await asyncio.wait_for(body.reading.wait(), 5)
    ex.verdict = Verdict(verdict="infeasible", reason="the floor is a staircase")
    body.answer.set()
    with pytest.raises(VerdictRequired, match="the floor is a staircase"):
        await asyncio.wait_for(walking, 10)
    assert body.intents_of("move") == []


async def test_a_battery_the_rule_reads_as_too_low_still_lets_the_stop_through(
    registry: VerbRegistry,
) -> None:
    """The rule is there to stop the body: the `stop` it refused was a stop never sent, and
    every call after it is refused as before."""
    body = MockTransport(states=[RobotState(policy="mock", posture="standing", battery_percent=5)])
    ex = Executor(registry, body, contract=glot("walk", abort="Battery below 15%").frontmatter)
    assert (await ex.run_verb("stop")).ok
    assert body.stops == 1, "the stop was refused before it was sent"
    assert ex.abort.is_set()
    with pytest.raises(Aborted):
        await ex.run_verb("walk", {"vx": 0.1})


async def test_a_stop_between_two_of_a_composites_calls_ends_the_second_before_it_starts(
    registry: VerbRegistry, mock_transport: MockTransport
) -> None:
    """A composite's nested call took a count of stops of its own when it was admitted, so a stop
    sent between two of the composite's calls was already in the count the second started from,
    and it walked its whole walk after the brake. It answers to the composite's count now, and is
    not started at all."""
    first_done = asyncio.Event()
    go_on = asyncio.Event()
    second: list[VerbResult] = []

    async def two_walks(ctx: VerbContext, _: NoParams) -> VerbResult:
        assert ctx.run_verb is not None
        await ctx.run_verb("walk", {"vx": 0.1, "duration_s": 0.2})
        first_done.set()
        await go_on.wait()
        # no look at `ctx.interrupted()` here, on purpose: the nested call has to hold the line
        # by itself, for a composite that does not
        second.append(await ctx.run_verb("walk", {"vx": 0.1, "duration_s": 0.5}))
        return second[-1]

    registry.register(Verb("two_walks", "two walks", two_walks, timeout_s=10))
    ex, seen = logged(registry, mock_transport, allow="two_walks, walk")
    running = asyncio.create_task(ex.run_verb("two_walks"))
    await asyncio.wait_for(first_done.wait(), 5)
    assert (await ex.run_verb("stop")).ok
    after = len(mock_transport.intents)
    go_on.set()
    result = await asyncio.wait_for(running, 5)
    assert not [i for i in mock_transport.intents[after:] if i.kind == "move"], "walked after it"
    assert second and second[0].summary == f"walk did not start: {STOPPED}"
    assert second[0].data["ended_by"] == STOP_ENDED
    assert ("stopped", "refused") in gates(seen)
    assert not result.ok


def _battery_falls() -> list[RobotState]:
    """The admission's read, then the one a verb takes mid-way, below a 15% line."""
    return [
        RobotState(policy="mock", posture="standing", battery_percent=40),
        RobotState(policy="mock", posture="standing", battery_percent=10),
    ]


async def _long_walk(ctx: VerbContext, _: NoParams) -> VerbResult:
    """A twist, the rules read against the state at a stop, and a twist the rules should forbid."""
    await ctx.transport.send_intent(Intent.move(0.1, 0.0, 0.0))
    await ctx.abort_when(await ctx.transport.get_state())
    await ctx.transport.send_intent(Intent.move(0.1, 0.0, 0.0))
    return VerbResult.success("walked on past the rule")


class _RoundTripStop(MockTransport):
    """A stop that takes a round trip, as every real link's does, and `delay_s` on top."""

    def __init__(self, delay_s: float = 0.0, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.delay_s = delay_s

    async def stop(self) -> None:
        for _ in range(3):
            await asyncio.sleep(0)
        if self.delay_s:
            await asyncio.sleep(self.delay_s)
        await super().stop()


async def test_a_rule_a_verb_reads_mid_way_stops_the_body_and_then_aborts_the_run(
    registry: VerbRegistry,
) -> None:
    """The executor read `abort_when` when it admitted a verb and not again until the next, so a
    verb that walked for minutes never heard the battery fall below the task's line. One that
    reads it at a stop (`VerbContext.abort_when`) is stopped where it stands, and then the run is
    aborted, in the words the admission's gate uses. The stop takes a round trip here, as a real
    one does: an abort set before it went out would race the verb and cancel it half sent."""
    body = _RoundTripStop(states=_battery_falls())
    registry.register(Verb("long_walk", "walks for a while", _long_walk, timeout_s=10))
    ex, seen = logged(registry, body, allow="long_walk", abort="Battery below 15%")
    with pytest.raises(Aborted, match="battery 10% below 15%; the verb stopped where it stood"):
        await ex.run_verb("long_walk")
    assert ex.abort.is_set()
    assert [i.kind for i in body.intents] == ["move", "stop"], "a twist after it, or no stop"
    fired = [e.data for e in seen if e.kind == "gate" and e.data["gate"] == "abort_when"]
    assert fired and fired[0]["name"] == "long_walk" and fired[0]["outcome"] == "fired"


async def test_a_rule_that_fired_aborts_the_run_however_its_stop_ends(
    registry: VerbRegistry,
) -> None:
    """The verb's own clock ran out while the stop the rule asked for was still on its way. The
    gate said the rule had fired, and the run was not aborted: the next verb would have walked."""
    body = _RoundTripStop(delay_s=0.5, states=_battery_falls())
    registry.register(Verb("long_walk", "walks for a while", _long_walk, timeout_s=0.2))
    ex, _ = logged(registry, body, allow="long_walk", abort="Battery below 15%")
    result = await ex.run_verb("long_walk")
    assert not result.ok
    assert ex.abort.is_set(), "the rule fired and the run went on"


async def test_a_composites_call_that_only_looks_still_runs_after_a_stop(
    registry: VerbRegistry, mock_transport: MockTransport
) -> None:
    """A stop is a brake. It ends what would move the body, and a call that reads the body's
    state or speaks runs on beside it, as it runs beside a pause."""

    async def walk_then_look(ctx: VerbContext, _: NoParams) -> VerbResult:
        assert ctx.run_verb is not None
        await ctx.run_verb("stop", {})
        return await ctx.run_verb("report_state", {})

    registry.register(Verb("walk_then_look", "d", walk_then_look, timeout_s=10, kind="composite"))
    ex, seen = logged(registry, mock_transport, allow="walk_then_look, report_state")
    result = await ex.run_verb("walk_then_look")
    assert result.ok, result.summary
    assert ("stopped", "refused") not in gates(seen)


async def test_nobody_is_asked_about_a_composites_call_a_stop_overtook(
    registry: VerbRegistry, mock_transport: MockTransport
) -> None:
    """The confirm gate came before the stop was looked at, so a person was asked whether to
    kick, said yes, and the kick was then refused anyway: a question answered for nothing."""
    asked: list[str] = []
    first_done, go_on = asyncio.Event(), asyncio.Event()

    async def walk_then_kick(ctx: VerbContext, _: NoParams) -> VerbResult:
        assert ctx.run_verb is not None
        await ctx.run_verb("walk", {"vx": 0.1, "duration_s": 0.2})
        first_done.set()
        await go_on.wait()
        return await ctx.run_verb("kick", {})

    def confirm(name: str, _params: dict[str, Any]) -> bool:
        asked.append(name)
        return True

    registry.register(Verb("walk_then_kick", "d", walk_then_kick, timeout_s=10, kind="composite"))
    ex = Executor(
        registry,
        mock_transport,
        contract=glot("walk_then_kick, walk, kick", confirm="kick").frontmatter,
        confirm=confirm,
    )
    running = asyncio.create_task(ex.run_verb("walk_then_kick"))
    await asyncio.wait_for(first_done.wait(), 5)
    assert (await ex.run_verb("stop")).ok
    go_on.set()
    result = await asyncio.wait_for(running, 5)
    assert asked == [], "a person was asked about a kick that was never going to start"
    assert result.summary == f"kick did not start: {STOPPED}"
    assert not mock_transport.intents_of("do")


class _BrakedRead(MockTransport):
    """A body whose `nth` state read sends a stop from beside the run before it answers, as an
    MCP client braking while a verb is admitted would."""

    def __init__(self, nth: int) -> None:
        super().__init__()
        self.nth = nth
        self.reads = 0
        self.executor: Executor | None = None

    async def get_state(self) -> RobotState:
        self.reads += 1
        if self.reads == self.nth and self.executor is not None:
            assert (await self.executor.run_verb("stop")).ok
        return await super().get_state()


async def test_a_stop_while_a_composites_kick_reads_the_body_keeps_the_kick_from_starting(
    registry: VerbRegistry,
) -> None:
    """The kick sends its one intent without looking for a stop, so a stop that came while it
    read the body's state, after every gate, has to end it before it starts."""
    body = _BrakedRead(nth=2)  # the composite's admission reads first, the kick's second

    async def just_kick(ctx: VerbContext, _: NoParams) -> VerbResult:
        assert ctx.run_verb is not None
        return await ctx.run_verb("kick", {})

    registry.register(Verb("just_kick", "d", just_kick, timeout_s=10, kind="composite"))
    ex, seen = logged(registry, body, allow="just_kick, kick")
    body.executor = ex
    result = await ex.run_verb("just_kick")
    assert not body.intents_of("do"), "kicked after the brake"
    assert result.data["ended_by"] == STOP_ENDED
    assert ("stopped", "refused") in gates(seen)


class _BallAhead:
    """A detector that sees the ball right in front, close enough to kick."""

    name = "ball_ahead"

    def detect(self, image: Image.Image) -> list[Detection]:
        return [
            Detection(label="ball", cx=0.5, cy=0.5, area=0.2, bearing_deg=0.0, est_distance_m=0.05)
        ]


class _BrakeBeforeTheKick(MockTransport):
    """Sends a stop from beside the run during the first state read after each `go_to` is in
    the record: the kick's own admission inside `approach_and`, after approach_and's check."""

    def __init__(self) -> None:
        super().__init__()
        self.executor: Executor | None = None
        self.braked_for = 0

    async def get_state(self) -> RobotState:
        if (ex := self.executor) is not None:
            walks = sum(1 for name, _params, _result in ex.history if name == "go_to")
            if walks > self.braked_for:
                self.braked_for = walks
                assert (await ex.run_verb("stop")).ok
        return await super().get_state()


async def test_an_approach_whose_kick_a_stop_kept_from_starting_is_no_failure_in_a_row(
    registry: VerbRegistry,
) -> None:
    """approach_and wrapped its kick's result without the kick's `ended_by`, so a person who
    braked it twice ended the run on approach_and failing twice in a row."""
    body = _BrakeBeforeTheKick()
    ex = Executor(
        registry,
        body,
        contract=glot(
            "walk_to, kick, approach_and", abort="Same verb fails 2 times in a row"
        ).frontmatter,
        detector=_BallAhead(),
    )
    body.executor = ex
    params = {"target": "ball", "then": "kick"}
    for _ in range(2):
        result = await ex.run_verb("approach_and", params)
        assert result.data["ended_by"] == STOP_ENDED, result.summary
    assert not body.intents_of("do"), "kicked after the brake"
    assert not ex.abort.is_set()
    assert ex.consecutive_failures.get("approach_and", 0) == 0


# ── what an ended verb leaves behind: nested calls, its stop, the record, the process ────────


async def _kicks_once(ctx: VerbContext, _p: NoParams) -> VerbResult:
    """One intent and done, as `kick` sends its skill."""
    await ctx.transport.send_intent(Intent.do("kick"))
    return VerbResult.success("kicked")


async def _approach_then_kick(ctx: VerbContext, _p: NoParams) -> VerbResult:
    """A composite whose step is a nested call, as `approach_and`'s `then` is."""
    assert ctx.run_verb is not None
    return await ctx.run_verb("kicks_once", {})


#: How long the composite below may run, and how long its nested call's read takes.
COMPOSITE_S = ENDING_S
NESTED_READ_S = 10 * ENDING_S


def _kick_after_approach(composite_s: float = COMPOSITE_S) -> VerbRegistry:
    reg = VerbRegistry()
    reg.register(Verb("kicks_once", "kicks", _kicks_once, timeout_s=NESTED_READ_S))
    reg.register(
        Verb("approach_then_kick", "approaches", _approach_then_kick, timeout_s=composite_s)
    )
    return reg


#: The task a composite and its nested kick run under.
KICK_AFTER_APPROACH = "approach_then_kick, kicks_once"


class _NestedReadLosesOneCancel(MockTransport):
    """The composite's own admission read answers at once; the nested call's is still out when
    the composite is ended, and answers instead of raising that cancel, as Python 3.11's
    `wait_for` does when the answer lands with it."""

    def __init__(self) -> None:
        super().__init__()
        self.reads = 0
        self.lost = 0

    async def get_state(self) -> RobotState:
        self.reads += 1
        if self.reads == 2:
            try:
                await asyncio.sleep(NESTED_READ_S)
            except asyncio.CancelledError:
                self.lost += 1
        return await super().get_state()


async def test_no_kick_goes_out_after_the_composite_that_asked_for_it_timed_out() -> None:
    """A nested call still reading the body's state when its composite's own timeout ended the
    composite, and whose read lost that cancel, started on a leash of its own that nothing had
    ended: the record said the composite had timed out and stopped, and the kick went out."""
    transport = _NestedReadLosesOneCancel()
    ex = Executor(_kick_after_approach(), transport, contract=glot(KICK_AFTER_APPROACH).frontmatter)
    running = asyncio.create_task(ex.run_verb("approach_then_kick"))
    try:
        await asyncio.wait({running}, timeout=COMPOSITE_S + 4 * ENDING_S)
        assert transport.lost == 1, "the stand-in did not get the cancel it swallows"
        assert running.done()
        result = running.result()
        assert "timed out" in result.summary, result.summary
        kinds = [i.kind for i in transport.intents]
        assert transport.intents_of("do") == [], f"the kick went out after the end: {kinds}"
    finally:
        await _ended(running)


async def test_a_nested_call_still_reading_when_the_abort_comes_is_refused_as_well() -> None:
    """The control for the test above: the abort is read again after that read, as it was."""
    transport = _NestedReadLosesOneCancel()
    ex = Executor(
        _kick_after_approach(NESTED_READ_S),
        transport,
        contract=glot(KICK_AFTER_APPROACH).frontmatter,
    )
    running = asyncio.create_task(ex.run_verb("approach_then_kick"))
    try:
        await asyncio.sleep(ENDING_S / 3)
        assert transport.reads == 2, "the nested call is not reading yet"
        ex.abort.set()
        await asyncio.wait({running}, timeout=4 * ENDING_S)
        assert transport.lost == 1 and running.done()
        assert transport.intents_of("do") == []
    finally:
        await _ended(running)


async def test_no_kick_goes_out_after_its_composite_timed_out_over_wait_for(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No stand-in loses anything here: the nested call's read goes through
    `wait_for(run_in_executor(...))`, the shape of the LeRobot camera call, and Python 3.11 loses
    the composite's cancel in it. On 3.12 nothing is lost, so only a 3.11 job can fail this."""
    asked, go = threading.Event(), threading.Event()
    reads: list[int] = []

    def read() -> None:
        asked.set()
        go.wait(10.0)

    class ThroughWaitFor(MockTransport):
        async def get_state(self) -> RobotState:
            reads.append(1)
            if len(reads) == 2:
                loop = asyncio.get_running_loop()
                await asyncio.wait_for(loop.run_in_executor(None, read), timeout=5.0)
            return await super().get_state()

    real_end = safety._Leash.end
    held: list[bool] = []

    def end(self: Any, why: str) -> None:
        real_end(self, why)
        if not held:  # the composite's: the read's answer lands, and is queued, before its cancel
            held.append(True)
            go.set()
            _block_the_loop(ENDING_S)

    monkeypatch.setattr(safety._Leash, "end", end)
    transport = ThroughWaitFor()
    ex = Executor(_kick_after_approach(), transport, contract=glot(KICK_AFTER_APPROACH).frontmatter)
    running = asyncio.create_task(ex.run_verb("approach_then_kick"))
    try:
        assert await asyncio.to_thread(asked.wait, 5.0), "the nested call's read is out"
        await asyncio.wait({running}, timeout=COMPOSITE_S + 4 * ENDING_S)
        assert held, "the composite was never ended"
        assert running.done()
        kinds = [i.kind for i in transport.intents]
        assert transport.intents_of("do") == [], (
            f"python {sys.version_info[:2]}: the kick went out after the composite timed out: "
            f"{kinds}"
        )
    finally:
        go.set()
        await _ended(running)


#: A watch that sends nothing, polled this often, for this long; and the bound an ended verb
#: has to be ended and stopped within: its two waits of `ENDING_S` and one more.
POLL_S = ENDING_S / 10
WATCH_S = 20 * ENDING_S
HALT_BOUND_S = 3 * ENDING_S


class _SleepLosesOneCancel(MockTransport):
    """A wall-clock body whose `sleep` swallows the first cancel it is handed, as a verb that
    polls a state or a segment the transport runs loses one over 3.11's `wait_for`."""

    def __init__(self) -> None:
        super().__init__()
        self.lost = 0
        self.out = asyncio.Event()

    async def send_intent(self, intent: Intent) -> Ack:
        ack = await super().send_intent(intent)
        if intent.kind == "move":
            self.out.set()
        return ack

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        try:
            await asyncio.sleep(seconds)
        except asyncio.CancelledError:
            if self.lost:
                raise
            self.lost += 1


async def _watcher(ctx: VerbContext, _p: NoParams) -> VerbResult:
    """One twist, then a watch that sends nothing, polling the transport."""
    await ctx.transport.send_intent(Intent.move(0.1, 0.0, 0.0))
    started = time.monotonic()
    while time.monotonic() - started < WATCH_S:
        await ctx.transport.sleep(POLL_S)
    return VerbResult.success("watched to the end")


@pytest.mark.parametrize("how", ["preempt", "pause", "abort"])
async def test_a_newer_command_or_a_pause_ends_a_verb_that_lost_its_cancel_within_the_bound(
    how: str,
) -> None:
    """A pause granted to a robot met, or a newer command, sent one cancel and then waited on the
    verb with no bound but its own timeout, so one that lost the cancel and sent nothing held
    the stop owed after it, and drive's take-over behind it, for that long. They end it as the
    abort does now (the abort's case is the control)."""
    transport = _SleepLosesOneCancel()
    reg = VerbRegistry()
    reg.register(Verb("watcher", "watches", _watcher, timeout_s=2 * WATCH_S))
    ex = Executor(reg, transport, contract=glot("watcher").frontmatter)
    running = asyncio.create_task(ex.run_verb("watcher"))
    try:
        await asyncio.wait_for(transport.out.wait(), 2.0)
        await asyncio.sleep(POLL_S / 2)  # in its watch now
        if how == "preempt":
            assert ex.preempt("a newer command") == 1
        elif how == "pause":
            ex.pause_for(WATCH_S)
        else:
            ex.abort.set()
        await asyncio.wait({running}, timeout=HALT_BOUND_S)
        assert transport.lost == 1, "the stand-in did not get the cancel it swallows"
        assert running.done(), f"the verb {how} ended still runs; stop sent: {transport.stops}"
        if how == "preempt":
            assert isinstance(running.exception(), Preempted)
        elif how == "pause":
            assert running.result().data.get("ended_by") == PAUSE_ENDED
        else:
            assert isinstance(running.exception(), Aborted)
        assert transport.intents[-1].kind == "stop"
    finally:
        await _ended(running)


async def test_a_newer_command_ends_a_verb_whose_cancel_wait_for_lost_within_the_bound() -> None:
    """No stand-in loses anything here: the twist's answer goes through `wait_for(to_thread)`,
    and the newer command lands in the turn Python 3.11 loses it in. Only a 3.11 job can fail."""
    loop = asyncio.get_running_loop()
    fetched, go = threading.Event(), threading.Event()
    box: list[Executor] = []

    def answer() -> None:
        if not fetched.is_set():
            fetched.set()
            go.wait(10.0)

            # two turns after the answer is queued, as the abort's cancel lands
            def second() -> None:
                loop.call_soon(box[0].preempt, "a newer command")

            loop.call_soon_threadsafe(loop.call_soon, second)

    class ThroughWaitFor(MockTransport):
        async def send_intent(self, intent: Intent) -> Ack:
            self.intents.append(intent)
            await asyncio.wait_for(asyncio.to_thread(answer), timeout=5.0)
            return Ack()

        async def sleep(self, seconds: float) -> None:
            self.sleeps.append(seconds)
            await asyncio.sleep(seconds)

    transport = ThroughWaitFor()
    reg = VerbRegistry()
    reg.register(Verb("watcher", "watches", _watcher, timeout_s=2 * WATCH_S))
    ex = Executor(reg, transport, contract=glot("watcher").frontmatter)
    box.append(ex)
    running = asyncio.create_task(ex.run_verb("watcher"))
    try:
        assert await asyncio.to_thread(fetched.wait, 5.0), "the twist is out"
        go.set()
        _block_the_loop(ENDING_S)  # the newer command and the twist's answer queue up in order
        await asyncio.wait({running}, timeout=HALT_BOUND_S)
        assert running.done(), f"python {sys.version_info[:2]}: the verb still runs"
        assert isinstance(running.exception(), Preempted)
    finally:
        go.set()
        await _ended(running)


#: What "the stop goes out without waiting for the verb" is held to: a third of `ENDING_S`.
AT_ONCE_S = ENDING_S / 3
FETCH_S = 20 * ENDING_S


class _ClockedStop(MockTransport):
    def __init__(self) -> None:
        super().__init__()
        self.stopped_at: float | None = None
        self.fetching = asyncio.Event()

    async def stop(self) -> None:
        if self.stopped_at is None:
            self.stopped_at = time.monotonic()
        await super().stop()


class _FetchLosesOneCancel(_ClockedStop):
    """A frame fetch that catches the first cancel and fetches again, as Python 3.11's
    `wait_for` does over a fetch whose frame lands with the cancel."""

    def __init__(self) -> None:
        super().__init__()
        self.lost = 0

    async def get_frame(self) -> Image.Image | None:
        self.fetching.set()
        try:
            await asyncio.sleep(FETCH_S)
        except asyncio.CancelledError:
            self.lost += 1
            await asyncio.sleep(FETCH_S)  # the next fetch; the second cancel ends this one
        return None


async def _twist_then_look(ctx: VerbContext, _p: NoParams) -> VerbResult:
    """A twist, then a look before the next one, as `go_to` steers."""
    await ctx.transport.send_intent(Intent.move(0.1, 0.0, 0.0))
    await ctx.transport.get_frame()
    await ctx.transport.send_intent(Intent.move(0.1, 0.0, 0.0))
    return VerbResult.success("looked")


async def test_the_stop_after_an_abort_goes_out_without_waiting_for_the_verb() -> None:
    """The stop waited up to twice `ENDING_S` for the verb, which protected nothing once the
    leash held its sends, while a body with no deadman of its own (a rosbridge base, a ToddlerBot
    whose keepalive feeds its own) drove on its last twist."""
    transport = _FetchLosesOneCancel()
    reg = VerbRegistry()
    reg.register(Verb("looker", "looks", _twist_then_look, timeout_s=2 * FETCH_S))
    ex = Executor(reg, transport, contract=glot("looker").frontmatter)
    running = asyncio.create_task(ex.run_verb("looker"))
    try:
        await asyncio.wait_for(transport.fetching.wait(), 2.0)
        aborted_at = time.monotonic()
        ex.abort.set()
        await asyncio.wait({running}, timeout=4 * ENDING_S)
        assert transport.lost == 1, "the stand-in did not get the cancel it swallows"
        assert running.done() and isinstance(running.exception(), Aborted)
        assert len(transport.intents_of("move")) == 1, "the leash let a twist through"
        assert transport.stopped_at is not None
        late = transport.stopped_at - aborted_at
        assert late <= AT_ONCE_S, f"the stop went out {late:.2f} s after the abort"
    finally:
        await _ended(running)


async def test_the_stop_after_an_abort_goes_out_at_once_over_wait_for() -> None:
    """No stand-in loses anything here: the fetch goes through `wait_for(to_thread)` and the
    abort lands in the turn its frame does. Only a 3.11 job can fail this."""
    loop = asyncio.get_running_loop()
    fetched, go = threading.Event(), threading.Event()
    box: list[Executor] = []
    frames: list[int] = []

    def fetch() -> None:
        fetched.set()
        go.wait(10.0)
        loop.call_soon_threadsafe(box[0].abort.set)  # ahead of the frame's own answer

    class ThroughWaitFor(_ClockedStop):
        async def get_frame(self) -> Image.Image | None:
            frames.append(1)
            if len(frames) == 1:
                await asyncio.wait_for(asyncio.to_thread(fetch), timeout=5.0)
            await asyncio.sleep(FETCH_S)  # what the verb does next sends nothing
            return None

    async def looks(ctx: VerbContext, _p: NoParams) -> VerbResult:
        await ctx.transport.send_intent(Intent.move(0.1, 0.0, 0.0))
        await ctx.transport.get_frame()
        return VerbResult.success("looked")

    reg = VerbRegistry()
    reg.register(Verb("looker", "looks", looks, timeout_s=2 * FETCH_S))
    transport = ThroughWaitFor()
    ex = Executor(reg, transport, contract=glot("looker").frontmatter)
    box.append(ex)
    running = asyncio.create_task(ex.run_verb("looker"))
    try:
        assert await asyncio.to_thread(fetched.wait, 5.0), "the fetch is out"
        go.set()
        _block_the_loop(ENDING_S)  # the abort and the frame queue up in that order
        free_at = time.monotonic()
        await asyncio.wait({running}, timeout=4 * ENDING_S)
        assert running.done() and isinstance(running.exception(), Aborted)
        assert transport.stopped_at is not None
        late = transport.stopped_at - free_at
        assert late <= AT_ONCE_S, (
            f"python {sys.version_info[:2]}: the stop went out {late:.2f} s after the loop was free"
        )
    finally:
        go.set()
        await _ended(running)


class _DeadLinkStop(MockTransport):
    """A link whose beat fails and whose stop never comes back, as a write into a full send
    buffer or a serial write that never completes does."""

    def __init__(self) -> None:
        super().__init__()
        self.never = asyncio.Event()

    async def heartbeat(self) -> None:
        raise HeartbeatError("no answer from the robot")

    async def stop(self) -> None:
        self.stops += 1
        await self.never.wait()


async def test_a_call_a_failed_heartbeat_ended_returns_though_the_link_eats_its_stop() -> None:
    """The heartbeat's wait on its own stop was bounded and the call it aborted was not: that
    call waited on its owed stop over the same dead link with no end, so the run never reached
    its teardown. The owed stop is waited for as long as the body gives its own `stop` verb
    (here `ENDING_S`), and the call says the stop was not confirmed."""

    async def long_walk(ctx: VerbContext, _p: NoParams) -> VerbResult:
        for _ in range(100):
            await ctx.transport.send_intent(Intent.move(0.1, 0.0, 0.0))
            await asyncio.sleep(MOVE_RESEND_S)
        return VerbResult.success("walked")

    reg = VerbRegistry()
    reg.register(Verb("long_walk", "walks", long_walk, timeout_s=60))
    reg.register(dataclasses.replace(CORE["stop"], timeout_s=ENDING_S))
    transport = _DeadLinkStop()
    ex = Executor(reg, transport, contract=glot("long_walk").frontmatter)
    running = asyncio.create_task(ex.run_verb("long_walk"))
    await asyncio.sleep(MOVE_RESEND_S / 2)
    assert transport.intents_of("move"), "the walk is not under way"
    hb = Heartbeat(transport, ex.abort, period_s=MOVE_RESEND_S)
    hb.start()
    bound = MOVE_RESEND_S + ENDING_S + 2 * ENDING_S + 3 * MOVE_RESEND_S
    try:
        await asyncio.wait({running}, timeout=bound)
        assert ex.abort.is_set(), "the heartbeat did not abort"
        assert running.done(), f"{bound:.1f} s on the call still waits on its stop"
        aborted = running.exception()
        assert isinstance(aborted, Aborted)
        assert "not answered within" in str(aborted), aborted
    finally:
        transport.never.set()
        await hb.stop()
        await asyncio.wait({running}, timeout=1.0)


#: How long the verb that will not end below runs before it ends by itself.
STUBBORN_S = 30 * ENDING_S

LEFT_BEHIND_PROGRAM = """
import asyncio, sys, time
from roboglot.glotfile.parser import parse_glot_text
from roboglot.safety import Aborted, Executor
from roboglot.transport.mock import MockTransport
from roboglot.verbs.registry import NoParams, Verb, VerbRegistry, VerbResult

STUBBORN_S = float(sys.argv[1])
GLOT = '''---
glot: 0
name: t
description: d
verbs:
  allow: [stubborn]
  confirm: []
success: [x]
abort_when: []
---
# Task
x
'''

async def stubborn(ctx, _p):
    until = time.monotonic() + STUBBORN_S
    while time.monotonic() < until:
        try:
            await asyncio.sleep(0.02)
        except asyncio.CancelledError:
            pass
    return VerbResult.success("ended by itself")

async def main():
    reg = VerbRegistry()
    reg.register(Verb("stubborn", "will not end", stubborn, timeout_s=10 * STUBBORN_S))
    ex = Executor(reg, MockTransport(), contract=parse_glot_text(GLOT).frontmatter)
    running = asyncio.ensure_future(ex.run_verb("stubborn"))
    await asyncio.sleep(0.05)
    ex.abort.set()
    try:
        await running
    except Aborted:
        pass
    print("RETURNED", time.monotonic(), flush=True)

asyncio.run(main())
print("EXITED", time.monotonic(), flush=True)
"""


def test_a_verb_left_behind_does_not_hold_the_process_at_the_loops_close() -> None:
    """`asyncio.run`, which every command runs under, cancels every task left at its close and
    then waits for all of them with no bound, so a verb the executor had left behind held the
    process after the run until it ended by itself, or for good. Run in a child Python under
    `asyncio.run`, as the CLI does."""
    import subprocess

    # UTF-8 on the child's pipes: the executor's log has an arrow in it, and a pipe on Windows
    # is the ANSI codepage unless the child is told otherwise
    done = subprocess.run(
        [sys.executable, "-X", "utf8", "-c", LEFT_BEHIND_PROGRAM, str(STUBBORN_S)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=STUBBORN_S + 60,
    )
    lines = dict(line.split(" ", 1) for line in done.stdout.split("\n") if " " in line)
    assert "RETURNED" in lines, f"the call never returned: {done.stdout!r} {done.stderr[-400:]!r}"
    assert "EXITED" in lines, done.stderr[-400:]
    held = float(lines["EXITED"]) - float(lines["RETURNED"])
    assert held < 2 * ENDING_S, f"the loop's close held the process {held:.1f} s"


async def test_a_verb_left_behind_cannot_land_a_late_stop_in_the_next_move(
    registry: VerbRegistry,
) -> None:
    """The leash let every stop through, so a verb the executor had left behind, once it ended
    at last, landed its own `ctx.transport.stop()` in whatever ran then: the next verb, or the
    run's fold. Once the executor's own stop for it has gone, its leash lets nothing through."""
    go_late = asyncio.Event()
    late: list[str] = []

    async def stubborn(ctx: VerbContext, _p: NoParams) -> VerbResult:
        while not go_late.is_set():
            with contextlib.suppress(asyncio.CancelledError):
                await go_late.wait()
        try:
            await ctx.transport.stop()
            late.append("reached the transport")
        except asyncio.CancelledError:
            late.append("refused")
        return VerbResult.success("ended at last")

    registry.register(Verb("stubborn", "will not end", stubborn, timeout_s=MOVE_RESEND_S / 2))
    walk = {"vx": 0.1, "duration_s": 0.5}
    alone = MockTransport()
    reference = await Executor(registry, alone, contract=glot("walk").frontmatter).run_verb(
        "walk", walk
    )
    transport = MockTransport()
    ex = Executor(registry, transport, contract=glot("stubborn, walk").frontmatter)
    timed_out = await ex.run_verb("stubborn")
    assert "timed out" in timed_out.summary, timed_out.summary
    before = len(transport.intents)
    send = transport.send_intent

    async def sending(intent: Intent) -> Ack:
        ack = await send(intent)
        if intent.kind == "move" and not go_late.is_set():
            go_late.set()  # the verb left behind ends now, in the middle of the next move
            for _ in range(5):
                await asyncio.sleep(0)
        return ack

    transport.send_intent = sending  # type: ignore[method-assign]
    walked = await ex.run_verb("walk", walk)
    assert late == ["refused"], late
    during = [i.kind for i in transport.intents[before:]]
    assert during == [i.kind for i in alone.intents], f"a late stop landed in the walk: {during}"
    assert walked.summary == reference.summary


def test_this_python_can_still_let_a_verb_left_behind_go_at_the_loops_close() -> None:
    """A verb the executor leaves behind is taken out of asyncio's own list of tasks so that
    `asyncio.run`'s close does not wait on it (`safety._leave_behind`), through
    `asyncio.tasks._unregister_task`, which is private. A Python without it has to turn CI red
    here rather than leave a run hanging at its close."""
    assert callable(getattr(asyncio.tasks, "_unregister_task", None)), (
        f"Python {sys.version_info[:3]} has no asyncio.tasks._unregister_task: a verb the "
        "executor leaves behind would hold the process open at asyncio.run's close"
    )


async def test_a_verb_left_behind_where_asyncio_cannot_let_it_go_is_said_to(
    monkeypatch: pytest.MonkeyPatch, mock_transport: MockTransport
) -> None:
    """Where asyncio has no way to let a verb go, the record and the log say the process may
    not exit until it ends, where they said only that it was left behind."""
    monkeypatch.delattr(asyncio.tasks, "_unregister_task")
    quit_now = asyncio.Event()

    async def stubborn(ctx: VerbContext, _p: NoParams) -> VerbResult:
        while not quit_now.is_set():
            with contextlib.suppress(asyncio.CancelledError):
                await quit_now.wait()
        return VerbResult.success("ended by the test")

    reg = VerbRegistry()
    reg.register(Verb("stubborn", "will not end", stubborn, timeout_s=MOVE_RESEND_S / 2))
    said: list[str] = []
    ex, seen = logged(reg, mock_transport, allow="stubborn")
    ex.log = said.append
    try:
        result = await ex.run_verb("stubborn")
        assert "timed out" in result.summary
        notes = [e.data["text"] for e in seen if e.kind == "note"]
        held = [n for n in notes if "may not exit" in n]
        assert held and held[0] in said, (notes, said)
    finally:
        quit_now.set()


@pytest.mark.parametrize("how", ["abort", "preempt", "pause"])
async def test_a_call_the_executor_ended_stays_so_when_its_gate_cannot_be_written(
    how: str,
) -> None:
    """The abort's, the newer command's and the pause's gates were written before their stop,
    unguarded: a record that could not be written (a full disk) held the stop back, and the call
    came back as an ordinary failure of the verb's, which `abort_when` counts."""
    gate = {"abort": "abort", "preempt": "preempted", "pause": "paused"}[how]

    def record(event: Any) -> None:
        if event.kind == "gate" and event.data.get("gate") == gate:
            raise OSError(28, "No space left on device")

    async def long_walk(ctx: VerbContext, _p: NoParams) -> VerbResult:
        for _ in range(100):
            await ctx.transport.send_intent(Intent.move(0.1, 0.0, 0.0))
            await asyncio.sleep(MOVE_RESEND_S)
        return VerbResult.success("walked")

    reg = VerbRegistry()
    reg.register(Verb("long_walk", "walks", long_walk, timeout_s=60))
    transport = MockTransport()
    ex = Executor(
        reg, transport, contract=glot("long_walk").frontmatter, event_log=EventLog(record=record)
    )
    running = asyncio.create_task(ex.run_verb("long_walk"))
    await asyncio.sleep(MOVE_RESEND_S / 2)
    assert transport.intents_of("move"), "the walk is not under way"
    if how == "abort":
        ex.abort.set()
    elif how == "preempt":
        ex.preempt("a newer command")
    else:
        ex.pause_for(60)
    await asyncio.wait({running}, timeout=2.0)
    assert running.done()
    assert transport.intents[-1].kind == "stop", "no stop went out"
    outcome: Any = running.exception() or running.result()
    if how == "abort":
        assert isinstance(outcome, Aborted), f"the abort answered {outcome!r}"
    elif how == "preempt":
        assert isinstance(outcome, Preempted), f"the newer command answered {outcome!r}"
    else:
        assert outcome.data.get("ended_by") == PAUSE_ENDED, f"the pause answered {outcome!r}"


async def test_a_call_its_client_cancelled_through_a_cancel_scope_says_so_in_the_record() -> None:
    """The MCP server cancels a call through anyio's cancel scope, which cancels it again on
    every turn of the loop. The stop was sent before the `cancelled` gate, the wait on the stop
    was cut short at the next turn, and the gate was never written."""
    import anyio

    async def long_walk(ctx: VerbContext, _p: NoParams) -> VerbResult:
        for _ in range(100):
            await ctx.transport.send_intent(Intent.move(0.1, 0.0, 0.0))
            await asyncio.sleep(MOVE_RESEND_S)
        return VerbResult.success("walked")

    reg = VerbRegistry()
    reg.register(Verb("long_walk", "walks", long_walk, timeout_s=60))
    seen: list[Any] = []
    transport = MockTransport()
    ex = Executor(
        reg,
        transport,
        contract=glot("long_walk").frontmatter,
        event_log=EventLog(observers=[seen.append]),
    )
    scope = anyio.CancelScope()
    outcome: list[Any] = []

    async def tool_call() -> None:
        with scope:
            outcome.append(await ex.run_verb("long_walk"))

    running = asyncio.create_task(tool_call())
    await asyncio.sleep(MOVE_RESEND_S / 2)
    assert transport.intents_of("move"), "the walk is not under way"
    scope.cancel()
    await asyncio.wait({running}, timeout=2.0)
    await asyncio.sleep(MOVE_RESEND_S)  # a stop still on its way lands meanwhile
    assert running.done() and not outcome, "the call was not cancelled"
    assert transport.intents[-1].kind == "stop", "no stop went out after the cancelled call"
    assert ("cancelled", "fired") in gates(seen), [e.kind for e in seen]


#: How long after a twist the link below refuses a stop, and how long the verb reads where it
#: is before its own stop on its way out: past the first, inside `ENDING_S`.
BUSY_S = ENDING_S / 3
CLEANUP_S = ENDING_S / 2


class _BusyAfterATwist(MockTransport):
    """A link still busy with the call the cancel cut, which refuses a stop for `BUSY_S` after a
    twist, raising or saying so in `stop_error`."""

    def __init__(self, how: str) -> None:
        super().__init__()
        self.how = how
        self.busy_until = 0.0
        self.landed = 0
        self.stop_error: str | None = None

    async def send_intent(self, intent: Intent) -> Ack:
        if intent.kind == "move":
            self.busy_until = time.monotonic() + BUSY_S
        return await super().send_intent(intent)

    async def stop(self) -> None:
        if time.monotonic() < self.busy_until:
            if self.how == "raises":
                raise TransportError("the bus is still busy with the call the cancel cut")
            self.stop_error = "the bus is still busy with the call the cancel cut"
            return
        self.stop_error = None
        self.landed += 1
        await super().stop()


@pytest.mark.parametrize("how", ["raises", "stop_error"])
async def test_a_stop_that_would_land_after_a_failed_one_is_not_refused(how: str) -> None:
    """The leash refused everything once the executor's stop was answered, whatever the answer:
    over a link still busy that stop failed, and the verb's own stop a moment later, the one the
    link would have taken, was refused, so no stop landed at all. The leash refuses a late stop
    only once one has landed."""

    async def careful_walk(ctx: VerbContext, _p: NoParams) -> VerbResult:
        try:
            for _ in range(100):
                await ctx.transport.send_intent(Intent.move(0.1, 0.0, 0.0))
                await asyncio.sleep(MOVE_RESEND_S)
        except asyncio.CancelledError:
            await asyncio.sleep(CLEANUP_S)  # reads where it is before it stops
            await ctx.transport.stop()
            raise
        return VerbResult.success("walked")

    reg = VerbRegistry()
    reg.register(Verb("careful_walk", "walks", careful_walk, timeout_s=60))
    transport = _BusyAfterATwist(how)
    ex = Executor(reg, transport, contract=glot("careful_walk").frontmatter)
    running = asyncio.create_task(ex.run_verb("careful_walk"))
    try:
        await asyncio.sleep(MOVE_RESEND_S / 4)  # inside BUSY_S of the first twist
        assert transport.intents_of("move"), "the walk is not under way"
        ex.abort.set()
        await asyncio.wait({running}, timeout=4 * ENDING_S)
        await asyncio.sleep(CLEANUP_S)
        assert running.done() and isinstance(running.exception(), Aborted)
        assert transport.landed >= 1, f"no stop landed: {running.exception()}"
    finally:
        await _ended(running)


#: The allowance a body below gives its own `stop`, and how late its stop is answered: after the
#: call's allowance and the run's own allowance after it, and before the close's allowance ends.
STOP_ALLOWANCE_S = CORE["stop"].timeout_s / 10
SLOW_STOP_S = 2.5 * STOP_ALLOWANCE_S

GOES_ON_PROGRAM = """
import asyncio, dataclasses, sys
from roboglot.glotfile.parser import parse_glot_text
from roboglot.safety import Aborted, Executor
from roboglot.transport.base import Intent
from roboglot.transport.mock import MockTransport
from roboglot.verbs.core import CORE, MOVE_RESEND_S
from roboglot.verbs.registry import Verb, VerbRegistry, VerbResult

ALLOWANCE_S, SLOW_S = float(sys.argv[1]), float(sys.argv[2])
GLOT = '''---
glot: 0
name: t
description: d
verbs:
  allow: [walk_long]
  confirm: []
success: [x]
abort_when: []
---
# Task
x
'''

class SlowStop(MockTransport):
    async def stop(self):
        await asyncio.sleep(SLOW_S)
        print("STOP LANDED", flush=True)
        await super().stop()

async def walk_long(ctx, _p):
    for _ in range(100):
        await ctx.transport.send_intent(Intent.move(0.1, 0.0, 0.0))
        await asyncio.sleep(MOVE_RESEND_S)
    return VerbResult.success("walked")

async def main():
    reg = VerbRegistry()
    reg.register(dataclasses.replace(CORE["stop"], timeout_s=ALLOWANCE_S))
    reg.register(Verb("walk_long", "walks", walk_long, timeout_s=60))
    transport = SlowStop()
    ex = Executor(reg, transport, contract=parse_glot_text(GLOT).frontmatter, log=print)
    running = asyncio.ensure_future(ex.run_verb("walk_long"))
    await asyncio.sleep(MOVE_RESEND_S / 2)
    ex.abort.set()
    try:
        await running
    except Aborted as e:
        print("ABORTED", e, flush=True)
    await asyncio.sleep(ALLOWANCE_S)  # the run's teardown goes on for a while

asyncio.run(main())
print("EXITED", flush=True)
"""


def test_a_stop_said_to_go_on_by_itself_goes_out_before_the_process_ends() -> None:
    """An owed stop not answered in its allowance is said to go on by itself, and `asyncio.run`'s
    close then cancelled it before it went out, a slow stop that would have landed, with only the
    log saying so. The close gives it its allowance once more. Run in a child Python under
    `asyncio.run`, as the CLI runs."""
    import subprocess

    # UTF-8 on the child's pipes, as above: its log is `print`, and the arrow in the executor's
    # lines raised UnicodeEncodeError on Windows before the program reached its last line
    done = subprocess.run(
        [
            sys.executable,
            "-X",
            "utf8",
            "-c",
            GOES_ON_PROGRAM,
            str(STOP_ALLOWANCE_S),
            str(SLOW_STOP_S),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )
    out = done.stdout
    assert "EXITED" in out, done.stderr[-600:]
    assert "goes on by itself" in out, f"the stop was answered in its allowance: {out!r}"
    assert "STOP LANDED" in out, f"the loop's close cancelled the stop: {out.strip()!r}"


#: How long the twist below takes to be written in its thread.
WRITE_S = ENDING_S / 2


class _ThreadWrites(MockTransport):
    """A transport that writes each command in a thread of its own, with nothing ordering the
    writes: a twist's write goes on in its thread whatever cancels the call that started it."""

    def __init__(self) -> None:
        super().__init__()
        self.wire: list[str] = []
        self.writing = threading.Event()
        self._wire_lock = threading.Lock()

    def _write(self, what: str, delay: float) -> None:
        if delay:
            self.writing.set()
            time.sleep(delay)
        with self._wire_lock:
            self.wire.append(what)

    async def send_intent(self, intent: Intent) -> Ack:
        self.intents.append(intent)
        await asyncio.to_thread(self._write, intent.kind, WRITE_S if intent.kind == "move" else 0)
        return Ack()

    async def stop(self) -> None:
        self.stops += 1
        self.intents.append(Intent.stop())
        await asyncio.to_thread(self._write, "stop", 0)


async def test_the_last_command_the_body_receives_after_an_abort_is_a_stop() -> None:
    """A twist already being written in a thread when the abort came landed after the one stop
    the executor sent, and the body's last command was the twist. The leash sees the send cut
    off in flight, and the stop is sent again one deadman window after the first."""

    async def walk_long(ctx: VerbContext, _p: NoParams) -> VerbResult:
        for _ in range(100):
            await ctx.transport.send_intent(Intent.move(0.1, 0.0, 0.0))
            await asyncio.sleep(MOVE_RESEND_S)
        return VerbResult.success("walked")

    reg = VerbRegistry()
    reg.register(Verb("walk_long", "walks", walk_long, timeout_s=60))
    transport = _ThreadWrites()
    ex = Executor(reg, transport, contract=glot("walk_long").frontmatter)
    running = asyncio.create_task(ex.run_verb("walk_long"))
    try:
        assert await asyncio.to_thread(transport.writing.wait, 5.0), "a twist is being written"
        ex.abort.set()
        await asyncio.wait({running}, timeout=4 * ENDING_S)
        await asyncio.sleep(2 * WRITE_S)  # every write still in a thread has landed by now
        assert running.done() and isinstance(running.exception(), Aborted)
        assert transport.wire[-1] == "stop", f"the body's last command: {transport.wire[-3:]}"
    finally:
        await _ended(running)


async def test_a_call_cancelled_as_its_admission_read_answers_does_not_run() -> None:
    """Python 3.11's `wait_for` loses a cancel that lands as the admission read answers, and
    nothing after the read asked whether the call had been cancelled: one MCP cancel, or the
    second Ctrl-C, was lost and the walk ran to its end. On 3.12 nothing is lost, so only a 3.11
    job can fail this."""
    asked, go = threading.Event(), threading.Event()

    def read() -> None:
        asked.set()
        go.wait(10.0)

    class ThroughWaitFor(MockTransport):
        async def get_state(self) -> RobotState:
            loop = asyncio.get_running_loop()
            await asyncio.wait_for(loop.run_in_executor(None, read), timeout=5.0)
            return await super().get_state()

    async def stroll(ctx: VerbContext, _p: NoParams) -> VerbResult:
        for _ in range(5):
            await ctx.transport.send_intent(Intent.move(0.1, 0.0, 0.0))
            await asyncio.sleep(MOVE_RESEND_S)
        await ctx.transport.stop()
        return VerbResult.success("walked")

    reg = VerbRegistry()
    reg.register(Verb("stroll", "walks", stroll, timeout_s=60))
    transport = ThroughWaitFor()
    ex = Executor(reg, transport, contract=glot("stroll").frontmatter)
    running = asyncio.create_task(ex.run_verb("stroll"))
    try:
        assert await asyncio.to_thread(asked.wait, 5.0), "the admission read is out"
        go.set()
        _block_the_loop(ENDING_S)  # the read's answer is queued before the cancel
        running.cancel()
        await asyncio.wait({running}, timeout=10 * MOVE_RESEND_S)
        moves = len(transport.intents_of("move"))
        assert running.done()
        assert running.cancelled() and moves == 0, (
            f"python {sys.version_info[:2]}: the walk ran though its call was cancelled: "
            f"{moves} twists"
        )
    finally:
        go.set()
        await _ended(running)


#: How long the twist below takes to be written, and the stop's answer its round trip after its
#: own write: the twist lands after the stop's write and before its answer.
LATE_WRITE_S = ENDING_S / 6
STOP_ANSWER_S = ENDING_S / 2


class _LateWrite(MockTransport):
    """A send the cancel does not reach (Python 3.11's `wait_for`, or a third party's send that
    finishes its write), which comes back once written: after the stop's write, and before the
    stop's answer."""

    def __init__(self) -> None:
        super().__init__()
        self.wire: list[str] = []
        self.writing = threading.Event()
        self.caught = 0
        self._lock = threading.Lock()

    def _write(self, what: str, delay: float) -> None:
        if delay:
            self.writing.set()
            time.sleep(delay)
        with self._lock:
            self.wire.append(what)

    async def send_intent(self, intent: Intent) -> Ack:
        self.intents.append(intent)
        delay = LATE_WRITE_S if intent.kind == "move" else 0
        write = asyncio.ensure_future(asyncio.to_thread(self._write, intent.kind, delay))
        try:
            await asyncio.shield(write)
        except asyncio.CancelledError:
            self.caught += 1
            await write  # the cancel did not reach it: it comes back once written
        return Ack()

    async def stop(self) -> None:
        self.stops += 1
        self.intents.append(Intent.stop())
        await asyncio.to_thread(self._write, "stop", 0)
        await asyncio.sleep(STOP_ANSWER_S)  # the answer's round trip


async def test_a_twist_written_after_the_stop_is_followed_by_another_stop() -> None:
    """A send counted only when it was cut off or came back after the stop was answered. One the
    cancel did not reach came back once its twist was written, after the stop's write and before
    its answer, was not counted, and the body's last command was that twist. Any send in flight
    as the stop went counts now, whatever it returned."""

    async def walk_long(ctx: VerbContext, _p: NoParams) -> VerbResult:
        for _ in range(100):
            await ctx.transport.send_intent(Intent.move(0.1, 0.0, 0.0))
            await asyncio.sleep(MOVE_RESEND_S)
        return VerbResult.success("walked")

    reg = VerbRegistry()
    reg.register(Verb("walk_long", "walks", walk_long, timeout_s=60))
    transport = _LateWrite()
    ex = Executor(reg, transport, contract=glot("walk_long").frontmatter)
    running = asyncio.create_task(ex.run_verb("walk_long"))
    try:
        assert await asyncio.to_thread(transport.writing.wait, 5.0), "a twist is being written"
        ex.abort.set()
        await asyncio.wait({running}, timeout=8 * ENDING_S)
        await asyncio.sleep(2 * LATE_WRITE_S)
        assert running.done() and isinstance(running.exception(), Aborted)
        assert transport.caught == 1, "the cancel reached the send, so this proves nothing"
        assert transport.wire[-1] == "stop", f"the body's last command: {transport.wire[-3:]}"
    finally:
        await _ended(running)
