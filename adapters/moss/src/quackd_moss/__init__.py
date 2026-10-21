"""The MOSS adapter: a small tracked litter-picking rover with an arm and a bin on top.

MOSS is Laurent Genoud's (Show Robotics) open rover, mostly 3D printed: two tracks, an SO-101
derived arm with a parallel gripper widened to 90 mm, a RealSense nose camera, a wrist camera
and a 2D lidar, on a Jetson or a Raspberry Pi. Its software is `moss_dimos`, a set of blueprints
for Dimensional's dimOS. The maker's own status line says the rover drives and demonstrates pick
and drop motions, and that autonomous litter picking and a field test are not yet demonstrated.

quackd imports neither MOSS nor dimOS. Two backends:

- `mock`, offline, never kinder than the rover (`mock.py`).
- `cockpit`, the real rover: MOSS's own cockpit over HTTP for state, the pose, the battery,
  goals for its planner, the explorer and the soft stop, and quackd's own bridge on the rover
  (`bridge/moss/`) for what the cockpit cannot do: a deadman twist, frames that say when they
  were taken, the maker's arm cycle and the litter mission's stop (`cockpit.py`). Never run
  against a rover by us.

The manifest is narrowed at connect by what answered: the stack running (moss-nav, moss-explore
or moss-litter, anything else refused), whether the bridge answers, whether the arm's
coordinator answers, and on moss-litter whether the mission answers. `describe()` declares
every stack's verbs together, because a task is validated against the static manifest before
anything connects.

No core verb is overridden. The bridge shapes every twist on the rover, where the timing is
real: arcs are kept tight enough that the inner track never reverses, and a turn on the spot is
sent as the maker's own pulses, because this body cannot turn slowly in place.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Callable, Sequence
from typing import Any
from urllib.parse import parse_qs, urlsplit

from PIL import Image

from quackd.adapters.base import AdapterError, RestResult, camera_urls, refuse_rest_pose
from quackd.adapters.manifest import (
    Datasheet,
    Figure,
    Frame,
    Health,
    RobotManifest,
    SafetyAuthority,
    verb_spec,
)
from quackd.transport.base import (
    Ack,
    CameraFrame,
    DuckState,
    DuckTransport,
    HeartbeatError,
    Intent,
    frames_of,
)
from quackd.verbs.core import CORE
from quackd.verbs.registry import Precondition, Verb
from quackd_moss import upstream_api as up
from quackd_moss.verbs import (
    ARM_GIVE_UP_S,
    MAX_VX,
    MAX_VY,
    MAX_WZ,
    STOP_VERB_TIMEOUT_S,
    VERB_MARGIN_S,
    moss_conditions,
    moss_verbs,
)

__version__ = "0.21.0"
"""Kept in step with quackd's own version by scripts/set_version.py. It lives here
rather than being read from the core, because this file is all an adapter's sdist
contains."""

BACKENDS = ("mock", "cockpit")
DEFAULT_ID = "moss-01"

BASE_STACKS = (up.NAV_STACK, up.EXPLORE_STACK)
"""The stacks quackd drives the base on: the pad's channel is quackd's twist's channel."""
LITTER_STACK = up.LITTER_STACK
STACKS = (*BASE_STACKS, LITTER_STACK)
"""Every stack quackd connects to. moss-gamepad and moss-base are the pad and the bare base,
with no odometry and no cockpit state worth reading, and are refused by name."""

HEARTBEAT_HZ = 2.0
"""quackd's own heartbeat runs every half second, and that is what feeds the bridge's session
keepalive (`cockpit.py`)."""

BLURB = (
    "a small tracked litter-picking rover, with an arm and a parallel gripper that drop what "
    "they pick into a bin on top"
)

