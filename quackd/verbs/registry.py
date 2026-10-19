"""The verb registry: one list that is simultaneously the LLM's vocabulary, the safety
allowlist's universe, the MCP tool list, and the v2 extension point for learned policies.

A verb is data plus one coroutine. Nothing about execution policy (allowlists, budgets,
confirmations) lives here — that is `quackd.safety`.
"""

from __future__ import annotations

import dataclasses
import inspect
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from quackd.perception.base import Detector
from quackd.transport.base import CameraFrame, DuckState, DuckTransport
from quackd.verbs.aliases import ALIASES

if TYPE_CHECKING:
    from quackd.adapters.manifest import RobotManifest
    from quackd.verbs.watch import Eyes

SafetyClass = Literal["safe", "confirm", "dangerous"]

STOPPED = "a stop was sent while it ran"
"""What `VerbContext.interrupted` says once a `stop` has run since the verb began, from any
caller, so a verb can tell the brake from a budget that ran out (`watch` ends on both)."""
VerbKind = Literal["builtin", "composite", "learned", "meta"]


class VerbResult(BaseModel):
    """What the LLM reads back after a verb. Keep `summary` short and factual."""

    model_config = ConfigDict(extra="forbid")

    ok: bool
    summary: str
    data: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def fail(cls, summary: str, **data: Any) -> VerbResult:
        return cls(ok=False, summary=summary, data=data)

    @classmethod
    def success(cls, summary: str, **data: Any) -> VerbResult:
        return cls(ok=True, summary=summary, data=data)


class NoParams(BaseModel):
    model_config = ConfigDict(extra="forbid")


@dataclass
class VerbContext:
    """Everything a verb may touch. Deliberately not the executor and not the LLM."""

    transport: DuckTransport
    detector: Detector | None = None
    dry_run: bool = False
    log: Callable[[str], None] = lambda _msg: None
    on_frame: Callable[[Any, str], None] = lambda _img, _caption: None
    """The one view that steers: the primary camera's frame."""
    on_frames: Callable[[Sequence[CameraFrame], str], None] = lambda _frames, _caption: None
    """Every camera's frame, named. A body with four lenses handed to `on_frame` alone
    arrives as whichever one was read last, which is nobody's picture."""
    run_verb: Callable[[str, dict[str, Any]], Awaitable[VerbResult]] | None = None
    """Composites call other verbs through the executor, so allowlists still apply."""
    manifest: RobotManifest | None = None
    """The connected robot's manifest, so composite verbs can pick a strategy (0.4)."""
    eyes: Eyes | None = None
    """What `watch` looks through: a decision LLM that may be shown the camera, on a run that
    asked with `--decision-frames` (`quackd.agent.decision.eyes`). None on every other run, and
    then `watch` refuses before it sends anything. Still not the pilot: it answers two closed
    questions about one frame and nothing else (ADR-0054)."""
    interrupted: Callable[[], str | None] = lambda: None
    """Why a verb that runs for a while should end now, or None while nothing says so: the run's
    time budget spent, in the budget's own words, or a `stop` run since the verb began, as
    `STOPPED`. The executor builds it for each call. Only `watch` reads it so far: the budget is
    checked between verbs, and `watch` is the first verb whose point is to run for a while."""


Precondition = Callable[[DuckState], str | None]
"""Returns a reason string if the verb must NOT run right now, else None."""

ExecuteFn = Callable[[VerbContext, Any], Awaitable[VerbResult]]


