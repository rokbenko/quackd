"""One robot of the Microduck's family, as data (ADR-0075).

The verbs in `verbs.py` and the transport in `transports/jsonrpc_unix.py` are written once, for
every robot that answers `robotd`'s JSON-RPC. A `Body` is what is different: its description, what
it offers, what it is called in what quackd says, and where to find what answers for it. `MICRODUCK`
is here, and imports no verbs and no transport, so that a body in another package can be declared
from this module alone (`quackd_huebot.body`).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Literal

from quackd.adapters.manifest import Datasheet, Figure, Sensor
from quackd_microduck import upstream_api as up

SIT_TOGGLE = "sit_toggle"
"""The skill `sit` and `stand` send through `robot.do`, by the name robotd answers to."""

KICK_SKILLS: dict[str, Literal["kick_left", "kick_right"]] = {
    "left": "kick_left",
    "right": "kick_right",
}
"""The skill `kick` sends through `robot.do` for each leg."""


@dataclass(frozen=True)
class Words:
    """How this body and what answers for it are named in what quackd says.

    Lines a pilot acts on and lines a person reads at a go or no-go gate, so each is said in full
    and none is left to a substitution that happens to read well for one body. The first group is
    nouns the verbs and the transport build their sentences with, and the rest are the sentences
    that differ whole."""

    noun: str
    """The body in a sentence: `the duck`, `Huebot`."""
    it: str
    """The body inside a bracket that already named it: `the duck`, `it`."""
    peer: str
    """What answers on the wire: `robotd`, `the shim`."""
    peer_s: str
    """Its possessive: `robotd's`, `the shim's`."""
    named_peer: str
    """What answers, on its own in a sentence: `robotd`, `Huebot's shim`."""
    this_peer: str
    """The one on this connection: `this robotd`, `this shim`."""
    skew_subject: str
    """Who a warning about a version names: `this robotd`, `Huebot's shim`."""
    a_peer: str
    """Any of them: `a robotd`, `a peer`."""
    build: str
    """What `hello`'s `daemon_version` names: `daemon`, `build`."""
    limp: str
    """Why posture is unknown while the gains are dropped."""
    forward: str
    """What to do about a unix socket on Windows."""
    floor_advice: str
    """What to do about a peer older than the floor, with `{floor}`, `{release}`, `{version}`."""
    newer_advice: str
    """What to do about a newer one, with `{version}`."""
    older_advice: str
    """What else to say of an older one above the floor, with `{version}`, or nothing."""
    no_hello: str
    """What a socket with no `hello` is not, and where to point `--address` instead."""
    no_subscribe: str
    """What follows `so it is not robotd` of a socket with `hello` and no `robot.subscribe`."""
    same_version: str
    """What a call refused by name means from a peer that speaks the version quackd read."""
    other_client: str
    """Who else sends a `robot.move`, as a clause, or nothing when nobody has said."""
    zero_twist: str
    """What else makes the state report a zero twist in place of the one sent, with nothing in
    the state to say so, as a clause, or nothing when nobody has said."""
    unhealthy: str
    """What goes before the reason a health poll gave."""
    no_stop: str
    """What stops the body once a `stop` could not be delivered."""
    sit_unknown: str
    """Why `sit` and `stand` will not guess a posture, after `posture is unknown`."""
    ask: str
    """What `stand_up` will not do when it refuses: `ask the policy to drive it`."""
    asked: str
    """What `stand_up` did, at the head of every line after its request."""
    no_pickup: str
    """Why `stand_up` refuses a robot that does not report being held."""
    fall: str
    """What `stand_up` says of a body in its own answer to a fall, with `{fall}`, `{waited}`."""
    down: str
    """What `stand_up` says of a body still down, with `{waited}`."""
    kick_text: str
    """`kick`'s description."""
    kick_done: str
    """What `kick` having finished means."""
    stand_up_text: str
    """`stand_up`'s description."""


def _always(_backend: str) -> bool:
    return True


