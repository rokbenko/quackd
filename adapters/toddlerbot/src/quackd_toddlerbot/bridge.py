"""EXPERIMENTAL: a ToddlerBot, through the daemon quackd ships for it.

This robot has no network API at all, so there is nothing to speak to until quackd puts
something there. `bridge/toddlerbot/` is that something, and this is its client: line
delimited JSON-RPC over TCP, standard library only, the same shape the Open Duck Mini's
bridge uses (ADR-0024, extended by ADR-0028).

The protocol is quackd's own at both ends, so it is deliberately **not** in `upstream_api.py`:
there is no upstream to verify it against and citing one would be a false citation. What
`upstream_api.py` carries instead is every assumption the daemon makes about the robot.

Two things are different here from every other adapter quackd has, and both come from the
same fact: **on this body, silence means hold forever, not stop.**

- The daemon owns the fifty hertz loop and its own deadman, because a verb is episodic and a
  humanoid frozen mid-stride while a model thinks is a humanoid on the floor. quackd sends
  intents, not motor targets.
- `stop` cannot mean zero velocity. There is no velocity at the hardware boundary at all: the
  command is an absolute pose. So `stop` means *hold the last verified-good measured pose*,
  and the daemon is the one that knows which pose that was.

Never run against a robot by us.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import contextlib
import json
import os
import time
from collections.abc import AsyncIterator
from io import BytesIO
from typing import Any

from PIL import Image, UnidentifiedImageError

from quackd.transport.base import Ack, DuckState, HeartbeatError, Intent, TransportError
from quackd_toddlerbot.verbs import look_degrees

STATUS = "EXPERIMENTAL - quackd's own protocol, never run against a robot"

PROTOCOL = "quackd-toddlerbot-bridge"
PROTOCOL_VERSION = 1
JSONRPC_VERSION = "2.0"
DEFAULT_PORT = 9873
"""The Open Duck Mini takes 9871 for its bridge and 9872 for its camera daemon, and
SECURITY.md tells people to tunnel that pair, so this robot starts after both."""

TOKEN_ENV = "QUACKD_TODDLERBOT_TOKEN"

HELLO = "bot.hello"
COMMAND = "bot.command"  # notification, re-sent while a verb runs; feeds the daemon's deadman
STOP = "bot.stop"
STATE = "bot.state"
HEALTH = "bot.health"
LOOK = "bot.look"
STAND = "bot.stand"
PERFORM = "bot.perform"
GRIP = "bot.grip"
FRAME = "bot.frame"
KEEPALIVE = "bot.keepalive"

KEEPALIVE_S = 0.15
"""How often the client says it is still there.

The daemon's deadman fires after half a second of silence, and on this body that is
not a stop but a slew to the safe pose, so a verb that took longer than half a second
without sending anything would be cancelled underneath itself. `stand` takes three.

