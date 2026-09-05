"""One interface for "a duck", whether it is a mock, a cartoon, or 800 g of servos.

The protocol is small on purpose: frames in, state in, intents out, plus a heartbeat and a
stop. Time is part of the interface (`now`/`sleep`) so the simulator can run faster than
real time while the real robot keeps its deadman fed.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any, Literal, Protocol, runtime_checkable

from PIL import Image
from pydantic import BaseModel, ConfigDict, Field

Posture = Literal["standing", "sitting", "fallen", "unknown"]
IntentKind = Literal["move", "stop", "do", "look", "sound", "enable", "pose", "joint", "gripper"]

# Neutral skill vocabulary. These strings are upstream's own (`duck-ipc-proto` `Skill` enum);
# see `upstream_api.SKILLS`. The sim interprets the same names.
Skill = Literal["ground_pick", "kick_left", "kick_right", "sit_toggle", "roulade"]


class TransportError(RuntimeError):
    """The transport could not do what was asked (connection, refusal, protocol).

    `code` carries the upstream error number when there was one, so a caller can tell a
    "busy, try again" from a "not allowed, do not" without reading the message text. It is
    None for everything that failed before an answer arrived.
    """

    def __init__(self, *args: object, code: int | None = None) -> None:
        super().__init__(*args)
        self.code = code


class HeartbeatError(TransportError):
    """A heartbeat failed. The caller must stop the robot and abort."""


class DuckState(BaseModel):
    """A compact snapshot the LLM can read in one glance.

    `x, y, theta` are only known in sim (or from upstream odometry, which drifts); real
    robots may leave them `None`. `extras` carries transport-specific detail without
    forcing it into the contract.
    """

    model_config = ConfigDict(extra="forbid")

    t: float = 0.0
    x: float | None = None
    y: float | None = None
    theta: float | None = None
    policy: str = "unknown"
    posture: Posture = "unknown"
    fallen: bool = False
    battery_percent: float | None = None
    holding: bool = False
    extras: dict[str, Any] = Field(default_factory=dict)

    def summary(self) -> str:
        parts = [f"posture={self.posture}", f"policy={self.policy}"]
        if self.fallen:
            parts.append("FALLEN")
        # An unchanging `posture=unknown` reads as "no news". It is not: on a backend where
        # nothing watches for falls, `fallen=False` is silence, and a pilot told elsewhere
        # that moving verbs refuse when it is down will read an accepted move as proof it is
        # upright. Say so in every observation instead. Backends that always know (the
        # simulator, the mock) set no such key and are unchanged.
        if self.extras.get("fall_detection") is False:
            parts.append("fall-blind=nothing-detects-falls")
        if self.extras.get("state_stale"):
            parts.append("state=UNREADABLE")
        if self.battery_percent is not None:
            parts.append(f"battery={self.battery_percent:.0f}%")
        if self.holding:
            parts.append("holding=object-in-beak")
        if self.x is not None and self.y is not None and self.theta is not None:
            parts.append(f"pose=({self.x:.2f}, {self.y:.2f}, {self.theta:.2f} rad)")
        return " ".join(parts)


class Ack(BaseModel):
    accepted: bool = True
    reason: str | None = None


class Intent(BaseModel):
    """What clients are allowed to say to a robot: intents, never motor writes."""

    model_config = ConfigDict(extra="forbid")

    kind: IntentKind
    params: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def move(cls, vx: float = 0.0, vy: float = 0.0, wz: float = 0.0) -> Intent:
        return cls(kind="move", params={"vx": vx, "vy": vy, "wz": wz})

    @classmethod
    def stop(cls) -> Intent:
        return cls(kind="stop")

    @classmethod
    def do(cls, skill: str) -> Intent:
        """A named skill. On the Microduck one of `Skill`; other adapters name their own."""
        return cls(kind="do", params={"skill": skill})

    @classmethod
    def joint(cls, positions: dict[str, float], duration_s: float = 1.0) -> Intent:
        """Joint targets for an arm (0.4). The Microduck never receives this kind."""
        return cls(kind="joint", params={"positions": positions, "duration_s": duration_s})

    @classmethod
    def gripper(cls, open: bool) -> Intent:
        return cls(kind="gripper", params={"open": open})

    @classmethod
    def look(cls, x: float, y: float, z: float) -> Intent:
        return cls(kind="look", params={"x": x, "y": y, "z": z})

    @classmethod
    def sound(cls, tag: str, text: str | None = None) -> Intent:
        return cls(kind="sound", params={"tag": tag, "text": text})

    @classmethod
    def enable(cls, on: bool = True) -> Intent:
        return cls(kind="enable", params={"on": on})

    def describe(self) -> str:
        if not self.params:
            return self.kind
        inner = ", ".join(f"{k}={v!r}" for k, v in self.params.items() if v is not None)
        return f"{self.kind}({inner})"


@runtime_checkable
class DuckTransport(Protocol):
    """Every transport implements exactly this. Verbs see nothing else."""

    name: str

    async def connect(self) -> Any:
        """Open the link. An adapter (0.4) returns its `RobotManifest`; a bare transport
        returns None and the caller falls back to the Microduck vocabulary."""
        ...

    async def close(self) -> None: ...

    async def get_frame(self) -> Image.Image | None:
        """The duck's camera view, or None if this transport has no camera."""
        ...

    async def get_state(self) -> DuckState: ...

    async def send_intent(self, intent: Intent) -> Ack: ...

    def subscribe(self, topic: str) -> AsyncIterator[dict[str, Any]]: ...

    async def heartbeat(self) -> None:
        """Raise `HeartbeatError` if the duck is unreachable or unhealthy."""
        ...

    async def stop(self) -> None:
        """Zero velocity. Safe to call many times, from anywhere, at any time."""
        ...

    def now(self) -> float:
        """Transport time in seconds (sim time for the simulator, monotonic for hardware)."""
        ...

    async def sleep(self, seconds: float) -> None:
        """Let `seconds` of transport time pass (advancing the sim, or actually waiting)."""
        ...
