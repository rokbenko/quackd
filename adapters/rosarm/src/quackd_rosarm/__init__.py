"""The rosarm adapter: an industrial arm on ROS 2, driven through rosbridge, rehearsed only.

Four bodies, which quackd also describes (ADR-0077): a Universal Robots UR5e, a KUKA LBR iisy 3
R760, a KUKA LBR iiwa 7 R800 and a Yaskawa MotoMini, each behind its maker's own ROS 2 driver, and
each reached as a robot type, `quackd robot add cell-1 rosarm:mock --robot-type ur5e`. This is a
family over the rosbridge package's binding (ADR-0075): roslibpy's plumbing and the protocol's
VERIFIED refs are `quackd_rosbridge`'s, the figures are the body files', and what is this package's
is what drives an arm: the driver records (`bodies.py`), one move as one trajectory goal
(`verbs.py`), and the transport that sends it and nothing else (`transport.py`).

Two backends. `mock` is the real transport over a fake bridge and a fake arm (`fake.py`), every line
from the verbs down to the roslibpy calls, run in the suite in fake time, and rehearsed. `ws` is the
same transport over roslibpy, and `make()` refuses it for every body in the owner rule's words
until somebody names a date for that arm's first hardware run (ADR-0076), as the Koch's arm type is
refused (docs/adapters/rosarm/README.md).

quackd never powers, enables, un-brakes or un-stops one of these arms, and parks none of them
(ADR-0078): the person at the cell brings the arm up, a person confirms every move, and when
quackd goes quiet in the middle of one the arm finishes it and holds.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable, Sequence
from typing import Any

from PIL import Image

from quackd.adapters.base import (
    AdapterError,
    RestResult,
    camera_urls,
    published_travel,
    refuse_rest_pose,
)
from quackd.adapters.described import body_data
from quackd.adapters.manifest import (
    Datasheet,
    Frame,
    Health,
    RobotManifest,
    SafetyAuthority,
    verb_spec,
)
from quackd.transport.base import Ack, DuckState, HeartbeatError, Intent
from quackd.verbs.core import CORE
from quackd.verbs.registry import Precondition, Verb
from quackd_rosarm.bodies import BODIES, ROBOT_TYPE, ROBOT_TYPES, ArmBody, arm_body
from quackd_rosarm.verbs import NOT_OFFERED, SPEED_SHARE, TOL_DEG, rosarm_conditions, rosarm_verbs

__version__ = "0.33.0"
"""Kept in step with quackd's own version by scripts/set_version.py. It lives here rather
than being read from the core, because this file is all an adapter's sdist contains."""

BACKENDS = ("mock", "ws")
DEFAULT_ID = "cell-01"

OWN = rosarm_verbs()
"""This family's two verbs: `report_state` in an arm's words, and `move_joints`."""

NO_REST_POSE = (
    "quackd parks an industrial arm nowhere: its controller and its brakes hold it where it "
    "stops, and a move nobody asked for at the end of a run is the hazard, not the cure"
)
"""Why `go_to_rest()` answers none, and why `make()` refuses a recorded rest pose."""

FAMILY_NOTES = (
    "quackd sends this arm one trajectory goal at a time and a cancel, and nothing else: it never "
    "powers, enables, un-brakes or un-stops it, and the person at the cell brings it up",
    "if quackd goes quiet in the middle of a move, the arm finishes that move and holds; the "
    "pendant's stop is the brake",
    "quackd is not a safety system here: the risk assessment, the guarding and the pendant are "
    "the integrator's, and quackd's confirmation and speed ceiling are courtesies",
)
"""What is true of every body in this family, beside its maker's figures."""


def _datasheet(body: ArmBody) -> Datasheet:
    sheet = Datasheet.model_validate(body_data(body.robot_type)["datasheet"])
    return sheet.model_copy(update={"notes": [*sheet.notes, *FAMILY_NOTES]})


def _blurb(body: ArmBody, backend: str) -> str:
    said = str(body_data(body.robot_type)["blurb"]).rstrip(".")
    if backend == "mock":
        return f"{said}, rehearsed through rosbridge against a fake bridge and a fake arm"
    return f"{said}, driven through rosbridge by its own ROS 2 driver"


