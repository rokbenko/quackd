"""Which turns are a choice, and what an answer must clear before it moves a servo.

quackd's loop asks one question a turn and pays a frontier model's full latency for it whether
the answer is `report_state` or a six-joint pose. On the SO-101 run at the top of `README.md`
that is 62.1 seconds of a 78.8 second run. Some of those turns are not writing, they are
choosing, and a decision LLM answers a choice without generating anything.

Which turns those are is decided here, from each tool's own JSON Schema and nothing else, so a
body quackd has never shipped is classified by the same rule as the seven that do. A verb whose
meaning is a number -- every `move_joints`, every `move` -- is not a choice and never becomes
one. On the arm that is not even a judgement call: `move_joints` takes a free-form object of
joint names the schema never lists, because they live in a `field_validator` rather than in an
enum, so there is nothing for a classifier to enumerate even in principle.

This is quackd's half and does not change with the vendor. The questions go out as plain dicts
in the System One shape, so the same four reach a hosted API, a server on this machine and a
model in this process without being rebuilt; which of those answers them is `factory.py`'s
question and `catalogue.py`'s table. Nothing here imports a backend at all.
"""

from __future__ import annotations

import asyncio
import itertools
import json
import math
import time
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from quackd.agent.decision.base import TIMEOUT_S
from quackd.agent.decision.catalogue import DecisionMode
from quackd.agent.providers.base import ToolCall
from quackd.agent.providers.catalogue import Price
from quackd.agent.providers.pricing import SELF_HOSTED, cost_usd
from quackd.command import redacted_url
from quackd.duckfile.schema import POLICY_VERB
from quackd.verbs.registry import Verb
from quackd.verdict import BEFORE_VERDICT, MOVES_THE_BODY

if TYPE_CHECKING:
    from quackd.agent.decision.base import DecisionLLM
    from quackd.agent.providers.base import Observation
    from quackd.verbs.registry import VerbRegistry

ESCALATE = "escalate"
"""The way out, offered on every turn. Without it a Choice always returns *something*, and the
confidence floor is then the only thing standing between "none of these is right" and a servo."""

MAX_CALLS_PER_VERB = 12
"""Past this a verb stops being a choice. TypeSafe's own guidance is that a question should be
a gut-check a knowledgeable person could make in a few seconds, and picking one of thirteen
shapes of the same verb is not that. The widest verb quackd ships is six (`gripper` on a
two-armed body: three sides times open or shut), so this is headroom rather than a limit. A
task file lists at most this many instructions for `manipulate` (`MAX_INSTRUCTIONS`), so each
of them is one choice."""

SHADOW_ONLY = frozenset({POLICY_VERB})
"""Verbs the stepper is offered and compared on, and never takes, whatever the mode.

`manipulate` narrowed to a task file's instructions is a closed set like any other, and which
subtask to hand the policy next is exactly the between-segment choice a decision LLM might one
day make. It answers to the confirm floor, and under `--yes` nobody is asked at that gate, so a
stepper that cleared the floor would start a segment of a learned policy driving the arm with
no person and no model involved. So it is offered, its answer is recorded beside the model's
(`shadow_event`), and the model takes the turn. Promoting it needs a measured agreement rate
and a decision of its own, not a floor."""
SHADOW_GATE = "shadow_only"
"""The gate a choice of a `SHADOW_ONLY` verb ends on: it cleared every other gate, and the turn
went to the model all the same."""

# The confidence a Choice must clear before the stepper acts on it, by what the verb does.
# TypeSafe's confidence page publishes exactly two numbers, 0.5 and 0.9, and both are here.
# The other two are quackd's, set between them, and saying so is the point: their own page
# says the right values are domain-specific and have to be tuned on your own data, so a
# number nobody published is a number nobody has calibrated either. All four are Jev-shaped
# and every other decision LLM inherits them unmeasured, which is what `--decision-mode
# shadow` is for: it records what would have happened at each of these, on whichever one you
# named, and that is how they get moved.
FLOORS: dict[str, float] = {
    # `stop`, and deliberately the lowest floor in the system. Below 0.5 is "genuinely unsure"
    # in TypeSafe's own words, and 0.5 is exactly where an unsure stepper should still be
    # allowed to reach for the brake: a wrong `stop` costs one step, and a wrong anything-else
    # costs a move nobody chose.
    "brake": 0.50,
    # Sends no intent: reads state or a camera. quackd's number, not theirs, set just above
    # the 0.5 they call genuinely unsure, because a read that is wrong costs a wasted turn.
    "read": 0.60,
    # Everything that sends an intent. quackd's number too, set below the 0.9 they pair with
    # "proceed with confirmation", because quackd expresses confirmation separately, below.
    # Nothing published sits between their two, so this one is an appetite for risk rather
    # than a calibration, and `--decision-mode shadow` is how it earns a better value.
    "motion": 0.85,
    # A verb the manifest or the `.duck` gated on a human. Literally their ">0.9, high stakes,
    # proceed with confirmation" — and quackd's own confirm gate still runs on top of it, so a
    # person is still asked.
    "confirm": 0.90,
    # Not a floor, a refusal: the label is never offered, so no confidence can reach it.
    "never": 1.01,
}


