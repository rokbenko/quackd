"""Flock primitives: the contract block, messages, bus, auction, planner, and the clock."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace as NS
from typing import Any

import pytest
from pydantic import TypeAdapter, ValidationError

from quackd.agent.providers.base import ProviderTurn, ToolCall, Usage
from quackd.agent.providers.fake import FakeProvider
from quackd.duckfile.parser import DuckParseError, parse_duck_text
from quackd.duckfile.schema import AUCTION_MAX_MEMBERS, PILOTS_MAX_MEMBERS, FlockSection
from quackd.flock.auction import Auction, AuctionPolicy
from quackd.flock.bus import InProcessBus
from quackd.flock.messages import BidMsg, FlockMessage, TaskMsg
from quackd.flock.planner import equal_wedges, plan_flock_task
from quackd.flock.transcript import FlockTranscript
from quackd.log import EventLog, LogEvent
from quackd.sim2d.clock import FlockClock, HookInterrupt
from quackd.sim2d.world import DT, World

# ── schema ──────────────────────────────────────────────────────────────────────────────


def test_flock_section_defaults_and_names() -> None:
    flock = FlockSection()
    assert flock.member_names == ["duck-0", "duck-1", "duck-2"]
    assert flock.allocation.hysteresis_pct == 20.0
    assert flock.safety.min_separation_m == 0.4
    named = FlockSection(members=["ada", "grace"])
    assert named.member_names == ["ada", "grace"]


@pytest.mark.parametrize("bad", [1, 5, ["solo"], ["a", "a"], ["A", "b"], ["x"] * 5])
def test_flock_members_validation(bad: Any) -> None:
    with pytest.raises(ValidationError):
        FlockSection(members=bad)


def test_the_method_says_which_kind_of_flock_and_auction_is_the_default() -> None:
    assert FlockSection().allocation.method == "auction"
    pilots = FlockSection(members=["ada", "grace"], allocation={"method": "pilots"})
    assert pilots.allocation.method == "pilots"


def test_pilots_take_up_to_eight_members_and_an_auction_still_caps_at_four() -> None:
    """The arena holds four. A pilot flock has no arena, so the bound is a person's terminal."""
    eight = [f"duck-{i}" for i in range(PILOTS_MAX_MEMBERS)]
    assert len(FlockSection(members=eight, allocation={"method": "pilots"}).member_names) == 8
    with pytest.raises(ValidationError, match="an auction flock needs 2 to 4"):
        FlockSection(members=eight[:5])
    with pytest.raises(ValidationError, match="2 to 8"):
        FlockSection(members=[*eight, "duck-8"], allocation={"method": "pilots"})


def test_the_auction_cap_is_the_arenas_own() -> None:
    """Spelled in the schema rather than imported, so the two must be checked against each
    other somewhere; here."""
    from quackd.sim2d.world import MAX_DUCKS

    assert AUCTION_MAX_MEMBERS == MAX_DUCKS


def test_roles_belong_to_the_auction() -> None:
    with pytest.raises(ValidationError, match="roles is an auction feature"):
        FlockSection(
            members=["ada", "grace"],
            allocation={"method": "pilots"},
            roles={"spotter": {"requires": ["observe"]}, "kicker": {"requires": ["kick"]}},
        )


def test_pilots_needs_duck_1() -> None:
    text = (
        "---\nduck: {v}\nname: t\ndescription: d\nverbs:\n"
        "  allow: [stop]\nsuccess: [x]\nflock:\n  members: [ada, grace]\n"
        "  allocation:\n    method: pilots\n---\n# T\nx\n"
    )
    with pytest.raises(DuckParseError, match="pilots needs duck: 1"):
        parse_duck_text(text.format(v=0))
    assert parse_duck_text(text.format(v=1)).frontmatter.flock is not None


