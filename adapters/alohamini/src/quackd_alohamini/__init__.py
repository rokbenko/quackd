"""The AlohaMini adapter: two arms on a lift, on a holonomic base.

Architecturally much closer to a LeKiwi than to an ALOHA: three omniwheels carrying a
motorised vertical axis with a follower arm on each side. It is the second bimanual body in
quackd and the first with a lift, so `pose` joins `twist`, `joint` and `gripper`.

quackd never imports it. The project is a fork of LeRobot that calls itself `lerobot`, is not
on PyPI, and installs only from a large git clone on Python 3.12 with torch, so the adapter
speaks its ZeroMQ host protocol instead and needs `pyzmq` alone (ADR-0027, `upstream_api.py`).
Three backends: `mock` (offline), `sim2d` (the cartoon world, with a lift) and `zmq` (the real
host behind `quackd[alohamini]`, never run against a robot by us).

Two things this robot has not got shape the manifest. There is no speaker and no head, so
`say` and `gaze` are not declared. And there is no odometry anywhere in the observation, only
velocities, so `go_to` closes the loop on the camera alone and `report_state` never claims a
position.

One thing it has got is worse than not having it: as shipped, **the arms have no torque**.
Upstream disables it and both re-enable calls are commented out, so a joint command would move
nothing and a stop could not hold. quackd ships a small host wrapper that turns it back on and
advertises itself; without that wrapper the three arm verbs refuse rather than pretend.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Sequence
from typing import Any
from urllib.parse import parse_qs, urlsplit

from PIL import Image

from quackd.adapters.base import RestResult, one_camera_url, refuse_rest_pose
from quackd.adapters.manifest import (
    Datasheet,
    Figure,
    Frame,
    Health,
    RobotManifest,
    SafetyAuthority,
    verb_spec,
)
from quackd.transport.base import Ack, DuckState, DuckTransport, HeartbeatError, Intent
from quackd.verbs.core import CORE
from quackd.verbs.registry import Precondition, Verb
from quackd_alohamini.verbs import (
    DEFAULT_MODEL,
    LIFT_MAX_MM,
    LIFT_MIN_MM,
    MODELS,
    alohamini_conditions,
    alohamini_verbs,
    joints_for,
)

__version__ = "0.17.1"
"""Kept in step with quackd's own version by scripts/set_version.py. It lives here
rather than being read from the core, because this file is all an adapter's sdist
contains."""

BACKENDS = ("mock", "sim2d", "zmq")
DEFAULT_ID = "alohamini-01"

# Upstream's own fast tier is 0.25 m/s and 75 deg/s (`upstream_api.SPEED_LEVELS`). quackd's
# schema caps vy at 0.2, and `limits` may only narrow, so on that axis the schema binds.
MAX_VX = 0.25
MAX_VY = 0.2
MAX_WZ = 1.30

WATCHDOG_MS = 1000
"""The host's own window (`upstream_api.WATCHDOG_TIMEOUT_MS`)."""
HEARTBEAT_HZ = 10.0
"""Ten times the watchdog floor, and a third of the host's loop. It matters for the lift,
which runs one proportional step per received command: a slow heartbeat would move it in
coarse bursts between corrections."""

BLURB = (
    "a two-armed robot on a wheeled base, with a motorised lift that raises both arms from "
    "the floor to table height"
)

DATASHEET = Datasheet(
    dof=Figure(
        value=14,
        confidence="official",
        source="the AlohaMini2 README",
        note="six joints and a gripper per arm; the lift and the base are on top of that",
    ),
    payload_kg=Figure(
        value=1.0, confidence="official", source="the AlohaMini2 README", note="per arm"
    ),
    reach_m=Figure(value=0.52, confidence="official", source="the AlohaMini2 README"),
    manipulator="gripper",
    arms=2,
    tethered=False,
    terrain="indoor_flat",
    not_rated=["stairs", "steps", "slopes"],
    cannot=[
        "hold more than 1 kg in one hand",
        "reach further than about half a metre from an arm's base",
        "see depth: five colour cameras, no depth sensor and no lidar",
    ],
    notes=[
        "The base carries a static load of 70 kg and the lift column 30 kg, which is what it "
        "rides on, not what the hands can hold",
        "Mass, driving speed, battery runtime and whether there is an IMU are not published; it "
        "runs on two 12 V 11.2 Ah packs",
    ],
)
_MOVE_DESCRIPTION = (
    "Drive with a velocity for a duration: vx forward m/s, vy left m/s (this base can slide "
    "sideways without turning), wz rad/s (+ = left). The base's own driver moves; quackd "
    "re-sends the command while the verb runs."
)


def parse_model(address: str | None) -> str:
    """`tcp://host:5555?model=alohamini2`. Only a hint: connect() prefers the observed keys."""
    if not address:
        return DEFAULT_MODEL
    wanted = parse_qs(urlsplit(address).query).get("model", [DEFAULT_MODEL])[0].strip().lower()
    return wanted if wanted in MODELS else DEFAULT_MODEL


