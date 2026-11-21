"""A team of pilots: one `AgentLoop` per body, on wall-clock time, talking over the bus.

There is no referee. Each member is a
real `AgentLoop` with its own provider, executor, allowlist, budget, heartbeat, memory and
feasibility verdict, running at the same time as the others, on any backend, and the only
thing that coordinates them is what they say to each other (ADR-0034).

Three consequences worth saying out loud, because they are the cost of that:

- **Nothing is reproducible.** There is no shared clock, so no two runs are the same.
- **Nothing is checked against the world.** There is no one world to ask, so the outcome is
  the members' own claims, which is exactly as true as a solo run's.
- **N mock members are N separate bodies.** Two `microduck:mock` pilots cannot see each
  other.

What this does guarantee: every member ends, every transport closes, a member that raises
stops the others with a reason that names it, and no body moves before every member whose body
cannot see a fall has been answered yes (`_Together`).
"""

from __future__ import annotations

import asyncio
import contextlib
import functools
import inspect
import time
import uuid
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from roboglot import __version__
from roboglot.adapters.base import AdapterError, go_to_rest_if_any
from roboglot.adapters.factory import (
    RobotSpec,
    describe,
    make_adapter,
    needs_rest_pose,
    parse_robot_spec,
)
from roboglot.adapters.manifest import RobotManifest
from roboglot.agent.loop import (
    LOCOMOTION,
    AgentLoop,
    Outcome,
    RunConfig,
    RunResult,
    resolve_play,
)
from roboglot.agent.providers.base import LLMProvider, Usage
from roboglot.agent.transcript import new_run_dir, run_label
from roboglot.command import command_line
from roboglot.glotfile.schema import GlotFile, GlotFrontmatter
from roboglot.log import EventLog, Sink, a_person_was_asked
from roboglot.memory import RobotMemory
from roboglot.safety import Aborted, allow_all, deny_all
from roboglot.team.bus import Bus, InProcessBus
from roboglot.team.messages import TeamMessage
from roboglot.team.talk import TEAM_SRC, Peer, make_links, notice
from roboglot.team.transcript import TeamTranscript
from roboglot.verbs.aliases import canonical

BusFactory = Callable[[Callable[[TeamMessage], None]], Bus]
"""`bus_factory(tap) -> Bus`: the seam for `MqttBus`; the in-process bus is the default."""

ViewFactory = Callable[[str], "Sink | None"]
"""`view(name) -> Sink | None`: a view per member, by member name, and one more for
`TEAM_LOG`: the team's own events, those of no single member. Return a
distinct sink per name, or two members' intent bursts fold into one line."""

TEAM_LOG = "team"


def member_specs(
    members: list[str],
    robots: dict[str, str] | None,
    glot_default: str | dict[str, str] | None,
) -> dict[str, RobotSpec]:
    """Each member's robot: `--robots`, else the task file's `robots:`, else this machine's.

    Member by member, so a command line that names one member's body (`--robot NAME`, a
    registered robot called after it) leaves the others to the task file. It used to take the
    file's whole list or none of it, and sent a member the command line did not name to the
    machine's answer. A team is N separate bodies of whatever kind, and the machine's own answer
    to "which robot, then" is `default_spec()`: the one adapter installed if there is one, and
    otherwise a refusal that says what to install or lists what is here.

    A robot given for a name that is no member is refused, before anything is built. It was
    passed over, so a slip in a name left the member it was meant for to the machine's answer:
    on a machine with one adapter, that adapter's mock."""
    given: dict[str, str] = {}
    if isinstance(glot_default, dict):
        given = dict(glot_default)
    elif isinstance(glot_default, str):
        given = dict.fromkeys(members, glot_default)
    given.update(robots or {})
    stray = [f"{name}={text}" for name, text in given.items() if name not in members]
    if stray:
        raise AdapterError(
            f"{', '.join(stray)} {'names' if len(stray) == 1 else 'name'} no member of this "
            f"team, whose members are {', '.join(members)}"
        )
    specs: dict[str, RobotSpec] = {}
    chosen: RobotSpec | None = None
    for name in members:
        if (named := given.get(name)) is not None:
            parsed = parse_robot_spec(named)
        else:
            from roboglot.adapters.factory import default_spec

            chosen = chosen or default_spec()  # asked once: the answer cannot differ per member
            parsed = chosen
        specs[name] = RobotSpec(parsed.adapter, parsed.backend, name)
    return specs