@dataclass(frozen=True)
class Call:
    """One concrete tool call the stepper may author, and the words it is offered in.

    `label` is what the decision LLM chooses between and what the log prints, so it has to
    read like something a person would say out loud: `gripper(open=false)`, not a schema
    fragment."""

    name: str
    arguments: dict[str, Any]
    label: str


# ── what counts as a choice ─────────────────────────────────────────────────────────────


def _closed_values(spec: Mapping[str, Any]) -> list[Any] | None:
    """The values this property can take, when they are a closed set, else None."""
    if "const" in spec:
        return [spec["const"]]
    if isinstance(spec.get("enum"), list):
        return list(spec["enum"])
    if spec.get("type") == "boolean":
        return [True, False]
    return None


def _allows_null(spec: Mapping[str, Any]) -> bool:
    if spec.get("type") == "null":
        return True
    return any(
        isinstance(member, Mapping) and member.get("type") == "null"
        for member in spec.get("anyOf") or ()
    )


def _inert(spec: Mapping[str, Any], *, required: bool) -> bool:
    """Whether leaving this property out chooses nothing by leaving it out.

    This is the hinge of the whole rule, and the two cases it separates look alike until you
    read the default. `gaze` has a nullable `bearing_deg` defaulting to null: omitted, it has
    no value at all, and the verb does exactly what its enum says. `move` has `vx` defaulting
    to 0.15, so `move()` walks the robot at 0.15 m/s — a speed chosen by not choosing. A
    default of null means nothing; a default of 0.15 means 0.15."""
    if required:
        return False
    return "default" in spec and spec["default"] is None and _allows_null(spec)


