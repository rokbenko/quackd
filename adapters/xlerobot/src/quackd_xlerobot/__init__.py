"""The XLeRobot adapter: a dual-arm mobile manipulator on a shopping cart.

The first body in quackd with both a mobile base and arms. Everything before it was one or
the other: `rosbridge` is a base, `lerobot` is an arm. So this manifest is the first to carry
`twist` alongside `joint` and `gripper`, and `move` here can strafe, because the base is three
omniwheels rather than two differential ones.

quackd never imports XLeRobot. It is not an installable package - its documented install is
copying files into an existing lerobot tree - so the adapter speaks its ZeroMQ host protocol
directly and depends on `pyzmq` alone (ADR-0026, `upstream_api.py`). Two backends: `mock`
(offline, scripted) and `zmq` (the real host behind `quackd[xlerobot]`, never run against a
robot by us).

What this robot has not got shapes the manifest more than what it has. There is no speaker and
no microphone in the bill of materials, so `say` is not declared. There is no battery data
link, so `battery_percent` is always None. There is no head axis mapping anywhere upstream, so
`gaze` is not declared and `search_scan` turns the whole cart. And a stock XLeRobot ships with
every camera commented out, so `observe`, `go_to`, `search_scan` and `approach_and` exist only
once a camera has actually been seen on the wire.
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
    Span,
    verb_spec,
)
from quackd.transport.base import Ack, DuckState, DuckTransport, HeartbeatError, Intent
from quackd.verbs.core import CORE
from quackd.verbs.registry import Precondition, Verb
from quackd_xlerobot import upstream_api as up
from quackd_xlerobot.verbs import JOINTS, xlerobot_conditions, xlerobot_verbs

__version__ = "0.16.1"
"""Kept in step with quackd's own version by scripts/set_version.py. It lives here
rather than being read from the core, because this file is all an adapter's sdist
contains."""

BACKENDS = ("mock", "zmq")
DEFAULT_ID = "xlerobot-01"

VARIANTS = ("omni3", "diff2", "mecanum")
"""Which base is bolted on. Only `omni3` can strafe.

Upstream ships the other two in sibling packages with different motor maps and action spaces,
and quackd cannot tell which one it is talking to, so this is a choice the owner makes."""
DEFAULT_VARIANT = "omni3"

# Upstream's own fast tier is 0.3 m/s and 90 deg/s (`upstream_api.SPEED_LEVELS`). quackd's
# schema caps vy at 0.2 and wz at 1.5 rad/s, and `limits` may only narrow, so the schema is
# the binding constraint on two of the three axes, not the robot.
MAX_VX = 0.3
MAX_VY = 0.2
MAX_WZ = 1.5

BLURB = (
    "a dual-arm mobile manipulator on an IKEA cart: two five-joint arms with grippers on a "
    "three-omniwheel base that can drive sideways as well as forward"
)

DATASHEET = Datasheet(
    mass_kg=Figure(value=12.0, confidence="official", source="the XLeRobot docs"),
    dof=Figure(
        value=17,
        confidence="official",
        source="the XLeRobot BOM",
        note="two five-joint arms with grippers, a two-axis head, three wheels",
    ),
    payload_kg=Figure(
        value=1.0,
        confidence="official",
        source="the XLeRobot docs",
        note="per arm; 0.6 to 1.0 kg depending on the pose, and 1.0 kg is the limit",
    ),
    reach_m=Figure(
        value=0.40,
        confidence="official",
        source="the XLeRobot docs and community measurements",
    ),
    workspace_height_m=Span(
        low=0.5,
        high=1.25,
        confidence="official",
        source="the XLeRobot docs",
        note="the torso does not lift, so the hands work in this band and nowhere else",
    ),
    endurance_min=Figure(
        value=600,
        confidence="official",
        source="the XLeRobot docs",
        note="a 288 Wh power station, ten hours or more",
    ),
    manipulator="gripper",
    arms=2,
    tethered=False,
    terrain="indoor_flat",
    not_rated=["stairs", "steps", "slopes", "thresholds"],
    cannot=[
        "lift more than 1 kg in one hand",
        "work lower than about 0.5 m or higher than about 1.25 m off the floor: the torso "
        "height is fixed",
        "move fast, catch, or manipulate a thing in one hand: no dynamic motion and no in-hand "
        "dexterity, in the maintainer's words",
        "go up or down a step: it is a 12 kg cart on three omniwheels",
    ],
    notes=[
        "Driving speed is not published; a community estimate for the LeKiwi base it is built "
        "on is 0.226 m/s",
        "Three colour cameras in the reference build and no depth sensor",
    ],
)
_MOVE_DESCRIPTION = (
    "Drive with a velocity for a duration: vx forward m/s, vy left m/s (this robot really "
    "can slide sideways without turning), wz rad/s (+ = left). The base's own driver moves; "
    "quackd re-sends the command while the verb runs. This is a 12 kg cart: upstream's own "
    "teleop opens at 0.1 m/s and 30 deg/s, so ask for 0.1 on a first run rather than "
    "taking the default."
)
_STALE_LIMIT_MS = float(up.WATCHDOG_TIMEOUT_MS.name)
"""Matches the host's own watchdog (`upstream_api.WATCHDOG_TIMEOUT_MS`): by the time an
observation is this old, the host has already stopped the base."""


def max_vy_for(variant: str) -> float:
    """Only the three-omniwheel base can strafe.

    The other two accept `y.vel` and do nothing with it, so declaring 0.0 makes quackd's own
    clamp zero the request and report `clamped=True` rather than promising a move that will
    not happen."""
    return MAX_VY if variant == DEFAULT_VARIANT else 0.0


def parse_variant(address: str | None) -> str:
    """`tcp://host:5555?variant=omni3`. Unknown or absent means the default base."""
    if not address:
        return DEFAULT_VARIANT
    wanted = parse_qs(urlsplit(address).query).get("variant", [DEFAULT_VARIANT])[0].strip().lower()
    return wanted if wanted in VARIANTS else DEFAULT_VARIANT


def parse_swap_colour(address: str | None) -> bool:
    """`tcp://host:5555?swap_colour=0`. The default is to swap, which is what upstream's
    own capture path produces, and it is UNVERIFIED because nobody has held a red ball in
    front of a real cart. It has to be reachable without editing quackd: the detector every
    camera verb steers by does not fail loudly on a swapped frame, it quietly stops finding
    things, and the Open Duck Mini's camera daemon has had the same switch since 0.5."""
    if not address:
        return True
    raw = parse_qs(urlsplit(address).query).get("swap_colour", ["1"])[0].strip().lower()
    return raw not in ("0", "false", "no", "off")


