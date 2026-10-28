"""The daemon quackd ships for a Jetson, exercised with no Jetson, no camera and no GPU.

The quackd that drives a robot from a laptop reaches the board through this daemon, with
`--host`. Every test loads it by path, the way the board runs it, and drives the real server
in-process on port 0. None of
them starts it as a subprocess, because subprocess-and-sleep tests flake on this project's
Windows machine. The one process started here is a stand-in for `tegrastats`, because killing a
command that streams forever is exactly the thing under test.

Nothing here says anything about a real board: the camera, the GPU and the board's files are
all fakes, and the README beside the daemon says so.
"""

from __future__ import annotations

import argparse
import ast
import contextlib
import http.client
import importlib.util
import io
import json
import logging
import os
import shlex
import signal
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest
from PIL import Image

from tests.jetson_fixtures import (
    COMPATIBLE,
    MEMINFO,
    NUL,
    NVPMODEL_Q,
    ORIN_NANO,
    RELEASE_36_4_3,
    TEGRASTATS_LINE,
    ZRAM_SWAPS,
    tegra_tree,
    write,
)

REPO = Path(__file__).resolve().parents[1]
HOSTD = REPO / "bridge" / "jetson" / "quackd_jetson_hostd.py"
UNIT = REPO / "bridge" / "jetson" / "quackd-jetson-hostd.service"
SOURCE = HOSTD.read_text(encoding="utf-8")
TOKEN = "correct-horse-battery-staple"
FILES = (
    "/proc/device-tree/model",
    "/proc/device-tree/compatible",
    "/etc/nv_tegra_release",
    "/proc/meminfo",
    "/proc/swaps",
)
NODES = ("/dev/nvgpu/igpu0", "/dev/nvhost-ctrl-gpu", "/dev/nvidia0")


def _load(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def hostd() -> ModuleType:
    return _load(HOSTD, "quackd_jetson_hostd")


@pytest.fixture(autouse=True)
def _no_token_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """A developer with `QUACKD_HOST_TOKEN` exported must not be running a different suite."""
    monkeypatch.delenv("QUACKD_HOST_TOKEN", raising=False)


@contextlib.contextmanager
def serving(hostd: ModuleType, daemon: Any) -> Iterator[int]:
    server = hostd.serve(daemon, "127.0.0.1", 0)
    try:
        yield server.server_address[1]
    finally:
        server.shutdown()
        server.server_close()
        daemon.stop()


def call(
    port: int,
    method: str,
    path: str,
    *,
    body: bytes | None = None,
    token: str | None = None,
) -> tuple[int, dict[str, str], bytes]:
    headers = {"X-Quackd-Token": token} if token is not None else {}
    if body is not None:
        headers["Content-Type"] = "image/jpeg"
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    try:
        conn.request(method, path, body=body, headers=headers)
        resp = conn.getresponse()
        return resp.status, {k.lower(): v for k, v in resp.getheaders()}, resp.read()
    finally:
        conn.close()


def reply(port: int, method: str, path: str, **kwargs: Any) -> tuple[int, dict[str, Any]]:
    status, headers, raw = call(port, method, path, **kwargs)
    assert headers["content-type"] == "application/json", (status, headers)
    assert headers["cache-control"] == "no-store"
    body = json.loads(raw)
    assert isinstance(body.get("ok"), bool), "every JSON reply carries ok"
    if not body["ok"]:
        assert body.get("reason"), "and every failure carries a reason"
    return status, body


def post_declaring(port: int, *, length: int | None) -> tuple[int, dict[str, Any]]:
    """A POST /detect whose Content-Length says `length`, or that has none, and sends no body.

    Sending nothing is on purpose: the daemon refuses on the header and closes, and a body left
    unread in its socket would make the kernel reset the connection under the reply."""
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    try:
        conn.putrequest("POST", "/detect")
        conn.putheader("Content-Type", "image/jpeg")
        if length is not None:
            conn.putheader("Content-Length", str(length))
        conn.endheaders()
        resp = conn.getresponse()
        return resp.status, json.loads(resp.read())
    finally:
        conn.close()


def raw_reply(port: int, request: bytes) -> tuple[bytes, bytes]:
    """The head and the body of one reply, read off the socket until the daemon closes it.

    http.client never reads a body after a HEAD, whatever the server sent, so a check that the
    daemon sent none has to look at the bytes."""
    chunks: list[bytes] = []
    with socket.create_connection(("127.0.0.1", port), timeout=10) as sock:
        sock.sendall(request)
        while chunk := sock.recv(65536):
            chunks.append(chunk)
    head, _, body = b"".join(chunks).partition(b"\r\n\r\n")
    return head, body


def jpeg_of(size: tuple[int, int] = (320, 240), fmt: str = "JPEG") -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, (236, 229, 212)).save(buf, format=fmt)
    return buf.getvalue()


def wait_for_frame(store: Any, timeout: float = 5.0) -> None:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if store.get()[0] is not None:
            return
        time.sleep(0.02)
    raise AssertionError("no frame was captured in time")


def no_commands(monkeypatch: pytest.MonkeyPatch, hostd: ModuleType) -> list[tuple[Any, ...]]:
    """Replace `run_quiet`, so no test depends on what this machine has on its PATH. A Linux
    runner with a real `nvpmodel` must read the same board as a Windows laptop."""
    calls: list[tuple[Any, ...]] = []

    def run(argv: Any, timeout_s: float, *, first_line: bool = False) -> tuple[Any, Any]:
        calls.append((tuple(argv), timeout_s, first_line))
        return None, "not on PATH"

    monkeypatch.setattr(hostd, "run_quiet", run)
    return calls


# ── a fake GPU: ultralytics and torch as far as the daemon touches them ─────────────────


class _Scalar:
    """One element of a tensor, which is all `int(box.cls)` and `float(box.conf)` read."""

    def __init__(self, value: float) -> None:
        self.value = value

    def __int__(self) -> int:
        return int(self.value)

    def __float__(self) -> float:
        return float(self.value)


class _Box:
    def __init__(self, cls: int, conf: float, xyxy: list[float]) -> None:
        self.cls = _Scalar(cls)
        self.conf = _Scalar(conf)
        self.xyxy = [xyxy]


COCO = {0: "person", 15: "cat", 32: "sports ball"}
"""A few rows of ultralytics' own `{index: name}` table, out of order on purpose."""


class FakeYolo:
    """`ultralytics.YOLO`: a constructor, `names`, and a `predict` that honours `conf`.

    It sees a ball in the middle of whatever it is shown at 0.87 and a person at 0.35, so the
    default floor of 0.4 keeps one box and `?conf=0.3` keeps both."""

    def __init__(self, model: str) -> None:
        self.model = model
        self.names = {32: COCO[32], 0: COCO[0], 15: COCO[15]}
        self.calls: list[dict[str, Any]] = []
        self.fail: str | None = None

    def predict(
        self, image: Any, conf: float = 0.25, verbose: bool = True, device: Any = None
    ) -> list[Any]:
        self.calls.append(
            {
                "size": image.size,
                "mode": image.mode,
                "extrema": image.getextrema(),
                "conf": conf,
                "device": device,
                "verbose": verbose,
            }
        )
        if self.fail:
            raise RuntimeError(self.fail)
        w, h = image.size
        found = [
            _Box(32, 0.87, [0.45 * w, 0.5 * h, 0.55 * w, 0.6 * h]),
            _Box(0, 0.35, [0.1 * w, 0.1 * h, 0.3 * w, 0.9 * h]),
        ]
        return [SimpleNamespace(names=self.names, boxes=[b for b in found if b.conf.value >= conf])]


@pytest.fixture
def fake_gpu(monkeypatch: pytest.MonkeyPatch) -> Callable[[bool], SimpleNamespace]:
    def install(cuda: bool) -> SimpleNamespace:
        made: list[FakeYolo] = []

        class YOLO(FakeYolo):
            def __init__(self, model: str) -> None:
                super().__init__(model)
                made.append(self)

        ultralytics = ModuleType("ultralytics")
        ultralytics.YOLO = YOLO  # type: ignore[attr-defined]
        torch = ModuleType("torch")
        torch.cuda = SimpleNamespace(is_available=lambda: cuda)  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "ultralytics", ultralytics)
        monkeypatch.setitem(sys.modules, "torch", torch)
        return SimpleNamespace(made=made)

    return install


# ── the protocol's constants ────────────────────────────────────────────────────────────


def test_the_constants_are_the_ones_the_protocol_names(hostd: ModuleType) -> None:
    """Both sides are built against one contract, so the names and values are pinned here."""
    assert hostd.PROTOCOL == "quackd-jetson-hostd"
    assert hostd.PROTOCOL_VERSION == 1
    assert hostd.HOSTD_VERSION == "0.1.0"
    assert hostd.DEFAULT_PORT == 9874
    assert hostd.TOKEN_ENV == "QUACKD_HOST_TOKEN"
    assert hostd.TOKEN_HEADER == "X-Quackd-Token"
    assert hostd.MAX_JPEG_BYTES == 8 * 1024 * 1024
    assert hostd.BOARD_FILES == FILES
    assert hostd.GPU_NODES == NODES, "the three node names quackd doctor has always read"
    assert set(hostd.COMMANDS) == {"nvpmodel -q", "tegrastats"}
    handler = hostd.make_handler(hostd.Hostd())
    assert handler.protocol_version == "HTTP/1.1"
    assert handler.timeout == hostd.REQUEST_TIMEOUT_S == 5.0
    assert hostd._Server.daemon_threads is True


# ── /hello, honestly ────────────────────────────────────────────────────────────────────