@dataclass(frozen=True, eq=False)
class Body:
    """What one robot of this family declares. Compared and hashed by identity, since two bodies
    are two robots whatever they hold."""

    name: str
    """Its adapter's name, and the id a robot with no other takes."""
    vendor: str
    model: str
    """What the manifest and a passport call this robot."""
    blurb: str
    datasheet: Datasheet
    backends: tuple[str, ...]
    intents: tuple[Literal["twist", "skill", "gaze", "sound", "pose"], ...]
    """What the transport may send this body. `look` is refused without `gaze`, and no `pose` is
    sent without `pose`."""
    sensors: tuple[Sensor, ...]
    """What it reads, without the camera, which goes first when a frame arrived."""
    limits: Mapping[str, float]
    verbs: frozenset[str]
    """The family's verbs it has, which `verbs.verbs_for` lists in the family's order."""
    core_texts: Mapping[str, str]
    """The descriptions of `move`, `go_to`, `search_scan` and `approach_and` on this body."""
    words: Words
    not_standing: Mapping[str, str]
    """What each label that neither stands nor seats it means, in this body's words."""
    health_key: str
    """The key `hello`'s record goes under in `extras` and in `health()`: `robotd`, `shim`."""
    health_line: Callable[[dict[str, Any]], str]
    """That record as one row of `doctor`'s health block."""
    assumptions: tuple[str, ...]
    """What quackd stands in for on this robot, whatever the camera."""
    default_address: str | None = None
    """Where what answers for it listens, or None when nothing says."""
    no_address: str = ""
    """Why it is refused with no `--address`, when it has no default."""
    look_refused: str = ""
    """Why a `look` is refused, on a body with no `gaze`."""
    skill_verbs: Mapping[str, tuple[str, ...]] | None = None
    """Each verb that sends `robot.do` and every skill it can send, when the robot lists which
    skills it has and a verb is offered only for those. None is a robot whose verbs are not
    gated on any list."""
    claims_deadman: Callable[[str], bool] = _always
    """Whether a description of this backend claims the robot's own deadman."""
    withheld: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))
    """Each verb of the family it does not have, and why, for `extras.not_offered`."""
    places_backends: frozenset[str] = frozenset()
    """The backends that say where it is before anything connects."""
    map_backends: tuple[str, ...] = ()
    """The backends that reach its own `mapd` (`--map-address`)."""
    room_backends: tuple[str, ...] = ()
    """The backends a room (`--room`) is laid out in."""
    mediad: bool = False
    """Whether it runs `mediad`, so that a `/frame` URL is mediad's picture and `webrtc://` its
    track. On any other body a URL of that shape is somebody's snapshot server, whose frames say
    nothing of when they were taken or which way up they are."""
    reads_the_ball: bool = False
    """Whether `kick` reports how far the ball moved, which the robot's state has to say."""

    @property
    def looks(self) -> bool:
        return "gaze" in self.intents

    @property
    def poses(self) -> bool:
        return "pose" in self.intents

    @property
    def odometry(self) -> bool:
        return "jsonrpc" in self.places_backends

    @property
    def mapd(self) -> bool:
        return "jsonrpc" in self.map_backends


DEFAULT_ADDRESS = f"unix://{up.SOCKET_ROBOTD.name}"
"""Where robotd listens unless it was told otherwise (`transports.jsonrpc_unix.default_address`)."""

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
        # It said the caps were a software clamp in robotd, which clamps no twist at any release
        # quackd has read (upstream_api.NO_TWIST_CLAMP). They are this manifest's own `limits`,
        # which quackd.verbs.core holds every twist a verb sends to (speed_limits), and which
        # `move` refuses to be asked past.
        "The speed caps are quackd's own clamp on the twists it sends, not a measured hardware "
        "maximum: robotd clamps no twist",
        "The time-of-flight sensor is an 8 by 8 grid good from 0.12 to 3.54 m",
        # It said the duck could get itself upright again, which was robotd's limp_fall. On
        # daemon-v0.16.1 that is off by default (upstream_api.FALLEN_IS_A_REPORT). From
        # daemon-v0.16.2 it is on again, and robotd hands a duck it catches falling back to a
        # policy upstream says gets it up, which nobody has seen under quackd. It catches none
        # during a skill, a ground pick or a sit, so none during quackd's kick or grab
        # (upstream_api.LIMP_FALL). This is the sheet a pilot judges a task against before
        # anything moves, so it says what daemon-v0.16.1 and daemon-v0.16.2 onwards do, and
        # promises no get-up. Two notes, since a note holds 300 characters.
        "It falls, and may not get up again by itself. By default, robotd on daemon-v0.16.1 "
        "does nothing about a fall but report it",
        "From daemon-v0.16.2, by default, robotd goes limp in a fall it predicts while the duck "
        "walks or stands, though not during a kick, a grab or a sit, poses the duck and hands it "
        "back to its walking policy, which upstream says gets it up",
    ],
)

