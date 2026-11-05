"""A body quackd only describes: one YAML in the core, reached as `described:<name>` (ADR-0077).

A described body is a robot quackd can list, register, describe to a pilot and plan with in a
flock, and drives no part of. Each is a file under `quackd/adapters/bodies/` holding what the
manifest holds about any body, its maker, its embodiment and its datasheet with a source and a
confidence per figure, and nothing a body nobody drives could honestly claim: no intents, no
verbs that move, no limits, no safety authority. The loader builds the `RobotManifest` with
`report_state` and `stop` as its only verbs, and a pilot handed one is told so in its prompt.

The catalogue's tenth row lists the bodies as its backends, so every path that reads an
adapter's name, `--robot`, the registry, a flock's roster, `quackd ui`, reaches one through this
module as it reaches any other body. The few that must not treat one as a robot to drive ask
`quackd.adapters.factory.drives`: the fallback when no robot is named, a probe, the registry's
door, the page's connect fields. The list of bodies a task could go to, and `announce`, ask the
manifest's `extras.described`.
"""

from __future__ import annotations

import dataclasses
from collections.abc import AsyncIterator, Mapping, Sequence
from functools import cache
from importlib import resources
from typing import Any

import yaml
from PIL import Image
from pydantic import ValidationError

from quackd.adapters.base import AdapterError, RestResult
from quackd.adapters.catalogue import DESCRIBED, DESCRIBED_BODIES
from quackd.adapters.manifest import Health, RobotManifest, verb_spec
from quackd.transport.base import Ack, DuckState, Intent
from quackd.transport.mock import MockTransport
from quackd.verbs.core import CORE
from quackd.verbs.registry import NoParams, Precondition, Verb, VerbContext, VerbResult

BACKENDS: tuple[str, ...] = DESCRIBED_BODIES
"""The bodies, as the factory reads a module's backends: the catalogue's list, which a test
holds equal to the files in `bodies/`."""
SIMULATOR_BACKENDS: tuple[str, ...] = ()

VERBS = ("report_state", "stop")
"""All a described body can be asked: say how it is, and stop saying so."""
DRIVES_NOTHING = "quackd drives no part of it"
"""The clause every described body's blurb ends with, so no file can leave it out."""

ALLOWED = frozenset({"vendor", "model", "embodiment", "mobility", "blurb", "datasheet"})
"""The keys a body file may carry: the manifest's own fields, minus everything a body nobody
drives could not honestly claim. No `sensors` among them: a manifest's sensors are what quackd
reads, a verb's requirements are judged against them, and quackd reads nothing from a body it
only describes. What the robot carries is a note in its datasheet."""
REQUIRED = ("vendor", "model", "embodiment", "mobility", "blurb", "datasheet")


class BodyFileError(AdapterError):
    """A body file says something quackd cannot use, named by file and key."""


def body_files() -> tuple[str, ...]:
    """The names of the files that are here, which the catalogue's list is held equal to."""
    folder = resources.files("quackd.adapters") / "bodies"
    return tuple(
        sorted(entry.name[:-5] for entry in folder.iterdir() if entry.name.endswith(".yaml"))
    )


def parse_body(name: str, text: str) -> dict[str, Any]:
    """A body file's text, checked: a mapping of allowed keys, every required one present.

    Checked here by name, before pydantic sees it, because the manifest's own refusal of an
    unknown field would name the field and not the rule: a `limits` key is not a typo but a
    claim a body nobody drives cannot make."""
    where = f"quackd/adapters/bodies/{name}.yaml"
    data = yaml.safe_load(text)
    if not isinstance(data, dict):
        raise BodyFileError(f"{where}: not a mapping of a body's fields")
    for key in data:
        if key not in ALLOWED:
            raise BodyFileError(
                f"{where}: {key!r} is not a key a described body may carry; it takes "
                f"{', '.join(sorted(ALLOWED))} and nothing a body nobody drives could claim"
            )
    for key in REQUIRED:
        if key not in data:
            raise BodyFileError(f"{where}: {key!r} is missing")
    return data


@cache
def _data(name: str) -> dict[str, Any]:
    """The file, read once and checked."""
    if name not in DESCRIBED_BODIES:
        raise AdapterError(
            f"no described body is called {name!r}; the bodies are {', '.join(DESCRIBED_BODIES)}"
        )
    text = (resources.files("quackd.adapters") / "bodies" / f"{name}.yaml").read_text(
        encoding="utf-8"
    )
    return parse_body(name, text)


def manifest_from(name: str, data: Mapping[str, Any], robot_id: str | None = None) -> RobotManifest:
    """The body as the manifest a pilot is shown: the file's fields, two verbs, no intents."""
    try:
        return RobotManifest(
            id=robot_id or name,
            vendor=str(data["vendor"]),
            model=str(data["model"]),
            embodiment=data["embodiment"],
            mobility=data["mobility"],
            intents=[],
            sensors=[],
            verbs=[verb_spec(CORE[verb], core=True) for verb in VERBS],
            backend=name,
            blurb=f"{str(data['blurb']).rstrip('.')}; {DRIVES_NOTHING}",
            datasheet=data["datasheet"],
            extras={"described": "maker"},
        )
    except ValidationError as e:
        raise BodyFileError(f"quackd/adapters/bodies/{name}.yaml: {e}") from e


