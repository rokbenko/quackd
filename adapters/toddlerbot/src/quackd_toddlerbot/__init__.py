"""The ToddlerBot adapter: a small humanoid, and a long list of things it will not be asked.

The first full humanoid in quackd. It has legs and arms on one body, which makes it the Open
Duck and the LeRobot arm at once, and it is the third robot whose robot side quackd ships,
because upstream has no network API of any kind: no socket, no daemon, no IPC, just a Python
library that opens serial ports in-process (ADR-0028).

Three capabilities are decided at connect rather than declared up front, because all three
depend on what the owner actually has:

- **a camera**, which is not in upstream's observation contract at all, so the daemon owns it;
- **a neck**, which every shipped build has but the teleop leader does not;
- **a walk policy**, which is an ONNX checkpoint from a wandb artifact that upstream does not
  publish and does not check in. Without one there is no locomotion at all, so `move`, `go_to`
  and `approach_and` do not exist rather than being gated off.

`say` is absent for good: the speaker plays audio and nothing at this pin synthesises speech.
A battery is absent too, so a battery abort can never fire here.

Backends: `mock` and `sim2d` run offline; `bridge` talks to the daemon quackd ships in
`bridge/toddlerbot/`, which is the only thing that ever touches a motor.
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
    verb_spec,
)
from quackd.transport.base import Ack, DuckState, DuckTransport, HeartbeatError, Intent
from quackd.verbs.core import CORE
from quackd.verbs.registry import Precondition, Verb
from quackd_toddlerbot.verbs import (
    MOTIONS,
    toddlerbot_conditions,
    toddlerbot_verbs,
)

CORE_OVERRIDES = frozenset({"search_scan"})
"""Core verbs this robot implements itself. Declared as core, run as ours."""


__version__ = "0.16.1"
"""Kept in step with quackd's own version by scripts/set_version.py. It lives here
rather than being read from the core, because this file is all an adapter's sdist
contains."""

BACKENDS = ("mock", "sim2d", "bridge")
DEFAULT_ID = "toddlerbot"

ROBOTS: tuple[str, ...] = (
    "toddlerbot_2xc",
    "toddlerbot_2xc_gripper",
    "toddlerbot_2xm",
    "toddlerbot_2xm_gripper",
)
"""The builds quackd will drive. Upstream also ships `teleop_leader`, which is the fourteen
motor arm a human holds, not a robot to pilot."""
DEFAULT_ROBOT = "toddlerbot_2xc"

# The walk policy's gin file at this pin says forward -0.2 to +0.3, which is asymmetric, and
# sideways +/- 0.1. quackd's schema caps vy at 0.2 and wz at 1.5, and `limits` may only
# narrow, so the robot binds on vx and vy and the schema binds on nothing here. The range
# actually enforced comes from the checkpoint, so the daemon reports it and connect() uses it.
MAX_VX = 0.2
MAX_VY = 0.1
MAX_WZ = 1.0

CONTROL_HZ = 50.0
HEARTBEAT_HZ = 5.0
"""The daemon owns the fifty hertz loop and its own deadman. quackd's heartbeat only has to
notice that the daemon stopped answering, so it runs an order of magnitude slower."""
STALE_LIMIT_MS = 1000.0


def _reported_motions(transport: object) -> tuple[str, ...]:
    """What the daemon said it loaded, or quackd's own list where nothing reports.

    `mock` and `sim2d` do not carry the attribute at all and get the full list. A
    `bridge` that reports an empty list gets an empty list, and `perform` goes with
    it."""
    reported = getattr(transport, "motions", None)
    return MOTIONS if reported is None else tuple(reported)


def _can_actually_walk(envelope: dict[str, float]) -> bool:
    """Whether a reported envelope leaves any axis with room to move on."""
    return any(abs(float(envelope.get(k, 0.0))) > 1e-3 for k in ("max_vx", "max_vy", "max_wz"))


def _limits(envelope: dict[str, float] | None = None) -> dict[str, float]:
    """The velocity envelope, from the checkpoint that is loaded where there is one.

    `limits` may only ever narrow relative to the schema, so a checkpoint trained
    wider than quackd's own caps is clamped to them rather than believed. With no
    daemon to ask, these are the walk gin file's numbers at the pin, which are what
    upstream trained its own checkpoint on and no promise about anybody else's."""
    e = envelope or {}
    return {
        "max_vx": min(float(e.get("max_vx", MAX_VX)), MAX_VX),
        "max_vy": min(float(e.get("max_vy", MAX_VY)), MAX_VY),
        "max_wz": min(float(e.get("max_wz", MAX_WZ)), MAX_WZ),
        "control_hz": CONTROL_HZ,
    }


