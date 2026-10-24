"""The policy loop: a named rate, pacing on the arm's clock, a derived speed cap and chunks.

Every segment here runs the real backend over the test suite's `FakeArm`, on the synthetic
`SPANS` calibration and the `REWIRED` motor table, so no travel, id or tick of any arm is in it.
The rates are the verbs' own (`TICK_S`) or a multiple of it, and every limit is read off the
loop's own constants. Time is a clock that moves only when it is slept, so a segment of many
seconds costs none of the wall's, and every tick lands where the pacer put it.

Two clocks stand in for the two kinds the loop tells apart. `LockstepClock` says it is lockstep,
as the simulator's does, so inference is awaited in the loop's turn and each chunk is held back
by the runner's declared latency. `GateClock` is the wall's kind, where an answer comes back
whenever the runner's thread has it: here it opens a gated runner's answer exactly `k` ticks
after it was asked, which is a runner that takes `k` ticks, on a clock a test can repeat.
"""

from __future__ import annotations

import asyncio
import gc
import itertools
import math
import sys
import threading
from collections.abc import Callable, Mapping, Sequence
from typing import Any

import numpy as np
import pytest

from quackd.safety import Aborted, Executor, Heartbeat, allow_all
from quackd.transport.base import HeartbeatError, Intent
from quackd.verbs.registry import registry_from_manifest
from quackd_lerobot import LeRobotAdapter
from quackd_lerobot.mock import LeRobotMock
from quackd_lerobot.policy.loop import (
    EXACT,
    FIRST_CHUNK_S,
    MAX_RATE_HZ,
    MIN_RATE_HZ,
    REFILL_LATENCIES,
    STALL_S,
    STARVE_S,
    WORKER,
    PolicyLoop,
    latency_ticks,
    longest_latency,
    rate_refusal,
    refill_at,
    speed_cap,
    starved_each_chunk,
)
from quackd_lerobot.policy.runner import Chunk, Features, Observation
from quackd_lerobot.policy.scripted import ScriptedRunner
from quackd_lerobot.real import HOT_C, MAX_STEP_DEG, POLICY_HZ, LeRobotReal
from quackd_lerobot.verbs import MANIPULATE_S, MANIPULATE_TIMEOUT_S, TICK_S, SegmentEnd
from tests.test_lerobot_adapter import (
    FakeArm,
    SteppedClock,
    _inside,
    _segment_arm,
    _until,
    _whole_hold,
)

RATE = 3 / TICK_S
"""A policy three times as fast as the verbs' tick, inside the band the loop paces."""
PERIOD = 1 / RATE
STEP = MAX_STEP_DEG / 2
"""quackd's step cap in these tests: half its default, so nothing leans on the default."""


class LockstepClock(SteppedClock):
    """A stepped clock that says it is lockstep, as the simulator's does."""

    lockstep = True


class Gated(ScriptedRunner):
    """A scripted runner whose every answer waits, in its worker thread, until the gate for the
    tick it was asked at opens, or every gate does (`open`). It keeps the name of each thread it
    was called on."""

    def __init__(self, script: Any, **kw: Any) -> None:
        super().__init__(script, **kw)
        self._gates: dict[int, threading.Event] = {}
        self._lock = threading.Lock()
        self.open = threading.Event()
        self.threads: list[str] = []
        self.inside = threading.Event()

    def gate(self, tick: int) -> threading.Event:
        with self._lock:
            return self._gates.setdefault(tick, threading.Event())

    def next_chunk(self, observation: Observation, sent: Mapping[str, float]) -> Chunk:
        self.threads.append(threading.current_thread().name)
        self.inside.set()
        gate = self.gate(observation.tick)
        while not (gate.is_set() or self.open.is_set()):
            gate.wait(0.005)
        return super().next_chunk(observation, sent)


class Misbehaving(Gated):
    """A gated runner whose answer to its request number `at` goes wrong as `how` says: it
    `raises`, it is `empty`, or it is `stamped` with the tick after the one it was asked at.
    Every other answer, and every answer with `how` None, is the script's."""

    def __init__(self, script: Any, *, how: str | None = None, at: int = 0, **kw: Any) -> None:
        super().__init__(script, **kw)
        self.how = how
        self.at = at

    def next_chunk(self, observation: Observation, sent: Mapping[str, float]) -> Chunk:
        chunk = super().next_chunk(observation, sent)
        if self.how is None or self.requests != self.at:
            return chunk
        if self.how == "raises":
            raise RuntimeError("the policy lost its place")
        if self.how == "empty":
            return Chunk(chunk.tick)
        return Chunk(chunk.tick + 1, chunk.actions)


class GateClock(SteppedClock):
    """The wall's kind of clock, whose `k`th tick after a request opens that request's gate and
    waits for its answer to be in before the next tick looks. `sleeps` counts the loop's ticks
    from when a test zeroes it, just before the `do`. A request asked at tick `leave` is left
    for the test to answer."""

    def __init__(self, runner: Gated, k: int, leave: int | None = None) -> None:
        super().__init__()
        self.runner = runner
        self.k = k
        self.leave = leave
        self.sleeps = 0
        self.loop: PolicyLoop | None = None

    async def sleep(self, seconds: float) -> None:
        self.t += seconds
        self.sleeps += 1
        req = self.loop._inflight if self.loop is not None else None
        if req is not None and req.tick == self.leave:
            req = None
        if req is not None and not req.future.done() and req.tick + self.k <= self.sleeps:
            self.runner.gate(req.tick).set()
            await _answer(req.future)
        await asyncio.sleep(0)


async def _answer(future: Any) -> None:
    """Wait for a runner's answer on its thread, and mark an error in it read, as the loop marks
    its own copy, so a runner that raises leaves nothing for asyncio to log."""
    wrapped = asyncio.wrap_future(future)
    await asyncio.wait({wrapped}, timeout=10.0)
    if wrapped.done() and not wrapped.cancelled():
        wrapped.exception()


class AnsweredClock(SteppedClock):
    """A stepped clock under a runner that declares no latency: a request still out when the
    loop sleeps is answered before time moves on, so it lands on the tick after it was asked,
    however long its thread or its server takes on the machine running the test. On a plain
    `SteppedClock` a tick costs no wall time, and a request the loop leaves to land while the
    arm plays (`PolicyLoop._request`) would land as many ticks later as the loop had run by the
    time its thread got round to it. `loop` is the backend's policy loop, which the test sets.
    A runner that never answers is waited for at every sleep, so a test of one keeps to a plain
    `SteppedClock`."""

    def __init__(self) -> None:
        super().__init__()
        self.loop: PolicyLoop | None = None

    async def sleep(self, seconds: float) -> None:
        req = self.loop._inflight if self.loop is not None else None
        if req is not None and not req.future.done():
            await _answer(req.future)
        await super().sleep(seconds)


def _tag(tick: int, index: int) -> float:
    """A pan goal that says which request it answered and where in its chunk it was."""
    return tick + index / 100


def _untag(value: float) -> tuple[int, int]:
    tick = math.floor(value + 1e-6)
    return tick, round((value - tick) * 100)


def _tagged(length: int) -> Callable[[Observation, Mapping[str, float]], list[dict[str, float]]]:
    """A chunk of `length` pan goals, each tagged with the tick it was asked at and its place."""

    def script(observation: Observation, _sent: Mapping[str, float]) -> list[dict[str, float]]:
        return [{"shoulder_pan": _tag(observation.tick, i)} for i in range(length)]

    return script


def _swing(arm: FakeArm) -> Callable[[Observation, Mapping[str, float]], list[dict[str, float]]]:
    """One pan goal a request, on alternate sides of the pan's middle, so the arm never stalls."""
    near, far = _inside(arm, "shoulder_pan", 0.1), _inside(arm, "shoulder_pan", -0.1)

    def script(observation: Observation, _sent: Mapping[str, float]) -> list[dict[str, float]]:
        return [{"shoulder_pan": near if observation.tick % 2 else far}]

    return script


async def _backend(
    runner: Any, *, clock: Any = None, arm: FakeArm | None = None, **kw: Any
) -> tuple[FakeArm, LeRobotReal, LeRobotAdapter, Executor]:
    arm = arm if arm is not None else _segment_arm()
    transport = LeRobotReal(
        "COM5",
        robot=arm,
        policy=runner,
        clock=clock if clock is not None else SteppedClock(),
        max_step_deg=STEP,
        **kw,
    )
    adapter = LeRobotAdapter(transport)
    manifest = await adapter.connect()
    ex = Executor(registry_from_manifest(manifest, adapter), adapter, confirm=allow_all)
    return arm, transport, adapter, ex


def _do(verb: str, max_s: float, max_chunks: int | None = None, told: str = "stack") -> Intent:
    params: dict[str, Any] = {"skill": f"policy:{verb}:{told}", "max_s": max_s}
    if max_chunks is not None:
        params["max_chunks"] = max_chunks
    return Intent(kind="do", params=params)


async def _segment_end(
    transport: LeRobotReal, verb: str = "manipulate", *, max_s: float, **kw: Any
) -> SegmentEnd:
    ack = await transport.send_intent(_do(verb, max_s, **kw))
    assert ack.accepted, ack.reason
    segment = transport.policy_segment
    assert segment is not None
    await asyncio.wait({segment})
    return segment.result()


def _recorded(arm: FakeArm, clock: Any) -> list[tuple[float, float, dict[str, float]]]:
    """Wrap `arm.send_action` to keep, for every send, the clock's time, the step cap on the
    follower's config as it went out, and what was asked."""
    sends: list[tuple[float, float, dict[str, float]]] = []
    send = arm.send_action

    def recorded(action: dict[str, float]) -> dict[str, float]:
        sends.append((clock.t, arm.config.max_relative_target, dict(action)))
        return send(action)

    arm.send_action = recorded  # type: ignore[method-assign]
    return sends


def _pans(sends: Sequence[tuple[float, float, dict[str, float]]]) -> list[float]:
    return [action["shoulder_pan.pos"] for _, _, action in sends if "shoulder_pan.pos" in action]


# ── the rate and the cap ────────────────────────────────────────────────────────────────


