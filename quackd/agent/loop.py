"""observe → think → enforce → act, until success, failure, budget, or abort.

This is the deliberation loop. It owns nothing clever: perception is a detector, safety is
the executor, memory is the transcript. What it does own is the *shape* of a turn — one
observation in, exactly one tool call out — and the honest bookkeeping of why a run ended.
"""

from __future__ import annotations

import asyncio
import contextlib
import re
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol, runtime_checkable

from PIL import Image
from pydantic import ValidationError

from quackd import __version__
from quackd.adapters.base import (
    AdapterError,
    HandResult,
    RestResult,
    adapter_name,
    backend_name,
    go_to_rest_if_any,
    let_go_if_any,
    take_hold_if_any,
)
from quackd.adapters.manifest import RobotManifest, apply_datasheet_override
from quackd.agent.decision.base import DecisionLLM
from quackd.agent.decision.catalogue import DecisionMode
from quackd.agent.decision.stepper import Advice, Stepper
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
    JUDGE_FEATURE,
    Decision,
    Exchange,
    JudgedPilot,
    LLMProvider,
    NamedPng,
    Observation,
    ToolCall,
    Usage,
)
from quackd.agent.providers.catalogue import Price
from quackd.agent.providers.pricing import cost_usd, resolve_price
from quackd.agent.transcript import Transcript, new_run_dir, png_bytes, run_label
from quackd.command import command_line, redacted_body, redacted_url
from quackd.duckfile.narrow import narrow_policy_verb
from quackd.duckfile.schema import DuckFile
from quackd.log import A_PERSON, EventLog, Sink, a_person_was_asked, fmt_params, who_answered
from quackd.memory import RobotMemory
from quackd.perception import detector_for
from quackd.perception.base import Detection, Detector, detect_off_loop, detect_times
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
from quackd.transport.base import (
    Ack,
    CameraFrame,
    DuckState,
    DuckTransport,
    Intent,
    camera_names_of,
    frames_of,
    primary_of,
)
from quackd.verbs.registry import (
    VerbRegistry,
    VerbResult,
    default_registry,
    registry_from_manifest,
)
from quackd.verdict import Verdict, own_sheet_objection, solo_hint

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


