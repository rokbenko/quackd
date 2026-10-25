"""The policy loop: one segment of a learned policy driving the arm, paced on the arm's clock.

A segment is what `pick` and `manipulate` hand the arm to. It runs as the backend's policy task
(`LeRobotReal._run_policy`, which takes the segment's first reading, refuses what it must and
sets the step cap), and this loop is its body: a tick at a time, it reads the arm, judges the
reading, takes what the runner has answered, asks it for more, and sends one goal.

**The rate** is the runner's, from the source it names (`Features`), and a rate that is not a
finite number inside `MIN_RATE_HZ`..`MAX_RATE_HZ` refuses the segment. quackd never measures
one: a rate is a fact about the data a policy learned from, and the loop's own pace is reported,
never used to choose it.

**The pace** is the clock's. Tick `k` is due at `start + k * period`, from the integer `k` and
never from a sum of sleeps, so a tick that ran long costs its own tick and not every one after
it. A tick that overruns the next deadline skips to the next whole period after the clock's now,
counts the ticks it missed, and never sends twice in one period. A tick never starts before its
deadline either: the simulator's clock wakes on its own steps, the nearest to what was asked,
and a period that is not a whole number of steps would otherwise wake a tick in the period
before its own (`PolicyLoop._pace`).

**The speed cap** is the verbs' own, `max_step_deg / TICK_S` degrees a second, so the one
setting (`QUACKD_LEROBOT_MAX_STEP_DEG`) governs both. Per send it is that speed over the pacer's
rate, clamped into `(0, max_step_deg]` (`speed_cap`), and the backend writes it on the follower's
config for the length of the segment.

**Chunks.** A request is stamped with the tick it was read at, and the chunk that answers it
holds one action per tick from that tick. When it arrives, the actions for ticks already played
are dropped and the rest replaces the queue's tail, never appended to it, because the newer
chunk saw a newer arm. At most one request is outstanding, and the next is asked for once what
is queued is down to half the last chunk or twice the ticks an answer takes to land, whichever
is more (`refill_due`), so any answer within half a chunk lands with something still queued.
What a chunk can carry is set by the one request out: the next goes out no sooner than the last
chunk lands, so a chunk has to hold twice the latency, and what it falls short by is starved
every chunk (`starved_each_chunk`), whenever it is asked for, which is why `quackd policy
serve` refuses such a latency. A tick with nothing queued
sends nothing (the arm holds its last goal), counts as starved, and `STARVE_S` of that ends the
segment, with `FIRST_CHUNK_S` of grace for a segment's first chunk. Each segment has an epoch,
and a chunk from an earlier one is thrown away.

**The runner's thread.** Every call on the runner goes to a single worker of its own, never to
the default pool the bus's calls run in (`LeRobotReal._call`), so a runner that never answers
cannot hold up a read, a hold or a heartbeat. A segment's reset waits for the one before it,
behind whatever inference is still on that worker, for no longer than `RESET_S`.

**Lockstep.** On the simulator (a clock whose `lockstep` is true) time stands still while
nothing sleeps on it, so a runner's inference is awaited in the loop's own turn and costs no sim
time. Its answer is then held back `k` ticks, `k` from the runner's declared latency, while the
pacer goes on ticking and playing the old queue, and is judged only when it is let go: that is
where it would have landed on the arm, a chunk thrown away and an error raised included.
A runner that answers at once (`k` of 0) is held back a tick where something is still queued,
since on the arm its answer is left to land then and the next tick's look is the soonest that
takes it in. With nothing left to play it is awaited in its own tick on either clock, which is
how a `PolicyLike` has always run, one `act` a tick and its goal sent that tick, and how a
segment's first chunk is waited for. On the wall's clock a request made with something still
queued is never waited for, whatever latency the runner declares: a server started with none
still takes a round trip to answer, and a tick that waited it out would hold the arm still
through it, every chunk.

**Chunks one at a time** (`async_chunks` False, `--no-async-chunks`), as LeRobot's own loop
plays them: a chunk is played out before the next is asked for, the arm holds its last goal
while that one is on its way, and it is played from its first action at the tick it lands,
since the arm has not moved on from where it was asked. Nothing is waited for in a tick, so
every tick of the wait still reads and judges the arm, a stall is not counted while nothing is
sent, and the wait is starved out with the patience a first chunk gets, since the arm waits for
every chunk by design. The simulator holds each back its declared latency, or a tick for a
runner that declares none, the soonest the arm takes one in. A runner that answers one action
at a time is asked as it always was, and a runner in tick mode plays no chunks, either way.

**A wait for a chunk that outlasts its tick** sends nothing in it: the arm held still through
the wait, and the tick that is due when it ends reads the arm and judges it before anything
more is sent, as a first chunk slower than a tick would otherwise have been played on a reading
taken before it. A runner that answers one action at a time has its late answer sent in the
tick it was asked for, since it is the only goal there is for it.

**Tick mode** (`Features.per_tick`): a runner that carries state from tick to tick is asked
every tick, and a tick it never saw, skipped by the pacer or not answered by the next, ends the
segment with the arm held and resets the runner. Its delay line is a tick deep at most: it is
asked again only once it has answered, so one that declares more than a tick to answer could
never keep up on the arm, and is refused on either clock rather than rehearsed on the
simulator's.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import contextlib
import dataclasses
import math
import numbers
import time
from collections import deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, TypeVar

from quackd_lerobot.policy.runner import Chunk, Features, Observation, PolicyRunner
from quackd_lerobot.verbs import (
    JOINTS,
    PICK_SETTLE_S,
    STALL_DEG,
    TICK_S,
    SegmentEnd,
    SegmentHow,
    SegmentStats,
)

if TYPE_CHECKING:
    from quackd_lerobot.real import LeRobotReal

T = TypeVar("T")

REGISTER_PERIOD_S = 0.5
"""How often a policy segment reads torque and temperature, in the transport's time. Every tick
reads the joints, which is what the policy and `holding` need; the two registers are two more
bus transactions each, and neither changes in a tenth of a second. A servo heats over seconds
and one that trips its overload drops torque for good, so twice a second catches both in time
to hold the arm, and the heartbeat's own probe was reading them at that rate anyway."""
FAILED_SENDS = 3
"""How many sends in a row may fail before a policy segment ends. A Feetech bus loses the odd
status packet, which is no reason to take the arm off a policy mid grasp; three in a row is a
bus or an arm that has stopped taking goals, and the segment ends there, holding the arm."""
CLIP_SUSTAIN_S = 1.0
"""How long a joint's goal may stay clipped to its travel before a policy segment ends, in the
transport's time. A policy goal past the travel is clipped and counted rather than refused
(ADR-0036), because one a tick over should not abort a grasp. A second of them in a row is a
policy pushing the arm somewhere it cannot go, and the segment ends on it."""

