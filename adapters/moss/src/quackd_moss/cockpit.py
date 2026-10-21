"""EXPERIMENTAL: a real MOSS rover, over its own cockpit and quackd's bridge.

Two HTTP servers on the rover, both reached through one ssh tunnel:

- **MOSS's cockpit** (`tools/moss_cockpit.py`, port 8904, no authentication) for the stack, the
  pose, the battery, goals for the planner, the explorer, the soft stop and its idle keepalive.
  Every name it spells is in `upstream_api.py`.
- **quackd's bridge** (`bridge/moss/quackd_moss_bridge.py`, port 9876, a token) for what the
  cockpit has not got: a twist with a deadman, frames that say when they were taken, the
  maker's arm cycle and its cancel, and the litter mission's own stop. Its protocol is quackd's
  at both ends (`PROTOCOL`), so it is pinned by tests that read both files, not by upstream refs.

Four properties of this rover decide what this file does, and each is read at the pin:

- **Every message on the pad's channel cancels navigation**, a zero included
  (`upstream_api.TELEOP_CANCELS_NAVIGATION`). So quackd never streams a twist from here: it
  posts each one to the bridge, which re-sends it at 10 Hz while it is fresh and falls silent
  after one zero.
- **The litter mission listens to no stop the cockpit sends** (`upstream_api.MISSION_INPUTS`).
  Its own RPC ends it, and only the bridge can call that, or the end of the whole stack.
- **MOSS's hard stop drops the arm** (`upstream_api.ARM_TORQUE_OFF`), so quackd never
  posts `/cmd/stack`, and no method here can.
- **A dropped link leaves a goal running.** MOSS finishes a goal the planner has, so the bridge
  halts what this session started three missed keepalives after it goes quiet, a dead process,
  a dead link and a wedged loop alike, since the keepalives go from the loop's own heartbeat,
  and without the bridge a goal is kept short (`verbs.DRIVE_TO_MAX_M`).

`stop()` is the bridge's `/stop` and then the cockpit's `/cmd/stop`, every time, each failure
reported and neither skipping the other. `close()` halts what this session started and nothing
else: the whole stop once `collect_litter` ran, and otherwise its own arm cycle by its id, a
twist of its own the bridge may still be repeating, and a goal or an exploration it started. A
session that started nothing sends no stop, so a `quackd doctor` leaves the owner's mission and
goal running.

Never run against a rover by us.
"""

from __future__ import annotations

import asyncio
import contextlib
import http.client
import io
import json
import math
import secrets
import threading
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

from PIL import Image, UnidentifiedImageError

from quackd.transport.base import (
    Ack,
    CameraFrame,
    DuckState,
    HeartbeatError,
    Intent,
    TransportError,
)
from quackd_moss import upstream_api as up
from quackd_moss.verbs import (
    BATTERY_FLOOR_V,
    FIRMWARE_LIMIT_S,
    MIN_STALE_S,
    MISSION_STOPPED,
    SKILL_ARM_CYCLE,
    SKILL_COLLECT,
    SKILL_EXPLORE,
    STOP_TIMEOUT_S,
    TELEMETRY_LIMIT_S,
    arm_note,
    stack_refusal,
    stale_after_s,
)

STATUS = "EXPERIMENTAL: MOSS's cockpit and quackd's own bridge, never run against a rover"

# ── the cockpit's names, from upstream_api and nowhere else ─────────────────────────────

DEFAULT_HOST = "127.0.0.1"
COCKPIT_PORT = int(up.COCKPIT_PORT.name)
CMD_HEADER_NAME, CMD_HEADER_VALUE = up.CMD_HEADER.name.split(": ", 1)
STATE_PATH = up.STATE_JSON.name.split(" ", 1)[1]
MJPEG_PATH = up.MJPEG_ROUTE.name.split(" ", 1)[1]
CMD_PREFIX = "/cmd/"
NEVER_POSTED = up.CMD_STACK.name.rsplit("/", 1)[1]
"""`stack`: the one cockpit command quackd refuses to send (`up.CMD_STACK`)."""
MJPEG_BOUNDARY = up.MJPEG_FRAMING.name.rsplit("=", 1)[1]
MJPEG_PERIOD_S = float(up.MJPEG_PERIOD_S.name)
CAMERA_PERIOD_S = 1.0 / float(up.CAMERA_FPS.name)
NOSE, WRIST = (name.strip().strip('"') for name in up.CAMERAS.name.split(","))
IDLE_STOP_MIN_S = float(up.IDLE_STOP_MIN_S.name)
KEEPALIVES_PER_LIMIT = 3
"""How many cockpit keepalives quackd sends inside one idle limit: the count restarts at each, so
two can be lost and the idle stop still never fires on a rover quackd is connected to."""

# ── quackd's bridge: its protocol, shared with bridge/moss/quackd_moss_bridge.py ────────

PROTOCOL = "quackd-moss-bridge"
PROTOCOL_VERSION = 1
"""Still 1 through every change so far, since nothing of the protocol has been released and no
bridge or client of another shape is out there to refuse. The first release fixes it, and a
change after that raises it."""
BRIDGE_PORT = 9876
"""9875 is the LeRobot policy server's, 9874 the Jetson daemon's, so the rover's bridge is next."""
TOKEN_ENV = "QUACKD_MOSS_TOKEN"
TOKEN_HEADER = "X-Quackd-Token"
"""In a header and never in a URL, because URLs are printed and land in transcripts."""
HELLO_PATH = "/hello"
TWIST_PATH = "/twist"
STOP_PATH = "/stop"
FRAME_PATH = "/frame"
ARM_PATH = "/arm"
ARM_CYCLE_PATH = "/arm/cycle"
ARM_CANCEL_PATH = "/arm/cancel"
MISSION_PATH = "/mission"
NAV_PATH = "/nav"
KEEPALIVE_PATH = "/session/keepalive"
FRAME_STAMP_HEADER = "X-Frame-Stamp"
FRAME_AFTER_HEADER = "X-Frame-After-Request"
"""1 when the frame's own stamp, on the rover's clock, is after the request reached the bridge
and the stamp passed the bridge's check against its own clock. Only such a frame carries
`frame_taken_at`."""
SESSION_MISSES = 3
"""Missed keepalives after which the bridge halts what this session started."""
MAX_KEEPALIVE_S = 1.0
"""The longest period the bridge takes from a client, so the longest a session's window is."""
STARTED = ("goal", "explore", "litter")
NAV_KEYS = ("exploring", "explore_done", "goal", "goal_reached")
"""What the bridge's nav says of the base stack, in the cockpit's own `nav` words."""
"""What quackd may have started through the cockpit, as the keepalive tells the bridge."""

HEARTBEAT_S = 0.5
"""How often the session's keepalives go: once in every heartbeat of the loop's own, whose
default this is (`quackd.safety.Heartbeat`). Each declares MAX_KEEPALIVE_S, the bridge's cap,
so the window is three of those, and one keepalive late by a couple of periods costs nothing.
A loop that does not run sends none: a dead process, a dead link and a wedged loop all end the
session."""
CALL_TIMEOUT_S = 3.0
FRAME_WAIT_S = stale_after_s(CAMERA_PERIOD_S)
"""How long the bridge may hold a frame request for a frame taken after it, and so how long
quackd waits for one, beyond its own call timeout."""
FIRST_FRAME_S = 2 * float(up.FIRST_FRAME_WAIT_S.name)
"""How long connect() waits for the cockpit's first nose frame when there is no bridge: the
cockpit's own wait for a camera, twice."""


class LinkDown(TransportError):
    """A server on the rover did not answer, or answered something that was not its protocol."""


