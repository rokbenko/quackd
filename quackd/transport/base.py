"""One interface for "a duck", whether it is a mock, a cartoon, or 800 g of servos.

The protocol is small on purpose: frames in, state in, intents out, plus a heartbeat and a
stop. Time is part of the interface (`now`/`sleep`) so the simulator can run faster than
real time while the real robot keeps its deadman fed.
"""

from __future__ import annotations

import math
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, Protocol, runtime_checkable

from PIL import Image
from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from quackd.space.source import SpatialSource

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


DEFAULT_CAMERA_NAME = "camera"
"""What a single frame is called when the transport names no camera. Never shown to a
model: a camera's name is only spoken when a body has more than one."""

FRAME_HEAD_YAW_DEG = "quackd.head_yaw_deg"
"""The key in a frame's `info` under which a body that knows it records where its camera pointed
from the trunk when the frame was taken, in degrees of yaw, positive left, the convention
`DuckState.extras["head_yaw_deg"]` is in. That one says where the camera points now, and a frame
handed out from memory can be older than the head's last move (`frame_head_yaw_of`)."""


@dataclass(frozen=True)
class CameraFrame:
    """One camera's newest picture, and which camera it came from.

    `name` is what the model, the transcript file and a policy's observation tell two
    views apart by, so it is required here even though a one-camera body never shows it.

    `primary` is carried rather than inferred from the position in the list. A camera that
    gave nothing is absent from that list, so on a two-camera arm whose primary lens died the
    first entry is the *other* camera, and a detector reading it would report bearings off a
    lens `--fov-deg` never measured."""

    name: str
    image: Image.Image
    primary: bool = False


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
        # A pointer, not the list. The agent loop puts the sentences in the system prompt,
        # where there is room for them, but `mcp_server.py` has no system prompt at all: a
        # Claude Desktop pilot reads tool results only, and `report_state` returns this line
        # with `extras` beside it. Without this, that pilot is told nothing at all.
        if assumptions := self.extras.get("assumptions"):
            parts.append(f"stand-ins={len(assumptions)}-listed-in-extras.assumptions")
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
    """Every transport implements exactly this. Verbs see nothing else.

    A transport whose body has several cameras may also offer
    `get_frames() -> list[CameraFrame]`. It is not part of this protocol, because a
    protocol cannot carry an optional member: `frames_of()` is how callers ask, and a
    transport that has not got one is a transport with a single camera. One that has must
    mark its primary camera's frame `primary=True`, and mark none when that camera gave
    nothing this time, because that flag is what the detector reads.

    A transport that hands out frames taken ahead of being asked for, and knows when each was
    taken, may offer `frame_taken_at() -> float | None` as well, and `frame_period_s`, how long
    one frame takes to follow the last. `frame_taken_at_of()` and `frame_period_of()` are how
    callers ask, and a transport without them keeps every verb as it was. One that offers
    `frame_taken_at` must stop handing out a frame once it is too old to steer on, since that
    is what ends a wait for a newer one that never comes (`frames_taken_since`)."""

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


async def frames_of(transport: Any) -> list[CameraFrame]:
    """Every camera's newest frame, the primary first.

    `get_frames()` where the transport has one, otherwise `get_frame()` wrapped as a single
    frame, so a body with one camera and a body with four are read the same way. An empty
    list means no picture, which is `get_frame`'s own contract: this never raises for a
    camera that failed, because every caller is an observation or a teardown."""
    getter = getattr(transport, "get_frames", None)
    if callable(getter):
        return list(await getter())
    image = await transport.get_frame()
    return [] if image is None else [CameraFrame(DEFAULT_CAMERA_NAME, image, primary=True)]


def camera_names_of(transport: Any) -> list[str]:
    """Every camera this body has, in order, whether or not it answered this step.

    The question every caller that renders a frame has to ask, and the one it is easy to ask
    wrong. How many pictures arrived is not how many cameras there are: a two-camera arm whose
    top lens stalls hands back one frame, and deciding from that count alone is how the
    survivor loses the label that says which lens it came from. The body's own list does not
    move when a camera does."""
    keys = getattr(transport, "camera_keys", None)
    return [str(key) for key in keys] if keys else []


def primary_of(frames: Sequence[CameraFrame]) -> Image.Image | None:
    """The view the detections describe, or None when that camera gave nothing this time.

    None with frames still in the list is a real state: the other cameras are worth showing a
    model even when the one the bearings are calibrated for has stopped answering."""
    return next((f.image for f in frames if f.primary), None)


def _finite(value: Any) -> float | None:
    if isinstance(value, int | float) and not isinstance(value, bool) and math.isfinite(value):
        return float(value)
    return None


