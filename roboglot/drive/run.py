"""`roboglot drive` from end to end: one robot connected, its session recorded, and a front end.

The robot is resolved by the flags `serve-mcp` takes (`mcp_server.team_from_flags`), and held by
a `RobotSession`, so the connect, the rest pose, the heartbeat and the close are the ones every
MCP session has. The record is a run directory of its own, `runs/<stamp>-drive[-name]/`, whose
`transcript.jsonl` opens with a `run_start` and closes with a `run_end`, as any run's does, so
`roboglot log` replays it, and whose `goals/` holds each goal the pilot flew as a run of its own.
"""

from __future__ import annotations

import asyncio
import contextlib
import functools
import logging
import math
import re
import sys
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, TextIO, TypeVar

from roboglot import __version__
from roboglot.drive import lanes
from roboglot.drive.lanes import BRAKE_WORDS
from roboglot.drive.session import Answered, DriveSession, GoalSettings
from roboglot.glotfile.parser import glot_from_goal
from roboglot.glotfile.schema import POLICY_TOTAL_MAX_S, POLICY_VERB, Budgets, GlotFile
from roboglot.verbs.watch import WATCH

if TYPE_CHECKING:
    from roboglot.front import Front
    from roboglot.mcp_server import RobotSession

T = TypeVar("T")

logger = logging.getLogger("roboglot.drive")

PRESSES = (asyncio.CancelledError, KeyboardInterrupt)
"""What a Ctrl-C is to a session outside its prompt, which is while it connects and once the
prompt has closed: drive installs no kill switch, so asyncio's own handler cancels the main task at
the first press and raises an interrupt at a later one."""

