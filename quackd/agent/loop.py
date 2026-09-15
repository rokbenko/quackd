"""observe → think → enforce → act, until success, failure, budget, or abort.

This is the deliberation loop. It owns nothing clever: perception is a detector, safety is
the executor, memory is the transcript. What it does own is the *shape* of a turn — one
observation in, exactly one tool call out — and the honest bookkeeping of why a run ended.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol, runtime_checkable

from PIL import Image
from pydantic import ValidationError

from quackd.adapters.base import AdapterError, adapter_name, backend_name
from quackd.adapters.manifest import RobotManifest, apply_datasheet_override
from quackd.agent.prompts import (
    ASSESS_TASK_NAME,
    DECLARE_NAMES,
    META_TOOLS,
    REMEMBER,
    REMEMBER_NAME,
    TELL,
    TELL_NAME,
    build_observation_text,
    build_system_prompt,
    observation_features,
)
from quackd.agent.providers.base import (
    Decision,
    Exchange,
    LLMProvider,
    Observation,
    ToolCall,
    Usage,
)
from quackd.agent.transcript import Transcript, new_run_dir, png_bytes
from quackd.duckfile.schema import DuckFile
from quackd.memory import RobotMemory
from quackd.perception import detector_for
from quackd.perception.base import Detection, Detector
from quackd.safety import (
    Aborted,
    Budget,
    BudgetExceeded,
    ConfirmDenied,
    ConfirmFn,
    Executor,
    Heartbeat,
    SafetyStop,
    VerbNotAllowed,
    VerdictRequired,
    deny_all,
)
from quackd.trace import Sink, Tracer
from quackd.transport.base import DuckState, DuckTransport
from quackd.verbs.registry import (
    VerbRegistry,
    VerbResult,
    default_registry,
    registry_from_manifest,
)
from quackd.verdict import Verdict, missing_needs, solo_hint

Outcome = Literal["success", "failure", "infeasible", "budget", "aborted", "error"]

REPROMPT = "You must call exactly one tool. Choose now."


@runtime_checkable
class FlockLinkLike(Protocol):
    """One pilot's end of a flock bus (`quackd.flock.talk.FlockLink`).

    Structural on purpose: `quackd.agent` must not import `quackd.flock`, because the flock
    imports the loop. The loop knows a link can be talked through and nothing else about
    flocks."""

    name: str
    abort_reason: str | None

    def send(self, to: str | None, text: str) -> Any: ...

    def drain(self) -> list[dict[str, Any]]: ...

    def prompt_section(self) -> str: ...

    def describe(self) -> dict[str, Any]: ...


@dataclass
class RunConfig:
    duck: DuckFile
    provider: LLMProvider
    transport: DuckTransport
    registry: VerbRegistry | None = None
    """None means: build it from the manifest the transport returns on connect (an adapter),
    or fall back to the Microduck vocabulary (a bare transport)."""
    detector: Detector | None = None
    dry_run: bool = False
    confirm: ConfirmFn = deny_all
    runs_dir: str | Path = "runs"
    run_dir: Path | None = None
    max_steps: int | None = None
    heartbeat_period_s: float = 0.5
    log: Any = lambda _m: None
    on_frame: Any = None
    """Optional callback (img, caption) for a recorder (M2). Called on every captured frame."""
    keep_images_for_last_n: int = 2
    memory: RobotMemory | None = None
    """What this robot remembers between runs. None = off: no `remember` tool, no
    episode written at the end, the prompt says nothing about earlier runs."""
    fov_deg: float | None = None
    """The horizontal field of view of the camera actually in front of you. None falls back
    to the robot's manifest, then to the simulator's, which is flagged as uncalibrated."""
    acknowledge: Callable[[str], bool] | None = None
    """Asked once, before the first leg moves, when the robot cannot see a fall and cannot
    recover from one — so the only guard is the person in the room. None means nobody is
    there to ask (MCP, tests), and the warning is logged instead of blocking."""
    decide: Callable[[str], bool] | None = None
    """Asked when the pilot says it is not sure this body can do the task, so a person makes
    the call. None means nobody is there (MCP, tests), and the pilot is told to decide itself
    rather than being cleared by default."""
    trace: Sink | None = None
    """Where to show the run as it happens (the CLI passes a `ConsoleTrace`). The transcript
    gets every event whether this is set or not; this is a second reader of the same stream."""
    link: FlockLinkLike | None = None
    """This pilot's end of a flock bus. None is a solo run: no `tell` tool, no flock section
    in the prompt, and no inbox in any observation."""
    summary_file: bool = True
    """Whether to write `summary.json` beside the transcript. False for a flock member, whose
    rollup belongs in the flock's own summary and whose directory must not read as a solo run
    (`quackd/flock/transcript.py`)."""


@dataclass
class RunResult:
    outcome: Outcome
    reason: str
    steps: int
    llm_calls: int
    usage: Usage
    run_dir: Path
    final_state: dict[str, Any] = field(default_factory=dict)
    gif_path: Path | None = None
    trace_dropped: int = 0
    """Events a view raised on and never showed. The transcript has them all."""

    @property
    def ok(self) -> bool:
        return self.outcome == "success"


class AgentLoop:
    def __init__(self, cfg: RunConfig) -> None:
        self.cfg = cfg
        self.duck = cfg.duck
        self.fm = cfg.duck.frontmatter
        if cfg.max_steps is not None:
            self.fm = self.fm.model_copy(
                update={"budgets": self.fm.budgets.model_copy(update={"max_steps": cfg.max_steps})}
            )
        self.run_dir = cfg.run_dir or new_run_dir(cfg.runs_dir, self.fm.name)
        self.transcript = Transcript(self.run_dir)
        # the transcript is the record, so its failure is the run's; a console is an observer
        self.tracer = Tracer(
            record=self.transcript.sink,
            observers=[cfg.trace] if cfg.trace is not None else [],
        )
        self.budget = Budget(self.fm.budgets, now=cfg.transport.now)
        self.registry = cfg.registry or default_registry()
        self.executor = Executor(
            registry=self.registry,
            transport=cfg.transport,
            contract=self.fm,
            budget=self.budget,
            detector=cfg.detector,
            dry_run=cfg.dry_run,
            confirm=cfg.confirm,
            log=cfg.log,
            on_frame=self._on_frame,
            trace=self.tracer,
        )
        self.heartbeat = Heartbeat(
            cfg.transport,
            self.executor.abort,
            period_s=cfg.heartbeat_period_s,
            log=cfg.log,
            trace=self.tracer,
        )
        # the pilot is handed an `assess_task` tool, so the executor holds it to the answer
        self.executor.require_verdict = True
        self.history: list[Exchange] = []
        self.usage = Usage()
        self.highlights: list[str] = []
        """Verb results worth carrying into the episode memory (the last few that went ok)."""

    # ── narration ───────────────────────────────────────────────────────────────────

    def _emit(self, kind: str, **data: Any) -> None:
        self.tracer.emit(kind, **data)

    def _note(self, text: str) -> None:
        """`log` is a contract (tests and the CLI's --verbose read it); the trace observes it."""
        self.cfg.log(text)
        self.tracer.emit("note", text=text)

    # ── frames ──────────────────────────────────────────────────────────────────────

    def _on_frame(self, img: Image.Image, caption: str) -> None:
        self.transcript.save_frame(img, caption)
        if self.cfg.on_frame is not None:
            self.cfg.on_frame(img, caption)

    async def _observe(
        self, last_verb: str | None, last_result: VerbResult | None
    ) -> tuple[Observation, Image.Image | None]:
        state = await self.cfg.transport.get_state()
        img = await self.cfg.transport.get_frame()
        detections: list[Detection] = []
        if img is not None:
            if self.cfg.detector is not None:
                detections = self.cfg.detector.detect(img)
            self._on_frame(img, f"step {self.budget.steps}: {last_verb or 'start'}")
        # drained here and nowhere else, so each message is shown exactly once: a re-prompt
        # reuses these features rather than observing again
        link = self.cfg.link
        inbox = link.drain() if link is not None else None
        text = build_observation_text(
            step=self.budget.steps,
            max_steps=self.fm.budgets.max_steps,
            state=state,
            detections=detections,
            last_verb=last_verb,
            last_result=last_result,
            budget_status=self.budget.status(),
            inbox=inbox,
            inbox_for=link.name if link is not None else None,
        )
        features = observation_features(
            state=state,
            detections=detections,
            last_verb=last_verb,
            last_result=last_result,
            allowed=self.executor.allowed,
            inbox=inbox,
            flock=link.describe() if link is not None else None,
        )
        image = png_bytes(img) if (img is not None and self.cfg.provider.supports_vision) else None
        return Observation(text=text, image_png=image, features=features), img

    NOBODY_TO_ASK = (
        "nobody is here to answer for the human: decide yourself and call assess_task again "
        "with feasible or infeasible, on your own responsibility"
    )

    def _assess(self, arguments: dict[str, Any]) -> tuple[VerbResult, str | None]:
        """Record the pilot's verdict. Returns what it hears back, and the reason the run ends
        with when the verdict ends it.

        A model cannot clear its own uncertainty: `assess_task` has no `human` field, and one
        that arrives with it is refused rather than quietly dropped."""
        if "human" in arguments:
            return VerbResult.fail(
                "assess_task takes no `human` field: only a person sets it"
            ), None
        try:
            verdict = Verdict.model_validate(arguments)
        except ValidationError as e:
            msgs = "; ".join(
                f"{'.'.join(map(str, err['loc']))}: {err['msg']}" for err in e.errors()
            )
            return VerbResult.fail(f"invalid assess_task: {msgs}"), None
        if verdict.verdict == "uncertain":
            if self.cfg.decide is None:
                self.executor.verdict = verdict  # recorded, and still not cleared
                return VerbResult.fail(self.NOBODY_TO_ASK), None
            try:
                answer = bool(self.cfg.decide(verdict.question()))
            except Exception:
                # a prompt that raised on Ctrl-C or EOF has not said yes, the same way the
                # confirm gate reads it
                answer = False
            verdict.human = "go" if answer else "no_go"
        if verdict.verdict == "feasible" and self.executor.manifest is not None:
            # The coordinator already holds another robot's bid to its datasheet with this
            # exact function. Nothing held a pilot's verdict about its OWN body to its own
            # sheet, so a `needs` naming an unpublished figure passed straight through and
            # the body moved. Measured on Qwen3-32B: a 45 minute patrol came back feasible
            # six times out of six, twice with `needs: {"endurance_min": 45}` recorded beside
            # it, on a body whose endurance nobody published.
            lacking = missing_needs(verdict.needs, self.executor.manifest)
            if lacking:
                # Refused rather than warned, the same way a verdict carrying `human` is
                # refused: the pilot is told which need its own sheet does not meet and can
                # assess again. A warning in the trace stops nothing.
                return VerbResult.fail(
                    "this body does not meet what you said the task needs: "
                    + "; ".join(lacking)
                    + ". Assess again, or call assess_task with infeasible"
                ), None
        self.executor.verdict = verdict
        if verdict.verdict == "infeasible":
            hint = solo_hint(verdict.needs, self.executor.manifest)
            return VerbResult.fail(verdict.reason), " ".join(p for p in (verdict.reason, hint) if p)
        if verdict.human == "no_go":
            return (
                VerbResult.fail("a human was asked and said no"),
                f"the pilot was unsure ({verdict.reason}) and the human said no",
            )
        return (
            VerbResult.success(f"recorded {verdict.summary()}; verbs that move the body now run"),
            None,
        )

    def _remember(self, arguments: dict[str, Any]) -> VerbResult:
        memory = self.cfg.memory
        if memory is None:
            return VerbResult.fail("memory is off for this run; nothing saved")
        if self.cfg.dry_run:
            # `--dry-run` sends nothing and leaves nothing behind. A note here would be a
            # permanent conclusion drawn from verb results the dry run itself invented.
            text = " ".join(str(arguments.get("text", "")).split())
            self._note(f"[dry-run] would remember: {text}")
            return VerbResult.success(f"[dry-run] not saved: {text}", dry_run=True)
        text = str(arguments.get("text", "")).strip()
        tags_raw = arguments.get("tags") or []
        tags = [str(t) for t in tags_raw] if isinstance(tags_raw, list) else []
        try:
            entry = memory.remember(text, tags=tags, duck=self.fm.name, run_dir=self.run_dir)
        except (ValueError, OSError) as e:
            return VerbResult.fail(f"could not remember: {e}")
        self._note(f"remembered: {entry.text}")
        return VerbResult.success(
            f"remembered for future runs: {entry.text}", notes=len(memory.notes())
        )

    def _tell(self, arguments: dict[str, Any]) -> VerbResult:
        """One sentence to another pilot. Not a verb: nothing is sent to any robot.

        It works under `--dry-run`, unlike `remember`. A dry run sends no intent and leaves
        nothing behind, and a message to a peer in the same dry run is neither: the peer is
        equally pretending, and a flock that could not talk would not be a dry run of a flock
        at all."""
        link = self.cfg.link
        if link is None:
            return VerbResult.fail("you are not in a flock; there is nobody to tell")
        to = str(arguments.get("to") or "").strip()
        text = str(arguments.get("text") or "")
        try:
            link.send(to, text)
        except ValueError as e:
            return VerbResult.fail(str(e))
        return VerbResult.success(f"told {to or 'all'}: {' '.join(text.split())}")

    def _history_for_provider(self) -> list[Exchange]:
        """Older images are dropped to keep context small; the last N keep theirs."""
        n = self.cfg.keep_images_for_last_n
        out: list[Exchange] = []
        for i, ex in enumerate(self.history):
            if ex.observation.image_png is not None and i < len(self.history) - n:
                ex = ex.model_copy(
                    update={"observation": ex.observation.model_copy(update={"image_png": None})}
                )
            out.append(ex)
        return out

    # ── the loop ────────────────────────────────────────────────────────────────────

    #: Verbs that can put the robot on the floor. A fall-blind robot only needs a human
    #: watching if the task can actually make it walk.
    _LOCOMOTION = frozenset({"move", "go_to", "search_scan", "approach_and"})

    def _fall_blind_warning(
        self, registry: VerbRegistry, allow: list[str], state: DuckState
    ) -> str | None:
        """Why the human has to watch this one, or None if they do not.

        Deliberately a one-time gate and not a precondition. On the Open Duck's bridge
        backend `fall_detection` is a constant False — the IMU has one owner and it is
        upstream's loop — so refusing per verb would refuse every locomotion verb forever
        and decommission the robot. The Microduck is a different case: it goes blind and
        comes back, and it has `stand_up`, so it keeps its per-call refusal and is not
        gated here."""
        if not any(registry.canonical(n) in self._LOCOMOTION for n in allow):
            return None
        if "stand_up" in registry:  # it can recover; being briefly blind is survivable
            return None
        if state.extras.get("fall_detection") is not False:
            return None
        return (
            "nothing on this robot detects a fall, and it has no way to get up. quackd will "
            "not know it is down, no verb will refuse because it is, and this task can make "
            "it walk. Keep it on a stand with a hand near the power switch, and watch it."
        )

    async def run(self) -> RunResult:
        cfg = self.cfg
        # connect FIRST: an adapter answers with its manifest, and the vocabulary (tools,
        # prompt, allowlist universe) is built from that, not hardcoded (ADR-0017)
        connect_started = time.perf_counter()
        connected = await cfg.transport.connect()
        connect_s = round(time.perf_counter() - connect_started, 3)
        manifest = connected if isinstance(connected, RobotManifest) else None
        if manifest is not None:
            # a v2 task file corrects the body's own sheet for the build in front of it, and it
            # does so here, before anything reads a manifest: the executor, the detector, the
            # prompt and the transcript all see the one the model was told about
            manifest = apply_datasheet_override(manifest, self.fm.datasheet)
        if manifest is not None:
            if cfg.registry is None:
                self.registry = registry_from_manifest(manifest, cfg.transport)
                self.executor.registry = self.registry
            self.executor.manifest = manifest
            # the CLI guessed from the description; this is what the robot actually has
            cfg.detector = detector_for(
                manifest.sensors,
                cfg.detector,
                fov_deg=cfg.fov_deg or manifest.limits.get("camera_fov_deg"),
                backend=backend_name(cfg.transport),
            )
            self.executor.detector = cfg.detector
        registry = self.registry
        allow = self.fm.verbs.allow
        # `validate` and the CLI check the STATIC manifest, which describes a fully built
        # robot. One that reports fewer capabilities at connect (no camera, no speaker, no
        # head) narrows its own vocabulary, and building the tool schemas would then raise a
        # bare VerbNotFound with the robot already connected.
        #
        # What a task *requires* it must have, so a missing one refuses in the validator's
        # words. What it merely *allows* is opportunistic, and a v1 task may allow more than
        # it needs, so those are dropped with a line in the log and the run goes on.
        missing = registry.unknown(self.fm.effective_requires)
        if missing:
            who = f"{manifest.id} ({manifest.model})" if manifest else "this robot"
            raise AdapterError(
                f"{self.duck.name} requires {', '.join(missing)}, but {who} does not provide "
                f"{'them' if len(missing) > 1 else 'it'}. The robot reported what it was "
                "actually built with when it connected, which is narrower than its "
                "description. Run `quackd list-verbs` against it to see what it has."
            )
        dropped = [n for n in allow if n not in registry]
        if dropped:
            allow = [n for n in allow if n in registry]
            self._note(f"this robot does not have {', '.join(dropped)}; running without")
        # One reading, before the budget starts, for two things the model has to be told at the
        # top: whether anything on this robot can see a fall, and what quackd is standing in
        # for on this backend. Both are the robot's own words about itself.
        first_state = await cfg.transport.get_state()
        if (warning := self._fall_blind_warning(registry, allow, first_state)) is not None:
            self._note(warning)
            if cfg.acknowledge is not None and not cfg.acknowledge(warning):
                raise Aborted("nobody confirmed they were watching a robot that cannot see a fall")
        tools = registry.tool_schemas(allow) + META_TOOLS
        if cfg.link is not None:
            tools = [*tools, TELL]
        memory_text: str | None = None
        if cfg.memory is not None:
            tools = [*tools, REMEMBER]
            memory_text = cfg.memory.recall()
        system = build_system_prompt(
            self.duck,
            [registry.view(n) for n in allow],
            backend_name(cfg.transport),
            manifest=manifest,
            memory_text=memory_text,
            assumptions=first_state.extras.get("assumptions") or None,
            flock_text=cfg.link.prompt_section() if cfg.link is not None else None,
        )
        system += getattr(cfg.provider, "prompt_hint", "") or ""  # e.g. the local JSON fallback
        self._emit(
            "run_start",
            duck=self.fm.name,
            duck_path=self.duck.path,
            provider=cfg.provider.name,
            model=cfg.provider.model,
            # Fields a passthrough added to every request (#12). A run whose model was told not
            # to think reads very differently from one that was, and the transcript is the only
            # place a reader can tell which they are holding.
            extra_body=getattr(cfg.provider, "extra_body", None),
            transport=backend_name(cfg.transport),
            adapter=adapter_name(cfg.transport),
            robot=manifest.model_dump(mode="json") if manifest is not None else None,
            dry_run=cfg.dry_run,
            contract=self.fm.model_dump(),
            system_prompt=system,
            tools=[t["name"] for t in tools],
            memory=cfg.memory.summary() if cfg.memory is not None else None,
            flock=cfg.link.describe() if cfg.link is not None else None,
            connect_s=connect_s,
        )
        outcome: Outcome = "error"
        reason = "loop exited unexpectedly"
        last_verb: str | None = None
        last_result: VerbResult | None = None
        retry_prompted = False

        self.budget.start()
        self.heartbeat.start()
        try:
            while True:
                await asyncio.sleep(0)  # let the heartbeat and kill switch run
                if self.executor.abort.is_set():
                    # a flock stops its members when one of them breaks, and the record has to
                    # say which one rather than blaming a kill switch nobody pressed
                    raise Aborted(
                        str(self.heartbeat.failure)
                        if self.heartbeat.failure
                        else (cfg.link.abort_reason if cfg.link is not None else None)
                        or "kill switch"
                    )
                observe_started = time.perf_counter()
                obs, _ = await self._observe(last_verb, last_result)
                if self.history and self.history[-1].decision is not None:
                    obs = obs.model_copy(
                        update={"tool_call_id": self.history[-1].decision.tool_call.id}
                    )
                self.history.append(Exchange(observation=obs))
                self._emit(
                    "observation",
                    step=self.budget.steps,
                    text=obs.text,
                    has_image=obs.image_png is not None,
                    features=obs.features,
                    elapsed_s=round(time.perf_counter() - observe_started, 3),
                )

                self.budget.note_llm_call()  # may raise BudgetExceeded: then no request is made
                history = self._history_for_provider()
                self._emit(
                    "llm_request",
                    step=self.budget.steps,
                    provider=cfg.provider.name,
                    model=cfg.provider.model,
                    messages=len(history),
                    images=sum(1 for ex in history if ex.observation.image_png is not None),
                    reprompt=retry_prompted,
                )
                llm_started = time.perf_counter()
                try:
                    turn = await cfg.provider.step(system, history, tools)
                except Exception as e:
                    # the call that failed is part of the record: what, and after how long
                    self._emit(
                        "llm",
                        step=self.budget.steps,
                        provider=cfg.provider.name,
                        model=cfg.provider.model,
                        error=f"{type(e).__name__}: {e}",
                        latency_s=round(time.perf_counter() - llm_started, 3),
                    )
                    raise
                self.usage = self.usage + turn.usage
                self._emit(
                    "llm",
                    step=self.budget.steps,
                    provider=cfg.provider.name,
                    model=cfg.provider.model,
                    text=turn.text,
                    tool_calls=[tc.model_dump() for tc in turn.tool_calls],
                    usage=turn.usage.model_dump(),
                    stop_reason=turn.stop_reason,
                    thinking=turn.thinking,
                    latency_s=round(time.perf_counter() - llm_started, 3),
                    usage_total=self.usage.model_dump(),
                    llm_calls=self.budget.llm_calls,
                )
                self.budget.check_time()

                if not turn.tool_calls:
                    if not retry_prompted:
                        retry_prompted = True
                        self.history[-1].decision = None
                        self.history.append(
                            Exchange(observation=Observation(text=REPROMPT, features=obs.features))
                        )
                        self._emit(
                            "enforce",
                            step=self.budget.steps,
                            issue="no_tool_call",
                            action="re-prompt",
                            text=REPROMPT,
                        )
                        continue
                    outcome, reason = "failure", "the model produced no tool call twice in a row"
                    break
                retry_prompted = False
                if len(turn.tool_calls) > 1:
                    self._emit(
                        "enforce",
                        step=self.budget.steps,
                        issue="multiple_tool_calls",
                        action="first_only",
                    )
                call: ToolCall = turn.tool_calls[0]
                self.history[-1].decision = Decision(tool_call=call, text=turn.text, raw=turn.raw)

                if call.name == REMEMBER_NAME:
                    # a note for next time: no robot motion, no step against the budget
                    last_verb = REMEMBER_NAME
                    last_result = self._remember(call.arguments)
                    self._emit(
                        "memory",
                        step=self.budget.steps,
                        ok=last_result.ok,
                        text=call.arguments.get("text"),
                        summary=last_result.summary,
                    )
                    continue

                if call.name == TELL_NAME:
                    # a word to another pilot: no motion, no step, one LLM call, like `remember`
                    last_verb = TELL_NAME
                    last_result = self._tell(call.arguments)
                    self._emit(
                        "talk",
                        step=self.budget.steps,
                        src=cfg.link.name if cfg.link is not None else None,
                        to=str(call.arguments.get("to") or "all"),
                        text=str(call.arguments.get("text") or ""),
                        ok=last_result.ok,
                        summary=last_result.summary,
                    )
                    continue

                if call.name == ASSESS_TASK_NAME:
                    # the pilot's judgement of the task against the body: no motion, no step,
                    # one LLM call, exactly like `remember`
                    last_verb = ASSESS_TASK_NAME
                    last_result, ends_with = self._assess(call.arguments)
                    recorded = self.executor.verdict
                    self._emit(
                        "assess",
                        step=self.budget.steps,
                        ok=last_result.ok,
                        summary=last_result.summary,
                        ends_run=ends_with is not None,
                        **(
                            recorded.model_dump(mode="json")
                            if recorded is not None
                            else {"verdict": None, "reason": str(call.arguments.get("reason", ""))}
                        ),
                    )
                    if ends_with is not None:
                        if recorded is not None and recorded.human == "no_go":
                            raise Aborted(ends_with)
                        outcome, reason = "infeasible", ends_with
                        break
                    continue

                if call.name in DECLARE_NAMES:
                    outcome = "success" if call.name == "declare_success" else "failure"
                    reason = str(call.arguments.get("reason", ""))
                    self._emit("declare", step=self.budget.steps, outcome=outcome, reason=reason)
                    break

                last_verb = call.name
                try:
                    last_result = await self.executor.run_verb(
                        call.name, call.arguments, source="agent"
                    )
                except VerdictRequired as e:
                    last_result = VerbResult.fail(f"{e}: call `{ASSESS_TASK_NAME}`")
                except VerbNotAllowed as e:
                    last_result = VerbResult.fail(str(e))
                except ConfirmDenied as e:
                    last_result = VerbResult.fail(f"{e}; choose something else or declare_failure")
                self._emit(
                    "verb",
                    step=self.budget.steps,
                    name=call.name,
                    canonical=registry.canonical(call.name),
                    params=call.arguments,
                    ok=last_result.ok,
                    summary=last_result.summary,
                    data=last_result.data,
                )
                if last_result.ok and last_result.summary:
                    self.highlights.append(f"{call.name}: {last_result.summary}")
                    self.highlights = self.highlights[-4:]
        except BudgetExceeded as e:
            outcome, reason = "budget", str(e)
        except Aborted as e:
            outcome, reason = "aborted", str(e)
        except SafetyStop as e:
            outcome, reason = "aborted", str(e)
        except Exception as e:
            # a provider that errored, a transport that died mid-observation, a bug: the run
            # still ends with a stop and a run_end, and run_end says with what
            outcome, reason = "error", f"{type(e).__name__}: {e}"
            self._emit("note", text=f"run ended with an error: {reason}")
            raise
        except BaseException as e:
            # A `KeyboardInterrupt` and a `CancelledError` are not `Exception`, so the branch
            # above missed both and `run_end` recorded its default, `loop exited
            # unexpectedly`. The CLI's second Ctrl-C is *designed* to raise a plain
            # KeyboardInterrupt, which makes this the normal way a person ends a run.
            outcome, reason = "aborted", f"interrupted: {type(e).__name__}"
            self._emit("note", text=f"run interrupted: {type(e).__name__}")
            raise
        finally:
            await self.heartbeat.stop()
            with contextlib.suppress(Exception):
                # the run's last intent, narrated like every other one
                await self.executor.traced_transport().stop()
            final_state: dict[str, Any] = {}
            with contextlib.suppress(Exception):
                final_state = (await cfg.transport.get_state()).model_dump()
            with contextlib.suppress(Exception):
                await cfg.transport.close()
            summary = {
                "duck": self.fm.name,
                "outcome": outcome,
                "reason": reason,
                "steps": self.budget.steps,
                "llm_calls": self.budget.llm_calls,
                "elapsed_s": round(self.budget.elapsed_s, 2),
                "usage": self.usage.model_dump(),
                "provider": cfg.provider.name,
                "model": cfg.provider.model,
                "transport": backend_name(cfg.transport),
                "robot": manifest.id if manifest is not None else None,
                "dry_run": cfg.dry_run,
                "final_state": final_state,
                # what a view could not show. The record has every one of them; a console
                # that swallowed a hundred events used to leave no sign anywhere.
                "trace_dropped": self.tracer.dropped,
            }
            # the only unguarded statements in this teardown used to be these three, so a
            # disk that filled at `run_end` skipped summary.json, leaked the file handle,
            # skipped the episode, and replaced the run's real exception with an OSError
            try:
                self._emit("run_end", **summary)
            finally:
                try:
                    if cfg.summary_file:
                        self.transcript.write_summary(summary)
                finally:
                    self.transcript.close()
            if cfg.memory is not None and not cfg.dry_run:
                with contextlib.suppress(Exception):  # memory must never turn a run into a crash
                    cfg.memory.record_episode(
                        duck=self.fm.name,
                        outcome=outcome,
                        reason=reason,
                        steps=self.budget.steps,
                        highlights=self.highlights,
                        run_dir=self.run_dir,
                    )
        return RunResult(
            outcome=outcome,
            reason=reason,
            steps=self.budget.steps,
            llm_calls=self.budget.llm_calls,
            usage=self.usage,
            run_dir=self.run_dir,
            final_state=final_state,
            trace_dropped=self.tracer.dropped,
        )


async def run_duck(cfg: RunConfig) -> RunResult:
    return await AgentLoop(cfg).run()
