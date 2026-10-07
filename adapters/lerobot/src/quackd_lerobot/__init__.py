"""The LeRobot adapter: a desktop arm (an SO-101 class follower) as a quackd robot.

An arm has no legs, no head and no voice, so its manifest lists none of that: `move`,
`go_to`, `search_scan`, `say` and `gaze` do not exist here. What it has is joints, a
gripper, `place`, and, when a policy is available, `pick` and `manipulate`, each one skill
intent that the arm's own learned controller executes (the thesis, unchanged). Three
backends: `mock` (offline, scripted), `real` (LeRobot behind `quackd[lerobot]`, Python 3.12
or newer, first driven on an arm on 2026-09-15) and `mujoco` (the real backend's own code over
a physics model of the SO-101, behind `quackd[lerobot-sim]`, for rehearsing a task at home).
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from typing import Any

from PIL import Image

from quackd.adapters.base import (
    AdapterError,
    HandResult,
    PolicyChoice,
    RestResult,
    camera_urls,
    go_to_rest_if_any,
    let_go_if_any,
    one_camera_url,
    take_hold_if_any,
)
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
from quackd_lerobot.verbs import (
    JOINTS,
    lerobot_conditions,
    lerobot_verbs,
    published_travel,
    reachable_rest_goal,
    rest_clip_note,
    worth_saying,
)

__version__ = "0.17.1"
"""Kept in step with quackd's own version by scripts/set_version.py. It lives here rather
than being read from the core, because this file is all an adapter's sdist contains."""

BACKENDS = ("mock", "real", "mujoco")
SIMULATOR_BACKENDS = ("mujoco",)
"""The backends that drive a simulated arm rather than one on a desk. Read before anything is
built (`quackd.adapters.factory.is_simulator`), so a command that must only ever run on a
simulator refuses a real arm before connecting to it."""
DEFAULT_ID = "arm-01"
ROBOT_TYPE = "so101_follower"
BLURB = (
    "a six-joint desktop robot arm with a parallel gripper (an SO-101 class arm driven "
    "by LeRobot), bolted to a table"
)

REACH = Figure(
    value=0.4,
    confidence="estimate",
    source="the maker's URDF, TheRobotStudio/SO-ARM100 Simulation/SO101/so101_new_calib.urdf",
    note="link lengths from the shoulder to the gripper frame, summed with the arm straight and "
    "rounded down",
)
"""How far the gripper gets from the shoulder, read off the maker's own robot description.

Nobody publishes a reach for the SO-101, and the sheet used to say so, which on an arm told
the pilot to decline anything that turned on reaching: every task an arm has. The URDF gives
each joint's origin in its parent link's frame, so the distance from one joint to the next is
the length of that origin vector. From `shoulder_lift` outwards, read on 2026-09-23:

    elbow_flex          (-0.11257, -0.028,     0)          0.116 m
    wrist_flex          (-0.1349,   0.0052,    0)          0.135 m
    wrist_roll          ( 0,       -0.0611,    0.0181)     0.064 m
    gripper_frame_joint (-0.0079,  -0.000218, -0.0981274)  0.098 m

They sum to 0.413 m. That is an upper bound, since the links only add up in full when they
are collinear, and a grid sweep of `elbow_flex`, `wrist_flex` and `wrist_roll` through their
URDF limits put the farthest the gripper frame gets from the `shoulder_lift` axis at about
0.41 m. So 0.4, rounded down, and an estimate rather than official: it is quackd's arithmetic
on the maker's file, not a figure the maker states. It is measured from the shoulder joint
rather than the base, and to the gripper frame rather than the fingertips."""

