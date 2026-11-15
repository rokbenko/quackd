"""The blackboard: a tiny pub/sub bus every team message crosses exactly once.

In-process by default; `roboglot.team.mqtt_bus.MqttBus` fills the same two-method `Bus`
protocol over a broker (0.4, library-only). One hard rule: **nobody ever awaits the bus**.
Publishing is a synchronous fan-out to per-subscriber queues; each pilot drains its own between
turns, so a quiet peer never holds up a loud one.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable
from typing import Protocol

from roboglot.team.messages import TeamMessage


class Subscription:
    def __init__(self, subscriber_id: str, on_close: Callable[[Subscription], None]) -> None:
        self.subscriber_id = subscriber_id
        self._queue: deque[TeamMessage] = deque()
        self._on_close = on_close
        self.closed = False

    def _push(self, msg: TeamMessage) -> None:
        if not self.closed:
            self._queue.append(msg)

    def drain(self) -> list[TeamMessage]:
        """Everything queued, non-blocking, publish order preserved.

        A `popleft` loop rather than copy-then-clear: `deque.append` and `popleft` are
        each atomic, so a producer on another thread (the MQTT bus, before a message is
        marshalled onto the loop) can never have its message cleared unseen."""
        out: list[TeamMessage] = []
        queue = self._queue
        while queue:
            try:
                out.append(queue.popleft())
            except IndexError:  # emptied under us between the check and the pop
                break
        return out

    def close(self) -> None:
        self.closed = True
        self._queue.clear()
        self._on_close(self)


class Bus(Protocol):
    def publish(self, msg: TeamMessage) -> None: ...

    def subscribe(self, subscriber_id: str) -> Subscription: ...


class InProcessBus:
    def __init__(self, *, tap: Callable[[TeamMessage], None] | None = None) -> None:
        self._subs: list[Subscription] = []
        self._tap = tap
        self.published = 0

    def publish(self, msg: TeamMessage) -> None:
        self.published += 1
        if self._tap is not None:
            self._tap(msg)
        for sub in self._subs:
            if sub.subscriber_id != msg.src:  # no echo to the sender
                sub._push(msg)

    def subscribe(self, subscriber_id: str) -> Subscription:
        sub = Subscription(subscriber_id, self._subs.remove)
        self._subs.append(sub)
        return sub
