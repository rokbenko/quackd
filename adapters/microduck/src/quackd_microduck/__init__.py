"""The Microduck adapter: every Microduck transport, wrapped, plus the manifest.

`quackd/transport/*` is untouched and becomes the Microduck backend layer. This adapter
delegates every call to one of those transports and adds what 0.4 needs: a manifest, the
named preconditions the manifest references, and the Microduck's own verbs. Wrapping
rather than moving is how "zero behaviour change" is made mechanically true (ADR-0017).

Another robot that answers `robotd`'s JSON-RPC is a body of this family (ADR-0075): a `Body` of its
own (`quackd_microduck.body`), and a package that hands it to `manifest_for`, `MicroduckAdapter` and
`transports.factory.make_transport`, as `quackd_huebot` does.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Collection, Sequence
from typing import Any

from PIL import Image

from quackd.adapters.base import AdapterError, RestResult, one_camera_url, refuse_rest_pose
from quackd.adapters.manifest import (
    Frame,
    Health,
    RobotManifest,
    SafetyAuthority,
    Sensor,
    verb_spec,
)
from quackd.perception import ASSUMED_LENS
from quackd.space.room import RoomError
from quackd.space.source import SpatialSource
from quackd.transport.base import (
    Ack,
    DuckState,
    DuckTransport,
    HeartbeatError,
    Intent,
    frame_period_of,
    frame_taken_at_of,
    spatial_of,
)
from quackd.verbs.core import CORE
from quackd.verbs.registry import Precondition, Verb
from quackd_microduck.body import (
    BLURB,
    DATASHEET,
    MAP_BACKENDS,
    MICRODUCK,
    ROOM_BACKENDS,
    Body,
)
from quackd_microduck.verbs import (
    MICRODUCK_VERBS,
    conditions_for,
    microduck_conditions,
    verbs_for,
)

__version__ = "0.32.0"
"""Kept in step with quackd's own version by scripts/set_version.py. It lives here
rather than being read from the core, because this file is all an adapter's sdist
contains."""

BACKENDS = MICRODUCK.backends

NO_MAP_WHY = (
    "it says where it is by odometry alone and keeps no map on this connection, and exploring "
    "walks by a map: a simulator given --room keeps one"
)
"""Why `explore` and `find` are not offered on a duck with places and no map, in
`extras.not_offered`, which the pilot reads beside its body and a refusal quotes: both walk to
what a map has not seen, and odometry sees nothing."""

NO_SIZE_WHY = (
    "nothing publishes the real duck's body radius, which exploring plans its way round a room "
    "by: a simulator given --room knows its own"
)
"""Why `explore` and `find` are not offered on a real duck whatever answers beside its robotd: a
way through a room keeps the body's radius from everything solid, and the real duck's is
published nowhere, where each simulator's is its own body's (`transports.mapd.sim_map_params`)."""

NO_CAMERA_WHY = "it has no camera on this connection, and find looks for things with one"
"""Why `find` is not offered on a duck that explores but has no frame to look in."""


def offered_skill_verbs(body: Body, skills: Collection[str] | None) -> list[str]:
    """The verbs that send `robot.do` a body that gates them offers (`Body.skill_verbs`): every one
    while nothing has said which skills it has, which is the description `validate` reads and the
    mock, and on a connected robot each one whose every skill it listed."""
    return [
        verb
        for verb, needs in (body.skill_verbs or {}).items()
        if skills is None or all(skill in skills for skill in needs)
    ]


def manifest_for(
    body: Body,
    backend: str,
    robot_id: str | None = None,
    *,
    camera: bool = True,
    camera_fov_deg: float | None = None,
    camera_fov_assumed: str | None = None,
    places: bool | None = None,
    mapped: bool | None = None,
    sized: bool | None = None,
    skills: Collection[str] | None = None,
) -> RobotManifest:
    """A body of this family as data. Static: no connection needed (validate, announce, doctor).

    `places` is whether the duck says where it is on this connection, which settles whether it
    can name places and list them (`quackd.verbs.places`). None is what the backend promises
    before anything connects: a robotd sends its odometry and the mock scripts a pose, so both
    declare them, and the simulators say nothing of where the duck is and declare none. A
    connection whose transport carries no reading (`spatial_of`) leaves them out whatever was
    declared, as one with no frame leaves out the camera's verbs.

    `mapped` is whether that reading keeps a map, which `explore` and `find` walk by, and `sized`
    whether it knows how big the body is, which they plan their way round a room by. None is what
    the backend may have before anything connects: a robotd may have `mapd` beside it and a
    simulator given a room keeps a map, but only a simulator knows how big its duck is, the real
    duck's radius being published nowhere. `find` also looks through the camera. A duck with
    places that cannot explore or find says why, in `extras.not_offered`, which the pilot reads
    beside its body.

    `camera` is the one thing a description of the robot cannot settle on its own. robotd's
    socket carries no picture, so on the jsonrpc backend a frame exists only when `--camera-url`
    names something serving one. Advertising `observe` either way promises the pilot vision it
    may not have.

    `camera_fov_deg` is the lens that camera reported, in degrees across the frames it hands
    out, when it reported one. It goes in the limits, where `quackd run` takes the lens its
    detector measures through when no `--fov-deg` names one, and where `serve-mcp`, which has
    no `--fov-deg`, always takes it, as the LeRobot arm's camera does.

    `camera_fov_assumed` is the words of the assumption that lens rests on, when nobody measured
    it, which go in the extras under `quackd.perception.ASSUMED_LENS`: the detector then
    measures through the lens uncalibrated, and says to pass `--fov-deg`.

    `skills` is what the robot's `robot.subscribe` answer listed, on a connected robot, and None
    everywhere else. On a body that gates its skills, each verb that sends `robot.do` is declared
    only when every skill it sends is among them, so a skill the robot does not have is never a
    verb the pilot is offered (`Body.skill_verbs`). Any other body takes no notice of it.
    """
    verbs = verbs_for(body)
    texts = body.core_texts
    core = [
        verb_spec(CORE["report_state"], core=True),
        verb_spec(CORE["stop"], core=True),
        verb_spec(verbs["say"], core=True),
        verb_spec(CORE["move"], core=True, description=texts["move"]),
    ]
    if camera:
        core += [
            verb_spec(CORE["observe"], core=True),
            verb_spec(CORE["go_to"], core=True, description=texts["go_to"]),
            verb_spec(CORE["search_scan"], core=True, description=texts["search_scan"]),
            verb_spec(CORE["approach_and"], core=True, description=texts["approach_and"]),
        ]
    offers_places = places if places is not None else backend in body.places_backends
    if offers_places:
        core += [
            verb_spec(CORE[name], core=True)
            for name in ("map", "mark_place", "forget_place", "go_to_place")
        ]
    keeps_map = mapped if mapped is not None else backend in body.map_backends or places is True
    knows_size = sized if sized is not None else backend in body.room_backends
    offers_explore = offers_places and keeps_map and knows_size and "explore" in verbs
    offers_find = offers_explore and camera and "find" in verbs
    offered = offered_skill_verbs(body, skills)
    gated = body.skill_verbs or {}
    # `watch` looks through the camera, `explore` walks by a map and `find` does both, so each is
    # a verb of a duck that has what it needs, declared below
    extensions = [
        verb_spec(v, core=False)
        for n, v in verbs.items()
        if n not in ("say", "watch", "explore", "find") and (n not in gated or n in offered)
    ]
    if camera and "watch" in verbs:
        extensions.append(verb_spec(verbs["watch"], core=False))
    if offers_explore:
        extensions.append(verb_spec(verbs["explore"], core=False))
    if offers_find:
        extensions.append(verb_spec(verbs["find"], core=False))
    sensors: list[Sensor] = list(body.sensors)
    if camera:
        sensors.insert(0, "camera")
    # The 0.3 attachments, walk, kick and grab needing standing and sit, stand and gaze not
    # being fallen, and the three verbs that walk on what the camera sees, which had no
    # precondition at all until posture began failing closed. A precondition is read once, when
    # the verb starts, and what stops a duck picked up halfway through one is the transport's
    # hold on `robot.move`. The simulators never report `unknown`, so on them the one change is
    # that a seated or fallen duck is refused the three, where it used to be steered while its
    # legs ignored every twist. Each applies only to a verb this body declares here.
    preconditions: dict[str, list[str]] = {
        "move": ["standing"],
        "kick": ["standing"],
        "grab": ["standing"],
        "sit": ["not_fallen"],
        "stand": ["not_fallen"],
        "gaze": ["not_fallen"],
        "go_to": ["standing"],
        "search_scan": ["standing"],
        "approach_and": ["standing"],
        # the head alone, as `gaze`: a seated duck may watch, and a fallen or held one may not
        "watch": ["not_fallen"],
        "go_to_place": ["standing"],  # it walks; where it is the verb checks
        "explore": ["standing"],
        "find": ["standing"],
    }
    declared = {spec.name for spec in core + extensions}
    preconditions = {verb: names for verb, names in preconditions.items() if verb in declared}
    limits = dict(body.limits)
    extras: dict[str, Any] = {}
    # what this duck lacks here, said beside its body where the pilot judges a task; the
    # datasheet is the body's own, the same on every backend
    not_offered = dict(body.withheld)
    if offers_places and not offers_explore:
        why = NO_SIZE_WHY if keeps_map else NO_MAP_WHY
        not_offered |= {"explore": why, "find": why}
    elif offers_explore and not offers_find:
        not_offered["find"] = NO_CAMERA_WHY
    if not_offered:
        extras["not_offered"] = not_offered
    if camera and camera_fov_deg is not None:
        limits["camera_fov_deg"] = float(camera_fov_deg)
        if camera_fov_assumed:
            extras[ASSUMED_LENS] = camera_fov_assumed
    # A claim about hardware, which a body that has not shown its deadman does not make (`Body`).
    deadman = body.claims_deadman(backend)
    return RobotManifest(
        id=robot_id or body.name,
        vendor=body.vendor,
        model=body.model,
        embodiment="biped",
        mobility="legged",
        intents=list(body.intents),
        sensors=sensors,
        verbs=core + extensions,
        preconditions=preconditions,
        safety_authority=SafetyAuthority(
            native="robotd_deadman" if deadman else "none", deadman=deadman, heartbeat_hz=2.0
        ),
        frame=Frame(reference="body"),
        limits=limits,
        backend=backend,
        blurb=body.blurb,
        datasheet=body.datasheet,
        extras=extras,
    )


def microduck_manifest(
    backend: str,
    robot_id: str = "microduck",
    *,
    camera: bool = True,
    camera_fov_deg: float | None = None,
    camera_fov_assumed: str | None = None,
    places: bool | None = None,
    mapped: bool | None = None,
    sized: bool | None = None,
) -> RobotManifest:
    """The Microduck as data: `manifest_for` over its own body, which says what each of these is."""
    return manifest_for(
        MICRODUCK,
        backend,
        robot_id,
        camera=camera,
        camera_fov_deg=camera_fov_deg,
        camera_fov_assumed=camera_fov_assumed,
        places=places,
        mapped=mapped,
        sized=sized,
    )


def _lens(value: Any) -> float | None:
    """A field of view a camera reported, when it is one: a number of degrees between nothing and
    a half turn. Anything else is no lens, and the detector falls back on its own."""
    if isinstance(value, int | float) and not isinstance(value, bool) and 0 < value < 180:
        return float(value)
    return None


class MicroduckAdapter:
    """A `RobotAdapter` over any of the Microduck transports, for the body its class names. A robot
    of this family in another package subclasses it and names its own (`quackd_huebot`)."""

    name = "microduck"
    body = MICRODUCK

    def __init__(self, transport: DuckTransport, *, robot_id: str | None = None) -> None:
        # A transport built for one body says what it may send in that body's terms, so a Huebot
        # over a Microduck's would send looks its shim does not answer. The simulators and the
        # mock carry no body, and are any body's.
        # Only a `Body` is a body here: the physics transport's `body` names the physics model
        # it loads. By name and not by identity, since a test that imports the package afresh
        # holds a second `MICRODUCK`, which is the same body.
        theirs = getattr(transport, "body", None)
        if isinstance(theirs, Body) and theirs.name != self.body.name:
            raise ValueError(f"a {self.name} adapter over a transport built for {theirs.name}")
        self.transport = transport
        self.backend = transport.name
        self.robot_id = robot_id or self.body.name
        self.manifest: RobotManifest | None = None
        self.last_look: tuple[float, float, float] | None = None
        """The trunk-frame point of the newest look the duck accepted, or None before any:
        where its head was last sent, which `watch` starts from. Not where the state says the
        camera points, which on a robotd that glances shows the glance."""

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
        assumed = getattr(self.transport, "camera_fov_assumed", None)
        source = spatial_of(self.transport)
        # The skills a robot listed, which a gated body's verbs that send `robot.do` are offered
        # for. The mock has no list, and is described whole.
        listed = getattr(self.transport, "skills", None)
        self.manifest = manifest_for(
            self.body,
            self.backend,
            self.robot_id,
            camera=camera,
            camera_fov_deg=_lens(getattr(self.transport, "camera_fov_deg", None)),
            camera_fov_assumed=assumed if isinstance(assumed, str) else None,
            # a robotd that reports no odometry, and a simulator not asked for a room, say
            # nothing of where the duck is, and a pilot offered places there could mark none
            places=source is not None,
            # a reading of odometry alone, where no mapd answered, keeps no map to explore by,
            # and mapd's map comes with no body radius to plan a way round a room by
            mapped=source is not None and source.has_grid and source.view is not None,
            sized=source is not None and bool(source.footprint_m),
            skills=tuple(str(s) for s in listed) if isinstance(listed, Sequence) else None,
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
        ack = await self.transport.send_intent(intent)
        if intent.kind == "look" and ack.accepted:
            p = intent.params
            self.last_look = (
                float(p.get("x", 1.0)),
                float(p.get("y", 0.0)),
                float(p.get("z", 0.0)),
            )
        return ack

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
        key = self.body.health_key
        if isinstance(hello := state.extras.get(key), dict):
            extras[key] = self.body.health_line(hello)
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

    @property
    def frame_period_s(self) -> float | None:
        """How long one frame of the camera takes to follow the last, which `go_to` reads off the
        object it was handed, this adapter, to tell whether its steering law keeps up with the
        camera: on `jsonrpc` from mediad's `/frame`, and None from every camera that does not
        say, the simulators' among them."""
        return frame_period_of(self.transport)

    @property
    def frames_upright(self) -> bool:
        """Whether every frame this duck hands out is upright and known fresh, which `watch` needs,
        since it turns the head by where a thing is across the picture, and uses a frame only once
        it was taken after the head settled: on the simulators always, which draw each frame when
        it is asked for, and on `jsonrpc` from mediad's `/frame` alone, whose frames say when they
        were taken. A transport that does not say is taken for one whose frames may be neither."""
        return getattr(self.transport, "frames_upright", False) is True

    @property
    def twist_shown_since(self) -> Callable[[float], bool] | None:
        """The transport's own `twist_shown_since` on `jsonrpc`, and None on the simulators and
        the mock, which say nothing of the kind. `search_scan` reads it off the object it was
        handed, this adapter (`quackd.transport.base.twist_shown_since_of`), to count the turns
        it made by the ones robotd's state showed it took, since nothing answers a `robot.move`.
        This adapter did not pass it on before 0.21.0, so a run's scan counted the turns it sent:
        over a slow link, one stopped on a robotd that took none of them said it had made a turn.
        A method here that answered for every backend would read as a body that took none."""
        shown = getattr(self.transport, "twist_shown_since", None)
        return shown if callable(shown) else None

    @property
    def spatial(self) -> SpatialSource | None:
        """Where the duck is: the transport's own source, contact odometry on `jsonrpc` when
        robotd reports it, and None on every backend that does not say. Passed on by name for
        the reason `twist_shown_since` is: anything else reads this adapter, not the transport,
        and a property that answered for every backend would read as a duck that knows where it
        is (`quackd.transport.base.spatial_of`)."""
        return spatial_of(self.transport)

    @property
    def head_pitches(self) -> bool:
        """Whether a look's pitch moves this duck's camera: on every backend but the cartoon,
        whose world is flat, so `watch` asks no `height` there."""
        return getattr(self.transport, "head_pitches", True) is not False

    @property
    def camera_fps(self) -> float | None:
        """How many frames a second an HTTP camera is asked for, on `jsonrpc`, and None on every
        backend that draws its frame when asked."""
        fps = getattr(self.transport, "camera_fps", None)
        return float(fps) if isinstance(fps, int | float) and not isinstance(fps, bool) else None

    @property
    def frame_fetch_s(self) -> float | None:
        """How long the newest fetch of an HTTP camera took, on `jsonrpc`, which `watch` sizes its
        wait for a fresh frame by (`quackd.transport.base.frame_fetch_of`)."""
        fetch = getattr(self.transport, "frame_fetch_s", None)
        return (
            float(fetch) if isinstance(fetch, int | float) and not isinstance(fetch, bool) else None
        )

    def frame_asked_at(self) -> float | None:
        """When the newest fetch of an HTTP camera was asked for, on `jsonrpc`, answered or not
        (`quackd.transport.base.frame_asked_at_of`), and None everywhere else."""
        asked = getattr(self.transport, "frame_asked_at", None)
        return asked() if callable(asked) else None

    @property
    def look_head(self) -> dict[str, float] | None:
        """The four head joints robotd answered the newest `robot.look` with (`LookResult.head`),
        on `jsonrpc`, which `watch` records beside the look it sent. None on every backend that
        answers a look with no joints."""
        head = getattr(self.transport, "look_head", None)
        return dict(head) if isinstance(head, dict) else None

    def lens_given(self, fov_deg: float) -> None:
        """Hand the field of view `--fov-deg` gave to the transport, when it keeps a record of
        the lens, so that the state says what the detector measures through: on `jsonrpc` it
        stops listing the lens it assumed. The simulators keep none."""
        given = getattr(self.transport, "lens_given", None)
        if callable(given):
            given(fov_deg)

    def subscribe(self, topic: str) -> AsyncIterator[dict[str, Any]]:
        return self.transport.subscribe(topic)

    def now(self) -> float:
        return self.transport.now()

    async def sleep(self, seconds: float) -> None:
        await self.transport.sleep(seconds)

    def preconditions(self) -> dict[str, Precondition]:
        return conditions_for(self.body)

    def implementations(self) -> dict[str, Verb]:
        return dict(verbs_for(self.body))

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


