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

from pydantic import ValidationError

from quackd.answers import DID_IT, GO_AHEAD, WATCHING, confirm_question
from quackd.drive.attached import AttachedBody
from quackd.drive.lanes import (
    BRAKE_WORDS,
    ExactLane,
    Matched,
    answer_word,
    is_brake,
    session_word,
)
from quackd.drive.presets import presets_for
from quackd.duckfile.schema import POLICY_VERB, Budgets, PolicySection, instruction_line
from quackd.latency import spread
from quackd.safety import (
    BudgetExceeded,
    ConfirmDenied,
    Preempted,
    SafetyStop,
    Source,
    VerbNotAllowed,
    VerdictRequired,
)

if TYPE_CHECKING:
    from quackd.agent.decision.base import DecisionLLM
    from quackd.agent.decision.catalogue import DecisionMode
    from quackd.agent.decision.commands import Choice, CommandChooser
    from quackd.agent.providers.base import LLMProvider
    from quackd.agent.providers.catalogue import Price
    from quackd.duckfile.schema import DuckFile, DuckFrontmatter
    from quackd.log import Sink
    from quackd.mcp_server import RobotSession
    from quackd.memory import RobotMemory
    from quackd.verbs.registry import Verb

BRAKE = "the brake"
"""Why the brake ends what it ends, in the words each record says it with."""


@dataclass
class _Answer:
    """The answer one call's confirm gets, given before the call: by a person, by `--yes`, or a
    pipe's no. It answers that call's own confirm once, and nothing after it.

    A verb the call runs inside itself, `approach_and`'s `then`, is answered by the standing
    rule and never by this. A yes to a composite was read as a yes to its nested verb too: it
    cleared a dangerous verb no person had been asked about, and the record wrote down a
    question put to a person that nobody had put."""

    verb: str
    yes: bool
    person: bool
    state: str = "pending"
    """`pending` until the call's confirm reads it, `given` once it has, and `spent` once the
    gate has also asked whether a person gave it, which it asks once, for its record."""
    channel: str | None = None
    """Where the person was asked: the line's channel, `keyboard` or the page's `web`."""


_ANSWER: contextvars.ContextVar[_Answer | None] = contextvars.ContextVar(
    "quackd_drive_answer", default=None
)
"""For the one call this task is running: the answer its confirm gate gets. Set by the session
before the call and read by the executor's `confirm` during it."""


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
        if answer is not None and answer.state == "pending" and name == answer.verb:
            answer.state = "given"
            return answer.yes
        if answer is not None:
            answer.state = "spent"
        # nothing was asked for this call, a nested verb's or a goal's own among them: the
        # standing rule answers, which is no without `--yes`
        return self.answers_as == "--yes"

    def asks_a_person(self) -> bool:
        """Whether a person is giving, or has just given, the answer the gate asks about. The
        gate asks before the answer, for a dangerous verb, and once after it, for its record;
        after that the answer is spent, and a verb the call runs inside it is nobody's."""
        answer = _ANSWER.get()
        if answer is None or not answer.person or answer.state == "spent":
            return False
        if answer.state == "given":
            answer.state = "spent"
        return True

    @property
    def channel(self) -> str | None:
        """Where the person who answered this call was asked, for the gate's record, wherever
        that was not the keyboard: `web`, the page's (`quackd.front`). None for the keyboard,
        so a terminal's records read as they always have, and for an answer nobody gave."""
        answer = _ANSWER.get()
        if answer is None or not answer.person or answer.channel in (None, "keyboard"):
            return None
        return answer.channel


def _go(_why: str) -> bool:
    """`--yes` answers a goal's doubt as it answers a run's: go."""
    return True


_go.answers_as = "--yes"  # type: ignore[attr-defined]
"""What a goal's record names as having said go (`quackd.log.who_answered`), never a person."""


