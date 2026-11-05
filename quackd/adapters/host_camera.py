"""The camera on the board `--host` names, joined to whatever body the run drives.

Any body, because the camera belongs to the board and not to the robot: a Jetson on a bench
with a USB webcam, or the Jetson inside a robot whose own adapter knows nothing about it. The
board's daemon serves the frames (`/snapshot.jpg`), and this module is the one place a body
learns it has them. Nothing else in quackd needs to know, which is the point of doing it here
rather than in `frames_of()`: that has nine callers, and none of them could add `camera` to a
manifest, so `observe` would stay out of the vocabulary of a body that can now see.

Two pieces. `with_host_camera` is pure and changes a manifest: the CLI applies it to the static
one before validating a task file, so a camera task on a blind body is not refused for a camera
the run will have. `HostCameraAdapter` wraps a built adapter the way `LoggedTransport` wraps a
transport, delegating everything it does not add, and applies the same function to the live
manifest at connect.

The rule for which view is primary: the host's frame is primary only when the body has no
camera of its own. A body with cameras keeps its primary, because the bearings are calibrated for
that lens and a Jetson on a bench says nothing about which way the robot faces. The host's frame
is then an extra view named `host`, which the LLM is shown every step and which `observe`
lists with the others, while the detections still come from the body's own primary lens.

A frame from the board older than `quackd.host.STALE_AFTER_S` is dropped, and so is one that
did not come at all; `camera_error` says why. A snapshot that fails costs the picture and never
the run, which is the Open Duck camera's rule.

`manifest.digest()` hashes the sensors, so a LAN announce of a body with a host camera
fingerprints differently from the same body without one, which is true: it can see. Memory
keys are `adapter:backend` or a registered name, so nothing a robot remembers moves.

Nothing here has run against a Jetson. The tests drive it against the fake daemon in
`tests/fake_jetson_hostd.py`.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

from PIL import Image

from quackd.adapters.base import RestResult, RobotAdapter, go_to_rest_if_any
from quackd.adapters.manifest import Health, RobotManifest, verb_spec
from quackd.host import HostClient, HostError, HostHello
from quackd.transport.base import (
    DEFAULT_CAMERA_NAME,
    Ack,
    CameraFrame,
    DuckState,
    Intent,
    frames_of,
)
from quackd.verbs.core import CORE, REQUIREMENTS, core_requirements_unmet
from quackd.verbs.registry import Precondition, Verb

HOST_CAMERA_NAME = "host"
"""The host camera's name among a body's views, and in the transcript's frame files."""

EXTRAS_KEY = "host_camera"
"""Where a manifest records that it has the host camera, and how: `primary`, `size`, `fov_deg`."""


def _gates(manifest: RobotManifest) -> list[str]:
    """The preconditions a verb the camera unlocks is held to, other than `observe`.

    Every one of them moves the body: `go_to` and `approach_and` drive it with the intent `move`
    does, and `search_scan` turns it in place or sweeps its head. A body that gates `move`
    knows what moving needs (standing, a fresh link, calibrated servos), so they take `move`'s
    conditions. A body with no `move` takes every condition it names on any verb, which may be
    one too many and never one too few: a condition too many refuses a verb and says why, and
    one too few moves a body its own adapter would have stopped."""
    if "move" in manifest.verb_names():
        return list(manifest.preconditions.get("move", []))
    return sorted({name for names in manifest.preconditions.values() for name in names})


