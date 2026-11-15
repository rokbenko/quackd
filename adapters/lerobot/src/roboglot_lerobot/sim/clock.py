"""The arm simulator's time: the physics steps only while everyone who is waiting on it waits.

The real backend paces and watches the arm through one clock (`real.Clock`): the verbs' ticks,
the rest move's, the settle before a hold is read back and the policy's own rate all sleep on
it, and everything it measures it measures against `now()`. On an arm that is the wall's time.
Here it is the world's, and the world moves only in lockstep with the sleepers, through the
team clock the other simulators share (`roboglot/sim2d/clock.py`, used through its public calls
only).

Each sleep is a participant of its own. It takes a fresh id, parks under it for the time it
asked for, and unregisters it as it wakes, so the team clock's rule does the rest: time runs
while every sleeper is parked and stops the moment any of them is awake. Nobody sleeping is
nobody registered, so time stands still. A pilot's thinking, a verb's reads and writes and a
lone task's work between two sleeps therefore cost no sim time at all, and two tasks sleeping at
once both reach their wake-up, however their sleeps overlap. The ids are numbered in the order
the sleeps arrive and zero-padded, so the team clock's sorted wake order is arrival order.

One id for the whole arm, as the Microduck has one per duck, cannot serve it: two tool calls
over MCP are two tasks, the team clock refuses two tasks sleeping under one id, and a single
gate that one task holds while the others ride along on it freezes the riders.

Nothing here imports `mujoco`: the world does, and the team clock is imported when a clock is
built, inside the transport's `connect()`.
"""

from __future__ import annotations

import asyncio
import itertools
import math
import time
from typing import TYPE_CHECKING, Any

from roboglot.transport.base import TransportError
from roboglot_lerobot.sim.model import LABEL
from roboglot_lerobot.sim.world import ArmWorld
from roboglot_lerobot.verbs import PICK_POLL_S, TICK_S

if TYPE_CHECKING:
    from collections.abc import Callable

SUBSTEPS = 5
"""Physics steps per step of the clock: 10 ms at the scene's timestep (`model.TIMESTEP_S`), a
tenth of a verb's tick. Fine enough that every wait the real backend makes lands on a whole
number of clock steps (`SimClock` refuses one that does not), coarse enough that the event loop
is not woken for every physics step."""
YIELD_EVERY = 4
"""Clock steps between two chances for the event loop to run anything else, when the clock runs
as fast as it can: the team clock's own default, and far inside the deadline of the one
thing that has to get through, a bus call's (`LeRobotReal.timeout_s`)."""
PERIODS = {"TICK_S": TICK_S, "PICK_POLL_S": PICK_POLL_S}
"""The waits the verbs make on this clock, which a clock step has to divide evenly: a sleep is
rounded to whole clock steps, and a tick that did not divide would drift against the verbs'
own arithmetic of ticks."""
EXACT = 1e-9
"""How near a whole number of clock steps a period has to be to be one, allowing for the float
arithmetic of a division, and for nothing else."""


def _divides(dt: float, period: float) -> bool:
    steps = period / dt
    return steps >= 1 and math.isclose(steps, round(steps), rel_tol=0.0, abs_tol=EXACT)


class _Stepper:
    """What the team clock steps: the world, and nothing else it would read.

    The team clock's world protocol has a settable `t`, and the world's is a property that
    takes its lock, so this carries a copy, taken after every step: the one time anybody here
    reads, and still readable once the world has closed. With `realtime`, each step is also held
    until the wall has caught up with it (`_pace`)."""

    def __init__(self, world: ArmWorld, realtime: bool) -> None:
        self.world = world
        self.realtime = realtime
        self.t = world.t
        self._due: float | None = None

    def step(self, dt: float) -> None:
        self.world.step(dt)
        self.t = self.world.t
        if self.realtime:
            self._pace(dt)

    def _pace(self, dt: float) -> None:
        """Hold this step until `dt` of the wall's time has passed since the last one ended,
        on `perf_counter`. The event loop's own sleep is only as fine as its timer, which on
        Windows is about 15 ms, and a clock paced on it would crawl at a fraction of real time.

        A step that ends late sets the pace from now rather than racing to catch up, so a slow
        render, or a stretch with time stopped because nobody slept, is never made up for by a
        burst of steps faster than real time. The hold blocks the event loop, one clock step
        at most at a time, and the clock yields to the loop after every step when it paces
        (`SimClock`)."""
        now = time.perf_counter()
        due = now if self._due is None else self._due + dt
        if due > now:
            time.sleep(due - now)
            self._due = due
        else:
            self._due = now


