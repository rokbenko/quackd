"""`roboglot serve-mcp`: a robot, or a team of them, as MCP tools over stdio.

This is the second wow-demo — "I asked Claude to make the robot patrol my desk" — and it
goes through the *same* `Executor` as `.glot` runs, so allowlists, confirm gates, budgets
and the heartbeat apply to an interactive session too. Since 0.4 one server can front
several robots (`--robots duck=microduck:mock,arm=lerobot:mock`): eight `robot_*`
tools take a robot name and every robot has its own executor, budget, heartbeat and
contract. stdout is the wire, and every log line goes to stderr.

The LLM here is the client, so what it thinks never reaches this process. What Roboglot can
see it narrates (`roboglot.log`): each call that reaches an executor comes back with a
`log` list saying which gates fired, which intents went to the robot, what came back and
how long it took, and the same lines go to stderr. `--no-log` or `ROBOGLOT_LOG=0` turns
both off. A session here has no run directory and writes no `terminal.txt`: stdout belongs
to the wire, and the only screen involved is the client's.
"""

from __future__ import annotations

import asyncio
import contextlib
import contextvars
import logging
import re
import sys
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml
from mcp.server import MCPServer
from mcp.server.mcpserver import Image
from pydantic import ValidationError

from roboglot import __version__
from roboglot.adapters.base import adapter_name, backend_name, go_to_rest_if_any
from roboglot.adapters.manifest import RobotManifest, apply_datasheet_override
from roboglot.agent.prompts import body_summary, play_joints, play_paragraphs, play_voice
from roboglot.agent.transcript import png_bytes
from roboglot.glotfile.narrow import narrow_policy_verb
from roboglot.glotfile.parser import GlotParseError, load_glot, resolve_glot_path
from roboglot.glotfile.schema import Budgets, GlotFile, GlotFrontmatter
from roboglot.glotfile.validate import validate_glot
from roboglot.glotfile.widen import widened_by
from roboglot.host import HostChoice, HostError, reach_host, resolve_host, unreached
from roboglot.log import (
    EventLog,
    LogEvent,
    Sink,
    call_lines,
    cap_lines,
    capture_sink,
    capturing,
    log_enabled_default,
    render_lines,
    unless_capturing,
)
from roboglot.memory import RobotMemory
from roboglot.perception import detector_for, explicit_detector, lens_of
from roboglot.perception.base import Detector
from roboglot.safety import (
    Aborted,
    Budget,
    BudgetExceeded,
    ConfirmDenied,
    Executor,
    Heartbeat,
    VerbNotAllowed,
    VerdictRequired,
    allow_all,
    deny_all,
)
from roboglot.space.places import PlaceStore
from roboglot.space.pose import where_record
from roboglot.transport.base import (
    CameraFrame,
    RobotState,
    RobotTransport,
    TransportError,
    camera_names_of,
)
from roboglot.verbs.places import view_record
from roboglot.verbs.registry import (
    Verb,
    VerbRegistry,
    VerbResult,
    default_registry,
    registry_from_manifest,
)
from roboglot.verbs.watch import withdraw_watch
from roboglot.verdict import (
    BEFORE_VERDICT,
    MANIPULATOR_WORDS,
    MOBILITY_WORDS,
    STANDING_VERDICT_WITHDRAWN,
    TERRAIN_ORDER,
    Verdict,
    missing_needs,
    own_sheet_objection,
)

if TYPE_CHECKING:  # the factory is imported inside the functions that build robots
    from roboglot.adapters.factory import RobotSpec

logger = logging.getLogger("roboglot.mcp")


def _either(words: Sequence[str]) -> str:
    return ", ".join(words[:-1]) + f" or {words[-1]}"


NEEDS_WORDS_TEXT = (
    f"mobility is {_either(MOBILITY_WORDS)}: none means the task needs no locomotion, so an "
    "arm on a table is fine, and any means some kind. "
    f"manipulator is {_either(MANIPULATOR_WORDS)}, and none means the task touches nothing. "
    f"terrain is {_either(TERRAIN_ORDER)}, least demanding first, and a body that does not "
    "move meets indoor_flat. work_height_m is a height the hands must reach, not a minimum. "
    "carries_kg is a load that rides on the body's back or deck, and payload_kg is what a "
    "hand holds."
)
"""The words `robot_assess_task` takes, spelled out in its description.

MCP types `needs` as a bare dict, so a client is shown no enum at all and the loop's schema
descriptions (`needs_properties`) never reach it. Built from the same tuples the checker reads,
so the two cannot drift: when `none` joined the vocabulary it reached this text by itself."""

TOOL_NAMES = (
    "robot_list",
    "robot_list_verbs",
    "robot_assess_task",
    "robot_run_verb",
    "robot_observe",
    "robot_say",
    "robot_load_glotfile",
    "robot_recall",
    "robot_remember",
)
"""Every tool the server registers, in this order; `docs/guides/mcp.md` must list each one."""

INSTRUCTIONS = """You are piloting one robot through Roboglot: {names}, which is {blurb}.
This body: {datasheet}
Call robot_list_verbs first: the verbs come from that robot's own manifest, so what it can
do is what it lists and nothing else. Before the first verb that moves the body, call
robot_assess_task with your verdict on whether this body can do the task at all, judged
against that datasheet: feasible, infeasible or uncertain. robot_run_verb refuses anything
that moves the body until you have answered, and loading a .glot starts a new task and
needs a new verdict. Every action is a *verb*; the executor enforces an allowlist, budgets
and confirmation gates, so a refused call is a rule, not a bug. Prefer composite verbs
(search_scan, go_to) over micro-managing velocities. Load a .glot file with
robot_load_glotfile(path) to adopt a task contract; then follow its body as your
instructions.{memory}{places} Call robot_run_verb(verb="stop") if anything looks wrong."""

SOLO_MEMORY = """ Call robot_recall early: it is what this robot learned in earlier
sessions, and robot_remember(text) keeps one short fact for the next one."""

PLACES_NOTE = """ If robot_list_verbs lists map, the body says where it is: map tells you where
and by what, and lists its places with how far and which way, mark_place names the spot it
stands on, and go_to_place walks back to one. Trust a position as far as map says: odometry
drifts as the body walks."""
"""Said when a body's static manifest declares `map`. Conditional on the live list, since a
connection whose transport says nothing of where the body is leaves the place verbs out."""

TEAM_INSTRUCTIONS = """You are piloting {n} robot(s) through Roboglot: {names}.
Call robot_list first, then robot_list_verbs(robot) for each body you will use: verbs come
from each robot's own manifest, so they differ per robot. Every action is a *verb*; each
robot's executor enforces its own allowlist, budgets and confirmation gates, so a refused
call is a rule, not a bug. Prefer composite verbs (search_scan, go_to) over micro-managing
velocities. Load a .glot file with robot_load_glotfile(path, robot) to adopt a task
contract on one robot; then follow its body as your instructions.{memory}{places} Without a
robot argument a tool acts on the default, {default}.
Before the first verb that moves a body, call robot_assess_task(robot=...) with your verdict
on whether that body can do the task, judged against its datasheet below: feasible,
infeasible or uncertain. robot_run_verb refuses anything that moves that body until you
have answered. An infeasible verdict names, in `could`, the robots here whose datasheets
meet what the task needs: hand the task over with robot_load_glotfile(path, robot=...) and
assess it again there.
The bodies:
{datasheets}
Call robot_run_verb(verb="stop", robot=...) if anything looks wrong."""

TEAM_MEMORY = """ robot_recall(robot) is what that robot learned in earlier sessions;
robot_remember(text, robot) keeps one short fact for the next one."""

NOBODY_ASKED = "Nobody is asked here whether they are watching it, so somebody must be"
"""What this server adds to the core's warning about a body that cannot see its own fall and
cannot get up (`fall_blind_warning`). `roboglot run` and `roboglot drive` ask whether somebody is
watching such a body before it walks; a server driven by an MCP client asks nobody, moves the
body as before, and says so: on stderr to the person who started it, and to the client in the
reply to the assessment every move waits for (`RobotSession.fall_blind`)."""

GLOT_MAX_BYTES = 64 * 1024
"""The most `robot_load_glotfile` reads of a task file: a larger one is refused before it is
opened. A task file is a frontmatter and a body a person writes, and the largest of those
bundled with Roboglot is about 3 KB; this is twenty times that, so no task written by hand meets
it, and a client steered by what it read cannot have the server read a file of any size. A test
holds every bundled task under a tenth of it."""


def _load_bounded(path: str) -> GlotFile:
    """`load_glot`, for a file no larger than `GLOT_MAX_BYTES`, found as `load_glot` finds it."""
    found = resolve_glot_path(path)
    size = found.stat().st_size
    if size > GLOT_MAX_BYTES:
        raise GlotParseError(
            f"{size} bytes is larger than the {GLOT_MAX_BYTES} bytes a task file may be here, "
            "and it was not read",
            str(found),
        )
    return load_glot(str(found))


def _without_its_text(e: GlotParseError) -> str:
    """A refusal to load a task file, in words that quote nothing of the file.

    The YAML parser's own error quotes the line it stopped on, beneath a caret, and this is the
    client's to read: one steered by text it had read could have the server read any YAML file
    its user can, a line per call. Its kind and where it is say as much to the person fixing a
    task. The parser raises its refusal from the YAML error (`parse_glot_text`), which is kept
    as the refusal's context."""
    cause = e.__context__
    if not isinstance(cause, yaml.YAMLError):
        return str(e)
    mark = getattr(cause, "problem_mark", None)
    where = (
        f" at line {mark.line + 1} of the frontmatter, column {mark.column + 1}"
        if mark is not None
        else ""
    )
    return f"{e.path or '<text>'}: frontmatter is not valid YAML: {type(cause).__name__}{where}"


