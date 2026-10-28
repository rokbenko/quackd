"""A robot quackd only talks for: its body as its owner described it in robot.yaml.

Most robots that carry a passport have software of their own that walks and grips, and quackd
needs none of it to introduce the robot and let its brain talk. So a robot.yaml without a
`robot:` section gets this body: a mock that reports a state and stops, under a manifest built
from what the owner wrote. It moves nothing, because nothing here can, and its prompt says so.

It is not an adapter in quackd's catalogue and no entry point names it, so it is never one of
the robots `quackd robot add` offers.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from PIL import Image

from quackd.adapters.base import RestResult
from quackd.adapters.manifest import Datasheet, Figure, Health, RobotManifest, verb_spec
from quackd.onboard.config import RobotFile
from quackd.transport.base import Ack, DuckState, Intent
from quackd.transport.mock import MockTransport
from quackd.verbs.core import CORE
from quackd.verbs.registry import Precondition, Verb

#: What a robot quackd only talks for can be asked to do: say how it is, and stop talking.
VERBS = ("report_state", "stop")
_FIGURES = ("mass_kg", "height_m", "payload_kg", "reach_m", "endurance_min")


def described_manifest(file: RobotFile, backend: str = "described") -> RobotManifest:
    """The body robot.yaml describes, as the manifest a pilot is shown. A tracked body is shown
    as wheeled, the nearest a manifest has; the passport keeps what the owner wrote."""
    body = file.passport.body
    embodiment = "wheeled" if body.embodiment == "tracked" else body.embodiment
    mobility = "wheeled" if body.mobility == "tracked" else body.mobility
    maker = file.passport.maker
    figures: dict[str, Any] = {}
    for name in _FIGURES:
        value = getattr(body, name)
        if value is not None:
            figures[name] = Figure(value=value[0], confidence=value[1], source=value[2])
    manipulator = body.manipulator if body.manipulator != "other" else "none"
    # the file says what the hands are, not how many: a humanoid has two, anything else one
    arms = 0 if manipulator == "none" else (2 if body.embodiment == "humanoid" else 1)
    return RobotManifest(
        id=file.passport.name,
        vendor=maker.vendor,
        model=maker.model,
        embodiment=embodiment,
        mobility=mobility,
        intents=[],
        sensors=[],
        verbs=[verb_spec(CORE[name], core=True) for name in VERBS],
        backend=backend,
        blurb=(
            f"a {body.embodiment} ({maker.vendor} {maker.model}) as its owner described it; "
            "quackd drives no part of it and only talks for it"
        ),
        datasheet=Datasheet(
            manipulator=manipulator,
            arms=arms,
            cannot=list(file.passport.cannot),
            notes=["quackd moves nothing of this body: it reports a state, stops and talks"],
            **figures,
        ),
        extras={"described": True},
    )


class DescribedBody(MockTransport):
    """A body in memory that stands where it is: a state to report, and nothing to move."""

    def __init__(self) -> None:
        super().__init__(states=[DuckState(policy="described", posture="standing")])


class DescribedAdapter:
    """A `RobotAdapter` over a `DescribedBody`, for the run loop and the executor."""

    name = "described"

    def __init__(self, file: RobotFile) -> None:
        self.transport = DescribedBody()
        self.backend = "described"
        self.robot_id = file.passport.name
        self._file = file
        self.manifest: RobotManifest | None = None

    async def connect(self) -> RobotManifest:
        await self.transport.connect()
        self.manifest = described_manifest(self._file)
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
        return Health(ok=True)

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
        return {}

    @property
    def mobility(self) -> str:
        return self.manifest.mobility if self.manifest else "none"