DATASHEET = Datasheet(
    mass_kg=Figure(
        value=4.2,
        confidence="official",
        source="the maker, in issue 2 of the MOSS repository",
        note=(
            "the V0.3 prototype complete, while the arm and gripper weigh 768 g on their own "
            "and the V0.6 hull has not been weighed"
        ),
    ),
    dof=Figure(
        value=6,
        confidence="official",
        source="the MOSS BOM",
        note="five ST3215 servos on the arm and a CF35-12 on the gripper, with the two tracks on "
        "top of that",
    ),
    manipulator="gripper",
    arms=1,
    tethered=False,
    terrain="indoor_flat",
    not_rated=["stairs", "steps", "kerbs", "slopes"],
    cannot=[
        "slide sideways: it runs on two tracks",
        "turn slowly on the spot: every turn in place runs both tracks at full power",
        "reach for an object: the jaws close at one fixed point in front of the rover, so the "
        "body has to bring the object there",
    ],
    notes=[
        "0.32 m/s at full power, measured by its maker",
        "A 3S battery: its BMS cuts at 10.2 V, and the maker's launcher refuses to start under "
        "10.8 V",
        "The jaws open to 90 mm",
        "The maker's own warning: a prototype with a lithium battery and no certified "
        "protection, to be put on blocks for its first run",
        "Built for streets and beaches, but neither autonomous picking nor a field test has been "
        "demonstrated",
    ],
)
"""Payload, reach, endurance and height are not published: the maker calls the battery bay
packaging space and not a claim about capacity or runtime (`upstream_api.NOT_A_RUNTIME_CLAIM`)."""

_MOVE_DESCRIPTION = (
    "Drive with a velocity for a duration: vx forward m/s (negative is back), wz rad/s "
    "(+ = left). There is no vy on tracks. A turn on the spot goes out as MOSS's own full power "
    "pulses and an arc is kept tight enough that the inner track never reverses, so the rover "
    "turns slower than asked and never faster."
)

_ASSUMPTIONS_ALWAYS = [
    "a stop takes the firmware's own ramp, which the bridge reads from the stack: with MOSS's "
    "launcher that is half a second from full power",
    "how far the maker's turn pulses turn the rover is unmeasured, so search_scan's angles are "
    "what it asked for, not what the rover turned",
    "the lidar odometry may lag a full power pivot, and drive_to's arrival is the planner's own "
    "word rather than a pose quackd checked",
    "the nose lens is uncalibrated unless the stack publishes its camera_info",
]
_ASSUMPTION_NO_ARM = (
    f"with no {up.ARM_STACK} run nothing holds the arm and nothing reports it, so quackd drives "
    "the "
    "base as if it were folded"
)
_ASSUMPTION_NO_CYCLE = (
    "the arm's coordinator answers and the arm is watched, but the bridge found none of the "
    f"maker's cycle to play ({up.ARM_PLAY.name}), so no arm verb is offered"
)
_ASSUMPTION_NO_BRIDGE = (
    "without quackd's bridge nothing on the rover halts what quackd started when it goes "
    "quiet: MOSS finishes a goal, at most 2 m, and explores on to its own radius"
)
_ASSUMPTION_LITTER = (
    "the litter mission acts on its own from the moment the stack starts, and every stop quackd "
    "sends ends it until the stack restarts, so collect_litter runs once per stack start"
)


def split_address(address: str | None) -> tuple[str, int, int]:
    """`http://host:8904?bridge=9876` -> (host, cockpit port, bridge port).

    The bridge's port rides in the query, as the XLeRobot's `?obs=` does, because it is on the
    same rover and nothing else in the address could name it. The token never goes here:
    addresses are printed and land in transcripts."""
    from quackd_moss.cockpit import BRIDGE_PORT, COCKPIT_PORT, DEFAULT_HOST

    if not address:
        return DEFAULT_HOST, COCKPIT_PORT, BRIDGE_PORT
    parts = urlsplit(address if "://" in address else f"http://{address}")
    host = parts.hostname or DEFAULT_HOST
    cockpit = parts.port or COCKPIT_PORT
    wanted = parse_qs(parts.query).get("bridge", [""])[0]
    return host, cockpit, int(wanted) if wanted.isdigit() else BRIDGE_PORT


