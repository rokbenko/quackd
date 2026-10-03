"""What a connected robot is and can do, as data.

A `RobotManifest` is what every adapter returns from `connect()`: pydantic in code, JSON on
the wire (MCP, mDNS, the flock bus), never YAML on disk. It decides *which* verbs exist on
a robot and how they are gated; the adapter and `verbs/core.py` decide *how* they run. A
verb that is not in the manifest does not exist anywhere in quackd (ADR-0017).

A manifest also carries a datasheet: the body as numbers, each with how sure quackd is and
who says so, so a pilot can refuse a task before anything moves (ADR-0032).
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from quackd.verbs.aliases import ALIASES
from quackd.verbs.registry import SafetyClass, Verb

MANIFEST_VERSION = 1

Embodiment = Literal["biped", "quadruped", "wheeled", "arm", "humanoid"]
Mobility = Literal["none", "legged", "wheeled"]
IntentName = Literal["twist", "skill", "gaze", "sound", "joint", "pose", "gripper"]
Sensor = Literal["camera", "battery", "odometry", "imu", "tof", "microphone", "joint_state"]
NativeSafety = Literal["robotd_deadman", "lease", "torque_limit", "estop", "none"]

# The manifest speaks the vocabulary other systems read; the transports speak the intent
# kinds quackd has used since 0.1. One table maps between them.
INTENT_KIND_FOR: dict[str, str] = {
    "twist": "move",
    "skill": "do",
    "gaze": "look",
    "sound": "sound",
    "joint": "joint",
    "pose": "pose",
    "gripper": "gripper",
}

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


class VerbSpec(BaseModel):
    """One verb a robot provides. `name` is canonical (never an alias)."""

    model_config = ConfigDict(extra="forbid")

    name: str
    core: bool = Field(default=False, description="A core verb: the same on every robot.")
    description: str = Field(
        default="", description="LLM-facing text. Empty means the implementation's default."
    )
    params_schema: dict[str, Any] = Field(
        default_factory=dict, description="JSON schema of the parameters (informational)."
    )
    safety_class: SafetyClass = "safe"
    timeout_s: float | None = Field(default=None, gt=0, le=600)

    @field_validator("name")
    @classmethod
    def _slug(cls, value: str) -> str:
        if not _SLUG_RE.match(value):
            raise ValueError(f"{value!r} is not a valid verb name")
        return value


class SafetyAuthority(BaseModel):
    """Who stops the body when quackd goes quiet. Honesty matters more than the enum."""

    model_config = ConfigDict(extra="forbid")

    native: NativeSafety = "none"
    deadman: bool = Field(default=False, description="The robot zeroes motion on silence.")
    heartbeat_hz: float = Field(default=2.0, gt=0, le=50)


class Frame(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reference: Literal["body", "head", "base", "world"] = "body"
    note: str = ""


class Health(BaseModel):
    """The informational liveness call. The watchdog contract stays `heartbeat()`."""

    model_config = ConfigDict(extra="forbid")

    ok: bool = True
    reason: str | None = None
    battery_percent: float | None = None
    extras: dict[str, Any] = Field(default_factory=dict)


Confidence = Literal["official", "estimate", "measured"]
"""official: the maker or a paper says so. estimate: one vendor, a community number or a
reading off a photo. measured: somebody weighed, measured or timed it and said how."""

Manipulator = Literal["none", "beak", "gripper", "arms"]
"""What quackd can command that touches an object: nothing, the Microduck's scripted scoop,
one or more grippers, or bare arms that hold by closing on a thing."""

Terrain = Literal["indoor_flat", "indoor", "outdoor"]

TASK_FILE = "the task file"
"""The source a `.duck` override is rendered under: "0.3 kg (measured: the task file)"."""

_SENTENCE_MAX = 300


def datasheet_sentences(values: list[str]) -> list[str]:
    """Free text the prompt bullets: whitespace-normalised, non-empty, starting with a word.

    A leading backtick would render as a line the prompt spells an offered verb with, and the
    adapter tests read the offered verbs back out of the prompt by that shape. Shared with the
    `.duck` override, so a task file is held to the same rule."""
    out: list[str] = []
    for raw in values:
        text = " ".join(raw.split())
        if not text:
            raise ValueError("an empty sentence")
        if text[0] in "`-*":
            raise ValueError(f"start with a word, not {text[0]!r}: {text!r}")
        if len(text) > _SENTENCE_MAX:
            raise ValueError(f"longer than {_SENTENCE_MAX} characters: {text[:40]!r}...")
        out.append(text)
    return out


class Figure(BaseModel):
    """One physical number, how sure quackd is of it, and who says so.

    A figure without a source is a rumour, so `source` is required. The pilot reads all three:
    "0.5 kg (estimate: one vendor's listing)" is a different instruction from "0.5 kg
    (official: the maker's datasheet)"."""

    model_config = ConfigDict(extra="forbid")

    value: float = Field(..., ge=0)
    confidence: Confidence
    source: str = Field(
        ...,
        min_length=1,
        description="Who says so: 'Pollen Robotics README', 'arXiv:2502.00893', 'the task file'.",
    )
    note: str = Field(default="", description="Read with the number: 'per arm', 'a software cap'.")

    def text(self, unit: str) -> str:
        """0.8 kg (official: Pollen Robotics README; a software cap)."""
        amount = f"{self.value:g} {unit}".rstrip()
        qualifier = f"; {self.note}" if self.note else ""
        return f"{amount} ({self.confidence}: {self.source}{qualifier})"


class Span(BaseModel):
    """A band rather than a point: the heights the hands can work at."""

    model_config = ConfigDict(extra="forbid")

    low: float = Field(..., ge=0)
    high: float = Field(..., ge=0)
    confidence: Confidence
    source: str = Field(..., min_length=1)
    note: str = ""

    @model_validator(mode="after")
    def _ordered(self) -> Span:
        if self.low > self.high:
            raise ValueError(f"low {self.low:g} exceeds high {self.high:g}")
        return self

    def text(self, unit: str) -> str:
        qualifier = f"; {self.note}" if self.note else ""
        return f"{self.low:g} to {self.high:g} {unit} ({self.confidence}: {self.source}{qualifier})"


class Datasheet(BaseModel):
    """The body as numbers, with how sure quackd is of each, and the things it cannot do.

    Speeds are not here: `RobotManifest.limits` is what quackd clamps to, and the prompt
    renders those as clamps. `None` means not published, and the prompt says so in those words
    and tells the pilot to answer `uncertain` where a task turns on it, and the verdict gate
    refuses a `feasible` that names it (refuse by default). The same datasheet describes a
    body on every backend: sim2d, mock or real, the body is the body, which is what keeps
    digests equal across backends.
    """

    model_config = ConfigDict(extra="forbid")

    mass_kg: Figure | None = None
    height_m: Figure | None = None
    dof: Figure | None = Field(default=None, description="Actuated joints, counted.")
    payload_kg: Figure | None = Field(
        default=None,
        description="What one hand, the beak or the whole body can hold; the note says which.",
    )
    reach_m: Figure | None = Field(default=None, description="Arm base to fingertips.")
    workspace_height_m: Span | None = Field(
        default=None, description="The band of heights the hands can work at."
    )
    endurance_min: Figure | None = Field(default=None, description="Minutes on a charge.")
    manipulator: Manipulator = Field(
        ..., description="What quackd can command that touches an object."
    )
    arms: int = Field(default=0, ge=0, le=4)
    tethered: bool | None = Field(
        default=None,
        description="True: mains powered, nothing to run down. False: a battery. None: not "
        "published.",
    )
    terrain: Terrain | None = Field(
        default=None,
        description="What it is rated for. None: not published; the prompt says to assume a flat "
        "indoor floor and decline the rest.",
    )
    not_rated: list[str] = Field(
        default_factory=list, description="What it is not rated for: stairs, steps, slopes."
    )
    cannot: list[str] = Field(
        default_factory=list,
        description="Categorical: what it cannot do whatever the task says, one sentence each.",
    )
    notes: list[str] = Field(
        default_factory=list, description="Worth reading before planning, one sentence each."
    )

    #: (field, prompt label, unit), in the order the prompt lists them. `workspace_height_m` is
    #: deliberately absent: it is an extra where known, never a gap where not.
    FIGURES: ClassVar[tuple[tuple[str, str, str], ...]] = (
        ("mass_kg", "Mass", "kg"),
        ("height_m", "Height", "m"),
        ("dof", "Actuated joints", ""),
        ("payload_kg", "Payload", "kg"),
        ("reach_m", "Reach", "m"),
        ("endurance_min", "Endurance", "min"),
    )

    @field_validator("cannot", "notes", "not_rated")
    @classmethod
    def _prose(cls, values: list[str]) -> list[str]:
        return datasheet_sentences(values)

    @model_validator(mode="after")
    def _consistent(self) -> Datasheet:
        if self.manipulator == "none" and self.arms:
            raise ValueError("arms without a manipulator: say what the hands are (gripper, arms)")
        if self.manipulator in ("gripper", "arms") and not self.arms:
            raise ValueError(f"manipulator {self.manipulator!r} needs arms of at least 1")
        if self.manipulator == "none" and (self.payload_kg is not None or self.reach_m is not None):
            raise ValueError(
                "a body with no manipulator has no payload and no reach: "
                "drop the figure or name the manipulator"
            )
        return self

    def known(self) -> list[tuple[str, Figure | Span, str]]:
        """(label, figure, unit) for every published figure, prompt order, workspace last."""
        out: list[tuple[str, Figure | Span, str]] = [
            (label, fig, unit)
            for field, label, unit in self.FIGURES
            if (fig := getattr(self, field)) is not None
        ]
        if self.workspace_height_m is not None:
            out.append(("Working height", self.workspace_height_m, "m"))
        return out

    def unknown(self) -> list[str]:
        """Prompt labels of the figures nobody has published, minus those that do not apply:
        endurance on a mains-powered body, payload and reach with nothing to hold with."""
        skip: set[str] = set()
        if self.tethered:
            skip.add("endurance_min")
        if self.manipulator == "none":
            skip |= {"payload_kg", "reach_m"}
        return [
            label.lower()
            for field, label, _unit in self.FIGURES
            if field not in skip and getattr(self, field) is None
        ]


class RobotManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", title="quackd robot manifest v1")

    manifest: Literal[1] = Field(default=1, description="Manifest schema version.")
    id: str = Field(..., description="Slug, unique within a run or flock (e.g. arm-01).")
    vendor: str
    model: str
    embodiment: Embodiment
    mobility: Mobility
    intents: list[IntentName]
    sensors: list[Sensor] = Field(default_factory=list)
    verbs: list[VerbSpec]
    preconditions: dict[str, list[str]] = Field(
        default_factory=dict,
        description="Verb -> condition names. The adapter supplies the predicates by name.",
    )
    safety_authority: SafetyAuthority = Field(default_factory=SafetyAuthority)
    frame: Frame = Field(default_factory=Frame)
    limits: dict[str, float] = Field(default_factory=dict)
    backend: str = Field(default="", description="Which backend produced this (informational).")
    blurb: str = Field(default="", description="Prompt intro: 'a small biped duck robot ...'.")
    datasheet: Datasheet | None = Field(
        default=None,
        description="Physical facts with a confidence and a source each "
        "(docs/reference/manifest-spec.md). None: the adapter published none, "
        "and the prompt says so.",
    )
    extras: dict[str, Any] = Field(default_factory=dict)

    @field_validator("id")
    @classmethod
    def _slug(cls, value: str) -> str:
        if not _SLUG_RE.match(value):
            raise ValueError("id must match ^[a-z0-9][a-z0-9_-]{0,63}$")
        return value

    @field_validator("intents", "sensors")
    @classmethod
    def _unique(cls, values: list[Any]) -> list[Any]:
        if len(set(values)) != len(values):
            raise ValueError(f"duplicates in {values}")
        return values

    @model_validator(mode="after")
    def _invariants(self) -> RobotManifest:
        from quackd.verbs.core import core_requirements_unmet  # lazy: core imports this module

        names = [v.name for v in self.verbs]
        if len(set(names)) != len(names):
            raise ValueError(f"duplicate verbs in manifest {self.id!r}")
        for v in self.verbs:
            if v.name in ALIASES:
                raise ValueError(f"declare {ALIASES[v.name]!r}, not its alias {v.name!r}")
        if "stop" not in names:
            # stop is universal: always present, always allowed, never gated
            self.verbs.append(VerbSpec(name="stop", core=True))
            names.append("stop")
        for v in self.verbs:
            if v.name == "stop" and v.safety_class != "safe":
                raise ValueError("stop can never be gated")
            if v.core:
                unmet = core_requirements_unmet(v.name, self)
                if unmet:
                    raise ValueError(f"{self.id}: core verb {v.name!r} {unmet}")
        for verb in self.preconditions:
            if verb not in names:
                raise ValueError(f"preconditions reference an undeclared verb {verb!r}")
        return self

    # ── queries ─────────────────────────────────────────────────────────────────────

    def verb_names(self) -> list[str]:
        return [v.name for v in self.verbs]

    def verb(self, name: str) -> VerbSpec | None:
        """Alias-aware lookup: `verb("walk")` is the `move` spec when `move` is declared."""
        wanted = {name, ALIASES.get(name, name)}
        return next((v for v in self.verbs if v.name in wanted), None)

    def provides(self, name: str) -> bool:
        return self.verb(name) is not None

    def digest(self) -> str:
        """A capability fingerprint: the same robot over sim2d and mock hashes the same.

        `id` and `backend` are excluded on purpose; discovery carries the id separately."""
        payload = self.model_dump(mode="json", exclude={"id", "backend"})
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]

    def summary(self) -> str:
        return (
            f"{self.model} ({self.embodiment}, mobility {self.mobility}) "
            f"{len(self.verbs)} verbs: {', '.join(self.verb_names())}"
        )


