"""observe → think → enforce → act, until success, failure, budget, or abort.

This is the deliberation loop. It owns nothing clever: perception is a detector, safety is
the executor, memory is the transcript. What it does own is the *shape* of a turn — one
observation in, exactly one tool call out — and the honest bookkeeping of why a run ended.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from PIL import Image

from quackd.adapters.base import AdapterError, adapter_name, backend_name
from quackd.adapters.manifest import RobotManifest
from quackd.agent.prompts import (
    META_TOOL_NAMES,
    META_TOOLS,
    REMEMBER,
    REMEMBER_NAME,
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
    deny_all,
)
from quackd.transport.base import DuckTransport
from quackd.verbs.registry import (
    VerbRegistry,
    VerbResult,
    default_registry,
    registry_from_manifest,
)

Outcome = Literal["success", "failure", "budget", "aborted", "error"]


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
    acknowledge: Callable[[str], bool] | None = None
    """Asked once, before the first leg moves, when the robot cannot see a fall and cannot
    recover from one — so the only guard is the person in the room. None means nobody is
    there to ask (MCP, tests), and the warning is logged instead of blocking."""


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
        )
        self.heartbeat = Heartbeat(
            cfg.transport, self.executor.abort, period_s=cfg.heartbeat_period_s, log=cfg.log
        )
        self.history: list[Exchange] = []
        self.usage = Usage()
        self.highlights: list[str] = []
        """Verb results worth carrying into the episode memory (the last few that went ok)."""

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
        text = build_observation_text(
            step=self.budget.steps,
            max_steps=self.fm.budgets.max_steps,
            state=state,
            detections=detections,
            last_verb=last_verb,
            last_result=last_result,
            budget_status=self.budget.status(),
        )
        features = observation_features(
            state=state,
            detections=detections,
            last_verb=last_verb,
            last_result=last_result,
            allowed=self.executor.allowed,
        )
        image = png_bytes(img) if (img is not None and self.cfg.provider.supports_vision) else None
        return Observation(text=text, image_png=image, features=features), img

    def _remember(self, arguments: dict[str, Any]) -> VerbResult:
        memory = self.cfg.memory
        if memory is None:
            return VerbResult.fail("memory is off for this run; nothing saved")
        if self.cfg.dry_run:
            # `--dry-run` sends nothing and leaves nothing behind. A note here would be a
            # permanent conclusion drawn from verb results the dry run itself invented.
            text = " ".join(str(arguments.get("text", "")).split())
            self.cfg.log(f"[dry-run] would remember: {text}")
            return VerbResult.success(f"[dry-run] not saved: {text}", dry_run=True)
        text = str(arguments.get("text", "")).strip()
        tags_raw = arguments.get("tags") or []
        tags = [str(t) for t in tags_raw] if isinstance(tags_raw, list) else []
        try:
            entry = memory.remember(text, tags=tags, duck=self.fm.name, run_dir=self.run_dir)
        except (ValueError, OSError) as e:
            return VerbResult.fail(f"could not remember: {e}")
        self.cfg.log(f"remembered: {entry.text}")
        return VerbResult.success(
            f"remembered for future runs: {entry.text}", notes=len(memory.notes())
        )

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

    async def _fall_blind_warning(self, registry: VerbRegistry, allow: list[str]) -> str | None:
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
        state = await self.cfg.transport.get_state()
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
        connected = await cfg.transport.connect()
        manifest = connected if isinstance(connected, RobotManifest) else None
        if manifest is not None:
            if cfg.registry is None:
                self.registry = registry_from_manifest(manifest, cfg.transport)
                self.executor.registry = self.registry
            self.executor.manifest = manifest
            # the CLI guessed from the description; this is what the robot actually has
            cfg.detector = detector_for(manifest.sensors, cfg.detector)
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
            cfg.log(f"this robot does not have {', '.join(dropped)}; running without")
        if (warning := await self._fall_blind_warning(registry, allow)) is not None:
            cfg.log(warning)
            if cfg.acknowledge is not None and not cfg.acknowledge(warning):
                raise Aborted("nobody confirmed they were watching a robot that cannot see a fall")
        tools = registry.tool_schemas(allow) + META_TOOLS
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
        )
        system += getattr(cfg.provider, "prompt_hint", "") or ""  # e.g. the local JSON fallback
        self.transcript.write(
            "run_start",
            duck=self.fm.name,
            duck_path=self.duck.path,
            provider=cfg.provider.name,
            model=cfg.provider.model,
            transport=backend_name(cfg.transport),
            adapter=adapter_name(cfg.transport),
            robot=manifest.model_dump(mode="json") if manifest is not None else None,
            dry_run=cfg.dry_run,
            contract=self.fm.model_dump(),
            system_prompt=system,
            tools=[t["name"] for t in tools],
            memory=cfg.memory.summary() if cfg.memory is not None else None,
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
                    raise Aborted(
                        str(self.heartbeat.failure) if self.heartbeat.failure else "kill switch"
                    )
                obs, _ = await self._observe(last_verb, last_result)
                if self.history and self.history[-1].decision is not None:
                    obs = obs.model_copy(
                        update={"tool_call_id": self.history[-1].decision.tool_call.id}
                    )
                self.history.append(Exchange(observation=obs))
                self.transcript.write(
                    "observation",
                    step=self.budget.steps,
                    text=obs.text,
                    has_image=obs.image_png is not None,
                    features=obs.features,
                )

                self.budget.note_llm_call()
                turn = await cfg.provider.step(system, self._history_for_provider(), tools)
                self.usage = self.usage + turn.usage
                self.transcript.write(
                    "llm",
                    step=self.budget.steps,
                    provider=cfg.provider.name,
                    model=cfg.provider.model,
                    text=turn.text,
                    tool_calls=[tc.model_dump() for tc in turn.tool_calls],
                    usage=turn.usage.model_dump(),
                    stop_reason=turn.stop_reason,
                )
                self.budget.check_time()

                if not turn.tool_calls:
                    if not retry_prompted:
                        retry_prompted = True
                        self.history[-1].decision = None
                        self.history.append(
                            Exchange(
                                observation=Observation(
                                    text="You must call exactly one tool. Choose now.",
                                    features=obs.features,
                                )
                            )
                        )
                        self.transcript.write(
                            "enforce",
                            step=self.budget.steps,
                            issue="no_tool_call",
                            action="re-prompt",
                        )
                        continue
                    outcome, reason = "failure", "the model produced no tool call twice in a row"
                    break
                retry_prompted = False
                if len(turn.tool_calls) > 1:
                    self.transcript.write(
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
                    self.transcript.write(
                        "memory",
                        step=self.budget.steps,
                        ok=last_result.ok,
                        text=call.arguments.get("text"),
                        summary=last_result.summary,
                    )
                    continue

                if call.name in META_TOOL_NAMES:
                    outcome = "success" if call.name == "declare_success" else "failure"
                    reason = str(call.arguments.get("reason", ""))
                    self.transcript.write(
                        "declare", step=self.budget.steps, outcome=outcome, reason=reason
                    )
                    break

                last_verb = call.name
                try:
                    last_result = await self.executor.run_verb(
                        call.name, call.arguments, source="agent"
                    )
                except VerbNotAllowed as e:
                    last_result = VerbResult.fail(str(e))
                except ConfirmDenied as e:
                    last_result = VerbResult.fail(f"{e}; choose something else or declare_failure")
                self.transcript.write(
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
        finally:
            await self.heartbeat.stop()
            with contextlib.suppress(Exception):
                await cfg.transport.stop()
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
            }
            self.transcript.write("run_end", **summary)
            self.transcript.write_summary(summary)
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
        )


async def run_duck(cfg: RunConfig) -> RunResult:
    return await AgentLoop(cfg).run()
