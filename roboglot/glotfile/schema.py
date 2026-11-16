"""The machine-enforced half of a `.glot` file.

Everything in the YAML frontmatter is validated here, strictly (unknown keys are errors),
because the executor trusts this model and nothing else. The Markdown body is free text
for the LLM and is deliberately not modelled beyond "it is a string".
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SerializerFunctionWrapHandler,
    field_validator,
    model_serializer,
    model_validator,
)

from roboglot.adapters.manifest import (
    Confidence,
    Manipulator,
    Terrain,
    datasheet_sentences,
    without_unsaid_back_load,
)
from roboglot.verbs.aliases import canonical

GLOT_SPEC_VERSION = 4

_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_ROBOT_SPEC_RE = re.compile(r"^[a-z][a-z0-9_]*(:[a-z][a-z0-9_]*)?$")

# How big a team may be. Each member is its own body on its own clock, so the bound is what a
# person can read in one terminal and what N concurrent pilots cost in tokens (ADR-0034).
PILOTS_MAX_MEMBERS = 8

# The two `abort_when` phrasings the executor enforces itself. Anything else in the list is
# passed to the LLM as an instruction, which is honest about what is and is not policed.
BATTERY_ABORT_RE = re.compile(r"battery\s+(?:below|under|<)\s*(\d+(?:\.\d+)?)\s*%", re.I)
REPEAT_FAIL_ABORT_RE = re.compile(r"same\s+verb\s+fails\s+(\d+)\s+times?\s+in\s+a\s+row", re.I)

# What a v3 `policy:` section may say, and what a run that has none gets. The verb it governs is
# the one that hands a body to its learned policy for a segment (`roboglot_lerobot.verbs`), named
# here rather than imported because this module parses files and imports no body.
POLICY_VERB = "manipulate"
POLICY_VERBS = ("pick", POLICY_VERB)
"""Every verb that hands a body to its learned policy for a segment, and tells the policy what
to do in words: `pick` its target, `manipulate` an instruction. The executor charges the
seconds of each against the section's `total_s`, and a section that lists instructions refuses
`pick` beside it, since the target `pick` would tell the policy is words the list never held."""
MAX_INSTRUCTIONS = 12
"""The most instructions a task may list. The stepper's `MAX_CALLS_PER_VERB`, so that every
listed instruction is one choice a decision LLM can be offered; spelled here rather than
imported because the stepper imports half the agent, and a test holds the two together."""
INSTRUCTION_MAX_CHARS = 200
"""How long one instruction may be. A policy is told one short subtask in the words it was
trained on, which is a line and not a paragraph."""
DEFAULT_SEGMENT_S = 10.0
"""How long one `manipulate` segment runs, in the body's time, when the task says nothing: a
goal run, a v2 file, an MCP session with no task loaded. Long enough for a learned policy to do
one short subtask, short enough that the pilot looks at the arm again before the next."""
SEGMENT_MAX_S = 60.0
"""The longest segment a task may ask for. A minute of a learned policy driving the arm with
nobody asked is already long, and every segment ends `SEGMENT_HEADROOM_S` inside the executor's
timeout for `manipulate` before any task narrows it, which a test holds against the arm's own
(`roboglot_lerobot.verbs.MANIPULATE_TIMEOUT_S`), as `MOVE_MAX_S` is held inside the timeout of
`move_joints`."""
SEGMENT_HEADROOM_S = 10.0
"""How far past a segment's own seconds the executor's timeout for `manipulate` lies. The
segment's time starts once the policy is reset and the step cap written, and a guard's hold and
the verb's stop follow its end; all of that has to land before the executor's clock does."""
DEFAULT_POLICY_TOTAL_S = 120.0
"""How many seconds of segments a run may spend in all when the task says nothing: twelve
default segments, as many as a task may list instructions."""
POLICY_TOTAL_MAX_S = 3600.0
"""The most seconds of segments a task may allow in all: an hour of a learned policy driving
the arm, which is past what a person watching a run would stand beside it for."""


def instruction_line(value: str, what: str = "instruction") -> str:
    """`value` as one instruction a learned policy may be told, stripped, or a ValueError that
    says why it is not one: something said, one line of plain text, and at most
    `INSTRUCTION_MAX_CHARS`. A task file's list is held to it, and so are the words a pilot gives
    `manipulate` itself when the list is empty (`roboglot_lerobot.verbs.ManipulateParams`) and the
    target it gives `pick` (`PickParams`, whose `what` is `target`), so a policy is never told a
    paragraph or a control character any way."""
    line = value.strip()
    if not line:
        raise ValueError(f"each {what} must say something: remove the blank one")
    if not line.isprintable():
        raise ValueError(f"{what} {line[:40]!r} must be one line of plain text")
    if len(line) > INSTRUCTION_MAX_CHARS:
        raise ValueError(
            f"{what} {line[:40]!r}... is {len(line)} characters, and one is at most "
            f"{INSTRUCTION_MAX_CHARS}: a policy is told one short subtask"
        )
    return line


def _verb_list(names: list[str]) -> list[str]:
    """Valid, unique verb names, where a verb and its alias count as one verb."""
    seen: set[str] = set()
    by_canonical: dict[str, str] = {}
    for name in names:
        if not _NAME_RE.match(name.replace("_", "-")):
            raise ValueError(f"{name!r} is not a valid verb name")
        if name in seen:
            raise ValueError(f"duplicate verb {name!r}")
        seen.add(name)
        other = by_canonical.setdefault(canonical(name), name)
        if other != name:
            raise ValueError(f"{other!r} and {name!r} are the same verb; list one of them")
    return names


class VerbsSection(BaseModel):
    """Which verbs the LLM may call, and which need a human to say yes first."""

    model_config = ConfigDict(extra="forbid")

    allow: list[str] = Field(
        default=..., min_length=1, description="Verbs the LLM may call. Anything else is refused."
    )
    confirm: list[str] = Field(
        default_factory=list,
        description="Verbs (subset of `allow`) that prompt a human y/N before executing.",
    )

    @field_validator("allow", "confirm")
    @classmethod
    def _unique_names(cls, names: list[str]) -> list[str]:
        return _verb_list(names)

    @model_validator(mode="after")
    def _confirm_subset_of_allow(self) -> VerbsSection:
        allowed = {canonical(v) for v in self.allow}
        extra = [v for v in self.confirm if canonical(v) not in allowed]
        if extra:
            raise ValueError(f"confirm lists verbs that are not allowed: {extra}")
        if "stop" in self.confirm:
            raise ValueError("stop can never be confirm-gated; it is the kill switch's verb")
        return self


class Budgets(BaseModel):
    """Hard stops. The loop ends when any of these is hit, whatever the LLM thinks."""

    model_config = ConfigDict(extra="forbid")

    max_steps: int = Field(
        default=40, ge=1, le=1000, description="Maximum number of LLM decisions."
    )
    max_minutes: float = Field(default=5.0, gt=0, le=180, description="Wall-clock cap.")
    max_llm_calls: int = Field(
        default=40,
        ge=1,
        le=2000,
        description=(
            "Maximum provider calls. A turn the LLM's API could not answer, and Roboglot asked "
            "again, counts once."
        ),
    )


class FigureOverride(BaseModel):
    """A number the task file asserts about the build in front of it.

    The source defaults to the file itself and the confidence to `estimate`; an author who
    weighed the thing says `measured`."""

    model_config = ConfigDict(extra="forbid")

    value: float = Field(..., ge=0)
    confidence: Confidence = "estimate"
    source: str = Field(default="", description="Optional: 'weighed with the printed gripper'.")
    note: str = ""


class SpanOverride(BaseModel):
    """A band the task file asserts, the same way."""

    model_config = ConfigDict(extra="forbid")

    low: float = Field(..., ge=0)
    high: float = Field(..., ge=0)
    confidence: Confidence = "estimate"
    source: str = ""
    note: str = ""


def _figure_shorthand(value: Any) -> Any:
    """`payload_kg: 0.3` is `payload_kg: {value: 0.3}`."""
    if isinstance(value, bool):
        return value
    return {"value": value} if isinstance(value, int | float) else value


class DatasheetOverride(BaseModel):
    """v2: what a `.glot` says about the body it was written for (`docs/reference/glot-spec.md`).

    Merged field-wise into the robot's own datasheet by `apply_datasheet_override`: a figure
    given here replaces the adapter's and is rendered as coming from the task file; `cannot`,
    `notes` and `not_rated` extend the adapter's; whatever is omitted is left alone. A task
    file can add a `cannot`; it can never delete one.
    """

    model_config = ConfigDict(extra="forbid")

    mass_kg: FigureOverride | None = None
    height_m: FigureOverride | None = None
    dof: FigureOverride | None = None
    payload_kg: FigureOverride | None = None
    carries_kg: FigureOverride | None = None
    reach_m: FigureOverride | None = None
    workspace_height_m: SpanOverride | None = None
    endurance_min: FigureOverride | None = None
    manipulator: Manipulator | None = None
    arms: int | None = Field(default=None, ge=0, le=4)
    tethered: bool | None = None
    terrain: Terrain | None = None
    not_rated: list[str] = Field(default_factory=list)
    cannot: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)

    @field_validator(
        "mass_kg",
        "height_m",
        "dof",
        "payload_kg",
        "carries_kg",
        "reach_m",
        "endurance_min",
        mode="before",
    )
    @classmethod
    def _numbers(cls, value: Any) -> Any:
        return _figure_shorthand(value)

    @field_validator("cannot", "notes", "not_rated")
    @classmethod
    def _prose(cls, values: list[str]) -> list[str]:
        return datasheet_sentences(values)

    @model_serializer(mode="wrap")
    def _leave_out_what_nobody_said(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        """A task file with no `carries_kg` dumps no such key (`without_unsaid_back_load`)."""
        return without_unsaid_back_load(handler(self))


class PolicySection(BaseModel):
    """v3: what the body's learned policy may be told through `manipulate`, and for how long.

    A `.glot` is untrusted input, so every number here is bounded and every instruction is a
    short line. The run narrows `manipulate` to it (`roboglot.glotfile.narrow`): the listed
    instructions become the only words the verb takes, and each segment runs `segment_s`. The
    executor refuses the next segment, a `pick` as well as a `manipulate`, once `total_s` of
    them have run. A run whose task has no section, a goal run or a v2 file, gets the defaults
    and takes any instruction."""

    model_config = ConfigDict(extra="forbid")

    instructions: list[str] = Field(
        default_factory=list,
        max_length=MAX_INSTRUCTIONS,
        description=f"The subtasks the policy may be told, word for word, at most "
        f"{MAX_INSTRUCTIONS}, each one line of at most {INSTRUCTION_MAX_CHARS} characters. "
        "Empty lets the pilot word each subtask itself. A list refuses pick in verbs.allow, "
        "whose target would be words of the pilot's own.",
    )
    segment_s: float = Field(
        default=DEFAULT_SEGMENT_S,
        gt=0,
        le=SEGMENT_MAX_S,
        allow_inf_nan=False,
        description="How long one manipulate segment runs, in the robot's time, unless "
        "something ends it sooner.",
    )
    total_s: float = Field(
        default=DEFAULT_POLICY_TOTAL_S,
        gt=0,
        le=POLICY_TOTAL_MAX_S,
        allow_inf_nan=False,
        description="How many seconds of segments the run may spend in all, pick's as well "
        "as manipulate's. The executor refuses the next one once they are spent.",
    )

    @field_validator("instructions")
    @classmethod
    def _short_lines(cls, values: list[str]) -> list[str]:
        seen: set[str] = set()
        lines: list[str] = []
        for value in values:
            line = instruction_line(value)
            if line in seen:
                raise ValueError(f"duplicate instruction {line!r}")
            seen.add(line)
            lines.append(line)
        return lines

    @model_validator(mode="after")
    def _total_holds_a_segment(self) -> PolicySection:
        if self.total_s < self.segment_s:
            raise ValueError(
                f"total_s ({self.total_s:g}) is shorter than one segment ({self.segment_s:g}): "
                "give total_s at least segment_s"
            )
        return self


class LearnedVerbRef(BaseModel):
    """Reserved for v2. The shape a `.glot` will use to pull in a learned (ONNX) verb.

    See `docs/concepts/learned-verbs.md`. Parsed and validated today; nothing executes it.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., description="Verb name it registers as.")
    policy: str = Field(..., description="Path or URL of the ONNX policy.")
    description: str = Field(default="", description="LLM-facing description.")
    metadata: dict[str, Any] = Field(default_factory=dict)


