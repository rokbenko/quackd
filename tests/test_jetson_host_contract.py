"""The two halves of the Jetson host protocol, talking to each other.

Every other test of this protocol holds one side against a stand-in for the other: the daemon's
own tests (`tests/test_jetson_hostd.py`) call it with `http.client`, and the client's, doctor's
and the host flag's call `tests/fake_jetson_hostd.py`. Each stand-in is written from the same
contract, and two sides can each agree with a fake and still disagree with each other: a field
renamed on one side, a shape one side sends and the other refuses, a constant that moved. This
file runs the REAL daemon, loaded by path the way the board runs it, against the REAL client in
`quackd/host.py` and the REAL `quackd doctor`. It is the test that catches drift between the
two sides of the protocol, and a change to either side that breaks it is a change to both.

What it cannot catch is anything about a Jetson. The board is a tree of files written from
`tests/jetson_fixtures.py`, the camera is the daemon's own fake, the GPU is a fake ultralytics
and torch in `sys.modules`, and `nvpmodel` and `tegrastats` are Python scripts printing the
fixture lines. Nobody on this project has run the daemon on a board. The server runs in-process
on port 0 rather than as a subprocess, for the reason the daemon's own tests give: subprocess
servers flake on this project's Windows machine.
"""

from __future__ import annotations

import importlib.util
import io
import platform
import socket
import sys
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest
from PIL import Image
from rich.console import Console

from quackd import doctor, host
from quackd.host import HostClient, HostError
from quackd.perception.yolo import detections_from_boxes
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
)

REPO = Path(__file__).resolve().parents[1]
HOSTD = REPO / "bridge" / "jetson" / "quackd_jetson_hostd.py"
TOKEN = "63d92f8974c051832d52dd04c78f314b01ef7436cc00e4a3805b9711e5318421"
"""Shaped like `openssl rand -hex 32`, and on every request, so the header is part of the
contract this file holds and not a detail left to the unit tests."""

BUSY_LINE = TEGRASTATS_LINE.replace("GR3D_FREQ 0%@[305]", "GR3D_FREQ 42%@[624]")
"""The fixture's `tegrastats` line with a GPU at work, so the load doctor reads is a number the
script printed and not a zero that any parser failing quietly could also produce."""
assert BUSY_LINE != TEGRASTATS_LINE, "the fixture line changed shape; this replace no longer bites"

FOV_DEG = 62.2
FPS = 10.0
SIZE = (640, 480)


def _load(path: Path, name: str) -> ModuleType:
    """The daemon as the board runs it: a file, never imported through quackd."""
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def hostd() -> ModuleType:
    # its own name, so nothing this file patches can leak into the daemon's own tests
    return _load(HOSTD, "quackd_jetson_hostd_contract")


# ── a fake GPU, and fake board commands ─────────────────────────────────────────────────


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


class FakeYolo:
    """`ultralytics.YOLO` as far as the daemon touches it. Whatever it is shown, it sees a ball
    in the middle at 0.87 and a person at 0.35, so the daemon's default floor of 0.4 keeps the
    ball and `conf=0.3` keeps both."""

    names = {0: "person", 32: "sports ball"}

    def __init__(self, model: str) -> None:
        self.model = model

    def predict(
        self, image: Any, conf: float = 0.25, verbose: bool = True, device: Any = None
    ) -> list[Any]:
        w, h = image.size
        found = [
            _Box(32, 0.87, [0.45 * w, 0.5 * h, 0.55 * w, 0.6 * h]),
            _Box(0, 0.35, [0.1 * w, 0.1 * h, 0.3 * w, 0.9 * h]),
        ]
        return [SimpleNamespace(names=self.names, boxes=[b for b in found if b.conf.value >= conf])]


def _script(path: Path, body: str) -> Path:
    path.write_text(body, encoding="utf-8")
    return path