GOAL_VERBS_SAFE = "safe"
"""A goal gets the body's safe verbs, as `roboglot run --goal` gives one."""


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
goal runs under the defaults of its own, as `roboglot run --goal` does."""

SESSION_POLICY_S = POLICY_TOTAL_MAX_S
"""A session's seconds of policy segments, for `SESSION_BUDGETS`' reason: the schema's ceiling for
a task's `policy.total_s`, where a task's default is two minutes, which a session of short
instructions to an arm spends in a dozen lines. Each goal spends from it under its own default
(`DriveSession._run_goal`). Set on the session's budget rather than written into `session_glot`,
whose `glot: 0` takes no `policy:` section."""


def session_glot(allow: Sequence[str]) -> GlotFile:
    """The contract a session without a `.glot` drives under: the verbs given, every one of the
    body's that is not dangerous, as a contractless session allows; the battery rule `roboglot run
    --goal` holds a goal to; and `SESSION_BUDGETS`. Not that goal's rule that a verb failing three
    times in a row ends the run: a person who kicks and misses three times has not asked for the
    session to end, and the rule would refuse every call after it."""
    goal = glot_from_goal("Drive the robot, one command at a time.", list(allow))
    front = goal.frontmatter
    battery = [
        rule
        for rule in front.abort_when
        if front.model_copy(update={"abort_when": [rule]}).battery_abort_percent is not None
    ]
    front = front.model_copy(
        update={"name": "drive", "abort_when": battery, "budgets": SESSION_BUDGETS}
    )
    return GlotFile(frontmatter=front, body=goal.body, path="<drive>")


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

PAGE_STOP_WHILE_CONNECTING = "the page's Stop came while the robot connected"
"""Why a session the page's Stop ended during its connect ended, the fold an arm opens with
among it, as `_HALTED`'s Stop says it of one that came before."""


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
    llm: str | None = None
    decision_llm: str | None = None
    decision_url: str | None = None
    glotfile: str | None = None
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
    """As typed. Only this gives a goal a stepper: `ROBOGLOT_DECISION_MODE` is not read here."""
    decision_frames: bool | None = None
    """As typed, and read only from the line too: `ROBOGLOT_DECISION_FRAMES` is not read here."""
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
    from roboglot.adapters.base import backend_name

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


def _folds(robot: RobotSession) -> bool:
    """Whether the session folds this body as it connects: what `RobotSession.connect` asks
    before its rest move, an arm with a rest pose on a session that sends things."""
    return not robot.executor.dry_run and getattr(robot.transport, "rest_pose", None) is not None


def _cancels() -> int:
    """How many times the task running the session has been asked to stop so far."""
    task = asyncio.current_task()
    return task.cancelling() if task is not None else 0


CLOSE_AFTER_A_PRESS_S = 30.0
"""How long the close of a body that does not say how long its close can take is waited for once
a press has landed on it (`_close_bound_s`). Such a body closes a socket or a link, which ends at
once; the arm, whose close releases its cameras one after another and then reads and lets go of
the arm, says its own figure."""


def _close_bound_s(transport: Any) -> float:
    """How long the robot's close is waited for once a press has landed on it. The close is what
    lets go of an arm's torque or keeps it, so a press does not cut it short; it is waited for as
    long as the body says its close can take (`close_bound_s`, the arm's counted from its cameras,
    its read and its disconnect, where one fixed figure counted a single camera), or
    `CLOSE_AFTER_A_PRESS_S` for a body that does not say. Counted from the press, which comes after
    the close began, so the close has less than that left by then. A close still going after it is
    said, and waited for no longer: as the session raises, `asyncio.run` cancels it with every
    other task. A figure that cannot be read is no figure: it is read before the close begins,
    outside every guard the close's own steps have, so what it raises must not skip the close."""
    said: object = None
    with contextlib.suppress(Exception):
        said = getattr(transport, "close_bound_s", None)
    if isinstance(said, int | float) and not isinstance(said, bool) and 0 <= said < math.inf:
        return float(said)
    return CLOSE_AFTER_A_PRESS_S


CLOSED_THROUGH_A_PRESS = (
    "the press came as the robot's transport was closing, and the close went on to its end, since "
    "it is what lets go of torque or keeps it"
)
"""Said, and recorded, when a press landed on the close and the close then ended."""

CLOSE_NOT_ENDED = (
    "the robot's transport had not finished closing {seconds:g} s after the press, so Roboglot "
    "cannot tell what the close did: keep clear of the robot, and cut its power"
)
"""Said, and recorded, when a close a press landed on was still going as long after the press as
the close can take (`_close_bound_s`)."""

CLOSE_CUT_SHORT = (
    "the robot's transport did not finish closing ({why}), so Roboglot cannot tell what the close "
    "did: keep clear of the robot, and cut its power"
)
"""Said, and recorded, when a close a press landed on was cancelled or raised before it returned:
`why` is which."""

FOLD_DID_NOT_STOP = (
    "the fold the session opened with did not stop for the press and went on to its end, so the "
    "session ends here, and nothing folds the arm again"
)
"""Said when a press during the opening fold was taken by it, which then went on to its end, at
its pose or short of it (`RobotSession.opening_fold` is `ended`), as a fold that lost the cancel in
Python 3.11's `asyncio.wait_for` does; and when the connect took a press and did not pass it on."""

CONNECTS_CLOSE_PRESSED = (
    "the press came as the session was closing the arm after that, and the close is made again here"
)
"""Said after the connect's own refusal, when the opening fold missed with nobody pressing and a
press then landed on the close the connect makes before it refuses the session
(`RobotSession.opening_fold` is `missed`)."""

_CLOSES_LEFT: set[asyncio.Future[Any]] = set()
"""A close still going past `_close_bound_s`, held until it ends or the loop's close cancels it."""


def _close_left_ended(closing: asyncio.Future[Any]) -> None:
    """A robot's close that has ended: let go of, and what it raised read and logged, as
    `RobotSession.close` goes on past a close that raises."""
    _CLOSES_LEFT.discard(closing)
    if not closing.cancelled() and (error := closing.exception()) is not None:
        logger.debug("the transport's close failed: %s: %s", type(error).__name__, error)


async def _to_its_end(
    step: Awaitable[T], *, bound_s: float | None = None
) -> tuple[asyncio.Future[T], BaseException | None]:
    """`step` in a task of its own, waited for to its end through any press, and the first press
    that came meanwhile, or None. Where `bound_s` is given, it is waited for that long once a press
    has come, and handed back still running past it.

    For what a press must not cut short, each bounded on its own as well: the stop after a press
    (`Executor.last_stop`) and the robot's close, which decides whether torque is let go of. A
    press cut each short where it landed: the stop's own line was lost, and the close left the arm
    connected and energised while the record said the session had ended, which a run's teardown
    waits through a press for (`AgentLoop._unbroken`)."""
    running = asyncio.ensure_future(step)
    press: BaseException | None = None
    until: float | None = None
    while not running.done():
        left = None if until is None else until - time.monotonic()
        if left is not None and left <= 0:
            break
        try:
            await asyncio.wait({running}, timeout=left)
        except PRESSES as e:
            press = press or e
            if until is None and bound_s is not None:
                until = time.monotonic() + bound_s
    return running, press


def _said_of_the_opening_fold(robot: RobotSession) -> tuple[str | None, str | None]:
    """What is said of the fold the session opened with, where a press ended the connect, and the
    refusal said before it, or None for each, read from what the connect saw
    (`RobotSession.opening_fold`): `FOLD_STOPPED`; `FOLD_WENT_ON` for a fold told twice that had
    not ended; `FOLD_DID_NOT_STOP` for one that took the press and went on to its end; and for a
    press on the close the connect makes after a fold that missed, its refusal and then
    `CONNECTS_CLOSE_PRESSED`. A press before any fold began is said nothing of here, and the close
    folds the arm, as a run's does.

    Read once, where the connect saw it. Read from outside as well, by wrapping the body's rest
    move, a stopped fold was said by the session's logger and by drive, and a fold that took the
    press and ended short of its pose was said to have been pressed on the connect's close, which
    the connect never reached."""
    from roboglot.release import FOLD_STOPPED, FOLD_WENT_ON

    what = robot.opening_fold
    if what == "stopped":
        return FOLD_STOPPED, None
    if what == "went on":
        return FOLD_WENT_ON, None
    if what == "ended":
        return FOLD_DID_NOT_STOP, None
    if what == "missed":
        return CONNECTS_CLOSE_PRESSED, robot.rest_refusal()
    return None, None


async def _no_press() -> None:
    """The watch the closing fold is raced against (`fold_unless_stopped`), which never ends: once
    the prompt has closed, a press reaches the fold as a cancel of the task running the session,
    and `fold_unless_stopped` answers that by telling the fold to stop twice and marking one that
    went on (`fold_went_on`)."""
    await asyncio.Event().wait()


async def _close_robot(
    robot: RobotSession,
    *,
    tell: Callable[[str], None],
    opening_fold_stopped: str | None,
    pressed: BaseException | None,
    cancels: int,
) -> BaseException | None:
    """The robot's close, in `RobotSession.close`'s order: closed to its peers, the heartbeat
    stopped, a stop, a pause waited out, the fold, and the transport closed with the note its close
    leaves. Returns the press that came during it, or the one handed in as `pressed`.

    Done here rather than by `RobotSession.close`, which lets a press through any step it lands
    on: a Ctrl-C in a session is a cancel of the task closing it, nothing else catches it, and the
    transport was never closed, with the arm energised partway through its fold and the session
    left without its `run_end`. So each step here is taken whatever a press did to the one before,
    and a press means what it means while a run tears down: one during the fold stops it where it
    is, a stop holds the arm there, and the person is told (`FOLD_STOPPED`, or `FOLD_WENT_ON` for a
    fold that had not ended when told to twice); one before the fold keeps it from starting, and
    the person is told the arm was not folded. `opening_fold_stopped` is what is said of the fold
    the session opened with where a press ended the connect after it began, and then nothing folds
    the arm again: a second fold went on to the pose the person had just stopped it short of, and
    its result took the place of the stopped one in the close's line. A press made before the
    body began its fold asked for the session's end, fold and all, as a run's does. `cancels` is
    how many cancels the task had been asked for as
    the close began, so one that a step took and did not pass on still counts as a press. A
    session nobody pressed in closes as `RobotSession.close` closes it, step for step."""
    from roboglot.adapters.base import go_to_rest_if_any
    from roboglot.agent.loop import AgentLoop
    from roboglot.mcp_server import logger as session_logger
    from roboglot.release import FOLD_STOPPED, FOLD_WENT_ON, fold_unless_stopped, fold_went_on

    executor = robot.executor

    async def step(what: str, call: Awaitable[Any]) -> None:
        nonlocal pressed
        try:
            await call
        except PRESSES as e:
            pressed = pressed or e
        except Exception as e:
            # as `RobotSession.close` goes on past each, and kept below INFO, where a session
            # nobody pressed in logged nothing of it
            logger.debug("%s: %s failed: %s: %s", robot.name, what, type(e).__name__, e)

    def stopped() -> bool:
        return pressed is not None or _cancels() > cancels

    executor.open_to_peers = False
    await step("the heartbeat's stop", robot.heartbeat.stop())
    said = opening_fold_stopped
    if opening_fold_stopped is None and not stopped():
        await step("the stop", robot.transport.stop())
    if opening_fold_stopped is None and not executor.dry_run:
        # a pause granted to a robot met is waited out first, since the rest move is not gated
        held = executor.paused_until - time.monotonic()
        while held > 0 and not stopped():  # until it is over: asyncio may wake a tick early
            await step("the wait for a pause", asyncio.sleep(held))
            held = executor.paused_until - time.monotonic()
        if stopped():
            said = AgentLoop.HELD_BACK if _folds(robot) else None
        else:
            try:
                await fold_unless_stopped(lambda: go_to_rest_if_any(robot.transport), _no_press)
            except PRESSES as e:
                pressed = pressed or e
                if _folds(robot):
                    said = FOLD_WENT_ON if fold_went_on(e) else FOLD_STOPPED
            except Exception as e:  # `go_to_rest_if_any` raises none, and this says so if it did
                logger.debug("%s: the fold failed: %s: %s", robot.name, type(e).__name__, e)
    if opening_fold_stopped is not None or stopped():
        # the stop a press is answered with, after the cancel, as a run's teardown sends it
        # (`AgentLoop._stop_after_a_press`), bounded as an owed stop is, and waited for through a
        # later press, which cut its wait short and lost what became of it
        holding, later = await _to_its_end(executor.last_stop())
        pressed = pressed or later
        try:
            unanswered = holding.result()
        except Exception as e:
            unanswered = f"{type(e).__name__}: {e}"
        if unanswered is not None:
            tell(
                f"the stop after the press: {unanswered}, so Roboglot cannot tell whether the arm "
                "is holding where it stopped"
            )
        if said is not None:
            tell(said)
    # to its end through a press, which used to cut it short where it landed: the arm stayed
    # connected and energised, and the close's line, which `FOLD_STOPPED` promises, never came
    bound = _close_bound_s(robot.transport)
    closing, during = await _to_its_end(robot.transport.close(), bound_s=bound)
    if during is not None:
        pressed = pressed or during
        # only a close that returned went on to its end: one asyncio cancelled, as its runner
        # cancels every task after a second Ctrl-C, or one that raised is done as well, and was
        # said to have ended over an arm still connected and energised
        if not closing.done():
            tell(CLOSE_NOT_ENDED.format(seconds=bound))
        elif closing.cancelled():
            tell(CLOSE_CUT_SHORT.format(why="it was cancelled"))
        elif (error := closing.exception()) is not None:
            tell(CLOSE_CUT_SHORT.format(why=f"{type(error).__name__}: {error}"))
        else:
            tell(CLOSED_THROUGH_A_PRESS)
    if not closing.done():
        _CLOSES_LEFT.add(closing)
        closing.add_done_callback(_close_left_ended)
    else:
        _close_left_ended(closing)
    if note := getattr(robot.transport, "close_note", None):
        # the note `RobotSession.close` logs, on its logger, so a session reads as it always has
        session_logger.warning("%s: %s", robot.name, note)
    return pressed


async def run_drive(
    args: DriveArgs,
    *,
    say: Callable[[str], None] = print,
    stream: TextIO | None = None,
    keyboard: bool | None = None,
    front: Front | None = None,
) -> Path:
    """Drive one robot until the person quits, and return the session's run directory.

    `front` is the page's end of the session (`roboglot.front`), where the page drives it: lines
    and Stops come from there, questions go there, and every record is read there too."""
    from roboglot.adapters.base import backend_name
    from roboglot.agent.decision.factory import (
        decision_llm_is_available,
        make_decision_llm,
        parse_decision_llm,
        resolve_decision_address,
        resolve_decision_frames,
        resolve_decision_mode,
        resolve_decision_price,
    )
    from roboglot.agent.decision.notices import MODE_ON_UNMEASURED, frames_notice
    from roboglot.agent.providers.factory import make_provider, resolve_llm
    from roboglot.agent.transcript import Transcript, new_run_dir
    from roboglot.glotfile.parser import load_glot
    from roboglot.glotfile.schema import Budgets
    from roboglot.log import EventLog, LogEvent
    from roboglot.mcp_server import RobotSession, session_memory_key, team_from_flags
    from roboglot.memory import RobotMemory
    from roboglot.registry import Registry, resolve_robot_ref
    from roboglot.safety import Budget, Executor, Heartbeat
    from roboglot.space.places import PlaceStore
    from roboglot.verbs.registry import default_registry

    ops: asyncio.Queue[Mapping[str, Any]] = asyncio.Queue()
    sessions: list[DriveSession] = []
    main = asyncio.current_task()
    # true while the robot connects, which folds an arm, and from the moment the session closes;
    # and how many of the page's Stops were taken as presses then (`arrived`)
    folding = [False]
    page_presses = [0]
    if front is not None:
        # from the first moment, so a line or a Stop the page sends while the robot is still
        # connecting waits for the session rather than being lost
        loop = asyncio.get_running_loop()

        def arrived(op: Mapping[str, Any]) -> None:
            ops.put_nowait(op)
            # The page's front end reads `ops` alone, and it has not begun while the robot
            # connects and has returned once the session closes, so a Stop then stopped nothing,
            # an arm's fold among it. It is a press there, as a Ctrl-C is.
            closing = folding[0] or (bool(sessions) and sessions[0].closing)
            if op.get("op") == "stop" and closing and main is not None and not main.done():
                page_presses[0] += 1
                main.cancel()

        def take(op: Mapping[str, Any]) -> None:
            with contextlib.suppress(RuntimeError):  # the loop has closed: the session is over
                loop.call_soon_threadsafe(arrived, op)

        front.attach_drive(take)

    most = _ceiling("max_steps")
    if args.max_steps is not None and not 1 <= args.max_steps <= most:
        raise DriveError(
            f"--max-steps is how many steps each goal may take, 1 to {most}, not {args.max_steps}"
        )
    plan = team_from_flags(
        robot=args.robot,
        registry_dir=args.registry_dir,
        glotfile=args.glotfile,
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
        map_address=args.map_address,
        fov_deg=args.fov_deg,
    )
    if len(plan.adapters) != 1:
        raise DriveError("drive drives one robot at a time: name it with --robot")
    ((name, adapter),) = plan.adapters.items()
    vla = args.controller == "vla"
    # what `roboglot run` hands the pilot's LLM, given once for every goal: only a board a person
    # named for this session or this robot, which ROBOGLOT_HOST sits below for a local preset
    llm_kw: dict[str, Any] = {
        "base_url": args.base_url,
        "api_key": args.api_key,
        "vision": args.vision,
        "extra_body": args.extra_body,
        "effort": args.effort,
        "host": plan.host.explicit,
    }
    vendor: str
    llm_id: str | None
    if vla:
        from roboglot.agent.providers.vla import NAME as VLA

        # the scripted pilot asks no LLM, so the robot's registered pilot and `ROBOGLOT_LLM`
        # name one this session never asks, as a `roboglot run --controller vla` never does
        vendor, llm_id = VLA, "scripted:goal"
    else:
        # a registered robot may name the pilot that flies it, as `roboglot run` reads it: the
        # flag first, then the robot's own, then `ROBOGLOT_LLM`
        here = resolve_robot_ref(args.robot, Registry(args.registry_dir))
        entry = here.entry
        vendor, llm_id, llm_source = resolve_llm(
            args.llm,
            entry.llm if entry is not None else None,
            robot=entry.name if entry is not None else None,
        )
        # built now whichever pilot it is, the scripted one included, so a flag it refuses (an
        # `--effort` it takes no setting for, an `--extra-body` that is no JSON object) is
        # refused before anything connects, as `roboglot run` refuses it
        built = make_provider(vendor, llm_id=llm_id, source=llm_source, **llm_kw)
    # The decision LLM: the fast lane's, as it always was, and a goal's stepper's only where the
    # command line asks for one. `ROBOGLOT_DECISION_MODE` and `ROBOGLOT_DECISION_FRAMES` are not
    # read, so a `.env` written for `roboglot run` changes no session, and a session started as
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
        # `roboglot run` refuses it before it asks whether the decision LLM is here at all
        resolve_decision_address(named[0], args.decision_url)
    chooser_llm = None
    decision_price = None
    if named is not None:
        spec, decision_llm_id = named
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
                llm_id=decision_llm_id,
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
    # drive, where `roboglot run` keeps it on, so a session of typed commands writes none
    memory = RobotMemory(memory_key, args.memory_dir) if args.memory else None
    adopted = load_glot(args.glotfile) if args.glotfile else None
    if vla and adopted is not None and POLICY_VERB not in adopted.frontmatter.verbs.allow:
        # refused here, before anything is made or connected, rather than at every line, each
        # asking whether to go ahead and then failing on the allowlist
        raise DriveError(
            "--controller vla hands each line to the arm's learned policy through "
            f"{POLICY_VERB}, and {adopted.name} does not allow it: add {POLICY_VERB} to its "
            "verbs.allow, or drive without --glotfile"
        )

    run_dir = new_run_dir(args.runs_dir, "drive", _slug(args.run_name) if args.run_name else None)
    transcript = Transcript(run_dir)
    if front is not None:
        front.run_dir(run_dir)

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
    # what is said of the fold the session opened with, where a press ended the connect after it
    # began; then nothing folds the arm again
    opening_fold_stopped: str | None = None
    opening_refused: str | None = None
    closed_on: BaseException | None = None
    connecting = False
    try:
        halted = front.halted() if front is not None else None
        if halted is not None:
            # nothing connects. A quit or the page's input ending ends a connected session as
            # asked, and so ends this one; a Stop pressed while it was set up is a run stopped
            raise Halted(_HALTED[halted], as_asked=halted != "stop")
        presses = _cancels()
        folding[0] = connecting = True
        try:
            await robot.connect()
        except PRESSES:
            # what the press did to the opening fold, as the connect saw it, said once
            opening_fold_stopped, opening_refused = _said_of_the_opening_fold(robot)
            raise
        if _cancels() > presses:
            # a press the connect took and did not pass on, which a body's own connect can do: a
            # body that folds has folded to its end since, and the session opened as if nobody
            # had pressed. It ends here, as one a press stopped the fold of ends
            if _folds(robot):
                opening_fold_stopped = FOLD_DID_NOT_STOP
            raise asyncio.CancelledError
        folding[0] = False
        if args.fov_deg:
            _given_lens(robot, float(args.fov_deg))
        registry = executor.registry
        if robot.glot is None:
            # after the connect, so the verbs are the body's own and not the default registry's
            robot.adopt(
                session_glot([v.name for v in registry.verbs() if v.safety_class != "dangerous"])
            )
            if executor.budget is not None:
                executor.budget.policy_total_s = SESSION_POLICY_S
        # by `is_allowed`, which reads an alias as the verb it names: a `.glot` allowing `walk`
        # allows `move`, and matching the names alone left it out
        verbs = {n: registry.get(n) for n in registry.names() if executor.is_allowed(n)}
        contract = robot.glot.frontmatter if robot.glot is not None else None
        # the `.glot`'s own confirm gates, which the fast lane's floors answer to as the run's
        # stepper's do (`verb_class`)
        gated = frozenset(
            registry.canonical(c) for c in (contract.verbs.confirm if contract is not None else ())
        )
        goal_verbs = sorted(n for n, v in verbs.items() if v.safety_class == GOAL_VERBS_SAFE)
        # `watch`, which the session withdrew for having nothing to look through, for goals that
        # do: shown the camera through the decision LLM, as `roboglot run` offers it on a run that
        # asked for frames, and only where the session's own `.glot`, if it has one, allows it
        manifest = executor.manifest
        watch = (
            frames
            and manifest is not None
            and WATCH in manifest.verb_names()
            and (args.glotfile is None or executor.is_allowed(WATCH))
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
                from roboglot.agent.providers.vla import VlaProvider

                # the line is its one instruction, held to the rule for one as it reached the
                # pilot (`DriveSession._vla_refusal`)
                return VlaProvider([goal], label="goal")
            if vendor == "fake":
                return make_provider("fake", task_name="goal", goal=goal, **llm_kw)
            return built

        chooser = None
        if chooser_llm is not None:
            from roboglot.agent.decision.commands import CommandChooser, offers_for
            from roboglot.drive.presets import presets_for

            warm = getattr(chooser_llm, "warm", None)
            if callable(warm):
                await warm()
            blurb = executor.manifest.blurb if executor.manifest is not None else ""
            chooser = CommandChooser(
                chooser_llm, offers_for(verbs, presets_for(executor.manifest, verbs), gated), blurb
            )
        from roboglot.drive.terminal import (
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
        for verb, ways in drive.exact.untaken.items():
            said = lanes.UNTAKEN.format(verb=verb, ways=ways, most=lanes.MAX_KEYS_PER_VERB)
            say(f"{'note':<8} {said}")
        if frames and chooser_llm is not None:
            say(f"{'note':<8} {frames_notice(decision, chooser_llm.url, watch=watch)}")
        if decision == "on":
            say(f"{'note':<8} {MODE_ON_UNMEASURED}")
        record(
            "run_start",
            glot="drive",
            # the pilot a goal is flown by, as the header of every run names its own
            provider=vendor,
            llm_id=llm_id or vendor,
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
            # terminal, so every record from a terminal reads as it did (`roboglot.front`)
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
        # every cancel the task was asked for was the page's Stop (`arrived`): no Ctrl-C was
        # made, which asyncio would end the process on, so the session ends as the page's Stop
        # ends it before the robot connects
        the_pages = isinstance(e, asyncio.CancelledError) and 0 < _cancels() == page_presses[0]
        if isinstance(e, PRESSES) and sessions and sessions[0].closing:
            # the keyboard or the page had closed its prompt and begun to close the commands, as
            # `quit`, the end of its input or a second Ctrl-C has it do: the session had ended as
            # asked, and this is a press during its close, as the same press is on a pipe
            closed_on = e
            if not the_pages:
                raise
        elif the_pages:
            outcome, reason = "aborted", PAGE_STOP_WHILE_CONNECTING
            raise DriveError(reason) from None
        else:
            outcome, reason = "aborted", f"interrupted: {type(e).__name__}"
            raise
    finally:
        # what the session was ending on, if anything, which is what leaves it whatever the
        # close meets; and the cancels asked for so far, the press that ended it among them
        ending = sys.exc_info()[1]
        cancels = _cancels()
        pressed: BaseException | None = closed_on
        # the page's Stop is a press in the close only where the robot began to connect: one sent
        # before it has refused the session already, and taken again here it said an arm nothing
        # had connected to was holding where it stopped
        folding[0] = connecting

        def tell(text: str) -> None:
            """A line for the person who pressed, which the record keeps as a run's keeps it."""
            try:
                say(f"{'brake':<8} {text}")
            except Exception as e:
                logger.warning("%s (not said: %s: %s)", text, type(e).__name__, e)
            try:
                event_log.emit("note", text=text)
            except Exception as e:
                logger.warning("%s (not recorded: %s: %s)", text, type(e).__name__, e)

        # every way out, an error and an interrupt among them, ends what is still moving the
        # body before the body is folded and let go of
        if sessions:
            try:
                await sessions[0].close()
            except PRESSES as e:
                pressed = pressed or e
            except Exception as e:  # kept below INFO, as `_close_robot`'s own steps are
                logger.debug("the session's close failed: %s: %s", type(e).__name__, e)
        if opening_refused is not None:
            tell(opening_refused)
        pressed = await _close_robot(
            robot,
            tell=tell,
            opening_fold_stopped=opening_fold_stopped,
            pressed=pressed,
            cancels=cancels,
        )
        if note := getattr(adapter, "close_note", None):
            # the close's own line, which `_close_robot` gives the logger alone, a bare line on
            # stderr: in the record too, which `roboglot log` replays and the page shows
            try:
                event_log.emit("note", text=str(note))
            except Exception as e:
                logger.warning("%s (not recorded: %s: %s)", note, type(e).__name__, e)
        spent = sessions[0].usage if sessions else None
        summary = {
            "glot": "drive",
            # a session a person ended, by quit or the end of its input, ended as asked, and one
            # that broke, a connect that failed among them, says how
            "outcome": outcome,
            "reason": reason,
            "steps": executor.budget.steps if executor.budget is not None else 0,
            # the pilot's calls and tokens over its goals, which `roboglot log` prints under the
            # session. No `cost_usd`: a session's fast lane and a goal's stepper can bill as well
            # and are not in these, so a total here would be a wrong one.
            "provider": vendor,
            "llm_calls": executor.budget.llm_calls if executor.budget is not None else 0,
            "usage": spent.model_dump() if spent is not None else {},
            "wall_s": round(time.perf_counter() - started, 3),
            "started_at": transcript.started_at_iso,
            "drive": sessions[0].stats.block() if sessions else {},
        }
        failed: list[Exception] = []

        def end(failure: str, step: Callable[[], Any]) -> None:
            """One step of the session's end, taken whatever the one before it did. A full disk
            or a run folder gone used to skip every step after the one it failed, and its
            `OSError` took the place of whatever the session was ending on. Said on stderr, since
            the record may be what failed."""
            try:
                step()
            except Exception as e:
                failed.append(e)
                logger.warning("%s: %s: %s", failure, type(e).__name__, e)

        end("the session's summary.json was not written", lambda: transcript.write_summary(summary))
        end("the session's run_end was not recorded", lambda: record("run_end", **summary))
        end("the session's record was not closed", transcript.close)
        if front is not None:
            end(
                "the page was not told the session finished",
                functools.partial(
                    front.finished, outcome, reason, run_dir=run_dir, summary=summary
                ),
            )
        the_pages = 0 < _cancels() == page_presses[0] and not isinstance(pressed, KeyboardInterrupt)
        if ending is None and not the_pages and (pressed is not None or _cancels() > cancels):
            # a press during the close, which the close carried on through, goes on up once the
            # session has ended in order, as the Ctrl-C it was; the page's Stop raises nothing
            raise pressed if pressed is not None else asyncio.CancelledError()
        if ending is None and failed:
            raise failed[0]
    return run_dir