def _prefixed(emit: Callable[..., None], name: str) -> Callable[[str], None]:
    """Log lines from a team say which robot they are about."""

    def log_line(message: str) -> None:
        emit("%s: %s", name, message)

    return log_line


def _stderr_view(name: str) -> Sink:
    """Events that belong to no tool call, rendered to stderr the moment they happen.

    A call's own lines are logged as one block when it ends (`RobotSession._call`), because
    one coalescing view shared by concurrent calls interleaved their bursts and attributed
    one call's intents to another. What is left is the heartbeat's: its note and the stop it
    sends when the link dies. Those must render immediately and one at a time, because a
    buffered burst is only flushed by the next event, and after the heartbeat fails there is
    no next event: the emergency stop was the one line that never reached the log."""

    def write(event: LogEvent) -> None:
        for text, _style in render_lines(event, prompt=False):
            logger.info("%s: %s", name, text)

    return unless_capturing(write)


_OBSERVED: contextvars.ContextVar[list[CameraFrame] | None] = contextvars.ContextVar(
    "roboglot_mcp_observed", default=None
)
"""The frames the `robot_observe` call this task runs for has captured, or None outside one.

A context variable, as `capturing` keeps a call's events: the SDK runs every call as its own
task and the verb's task is started from it, so each call reads only what its own `observe`
captured. One slot on the session, emptied as a call started and written by every observe,
handed a call made while another waited on its detector the other's picture, under its own
summary and detections, or words with no picture at all."""


def _stash_frames(session: RobotSession) -> Callable[[Sequence[CameraFrame], str], None]:
    """The executor's `on_frames` hook: keep what `observe` captured for `robot_observe`.

    Every camera rather than the last one to arrive. The single slot this replaced held one
    picture, so a body with two cameras would have returned whichever was read second and
    called it the view. The call's own frames go to the call (`_OBSERVED`); the session keeps
    the last observe's as well, for whoever reads it after."""

    def on_frames(frames: Sequence[CameraFrame], _cause: str) -> None:
        session.last_frames = list(frames)
        if (mine := _OBSERVED.get()) is not None:
            mine[:] = frames

    return on_frames