@dataclass
class Board:
    """The daemon serving, and what a client needs to reach it."""

    module: ModuleType
    daemon: Any
    port: int

    @property
    def address(self) -> str:
        return f"127.0.0.1:{self.port}"

    def client(self, *, token: str | None = TOKEN) -> HostClient:
        return HostClient(self.address, token=token)


@pytest.fixture
def board(hostd: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[Board]:
    """The real daemon on loopback: a board tree for its files, its own fake camera, a YOLO
    engine over the fake GPU, a token, and two scripts standing in for the board's commands.

    The scripts write bytes, not text, so Windows cannot turn their newlines into CRLF: a
    board's commands print LF, and the daemon passes on exactly what it read."""
    ultralytics = ModuleType("ultralytics")
    ultralytics.YOLO = FakeYolo  # type: ignore[attr-defined]
    torch = ModuleType("torch")
    torch.cuda = SimpleNamespace(is_available=lambda: True)  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "ultralytics", ultralytics)
    monkeypatch.setitem(sys.modules, "torch", torch)

    nvpmodel = _script(
        tmp_path / "nvpmodel.py",
        f"import sys\nsys.stdout.buffer.write({NVPMODEL_Q.encode()!r})\n",
    )
    tegrastats = _script(
        tmp_path / "tegrastats.py",
        "import sys, time\n"
        "while True:\n"
        f"    sys.stdout.buffer.write({(BUSY_LINE + chr(10)).encode()!r})\n"
        "    sys.stdout.buffer.flush()\n"
        "    time.sleep(0.5)\n",
    )
    # The daemon's own table with only the programs swapped: the names it answers under, the
    # arguments and whether it reads one line are all the daemon's, so a change to any of them
    # reaches doctor here. The timeouts only grow, for a loaded CI machine starting Python.
    scripts = {"nvpmodel": nvpmodel, "tegrastats": tegrastats}
    monkeypatch.setattr(
        hostd,
        "COMMANDS",
        {
            name: ((sys.executable, str(scripts[argv[0]]), *argv[1:]), max(timeout_s, 10.0), first)
            for name, (argv, timeout_s, first) in hostd.COMMANDS.items()
        },
    )

    daemon = hostd.Hostd(
        camera=hostd.FakeCamera(SIZE),
        fps=FPS,
        fov_deg=FOV_DEG,
        engine=hostd.YoloEngine("yolov8n.pt", 0.4),
        token=TOKEN,
        board_root=str(tegra_tree(tmp_path / "board")),
    )
    daemon.start()
    server = hostd.serve(daemon, "127.0.0.1", 0)
    try:
        end = time.monotonic() + 10
        while daemon.store.get()[0] is None:
            assert time.monotonic() < end, "the daemon's fake camera captured nothing"
            time.sleep(0.02)
        yield Board(hostd, daemon, int(server.server_address[1]))
    finally:
        server.shutdown()
        server.server_close()
        daemon.stop()


# ── the protocol, end to end ────────────────────────────────────────────────────────────


def test_both_sides_spell_the_protocol_the_same(hostd: ModuleType) -> None:
    """The constants the contract names, compared across the two files rather than each against
    a number written in a test, so moving one side alone is caught here."""
    for name in ("PROTOCOL", "PROTOCOL_VERSION", "DEFAULT_PORT", "TOKEN_ENV", "TOKEN_HEADER"):
        assert getattr(host, name) == getattr(hostd, name), name
    assert host.MAX_JPEG_BYTES == hostd.MAX_JPEG_BYTES
    assert host.SNAPSHOT_PATH == hostd.SNAPSHOT_PATH


