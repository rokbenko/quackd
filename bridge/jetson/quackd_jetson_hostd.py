#!/usr/bin/env python3
"""quackd's daemon for an NVIDIA Jetson: the board's camera, its GPU and its health, over HTTP.

The quackd that reaches this runs on a laptop, where `--host` names the Jetson, and this is
what answers there; the one command that runs on a board itself, `quackd onboard`, needs none
of it. It gives quackd three things a model server cannot: a frame from a camera
on the board, detections computed on the board's GPU, and the board's own facts (its model,
its L4T release, its memory and swap, its power mode, one `tegrastats` line). The fourth thing
the board offers, the language model on its GPU, is your own model server (Ollama and the
like), which quackd reaches through the same `--host`.

It parses nothing. `/board` ships raw file text and raw command output, and quackd reads them
with the parsers `quackd doctor` already has, so there is one parser to fix and it lives where
the tests are.

**It cannot move a robot.** There is no control path in this file: it reads a camera, runs a
detector on the JPEGs it is sent, reads five files and runs two read-only commands. When the
board is also a robot's own computer, the robot's daemon owns the body, and this process is
the one to lose first; the systemd unit beside this file says so to the kernel.

Rules this file lives by, the same as the other daemons under `bridge/`:

- **It never imports quackd.** quackd's dependencies do not belong on the board.
- **Python 3.10 and the standard library at import.** JetPack 6's system python is 3.10, and
  that python is the one with an OpenCV built against GStreamer, which the CSI camera needs.
  OpenCV, Pillow, torch and ultralytics are imported where they are used, and a board without
  one of them says so in `/hello` instead of failing to start.
- **It is testable with no board.** Every file is read relative to a board root, `--camera
  fake` paints a scene quackd's own colour detector can see, and the tests drive the real
  server in-process with a fake ultralytics.

    python quackd_jetson_hostd.py --camera fake --no-detect      # any laptop, no camera, no GPU
    python quackd_jetson_hostd.py --camera csi --token-file /etc/quackd/jetson-hostd.token

Nothing here has been run on a Jetson by this project.
"""

from __future__ import annotations

import argparse
import base64
import contextlib
import hmac
import io
import json
import logging
import os
import platform
import re
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs

HOSTD_VERSION = "0.1.0"
PROTOCOL = "quackd-jetson-hostd"
PROTOCOL_VERSION = 1
DEFAULT_PORT = 9874
"""The Open Duck Mini takes 9871 and 9872 and the ToddlerBot daemon takes 9873, and SECURITY.md
tells people to tunnel those, so the board's own daemon comes next. A ToddlerBot's Jetson can
run both, which is why the numbers must not collide."""
TOKEN_ENV = "QUACKD_HOST_TOKEN"
TOKEN_HEADER = "X-Quackd-Token"
"""The token rides in a header and never in a URL, because URLs get logged and land in
transcripts. A token in the query string is not read at all, rather than read and refused."""

DEFAULT_FPS = 5.0
"""The rate the Open Duck camera daemon settled on: `go_to` and `search_scan` close a visual
loop at 10 Hz, and a frame period much longer than that makes the steering weave."""
MIN_FPS = 1.0
"""The slowest `--fps` this daemon takes. quackd refuses a frame older than 2 s as a camera
that has stopped (`STALE_AFTER_S` in quackd/host.py), and the newest frame is up to one capture
period old plus however long the next capture takes. At 1 fps that leaves a second for a slow
capture. At 0.5 it leaves none, and below that quackd would call a working camera stopped on
a share of its snapshots while this daemon's own `/healthz` said ok."""
DEFAULT_SIZE = (640, 480)
"""A box the served JPEG fits inside, not a size it is forced to. See `fit_within`."""
JPEG_QUALITY = 80
#: A frame this many capture periods old is not a picture of now. Wide enough that ordinary
#: jitter never trips it.
STALE_PERIODS = 4.0
#: ...but a very low --fps must still expire in human time, not eventually.
MIN_STALE_S = 1.5
#: How long a client may hold a connection open without sending anything.
REQUEST_TIMEOUT_S = 5.0
REQUEST_DEADLINE_S = 10.0
"""How long one request may take to arrive whole, head and body, from when the connection
starts waiting for it. `REQUEST_TIMEOUT_S` bounds each wait on the socket, and a client that
sends a byte every four seconds never trips it, so this bounds all the waits together."""
MAX_HEAD_BYTES = 32 << 10
"""The most a request line and its headers may take. quackd sends a few hundred bytes, and
http.server alone reads a hundred header lines of 64 KB, over 6 MB, before this daemon's code
or its token check runs. A head over this is not answered: the connection is closed."""
MAX_CONNECTIONS = 16
"""How many connections are served at once. Each one is a thread and whatever it has read,
and ThreadingHTTPServer bounds neither, so without this anyone who can reach the port could
open connections until the board ran out of memory. quackd opens one per call and closes it.
A connection past this is answered 503 and closed."""
LINGER_S = 2.0
"""How long a connection closed on a body it did not read waits for the client to finish
sending, dropping what arrives, before it closes anyway. See `_linger`."""
DRAIN_CHUNK_BYTES = 64 << 10
"""A body read only to be dropped is read this much at a time, never all at once."""
MAX_JPEG_BYTES = 8 << 20
"""The largest body `POST /detect` reads. quackd sends a 640 by 480 JPEG of tens of kilobytes,
so this is room for a large frame and a bound on what a stranger can make the board hold."""
MAX_DETECT_PIXELS = 4096 * 4096
"""The largest image `POST /detect` decodes. A JPEG of a few bytes can declare a canvas of
billions of pixels, and Pillow only refuses one past twice its own warning threshold, so a
small body could otherwise make a board with 8 GB of shared memory allocate hundreds of MB."""
DETECT_SLOTS = 2
"""How many `POST /detect` requests may hold a body and its decoded image at once. The two caps
above are per request, so without this thirty requests at the pixel cap would hold thirty
decoded frames of 48 MB each while they queued for the model. Two rather than one, so a slow
upload cannot stall the model for everyone else."""
DETECT_WAIT_S = REQUEST_TIMEOUT_S
"""How long a `POST /detect` waits for a slot before it is refused as busy, its body unread."""

DEFAULT_YOLO_MODEL = "yolov8n.pt"
DEFAULT_CONF = 0.4
"""The confidence floor quackd's own `YoloDetector` uses, so a box means the same on both."""

FILE_CAP_BYTES = 65536
"""No file `/board` reads is anywhere near this, and a wrong board root must not be able to
make a reply of one. It bounds each value as it goes on the wire, JSON's escapes included,
not only what is read: a byte that is not UTF-8 or a control character is six bytes there."""
NVPMODEL_TIMEOUT_S = 3.0
TEGRASTATS_TIMEOUT_S = 3.0

HELLO_PATH = "/hello"
HEALTH_PATH = "/healthz"
BOARD_PATH = "/board"
SNAPSHOT_PATH = "/snapshot.jpg"
DETECT_PATH = "/detect"
GET_PATHS = frozenset({HELLO_PATH, HEALTH_PATH, BOARD_PATH, SNAPSHOT_PATH})
POST_PATHS = frozenset({DETECT_PATH})
"""The only path that takes a body, and what it does with the body is look at it."""

BOARD_FILES = (
    "/proc/device-tree/model",
    "/proc/device-tree/compatible",
    "/etc/nv_tegra_release",
    "/proc/meminfo",
    "/proc/swaps",
)
"""Exactly the files `quackd doctor` reads, under the names it reads them by. The device tree
entries are the kernel's own bytes and are NUL terminated, which is why every read strips NULs."""
GPU_NODES = ("/dev/nvgpu/igpu0", "/dev/nvhost-ctrl-gpu", "/dev/nvidia0")
"""JetPack 6's node, JetPack 5's, and the one a discrete driver leaves: the same three names
`quackd/doctor.py` has always looked for. A model server that cannot see one of these is the
commonest reason a Jetson answers off its CPU."""
COMMANDS: dict[str, tuple[tuple[str, ...], float, bool]] = {
    "nvpmodel -q": (("nvpmodel", "-q"), NVPMODEL_TIMEOUT_S, False),
    "tegrastats": (("tegrastats", "--interval", "500"), TEGRASTATS_TIMEOUT_S, True),
}
"""Name in the reply, argv, hard timeout, and whether the command streams forever so only its
first line is wanted. Both only read: `nvpmodel -q` queries the mode and never sets one."""