DATASHEET = Datasheet(
    height_m=Figure(
        value=0.53,
        confidence="estimate",
        source="one vendor's listing",
        note="reaching straight up",
    ),
    dof=Figure(
        value=6,
        confidence="official",
        source="the LeRobot SO-101 docs",
        note="five joints and a gripper",
    ),
    payload_kg=Figure(value=0.5, confidence="estimate", source="one vendor's listing"),
    reach_m=REACH,
    manipulator="gripper",
    arms=1,
    tethered=True,
    cannot=[
        "go anywhere: it is bolted to a table and has no base",
        # This used to end "and nothing whose weight is not known", which is nearly every
        # object a task names: nobody tells the pilot what a pen weighs. It needs a scale to
        # judge an object by, not a ban on everything unweighed
        "lift or hold more than about half a kilogram: a pen, an empty cup or a wooden block "
        "weighs far less than that, and a full bottle or a tool may weigh more",
        f"reach anything more than about {REACH.value:g} m from its shoulder: that is the arm "
        "held straight out, and any bent pose reaches less",
        "feel what it holds: nothing reports grip force, so holding is inferred from the "
        "gripper stopping short of shut, which an empty hand that binds also does",
        "know its own mass: vendor listings disagree by a factor of three",
    ],
    notes=[
        "How wide the gripper opens and how hard it grips are not published",
        "No sensors beyond the servo positions unless a camera is configured, and the servo "
        "temperature, which quackd reads off the bus because LeRobot does not",
        "Vendor listings give the mass as anything from 0.8 to 2.5 kg and nobody official "
        "publishes one, so it is listed as not published rather than picked from a hat",
        "There are 7.4 V and 12 V builds of the same arm, with different stall torque and "
        "different supplies. Which one is on the desk is not something quackd can ask",
    ],
)


def lerobot_manifest(
    backend: str,
    robot_id: str | None = None,
    *,
    camera: bool = False,
    policy: bool = False,
    robot_type: str = ROBOT_TYPE,
    lerobot_version: str | None = None,
    joint_range_deg: dict[str, tuple[float, float]] | None = None,
    step_deg: float | None = None,
    calibration_file: str | None = None,
    camera_url: str | None = None,
    camera_fov_deg: float | None = None,
    camera_names: Sequence[str] = (),
    rest_pose_clipped: Sequence[tuple[str, float, float]] = (),
) -> RobotManifest:
    """The arm as data. `camera` and `policy` are what the backend found at connect: the
    static manifest of `real` claims neither, the mock has both. So are the joint ranges,
    which come off the arm's own calibration file and are unknown until it has answered.

    So is `rest_pose_clipped`: each joint the recorded rest pose puts past that travel, as
    `(joint, recorded, reachable)`. It goes in the manifest rather than the state because the
    manifest is what a run's record opens with, and the reader of a transcript whose arm
    parked short of its recorded fold deserves to find out why on the first line."""
    own = lerobot_verbs(policy=policy)
    verbs = [
        verb_spec(own["report_state"], core=True),
        verb_spec(CORE["stop"], core=True),
        verb_spec(own["move_joints"], core=False),
        verb_spec(own["gripper"], core=False),
        verb_spec(own["place"], core=False),
    ]
    if camera:
        verbs.insert(0, verb_spec(CORE["observe"], core=True))
    # not_hot guards the five body joints, which are the ones LeRobot caps nothing on; the
    # gripper has its own torque and current caps, and refusing to open a hot one would
    # strand whatever it is holding
    preconditions = {"move_joints": ["torque_on", "not_hot"], "place": ["holding"]}
    if policy:
        verbs.append(verb_spec(own["pick"], core=False, safety_class="confirm"))
        verbs.append(verb_spec(own["manipulate"], core=False, safety_class="confirm"))
        preconditions["pick"] = ["torque_on", "not_hot"]
        preconditions["manipulate"] = ["torque_on", "not_hot"]
    intents: list[Any] = ["joint", "gripper"] + (["skill"] if policy else [])
    sensors: list[Any] = ["joint_state"] + (["camera"] if camera else [])
    limits = {"joint_deg": 180.0, "gripper": 100.0}
    if step_deg is not None:
        limits["step_deg"] = float(step_deg)
    if camera_fov_deg is not None:
        # what the detector calibrates bearings with; over MCP there is no --fov-deg, so
        # the lens travels with the camera instead
        limits["camera_fov_deg"] = float(camera_fov_deg)
    extras: dict[str, Any] = {
        "robot_type": robot_type,
        "joints": list(JOINTS),
        "policy": policy,
        "lerobot_version": lerobot_version,
        # the gripper is the only joint LeRobot writes a torque or current cap for
        "torque_limit_scope": "gripper_only",
    }
    if joint_range_deg:
        extras["joint_range_deg"] = {
            joint: published_travel(lo, hi) for joint, (lo, hi) in joint_range_deg.items()
        }
    if rest_pose_clipped:
        # only when there is one: a pose inside its travel changes nothing, so the manifest of
        # every such arm, its digest included, stays exactly what it was
        extras["rest_pose_clipped"] = {
            joint: {"recorded": round(recorded, 1), "reachable": round(reachable, 1)}
            for joint, recorded, reachable in rest_pose_clipped
        }
    if calibration_file:
        extras["calibration_file"] = calibration_file
    if camera_url:
        extras["camera"] = camera_url
    if len(camera_names) > 1:
        # only when there is more than one: the name of a single camera is quackd's own
        # default rather than anything the owner chose, and a pilot told its one camera is
        # called `front` would start naming it in sentences nobody needs
        extras["cameras"] = list(camera_names)
    return RobotManifest(
        id=robot_id or DEFAULT_ID,
        vendor="huggingface",
        model="lerobot-so101",
        embodiment="arm",
        mobility="none",
        intents=intents,
        sensors=sensors,
        verbs=verbs,
        preconditions=preconditions,
        # the gripper's torque and current caps written by LeRobot at configure() are the
        # only native limit; no deadman: an arm holds its goal when the client goes quiet
        safety_authority=SafetyAuthority(native="torque_limit", deadman=False, heartbeat_hz=2.0),
        frame=Frame(
            reference="base",
            note="joint space in degrees (gripper 0..100); no camera-to-base calibration",
        ),
        limits=limits,
        backend=backend,
        blurb=BLURB,
        datasheet=DATASHEET,
        extras=extras,
    )