def alohamini_manifest(
    backend: str,
    robot_id: str | None = None,
    *,
    camera: bool = False,
    cameras: tuple[str, ...] = (),
    arms: bool = True,
    arm_torque: bool = False,
    model: str = DEFAULT_MODEL,
    host: str | None = None,
) -> RobotManifest:
    """The robot as data, built from what the wire actually carried rather than from config."""
    own = alohamini_verbs(arms=arms)
    verbs = [
        verb_spec(CORE["report_state"], core=True),
        verb_spec(CORE["stop"], core=True),
        verb_spec(CORE["move"], core=True, description=_MOVE_DESCRIPTION),
        verb_spec(own["lift"], core=False, safety_class="confirm"),
    ]
    preconditions: dict[str, list[str]] = {
        "move": ["host_fresh", "calibrated"],
        "lift": ["host_fresh", "calibrated"],
    }
    if arms:
        verbs += [
            verb_spec(own["move_joints"], core=False, safety_class="confirm"),
            verb_spec(own["gripper"], core=False),
            verb_spec(own["home_arms"], core=False, safety_class="confirm"),
        ]
        preconditions |= {
            "move_joints": ["host_fresh", "calibrated", "arm_torque"],
            "gripper": ["host_fresh", "arm_torque"],
            "home_arms": ["host_fresh", "calibrated", "arm_torque"],
        }
    if camera:
        verbs = [
            verb_spec(CORE["observe"], core=True),
            *verbs,
            verb_spec(CORE["go_to"], core=True),
            verb_spec(CORE["search_scan"], core=True),
            verb_spec(CORE["approach_and"], core=True),
        ]
        preconditions |= {
            "go_to": ["host_fresh", "calibrated"],
            "search_scan": ["host_fresh", "calibrated"],
            "approach_and": ["host_fresh", "calibrated"],
        }
    intents: list[Any] = ["twist", "pose"] + (["joint", "gripper"] if arms else [])
    sensors: list[Any] = ["joint_state"] + (["camera"] if camera else [])
    return RobotManifest(
        id=robot_id or DEFAULT_ID,
        vendor="liyiteng",
        model=model,
        embodiment="wheeled",
        mobility="wheeled",
        intents=intents,
        sensors=sensors,
        verbs=verbs,
        preconditions=preconditions,
        # The host's 1 s watchdog is real, but stop_motion is the base and the lift and NOT
        # the arms, so the scope goes in extras rather than letting the flag imply more.
        safety_authority=SafetyAuthority(native="none", deadman=True, heartbeat_hz=HEARTBEAT_HZ),
        frame=Frame(
            reference="base",
            note=(
                "body frame: +x forward, +y left (SIGN UNVERIFIED), wz rad/s converted to the "
                "robot's own deg/s on the wire. Arm joints are normalised -100..100 "
                "(grippers 0..100), NOT degrees. The lift is absolute millimetres, 5 to 600. "
                "No odometry: velocities only, never a pose."
            ),
        ),
        limits={
            "max_vx": MAX_VX,
            "max_vy": MAX_VY,
            "max_wz": MAX_WZ,
            "lift_min_mm": LIFT_MIN_MM,
            "lift_max_mm": LIFT_MAX_MM,
            "joint_norm": 100.0,
        },
        backend=backend,
        blurb=BLURB,
        datasheet=DATASHEET,
        extras={
            "robot_model": model,
            "joints": list(joints_for(model)) if arms else [],
            "arms": arms,
            "arm_torque": arm_torque,
            "cameras": list(cameras),
            "host": host,
            "deadman_scope": "base_and_lift_only",
            "watchdog_ms": WATCHDOG_MS,
            "jpeg_quality": 70,
            "speech": "none",
            "assumptions": [
                "holding is what quackd commanded, never what the robot felt",
                "the base sign convention is unverified: whether +x is physically forward "
                "depends on wheel mounting and motor polarity, which no source read settles",
                "camera frames are assumed to hold RGB in stored order and are not swapped",
                "bearings assume a 90 degree field of view, so distances are approximate",
                "no odometry exists, so go_to closes the loop on the camera alone",
                "the lift's speed in millimetres per second is unknown upstream",
            ],
        },
    )


