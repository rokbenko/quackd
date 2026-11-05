"""A robot quackd only talks for: its body as its owner described it in robot.yaml.

Most robots that carry a passport have software of their own that walks and grips, and quackd
needs none of it to introduce the robot and let its brain talk. So a robot.yaml without a
`robot:` section gets this body: a mock that reports a state and stops, under a manifest built
from what the owner wrote. It moves nothing, because nothing here can, and its prompt says so.

The body itself is `quackd.adapters.described`'s, the same one the catalogue's described
bodies are (ADR-0077); this module only builds its manifest from a robot.yaml. A robot.yaml
body is not in the catalogue and no entry point names it, so it is never one of the robots
`quackd robot add` offers.
"""

from __future__ import annotations

from typing import Any

from quackd.adapters.described import DescribedAdapter as _DescribedAdapter
from quackd.adapters.manifest import Datasheet, Figure, RobotManifest, verb_spec
from quackd.onboard.config import RobotFile
from quackd.verbs.core import CORE

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


class DescribedAdapter(_DescribedAdapter):
    """The described body of `quackd.adapters.described`, built from a robot.yaml."""

    def __init__(self, file: RobotFile) -> None:
        super().__init__(described_manifest(file))
        self._file = file