MIN_MEMBERS = 2
MAX_MEMBERS = 8
"""`roboglot.glotfile.schema.PILOTS_MAX_MEMBERS`, spelled here because this is where a roster
that did not come from a task file is bounded."""

WORST_FIRST: tuple[Outcome, ...] = ("error", "aborted", "infeasible", "budget", "failure")
"""How a team's outcome is chosen when its members disagree, worst first.

`error` outranks `aborted` because of what the pair usually means together: one member raised
and the rest were stopped *because it did*, so the aborts are the consequence and the error is
the cause. A team that reported `aborted` there would read as though somebody had pressed
something. When a person really does press something, every member aborts and nothing errors,
so `aborted` is still what that says."""


def _standing_go(_why: str) -> bool:
    """A team member's answer to its pilot's doubt under `--yes`: go, asked of nobody."""
    return True


_standing_go.answers_as = "a team's standing answer"  # type: ignore[attr-defined]
"""What the record names as having said go (`roboglot.log.who_answered`), and never a person."""

Acknowledge = Callable[[str, str], bool | Awaitable[bool]]
"""`acknowledge(member, warning)`: whether the person is watching a member whose body cannot see
a fall (`roboglot.agent.loop.fall_blind_warning`), at once or later from a coroutine, as a solo
run's `acknowledge` answers."""


def _nobody_watching(_member: str, _why: str) -> bool:
    """The answer about a body that cannot see a fall where nobody is there to ask: nobody said
    they watch it, so the team ends before anything moves, as a solo run that nobody answers
    does. Unmarked, so the record names no person."""
    return False


def _can_walk(contract: GlotFrontmatter) -> bool:
    """Whether this member's part of the task can make its body walk: the half of
    `fall_blind_warning` that is known before the body connects. The other half, whether the body
    can see a fall, is in its first reading, so a member this is True of may be asked nothing."""
    return any(v in LOCOMOTION or canonical(v) in LOCOMOTION for v in contract.verbs.allow)