@dataclass(frozen=True)
class GoalSettings:
    """What every goal of a session is flown with, beside its own line and the session's
    contract: the flags `quackd run` takes for one goal, given once for the whole session. The
    defaults are a session started with none of them, whose goals fly as they always have."""

    max_steps: int | None = None
    """`--max-steps`: each goal's steps, within what the session has left of its own."""
    play: bool | None = None
    """`--play`: whether a goal may give its pilot a part to play."""
    price: str | None = None
    """`--price`: what the pilot's model costs, over the catalogue."""
    memory: RobotMemory | None = None
    """`--memory`: the robot's memory, which each goal recalls and adds one fact to. None, the
    default in drive, keeps none."""
    fov_deg: float | None = None
    """`--fov-deg`, which each goal measures through as the session does. The session told the
    body once, as it connected, and a goal never tells it again (`AttachedBody.lens_given`)."""
    decision: DecisionMode = "off"
    """`--decision-mode`: how much a goal's stepper may do. `off` unless the command line said
    otherwise: `--decision-llm` alone is the fast lane, and never a goal's stepper."""
    decision_llm: DecisionLLM | None = None
    """The decision LLM a goal's stepper asks, the fast lane's own client, where a goal has one."""
    decision_price: Price | None = None
    decision_frames: bool = False
    """`--decision-frames`: whether a goal's stepper is shown the camera, and `watch` offered."""
    vla: bool = False
    """`--controller vla`: each goal is one instruction to the arm's learned policy, which a
    scripted pilot hands over and the person driving judges."""
    policy: bool = False
    """Whether the body hands `manipulate` to a policy server (`--policy-url`), which a goal
    then allows behind the confirm gate, as `quackd run --goal` allows it."""


class GoalAsker:
    """One of the questions a goal puts, to the person driving the session, at the keyboard or
    in the page where the goal's line was typed. The goal waits for the answer while the session
    goes on reading lines (`quackd.answers`), and a brake, or a newer line that moves the body,
    ends the goal and takes the question back. Marked for the goal's record as the session's own
    asker is marked.

    Put behind any question already showing and named after its goal, `goal 2: run walk(...)?`:
    a question about the line a person has just typed goes in front of it, so a `y` they mean for
    that line never answers the goal's (`terminal.Asker`)."""

    def __init__(
        self,
        ask: Callable[..., Awaitable[bool]],
        kind: str,
        words: Callable[..., tuple[str, str | None]],
        *,
        label: str,
    ) -> None:
        self._ask = ask
        self._kind = kind
        self._words = words
        self._label = label
        self.asks_a_person: Any = getattr(ask, "asks_a_person", False)
        self.channel: str | None = getattr(ask, "channel", None)

    async def __call__(self, *args: Any) -> bool:
        question, detail = self._words(*args)
        said = await self._ask(
            question, detail=detail, kind=self._kind, behind=True, label=self._label
        )
        return bool(said)


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


def _confirm_question(matched: Matched, verb: Verb) -> str | None:
    """The question the confirm gate's record quotes for this call (`prompt`), word for word, so
    a person is asked what the record says they were asked. None where the arguments do not fit
    the verb, which the gate refuses before it asks anybody anything."""
    try:
        parsed = verb.params.model_validate(matched.arguments)
    except ValidationError:
        return None
    return confirm_question(matched.verb, parsed.model_dump())


def _denied(matched: Matched, answer: _Answer | None, error: Exception) -> str:
    """Why a call its confirm gate denied did not run. The gate's own words say a human
    declined, which nobody did where nobody was asked: a pipe, or a verb the call runs inside
    itself, which drive cannot stop to ask about."""
    if answer is not None and answer.person and not answer.yes:
        return f"{matched.label}: a person said no"
    return (
        f"{matched.label}: nobody was asked for the yes it needs, so it was answered no "
        "(a pipe, or a verb run inside another; --yes answers yes)"
    )


