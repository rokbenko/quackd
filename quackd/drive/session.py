"""A drive session: one robot, one command at a time, from a person at the keyboard or a pipe.

Every line goes through the lanes in order, and the first that takes it wins: the brake, the
session's own words, the exact lane, the fast lane where the session has a decision LLM, and the
pilot. The executor runs every call with every gate it has, and a person's own command clears
the verdict gate for its own call and nothing else (`quackd.safety.PERSONS`). One mover at a
time: a command that moves the body ends whatever it finds moving it (`Executor.preempt`), a goal
included, while a read runs alongside. The brake ends everything at once and waits on none of it.
"""

from __future__ import annotations

import asyncio
import contextlib
import contextvars
import functools
import itertools
import re
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from quackd.drive.attached import AttachedBody
from quackd.drive.lanes import BRAKE_WORDS, ExactLane, Matched, is_brake, session_word
from quackd.drive.presets import presets_for
from quackd.latency import spread
from quackd.safety import (
    ConfirmDenied,
    Preempted,
    SafetyStop,
    Source,
    VerbNotAllowed,
    VerdictRequired,
)

if TYPE_CHECKING:
    from quackd.agent.decision.commands import Choice, CommandChooser
    from quackd.agent.providers.base import LLMProvider
    from quackd.mcp_server import RobotSession

BRAKE = "the brake"
"""Why the brake ends what it ends, in the words each record says it with."""

_ANSWER: contextvars.ContextVar[tuple[bool, bool] | None] = contextvars.ContextVar(
    "quackd_drive_answer", default=None
)
"""For the one call this task is running: the answer its confirm gate gets, and whether a person
gave it. Set by the session before the call and read by the executor's `confirm` during it."""


class Answered:
    """The executor's `confirm` in a drive session.

    The executor asks its confirm gate synchronously, and a drive session cannot block the loop
    that reads the keyboard on a question. So the session asks first, asynchronously and in the
    executor's own words, and hands the answer to the call it is about through `_ANSWER`, which
    this reads. A person answered only where `asks_a_person` says one did, which is what the
    record names (`log.who_answered`); `--yes` answers by its standing rule and says so."""

    def __init__(self, *, yes: bool) -> None:
        self.answers_as = "--yes" if yes else None

    def __call__(self, name: str, params: dict[str, Any]) -> bool:
        answer = _ANSWER.get()
        if answer is not None:
            return answer[0]
        # nothing was asked for this call, a goal's own among them: the standing rule answers
        return self.answers_as == "--yes"

    def asks_a_person(self) -> bool:
        answer = _ANSWER.get()
        return bool(answer is not None and answer[1])


@dataclass
class Routed:
    """Which lane took a line, and the call it came to."""

    lane: str
    label: str = ""
    verb: str | None = None
    arguments: dict[str, Any] = field(default_factory=dict)
    preset: str | None = None
    gate: str | None = None
    latency_ms: float = 0.0
    note: str = ""

    def record(self) -> dict[str, Any]:
        out: dict[str, Any] = {"lane": self.lane, "latency_ms": self.latency_ms}
        for name in ("label", "verb", "preset", "gate", "note"):
            if value := getattr(self, name):
                out[name] = value
        if self.verb is not None:
            out["arguments"] = dict(self.arguments)
        return out


@dataclass
class Ended:
    outcome: str
    summary: str = ""


@dataclass(eq=False)
class Command:
    """One line, from the moment it arrived: `routed` once a lane has taken it, and `ended` once
    whatever it came to is over. Both always settle, a line the brake or a newer line dropped
    included, so a front end can wait on them without a timeout."""

    n: int
    text: str
    by: Source
    channel: str
    at: float
    routed: asyncio.Future[Routed]
    ended: asyncio.Future[Ended]
    task: asyncio.Task[None] | None = None
    dropped_by: str | None = None
    """Why a line that never started was dropped, in the words its record gives."""
    decided_at: float | None = None
    """When a lane took the line, where a question was put after it: the latency the record gives
    runs to here, and never includes the time a person took to answer."""
    started_at: float | None = None
    moved_at: float | None = None