def _render(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return value
    return json.dumps(value)


def _label(name: str, arguments: Mapping[str, Any]) -> str:
    """How one concrete call is spelled, everywhere it is spelled.

    The decision LLM chooses between these strings and `_route` matches the answer back by
    equality, so the spelling is load-bearing rather than cosmetic. Sorted by key, so a call
    built here and a call that came back from a provider render the same whatever order their
    keys arrived in."""
    shown = ", ".join(f"{prop}={_render(arguments[prop])}" for prop in sorted(arguments))
    return f"{name}({shown})" if shown else name


def _finite(value: Any) -> tuple[float, bool]:
    """A probability as a number, and whether it was one.

    Three things are not a probability and all three used to read as one.

    NaN and the infinities are not low values, they are absent ones, and they lose every
    comparison they are put through: `nan < 0.85` is False, which clears a floor rather than
    missing it.

    `None` is not zero. It is what comes back when a backend did not answer that question at
    all, and zero is a confident answer: read as one, a missing `done` says "certainly not
    finished" and a missing `need_human` says "certainly nobody is needed", which are the two
    answers that let everything after them move a servo.

    `True` is not one. Python will float it to 1.0 without complaining, and 1.0 clears every
    floor in the table, so a backend that spelled a confidence as a boolean would be believed
    absolutely rather than doubted.

    The caller hands the turn back to the model whenever this says False."""
    if value is None or isinstance(value, bool):
        return 0.0, False
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0, False
    return (number, True) if math.isfinite(number) else (0.0, False)


def discrete_calls(schema: Mapping[str, Any]) -> list[Call] | None:
    """Every concrete call this tool allows, or None when it is not a choice.

    Takes a whole tool schema, as `Verb.tool_schema()` builds it. A tool is a choice when every
    property it has is either a closed set — an enum, a const, or a boolean — or inert. One
    property that is a number, a free string, an object or an array and the answer is a value
    rather than a choice: that turn belongs to the model, whatever else is true about it.
    """
    name = str(schema.get("name") or "")
    inner = schema.get("input_schema") or {}
    properties: Mapping[str, Any] = inner.get("properties") or {}
    required = set(inner.get("required") or ())
    closed: list[tuple[str, list[Any]]] = []
    for prop in sorted(properties):
        spec = properties[prop]
        if not isinstance(spec, Mapping):
            return None
        # An object or an array is continuous whatever else it says about itself. The arm's
        # `positions` is the case this exists for: a free-form map of joint name to degrees
        # whose keys the schema never lists.
        if spec.get("type") in ("object", "array"):
            return None
        values = _closed_values(spec)
        if values is not None:
            closed.append((prop, values))
        elif not _inert(spec, required=prop in required):
            return None
    total = 1
    for _prop, values in closed:
        total *= max(len(values), 1)
    if total > MAX_CALLS_PER_VERB:
        return None
    calls: list[Call] = []
    for combination in itertools.product(*(values for _prop, values in closed)):
        arguments = {
            prop: value for (prop, _values), value in zip(closed, combination, strict=True)
        }
        calls.append(Call(name=name, arguments=arguments, label=_label(name, arguments)))
    return calls


def verb_class(
    verb: Verb, canonical: str | None = None, gated: frozenset[str] = frozenset()
) -> str:
    """Which confidence floor this verb answers to. Always a key of `FLOORS`.

    Read off what the verb says about itself rather than off its name, with the one exception
    the verdict gate already makes (`safety.py`): `MOVES_THE_BODY` wins over `read_only`,
    because a verb arriving under a name quackd has recorded as motion while claiming to only
    read is saying two contradictory things, and quackd believes its own record.

    `gated` is the task contract's own `verbs.confirm`, canonical names, and it is here because
    a floor that could not see it was a floor that disagreed with the executor. `Executor.
    needs_confirm` treats a verb the `.duck` named exactly as it treats one the manifest marked
    `confirm`; without this, a verb its author gated on a person answered to the 0.85 motion
    floor rather than the 0.90 one, and under `--yes` nothing else would have noticed.
    """
    name = canonical or verb.name
    if verb.safety_class == "dangerous":
        return "never"
    if verb.safety_class == "confirm" or (name in gated and name != "stop"):
        # `stop` is never gated whatever a contract says, which is the same exemption
        # `Executor.needs_confirm` makes and for the same reason: the brake.
        return "confirm"
    if name == "stop":
        return "brake"
    if verb.read_only and name not in MOVES_THE_BODY:
        return "read"
    return "motion"


def labels(calls: Sequence[Call]) -> list[str]:
    """The label set for one turn's Choice: every call on offer, then the way out."""
    return [call.label for call in calls] + [ESCALATE]


# ── what one turn looks like ────────────────────────────────────────────────────────────

STATE_SOFT_CHARS = 6_000
STATE_HARD_CHARS = 24_000
"""The API allows 32k tokens of state; neither of these is near it, because the limit that
binds is accuracy rather than the API. TypeSafe say plainly that an answer gets worse as the
state grows with content unrelated to the decision, and nothing about that is particular to
them, so the cap is an accuracy budget and the trim order below is which parts of a turn are
least likely to decide it."""

NEVER_TRIMMED = ("goal", "success_when", "body", "where", "now", "last")
"""A turn without these is not a turn worth answering, so if they alone blow the hard cap the
call is not made at all and the model takes it."""

TRIM_ORDER = ("flock", "notes", "recent", "tried", "camera")
"""Dropped in this order until the state fits: the flock's chatter first, then what earlier
runs remembered, then the tail of this run, then the counters, and the camera last because on
a body that can see it is often the whole question."""

MAX_IN_A_ROW = 8
"""The most turns in a row a stepper may answer, on a run long enough for the number to mean
anything.

Only the model can record a verdict, declare an outcome or write a note, so a run that never
reaches it is a run that can only end on a budget. Eight because the longest wholly discrete
sequence quackd can presently describe is the arm's six-turn grip loop, and this has to clear
it with room.

Eight alone is not a backstop, which is the correction this constant needed. A duck's whole
step budget can be smaller than it: `hello-world` allows five, so a streak of eight can never
be reached and the model would be consulted once, or not at all, before `max_steps` ended the
run. `Stepper.streak_limit` is the number that actually holds, and it is this or half the
budget, whichever is less.

A starting value, like the floors, to be moved once `--decision-mode shadow` has said what
real runs look like."""


@dataclass(frozen=True)
class Advice:
    """One turn's answer, and the record of how it was reached.

    `call` is None whenever the model should take this turn, for any reason at all: the
    stepper declined, it was not confident enough, it thinks the job is done, it errored, or
    it was never asked. The caller does not need to know which; the record says."""

    call: ToolCall | None
    record: dict[str, Any]

    @property
    def gate(self) -> str:
        return str(self.record.get("gate", ""))

    def event(self) -> dict[str, Any]:
        return dict(self.record)


# ── the state, and the questions asked against it ───────────────────────────────────────


def _trimmed_state(raw: dict[str, str]) -> tuple[dict[str, str], list[str]]:
    """The state as it will be sent, and the names of whatever had to go.

    Trimming is recorded rather than silent: a stepper answering badly on a long run and a
    stepper answering badly on a short one are different problems, and the only way to tell
    them apart afterwards is to know what it was actually looking at."""
    state = {k: v for k, v in raw.items() if v}
    dropped: list[str] = []
    for name in TRIM_ORDER:
        if len(json.dumps(state)) <= STATE_SOFT_CHARS:
            break
        if state.pop(name, None) is not None:
            dropped.append(name)
    return state, dropped


def _fits(state: Mapping[str, str]) -> bool:
    return len(json.dumps(state)) <= STATE_HARD_CHARS


def _one_line(text: str, limit: int = 240) -> str:
    """Somebody else's prose as one field of a named state. These read text, not layout."""
    flat = " ".join(str(text).split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


DONE_THRESHOLD = 0.5
HUMAN_THRESHOLD = 0.5
"""A Noul carries no confidence, so this is the raw probability and 0.5 is "more likely than
not". Escalating when the job is not in fact done costs one model call; not escalating when it
is costs a robot that carries on working after the task is finished."""


def _escalate_criterion() -> str:
    return (
        "None of the other options is the right action now, or the right action needs a "
        "number, an angle, a distance, a target name or a sentence. Choose this whenever the "
        "answer is a value rather than one of the listed actions."
    )


def build_questions(offered: Sequence[Call], what: Mapping[str, str]) -> dict[str, dict[str, Any]]:
    """One fan-out per turn, in the shape every decision LLM takes.

    Plain dicts rather than an SDK's own `Choice` and `Noul`: the System One format spells a
    question as a type, an instruction and its criteria, and each backend reads that mapping
    itself. Built once here, they go to a hosted API, to a server on this machine and to a
    model in this process without being rebuilt for any of them.

    All four go every time. The questions are answered in parallel and in isolation against the
    same state, so adding one costs almost nothing, and `feasible` is asked on every turn even
    though v1 never acts on it: recording it beside the model's own verdict is the cheapest
    possible way to earn the right to act on it later."""
    criteria = {call.label: what[call.label] for call in offered}
    criteria[ESCALATE] = _escalate_criterion()
    return {
        "next_verb": {
            "type": "choice",
            "instructions": (
                "Which single action should the robot take right now to make progress on "
                "`goal`? Read `now` for what the robot reports about itself, `last` for what "
                "the previous action returned, and `tried` for how often each action has "
                "already been used on this task."
            ),
            "criteria": criteria,
        },
        "done": {
            "type": "noul",
            "instructions": (
                "Everything listed under `success_when` has already happened, according to "
                "`now`, `last` and `recent`. Something merely planned or in progress is not "
                "done."
            ),
        },
        "need_human": {
            "type": "noul",
            "instructions": (
                "A person has to decide before this robot does anything else: the readings "
                "contradict each other, something is stuck or jammed, or the obvious next "
                "step could damage the body or what it is holding."
            ),
        },
        "feasible": {
            "type": "choice",
            "instructions": (
                "Can this body, as `body` describes it, do `goal` at all? Judge the body "
                "against the task, not how far along it is."
            ),
            "criteria": {
                "feasible": "This body can do it with the actions it has.",
                "infeasible": "This body cannot do it however well it is driven.",
                "uncertain": "It depends on something the body does not report.",
            },
        },
    }


# ── the stepper ─────────────────────────────────────────────────────────────────────────


def _read(question: Any, name: str) -> Any:
    """One field of a question, whether it is a dict or somebody's object.

    quackd builds dicts, and a plugin is free to hand its backend whatever it likes; this is
    the same tolerance `_answer` applies to what comes back, applied to what goes out."""
    if isinstance(question, Mapping):
        return question.get(name)
    return getattr(question, name, None)


def _question_chars(questions: Mapping[str, Any]) -> int:
    """How much text the four questions are, for the turns a server does not count for us.

    Measured off the questions themselves rather than rebuilt from the criteria, so a question
    somebody adds or rewords is counted without anybody remembering there was a second place.

    On `lerobot:mock` under `arm-grip-check` this is 1,299 characters before the pilot's
    verdict clears and 1,454 after, against a few hundred of state, which is why the state on
    its own was never the size of the request. It moves with the allowlist because the criteria
    are one line per verb on offer, so it is a figure to re-measure rather than to quote.

    This half is the plain text and the state half is a JSON dump of itself, so the two are not
    counted to the same convention and their sum is an estimate rather than a measurement of
    the bytes on the wire. That is what `usage_estimated` on the record is for."""
    total = 0
    for question in questions.values():
        total += len(str(_read(question, "instructions") or ""))
        criteria = _read(question, "criteria")
        if isinstance(criteria, Mapping):
            total += sum(len(str(k)) + len(str(v)) for k, v in criteria.items())
        elif isinstance(criteria, list | tuple):
            total += sum(len(str(c)) for c in criteria)
    if questions and not total:
        # A backend that keeps its text under other names would otherwise report a request
        # that did go out as having cost nothing, and a zero here does not merely under-bill the
        # turn: `advise` reads it as "nothing was sent" and skips the bill entirely. A rough
        # length off whatever can be read is the wrong number in the right direction.
        total = sum(len(repr(question)) for question in questions.values())
    return total


def _usage(result: Any) -> tuple[int | None, int]:
    """`(input_tokens, output_tokens)` off whatever answered, read tolerantly.

    `None` for the input is TypeSafe's own documented possibility -- their SDK types both
    counts `int | None`, "when the API did not report it" -- and it is what a backend that
    counts nothing at all reports too. Either way it is the difference between a cost quackd
    measured and one it estimated. Read with `getattr` and a mapping fallback in the style of
    `_answer`, because a backend that renamed a field should cost the run an estimate rather
    than a traceback through the loop."""
    usage = getattr(result, "usage", None)
    if usage is None and isinstance(result, Mapping):
        usage = result.get("usage")

    def read(*names: str) -> int | None:
        for name in names:
            value = getattr(usage, name, None)
            if value is None and isinstance(usage, Mapping):
                value = usage.get(name)
            if value is None:
                continue
            try:
                number = int(value)
            except (TypeError, ValueError):
                continue
            if number <= 0:
                # Not a measurement: a request always has input, so a zero or a negative is a
                # field that was never filled in. Falling through to the estimate is the
                # direction this module errs in everywhere else, and it keeps a glitched turn
                # from cancelling out the real tokens of the turns around it.
                continue
            return number
        return None

    return read("input_tokens", "prompt_tokens"), read("output_tokens", "completion_tokens") or 0


def _answer(result: Any, key: str) -> Any:
    """One question's answer, however this backend hands them back.

    Tolerant on purpose, and load-bearing rather than defensive. An SDK returns an object with
    an `answers` attribute; a server's JSON and an in-process model both return a plain
    mapping with an `"answers"` key; the format is young and its own docs have spelled the
    container two ways. Every one of those must cost a run one escalation rather than ending
    it with a traceback while an arm is energised."""
    answers = getattr(result, "answers", None)
    if answers is None and isinstance(result, Mapping):
        answers = result.get("answers")
    for holder in (answers, result):
        if holder is None:
            continue
        with_key = None
        if isinstance(holder, Mapping):
            with_key = holder.get(key)
        elif hasattr(holder, key):
            with_key = getattr(holder, key)
        if with_key is not None:
            return with_key
    return None


def _field(answer: Any, name: str, default: Any = None) -> Any:
    if answer is None:
        return default
    if isinstance(answer, Mapping):
        return answer.get(name, default)
    return getattr(answer, name, default)


@dataclass
class Stepper:
    """The turns that are a choice, on this body, under this contract.

    Built once per run, after the allowlist is final, because its whole vocabulary is the
    allowlist's discrete calls and nothing else. It is never offered a verb the executor would
    refuse this turn, so a stepper-authored call cannot meet the verdict gate or the allowlist:
    those refusals are unreachable rather than caught.
    """

    mode: DecisionMode
    goal: str
    success: Sequence[str] = ()
    body: str = ""
    max_steps: int = 0
    """The run's own step budget, or 0 where nobody said. Read only by `streak_limit`, which
    is why a bare instance built for a shadow record can leave it alone."""
    llm: DecisionLLM | None = None
    """What answers. `None` only where nothing will be asked: a bare instance built to render
    a shadow record, and the `off` runs that never build one at all."""
    name: str = ""
    """The preset that answered, for the record: `jev`, `kev`, a plugin's own name."""
    model: str = ""
    url: str | None = None
    """Where it was reached, for the record. Already redacted: `build` is the only thing that
    sets it, and it redacts. A password or a credential-shaped query parameter in a
    `--decision-url` is `***` by the time anything here can write it down."""

    calls: dict[str, Call] = field(default_factory=dict)
    classes: dict[str, str] = field(default_factory=dict)
    what: dict[str, str] = field(default_factory=dict)
    """Label to the verb's own one-line description: what each option is said to mean."""
    early: set[str] = field(default_factory=set)
    """Labels whose verb may run before the pilot has judged the task."""
    shadow: set[str] = field(default_factory=set)
    """Labels whose verb is `SHADOW_ONLY`: offered, compared, never taken."""
    tried: Counter[str] = field(default_factory=Counter)
    last_call: str | None = None
    """The label it authored on the previous turn, or None when the model took that turn."""
    in_a_row: int = 0
    asked: int = 0
    taken: int = 0
    errors: int = 0
    latency_s: float = 0.0
    price: Price = SELF_HOSTED
    """What a question costs: the preset's published rate, `QUACKD_DECISION_PRICE`, or the
    self-hosted `$0` of a server you run. Always resolved by `factory`; the default here is
    only so a bare instance constructs."""
    input_tokens: int = 0
    output_tokens: int = 0
    """What the whole run asked of it. Output is counted and, at every rate quackd ships,
    not charged; it is here so a reader can see the shape of the exchange."""
    cost_usd: float = 0.0
    estimated: bool = False
    """True once any turn had to estimate its own size, so the run total says `~$` rather than
    claiming a precision nothing ever gave it."""

    @classmethod
    def build(
        cls,
        *,
        mode: DecisionMode,
        llm: DecisionLLM,
        price: Price,
        registry: VerbRegistry,
        allow: Sequence[str],
        goal: str,
        success: Sequence[str] = (),
        body: str = "",
        gated: Sequence[str] = (),
        max_steps: int = 0,
    ) -> Stepper:
        stepper = cls(
            mode=mode,
            goal=goal,
            success=list(success),
            body=body,
            llm=llm,
            name=llm.name,
            model=llm.model,
            # Redacted here, once, rather than at each of the two places that write it out.
            # A `--decision-url` can carry a password or a credential-shaped query parameter,
            # and it also arrives from `QUACKD_DECISION_URL`, which argv redaction never sees;
            # holding the safe spelling is what makes every reader of this object safe by
            # default rather than by remembering.
            url=redacted_url(raw_url) if (raw_url := getattr(llm, "url", None)) else None,
            price=price,
            max_steps=max_steps,
        )
        # The contract's own confirm list, in canonical names, so a verb the task file gated
        # answers to the same floor as one the manifest did.
        confirm = frozenset(registry.canonical(c) for c in gated)
        for name in allow:
            verb = registry.view(name)
            canonical = registry.canonical(name)
            kind = verb_class(verb, canonical, confirm)
            if kind == "never":
                continue  # a dangerous verb is never offered, so no confidence can reach it
            found = discrete_calls(verb.tool_schema())
            if found is None:
                continue
            # the same two conditions the verdict gate itself uses, so what the stepper may
            # reach for before a verdict is exactly what the executor would let through
            runs_early = (canonical in BEFORE_VERDICT and verb.kind != "learned") or (
                verb.read_only and canonical not in MOVES_THE_BODY
            )
            described = _one_line(verb.description, 200)
            for call in found:
                stepper.calls[call.label] = call
                stepper.classes[call.label] = kind
                stepper.what[call.label] = described
                if runs_early:
                    stepper.early.add(call.label)
                if canonical in SHADOW_ONLY:
                    stepper.shadow.add(call.label)
        return stepper

    # ── what is on offer this turn ──

    @property
    def streak_limit(self) -> int:
        """How many turns in a row this stepper may answer on *this* run.

        `MAX_IN_A_ROW` is an absolute number and a budget is not, so on a short duck the
        absolute one never binds: five steps against a limit of eight is a stepper that can
        own the entire run. Half the budget is the floor under that, and it is halves rather
        than some tuned fraction because the property worth keeping is simple enough to say
        out loud -- on a budget of two steps or more, a stepper cannot answer the whole run.

        One step is the exception, and the floor of 1 below is what makes it one: a budget of
        1 gives a limit of 1, the counter starts at 0, and 0 is under the limit, so the single
        turn of that run can be the stepper's with the model never consulted. A one-turn run
        is not a run this gate was written for, and `max_steps=1` is reachable from the flag,
        so it is said here rather than left to be discovered.

        Not parity either, and it would be easy to read it that way. A streak ends by handing
        exactly one turn to the model, after which the count starts again, so the shape is a
        run of stepper turns and then a model turn rather than one each."""
        if self.max_steps <= 0:
            return MAX_IN_A_ROW
        return max(1, min(MAX_IN_A_ROW, self.max_steps // 2))

    def labels_for(self, *, cleared: bool) -> list[Call]:
        """The calls the executor would actually run right now.

        Before the pilot has recorded a feasibility verdict that is the reading verbs and the
        brake, which is exactly what the hero run reached for unprompted on its first turn."""
        return [call for label, call in self.calls.items() if cleared or label in self.early]

    def summary(self) -> dict[str, Any]:
        """The `decision` block of `summary.json`, written only when a stepper actually ran."""
        return {
            "mode": self.mode,
            # Which decision LLM, not just which model id: `jev-1.13.0` names itself, but
            # `kev-latest` on one machine and on another are two different servers, and a
            # transcript that recorded only the id could not tell a reader which answered.
            "llm": self.name,
            "model": self.model,
            "url": self.url,
            "asked": self.asked,
            "taken": self.taken,
            "errors": self.errors,
            "latency_s": round(self.latency_s, 3),
            # What the stepper asked for and what that cost, kept apart from the model's own
            # `usage` and `cost_usd` at the top of the summary rather than folded into them:
            # they are two different things at rates that can be three orders of magnitude
            # apart, and the whole question `--decision-mode shadow` exists to answer is the
            # ratio between them.
            "usage": {
                "input_tokens": self.input_tokens,
                "output_tokens": self.output_tokens,
            },
            "cost_usd": round(self.cost_usd, 6),
            "cost_estimated": self.estimated,
            "price": self.price.record(),
        }

    def _bill(self, result: Any, state_chars: int, question_chars: int) -> dict[str, Any]:
        """What this turn asked for and what it cost, measured wherever the backend said so.

        TypeSafe's SDK types both token counts `int | None`, "when the API did not report it";
        several of the self-hosted rows report a character heuristic rather than a count; and a
        call that raised reports nothing. Those fall back to the arithmetic
        `docs/guides/decision-llms/README.md` does by hand -- the state plus the questions, four
        characters to the token -- and say which they are, because an estimate a reader cannot tell
        from a measurement is worse than no number at all. Laya is the one backend whose count is a
        real tokeniser figure, so its turns go through measured.

        Output is counted and, at every rate quackd ships, costs nothing: TypeSafe charge per
        input token and do not charge for output, and a server you run charges for neither. So
        the rate's output is 0 and this multiplies it out rather than special-casing it, which
        is what lets `QUACKD_DECISION_PRICE` say otherwise for somebody whose contract differs.
        """
        measured, out_tokens = _usage(result) if result is not None else (None, 0)
        estimated = measured is None
        in_tokens = (state_chars + question_chars) // 4 if measured is None else measured
        self.input_tokens += in_tokens
        self.output_tokens += out_tokens
        self.estimated = self.estimated or estimated
        cost = cost_usd({"input_tokens": in_tokens, "output_tokens": out_tokens}, self.price)
        self.cost_usd += cost
        return {
            "usage": {"input_tokens": in_tokens, "output_tokens": out_tokens},
            "usage_estimated": estimated,
            "cost_usd": cost,
        }

    # ── one turn ──

    def _build_state(
        self,
        obs: Observation,
        *,
        budget: str,
        stepped: Sequence[str],
        notes: str | None,
    ) -> tuple[dict[str, str], list[str]]:
        """A named JSON object, in English, carrying only what the questions need.

        Never a picture: these models read text, and quackd would rather escalate a turn that
        needs eyes than pretend otherwise. Never the system prompt either, because the
        instructions belong in the questions, and never the history, which is the distractor
        TypeSafe warn about by name."""
        from quackd.perception.base import Detection, summarize_detections
        from quackd.transport.base import DuckState

        features = obs.features or {}
        dump = features.get("state") or {}
        now = DuckState(**dump).summary() if dump else "not reported"
        detections = [Detection(**d) for d in features.get("detections") or []]
        last = features.get("last_result")
        raw = {
            # Whole, never shortened. Every other field here is a summary of something the
            # robot can be asked again for, but a goal cut off at a comma is a different
            # goal: "wave, but do not raise the shoulder" truncates into its own opposite.
            # If that alone will not fit, the turn goes to the model rather than going out
            # half-said.
            "goal": " ".join(self.goal.split()),
            "success_when": " ".join("; ".join(self.success).split()),
            "body": _one_line(self.body, 300),
            "where": _one_line(budget),
            "now": _one_line(now, 400),
            "camera": _one_line(summarize_detections(detections)),
            "last": (
                _one_line(
                    f"{last.get('verb')}: {'ok' if last.get('ok') else 'FAILED'} — "
                    f"{last.get('summary') or ''}"
                )
                if last
                else "nothing has run yet"
            ),
            "recent": _one_line(" | ".join(list(stepped)[-4:]), 600),
            "tried": ", ".join(f"{name} {n}" for name, n in sorted(self.tried.items())),
            "notes": _one_line(notes or "", 400),
            "flock": _one_line(json.dumps(features["flock"])) if features.get("flock") else "",
        }
        return _trimmed_state(raw)

    async def advise(
        self,
        obs: Observation,
        *,
        cleared: bool,
        budget: str,
        stepped: Sequence[str] = (),
        notes: str | None = None,
    ) -> Advice:
        """Ask this turn's questions, and say whether the answer is good enough to act on."""
        offered = self.labels_for(cleared=cleared)
        record: dict[str, Any] = {
            "mode": self.mode,
            "llm": self.name,
            "model": self.model,
            "labels": labels(offered),
        }
        if not offered:
            # Nothing on this body is a choice right now, so there is no question to ask and
            # nothing to pay for. A duck whose whole allowlist is continuous never reaches the
            # network at all.
            return Advice(None, {**record, "gate": "not_offered", "latency_s": 0.0})

        state, dropped = self._build_state(obs, budget=budget, stepped=stepped, notes=notes)
        record["state_chars"] = len(json.dumps(state))
        record["state_tokens_est"] = record["state_chars"] // 4
        record["trimmed"] = dropped
        if not _fits(state):
            return Advice(None, {**record, "gate": "state_too_large", "latency_s": 0.0})

        started = time.perf_counter()
        # Characters of questions that actually went out, and 0 while nothing has: a turn that
        # could not build its questions owes nobody anything, and one that timed out after the
        # request left probably does. The client itself was built before the robot connected,
        # so nothing in here can fail without the request being real.
        sent = 0
        try:
            questions = build_questions(offered, self.what)
            sent = _question_chars(questions)
            assert self.llm is not None  # `build` always sets it; `advise` is never reached without
            # Bounded here rather than in each backend, because "quicker than the model" is a
            # promise quackd makes and only quackd can keep: a hosted client has its own
            # timeout with its own meaning, a model in this process has none, and a plugin has
            # whatever its author thought of. A turn that runs out escalates like any other
            # failure, and the record says which.
            result = await asyncio.wait_for(self.llm.decide(state, questions), TIMEOUT_S)
        except Exception as e:
            self.errors += 1
            self.asked += 1
            # A bare `TimeoutError` stringifies to nothing, and `error:` with nothing after it
            # tells a reader less than the gate already did. The one thing they want to know
            # is how long it waited, because that is the number they would change.
            if isinstance(e, TimeoutError) and not str(e):
                e = TimeoutError(f"no answer within {TIMEOUT_S:.1f} s")
            took = round(time.perf_counter() - started, 3)
            self.latency_s += took
            failed = {
                **record,
                "gate": "error",
                "error": f"{type(e).__name__}: {e}",
                "latency_s": took,
            }
            if sent:
                # The questions were built and handed over, so the turn is counted as billed.
                # Nobody publishes whether a call that failed on their side is charged, and
                # between an estimate that is slightly high and a bill that silently is not
                # there, the high one is the one nobody is hurt by.
                failed.update(self._bill(None, record["state_chars"], sent))
            return Advice(None, failed)
        self.asked += 1
        took = round(time.perf_counter() - started, 3)
        self.latency_s += took
        record["latency_s"] = took
        record["questions"] = len(questions)
        try:
            record.update(self._bill(result, record["state_chars"], sent))
        except Exception:
            # Reading the backend's own count was the one read of its answer outside a guard,
            # so a usage field that raised anything the tolerant reader does not catch ended
            # the run with a traceback while the arm was energised. Falling back to the estimate
            # charges the turn rather than losing it, and the guard below then gets its chance
            # to turn a bad answer into a handover instead of a crash.
            record.update(self._bill(None, record["state_chars"], sent))
        try:
            return self._route(result, record, offered)
        except Exception as e:
            # Reading the answer is part of the call, so it fails the way the call does. A
            # backend that returns a confidence of "high" or a list where a mapping belongs
            # would otherwise raise through the loop and end the run with a traceback while
            # the arm is energised, which is the one thing this must never do.
            self.errors += 1
            return self._hand_back({**record, "gate": "error", "error": f"{type(e).__name__}: {e}"})

    def _route(self, result: Any, record: dict[str, Any], offered: Sequence[Call]) -> Advice:
        """What one fan-out means, in the order the gates have to be read.

        The Nouls come first, so a stepper that thinks the job is finished never moves
        anything else. Either of them hands the turn back: one ends a run and the other asks a
        person, and the stepper is allowed to do neither."""
        verb = _answer(result, "next_verb")
        choice = str(_field(verb, "choice", ESCALATE))
        confidence, finite = _finite(_field(verb, "confidence"))
        spread = {str(k): float(v) for k, v in (_field(verb, "probabilities") or {}).items()}
        # No default. A question a backend did not answer has to arrive here as nothing, so
        # that the guard below can tell it from an answer of zero.
        done, done_ok = _finite(_field(_answer(result, "done"), "noul"))
        human, human_ok = _finite(_field(_answer(result, "need_human"), "noul"))
        feasible = _answer(result, "feasible")
        record.update(
            choice=choice,
            confidence=confidence,
            probabilities=spread,
            # `null` rather than `0.0` where nothing was answered, because a reader of this
            # record should be able to tell a backend that said "not done" from one that was
            # never asked or never replied.
            done=done if done_ok else None,
            need_human=human if human_ok else None,
            feasible={
                "choice": _field(feasible, "choice"),
                "confidence": _field(feasible, "confidence"),
            },
        )
        # An answer that cannot be read is not a permissive answer. NaN is no confidence
        # rather than a low one and loses every comparison it is put through, so an unguarded
        # one would clear the motion floor, the done gate and the need_human gate at once and
        # move the body. A missing Noul is the same failure wearing different clothes: read as
        # zero it says "certainly not done, certainly no person needed", which is exactly the
        # pair of answers that lets a verb through. A backend that answered only `next_verb`
        # -- a plugin under construction, a server that dropped a field, a model that does not
        # implement Nouls at all -- must hand the turn to the model rather than move the body
        # on two questions nobody answered.
        if not (finite and done_ok and human_ok):
            return self._hand_back({**record, "gate": "unreadable"})
        if done >= DONE_THRESHOLD:
            return self._hand_back({**record, "gate": "done"})
        if human >= HUMAN_THRESHOLD:
            return self._hand_back({**record, "gate": "need_human"})
        # Against what was offered *this turn*, not against everything this body can do. The
        # difference is the whole of the verdict gate: before a verdict, `gripper` is not on
        # the list, and a stepper that answered `gripper` anyway would have the executor
        # refuse it and waste the turn. Checked here, so that refusal is unreachable rather
        # than merely unlikely.
        on_offer = {call.label for call in offered}
        if choice == ESCALATE or choice not in on_offer:
            return self._hand_back({**record, "gate": "escalate"})
        # A reflex that fires twice identically is not deciding, it is looping. quackd already
        # reads repetition as the signature of a stuck pilot (`abort_when: Same verb fails 3
        # times in a row`), and these calls succeed, so nothing else here would catch it.
        if choice == self.last_call:
            return self._hand_back({**record, "gate": "repeat"})
        if self.in_a_row >= self.streak_limit:
            return self._hand_back({**record, "gate": "handover"})
        kind = self.classes[choice]
        floor = FLOORS[kind]
        record.update({"class": kind, "floor": floor})
        if confidence < floor:
            return self._hand_back({**record, "gate": "below_floor"})
        if choice in self.shadow:
            # last, so the record says this answer cleared every gate a taken one clears, which
            # is the number that would ever promote it
            return self._hand_back({**record, "gate": SHADOW_GATE})
        call = self.calls[choice]
        self.taken += 1
        self.tried[call.name] += 1
        self.last_call = choice
        self.in_a_row += 1
        return Advice(
            ToolCall(id=f"decision-{self.asked}", name=call.name, arguments=dict(call.arguments)),
            {
                **record,
                "gate": "taken",
                "call": {"name": call.name, "arguments": dict(call.arguments)},
            },
        )

    def _hand_back(self, record: dict[str, Any]) -> Advice:
        """The model takes this turn, so the run of stepper turns is over and both counters
        start again from the next one."""
        self.last_call = None
        self.in_a_row = 0
        return Advice(None, record)

    def compares(self, advice: Advice) -> bool:
        """Whether the model's answer to this turn is recorded beside the stepper's in
        `--decision-mode on` as it is in shadow: a turn that offered a `SHADOW_ONLY` call,
        which is every between-segment choice, whatever the stepper answered. Only those, so
        the agreement they measure is on the choices it is never let make, and not only on the
        ones it happened to answer confidently."""
        return any(label in self.shadow for label in advice.record.get("labels") or ())

    def shadow_event(
        self, advice: Advice, call: ToolCall, llm: Mapping[str, Any] | None = None
    ) -> dict[str, Any]:
        """What the stepper would have done, beside what the model did, on the same reading.

        The only record shadow mode leaves, and what turns the arithmetic in
        `docs/guides/decision-llms/README.md` into a measurement."""
        chosen = advice.record.get("choice")
        return {
            "decision_choice": chosen,
            "decision_confidence": advice.record.get("confidence"),
            "decision_gate": advice.gate,
            "decision_latency_s": advice.record.get("latency_s"),
            "model_verb": call.name,
            "model_arguments": dict(call.arguments),
            # The whole call, not the verb's name. Every discrete verb worth comparing is one
            # whose arguments are the decision: `gripper(open=true)` and `gripper(open=false)`
            # are opposite instructions that share a word, and scoring them as agreement would
            # corrupt the one measurement that earns the right to move a floor.
            "agree": bool(chosen) and chosen == _label(call.name, call.arguments),
            "same_verb": bool(chosen) and str(chosen).split("(")[0] == call.name,
            "would_have_acted": advice.gate == "taken",
            "llm_latency_s": (llm or {}).get("latency_s"),
            "llm_usage": (llm or {}).get("usage"),
            # The two bills for the same turn, which is the number this whole mode exists to
            # produce and the one `docs/guides/decision-llms/README.md` could only reach by
            # arithmetic. Either can be None: the model's when nobody publishes a rate for it, the
            # stepper's when the turn never got as far as asking.
            "llm_cost_usd": (llm or {}).get("cost_usd"),
            "decision_cost_usd": advice.record.get("cost_usd"),
        }
