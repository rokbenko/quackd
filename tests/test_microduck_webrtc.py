"""The signalling exchange with `mediad`, driven by a fake socket and a fake peer connection,
and what is done with the frames and the control channel once a session is up.

What this can prove: that quackd sends the five messages gst-plugins-rs `net/webrtc` expects,
in the order the robot's own web client sends them, with the field shapes read off
`mediad/webclient/index.html`. Getting those wrong is the mistake that produces silence rather
than an error, which is exactly the mistake worth a test. And that a frame comes out turned the
way upstream's own code turns one, by the `rotate` `mediad` says, never before it has said, with
the lens its intrinsics give: getting the turn wrong is a bearing on the wrong axis, or mirrored.

What it cannot prove: that a Microduck agrees. There is no H.264 here, no ICE, and no robot.
"""

from __future__ import annotations

import asyncio
import json
import math
import time
from collections.abc import AsyncIterator, Callable
from typing import Any

import pytest
from PIL import Image

from quackd_microduck import upstream_api as up
from quackd_microduck.transports.jsonrpc_unix import JsonRpcUnixTransport
from quackd_microduck.webrtc import (
    INTRINSICS_SHOWN,
    NO_MOUNT,
    WebRtcCamera,
    is_webrtc_url,
    signalling_url,
    upright_fov_deg,
)

OFFER = "v=0\r\no=- 0 0 IN IP4 127.0.0.1\r\n"
#: The four angles `media.video`'s `rotate` can be, the degrees clockwise the camera is mounted
#: from upright: mediad refuses to start on any other.
QUARTER_TURNS = (0, 90, 180, 270)


class FakeSocket:
    """Feeds the pump a scripted conversation and records what it sends back."""

    def __init__(self, incoming: list[dict[str, Any]]) -> None:
        self.incoming = incoming
        self.sent: list[dict[str, Any]] = []

    async def send(self, raw: str) -> None:
        self.sent.append(json.loads(raw))

    async def __aiter__(self) -> AsyncIterator[str]:
        for msg in self.incoming:
            yield json.dumps(msg)


class FakePeerConnection:
    def __init__(self) -> None:
        self.remote: Any = None
        self.ice: list[Any] = []
        self.localDescription = type("D", (), {"sdp": "v=0\r\nANSWER\r\n"})()

    async def setRemoteDescription(self, desc: Any) -> None:
        self.remote = desc

    async def createAnswer(self) -> Any:
        return object()

    async def setLocalDescription(self, answer: Any) -> None:
        return None

    async def addIceCandidate(self, candidate: Any) -> None:
        self.ice.append(candidate)


class FakeChannel:
    """The `control` datachannel `mediad` opens at a peer, as aiortc hands it over: already open
    unless told otherwise, with an `on` decorator and a `send` that records each line."""

    def __init__(self, state: str = "open") -> None:
        self.readyState = state
        self.sent: list[dict[str, Any]] = []
        self.handlers: dict[str, Callable[..., None]] = {}

    def on(self, event: str) -> Callable[[Callable[..., None]], Callable[..., None]]:
        def register(handler: Callable[..., None]) -> Callable[..., None]:
            self.handlers[event] = handler
            return handler

        return register

    def send(self, data: str) -> None:
        self.sent.append(json.loads(data))

    def deliver(self, msg: Any) -> None:
        """A line arriving from mediad, as aiortc delivers a text message."""
        self.handlers["message"](msg if isinstance(msg, str) else json.dumps(msg))

    def opens(self) -> None:
        self.readyState = "open"
        self.handlers["open"]()


def _video(rotate: int, **more: Any) -> dict[str, Any]:
    """`media.video`'s params as mediad builds them (mediad/src/session.rs:97-114), for a frame
    sent at 1280 by 720, mounted `rotate` degrees clockwise from upright."""
    return {
        "width": 1280,
        "height": 720,
        "rotate": rotate,
        "mono_ns": 1,
        "real_ns": 2,
        **more,
    }


