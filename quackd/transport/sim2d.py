"""The default transport: the built-in 2D simulator behind the `DuckTransport` protocol.

Its job is to make the north-star demo need no hardware and no GPU. Time is simulated and
shared: a `FlockClock` advances the world only while every attached duck is asleep, so a
single duck behaves exactly as before and a flock of N stays deterministic. One transport
is one duck's view of a (possibly shared) world.
"""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator, Callable
from typing import Any

from PIL import Image

from quackd.sim2d.clock import FlockClock, HookInterrupt
from quackd.sim2d.render import render_duckcam
from quackd.sim2d.world import DT, World
from quackd.transport.base import Ack, DuckState, HeartbeatError, Intent

BATTERY_DRAIN_PER_S = 0.02  # percent per sim second: a 5-minute run costs ~6 %


class Sim2DTransport:
    name = "sim2d"
    frames_upright = True
    """The duck-cam is drawn the right way up, so `watch` may read left and right off it."""
    head_pitches = False
    """The cartoon's world is flat: a look's height is taken and does nothing, so `watch` asks
    the decision LLM where a thing is across the picture and never how high."""

    def __init__(
        self,
        seed: int = 0,
        *,
        live: bool = False,
        realtime: bool | None = None,
        person: bool = True,
        frame_size: int = 256,
        battery_start: float = 100.0,
        world: World | None = None,
        clock: FlockClock | None = None,
        duck_index: int = 0,
    ) -> None:
        self.realtime = live if realtime is None else realtime
        self.world = world if world is not None else World(seed=seed, person=person)
        self.clock = clock if clock is not None else FlockClock(self.world, realtime=self.realtime)
        self.duck_index = duck_index
        self.pid = f"duck-{duck_index}"
        self.seed = seed
        self.live = live
        self.frame_size = frame_size
        self.battery_start = battery_start
        self._closed = False
        self._window: Any = None
        self.post_sleep: Callable[[], None] | None = None
        """Called after every sim sleep. The flock uses it to preempt an in-flight verb."""

    # ── hooks (recorder, live window) ───────────────────────────────────────────────

    def add_tick_hook(self, hook: Callable[[World], None]) -> None:
        self.clock.add_tick_hook(hook)

    # ── protocol ────────────────────────────────────────────────────────────────────

    async def connect(self) -> None:
        self._closed = False
        if self.live:
            # the window can fail to open (no pygame, headless display): fail BEFORE
            # registering with the clock, or a dead registration would freeze the flock
            from quackd.sim2d.live import LiveWindow  # optional pygame dependency

            self._window = LiveWindow(self.frame_size)
            self.add_tick_hook(self._window.draw)
        self.clock.register(self.pid)

    async def close(self) -> None:
        self._closed = True
        self.clock.unregister(self.pid)
        if self._window is not None:
            self.clock.remove_tick_hook(self._window.draw)  # never draw on a dead display
            with contextlib.suppress(Exception):
                self._window.close()
            self._window = None

    async def get_frame(self) -> Image.Image | None:
        return render_duckcam(self.world, self.frame_size, duck_index=self.duck_index)

    async def get_state(self) -> DuckState:
        w = self.world
        d = w.ducks[self.duck_index]
        battery = max(0.0, self.battery_start - BATTERY_DRAIN_PER_S * w.t)
        policy = "sit" if d.posture == "sitting" else ("walk" if d.moving else "stand")
        return DuckState(
            t=w.t,
            x=d.x,
            y=d.y,
            theta=d.theta,
            policy=policy,
            posture="fallen" if d.posture == "fallen" else d.posture,
            fallen=d.posture == "fallen",
            battery_percent=battery,
            holding=d.holding,
            extras=w.snapshot(self.duck_index),
        )

    async def send_intent(self, intent: Intent) -> Ack:
        w = self.world
        i = self.duck_index
        p = intent.params
        match intent.kind:
            case "move":
                w.set_velocity(p.get("vx", 0.0), p.get("vy", 0.0), p.get("wz", 0.0), duck_index=i)
            case "stop":
                w.stop(duck_index=i)
            case "do":
                return self._do(str(p.get("skill")))
            case "look":
                clamped = w.look(p.get("x", 1.0), p.get("y", 0.0), p.get("z", 0.0), duck_index=i)
                return Ack(accepted=True, reason="clamped to head limits" if clamped else None)
            case "sound":
                w.sound(str(p.get("tag", "chirp")), p.get("text"), duck_index=i)
            case "enable":
                if p.get("on", True):
                    w.enable(duck_index=i)
            case "pose":
                pass
            case _:
                return Ack(accepted=False, reason=f"sim2d: unknown intent {intent.kind}")
        return Ack()

    def _do(self, skill: str) -> Ack:
        w = self.world
        i = self.duck_index
        duck = w.ducks[i]
        if duck.posture == "fallen":
            return Ack(accepted=False, reason="the duck has fallen")
        match skill:
            case "kick_left" | "kick_right":
                if duck.posture != "standing":
                    return Ack(accepted=False, reason="cannot kick while sitting")
                w.kick("left" if skill == "kick_left" else "right", duck_index=i)
                return Ack()  # the kick *ran*; whether it connected shows in ball telemetry
            case "ground_pick":
                w.ground_pick(duck_index=i)
                return Ack()
            case "sit_toggle":
                w.sit_toggle(duck_index=i)
                return Ack()
            case "roulade":
                return Ack(accepted=True, reason="roulade is a no-op in sim2d")
            case _:
                return Ack(accepted=False, reason=f"unknown skill {skill!r}")

    async def subscribe(self, topic: str) -> AsyncIterator[dict[str, Any]]:  # type: ignore[override]
        while not self._closed:
            await self.sleep(DT)
            yield {"topic": topic, **(await self.get_state()).model_dump()}

    async def heartbeat(self) -> None:
        if self._closed:
            raise HeartbeatError("sim2d transport is closed")

    async def stop(self) -> None:
        self.world.stop(duck_index=self.duck_index)

    def now(self) -> float:
        return self.world.t

    async def sleep(self, seconds: float) -> None:
        try:
            await self.clock.sleep(self.pid, seconds)
        except HookInterrupt as e:
            from quackd.safety import Aborted  # local import: safety must stay clock-free

            raise Aborted(str(e)) from None
        if self.post_sleep is not None:
            self.post_sleep()


def make_flock(
    n: int,
    seed: int = 0,
    *,
    live: bool = False,
    realtime: bool | None = None,
    person: bool = True,
    frame_size: int = 256,
) -> list[Sim2DTransport]:
    """N transports sharing one world and one clock. Transport 0 owns the live window;
    attach a recorder through exactly one of them (tick hooks live on the shared clock)."""
    world = World(seed=seed, person=person, n_ducks=n)
    clock = FlockClock(world, realtime=live if realtime is None else realtime)
    return [
        Sim2DTransport(
            seed,
            live=live and i == 0,
            realtime=realtime,
            person=person,
            frame_size=frame_size,
            world=world,
            clock=clock,
            duck_index=i,
        )
        for i in range(n)
    ]
