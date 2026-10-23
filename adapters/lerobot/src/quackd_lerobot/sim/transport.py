"""`lerobot:mujoco`: the real backend, driving a simulated SO-101.

`LeRobotSim` is `LeRobotReal` with two things changed underneath it and nothing above. Its two
builders return the simulated follower (`follower.py`) and the scene's cameras (`camera.py`)
where the real backend's return LeRobot's, and its clock is the world's (`clock.py`) where the
real one's is the wall's. Every line in between is the real backend's own: the connect and its
retries, the travel read off a calibration and the refusals past it, the rest move, the hold,
the release and the take-hold, the close and what it says. A task file rehearsed here runs
through the code that will drive the arm in the lab, which is the point of rehearsing it.

What it adds is what an arm has and a model does not:

- **A world and its lifecycle.** `connect()` loads the model, lays out the scene from the seed,
  starts the arm at its rest pose, settled clear of the table and of itself where the pose, or
  the pose the close parks it in at the edge of its travel, puts it into either
  (`ArmWorld.settled`, which the rest move then drives back to, and to the edge of the travel
  where a settled angle is past it) and refused where settling cannot clear it, builds the
  clock and renders once on the event loop, so a machine that cannot draw the cameras says so
  at connect rather than at the first frame.
  `close()` stops the clock and frees every renderer, the viewer and the world on the loop.
- **Which calibration.** The file `--address` names, or for a named robot the one LeRobot
  would find under that name. A bare `--robot lerobot:mujoco` names no arm, and quackd's
  default id for an arm nobody named (`DEFAULT_ID`) would find whatever arm this machine last
  calibrated under it, so it gets the generic arm instead, whose travel is the model's own, and
  says so.
- **A person's hands.** Nobody is there to place a released arm, so a take-hold first lets
  gravity act on it (`PLACE_SETTLE_S`) and meets whatever pose physics left: the arm falls, and
  the take-hold's refusals for a slip or a joint past its travel are rehearsed as they happen.
  A close over an arm still in a hand lets it settle the same way.
- **The truth.** `sim_world` is where every object on the table is, now and at its peak, and
  `stop()` and `go_to_rest()` latch it on their way in, before a teardown moves anything. It is
  never in the state's extras, which reach the pilot and MCP. The attribute is not `world`,
  which the core reads as a world to record a GIF of.

Nothing here imports `mujoco` when the module is imported: `make()` builds this without the
physics extra installed, and `connect()` is where it is needed, and where a machine without it
is told which extra to install, before anything is fetched.
"""

from __future__ import annotations

import asyncio
import contextlib
import math
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any

from quackd.adapters.base import AdapterError, AdapterNotInstalled, HandResult, RestResult
from quackd.perception.color_blob import DEFAULT_FOV_DEG
from quackd.transport.base import DuckState, HeartbeatError, TransportError
from quackd_lerobot.policy.loop import FIRST_CHUNK_S, latency_ticks, rate_refusal
from quackd_lerobot.real import (
    MAX_STEP_DEG,
    CameraSpec,
    LeRobotReal,
    PolicyLike,
    PolicyRunner,
    joint_ranges,
)
from quackd_lerobot.sim import SIM_EXTRA
from quackd_lerobot.sim import upstream_api as so
from quackd_lerobot.sim.camera import SimCamera, open_renderer, render
from quackd_lerobot.sim.clock import SimClock
from quackd_lerobot.sim.faults import FaultPlan
from quackd_lerobot.sim.follower import SimFollower, SimFollowerConfig
from quackd_lerobot.sim.model import (
    LABEL,
    MOUNTS,
    ArmModel,
    CalibrationError,
    MotorCalibration,
    Scene,
    calibration_path,
    generic_calibration,
    load,
    names_a_port,
    parse_scene,
    port_refusal,
    read_calibration,
)
from quackd_lerobot.sim.world import ArmWorld
from quackd_lerobot.verbs import reachable_rest_goal