BLURB = (
    "a small open source humanoid about 56 cm tall, with two arms, two legs, a two joint "
    "neck and thirty servos, which cannot get up by itself if it falls"
)

DATASHEET = Datasheet(
    mass_kg=Figure(value=3.4, confidence="official", source="arXiv:2502.00893"),
    height_m=Figure(value=0.56, confidence="official", source="arXiv:2502.00893"),
    dof=Figure(value=30, confidence="official", source="arXiv:2502.00893"),
    payload_kg=Figure(
        value=1.484,
        confidence="official",
        source="arXiv:2502.00893",
        note="the whole body with both arms, 40 percent of its own weight; per arm is not "
        "published",
    ),
    endurance_min=Figure(
        value=19, confidence="official", source="the ToddlerBot project site", note="about"
    ),
    manipulator="arms",
    arms=2,
    tethered=False,
    terrain="indoor_flat",
    not_rated=["stairs", "steps", "slopes"],
    cannot=[
        "get back on its feet after a fall: there is no recovery policy, so a fall ends the run "
        "and needs a human",
        "carry more than about 1.5 kg with both arms together, or an unknown weight in one",
        "keep going for more than about twenty minutes: past that the servos heat up and "
        "balance suffers",
    ],
    notes=[
        "It holds a thing by closing both arms on it, up to 0.27 by 0.24 by 0.31 m; a parallel "
        "gripper exists only on the gripper builds, which the daemon reports at connect",
        "Stereo fisheye cameras, an IMU, microphones and a speaker in the reference build",
    ],
)
_MOVE_DESCRIPTION = (
    "Walk with a velocity for a duration: vx forward m/s (backwards is slower than forwards "
    "on this robot), vy left m/s, wz rad/s (+ = left). The robot's own learned gait does the "
    "walking. It cannot get up if it falls, so ask for less than you think."
)