def test_flock_block_parses_in_a_duck_and_is_optional() -> None:
    duck = parse_duck_text(
        "---\nduck: 0\nname: t\ndescription: d\nverbs:\n  allow: [stop]\nsuccess: [x]\n"
        "flock:\n  members: 2\n---\n# T\nx\n"
    )
    assert duck.frontmatter.flock is not None
    assert duck.frontmatter.flock.member_names == ["duck-0", "duck-1"]
    solo = parse_duck_text(
        "---\nduck: 0\nname: t\ndescription: d\nverbs:\n  allow: [stop]\n"
        "success: [x]\n---\n# T\nx\n"
    )
    assert solo.frontmatter.flock is None


# ── messages ────────────────────────────────────────────────────────────────────────────


def test_messages_round_trip_through_json() -> None:
    adapter: TypeAdapter[FlockMessage] = TypeAdapter(FlockMessage)
    bid = BidMsg(t=1.5, src="duck-1", task_id="t1", ball_dist_m=0.62, bearing_deg=-8.0)
    again = adapter.validate_python(bid.model_dump())
    assert again == bid and again.kind == "BID"


# ── bus ─────────────────────────────────────────────────────────────────────────────────


def test_bus_fanout_no_echo_and_tap() -> None:
    tapped: list[FlockMessage] = []
    bus = InProcessBus(tap=tapped.append)
    a = bus.subscribe("duck-0")
    b = bus.subscribe("duck-1")
    msg = BidMsg(t=0.0, src="duck-0", task_id="t", ball_dist_m=1.0)
    bus.publish(msg)
    assert b.drain() == [msg]
    assert a.drain() == []  # no echo to the sender
    assert tapped == [msg] and bus.published == 1
    b.close()
    bus.publish(msg)
    assert b.drain() == []  # closed subscriptions receive nothing


# ── auction ─────────────────────────────────────────────────────────────────────────────


def _bid(src: str, dist: float, t: float = 0.0) -> BidMsg:
    return BidMsg(t=t, src=src, task_id="t", ball_dist_m=dist)


def test_auction_window_lowest_bid_and_ties() -> None:
    now = NS(t=0.0)
    auction = Auction(AuctionPolicy(window_s=0.4), lambda: now.t)
    auction.open(_bid("duck-2", 0.9))
    auction.add(_bid("duck-0", 0.5))
    auction.add(_bid("duck-0", 0.7))  # keeps the lowest per duck
    auction.add(_bid("duck-1", 0.5))  # tie with duck-0
    assert not auction.due()
    now.t = 0.5
    assert auction.due()
    decision = auction.decide(prev_kicker=None, excluded=set())
    assert decision is not None
    assert decision.kicker == "duck-0"  # tie broken by lowest name
    assert decision.tie is True and decision.winning_dist == 0.5


def test_auction_hysteresis_boundary() -> None:
    policy = AuctionPolicy(hysteresis=0.2)
    auction = Auction(policy, lambda: 1.0)
    auction.open(_bid("duck-1", 0.85))  # challenger, 15 % better than prev
    auction.add(_bid("duck-0", 1.0))  # previous kicker
    d = auction.decide(prev_kicker="duck-0", excluded=set())
    assert d is not None and d.kicker == "duck-0" and d.hysteresis_applied
    auction.open(_bid("duck-1", 0.75))  # 25 % better: unseats
    auction.add(_bid("duck-0", 1.0))
    d = auction.decide(prev_kicker="duck-0", excluded=set())
    assert d is not None and d.kicker == "duck-1" and not d.hysteresis_applied


def test_auction_exclusions() -> None:
    auction = Auction(AuctionPolicy(), lambda: 0.0)
    auction.open(_bid("duck-0", 0.4))
    auction.add(_bid("duck-1", 0.6))
    d = auction.decide(prev_kicker=None, excluded={"duck-0"})
    assert d is not None and d.kicker == "duck-1"
    auction.open(_bid("duck-0", 0.4))
    assert auction.decide(prev_kicker=None, excluded={"duck-0"}) is None


# ── planner ─────────────────────────────────────────────────────────────────────────────

DUCK = parse_duck_text(
    "---\nduck: 0\nname: flock-kick\ndescription: d\nverbs:\n  allow: [stop]\nsuccess: [x]\n"
    "flock:\n  members: 3\n---\n# Task\nkick the ball\n"
)