def body_manifest(name: str, robot_id: str | None = None) -> RobotManifest:
    return manifest_from(name, _data(name), robot_id)


# ── what the factory calls ──────────────────────────────────────────────────────────────


def describe(backend: str, robot_id: str | None = None) -> RobotManifest:
    return body_manifest(backend, robot_id)


def implementations() -> dict[str, Verb]:
    return {"stop": STOP}


def conditions() -> dict[str, Precondition]:
    return {}


def make(
    backend: str,
    *,
    robot_id: str | None = None,
    seed: int | None = None,
    address: str | None = None,
    live: bool = False,
    camera_url: str | Sequence[str] | None = None,
    token: str | None = None,
    rest_pose: dict[str, float] | None = None,
) -> DescribedAdapter:
    """A described body, refusing everything that would reach a robot: there is none to reach,
    and a flag accepted and dropped would be a claim the body cannot keep."""
    if address:
        raise AdapterError(f"a described body takes no --address: {DRIVES_NOTHING}")
    if camera_url:
        raise AdapterError(f"a described body takes no --camera-url: {DRIVES_NOTHING}")
    if token:
        raise AdapterError(f"a described body takes no --token: {DRIVES_NOTHING}")
    if rest_pose is not None:
        raise AdapterError(
            f"a described body keeps no rest pose, so one cannot be kept for it: {DRIVES_NOTHING}"
        )
    return DescribedAdapter(describe(backend, robot_id))


# ── the body in memory ──────────────────────────────────────────────────────────────────


NOTHING_READ = "quackd reads nothing from a described body"
"""Why its posture is unknown and what its health rests on: nothing here has heard from a robot."""


async def _nothing_to_stop(ctx: VerbContext, _: NoParams) -> VerbResult:
    """No intent goes anywhere, so the core's "stopped (velocity zeroed)" would be a claim."""
    return VerbResult.success(f"nothing to stop: {DRIVES_NOTHING}")


STOP = dataclasses.replace(CORE["stop"], execute=_nothing_to_stop)
"""`stop` on a described body, which says so rather than report a velocity it never had."""


class DescribedBody(MockTransport):
    """A body in memory: a state to report, and nothing to move.

    Its posture is unknown, with why, and nothing reports falls, since nothing here has heard
    from the robot: `fallen: false` from a body nobody reads is silence and not a verdict."""

    def __init__(self, state: DuckState | None = None) -> None:
        told = {"posture_reason": NOTHING_READ, "fall_detection": False}
        super().__init__(
            states=[state or DuckState(policy="described", posture="unknown", extras=told)]
        )


class DescribedAdapter:
    """A `RobotAdapter` over a `DescribedBody`, for the run loop, the executor and a flock."""

    name = DESCRIBED

    def __init__(self, manifest: RobotManifest, *, state: DuckState | None = None) -> None:
        self.transport = DescribedBody(state)
        self.backend = manifest.backend
        self.robot_id = manifest.id
        self._static = manifest
        self.manifest: RobotManifest | None = None

    async def connect(self) -> RobotManifest:
        await self.transport.connect()
        self.manifest = self._static
        return self.manifest

    async def disconnect(self) -> None:
        await self.transport.close()

    async def close(self) -> None:
        await self.disconnect()

    async def get_state(self) -> DuckState:
        return await self.transport.get_state()

    async def get_frame(self) -> Image.Image | None:
        return None

    async def send_intent(self, intent: Intent) -> Ack:
        return await self.transport.send_intent(intent)

    async def health(self) -> Health:
        return Health(ok=True, reason=NOTHING_READ)

    async def heartbeat(self) -> None:
        await self.transport.heartbeat()

    async def stop(self) -> None:
        await self.transport.stop()

    async def go_to_rest(self) -> RestResult:
        return RestResult.none("a described body keeps no rest pose")

    def subscribe(self, topic: str) -> AsyncIterator[dict[str, Any]]:
        return self.transport.subscribe(topic)

    def now(self) -> float:
        return self.transport.now()

    async def sleep(self, seconds: float) -> None:
        await self.transport.sleep(seconds)

    def preconditions(self) -> dict[str, Precondition]:
        return {}

    def implementations(self) -> dict[str, Verb]:
        return {"stop": STOP}

    @property
    def mobility(self) -> str:
        return self._static.mobility


__all__ = [
    "ALLOWED",
    "BACKENDS",
    "DRIVES_NOTHING",
    "REQUIRED",
    "VERBS",
    "BodyFileError",
    "DescribedAdapter",
    "DescribedBody",
    "body_files",
    "body_manifest",
    "conditions",
    "describe",
    "implementations",
    "make",
    "manifest_from",
    "parse_body",
]