def with_host_camera(manifest: RobotManifest, hello: HostHello) -> RobotManifest:
    """`manifest` with the board's camera added, when the daemon has one; else `manifest`.

    Pure, and idempotent: a manifest that already records the host camera comes back as it is.
    Only a body with no camera of its own gains anything in its vocabulary. It gains `camera` in
    its sensors and exactly the core verbs a camera unlocks: each one whose requirements were
    unmet without a camera and are met with one (`core_requirements_unmet`), so `observe`
    always, and `go_to`, `search_scan` and `approach_and` where the body's mobility and intents
    allow them. An arm that cannot move its base gains `observe` and nothing else. The verbs
    that move are gated like `move` (`_gates`). The daemon's field of view, when it was started
    with one, becomes the body's `camera_fov_deg` unless the body already names one.

    A body with a camera gains nothing but the record in `extras`, because its vocabulary
    already has everything a camera unlocks, and the host's frame is only an extra view."""
    if not hello.has_camera or EXTRAS_KEY in manifest.extras:
        return manifest
    primary = "camera" not in manifest.sensors
    size = hello.camera_size
    extras = {
        **manifest.extras,
        EXTRAS_KEY: {
            "primary": primary,
            "size": list(size) if size is not None else None,
            "fov_deg": hello.camera_fov_deg,
        },
    }
    if not primary:
        return manifest.model_copy(update={"extras": extras})
    seeing = manifest.model_copy(update={"sensors": [*manifest.sensors, "camera"]})
    unlocked = [
        name
        for name, needs in REQUIREMENTS.items()
        if needs.camera
        and name not in manifest.verb_names()
        and core_requirements_unmet(name, manifest) is not None
        and core_requirements_unmet(name, seeing) is None
    ]
    gates = _gates(manifest)
    preconditions = dict(manifest.preconditions)
    for name in unlocked:
        if name != "observe" and gates:
            preconditions[name] = list(gates)
    limits = dict(manifest.limits)
    if hello.camera_fov_deg is not None:
        limits.setdefault("camera_fov_deg", hello.camera_fov_deg)
    # validated rather than copied, so the manifest's own invariants (every core verb's
    # requirements met, every precondition naming a declared verb) are checked on the result
    return RobotManifest.model_validate(
        {
            **manifest.model_dump(),
            "sensors": seeing.sensors,
            "verbs": [*manifest.verbs, *(verb_spec(CORE[name], core=True) for name in unlocked)],
            "preconditions": preconditions,
            "limits": limits,
            "extras": extras,
        }
    )


