"""A fake of MOSS's cockpit, on loopback, written from `tools/moss_cockpit.py` at the pin.

Not a copy of the cockpit and not a rover: it answers the routes quackd uses in the cockpit's
own shapes, and every answer is an attribute a test may change, including the wrong ones a
real cockpit can give. What it keeps from the source, because the client depends on each:

- `GET /state.json` with the keys `LiveCockpit.state` returns (stack, idle, pose, topics,
  base, nav, bus, mode), and `nav.goal_reached` reported only once it arrived after the goal.
- `POST /cmd/*` refused 403 without `X-Requested-With: moss-cockpit`, its body unread.
- `/cmd/goal` while exploring stops the explorer and holds the goal back `goal_defer_s`,
  `GOAL_AFTER_STOP_S` in the cockpit, before it shows in `nav.goal`.
- `/cmd/keepalive` answers with the idle document.
- `GET /camera.mjpg` as multipart with `boundary=frame`, at most `max_viewers` at once, a
  fifth refused 503, the cockpit's own `MAX_CAMERA_CLIENTS`.

Every request is recorded, so a test can say what the client sent and what it never sent:
`/cmd/stack` above all.

What it proves is that quackd reads the cockpit as written. What it cannot prove is anything
about a rover.
"""

from __future__ import annotations

import contextlib
import io
import json
import socket
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlsplit

from PIL import Image, ImageDraw

CMD_HEADER = ("X-Requested-With", "moss-cockpit")
GOAL_AFTER_STOP_S = 3.0
MJPEG_PERIOD_S = 0.2
MAX_CAMERA_CLIENTS = 4
IDLE_STOP_DEFAULT_S = 300.0


def jpeg(color: tuple[int, int, int] = (90, 120, 90), size: tuple[int, int] = (64, 48)) -> bytes:
    img = Image.new("RGB", size, color)
    ImageDraw.Draw(img).rectangle([20, 16, 40, 32], fill=(255, 140, 0))
    out = io.BytesIO()
    img.save(out, "JPEG")
    return out.getvalue()


@dataclass
class Request:
    method: str
    path: str
    query: str
    headers: dict[str, str]
    body: dict[str, Any] | None
    t: float = field(default_factory=time.monotonic)
    """When it arrived, on the clock the bridge's fake bus stamps its publishes with."""


