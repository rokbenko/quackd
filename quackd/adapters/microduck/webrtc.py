"""Frames off a real Microduck, over the only path upstream actually offers: `mediad`'s WebRTC.

`duck-ipc-proto` has no camera method at all, `robotctl` and `duckctl` have no camera
subcommand, and `mediad`'s HTTP port serves exactly one route: the console page. The camera
reaches clients as an H.264 track over WebRTC and nowhere else, so getting a picture into quackd
means being a WebRTC peer. `docs/adapter-status.md` carries the reference for that.

This runs **on your machine**, not on the robot. That is the point: a Microduck at somebody
else's desk is not a robot you install daemons on or stop services on, and the alternative — a
snapshot server on the duck — needs `sudo systemctl stop mediad` first, because `mediad`'s
`v4l2src` holds `/dev/video0` for the life of the process. Nothing here writes to the robot.

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

Two things to know before trusting a frame from this. **One media session at a time** — pulling
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
import time
from typing import Any
from urllib.parse import urlparse

from PIL import Image

SCHEME = "webrtc"
DEFAULT_PORT = 8443
#: Long enough for ICE on an unhelpful network, short enough to fail a bring-up check rather
#: than hang it.
DEFAULT_TIMEOUT_S = 15.0

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


class WebRtcCamera:
    """One video track, decoded, newest frame kept. Not a control path: it never sends intents.

    `mediad` opens a `control` datachannel at us whether we want one or not (the robot creates
    it, so a client that opens nothing still gets a control surface). We accept it and ignore
    it — motion goes over `robotd`'s socket, where the executor, the allowlist and the deadman
    feed already live, and having two ways to move the robot would mean two places to be sure
    about. `detections` is the one thing read from it, because it costs nothing and is the only
    perception upstream offers.
    """

    def __init__(self, address: str, *, timeout_s: float = DEFAULT_TIMEOUT_S) -> None:
        self.address = address
        self.url = signalling_url(address)
        self.timeout_s = timeout_s
        self.frames = 0
        self.error: str | None = None
        self.producer: dict[str, Any] | None = None
        self.detections: dict[str, Any] | None = None
        self._frame: Image.Image | None = None
        self._frame_at: float | None = None
        self._task: asyncio.Task[None] | None = None
        self._tracks: set[asyncio.Task[None]] = set()
        self._first = asyncio.Event()
        self._settled = asyncio.Event()
        """Set when there is an answer either way: a frame decoded, or it definitively cannot."""
        self._pc: Any = None
        self._closing = False

    # ── what the transport uses ─────────────────────────────────────────────────────────

    def latest(self, *, max_age_s: float | None = None) -> Image.Image | None:
        """The newest decoded frame, or None if it is too old to steer on.

        A session that dies leaves its last frame in memory. Handing that out forever is how a
        stopped video turns into a robot walking at something that is no longer there.
        """
        if self._frame is None or self._frame_at is None:
            return None
        if max_age_s is not None and time.monotonic() - self._frame_at > max_age_s:
            return None
        return self._frame

    async def start(self, *, wait: bool = True) -> bool:
        """Open the session. With `wait`, resolve once a frame decodes — or once it cannot.

        Every terminal failure sets `_settled` as well: a missing extra, a refused connection,
        `mediad` with no producer, the robot ending the session. Waiting out the full timeout
        for an answer already in hand is how a bring-up check that says it will "say so rather
        than hang" hangs for fifteen seconds.
        """
        self._task = asyncio.create_task(self._run(), name="quackd-microduck-webrtc")
        if not wait:
            return False
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self._settled.wait(), timeout=self.timeout_s)
        return self._frame is not None

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
        return {
            "configured": True,
            "url": self.address,
            "ok": self._frame is not None,
            "age_s": age,
            "frames": self.frames,
            "size": list(self._frame.size) if self._frame is not None else None,
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
            # The robot opens this at us; we read it and never write to it.
            @channel.on("message")
            def _on_message(data: Any) -> None:
                self._on_control(data)

        return pc

    async def _drain(self, track: Any) -> None:
        """Decode frames until the track ends. The newest one is all anybody wants."""
        while True:
            try:
                frame = await track.recv()
            except Exception:  # the track ended, or the session did
                return
            self._frame = frame.to_image()
            self._frame_at = time.monotonic()
            self.frames += 1
            self._first.set()
            self._settled.set()

    def _on_control(self, data: Any) -> None:
        with contextlib.suppress(Exception):
            msg = json.loads(data)
            if msg.get("method") == "media.detections":
                self.detections = msg.get("params") or {}


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
