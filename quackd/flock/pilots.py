"""A flock of pilots: one `AgentLoop` per body, on wall-clock time, talking over the bus.

The other kind of flock ([`runner.py`](runner.py)) is a referee and N state machines in one
simulated arena on a lockstep clock. This one has no referee and no arena. Each member is a
real `AgentLoop` with its own provider, executor, allowlist, budget, heartbeat, memory and
feasibility verdict, running at the same time as the others, on any backend, and the only
thing that coordinates them is what they say to each other (ADR-0034).

Three consequences worth saying out loud, because they are the cost of that:

- **Nothing is reproducible.** There is no shared clock, so a seed does not fix a run.
- **Nothing is checked against the world.** The auction vetoes a claimed kick with the
  simulator's own `ball_displacement_m`. Here there is no one world to ask, so the outcome is
  the members' own claims, which is exactly as true as a solo run's.
- **N simulated members are N separate worlds.** Two `microduck:sim2d` pilots cannot see each
  other. A shared arena stays an auction feature.

What this does guarantee: every member ends, every transport closes, and a member that raises
stops the others with a reason that names it.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from quackd import __version__
from quackd.adapters.base import go_to_rest_if_any
from quackd.adapters.factory import RobotSpec, describe, make_adapter
from quackd.adapters.manifest import RobotManifest
from quackd.agent.loop import AgentLoop, Outcome, RunConfig, RunResult, resolve_play
from quackd.agent.providers.base import LLMProvider, Usage
from quackd.agent.transcript import new_run_dir, run_label
from quackd.command import command_line
from quackd.duckfile.schema import DuckFile, DuckFrontmatter
from quackd.flock.bus import Bus, InProcessBus
from quackd.flock.runner import FLOCK_LOG, BusFactory, ViewFactory
from quackd.flock.talk import FLOCK_SRC, Peer, make_links, notice
from quackd.flock.transcript import FlockTranscript
from quackd.log import EventLog, Sink
from quackd.memory import RobotMemory
from quackd.safety import allow_all, deny_all
from quackd.verbs.aliases import canonical

MIN_MEMBERS = 2
MAX_MEMBERS = 8
"""`quackd.duckfile.schema.PILOTS_MAX_MEMBERS`, spelled here because this is where a roster
that did not come from a task file is bounded."""

WORST_FIRST: tuple[Outcome, ...] = ("error", "aborted", "infeasible", "budget", "failure")
"""How a flock's outcome is chosen when its members disagree, worst first.