class LeRobotAdapter:
    """A `RobotAdapter` over the mock or the real backend."""

    name = "lerobot"
    supports_hand_off = True
    """A person can be handed this body: quackd takes torque off at its recorded rest pose,
    waits while they place it, and holds whatever pose they left it in (`quackd run
    --by-hand`), and a person holding it can have its torque taken off wherever it stands
    (`quackd robot release`, and the offer at the end of a run whose rest move missed).
    Declared rather than inferred, because the run and the command refuse outright on a body
    that does not offer it rather than connecting and finding out."""
    supports_rest_pose = True
    """This body is driven to a recorded pose before torque is released. The registry's
    `rest-pose` command asks for exactly this, because a body with joints that quackd does
    not park would take the recording and never use it."""

    def __init__(self, transport: DuckTransport, *, robot_id: str | None = None) -> None:
        self.transport = transport
        self.backend = transport.name
        self.robot_id = robot_id or DEFAULT_ID
        self.manifest: RobotManifest | None = None
        # known before connect for the mock (a class attribute) and for a real backend
        # with an injected policy; refreshed at connect
        self._policy = bool(getattr(transport, "policy_available", False))

    async def connect(self) -> RobotManifest:
        await self.transport.connect()
        self._policy = bool(getattr(self.transport, "policy_available", False))
        spec = getattr(self.transport, "camera_spec", None)
        self.manifest = lerobot_manifest(
            self.backend,
            self.robot_id,
            camera=bool(getattr(self.transport, "camera_available", False)),
            policy=self._policy,
            lerobot_version=getattr(self.transport, "lerobot_version", None),
            joint_range_deg=getattr(self.transport, "joint_range_deg", None) or None,
            step_deg=getattr(self.transport, "max_step_deg", None),
            calibration_file=getattr(self.transport, "calibration_file", None),
            camera_url=getattr(spec, "url", None),
            camera_fov_deg=getattr(spec, "fov_deg", None),
            camera_names=getattr(self.transport, "camera_keys", ()),
            rest_pose_clipped=tuple(getattr(self.transport, "rest_clipped", ())),
        )
        return self.manifest

    def rest_pose_note(self, pose: dict[str, float]) -> str | None:
        """What recording `pose` as the rest pose would mean on this arm, or None if nothing.

        `quackd robot rest-pose` asks this after reading the joints and before asking whether
        to keep them. The clip is this adapter's, on the travel the connected backend read off
        its own calibration, so the command neither knows the rule nor reimplements it, and the
        sentence is the one the run and `doctor` say about the same pose."""
        ranges = dict(getattr(self.transport, "joint_range_deg", None) or {})
        _, clipped = reachable_rest_goal(pose, ranges)
        name = getattr(self.transport, "registered_name", None)
        return rest_clip_note(worth_saying(clipped), name)

    async def disconnect(self) -> None:
        await self.transport.close()

    async def close(self) -> None:
        await self.disconnect()

    async def get_state(self) -> DuckState:
        return await self.transport.get_state()

    async def get_frame(self) -> Image.Image | None:
        return await self.transport.get_frame()

    async def get_frames(self) -> list[CameraFrame]:
        """Every camera this arm has, primary first. A mock has one and says so."""
        return await frames_of(self.transport)

    @property
    def camera_keys(self) -> tuple[str, ...]:
        """The cameras this arm was opened with, whether or not one is answering now.

        Forwarded for the same reason `camera_error` is, and read for a sharper one: it is how
        a caller knows a lone picture came off a two-camera arm and still needs its name."""
        return tuple(getattr(self.transport, "camera_keys", ()))

    async def go_to_rest(self) -> RestResult:
        return await go_to_rest_if_any(self.transport)

    async def let_go(self, *, anywhere: bool = False) -> HandResult:
        """Torque off, for a person at the arm. `anywhere` is the transport's own keyword and
        is passed only when set, for `let_go_if_any`'s reason: without it the arm is released
        at its rest pose or refused, which is `--by-hand`'s rule and stays the default."""
        if anywhere:
            return await let_go_if_any(self.transport, anywhere=True)
        return await let_go_if_any(self.transport)

    async def take_hold(self) -> HandResult:
        return await take_hold_if_any(self.transport)

    @property
    def rest_pose(self) -> dict[str, float] | None:
        """Where this arm rests, if it was recorded. Read by the loop and the MCP session to
        decide whether there is a move to narrate at all."""
        pose = getattr(self.transport, "rest_pose", None)
        return dict(pose) if pose else None

    async def send_intent(self, intent: Intent) -> Ack:
        return await self.transport.send_intent(intent)

    async def health(self) -> Health:
        try:
            await self.transport.heartbeat()
        except HeartbeatError as e:
            return Health(ok=False, reason=str(e))
        state = await self.transport.get_state()
        extras: dict[str, Any] = {
            "holding": state.holding,
            "policy": state.policy,
            "torque": state.extras.get("torque"),
        }
        temperatures = [float(v) for v in state.extras.get("temperature_c", {}).values()]
        if temperatures:
            extras["hottest_c"] = round(max(temperatures))
        # what doctor is the first place to show: which calibration file the arm answered
        # with, and the travel that file gives each joint, as the manifest publishes it and the
        # pilot is told it, to a tenth rounded inward, and never rounded out to a whole degree
        # the arm refuses a goal at
        if path := getattr(self.transport, "calibration_file", None):
            extras["calibration_file"] = path
        if ranges := getattr(self.transport, "joint_range_deg", None):
            extras["joint_range_deg"] = {
                j: published_travel(lo, hi) for j, (lo, hi) in ranges.items()
            }
        return Health(ok=True, battery_percent=None, extras=extras)

    @property
    def camera_error(self) -> str | None:
        """Why the last frame did not arrive, when the backend knows. A verb's `ctx.transport`
        is this adapter, not the transport underneath, so the hint has to be proxied here to
        reach `observe`."""
        error = getattr(self.transport, "camera_error", None)
        return str(error) if error else None

    @property
    def close_note(self) -> str | None:
        """What `close()` has to say about torque, when it left it on. Proxied for the same
        reason `camera_error` is: the callers hold this adapter, not the transport."""
        note = getattr(self.transport, "close_note", None)
        return str(note) if note else None

    @property
    def connect_notes(self) -> tuple[str, ...]:
        """One sentence per connect attempt the backend had to make again, from the last
        `connect()`. Proxied for `close_note`'s reason: the run narrates them into its
        transcript and `doctor` lists them as advice, and both hold this adapter. The backend
        has already logged each one while it happened; this is the record."""
        return tuple(str(n) for n in getattr(self.transport, "connect_notes", ()) or ())

    @property
    def in_hand(self) -> bool | None:
        """Whether the backend takes the arm to be in somebody's hands, because a release went
        out and no hold has confirmed torque since, or None when the backend does not say.
        Proxied for `close_note`'s reason: the end-of-run offer reads it off this adapter after
        an interrupt lands on the release it asked for, to tell a release that went out from
        one that never did."""
        held = getattr(self.transport, "in_hand", None)
        return None if held is None else bool(held)

    @property
    def refused_hold(self) -> HandResult | None:
        """The take-hold the backend refused since the last release that left the arm in a
        hand, or None. Proxied for `close_note`'s reason: the run reads it off this adapter
        after the stop that opens its teardown, to say a refusal that stop made, and to know
        which arm the person is holding when an interrupt kept the run's own take-hold from
        telling it."""
        refused = getattr(self.transport, "refused_hold", None)
        return refused if isinstance(refused, HandResult) else None

    def set_stop_check(self, check: Callable[[], bool] | None) -> None:
        """Hand the backend a way to hear that a stop was asked for while it connects, or take
        it away. Passed on to a backend that retries its connect (`LeRobotReal`), and a no-op
        on one that does not, which is the mock. The agent loop hands its abort flag's `is_set`
        to any body that has this before it connects, and takes it back afterwards."""
        forward = getattr(self.transport, "set_stop_check", None)
        if callable(forward):
            forward(check)

    @property
    def stop_error(self) -> str | None:
        """Why the last stop did not reach the arm, when the backend knows: the core `stop`
        verb reads this and refuses to say "stopped" over a hold that never got there."""
        error = getattr(self.transport, "stop_error", None)
        return str(error) if error else None

    @property
    def stop_skipped(self) -> tuple[str, ...]:
        """The body joints the last stop wrote no goal for, because each read past its travel.
        Proxied for `stop_error`'s reason: the core `stop` verb reads it off whatever it was
        handed, which is this adapter, and says which joints the hold left alone."""
        return tuple(str(j) for j in getattr(self.transport, "stop_skipped", ()) or ())

    @property
    def policy_segment(self) -> Any:
        """The policy segment the backend's last `do` started, for `pick` or `manipulate` to
        wait on, or None on a backend that runs none, the mock. Proxied for `stop_error`'s
        reason: a verb's `ctx.transport` is this adapter."""
        return getattr(self.transport, "policy_segment", None)

    @property
    def policy_stopped_by(self) -> str | None:
        """What stopped the backend's last segment from outside, which `pick` and
        `manipulate` say after `stopped:`. Proxied for `stop_error`'s reason."""
        by = getattr(self.transport, "policy_stopped_by", None)
        return str(by) if by else None

    @property
    def segment_s(self) -> float | None:
        """How long the backend runs a `manipulate` segment, which the verb reads off whatever
        it was handed, and that is this adapter. Proxied for `stop_error`'s reason."""
        seconds = getattr(self.transport, "segment_s", None)
        return None if seconds is None else float(seconds)

    def set_segment_s(self, seconds: float) -> None:
        """Tell the backend how long each `manipulate` segment runs from now on: the task
        file's `policy.segment_s`, as the run narrows the verb to it
        (`quackd.duckfile.narrow`). The run holds this adapter, so it is passed on here."""
        forward = getattr(self.transport, "set_segment_s", None)
        if not callable(forward):
            raise TypeError(f"the {self.backend} backend cannot be told a segment's length")
        forward(seconds)

    def frozen_inference_s(self, segment_s: float) -> float:
        """The wall seconds the backend's clock stands still over a segment that long while its
        policy thinks: the simulator's, and nothing on any other. Passed on for
        `set_segment_s`'s reason."""
        ask = getattr(self.transport, "frozen_inference_s", None)
        return float(ask(segment_s)) if callable(ask) else 0.0

    def slow_policy(self) -> str | None:
        """Why the executor's timeout ended the last segment, when the backend can tell that
        its policy answered slower than it declared: the simulator's, and nothing on any other,
        whose clock does not stand still while the policy thinks. Passed on for
        `set_segment_s`'s reason."""
        ask = getattr(self.transport, "slow_policy", None)
        said = ask() if callable(ask) else None
        return said if isinstance(said, str) and said else None

    def ask_policy(self) -> dict[str, Any] | None:
        """Ask the policy server what it serves, now, and say it as the record names it
        (`RemoteRunner.record`), or None for an arm whose policy is not served by another
        process. It blocks for as long as the client's own timeouts let a call take, and raises
        the client's sentence when the server does not answer. `quackd run` asks this before the
        arm connects, so its header can name the checkpoint and a server that is not there is a
        run refused with nothing energised. The connect asks again, and checks what it hears
        against the arm (`LeRobotReal._fit_policy`)."""
        loop = getattr(self.transport, "policy_loop", None)
        runner = getattr(loop, "runner", None)
        ask, record = getattr(runner, "policy", None), getattr(runner, "record", None)
        if not callable(ask) or not callable(record):
            return None
        ask()
        return dict(record())

    @property
    def policy_served(self) -> dict[str, Any] | None:
        """What the arm's policy is, as it was last heard and with nothing asked now: the
        server's address, redacted, and what it serves, for a policy served by another process
        (`PolicyLoop.served`), an empty dict for one in this process, and None for an arm with
        no policy, the mock included, whose policy is its own script."""
        loop = getattr(self.transport, "policy_loop", None)
        return None if loop is None else loop.served()

    @property
    def policy_record(self) -> dict[str, Any] | None:
        """The policy block of the run's record (`PolicyLoop.record`): what the policy is,
        what its segments counted and the round trips to its server, or None for an arm with no
        policy. The run reads it once, as it ends, and writes it into `summary.json`."""
        loop = getattr(self.transport, "policy_loop", None)
        return None if loop is None else loop.record()

    async def heartbeat(self) -> None:
        await self.transport.heartbeat()

    async def stop(self) -> None:
        await self.transport.stop()

    def subscribe(self, topic: str) -> AsyncIterator[dict[str, Any]]:
        return self.transport.subscribe(topic)

    def now(self) -> float:
        return self.transport.now()

    async def sleep(self, seconds: float) -> None:
        await self.transport.sleep(seconds)

    def preconditions(self) -> dict[str, Precondition]:
        return lerobot_conditions()

    def implementations(self) -> dict[str, Verb]:
        return lerobot_verbs(policy=self._policy)

    @property
    def mobility(self) -> str:
        return "none"

    @property
    def is_simulator(self) -> bool:
        """Whether this arm is the simulator's (`lerobot:mujoco`) rather than one on a desk. Not
        `perception.is_simulated`, which counts the mock too."""
        from quackd_lerobot.sim.transport import LeRobotSim

        return isinstance(self.transport, LeRobotSim)

    @property
    def post_sleep(self) -> Callable[[], None] | None:
        return getattr(self.transport, "post_sleep", None)

    @post_sleep.setter
    def post_sleep(self, hook: Callable[[], None] | None) -> None:
        self.transport.post_sleep = hook  # type: ignore[attr-defined]