class Answered(LinkDown):
    """A server on the rover answered, with an error or with something that is not its
    protocol. It is up, so the fix is in what it said and not in reaching it."""


class TokenRefused(TransportError):
    """The bridge answered, and refused the token it was sent."""


# ── HTTP, standard library only ─────────────────────────────────────────────────────────


class _Link:
    """One HTTP endpoint on the rover, a persistent connection behind a lock.

    A request that is safe to repeat is tried once more on a fresh connection when a kept one
    turns out dead, which is the common way a keep-alive connection fails. One that is not safe
    to repeat (a goal, an exploration, an arm cycle) always gets a fresh connection, so it is
    never sent twice."""

    def __init__(self, host: str, port: int, *, timeout_s: float, headers: dict[str, str]) -> None:
        self.host = host
        self.port = port
        self.timeout_s = timeout_s
        self.headers = dict(headers)
        self._conn: http.client.HTTPConnection | None = None
        self._lock = threading.Lock()

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}"

    def _drop(self) -> None:
        if self._conn is not None:
            with contextlib.suppress(Exception):
                self._conn.close()
        self._conn = None

    def close(self) -> None:
        with self._lock:
            self._drop()

    def request(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
        *,
        repeatable: bool = True,
        timeout_s: float | None = None,
    ) -> tuple[int, dict[str, str], bytes]:
        payload = None if body is None else json.dumps(body).encode("utf-8")
        headers = dict(self.headers)
        if payload is not None:
            headers["Content-Type"] = "application/json"
        wanted = timeout_s or self.timeout_s
        with self._lock:
            if not repeatable:
                self._drop()
            for attempt in (1, 2):
                kept = self._conn is not None
                if self._conn is None:
                    self._conn = http.client.HTTPConnection(
                        self.host, self.port, timeout=self.timeout_s
                    )
                conn = self._conn
                conn.timeout = wanted
                try:
                    if conn.sock is not None:
                        conn.sock.settimeout(wanted)
                    conn.request(method, path, body=payload, headers=headers)
                    resp = conn.getresponse()
                    data = resp.read()
                except (OSError, http.client.HTTPException) as e:
                    self._drop()
                    if attempt == 1 and kept and repeatable:
                        continue
                    raise LinkDown(f"{self.url} did not answer {method} {path}: {e}") from e
                got = {k.lower(): v for k, v in resp.getheaders()}
                if got.get("connection", "").lower() == "close" or not repeatable:
                    self._drop()
                else:
                    conn.timeout = self.timeout_s
                    if conn.sock is not None:
                        conn.sock.settimeout(self.timeout_s)
                return resp.status, got, data
        raise LinkDown(f"{self.url} did not answer {method} {path}")


def _json(status: int, data: bytes, where: str) -> dict[str, Any]:
    try:
        doc = json.loads(data.decode("utf-8")) if data else {}
    except (UnicodeDecodeError, ValueError) as e:
        raise Answered(f"{where} answered {status} with something that is not JSON") from e
    if not isinstance(doc, dict):
        raise Answered(f"{where} answered {status} with JSON that is not an object")
    return doc


class CockpitClient:
    """MOSS's own cockpit. Every command carries the header the cockpit asks for, and there is
    deliberately no way to post `/cmd/stack` through it."""

    def __init__(self, host: str = DEFAULT_HOST, port: int = COCKPIT_PORT) -> None:
        headers = {CMD_HEADER_NAME: CMD_HEADER_VALUE}
        self.link = _Link(host, port, timeout_s=CALL_TIMEOUT_S, headers=headers)
        self.slow = _Link(host, port, timeout_s=CALL_TIMEOUT_S, headers=headers)
        self.beat = _Link(host, port, timeout_s=CALL_TIMEOUT_S, headers=headers)
        """The idle keepalive's own, so it never waits behind a state read or a command."""

    @property
    def url(self) -> str:
        return self.link.url

    def state(self) -> dict[str, Any]:
        status, _, data = self.link.request("GET", STATE_PATH)
        doc = _json(status, data, f"{self.url}{STATE_PATH}")
        if status != 200:
            raise Answered(f"{self.url}{STATE_PATH} answered {status}: {doc.get('error')}")
        return doc

    def command(self, name: str, body: dict[str, Any] | None = None) -> tuple[bool, dict[str, Any]]:
        """(the cockpit's ok, its answer). A refusal is data: 403, 409 and 503 come back as
        False with the cockpit's own words, and only a cockpit that did not answer raises."""
        if name == NEVER_POSTED:
            raise ValueError(
                "quackd never posts /cmd/stack: its stop ends every dimOS run and drops the "
                f"arm's torque. Stop the stack on the rover with {up.FLY_STOP.name}"
            )
        path = f"{CMD_PREFIX}{name}"
        repeatable = name in ("stop", "keepalive")
        link = self.link if repeatable else self.slow
        status, _, data = link.request(
            "POST",
            path,
            body or {},
            repeatable=repeatable,
        )
        doc = _json(status, data, f"{self.url}{path}")
        return status == 200 and bool(doc.get("ok")), doc

    def goal(self, x: float, y: float) -> tuple[bool, dict[str, Any]]:
        return self.command("goal", {"x": x, "y": y})

    def explore(self, action: str) -> tuple[bool, dict[str, Any]]:
        return self.command("explore", {"action": action})

    def stop(self) -> tuple[bool, dict[str, Any]]:
        return self.command("stop")

    def keepalive(self) -> tuple[bool, dict[str, Any]]:
        path = f"{CMD_PREFIX}keepalive"
        status, _, data = self.beat.request("POST", path, {})
        doc = _json(status, data, f"{self.url}{path}")
        return status == 200 and bool(doc.get("ok")), doc

    def close(self) -> None:
        for link in (self.link, self.slow, self.beat):
            link.close()


