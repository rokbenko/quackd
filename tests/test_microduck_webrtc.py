"""The signalling exchange with `mediad`, driven by a fake socket and a fake peer connection.

What this can prove: that quackd sends the five messages gst-plugins-rs `net/webrtc` expects,
in the order the robot's own web client sends them, with the field shapes read off
`mediad/webclient/index.html`. Getting those wrong is the mistake that produces silence rather
than an error, which is exactly the mistake worth a test.

What it cannot prove: that a Microduck agrees. There is no H.264 here, no ICE, and no robot.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import pytest

from quackd.adapters.microduck.webrtc import WebRtcCamera, is_webrtc_url, signalling_url

OFFER = "v=0\r\no=- 0 0 IN IP4 127.0.0.1\r\n"


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


async def test_detections_are_read_off_the_control_channel_and_nothing_is_written_to_it(
    camera: WebRtcCamera,
) -> None:
    """mediad opens `control` at us whether we want it or not. Reading is all we do."""
    camera._on_control(
        json.dumps(
            {
                "jsonrpc": "2.0",
                "method": "media.detections",
                "params": {"width": 640, "height": 480, "boxes": [{"x0": 1, "score": 0.9}]},
            }
        )
    )
    assert camera.detections is not None
    assert camera.detections["width"] == 640
    camera._on_control("not json at all")  # must not raise
    assert camera.detections["width"] == 640


def test_health_says_what_is_wrong_before_a_frame_arrives(camera: WebRtcCamera) -> None:
    health = camera.health()
    assert health["configured"] is True and health["ok"] is False and health["frames"] == 0
    assert health["url"] == "webrtc://duck.local:8443"
