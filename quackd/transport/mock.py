"""A duck that does exactly what the test tells it to.

MockTransport records every intent, serves scripted frames and states, and can be told to
fail its heartbeat after N beats — which is how the safety layer gets tested without a
robot to endanger.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

from PIL import Image

from quackd.transport.base import Ack, DuckState, HeartbeatError, Intent


class MockTransport:
    name = "mock"
    frames_upright = True
    """Its frames are whatever the test hands it, which are the right way up by saying so."""

    def __init__(
        self,
        *,
        frames: list[Image.Image] | None = None,
        states: list[DuckState] | None = None,
        fail_heartbeat_after: int | None = None,
        refuse_kinds: set[str] | None = None,
        frame_size: tuple[int, int] = (64, 64),
    ) -> None:
        self._frames = frames or []
        self._states = states or [DuckState(policy="mock", posture="standing", battery_percent=88)]
        self._fail_after = fail_heartbeat_after
        self._refuse = refuse_kinds or set()
        self._frame_size = frame_size
        self._t = 0.0
        self.intents: list[Intent] = []
        self.heartbeats = 0
        self.stops = 0
        self.sleeps: list[float] = []
        self.connected = False
        self._frame_i = 0
        self._state_i = 0

    async def connect(self) -> None:
        self.connected = True

    async def close(self) -> None:
        self.connected = False

    async def get_frame(self) -> Image.Image | None:
        if self._frames:
            img = self._frames[min(self._frame_i, len(self._frames) - 1)]
            self._frame_i += 1
            return img
        return Image.new("RGB", self._frame_size, (40, 40, 40))

    async def get_state(self) -> DuckState:
        state = self._states[min(self._state_i, len(self._states) - 1)]
        self._state_i += 1
        return state.model_copy(update={"t": self._t})

    async def send_intent(self, intent: Intent) -> Ack:
        self.intents.append(intent)
        if intent.kind in self._refuse:
            return Ack(accepted=False, reason=f"mock refuses {intent.kind}")
        return Ack()

    async def subscribe(self, topic: str) -> AsyncIterator[dict[str, Any]]:  # type: ignore[override]
        for state in self._states:
            yield {"topic": topic, **state.model_dump()}

    async def heartbeat(self) -> None:
        self.heartbeats += 1
        if self._fail_after is not None and self.heartbeats > self._fail_after:
            raise HeartbeatError("mock heartbeat failure (scripted)")

    async def stop(self) -> None:
        self.stops += 1
        self.intents.append(Intent.stop())

    def now(self) -> float:
        return self._t

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self._t += seconds
        await asyncio.sleep(0)  # yield so the heartbeat task gets a turn

    # ── test helpers ────────────────────────────────────────────────────────────────

    def intents_of(self, kind: str) -> list[Intent]:
        return [i for i in self.intents if i.kind == kind]
