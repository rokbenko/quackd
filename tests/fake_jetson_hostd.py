"""A fake of the Jetson host daemon's protocol, on loopback, whose every answer is an attribute.

This is not a copy of `bridge/jetson/roboglot_jetson_hostd.py`. It has no camera, no model, no
board and no logic beyond routing and the token: it answers each path with whatever a test put
in the matching attribute, and records every request it was sent. That is what the client, the
doctor's host section, the host detector and the host camera need to be tested against: a
daemon whose every answer a test chooses, including the wrong ones a real daemon would never
give. The daemon's own behaviour is tested against the daemon itself.

Use it as a context manager, or as a fixture built from one::

    @pytest.fixture
    def hostd() -> Iterator[FakeHostd]:
        with FakeHostd() as fake:
            yield fake

    def test_something(hostd: FakeHostd) -> None:
        client = HostClient(hostd.address)
        hostd.token = "s3cret"                 # every path now wants X-Roboglot-Token
        hostd.camera_stopped(age_s=3.2)       # /snapshot.jpg now answers camd's stale 503
        hostd.without_detector()              # /hello says no detector, /detect answers 503
        assert hostd.requests_to("/hello")[0].headers.get("x-roboglot-token") == "s3cret"

The defaults describe one plausible board, and every one of them is a plain attribute a test
may replace: a Tegra (an Orin Nano developer kit) with a camera (62.2 degrees, 640x480 at 5 fps)
and a YOLO detector on CUDA. The snapshot is a grey 640x480 frame with an orange ball whose box
is exactly the default `/detect` reply's, in the orange the colour detector looks for, so a test
that runs either detector on it sees the same ball. The board texts are imported from
`tests/jetson_fixtures.py`, the one fake board every test reads, with the NULs removed where
the daemon removes them, so this fake cannot come to describe a different board from the one
the daemon's own tests build as files.

What it proves is that Roboglot reads the protocol as written. What it cannot prove is anything
about a Jetson, which no test here has seen.
"""

from __future__ import annotations

import copy
import hmac
import io
import json
import socket
import sys
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from PIL import Image, ImageDraw

from tests import jetson_fixtures as board_files
from tests.jetson_fixtures import (
    MEMINFO,
    NUL,
    NVPMODEL_Q,
    ORIN_NANO,
    RELEASE_36_4_3,
    TEGRASTATS_LINE,
    ZRAM_SWAPS,
)

HOSTD_VERSION = "0.1.0"
PROTOCOL = "roboglot-jetson-hostd"
PROTOCOL_VERSION = 1
TOKEN_HEADER = "X-Roboglot-Token"
MAX_JPEG_BYTES = 8 * 1024 * 1024

#: The device tree's compatible list with its NUL separators removed, as `/board` sends it. The
#: model name needs no such step: the NUL is its file's terminator, which `tegra_tree` adds.
COMPATIBLE = board_files.COMPATIBLE.replace(NUL, "")


FRAME_SIZE = (640, 480)
#: The default ball's box, in the default frame's pixels: the default `/detect` reply names it.
BALL_BOX = (300.0, 200.0, 340.0, 240.0)
SIM_ORANGE = (255, 140, 0)
"""The simulators' ball colour, which `ColorBlobDetector`'s default ball range is tuned to."""


def jpeg_bytes(
    size: tuple[int, int] = FRAME_SIZE,
    *,
    ball: tuple[float, float, float, float] | None = BALL_BOX,
    quality: int = 85,
) -> bytes:
    """A JPEG of a grey floor, with an orange ball filling `ball` unless it is None."""
    image = Image.new("RGB", size, (110, 110, 110))
    if ball is not None:
        ImageDraw.Draw(image).ellipse(ball, fill=SIM_ORANGE)
    buf = io.BytesIO()
    image.save(buf, format="JPEG", quality=quality)
    return buf.getvalue()


