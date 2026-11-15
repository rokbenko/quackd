"""What a policy segment asks of a policy: a runner, the chunks it answers with, and its rate.

A runner is whatever turns an observation of the arm into goals for it: a scripted one in this
package (`scripted.py`), and later a checkpoint served by a process of its own. The segment's
loop (`loop.py`) owns the arm and the pace, and asks the runner for a chunk of actions at a time,
one action per tick from the tick the observation was read at. Every call on a runner is made on
the loop's own worker thread and never on the event loop, so a runner may block as long as it
likes: the loop keeps its own time meanwhile, and the bus has threads of its own.

`PolicyLike` is the older shape, one observation in and one goal out, which `pick` has taken
since it was written. A `ScriptedRunner` wraps one, so a policy object written for it still runs,
one action per tick at the rate the backend gives it.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol


class PolicyLike(Protocol):
    """What `pick` needs from a policy: one observation in, one joint goal out (or None
    when it considers the task done). The `real` backend never builds one on its own.

    A policy may also have a `reset()`, which takes nothing and is called at the start of every
    segment, before the first `act`. A LeRobot policy keeps a queue of the actions its last
    chunk predicted (`POLICY_SELECT_ACTION` in `upstream_api.py`), and without a reset a second
    pick would play out the first one's queue from wherever the arm now is. It is looked up
    rather than declared here, so a policy that keeps nothing between calls need not have one."""

    def act(self, observation: dict[str, Any], *, task: str) -> dict[str, float] | None: ...


@dataclass(frozen=True)
class Features:
    """What a runner says about itself before a segment starts.

    `rate_hz` is how many ticks a second it was made for, and `rate_source` is where that
    number came from, in a few words a refusal can quote: no rate is ever measured here, and a
    policy's own is the fps of the data it learned from. `per_tick` is a runner that has to be
    asked every tick (tick mode), because it carries state from one tick to the next and a tick
    it never saw leaves that state wrong."""

    rate_hz: float
    rate_source: str
    per_tick: bool = False


@dataclass(frozen=True)
class Observation:
    """One tick's reading of the arm, handed to a runner with the tick it was read at.

    `reading` is shaped as the follower's own observation is: `<motor>.pos` for every motor,
    in degrees (the gripper 0..100), and each camera's newest frame under the camera's name. A
    chunked runner is given frames only with a request, never between two."""

    tick: int
    reading: Mapping[str, Any]


@dataclass(frozen=True)
class Chunk:
    """A runner's answer: `actions[i]` is the goal for tick `tick + i`, each a mapping from motor
    name to degrees (the gripper 0..100). `tick` is the observation's, echoed, and a chunk whose
    tick is not the request's is thrown away. `done` is a runner with nothing more to do, which
    a `PolicyLike` says by answering None: the loop asks it for nothing after that."""

    tick: int
    actions: tuple[Mapping[str, Any], ...] = ()
    done: bool = False


class PolicyRunner(Protocol):
    """A policy as a segment's loop drives it. Every method may block, and each is called on the
    loop's own single worker thread, one at a time, never two at once.

    - `reset(instruction)` starts a segment: the runner forgets every queued action and every
      state it carried, and takes `instruction` as the task it is told.
    - `features()` says its rate and whether it runs every tick (`Features`).
    - `next_chunk(observation, sent)` answers one observation with a `Chunk`. `sent` is the
      command the loop actually sent at the latest tick that sent one, after the travel clip and
      the step cap, and empty before the first: a runner that integrates its own deltas
      re-anchors on it rather than on what it asked for.
    - `latency_s()` is how long it takes to answer, as declared and never measured here. On the
      simulator, whose time stands still while a runner thinks, a chunk is held back that long
      before it is played, so a rehearsal plays what the arm would.
    - `close()` lets go of whatever it holds."""

    def reset(self, instruction: str) -> None: ...

    def features(self) -> Features: ...

    def next_chunk(self, observation: Observation, sent: Mapping[str, float]) -> Chunk: ...

    def latency_s(self) -> float: ...

    def close(self) -> None: ...


def is_runner(policy: object) -> bool:
    """Whether `policy` is a `PolicyRunner` already, rather than a `PolicyLike` to wrap."""
    return callable(getattr(policy, "next_chunk", None)) and callable(
        getattr(policy, "features", None)
    )


__all__ = ["Chunk", "Features", "Observation", "PolicyLike", "PolicyRunner", "is_runner"]