@dataclass
class Verb:
    name: str
    description: str
    execute: ExecuteFn
    params: type[BaseModel] = NoParams
    timeout_s: float = 30.0
    safety_class: SafetyClass = "safe"
    kind: VerbKind = "builtin"
    preconditions: list[Precondition] = field(default_factory=list)
    read_only: bool = False
    """The verb sends no intent: it reads state or a camera and nothing else.

    Two gates believe that, so it has to be true. `--dry-run` runs a read-only verb against
    real hardware, and the verdict gate lets one run before the pilot has judged the task,
    because a verb that sends nothing cannot move the body. Every verb quackd ships with this
    flag is in `BEFORE_VERDICT` as well, and a test says it must stay there; the flag is how a
    body quackd never shipped says the same of its own sensing verbs (a `locate` that reads
    where things are)."""
    done_condition: str = ""
    """Human-readable: what 'done' means. Shown to the LLM after the description."""
    core: bool = False
    """A core verb (the same on every robot) rather than one robot's extension."""
    template: Verb | None = field(default=None, repr=False, compare=False)
    """The verb as its robot registered it, when a task file has narrowed this one
    (`quackd.duckfile.narrow`), else None. Every narrowing starts again from it, so a second
    task file is never narrowed through the first one's words."""

    def tool_schema(self) -> dict[str, Any]:
        """Provider-neutral tool definition: name, description, JSON schema."""
        schema = self.params.model_json_schema()
        schema.pop("title", None)
        schema.setdefault("type", "object")
        schema.setdefault("properties", {})
        schema["additionalProperties"] = False
        desc = self.description
        if self.done_condition:
            desc = f"{desc} Done when: {self.done_condition}"
        return {"name": self.name, "description": desc, "input_schema": schema}

    def param_summary(self) -> str:
        fields = self.params.model_fields
        if not fields:
            return "—"
        return ", ".join(f"{n}: {_type_name(f.annotation)}" for n, f in fields.items())


def _type_name(annotation: Any) -> str:
    if annotation is None:
        return "any"
    if inspect.isclass(annotation):
        return annotation.__name__
    return str(annotation).replace("typing.", "")


class VerbNotFound(KeyError):
    pass


class VerbRegistry:
    """Verbs are stored under their canonical name; every lookup accepts an alias too.

    `get("walk")` returns the `move` verb once `move` is registered, and `view("walk")`
    returns that same verb *named as the caller spelled it*, which is what the tool list
    and the system prompt show. A registry that has no `move` still resolves `walk` to a
    directly registered `walk`, so nothing changes until a canonical verb exists.
    """

    def __init__(self) -> None:
        self._verbs: dict[str, Verb] = {}

    def register(self, verb: Verb, *, replace: bool = False) -> Verb:
        if verb.name in ALIASES:
            raise ValueError(f"register {ALIASES[verb.name]!r}, not its alias {verb.name!r}")
        if verb.name in self._verbs and not replace:
            raise ValueError(f"verb {verb.name!r} already registered")
        self._verbs[verb.name] = verb
        return verb

    def canonical(self, name: str) -> str:
        """The name this registry files `name` under: itself if registered, else its alias
        target, else `name` unchanged (so unknown names stay unknown, not remapped)."""
        if name in self._verbs:
            return name
        target = ALIASES.get(name)
        return target if target is not None and target in self._verbs else name

    def get(self, name: str) -> Verb:
        try:
            return self._verbs[self.canonical(name)]
        except KeyError:
            raise VerbNotFound(name) from None

    def remove(self, name: str) -> Verb:
        """Take a verb out, by any name it answers to, and hand it back.

        For a verb a run cannot offer although its body declares it, withdrawn once the robot
        has connected: `watch` on a run with nothing to look through. `stop` is refused, because
        the brake is the one verb every registry keeps whatever else is taken out of it."""
        canonical = self.canonical(name)
        if canonical == "stop":
            raise ValueError("stop is never removed: it is the one verb every registry keeps")
        try:
            return self._verbs.pop(canonical)
        except KeyError:
            raise VerbNotFound(name) from None

    def view(self, name: str) -> Verb:
        """The verb as the caller named it: identical, except `.name` is the alias used."""
        verb = self.get(name)
        return verb if verb.name == name else dataclasses.replace(verb, name=name)

    def __contains__(self, name: str) -> bool:
        return self.canonical(name) in self._verbs

    def names(self) -> list[str]:
        return list(self._verbs)

    def aliases(self) -> dict[str, str]:
        """Alias -> canonical, for the aliases whose canonical verb is present."""
        return {a: c for a, c in ALIASES.items() if c in self._verbs and a not in self._verbs}

    def verbs(self) -> list[Verb]:
        return list(self._verbs.values())

    def tool_schemas(self, allow: list[str] | None = None) -> list[dict[str, Any]]:
        names = allow if allow is not None else self.names()
        return [self.view(n).tool_schema() for n in names]

    def unknown(self, names: list[str]) -> list[str]:
        return [n for n in names if n not in self]

    def same_verb(self, names: list[str]) -> list[tuple[str, str]]:
        """Pairs in `names` that resolve to one verb, e.g. `("walk", "move")`."""
        seen: dict[str, str] = {}
        pairs: list[tuple[str, str]] = []
        for n in names:
            c = self.canonical(n)
            if c in seen and seen[c] != n:
                pairs.append((seen[c], n))
            seen.setdefault(c, n)
        return pairs