# ── what the factory calls ──────────────────────────────────────────────────────────────


def describe(
    backend: str, robot_id: str | None = None, *, policy: PolicyChoice | None = None
) -> RobotManifest:
    """Static: the mock always has its camera and its scripted policy; the real backend
    claims neither until connect() finds them, and claims no joint ranges either, because
    they are read off the arm's calibration file. The simulator is the real backend's code and
    describes itself as the real backend does.

    With `policy`, the policy server a run hands its segments to, the real backend and the
    simulator claim `pick` and `manipulate` before they connect, since the connect that would
    find the policy is the one given it here: a task that allows them validates before anything
    connects, as it would once connected. The mock runs its own scripted policy and refuses one
    (`_refuse_policy`)."""
    offline = backend == "mock"
    if policy is not None:
        _refuse_policy(backend)
    return lerobot_manifest(backend, robot_id, camera=offline, policy=offline or policy is not None)


def implementations() -> dict[str, Verb]:
    return lerobot_verbs(policy=True)


def conditions() -> dict[str, Precondition]:
    return lerobot_conditions()


def _check_rest_pose(rest_pose: dict[str, float] | None) -> None:
    """Refuse a pose that names nothing this arm would drive, rather than ignoring it.

    `quackd robot rest-pose` reads the pose off the arm and cannot produce one of these; a
    hand-edited `robots.json` can, and did in review. Without this the pose is accepted,
    printed by `robot show`, driven nowhere, and the arm is released where it stands: the one
    failure this whole feature exists to prevent, arrived at by a typed joint name."""
    from quackd_lerobot.verbs import NO_DRIVABLE_JOINT, drivable_rest_joints, rest_goal

    if rest_pose and not rest_goal(rest_pose):
        raise AdapterError(
            NO_DRIVABLE_JOINT.format(
                named=", ".join(sorted(rest_pose)), drivable=", ".join(drivable_rest_joints())
            )
        )