def frame_taken_at_of(transport: Any) -> float | None:
    """When the frame `transport.get_frame()` would hand out now was taken, at the earliest, on
    the clock `transport.now()` reads, or None when the body does not say.

    A body that draws its frame when it is asked for has nothing to say, since every frame it
    hands out is taken then, and a body that cannot tell when a frame it kept was taken says
    nothing rather than guess. Either way the caller acts on the frame it is handed, as every
    caller did before any body said. A body that keeps frames taken ahead of being asked for,
    and knows when, offers `frame_taken_at()`: a Microduck on mediad's `/frame` does."""
    taken_at = getattr(transport, "frame_taken_at", None)
    return _finite(taken_at()) if callable(taken_at) else None


def frame_period_of(transport: Any) -> float | None:
    """How long one frame of the body's camera takes to follow the last, as it runs now, in
    seconds, or None when the body does not say.

    A body that draws its frame when it is asked for has no period, and one that streams its
    frames faster than a verb looks at them has none worth saying. A body that hands out frames
    at a pace of its own, and says when each was taken (`frame_taken_at_of`), offers
    `frame_period_s`, which `go_to` reads to tell whether its steering law keeps up."""
    period = _finite(getattr(transport, "frame_period_s", None))
    return period if period is not None and period > 0 else None


def spatial_of(transport: Any) -> SpatialSource | None:
    """The robot's reading of where it is, or None when its body does not say.

    A transport that knows where its robot is carries a `spatial` attribute, the source for this
    connection (`quackd.space.source`): odometry, a robot's own map, a simulator's. A transport
    without one is a body quackd has no pose for, and the place verbs are not offered on it. An
    adapter that wraps a transport has to pass the attribute on, as it passes every other one a
    caller asks for by name."""
    source = getattr(transport, "spatial", None)
    return source if source is not None and callable(getattr(source, "fix", None)) else None


def frame_fetch_of(transport: Any) -> float | None:
    """How long the body's newest fetch of a frame took, in seconds, answered or not, or None when
    the body does not fetch its frames one at a time or does not say.

    A body that pulls each frame with a request of its own, as a Microduck on mediad's `/frame`
    does, offers `frame_fetch_s`. `watch` reads it to size how long it waits for a frame taken
    after the head settled: a fetch that takes a second puts the first such frame up to two
    fetches away."""
    fetch = _finite(getattr(transport, "frame_fetch_s", None))
    return fetch if fetch is not None and fetch >= 0 else None


def frame_asked_at_of(transport: Any) -> float | None:
    """When the body's newest fetch of a frame was asked for, on the clock `transport.now()`
    reads, whether or not it has answered yet, or None when the body does not say.

    `watch` reads it when a wait for a fresh frame runs out: a fetch asked for inside the wait
    and still out is a slow camera, and none asked for at all is a camera that has stopped."""
    asked_at = getattr(transport, "frame_asked_at", None)
    return _finite(asked_at()) if callable(asked_at) else None


async def frames_taken_since(transport: Any, since: float, poll_s: float) -> None:
    """Wait until the frame the body would hand out was taken no earlier than `since` on its own
    clock, checking every `poll_s` of its time, on a body that says when its frames were taken.

    It returns at once on any other body, and as soon as the body has no frame left to hand out,
    whose `get_frame` then says None and whose verb stops on that. A body that says when its
    frames were taken stops handing out one too old to steer on, which is what bounds the wait
    on a camera that has stopped: the frame taken before `since` runs out of age, and nothing
    newer comes."""
    while (taken := frame_taken_at_of(transport)) is not None and taken < since:
        await transport.sleep(poll_s)


def twist_shown_since_of(transport: Any, since: float) -> bool | None:
    """Whether the body's own state has shown that it took a twist the calling verb sent no
    earlier than `since`, on the clock `transport.now()` reads, or None when the body does not
    say.

    Most bodies answer a twist they cannot take with a refusal, so a twist they accepted is one
    they took, and they say nothing here. A body whose twists go out with no answer, as a
    Microduck's `robot.move` does, can say only afterwards, from what its state shows it was
    asked for, and offers `twist_shown_since(since)`, which `search_scan` reads to count the
    turns it made by what the body took rather than by what it sent. Asked from inside the
    verb, so the body can tell the calling verb's twists from another call's."""
    shown = getattr(transport, "twist_shown_since", None)
    return bool(shown(since)) if callable(shown) else None


def frame_head_yaw_of(image: Image.Image | None) -> float | None:
    """Where the camera pointed from the trunk when `image` was taken, in degrees of yaw positive
    left, when the body recorded it on the frame (`FRAME_HEAD_YAW_DEG`), or None."""
    return None if image is None else _finite(image.info.get(FRAME_HEAD_YAW_DEG))