MIN_RATE_HZ = 1.0
MAX_RATE_HZ = 60.0
"""The rates quackd paces a policy at. quackd's own bounds, and no measurement: a rate outside
them is more likely a wrong number than a policy, a period given where a frequency was meant,
and above the ceiling a tick is shorter than a read and a send of the arm's bus are likely to
take together, and than a few steps of the simulator's clock. Below the floor every send is a
step the speed cap clamps anyway."""
STARVE_S = 1.0
"""How long, in the transport's time, a segment may have nothing to send before it ends. The
arm holds its last goal meanwhile, which is safe and is also a policy that has stopped driving,
and a second of it is a runner that has fallen behind the arm rather than one that is slow."""
FIRST_CHUNK_S = 5.0
"""The grace a segment's first chunk gets, in place of `STARVE_S`: the first inference after a
reset is the slow one, with caches and buffers built on it, and nothing has been sent yet, so
the arm is exactly where the segment found it while it waits."""
RESET_S = 5.0
"""How long, on the wall's clock, a segment's start waits for the runner's reset, which queues
behind any inference the last segment left on the runner's worker. A runner still busy after
that is not handed the arm, and the segment is refused rather than started on a stale policy."""
STALL_S = 1.0
"""How long every joint of the arm may read within `STALL_DEG` of where it was while the policy
is sending, before `manipulate`'s segment ends on it, with the arm held: an arm that has stopped
moving under a policy that is still sending is a policy that has done what it will do, or one
pressing the arm on something it cannot move, whose last goal must not be left pushing a servo.
Only ticks that sent count, so a policy starved of chunks is starving and not stalled."""
REFILL_LATENCIES = 2
"""How many of a chunked runner's declared latencies before its queue runs dry it is asked for
its next chunk, where that is sooner than half the chunk (`refill_at`): one for the answer to
land in, and one more to spare. The latency a bench suggests is the 95th percentile of the steps
it timed, so one step in twenty takes longer, and the arm would hold still through the ticks
such a step ran over. Half a chunk already gives any step up to half a chunk slow the time to
land, and asking at twice a latency longer than a quarter chunk gives a step that runs past it
what room the chunk has: up to twice the latency where a chunk holds three of them, and
otherwise all that is left of the last chunk as it lands. The cost is a stretch of each chunk
replaced rather than played, so a policy that answers fast still plays half of each chunk
before it asks."""
WORKER = "quackd-policy"
"""The name of the runner's own worker thread, which is never one of the bus's."""
EXACT = 1e-9
"""How near a whole number of ticks a declared latency has to be to be that number, and how near
its deadline a clock has to read for a tick to be due, allowing for the float arithmetic of a
sum and for nothing else."""


@dataclass(frozen=True)
class Segment:
    """What one `do` asked for: `verb` is `pick` or `manipulate`, `instruction` what the runner
    is told, `max_s` the segment's time on the transport's clock and `max_chunks` how many chunks
    it may play, each None for no limit."""

    verb: str
    instruction: str
    max_s: float | None = None
    max_chunks: int | None = None

    @property
    def ends_on_holding(self) -> bool:
        """`pick` is done when something is held, judged on the loop's own reads each tick."""
        return self.verb == "pick"

    @property
    def ends_on_stall(self) -> bool:
        """`manipulate` is done when the arm stops moving under it (`STALL_S`)."""
        return self.verb == "manipulate"


@dataclass(frozen=True)
class Plan:
    """What a segment's start settled: the runner's features, the pacer's period, the step cap
    per send and the ticks a chunk is held back on a lockstep clock."""

    features: Features
    period_s: float
    cap_deg: float
    latency_ticks: int


def speed_cap(max_step_deg: float, rate_hz: float) -> float:
    """The step cap per send for a policy at `rate_hz`: the verbs' speed, `max_step_deg` per
    `TICK_S`, over the pacer's rate, clamped into `(0, max_step_deg]`. A policy at the verbs' own
    rate gets the verbs' own cap, a faster one a smaller step and the same speed, and a slower one
    never more than one verb step. A rate that gives no positive finite step gives none at all."""
    per_send = max_step_deg / TICK_S / rate_hz if rate_hz else math.nan
    if not (math.isfinite(per_send) and per_send > 0):
        raise ValueError(f"no step cap for {max_step_deg!r} degrees at {rate_hz!r} Hz")
    return min(float(max_step_deg), per_send)


def _finite(value: object) -> bool:
    """A finite real number, whatever library made it (a numpy float is one), and never a bool,
    which Python counts as a number and no runner means as one."""
    return (
        isinstance(value, numbers.Real)
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )


def rate_refusal(features: Features) -> str | None:
    """Why a runner's declared rate cannot pace a segment, or None."""
    rate = features.rate_hz
    if _finite(rate) and MIN_RATE_HZ <= float(rate) <= MAX_RATE_HZ:
        return None
    return (
        f"the policy was not started: it declares {rate!r} Hz ({features.rate_source}), and "
        f"quackd paces a policy at {MIN_RATE_HZ:g} to {MAX_RATE_HZ:g} Hz. Give it the rate of "
        "the data it was trained on"
    )


def latency_ticks(latency_s: float, rate_hz: float) -> int:
    """How many ticks a chunk is held back on a lockstep clock: the declared latency in ticks,
    rounded up, since a chunk cannot be played before it would have arrived."""
    return max(0, math.ceil(latency_s * rate_hz - EXACT))


def longest_latency(chunk: int) -> int:
    """The most ticks every answer of a chunked runner whose chunks hold `chunk` actions can
    take to land, and the arm still never be left with nothing to play after the first: half
    the chunk, rounded down, the most `starved_each_chunk` counts none for. `quackd policy
    serve` refuses a latency longer."""
    return chunk // 2


def refill_at(latency: int, chunk: int) -> int:
    """How few actions of its last chunk, of `chunk`, are left to play when a chunked runner
    whose answers land `latency` ticks after it is asked (`latency_ticks`) is asked for the
    next: half the chunk (`longest_latency`), so any answer up to that slow lands before the
    last of them is played, whatever latency was declared, or `REFILL_LATENCIES` of the
    latency, where that is more, to leave a slow step more room than that."""
    return max(REFILL_LATENCIES * latency, longest_latency(chunk))


def refill_due(left: int, latency: int, chunk: int) -> bool:
    """Whether a chunked runner is asked for its next chunk, with `left` actions of the last,
    which held `chunk`, still to play from this tick on, and `latency` ticks for an answer to
    land: once they are down to `refill_at`. An empty queue always asks, a segment's first
    request among them."""
    return left <= refill_at(latency, chunk)


def starved_each_chunk(latency: int, chunk: int) -> int:
    """How many ticks of every chunk after a segment's first have nothing to play, for a
    chunked runner whose chunks hold `chunk` actions and land `latency` ticks after they are
    asked for: none while a chunk holds twice the latency, and otherwise what it falls short.

    At most one request is out at a time, so the next can go no sooner than the tick the last
    chunk lands, `latency` ticks into it, and lands `latency` ticks after that, while all the
    arm has to play meanwhile is the `chunk - latency` actions left of the last one. No
    threshold for `refill_at` can help: the next is asked for the tick the last lands, and
    cannot be asked sooner. So a latency this gives a number above zero for starves the arm
    every chunk, on the simulator's delay line as on the arm, and `quackd policy serve` refuses
    to declare one."""
    return max(0, 2 * latency - chunk)


def clipped_too_long(
    goals: Mapping[str, float],
    since: dict[str, float],
    now: float,
    travel: Mapping[str, tuple[float, float]],
) -> str | None:
    """Keep `since`, when each joint's goal first went past its travel in the run of ticks it
    has stayed there, and say which has stayed there `CLIP_SUSTAIN_S`. A tick whose action
    leaves a joint out keeps its run going."""
    for joint, goal in goals.items():
        span = travel.get(joint)
        if span is not None and not span[0] <= goal <= span[1]:
            since.setdefault(joint, now)
        else:
            since.pop(joint, None)
    stuck = sorted(joint for joint, at in since.items() if now - at >= CLIP_SUSTAIN_S)
    if not stuck:
        return None
    return (
        f"the policy's goal for {', '.join(stuck)} stayed past the travel for "
        f"{CLIP_SUSTAIN_S:g} s, clipped to its end on every tick, so it was driving the arm "
        "somewhere it cannot go"
    )


@dataclass
class _Request:
    """One request to the runner: the segment's epoch, the tick it was read at, and the call on
    the runner's worker."""

    epoch: int
    tick: int
    future: concurrent.futures.Future[Chunk]


@dataclass
class _Run:
    """One segment's working state, kept apart from the loop, which outlives it."""

    started: float
    registers_at: float
    clips_at: int
    queue: deque[tuple[int, Mapping[str, Any]]] = field(default_factory=deque)
    held_back: deque[tuple[int, _Request]] = field(default_factory=deque)
    """Requests a lockstep clock has had answered and holds back until their tick, oldest
    first. An answer is judged only when it is let go, so one thrown away and an error raised
    land where the arm would first have met them."""
    last_len: int = 0
    got_first: bool = False
    done: bool = False
    done_at: float | None = None
    starved_since: float | None = None
    still: tuple[float, dict[str, float]] | None = None
    last_sent: dict[str, float] = field(default_factory=dict)
    clipped_since: dict[str, float] = field(default_factory=dict)
    failed_sends: int = 0
    ticks: int = 0
    skipped: int = 0
    chunks: int = 0
    starved: int = 0
    stale: int = 0
    late: int = 0
    waits_each: bool = False
    """Chunks go one at a time (`PolicyLoop.async_chunks` off, for a runner not in tick
    mode)."""

    def left(self, tick: int) -> int:
        """How many queued actions are for `tick` or a tick after it: what the arm still has to
        play from this tick on."""
        return sum(1 for at, _ in self.queue if at >= tick)

    def one_at_a_time(self) -> bool:
        """Whether the next chunk waits for the last to be played out, and the arm holds still
        while it is on its way (`waits_each`). A runner that answered one action at a time is
        asked as it always was, every tick and awaited in it."""
        return self.waits_each and self.last_len != 1