`error` outranks `aborted` because of what the pair usually means together: one member raised
and the rest were stopped *because it did*, so the aborts are the consequence and the error is
the cause. A flock that reported `aborted` there would read as though somebody had pressed
something. When a person really does press something, every member aborts and nothing errors,
so `aborted` is still what that says."""


def _standing_go(_why: str) -> bool:
    """A flock member's answer to its pilot's doubt under `--yes`: go, asked of nobody."""
    return True


_standing_go.answers_as = "a flock's standing answer"  # type: ignore[attr-defined]
"""What the record names as having said go (`quackd.log.who_answered`), and never a person."""


class RosterEntry(Protocol):
    """What this needs of a registered robot (`quackd.registry.RobotEntry`).

    Structural, so a flock can also be run from `--robots` with no registry in sight."""

    name: str
    provider: str | None
    model: str | None

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
    """`member_specs(...)` (the auction's own resolver) turned into a roster."""
    return {name: SpecEntry(name, spec) for name, spec in specs.items()}


@dataclass
class PilotFlockResult:
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
    """What the whole flock cost, or None the moment one member could not be priced: a flock
    bill quietly missing a robot is worse than no flock bill."""
    log_dropped: int = 0
    gif_path: Path | None = None

    @property
    def ok(self) -> bool:
        return self.outcome == "success"


# ── the contract, per body ──────────────────────────────────────────────────────────────


def trim_contract(fm: DuckFrontmatter, manifest: RobotManifest) -> DuckFrontmatter:
    """This task file as it applies to this body.

    A flock's task file allows what the flock as a whole needs, which for any one member is
    too much: `AgentLoop.run` refuses to start when a robot does not provide something the
    contract *requires*, so an arm in a kicking flock would be turned away at the door for not
    being able to walk. The union is checked once, by the runner, before anything connects
    (`validate_duck(..., flock=True)`); each member is then handed the part of it its own
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
    return DuckFrontmatter.model_validate(data)


def aggregate_outcome(per_member: Mapping[str, tuple[Outcome, str]]) -> tuple[Outcome, str]:
    """One outcome for the flock, from what each member said about itself.

    Success only when everyone declared it: a flock in which one member never finished did not
    finish. Otherwise the worst outcome wins and the reason names every member that did not
    succeed, worst first, so the first thing read is the thing that went most wrong."""
    if not per_member:
        return "error", "the flock had no members"
    failed = {name: pair for name, pair in per_member.items() if pair[0] != "success"}
    if not failed:
        return "success", f"every member declared success: {', '.join(per_member)}"
    worst = next(o for o in WORST_FIRST if any(pair[0] == o for pair in failed.values()))
    ranked = sorted(failed.items(), key=lambda kv: WORST_FIRST.index(kv[1][0]))
    return worst, "; ".join(f"{name} {outcome}: {why}" for name, (outcome, why) in ranked)


# ── the run ─────────────────────────────────────────────────────────────────────────────


async def run_pilot_flock(
    duck: DuckFile,
    roster: Roster,
    *,
    providers: Mapping[str, LLMProvider],
    seed: int | None = None,
    runs_dir: str | Path = "runs",
    dry_run: bool = False,
    max_steps: int | None = None,
    live: bool = False,
    yes: bool = False,
    memories: Mapping[str, RobotMemory] | None = None,
    fov_deg: float | None = None,
    log: Callable[[str], None] = lambda _m: None,
    bus_factory: BusFactory | None = None,
    view: ViewFactory | None = None,
    on_run_dir: Callable[[Path], None] | None = None,
    abort: asyncio.Event | None = None,
    flock_name: str | None = None,
    run_name: str | None = None,
    price: str | None = None,
    play: bool | None = None,
) -> PilotFlockResult:
    """One pilot per member, all at once, until every one of them has ended.

    `play` is `--play` or `--no-play` over the task file's own `play`, for every member at
    once, and None is the file's word."""
    members = list(roster)
    if not MIN_MEMBERS <= len(members) <= MAX_MEMBERS:
        raise ValueError(
            f"a pilot flock needs {MIN_MEMBERS} to {MAX_MEMBERS} members; this one names "
            f"{len(members)}"
        )
    missing = [name for name in members if name not in providers]
    if missing:
        raise ValueError(f"no pilot for {', '.join(missing)}")

    manifests = {name: describe(entry.robot_spec) for name, entry in roster.items()}
    problems = union_problems(duck, manifests)
    if problems:
        where = ", ".join(f"{name}={roster[name].robot_spec.key}" for name in members)
        raise ValueError(f"{duck.name} cannot run on {where}: {'; '.join(problems)}")
    contracts = {name: trim_contract(duck.frontmatter, manifests[name]) for name in members}

    # built before the run directory exists, so a missing extra or a bad address fails with
    # nothing on disk to explain away
    adapters = {
        name: make_adapter(
            entry.robot_spec,
            seed=seed,
            live=live and name == members[0],
            **entry.adapter_kwargs(),
        )
        for name, entry in roster.items()
    }

    stem = duck.name if duck.name.startswith("flock") else f"flock-{duck.name}"
    run_dir = new_run_dir(runs_dir, stem, run_label(run_name) if run_name else None)
    if on_run_dir is not None:
        # The first moment there is somewhere to write: the CLI's terminal capture
        # has been buffering since before this call and moves into the directory
        # here, before any member opens a file of its own.
        on_run_dir(run_dir)
    wall0 = time.perf_counter()

    def now() -> float:
        return time.perf_counter() - wall0

    # `stamp="t"`: these are wall seconds, and writing them under the auction's `sim_t` would
    # be a lie in the one file a replay reads times from
    transcript = FlockTranscript(run_dir, now, stamp="t")
    talks = {"members": 0, "notices": 0}

    def tap(msg: Any) -> None:
        transcript.on_bus(msg)
        if getattr(msg, "kind", None) == "TALK":
            talks["notices" if msg.src == FLOCK_SRC else "members"] += 1

    bus: Bus = bus_factory(tap) if bus_factory else InProcessBus(tap=tap)
    if callable(start := getattr(bus, "start", None)):
        start()  # inside the event loop, so remote deliveries are marshalled onto it

    task_id = uuid.uuid4().hex[:8]
    peers = {name: Peer(name, roster[name].robot_spec.key, manifests[name]) for name in members}
    links = make_links(members, peers, bus=bus, task_id=task_id, now=now)

    def member_view(name: str) -> Sink | None:
        return view(name) if view is not None else None

    # no record: every TALK is already in flock.jsonl through the tap, under its own name
    story = EventLog(observers=[v] if (v := member_view(FLOCK_LOG)) is not None else [])

    loops: dict[str, AgentLoop] = {}
    for name in members:
        member_dir = run_dir / "ducks" / name
        member_dir.mkdir(parents=True, exist_ok=True)  # `Transcript` opens, it does not mkdir
        loops[name] = AgentLoop(
            RunConfig(
                duck=DuckFile(frontmatter=contracts[name], body=duck.body, path=duck.path),
                provider=providers[name],
                transport=adapters[name],
                run_dir=member_dir,
                runs_dir=runs_dir,
                max_steps=max_steps,
                dry_run=dry_run,
                # N blocking prompts would starve every other member's heartbeat, so the
                # answer for all of them is `--yes` or nothing, exactly as it is over MCP
                confirm=allow_all if yes else deny_all,
                acknowledge=None,
                decide=_standing_go if yes else None,
                memory=(memories or {}).get(name),
                fov_deg=fov_deg,
                log=lambda m, who=name: log(f"{who}: {m}"),
                view=member_view(name),
                link=links[name],
                # every member is priced the same way, because `--price` is one rate for the
                # run rather than one per robot: a flock of the same model on three bodies
                # is three bills at one rate
                price=price,
                summary_file=False,
                play=play,
            )
        )

    played, played_from = resolve_play(duck.frontmatter.play, play)
    transcript.write(
        "flock_start",
        duck=duck.name,
        flock=flock_name,
        method="pilots",
        clock="wall",
        members=members,
        robots={name: roster[name].robot_spec.key for name in members},
        providers={
            name: {"provider": providers[name].name, "model": providers[name].model}
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
        seed=seed,
        dry_run=dry_run,
        contract=duck.frontmatter.model_dump(),
        # as each member's `run_start` says it, so the flock's own record agrees with them
        # when `--play` or `--no-play` overrode the file
        play={"on": played, "from": played_from},
    )

    results: dict[str, RunResult] = {}
    ended: set[str] = set()
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

    watcher = asyncio.create_task(watch(), name="quackd-pilots-abort")

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
            # still reports the wall clock, the model seconds and the bill it had already
            # measured. Without it a flock of fully priced models read `cost_usd: null` on
            # the strength of one member being interrupted.
            summary=loop.summary,
        )

    async def member(name: str) -> None:
        """One member, start to finish, recorded whatever it did.

        Swallows every `Exception`, so a flock ends when every member has ended however each
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
            results[name] = _ended(name, "error", f"{type(e).__name__}: {e}")
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
                fan_out(f"stopped by the flock: {name} raised {type(e).__name__}: {e}")
                master.set()
        finally:
            ended.add(name)
            links[name].close()
            result = results.setdefault(name, _ended(name, "error", "ended without a result"))
            transcript.write(
                "member_end",
                duck=name,
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
            story.emit("talk", src=FLOCK_SRC, to="all", text=sent.text, ok=True, summary=text)

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
            duck=duck,
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
            seed=seed,
            dry_run=dry_run,
            flock_name=flock_name,
            run_name=run_name,
        )
    return result


ADVISORY_FIELDS = ("verbs.allow",)
"""Problems a pilot flock answers by trimming rather than by refusing.

`verbs.allow` naming a verb no member provides is the validator's weaker line: a task file may
allow more than it needs, and for a solo run `AgentLoop` drops the extras with a note. A pilot
flock does the same thing per member (`trim_contract`), so refusing here would turn "the duck
cannot move joints and the arm cannot say" into a reason two robots may not work together,
which is the opposite of the point. `requires`, which is what the task actually needs, is
still checked against the union and still refuses."""


def union_problems(duck: DuckFile, manifests: Mapping[str, RobotManifest]) -> list[str]:
    """What no body in this flock can do. Checked before anything connects."""
    from quackd.duckfile.validate import validate_duck

    return [
        p.message
        for p in validate_duck(duck, list(manifests.values()), flock=True)
        if p.field not in ADVISORY_FIELDS
    ]


def _finish(
    *,
    duck: DuckFile,
    members: Sequence[str],
    roster: Roster,
    providers: Mapping[str, LLMProvider],
    results: Mapping[str, RunResult],
    talks: Mapping[str, int],
    bus: Bus,
    transcript: FlockTranscript,
    story: EventLog,
    loops: Mapping[str, AgentLoop],
    run_dir: Path,
    wall_elapsed_s: float,
    seed: int | None,
    dry_run: bool,
    flock_name: str | None,
    run_name: str | None = None,
) -> PilotFlockResult:
    """The rollup, the summary and the teardown. Runs whatever happened to the members."""
    outcome, reason = aggregate_outcome(
        {name: (results[name].outcome, results[name].reason) for name in members}
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
            "provider": providers[name].name,
            "model": providers[name].model,
            "robot": roster[name].robot_spec.key,
            "run_dir": f"ducks/{name}",
            "log_dropped": results[name].log_dropped,
        }
        for name in members
    }
    usage = Usage()
    for name in members:
        usage = usage + results[name].usage
    # None the moment ONE member could not be priced, rather than a total that quietly leaves
    # that member out: a flock bill missing a robot is worse than no flock bill.
    costs: list[float | None] = [results[name].summary.get("cost_usd") for name in members]
    total_cost = None if any(c is None for c in costs) else round(sum(c or 0.0 for c in costs), 6)
    backends = {roster[name].robot_spec.backend for name in members}
    log_dropped = story.dropped + sum(loops[name].event_log.dropped for name in members)
    summary: dict[str, Any] = {
        "duck": duck.name,
        "outcome": outcome,
        "reason": reason,
        "flock": {"members": list(members), "method": "pilots", "name": flock_name},
        "run_name": run_name,
        # What was asked for, beside what happened. A flock root writes no `run_start`,
        # so without this the argv would be in the terminal file and nowhere a reader
        # can parse, and on a deterministic flock in no record at all.
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
        "seed": seed,
        "transport": backends.pop() if len(backends) == 1 else "mixed",
        "dry_run": dry_run,
        "log_dropped": log_dropped,
        "per_member": per_member,
    }
    transcript.write("flock_end", **{k: v for k, v in summary.items() if k != "per_member"})
    transcript.write_summary(summary)
    transcript.close()
    if callable(close := getattr(bus, "close", None)):
        close()
    return PilotFlockResult(
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
    "PilotFlockResult",
    "Roster",
    "RosterEntry",
    "SpecEntry",
    "aggregate_outcome",
    "roster_from_specs",
    "run_pilot_flock",
    "trim_contract",
    "union_problems",
]
