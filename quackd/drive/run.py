"""`quackd drive` from end to end: one robot connected, its session recorded, and a front end.

The robot is resolved by the flags `serve-mcp` takes (`mcp_server.fleet_from_flags`), and held by
a `RobotSession`, so the connect, the rest pose, the heartbeat and the close are the ones every
MCP session has. The record is a run directory of its own, `runs/<stamp>-drive[-name]/`, whose
`transcript.jsonl` opens with a `run_start` and closes with a `run_end`, as any run's does, so
`quackd log` replays it, and whose `goals/` holds each goal the pilot flew as a run of its own.
"""

from __future__ import annotations

import asyncio
import contextlib
import re
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, TextIO

from quackd import __version__
from quackd.drive.lanes import BRAKE_WORDS
from quackd.drive.session import Answered, DriveSession, GoalSettings
from quackd.duckfile.parser import duck_from_goal
from quackd.duckfile.schema import POLICY_TOTAL_MAX_S, POLICY_VERB, Budgets, DuckFile
from quackd.verbs.watch import WATCH

if TYPE_CHECKING:
    from quackd.front import Front

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

SESSION_POLICY_S = POLICY_TOTAL_MAX_S
"""A session's seconds of policy segments, for `SESSION_BUDGETS`' reason: the schema's ceiling for
a task's `policy.total_s`, where a task's default is two minutes, which a session of short
instructions to an arm spends in a dozen lines. Each goal spends from it under its own default
(`DriveSession._run_goal`). Set on the session's budget rather than written into `session_duck`,
whose `duck: 0` takes no `policy:` section."""


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


class Halted(Exception):
    """The page sent a Stop, a quit or its going away while the session was being set up."""

    def __init__(self, reason: str, *, as_asked: bool) -> None:
        super().__init__(reason)
        self.as_asked = as_asked
        """A quit, or the page's input ending, which end a connected session as asked too."""


_HALTED = {
    "stop": "the page's Stop came before the robot connected",
    "quit": "the session ended before the robot connected",
    "end": "the page went away before the robot connected",
}


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
    map_address: str | None = None
    yes: bool = False
    dry_run: bool = False
    runs_dir: str = "runs"
    run_name: str | None = None
    max_steps: int | None = None
    play: bool | None = None
    controller: str = "llm"
    """`llm` or `vla`, as the CLI has already checked it."""
    policy_url: str | None = None
    policy_token: str | None = None
    accept_other_frame: bool = False
    async_chunks: bool = True
    base_url: str | None = None
    api_key: str | None = None
    vision: bool | None = None
    extra_body: str | None = None
    effort: str | None = None
    price: str | None = None
    decision_mode: str | None = None
    """As typed. Only this gives a goal a stepper: `QUACKD_DECISION_MODE` is not read here."""
    decision_frames: bool | None = None
    """As typed, and read only from the line too: `QUACKD_DECISION_FRAMES` is not read here."""
    memory: bool = False
    memory_dir: str | None = None
    fov_deg: float | None = None


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.casefold()).strip("-")[:40]


def _given_lens(robot: Any, fov_deg: float) -> None:
    """`--fov-deg`, once, as the session connects, as a run applies it at its own connect
    (`AgentLoop.run`): the session's detector measures through it, and the body names it in every
    state a pilot reads. Each goal measures through the same lens, and never tells the body again
    (`AttachedBody.lens_given`). Only a body with a camera has a lens to be given."""
    from quackd.adapters.base import backend_name

    manifest = robot.executor.manifest
    if manifest is None or "camera" not in manifest.sensors:
        return
    detector = robot.detector
    calibrate = getattr(detector, "calibrate", None)
    if getattr(detector, "lens_at_connect", False) is True and callable(calibrate):
        calibrate(fov_deg, backend=backend_name(robot.transport), assumed=None)
    lens_given = getattr(robot.transport, "lens_given", None)
    if callable(lens_given):
        lens_given(fov_deg)


def _served(transport: Any) -> dict[str, Any] | None:
    """What the body says of the policy server it hands its segments to, as a run's `run_start`
    names it (`AgentLoop._policy`), or None for a body with none. Never allowed to fail the
    session over its record."""
    try:
        said = getattr(transport, "policy_served", None)
    except Exception:
        return None
    return dict(said) if isinstance(said, Mapping) else None


