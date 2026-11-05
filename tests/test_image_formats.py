"""Every picture quackd decodes names the formats it takes.

`Image.open` with no `formats=` tries dozens of decoders, one of which starts Ghostscript, on the
strength of the first bytes, so whoever answers a camera request chooses which decoder runs: a
robot that has been taken over, or anything else on its network, since most of these wires are
plain HTTP or ZeroMQ. Pillow's own advisories are mostly about decoders no camera here needs, and
the extras that bring LeRobot hold Pillow under the release that fixes them. A wire takes what it
carries, as `bridge/jetson/quackd_jetson_hostd.py` always has.
"""

from __future__ import annotations

import ast
import base64
import io
import urllib.request
from collections.abc import Awaitable, Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar

import pytest
from PIL import Image, ImageFile

from quackd.agent.images import FORMATS, TaskImageError, load_task_images
from quackd.host import HostClient, HostError
from quackd.transport.base import camera_url_frame

REPO = Path(__file__).resolve().parent.parent
SIZE = (16, 16)

Reader = Callable[[bytes, pytest.MonkeyPatch, Path], Awaitable[Any]]


def _picture(fmt: str) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", SIZE, (40, 80, 120)).save(out, fmt)
    return out.getvalue()


class _Answer:
    """What `urllib.request.urlopen` hands a `with`, for the cameras read over HTTP."""

    def __init__(self, raw: bytes) -> None:
        self.raw = raw
        self.headers: dict[str, str] = {}

    def __enter__(self) -> _Answer:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def read(self) -> bytes:
        return self.raw


def _answers_with(raw: bytes, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: _Answer(raw))


async def _nothing_to_pump(*_: Any) -> bool:
    return True


# ── one reader for each place a picture is decoded: the picture, or None for a refusal ─────────


async def _host(raw: bytes, monkeypatch: pytest.MonkeyPatch, _: Path) -> Any:
    client = HostClient("127.0.0.1:9")
    monkeypatch.setattr(client, "_exchange", lambda *a, **k: (200, {}, raw))
    try:
        return client.snapshot()[0]
    except HostError:
        return None


async def _policy(raw: bytes, _: pytest.MonkeyPatch, __: Path) -> Any:
    from quackd_lerobot.policy.protocol import CameraInfo, Frame, ProtocolError, decode_frame

    frame = Frame(
        name="front",
        encoding="jpeg",
        height=SIZE[1],
        width=SIZE[0],
        data=base64.b64encode(raw).decode("ascii"),
    )
    try:
        return decode_frame(frame, CameraInfo(name="front", height=SIZE[1], width=SIZE[0]))
    except ProtocolError:
        return None


async def _alohamini(raw: bytes, monkeypatch: pytest.MonkeyPatch, _: Path) -> Any:
    from quackd_alohamini.zmq_host import AlohaMiniZmq

    link = AlohaMiniZmq()
    monkeypatch.setattr(link, "_pump", _nothing_to_pump)
    link.cameras = ("front",)
    link._frames = {"front": raw}
    return await link.get_frame()


async def _xlerobot(raw: bytes, monkeypatch: pytest.MonkeyPatch, _: Path) -> Any:
    from quackd_xlerobot.zmq_host import XLerobotZmq

    link = XLerobotZmq()
    monkeypatch.setattr(link, "_pump", _nothing_to_pump)
    link._frame_b64 = base64.b64encode(raw).decode("ascii")
    return await link.get_frame()


async def _toddlerbot(raw: bytes, monkeypatch: pytest.MonkeyPatch, _: Path) -> Any:
    from quackd_toddlerbot.bridge import ToddlerBotBridge

    async def request(*_: Any) -> dict[str, str]:
        return {"jpeg": base64.b64encode(raw).decode("ascii")}

    link = ToddlerBotBridge()
    link.camera_available = True
    monkeypatch.setattr(link, "request", request)
    return await link.get_frame()


