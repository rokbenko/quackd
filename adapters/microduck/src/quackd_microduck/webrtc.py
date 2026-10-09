"""Frames off a real Microduck over `mediad`'s WebRTC video track, turned upright.

At the pin `mediad` hands a picture out three ways: this track, one PNG per `GET /frame` on its
console port (`up.MEDIAD_FRAME_HTTP`), and one raw frame per `media.frame` on a socket only the
robot itself can reach (`up.MEDIA_FRAME`). The track is the stream, decoded here frame by frame,
and it takes this extra and a WebRTC peer. `docs/adapters/microduck/README.md` carries the
reference for all three.

The track is the picture as the camera took it. On an alpha the camera is mounted a quarter turn
off upright and nothing on the robot turns the pixels back (`up.CAMERA_MOUNT`), so each frame
comes in on its side, where how far across it a ball is says how high the ball is in the room
rather than where it is. `mediad` says how far to turn the picture, as `media.video`'s `rotate`
(`up.MEDIA_VIDEO`). This turns every frame clockwise by that much, the way upstream's own `uyvy`
crate does, and hands out no frame at all until `mediad` has said. The same answer carries the
lens, for the frame as it is sent, and `upright_fov_deg` turns that with the frame.

This runs **on your machine**, not on the robot, and installs nothing there. Past the signalling,
the one line it writes to the robot is the `media.video` question, on the control datachannel
`mediad` opens at every peer.

Signalling is gst-plugins-rs `net/webrtc`, read from `mediad/webclient/index.html` at the pin.
Five messages, and the robot offers rather than answers:

    <- welcome                        the server has given us an id
    -> {"type": "list"}               who is producing?
    <- list {producers: [...]}        mediad registers one when its pipeline reaches PLAYING
    -> {"type": "startSession", "peerId": <producer id>}
    <- sessionStarted {sessionId}
    <- peer {sessionId, sdp: {type: "offer", sdp}}
    -> peer {sessionId, sdp: {type: "answer", sdp}}
    <- peer {sessionId, ice: {candidate, sdpMLineIndex}}

Two things to know before trusting a frame from this. **One media session at a time**: pulling
video competes with anyone who has the browser console open, though control-only clients
coexist. And there is **no authentication**: upstream's own note is that a pairing PIN which is
`000000` on every robot "authenticates nobody", so anyone who can reach port 8443 can watch and
drive. Reach it over an ssh tunnel rather than across a room's wifi.

aiortc gathers ICE during `setLocalDescription` and has no trickle, so our candidates travel
inside the answer and this only ever *receives* `ice` messages. That is a difference from the
browser client, not a bug, but it does mean a network where gathering is slow shows up as a
long `start()` rather than as candidates arriving late.

**Nothing here has been run against a Microduck.** It is written from upstream's source, and
the failure it is most likely to hit first is ICE on a network that is not a home LAN.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import math
import time
from collections.abc import Mapping
from typing import Any, TypeGuard
from urllib.parse import urlparse

from PIL import Image

from quackd_microduck import upstream_api as up

SCHEME = "webrtc"
DEFAULT_PORT = 8443
#: Long enough for ICE on an unhelpful network, short enough to fail a bring-up check rather
#: than hang it.
DEFAULT_TIMEOUT_S = 15.0

#: How to stand a frame upright, by `media.video`'s `rotate`: the degrees clockwise the camera is
#: mounted from upright, which is how far clockwise the frame has to turn (`up.CAMERA_MOUNT`).
#: PIL's transposes turn counterclockwise, so a quarter turn clockwise is its ROTATE_270. mediad
#: names no other angle: it refuses to start on one.
UPRIGHT: dict[int, Image.Transpose | None] = {
    0: None,
    90: Image.Transpose.ROTATE_270,
    180: Image.Transpose.ROTATE_180,
    270: Image.Transpose.ROTATE_90,
}

#: The id of the one request written to the control channel, the `media.video` question.
VIDEO_REQUEST_ID = 1

#: What `health()` shows of `media.video`'s intrinsics: the numbers and whose solve they are.
INTRINSICS_SHOWN = ("fx", "fy", "cx", "cy", "calibrated", "source")

#: Why `start()` reports no camera when frames came and the turn never did.
NO_MOUNT = (
    f"frames arrived, but mediad never said how its camera is mounted ({up.MEDIA_VIDEO.name}'s "
    "rotate), so quackd hands none out: on an alpha they would be on their side"
)

log = logging.getLogger("quackd.microduck.webrtc")

_MISSING = (
    "the WebRTC camera needs aiortc, av and websockets, which are not installed. "
    "Install them with `pip install 'quackd[microduck-camera]'`. "
    "The alternative that needs no extra is an HTTP snapshot server and "
    "`--camera-url http://.../snapshot.jpg`."
)


def is_webrtc_url(url: str | None) -> bool:
    return bool(url) and str(url).startswith(f"{SCHEME}://")


def signalling_url(address: str) -> str:
    """`webrtc://duck.local:8443` -> `ws://duck.local:8443`, the signalling socket."""
    parsed = urlparse(address)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or DEFAULT_PORT
    return f"ws://{host}:{port}"


def _quarter_turn(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value in UPRIGHT


def _positive(value: Any) -> TypeGuard[float]:
    return (
        isinstance(value, int | float)
        and not isinstance(value, bool)
        and math.isfinite(value)
        and value > 0
    )


def upright_fov_deg(video: Mapping[str, Any]) -> float | None:
    """The horizontal field of view of an upright frame, in degrees, from what `media.video`
    said, or None when it did not say enough to tell: no quarter turn, no intrinsics, or numbers
    that are not a size and a focal length.

    The intrinsics are for the frame as it is sent, which nothing on the robot turns, so a
    quarter turn swaps the axes (`up.MEDIA_VIDEO`): the upright frame is as wide as the sent one
    is tall, and the focal length across it is `fy`. Half that width over the focal length is the
    tangent of half the lens. An angle holds at whatever size the frames are decoded at, and the
    detector works the focal length back out of it at the width it is handed, which comes to
    mediad's own, scaled. Where the principal point sits is not in it: the detector takes it to
    be the centre."""
    rotate, intrinsics = video.get("rotate"), video.get("intrinsics")
    if not _quarter_turn(rotate) or not isinstance(intrinsics, Mapping):
        return None
    across, focal = ("height", "fy") if rotate in (90, 270) else ("width", "fx")
    width, f = video.get(across), intrinsics.get(focal)
    if not (_positive(width) and _positive(f)):
        return None
    return math.degrees(2 * math.atan(float(width) / 2 / float(f)))


class WebRtcCamera:
    """One video track, decoded and turned upright, newest frame kept. Not a control path: it
    never sends an intent.

    `mediad` opens a `control` datachannel at us whether we want one or not (the robot creates
    it, so a client that opens nothing still gets a control surface). Two things are read off
    it: `media.video`, which says how far to turn the picture and what the lens is, and
    `media.detections`, which costs nothing and is the perception `mediad` runs itself. One
    thing is written to it, once: the `media.video` question. Motion goes over `robotd`'s
    socket, where the executor, the allowlist and the deadman feed already live, and having two
    ways to move the robot would mean two places to be sure about.
    """

    def __init__(self, address: str, *, timeout_s: float = DEFAULT_TIMEOUT_S) -> None:
        self.address = address
        self.url = signalling_url(address)
        self.timeout_s = timeout_s
        self.frames = 0
        self.error: str | None = None
        self.producer: dict[str, Any] | None = None
        self.detections: dict[str, Any] | None = None
        self.video: dict[str, Any] | None = None
        """What `mediad` last said the video is, `media.video`'s answer or its push, or None until
        it has said either."""
        self.rotate: int | None = None
        """How far clockwise a frame turns to stand upright, once `mediad` has said a quarter turn,
        or None. No frame is handed out while it is None."""
        self._frame: Image.Image | None = None
        self._frame_at: float | None = None
        self._upright: tuple[Image.Image, int, Image.Image] | None = None
        """The newest frame asked for, turned upright, beside the decoded frame and the turn it
        came from (`_turned`)."""
        self._task: asyncio.Task[None] | None = None
        self._tracks: set[asyncio.Task[None]] = set()
        self._first = asyncio.Event()
        self._settled = asyncio.Event()
        """Set when there is an answer either way: a frame quackd can stand upright, or none it
        ever will."""
        self._pc: Any = None
        self._closing = False

    # ── what the transport uses ─────────────────────────────────────────────────────────

    def latest(self, *, max_age_s: float | None = None) -> Image.Image | None:
        """The newest frame, upright, or None if there is none to steer on: none has decoded, it
        is too old, or `mediad` has not said how far to turn it.

        A session that dies leaves its last frame in memory. Handing that out forever is how a
        stopped video turns into a robot walking at something that is no longer there. And a
        frame handed out before the turn is known may be on its side, which `go_to` would steer
        on as if a ball's height in the room were its bearing.
        """
        frame, rotate = self._frame, self.rotate
        if frame is None or self._frame_at is None or rotate is None:
            return None
        if max_age_s is not None and time.monotonic() - self._frame_at > max_age_s:
            return None
        return self._turned(frame, rotate)

    @property
    def ready(self) -> bool:
        """Whether a frame has decoded and `mediad` has said how far to turn it."""
        return self._frame is not None and self.rotate is not None

    @property
    def fov_deg(self) -> float | None:
        """How wide an upright frame is, in degrees, by `mediad`'s own intrinsics, or None until
        it has said how far to turn one, and when it has no geometry to give (`upright_fov_deg`)."""
        if self.video is None or self.rotate is None:
            return None
        return upright_fov_deg(self.video)

    async def start(self, *, wait: bool = True) -> bool:
        """Open the session. With `wait`, resolve once a frame decodes and `mediad` has said how
        far to turn it, or once that cannot happen.

        Every terminal failure sets `_settled` as well: a missing extra, a refused connection,
        `mediad` with no producer, the robot ending the session. Waiting out the full timeout
        for an answer already in hand is how a bring-up check that says it will "say so rather
        than hang" hangs for fifteen seconds.

        A frame with no word on the turn is not a camera yet, since it may be on its side, so the
        wait is for both. When it runs out on frames alone, `error` says so.
        """
        self._task = asyncio.create_task(self._run(), name="quackd-microduck-webrtc")
        if not wait:
            return False
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self._settled.wait(), timeout=self.timeout_s)
        if self._frame is not None and self.rotate is None and self.error is None:
            self.error = NO_MOUNT
        return self.ready

    async def close(self) -> None:
        self._closing = True
        for task in (self._task, *self._tracks):
            if task is None:
                continue
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        self._task = None
        self._tracks.clear()
        if self._pc is not None:
            with contextlib.suppress(Exception):
                await self._pc.close()
            self._pc = None

    def health(self) -> dict[str, Any]:
        age = None if self._frame_at is None else round(time.monotonic() - self._frame_at, 2)
        upright = self.latest()
        fov = self.fov_deg
        intrinsics = (self.video or {}).get("intrinsics")
        return {
            "configured": True,
            "url": self.address,
            "ok": upright is not None,
            "age_s": age,
            "frames": self.frames,
            "size": list(upright.size) if upright is not None else None,
            "rotate": self.rotate,
            "fov_deg": None if fov is None else round(fov, 1),
            "intrinsics": (
                {key: intrinsics.get(key) for key in INTRINSICS_SHOWN}
                if isinstance(intrinsics, Mapping)
                else None
            ),
            "producer": (self.producer or {}).get("id"),
            "error": self.error,
        }

    # ── the session ─────────────────────────────────────────────────────────────────────

    async def _run(self) -> None:
        try:
            websockets = _websockets()
            async with websockets.connect(self.url) as ws:
                await self._pump(ws)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            if not self._closing:
                self.error = str(e)
                log.warning("webrtc camera: %s", e)
        finally:
            # Whatever happened, there is nothing more to wait for: the socket is closed and
            # no frame is coming from this attempt.
            self._settled.set()

    async def _pump(self, ws: Any) -> None:
        """The five-message exchange. Split out so it can be driven by a fake socket."""
        session: str | None = None
        async for raw in ws:
            msg = json.loads(raw)
            kind = msg.get("type")
            if kind == "welcome":
                await ws.send(json.dumps({"type": "list"}))
            elif kind == "list":
                producers = msg.get("producers") or []
                if not producers:
                    self.error = (
                        "mediad is reachable but registered no producer, which means its "
                        "pipeline never reached PLAYING — check its journal on the robot"
                    )
                    return
                self.producer = producers[0]
                # No `offer` field, so the producer offers and we answer: the direction
                # webrtcsink wants, since it knows what it is sending.
                await ws.send(
                    json.dumps({"type": "startSession", "peerId": self.producer.get("id")})
                )
            elif kind == "sessionStarted":
                session = msg.get("sessionId")
                self._pc = self._new_peer_connection()
            elif kind == "peer":
                await self._on_peer(ws, msg, session)
            elif kind == "endSession":
                self.error = "the robot ended the session"
                return

    async def _on_peer(self, ws: Any, msg: dict[str, Any], session: str | None) -> None:
        if self._pc is None:
            return
        if sdp := msg.get("sdp"):
            answer = await self._answer(sdp)
            await ws.send(
                json.dumps(
                    {"type": "peer", "sessionId": session, "sdp": {"type": "answer", "sdp": answer}}
                )
            )
        elif ice := msg.get("ice"):
            with contextlib.suppress(Exception):
                await self._pc.addIceCandidate(_candidate(ice))

    async def _answer(self, sdp: dict[str, Any]) -> str:
        aiortc = _aiortc()
        await self._pc.setRemoteDescription(
            aiortc.RTCSessionDescription(sdp=sdp["sdp"], type=sdp["type"])
        )
        answer = await self._pc.createAnswer()
        await self._pc.setLocalDescription(answer)
        return str(self._pc.localDescription.sdp)

    def _new_peer_connection(self) -> Any:
        aiortc = _aiortc()
        pc = aiortc.RTCPeerConnection()

        @pc.on("track")
        def _on_track(track: Any) -> None:
            if track.kind == "video":
                # Keep the reference: the event loop holds only a weak one, so a decode task
                # nobody is holding can be collected mid-session and the video simply stops.
                task = asyncio.create_task(self._drain(track), name="quackd-microduck-decode")
                self._tracks.add(task)
                task.add_done_callback(self._tracks.discard)

        @pc.on("datachannel")
        def _on_datachannel(channel: Any) -> None:
            self._on_channel(channel)

        return pc

    def _on_channel(self, channel: Any) -> None:
        """The control datachannel the robot opens at us: read everything that comes on it, and
        ask it once what the video is.

        `mediad` pushes `media.video` when the channel opens as well, but its own doc says that
        push races the peer and can be lost, which is why upstream's console asks and this does
        too (`up.MEDIA_VIDEO`). aiortc hands over a channel the robot opened already open; one
        that is not yet is asked the moment it opens."""

        @channel.on("message")
        def _on_message(data: Any) -> None:
            self._on_control(data)

        if channel.readyState == "open":
            self._ask_video(channel)
        else:

            @channel.on("open")
            def _on_open() -> None:
                self._ask_video(channel)

    def _ask_video(self, channel: Any) -> None:
        """Write the `media.video` question, the one line this writes to the robot, as upstream's
        console writes it."""
        request = {
            "jsonrpc": up.JSONRPC_VERSION,
            "id": VIDEO_REQUEST_ID,
            "method": up.MEDIA_VIDEO.name,
            "params": {},
        }
        try:
            channel.send(json.dumps(request))
        except Exception as e:  # a channel closing under us; the push may still come
            log.warning("webrtc camera: could not ask mediad what its video is: %s", e)

    async def _drain(self, track: Any) -> None:
        """Decode frames until the track ends. The newest one is all anybody wants."""
        while True:
            try:
                frame = await track.recv()
            except Exception:  # the track ended, or the session did
                return
            self._took_frame(frame.to_image())

    def _took_frame(self, image: Image.Image) -> None:
        """Keep a decoded frame as the newest, as it came off the track: it is turned when it is
        asked for (`_turned`)."""
        self._frame = image
        self._frame_at = time.monotonic()
        self.frames += 1
        self._first.set()
        if self.rotate is not None:
            self._settled.set()

    def _turned(self, frame: Image.Image, rotate: int) -> Image.Image:
        """`frame` turned upright by `rotate`. Turned when it is asked for rather than as it
        decodes, since the track decodes up to thirty frames a second and `go_to` reads ten, and
        kept, since `doctor`, the state and a verb may each ask for the same frame."""
        turn = UPRIGHT[rotate]
        if turn is None:
            return frame
        kept = self._upright
        if kept is not None and kept[0] is frame and kept[1] == rotate:
            return kept[2]
        upright = frame.transpose(turn)
        self._upright = (frame, rotate, upright)
        return upright

    def _on_control(self, data: Any) -> None:
        """One line off the control channel. Nothing on it is taken to be well formed, and
        nothing raises out of here, which aiortc calls."""
        with contextlib.suppress(Exception):
            msg = json.loads(data)
            if not isinstance(msg, dict):
                return
            method = msg.get("method")
            if method == up.MEDIA_VIDEO.name:  # the push, when the channel opened
                self._took_video(msg.get("params"))
            elif method == up.FEATURE_STREAM.name:
                self.detections = msg.get("params") or {}
            elif method is None and msg.get("id") == VIDEO_REQUEST_ID:  # the answer
                if "result" in msg:
                    self._took_video(msg.get("result"))
                elif "error" in msg:
                    self._video_refused(msg.get("error"))

    def _took_video(self, video: Any) -> None:
        """What `mediad` says the video is. A `rotate` that is not a quarter turn is no turn
        quackd can make, so no frame goes out on it."""
        if not isinstance(video, dict):
            return
        self.video = dict(video)
        rotate = video.get("rotate")
        if _quarter_turn(rotate):
            self.rotate = rotate
            if self.error == NO_MOUNT:  # said late, after `start()` gave up waiting for it
                self.error = None
            if self._frame is not None:
                self._settled.set()
            return
        self.rotate = None
        self.error = (
            f"mediad says its camera is mounted {rotate!r} degrees from upright, which is not a "
            "quarter turn quackd can undo, so it hands out no frame"
        )
        self._settled.set()

    def _video_refused(self, error: Any) -> None:
        """`mediad` answered the question with an error, which it does when it has no video to
        describe. A push that already said how to turn the frames still stands."""
        if self.rotate is not None:
            return
        said = error.get("message") if isinstance(error, dict) else None
        self.error = f"mediad would not say what its video is: {said or error!r}"
        self._settled.set()


# ── the optional dependencies, named once ───────────────────────────────────────────────


def _aiortc() -> Any:
    try:
        import aiortc
    except ImportError as e:  # pragma: no cover - exercised by the message, not the path
        raise RuntimeError(_MISSING) from e
    return aiortc


def _websockets() -> Any:
    try:
        import websockets.asyncio.client as client
    except ImportError as e:  # pragma: no cover
        raise RuntimeError(_MISSING) from e
    return client


def _candidate(ice: dict[str, Any]) -> Any:
    """Build an aiortc candidate from the browser-shaped `{candidate, sdpMLineIndex}`."""
    from aiortc.sdp import candidate_from_sdp

    raw = str(ice.get("candidate") or "")
    parsed = candidate_from_sdp(raw.removeprefix("candidate:"))
    parsed.sdpMLineIndex = ice.get("sdpMLineIndex")
    return parsed