def _refuse_policy(backend: str) -> None:
    """The mock's policy is its own, scripted and in process, and it reaches no server: one
    given to it would be dropped without a word, and a rehearsal against the mock would pass
    where the arm's server was never asked."""
    if backend == "mock":
        raise AdapterError(
            "lerobot:mock runs its own scripted policy and reaches no policy server: "
            "--policy-url is for lerobot:real and lerobot:mujoco"
        )


def _policy_runner(policy: PolicyChoice | None) -> Any:
    """The client a policy server is reached through, `RemoteRunner`, built from the address
    and the token `--policy-url` and `--policy-token` gave, or None without a server. A token
    that was not typed is `QUACKD_POLICY_TOKEN`, then the file the server writes, by the
    client's own rule (`policy.client.client_token`). An address the client would refuse, and a
    token found nowhere, are refused here, before anything connects. Its motors are the arm's
    joints until the connect's fit hands it the bus's own (`LeRobotReal._fit_policy`), and
    `--accept-other-frame` is the fit's override of the same name (`policy.fit.fit`)."""
    if policy is None:
        return None
    from quackd_lerobot.policy.client import RemoteRunner, client_token, policy_address

    url, token = policy.reach()
    try:
        policy_address(url)  # an address that will be refused is said before a missing token
        return RemoteRunner(
            url,
            token=client_token(token),
            motors=JOINTS,
            accept_other_frame=policy.accept_other_frame,
        )
    except ValueError as e:
        raise AdapterError(str(e)) from None


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
    faults: str | None = None,
    scene: Sequence[Mapping[str, Any]] | None = None,
    policy: PolicyChoice | None = None,
) -> LeRobotAdapter:
    """`faults` is a spec of bus faults for the simulator to meet (`sim.faults.FaultPlan`),
    seeded by `seed`, and refused on any other backend: an arm on a desk has the faults it
    has. `scene` is the objects the simulator lays on its table in place of its default ones
    (`sim.model.parse_scene`), refused on any other backend for the same reason: the table in
    front of a real arm has on it what somebody put there.

    `policy` is the policy server the arm hands its `pick` and `manipulate` segments to
    (`--policy-url`), reached through a `RemoteRunner` (`_policy_runner`), which the real
    backend and the simulator both take as their policy and check against the arm at connect,
    before torque (`LeRobotReal._fit_policy`). The mock refuses one (`_refuse_policy`)."""
    _check_rest_pose(rest_pose)
    if policy is not None:
        _refuse_policy(backend)
    if faults is not None and backend != "mujoco":
        raise AdapterError(
            f"lerobot:{backend} has no faults to be told of: only the simulator, "
            "lerobot:mujoco, takes a fault spec."
        )
    if scene is not None and backend != "mujoco":
        raise AdapterError(
            f"lerobot:{backend} has no scene to lay out: only the simulator, lerobot:mujoco, "
            "takes one."
        )
    # The name the arm was asked for by, before the default fills it in: the registered name
    # for every robot built from the registry, which is the only place a rest pose comes from.
    # The close note names it in the commands it gives a person, and says NAME where there
    # is none rather than the default id, which is not necessarily what anybody registered.
    registered_name = robot_id
    if backend == "mock":
        from quackd_lerobot.mock import LeRobotMock

        # the mock draws its own view and reads no url, but a second camera is still refused
        # rather than dropped: only the real arm opens more than one, and a task rehearsed
        # against the mock should fail here rather than at the bench
        one_camera_url(camera_url, spec="lerobot:mock")
        return LeRobotAdapter(
            LeRobotMock(rest_pose=rest_pose, registered_name=registered_name), robot_id=robot_id
        )
    if backend == "mujoco":
        from quackd_lerobot.real import parse_camera_urls, step_from_env
        from quackd_lerobot.sim.camera import CAMERA_HINT
        from quackd_lerobot.sim.faults import FaultPlan
        from quackd_lerobot.sim.transport import LeRobotSim

        start = seed if seed is not None else 0
        return LeRobotAdapter(
            LeRobotSim(
                address=address,
                robot_id=robot_id or DEFAULT_ID,
                seed=start,
                live=live,
                faults=None if faults is None else FaultPlan.parse(faults, seed=start),
                max_step_deg=step_from_env(),
                cameras=parse_camera_urls(camera_urls(camera_url), label=backend, hint=CAMERA_HINT),
                rest_pose=rest_pose,
                registered_name=registered_name,
                scene=scene,
                policy=_policy_runner(policy),
            ),
            robot_id=robot_id,
        )
    if backend == "real":
        from quackd_lerobot.real import LeRobotReal, parse_camera_urls, step_from_env

        return LeRobotAdapter(
            LeRobotReal(
                address=address,
                robot_id=robot_id or DEFAULT_ID,
                max_step_deg=step_from_env(),
                cameras=parse_camera_urls(camera_urls(camera_url)),
                rest_pose=rest_pose,
                registered_name=registered_name,
                policy=_policy_runner(policy),
            ),
            robot_id=robot_id,
        )
    raise ValueError(f"unknown lerobot backend {backend!r}; choose one of {BACKENDS}")