@dataclass
class RobotSession:
    """One robot behind the server: its own executor, budget, heartbeat and contract."""

    name: str
    transport: RobotTransport
    registry: VerbRegistry
    executor: Executor
    heartbeat: Heartbeat
    detector: Detector | None = None
    glot: GlotFile | None = None
    manifest: RobotManifest | None = None
    """Set on connect when the transport is an adapter that describes itself."""
    frames: int = 0
    calls: int = 0
    log_lines: list[str] = field(default_factory=list)
    last_frames: list[CameraFrame] = field(default_factory=list)
    """What the last `observe` captured, one entry per camera. Empty when nothing was captured:
    refused, no camera, or a dry run. `robot_observe` returns each call's own (`_OBSERVED`)."""
    explicit_registry: bool = False
    """A caller-supplied registry is kept as is; otherwise the manifest builds one."""
    memory: RobotMemory | None = None
    """What this robot keeps between sessions (`roboglot memory`). None = off."""
    event_log: EventLog | None = None
    """Narrates this robot's calls to stderr and into each result's `log`. None = off."""
    locked_to: GlotFrontmatter | None = None
    """The contract `--lock-glotfile` holds this session to, or None, as it is by default, where a
    contract the client loads replaces the one before it."""
    fall_sense: RobotState | None = None
    """What the body said of itself once it had connected, read only where it can matter: on a
    body this session may walk that has no `stand_up` (`fall_blind`). None until then."""
    fall_blind_said: bool = False
    """Whether the person at the server's stderr has been told this body needs watching."""

    async def fall_blind(self) -> str | None:
        """Why somebody must watch this body while it walks, in the core's words, or None.

        The body says whether anything on it sees a fall only once it has connected (the Open
        Duck's bridge reads it from its runtime), which is after this server's instructions were
        written, so they cannot say it: the start-up line on stderr does, and the reply to the
        assessment. Read once, on the first ask, and only on a body whose task can make it walk
        and that cannot get up, as `fall_blind_warning` asks; against the task as it is now,
        since loading one may allow a walk the last did not."""
        from roboglot.agent.loop import LOCOMOTION, fall_blind_warning

        allow = self.executor.allowed
        walks = any(self.registry.canonical(n) in LOCOMOTION for n in allow)
        if not walks or "stand_up" in self.registry:
            return None
        if self.fall_sense is None:
            try:
                self.fall_sense = await self.transport.get_state()
            except Exception as e:  # asked again at the next assessment
                logger.warning(
                    "%s: whether anything on this body sees a fall could not be read: %s: %s",
                    self.name,
                    type(e).__name__,
                    e,
                )
                return None
        return fall_blind_warning(self.registry, allow, self.fall_sense)

    def say_fall_blind(self, warning: str) -> None:
        """The warning, said once on stderr to the person who started the server."""
        if not self.fall_blind_said:
            self.fall_blind_said = True
            logger.warning("%s: %s %s.", self.name, warning, NOBODY_ASKED)

    def _gate(self, name: str, gate: str, reason: str) -> None:
        """A refusal the session makes before the executor sees the call, told the same way."""
        if self.event_log is not None:
            self.event_log.emit("gate", name=name, gate=gate, outcome="refused", reason=reason)

    async def _call(
        self, tool: str, args: dict[str, Any], fn: Callable[[], Awaitable[dict[str, Any]]]
    ) -> dict[str, Any]:
        """One tool call, narrated. The SDK runs every call as its own task, and `capturing`
        is a context variable, so two calls on one robot never see each other's events.

        stderr gets the call as one block when it ends, rather than line by line as they
        happen: two concurrent calls sharing one coalescing view merged their bursts, and a
        `verb_end` from one split the other's at an arbitrary point. The result's `log` is
        the same lines, capped."""
        if self.event_log is None:
            return await fn()
        with capturing() as events:
            started = time.perf_counter()
            robot_started = self.executor._robot_now()
            self.event_log.emit("tool_call", tool=tool, robot=self.name, **args)
            payload = await fn()
            budget = self.executor.budget
            clocks: dict[str, Any] = {}
            robot_now = self.executor._robot_now()
            if robot_started is not None and robot_now is not None:
                clocks["transport_s"] = round(robot_now - robot_started, 3)
            self.event_log.emit(
                "tool_result",
                tool=tool,
                ok=bool(payload.get("ok")),
                summary=payload.get("summary"),
                elapsed_s=round(time.perf_counter() - started, 3),
                budget=budget.status() if budget is not None else None,
                **clocks,
            )
        lines = call_lines(events)
        for line in lines:
            logger.info("%s: %s", self.name, line)
        payload["log"] = cap_lines(lines)
        return payload

    def shown_name(self, verb: Verb) -> str:
        """The name a client sees: the loaded contract's own spelling when it used an alias."""
        if self.glot is not None:
            for spelled in self.executor.allowed:
                if spelled != verb.name and self.registry.canonical(spelled) == verb.name:
                    return spelled
        return verb.name

    def adopt(self, glot: GlotFile) -> None:
        """Take on a task contract. Loading a second one never refunds the first.

        `robot_load_glotfile` is a tool the *LLM* holds, so a fresh `Budget` here was the
        way out of one: a pilot that had spent its steps, or been refused a verb, could load
        a wider task and start counting from zero. The limits become the new contract's. The
        steps, the LLM calls, the clock and the failure tallies stay the session's. The seconds
        a learned policy has driven the body stay the session's from the first load on, since
        a session with no task has a policy budget of its own to spend first."""
        # "First contract" used to be spelled `budget is None`, which stopped being true when
        # a contractless session gained a default budget of its own. Ask the question
        # directly: it is the first if no task has been adopted yet.
        first = self.glot is None
        spent = self.executor.budget
        self.glot = glot
        self.executor.contract = glot.frontmatter
        if self.manifest is not None:
            self.executor.manifest = self._merged(self.manifest)
        self.executor.budget = Budget(
            glot.frontmatter.budgets,
            now=self.transport.now,
            policy_total_s=glot.frontmatter.effective_policy.total_s,
        )
        if spent is not None:
            # the seconds a policy has driven the body are the body's and not the task's, the
            # first load's included: a session with no task spends the default total, and a
            # load that zeroed it would hand the LLM a fresh `policy.total_s` for the price
            # of a tool call
            self.executor.budget.policy_s = spent.policy_s
        if first or spent is None:
            # The contract's budget is the task's, counted from when the task starts. The
            # carry-over below exists to stop a *second* load refunding a spent budget, and
            # applying it to the first would make "hello-world allows 5 steps" depend on
            # whatever happened before the task file was loaded.
            self.executor.budget.start()
            self.executor.consecutive_failures.clear()
            return
        self.executor.budget.steps = spent.steps
        self.executor.budget.llm_calls = spent.llm_calls
        self.executor.budget.started_at = spent.started_at

    def _merged(self, manifest: RobotManifest) -> RobotManifest:
        """The robot's manifest with the loaded task file's datasheet corrections folded in."""
        override = self.glot.frontmatter.datasheet if self.glot is not None else None
        return apply_datasheet_override(manifest, override)

    def effective_manifest(self) -> RobotManifest | None:
        """What the pilot should be told about this body: the merged sheet where there is one."""
        return self.executor.manifest or self.manifest

    async def connect(self) -> None:
        await self._adopt(await self.transport.connect())
        # The budget's clock is the transport's, which a body may only keep once it has
        # connected. Started again here, on the clock the session is timed by, with the
        # counts a `--glotfile` carried kept: nothing has run to count yet.
        if self.executor.budget is not None:
            self.executor.budget.start()
        # before the heartbeat starts, so the arm this session is handed is the arm the last
        # one put down rather than wherever it was left. A session that cannot get there is
        # refused: the client is about to drive a body nobody has established the pose of.
        if not self.executor.dry_run and getattr(self.transport, "rest_pose", None) is not None:
            parked = await go_to_rest_if_any(self.transport)
            logger.info("%s: %s", self.name, parked.reason)
            if parked.note:
                # the body's sentence about the pose it parks in; said once, at the start
                logger.info("%s: %s", self.name, parked.note)
            if not parked.reached:
                with contextlib.suppress(Exception):
                    await self.transport.close()
                # the close is what decides whether torque dropped, and it leaves its reason
                # behind. Carried into the refusal rather than logged: this is the one moment
                # the server refuses to start, nobody is watching the arm, and the client is
                # about to be told only that the pose was not reached.
                note = getattr(self.transport, "close_note", None)
                if note:
                    logger.warning("%s: %s", self.name, note)
                raise TransportError(
                    f"{self.name}: the arm did not reach its rest pose: {parked.reason}"
                    + (f". {note}" if note else "")
                )
        self.heartbeat.start()

    async def _adopt(self, connected: Any) -> None:
        if isinstance(connected, RobotManifest):
            # an adapter: the vocabulary is the manifest's, not the Microduck default
            self.manifest = connected
            # `self.manifest` stays the robot's own sheet, so a second `robot_load_glotfile`
            # merges from the body rather than from the previous task file's corrections
            self.executor.manifest = self._merged(connected)
            if not self.explicit_registry:
                self.registry = registry_from_manifest(connected, self.transport)
                self.executor.registry = self.registry
        if not self.explicit_registry:
            # A session has nothing to look through, since serve-mcp names no decision LLM, so
            # the body's `watch` is not offered: a verb that could only refuse
            # (`withdraw_watch`).
            withdraw_watch(self.registry, eyes=None, transport=self.transport)
        self._narrow()

    def _narrow(self) -> None:
        """Hold `manipulate` to the loaded task's `policy:` section, or to the named defaults
        when no task is loaded, so a session with no contract still runs segments of the default
        length under a timeout that covers them (`narrow_policy_verb`)."""
        narrow_policy_verb(
            self.registry,
            self.glot.frontmatter if self.glot is not None else None,
            self.transport,
        )

    async def close(self) -> None:
        """Stop the robot, then put it down and let go of it: `halt`, then `put_down`, the two
        halves a team's close runs for all its robots, every `halt` before any `put_down`
        (`_close_in_two_passes`). A press or a cancel while the stop is out keeps the fold from
        starting, and one in the fold or the transport's close ends that step; the transport is
        closed whatever came, and the press is raised once the close has run."""
        if (pressed := await _close_in_two_passes([self])) is not None:
            raise pressed

    async def halt(self) -> None:
        """The first half of the close: no more courtesy runs for robots met, the heartbeat
        stopped, then the stop.

        The stop goes out in a task of its own and is waited for as long as the executor waits
        for a stop it owes (`Executor._answer_of`, its `stop` verb's `timeout_s`), so a cancel of
        this wait never cuts it off half sent, and a link that holds it does not hold the close:
        it goes on by itself. A cancel of the wait is raised once the stop is out of its hands,
        and so is one `Heartbeat.stop` took, which it does with any that lands in it: the close
        went on to stop and fold the arm as if nothing had come."""
        me = asyncio.current_task()
        before = me.cancelling() if me is not None else 0
        # closed before the rest move, which reaches the body without passing the executor
        self.executor.open_to_peers = False
        await self.heartbeat.stop()
        taken = me is not None and me.cancelling() > before
        unstopped, cancelled = await self.executor._answer_of(_set_going(self.transport.stop))
        if unstopped is not None:
            # a stop that raised was dropped here without a word, and one that never answered
            # held the close, and every robot after it, for good
            logger.warning("%s: the close's stop: %s", self.name, unstopped)
        if cancelled or taken:
            raise asyncio.CancelledError

    async def put_down(self, *, fold: bool = True) -> None:
        """The second half of the close: a pause granted to a robot met waited out, the rest
        move, the transport's close and what the close says of the body. `fold` is False where a
        press came while the stops were out.

        A press or a cancel keeps a fold from starting if it comes first, and stops one where it
        is: the arm is held there and that is said (`AgentLoop.HELD_BACK`, `FOLD_STOPPED`), where
        the arm used to go down after a press in the pause's wait. The transport's close then
        runs to its end through any press, as a run's does (`AgentLoop._unbroken`), its note is
        said, and the first press is raised after them. A press in the fold used to leave the
        transport open, with nothing said of its torque, and one in the close to cut it short."""
        me = asyncio.current_task()
        before = me.cancelling() if me is not None else 0
        interrupted: BaseException | None = None
        if not fold:
            self._held_back()
        elif not self.executor.dry_run:
            # a pause granted to a robot met is waited out first, since the rest move is not
            # gated; then between the stop and the close, which is the only window where
            # putting the arm down changes whether it falls when torque is released
            held = self.executor.paused_until - time.monotonic()
            try:
                while held > 0:  # until it is over: asyncio may wake a clock tick early
                    await asyncio.sleep(held)
                    held = self.executor.paused_until - time.monotonic()
            except (asyncio.CancelledError, KeyboardInterrupt) as e:
                interrupted = e  # a press before the fold: it does not start
                self._held_back()
            if interrupted is None:
                try:
                    with contextlib.suppress(Exception):
                        await go_to_rest_if_any(self.transport)
                except BaseException as e:  # a press stops the fold where it is
                    interrupted = e
                    await self._held_where_the_fold_stopped()
        closing = _set_going(self.transport.close)
        late = await _outlasted({closing})
        interrupted = interrupted or late
        if not closing.cancelled() and (error := closing.exception()) is not None:
            if isinstance(error, Exception):
                logger.warning(
                    "%s: the transport's close raised %s: %s",
                    self.name,
                    type(error).__name__,
                    error,
                )
            else:
                interrupted = interrupted or error
        if note := getattr(self.transport, "close_note", None):
            logger.warning("%s: %s", self.name, note)
        if interrupted is None and me is not None and me.cancelling() > before:
            interrupted = asyncio.CancelledError()  # one a step took and did not raise
        if interrupted is not None:
            raise interrupted

    def _held_back(self) -> None:
        """Said where a press kept this body's closing fold from starting: an arm with a pose to
        fold to, held by the close's stop where it stands."""
        if self.executor.dry_run or getattr(self.transport, "rest_pose", None) is None:
            return
        from roboglot.agent.loop import AgentLoop

        logger.warning("%s: %s", self.name, AgentLoop.HELD_BACK)

    async def _held_where_the_fold_stopped(self) -> None:
        """A fold a press stopped, held where it is with a stop, bounded as the close's own is,
        and said (`FOLD_STOPPED`); whether the arm holds itself up is the close's note to say."""
        from roboglot.release import FOLD_STOPPED

        unstopped, _cancelled = await self.executor._answer_of(_set_going(self.transport.stop))
        if unstopped is not None:
            logger.warning("%s: the close's stop: %s", self.name, unstopped)
        logger.warning("%s: %s", self.name, FOLD_STOPPED)

    async def run(self, name: str, params: dict[str, Any] | None) -> dict[str, Any]:
        """`robot_run_verb`: the verb through the executor, with its log."""
        return await self._call(
            "robot_run_verb",
            {"verb": name, "params": params or {}},
            lambda: self._run(name, params),
        )

    async def _run(self, name: str, params: dict[str, Any] | None) -> dict[str, Any]:
        self.calls += 1
        # `stop` is exempt on purpose, the same way the Executor exempts it. An aborted
        # session is exactly the situation the pilot reaches for the brake in — the heartbeat
        # has just fired, and a verb that was already walking may still be finishing — and
        # refusing `stop` here closed the only control the tool surface offers.
        if self.executor.abort.is_set() and self.registry.canonical(name) != "stop":
            # say *what* went wrong. The heartbeat's own note goes to stderr and reaches no
            # call's log (its task predates every `capturing` block), so without this the
            # pilot was told only that the session had aborted, never that the link had died.
            why = self.heartbeat.failure
            reason = (
                f"session aborted: the heartbeat failed ({why}); restart Roboglot. "
                if why is not None
                else "session aborted (kill switch or abort_when); restart Roboglot. "
            ) + "`stop` still works and is worth sending."
            self._gate(name, "session_aborted", reason)
            return _result(VerbResult.fail(reason))
        try:
            result = await self.executor.run_verb(name, params or {}, source="mcp")
        except VerdictRequired as e:
            result = VerbResult.fail(
                f"{e}: call robot_assess_task(robot={self.name!r}, verdict=...) first"
            )
        except VerbNotAllowed as e:
            result = VerbResult.fail(str(e))
        except ConfirmDenied as e:
            result = VerbResult.fail(
                f"{e}: this verb needs human confirmation; "
                "start `roboglot serve-mcp --yes` to allow it"
            )
        except BudgetExceeded as e:
            result = VerbResult.fail(f"budget exhausted: {e}")
        except Aborted as e:
            why = self.heartbeat.failure
            result = VerbResult.fail(
                f"aborted: {e}" + (f" (the heartbeat failed: {why})" if why is not None else "")
            )
        except TransportError as e:
            # the read a verb's gates begin with failed, and the stop that follows has been tried,
            # though an arm that cannot be read gets no goal from it. Left to the SDK, the chat
            # learned only that the tool had failed, on `stop` too.
            result = VerbResult.fail(str(e))
        return _result(result)

    async def assess(self, arguments: dict[str, Any], could: dict[str, str]) -> dict[str, Any]:
        """`robot_assess_task`: the pilot's verdict on this task against this body.

        `could` is the other robots in the team whose datasheets meet what the task needs,
        already matched by the caller, which is the only place that knows the team."""
        return await self._call(
            "robot_assess_task", dict(arguments), lambda: self._assess(arguments, could)
        )

    def _refused_assessment(self, why: str) -> dict[str, Any]:
        """An assessment refused for its shape, which withdraws the verdict standing before it,
        as the loop's does (`AgentLoop._refused_assessment`), and says so."""
        if self.executor.verdict is not None:
            self.executor.verdict = None
            why = f"{why}; {STANDING_VERDICT_WITHDRAWN}"
        return {"ok": False, "robot": self.name, "summary": why}

    async def _assess(self, arguments: dict[str, Any], could: dict[str, str]) -> dict[str, Any]:
        if "human" in arguments:
            return self._refused_assessment(
                "assess_task takes no `human` field: only a person sets it"
            )
        try:
            verdict = Verdict.model_validate(arguments)
        except ValidationError as e:
            msgs = "; ".join(
                f"{'.'.join(map(str, err['loc']))}: {err['msg']}" for err in e.errors()
            )
            return self._refused_assessment(f"invalid verdict: {msgs}")
        if verdict.verdict == "feasible":
            # the loop's check, on this surface too. `robot_assess_task` matched `needs`
            # against every OTHER robot in the team to fill in `could`, and recorded a
            # feasible against this robot's own sheet without ever looking at it.
            objection = own_sheet_objection(
                verdict.needs, self.effective_manifest(), tool="robot_assess_task"
            )
            if objection is not None:
                # the gate shuts, as it does in the loop: an earlier `feasible` left standing
                # would refuse the words and not the motion
                self.executor.verdict = None
                return {"ok": False, "robot": self.name, "summary": objection}
        self.executor.verdict = verdict
        payload: dict[str, Any] = {
            "ok": True,
            "robot": self.name,
            "verdict": verdict.verdict,
            "summary": verdict.summary(),
            "pending": verdict.verdict == "uncertain",
            "could": sorted(could),
            "could_datasheets": could,
        }
        if verdict.verdict != "infeasible" and (warning := await self.fall_blind()) is not None:
            # in the reply every move waits for, since the instructions could not say it
            payload["warning"] = (
                f"{warning} {NOBODY_ASKED}: tell the person you are chatting with before "
                "anything moves it."
            )
            self.say_fall_blind(warning)
        if verdict.verdict == "uncertain":
            # `--yes` answers a confirm gate because there is no terminal to ask on. Here
            # there is a person, reachable through the LLM, which is better than a flag.
            payload["note"] = (
                "recorded as uncertain, which does not clear verbs that move the body. There "
                "is no terminal here: ask the person you are chatting with, then call "
                "robot_assess_task again with feasible on your own responsibility, or with "
                "infeasible."
            )
        elif verdict.verdict == "infeasible":
            payload["note"] = f"nothing on {self.name} will move for this task. " + (
                f"These robots meet what it needs: {', '.join(sorted(could))}. Load the "
                "task on one of them with robot_load_glotfile(path, robot=...) and assess "
                "it again there."
                if could
                else "No robot in this team meets what it needs; tell the user."
            )
        else:
            payload["note"] = "verbs that move the body now run."
        self._emit_assess(verdict)
        return payload

    def _emit_assess(self, verdict: Verdict) -> None:
        if self.event_log is not None:
            self.event_log.emit("assess", **verdict.model_dump(mode="json"), ends_run=False)

    async def info(self, *, default: bool) -> dict[str, Any]:
        m = self.effective_manifest()
        healthy: bool | None = None
        reason: str | None = None
        health = getattr(self.transport, "health", None)
        if health is not None:
            try:
                h = await health()
                healthy, reason = bool(h.ok), h.reason
            except Exception as e:  # informational: a sick robot is a row, not a crash
                healthy, reason = False, str(e)
        return {
            "name": self.name,
            "adapter": adapter_name(self.transport),
            "backend": backend_name(self.transport),
            "vendor": m.vendor if m else None,
            "model": m.model if m else None,
            "embodiment": m.embodiment if m else None,
            "mobility": m.mobility if m else None,
            "manifest_id": m.id if m else None,
            "digest": m.digest() if m else None,
            "datasheet": m.datasheet.model_dump(mode="json") if m and m.datasheet else None,
            "datasheet_text": body_summary(m) if m else None,
            "contract": self.glot.name if self.glot else None,
            "healthy": healthy,
            "health_reason": reason,
            "aborted": self.executor.abort.is_set(),
            "default": default,
            # which detector reads this robot's frames, the board's included, so a client
            # can tell "nothing seen" on the laptop's colour detector from the Jetson's YOLO
            "detector": getattr(self.detector, "name", None) if self.detector else None,
        }

    def verbs_payload(self) -> dict[str, Any]:
        reg = self.registry
        aliases = reg.aliases()
        return {
            "robot": self.name,
            "contract": self.glot.name if self.glot else None,
            "manifest_id": self.manifest.id if self.manifest else None,
            "verbs": [
                {
                    "name": self.shown_name(v),
                    "canonical": v.name,
                    "aliases": [a for a, c in aliases.items() if c == v.name],
                    "core": v.core,
                    "before_verdict": v.name in BEFORE_VERDICT or v.read_only,
                    "kind": v.kind,
                    "safety_class": v.safety_class,
                    "allowed": self.executor.is_allowed(v.name),
                    "description": v.description,
                    "params": v.tool_schema()["input_schema"],
                }
                for v in reg.verbs()
            ],
        }

    async def observe(self) -> list[str | Image]:
        """The `observe` verb through the executor, then the frames it captured, then the
        log as one text block (this tool returns content, not a dict). The frames are this
        call's own (`_OBSERVED`), whatever another call on this robot captures meanwhile."""
        self.last_frames = []
        captured: list[CameraFrame] = []
        mine = _OBSERVED.set(captured)
        try:
            result = await self._call("robot_observe", {}, lambda: self._run("observe", {}))
        finally:
            _OBSERVED.reset(mine)
        pngs: list[bytes] = []
        if result["ok"] and captured:
            # in a worker, as a run's frames are (`Transcript.save_frames_off_loop`): this loop
            # runs every robot's heartbeat and every verb still sending, and a full frame per
            # camera held it for as long as the encodes took
            pngs = await asyncio.to_thread(lambda: [png_bytes(f.image) for f in captured])
        content: list[str | Image]
        if not pngs:
            # refused, no camera, or a dry run (nothing was captured): words only
            content = [f"{self.name}: {result['summary']}"]
        elif len(captured) == 1 and len(camera_names_of(self.transport)) < 2:
            # the body's cameras rather than this step's frames: a two-camera arm down to one
            # lens takes the named branch below, because an unnamed picture from a body with
            # two views is the one thing the naming exists to prevent
            self.frames += 1
            summary = str(result["summary"]).removeprefix("frame captured; ")
            content = [
                f"{self.name} camera: {summary}",
                Image(data=pngs[0], format="png"),
            ]
        else:
            # several cameras: each picture is named, and the summary is the primary's,
            # because the detector reads one view and a bearing from another means nothing
            self.frames += len(captured)
            # the body's camera list, not the frames that arrived. Naming the first picture
            # as the primary is only true when the primary is the one that answered: if it is
            # the lens that died, calling the survivor primary and handing it the detections
            # turns an unnamed picture into a mislabelled one, which is worse than what this
            # branch was written to fix. The detections belong to the primary either way, and
            # when it gave nothing there are none.
            here = camera_names_of(self.transport) or [f.name for f in captured]
            arrived = [f.name for f in captured]
            primary = next((f.name for f in captured if f.primary), None)
            summary = re.sub(r"^frames? captured[^;]*; ", "", str(result["summary"]))
            whose = (
                f"{primary} is the primary, the detections are its"
                if primary is not None
                else f"{here[0]} is the primary and gave nothing this step, "
                "so there are no detections"
            )
            content = [f"{self.name} cameras {', '.join(arrived)} ({whose}): {summary}"]
            for frame, png in zip(captured, pngs, strict=True):
                content.append(f"camera {frame.name}:")
                content.append(Image(data=png, format="png"))
        if result.get("log"):
            content.append("log:\n" + "\n".join(result["log"]))
        return content

    async def say(self, text: str) -> dict[str, Any]:
        async def inner() -> dict[str, Any]:
            if self.manifest is not None and "sound" not in self.manifest.intents:
                reason = f"{self.name} ({self.manifest.model}) has no sound intent"
                self._gate("say", "no_sound_intent", reason)
                return {"ok": False, "summary": reason, "data": {}}
            return await self._run("say", {"text": text})

        return await self._call("robot_say", {"text": text}, inner)

    def recall(self) -> dict[str, Any]:
        if self.memory is None:
            return {"ok": False, "robot": self.name, "summary": "memory is off for this server"}
        book = self.executor.place_book()
        text = self.memory.recall(where_said=book.where_words if book else None)
        out: dict[str, Any] = {
            "ok": True,
            "robot": self.name,
            "summary": text or "nothing remembered yet: this is the first session on this robot",
            "notes": [e.text for e in self.memory.notes()[-20:]],
            "episodes": [e.text for e in self.memory.episodes()[-5:]],
            "path": str(self.memory.path),
        }
        if book is not None:
            # the places this robot knows, read against where it is now, on a body that says
            out["places"] = [view_record(v) for v in book.views()]
        return out

    def remember(self, text: str, tags: list[str] | None = None) -> dict[str, Any]:
        if self.memory is None:
            return {"ok": False, "robot": self.name, "summary": "memory is off for this server"}
        # where the robot stands, read off the robot and never from the pilot's arguments
        book = self.executor.place_book()
        fix = book.source.fix() if book is not None else None
        try:
            entry = self.memory.remember(
                text,
                tags=tags,
                task=self.glot.name if self.glot else None,
                where=where_record(fix) if fix is not None and fix.localized else None,
            )
        except (ValueError, OSError) as e:
            return {"ok": False, "robot": self.name, "summary": f"could not remember: {e}"}
        return {
            "ok": True,
            "robot": self.name,
            "summary": f"remembered for future sessions: {entry.text}",
            "notes": len(self.memory.notes()),
        }

    def load(self, path: str) -> dict[str, Any]:
        try:
            glot = _load_bounded(path)
        except GlotParseError as e:
            return {"ok": False, "error": _without_its_text(e)}
        if glot.frontmatter.team is not None:
            # same guard as serve(): one MCP pilot must not adopt a many-robot contract
            return {
                "ok": False,
                "error": (
                    "team tasks are not available over MCP (this session is one pilot, and a "
                    "team has one LLM per robot rather than one for all of them). Run it "
                    f"with: roboglot run {path}"
                ),
            }
        if self.manifest is not None:
            problems = validate_glot(glot, [self.manifest], registry=self.registry)
            if problems:
                return {
                    "ok": False,
                    "error": "; ".join(p.message for p in problems),
                    "problems": [p.message for p in problems],
                }
        if self.locked_to is not None:
            wider = widened_by(self.locked_to, glot.frontmatter, self.registry.canonical)
            if wider:
                return {
                    "ok": False,
                    "error": (
                        f"this server holds {self.name} to the contract it started with "
                        f"(--lock-glotfile), and this file asks for more: {'; '.join(wider)}"
                    ),
                }
        # Validated, so its policy section is the one the verb is held to from now on. Narrowed
        # before anything is adopted, so a file this body's verb cannot be held to leaves the
        # session's task, its verdict and its verb as they were; and nothing is awaited from
        # here to the verdict being cleared, so no call runs under the one and not the other.
        try:
            narrow_policy_verb(self.registry, glot.frontmatter, self.transport)
        except ValueError as e:
            return {"ok": False, "error": str(e)}
        reloaded = self.executor.budget is not None
        self.adopt(glot)
        # a new task is a new question about this body; the last task's verdict does not
        # carry over, and the gate shuts again until the pilot answers for this one
        self.executor.verdict = None
        note = (
            f"The executor now enforces this contract for every call to {self.name}. This is "
            "a new task: assess it with robot_assess_task before anything moves."
        )
        budget = self.executor.budget
        if reloaded and budget is not None:
            # say it, so a human reading the session sees the carry-over rather than
            # wondering why the new contract's budget is already part spent
            note += f" What this session already spent still counts: {budget.status()}."
        effective = self.effective_manifest()
        played = glot.frontmatter.play
        if played:
            note += (
                " This task may give the robot a part to play: read the play field before you "
                "assess it."
            )
        result: dict[str, Any] = {
            "ok": True,
            "robot": self.name,
            "name": glot.name,
            # without `play` for a file that does not play, so loading any task file written
            # before there was such a key reads exactly the result it always did
            "contract": glot.frontmatter.model_dump(exclude=None if played else {"play"}),
            "instructions": glot.body,
            "datasheet_text": body_summary(effective) if effective is not None else None,
            "note": note,
        }
        if played:
            # The client's own prompt frames this pilot, so what a played run reads in its
            # system prompt arrives here, worded for a chat by the same function. The facts
            # are this session's: a body with no manifest is taken as one that moves, as the
            # run's prompt takes it, and a voice or `move_joints` counts only where this body
            # has the verb.
            allowed = [v for v in glot.frontmatter.verbs.allow if v in self.registry]
            result["play"] = "\n\n".join(
                play_paragraphs(
                    still=effective is not None and effective.mobility == "none",
                    voice=play_voice(allowed),
                    memory=self.memory is not None,
                    joints=play_joints(allowed),
                    mcp=True,
                )
            )
        return result