class _Together:
    """A team's start: no body moves until every member whose part can make it walk has read
    its body and, where that body cannot see a fall, been answered yes.

    Members connect at once, and only a body's first reading says whether it can see a fall, so
    every member waits for each one whose part can make it walk, and nobody waits for one whose
    part cannot. Their questions are put one at a time (`turn`), and after a no, or a question
    nobody answered, no other is put: that member's end stops the team (`run_pilot_team`), which
    takes back every question still waiting and lets go of every member waiting here, before
    anything moved. Asked while the other members moved, a question took the `q` meant to stop
    them for its answer, and the bodies went on moving until somebody pressed Enter."""

    def __init__(self, walkers: Iterable[str]) -> None:
        self.turn = asyncio.Lock()
        """Held by the member whose question is up, so two are never up at once."""
        self.refused: str | None = None
        """The member whose question was answered no, or not at all."""
        self._waiting = set(walkers)
        self._clear = asyncio.Event()
        if not self._waiting:
            self._clear.set()

    async def hold(self, name: str, abort: asyncio.Event) -> None:
        """`RunConfig.wait_for_team` for `name`, which has read its body and had its answer: the
        team waits for it no longer, and it waits for the others, or for its own stop."""
        self._waiting.discard(name)
        if not self._waiting:
            self._clear.set()
        if self._clear.is_set() or abort.is_set():
            return
        from roboglot.release import cancel_and_wait

        clear = asyncio.ensure_future(self._clear.wait())
        stopped = asyncio.ensure_future(abort.wait())
        try:
            await asyncio.wait({clear, stopped}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            await cancel_and_wait(clear, stopped)


class _Asked:
    """A member's `acknowledge`: the team's question about its body, put when the team's turn
    comes to it, with the member's name, and never after a no."""

    def __init__(self, ask: Acknowledge, member: str, together: _Together) -> None:
        self._ask = ask
        self.member = member
        self._together = together

    async def __call__(self, why: str) -> bool:
        together = self._together
        async with together.turn:
            if together.refused is not None:
                # Never put. The no ends its own member, whose end stops every member still
                # running, and the stop takes this back (`roboglot.answers.answered`).
                await asyncio.Event().wait()
            try:
                said = self._ask(self.member, why)
                answer = bool(await said) if inspect.isawaitable(said) else bool(said)
            except asyncio.CancelledError:
                raise  # taken back by the stop, which is nobody's answer: the stop says why
            except BaseException:
                together.refused = self.member  # nobody answered, which ends it as a no does
                raise
            if not answer:
                together.refused = self.member
            return answer

    def asks_a_person(self) -> bool:
        """Whatever the person's own question says, at the moment of asking."""
        return a_person_was_asked(self._ask)

    @property
    def channel(self) -> str | None:
        """Where the person was asked, wherever their question names a channel."""
        channel = getattr(self._ask, "channel", None)
        return channel if isinstance(channel, str) else None


class RosterEntry(Protocol):
    """What this needs of a registered robot (`roboglot.registry.RobotEntry`).

    Structural, so a team can also be run from `--robots` with no registry in sight."""

    name: str
    provider: str | None
    llm_id: str | None

    @property
    def robot_spec(self) -> RobotSpec: ...

    @property
    def memory_key(self) -> str: ...

    def adapter_kwargs(self) -> dict[str, Any]: ...


Roster = Mapping[str, RosterEntry]


@dataclass(frozen=True)
class SpecEntry:
    """A member that came from `--robots` or a task file's `robots:` rather than the registry.

    It has no endpoint and no pilot of its own, because nothing wrote either down."""

    name: str
    spec: RobotSpec
    llm: str | None = None
    """The pilot it would fly under, in the shape `--llm` takes. Always None here, and the
    field exists so a roster of these reads the same way a roster of registered robots does:
    the run asks every member what pilot it remembers, and this one remembers nothing."""

    @property
    def robot_spec(self) -> RobotSpec:
        return self.spec

    @property
    def memory_key(self) -> str:
        return self.spec.key

    def adapter_kwargs(self) -> dict[str, Any]:
        return {}


def roster_from_specs(specs: Mapping[str, RobotSpec]) -> dict[str, SpecEntry]:
    """`member_specs(...)` turned into a roster."""
    return {name: SpecEntry(name, spec) for name, spec in specs.items()}


@dataclass
class PilotTeamResult:
    outcome: Outcome
    reason: str
    run_dir: Path
    members: list[str]
    per_member: dict[str, dict[str, Any]] = field(default_factory=dict)
    messages: int = 0
    """TALKs the members sent each other. The runner's own notices are counted apart."""
    notices: int = 0
    usage: Usage = field(default_factory=Usage)
    steps: int = 0
    llm_calls: int = 0
    wall_elapsed_s: float = 0.0
    cost_usd: float | None = None
    """What the whole team cost, or None the moment one member could not be priced: a team
    bill quietly missing a robot is worse than no team bill."""
    log_dropped: int = 0

    @property
    def ok(self) -> bool:
        return self.outcome == "success"


# ── the contract, per body ──────────────────────────────────────────────────────────────


def trim_contract(fm: GlotFrontmatter, manifest: RobotManifest) -> GlotFrontmatter:
    """This task file as it applies to this body.

    A team's task file allows what the team as a whole needs, which for any one member is
    too much: `AgentLoop.run` refuses to start when a robot does not provide something the
    contract *requires*, so an arm in a kicking team would be turned away at the door for not
    being able to walk. The union is checked once, by the runner, before anything connects
    (`validate_glot(..., team=True)`); each member is then handed the part of it its own
    manifest can answer for.

    Rebuilt through the model rather than copied, so a trimmed contract is a contract: every
    cross-field rule in the schema still holds for it."""
    allow = [v for v in fm.verbs.allow if manifest.provides(v)]
    if not allow:
        # `stop` is in every manifest and can never be gated, so there is always something
        allow = ["stop"]
    kept = {canonical(v) for v in allow}
    data = fm.model_dump()
    data["verbs"] = {
        "allow": allow,
        "confirm": [v for v in fm.verbs.confirm if canonical(v) in kept],
    }
    data["requires"] = [v for v in fm.requires if manifest.provides(v)]
    return GlotFrontmatter.model_validate(data)


def aggregate_outcome(
    per_member: Mapping[str, tuple[Outcome, str]], *, first: str | None = None
) -> tuple[Outcome, str]:
    """One outcome for the team, from what each member said about itself.

    Success only when everyone declared it: a team in which one member never finished did not
    finish. Otherwise the worst outcome wins and the reason names every member that did not
    succeed, worst first, so the first thing read is the thing that went most wrong. Of members
    that ended alike, `first`, the member whose own end stopped the team, comes before the
    members it stopped: a person's no ends its member `aborted`, as every stop it causes ends the
    others, and the reason opened on whichever of them the roster listed first."""
    if not per_member:
        return "error", "the team had no members"
    failed = {name: pair for name, pair in per_member.items() if pair[0] != "success"}
    if not failed:
        return "success", f"every member declared success: {', '.join(per_member)}"
    worst = next(o for o in WORST_FIRST if any(pair[0] == o for pair in failed.values()))
    ranked = sorted(failed.items(), key=lambda kv: (WORST_FIRST.index(kv[1][0]), kv[0] != first))
    return worst, "; ".join(f"{name} {outcome}: {why}" for name, (outcome, why) in ranked)


# ── the run ─────────────────────────────────────────────────────────────────────────────


async def run_pilot_team(
    glot: GlotFile,
    roster: Roster,
    *,
    providers: Mapping[str, LLMProvider],
    runs_dir: str | Path = "runs",
    dry_run: bool = False,
    max_steps: int | None = None,
    yes: bool = False,
    memories: Mapping[str, RobotMemory] | None = None,
    fov_deg: float | None = None,
    log: Callable[[str], None] = lambda _m: None,
    bus_factory: BusFactory | None = None,
    view: ViewFactory | None = None,
    on_run_dir: Callable[[Path], None] | None = None,
    abort: asyncio.Event | None = None,
    team_name: str | None = None,
    run_name: str | None = None,
    price: str | None = None,
    before_connect: Callable[[Sequence[RobotSpec]], None] | None = None,
    play: bool | None = None,
    acknowledge: Acknowledge | None = None,
) -> PilotTeamResult:
    """One pilot per member, all at once, until every one of them has ended.

    `acknowledge(member, warning)` is put once for each member whose body cannot see a fall, on
    a task that can make it walk, as `roboglot run` asks a solo run's question, and no body moves
    until each such member has been answered yes: one question at a time, and a no, or no answer,
    ends the team before anything moved. With no `acknowledge` nobody is asked, and such a member
    is answered as nobody watching, which ends the team the same way. `yes` is the team's
    standing answer to its confirm gates and its pilots' doubts, and never answers this:
    `roboglot run --yes` hands in an `acknowledge` that says yes for every member at once.

    `before_connect` is handed every member's robot, in roster order, once all of them are
    built. Building is the last thing that can refuse the team before its members connect, and
    they all start to connect together, so whatever a caller says there is said of bodies that
    are about to connect, every one of them: `roboglot run` says there which members are on a
    backend their adapter is retiring (`cli._deprecated_backends`). A team refused before then,
    by its contract or by a body that could not be built, never calls it.

    `play` is `--play` or `--no-play` over the task file's own `play`, for every member at
    once, and None is the file's word."""
    members = list(roster)
    if not MIN_MEMBERS <= len(members) <= MAX_MEMBERS:
        raise ValueError(
            f"a pilot team needs {MIN_MEMBERS} to {MAX_MEMBERS} members; this one names "
            f"{len(members)}"
        )
    missing = [name for name in members if name not in providers]
    if missing:
        raise ValueError(f"no pilot for {', '.join(missing)}")

    manifests = {name: describe(entry.robot_spec) for name, entry in roster.items()}
    problems = union_problems(glot, manifests)
    if problems:
        where = ", ".join(f"{name}={roster[name].robot_spec.key}" for name in members)
        raise ValueError(f"{glot.name} cannot run on {where}: {'; '.join(problems)}")
    contracts = {name: trim_contract(glot.frontmatter, manifests[name]) for name in members}
    # A real arm with no rest pose has nothing to be folded to when its member ends, and would
    # be left holding itself up wherever it stopped (ADR-0058). `roboglot run` refuses such a member
    # before anything is built; this is the same rule for a caller that reaches the team
    # without it, read off each member's own kwargs, since a roster is structural.
    for name, entry in roster.items():
        if needs_rest_pose(entry.robot_spec) and entry.adapter_kwargs().get("rest_pose") is None:
            key = entry.robot_spec.key
            # a member from a spec has no registry entry to keep a pose under, so recording one
            # for its name would change nothing: it is sent to register the arm instead
            said, way_out = (
                (
                    f"{name} ({key}) names no registered arm, so it has no rest pose",
                    f"roboglot robot add NAME {key} --address PORT, then roboglot robot rest-pose "
                    "NAME, and run the team from the registry with --team",
                )
                if isinstance(entry, SpecEntry)
                else (
                    f"{name} ({key}) has no rest pose recorded",
                    f"roboglot robot rest-pose {name}",
                )
            )
            raise ValueError(
                f"{said}, so at the end of a team's run there is nothing to fold it to, and it "
                f"would be left holding itself up wherever it stopped: {way_out}"
            )

    # built before the run directory exists, so a missing extra or a bad address fails with
    # nothing on disk to explain away
    adapters = {
        name: make_adapter(entry.robot_spec, **entry.adapter_kwargs())
        for name, entry in roster.items()
    }
    if before_connect is not None:
        before_connect([roster[name].robot_spec for name in members])

    stem = glot.name if glot.name.startswith("team") else f"team-{glot.name}"
    run_dir = new_run_dir(runs_dir, stem, run_label(run_name) if run_name else None)
    if on_run_dir is not None:
        # The first moment there is somewhere to write: the CLI's terminal capture
        # has been buffering since before this call and moves into the directory
        # here, before any member opens a file of its own.
        on_run_dir(run_dir)
    wall0 = time.perf_counter()

    def now() -> float:
        return time.perf_counter() - wall0

    talks = {"members": 0, "notices": 0}

    def tap(msg: Any) -> None:
        transcript.on_bus(msg)
        if getattr(msg, "kind", None) == "TALK":
            talks["notices" if msg.src == TEAM_SRC else "members"] += 1

    # built before `team.jsonl` is opened, so a factory that raises leaves nothing open: it
    # opens nothing itself, and the tap reads the transcript only once a message comes
    bus: Bus = bus_factory(tap) if bus_factory else InProcessBus(tap=tap)
    transcript = TeamTranscript(run_dir, now)
    loops: dict[str, AgentLoop] = {}
    # From here to `team_start`, `team.jsonl`, the bus and each member's transcript are open
    # before the `try` whose `finally` closes them: a broker that refused the bus, or a member
    # that could not be built, left them open, and an MQTT bus's thread running, for the rest
    # of the process.
    try:
        if callable(start := getattr(bus, "start", None)):
            start()  # inside the event loop, so remote deliveries are marshalled onto it

        task_id = uuid.uuid4().hex[:8]
        peers = {name: Peer(name, roster[name].robot_spec.key, manifests[name]) for name in members}
        links = make_links(members, peers, bus=bus, task_id=task_id, now=now)

        def member_view(name: str) -> Sink | None:
            return view(name) if view is not None else None

        # no record: every TALK is already in team.jsonl through the tap, under its own name
        story = EventLog(observers=[v] if (v := member_view(TEAM_LOG)) is not None else [])

        # Every member waits, before it moves, for each member whose part can make it walk to
        # have read its body and had any question about it answered, and where no part can,
        # nobody waits
        walkers = [name for name in members if _can_walk(contracts[name])]
        together = _Together(walkers)
        for name in members:
            member_dir = run_dir / "robots" / name
            member_dir.mkdir(parents=True, exist_ok=True)  # `Transcript` opens, it does not mkdir
            loops[name] = AgentLoop(
                RunConfig(
                    glot=GlotFile(frontmatter=contracts[name], body=glot.body, path=glot.path),
                    provider=providers[name],
                    transport=adapters[name],
                    run_dir=member_dir,
                    runs_dir=runs_dir,
                    max_steps=max_steps,
                    dry_run=dry_run,
                    # N blocking prompts would starve every other member's heartbeat, so the
                    # answer for all of them is `--yes` or nothing, exactly as it is over MCP
                    confirm=allow_all if yes else deny_all,
                    # except whether anybody watches a body that cannot see a fall, which is
                    # asked before anything moves, while no heartbeat has started (`_Together`)
                    acknowledge=_Asked(
                        acknowledge if acknowledge is not None else _nobody_watching,
                        name,
                        together,
                    ),
                    wait_for_team=functools.partial(together.hold, name) if walkers else None,
                    decide=_standing_go if yes else None,
                    memory=(memories or {}).get(name),
                    fov_deg=fov_deg,
                    log=lambda m, who=name: log(f"{who}: {m}"),
                    view=member_view(name),
                    link=links[name],
                    # every member is priced the same way, because `--price` is one rate for
                    # the run rather than one per robot: a team of the same LLM on three
                    # bodies is three bills at one rate
                    price=price,
                    summary_file=False,
                    play=play,
                )
            )

        played, played_from = resolve_play(glot.frontmatter.play, play)
        transcript.write(
            "team_start",
            glot=glot.name,
            team=team_name,
            method="pilots",
            clock="wall",
            members=members,
            robots={name: roster[name].robot_spec.key for name in members},
            providers={
                name: {"provider": providers[name].name, "llm_id": providers[name].llm_id}
                for name in members
            },
            contracts={
                name: {
                    "allow": contracts[name].verbs.allow,
                    "confirm": contracts[name].verbs.confirm,
                    "requires": contracts[name].requires,
                }
                for name in members
            },
            dry_run=dry_run,
            contract=glot.frontmatter.model_dump(),
            # as each member's `run_start` says it, so the team's own record agrees with them
            # when `--play` or `--no-play` overrode the file
            play={"on": played, "from": played_from},
        )
    except BaseException as failed:
        for unclosed in _close_unstarted(transcript, bus, loops):
            failed.add_note(f"and {unclosed} could not be closed")
        raise

    results: dict[str, RunResult] = {}
    ended: set[str] = set()
    stopped_by: list[str] = []
    """The member whose own end stopped the team, once one has."""
    master = abort if abort is not None else asyncio.Event()

    def fan_out(reason: str | None) -> None:
        """Stop every member that is still running, and tell it why if it was not a person."""
        for name, loop in loops.items():
            if reason and name not in ended:
                links[name].abort_reason = reason
            loop.executor.abort.set()

    async def watch() -> None:
        await master.wait()
        fan_out(None)  # a person pressed something: the loop's own wording is right

    watcher = asyncio.create_task(watch(), name="roboglot-pilots-abort")

    def _ended(name: str, outcome: Outcome, reason: str) -> RunResult:
        """What a member that did not return a result of its own is recorded as."""
        loop = loops[name]
        return RunResult(
            outcome=outcome,
            reason=reason,
            steps=loop.budget.steps,
            llm_calls=loop.budget.llm_calls,
            usage=loop.usage,
            run_dir=loop.run_dir,
            log_dropped=loop.event_log.dropped,
            # The loop's own teardown built this before it re-raised, so a member that died
            # still reports the wall clock, the LLM seconds and the bill it had already
            # measured. Without it a team of fully priced LLMs read `cost_usd: null` on
            # the strength of one member being interrupted.
            summary=loop.summary,
        )

    async def member(name: str) -> None:
        """One member, start to finish, recorded whatever it did.

        Swallows every `Exception`, so a team ends when every member has ended however each
        one ended, and one that raised does not take the `gather` down with it. A
        `CancelledError` is re-raised, because a cancellation is not this member's failure to
        report: the run is over. It is still recorded first, so the teardown below has a
        result to write rather than a `KeyError` in place of the interrupt."""
        loop = loops[name]
        try:
            results[name] = await loop.run()
        except asyncio.CancelledError:
            # a second Ctrl-C: `asyncio.run` cancels every task, and this one is not an error
            # to blame on the member. Recorded before the re-raise, because the `finally` below
            # reads it and a KeyError there would replace the interrupt with a crash.
            results[name] = _ended(name, "aborted", "interrupted")
            raise
        except Exception as e:
            # A run that ends before its first step raises, and `Aborted` is what it raises, its
            # own end written first, for a person's no, a question nobody answered, and a stop
            # that came while it waited to move. The team says so as the member's record does,
            # rather than call a person's answer a fault. Any other raise is the member's error.
            stopped = str(e) if isinstance(e, Aborted) else None
            results[name] = (
                _ended(name, "aborted", stopped)
                if stopped is not None
                else _ended(name, "error", f"{type(e).__name__}: {e}")
            )
            # `AgentLoop.run` connects before its own try/finally, so a transport that failed
            # to connect, or one whose vocabulary refused the contract, is still open here
            with contextlib.suppress(Exception):
                loop.transcript.close()
            if not dry_run:
                # the same window as the close below, for the same reason: a member that died
                # in the connect never reached the rest move `AgentLoop.run`'s finally does
                with contextlib.suppress(Exception):
                    await go_to_rest_if_any(adapters[name])
            with contextlib.suppress(Exception):
                await adapters[name].close()
            if not master.is_set():
                stopped_by.append(name)
                fan_out(
                    f"stopped by the team: {name} ended aborted: {stopped}"
                    if stopped is not None
                    else f"stopped by the team: {name} raised {type(e).__name__}: {e}"
                )
                master.set()
        finally:
            ended.add(name)
            links[name].close()
            result = results.setdefault(name, _ended(name, "error", "ended without a result"))
            transcript.write(
                "member_end",
                robot=name,
                outcome=result.outcome,
                reason=result.reason,
                steps=result.steps,
                llm_calls=result.llm_calls,
            )
            text = (
                f"{name} declared success: {result.reason}"
                if result.outcome == "success"
                else f"{name} ended {result.outcome}: {result.reason}"
            )
            # the survivors learn it in their next observation rather than waiting on somebody
            # who has stopped
            sent = notice(bus, task_id=task_id, t=now(), text=text)
            story.emit("talk", src=TEAM_SRC, to="all", text=sent.text, ok=True, summary=text)

    try:
        # gather rather than a TaskGroup: a sibling cancelled by a TaskGroup ends as a bare
        # CancelledError, while the fan-out above ends it through its own executor's abort
        # gate, which cancels the verb, sends a stop and records a reason that names the culprit
        await asyncio.gather(*(member(name) for name in members))
    finally:
        watcher.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await watcher
        for name in members:
            if name not in results:  # cancelled before its own task could record anything
                results[name] = _ended(name, "aborted", "interrupted")
        result = _finish(
            glot=glot,
            members=members,
            roster=roster,
            providers=providers,
            results=results,
            talks=talks,
            bus=bus,
            transcript=transcript,
            story=story,
            loops=loops,
            run_dir=run_dir,
            wall_elapsed_s=round(time.perf_counter() - wall0, 2),
            dry_run=dry_run,
            team_name=team_name,
            run_name=run_name,
            first=stopped_by[0] if stopped_by else None,
        )
    return result


ADVISORY_FIELDS = ("verbs.allow",)
"""Problems a pilot team answers by trimming rather than by refusing.

`verbs.allow` naming a verb no member provides is the validator's weaker line: a task file may
allow more than it needs, and for a solo run `AgentLoop` drops the extras with a note. A pilot
team does the same thing per member (`trim_contract`), so refusing here would turn "the duck
cannot move joints and the arm cannot say" into a reason two robots may not work together,
which is the opposite of the point. `requires`, which is what the task actually needs, is
still checked against the union and still refuses."""


def union_problems(glot: GlotFile, manifests: Mapping[str, RobotManifest]) -> list[str]:
    """What no body in this team can do. Checked before anything connects."""
    from roboglot.glotfile.validate import validate_glot

    return [
        p.message
        for p in validate_glot(glot, list(manifests.values()), team=True)
        if p.field not in ADVISORY_FIELDS
    ]


def _close_unstarted(
    transcript: TeamTranscript, bus: Bus, loops: Mapping[str, AgentLoop]
) -> list[str]:
    """What a team that failed before its members started had opened, closed: each member's
    transcript built so far, the bus, and `team.jsonl`. A close that raises does not stop the
    others, nor take the place of the failure that brought the team here, which is what the
    caller is owed: each is handed back in words, for the caller to note on that failure."""
    unclosed: list[str] = []
    closes: list[tuple[str, Callable[[], Any]]] = [
        (f"{name}'s transcript", loop.transcript.close) for name, loop in loops.items()
    ]
    if callable(close := getattr(bus, "close", None)):
        closes.append(("the bus", close))
    closes.append(("team.jsonl", transcript.close))
    for what, closing in closes:
        try:
            closing()
        except Exception as e:
            unclosed.append(f"{what} ({type(e).__name__}: {e})")
    return unclosed


def _finish(
    *,
    glot: GlotFile,
    members: Sequence[str],
    roster: Roster,
    providers: Mapping[str, LLMProvider],
    results: Mapping[str, RunResult],
    talks: Mapping[str, int],
    bus: Bus,
    transcript: TeamTranscript,
    story: EventLog,
    loops: Mapping[str, AgentLoop],
    run_dir: Path,
    wall_elapsed_s: float,
    dry_run: bool,
    team_name: str | None,
    run_name: str | None = None,
    first: str | None = None,
) -> PilotTeamResult:
    """The rollup, the summary and the teardown. Runs whatever happened to the members. `first`
    is the member whose own end stopped the team, if one did (`aggregate_outcome`)."""
    outcome, reason = aggregate_outcome(
        {name: (results[name].outcome, results[name].reason) for name in members}, first=first
    )
    per_member = {
        name: {
            "outcome": results[name].outcome,
            "reason": results[name].reason,
            "steps": results[name].steps,
            "llm_calls": results[name].llm_calls,
            "usage": results[name].usage.model_dump(),
            # off the member's own summary, which `_ended` recovers from the loop for a member
            # that raised rather than returned
            "cost_usd": results[name].summary.get("cost_usd"),
            "wall_s": results[name].summary.get("wall_s"),
            "llm_latency_s": results[name].summary.get("llm_latency_s"),
            # the member's tries asked again and its waits, only where it waited (ADR-0059)
            **{
                key: results[name].summary[key]
                for key in ("llm_retries", "llm_retry_wait_s")
                if key in results[name].summary
            },
            "provider": providers[name].name,
            "llm_id": providers[name].llm_id,
            "robot": roster[name].robot_spec.key,
            "run_dir": f"robots/{name}",
            "log_dropped": results[name].log_dropped,
        }
        for name in members
    }
    usage = Usage()
    for name in members:
        usage = usage + results[name].usage
    # None the moment ONE member could not be priced, rather than a total that quietly leaves
    # that member out: a team bill missing a robot is worse than no team bill.
    costs: list[float | None] = [results[name].summary.get("cost_usd") for name in members]
    total_cost = None if any(c is None for c in costs) else round(sum(c or 0.0 for c in costs), 6)
    backends = {roster[name].robot_spec.backend for name in members}
    log_dropped = story.dropped + sum(loops[name].event_log.dropped for name in members)
    summary: dict[str, Any] = {
        "glot": glot.name,
        "outcome": outcome,
        "reason": reason,
        "team": {"members": list(members), "method": "pilots", "name": team_name},
        "run_name": run_name,
        # What was asked for, beside what happened. A team root writes no `run_start`,
        # so without this the argv would be in the terminal file and nowhere a reader
        # can parse, and on a deterministic team in no record at all.
        "command": command_line(),
        "version": __version__,
        "robots": {name: roster[name].robot_spec.key for name in members},
        "messages": talks["members"],
        "notices": talks["notices"],
        "bus_messages": getattr(bus, "published", 0),
        "usage": usage.model_dump(),
        "cost_usd": total_cost,
        "steps": sum(results[name].steps for name in members),
        "llm_calls": sum(results[name].llm_calls for name in members),
        "wall_elapsed_s": wall_elapsed_s,
        "transport": backends.pop() if len(backends) == 1 else "mixed",
        "dry_run": dry_run,
        "log_dropped": log_dropped,
        "per_member": per_member,
    }
    transcript.write("team_end", **{k: v for k, v in summary.items() if k != "per_member"})
    transcript.write_summary(summary)
    transcript.close()
    if callable(close := getattr(bus, "close", None)):
        close()
    return PilotTeamResult(
        outcome=outcome,
        reason=reason,
        run_dir=run_dir,
        members=list(members),
        per_member=per_member,
        messages=talks["members"],
        notices=talks["notices"],
        usage=usage,
        steps=int(summary["steps"]),
        llm_calls=int(summary["llm_calls"]),
        wall_elapsed_s=wall_elapsed_s,
        cost_usd=total_cost,
        log_dropped=log_dropped,
    )


__all__ = [
    "ADVISORY_FIELDS",
    "MAX_MEMBERS",
    "MIN_MEMBERS",
    "WORST_FIRST",
    "PilotTeamResult",
    "Roster",
    "RosterEntry",
    "SpecEntry",
    "aggregate_outcome",
    "roster_from_specs",
    "run_pilot_team",
    "trim_contract",
    "union_problems",
]