def _pushed(video: dict[str, Any]) -> dict[str, Any]:
    """The notification mediad pushes when the channel opens (session.rs:82-91)."""
    return {"jsonrpc": "2.0", "method": up.MEDIA_VIDEO.name, "params": video}


def _answered(request: dict[str, Any], video: dict[str, Any]) -> dict[str, Any]:
    """mediad's answer to the question quackd asked, with the id it was asked with
    (session.rs:167-172)."""
    return {"jsonrpc": "2.0", "id": request["id"], "result": video}


def _numbered(width: int, height: int) -> Image.Image:
    """A frame whose every pixel is a different colour, so a turn that puts any pixel anywhere but
    where it belongs shows."""
    image = Image.new("RGB", (width, height))
    for y in range(height):
        for x in range(width):
            image.putpixel((x, y), (x * 40 % 256, y * 80 % 256, x + y * width))
    return image


def _uyvy_source(rotate: int, ux: int, uy: int, width: int, height: int) -> tuple[int, int]:
    """`uyvy::Turn::source` at the pin (uyvy/src/lib.rs:64-80): where a pixel of the upright
    picture is in the frame the camera took, for a frame `width` by `height` as it was sent and the
    turn `Turn::from_degrees(rotate)` names. It is what mediad stands its PNGs and its duck
    detector's input up with, written out here as upstream wrote it, so that quackd's turn is held
    to upstream's arithmetic and not to PIL's."""
    if rotate == 0:
        return ux, uy
    if rotate == 90:  # Turn::Right, a quarter turn clockwise
        return uy, height - 1 - ux
    if rotate == 180:  # Turn::Half
        return width - 1 - ux, height - 1 - uy
    return width - 1 - uy, ux  # Turn::Left


@pytest.fixture
def camera(monkeypatch: pytest.MonkeyPatch) -> WebRtcCamera:
    cam = WebRtcCamera("webrtc://duck.local:8443")
    monkeypatch.setattr(cam, "_new_peer_connection", FakePeerConnection)
    monkeypatch.setattr(cam, "_answer", _fake_answer.__get__(cam))
    return cam


async def _fake_answer(self: WebRtcCamera, sdp: dict[str, Any]) -> str:
    await self._pc.setRemoteDescription(sdp)
    return "v=0\r\nANSWER\r\n"


def test_webrtc_urls_are_recognised_and_become_signalling_sockets() -> None:
    assert is_webrtc_url("webrtc://duck.local:8443")
    assert not is_webrtc_url("http://duck.local:9872/snapshot.jpg")
    assert not is_webrtc_url(None)
    assert signalling_url("webrtc://duck.local:8443") == "ws://duck.local:8443"
    assert signalling_url("webrtc://duck.local") == "ws://duck.local:8443"  # mediad's port


async def test_the_five_messages_go_out_in_the_order_the_robot_expects(
    camera: WebRtcCamera,
) -> None:
    ws = FakeSocket(
        [
            {"type": "welcome", "peerId": "us"},
            {"type": "list", "producers": [{"id": "duck-0", "meta": {"name": "microduck"}}]},
            {"type": "sessionStarted", "sessionId": "s1"},
            {"type": "peer", "sessionId": "s1", "sdp": {"type": "offer", "sdp": OFFER}},
            {"type": "peer", "sessionId": "s1", "ice": {"candidate": "", "sdpMLineIndex": 0}},
        ]
    )
    await camera._pump(ws)

    assert [m["type"] for m in ws.sent] == ["list", "startSession", "peer"]
    assert ws.sent[1] == {"type": "startSession", "peerId": "duck-0"}
    # The producer offers and we answer — the direction webrtcsink wants.
    assert ws.sent[2] == {
        "type": "peer",
        "sessionId": "s1",
        "sdp": {"type": "answer", "sdp": "v=0\r\nANSWER\r\n"},
    }
    assert camera.producer == {"id": "duck-0", "meta": {"name": "microduck"}}