DEFAULT_CSI_PIPELINE = (
    "nvarguscamerasrc sensor-id=0 ! "
    "video/x-raw(memory:NVMM), width=1640, height=1232, framerate=30/1, format=NV12 ! "
    "nvvidconv ! video/x-raw, format=BGRx ! videoconvert ! video/x-raw, format=BGR ! "
    "appsink drop=1 max-buffers=1"
)
"""What `--camera csi` opens. UNTESTED: no Jetson has run this, so nobody has watched this
string produce a frame.

It needs the OpenCV JetPack installs for its system python, which is built with GStreamer; a
pip `opencv-python` wheel is not, and opening this through one fails. 1640 by 1232 is the
IMX219's binned full-sensor mode, so the frame keeps the lens's whole field of view, where the
1280 by 720 mode is a crop of the middle. `drop=1 max-buffers=1` makes the appsink hold the
newest frame only, so a capture on a 5 Hz timer reads now rather than a queue of the past.
Any other sensor or mode is a pipeline string passed to `--camera` directly."""

#: A 2 by 2 grey JPEG, so `--camera fake` works where neither OpenCV nor Pillow is installed.
_TINY_JPEG = base64.b64decode(
    "/9j/4AAQSkZJRgABAQEAYABgAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRofHh0a"
    "HBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/wAALCAACAAIBAREA/8QAHwAAAQUBAQEB"
    "AQEAAAAAAAAAAAECAwQFBgcICQoL/8QAtRAAAgEDAwIEAwUFBAQAAAF9AQIDAAQRBRIhMUEGE1Fh"
    "ByJxFDKBkaEII0KxwRVS0fAkM2JyggkKFhcYGRolJicoKSo0NTY3ODk6Q0RFRkdISUpTVFVWV1hZ"
    "WmNkZWZnaGlqc3R1dnd4eXqDhIWGh4iJipKTlJWWl5iZmqKjpKWmp6ipqrKztLW2t7i5usLDxMXG"
    "x8jJytLT1NXW19jZ2uHi4+Tl5ufo6erx8vP09fb3+Pn6/9oACAEBAAA/APn+iiiv/9k="
)

log = logging.getLogger("quackd-jetson-hostd")


def stale_after_s(fps: float) -> float:
    """How old a frame may be and still count as a picture of now, at this capture rate."""
    return max(MIN_STALE_S, STALE_PERIODS / max(0.1, fps))


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

    def health(self, *, now: float, stale_after: float) -> dict[str, Any]:
        """The Open Duck camera daemon's health, with `ok` meaning fresh rather than merely
        present: a camera that stopped an hour ago still has a frame."""
        with self._lock:
            has = self._jpeg is not None
            age = now - self._at if has else None
            return {
                "ok": age is not None and age <= stale_after,
                "age_s": round(age, 2) if age is not None else None,
                "size": list(self._size) if has else None,
                "bytes": len(self._jpeg) if self._jpeg else 0,
                "frames": self._frames,
                "errors": self._errors,
                "last_error": self._last_error,
                "stale_after_s": round(stale_after, 2),
            }


# ── where a frame comes from ────────────────────────────────────────────────────────────


class CameraUnavailable(RuntimeError):
    """A camera that was asked for and is not there, with the reason `/hello` should give."""


def fit_within(width: int, height: int, box: tuple[int, int]) -> tuple[int, int]:
    """The size a `width` by `height` frame is shrunk to so it fits inside `box`.

    The aspect ratio is kept, so the frame's horizontal field of view is still the lens's and
    the bearings quackd computes from it stay right. Stretching a 16:9 frame into 4:3 would
    bend every bearing, and cropping it would narrow the view without anyone saying so.

    Never enlarged: upscaling adds bytes and no information, and the field of view is the same
    at any size."""
    scale = min(box[0] / max(1, width), box[1] / max(1, height), 1.0)
    return max(1, round(width * scale)), max(1, round(height * scale))


def _gstreamer_built_in(cv2: Any) -> bool | None:
    """Whether this OpenCV can open a GStreamer pipeline, or None if it will not say.

    The commonest way `--camera csi` fails is an OpenCV from pip, which is built without
    GStreamer and answers a pipeline with a capture that is simply not open."""
    try:
        info = cv2.getBuildInformation()
    except Exception:
        return None
    match = re.search(r"GStreamer:\s*(\w+)", str(info))
    return None if match is None else match.group(1).upper() == "YES"


_SECRET_NAME = re.compile(
    r"(?:^|[-_.])(?:pw|pass|key|auth|authorization|cookies?|headers|credentials?|sig|signature)$"
    r"|(?:pwd|passwd|password|passphrase|secret|token|apikey)$",
    re.IGNORECASE,
)
"""A property or query parameter whose value is a credential: rtspsrc's and souphttpsrc's
`user-pw` and `proxy-pw`, srtsrc's `passphrase`, souphttpsrc's `extra-headers` and `cookies`,
and the query names quackd's own `SECRET_QUERY_KEYS` redacts (`pwd`, `token`, `api_key`...)."""
_VALUE = r"""(?:\([\w-]+\)\s*)?(?:"(?:\\.|[^"\\])*"?|'(?:\\.|[^'\\])*'?|(?:\\.|[^\s\\])+)"""
"""A property value as GStreamer's lexer reads one: a quoted string with its escapes, or a run
of anything but unescaped whitespace, `!` included. An optional `(type)` cast before either."""
_ASSIGNMENT = re.compile(rf"(?<![^\s])([A-Za-z_][\w-]*)\s*=\s*({_VALUE})", re.DOTALL)
"""`name=value` at the start of a word, with the spaces around `=` the lexer also takes."""
_WORD = re.compile(r"""(?:"(?:\\.|[^"\\])*"?|'(?:\\.|[^'\\])*'?|\\.|[^\s"'\\])+""", re.DOTALL)
"""One word of a pipeline, a quoted part and its spaces included, so a URL in quotes is whole."""
_URL_USERINFO = re.compile(r"(?<![A-Za-z0-9+.-])([A-Za-z][A-Za-z0-9+.-]*://).*@", re.DOTALL)
"""A URL's scheme and everything after it up to the word's last `@`: a password may hold an `@`
or a `/`, and masking a path along with it costs nothing a reader needs."""
_QUERY_PAIR = re.compile(r"""([?&])([^=&#?\s]+)=(.*?)(?=[&#]|["']?\Z)""", re.DOTALL)
"""One `name=value` of a query string, up to the next pair or the word's end."""


def _mask_assignment(match: re.Match[str]) -> str:
    name = match.group(1)
    return f"{name}=***" if _SECRET_NAME.search(name) else match.group(0)


def _mask_query_pair(match: re.Match[str]) -> str:
    sep, name = match.group(1), match.group(2)
    return f"{sep}{name}=***" if _SECRET_NAME.search(name) else match.group(0)


def _redact_word(match: re.Match[str]) -> str:
    word = _URL_USERINFO.sub(r"\1***@", match.group(0))
    return _QUERY_PAIR.sub(_mask_query_pair, word)


def redact(text: str) -> str:
    """`text` with the credentials a camera pipeline can carry replaced by `***`.

    A GStreamer pipeline of your own can hold a camera's credentials: as the userinfo of a URL
    (`rtsp://user:pass@host/`), as a query parameter (`?pwd=`, `?token=`), or as a property
    such as rtspsrc's `user-pw=` or souphttpsrc's `extra-headers=`, with or without spaces
    around its `=`. The pipeline is shown in `/hello`, in every reason that names the camera
    and in the log, which is to say to every client and to journald, and quackd puts a 503's
    reason in the error it raises, so an unredacted password would reach the laptop's terminal
    and its transcripts. Only the capture itself is handed the real string.

    It masks by name, like quackd's own redaction, and it masks too much rather than too
    little: a whole URL userinfo, username included, and a value to the end of its word."""
    return _WORD.sub(_redact_word, _ASSIGNMENT.sub(_mask_assignment, text))


