"""The run slot: at most one run on a robot at a time, a goal its owner gave or a meeting.

An idle robot's brain runs the standing meeting task when a robot it may talk to comes close
(`quackd.onboard.meeting`), and a goal from `quackd onboard goal` takes the slot from a meeting
but never from another goal. Every run is a `quackd run` in all but where it came from: the same
loop, the same executor and its gates, the same record, with the robots nearby read through the
`NearbyLike` seam straight from this robot's talk. Each run has a thread and an event loop of its
own, so a hung model call holds that thread and never the service.

Meetings are bounded three ways, since a stranger costs an attacker nothing to make: no more than
`per_hour` an hour, never the same robot twice in `COOLDOWN_S`, and none with a stranger unless
the owner said `with_strangers`.
"""

from __future__ import annotations

import asyncio
import contextlib
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from quackd.duckfile.parser import duck_from_goal
from quackd.duckfile.schema import DuckFile
from quackd.onboard.config import RobotFile
from quackd.onboard.meeting import NEARBY_LINE, meeting_duck

#: Seconds before the same robot can start another meeting, and how long a meeting runs on once
#: every robot in it has left, for a last word to arrive.
COOLDOWN_S = 600.0
LINGER_S = 20.0
#: Why a run stopped when the slot stopped it, as its record says.
FOR_A_GOAL = "a goal came, and a goal takes the slot from a meeting"
EVERYONE_LEFT = "the robots it met all left"
STOPPED = "stopped by quackd onboard stop"


@dataclass
class Running:
    """The run in the slot: what it is, who it met, and how to stop it from another thread."""

    kind: str  # "meeting" or "goal"
    title: str
    started: float
    with_peers: set[str] = field(default_factory=set)
    agent: Any = None
    #: the terminal that gave a goal, over the control connection: its questions are asked
    #: there and its stop stops this run (`quackd.front.Front`); None for a meeting
    front: Any = None
    aio: asyncio.AbstractEventLoop | None = None
    thread: threading.Thread | None = None
    result: Any = None
    error: str | None = None
    emptied_at: float | None = None
    #: a stop asked for, kept so a run stopped before its loop existed stops as it starts
    stop_wanted: str | None = None


#: Builds one run's loop: (task file, kind, front or None) to an `AgentLoop` ready to run.
LoopFactory = Callable[[DuckFile, str, Any], Any]