async def test_a_robot_with_no_producer_says_why(camera: WebRtcCamera) -> None:
    """The pipeline not reaching PLAYING is the likeliest failure, and looks like nothing."""
    ws = FakeSocket([{"type": "welcome"}, {"type": "list", "producers": []}])
    await camera._pump(ws)
    assert camera.error is not None and "PLAYING" in camera.error
    assert [m["type"] for m in ws.sent] == ["list"]


async def test_the_robot_ending_the_session_is_recorded(camera: WebRtcCamera) -> None:
    ws = FakeSocket(
        [
            {"type": "welcome"},
            {"type": "list", "producers": [{"id": "duck-0"}]},
            {"type": "sessionStarted", "sessionId": "s1"},
            {"type": "endSession", "sessionId": "s1"},
        ]
    )
    await camera._pump(ws)
    assert camera.error == "the robot ended the session"


async def test_the_one_line_written_to_the_control_channel_is_the_video_question(
    camera: WebRtcCamera,
) -> None:
    """mediad opens `control` at us whether we want it or not. quackd reads what comes on it and
    writes one line, once: `media.video`, asked the way upstream's console asks it, because the
    push mediad sends when the channel opens can be lost. Motion never goes this way."""
    channel = FakeChannel()
    camera._on_channel(channel)
    assert channel.sent == [
        {"jsonrpc": "2.0", "id": channel.sent[0]["id"], "method": up.MEDIA_VIDEO.name, "params": {}}
    ]
    channel.deliver(
        {
            "jsonrpc": "2.0",
            "method": up.FEATURE_STREAM.name,
            "params": {"width": 640, "height": 480, "boxes": [{"x0": 1, "score": 0.9}]},
        }
    )
    assert camera.detections is not None
    assert camera.detections["width"] == 640
    channel.deliver("not json at all")  # must not raise
    channel.deliver({"jsonrpc": "2.0", "id": 99, "result": _video(90)})  # nobody asked id 99
    assert camera.detections["width"] == 640 and camera.rotate is None
    assert len(channel.sent) == 1, "nothing else is ever written to the channel"

    late = FakeChannel(state="connecting")  # a channel not open yet is asked once it opens
    camera._on_channel(late)
    assert late.sent == []
    late.opens()
    assert [m["method"] for m in late.sent] == [up.MEDIA_VIDEO.name]


@pytest.mark.parametrize("how", ["answer", "push"])
async def test_how_far_to_turn_a_frame_is_read_off_the_answer_and_off_the_push(
    camera: WebRtcCamera, how: str
) -> None:
    """mediad pushes `media.video` when the channel opens and answers it when asked, with the
    same params either way, and whichever arrives is what quackd turns frames by."""
    channel = FakeChannel()
    camera._on_channel(channel)
    video = _video(90, intrinsics={"fx": 1000.0, "fy": 1000.0, "cx": 640.0, "cy": 360.0})
    channel.deliver(_answered(channel.sent[0], video) if how == "answer" else _pushed(video))
    assert camera.rotate == 90 and camera.video == video
    camera._took_frame(Image.new("RGB", (1280, 720)))
    assert camera.ready and camera.latest() is not None


@pytest.mark.parametrize("rotate", QUARTER_TURNS)
def test_every_quarter_turn_stands_a_frame_up_as_upstream_does(
    camera: WebRtcCamera, rotate: int
) -> None:
    """`rotate` is the degrees clockwise the camera is mounted from upright, and every pixel of
    the frame handed out is where upstream's own turn puts it: on an alpha a quarter turn
    clockwise, source (x, y) to upright (h-1-y, x). Turned the other way the picture is mirrored
    upright, and every bearing comes out on the wrong side."""
    width, height = 5, 3
    taken = _numbered(width, height)
    camera._took_video(_video(rotate))
    camera._took_frame(taken)
    upright = camera.latest()
    assert upright is not None
    quarter = rotate in (90, 270)
    assert upright.size == ((height, width) if quarter else (width, height))
    for uy in range(upright.height):
        for ux in range(upright.width):
            source = _uyvy_source(rotate, ux, uy, width, height)
            assert upright.getpixel((ux, uy)) == taken.getpixel(source), (rotate, ux, uy)
    if rotate:
        assert camera.latest() is upright, "a frame is turned once, however often it is read"
        camera._took_frame(_numbered(width, height))
        assert camera.latest() is not upright, "and a newer frame is turned anew"