class Cv2Camera:
    """A camera OpenCV can open: the CSI module, a V4L2 device by index, or any pipeline.

    `csi` opens `DEFAULT_CSI_PIPELINE` through GStreamer, a spec containing `!` is a GStreamer
    pipeline of your own that must end in an appsink, and digits are a V4L2 index, so `0` is
    `/dev/video0`. OpenCV hands back BGR and encodes BGR, so no channel swap happens here.

    UNTESTED against a real camera on a Jetson; the tests stub the capture. The constructor
    reads one frame, so a camera that opens and delivers nothing is refused at start rather
    than advertised in `/hello` and discovered at the first snapshot."""

    def __init__(self, spec: str, size: tuple[int, int] = DEFAULT_SIZE) -> None:
        try:
            import cv2
        except ImportError as e:
            raise CameraUnavailable(
                f"OpenCV (cv2) is not importable by this python: {e}. JetPack's own python3 "
                "has the OpenCV built with GStreamer; a virtualenv usually has none"
            ) from e
        self._cv2 = cv2
        self.box = size
        gstreamer = spec == "csi" or "!" in spec
        if spec == "csi":
            self.source = "csi"
            cap = cv2.VideoCapture(DEFAULT_CSI_PIPELINE, cv2.CAP_GSTREAMER)
        elif "!" in spec:
            self.source = f"gstreamer: {redact(spec)}"
            cap = cv2.VideoCapture(spec, cv2.CAP_GSTREAMER)
        else:
            index = int(spec)
            self.source = f"/dev/video{index}"
            # V4L2 by name, because a GStreamer-built OpenCV may otherwise pick GStreamer for
            # an index, and a driver's queue of old frames is the opposite of a picture of now
            cap = cv2.VideoCapture(index, cv2.CAP_V4L2)
            with contextlib.suppress(Exception):
                cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        if not cap.isOpened():
            with contextlib.suppress(Exception):
                cap.release()
            reason = f"{self.source} did not open"
            if gstreamer and _gstreamer_built_in(cv2) is False:
                # named, because the usual culprit is pip's opencv-python, which pip installs
                # with ultralytics and puts ahead of JetPack's on this python's path
                where = getattr(cv2, "__file__", None)
                loaded = f" ({os.path.dirname(where)})" if where else ""
                reason += (
                    f": the OpenCV this python loads{loaded} was built without GStreamer, as "
                    "pip's opencv-python is, and a CSI camera needs JetPack's own. pip installs "
                    "opencv-python along with ultralytics; remove it with: pip uninstall "
                    "opencv-python"
                )
            elif spec == "csi":
                reason += ": is nvargus-daemon running and a camera on the connector?"
            raise CameraUnavailable(reason)
        self._cap = cap
        self.size = size
        try:
            _, self.size = self.jpeg()
        except Exception as e:
            self.close()
            raise CameraUnavailable(f"{self.source} opened but gave no frame: {e}") from e
        except BaseException:  # a stop while it reads (see _Stopped) still lets go of it
            self.close()
            raise

    def jpeg(self) -> tuple[bytes, tuple[int, int]]:
        cv2 = self._cv2
        ok, frame = self._cap.read()
        if not ok or frame is None:
            raise RuntimeError("the camera returned no frame")
        height, width = frame.shape[:2]
        size = fit_within(width, height, self.box)
        if size != (width, height):
            frame = cv2.resize(frame, size, interpolation=cv2.INTER_AREA)
        ok, buf = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), JPEG_QUALITY])
        if not ok:
            raise RuntimeError("cv2 could not encode the frame as JPEG")
        self.size = size
        return bytes(buf.tobytes()), size

    def close(self) -> None:
        # An argus client that dies holding the sensor has been reported to leave nvargus-daemon
        # needing a restart, so the capture is released on the way out, not left to exit.
        with contextlib.suppress(Exception):
            self._cap.release()


#: The scene `--camera fake` paints: the colours and horizon quackd's own simulator uses,
#: copied rather than imported, because nothing on the board may import quackd. An orange ball
#: on a pale floor under a pale sky is what its colour detector is tuned for, so the fake
#: exercises that detector as well as the plumbing. It exercises no camera: the frame is
#: synthesised, so `Cv2Camera`'s capture, resize and encode never run under it.
FAKE_SKY = (204, 222, 240)
FAKE_FLOOR = (236, 229, 212)
FAKE_BALL = (255, 140, 0)
FAKE_HORIZON = 0.45


class FakeCamera:
    """A view with a ball rolling across the floor, so the chain works with no camera.

    Pillow paints it when Pillow is there. Without Pillow it serves a 2 by 2 grey JPEG, which
    is enough to prove the plumbing and honestly shows nothing, and OpenCV is never needed."""

    source = "fake"

    def __init__(self, size: tuple[int, int] = DEFAULT_SIZE) -> None:
        self.box = size
        self.n = 0
        try:
            import PIL.Image  # noqa: F401  (only asking whether it is there)

            self.size = size
        except ImportError:
            self.size = (2, 2)

    def jpeg(self) -> tuple[bytes, tuple[int, int]]:
        self.n += 1
        try:
            from PIL import Image, ImageDraw
        except ImportError:
            return _TINY_JPEG, (2, 2)
        width, height = self.box
        img = Image.new("RGB", (width, height), FAKE_SKY)
        draw = ImageDraw.Draw(img)
        horizon = int(height * FAKE_HORIZON)
        draw.rectangle((0, horizon, width, height), fill=FAKE_FLOOR)
        # the ball sits ON the floor, well below the horizon, because the colour detector
        # reads distance from where a blob meets the ground
        radius = max(4, min(width, height) // 12)
        ground = horizon + (height - horizon) // 2
        cx = radius + (self.n * 3) % max(1, width - 2 * radius)
        draw.ellipse((cx - radius, ground - 2 * radius, cx + radius, ground), fill=FAKE_BALL)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=JPEG_QUALITY)
        return buf.getvalue(), (width, height)

    def close(self) -> None:
        pass


def capture_loop(store: FrameStore, source: Any, fps: float, stop: threading.Event) -> None:
    """Capture on a timer rather than on request, so a slow client can never stall it."""
    period = 1.0 / max(0.1, fps)
    while not stop.is_set():
        started = time.monotonic()
        try:
            jpeg, size = source.jpeg()
            store.put(jpeg, size, now=time.monotonic())
        except Exception as e:  # a camera hiccup must not kill the server
            # redacted, like every reason that may quote the pipeline: /healthz serves this
            said = redact(str(e))
            store.fail(said)
            log.warning("capture failed: %s", said)
        stop.wait(max(0.0, period - (time.monotonic() - started)))


def open_camera(spec: str, size: tuple[int, int]) -> tuple[Any | None, str | None]:
    """The camera `--camera` asked for and None, or None and why it did not open, or None and
    None when it asked for none. A reason means a camera was wanted and is missing, which
    `/healthz` counts against the daemon; `Hostd` says "started with --camera none" itself.

    Every reason is redacted, because the spec may be a pipeline with a password in it and an
    OpenCV error may quote the spec it was given."""
    if spec == "none":
        return None, None
    if spec == "fake":
        return FakeCamera(size), None
    try:
        return Cv2Camera(spec, size), None
    except CameraUnavailable as e:
        return None, redact(str(e))
    except Exception as e:  # an OpenCV that throws while opening is still just no camera
        return None, redact(f"--camera {spec} did not open: {e}")


# ── what the GPU is for ─────────────────────────────────────────────────────────────────


class EngineUnavailable(RuntimeError):
    """A detector that could not start, with the reason `/hello` should give."""


class NotAnImage(ValueError):
    """A body this daemon cannot decode as a JPEG."""


class TooManyPixels(ValueError):
    """A JPEG that decodes to more pixels than this daemon will hold."""