def test_hello_and_healthz_round_trip(board: Board) -> None:
    client = board.client()
    hello = client.hello()
    assert (hello.protocol, hello.protocol_version) == (host.PROTOCOL, host.PROTOCOL_VERSION)
    assert hello.daemon_version == board.module.HOSTD_VERSION
    assert hello.hostname == socket.gethostname() and hello.python == platform.python_version()
    assert hello.has_camera and hello.can_detect and hello.is_tegra
    assert hello.camera_size == SIZE and hello.camera_fps == FPS
    assert hello.camera_fov_deg == FOV_DEG
    assert hello.label() == "yolov8n.pt on cuda"
    assert hello.board_model == ORIN_NANO
    assert hello.camera_error is None and hello.detect_error is None

    health = client.healthz()
    assert health["ok"] is True, health
    assert health["daemon_version"] == hello.daemon_version
    assert health["camera"]["ok"] is True and health["camera"]["size"] == list(SIZE)
    assert health["detect"]["device"] == "cuda"


def test_the_board_dump_round_trips_raw(board: Board) -> None:
    """Raw text across the wire, with the NULs gone where the daemon strips them: the dump the
    client parses is the fixture board, and the two commands' output is what the scripts
    printed, byte for byte."""
    dump = board.client().board()
    assert dump.files == {
        "/proc/device-tree/model": ORIN_NANO,
        "/proc/device-tree/compatible": COMPATIBLE.replace(NUL, ""),
        "/etc/nv_tegra_release": RELEASE_36_4_3,
        "/proc/meminfo": MEMINFO,
        "/proc/swaps": ZRAM_SWAPS,
    }
    assert dump.nodes == {
        "/dev/nvgpu/igpu0": True,
        "/dev/nvhost-ctrl-gpu": False,
        "/dev/nvidia0": False,
    }
    assert dump.commands == {"nvpmodel -q": NVPMODEL_Q, "tegrastats": BUSY_LINE}
    assert dump.errors == {}


def test_a_snapshot_and_a_detection_round_trip_into_quackds_geometry(board: Board) -> None:
    """A frame from the daemon's camera, sent back to its detector, and the boxes read by the
    one function every quackd detector reads boxes with: the ball the board saw is a ball on
    this side, dead ahead of the lens."""
    client = board.client()
    image, age = client.snapshot()
    assert image.mode == "RGB" and image.size == SIZE
    assert age is not None and 0.0 <= age <= host.STALE_AFTER_S
    buf = io.BytesIO()
    image.save(buf, format="JPEG", quality=85)

    found = client.detect(buf.getvalue())
    assert (found.w, found.h) == SIZE
    assert (found.model, found.device) == ("yolov8n.pt", "cuda")
    assert found.ms is not None and found.ms >= 0
    assert [(b.name, b.conf) for b in found.boxes] == [("sports ball", 0.87)]
    seen = detections_from_boxes(
        [b.as_tuple() for b in found.boxes], found.w, found.h, fov_deg=FOV_DEG
    )
    assert [d.label for d in seen] == ["ball"]
    assert seen[0].bearing_deg == 0.0 and seen[0].est_distance_m is not None

    lower = client.detect(buf.getvalue(), conf=0.3)
    assert sorted(b.name for b in lower.boxes) == ["person", "sports ball"]


def test_the_token_rides_every_path_and_its_absence_names_the_flag(board: Board) -> None:
    anonymous = board.client(token=None)
    for call in (anonymous.hello, anonymous.healthz, anonymous.board, anonymous.snapshot):
        with pytest.raises(HostError) as refused:
            call()
        assert refused.value.status == 401 and "--host-token" in str(refused.value)
    with pytest.raises(HostError) as wrong:
        board.client(token="not-the-token").hello()
    assert wrong.value.status == 401 and "not-the-token" not in str(wrong.value)
    assert board.client().hello().protocol == host.PROTOCOL


