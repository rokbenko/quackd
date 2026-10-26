"""A pose on the floor, the frame it belongs to, and one reading of where a robot is.

Nothing here knows any robot. A source (`quackd.space.source`) turns what its body reports into a
`Fix`; everything above reads the `Fix`.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Literal

LocalizationKind = Literal["map", "odometry"]
"""How a pose was found. `map`: in a map that corrects itself and outlives the connection, which
a robot can be lost in and found again. `odometry`: relative motion since the frame began, which
drifts with every metre walked and begins again whenever the robot leaves its feet; while it is
off them there is no pose (`quackd.space.odometry`)."""


def wrap_angle(rad: float) -> float:
    """An angle in (-pi, pi]."""
    wrapped = math.remainder(rad, math.tau)
    return math.pi if wrapped == -math.pi else wrapped


@dataclass(frozen=True)
class Pose2D:
    """Metres and radians on the floor: x forward of the frame's origin, y to its left, yaw
    counter-clockwise from x."""

    x: float
    y: float
    yaw: float

    def compose(self, other: Pose2D) -> Pose2D:
        """`other`, given in this pose's frame, in the frame this pose is given in."""
        c, s = math.cos(self.yaw), math.sin(self.yaw)
        return Pose2D(
            self.x + c * other.x - s * other.y,
            self.y + s * other.x + c * other.y,
            wrap_angle(self.yaw + other.yaw),
        )

    def inverse(self) -> Pose2D:
        c, s = math.cos(self.yaw), math.sin(self.yaw)
        return Pose2D(-c * self.x - s * self.y, s * self.x - c * self.y, wrap_angle(-self.yaw))

    def relative_to(self, origin: Pose2D) -> Pose2D:
        """This pose as `origin` sees it: in the frame whose origin is `origin`."""
        return origin.inverse().compose(self)

    def distance_to(self, x: float, y: float) -> float:
        return math.hypot(x - self.x, y - self.y)

    def bearing_to(self, x: float, y: float) -> float:
        """Where (x, y) is from this pose's heading, in (-pi, pi], positive to the left."""
        return wrap_angle(math.atan2(y - self.y, x - self.x) - self.yaw)


@dataclass(frozen=True)
class FrameRef:
    """The frame a pose belongs to. `key` names it (`odom:<connection>`, `map:<id>`,
    `sim:<digest>`), `persistent` says whether it outlives the connection, and `epoch` counts the
    times an odometry frame had to start again within one connection: a pick-up, a fall, a step
    no gait could make."""

    kind: LocalizationKind
    key: str
    persistent: bool
    epoch: int = 0

    @property
    def id(self) -> str:
        """What a place records: the key, and the epoch once there has been one."""
        return self.key if self.epoch == 0 else f"{self.key}#{self.epoch}"


@dataclass(frozen=True)
class Fix:
    """One reading of where the robot is.

    `pose` is None while the robot does not know, and `lost` then says why in quackd's words.
    `t` is the robot's own clock in seconds, which freshness is judged by: a stream whose `t`
    stops advancing is frozen, however many messages keep arriving. `keyframes` and `collecting`
    are a mapper's (how many stops its map holds, and whether a stop is being taken now), and
    `walked_m` is the distance walked in this frame, which is how far an odometry pose may have
    drifted from the truth."""

    pose: Pose2D | None
    kind: LocalizationKind
    frame: FrameRef | None
    lost: str | None = None
    t: float | None = None
    keyframes: int | None = None
    collecting: bool = False
    walked_m: float = 0.0

    @property
    def localized(self) -> bool:
        return self.pose is not None and self.frame is not None and self.lost is None


def where_record(fix: Fix) -> dict[str, Any]:
    """The one shape a reading takes outside this package: `DuckState.extras["where"]`, a memory
    note's `where`, a verb's result. Rounded to what the pose can be trusted to, and nothing a
    reading does not say."""
    out: dict[str, Any] = {"kind": fix.kind}
    if fix.frame is not None:
        out["frame"] = fix.frame.id
    if fix.pose is not None and fix.lost is None:
        out.update(x=round(fix.pose.x, 3), y=round(fix.pose.y, 3), yaw=round(fix.pose.yaw, 3))
    if fix.lost is not None:
        out["lost"] = fix.lost
    if fix.kind == "odometry":
        out["walked_m"] = round(fix.walked_m, 2)
    if fix.keyframes is not None:
        out["keyframes"] = fix.keyframes
    return out


def where_summary(record: dict[str, Any]) -> str:
    """The `where=` a state summary carries, from `where_record`'s shape: `map(x, y, yaw rad)`,
    `odometry(x, y, yaw rad, walked N m)`, or `lost(why)`."""
    if record.get("lost"):
        return f"lost({record['lost']})"
    if not all(isinstance(record.get(k), int | float) for k in ("x", "y", "yaw")):
        return "unknown"
    pose = f"{record['x']:.2f}, {record['y']:.2f}, {record['yaw']:.2f} rad"
    if record.get("kind") == "odometry":
        return f"odometry({pose}, walked {float(record.get('walked_m', 0.0)):.1f} m)"
    return f"map({pose})"