class BridgeClient:
    """quackd's bridge on the rover. Four links, so a frame that waits for the camera never
    holds a twist up behind it, and nothing ever holds up the session's keepalive."""

    def __init__(self, host: str = DEFAULT_HOST, port: int = BRIDGE_PORT, token: str | None = None):
        headers = {TOKEN_HEADER: token} if token else {}
        self.control = _Link(host, port, timeout_s=CALL_TIMEOUT_S, headers=headers)
        self.frames = _Link(host, port, timeout_s=CALL_TIMEOUT_S + FRAME_WAIT_S, headers=headers)
        self.slow = _Link(host, port, timeout_s=CALL_TIMEOUT_S * 2, headers=headers)
        self.beat = _Link(host, port, timeout_s=CALL_TIMEOUT_S, headers=headers)
        self.token = token

    @property
    def url(self) -> str:
        return self.control.url

    def _doc(self, link: _Link, method: str, path: str, body: Any = None, **kw: Any) -> Any:
        status, _, data = link.request(method, path, body, **kw)
        doc = _json(status, data, f"{self.url}{path}")
        if status == 401:
            raise TokenRefused(
                f"the bridge at {self.url} refused the token: pass --token or set {TOKEN_ENV} to "
                "the one in the bridge's token file"
            )
        return status, doc

    def hello(self) -> dict[str, Any]:
        _, doc = self._doc(self.slow, "GET", HELLO_PATH)
        if doc.get("protocol") != PROTOCOL or doc.get("protocol_version") != PROTOCOL_VERSION:
            raise TransportError(
                f"the server at {self.url} speaks {doc.get('protocol')!r} version "
                f"{doc.get('protocol_version')!r}, and this quackd speaks {PROTOCOL} version "
                f"{PROTOCOL_VERSION}: copy bridge/moss/quackd_moss_bridge.py from this quackd "
                "to the rover and restart it"
            )
        return dict(doc)

    def twist(self, vx: float, wz: float) -> tuple[int, dict[str, Any]]:
        status, doc = self._doc(self.control, "POST", TWIST_PATH, {"vx": vx, "wz": wz})
        return status, doc

    def stop(self, run: str | None = None, session: str | None = None) -> dict[str, Any]:
        """The bridge's stop, for the base run quackd connected to: a run started since is not
        touched, and of the arm only a cycle `session` started is cancelled under it. No run
        stops whatever runs."""
        body: dict[str, Any] = {}
        if run:
            body["run"] = run
        if session:
            body["session"] = session
        _, doc = self._doc(self.slow, "POST", STOP_PATH, body, timeout_s=STOP_TIMEOUT_S)
        return dict(doc)

    def keepalive(
        self,
        session: str,
        keepalive_s: float,
        started: str | None,
        *,
        end: bool = False,
        run: str | None = None,
    ) -> dict[str, Any]:
        """One keepalive of `session`, on its own link, for the base run it connected to.
        Raises when the bridge did not take it."""
        body = {
            "session": session,
            "keepalive_s": keepalive_s,
            "started": started,
            "end": end,
            "run": run,
        }
        status, doc = self._doc(self.beat, "POST", KEEPALIVE_PATH, body)
        if status != 200:
            raise Answered(
                f"the bridge at {self.url} refused the keepalive: {doc.get('reason') or status}"
            )
        return dict(doc)

    def arm(self) -> dict[str, Any]:
        _, doc = self._doc(self.slow, "GET", ARM_PATH)
        return dict(doc)

    def arm_cycle(
        self, pick_pose: str, from_phase: str | None, session: str | None = None
    ) -> tuple[int, dict[str, Any]]:
        body = {"pick_pose": pick_pose, "from_phase": from_phase, "session": session}
        status, doc = self._doc(self.slow, "POST", ARM_CYCLE_PATH, body, repeatable=False)
        return status, doc

    def arm_cancel(self, job_id: str) -> dict[str, Any]:
        """The cancel for one cycle of quackd's, the one `job_id` names, which may take as long
        as the cancel inside a stop."""
        body = {"id": job_id}
        _, doc = self._doc(self.slow, "POST", ARM_CANCEL_PATH, body, timeout_s=STOP_TIMEOUT_S)
        return dict(doc)

    def mission(self) -> dict[str, Any]:
        _, doc = self._doc(self.slow, "GET", MISSION_PATH)
        return dict(doc)

    def nav(self) -> dict[str, Any]:
        """The base stack's own navigation, read off the bus by the bridge."""
        _, doc = self._doc(self.slow, "GET", NAV_PATH)
        return dict(doc)

    def frame(self, cam: str) -> tuple[bytes, bool, str | None]:
        """(the JPEG, whether it was taken after the request, the bridge's reason if none)."""
        status, headers, data = self.frames.request("GET", f"{FRAME_PATH}?cam={cam}")
        if status == 401:
            raise TokenRefused(f"the bridge at {self.url} refused the token")
        if status != 200 or not headers.get("content-type", "").startswith("image/jpeg"):
            doc = _json(status, data, f"{self.url}{FRAME_PATH}")
            raise LinkDown(f"no {cam} frame: {doc.get('reason') or status}")
        return data, headers.get(FRAME_AFTER_HEADER.lower()) == "1", None

    def close(self) -> None:
        for link in (self.control, self.frames, self.slow, self.beat):
            link.close()


# ── the cockpit's MJPEG stream, for a rover with no bridge ──────────────────────────────


class MjpegReader:
    """One persistent reader of the cockpit's nose stream, keeping the newest JPEG.

    One reader takes one of the cockpit's four viewer slots and holds it, so the owner's page
    keeps room. A frame's arrival is all a stream says of when it was taken, an upper bound and
    not a lower one, so nothing here offers `frame_taken_at`."""

    def __init__(self, host: str, port: int, cam: str = NOSE, *, timeout_s: float = 5.0) -> None:
        self.host, self.port, self.cam = host, port, cam
        self.timeout_s = timeout_s
        self.error: str | None = None
        self._jpeg: bytes | None = None
        self._at: float | None = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self.first = threading.Event()
        """Set once the first frame has arrived."""
        self._conn: http.client.HTTPConnection | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name="moss-mjpeg", daemon=True)
            self._thread.start()

    def latest(self) -> tuple[bytes, float] | None:
        with self._lock:
            return None if self._jpeg is None or self._at is None else (self._jpeg, self._at)

    def stop(self) -> None:
        self._stop.set()
        conn = self._conn
        if conn is not None:
            with contextlib.suppress(Exception):
                conn.close()
        if self._thread is not None:
            self._thread.join(timeout=self.timeout_s + 1.0)

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self._stream()
            except Exception as e:  # a hiccup must never end the run
                self.error = f"the cockpit's {self.cam} stream: {type(e).__name__}: {e}"
            self._stop.wait(1.0)

    def _stream(self) -> None:
        conn = http.client.HTTPConnection(self.host, self.port, timeout=self.timeout_s)
        self._conn = conn
        try:
            conn.request("GET", f"{MJPEG_PATH}?cam={self.cam}")
            resp = conn.getresponse()
            if resp.status != 200:
                body = resp.read()
                why = _json(resp.status, body, "the cockpit's camera").get("error")
                self.error = f"the cockpit's {self.cam} stream answered {resp.status}: {why}"
                return
            kind = resp.getheader("Content-Type", "")
            if f"boundary={MJPEG_BOUNDARY}" not in kind:
                self.error = f"the cockpit's {self.cam} stream is {kind!r}, not its MJPEG"
                return
            marker = f"--{MJPEG_BOUNDARY}".encode("ascii")
            while not self._stop.is_set():
                line = resp.readline()
                if not line:
                    return
                if line.strip() != marker:
                    continue
                length = None
                while True:
                    header = resp.readline()
                    if not header or not header.strip():
                        break
                    name, _, value = header.decode("latin-1").partition(":")
                    if name.strip().lower() == "content-length":
                        length = int(value.strip())
                if length is None or length <= 0:
                    continue
                jpeg = resp.read(length)
                if len(jpeg) < length:
                    return
                with self._lock:
                    self._jpeg, self._at = jpeg, time.monotonic()
                self.error = None
                self.first.set()
        finally:
            self._conn = None
            with contextlib.suppress(Exception):
                conn.close()


def _decode(jpeg: bytes) -> Image.Image | None:
    try:
        img = Image.open(io.BytesIO(jpeg))
        img.load()
    except (OSError, ValueError, UnidentifiedImageError):
        return None
    return img.convert("RGB")


def _number(value: Any) -> float | None:
    if isinstance(value, int | float) and not isinstance(value, bool) and math.isfinite(value):
        return float(value)
    return None


# ── the transport ───────────────────────────────────────────────────────────────────────


