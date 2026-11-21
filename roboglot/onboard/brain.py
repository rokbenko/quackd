"""The run slot: at most one run on a robot at a time, a goal its owner gave or a meeting.

An idle robot's brain runs the standing meeting task when a robot it may talk to comes close
(`roboglot.onboard.meeting`), and a goal from `roboglot onboard goal` takes the slot from a meeting
but never from another goal. Every run is a `roboglot run` in all but where it came from: the same
loop, the same executor and its gates, the same record, with the robots nearby read through the
`NearbyLike` seam straight from this robot's talk. Each run has a thread and an event loop of its
own, so a hung LLM call holds that thread and never the service.

Meetings are bounded three ways, since a stranger costs an attacker nothing to make: no more than
`per_hour` an hour, never the same robot twice in `COOLDOWN_S`, and none with a stranger unless
the owner said `with_strangers`.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import contextlib
import threading
import time
import weakref
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, ParamSpec, TypeVar

from roboglot.glotfile.parser import glot_from_goal
from roboglot.glotfile.schema import GlotFile
from roboglot.onboard.config import RobotFile
from roboglot.onboard.meeting import NEARBY_LINE, meeting_glot

_P = ParamSpec("_P")
_T = TypeVar("_T")

#: Seconds before the same robot can start another meeting, and how long a meeting runs on once
#: every robot in it has left, for a last word to arrive.
COOLDOWN_S = 600.0
LINGER_S = 20.0
#: Why a run stopped when the slot stopped it, as its record says.
FOR_A_GOAL = "a goal came, and a goal takes the slot from a meeting"
EVERYONE_LEFT = "the robots it met all left"
STOPPED = "stopped by roboglot onboard stop"
#: How the threads a run's calls run on are named, its kind after it: nothing in Python ends a
#: thread, and the interpreter waits at its exit for these, so one still going holds the process.
CALL_THREADS = "onboard-call-"
#: How long a goal waits for the meeting it takes the slot from to end, holding nothing that
#: status, a stop or the service's tick needs. A meeting moves nothing (`meeting_glot` offers it
#: what reports and what stops), so its teardown is a stop and a last look at the state; the
#: 15 s it has always been given is kept, and a meeting not ended by then is stuck.
MEETING_STOPS_S = 15.0
#: Why a goal is refused while the meeting it would take the slot from has not ended.
STILL_STOPPING = (
    "the meeting in hand is still stopping: give the goal again once roboglot onboard status "
    "says idle"
)
#: Why a robot coming near, and a second goal, start nothing while a goal waits for its meeting.
NO_MEETING_FOR_A_GOAL = "no meeting: a goal is taking the slot"
GOAL_WAITING = "a goal is taking the slot from a meeting: roboglot onboard stop ends it"


@dataclass
class Running:
    """The run in the slot: what it is, who it met, and how to stop it from another thread."""

    kind: str  # "meeting" or "goal"
    title: str
    started: float
    with_peers: set[str] = field(default_factory=set)
    agent: Any = None
    #: the terminal that gave a goal, over the control connection: its questions are asked
    #: there and its stop stops this run (`roboglot.front.Front`); None for a meeting
    front: Any = None
    aio: asyncio.AbstractEventLoop | None = None
    thread: threading.Thread | None = None
    result: Any = None
    error: str | None = None
    emptied_at: float | None = None
    #: a stop asked for, kept so a run stopped before its loop existed stops as it starts
    stop_wanted: str | None = None
    #: the run itself has returned, its teardown done as far as it went, though its thread may
    #: still be telling the terminal that gave it how it ended
    returned: bool = False


class CallPool(concurrent.futures.ThreadPoolExecutor):
    """A thread pool that counts the calls under way on it. Nothing in Python ends a thread, and
    the interpreter waits at its exit for every call a pool is running, while an idle worker
    leaves at once: the count, not a thread's being alive, is what says a pool would hold the
    process. `who` names the pool's owner in the line that says so."""

    def __init__(self, name: str, who: str, max_workers: int | None = None) -> None:
        super().__init__(max_workers=max_workers, thread_name_prefix=name)
        self.who = who
        self._busy = 0
        self._calls = threading.Condition()
        _COUNTED.add(weakref.ref(self, _COUNTED.discard))

    @property
    def busy(self) -> int:
        with self._calls:
            return self._busy

    def submit(
        self, fn: Callable[_P, _T], /, *args: _P.args, **kwargs: _P.kwargs
    ) -> concurrent.futures.Future[_T]:
        def call() -> _T:
            # counted from its start: one cancelled before it ran never holds anything
            with self._calls:
                self._busy += 1
            try:
                return fn(*args, **kwargs)
            finally:
                with self._calls:
                    self._busy -= 1
                    self._calls.notify_all()

        return super().submit(call)

    def wait_idle(self, timeout: float) -> bool:
        """Whether no call is under way, waiting up to `timeout` seconds for the last to end."""
        with self._calls:
            return self._calls.wait_for(lambda: self._busy == 0, max(0.0, timeout))