def test_the_speed_cap_is_the_verbs_speed_at_the_policys_rate_and_never_more_than_a_step() -> None:
    verbs_rate = 1 / TICK_S
    for step in (MAX_STEP_DEG, STEP):
        assert speed_cap(step, verbs_rate) == pytest.approx(step)
        for times in (2, 3, 5):
            cap = speed_cap(step, verbs_rate * times)
            assert cap == pytest.approx(step / times)
            # the same degrees a second as a verb, whatever the rate
            assert cap * verbs_rate * times == pytest.approx(step / TICK_S)
        # slower than a verb: never more than one verb step a send
        assert speed_cap(step, verbs_rate / 2) == step
        assert 0 < speed_cap(step, MAX_RATE_HZ) <= step
    for rate in (0.0, math.inf, math.nan, -verbs_rate):
        with pytest.raises(ValueError):
            speed_cap(STEP, rate)
    assert MIN_RATE_HZ <= RATE <= MAX_RATE_HZ
    assert speed_cap(MAX_STEP_DEG, POLICY_HZ) == MAX_STEP_DEG, "a PolicyLike keeps the verbs' cap"


def test_a_declared_latency_is_whole_ticks_rounded_up() -> None:
    assert latency_ticks(0.0, RATE) == 0
    assert latency_ticks(3 / RATE, RATE) == 3
    assert latency_ticks(2.5 / RATE, RATE) == 3
    assert (
        latency_ticks(ScriptedRunner(_tagged(1), rate_hz=RATE, latency_ticks=4).latency_s(), RATE)
        == 4
    )


@pytest.mark.parametrize(
    "rate",
    [math.nan, math.inf, -math.inf, 0.0, -RATE, MIN_RATE_HZ / 2, MAX_RATE_HZ * 2, True],
    ids=["nan", "inf", "-inf", "zero", "negative", "below", "above", "a bool"],
)
async def test_a_rate_that_is_not_finite_or_outside_the_band_refuses_the_segment(
    rate: float,
) -> None:
    runner = ScriptedRunner(_tagged(1), rate_hz=rate, rate_source="the test's own figure")
    arm, transport, adapter, _ = await _backend(runner)
    ack = await transport.send_intent(_do("manipulate", 1.0))
    assert not ack.accepted
    assert ack.reason == rate_refusal(Features(rate, "the test's own figure")), ack.reason
    assert "the test's own figure" in (ack.reason or "") and "Hz" in (ack.reason or "")
    assert runner.requests == 0 and arm.actions == []
    assert not transport.policy_running
    await adapter.close()


@pytest.mark.parametrize("rate", [MIN_RATE_HZ, MAX_RATE_HZ])
async def test_the_band_s_own_ends_are_paced(rate: float) -> None:
    runner = ScriptedRunner(lambda o, s: [{"shoulder_pan": float(o.tick % 3)}], rate_hz=rate)
    _, transport, adapter, _ = await _backend(runner)
    ended = await _segment_end(transport, "pick", max_s=5 / rate)
    assert ended.how == "time", ended.reason
    assert ended.stats.ticks == 5
    await adapter.close()


@pytest.mark.parametrize("latency", [math.nan, -1.0, math.inf])
async def test_a_latency_that_is_not_a_number_of_seconds_refuses_the_segment(
    latency: float,
) -> None:
    class Declares(ScriptedRunner):
        def latency_s(self) -> float:
            return latency

    runner = Declares(_tagged(1), rate_hz=RATE)
    arm, transport, adapter, _ = await _backend(runner)
    ack = await transport.send_intent(_do("manipulate", 1.0))
    assert not ack.accepted and "latency" in (ack.reason or ""), ack.reason
    assert runner.requests == 0 and arm.actions == []
    await adapter.close()


async def test_a_rate_numpy_made_is_paced_like_any_other() -> None:
    """A rate read out of a checkpoint's metadata may come as a numpy number, which is a real
    number all the same."""
    runner = ScriptedRunner(_tagged(1), rate_hz=np.float32(RATE))
    _, transport, adapter, _ = await _backend(runner)
    ended = await _segment_end(transport, "pick", max_s=5 * PERIOD)
    assert ended.how == "time" and ended.stats.ticks == 5, ended
    await adapter.close()


class _NoFeatures(ScriptedRunner):
    def features(self) -> Any:
        return None


class _DictFeatures(ScriptedRunner):
    def features(self) -> Any:
        return {"rate_hz": RATE}


class _EndlessLatency(ScriptedRunner):
    def latency_s(self) -> float:
        return float(sys.float_info.max)


@pytest.mark.parametrize(
    ("runner", "said"),
    [
        (_NoFeatures, "its features gave NoneType rather than the Features"),
        (_DictFeatures, "its features gave dict rather than the Features"),
        (_EndlessLatency, "that comes to a number of ticks"),
    ],
    ids=["features None", "features a dict", "a latency no number of ticks"],
)
async def test_a_runner_that_says_nothing_quackd_can_pace_is_refused_with_its_own_reason(
    runner: type[ScriptedRunner], said: str
) -> None:
    """Refused before anything is sent, and in words about the runner, never as a stop that was
    never sent while the segment was starting."""
    policy = runner(_tagged(1), rate_hz=RATE)
    arm, transport, adapter, _ = await _backend(policy)
    ack = await transport.send_intent(_do("manipulate", 1.0))
    assert not ack.accepted and said in (ack.reason or ""), ack.reason
    assert "stop" not in (ack.reason or "")
    assert policy.requests == 0 and arm.actions == []
    await adapter.close()


async def test_a_start_that_raises_refuses_the_do_with_what_it_raised() -> None:
    arm = _segment_arm()
    _, transport, adapter, _ = await _backend(ScriptedRunner(_swing(arm), rate_hz=RATE), arm=arm)
    loop = transport._policy_loop
    assert loop is not None

    async def falls_over(instruction: str, max_step_deg: float) -> Any:
        raise RuntimeError("the start fell over")

    loop.start = falls_over  # type: ignore[method-assign]
    cap = arm.config.max_relative_target
    ack = await transport.send_intent(_do("manipulate", 1.0))
    assert not ack.accepted, ack
    assert ack.reason == (
        "the policy was not started: its start raised RuntimeError: the start fell over"
    )
    assert arm.actions == [] and not transport.policy_running
    assert arm.config.max_relative_target == cap, "a cap was written for a segment never started"
    await adapter.close()


# ── pacing ──────────────────────────────────────────────────────────────────────────────


async def test_every_tick_is_due_at_the_start_plus_its_index_times_the_period() -> None:
    """Each read costs a quarter of a period here. A pacer that slept a whole period after each
    tick would drift a quarter period a tick; this one sleeps to the deadline, so tick `k` sends
    at `start + k * period` plus that tick's own read and nothing more."""
    clock = SteppedClock()
    arm = _segment_arm()
    work = PERIOD / 4
    read = arm.get_observation

    def slow_read() -> dict[str, Any]:
        clock.t += work
        return read()

    arm.get_observation = slow_read  # type: ignore[method-assign]
    runner = ScriptedRunner(_swing(arm), rate_hz=RATE)
    _, transport, adapter, _ = await _backend(runner, clock=clock, arm=arm)
    sends = _recorded(arm, clock)
    sleeps: list[float] = []
    sleep = clock.sleep

    async def noted(seconds: float) -> None:
        sleeps.append(seconds)
        await sleep(seconds)

    clock.sleep = noted  # type: ignore[method-assign]
    ticks = 12
    ended = await _segment_end(transport, "pick", max_s=ticks * PERIOD)
    assert ended.how == "time" and ended.stats.skipped == 0, ended
    assert len(sends) == ticks
    first = sends[0][0]
    for k, (at, _, _) in enumerate(sends):
        assert at - first == pytest.approx(k * PERIOD, abs=1e-9), (k, at - first)
    # each tick sleeps what is left of its period, never a whole one
    assert sleeps == pytest.approx([PERIOD - work] * len(sleeps), abs=1e-9)
    await adapter.close()


async def test_a_tick_that_overruns_skips_to_the_next_whole_period_and_counts_what_it_missed() -> (
    None
):
    """Tick 4's request takes two and a half periods, so its send lands in period 6. The next
    tick is 7, the next whole period after that, ticks 5 and 6 are counted as skipped, and no
    period sees two sends."""
    clock = SteppedClock()
    arm = _segment_arm()
    swing = _swing(arm)

    def slow_at_four(observation: Observation, sent: Mapping[str, float]) -> Any:
        if observation.tick == 4:
            clock.t += 2.5 * PERIOD
        return swing(observation, sent)

    runner = ScriptedRunner(slow_at_four, rate_hz=RATE)
    _, transport, adapter, _ = await _backend(runner, clock=clock, arm=arm)
    sends = _recorded(arm, clock)
    start = clock.t
    ended = await _segment_end(transport, "pick", max_s=12 * PERIOD)
    periods = [math.floor((at - start) / PERIOD + 1e-9) for at, _, _ in sends]
    assert len(set(periods)) == len(periods), f"two sends in one period: {periods}"
    assert periods[:6] == [0, 1, 2, 3, 6, 7], periods
    assert ended.stats.skipped == 2, ended.stats
    # after the overrun the ticks are on their deadlines again, not a period and a half late
    assert sends[5][0] == pytest.approx(start + 7 * PERIOD, abs=1e-9)
    await adapter.close()


class RoundingClock(SteppedClock):
    """A clock that wakes only on whole steps of its own, the nearest number of them to what was
    asked and at least one, as the simulator's flock clock does (`quackd.sim2d.clock`)."""

    def __init__(self, step: float) -> None:
        super().__init__()
        self.step = step

    async def sleep(self, seconds: float) -> None:
        if seconds > 0:
            self.t += max(1, round(seconds / self.step)) * self.step
        await asyncio.sleep(0)