def test_equal_wedges_partition_the_circle() -> None:
    wedges = equal_wedges(["b", "a", "c"])
    assert list(wedges) == ["a", "b", "c"]
    assert sum(w.width_deg for w in wedges.values()) == pytest.approx(360.0)
    assert wedges["a"].start_deg == 0.0 and wedges["c"].end_deg == 360.0


async def test_planner_fake_makes_zero_calls() -> None:
    task, wedges, _usage, calls, fallback, _cost = await plan_flock_task(
        DUCK, ["duck-0", "duck-1"], FakeProvider.for_duck("flock-kick"), "t1"
    )
    assert calls == 0 and not fallback and task.target == "ball" and len(wedges) == 2


class _PlannerStub:
    name = "stub"
    model = "stub-1"
    supports_vision = False

    def __init__(self, turn: ProviderTurn | Exception) -> None:
        self._turn = turn
        self.calls = 0

    async def step(self, system: str, history: Any, tools: Any) -> ProviderTurn:
        self.calls += 1
        if isinstance(self._turn, Exception):
            raise self._turn
        return self._turn


async def test_planner_applies_valid_tuning_and_counts_one_call() -> None:
    turn = ProviderTurn(
        tool_calls=[ToolCall(name="plan_flock_task", arguments={"stop_distance": 0.3})],
        usage=Usage(input_tokens=10, output_tokens=5),
    )
    stub = _PlannerStub(turn)
    task, _, usage, calls, fallback, _cost = await plan_flock_task(
        DUCK, ["duck-0", "duck-1"], stub, "t"
    )
    assert calls == 1 and stub.calls == 1 and not fallback
    assert task.stop_distance == 0.3 and usage.input_tokens == 10


@pytest.mark.parametrize(
    "turn",
    [
        ProviderTurn(tool_calls=[]),
        RuntimeError("provider down"),
    ],
)
async def test_planner_falls_back_on_trouble(turn: Any) -> None:
    task, _, _, calls, fallback, _cost = await plan_flock_task(
        DUCK, ["duck-0", "duck-1"], _PlannerStub(turn), "t"
    )
    assert calls == 1 and fallback and task.stop_distance == 0.22  # defaults


async def test_planner_clamps_numbers_and_drops_only_the_invalid_field() -> None:
    turn = ProviderTurn(
        tool_calls=[
            ToolCall(
                name="plan_flock_task",
                arguments={"stop_distance": 99, "step_deg": 60, "kick_leg": "sideways"},
            )
        ]
    )
    task, _, _, calls, fallback, _cost = await plan_flock_task(
        DUCK, ["duck-0", "duck-1"], _PlannerStub(turn), "t"
    )
    assert calls == 1 and not fallback
    assert task.stop_distance == 1.0  # out of range clamps to the schema bound
    assert task.step_deg == 60  # the valid field survives
    assert task.kick_leg == "right"  # the unclampable invalid field is dropped alone


async def test_the_planners_one_call_is_logged_as_a_request_and_an_answer() -> None:
    events: list[LogEvent] = []
    turn = ProviderTurn(
        tool_calls=[ToolCall(name="plan_flock_task", arguments={"stop_distance": 0.3})],
        usage=Usage(input_tokens=10, output_tokens=5),
        text="one wedge each",
    )
    _task, _wedges, _usage, calls, fallback, _cost = await plan_flock_task(
        DUCK,
        ["duck-0", "duck-1"],
        _PlannerStub(turn),
        "t",
        event_log=EventLog(record=events.append),
    )
    assert calls == 1 and not fallback
    assert [e.kind for e in events] == ["llm_request", "llm"]
    assert events[0].data["provider"] == "stub" and events[0].data["purpose"] == "plan_flock_task"
    answer = events[1].data
    assert answer["text"] == "one wedge each" and "error" not in answer
    assert answer["tool_calls"][0]["name"] == "plan_flock_task"
    assert answer["usage"]["input_tokens"] == 10 and answer["latency_s"] >= 0

    # the fake provider never reaches a model, so it must not narrate one either
    events.clear()
    await plan_flock_task(
        DUCK,
        ["duck-0", "duck-1"],
        FakeProvider.for_duck("flock-kick"),
        "t",
        event_log=EventLog(record=events.append),
    )
    assert events == []


