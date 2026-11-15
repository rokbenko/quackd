"""Runners that need no torch: a Python function answers every request.

`ScriptedRunner` is what the tests drive the policy loop with, and what a `PolicyLike` becomes
when it is handed to the backend: `ScriptedRunner.wrapping(policy)` asks it for one action per
tick, as `pick` always did. Everything here is deterministic. The same script, asked at the same
ticks with the same readings, answers the same chunks, which is what lets a test say that a
lockstep clock and a run on the wall's clock played the same trajectory.

A few scripts have names (`SCRIPTS`), and `named` builds a runner from one. They are what
`roboglot policy serve --policy scripted:NAME` serves, so the policy server, its client and the
loop behind them can be run end to end, on the arm or in the tests, with no checkpoint and no
torch anywhere: `hold` holds the arm where it reads, and `sweep` swings the wrist to and fro.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from roboglot_lerobot.policy.runner import Chunk, Features, Observation, PolicyLike
from roboglot_lerobot.verbs import TICK_S

Script = Callable[[Observation, Mapping[str, float]], Sequence[Mapping[str, Any]] | None]
"""A scripted policy: an observation and the command last sent in, the chunk's actions out, one
per tick from the observation's, or None for a policy with nothing more to do."""


class ScriptedRunner:
    """A `PolicyRunner` whose answers come from `script`.

    `rate_hz` and `rate_source` are its declared rate and where that came from, as a runner
    loaded from a checkpoint declares them. `latency_ticks` is how many ticks it declares it
    takes to answer, which on a lockstep clock holds every chunk back that many ticks before it
    is played, and which it never actually spends: a script answers as fast as Python does.
    `per_tick` makes it a runner asked every tick (tick mode). `chunk` is how many actions its
    script answers with, where it says, which a policy server reports. `instruction` and
    `resets` are what the last `reset` was told and how many there have been, for a test to
    read."""

    def __init__(
        self,
        script: Script,
        *,
        rate_hz: float,
        rate_source: str = "the script",
        latency_ticks: int = 0,
        per_tick: bool = False,
        chunk: int | None = None,
    ) -> None:
        if latency_ticks < 0:
            raise ValueError(f"latency_ticks={latency_ticks} must be 0 or more")
        self.script = script
        self.rate_hz = rate_hz
        self.rate_source = rate_source
        self.latency_ticks = latency_ticks
        self.per_tick = per_tick
        self.chunk = chunk
        self.instruction = ""
        self.resets = 0
        self.requests = 0
        self.closed = False
        self._on_reset: Callable[[], None] | None = None

    @classmethod
    def wrapping(cls, policy: PolicyLike, *, rate_hz: float, rate_source: str) -> ScriptedRunner:
        """A `PolicyLike` as a runner: each request is one `act`, its answer a chunk of one
        action, and None a runner that is done. The policy's own `reset()`, where it has one,
        runs at every `reset`, and the instruction becomes the `task` every `act` is told."""
        runner: ScriptedRunner

        def act(observation: Observation, _sent: Mapping[str, float]) -> list[Any] | None:
            action = policy.act(dict(observation.reading), task=runner.instruction)
            return None if action is None else [action]

        runner = cls(act, rate_hz=rate_hz, rate_source=rate_source)
        reset = getattr(policy, "reset", None)
        if callable(reset):
            runner._on_reset = reset
        return runner

    def reset(self, instruction: str) -> None:
        self.instruction = instruction
        self.resets += 1
        if self._on_reset is not None:
            self._on_reset()

    def features(self) -> Features:
        return Features(self.rate_hz, self.rate_source, per_tick=self.per_tick)

    def next_chunk(self, observation: Observation, sent: Mapping[str, float]) -> Chunk:
        self.requests += 1
        actions = self.script(observation, sent)
        if actions is None:
            return Chunk(observation.tick, done=True)
        return Chunk(observation.tick, tuple(actions))

    def latency_s(self) -> float:
        # the declared ticks at the declared rate, so the loop's own rounding gives the ticks back
        if not (math.isfinite(self.rate_hz) and self.rate_hz > 0):
            return 0.0
        return self.latency_ticks / self.rate_hz

    def close(self) -> None:
        self.closed = True


# ── the scripts a policy server serves by name ──────────────────────────────────────────