async def test_no_frame_is_handed_out_before_mediad_says_how_to_turn_it(
    camera: WebRtcCamera, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A frame off an alpha is on its side until it is turned, and `go_to` would steer on how
    high a ball is as if it were where the ball is. So no frame goes out, from `latest` or as a
    working camera at connect, until mediad has said a quarter turn, and a session whose frames
    came without one ends its wait saying so, rather than reporting a camera."""
    camera.timeout_s = 0.3

    async def frames_alone() -> None:
        camera._took_frame(_numbered(4, 2))

    monkeypatch.setattr(camera, "_run", frames_alone)
    assert await camera.start() is False
    assert camera.latest() is None and camera.health()["ok"] is False
    assert camera.error == NO_MOUNT and up.MEDIA_VIDEO.name in NO_MOUNT

    camera._took_video(_video(90))  # mediad says, late: the frame goes out from then on
    assert camera.latest() is not None and camera.health()["ok"] is True
    assert camera.error is None, "and the word that it never said is gone"

    for unturnable in (45, -90, "90", 90.0, None, True):
        odd = WebRtcCamera("webrtc://duck.local:8443")
        odd._took_frame(_numbered(4, 2))
        odd._took_video(_video(0) | {"rotate": unturnable})
        assert odd.latest() is None and odd._settled.is_set(), unturnable
        assert odd.error is not None and "quarter turn" in odd.error, unturnable


@pytest.mark.parametrize("video_first", [False, True], ids=["frame-first", "video-first"])
async def test_a_camera_is_working_once_a_frame_and_the_turn_are_both_in(
    monkeypatch: pytest.MonkeyPatch, video_first: bool
) -> None:
    """Either can come first, and the wait ends on the second, well before the timeout."""
    camera = WebRtcCamera("webrtc://duck.local:8443", timeout_s=60.0)
    steps: list[Callable[[], None]] = [
        lambda: camera._took_frame(_numbered(4, 2)),
        lambda: camera._took_video(_video(90)),
    ]
    if video_first:
        steps.reverse()

    async def session() -> None:
        steps[0]()
        await asyncio.sleep(0.05)
        assert not camera._settled.is_set(), "one of the two is not a camera yet"
        steps[1]()
        await asyncio.sleep(3600)  # the session goes on, as a real one does

    monkeypatch.setattr(camera, "_run", session)
    started = time.monotonic()
    assert await camera.start() is True
    assert time.monotonic() - started < camera.timeout_s / 2
    assert camera.error is None
    await camera.close()


async def test_mediad_refusing_the_question_ends_the_wait_unless_it_said_already(
    camera: WebRtcCamera,
) -> None:
    """mediad answers `media.video` with an error when it has no video to describe. That is no
    camera, said at once. A push that already said how to turn the frames still stands."""
    channel = FakeChannel()
    camera._on_channel(channel)
    refusal = {"code": -32603, "message": "this robot is not publishing video"}
    channel.deliver({"jsonrpc": "2.0", "id": channel.sent[0]["id"], "error": refusal})
    assert camera._settled.is_set() and camera.rotate is None
    assert camera.error is not None and refusal["message"] in camera.error

    told = WebRtcCamera("webrtc://duck.local:8443")
    told_channel = FakeChannel()
    told._on_channel(told_channel)
    told_channel.deliver(_pushed(_video(90)))
    told_channel.deliver({"jsonrpc": "2.0", "id": told_channel.sent[0]["id"], "error": refusal})
    assert told.rotate == 90 and told.error is None


@pytest.mark.parametrize("rotate", QUARTER_TURNS)
def test_the_lens_of_an_upright_frame_is_mediads_own_turned_with_it(rotate: int) -> None:
    """The intrinsics are for the frame as mediad sends it, which nothing on the robot turns, so a
    quarter turn swaps the axes: the upright width is the sent height, across `fy`. With every
    number different, the one used for each turn shows."""
    width, height, fx, fy = 400, 300, 250.0, 200.0
    video: dict[str, Any] = {"width": width, "height": height, "rotate": rotate}
    video["intrinsics"] = {"fx": fx, "fy": fy, "cx": 210.0, "cy": 140.0}
    across, focal = (height, fy) if rotate in (90, 270) else (width, fx)
    assert upright_fov_deg(video) == pytest.approx(math.degrees(2 * math.atan(across / 2 / focal)))

    camera = WebRtcCamera("webrtc://duck.local:8443")
    assert camera.fov_deg is None, "nothing said yet, so no lens"
    camera._took_video(video)
    assert camera.fov_deg == upright_fov_deg(video)

    # absent rather than null when mediad does not know its geometry, and then there is no lens
    assert upright_fov_deg({k: v for k, v in video.items() if k != "intrinsics"}) is None
    for broken in (0, -fy, math.inf, math.nan, None, "200", True):
        assert upright_fov_deg(video | {"intrinsics": {"fx": broken, "fy": broken}}) is None
    assert upright_fov_deg(video | {"rotate": 45}) is None


def test_an_upright_alpha_frame_is_as_wide_as_upstreams_page_and_quackds_say() -> None:
    """The alpha family's calibration, solved on the frame as mediad sends it, gives the lens of
    an upright alpha frame at the alpha's own mount, and the number quackd states for it is that
    one, not a figure typed in."""
    solve = up.ALPHA_SOLVE
    video = {
        "width": solve["width"],
        "height": solve["height"],
        "rotate": 90,  # the Zero 3W's mount (up.CAMERA_MOUNT)
        "intrinsics": {k: solve[k] for k in ("fx", "fy", "cx", "cy")},
    }
    fov = upright_fov_deg(video)
    assert fov is not None
    assert f"{fov:.1f} degrees" in up.ALPHA_INTRINSICS.note
    assert f"atan({solve['height'] / 2:g}/fy)" in up.ALPHA_INTRINSICS.note


def test_health_says_how_far_it_turns_and_what_lens_it_measures_through(
    camera: WebRtcCamera,
) -> None:
    intrinsics = {
        "fx": 1000.0,
        "fy": 990.0,
        "cx": 600.0,
        "cy": 470.0,
        "calibrated": True,
        "source": "family",
        "distortion": [0.1, 0.2],
    }
    camera._took_video(_video(90, intrinsics=intrinsics))
    camera._took_frame(Image.new("RGB", (1280, 720)))
    health = camera.health()
    assert health["ok"] is True and health["size"] == [720, 1280]  # as handed out, upright
    assert health["rotate"] == 90 and health["fov_deg"] == round(camera.fov_deg or 0.0, 1)
    assert health["intrinsics"] == {key: intrinsics[key] for key in INTRINSICS_SHOWN}


def test_the_transport_reports_the_lens_its_webrtc_camera_measures_through() -> None:
    """`MicroduckAdapter.connect` reads the lens off the transport, and over WebRTC it is the
    camera's own: what `mediad` said, turned with the frame."""
    url = "webrtc://127.0.0.1:8443"
    transport = JsonRpcUnixTransport("tcp://127.0.0.1:1", camera_url=url)
    assert transport.camera_fov_deg is None
    camera = WebRtcCamera(url)
    camera._took_video(_video(90, intrinsics={"fx": 1000.0, "fy": 990.0}))
    transport._webrtc = camera
    assert transport.camera_fov_deg == camera.fov_deg is not None


def test_health_says_what_is_wrong_before_a_frame_arrives(camera: WebRtcCamera) -> None:
    health = camera.health()
    assert health["configured"] is True and health["ok"] is False and health["frames"] == 0
    assert health["url"] == "webrtc://duck.local:8443"
    assert health["rotate"] is None and health["fov_deg"] is None and health["size"] is None