async def _moss(raw: bytes, _: pytest.MonkeyPatch, __: Path) -> Any:
    from quackd_moss.cockpit import _decode

    return _decode(raw)


async def _open_duck(raw: bytes, monkeypatch: pytest.MonkeyPatch, _: Path) -> Any:
    from quackd_open_duck.bridge import OpenDuckBridge

    _answers_with(raw, monkeypatch)
    return await OpenDuckBridge(camera_url="http://127.0.0.1:9/snapshot.jpg").get_frame()


async def _microduck(raw: bytes, monkeypatch: pytest.MonkeyPatch, _: Path) -> Any:
    from quackd_microduck.transports import jsonrpc_unix

    monkeypatch.setattr(jsonrpc_unix, "fetch_still", lambda *a, **k: raw)
    link = jsonrpc_unix.JsonRpcUnixTransport(
        "tcp://127.0.0.1:9", camera_url="http://127.0.0.1:9/frame"
    )
    try:
        return await link._fetch_frame()
    except (ValueError, Image.DecompressionBombError):  # what `_camera_loop` keeps as the error
        return None


async def _huebot(raw: bytes, monkeypatch: pytest.MonkeyPatch, _: Path) -> Any:
    from quackd_huebot.body import HUEBOT
    from quackd_microduck.transports import jsonrpc_unix

    monkeypatch.setattr(jsonrpc_unix, "fetch_still", lambda *a, **k: raw)
    link = jsonrpc_unix.JsonRpcUnixTransport(
        "tcp://127.0.0.1:9", body=HUEBOT, camera_url="http://127.0.0.1:9/still.jpg"
    )
    try:
        return await link._fetch_frame()
    except (ValueError, Image.DecompressionBombError):  # what `_camera_loop` keeps as the error
        return None


async def _rosbridge(raw: bytes, _: pytest.MonkeyPatch, __: Path) -> Any:
    from quackd_rosbridge.ws import RosbridgeWs

    link = RosbridgeWs("ws://127.0.0.1:9")
    link._image = object()  # type: ignore[assignment]
    link._latest_image = {"format": "jpeg", "data": base64.b64encode(raw).decode("ascii")}
    return await link.get_frame()


async def _task_image(raw: bytes, _: pytest.MonkeyPatch, folder: Path) -> Any:
    picture = folder / "sketch"
    picture.write_bytes(raw)
    try:
        return load_task_images([str(picture)])
    except TaskImageError:
        return None


@dataclass(frozen=True)
class Site:
    name: str
    read: Reader
    carries: tuple[str, ...]


JPEG = ("JPEG",)
JPEG_OR_PNG = ("JPEG", "PNG")
"""A `--camera-url` names a server of the person's own, and rosbridge's message says which of the
two it holds. Microduck's `mediad` serves PNG, and Huebot's refusal already asks for one or the
other. Every other wire below is defined as JPEG by the host at the far end of it."""

SITES = [
    Site("the Jetson's snapshot", _host, JPEG),
    Site("the policy server's frames", _policy, JPEG),
    Site("alohamini", _alohamini, JPEG),
    Site("xlerobot", _xlerobot, JPEG),
    Site("toddlerbot", _toddlerbot, JPEG),
    Site("moss", _moss, JPEG),
    Site("open_duck", _open_duck, JPEG_OR_PNG),
    Site("microduck", _microduck, JPEG_OR_PNG),
    Site("huebot", _huebot, JPEG_OR_PNG),
    Site("rosbridge", _rosbridge, JPEG_OR_PNG),
    Site("--image", _task_image, FORMATS),
]
EACH_SITE = pytest.mark.parametrize("site", SITES, ids=[site.name for site in SITES])


class _Probe(ImageFile.ImageFile):
    """A format of nobody's, so a test can see whether a decoder nobody named was asked at all.

    Refusing a picture proves little where a site reads the format back after opening it: by
    then a decoder has already run, and most of Pillow's advisories fire right there."""

    format = "QUACKDPROBE"
    format_description = "a decoder no wire names"
    magic: ClassVar[bytes] = b"QUACKD-PROBE"
    opened: ClassVar[int] = 0

    def _open(self) -> None:
        type(self).opened += 1
        self._mode = "RGB"
        self._size = SIZE
        self.tile = []


