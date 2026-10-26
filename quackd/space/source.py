"""What a body offers when it knows where it is.

A transport that can say where its robot is carries a `spatial` attribute that is a
`SpatialSource`, and `quackd.transport.base.spatial_of` is how everything else asks for it. One
source lives as long as one connection: a reconnect is a new source, and an odometry frame does not
outlive it.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from quackd.space.grid import OccupancyGrid
from quackd.space.pose import Fix, FrameRef

if TYPE_CHECKING:
    from quackd.space.places import FrameRecord


class SpatialError(RuntimeError):
    """A source could not do what it was asked: it keeps no map to give or to wipe."""


@runtime_checkable
class SpatialSource(Protocol):
    """One body's reading of where it is.

    `fix()` never waits: it hands back the newest reading the transport holds, which is what a
    walk reads every tick. The rest describes the source, each None where the body does not say:

    - `period_s`: the measured time between readings, which freshness and tolerances come from;
    - `footprint_m`: the radius a planner grows walls by, from a figure somebody cited;
    - `stop_every_m`: how far to walk between stops, for a map that corrects itself only at stops;
    - `settle_timeout_s`: how long to stand at a stop for the map to take its view;
    - `keyframes_follow_head`: whether a view the head turns to is one the map takes.
    """

    @property
    def period_s(self) -> float | None: ...

    @property
    def footprint_m(self) -> float | None: ...

    @property
    def stop_every_m(self) -> float | None: ...

    @property
    def settle_timeout_s(self) -> float | None: ...

    @property
    def keyframes_follow_head(self) -> bool: ...

    @property
    def has_grid(self) -> bool: ...

    @property
    def can_wipe(self) -> bool: ...

    def fix(self) -> Fix: ...

    async def grid(self, res_m: float | None = None) -> OccupancyGrid | None: ...

    async def wipe(self) -> int: ...

    def resolve_frame(self, records: Sequence[FrameRecord]) -> FrameRef | None: ...

    def describe(self) -> str: ...


class SpatialSourceBase:
    """A source with no grid and nothing to wipe, whose frame is the one its readings carry,
    and which says nothing of its period, footprint or stops. Every source here starts from it
    and overrides what its body offers."""

    @property
    def period_s(self) -> float | None:
        return None

    @property
    def footprint_m(self) -> float | None:
        return None

    @property
    def stop_every_m(self) -> float | None:
        return None

    @property
    def settle_timeout_s(self) -> float | None:
        return None

    @property
    def keyframes_follow_head(self) -> bool:
        return False

    @property
    def has_grid(self) -> bool:
        return False

    @property
    def can_wipe(self) -> bool:
        return False

    def fix(self) -> Fix:
        raise NotImplementedError

    async def grid(self, res_m: float | None = None) -> OccupancyGrid | None:
        return None

    async def wipe(self) -> int:
        raise SpatialError(f"{self.describe()} keeps no map to wipe")

    def resolve_frame(self, records: Sequence[FrameRecord]) -> FrameRef | None:
        """The frame places are compared in. A source whose frames outlive a connection may
        match its current one against frames the place store remembers (a map recognised
        again); every other source's frame is simply the one its readings carry."""
        return self.fix().frame

    def describe(self) -> str:
        return type(self).__name__
