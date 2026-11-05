"""EXPERIMENTAL: the real AlohaMini, over the ZeroMQ host protocol it already ships.

quackd connects PUSH to the command port and DEALER to the observation port, which is
request/reply rather than a stream: nothing arrives unasked
(`upstream_api.OBSERVATION_REQUEST_REPLY`). Nothing from the fork is imported; every name here
is in `upstream_api.py`.

Four properties of that protocol shape this file, and the first two decide whether a stop is
real:

- **All three `.vel` keys are mandatory in every payload.** `send_action` indexes them with no
  default, so omitting one raises before the lift is touched and before any bus write: the
  whole action is discarded, arms included, and the watchdog is never refreshed.
- **The lift latches, and its two action keys are not symmetric.** `apply_action` runs the
  height branch and then the velocity branch, both unconditionally guarded by `if key in
  action`, so a payload carrying BOTH keys ends with velocity winning and the lift frozen,
  while a payload carrying NEITHER leaves the register untouched and the lift travelling.
  Therefore: to move the lift, send the height alone; to stop it, send the velocity zero.
  `_payload()` is the only place that decision is made.
- **The host exits on its own**, after 6000 s or after an over-current trip that calls
  `sys.exit(1)`. Both look the same from here, so both surface as staleness.
- **Nothing is timestamped**, and upstream's own client serves its cache on a timeout, where
  the cached state starts empty. quackd tracks a sequence and only surfaces a reading that
  actually advanced it.

Never run against a robot by us.
"""

from __future__ import annotations

import asyncio
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
from quackd_alohamini import upstream_api as up
from quackd_alohamini.verbs import (
    ARMS,
    DEFAULT_MODEL,
    GRIPPER_CLOSED,
    GRIPPER_OPEN,
    joints_for,
    model_from_keys,
)

DEFAULT_HOST = "127.0.0.1"
CMD_PORT = int(up.PORT_ZMQ_CMD.name)
OBS_PORT = int(up.PORT_ZMQ_OBSERVATIONS.name)
"""Both from `upstream_api`, not retyped here. `upstream_api.py` is the one file to
edit when an upstream moves, and a port spelled twice can disagree with itself."""
WATCHDOG_MS = float(up.WATCHDOG_TIMEOUT_MS.name)
"""The host's own watchdog window, from `upstream_api`, not retyped. By the time an
observation is this old the host has already stopped the base and the lift, so this is
the moment a cached reading stops being a reading."""
POLL_MS = 200
"""Upstream's own client poll window: it must exceed one host cycle at 30 Hz."""
CONNECT_TIMEOUT_S = 5.0
CALL_TIMEOUT_S = 3.0
STOP_REPEATS = 3
"""`stop_base` writes with num_retry=0 and shares a bus with the lift, so one lost serial
packet loses the stop. Saying it more than once is cheap."""
HOLD_READ_S = 0.2
"""How long a stop looks for a reading from after it began, once the wheels and the lift are
stopped: a request and its reply, where the host answers one request a cycle at 30 Hz. The arm
goes on meanwhile, and past this it goes on to its goal."""
HOLD_READ_POLL_MS = 20
HOLD_READ_ANSWERS = 2
"""Which answer after the stop a hold is built from. The host reads its motors once a cycle and
answers from that, and can stick in between, so the first answer can hold a reading from before the
stop, with the servo gone on since. The second's motors were read after the first went out."""

VEL_KEYS = ("x.vel", "y.vel", "theta.vel")
LIFT_HEIGHT_KEY = "lift_axis.height_mm"
LIFT_VEL_KEY = "lift_axis.vel"


class HostLink(Protocol):
    """The socket pair, injectable so tests drive the mapping without pyzmq."""

    def open(self, host: str, cmd_port: int, obs_port: int) -> None: ...

    def send(self, text: str) -> None: ...

    def request(self, token: bytes, timeout_ms: int) -> list[bytes] | None: ...

    def answer(self, timeout_ms: int) -> list[bytes] | None: ...

    def close(self) -> None: ...