@pytest.mark.parametrize(
    "steps",
    [10 / 3, 2 / 3],
    ids=["a period of three and a third steps", "a step longer than a period"],
)
async def test_a_clock_that_wakes_on_its_own_steps_never_sends_a_tick_before_its_period(
    steps: float,
) -> None:
    """Each goal says the tick it was asked for. Rounded to the nearest step, a sleep can wake
    short of its deadline, and every tick is still sent inside its own period, one a period; a
    step longer than a period wakes past later deadlines, and the ticks it passed are skipped
    and counted rather than sent two to a period."""
    clock = RoundingClock(PERIOD / steps)
    runner = ScriptedRunner(lambda o, s: [{"shoulder_pan": _tag(o.tick, 0)}], rate_hz=RATE)
    arm, transport, adapter, _ = await _backend(runner, clock=clock)
    sends = _recorded(arm, clock)
    ticks = 12
    ended = await _segment_end(transport, "pick", max_s=ticks * PERIOD)
    assert ended.how == "time", ended.reason
    first = sends[0][0]
    played = [(_untag(pan)[0], at) for (at, _, _), pan in zip(sends, _pans(sends), strict=True)]
    for tick, at in played:
        assert at - first >= tick * PERIOD - 1e-9, f"tick {tick} sent before its deadline"
        assert math.floor((at - first) / PERIOD + 1e-9) == tick, f"tick {tick} sent late"
    periods = [tick for tick, _ in played]
    assert periods == sorted(set(periods)), f"two sends in one period: {periods}"
    assert ended.stats.skipped >= periods[-1] + 1 - len(periods), ended.stats
    if steps < 1:
        assert ended.stats.skipped > 0
    else:
        assert periods == list(range(len(periods))) and ended.stats.skipped == 0
    await adapter.close()


# ── the speed cap on the follower ───────────────────────────────────────────────────────

ENDINGS = [
    "an executor timeout",
    "an abort",
    "a heartbeat stop",
    "an MCP stop",
    "an error",
    "a close",
]


def _long(
    swing: Callable[[Observation, Mapping[str, float]], list[dict[str, float]]],
) -> Callable[[Observation, Mapping[str, float]], list[dict[str, float]]]:
    """`swing` as a chunk `LONG` goals long, each the goal it gives for its own tick."""

    def script(observation: Observation, sent: Mapping[str, float]) -> list[dict[str, float]]:
        return [
            swing(Observation(observation.tick + i, observation.reading), sent)[0]
            for i in range(LONG)
        ]

    return script


RUNNERS = ["one action a tick", "a long chunk"]
"""The two ways a runner answers: one action a request, as a `PolicyLike` does, waited for in
its tick, and a chunk `LONG` long that declares half a chunk of latency, the most `serve` takes,
never waited for on the wall's kind of clock and asked for again as each lands."""


def _parks(clock: SteppedClock, when: Callable[[], bool]) -> asyncio.Event:
    """Make the first sleep on `clock` once `when()` says so last until it is cancelled, and
    return the event set as that sleep begins. A segment asleep there can end on nothing of its
    own, its time included, and has no call out on the bus meanwhile. Every sleep after it is
    the clock's own."""
    parked = asyncio.Event()
    sleep = clock.sleep

    async def parking(seconds: float) -> None:
        if not parked.is_set() and when():
            parked.set()
            await asyncio.Event().wait()  # which only a cancel ends
        await sleep(seconds)

    clock.sleep = parking  # type: ignore[method-assign]
    return parked


def _loop_clock_on(seconds: float, monkeypatch: pytest.MonkeyPatch) -> None:
    """Put the event loop's clock `seconds` on, so that every deadline set on it which falls
    inside them is due at once: a timeout the test runs out, rather than one raced on the wall's
    clock. The loop's clock runs with the wall's from there, for the worker threads that read it
    as well, and is put back as it was once the test is over."""
    loop = asyncio.get_running_loop()
    wall = loop.time
    monkeypatch.setattr(loop, "time", lambda: wall() + seconds)