def _result(r: VerbResult) -> dict[str, Any]:
    return {"ok": r.ok, "summary": r.summary, "data": r.data}


def _set_going(send: Callable[[], Awaitable[Any]]) -> asyncio.Future[Any]:
    """`send` in a task of its own, as the executor sets an owed stop going (`_start_stop`): one
    that raises as it is called is a failed send, as one that raises later is."""
    try:
        return asyncio.ensure_future(send())
    except Exception as e:
        failed: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        failed.set_exception(e)
        return failed


async def _outlasted(tasks: set[asyncio.Future[Any]]) -> BaseException | None:
    """Wait until every one of `tasks` has ended, whatever cancel or press the waiting task is
    sent meanwhile, and return the first it was sent, or None. `asyncio.wait` hands none of
    them on, so each task ends as it would have. Every task here is bounded by its own clock."""
    first: BaseException | None = None
    pending = {t for t in tasks if not t.done()}
    while pending:
        try:
            _done, pending = await asyncio.wait(pending)
        except (asyncio.CancelledError, KeyboardInterrupt) as e:
            first = first or e
            pending = {t for t in pending if not t.done()}
    return first


async def _close_in_two_passes(sessions: Sequence[RobotSession]) -> BaseException | None:
    """End a session's robots: every robot's `halt` at once, then each robot's `put_down`, in
    order. Returns the first press or cancel that came, for the caller to raise.

    One robot after another, each close waited out a granted pause and folded its arm before the
    next robot was sent its stop, and a stdio client ends a server a fixed grace after it closes
    its stdin (the MCP SDK's own client: two seconds, `PROCESS_TERMINATION_TIMEOUT`), so a robot
    late in the list, driving on its last command, could be left moving. Now every stop has gone
    out, and been answered or left to go on by itself, before any pause is waited out or any fold
    starts. A press that came while the stops were out keeps every fold from starting. One that
    lands in a robot's fold or close ends that step and no other: each robot is still put down
    and closed, and the first press is raised once all of them have been."""
    halts = {_set_going(session.halt): session for session in sessions}
    pressed = await _outlasted(set(halts))
    for halt, session in halts.items():
        if halt.cancelled():
            pressed = pressed or asyncio.CancelledError()
        elif isinstance(error := halt.exception(), Exception):
            logger.warning(
                "%s: the close's stop: %s: %s", session.name, type(error).__name__, error
            )
        elif error is not None:
            pressed = pressed or error
    interrupted: BaseException | None = None
    for session in sessions:
        try:
            await session.put_down(fold=pressed is None)
        except BaseException as e:  # handed back, once every robot has been closed
            interrupted = interrupted or e
    return pressed or interrupted