@dataclass
class FakeCockpit:
    """Use as a context manager: the server runs on a free loopback port while it is open."""

    blueprint: str = "moss-explore"
    running: bool = True
    run_id: str = "20261020-run"
    mode: str = "live"
    transport: str = "lcm"
    pose: dict[str, Any] | None = field(
        default_factory=lambda: {"x": 0.0, "y": 0.0, "yaw": 0.0, "age_s": 0.05}
    )
    odom_hz: float = 10.0
    base: dict[str, Any] = field(
        default_factory=lambda: {
            "fw": "qtpy-mdd3a-v4",
            "driver": "mdd10a",
            "mode": 1,
            "mode_name": "ready",
            "battery_v": 11.6,
            "battery_pct": 58,
            "output_pct": [0, 0],
            "ramp_ms": [5, 5],
            "firmware_age_s": 0.1,
            "stalled": False,
            "stall_count": 0,
            "age_s": 0.2,
        }
    )
    exploring: bool = False
    explore_done: bool = False
    goal: dict[str, float] | None = None
    goal_reached: bool | None = None
    idle_limit_s: float = IDLE_STOP_DEFAULT_S
    idle_enabled: bool = True
    goal_defer_s: float = GOAL_AFTER_STOP_S
    frame_period_s: float = MJPEG_PERIOD_S
    max_viewers: int = MAX_CAMERA_CLIENTS
    camera_on: bool = True
    frame: bytes = field(default_factory=jpeg)
    state_error: str | None = None
    """When set, `/state.json` answers 500 with it as its error, as the cockpit does when
    building its state raises."""
    refuse: dict[str, tuple[int, str]] = field(default_factory=dict)
    """Command name to (status, error): the answer a command gets instead of its own."""
    newest: str | None = None
    """The newest dimOS run, when it is not the base stack: `moss-arm`, started after the base
    as tools/moss-fly.sh's gate 0 makes it. The cockpit reads only the newest run
    (stack_status), so then its stack, its nav's exploring, its goal deferral and its idle
    stop all go by that run and not by the base."""
    explorer_replan_s: float = 2.0
    """How soon the explorer sends a goal of its own over one sent beside it: the fake's own
    number, since nobody has timed the explorer's next frontier."""
    bus: Any = None
    """The bridge's fake bus beside this cockpit, which carries what the cockpit and the
    planner publish on the rover's one LCM bus (`nav_publish`), as the real bus carries it to
    the bridge."""
    requests: list[Request] = field(default_factory=list)
    viewers: int = 0
    idle_reset_at: float = field(default_factory=time.monotonic)

    def __post_init__(self) -> None:
        self._lock = threading.Lock()
        self._server: ThreadingHTTPServer | None = None
        self._pending: tuple[dict[str, float], float] | None = None
        self._beside: float | None = None
        """When a goal went out beside a running explorer the cockpit could not see."""
        self._stopping = threading.Event()
        self._conns: set[socket.socket] = set()
        """Every connection being served, so a cockpit that goes away drops them all, as a
        process that died does, and not only its listening socket."""

    # ── what the cockpit says ───────────────────────────────────────────────────────

    @property
    def shown(self) -> str:
        """The blueprint the cockpit reads: the newest run's."""
        return self.newest or self.blueprint

    def _sees_exploring(self) -> bool:
        """The cockpit's own rule: only an explore stack can be exploring."""
        return self.exploring and "explore" in self.shown

    def _publish(self, stream: str, value: Any) -> None:
        if self.bus is not None:
            self.bus.nav_publish(stream, value)

    def stack_doc(self) -> dict[str, Any]:
        if not self.running:
            return {"running": False}
        return {
            "running": True,
            "run_id": self.run_id,
            "blueprint": self.shown,
            "pid": 4242,
            "started_at": "2026-10-20T10:00:00+00:00",
            "uptime_s": 12.0,
            "log_dir": None,
        }

    def idle_doc(self) -> dict[str, Any]:
        idle_s = round(time.monotonic() - self.idle_reset_at, 1)
        counted = self.idle_enabled and self.shown in ("moss-nav", "moss-explore")
        return {
            "enabled": self.idle_enabled,
            "idle_s": idle_s,
            "stop_in_s": round(max(0.0, self.idle_limit_s - idle_s), 1) if counted else None,
            "limit_s": self.idle_limit_s,
            "last_source": "start",
            "blind": None,
        }

    def state(self) -> dict[str, Any]:
        with self._lock:
            if self._pending is not None and time.monotonic() >= self._pending[1]:
                self.goal, self.goal_reached = self._pending[0], None
                self._pending = None
                self._publish("goal_request", dict(self.goal))
            replan = self._beside is not None and self.exploring
            if replan and time.monotonic() - (self._beside or 0.0) >= self.explorer_replan_s:
                # the explorer, never stopped, sends its next frontier over the goal
                self.goal, self.goal_reached, self._beside = {"x": 9.9, "y": 9.9}, None, None
                self._publish("goal_request", dict(self.goal))
            return {
                "ts": time.time(),
                "mode": self.mode,
                "stack": self.stack_doc(),
                "idle": self.idle_doc(),
                "pose": self.pose,
                "topics": {"odom": {"age_s": 0.05, "hz": self.odom_hz}},
                "base": dict(self.base),
                "nav": {
                    "exploring": self._sees_exploring(),
                    "explore_done": self.explore_done,
                    "goal": self.goal,
                    "goal_reached": self.goal_reached,
                    "last_cmd_vel": None,
                },
                "bus": {"transport": self.transport, "listening": True, "error": None},
                "camera": {"clients": self.viewers},
            }

    def command(self, name: str, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        if name in self.refuse:
            code, error = self.refuse[name]
            return code, {"ok": False, "error": error}
        self.idle_reset_at = time.monotonic()  # any POST is activity, as the cockpit counts it
        moss = self.running and self.blueprint.startswith("moss")
        if name == "keepalive":
            return 200, {"ok": True, "idle": self.idle_doc()}
        if name == "stack":
            return 200, {"ok": True, "sent": f"moss-fly.sh {body.get('action')}"}
        if self.transport != "lcm":
            return 503, {"ok": False, "error": f"cannot publish on the bus here: {self.transport}"}
        if name == "explore":
            if body.get("action") not in ("start", "stop"):
                return 400, {"ok": False, "error": 'explore needs "action": "start" or "stop"'}
            if not moss:
                return 409, {"ok": False, "error": "refused: not a MOSS stack"}
            with self._lock:
                self.exploring = body["action"] == "start"
                self._pending, self._beside = None, None
                if body["action"] == "start":
                    self._publish("explore_cmd", True)
                else:
                    self._publish("stop_explore_cmd", True)
            return 200, {"ok": True, "sent": "/explore_cmd#std_msgs.Bool"}
        if name == "goal":
            try:
                x, y = float(body["x"]), float(body["y"])
            except (KeyError, TypeError, ValueError):
                return 400, {"ok": False, "error": "goal needs numbers x and y"}
            if not moss:
                return 409, {"ok": False, "error": "refused: not a MOSS stack"}
            wanted = {"x": round(x, 3), "y": round(y, 3)}
            with self._lock:
                if self._sees_exploring():
                    self.exploring = False
                    self._publish("stop_explore_cmd", True)
                    self._pending = (wanted, time.monotonic() + self.goal_defer_s)
                    return 200, {
                        "ok": True,
                        "queued_s": self.goal_defer_s,
                        "goal": {"x": x, "y": y},
                    }
                # an explorer the cockpit cannot see is not stopped: it goes on, and its next
                # frontier replaces this goal
                self._beside = time.monotonic() if self.exploring else None
                self.goal, self.goal_reached = wanted, None
                self._publish("goal_request", dict(wanted))
            return 200, {"ok": True, "sent": "/goal_request#geometry_msgs.PoseStamped"}
        if name == "stop":
            with self._lock:
                self.exploring, self._pending, self._beside = False, None, None
                self._publish("stop_explore_cmd", True)
                self._publish("stop_movement", True)
            return 200, {"ok": True, "sent": "/tele_cmd_vel#geometry_msgs.Twist"}
        return 404, {"ok": False, "error": f"unknown command {name!r}"}

    # ── what a test does to it ──────────────────────────────────────────────────────

    def reach_goal(self, ok: bool = True) -> None:
        """The planner's verdict on the goal it has now."""
        with self._lock:
            self.goal_reached = ok
            self._publish("goal_reached", ok)

    def posted(self, name: str) -> list[Request]:
        return [r for r in self.requests if r.method == "POST" and r.path == f"/cmd/{name}"]

    @property
    def url(self) -> str:
        assert self._server is not None
        return f"http://127.0.0.1:{self._server.server_address[1]}"

    @property
    def port(self) -> int:
        assert self._server is not None
        return int(self._server.server_address[1])

    def __enter__(self) -> FakeCockpit:
        fake = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def setup(self) -> None:
                super().setup()
                fake._conns.add(self.connection)

            def finish(self) -> None:
                fake._conns.discard(self.connection)
                super().finish()

            def log_message(self, *args: Any) -> None:
                pass

            def _send(self, code: int, doc: dict[str, Any]) -> None:
                body = json.dumps(doc).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self) -> None:
                url = urlsplit(self.path)
                fake.requests.append(
                    Request("GET", url.path, url.query, dict(self.headers.items()), None)
                )
                if url.path == "/state.json" and fake.state_error is not None:
                    self._send(500, {"error": fake.state_error})
                elif url.path == "/state.json":
                    self._send(200, fake.state())
                elif url.path == "/camera.mjpg":
                    self._mjpeg(parse_qs(url.query).get("cam", ["nose"])[0])
                else:
                    self._send(404, {"error": f"nothing at this address: {url.path}"})

            def _mjpeg(self, cam: str) -> None:
                with fake._lock:
                    if fake.viewers >= fake.max_viewers:
                        self._send(
                            503,
                            {"error": f"{fake.max_viewers} viewers already on the {cam} camera"},
                        )
                        return
                    fake.viewers += 1
                try:
                    self.send_response(200)
                    self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
                    self.send_header("Connection", "close")
                    self.end_headers()
                    self.close_connection = True
                    while not fake._stopping.is_set():
                        if fake.camera_on:
                            data = fake.frame
                            self.wfile.write(
                                b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: "
                                + str(len(data)).encode()
                                + b"\r\n\r\n"
                                + data
                                + b"\r\n"
                            )
                            self.wfile.flush()
                        time.sleep(fake.frame_period_s)
                except OSError:
                    pass
                finally:
                    with fake._lock:
                        fake.viewers -= 1

            def do_POST(self) -> None:
                url = urlsplit(self.path)
                headers = dict(self.headers.items())
                if not url.path.startswith("/cmd/"):
                    fake.requests.append(Request("POST", url.path, url.query, headers, None))
                    self._send(404, {"ok": False, "error": "nothing at this address"})
                    return
                if self.headers.get(CMD_HEADER[0]) != CMD_HEADER[1]:
                    fake.requests.append(Request("POST", url.path, url.query, headers, None))
                    self.close_connection = True
                    self._send(403, {"ok": False, "error": "refused: POST /cmd/* needs the header"})
                    return
                length = int(self.headers.get("Content-Length") or "0")
                raw = self.rfile.read(length) if length else b""
                body = json.loads(raw.decode("utf-8")) if raw.strip() else {}
                fake.requests.append(Request("POST", url.path, url.query, headers, body))
                code, doc = fake.command(url.path[len("/cmd/") :], body)
                self._send(code, doc)

        class Server(ThreadingHTTPServer):
            daemon_threads = True
            allow_reuse_address = False

        self._server = Server(("127.0.0.1", 0), Handler)
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._stopping.set()
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        for conn in list(self._conns):
            with contextlib.suppress(OSError):
                conn.shutdown(socket.SHUT_RDWR)


@contextmanager
def fake_cockpit(**kw: Any) -> Iterator[FakeCockpit]:
    with FakeCockpit(**kw) as fake:
        yield fake