def verb_spec(
    verb: Verb,
    *,
    core: bool,
    description: str | None = None,
    safety_class: SafetyClass | None = None,
    timeout_s: float | None = None,
) -> VerbSpec:
    """A manifest entry for an implementation template (schema derived from its params)."""
    schema = verb.params.model_json_schema()
    schema.pop("title", None)
    return VerbSpec(
        name=verb.name,
        core=core,
        description=description if description is not None else "",
        params_schema=schema,
        safety_class=safety_class or verb.safety_class,
        timeout_s=timeout_s,
    )


def apply_datasheet_override(manifest: RobotManifest, override: Any) -> RobotManifest:
    """The manifest with a `.duck` v2 `datasheet:` block folded in; the same object when there
    is none.

    Field-wise: a figure from the file replaces the adapter's and is sourced to the file, the
    sentence lists extend, everything omitted stays. A task file can add a `cannot`; it can
    never delete one. `override` is a `DatasheetOverride` (quackd/duckfile/schema.py), taken by
    duck type so this module owes nothing to that one.
    """
    if override is None:
        return manifest
    base: dict[str, Any] = (
        manifest.datasheet.model_dump() if manifest.datasheet else {"manipulator": "none"}
    )
    for key in override.model_fields_set:
        given = getattr(override, key)
        if key in ("cannot", "notes", "not_rated"):
            base[key] = [*base.get(key, []), *given]
        elif isinstance(given, BaseModel):  # a figure or a span, dumped whole: a shorthand
            value = given.model_dump()  # number leaves the defaults unset, and they are real
            own = value.get("source") or ""
            value["source"] = f"{TASK_FILE}, {own}" if own else TASK_FILE
            base[key] = value
        elif given is not None:
            base[key] = given
    return manifest.model_copy(update={"datasheet": Datasheet.model_validate(base)})


def manifest_json_schema() -> dict[str, Any]:
    """The JSON Schema for a manifest, as exported to `manifest.schema.json`."""
    schema = RobotManifest.model_json_schema()
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://github.com/rokbenko/quackd/blob/main/quackd/adapters/manifest.schema.json",
        **schema,
    }