def test_doctor_reads_the_real_daemon_into_a_jetson_report(
    board: Board, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The whole path `quackd doctor --host` takes: the daemon's `/hello`, `/healthz` and
    `/board`, the client's parsing, and doctor's parsers turning raw text into the section. The
    model servers are the one thing stubbed, since the presets would be probed on loopback."""
    monkeypatch.setattr(doctor, "_probe_models", lambda url, timeout_s=1.5: ("down", "not running"))
    monkeypatch.setattr(doctor, "_host_client", HostClient)
    report = doctor.collect(host=board.address, host_token=TOKEN)
    assert report.ok is True
    assert report.host is not None and report.host.ok is True and report.host.error is None
    assert report.host.healthz is not None and report.host.healthz["ok"] is True
    jetson = report.host.jetson
    assert jetson is not None
    assert jetson.board == "NVIDIA Jetson Orin Nano Developer Kit"
    assert (jetson.l4t, jetson.jetpack) == ("36.4.3", "6.2")
    assert jetson.swap_only_zram is True and jetson.swap_total_bytes == 1017852 * 1024
    assert jetson.mem_total_bytes == 7650336 * 1024
    assert jetson.gpu_device == "/dev/nvgpu/igpu0"
    assert jetson.power_mode == "15W"
    assert jetson.gpu_busy_pct == 42 and jetson.tegrastats == BUSY_LINE
    assert jetson.errors == {}

    buf = io.StringIO()
    doctor.render(Console(file=buf, width=200), report)
    out = " ".join(buf.getvalue().split())
    for needle in (
        f"Jetson at {board.address} (the board the daemon runs on)",
        "36.4.3 (JetPack 6.2)",
        "all zram: it compresses RAM rather than adding any (docs/guides/jetson.md)",
        "GPU busy 42% (GR3D_FREQ in tegrastats)",
        f"field of view {FOV_DEG:g} degrees",
        "detector yolov8n.pt on cuda",
    ):
        assert needle in out, needle
    assert TOKEN not in out


def test_a_frame_the_daemon_encodes_is_one_pil_reads_the_same_size(board: Board) -> None:
    """The snapshot's size in `/hello` is a promise about `/snapshot.jpg`, and the lens's field
    of view only means something at the size it was given for."""
    hello = board.client().hello()
    jpeg, _ = board.daemon.store.get()
    with Image.open(io.BytesIO(jpeg)) as picture:
        assert picture.size == hello.camera_size


def test_both_sides_bound_a_confidence_floor_the_same(board: Board) -> None:
    """The client refuses, as a ValueError before anything is sent, exactly the floors the
    daemon would refuse, and sends the rest. A floor it passed and the daemon refused would come
    back as a 400, a HostError, which every consumer reads as the board failing: a
    `HostDetector` built with it would run blind and blame the board."""
    client = board.client()
    buf = io.BytesIO()
    Image.new("RGB", SIZE, (236, 229, 212)).save(buf, format="JPEG")
    for conf in (0.0, -0.0, 1e-9, 0.3, 1.0, 1.000000001, -0.1):
        try:
            board.module._conf_from(f"conf={conf!r}")
        except ValueError:
            with pytest.raises(ValueError, match="confidence floor"):
                client.detect(buf.getvalue(), conf=conf)
        else:
            assert client.detect(buf.getvalue(), conf=conf).w == SIZE[0], conf


def test_the_slowest_capture_the_daemon_takes_keeps_frames_younger_than_the_client_refuses(
    hostd: ModuleType,
) -> None:
    """The client refuses a frame older than `STALE_AFTER_S` as a camera that has stopped, and
    the newest frame is up to a capture period old plus however long the next capture takes. A
    daemon capturing every 2.5 s would have the client call a working camera stopped on a fifth
    of its snapshots while its own `/healthz`, which expires frames at `max(1.5, 4 / fps)`, said
    ok. So the daemon refuses a rate that slow, and its slowest leaves a period to spare."""
    parse = hostd.parser().parse_args
    assert 1.0 / hostd.MIN_FPS <= host.STALE_AFTER_S / 2
    assert parse(["--fps", f"{hostd.MIN_FPS:g}"]).fps == hostd.MIN_FPS
    for slower in ("0.99", "0.5", "0.4"):
        with pytest.raises(SystemExit):
            parse(["--fps", slower])