PLACES_BACKENDS = frozenset({"jsonrpc", "mock"})
"""The backends that say where the duck is before anything connects: robotd's contact odometry
(`upstream_api.STATE_ODOM`) and the mock's scripted pose. A simulator says where the duck is only
in a room it was given (`ROOM_BACKENDS`)."""

MAP_BACKENDS = ("jsonrpc",)
"""The backends that reach the duck's own `mapd` (`--map-address`): a real duck, beside its
robotd. A simulator keeps its own map of the room it was given (`ROOM_BACKENDS`)."""

ROOM_BACKENDS = ("sim2d", "mujoco")
"""The backends a room (`--room`) is laid out in. A simulator given one keeps a map of it the way
the duck's own `mapd` keeps one (`transports.mapd.sim_map_params`), and offers the place verbs. A
real duck's room is the one it stands in, and the mock scripts its own."""


def _robotd_line(robotd: dict[str, Any]) -> str:
    """`0.16.1, API v41, revision d818af5`: the release, the version and the build that answered
    `hello`. robotd sends null for a release number its own build cannot parse as one, and for
    the revision of a build that did not come out of its CI, so either can be missing."""
    build = robotd.get("daemon_version") or "a build that names no release"
    revision = robotd.get("revision") or "none"
    return f"{build}, API v{robotd.get('api_version')}, revision {revision}"


MOVE_TEXT = "Walk with a velocity for a duration. Use small values; the robot is 25 cm tall."
"""The 0.3 description of `move`, so an old duck's tool schemas are byte-identical."""

GO_TO_TEXT = (
    "Walk toward a detected target and stop at a distance. Closes the loop on the camera itself."
)
"""The 0.3 description of `go_to`."""

SEARCH_SCAN_TEXT = "Rotate in steps, looking for a target. Returns where it was seen."
"""The 0.3 description of `search_scan`."""