def xlerobot_manifest(
    backend: str,
    robot_id: str | None = None,
    *,
    camera: bool = False,
    camera_key: str | None = None,
    variant: str = DEFAULT_VARIANT,
    host: str | None = None,
) -> RobotManifest:
    """The cart as data. `camera` is what the first observation actually carried, not config."""
    own = xlerobot_verbs()
    verbs = [
        verb_spec(CORE["report_state"], core=True),
        verb_spec(CORE["stop"], core=True),
        verb_spec(CORE["move"], core=True, description=_MOVE_DESCRIPTION),
        verb_spec(own["move_joints"], core=False),
        verb_spec(own["gripper"], core=False),
    ]
    preconditions: dict[str, list[str]] = {
        "move": ["link_fresh"],
        "move_joints": ["link_fresh"],
        "gripper": ["link_fresh"],
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
            "go_to": ["link_fresh"],
            "search_scan": ["link_fresh"],
            "approach_and": ["link_fresh"],
        }
    sensors: list[Any] = ["joint_state"] + (["camera"] if camera else [])
    return RobotManifest(
        id=robot_id or DEFAULT_ID,
        vendor="vector-wangel",
        model="xlerobot",
        embodiment="wheeled",
        mobility="wheeled",
        intents=["twist", "joint", "gripper"],
        sensors=sensors,
        verbs=verbs,
        preconditions=preconditions,
        # The host's 500 ms watchdog is real, and it zeroes the wheels and nothing else: the
        # arms keep holding. That is not `torque_limit`, not `estop` and not `robotd_deadman`,
        # so `native` stays none and the scope goes in extras where it can be read.
        safety_authority=SafetyAuthority(native="none", deadman=True, heartbeat_hz=2.0),
        frame=Frame(
            reference="base",
            note=(
                "body frame: +x forward, +y left, wz rad/s (+ = left), converted to the "
                "robot's own deg/s on the wire. Arm joints are normalised -100..100 "
                "(grippers 0..100), NOT degrees. No odometry: velocities only, no pose."
            ),
        ),
        limits={
            "max_vx": MAX_VX,
            "max_vy": max_vy_for(variant),
            "max_wz": MAX_WZ,
            "joint_norm": 100.0,
        },
        backend=backend,
        blurb=BLURB,
        datasheet=DATASHEET,
        extras={
            "variant": variant,
            "joints": list(JOINTS),
            "camera_key": camera_key,
            "host": host,
            "deadman_scope": "base_only",
            "watchdog_ms": int(_STALE_LIMIT_MS),
            "speech": "none",
            "assumptions": [
                "holding is what quackd commanded, never what the robot felt: nothing on the "
                "wire reports grip force or contact",
                "camera frames are assumed BGR inside the host's JPEG and swapped to RGB",
                "bearings assume a 90 degree field of view, which is the simulator's camera; "
                "a real webcam is narrower, so distances and angles are approximate",
                "the head's two motors are on the wire but quackd never moves them: which one "
                "is yaw and which is pitch is stated nowhere upstream",
                "no odometry exists, so go_to closes the loop on the camera alone",
            ],
        },
    )