class TeamAllocation(BaseModel):
    """Which kind of team this is. One is left: `pilots`, one LLM pilot per body, on wall-clock
    time, dividing the work by talking to each other (ADR-0034). The coordinator team that
    `auction` named is gone."""

    model_config = ConfigDict(extra="forbid")

    method: Literal["pilots"] = "pilots"


RETIRED_TEAM_KEYS = ("safety", "search", "roles", "frame_hints")
RETIRED_ALLOCATION_KEYS = ("bid", "tie_break", "hysteresis_pct", "claim_lease_s")
"""What only the coordinator team read, which a task file written for it may still carry."""
COORDINATOR_GONE = (
    "Roboglot no longer has the coordinator team it was for: a team is pilots, one LLM per "
    "body, and reads team.members alone"
)
"""Why a task file's coordinator setting is refused rather than dropped without a word."""


class TeamSection(BaseModel):
    """Cooperating robots: a team of pilots, one LLM per body, on any backend."""

    model_config = ConfigDict(extra="forbid")

    members: int | list[str] = Field(
        default=3,
        description="Count (named robot-0..) or a list of unique slugs, 2 to 8.",
    )
    allocation: TeamAllocation = Field(default_factory=TeamAllocation)

    @model_validator(mode="before")
    @classmethod
    def _no_coordinator(cls, value: Any) -> Any:
        """Refuse a setting only the coordinator team read, naming it, rather than drop it or
        let `extra="forbid"` say only that it is not permitted."""
        if not isinstance(value, Mapping):
            return value
        for key in RETIRED_TEAM_KEYS:
            if key in value:
                raise ValueError(f"team.{key}: {COORDINATOR_GONE}")
        allocation = value.get("allocation")
        if isinstance(allocation, Mapping):
            for key in RETIRED_ALLOCATION_KEYS:
                if key in allocation:
                    raise ValueError(f"team.allocation.{key}: {COORDINATOR_GONE}")
            if allocation.get("method") == "auction":
                raise ValueError(f"team.allocation.method: auction: {COORDINATOR_GONE}")
        return value

    @field_validator("members")
    @classmethod
    def _members(cls, value: int | list[str]) -> int | list[str]:
        # the wide bound here and the narrow one per method below: `members` is validated
        # before `allocation` is in hand, so the kind-specific cap is a model validator
        if isinstance(value, int):
            if not 2 <= value <= PILOTS_MAX_MEMBERS:
                raise ValueError(f"a team needs 2 to {PILOTS_MAX_MEMBERS} robots")
            return value
        if not 2 <= len(value) <= PILOTS_MAX_MEMBERS:
            raise ValueError(f"a team needs 2 to {PILOTS_MAX_MEMBERS} named robots")
        seen: set[str] = set()
        for name in value:
            if not _NAME_RE.match(name):
                raise ValueError(f"{name!r} is not a valid member name (slug)")
            if name in seen:
                raise ValueError(f"duplicate member {name!r}")
            seen.add(name)
        return value

    @property
    def member_names(self) -> list[str]:
        if isinstance(self.members, int):
            return [f"robot-{i}" for i in range(self.members)]
        return list(self.members)