async def _closed(sessions: Sequence[RobotSession]) -> None:
    """`_close_in_two_passes` in a task of its own, waited for whatever this task is sent.

    `serve-mcp` closes its robots in the lifespan's `finally`, inside the task group the MCP SDK
    opens for stdio. When the client dies with a call in flight, the group's `stdout_writer`
    raises on a write nobody reads, and anyio cancels the group's host task again at every turn
    of the loop until it ends. The close ran in that task and was cut at its first wait: no robot
    after the first one's stop was stopped, folded or closed. A cancel sent here now ends
    nothing; each step of the close is bounded by its own clock, and the first cancel or press
    is raised once it has run. A person's Ctrl-C reaches the close itself as asyncio's runner
    hands it on, cancelling every task at a second press: it keeps a fold from starting, or
    stops one where it is, and every transport is still closed (`RobotSession.put_down`).

    A process killed outright, as a stdio client does a fixed grace after it closes stdin, runs
    none of this. Its robots are left as their last command left them, and the order is the
    answer there: every stop goes out first, in the close's first turns of the loop, so a kill
    that comes during a pause or a fold finds every robot already sent its stop."""
    closing = asyncio.ensure_future(_close_in_two_passes(sessions))
    sent = await _outlasted({closing})
    if closing.cancelled():
        ended: BaseException | None = asyncio.CancelledError()
    else:
        ended = closing.exception() or closing.result()
    if (first := sent or ended) is not None:
        raise first


@dataclass
class ServedTeam:
    sessions: dict[str, RobotSession]
    default: str
    static: dict[str, RobotManifest] = field(default_factory=dict)
    """What each robot says about itself before it is asked: `describe()`, from the CLI. The
    instructions are built before `connect_all` runs, so this is what they can read."""
    before_connect: Callable[[str], None] | None = None
    """Handed a robot's name just before `connect_all` connects it, and never for a robot whose
    connect does not come: `serve` says there that a robot is on a backend its adapter is
    retiring."""

    def described(self, name: str) -> RobotManifest | None:
        """The best manifest for this robot right now: the live one, else the static one."""
        session = self.sessions.get(name)
        live = session.effective_manifest() if session is not None else None
        return live or self.static.get(name)

    def get(self, name: str | None) -> RobotSession | None:
        return self.sessions.get(name or self.default)

    def unknown(self, name: str | None) -> dict[str, Any]:
        return {
            "ok": False,
            "error": f"unknown robot {name!r}; robots: {', '.join(self.sessions)}",
        }

    async def connect_all(self) -> None:
        """Sequential and fail-fast: a team with a hole in it is not served.

        So a robot after the first that fails is never connected, and `before_connect` is
        never handed it: what is said of a robot just before its connect is said only of a
        robot whose connect comes."""
        connected: list[RobotSession] = []
        for name, session in self.sessions.items():
            try:
                if self.before_connect is not None:
                    self.before_connect(name)
                await session.connect()
            except BaseException:
                # closed as a session's end closes its robots, so a press in one robot's close
                # does not leave the next one open
                await _closed(connected)
                raise
            connected.append(session)

    async def close_all(self) -> None:
        """Every robot stopped, then each one put down and closed, whatever cancel the task
        that asks for it is sent meanwhile (`_closed`)."""
        await _closed(list(self.sessions.values()))


def _pick_default(robots: Mapping[str, Any]) -> str:
    """The only robot; else the first Microduck, because a caller that names no robot on
    a mixed team most likely means the duck; else the first declared."""
    names = list(robots)
    if len(names) == 1:
        return names[0]
    for name, transport in robots.items():
        if adapter_name(transport) in (None, "microduck"):
            return name
    return names[0]