def describe(
    backend: str, robot_id: str | None = None, *, room: str | None = None
) -> RobotManifest:
    _refuse_room(backend, room)
    # a simulator given a room says where the duck is; any other backend as it promises
    return microduck_manifest(
        backend, robot_id or "microduck", places=True if room is not None else None
    )


def _refuse_room(backend: str, room: str | None) -> None:
    """Refuse a room on a backend that lays none out, rather than take it and drop it."""
    if room is None or backend in ROOM_BACKENDS:
        return
    where = " or ".join(f"microduck:{b}" for b in ROOM_BACKENDS)
    if backend == "jsonrpc":
        raise AdapterError(
            "microduck:jsonrpc is a real duck, whose room is the one it stands in: --room lays "
            f"a room out in {where}"
        )
    raise AdapterError(f"microduck:{backend} lays out no room: --room is for {where}")


def _refuse_map_address(backend: str, map_address: str | None) -> None:
    """Refuse a map's address on a backend that reaches no mapd, rather than take it and drop
    it."""
    if map_address is None or backend in MAP_BACKENDS:
        return
    if backend in ROOM_BACKENDS:
        raise AdapterError(
            f"microduck:{backend} keeps its own map of the room --room lays out: --map-address "
            "is where a real duck's mapd answers, for microduck:jsonrpc"
        )
    raise AdapterError(
        f"microduck:{backend} reaches no mapd: --map-address is for microduck:jsonrpc"
    )


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
    room: str | None = None,
    map_address: str | None = None,
) -> MicroduckAdapter:
    refuse_rest_pose("microduck", rest_pose)
    _refuse_room(backend, room)
    _refuse_map_address(backend, map_address)
    url = one_camera_url(camera_url, spec=f"microduck:{backend}")
    # `token` is accepted and unused, deliberately: the factory calls every adapter's `make`
    # with the same four keywords, and this robot has nothing to authenticate to. `robotd`'s
    # socket has no auth at all — access is filesystem permissions on /run/robotd.sock — and
    # mediad's own note is that a pairing PIN which is 000000 on every robot "authenticates
    # nobody". Reach both over ssh rather than trusting the network.
    from quackd_microduck.transports.factory import make_transport

    try:
        transport = make_transport(
            backend,
            seed=seed,
            address=address,
            live=live,
            camera_url=url,
            room=room,
            map_address=map_address,
        )
    except RoomError as e:
        raise AdapterError(str(e)) from None
    return MicroduckAdapter(transport, robot_id=robot_id)


__all__ = [
    "BACKENDS",
    "BLURB",
    "DATASHEET",
    "MICRODUCK",
    "MICRODUCK_VERBS",
    "Body",
    "MicroduckAdapter",
    "conditions",
    "conditions_for",
    "describe",
    "implementations",
    "make",
    "manifest_for",
    "microduck_conditions",
    "microduck_manifest",
    "offered_skill_verbs",
    "verbs_for",
]


# What this adapter reads from upstream, for `quackd doctor`. Three rows: the robot's own IPC
# protocol, the model and walking policy the physics backend fetches at run time and never ships,
# and `mapd`, the onboard map read on its branch. Declared here rather than in a table in the
# core (ADR-0022).
def _upstream_rows() -> tuple[tuple[str, object, str, str], ...]:
    from quackd_microduck import mapd_upstream_api as mapd
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
        (
            "mapd",
            mapd,
            "docs/adapters/microduck/README.md",
            "a robot: it is on an upstream branch, in no robotd release",
        ),
    )


UPSTREAMS = _upstream_rows()
