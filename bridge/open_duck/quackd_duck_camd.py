"""quackd's camera server for the Open Duck Mini v2: one JPEG, over HTTP, out of the way.

Frames deliberately do not travel over the bridge's socket. The walk loop has a 20 ms budget
on a Pi Zero 2 W, and capturing plus encoding a 512 by 512 JPEG does not fit in it, so doing
that work in the walk process would degrade the gait with no other symptom. picamzero also
costs tens of megabytes of RSS on a 512 MB board that is already running onnxruntime.

So the camera lives here, in its own process, with its own memory limit and an OOM score
that makes the kernel take this before it takes the walk loop. It captures on a timer rather
than on request, so a slow client can never stall the capture, and it serves the most recent
frame to anyone who asks. quackd fetches from it directly, exactly the way `--camera-url`
already works for `microduck:jsonrpc`.

**It cannot move the robot.** There is no control path in this file at all: it reads a
camera and answers GET requests. That is the entire program.

Two processes cannot own one camera. If your `duck_config.json` says
`expression_features.camera` is true, the robot's own runtime constructs a `Cam` and owns
it, and this server refuses to start rather than fight for the device. Set that flag false
and let this serve the camera instead.

    python quackd_duck_camd.py --fake            # a synthetic frame, no camera needed
    python quackd_duck_camd.py --bind 0.0.0.0    # a real camera, on the duck

Nothing here has been run on a physical duck.
"""

from __future__ import annotations

import argparse
import base64
import contextlib
import io
import json
import logging
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

CAMD_VERSION = "0.1.0"
DEFAULT_PORT = 9872
# 1 fps was the rate for a pilot that looks about once a second. But `go_to` and
# `search_scan` close a visual loop at 10 Hz, and holding a steering correction for a whole
# frame period at 1 fps gives a per-frame loop gain above 1 — a divergent weave that swings
# the target back out of frame. The sibling Microduck transport already learned this and
# runs at 5. Paired with a smaller default frame, because camd exists precisely because CPU
# on a Pi Zero 2 W is scarce.
DEFAULT_FPS = 5.0
DEFAULT_SIZE = 256
#: A frame this many capture periods old is not a picture of now. Wide enough that ordinary
#: jitter never trips it.
STALE_PERIODS = 4.0
#: ...but a very low --fps must still expire in human time, not eventually.
MIN_STALE_S = 1.5
SNAPSHOT_PATH = "/snapshot.jpg"
HEALTH_PATH = "/healthz"

#: Upstream's own camera code resizes to 512 square, swaps red and blue, and rotates 90
#: degrees clockwise, which says the module is mounted on its side. quackd matches that by
#: default so the frame arrives the way the robot's own code produced it. Whether the swap
#: is genuinely needed is UNVERIFIED, and both are flags for whoever has the hardware.
DEFAULT_ROTATE = 90

#: A 2 by 2 grey JPEG, so `--fake` works even where neither OpenCV nor Pillow is installed.
_TINY_JPEG = base64.b64decode(
    "/9j/4AAQSkZJRgABAQEAYABgAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRofHh0a"
    "HBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/wAALCAACAAIBAREA/8QAHwAAAQUBAQEB"
    "AQEAAAAAAAAAAAECAwQFBgcICQoL/8QAtRAAAgEDAwIEAwUFBAQAAAF9AQIDAAQRBRIhMUEGE1Fh"
    "ByJxFDKBkaEII0KxwRVS0fAkM2JyggkKFhcYGRolJicoKSo0NTY3ODk6Q0RFRkdISUpTVFVWV1hZ"
    "WmNkZWZnaGlqc3R1dnd4eXqDhIWGh4iJipKTlJWWl5iZmqKjpKWmp6ipqrKztLW2t7i5usLDxMXG"
    "x8jJytLT1NXW19jZ2uHi4+Tl5ufo6erx8vP09fb3+Pn6/9oACAEBAAA/APn+iiiv/9k="
)

log = logging.getLogger("quackd-duck-camd")


# ── where the newest frame lives ────────────────────────────────────────────────────────


