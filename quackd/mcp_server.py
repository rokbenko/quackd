"""`quackd serve-mcp`: a robot, or a flock of them, as MCP tools over stdio.

This is the second wow-demo — "I asked Claude to make the duck patrol my desk" — and it
goes through the *same* `Executor` as `.duck` runs, so allowlists, confirm gates, budgets
and the heartbeat apply to an interactive session too. Since 0.4 one server can front
several robots (`--robots duck=microduck:sim2d,arm=lerobot:mock`): eight `robot_*`
tools take a robot name and every robot has its own executor, budget, heartbeat and
contract. stdout is the wire, and every log line goes to stderr.

The model here is the client, so what it thinks never reaches this process. What quackd can
see it narrates (`quackd.log`): each call that reaches an executor comes back with a
`log` list saying which gates fired, which intents went to the robot, what came back and
how long it took, and the same lines go to stderr. `--no-log` or `QUACKD_LOG=0` turns
both off. A session here has no run directory and writes no `terminal.txt`: stdout belongs
to the wire, and the only screen involved is the client's.
"""

from __future__ import annotations

import contextlib
import logging
import re
import sys
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from mcp.server import MCPServer
from mcp.server.mcpserver import Image
from pydantic import ValidationError

from quackd import __version__
from quackd.adapters.base import adapter_name, backend_name, go_to_rest_if_any
from quackd.adapters.manifest import RobotManifest, apply_datasheet_override
from quackd.agent.prompts import body_summary
from quackd.agent.transcript import png_bytes
from quackd.duckfile.narrow import narrow_policy_verb
from quackd.duckfile.parser import DuckParseError, load_duck
from quackd.duckfile.schema import Budgets, DuckFile
from quackd.duckfile.validate import validate_duck
from quackd.host import HostChoice, HostError, reach_host, resolve_host, unreached
from quackd.log import (
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
from quackd.memory import RobotMemory
from quackd.perception import detector_for, explicit_detector
from quackd.perception.base import Detector
from quackd.safety import (
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
from quackd.transport.base import (
    CameraFrame,
    DuckTransport,
    TransportError,
    camera_names_of,
)
from quackd.verbs.registry import (
    Verb,
    VerbRegistry,
    VerbResult,
    default_registry,
    registry_from_manifest,
)
from quackd.verdict import (
    BEFORE_VERDICT,
    MANIPULATOR_WORDS,
    MOBILITY_WORDS,
    TERRAIN_ORDER,
    Verdict,
    missing_needs,
    own_sheet_objection,
)

logger = logging.getLogger("quackd.mcp")


def _either(words: Sequence[str]) -> str:
    return ", ".join(words[:-1]) + f" or {words[-1]}"


NEEDS_WORDS_TEXT = (
    f"mobility is {_either(MOBILITY_WORDS)}: none means the task needs no locomotion, so an "
    "arm on a table is fine, and any means some kind. "
    f"manipulator is {_either(MANIPULATOR_WORDS)}, and none means the task touches nothing. "
    f"terrain is {_either(TERRAIN_ORDER)}, least demanding first, and a body that does not "
    "move meets indoor_flat. work_height_m is a height the hands must reach, not a minimum."
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
    "robot_load_duckfile",
    "robot_recall",
    "robot_remember",
)
"""Every tool the server registers, in this order; `docs/guides/mcp.md` must list each one."""

INSTRUCTIONS = """You are piloting one robot through quackd: {names}, which is {blurb}.
This body: {datasheet}
Call robot_list_verbs first: the verbs come from that robot's own manifest, so what it can
do is what it lists and nothing else. Before the first verb that moves the body, call
robot_assess_task with your verdict on whether this body can do the task at all, judged
against that datasheet: feasible, infeasible or uncertain. robot_run_verb refuses anything
that moves the body until you have answered, and loading a .duck starts a new task and
needs a new verdict. Every action is a *verb*; the executor enforces an allowlist, budgets
and confirmation gates, so a refused call is a rule, not a bug. Prefer composite verbs
(search_scan, go_to) over micro-managing velocities. Load a .duck file with
robot_load_duckfile(path) to adopt a task contract; then follow its body as your
instructions.{memory} Call robot_run_verb(verb="stop") if anything looks wrong."""

SOLO_MEMORY = """ Call robot_recall early: it is what this robot learned in earlier
sessions, and robot_remember(text) keeps one short fact for the next one."""

FLEET_INSTRUCTIONS = """You are piloting {n} robot(s) through quackd: {names}.
Call robot_list first, then robot_list_verbs(robot) for each body you will use: verbs come
from each robot's own manifest, so they differ per robot. Every action is a *verb*; each
robot's executor enforces its own allowlist, budgets and confirmation gates, so a refused
call is a rule, not a bug. Prefer composite verbs (search_scan, go_to) over micro-managing
velocities. Load a .duck file with robot_load_duckfile(path, robot) to adopt a task
contract on one robot; then follow its body as your instructions.{memory} Without a robot
argument a tool acts on the default, {default}.
Before the first verb that moves a body, call robot_assess_task(robot=...) with your verdict
on whether that body can do the task, judged against its datasheet below: feasible,
infeasible or uncertain. robot_run_verb refuses anything that moves that body until you
have answered. An infeasible verdict names, in `could`, the robots here whose datasheets
meet what the task needs: hand the task over with robot_load_duckfile(path, robot=...) and
assess it again there.
The bodies:
{datasheets}
Call robot_run_verb(verb="stop", robot=...) if anything looks wrong."""

FLEET_MEMORY = """ robot_recall(robot) is what that robot learned in earlier sessions;
robot_remember(text, robot) keeps one short fact for the next one."""


def _prefixed(emit: Callable[..., None], name: str) -> Callable[[str], None]:
    """Log lines from a fleet say which robot they are about."""

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


def _stash_frames(session: RobotSession) -> Callable[[Sequence[CameraFrame], str], None]:
    """The executor's `on_frames` hook: keep what `observe` captured for `robot_observe`.

    Every camera rather than the last one to arrive. The single slot this replaced held one
    picture, so a body with two cameras would have returned whichever was read second and
    called it the view."""

    def on_frames(frames: Sequence[CameraFrame], _cause: str) -> None:
        session.last_frames = list(frames)

    return on_frames


@dataclass
class RobotSession:
    """One robot behind the server: its own executor, budget, heartbeat and contract."""

    name: str
    transport: DuckTransport
    registry: VerbRegistry
    executor: Executor
    heartbeat: Heartbeat
    detector: Detector | None = None
    duck: DuckFile | None = None
    manifest: RobotManifest | None = None
    """Set on connect when the transport is an adapter that describes itself."""
    frames: int = 0
    calls: int = 0
    log_lines: list[str] = field(default_factory=list)
    last_frames: list[CameraFrame] = field(default_factory=list)
    """What the last `observe` captured, one entry per camera, so `robot_observe` can return
    them. Empty when nothing was captured: refused, no camera, or a dry run."""
    explicit_registry: bool = False
    """A caller-supplied registry is kept as is; otherwise the manifest builds one."""
    memory: RobotMemory | None = None
    """What this robot keeps between sessions (`quackd memory`). None = off."""
    event_log: EventLog | None = None
    """Narrates this robot's calls to stderr and into each result's `log`. None = off."""

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
                if (label := self.executor._clock()) is not None:
                    clocks["clock"] = label
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
        if self.duck is not None:
            for spelled in self.executor.allowed:
                if spelled != verb.name and self.registry.canonical(spelled) == verb.name:
                    return spelled
        return verb.name

    def adopt(self, duck: DuckFile) -> None:
        """Take on a task contract. Loading a second one never refunds the first.

        `robot_load_duckfile` is a tool the *model* holds, so a fresh `Budget` here was the
        way out of one: a pilot that had spent its steps, or been refused a verb, could load
        a wider duck and start counting from zero. The limits become the new contract's. The
        steps, the llm calls, the clock and the failure tallies stay the session's. The seconds
        a learned policy has driven the body stay the session's from the first load on, since
        a session with no task has a policy budget of its own to spend first."""
        # "First contract" used to be spelled `budget is None`, which stopped being true when
        # a contractless session gained a default budget of its own. Ask the question
        # directly: it is the first if no duck has been adopted yet.
        first = self.duck is None
        spent = self.executor.budget
        self.duck = duck
        self.executor.contract = duck.frontmatter
        if self.manifest is not None:
            self.executor.manifest = self._merged(self.manifest)
        self.executor.budget = Budget(
            duck.frontmatter.budgets,
            now=self.transport.now,
            policy_total_s=duck.frontmatter.effective_policy.total_s,
        )
        if spent is not None:
            # the seconds a policy has driven the body are the body's and not the task's, the
            # first load's included: a session with no task spends the default total, and a
            # load that zeroed it would hand the model a fresh `policy.total_s` for the price
            # of a tool call
            self.executor.budget.policy_s = spent.policy_s
        if first or spent is None:
            # The contract's budget is the task's, counted from when the task starts. The
            # carry-over below exists to stop a *second* load refunding a spent budget, and
            # applying it to the first would make "hello-world allows 5 steps" depend on
            # whatever happened before the duck was loaded.
            self.executor.budget.start()
            self.executor.consecutive_failures.clear()
            return
        self.executor.budget.steps = spent.steps
        self.executor.budget.llm_calls = spent.llm_calls
        self.executor.budget.started_at = spent.started_at

    def _merged(self, manifest: RobotManifest) -> RobotManifest:
        """The robot's manifest with the loaded task file's datasheet corrections folded in."""
        override = self.duck.frontmatter.datasheet if self.duck is not None else None
        return apply_datasheet_override(manifest, override)

    def effective_manifest(self) -> RobotManifest | None:
        """What the pilot should be told about this body: the merged sheet where there is one."""
        return self.executor.manifest or self.manifest

    async def connect(self) -> None:
        await self._adopt(await self.transport.connect())
        # The budget's clock is the transport's, and a simulator's is the wall's until it
        # connects and its world's from then on. Started when the server was built, the minutes
        # were the world's seconds less the wall's, which ran negative and never reached
        # `max_minutes`. Started again here, on the clock the session is timed by, with the
        # counts a `--duckfile` carried kept: nothing has run to count yet.
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
            # `self.manifest` stays the robot's own sheet, so a second `robot_load_duckfile`
            # merges from the body rather than from the previous task file's corrections
            self.executor.manifest = self._merged(connected)
            if not self.explicit_registry:
                self.registry = registry_from_manifest(connected, self.transport)
                self.executor.registry = self.registry
        self._narrow()

    def _narrow(self) -> None:
        """Hold `manipulate` to the loaded task's `policy:` section, or to the named defaults
        when no task is loaded, so a session with no contract still runs segments of the default
        length under a timeout that covers them (`narrow_policy_verb`)."""
        narrow_policy_verb(
            self.registry,
            self.duck.frontmatter if self.duck is not None else None,
            self.transport,
        )

    async def close(self) -> None:
        await self.heartbeat.stop()
        with contextlib.suppress(Exception):
            await self.transport.stop()
        if not self.executor.dry_run:
            # between the stop and the close, which is the only window where putting the arm
            # down changes whether it falls when torque is released
            with contextlib.suppress(Exception):
                await go_to_rest_if_any(self.transport)
        with contextlib.suppress(Exception):
            await self.transport.close()
        if note := getattr(self.transport, "close_note", None):
            logger.warning("%s: %s", self.name, note)

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
                f"session aborted: the heartbeat failed ({why}); restart quackd. "
                if why is not None
                else "session aborted (kill switch or abort_when); restart quackd. "
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
                "start `quackd serve-mcp --yes` to allow it"
            )
        except BudgetExceeded as e:
            result = VerbResult.fail(f"budget exhausted: {e}")
        except Aborted as e:
            why = self.heartbeat.failure
            result = VerbResult.fail(
                f"aborted: {e}" + (f" (the heartbeat failed: {why})" if why is not None else "")
            )
        return _result(result)

    async def assess(self, arguments: dict[str, Any], could: dict[str, str]) -> dict[str, Any]:
        """`robot_assess_task`: the pilot's verdict on this task against this body.

        `could` is the other robots in the fleet whose datasheets meet what the task needs,
        already matched by the caller, which is the only place that knows the fleet."""
        return await self._call(
            "robot_assess_task", dict(arguments), lambda: self._assess(arguments, could)
        )

    async def _assess(self, arguments: dict[str, Any], could: dict[str, str]) -> dict[str, Any]:
        if "human" in arguments:
            return {
                "ok": False,
                "robot": self.name,
                "summary": "assess_task takes no `human` field: only a person sets it",
            }
        try:
            verdict = Verdict.model_validate(arguments)
        except ValidationError as e:
            msgs = "; ".join(
                f"{'.'.join(map(str, err['loc']))}: {err['msg']}" for err in e.errors()
            )
            return {"ok": False, "robot": self.name, "summary": f"invalid verdict: {msgs}"}
        if verdict.verdict == "feasible":
            # the loop's check, on this surface too. `robot_assess_task` matched `needs`
            # against every OTHER robot in the fleet to fill in `could`, and recorded a
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
        if verdict.verdict == "uncertain":
            # `--yes` answers a confirm gate because there is no terminal to ask on. Here
            # there is a person, reachable through the model, which is better than a flag.
            payload["note"] = (
                "recorded as uncertain, which does not clear verbs that move the body. There "
                "is no terminal here: ask the person you are chatting with, then call "
                "robot_assess_task again with feasible on your own responsibility, or with "
                "infeasible."
            )
        elif verdict.verdict == "infeasible":
            payload["note"] = f"nothing on {self.name} will move for this task. " + (
                f"These robots meet what it needs: {', '.join(sorted(could))}. Load the "
                "task on one of them with robot_load_duckfile(path, robot=...) and assess "
                "it again there."
                if could
                else "No robot in this flock meets what it needs; tell the user."
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
            "contract": self.duck.name if self.duck else None,
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
            "contract": self.duck.name if self.duck else None,
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
        log as one text block (this tool returns content, not a dict)."""
        self.last_frames = []
        result = await self._call("robot_observe", {}, lambda: self._run("observe", {}))
        content: list[str | Image]
        if not result["ok"] or not self.last_frames:
            # refused, no camera, or a dry run (nothing was captured): words only
            content = [f"{self.name}: {result['summary']}"]
        elif len(self.last_frames) == 1 and len(camera_names_of(self.transport)) < 2:
            # the body's cameras rather than this step's frames: a two-camera arm down to one
            # lens takes the named branch below, because an unnamed picture from a body with
            # two views is the one thing the naming exists to prevent
            self.frames += 1
            summary = str(result["summary"]).removeprefix("frame captured; ")
            content = [
                f"{self.name} camera: {summary}",
                Image(data=png_bytes(self.last_frames[0].image), format="png"),
            ]
        else:
            # several cameras: each picture is named, and the summary is the primary's,
            # because the detector reads one view and a bearing from another means nothing
            self.frames += len(self.last_frames)
            # the body's camera list, not the frames that arrived. Naming the first picture
            # as the primary is only true when the primary is the one that answered: if it is
            # the lens that died, calling the survivor primary and handing it the detections
            # turns an unnamed picture into a mislabelled one, which is worse than what this
            # branch was written to fix. The detections belong to the primary either way, and
            # when it gave nothing there are none.
            here = camera_names_of(self.transport) or [f.name for f in self.last_frames]
            arrived = [f.name for f in self.last_frames]
            primary = next((f.name for f in self.last_frames if f.primary), None)
            summary = re.sub(r"^frames? captured[^;]*; ", "", str(result["summary"]))
            whose = (
                f"{primary} is the primary, the detections are its"
                if primary is not None
                else f"{here[0]} is the primary and gave nothing this step, "
                "so there are no detections"
            )
            content = [f"{self.name} cameras {', '.join(arrived)} ({whose}): {summary}"]
            for frame in self.last_frames:
                content.append(f"camera {frame.name}:")
                content.append(Image(data=png_bytes(frame.image), format="png"))
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
        text = self.memory.recall()
        return {
            "ok": True,
            "robot": self.name,
            "summary": text or "nothing remembered yet: this is the first session on this robot",
            "notes": [e.text for e in self.memory.notes()[-20:]],
            "episodes": [e.text for e in self.memory.episodes()[-5:]],
            "path": str(self.memory.path),
        }

    def remember(self, text: str, tags: list[str] | None = None) -> dict[str, Any]:
        if self.memory is None:
            return {"ok": False, "robot": self.name, "summary": "memory is off for this server"}
        try:
            entry = self.memory.remember(
                text, tags=tags, duck=self.duck.name if self.duck else None
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
            duck = load_duck(path)
        except DuckParseError as e:
            return {"ok": False, "error": str(e)}
        if duck.frontmatter.flock is not None:
            # same guard as serve(): one MCP pilot must not adopt a many-robot contract
            return {
                "ok": False,
                "error": (
                    "flock ducks are not available over MCP (this session is one pilot, "
                    f"a flock needs a coordinator). Run it with: quackd run {path}"
                ),
            }
        if self.manifest is not None:
            problems = validate_duck(duck, [self.manifest], registry=self.registry)
            if problems:
                return {
                    "ok": False,
                    "error": "; ".join(p.message for p in problems),
                    "problems": [p.message for p in problems],
                }
        # Validated, so its policy section is the one the verb is held to from now on. Narrowed
        # before anything is adopted, so a file this body's verb cannot be held to leaves the
        # session's task, its verdict and its verb as they were; and nothing is awaited from
        # here to the verdict being cleared, so no call runs under the one and not the other.
        try:
            narrow_policy_verb(self.registry, duck.frontmatter, self.transport)
        except ValueError as e:
            return {"ok": False, "error": str(e)}
        reloaded = self.executor.budget is not None
        self.adopt(duck)
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
        return {
            "ok": True,
            "robot": self.name,
            "name": duck.name,
            "contract": duck.frontmatter.model_dump(),
            "instructions": duck.body,
            "datasheet_text": body_summary(effective) if effective is not None else None,
            "note": note,
        }


DuckSession = RobotSession
"""The 0.3 name."""


def _result(r: VerbResult) -> dict[str, Any]:
    return {"ok": r.ok, "summary": r.summary, "data": r.data}


@dataclass
class Fleet:
    sessions: dict[str, RobotSession]
    default: str
    static: dict[str, RobotManifest] = field(default_factory=dict)
    """What each robot says about itself before it is asked: `describe()`, from the CLI. The
    instructions are built before `connect_all` runs, so this is what they can read."""

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
        """Sequential and fail-fast: a fleet with a hole in it is not served."""
        connected: list[RobotSession] = []
        for session in self.sessions.values():
            try:
                await session.connect()
            except BaseException:
                for done in connected:
                    with contextlib.suppress(Exception):
                        await done.close()
                raise
            connected.append(session)

    async def close_all(self) -> None:
        for session in self.sessions.values():
            with contextlib.suppress(Exception):
                await session.close()


def _pick_default(robots: Mapping[str, Any]) -> str:
    """The only robot; else the first Microduck, because a caller that names no robot on
    a mixed fleet most likely means the duck; else the first declared."""
    names = list(robots)
    if len(names) == 1:
        return names[0]
    for name, transport in robots.items():
        if adapter_name(transport) in (None, "microduck"):
            return name
    return names[0]


def _instructions(fleet: Fleet) -> str:
    """One robot gets a prompt about that robot, whichever body it is.

    Until 0.5 the solo prompt was hardcoded to a 25 cm Microduck, which was wrong for every
    other body. The description now comes from the manifest's own blurb, and the datasheet
    from `Fleet.static`: these instructions are built when the server is, which is before
    anything has connected, so a live manifest is not there to read yet and the static
    description is the honest source."""
    # with --no-memory both tools answer "memory is off", so telling the pilot to call
    # them early is an instruction to waste a turn
    on = any(s.memory is not None for s in fleet.sessions.values())
    if len(fleet.sessions) == 1:
        manifest = fleet.described(fleet.default)
        blurb = manifest.blurb if manifest and manifest.blurb else "a small robot"
        return INSTRUCTIONS.format(
            names=fleet.default,
            blurb=blurb,
            datasheet=body_summary(manifest) if manifest else "no datasheet until it connects.",
            memory=SOLO_MEMORY if on else "",
        )
    described = {name: fleet.described(name) for name in fleet.sessions}
    newline = "\n"
    return FLEET_INSTRUCTIONS.format(
        n=len(fleet.sessions),
        names=", ".join(fleet.sessions),
        default=fleet.default,
        memory=FLEET_MEMORY if on else "",
        datasheets=newline.join(
            f"- {name}: {body_summary(m) if m else 'no datasheet until it connects.'}"
            for name, m in described.items()
        ),
    )


def build_fleet_server(
    robots: Mapping[str, DuckTransport],
    *,
    duckfile: str | None = None,
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
) -> tuple[MCPServer, Fleet]:
    """One MCP server over several robots, each behind its own executor.

    `--yes` and `--dry-run` are global; contracts, budgets and abort flags are per robot.
    A `.duck` given at startup is adopted by the default robot. With `memory` on, each
    robot gets its `RobotMemory` (keyed adapter:backend, so a simulated body never
    inherits a real one's notes, or by its registered name where `memory_keys` gives one,
    so two registered robots of one kind keep separate notes) behind `robot_recall` /
    `robot_remember`. With `log` on,
    each robot narrates its calls: a `log` list in every result that reached its executor,
    and the same lines on stderr in place of the executor's own log lines."""
    if not robots:
        raise ValueError("a fleet needs at least one robot")
    sessions: dict[str, RobotSession] = {}
    for name, transport in robots.items():
        event_log: EventLog | None = None
        if log:
            event_log = EventLog(observers=[_stderr_view(name), capture_sink])
        reg = registry or default_registry()
        det = detector
        if det is None and backend_name(transport) in ("sim2d", "mujoco"):
            # a bare transport has no manifest to ask; an adapter is upgraded after connect
            from quackd.perception.color_blob import ColorBlobDetector

            det = ColorBlobDetector()
        # A session with no `.duck` has no contract, and used to have no Budget either — so
        # `quackd serve-mcp --robot open_duck:bridge`, which is the setup this module's own
        # docstring advertises, handed an MCP client unlimited, uncounted control of a
        # physical biped. The default budget is generous; what matters is that it is finite
        # and that the step count is visible. Loading a duck replaces it with the contract's.
        # Its clock starts again at the connect, on the clock the robot keeps once connected.
        budget = Budget(Budgets(), now=transport.now)
        budget.start()
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
            memory=(
                RobotMemory(
                    (memory_keys or {}).get(name)
                    or f"{adapter_name(transport) or name}:{backend_name(transport)}",
                    memory_dir,
                )
                if memory
                else None
            ),
            event_log=event_log,
        )
        executor.on_frames = _stash_frames(session)
        sessions[name] = session
    fleet = Fleet(sessions, default or _pick_default(robots), dict(manifests or {}))
    if fleet.default not in sessions:
        raise ValueError(f"default robot {fleet.default!r} is not one of {list(sessions)}")
    if duckfile:
        sessions[fleet.default].adopt(load_duck(duckfile))

    @contextlib.asynccontextmanager
    async def lifespan(_server: MCPServer) -> AsyncIterator[Fleet]:
        await fleet.connect_all()
        for session in fleet.sessions.values():
            # now that the robot has said what it actually has, not what its description
            # claims. `build_fleet_server` can only see a bare transport's backend.
            live = getattr(session.transport, "manifest", None)
            if live is not None:
                # a detector the server was given is kept. One `explicit_detector` built
                # before connect learns the lens the body reported: the board's, when the
                # board's camera is the only one it has. One handed to `build_fleet_server`
                # from Python keeps the lens it was built with, as `run_duck` keeps it.
                lens_fov = live.limits.get("camera_fov_deg")
                session.detector = detector_for(
                    live.sensors,
                    session.detector,
                    fov_deg=lens_fov,
                    backend=live.backend,
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
                    calibrate(lens_fov, backend=live.backend)
                session.executor.detector = session.detector
            logger.info(
                "quackd MCP server up: robot=%s transport=%s dry_run=%s detector=%s",
                session.name,
                backend_name(session.transport),
                dry_run,
                getattr(session.detector, "name", None) if session.detector else None,
            )
        try:
            yield fleet
        finally:
            await fleet.close_all()

    mcp = MCPServer(
        "quackd", instructions=_instructions(fleet), version=__version__, lifespan=lifespan
    )

    def me() -> RobotSession:
        return fleet.sessions[fleet.default]

    # ── the fleet tools ─────────────────────────────────────────────────────────────

    @mcp.tool(
        description="Every robot this server fronts (adapter, body, manifest, contract, "
        "health) and which one is the default. Call this first."
    )
    async def robot_list() -> dict[str, Any]:
        return {
            "robots": [
                await s.info(default=(name == fleet.default)) for name, s in fleet.sessions.items()
            ],
            "default": fleet.default,
        }

    @mcp.tool(
        description="One robot's verbs from its own manifest: params, safety class, "
        "canonical name, aliases, and whether its contract allows each now."
    )
    async def robot_list_verbs(robot: str | None = None) -> dict[str, Any]:
        session = fleet.get(robot)
        return session.verbs_payload() if session else fleet.unknown(robot)

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
            "in `needs` (payload_kg, reach_m, manipulator, mobility, ...) even when feasible, "
            "because that is what names the robots that could, and name only what the task "
            "turns on: leave a field out, or give 0 or none, when the task does not need it. "
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
        session = fleet.get(robot)
        if session is None:
            return fleet.unknown(robot)
        arguments: dict[str, Any] = {"verdict": verdict, "reason": reason}
        if limits_consulted is not None:
            arguments["limits_consulted"] = limits_consulted
        if estimates is not None:
            arguments["estimates"] = estimates
        if needs is not None:
            arguments["needs"] = needs
        could: dict[str, str] = {}
        if needs:
            for name in fleet.sessions:
                other = fleet.described(name)
                if name != session.name and other is not None:
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
        session = fleet.get(robot)
        return await session.run(verb, params) if session else fleet.unknown(robot)

    @mcp.tool(
        description="The observe verb on one robot, through its executor: the camera frame "
        "as a PNG plus a detection summary, then a log block of what happened.",
        structured_output=False,
    )
    async def robot_observe(robot: str | None = None) -> list[str | Image]:
        session = fleet.get(robot)
        return await session.observe() if session else [str(fleet.unknown(robot)["error"])]

    @mcp.tool(
        description="Say something on one robot: tones on a Microduck. A robot without a "
        "sound intent refuses."
    )
    async def robot_say(text: str, robot: str | None = None) -> dict[str, Any]:
        session = fleet.get(robot)
        return await session.say(text) if session else fleet.unknown(robot)

    @mcp.tool(
        description="Load a .duck contract on one robot: its requires are checked against "
        "that robot's manifest, then its allowlist and budgets apply there; the body comes "
        "back as instructions."
    )
    async def robot_load_duckfile(path: str, robot: str | None = None) -> dict[str, Any]:
        session = fleet.get(robot)
        return session.load(path) if session else fleet.unknown(robot)

    @mcp.tool(
        description="What one robot remembers from earlier sessions and runs: the notes a "
        "pilot saved with robot_remember, and how its recent runs ended. Call it before "
        "planning; it costs no step."
    )
    async def robot_recall(robot: str | None = None) -> dict[str, Any]:
        session = fleet.get(robot)
        return session.recall() if session else fleet.unknown(robot)

    @mcp.tool(
        description="Keep one short fact for future sessions on one robot (where things "
        "usually are, what worked, what to avoid). Moves nothing, costs no step. The same "
        "sentence twice updates the old note instead of duplicating it."
    )
    async def robot_remember(
        text: str, tags: list[str] | None = None, robot: str | None = None
    ) -> dict[str, Any]:
        session = fleet.get(robot)
        return session.remember(text, tags) if session else fleet.unknown(robot)

    return mcp, fleet


def build_server(
    transport: DuckTransport,
    *,
    duckfile: str | None = None,
    dry_run: bool = False,
    yes: bool = False,
    registry: VerbRegistry | None = None,
    detector: Detector | None = None,
    heartbeat_period_s: float = 0.5,
    memory: bool = True,
    memory_dir: str | Path | None = None,
    log: bool = True,
) -> tuple[MCPServer, RobotSession]:
    """One robot, the 0.3 entry point: a fleet of one named after its adapter."""
    name = adapter_name(transport) or "duck"
    mcp, fleet = build_fleet_server(
        {name: transport},
        duckfile=duckfile,
        dry_run=dry_run,
        yes=yes,
        registry=registry,
        detector=detector,
        heartbeat_period_s=heartbeat_period_s,
        memory=memory,
        memory_dir=memory_dir,
        log=log,
    )
    return mcp, fleet.sessions[name]


@dataclass
class FleetPlan:
    """A fleet, built but not connected: what `serve` hands `build_fleet_server`."""

    adapters: dict[str, Any]
    manifests: dict[str, RobotManifest]
    memory_keys: dict[str, str]
    """name -> memory key, for the robots that have a registered name to be keyed by."""
    default: str | None
    """The flock's first member, when a stored flock named one. Else `_pick_default` decides."""
    host: HostChoice = field(default_factory=HostChoice)
    """The board this server's one robot uses (`--host`), settled once from the flag, the
    registry and the environment. Empty for a fleet, which may not have one."""
    detector: Detector | None = None
    """The detector `--detector` asked for, or the board's when `--host` brings one, for the
    one robot. None leaves it to `detector_for` at connect, which is the colour detector."""


def fleet_from_flags(
    *,
    robot: str | None = None,
    robots: str | None = None,
    flock: str | None = None,
    registry_dir: str | None = None,
    duckfile: str | None = None,
    seed: int | None = None,
    address: str | None = None,
    camera_url: str | Sequence[str] | None = None,
    token: str | None = None,
    host: str | None = None,
    host_token: str | None = None,
    detector: str | None = None,
    policy_url: str | None = None,
    policy_token: str | None = None,
    accept_other_frame: bool = False,
) -> FleetPlan:
    """Which robots this server fronts, from the flags that name them.

    Three ways in: one robot, an ad-hoc fleet, or a stored flock. The last is the only one
    where each robot brings its own address, token and camera, because it is the only one
    where somebody wrote them down (ADR-0034).

    `--host` is for one robot, and refused for a fleet by `quackd run`'s rule and in its
    words: a host names one machine's camera and detector, and a fleet has several bodies.
    The board is asked what it is before any body is built, and a board that does not answer
    refuses the server, as it refuses a run. `--detector` is for one robot too.

    So is `--policy-url`, the policy server an arm hands its `pick` and `manipulate` segments
    to, by the same rule and in `quackd run`'s words. The server is asked what it serves once
    the arm is built, and one that does not answer refuses the server, as the board does. Both
    verbs are confirm gated, so an MCP client reaches them only on a server started with
    `--yes`."""
    from quackd.adapters.base import policy_choice, policy_hint
    from quackd.adapters.factory import (
        RobotSpec,
        describe,
        make_adapter,
        parse_robots,
    )
    from quackd.registry import Registry, RegistryError, Resolved, resolve_robot_ref

    given = (("--flock", flock), ("--robots", robots), ("--robot", robot))
    named = [name for name, value in given if value]
    if len(named) > 1:
        raise SystemExit(f"choose one: {', '.join(named)}")
    try:
        policy = policy_choice(policy_url, policy_token, accept_other_frame=accept_other_frame)
    except ValueError as e:
        raise SystemExit(str(e)) from e
    policy_kw: dict[str, Any] = {} if policy is None else {"policy": policy}
    if flock and (address or camera_url or token):
        raise SystemExit(
            "--flock takes every member's address, token and camera from the registry: "
            "quackd robot edit NAME to change one"
        )
    probe: DuckFile | None = None
    default = None
    if duckfile:
        probe = load_duck(duckfile)
        if probe.frontmatter.flock is not None:
            raise SystemExit(
                "flock ducks are not available over MCP yet (the MCP client is one pilot, "
                "a flock needs a coordinator, and a pilot flock has one model per robot "
                "rather than one for all of them). Run it with: quackd run " + duckfile
            )
        if isinstance(probe.frontmatter.robots, str):
            default = probe.frontmatter.robots
    registry = Registry(registry_dir)
    resolved: list[Resolved]
    fleet_default: str | None = None
    if flock:
        try:
            roster = registry.roster(flock)
        except RegistryError as e:
            raise SystemExit(str(e)) from e
        resolved = [Resolved(entry.robot_spec, entry) for entry in roster.values()]
        # the flock's own order decides, rather than `_pick_default`'s first-Microduck rule
        fleet_default = next(iter(roster), None)
    elif robots:
        resolved = [Resolved(spec) for spec in parse_robots(robots)]
    else:
        resolved = [resolve_robot_ref(robot, registry, duck_default=default)]
    specs: list[RobotSpec] = [r.spec for r in resolved]
    # Refused before a manifest is described or an adapter built, as `run` refuses it.
    # QUACKD_HOST is not refused, for `run`'s reason: it is the board you usually use, and
    # with several bodies there is nothing here for it to name, so it is left unread.
    fleet = bool(flock or robots) or len(resolved) > 1
    hosted = [r.entry.name for r in resolved if r.entry is not None and r.entry.host]
    if fleet and (host or "").strip():
        raise SystemExit(
            "--host names one machine's camera and detector, and a fleet has several bodies: "
            "drop --host, or serve one robot"
        )
    if fleet and hosted:
        raise SystemExit(
            f"{hosted[0]} has a host in robots.json, which names one machine's camera and "
            "detector, and a fleet has several bodies: quackd robot edit "
            f"{hosted[0]} --clear host, or serve it on its own"
        )
    if fleet and (host_token or "").strip():
        raise SystemExit(
            "--host-token is one board's token, and a fleet has several bodies and no board: "
            "drop --host-token, or serve one robot"
        )
    if fleet and policy is not None:
        raise SystemExit(
            "--policy-url is one arm's policy server, and a fleet has several bodies: drop "
            "--policy-url, or serve one arm"
        )
    # The one place this server's board is settled, carried on the plan so everything that
    # uses the board reads this value rather than deriving its own.
    host_choice = HostChoice()
    if not fleet:
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
    if fleet and (detector or "").strip():
        raise SystemExit(
            "--detector is for one robot, and a fleet has several bodies: drop --detector, or "
            "serve one robot"
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
        spec.name or describe(spec, **policy_kw).id: describe(spec, **policy_kw) for spec in specs
    }
    chosen: Detector | None = None
    if not fleet:
        (only,) = manifests
        # the body as its adapter describes it: all a detector built before connect may know
        # about the lens. Never the board's yet, as `quackd run` says: a body described blind
        # may report a camera of its own at connect, and the board's is then an extra view.
        described = manifests[only]
        if hello is not None:
            # the board's camera is the one robot's before the contract is judged, so a camera
            # task on a blind body is not refused for a camera the server will have
            from quackd.adapters.host_camera import with_host_camera

            manifests[only] = with_host_camera(described, hello)
        try:
            chosen = explicit_detector(
                (detector or "").strip().lower() or None,
                client=board,
                hello=hello,
                fov_deg=described.limits.get("camera_fov_deg"),
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
        problems = validate_duck(probe, [manifests[target]])
        if problems:
            # an arm's pick and manipulate come only from --policy-url, which a fleet refuses
            hint = (
                None
                if fleet or policy is not None
                else policy_hint(
                    [p.verb for p in problems if p.verb],
                    [dict(zip(manifests, specs, strict=True))[target].key],
                    "quackd serve-mcp",
                )
            )
            raise SystemExit(
                f"{duckfile} cannot run on {target} ({manifests[target].model}): "
                + "; ".join(p.message for p in problems)
                # on a line of its own, as `quackd run` prints a hint under its refusal
                + (f"\n  {hint}" if hint else "")
            )
    adapters = {}
    for (name, spec), one in zip(zip(manifests, specs, strict=True), resolved, strict=True):
        where = one.adapter_kwargs(address=address, camera_url=camera_url, token=token)
        adapters[name] = make_adapter(
            spec,
            seed=seed if seed is not None else 0,
            address=where["address"],
            camera_url=where["camera_url"],
            token=where["token"],
            # the recorded pose, without which `RobotSession.connect`'s rest gate and the
            # torque hold in `close` are both dead code: the transport's `rest_pose` would be
            # None, every guard reading it would be False, and an arm served over MCP would be
            # released wherever the session left it, which is the fall this all exists to stop
            rest_pose=where["rest_pose"],
            # the board's camera joins the one body here; it answered its hello above
            host=board,
            **policy_kw,
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
    return FleetPlan(adapters, manifests, memory_keys, fleet_default, host_choice, chosen)


def serve(
    duckfile: str | None = None,
    seed: int | None = None,
    address: str | None = None,
    camera_url: str | Sequence[str] | None = None,
    token: str | None = None,
    dry_run: bool = False,
    yes: bool = False,
    *,
    robot: str | None = None,
    robots: str | None = None,
    flock: str | None = None,
    registry_dir: str | None = None,
    warn: Any = None,
    memory: bool = True,
    memory_dir: str | None = None,
    log: bool | None = None,
    host: str | None = None,
    host_token: str | None = None,
    detector: str | None = None,
    policy_url: str | None = None,
    policy_token: str | None = None,
    accept_other_frame: bool = False,
) -> None:
    plan = fleet_from_flags(
        robot=robot,
        robots=robots,
        flock=flock,
        registry_dir=registry_dir,
        duckfile=duckfile,
        seed=seed,
        address=address,
        camera_url=camera_url,
        token=token,
        host=host,
        host_token=host_token,
        detector=detector,
        policy_url=policy_url,
        policy_token=policy_token,
        accept_other_frame=accept_other_frame,
    )
    logging.basicConfig(
        stream=sys.stderr, level=logging.INFO, format="quackd-mcp %(levelname)s %(message)s"
    )
    mcp, _fleet = build_fleet_server(
        plan.adapters,
        manifests=plan.manifests,
        memory_keys=plan.memory_keys,
        default=plan.default,
        duckfile=duckfile,
        dry_run=dry_run,
        yes=yes,
        memory=memory,
        memory_dir=memory_dir,
        # the env is the switch a desktop-spawned server has (no shell, no cwd `.env`)
        log=log if log is not None else log_enabled_default(),
        detector=plan.detector,
    )
    mcp.run(transport="stdio")


class _Probe:
    """Enough of an adapter for `_pick_default` to choose before anything is built."""

    def __init__(self, spec: Any) -> None:
        self.name = spec.adapter
        self.backend = spec.backend


if __name__ == "__main__":  # pragma: no cover
    serve()


__all__ = [
    "TOOL_NAMES",
    "DuckSession",
    "Fleet",
    "FleetPlan",
    "RobotSession",
    "build_fleet_server",
    "build_server",
    "fleet_from_flags",
    "serve",
]