class DriveSession:
    """One connected robot, driven one line at a time.

    `record(kind, **data)` writes the session's own record, `transcript.jsonl` under the
    session's directory. `ask(question)` puts a yes-or-no question to the person at the keyboard
    and returns their answer, or is None where nobody can be asked, a pipe among them, where a
    question is answered no. `pilot(goal)` hands over the pilot a goal is flown by: the scripted
    one is built afresh for each goal, since it picks its strategy from the goal's own words, and
    a vendor's is the one client for every goal. `goals` is where each goal's own run directory
    goes, and `settings` what every goal is flown with beside its line (`GoalSettings`)."""

    def __init__(
        self,
        robot: RobotSession,
        *,
        record: Callable[..., None],
        chooser: CommandChooser | None = None,
        pilot: Callable[[str], LLMProvider] | None = None,
        goals: Path | None = None,
        goal_verbs: Sequence[str] = (),
        ask: Callable[..., Awaitable[bool]] | None = None,
        yes: bool = False,
        clock: Callable[[], float] = time.perf_counter,
        goal_view: Callable[[int], Sink | None] | None = None,
        goal_frames: Callable[[Any, str], None] | None = None,
        settings: GoalSettings | None = None,
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
        self.goal_view = goal_view
        """A reader for each goal's own record as it is written, given the goal's number: the
        page's (`quackd.front`). None, at a terminal, leaves a goal's record to its transcript."""
        self.goal_frames = goal_frames
        """Where a goal's camera frames go as they are taken, for the page as `goal_view` is."""
        self.settings = settings or GoalSettings()
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
        self._watching = False
        """Whether a body that cannot see a fall has a person watching it, by their own yes or
        by `--yes`'s (`_watched`)."""
        self._prepared = False
        self._fall_blind: str | None = None
        self.exact = self._exact()

    async def prepare(self) -> None:
        """Read once, before the first line, what the session has to know about the body before
        anything moves it (`_watched`), so the first command that moves it waits on no state
        read of its own. A session nobody prepared reads it at its first motion."""
        from quackd.agent.loop import fall_blind_warning

        state = await self.robot.transport.get_state()
        self._fall_blind = fall_blind_warning(
            self.robot.executor.registry, list(self.exact.verbs), state
        )
        self._prepared = True

    async def _watched(self, cmd: Command) -> str | None:
        """Why a command that moves the body may not start: on a body that cannot see a fall
        and cannot get up, nobody has said they are watching it. Asked once, before the first
        thing that moves it, as `quackd run` asks before its first leg moves; a no refuses the
        command and the next one asks again. `--yes` answers yes, and a pipe, which nobody
        answers for, is refused. A typed walk and a goal each walked such a body unasked."""
        if self._watching:
            return None
        if not self._prepared:
            await self.prepare()
        warning = self._fall_blind
        if warning is None or self.yes:
            self._watching = True
            return None
        if cmd.by != "person" or self.ask is None:
            return f"{warning} Nobody here can say they are watching it: pass --yes to say so."
        watching = await self._asked(WATCHING, detail=warning, kind="acknowledge")
        self.record(
            "prompt",
            what="acknowledge",
            question=warning,
            answer=watching,
            # where the person was asked, wherever it was not the keyboard: the page's `web`
            **({"channel": cmd.channel} if cmd.channel != "keyboard" else {}),
        )
        if not watching:
            return "nobody confirmed they were watching a robot that cannot see a fall"
        self._watching = True
        return None

    def _exact(self) -> ExactLane:
        executor = self.robot.executor
        registry = executor.registry
        # by `is_allowed`, which reads an alias as the verb it names: a `.duck` allowing `walk`
        # allows `move`, and matching the names alone left `move` and every preset out
        verbs = {
            name: registry.get(name)
            for name in registry.names()
            if self.robot.duck is None or executor.is_allowed(name)
        }
        self.presets = presets_for(executor.manifest, verbs)
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
        braking = is_brake(text)
        if braking:
            # before anything is written: the brake waits on nothing, the record included
            self.brake(text.strip() or "Enter", channel=channel, by=by)
        self.record("command", n=cmd.n, text=text, by=by, channel=channel)
        if braking:
            self._route(cmd, Routed("brake", label=text.strip() or "Enter"))
            self._end(cmd, Ended("braked", "the brake"))
            return cmd
        if (word := session_word(text)) is not None:
            # the session's own, answered at once: nothing is routed, run or dropped for it, and
            # a `quit` has its answer before the front end that read it closes the session
            self._route(cmd, Routed("session", label=word))
            self._end(cmd, Ended("ok", self._session_says(word)))
            return cmd
        if answer_word(text) is not None:
            # a yes or a no with no question waiting for it answers nothing: it was flown as a
            # goal, which ended whatever a y pressed twice had just said yes to
            self._route(cmd, Routed("session", label=text.strip().casefold()))
            self._end(cmd, Ended("ok", "no question is waiting for an answer"))
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
            # settled before the record is written, so a record that cannot be written, a disk
            # gone full, leaves no front end and no newer command waiting on this one for good
            cmd.routed.set_result(routed)
            self.record("routed", n=cmd.n, **routed.record())

    def _end(self, cmd: Command, ended: Ended) -> None:
        if not cmd.ended.done():
            self.stats.outcomes[ended.outcome] = self.stats.outcomes.get(ended.outcome, 0) + 1
            cmd.ended.set_result(ended)
            self.record("ended", n=cmd.n, outcome=ended.outcome, summary=ended.summary)

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
            # the question as it was answered: its confidence, floor and gate, with the person's
            # yes or no to it in the `routed` row after, are what the floors are measured by
            self.record("fast", n=cmd.n, **choice.record)
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

    async def _asked(
        self, question: str, *, detail: str | None = None, kind: str = "drive"
    ) -> bool:
        if self.ask is None:
            return False
        return bool(await self.ask(question, detail=detail, kind=kind))

    # ── running ──

    def _chooser_name(self) -> str:
        return str(getattr(getattr(self.chooser, "llm", None), "name", None) or "the decision LLM")

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
        if self._moves(matched.verb) and (refusal := await self._watched(cmd)) is not None:
            routed.lane, routed.note = "refused", refusal
            self._route(cmd, routed)
            self._end(cmd, Ended("refused", refusal))
            return
        answer: _Answer | None = None
        if executor.needs_confirm(verb):
            # `--yes` answers by its standing rule; a person at the keyboard is asked, in the
            # words the gate's record will quote; and a pipe without `--yes` is answered no,
            # since drive reads a pipe's lines as commands and never as answers, where
            # `quackd run` takes the pipe's next line for one
            question = _confirm_question(matched, verb)
            if self.yes:
                answer = _Answer(matched.verb, yes=True, person=False)
            elif cmd.by == "person" and self.ask is not None and question is not None:
                said = await self._asked(question)
                answer = _Answer(matched.verb, yes=said, person=True, channel=cmd.channel)
            else:
                answer = _Answer(matched.verb, yes=False, person=False)
        self._route(cmd, routed)
        self._routing.discard(cmd)
        if self._moves(matched.verb):
            await self._take_over(cmd)
        cmd.started_at = self.clock()
        to_start = round((cmd.started_at - cmd.at) * 1000, 3)
        self.stats.to_start_ms.setdefault(lane, []).append(to_start)
        self.record("started", n=cmd.n, verb=matched.verb, to_start_ms=to_start)
        # A call the fast lane took without asking is the decision LLM's reading of what the
        # person typed, and the verdict gate's record says so; one the person said yes to, as
        # one they typed outright, is theirs.
        picked_by = self._chooser_name() if lane == "fast" and gate == "taken" else None
        token = _ANSWER.set(answer)
        try:
            result = await executor.run_verb(
                matched.verb, matched.arguments, source=cmd.by, picked_by=picked_by
            )
            ended = Ended("ok" if result.ok else "fail", result.summary)
        except Preempted as e:
            ended = Ended("preempted", str(e))
        except ConfirmDenied as e:
            ended = Ended("denied", _denied(matched, answer, e))
        except (VerdictRequired, VerbNotAllowed) as e:
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
        session's gates out let the pilot run a verb the `.duck` asks a yes for without one.

        And `manipulate`, where the body hands it to a policy server and the session allows it:
        no safe verb, so a goal allows it behind the gate, as `quackd run --goal` does
        (`duck_from_goal`), or a goal could never use the policy the session was given."""
        executor = self.robot.executor
        registry = executor.registry
        gated: list[str] = []
        duck = self.robot.duck
        if duck is not None:
            confirmed = {registry.canonical(name) for name in duck.frontmatter.verbs.confirm}
            gated = [name for name in self.goal_verbs if registry.canonical(name) in confirmed]
        if (
            self.settings.policy
            and POLICY_VERB in registry
            and executor.is_allowed(POLICY_VERB)
            and POLICY_VERB not in gated
        ):
            gated.append(POLICY_VERB)
        return gated

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
        # and a brake still on its way. Its `stop` verb runs after the stop it sends the body,
        # and is a stop beside whatever has begun meanwhile: it ended the walk typed after it.
        while pending := [brake for brake in self._brakes if not brake.done()]:
            await asyncio.wait(pending)
        self._mover = cmd

    def _goal_refusal(self) -> str | None:
        """Why no goal may start now, for a reason that refuses a typed call too: the session's
        abort, or its own budget spent."""
        executor = self.robot.executor
        if executor.abort.is_set():
            return "the session was aborted, and refuses every call but stop"
        budget = executor.budget
        if budget is not None:
            try:
                budget.check()
            except BudgetExceeded as e:
                return f"the session's budget is spent: {e}"
            if budget.limits.max_minutes * 60 - budget.elapsed_s <= 0:
                return f"the session's budget is spent: max_minutes ({budget.limits.max_minutes:g})"
        return None

    def _goal_duck(self, text: str) -> DuckFile:
        """A goal's contract: the one `quackd run --goal` writes from its line, held inside the
        session's. Its safe verbs are behind the session's confirm gates, its budgets are no
        more than the session has left of its own, and its abort rules are the session's, the
        goal's own kept only where the session has none of that kind. Without this a goal
        walked where the session's own budget or battery rule had stopped a typed call."""
        from quackd.duckfile.parser import duck_from_goal

        duck = duck_from_goal(
            text,
            self.goal_verbs or ["stop"],
            confirm=self._gated_for_goals(),
            memory=self.settings.memory is not None,
        )
        front = duck.frontmatter
        updates: dict[str, Any] = {"budgets": self._goal_budgets(front.budgets)}
        session = self.robot.duck
        mine = session.frontmatter if session is not None else None
        if mine is not None:
            rules = list(mine.abort_when)
            for rule in front.abort_when:
                one = front.model_copy(update={"abort_when": [rule]})
                battery = one.battery_abort_percent is not None
                repeat = one.repeat_failure_abort is not None
                if (battery and mine.battery_abort_percent is not None) or (
                    repeat and mine.repeat_failure_abort is not None
                ):
                    continue
                if rule not in rules:
                    rules.append(rule)
            updates["abort_when"] = rules
        if (policy := self._goal_policy(mine)) is not None:
            updates["policy"] = policy
        return duck.model_copy(update={"frontmatter": front.model_copy(update=updates)})

    def _goal_policy(self, session: DuckFrontmatter | None) -> PolicySection | None:
        """What a goal holds `manipulate` to: the session `.duck`'s own `policy:` section, whose
        listed instructions are the only words the arm's policy may be told, as a typed
        `manipulate` and `quackd run` hold it, and its seconds cut to what the session has left.
        A goal held to its own contract's defaults took any words at all, and was given its own
        two minutes whatever the session had spent. None, for a goal held to the defaults, where
        the session has no section and has a goal's seconds left: every goal of a session started
        bare, whose record reads as it did."""
        policy = session.policy if session is not None else None
        held = policy if policy is not None else PolicySection()
        left = self._policy_left()
        if left is not None and left < held.total_s:
            # by `model_copy`, which validates nothing: what is left may be less than a segment,
            # and the last segment may run past the total by its own seconds, as a run's may
            return held.model_copy(update={"total_s": max(left, 0.0)})
        return policy

    def _goal_budgets(self, defaults: Budgets) -> Budgets:
        """A goal's own budgets, `quackd run --goal`'s with `--max-steps` over its steps, cut to
        what the session has left."""
        steps = self.settings.max_steps
        if steps is None:
            steps = defaults.max_steps
        budget = self.robot.executor.budget
        if budget is None:
            return defaults.model_copy(update={"max_steps": steps})
        limits = budget.limits
        return Budgets(
            max_steps=min(steps, limits.max_steps - budget.steps),
            max_minutes=min(defaults.max_minutes, limits.max_minutes - budget.elapsed_s / 60),
            max_llm_calls=min(defaults.max_llm_calls, limits.max_llm_calls - budget.llm_calls),
        )

    def _policy_left(self) -> float | None:
        """The seconds of policy segments the session has left for its goals, or None for a
        session with no budget."""
        budget = self.robot.executor.budget
        return None if budget is None else budget.policy_total_s - budget.policy_s

    def _charge(self, goal: Any) -> None:
        """A goal's steps and model calls are the session's as well, so the session's own
        budgets bound every goal together rather than each one alone. So are the seconds a
        policy drove the body, which are the body's, as a second task file loaded into a session
        keeps them (`RobotSession.adopt`)."""
        spent, mine = goal.executor.budget, self.robot.executor.budget
        if spent is not None and mine is not None:
            mine.steps += spent.steps
            mine.llm_calls += spent.llm_calls
            mine.policy_s += spent.policy_s

    def _vla_refusal(self, text: str) -> str | None:
        """Why the session's VLA pilot cannot take this line, where it flies its goals with one:
        the arm's policy is told the line word for word, so it is held to the rule every
        instruction is (`instruction_line`) and to the session `.duck`'s list where it has one,
        and it needs policy seconds the session has left."""
        if not self.settings.vla:
            return None
        try:
            line = instruction_line(text)
        except ValueError as e:
            return (
                "--controller vla tells the arm's policy each line word for word, as its one "
                f"instruction, and {e}"
            )
        duck = self.robot.duck
        listed = duck.frontmatter.effective_policy.instructions if duck is not None else []
        if duck is not None and listed and line not in listed:
            return (
                "--controller vla tells the arm's policy each line word for word, and "
                f"{duck.name} lists the only instructions it may be told: "
                + ", ".join(repr(each) for each in listed)
            )
        budget = self.robot.executor.budget
        if budget is not None and budget.policy_s >= budget.policy_total_s:
            return (
                f"the session's policy seconds are spent: {budget.policy_s:.1f} s of "
                f"{budget.policy_total_s:g} s have run"
            )
        return None

    def _goal_answerers(self, cmd: Command, number: int) -> tuple[Any, Any, Any]:
        """What answers a goal's confirm gate, the pilot's doubt, and, for a pilot that cannot
        tell, whether the arm did it.

        The person who typed the goal, at the keyboard or in the page, as `quackd run` asks the
        person at its terminal: the goal waits for the answer, and the session goes on reading
        lines. `--yes` answers as it answers a run: yes to the gate and go to the doubt. A goal
        nobody can be asked about, a pipe's, keeps the standing rule a session always gave it:
        no to the gate, and the doubt left to the pilot. Nobody judges either, and a VLA pilot,
        which needs somebody to, is refused both before the session starts."""
        executor = self.robot.executor
        if self.yes:
            return executor.confirm, _go, None
        if cmd.by != "person" or self.ask is None:
            return executor.confirm, None, None
        ask, label = self.ask, f"goal {number}"
        return (
            GoalAsker(
                ask,
                "confirm",
                lambda name, params: (confirm_question(name, params), None),
                label=label,
            ),
            GoalAsker(ask, "decide", lambda why: (GO_AHEAD, why), label=label),
            GoalAsker(ask, "judge", lambda why: (DID_IT, why), label=label)
            if self.settings.vla
            else None,
        )

    async def _run_goal(self, cmd: Command, *, lane: str) -> None:
        from quackd.agent.loop import AgentLoop, RunConfig

        if self.pilot is None or self.goals is None:
            self._route(cmd, Routed("pilot", note="no pilot was named"))
            self._end(cmd, Ended("refused", "this session has no pilot to hand a goal to"))
            return
        refusal = self._goal_refusal() or self._vla_refusal(cmd.text) or await self._watched(cmd)
        if refusal is not None:
            self._route(cmd, Routed(lane, note=refusal))
            self._end(cmd, Ended("refused", refusal))
            return
        executor = self.robot.executor
        manifest = executor.manifest
        if manifest is None:
            raise RuntimeError("a goal needs the body's manifest, and the session has none")
        number = next(self._goal_count)
        run_dir = self.goals / f"{number:02d}-{_slug(cmd.text)}"
        self._route(cmd, Routed(lane, label=f"goal {number}", note=str(run_dir.name)))
        self._routing.discard(cmd)
        await self._take_over(cmd)
        # Built once whatever it took over from has ended, and been charged for what it spent,
        # which `_charge` is before that command's `ended` settles: a goal typed over a goal was
        # given the steps and policy seconds the first had spent and not yet been charged for,
        # and ran past the session's own.
        refusal = self._goal_refusal() or self._vla_refusal(cmd.text)
        if refusal is not None:
            if self._mover is cmd:
                self._mover = None
            self._end(cmd, Ended("refused", refusal))
            return
        try:
            run_dir.mkdir(parents=True, exist_ok=True)
            confirm, decide, judge = self._goal_answerers(cmd, number)
            settings = self.settings
            loop = AgentLoop(
                RunConfig(
                    duck=self._goal_duck(cmd.text),
                    provider=self.pilot(cmd.text),
                    transport=AttachedBody(self.robot.transport, manifest),
                    detector=self.robot.detector,
                    run_dir=run_dir,
                    runs_dir=self.goals,
                    confirm=confirm,
                    # a session started with --dry-run sends nothing, a goal's verbs included:
                    # the goal's own executor ran every verb for real on the body it holds
                    dry_run=executor.dry_run,
                    view=self.goal_view(number) if self.goal_view is not None else None,
                    on_frame=self.goal_frames,
                    memory=settings.memory,
                    fov_deg=settings.fov_deg,
                    decide=decide,
                    judge=judge,
                    decision=settings.decision,
                    decision_llm=settings.decision_llm,
                    decision_price=settings.decision_price,
                    price=settings.price,
                    play=settings.play,
                    decision_frames=settings.decision_frames,
                    # the session's own places, so one marked between two goals is there for
                    # the second, and what a goal marks or forgets is kept where the session
                    # keeps a memory: without one a goal changes a copy that lasts as long as it
                    # does, as `quackd run --no-memory` keeps its own places
                    places=executor.places
                    if settings.memory is not None
                    else executor.places.held_copy(),
                )
            )
        except BaseException:
            if self._mover is cmd:
                self._mover = None
            raise
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
        except Exception as e:  # a pilot that raised: the goal ends, and its record says how
            ended = Ended("error", f"{type(e).__name__}: {e}")
        finally:
            watch.cancel()
            self._charge(loop)
            if self._goal is loop:
                self._goal = None
            if self._mover is cmd:
                self._mover = None
        self.record("goal_end", n=cmd.n, goal=number, outcome=ended.outcome, reason=ended.summary)
        self._end(cmd, ended)