def _instructions(team: ServedTeam) -> str:
    """One robot gets a prompt about that robot, whichever body it is.

    Until 0.5 the solo prompt was hardcoded to a 25 cm Microduck, which was wrong for every
    other body. The description now comes from the manifest's own blurb, and the datasheet
    from `ServedTeam.static`: these instructions are built when the server is, which is before
    anything has connected, so a live manifest is not there to read yet and the static
    description is the honest source."""
    # with --no-memory both tools answer "memory is off", so telling the pilot to call
    # them early is an instruction to waste a turn
    on = any(s.memory is not None for s in team.sessions.values())
    described = {name: team.described(name) for name in team.sessions}
    places = (
        PLACES_NOTE
        if any(m is not None and "map" in {v.name for v in m.verbs} for m in described.values())
        else ""
    )
    if len(team.sessions) == 1:
        manifest = described[team.default]
        blurb = manifest.blurb if manifest and manifest.blurb else "a small robot"
        return INSTRUCTIONS.format(
            names=team.default,
            blurb=blurb,
            datasheet=body_summary(manifest) if manifest else "no datasheet until it connects.",
            memory=SOLO_MEMORY if on else "",
            places=places,
        )
    newline = "\n"
    return TEAM_INSTRUCTIONS.format(
        n=len(team.sessions),
        names=", ".join(team.sessions),
        default=team.default,
        memory=TEAM_MEMORY if on else "",
        places=places,
        datasheets=newline.join(
            f"- {name}: {body_summary(m) if m else 'no datasheet until it connects.'}"
            for name, m in described.items()
        ),
    )


def session_memory_key(
    name: str, transport: RobotTransport, memory_keys: Mapping[str, str] | None = None
) -> str:
    """The key a session keeps a robot's memory and places under: its registered name where
    `memory_keys` gives one, so two registered robots of one kind keep their own, and
    adapter:backend otherwise, so a mock never inherits a real body's."""
    return (memory_keys or {}).get(name) or (
        f"{adapter_name(transport) or name}:{backend_name(transport)}"
    )


def build_team_server(
    robots: Mapping[str, RobotTransport],
    *,
    glotfile: str | None = None,
    lock_glotfile: bool = False,
    dry_run: bool = False,
    yes: bool = False,
    registry: VerbRegistry | None = None,
    detector: Detector | None = None,
    heartbeat_period_s: float = 0.5,
    default: str | None = None,
    memory: bool = True,
    memory_dir: str | Path | None = None,
    memory_keys: Mapping[str, str] | None = None,
    log: bool = True,
    manifests: Mapping[str, RobotManifest] | None = None,
    before_connect: Callable[[str], None] | None = None,
) -> tuple[MCPServer, ServedTeam]:
    """One MCP server over several robots, each behind its own executor.

    `--yes` and `--dry-run` are global; contracts, budgets and abort flags are per robot.
    A `.glot` given at startup is adopted by the default robot. With `memory` on, each
    robot gets its `RobotMemory` (keyed adapter:backend, so a mock never inherits a real
    body's notes, or by its registered name where `memory_keys` gives one,
    so two registered robots of one kind keep separate notes) behind `robot_recall` /
    `robot_remember`. With `log` on,
    each robot narrates its calls: a `log` list in every result that reached its executor,
    and the same lines on stderr in place of the executor's own log lines.

    `before_connect` is handed each robot's name just before the server's start connects it
    (`ServedTeam.connect_all`)."""
    if not robots:
        raise ValueError("an MCP server needs at least one robot")
    sessions: dict[str, RobotSession] = {}
    for name, transport in robots.items():
        event_log: EventLog | None = None
        if log:
            event_log = EventLog(observers=[_stderr_view(name), capture_sink])
        reg = registry or default_registry()
        if registry is None:
            # the vocabulary a session shows before its robot connects has no `watch` either
            withdraw_watch(reg, eyes=None, transport=transport)
        det = detector
        # A session with no `.glot` has no contract, and used to have no Budget either — so
        # `roboglot serve-mcp --robot open_duck:bridge`, which is the setup this module's own
        # docstring advertises, handed an MCP client unlimited, uncounted control of a
        # physical biped. The default budget is generous; what matters is that it is finite
        # and that the step count is visible. Loading a task file replaces it with the contract's.
        # Its clock starts again at the connect, on the clock the robot keeps once connected.
        budget = Budget(Budgets(), now=transport.now)
        budget.start()
        memory_key = session_memory_key(name, transport, memory_keys)
        executor = Executor(
            registry=reg,
            transport=transport,
            contract=None,
            budget=budget,
            detector=det,
            dry_run=dry_run,
            confirm=allow_all if yes else deny_all,
            # with the log on, its lines replace the executor's own (which would say the
            # same verb twice on stderr); those drop to DEBUG rather than vanish
            log=_prefixed(logger.debug if log else logger.info, name),
            event_log=event_log,
            # beside the memory file and keyed as it is; with memory off, places last as long
            # as the session, and a dry run marks none and writes nothing
            places=PlaceStore(memory_key, memory_dir, persist=memory and not dry_run),
        )
        # the pilot is offered `robot_assess_task`, so the executor holds it to the answer
        executor.require_verdict = True
        heartbeat = Heartbeat(
            transport,
            executor.abort,
            period_s=heartbeat_period_s,
            log=_prefixed(logger.warning, name),
            event_log=event_log,
        )
        session = RobotSession(
            name=name,
            transport=transport,
            registry=reg,
            executor=executor,
            heartbeat=heartbeat,
            detector=det,
            explicit_registry=registry is not None,
            memory=RobotMemory(memory_key, memory_dir) if memory else None,
            event_log=event_log,
        )
        executor.on_frames = _stash_frames(session)
        sessions[name] = session
    team = ServedTeam(
        sessions,
        default or _pick_default(robots),
        dict(manifests or {}),
        before_connect=before_connect,
    )
    if team.default not in sessions:
        raise ValueError(f"default robot {team.default!r} is not one of {list(sessions)}")
    if glotfile:
        started_with = load_glot(glotfile)
        sessions[team.default].adopt(started_with)
        if lock_glotfile:
            sessions[team.default].locked_to = started_with.frontmatter

    @contextlib.asynccontextmanager
    async def lifespan(_server: MCPServer) -> AsyncIterator[ServedTeam]:
        await team.connect_all()
        for session in team.sessions.values():
            # now that the robot has said what it actually has, not what its description
            # claims. `build_team_server` can only see a bare transport's backend.
            live = getattr(session.transport, "manifest", None)
            if live is not None:
                # a detector the server was given is kept. One `explicit_detector` built
                # before connect learns the lens the body reported: the board's, when the
                # board's camera is the only one it has. One handed to `build_team_server`
                # from Python keeps the lens it was built with, as `run_glot` keeps it.
                lens_fov, assumed = lens_of(live, None)
                session.detector = detector_for(
                    live.sensors,
                    session.detector,
                    fov_deg=lens_fov,
                    backend=live.backend,
                    assumed=assumed,
                )
                calibrate = getattr(session.detector, "calibrate", None)
                # only a body with a camera has a lens to learn; one without keeps a detector
                # that reads nothing, and a warning about its field of view would be about
                # a camera it has not got
                if (
                    getattr(session.detector, "lens_at_connect", False) is True
                    and callable(calibrate)
                    and "camera" in live.sensors
                ):
                    calibrate(lens_fov, backend=live.backend, assumed=assumed)
                session.executor.detector = session.detector
            logger.info(
                "Roboglot MCP server up: robot=%s transport=%s dry_run=%s detector=%s",
                session.name,
                backend_name(session.transport),
                dry_run,
                getattr(session.detector, "name", None) if session.detector else None,
            )
        try:
            for session in team.sessions.values():
                # under the start-up lines, the first moment it is known, and inside the `try`:
                # it reads the body, and a cancel there must still close every robot
                if (warning := await session.fall_blind()) is not None:
                    session.say_fall_blind(warning)
            yield team
        finally:
            await team.close_all()

    mcp = MCPServer(
        "roboglot", instructions=_instructions(team), version=__version__, lifespan=lifespan
    )

    def me() -> RobotSession:
        return team.sessions[team.default]

    # ── the team tools ─────────────────────────────────────────────────────────────

    @mcp.tool(
        description="Every robot this server fronts (adapter, body, manifest, contract, "
        "health) and which one is the default. Call this first."
    )
    async def robot_list() -> dict[str, Any]:
        return {
            "robots": [
                await s.info(default=(name == team.default)) for name, s in team.sessions.items()
            ],
            "default": team.default,
        }

    @mcp.tool(
        description="One robot's verbs from its own manifest: params, safety class, "
        "canonical name, aliases, and whether its contract allows each now."
    )
    async def robot_list_verbs(robot: str | None = None) -> dict[str, Any]:
        session = team.get(robot)
        return session.verbs_payload() if session else team.unknown(robot)

    @mcp.tool(
        description=(
            "Your verdict on whether one robot can do the task, judged against the datasheet "
            "in its robot_list row: the task's needs against that body's limits, not whether "
            "you have found the target yet. Required before the first verb that moves it: "
            "until you answer, robot_run_verb refuses anything that moves it and says so, and "
            "only the verbs robot_list_verbs marks before_verdict run. "
            "feasible: go. infeasible: nothing on that robot will move, so name the limit and "
            "what you estimated, and read `could` for a robot here that meets what the task "
            "needs. uncertain: only when the verdict itself turns on a figure you cannot judge "
            "from here, the mass or size of a thing that decides a limit and that you have not "
            "seen, or a limit listed as not published. Not having found the target yet is not "
            "by itself one of those. Ask the person you are chatting with, then answer again. "
            "Fill "
            "in `needs` (payload_kg, carries_kg, reach_m, manipulator, mobility, ...) even when "
            "feasible, because that is what names the robots that could, and name only what "
            "the task turns on: leave a field out, or give 0 or none, when the task does not "
            "need it. "
            + NEEDS_WORDS_TEXT
            + " A feasible whose needs that robot's own datasheet does not meet is refused "
            "before it is recorded, and names the need."
        )
    )
    async def robot_assess_task(
        verdict: str,
        reason: str,
        limits_consulted: list[str] | None = None,
        estimates: list[dict[str, Any]] | None = None,
        needs: dict[str, Any] | None = None,
        robot: str | None = None,
    ) -> dict[str, Any]:
        session = team.get(robot)
        if session is None:
            return team.unknown(robot)
        arguments: dict[str, Any] = {"verdict": verdict, "reason": reason}
        if limits_consulted is not None:
            arguments["limits_consulted"] = limits_consulted
        if estimates is not None:
            arguments["estimates"] = estimates
        if needs is not None:
            arguments["needs"] = needs
        could: dict[str, str] = {}
        if needs:
            for name in team.sessions:
                other = team.described(name)
                # this list names bodies to hand a task to, and Roboglot drives no part of a
                # described one (ADR-0077)
                drivable = other is not None and not other.extras.get("described")
                if name != session.name and other is not None and drivable:
                    try:
                        if not missing_needs(needs, other):
                            could[name] = body_summary(other)
                    except ValueError:  # a need outside the vocabulary: the verdict refuses it
                        could = {}
                        break
        return await session.assess(arguments, could)

    @mcp.tool(
        description="Run a verb on one robot through its executor, with JSON params. "
        "Refusals come back as ok=false; a verb its manifest lacks is a refusal too. "
        "`log` lists what happened behind the scenes: gates, intents sent, timing."
    )
    async def robot_run_verb(
        verb: str, params: dict[str, Any] | None = None, robot: str | None = None
    ) -> dict[str, Any]:
        session = team.get(robot)
        return await session.run(verb, params) if session else team.unknown(robot)

    @mcp.tool(
        description="The observe verb on one robot, through its executor: the camera frame "
        "as a PNG plus a detection summary, then a log block of what happened.",
        structured_output=False,
    )
    async def robot_observe(robot: str | None = None) -> list[str | Image]:
        session = team.get(robot)
        return await session.observe() if session else [str(team.unknown(robot)["error"])]

    @mcp.tool(
        description="Say something on one robot: tones on a Microduck. A robot without a "
        "sound intent refuses."
    )
    async def robot_say(text: str, robot: str | None = None) -> dict[str, Any]:
        session = team.get(robot)
        return await session.say(text) if session else team.unknown(robot)

    @mcp.tool(
        description="Load a .glot contract on one robot: its requires are checked against "
        "that robot's manifest, then its allowlist and budgets apply there; the body comes "
        "back as instructions."
    )
    async def robot_load_glotfile(path: str, robot: str | None = None) -> dict[str, Any]:
        session = team.get(robot)
        return session.load(path) if session else team.unknown(robot)

    # The rule `_instructions` keeps: with --no-memory both tools stay registered and answer
    # "memory is off", so a description that says to call one is an instruction to waste a turn.
    remembering = any(s.memory is not None for s in team.sessions.values())

    @mcp.tool(
        description="What one robot remembers from earlier sessions and runs: the notes a "
        "pilot saved with robot_remember, and how its recent runs ended. Call it before "
        "planning; it costs no step."
        if remembering
        else "Memory is off for this server (--no-memory): no robot here keeps notes or a "
        "record of earlier runs, so this only answers that, and there is no need to call it."
    )
    async def robot_recall(robot: str | None = None) -> dict[str, Any]:
        session = team.get(robot)
        return session.recall() if session else team.unknown(robot)

    @mcp.tool(
        description="Keep one short fact for future sessions on one robot (where things "
        "usually are, what worked, what to avoid). Moves nothing, costs no step. The same "
        "sentence twice updates the old note instead of duplicating it."
        if remembering
        else "Memory is off for this server (--no-memory): nothing is kept, so this only "
        "answers that, and there is no need to call it."
    )
    async def robot_remember(
        text: str, tags: list[str] | None = None, robot: str | None = None
    ) -> dict[str, Any]:
        session = team.get(robot)
        return session.remember(text, tags) if session else team.unknown(robot)

    return mcp, team


