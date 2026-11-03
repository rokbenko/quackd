"""EXPERIMENTAL: the real XLeRobot, over the ZeroMQ host protocol it already ships.

quackd connects PUSH to the host's command port and PULL to its observation port, and speaks
the flat JSON dict upstream's `send_action` and `get_observation` already use
(`upstream_api.WIRE_FORMAT`). Nothing from XLeRobot is imported: it is not an installable
package, so there is no name here that is not in `upstream_api.py`.

Three properties of that protocol shape this file:

- **`zmq.CONFLATE` keeps only the newest message**, so two sends in one host cycle silently
  lose the first. This backend therefore holds one desired action and re-sends the whole of
  it, so a dropped message is recovered by the next one rather than lost.
- **An action carrying no velocity keys commands zero base velocity**, because upstream writes
  the wheels unconditionally. That is faithfully reproduced: a joint or gripper command zeroes
  the desired velocity, so moving an arm stops the cart, exactly as it would on the robot.
- **Nothing on the wire is timestamped**, and upstream's own client answers a poll timeout with
  its cached frames. This one stamps on arrival instead and calls a reading older than the
  host's own watchdog window a heartbeat failure, which is also how the host's one-hour
  self-termination becomes legible rather than a hang.

Never run against a cart by us.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import contextlib
import json
import math
import threading
import time
from collections.abc import AsyncIterator
from io import BytesIO
from typing import Any, Protocol
from urllib.parse import parse_qs, urlsplit

from PIL import Image, UnidentifiedImageError

from quackd.adapters.base import AdapterNotInstalled
from quackd.transport.base import Ack, DuckState, HeartbeatError, Intent, TransportError
from quackd_xlerobot import upstream_api as up
from quackd_xlerobot.verbs import ARMS, GRIPPER_CLOSED, GRIPPER_OPEN, JOINTS

DEFAULT_HOST = "127.0.0.1"
CMD_PORT = int(up.PORT_ZMQ_CMD.name)
OBS_PORT = int(up.PORT_ZMQ_OBSERVATIONS.name)
"""Both from `upstream_api`, not retyped here. A port spelled twice is a port that
can disagree with itself, and this one is read from upstream's own host config."""
STALE_LIMIT_MS = float(up.WATCHDOG_TIMEOUT_MS.name)
"""The host's own watchdog window, from `upstream_api`, not retyped. By the time an
observation is this old the host has already stopped the base, so this is the moment a
cached reading stops being a reading. A number spelled twice can disagree with itself."""
"""The host's own watchdog window: by now it has already stopped the base."""
POLL_MS = 50
HOLD_READ_S = 0.2
"""How long a stop looks for a reading from after it began, once the wheels are stopped: six of the
host's cycles at 30 Hz. The arm goes on meanwhile, and past this it goes on to its goal, since a
host that has stopped publishing leaves nothing to say where the arm is."""
HOLD_READ_POLL_MS = 20
HOLD_READ_MESSAGES = 2
"""Which message after the stop a hold is built from. The host reads its motors, then its cameras,
and publishes, and can stick anywhere in that, so the first to come in can hold a reading from
before the stop, with the servo gone on since. The second's motors were read after the first went
out."""
CONNECT_TIMEOUT_S = 5.0
CALL_TIMEOUT_S = 2.0

_VEL_KEYS = ("x.vel", "y.vel", "theta.vel")


class HostLink(Protocol):
    """The socket pair, injectable so tests drive the mapping without pyzmq."""

    def open(self, host: str, cmd_port: int, obs_port: int) -> None: ...

    def send(self, text: str) -> None: ...

    def recv(self, timeout_ms: int) -> str | None: ...

    def close(self) -> None: ...