@dataclass
class Tally:
    """What every segment a loop ran counted, added up for a run's record: the segments, the
    seconds they ran on the transport's clock, their ticks, the ticks the pacer skipped for
    running late, the chunks taken in, the ticks with nothing to send, and the goals clipped
    into the travel. A segment stopped from outside counts what it had counted when it was
    stopped. A segment refused before it started is not one. A report, and never a rate
    anything is set from, as `SegmentStats` is.

    The segments' seconds are kept on two clocks, because on the simulator they are not the
    same: `seconds`, and the rate the ticks were achieved at, on the transport's clock, which
    is what the ticks were paced on, and `wall_s` on the wall's, which is the share of a run's
    wall time the segments had. A lockstep clock's seconds are the simulator's own and run as
    fast as it steps, so a record of them says so (`clock`), as a verb's does."""

    segments: int = 0
    seconds: float = 0.0
    wall_s: float = 0.0
    ticks: int = 0
    late: int = 0
    chunks: int = 0
    starved: int = 0
    clips: int = 0
    lockstep: bool = False

    def add(self, run: _Run, seconds: float, clips: int, *, wall_s: float, lockstep: bool) -> None:
        self.segments += 1
        self.seconds += max(0.0, seconds)
        self.wall_s += max(0.0, wall_s)
        self.ticks += run.ticks
        self.late += run.skipped
        self.chunks += run.chunks
        self.starved += run.starved
        self.clips += clips
        self.lockstep = self.lockstep or lockstep

    def summary(self) -> dict[str, Any]:
        """The counts as the run's record keeps them, and the rate the ticks were achieved at
        across every segment, or None before any tick. `clock` is there only for a lockstep
        clock, and says `sim`, the word `quackd.safety` gives a simulator's clock: on the wall's
        clock `seconds` and `wall_s` measure the same time and there is nothing to name."""
        return {
            "segments": self.segments,
            "seconds": round(self.seconds, 2),
            **({"clock": "sim"} if self.lockstep else {}),
            "wall_s": round(self.wall_s, 2),
            "ticks": self.ticks,
            "late_ticks": self.late,
            "chunks": self.chunks,
            "starved_ticks": self.starved,
            "clips": self.clips,
            "hz": round(self.ticks / self.seconds, 1) if self.seconds > 0 and self.ticks else None,
        }