class RunSlot:
    """One run at a time on this robot, and the rules for which run gets it."""

    def __init__(
        self,
        file: RobotFile,
        make_loop: LoopFactory,
        *,
        safe_verbs: Callable[[], list[str]],
        log: Callable[[str], None] = lambda _line: None,
        clock: Callable[[], float] = time.monotonic,
        holding: Callable[[Any], None] | None = None,
        released: Callable[[], None] | None = None,
    ) -> None:
        self.file = file
        self._make_loop = make_loop
        #: told on the run's own loop that a run holds the body, with its agent, and that it let
        #: go: a peer's courtesy goes through that run's executor and no other
        self._holding = holding
        self._released = released
        self._safe_verbs = safe_verbs
        self._log = log
        self._clock = clock
        self._lock = threading.RLock()
        self.running: Running | None = None
        self._met_at: dict[str, float] = {}
        self._meetings: deque[float] = deque()

    # ── which run may start ─────────────────────────────────────────────────────────────

    def why_no_meeting(self, peer: str, tier: str) -> str | None:
        """None when a meeting with `peer` may start now; otherwise the reason it may not."""
        now = self._clock()
        if not self.file.brain.llm:
            return "this robot has no brain configured (brain.llm in robot.yaml)"
        if tier == "stranger" and not self.file.meeting.with_strangers:
            return "its owner meets no strangers (meeting.with_strangers is false)"
        last = self._met_at.get(peer)
        if last is not None and now - last < COOLDOWN_S:
            return f"it met this robot {now - last:.0f} s ago"
        while self._meetings and now - self._meetings[0] > 3600:
            self._meetings.popleft()
        if len(self._meetings) >= self.file.meeting.per_hour:
            return f"it already had {self.file.meeting.per_hour} meetings this hour"
        return None

    def robot_near(self, peer: str, tier: str) -> str:
        """A robot this one may talk to came close: it joins the run in the slot, or starts a
        meeting when the slot is empty and the rules allow one. Says what happened."""
        with self._lock:
            running = self.running
            if running is not None and running.thread is not None and running.thread.is_alive():
                running.with_peers.add(peer)
                running.emptied_at = None
                return f"it joins the {running.kind} in hand"
            why = self.why_no_meeting(peer, tier)
            if why is not None:
                return f"no meeting: {why}"
            self._met_at[peer] = self._clock()
            self._meetings.append(self._clock())
            started = self._start("meeting", meeting_duck(self.file.meeting.task), None, {peer})
            return "a meeting starts" if started else "no meeting: the run did not start"

    def robot_left(self, peer: str) -> None:
        """A robot left: a meeting whose robots have all gone ends `LINGER_S` later."""
        with self._lock:
            running = self.running
            if running is None or running.kind != "meeting":
                return
            running.with_peers.discard(peer)
            if not running.with_peers and running.emptied_at is None:
                running.emptied_at = self._clock()

    def tick(self) -> None:
        """What the service calls every second: end a meeting nobody is left in."""
        with self._lock:
            running = self.running
            if (
                running is not None
                and running.kind == "meeting"
                and running.emptied_at is not None
                and self._clock() - running.emptied_at >= LINGER_S
            ):
                self._stop(running, EVERYONE_LEFT)

    def goal(self, text: str, front: Any = None, *, max_steps: int | None = None) -> str | None:
        """Start a goal, taking the slot from a meeting. None when it started; otherwise why not."""
        with self._lock:
            running = self.running
            if running is not None and running.thread is not None and running.thread.is_alive():
                if running.kind == "goal":
                    return "a goal is running: quackd onboard stop ends it"
                self._stop(running, FOR_A_GOAL)
                if running.thread is not None:
                    running.thread.join(timeout=15)
            duck = duck_from_goal(
                f"{text.strip()}\n\n{NEARBY_LINE}", self._safe_verbs(), memory=True
            )
            if max_steps is not None:
                front_matter = duck.frontmatter
                budgets = front_matter.budgets.model_copy(update={"max_steps": max_steps})
                duck = duck.model_copy(
                    update={"frontmatter": front_matter.model_copy(update={"budgets": budgets})}
                )
            if not self._start("goal", duck, front, set()):
                return "the goal did not start"
            return None

    def stop(self, why: str = STOPPED) -> bool:
        """Stop the run in hand, a goal or a meeting; the slot stays. False when it was empty."""
        with self._lock:
            running = self.running
            if running is None or running.thread is None or not running.thread.is_alive():
                return False
            self._stop(running, why)
            return True

    def state(self) -> dict[str, Any]:
        """What the slot holds, for `quackd onboard status`."""
        with self._lock:
            running = self.running
            if running is None or running.thread is None or not running.thread.is_alive():
                return {"run": "idle"}
            step = getattr(running.agent, "step", None)
            return {
                "run": running.kind,
                "title": running.title,
                "with": sorted(running.with_peers),
                "seconds": round(self._clock() - running.started),
                **({"step": step} if isinstance(step, int) else {}),
            }

    # ── running ─────────────────────────────────────────────────────────────────────────

    def _start(self, kind: str, duck: DuckFile, front: Any, peers: set[str]) -> bool:
        try:
            agent = self._make_loop(duck, kind, front)
        except Exception as e:  # a run that cannot be built is said and the slot stays empty
            self._log(f"the {kind} could not start: {e}")
            return False
        running = Running(kind, duck.name, self._clock(), set(peers), agent, front)
        thread = threading.Thread(
            target=self._run, args=(running,), name=f"onboard-{kind}", daemon=True
        )
        running.thread = thread
        self.running = running
        thread.start()
        return True

    def _run(self, running: Running) -> None:
        aio = asyncio.new_event_loop()
        running.aio = aio
        front = running.front
        try:
            running.result = aio.run_until_complete(self._go(running))
        except Exception as e:  # the run's own record says the rest
            running.error = str(e)
            self._log(f"the {running.kind} ended with an error: {e}")
        finally:
            with contextlib.suppress(Exception):
                aio.close()
        result = running.result
        if front is not None:
            with contextlib.suppress(Exception):  # the terminal that gave it may have gone
                if result is not None:
                    front.finished(
                        result.outcome,
                        result.reason,
                        run_dir=result.run_dir,
                        summary=result.summary,
                    )
                else:
                    front.errored(running.error or "the run ended before it began")
                front.end(0 if result is not None and result.outcome == "success" else 1)
        outcome = getattr(result, "outcome", None) or running.error or "ended"
        self._log(f"the {running.kind} ended: {outcome}")

    async def _go(self, running: Running) -> Any:
        """The run, on its own loop, with the terminal that gave it bound to that loop."""
        front = running.front
        if front is not None:
            front.run_dir(running.agent.run_dir)
            front.bind(running.agent, asyncio.get_running_loop())
        if running.stop_wanted is not None:
            _set_abort(running.agent, running.stop_wanted)
        if self._holding is not None:
            self._holding(running.agent)
        try:
            return await running.agent.run()
        finally:
            if self._released is not None:
                with contextlib.suppress(Exception):
                    self._released()
            if front is not None:
                front.unbind()

    def _stop(self, running: Running, why: str) -> None:
        # kept first: a run whose loop does not exist yet reads it as it starts (`_go`)
        running.stop_wanted = running.stop_wanted or why
        aio, agent = running.aio, running.agent
        if aio is not None and agent is not None and not aio.is_closed():
            with contextlib.suppress(RuntimeError):  # the loop closed as the stop was sent
                aio.call_soon_threadsafe(_set_abort, agent, why)
        if running.front is not None:
            # a run waiting on its question holds its own loop, where the abort cannot land
            # until it is answered: the question is taken back, as a page's Stop takes it
            with contextlib.suppress(Exception):
                running.front.questions.close(why)
        self._log(f"the {running.kind} is stopping: {why}")


def _set_abort(agent: Any, why: str) -> None:
    """On the run's own loop: name the stop, unless something stopped it first, and set the
    abort, which ends the verb in hand and the run, as the kill switch's first press does."""
    if not agent.executor.abort.is_set() and not agent.stop_reason:
        agent.stop_reason = why
    agent.executor.abort.set()