MICRODUCK = Body(
    name="microduck",
    vendor="pollen-robotics",
    model="microduck",
    blurb=BLURB,
    datasheet=DATASHEET,
    backends=("sim2d", "mujoco", "mock", "jsonrpc"),
    intents=("twist", "skill", "gaze", "sound", "pose"),
    sensors=("battery", "odometry", "imu", "tof"),
    # quackd's own clamp, robotd's being none (`NO_TWIST_CLAMP`). The turn is the one upstream's
    # walking task was trained on (sim3d's `TRAINING_TWIST`) and its pad asks for from
    # daemon-v0.16.2, where v8's velstand follows it well: past that is asking, not getting.
    limits=MappingProxyType({"max_vx": 0.3, "max_vy": 0.2, "max_wz": 1.0}),
    verbs=frozenset(
        {
            "sit",
            "stand",
            "kick",
            "grab",
            "stand_up",
            "quack",
            "gaze",
            "say",
            "watch",
            "explore",
            "find",
        }
    ),
    core_texts=MappingProxyType(
        {
            "move": MOVE_TEXT,
            "go_to": GO_TO_TEXT,
            "search_scan": SEARCH_SCAN_TEXT,
            "approach_and": "walk_to a target, then run another verb (kick, grab).",
        }
    ),
    words=Words(
        noun="the duck",
        it="the duck",
        peer="robotd",
        peer_s="robotd's",
        named_peer="robotd",
        this_peer="this robotd",
        skew_subject="this robotd",
        a_peer="a robotd",
        build="daemon",
        limp="robotd has dropped the gains so the duck yields (safety.limp)",
        forward=(
            "forward the robot's socket with `ssh -L 9870:/run/robotd.sock <robot>` and use "
            "--address tcp://127.0.0.1:9870"
        ),
        floor_advice=(
            "Update the robot to a release on API v{floor} or later: quackd read {release}, on "
            "API v{version}"
        ),
        newer_advice=(
            "Before it walks, read the robot's own duck-ipc-proto version notes, above "
            "API_VERSION in its lib.rs, for anything touching the calls quackd makes."
        ),
        older_advice="",
        no_hello=f"robotd: point --address at robotd's socket, {up.SOCKET_ROBOTD.name}",
        no_subscribe=(
            f": other daemons on the robot answer {up.HELLO.name} too, updaterd at "
            f"{up.SOCKET_UPDATERD.name} among them. Point --address at robotd's socket, "
            f"{up.SOCKET_ROBOTD.name}"
        ),
        same_version=(
            "so this robot was built from a duck-ipc-proto quackd has not read: upstream does "
            "not promise one version number the same calls"
        ),
        other_client=(
            ", as padd does at least ten times a second while a gamepad is connected, a stop "
            "while its sticks are still"
        ),
        # robotd puts an emotion's zero twist in place of the one it holds on every tick the
        # emotion plays, and no field of robot.state says one is playing (`up.MOVE_ECHO`)
        zero_twist=(
            ", or an emotion was playing, which holds robotd's twist at zero with nothing in "
            "robot.state to say so (any client can start one with robot.do, as a stock pad's Y "
            "and bumpers do, and robotd logs 'emotion started' as one begins)"
        ),
        unhealthy="robotd unhealthy",
        no_stop=(
            "The robot's own deadman zeroes velocity when robot.move stops arriving, which is "
            "what stops it now."
        ),
        sit_unknown=(
            "upstream has one sit_toggle rather than a sit and a stand, so quackd would be "
            "guessing which way it goes. Use the gamepad, or a backend that reports posture."
        ),
        ask="ask the policy to drive it",
        asked="asked the policy to run",
        no_pickup=(
            "cannot stand_up: this robot does not report whether somebody is holding the duck "
            "(no safety.picked_up), so quackd will not ask the policy to drive legs that may be "
            "in somebody's hands. Stand it up by hand"
        ),
        fall=(
            "asked the policy to run, but robotd is still answering the fall itself ({fall}). "
            "It hands the duck back to the policy once it has posed it, so run stand_up again in "
            "a moment to see whether the duck is up"
        ),
        # It used to say an onboard recovery had not finished, which sent the pilot to wait for
        # one. robotd runs none on daemon-v0.16.1 by default, and from daemon-v0.16.2 a duck it
        # caught falling is back with the policy once it has posed it (`_answering_a_fall`), which
        # upstream says gets it up and nobody has seen do so under quackd, so one still down may
        # need a hand.
        down=(
            "asked the policy to run, and the duck is still down {waited} s later: robotd does "
            "not promise to get a fallen duck up, so it may need a hand"
        ),
        kick_text=(
            "Kick forward with one leg. Only connects if the ball is < 0.3 m away and roughly "
            "ahead."
        ),
        kick_done="the kick animation finished; the result says whether the ball moved.",
        stand_up_text=(
            "Ask the robot's policy to get a fallen duck up. It may not manage, and the result "
            "says whether the duck is upright, or that the robot is still answering the fall "
            "itself."
        ),
    ),
    not_standing=up.NOT_STANDING_LABELS,
    health_key="robotd",
    health_line=_robotd_line,
    assumptions=(up.STAND_UP_RPC.name,),
    default_address=DEFAULT_ADDRESS,
    places_backends=PLACES_BACKENDS,
    map_backends=MAP_BACKENDS,
    room_backends=ROOM_BACKENDS,
    mediad=True,
    reads_the_ball=True,
)
"""The Microduck: `robotd` and `mediad` as upstream ships them, on every backend it has."""