def toddlerbot_manifest(
    backend: str,
    robot_id: str | None = None,
    *,
    camera: bool = False,
    neck: bool = True,
    gripper: bool = False,
    walk: bool = False,
    robot: str = DEFAULT_ROBOT,
    deadman: bool = False,
    motors: int = 30,
    motions: tuple[str, ...] = MOTIONS,
    envelope: dict[str, float] | None = None,
) -> RobotManifest:
    """The robot as data. Every flag is what the daemon reported, not what a config claimed."""
    # A checkpoint whose envelope is zero on every axis is a checkpoint that cannot move
    # the robot: `limits` would clamp every velocity to nothing while `move`, `go_to` and
    # `approach_and` went on being offered and going on accepting. That is the same claim
    # without a body behind it that a missing checkpoint is, so it is treated the same way.
    if walk and envelope is not None and not _can_actually_walk(envelope):
        walk = False
    own = toddlerbot_verbs(neck=neck, gripper=gripper, motions=motions)
    verbs = [
        verb_spec(CORE["report_state"], core=True),
        verb_spec(CORE["stop"], core=True),
        # `search_scan` is quackd's own implementation of a core verb, not an extension
        # of this robot's: it is declared below as core, where the camera gates it, and
        # the registry takes the head-only implementation from `implementations()`.
        *[verb_spec(v, core=False) for n, v in own.items() if n not in CORE_OVERRIDES],
    ]
    preconditions: dict[str, list[str]] = {"stand": ["link_fresh", "calibrated"]}
    if motions:
        preconditions["perform"] = ["link_fresh", "calibrated", "not_fallen"]
    if neck:
        preconditions["look"] = ["link_fresh", "not_fallen"]
    if gripper:
        preconditions["grip"] = ["link_fresh", "calibrated"]
    if camera:
        verbs.insert(0, verb_spec(CORE["observe"], core=True))
    # Locomotion is not gated off when there is no checkpoint, it does not exist: `move`,
    # `go_to` and `approach_and` all need the twist intent, and there is no walk without one.
    if walk:
        verbs.append(verb_spec(CORE["move"], core=True, description=_MOVE_DESCRIPTION))
        preconditions["move"] = ["link_fresh", "calibrated", "not_fallen"]
        if camera:
            verbs += [
                verb_spec(CORE["go_to"], core=True),
                verb_spec(CORE["approach_and"], core=True),
            ]
            preconditions |= {
                "go_to": ["link_fresh", "calibrated", "not_fallen"],
                "approach_and": ["link_fresh", "calibrated", "not_fallen"],
            }
    # search_scan needs a camera and either twist or gaze. The neck is the gaze, and turning a
    # humanoid on the spot to look around is not something to do without a fall recovery.
    if camera and neck:
        verbs.append(verb_spec(CORE["search_scan"], core=True))
        preconditions["search_scan"] = ["link_fresh", "not_fallen"]

    intents: list[Any] = ["skill"]
    if neck:
        intents.append("gaze")
    if gripper:
        intents.append("gripper")
    if walk:
        intents.append("twist")
    sensors: list[Any] = ["imu", "joint_state"] + (["camera"] if camera else [])
    return RobotManifest(
        id=robot_id or DEFAULT_ID,
        vendor="hshi74",
        model=robot,
        embodiment="humanoid",
        mobility="legged" if walk else "none",
        intents=intents,
        sensors=sensors,
        verbs=verbs,
        preconditions=preconditions,
        # There is no watchdog, no timeout, no e-stop and no deadman anywhere upstream, and
        # silence on this robot means "hold the last target forever" rather than "stop". The
        # only deadman that can exist is the one quackd's own daemon runs, so `deadman` is
        # true wherever something is actually running one: the offline doubles emulate
        # it, and `:bridge` is false until the daemon has answered the handshake and
        # said otherwise, because before that there is nothing on the other end.
        safety_authority=SafetyAuthority(native="none", deadman=deadman, heartbeat_hz=HEARTBEAT_HZ),
        frame=Frame(
            reference="body",
            note=(
                "+x forward, joint space in radians. There is no odometry and no pose on "
                "hardware: the observation carries motor positions and an orientation, and "
                "nothing else. Orientation comes from an IMU quaternion, scalar first."
            ),
        ),
        limits=_limits(envelope),
        backend=backend,
        blurb=BLURB,
        datasheet=DATASHEET,
        extras={
            "robot": robot,
            "motors": motors,
            "motions": list(motions),
            "walk_policy": walk,
            "neck": neck,
            "gripper": gripper,
            "speech": "none",
            "no_recovery": (
                "there is no get-up policy for this body, so a fall ends the run and needs a human"
            ),
            "no_battery": "nothing reports a battery to Python, so a battery abort cannot fire",
            "deadman_scope": "the daemon slews to a safe pose and holds it; it never goes limp",
            "assumptions": [
                "a fall is detected from the gravity vector past a tilt threshold that nobody "
                "has calibrated against a real robot",
                "which neck motor is yaw and which is pitch is inferred from the motor names",
                "holding is what quackd commanded, never what the robot felt",
                "the safe pose is upstream's default pose, and whether it is safe to reach "
                "from a crawling or prone start is untested",
                "no odometry exists, so go_to closes the loop on the camera alone",
            ],
        },
    )


