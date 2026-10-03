"""The rosbridge adapter: any wheeled base that takes a Twist over rosbridge.

The first robot in quackd that is not a specific product: a ROS 2 base reachable through
`rosbridge_server`. Its manifest is small and honest: one intent (`twist`), odometry,
optionally a compressed image topic, and therefore `move`, `stop`, `report_state`, plus
`observe`, `go_to`, `search_scan` and `approach_and` only when a camera topic is given.
No `say`, no `gaze`. Two backends: `mock` (offline kinematics with deadman semantics) and
`ws` (roslibpy behind `quackd[rosbridge]`, never run against a bridge by us).

The name says nothing about the body, so this is the one adapter whose datasheet is not a
constant: at connect it asks the bridge for the topic list and the robot's own description,
and a mass and a count of moving joints come back from the URDF. What is not in a URDF, a
payload above all, stays unknown, and `introspect` asks again (ADR-0032).
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Sequence
from typing import Any

from PIL import Image

from quackd.adapters.base import RestResult, one_camera_url, refuse_rest_pose
from quackd.adapters.manifest import (
    Datasheet,
    Frame,
    Health,
    RobotManifest,
    SafetyAuthority,
    verb_spec,
)
from quackd.transport.base import (
    Ack,
    DuckState,
    DuckTransport,
    HeartbeatError,
    Intent,
    TransportError,
)
from quackd.verbs.core import CORE
from quackd.verbs.registry import Precondition, Verb
from quackd_rosbridge.introspection import (
    Introspection,
    datasheet_from_introspection,
)
from quackd_rosbridge.verbs import rosbridge_verbs

OWN = rosbridge_verbs()
"""The one verb that is this adapter's own: everything else here is a core verb."""


__version__ = "0.16.1"
"""Kept in step with quackd's own version by scripts/set_version.py. It lives here
rather than being read from the core, because this file is all an adapter's sdist
contains."""

BACKENDS = ("mock", "ws")
DEFAULT_ID = "base-01"
MAX_VX = 0.3
MAX_WZ = 1.0
BLURB = (
    "a small wheeled base driven over rosbridge (a ROS 2 robot that takes velocity "
    "commands and reports odometry)"
)

DATASHEET = Datasheet(
    manipulator="none",
    cannot=[
        "carry, push or hold anything through quackd: this adapter commands a velocity and "
        "nothing else",
    ],
    notes=[
        "rosbridge is a software bridge: the name says nothing about the body under it. What is "
        "unknown here is unknown, not zero",
    ],
)
_MOVE_DESCRIPTION = (
    "Drive with a velocity for a duration: vx forward m/s, wz rad/s (+ = left). The base's "
    "own driver moves; quackd re-sends the command while the verb runs."
)


def rosbridge_manifest(
    backend: str,
    robot_id: str | None = None,
    *,
    camera: bool = False,
    max_vx: float = MAX_VX,
    max_wz: float = MAX_WZ,
    cmd_vel: str = "/cmd_vel",
    odom: str = "/odom",
    image: str | None = None,
    roslibpy_version: str | None = None,
    datasheet: Datasheet | None = None,
) -> RobotManifest:
    """The base as data. `camera` is whether an image topic is configured, and `datasheet` is
    what the bridge said about the body when it was asked (`introspection.py`); without one,
    the static sheet says nothing is known, which is the honest answer for a name."""
    verbs = [
        verb_spec(CORE["report_state"], core=True),
        verb_spec(CORE["stop"], core=True),
        verb_spec(CORE["move"], core=True, description=_MOVE_DESCRIPTION),
        verb_spec(OWN["introspect"], core=False),
    ]
    if camera:
        verbs = [
            verb_spec(CORE["observe"], core=True),
            *verbs,
            verb_spec(CORE["go_to"], core=True),
            verb_spec(CORE["search_scan"], core=True),
            verb_spec(CORE["approach_and"], core=True),
        ]
    sensors: list[Any] = ["odometry"] + (["camera"] if camera else [])
    return RobotManifest(
        id=robot_id or DEFAULT_ID,
        vendor="ros",
        model="rosbridge-base",
        embodiment="wheeled",
        mobility="wheeled",
        intents=["twist"],
        sensors=sensors,
        verbs=verbs,
        preconditions={},
        # no deadman was verified anywhere: quackd re-sends and zeroes, and says so
        safety_authority=SafetyAuthority(native="none", deadman=False, heartbeat_hz=2.0),
        frame=Frame(reference="base", note="Twist in the base frame; odometry in its odom frame"),
        limits={"max_vx": max_vx, "max_vy": 0.0, "max_wz": max_wz},
        backend=backend,
        blurb=BLURB,
        datasheet=datasheet if datasheet is not None else DATASHEET,
        extras={
            "ros": "2",
            "cmd_vel": cmd_vel,
            "odom": odom,
            "image": image,
            "roslibpy_version": roslibpy_version,
        },
    )


