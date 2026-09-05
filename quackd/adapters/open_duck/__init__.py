"""The Open Duck Mini v2 adapter: an open-hardware biped that walks on its own ONNX policy.

Three backends behind one manifest: `sim2d` (a duck in the cartoon world), `mock` (scripted,
for tests) and `bridge` (a real duck through the quackd bridge daemon on its Raspberry Pi,
EXPERIMENTAL and never run on hardware by us).

This is the first robot quackd supports whose upstream has **no network control API at all**.
The runtime reads a local pygame gamepad and runs a 50 Hz ONNX walk policy; the only socket
in it is an IMU check. So quackd ships a small daemon that runs on the robot, replaces the
gamepad as the walk loop's command source, and speaks a protocol quackd itself defines. Every
name the daemon borrows from the robot's own runtime is a VERIFIED or UNVERIFIED ref in
`upstream_api.py` (ADR-0022, ADR-0024).

What this duck cannot do matters as much as what it can. It has no beak, no gripper, no kick
policy, no sit policy and, critically, no get-up-after-fall policy. Those verbs are not gated
here, they are simply never declared, so they do not exist anywhere in quackd for this robot.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from typing import Any

from PIL import Image

from quackd.adapters.manifest import (
    Frame,
    Health,
    RobotManifest,
    SafetyAuthority,
    verb_spec,
)
from quackd.adapters.open_duck.verbs import (
    GAZE_YAW_DEG,
    HEAD_PITCH_RANGE,
    HEAD_ROLL_RANGE,
    HEAD_YAW_RANGE,
    MAX_VX,
    MAX_VY,
    MAX_WZ,
    NECK_PITCH_RANGE,
    open_duck_conditions,
    open_duck_verbs,
)
from quackd.transport.base import Ack, DuckState, DuckTransport, HeartbeatError, Intent
from quackd.verbs.core import CORE
from quackd.verbs.registry import Precondition, Verb

BACKENDS = ("sim2d", "mock", "bridge")
DEFAULT_ID = "open-duck-01"
CONTROL_HZ = 50

BLURB = (
    "a 3D-printed biped duck robot about 42 cm tall that walks on its own 50 Hz policy "
    "(an Open Duck Mini v2). It is slow, it cannot pick anything up, and it cannot get "
    "back up on its own if it falls"
)
_MOVE_DESCRIPTION = (
    "Walk with a velocity for a duration. This robot is deliberately slow: forward speed is "
    "clamped near 0.15 m/s and the turn rate near 1 rad/s, and its own walk policy makes the "
    "gait. Use short durations."
)
_SEARCH_SCAN_DESCRIPTION = (
    "Look around for a target by turning in place in steps. This robot has legs, so this "
    "WALKS: it turns the whole body, it does not sweep the head. Returns where it was seen."
)
_GO_TO_DESCRIPTION = (
    "Walk toward a detected target and stop at a distance. Closes the loop on the camera "
    "itself. Slow: expect this to take a while."
)
# the core wording offers `kick, grab`, which this robot does not have at all
_APPROACH_AND_DESCRIPTION = (
    "Walk to a target, then run another verb there (say, quack, express, observe)."
)


def open_duck_manifest(
    backend: str,
    robot_id: str | None = None,
    *,
    camera: bool = True,
    speaker: bool = True,
    antennas: bool = True,
    microphone: bool = False,
    head: bool = True,
    bridge_version: str | None = None,
    runtime_commit: str | None = None,
    deadman: bool = True,
    deadman_ms: int | None = None,
) -> RobotManifest:
    """The Open Duck Mini v2 as data.

    The static manifest describes a fully built duck: a camera, a speaker and antennas. A
    real one is whatever its owner soldered, so the `bridge` backend narrows this at
    `connect()` from the `expression_features` flags in the robot's own `duck_config.json`
    and from whether the daemon was started with head control enabled."""
    own = open_duck_verbs()
    verbs = [
        verb_spec(CORE["report_state"], core=True),
        verb_spec(CORE["stop"], core=True),
        verb_spec(CORE["move"], core=True, description=_MOVE_DESCRIPTION),
    ]
    preconditions: dict[str, list[str]] = {"move": ["not_fallen", "policy_running"]}
    if camera:
        verbs += [
            verb_spec(CORE["observe"], core=True),
            verb_spec(CORE["go_to"], core=True, description=_GO_TO_DESCRIPTION),
            verb_spec(CORE["search_scan"], core=True, description=_SEARCH_SCAN_DESCRIPTION),
            verb_spec(CORE["approach_and"], core=True, description=_APPROACH_AND_DESCRIPTION),
        ]
        for name in ("go_to", "search_scan", "approach_and"):
            preconditions[name] = ["not_fallen", "policy_running"]
    if speaker:
        verbs += [verb_spec(own["say"], core=True), verb_spec(own["quack"], core=False)]
    if head:
        verbs.append(verb_spec(own["gaze"], core=False))
        preconditions["gaze"] = ["not_fallen"]
    if antennas:
        verbs.append(verb_spec(own["express"], core=False))
        preconditions["express"] = ["not_fallen"]

    intents: list[Any] = ["twist"]
    if head:
        intents.append("gaze")
    if speaker:
        intents.append("sound")
    if antennas:
        intents.append("skill")

    sensors: list[Any] = ["imu", "joint_state"]
    if camera:
        sensors.append("camera")
    if microphone:
        sensors.append("microphone")

    return RobotManifest(
        id=robot_id or DEFAULT_ID,
        vendor="apirrone",
        model="open-duck-mini-v2",
        embodiment="biped",
        mobility="legged",
        intents=intents,
        sensors=sensors,
        verbs=verbs,
        preconditions=preconditions,
        # The robot's own runtime has no deadman: its only command source is a local pygame
        # pad, which is never silent. The deadman is quackd's own bridge daemon, which zeroes
        # the velocity inside the 50 Hz loop when commands stop, so `native` stays `none`:
        # the authority is our code, not a robot feature (ADR-0024). Whether there is one at
        # all now comes from what the bridge reported at connect rather than from this
        # literal: the command-TTL note in upstream_api says the manifest claims a deadman
        # only when a bridge says it has one, and it used to claim it regardless.
        safety_authority=SafetyAuthority(native="none", deadman=deadman, heartbeat_hz=2.0),
        frame=Frame(
            reference="body",
            note="trunk frame: vx forward, vy left, +vyaw left. The head is four joints "
            "(neck_pitch, head_pitch, head_yaw, head_roll) and barely turns",
        ),
        limits={
            "max_vx": MAX_VX,
            "max_vy": MAX_VY,
            "max_wz": MAX_WZ,
            "gaze_yaw_deg": round(GAZE_YAW_DEG, 1),
        },
        backend=backend,
        blurb=BLURB,
        extras={
            # no text to speech anywhere in the runtime: say() plays one of the duck's sounds
            "speech": "sounds",
            "control_hz": CONTROL_HZ,
            "policy": "onnx-walk",
            "joints": 14,
            "head_dof": ["neck_pitch", "head_pitch", "head_yaw", "head_roll"],
            "head_ranges_rad": {
                "neck_pitch": list(NECK_PITCH_RANGE),
                "head_pitch": list(HEAD_PITCH_RANGE),
                "head_yaw": list(HEAD_YAW_RANGE),
                "head_roll": list(HEAD_ROLL_RANGE),
            },
            "head_enabled": head,
            "expression_features": {
                "camera": camera,
                "speaker": speaker,
                "antennas": antennas,
                "microphone": microphone,
            },
            # the two facts that shape every task written for this robot
            "no_recovery": "a fallen Open Duck Mini v2 has no get-up policy and needs a human",
            "no_battery": "nothing in the runtime reports a battery, so battery aborts cannot fire",
            "bridge_version": bridge_version,
            "runtime_commit": runtime_commit,
            "deadman_ms": deadman_ms,
        },
    )


class OpenDuckAdapter:
    """A `RobotAdapter` over one of the three Open Duck Mini v2 backends."""

    name = "open_duck"

    def __init__(self, transport: DuckTransport, *, robot_id: str | None = None) -> None:
        self.transport = transport
        self.backend = transport.name
        self.robot_id = robot_id or DEFAULT_ID
        self.manifest: RobotManifest | None = None
        self._features: dict[str, bool] = {}

    async def connect(self) -> RobotManifest:
        await self.transport.connect()
        # a live duck reports what its owner actually built; sim and mock report everything
        self._features = dict(getattr(self.transport, "features", None) or {})
        self.manifest = open_duck_manifest(
            self.backend,
            self.robot_id,
            camera=bool(self._features.get("camera", True)),
            speaker=bool(self._features.get("speaker", True)),
            antennas=bool(self._features.get("antennas", True)),
            microphone=bool(self._features.get("microphone", False)),
            head=bool(self._features.get("head", True)),
            bridge_version=getattr(self.transport, "bridge_version", None),
            runtime_commit=getattr(self.transport, "runtime_commit", None),
            # sim2d and mock have no `safety` block and are unchanged; a bridge that reports
            # none has not told us it has a deadman, so the manifest stops saying it does
            deadman=self._deadman_claim(),
            deadman_ms=getattr(self.transport, "deadman_ms", None),
        )
        return self.manifest

    def _deadman_claim(self) -> bool:
        """True only when this backend has actually said it has one.

        The Open Duck's runtime has no deadman of its own — its command source is a local
        pad, which is never silent — so the guarantee is entirely quackd's daemon on the Pi.
        A bridge that reports no `safety` block has not told us it has one, and the manifest
        must not say so on its behalf. sim2d and mock have no such block and are unchanged:
        the claim is about hardware."""
        if not hasattr(self.transport, "safety"):
            return True
        return bool(getattr(self.transport, "deadman_ms", None))

    async def disconnect(self) -> None:
        await self.transport.close()

    async def close(self) -> None:
        await self.disconnect()

    async def get_state(self) -> DuckState:
        return await self.transport.get_state()

    async def get_frame(self) -> Image.Image | None:
        return await self.transport.get_frame()

    async def send_intent(self, intent: Intent) -> Ack:
        return await self.transport.send_intent(intent)

    async def health(self) -> Health:
        failure: str | None = None
        try:
            await self.transport.heartbeat()
        except HeartbeatError as e:
            failure = str(e)
        # The state is read either way. Returning `Health(ok=False, reason=...)` with no
        # extras meant `doctor` printed the heartbeat's complaint and suppressed every row
        # that would have explained it — so a paused policy showed only "the Pi is starved".
        state = await self.transport.get_state()
        return Health(
            ok=failure is None,
            reason=failure,
            battery_percent=None,  # this robot reports no battery, on any backend
            extras={
                "policy": state.policy,
                "policy_running": state.extras.get("policy_running"),
                "fall_detection": state.extras.get("fall_detection"),
                "loop_hz": state.extras.get("loop_hz"),
                "fallen": state.fallen,
            },
        )

    async def heartbeat(self) -> None:
        await self.transport.heartbeat()

    async def stop(self) -> None:
        await self.transport.stop()

    @property
    def stop_error(self) -> str | None:
        """Forwarded from the transport, because `stop` in `verbs/core.py` reads this off
        whatever object it was handed — and what it is handed is the adapter, not the
        transport underneath. Without the forward the check was dead on every backend, and a
        stop that never left the laptop was recorded as one that had zeroed the legs."""
        return getattr(self.transport, "stop_error", None)

    def subscribe(self, topic: str) -> AsyncIterator[dict[str, Any]]:
        return self.transport.subscribe(topic)

    def now(self) -> float:
        return self.transport.now()

    async def sleep(self, seconds: float) -> None:
        await self.transport.sleep(seconds)

    def preconditions(self) -> dict[str, Precondition]:
        return open_duck_conditions()

    def implementations(self) -> dict[str, Verb]:
        return open_duck_verbs()

    # ── sim-only passthroughs (recorder, flock) ─────────────────────────────────────

    @property
    def mobility(self) -> str:
        return "legged"

    @property
    def world(self) -> Any:
        return self.transport.world  # type: ignore[attr-defined]

    @property
    def clock(self) -> Any:
        return self.transport.clock  # type: ignore[attr-defined]

    @property
    def duck_index(self) -> int:
        return int(self.transport.duck_index)  # type: ignore[attr-defined]

    def add_tick_hook(self, hook: Callable[[Any], None]) -> None:
        self.transport.add_tick_hook(hook)  # type: ignore[attr-defined]

    @property
    def post_sleep(self) -> Callable[[], None] | None:
        return getattr(self.transport, "post_sleep", None)

    @post_sleep.setter
    def post_sleep(self, hook: Callable[[], None] | None) -> None:
        self.transport.post_sleep = hook  # type: ignore[attr-defined]


# ── what the factory calls ──────────────────────────────────────────────────────────────


def describe(backend: str, robot_id: str | None = None) -> RobotManifest:
    """Static: a fully built duck. `connect()` narrows it to what a real one reports."""
    return open_duck_manifest(backend, robot_id)


def implementations() -> dict[str, Verb]:
    return open_duck_verbs()


def conditions() -> dict[str, Precondition]:
    return open_duck_conditions()


def make(
    backend: str,
    *,
    robot_id: str | None = None,
    seed: int | None = None,
    address: str | None = None,
    live: bool = False,
    camera_url: str | None = None,
    token: str | None = None,
) -> OpenDuckAdapter:
    if backend == "sim2d":
        from quackd.adapters.open_duck.sim2d import OpenDuckSim2D

        return OpenDuckAdapter(
            OpenDuckSim2D(seed if seed is not None else 0, live=live), robot_id=robot_id
        )
    if backend == "mock":
        from quackd.adapters.open_duck.mock import OpenDuckMock

        return OpenDuckAdapter(OpenDuckMock(), robot_id=robot_id)
    if backend == "bridge":
        from quackd.adapters.open_duck.bridge import OpenDuckBridge

        return OpenDuckAdapter(
            OpenDuckBridge(address=address, camera_url=camera_url, token=token),
            robot_id=robot_id,
        )
    raise ValueError(f"unknown open_duck backend {backend!r}; choose one of {BACKENDS}")


__all__ = [
    "BACKENDS",
    "CONTROL_HZ",
    "DEFAULT_ID",
    "OpenDuckAdapter",
    "conditions",
    "describe",
    "implementations",
    "make",
    "open_duck_manifest",
]