class _PyZmqLink:
    """The real sockets. Built only inside `connect()`, so a machine without the extra can
    still list, validate and mock this robot."""

    def __init__(self) -> None:
        try:
            import zmq
        except ImportError as e:  # pragma: no cover - exercised by tests/test_extras_absent
            raise AdapterNotInstalled("alohamini", "quackd[alohamini]") from e
        self._zmq = zmq
        self._ctx: Any = None
        self._cmd: Any = None
        self._obs: Any = None

    def open(self, host: str, cmd_port: int, obs_port: int) -> None:
        zmq = self._zmq
        self._ctx = zmq.Context()
        # LINGER 0 at creation, not only on the close() path. A context that is garbage
        # collected with a socket still holding a message to a peer that has gone calls
        # term() with the default linger, which is forever: that held a macOS test run
        # alive for six hours after one failed test skipped its close().
        self._cmd = self._ctx.socket(zmq.PUSH)
        self._cmd.setsockopt(zmq.LINGER, 0)
        self._cmd.setsockopt(zmq.CONFLATE, 1)  # as upstream's host sets on its PULL
        self._cmd.connect(f"tcp://{host}:{cmd_port}")
        self._obs = self._ctx.socket(zmq.DEALER)
        self._obs.setsockopt(zmq.LINGER, 0)
        self._obs.setsockopt(zmq.SNDHWM, 3)  # upstream's observation_request_window
        self._obs.setsockopt(zmq.RCVHWM, 3)
        self._obs.connect(f"tcp://{host}:{obs_port}")

    def send(self, text: str) -> None:
        self._cmd.send_string(text, flags=self._zmq.NOBLOCK)

    def request(self, token: bytes, timeout_ms: int) -> list[bytes] | None:
        self._obs.send(token, flags=self._zmq.NOBLOCK)
        return self.answer(timeout_ms)

    def answer(self, timeout_ms: int) -> list[bytes] | None:
        if self._obs.poll(timeout_ms):
            parts: list[bytes] = self._obs.recv_multipart()
            return parts
        return None

    def close(self) -> None:
        for sock in (self._cmd, self._obs):
            if sock is not None:
                sock.close(linger=0)
        if self._ctx is not None:
            self._ctx.term()
        self._cmd = self._obs = self._ctx = None


def split_address(address: str | None) -> tuple[str, int, int]:
    """`tcp://host:5555` -> (host, 5555, 5556); `?obs=NNNN` overrides the observation port."""
    if not address:
        return DEFAULT_HOST, CMD_PORT, OBS_PORT
    parts = urlsplit(address if "://" in address else f"tcp://{address}")
    host = parts.hostname or DEFAULT_HOST
    cmd = parts.port or CMD_PORT
    wanted = parse_qs(parts.query).get("obs", [""])[0]
    return host, cmd, int(wanted) if wanted.isdigit() else cmd + 1


