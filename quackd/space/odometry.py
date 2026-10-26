"""Odometry read as a pose: relative motion since its frame began, cut wherever it had to begin
again.

Odometry is the robot's own estimate of how far it has gone, from its legs, wheels or a lidar,
with nothing to correct it: every metre walked can add error, and nothing takes it away. So its
frame is kept short. It lasts one connection, since nothing watches the robot between two, and it
starts again within one whenever the estimate stops meaning anything: the robot was picked up,
fell or went limp (contact odometry follows feet on the floor and nothing else), the robot's clock
went backwards (whatever produced the readings started over), or a step came that is longer than
the body could have walked in the time between two readings. Every place marked before that is in
a frame that has ended (`quackd.space.places`).
"""

from __future__ import annotations

import math
import statistics
from collections import deque
from collections.abc import Callable
from dataclasses import replace

from quackd.space.pose import Fix, FrameRef, Pose2D
from quackd.space.source import SpatialSourceBase

PERIOD_SAMPLES = 11
"""How many of the newest intervals between readings the period is judged by, quackd's own
number. Their median stays the stream's own while fewer than half of them are pauses."""


class OdometryTrack:
    """One connection's odometry readings, on the robot's own clock.

    `key` names the frame, and `epoch` counts the times it began again within the connection.
    `max_speed_mps` is the fastest the body can walk, from a figure its maker gave: a step longer
    than that times the time between two readings, with one measured period of slack for a
    reading that came late, is no walk, and the frame begins again. None trusts every step.
    `persistent` says the frame outlives the connection, for a body whose odometry is kept by
    something that does, as a rover's by the run of its own stack. `restarts_with_clock` False
    keeps the frame when the robot's clock goes backwards, for odometry whose start something
    else says, as that run: a date going back there is a clock being set or read loosely, and
    only the period is measured afresh."""

    def __init__(
        self,
        key: str,
        *,
        max_speed_mps: float | None = None,
        persistent: bool = False,
        restarts_with_clock: bool = True,
    ) -> None:
        self.key = key
        self.max_speed_mps = max_speed_mps
        self.persistent = persistent
        self.restarts_with_clock = restarts_with_clock
        self.epoch = 0
        self.walked_m = 0.0
        self.held: str | None = None
        """Why the robot is off its feet now, in words, or None while it stands on them."""
        self._pose: Pose2D | None = None
        self._t: float | None = None
        self._intervals: deque[float] = deque(maxlen=PERIOD_SAMPLES)

    @property
    def frame(self) -> FrameRef:
        return FrameRef("odometry", self.key, persistent=self.persistent, epoch=self.epoch)

    @property
    def period_s(self) -> float | None:
        """The time between two readings that moved the robot's clock on: the lower median of
        the newest intervals (`PERIOD_SAMPLES`). A mean let one pause stretch it: a stream that
        went quiet for half a minute read afterwards as a slow one, and a walk sized from that
        took a base three metres off for one already at its place. The lower of two middles,
        since a period read short only makes the windows sized from it tighter. None before two
        intervals, since one alone may be such a pause."""
        if len(self._intervals) < 2:
            return None
        return statistics.median_low(self._intervals)

    def update(
        self, x: float, y: float, yaw: float, t: float | None, *, held: str | None = None
    ) -> None:
        """One reading at `t` on the robot's clock, in seconds, or None where it sends none.
        `held` says why the robot is off its feet in this reading, or None while it is on them."""
        if not all(math.isfinite(v) for v in (x, y, yaw)):
            return  # a reading that is not a number moves nothing
        if t is not None and not math.isfinite(t):
            t = None
        pose = Pose2D(x, y, yaw)
        if held is not None:
            if self.held is None:
                self._begin_again()  # the moment it leaves its feet, the frame it walked in ends
            self.held = held
            self._remember(pose, t, count=False)
            return
        if self.held is not None:
            self.held = None  # back on its feet, in the frame that began when it left them
            self._remember(pose, t, count=False)
            return
        last, last_t = self._pose, self._t
        if last is not None and t is not None and last_t is not None:
            dt = t - last_t
            if dt < 0:
                if self.restarts_with_clock:
                    self._begin_again()
                self._intervals.clear()
            elif dt > 0:
                step = math.hypot(x - last.x, y - last.y)
                slack = self.period_s or dt
                if self.max_speed_mps is not None and step > self.max_speed_mps * (dt + slack):
                    self._begin_again()
                else:
                    self.walked_m += step
        self._remember(pose, t, count=True)

    def _remember(self, pose: Pose2D, t: float | None, *, count: bool) -> None:
        if count and t is not None and self._t is not None and t > self._t:
            self._intervals.append(t - self._t)
        self._pose, self._t = pose, t

    def _begin_again(self) -> None:
        self.epoch += 1
        self.walked_m = 0.0

    def reclock(self) -> None:
        """The readings are dated on another clock from the next one on, in the same frame: no
        step is judged across the change, and the period is measured afresh."""
        self._t = None
        self._intervals.clear()

    def fix(self) -> Fix:
        if self._pose is None:
            return Fix(None, "odometry", self.frame, lost="no odometry reading yet")
        if self.held is not None:
            return Fix(
                None, "odometry", self.frame, lost=self.held, t=self._t, walked_m=self.walked_m
            )
        return Fix(self._pose, "odometry", self.frame, t=self._t, walked_m=self.walked_m)


class OdometrySource(SpatialSourceBase):
    """A source that is odometry and nothing more: no grid, no map to wipe, no stops it needs.
    `stale` says why the newest reading cannot be trusted now (the link is gone, the stream
    stopped), or None, and a reading it names is no reading."""

    def __init__(
        self,
        track: OdometryTrack,
        *,
        stale: Callable[[], str | None] | None = None,
        label: str = "odometry",
    ) -> None:
        self.track = track
        self._stale = stale
        self.label = label

    @property
    def period_s(self) -> float | None:
        return self.track.period_s

    def fix(self) -> Fix:
        reading = self.track.fix()
        why = self._stale() if self._stale is not None else None
        if why is not None and reading.lost is None:
            return replace(reading, pose=None, lost=why)
        return reading

    def describe(self) -> str:
        return self.label