class ManifestError(ValueError):
    """A manifest names a verb or a precondition that no code implements."""


def registry_from_manifest(
    manifest: RobotManifest,
    adapter: Any = None,
    *,
    implementations: Mapping[str, Verb] | None = None,
    conditions: Mapping[str, Precondition] | None = None,
) -> VerbRegistry:
    """The manifest decides WHICH verbs exist and how they are gated; code decides HOW they
    run. Implementations come from the adapter first, then from `verbs/core.py`."""
    from quackd.verbs.core import CORE

    if implementations is None:
        implementations = adapter.implementations() if adapter is not None else {}
    if conditions is None:
        conditions = adapter.preconditions() if adapter is not None else {}
    templates: dict[str, Verb] = {**CORE, **implementations}
    registry = VerbRegistry()
    for spec in manifest.verbs:
        template = templates.get(spec.name)
        if template is None:
            raise ManifestError(f"{manifest.id}: verb {spec.name!r} has no implementation")
        preconditions: list[Precondition] = []
        for condition in manifest.preconditions.get(spec.name, []):
            predicate = conditions.get(condition)
            if predicate is None:
                raise ManifestError(
                    f"{manifest.id}: precondition {condition!r} of {spec.name} has no predicate"
                )
            preconditions.append(predicate)
        registry.register(
            dataclasses.replace(
                template,
                description=spec.description or template.description,
                safety_class="safe" if spec.name == "stop" else spec.safety_class,
                timeout_s=spec.timeout_s if spec.timeout_s is not None else template.timeout_s,
                preconditions=preconditions,
                core=spec.core,
            )
        )
    if "stop" not in registry:  # the manifest validator already inserted it; belt and braces
        registry.register(CORE["stop"])
    return registry


def core_registry() -> VerbRegistry:
    """The verbs every body has, and no body's own.

    The base `installed_vocabulary()` builds on when it unions what the installed bodies can
    do. It is deliberately not the placeholder the loop, a flock member and the MCP server
    use before a robot has described itself: those accept a bare `DuckTransport` as well as
    an adapter, and a bare transport is a Microduck transport whose `connect()` returns no
    manifest to rebuild from, so the Microduck's own list is the honest fallback there."""
    from quackd.verbs.core import CORE

    registry = VerbRegistry()
    for verb in CORE.values():
        registry.register(verb)
    return registry


def default_registry() -> VerbRegistry:
    """The vocabulary a caller gets when it holds a transport that never described itself.

    A bare `DuckTransport` returns no manifest from `connect()`, so there is nothing to build
    a vocabulary from. Historically that meant the Microduck's list, because every bare
    transport was a Microduck's. Now that the duck is a package of its own, it is asked when
    it is installed and the core verbs answer when it is not: a body that never said what it
    can do gets the verbs every body has, which is the honest floor.

    Learned verbs are added by whoever has one (v2)."""
    try:
        from quackd_microduck import MICRODUCK_VERBS, microduck_conditions, microduck_manifest
    except ImportError:
        return core_registry()

    return registry_from_manifest(
        microduck_manifest("sim2d"),
        implementations=MICRODUCK_VERBS,
        conditions=microduck_conditions(),
    )
