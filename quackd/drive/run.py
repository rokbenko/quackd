"""`quackd drive` from end to end: one robot connected, its session recorded, and a front end.

The robot is resolved by the flags `serve-mcp` takes (`mcp_server.fleet_from_flags`), and held by
a `RobotSession`, so the connect, the rest pose, the heartbeat and the close are the ones every
MCP session has. The record is a run directory of its own, `runs/<stamp>-drive[-name]/`, whose
`transcript.jsonl` opens with a `run_start` and closes with a `run_end`, as any run's does, so
`quackd log` replays it, and whose `goals/` holds each goal the pilot flew as a run of its own.
"""

from __future__ import annotations

import contextlib
import re
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TextIO

from quackd import __version__
from quackd.drive.lanes import BRAKE_WORDS
from quackd.drive.session import Answered, DriveSession
from quackd.duckfile.parser import duck_from_goal
from quackd.duckfile.schema import Budgets, DuckFile

GOAL_VERBS_SAFE = "safe"
"""A goal gets the body's safe verbs, as `quackd run --goal` gives one."""


def _ceiling(name: str) -> Any:
    """The schema's own bound on one of a task's budgets, or None where it sets none."""
    for bound in Budgets.model_fields[name].metadata:
        if (le := getattr(bound, "le", None)) is not None:
            return le
    return None


SESSION_BUDGETS = Budgets(
    **{name: le for name in Budgets.model_fields if (le := _ceiling(name)) is not None}
)
"""A session's budgets: the schema's own ceilings for a task's. A person drives for longer than one
task's defaults last, and a session held to them refused every call after its fifth minute. Each
goal runs under the defaults of its own, as `quackd run --goal` does."""


def session_duck(allow: Sequence[str]) -> DuckFile:
    """The contract a session without a `.duck` drives under: the verbs given, every one of the
    body's that is not dangerous, as a contractless session allows; the battery rule `quackd run
    --goal` holds a goal to; and `SESSION_BUDGETS`. Not that goal's rule that a verb failing three
    times in a row ends the run: a person who kicks and misses three times has not asked for the
    session to end, and the rule would refuse every call after it."""
    goal = duck_from_goal("Drive the robot, one command at a time.", list(allow))
    front = goal.frontmatter
    battery = [
        rule
        for rule in front.abort_when
        if front.model_copy(update={"abort_when": [rule]}).battery_abort_percent is not None
    ]
    front = front.model_copy(
        update={"name": "drive", "abort_when": battery, "budgets": SESSION_BUDGETS}
    )
    return DuckFile(frontmatter=front, body=goal.body, path="<drive>")


class DriveError(Exception):
    """A session refused before anything connected, in a sentence of its own."""


@dataclass(frozen=True)
class DriveArgs:
    robot: str | None = None
    registry_dir: str | None = None
    address: str | None = None
    camera_url: tuple[str, ...] = ()
    token: str | None = None
    host: str | None = None
    host_token: str | None = None
    detector: str | None = None
    seed: int | None = None
    llm: str | None = None
    decision_llm: str | None = None
    decision_url: str | None = None
    duckfile: str | None = None
    room: str | None = None
    yes: bool = False
    dry_run: bool = False
    runs_dir: str = "runs"
    run_name: str | None = None


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.casefold()).strip("-")[:40]