class YoloEngine:
    """ultralytics YOLO on the board's GPU, or its CPU when torch cannot see a GPU.

    It reports boxes in the pixels of the image it was sent and the model's own class names,
    and maps nothing: quackd applies its own label table and its own geometry, so the laptop's
    `YoloDetector` and this engine agree on what a box means.

    One warm-up prediction runs at start, so the first real `/detect` does not pay for CUDA
    initialisation inside somebody's steering loop, and a model that cannot predict at all is
    refused at start with the reason. Calls into the model are serialised with a lock: one GPU
    runs one of these at a time anyway, and a queue is fairer than a contended model."""

    def __init__(self, model: str = DEFAULT_YOLO_MODEL, conf: float = DEFAULT_CONF) -> None:
        try:
            from ultralytics import YOLO
        except ImportError as e:
            raise EngineUnavailable(f"ultralytics is not installed on this board ({e})") from e
        try:
            from PIL import Image
        except ImportError as e:
            raise EngineUnavailable(f"Pillow is not installed on this board ({e})") from e
        self._image: Any = Image
        try:
            self.decode(_TINY_JPEG)
        except Exception as e:
            # an old Pillow without Image.open(formats=) would otherwise turn every /detect
            # into a 400 about the image, which is a lie about the image
            raise EngineUnavailable(
                f"this board's Pillow cannot decode a JPEG the way /detect does: {e}"
            ) from e
        cuda = False
        try:
            import torch

            cuda = bool(torch.cuda.is_available())
        except Exception as e:
            # ultralytics cannot run without torch, so this is mostly a torch that raised
            # while probing CUDA; the CPU is still an honest answer, and /hello will say cpu
            log.warning("torch could not say whether CUDA is available (%s); using the CPU", e)
        self.device = "cuda" if cuda else "cpu"
        self._device_arg = "cuda:0" if cuda else "cpu"
        self.model = model
        self.conf = conf
        self._lock = threading.Lock()
        self._stats_lock = threading.Lock()
        self.calls = 0
        self.errors = 0
        self.last_error: str | None = None
        self.last_ms: float | None = None
        self._last_ok = True
        try:
            self._yolo: Any = YOLO(model)
        except Exception as e:
            raise EngineUnavailable(f"{model} did not load: {e}") from e
        self.names = _class_names(getattr(self._yolo, "names", None))
        try:
            with self._lock:
                self._predict(Image.new("RGB", (64, 64)), conf)
        except Exception as e:
            raise EngineUnavailable(
                f"{model} loaded but its first prediction on {self.device} failed: {e}"
            ) from e

    def _predict(self, image: Any, conf: float) -> list[dict[str, Any]]:
        """Boxes the way `quackd/perception/yolo.py` reads them, in pixels, labels unmapped."""
        results = self._yolo.predict(image, conf=conf, verbose=False, device=self._device_arg)
        boxes: list[dict[str, Any]] = []
        for res in results:
            names = res.names
            for box in res.boxes:
                x1, y1, x2, y2 = (float(v) for v in box.xyxy[0])
                boxes.append(
                    {
                        "name": str(names[int(box.cls)]),
                        "conf": round(float(box.conf), 4),
                        "x1": round(x1, 2),
                        "y1": round(y1, 2),
                        "x2": round(x2, 2),
                        "y2": round(y2, 2),
                    }
                )
        return boxes

    def decode(self, jpeg: bytes) -> Any:
        """A JPEG and only a JPEG. Pillow opens dozens of formats, some through external
        programs, and a network port that takes any of them is a larger surface than one
        that takes what quackd actually sends."""
        try:
            image = self._image.open(io.BytesIO(jpeg), formats=["JPEG"])
            width, height = image.size
        except Exception as e:
            raise NotAnImage(str(e)) from e
        if width * height > MAX_DETECT_PIXELS:
            raise TooManyPixels(
                f"the image is {width}x{height}, over this daemon's limit of "
                f"{MAX_DETECT_PIXELS} pixels"
            )
        try:
            return image.convert("RGB")
        except Exception as e:
            raise NotAnImage(str(e)) from e

    def detect(self, jpeg: bytes, conf: float | None = None) -> dict[str, Any]:
        """Decode outside the lock, predict inside it. `ms` is the time in the model, so the
        client can subtract it from its own round trip and see what the network costs."""
        image = self.decode(jpeg)
        width, height = image.size
        floor = self.conf if conf is None else conf
        with self._lock:
            started = time.perf_counter()
            try:
                boxes = self._predict(image, floor)
            except Exception as e:
                self._record(None, f"{type(e).__name__}: {e}")
                raise
            ms = round((time.perf_counter() - started) * 1000.0, 1)
        self._record(ms, None)
        return {
            "ok": True,
            "w": width,
            "h": height,
            "boxes": boxes,
            "model": self.model,
            "device": self.device,
            "ms": ms,
        }

    def _record(self, ms: float | None, error: str | None) -> None:
        with self._stats_lock:
            self.calls += 1
            self._last_ok = error is None
            if error is None:
                self.last_ms = ms
            else:
                self.errors += 1
                self.last_error = error

    def describe(self) -> dict[str, Any]:
        return {"model": self.model, "device": self.device, "conf": self.conf, "names": self.names}

    def health(self) -> dict[str, Any]:
        with self._stats_lock:
            return {
                "ok": self._last_ok,
                "calls": self.calls,
                "errors": self.errors,
                "last_error": self.last_error,
                "last_ms": self.last_ms,
                "device": self.device,
            }


def _class_names(names: Any) -> list[str]:
    """ultralytics keeps class names as `{index: name}`; the wire carries them in index order."""
    try:
        if isinstance(names, dict):
            return [str(names[k]) for k in sorted(names)]
        return [str(n) for n in names or ()]
    except Exception:
        return []


def open_engine(enabled: bool, model: str, conf: float) -> tuple[YoloEngine | None, str | None]:
    """The detector and None, or None and why it did not start, or None and None when it was
    not asked for, the same shape as `open_camera` and for the same reason."""
    if not enabled:
        return None, None
    try:
        return YoloEngine(model, conf), None
    except EngineUnavailable as e:
        return None, str(e)
    except Exception as e:
        return None, f"the detector did not start: {type(e).__name__}: {e}"


# ── the board's own facts ───────────────────────────────────────────────────────────────


def _text(raw: bytes) -> str:
    """Bytes as the wire carries them: utf-8 whatever they were, capped, and with no NULs,
    because the device tree's own strings are NUL terminated and a NUL in a JSON string
    reaches a terminal cell on the other side.

    The cap is on the JSON the value becomes, the way `_json` writes it: a replaced byte or a
    control character is a six byte escape there, so a file capped only as it was read could
    reach six times the cap on the wire. A board's own files are short plain text, which
    this never trims."""
    text = raw[:FILE_CAP_BYTES].decode("utf-8", errors="replace").replace("\x00", "")
    if _wire_len(text) <= FILE_CAP_BYTES:
        return text
    fits, over = 0, len(text)  # the longest prefix that fits, by bisection
    while over - fits > 1:
        mid = (fits + over) // 2
        if _wire_len(text[:mid]) <= FILE_CAP_BYTES:
            fits = mid
        else:
            over = mid
    return text[:fits]


def _wire_len(text: str) -> int:
    """The bytes `text` takes inside a JSON string as `_json` writes one, quotes left out."""
    return len(json.dumps(text)) - 2


def on_board(root: str, path: str) -> str:
    """`path` as the board sees it, under `root`, so a test can hand in a tree it built."""
    return os.path.join(root, path.lstrip("/"))


def read_board_file(root: str, path: str) -> tuple[str | None, str | None]:
    """(text, None) or (None, why). Never raises: a missing file is a fact about the board."""
    try:
        with open(on_board(root, path), "rb") as fh:
            return _text(fh.read(FILE_CAP_BYTES)), None
    except OSError as e:
        return None, e.strerror or type(e).__name__
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"


def node_exists(root: str, node: str) -> bool:
    """Being refused a look at a device node is reported as no node, never as a crash."""
    try:
        return os.path.exists(on_board(root, node))
    except Exception:
        return False