class HostCameraAdapter:
    """A built adapter with the board's camera added, and nothing else changed.

    Everything this does not define is the wrapped adapter's, through `__getattr__`, so a verb's
    `getattr(ctx.transport, "stop_error", None)`, an arm's `rest_pose` and `let_go`, and doctor's
    `transport` all still reach the body. The protocol's own methods are spelled out rather than
    left to `__getattr__`, so this is a `RobotAdapter` to mypy as well as to `isinstance`, and
    `name`, `backend` and `manifest` are real attributes for the same reason.

    `manifest` is None until `connect()`, and then the body's live manifest with the host
    camera added (`with_host_camera`). Whether the host's frame is primary is decided there,
    from what the body reported, not from its description."""

    def __init__(self, inner: RobotAdapter, client: HostClient, hello: HostHello) -> None:
        self._inner = inner
        self._client = client
        self._hello = hello
        self.name = inner.name
        self.backend = inner.backend
        self.manifest: RobotManifest | None = None
        self._primary = False

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):  # never delegate our own privates (copy, pickle, half-init)
            raise AttributeError(name)
        return getattr(self._inner, name)

    def __repr__(self) -> str:
        return f"HostCameraAdapter({self._inner!r}, {self._client!r})"

    @property
    def inner(self) -> RobotAdapter:
        """The body without the board's camera."""
        return self._inner

    @property
    def host(self) -> HostClient:
        """The board whose camera this adds."""
        return self._client

    @property
    def host_is_primary(self) -> bool:
        """True when the host's frame is the one the detections and the steering verbs read,
        which is when the body has no camera of its own. False before `connect()`."""
        return self._primary

    # ── the connection ──────────────────────────────────────────────────────────────────

    async def connect(self) -> RobotManifest:
        live = await self._inner.connect()
        self._primary = "camera" not in live.sensors
        self.manifest = with_host_camera(live, self._hello)
        return self.manifest

    async def disconnect(self) -> None:
        await self._inner.disconnect()

    async def close(self) -> None:
        await self._inner.close()

    # ── the cameras ─────────────────────────────────────────────────────────────────────

    async def _snapshot(self) -> Image.Image | None:
        """The board's newest frame, or None. In a thread, so the event loop that sends a
        walking body its keepalives never waits on the network; the client bounds the wait and
        refuses a frame older than `STALE_AFTER_S`, keeping the reason as `camera_error`."""
        try:
            image, _age = await asyncio.to_thread(self._client.snapshot)
        except HostError:
            return None
        return image

    async def get_frames(self) -> list[CameraFrame]:
        """The body's own views, then the host's, primary first as `frames_of` promises: the
        body's primary when it has a camera, the host's when it has none."""
        frames = [] if self._primary else await frames_of(self._inner)
        image = await self._snapshot()
        if image is not None:
            frames.append(CameraFrame(HOST_CAMERA_NAME, image, primary=self._primary))
        return frames

    async def get_frame(self) -> Image.Image | None:
        """The primary view: the host's when the body has no camera, else the body's own."""
        if self._primary:
            return await self._snapshot()
        return await self._inner.get_frame()

    @property
    def camera_keys(self) -> tuple[str, ...]:
        """Every view this body has, the host's last. A body with no camera of its own has the
        host's alone; one with a camera that names no views has the one `frames_of` calls
        `camera`."""
        if self._primary:
            return (HOST_CAMERA_NAME,)
        own = tuple(str(key) for key in (getattr(self._inner, "camera_keys", None) or ()))
        return (*(own or (DEFAULT_CAMERA_NAME,)), HOST_CAMERA_NAME)

    @property
    def camera_error(self) -> str | None:
        """Why a view gave nothing last time, or None. The primary's reason first, because that
        is the view `observe` and the steering verbs depend on."""
        host = self._client.camera_error
        if self._primary:
            return host
        own = getattr(self._inner, "camera_error", None)
        reasons = [str(own)] if own else []
        if host:
            reasons.append(f"host camera: {host}")
        return "; ".join(reasons) or None

    def camera_health(self) -> dict[str, Any]:
        """Every view as `doctor` renders a body with several: one row each in `cameras`, the
        host's with its `role`, primary or extra view, so the report says which it is.

        The body's own rows come from its camera's own health where it reports one, and are
        left out when it reports none, which is a body whose camera nobody configured."""
        host = {
            "name": HOST_CAMERA_NAME,
            "role": "primary" if self._primary else "extra view",
            **self._client.camera_health(),
        }
        host["ok"] = host.get("error") is None and bool(host.get("frames"))
        rows: list[dict[str, Any]] = []
        if not self._primary:
            rows = self._own_camera_rows()
        rows.append(host)
        first = rows[0]
        return {"url": first.get("url"), "error": first.get("error"), "cameras": rows}

    def _own_camera_rows(self) -> list[dict[str, Any]]:
        probe = getattr(getattr(self._inner, "transport", None), "camera_health", None)
        if not callable(probe):
            return []
        health = dict(probe())
        if health.get("configured") is False:
            return []
        if rows := health.get("cameras"):
            return [dict(row) for row in rows]
        keys = tuple(getattr(self._inner, "camera_keys", None) or ())
        return [{**health, "name": keys[0] if keys else DEFAULT_CAMERA_NAME, "role": "primary"}]

    # ── everything else is the body's ───────────────────────────────────────────────────

    async def get_state(self) -> DuckState:
        return await self._inner.get_state()

    async def send_intent(self, intent: Intent) -> Ack:
        return await self._inner.send_intent(intent)

    async def health(self) -> Health:
        return await self._inner.health()

    async def heartbeat(self) -> None:
        await self._inner.heartbeat()

    async def stop(self) -> None:
        await self._inner.stop()

    async def go_to_rest(self) -> RestResult:
        # through the helper rather than straight to the body: this wrapper always has the
        # method, so the helper's "no such method means no rest pose" must be asked of the body
        return await go_to_rest_if_any(self._inner)

    def subscribe(self, topic: str) -> AsyncIterator[dict[str, Any]]:
        return self._inner.subscribe(topic)

    def now(self) -> float:
        return self._inner.now()

    async def sleep(self, seconds: float) -> None:
        await self._inner.sleep(seconds)

    def preconditions(self) -> dict[str, Precondition]:
        return self._inner.preconditions()

    def implementations(self) -> dict[str, Verb]:
        return self._inner.implementations()