async def run_drive(
    args: DriveArgs,
    *,
    say: Callable[[str], None] = print,
    stream: TextIO | None = None,
    keyboard: bool | None = None,
    front: Front | None = None,
) -> Path:
    """Drive one robot until the person quits, and return the session's run directory.

    `front` is the page's end of the session (`quackd.front`), where the page drives it: lines
    and Stops come from there, questions go there, and every record is read there too."""
    from quackd.adapters.base import backend_name
    from quackd.agent.decision.factory import (
        decision_llm_is_available,
        make_decision_llm,
        parse_decision_llm,
        resolve_decision_address,
        resolve_decision_frames,
        resolve_decision_mode,
        resolve_decision_price,
    )
    from quackd.agent.decision.notices import MODE_ON_UNMEASURED, frames_notice
    from quackd.agent.providers.factory import make_provider, resolve_llm
    from quackd.agent.transcript import Transcript, new_run_dir
    from quackd.duckfile.parser import load_duck
    from quackd.duckfile.schema import Budgets
    from quackd.log import EventLog, LogEvent
    from quackd.mcp_server import RobotSession, fleet_from_flags, session_memory_key
    from quackd.memory import RobotMemory
    from quackd.registry import Registry, resolve_robot_ref
    from quackd.safety import Budget, Executor, Heartbeat
    from quackd.space.places import PlaceStore
    from quackd.verbs.registry import default_registry

    ops: asyncio.Queue[Mapping[str, Any]] = asyncio.Queue()
    if front is not None:
        # from the first moment, so a line or a Stop the page sends while the robot is still
        # connecting waits for the session rather than being lost
        loop = asyncio.get_running_loop()

        def take(op: Mapping[str, Any]) -> None:
            with contextlib.suppress(RuntimeError):  # the loop has closed: the session is over
                loop.call_soon_threadsafe(ops.put_nowait, op)

        front.attach_drive(take)

    most = _ceiling("max_steps")
    if args.max_steps is not None and not 1 <= args.max_steps <= most:
        raise DriveError(
            f"--max-steps is how many steps each goal may take, 1 to {most}, not {args.max_steps}"
        )
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
        policy_url=args.policy_url,
        policy_token=args.policy_token,
        accept_other_frame=args.accept_other_frame,
        async_chunks=args.async_chunks,
        room=args.room,
        map_address=args.map_address,
        fov_deg=args.fov_deg,
    )
    if len(plan.adapters) != 1:
        raise DriveError("drive drives one robot at a time: name it with --robot")
    ((name, adapter),) = plan.adapters.items()
    vla = args.controller == "vla"
    # what `quackd run` hands the pilot's model, given once for every goal: only a board a person
    # named for this session or this robot, which QUACKD_HOST sits below for a local preset
    model_kw: dict[str, Any] = {
        "base_url": args.base_url,
        "api_key": args.api_key,
        "vision": args.vision,
        "extra_body": args.extra_body,
        "effort": args.effort,
        "host": plan.host.explicit,
    }
    vendor: str
    model_id: str | None
    if vla:
        from quackd.agent.providers.vla import NAME as VLA

        # the scripted pilot asks no model, so the robot's registered pilot and `QUACKD_LLM`
        # name one this session never asks, as a `quackd run --controller vla` never does
        vendor, model_id = VLA, "scripted:goal"
    else:
        # a registered robot may name the pilot that flies it, as `quackd run` reads it: the
        # flag first, then the robot's own, then `QUACKD_LLM`
        here = resolve_robot_ref(args.robot, Registry(args.registry_dir))
        entry = here.entry
        vendor, model_id, llm_source = resolve_llm(
            args.llm,
            entry.llm if entry is not None else None,
            robot=entry.name if entry is not None else None,
        )
        # built now whichever pilot it is, the scripted one included, so a flag it refuses (an
        # `--effort` it takes no setting for, an `--extra-body` that is no JSON object) is
        # refused before anything connects, as `quackd run` refuses it
        built = make_provider(vendor, model=model_id, source=llm_source, **model_kw)
    # The decision LLM: the fast lane's, as it always was, and a goal's stepper's only where the
    # command line asks for one. `QUACKD_DECISION_MODE` and `QUACKD_DECISION_FRAMES` are not
    # read, so a `.env` written for `quackd run` changes no session, and a session started as
    # one always was is driven as it always was.
    named = parse_decision_llm(args.decision_llm)
    said_off = (args.decision_llm or "").strip().lower() == "off"
    decision = (
        resolve_decision_mode(args.decision_mode, named=named is not None, refused=said_off)
        if (args.decision_mode or "").strip()
        else "off"
    )
    frames = bool(args.decision_frames) and resolve_decision_frames(
        True,
        spec=named[0] if named is not None else None,
        url=args.decision_url,
        refused=said_off,
    )
    stepper = decision != "off" or frames
    if named is not None and stepper:
        # an address the named row can never be handed is a typo, refused on every machine, as
        # `quackd run` refuses it before it asks whether the decision LLM is here at all
        resolve_decision_address(named[0], args.decision_url)
    chooser_llm = None
    decision_price = None
    if named is not None:
        spec, decision_model = named
        available, why = decision_llm_is_available(spec, url=args.decision_url)
        if not available:
            say(f"{'note':<8} --decision-llm {spec.name} asked for, driving without it: {why}")
            decision, frames, stepper = "off", False, False
        else:
            # one client, the fast lane's and every goal's stepper's: a closed question each,
            # and the in-process one takes them one at a time (`LayaLLM._predict`). Built to be
            # shown frames only where they were asked for, so every other session builds it as
            # a session always has
            chooser_llm = make_decision_llm(
                spec,
                model=decision_model,
                url=args.decision_url,
                **({"frames": True} if frames else {}),
            )
            if stepper:
                try:
                    decision_price = resolve_decision_price(spec, url=args.decision_url)
                except ValueError as e:
                    raise DriveError(str(e)) from None
    # one key for the robot's memory and its places, its registered name or adapter:backend
    memory_key = session_memory_key(name, adapter, plan.memory_keys)
    # the robot's memory for the goals the pilot flies, where --memory asked for it: off in
    # drive, where `quackd run` keeps it on, so a session of typed commands writes none
    memory = RobotMemory(memory_key, args.memory_dir) if args.memory else None
    adopted = load_duck(args.duckfile) if args.duckfile else None
    if vla and adopted is not None and POLICY_VERB not in adopted.frontmatter.verbs.allow:
        # refused here, before anything is made or connected, rather than at every line, each
        # asking whether to go ahead and then failing on the allowlist
        raise DriveError(
            "--controller vla hands each line to the arm's learned policy through "
            f"{POLICY_VERB}, and {adopted.name} does not allow it: add {POLICY_VERB} to its "
            "verbs.allow, or drive without --duckfile"
        )

    run_dir = new_run_dir(args.runs_dir, "drive", _slug(args.run_name) if args.run_name else None)
    transcript = Transcript(run_dir)
    if front is not None:
        front.run_dir(run_dir)
    sessions: list[DriveSession] = []

    def record(kind: str, **data: Any) -> None:
        """The session's own records, written beside the event log rather than through it, and
        read by the page as they are written where one drives the session."""
        transcript.write(kind, **data)
        if front is not None:
            front.record(kind, data)

    def observe(event: LogEvent) -> None:
        if event.kind == "intent" and sessions:
            sessions[0].saw_intent(str(event.data.get("intent")), bool(event.data.get("accepted")))

    event_log = EventLog(
        record=transcript.sink, observers=[observe] if front is None else [observe, front.view]
    )
    detector = plan.detector
    if detector is None and backend_name(adapter) in ("sim2d", "mujoco"):
        from quackd.perception.color_blob import ColorBlobDetector

        # through `--fov-deg` where it was given, as `quackd run` builds its detector through it:
        # this one learns no lens at connect, so the lens it is built with is the one it keeps
        detector = (
            ColorBlobDetector(fov_deg=float(args.fov_deg)) if args.fov_deg else ColorBlobDetector()
        )
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
        # and an MCP session keep them, for the next session and the next run to walk to, and
        # shared with every goal; a dry session marks none and writes nothing
        places=PlaceStore(memory_key, args.memory_dir, persist=not args.dry_run),
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
    if adopted is not None:
        robot.adopt(adopted)
    started = time.perf_counter()
    outcome, reason = "success", "the session ended"
    try:
        halted = front.halted() if front is not None else None
        if halted is not None:
            # nothing connects. A quit or the page's input ending ends a connected session as
            # asked, and so ends this one; a Stop pressed while it was set up is a run stopped
            raise Halted(_HALTED[halted], as_asked=halted != "stop")
        await robot.connect()
        if args.fov_deg:
            _given_lens(robot, float(args.fov_deg))
        registry = executor.registry
        if robot.duck is None:
            # after the connect, so the verbs are the body's own and not the default registry's
            robot.adopt(
                session_duck([v.name for v in registry.verbs() if v.safety_class != "dangerous"])
            )
            if executor.budget is not None:
                executor.budget.policy_total_s = SESSION_POLICY_S
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
        # `watch`, which the session withdrew for having nothing to look through, for goals that
        # do: shown the camera through the decision LLM, as `quackd run` offers it on a run that
        # asked for frames, and only where the session's own `.duck`, if it has one, allows it
        manifest = executor.manifest
        watch = (
            frames
            and manifest is not None
            and WATCH in manifest.verb_names()
            and (args.duckfile is None or executor.is_allowed(WATCH))
        )
        if watch and WATCH not in goal_verbs:
            goal_verbs = sorted([*goal_verbs, WATCH])
        settings = GoalSettings(
            max_steps=args.max_steps,
            play=args.play,
            price=args.price,
            memory=memory,
            fov_deg=args.fov_deg,
            decision=decision,
            decision_llm=chooser_llm if stepper else None,
            decision_price=decision_price,
            decision_frames=frames,
            vla=vla,
            policy=args.policy_url is not None and POLICY_VERB in registry,
        )

        def pilot(goal: str) -> Any:
            if vla:
                from quackd.agent.providers.vla import VlaProvider

                # the line is its one instruction, held to the rule for one as it reached the
                # pilot (`DriveSession._vla_refusal`)
                return VlaProvider([goal], label="goal")
            if vendor == "fake":
                return make_provider("fake", duck_name="goal", goal=goal, **model_kw)
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
        from quackd.drive.terminal import (
            Asker,
            PageAsker,
            drive_from_jsonl,
            drive_from_keyboard,
            drive_from_pipe,
        )

        at_keyboard = front is None and (
            keyboard if keyboard is not None else (stream is None and sys.stdin.isatty())
        )
        asker = Asker(say) if at_keyboard else None
        drive = DriveSession(
            robot,
            record=record,
            chooser=chooser,
            pilot=pilot,
            goals=run_dir / "goals",
            goal_verbs=goal_verbs,
            ask=PageAsker(front, say) if front is not None else asker,
            yes=args.yes,
            goal_view=front.goal_view if front is not None else None,
            goal_frames=front.frame if front is not None else None,
            settings=settings,
        )
        sessions.append(drive)
        await drive.prepare()
        if frames and chooser_llm is not None:
            say(f"{'note':<8} {frames_notice(decision, chooser_llm.url, watch=watch)}")
        if decision == "on":
            say(f"{'note':<8} {MODE_ON_UNMEASURED}")
        record(
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
            # the policy server the arm hands its segments to, as a run's record names it,
            # `--accept-other-frame` and `--no-async-chunks` among what it says
            **({"policy": served} if (served := _served(adapter)) else {}),
            # the front end the session's lines came through, only where it was not a
            # terminal, so every record from a terminal reads as it did (`quackd.front`)
            **({"front": "jsonl"} if front is not None else {}),
        )
        say(f"{'drive':<8} {name}: type a command, a goal, or help; Enter brakes")
        if front is not None:
            await drive_from_jsonl(drive, front, ops, say=say)
        elif at_keyboard and asker is not None:
            await drive_from_keyboard(drive, asker, say=say)
        else:
            await drive_from_pipe(drive, stream, say=say)
    except Halted as e:
        reason = str(e)
        if not e.as_asked:
            outcome = "aborted"
            raise DriveError(reason) from None
        say(f"{'ended':<8} {reason}")
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
        record("run_end", **summary)
        transcript.close()
        if front is not None:
            front.finished(outcome, reason, run_dir=run_dir, summary=summary)
    return run_dir