def run_quiet(
    argv: Any, timeout_s: float, *, first_line: bool = False
) -> tuple[str | None, str | None]:
    """(stdout, None) for a command that answered, or (None, why). Never raises.

    `shutil.which` first, so a binary that is not there is never forked, stdin is closed so
    nothing can sit waiting on a terminal, and every command has a hard timeout.

    `first_line` is for `tegrastats`, which prints a line per interval forever: it starts the
    process, reads one line on a helper thread, and kills and reaps the process whatever
    happened, so a board is never left with a stray `tegrastats` per `/board` request."""
    try:
        args = [str(part) for part in argv]
        exe = shutil.which(args[0]) if args else None
    except Exception as e:
        return None, f"could not run: {e}"
    if not args:
        return None, "no command"
    if exe is None:
        return None, "not on PATH"
    if first_line:
        return _first_line([exe, *args[1:]], timeout_s)
    try:
        done = subprocess.run(
            [exe, *args[1:]],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=timeout_s,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return None, f"no answer within {timeout_s:g}s"
    except Exception as e:
        return None, f"could not run: {e}"
    if done.returncode != 0:
        said = _text(done.stderr or b"").strip().splitlines()
        return None, f"exited {done.returncode}" + (f": {said[0]}" if said else "")
    return _text(done.stdout or b""), None


def _first_line(argv: list[str], timeout_s: float) -> tuple[str | None, str | None]:
    try:
        proc = subprocess.Popen(
            argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL
        )
    except Exception as e:
        return None, f"could not run: {e}"
    got: list[bytes] = []

    def read_one() -> None:
        with contextlib.suppress(Exception):
            if proc.stdout is not None:
                got.append(proc.stdout.readline(FILE_CAP_BYTES))

    reader = threading.Thread(target=read_one, name="quackd-first-line", daemon=True)
    timed_out = True
    try:
        reader.start()
        reader.join(timeout_s)
        # asked before the kill, because the kill is itself an EOF the reader will then see
        timed_out = reader.is_alive()
    except Exception as e:
        return None, f"could not read it: {e}"
    finally:
        _reap(proc)
    # the process is dead, so its end of the pipe is closed and a blocked read sees EOF
    reader.join(1.0)
    if not reader.is_alive() and proc.stdout is not None:
        # never while a read is still blocked: closing a buffered pipe another thread is
        # reading waits on that thread's lock, which would hang this request instead
        with contextlib.suppress(Exception):
            proc.stdout.close()
    line = got[0] if got else b""
    if line:
        return _text(line).rstrip("\r\n"), None
    if timed_out:
        return None, f"no line within {timeout_s:g}s"
    return None, f"exited {proc.returncode} before printing a line"


def _reap(proc: Any) -> None:
    with contextlib.suppress(Exception):
        proc.kill()
    with contextlib.suppress(Exception):
        proc.wait(timeout=5.0)


_BOARD_LOCK = threading.Lock()
"""`/board` forks two commands, and two callers at once must not fork four."""


def board_dump(root: str = "/") -> dict[str, Any]:
    """The five files, the three nodes and the two commands, raw, and why any of them is null.

    `run_quiet` is looked up when this runs, not bound as a default, so a test can replace it
    and a board-root test never depends on what the machine running it has on its PATH."""
    files: dict[str, str | None] = {}
    nodes: dict[str, bool] = {}
    commands: dict[str, str | None] = {}
    errors: dict[str, str] = {}
    with _BOARD_LOCK:
        for path in BOARD_FILES:
            text, why = read_board_file(root, path)
            files[path] = text
            if text is None:
                errors[path] = why or "could not be read"
        for node in GPU_NODES:
            nodes[node] = node_exists(root, node)
        for name, (argv, timeout_s, first_line) in COMMANDS.items():
            try:
                out, why = run_quiet(argv, timeout_s, first_line=first_line)
            except Exception as e:  # run_quiet does not raise; a replacement might
                out, why = None, f"could not run: {e}"
            commands[name] = out
            if out is None:
                errors[name] = why or "no output"
    return {"files": files, "nodes": nodes, "commands": commands, "errors": errors}


def tegra_facts(root: str) -> tuple[bool, str | None]:
    """Whether this is a Tegra, asked the two ways `quackd doctor` asks, and its model name.

    False on a laptop, honestly: `--camera fake` on a workstation is a daemon with no board."""
    compatible, _ = read_board_file(root, "/proc/device-tree/compatible")
    model, _ = read_board_file(root, "/proc/device-tree/model")
    tegra = "nvidia,tegra" in (compatible or "") or node_exists(root, "/etc/nv_tegra_release")
    return tegra, (model or "").strip() or None


# ── one daemon ──────────────────────────────────────────────────────────────────────────


class Hostd:
    """What one daemon opened, what failed to open and why, and the token it checks.

    A `camera_error` or `detect_error` passed in means that thing was asked for and did not
    start. Leaving one out means nobody asked for it, and the daemon says "started with
    --camera none" or "started with --no-detect" in its place."""

    def __init__(
        self,
        *,
        camera: Any | None = None,
        camera_error: str | None = None,
        fps: float = DEFAULT_FPS,
        fov_deg: float | None = None,
        engine: YoloEngine | None = None,
        detect_error: str | None = None,
        token: str | None = None,
        board_root: str = "/",
    ) -> None:
        self.camera = camera
        self.camera_failed = camera is None and camera_error is not None
        self.camera_error = (
            None if camera is not None else camera_error or "started with --camera none"
        )
        self.fps = fps
        self.fov_deg = fov_deg
        self.engine = engine
        self.detect_failed = engine is None and detect_error is not None
        self.detect_error = (
            None if engine is not None else detect_error or "started with --no-detect"
        )
        # An empty string is no secret: it would admit any client that sent an empty header,
        # which is a check in name only. The protocol makes it no token, and so does this.
        self.token = token or None
        self.board_root = board_root
        self.detect_slots = threading.BoundedSemaphore(DETECT_SLOTS)
        self.store = FrameStore()
        self.stale_after = stale_after_s(fps)
        self.started = time.monotonic()
        self._stop = threading.Event()
        self._capture: threading.Thread | None = None

    @classmethod
    def from_args(cls, args: argparse.Namespace, token: str | None) -> Hostd:
        """The detector first and the camera second, so the camera is not held through the
        model's load, which on a first start includes downloading it. A stop that comes while
        this runs (`_Stopped`, or Ctrl+C) closes the camera if it had opened."""
        if not args.no_detect:
            log.info("loading %s", args.yolo_model)
        engine, detect_error = open_engine(not args.no_detect, args.yolo_model, args.conf)
        if engine is None and not args.no_detect:
            log.warning("no detector: %s", detect_error)
        camera, camera_error = open_camera(args.camera, args.size)
        try:
            if camera is None and args.camera != "none":
                log.warning("no camera: %s", camera_error)
            return cls(
                camera=camera,
                camera_error=camera_error,
                fps=args.fps,
                fov_deg=args.fov_deg,
                engine=engine,
                detect_error=detect_error,
                token=token,
                board_root=args.board_root,
            )
        except BaseException:
            if camera is not None:
                camera.close()
            raise

    def start(self) -> None:
        if self.camera is None or self._capture is not None:
            return
        self._capture = threading.Thread(
            target=capture_loop,
            args=(self.store, self.camera, self.fps, self._stop),
            name="quackd-capture",
            daemon=True,
        )
        self._capture.start()

    def stop(self) -> None:
        self._stop.set()
        capture = self._capture
        if capture is not None:
            capture.join(2.0)
            if capture.is_alive():
                return  # still inside a read; releasing under it can crash OpenCV
        if self.camera is not None:
            self.camera.close()

    def authorised(self, given: str | None) -> bool:
        if self.token is None:
            return True
        if given is None:
            return False
        return hmac.compare_digest(given.encode("utf-8"), self.token.encode("utf-8"))

    def hello(self) -> dict[str, Any]:
        tegra, board_model = tegra_facts(self.board_root)
        camera = None
        if self.camera is not None:
            camera = {
                "source": self.camera.source,
                "fov_deg": self.fov_deg,
                "size": list(self.camera.size),
                "fps": self.fps,
            }
        return {
            "ok": True,
            "protocol": PROTOCOL,
            "protocol_version": PROTOCOL_VERSION,
            "daemon_version": HOSTD_VERSION,
            "hostname": socket.gethostname(),
            "python": platform.python_version(),
            "capabilities": {
                "camera": self.camera is not None,
                "detect": self.engine is not None,
                "tegra": tegra,
            },
            "camera": camera,
            "camera_error": self.camera_error,
            "detect": self.engine.describe() if self.engine is not None else None,
            "detect_error": self.detect_error,
            "board_model": board_model,
        }

    def health(self) -> dict[str, Any]:
        """`ok` covers everything this daemon was asked to run: the camera's newest frame is
        fresh, the detector's last call worked, and neither was asked for and failed to start.

        The last is the case the unit's own comment warns about: `--camera csi` racing
        nvargus-daemon at boot leaves the daemon up with no camera, and a health check that
        said ok there would hide it from everything but `/hello`. The daemon keeps serving
        rather than exit for systemd to restart it, because a board with no camera on the
        connector at all would then restart forever, five seconds apart, loading the model
        each time. What nobody asked for is not held against it: a daemon started with
        `--camera none` or `--no-detect` is missing nothing it was asked for. A board without
        ultralytics started without `--no-detect` is not ok until it has one or the other,
        because it was asked for a detector it cannot run and the fix is one flag."""
        now = time.monotonic()
        camera = None
        sick: list[str] = []
        if self.camera_failed:
            sick.append(f"no camera: {self.camera_error}")
        if self.detect_failed:
            sick.append(f"no detector: {self.detect_error}")
        if self.camera is not None:
            camera = self.store.health(now=now, stale_after=self.stale_after)
            if not camera["ok"]:
                if camera["age_s"] is None:
                    said = "the camera has not captured a frame yet"
                else:
                    said = (
                        f"the camera's last frame is {camera['age_s']:.1f}s old "
                        f"(stale after {self.stale_after:.1f}s)"
                    )
                if camera["last_error"]:
                    said += f": {camera['last_error']}"
                sick.append(said)
        detect = None
        if self.engine is not None:
            detect = self.engine.health()
            if not detect["ok"]:
                sick.append(f"the detector's last call failed: {detect['last_error']}")
        body: dict[str, Any] = {
            "ok": not sick,
            "daemon_version": HOSTD_VERSION,
            "uptime_s": round(now - self.started, 1),
            "camera": camera,
            "detect": detect,
        }
        if sick:
            body["reason"] = "; ".join(sick)
        return body

    def board(self) -> dict[str, Any]:
        return {"ok": True, **board_dump(self.board_root)}


# ── the server ──────────────────────────────────────────────────────────────────────────


def _conf_from(query: str) -> float | None:
    values = parse_qs(query).get("conf")
    if not values:
        return None
    try:
        conf = float(values[-1])
    except ValueError:
        conf = -1.0
    if not 0.0 < conf <= 1.0:
        raise ValueError(f"conf must be a number above 0 and at most 1, not {values[-1]!r}")
    return conf


class _Cutoff(TimeoutError):
    """A request cut off for taking too long or growing too large. A TimeoutError, because that
    is what http.server treats as "discard this connection", without a traceback."""


class _Reader(io.RawIOBase):
    """The socket as a request reads it, with one deadline and one allowance per request.

    The handler's timeout bounds each wait for bytes, which a client sending a byte every few
    seconds never trips, and http.server reads a head of over 6 MB before this daemon's code
    runs, so a client with no token could hold a thread and all it had sent for as long as it
    liked. So a request has to arrive whole by `REQUEST_DEADLINE_S`, and may send a head of
    `MAX_HEAD_BYTES` and whatever body the handler chose to read, and nothing more."""

    def __init__(self, sock: socket.socket, timeout: float | None) -> None:
        super().__init__()
        self._sock = sock
        self._timeout = timeout
        self._deadline = 0.0
        self._allowed = 0

    def begin(self) -> None:
        """A new request: a fresh deadline, and room for a head and no body."""
        self._deadline = time.monotonic() + REQUEST_DEADLINE_S
        self._allowed = MAX_HEAD_BYTES

    def allow(self, n: int) -> None:
        """Room for `n` more bytes: a body the handler has decided to read."""
        self._allowed += n

    def readable(self) -> bool:
        return True

    def readinto(self, buffer: Any) -> int:
        left = self._deadline - time.monotonic()
        if left <= 0:
            raise _Cutoff(f"the request did not arrive whole within {REQUEST_DEADLINE_S:g}s")
        if self._allowed <= 0:
            raise _Cutoff("the request is longer than this daemon reads")
        self._sock.settimeout(left if self._timeout is None else min(self._timeout, left))
        try:
            got = self._sock.recv_into(memoryview(buffer)[: self._allowed])
        finally:
            self._sock.settimeout(self._timeout)
        self._allowed -= got
        return got


def _linger(sock: socket.socket) -> None:
    """Close a connection whose request body was left unread without resetting the reply away.

    Closing a socket with unread bytes in it makes the kernel send a reset rather than an end
    of stream, and a client that has not read the reply yet can lose it to that reset: quackd
    sending a frame with the wrong token would hear that the connection was reset rather than
    that the token was wrong. So the reply is followed by an end of stream, and what the client
    still sends is read a chunk at a time and dropped until it closes, for `LINGER_S` at most."""
    end = time.monotonic() + LINGER_S
    with contextlib.suppress(OSError):
        sock.shutdown(socket.SHUT_WR)
        while (left := end - time.monotonic()) > 0:
            sock.settimeout(left)
            if not sock.recv(DRAIN_CHUNK_BYTES):
                return


def make_handler(daemon: Hostd) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = f"quackd-jetson-hostd/{HOSTD_VERSION}"
        protocol_version = "HTTP/1.1"
        # ThreadingHTTPServer gives every connection a thread and HTTP/1.1 keeps them alive,
        # so without this one half-open connection pins a thread for the life of the process,
        # on a board that may also be running a robot's fifty hertz control loop.
        timeout = REQUEST_TIMEOUT_S
        _consumed = False
        _linger_on_close = False
        _reader: _Reader

        def setup(self) -> None:
            super().setup()
            # every read of a request goes through one deadline and one allowance (`_Reader`)
            self.rfile.close()
            self._reader = _Reader(self.connection, self.timeout)
            self.rfile = io.BufferedReader(self._reader)

        def handle_one_request(self) -> None:
            self._consumed = False
            self._linger_on_close = False
            self._reader.begin()
            super().handle_one_request()

        def finish(self) -> None:
            super().finish()
            if self._linger_on_close:
                _linger(self.connection)

        def do_GET(self) -> None:
            self._route("GET")

        def do_POST(self) -> None:
            self._route("POST")

        def send_error(
            self, code: int, message: str | None = None, explain: str | None = None
        ) -> None:
            """Every refusal as JSON, including the ones http.server makes by itself.

            http.server answers a malformed request, an oversized header and any verb without
            a `do_` method with an HTML page. The protocol promises JSON with a reason, and a
            verb this daemon does not answer on a path it has is a 405: so that one goes
            through the router, which checks the token first like every other request."""
            if code == HTTPStatus.NOT_IMPLEMENTED and self.command:
                self._route(self.command)
                return
            reason = message or "the request could not be read"
            self._json(code, {"ok": False, "reason": reason}, close=True)

        def _route(self, method: str) -> None:
            self._consumed = False
            path, _, query = self.path.partition("?")
            if not daemon.authorised(self.headers.get(TOKEN_HEADER)):
                # answered on the head alone and closed: a client without the token is never
                # waited on for its body, and nothing it sends is kept (see _linger)
                self._json(401, {"ok": False, "reason": "bad or missing token"}, close=True)
                return
            if path in GET_PATHS:
                if method != "GET":
                    self._not_allowed(method, path, "GET")
                elif path == HELLO_PATH:
                    self._json(200, daemon.hello())
                elif path == HEALTH_PATH:
                    self._json(200, daemon.health())
                elif path == BOARD_PATH:
                    self._json(200, daemon.board())
                else:
                    self._snapshot()
                return
            if path in POST_PATHS:
                if method != "POST":
                    self._not_allowed(method, path, "POST")
                else:
                    self._detect(query)
                return
            self._json(404, {"ok": False, "reason": f"nothing at {path}"})

        def _not_allowed(self, method: str, path: str, allowed: str) -> None:
            reason = f"{path} answers {allowed}, not {method}"
            self._json(405, {"ok": False, "reason": reason}, allow=allowed)

        def _snapshot(self) -> None:
            if daemon.camera is None:
                reason = f"no camera on this host: {daemon.camera_error}"
                self._json(503, {"ok": False, "reason": reason})
                return
            jpeg, at = daemon.store.get()
            if jpeg is None:
                self._json(503, {"ok": False, "reason": "no frame captured yet"})
                return
            # The Open Duck camera daemon once read this timestamp and threw it away, so after
            # one capture it could never 503 again: a camera that stopped went on being served
            # as a picture of now, and `go_to` steered on a photograph.
            age = time.monotonic() - at
            if age > daemon.stale_after:
                self._json(
                    503,
                    {
                        "ok": False,
                        "reason": f"the last frame is {age:.1f}s old (stale after "
                        f"{daemon.stale_after:.1f}s); the camera has stopped",
                        "age_s": round(age, 2),
                    },
                )
                return
            self._bytes(200, "image/jpeg", jpeg, age=age)

        def _detect(self, query: str) -> None:
            engine = daemon.engine
            if engine is None:
                reason = f"detection is not available: {daemon.detect_error}"
                self._json(503, {"ok": False, "reason": reason})
                return
            try:
                conf = _conf_from(query)
            except ValueError as e:
                self._json(400, {"ok": False, "reason": str(e)})
                return
            declared = self.headers.get("Content-Length")
            if declared is None:
                reason = f"POST {DETECT_PATH} needs a Content-Length"
                self._json(411, {"ok": False, "reason": reason})
                return
            try:
                length = int(declared)
            except ValueError:
                length = -1
            if length < 0:
                reason = f"Content-Length {declared!r} is not a byte count"
                self._json(400, {"ok": False, "reason": reason})
                return
            if length > MAX_JPEG_BYTES:
                reason = (
                    f"the body is {length} bytes and this daemon takes at most "
                    f"{MAX_JPEG_BYTES} (8 MB)"
                )
                self._json(413, {"ok": False, "reason": reason})
                return
            # the slot is taken before the body is read, so a request queued for the model
            # holds neither its body nor its decoded image (see DETECT_SLOTS)
            if not daemon.detect_slots.acquire(timeout=DETECT_WAIT_S):
                reason = f"busy: {DETECT_SLOTS} detections are already under way; try again"
                self._json(503, {"ok": False, "reason": reason}, close=True)
                return
            try:
                self._detect_body(engine, length, conf)
            finally:
                daemon.detect_slots.release()

        def _detect_body(self, engine: YoloEngine, length: int, conf: float | None) -> None:
            self._reader.allow(length)
            try:
                body = self.rfile.read(length)
            except OSError:
                self.close_connection = True  # the client went away or went quiet
                return
            self._consumed = True
            if len(body) < length:
                reason = f"the body ended after {len(body)} of {length} bytes"
                self._json(400, {"ok": False, "reason": reason}, close=True)
                return
            try:
                found = engine.detect(body, conf)
            except NotAnImage:
                reason = "the body is not an image this daemon can decode"
                self._json(400, {"ok": False, "reason": reason})
                return
            except TooManyPixels as e:
                self._json(413, {"ok": False, "reason": str(e)})
                return
            except Exception as e:
                reason = f"detection failed: {type(e).__name__}: {e}"
                self._json(500, {"ok": False, "reason": reason})
                return
            self._json(200, found)

        def _settle_body(self) -> None:
            """Leave the connection at the start of the next request, or mark it to close.

            A reply that did not read the body must still get it off the wire: a keep-alive
            connection would parse it as the next request, and closing a socket with unread
            bytes makes the kernel reset it, which can throw away the reply before the client
            reads it. So a body within the cap is read a chunk at a time and dropped, within
            the request's deadline, and anything else closes and lingers (`_linger`)."""
            if self._consumed:
                return
            self._consumed = True
            declared = self.headers.get("Content-Length")
            if declared is None:
                if self.headers.get("Transfer-Encoding"):
                    self.close_connection = self._linger_on_close = True
                return
            try:
                length = int(declared)
            except ValueError:
                self.close_connection = self._linger_on_close = True
                return
            if length < 0 or length > MAX_JPEG_BYTES:
                self.close_connection = self._linger_on_close = True
                return
            self._reader.allow(length)
            try:
                while length > 0 and (chunk := self.rfile.read(min(length, DRAIN_CHUNK_BYTES))):
                    length -= len(chunk)
            except OSError:
                self.close_connection = True

        def _bytes(
            self,
            code: int,
            content_type: str,
            payload: bytes,
            *,
            age: float | None = None,
            allow: str | None = None,
            close: bool = False,
        ) -> None:
            if close:
                self.close_connection = True
                self._linger_on_close = not self._consumed
            else:
                self._settle_body()
            try:
                self.send_response(code)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(payload)))
                self.send_header("Cache-Control", "no-store")
                if age is not None:
                    # Age is the standard header; the float one is what quackd actually reads,
                    # because whole seconds cannot express a 200 ms frame.
                    self.send_header("Age", str(int(age)))
                    self.send_header("X-Frame-Age", f"{age:.3f}")
                if allow is not None:
                    self.send_header("Allow", allow)
                if self.close_connection:
                    self.send_header("Connection", "close")
                self.end_headers()
                if self.command != "HEAD":
                    self.wfile.write(payload)
            except OSError:
                self.close_connection = True  # the client hung up; nothing to tell it

        def _json(
            self,
            code: int,
            body: dict[str, Any],
            *,
            allow: str | None = None,
            close: bool = False,
        ) -> None:
            payload = json.dumps(body).encode("utf-8")
            self._bytes(code, "application/json", payload, allow=allow, close=close)

        def log_message(self, fmt: str, *args: Any) -> None:
            log.debug("%s %s", self.address_string(), fmt % args)

    return Handler