class _BillingPlannerStub(_PlannerStub):
    """A planner whose vendor says what the call was billed, the way OpenRouter does."""

    name = "openrouter"
    model = "openai/gpt-6-sol"
    bills_per_call = True


async def test_the_planner_records_a_billed_call_at_its_bill() -> None:
    """The planner is one model call like any other, so it is costed like one: at what the
    vendor billed where it says so, marked as such, and at a person's own rate over both."""
    from quackd.agent.providers.catalogue import Price
    from quackd.agent.providers.pricing import cost_usd

    events: list[LogEvent] = []
    turn = ProviderTurn(
        tool_calls=[ToolCall(name="plan_flock_task", arguments={"stop_distance": 0.3})],
        usage=Usage(input_tokens=10, output_tokens=5),
        billed_usd=0.0042,
    )
    *_, spent = await plan_flock_task(
        DUCK,
        ["duck-0", "duck-1"],
        _BillingPlannerStub(turn),
        "t",
        event_log=EventLog(record=events.append),
    )
    assert spent == 0.0042
    assert events[1].data["cost_usd"] == 0.0042 and events[1].data["billed"] is True

    events.clear()
    *_, spent = await plan_flock_task(
        DUCK,
        ["duck-0", "duck-1"],
        _BillingPlannerStub(turn),
        "t",
        event_log=EventLog(record=events.append),
        price="in=3,out=15",
    )
    usage = {"input_tokens": 10, "output_tokens": 5}
    assert spent == cost_usd(usage, Price(3.0, 15.0)) != 0.0042
    assert "billed" not in events[1].data

    events.clear()
    unbilled = turn.model_copy(update={"billed_usd": None})
    await plan_flock_task(
        DUCK,
        ["duck-0", "duck-1"],
        _PlannerStub(unbilled),
        "t",
        event_log=EventLog(record=events.append),
    )
    assert "billed" not in events[1].data, "a vendor that bills nothing reads as it did"


async def test_a_planner_call_that_failed_after_it_was_billed_is_costed() -> None:
    """The planner falls back to defaults, as on any failure, and still owns up to the bill."""
    from quackd.agent.providers.base import ProviderError

    events: list[LogEvent] = []
    raised = ProviderError("openrouter: the provider failed partway through the answer")
    raised.billed_usd = 0.003
    raised.usage = Usage(input_tokens=12, output_tokens=1)
    _task, _wedges, usage, _calls, fallback, spent = await plan_flock_task(
        DUCK,
        ["duck-0", "duck-1"],
        _BillingPlannerStub(raised),
        "t",
        event_log=EventLog(record=events.append),
    )
    assert fallback and spent == 0.003
    error = events[1].data
    assert error["cost_usd"] == 0.003 and error["billed"] is True and "error" in error
    assert usage.input_tokens == 12, "the tokens the failed call spent are the flock's too"


async def test_a_planner_that_fails_is_logged_as_an_error_and_a_fallback_note() -> None:
    events: list[LogEvent] = []
    logged: list[str] = []
    task, _wedges, _usage, calls, fallback, _cost = await plan_flock_task(
        DUCK,
        ["duck-0", "duck-1"],
        _PlannerStub(RuntimeError("provider down")),
        "t",
        log=logged.append,
        event_log=EventLog(record=events.append),
    )
    assert calls == 1 and fallback and task.stop_distance == 0.22  # defaults
    assert [e.kind for e in events] == ["llm_request", "llm", "note"]
    assert events[1].data["error"] == "RuntimeError: provider down"
    assert events[1].data["latency_s"] >= 0
    # --verbose and the log say the same sentence, from the one call site
    assert events[2].data["text"] == logged[0] == "planner fallback: RuntimeError: provider down"


# ── the lockstep clock ──────────────────────────────────────────────────────────────────


async def test_clock_single_participant_step_counts() -> None:
    world = World(seed=0)
    clock = FlockClock(world)
    await clock.sleep("duck-0", 0.1)
    assert world.steps == 2  # round(0.1 / DT)
    await clock.sleep("duck-0", DT)
    assert world.steps == 3
    await clock.stop()