PLACE_SETTLE_S = 1.0
"""How long a take-hold lets a released arm fall before it takes hold, in sim time. At the
bench a person places the arm and a take-hold meets it where they left it; here nobody does,
so the person is modelled by letting gravity act, and the arm is taken hold of wherever the
table, its stops and its own weight have put it by then."""
GL_CHECK_SIZE = (64, 48)
"""The frame `connect()` renders to prove this machine can draw the scene: the smallest worth
drawing, because what it proves is the context and not the picture."""
GENERIC_ARM = (
    "no arm was named, so this rehearsal runs on the generic arm, whose travel is the model's "
    "own range on every joint and not any arm's calibration; give --address the file "
    "lerobot-calibrate wrote for an arm to rehearse that arm's travel"
)
DEFAULT_VIEWS = ("front", "top")
"""The scene's cameras that are quackd's own views of the table rather than a camera on the
arm, which a connect note and the state's assumptions name whenever one is open
(`default_views`)."""
SIM_TORQUE_LEFT_ON = (
    "the simulated arm is not at its rest pose ({why}), so torque was left on, as the arm's own "
    "close would leave it. There is nothing to hold, release or park: the simulated arm ends "
    "with the run, and the next connect starts it at its rest pose again"
)
"""The simulator's close line for an arm it kept torque on away from its rest pose. The real
one (`verbs.TORQUE_LEFT_ON`) tells a person to clear the arm's way and fold it with `quackd robot
release`, supporting it while that connects, or to cut its power, none of which a simulated arm
needs: its world closes with the close. What is left to act on is the reason, which is a rest
move that missed on the arm's own code."""
SIM_ASSUMPTIONS = (
    so.SERVO_DYNAMICS.name,
    so.JOINT_SIGN.name,
    so.JOINT_ZERO.name,
    so.GRIPPER_MAP.name,
)
"""What every simulated arm stands in for, on top of what the real backend lists: dynamics
nobody measured on an SO-101, and the maps from LeRobot's units onto the model."""


def default_views(cameras: Sequence[str]) -> str | None:
    """What a run is told about the views of the table it has open, naming those and no
    others, or None where it has none of them: a run on front and wrist used to be told about
    a top camera it never opened."""
    views = [name for name in DEFAULT_VIEWS if name in cameras]
    if not views:
        return None
    if len(views) == 1:
        return (
            f"the {views[0]} camera is quackd's default view of the table, not where any real "
            "camera stands"
        )
    return (
        f"the {' and '.join(views)} cameras are quackd's default views of the table, not where "
        "any real camera stands"
    )


def default_model() -> str | Path:
    """The model a rehearsal runs on: the SO-101's own, fetched at its pin the first time it is
    needed (`assets.py`). Tests put the stand-in here."""
    from quackd_lerobot.sim.assets import ensure_so101

    return ensure_so101().model_path


class LiveViewer:
    """MuJoCo's own passive viewer on the world, for `--live`: kept in step by a tick hook on
    the clock, and closing its window is a stop, as closing the cartoon's is."""

    def __init__(self, world: ArmWorld) -> None:
        import mujoco.viewer

        self.world = world
        with world.locked() as (model, data):
            try:
                self.handle = mujoco.viewer.launch_passive(
                    model, data, show_left_ui=False, show_right_ui=False
                )
            except Exception as e:
                hint = (
                    " On macOS the viewer must own the main thread: run the same command under "
                    "`mjpython` (installed with mujoco) instead of `python`."
                    if sys.platform == "darwin"
                    else ""
                )
                raise TransportError(
                    f"{LABEL} could not open the MuJoCo viewer ({type(e).__name__}: {e}).{hint}"
                ) from e

    def sync(self, _stepper: Any) -> None:
        """A tick hook. The world's state is read under its lock, as a renderer reads it."""
        if not self.handle.is_running():
            raise KeyboardInterrupt
        with self.world.locked():
            self.handle.sync()

    def close(self) -> None:
        self.handle.close()


