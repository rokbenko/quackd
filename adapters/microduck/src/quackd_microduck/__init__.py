"""The Microduck adapter: every Microduck transport, wrapped, plus the manifest.

`quackd/transport/*` is untouched and becomes the Microduck backend layer. This adapter
delegates every call to one of those transports and adds what 0.4 needs: a manifest, the
named preconditions the manifest references, and the Microduck's own verbs. Wrapping
rather than moving is how "zero behaviour change" is made mechanically true (ADR-0017).
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Sequence
from typing import Any

from PIL import Image

from quackd.adapters.base import RestResult, one_camera_url, refuse_rest_pose
from quackd.adapters.manifest import (
    Datasheet,
    Figure,
    Frame,
    Health,
    RobotManifest,
    SafetyAuthority,
    Sensor,
    verb_spec,
)
from quackd.transport.base import Ack, DuckState, DuckTransport, HeartbeatError, Intent
from quackd.verbs.core import CORE
from quackd.verbs.registry import Precondition, Verb
from quackd_microduck.verbs import MICRODUCK_VERBS, microduck_conditions

__version__ = "0.17.1"
"""Kept in step with quackd's own version by scripts/set_version.py. It lives here
rather than being read from the core, because this file is all an adapter's sdist
contains."""

BACKENDS = ("sim2d", "mujoco", "mock", "jsonrpc", "websocket")

# The 0.3 descriptions of the renamed verbs, so an old duck's tool schemas are byte-identical.
_MOVE_DESCRIPTION = (
    "Walk with a velocity for a duration. Use small values; the robot is 25 cm tall."
)
_GO_TO_DESCRIPTION = (
    "Walk toward a detected target and stop at a distance. Closes the loop on the camera itself."
)
_SEARCH_SCAN_DESCRIPTION = "Rotate in steps, looking for a target. Returns where it was seen."
_APPROACH_AND_DESCRIPTION = "walk_to a target, then run another verb (kick, grab)."
BLURB = "a small biped duck robot (25 cm, 800 g)"

DATASHEET = Datasheet(
    mass_kg=Figure(value=0.8, confidence="official", source="the Pollen Robotics README"),
    height_m=Figure(value=0.25, confidence="official", source="the Pollen Robotics README"),
    dof=Figure(
        value=15,
        confidence="official",
        source="the Pollen Robotics README",
        note="XL330 class servos, which is an estimate",
    ),
    manipulator="beak",
    tethered=False,
    terrain="indoor_flat",
    not_rated=["stairs", "steps", "slopes"],
    cannot=[
        "carry, hold or push anything: the beak scoops at the floor and nothing else",
        "climb or descend a step",
        "hold a heading for long without a landmark: the IMU has no magnetometer, so heading "
        "drifts",
    ],
    notes=[
        "The speed caps are a software clamp in robotd, not a measured hardware maximum",
        "The time-of-flight sensor is an 8 by 8 grid good from 0.12 to 3.54 m",
        "It falls, and it can get itself upright again",
    ],
)


def microduck_manifest(
    backend: str, robot_id: str = "microduck", *, camera: bool = True
) -> RobotManifest:
    """The Microduck as data. Static: no connection needed (validate, announce, doctor).

    `camera` is the one thing a description of the robot cannot settle on its own. Upstream has
    no socket-level camera method at all — the camera reaches clients through `mediad`'s WebRTC
    track — so on the jsonrpc backend a frame exists only when `--camera-url` names something
    serving one. Advertising `observe` either way promises the pilot vision it may not have.
    """
    core = [
        verb_spec(CORE["report_state"], core=True),
        verb_spec(CORE["stop"], core=True),
        verb_spec(MICRODUCK_VERBS["say"], core=True),
        verb_spec(CORE["move"], core=True, description=_MOVE_DESCRIPTION),
    ]
    if camera:
        core += [
            verb_spec(CORE["observe"], core=True),
            verb_spec(CORE["go_to"], core=True, description=_GO_TO_DESCRIPTION),
            verb_spec(CORE["search_scan"], core=True, description=_SEARCH_SCAN_DESCRIPTION),
            verb_spec(CORE["approach_and"], core=True, description=_APPROACH_AND_DESCRIPTION),
        ]
    extensions = [verb_spec(v, core=False) for n, v in MICRODUCK_VERBS.items() if n != "say"]
    sensors: list[Sensor] = ["battery", "odometry", "imu", "tof"]
    if camera:
        sensors.insert(0, "camera")
    return RobotManifest(
        id=robot_id,
        vendor="pollen-robotics",
        model="microduck",
        embodiment="biped",
        mobility="legged",
        intents=["twist", "skill", "gaze", "sound", "pose"],
        sensors=sensors,
        verbs=core + extensions,
        # exactly the 0.3 attachments: walk/kick/grab need standing, sit/stand/gaze not fallen
        preconditions={
            "move": ["standing"],
            "kick": ["standing"],
            "grab": ["standing"],
            "sit": ["not_fallen"],
            "stand": ["not_fallen"],
            "gaze": ["not_fallen"],
        },
        safety_authority=SafetyAuthority(native="robotd_deadman", deadman=True, heartbeat_hz=2.0),
        frame=Frame(reference="body"),
        limits={"max_vx": 0.3, "max_vy": 0.2, "max_wz": 1.5},
        backend=backend,
        blurb=BLURB,
        datasheet=DATASHEET,
    )


class MicroduckAdapter:
    """A `RobotAdapter` over any of the Microduck transports."""

    name = "microduck"

    def __init__(self, transport: DuckTransport, *, robot_id: str = "microduck") -> None:
        self.transport = transport
        self.backend = transport.name
        self.robot_id = robot_id
        self.manifest: RobotManifest | None = None

    # ── protocol ────────────────────────────────────────────────────────────────────

    async def connect(self) -> RobotManifest:
        await self.transport.connect()
        # sim and mock always have a camera. A real duck has one only if a frame actually
        # arrived: a URL that was typed but never answered is not a camera, and advertising
        # `observe` on the strength of one is how a pilot gets told it can see.
        if hasattr(self.transport, "camera_working"):
            camera = bool(self.transport.camera_working)
        else:
            camera = getattr(self.transport, "camera_url", True) is not None
        self.manifest = microduck_manifest(self.backend, self.robot_id, camera=camera)
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
            battery_percent=state.battery_percent,
            extras={"policy": state.policy, "posture": state.posture},
        )

    async def heartbeat(self) -> None:
        await self.transport.heartbeat()

    async def stop(self) -> None:
        await self.transport.stop()

    async def go_to_rest(self) -> RestResult:
        """A rest pose is a joint-angle map for an arm, and this body takes postures through
        its own verbs (`sit`, `stand`), so there is nothing here to drive it to."""
        return RestResult.none()

    @property
    def stop_error(self) -> str | None:
        """`jsonrpc` has recorded this since 0.6, but nothing could read it: `stop` in
        `verbs/core.py` looks for it on the object it was handed, which is this adapter."""
        return getattr(self.transport, "stop_error", None)

    def subscribe(self, topic: str) -> AsyncIterator[dict[str, Any]]:
        return self.transport.subscribe(topic)

    def now(self) -> float:
        return self.transport.now()

    async def sleep(self, seconds: float) -> None:
        await self.transport.sleep(seconds)

    def preconditions(self) -> dict[str, Precondition]:
        return microduck_conditions()

    def implementations(self) -> dict[str, Verb]:
        return dict(MICRODUCK_VERBS)

    # ── sim-only passthroughs the flock and the recorder use today ──────────────────

    @property
    def world(self) -> Any:
        return self.transport.world  # type: ignore[attr-defined]

    @property
    def clock(self) -> Any:
        return self.transport.clock  # type: ignore[attr-defined]

    @property
    def duck_index(self) -> int:
        return int(self.transport.duck_index)  # type: ignore[attr-defined]

    def add_tick_hook(self, hook: Callable[[Any], None]) -> None:
        self.transport.add_tick_hook(hook)  # type: ignore[attr-defined]

    @property
    def post_sleep(self) -> Callable[[], None] | None:
        return getattr(self.transport, "post_sleep", None)

    @post_sleep.setter
    def post_sleep(self, hook: Callable[[], None] | None) -> None:
        self.transport.post_sleep = hook  # type: ignore[attr-defined]


# ── what the factory calls (the same four names on every adapter package) ───────────────


def describe(backend: str, robot_id: str | None = None) -> RobotManifest:
    return microduck_manifest(backend, robot_id or "microduck")


def implementations() -> dict[str, Verb]:
    return dict(MICRODUCK_VERBS)


def conditions() -> dict[str, Precondition]:
    return microduck_conditions()


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
) -> MicroduckAdapter:
    refuse_rest_pose("microduck", rest_pose)
    url = one_camera_url(camera_url, spec=f"microduck:{backend}")
    # `token` is accepted and unused, deliberately: the factory calls every adapter's `make`
    # with the same four keywords, and this robot has nothing to authenticate to. `robotd`'s
    # socket has no auth at all — access is filesystem permissions on /run/robotd.sock — and
    # mediad's own note is that a pairing PIN which is 000000 on every robot "authenticates
    # nobody". Reach both over ssh rather than trusting the network.
    from quackd_microduck.transports.factory import make_transport

    transport = make_transport(backend, seed=seed, address=address, live=live, camera_url=url)
    return MicroduckAdapter(transport, robot_id=robot_id or "microduck")


__all__ = [
    "BACKENDS",
    "MICRODUCK_VERBS",
    "MicroduckAdapter",
    "conditions",
    "describe",
    "implementations",
    "make",
    "microduck_conditions",
    "microduck_manifest",
]


# What this adapter reads from upstream, for `quackd doctor`. Two rows: the robot's own IPC
# protocol, and the model and walking policy the physics backend fetches at run time and never
# ships. Declared here rather than in a table in the core (ADR-0022).
def _upstream_rows() -> tuple[tuple[str, object, str, str], ...]:
    from quackd_microduck import upstream_api as robotd
    from quackd_microduck.sim3d import upstream_api as rl

    return (
        (
            "microduck",
            robotd,
            "docs/adapters/microduck/README.md",
            "a robotd (the jsonrpc backend)",
        ),
        (
            "microduck_rl",
            rl,
            "docs/adr/0030-mujoco-physics-backend.md",
            "a robot: the model and the policies are fetched at run time and never shipped",
        ),
    )


UPSTREAMS = _upstream_rows()
