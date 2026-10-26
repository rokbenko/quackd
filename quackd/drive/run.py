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
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TextIO

from quackd import __version__
from quackd.drive.lanes import BRAKE_WORDS
from quackd.drive.session import Answered, DriveSession

GOAL_VERBS_SAFE = "safe"
"""A goal gets the body's safe verbs, as `quackd run --goal` gives one."""


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
    from quackd.mcp_server import RobotSession, fleet_from_flags
    from quackd.safety import Budget, Executor, Heartbeat
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
    )
    if len(plan.adapters) != 1:
        raise DriveError("drive drives one robot at a time: name it with --robot")
    ((name, adapter),) = plan.adapters.items()
    vendor, model_id, llm_source = resolve_llm(args.llm)
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
    try:
        await robot.connect()
        registry = executor.registry
        allowed = set(executor.allowed) if robot.duck is not None else None
        verbs = {n: registry.get(n) for n in registry.names() if allowed is None or n in allowed}
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
                chooser_llm, offers_for(verbs, presets_for(executor.manifest, verbs)), blurb
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
        await drive.drain()
    finally:
        with contextlib.suppress(Exception):
            await robot.close()
        summary = {
            "duck": "drive",
            # a session a person ended, by quit or the end of its input, ended as asked
            "outcome": "success",
            "reason": "the session ended",
            "steps": executor.budget.steps if executor.budget is not None else 0,
            "wall_s": round(time.perf_counter() - started, 3),
            "started_at": transcript.started_at_iso,
            "drive": sessions[0].stats.block() if sessions else {},
        }
        transcript.write_summary(summary)
        transcript.write("run_end", **summary)
        transcript.close()
    return run_dir
