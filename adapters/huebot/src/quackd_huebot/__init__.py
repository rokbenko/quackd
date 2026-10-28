"""The Huebot adapter: a small biped from Leif Rydenfalk, over the shim its maker is writing.

Huebot does not run the Microduck runtime, and its own API is closed. Its maker is writing a shim
for the robot that is to answer the same calls the Microduck's `robotd` answers, the same way, so
this adapter is the Microduck's, copied and renamed rather than imported, since the two robots may
diverge: the same protocol, the same version gate, the same hold on a walk the robot does not take,
and the same reliance on the robot's own deadman. What it leaves out is what Huebot's shim does not
answer yet, `robot.look`, so there is no `gaze` and no `watch`, and the Microduck's simulators,
whose model and gait are not Huebot's. It has no `grab` either, until its maker says what
`ground_pick` does on Huebot. Two backends: `mock`, the core's scripted body, and `jsonrpc`, the
shim, which nobody has run (docs/adapters/huebot/README.md).
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Collection, Sequence
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
from quackd_huebot.verbs import HUEBOT_VERBS, SKILLS_OF, huebot_conditions

__version__ = "0.28.0"
"""Kept in step with quackd's own version by scripts/set_version.py. It lives here
rather than being read from the core, because this file is all an adapter's sdist
contains."""

BACKENDS = ("mock", "jsonrpc")

DEADMAN_SEEN = False
"""Whether a Huebot has passed step 3 of docs/adapters/huebot/hardware-checklist.md, which is
where its legs are seen to stop once `robot.move` stops arriving, on a stand. Until one has, the
description of a real Huebot claims no deadman, as the Open Duck's bridge and the ToddlerBot's
daemon claim none they have not reported, and quackd relies on the shim's all the same, as the
deadman's UNVERIFIED ref in `upstream_api.py` says: nothing else stops a walk whose link died.
The mock is not a robot, and claims what every offline backend claims."""

_MOVE_DESCRIPTION = (
    "Walk with a velocity for a duration. Use small values; the robot is 25 cm tall."
)
_GO_TO_DESCRIPTION = (
    "Walk toward a detected target and stop at a distance. Closes the loop on the camera itself."
)
_SEARCH_SCAN_DESCRIPTION = "Rotate in steps, looking for a target. Returns where it was seen."
# The core's wording offers `kick, grab`, and Huebot has no `grab`. Its `then` parameter still
# says so, since a parameter's description is the core's and no manifest can change it.
_APPROACH_AND_DESCRIPTION = "walk_to a target, then run another verb (e.g. kick)."
BLURB = "a small biped robot (25 cm)"

_MAKER_PAGE = "ce-net.com/huebot, read 2026-10-20"
"""Where every figure below comes from: the maker's own page, on the day it was read."""

DATASHEET = Datasheet(
    height_m=Figure(value=0.25, confidence="official", source=_MAKER_PAGE),
    dof=Figure(
        value=15,
        confidence="official",
        source=_MAKER_PAGE,
        note="Feetech HD-1910 bus servos on one serial bus",
    ),
    manipulator="none",
    tethered=None,
    cannot=[
        "carry or push anything: its maker's page lists no arms",
        "climb or descend a step",
    ],
    notes=[
        "The speed caps are quackd's own clamp on the twists it sends, not a measured hardware "
        "maximum: nobody has published one for Huebot",
        "Its maker said on 2026-10-15 that it stands and steps, and that the walk's balance is "
        "still being tuned",
        "Its head turns on four servos, its maker wrote on 2026-10-15, and quackd turns none of "
        "them: robot.look is not on Huebot's side yet",
        "It has a speaker and a 13 by 8 LED face, its maker's page says. quack and say send "
        "robot.sound, which its maker has not named among the shim's calls, and quackd does not "
        "drive the face",
        "Its time-of-flight sensor is an 8 by 8 grid, which quackd does not read. The maker's "
        "page lists its battery as coming",
    ],
)


def offered_skill_verbs(skills: Collection[str] | None) -> list[str]:
    """The verbs that send `robot.do` a Huebot offers: every one while nothing has said which
    skills it has, which is the description `validate` reads and the mock, and on a connected
    robot each one whose every skill its shim listed (`SKILLS_OF`)."""
    return [
        verb
        for verb, needs in SKILLS_OF.items()
        if skills is None or all(skill in skills for skill in needs)
    ]