#: Every `CallPool` in the process, whoever made it, so a close can tell the pools that count
#: their calls from those nobody counts (`counted_workers`): weak references, each taken out of
#: the set once its pool is gone.
_COUNTED: set[weakref.ref[CallPool]] = set()


def counted_workers() -> set[threading.Thread] | None:
    """The worker threads of every `CallPool` in the process, or None where they could not be
    read. Each set is copied in one call, which no other thread can interrupt, so a pool that
    starts a worker meanwhile cannot change it under the copy; read again if one does."""
    for _ in range(COUNTED_READS):
        try:
            pools = [pool for ref in list(_COUNTED) if (pool := ref()) is not None]
            return {worker for pool in pools for worker in list(pool._threads)}
        except RuntimeError:  # changed as it was copied: read again
            continue
    return None


#: How many times `counted_workers` reads the pools' sets before it gives up.
COUNTED_READS = 3


@dataclass
class _Waiting:
    """A goal waiting for the meeting it took the slot from to end, and the stop, if any, that
    was asked for meanwhile, which the goal then ends with before it starts."""

    stopped: str | None = None


def _alive(running: Running | None) -> bool:
    return running is not None and running.thread is not None and running.thread.is_alive()


#: Builds one run's loop: (task file, kind, front or None) to an `AgentLoop` ready to run.
LoopFactory = Callable[[GlotFile, str, Any], Any]


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
        #: why the slot takes no run any more, once the service has begun to close (`halt`). Set
        #: without the slot's lock, which a run being made holds for as long as its making takes
        #: (a hung disk under its runs folder): a start that holds the lock reads it again once
        #: its run is made (`_start`)
        self._closed: str | None = None
        #: `halt`'s own, held for a few assignments and never across a wait or a write
        self._halting = threading.Lock()
        #: a goal waiting for the meeting it took the slot from to end, while nothing else starts
        self._waiting: _Waiting | None = None
        #: the pools the runs handed calls to: the run in hand's, and an earlier run's while a
        #: call it left is still under way (`CallPool.busy`)
        self.pools: list[CallPool] = []

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
            if self._closed is not None:
                return f"no meeting: {self._closed}"
            if self._waiting is not None:
                return NO_MEETING_FOR_A_GOAL
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
            started = self._start("meeting", meeting_glot(self.file.meeting.task), None, {peer})
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
        """Start a goal, taking the slot from a meeting. None when it started; otherwise why not.

        A meeting in hand is told to stop and waited for, `MEETING_STOPS_S` at most and without
        the lock, so status, a stop and the tick are answered meanwhile. One that has not ended
        by then refuses the goal: two runs never hold one body."""
        with self._lock:
            if self._closed is not None:
                return self._closed
            if self._waiting is not None:
                return GOAL_WAITING
            meeting = self.running
            thread = meeting.thread if meeting is not None else None
            if meeting is None or thread is None or not thread.is_alive():
                return self._start_goal(text, front, max_steps)
            if meeting.kind == "goal":
                return "a goal is running: roboglot onboard stop ends it"
            self._stop(meeting, FOR_A_GOAL)
            waiting = self._waiting = _Waiting()
        try:
            thread.join(timeout=MEETING_STOPS_S)
        except BaseException:
            with self._lock:
                self._waiting = None
            raise
        with self._lock:
            self._waiting = None
            # no run started in the wait: a robot coming near and a second goal were refused
            why = self._closed or waiting.stopped or (STILL_STOPPING if thread.is_alive() else None)
            if why is not None:
                return why
            return self._start_goal(text, front, max_steps)

    def _start_goal(self, text: str, front: Any, max_steps: int | None) -> str | None:
        """Under the lock, with the slot empty: the goal's run, or why it did not start."""
        glot = glot_from_goal(f"{text.strip()}\n\n{NEARBY_LINE}", self._safe_verbs(), memory=True)
        if max_steps is not None:
            front_matter = glot.frontmatter
            budgets = front_matter.budgets.model_copy(update={"max_steps": max_steps})
            glot = glot.model_copy(
                update={"frontmatter": front_matter.model_copy(update={"budgets": budgets})}
            )
        if not self._start("goal", glot, front, set()):
            return "the goal did not start"
        return None

    def stop(self, why: str = STOPPED) -> bool:
        """Stop the run in hand, a goal or a meeting, and a goal waiting for its meeting to end;
        the slot stays. False when it was empty."""
        with self._lock:
            waiting = self._waiting
            if waiting is not None and waiting.stopped is None:
                waiting.stopped = why
            running = self.running
            if running is None or not _alive(running):
                return waiting is not None
            self._stop(running, why)
            return True

    def stop_and_wait(self, within: float, why: str = STOPPED) -> tuple[bool, bool]:
        """`stop`, then up to `within` seconds, holding no lock, for the run it stopped to end:
        whether there was one to stop, and whether it has ended."""
        with self._lock:
            running = self.running
            was = self.stop(why)
        thread = running.thread if was and running is not None else None
        if thread is not None:
            thread.join(timeout=within)
        return was, thread is None or not thread.is_alive()

    def close(self, why: str) -> None:
        """`halt`, and the rest of the telling done here and now: the run's question taken back,
        and its line said."""
        halted = self.halt(why)
        if halted is not None:
            line, take_back = halted
            if take_back is not None:
                take_back()
            self._log(line)

    def halt(self, why: str) -> tuple[str, Callable[[], None] | None] | None:
        """The service has begun to close: from now on no goal and no meeting starts, whoever
        asks, `why` being the reason given, and the run in hand is told at once: its stop named
        and its abort posted to its own loop. Without the slot's lock, and with no write: a start
        that holds the lock reads `_closed` again once its run is made, and either the start
        sees it or this sees the run (`_start`). Once only: the service's stop and its close
        both halt it. What is left of the telling, which writes, comes back for the caller to do
        bounded: the line to say, and the taking back of a question the run waits on."""
        with self._halting:
            if self._closed is not None:
                return None
            self._closed = why
        waiting = self._waiting
        if waiting is not None and waiting.stopped is None:
            waiting.stopped = why
        running = self.running
        if running is None or not _alive(running):
            return None
        front = running.front
        self._abort(running, why)
        if front is None:
            return f"the {running.kind} is stopping: {why}", None

        def take_back() -> None:
            # a run waiting on its question holds its own loop, where the abort cannot land
            # until it is answered: the question is taken back, as a page's Stop takes it
            with contextlib.suppress(Exception):  # a terminal gone: no question waits on it
                front.questions.close(why)

        return f"the {running.kind} is stopping: {why}", take_back

    def state(self) -> dict[str, Any]:
        """What the slot holds, for `roboglot onboard status`."""
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

    def _start(self, kind: str, glot: GlotFile, front: Any, peers: set[str]) -> bool:
        try:
            agent = self._make_loop(glot, kind, front)
        except Exception as e:  # a run that cannot be built is said and the slot stays empty
            self._log(f"the {kind} could not start: {e}")
            return False
        if self._closed is not None:  # halted while it was made: it does not start
            self._log(f"the {kind} does not start: {self._closed}")
            return False
        running = Running(kind, glot.name, self._clock(), set(peers), agent, front)
        thread = threading.Thread(
            target=self._run, args=(running,), name=f"onboard-{kind}", daemon=True
        )
        running.thread = thread
        self.running = running
        if self._closed is not None:
            # halted between the look above and this run's being the slot's, so the halt did
            # not see it: it starts stopped, as a run stopped before its loop exists does (`_go`)
            running.stop_wanted = running.stop_wanted or self._closed
        thread.start()
        return True

    def _run(self, running: Running) -> None:
        aio = asyncio.new_event_loop()
        # the calls this run hands to threads of their own (`asyncio.to_thread`, a verb's
        # blocking call) go to a pool of its own that counts those under way, so the service's
        # close can tell one that would hold the process at its exit from an idle worker
        pool = CallPool(CALL_THREADS + running.kind, f"the {running.kind}")
        with self._lock:
            self.pools = [kept for kept in self.pools if kept.busy] + [pool]
        aio.set_default_executor(pool)
        running.aio = aio
        front = running.front
        try:
            running.result = aio.run_until_complete(self._go(running))
        except Exception as e:  # the run's own record says the rest
            running.error = str(e)
            self._log(f"the {running.kind} ended with an error: {e}")
        finally:
            running.returned = True
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
        # a run's hooks act only while it is the slot's run: one that is not any more must not
        # change what the slot's run set up, its courtesy above all
        if self._holding is not None:
            with self._lock:
                if self.running is running:
                    self._holding(running.agent)
        try:
            return await running.agent.run()
        finally:
            if self._released is not None:
                with contextlib.suppress(Exception), self._lock:
                    if self.running is running:
                        self._released()
            if front is not None:
                front.unbind()

    def _stop(self, running: Running, why: str) -> None:
        self._abort(running, why)
        if running.front is not None:
            # a run waiting on its question holds its own loop, where the abort cannot land
            # until it is answered: the question is taken back, as a page's Stop takes it
            with contextlib.suppress(Exception):
                running.front.questions.close(why)
        self._log(f"the {running.kind} is stopping: {why}")

    @staticmethod
    def _abort(running: Running, why: str) -> None:
        """The run's stop named and its abort posted to its own loop, with no lock and no write
        but the byte asyncio sends its own loop to wake it, which never waits."""
        # kept first: a run whose loop does not exist yet reads it as it starts (`_go`)
        running.stop_wanted = running.stop_wanted or why
        aio, agent = running.aio, running.agent
        if aio is not None and agent is not None and not aio.is_closed():
            with contextlib.suppress(RuntimeError):  # the loop closed as the stop was sent
                aio.call_soon_threadsafe(_set_abort, agent, why)


def _set_abort(agent: Any, why: str) -> None:
    """On the run's own loop: name the stop, unless something stopped it first, and set the
    abort, which ends the verb in hand and the run, as the kill switch's first press does."""
    if not agent.executor.abort.is_set() and not agent.stop_reason:
        agent.stop_reason = why
    agent.executor.abort.set()