class PolicyLoop:
    """The loop one runner's segments run in, kept by the backend for as long as it has that
    runner, so that its epoch, its worker and a request an earlier segment left in flight carry
    from one segment to the next.

    `starve_s`, `first_chunk_s` and `reset_s` are `STARVE_S`, `FIRST_CHUNK_S` and `RESET_S`,
    as attributes a test can shorten. `tally` adds up every segment it has run (`record`).
    `async_chunks` is whether a chunk is asked for while the arm plays the last
    (`--async-chunks`, the default) or each is played out before the next is asked for, the arm
    holding still between (`--no-async-chunks`), and the record says which."""

    def __init__(self, runner: PolicyRunner, *, async_chunks: bool = True) -> None:
        self.runner = runner
        self.async_chunks = async_chunks
        self.tally = Tally()
        self.epoch = 0
        """The segment the loop is on, counted from 1. A chunk asked for under another is
        thrown away when it arrives."""
        self.starve_s = STARVE_S
        self.first_chunk_s = FIRST_CHUNK_S
        self.reset_s = RESET_S
        self.instruction = ""
        self._executor: concurrent.futures.ThreadPoolExecutor | None = None
        self._inflight: _Request | None = None
        self.declared_s = 0.0
        """The latency the runner declared at the last segment's start, in seconds."""
        self.waited = 0
        self.thinking_s = 0.0
        self.slowest_s = 0.0
        """How many requests the last segment waited for with its lockstep clock standing
        still, how long on the wall's clock in all, and the longest of them, a request still
        out when the segment ended included. All 0 on any other clock, where nothing waits for
        one. A simulator's timeout for `manipulate` covers only the thinking the runner
        declares, and these tell a runner that outran it from one that did not
        (`LeRobotSim.slow_policy`)."""

    # ── the runner's own thread ─────────────────────────────────────────────────────────

    def _submit(self, fn: Callable[..., T], *args: Any) -> concurrent.futures.Future[T]:
        """Run `fn` on the runner's single worker, made the first time it is needed."""
        if self._executor is None:
            self._executor = concurrent.futures.ThreadPoolExecutor(
                max_workers=1, thread_name_prefix=WORKER
            )
        return self._executor.submit(fn, *args)

    @staticmethod
    async def _answered(future: concurrent.futures.Future[Any], within: float | None) -> bool:
        """Wait for `future`, for at most `within` seconds of the event loop's clock or for as
        long as it takes. Whatever ends the wait, a timeout or a cancellation of the caller,
        lets go of it here: a call still queued is cancelled, one already running goes on in
        its thread, and nothing waits on it any more. A runner's error is read off `future`
        where its answer is judged, and this copy of it is marked read, or asyncio would log it
        as never retrieved when it is collected."""
        wrapped = asyncio.wrap_future(future)
        try:
            done, _ = await asyncio.wait({wrapped}, timeout=within)
        finally:
            if not wrapped.done():
                wrapped.cancel()
            elif not wrapped.cancelled():
                wrapped.exception()
        return bool(done)

    async def _ask(self, fn: Callable[..., T], *args: Any) -> T | str:
        """A call on the runner at a segment's start, within `reset_s`, or why it failed."""
        future = self._submit(fn, *args)
        name = getattr(fn, "__name__", "call")
        if not await self._answered(future, self.reset_s):
            return (
                f"the policy was not started: its {name} had not come back after "
                f"{self.reset_s:g} s, behind whatever the last segment left it thinking about, "
                "so quackd will not hand it the arm"
            )
        if future.cancelled():
            return f"the policy was not started: its {name} was cancelled"
        if (error := future.exception()) is not None:
            return f"the policy was not started: its {name} raised {type(error).__name__}: {error}"
        return future.result()

    async def call(self, fn: Callable[..., T], *args: Any, within: float | None = None) -> T:
        """`fn(*args)` on the runner's own worker, behind anything it is doing, for a caller
        outside a segment: the connect asking whether the policy fits the arm. It raises what
        `fn` raised, and a TimeoutError once `within` seconds, `reset_s` unless it is given,
        have passed without an answer."""
        wait = self.reset_s if within is None else within
        future = self._submit(fn, *args)
        if not await self._answered(future, wait):
            name = getattr(fn, "__name__", "call")
            raise TimeoutError(f"the policy's {name} had not come back after {wait:g} s")
        return future.result()

    def close(self) -> None:
        """Close the runner on its own worker, after whatever it is doing, without waiting for
        it. The worker is kept, so a segment after a reconnect still queues behind anything the
        runner was left doing and never runs beside it."""
        if self._executor is not None:
            self._executor.submit(self.runner.close)

    def served(self) -> dict[str, Any]:
        """What the runner says it is, as a run's record names it: for a policy served by
        another process, where the server is, what it serves and every repository it loaded
        (`RemoteRunner.record`), as last asked, with nothing asked now, and whether its chunks
        are asked for while the arm plays (`async_chunks`). Empty for a runner that says
        nothing about itself, an in-process one."""
        said = getattr(self.runner, "record", None)
        return {**said(), "async_chunks": self.async_chunks} if callable(said) else {}

    def record(self) -> dict[str, Any]:
        """The policy block of a run's record: what the runner is (`served`), what every
        segment counted (`tally`), and the round trips a runner that crosses a wire timed
        (`RemoteRunner.round_trips`). Numbers and names only, never an action: a chunk is the
        runner's to play and nobody's to keep."""
        record = self.served()
        record.update(self.tally.summary())
        timed = getattr(self.runner, "round_trips", None)
        if callable(timed) and (trips := timed()) is not None:
            record["round_trip_ms"] = trips
        return record

    # ── a segment ───────────────────────────────────────────────────────────────────────

    async def start(self, instruction: str, max_step_deg: float) -> Plan | str:
        """Open the next epoch and reset the runner for it, then settle the rate, the cap and the
        latency, or say why the segment must not start. The reset queues behind any inference
        the last segment left on the worker, which is how its answer is kept out of this one."""
        self.epoch += 1
        self.instruction = instruction
        self.declared_s = self.thinking_s = self.slowest_s = 0.0
        self.waited = 0
        reset = await self._ask(self.runner.reset, instruction)
        if isinstance(reset, str):
            return reset
        features = await self._ask(self.runner.features)
        if isinstance(features, str):
            return features
        if not isinstance(features, Features):
            return (
                f"the policy was not started: its features gave {type(features).__name__} "
                "rather than the Features that say its rate, so quackd has no rate to pace it "
                "at. Fix the runner to return one"
            )
        if (refusal := rate_refusal(features)) is not None:
            return refusal
        rate = float(features.rate_hz)
        latency = await self._ask(self.runner.latency_s)
        if isinstance(latency, str):
            return latency
        if not (_finite(latency) and latency >= 0 and math.isfinite(float(latency) * rate)):
            return (
                f"the policy was not started: it declares a latency of {latency!r} s, and a "
                "latency is a number of seconds, 0 or more, that comes to a number of ticks"
            )
        self.declared_s = float(latency)
        ticks = latency_ticks(float(latency), rate)
        if features.per_tick and ticks > 1:
            return (
                f"the policy was not started: it has to be asked every tick, and it declares "
                f"{float(latency):g} s to answer, which is {ticks} ticks at {rate:g} Hz. quackd "
                "asks a policy again only once it has answered, so one asked every tick has to "
                "answer within a tick: run it where it answers faster"
            )
        return Plan(
            features=features,
            period_s=1.0 / rate,
            cap_deg=speed_cap(max_step_deg, rate),
            latency_ticks=ticks,
        )

    async def run(
        self, arm: LeRobotReal, plan: Plan, segment: Segment, started: float, registers_at: float
    ) -> SegmentEnd:
        """The segment's ticks, from `started` on the arm's clock, until one of them ends it.

        Each tick reads the arm (the registers every `REGISTER_PERIOD_S`) and judges that
        reading before it sends anything: `holding` ends a `pick`, a hot joint, torque off or a
        read the arm did not answer ends any segment with the arm held. Then it takes in what
        the runner has answered, asks it again where the queue is running low, and plays this
        tick's action, which `LeRobotReal._policy_goals` and `clipped_too_long` judge before it
        is sent. What ends it says so in a `SegmentEnd`, with the segment's counts.

        The runner's own error, raised where its answer is judged, ends it `error` with the
        counts up to there, and with no hold: the verb waiting on it stops the arm."""
        run = _Run(started, registers_at, arm._range_clips)
        run.waits_each = not self.async_chunks and not plan.features.per_tick
        # the wall's clock beside the arm's, which on the simulator is the simulator's (`Tally`)
        walled = time.perf_counter()
        try:
            return await self._ticks(arm, plan, segment, run)
        except Exception as e:
            arm._policy_error = f"{type(e).__name__}: {e}"
            return self._end(arm, run, "error", f"the policy raised {arm._policy_error}")
        finally:
            # however it ended, a stop from outside included, which raises through here, and a
            # count that could not be taken never takes the segment's own ending with it
            with contextlib.suppress(Exception):
                self.tally.add(
                    run,
                    arm.now() - run.started,
                    arm._range_clips - run.clips_at,
                    wall_s=time.perf_counter() - walled,
                    lockstep=bool(getattr(arm.clock, "lockstep", False)),
                )

    async def _ticks(self, arm: LeRobotReal, plan: Plan, segment: Segment, run: _Run) -> SegmentEnd:
        """The ticks themselves, counted on the segment's `_Run`, which `run` still has to
        report from when one of them raises."""
        started = run.started
        lockstep = bool(getattr(arm.clock, "lockstep", False))
        tick = 0
        while not arm._closed:
            ticked = time.perf_counter()
            now = arm.now()
            # within `EXACT`, as a tick is due within it (`_pace`): the tick a segment's time
            # runs out on is due at its end, and a clock that reads a hair short of that there,
            # as the difference of two floats can, would play one tick past its time
            if segment.max_s is not None and now - started >= segment.max_s - EXACT:
                return self._end(arm, run, "time", f"its {segment.max_s:g} s ran out")
            registers = now - run.registers_at >= REGISTER_PERIOD_S
            try:
                obs = await arm._loop_read(registers=registers)
            except Exception as e:
                said = arm.stop_error or f"{type(e).__name__}: {e}"
                return await self._held(arm, run, f"the arm did not answer a read ({said})")
            if registers:
                run.registers_at = now
            if segment.ends_on_holding and arm._holding():
                return self._end(arm, run, "holding", "the gripper closed and settled on something")
            if (why := arm._unsafe_to_drive()) is not None:
                return await self._held(arm, run, why)
            if plan.features.per_tick and not lockstep and self._unanswered():
                # asked last tick and not answered by this one: a tick it never saw
                return await self._missed(
                    arm, run, f"it had not answered tick {tick - 1} by tick {tick}"
                )
            self._take_in(run, tick, plan)
            waited_out = False
            if self._wants(run, tick, plan, segment):
                if (why := await arm._loop_frames(obs)) is not None:
                    return await self._held(arm, run, why)
                ended = await self._request(arm, run, Observation(tick, obs), plan, lockstep)
                if ended is not None:
                    return ended
                # a wait for a chunk that outlasted this tick sends nothing in it, and the tick
                # due when it ended reads and judges the arm first (the module's note)
                waited_out = (
                    run.last_len != 1 and arm.now() >= started + (tick + 1) * plan.period_s - EXACT
                )
            while run.queue and run.queue[0][0] < tick:
                run.queue.popleft()  # meant for a tick the pacer skipped
            action = run.queue.popleft()[1] if run.queue and run.queue[0][0] == tick else None
            if waited_out:
                run.still = None  # an arm held through a wait is not a stalled one
            elif action is None:
                ended = await self._idle(arm, run, segment, now)
                if ended is not None:
                    return ended
            else:
                run.starved_since = None
                ended = await self._play(arm, run, segment, action, now)
                if ended is not None:
                    return ended
            arm._tick_timing.add(time.perf_counter() - ticked)
            run.ticks += 1
            following = await self._pace(arm, run, plan, started, tick + 1)
            if isinstance(following, SegmentEnd):
                return following
            tick = following
        return self._end(arm, run, "guard", "the arm's transport was closed")

    async def _pace(
        self, arm: LeRobotReal, run: _Run, plan: Plan, started: float, following: int
    ) -> int | SegmentEnd:
        """Sleep to tick `following`'s deadline and say which tick it is on waking, or end the
        segment where a runner in tick mode lost one.

        A tick that overran that deadline goes to the next whole period strictly after now,
        and the ticks between are skipped rather than sent late, two to a period. The sleep
        never wakes a tick short of its deadline: the simulator's clock rounds a sleep to its
        nearest whole step, which for a period that is not a whole number of steps can land
        up to half a step early, in the period before the tick's own, and what is left is
        slept again. A clock whose step is longer than a period can also wake past a later
        tick's deadline, and that later tick is the one it is on, the ones between skipped."""
        now = arm.now()
        if now > started + following * plan.period_s:
            after = max(following + 1, math.floor((now - started) / plan.period_s) + 1)
            if (ended := await self._skip(arm, run, plan, after - following)) is not None:
                return ended
            following = after
        deadline = started + following * plan.period_s
        await arm.clock.sleep(deadline - now)
        while (left := deadline - arm.now()) > EXACT:
            before = arm.now()
            await arm.clock.sleep(left)
            if arm.now() <= before:
                break  # a clock a sleep does not move is not slept on again
        woke = math.floor((arm.now() - started) / plan.period_s + EXACT)
        if woke > following:
            if (ended := await self._skip(arm, run, plan, woke - following)) is not None:
                return ended
            following = woke
        return following

    async def _skip(
        self, arm: LeRobotReal, run: _Run, plan: Plan, missed: int
    ) -> SegmentEnd | None:
        """Count `missed` ticks the pacer skipped, which a runner in tick mode never survives."""
        run.skipped += missed
        if plan.features.per_tick:
            return await self._missed(arm, run, f"the pacer skipped {missed} of its ticks")
        return None

    # ── one tick's parts ────────────────────────────────────────────────────────────────

    def _outstanding(self) -> bool:
        """A request of this segment's that has not been taken in yet, answered or not: the one
        request a segment may have out. `_take_in` and `_request` let go of each they take in,
        so an answer that lands between a tick's look and its next ask still counts as out,
        and is taken in on the tick after rather than asked over and lost."""
        req = self._inflight
        return req is not None and req.epoch == self.epoch

    def _unanswered(self) -> bool:
        """A request of this segment's that its runner has not answered yet."""
        req = self._inflight
        return req is not None and req.epoch == self.epoch and not req.future.done()

    def _take_in(self, run: _Run, tick: int, plan: Plan) -> None:
        """Take in whatever has come back by this tick: a request answered on the runner's
        worker, and on a lockstep clock the requests held back until now."""
        req = self._inflight
        if req is not None and req.future.done():
            self._inflight = None
            self._received(run, req, tick, plan)
        while run.held_back and run.held_back[0][0] <= tick:
            _, held = run.held_back.popleft()
            self._received(run, held, tick, plan)

    def _received(self, run: _Run, req: _Request, tick: int, plan: Plan) -> None:
        """One answered request, at the tick it is taken in: thrown away when it is another
        segment's or answers another tick, raised when the runner raised, merged otherwise."""
        if req.future.cancelled():
            return
        if req.epoch != self.epoch:
            run.stale += 1
            return
        chunk = req.future.result()  # the runner's own error ends the segment
        if not isinstance(chunk, Chunk) or chunk.tick != req.tick:
            run.stale += 1
            return
        self._merge(run, chunk, tick, plan)

    def _merge(self, run: _Run, chunk: Chunk, tick: int, plan: Plan) -> None:
        """Put a chunk's actions in the queue at `tick`, the tick it has arrived at."""
        if chunk.done:
            run.done = True
        if not chunk.actions:
            return
        if plan.features.per_tick:
            # asked every tick, it answers for the tick its answer is played at
            run.chunks += 1
            run.got_first = True
            run.queue = deque([(tick, chunk.actions[0])])
            return
        if run.waits_each:
            # one chunk at a time it is played from its first action at the tick it lands: the
            # arm held still while it was on its way, so none of it is for a tick already played
            fresh = [(tick + i, action) for i, action in enumerate(chunk.actions)]
        else:
            fresh = [
                (chunk.tick + i, action)
                for i, action in enumerate(chunk.actions)
                if chunk.tick + i >= tick
            ]
        run.last_len = len(chunk.actions)
        if not fresh:
            # every action was for a tick already played: counted as thrown away, and not as
            # the segment's first chunk, whose grace still runs for the next
            run.late += 1
            return
        run.chunks += 1
        run.got_first = True
        # the unplayed rest replaces the queue from its first tick on, never appended to it
        kept = deque(entry for entry in run.queue if entry[0] < fresh[0][0])
        kept.extend(fresh)
        run.queue = kept

    def _wants(self, run: _Run, tick: int, plan: Plan, segment: Segment) -> bool:
        """Whether this tick asks the runner for more: never after it said it was done or once
        the segment has played its chunks, never with a request of its own outstanding, every
        tick in tick mode, one chunk at a time once the last has been played out, and otherwise
        once the queue is down to `refill_at` of the last chunk's length and the runner's latency
        (`refill_due`). A chunk is one that had something to play (`_merge`), so an answer
        thrown away is asked over rather than counted, and a runner whose answers never play
        ends on the grace or `STARVE_S` rather than on its chunks."""
        if run.done or self._outstanding():
            return False
        if segment.max_chunks is not None and run.chunks >= segment.max_chunks:
            return False
        if plan.features.per_tick:
            return True
        if run.held_back:
            return False  # a request held back on its way is the one outstanding
        if run.one_at_a_time():
            return not run.left(tick)  # the next chunk once the last has been played out
        return refill_due(run.left(tick), plan.latency_ticks, run.last_len)

    async def _request(
        self, arm: LeRobotReal, run: _Run, observation: Observation, plan: Plan, lockstep: bool
    ) -> SegmentEnd | None:
        """Ask the runner for a chunk, stamped with this tick. On a lockstep clock the answer is
        awaited here, with time standing still, and held back its `k` ticks whole, or one tick
        where `k` is 0 and something is still queued or chunks go one at a time, to be judged
        when it is let go as the arm would judge it on arrival. On the wall's clock a runner
        that answers at once is awaited here too, within what is left of the segment's
        patience, but only with nothing left to play from this tick on: a first chunk, or a
        runner that answers one action at a time. Otherwise the answer is left on the worker and
        taken in on the tick it has come back by, while the arm plays what is queued, or, one
        chunk at a time (`_Run.one_at_a_time`), holds still."""
        future = self._submit(self.runner.next_chunk, observation, dict(run.last_sent))
        req = _Request(self.epoch, observation.tick, future)
        self._inflight = req
        each = run.one_at_a_time()
        queued = run.left(observation.tick)
        if not lockstep and (each or plan.latency_ticks > 0 or queued):
            return None
        patience = None
        if not lockstep:
            patience = (
                self._patience(run)
                if run.got_first
                else max(0.0, self.first_chunk_s - (arm.now() - run.started))
            )
        asked = time.perf_counter()
        try:
            answered = await self._answered(future, patience)
        finally:
            # a request still out when the segment is cancelled, by the executor's timeout
            # among others, counts for as long as it had been out
            if lockstep:
                took = time.perf_counter() - asked
                self.waited += 1
                self.thinking_s += took
                self.slowest_s = max(self.slowest_s, took)
        if not answered:
            return await self._starved(arm, run)
        self._inflight = None
        # asked with something queued, or one chunk at a time, an answer reaches the arm by the
        # next tick's look at the soonest, however fast it comes back, so the simulator holds it
        # back at least that long
        held = plan.latency_ticks or (1 if queued or each else 0)
        if lockstep and held:
            run.held_back.append((req.tick + held, req))
            return None
        self._received(run, req, observation.tick, plan)
        return None

    async def _idle(
        self, arm: LeRobotReal, run: _Run, segment: Segment, now: float
    ) -> SegmentEnd | None:
        """A tick with nothing to send: a runner that is done, a segment whose chunks are all
        played, or starvation, which the grace and `STARVE_S` bound."""
        run.still = None  # an arm left alone is not a stalled one
        outstanding = self._outstanding() or bool(run.held_back)
        if run.done:
            if segment.ends_on_holding:
                # the grasp may still be closing: read on, send nothing, and look again
                if run.done_at is None:
                    run.done_at = now
                elif now - run.done_at >= PICK_SETTLE_S:
                    return self._end(
                        arm,
                        run,
                        "finished",
                        "the policy finished and the gripper did not settle on anything",
                    )
                return None
            return self._end(
                arm, run, "chunks", f"the policy said it was done after {run.chunks} chunks"
            )
        if segment.max_chunks is not None and run.chunks >= segment.max_chunks and not outstanding:
            return self._end(arm, run, "chunks", f"its {run.chunks} chunks were played")
        run.starved += 1
        if run.starved_since is None:
            run.starved_since = now
        since = run.starved_since if run.got_first else run.started
        if now - since >= self._patience(run):
            return await self._starved(arm, run)
        return None

    async def _play(
        self,
        arm: LeRobotReal,
        run: _Run,
        segment: Segment,
        action: Mapping[str, Any],
        now: float,
    ) -> SegmentEnd | None:
        """Judge this tick's action and send it, or end the segment on it."""
        goals, why = arm._policy_goals(action)
        if why is None:
            why = clipped_too_long(goals, run.clipped_since, now, arm.joint_range_deg)
        if why is not None:
            return await self._held(arm, run, why)
        try:
            sent = await arm._send(goals)
        except Exception as e:
            run.failed_sends += 1
            if run.failed_sends >= FAILED_SENDS:
                said = arm.stop_error or f"{type(e).__name__}: {e}"
                return await self._held(
                    arm,
                    run,
                    f"{run.failed_sends} sends in a row did not reach the arm, the last with "
                    f"{said}",
                )
            return None
        run.failed_sends = 0
        if sent:
            # what went out, after the clip and LeRobot's step cap, for the runner's next ask
            run.last_sent = dict(sent)
        if segment.ends_on_stall:
            return await self._stall(arm, run, now)
        return None

    async def _stall(self, arm: LeRobotReal, run: _Run, now: float) -> SegmentEnd | None:
        """End `manipulate` once every joint has read within `STALL_DEG` of where it was for
        `STALL_S`, on ticks that sent, with the arm held where it is, as a verb's own stall
        holds it (`verbs._drive`). An arm stopped by something in its way still has the
        policy's last goal past it, and the servo would push on toward it through the pilot's
        thinking. The hold leaves the gripper's goal alone, so a grasp keeps its squeeze."""
        joints = {j: arm._joints[j] for j in JOINTS if j in arm._joints}
        anchor = run.still
        if anchor is None or any(
            abs(joints[j] - anchor[1].get(j, joints[j])) > STALL_DEG for j in joints
        ):
            run.still = (now, joints)
            return None
        if now - anchor[0] >= STALL_S:
            return await self._held(
                arm,
                run,
                f"the arm moved less than {STALL_DEG:g} degrees in {STALL_S:g} s of goals",
                how="stall",
            )
        return None

    # ── endings ─────────────────────────────────────────────────────────────────────────

    def _patience(self, run: _Run) -> float:
        """How long the segment may go without an action: `FIRST_CHUNK_S` until its first chunk,
        and `STARVE_S` after it, unless chunks go one at a time (`_Run.one_at_a_time`) and each
        is waited for as the first is: a runner the arm waits for by design has not fallen
        behind it."""
        return self.starve_s if run.got_first and not run.one_at_a_time() else self.first_chunk_s

    async def _starved(self, arm: LeRobotReal, run: _Run) -> SegmentEnd:
        """End a segment that went its whole patience without an action, which is said as that
        bound, however little of it the last wait had left."""
        which = "a first chunk" if not run.got_first else "a chunk"
        return await self._held(
            arm,
            run,
            f"the policy gave no action for {self._patience(run):g} s, waiting for {which}",
            how="starved",
        )

    async def _missed(self, arm: LeRobotReal, run: _Run, why: str) -> SegmentEnd:
        """A runner asked every tick has missed one: hold the arm, reset it on its worker behind
        whatever it is still doing, and end."""
        ended = await self._held(
            arm, run, f"the policy has to be asked every tick, and {why}", how="guard"
        )
        self._submit(self.runner.reset, self.instruction)
        return ended

    async def _held(
        self, arm: LeRobotReal, run: _Run, why: str, *, how: SegmentHow = "guard"
    ) -> SegmentEnd:
        return self._counted(arm, run, await arm._held(why, how=how))

    def _end(self, arm: LeRobotReal, run: _Run, how: SegmentHow, reason: str) -> SegmentEnd:
        return self._counted(arm, run, SegmentEnd(how, reason))

    @staticmethod
    def _counted(arm: LeRobotReal, run: _Run, ended: SegmentEnd) -> SegmentEnd:
        elapsed = arm.now() - run.started
        stats = SegmentStats(
            ticks=run.ticks,
            skipped=run.skipped,
            chunks=run.chunks,
            starved=run.starved,
            stale=run.stale + run.late,
            clips=arm._range_clips - run.clips_at,
            hz=round(run.ticks / elapsed, 1) if elapsed > 0 else None,
        )
        return dataclasses.replace(ended, stats=stats)


__all__ = [
    "CLIP_SUSTAIN_S",
    "FAILED_SENDS",
    "FIRST_CHUNK_S",
    "MAX_RATE_HZ",
    "MIN_RATE_HZ",
    "REFILL_LATENCIES",
    "REGISTER_PERIOD_S",
    "RESET_S",
    "STALL_S",
    "STARVE_S",
    "Plan",
    "PolicyLoop",
    "Segment",
    "Tally",
    "clipped_too_long",
    "latency_ticks",
    "longest_latency",
    "rate_refusal",
    "refill_at",
    "refill_due",
    "speed_cap",
    "starved_each_chunk",
]