async def run_drive(
    args: DriveArgs,
    *,
    say: Callable[[str], None] = print,
    stream: TextIO | None = None,
    keyboard: bool | None = None,
) -> Path:
    """Drive one robot until the person quits, and return the session's run directory."""
    from quackd.adapters.base import backend_name
    from quackd.agent.decision.factory import (
        decision_llm_is_available,
        make_decision_llm,
        parse_decision_llm,
    )
    from quackd.agent.providers.factory import make_provider, resolve_llm
    from quackd.agent.transcript import Transcript, new_run_dir
    from quackd.duckfile.parser import load_duck
    from quackd.duckfile.schema import Budgets
    from quackd.log import EventLog, LogEvent
    from quackd.mcp_server import RobotSession, fleet_from_flags, session_memory_key
    from quackd.registry import Registry, resolve_robot_ref
    from quackd.safety import Budget, Executor, Heartbeat
    from quackd.space.places import PlaceStore
    from quackd.verbs.registry import default_registry

    plan = fleet_from_flags(
        robot=args.robot,
        registry_dir=args.registry_dir,
        duckfile=args.duckfile,
        seed=args.seed,
        address=args.address,
        camera_url=list(args.camera_url) or None,
        token=args.token,
        host=args.host,
        host_token=args.host_token,
        detector=args.detector,
        room=args.room,
    )
    if len(plan.adapters) != 1:
        raise DriveError("drive drives one robot at a time: name it with --robot")
    ((name, adapter),) = plan.adapters.items()
    # a registered robot may name the pilot that flies it, as `quackd run` reads it: the flag
    # first, then the robot's own, then `QUACKD_LLM`
    here = resolve_robot_ref(args.robot, Registry(args.registry_dir))
    entry = here.entry
    vendor, model_id, llm_source = resolve_llm(
        args.llm,
        entry.llm if entry is not None else None,
        robot=entry.name if entry is not None else None,
    )
    if vendor != "fake":
        built = make_provider(vendor, model=model_id, source=llm_source)
    chooser_llm = None
    named = parse_decision_llm(args.decision_llm)
    if named is not None:
        spec, decision_model = named
        available, why = decision_llm_is_available(spec, url=args.decision_url)
        if not available:
            say(f"{'note':<8} --decision-llm {spec.name} asked for, driving without it: {why}")
        else:
            chooser_llm = make_decision_llm(spec, model=decision_model, url=args.decision_url)

    run_dir = new_run_dir(args.runs_dir, "drive", _slug(args.run_name) if args.run_name else None)
    transcript = Transcript(run_dir)
    sessions: list[DriveSession] = []

    def observe(event: LogEvent) -> None:
        if event.kind == "intent" and sessions:
            sessions[0].saw_intent(str(event.data.get("intent")), bool(event.data.get("accepted")))

    event_log = EventLog(record=transcript.sink, observers=[observe])
    detector = plan.detector
    if detector is None and backend_name(adapter) in ("sim2d", "mujoco"):
        from quackd.perception.color_blob import ColorBlobDetector

        detector = ColorBlobDetector()
    executor = Executor(
        registry=default_registry(),
        transport=adapter,
        contract=None,
        budget=Budget(Budgets(), now=adapter.now),
        detector=detector,
        dry_run=args.dry_run,
        confirm=Answered(yes=args.yes),
        event_log=event_log,
        # the places a person marks, kept beside the robot's memory and keyed as it is, as a run
        # and an MCP session keep them, for the next session and the next run to walk to; a dry
        # session marks none and writes nothing
        places=PlaceStore(
            session_memory_key(name, adapter, plan.memory_keys), persist=not args.dry_run
        ),
    )
    executor.require_verdict = True
    heartbeat = Heartbeat(adapter, executor.abort, period_s=0.5, event_log=event_log)
    robot = RobotSession(
        name=name,
        transport=adapter,
        registry=executor.registry,
        executor=executor,
        heartbeat=heartbeat,
        detector=detector,
        event_log=event_log,
    )
    if args.duckfile:
        robot.adopt(load_duck(args.duckfile))
    started = time.perf_counter()
    outcome, reason = "success", "the session ended"
    try:
        await robot.connect()
        registry = executor.registry
        if robot.duck is None:
            # after the connect, so the verbs are the body's own and not the default registry's
            robot.adopt(
                session_duck([v.name for v in registry.verbs() if v.safety_class != "dangerous"])
            )
        # by `is_allowed`, which reads an alias as the verb it names: a `.duck` allowing `walk`
        # allows `move`, and matching the names alone left it out
        verbs = {n: registry.get(n) for n in registry.names() if executor.is_allowed(n)}
        contract = robot.duck.frontmatter if robot.duck is not None else None
        # the `.duck`'s own confirm gates, which the fast lane's floors answer to as the run's
        # stepper's do (`verb_class`)
        gated = frozenset(
            registry.canonical(c) for c in (contract.verbs.confirm if contract is not None else ())
        )
        goal_verbs = sorted(n for n, v in verbs.items() if v.safety_class == GOAL_VERBS_SAFE)

        def pilot(goal: str) -> Any:
            if vendor == "fake":
                return make_provider("fake", duck_name="goal", goal=goal)
            return built

        chooser = None
        if chooser_llm is not None:
            from quackd.agent.decision.commands import CommandChooser, offers_for
            from quackd.drive.presets import presets_for

            warm = getattr(chooser_llm, "warm", None)
            if callable(warm):
                await warm()
            blurb = executor.manifest.blurb if executor.manifest is not None else ""
            chooser = CommandChooser(
                chooser_llm, offers_for(verbs, presets_for(executor.manifest, verbs), gated), blurb
            )
        from quackd.drive.terminal import Asker, drive_from_keyboard, drive_from_pipe

        at_keyboard = keyboard if keyboard is not None else (stream is None and sys.stdin.isatty())
        asker = Asker(say) if at_keyboard else None
        drive = DriveSession(
            robot,
            record=transcript.write,
            chooser=chooser,
            pilot=pilot,
            goals=run_dir / "goals",
            goal_verbs=goal_verbs,
            ask=asker,
            yes=args.yes,
        )
        sessions.append(drive)
        await drive.prepare()
        transcript.write(
            "run_start",
            duck="drive",
            # the pilot a goal is flown by, as the header of every run names its own
            provider=vendor,
            model=model_id or vendor,
            robot=name,
            adapter=getattr(adapter, "name", None),
            transport=backend_name(adapter),
            version=__version__,
            started_at=transcript.started_at_iso,
            drive={
                "presets": {p.name: p.call for p in drive.presets.values()},
                "brake_words": [w for w in BRAKE_WORDS if w],
                "decision_llm": getattr(chooser_llm, "name", None),
                "pilot": vendor,
            },
        )
        say(f"{'drive':<8} {name}: type a command, a goal, or help; Enter brakes")
        if at_keyboard and asker is not None:
            await drive_from_keyboard(drive, asker, say=say)
        else:
            await drive_from_pipe(drive, stream, say=say)
    except Exception as e:
        outcome, reason = "error", f"{type(e).__name__}: {e}"
        raise
    except BaseException as e:
        outcome, reason = "aborted", f"interrupted: {type(e).__name__}"
        raise
    finally:
        # every way out, an error and an interrupt among them, ends what is still moving the
        # body before the body is folded and let go of
        if sessions:
            with contextlib.suppress(Exception):
                await sessions[0].close()
        with contextlib.suppress(Exception):
            await robot.close()
        summary = {
            "duck": "drive",
            # a session a person ended, by quit or the end of its input, ended as asked, and one
            # that broke, a connect that failed among them, says how
            "outcome": outcome,
            "reason": reason,
            "steps": executor.budget.steps if executor.budget is not None else 0,
            "wall_s": round(time.perf_counter() - started, 3),
            "started_at": transcript.started_at_iso,
            "drive": sessions[0].stats.block() if sessions else {},
        }
        transcript.write_summary(summary)
        transcript.write("run_end", **summary)
        transcript.close()
    return run_dir