def huebot_manifest(
    backend: str,
    robot_id: str = "huebot",
    *,
    camera: bool = True,
    skills: Collection[str] | None = None,
) -> RobotManifest:
    """Huebot as data. Static: no connection needed (validate, announce, doctor).

    `camera` is what a description cannot settle on its own. The shim's socket carries no
    picture, so on `jsonrpc` a frame exists only when `--camera-url` names something serving
    one, and a frame has arrived from it. Advertising `observe` either way promises the pilot
    vision it may not have.

    `skills` is what the shim's `robot.subscribe` answer listed, on a connected robot, and None
    everywhere else. Each verb that sends `robot.do` is declared only when every skill it sends
    is among them, so a skill Huebot does not have is never a verb the pilot is offered.

    No lens goes in the limits: the maker publishes none, so a detector measures through a
    guess, says so, and `--fov-deg` gives it the real one."""
    skill_verbs = offered_skill_verbs(skills)
    core = [
        verb_spec(CORE["report_state"], core=True),
        verb_spec(CORE["stop"], core=True),
        verb_spec(HUEBOT_VERBS["say"], core=True),
        verb_spec(CORE["move"], core=True, description=_MOVE_DESCRIPTION),
    ]
    if camera:
        core += [
            verb_spec(CORE["observe"], core=True),
            verb_spec(CORE["go_to"], core=True, description=_GO_TO_DESCRIPTION),
            verb_spec(CORE["search_scan"], core=True, description=_SEARCH_SCAN_DESCRIPTION),
            verb_spec(CORE["approach_and"], core=True, description=_APPROACH_AND_DESCRIPTION),
        ]
    extensions = [
        verb_spec(v, core=False)
        for n, v in HUEBOT_VERBS.items()
        if n != "say" and (n not in SKILLS_OF or n in skill_verbs)
    ]
    sensors: list[Sensor] = ["tof", "microphone"]
    if camera:
        sensors.insert(0, "camera")
    # A precondition is read once, when the verb starts, and what stops a robot picked up
    # halfway through one is the transport's hold on `robot.move`.
    gated: dict[str, list[str]] = {
        "move": ["standing"],
        "kick": ["standing"],
        "sit": ["not_fallen"],
        "stand": ["not_fallen"],
    }
    if camera:
        gated |= {verb: ["standing"] for verb in ("go_to", "search_scan", "approach_and")}
    declared = {spec.name for spec in core + extensions}
    # The claim is about hardware, and no Huebot has shown its deadman yet (`DEADMAN_SEEN`).
    deadman = backend == "mock" or DEADMAN_SEEN
    preconditions = {verb: names for verb, names in gated.items() if verb in declared}
    return RobotManifest(
        id=robot_id,
        vendor="leif-rydenfalk",
        model="huebot",
        embodiment="biped",
        mobility="legged",
        intents=["twist", "skill", "sound"],
        sensors=sensors,
        verbs=core + extensions,
        preconditions=preconditions,
        safety_authority=SafetyAuthority(
            native="robotd_deadman" if deadman else "none", deadman=deadman, heartbeat_hz=2.0
        ),
        frame=Frame(reference="body"),
        limits={"max_vx": 0.15, "max_vy": 0.1, "max_wz": 0.75},
        backend=backend,
        blurb=BLURB,
        datasheet=DATASHEET,
    )


def _shim_line(shim: dict[str, Any]) -> str:
    """`API v41, build 0.16.1, revision d818af5`: the version, the build and the revision the
    shim answered `hello` with. The protocol lets the last two be null, so either can be
    missing."""
    build = shim.get("daemon_version") or "none named"
    revision = shim.get("revision") or "none"
    return f"API v{shim.get('api_version')}, build {build}, revision {revision}"