class SimClock:
    """The world's time, as `real.Clock` asks for it: `now()` and `sleep()`.

    `dt` is the model's timestep times `SUBSTEPS`, and it must divide every period in
    `PERIODS`, or the clock refuses to be built. `realtime` holds each step until the wall has
    caught up (`_Stepper._pace`), for a person watching the viewer, and it is still lockstep:
    time runs only while every sleeper is parked, so nothing measured on it is a rate."""

    lockstep = True
    """Time stands still while nobody sleeps on it (`real.Clock`). A policy segment reads this
    and awaits a runner's inference in its own turn, costing no sim time, then holds the answer
    back by the runner's declared latency before it judges it (`policy/loop.py`)."""

    def __init__(self, world: ArmWorld, *, realtime: bool = False) -> None:
        from roboglot.sim2d.clock import TeamClock

        self.world = world
        self.dt = world.timestep * SUBSTEPS
        uneven = [name for name, period in PERIODS.items() if not _divides(self.dt, period)]
        if uneven:
            raise ValueError(
                f"{LABEL} a clock step of {self.dt:g} s does not divide "
                f"{' or '.join(uneven)}, so the verbs' ticks would drift against the physics. "
                "Give the model a timestep that divides them."
            )
        self.realtime = realtime
        self._stepper = _Stepper(world, realtime)
        # the team clock's own realtime sleeps on the event loop, which is too coarse here
        self._team = TeamClock(
            self._stepper, dt=self.dt, realtime=False, yield_every=1 if realtime else YIELD_EVERY
        )
        self._ids = itertools.count(1)
        self._live: set[str] = set()
        self._closed = False

    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def failure(self) -> Exception | None:
        """Why time stopped, if the physics could not step on (`WorldStepError`), else None."""
        return self._team.failure

    def now(self) -> float:
        """Sim time in seconds: the world's, as of its last step."""
        return self._stepper.t

    def add_tick_hook(self, hook: Callable[[Any], None]) -> None:
        """Called on the event loop after every clock step. A hook that raises
        KeyboardInterrupt, as the live viewer's close button does, aborts every sleeper."""
        self._team.add_tick_hook(hook)

    def remove_tick_hook(self, hook: Callable[[Any], None]) -> None:
        self._team.remove_tick_hook(hook)

    async def sleep(self, seconds: float) -> None:
        """Wait `seconds` of sim time, rounded to whole clock steps, under an id of its own.

        A wait of nothing yields to the loop and registers nothing: the team clock registers
        an id even for a zero wait and never lets it go, which would stop time for everyone.

        Every error a sleep can meet comes out as the transport's, whoever called it, so the
        rest move, the take-hold and the policy loop, which sleep on the clock directly, meet
        the same ones as a verb sleeping through the transport: the live viewer closed is
        `Aborted`, physics that could not step on is `TransportError`, and so is a sleep cut
        short by the clock's close."""
        if self._closed:
            raise TransportError(f"{LABEL} the simulator is closed.")
        if seconds <= 0:
            await asyncio.sleep(0)
            return
        from roboglot.sim2d.clock import HookInterrupt, WorldStepError

        pid = f"s{next(self._ids):09d}"
        self._live.add(pid)
        try:
            await self._team.sleep(pid, seconds)
        except HookInterrupt as e:
            from roboglot.safety import Aborted  # here: the safety module imports no clock

            raise Aborted(str(e)) from None
        except WorldStepError as e:
            raise TransportError(str(e)) from e
        except asyncio.CancelledError:
            task = asyncio.current_task()
            if self._closed and (task is None or not task.cancelling()):
                # the close let go of this sleep, and nobody cancelled the task that slept
                raise TransportError(f"{LABEL} the simulator closed during a wait.") from None
            raise
        finally:
            self._live.discard(pid)
            self._team.unregister(pid)

    async def close(self) -> None:
        """Let go of every sleep still parked, which ends each with a `TransportError`, and
        stop the clock. Every sleep after this is refused. Safe to call twice."""
        if self._closed:
            return
        self._closed = True
        for pid in sorted(self._live):
            self._team.unregister(pid)
        await self._team.stop()