def default_hello() -> dict[str, Any]:
    """`/hello` from an Orin Nano with a camera and a CUDA detector, as the protocol spells it."""
    return {
        "ok": True,
        "protocol": PROTOCOL,
        "protocol_version": PROTOCOL_VERSION,
        "daemon_version": HOSTD_VERSION,
        "hostname": "orin-nano",
        "python": "3.10.12",
        "capabilities": {"camera": True, "detect": True, "tegra": True},
        "camera": {"source": "csi", "fov_deg": 62.2, "size": list(FRAME_SIZE), "fps": 5.0},
        "camera_error": None,
        "detect": {
            "model": "yolov8n.pt",
            "device": "cuda",
            "conf": 0.4,
            "names": ["person", "bicycle", "car", "sports ball", "cat", "dog"],
        },
        "detect_error": None,
        "board_model": ORIN_NANO,
    }


def default_healthz() -> dict[str, Any]:
    return {
        "ok": True,
        "daemon_version": HOSTD_VERSION,
        "uptime_s": 12.3,
        "camera": {
            "ok": True,
            "age_s": 0.12,
            "size": list(FRAME_SIZE),
            "bytes": 31337,
            "frames": 60,
            "errors": 0,
            "last_error": None,
            "stale_after_s": 1.5,
        },
        "detect": {
            "ok": True,
            "calls": 12,
            "errors": 0,
            "last_error": None,
            "last_ms": 23.4,
            "device": "cuda",
        },
    }


def default_board() -> dict[str, Any]:
    """`/board` from the same Orin Nano: every file readable, JetPack 6's GPU node, and both
    commands answering."""
    return {
        "ok": True,
        "files": {
            "/proc/device-tree/model": ORIN_NANO,
            "/proc/device-tree/compatible": COMPATIBLE,
            "/etc/nv_tegra_release": RELEASE_36_4_3,
            "/proc/meminfo": MEMINFO,
            "/proc/swaps": ZRAM_SWAPS,
        },
        "nodes": {"/dev/nvgpu/igpu0": True, "/dev/nvhost-ctrl-gpu": False, "/dev/nvidia0": False},
        "commands": {"nvpmodel -q": NVPMODEL_Q, "tegrastats": TEGRASTATS_LINE},
        "errors": {},
    }


def default_detections() -> dict[str, Any]:
    """`/detect`'s answer for the default frame: the one ball, and nothing else."""
    x1, y1, x2, y2 = BALL_BOX
    return {
        "ok": True,
        "w": FRAME_SIZE[0],
        "h": FRAME_SIZE[1],
        "boxes": [{"name": "sports ball", "conf": 0.87, "x1": x1, "y1": y1, "x2": x2, "y2": y2}],
        "model": "yolov8n.pt",
        "device": "cuda",
        "ms": 23.4,
    }


def stale_reason(age_s: float, stale_after_s: float = 1.5) -> str:
    """The Open Duck camera daemon's sentence for a camera that stopped, which hostd copies."""
    return (
        f"the last frame is {age_s:.1f}s old (stale after {stale_after_s:.1f}s); "
        "the camera has stopped"
    )