class HuebotAdapter:
    """A `RobotAdapter` over either Huebot transport."""

    name = "huebot"

    def __init__(self, transport: DuckTransport, *, robot_id: str = "huebot") -> None:
        self.transport = transport
        self.backend = transport.name
        self.robot_id = robot_id
        self.manifest: RobotManifest | None = None

    # ── protocol ────────────────────────────────────────────────────────────────────

    async def connect(self) -> RobotManifest:
        await self.transport.connect()
        # The mock always has a camera. A real Huebot has one only if a frame actually arrived:
        # a URL that was typed but never answered is not a camera, and advertising `observe` on
        # the strength of one is how a pilot gets told it can see.
        if hasattr(self.transport, "camera_working"):
            camera = bool(self.transport.camera_working)
        else:
            camera = getattr(self.transport, "camera_url", True) is not None
        # The skills the shim listed, which the verbs that send `robot.do` are offered for. The
        # mock has no list, and is described whole.
        listed = getattr(self.transport, "skills", None)
        skills = tuple(str(s) for s in listed) if isinstance(listed, Sequence) else None
        self.manifest = huebot_manifest(self.backend, self.robot_id, camera=camera, skills=skills)
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
        # Which shim answered, a row in doctor's health block, so what a robot runs is on the
        # screen at the checklist's go/no-go gate. Only a real robot says; the mock has none.
        if isinstance(shim := state.extras.get("shim"), dict):
            extras["shim"] = _shim_line(shim)
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
        """Why the last `stop` did not reach the robot, which `stop` in `verbs/core.py` reads off
        the object it was handed, this adapter."""
        return getattr(self.transport, "stop_error", None)

    @property
    def camera_error(self) -> str | None:
        """Why the camera has no frame to hand out just now, when the transport knows, which
        `observe`, `go_to` and `search_scan` read off this adapter when a frame does not come."""
        error = getattr(self.transport, "camera_error", None)
        return str(error) if error else None

    @property
    def head_fade_s(self) -> float:
        """How long `go_to` and `search_scan` wait after centring the head, which they read off
        this adapter: 0, on every backend. The Microduck's robotd glances around on its own and
        takes a second and a half to let go of a glance once a look arrives. Huebot is sent no
        look at all, since its shim does not answer `robot.look` yet, so there is nothing to
        wait out, and the look-ahead in both verbs goes on at once."""
        return 0.0

    @property
    def frames_upright(self) -> bool:
        """Whether every frame this robot hands out is upright and known fresh, which `watch`
        needs, and Huebot has no `watch`: no camera quackd reads on it says either, so False on
        `jsonrpc`, and the mock's scripted frames say so of themselves."""
        return getattr(self.transport, "frames_upright", False) is True

    @property
    def twist_shown_since(self) -> Callable[[float], bool] | None:
        """The transport's own `twist_shown_since` on `jsonrpc`, and None on the mock, which says
        nothing of the kind. `search_scan` reads it off the object it was handed, this adapter
        (`quackd.transport.base.twist_shown_since_of`), to count the turns it says it made by
        the ones the shim's state showed it took, since nothing answers a `robot.move`. A method
        here that answered for the mock would read as a body that took none of its turns."""
        shown = getattr(self.transport, "twist_shown_since", None)
        return shown if callable(shown) else None

    def subscribe(self, topic: str) -> AsyncIterator[dict[str, Any]]:
        return self.transport.subscribe(topic)

    def now(self) -> float:
        return self.transport.now()

    async def sleep(self, seconds: float) -> None:
        await self.transport.sleep(seconds)

    def preconditions(self) -> dict[str, Precondition]:
        return huebot_conditions()

    def implementations(self) -> dict[str, Verb]:
        return dict(HUEBOT_VERBS)


# ── what the factory calls (the same four names on every adapter package) ───────────────


def describe(backend: str, robot_id: str | None = None) -> RobotManifest:
    return huebot_manifest(backend, robot_id or "huebot")


def implementations() -> dict[str, Verb]:
    return dict(HUEBOT_VERBS)


def conditions() -> dict[str, Precondition]:
    return huebot_conditions()


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
) -> HuebotAdapter:
    refuse_rest_pose("huebot", rest_pose)
    url = one_camera_url(camera_url, spec=f"huebot:{backend}")
    # `token`, `seed` and `live` are accepted and unused, deliberately: the factory calls every
    # adapter's `make` with the same keywords. The protocol has no authentication at all, so the
    # shim is reached over ssh or on the robot itself, never through an open port.
    from quackd_huebot.transports.factory import make_transport

    transport = make_transport(backend, address=address, camera_url=url)
    return HuebotAdapter(transport, robot_id=robot_id or "huebot")


__all__ = [
    "BACKENDS",
    "HUEBOT_VERBS",
    "HuebotAdapter",
    "conditions",
    "describe",
    "huebot_conditions",
    "huebot_manifest",
    "implementations",
    "make",
    "offered_skill_verbs",
]


# What this adapter reads from upstream, for `quackd doctor`: the protocol Huebot's shim answers,
# read in the Microduck's source, beside what its maker says of Huebot. Declared here rather
# than in a table in the core (ADR-0022).
def _upstream_rows() -> tuple[tuple[str, object, str, str], ...]:
    from quackd_huebot import upstream_api as shim

    return (
        (
            "huebot",
            shim,
            "docs/adapters/huebot/README.md",
            "a Huebot's shim (the jsonrpc backend)",
        ),
    )


UPSTREAMS = _upstream_rows()