class XLerobotAdapter:
    """A `RobotAdapter` over the mock or the zmq backend."""

    name = "xlerobot"

    def __init__(self, transport: DuckTransport, *, robot_id: str | None = None) -> None:
        self.transport = transport
        self.backend = transport.name
        self.robot_id = robot_id or DEFAULT_ID
        self.manifest: RobotManifest | None = None

    async def connect(self) -> RobotManifest:
        await self.transport.connect()
        # a stock XLeRobot ships blind: every camera in its config is commented out. So the
        # manifest is built from what the first observation actually carried, and a cart with
        # no camera loses observe, go_to, search_scan and approach_and by REQUIREMENTS rather
        # than by any branching of ours.
        self.manifest = xlerobot_manifest(
            self.backend,
            self.robot_id,
            camera=bool(getattr(self.transport, "camera_available", False)),
            camera_key=getattr(self.transport, "camera_key", None),
            variant=str(getattr(self.transport, "variant", DEFAULT_VARIANT)),
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
            # no BMS in the bill of materials: the power station has no data link
            battery_percent=None,
            extras={
                "stale_ms": state.extras.get("stale_ms"),
                "holding": state.extras.get("holding"),
                "variant": state.extras.get("variant"),
            },
        )

    async def heartbeat(self) -> None:
        await self.transport.heartbeat()

    async def stop(self) -> None:
        await self.transport.stop()

    async def go_to_rest(self) -> RestResult:
        """quackd keeps no rest pose for this cart, so there is nothing to drive it to: the
        wheels stop where the host's watchdog stopped them and the arms hold their last angles."""
        return RestResult.none()

    def subscribe(self, topic: str) -> AsyncIterator[dict[str, Any]]:
        return self.transport.subscribe(topic)

    def now(self) -> float:
        return self.transport.now()

    async def sleep(self, seconds: float) -> None:
        await self.transport.sleep(seconds)

    def preconditions(self) -> dict[str, Precondition]:
        return xlerobot_conditions()

    def implementations(self) -> dict[str, Verb]:
        return xlerobot_verbs()

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
    """Static: the mock always has its camera; the real host claims none until connect() has
    seen one on the wire, because a stock cart ships with every camera commented out."""
    camera = backend == "mock"
    # never a camera_key on a manifest that declares no camera: a stock cart ships
    # blind, and naming a camera it has not got is a claim the same manifest denies.
    return xlerobot_manifest(
        backend, robot_id, camera=camera, camera_key="head" if camera else None
    )


def implementations() -> dict[str, Verb]:
    return xlerobot_verbs()


def conditions() -> dict[str, Precondition]:
    return xlerobot_conditions()


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
) -> XLerobotAdapter:
    refuse_rest_pose("xlerobot", rest_pose)
    # This cart's cameras come from the host's own config rather than from a url, so there is
    # no backend here to hand one to. It is still collapsed rather than ignored, so a second
    # --camera-url is refused by name instead of being dropped without a word.
    _url = one_camera_url(camera_url, spec=f"xlerobot:{backend}")
    if backend == "mock":
        from quackd_xlerobot.mock import XLerobotMock

        return XLerobotAdapter(XLerobotMock(variant=parse_variant(address)), robot_id=robot_id)
    if backend == "zmq":
        from quackd_xlerobot.zmq_host import XLerobotZmq

        return XLerobotAdapter(
            XLerobotZmq(
                address=address,
                variant=parse_variant(address),
                swap_colour=parse_swap_colour(address),
            ),
            robot_id=robot_id,
        )
    raise ValueError(f"unknown xlerobot backend {backend!r}; choose one of {BACKENDS}")


__all__ = [
    "BACKENDS",
    "DEFAULT_ID",
    "DEFAULT_VARIANT",
    "JOINTS",
    "MAX_VX",
    "MAX_VY",
    "MAX_WZ",
    "VARIANTS",
    "XLerobotAdapter",
    "conditions",
    "describe",
    "implementations",
    "make",
    "max_vy_for",
    "parse_swap_colour",
    "parse_variant",
    "xlerobot_manifest",
]


# What this adapter reads from upstream, for `quackd doctor`. Declared here rather than in a
# table in the core, because the list belongs to whoever wrote the adapter (ADR-0022). The
# import is deferred so that naming the upstream costs nothing until doctor asks.
def _upstream_rows() -> tuple[tuple[str, object, str, str], ...]:
    from quackd_xlerobot import upstream_api

    return (
        ("xlerobot", upstream_api, "docs/adapters/xlerobot/README.md", "a cart (the zmq backend)"),
    )


UPSTREAMS = _upstream_rows()