class RosbridgeAdapter:
    """A `RobotAdapter` over the mock or the ws backend."""

    name = "rosbridge"

    def __init__(self, transport: DuckTransport, *, robot_id: str | None = None) -> None:
        self.transport = transport
        self.backend = transport.name
        self.robot_id = robot_id or DEFAULT_ID
        self.manifest: RobotManifest | None = None

    async def connect(self) -> RobotManifest:
        await self.transport.connect()
        endpoint = getattr(self.transport, "endpoint", None)
        self.manifest = rosbridge_manifest(
            self.backend,
            self.robot_id,
            camera=bool(getattr(self.transport, "camera_available", False)),
            cmd_vel=getattr(endpoint, "cmd_vel", "/cmd_vel"),
            odom=getattr(endpoint, "odom", "/odom"),
            image=getattr(endpoint, "image", None),
            roslibpy_version=getattr(self.transport, "roslibpy_version", None),
            datasheet=datasheet_from_introspection(
                getattr(self.transport, "introspection", None), base=DATASHEET
            ),
        )
        return self.manifest

    async def introspect(self) -> Introspection:
        """Re-read the bridge and refresh the datasheet in place, so a pilot that asks again
        is judging against what the robot says now rather than what it said at connect."""
        reread = getattr(self.transport, "introspect", None)
        if reread is None:
            raise TransportError(f"the {self.backend} backend cannot re-read a description")
        intro: Introspection = await reread()
        if self.manifest is not None:
            self.manifest.datasheet = datasheet_from_introspection(intro, base=DATASHEET)
        return intro

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
        state = await self.transport.get_state()
        return Health(ok=True, battery_percent=None, extras={"odom": state.extras.get("odom")})

    async def heartbeat(self) -> None:
        await self.transport.heartbeat()

    async def stop(self) -> None:
        await self.transport.stop()

    async def go_to_rest(self) -> RestResult:
        """A wheeled base holds no pose: this adapter commands a velocity, so zeroing it in
        `stop()` is the whole of coming to rest and there is nothing further to drive to."""
        return RestResult.none()

    def subscribe(self, topic: str) -> AsyncIterator[dict[str, Any]]:
        return self.transport.subscribe(topic)

    def now(self) -> float:
        return self.transport.now()

    async def sleep(self, seconds: float) -> None:
        await self.transport.sleep(seconds)

    def preconditions(self) -> dict[str, Precondition]:
        return {}

    def implementations(self) -> dict[str, Verb]:
        return dict(OWN)  # one verb of its own: asking the bridge what the body is

    @property
    def mobility(self) -> str:
        return "wheeled"

    @property
    def post_sleep(self) -> Callable[[], None] | None:
        return getattr(self.transport, "post_sleep", None)

    @post_sleep.setter
    def post_sleep(self, hook: Callable[[], None] | None) -> None:
        self.transport.post_sleep = hook  # type: ignore[attr-defined]


# ── what the factory calls ──────────────────────────────────────────────────────────────


def describe(backend: str, robot_id: str | None = None) -> RobotManifest:
    """Static: the mock serves a frame and a canned description, so what it says here is what
    a connected run reads. The ws backend knows nothing until it has asked a bridge: whether
    there is a camera, and what the body is."""
    if backend == "mock":
        from quackd_rosbridge.mock import mock_introspection

        return rosbridge_manifest(
            backend,
            robot_id,
            camera=True,
            datasheet=datasheet_from_introspection(mock_introspection(), base=DATASHEET),
        )
    return rosbridge_manifest(backend, robot_id, camera=False)


def implementations() -> dict[str, Verb]:
    return dict(OWN)


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
) -> RosbridgeAdapter:
    refuse_rest_pose("rosbridge", rest_pose)
    # The image topic arrives in --address, so the url itself has nowhere to go here. It is
    # still collapsed, because a second camera is a body this adapter cannot drive and saying
    # so beats opening the first and dropping the rest without a word.
    _url = one_camera_url(camera_url, spec=f"rosbridge:{backend}")
    if backend == "mock":
        from quackd_rosbridge.mock import RosbridgeMock

        return RosbridgeAdapter(RosbridgeMock(), robot_id=robot_id)
    if backend == "ws":
        from quackd_rosbridge.ws import RosbridgeWs

        return RosbridgeAdapter(RosbridgeWs(address=address), robot_id=robot_id)
    raise ValueError(f"unknown rosbridge backend {backend!r}; choose one of {BACKENDS}")


__all__ = [
    "BACKENDS",
    "DEFAULT_ID",
    "RosbridgeAdapter",
    "conditions",
    "describe",
    "implementations",
    "make",
    "rosbridge_manifest",
]


# What this adapter reads from upstream, for `quackd doctor`. Declared here rather than in a
# table in the core, because the list belongs to whoever wrote the adapter (ADR-0022). The
# import is deferred so that naming the upstream costs nothing until doctor asks.
def _upstream_rows() -> tuple[tuple[str, object, str, str], ...]:
    from quackd_rosbridge import upstream_api

    return (
        (
            "rosbridge",
            upstream_api,
            "docs/adapters/rosbridge/README.md",
            "a bridge (the ws backend)",
        ),
    )


UPSTREAMS = _upstream_rows()
