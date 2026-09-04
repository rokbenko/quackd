"""EXPERIMENTAL: the real robot, over robotd's JSON-RPC socket.

Every method name here is VERIFIED against upstream's `duck-ipc-proto` at a pinned commit
(see `upstream_api.py`), but nobody has run this against a Microduck. What is honest today:
the handshake and its version refusal, the intent vocabulary, the deadman-friendly `move`
notifications, the state subscription, and the health poll. What is not: posture, which is
inferred from the policy name (UNVERIFIED), and everything about the camera.

Two rules run through this file, and both exist because the robot is on a floor and quackd
is at the other end of an ssh tunnel:

- **Silence is not consent.** A state frame that stops arriving, or arrives without a
  `safety` block, reads as `unknown` rather than as a standing duck, because `fallen: false`
  from a stream nobody is watching is not a fact about the robot. The preconditions refuse
  on `unknown`, so a link that dies stops the duck instead of freeing it.
- **A frame kept is not a frame seen.** `get_frame` expires what it caches. `go_to` steers
  on every tick, so a camera that died with the ball in shot would otherwise show the ball
  in shot forever while the duck walked at it.

Addresses: `unix:///run/robotd.sock` (on the robot, POSIX only) or `tcp://host:port`
(e.g. after `ssh -L 9870:/run/robotd.sock robot`, which also works from Windows).
Frames: `--camera-url http://.../snapshot.jpg`, or `webrtc://host:8443` for `mediad`'s own
video track, which is the only camera upstream offers.
"""

from __future__ import annotations

import asyncio
import contextlib
import io
import json
import logging
import os
import sys
import time
import urllib.request
from collections.abc import AsyncIterator
from typing import Any

from PIL import Image

from quackd.adapters.microduck.webrtc import WebRtcCamera, is_webrtc_url
from quackd.transport import upstream_api as up
from quackd.transport.base import (
    Ack,
    DuckState,
    HeartbeatError,
    Intent,
    Posture,
    TransportError,
)

STATUS = "EXPERIMENTAL — verified method names, unverified against hardware"

log = logging.getLogger("quackd.transport.jsonrpc")

#: How often we ask robotd for a state frame. Ten a second is the rate the walk verb already
#: feeds intents at, and upstream decimates per-subscriber server-side, so asking for less than
#: the loop's 50 Hz costs the robot proportionally less (`up.ROBOT_SUBSCRIBE`).
STATE_HZ = 10


def state_stale_after_s(hz: float) -> float:
    """How old a state frame may be before `get_state` stops believing it.

    Derived from the rate actually subscribed at, not from the default: a transport built with
    `state_hz=2` gets a 2.5 s window, where a fixed 0.3 s one would have declared every healthy
    duck blind half a second after each frame and refused every verb that moves it, with a
    message saying the stream was dead while it streamed exactly as asked.

    Five frame periods, floored at one second so link jitter on an ssh tunnel does not blank
    the posture (`up.STATE_NEEDS_SUBSCRIBE`).
    """
    return max(1.0, 5.0 / max(0.1, hz))


#: The window at the default rate, for tests and for anything reading the module constant.
STATE_STALE_AFTER_S = state_stale_after_s(STATE_HZ)

#: How often to pull a snapshot when `--camera-url` names one. `go_to` and `search_scan` steer
#: at 10 Hz, so this is the rate a control decision can be stale by: keep it well above the
#: pilot's roughly-once-a-second glance, and below what an ssh tunnel will carry.
CAMERA_FPS = 5.0


def camera_stale_after_s(fps: float) -> float:
    """How old a frame may be before `get_frame` stops handing it out.

    A cached frame that never expires is worse than no frame at all: `go_to` steers on every
    tick, and a camera that died with the ball in shot would show the ball in shot forever
    while the duck walked at it. Four frame periods, floored at two seconds so a slow-but-
    working camera is not dropped, and floored again by the fetch timeout so one slow HTTP
    round trip cannot look like a dead camera.
    """
    return max(2.0, 4.0 / max(0.1, fps))


def default_address() -> str:
    root = os.environ.get(up.RUNTIME_DIR_ENV.name, "/run")
    return f"unix://{root}/robotd.sock"


