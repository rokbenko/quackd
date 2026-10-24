"""Agile ONE SN, as a body quackd can run with no robot to hand: a mock.

Agile Robots launched Agile ONE SN on 21 October 2026 as "a humanoid reference platform built
on NVIDIA Isaac GR00T", with NVIDIA Jetson Thor onboard, to be available from the beginning of
2027. quackd has no driver for it, so this module is a mock body for the doorstep: a postman that
holds one letter, and can hold it out in an open hand or lay it flat on the floor in front of it.
Its datasheet carries what Agile Robots published, every figure sourced to that launch, and says
what they did not publish rather than guessing it.

It reaches quackd through the `quackd.adapters` entry point this folder's pyproject declares,
the way any third party's adapter does, so `--robot agile_one_sn:mock` works in this folder's
environment and nowhere else in quackd.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from typing import Any

from PIL import Image

from quackd.adapters.base import RestResult, one_camera_url, refuse_rest_pose
from quackd.adapters.manifest import Datasheet, Figure, Health, RobotManifest, verb_spec
from quackd.transport.base import Ack, DuckState, HeartbeatError, Intent
from quackd.transport.mock import MockTransport
from quackd.verbs.core import CORE
from quackd.verbs.registry import NoParams, Precondition, Verb, VerbContext, VerbResult

NAME = "agile_one_sn"
BACKENDS = ("mock",)
STATUS = "a mock only: quackd has no driver for Agile ONE SN, which ships from early 2027"
SUMMARY = "a 1.4 m humanoid from Agile Robots, built on NVIDIA Isaac GR00T"
DEFAULT_ID = "agile-one-sn-01"
SOURCE = "Agile Robots, launch of Agile ONE SN, 21 October 2026"

BLURB = (
    "a 1.4 m, 43 kg humanoid from Agile Robots, built on NVIDIA Isaac GR00T with NVIDIA Jetson "
    "Thor onboard, with two five-finger hands; here a mock, standing still at a front door with "
    "one letter in its hand"
)

DATASHEET = Datasheet(
    mass_kg=Figure(value=43.0, confidence="official", source=SOURCE),
    height_m=Figure(value=1.4, confidence="official", source=SOURCE),
    dof=Figure(
        value=71,
        confidence="official",
        source=SOURCE,
        note="31 in the body, for human-like mobility in the maker's words, and 20 in each hand",
    ),
    payload_kg=Figure(value=3.0, confidence="official", source=SOURCE, note="per arm"),
    manipulator="arms",
    arms=2,
    notes=[
        "Built on the NVIDIA Isaac GR00T development platform, with NVIDIA Jetson Thor onboard",
        "RGB-D cameras on the head, cameras on the wrists for close work, and a 3D LiDAR",
        "Two tactile five-finger hands and an emergency stop button",
        "Walking speed, reach and battery life are not published",
        "quackd has no driver for it: this body is a mock that stands still at a front door",
    ],
)

#: Where the letter can be. The mock knows nothing else about the world.
IN_HAND = "in its hand"
HELD_OUT = "held out in an open hand, at hand height"
ON_FLOOR = "flat on the floor right in front of it"


class AgileOneSnMock(MockTransport):
    """The postman in memory: where its one letter is. Nothing moves; every verb is a claim
    about the letter that the next state reports back."""

    def __init__(self) -> None:
        # the battery is not published, so the mock reports none rather than a number
        super().__init__(states=[DuckState(policy="mock", posture="standing")])
        self.letter = IN_HAND

    async def get_state(self) -> DuckState:
        state = await super().get_state()
        return state.model_copy(
            update={"holding": self.letter != ON_FLOOR, "extras": {"letter": self.letter}}
        )


def _body(ctx: VerbContext) -> AgileOneSnMock | None:
    """The mock under whatever the executor hands a verb: the adapter, or the transport."""
    for candidate in (ctx.transport, getattr(ctx.transport, "transport", None)):
        if isinstance(candidate, AgileOneSnMock):
            return candidate
    return None


async def _hold_out(ctx: VerbContext, _: NoParams) -> VerbResult:
    body = _body(ctx)
    if body is None:
        return VerbResult.fail("only the Agile ONE SN mock carries this letter")
    if body.letter == ON_FLOOR:
        return VerbResult.fail("the letter already lies on the floor", letter=body.letter)
    if ctx.dry_run:
        return VerbResult.success("dry run: would hold the letter out", letter=body.letter)
    body.letter = HELD_OUT
    return VerbResult.success(f"the letter is {HELD_OUT}", letter=body.letter)


async def _lay_down(ctx: VerbContext, _: NoParams) -> VerbResult:
    body = _body(ctx)
    if body is None:
        return VerbResult.fail("only the Agile ONE SN mock carries this letter")
    if body.letter == ON_FLOOR:
        return VerbResult.success(f"the letter already lies {ON_FLOOR}", letter=body.letter)
    if ctx.dry_run:
        return VerbResult.success("dry run: would lay the letter down", letter=body.letter)
    body.letter = ON_FLOOR
    return VerbResult.success(f"the letter lies {ON_FLOOR}", letter=body.letter)


HOLD_OUT = Verb(
    "hold_out_letter",
    "Hold the letter out in an open hand at hand height, for a robot that can take a thing "
    "from a hand.",
    _hold_out,
    NoParams,
    timeout_s=10,
    done_condition="the letter is held out in an open hand.",
)
LAY_DOWN = Verb(
    "lay_letter_down",
    "Bend and lay the letter flat on the floor right in front of you, where a small robot "
    "standing there can reach it.",
    _lay_down,
    NoParams,
    timeout_s=10,
    done_condition="the letter lies on the floor in front of you.",
)


def agile_one_sn_manifest(backend: str = "mock", robot_id: str | None = None) -> RobotManifest:
    """The postman as data."""
    return RobotManifest(
        id=robot_id or DEFAULT_ID,
        vendor="agile-robots",
        model="agile-one-sn",
        embodiment="humanoid",
        mobility="legged",
        intents=["pose", "gripper"],
        sensors=["camera", "joint_state"],
        verbs=[
            verb_spec(CORE["report_state"], core=True),
            verb_spec(CORE["stop"], core=True),
            verb_spec(HOLD_OUT, core=False),
            verb_spec(LAY_DOWN, core=False),
        ],
        backend=backend,
        blurb=BLURB,
        datasheet=DATASHEET,
        extras={
            "letter": "one, an ordinary envelope",
            "assumptions": [
                "a mock: it keeps the letter in memory and says where it is; nothing moves"
            ],
        },
    )


class AgileOneSnAdapter:
    """A `RobotAdapter` over the mock."""

    name = NAME

    def __init__(self, transport: AgileOneSnMock, *, robot_id: str | None = None) -> None:
        self.transport = transport
        self.backend = transport.name
        self.robot_id = robot_id or DEFAULT_ID
        self.manifest: RobotManifest | None = None

    async def connect(self) -> RobotManifest:
        await self.transport.connect()
        self.manifest = agile_one_sn_manifest(self.backend, self.robot_id)
        return self.manifest

    async def disconnect(self) -> None:
        await self.transport.close()

    async def close(self) -> None:
        await self.disconnect()

    async def get_state(self) -> DuckState:
        return await self.transport.get_state()

    async def get_frame(self) -> Image.Image | None:
        return await self.transport.get_frame()

    async def send_intent(self, intent: Intent) -> Ack:
        return await self.transport.send_intent(intent)

    async def health(self) -> Health:
        try:
            await self.transport.heartbeat()
        except HeartbeatError as e:
            return Health(ok=False, reason=str(e))
        return Health(ok=True, extras={"letter": self.transport.letter})

    async def heartbeat(self) -> None:
        await self.transport.heartbeat()

    async def stop(self) -> None:
        await self.transport.stop()

    async def go_to_rest(self) -> RestResult:
        return RestResult.none("a mock keeps no rest pose")

    def subscribe(self, topic: str) -> AsyncIterator[dict[str, Any]]:
        return self.transport.subscribe(topic)

    def now(self) -> float:
        return self.transport.now()

    async def sleep(self, seconds: float) -> None:
        await self.transport.sleep(seconds)

    def preconditions(self) -> dict[str, Precondition]:
        return conditions()

    def implementations(self) -> dict[str, Verb]:
        return implementations()

    @property
    def mobility(self) -> str:
        return "legged"


# what quackd's adapter factory calls


def describe(backend: str, robot_id: str | None = None) -> RobotManifest:
    if backend not in BACKENDS:
        raise ValueError(f"unknown {NAME} backend {backend!r}; choose one of {BACKENDS}")
    return agile_one_sn_manifest(backend, robot_id)


def implementations() -> dict[str, Verb]:
    return {HOLD_OUT.name: HOLD_OUT, LAY_DOWN.name: LAY_DOWN}


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
) -> AgileOneSnAdapter:
    refuse_rest_pose(NAME, rest_pose)
    one_camera_url(camera_url, spec=f"{NAME}:{backend}")
    if backend not in BACKENDS:
        raise ValueError(f"unknown {NAME} backend {backend!r}; choose one of {BACKENDS}")
    return AgileOneSnAdapter(AgileOneSnMock(), robot_id=robot_id)