class _PyZmqLink:
    """The real sockets. Built only inside `connect()`, so a machine without the extra can
    still list, validate and mock this robot."""

    def __init__(self) -> None:
        try:
            import zmq
        except ImportError as e:  # pragma: no cover - exercised by tests/test_extras_absent
            raise AdapterNotInstalled("xlerobot", "quackd[xlerobot]") from e
        self._zmq = zmq
        self._ctx: Any = None
        self._cmd: Any = None
        self._obs: Any = None

    def open(self, host: str, cmd_port: int, obs_port: int) -> None:
        zmq = self._zmq
        self._ctx = zmq.Context()
        # the host BINDS both sockets, so quackd connects; CONFLATE on each, as upstream sets
        # LINGER 0 at creation, not only on the close() path. A context that is garbage
        # collected with a socket still holding a message to a peer that has gone calls
        # term() with the default linger, which is forever: that held a macOS test run
        # alive for six hours after one failed test skipped its close().
        self._cmd = self._ctx.socket(zmq.PUSH)
        self._cmd.setsockopt(zmq.LINGER, 0)
        self._cmd.setsockopt(zmq.CONFLATE, 1)
        self._cmd.connect(f"tcp://{host}:{cmd_port}")
        self._obs = self._ctx.socket(zmq.PULL)
        self._obs.setsockopt(zmq.LINGER, 0)
        self._obs.setsockopt(zmq.CONFLATE, 1)
        self._obs.connect(f"tcp://{host}:{obs_port}")

    def send(self, text: str) -> None:
        self._cmd.send_string(text, flags=self._zmq.NOBLOCK)

    def recv(self, timeout_ms: int) -> str | None:
        if self._obs.poll(timeout_ms):
            return str(self._obs.recv_string())
        return None

    def close(self) -> None:
        for sock in (self._cmd, self._obs):
            if sock is not None:
                sock.close(linger=0)
        if self._ctx is not None:
            self._ctx.term()
        self._cmd = self._obs = self._ctx = None


def split_address(address: str | None) -> tuple[str, int, int]:
    """`tcp://host:5555` -> (host, 5555, 5556).

    The observation port defaults to the command port plus one, which is what upstream's two
    defaults are. `?obs=NNNN` overrides it, for a host whose ports were assigned rather than
    configured."""
    if not address:
        return DEFAULT_HOST, CMD_PORT, OBS_PORT
    parts = urlsplit(address if "://" in address else f"tcp://{address}")
    host = parts.hostname or DEFAULT_HOST
    cmd = parts.port or CMD_PORT
    wanted = parse_qs(parts.query).get("obs", [""])[0]
    obs = int(wanted) if wanted.isdigit() else cmd + 1
    return host, cmd, obs


def pick_camera(observation: dict[str, Any]) -> str | None:
    """Which key is a camera. The seventeen state keys are floats and the host writes each
    camera as a base64 string, so a string value is the marker (`upstream_api.CAMERA_KEY_SHAPE`).

    A head camera is preferred over a wrist one: a wrist view answers `observe` but gives a
    bearing that means nothing for navigation."""
    names = sorted(k for k, v in observation.items() if isinstance(v, str))
    if not names:
        return None
    return next((n for n in names if "head" in n.lower()), names[0])