def build_server(
    transport: RobotTransport,
    *,
    glotfile: str | None = None,
    lock_glotfile: bool = False,
    dry_run: bool = False,
    yes: bool = False,
    registry: VerbRegistry | None = None,
    detector: Detector | None = None,
    heartbeat_period_s: float = 0.5,
    memory: bool = True,
    memory_dir: str | Path | None = None,
    log: bool = True,
) -> tuple[MCPServer, RobotSession]:
    """One robot, the 0.3 entry point: a team of one named after its adapter."""
    name = adapter_name(transport) or "robot"
    mcp, team = build_team_server(
        {name: transport},
        glotfile=glotfile,
        lock_glotfile=lock_glotfile,
        dry_run=dry_run,
        yes=yes,
        registry=registry,
        detector=detector,
        heartbeat_period_s=heartbeat_period_s,
        memory=memory,
        memory_dir=memory_dir,
        log=log,
    )
    return mcp, team.sessions[name]


@dataclass
class ServedTeamPlan:
    """A team, built but not connected: what `serve` hands `build_team_server`."""

    adapters: dict[str, Any]
    manifests: dict[str, RobotManifest]
    memory_keys: dict[str, str]
    """name -> memory key, for the robots that have a registered name to be keyed by."""
    default: str | None
    """The team's first member, when a stored team named one. Else `_pick_default` decides."""
    host: HostChoice = field(default_factory=HostChoice)
    """The board this server's one robot uses (`--host`), settled once from the flag, the
    registry and the environment. Empty for a team, which may not have one."""
    detector: Detector | None = None
    """The detector `--detector` asked for, or the board's when `--host` brings one, for the
    one robot. None leaves it to `detector_for` at connect, which is the colour detector."""
    specs: dict[str, RobotSpec] = field(default_factory=dict)
    """name -> the robot each body was built from, which `serve` reads just before the server
    connects a robot, to say whether it is on a backend its adapter is retiring."""