def dead_address() -> str:
    """A loopback `host:port` with nothing listening on it, for a daemon that is down."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    return f"127.0.0.1:{port}"


@dataclass(frozen=True)
class SeenRequest:
    """One request as the fake received it. Header names are lower-cased, because HTTP's are
    case-insensitive and urllib capitalises them its own way (`X-roboglot-token`)."""

    method: str
    path: str
    query: str
    headers: dict[str, str]
    body_length: int
    body: bytes = field(repr=False)


@dataclass(frozen=True)
class CannedReply:
    """A verbatim answer for one path, served in place of the normal one: for the replies a real
    daemon never sends, such as HTML, a JSON list, or a redirect."""

    status: int
    body: bytes
    content_type: str = "application/json"
    headers: tuple[tuple[str, str], ...] = ()


class FakeHostd:
    """roboglot-jetson-hostd's protocol on `127.0.0.1` and a free port, served from a thread.

    Settable, all read afresh on every request:

    - `hello`, `healthz`, `board`: the JSON objects served on those paths.
    - `snapshot_jpeg` and `frame_age_s`: the frame `/snapshot.jpg` serves and the age it is
      stamped with (`X-Frame-Age`, and `Age` in whole seconds); None for either means no frame
      and no stamp respectively.
    - `snapshot_reason`: when set, `/snapshot.jpg` answers 503 with it, adding `age_s` when
      `frame_age_s` is set, as the camera daemon does for a stale frame.
    - `detect_status` and `detect_reply`: what `POST /detect` answers once the body passed the
      protocol's checks (411 without a length, 413 over 8 MB, 400 when it is not a JPEG).
    - `token`: when set, every path wants it in `X-Roboglot-Token`, compared in constant time,
      and answers 401 without it. A token in the query string is never read.
    - `replies`: path to a `CannedReply`, served verbatim after the token check.
    - `delays`: path to seconds to sit on a request before answering it.

    `requests` records every request received, including refused ones, in order."""

    def __init__(self, *, token: str | None = None) -> None:
        self.hello: dict[str, Any] = default_hello()
        self.healthz: dict[str, Any] = default_healthz()
        self.board: dict[str, Any] = default_board()
        self.snapshot_jpeg: bytes | None = jpeg_bytes()
        self.frame_age_s: float | None = 0.12
        self.snapshot_reason: str | None = None
        self.detect_status = 200
        self.detect_reply: dict[str, Any] = default_detections()
        self.token = token
        self.replies: dict[str, CannedReply] = {}
        self.delays: dict[str, float] = {}
        self.requests: list[SeenRequest] = []
        self._lock = threading.Lock()
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    # ── describing a different board ────────────────────────────────────────────────────

    def without_camera(self, reason: str = "started with --camera none") -> FakeHostd:
        """A daemon with no capture source: `/hello` says so and `/snapshot.jpg` answers 503."""
        self.hello["capabilities"]["camera"] = False
        self.hello["camera"] = None
        self.hello["camera_error"] = reason
        self.healthz["camera"] = None
        self.snapshot_jpeg = None
        self.frame_age_s = None
        self.snapshot_reason = f"no camera on this host: {reason}"
        return self

    def without_detector(self, reason: str = "started with --no-detect") -> FakeHostd:
        """A daemon with no YOLO engine: `/hello` says so and `/detect` answers 503."""
        self.hello["capabilities"]["detect"] = False
        self.hello["detect"] = None
        self.hello["detect_error"] = reason
        self.healthz["detect"] = None
        self.detect_status = 503
        self.detect_reply = {"ok": False, "reason": f"detection is not available: {reason}"}
        return self

    def no_frame_yet(self) -> FakeHostd:
        """A camera that opened and has not delivered its first frame."""
        self.snapshot_jpeg = None
        self.frame_age_s = None
        self.snapshot_reason = "no frame captured yet"
        return self

    def camera_stopped(self, age_s: float = 3.2, stale_after_s: float = 1.5) -> FakeHostd:
        """A camera that delivered frames and then stopped: the daemon's stale 503."""
        self.frame_age_s = age_s
        self.snapshot_reason = stale_reason(age_s, stale_after_s)
        return self

    # ── what it saw ─────────────────────────────────────────────────────────────────────

    def requests_to(self, path: str) -> list[SeenRequest]:
        with self._lock:
            return [r for r in self.requests if r.path == path]

    # ── running ─────────────────────────────────────────────────────────────────────────

    @property
    def port(self) -> int:
        if self._server is None:
            raise RuntimeError("the fake daemon is not running: use it as a context manager")
        return int(self._server.server_address[1])

    @property
    def address(self) -> str:
        """`127.0.0.1:PORT`, which is what `--host` takes."""
        return f"127.0.0.1:{self.port}"

    @property
    def url(self) -> str:
        return f"http://{self.address}"

    def start(self) -> FakeHostd:
        server = _QuietServer(("127.0.0.1", 0), _handler_for(self))
        self._server = server
        self._thread = threading.Thread(
            target=server.serve_forever,
            kwargs={"poll_interval": 0.05},
            name="fake-jetson-hostd",
            daemon=True,
        )
        self._thread.start()
        return self

    def close(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)
        self._server = None
        self._thread = None

    def __enter__(self) -> FakeHostd:
        return self.start()

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ── answering ───────────────────────────────────────────────────────────────────────

    def _record(self, seen: SeenRequest) -> None:
        with self._lock:
            self.requests.append(seen)

    def _token_ok(self, given: str | None) -> bool:
        if not self.token:
            return True
        return hmac.compare_digest((given or "").encode(), self.token.encode())

    def _snapshot(self) -> tuple[int, dict[str, Any] | bytes, dict[str, str]]:
        if self.snapshot_reason is not None or self.snapshot_jpeg is None:
            body: dict[str, Any] = {
                "ok": False,
                "reason": self.snapshot_reason or "no frame captured yet",
            }
            if self.snapshot_reason is not None and self.frame_age_s is not None:
                body["age_s"] = round(self.frame_age_s, 2)
            return 503, body, {}
        headers: dict[str, str] = {}
        if self.frame_age_s is not None:
            headers = {"Age": str(int(self.frame_age_s)), "X-Frame-Age": f"{self.frame_age_s:.3f}"}
        return 200, self.snapshot_jpeg, headers