def _refuse_busy(sock: socket.socket) -> None:
    """A 503 written straight to a connection there is no thread for, never waiting on it: this
    runs on the accept loop, which a client that does not read must not be able to stall."""
    reason = f"busy: this daemon is already serving {MAX_CONNECTIONS} connections; try again"
    body = json.dumps({"ok": False, "reason": reason}).encode("utf-8")
    head = (
        "HTTP/1.1 503 Service Unavailable\r\n"
        "Content-Type: application/json\r\n"
        f"Content-Length: {len(body)}\r\n"
        "Cache-Control: no-store\r\n"
        "Connection: close\r\n\r\n"
    )
    with contextlib.suppress(OSError):
        sock.setblocking(False)
        sock.send(head.encode("ascii") + body)


class _Server(ThreadingHTTPServer):
    """ThreadingHTTPServer with at most `MAX_CONNECTIONS` connections, and so threads, at once."""

    daemon_threads = True
    # SO_REUSEADDR lets a restart bind past the last run's TIME_WAIT on Linux. On Windows it
    # lets a second server bind a port another one is listening on, so a daemon started on a
    # busy port there would say it was serving while every request went to the other one.
    allow_reuse_address = sys.platform != "win32"

    def __init__(self, address: Any, handler: Any) -> None:
        self._connections = threading.BoundedSemaphore(MAX_CONNECTIONS)
        super().__init__(address, handler)

    def verify_request(self, conn: Any, client_address: Any) -> bool:
        if self._connections.acquire(blocking=False):
            return True
        _refuse_busy(conn)
        return False  # and socketserver closes it

    def process_request(self, conn: Any, client_address: Any) -> None:
        try:
            super().process_request(conn, client_address)
        except BaseException:
            self._connections.release()  # no thread started, so none will release it
            raise

    def process_request_thread(self, conn: Any, client_address: Any) -> None:
        try:
            super().process_request_thread(conn, client_address)
        finally:
            self._connections.release()


