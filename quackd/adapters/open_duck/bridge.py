"""EXPERIMENTAL: a real Open Duck Mini v2, over the quackd bridge daemon on its Pi.

The robot's own runtime has no network control API at all: it reads a local pygame gamepad
and runs a 50 Hz ONNX walk policy, and its only socket checks the IMU. So quackd ships a
small daemon that runs on the duck's Raspberry Pi, replaces the gamepad as the walk loop's
command source, and speaks the protocol below. This module is the client half.

The protocol is quackd's own. It deliberately does not reuse the Microduck's `robot.move`
and `robot.health`, which are that robot's `duck-ipc-proto` API v16: the same words would
be a false claim about a different body with different limits and no skills behind them.
Because quackd defines both ends, the method names are not upstream names and are not in
`upstream_api.py`; what *is* there is every assumption the daemon makes about the robot.

Nothing here has been run against a physical duck.

Address: `tcp://open-duck.local:9871`, or a bare `host:port`.
"""

from __future__ import annotations

import asyncio
import contextlib
import io
import json
import math
import os
import time
import urllib.request
from collections.abc import AsyncIterator
from typing import Any

from PIL import Image

from quackd.adapters.open_duck import upstream_api as up
from quackd.adapters.open_duck.verbs import (
    GESTURES,
    HEAD_PITCH_RANGE,
    HEAD_ROLL_RANGE,
    HEAD_YAW_RANGE,
    MOODS,
    NECK_PITCH_RANGE,
)
from quackd.transport.base import (
    Ack,
    DuckState,
    HeartbeatError,
    Intent,
    Posture,
    TransportError,
)

STATUS = "EXPERIMENTAL — quackd's own protocol, never run against a duck"

#: The wire contract. Bumped whenever a field changes meaning; a mismatch is refused.
PROTOCOL = "quackd-open-duck-bridge"
PROTOCOL_VERSION = 1
JSONRPC_VERSION = "2.0"
DEFAULT_PORT = 9871
#: Where the client looks for the bridge token when no --token was given.
TOKEN_ENV = "QUACKD_DUCK_TOKEN"

HELLO = "duck.hello"
COMMAND = "duck.command"  # notification, re-sent at 10 Hz; feeds the daemon's deadman
STOP = "duck.stop"
STATE = "duck.state"
HEALTH = "duck.health"
SOUND = "duck.sound"
ANTENNAS = "duck.antennas"

#: Below this the walk loop is starving and the gait is degrading silently, so the
#: heartbeat fails and the run aborts rather than letting the duck stumble (up.LOOP_HEADROOM).
MIN_LOOP_HZ = 35.0


def parse_address(address: str) -> tuple[str, int]:
    text = address[len("tcp://") :] if address.startswith("tcp://") else address
    if "://" in text:
        raise TransportError(f"unknown address {address!r}; use tcp://host:port")
    host, _, port = text.rpartition(":")
    if not host:
        return text, DEFAULT_PORT
    if not port.isdigit():
        raise TransportError(f"bad address {address!r}; expected tcp://host:port")
    return host, int(port)


def _clamp(value: float, bounds: tuple[float, float]) -> float:
    return max(bounds[0], min(bounds[1], value))


