"""Rotating the beacon id: a new random one every quarter of an hour, never in the middle of a
meeting.

An advert that carried the same id for ever would let anybody with a phone follow a robot, and so
its owner, from street to street, collecting the id wherever it is heard. So the id is 8 random
bytes derived from no key, and it is replaced every `period_s`: the advert, the `bt` in the LAN
record and the beacon the robot names in its hellos change together, as one. A robot in a link,
a session or a meeting keeps its id until that is over, because the robot it is talking to found
it by that id, and tries again every `retry_s`. Who a robot is never rests on the id: the
passport does that, and what a robot remembers of another (refusals, cooldowns) is kept by its
`did`, learned in the hello, so a rotation costs another robot one more hello at most.

What it does not do: a listener standing by a robot through a rotation hears one id stop and
another start at the same strength, and can join the two. Nor does it rotate the radio's own
address, which BlueZ rotates on a timer of its own when address privacy is on, and which a
public address never does; while the address stays public, a rotating beacon id alone does not
stop tracking. Designed to rotate and tested against a clock; not yet run over a real radio.
"""

from __future__ import annotations

import time
from collections.abc import Callable

from quackd.passport.beacon import is_beacon_id, new_beacon_id

ROTATE_S = 15 * 60.0
RETRY_S = 30.0


class BeaconRotor:
    """A robot's beacon id now, and when it changes. `tick` is all the clock it has."""

    def __init__(
        self,
        *,
        period_s: float = ROTATE_S,
        retry_s: float = RETRY_S,
        busy: Callable[[], bool],
        on_rotate: Callable[[str], None],
        clock: Callable[[], float] = time.monotonic,
        draw: Callable[[], str] = new_beacon_id,
        current: str | None = None,
    ) -> None:
        if period_s <= 0 or retry_s <= 0:
            raise ValueError("a rotation period and its retry are some seconds long")
        self.period_s, self.retry_s = period_s, retry_s
        self.busy, self.on_rotate, self.clock, self.draw = busy, on_rotate, clock, draw
        self.current = current if current is not None else draw()
        if not is_beacon_id(self.current):
            raise ValueError(f"{self.current!r} is not a beacon id")
        self.next_at = clock() + period_s
        #: how many rotations so far, and how many were put off for a meeting
        self.rotations = 0
        self.deferred = 0

    def tick(self, now: float | None = None) -> str | None:
        """The new beacon id when it is time and nothing is open, else None. When something is
        open the rotation waits `retry_s` and asks again."""
        now = self.clock() if now is None else now
        if now < self.next_at:
            return None
        if self.busy():
            self.deferred += 1
            self.next_at = now + self.retry_s
            return None
        fresh = self.draw()
        while fresh == self.current:  # two draws of 8 random bytes alike: draw again
            fresh = self.draw()
        self.current, self.next_at = fresh, now + self.period_s
        self.rotations += 1
        self.on_rotate(fresh)
        return fresh

    def due_in(self, now: float | None = None) -> float:
        """Seconds until the next rotation is tried, for a status line."""
        return max(0.0, self.next_at - (self.clock() if now is None else now))


__all__ = ["RETRY_S", "ROTATE_S", "BeaconRotor"]
