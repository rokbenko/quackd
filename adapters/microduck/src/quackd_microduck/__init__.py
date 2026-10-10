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
from quackd.transport.base import (
    Ack,
    DuckState,
    DuckTransport,
    HeartbeatError,
    Intent,
    frame_taken_at_of,
)
from quackd.verbs.core import CORE
from quackd.verbs.registry import Precondition, Verb
from quackd_microduck.verbs import MICRODUCK_VERBS, microduck_conditions

__version__ = "0.17.1"
"""Kept in step with quackd's own version by scripts/set_version.py. It lives here
rather than being read from the core, because this file is all an adapter's sdist
contains."""

BACKENDS = ("sim2d", "mujoco", "mock", "jsonrpc", "websocket")
DEPRECATED_BACKENDS = {
    "websocket": (
        "microduck:websocket is deprecated and is removed in 0.19.0: upstream dropped the "
        "WebSocket agent gateway it was waiting for. Use --robot microduck:jsonrpc, with "
        "--camera-url http://127.0.0.1:8080/frame for mediad's camera"
    ),
}
"""A backend on its way out, to the one line `quackd run`, `doctor` and `serve-mcp` print when
they connect through it (`quackd.adapters.factory.deprecated_backend`). The WebSocket stub was
waiting for a gateway upstream's architecture.md designed, and by daemon-v0.16.1 upstream had
dropped it for a lane quackd will not drive over, so there is nothing for the stub to become.
The line names what replaces it: the robot's own socket, and mediad's `/frame` for a picture,
both through an ssh tunnel the Microduck page gives. RELEASING.md wants a removal announced a
minor ahead, so it stays a release with this line on it."""

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
        # It said the duck could get itself upright again, which was robotd's limp_fall. On
        # daemon-v0.16.1 that is off by default (upstream_api.FALLEN_IS_A_REPORT), and this
        # is the sheet a pilot judges a task against before anything moves.
        "It falls, and may not get up again by itself: on daemon-v0.16.1 robotd does nothing "
        "about a fall but report it",
    ],
)


def microduck_manifest(
    backend: str,
    robot_id: str = "microduck",
    *,
    camera: bool = True,
    camera_fov_deg: float | None = None,
) -> RobotManifest:
    """The Microduck as data. Static: no connection needed (validate, announce, doctor).

    `camera` is the one thing a description of the robot cannot settle on its own. robotd's
    socket carries no picture, so on the jsonrpc backend a frame exists only when `--camera-url`
    names something serving one. Advertising `observe` either way promises the pilot vision it
    may not have.

    `camera_fov_deg` is the lens that camera reported, in degrees across the frames it hands
    out, when it reported one. It goes in the limits, where `quackd run` takes the lens its
    detector measures through when no `--fov-deg` names one, and where `serve-mcp`, which has
    no `--fov-deg`, always takes it, as the LeRobot arm's camera does.
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
    # The 0.3 attachments, walk, kick and grab needing standing and sit, stand and gaze not
    # being fallen, and the three verbs that walk on what the camera sees, which had no
    # precondition at all until posture began failing closed. A precondition is read once, when
    # the verb starts, and what stops a duck picked up halfway through one is the transport's
    # hold on `robot.move`. The simulators never report `unknown`, so on them the one change is
    # that a seated or fallen duck is refused the three, where it used to be steered while its
    # legs ignored every twist.
    preconditions: dict[str, list[str]] = {
        "move": ["standing"],
        "kick": ["standing"],
        "grab": ["standing"],
        "sit": ["not_fallen"],
        "stand": ["not_fallen"],
        "gaze": ["not_fallen"],
    }
    if camera:
        preconditions |= {verb: ["standing"] for verb in ("go_to", "search_scan", "approach_and")}
    limits = {"max_vx": 0.3, "max_vy": 0.2, "max_wz": 1.5}
    if camera and camera_fov_deg is not None:
        limits["camera_fov_deg"] = float(camera_fov_deg)
    return RobotManifest(
        id=robot_id,
        vendor="pollen-robotics",
        model="microduck",
        embodiment="biped",
        mobility="legged",
        intents=["twist", "skill", "gaze", "sound", "pose"],
        sensors=sensors,
        verbs=core + extensions,
        preconditions=preconditions,
        safety_authority=SafetyAuthority(native="robotd_deadman", deadman=True, heartbeat_hz=2.0),
        frame=Frame(reference="body"),
        limits=limits,
        backend=backend,
        blurb=BLURB,
        datasheet=DATASHEET,
    )


def _lens(value: Any) -> float | None:
    """A field of view a camera reported, when it is one: a number of degrees between nothing and
    a half turn. Anything else is no lens, and the detector falls back on its own."""
    if isinstance(value, int | float) and not isinstance(value, bool) and 0 < value < 180:
        return float(value)
    return None


def _robotd_line(robotd: dict[str, Any]) -> str:
    """`0.16.1, API v41, revision d818af5`: the release, the version and the build that answered
    `hello`. robotd sends null for a release number its own build cannot parse as one, and for
    the revision of a build that did not come out of its CI, so either can be missing."""
    build = robotd.get("daemon_version") or "a build that names no release"
    revision = robotd.get("revision") or "none"
    return f"{build}, API v{robotd.get('api_version')}, revision {revision}"


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
        # The lens the camera reported, read once a frame has arrived, which is where the
        # transport learns it. The simulators report none: the detector knows their lens.
        self.manifest = microduck_manifest(
            self.backend,
            self.robot_id,
            camera=camera,
            camera_fov_deg=_lens(getattr(self.transport, "camera_fov_deg", None)),
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
        extras: dict[str, Any] = {"policy": state.policy, "posture": state.posture}
        # Which robotd answered, a row in doctor's health block, so the release a robot runs is
        # on the screen at the checklist's go/no-go gate whether or not it is the one quackd
        # read. Only a real robot says; the simulators and the mock have no such record.
        if isinstance(robotd := state.extras.get("robotd"), dict):
            extras["robotd"] = _robotd_line(robotd)
        return Health(ok=True, battery_percent=state.battery_percent, extras=extras)

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

    @property
    def camera_error(self) -> str | None:
        """Why the camera has no frame to hand out just now, when the transport knows. `observe`,
        `go_to` and `search_scan` read it off the object they were handed when a frame does not
        come, which is this adapter, for `stop_error`'s reason."""
        error = getattr(self.transport, "camera_error", None)
        return str(error) if error else None

    @property
    def head_fade_s(self) -> float:
        """How long `go_to` and `search_scan` wait after centring the head, which they read off
        the object they were handed, this adapter: on `jsonrpc` the time a robotd that glances
        around on its own takes to let go of a glance once a look arrives, and 0 on every
        backend whose head moves only when it is asked to."""
        fade = getattr(self.transport, "head_fade_s", 0.0)
        return float(fade) if isinstance(fade, int | float) and not isinstance(fade, bool) else 0.0

    def frame_taken_at(self) -> float | None:
        """When the frame `get_frame` hands out now was taken, at the earliest, on the clock `now()`
        reads, which `go_to` and `search_scan` read off the object they were handed, this
        adapter, to act only on a frame taken after a move of theirs had settled. On `jsonrpc`
        from mediad's `/frame`, and None from every camera that cannot say, which the simulators
        are among: they draw a frame when it is asked for, so every frame they hand out is new."""
        return frame_taken_at_of(self.transport)

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
    "DEPRECATED_BACKENDS",
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