def rosarm_manifest(
    backend: str,
    robot_id: str | None = None,
    *,
    robot_type: str | None = None,
    travel_deg: dict[str, tuple[float, float]] | None = None,
    speed_deg_s: dict[str, float] | None = None,
    action: str | None = None,
    joint_states: str | None = None,
    roslibpy_version: str | None = None,
) -> RobotManifest:
    """The arm as data. Its maker, its embodiment and its figures are the body file's
    (`described.body_data`); its travel and its rated speeds are the robot's own description as
    the bridge served it, unknown until it has, so the static manifest of `ws` claims neither.
    `move_joints` is gated on every backend: a person confirms each move (ADR-0078)."""
    body = arm_body(robot_type or ROBOT_TYPE)
    data = body_data(body.robot_type)
    verbs = [
        verb_spec(OWN["report_state"], core=True),
        verb_spec(CORE["stop"], core=True),
        verb_spec(OWN["move_joints"], core=False, safety_class="confirm"),
    ]
    extras: dict[str, Any] = {
        "robot_type": body.robot_type,
        "joints": list(body.joints),
        "driver": body.driver,
        "action": action or body.action,
        "joint_states": joint_states or body.joint_states,
        "status_topics": [topic for topic, _ in body.status.topics] if body.status else [],
        "before_a_goal": body.before_a_goal,
        "not_offered": dict(NOT_OFFERED),
        "speed_share": SPEED_SHARE,
        "tolerance_deg": TOL_DEG,
        "roslibpy_version": roslibpy_version,
    }
    if body.readings is not None:
        extras["readings"] = body.readings
    if travel_deg:
        extras["joint_range_deg"] = {
            joint: published_travel(lo, hi) for joint, (lo, hi) in travel_deg.items()
        }
        extras["joint_range_from"] = "the robot's own description on the bridge"
    if speed_deg_s:
        extras["joint_speed_deg_s"] = {joint: round(v, 3) for joint, v in speed_deg_s.items()}
    return RobotManifest(
        id=robot_id or DEFAULT_ID,
        vendor=str(data["vendor"]),
        model=str(data["model"]),
        embodiment=data["embodiment"],
        mobility=data["mobility"],
        intents=["joint"],
        sensors=["joint_state"],
        verbs=verbs,
        preconditions={"move_joints": ["fresh_state", "can_move"]},
        # nothing in the stack ends a goal when quackd goes quiet (`up.JTC_NO_CLIENT_TIMEOUT`,
        # `up.CLOSED_CLIENT_CANCELS_NOTHING`): the robot's own stop is the pendant's, and
        # quackd's heartbeat cancels what it can reach
        safety_authority=SafetyAuthority(native="estop", deadman=False, heartbeat_hz=2.0),
        frame=Frame(reference="base", note="joint space in degrees; no camera, no Cartesian goals"),
        limits={"speed_share": SPEED_SHARE},
        backend=backend,
        blurb=_blurb(body, backend),
        datasheet=_datasheet(body),
        extras=extras,
    )


class RosArmAdapter:
    """A `RobotAdapter` over `RosArmWs`, on either backend."""

    name = "rosarm"
    supports_hand_off = False
    """A cobot is hand guided on its own pendant, in a mode quackd neither enters nor watches."""
    supports_rest_pose = False
    """quackd parks none of these arms (`NO_REST_POSE`)."""

    def __init__(self, transport: Any, *, robot_id: str | None = None, bridge: Any = None) -> None:
        self.transport = transport
        self.backend = transport.name
        self.robot_id = robot_id or DEFAULT_ID
        self.manifest: RobotManifest | None = None
        self.bridge = bridge
        """The fake bridge under `mock`, which a test drives faults through; None on `ws`."""
        self.move_lock = asyncio.Lock()
        """Held by `move_joints` from its checks to its arrival, so a second move asked meanwhile
        is refused rather than read as the first one's (`verbs.move_joints`)."""

    async def connect(self) -> RobotManifest:
        await self.transport.connect()
        endpoint = self.transport.endpoint
        self.manifest = rosarm_manifest(
            self.backend,
            self.robot_id,
            robot_type=self.transport.body.robot_type,
            travel_deg=self.transport.travel_deg,
            speed_deg_s=self.transport.speed_deg_s,
            action=endpoint.action,
            joint_states=endpoint.joint_states,
            roslibpy_version=self.transport.roslibpy_version,
        )
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
        try:
            await self.transport.heartbeat()
        except HeartbeatError as e:
            return Health(ok=False, reason=str(e))
        state = await self.transport.get_state()
        return Health(
            ok=True,
            battery_percent=None,
            extras={
                "joints": state.extras.get("joints"),
                "driver": state.extras.get("driver"),
                "move": state.extras.get("move"),
            },
        )

    async def heartbeat(self) -> None:
        await self.transport.heartbeat()

    async def stop(self) -> None:
        await self.transport.stop()

    @property
    def stop_error(self) -> str | None:
        """Why the last stop did not reach the arm: the core `stop` verb reads this off whatever
        it is handed, which is this adapter, and refuses to say "stopped" over a cancel that never
        got there."""
        error = getattr(self.transport, "stop_error", None)
        return str(error) if error else None

    async def go_to_rest(self) -> RestResult:
        return RestResult.none(NO_REST_POSE)

    def subscribe(self, topic: str) -> AsyncIterator[dict[str, Any]]:
        return self.transport.subscribe(topic)

    def now(self) -> float:
        return float(self.transport.now())

    async def sleep(self, seconds: float) -> None:
        await self.transport.sleep(seconds)

    def preconditions(self) -> dict[str, Precondition]:
        return rosarm_conditions()

    def implementations(self) -> dict[str, Verb]:
        return dict(OWN)

    @property
    def mobility(self) -> str:
        return "none"

    @property
    def post_sleep(self) -> Callable[[], None] | None:
        return getattr(self.transport, "post_sleep", None)

    @post_sleep.setter
    def post_sleep(self, hook: Callable[[], None] | None) -> None:
        self.transport.post_sleep = hook