class OpenDuckBridge:
    name = "bridge"
    mobility = "legged"

    def __init__(
        self,
        address: str | None = None,
        *,
        camera_url: str | None = None,
        token: str | None = None,
        request_timeout_s: float = 2.0,
    ) -> None:
        self.address = address or f"tcp://open-duck.local:{DEFAULT_PORT}"
        self.camera_url = camera_url
        # The bridge's installer writes a token to the robot, so a duck set up by the book
        # refuses an unauthenticated client. It travels in the handshake and never in the
        # address, because addresses are printed and land in transcripts.
        self.token = token or os.environ.get(TOKEN_ENV) or None
        self.request_timeout_s = request_timeout_s
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._pump: asyncio.Task[None] | None = None
        self._pending: dict[int, asyncio.Future[Any]] = {}
        self._notifications: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=256)
        self._next_id = 1
        self._last_state: dict[str, Any] | None = None
        self._t0 = time.monotonic()
        self.hello: dict[str, Any] | None = None
        #: What this particular duck was built with, read at connect. The adapter narrows
        #: its manifest from these, so a duck with no camera loses the verbs that need one.
        self.features: dict[str, bool] = {}
        self.bridge_version: str | None = None
        self.runtime_commit: str | None = None
        #: Why the last `stop` did not reach the duck, or None if it did. `stop` is the verb
        #: the pilot is told to reach for when something is wrong, and it is asked for most
        #: often when the link is the thing that is wrong — so a stop that was never delivered
        #: must not be logged as one that was.
        self.stop_error: str | None = None
        #: Set when the read pump sees EOF. Without it every later request waited out the
        #: full timeout on a socket nobody was reading.
        self._closed = False

    # ── wire ────────────────────────────────────────────────────────────────────────

    async def connect(self) -> None:
        host, port = parse_address(self.address)
        try:
            self._reader, self._writer = await asyncio.open_connection(host, port)
        except OSError as e:
            raise TransportError(
                f"cannot connect to the bridge at {self.address}: {e}. Is "
                "quackd-duck-bridge running on the duck's Pi?"
            ) from e
        self._pump = asyncio.create_task(self._read_loop(), name="quackd-open-duck-pump")
        hello: dict[str, Any] = {"protocol": PROTOCOL, "protocol_version": PROTOCOL_VERSION}
        if self.token:
            hello["token"] = self.token
        try:
            result = await self.request(HELLO, hello)
        except TransportError as e:
            await self.close()
            if "2:" in str(e) and not self.token:
                raise TransportError(
                    f"the bridge at {self.address} wants a token and none was given. Its "
                    f"installer writes one on the robot; pass it with --token or "
                    f"{TOKEN_ENV}. Original answer: {e}"
                ) from e
            raise
        self.hello = result if isinstance(result, dict) else {}
        remote = self.hello.get("protocol_version")
        if remote is not None and int(remote) != PROTOCOL_VERSION:
            await self.close()
            raise TransportError(
                f"the bridge speaks {PROTOCOL} v{remote}, quackd speaks "
                f"v{PROTOCOL_VERSION}; refusing rather than guessing. Update whichever is older"
            )
        caps = self.hello.get("capabilities") or {}
        self.features = {k: bool(v) for k, v in caps.items()}
        self.bridge_version = self.hello.get("bridge_version")
        self.runtime_commit = (self.hello.get("runtime") or {}).get("commit")
        if not self.camera_url:
            self.camera_url = (self.hello.get("camera") or {}).get("url")

    async def close(self) -> None:
        if self._pump is not None:
            self._pump.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._pump
            self._pump = None
        if self._writer is not None:
            self._writer.close()
            with contextlib.suppress(Exception):
                await self._writer.wait_closed()
            self._writer = None
            self._reader = None

    async def _read_loop(self) -> None:
        assert self._reader is not None
        while True:
            line = await self._reader.readline()
            if not line:
                # Mark the link dead before failing the waiters: otherwise every later
                # request sat out its full timeout waiting for a pump that had exited, and
                # `stop` in particular blocked for seconds before reporting anything.
                self._closed = True
                for fut in self._pending.values():
                    if not fut.done():
                        fut.set_exception(TransportError("the bridge closed the connection"))
                return
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            if "id" in msg and msg["id"] is not None and ("result" in msg or "error" in msg):
                pending = self._pending.pop(int(msg["id"]), None)
                if pending is not None and not pending.done():
                    if "error" in msg:
                        err = msg["error"]
                        pending.set_exception(
                            TransportError(f"{err.get('code')}: {err.get('message')}")
                        )
                    else:
                        pending.set_result(msg.get("result"))
            elif "method" in msg:
                if msg["method"] == STATE:
                    self._last_state = msg.get("params") or {}
                with contextlib.suppress(asyncio.QueueFull):
                    self._notifications.put_nowait(msg)

    def _write(self, obj: dict[str, Any]) -> None:
        if self._closed:
            raise TransportError("the bridge closed the connection")
        if self._writer is None:
            raise TransportError("not connected to the bridge")
        self._writer.write((json.dumps(obj, separators=(",", ":")) + "\n").encode())

    async def request(self, method: str, params: dict[str, Any] | None = None) -> Any:
        req_id = self._next_id
        self._next_id += 1
        fut: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        self._pending[req_id] = fut
        msg: dict[str, Any] = {"jsonrpc": JSONRPC_VERSION, "id": req_id, "method": method}
        if params is not None:
            msg["params"] = params
        self._write(msg)
        assert self._writer is not None
        await self._writer.drain()
        try:
            return await asyncio.wait_for(fut, timeout=self.request_timeout_s)
        except TimeoutError as e:
            self._pending.pop(req_id, None)
            raise TransportError(f"{method}: no answer within {self.request_timeout_s:g}s") from e

    async def notify(self, method: str, params: dict[str, Any]) -> None:
        self._write({"jsonrpc": JSONRPC_VERSION, "method": method, "params": params})
        assert self._writer is not None
        await self._writer.drain()

    # ── protocol ────────────────────────────────────────────────────────────────────

    async def get_frame(self) -> Image.Image | None:
        """The camera lives in its own process on the Pi: encoding a 512 by 512 JPEG inside
        a 20 ms control tick is not affordable (up.CAM), so the bridge advertises a URL."""
        if not self.camera_url:
            return None
        url = self.camera_url

        def fetch() -> bytes:
            with urllib.request.urlopen(url, timeout=3) as resp:
                return bytes(resp.read())

        try:
            data = await asyncio.to_thread(fetch)
            return Image.open(io.BytesIO(data)).convert("RGB")
        except Exception as e:
            raise TransportError(f"camera snapshot failed: {e}") from e

    async def get_state(self) -> DuckState:
        state = self._last_state
        if state is None:
            with contextlib.suppress(TransportError):
                result = await self.request(STATE)
                state = result if isinstance(result, dict) else {}
        state = state or {}
        # `fallen` is tri-state on the wire: None means the bridge cannot see falls at all.
        # A duck nobody is watching must read as unknown, never as standing (up.FALL_SIGNAL).
        raw_fallen = state.get("fallen")
        detects_falls = raw_fallen is not None
        fallen = raw_fallen is True
        running = state.get("policy_running")
        posture: Posture
        if fallen:
            posture = "fallen"
        elif detects_falls and running:
            posture = "standing"
        else:
            posture = "unknown"
        pose = state.get("pose") or {}
        return DuckState(
            t=self.now(),
            x=float(pose.get("x", 0.0)),
            y=float(pose.get("y", 0.0)),
            theta=float(pose.get("theta", 0.0)),
            policy="walk" if state.get("moving") else "stand",
            posture=posture,
            fallen=fallen,
            # nothing in the runtime reports a battery, so a battery abort cannot fire
            battery_percent=None,
            extras={
                "policy_running": running,
                "fall_detection": detects_falls,
                "loop_hz": state.get("loop_hz"),
                "command_age_ms": state.get("command_age_ms"),
                "deadman_tripped": state.get("deadman_tripped"),
                "pad_override": state.get("pad_override"),
                "stop_error": self.stop_error,
                "assumptions": [up.FALL_SIGNAL.name, up.COMMAND_TTL.name],
            },
        )

    async def send_intent(self, intent: Intent) -> Ack:
        p = intent.params
        try:
            match intent.kind:
                case "move":
                    await self.notify(
                        COMMAND,
                        {
                            "vx": float(p.get("vx", 0.0)),
                            "vy": float(p.get("vy", 0.0)),
                            "vyaw": float(p.get("wz", 0.0)),
                        },
                    )
                    return Ack()
                case "stop":
                    await self.request(STOP)
                    return Ack()
                case "look":
                    return await self._look(p)
                case "sound":
                    tag = str(p.get("tag", "chirp"))
                    mood = tag if tag in MOODS else "chirp"
                    return _ack(await self.request(SOUND, {"mood": mood}))
                case "do":
                    kind, _, arg = str(p.get("skill")).partition(":")
                    if kind != "antennas" or arg not in GESTURES:
                        return Ack(
                            accepted=False,
                            reason=f"an Open Duck Mini v2 has no skill {p.get('skill')!r}",
                        )
                    return _ack(await self.request(ANTENNAS, {"gesture": arg}))
                case "enable":
                    if not p.get("on", True):
                        return Ack(accepted=False, reason="quackd never limps a robot")
                    return Ack(accepted=True, reason="this robot's policy is always enabled")
                case _:
                    return Ack(accepted=False, reason=f"no bridge mapping for {intent.kind}")
        except TransportError as e:
            return Ack(accepted=False, reason=str(e))

    async def _look(self, p: dict[str, Any]) -> Ack:
        """A gaze point comes in as a unit vector; the robot wants four joint angles.

        Only the keys we send are changed, so re-centring the gaze before a walk never
        yanks the neck or the roll to zero."""
        x, y, z = float(p.get("x", 1.0)), float(p.get("y", 0.0)), float(p.get("z", 0.0))
        yaw = math.atan2(y, x)
        pitch = math.atan2(z, math.hypot(x, y))
        wanted = {"head_yaw": yaw, "head_pitch": pitch}
        head = {
            "head_yaw": _clamp(yaw, HEAD_YAW_RANGE),
            "head_pitch": _clamp(pitch, HEAD_PITCH_RANGE),
        }
        for name, bounds in (("neck_pitch", NECK_PITCH_RANGE), ("head_roll", HEAD_ROLL_RANGE)):
            if name in p:
                head[name] = _clamp(float(p[name]), bounds)
        await self.notify(COMMAND, {"head": head})
        clamped = any(abs(head[k] - v) > 1e-9 for k, v in wanted.items())
        return Ack(accepted=True, reason="clamped to this neck's travel" if clamped else None)

    async def subscribe(self, topic: str) -> AsyncIterator[dict[str, Any]]:  # type: ignore[override]
        while True:
            msg = await self._notifications.get()
            yield {"topic": msg.get("method"), **(msg.get("params") or {})}

    async def heartbeat(self) -> None:
        try:
            health = await self.request(HEALTH)
        except TransportError as e:
            raise HeartbeatError(f"{HEALTH} failed: {e}") from e
        if not isinstance(health, dict):
            return
        if health.get("healthy") is False:
            raise HeartbeatError(f"the duck is unhealthy: {health.get('reason') or 'no reason'}")
        loop_hz = health.get("loop_hz")
        if loop_hz is not None and float(loop_hz) < MIN_LOOP_HZ:
            raise HeartbeatError(
                f"the walk loop is down to {float(loop_hz):.1f} Hz (needs {MIN_LOOP_HZ:g}); "
                "the Pi is starved and the gait is degrading"
            )

    async def stop(self) -> None:
        try:
            await self.request(STOP)
        except (TransportError, OSError) as e:
            # Recorded rather than raised: `stop` must never itself take a run down. What
            # actually zeroes the legs when this fails is the daemon's own deadman, 300 ms
            # after the commands stop arriving — which is exactly what has just happened.
            self.stop_error = str(e)
        else:
            self.stop_error = None

    def now(self) -> float:
        return time.monotonic() - self._t0

    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(seconds)


def _ack(result: Any) -> Ack:
    if isinstance(result, dict) and "accepted" in result:
        return Ack(accepted=bool(result["accepted"]), reason=result.get("reason"))
    return Ack()
