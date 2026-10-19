"""The physics simulator behind the `DuckTransport` protocol (`--robot microduck:mujoco`).

The same shape as `sim2d.py` on purpose: one world, one shared `FlockClock` ticking it at
the policy rate, intents mapped the same way. What differs is what a step costs, which is
why the clock's `dt` is 20 ms here and 50 ms there, and why nothing in this module imports
`mujoco` until `connect()`: the extra is optional and the failure names it.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator, Callable
from typing import Any

from PIL import Image

from quackd.transport.base import Ack, DuckState, HeartbeatError, Intent, TransportError

BATTERY_DRAIN_PER_S = 0.02  # percent per sim second, as in sim2d
BODIES = ("puppet", "microduck")
"""What `body=` accepts. `puppet` is the kinematic stand-in that needs nothing downloaded
and is what the tests use; `microduck` is upstream's real model on upstream's real walking
policy, fetched into a user cache on first use. Spelled here rather than imported, because
naming a body must not import mujoco (`tests/test_memory.py` builds every backend with no
extras installed)."""

#: The physics world is expensive to draw, so the recorder samples it half as often as the
#: cartoon's. `FrameRecorder` reads this off the transport.
RECORDER_EVERY_S = 0.5

DEFAULT_BODY = "microduck"
BODY_ENV = "QUACKD_MUJOCO_BODY"


class MujocoTransport:
    name = "mujoco"
    frames_upright = True
    """The head camera is rendered upright, along the head's yaw and pitch, when a frame is asked
    for (`render_headcam`), so every frame `watch` takes shows the head where it is."""

    def __init__(
        self,
        seed: int = 0,
        *,
        live: bool = False,
        realtime: bool | None = None,
        frame_size: int = 256,
        battery_start: float = 100.0,
        body: str = DEFAULT_BODY,
    ) -> None:
        self.recorder_every_s = RECORDER_EVERY_S
        if body not in BODIES:
            raise TransportError(f"unknown mujoco body {body!r}; choose one of {', '.join(BODIES)}")
        self.seed = seed
        self.live = live
        self.realtime = live if realtime is None else realtime
        self.frame_size = frame_size
        self.battery_start = battery_start
        self.body = body
        self.duck_index = 0
        self.pid = "duck-0"
        self.world: Any = None
        self.clock: Any = None
        self._closed = False
        self._viewer: Any = None
        self._pending_hooks: list[Callable[[Any], None]] = []
        self.post_sleep: Callable[[], None] | None = None
        """Called after every sim sleep. The flock uses it to preempt an in-flight verb."""

    # ── hooks (recorder, live window) ───────────────────────────────────────────────

    def add_tick_hook(self, hook: Callable[[Any], None]) -> None:
        """Hooks may arrive before `connect()` (the CLI builds the recorder first); they are
        attached to the clock the moment there is one."""
        if self.clock is None:
            self._pending_hooks.append(hook)
        else:
            self.clock.add_tick_hook(hook)

    def _connected(self) -> Any:
        """The world, or a refusal that names the reason.

        Every one of these used to reach through `self.world` while it was still None, so
        calling them before `connect()` gave an AttributeError about NoneType rather than a
        transport error saying what was wrong.
        """
        if self.world is None:
            raise TransportError("the mujoco transport is not connected")
        return self.world

    def render_panes(self, size: int) -> tuple[Image.Image, Image.Image, str]:
        """What the recorder draws: the arena from a corner, and the head's own view."""
        from quackd_microduck.sim3d.render import render_headcam, render_overview

        w = self._connected()
        return render_overview(w, size), render_headcam(w, size), "duck cam"

    # ── protocol ────────────────────────────────────────────────────────────────────

    async def connect(self) -> None:
        try:
            from quackd_microduck.sim3d.world import CONTROL_DT, MujocoWorld
        except ImportError as e:
            raise TransportError(
                "the mujoco backend needs the physics extra: uv pip install 'quackd[mujoco]'"
            ) from e
        from quackd.sim2d.clock import FlockClock

        self._closed = False
        # Building a MicroduckBody loads two ONNX sessions and may download ten megabytes
        # of upstream model on first use. Off the event loop, so the heartbeat keeps beating.
        self.world = await asyncio.to_thread(MujocoWorld, seed=self.seed, body=self.body)
        self.clock = FlockClock(self.world, dt=CONTROL_DT, realtime=self.realtime)
        if self.live:
            # fail BEFORE registering with the clock, as sim2d does: a dead registration
            # would freeze time for everyone
            from quackd_microduck.sim3d.live import LiveViewer

            self._viewer = LiveViewer(self.world)
            self.clock.add_tick_hook(self._viewer.sync)
        for hook in self._pending_hooks:
            self.clock.add_tick_hook(hook)
        self._pending_hooks.clear()
        self.clock.register(self.pid)

    async def close(self) -> None:
        self._closed = True
        if self.clock is not None:
            self.clock.unregister(self.pid)
            await self.clock.stop()  # this transport owns its clock; sim2d's may be shared
        if self._viewer is not None:
            self.clock.remove_tick_hook(self._viewer.sync)
            with contextlib.suppress(Exception):
                self._viewer.close()
            self._viewer = None
        if self.world is not None:
            self.world.close()

    async def get_frame(self) -> Image.Image | None:
        from quackd_microduck.sim3d.render import render_headcam

        return render_headcam(self._connected(), self.frame_size)

    async def get_state(self) -> DuckState:
        w = self._connected()
        battery = max(0.0, self.battery_start - BATTERY_DRAIN_PER_S * w.t)
        return DuckState(
            t=w.t,
            x=w.x,
            y=w.y,
            theta=w.theta,
            policy=w.policy,
            posture=w.posture,
            fallen=w.posture == "fallen",
            battery_percent=battery,
            holding=w.holding,
            extras=w.snapshot(),
        )

    async def send_intent(self, intent: Intent) -> Ack:
        w = self._connected()
        p = intent.params
        try:
            return self._dispatch(w, intent, p)
        except ValueError as e:
            # the world refuses a non-finite twist or gaze. A refused intent is an answer the
            # pilot can read and correct; letting it out of here would end the run instead.
            return Ack(accepted=False, reason=str(e))

    def _dispatch(self, w: Any, intent: Intent, p: dict[str, Any]) -> Ack:
        match intent.kind:
            case "move":
                w.set_velocity(p.get("vx", 0.0), p.get("vy", 0.0), p.get("wz", 0.0))
            case "stop":
                w.stop()
            case "do":
                return self._do(str(p.get("skill")))
            case "look":
                clamped = w.look(p.get("x", 1.0), p.get("y", 0.0), p.get("z", 0.0))
                return Ack(accepted=True, reason="clamped to head limits" if clamped else None)
            case "sound":
                w.sound(str(p.get("tag", "chirp")), p.get("text"))
            case "enable":
                if p.get("on", True):
                    w.enable()
            case "pose":
                pass
            case _:
                return Ack(accepted=False, reason=f"mujoco: unknown intent {intent.kind}")
        return Ack()

    def _do(self, skill: str) -> Ack:
        w = self.world
        if w.posture == "fallen":
            return Ack(accepted=False, reason="the duck has fallen")
        match skill:
            case "kick_left" | "kick_right":
                if w.posture != "standing":
                    return Ack(accepted=False, reason="cannot kick while sitting")
                w.kick("left" if skill == "kick_left" else "right")
                return Ack()  # the kick *ran*; whether it connected shows in ball telemetry
            case "ground_pick":
                w.ground_pick()
                return Ack()
            case "sit_toggle":
                from quackd_microduck.sim3d.world import NotSupported

                try:
                    w.sit_toggle()
                except NotSupported as e:
                    return Ack(accepted=False, reason=str(e))
                return Ack()
            case "roulade":
                return Ack(accepted=True, reason="roulade is a no-op in mujoco")
            case _:
                return Ack(accepted=False, reason=f"unknown skill {skill!r}")

    async def subscribe(self, topic: str) -> AsyncIterator[dict[str, Any]]:  # type: ignore[override]
        from quackd_microduck.sim3d.world import CONTROL_DT

        # Deliberately this duck's own participant id, not a second one. Time advances only
        # when every registered participant is parked, so a subscriber with an id of its own
        # would be waiting for a duck that is awake only because the same coroutine is inside
        # the subscription: one task, two ids, and it blocks itself. The clock refuses two
        # tasks sleeping under one id, which is the case this used to lose silently.
        while not self._closed:
            await self.sleep(CONTROL_DT)
            yield {"topic": topic, **(await self.get_state()).model_dump()}

    async def heartbeat(self) -> None:
        if self._closed:
            raise HeartbeatError("mujoco transport is closed")
        if self.clock is not None and self.clock.failure is not None:
            raise HeartbeatError(str(self.clock.failure))

    async def stop(self) -> None:
        if self.world is not None:
            self.world.stop()

    def now(self) -> float:
        return 0.0 if self.world is None else float(self.world.t)

    async def sleep(self, seconds: float) -> None:
        from quackd.sim2d.clock import HookInterrupt, WorldStepError

        try:
            await self.clock.sleep(self.pid, seconds)
        except HookInterrupt as e:
            from quackd.safety import Aborted  # local import: safety must stay clock-free

            raise Aborted(str(e)) from None
        except WorldStepError as e:
            # The verb fails with the reason, and the next heartbeat aborts the run with the
            # same words. Without this the physics failure surfaced as a verb that hung.
            raise TransportError(str(e)) from e
        if self.post_sleep is not None:
            self.post_sleep()