__all__ = [
    "BACKENDS",
    "DEFAULT_ID",
    "JOINTS",
    "SIMULATOR_BACKENDS",
    "LeRobotAdapter",
    "conditions",
    "describe",
    "doctor_rows",
    "implementations",
    "lerobot_manifest",
    "make",
    "published_travel",
]


# What this adapter reads from upstream, for `quackd doctor`. Declared here rather than in a
# table in the core, because the list belongs to whoever wrote the adapter (ADR-0022). The
# import is deferred so that naming the upstream costs nothing until doctor asks.
def _upstream_rows() -> tuple[tuple[str, object, str, str], ...]:
    from quackd_lerobot import upstream_api
    from quackd_lerobot.policy import upstream_api as policies
    from quackd_lerobot.sim import upstream_api as so_arm100

    # The first row is the one in this table that is not a list of what nobody has tried. An
    # SO-101 ran the real backend on 2026-09-15, so the column says what that run did and did
    # not cover. The second is the arm's simulator model, which lerobot:mujoco loads and no arm
    # has been compared against. The third is LeRobot's policies, read at the version the
    # laptop runs, which the policy server loads, and has loaded a tiny random ACT alone.
    return (
        (
            "lerobot",
            upstream_api,
            "docs/adapters/lerobot/README.md",
            "run on an SO-101 on 2026-09-15; the pick policy was not exercised",
        ),
        (
            "SO-ARM100",
            so_arm100,
            "docs/adapters/lerobot/README.md",
            "anything against an arm: lerobot:mujoco loads it, and nobody has compared the two",
        ),
        (
            "LeRobot policies",
            policies,
            "docs/adapters/lerobot/README.md",
            f"a trained checkpoint: read at lerobot {policies.VERSION}, and CI's policy job serves "
            "a tiny random ACT alone, so SmolVLA, pi05 and tick mode have never run",
        ),
    )