async def test_clock_barrier_advances_to_each_deadline() -> None:
    world = World(seed=0, n_ducks=3)
    clock = FlockClock(world)

    async def sleeper(pid: str, seconds: float) -> float:
        await clock.sleep(pid, seconds)
        woke_at = world.t
        clock.unregister(pid)  # a finished participant must leave, or it freezes time
        return woke_at

    woke = await asyncio.gather(
        sleeper("duck-0", 0.10), sleeper("duck-1", 0.05), sleeper("duck-2", 0.20)
    )
    assert world.steps == 4  # advanced exactly to the furthest deadline
    assert woke[1] <= woke[0] <= woke[2]
    await clock.stop()


async def test_clock_freezes_while_anyone_is_awake() -> None:
    world = World(seed=0, n_ducks=2)
    clock = FlockClock(world)
    clock.register("duck-1")  # registered but AWAKE: the barrier must hold time still

    task = asyncio.create_task(clock.sleep("duck-0", 0.05))
    await asyncio.sleep(0.05)  # real time passes; sim time must not
    assert world.steps == 0 and not task.done()
    clock.unregister("duck-1")  # now everyone (i.e. duck-0) is parked
    await asyncio.wait_for(task, timeout=2)
    assert world.steps == 1
    clock.unregister("duck-0")
    await clock.stop()


async def test_clock_unregister_mid_sleep_does_not_deadlock_the_rest() -> None:
    world = World(seed=0, n_ducks=2)
    clock = FlockClock(world)

    async def dies() -> None:
        with pytest.raises(asyncio.CancelledError):
            await clock.sleep("duck-1", 10.0)

    clock.register("holder")  # stays awake: freezes time so duck-1 stays parked
    dying = asyncio.create_task(dies())
    await asyncio.sleep(0.02)
    clock.unregister("duck-1")  # cancels its future, re-evaluates the barrier
    await asyncio.wait_for(dying, timeout=2)
    survivor = asyncio.create_task(clock.sleep("duck-0", 0.1))
    await asyncio.sleep(0.02)
    clock.unregister("holder")  # now only duck-0 is left, and it is parked
    await asyncio.wait_for(survivor, timeout=2)
    assert world.steps >= 2
    await clock.stop()


async def test_clock_a_task_parking_under_an_id_that_just_woke_is_never_stranded() -> None:
    """The advancer marks a woken participant awake before resolving its future, and the task
    only runs its `finally` when it resumes. A second task sleeping under the same id can park
    in between, because the slot reads awake. The first task's `finally` cleared whatever
    waiter sat under the id, so it cleared the second one's: that future never resolved and B
    hung with no error. The cartoon's transport and microduck's MuJoCo one share one id across
    their tasks, so concurrent MCP calls can do this."""
    world = World(seed=0)
    clock = FlockClock(world)
    order: list[str] = []
    second: list[asyncio.Task[None]] = []

    async def b() -> None:
        order.append("b parks")  # clock.sleep parks before it first awaits, so nothing between
        await clock.sleep("duck-0", DT)
        order.append("b wakes")

    def park_b_before_a_resumes(_w: World) -> None:
        # A tick hook runs after the step and before the advancer wakes A, so B's first step
        # is queued ahead of A's resumption, and asyncio runs ready callbacks in order.
        if not second:
            second.append(asyncio.get_running_loop().create_task(b()))

    clock.add_tick_hook(park_b_before_a_resumes)
    # Awaited in this task, not wrapped in wait_for, which on 3.11 runs it as a task of its
    # own and so reorders what this test records.
    await clock.sleep("duck-0", DT)  # this test is task A
    order.append("a wakes")
    await asyncio.wait_for(second[0], timeout=2)  # stranded, this timed out
    assert order == ["b parks", "a wakes", "b wakes"]  # B really parked before A resumed
    assert world.steps == 2
    # nothing of A or B is left in the slot, so the id sleeps again rather than meet the guard
    await asyncio.wait_for(clock.sleep("duck-0", 0.1), timeout=2)
    assert world.steps == 4
    await clock.stop()