quackd's own `Heartbeat` cannot be the signal: its period is a run setting rather than
the manifest's, and it defaults to the same half second the deadman uses. So the
transport keeps its own timer, the way the Microduck's re-sent `robot.move` does, and
reading state or a frame deliberately does not count as being alive."""

MIN_LOOP_HZ = 40.0
"""The daemon runs at fifty. Much below that and the gait phase drifts, so quackd says so
rather than letting a slow robot look like a healthy one."""
STALE_LIMIT_MS = 1000.0


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


def _yaw_deg(p: dict[str, Any]) -> float:
    return look_degrees(float(p.get("x", 1.0)), float(p.get("y", 0.0)), float(p.get("z", 0.0)))[0]


def _pitch_deg(p: dict[str, Any]) -> float:
    return look_degrees(float(p.get("x", 1.0)), float(p.get("y", 0.0)), float(p.get("z", 0.0)))[1]


class ToddlerBotBridge:
    name = "bridge"

    def __init__(
        self,
        address: str | None = None,
        *,
        token: str | None = None,
        request_timeout_s: float = 3.0,
    ) -> None:
        self.address = address or f"tcp://toddlerbot.local:{DEFAULT_PORT}"
        # The installer writes a token onto the robot, so a robot set up by the book refuses
        # an unauthenticated client. It travels in the handshake and never in the address,
        # because addresses are printed and land in transcripts.
        self.token = token or os.environ.get(TOKEN_ENV) or None
        self.request_timeout_s = request_timeout_s
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._pump: asyncio.Task[None] | None = None
        self._alive: asyncio.Task[None] | None = None
        self._pending: dict[int, asyncio.Future[Any]] = {}
        self._notifications: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=256)
        self._next_id = 1
        self._last_state: dict[str, Any] | None = None
        self._t0 = time.monotonic()
        self.hello: dict[str, Any] | None = None
        #: What the daemon said is actually on this robot. The adapter narrows its manifest
        #: from these, so a build with no walk checkpoint loses locomotion entirely.
        self.camera_available = False
        self.neck_available = True
        self.gripper_available = False
        self.walk_available = False
        #: The motions the daemon actually loaded, which is at most the five quackd
        #: offers and fewer if a keyframe file was missing or unreadable.
        self.motions: tuple[str, ...] = ()
        #: The velocity envelope the loaded walk checkpoint was trained on, read
        #: from its own config rather than assumed.
        self.walk_envelope: dict[str, float] | None = None
        self.deadman = False
        self.robot_name = "toddlerbot_2xc"
        self.motors = 30
        self.daemon_version: str | None = None
        self.upstream_commit: str | None = None
        self.post_sleep: Any = None

    # ── wire ────────────────────────────────────────────────────────────────────────

    async def connect(self) -> None:
        host, port = parse_address(self.address)
        try:
            self._reader, self._writer = await asyncio.open_connection(host, port)
        except OSError as e:
            raise TransportError(
                f"cannot connect to the bridge at {self.address}: {e}. Is "
                "quackd-toddlerbot-bridge running on the robot?"
            ) from e
        self._pump = asyncio.create_task(self._read_loop(), name="quackd-toddlerbot-pump")
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
                f"the bridge speaks {PROTOCOL} v{remote}, quackd speaks v{PROTOCOL_VERSION}; "
                "refusing rather than guessing. Update whichever is older"
            )
        caps = self.hello.get("capabilities") or {}
        self.camera_available = bool(caps.get("camera", False))
        self.neck_available = bool(caps.get("neck", True))
        self.gripper_available = bool(caps.get("gripper", False))
        # No walk checkpoint means no locomotion at all: it is an ONNX artifact upstream does
        # not publish, so the adapter drops move, go_to and approach_and rather than gate them.
        self.walk_available = bool(caps.get("walk", False))
        self.motions = tuple(caps.get("motions") or ())
        envelope = caps.get("walk_envelope")
        self.walk_envelope = (
            {k: float(v) for k, v in envelope.items()} if isinstance(envelope, dict) else None
        )
        self.deadman = bool(caps.get("deadman", False))
        self.robot_name = str(self.hello.get("robot") or self.robot_name)
        self.motors = int(self.hello.get("motors") or self.motors)
        self.daemon_version = self.hello.get("daemon_version")
        self._alive = asyncio.create_task(self._keepalive(), name="quackd-toddlerbot-keepalive")
        self.upstream_commit = (self.hello.get("upstream") or {}).get("commit")

    async def _keepalive(self) -> None:
        """Say we are here, often enough that the deadman does not fire on a verb."""
        while self._writer is not None and not self._writer.is_closing():
            with contextlib.suppress(Exception):
                await self.notify(KEEPALIVE, {})
            await asyncio.sleep(KEEPALIVE_S)

    async def close(self) -> None:
        if self._alive is not None:
            self._alive.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._alive
            self._alive = None
        # The final stop goes FIRST, while the reader is still running. `request()` waits on a
        # future that only the read loop can resolve, so cancelling the pump before asking
        # meant every close sat out the full request timeout and never saw the answer.
        if self._writer is not None:
            # never a torque-off from here: quackd asks the daemon to hold, and the daemon
            # decides what leaving looks like. Dropping the socket is not a shutdown.
            with contextlib.suppress(Exception):
                await self.stop()
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
        """The camera lives on the daemon's own thread, because upstream's observation has no
        image field at all and reading a camera on the control thread would cost ticks."""
        if not self.camera_available:
            return None
        try:
            result = await self.request(FRAME)
        except TransportError:
            return None
        payload = (result or {}).get("jpeg") if isinstance(result, dict) else None
        if not payload:
            return None
        try:
            img = Image.open(BytesIO(base64.b64decode(payload, validate=True)), formats=["JPEG"])
            img.load()
        except (binascii.Error, ValueError, OSError, UnidentifiedImageError):
            return None
        return img.convert("RGB")

    def _stale_ms(self) -> float:
        state = self._last_state or {}
        stamped = state.get("t")
        if stamped is None:
            return float("inf")
        return max(0.0, (time.monotonic() - self._t0 - float(stamped)) * 1000.0)

    async def get_state(self) -> DuckState:
        result = await self.request(STATE)
        state = result if isinstance(result, dict) else {}
        self._last_state = state
        extras = {
            "joints": state.get("joints") or {},
            "neck": state.get("neck") or {},
            "head_yaw_deg": float((state.get("neck") or {}).get("yaw_deg") or 0.0),
            "holding": state.get("holding") or {},
            "robot": self.robot_name,
            "calibrated": bool(state.get("calibrated", False)),
            "moving": bool(state.get("moving", False)),
            "loop_hz": state.get("loop_hz"),
            "deadman_tripped": bool(state.get("deadman_tripped", False)),
            "walk_policy": self.walk_available,
            "stale_ms": round(float(state.get("age_ms") or 0.0), 1),
            "stale_limit_ms": STALE_LIMIT_MS,
            "tilt_deg": state.get("tilt_deg"),
        }
        return DuckState(
            t=float(state.get("t") or 0.0),
            policy=str(state.get("policy") or "idle"),
            posture=state.get("posture") or "unknown",
            fallen=bool(state.get("fallen", False)),
            # nothing on this robot reports a battery to Python
            battery_percent=None,
            # no odometry anywhere: motor positions and an orientation, and nothing else
            x=None,
            y=None,
            theta=None,
            holding=any((state.get("holding") or {}).values()),
            extras=extras,
        )

    async def send_intent(self, intent: Intent) -> Ack:
        p = intent.params
        try:
            match intent.kind:
                case "move":
                    if not self.walk_available:
                        return Ack(
                            accepted=False,
                            reason="this robot has no walk policy staged, so it cannot walk",
                        )
                    # all three keys or none: the walk policy indexes them unconditionally
                    return _ack(
                        await self.request(
                            COMMAND,
                            {
                                "walk_x": float(p.get("vx", 0.0)),
                                "walk_y": float(p.get("vy", 0.0)),
                                "walk_turn": float(p.get("wz", 0.0)),
                            },
                        )
                    )
                case "stop":
                    return _ack(await self.request(STOP))
                case "look":
                    if not self.neck_available:
                        return Ack(accepted=False, reason="this build has no neck")
                    return _ack(
                        await self.request(
                            LOOK,
                            {
                                # the intent carries a direction; the wire carries
                                # the two servo angles the daemon actually commands
                                "yaw_deg": _yaw_deg(p),
                                "pitch_deg": _pitch_deg(p),
                            },
                        )
                    )
                case "gripper":
                    if not self.gripper_available:
                        return Ack(accepted=False, reason="this build has no grippers")
                    return _ack(
                        await self.request(
                            GRIP,
                            {"side": str(p.get("side", "right")), "close": not p.get("open", True)},
                        )
                    )
                case "do":
                    skill = str(p.get("skill", ""))
                    if skill == "stand":
                        return _ack(await self.request(STAND))
                    kind, _, name = skill.partition(":")
                    if kind == "motion" and name:
                        return _ack(await self.request(PERFORM, {"motion": name}))
                    return Ack(accepted=False, reason=f"unknown skill {skill!r}")
                case "enable":
                    if not p.get("on", True):
                        # there is no way back: enable_motors is not bound to Python at this
                        # pin, so a torque-off would be one way and quackd never sends one
                        return Ack(accepted=False, reason="quackd never limps a robot")
                    return Ack()
                case _:
                    return Ack(accepted=False, reason=f"a ToddlerBot cannot {intent.kind}")
        except TransportError as e:
            return Ack(accepted=False, reason=str(e))

    async def subscribe(self, topic: str) -> AsyncIterator[dict[str, Any]]:  # type: ignore[override]
        while self._writer is not None:
            msg = await self._notifications.get()
            yield {"topic": topic, **(msg.get("params") or {})}

    async def heartbeat(self) -> None:
        try:
            result = await self.request(HEALTH)
        except TransportError as e:
            raise HeartbeatError(f"toddlerbot bridge: {e}") from e
        health = result if isinstance(result, dict) else {}
        loop_hz = float(health.get("loop_hz") or 0.0)
        if loop_hz and loop_hz < MIN_LOOP_HZ:
            raise HeartbeatError(
                f"the robot's control loop has fallen to {loop_hz:.0f} Hz (needs "
                f"{MIN_LOOP_HZ:.0f}). Its gait is timed against fifty, so it is no longer "
                "walking the way it was trained to"
            )
        if not health.get("ok", True):
            raise HeartbeatError(str(health.get("reason") or "the robot reported not ok"))

    async def stop(self) -> None:
        """Hold, never limp.

        There is no zero velocity on this body and torque-off is a fall, so the daemon holds
        the last verified-good measured pose. Safe to call when the link has already gone: the
        daemon's own deadman is what is holding the robot at that point."""
        with contextlib.suppress(Exception):
            await self.request(STOP)

    def now(self) -> float:
        return time.monotonic() - self._t0

    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(seconds)
        if self.post_sleep is not None:
            self.post_sleep()


def _ack(result: Any) -> Ack:
    if isinstance(result, dict) and not result.get("accepted", True):
        return Ack(accepted=False, reason=str(result.get("reason") or "refused"))
    return Ack()


__all__ = [
    "COMMAND",
    "DEFAULT_PORT",
    "HELLO",
    "MIN_LOOP_HZ",
    "PROTOCOL",
    "PROTOCOL_VERSION",
    "STATUS",
    "STOP",
    "TOKEN_ENV",
    "ToddlerBotBridge",
    "parse_address",
]