class GlotFrontmatter(BaseModel):
    """The contract. This is what `schema.json` describes and what the executor enforces."""

    model_config = ConfigDict(
        extra="forbid", title="Roboglot .glot frontmatter (v0, v1, v2, v3, v4)"
    )

    glot: Literal[0, 1, 2, 3, 4] = Field(
        ...,
        description="Spec version: 0 (Roboglot 0.1 to 0.3), 1 (0.4: requires, robots, team; "
        "0.9: team.allocation.method: pilots), 2 (0.9: datasheet), 3 (0.16: policy) or 4 "
        "(0.19: play). Older files parse unchanged, but for the coordinator team's settings.",
    )
    name: str = Field(..., description="Slug: lowercase letters, digits, hyphens.")
    description: str = Field(..., min_length=1, description="One line, human-facing.")
    author: str | None = None
    verbs: VerbsSection
    budgets: Budgets = Budgets()
    success: list[str] = Field(
        default=..., min_length=1, description="Success criteria the LLM must judge itself against."
    )
    abort_when: list[str] = Field(
        default_factory=list,
        description=(
            "Abort conditions. 'Battery below N%' and 'Same verb fails N times in a row' are "
            "enforced by the executor; other entries are passed to the LLM as instructions."
        ),
    )
    persona: str | None = Field(default=None, description="Tone for the LLM. Optional.")
    play: bool = Field(
        default=False,
        description="v4: the task may give the pilot a part to play, an animal, a person or a "
        "character, which it acts out with the body it has. Adds one section to the system "
        "prompt and changes no gate. `roboglot run --play` or `--no-play` overrides it for one "
        "run.",
    )
    providers: list[str] = Field(
        default_factory=list, description="Providers this task was tested with (not enforced)."
    )
    learned_verbs: list[LearnedVerbRef] = Field(
        default_factory=list, description="Reserved for v2 learned verbs. Must be empty in v0.1."
    )
    team: TeamSection | None = Field(
        default=None,
        description="Cooperating robots. Absent means a single robot, unless the run names a "
        "stored team, which makes it a pilot team.",
    )
    requires: list[str] = Field(
        default_factory=list,
        description="v1: verbs the task needs. `roboglot validate --robot` checks them against "
        "the robot's manifest. For v0 files every allowed verb is required.",
    )
    datasheet: DatasheetOverride | None = Field(
        default=None,
        description="v2: corrections and additions to the robot's datasheet for this build, "
        "rendered in the prompt as coming from the task file.",
    )
    robots: str | dict[str, str] | None = Field(
        default=None,
        description="v1: default robot as <adapter>[:<backend>], or a mapping from team "
        "member name to such a spec, so `roboglot run <task>` needs no --robot flag.",
    )
    policy: PolicySection | None = Field(
        default=None,
        description="v3: the instructions manipulate may give the robot's learned policy, how "
        "long each segment runs and how long they may run in all. Needs manipulate allowed.",
    )

    @field_validator("name")
    @classmethod
    def _slug(cls, value: str) -> str:
        if not _NAME_RE.match(value):
            raise ValueError("name must match ^[a-z0-9][a-z0-9-]{0,63}$")
        return value

    @field_validator("requires")
    @classmethod
    def _requires_names(cls, names: list[str]) -> list[str]:
        return _verb_list(names)

    @field_validator("robots")
    @classmethod
    def _robot_specs(cls, value: str | dict[str, str] | None) -> str | dict[str, str] | None:
        specs = [value] if isinstance(value, str) else list((value or {}).values())
        for spec in specs:
            if not _ROBOT_SPEC_RE.match(spec):
                raise ValueError(f"{spec!r} is not <adapter>[:<backend>]")
        if isinstance(value, dict):
            for member in value:
                if not _NAME_RE.match(member):
                    raise ValueError(f"{member!r} is not a valid member name (slug)")
        return value

    @model_validator(mode="after")
    def _version_and_cross_field_rules(self) -> GlotFrontmatter:
        if self.glot < 2 and self.datasheet is not None:
            raise ValueError("datasheet needs glot: 2")
        if self.glot < 1 and self.team is not None:
            raise ValueError(
                f"team needs glot: 1, and a glot: 0 team was a coordinator team: {COORDINATOR_GONE}"
            )
        if self.datasheet is not None and self.team is not None:
            raise ValueError("datasheet describes one body; a team task cannot carry one")
        if self.glot < 3 and self.policy is not None:
            raise ValueError("policy needs glot: 3")
        # Read off the value, never off whether the key was written: a pilot team's contract
        # is dumped and validated again for each member (`team.pilots.trim_contract`), and a
        # dumped `play: false` on a `glot: 1` file must keep passing there.
        if self.glot < 4 and self.play:
            raise ValueError("play needs glot: 4")
        if self.policy is not None and self.team is not None:
            raise ValueError(
                "policy hands one arm to its learned policy; a team task cannot carry one"
            )
        if self.policy is not None and POLICY_VERB not in {canonical(v) for v in self.verbs.allow}:
            raise ValueError(
                f"policy governs {POLICY_VERB}, which verbs.allow does not list: allow "
                f"{POLICY_VERB}, or remove the policy section"
            )
        told = [
            v
            for v in self.verbs.allow
            if canonical(v) in POLICY_VERBS and canonical(v) != POLICY_VERB
        ]
        if self.policy is not None and self.policy.instructions and told:
            raise ValueError(
                f"policy.instructions are the only words the robot's learned policy may be "
                f"told, and {told[0]} tells it a target of the pilot's own: remove {told[0]} "
                "from verbs.allow, or leave policy.instructions empty"
            )
        if self.glot == 0:
            v1_keys = {
                "requires": bool(self.requires),
                "robots": self.robots is not None,
            }
            for key, used in v1_keys.items():
                if used:
                    raise ValueError(f"{key} needs glot: 1")
        allowed = {canonical(v) for v in self.verbs.allow}
        extra = [v for v in self.requires if canonical(v) not in allowed]
        if extra:
            raise ValueError(f"requires lists verbs that are not allowed: {extra}")
        return self

    @property
    def effective_requires(self) -> list[str]:
        """What `validate --robot` checks: v1 says it; a v0 task needs everything it allows."""
        return list(self.requires) if self.glot >= 1 else list(self.verbs.allow)

    @property
    def effective_policy(self) -> PolicySection:
        """What the run holds `manipulate` to: the file's `policy:`, or the named defaults for
        a task that has none, a goal run and every v2 file among them."""
        return self.policy if self.policy is not None else PolicySection()

    # ── derived, machine-enforced abort thresholds ──────────────────────────────────

    @property
    def battery_abort_percent(self) -> float | None:
        for line in self.abort_when:
            m = BATTERY_ABORT_RE.search(line)
            if m:
                return float(m.group(1))
        return None

    @property
    def repeat_failure_abort(self) -> int | None:
        for line in self.abort_when:
            m = REPEAT_FAIL_ABORT_RE.search(line)
            if m:
                return int(m.group(1))
        return None

    @property
    def advisory_abort_conditions(self) -> list[str]:
        """The `abort_when` entries we cannot enforce and therefore hand to the LLM."""
        return [
            line
            for line in self.abort_when
            if not BATTERY_ABORT_RE.search(line) and not REPEAT_FAIL_ABORT_RE.search(line)
        ]


class GlotFile(BaseModel):
    """A parsed `.glot`: the enforced contract plus the LLM-facing body."""

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    frontmatter: GlotFrontmatter
    body: str
    path: str | None = None

    @property
    def name(self) -> str:
        return self.frontmatter.name


def json_schema() -> dict[str, Any]:
    """The JSON Schema for the frontmatter, as exported to `schema.json`."""
    schema = GlotFrontmatter.model_json_schema()
    schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://github.com/rokbenko/quackd/blob/main/roboglot/glotfile/schema.json",
        **schema,
    }
    return schema