SCRIPTED_HZ = 1.0 / TICK_S
"""The rate a named script runs at unless it is given one: the verbs' own tick, which is the
pace every other goal Roboglot sends the arm is written for."""
SCRIPTED_CHUNK_S = 1.0
"""How much of the arm's time one chunk of a named script covers, which at its rate is how many
actions it holds: long enough that the loop asks a few times a second, short enough that a
chunk dropped for a newer one costs nothing."""
SWEEP_JOINT = "wrist_flex"
"""The joint `sweep` swings: the lightest of the arm's, whose swing moves nothing else."""
SWEEP_DEG = 5.0
"""How far either side of where it started `sweep` swings its joint. Small, so it is a test of
the path from a policy to the arm and not a move, and well inside a joint's travel."""
SWEEP_PERIOD_S = 2.0
"""How long one swing there and back takes."""


def _positions(observation: Observation) -> dict[str, float]:
    """Every motor's reading in an observation, by motor name."""
    return {
        key.removesuffix(".pos"): float(value)
        for key, value in observation.reading.items()
        if key.endswith(".pos")
    }


def _hold(length: int) -> Script:
    """A chunk of `length` goals, each every motor where it reads now: an arm that stays put."""

    def script(observation: Observation, _sent: Mapping[str, float]) -> list[dict[str, float]]:
        here = _positions(observation)
        return [dict(here) for _ in range(length)]

    return script


class _Sweep:
    """`SWEEP_JOINT` swung `SWEEP_DEG` either side of where the segment found it, one swing a
    `SWEEP_PERIOD_S`, timed by the tick so the same ticks always get the same goals."""

    def __init__(self, length: int, rate_hz: float) -> None:
        self.length = length
        self.ticks_a_swing = SWEEP_PERIOD_S * rate_hz
        self.anchor: tuple[int, float] | None = None

    def reset(self) -> None:
        self.anchor = None

    def __call__(self, observation: Observation, _sent: Mapping[str, float]) -> list[Any]:
        here = _positions(observation)
        if SWEEP_JOINT not in here:
            raise ValueError(
                f"scripted:sweep swings {SWEEP_JOINT}, and this arm has no motor by that name"
            )
        if self.anchor is None:
            self.anchor = (observation.tick, here[SWEEP_JOINT])
        start, middle = self.anchor
        return [
            {
                SWEEP_JOINT: middle
                + SWEEP_DEG
                * math.sin(2 * math.pi * (observation.tick + i - start) / self.ticks_a_swing)
            }
            for i in range(self.length)
        ]


SCRIPTS = {
    "hold": "holds every motor where it reads, so a segment of it ends on a stall",
    "sweep": f"swings {SWEEP_JOINT} {SWEEP_DEG:g} degrees either side of where it started",
}
"""The scripts `named` builds, and what each does, as `roboglot policy serve` lists them."""


def named(name: str, *, rate_hz: float | None = None) -> ScriptedRunner:
    """The runner for the script called `name`, at `rate_hz` (from `--fps`) or at its own rate,
    `SCRIPTED_HZ`. A name that is not in `SCRIPTS` is a ValueError that lists them."""
    if name not in SCRIPTS:
        raise ValueError(
            f"there is no scripted policy called {name!r}: the scripted ones are "
            f"{', '.join(f'scripted:{n}' for n in SCRIPTS)}"
        )
    rate = SCRIPTED_HZ if rate_hz is None else rate_hz
    source = "--fps" if rate_hz is not None else f"scripted:{name}'s own, the verbs' tick"
    length = max(1, round(rate * SCRIPTED_CHUNK_S)) if math.isfinite(rate) else 1
    if name == "hold":
        return ScriptedRunner(_hold(length), rate_hz=rate, rate_source=source, chunk=length)
    sweep = _Sweep(length, rate)
    runner = ScriptedRunner(sweep, rate_hz=rate, rate_source=source, chunk=length)
    runner._on_reset = sweep.reset
    return runner


__all__ = [
    "SCRIPTED_CHUNK_S",
    "SCRIPTED_HZ",
    "SCRIPTS",
    "SWEEP_DEG",
    "SWEEP_JOINT",
    "Script",
    "ScriptedRunner",
    "named",
]