def moss_manifest(
    backend: str,
    robot_id: str | None = None,
    *,
    stack: str | None = None,
    bridge: bool = True,
    arm: bool = True,
    mission: bool = True,
    camera: bool = True,
    cameras: Sequence[str] = (up.NOSE, up.WRIST),
    fov_deg: float | None = None,
    extras: dict[str, Any] | None = None,
    arm_seen: bool | None = None,
) -> RobotManifest:
    """The rover as data. `stack` None is every stack's verbs together, which is what
    `describe()` declares and `connect()` narrows from. `arm` is whether the arm verbs can
    run, the coordinator and the maker's cycle both, and `arm_seen` whether the coordinator
    answers at all, which is what lets a precondition see the arm (None: the same as arm)."""
    own = moss_verbs()
    stacks = STACKS if stack is None else (stack,)
    on_base = any(s in BASE_STACKS for s in stacks)
    planner = up.EXPLORE_STACK in stacks
    litter = LITTER_STACK in stacks
    twists = bridge and on_base
    arm_verbs = bridge and arm and on_base
    collect = bridge and mission and litter

    verbs = [
        verb_spec(CORE["report_state"], core=True),
        # a timeout that covers the transport's own wait, so a slow stop is never cut short
        verb_spec(CORE["stop"], core=True, timeout_s=STOP_VERB_TIMEOUT_S),
    ]
    preconditions: dict[str, list[str]] = {}
    if camera:
        verbs.insert(0, verb_spec(CORE["observe"], core=True))
    drive_gates = ["cockpit_fresh", "bridge_up", "battery_ok", "arm_folded", "arm_idle"]
    if twists:
        verbs.append(verb_spec(CORE["move"], core=True, description=_MOVE_DESCRIPTION))
        preconditions["move"] = list(drive_gates)
        if camera:
            verbs += [
                verb_spec(CORE["go_to"], core=True),
                verb_spec(CORE["search_scan"], core=True),
            ]
            preconditions["go_to"] = list(drive_gates)
            preconditions["search_scan"] = list(drive_gates)
    plan_gates = [
        "cockpit_fresh",
        "pose_fresh",
        "battery_ok",
        "not_stalled",
        "arm_folded",
        "arm_idle",
        "explore_stack",
    ]
    if planner:
        verbs += [verb_spec(own["drive_to"], core=False), verb_spec(own["explore"], core=False)]
        preconditions["drive_to"] = list(plan_gates)
        preconditions["explore"] = list(plan_gates)
    if arm_verbs:
        verbs += [
            verb_spec(own["pick_and_drop"], core=False),
            verb_spec(own["stow_arm"], core=False),
        ]
        arm_gates = ["cockpit_fresh", "bridge_up", "battery_ok", "arm_idle"]
        preconditions["pick_and_drop"] = list(arm_gates)
        preconditions["stow_arm"] = list(arm_gates)
    if twists and camera:
        # go_to, then whatever `then` names, the 37 s arm cycle included
        timeout = float(CORE["go_to"].timeout_s) + ARM_GIVE_UP_S + 2 * VERB_MARGIN_S
        verbs.append(verb_spec(CORE["approach_and"], core=True, timeout_s=timeout))
        preconditions["approach_and"] = list(drive_gates)
    if collect:
        verbs.append(verb_spec(own["collect_litter"], core=False))
        preconditions["collect_litter"] = [
            "cockpit_fresh",
            "pose_fresh",
            "battery_ok",
            "bridge_up",
            "mission_up",
            # the mission drives off at once, so the arm must be in its drive pose first
            "arm_folded",
        ]

    intents: list[Any] = []
    if twists:
        intents.append("twist")
    if planner:
        intents.append("pose")
    if planner or arm_verbs or collect:
        intents.append("skill")
    seen = bridge and (arm if arm_seen is None else arm_seen)
    sensors: list[Any] = (["camera"] if camera else []) + ["odometry", "battery"]
    if seen:
        sensors.append("joint_state")

    assumptions = list(_ASSUMPTIONS_ALWAYS)
    if not seen:
        assumptions.append(_ASSUMPTION_NO_ARM)
    elif not arm:
        assumptions.append(_ASSUMPTION_NO_CYCLE)
    if not bridge:
        assumptions.append(_ASSUMPTION_NO_BRIDGE)
    if litter:
        assumptions.append(_ASSUMPTION_LITTER)
    scope = (
        "the bridge's own twist deadman, and three missed keepalives after this session goes "
        "quiet, the arm cycle it started and whatever it started through the cockpit: a goal, "
        "an exploration, the litter mission"
        if bridge
        else "none: nothing on the rover halts a goal or an exploration quackd started"
    )
    limits: dict[str, float] = {"max_vx": MAX_VX, "max_vy": MAX_VY, "max_wz": MAX_WZ}
    if fov_deg is not None:
        limits["camera_fov_deg"] = float(fov_deg)
    return RobotManifest(
        id=robot_id or DEFAULT_ID,
        vendor="Show Robotics",
        model="MOSS V0.6",
        embodiment="wheeled",
        mobility="wheeled",
        intents=intents,
        sensors=sensors,
        verbs=verbs,
        preconditions=preconditions,
        safety_authority=SafetyAuthority(native="none", deadman=bridge, heartbeat_hz=HEARTBEAT_HZ),
        frame=Frame(
            reference="base",
            note=(
                "tracks: +x forward, +wz turns left (twist_to_tracks), and vy is dropped because "
                "a tracked base cannot slide. The pose is the lidar odometry's, in the frame the "
                "stack started in, and drive_to's world goals are in that frame."
            ),
        ),
        limits=limits,
        backend=backend,
        blurb=BLURB,
        datasheet=DATASHEET,
        extras={
            "blueprint": stack,
            "stacks": list(stacks),
            "cameras": list(cameras) if camera else [],
            "deadman_scope": scope,
            "speech": "none",
            "assumptions": assumptions,
            **(extras or {}),
        },
    )