@pytest.mark.parametrize("leaves_by", ["cancel", "unregister", "interrupt"])
async def test_clock_a_sleep_that_ends_early_never_strands_the_next_under_its_id(
    leaves_by: str,
) -> None:
    """The same gap on the other ways out of `sleep`: a task cancelled as it comes due, an
    unregistered id and a closed live window each resume the first task after a second one has
    parked, and its `finally` must leave that waiter alone."""
    world = World(seed=0)
    clock = FlockClock(world)
    order: list[str] = []
    second: list[asyncio.Task[None]] = []

    async def a() -> None:
        try:
            await clock.sleep("duck-0", DT)
        finally:
            order.append("a leaves")

    async def b() -> None:
        order.append("b parks")
        await clock.sleep("duck-0", DT)
        order.append("b wakes")

    first = asyncio.create_task(a())

    def park_b_then_end_a(_w: World) -> None:
        if second:
            return
        second.append(asyncio.get_running_loop().create_task(b()))  # queued ahead of A
        if leaves_by == "cancel":
            first.cancel()
        elif leaves_by == "unregister":
            clock.unregister("duck-0")
        else:
            raise KeyboardInterrupt  # what the live window's close button raises

    clock.add_tick_hook(park_b_then_end_a)
    with pytest.raises((asyncio.CancelledError, HookInterrupt)):
        await first
    await asyncio.wait_for(second[0], timeout=2)  # stranded, this timed out
    assert order == ["b parks", "a leaves", "b wakes"]  # B really parked before A resumed
    await clock.stop()


async def test_clock_a_cancelled_sleep_frees_its_id() -> None:
    """A task cancelled while parked clears its own waiter on the way out. Left behind, that
    dead waiter would make the next sleep under the id look like a second sleeper and be
    refused."""
    world = World(seed=0)
    clock = FlockClock(world)
    clock.register("holder")  # stays awake: freezes time so duck-0 stays parked
    parked = asyncio.create_task(clock.sleep("duck-0", 5 * DT))
    await asyncio.sleep(0)
    parked.cancel()
    with pytest.raises(asyncio.CancelledError):
        await parked
    clock.unregister("holder")
    await asyncio.wait_for(clock.sleep("duck-0", DT), timeout=2)
    assert world.steps == 1  # the cancelled sleep never counted as parked
    await clock.stop()


def test_task_msg_carries_the_plan() -> None:
    from quackd.flock.messages import FlockTask

    task = FlockTask(task_id="t", name="n", goal="g")
    msg = TaskMsg(t=0.0, src="coordinator", task_id="t", task=task, members=["duck-0", "duck-1"])
    assert msg.task.target == "ball" and msg.members[0] == "duck-0"


# ── regressions from the v0.3.0 adversarial review ─────────────────────────────────────


async def test_clock_survives_a_raising_tick_hook() -> None:
    world = World(seed=0)
    clock = FlockClock(world)

    def bad_hook(_w: World) -> None:
        raise RuntimeError("boom")

    clock.add_tick_hook(bad_hook)
    await asyncio.wait_for(clock.sleep("duck-0", 0.2), timeout=2)  # must not wedge
    assert world.steps == 4
    assert len(clock.hook_errors) == 1 and bad_hook not in clock._tick_hooks
    await clock.stop()


async def test_clock_hook_keyboard_interrupt_reaches_the_sleeper() -> None:
    from quackd.sim2d.clock import HookInterrupt

    world = World(seed=0)
    clock = FlockClock(world)

    def quit_hook(_w: World) -> None:
        raise KeyboardInterrupt  # the live window's close button

    clock.add_tick_hook(quit_hook)
    # translated, never a raw KeyboardInterrupt: asyncio would re-raise that into the loop
    with pytest.raises(HookInterrupt):
        await asyncio.wait_for(clock.sleep("duck-0", 1.0), timeout=2)
    await clock.stop()


def test_hb_timeout_floors_above_the_longest_verb_sleep() -> None:
    fast = FlockSection.model_validate({"members": 2, "safety": {"per_duck_heartbeat_s": 0.5}})
    policy = AuctionPolicy.from_flock(fast)
    # the kick verb sleeps 1.5 s in one piece; a healthy kicker must survive that
    assert policy.hb_timeout_s >= 0.5 + 1.5