def test_hello_is_honest_when_opencv_and_ultralytics_are_absent(
    hostd: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A board with neither still starts, and says what it lacks rather than crashing or
    advertising a camera and a detector it cannot deliver."""
    monkeypatch.setitem(sys.modules, "cv2", None)
    monkeypatch.setitem(sys.modules, "ultralytics", None)
    args = hostd.parser().parse_args(["--camera", "csi", "--board-root", str(tmp_path)])
    daemon = hostd.Hostd.from_args(args, None)
    with serving(hostd, daemon) as port:
        status, hello = reply(port, "GET", "/hello")
        assert status == 200
        assert set(hello) == {
            "ok",
            "protocol",
            "protocol_version",
            "daemon_version",
            "hostname",
            "python",
            "capabilities",
            "camera",
            "camera_error",
            "detect",
            "detect_error",
            "board_model",
        }
        assert hello["protocol"] == "quackd-jetson-hostd" and hello["protocol_version"] == 1
        assert hello["capabilities"] == {"camera": False, "detect": False, "tegra": False}
        assert hello["camera"] is None and hello["detect"] is None
        assert "OpenCV (cv2)" in hello["camera_error"]
        assert hello["detect_error"].startswith("ultralytics is not installed on this board")
        assert hello["board_model"] is None

        status, body = reply(port, "GET", "/snapshot.jpg")
        assert status == 503 and body["reason"].startswith("no camera on this host: OpenCV")
        status, body = reply(port, "POST", "/detect", body=jpeg_of())
        assert status == 503
        assert body["reason"].startswith(
            "detection is not available: ultralytics is not installed on this board"
        )

        # These are the unit's own flags, so this is the board it starts on: both were asked
        # for and neither is there, and /healthz says so rather than ok.
        status, health = reply(port, "GET", "/healthz")
        assert status == 200 and health["ok"] is False
        assert health["camera"] is None and health["detect"] is None
        assert "no camera: OpenCV (cv2)" in health["reason"]
        assert "no detector: ultralytics is not installed" in health["reason"]


def test_the_daemon_loads_and_fakes_a_camera_with_only_the_standard_library(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """JetPack's system python may have none of these. The module must still import, and
    `--camera fake` must still serve a JPEG, from the standard library alone."""
    for name in ("cv2", "numpy", "PIL", "PIL.Image", "PIL.ImageDraw", "torch", "ultralytics"):
        monkeypatch.setitem(sys.modules, name, None)
    try:
        bare = _load(HOSTD, "quackd_jetson_hostd_bare")
        camera = bare.FakeCamera()
        jpeg, size = camera.jpeg()
        assert jpeg[:2] == b"\xff\xd8" and size == camera.size == (2, 2)
        engine, why = bare.open_engine(True, "yolov8n.pt", 0.4)
        assert engine is None and "ultralytics is not installed" in why
    finally:
        sys.modules.pop("quackd_jetson_hostd_bare", None)


@pytest.mark.parametrize("cuda", [True, False], ids=["cuda", "cpu"])
def test_a_fake_gpu_is_advertised_and_detect_returns_pixel_boxes_model_and_device(
    hostd: ModuleType,
    fake_gpu: Callable[[bool], SimpleNamespace],
    tmp_path: Path,
    cuda: bool,
) -> None:
    gpu = fake_gpu(cuda)
    args = hostd.parser().parse_args(["--board-root", str(tmp_path)])
    daemon = hostd.Hostd.from_args(args, None)
    device = "cuda" if cuda else "cpu"
    with serving(hostd, daemon) as port:
        _, hello = reply(port, "GET", "/hello")
        assert hello["capabilities"]["detect"] is True and hello["detect_error"] is None
        assert hello["detect"] == {
            "model": "yolov8n.pt",
            "device": device,
            "conf": 0.4,
            "names": ["person", "cat", "sports ball"],
        }, "names travel in the model's index order"

        (model,) = gpu.made
        warm_up = model.calls[0]
        assert warm_up["size"] == (64, 64) and warm_up["extrema"] == ((0, 0), (0, 0), (0, 0))
        assert warm_up["device"] == ("cuda:0" if cuda else "cpu")

        status, found = reply(port, "POST", "/detect", body=jpeg_of((320, 240)))
        assert status == 200
        assert set(found) == {"ok", "w", "h", "boxes", "model", "device", "ms"}
        assert (found["w"], found["h"]) == (320, 240)
        assert found["model"] == "yolov8n.pt" and found["device"] == device
        assert isinstance(found["ms"], float) and found["ms"] >= 0
        assert found["boxes"] == [
            {
                "name": "sports ball",
                "conf": 0.87,
                "x1": 144.0,
                "y1": 120.0,
                "x2": 176.0,
                "y2": 144.0,
            }
        ], "pixels in the image that was sent, and the model's own class name, mapped by nobody"
        call = model.calls[-1]
        assert call["conf"] == 0.4 and call["mode"] == "RGB" and call["verbose"] is False
        assert call["device"] == ("cuda:0" if cuda else "cpu")

        status, found = reply(port, "POST", "/detect?conf=0.3", body=jpeg_of((320, 240)))
        assert status == 200 and [b["name"] for b in found["boxes"]] == ["sports ball", "person"]
        assert model.calls[-1]["conf"] == 0.3

        _, health = reply(port, "GET", "/healthz")
        assert health["ok"] is True
        assert health["detect"] == {
            "ok": True,
            "calls": 2,
            "errors": 0,
            "last_error": None,
            "last_ms": health["detect"]["last_ms"],
            "device": device,
        }, "the warm-up is not a call anybody made"


# ── the token ───────────────────────────────────────────────────────────────────────────


def test_every_path_refuses_a_missing_or_wrong_token_and_never_reads_one_from_the_url(
    hostd: ModuleType,
    fake_gpu: Callable[[bool], SimpleNamespace],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fake_gpu(True)
    no_commands(monkeypatch, hostd)
    daemon = hostd.Hostd(
        camera=hostd.FakeCamera(),
        engine=hostd.YoloEngine(),
        token=TOKEN,
        board_root=str(tmp_path),
    )
    daemon.store.put(jpeg_of(), (320, 240), now=time.monotonic())
    routes = [
        ("GET", "/hello", None),
        ("GET", "/healthz", None),
        ("GET", "/board", None),
        ("GET", "/snapshot.jpg", None),
        ("GET", "/nothing-here", None),
        ("POST", "/detect", jpeg_of()),
        ("POST", "/hello", jpeg_of()),
        ("PUT", "/hello", None),
        ("DELETE", "/detect", None),
    ]
    with serving(hostd, daemon) as port:
        for method, path, body in routes:
            for token in (None, "wrong", TOKEN + "x", TOKEN[:-1], ""):
                status, said = reply(port, method, path, body=body, token=token)
                assert (status, said["reason"]) == (401, "bad or missing token"), (method, path)
            for query in (
                f"?token={TOKEN}",
                f"?X-Quackd-Token={TOKEN}",
                f"?x_quackd_token={TOKEN}",
            ):
                status, _, _ = call(port, method, path + query, body=body)
                assert status == 401, f"{method} {path}{query} took a token from the URL"
            status, _, _ = call(port, method, path, body=body, token=TOKEN)
            assert status != 401, f"{method} {path} refused the right token"


def test_a_refused_body_is_drained_so_the_connection_reads_the_next_request_cleanly(
    hostd: ModuleType,
    fake_gpu: Callable[[bool], SimpleNamespace],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A 400 or a 405 that leaves the body in the socket turns it into the next request line
    on a kept-alive connection. So a client with the token has what it sent read and dropped,
    a chunk at a time and never all at once, before it is answered."""
    asked: list[int] = []
    readinto = hostd._Reader.readinto

    def recording(self: Any, buffer: Any) -> int:
        asked.append(len(memoryview(buffer)))
        return int(readinto(self, buffer))

    monkeypatch.setattr(hostd._Reader, "readinto", recording)
    fake_gpu(False)
    daemon = hostd.Hostd(engine=hostd.YoloEngine(), token=TOKEN, board_root=str(tmp_path))
    with serving(hostd, daemon) as port:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        try:
            conn.request("POST", "/detect", body=b"not a jpeg", headers={"X-Quackd-Token": TOKEN})
            resp = conn.getresponse()
            assert resp.status == 400 and resp.getheader("Connection") != "close"
            resp.read()
            big = bytes(5 * hostd.DRAIN_CHUNK_BYTES + 7)
            conn.request("POST", "/hello", body=big, headers={"X-Quackd-Token": TOKEN})
            resp = conn.getresponse()
            assert resp.status == 405 and resp.getheader("Connection") != "close"
            resp.read()
            conn.request("GET", "/hello", headers={"X-Quackd-Token": TOKEN})
            resp = conn.getresponse()
            assert resp.status == 200 and json.loads(resp.read())["protocol_version"] == 1
        finally:
            conn.close()
    assert asked and max(asked) <= hostd.DRAIN_CHUNK_BYTES, "a dropped body was read whole"


def read_until_closed(sock: socket.socket) -> bytes:
    """Everything the daemon sends before it closes, or what came before it reset or went
    quiet: a test asks what arrived, and a reset is only one way of nothing arriving."""
    chunks: list[bytes] = []
    with contextlib.suppress(OSError):
        while chunk := sock.recv(65536):
            chunks.append(chunk)
    return b"".join(chunks)


def test_a_client_without_the_token_is_refused_before_its_body_and_the_refusal_arrives(
    hostd: ModuleType, fake_gpu: Callable[[bool], SimpleNamespace], tmp_path: Path
) -> None:
    """A 401 that first read the body it was going to throw away let a client with no token
    declare 8 MB and send a byte every few seconds, holding a thread and the buffer for as long
    as it liked, and never be told no. So the 401 goes out on the head alone, and closes.

    Closing with the body unread makes the kernel reset the connection, which can throw the
    401 away before the client reads it: quackd sending a frame with the wrong token would hear
    "connection reset" rather than which token was wrong. So the daemon lingers after the
    refusal, dropping what arrives, and the 401 gets through whatever size the body was."""
    fake_gpu(False)
    daemon = hostd.Hostd(engine=hostd.YoloEngine(), token=TOKEN, board_root=str(tmp_path))
    with serving(hostd, daemon) as port:
        # the largest body it takes: a smaller one can fit in the kernel's buffers whole, and
        # then the reset comes too late to show
        for body in (jpeg_of(), bytes(hostd.MAX_JPEG_BYTES)):
            for _ in range(3):
                status, headers, raw = call(port, "POST", "/detect", body=body, token="wrong")
                assert status == 401 and headers["connection"] == "close"
                assert json.loads(raw)["reason"] == "bad or missing token"

        with socket.create_connection(("127.0.0.1", port), timeout=10) as sock:
            sock.sendall(
                b"POST /detect HTTP/1.1\r\nHost: hostd\r\nContent-Type: image/jpeg\r\n"
                b"Content-Length: %d\r\n\r\n" % hostd.MAX_JPEG_BYTES
            )
            began = time.monotonic()
            sock.settimeout(hostd.REQUEST_TIMEOUT_S / 2)
            said = read_until_closed(sock)
            took = time.monotonic() - began
    assert said.startswith(b"HTTP/1.1 401 "), said[:80]
    assert took < hostd.REQUEST_TIMEOUT_S / 2, "the refusal waited on a body it never read"


def test_a_verb_or_path_it_does_not_answer_is_refused_in_json(
    hostd: ModuleType, tmp_path: Path
) -> None:
    """http.server answers an unknown verb with an HTML 501. The protocol says JSON, a 405 on
    a path this daemon has, and a 404 on one it has not."""
    daemon = hostd.Hostd(board_root=str(tmp_path))
    with serving(hostd, daemon) as port:
        status, headers, raw = call(port, "POST", "/hello", body=jpeg_of())
        assert status == 405 and headers["allow"] == "GET" and not json.loads(raw)["ok"]
        status, headers, raw = call(port, "GET", "/detect")
        assert status == 405 and headers["allow"] == "POST"
        assert reply(port, "PUT", "/healthz")[0] == 405
        assert reply(port, "DELETE", "/detect")[0] == 405
        status, body = reply(port, "PUT", "/nowhere")
        assert status == 404 and body["reason"] == "nothing at /nowhere"
        status, body = reply(port, "GET", "/hello?x=1")
        assert status == 200, "the query string is stripped before routing"
        head, rest = raw_reply(
            port, b"HEAD /hello HTTP/1.1\r\nHost: hostd\r\nConnection: close\r\n\r\n"
        )
        assert head.startswith(b"HTTP/1.1 405 "), head
        assert rest == b"", "a body after a HEAD reply is read as the next reply on the socket"


# ── /detect's refusals ──────────────────────────────────────────────────────────────────


def test_detect_refuses_what_it_cannot_decode_and_bodies_it_will_not_hold(
    hostd: ModuleType,
    fake_gpu: Callable[[bool], SimpleNamespace],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    gpu = fake_gpu(True)
    daemon = hostd.Hostd(engine=hostd.YoloEngine(), board_root=str(tmp_path))
    (model,) = gpu.made
    with serving(hostd, daemon) as port:
        for junk in (b"this is not a jpeg", b"", jpeg_of(fmt="PNG"), jpeg_of()[:40]):
            status, body = reply(port, "POST", "/detect", body=junk)
            assert status == 400, junk[:20]
            assert body["reason"] == "the body is not an image this daemon can decode"

        assert post_declaring(port, length=None)[0] == 411
        status, body = post_declaring(port, length=hostd.MAX_JPEG_BYTES + 1)
        assert status == 413 and str(hostd.MAX_JPEG_BYTES) in body["reason"]

        status, body = reply(port, "POST", "/detect?conf=2", body=jpeg_of())
        assert status == 400 and "conf" in body["reason"]
        status, body = reply(port, "POST", "/detect?conf=nan", body=jpeg_of())
        assert status == 400 and "conf" in body["reason"]

        monkeypatch.setattr(hostd, "MAX_DETECT_PIXELS", 100)
        status, body = reply(port, "POST", "/detect", body=jpeg_of((320, 240)))
        assert status == 413 and "320x240" in body["reason"]
    assert len(model.calls) == 1, "nothing refused ever reached the model"


def test_a_pillow_that_cannot_decode_the_way_detect_does_is_refused_at_start(
    hostd: ModuleType,
    fake_gpu: Callable[[bool], SimpleNamespace],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Otherwise every `/detect` answers 400 about the image, which is a lie about the image."""
    gpu = fake_gpu(True)

    def old_open(fp: Any, mode: str = "r", **kwargs: Any) -> Any:
        if kwargs:
            raise TypeError("open() got an unexpected keyword argument 'formats'")
        raise AssertionError("never reached")

    monkeypatch.setattr(Image, "open", old_open)
    engine, why = hostd.open_engine(True, "yolov8n.pt", 0.4)
    assert engine is None and "cannot decode a JPEG the way /detect does" in why
    assert gpu.made == [], "refused before the model was loaded"


def test_detect_says_503_with_the_reason_when_there_is_no_detector(
    hostd: ModuleType, tmp_path: Path
) -> None:
    args = hostd.parser().parse_args(["--no-detect", "--board-root", str(tmp_path)])
    daemon = hostd.Hostd.from_args(args, None)
    with serving(hostd, daemon) as port:
        status, body = reply(port, "POST", "/detect", body=jpeg_of())
        assert status == 503
        assert body["reason"] == "detection is not available: started with --no-detect"


def test_a_detector_that_fails_is_named_by_healthz_until_it_answers_again(
    hostd: ModuleType, fake_gpu: Callable[[bool], SimpleNamespace], tmp_path: Path
) -> None:
    gpu = fake_gpu(True)
    daemon = hostd.Hostd(engine=hostd.YoloEngine(), board_root=str(tmp_path))
    (model,) = gpu.made
    with serving(hostd, daemon) as port:
        model.fail = "CUDA out of memory"
        status, body = reply(port, "POST", "/detect", body=jpeg_of())
        assert status == 500 and "CUDA out of memory" in body["reason"]
        _, health = reply(port, "GET", "/healthz")
        assert health["ok"] is False and health["camera"] is None
        assert health["detect"]["ok"] is False and health["detect"]["errors"] == 1
        assert "detector" in health["reason"] and "CUDA out of memory" in health["reason"]

        model.fail = None
        assert reply(port, "POST", "/detect", body=jpeg_of())[0] == 200
        _, health = reply(port, "GET", "/healthz")
        assert health["ok"] is True and "reason" not in health
        assert health["detect"]["errors"] == 1, "history is kept, the verdict is current"


# ── /snapshot.jpg, the Open Duck camera daemon's semantics ──────────────────────────────


def test_a_snapshot_before_the_first_frame_is_a_clean_503(
    hostd: ModuleType, tmp_path: Path
) -> None:
    daemon = hostd.Hostd(camera=hostd.FakeCamera(), board_root=str(tmp_path))
    with serving(hostd, daemon) as port:
        status, body = reply(port, "GET", "/snapshot.jpg")
        assert (status, body["reason"]) == (503, "no frame captured yet")


def test_a_stale_frame_is_refused_with_its_age(hostd: ModuleType, tmp_path: Path) -> None:
    """A camera that stops must stop being served, or `go_to` steers on a photograph."""
    daemon = hostd.Hostd(camera=hostd.FakeCamera(), fps=5.0, board_root=str(tmp_path))
    daemon.store.put(jpeg_of(), (320, 240), now=time.monotonic() - 30.0)
    with serving(hostd, daemon) as port:
        status, body = reply(port, "GET", "/snapshot.jpg")
        assert status == 503 and "stale after 1.5s" in body["reason"]
        assert body["age_s"] >= 29.0


def test_a_fresh_frame_is_served_with_its_age(hostd: ModuleType, tmp_path: Path) -> None:
    daemon = hostd.Hostd(camera=hostd.FakeCamera(), board_root=str(tmp_path))
    jpeg = jpeg_of()
    daemon.store.put(jpeg, (320, 240), now=time.monotonic())
    with serving(hostd, daemon) as port:
        status, headers, body = call(port, "GET", "/snapshot.jpg")
        assert status == 200 and body == jpeg
        assert headers["content-type"] == "image/jpeg"
        assert headers["cache-control"] == "no-store"
        assert headers["age"] == "0" and 0.0 <= float(headers["x-frame-age"]) < 1.0
        assert len(headers["x-frame-age"].partition(".")[2]) == 3


def test_a_host_with_no_camera_says_why_instead_of_serving_nothing(
    hostd: ModuleType, tmp_path: Path
) -> None:
    daemon = hostd.Hostd.from_args(
        hostd.parser().parse_args(["--no-detect", "--board-root", str(tmp_path)]), None
    )
    with serving(hostd, daemon) as port:
        status, body = reply(port, "GET", "/snapshot.jpg")
        assert status == 503
        assert body["reason"] == "no camera on this host: started with --camera none"
        _, health = reply(port, "GET", "/healthz")
        assert health["ok"] is True and "reason" not in health, "nothing asked for is missing"


def test_healthz_names_the_camera_when_it_is_the_sick_one(
    hostd: ModuleType, tmp_path: Path
) -> None:
    daemon = hostd.Hostd(camera=hostd.FakeCamera(), board_root=str(tmp_path))
    with serving(hostd, daemon) as port:
        status, health = reply(port, "GET", "/healthz")
        assert status == 200, "healthz always answers 200; ok is the verdict"
        assert set(health) == {"ok", "daemon_version", "uptime_s", "camera", "detect", "reason"}
        assert health["ok"] is False and health["detect"] is None
        assert "camera" in health["reason"] and "not captured a frame" in health["reason"]
        assert set(health["camera"]) == {
            "ok",
            "age_s",
            "size",
            "bytes",
            "frames",
            "errors",
            "last_error",
            "stale_after_s",
        }

        daemon.store.fail("the ribbon cable came loose")
        _, health = reply(port, "GET", "/healthz")
        assert "the ribbon cable came loose" in health["reason"]

        daemon.store.put(jpeg_of(), (320, 240), now=time.monotonic())
        _, health = reply(port, "GET", "/healthz")
        assert health["ok"] is True and "reason" not in health
        assert health["camera"]["ok"] is True and health["camera"]["size"] == [320, 240]

        daemon.store.put(jpeg_of(), (320, 240), now=time.monotonic() - 30.0)
        _, health = reply(port, "GET", "/healthz")
        assert health["ok"] is False and health["camera"]["ok"] is False
        assert "last frame is 30." in health["reason"]


# ── the camera sources ──────────────────────────────────────────────────────────────────


def test_the_fake_cameras_frame_is_one_quackds_colour_detector_can_see(
    hostd: ModuleType, tmp_path: Path
) -> None:
    """Served over HTTP and read by quackd's own detector, so `--camera fake` checks the
    detector's side of the chain as well as the plumbing."""
    from quackd.perception.color_blob import ColorBlobDetector

    daemon = hostd.Hostd(camera=hostd.FakeCamera(), fps=20.0, board_root=str(tmp_path))
    daemon.start()
    with serving(hostd, daemon) as port:
        wait_for_frame(daemon.store)
        status, headers, body = call(port, "GET", "/snapshot.jpg")
        assert status == 200 and headers["content-type"] == "image/jpeg"
        frame = Image.open(io.BytesIO(body)).convert("RGB")
        assert frame.size == (640, 480)
        balls = [d for d in ColorBlobDetector().detect(frame) if d.label == "ball"]
        assert balls, "the synthetic scene has to be one quackd's own detector can see"
        assert balls[0].est_distance_m is not None


def test_a_frame_is_shrunk_to_fit_keeping_its_shape_and_never_enlarged(hostd: ModuleType) -> None:
    """Keeping the aspect ratio keeps the lens's horizontal field of view in the frame."""
    assert hostd.fit_within(1640, 1232, (640, 480)) == (639, 480)
    assert hostd.fit_within(1280, 720, (640, 480)) == (640, 360)
    assert hostd.fit_within(1920, 1080, (640, 480)) == (640, 360)
    assert hostd.fit_within(320, 240, (640, 480)) == (320, 240)
    assert hostd.fit_within(640, 480, (640, 480)) == (640, 480)


class _Capture:
    """`cv2.VideoCapture` for one frame source, recording how it was opened."""

    opened: list[Any] = []
    frame: Any = None
    works = True

    def __init__(self, source: Any, api: Any) -> None:
        type(self).opened.append((source, api))

    def isOpened(self) -> bool:
        return type(self).works

    def read(self) -> tuple[bool, Any]:
        frame = type(self).frame
        return frame is not None, None if frame is None else frame.copy()

    def set(self, *args: Any) -> bool:
        return True

    def release(self) -> None:
        type(self).opened.append("released")


@pytest.fixture
def capture(monkeypatch: pytest.MonkeyPatch) -> type[_Capture]:
    cv2 = pytest.importorskip("cv2")
    np = pytest.importorskip("numpy")

    class Capture(_Capture):
        opened: list[Any] = []
        # a pale floor with an orange ball, in the BGR order OpenCV reads a camera in
        frame = np.full((1232, 1640, 3), (212, 229, 236), dtype=np.uint8)

    cv2.circle(Capture.frame, (820, 700), 90, (0, 140, 255), -1)
    monkeypatch.setattr(cv2, "VideoCapture", Capture)
    return Capture


def test_csi_opens_the_default_pipeline_through_gstreamer_and_serves_a_frame_that_keeps_its_shape(
    hostd: ModuleType, capture: type[_Capture]
) -> None:
    import cv2

    from quackd.perception.color_blob import ColorBlobDetector

    camera = hostd.Cv2Camera("csi", (640, 480))
    assert capture.opened[0] == (hostd.DEFAULT_CSI_PIPELINE, cv2.CAP_GSTREAMER)
    assert "nvarguscamerasrc" in hostd.DEFAULT_CSI_PIPELINE
    assert hostd.DEFAULT_CSI_PIPELINE.endswith("appsink drop=1 max-buffers=1")
    assert camera.source == "csi" and camera.size == (639, 480)
    jpeg, size = camera.jpeg()
    image = Image.open(io.BytesIO(jpeg)).convert("RGB")
    assert image.size == size == (639, 480)
    balls = [d for d in ColorBlobDetector().detect(image) if d.label == "ball"]
    assert balls, "BGR in, BGR encoded: an orange ball must come out orange"
    camera.close()
    assert capture.opened[-1] == "released"


def test_an_index_is_v4l2_and_a_string_with_a_bang_is_a_pipeline(
    hostd: ModuleType, capture: type[_Capture]
) -> None:
    import cv2

    usb = hostd.Cv2Camera("0", (640, 480))
    assert capture.opened[0] == (0, cv2.CAP_V4L2) and usb.source == "/dev/video0"
    pipeline = "v4l2src device=/dev/video1 ! videoconvert ! appsink"
    own = hostd.Cv2Camera(pipeline, (640, 480))
    assert (pipeline, cv2.CAP_GSTREAMER) in capture.opened
    assert own.source == f"gstreamer: {pipeline}"


def test_a_camera_that_will_not_open_or_will_not_deliver_is_no_camera_with_a_reason(
    hostd: ModuleType, capture: type[_Capture]
) -> None:
    capture.works = False
    camera, why = hostd.open_camera("csi", (640, 480))
    assert camera is None and why.startswith("csi did not open")
    assert "GStreamer" in why or "nvargus-daemon" in why
    capture.works = True
    capture.frame = None
    camera, why = hostd.open_camera("2", (640, 480))
    assert camera is None and why.startswith("/dev/video2 opened but gave no frame")
    assert capture.opened[-1] == "released"


def test_an_opencv_without_gstreamer_is_named_with_where_it_loads_from_and_how_to_remove_it(
    hostd: ModuleType, capture: type[_Capture], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The usual way a board gets one is `pip install ultralytics` for the system python, which
    installs pip's opencv-python where that python finds it before JetPack's own. So the reason
    names the copy that loaded and the uninstall, where the operator reads it."""
    import cv2

    monkeypatch.setattr(cv2, "getBuildInformation", lambda: "  Video I/O:\n    GStreamer: NO\n")
    capture.works = False
    for spec in ("csi", "v4l2src ! videoconvert ! appsink"):
        camera, why = hostd.open_camera(spec, (640, 480))
        assert camera is None and "was built without GStreamer" in why, why
        assert f"({os.path.dirname(cv2.__file__)})" in why, why
        assert why.endswith("pip uninstall opencv-python"), why


def test_the_readme_says_ultralytics_brings_a_pip_opencv_and_how_to_take_it_out() -> None:
    """The unit runs the system python for JetPack's OpenCV, and the README sends people to
    install ultralytics for that same python, which brings pip's OpenCV and hides JetPack's.
    The section that sends them there is the one that has to say so."""
    readme = (REPO / "bridge" / "jetson" / "README.md").read_text(encoding="utf-8")
    section = readme.split("\n## Detection on the GPU\n", 1)[1].split("\n## ", 1)[0]
    assert "ultralytics requires pip's `opencv-python`" in section
    assert "python3 -m pip uninstall -y opencv-python" in section
    assert 'python3 -c "import cv2; print(cv2.getBuildInformation())" | grep GStreamer' in section


PASSWORDED = (
    "rtspsrc location=rtsp://admin:hunter2@192.168.1.64/stream1 user-id=admin "
    'user-pw="sword fish" ! decodebin ! videoconvert ! appsink'
)
"""A pipeline of your own with a camera's credentials in it, both ways rtspsrc takes them."""


def test_a_password_in_a_camera_pipeline_reaches_the_capture_and_nothing_else(
    hostd: ModuleType,
    capture: type[_Capture],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The pipeline is shown in `/hello`, in every reason that names the camera and in the log,
    which is to say to every client and to journald. quackd puts a 503's reason in the error it
    raises, so a password there would reach the laptop's terminal and its transcripts."""
    import cv2

    def leaks(text: str) -> bool:
        return "hunter2" in text or "sword" in text

    capture.works = False
    args = hostd.parser().parse_args(
        ["--camera", PASSWORDED, "--no-detect", "--board-root", str(tmp_path)]
    )
    with caplog.at_level(logging.INFO, logger="quackd-jetson-hostd"):
        daemon = hostd.Hostd.from_args(args, None)
    assert capture.opened[0] == (PASSWORDED, cv2.CAP_GSTREAMER), "the capture gets it all"
    with serving(hostd, daemon) as port:
        _, hello = reply(port, "GET", "/hello")
        _, snapshot = reply(port, "GET", "/snapshot.jpg")
        _, health = reply(port, "GET", "/healthz")
    shown = hello["camera_error"]
    assert "rtsp://***@192.168.1.64/stream1" in shown and "user-pw=***" in shown, shown
    assert "user-id=admin" in shown, "only the password is a secret in a property"
    for said in (shown, snapshot["reason"], health["reason"], caplog.text):
        assert not leaks(said), said

    capture.works = True
    camera = hostd.Cv2Camera(PASSWORDED, (640, 480))
    assert camera.source.startswith("gstreamer: rtspsrc") and not leaks(camera.source)
    camera.close()

    def throws(source: Any, api: Any) -> Any:
        raise RuntimeError(f"cannot open {source}")

    monkeypatch.setattr(cv2, "VideoCapture", throws)
    camera, why = hostd.open_camera(PASSWORDED, (640, 480))
    assert camera is None and why.startswith("--camera rtspsrc") and not leaks(why)
    with pytest.raises(argparse.ArgumentTypeError) as refused:
        hostd.camera_spec("rtsp://admin:hunter2@192.168.1.64/stream1")
    assert not leaks(str(refused.value))


@pytest.mark.parametrize(
    "pipeline",
    [
        # a property with spaces around its `=`, which GStreamer's lexer takes
        "rtspsrc location=rtsp://cam/s user-id=admin user-pw = hunter2 ! decodebin ! appsink",
        # a query parameter: how a Foscam-style snapshot CGI authenticates
        'souphttpsrc location="http://cam:88/cgi-bin/CGIProxy.fcgi?cmd=snapPicture2&usr=admin'
        '&pwd=hunter2" ! jpegdec ! appsink',
        "souphttpsrc location=https://cam/mjpeg?token=hunter2 ! jpegdec ! appsink",
        "souphttpsrc location=https://cam/mjpeg?res=hd&api_key=hunter2#top ! jpegdec ! appsink",
        'srtsrc uri="srt://cam:7001?mode=caller&passphrase=hunter2" ! tsdemux ! appsink',
        # a header, and a cookie
        'souphttpsrc location=http://cam/ extra-headers="h, Authorization=(string)\\"Bearer '
        'hunter2\\"" ! jpegdec ! appsink',
        'souphttpsrc location=http://cam/ cookies="session=hunter2" ! jpegdec ! appsink',
        # a quoted URL whose password has a space in it, or a slash
        'rtspsrc location="rtsp://admin:hun ter2@cam/s" ! decodebin ! appsink',
        "rtspsrc location=rtsp://admin:hun/ter2@cam/s ! decodebin ! appsink",
        # a value holding what the lexer keeps in one: a `!`, an escaped space or quote
        "rtspsrc location=rtsp://cam/s user-pw=hun!ter2 ! decodebin ! appsink",
        "rtspsrc location=rtsp://cam/s user-pw=hun\\ ter2 ! decodebin ! appsink",
        'rtspsrc location=rtsp://cam/s user-pw="hun\\"ter2" ! decodebin ! appsink',
        "rtspsrc location=rtsp://cam/s proxy-pw=(string)hunter2 ! decodebin ! appsink",
    ],
)
def test_every_way_a_pipeline_carries_a_credential_is_masked(
    hostd: ModuleType, pipeline: str
) -> None:
    shown = hostd.redact(pipeline)
    assert "hunter2" not in shown and "ter2" not in shown and "***" in shown, shown
    assert shown.endswith("appsink"), "the rest of the pipeline is still there to read"


def test_redaction_takes_the_credential_and_leaves_what_a_reader_needs(
    hostd: ModuleType,
) -> None:
    snapshot = (
        'souphttpsrc location="http://cam:88/cgi-bin/CGIProxy.fcgi?cmd=snapPicture2&usr=admin'
        '&pwd=hunter2" ! jpegdec ! videoconvert ! appsink'
    )
    assert hostd.redact(snapshot) == snapshot.replace("hunter2", "***")
    spaced = "rtspsrc location=rtsp://cam/s user-id=admin user-pw = hunter2 latency=0 ! appsink"
    assert hostd.redact(spaced) == (
        "rtspsrc location=rtsp://cam/s user-id=admin user-pw=*** latency=0 ! appsink"
    )
    usb = "v4l2src device=/dev/video0 ! video/x-raw, format=YUY2, width=640 ! appsink drop=1"
    for untouched in (hostd.DEFAULT_CSI_PIPELINE, usb):
        assert hostd.redact(untouched) == untouched


def test_a_capture_error_that_quotes_the_pipeline_is_redacted_in_healthz_and_the_log(
    hostd: ModuleType, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Every reason that may name the camera is redacted, and a capture that fails mid-run is
    one: `/healthz` serves its error, and quackd doctor prints it."""

    class Quoting:
        source = f"gstreamer: {hostd.redact(PASSWORDED)}"
        size = (640, 480)

        def jpeg(self) -> tuple[bytes, tuple[int, int]]:
            raise RuntimeError(f"could not read a frame from {PASSWORDED}")

        def close(self) -> None:
            pass

    daemon = hostd.Hostd(camera=Quoting(), board_root=str(tmp_path))
    with caplog.at_level(logging.WARNING, logger="quackd-jetson-hostd"):
        daemon.start()
        try:
            end = time.monotonic() + 5.0
            while daemon.store.health(now=0.0, stale_after=1.0)["errors"] == 0:
                assert time.monotonic() < end, "the capture never failed"
                time.sleep(0.02)
        finally:
            daemon.stop()
    health = daemon.health()
    assert "could not read a frame from rtspsrc" in health["reason"]
    assert "could not read a frame from rtspsrc" in health["camera"]["last_error"]
    for said in (health["reason"], caplog.text):
        assert "hunter2" not in said and "sword" not in said, said


# ── /board ──────────────────────────────────────────────────────────────────────────────


def test_board_ships_a_tegra_trees_files_raw_with_the_nuls_removed(
    hostd: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[tuple[Any, ...]] = []

    def run(argv: Any, timeout_s: float, *, first_line: bool = False) -> tuple[Any, Any]:
        calls.append((tuple(argv), timeout_s, first_line))
        return (NVPMODEL_Q if argv[0] == "nvpmodel" else TEGRASTATS_LINE), None

    monkeypatch.setattr(hostd, "run_quiet", run)
    root = tegra_tree(tmp_path / "board")
    daemon = hostd.Hostd(board_root=str(root))
    with serving(hostd, daemon) as port:
        status, board = reply(port, "GET", "/board")
    assert status == 200 and set(board) == {"ok", "files", "nodes", "commands", "errors"}
    assert board["files"] == {
        "/proc/device-tree/model": ORIN_NANO,
        "/proc/device-tree/compatible": COMPATIBLE.replace(NUL, ""),
        "/etc/nv_tegra_release": RELEASE_36_4_3,
        "/proc/meminfo": MEMINFO,
        "/proc/swaps": ZRAM_SWAPS,
    }
    assert board["nodes"] == {
        "/dev/nvgpu/igpu0": True,
        "/dev/nvhost-ctrl-gpu": False,
        "/dev/nvidia0": False,
    }
    assert board["commands"] == {"nvpmodel -q": NVPMODEL_Q, "tegrastats": TEGRASTATS_LINE}
    assert board["errors"] == {}
    assert calls == [
        (("nvpmodel", "-q"), 3.0, False),
        (("tegrastats", "--interval", "500"), 3.0, True),
    ], "tegrastats streams forever, so it is the one read for a first line"


def test_board_reports_what_is_missing_under_the_same_key(
    hostd: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    no_commands(monkeypatch, hostd)
    root = tegra_tree(tmp_path / "board", release=None, gpu_node=False)
    board = hostd.board_dump(str(root))
    assert board["files"]["/etc/nv_tegra_release"] is None
    assert board["errors"]["/etc/nv_tegra_release"], "every null has its reason"
    assert board["files"]["/proc/device-tree/model"] == ORIN_NANO
    assert board["nodes"] == dict.fromkeys(NODES, False)
    assert board["commands"] == {"nvpmodel -q": None, "tegrastats": None}
    assert board["errors"]["nvpmodel -q"] == board["errors"]["tegrastats"] == "not on PATH"
    assert set(board["errors"]) == {"/etc/nv_tegra_release", "nvpmodel -q", "tegrastats"}


def test_board_on_an_empty_root_is_ok_with_nulls_and_reasons_never_a_crash(
    hostd: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    no_commands(monkeypatch, hostd)
    for root in (tmp_path / "empty", tmp_path / "does-not-exist"):
        if root.name == "empty":
            root.mkdir()
        board = hostd.Hostd(board_root=str(root)).board()
        assert board["ok"] is True
        assert board["files"] == dict.fromkeys(FILES)
        assert board["nodes"] == dict.fromkeys(NODES, False)
        assert board["commands"] == {"nvpmodel -q": None, "tegrastats": None}
        assert set(board["errors"]) == {*FILES, "nvpmodel -q", "tegrastats"}
        assert all(board["errors"].values())


def test_board_files_are_capped_decoded_whatever_they_hold_and_a_directory_is_a_reason(
    hostd: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    no_commands(monkeypatch, hostd)
    root = tmp_path / "board"
    write(root, "proc/meminfo", "x" * 70000)
    (root / "proc" / "swaps").mkdir(parents=True)
    (root / "etc").mkdir()
    (root / "etc" / "nv_tegra_release").write_bytes(b"# R36 \xff\xfe (release)\x00")
    board = hostd.board_dump(str(root))
    assert len(board["files"]["/proc/meminfo"]) == 65536
    assert board["files"]["/proc/swaps"] is None and board["errors"]["/proc/swaps"]
    assert board["files"]["/etc/nv_tegra_release"] == "# R36 �� (release)"


def test_a_board_file_is_capped_as_it_goes_on_the_wire_not_only_as_it_is_read(
    hostd: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A byte that is not UTF-8 becomes U+FFFD and a control character stays itself, and JSON
    writes either as a six byte escape; a character past the BMP is twelve, and a quote two.
    The cap bounds what a wrong board root can make `/board` send, so it is measured there,
    and a value is trimmed to the longest start of it that fits, not shorter."""
    no_commands(monkeypatch, hostd)
    root = tmp_path / "board"
    (root / "proc" / "device-tree").mkdir(parents=True)
    (root / "proc" / "swaps").write_bytes(b"\xff" * 70000)
    (root / "proc" / "meminfo").write_bytes(b"\x01" * 70000)
    write(root, "proc/device-tree/model", "\U0001f986" * 20000)
    write(root, "proc/device-tree/compatible", '"' * 70000)
    with serving(hostd, hostd.Hostd(board_root=str(root))) as port:
        status, _, raw = call(port, "GET", "/board")
    assert status == 200
    files = json.loads(raw)["files"]
    for path in FILES[:2] + FILES[3:]:
        wire = len(json.dumps(files[path])) - 2
        assert hostd.FILE_CAP_BYTES - 12 < wire <= hostd.FILE_CAP_BYTES, (path, wire)


def test_a_board_with_no_device_tree_is_still_a_tegra_by_its_release_file(
    hostd: ModuleType, tmp_path: Path
) -> None:
    """The two ways quackd doctor has always asked, and a laptop answers no to both."""
    orin = hostd.Hostd(board_root=str(tegra_tree(tmp_path / "orin"))).hello()
    assert orin["capabilities"]["tegra"] is True and orin["board_model"] == ORIN_NANO
    masked = tegra_tree(tmp_path / "masked", device_tree=False)
    hello = hostd.Hostd(board_root=str(masked)).hello()
    assert hello["capabilities"]["tegra"] is True and hello["board_model"] is None
    laptop = hostd.Hostd(board_root=str(tmp_path / "laptop")).hello()
    assert laptop["capabilities"]["tegra"] is False and laptop["board_model"] is None


# ── run_quiet ───────────────────────────────────────────────────────────────────────────

FOREVER = "\n".join(
    [
        "import sys, time",
        "n = 0",
        "while True:",
        "    sys.stdout.write('line %d\\n' % n)",
        "    sys.stdout.flush()",
        "    n += 1",
        "    time.sleep(0.01)",
    ]
)
"""A stand-in for `tegrastats --interval 500`, which prints a line per interval forever."""

SILENT = "import time\ntime.sleep(60)"
"""The same stand-in on a board where it never gets as far as a line."""


def _record_popen(monkeypatch: pytest.MonkeyPatch, hostd: ModuleType) -> list[Any]:
    started: list[Any] = []
    real = subprocess.Popen

    def recording(*args: Any, **kwargs: Any) -> Any:
        proc = real(*args, **kwargs)
        started.append(proc)
        return proc

    monkeypatch.setattr(hostd.subprocess, "Popen", recording)
    return started


def test_run_quiet_takes_one_line_from_a_command_that_never_stops_and_kills_it(
    hostd: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    started = _record_popen(monkeypatch, hostd)
    began = time.monotonic()
    out, why = hostd.run_quiet([sys.executable, "-c", FOREVER], 10.0, first_line=True)
    took = time.monotonic() - began
    assert (out, why) == ("line 0", None), "exactly one line, without its newline"
    assert len(started) == 1
    assert started[0].poll() is not None, "a tegrastats left running per /board is a leak"
    assert took < 10.0


def test_run_quiet_kills_a_command_that_prints_nothing_when_its_time_is_up(
    hostd: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    started = _record_popen(monkeypatch, hostd)
    began = time.monotonic()
    out, why = hostd.run_quiet([sys.executable, "-c", SILENT], 1.0, first_line=True)
    took = time.monotonic() - began
    assert (out, why) == (None, "no line within 1s")
    assert started[0].poll() is not None, "the child is dead, not orphaned"
    assert 0.9 <= took < 8.0


def test_run_quiet_never_forks_a_binary_that_is_not_on_the_path(
    hostd: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    forked: list[Any] = []

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        forked.append(args)
        raise AssertionError("forked a binary that is not there")

    monkeypatch.setattr(hostd.subprocess, "Popen", forbidden)
    monkeypatch.setattr(hostd.subprocess, "run", forbidden)
    for first_line in (False, True):
        out = hostd.run_quiet(["quackd-no-such-binary-anywhere"], 1.0, first_line=first_line)
        assert out == (None, "not on PATH")
    assert hostd.run_quiet([], 1.0) == (None, "no command")
    assert forked == []


def test_run_quiet_says_how_a_command_failed_and_never_raises(
    hostd: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The `nvpmodel -q` path, with `subprocess.run` replaced: no process is started here."""
    monkeypatch.setattr(hostd.shutil, "which", lambda name: f"/usr/sbin/{name}")
    seen: list[dict[str, Any]] = []
    outcome: list[Any] = []

    def run(argv: list[str], **kwargs: Any) -> Any:
        seen.append({"argv": argv, **kwargs})
        if isinstance(outcome[0], BaseException):
            raise outcome[0]
        return outcome[0]

    monkeypatch.setattr(hostd.subprocess, "run", run)
    outcome[:] = [subprocess.CompletedProcess(["nvpmodel"], 0, NVPMODEL_Q.encode() + b"\0", b"")]
    assert hostd.run_quiet(["nvpmodel", "-q"], 3.0) == (NVPMODEL_Q, None)
    assert seen[-1]["argv"] == ["/usr/sbin/nvpmodel", "-q"]
    assert seen[-1]["stdin"] is subprocess.DEVNULL and seen[-1]["timeout"] == 3.0

    outcome[:] = [subprocess.CompletedProcess(["nvpmodel"], 1, b"", b"NVPM ERROR: not root\n")]
    assert hostd.run_quiet(["nvpmodel", "-q"], 3.0) == (None, "exited 1: NVPM ERROR: not root")
    outcome[:] = [subprocess.TimeoutExpired(["nvpmodel"], 3.0)]
    assert hostd.run_quiet(["nvpmodel", "-q"], 3.0) == (None, "no answer within 3s")
    outcome[:] = [PermissionError(13, "Permission denied")]
    out, why = hostd.run_quiet(["nvpmodel", "-q"], 3.0)
    assert out is None and why.startswith("could not run:")


# ── many clients at once, and clients that say nothing ──────────────────────────────────


class Overlap:
    """Counts the callers inside a block at once, holding each one there for `hold_s`, so that
    anything not serialised is caught in the act rather than by luck."""

    def __init__(self, hold_s: float) -> None:
        self.hold_s = hold_s
        self._lock = threading.Lock()
        self.inside = 0
        self.peak = 0
        self.calls = 0

    @contextlib.contextmanager
    def enter(self) -> Iterator[None]:
        with self._lock:
            self.inside += 1
            self.calls += 1
            self.peak = max(self.peak, self.inside)
        try:
            time.sleep(self.hold_s)
            yield
        finally:
            with self._lock:
                self.inside -= 1


def at_once(n: int, work: Callable[[], Any]) -> list[Any]:
    """`work` on `n` threads released together, and what each returned. A thread that raised
    raises here, so a failure is a failed test and not a warning about a thread."""
    barrier = threading.Barrier(n)
    results: list[Any] = [None] * n
    errors: list[BaseException] = []

    def run(i: int) -> None:
        try:
            barrier.wait(10)
            results[i] = work()
        except BaseException as e:
            errors.append(e)

    threads = [threading.Thread(target=run, args=(i,)) for i in range(n)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(30)
    assert not [t for t in threads if t.is_alive()], "a caller never came back"
    if errors:
        raise errors[0]
    return results


def test_calls_into_the_model_never_overlap_however_many_clients_ask_at_once(
    hostd: ModuleType, fake_gpu: Callable[[bool], SimpleNamespace], tmp_path: Path
) -> None:
    """One GPU runs one model at a time anyway, and a model called from two threads at once
    is not one anybody has promised works. So the calls queue, and decoding happens outside."""
    gpu = fake_gpu(True)
    daemon = hostd.Hostd(engine=hostd.YoloEngine(), board_root=str(tmp_path))
    (model,) = gpu.made
    overlap = Overlap(0.2)
    predict = model.predict

    def slow(*args: Any, **kwargs: Any) -> Any:
        with overlap.enter():
            return predict(*args, **kwargs)

    model.predict = slow  # type: ignore[method-assign]
    with serving(hostd, daemon) as port:
        statuses = at_once(3, lambda: reply(port, "POST", "/detect", body=jpeg_of())[0])
    assert statuses == [200, 200, 200]
    assert overlap.calls == 3 and overlap.peak == 1


def test_two_board_requests_at_once_never_run_its_commands_at_once(
    hostd: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`/board` forks `nvpmodel` and a `tegrastats` it has to kill, and a monitoring script and
    `quackd doctor` asking together must not fork them twice over, on a board that may also
    be running a robot's control loop."""
    overlap = Overlap(0.1)

    def run(argv: Any, timeout_s: float, *, first_line: bool = False) -> tuple[Any, Any]:
        with overlap.enter():
            return None, "not on PATH"

    monkeypatch.setattr(hostd, "run_quiet", run)
    daemon = hostd.Hostd(board_root=str(tmp_path))
    with serving(hostd, daemon) as port:
        statuses = at_once(3, lambda: reply(port, "GET", "/board")[0])
    assert statuses == [200, 200, 200]
    assert overlap.calls == 6 and overlap.peak == 1


def test_a_frame_and_its_time_are_written_and_read_as_one(hostd: ModuleType) -> None:
    """The capture thread writes a JPEG, its size and when it arrived. A request that read the
    new JPEG with the old time would call a fresh frame stale, or a stale one fresh. Racing two
    threads until that shows is a test that passes by luck, so this holds the store's lock and
    shows that every method waits for it."""
    store = hostd.FrameStore()
    store.put(b"old", (1, 1), now=1.0)
    calls: dict[str, Callable[[], Any]] = {
        "put": lambda: store.put(b"new", (2, 2), now=2.0),
        "fail": lambda: store.fail("a hiccup"),
        "get": store.get,
        "health": lambda: store.health(now=3.0, stale_after=1.5),
    }
    waiting = {name: threading.Thread(target=fn, daemon=True) for name, fn in calls.items()}
    with store._lock:
        for thread in waiting.values():
            thread.start()
        time.sleep(0.3)
        done = [name for name, thread in waiting.items() if not thread.is_alive()]
        assert done == [], f"{done} did not wait for the lock"
    for thread in waiting.values():
        thread.join(5)
    assert store.get() == (b"new", 2.0)


def test_a_connection_that_never_sends_a_request_is_closed_rather_than_kept(
    hostd: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Every connection gets a thread, and HTTP/1.1 keeps connections open. Without a timeout, a
    client that connects and says nothing holds a thread on the board for as long as the
    process lives. The timeout is shortened here so the test does not wait five seconds."""
    monkeypatch.setattr(hostd, "REQUEST_TIMEOUT_S", 0.5)
    daemon = hostd.Hostd(board_root=str(tmp_path))
    with (
        serving(hostd, daemon) as port,
        socket.create_connection(("127.0.0.1", port), timeout=5) as sock,
    ):
        began = time.monotonic()
        try:
            said = sock.recv(1)
        except ConnectionResetError:
            said = b""
        took = time.monotonic() - began
    assert said == b"", "the daemon hung up without answering"
    assert took < 4.0


def test_a_client_that_keeps_its_connection_open_does_not_hold_up_shutdown(
    hostd: ModuleType, tmp_path: Path
) -> None:
    """systemd stops the unit with SIGTERM, and `main` then closes the server. A server that
    joined every request thread on the way out would first wait out each idle client's
    timeout, so request threads are daemon threads and closing does not wait for them."""
    daemon = hostd.Hostd(board_root=str(tmp_path))
    server = hostd.serve(daemon, "127.0.0.1", 0)
    conn = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=10)
    try:
        conn.request("GET", "/hello")
        resp = conn.getresponse()
        resp.read()
        assert resp.status == 200 and resp.getheader("Connection") != "close"
        # the connection stays open, and its thread is waiting for a next request
        began = time.monotonic()
        server.shutdown()
        server.server_close()
        took = time.monotonic() - began
    finally:
        conn.close()
        daemon.stop()
    assert took < 2.0, f"closing the server waited {took:.1f}s for an idle client"


def test_a_request_trickled_a_byte_at_a_time_is_cut_off_at_its_deadline(
    hostd: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The timeout bounds each wait for a byte, and a client sending one just inside it never
    trips that: it held a thread, and everything it had sent, for as long as it went on. So the
    whole request has one deadline. It is shortened here so the test does not wait ten seconds;
    the byte interval is well inside the five-second timeout, which is left as it is."""
    monkeypatch.setattr(hostd, "REQUEST_DEADLINE_S", 1.0)
    daemon = hostd.Hostd(token=TOKEN, board_root=str(tmp_path))
    with (
        serving(hostd, daemon) as port,
        socket.create_connection(("127.0.0.1", port), timeout=10) as sock,
    ):
        sock.sendall(b"GET /hello HTTP/1.1\r\nHost: hostd\r\nX-Pad: ")
        sock.settimeout(0.2)
        began = time.monotonic()
        closed_after: float | None = None
        while closed_after is None and time.monotonic() - began < 4.0:
            try:
                sock.sendall(b"a")
                if sock.recv(1) == b"":
                    closed_after = time.monotonic() - began
            except TimeoutError:
                continue  # still open, and still waiting for the rest of the head
            except OSError:
                closed_after = time.monotonic() - began
    assert closed_after is not None, "a request trickled for 4 s was still being read"
    assert closed_after < 2.5


def test_a_head_larger_than_the_daemon_reads_is_not_answered(
    hostd: ModuleType, tmp_path: Path
) -> None:
    """http.server by itself reads a hundred header lines of 64 KB before the handler or the
    token check runs: over 6 MB held for a client that has proved nothing. The daemon reads
    `MAX_HEAD_BYTES` of head and then closes; a head of an ordinary size is answered."""
    daemon = hostd.Hostd(token=TOKEN, board_root=str(tmp_path))

    def head_of(lines: int) -> bytes:
        pad = b"a" * 1000
        padding = b"".join(b"X-Pad-%d: %s\r\n" % (i, pad) for i in range(lines))
        return b"GET /hello HTTP/1.1\r\nHost: hostd\r\n" + padding + b"\r\n"

    assert len(head_of(60)) > hostd.MAX_HEAD_BYTES > len(head_of(16))
    with serving(hostd, daemon) as port:
        for lines, answered in ((16, True), (60, False)):
            with socket.create_connection(("127.0.0.1", port), timeout=10) as sock:
                with contextlib.suppress(OSError):
                    sock.sendall(head_of(lines))
                said = read_until_closed(sock)
            assert said.startswith(b"HTTP/1.1 401 ") is answered, (lines, said[:80])


def test_connections_past_the_cap_are_refused_busy_rather_than_given_a_thread(
    hostd: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Every connection is a thread and what it has read, and ThreadingHTTPServer bounds
    neither, so anyone who could reach the port could open connections until the board ran
    out of memory. Past the cap a connection is answered 503 in JSON and closed, and a slot
    comes back when a connection ends."""
    monkeypatch.setattr(hostd, "MAX_CONNECTIONS", 2)
    daemon = hostd.Hostd(board_root=str(tmp_path))
    with serving(hostd, daemon) as port:
        idle = [socket.create_connection(("127.0.0.1", port), timeout=10) for _ in range(2)]
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=10) as sock:
                sock.settimeout(hostd.REQUEST_TIMEOUT_S / 2)
                said = read_until_closed(sock)
        finally:
            for sock in idle:
                sock.close()
        head, _, body = said.partition(b"\r\n\r\n")
        assert head.startswith(b"HTTP/1.1 503 "), said[:80]
        assert b"Connection: close" in head
        refused = json.loads(body)
        assert refused["ok"] is False and refused["reason"].startswith("busy: ")

        def hello() -> int | None:
            try:
                return call(port, "GET", "/hello")[0]
            except (OSError, http.client.HTTPException):
                return None  # still busy: a 503 to a request already sent can be reset away

        end = time.monotonic() + 5.0
        while (status := hello()) != 200 and time.monotonic() < end:
            time.sleep(0.05)
        assert status == 200, "the idle connections' slots never came back"


def test_detect_holds_no_more_bodies_and_decoded_images_than_it_has_slots(
    hostd: ModuleType, fake_gpu: Callable[[bool], SimpleNamespace], tmp_path: Path
) -> None:
    """The body and pixel caps are per request, and decoding ran outside any bound, so thirty
    requests at the pixel cap held thirty decoded frames of 48 MB while they queued for the
    model. Now a request takes a slot before it reads its body, and every request is still
    answered: they queue for the slots as they queue for the model."""
    fake_gpu(True)
    daemon = hostd.Hostd(engine=hostd.YoloEngine(), board_root=str(tmp_path))
    engine = daemon.engine
    overlap = Overlap(0.2)
    decode = engine.decode

    def slow(jpeg: bytes) -> Any:
        with overlap.enter():
            return decode(jpeg)

    engine.decode = slow
    with serving(hostd, daemon) as port:
        statuses = at_once(5, lambda: reply(port, "POST", "/detect", body=jpeg_of())[0])
    assert statuses == [200] * 5
    assert overlap.calls == 5 and overlap.peak <= hostd.DETECT_SLOTS


def test_a_detect_that_cannot_get_a_slot_is_refused_busy_with_its_body_unread(
    hostd: ModuleType,
    fake_gpu: Callable[[bool], SimpleNamespace],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(hostd, "DETECT_SLOTS", 1)
    monkeypatch.setattr(hostd, "DETECT_WAIT_S", 0.3)
    gpu = fake_gpu(True)
    daemon = hostd.Hostd(engine=hostd.YoloEngine(), board_root=str(tmp_path))
    (model,) = gpu.made
    inside, release = threading.Event(), threading.Event()
    predict = model.predict

    def held(*args: Any, **kwargs: Any) -> Any:
        inside.set()
        release.wait(10)
        return predict(*args, **kwargs)

    model.predict = held  # type: ignore[method-assign]
    first: list[int] = []
    with serving(hostd, daemon) as port:
        holder = threading.Thread(
            target=lambda: first.append(reply(port, "POST", "/detect", body=jpeg_of())[0])
        )
        holder.start()
        try:
            assert inside.wait(5), "the first detection never reached the model"
            with socket.create_connection(("127.0.0.1", port), timeout=10) as sock:
                # a body declared and never sent: a reply at all means it was not waited for
                sock.sendall(
                    b"POST /detect HTTP/1.1\r\nHost: hostd\r\nContent-Type: image/jpeg\r\n"
                    b"Content-Length: 1000\r\n\r\n"
                )
                sock.settimeout(hostd.REQUEST_TIMEOUT_S / 2)
                said = read_until_closed(sock)
        finally:
            release.set()
            holder.join(10)
    head, _, body = said.partition(b"\r\n\r\n")
    assert head.startswith(b"HTTP/1.1 503 "), said[:80]
    assert json.loads(body)["reason"].startswith("busy: ")
    assert first == [200], "the detection holding the slot was answered"


# ── what the daemon cannot do, read from its source ─────────────────────────────────────


def _identifiers(tree: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, (ast.arg, ast.keyword)) and node.arg:
            names.add(node.arg)
        elif isinstance(node, ast.alias):
            names.update(filter(None, (node.name, node.asname)))
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def _imports(tree: ast.AST) -> tuple[list[str], list[str]]:
    """(modules imported when the file loads, modules imported inside a function)."""
    inside: set[int] = set()
    for fn in ast.walk(tree):
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            inside.update(
                id(n) for n in ast.walk(fn) if isinstance(n, (ast.Import, ast.ImportFrom))
            )
    at_load: list[str] = []
    lazily: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            modules = [node.module or ""]
        else:
            continue
        (lazily if id(node) in inside else at_load).extend(modules)
    return at_load, lazily


def test_nothing_in_the_daemon_can_move_a_robot(hostd: ModuleType, tmp_path: Path) -> None:
    """It reads a camera, runs a detector on what it is sent, reads files and runs two
    read-only commands. On a robot's own board, this is a port that cannot reach the body."""
    tree = ast.parse(SOURCE)
    (handler,) = [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef) and n.name == "Handler"]
    verbs = {
        f.name for f in handler.body if isinstance(f, ast.FunctionDef) and f.name.startswith("do_")
    }
    assert verbs == {"do_GET", "do_POST"}
    everywhere = {
        n.name
        for n in ast.walk(tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name.startswith("do_")
    }
    assert everywhere == {"do_GET", "do_POST"}
    assert set(hostd.POST_PATHS) == {"/detect"}, "the only path that takes a body"

    names = {name.lower() for name in _identifiers(tree)}
    for forbidden in (
        "send_intent",
        "intent",
        "torque",
        "serial",
        "motor",
        "servo",
        "actuat",
        "joint",
        "velocity",
        "twist",
        "cmd_vel",
        "gpio",
        "smbus",
        "i2c",
        "pwm",
        "dynamixel",
        "feetech",
    ):
        hits = sorted(n for n in names if forbidden in n)
        assert not hits, f"the daemon names {hits}"
    # and it reaches nothing: a robot's daemon on the same board is one connect() away
    for outward in ("urlopen", "create_connection", "httpconnection", "connect", "request"):
        assert outward not in names, f"the daemon calls {outward}"

    # the router agrees with the source: every GET path refuses a POST
    daemon = hostd.Hostd(board_root=str(tmp_path))
    with serving(hostd, daemon) as port:
        for path in sorted(hostd.GET_PATHS):
            assert call(port, "POST", path, body=b"{}")[0] == 405, path


def test_the_daemon_never_imports_quackd_and_loads_on_the_standard_library_alone() -> None:
    tree = ast.parse(SOURCE)
    at_load, lazily = _imports(tree)
    everything = at_load + lazily
    assert not [m for m in everything if m == "quackd" or m.startswith(("quackd.", "quackd_"))]
    stdlib = set(sys.stdlib_module_names) | {"__future__"}
    assert not [m for m in at_load if m.split(".")[0] not in stdlib], at_load
    optional = {"cv2", "PIL", "torch", "ultralytics", "numpy"}
    assert {m.split(".")[0] for m in lazily} <= stdlib | optional, lazily
    assert {"cv2", "torch", "ultralytics"} <= {m.split(".")[0] for m in lazily}


BANNED_BARE = {
    "tomllib",
    "UTC",
    "ExceptionGroup",
    "BaseExceptionGroup",
    "Self",
    "StrEnum",
    "TaskGroup",
    "add_note",
    "LiteralString",
    "Never",
    "assert_never",
    "NotRequired",
    "Required",
    "reveal_type",
    "file_digest",
    "getLevelNamesMapping",
}
"""Names that arrived in 3.11 or later, and parse happily on 3.10 until they are reached."""

BANNED_DOTTED = {"contextlib.chdir", "asyncio.timeout", "enum.verify", "operator.call"}
"""The same, for attributes whose bare name 3.10 also has (`os.chdir` is fine)."""


def _dotted(tree: ast.AST) -> set[str]:
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            out.add(f"{node.value.id}.{node.attr}")
        elif isinstance(node, ast.ImportFrom) and node.module:
            out.update(f"{node.module}.{alias.name}" for alias in node.names)
    return out


def test_the_daemon_is_python_3_10_because_jetpack_6s_system_python_is() -> None:
    tree = ast.parse(SOURCE, feature_version=(3, 10))
    assert not [n for n in ast.walk(tree) if type(n).__name__ == "TryStar"], "no except*"
    used = _identifiers(tree)
    assert not sorted(used & BANNED_BARE)
    assert not sorted(_dotted(tree) & BANNED_DOTTED)
    # and the guard bites: 3.11's grammar is refused under 3.10's
    with pytest.raises(SyntaxError):
        ast.parse("try:\n    pass\nexcept* ValueError:\n    pass\n", feature_version=(3, 10))
    assert not _same_quote_fstrings(SOURCE), "an f-string reusing its own quote is 3.12 only"
    assert _same_quote_fstrings('f"{d["k"]}"\n') or sys.version_info < (3, 12)


def _same_quote_fstrings(source: str) -> list[int]:
    """Lines where an f-string holds a string in its own quote, which PEP 701 allowed in 3.12.

    `feature_version` does not catch this: it is the tokenizer's business, not the grammar's.
    From 3.12 the tokenizer splits f-strings into parts and this finds them; on 3.11 it does
    not, and 3.11's parser refuses the file by itself."""
    import tokenize

    lines: list[int] = []
    quotes: list[str] = []
    for tok in tokenize.generate_tokens(io.StringIO(source).readline):
        kind = tokenize.tok_name[tok.type]
        if kind == "FSTRING_START":
            if quotes and tok.string[-1] == quotes[-1]:
                lines.append(tok.start[0])
            quotes.append(tok.string[-1])
        elif kind == "FSTRING_END" and quotes:
            quotes.pop()
        elif kind == "STRING" and quotes and tok.string.lstrip("rbuRBU")[:1] == quotes[-1]:
            lines.append(tok.start[0])
    return lines


# ── the network, the unit, and starting up ──────────────────────────────────────────────


def _exec_start() -> list[str]:
    unit = UNIT.read_text(encoding="utf-8").replace("\\\n", " ")
    (line,) = [ln for ln in unit.splitlines() if ln.startswith("ExecStart=")]
    return shlex.split(line.partition("=")[2])


def test_it_binds_loopback_by_default_and_the_units_exec_start_agrees(hostd: ModuleType) -> None:
    """It serves a live view of wherever the board is and a GPU anyone can keep busy, so the
    default must not be the LAN. The unit's flags go through the daemon's own parser."""
    assert hostd.parser().parse_args([]).bind == "127.0.0.1"
    argv = _exec_start()
    assert argv[:2] == ["/usr/bin/python3", "/opt/quackd/quackd_jetson_hostd.py"]
    args = hostd.parser().parse_args(argv[2:])
    assert args.bind == "127.0.0.1" and args.port == hostd.DEFAULT_PORT
    assert args.camera == "csi" and args.fps == 5.0 and args.size == (640, 480)
    assert args.token_file == "/etc/quackd/jetson-hostd.token"
    assert "--bind 0.0.0.0" not in UNIT.read_text(encoding="utf-8")


def test_the_unit_makes_this_the_process_to_lose_first() -> None:
    unit = UNIT.read_text(encoding="utf-8")
    lines = {ln.strip() for ln in unit.splitlines()}
    for wanted in (
        "After=network-online.target nvargus-daemon.service",
        "Wants=network-online.target",
        "User=jetson",
        "SupplementaryGroups=video",
        "MemoryMax=2G",
        "OOMScoreAdjust=500",
        "Nice=10",
        "Restart=on-failure",
        "RestartSec=5",
        "WantedBy=multi-user.target",
    ):
        assert wanted in lines, f"the unit has no {wanted}"
    assert "fifty hertz" in unit, "the unit says why it yields"


def test_binding_wide_without_a_token_warns_about_the_view_and_the_gpu(hostd: ModuleType) -> None:
    said = hostd.wide_bind_warning("0.0.0.0", None)
    assert "live view" in said and "GPU" in said
    assert "no control path" in said and "ssh tunnel" in said
    for loopback in ("127.0.0.1", "localhost", "::1"):
        assert hostd.wide_bind_warning(loopback, None) is None
    assert hostd.wide_bind_warning("0.0.0.0", TOKEN) is None


def test_it_serves_on_ipv6_loopback_too(hostd: ModuleType, tmp_path: Path) -> None:
    daemon = hostd.Hostd(board_root=str(tmp_path))
    try:
        server = hostd.serve(daemon, "::1", 0)
    except OSError:
        pytest.skip("this machine has no IPv6 loopback")
    try:
        conn = http.client.HTTPConnection("::1", server.server_address[1], timeout=10)
        conn.request("GET", "/hello")
        assert conn.getresponse().status == 200
        conn.close()
    finally:
        server.shutdown()
        server.server_close()


def test_a_port_another_server_listens_on_is_refused_rather_than_shared(
    hostd: ModuleType, tmp_path: Path
) -> None:
    """On Windows SO_REUSEADDR lets two servers bind one port, so a daemon started on a busy
    port there said it was serving while every request went to the one already listening. The
    one already listening here is what http.server, the fake daemon and a second copy of this
    daemon all are: a server that sets SO_REUSEADDR. Linux refuses both ways regardless."""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    other = ThreadingHTTPServer(("127.0.0.1", 0), BaseHTTPRequestHandler)
    try:
        try:
            shared = hostd.serve(
                hostd.Hostd(board_root=str(tmp_path)), "127.0.0.1", other.server_address[1]
            )
        except OSError:
            pass
        else:
            shared.shutdown()
            shared.server_close()
            pytest.fail("the daemon bound a port another server was listening on")
    finally:
        other.server_close()

    with serving(hostd, hostd.Hostd(board_root=str(tmp_path))) as port:
        try:
            later = ThreadingHTTPServer(("127.0.0.1", port), BaseHTTPRequestHandler)
        except OSError:
            pass
        else:
            later.server_close()
            pytest.fail("a later server bound the port the daemon was listening on")


class _Died(BaseException):
    """What SIGTERM's default action does to the daemon, without doing it to the test run. A
    BaseException, so nothing in the daemon that catches Exception can swallow it."""


@pytest.mark.parametrize("when", ["the model loads", "the camera reads its first frame"])
def test_a_sigterm_while_it_starts_stops_it_and_lets_go_of_the_camera(
    hostd: ModuleType,
    capture: type[_Capture],
    fake_gpu: Callable[[bool], SimpleNamespace],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    when: str,
) -> None:
    """systemd stops the unit with SIGTERM, and starting can take minutes: importing torch and,
    on a first start, downloading the model. SIGTERM's default is to die on the spot, and an
    argus client that dies holding the sensor can leave nvargus-daemon needing a restart. The
    handler this test puts in stands for that default, so a daemon that has not put its own in
    by then fails here, rather than killing the test run."""
    if threading.current_thread() is not threading.main_thread():
        pytest.skip("only the main thread can take a signal")
    fake_gpu(True)

    def terminate() -> None:
        signal.raise_signal(signal.SIGTERM)

    if when == "the model loads":
        loaded = sys.modules["ultralytics"].YOLO

        class Loading(loaded):  # type: ignore[misc,valid-type]
            def __init__(self, model: str) -> None:
                terminate()
                super().__init__(model)

        monkeypatch.setattr(sys.modules["ultralytics"], "YOLO", Loading)
    else:
        read = capture.read

        def reading(self: Any) -> tuple[bool, Any]:
            terminate()
            return read(self)

        monkeypatch.setattr(capture, "read", reading)

    def default_action(signum: int, frame: Any) -> None:
        raise _Died

    found = signal.signal(signal.SIGTERM, default_action)
    try:
        code = hostd.main(["--camera", "0", "--port", "0", "--board-root", str(tmp_path)])
        left = signal.getsignal(signal.SIGTERM)
    except _Died:
        pytest.fail(f"a SIGTERM while {when} met the default action: the daemon died there")
    finally:
        signal.signal(signal.SIGTERM, found)
    assert code == 0
    assert left is default_action, "main puts back the handler it found"
    opened = [o for o in capture.opened if o != "released"]
    assert capture.opened.count("released") == len(opened), capture.opened
    if when == "the camera reads its first frame":
        assert opened and capture.opened[-1] == "released"


def test_once_prints_a_hello_whose_protocol_and_version_match(
    hostd: ModuleType, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = hostd.main(["--camera", "fake", "--no-detect", "--once", "--board-root", str(tmp_path)])
    assert code == 0
    hello = json.loads(capsys.readouterr().out)
    assert hello["protocol"] == hostd.PROTOCOL == "quackd-jetson-hostd"
    assert hello["protocol_version"] == hostd.PROTOCOL_VERSION == 1
    assert hello["daemon_version"] == hostd.HOSTD_VERSION
    assert hello["capabilities"] == {"camera": True, "detect": False, "tegra": False}
    assert hello["camera"] == {"source": "fake", "fov_deg": None, "size": [640, 480], "fps": 5.0}
    assert hello["detect_error"] == "started with --no-detect"

    board = str(tegra_tree(tmp_path / "orin"))
    code = hostd.main(["--no-detect", "--fov-deg", "62.2", "--once", "--board-root", board])
    hello = json.loads(capsys.readouterr().out)
    assert code == 0 and hello["capabilities"]["tegra"] is True
    assert hello["board_model"] == ORIN_NANO and hello["camera"] is None


@pytest.mark.parametrize("what", ["missing", "a directory", "not readable", "empty", "blank"])
def test_a_token_file_that_cannot_be_read_or_holds_no_token_refuses_to_start(
    hostd: ModuleType,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
    what: str,
) -> None:
    """Starting anyway would be authentication silently off, which no client can tell apart
    from a daemon that checked the token it was sent. An empty file is the same failure by
    another route, and the README's `openssl ... | sudo install` line writes one on a board
    that has no openssl."""
    path = tmp_path / "jetson-hostd.token"
    if what == "a directory":
        path.mkdir()
    elif what in ("empty", "blank"):
        path.write_bytes(b"" if what == "empty" else b"  \n")
    elif what == "not readable":
        if os.name != "posix" or os.geteuid() == 0:
            pytest.skip("only a POSIX non-root user can be refused a file by its mode")
        path.write_text(TOKEN, encoding="utf-8")
        path.chmod(0)
    with caplog.at_level(logging.ERROR, logger="quackd-jetson-hostd"):
        code = hostd.main(["--once", "--token-file", str(path), "--board-root", str(tmp_path)])
    assert code == 2
    assert "Refusing to start" in caplog.text and str(path) in caplog.text
    assert capsys.readouterr().out == "", "it refused before it opened or served anything"


def test_a_token_comes_from_the_file_then_the_flag_then_the_environment(
    hostd: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    parse = hostd.parser().parse_args
    token_file = tmp_path / "jetson-hostd.token"
    token_file.write_text(f"  {TOKEN}\n", encoding="utf-8")
    monkeypatch.setenv("QUACKD_HOST_TOKEN", "from-the-environment")
    assert hostd.resolve_token(parse(["--token-file", str(token_file)])) == TOKEN
    assert hostd.resolve_token(parse(["--token", "from-the-flag"])) == "from-the-flag"
    assert hostd.resolve_token(parse([])) == "from-the-environment"
    assert hostd.resolve_token(parse(["--token", ""])) is None, "an empty token is no token"
    monkeypatch.setenv("QUACKD_HOST_TOKEN", "")
    assert hostd.resolve_token(parse([])) is None

    token_file.write_text("\n", encoding="utf-8")
    with pytest.raises(hostd.TokenFileError, match="is empty"):
        hostd.resolve_token(parse(["--token-file", str(token_file)]))

    with pytest.raises(SystemExit):
        parse(["--token", "a", "--token-file", str(token_file)])
    assert hostd.Hostd(token="").token is None