@pytest.fixture
def probe() -> Iterator[type[_Probe]]:
    _Probe.opened = 0
    Image.register_open(_Probe.format, _Probe, lambda prefix: prefix.startswith(_Probe.magic))
    try:
        yield _Probe
    finally:
        Image.OPEN.pop(_Probe.format, None)
        Image.ID.remove(_Probe.format)


@EACH_SITE
async def test_a_wire_still_reads_every_format_it_carries(
    site: Site, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    for fmt in site.carries:
        assert await site.read(_picture(fmt), monkeypatch, tmp_path) is not None, (
            f"{site.name} carries {fmt} and no longer reads it"
        )


@EACH_SITE
async def test_a_wire_refuses_a_format_it_does_not_carry(
    site: Site, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A netpbm file decodes on every Pillow and is carried by no wire here."""
    assert await site.read(_picture("PPM"), monkeypatch, tmp_path) is None


@pytest.mark.parametrize("site", [s for s in SITES if "PNG" not in s.carries], ids=lambda s: s.name)
async def test_a_jpeg_wire_refuses_a_png(
    site: Site, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An animated PNG allocates what it claims inside `Image.open`, on any wire that lets it."""
    assert await site.read(_picture("PNG"), monkeypatch, tmp_path) is None


@EACH_SITE
async def test_no_decoder_a_wire_does_not_name_is_even_asked(
    site: Site, probe: type[_Probe], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    await site.read(probe.magic + bytes(64), monkeypatch, tmp_path)
    assert probe.opened == 0, f"{site.name} let a decoder it does not name open the bytes"


@EACH_SITE
async def test_a_frame_claiming_to_be_enormous_is_a_refusal_and_not_an_exception(
    site: Site, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Pillow raises `DecompressionBombError` from inside `Image.open`, and it is neither an
    `OSError` nor a `ValueError`. Five sites let it out of `get_frame`, whose contract is None, so
    one JPEG header from the far end could end a run."""
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 10)
    assert await site.read(_picture("JPEG"), monkeypatch, tmp_path) is None


def test_a_camera_url_that_serves_anything_else_is_told_what_to_serve() -> None:
    with pytest.raises(ValueError, match="a JPEG or a PNG"):
        camera_url_frame(_picture("BMP"))


async def test_the_refusal_is_what_a_person_reads_in_camera_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from quackd_open_duck.bridge import OpenDuckBridge

    _answers_with(_picture("BMP"), monkeypatch)
    link = OpenDuckBridge(camera_url="http://127.0.0.1:9/snapshot.jpg")
    assert await link.get_frame() is None
    assert "a JPEG or a PNG" in str(link.camera_error)


def _opens(path: Path) -> Iterator[ast.Call]:
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "open"
            and ast.unparse(node.func.value).endswith(("Image", "_image"))
        ):
            yield node


def test_every_image_open_that_ships_names_its_formats() -> None:
    """What a wheel or a robot runs. `docs/assets`, `web/` and `docs/examples` open a file the
    person running them chose, and no install runs them."""
    shipped = [
        *REPO.glob("quackd/**/*.py"),
        *REPO.glob("adapters/*/src/**/*.py"),
        *REPO.glob("bridge/**/*.py"),
    ]
    found = {path: list(_opens(path)) for path in shipped}
    assert found[REPO / "quackd" / "host.py"], "the walk no longer sees the call it is looking for"
    bare = [
        f"{path.relative_to(REPO)}:{call.lineno}"
        for path, calls in found.items()
        for call in calls
        if not any(keyword.arg == "formats" for keyword in call.keywords)
    ]
    assert not bare, f"Image.open with no formats=, so the bytes choose the decoder: {bare}"