class _Server6(_Server):
    address_family = socket.AF_INET6


def serve(daemon: Hostd, host: str, port: int) -> ThreadingHTTPServer:
    """Serve on a thread and return the server, so a caller (or a test) can shut it down."""
    server_class = _Server6 if ":" in host else _Server
    server = server_class((host, port), make_handler(daemon))
    threading.Thread(target=server.serve_forever, name="quackd-jetson-hostd", daemon=True).start()
    return server


# ── running it ──────────────────────────────────────────────────────────────────────────

LOOPBACK = ("127.0.0.1", "localhost", "::1")


class TokenFileError(RuntimeError):
    """A token file that was named and cannot be read, or holds no token."""


def read_token(path: str | None) -> str | None:
    """The token in `path`, or None if the daemon genuinely has none.

    The Open Duck bridge learned this one the hard way: its installer wrote a token the
    service user could not read, `os.path.exists()` swallowed the EACCES, and the bridge
    started with authentication silently off, which no client can tell from a daemon that
    checked the token it was sent. So a named file that cannot be read refuses to start.

    Stricter than the duck's on two points: there, a missing file is no token, because its
    `--token-file` has a default that names a file whether or not anyone created it. Here the
    flag has no default, so naming a file is asking for a token, and a missing one refuses too.
    So does an empty one. An empty token is no token, so starting on one would be the same
    silent failure by another route, and the README's `openssl rand -hex 32 | sudo install`
    writes exactly that file on a board without openssl, because a pipe succeeds when its last
    command does."""
    if not path:
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            token = fh.read().strip()
    except (OSError, UnicodeDecodeError) as e:
        raise TokenFileError(
            f"cannot read the token file {path}: {e}. The service user has to be able to read "
            "it: check the owner and mode, or point --token-file somewhere it can. Refusing "
            "to start with authentication silently disabled."
        ) from e
    if not token:
        raise TokenFileError(
            f"the token file {path} is empty. Write a token into it, or drop --token-file to "
            "run with no token on purpose. Refusing to start with authentication silently "
            "disabled."
        )
    return token