def parse_address(address: str) -> tuple[str, str, int | None]:
    if address.startswith("unix://"):
        return "unix", address[len("unix://") :], None
    if address.startswith("tcp://"):
        host, _, port = address[len("tcp://") :].rpartition(":")
        if not host or not port.isdigit():
            raise TransportError(f"bad tcp address {address!r}; expected tcp://host:port")
        return "tcp", host, int(port)
    if address.startswith("/"):
        return "unix", address, None
    raise TransportError(f"unknown address {address!r}; use unix:///path or tcp://host:port")


class JsonRpcUnixTransport:
    name = "jsonrpc"

    def __init__(
        self,
        address: str | None = None,
        *,
        camera_url: str | None = None,
        api_version: int = int(up.API_VERSION.name),
        request_timeout_s: float = 2.0,
        state_hz: int = STATE_HZ,
        camera_fps: float = CAMERA_FPS,
    ) -> None:
        self.address = address or default_address()
        self.camera_url = camera_url
        self.api_version = api_version
        self.request_timeout_s = request_timeout_s
        self.state_hz = state_hz
        self.camera_fps = camera_fps
        self._camera_task: asyncio.Task[None] | None = None
        self._webrtc: WebRtcCamera | None = None
        self._frame: Image.Image | None = None
        self._frame_at: float | None = None
        self._frame_error: str | None = None
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._pump: asyncio.Task[None] | None = None
        self._pending: dict[int, asyncio.Future[Any]] = {}
        self._notifications: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=256)
        self._next_id = 1
        self._last_state: dict[str, Any] | None = None
        self._last_state_at: float | None = None
        self._state_arrived = asyncio.Event()
        self._last_health: dict[str, Any] | None = None
        self._t0 = time.monotonic()
        self.hello: dict[str, Any] | None = None
        self.stop_error: str | None = None
        """Why the last `stop` did not reach the robot, or None if it did (or none was sent)."""
        self.camera_working = False
        """Whether a frame actually arrived during connect. What the manifest narrows on."""
        self.subscribed: dict[str, Any] | None = None
        """`robot.subscribe`'s answer: the policies and the skill names this robot actually has."""

    # ── wire ────────────────────────────────────────────────────────────────────────

    async def connect(self) -> None:
        kind, host, port = parse_address(self.address)
        try:
            if kind == "unix":
                if sys.platform == "win32":
                    raise TransportError(
                        "unix sockets are not available on Windows; forward the robot's socket "
                        "with `ssh -L 9870:/run/robotd.sock <robot>` and use --address tcp://127.0.0.1:9870"
                    )
                self._reader, self._writer = await asyncio.open_unix_connection(host)
            else:
                self._reader, self._writer = await asyncio.open_connection(host, port)
        except OSError as e:
            raise TransportError(f"cannot connect to {self.address}: {e}") from e
        self._pump = asyncio.create_task(self._read_loop(), name="quackd-jsonrpc-pump")
        # Past this point a task and a socket are open, so every exit has to close them. The
        # callers retry (`mcp_server`, `flock.member`), so a leak here is one task and one fd
        # per attempt, not one per process.
        try:
            await self._handshake()
        except BaseException:
            await self.close()
            raise

    async def _handshake(self) -> None:
        result = await self.request(up.HELLO.name, {"api_version": self.api_version})
        self.hello = result if isinstance(result, dict) else {"result": result}
        remote = self.hello.get("api_version")
        if remote is not None and int(remote) != self.api_version:
            raise TransportError(
                f"robotd speaks API v{remote}, quackd was written against v{self.api_version} "
                f"({up.IPC_PROTO}); refusing rather than guessing"
            )
        # Nothing arrives until we ask (up.STATE_NEEDS_SUBSCRIBE), and everything that decides
        # whether the duck may walk reads the frames this starts: posture, `safety.fallen`, the
        # preconditions in the manifest. Subscribing here rather than in `subscribe()` is what
        # makes those true for every caller instead of only for one that happens to iterate the
        # stream. The answer also names the robot's real skills and policies.
        result = await self.request(up.ROBOT_SUBSCRIBE.name, {"hz": self.state_hz})
        self.subscribed = result if isinstance(result, dict) else {}
        await self._await_first_state()
        # `camera_working` is what the manifest narrows on, and it has to mean "a frame
        # arrived", not "a URL was typed": a typo'd host, a missing extra or a mediad that
        # never reached PLAYING would otherwise still advertise observe, go_to, search_scan
        # and approach_and, every one of which can then only answer "no camera".
        if is_webrtc_url(self.camera_url):
            # The only camera upstream actually offers. Nothing is installed on the robot.
            self._webrtc = WebRtcCamera(str(self.camera_url))
            self.camera_working = await self._webrtc.start()
        elif self.camera_url:
            self._camera_task = asyncio.create_task(
                self._camera_loop(), name="quackd-jsonrpc-camera"
            )
            self.camera_working = await self._await_first_frame()

    async def _await_first_frame(self, timeout_s: float = 5.0) -> bool:
        """Wait for the snapshot loop's first frame, so `connect()` knows if there is a camera."""
        deadline = time.monotonic() + timeout_s
        while self._frame is None and self._frame_error is None:
            if time.monotonic() >= deadline:
                break
            await asyncio.sleep(0.02)
        return self._frame is not None

    async def _await_first_state(self, timeout_s: float = 1.0) -> bool:
        """Wait briefly for the first state frame, so `connect()` returns a duck we can see.

        The subscribe *reply* and the first state *notification* are two messages and nothing
        orders them, so without this the first `get_state()` after connecting can honestly
        report `unknown` on a robot that is about to start streaming. Not fatal if it never
        comes: `get_state` already reads that as unknown and the preconditions refuse, which is
        the outcome we want anyway. Returning the answer lets `doctor` say which happened.
        """
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self._state_arrived.wait(), timeout=timeout_s)
        return self._last_state_at is not None

    async def close(self) -> None:
        if self._webrtc is not None:
            await self._webrtc.close()
            self._webrtc = None
        if self._camera_task is not None:
            self._camera_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._camera_task
            self._camera_task = None
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
                        fut.set_exception(TransportError("robotd closed the connection"))
                return
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            if "id" in msg and msg["id"] is not None and ("result" in msg or "error" in msg):
                pending = self._pending.get(int(msg["id"]))
                if pending is not None and not pending.done():
                    del self._pending[int(msg["id"])]
                    if "error" in msg:
                        err = msg["error"]
                        # Carrying the number lets a caller tell BUSY (retry) from
                        # PERMISSION_DENIED (do not) without parsing the message text. The
                        # codes are in `upstream_api` and were spelled nowhere else.
                        pending.set_exception(
                            TransportError(
                                f"{err.get('code')}: {err.get('message')}", code=err.get("code")
                            )
                        )
                    else:
                        pending.set_result(msg.get("result"))
            elif "method" in msg:
                if msg["method"] == up.ROBOT_STATE.name:
                    # Before the queue push, deliberately: the queue is bounded and drops when
                    # nobody drains it, and freshness must not depend on anyone consuming.
                    self._last_state = msg.get("params") or {}
                    self._last_state_at = time.monotonic()
                    self._state_arrived.set()
                with contextlib.suppress(asyncio.QueueFull):
                    self._notifications.put_nowait(msg)

    def _write(self, obj: dict[str, Any]) -> None:
        if self._writer is None:
            raise TransportError("not connected")
        self._writer.write((json.dumps(obj, separators=(",", ":")) + "\n").encode())

    async def request(self, method: str, params: dict[str, Any] | None = None) -> Any:
        req_id = self._next_id
        self._next_id += 1
        fut: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        self._pending[req_id] = fut
        msg: dict[str, Any] = {"jsonrpc": up.JSONRPC_VERSION, "id": req_id, "method": method}
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
        self._write({"jsonrpc": up.JSONRPC_VERSION, "method": method, "params": params})
        assert self._writer is not None
        await self._writer.drain()

    # ── protocol ────────────────────────────────────────────────────────────────────

    async def _fetch_frame(self) -> Image.Image:
        url = self.camera_url or ""

        def fetch() -> bytes:
            with urllib.request.urlopen(url, timeout=3) as resp:
                return resp.read()

        data = await asyncio.to_thread(fetch)
        return Image.open(io.BytesIO(data)).convert("RGB")

    async def _camera_loop(self) -> None:
        """Keep the newest frame, on a timer, so `get_frame` is a memory read.

        Same shape as the camera daemon quackd already ships for the Open Duck: capture on a
        clock rather than on request, so a slow or broken camera cannot stall the caller and a
        run asking for a frame every step does not pay an HTTP round trip every step.
        """
        period = 1.0 / max(0.1, self.camera_fps)
        while True:
            started = time.monotonic()
            try:
                self._frame = await self._fetch_frame()
                self._frame_at = time.monotonic()
                self._frame_error = None
            except Exception as e:  # a camera hiccup must not end a run
                self._frame_error = str(e)
            await asyncio.sleep(max(0.0, period - (time.monotonic() - started)))

    async def get_frame(self) -> Image.Image | None:
        """The newest frame, or None when there is no camera to read.

        Never raises. It used to raise `TransportError` on a failed snapshot, directly under a
        comment saying snapshot failures must not kill a run — and `AgentLoop._observe` calls
        this every step without catching anything, so one dropped HTTP response ended the
        session. `camera_health()` is where the failure is visible instead.
        """
        if not self.camera_url:
            return None  # no socket-level camera method upstream (up.CAMERA_SNAPSHOT)
        if self._webrtc is not None:
            return self._webrtc.latest(max_age_s=camera_stale_after_s(self.camera_fps))
        if self._camera_task is None:  # not connected: fetch once rather than nothing
            with contextlib.suppress(Exception):
                self._frame = await self._fetch_frame()
                self._frame_at = time.monotonic()
        return None if self.frame_age_s() is None else self._frame

    def frame_age_s(self) -> float | None:
        """Seconds since the newest usable frame, or None if there is none to steer on.

        None covers both "nothing has ever arrived" and "what we have is too old to act on".
        The callers already treat a missing frame as a reason to stop — `go_to` fails on the
        next tick rather than driving at a memory — so expiry is what turns a dead camera back
        into a stop instead of twenty seconds of blind walking.
        """
        if self._frame is None or self._frame_at is None:
            return None
        age = time.monotonic() - self._frame_at
        return None if age > camera_stale_after_s(self.camera_fps) else age

    def camera_health(self) -> dict[str, Any]:
        """What the camera is doing, for `doctor` and for the run's own state."""
        if not self.camera_url:
            return {"configured": False}
        if self._webrtc is not None:
            return self._webrtc.health()
        age = None if self._frame_at is None else round(time.monotonic() - self._frame_at, 2)
        return {
            "configured": True,
            "url": self.camera_url,
            "ok": self._frame is not None,
            "age_s": age,
            "size": list(self._frame.size) if self._frame is not None else None,
            "error": self._frame_error,
        }

    def state_age_s(self) -> float | None:
        """Seconds since the last `robot.state` frame, or None if none has ever arrived."""
        if self._last_state_at is None:
            return None
        return time.monotonic() - self._last_state_at

    async def get_state(self) -> DuckState:
        health = self._last_health
        if health is None:
            with contextlib.suppress(TransportError):
                health = await self.request(up.ROBOT_HEALTH.name)
                self._last_health = health if isinstance(health, dict) else None

        # A duck nobody is watching reads as unknown, never as standing. `fallen` is a plain
        # bool on the wire and in DuckState, so silence and "upright" are the same value there
        # — the difference has to live in `posture` and in `fall_detection`, or a stream that
        # never started looks exactly like a robot that is fine. Same invariant the Open Duck
        # bridge states in `bridge.py`.
        age = self.state_age_s()
        arriving = age is not None and age <= state_stale_after_s(self.state_hz)
        state = (self._last_state or {}) if arriving else {}
        safety = state.get("safety")
        policy = str(state.get("policy") or "unknown")
        fallen = bool((safety or {}).get("fallen"))
        # Frames arriving is not the same as frames that say anything about falling. Upstream
        # declares `safety` on every state frame, but a frame without one would otherwise read
        # as "not fallen" on the strength of a key that was never there.
        fresh = arriving and isinstance(safety, dict) and "fallen" in safety
        posture: Posture
        if not fresh:
            posture = "unknown"
        elif fallen:
            posture = "fallen"
        elif "sit" in policy:  # up.POSTURE_FROM_POLICY — an assumption
            posture = "sitting"
        elif policy == "unknown":
            posture = "unknown"
        else:
            posture = "standing"
        battery = ((health or {}).get("battery") or {}).get("percent")
        return DuckState(
            t=self.now(),
            policy=policy,
            posture=posture,
            fallen=fallen,
            battery_percent=float(battery) if battery is not None else None,
            extras={
                "health": health,
                "move": state.get("move"),
                "loop": state.get("loop"),
                "odom": state.get("odom"),
                # False means quackd cannot see falls at all right now, so `fallen: false` above
                # is silence rather than a verdict. The preconditions read this.
                "fall_detection": fresh,
                "state_age_s": round(age, 3) if age is not None else None,
                "subscribed": self.subscribed,
                "camera": self.camera_health(),
                "stop_error": self.stop_error,
                "assumptions": [up.POSTURE_FROM_POLICY.name],
            },
        )

    async def send_intent(self, intent: Intent) -> Ack:
        p = intent.params
        try:
            match intent.kind:
                case "move":
                    await self.notify(
                        up.ROBOT_MOVE.name,
                        {"vx": p.get("vx", 0.0), "vy": p.get("vy", 0.0), "vyaw": p.get("wz", 0.0)},
                    )
                    return Ack()
                case "stop":
                    await self.request(up.ROBOT_STOP.name)
                    return Ack()
                case "do":
                    res = await self.request(up.ROBOT_DO.name, {"skill": p.get("skill")})
                    return _ack(res)
                case "look":
                    res = await self.request(
                        up.ROBOT_LOOK.name,
                        {
                            "x": p.get("x", 1.0),
                            "y": p.get("y", 0.0),
                            "z": p.get("z", 0.0),
                            "neck_pitch": 0.0,
                        },
                    )
                    clamped = isinstance(res, dict) and res.get("clamped")
                    return Ack(accepted=True, reason="clamped" if clamped else None)
                case "sound":
                    asked = p.get("tag", "chirp")
                    tag = asked if asked in up.SOUND_TAG_LIST else "chirp"
                    res = await self.request(up.ROBOT_SOUND.name, {"tag": tag})
                    ack = _ack(res)
                    if tag != asked and ack.accepted:
                        # Upstream has seven tags and no TTS. Substituting is right; doing it
                        # without saying so meant a verb reporting a sound nobody asked for.
                        return Ack(accepted=True, reason=f"{asked!r} is not a duck sound; {tag}")
                    return ack
                case "enable":
                    res = await self.request(up.ROBOT_ENABLE.name, {"on": bool(p.get("on", True))})
                    return _ack(res)
                case "pose":
                    await self.notify(
                        up.ROBOT_POSE.name, {k: p[k] for k in ("z", "roll", "pitch") if k in p}
                    )
                    return Ack()
                case _:
                    return Ack(accepted=False, reason=f"no upstream mapping for {intent.kind}")
        except TransportError as e:
            return Ack(accepted=False, reason=str(e))

    async def subscribe(self, topic: str) -> AsyncIterator[dict[str, Any]]:  # type: ignore[override]
        if topic in ("state", up.ROBOT_STATE.name) and self.subscribed is None:
            # connect() normally does this; only a transport used without it needs asking twice.
            self.subscribed = await self.request(up.ROBOT_SUBSCRIBE.name, {"hz": self.state_hz})
        while True:
            msg = await self._notifications.get()
            yield {"topic": msg.get("method"), **(msg.get("params") or {})}

    async def heartbeat(self) -> None:
        try:
            health = await self.request(up.ROBOT_HEALTH.name)
        except TransportError as e:
            raise HeartbeatError(f"robot.health failed: {e}") from e
        if isinstance(health, dict):
            self._last_health = health
            if health.get("healthy") is False:
                raise HeartbeatError(f"robotd unhealthy: {health.get('reason') or 'no reason'}")

    async def stop(self) -> None:
        """Zero the velocity. Never raises — but never pretends it landed, either.

        The protocol asks that this be safe to call from anywhere at any time, including from
        the exception paths that run *because* the socket died, so it cannot raise. It used to
        swallow the failure silently, which meant the heartbeat could log "stopping the duck"
        while delivering nothing. What actually stops a Microduck in that situation is robotd's
        own deadman: `robot.move` notifications stop arriving and the velocity goes to zero
        (`up.DEADMAN`). That is a real protection and this is not a substitute for it.
        """
        try:
            await self.request(up.ROBOT_STOP.name)
            self.stop_error = None
        except TransportError as e:
            self.stop_error = str(e)
            log.warning(
                "stop could not be delivered to %s (%s). The robot's own deadman zeroes "
                "velocity when robot.move stops arriving, which is what stops it now.",
                self.address,
                e,
            )

    def now(self) -> float:
        return time.monotonic() - self._t0

    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(seconds)


def _ack(result: Any) -> Ack:
    if isinstance(result, dict) and "accepted" in result:
        return Ack(accepted=bool(result["accepted"]), reason=result.get("reason"))
    return Ack()