UPSTREAMS = _upstream_rows()


def doctor_rows() -> list[Any]:
    """What `quackd doctor` says about the arm's simulator on this machine: whether the extra
    that installs MuJoCo is here and at which version, which commit of the SO-101's model the
    simulator runs, and whether that model is in the cache yet or the first connect fetches it.

    Doctor asks every installed adapter this with no arguments, so it cannot know which robot,
    if any, a person asked about, and it answers only what is true of the machine. Nothing here
    imports `mujoco`, makes a GL context or fetches anything: the version is read from the
    installer's metadata and the cache is only looked at. Whether this machine can draw the
    scene is the simulator's own connect, which renders once, and `doctor --robot NAME`
    reaches it for a registered `lerobot:mujoco` robot, with the address it was registered
    with or, when it has none, on the calibration LeRobot keeps under its name."""
    import importlib.metadata
    import os
    from pathlib import Path

    from quackd.doctor import TransportRow
    from quackd_lerobot.sim import SIM_EXTRA
    from quackd_lerobot.sim import upstream_api as so
    from quackd_lerobot.sim.assets import ASSETS_ENV, cached_so101

    try:
        version: str | None = importlib.metadata.version("mujoco")
    except importlib.metadata.PackageNotFoundError:
        version = None
    pin = so.PIN[:7]
    model = cached_so101()
    # A checkout named by the variable is not the cache, and a connect never fetches in its
    # place: one without the model is refused, so that is what the row has to say, with what
    # to do about it, rather than promising a fetch that will not happen.
    override = os.environ.get(ASSETS_ENV)
    if override and model is None:
        note = (
            f"{ASSETS_ENV} points at {Path(override).expanduser()}, which has no "
            f"{so.MODEL_FILE}, so a connect refuses: point it at the {so.SIM_DIR} directory "
            f"of an SO-ARM100 checkout, or unset it to let quackd fetch {pin}"
        )
    elif override and model is not None and model.pinned:
        note = f"SO-ARM100 at {pin} from {ASSETS_ENV} at {model.directory}"
    elif model is None:
        note = f"SO-ARM100 at {pin}: not in the cache yet, and the first connect fetches it"
    elif model.pinned:
        note = f"SO-ARM100 at {pin}: in the cache at {model.directory}"
    else:
        note = f"SO-ARM100 from {ASSETS_ENV} at {model.directory}, which differs from {pin}"
    status = f"mujoco {version}" if version is not None else f"missing ({SIM_EXTRA})"
    # found only when a connect has both things it needs here, the physics and the model
    ready = version is not None and model is not None
    return [TransportRow("lerobot:mujoco", status, note, found=ready)]