class XLerobotZmq:
    name = "zmq"
    mobility = "wheeled"

    def __init__(
        self,
        *,
        address: str | None = None,
        variant: str = "omni3",
        client: HostLink | None = None,
        stale_limit_ms: float = STALE_LIMIT_MS,
        timeout_s: float = CALL_TIMEOUT_S,
        connect_timeout_s: float = CONNECT_TIMEOUT_S,
        swap_colour: bool = True,
    ) -> None:
        self.host, self.cmd_port, self.obs_port = split_address(address)
        self.variant = variant
        self.stale_limit_ms = stale_limit_ms
        self.timeout_s = timeout_s
        self.connect_timeout_s = connect_timeout_s
        self.swap_colour = swap_colour
        self._link = client
        self._lock = threading.Lock()
        self._connected = False
        # what quackd wants the robot to be doing; re-sent whole so a conflated drop recovers
        self._desired: dict[str, float] = dict.fromkeys(_VEL_KEYS, 0.0)
        self._state: dict[str, float] = {}
        self._frame_b64: str | None = None
        self._rx_at: float | None = None
        self.camera_available = False
        self.camera_key: str | None = None
        self.holding: dict[str, bool] = dict.fromkeys(ARMS, False)
        self.post_sleep: Any = None
        self._unheld: set[str] = set()
        """The arm joints sent a goal that no stop has held since (`_hold_the_arms`)."""
        self.stop_left_going: tuple[str, ...] = ()
        """The joints the last stop could not hold, for want of a recent reading."""

    # ── the link ────────────────────────────────────────────────────────────────────

    def _require_link(self) -> HostLink:
        if self._link is None:
            raise TransportError("xlerobot: not connected")
        return self._link

    def _send_locked(self, payload: dict[str, float]) -> None:
        with self._lock:
            self._require_link().send(json.dumps(payload))

    def _recv_locked(self, timeout_ms: int) -> str | None:
        with self._lock:
            return self._require_link().recv(timeout_ms)

    def _open_locked(self) -> None:
        with self._lock:
            self._require_link().open(self.host, self.cmd_port, self.obs_port)

    def _close_locked(self) -> None:
        with self._lock:
            if self._link is not None:
                self._link.close()

    async def _call(self, fn: Any, *args: Any) -> Any:
        try:
            return await asyncio.wait_for(asyncio.to_thread(fn, *args), timeout=self.timeout_s)
        except TimeoutError as e:
            raise TransportError(f"xlerobot: the host did not answer in {self.timeout_s}s") from e
        except TransportError:
            raise
        except Exception as e:
            # Refusal is data, never an exception. Whatever the socket raised (the host
            # exited on its own timer, the process died, the cable went), the pilot is
            # told the link is gone rather than handed a ZMQ error through the
            # executor's catch-all, which reads like a crash in quackd.
            raise TransportError(
                f"xlerobot: the host at tcp://{self.host}:{self.cmd_port} stopped answering "
                f"({type(e).__name__}: {e}). It exits by itself after an hour, so if it "
                "has been running a while, restart it on the robot."
            ) from e

    async def _pump(self, timeout_ms: int = POLL_MS) -> bool:
        """Take the newest observation if one is waiting. Stamps on arrival: nothing on the
        wire carries a time, and a cached reading served as fresh is how a stopped robot looks
        like a moving one."""
        raw = await self._call(self._recv_locked, timeout_ms)
        if raw is None:
            return False
        try:
            observation = dict(json.loads(raw))
        except (ValueError, TypeError):
            return False
        # bool is a subclass of int, so an added flag would otherwise read back as state
        floats = {
            k: float(v)
            for k, v in observation.items()
            if isinstance(v, (int, float)) and not isinstance(v, bool)
        }
        key = pick_camera(observation)
        if key is not None:
            self.camera_available = True
            self.camera_key = key
            frame = observation.get(key)
            self._frame_b64 = frame if isinstance(frame, str) and frame else None
        self._state = floats
        self._rx_at = time.monotonic()
        return True

    def _stale_ms(self) -> float:
        if self._rx_at is None:
            return float("inf")
        return (time.monotonic() - self._rx_at) * 1000.0

    # ── protocol ────────────────────────────────────────────────────────────────────

    async def connect(self) -> None:
        if self._link is None:
            self._link = await asyncio.to_thread(_PyZmqLink)
        # Under the lock like every other socket call. A leftover worker from a timed-out
        # `_call` can still be inside poll() or send_string() on this socket, and ZeroMQ
        # sockets are not safe across threads.
        await self._call(self._open_locked)
        self._connected = True
        # the handshake is "did an observation come back": there is no hello on this wire
        deadline = time.monotonic() + self.connect_timeout_s
        while time.monotonic() < deadline:
            if await self._pump(POLL_MS):
                return
        raise TransportError(
            f"xlerobot: no observation from tcp://{self.host}:{self.obs_port} in "
            f"{self.connect_timeout_s:.0f}s. Is the host running on the robot? It is commented "
            "out of upstream's package by default, and it exits by itself after an hour."
        )

    async def close(self) -> None:
        if self._link is not None and self._connected:
            # never upstream's disconnect(): that disables torque and the arms drop
            await self.stop()
            with contextlib.suppress(Exception):
                # closing a socket that is already gone is not a failure worth propagating
                # Under the lock: a worker left behind by a timed-out `_call` may still be
                # inside a poll on this socket, and closing it underneath one is undefined.
                await self._call(self._close_locked)
        self._connected = False

    async def get_frame(self) -> Image.Image | None:
        await self._pump(0)
        if not self._frame_b64:
            return None
        try:
            opened = Image.open(BytesIO(base64.b64decode(self._frame_b64, validate=True)))
            opened.load()
        except (binascii.Error, ValueError, OSError, UnidentifiedImageError):
            return None
        img: Image.Image = opened.convert("RGB")
        if self.swap_colour:
            # the host encodes an RGB array with a call that assumes BGR, so the stored order
            # is swapped and swapping back is what recovers it (upstream_api.CAMERA_COLOR_ORDER)
            b, g, r = img.split()
            img = Image.merge("RGB", (r, g, b))
        return img

    async def get_state(self) -> DuckState:
        await self._pump(0)
        joints = {j: self._state[f"{j}.pos"] for j in JOINTS if f"{j}.pos" in self._state}
        wz_deg = self._state.get("theta.vel", 0.0)
        return DuckState(
            t=time.monotonic(),
            policy="idle",
            posture="unknown",
            fallen=False,
            # no data link to the power station: this is unknowable, not merely unknown
            battery_percent=None,
            # no odometry anywhere in the observation: velocities only, never a pose
            x=None,
            y=None,
            theta=None,
            holding=any(self.holding.values()),
            extras={
                "joints": {k: round(v, 1) for k, v in joints.items()},
                "holding": dict(self.holding),
                "twist": {
                    "vx": self._state.get("x.vel", 0.0),
                    "vy": self._state.get("y.vel", 0.0),
                    "wz": math.radians(wz_deg),
                },
                "variant": self.variant,
                "camera_key": self.camera_key,
                "stale_ms": round(self._stale_ms(), 1),
                "stale_limit_ms": self.stale_limit_ms,
                "host": f"tcp://{self.host}:{self.cmd_port}",
            },
        )

    async def send_intent(self, intent: Intent) -> Ack:
        if not self._connected:
            return Ack(accepted=False, reason="xlerobot: not connected")
        p = intent.params
        match intent.kind:
            case "move":
                vy = float(p.get("vy", 0.0))
                if self.variant != "omni3" and vy:
                    return Ack(
                        accepted=False,
                        reason=f"this robot's {self.variant} base cannot strafe; vy is ignored "
                        "by the robot, so quackd refuses rather than promising the move",
                    )
                self._desired["x.vel"] = float(p.get("vx", 0.0))
                self._desired["y.vel"] = vy
                # quackd speaks rad/s, the wire is deg/s (upstream_api.THETA_VEL_IS_DEGPS)
                self._desired["theta.vel"] = math.degrees(float(p.get("wz", 0.0)))
            case "stop":
                await self._stopping()
            case "joint":
                goals = {str(k): float(v) for k, v in dict(p.get("positions", {})).items()}
                unknown = sorted(set(goals) - set(JOINTS))
                if unknown:
                    return Ack(accepted=False, reason=f"unknown joints {unknown}")
                self._desired.update({f"{k}.pos": v for k, v in goals.items()})
                self._unheld.update(f"{k}.pos" for k in goals if not k.endswith("gripper"))
                self._halt_base()  # upstream writes the wheels on every action
            case "gripper":
                open_ = bool(p.get("open", True))
                sides = list(ARMS) if p.get("side") == "both" else [str(p.get("side", "right"))]
                for side in sides:
                    if side not in ARMS:
                        return Ack(accepted=False, reason=f"no {side!r} arm on this robot")
                # the verb supplies goals; a bare Intent.gripper(open=...) does not, and a
                # gripper that moved nothing while reporting `holding` would be a lie, so the
                # goal is derived here rather than trusted from the caller
                goals = {str(k): float(v) for k, v in dict(p.get("positions", {})).items()}
                if not goals:
                    goals = {
                        f"{side}_arm_gripper": GRIPPER_OPEN if open_ else GRIPPER_CLOSED
                        for side in sides
                    }
                self._desired.update({f"{k}.pos": v for k, v in goals.items()})
                for side in sides:
                    self.holding[side] = not open_  # commanded, never sensed
                self._halt_base()
            case "enable":
                if not p.get("on", True):
                    return Ack(accepted=False, reason="quackd never limps a robot")
                return Ack()
            case _:
                return Ack(accepted=False, reason=f"an XLeRobot cannot {intent.kind}")
        try:
            await self._call(self._send_locked, dict(self._desired))
        except TransportError as e:
            return Ack(accepted=False, reason=str(e))
        return Ack()

    def _halt_base(self) -> None:
        for key in _VEL_KEYS:
            self._desired[key] = 0.0

    async def _stopping(self) -> None:
        """Stop zeroes the wheels and holds the arm where it stands.

        A stop that left the goals alone was a stop only for the wheels: an arm on its way
        somewhere got there, with a person's hand in the way or not, as upstream's `stop_base`
        lets it. So each joint with a goal no stop has held since is sent where it reads, as
        the SO-101's stop does (`_hold_the_arms`). A reading takes up to a cycle of the host's to
        come, so the wheels do not wait on it. quackd never sends the disconnect that would
        disable torque and drop whatever is held."""
        self._halt_base()
        fresh = False
        if self._unheld:
            with contextlib.suppress(Exception):
                await self._call(self._send_locked, dict(self._desired))
            fresh = await self._reading_from_now_on()
        self._hold_the_arms(fresh)

    async def _reading_from_now_on(self) -> bool:
        """Whether a reading taken after this began has come in. The socket keeps the newest
        message however long it has sat there, and nothing on the wire says how old it is, so
        what is waiting is thrown away and `HOLD_READ_MESSAGES` more are waited for. A heartbeat
        that takes one first leaves the next, a cycle later. Its own clock rather than
        `asyncio.timeout`, whose one cancel a `wait_for` under it can swallow on 3.11, and then
        nothing ended this on a host that had gone quiet."""
        end = time.monotonic() + HOLD_READ_S
        got = 0
        with contextlib.suppress(Exception):
            await self._call(self._recv_locked, 0)
            while got < HOLD_READ_MESSAGES and time.monotonic() < end:
                got += 1 if await self._pump(HOLD_READ_POLL_MS) else 0
        return got >= HOLD_READ_MESSAGES

    def _hold_the_arms(self, fresh: bool) -> None:
        """The reading becomes the goal, under three rules, each of which is about a stop that
        would otherwise move an arm.

        Only a reading taken after the stop began is used (`fresh`), since a goal built from an
        older one is a move of its own: a fake host caught a stop built from an observation that
        had not caught up, which sent the arm back to zero (ADR-0026), and one that had sat unread
        while the host stalled sent an arm back as far as it had travelled in the stall. Failing
        that, the joint goes on to the goal it was sent, the transport says so, and the next stop
        tries again.

        A joint is held once for each goal it is sent. A servo under load reads below its goal,
        so sending it its reading lets it sink by that much, and every failed verb ends in a
        stop: held every time, an arm carrying something would walk down a stop at a time.

        A gripper is never held: sending it what it reads would open a hand that is squeezing
        something against its goal. The goal it has stands, as on the SO-101, and the head has
        no goal here unless a verb sent it one."""
        if fresh:
            for key in self._unheld & set(self._state):
                self._desired[key] = self._state[key]
            self._unheld -= set(self._state)
        self.stop_left_going = tuple(sorted(k.removesuffix(".pos") for k in self._unheld))

    async def subscribe(self, topic: str) -> AsyncIterator[dict[str, Any]]:  # type: ignore[override]
        while self._connected:
            await self.sleep(0.5)
            yield {"topic": topic, **(await self.get_state()).model_dump()}

    async def heartbeat(self) -> None:
        await self._pump(POLL_MS)
        stale = self._stale_ms()
        if stale > self.stale_limit_ms:
            raise HeartbeatError(
                f"xlerobot: no observation for {stale:.0f} ms (limit {self.stale_limit_ms:.0f}). "
                "The host has stopped answering; it exits by itself an hour after it was "
                "started, and its own watchdog has already stopped the base."
            )

    async def stop(self) -> None:
        """Safe to call many times, from anywhere, at any time - including when the link is
        already gone, which is exactly when the executor calls it."""
        await self._stopping()
        if self._link is None:
            return
        try:
            await self._call(self._send_locked, dict(self._desired))
        except Exception:
            # Deliberately broad. A dead socket raises its own library's error, not ours, and
            # the host is unreachable anyway, so its 500 ms watchdog is what stops the base
            # now. Raising here would turn a stop into a failed verb and hide that.
            return

    def now(self) -> float:
        return time.monotonic()

    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(seconds)
        if self.post_sleep is not None:
            self.post_sleep()


__all__ = [
    "CMD_PORT",
    "OBS_PORT",
    "STALE_LIMIT_MS",
    "HostLink",
    "XLerobotZmq",
    "pick_camera",
    "split_address",
]