def test_one_claimant_false_is_rejected() -> None:
    with pytest.raises(ValidationError, match="one_claimant"):
        FlockSection.model_validate({"members": 2, "safety": {"one_claimant": False}})


def test_restart_s_reaches_the_task() -> None:
    duck = parse_duck_text(
        "---\nduck: 0\nname: slow-scan\ndescription: d\nverbs:\n  allow: [stop]\n"
        "success: [x]\nflock:\n  members: 2\n  search:\n    restart_s: 30\n---\n# T\nx\n",
        "slow-scan.duck",
    )
    from quackd.flock.planner import default_task

    assert default_task(duck, "t").restart_s == 30.0


def _mini_coordinator() -> Any:
    from quackd.flock.coordinator import FlockCoordinator
    from quackd.flock.messages import FlockTask

    world = World(seed=0)
    clock = FlockClock(world)
    transcript: Any = NS(write=lambda *a, **k: None)
    return FlockCoordinator(
        task=FlockTask(task_id="t", name="n", goal="g"),
        members={},
        wedges={},
        bus=InProcessBus(),
        clock=clock,
        transcript=transcript,
    )


def test_exclusion_is_never_shortened() -> None:
    import math

    coord = _mini_coordinator()
    coord._exclude("duck-1", math.inf)  # presumed dead
    coord._exclude("duck-1", 3.0)  # a late RESULT must not resurrect it
    assert coord.excluded_until["duck-1"] == math.inf


def test_excluded_duck_cannot_bid_and_a_bid_clears_search_empty() -> None:
    coord = _mini_coordinator()
    coord.searching_empty.add("duck-1")
    coord._exclude("duck-0", 3.0)
    coord._dispatch(BidMsg(t=0.0, src="duck-0", task_id="t", ball_dist_m=0.5))
    assert not coord.auction.is_open  # a cooldown duck's bid opens nothing
    coord._dispatch(BidMsg(t=0.0, src="duck-1", task_id="t", ball_dist_m=0.7))
    assert coord.auction.is_open
    assert "duck-1" not in coord.searching_empty  # its sighting outranks the empty scan


# ── the log ─────────────────────────────────────────────────────────────────────────────


def test_the_flock_transcript_is_a_log_record_stamped_in_sim_time(tmp_path: Path) -> None:
    transcript = FlockTranscript(tmp_path, now=lambda: 4.2)
    transcript.sink(LogEvent("llm", 99.0, {"provider": "stub", "usage": {"input_tokens": 3}}))
    transcript.close()
    lines = (tmp_path / "flock.jsonl").read_text(encoding="utf-8").splitlines()
    # the event's own wall clock (99.0) is dropped: flock.jsonl is stamped in sim time only
    assert [json.loads(line) for line in lines if line.strip()] == [
        {"sim_t": 4.2, "kind": "llm", "provider": "stub", "usage": {"input_tokens": 3}}
    ]


def test_a_flock_view_that_raises_never_ends_the_run_and_is_counted() -> None:
    def boom(_event: LogEvent) -> None:
        raise RuntimeError("the console went away")

    seen: list[tuple[str, dict[str, Any]]] = []
    coord = _mini_coordinator()
    coord.event_log = EventLog(observers=[boom])
    coord.on_event = lambda kind, data: seen.append((kind, data))
    coord._event("auction", first_bid="duck-0", dist=0.5)  # must not raise
    assert coord.event_log.dropped == 1
    # and the recorder behind it still got its event: one blind view blinds nobody else
    assert seen == [("auction", {"first_bid": "duck-0", "dist": 0.5})]