class FrameStore:
    """One JPEG and when it arrived. The capture thread writes, request threads read."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._jpeg: bytes | None = None
        self._at: float = 0.0
        self._size: tuple[int, int] = (0, 0)
        self._frames = 0
        self._errors = 0
        self._last_error: str | None = None

    def put(self, jpeg: bytes, size: tuple[int, int], *, now: float) -> None:
        with self._lock:
            self._jpeg = jpeg
            self._at = now
            self._size = size
            self._frames += 1
            self._last_error = None

    def fail(self, reason: str) -> None:
        with self._lock:
            self._errors += 1
            self._last_error = reason

    def get(self) -> tuple[bytes | None, float]:
        with self._lock:
            return self._jpeg, self._at

    def health(self, *, now: float) -> dict[str, Any]:
        with self._lock:
            has = self._jpeg is not None
            return {
                "ok": has,
                "camd_version": CAMD_VERSION,
                "age_s": round(now - self._at, 2) if has else None,
                "size": list(self._size) if has else None,
                "bytes": len(self._jpeg) if self._jpeg else 0,
                "frames": self._frames,
                "errors": self._errors,
                "last_error": self._last_error,
            }


# ── where a frame comes from ────────────────────────────────────────────────────────────


class PiCamera:
    """The real camera, through the two picamzero calls the robot's own code uses."""

    def __init__(
        self, size: int = DEFAULT_SIZE, rotate: int = DEFAULT_ROTATE, swap_rb: bool = True
    ):
        from picamzero import Camera  # type: ignore[import-not-found]

        self.camera = Camera()
        # picamzero's capture_array() is switch_mode_and_capture_array(still_configuration),
        # and that configuration defaults to the full sensor — so every tick reconfigured the
        # pipeline and copied ~24-36 MB before resizing to a thumbnail, inside a 140 MB
        # cgroup. Asking for the size we actually want drops the copy by more than an order
        # of magnitude.
        with contextlib.suppress(Exception):
            self.camera.still_size = (size, size)
        self.size = size
        self.rotate = rotate
        self.swap_rb = swap_rb

    def jpeg(self) -> tuple[bytes, tuple[int, int]]:
        import cv2  # installed wherever picamzero is, and what upstream uses

        frame = self.camera.capture_array()
        frame = cv2.resize(frame, (self.size, self.size))
        if self.swap_rb:
            frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        turns = {
            90: cv2.ROTATE_90_CLOCKWISE,
            180: cv2.ROTATE_180,
            270: cv2.ROTATE_90_COUNTERCLOCKWISE,
        }
        if self.rotate in turns:
            frame = cv2.rotate(frame, turns[self.rotate])
        ok, buf = cv2.imencode(".jpg", frame)
        if not ok:
            raise RuntimeError("cv2 could not encode the frame as JPEG")
        return bytes(buf.tobytes()), (self.size, self.size)


#: The scene `--fake` paints. These are the colours and the horizon quackd's own simulator
#: uses, copied rather than imported, because nothing on the robot may import quackd. An
#: orange ball on a pale floor under a pale sky is what its colour detector is tuned for, so
#: `--fake` is a smoke test of the whole chain and not just of the plumbing.
FAKE_SKY = (204, 222, 240)
FAKE_FLOOR = (236, 229, 212)
FAKE_BALL = (255, 140, 0)
FAKE_HORIZON = 0.45


class FakeCamera:
    """A duck's eye view with a ball rolling across it, so the chain works with no camera."""

    def __init__(self, size: int = 128) -> None:
        self.size = size
        self.n = 0

    def jpeg(self) -> tuple[bytes, tuple[int, int]]:
        self.n += 1
        try:
            from PIL import Image, ImageDraw

            size = self.size
            img = Image.new("RGB", (size, size), FAKE_SKY)
            draw = ImageDraw.Draw(img)
            horizon = int(size * FAKE_HORIZON)
            draw.rectangle([0, horizon, size, size], fill=FAKE_FLOOR)
            # the ball sits ON the floor, well below the horizon, because the detector reads
            # distance from where a blob meets the ground
            radius = max(4, size // 12)
            ground = horizon + (size - horizon) // 2
            cx = radius + (self.n * 3) % max(1, size - 2 * radius)
            draw.ellipse([cx - radius, ground - 2 * radius, cx + radius, ground], fill=FAKE_BALL)
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=85)
            return buf.getvalue(), (size, size)
        except Exception:
            return _TINY_JPEG, (2, 2)


def capture_loop(store: FrameStore, source: Any, fps: float, stop: threading.Event) -> None:
    period = 1.0 / max(0.1, fps)
    while not stop.is_set():
        started = time.monotonic()
        try:
            jpeg, size = source.jpeg()
            store.put(jpeg, size, now=time.monotonic())
        except Exception as e:  # a camera hiccup must not kill the server
            store.fail(str(e))
            log.warning("capture failed: %s", e)
        stop.wait(max(0.0, period - (time.monotonic() - started)))


# ── the server ──────────────────────────────────────────────────────────────────────────