@dataclass
class Stats:
    """The seconds a person waits, per lane, for the summary's `drive` block."""

    to_start_ms: dict[str, list[float]] = field(default_factory=dict)
    to_motion_ms: dict[str, list[float]] = field(default_factory=dict)
    brake_ms: list[float] = field(default_factory=list)
    lanes: dict[str, int] = field(default_factory=dict)
    outcomes: dict[str, int] = field(default_factory=dict)

    def block(self) -> dict[str, Any]:
        def per_lane(values: dict[str, list[float]]) -> dict[str, Any]:
            return {
                lane: {"n": len(seen), **(spread(seen, digits=1) or {})}
                for lane, seen in sorted(values.items())
            }

        return {
            "lanes": dict(sorted(self.lanes.items())),
            "outcomes": dict(sorted(self.outcomes.items())),
            "to_start_ms": per_lane(self.to_start_ms),
            "to_motion_ms": per_lane(self.to_motion_ms),
            "brake_ms": {"n": len(self.brake_ms), **(spread(self.brake_ms, digits=1) or {})},
        }


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.casefold()).strip("-")[:40] or "goal"


class DriveSession:
    """One connected robot, driven one line at a time.

    `record(kind, **data)` writes the session's own record, `transcript.jsonl` under the
    session's directory. `ask(question)` puts a yes-or-no question to the person at the keyboard
    and returns their answer, or is None where nobody can be asked, a pipe among them, where a
    question is answered no. `pilot(goal)` builds the pilot a goal is flown by, once per goal,
    since the scripted one picks its strategy from the goal's own words, and `goals` is where
    each goal's own run directory goes."""

    def __init__(
        self,
        robot: RobotSession,
        *,
        record: Callable[..., None],
        chooser: CommandChooser | None = None,
        pilot: Callable[[str], LLMProvider] | None = None,
        goals: Path | None = None,
        goal_verbs: Sequence[str] = (),
        ask: Callable[[str], Awaitable[bool]] | None = None,
        yes: bool = False,
        clock: Callable[[], float] = time.perf_counter,
    ) -> None:
        self.robot = robot
        self.record = record
        self.chooser = chooser
        self.pilot = pilot
        self.goals = goals
        self.goal_verbs = list(goal_verbs)
        self.ask = ask
        self.yes = yes
        self.clock = clock
        self.stats = Stats()
        self._count = itertools.count(1)
        self._goal_count = itertools.count(1)
        self._routing: set[Command] = set()
        self._movers: list[Command] = []
        """Every command holding the body or waiting its turn for it, oldest first."""
        self._mover: Command | None = None
        self._goal: Any = None
        self._tasks: set[asyncio.Task[None]] = set()
        self._brakes: list[asyncio.Task[None]] = []
        self._closed = False
        self.exact = self._exact()

    def _exact(self) -> ExactLane:
        registry = self.robot.executor.registry
        allowed = set(self.robot.executor.allowed) if self.robot.duck is not None else None
        verbs = {
            name: registry.get(name)
            for name in registry.names()
            if allowed is None or name in allowed
        }
        self.presets = presets_for(self.robot.executor.manifest, verbs)
        return ExactLane(verbs, self.presets)

    # ── the front ends' seam ──

    def submit(self, text: str, *, by: Source = "person", channel: str = "keyboard") -> Command:
        """Take one line now and return at once: routing and running go on in a task, and the
        command's `routed` and `ended` settle when each is over. A brake line brakes before this
        returns, and a newer line drops an older one still being routed."""
        loop = asyncio.get_running_loop()
        cmd = Command(
            next(self._count),
            text,
            by,
            channel,
            self.clock(),
            loop.create_future(),
            loop.create_future(),
        )
        self.record("command", n=cmd.n, text=text, by=by, channel=channel)
        if is_brake(text):
            self.brake(text.strip() or "Enter", channel=channel, by=by)
            self._route(cmd, Routed("brake", label=text.strip() or "Enter"))
            self._end(cmd, Ended("braked", "the brake"))
            return cmd
        if (word := session_word(text)) is not None:
            # the session's own, answered at once: nothing is routed, run or dropped for it, and
            # a `quit` has its answer before the front end that read it closes the session
            self._route(cmd, Routed("session", label=word))
            self._end(cmd, Ended("ok", self._session_says(word)))
            return cmd
        for older in list(self._routing):
            self._drop(older, "a newer line came first")
        self._routing.add(cmd)
        cmd.task = asyncio.ensure_future(self._handle(cmd))
        self._tasks.add(cmd.task)
        cmd.task.add_done_callback(functools.partial(self._done, cmd))
        return cmd

    def _drop(self, cmd: Command, why: str) -> None:
        """Drop a line that has not started, in `why`'s words."""
        if cmd.task is not None and not cmd.task.done():
            cmd.dropped_by = cmd.dropped_by or why
            cmd.task.cancel()

    def _done(self, cmd: Command, task: asyncio.Task[None]) -> None:
        """Settle a command whose task is over. One cancelled before its first step never ran
        `_handle` at all, so nothing else would settle it, and a front end waiting on it would
        wait for good."""
        self._tasks.discard(task)
        self._routing.discard(cmd)
        self._route(cmd, Routed("dropped"))
        self._end(cmd, Ended("dropped", cmd.dropped_by or "it was cancelled"))

    def brake(self, why: str, *, channel: str = "keyboard", by: Source = "person") -> None:
        """Stop everything, now, waiting on none of it: every line that has not started, still
        being routed, asked about or waiting its turn for the body, is dropped, every verb moving
        the body is ended (`preempt`), a goal is stopped, and the stop goes to the body directly,
        ahead of the `stop` verb, which reads the body's state first and is run after it for the
        record, in the name of whoever braked."""
        at = self.clock()
        self._drop_unstarted(BRAKE)
        self.robot.executor.preempt(f"{BRAKE}, {why}")
        self._stop_goal(f"{BRAKE}, {why}")
        self._brakes.append(asyncio.ensure_future(self._stop(at, why, channel, by)))

    def _drop_unstarted(self, why: str) -> None:
        """Drop every line that has not started: still being routed, asked about, or waiting
        its turn for the body behind a command still being stopped. One left waiting would start
        once that command had stopped, after the brake that was meant to end it too."""
        for cmd in [*self._routing, *self._movers]:
            if cmd.started_at is None:
                self._drop(cmd, why)

    async def _stop(self, at: float, why: str, channel: str, by: Source) -> None:
        with contextlib.suppress(Exception):
            await self.robot.executor.logged_transport().stop()
        ms = round((self.clock() - at) * 1000, 1)
        self.stats.brake_ms.append(ms)
        self.record("brake", why=why, channel=channel, by=by, brake_ms=ms)
        with contextlib.suppress(Exception):
            await self.robot.executor.run_verb("stop", source=by)

    def doing(self) -> str:
        """What the robot is doing now, in a few words, for the fast lane's question."""
        if self._goal is not None:
            return "working on a goal"
        if self._mover is not None:
            return f"running {self._mover.text!r}"
        return "standing by for a command"

    async def drain(self) -> None:
        """Wait for every command and every brake still going, a read running beside a walk
        among them."""
        tasks = [*self._tasks, *self._brakes]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def close(self, why: str = "the session ended") -> None:
        """End every command still going as the session closes, and wait for each: nothing that
        has not started runs, a verb moving the body is ended and a goal stopped, so a `quit`
        typed over a walk stops the walk rather than waiting it out. A read still going is
        waited for. A second call only waits."""
        if not self._closed:
            self._closed = True
            self._drop_unstarted(why)
            self.robot.executor.preempt(why)
            self._stop_goal(why)
        await self.drain()

    # ── routing ──

    def _route(self, cmd: Command, routed: Routed) -> None:
        if not cmd.routed.done():
            decided = cmd.decided_at if cmd.decided_at is not None else self.clock()
            routed.latency_ms = round((decided - cmd.at) * 1000, 3)
            self.stats.lanes[routed.lane] = self.stats.lanes.get(routed.lane, 0) + 1
            self.record("routed", n=cmd.n, **routed.record())
            cmd.routed.set_result(routed)

    def _end(self, cmd: Command, ended: Ended) -> None:
        if not cmd.ended.done():
            self.stats.outcomes[ended.outcome] = self.stats.outcomes.get(ended.outcome, 0) + 1
            self.record("ended", n=cmd.n, outcome=ended.outcome, summary=ended.summary)
            cmd.ended.set_result(ended)

    async def _handle(self, cmd: Command) -> None:
        try:
            await self._dispatch(cmd)
        except asyncio.CancelledError:
            # a newer line, the brake or the session's end dropped this one before it started
            self._route(cmd, Routed("dropped"))
            self._end(cmd, Ended("dropped", cmd.dropped_by or "it was cancelled"))
        except Exception as e:  # a command never ends the session
            self._route(cmd, Routed("error", note=f"{type(e).__name__}: {e}"))
            self._end(cmd, Ended("error", f"{type(e).__name__}: {e}"))
        finally:
            self._routing.discard(cmd)

    async def _dispatch(self, cmd: Command) -> None:
        if (matched := self.exact.parse(cmd.text)) is not None:
            await self._matched(cmd, matched, "exact")
            return
        if self.chooser is not None:
            choice = await self.chooser.choose(cmd.text, self.doing())
            if await self._chosen(cmd, choice):
                return
        await self._run_goal(cmd, lane="pilot")

    def _session_says(self, word: str) -> str:
        if word == "help":
            return (
                "type a verb of this body's, a preset ("
                + ", ".join(self.presets)
                + "), or a goal in your own words; Enter, "
                + ", ".join(w for w in BRAKE_WORDS if w)
                + " brake"
            )
        if word == "status":
            return self.doing()
        return "goodbye"

    async def _chosen(self, cmd: Command, choice: Choice) -> bool:
        """What the fast lane's answer comes to. True where it settled the line, False where the
        pilot takes it."""
        cmd.decided_at = self.clock()
        gate = choice.gate
        if gate in ("taken", "ask") and choice.offer is not None:
            offer = choice.offer
            matched = Matched(offer.verb, dict(offer.arguments), offer.label, preset=offer.preset)
            if gate == "ask" and not await self._asked(f"did you mean {offer.label}?"):
                self._route(cmd, Routed("fast", label=offer.label, gate=gate, note="declined"))
                self._end(cmd, Ended("declined", f"not {offer.label}"))
                return True
            await self._matched(cmd, matched, "fast", gate=gate)
            return True
        if gate == "unclear":
            self._route(cmd, Routed("fast", gate=gate))
            self._end(cmd, Ended("unclear", "say it another way"))
            return True
        if gate == "pilot_ask" and not await self._asked("should the pilot take this on?"):
            self._route(cmd, Routed("fast", gate=gate, note="declined"))
            self._end(cmd, Ended("declined", "not for the pilot"))
            return True
        return False

    async def _asked(self, question: str) -> bool:
        if self.ask is None:
            return False
        return bool(await self.ask(question))

    # ── running ──

    def _moves(self, name: str) -> bool:
        executor = self.robot.executor
        verb = executor.registry.get(name)
        return executor._may_move(executor.registry.canonical(name), verb)

    async def _matched(self, cmd: Command, matched: Matched, lane: str, gate: str = "") -> None:
        if cmd.decided_at is None:
            cmd.decided_at = self.clock()
        routed = Routed(
            lane,
            label=matched.label,
            verb=matched.verb,
            arguments=dict(matched.arguments),
            preset=matched.preset,
            gate=gate or None,
        )
        if matched.refused is not None:
            routed.lane, routed.note = "refused", matched.refused
            self._route(cmd, routed)
            self._end(cmd, Ended("refused", matched.refused))
            return
        executor = self.robot.executor
        verb = executor.registry.get(matched.verb)
        answer: tuple[bool, bool] | None = None
        if executor.needs_confirm(verb):
            # `--yes` answers by its standing rule; a person at the keyboard is asked; and a pipe
            # without `--yes` is answered no, since drive reads a pipe's lines as commands and
            # never as answers, where `quackd run` takes the pipe's next line for one
            if self.yes:
                answer = (True, False)
            elif cmd.by == "person" and self.ask is not None:
                said = await self._asked(f"{matched.label} needs a yes before it runs: run it?")
                answer = (said, True)
            else:
                answer = (False, False)
        self._route(cmd, routed)
        self._routing.discard(cmd)
        if self._moves(matched.verb):
            await self._take_over(cmd)
        cmd.started_at = self.clock()
        to_start = round((cmd.started_at - cmd.at) * 1000, 3)
        self.stats.to_start_ms.setdefault(lane, []).append(to_start)
        self.record("started", n=cmd.n, verb=matched.verb, to_start_ms=to_start)
        token = _ANSWER.set(answer)
        try:
            result = await executor.run_verb(matched.verb, matched.arguments, source=cmd.by)
            ended = Ended("ok" if result.ok else "fail", result.summary)
        except Preempted as e:
            ended = Ended("preempted", str(e))
        except (VerdictRequired, VerbNotAllowed, ConfirmDenied) as e:
            ended = Ended("refused", str(e))
        except SafetyStop as e:
            ended = Ended(e.outcome, str(e))
        finally:
            _ANSWER.reset(token)
            if self._mover is cmd:
                self._mover = None
        if cmd.moved_at is not None:
            to_motion = round((cmd.moved_at - cmd.at) * 1000, 3)
            self.stats.to_motion_ms.setdefault(lane, []).append(to_motion)
        self._end(cmd, ended)

    def saw_intent(self, kind: str, accepted: bool) -> None:
        """The first accepted intent other than a stop after a command started is when the body
        moved for it, `to_motion_ms` (an observer of the executor's own `intent` events)."""
        cmd = self._mover
        if cmd is not None and cmd.moved_at is None and accepted and kind != "stop":
            cmd.moved_at = self.clock()

    def _stop_goal(self, why: str, goal: Any = None) -> None:
        goal = goal if goal is not None else self._goal
        if goal is not None:
            goal.stop_reason = why
            goal.executor.abort.set()

    async def _end_on_abort(self, goal: Any) -> None:
        """End a goal when the session is aborted, its heartbeat failing above all. The goal's
        own heartbeat watches nothing (`AttachedBody`), since the session's watches the link,
        and a goal nobody told went on sending its verbs down a link already known to be gone."""
        await self.robot.executor.abort.wait()
        failure = getattr(self.robot.heartbeat, "failure", None)
        why = f"the session's heartbeat failed: {failure}" if failure else "the session was aborted"
        self._stop_goal(why, goal)

    def _gated_for_goals(self) -> list[str]:
        """The verbs a goal runs behind a confirm: those the session's `.duck` gates, among the
        goal's own. A goal is a contract of its own, written from its line, and one that left the
        session's gates out let the pilot run a verb the `.duck` asks a yes for without one."""
        duck = self.robot.duck
        if duck is None:
            return []
        registry = self.robot.executor.registry
        gated = {registry.canonical(name) for name in duck.frontmatter.verbs.confirm}
        return [name for name in self.goal_verbs if registry.canonical(name) in gated]

    async def _take_over(self, cmd: Command) -> None:
        """Make `cmd` the one mover: whatever is moving the body, a verb or a goal, is ended for
        it and waited for, so the stop that ends it reaches the body before this command's first
        twist rather than after it. A command still waiting its own turn is dropped, so two
        commands that come while a third is being stopped never both start once it has."""
        earlier = [old for old in self._movers if not old.ended.done()]
        self._movers = [*earlier, cmd]
        if earlier:
            why = f"a newer command, {cmd.text}"
            for old in earlier:
                if old.started_at is None:
                    self._drop(old, why)
            self.robot.executor.preempt(why)
            self._stop_goal(why)
            await asyncio.wait({old.ended for old in earlier})
        self._mover = cmd

    async def _run_goal(self, cmd: Command, *, lane: str) -> None:
        from quackd.agent.loop import AgentLoop, RunConfig
        from quackd.duckfile.parser import duck_from_goal

        if self.pilot is None or self.goals is None:
            self._route(cmd, Routed("pilot", note="no pilot was named"))
            self._end(cmd, Ended("refused", "this session has no pilot to hand a goal to"))
            return
        executor = self.robot.executor
        number = next(self._goal_count)
        run_dir = self.goals / f"{number:02d}-{_slug(cmd.text)}"
        run_dir.mkdir(parents=True, exist_ok=True)
        manifest = executor.manifest
        if manifest is None:
            raise RuntimeError("a goal needs the body's manifest, and the session has none")
        loop = AgentLoop(
            RunConfig(
                duck=duck_from_goal(
                    cmd.text, self.goal_verbs or ["stop"], confirm=self._gated_for_goals()
                ),
                provider=self.pilot(cmd.text),
                transport=AttachedBody(self.robot.transport, manifest),
                detector=self.robot.detector,
                run_dir=run_dir,
                runs_dir=self.goals,
                confirm=executor.confirm,
            )
        )
        self._route(cmd, Routed(lane, label=f"goal {number}", note=str(run_dir.name)))
        self._routing.discard(cmd)
        await self._take_over(cmd)
        self._goal = loop
        cmd.started_at = self.clock()
        to_start = round((cmd.started_at - cmd.at) * 1000, 3)
        self.stats.to_start_ms.setdefault(lane, []).append(to_start)
        self.record("goal_start", n=cmd.n, goal=number, text=cmd.text, dir=run_dir.name)
        watch = asyncio.ensure_future(self._end_on_abort(loop))
        try:
            result = await loop.run()
            ended = Ended(result.outcome, result.reason)
        except SafetyStop as e:
            ended = Ended(e.outcome, str(e))
        finally:
            watch.cancel()
            if self._goal is loop:
                self._goal = None
            if self._mover is cmd:
                self._mover = None
        self.record("goal_end", n=cmd.n, goal=number, outcome=ended.outcome, reason=ended.summary)
        self._end(cmd, ended)