def resolve_token(args: argparse.Namespace) -> str | None:
    """`--token-file`, else `--token`, else `$QUACKD_HOST_TOKEN`. An empty `--token` or
    variable means none; an empty file refuses to start (see `read_token`)."""
    if args.token_file:
        return read_token(args.token_file)
    if args.token is not None:
        return str(args.token).strip() or None
    return os.environ.get(TOKEN_ENV, "").strip() or None


def wide_bind_warning(bind: str, token: str | None) -> str | None:
    """What to say when this is reachable from beyond the board with nothing checking who."""
    if bind in LOOPBACK or token:
        return None
    return (
        f"binding {bind} with no token: this serves a live view of wherever this board is, "
        "and a GPU that anyone who can reach this port can keep busy, to everyone on that "
        "network. There is no control path in this process, so the worst case is a stranger "
        "watching the room and spending the board's GPU rather than moving anything, but that "
        "is worth deciding on purpose. Prefer --bind 127.0.0.1 and an ssh tunnel, or give it "
        "a --token-file."
    )


def camera_spec(text: str) -> str:
    spec = text.strip()
    if spec in ("none", "csi", "fake") or spec.isdigit() or "!" in spec:
        return spec
    raise argparse.ArgumentTypeError(
        "--camera takes none, csi, fake, a V4L2 index such as 0, or a GStreamer pipeline "
        f"(a string containing '!'), not {redact(text)!r}"
    )


def size_spec(text: str) -> tuple[int, int]:
    width, sep, height = text.lower().partition("x")
    try:
        size = (int(width), int(height))
    except ValueError:
        size = (0, 0)
    if not sep or min(size) < 1:
        raise argparse.ArgumentTypeError(f"--size takes WIDTHxHEIGHT such as 640x480, not {text!r}")
    return size


def _bounded(name: str, low: float, high: float, *, low_included: bool = False) -> Any:
    """A float type for argparse that is above `low` (or at least it, with `low_included`) and
    at most `high`, NaN refused."""
    floor = "at least" if low_included else "above"

    def parse(text: str) -> float:
        try:
            value = float(text)
        except ValueError:
            value = float("nan")
        if not (low <= value if low_included else low < value) or not value <= high:
            raise argparse.ArgumentTypeError(f"{name} must be {floor} {low:g} and at most {high:g}")
        return value

    return parse


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="quackd-jetson-hostd", description=(__doc__ or "").splitlines()[0]
    )
    p.add_argument(
        "--bind",
        default="127.0.0.1",
        help="loopback by default: this serves a live view of wherever the board is and a GPU "
        "anyone who can reach it can keep busy. Prefer an ssh tunnel over binding 0.0.0.0",
    )
    p.add_argument("--port", type=int, default=DEFAULT_PORT)
    p.add_argument(
        "--camera",
        type=camera_spec,
        default="none",
        help="none (default), csi (UNTESTED), a V4L2 index such as 0, a GStreamer pipeline "
        "ending in an appsink, or fake for a synthetic scene with no camera",
    )
    p.add_argument(
        "--fps",
        type=_bounded("--fps", MIN_FPS, 60.0, low_included=True),
        default=DEFAULT_FPS,
        help="capture rate, at least 1. go_to and search_scan steer on these frames, so a low "
        "rate is a slow visual loop, not just a stale picture",
    )
    p.add_argument(
        "--size",
        type=size_spec,
        default=DEFAULT_SIZE,
        help="the box a frame is shrunk to fit, keeping its aspect ratio (default 640x480)",
    )
    p.add_argument(
        "--fov-deg",
        type=_bounded("--fov-deg", 0.0, 179.0),
        default=None,
        help="the lens's horizontal field of view. Unset, quackd treats the lens as "
        "uncalibrated rather than guess",
    )
    p.add_argument("--yolo-model", default=DEFAULT_YOLO_MODEL)
    p.add_argument("--no-detect", action="store_true", help="do not load a detector at all")
    p.add_argument(
        "--conf",
        type=_bounded("--conf", 0.0, 1.0),
        default=DEFAULT_CONF,
        help="the confidence floor, unless a request sends ?conf=",
    )
    tokens = p.add_mutually_exclusive_group()
    tokens.add_argument(
        "--token", default=None, help=f"the token clients must send (default: ${TOKEN_ENV})"
    )
    tokens.add_argument(
        "--token-file",
        default=None,
        help="read the token from this file. Refuses to start if it is named and unreadable "
        "or empty",
    )
    p.add_argument(
        "--once", action="store_true", help="print what /hello would say, as JSON, and exit"
    )
    # Where the board's files are read from. Tests point it at a tree they built.
    p.add_argument("--board-root", default="/", help=argparse.SUPPRESS)
    return p


class _Stopped(BaseException):
    """SIGTERM while the daemon is still starting, raised from wherever the signal lands.

    Starting can take minutes: importing torch, and on a first start downloading the model. A
    handler that only set a flag would be read after all of that, and systemd kills a unit
    that has not stopped within 90 seconds, so the stop would still end in a kill, only a
    later one. A BaseException, so the `except Exception` around the camera and the detector
    lets it through to the code that closes what had opened."""


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    logging.basicConfig(
        stream=sys.stderr,
        level=logging.INFO,
        format="quackd-jetson-hostd %(levelname)s %(message)s",
    )
    stop = threading.Event()
    starting = True

    def on_term(signum: int, frame: Any) -> None:
        nonlocal starting
        stop.set()
        if starting:
            starting = False  # once: the close it unwinds into must not be cut short
            raise _Stopped

    # systemd stops a unit with SIGTERM, whose default is to die on the spot, and an argus
    # client that dies holding the sensor can leave nvargus-daemon needing a restart (see
    # Cv2Camera.close). So the handler goes in before anything opens, and a stop at any point
    # is an exit that lets go of the camera. It is put back on the way out, for a caller that
    # runs this inside a process of its own, as the tests do.
    previous: Any = None
    with contextlib.suppress(ValueError):  # not the main thread
        previous = signal.signal(signal.SIGTERM, on_term)
    try:
        daemon: Hostd | None = None
        try:
            token = resolve_token(args)
            daemon = Hostd.from_args(args, token)
            starting = False
        except TokenFileError as e:
            log.error("%s", e)
            return 2
        except (_Stopped, KeyboardInterrupt):
            if daemon is not None:  # stopped after it had opened, before it could say so
                daemon.stop()
            log.info("stopped while starting")
            return 0

        if args.once:
            try:
                sys.stdout.write(json.dumps(daemon.hello(), indent=2) + "\n")
                sys.stdout.flush()
            finally:
                daemon.stop()
            return 0

        warning = wide_bind_warning(args.bind, token)
        if warning:
            log.warning("%s", warning)

        try:
            server = serve(daemon, args.bind, args.port)
        except OSError as e:
            log.error("cannot listen on %s:%s: %s", args.bind, args.port, e)
            daemon.stop()
            return 2
        daemon.start()
        log.info(
            "serving http://%s:%s (camera: %s, detect: %s, token: %s)",
            args.bind,
            server.server_address[1],
            daemon.camera.source if daemon.camera is not None else "none",
            daemon.engine.device if daemon.engine is not None else "none",
            "required" if token else "none",
        )
        try:
            while not stop.is_set():
                time.sleep(0.5)
        except KeyboardInterrupt:
            pass
        finally:
            server.shutdown()
            server.server_close()
            daemon.stop()
        return 0
    finally:
        if previous is not None:
            signal.signal(signal.SIGTERM, previous)


if __name__ == "__main__":
    raise SystemExit(main())