class ToddlerBotAdapter:
    """A `RobotAdapter` over the mock, sim2d or bridge backend."""

    name = "toddlerbot"

    def __init__(self, transport: DuckTransport, *, robot_id: str | None = None) -> None:
        self.transport = transport
        self.backend = transport.name
        self.robot_id = robot_id or DEFAULT_ID
        self.manifest: RobotManifest | None = None

    async def connect(self) -> RobotManifest:
        await self.transport.connect()
        # Everything below is what the daemon reported about the robot in front of it: which
        # build, whether a neck and grippers are on it, whether a camera answered, and whether
        # a walk checkpoint is staged. None of it is taken from configuration.
        self.manifest = toddlerbot_manifest(
            self.backend,
            self.robot_id,
            camera=bool(getattr(self.transport, "camera_available", False)),
            neck=bool(getattr(self.transport, "neck_available", True)),
            gripper=bool(getattr(self.transport, "gripper_available", False)),
            walk=bool(getattr(self.transport, "walk_available", False)),
            robot=str(getattr(self.transport, "robot_name", DEFAULT_ROBOT)),
            deadman=bool(getattr(self.transport, "deadman", False)),
            motors=int(getattr(self.transport, "motors", 30)),
            # An empty list means the daemon loaded none, which is a different thing
            # from a backend that does not report them at all. `or MOTIONS` treated
            # the two as the same and re-advertised five motions the robot refused.
            motions=_reported_motions(self.transport),
            envelope=getattr(self.transport, "walk_envelope", None),
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
            ok=not state.fallen,
            reason="the robot has fallen and needs a human" if state.fallen else None,
            # nothing reports a battery to Python on this robot
            battery_percent=None,
            extras={
                "posture": state.posture,
                "stale_ms": state.extras.get("stale_ms"),
                "loop_hz": state.extras.get("loop_hz"),
                "calibrated": state.extras.get("calibrated"),
                "deadman_tripped": state.extras.get("deadman_tripped"),
            },
        )

    async def heartbeat(self) -> None:
        await self.transport.heartbeat()

    async def stop(self) -> None:
        await self.transport.stop()

    async def go_to_rest(self) -> RestResult:
        """Nothing records a rest pose for this body: the only pose it goes to on its own is
        the daemon's safe pose on a deadman, and quackd never asks for that one."""
        return RestResult.none()

    def subscribe(self, topic: str) -> AsyncIterator[dict[str, Any]]:
        return self.transport.subscribe(topic)

    def now(self) -> float:
        return self.transport.now()

    async def sleep(self, seconds: float) -> None:
        await self.transport.sleep(seconds)

    def preconditions(self) -> dict[str, Precondition]:
        return toddlerbot_conditions()

    def implementations(self) -> dict[str, Verb]:
        return toddlerbot_verbs(neck=True, gripper=True)

    @property
    def mobility(self) -> str:
        return str(self.manifest.mobility) if self.manifest else "legged"

    @property
    def world(self) -> Any:
        """The simulated world, for the GIF recorder. None on the backends that have none."""
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
    """Static: the offline backends describe a fully built robot with a staged walk policy;
    the bridge claims nothing until the daemon has said what is actually there."""
    offline = backend in ("mock", "sim2d")
    return toddlerbot_manifest(
        backend,
        robot_id,
        camera=offline,
        neck=offline,
        gripper=False,
        walk=offline,
        deadman=offline,
    )


def implementations() -> dict[str, Verb]:
    return toddlerbot_verbs(neck=True, gripper=True)


def conditions() -> dict[str, Precondition]:
    return toddlerbot_conditions()


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
) -> ToddlerBotAdapter:
    refuse_rest_pose("toddlerbot", rest_pose)
    # The daemon owns the camera and reports it at connect, so no backend here has a url to
    # open. The value is still collapsed rather than ignored, so a second camera is refused
    # instead of being taken and dropped.
    _url = one_camera_url(camera_url, spec=f"toddlerbot:{backend}")
    if backend == "mock":
        from quackd_toddlerbot.mock import ToddlerBotMock

        return ToddlerBotAdapter(ToddlerBotMock(), robot_id=robot_id)
    if backend == "sim2d":
        from quackd_toddlerbot.sim2d import ToddlerBotSim2D

        return ToddlerBotAdapter(ToddlerBotSim2D(seed=seed or 0), robot_id=robot_id)
    if backend == "bridge":
        from quackd_toddlerbot.bridge import ToddlerBotBridge

        return ToddlerBotAdapter(ToddlerBotBridge(address=address, token=token), robot_id=robot_id)
    raise ValueError(f"unknown toddlerbot backend {backend!r}; choose one of {BACKENDS}")


__all__ = [
    "BACKENDS",
    "CONTROL_HZ",
    "DEFAULT_ID",
    "DEFAULT_ROBOT",
    "HEARTBEAT_HZ",
    "MAX_VX",
    "MAX_VY",
    "MAX_WZ",
    "ROBOTS",
    "STALE_LIMIT_MS",
    "ToddlerBotAdapter",
    "conditions",
    "describe",
    "implementations",
    "make",
    "toddlerbot_manifest",
]


# What this adapter reads from upstream, for `quackd doctor`. Declared here rather than in a
# table in the core, because the list belongs to whoever wrote the adapter (ADR-0022). The
# import is deferred so that naming the upstream costs nothing until doctor asks.
def _upstream_rows() -> tuple[tuple[str, object, str, str], ...]:
    from quackd_toddlerbot import upstream_api

    return (
        (
            "toddlerbot",
            upstream_api,
            "docs/adapters/toddlerbot/README.md",
            "a humanoid (the bridge)",
        ),
    )


UPSTREAMS = _upstream_rows()