@pytest.mark.parametrize("kind", RUNNERS)
@pytest.mark.parametrize("ending", ENDINGS)
async def test_the_policy_s_cap_is_on_every_send_and_the_verbs_cap_is_back_after(
    ending: str, kind: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The cap on the follower's config is the policy's own for every send of the segment, and
    the setting, `max_step_deg`, once it ends, however it ends. Never the cap it found: the arm
    here was handed in with another, and a restore to that would be a saved value. The same
    whether the runner answers an action a tick, waited for in its tick, or long chunks, never
    waited for.

    The executor's timeout runs on the test's clocks, not the wall's, where it could run out
    before the first send, or ticks on a clock that costs no wall time could outrun it: a long
    chunk lands the `HALF` ticks in that it declares (`GateClock`), the segment parks on its
    clock once three of the policy's goals have gone out (`_parks`), and the executor's own
    timeout runs out when the test puts the event loop's clock past it (`_loop_clock_on`)."""
    arm = _segment_arm()
    found = STEP * 3
    arm.config.max_relative_target = found
    swing = _swing(arm)
    long = kind == "a long chunk"
    answer = _long(swing) if long else swing
    timed_out = ending == "an executor timeout"

    def script(observation: Observation, sent: Mapping[str, float]) -> Any:
        # a long chunk's third request comes a chunk and more in, well past tick 3
        if ending == "an error" and (runner.requests == 3 if long else observation.tick == 3):
            raise RuntimeError("the policy fell over")
        return answer(observation, sent)

    runner = ScriptedRunner(script, rate_hz=RATE, latency_ticks=HALF if long else 0)
    clock = SteppedClock()
    if timed_out and long:
        runner = Gated(script, rate_hz=RATE, latency_ticks=HALF)
        clock = GateClock(runner, HALF)
    _, transport, adapter, ex = await _backend(runner, clock=clock, arm=arm)
    timeout_s = ex.registry.get("manipulate").timeout_s
    sends = _recorded(arm, clock)
    start = clock.t
    if isinstance(clock, GateClock):
        clock.loop = transport._policy_loop
        clock.sleeps = 0
    parked = None
    if timed_out:
        parked = _parks(clock, lambda: sum(not _whole_hold(a) for _, _, a in sends) >= 3)
    running = asyncio.create_task(ex.run_verb("manipulate", {"instruction": "stack"}))
    beat: Heartbeat | None = None
    if parked is not None:
        try:
            await _until(parked.is_set)
        finally:
            if isinstance(runner, Gated):
                runner.open.set()  # the request out as it parked is answered, never taken in
        assert clock.t - start < MANIPULATE_S, "parked past the segment's own time"
        assert transport._bus[1] is None, "the loop's clock would move under a call on the bus"
        _loop_clock_on(timeout_s, monkeypatch)
    elif ending != "an error":
        await _until(lambda: len(sends) >= 3)
        if ending == "an abort":
            ex.abort.set()
        elif ending == "a heartbeat stop":

            async def lost() -> None:
                raise HeartbeatError("the arm did not answer: TimeoutError")

            transport.heartbeat = lost  # type: ignore[method-assign]
            beat = Heartbeat(adapter, asyncio.Event(), period_s=0.001)
            beat.start()
        elif ending == "an MCP stop":
            assert (await ex.run_verb("stop")).ok
        else:
            await adapter.close()
    if ending == "an abort":
        with pytest.raises(Aborted):
            await running
    else:
        result = await running
        assert not result.ok, result.summary
        if timed_out:
            # the executor's own timeout, neither a cancel nor an abort
            expected = f"manipulate timed out after {timeout_s:g}s; stopped"
            assert result.summary.startswith(expected), result.summary
    if beat is not None:
        await beat.stop()
    assert not transport.policy_running
    policy_cap = speed_cap(STEP, RATE)
    during = [cap for _, cap, action in sends if not _whole_hold(action)]
    assert during and all(cap == pytest.approx(policy_cap) for cap in during), [
        (cap, action) for _, cap, action in sends if cap != pytest.approx(policy_cap)
    ]
    # a stop that cancelled the segment mid call leaves that call on the wire, and the verbs'
    # cap waits for it, since a send reads the cap as it goes out (`_cap_back`); a stop cut
    # short itself, as the heartbeat's is by its own stop here, never gets to write it sooner
    await _until(lambda: transport._wedged is None or transport._wedged.done())
    await asyncio.sleep(0)  # the restore waiting on that call runs before this goes on
    assert arm.config.max_relative_target == float(STEP) != found
    assert isinstance(arm.config.max_relative_target, float)
    await adapter.close()


async def test_a_policy_send_that_outlives_its_deadline_still_goes_out_under_the_policy_s_cap() -> (
    None
):
    """A send that runs out the bus's deadline is still on the wire when the segment ends on the
    wedge it leaves, and LeRobot reads the cap only as that send goes out. The verbs' cap is put
    back once the send is back, never under it."""
    arm = _segment_arm()
    send = arm.send_action
    gate = threading.Event()
    late: list[float] = []
    policy_sends = 0

    def stuck_once(action: dict[str, float]) -> dict[str, float]:
        nonlocal policy_sends
        if not _whole_hold(action):
            policy_sends += 1
            if policy_sends == 3:
                gate.wait(10.0)  # on the wire past the deadline, reading the cap only after
                late.append(arm.config.max_relative_target)
        return send(action)

    arm.send_action = stuck_once  # type: ignore[method-assign]
    runner = ScriptedRunner(_swing(arm), rate_hz=RATE)
    _, transport, adapter, _ = await _backend(runner, arm=arm, timeout_s=0.1)
    try:
        ended = await _segment_end(transport, max_s=1e6)
        assert ended.how == "guard" and "did not answer a read" in ended.reason, ended
        assert late == [] and arm.config.max_relative_target == pytest.approx(speed_cap(STEP, RATE))
    finally:
        gate.set()
    await _until(lambda: bool(late))
    await _until(lambda: arm.config.max_relative_target == float(STEP))
    assert late == [pytest.approx(speed_cap(STEP, RATE))], late
    await adapter.close()


@pytest.mark.parametrize("where", ["a verb's send", "a stop", "a rest move"])
async def test_a_policy_s_cap_left_on_the_follower_is_taken_off_before_anything_else_is_sent(
    where: str,
) -> None:
    """As though a segment's own `finally` had not got the cap back: the hold, the rest move
    and every verb's send write the verbs' cap again before they send."""
    arm = _segment_arm()
    rest = {joint: 0.0 for joint in ("shoulder_pan", "elbow_flex")}
    runner = ScriptedRunner(_swing(arm), rate_hz=RATE)
    clock = SteppedClock()
    _, transport, adapter, ex = await _backend(runner, clock=clock, arm=arm, rest_pose=rest)
    ended = await _segment_end(transport, max_s=5 * PERIOD)
    assert ended.how == "time"
    arm.positions["shoulder_pan"] = _inside(arm, "shoulder_pan", 0.3)
    arm.config.max_relative_target = speed_cap(STEP, RATE)
    transport._policy_cap_on = True
    sends = _recorded(arm, clock)
    if where == "a verb's send":
        moved = await ex.run_verb(
            "move_joints", {"positions": {"shoulder_pan": _inside(arm, "shoulder_pan", 0.2)}}
        )
        assert moved.ok, moved.summary
    elif where == "a stop":
        await adapter.stop()
    else:
        parked = await adapter.go_to_rest()
        assert parked.reached, parked.reason
    assert sends and all(cap == float(STEP) for _, cap, _ in sends), sends
    await adapter.close()


# ── the chunk queue ─────────────────────────────────────────────────────────────────────


async def test_a_chunk_s_played_ticks_are_dropped_and_the_rest_replaces_the_queue_s_tail() -> None:
    """Chunks of eight, held back two ticks each on a lockstep clock, asked for again once
    four are left, half the chunk and twice the latency both (`refill_at`). The chunk asked at
    tick 4 lands at tick 6: its goals for ticks 4 and 5 are dropped, and from tick 6 on it
    replaces what was left of the first, whose goals for ticks 6 and 7 are never sent."""
    assert refill_at(2, 8) == 4, "the ticks below are worked out for four left"
    clock = LockstepClock()
    runner = ScriptedRunner(_tagged(8), rate_hz=RATE, latency_ticks=2)
    arm, transport, adapter, _ = await _backend(runner, clock=clock)
    sends = _recorded(arm, clock)
    start = clock.t
    ended = await _segment_end(transport, max_s=12 * PERIOD)
    assert ended.how == "time", ended.reason
    played = [_untag(pan) for pan in _pans(sends)]
    assert played[:9] == [
        (0, 2),
        (0, 3),
        (0, 4),
        (0, 5),
        (4, 2),
        (4, 3),
        (4, 4),
        (4, 5),
        (8, 2),
    ], played
    assert (0, 6) not in played and (0, 7) not in played
    for (at, _, _), (asked, index) in zip(sends, played, strict=True):
        tick = round((at - start) / PERIOD)
        assert asked + index == tick, "a goal went out on a tick it was not for"
        assert tick - asked >= 2, "a chunk was played before its latency had passed"
    await adapter.close()


CHUNK_S = 2.0
"""How long a long chunk lasts at the test's rate: long enough that half of it is many ticks."""
LONG = round(CHUNK_S * RATE)
HALF = LONG // 2
LATENCIES = {
    "under half a chunk": HALF - HALF // 3,
    "half a chunk": HALF,
    "over half a chunk": HALF + math.ceil(STARVE_S * RATE / 4),
}
"""Latencies in ticks, for a chunk of `LONG`. The one over half starves every chunk after the
first for less than `STARVE_S`, so a segment of it runs to its time and its gaps can be counted."""


def _gaps(sends: Sequence[tuple[float, float, dict[str, float]]], start: float) -> list[int]:
    """The runs of ticks with nothing sent, between one send and the next."""
    ticks = [round((at - start) / PERIOD) for at, _, action in sends if not _whole_hold(action)]
    return [b - a - 1 for a, b in itertools.pairwise(ticks) if b - a > 1]


@pytest.mark.parametrize("latency", list(LATENCIES))
async def test_a_long_chunk_is_asked_for_again_before_it_runs_out(latency: str) -> None:
    """A chunk `CHUNK_S` long, from a runner that declares a latency under half of it, exactly
    half, or over half. It is asked again once what is left of it is down to half of it or twice
    that latency, whichever is more, or as it lands where less is left, so up to half a chunk
    an answer lands before the queue runs out, and the arm is starved only while a segment's
    first chunk is on its way. Over half, no rule could keep it fed, since the next request
    goes out no sooner than the last chunk lands: every chunk after the first starves what
    `starved_each_chunk` says, which is what `quackd policy serve` refuses to declare. The
    lockstep clock and the wall's kind play the same goals at the same times either way."""
    k = LATENCIES[latency]
    ticks = 5 * LONG
    runs: list[tuple[list[tuple[float, float, dict[str, float]]], SegmentEnd]] = []
    for lockstep in (True, False):
        arm = _segment_arm()
        swing = _swing(arm)

        def script(o: Observation, s: Mapping[str, float], swing: Any = swing) -> list[Any]:
            return [swing(Observation(o.tick + i, o.reading), s)[0] for i in range(LONG)]

        if lockstep:
            runner: ScriptedRunner = ScriptedRunner(script, rate_hz=RATE, latency_ticks=k)
            clock: SteppedClock = LockstepClock()
        else:
            runner = Gated(script, rate_hz=RATE, latency_ticks=k)
            clock = GateClock(runner, k)
        _, transport, adapter, _ = await _backend(runner, clock=clock, arm=arm)
        if isinstance(clock, GateClock):
            clock.loop = transport._policy_loop
            clock.sleeps = 0
        sends = _recorded(arm, clock)
        start = clock.t
        try:
            ended = await _segment_end(transport, max_s=ticks * PERIOD)
        finally:
            if isinstance(runner, Gated):
                runner.open.set()
        assert ended.how == "time", ended.reason
        assert ended.stats.ticks == ticks, ended.stats
        runs.append(([(at - start, cap, action) for at, cap, action in sends], ended))
        await adapter.close()
    (lockstep_sends, lockstep_end), (wall_sends, wall_end) = runs
    assert lockstep_sends == wall_sends
    assert lockstep_end.stats == wall_end.stats
    stats = lockstep_end.stats
    assert lockstep_sends[0][0] == pytest.approx(k * PERIOD), "the first chunk was not held back"
    starved = starved_each_chunk(k, LONG)
    gaps = _gaps(lockstep_sends, 0.0)
    if k <= HALF:
        assert starved == 0 and gaps == [], gaps
        assert stats.starved == k, stats
    else:
        assert gaps and gaps == [starved] * len(gaps), gaps
        assert len(gaps) == stats.chunks - 1, (gaps, stats)
        assert stats.starved == k + sum(gaps) + (ticks - 1 - round(lockstep_sends[-1][0] / PERIOD))


class LateClock(GateClock):
    """A `GateClock` that answers each request `late` names, by its number from one, that many
    ticks after it is asked, and every other `k` ticks after."""

    def __init__(self, runner: Gated, k: int, late: Mapping[int, int]) -> None:
        super().__init__(runner, k)
        self.usual = k
        self.late = late
        self.asked: list[int] = []

    async def sleep(self, seconds: float) -> None:
        req = self.loop._inflight if self.loop is not None else None
        if req is not None and req.tick not in self.asked:
            self.asked.append(req.tick)
        nth = self.asked.index(req.tick) + 1 if req is not None else 0
        self.k = self.late.get(nth, self.usual)
        await super().sleep(seconds)


THIRD = LONG // 3
LATE = {
    "a tick declared, every answer in half a chunk": (1, HALF, {}),
    "a third of half declared, every answer in half a chunk": (HALF // 3, HALF, {}),
    "a third of a chunk declared, one answer in twice that": (
        THIRD,
        THIRD,
        {2: REFILL_LATENCIES * THIRD},
    ),
}
"""A declared latency in ticks, how many ticks an answer takes on the wall's kind of clock, and
the answers, by number, that take longer, for a chunk of `LONG`."""


@pytest.mark.parametrize("case", list(LATE))
async def test_an_answer_slower_than_declared_still_lands_before_the_queue_runs_out(
    case: str,
) -> None:
    """A policy's answers can take longer on the arm than the latency it declares, and one in
    twenty does by the latency a bench suggests. Every answer within half a chunk still lands
    with something queued, however short the latency declared, since the next is asked for once
    half the chunk is left, if not sooner. And where twice the latency is more than half a
    chunk and a chunk holds three of them, the next is asked for with twice the latency left,
    so one answer that takes twice as long as declared lands in time too. The arm is starved
    only while the segment's first chunk is on its way."""
    declared, usual, late = LATE[case]
    if late:
        assert REFILL_LATENCIES * declared > longest_latency(LONG), "half a chunk would cover it"
        assert (REFILL_LATENCIES + 1) * declared <= LONG, "the chunk does not hold it"
    ticks = 5 * LONG
    arm = _segment_arm()
    runner = Gated(_long(_swing(arm)), rate_hz=RATE, latency_ticks=declared)
    clock = LateClock(runner, usual, late)
    _, transport, adapter, _ = await _backend(runner, clock=clock, arm=arm)
    clock.loop = transport._policy_loop
    clock.sleeps = 0
    sends = _recorded(arm, clock)
    start = clock.t
    try:
        ended = await _segment_end(transport, max_s=ticks * PERIOD)
    finally:
        runner.open.set()
    await adapter.close()
    assert ended.how == "time", ended.reason
    assert ended.stats.ticks == ticks, ended.stats
    assert len(clock.asked) > max(late, default=1), "the slow answer was never asked for"
    assert _gaps([(at - start, cap, action) for at, cap, action in sends], 0.0) == []
    assert ended.stats.starved == late.get(1, usual), ended.stats


@pytest.mark.parametrize("k", [1, HALF], ids=["a tick late", "half a chunk late"])
async def test_a_runner_that_declares_no_latency_is_never_waited_for_with_actions_queued(
    k: int,
) -> None:
    """A server started with no `--latency-s` declares none, and still takes a round trip to
    answer, here `k` ticks on the wall's kind of clock. Its first chunk is waited for in the
    tick that asked, with nothing to play until it lands. Every chunk after that is asked for
    with half the last still queued and left to land while the arm plays it, as for a declared
    latency, so the arm holds still for no round trip after the first chunk, and an answer up
    to half a chunk late still lands with something queued."""
    ticks = 5 * LONG
    arm = _segment_arm()
    runner = Gated(_long(_swing(arm)), rate_hz=RATE)
    assert runner.latency_s() == 0
    runner.gate(0).set()  # the first chunk answers at once, in the tick that asked for it
    clock = GateClock(runner, k)
    _, transport, adapter, _ = await _backend(runner, clock=clock, arm=arm)
    clock.loop = transport._policy_loop
    clock.sleeps = 0
    sends = _recorded(arm, clock)
    start = clock.t
    try:
        ended = await _segment_end(transport, max_s=ticks * PERIOD)
    finally:
        runner.open.set()
    await adapter.close()
    assert ended.how == "time", ended.reason
    assert ended.stats.ticks == ticks and ended.stats.chunks >= 5, ended.stats
    assert ended.stats.skipped == 0 and ended.stats.starved == 0, ended.stats
    assert _gaps([(at - start, cap, action) for at, cap, action in sends], 0.0) == []


async def test_an_empty_queue_sends_nothing_and_ends_the_segment_after_the_starve_time() -> None:
    clock = LockstepClock()

    def once(observation: Observation, _sent: Mapping[str, float]) -> list[dict[str, float]]:
        return [{"shoulder_pan": _tag(0, i)} for i in range(3)] if observation.tick == 0 else []

    runner = ScriptedRunner(once, rate_hz=RATE)
    arm, transport, adapter, _ = await _backend(runner, clock=clock)
    sends = _recorded(arm, clock)
    start = clock.t
    ended = await _segment_end(transport, max_s=20 * STARVE_S)
    assert ended.how == "starved" and "gave no action" in ended.reason, ended.reason
    goals = [action for _, _, action in sends]
    assert len(goals) == 4 and _whole_hold(goals[-1]), goals
    # three goals, then nothing for `STARVE_S`, then the hold
    dry = start + 3 * PERIOD
    assert sends[-1][0] - dry == pytest.approx(STARVE_S, abs=PERIOD + 1e-9)
    assert ended.stats.starved == pytest.approx(STARVE_S * RATE, abs=2)
    await adapter.close()


@pytest.mark.parametrize("waits", ["inside the grace", "past the grace"])
async def test_a_segment_s_first_chunk_gets_its_own_grace(waits: str) -> None:
    """A first chunk held back longer than `STARVE_S` and shorter than `FIRST_CHUNK_S` is waited
    for; one held back longer than the grace ends the segment before anything is sent."""
    assert STARVE_S < FIRST_CHUNK_S
    inside = math.ceil((STARVE_S + FIRST_CHUNK_S) / 2 * RATE)
    past = math.ceil(FIRST_CHUNK_S * RATE) + 2
    k = inside if waits == "inside the grace" else past
    runner = ScriptedRunner(_tagged(k + 4), rate_hz=RATE, latency_ticks=k)
    clock = LockstepClock()
    arm, transport, adapter, _ = await _backend(runner, clock=clock)
    ended = await _segment_end(transport, max_s=(k + 2) * PERIOD)
    if waits == "inside the grace":
        assert ended.how == "time", ended.reason
        assert [_untag(p) for p in _pans([(0, 0, a) for a in arm.actions])][:2] == [
            (0, k),
            (0, k + 1),
        ]
    else:
        assert ended.how == "starved" and "waiting for a first chunk" in ended.reason, ended
        assert all(_whole_hold(a) for a in arm.actions), arm.actions
    await adapter.close()


class FirstLateClock(SteppedClock):
    """The wall's kind of clock, on which the segment's first request is answered at tick
    `first` and every later one by the tick after it was asked. `sleeps` counts the loop's ticks
    from when a test zeroes it, just before the `do`."""

    def __init__(self, runner: Gated, first: int) -> None:
        super().__init__()
        self.runner = runner
        self.first = first
        self.sleeps = 0
        self.loop: PolicyLoop | None = None

    async def sleep(self, seconds: float) -> None:
        self.t += seconds
        self.sleeps += 1
        req = self.loop._inflight if self.loop is not None else None
        if req is not None and not req.future.done():
            due = self.first if req.tick == 0 else req.tick + 1
            if due <= self.sleeps:
                self.runner.gate(req.tick).set()
                await _answer(req.future)
        await asyncio.sleep(0)


async def test_a_first_chunk_that_lands_too_late_to_play_leaves_the_grace_running() -> None:
    """The first inference is the slow one: its chunk lands past `STARVE_S` and inside the
    grace, and is all for ticks already played. It is thrown away and counts for nothing, and
    the segment goes on to play the next one rather than calling that wait starvation."""
    assert STARVE_S < FIRST_CHUNK_S
    first = math.ceil((STARVE_S + FIRST_CHUNK_S) / 2 * RATE)
    arm = _segment_arm()
    near, far = _inside(arm, "shoulder_pan", 0.1), _inside(arm, "shoulder_pan", -0.1)

    def script(observation: Observation, _sent: Mapping[str, float]) -> list[dict[str, float]]:
        # the first answers for fewer ticks than it took, the next for plenty
        if observation.tick == 0:
            return [{"shoulder_pan": near}] * (first // 2)
        return [{"shoulder_pan": far}] * first

    runner = Gated(script, rate_hz=RATE, latency_ticks=1)
    clock = FirstLateClock(runner, first)
    _, transport, adapter, _ = await _backend(runner, clock=clock, arm=arm)
    clock.loop = transport._policy_loop
    clock.sleeps = 0
    try:
        ended = await _segment_end(transport, max_s=(first + 5) * PERIOD)
    finally:
        runner.open.set()
    assert ended.how == "time", ended
    pans = _pans([(0, 0, action) for action in arm.actions])
    assert near not in pans and far in pans, pans
    assert ended.stats.chunks == 1 and ended.stats.stale == 1, ended.stats
    await adapter.close()


@pytest.mark.parametrize("first", ["late", "empty", "stamped"])
async def test_a_segment_s_chunks_are_the_ones_it_played(first: str) -> None:
    """`max_chunks` counts the chunks a segment played, never the requests it made. A first
    answer with nothing to play, empty or stamped with another tick, is asked over and the next
    one is the segment's chunk; a runner whose every chunk is for ticks already played ends on
    its time, inside the grace, rather than calling its chunks played."""
    k = 3
    if first == "late":
        # each chunk covers fewer ticks than it takes to land, so all of it has been played
        runner = Misbehaving(_tagged(k), rate_hz=RATE, latency_ticks=k)
    else:
        runner = Misbehaving(_tagged(k), how=first, at=1, rate_hz=RATE)
    runner.open.set()
    arm, transport, adapter, _ = await _backend(runner, clock=LockstepClock())
    ticks = 4 * k
    assert ticks * PERIOD < FIRST_CHUNK_S
    ended = await _segment_end(transport, max_s=ticks * PERIOD, max_chunks=1)
    played = [_untag(pan) for pan in _pans([(0, 0, action) for action in arm.actions])]
    if first == "late":
        assert ended.how == "time" and ended.stats.chunks == 0 and played == [], ended
    else:
        assert ended.how == "chunks" and ended.reason == "its 1 chunks were played", ended
        assert ended.stats.chunks == 1, ended.stats
        assert ended.stats.stale == (1 if first == "stamped" else 0), ended.stats
        assert played == [(1, index) for index in range(k)], played
    await adapter.close()


async def test_a_first_chunk_the_grace_runs_out_on_is_said_as_the_whole_grace() -> None:
    """A runner that answers at once is waited for in its tick, with nothing to play, on the
    wall's kind of clock, for whatever is left of the grace. One that runs it out is said to
    have given nothing for the grace, never for the sliver of it the last wait had."""
    edge = math.floor(FIRST_CHUNK_S * RATE) - 1
    assert 0 < FIRST_CHUNK_S - edge * PERIOD < STARVE_S
    runner = Gated(lambda o, s: [], rate_hz=RATE)
    for tick in range(edge):
        runner.gate(tick).set()  # an empty answer at once, up to the tick it stops answering
    _, transport, adapter, _ = await _backend(runner)
    try:
        ended = await asyncio.wait_for(_segment_end(transport, max_s=1e6), 10.0)
    finally:
        runner.open.set()
    assert ended.how == "starved", ended
    assert ended.reason == (
        f"the policy gave no action for {FIRST_CHUNK_S:g} s, waiting for a first chunk"
    ), ended.reason
    await adapter.close()


async def test_an_answer_that_lands_between_a_tick_s_look_and_its_ask_is_played() -> None:
    """The runner's thread answers whenever it likes, here right after a tick has looked for an
    answer and before it decides whether to ask again. That answer is still the one request
    out: it is taken in on the next tick and played, never asked over and lost."""
    chunk, k = 6, 1
    racing = chunk - refill_at(k, chunk)  # when what is left of the first chunk asks for more
    arm = _segment_arm()
    runner = Gated(_tagged(chunk), rate_hz=RATE, latency_ticks=k)
    clock = GateClock(runner, k, leave=racing)
    _, transport, adapter, _ = await _backend(runner, clock=clock, arm=arm)
    loop = transport._policy_loop
    assert loop is not None
    clock.loop = loop
    clock.sleeps = 0
    take_in = loop._take_in
    raced: list[int] = []

    def then_answer(*args: Any) -> None:
        take_in(*args)
        req = loop._inflight
        if req is not None and req.tick == racing and not req.future.done():
            runner.gate(req.tick).set()
            req.future.result(timeout=10.0)
            raced.append(req.tick)

    loop._take_in = then_answer  # type: ignore[method-assign]
    try:
        ended = await _segment_end(transport, max_s=12 * PERIOD)
    finally:
        runner.open.set()
    assert ended.how == "time", ended
    assert raced == [racing], "the race was not run"
    played = [_untag(pan) for pan in _pans([(0, 0, a) for a in arm.actions])]
    assert any(asked == racing for asked, _ in played), played
    await adapter.close()


async def test_at_most_one_request_is_outstanding() -> None:
    """The runner has not answered, and the pacer keeps ticking: nothing more is asked of it."""
    arm = _segment_arm()
    runner = Gated(_tagged(4), rate_hz=RATE, latency_ticks=1)
    _, transport, adapter, _ = await _backend(runner, arm=arm)
    assert transport._policy_loop is not None
    transport._policy_loop.first_chunk_s = 1e9
    try:
        assert (await transport.send_intent(_do("manipulate", 1e6))).accepted
        clock = transport.clock
        assert isinstance(clock, SteppedClock)
        await _until(lambda: clock.t > 20 * PERIOD)
        assert runner.requests == 0 and len(runner.threads) == 1
    finally:
        runner.open.set()
        await adapter.close()


# ── epochs and the runner's own thread ──────────────────────────────────────────────────


async def test_a_chunk_asked_for_in_an_earlier_segment_is_thrown_away() -> None:
    """The first segment is stopped with its request still on the runner. The next one's reset
    waits behind it, and the answer, which comes back once the next segment has begun, is the
    earlier segment's and is never played."""
    arm = _segment_arm()
    epochs: list[int] = []

    def by_epoch(observation: Observation, _sent: Mapping[str, float]) -> list[dict[str, float]]:
        return [{"shoulder_pan": float(runner.resets)}] * 4

    runner = Gated(by_epoch, rate_hz=RATE, latency_ticks=1)
    _, transport, adapter, _ = await _backend(runner, arm=arm)
    loop = transport._policy_loop
    assert loop is not None
    loop.first_chunk_s = 1e9
    try:
        assert (await transport.send_intent(_do("manipulate", 1e6))).accepted
        await _until(runner.inside.is_set)
        epochs.append(loop.epoch)
        await adapter.stop()
        assert not transport.policy_running and arm.actions and _whole_hold(arm.actions[-1])
        before = len(arm.actions)
        second = asyncio.create_task(_segment_end(transport, max_s=10 * PERIOD))
        await asyncio.sleep(0.05)
        runner.open.set()
        ended = await second
        epochs.append(loop.epoch)
        assert epochs == [1, 2]
        assert ended.stats.stale >= 1, ended.stats
        pans = [a["shoulder_pan.pos"] for a in arm.actions[before:] if "shoulder_pan.pos" in a]
        assert pans and 1.0 not in pans and 2.0 in pans, pans
    finally:
        runner.open.set()
        await adapter.close()


async def test_a_runner_that_never_answers_never_holds_up_the_bus() -> None:
    """Its calls are on a worker of its own, so the reads, the heartbeat and the stop go on
    around it, and a segment after it is refused rather than started behind it."""
    arm = _segment_arm()
    bus_threads: list[str] = []
    read = arm.get_observation

    def noted() -> dict[str, Any]:
        bus_threads.append(threading.current_thread().name)
        return read()

    arm.get_observation = noted  # type: ignore[method-assign]
    runner = Gated(_tagged(4), rate_hz=RATE, latency_ticks=1)
    _, transport, adapter, _ = await _backend(runner, arm=arm)
    loop = transport._policy_loop
    assert loop is not None
    loop.first_chunk_s = 1e9
    try:
        assert (await transport.send_intent(_do("manipulate", 1e6))).accepted
        await _until(runner.inside.is_set)
        state = await asyncio.wait_for(adapter.get_state(), 2.0)
        assert state.extras["torque"] is True
        await asyncio.wait_for(adapter.heartbeat(), 2.0)
        await asyncio.wait_for(adapter.stop(), 2.0)
        assert _whole_hold(arm.actions[-1]) and transport.stop_error is None
        assert runner.threads and all(name.startswith(WORKER) for name in runner.threads)
        assert bus_threads and not any(name.startswith(WORKER) for name in bus_threads)
        loop.reset_s = 0.2
        refused = await transport.send_intent(_do("manipulate", 1.0))
        assert not refused.accepted and "had not come back after" in (refused.reason or "")
        assert runner.resets == 1, "the second reset ran beside the stuck inference"
    finally:
        runner.open.set()
        await adapter.close()


async def test_a_runner_that_answers_at_once_but_does_not_starves_its_tick_and_is_held() -> None:
    """A runner that declares no latency is awaited in its own tick for a first chunk, with
    nothing to play meanwhile, for no longer than the segment's patience on the wall's clock,
    and then the segment ends with the arm held."""
    arm = _segment_arm()
    runner = Gated(_tagged(4), rate_hz=RATE)
    _, transport, adapter, _ = await _backend(runner, arm=arm)
    loop = transport._policy_loop
    assert loop is not None
    loop.first_chunk_s = 0.2
    try:
        ended = await asyncio.wait_for(_segment_end(transport, max_s=1e6), 5.0)
        assert ended.how == "starved" and "waiting for a first chunk" in ended.reason, ended
        assert all(_whole_hold(a) for a in arm.actions), arm.actions
        assert (await asyncio.wait_for(adapter.get_state(), 2.0)).extras["torque"] is True
    finally:
        runner.open.set()
        await adapter.close()


class ShortClock(LockstepClock):
    """A lockstep clock that wakes a hair short of what each sleep asked for, inside what the
    pacer takes as due (`EXACT`), as the difference of two of the simulator's times can read."""

    async def sleep(self, seconds: float) -> None:
        await super().sleep(seconds - EXACT / 2)


async def test_a_segment_plays_its_seconds_worth_of_ticks_and_the_record_counts_them() -> None:
    """A segment given so many periods plays that many ticks and not one more, even where the
    clock reads the tick its time runs out on a hair short of that time, and what each segment
    says it counted is what the run's record adds up: the ticks, the seconds and the rate."""
    periods, segments = 4 * round(RATE), 3
    arm = _segment_arm()
    runner = ScriptedRunner(_swing(arm), rate_hz=RATE)
    _, transport, adapter, _ = await _backend(runner, clock=ShortClock(), arm=arm)
    loop = transport._policy_loop
    assert loop is not None
    ends = [await _segment_end(transport, max_s=periods * PERIOD) for _ in range(segments)]
    assert [(e.how, e.stats.ticks) for e in ends] == [("time", periods)] * segments, ends
    record = loop.record()
    assert record["segments"] == segments and record["ticks"] == segments * periods, record
    assert record["seconds"] == pytest.approx(segments * periods * PERIOD, abs=0.01), record
    assert all(e.stats.hz == record["hz"] == pytest.approx(RATE) for e in ends), (ends, record)
    await adapter.close()


# ── lockstep and the wall ───────────────────────────────────────────────────────────────


async def test_a_runner_three_ticks_slow_plays_one_trajectory_in_lockstep_and_on_the_wall() -> None:
    """On the lockstep clock the chunk is computed at once and held back three ticks; on the
    wall's kind it comes back from the runner's thread three ticks after it was asked. Either
    way the arm is sent the same goals at the same times, and the segments count the same."""
    k = 3
    ticks = 40
    runs: list[tuple[list[tuple[float, float, dict[str, float]]], SegmentEnd]] = []
    for lockstep in (True, False):
        arm = _segment_arm()
        if lockstep:
            runner: ScriptedRunner = ScriptedRunner(_tagged(8), rate_hz=RATE, latency_ticks=k)
            clock: SteppedClock = LockstepClock()
        else:
            runner = Gated(_tagged(8), rate_hz=RATE, latency_ticks=k)
            clock = GateClock(runner, k)
        _, transport, adapter, _ = await _backend(runner, clock=clock, arm=arm)
        if isinstance(clock, GateClock):
            clock.loop = transport._policy_loop
            clock.sleeps = 0
        sends = _recorded(arm, clock)
        start = clock.t
        try:
            ended = await _segment_end(transport, max_s=ticks * PERIOD)
        finally:
            # a request the segment's end left out would keep its gate, and the thread, shut
            if isinstance(runner, Gated):
                runner.open.set()
        assert ended.how == "time", ended.reason
        runs.append(([(at - start, cap, action) for at, cap, action in sends], ended))
        await adapter.close()
    (lockstep_sends, lockstep_end), (wall_sends, wall_end) = runs
    assert lockstep_sends == wall_sends
    assert lockstep_end.stats == wall_end.stats
    first = _untag(_pans(lockstep_sends)[0])
    assert first == (0, k), "the first chunk was not held back its latency"
    assert lockstep_sends[0][0] == pytest.approx(k * PERIOD)


@pytest.mark.parametrize("how", ["stamped", "raises"])
async def test_an_answer_thrown_away_or_raised_lands_its_latency_on_in_lockstep_as_on_the_wall(
    how: str,
) -> None:
    """A runner three ticks slow whose second answer is stamped with another tick, or raises.
    On the arm the loop meets that answer only when it lands, three ticks after it was asked,
    and the lockstep clock holds the whole answer back to that tick before it judges it. So the
    arm is sent the same goals at the same times either way, and the segments end alike, with
    the counts up to their end, an error's included."""
    k = 3
    ticks = 20
    runs: list[tuple[list[tuple[float, float, dict[str, float]]], SegmentEnd]] = []
    for lockstep in (True, False):
        arm = _segment_arm()
        runner = Misbehaving(_tagged(8), how=how, at=2, rate_hz=RATE, latency_ticks=k)
        clock: SteppedClock = LockstepClock() if lockstep else GateClock(runner, k)
        if lockstep:
            runner.open.set()
        _, transport, adapter, _ = await _backend(runner, clock=clock, arm=arm)
        if isinstance(clock, GateClock):
            clock.loop = transport._policy_loop
            clock.sleeps = 0
        sends = _recorded(arm, clock)
        start = clock.t
        try:
            ended = await _segment_end(transport, max_s=ticks * PERIOD)
        finally:
            runner.open.set()
        runs.append(([(at - start, cap, action) for at, cap, action in sends], ended))
        await adapter.close()
    (lockstep_sends, lockstep_end), (wall_sends, wall_end) = runs
    assert lockstep_sends == wall_sends
    assert (lockstep_end.how, lockstep_end.reason) == (wall_end.how, wall_end.reason)
    assert lockstep_end.stats == wall_end.stats
    if how == "raises":
        assert lockstep_end.how == "error", lockstep_end
        assert "the policy lost its place" in lockstep_end.reason
        assert lockstep_end.stats.ticks > k and lockstep_end.stats.chunks > 0, lockstep_end.stats
    else:
        assert lockstep_end.how == "time" and lockstep_end.stats.stale == 1, lockstep_end


async def test_a_runner_that_raises_leaves_asyncio_nothing_to_log() -> None:
    """The loop waits on a runner's answer through a copy of it on the event loop. The error in
    it is the segment's to report, and the copy is marked read, so nothing is logged as never
    retrieved when it is collected, whichever kind of clock the segment ran on."""
    logged: list[str] = []
    loop = asyncio.get_running_loop()
    loop.set_exception_handler(lambda _loop, context: logged.append(str(context.get("message"))))
    try:
        for clock in (LockstepClock(), SteppedClock()):
            runner = Misbehaving(_tagged(4), how="raises", at=1, rate_hz=RATE)
            runner.open.set()
            _, transport, adapter, _ = await _backend(runner, clock=clock)
            ended = await _segment_end(transport, max_s=1e6)
            assert ended.how == "error", ended
            await adapter.close()
            del transport, adapter, runner, ended
            gc.collect()
    finally:
        loop.set_exception_handler(None)
    assert not [message for message in logged if "never retrieved" in message], logged


# ── tick mode ───────────────────────────────────────────────────────────────────────────


async def test_a_runner_asked_every_tick_plays_one_tick_late_in_lockstep_and_on_the_wall() -> None:
    """A runner in tick mode that takes a tick to answer: on the lockstep clock its answer is
    held back the tick, and on the wall's kind it comes back from its thread by the next tick.
    Either way it is asked every tick and each answer is played the tick after it was asked."""
    k = 1
    ticks = 10
    runs: list[tuple[list[tuple[float, float, dict[str, float]]], SegmentEnd]] = []
    for lockstep in (True, False):
        arm = _segment_arm()
        if lockstep:
            runner: ScriptedRunner = ScriptedRunner(
                _tagged(1), rate_hz=RATE, latency_ticks=k, per_tick=True
            )
            clock: SteppedClock = LockstepClock()
        else:
            runner = Gated(_tagged(1), rate_hz=RATE, latency_ticks=k, per_tick=True)
            clock = GateClock(runner, k)
        _, transport, adapter, _ = await _backend(runner, clock=clock, arm=arm)
        if isinstance(clock, GateClock):
            clock.loop = transport._policy_loop
            clock.sleeps = 0
        sends = _recorded(arm, clock)
        start = clock.t
        ended = await _segment_end(transport, max_s=ticks * PERIOD)
        assert ended.how == "time", ended.reason
        asked = [_untag(p) for p in _pans(sends)]
        assert asked == [(tick, 0) for tick in range(ticks - k)], asked
        assert runner.requests >= ticks - k, "a runner in tick mode is asked every tick"
        runs.append(([(at - start, cap, action) for at, cap, action in sends], ended))
        await adapter.close()
    (lockstep_sends, lockstep_end), (wall_sends, wall_end) = runs
    assert lockstep_sends == wall_sends
    assert lockstep_end.stats == wall_end.stats


@pytest.mark.parametrize("lockstep", [True, False], ids=["lockstep", "the wall's kind"])
async def test_a_runner_asked_every_tick_that_needs_more_than_a_tick_is_refused(
    lockstep: bool,
) -> None:
    """It is asked again only once it has answered, so it could never answer every tick on the
    arm, and the simulator does not rehearse a delay line the arm could not play."""
    k = 2
    runner = ScriptedRunner(_tagged(1), rate_hz=RATE, latency_ticks=k, per_tick=True)
    clock = LockstepClock() if lockstep else SteppedClock()
    arm, transport, adapter, _ = await _backend(runner, clock=clock)
    ack = await transport.send_intent(_do("manipulate", 10 * PERIOD))
    assert not ack.accepted, ack
    assert f"which is {k} ticks at {RATE:g} Hz" in (ack.reason or ""), ack.reason
    assert "has to answer within a tick" in (ack.reason or "")
    assert runner.requests == 0 and arm.actions == []
    await adapter.close()


@pytest.mark.parametrize("missed", ["the pacer skipped it", "it had not answered"])
async def test_a_tick_a_runner_in_tick_mode_never_saw_ends_the_segment_and_resets_it(
    missed: str,
) -> None:
    clock = SteppedClock()
    arm = _segment_arm()
    swing = _swing(arm)
    runner: ScriptedRunner
    if missed == "the pacer skipped it":

        def slow_at_three(observation: Observation, sent: Mapping[str, float]) -> Any:
            if observation.tick == 3:
                clock.t += 1.5 * PERIOD
            return swing(observation, sent)

        runner = ScriptedRunner(slow_at_three, rate_hz=RATE, per_tick=True)
    else:
        runner = Gated(swing, rate_hz=RATE, per_tick=True, latency_ticks=1)
    _, transport, adapter, _ = await _backend(runner, clock=clock, arm=arm)
    try:
        ended = await _segment_end(transport, max_s=1e6)
        assert ended.how == "guard" and "has to be asked every tick" in ended.reason, ended
        assert _whole_hold(arm.actions[-1])
        if isinstance(runner, Gated):
            runner.open.set()
            assert "had not answered tick 0 by tick 1" in ended.reason
        else:
            assert ended.stats.skipped == 1
        await _until(lambda: runner.resets == 2)
    finally:
        if isinstance(runner, Gated):
            runner.open.set()
        await adapter.close()


# ── manipulate ──────────────────────────────────────────────────────────────────────────


async def test_manipulate_is_ok_when_it_ran_and_never_says_the_task_is_done() -> None:
    arm = _segment_arm()
    runner = ScriptedRunner(_swing(arm), rate_hz=RATE)
    _, _, adapter, ex = await _backend(runner, arm=arm)
    ran = await ex.run_verb("manipulate", {"instruction": "put the block in the cup"})
    assert ran.ok and ran.data["ended"] == "time", ran.summary
    assert ran.data["seconds"] == pytest.approx(MANIPULATE_S)
    assert f"its {MANIPULATE_S:g} s ran out" in ran.summary
    assert "Nothing on the arm says whether it did the task" in ran.summary
    assert ran.data["chunks"] == round(MANIPULATE_S * RATE)
    assert ran.data["hz"] == pytest.approx(RATE, abs=0.1)
    assert runner.instruction == "put the block in the cup"
    assert ex.registry.get("manipulate").timeout_s == MANIPULATE_TIMEOUT_S
    await adapter.close()


async def test_manipulate_ends_on_its_chunks_and_on_a_stall_and_is_ok_on_both() -> None:
    arm = _segment_arm()

    def twice(observation: Observation, _sent: Mapping[str, float]) -> list[Any] | None:
        return None if runner.requests > 2 else [{"shoulder_pan": _tag(observation.tick, 0)}]

    runner = ScriptedRunner(twice, rate_hz=RATE)
    _, _, adapter, ex = await _backend(runner, arm=arm)
    done = await ex.run_verb("manipulate", {"instruction": "wave"})
    assert done.ok and done.data["ended"] == "chunks", done.summary
    assert "the policy said it was done after 2 chunks" in done.summary
    await adapter.close()

    arm = _segment_arm()
    here = arm.positions["shoulder_pan"]
    still = ScriptedRunner(lambda o, s: [{"shoulder_pan": here}], rate_hz=RATE)
    _, _, adapter, ex = await _backend(still, arm=arm)
    stalled = await ex.run_verb("manipulate", {"instruction": "wave"})
    assert stalled.ok and stalled.data["ended"] == "stall", stalled.summary
    assert stalled.data["seconds"] == pytest.approx(STALL_S, abs=2 * PERIOD)
    await adapter.close()

    arm = _segment_arm()
    _, transport, adapter, _ = await _backend(ScriptedRunner(_swing(arm), rate_hz=RATE), arm=arm)
    ended = await _segment_end(transport, max_s=1e6, max_chunks=4)
    assert ended.how == "chunks" and ended.reason == "its 4 chunks were played", ended
    assert ended.stats.chunks == 4
    await adapter.close()


async def test_a_stall_against_something_in_the_way_holds_the_arm_and_is_still_ok() -> None:
    """The policy keeps asking for an elbow angle something stops the arm reaching. The segment
    ends on the stall, which is a segment that ran, and the arm is held where it stopped rather
    than left pushing toward the policy's last goal while the pilot thinks."""
    arm = _segment_arm()
    arm.stuck = {"elbow_flex"}
    here = arm.positions["elbow_flex"]
    goal = _inside(arm, "elbow_flex", 0.5)
    assert goal != here
    runner = ScriptedRunner(lambda o, s: [{"elbow_flex": goal}], rate_hz=RATE)
    _, transport, adapter, ex = await _backend(runner, arm=arm)
    ran = await ex.run_verb("manipulate", {"instruction": "press down"})
    assert ran.ok and ran.data["ended"] == "stall", ran.summary
    last = arm.actions[-1]
    assert _whole_hold(last) and last["elbow_flex.pos"] == pytest.approx(here), last
    assert not transport.policy_running
    await adapter.close()


@pytest.mark.parametrize("ending", ["starved", "guard"])
async def test_a_long_chunk_s_segment_still_ends_on_starving_and_on_a_guard(ending: str) -> None:
    """With the next chunk asked for as the last lands, half a chunk of latency calling for no
    later, a runner that stops answering anything to play still starves the segment `STARVE_S`
    after its last action, and a joint that runs hot mid chunk still ends it on the reading that
    finds it hot, the arm held either way."""
    arm = _segment_arm()
    answer = _long(_swing(arm))

    def script(observation: Observation, sent: Mapping[str, float]) -> list[dict[str, float]]:
        if runner.requests > 1:
            if ending == "starved":
                return []
            arm.temperature["elbow_flex"] = HOT_C + 5
        return answer(observation, sent)

    runner = ScriptedRunner(script, rate_hz=RATE, latency_ticks=HALF)
    clock = LockstepClock()
    _, transport, adapter, ex = await _backend(runner, clock=clock, arm=arm)
    sends = _recorded(arm, clock)
    ran = await ex.run_verb("manipulate", {"instruction": "wave"})
    assert not ran.ok and ran.data["ended"] == ending, ran.summary
    assert _whole_hold(arm.actions[-1]) and not transport.policy_running
    played = [at for at, _, action in sends if not _whole_hold(action)]
    if ending == "starved":
        # the first chunk's last action, then `STARVE_S` with nothing, then the hold
        assert len(played) == LONG - HALF, len(played)
        assert sends[-1][0] - played[-1] == pytest.approx(STARVE_S, abs=2 * PERIOD)
    else:
        assert "elbow_flex reads" in ran.summary, ran.summary
        # asked again half a chunk into the first, and ended before the first ran out
        assert 0 < len(played) < LONG - HALF, len(played)
    await adapter.close()


async def test_manipulate_is_not_ok_on_a_starved_policy_a_guard_an_error_or_a_stop() -> None:
    arm = _segment_arm()
    empty = ScriptedRunner(lambda o, s: [], rate_hz=RATE)
    _, _, adapter, ex = await _backend(empty, arm=arm)
    starved = await ex.run_verb("manipulate", {"instruction": "wave"})
    assert not starved.ok and starved.data["ended"] == "starved", starved.summary
    assert starved.summary.startswith("manipulate 'wave' was stopped: the policy gave no action")
    assert _whole_hold(arm.actions[-1])
    await adapter.close()

    arm = _segment_arm()
    swing = _swing(arm)

    def warms(observation: Observation, sent: Mapping[str, float]) -> Any:
        if observation.tick == 3:
            arm.temperature["elbow_flex"] = HOT_C + 5
        return swing(observation, sent)

    _, _, adapter, ex = await _backend(ScriptedRunner(warms, rate_hz=RATE), arm=arm)
    hot = await ex.run_verb("manipulate", {"instruction": "wave"})
    assert not hot.ok and hot.data["ended"] == "guard", hot.summary
    assert "elbow_flex reads" in hot.summary
    await adapter.close()

    def broken(observation: Observation, sent: Mapping[str, float]) -> Any:
        raise RuntimeError("no accelerated backend")

    _, _, adapter, ex = await _backend(ScriptedRunner(broken, rate_hz=RATE))
    failed = await ex.run_verb("manipulate", {"instruction": "wave"})
    assert not failed.ok and failed.data["ended"] == "error", failed.summary
    assert "no accelerated backend" in failed.data["error"]
    await adapter.close()

    arm = _segment_arm()
    runner = ScriptedRunner(_swing(arm), rate_hz=RATE)
    _, _, adapter, ex = await _backend(runner, arm=arm)
    running = asyncio.create_task(ex.run_verb("manipulate", {"instruction": "wave"}))
    await _until(lambda: runner.requests >= 3)
    assert (await ex.run_verb("stop")).ok
    stopped = await running
    assert not stopped.ok and stopped.data["ended"] == "stopped", stopped.summary
    assert stopped.summary == "manipulate 'wave' stopped: a stop was sent to the arm"
    await adapter.close()


@pytest.mark.parametrize("lost", ["one hold", "every hold"])
async def test_a_stall_whose_hold_did_not_reach_the_arm_is_held_again_or_is_not_ok(
    lost: str,
) -> None:
    """A Feetech bus loses the odd status packet, and the stall's own hold can be the one lost.
    The verb holds the arm again, as it does after every ending that held, so one lost packet
    still leaves the arm held where something stopped it. An arm the holds do not reach is not
    ok, and the pilot is told it may still be pushing toward the policy's last goal."""
    arm = _segment_arm()
    in_the_way = _inside(arm, "shoulder_pan", 0.1)
    goal = _inside(arm, "shoulder_pan", 0.4)
    assert arm.positions["shoulder_pan"] < in_the_way < goal
    arm.obstacles["shoulder_pan"] = (-math.inf, in_the_way)
    runner = ScriptedRunner(lambda o, s: [{"shoulder_pan": goal}], rate_hz=RATE)
    _, transport, adapter, ex = await _backend(runner, arm=arm)
    send = arm.send_action
    failed: list[dict[str, float]] = []

    def loses(action: dict[str, float]) -> dict[str, float]:
        if _whole_hold(action) and (lost == "every hold" or not failed):
            failed.append(dict(action))
            raise ConnectionError("Failed to sync write 'Goal_Position'")
        return send(action)

    arm.send_action = loses  # type: ignore[method-assign]
    try:
        ran = await ex.run_verb("manipulate", {"instruction": "push it along"})
    finally:
        arm.send_action = send  # type: ignore[method-assign]
    assert ran.data["ended"] == "stall" and failed, ran.summary
    if lost == "one hold":
        assert ran.ok, ran.summary
        last = arm.actions[-1]
        assert _whole_hold(last), last
        assert last["shoulder_pan.pos"] == pytest.approx(arm.positions["shoulder_pan"])
        assert transport.stop_error is None
    else:
        assert not ran.ok, ran.summary
        assert "the last hold sent to the arm did not reach it" in ran.summary, ran.summary
        assert "send stop" in ran.summary
        assert not any(_whole_hold(action) for action in arm.actions), arm.actions
    await adapter.close()


async def _manipulate_meanwhile(body: str, meanwhile: str) -> tuple[bool, bool, bool, Any, str]:
    """Run `manipulate` on the mock or the arm, and `meanwhile` while its segment runs: how the
    other verb went, whether it was refused for the segment, and how `manipulate` ended."""
    running: asyncio.Task[Any]
    go = asyncio.Event()
    if body == "mock":
        mock = LeRobotMock()
        adapter = LeRobotAdapter(mock)
        manifest = await adapter.connect()
        ex = Executor(registry_from_manifest(manifest, adapter), adapter, confirm=allow_all)
        paused = asyncio.Event()
        sleep = mock.sleep

        async def gated(seconds: float) -> None:
            # the verb's first look at its running segment waits for the other verb
            if mock.policy_running and not paused.is_set():
                paused.set()
                await go.wait()
            await sleep(seconds)

        mock.sleep = gated  # type: ignore[method-assign]
        running = asyncio.create_task(ex.run_verb("manipulate", {"instruction": "wave"}))
        await asyncio.wait_for(paused.wait(), 5.0)
        wrist = mock.joints["wrist_roll"]
    else:
        arm = _segment_arm()
        runner = ScriptedRunner(_swing(arm), rate_hz=RATE)
        _, _, adapter, ex = await _backend(runner, arm=arm)
        running = asyncio.create_task(ex.run_verb("manipulate", {"instruction": "wave"}))
        await _until(lambda: runner.requests >= 3)
        wrist = arm.positions["wrist_roll"]
    if meanwhile == "stop":
        other = await ex.run_verb("stop")
    elif meanwhile == "move_joints":
        other = await ex.run_verb("move_joints", {"positions": {"wrist_roll": wrist}})
    else:
        other = await ex.run_verb("gripper", {"open": False})
    go.set()
    ran = await asyncio.wait_for(running, 10.0)
    await adapter.close()
    refused = "manipulate is running: stop first" in other.summary
    return other.ok, refused, ran.ok, ran.data.get("ended"), ran.summary


@pytest.mark.parametrize("meanwhile", ["stop", "move_joints", "gripper"])
async def test_the_mock_s_manipulate_gives_way_and_refuses_where_the_arm_s_does(
    meanwhile: str,
) -> None:
    """A rehearsal on the mock has to end where the arm would. While a segment runs, a stop
    fails `manipulate` as stopped and names the stop, and a verb that sends a goal is refused
    in the arm's own words, on the mock as on the arm."""
    outcomes = [await _manipulate_meanwhile(body, meanwhile) for body in ("mock", "arm")]
    on_the_mock, on_the_arm = outcomes
    assert on_the_mock == on_the_arm
    stopped_by_the_stop = on_the_arm == (
        True,
        False,
        False,
        "stopped",
        "manipulate 'wave' stopped: a stop was sent to the arm",
    )
    refused = on_the_arm[:2] == (False, True) and not on_the_arm[2]
    assert stopped_by_the_stop if meanwhile == "stop" else refused, on_the_arm


async def test_manipulate_needs_an_instruction_and_a_verb_is_refused_while_it_runs() -> None:
    arm = _segment_arm()
    runner = ScriptedRunner(_swing(arm), rate_hz=RATE)
    _, transport, adapter, ex = await _backend(runner, arm=arm)
    blank = await transport.send_intent(Intent.do("policy:manipulate:  "))
    assert not blank.accepted and "needs an instruction" in (blank.reason or "")
    unknown = await transport.send_intent(Intent.do("policy:dance:now"))
    assert not unknown.accepted and "unknown skill" in (unknown.reason or "")
    running = asyncio.create_task(ex.run_verb("manipulate", {"instruction": "wave"}))
    await _until(lambda: runner.requests >= 3)
    refused = await transport.send_intent(Intent.gripper(True))
    assert not refused.accepted and refused.reason == "manipulate is running: stop first"
    await adapter.stop()
    assert not (await running).ok
    await adapter.close()


async def test_a_policy_object_still_runs_one_act_a_tick_at_the_verbs_rate() -> None:
    """A `PolicyLike` handed to the backend is wrapped, and runs as `pick` always ran it: one
    `act` a tick at `POLICY_HZ`, its goal sent that tick, under the verbs' own cap."""

    class Counts:
        def __init__(self) -> None:
            self.tasks: list[str] = []

        def act(self, observation: dict[str, Any], *, task: str) -> dict[str, float] | None:
            self.tasks.append(task)
            return {"shoulder_pan": float(len(self.tasks) % 3)}

    policy = Counts()
    clock = SteppedClock()
    arm, transport, adapter, _ = await _backend(policy, clock=clock)
    assert isinstance(transport._policy_loop, PolicyLoop)
    sends = _recorded(arm, clock)
    start = clock.t
    ended = await _segment_end(transport, "pick", max_s=6 / POLICY_HZ, told="cup")
    assert ended.how == "time" and policy.tasks == ["cup"] * 6
    assert [at - start for at, _, _ in sends] == pytest.approx([k / POLICY_HZ for k in range(6)])
    assert all(cap == float(STEP) for _, cap, _ in sends)
    await adapter.close()