class MossAdapter:
    """A `RobotAdapter` over the mock or the cockpit backend."""

    name = "moss"

    def __init__(self, transport: DuckTransport, *, robot_id: str | None = None) -> None:
        self.transport = transport
        self.backend = transport.name
        self.robot_id = robot_id or DEFAULT_ID
        self.manifest: RobotManifest | None = None

    async def connect(self) -> RobotManifest:
        await self.transport.connect()
        t = self.transport
        # what answered, never what a flag claimed
        self.manifest = moss_manifest(
            self.backend,
            self.robot_id,
            stack=str(getattr(t, "stack", None) or "") or None,
            bridge=bool(getattr(t, "bridge_up", False)),
            arm=bool(getattr(t, "arm_up", False)),
            mission=bool(getattr(t, "mission_up", False)),
            camera=bool(getattr(t, "camera_available", False)),
            cameras=tuple(getattr(t, "camera_keys", ()) or ()),
            fov_deg=getattr(t, "fov_deg", None),
            extras=dict(getattr(t, "manifest_extras", {}) or {}),
            arm_seen=getattr(t, "arm_seen", None),
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

    async def get_frames(self) -> list[CameraFrame]:
        return await frames_of(self.transport)

    def frame_taken_at(self) -> float | None:
        taken = getattr(self.transport, "frame_taken_at", None)
        return taken() if callable(taken) else None

    @property
    def frame_period_s(self) -> float | None:
        return getattr(self.transport, "frame_period_s", None)

    @property
    def camera_keys(self) -> list[str]:
        return list(getattr(self.transport, "camera_keys", ()) or ())

    @property
    def camera_error(self) -> str | None:
        return getattr(self.transport, "camera_error", None)

    @property
    def stop_error(self) -> str | None:
        """Why the last stop did not reach every surface, read by the core `stop` verb."""
        return getattr(self.transport, "stop_error", None)

    @property
    def close_note(self) -> str | None:
        return getattr(self.transport, "close_note", None)

    @property
    def arm_job_id(self) -> str | None:
        return getattr(self.transport, "arm_job_id", None)

    async def send_intent(self, intent: Intent) -> Ack:
        return await self.transport.send_intent(intent)

    async def health(self) -> Health:
        """The heartbeat's own checks. A probe such as `quackd doctor` holds a session of its own
        on the bridge while it is connected, and a session that started nothing halts nothing
        when it ends or expires, on moss-litter as anywhere."""
        try:
            await self.transport.heartbeat()
        except HeartbeatError as e:
            return Health(ok=False, reason=str(e))
        state = await self.transport.get_state()
        return Health(
            ok=True,
            battery_percent=state.battery_percent,
            extras={
                "stack": state.extras.get("stack"),
                "battery_v": state.extras.get("battery_v"),
                "telemetry_age_s": state.extras.get("telemetry_age_s"),
                "bridge_up": state.extras.get("bridge_up"),
            },
        )

    async def heartbeat(self) -> None:
        await self.transport.heartbeat()

    async def stop(self) -> None:
        await self.transport.stop()

    async def go_to_rest(self) -> RestResult:
        """Not a body quackd parks: make() refuses a rest pose for it. The arm's drive pose is
        reached through `stow_arm`, a verb a person confirms, never by a teardown."""
        return RestResult.none()

    def subscribe(self, topic: str) -> AsyncIterator[dict[str, Any]]:
        return self.transport.subscribe(topic)

    def now(self) -> float:
        return self.transport.now()

    async def sleep(self, seconds: float) -> None:
        await self.transport.sleep(seconds)

    def preconditions(self) -> dict[str, Precondition]:
        return moss_conditions()

    def implementations(self) -> dict[str, Verb]:
        return moss_verbs()

    @property
    def mobility(self) -> str:
        return "wheeled"

    @property
    def world(self) -> Any:
        return getattr(self.transport, "world", None)

    @property
    def post_sleep(self) -> Callable[[], None] | None:
        return getattr(self.transport, "post_sleep", None)

    @post_sleep.setter
    def post_sleep(self, hook: Callable[[], None] | None) -> None:
        self.transport.post_sleep = hook  # type: ignore[attr-defined]


# ── what the factory calls ──────────────────────────────────────────────────────────────


def describe(backend: str, robot_id: str | None = None) -> RobotManifest:
    """Static: every stack's verbs together, the bridge, the arm and the mission answering.
    `connect()` narrows it to what is really there."""
    return moss_manifest(backend, robot_id)


def implementations() -> dict[str, Verb]:
    return moss_verbs()


def conditions() -> dict[str, Precondition]:
    return moss_conditions()


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
) -> MossAdapter:
    refuse_rest_pose("moss", rest_pose)
    if camera_urls(camera_url):
        raise AdapterError(
            f"moss:{backend} takes no --camera-url: the nose and wrist cameras come from the "
            "rover itself, through quackd's bridge or the cockpit's own stream"
        )
    if backend == "mock":
        from quackd_moss.mock import MossMock

        return MossAdapter(MossMock.from_address(address), robot_id=robot_id)
    if backend == "cockpit":
        from quackd_moss.cockpit import TOKEN_ENV, MossCockpit

        host, cockpit_port, bridge_port = split_address(address)
        return MossAdapter(
            MossCockpit(
                host=host,
                cockpit_port=cockpit_port,
                bridge_port=bridge_port,
                token=token or os.environ.get(TOKEN_ENV) or None,
            ),
            robot_id=robot_id,
        )
    raise ValueError(f"unknown moss backend {backend!r}: choose one of {BACKENDS}")


__all__ = [
    "BACKENDS",
    "BASE_STACKS",
    "DATASHEET",
    "DEFAULT_ID",
    "HEARTBEAT_HZ",
    "LITTER_STACK",
    "STACKS",
    "MossAdapter",
    "conditions",
    "describe",
    "implementations",
    "make",
    "moss_manifest",
    "split_address",
]


# What this adapter reads from upstream, for `quackd doctor`. Declared here rather than in a
# table in the core, because the list belongs to whoever wrote the adapter (ADR-0022). The
# import is deferred so that naming the upstream costs nothing until doctor asks.
def _upstream_rows() -> tuple[tuple[str, object, str, str], ...]:
    from quackd_moss import upstream_api

    return (
        (
            "moss",
            upstream_api,
            "docs/adapters/moss/README.md",
            "a rover (the cockpit backend and quackd's bridge)",
        ),
    )


UPSTREAMS = _upstream_rows()