@runtime_checkable
class HandOff(Protocol):
    """Whoever is standing at the robot, and how the run talks to them.

    The loop needs exactly two things of a person: to be told something, and to be waited
    for. Both are the CLI's business, because it owns the terminal; the loop only decides
    when. An MCP session and a test have nobody there and pass None."""

    def say(self, text: str) -> None:
        """Print this whatever the log is doing: somebody has to read it to act on it."""
        ...

    async def wait(
        self, text: str, *, timeout_s: float | None = None, until_abort: bool = True
    ) -> bool:
        """Say `text`, then wait for Enter. True if it came, False if anything else ended it.

        A person who can say *what* else ended it sets `ended` after each wait (the CLI's
        `_TerminalHandOff` does: `enter`, `kill switch`, `no keys`, `stopped` or `timeout`),
        and the loop reads it with `getattr`, so a person that cannot say is still a
        `HandOff`. It is not a member here because this protocol is runtime-checkable, and a
        member would be one more thing every stand-in for a person had to carry."""
        ...


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
    hand_off: HandOff | None = None
    """Somebody at the robot, ready to place it by hand (`quackd run --by-hand`).

    None is every other run: the arm goes to its recorded rest pose and starts from there,
    which is the default and stays it. Set, and the run releases the arm at that pose, waits
    while they put it where they want it, holds whatever pose they left, and hands it back the
    same way at the end. Only a body whose adapter declares `supports_hand_off` is offered
    this, and the CLI refuses the flag before connecting where it is not."""
    person: HandOff | None = None
    """Somebody at a terminal, for the one question any run may put at its very end: the rest
    move missed, torque is being kept on an arm holding itself up, and would they like it
    released while they hold it (`AgentLoop.RELEASE_OFFER`).

    Separate from `hand_off` on purpose, and never a way to read "this is a by-hand run": the
    loop takes `hand_off is not None` to mean exactly that, and a run with a person at the
    keyboard is not a run whose arm is to be handed over. The CLI sets it whenever it can
    prompt and the run is not a dry one, to the same object as `hand_off` when there is one.
    None is an MCP session, a flock member, a test and a terminal-less run, and those keep
    torque on at a missed rest pose exactly as they always have."""
    task_images: Sequence[NamedPng] = ()
    """Pictures handed to the task by `quackd run --image`, already PNG and already sized
    (`quackd.agent.images`). They ride on the first observation and are never trimmed, so a
    task about a picture still is one twenty turns later. Empty is every run before there was
    a flag for it, and those go out unchanged."""
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
    judge: Callable[[str], bool] | None = None
    """Asked once, when a pilot that cannot tell whether its task was done says it is time
    (`JudgedPilot`, `--controller vla`), whether the arm did it: after its last segment, or
    about the segments that ran when a budget ends the run first. The answer goes to the pilot
    on its next observation, and only a yes from a person really asked lets it succeed. None
    means nobody is there, and such a pilot's run can then only fail. Every other pilot is
    never asked about, whether or not this is set."""
    view: Sink | None = None
    """Where to show the run as it happens (the CLI passes a `ConsoleLog`). The transcript
    gets every event whether this is set or not; this is a second reader of the same stream."""
    link: FlockLinkLike | None = None
    """This pilot's end of a flock bus. None is a solo run: no `tell` tool, no flock section
    in the prompt, and no inbox in any observation."""
    decision: DecisionMode = "off"
    """How much a decision LLM may do with the turns that are a choice (`--decision-mode`).

    `off` is every run made before there was a flag for it and every run that does not ask for
    one, and on that path no `Stepper` is built and no vendor SDK is imported. `shadow` asks it
    every turn, records the answer beside the model's, and changes nothing. `on` lets it take
    the turns it is confident about; every pose and every sentence is still the model's
    (`docs/guides/decision-llms/README.md`).

    *Which* decision LLM answers is a separate field, `decision_llm` below, chosen with
    `--decision-llm`: this one is only how much authority whichever one it is gets. The two are
    apart because they change for different reasons -- you swap Jev for a Kev server you run
    yourself without touching how far you trust it, and you promote a run from `shadow` to `on`
    without changing who answers."""
    decision_llm: DecisionLLM | None = None
    """Which decision LLM answers, already built and ready to be asked.

    The CLI builds it before the robot connects (`quackd.agent.decision.factory`), because a
    missing extra or a missing API key is a typo to be told about while nothing is powered up,
    not a `DecisionError` raised with an arm halfway through a reach. Required whenever
    `decision` is not `off`; None on every other run, and then nothing here imports a backend."""
    decision_price: Price | None = None
    """What one question to that decision LLM is costed at, resolved by the same factory.

    Written into `run_start` and into the `decision` block of `summary.json`, so a replay
    prices the run at what it cost on the day rather than at whatever the catalogue says months
    later. Never None when a decision LLM runs: the factory always resolves a rate, falling
    back to a self-hosted zero for a server you run yourself, because a missing number in the
    record reads as free when it means unknown."""
    summary_file: bool = True
    """Whether to write `summary.json` beside the transcript. False for a flock member, whose
    rollup belongs in the flock's own summary and whose directory must not read as a solo run
    (`quackd/flock/transcript.py`)."""
    run_name: str | None = None
    """What to call this run on disk (`quackd run --run-name`), as the person typed it.

    Slugged into the run directory name after the duck (`20260915-145349-goal-example-1`) and
    written into the record as typed. None is every run before there was a flag for it, and
    those are named exactly as they always were.

    It exists for a bench session: a hundred runs on one arm in one afternoon are a hundred
    directories that differ only in a timestamp nobody wrote down."""
    price: str | None = None
    """What the model costs, as `--price` spells it, overriding the catalogue and
    `QUACKD_PRICE` (`providers.pricing`). None asks the catalogue, which is the usual path."""
    host: dict[str, Any] | None = None
    """The board `--host` named, as `HostHello.record` describes it for `run_start`: its
    address, the daemon's version, what it can do, and its detector's model and device. None
    is every run without a board. The loop never talks to the board itself: the camera is in
    the transport and the detector is `detector`, and this is only what the record says."""


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
    log_dropped: int = 0
    """Events a view raised on and never showed. The transcript has them all."""
    decision_calls: int = 0
    """Turns the discrete stepper answered. 0 on every run that did not ask for one."""
    summary: dict[str, Any] = field(default_factory=dict)
    """The dict written to `summary.json`, verbatim, and the payload of `run_end`.

    Carried back whole rather than unpacked into a field apiece, because the CLI prints the
    same counters from a finished run and from a replayed transcript, and those two used to be
    two hand-kept lists that drifted. One shape, one printer (`cli.run_counters`)."""

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
            # and the duck with it, because the system prompt is built from `self.duck` while
            # the executor and the observation header read `self.fm`. On 2026-09-15 a
            # `--max-steps 10` run on the arm told the model `Budgets: 40 steps` above an
            # observation header that said `step 0/10`: it planned against four times the
            # budget the run actually stopped at, and every `--max-steps` run before this one
            # did the same.
            self.duck = cfg.duck.model_copy(update={"frontmatter": self.fm})
        # `getattr`, because a config whose provider is None is a real and supported one: a
        # run that will refuse before its first turn (a body that turned out to have no camera)
        # never needs a model and is built without one. It prices as unpriced, which is the
        # truth about a run that called nothing.
        self.price: Price | None = resolve_price(
            getattr(cfg.provider, "name", "") or "",
            getattr(cfg.provider, "model", "") or "",
            override=cfg.price,
        )
        """What this run is priced at: `--price`, `QUACKD_PRICE`, or the catalogue
        (`providers.pricing`). None where nobody publishes a rate for this model, which is not
        the same as free. Resolved here, before the run directory is made and before anything
        connects, because an unparsable price should cost you a sentence rather than a run."""
        self.run_dir = cfg.run_dir or new_run_dir(
            cfg.runs_dir, self.fm.name, run_label(cfg.run_name) if cfg.run_name else None
        )
        self.transcript = Transcript(self.run_dir)
        # the transcript is the record, so its failure is the run's; a console is an observer
        self.event_log = EventLog(
            record=self.transcript.sink,
            observers=[cfg.view] if cfg.view is not None else [],
        )
        self.budget = Budget(
            self.fm.budgets,
            now=cfg.transport.now,
            policy_total_s=self.fm.effective_policy.total_s,
        )
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
            # every camera a verb captures, not just the one that steers: with one camera this
            # writes exactly the frame and the record `on_frame` wrote before there were two
            on_frames=self._on_frames,
            event_log=self.event_log,
        )
        self.heartbeat = Heartbeat(
            cfg.transport,
            self.executor.abort,
            period_s=cfg.heartbeat_period_s,
            log=cfg.log,
            event_log=self.event_log,
        )
        # the pilot is handed an `assess_task` tool, so the executor holds it to the answer
        self.executor.require_verdict = True
        self.history: list[Exchange] = []
        self._rest_note_said = False
        """Whether the rest move's note about the pose itself has been said this run. The
        rest move runs at both ends, and the note is about the pose rather than the move."""
        self.usage = Usage()
        self.llm_latency_s = 0.0
        """Seconds this run spent waiting on the model, every call including the ones that
        raised. The single largest number in a quackd run and, until now, the one a reader had
        to add up by hand out of the `llm` records (ADR-0040 did exactly that)."""
        self.cost_usd: float | None = 0.0 if self.price is not None else None
        """What the model has cost so far, or None when there is no rate to cost it at.

        None is load-bearing: a model quackd has no rate for must not report `$0.00`, which
        would read as a free run rather than an unpriced one."""
        self._handed_over = False
        """Somebody answered the invitation to place this arm, so the gripper may be holding
        whatever they put in it.

        Set when the wait is answered rather than when the hold succeeds, because the two can
        differ and the difference is the jam: an arm that sagged as torque returned refuses the
        hold, ends the run, and still has the pencil in it. The teardown reads this to decide
        whether to ask for it back before the fold."""
        self._not_taken: HandResult | None = None
        """The take-hold that ended the placement wait, when it was refused and the arm is still
        in the person's hands, else None. The teardown reads its `energised` to say which arm
        they are holding: one nothing switched torque on under, which is still exactly as the
        release left it, or one whose torque write went out and was not confirmed, which may
        move or drop."""
        self.stepped: list[str] = []
        """Verbs the discrete stepper chose since the model was last asked, in the words the
        model will be given.

        Cleared the moment the model chooses again, because from then on its own history says
        what happened. This is the whole of what a stepper turn leaves in the conversation:
        no `Exchange`, no `Decision`, nothing a provider replays to the model as something it
        said. Writing one would put an unsigned tool call in an assistant turn, which Gemini
        refuses outright, and would teach every other model that answering nothing is fine."""
        self.highlights: list[str] = []
        """Verb results worth carrying into the episode memory (the last few that went ok)."""
        self.summary: dict[str, Any] = {}
        """What `run_end` said, kept for a caller that never gets a `RunResult` because the
        run raised its way out. A flock reads it off the loop to cost a member that died."""
        self._detector_reasons: set[str] = set()
        """The kinds of detector failure the record was told about in the outage going on
        now, empty while the detector answers, so a board that stops answering is one note
        when it stops and one when it answers again, rather than one on every frame in
        between (`_detector_said`)."""
        self.judged: dict[str, Any] | None = None
        """The question a `JudgedPilot`'s run put to a person about what the arm did, and the
        answer, as the pilot reads it under `JUDGE_FEATURE`, or None until it is put. Put once
        a run, and read again by the declare that ends it and by a budget that ends it first."""
        self.answered_by: str | None = None
        """What answered the doubt the last `assess_task` raised (`who_answered`), or None
        where it raised none or nothing answered it. The `assess` event records it as
        `answered_by` beside the `human` the gate reads, so the line drawn from it names what
        said go or no."""
        self.unanswered: str | None = None
        """What the prompt put the last `assess_task`'s doubt raised, EOF or click's `Abort`,
        where it went unanswered, or None. The gate reads that as no, and the `assess` event
        records it as `raised` in place of an `answered_by`: nobody said no."""

    # ── narration ───────────────────────────────────────────────────────────────────

    def _emit(self, kind: str, **data: Any) -> None:
        self.event_log.emit(kind, **data)

    def _abort_reason(self) -> str:
        """Why the abort flag is set, in the words the run should end with.

        A flock stops its members when one of them breaks, and a heartbeat stops a run when
        the body stops answering; the record has to say which rather than blaming a kill
        switch nobody pressed."""
        return (
            str(self.heartbeat.failure)
            if self.heartbeat.failure
            else (self.cfg.link.abort_reason if self.cfg.link is not None else None)
            or "kill switch"
        )

    def _ask_recorded(self, what: str, question: str, answer: bool, asker: Any) -> None:
        """Record a question a PERSON was put, and what they said.

        Only a person: `--yes` and a flock wire in callables that answer without asking, and a
        record claiming a question was put to somebody who was never there is worse than no
        record. The CLI marks the ones that reach a terminal with `asks_a_person`.

        The consequence of the answer is already written down elsewhere (`gate.answer` for a
        confirm, `assess.human` for a verdict, the `hand_off` stages for an arm, the `release`
        stages for the end-of-run offer, the `declare` a judged pilot makes of a `judge`). This
        is the exchange itself, which nothing held."""
        if not a_person_was_asked(asker):
            return
        self._emit("prompt", what=what, question=question, answer=answer)

    def _note(self, text: str) -> None:
        """`log` is a contract (tests and the CLI's --verbose read it); the run's log
        observes it."""
        self.cfg.log(text)
        self.event_log.emit("note", text=text)

    # ── frames ──────────────────────────────────────────────────────────────────────

    def _on_frames(self, frames: Sequence[CameraFrame], caption: str) -> None:
        """Every camera's picture to the record, the primary's to whoever asked for a frame.

        `cfg.on_frame` is the GIF recorder and a flock's own viewer, and both draw one world
        rather than a contact sheet, so they keep being handed the view that steers."""
        if not frames:
            return
        # the body's camera list rather than this step's frames: a two-camera arm that hands
        # back one picture still writes it as `0000-side.png`, because a bare `0000.png` in
        # the middle of a run is a file nothing says the lens of
        self.transcript.save_frames(
            list(frames), caption, several=len(camera_names_of(self.cfg.transport)) > 1
        )
        # `several` is the body's list and not len(frames): save_frames already takes the
        # named branch for two pictures, so this only adds the case where one arrived
        if self.cfg.on_frame is not None:
            primary = primary_of(frames)
            self.cfg.on_frame(primary if primary is not None else frames[0].image, caption)

    async def _on_frames_off_loop(self, frames: Sequence[CameraFrame], caption: str) -> None:
        """`_on_frames` for the turn's own observation, with the PNGs written in a worker thread
        (`Transcript.save_frames_off_loop`). A verb's frames still go through `_on_frames`,
        which the executor calls from inside the verb."""
        if not frames:
            return
        await self.transcript.save_frames_off_loop(
            list(frames), caption, several=len(camera_names_of(self.cfg.transport)) > 1
        )
        if self.cfg.on_frame is not None:
            primary = primary_of(frames)
            self.cfg.on_frame(primary if primary is not None else frames[0].image, caption)

    async def _park_before_the_run_began(self) -> None:
        """Put the body down and let go, for a failure between the connect and the first step.

        The run's own `finally` is what does this every other way a run can end, and it is not
        in scope yet here. Nothing in this teardown may raise, because whatever brought us here
        is the error the caller is owed: an `AdapterError` for a task this build cannot run, an
        `Aborted` for a warning nobody confirmed, a state read that timed out.

        The order is the finally's order for the same reasons: stop holds the body where it is,
        the rest move is the only window in which putting it down changes whether it falls, and
        the close is what releases torque. The transcript is closed too, since the run that
        would have closed it never started."""
        cfg = self.cfg
        with contextlib.suppress(Exception):
            await cfg.transport.stop()
        with contextlib.suppress(Exception):
            await self._rest()
        with contextlib.suppress(Exception):
            await cfg.transport.close()
        if note := getattr(cfg.transport, "close_note", None):
            with contextlib.suppress(Exception):
                self._note(str(note))
        with contextlib.suppress(Exception):
            self.transcript.close()

    async def _rest(self) -> RestResult | None:
        """The rest move, narrated. None for a dry run, and for a body with no rest pose.

        Called directly on the transport rather than through the executor: this runs at the
        end of every run including the one a person ended with Ctrl-C, and by then the
        executor's abort is set and would cancel the move that puts the arm down.

        An arm still in a person's hands (`in_hand`), which is where a refused take-hold leaves
        it, is not folded, and the one line said about it is that (`NOT_FOLDED`). The body's
        own rest move writes it nothing either (the LeRobot arm answers `already` where it
        reads at its rest pose, and refuses otherwise), and this is what keeps the narration
        honest over it. It used to say "moving to the rest pose" and then that the arm did not
        reach it "and it has stopped moving", of an arm that never moved, straight after the
        person holding it was told to keep hold of it: two lines that read as the arm about to
        move under their hand."""
        cfg = self.cfg
        if cfg.dry_run or getattr(cfg.transport, "rest_pose", None) is None:
            return None
        in_hand = getattr(cfg.transport, "in_hand", None) is True
        if not in_hand:
            self._note("moving to the rest pose")
        parked = await go_to_rest_if_any(cfg.transport)
        if parked.reached:
            self._note(
                "already at the rest pose" if parked.how == "already" else "at the rest pose"
            )
            if parked.note and not self._rest_note_said:
                # the body's own sentence about the pose it parked in, which is the same at
                # both ends of a run: said the first time, so the record carries it once and
                # the person reads it before the run starts rather than after it has ended
                self._rest_note_said = True
                self._note(parked.note)
        elif in_hand:
            self._note(self.NOT_FOLDED)
        else:
            self._note(f"the arm did not reach its rest pose: {parked.reason}")
        return parked

    PLACE_IT = (
        "the arm is yours: torque is off at its rest pose, so lift it, put whatever it needs "
        "in the gripper, close the gripper on that, hold it where you want the run to start, "
        "and press Enter"
    )
    """What a person is asked to do. Said, not logged: nobody reads a log to know it is their
    turn, and this run does not go on until they act."""

    NOBODY_PLACED_IT = (
        "nobody placed the arm: it was released at its rest pose for somebody to put it "
        "somewhere, and nothing was pressed"
    )

    HAND_IT_BACK = (
        "the run is over and the arm is holding where it ended. Take hold of whatever is in "
        "the gripper and press Enter, and the gripper opens before the arm folds up. Leave it "
        "and the arm folds up with the gripper shut"
    )
    """Asked before the gripper opens, not after. An arm folding to its rest pose with a
    pencil still in the jaws can drive that pencil into the bench, and the person who put it
    there is the one who should take it out."""

    RUN_STOPS = ", so the run stops here"
    """Where the lines for a refused take-hold say what became of the run, when the refusal is
    the take-hold at Enter, which ends the run with the same line."""

    RUN_STOPPED = " when the run stopped"
    """The same place in those lines, when the refusal is the take-hold the teardown's stop
    made, after a Ctrl-C in the placement wait. That run was already ending, and "so the run
    stops here" would give the refusal as the reason it ended."""

    _NOT_TAKEN = "quackd did not take hold of the arm{stops} and the arm is still in your hands"
    NOT_TAKEN_HOLD = _NOT_TAKEN.format(stops=RUN_STOPS)
    """Said to the person the moment `take_hold` refuses while the arm is still in their hands
    (`in_hand`) and switched nothing on (`energised` False), ahead of its reason, and the run
    aborts with the same line. They pressed Enter holding the arm and are waiting to hear they
    can let go, and "the arm is not holding the pose you set", the refusal for an arm that
    slipped as torque came on, reads as though something is holding it. Here nothing is: a
    joint placed past its travel is refused before any torque write goes out, and a servo that
    read off after it is still limp.

    Only then. A take-hold refused after its torque write, a register that did not answer or a
    call that raised with the write on the wire, may have left the arm energised, and "did not
    take hold ... still in your hands" said of it is a claim nothing read (`HOLD_UNCONFIRMED`).
    One whose read found some motors on is said by what it found (`HELD_IN_PART`), and one over
    an arm its read found still lying at its rest pose is said that way (`NOT_TAKEN_AT_REST`).
    A take-hold the teardown's stop made is said in the same words, with `RUN_STOPPED` in
    place of `RUN_STOPS`."""

    _NOT_TAKEN_AT_REST = (
        "quackd did not take hold of the arm{stops} and the arm is still limp at its rest pose"
    )
    NOT_TAKEN_AT_REST = _NOT_TAKEN_AT_REST.format(stops=RUN_STOPS)
    """`NOT_TAKEN_HOLD` for a take-hold whose own read found the whole arm at its rest pose with
    every motor off (`HandResult.resting`): the person pressed Enter without lifting the arm out
    of a fold recorded past its travel, and its reason names the folded joints and says quackd
    takes hold only once they are lifted inside. On the bench arm, whose shoulder folds past its
    travel, a person who loaded the gripper and pressed Enter was told the arm was still in
    their hands and to keep hold of it, and the close then said it was lying at its rest pose
    with no torque: the two lines before the last told them the arm was up and needed holding,
    which no read said."""

    HELD_IN_PART = (
        "quackd did not take hold of the arm{stops}: torque came on for {joints}, so {holds} "
        "and any joint not named is limp. Keep hold of the arm, and cut its power to let go of "
        "{them}"
    )
    """Said, and the run aborted with it, when `take_hold` refuses with the arm still in the
    person's hands and a read after its torque write found motors on (`energised` True, those
    motors in `torque_on`): some of them, or every one, which is why it names the joints that
    hold and says only that a joint it does not name is limp. It used to be `HOLD_UNCONFIRMED`,
    which opens on quackd not being able to confirm whether the arm has torque, in front of a
    parenthesis in which the read confirmed it motor by motor. The read did confirm it, and the
    person holding the arm is told what it found: which joints hold, that any other is limp, and
    that the switch is what lets go of the ones that hold. Nothing after this writes to the arm
    or folds it."""

    _HOLD_UNCONFIRMED = (
        "quackd could not confirm whether the arm has torque ({why}){stops}: keep hold of the "
        "arm as though it may move or drop, and cut its power to be sure"
    )
    HOLD_UNCONFIRMED = _HOLD_UNCONFIRMED.format(why="{why}", stops=RUN_STOPS)
    """Said, and the run aborted with it, when `take_hold` refuses with the arm still in the
    person's hands and its torque write may have gone out with nothing read back since
    (`energised` None). The person is holding an arm that may be limp, energised, or both in
    parts, so they are told to hold it against either and given the one way to be certain, the
    switch. Nothing after this writes to the arm or folds it."""

    STILL_IN_YOUR_HANDS = (
        "quackd never took hold of the arm, so it does not open the gripper for you: take out "
        "whatever is in it by hand, and keep hold of the arm"
    )
    """The end of a `--by-hand` run whose arm nothing took hold of and nothing switched torque
    on under, in place of `HAND_IT_BACK`, which begins "the arm is holding where it ended" and
    asks for Enter to open a gripper that a limp servo would not open. No fold follows it: the
    arm is in their hands (`NOT_FOLDED`)."""

    STILL_UNCONFIRMED = (
        "quackd could not confirm whether the arm has torque, so it does not open the gripper "
        "or fold the arm: keep hold of it as though it may move or drop, and cut its power "
        "before you take out whatever is in the gripper"
    )
    """`STILL_IN_YOUR_HANDS` for the arm `HOLD_UNCONFIRMED` was said of. "Take out whatever is
    in it by hand" is an invitation to put fingers into jaws that may be energised, so the power
    comes first. Said too where this run does not know which arm it is: a refusal nothing kept."""

    STILL_IN_PART = (
        "quackd did not take hold of the arm, and torque came on for {joints}, so it does not "
        "open the gripper or fold the arm: keep hold of it, and cut its power before you take "
        "out whatever is in the gripper"
    )
    """`STILL_IN_YOUR_HANDS` for the arm `HELD_IN_PART` was said of: the read named the joints
    that hold, so this names them again rather than say quackd could not tell. The gripper may
    be one of them, so the power still comes before anybody's fingers go into its jaws."""

    STILL_AT_REST = (
        "quackd never took hold of the arm, which is still limp at its rest pose, so it does not "
        "open the gripper for you: take out whatever is in it by hand. quackd takes hold of the "
        "arm only once {joints} {are} lifted inside {its} travel"
    )
    """`STILL_IN_YOUR_HANDS` for the arm `NOT_TAKEN_AT_REST` was said of, lying in its fold with
    torque off. "Keep hold of the arm" would tell somebody to hold up an arm that is down, and
    the close that follows reads it at its rest pose and says so. What it does say is the one
    thing to do differently next time: lift the folded joints inside their travel before Enter."""

    NOT_FOLDED = "the arm is in your hands, so it is not folded"
    """The teardown's one line in place of the rest move's two, for an arm a refused take-hold
    left in a person's hands. Said once, because a fold that does not happen has nothing more to
    report."""

    HAND_BACK_S = 120.0
    """How long the arm waits to be unloaded at the end. It is holding its pose meanwhile, so
    the cost of waiting is an energised arm and the cost of not waiting is a jam. Bounded
    because a run must still end when the room is empty."""

    RELEASE_OFFER = (
        "the arm did not reach its rest pose ({why}), so it is holding itself up. Hold it and "
        "press Enter to release torque now. Leave it, and after {seconds:.0f} s it stays that "
        "way"
    )
    """Put to a person at the end of a run whose rest move missed, before the close keeps
    torque on. Without it the only ways to take torque off such an arm were `quackd robot
    release` in another terminal and the power switch, and on the bench of 2026-09-23 every
    run that got to its end finished at the switch. It asks the person to hold the arm first,
    because the release lets it fall from wherever it stands."""

    RELEASE_OFFER_S = 60.0
    """How long the offer waits for Enter. Bounded for `HAND_BACK_S`'s reason, a run must end
    when the room is empty, and shorter than it, because what the wait costs is an arm holding
    itself up against a pose it could not reach, and nothing is lost by not answering: the arm
    is left exactly as a run without the offer leaves it."""

    RELEASE_INTERRUPTED = (
        "the release was interrupted while it was going out, so the arm may be limp, all of it "
        "or part of it: hold it as though nothing holds it, and put it down"
    )
    """Said when a Ctrl-C lands on the release after Enter, once it has gone out. The person
    pressed Enter because they were holding the arm, the release may have reached some motors
    and not others, and the only safe reading of that is the limp one."""

    RELEASE_NOT_SENT = (
        "the release was interrupted before anything was sent, so torque is as the rest move "
        "left it"
    )
    """Said when a Ctrl-C lands on the release after Enter and before it went out, on the read
    the release begins with: the arm's own backend says so (`in_hand` still False). Nothing
    reached a motor, which is all this line knows, so it says that and no more.

    It used to go on to say the arm "still holds itself up", and nothing read after the
    interrupt said so. On the arm the read the Ctrl-C landed on can still be out on the bus when
    the close comes straight after it, and then the close's own read is refused and its line
    says quackd cannot tell whether the arm is holding itself up: two lines in a row saying
    opposite things to a person deciding whether to let go. Whether the arm holds itself up is
    the close's line to say, from its own read, and it follows this one."""

    async def _hand_over(self) -> bool:
        """Let go of the arm, wait for somebody to place it, then hold what they left.

        Returns whether the arm is now holding a pose a person chose. False is an abort, and
        the caller raises: there is no sensible run from here, because the arm is either limp
        in somebody's hand or holding a pose nobody picked. Every way out of here still goes
        through the run's own teardown, which stops (picking a released arm back up), folds the
        arm to its rest pose and lets go there. Except where a take-hold was refused with the
        arm still in the person's hands, here or in the stop: then nothing picks it up, nothing
        folds it and nothing is written to it, and the close says which arm they are holding."""
        hand = self.cfg.hand_off
        if hand is None or self.cfg.dry_run:
            return False
        if self.executor.abort.is_set():
            # Ctrl-C between the connect and here, which is a window wide enough to hit: the
            # rest move is in it. Releasing now would de-energise the arm, tell somebody it was
            # theirs to place, and abort the run in the same breath.
            raise Aborted(self._abort_reason())
        released = await let_go_if_any(self.cfg.transport)
        self._emit("hand_off", stage="released", how=released.how, reason=released.reason)
        if not released.ok:
            raise Aborted(f"the arm was not handed over: {released.reason}")
        placed = await hand.wait(self.PLACE_IT)
        self._ask_recorded("hand_off", self.PLACE_IT, placed, hand)
        if not placed:
            # The invitation said "put whatever it needs in the gripper", so from the moment it
            # is answered the jaws may be holding something whatever happens next, and the
            # teardown owes them the chance to take it out before the arm folds on it.
            # the abort flag is what a Ctrl-C during the wait sets, and the loop's own reason
            # for one is better than this function's guess at it
            if self.executor.abort.is_set():
                raise Aborted(self._abort_reason())
            raise Aborted(self.NOBODY_PLACED_IT)
        self._handed_over = True
        held = await self._take_hold()
        if not held.ok:
            if self._not_taken is not None:
                # Nothing took the arm from the person, who is holding it and waiting to be
                # told they can let go, so they are told now and not only in the summary at the
                # end: the teardown after this takes a while, and every line of it is about an
                # arm they are still holding. Told as what it is: an arm nothing switched on
                # under, one still lying at its rest pose, one a read found holding in part,
                # or one whose torque write went out and nothing confirmed.
                said = self._not_taken_line(held, self.RUN_STOPS)
                with contextlib.suppress(Exception):
                    hand.say(said)
                raise Aborted(said)
            raise Aborted(f"the arm is not holding the pose you set: {held.reason}")
        hand.say(f"{held.reason}, you can let go. {_joints_line(held.joints)}")
        # The person's time is not the pilot's. `max_minutes` starts before the rest move, and
        # an arm can sit waiting for somebody to come back from finding a pencil, which used to
        # be spent out of the budget the model gets for the task.
        self.budget.start()
        return True

    async def _take_hold(self) -> HandResult:
        """Hold whatever pose the arm is in now, narrated, and remember a refusal that left the
        arm in the person's hands (`_not_taken`)."""
        held = await take_hold_if_any(self.cfg.transport)
        self._held(held)
        if not held.ok and getattr(self.cfg.transport, "in_hand", None) is True:
            self._not_taken = held
        return held

    def _held(self, held: HandResult) -> None:
        """A take-hold on the record: its `hand_off` event, and a note when it was refused.

        The joints a refusal was made over because each read outside its travel go into the
        event as `outside` where there are any, which is how the log line knows its reason
        already names them with their readings and leaves its own whole-degree list out."""
        extra: dict[str, Any] = {"outside": list(held.outside)} if held.outside else {}
        self._emit(
            "hand_off",
            stage="held",
            how=held.how,
            reason=held.reason,
            joints=held.joints,
            **extra,
        )
        if not held.ok:
            self._note(f"the arm did not take hold: {held.reason}")

    def _not_taken_line(self, held: HandResult, stops: str) -> str:
        """What the person holding the arm is told when a take-hold was refused with the arm
        still in their hands, by what the refusal found (`HandResult.energised`, `resting`,
        `torque_on`), with `stops` saying what became of the run (`RUN_STOPS` at Enter,
        `RUN_STOPPED` for the teardown's stop).

        Four arms, four lines. One lying at its rest pose with every motor off, never lifted
        out of its fold (`NOT_TAKEN_AT_REST`). One nothing switched on under (`NOT_TAKEN_HOLD`).
        One a read after the torque write found holding in part, named joint by joint
        (`HELD_IN_PART`). And one whose torque write went out with nothing read back, or that
        says it holds in part without saying where (`HOLD_UNCONFIRMED`), since only a read may
        tell the person which joints hold."""
        if held.resting:
            return f"{self._NOT_TAKEN_AT_REST.format(stops=stops)}: {held.reason}"
        if held.energised is False:
            return f"{self._NOT_TAKEN.format(stops=stops)}: {held.reason}"
        if held.energised and held.torque_on:
            one = len(held.torque_on) == 1
            return self.HELD_IN_PART.format(
                stops=stops,
                joints=_listed(held.torque_on),
                holds="that joint holds" if one else "those joints hold",
                them="it" if one else "them",
            )
        return self._HOLD_UNCONFIRMED.format(why=held.reason, stops=stops)

    def _still_line(self, refused: HandResult | None) -> str:
        """The hand-back's one line over an arm still in the person's hands, by what the refusal
        that left it there found, or `STILL_UNCONFIRMED` where no refusal is known at all."""
        if refused is None:
            return self.STILL_UNCONFIRMED
        if refused.resting:
            one = len(refused.outside) == 1
            return self.STILL_AT_REST.format(
                joints=_listed(refused.outside),
                are="is" if one else "are",
                its="its" if one else "their",
            )
        if refused.energised is False:
            return self.STILL_IN_YOUR_HANDS
        if refused.energised and refused.torque_on:
            return self.STILL_IN_PART.format(joints=_listed(refused.torque_on))
        return self.STILL_UNCONFIRMED

    def _refused_in_the_stop(self) -> None:
        """Say, once, a take-hold the teardown's stop made and was refused.

        The stop that opens every teardown takes hold of an arm in a person's hands when no
        take-hold has been refused since the release, which is a Ctrl-C in the placement wait
        (ADR-0039). Where that take-hold is refused, over a joint placed past its travel or a
        fold nobody lifted, the stop swallowed it: the refusal went into `stop_error`, which
        only the pilot's `stop` verb reads, so the person who expected the arm to be taken from
        them and folded heard only that it was not folded, and the record said nothing of which
        joint or why. It is read off the body (`refused_hold`) and said the way the take-hold at
        Enter is: the `hand_off` event, the note, and the line to the person, naming the joint.

        Only where this run has not said a refusal already (`_not_taken`), since the stop sends
        nothing after one and the body's refusal is then the same one. And it becomes this
        run's own (`_not_taken`), so the hand-back that may follow says which arm the person is
        holding by it, rather than assume the worst where an interrupt kept the take-hold at
        Enter from ever telling the run."""
        if self._not_taken is not None or self.cfg.dry_run:
            return
        transport = self.cfg.transport
        if getattr(transport, "in_hand", None) is not True:
            return
        refused = getattr(transport, "refused_hold", None)
        if not isinstance(refused, HandResult):
            return
        self._not_taken = refused
        self._held(refused)
        hand = self.cfg.hand_off
        if hand is not None:
            with contextlib.suppress(Exception):
                hand.say(self._not_taken_line(refused, self.RUN_STOPPED))

    async def _hand_back(self) -> None:
        """Ask before the gripper opens, at the end of a run the arm was handed over for.

        This sits between the run's `stop`, which is holding the arm where it ended, and the
        rest move, which folds it. Nothing here may raise: it is in the teardown, and a
        cancellation landing on the wait is a person pressing Ctrl-C again, which means "skip
        this and finish" rather than "abandon the arm energised with no record written".

        An arm still in the person's hands is not asked about: the take-hold was refused, the
        stop that begins the teardown sent it nothing, and the question would tell them the arm
        is holding where it ended. They are told what they are holding instead, and nothing is
        written to open the gripper (`_still_line`): `STILL_IN_YOUR_HANDS` for an arm nothing
        switched on under, `STILL_AT_REST` for one still lying in its fold, `STILL_IN_PART`
        for one a read found holding in part, and `STILL_UNCONFIRMED` for one whose torque
        write went out unconfirmed, which may be energised and is given the switch before
        anybody's fingers go into its jaws.

        Which refusal that is comes from this run's own record (`_not_taken`), and where an
        interrupt landed on the take-hold at Enter and left none, from the body's
        (`refused_hold`), which knows which it was. It used to assume the worst there, and tell
        somebody holding an arm nothing had switched on that quackd could not confirm whether
        it had torque, straight before the close said, from its own read, that it was limp.
        `STILL_UNCONFIRMED` is left for a refusal neither knows."""
        hand = self.cfg.hand_off
        if hand is None or self.cfg.dry_run:
            return
        if getattr(self.cfg.transport, "in_hand", None) is True:
            refused = self._not_taken or getattr(self.cfg.transport, "refused_hold", None)
            known = refused if isinstance(refused, HandResult) else None
            said = self._still_line(known)
            # the record's word for why nothing was asked, which for an arm still lying in its
            # fold is not that it is in somebody's hands
            why = (
                "the arm is still limp at its rest pose"
                if known is not None and known.resting
                else "the arm is still in your hands"
            )
            self._emit("hand_off", stage="skipped", reason=why)
            self._note(said)
            with contextlib.suppress(Exception):
                hand.say(said)
            return
        try:
            unloaded = await hand.wait(
                self.HAND_IT_BACK, timeout_s=self.HAND_BACK_S, until_abort=False
            )
        except (asyncio.CancelledError, KeyboardInterrupt):
            # A second Ctrl-C in this window used to raise through the whole teardown, which
            # skipped the rest move, the close, `run_end` and the summary. Here it means the
            # gripper stays shut, and the rest of the teardown still runs. A third press lands
            # somewhere without this guard and still quits at once.
            self._emit("hand_off", stage="skipped", reason="interrupted while waiting")
            self._note("the gripper was left as it is, and the arm still folds up")
            return
        self._ask_recorded("hand_off", self.HAND_IT_BACK, unloaded, hand)
        if not unloaded:
            # why the wait ended, where the person can say (`_TerminalHandOff.ended`), for
            # `_offer_release`'s reason: this wait watches a fresh key press and not the abort
            # flag, so a first Ctrl-C (or `q`) ends it with False and no exception, exactly as
            # the room being empty does, and "nobody answered" is not what happened
            ended = getattr(hand, "ended", None)
            if ended == "kill switch":
                self._emit("hand_off", stage="skipped", reason="interrupted while waiting")
                self._note("the gripper was left as it is, and the arm still folds up")
            elif ended == "no keys":
                self._emit("hand_off", stage="skipped", reason="no key could be read")
                self._note("no key could be read, so the gripper stays shut and the arm folds up")
            else:
                self._emit("hand_off", stage="skipped", reason="nobody answered")
                self._note("nobody unloaded the gripper, so it stays shut and the arm folds up")
            return
        # through the logged transport, so the record has the intent like every other one.
        # Not through the executor: its abort is set on every run a person ended, and this runs
        # on exactly those.
        ack: Any = None
        try:
            ack = await self.executor.logged_transport().send_intent(Intent.gripper(open=True))
        except Exception as e:
            ack = Ack(accepted=False, reason=f"{type(e).__name__}: {e}")
        # The arm's backend answers a refusal rather than raising it, so a suppressed exception
        # was never going to catch the failure that matters. Somebody is standing there with
        # their hand out: a gripper that did not open has to be said out loud, not left to be
        # discovered when the arm folds up with the pencil still in it.
        if ack is not None and not getattr(ack, "accepted", True):
            self._emit("hand_off", stage="stuck", reason=str(ack.reason or "the gripper refused"))
            self._note(f"the gripper did not open: {ack.reason}; take what is in it by hand")
            return
        self._emit("hand_off", stage="unloaded", reason="opening the gripper")

    async def _offer_release(self, parked: RestResult | None) -> None:
        """Offer the person at the terminal torque off, when the run's last rest move missed.

        This sits between the teardown's rest move and the close. When the move reached the
        pose there is nothing to offer, because the close lets go there anyway; when it missed,
        the close keeps torque on and the arm stands holding itself up at whatever pose it
        stopped in, which is right for an empty room and a dead end for a person standing next
        to it. So a person, and only a person (`cfg.person`: never MCP, never a flock member,
        never a dry run, which moved nothing), is told to hold the arm and asked for Enter.

        Enter releases through `let_go_if_any(..., anywhere=True)`, the transport's own second
        door, and the close then says the arm is limp in their hands. Anything else leaves the
        arm exactly as a run without the offer would: a wait that ran out, no key thread to
        read one, and a Ctrl-C. The wait watches a fresh key press rather than the abort flag
        (`until_abort=False`), for `_hand_back`'s reason: the flag is already set on every run a
        person ended, which are the runs most likely to have missed their fold. And a second
        Ctrl-C landing on it is caught here for the same reason too: it means "skip this and
        finish", and the close, `run_end` and the summary still have to happen. So is one
        landing on the release itself, after Enter: once the release has gone out, which is the
        one moment the arm may already be limp in part, the person is told so, and before it
        went out, that nothing was sent and torque is as the rest move left it. Either way the
        teardown carries on. Nothing here raises.

        Only a miss the arm answered for gets the offer: a move that stalled or ran out of time,
        or one refused on a write after a read that came back (`RestResult.answered`). A move that
        failed because the arm stopped answering, which is what cutting the servo supply looks
        like, or on a call that never came back and left the bus wedged, is nothing quackd can
        say "holding itself up" of, and the release it would offer refuses at its first read
        for as long as that stays so, after a person has been kept waiting for it.

        Nor is it made over an arm still in somebody's hands (`in_hand`), which is where a
        `--by-hand` run whose take-hold was refused ends: a joint placed past its travel, a
        servo that never took torque back, or a torque write nothing read back. Nothing read
        says that arm is holding itself up, the one thing the offer begins by saying, and it
        is in a person's hands already. The close's own line says which arm they are holding.

        Nor on a simulator (`is_simulator`), which has no arm to hold: the offer told a person
        to hold one and press Enter, and the close then said there was nothing to hold. The
        close's own line is what they hear, and it says what applies to a simulated arm."""
        person = self.cfg.person
        if person is None or self.cfg.dry_run or parked is None:
            return
        if not parked.recorded or parked.reached or not parked.answered:
            return
        if getattr(self.cfg.transport, "in_hand", None) is True:
            return
        if getattr(self.cfg.transport, "is_simulator", False) is True:
            return
        offer = self.RELEASE_OFFER.format(why=parked.reason, seconds=self.RELEASE_OFFER_S)
        try:
            agreed = await person.wait(offer, timeout_s=self.RELEASE_OFFER_S, until_abort=False)
        except (asyncio.CancelledError, KeyboardInterrupt):
            self._emit("release", stage="kept", reason="interrupted while waiting")
            self._note("torque stays on, and the arm holds itself up where it stopped")
            return
        self._ask_recorded("release", offer, agreed, person)
        if not agreed:
            # why the wait ended, where the person can say (`_TerminalHandOff.ended`): a first
            # Ctrl-C ends it without raising, and "nobody pressed Enter" is not what happened
            ended = getattr(person, "ended", None)
            if ended == "kill switch":
                self._emit("release", stage="kept", reason="interrupted while waiting")
                self._note("torque stays on, and the arm holds itself up where it stopped")
            elif ended == "no keys":
                self._emit("release", stage="kept", reason="no key could be read")
                self._note("no key could be read, so torque stays on and the arm holds itself up")
            else:
                self._emit("release", stage="kept", reason="nobody pressed Enter")
                self._note("nobody pressed Enter, so torque stays on and the arm holds itself up")
            return
        try:
            released = await let_go_if_any(self.cfg.transport, anywhere=True)
        except (asyncio.CancelledError, KeyboardInterrupt):
            # A Ctrl-C after the Enter. Which side of the send it landed on is the arm's own
            # backend's to say (`in_hand`), because the backend marks the arm in a hand the
            # moment the release call goes out. Before that, on the read the release begins
            # with, nothing was sent and torque is as the rest move left it, and "the arm may be
            # limp" would be said of a release that never happened: whether the arm holds itself
            # up is the close's line, from its own read (`RELEASE_NOT_SENT`). After it, part of
            # the arm may already be limp in their hands, and the close says the same. A body
            # that does not say gets the limp reading, which is the one that never drops an arm.
            if getattr(self.cfg.transport, "in_hand", None) is False:
                self._emit(
                    "release", stage="kept", reason="interrupted before the release was sent"
                )
                said = self.RELEASE_NOT_SENT
            else:
                self._emit("release", stage="interrupted", reason="interrupted during the release")
                said = self.RELEASE_INTERRUPTED
            self._note(said)
            with contextlib.suppress(Exception):
                person.say(said)
            return
        self._emit(
            "release",
            stage="released",
            how=released.how,
            reason=released.reason,
            joints=released.joints,
            torque_on=list(released.torque_on) if released.torque_on is not None else None,
        )
        # Said to the person rather than only logged, like the offer itself: they are holding
        # the arm and act on this line, and a run with its log off prints no note at all. The
        # close's own line follows in the log where there is one.
        if released.ok and released.torque_on:
            person.say(
                f"{released.reason}: the arm is in your hands, so put it down before you let "
                "go of it, and cut its power to let go of what still holds"
            )
        elif released.ok:
            person.say(
                f"{released.reason}: the arm is in your hands, so put it down before you let "
                "go of it"
            )
        elif released.torque_on:
            # read back: every motor said torque on, so the arm holding itself up is a reading
            self._note(f"torque was not released: {released.reason}")
            person.say(
                f"torque was not released ({released.reason}): the arm is still holding itself "
                "up, so keep hold of it and cut its power"
            )
        else:
            # nothing was read back, so nothing is known about torque either way: an arm whose
            # supply was cut reads exactly like one whose cable came out in front of live servos
            self._note(f"torque was not released: {released.reason}")
            person.say(
                f"torque was not released ({released.reason}), and quackd cannot tell whether "
                "torque is on: keep holding the arm, and cut its power"
            )

    async def _observe(
        self,
        last_verb: str | None,
        last_result: VerbResult | None,
        stepped: Sequence[str] = (),
        mine: tuple[str | None, VerbResult | None] | None = None,
    ) -> tuple[Observation, Image.Image | None]:
        """One turn as its two readers need it.

        `last_verb`/`last_result` are the most recent of anybody's and go into the features,
        which is what the discrete stepper reads: it is deciding what to do next and wants the
        world as it actually is. `mine` is the last verb the *model* chose, and goes into the
        text, because the text becomes the tool_result answering the model's own tool call and
        has to carry that call's outcome. Without a stepper the two are always the same and
        every observation is byte-identical to what it was."""
        state = await self.cfg.transport.get_state()
        frames = await frames_of(self.cfg.transport)
        # the body's own list first, because it does not shrink when a lens stalls, and the
        # names of the frames that arrived only when a transport does not publish one
        cameras = camera_names_of(self.cfg.transport) or [f.name for f in frames]
        img = primary_of(frames)
        detections: list[Detection] = []
        if img is not None and self.cfg.detector is not None:
            # the primary camera and only it: the detections line describes one view, and a
            # bearing is only meaningful from the lens --fov-deg measured. Off the loop when
            # the detector waits on a board, so a body's keepalives are not held up by it.
            detections = await detect_off_loop(self.cfg.detector, img)
            error = getattr(self.cfg.detector, "error", None)
            self._detector_said(error if isinstance(error, str) and error else None)
        if frames:
            # saved even when the primary gave nothing, because the other views are still what
            # the model is about to be shown. Off the loop's thread, like the encoding for the
            # model below: a body's heartbeat waits on that thread. The detector above and the
            # provider's own SDK stay where they are, the first because only a board's detector
            # waits on anything, the second because it is somebody else's code.
            await self._on_frames_off_loop(
                frames, f"step {self.budget.steps}: {last_verb or 'start'}"
            )
        # drained here and nowhere else, so each message is shown exactly once: a re-prompt
        # reuses these features rather than observing again
        link = self.cfg.link
        inbox = link.drain() if link is not None else None
        text = build_observation_text(
            step=self.budget.steps,
            max_steps=self.fm.budgets.max_steps,
            state=state,
            detections=detections,
            last_verb=mine[0] if mine is not None else last_verb,
            last_result=mine[1] if mine is not None else last_result,
            budget_status=self.budget.status(),
            inbox=inbox,
            inbox_for=link.name if link is not None else None,
            cameras=cameras if len(cameras) > 1 else None,
            stepped=stepped,
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
        images = (
            await asyncio.to_thread(
                lambda: [NamedPng(name=f.name, png=png_bytes(f.image)) for f in frames]
            )
            if self.cfg.provider.supports_vision and frames
            else []
        )
        return (
            Observation(
                text=text,
                images=images,
                cameras=cameras,
                features=features,
                attachments=self._attachments(),
            ),
            img,
        )

    def _detector_said(self, error: str | None) -> None:
        """One note when a detector that reports its failures starts failing, one when it
        answers again, and one for each new kind of reason in between. The board's detector is
        the one that does: a failed call is a frame with no detections, which reads exactly
        like an empty room, and the record has to say which it was. The run keeps the same
        detector either way, because a quiet switch to another would change what `go_to`
        steers at with nothing here to say so.

        A kind of reason is its text with the numbers taken out. A daemon's reasons carry
        figures that change from one request to the next (`the body ended after 812 of 30416
        bytes`, a model's out-of-memory sizes), so comparing the raw text would write a note
        for every frame of a flaky link, which is the noise one note per outage exists to
        avoid. A kind already noted in this outage is not noted again, so a link that
        alternates between two failures is two notes rather than one per frame."""
        name = getattr(self.cfg.detector, "name", "the detector")
        if error is None:
            if self._detector_reasons:
                self._note(f"{name} answers again")
                self._detector_reasons.clear()
            return
        kind = re.sub(r"\d+", "#", error)
        if kind in self._detector_reasons:
            return
        if self._detector_reasons:
            self._note(f"{name} still fails, now: {error}")
        else:
            self._note(
                f"{name} failed, so frames have no detections until it answers again, and "
                f"this run keeps it rather than switching: {error}"
            )
        self._detector_reasons.add(kind)

    def _detector_record(self, manifest: RobotManifest | None) -> str | None:
        """The detector `run_start` names: the one the run kept, or None when the body it
        connected to has no camera, because a detector with no frames reads nothing. A
        detector asked for before connect can be in that position: the board's is chosen for a
        body described without a camera, in case it reports one, and this is where a body that
        did not is told apart. A bare transport has no manifest and keeps its detector."""
        detector = self.cfg.detector
        if detector is None or (manifest is not None and "camera" not in manifest.sensors):
            return None
        return getattr(detector, "name", None)

    def _host_record(self, manifest: RobotManifest | None) -> dict[str, Any] | None:
        """The board as `run_start` records it, with the role its camera took at connect:
        `primary` when the body reported no camera of its own, else `extra view`. The run
        header is written before connect and can only say what the description suggests, so
        this is where a reader finds which one it was."""
        from quackd.adapters.host_camera import EXTRAS_KEY

        record = self.cfg.host
        placed = manifest.extras.get(EXTRAS_KEY) if manifest is not None else None
        if record is None or not isinstance(placed, dict):
            return record
        return {**record, "camera_role": "primary" if placed.get("primary") else "extra view"}

    def _policy(self, name: str) -> dict[str, Any] | None:
        """What the body says about the policy it hands segments to, `policy_served` for
        `run_start` or `policy_record` for the summary, or None for a body with none, which is
        every body but an arm given one. Asked of whatever the transport is, so a body quackd
        never shipped is left alone, and never allowed to fail a run over its record."""
        try:
            said = getattr(self.cfg.transport, name, None)
        except Exception:
            return None
        return dict(said) if isinstance(said, Mapping) else None

    def _attachments(self) -> list[NamedPng]:
        """The task's own pictures, on the first observation and on no other.

        Sent once because they never change: repeating them every step would pay for the same
        picture on every request, and the trim that bounds a run's picture cost counts camera
        frames per exchange and would not bound these at all. The first observation is the one
        the verdict gate answers from, which is the turn that most needs to see them.

        A pilot that cannot take an image gets a note instead, once. The CLI refuses `--image`
        for such a pilot before a run starts; this is for every other caller of the loop, and
        for the truth being in the record rather than in an argument about it."""
        if not self.cfg.task_images or self.history:
            return []
        if not self.cfg.provider.supports_vision:
            self._note(
                f"{len(self.cfg.task_images)} picture(s) came with this task and "
                f"{self.cfg.provider.name} {self.cfg.provider.model} cannot see: they were "
                "not sent, and the task has to stand on its words alone"
            )
            return []
        return list(self.cfg.task_images)

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
        asked = False  # whether a person answered the doubt, as against a standing answer
        self.answered_by = self.unanswered = None
        if verdict.verdict == "uncertain":
            if self.cfg.decide is None:
                self.executor.verdict = verdict  # recorded, and still not cleared
                return VerbResult.fail(self.NOBODY_TO_ASK), None
            try:
                answer = bool(self.cfg.decide(verdict.question()))
            except Exception as e:
                # a prompt that raised on Ctrl-C or EOF has not said yes, the same way the
                # confirm gate reads it, and nobody said no either: it is recorded as the
                # question unanswered, never as a no from whatever put it, and it leaves no
                # `prompt` row, as a judge's question that raised leaves none
                answer = False
                self.unanswered = type(e).__name__
            verdict.human = "go" if answer else "no_go"
            if self.unanswered is None:
                self.answered_by = who_answered(self.cfg.decide)
                asked = self.answered_by == A_PERSON
                self._ask_recorded("decide", verdict.question(), answer, self.cfg.decide)
        if verdict.verdict == "feasible":
            # The coordinator already holds another robot's bid to its datasheet, and nothing
            # held a pilot's verdict about its OWN body to its own sheet, so a `needs` naming
            # an unpublished figure passed straight through and the body moved. Measured on
            # Qwen3-32B: a 45 minute patrol came back feasible six times out of six, twice
            # with `needs: {"endurance_min": 45}` recorded beside it, on a body whose
            # endurance nobody published. Refused rather than warned, the same way a verdict
            # carrying `human` is refused: a warning in the log stops nothing.
            objection = own_sheet_objection(verdict.needs, self.executor.manifest)
            if objection is not None:
                # and the gate shuts. A refusal that left an earlier `feasible` standing
                # refused the words and not the motion: a pilot cleared for one task, then
                # naming a need this body cannot meet, went on moving on the older verdict
                # while the newer and better informed one was thrown away.
                self.executor.verdict = None
                return VerbResult.fail(objection), None
        self.executor.verdict = verdict
        if verdict.verdict == "infeasible":
            hint = solo_hint(verdict.needs, self.executor.manifest)
            return VerbResult.fail(verdict.reason), " ".join(p for p in (verdict.reason, hint) if p)
        if verdict.human == "no_go":
            if self.unanswered is not None:
                unanswered = f"the question went unanswered: the prompt raised {self.unanswered}"
                return (
                    VerbResult.fail(unanswered),
                    f"the pilot was unsure ({verdict.reason}) and {unanswered}",
                )
            # named as the record names it: a pipe says no as readily as a person does
            return (
                VerbResult.fail(f"{self.answered_by} was asked and said no"),
                f"the pilot was unsure ({verdict.reason}) and {self.answered_by} said no",
            )
        if verdict.human == "go":
            # The pilot has to hear who cleared it. It used to be told only "recorded
            # uncertain: ...; verbs that move the body now run", which reads the same as its
            # own feasible, and the prompt invites it to assess again when it changes its mind.
            # On the 2026-09-23 bench a pilot that a person had just said go to assessed the
            # same doubt again, as infeasible, and the run ended on the question it had already
            # asked and been answered. Something new it sees is still a reason to assess again.
            #
            # And it hears who cleared it only where somebody did. `--yes`, a flock's standing
            # answer and a pipe all answer go without asking anyone, and the record and the
            # pilot's ears are held to the same rule as a `prompt` row (`a_person_was_asked`):
            # a witness nobody was is worse than none. Its doubt is settled all the same, so
            # it is told not to raise it again either way.
            cleared = (
                "a person read that and said go"
                if asked
                else "this run was started to go ahead without asking anybody (--yes, a "
                "flock's standing answer or a pipe)"
            )
            return (
                VerbResult.success(
                    f"recorded {verdict.summary()}; {cleared}, so verbs that move the body now "
                    "run. Do not assess again on the same doubt, only on something new you see"
                ),
                None,
            )
        return (
            VerbResult.success(f"recorded {verdict.summary()}; verbs that move the body now run"),
            None,
        )

    # ── a person's word on the task, for a pilot that cannot tell ─────────────────────────

    def _judgement(self, question: str) -> dict[str, Any]:
        """Put `question` to whoever `RunConfig.judge` reaches, record the exchange, and keep
        what the pilot is handed back.

        `asked` is the test that decides whether a `prompt` row is written: a pipe and a
        standing answer can both say yes, and neither is a person who watched the arm. An
        asker that raised, on EOF or with click's `Abort`, said neither yes nor no. Its answer
        stays None, `raised` names what it raised, and no `prompt` row says somebody answered,
        the way the confirm gate records a prompt that raised.

        The seconds the question takes are the person's and not the run's. They come off
        `max_minutes` the way a `--by-hand` handover's do, so a yes given after a long look at
        the arm is still the answer the run ends on, and not a spent budget."""
        judge = self.cfg.judge
        judged: dict[str, Any] = {"question": question, "answer": None, "asked": False}
        if judge is not None:
            put_at = self.budget.now()
            try:
                judged["answer"] = bool(judge(question))
            except Exception as e:
                judged["raised"] = type(e).__name__
            finally:
                if self.budget.started_at is not None:
                    self.budget.started_at += max(0.0, self.budget.now() - put_at)
            judged["asked"] = a_person_was_asked(judge)
            if "raised" not in judged:
                self._ask_recorded("judge", question, judged["answer"], judge)
        self.judged = judged
        return judged

    def _judged_observation(self, obs: Observation) -> Observation:
        """`obs` with a person's answer on it, where the pilot is one that cannot tell whether
        its task was done and says it is time to ask. Every other turn, and every other pilot,
        gets `obs` as it was."""
        pilot = self.cfg.provider
        if self.judged is not None or not isinstance(pilot, JudgedPilot):
            return obs
        question = pilot.judge_question([*self.history, Exchange(observation=obs)])
        if question is None:
            return obs
        features = {**obs.features, JUDGE_FEATURE: self._judgement(question)}
        return obs.model_copy(update={"features": features})

    def _a_person_said_yes(self) -> bool:
        """Whether a person really asked said the arm did it: the only thing that lets a
        `JudgedPilot`'s run end in success."""
        judged = self.judged
        return judged is not None and judged["asked"] is True and judged["answer"] is True

    def _cut_short(self, reason: str) -> str:
        """Why a budget ended a `JudgedPilot`'s run, with what a person said about the segments
        that ran before it did. They are asked now if they were not yet, so a run that spent its
        seconds on two subtasks of three still has somebody's word on those two. The outcome
        stays the budget's: a yes about part of a task is not the task done."""
        pilot = self.cfg.provider
        if not isinstance(pilot, JudgedPilot):
            return reason
        judged = self.judged
        if judged is None:
            question = pilot.judge_question(self.history, cut_short=reason)
            if question is None:
                return reason
            judged = self._judgement(question)
        if not judged["asked"]:
            return f"{reason}; no person was asked whether the arm did what ran"
        if "raised" in judged:
            return (
                f"{reason}; a person was asked whether the arm did what ran, and the prompt "
                f"raised {judged['raised']} before they answered"
            )
        said = "the arm did it" if judged["answer"] else "it did not"
        return f"{reason}; a person watched what ran and said {said}"

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
        """Older pictures are dropped to keep context small; the last N exchanges keep theirs.

        N counts exchanges rather than images, so a body with two cameras sends twice the
        pictures of a body with one, which is the cost of the second view and is what the
        arm's page says it costs."""
        n = self.cfg.keep_images_for_last_n
        # A model that binds each replayed thinking block to everything before it (Claude Opus
        # 5.5, Fable 5.1) is trimmed in steps rather than on every call, because a trim that
        # takes a frame away edits the older messages and so invalidates the blocks produced
        # while that frame was still sent. From the trim call on, those blocks are left out,
        # which Anthropic allows for a leading run of them, so the blocks after them form an
        # unbroken run that stays valid until the next trim. The latest assistant message is
        # the one exception: its blocks may not be filtered, so on a trim call the API is asked
        # to drop them instead (`drop_block`). Every other provider has a period of 1, which is
        # a trim every call and nothing left out.
        period = max(1, int(getattr(self.cfg.provider, "frame_trim_period", 1) or 1))
        cut = max(0, len(self.history) - n)
        cut -= cut % period
        forget = getattr(self.cfg.provider, "without_thinking", None) if period > 1 else None
        stale = self._last_invalidated(cut, n, period) if forget is not None else -1
        latest = max(
            (i for i, ex in enumerate(self.history) if ex.decision is not None), default=-1
        )
        out: list[Exchange] = []
        for i, ex in enumerate(self.history):
            if forget is not None and i <= stale and i != latest and ex.decision is not None:
                ex = ex.model_copy(update={"decision": forget(ex.decision)})
            if ex.observation.images and i < cut:
                ex = ex.model_copy(
                    update={"observation": ex.observation.model_copy(update={"images": []})}
                )
            out.append(ex)
        return out

    def _last_invalidated(self, cut: int, n: int, period: int) -> int:
        """The last exchange whose thinking block a trim at `cut` invalidated, or -1 for none.

        Exchange `j` was decided on the call that appended it, when the trim stood at `c`, so
        its block was bound to every frame from `c` up to and including its own. It is
        invalid once one of those frames is no longer sent. A trim that took nothing away, as
        on a run with `--no-vision` or a body with no camera, invalidates nothing, and the
        whole run is replayed as it was produced."""
        last = -1
        for j, ex in enumerate(self.history):
            if ex.decision is None:
                continue  # a prose turn, its re-prompt or the one pending: no block of its own
            c = max(0, j + 1 - n)
            c -= c % period
            if any(self.history[k].observation.images for k in range(c, min(cut, j + 1))):
                last = j
        return last

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
        # A body that tries its connect again when the bus loses a packet (the LeRobot arm) is
        # told how to hear a stop while it does. The kill switch's first press only sets the
        # abort flag and cancels nothing, so without this a Ctrl-C during a connect that was
        # failing got every attempt still to come, each one switching torque off and on again
        # on every motor, and a later one that connected went on into the start of the run.
        # Read with `getattr`, so a body with no such thing is called exactly as it always was,
        # and taken back once the connect is over: it is the connect's and nothing else's.
        stop_check = getattr(cfg.transport, "set_stop_check", None)
        if callable(stop_check):
            stop_check(self.executor.abort.is_set)
        try:
            connected = await cfg.transport.connect()
        finally:
            if callable(stop_check):
                stop_check(None)
        # Everything from here to the first step can raise: a task that needs a verb this
        # build does not have, a person who would not confirm they were watching, a state
        # read that timed out. The arm is connected and holding by then, and the run's own
        # `finally` does not exist yet, so a failure in this window used to exit with the
        # arm energised, away from any pose anybody chose, and nothing said about it.
        try:
            connect_s = round(time.perf_counter() - connect_started, 3)
            # What the body had to do to get connected, in its own words: a connect it tried
            # again after the bus lost a packet (the LeRobot arm's `connect_notes`). The body
            # logged each one as it happened; this is what puts them in the run's record, so a
            # joint whose cable drops a packet every session shows up across transcripts.
            for note in getattr(cfg.transport, "connect_notes", ()) or ():
                self._note(str(note))
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
                # the CLI guessed from the description; this is what the robot actually has.
                # A detector the run was given is kept. One `explicit_detector` built before
                # connect learns the lens the body reported (the board's, when the board's
                # camera is the only one this body has). One handed in from Python is not
                # touched: it keeps the lens it was built with, and `Detector` asks for no
                # `calibrate`, so one of its own may mean something else.
                lens_fov = cfg.fov_deg or manifest.limits.get("camera_fov_deg")
                cfg.detector = detector_for(
                    manifest.sensors,
                    cfg.detector,
                    fov_deg=lens_fov,
                    backend=backend_name(cfg.transport),
                )
                calibrate = getattr(cfg.detector, "calibrate", None)
                # only a body with a camera has a lens to learn; one without keeps a detector
                # that reads nothing, and a warning about its field of view would be about a
                # camera it has not got
                if (
                    getattr(cfg.detector, "lens_at_connect", False) is True
                    and callable(calibrate)
                    and "camera" in manifest.sensors
                ):
                    calibrate(lens_fov, backend=backend_name(cfg.transport))
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
                if cfg.acknowledge is not None:
                    watching = bool(cfg.acknowledge(warning))
                    # The one question whose answer was nowhere in the record: a yes left no
                    # mark at all, so a run on a robot that cannot see a fall could not be
                    # told from one nobody was watching.
                    self._ask_recorded("acknowledge", warning, watching, cfg.acknowledge)
                    if not watching:
                        raise Aborted(
                            "nobody confirmed they were watching a robot that cannot see a fall"
                        )
            # The task file's word on the body's learned policy, made into the verb itself: its
            # listed instructions the only ones `manipulate` takes, its segment the one the
            # verb runs and its timeout covers. Here, between the allowlist becoming final and
            # the tools being built, because the model's tools, the stepper's choices and the
            # executor's check are all read off this one registry from now on.
            narrow_policy_verb(registry, self.fm, cfg.transport)
            tools = registry.tool_schemas(allow) + META_TOOLS
            if cfg.link is not None:
                tools = [*tools, TELL]
            memory_text: str | None = None
            if cfg.memory is not None:
                tools = [*tools, REMEMBER]
                memory_text = cfg.memory.recall()
            # The stepper, or None, which is every run that does not ask for one and the only
            # path below that existed before there was a flag. Built here because its whole
            # vocabulary is the allowlist's discrete calls, and `allow` is not final until the
            # verbs this robot turned out not to have have been dropped from it, just above.
            stepper: Stepper | None = None
            if cfg.decision != "off":
                # A mode with nobody to answer it is a caller that asked for a decision LLM and
                # never built one, which is a bug in the CLI rather than a run to start quietly
                # with every choice going to the model anyway.
                if cfg.decision_llm is None or cfg.decision_price is None:
                    raise ValueError(
                        f"decision={cfg.decision!r} needs both decision_llm and decision_price: "
                        "build them with `quackd.agent.decision.factory` before the run starts"
                    )
                stepper = Stepper.build(
                    mode=cfg.decision,
                    llm=cfg.decision_llm,
                    price=cfg.decision_price,
                    registry=registry,
                    allow=allow,
                    goal=self.duck.body,
                    success=self.fm.success,
                    gated=self.fm.verbs.confirm,
                    max_steps=self.budget.limits.max_steps,
                    body=manifest.summary()
                    if manifest is not None
                    else backend_name(cfg.transport),
                )
            system = build_system_prompt(
                self.duck,
                [registry.view(n) for n in allow],
                backend_name(cfg.transport),
                manifest=manifest,
                memory_text=memory_text,
                assumptions=first_state.extras.get("assumptions") or None,
                flock_text=cfg.link.prompt_section() if cfg.link is not None else None,
                task_images=[p.name for p in cfg.task_images] or None,
                by_hand=cfg.hand_off is not None and not cfg.dry_run,
                adapter=adapter_name(cfg.transport),
                # as `_observe` decides whether an observation carries the frames it read
                sees=bool(cfg.provider.supports_vision),
            )
            system += getattr(cfg.provider, "prompt_hint", "") or ""  # e.g. the local JSON fallback
            # before `run_start`, so a reader of the record meets the pictures the task is
            # about before the run that was given them
            self.transcript.save_task_images(cfg.task_images)
            self._emit(
                "run_start",
                duck=self.fm.name,
                duck_path=self.duck.path,
                provider=cfg.provider.name,
                model=cfg.provider.model,
                # Fields a passthrough added to every request (#12). A run whose model was told not
                # to think reads very differently from one that was, and the transcript is the only
                # place a reader can tell which they are holding.
                # Credential-shaped keys taken out on the way in. `--extra-body` is
                # documented as a field the vendor wants and quackd never reads, and an
                # `authorization` header is the canonical such field; it also arrives
                # from `QUACKD_EXTRA_BODY`, which no amount of argv redaction can reach.
                extra_body=redacted_body(getattr(cfg.provider, "extra_body", None)),
                transport=backend_name(cfg.transport),
                adapter=adapter_name(cfg.transport),
                robot=manifest.model_dump(mode="json") if manifest is not None else None,
                dry_run=cfg.dry_run,
                contract=self.fm.model_dump(),
                system_prompt=system,
                tools=[t["name"] for t in tools],
                memory=cfg.memory.summary() if cfg.memory is not None else None,
                images=[p.name for p in cfg.task_images],
                flock=cfg.link.describe() if cfg.link is not None else None,
                connect_s=connect_s,
                # Which detector read the frames, or null for a body with nothing to look at,
                # and the board when `--host` named one. The same detector for the whole run,
                # so this is the answer for every detection in the transcript.
                detector=self._detector_record(manifest),
                **({"host": self._host_record(manifest)} if cfg.host is not None else {}),
                # What this run was called and when it began. The directory name held both
                # until now, at second precision on the local clock, and stopped being
                # evidence the moment anybody renamed the folder or copied it off the machine.
                run_name=cfg.run_name,
                # What was asked for, and by what. Every flag is half the story of a run
                # (which robot, which model, which budget, whether it was a dry run), and
                # reading a transcript a month later used to mean guessing at them. The
                # values of `--api-key`, `--token`, `--host-token` and `--policy-token` never
                # appear (`quackd.command`).
                command=command_line(),
                version=__version__,
                started_at=self.transcript.started_at_iso,
                # The rate this run is costed at, written down rather than looked up later, so
                # a replay prices it at what it cost on the day rather than at whatever the
                # catalogue says months from now (`providers.pricing`). `null` is a model
                # quackd has no rate for, and says so rather than implying a free one.
                price=self.price.record() if self.price is not None else None,
                # Which decision LLM answered the choices, and at what rate, for the same
                # reason: a transcript that recorded only `jev-1.13.0` could not tell a
                # reader whether that was TypeSafe's API or a server on the bench. The url is
                # redacted here rather than left to argv redaction, because it can arrive from
                # `QUACKD_DECISION_URL`, which never appears in argv for redaction to reach.
                **(
                    {
                        "decision_price": stepper.price.record(),
                        "decision_llm": {
                            "name": stepper.name,
                            "model": stepper.model,
                            "url": redacted_url(stepper.url) if stepper.url is not None else None,
                        },
                    }
                    if stepper is not None
                    else {}
                ),
                # The policy the arm hands its segments to, when it has one: the server's
                # address, redacted, and the checkpoint it serves, as the connect heard it.
                # Only when there is one, so every other run's record stays what it was.
                **({"policy": served} if (served := self._policy("policy_served")) else {}),
            )
            outcome: Outcome = "error"
            reason = "loop exited unexpectedly"
            last_verb: str | None = None
            last_result: VerbResult | None = None
            last_llm: dict[str, Any] = {}
            """What the model's last answer cost, for the shadow record beside it."""
            mine_verb: str | None = None
            mine_result: VerbResult | None = None
            """The last verb the *model* chose, and what it returned.

            Not the same as `last_verb`/`last_result`, which are the most recent of anybody's.
            The model's next observation is the tool_result answering the model's own tool
            call, so it has to carry that call's outcome: with a stepper running, the most
            recent verb is usually one the model never asked for, and putting its summary
            under the model's own tool_use id answers a question with somebody else's answer.
            What the stepper did goes in `self.stepped` instead, which says who chose it."""
            retry_prompted = False

            self.budget.start()
            self.heartbeat.start()
        except BaseException:
            await self._park_before_the_run_began()
            raise
        try:
            # a run starts from the pose it will end at, so what the pilot improvises from is
            # the same arm every time rather than wherever the last run put it down
            # `recorded` as well as `reached`, matching the flock member: a body that answers
            # `none` has no pose to be away from, and ending a run over that would punish a
            # transport for not being one quackd parks rather than for failing to park.
            parked = await self._rest()
            if parked is not None and parked.recorded and not parked.reached:
                raise Aborted(f"the arm did not reach its rest pose: {parked.reason}")
            # and then, where somebody asked for it, the arm is theirs to place: released at
            # that pose, held again wherever they leave it, and the pilot improvises from
            # there instead of from the fold
            if cfg.hand_off is not None:
                await self._hand_over()
            while True:
                await asyncio.sleep(0)  # let the heartbeat and kill switch run
                if self.executor.abort.is_set():
                    # a flock stops its members when one of them breaks, and the record has to
                    # say which one rather than blaming a kill switch nobody pressed
                    raise Aborted(self._abort_reason())
                observe_started = time.perf_counter()
                obs, _ = await self._observe(
                    last_verb, last_result, self.stepped, mine=(mine_verb, mine_result)
                )
                observed_s = round(time.perf_counter() - observe_started, 3)
                # before the observation is recorded, so the record of the turn carries the
                # person's answer the pilot is about to read, and after its clock stops, so
                # the seconds they took to answer are not counted as the observation's
                obs = self._judged_observation(obs)
                self._emit(
                    "observation",
                    step=self.budget.steps,
                    text=obs.text,
                    has_image=bool(obs.images),
                    features=obs.features,
                    elapsed_s=observed_s,
                    **detect_times(self.cfg.detector),
                )

                # The stepper answers first where it can. It reads the same turn the model
                # would have been given and nothing else: no picture, no system prompt, no
                # history. It is only ever offered verbs the executor would run *this* turn,
                # so a stepper-authored call never meets the verdict gate or the allowlist.
                advice: Advice | None = None
                stepper_call: ToolCall | None = None
                if stepper is not None:
                    advice = await stepper.advise(
                        obs,
                        cleared=self.executor.cleared,
                        budget=self.budget.status(),
                        stepped=self.stepped,
                        notes=memory_text,
                    )
                    self._emit("decision", step=self.budget.steps, **advice.event())
                    if cfg.decision == "on":
                        stepper_call = advice.call

                call: ToolCall
                if stepper_call is not None:
                    # No `Exchange` and no `Decision`: this turn never enters the model's
                    # history, so nothing the stepper chose can come back to the model as
                    # something it said. What the model is told is `self.stepped`, on the next
                    # observation it is actually shown.
                    call = stepper_call
                    self.budget.note_stepper_call()
                else:
                    if self.history and self.history[-1].decision is not None:
                        obs = obs.model_copy(
                            update={"tool_call_id": self.history[-1].decision.tool_call.id}
                        )
                    self.history.append(Exchange(observation=obs))
                    self.budget.note_llm_call()  # may raise BudgetExceeded: then no request is made
                    history = self._history_for_provider()
                    self._emit(
                        "llm_request",
                        step=self.budget.steps,
                        provider=cfg.provider.name,
                        model=cfg.provider.model,
                        messages=len(history),
                        images=sum(len(ex.observation.images) for ex in history),
                        with_image=sum(1 for ex in history if ex.observation.images),
                        task_pictures=sum(len(ex.observation.attachments) for ex in history),
                        reprompt=retry_prompted,
                    )
                    llm_started = time.perf_counter()
                    try:
                        turn = await cfg.provider.step(system, history, tools)
                    except BaseException as e:
                        # BaseException, not Exception. The second Ctrl-C raises a bare
                        # KeyboardInterrupt and a flock cancels its members, and both land
                        # here: with `Exception` the wait was dropped from `llm_latency_s` AND
                        # no `llm` record was written, so the seconds a person actually spent
                        # waiting on a hanging model could not even be recovered by hand. That
                        # is the one case the number exists for.
                        #
                        # The call that failed is part of the record: what, and after how long.
                        # Its seconds count towards `llm_latency_s` like any other call's,
                        # because the run waited every one of them, and a provider that hangs
                        # until it times out is exactly the case somebody reads that number
                        # to find.
                        latency_s = round(time.perf_counter() - llm_started, 3)
                        self.llm_latency_s += latency_s
                        self._emit(
                            "llm",
                            step=self.budget.steps,
                            provider=cfg.provider.name,
                            model=cfg.provider.model,
                            error=f"{type(e).__name__}: {e}",
                            latency_s=latency_s,
                        )
                        raise
                    # One reading of the clock and one costing, used by all three records
                    # below. They each called `perf_counter()` for themselves before, so the
                    # shadow record and the log disagreed about the same call by however
                    # long the lines between them took.
                    latency_s = round(time.perf_counter() - llm_started, 3)
                    self.llm_latency_s += latency_s
                    self.usage = self.usage + turn.usage
                    turn_usage = turn.usage.model_dump()
                    turn_cost = cost_usd(turn_usage, self.price) if self.price is not None else None
                    if turn_cost is not None and self.cost_usd is not None:
                        self.cost_usd = round(self.cost_usd + turn_cost, 6)
                    last_llm = {
                        "latency_s": latency_s,
                        "usage": turn_usage,
                        "cost_usd": turn_cost,
                    }
                    self._emit(
                        "llm",
                        step=self.budget.steps,
                        provider=cfg.provider.name,
                        model=cfg.provider.model,
                        text=turn.text,
                        tool_calls=[tc.model_dump() for tc in turn.tool_calls],
                        usage=turn_usage,
                        stop_reason=turn.stop_reason,
                        thinking=turn.thinking,
                        latency_s=latency_s,
                        usage_total=self.usage.model_dump(),
                        llm_calls=self.budget.llm_calls,
                        cost_usd=turn_cost,
                        cost_usd_total=self.cost_usd,
                        # only when a server-side fallback answered, so every turn the model
                        # asked for took, and every transcript from before this, reads as it did
                        **({"served_by": turn.served_by} if turn.served_by else {}),
                    )
                    self.budget.check_time()

                    if not turn.tool_calls:
                        if not retry_prompted:
                            retry_prompted = True
                            self.history[-1].decision = None
                            self.history.append(
                                Exchange(
                                    observation=Observation(text=REPROMPT, features=obs.features)
                                )
                            )
                            self._emit(
                                "enforce",
                                step=self.budget.steps,
                                issue="no_tool_call",
                                action="re-prompt",
                                text=REPROMPT,
                            )
                            continue
                        outcome, reason = (
                            "failure",
                            "the model produced no tool call twice in a row",
                        )
                        break
                    retry_prompted = False
                    if len(turn.tool_calls) > 1:
                        self._emit(
                            "enforce",
                            step=self.budget.steps,
                            issue="multiple_tool_calls",
                            action="first_only",
                        )
                    call = turn.tool_calls[0]
                    self.history[-1].decision = Decision(
                        tool_call=call, text=turn.text, raw=turn.raw
                    )
                    if advice is not None and (
                        cfg.decision == "shadow" or stepper.compares(advice)  # type: ignore[union-attr]
                    ):
                        # Shadow mode's whole point: what the stepper would have done, beside
                        # what the model did, on the same reading, in one record. And in `on`
                        # for a turn that offered a call the stepper may never take, which is
                        # how its answers on those turns are ever measured.
                        self._emit(
                            "decision_shadow",
                            step=self.budget.steps,
                            **stepper.shadow_event(advice, call, last_llm),  # type: ignore[union-attr]
                        )
                    self.stepped.clear()

                if call.name == REMEMBER_NAME:
                    # a note for next time: no robot motion, no step against the budget
                    last_verb = mine_verb = REMEMBER_NAME
                    last_result = mine_result = self._remember(call.arguments)
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
                    last_verb = mine_verb = TELL_NAME
                    last_result = mine_result = self._tell(call.arguments)
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
                    last_verb = mine_verb = ASSESS_TASK_NAME
                    standing = self.executor.verdict
                    last_result, ends_with = self._assess(call.arguments)
                    mine_result = last_result
                    recorded = self.executor.verdict
                    if recorded is standing:
                        # this call recorded nothing, so the row describes the call that was
                        # refused rather than whatever verdict happened to be standing. Before
                        # this, a refused re-assessment was written into the transcript with
                        # the *earlier* verdict's word, reason and needs, and read as though
                        # that one had been refused.
                        recorded = None
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
                        # what said go or no, where this call's doubt was answered, or what
                        # the prompt raised, where it went unanswered
                        **(
                            (
                                {"raised": self.unanswered}
                                if self.unanswered is not None
                                else {"answered_by": self.answered_by}
                            )
                            if recorded is not None and recorded.human is not None
                            else {}
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
                    if (
                        outcome == "success"
                        and isinstance(cfg.provider, JudgedPilot)
                        and not self._a_person_said_yes()
                    ):
                        # A pilot that cannot tell whether its task was done does not get to
                        # say it was. `providers.vla` never tries; this holds any pilot that
                        # says it cannot judge to the same rule, whatever it declares.
                        outcome = "failure"
                        reason = (
                            f"{reason or 'the pilot declared success'}, and no person asked "
                            "said the arm did it, which only a person can say for this pilot"
                        )
                    self._emit("declare", step=self.budget.steps, outcome=outcome, reason=reason)
                    break

                last_verb = call.name
                try:
                    last_result = await self.executor.run_verb(
                        call.name,
                        call.arguments,
                        # who chose it, so the log says `from decision` and the transcript
                        # records a verb the model never saw as the stepper's own
                        source="decision" if stepper_call is not None else "agent",
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
                if stepper_call is not None:
                    # the model did not choose this and will not see it in its own history,
                    # so it is told once, in the observation it is next shown
                    self.stepped.append(
                        f"{call.name}({fmt_params(call.arguments)}): "
                        f"{'ok' if last_result.ok else 'FAILED'} - {last_result.summary}"
                    )
                else:
                    # and this one the model did choose, so its next observation is the
                    # tool_result answering it and must carry this outcome rather than
                    # whatever the stepper did in between
                    mine_verb, mine_result = call.name, last_result
        except BudgetExceeded as e:
            outcome, reason = "budget", str(e)
            # a question to a person, and a pilot's code, inside a handler: neither may lose
            # the budget's own reason or keep the teardown below from stopping the robot
            try:
                with contextlib.suppress(Exception):
                    reason = self._cut_short(reason)
            except BaseException as interrupted:
                # the CLI's second Ctrl-C, or a cancel, while that question waits. The handler
                # for an interrupt below never sees one raised in here, so this one says it:
                # the budget still ended the run, and the record names the question it cut off
                what = type(interrupted).__name__
                reason = f"{reason}; the question about what ran was interrupted ({what})"
                self._emit("note", text=f"run interrupted: {what}")
                raise
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
                await self.executor.logged_transport().stop()
            with contextlib.suppress(Exception):
                # a take-hold that stop made over an arm in a person's hands, and was refused,
                # said to them and put on the record, which the stop itself does neither of
                self._refused_in_the_stop()
            if self._handed_over:
                # between the stop, which is holding the arm where the run left it, and the
                # rest move, which folds it: the one moment where opening the gripper is
                # neither fighting a verb nor happening after the arm has already folded up
                with contextlib.suppress(Exception):
                    await self._hand_back()
            folded: RestResult | None = None
            with contextlib.suppress(Exception):
                # after the stop and before the close: the stop holds the arm where it is,
                # and the close is what releases torque, so this is the only window in which
                # putting it down changes whether it falls
                folded = await self._rest()
            with contextlib.suppress(Exception):
                # and where it could not be put down, the person at the terminal may take
                # torque off while they hold it, before the close keeps it on
                await self._offer_release(folded)
            final_state: dict[str, Any] = {}
            with contextlib.suppress(Exception):
                final_state = (await cfg.transport.get_state()).model_dump()
            with contextlib.suppress(Exception):
                await cfg.transport.close()
            if note := getattr(cfg.transport, "close_note", None):
                self._note(str(note))
            # The whole run, on the transcript's own clock, which started the instant the
            # record opened rather than when the budget did. Read once so `ended_at`,
            # `wall_s` and `run_end.t` are three spellings of one number.
            wall_s = round(self.transcript.elapsed_s, 3)
            summary = {
                "duck": self.fm.name,
                "run_name": cfg.run_name,
                "command": command_line(),
                "version": __version__,
                "outcome": outcome,
                "reason": reason,
                "steps": self.budget.steps,
                "llm_calls": self.budget.llm_calls,
                # Three clocks that were one, and they answer different questions. `elapsed_s`
                # is the budget's: it starts after `run_start`, restarts after a `--by-hand`
                # handover, and on a simulator it is the simulator's own time, which is what
                # `max_minutes` is checked against and all it is. `wall_s` is the run as
                # somebody standing next to the robot experienced it, connect and teardown
                # included. `llm_latency_s` is how much of that was spent waiting on the
                # model, which on the arm was 62.1 seconds of 78.8 and had to be added up by
                # hand to say so.
                "elapsed_s": round(self.budget.elapsed_s, 2),
                "started_at": self.transcript.started_at_iso,
                "ended_at": self.transcript.ended_at_iso(wall_s),
                "wall_s": wall_s,
                "connect_s": connect_s,
                "llm_latency_s": round(self.llm_latency_s, 3),
                "usage": self.usage.model_dump(),
                # What it cost and what it was costed at. `cost_usd` is the model only: the
                # stepper bills separately and keeps its figure in its own block below.
                "price": self.price.record() if self.price is not None else None,
                "cost_usd": self.cost_usd,
                "provider": cfg.provider.name,
                "model": cfg.provider.model,
                "transport": backend_name(cfg.transport),
                "robot": manifest.id if manifest is not None else None,
                "dry_run": cfg.dry_run,
                "final_state": final_state,
                # what a view could not show. The record has every one of them; a console
                # that swallowed a hundred events used to leave no sign anywhere.
                "log_dropped": self.event_log.dropped,
                # only when there was one, so every summary written before the stepper
                # existed, and every run that does not ask for one, stays byte for byte
                # what it was
                **({"decision": stepper.summary()} if stepper is not None else {}),
                # and the arm's policy by the same rule: what it is, what its segments
                # counted and the round trips to its server, never an action it sent
                **({"policy": block} if (block := self._policy("policy_record")) else {}),
            }
            # the only unguarded statements in this teardown used to be these three, so a
            # disk that filled at `run_end` skipped summary.json, leaked the file handle,
            # skipped the episode, and replaced the run's real exception with an OSError
            # Kept on the loop as well as returned, because a member that raises never
            # reaches its `return RunResult(...)` and the flock would otherwise throw away the
            # wall clock, the model seconds and the bill it had already measured.
            self.summary = summary
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
            log_dropped=self.event_log.dropped,
            decision_calls=self.budget.stepper_calls,
            summary=summary,
        )


def _joints_line(joints: Mapping[str, float]) -> str:
    """The pose a person set, in one line they can read back off the arm."""
    if not joints:
        return "the arm reported no joint"
    return "It is at " + ", ".join(f"{j} {v:.0f}" for j, v in sorted(joints.items()))


def _listed(names: Sequence[str]) -> str:
    """Joint names as a sentence lists them, "a", "a and b", "a, b and c", the way the arm's
    own lines join them, so the person holding it hears one list the same way twice."""
    items = list(names)
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


async def run_duck(cfg: RunConfig) -> RunResult:
    return await AgentLoop(cfg).run()