def test_the_coordinators_transcript_only_kinds_reach_the_view_with_the_records_keys() -> None:
    """Six decisions used to reach `flock.jsonl` and nothing else, so a watcher saw a duck
    stop bidding and never learned it had been declared dead. The view carries the record's
    own keys, because two spellings of one fact is how a log starts lying."""
    from quackd.log import EventLog

    seen: list[Any] = []
    coord = _mini_coordinator()
    coord.event_log = EventLog(observers=[seen.append])

    coord._event("member_dead", duck="duck-2", last_hb=3.0)
    coord._event("member_excluded", duck="duck-1", why="repeated misses")
    coord._event("auction_void", auctions=2)
    coord._event("auction_waiting", missing_roles=["kicker"])
    coord._event("wedges_rotated", round=1, by_deg=45)
    coord._event("bid_rejected", src="duck-0", role="spotter", missing=["gaze"])

    assert [e.kind for e in seen] == [
        "member_dead",
        "member_excluded",
        "auction_void",
        "auction_waiting",
        "wedges_rotated",
        "bid_rejected",
    ]
    assert seen[0].data == {"duck": "duck-2", "last_hb": 3.0}
    assert seen[5].data == {"src": "duck-0", "role": "spotter", "missing": ["gaze"]}
    # and every one of them has words in the renderer, or the view would show nothing
    from quackd.log import flock_caption

    assert all(flock_caption(e.kind, e.data) is not None for e in seen)


def test_a_coordinator_rejects_a_bid_whose_body_cannot_do_the_role() -> None:
    """A role's physical needs are judged from what the bid itself carried, so a robot the
    coordinator does not run is held to the same standard as one it does."""
    from quackd.adapters.manifest import Datasheet, Figure
    from quackd.duckfile.schema import FlockRole
    from quackd.flock.coordinator import FlockCoordinator
    from quackd.flock.messages import FlockTask

    def sheet(payload: float | None) -> dict[str, Any]:
        figure = (
            Figure(value=payload, confidence="official", source="the docs")
            if payload is not None
            else None
        )
        return Datasheet(manipulator="gripper", arms=1, payload_kg=figure).model_dump(mode="json")

    events: list[tuple[str, dict[str, Any]]] = []
    verbs = ["observe", "gaze", "go_to", "kick"]
    roles = {
        "spotter": FlockRole(requires=["observe", "gaze"]),
        "kicker": FlockRole(requires=["go_to", "kick"], needs={"payload_kg": 1.0}),
    }
    now = NS(t=0.0)
    coord = FlockCoordinator(
        task=FlockTask(task_id="t", name="n", goal="g", roles=roles),
        members={
            name: NS(transport=NS(mobility="wheeled"), provides=verbs)
            for name in ("light", "strong", "eye")
        },
        wedges={},
        bus=InProcessBus(),
        clock=NS(now=lambda: now.t),
        transcript=NS(write=lambda *a, **k: None),
        on_event=lambda kind, data: events.append((kind, data)),
    )

    # nearest, but it cannot carry: rejected, and told exactly what it lacks
    coord._dispatch(
        BidMsg(
            t=0.0,
            src="light",
            task_id="t",
            ball_dist_m=0.3,
            role="kicker",
            provides=verbs,
            datasheet=sheet(0.5),
            mobility="wheeled",
        )
    )
    rejected = [d for kind, d in events if kind == "bid_rejected"]
    assert rejected == [
        {"src": "light", "role": "kicker", "missing": ["payload_kg >= 1 (has 0.5)"]}
    ]

    # a bid that says nothing about its body is a body that said nothing
    coord._dispatch(
        BidMsg(t=0.0, src="light", task_id="t", ball_dist_m=0.3, role="kicker", provides=verbs)
    )
    assert [d for kind, d in events if kind == "bid_rejected"][-1]["missing"] == [
        "payload_kg >= 1 (not published)"
    ]

    # the further robot that can carry wins it, and the spotter role is unaffected
    for src, dist, role, payload in (
        ("strong", 0.9, "kicker", 1.0),
        ("eye", 0.4, "spotter", None),
    ):
        coord._dispatch(
            BidMsg(
                t=0.0,
                src=src,
                task_id="t",
                ball_dist_m=dist,
                role=role,
                provides=verbs,
                datasheet=sheet(payload),
                mobility="wheeled",
            )
        )
    now.t = 0.5
    coord._decide_roles_if_due()
    assert coord.assignments == {"kicker": "strong", "spotter": "eye"}