class MossCockpit:
    """The real rover. Built with no address it still builds, pointed at 127.0.0.1:8904, the
    near end of the ssh tunnel the docs give. Nothing is opened until connect()."""

    name = "cockpit"
    mobility = "wheeled"

    def __init__(
        self,
        *,
        host: str = DEFAULT_HOST,
        cockpit_port: int = COCKPIT_PORT,
        bridge_port: int = BRIDGE_PORT,
        token: str | None = None,
        cockpit: CockpitClient | None = None,
        bridge: BridgeClient | None = None,
        timeout_s: float = CALL_TIMEOUT_S,
        heartbeat_s: float = HEARTBEAT_S,
    ) -> None:
        self.host = host
        self.cockpit = cockpit or CockpitClient(host, cockpit_port)
        self.bridge = bridge or BridgeClient(host, bridge_port, token)
        self.timeout_s = timeout_s
        self.heartbeat_floor_s = heartbeat_s
        """The loop's heartbeat period, which sends one keepalive each beat. Each keepalive
        declares the bridge's own cap instead, a constant, never a measurement, so the window
        is the same however slow a round trip is."""
        self.session_id = secrets.token_hex(8)
        """Drawn here, so this session's keepalives, end and halts are its own on the bridge."""
        self.connected = False
        self.stack: str | None = None
        self.run_id: str | None = None
        self.bridge_up = False
        self.bridge_error: str | None = None
        self.hello: dict[str, Any] | None = None
        self.arm_up = False
        """The arm verbs can run: the coordinator answers and the maker's cycle is there."""
        self.arm_seen = False
        """The coordinator answers, so the arm is watched even with no cycle to play."""
        self.mission_up = False
        self.camera_available = False
        self.camera_keys: list[str] = []
        self.camera_error: str | None = None
        self.fov_deg: float | None = None
        self.manifest_extras: dict[str, Any] = {}
        self.stop_error: str | None = None
        self.close_note: str | None = None
        self.mission_stop: str | None = None
        """What the bridge said of the litter mission at the last stop: stopped, unconfirmed."""
        self.arm_job_id: str | None = None
        """The last arm cycle this session started, which close() cancels by this id."""
        self.started: str | None = None
        """What quackd has begun through the cockpit and not yet stopped, for the bridge."""
        self.litter_started = False
        """`collect_litter` started in this session, so close() stops the mission."""
        self.stop_sent = False
        """A stop ran in this session, so close() says what it did to the mission."""
        self._clean_stop = False
        """The last stop reached both surfaces, and nothing has been started since it, so a
        close has nothing left to stop and sends nothing."""
        self.run_moved: str | None = None
        """The base run a stop or a halt of this session found running instead of the one it
        connected to, which it then left alone."""
        self.twist_deadman_s = float(up.FIRMWARE_DEADMAN_S.name)
        """How long the bridge repeats a twist with no new one, read from its hello."""
        self._twist_at: float | None = None
        """When the bridge took this session's last twist that was not a zero."""
        self.post_sleep: Callable[[], None] | None = None
        self._mjpeg: MjpegReader | None = None
        self._frames_task: asyncio.Task[None] | None = None
        self._frames_on = False
        """Whether the frame loop goes on: close clears it as well as cancelling the loop."""
        self._frame: Image.Image | None = None
        self._frame_asked: float | None = None
        self._frame_after = False
        self._fetch_s = 0.0
        self._beat_lock = threading.Lock()
        self._session_open = False
        self._session_doc: dict[str, Any] = {}
        """The bridge's answer to this session's last keepalive, with its stack, for the
        heartbeat's check that the stack quackd connected to still runs."""
        self._session_at: float | None = None
        self._session_error: str | None = None
        self._idle: dict[str, Any] = {}
        """The cockpit's idle rule as its state last said it, for the idle keepalive."""
        self._last_cockpit_keepalive = 0.0

    # ── calls, off the loop and on a deadline ───────────────────────────────────────

    async def _call(
        self, fn: Callable[..., Any], *args: Any, timeout_s: float | None = None
    ) -> Any:
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(fn, *args), timeout=timeout_s or self.timeout_s * 2
            )
        except TimeoutError as e:
            raise LinkDown(
                f"the rover did not answer in {timeout_s or self.timeout_s * 2:g} s"
            ) from e

    # ── connect, and the refusals that name their fix ───────────────────────────────

    async def connect(self) -> None:
        try:
            state = await self._call(self.cockpit.state)
        except Answered as e:
            raise TransportError(
                f"moss: the cockpit at {self.cockpit.url} answered, but not with the rover's "
                f"state: {e}. quackd reads the rover through {STATE_PATH} and cannot connect "
                "until it does"
            ) from e
        except LinkDown as e:
            raise TransportError(
                f"moss: nothing answers at {self.cockpit.url}{STATE_PATH} ({e}). Start the cockpit "
                f"on the rover ({up.FLY_COCKPIT}) and reach it through the tunnel: ssh -L "
                f"{COCKPIT_PORT}:127.0.0.1:{COCKPIT_PORT} -L {BRIDGE_PORT}:127.0.0.1:{BRIDGE_PORT} "
                "<rover>"
            ) from e
        if state.get("mode") == "demo":
            raise TransportError(
                f"moss: the cockpit at {self.cockpit.url} is in --demo mode, a synthetic rover "
                "with no bus behind it: start it without --demo on the rover"
            )
        await self._bridge_hello()
        hello = self.hello or {}
        # the base stack: the bridge's reading where there is one, since it keeps the base and the
        # arm's run apart, and the cockpit's otherwise, which is the newest run of any kind
        hello_stack = hello.get("stack") if self.bridge_up else None
        stack = hello_stack if isinstance(hello_stack, dict) else (state.get("stack") or {})
        blueprint = str(stack.get("blueprint") or "")
        mission = hello.get("mission") or {}
        refusal = stack_refusal(
            running=bool(stack.get("running")),
            blueprint=blueprint,
            bus=str((state.get("bus") or {}).get("transport") or ""),
            bridge=self.bridge_up,
            mission=bool(mission.get("answering")),
            mission_error=mission.get("error"),
            bridge_error=self.bridge_error,
        )
        if refusal is not None:
            raise TransportError(f"moss: {refusal}")
        run = stack.get("run_id")
        self.stack, self.run_id = blueprint, (str(run) if run is not None else None)
        # the arm verbs need the coordinator answering and the maker's cycle there to play
        arm = hello.get("arm") or {}
        self.arm_seen = self.bridge_up and bool(arm.get("answering"))
        self.arm_up = self.arm_seen and bool(arm.get("cycle"))
        self.mission_up = self.bridge_up and bool((hello.get("mission") or {}).get("answering"))
        deadman = _number((hello.get("shaping") or {}).get("deadman_s"))
        if deadman is not None:
            self.twist_deadman_s = deadman
        self._idle = state.get("idle") or {}
        if self.bridge_up:
            # the session is open before anything can be started in it
            try:
                await self._call(self._beat)
            except Exception as e:
                raise TransportError(
                    f"moss: quackd's bridge at {self.bridge.url} answered its hello and refused "
                    f"this session's first keepalive ({e}), so it would keep no deadman: copy "
                    "bridge/moss/quackd_moss_bridge.py from this quackd to the rover and restart it"
                ) from e
        await self._cameras()
        base = state.get("base") or {}
        self.manifest_extras = {
            "cockpit": self.cockpit.url,
            "bridge": (
                {"url": self.bridge.url, "version": hello.get("bridge_version")}
                if self.bridge_up
                else None
            ),
            "bridge_error": None if self.bridge_up else self.bridge_error,
            "track_m": hello.get("track_m"),
            "stop_ramp_s": hello.get("stop_ramp_s"),
            "fw": base.get("fw"),
            "driver": base.get("driver"),
        }
        self.connected = True

    async def _bridge_hello(self) -> None:
        """A bridge that is not there is a rover without one. A bridge that answers with the
        wrong protocol or refuses the token is refused, by name, since running on without it
        would quietly drop the deadman it was installed for."""
        try:
            self.hello = await self._call(self.bridge.hello)
        except TokenRefused as e:
            raise TransportError(f"moss: {e}") from e
        except LinkDown as e:
            self.hello, self.bridge_up, self.bridge_error = None, False, str(e)
            return
        publisher = (self.hello or {}).get("publisher") or {}
        if not publisher.get("loaded"):
            # it answers, and it cannot publish: none of what it was installed for, its
            # deadman included, would reach the rover, so it counts as no bridge at all
            self.bridge_up = False
            self.bridge_error = (
                f"quackd's bridge at {self.bridge.url} answers but cannot publish on the "
                f"rover's bus: {publisher.get('error') or 'no reason given'}"
            )
            return
        self.bridge_up, self.bridge_error = True, None

    async def _cameras(self) -> None:
        if self.bridge_up:
            heard = (self.hello or {}).get("cameras") or {}
            self.camera_keys = [cam for cam in (NOSE, WRIST) if (heard.get(cam) or {}).get("heard")]
            self.camera_available = NOSE in self.camera_keys
            self.fov_deg = _number((self.hello or {}).get("fov_deg"))
            if not self.camera_available:
                self.camera_error = "the bridge has heard no nose camera on the bus"
                self.camera_keys = []
                return
            self._frames_on = True
            self._frames_task = asyncio.create_task(self._frame_loop(), name="moss-frames")
            return
        reader = MjpegReader(self.host, self.cockpit.link.port)
        reader.start()
        self._mjpeg = reader
        await asyncio.to_thread(reader.first.wait, FIRST_FRAME_S)
        self.camera_available = reader.latest() is not None
        self.camera_keys = [NOSE] if self.camera_available else []
        if not self.camera_available:
            self.camera_error = reader.error or "the cockpit's nose stream gave no frame"

    # ── frames ──────────────────────────────────────────────────────────────────────

    async def _frame_loop(self) -> None:
        """The newest nose frame on a timer, each stamped with the moment its request went out,
        which a frame the bridge says was taken after the request is no older than. It ends
        when close clears its flag, or at a cancel from anywhere, a lost one among them."""
        me = asyncio.current_task()
        while self._frames_on:
            asked = time.monotonic()
            try:
                jpeg, after, _ = await self._call(
                    self.bridge.frame, NOSE, timeout_s=CALL_TIMEOUT_S + FRAME_WAIT_S
                )
            except Exception as e:  # a camera hiccup must not end a run
                self.camera_error = f"the bridge's nose frame: {e}"
            else:
                img = _decode(jpeg)
                if img is not None:
                    self._frame, self._frame_asked, self._frame_after = img, asked, after
                    self.camera_error = None
                    # only a fetch that brought a frame says how long one takes: a failed one
                    # says how long the bridge waited for a camera that may have stopped
                    self._fetch_s = time.monotonic() - asked
            if me is not None and me.cancelling():
                # Python 3.11's wait_for loses a cancel that arrives as its call returns, and the
                # cancel is still asked for: a loop shutting down cancels every task, this one
                # without close, and would wait on it for good
                raise asyncio.CancelledError
            took = time.monotonic() - asked
            await asyncio.sleep(max(0.0, CAMERA_PERIOD_S - took))

    def _frame_age_s(self) -> float | None:
        if self._mjpeg is not None:
            got = self._mjpeg.latest()
            if got is None:
                return None
            age = time.monotonic() - got[1]
            return None if age > stale_after_s(MJPEG_PERIOD_S) else age
        if self._frame is None or self._frame_asked is None:
            return None
        age = time.monotonic() - self._frame_asked
        # from the camera's own configured rate, as the Microduck's frames expire, and never
        # from a fetch's length: a stopped camera has every fetch wait out the bridge and fail
        return None if age > stale_after_s(CAMERA_PERIOD_S) else age

    def _period(self) -> float:
        """How often a frame comes: the camera's rate, or the last fetch that brought one."""
        return max(CAMERA_PERIOD_S, self._fetch_s)

    async def get_frame(self) -> Image.Image | None:
        """The newest nose frame, or None once it is too old to steer on. Never raises."""
        if self._frame_age_s() is None:
            return None
        if self._mjpeg is not None:
            got = self._mjpeg.latest()
            return None if got is None else _decode(got[0])
        return self._frame

    async def get_frames(self) -> list[CameraFrame]:
        frames: list[CameraFrame] = []
        nose = await self.get_frame()
        if nose is not None:
            frames.append(CameraFrame(NOSE, nose, primary=True))
        if self.bridge_up and WRIST in self.camera_keys:
            try:
                jpeg, _, _ = await self._call(
                    self.bridge.frame, WRIST, timeout_s=CALL_TIMEOUT_S + FRAME_WAIT_S
                )
            except Exception:
                return frames
            wrist = _decode(jpeg)
            if wrist is not None:
                frames.append(CameraFrame(WRIST, wrist))
        return frames

    def frame_taken_at(self) -> float | None:
        """When the nose frame `get_frame` hands out now was taken, at the earliest, or None.

        Only a frame the bridge answered after its own stamp passed its check: the bridge waits
        for the first image whose stamp is after the request arrived, and the request went out
        before it arrived, so the send time is a true lower bound. A frame from the cockpit's
        stream, or one whose stamp the bridge could not trust, says nothing."""
        if self._mjpeg is not None or not self._frame_after:
            return None
        if self._frame_age_s() is None:
            return None
        return self._frame_asked

    @property
    def frame_period_s(self) -> float | None:
        if self._mjpeg is not None or not self.bridge_up:
            return None
        return self._period()

    # ── state ───────────────────────────────────────────────────────────────────────

    async def get_state(self) -> DuckState:
        """What the cockpit and the bridge say now. A cockpit that does not answer is a state
        that says so, which every precondition then refuses on, rather than an exception."""
        extras: dict[str, Any] = {
            "stack": self.stack,
            "bridge_up": False,
            "bridge_error": self.bridge_error,
            "telemetry_limit_s": TELEMETRY_LIMIT_S,
            "firmware_limit_s": FIRMWARE_LIMIT_S,
            "battery_floor_v": BATTERY_FLOOR_V,
            "started": self.started,
            "cockpit": self.cockpit.url,
        }
        try:
            state = await self._call(self.cockpit.state)
        except LinkDown as e:
            extras |= {"state_stale": True, "cockpit_error": str(e)}
            return DuckState(t=time.monotonic(), policy="unknown", extras=extras)
        pose = state.get("pose") or None
        base = state.get("base") or {}
        topics = state.get("topics") or {}
        odom_hz = _number((topics.get("odom") or {}).get("hz"))
        x = y = theta = None
        if isinstance(pose, dict):
            x, y, theta = _number(pose.get("x")), _number(pose.get("y")), _number(pose.get("yaw"))
            extras["pose_age_s"] = _number(pose.get("age_s"))
        extras["pose_limit_s"] = stale_after_s(1.0 / odom_hz) if odom_hz else MIN_STALE_S
        extras |= {
            "telemetry_age_s": _number(base.get("age_s")),
            "firmware_age_s": _number(base.get("firmware_age_s")),
            "battery_v": _number(base.get("battery_v")),
            "stalled": base.get("stalled"),
            "base_mode": base.get("mode_name"),
            "output_pct": base.get("output_pct"),
            "nav": state.get("nav") or {},
            "idle": state.get("idle") or {},
        }
        if self.bridge_up:
            try:
                arm = await self._call(self.bridge.arm)
                extras |= {"bridge_up": True, "arm": arm}
                if self.stack == up.LITTER_STACK:
                    extras["mission"] = await self._call(self.bridge.mission)
                # the base stack's own navigation: the cockpit reads only the newest dimOS
                # run, and a moss-arm started after the base hides the base from it
                nav = await self._call(self.bridge.nav)
                if nav.get("ok"):
                    extras["cockpit_nav"] = extras["nav"]
                    extras["nav"] = {k: nav.get(k) for k in NAV_KEYS}
            except (LinkDown, TransportError) as e:
                extras |= {"bridge_up": False, "bridge_error": str(e)}
        return DuckState(
            t=time.monotonic(),
            policy="unknown",
            posture="unknown",
            battery_percent=_number(base.get("battery_pct")),
            x=x,
            y=y,
            theta=theta,
            extras=extras,
        )

    # ── intents ─────────────────────────────────────────────────────────────────────

    async def send_intent(self, intent: Intent) -> Ack:
        if not self.connected:
            return Ack(accepted=False, reason="moss: not connected")
        p = intent.params
        try:
            match intent.kind:
                case "move":
                    return await self._twist(p)
                case "stop":
                    await self.stop()
                    return Ack(accepted=self.stop_error is None, reason=self.stop_error)
                case "pose":
                    return await self._goal(p)
                case "do":
                    return await self._skill(p)
                case "look":
                    return Ack(
                        accepted=False,
                        reason="MOSS has no head: its cameras turn with the body or the arm",
                    )
                case "enable":
                    if not p.get("on", True):
                        return Ack(accepted=False, reason="quackd never limps a robot")
                    return Ack()
                case _:
                    return Ack(accepted=False, reason=f"MOSS cannot {intent.kind}")
        except (LinkDown, TransportError) as e:
            return Ack(accepted=False, reason=str(e))

    async def _twist(self, p: dict[str, Any]) -> Ack:
        if not self.bridge_up:
            return Ack(
                accepted=False, reason="a twist goes through quackd's bridge, and it is down"
            )
        if abs(float(p.get("vy", 0.0))) > 0.0:
            return Ack(accepted=False, reason="MOSS runs on tracks and cannot slide sideways")
        vx, wz = float(p.get("vx", 0.0)), float(p.get("wz", 0.0))
        try:
            status, doc = await self._call(self.bridge.twist, vx, wz)
        except LinkDown as e:
            return Ack(
                accepted=False, reason=f"the bridge at {self.bridge.url} stopped answering: {e}"
            )
        if status == 200 and doc.get("ok"):
            # timed from the answer, which the bridge's own clock started before, so a close
            # that reads this as fresh may send a zero the pump no longer needed, and never
            # the reverse
            self._twist_at = None if vx == 0.0 and wz == 0.0 else time.monotonic()
            if self._twist_at is not None:
                self._clean_stop = False
            return Ack()
        return Ack(accepted=False, reason=str(doc.get("reason") or f"the bridge answered {status}"))

    async def _announce(self, started: str) -> tuple[str | None, str | None]:
        """Tell the bridge what this session is about to start, before the start goes out, so
        its deadman halts it even if quackd dies the instant after: (why it could not, the
        session's word before). With no bridge there is no deadman to tell."""
        previous, self.started = self.started, started
        self._clean_stop = False
        if not self.bridge_up:
            return None, previous
        try:
            await self._call(self._beat)
        except Exception as e:
            self.started = previous
            return (
                f"the bridge did not take this session's word that it is starting {started}, "
                f"so nothing was started: {e}"
            ), previous
        return None, previous

    async def _lost(self, what: str, e: Exception) -> Ack:
        """A start whose answer never came may have started all the same: the session keeps
        its word that it did, so the deadman still covers it, and a stop goes out for it."""
        await self.stop()
        said = f" and the stop failed too: {self.stop_error}" if self.stop_error else ""
        return Ack(
            accepted=False,
            reason=f"{what} got no answer ({e}), so it may have started: quackd sent a stop{said}",
        )

    async def _goal(self, p: dict[str, Any]) -> Ack:
        if p.get("frame", "world") != "world":
            return Ack(accepted=False, reason="the cockpit takes goals in the world frame only")
        x, y = float(p["x"]), float(p["y"])
        why, previous = await self._announce("goal")
        if why is not None:
            return Ack(accepted=False, reason=why)
        try:
            ok, doc = await self._call(self.cockpit.goal, x, y)
        except LinkDown as e:
            return await self._lost("the goal", e)
        if not ok:
            self.started = previous
            return Ack(accepted=False, reason=f"the cockpit refused the goal: {doc.get('error')}")
        return Ack()

    async def _skill(self, p: dict[str, Any]) -> Ack:
        skill = p.get("skill")
        if skill in (SKILL_EXPLORE, SKILL_COLLECT):
            action = str(p.get("action", "start"))
            if skill == SKILL_COLLECT and action == "start":
                # the explorer is started for the mission, so not for one that is not there
                if not self.bridge_up:
                    return Ack(
                        accepted=False,
                        reason="the litter mission is reached through quackd's bridge, and it "
                        "is down",
                    )
                try:
                    mission = await self._call(self.bridge.mission)
                except (LinkDown, TransportError) as e:
                    return Ack(
                        accepted=False, reason=f"the bridge did not say how the mission is: {e}"
                    )
                if mission.get("stopped"):
                    return Ack(accepted=False, reason=MISSION_STOPPED)
                if not mission.get("answering"):
                    return Ack(
                        accepted=False,
                        reason="the litter mission is not answering its RPC, so nothing could "
                        "stop it",
                    )
            previous = self.started
            if action == "start":
                why, previous = await self._announce(
                    "litter" if skill == SKILL_COLLECT else "explore"
                )
                if why is not None:
                    return Ack(accepted=False, reason=why)
                if skill == SKILL_COLLECT:
                    # before the start goes out: a start whose answer was lost may have handed
                    # the rover to the mission all the same, and close() must still end it
                    self.litter_started = True
            try:
                ok, doc = await self._call(self.cockpit.explore, action)
            except LinkDown as e:
                if action != "start":
                    raise
                return await self._lost(f"the {skill} start", e)
            if not ok:
                if action == "start":
                    self.started = previous
                return Ack(accepted=False, reason=f"the cockpit refused: {doc.get('error')}")
            if action != "start" and self.started in ("explore", "litter"):
                self.started = None
            return Ack()
        if skill == SKILL_ARM_CYCLE:
            if not self.bridge_up:
                return Ack(
                    accepted=False, reason="the arm goes through quackd's bridge, and it is down"
                )
            try:
                status, doc = await self._call(
                    self.bridge.arm_cycle,
                    str(p.get("pick_pose")),
                    p.get("from_phase"),
                    self.session_id,
                )
            except LinkDown as e:
                return await self._lost("the arm cycle", e)
            if status == 200 and doc.get("ok"):
                self.arm_job_id = str(doc.get("id"))
                self._clean_stop = False
                return Ack()
            return Ack(
                accepted=False, reason=str(doc.get("reason") or f"the bridge answered {status}")
            )
        return Ack(accepted=False, reason=f"MOSS has no skill called {skill!r}")

    async def stop(self) -> None:
        """The bridge's stop and the cockpit's soft stop, both at once, every time, each failure
        kept.

        On moss-litter the bridge stops the mission first, then ends the mission's own pick,
        cancels the arm and sends a zero twist. On the other stacks it sends the zero first and
        then cancels the arm. The cockpit's soft stop goes out beside it, not after its answer,
        and ends the explorer and any goal. Both are for the base run quackd connected to: a
        stack restarted since is another run, and neither half touches it. Safe to call any
        number of times, from anywhere, and it never raises: a link that is down is a failure
        said in `stop_error`."""
        bridge_errors: list[str] = []
        cockpit_errors: list[str] = []
        self.mission_stop = None
        self.stop_sent = True
        found: list[str] = []
        """The run the bridge found running instead of the one quackd connected to."""
        soft_sent: list[bool] = []
        run, sid = self.run_id, self.session_id

        async def bridge_half() -> None:
            if not self.bridge_up:
                return
            try:
                doc = await self._call(
                    lambda: self.bridge.stop(run, sid), timeout_s=STOP_TIMEOUT_S + 1.0
                )
            except Exception as e:
                bridge_errors.append(f"the bridge's stop: {e}")
                return
            if doc.get("run_changed"):
                found.append(str(doc.get("run") or "another"))
                self.run_moved = found[0]
            else:
                # an answered stop zeroed the bridge's pump, whatever else it says failed
                self._twist_at = None
            said_mission = doc.get("mission")
            self.mission_stop = str(said_mission) if said_mission is not None else None
            if not doc.get("ok"):
                said = ", ".join(str(e) for e in doc.get("errors") or []) or "it said no"
                bridge_errors.append(f"the bridge's stop: {said}")

        async def cockpit_half() -> None:
            now = await self._run_now()
            if now is not None:
                self.run_moved = now
                cockpit_errors.append(
                    f"the cockpit's soft stop was not sent, since run {now} runs now and "
                    f"quackd connected to run {run}: it would have ended that run's explorer"
                )
                return
            soft_sent.append(True)
            try:
                ok, doc = await self._call(self.cockpit.stop)
                if not ok:
                    cockpit_errors.append(
                        f"the cockpit's soft stop: {doc.get('error') or 'it said no'}"
                    )
            except Exception as e:
                cockpit_errors.append(f"the cockpit's soft stop: {e}")

        await asyncio.gather(bridge_half(), cockpit_half())
        errors = bridge_errors + cockpit_errors
        if found and soft_sent:
            # the run changed after the last keepalive said which runs, so the soft stop that
            # went out beside the bridge's reached the new run, and the owner is told
            errors.append(
                f"the cockpit's soft stop went out before the bridge found run {found[0]} "
                "running, so it ended that run's explorer and goal"
            )
        if not errors:
            # only a stop that reached both surfaces ends what this session started. One that
            # failed keeps it, so the session still says so to the bridge's deadman and a later
            # close still sends the soft stop for it
            self.started = None
        self._clean_stop = not errors
        self.stop_error = " and ".join(errors) if errors else None

    # ── the watchdog ────────────────────────────────────────────────────────────────

    async def heartbeat(self) -> None:
        """The session's keepalive first, then a round trip to the cockpit, the cockpit's idle
        keepalive when it is due, and the stack quackd connected to still running.

        The keepalives go from here, from the loop's own heartbeat, so a loop that does not run
        sends none. A dead process, a dead link and a wedged loop all end the session, and the
        bridge halts what it started. The keepalive goes before the cockpit's state is asked,
        and again every HEARTBEAT_S for as long as the cockpit takes to answer, so a cockpit
        slow to answer delays none of them. A prompt on the
        terminal blocks the loop too, and a session that started nothing while it waits halts
        nothing. A halt that halted something this session started is this heartbeat's
        failure, which stops the run."""
        if self.bridge_up:
            await self._beat_once()
        try:
            state = await self._while_beating(self._call(self.cockpit.state))
        except LinkDown as e:
            raise HeartbeatError(
                f"moss: the cockpit at {self.cockpit.url} stopped answering with the rover's "
                f"state: {e}"
            ) from e
        self._idle = state.get("idle") or {}
        await self._while_beating(self._idle_keepalive())
        if not self.bridge_up:
            stack = state.get("stack") or {}
            same = stack.get("running") and stack.get("blueprint") == self.stack
            if not same or (self.run_id is not None and str(stack.get("run_id")) != self.run_id):
                raise HeartbeatError(self._stack_gone(stack))
            return
        window = SESSION_MISSES * MAX_KEEPALIVE_S
        if self._session_at is not None and time.monotonic() - self._session_at > window:
            # the loop was held past the window, by a prompt or a wedge, while this heartbeat
            # waited: the bridge's own word on the session decides. A halt of something this
            # session started fails here, and a halt of nothing is no event
            await self._beat_once()
        if self._session_at is None or time.monotonic() - self._session_at > window:
            raise HeartbeatError(
                f"moss: the bridge at {self.bridge.url} has taken no keepalive of this session "
                f"for {window:g} s: {self._session_error or 'no answer'}"
            )
        stack = self._session_doc.get("stack") or {}
        alive = (self._session_doc.get("session") or {}).get("run_alive") is True
        if not alive and (
            not stack.get("running")
            or (self.run_id is not None and str(stack.get("run_id")) != self.run_id)
        ):
            raise HeartbeatError(self._stack_gone(stack))

    # ── the keepalives, from the loop's own heartbeat ───────────────────────────────────

    async def _beat_once(self) -> None:
        """One keepalive. A halt of something this session started raises, and any other
        failure is the window check's to say, once the window has passed."""
        try:
            await self._call(self._beat)
        except HeartbeatError:
            raise
        except Exception:
            pass

    async def _while_beating(self, call: Awaitable[Any]) -> Any:
        """`call`, with this session's keepalive going out every HEARTBEAT_S while it waits, so
        a cockpit slow to answer never delays the bridge's keepalive. They go from the loop, as
        every keepalive does: a loop that does not run sends none."""
        work = asyncio.ensure_future(call)
        if not self.bridge_up:
            return await work
        try:
            while True:
                done, _ = await asyncio.wait({work}, timeout=HEARTBEAT_S)
                if done:
                    return work.result()
                await self._beat_once()
        except BaseException:
            work.cancel()  # a halt said, or the heartbeat stopped: the call is not waited on
            raise

    def _beat(self) -> dict[str, Any]:
        """One keepalive of this session, declaring the bridge's cap as its period, with what
        the session has started. Raises when the bridge did not take it, and keeps its answer
        for the heartbeat. A HeartbeatError when the bridge says it halted something this
        session had started, after it went quiet: what the run was doing is not running any
        more, so the run stops. A halt that halted nothing is no event."""
        with self._beat_lock:
            try:
                doc = self.bridge.keepalive(
                    self.session_id, MAX_KEEPALIVE_S, self.started, run=self.run_id
                )
            except Exception as e:
                self._session_error = str(e)
                raise
            self._session_doc, self._session_at, self._session_error = doc, time.monotonic(), None
            self._session_open = True
        halt = (doc.get("session") or {}).get("halted")
        if isinstance(halt, dict) and halt.get("halted"):
            what = " and ".join(str(h) for h in halt["halted"])
            did = "is halting" if halt.get("done") is False else "halted"
            raise HeartbeatError(
                f"moss: quackd's bridge {did} {what} after this session went quiet "
                f"({halt.get('why')}), so quackd stops here rather than go on as if it ran"
            )
        return doc

    async def _idle_keepalive(self) -> None:
        """The cockpit's idle keepalive, three times inside its own limit, from the loop's own
        heartbeat. While the loop waits on a prompt none goes, so a prompt left open longer
        than about two thirds of that limit lets the cockpit's idle stop end the stack."""
        idle = self._idle
        limit = _number(idle.get("limit_s"))
        if not idle.get("enabled") or limit is None or limit < IDLE_STOP_MIN_S:
            return
        now = time.monotonic()
        if now - self._last_cockpit_keepalive < limit / KEEPALIVES_PER_LIMIT:
            return
        with contextlib.suppress(Exception):
            ok, _ = await self._call(self.cockpit.keepalive)
            if ok:
                self._last_cockpit_keepalive = now

    async def _end_session(self) -> None:
        """The bridge's session ended if this transport opened one, so the bridge halts nothing
        for it after it has gone."""
        if self.bridge_up and self._session_open:
            sid, period = self.session_id, MAX_KEEPALIVE_S
            with contextlib.suppress(Exception):
                await self._call(lambda: self.bridge.keepalive(sid, period, None, end=True))
        self._session_open = False

    async def _run_now(self) -> str | None:
        """The base run running now when it is another than the one this session connected
        to, or None for the same run and for any run that cannot be told, all of which are
        stopped. With the bridge up, its last keepalive answer says whether the run this
        session connected to still runs, a newer run beside it notwithstanding, which is at
        most one period old and costs the stop no round trip. Without it, the cockpit's own
        state says, which shows only the newest run: a newer run of the same blueprint is a
        restart, and one of any other, moss-arm's or one started by hand, tells nothing of the
        base's."""
        if self.run_id is None:
            return None
        if self.bridge_up:
            window = SESSION_MISSES * MAX_KEEPALIVE_S
            fresh = self._session_at is not None and time.monotonic() - self._session_at <= window
            if fresh and (self._session_doc.get("session") or {}).get("run_alive") is True:
                return None  # the run it connected to still runs, whatever runs beside it
            stack = (self._session_doc.get("stack") or {}) if fresh else {}
        else:
            try:
                state = await self._call(self.cockpit.state)
            except Exception:
                return None  # a cockpit that does not answer is told to stop all the same
            stack = state.get("stack") or {}
            if stack.get("blueprint") != self.stack:
                # another blueprint newest, moss-arm or a run started by hand, says nothing of
                # the run quackd connected to, which may run behind it: it is stopped all the same
                return None
        now = stack.get("run_id")
        if now is None or str(now) == self.run_id:
            return None
        return str(now)

    def _stack_gone(self, stack: dict[str, Any]) -> str:
        now = stack.get("blueprint") if stack.get("running") else "nothing"
        return (
            f"moss: the stack quackd connected to ({self.stack}) is not running any more, and the "
            f"rover runs {now} now. The cockpit's idle stop, or {up.FLY_STOP.name}, ends every "
            "dimOS run, the arm's included"
        )

    # ── teardown ────────────────────────────────────────────────────────────────────

    def _twist_fresh(self) -> bool:
        """Whether the bridge may still be repeating a twist of this session's."""
        if self._twist_at is None:
            return False
        return time.monotonic() - self._twist_at <= self.twist_deadman_s

    async def _halt_own(self) -> list[str]:
        """close()'s halt when no `collect_litter` ran: what this session started and nothing
        else. Its own arm cycle, by its id, a twist of its own the bridge may still repeat, and
        a goal or an exploration it started, which the cockpit's soft stop ends. A session that
        started none of them sends nothing, and each failure comes back to be said. A base
        stack restarted since is another run: its twist and its explorer are not this
        session's, so only the arm cycle is cancelled, and that is said."""
        errors: list[str] = []
        now = await self._run_now()
        if self.bridge_up and self.arm_job_id is not None:
            job = self.arm_job_id
            try:
                doc = await self._call(
                    lambda: self.bridge.arm_cancel(job), timeout_s=STOP_TIMEOUT_S + 1.0
                )
                if not doc.get("ok"):
                    said = ", ".join(str(e) for e in doc.get("errors") or [])
                    errors.append(f"its arm cycle's cancel: {said or doc.get('reason')}")
            except Exception as e:
                errors.append(f"its arm cycle's cancel: {e}")
        if now is not None and (self._twist_fresh() or self.started in ("goal", "explore")):
            # its arm cycle is its own whatever runs, but its twist and its goal went to the old
            # run, and went with it
            self.run_moved = now
            self.started = None
            return errors
        if self.bridge_up and self._twist_fresh():
            try:
                status, doc = await self._call(self.bridge.twist, 0.0, 0.0)
                if status == 200 and doc.get("ok"):
                    self._twist_at = None
                else:
                    errors.append(f"its twist's zero: {doc.get('reason') or status}")
            except Exception as e:
                errors.append(f"its twist's zero: {e}")
        if self.started in ("goal", "explore"):
            try:
                ok, doc = await self._call(self.cockpit.stop)
                if not ok:
                    errors.append(f"the cockpit's soft stop: {doc.get('error') or 'it said no'}")
            except Exception as e:
                errors.append(f"the cockpit's soft stop: {e}")
            self.started = None
        return errors

    async def close(self) -> None:
        """Stop and hold what this session started, and nothing else, never a go-limp call.

        After `collect_litter` it is the whole stop, the mission's own first. Otherwise it is
        `_halt_own`, so a session that started nothing, a `quackd doctor` among them, sends no
        stop at all and leaves a mission or a goal of the owner's running. The session ends so
        the bridge halts nothing after quackd has gone. An arm this session moved and left out
        of its drive pose is said, and so is what a stop of this session's did to the mission."""
        if self.connected:
            note: list[str] = []
            if self._clean_stop:
                pass  # the last stop reached both surfaces, and nothing has started since it
            elif self.litter_started:
                await self.stop()
            elif failed := await self._halt_own():
                note.append(
                    "what this session started may still be moving, since its halt failed: "
                    + " and ".join(failed)
                )
            if self.run_moved is not None:
                note.append(
                    f"the base stack was restarted as run {self.run_moved} after quackd "
                    f"connected to run {self.run_id}, so this session's stop and halt sent "
                    "nothing to the new run"
                )
            await self._end_session()
            moved_arm = self.arm_job_id is not None or self.litter_started
            if self.bridge_up and self.arm_up and moved_arm:
                with contextlib.suppress(Exception):
                    arm = await self._call(self.bridge.arm)
                    if arm.get("answering") and arm.get("folded") is False:
                        held = (arm.get("job") or {}).get("held")
                        note.append(arm_note(held, arm.get("guard")))
            if self.stack == up.LITTER_STACK and self.stop_sent and self.run_moved is None:
                if self.mission_stop == "stopped":
                    note.append(
                        "the litter mission is stopped, and runs again only after the stack "
                        "restarts on the rover"
                    )
                else:
                    note.append(
                        "the litter mission's stop was not confirmed, so it may still be "
                        "running: stop the stack on the rover"
                    )
            self.close_note = " ".join(n[0].upper() + n[1:] + "." for n in note) or None
        if self._frames_task is not None:
            task, self._frames_task = self._frames_task, None
            # Python 3.11's wait_for loses a cancel that arrives after its call has returned and
            # before the loop has resumed it, as when a fetch and close's own last call to a
            # bridge that has gone fail together. The loop then ends on its flag at its next
            # turn, and close waits for one fetch at most
            self._frames_on = False
            task.cancel()
            await asyncio.wait({task}, timeout=CALL_TIMEOUT_S + FRAME_WAIT_S)
        if self._mjpeg is not None:
            reader, self._mjpeg = self._mjpeg, None
            await asyncio.to_thread(reader.stop)
        self.cockpit.close()
        self.bridge.close()
        self.connected = False

    async def subscribe(self, topic: str) -> AsyncIterator[dict[str, Any]]:  # type: ignore[override]
        while self.connected:
            await self.sleep(0.5)
            yield {"topic": topic, **(await self.get_state()).model_dump()}

    def now(self) -> float:
        return time.monotonic()

    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(seconds)
        if self.post_sleep is not None:
            self.post_sleep()


__all__ = [
    "ARM_CANCEL_PATH",
    "ARM_CYCLE_PATH",
    "ARM_PATH",
    "BRIDGE_PORT",
    "CMD_HEADER_NAME",
    "CMD_HEADER_VALUE",
    "COCKPIT_PORT",
    "DEFAULT_HOST",
    "FRAME_AFTER_HEADER",
    "FRAME_PATH",
    "FRAME_STAMP_HEADER",
    "HELLO_PATH",
    "KEEPALIVE_PATH",
    "MISSION_PATH",
    "NAV_PATH",
    "PROTOCOL",
    "PROTOCOL_VERSION",
    "SESSION_MISSES",
    "STARTED",
    "STOP_PATH",
    "TOKEN_ENV",
    "TOKEN_HEADER",
    "TWIST_PATH",
    "BridgeClient",
    "CockpitClient",
    "LinkDown",
    "MjpegReader",
    "MossCockpit",
    "TokenRefused",
]