class LeRobotSim(LeRobotReal):
    """The real backend over a simulated SO-101: `--robot lerobot:mujoco`.

    `address` is a calibration file rather than a serial port, and one shaped like a port is
    refused as the robot is built, before anything could open it. `seed` lays out the objects and
    seeds `faults`, a plan of bus faults or None for a bus with none. `live` opens MuJoCo's
    viewer and paces the clock on the wall's. `model` is the model to load, a path or MJCF
    text, and None for the SO-101's own (`default_model`). A camera url must name one of the
    scene's mounts. `scene` is the objects to lay on the table in place of the default ones, as
    `quackd preflight` reads them from a task's sidecar (`model.parse_scene`), and None for the
    default ones. `policy` is what `pick` and `manipulate` run, as for the real backend, a policy
    object or a runner such as the client of a policy server (`--policy-url`), and None leaves
    the arm without either."""

    name = "mujoco"
    label = "mujoco"

    def __init__(
        self,
        address: str | None = None,
        *,
        robot_id: str = "arm-01",
        seed: int = 0,
        live: bool = False,
        faults: FaultPlan | None = None,
        max_step_deg: float = MAX_STEP_DEG,
        cameras: Sequence[CameraSpec] = (),
        rest_pose: dict[str, float] | None = None,
        registered_name: str | None = None,
        model: str | Path | None = None,
        scene: Sequence[Mapping[str, Any]] | None = None,
        timeout_s: float = 1.0,
        policy: PolicyLike | PolicyRunner | None = None,
    ) -> None:
        if address and names_a_port(address):
            # for every other lerobot robot --address is the port, so this is an easy slip
            raise AdapterError(port_refusal(address))
        for spec in cameras:
            if spec.name not in MOUNTS:
                raise AdapterError(
                    f"{LABEL} --camera-url {spec.url!r} names {spec.name!r}, and the scene has "
                    f"no camera by that name; its cameras are {', '.join(MOUNTS)}. Name one "
                    "with ?name=."
                )
        # the lens each camera renders with is the one published, horizontal as the detector
        # measures it (`camera.py`), so a url that gave none still says which it was
        published = tuple(
            replace(s, fov_deg=s.fov_deg if s.fov_deg is not None else DEFAULT_FOV_DEG)
            for s in cameras
        )
        super().__init__(
            None,
            policy=policy,
            robot_id=robot_id,
            timeout_s=timeout_s,
            max_step_deg=max_step_deg,
            cameras=published,
            rest_pose=rest_pose,
            registered_name=registered_name,
        )
        self.calibration_address = address
        """The calibration file `--address` named, or None."""
        self.seed = seed
        self.live = live
        self.faults = faults
        self.model_source = model
        # parsed here rather than at connect, so a scene that cannot be laid out is refused as
        # the robot is built, before anything is loaded
        self.scene: Scene | None = None if scene is None else parse_scene(scene)
        self.place_settle_s = PLACE_SETTLE_S
        self.sim_world: ArmWorld | None = None
        """The world the last connect built, kept readable after the close for its latches."""
        self._calibration: tuple[dict[str, MotorCalibration], Path | None] = ({}, None)
        self._loop: asyncio.AbstractEventLoop | None = None
        self._viewer: LiveViewer | None = None
        self._thinking: tuple[float, float, int] = (0.0, 0.0, 1)
        """The policy's rate, the wall seconds one of its requests stands the clock still for,
        and the fewest ticks between two of its requests, as the last connect found them
        (`_time_thinking`), or zeros for an arm with no policy, or one whose policy could not
        say."""

    # ── the world ───────────────────────────────────────────────────────────────────────

    def _world(self) -> ArmWorld:
        if self.sim_world is None:
            raise TransportError(f"{LABEL} the simulator is not connected.")
        return self.sim_world

    def _sim_clock(self) -> SimClock | None:
        return self.clock if isinstance(self.clock, SimClock) else None

    def _load(self) -> tuple[ArmModel, list[str]]:
        """The model in its scene and the calibration its travel comes from, with a note for
        each thing a person should hear about them. In a worker thread: loading compiles the
        model twice and may fetch it, and none of it draws."""
        source = self.model_source if self.model_source is not None else default_model()
        if self.scene is None:
            arm = load(source, seed=self.seed)
        else:
            arm = load(source, seed=self.seed, objects=self.scene.objects)
        notes: list[str] = []
        path: Path | None = None
        if self.calibration_address:
            path = Path(self.calibration_address).expanduser()
            calibration = read_calibration(path)
        elif self.registered_name is not None:
            path = calibration_path(self.registered_name)
            if not path.is_file():
                raise CalibrationError(
                    f"{LABEL} {self.registered_name} has no calibration file where LeRobot "
                    f"would look for one ({path}). Give --address the file lerobot-calibrate "
                    "wrote for the arm, or leave the name out to rehearse on the generic arm."
                )
            calibration = read_calibration(path)
        else:
            calibration = generic_calibration(arm)
            notes.append(GENERIC_ARM)
        self._calibration = (calibration, path)
        return arm, notes

    def _lay_out(self, arm: ArmModel) -> ArmWorld:
        """The world the arm starts in, all of it on the calling thread: the arm at its rest
        pose, settled clear of the table and itself where the pose puts it into either, and
        the table laid out around it (`_lay_table`)."""
        world = self._arm_world(arm)
        try:
            self._lay_table(world)
        except BaseException:
            world.close()
            raise
        return world

    def _arm_world(self, arm: ArmModel) -> ArmWorld:
        """The arm at its rest pose, settled clear of the table and itself where the pose, or
        the pose the close parks it in at the edge of the travel this calibration recorded,
        puts it into either (`ArmWorld._settle`)."""
        travel = joint_ranges(dict(self._calibration[0]))
        return ArmWorld(arm, rest_pose=self.rest_pose, name=self.registered_name, travel=travel)

    def _lay_table(self, world: ArmWorld) -> None:
        """The scene's object between the jaws where the scene puts one there, which is read
        off the arm as it starts, and every other object clear of the arm and of that one
        (`ArmWorld.lay_clear`), so nothing is moved before the pilot moves it. It only places
        things and never steps, so a connect runs it in a worker thread."""
        jaws = self.scene.jaws if self.scene is not None else None
        if jaws is not None:
            world.place_between_jaws(jaws)
        world.lay_clear(self.seed, keep=jaws)

    def _render_once(self, world: ArmWorld) -> None:
        """Draw one small frame on this thread, the event loop's, and let the renderer go: a
        machine with no GL context is refused at connect, in words that say what to install,
        whether or not a camera was asked for."""
        mount = self.camera_specs[0].name if self.camera_specs else MOUNTS[0]
        renderer = open_renderer(world, *GL_CHECK_SIZE)
        try:
            render(renderer, world, int(world.arm.model.camera(mount).id))
        finally:
            renderer.close()

    async def connect(self) -> None:
        try:
            import mujoco  # noqa: F401
        except ImportError as e:
            # first, because the next thing a connect does is fetch the SO-101's model, which
            # is of no use to a machine that has nothing to load it in
            raise AdapterNotInstalled("lerobot", SIM_EXTRA) from e
        await self._shut()
        self._loop = asyncio.get_running_loop()
        arm, notes = await asyncio.to_thread(self._load)
        # Built on this thread, the event loop's, where the physics steps: a rest pose that
        # puts the arm into the table or into itself is settled out of them here, before the
        # clock starts, or refused where it cannot be (`ArmWorld._settle`), and the jaws and
        # the table are read off where it came to rest.
        world = self._arm_world(arm)
        try:
            await asyncio.to_thread(self._lay_table, world)
        except BaseException:
            world.close()
            raise
        self.sim_world = world
        self.clock = SimClock(world, realtime=self.live)
        try:
            self._render_once(world)
            if self.live:
                # before the hook goes on the clock: a viewer that will not open refuses the
                # connect, and one half set up would stop time for nobody's benefit
                self._viewer = LiveViewer(world)
                self.clock.add_tick_hook(self._viewer.sync)
            # built again on every connect, over this world
            self._robot = None
            self._cameras = {}
            await super().connect()
        except BaseException:
            await self._shut()
            raise
        notes.extend(world.notes)
        if views := default_views(self.camera_keys):
            notes.append(views)
        self.connect_notes.extend(notes)
        await self._time_thinking()

    async def _time_thinking(self) -> None:
        """What a request of this arm's policy costs on the wall's clock while the simulator's
        stands still: the latency the runner declares, asked on the runner's own worker as every
        call on it is, and one frame from every open camera, drawn and timed here, since a
        request carries one from each. Kept with the runner's rate for `frozen_inference_s`,
        and with how far apart its requests come: every tick for a runner asked every tick, and
        otherwise no closer than the ticks its answer is held back, since the loop asks again
        only once the last answer is let go (`PolicyLoop._wants`). A runner that cannot say
        leaves the zeros, and its segments are refused at their start for the same reason
        (`PolicyLoop.start`).

        So does a rate the loop will not pace (`rate_refusal`), whose segments are refused at
        their start too, and a latency of `FIRST_CHUNK_S` or more, whose segments starve before
        their first chunk lands: neither has thinking to wait for, and a rate or a latency past
        any a policy has made a bound too large to be a number of requests."""
        self._thinking = (0.0, 0.0, 1)
        loop = self._policy_loop
        if loop is None:
            return
        try:
            features = await loop.call(loop.runner.features)
            latency = float(await loop.call(loop.runner.latency_s))
            refused = rate_refusal(features)
            rate, per_tick = float(features.rate_hz), bool(features.per_tick)
        except Exception:
            return
        if refused is not None or not (math.isfinite(latency) and 0 <= latency < FIRST_CHUNK_S):
            return
        apart = 1 if per_tick else max(1, latency_ticks(latency, rate))
        started = time.perf_counter()
        for name in self.camera_keys:
            camera = self._cameras.get(name)
            if camera is not None:
                with contextlib.suppress(Exception):
                    await asyncio.to_thread(camera.read_latest)
        self._thinking = (rate, latency + time.perf_counter() - started, apart)

    def frozen_inference_s(self, segment_s: float) -> float:
        """The wall seconds this simulator's clock stands still over a segment of `segment_s`
        while its policy thinks, which the executor's timeout for `manipulate` has to cover on
        top of the segment's own seconds (`quackd.duckfile.narrow`). A lockstep clock waits for
        every request, so it is a request's cost (`_time_thinking`) for as many requests as the
        segment's ticks can hold, one every so many ticks, and one more: a bound on what the
        policy declared, never an estimate. A policy slower than it declares can still outrun
        it, as it would outrun its own delay line, and the timeout then says so
        (`slow_policy`). `--live` paces the clock on the wall's and it still waits, so it is the
        same there. A segment too long to count its ticks is bounded by nothing, which the
        narrowing holds to its own maximum."""
        rate, per_request, apart = self._thinking
        clock = self._sim_clock()
        if clock is None or not clock.lockstep or rate <= 0 or per_request <= 0:
            return 0.0
        ticks = segment_s * rate
        if not math.isfinite(ticks):
            return math.inf if ticks > 0 else 0.0
        return (math.ceil(math.ceil(ticks) / apart) + 1) * per_request

    def slow_policy(self) -> str | None:
        """Why the executor's own timeout ended the last segment, when its policy is why: its
        answers kept the simulator's clock standing still, on the wall's, for longer than the
        latency it declares allows as many requests and one more, which is how the timeout
        counts its thinking (`frozen_inference_s`), so a request took longer than it declares
        (`PolicyLoop.slowest_s`). A timeout that said no more than that `manipulate` timed out
        read as a simulator that had hung. None for a policy inside what it declares, a request
        that ran a little long among them, and before any segment has run."""
        loop = self._policy_loop
        if loop is None or loop.thinking_s <= (loop.waited + 1) * loop.declared_s:
            return None
        return (
            "The policy answered slower than the latency it declared: one request took "
            f"{loop.slowest_s:.2f} s on the wall's clock against the {loop.declared_s:g} s it "
            "declares, and the simulator's clock stands still while it thinks, so the timeout, "
            "sized from what it declares, ran out first. Bench it with quackd policy check "
            "--bench and serve it with the --latency-s that bench says covers it"
        )

    def _build_robot(self) -> SimFollower:
        calibration, path = self._calibration
        config = SimFollowerConfig(**self._config_kwargs())
        return SimFollower(self._world(), calibration, path, config, self.faults)

    def _build_camera(self, spec: CameraSpec) -> SimCamera:
        assert self._loop is not None, "connect() sets the loop before it builds a camera"
        return SimCamera(spec, self._world(), spec.name, self._loop)

    async def _shut(self) -> None:
        """Stop the clock, then free the viewer, every renderer and the world, on the event
        loop's thread, where each GL resource was made. Never raises; safe to call twice."""
        clock = self._sim_clock()
        if clock is not None:
            await clock.close()
        viewer, self._viewer = self._viewer, None
        if viewer is not None:
            if clock is not None:
                clock.remove_tick_hook(viewer.sync)
            with contextlib.suppress(Exception):
                viewer.close()
        for camera in list(self._cameras.values()):
            if isinstance(camera, SimCamera):
                with contextlib.suppress(Exception):
                    camera.disconnect()
        if self.sim_world is not None and not self.sim_world.closed:
            self.sim_world.close()

    async def close(self) -> None:
        clock = self._sim_clock()
        if self._in_hand and clock is not None and not clock.closed:
            # nobody holds a simulated arm, so the hand the close speaks to lets it settle
            with contextlib.suppress(Exception):
                await clock.sleep(self.place_settle_s)
        try:
            await super().close()
        finally:
            await self._shut()

    # ── what a rehearsal reads back ─────────────────────────────────────────────────────
    #
    # `quackd preflight` judges a run by these once it has ended, with `sim_world`'s latched
    # truth. None of it is in the state's extras, which reach the pilot.

    @property
    def last_rest(self) -> RestResult | None:
        """What the last rest move did, which at the end of a run is the teardown's, or None
        where none was made: an arm with no rest pose is not moved to one."""
        return self._rest_result

    @property
    def wedged(self) -> bool:
        """A call to the simulated bus has not come back, so every call after it is refused
        until it does (`LeRobotReal._call`). On the simulator that is quackd's own code stuck,
        since nothing on the other end of the bus can be slow."""
        return self._wedged is not None and not self._wedged.done()

    @property
    def sim_dt(self) -> float | None:
        """One step of the simulator's clock in seconds, or None before the first connect."""
        clock = self._sim_clock()
        return None if clock is None else clock.dt

    # ── what the arm does that a model does not ─────────────────────────────────────────

    def _latch(self, label: str) -> None:
        world = self.sim_world
        if world is not None and not world.closed:
            with contextlib.suppress(TransportError):
                world.latch(label)

    def _rest_target(self) -> tuple[dict[str, float], dict[str, float]]:
        """The real backend's rest pose, with each joint the world settled out of the table or
        the arm itself at the angle it came to rest at (`ArmWorld.settled`), as it starts or as
        the close parks it, and then clipped into the travel as any recorded pose is.

        The recorded fold on such a joint is a pose the model cannot hold, so a rest move
        driving back to it pushes the arm into the table, stalls, and every close after time
        has passed leaves torque on. The settled angle is where the arm started and what it
        holds. Inside the travel the joint is judged by the point rule there, because past it
        lies the table and not a fold the half-line rule could let the arm settle toward. A
        joint settled past its travel is parked at the edge of it and judged by the half-line
        rule there, as a recorded pose past its travel is, and the world settled the parked
        pose so that the joints beside it are clear there. The gripper is never in a rest goal,
        settled or not."""
        goal, recorded = super()._rest_target()
        settled = self.sim_world.settled if self.sim_world is not None else {}
        if not any(joint in recorded for joint in settled):
            return goal, recorded
        recorded = {joint: settled.get(joint, value) for joint, value in recorded.items()}
        return reachable_rest_goal(recorded, self.joint_range_deg)[0], recorded

    def _torque_left_on(self, why: str) -> str:
        return SIM_TORQUE_LEFT_ON.format(why=why)

    async def stop(self) -> None:
        self._latch("stop")
        await super().stop()

    async def go_to_rest(self) -> RestResult:
        self._latch("rest")
        return await super().go_to_rest()

    async def take_hold(self) -> HandResult:
        clock = self._sim_clock()
        if self._in_hand and not self._closed and clock is not None:
            # the person placing the arm, modelled by letting it fall (`PLACE_SETTLE_S`). A
            # clock that cannot run leaves the arm as it is, and the take-hold meets that. A
            # stop is not that: the viewer closed during the fall is a person's stop, and it
            # ends the take-hold here, with the arm still limp in the hand, as it ends a verb
            with contextlib.suppress(TransportError):
                await clock.sleep(self.place_settle_s)
        return await super().take_hold()

    async def heartbeat(self) -> None:
        clock = self._sim_clock()
        if clock is not None and clock.failure is not None:
            raise HeartbeatError(str(clock.failure))
        await super().heartbeat()

    def _heartbeat_reads(self) -> Callable[[], Any]:
        robot = self._robot
        if isinstance(robot, SimFollower):
            return robot.as_heartbeat(self._read_all)
        return self._read_all

    async def get_state(self) -> DuckState:
        state = await super().get_state()
        assumptions = [*state.extras.get("assumptions", []), *SIM_ASSUMPTIONS]
        if "wrist" in self.camera_keys:
            assumptions.append(so.WRIST_CAMERA_POSE.name)
        if views := default_views(self.camera_keys):
            assumptions.append(views)
        state.extras["assumptions"] = assumptions
        return state
