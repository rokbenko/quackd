"""The executor is the layer that does not trust the LLM. Every rule gets a test."""

from __future__ import annotations

import asyncio

import pytest

from quackd.duckfile.parser import parse_duck_text
from quackd.duckfile.schema import Budgets, DuckFile
from quackd.safety import (
    Aborted,
    Budget,
    BudgetExceeded,
    ConfirmDenied,
    Executor,
    Heartbeat,
    VerbNotAllowed,
    allow_all,
    deny_all,
)
from quackd.transport.base import DuckState, Intent
from quackd.transport.mock import MockTransport
from quackd.verbs.registry import NoParams, Verb, VerbContext, VerbRegistry, VerbResult


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