# ── what the factory calls ──────────────────────────────────────────────────────────────────


def refusal(backend: str, robot_type: str | None = None) -> str | None:
    """Why `make()` will not build this backend for this arm, in the owner rule's words, or None.
    Asked by `make()` and, through `quackd.adapters.factory.refusal`, by the registry at `quackd
    robot add`, so a robot that cannot run is never written down (ADR-0076)."""
    body = arm_body(robot_type or ROBOT_TYPE)
    if backend == "ws" and not body.drivable:
        return body.refusal
    return None


def describe(
    backend: str, robot_id: str | None = None, *, robot_type: str | None = None
) -> RobotManifest:
    """Static. The mock serves the description its fake arm is built from, so its travel and its
    speeds are known here, and what this says is what a connected run reads; `ws` knows neither
    until a bridge has served them."""
    body = arm_body(robot_type or ROBOT_TYPE)
    if backend == "mock":
        import math

        return rosarm_manifest(
            backend,
            robot_id,
            robot_type=body.robot_type,
            # as the transport reads a description (`transport.RosArmWs._check_description`)
            travel_deg={
                name: (round(math.degrees(joint.lower), 6), round(math.degrees(joint.upper), 6))
                for name, joint in zip(body.joints, body.description, strict=True)
            },
            speed_deg_s={
                name: round(math.degrees(joint.velocity), 6)
                for name, joint in zip(body.joints, body.description, strict=True)
            },
        )
    return rosarm_manifest(backend, robot_id, robot_type=body.robot_type)


def implementations() -> dict[str, Verb]:
    return dict(OWN)


def conditions() -> dict[str, Precondition]:
    return rosarm_conditions()


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
    robot_type: str | None = None,
) -> RosArmAdapter:
    """`robot_type` is which arm this is (`bodies.BODIES`, `--robot-type`), a UR5e when nobody
    says. `ws` is refused for every arm nobody has named a date for (`refusal`), before anything
    is built. A rest pose is refused, since quackd parks none of these arms, and so is a camera
    url, which has nowhere to go: a `--host` board's camera gives this body `observe`, as it gives
    any. `token`, `seed` and `live` are accepted and unused, as the factory calls every adapter's
    `make` with the same keywords: rosbridge has no authentication of its own."""
    body = arm_body(robot_type or ROBOT_TYPE)
    if backend not in BACKENDS:
        raise ValueError(f"unknown rosarm backend {backend!r}; choose one of {BACKENDS}")
    if (why := refusal(backend, body.robot_type)) is not None:
        raise AdapterError(why)
    refuse_rest_pose("rosarm", rest_pose)
    if camera_urls(camera_url):
        raise AdapterError(
            f"rosarm:{backend} reads no camera, so --camera-url has nowhere to go: a --host "
            "board's camera gives this body observe"
        )
    from quackd_rosarm.transport import RosArmWs

    if backend == "mock":
        from quackd_rosarm.fake import FakeBridge

        bridge = FakeBridge(body)
        transport = RosArmWs(
            address,
            body=body,
            roslibpy=bridge.roslibpy(),
            clock=bridge.clock,
            name="mock",
            assumptions=bridge.assumptions,
        )
        return RosArmAdapter(transport, robot_id=robot_id, bridge=bridge)
    return RosArmAdapter(RosArmWs(address, body=body), robot_id=robot_id)


__all__ = [
    "BACKENDS",
    "BODIES",
    "DEFAULT_ID",
    "ROBOT_TYPE",
    "ROBOT_TYPES",
    "RosArmAdapter",
    "conditions",
    "describe",
    "implementations",
    "make",
    "refusal",
    "rosarm_manifest",
]


# What this adapter reads from upstream, for `quackd doctor`. Declared here rather than in a
# table in the core, because the list belongs to whoever wrote the adapter (ADR-0022). The
# import is deferred so that naming the upstream costs nothing until doctor asks.
def _upstream_rows() -> tuple[tuple[str, object, str, str], ...]:
    from quackd_rosarm import upstream_api

    return (
        (
            "rosarm",
            upstream_api,
            "docs/adapters/rosarm/README.md",
            "a bridge, a controller or an arm: the mock is a fake written from these sources",
        ),
    )


UPSTREAMS = _upstream_rows()