class AlohaMiniZmq:
    name = "zmq"
    mobility = "wheeled"

    def __init__(
        self,
        *,
        address: str | None = None,
        model: str = DEFAULT_MODEL,
        client: HostLink | None = None,
        stale_limit_ms: float = WATCHDOG_MS,
        timeout_s: float = CALL_TIMEOUT_S,
        connect_timeout_s: float = CONNECT_TIMEOUT_S,
    ) -> None:
        self.host, self.cmd_port, self.obs_port = split_address(address)
        self.robot_model = model
        self.stale_limit_ms = stale_limit_ms
        self.timeout_s = timeout_s
        self.connect_timeout_s = connect_timeout_s
        self._link = client
        self._lock = threading.Lock()
        self._connected = False
        self._token = 0
        self.sequence = 0
        """Advances only when a reply actually decoded, which is the one honest freshness
        signal on this wire."""
        self._arm_goals: dict[str, float] = {}
        self._vel: dict[str, float] = dict.fromkeys(VEL_KEYS, 0.0)
        self._lift_target_mm: float | None = None
        self._state: dict[str, float] = {}
        self._frames: dict[str, bytes] = {}
        self._rx_at: float | None = None
        self._answered = 0
        """The newest request the host has answered. A DEALER keeps a reply that came in behind
        its time, so the one read can be the answer to an earlier request, however old, and
        `_reading_from_now_on` wants an answer to a request sent since it began."""
        self._unheld: set[str] = set()
        """The arm joints sent a goal that no stop has held since (`_hold_the_arms`)."""
        self.stop_left_going: tuple[str, ...] = ()
        """The joints the last stop could not hold, for want of a recent reading."""
        self.camera_available = False
        self.cameras: tuple[str, ...] = ()
        self.arms_available = True
        self.arm_torque = False
        self.calibrated = False
        self.holding: dict[str, bool] = dict.fromkeys(ARMS, False)
        self.post_sleep: Any = None

    # ── the payload, which is the whole safety story ────────────────────────────────

    def _payload(self) -> dict[str, float]:
        """The only place a payload is built, because there is only one way to build a safe one.

        Every payload carries all three velocity keys, or the robot discards the whole action.
        And every payload carries exactly one lift key: the height when quackd is deliberately
        driving the lift, and a velocity zero the rest of the time. Sending both would let the
        velocity branch overwrite the height branch and freeze the lift; sending neither would
        leave the register latched and the lift travelling."""
        payload: dict[str, float] = {**self._arm_goals, **self._vel}
        if self._lift_target_mm is None:
            payload[LIFT_VEL_KEY] = 0
        else:
            payload[LIFT_HEIGHT_KEY] = float(self._lift_target_mm)
        return payload

    def _require_link(self) -> HostLink:
        if self._link is None:
            raise TransportError("alohamini: not connected")
        return self._link

    def _send_locked(self, payload: dict[str, float]) -> None:
        with self._lock:
            self._require_link().send(json.dumps(payload))

    def _request_locked(self, timeout_ms: int) -> list[bytes] | None:
        with self._lock:
            self._token += 1
            return self._require_link().request(str(self._token).encode("ascii"), timeout_ms)

    def _answer_locked(self, timeout_ms: int) -> list[bytes] | None:
        with self._lock:
            return self._require_link().answer(timeout_ms)

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
            raise TransportError(f"alohamini: the host did not answer in {self.timeout_s}s") from e
        except TransportError:
            raise
        except Exception as e:
            # Refusal is data, never an exception. Whatever the socket raised (the host
            # exited on its own timer, the process died, the cable went), the pilot is
            # told the link is gone rather than handed a ZMQ error through the
            # executor's catch-all, which reads like a crash in quackd.
            raise TransportError(
                f"alohamini: the host at tcp://{self.host}:{self.cmd_port} stopped answering "
                f"({type(e).__name__}: {e}). It exits by itself after about 100 minutes, "
                "and also when a motor draws too much current, so check it on the robot."
            ) from e

    async def _send(self) -> None:
        await self._call(self._send_locked, self._payload())

    async def _pump(self, timeout_ms: int = POLL_MS) -> bool:
        """One request/reply round. Returns True only when a reply actually decoded. A reply behind
        its time is read past, or every read for the rest of the session would be that much older
        than it says."""
        took = self._took(await self._call(self._request_locked, timeout_ms))
        while took and self._answered < self._token:
            parts = await self._call(self._answer_locked, 0)
            if not parts:
                break
            self._took(parts)
        return took

    def _took(self, parts: list[bytes] | None) -> bool:
        """Keep what a reply says. True only where it decoded."""
        if not parts or len(parts) < 2:
            return False
        try:
            observation = dict(json.loads(parts[1]))
        except (ValueError, TypeError):
            return False
        if observation.get("_image_encoding") not in (None, "jpeg"):
            raise TransportError(
                f"alohamini: this host encodes images as "
                f"{observation.get('_image_encoding')!r}, which quackd does not read"
            )
        names = [str(n) for n in (observation.get("_images") or [])]
        frames: dict[str, bytes] = {}
        for i in range(2, len(parts) - 1, 2):
            frames[parts[i].decode("utf-8", "replace")] = parts[i + 1]
        self._state = {
            k: float(v)
            for k, v in observation.items()
            if isinstance(v, (int, float)) and not isinstance(v, bool)
        }
        self._frames = frames
        self.cameras = tuple(names or frames)
        self.camera_available = bool(self.cameras)
        self.robot_model = model_from_keys(self._state)
        self.arms_available = any(k.startswith("arm_") for k in self._state)
        # quackd's own host wrapper says so; a stock host never enables arm torque
        self.arm_torque = bool(observation.get("quackd_arm_torque", False))
        self.calibrated = bool(observation.get("quackd_calibrated", True))
        self._rx_at = time.monotonic()
        if parts[0].isdigit():
            self._answered = max(self._answered, int(parts[0]))
        self.sequence += 1
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
        # Before anything else: homing left full-speed descent in the lift's register and the
        # zeroing write after it is commented out upstream, so the lift is travelling right
        # now. The first thing quackd says to this robot is stop.
        await self.stop()
        deadline = time.monotonic() + self.connect_timeout_s
        while time.monotonic() < deadline:
            if await self._pump(POLL_MS):
                return
        raise TransportError(
            f"alohamini: no observation from tcp://{self.host}:{self.obs_port} in "
            f"{self.connect_timeout_s:.0f}s. Is the host running on the robot? It exits by "
            "itself after about 100 minutes, and also on an over-current trip."
        )

    async def close(self) -> None:
        if self._link is not None and self._connected:
            # never upstream's disconnect(): it disables torque and a loaded arm falls
            await self.stop()
            with contextlib.suppress(Exception):
                # Under the lock: a worker left behind by a timed-out `_call` may still be
                # inside a poll on this socket, and closing it underneath one is undefined.
                await self._call(self._close_locked)
        self._connected = False

    async def get_frame(self) -> Image.Image | None:
        """The first camera the host actually sent, decoded with Pillow.

        The colour chain swaps twice on the way here and most likely lands back on RGB, so
        quackd does not swap again (`upstream_api.CAMERA_COLOUR_ORDER`)."""
        await self._pump()
        for name in self.cameras:
            raw = self._frames.get(name)
            if not raw:
                continue
            try:
                img = Image.open(BytesIO(raw), formats=["JPEG"])
                img.load()
            except (ValueError, OSError, UnidentifiedImageError, Image.DecompressionBombError):
                return None
            return img.convert("RGB")
        return None

    async def get_state(self) -> DuckState:
        # A real request with a real timeout. This wire is request/reply: nothing arrives
        # unasked, so polling for zero milliseconds would send a token, give up before any
        # reply could arrive, and serve the cache every single time.
        await self._pump()
        joints = {
            j: self._state[f"{j}.pos"]
            for j in joints_for(self.robot_model)
            if f"{j}.pos" in self._state
        }
        return DuckState(
            t=time.monotonic(),
            policy="idle",
            posture="unknown",
            fallen=False,
            battery_percent=None,
            # velocities only: there is no pose anywhere in this observation
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
                    "wz": math.radians(self._state.get("theta.vel", 0.0)),
                },
                "lift_height_mm": self._state.get(LIFT_HEIGHT_KEY),
                "robot_model": self.robot_model,
                "arm_torque": self.arm_torque,
                "calibrated": self.calibrated,
                "cameras": list(self.cameras),
                "sequence": self.sequence,
                "stale_ms": round(self._stale_ms(), 1),
                "stale_limit_ms": self.stale_limit_ms,
                "host": f"tcp://{self.host}:{self.cmd_port}",
            },
        )

    async def send_intent(self, intent: Intent) -> Ack:
        if not self._connected:
            return Ack(accepted=False, reason="alohamini: not connected")
        p = intent.params
        match intent.kind:
            case "move":
                self._vel["x.vel"] = float(p.get("vx", 0.0))
                self._vel["y.vel"] = float(p.get("vy", 0.0))
                # quackd speaks rad/s, this wire is deg/s
                self._vel["theta.vel"] = math.degrees(float(p.get("wz", 0.0)))
            case "stop":
                await self._stopping()
            case "pose":
                target = p.get("lift_height_mm")
                if target is None:
                    return Ack(accepted=False, reason="pose needs a lift_height_mm")
                self._lift_target_mm = float(target)
                self._zero_base()
            case "joint":
                if not self.arm_torque:
                    return Ack(accepted=False, reason="the arms have no torque on this host")
                goals = {str(k): float(v) for k, v in dict(p.get("positions", {})).items()}
                known = set(joints_for(self.robot_model))
                unknown = sorted(set(goals) - known)
                if unknown:
                    return Ack(accepted=False, reason=f"unknown joints {unknown}")
                self._arm_goals.update({f"{k}.pos": v for k, v in goals.items()})
                self._unheld.update(f"{k}.pos" for k in goals if not k.endswith("gripper"))
                self._zero_base()
            case "gripper":
                if not self.arm_torque:
                    return Ack(accepted=False, reason="the arms have no torque on this host")
                open_ = bool(p.get("open", True))
                sides = list(ARMS) if p.get("side") == "both" else [str(p.get("side", "right"))]
                for side in sides:
                    if side not in ARMS:
                        return Ack(accepted=False, reason=f"no {side!r} arm on this robot")
                goals = {str(k): float(v) for k, v in dict(p.get("positions", {})).items()}
                if not goals:
                    goals = {
                        f"arm_{side}_gripper": GRIPPER_OPEN if open_ else GRIPPER_CLOSED
                        for side in sides
                    }
                self._arm_goals.update({f"{k}.pos": v for k, v in goals.items()})
                for side in sides:
                    self.holding[side] = not open_  # commanded, never sensed
                self._zero_base()
            case "enable":
                if not p.get("on", True):
                    return Ack(accepted=False, reason="quackd never limps a robot")
                return Ack()
            case _:
                return Ack(accepted=False, reason=f"an AlohaMini cannot {intent.kind}")
        try:
            await self._send()
        except TransportError as e:
            return Ack(accepted=False, reason=str(e))
        return Ack()

    def _zero_base(self) -> None:
        for key in VEL_KEYS:
            self._vel[key] = 0.0

    def _halt(self) -> None:
        """Zero the wheels and zero the lift."""
        self._zero_base()
        self._lift_target_mm = None  # so `_payload` emits the lift velocity zero

    async def _stopping(self) -> None:
        """The wheels and the lift, at once, and then the arms where they stand.

        A stop that left the goals alone was a stop only for the base: an arm on its way
        somewhere got there, with a person's hand in the way or not. A reading here takes a
        request and a reply, so the base does not wait on it."""
        self._halt()
        fresh = False
        if self._unheld:
            with contextlib.suppress(Exception):
                await self._send()
            fresh = await self._reading_from_now_on()
        self._hold_the_arms(fresh)

    async def _reading_from_now_on(self) -> bool:
        """Whether the host has answered, `HOLD_READ_ANSWERS` times, requests sent after this began.
        One request at a time, read down to its answer: the host answers one a cycle, so asking
        again before it has answered only leaves replies behind for every read after this, each
        an older reading than the last, with the arm's position marked fresh. A heartbeat's
        request sent since then will do as well, whichever of the two reads the answer. Its own
        clock rather than `asyncio.timeout`, whose one cancel a `wait_for` under it can swallow on
        3.11, and then nothing ended this on a host that had gone quiet."""
        end = time.monotonic() + HOLD_READ_S
        with contextlib.suppress(Exception):
            for _ in range(HOLD_READ_ANSWERS):
                asked = self._token + 1
                self._took(await self._call(self._request_locked, HOLD_READ_POLL_MS))
                while self._answered < asked:
                    if time.monotonic() >= end:
                        return False
                    self._took(await self._call(self._answer_locked, HOLD_READ_POLL_MS))
            return True
        return False

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
        something against its goal, so the goal it has stands."""
        if fresh:
            for key in self._unheld & set(self._state):
                self._arm_goals[key] = self._state[key]
            self._unheld -= set(self._state)
        self.stop_left_going = tuple(sorted(k.removesuffix(".pos") for k in self._unheld))

    async def stop(self) -> None:
        """Safe to call at any time, including when the link has already gone."""
        await self._stopping()
        if self._link is None:
            return
        for _ in range(STOP_REPEATS):
            try:
                await self._send()
            except Exception:
                # the host is unreachable, so its own watchdog is what stops the base and the
                # lift now. Raising would turn a stop into a failed verb and hide that.
                return

    async def subscribe(self, topic: str) -> AsyncIterator[dict[str, Any]]:  # type: ignore[override]
        while self._connected:
            await self.sleep(0.5)
            yield {"topic": topic, **(await self.get_state()).model_dump()}

    async def heartbeat(self) -> None:
        await self._pump(POLL_MS)
        stale = self._stale_ms()
        if stale > self.stale_limit_ms:
            raise HeartbeatError(
                f"alohamini: no fresh observation for {stale:.0f} ms (limit "
                f"{self.stale_limit_ms:.0f}). The host has stopped answering; it exits by "
                "itself after about 100 minutes, and also on an over-current trip."
            )

    def now(self) -> float:
        return time.monotonic()

    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(seconds)
        if self.post_sleep is not None:
            self.post_sleep()


__all__ = [
    "CMD_PORT",
    "LIFT_HEIGHT_KEY",
    "LIFT_VEL_KEY",
    "OBS_PORT",
    "STOP_REPEATS",
    "VEL_KEYS",
    "AlohaMiniZmq",
    "HostLink",
    "split_address",
]