def make_handler(store: FrameStore, fps: float = DEFAULT_FPS) -> type[BaseHTTPRequestHandler]:
    # A frame older than this is not a picture of now. Several capture periods, so ordinary
    # jitter never trips it, with a floor so a very low --fps still expires eventually.
    stale_after = max(MIN_STALE_S, STALE_PERIODS / max(0.1, fps))

    class Handler(BaseHTTPRequestHandler):
        server_version = f"quackd-duck-camd/{CAMD_VERSION}"
        protocol_version = "HTTP/1.1"

        def do_GET(self) -> None:
            path = self.path.split("?", 1)[0]
            if path in (SNAPSHOT_PATH, "/"):
                jpeg, at = store.get()
                if jpeg is None:
                    self._json(503, {"ok": False, "reason": "no frame captured yet"})
                    return
                # The timestamp used to be read and thrown away, so once one frame had been
                # captured this could never 503 again: a ribbon working loose on a walking
                # duck left camd answering 200 with the same JPEG forever, `observe`
                # reporting a confident detection, and `go_to` visually servoing on a
                # photograph. `lost > 30` could not save it either, because the frozen frame
                # still contained the ball.
                age = time.monotonic() - at
                if age > stale_after:
                    self._json(
                        503,
                        {
                            "ok": False,
                            "reason": f"the last frame is {age:.1f}s old (stale after "
                            f"{stale_after:.1f}s); the camera has stopped",
                            "age_s": round(age, 2),
                        },
                    )
                    return
                self._bytes(200, "image/jpeg", jpeg, age=age)
                return
            if path == HEALTH_PATH:
                health = store.health(now=time.monotonic())
                health["stale_after_s"] = round(stale_after, 2)
                self._json(200, health)
                return
            self._json(404, {"ok": False, "reason": f"nothing at {path}"})

        def _bytes(
            self, code: int, content_type: str, payload: bytes, *, age: float | None = None
        ) -> None:
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            if age is not None:
                # Age is the standard header; the float one is what quackd actually reads,
                # because whole seconds cannot express a 200 ms frame.
                self.send_header("Age", str(int(age)))
                self.send_header("X-Frame-Age", f"{age:.3f}")
            self.end_headers()
            self.wfile.write(payload)

        def _json(self, code: int, body: dict[str, Any]) -> None:
            self._bytes(code, "application/json", json.dumps(body).encode())

        def log_message(self, fmt: str, *args: Any) -> None:
            log.debug("%s %s", self.address_string(), fmt % args)

    return Handler


def serve(
    store: FrameStore, host: str, port: int, fps: float = DEFAULT_FPS
) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer((host, port), make_handler(store, fps))
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, name="quackd-duck-camd", daemon=True).start()
    return server


# ── running it ──────────────────────────────────────────────────────────────────────────


def runtime_owns_the_camera(duck_config_path: str) -> bool:
    """Two processes cannot own one camera, and the robot's runtime wins if it was told to."""
    try:
        with open(os.path.expanduser(duck_config_path), encoding="utf-8") as fh:
            config = json.load(fh)
    except (OSError, ValueError):
        return False
    return bool((config.get("expression_features") or {}).get("camera"))


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="quackd-duck-camd", description=__doc__)
    p.add_argument(
        "--bind",
        default="127.0.0.1",
        help="loopback by default: this serves a live view of wherever your robot is, and "
        "there is no authentication. Prefer an ssh tunnel over binding 0.0.0.0.",
    )
    p.add_argument("--port", type=int, default=DEFAULT_PORT)
    p.add_argument(
        "--fps",
        type=float,
        default=DEFAULT_FPS,
        help="capture rate. go_to and search_scan steer on these frames, so a low rate is a "
        "slow visual loop, not just a stale picture",
    )
    p.add_argument("--size", type=int, default=DEFAULT_SIZE)
    p.add_argument("--rotate", type=int, choices=[0, 90, 180, 270], default=DEFAULT_ROTATE)
    p.add_argument(
        "--no-swap-rb",
        action="store_true",
        help="skip upstream's red and blue swap. The default is correct and this inverts "
        "the image: an orange ball then detects as a person. For wrong-looking colours try "
        "white balance or --rotate instead",
    )
    p.add_argument("--duck-config", default="~/duck_config.json")
    p.add_argument("--fake", action="store_true", help="a synthetic frame, no camera needed")
    p.add_argument("--seconds", type=float, default=0.0, help="stop after this long")
    return p


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    logging.basicConfig(
        stream=sys.stderr,
        level=logging.INFO,
        format="quackd-duck-camd %(levelname)s %(message)s",
    )
    if not args.fake and runtime_owns_the_camera(args.duck_config):
        log.error(
            "duck_config.json has expression_features.camera true, so the robot's own "
            "runtime owns the camera and this server would fight it for the device. Set "
            "that flag false and let this serve the camera, or do not run this."
        )
        return 2

    store = FrameStore()
    source: Any = FakeCamera(args.size) if args.fake else None
    if source is None:
        try:
            source = PiCamera(args.size, args.rotate, not args.no_swap_rb)
        except Exception as e:
            log.error("no camera: %s. Try --fake to check the plumbing without one.", e)
            return 2

    if args.bind not in ("127.0.0.1", "localhost"):
        log.warning(
            "binding %s: this serves a live view of wherever your robot is, to anyone on "
            "that network, with no authentication. Prefer --bind 127.0.0.1 and an ssh tunnel.",
            args.bind,
        )

    stop = threading.Event()
    threading.Thread(
        target=capture_loop, args=(store, source, args.fps, stop), name="capture", daemon=True
    ).start()
    server = serve(store, args.bind, args.port, args.fps)
    port = server.server_address[1]
    log.info(
        "serving http://%s:%s%s at %.1f fps. Point the bridge at it with "
        "--camera-url http://<this-pi>:%s%s",
        args.bind,
        port,
        SNAPSHOT_PATH,
        args.fps,
        port,
        SNAPSHOT_PATH,
    )
    try:
        if args.seconds:
            time.sleep(args.seconds)
        else:
            while True:
                time.sleep(3600)
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        server.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
