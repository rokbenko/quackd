"""The executor is the layer that does not trust the LLM. Every rule gets a test."""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any

import pytest
from PIL import Image

from quackd.adapters.factory import ADAPTER_NAMES, make_adapter
from quackd.duckfile.parser import parse_duck_text
from quackd.duckfile.schema import Budgets, DuckFile
from quackd.log import EventLog
from quackd.safety import (
    Aborted,
    Budget,
    BudgetExceeded,
    ConfirmDenied,
    Executor,
    Heartbeat,
    Preempted,
    SafetyStop,
    VerbNotAllowed,
    allow_all,
    deny_all,
)
from quackd.transport.base import DEFAULT_CAMERA_NAME, CameraFrame, DuckState, Intent
from quackd.transport.mock import MockTransport
from quackd.verbs.registry import (
    STOP_ENDED,
    STOPPED,
    NoParams,
    Verb,
    VerbContext,
    VerbRegistry,
    VerbResult,
    registry_from_manifest,
)


def duck(allow: str, confirm: str = "", abort: str = "") -> DuckFile:
    return parse_duck_text(
        f"""---
duck: 0
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
    ex = Executor(registry, mock_transport, contract=duck("quack, walk").frontmatter)
    with pytest.raises(VerbNotAllowed):
        await ex.run_verb("kick")
    assert mock_transport.intents == []
    result = await ex.run_verb("quack", {"text": "hi"})
    assert result.ok and mock_transport.intents_of("sound")


async def test_stop_is_always_allowed(
    registry: VerbRegistry, mock_transport: MockTransport
) -> None:
    ex = Executor(registry, mock_transport, contract=duck("quack").frontmatter)
    assert (await ex.run_verb("stop")).ok
    assert mock_transport.stops == 1


async def test_confirm_gate(registry: VerbRegistry, mock_transport: MockTransport) -> None:
    fm = duck("quack, kick", confirm="kick").frontmatter
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


async def test_budget_hard_stop(registry: VerbRegistry, mock_transport: MockTransport) -> None:
    budget = Budget(Budgets(max_steps=2), now=mock_transport.now)
    budget.start()
    ex = Executor(registry, mock_transport, contract=duck("quack").frontmatter, budget=budget)
    await ex.run_verb("quack")
    await ex.run_verb("quack")
    with pytest.raises(BudgetExceeded):
        await ex.run_verb("quack")
    assert budget.steps == 2


async def test_budget_minutes_uses_transport_clock(mock_transport: MockTransport) -> None:
    budget = Budget(Budgets(max_minutes=0.1), now=mock_transport.now)
    budget.start()
    budget.check()
    await mock_transport.sleep(7)
    with pytest.raises(BudgetExceeded):
        budget.check()


async def test_dry_run_sends_nothing(registry: VerbRegistry, mock_transport: MockTransport) -> None:
    ex = Executor(
        registry, mock_transport, contract=duck("walk, get_frame").frontmatter, dry_run=True
    )
    result = await ex.run_verb("walk", {"vx": 0.2})
    assert result.ok and result.data.get("dry_run") is True
    assert mock_transport.intents == []
    frame = await ex.run_verb("get_frame")  # read-only verbs still run
    assert frame.ok and "frame captured" in frame.summary


async def test_a_read_and_a_dry_run_are_never_the_robots_first_motion(
    registry: VerbRegistry, mock_transport: MockTransport
) -> None:
    """`first_motion_at` is the robot moving. A third party's `read_only` sensor runs for real on
    a dry run, since it sends nothing, and the pause gate's caution, which holds such a verb
    still because the flag is no promise, had the record take it for the robot moving."""

    async def locate(_ctx: VerbContext, _p: NoParams) -> VerbResult:
        return VerbResult.success("the ball is 1 m ahead")

    registry.register(Verb("locate", "reads where things are", locate, read_only=True))
    contract = duck("locate, walk").frontmatter
    dry = Executor(registry, mock_transport, contract=contract, dry_run=True)
    assert (await dry.run_verb("locate")).ok
    assert (await dry.run_verb("walk", {"vx": 0.1, "duration_s": 0.2})).ok
    assert dry.first_motion_at is None and dry.first_motion_llm_calls is None

    live = Executor(registry, MockTransport(), contract=contract)
    assert (await live.run_verb("locate")).ok
    assert live.first_motion_at is None, "a read is not the robot moving"
    assert (await live.run_verb("walk", {"vx": 0.1, "duration_s": 0.2})).ok
    assert live.first_motion_at is not None


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
    ex = Executor(registry, mock_transport, contract=duck("walk").frontmatter)
    result = await ex.run_verb("walk", {"vx": 5.0})
    assert not result.ok and "vx" in result.summary
    assert mock_transport.intents == []


async def test_preconditions_block_unsafe_verbs(registry: VerbRegistry) -> None:
    fallen = MockTransport(states=[DuckState(fallen=True, posture="fallen")])
    ex = Executor(registry, fallen, contract=duck("walk, stand_up").frontmatter)
    result = await ex.run_verb("walk")
    assert not result.ok and "fallen" in result.summary
    assert fallen.intents_of("move") == []


async def test_repeat_failure_abort(registry: VerbRegistry) -> None:
    refusing = MockTransport(refuse_kinds={"do"})
    ex = Executor(
        registry,
        refusing,
        contract=duck("kick", abort="Same verb fails 2 times in a row").frontmatter,
    )
    assert not (await ex.run_verb("kick")).ok
    with pytest.raises(Aborted):
        await ex.run_verb("kick")
    assert ex.abort.is_set()


async def test_battery_abort(registry: VerbRegistry) -> None:
    low = MockTransport(states=[DuckState(battery_percent=10, posture="standing")])
    ex = Executor(registry, low, contract=duck("quack", abort="Battery below 15%").frontmatter)
    with pytest.raises(Aborted):
        await ex.run_verb("quack")


async def test_verb_timeout_stops_the_duck(mock_transport: MockTransport) -> None:
    registry = VerbRegistry()

    async def slow(ctx: VerbContext, _: NoParams) -> VerbResult:
        await asyncio.sleep(1)
        return VerbResult.success("never")

    registry.register(Verb("slow", "slow", slow, timeout_s=0.05))
    ex = Executor(registry, mock_transport, contract=duck("slow").frontmatter)
    result = await ex.run_verb("slow")
    assert not result.ok and "timed out" in result.summary
    assert mock_transport.stops == 1


async def test_buggy_verb_stops_the_duck(mock_transport: MockTransport) -> None:
    registry = VerbRegistry()

    async def boom(ctx: VerbContext, _: NoParams) -> VerbResult:
        raise RuntimeError("kaboom")

    registry.register(Verb("boom", "boom", boom))
    ex = Executor(registry, mock_transport, contract=duck("boom").frontmatter)
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
    ex = Executor(registry, transport, contract=duck("walk").frontmatter)
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
    ex = Executor(registry, transport, contract=duck("slow").frontmatter)
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
    ex = Executor(registry, transport, contract=duck("waiting").frontmatter)
    running = asyncio.create_task(ex.run_verb("waiting"))
    await asyncio.wait_for(started.wait(), timeout=5)
    ex.abort.set()
    with pytest.raises(Aborted, match=DROPPED):
        await asyncio.wait_for(running, timeout=5)
    assert transport.stops == 1


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
    ex = Executor(registry, mock_transport, contract=duck("walk").frontmatter)
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
    ex = Executor(registry, mock_transport, contract=duck("long_walk").frontmatter)
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


async def test_stop_still_runs_on_an_aborted_executor(
    registry: VerbRegistry, mock_transport: MockTransport
) -> None:
    """The abort is set exactly when the pilot reaches for the brake — a failed heartbeat, a
    kill switch — so refusing `stop` closed the panic button at the only moment it mattered.
    Everything else stays refused."""
    ex = Executor(registry, mock_transport, contract=duck("walk, stop").frontmatter)
    ex.abort.set()

    before = mock_transport.stops
    result = await ex.run_verb("stop")
    assert result.ok and mock_transport.stops == before + 1

    with pytest.raises(Aborted):
        await ex.run_verb("walk", {"vx": 0.1, "duration_s": 0.2})


async def test_a_stop_that_never_left_is_not_reported_as_a_stop(
    registry: VerbRegistry, mock_transport: MockTransport
) -> None:
    """`stop` is asked for most often when the link is the thing that is wrong. The guard in
    verbs/core.py reads `stop_error` off whatever it was handed, which in a real run is the
    adapter — and no adapter forwarded it, so the check was dead everywhere and an
    undeliverable stop was written into the transcript as a success."""
    ex = Executor(registry, mock_transport, contract=duck("stop").frontmatter)
    assert (await ex.run_verb("stop")).ok

    mock_transport.stop_error = "duck.stop: no answer within 2s"
    result = await ex.run_verb("stop")
    assert not result.ok
    assert "could not be delivered" in result.summary
    assert "deadman" in result.summary


# ── the log: the executor says what it decided and what it sent ─────────────────────────


def logged(
    registry: VerbRegistry,
    transport: MockTransport,
    allow: str = "quack, walk, kick",
    **kwargs: str,
) -> tuple[Executor, list[Any]]:
    """An executor whose narration is collected, for the tests below."""
    from quackd.log import EventLog

    seen: list[Any] = []
    ex = Executor(
        registry,
        transport,
        contract=duck(allow, **kwargs).frontmatter,
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
    """`--yes` and every flock member answer their own confirm gates with nobody in the room.
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
    which is `--yes`, a flock's standing answer and the MCP server."""

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
    """`yes | quackd run` and `quackd run < answers.txt` reach the same callable a person at
    a keyboard reaches, and `input()` reads a pipe as happily as it reads a person.

    The gate still opens on a yes and still closes on a no, because that is what the pipe
    asked for and what quackd has always done. What is withheld is the testimony: a row here
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

    `a human said yes` under `--yes`, under a flock's standing answer or under a pipe is the
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
        "--yes and a flock's standing answer",
        "the MCP server without --yes",
        "an asker nobody marked",
        "a pipe on stdin",
    ],
)
async def test_a_dangerous_verb_is_refused_where_nobody_is_asked(
    registry: VerbRegistry, mock_transport: MockTransport, asker: Any
) -> None:
    """`--yes`, a flock's standing answer, the MCP server and a pipe answer without anybody
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
    """The docs have always promised --dry-run shows every intent a model would send. The
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
    transport = MockTransport(states=[DuckState(policy="p", posture="fallen", fallen=True)])
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
    from quackd.log import EventLog

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
    and almost no wall time: exactly the shape a free-running simulator has."""
    ex, seen = logged(registry, mock_transport, allow="walk")
    await ex.run_verb("walk", {"vx": 0.1, "duration_s": 1.0})
    end = ends(seen)[0]
    assert end["transport_s"] == pytest.approx(1.0)
    assert end["elapsed_s"] < 0.5
    assert "clock" not in end, "the mock is not a simulator, so there is no label to give"


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
    that a human had been asked."""

    class Abort(RuntimeError):  # the shape click's own Abort has
        pass

    def raises(_name: str, _params: dict[str, Any]) -> bool:
        raise Abort

    ex, seen = logged(registry, mock_transport, allow="quack, kick", confirm="kick")
    ex.confirm = raises
    with pytest.raises(ConfirmDenied, match="Abort"):
        await ex.run_verb("kick")
    assert gates(seen) == [("confirm", "denied")]
    assert "Abort" in seen[1].data["reason"] and seen[1].data["answer"] is False
    assert ends(seen)[0]["outcome"] == "denied"
    assert mock_transport.intents_of("do") == []


async def test_a_state_read_that_fails_still_sends_a_stop(registry: VerbRegistry) -> None:
    """Every other in-verb failure stops the robot. A link that died one line earlier, while
    reading the state the gates need, was the one that did not."""

    class Dead(MockTransport):
        async def get_state(self) -> DuckState:
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
    from quackd.log import EventLog

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
    from quackd.log import EventLog

    seen: list[Any] = []

    def record(event: Any) -> None:
        if event.kind == "verb_start":
            raise OSError("disk full")

    ex = Executor(
        registry,
        mock_transport,
        contract=duck("quack").frontmatter,
        event_log=EventLog(record=record, observers=[seen.append]),
    )
    with pytest.raises(OSError, match="disk full"):
        await ex.run_verb("quack")
    assert [e.data["outcome"] for e in seen if e.kind == "verb_end"] == ["error"]


async def test_without_a_event_log_the_executor_is_silent_and_hands_over_the_real_transport(
    registry: VerbRegistry, mock_transport: MockTransport
) -> None:
    """The flock and every direct user build an Executor with no event log, and get exactly
    what they got before."""
    ex = Executor(registry, mock_transport, contract=duck("quack").frontmatter)
    assert ex.logged_transport() is mock_transport
    assert ex.context().transport is mock_transport
    assert (await ex.run_verb("quack")).ok


async def test_a_safety_stop_from_another_layer_ends_the_verb_with_its_own_word(
    registry: VerbRegistry, mock_transport: MockTransport
) -> None:
    """A flock's role change preempts an in-flight verb by raising a `SafetyStop` subclass.
    Before this the log called that `error`, which the docs define as a bug, and every
    handover in a three-robot run printed a red line about nothing being wrong."""

    class Preempted(SafetyStop):
        outcome = "preempted"

    async def handover(_ctx: VerbContext, _p: NoParams) -> VerbResult:
        raise Preempted("role change to kicker")

    registry.register(Verb("handover", "gives way", handover, timeout_s=5))
    event_log = EventLog()
    seen: list[Any] = []
    event_log.add(seen.append)
    ex = Executor(
        registry, mock_transport, contract=duck("handover").frontmatter, event_log=event_log
    )
    with pytest.raises(Preempted):
        await ex.run_verb("handover")

    (end,) = [e for e in seen if e.kind == "verb_end"]
    assert end.data["outcome"] == "preempted"
    assert end.data["summary"] == "role change to kicker"


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
        contract=duck("observe").frontmatter,
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
    has one to carry, and is never spoken to a model."""
    saved: list[list[str]] = []
    ex = Executor(
        registry,
        mock_transport,
        contract=duck("observe").frontmatter,
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
    ex = Executor(registry, mock_transport, contract=duck("walk").frontmatter)
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
    contract = duck("walk", abort="the same verb fails 2 times in a row").frontmatter
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

    async def get_state(self) -> DuckState:
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
    ex = Executor(registry, MockTransport(), contract=duck("report_state").frontmatter)
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
    ex = Executor(registry, body, contract=duck("walk").frontmatter)
    walking = asyncio.create_task(ex.run_verb("walk", {"vx": 0.1, "duration_s": 1.0}))
    await asyncio.wait_for(body.reading.wait(), 5)
    assert ex.preempt("a newer command, sit") == 0
    body.answer.set()
    with pytest.raises(Preempted, match="walk did not start: a newer command, sit"):
        await walking
    assert body.intents_of("move") == [], "it started after the newer command"