def team_from_flags(
    *,
    robot: str | None = None,
    robots: str | None = None,
    team: str | None = None,
    registry_dir: str | None = None,
    glotfile: str | None = None,
    address: str | None = None,
    camera_url: str | Sequence[str] | None = None,
    token: str | None = None,
    host: str | None = None,
    host_token: str | None = None,
    detector: str | None = None,
    policy_url: str | None = None,
    policy_token: str | None = None,
    accept_other_frame: bool = False,
    async_chunks: bool = True,
    map_address: str | None = None,
    fov_deg: float | None = None,
) -> ServedTeamPlan:
    """Which robots this server fronts, from the flags that name them.

    Three ways in: one robot, an ad-hoc team, or a stored team. The last is the only one
    where each robot brings its own address, token and camera, because it is the only one
    where somebody wrote them down (ADR-0034).

    `--host` is for one robot, and refused for a team by `roboglot run`'s rule and in its
    words: a host names one machine's camera and detector, and a team has several bodies.
    The board is asked what it is before any body is built, and a board that does not answer
    refuses the server, as it refuses a run. `--detector` is for one robot too.

    So are `--map-address`, where one robot's own map answers, and `--policy-url`, the policy
    server an arm hands its `pick` and `manipulate` segments to, by the same rule and in
    `roboglot run`'s words.
    The server is asked what it serves once the arm is built, and one that does not answer
    refuses the server, as the board does. Both verbs are confirm gated, so an MCP client
    reaches them only on a server started with `--yes`.

    Nothing here connects, so nothing here says whether a robot is on a backend its adapter is
    retiring: the plan carries each robot's spec, and `serve` says it just before the server's
    start connects that robot."""
    from roboglot.adapters.base import policy_choice, policy_hint
    from roboglot.adapters.factory import describe, make_adapter, parse_robots
    from roboglot.registry import (
        Registry,
        RegistryError,
        Resolved,
        no_rest_pose_refusal,
        resolve_robot_ref,
    )

    given = (("--team", team), ("--robots", robots), ("--robot", robot))
    named = [name for name, value in given if value]
    if len(named) > 1:
        raise SystemExit(f"choose one: {', '.join(named)}")
    try:
        policy = policy_choice(
            policy_url,
            policy_token,
            accept_other_frame=accept_other_frame,
            async_chunks=async_chunks,
            registry_dir=registry_dir,
        )
    except ValueError as e:
        raise SystemExit(str(e)) from e
    body_kw: dict[str, Any] = {} if policy is None else {"policy": policy}
    if team and (address or camera_url or token or map_address):
        raise SystemExit(
            "--team takes every member's address, map address, token and camera from the "
            "registry: roboglot robot edit NAME to change one"
        )
    probe: GlotFile | None = None
    default = None
    if glotfile:
        probe = load_glot(glotfile)
        if probe.frontmatter.team is not None:
            raise SystemExit(
                "team tasks are not available over MCP (the MCP client is one pilot, and "
                "a team has one LLM per robot rather than one for all of them). Run it "
                "with: roboglot run " + glotfile
            )
        if isinstance(probe.frontmatter.robots, str):
            default = probe.frontmatter.robots
    registry = Registry(registry_dir)
    resolved: list[Resolved]
    team_default: str | None = None
    if team:
        try:
            roster = registry.roster(team)
        except RegistryError as e:
            raise SystemExit(str(e)) from e
        resolved = [Resolved(entry.robot_spec, entry) for entry in roster.values()]
        # the team's own order decides, rather than `_pick_default`'s first-Microduck rule
        team_default = next(iter(roster), None)
    elif robots:
        resolved = [Resolved(spec) for spec in parse_robots(robots)]
    else:
        resolved = [resolve_robot_ref(robot, registry, glot_default=default)]
    specs: list[RobotSpec] = [r.spec for r in resolved]
    # A real arm with no rest pose has nothing to be folded to when its session closes, and
    # would be left holding itself up wherever it stopped (ADR-0058). Refused before anything is
    # described or built, as `run` refuses it, a `--dry-run` session included: it connects too.
    for each in resolved:
        refused = no_rest_pose_refusal(
            each,
            what="an MCP session",
            address=address,
            camera_url=camera_url,
            as_team=bool(robots) or len(resolved) > 1,
        )
        if refused is not None:
            raise SystemExit(f"{refused[0]}: {refused[1]}")
    # Refused before a manifest is described or an adapter built, as `run` refuses it.
    # ROBOGLOT_HOST is not refused, for `run`'s reason: it is the board you usually use, and
    # with several bodies there is nothing here for it to name, so it is left unread.
    as_team = bool(team or robots) or len(resolved) > 1
    hosted = [r.entry.name for r in resolved if r.entry is not None and r.entry.host]
    if as_team and (host or "").strip():
        raise SystemExit(
            "--host names one machine's camera and detector, and this server serves several "
            "robots: drop --host, or serve one robot"
        )
    if as_team and hosted:
        raise SystemExit(
            f"{hosted[0]} has a host in robots.json, which names one machine's camera and "
            "detector, and this server serves several robots: roboglot robot edit "
            f"{hosted[0]} --clear host, or serve it on its own"
        )
    if as_team and (host_token or "").strip():
        raise SystemExit(
            "--host-token is one board's token, and this server serves several robots and no "
            "board: drop --host-token, or serve one robot"
        )
    if as_team and policy is not None:
        raise SystemExit(
            "--policy-url is one arm's policy server, and this server serves several robots: "
            "drop --policy-url, or serve one arm"
        )
    if as_team and map_address is not None:
        raise SystemExit(
            "--map-address is where one robot's map answers, and this server serves several "
            "robots: drop --map-address, or keep each robot's own with roboglot robot edit NAME "
            "--map-address ..."
        )
    # The one place this server's board is settled, carried on the plan so everything that
    # uses the board reads this value rather than deriving its own.
    host_choice = HostChoice()
    if not as_team:
        (solo,) = resolved
        stored = solo.host_kwargs()
        try:
            host_choice = resolve_host(
                host,
                stored["host"],
                token=host_token,
                stored_token=stored["host_token"],
                robot=solo.entry.name if solo.entry is not None else None,
            )
        except ValueError as e:
            raise SystemExit(str(e)) from e
    if as_team and (detector or "").strip():
        raise SystemExit(
            "--detector is for one robot, and this server serves several robots: drop "
            "--detector, or serve one robot"
        )
    board = None
    hello = None
    try:
        reached = reach_host(host_choice)
    except HostError as e:
        raise SystemExit(unreached(host_choice, e)) from e
    if reached is not None:
        board, hello = reached
    manifests = {
        spec.name or describe(spec, **body_kw).id: describe(spec, **body_kw) for spec in specs
    }
    chosen: Detector | None = None
    if not as_team:
        (only,) = manifests
        # the body as its adapter describes it: all a detector built before connect may know
        # about the lens. Never the board's yet, as `roboglot run` says: a body described blind
        # may report a camera of its own at connect, and the board's is then an extra view.
        described = manifests[only]
        if hello is not None:
            # the board's camera is the one robot's before the contract is judged, so a camera
            # task on a blind body is not refused for a camera the server will have
            from roboglot.adapters.host_camera import with_host_camera

            manifests[only] = with_host_camera(described, hello)
        try:
            chosen = explicit_detector(
                (detector or "").strip().lower() or None,
                client=board,
                hello=hello,
                # `--fov-deg`, where `roboglot drive` was given it, as `roboglot run` builds its
                # detector through it; serve-mcp takes no such flag
                fov_deg=fov_deg or described.limits.get("camera_fov_deg"),
                backend=specs[0].backend,
                has_camera="camera" in described.sensors,
            )
        except (ValueError, ImportError) as e:
            raise SystemExit(str(e)) from e
    if probe is not None:
        # the contract lands on the default robot: refuse now, with the validator's words
        target = _pick_default(
            {name: _Probe(spec) for name, spec in zip(manifests, specs, strict=True)}
        )
        problems = validate_glot(probe, [manifests[target]])
        if problems:
            # an arm's pick and manipulate come only from --policy-url, which a team refuses
            hint = (
                None
                if as_team or policy is not None
                else policy_hint(
                    [p.verb for p in problems if p.verb],
                    [dict(zip(manifests, specs, strict=True))[target].key],
                    "roboglot serve-mcp",
                )
            )
            raise SystemExit(
                f"{glotfile} cannot run on {target} ({manifests[target].model}): "
                + "; ".join(p.message for p in problems)
                # on a line of its own, as `roboglot run` prints a hint under its refusal
                + (f"\n  {hint}" if hint else "")
            )
    adapters = {}
    for (name, spec), one in zip(zip(manifests, specs, strict=True), resolved, strict=True):
        where = one.adapter_kwargs(
            address=address, camera_url=camera_url, token=token, map_address=map_address
        )
        adapters[name] = make_adapter(
            spec,
            address=where["address"],
            camera_url=where["camera_url"],
            token=where["token"],
            map_address=where["map_address"],
            # the recorded pose, without which `RobotSession.connect`'s rest gate and the
            # torque hold in `close` are both dead code: the transport's `rest_pose` would be
            # None, every guard reading it would be False, and an arm served over MCP would be
            # released wherever the session left it, which is the fall this all exists to stop
            rest_pose=where["rest_pose"],
            # the board's camera joins the one body here; it answered its hello above
            host=board,
            **body_kw,
        )
        if policy is not None:
            # asked now, as the board was, so a server that is not there refuses this one in
            # a sentence rather than the first tool call's connect
            ask = getattr(adapters[name], "ask_policy", None)
            try:
                if callable(ask):
                    ask()
            except RuntimeError as e:
                raise SystemExit(str(e)) from e
    memory_keys = {
        name: one.memory_key
        for name, one in zip(manifests, resolved, strict=True)
        if one.registered
    }
    return ServedTeamPlan(
        adapters,
        manifests,
        memory_keys,
        team_default,
        host_choice,
        chosen,
        specs=dict(zip(manifests, specs, strict=True)),
    )


def serve(
    glotfile: str | None = None,
    address: str | None = None,
    camera_url: str | Sequence[str] | None = None,
    token: str | None = None,
    dry_run: bool = False,
    yes: bool = False,
    *,
    robot: str | None = None,
    robots: str | None = None,
    team: str | None = None,
    registry_dir: str | None = None,
    warn: Callable[[str], None] | None = None,
    memory: bool = True,
    memory_dir: str | None = None,
    log: bool | None = None,
    host: str | None = None,
    host_token: str | None = None,
    detector: str | None = None,
    policy_url: str | None = None,
    policy_token: str | None = None,
    accept_other_frame: bool = False,
    async_chunks: bool = True,
    map_address: str | None = None,
    lock_glotfile: bool = False,
) -> None:
    plan = team_from_flags(
        robot=robot,
        robots=robots,
        team=team,
        registry_dir=registry_dir,
        glotfile=glotfile,
        address=address,
        camera_url=camera_url,
        token=token,
        host=host,
        host_token=host_token,
        detector=detector,
        policy_url=policy_url,
        policy_token=policy_token,
        accept_other_frame=accept_other_frame,
        async_chunks=async_chunks,
        map_address=map_address,
    )
    logging.basicConfig(
        stream=sys.stderr, level=logging.INFO, format="roboglot-mcp %(levelname)s %(message)s"
    )
    mcp, _team = build_team_server(
        plan.adapters,
        manifests=plan.manifests,
        memory_keys=plan.memory_keys,
        default=plan.default,
        glotfile=glotfile,
        lock_glotfile=lock_glotfile,
        dry_run=dry_run,
        yes=yes,
        memory=memory,
        memory_dir=memory_dir,
        # the env is the switch a desktop-spawned server has (no shell, no cwd `.env`)
        log=log if log is not None else log_enabled_default(),
        detector=plan.detector,
        # how a retiring backend is said: `roboglot serve-mcp` hands the line `roboglot run`
        # prints, and a caller that hands nothing gets it through the logger
        before_connect=_deprecations_before_connect(plan.specs, warn),
    )
    mcp.run(transport="stdio")


def _deprecations_before_connect(
    specs: Mapping[str, RobotSpec], warn: Callable[[str], None] | None
) -> Callable[[str], None]:
    """What `serve` hands its team as `before_connect`.

    A robot on a backend its adapter is retiring (`deprecated_backend`) is said just before the
    server's start connects it, which `ServedTeam.connect_all` does one robot at a time and stops at
    the first that fails, so a robot whose connect never comes is never said. A line is said
    once however many robots are on its backend, through `warn`, which `roboglot serve-mcp` hands
    the line `roboglot run` prints, or through this module's logger when nothing is handed, so a
    program that serves without the CLI has it in its log. Either way it is stderr, and stdout
    stays the wire."""
    from roboglot.adapters.factory import deprecated_backend

    said: set[str] = set()

    def before_connect(name: str) -> None:
        spec = specs.get(name)
        line = deprecated_backend(spec) if spec is not None else None
        if line is None or line in said:
            return
        said.add(line)
        if warn is not None:
            warn(line)
        else:
            logger.warning("%s", line)

    return before_connect


class _Probe:
    """Enough of an adapter for `_pick_default` to choose before anything is built."""

    def __init__(self, spec: Any) -> None:
        self.name = spec.adapter
        self.backend = spec.backend


if __name__ == "__main__":  # pragma: no cover
    serve()


__all__ = [
    "TOOL_NAMES",
    "RobotSession",
    "ServedTeam",
    "ServedTeamPlan",
    "build_server",
    "build_team_server",
    "serve",
    "team_from_flags",
]