_GET_PATHS = ("/hello", "/healthz", "/board", "/snapshot.jpg")
_POST_PATHS = ("/detect",)


class _QuietServer(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request: Any, client_address: Any) -> None:
        """A client that gave up leaves its handler writing to a closed socket. The timeout
        tests make that happen on purpose, and a traceback for the test working is noise that
        reads like a failure. Anything else still prints."""
        if isinstance(sys.exc_info()[1], ConnectionError | TimeoutError):
            return
        super().handle_error(request, client_address)


def _handler_for(fake: FakeHostd) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = f"fake-jetson-hostd/{HOSTD_VERSION}"
        protocol_version = "HTTP/1.1"
        timeout = 5.0

        def do_GET(self) -> None:
            self._handle("GET")

        def do_POST(self) -> None:
            self._handle("POST")

        def do_PUT(self) -> None:
            self._handle("PUT")

        def do_DELETE(self) -> None:
            self._handle("DELETE")

        def _handle(self, method: str) -> None:
            path, _, query = self.path.partition("?")
            length_header = self.headers.get("Content-Length")
            length = int(length_header) if length_header and length_header.isdigit() else 0
            body = b""
            if 0 < length <= MAX_JPEG_BYTES:
                body = self.rfile.read(length)
            fake._record(
                SeenRequest(
                    method=method,
                    path=path,
                    query=query,
                    headers={k.lower(): v for k, v in self.headers.items()},
                    body_length=length,
                    body=body,
                )
            )
            if not fake._token_ok(self.headers.get(TOKEN_HEADER)):
                self._json(401, {"ok": False, "reason": "bad or missing token"})
                return
            delay = fake.delays.get(path, 0.0)
            if delay:
                time.sleep(delay)
            canned = fake.replies.get(path)
            if canned is not None:
                self._send(canned.status, canned.content_type, canned.body, canned.headers)
                return
            if path in _GET_PATHS and method != "GET":
                self._json(405, {"ok": False, "reason": f"{path} takes GET, not {method}"})
                return
            if path in _POST_PATHS and method != "POST":
                self._json(405, {"ok": False, "reason": f"{path} takes POST, not {method}"})
                return
            if path == "/hello":
                self._json(200, fake.hello)
            elif path == "/healthz":
                self._json(200, fake.healthz)
            elif path == "/board":
                self._json(200, fake.board)
            elif path == "/snapshot.jpg":
                status, answer, headers = fake._snapshot()
                if isinstance(answer, bytes):
                    self._send(status, "image/jpeg", answer, tuple(headers.items()))
                else:
                    self._json(status, answer)
            elif path == "/detect":
                self._detect(length_header, length, body)
            else:
                self._json(404, {"ok": False, "reason": f"nothing at {path}"})

        def _detect(self, length_header: str | None, length: int, body: bytes) -> None:
            if length_header is None:
                self._json(411, {"ok": False, "reason": "POST /detect needs a Content-Length"})
                return
            if length > MAX_JPEG_BYTES:
                self.close_connection = True
                self._json(413, {"ok": False, "reason": "the image is over 8 MB"})
                return
            if not body.startswith(b"\xff\xd8"):
                self._json(
                    400, {"ok": False, "reason": "the body is not an image this daemon can decode"}
                )
                return
            self._json(fake.detect_status, fake.detect_reply)

        def _json(self, status: int, body: dict[str, Any]) -> None:
            self._send(status, "application/json", json.dumps(copy.deepcopy(body)).encode(), ())

        def _send(
            self,
            status: int,
            content_type: str,
            payload: bytes,
            headers: tuple[tuple[str, str], ...],
        ) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            for name, value in headers:
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, fmt: str, *args: Any) -> None:
            pass

    return Handler