class AlohaMiniAdapter:
    """A `RobotAdapter` over the mock, sim2d or zmq backend."""

    name = "alohamini"

    def __init__(self, transport: DuckTransport, *, robot_id: str | None = None) -> None:
        self.transport = transport
        self.backend = transport.name
        self.robot_id = robot_id or DEFAULT_ID
        self.manifest: RobotManifest | None = None

    async def connect(self) -> RobotManifest:
        await self.transport.connect()
        # what the wire carried, not what a config claimed: the host and upstream's own client
        # disagree about the default SKU and nothing cross-checks them
        self.manifest = alohamini_manifest(
            self.backend,
            self.robot_id,
            camera=bool(getattr(self.transport, "camera_available", False)),
            cameras=tuple(getattr(self.transport, "cameras", ()) or ()),
            arms=bool(getattr(self.transport, "arms_available", True)),
            arm_torque=bool(getattr(self.transport, "arm_torque", False)),
            model=str(getattr(self.transport, "robot_model", DEFAULT_MODEL)),
            host=getattr(self.transport, "host", None),
        )
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
        state = await self.transport.get_state()
        return Health(
            ok=True,
            # nothing on the wire reports a battery
            battery_percent=None,
            extras={
                "stale_ms": state.extras.get("stale_ms"),
                "lift_height_mm": state.extras.get("lift_height_mm"),
                "arm_torque": state.extras.get("arm_torque"),
                "holding": state.extras.get("holding"),
            },
        )

    async def heartbeat(self) -> None:
        await self.transport.heartbeat()

    async def stop(self) -> None:
        await self.transport.stop()

    async def go_to_rest(self) -> RestResult:
        """This is not a body quackd parks: make() refuses a rest pose for it, so there is
        nothing here to drive to."""
        return RestResult.none()

    def subscribe(self, topic: str) -> AsyncIterator[dict[str, Any]]:
        return self.transport.subscribe(topic)

    def now(self) -> float:
        return self.transport.now()

    async def sleep(self, seconds: float) -> None:
        await self.transport.sleep(seconds)

    def preconditions(self) -> dict[str, Precondition]:
        return alohamini_conditions()

    def implementations(self) -> dict[str, Verb]:
        return alohamini_verbs(arms=True)

    @property
    def mobility(self) -> str:
        return "wheeled"

    @property
    def world(self) -> Any:
        """The simulated world, for the GIF recorder. None on the backends that have none.

        Every adapter with a sim2d backend forwards this; without it the recorder gets no
        world, records nothing, and fails saving an empty GIF at the end of an otherwise
        successful run."""
        return getattr(self.transport, "world", None)

    @property
    def duck_index(self) -> int:
        return int(getattr(self.transport, "duck_index", 0))

    @property
    def post_sleep(self) -> Callable[[], None] | None:
        return getattr(self.transport, "post_sleep", None)

    @post_sleep.setter
    def post_sleep(self, hook: Callable[[], None] | None) -> None:
        self.transport.post_sleep = hook  # type: ignore[attr-defined]


# ── what the factory calls ──────────────────────────────────────────────────────────────


def describe(backend: str, robot_id: str | None = None) -> RobotManifest:
    """Static: the offline backends have their camera and their arm torque; the real host
    claims neither until connect() has seen them on the wire."""
    offline = backend in ("mock", "sim2d")
    return alohamini_manifest(
        backend,
        robot_id,
        camera=offline,
        cameras=("forward", "wrist_right") if offline else (),
        arm_torque=offline,
    )


def implementations() -> dict[str, Verb]:
    return alohamini_verbs(arms=True)


def conditions() -> dict[str, Precondition]:
    return alohamini_conditions()


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
) -> AlohaMiniAdapter:
    refuse_rest_pose("alohamini", rest_pose)
    # No backend here opens a camera by url: mock and sim2d carry their own, and the zmq host
    # publishes whichever it has. A second one is still refused rather than silently dropped.
    _url = one_camera_url(camera_url, spec=f"alohamini:{backend}")
    if backend == "mock":
        from quackd_alohamini.mock import AlohaMiniMock

        return AlohaMiniAdapter(AlohaMiniMock(model=parse_model(address)), robot_id=robot_id)
    if backend == "sim2d":
        from quackd_alohamini.sim2d import AlohaMiniSim2D

        return AlohaMiniAdapter(AlohaMiniSim2D(seed=seed or 0), robot_id=robot_id)
    if backend == "zmq":
        from quackd_alohamini.zmq_host import AlohaMiniZmq

        return AlohaMiniAdapter(
            AlohaMiniZmq(address=address, model=parse_model(address)), robot_id=robot_id
        )
    raise ValueError(f"unknown alohamini backend {backend!r}; choose one of {BACKENDS}")


__all__ = [
    "BACKENDS",
    "DEFAULT_ID",
    "HEARTBEAT_HZ",
    "MAX_VX",
    "MAX_VY",
    "MAX_WZ",
    "WATCHDOG_MS",
    "AlohaMiniAdapter",
    "alohamini_manifest",
    "conditions",
    "describe",
    "implementations",
    "make",
    "parse_model",
]


# What this adapter reads from upstream, for `quackd doctor`. Declared here rather than in a
# table in the core, because the list belongs to whoever wrote the adapter (ADR-0022). The
# import is deferred so that naming the upstream costs nothing until doctor asks.
def _upstream_rows() -> tuple[tuple[str, object, str, str], ...]:
    from quackd_alohamini import upstream_api

    return (
        (
            "alohamini",
            upstream_api,
            "docs/adapters/alohamini/README.md",
            "a robot (the zmq backend)",
        ),
    )


UPSTREAMS = _upstream_rows()
