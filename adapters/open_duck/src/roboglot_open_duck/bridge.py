"""EXPERIMENTAL: a real Open Duck Mini v2, over the Roboglot bridge daemon on its Pi.

The robot's own runtime has no network control API at all: it reads a local pygame gamepad
and runs a 50 Hz ONNX walk policy, and its only socket checks the IMU. So Roboglot ships a
small daemon that runs on the duck's Raspberry Pi, replaces the gamepad as the walk loop's
command source, and speaks the protocol below. This module is the client half.

The protocol is Roboglot's own. It deliberately does not reuse the Microduck's `robot.move`
and `robot.health`, which are that robot's `duck-ipc-proto` API v16: the same words would
be a false claim about a different body with different limits and no skills behind them.
Because Roboglot defines both ends, the method names are not upstream names and are not in
`upstream_api.py`; what *is* there is every assumption the daemon makes about the robot.

Nothing here has been run against a physical duck.

Address: `tcp://open-duck.local:9871`, or a bare `host:port`.
"""

from __future__ import annotations

import asyncio
import contextlib
import http.client
import ipaddress
import json
import logging
import math
import os
import socket
import ssl
import time
import urllib.parse
import urllib.request
from collections.abc import AsyncIterator
from typing import Any

from PIL import Image

from roboglot.command import redacted_url
from roboglot.transport.base import (
    Ack,
    HeartbeatError,
    Intent,
    Posture,
    RobotState,
    TransportError,
    camera_url_frame,
)
from roboglot_open_duck import upstream_api as up
from roboglot_open_duck.verbs import (
    GESTURES,
    HEAD_PITCH_RANGE,
    HEAD_ROLL_RANGE,
    HEAD_YAW_RANGE,
    MOODS,
    NECK_PITCH_RANGE,
)

log = logging.getLogger(__name__)

STATUS = "EXPERIMENTAL — Roboglot's own protocol, never run against a duck"

#: The wire contract. Bumped whenever a field changes meaning; a mismatch is refused.
PROTOCOL = "roboglot-open-duck-bridge"
PROTOCOL_VERSION = 1
JSONRPC_VERSION = "2.0"
DEFAULT_PORT = 9871
#: Where the client looks for the bridge token when no --token was given.
TOKEN_ENV = "ROBOGLOT_DUCK_TOKEN"
#: The longest line the pump takes from the bridge, 1 MiB, where asyncio's default is 64 KiB.
#: Nothing the bridge says today comes near either, its camera being fetched over HTTP, so
#: this is room rather than need: what matters is that a longer line costs that line and not
#: the link.
READER_LIMIT = 2**20

HELLO = "duck.hello"
COMMAND = "duck.command"  # notification, re-sent at 10 Hz; feeds the daemon's deadman
STOP = "duck.stop"
STATE = "duck.state"
HEALTH = "duck.health"
SOUND = "duck.sound"
ANTENNAS = "duck.antennas"
#: The bridge's answer to a `duck.command` it refuses, a notification since the command was one.
REFUSED = "duck.refused"
#: What the bridge says once its shutdown has begun, and so what this client says then too.
SHUTTING_DOWN = "the bridge is shutting down; nothing moves now"

#: Below this the walk loop is starving and the gait is degrading silently, so the
#: heartbeat fails and the run aborts rather than letting the duck stumble (up.LOOP_HEADROOM).
MIN_LOOP_HZ = 35.0
#: How long a frame fetch may take. Well under the 3 s it used to allow: `go_to` sends its
#: next command after this returns, and the daemon's deadman is 300 ms, so a long stall here
#: is a duck walking blind rather than a slow picture.
CAMERA_TIMEOUT_S = 1.0
#: A frame older than this is not steering material. camd expires its own frames sooner; this
#: is the client's backstop for a camera server that does not stamp them at all.
CAMERA_STALE_AFTER_S = 2.0
#: The deadman window Roboglot will drive behind. Below two command intervals it trips on
#: ordinary jitter; above this it stops firing before Roboglot's own 0.5 s heartbeat, so the
#: laptop would notice the link was gone before the robot did.
DEADMAN_MIN_MS = 200
DEADMAN_MAX_MS = 500


def parse_address(address: str) -> tuple[str, int]:
    text = address[len("tcp://") :] if address.startswith("tcp://") else address
    if "://" in text:
        raise TransportError(f"unknown address {address!r}; use tcp://host:port")
    host, _, port = text.rpartition(":")
    if not host:
        return text, DEFAULT_PORT
    if not port.isdigit():
        raise TransportError(f"bad address {address!r}; expected tcp://host:port")
    return host, int(port)


def _clamp(value: float, bounds: tuple[float, float]) -> float:
    return max(bounds[0], min(bounds[1], value))


#: The schemes a camera URL may use, from the bridge's hello or from the person: a camera here is
#: an HTTP snapshot. `urllib` opened `file:` and `ftp:` URLs as readily, so a bridge, or a typo,
#: could have the laptop read one of its own files and hand it on as the camera's frame.
CAMERA_SCHEMES = ("http", "https")
#: The most one frame may be, in bytes: the Microduck transport's `SNAPSHOT_MAX_BYTES`, the frame
#: limit Roboglot already reads HTTP cameras under, copied because an adapter imports no other
#: adapter. camd's JPEG is tens of kilobytes, so an answer past this is not one picture.
CAMERA_MAX_BYTES = 8_000_000
#: The most one read of a frame asks for: the Microduck transport's `SNAPSHOT_CHUNK_BYTES`.
CAMERA_CHUNK_BYTES = 64 * 1024
#: The statuses that redirect. One is followed only to the same scheme, host and port, and no
#: more often in one fetch than `urllib` itself would follow them.
REDIRECTS = (301, 302, 303, 307, 308)
MAX_REDIRECTS = urllib.request.HTTPRedirectHandler.max_redirections


class FrameTooSlow(TimeoutError):
    """A camera whose frame did not arrive whole within the fetch's one deadline."""


def camera_url_refusal(url: str) -> str | None:
    """Why `url` is not a camera URL this client fetches, or None when it is: http or https,
    with a host, and a port, if it names one, that a connection can use (0 is none, and
    `urlsplit` refuses one past 65535). A refusal shows the URL as Roboglot's records do, with
    its password hidden (`redacted_url`): the fetch never sends it, so nothing needs it said."""
    try:
        parts = urllib.parse.urlsplit(url)
        fetched = parts.scheme.lower() in CAMERA_SCHEMES and bool(parts.hostname)
        port = parts.port  # raises ValueError for one out of range, as for no URL at all
    except ValueError:  # recorded in the refusal below, which names what a camera URL is
        fetched, port = False, None
    if fetched and port != 0:
        return None
    return (
        f"{redacted_url(url)} is not a camera URL Roboglot fetches: a camera URL is http or "
        "https, with a port from 1 to 65535 if it names one, such as "
        "http://127.0.0.1:9872/snapshot.jpg"
    )


def _origin(url: str) -> tuple[str, str, int]:
    """The scheme, host and port `url` reaches, the port filled in, which is what a redirect
    has to keep. A URL that is not a camera URL raises ValueError."""
    refusal = camera_url_refusal(url)
    if refusal is not None:
        raise ValueError(refusal)
    parts = urllib.parse.urlsplit(url)
    scheme = parts.scheme.lower()
    port = parts.port or (http.client.HTTPS_PORT if scheme == "https" else http.client.HTTP_PORT)
    return scheme, str(parts.hostname).lower(), port


def _bound(sock: socket.socket, ends: float, within_s: float) -> None:
    """Make the next wait on `sock` no longer than what is left before the monotonic time `ends`,
    or raise once nothing is."""
    left = ends - time.monotonic()
    if left <= 0:
        raise FrameTooSlow(f"no whole frame within {within_s:g} s")
    sock.settimeout(left)


class _DeadlineSocket(socket.socket):
    """A socket on which every read and write waits no longer than what is left of the fetch's
    one clock. `http.client` reads the headers a line at a time and the body a chunk at a time,
    and a timeout set once before it starts lets one byte near the end buy each later read a
    whole fresh wait: here none can, on any platform. Unbounded until `ends` is set."""

    ends: float | None = None
    within_s = 0.0

    def recv_into(self, buffer: Any, nbytes: int = 0, flags: int = 0) -> int:
        if self.ends is not None:
            _bound(self, self.ends, self.within_s)
        return super().recv_into(buffer, nbytes, flags)

    def recv(self, bufsize: int, flags: int = 0) -> bytes:
        if self.ends is not None:
            _bound(self, self.ends, self.within_s)
        return super().recv(bufsize, flags)

    def sendall(self, data: Any, flags: int = 0) -> None:
        if self.ends is not None:
            _bound(self, self.ends, self.within_s)
        super().sendall(data, flags)


class _DeadlineSSLSocket(ssl.SSLSocket):
    """`_DeadlineSocket` for an https camera: the socket TLS wraps is not the one read from."""

    ends: float | None = None
    within_s = 0.0

    def recv_into(self, buffer: Any, nbytes: int | None = None, flags: int = 0) -> int:
        if self.ends is not None:
            _bound(self, self.ends, self.within_s)
        return super().recv_into(buffer, nbytes, flags)

    def recv(self, buflen: int = 1024, flags: int = 0) -> bytes:
        if self.ends is not None:
            _bound(self, self.ends, self.within_s)
        return super().recv(buflen, flags)

    def sendall(self, data: Any, flags: int = 0) -> None:
        if self.ends is not None:
            _bound(self, self.ends, self.within_s)
        super().sendall(data, flags)


def _connect(host: str, port: int, ends: float, within_s: float) -> _DeadlineSocket:
    """A socket connected to `host`, trying each address it resolves to in the order given, each
    with its share of what is left of the clock, so a camera reachable on any of them answers.

    `socket.create_connection` gave every address the whole of what was left, and on Windows a
    refused connect takes about two seconds, so `localhost`, which resolves to `::1` first, spent
    the deadline on `::1` and never reached a camera on 127.0.0.1. The lookup itself is the
    system resolver's, and is not bounded here."""
    found = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    failed: OSError | None = None
    for tried, (family, kind, proto, _, address) in enumerate(found):
        left = ends - time.monotonic()
        if left <= 0:
            break
        sock = _DeadlineSocket(family, kind, proto)
        sock.settimeout(left / (len(found) - tried))
        try:
            sock.connect(address)
        except OSError as e:  # kept, and raised if no address answers
            sock.close()
            failed = e
            continue
        sock.ends, sock.within_s = ends, within_s
        return sock
    if failed is not None and time.monotonic() < ends:
        raise failed
    raise FrameTooSlow(f"no whole frame within {within_s:g} s") from failed


def _get_once(
    url: str, origin: tuple[str, str, int], ends: float, within_s: float, max_bytes: int
) -> tuple[int, str, Any, bytes]:
    """One GET of `url`, ending by the monotonic time `ends`. A redirect or an error page is read
    only as far as one chunk, which is all its reason needs."""
    scheme, host, port = origin
    too_slow = f"no whole frame within {within_s:g} s"
    if time.monotonic() >= ends:
        raise FrameTooSlow(too_slow)
    kind = http.client.HTTPSConnection if scheme == "https" else http.client.HTTPConnection
    # No timeout of its own: the socket handed to it keeps the fetch's one clock.
    conn = kind(host, port)
    answer: http.client.HTTPResponse | None = None
    try:
        sock: socket.socket = _connect(host, port, ends, within_s)
        if scheme == "https":
            context = ssl.create_default_context()
            context.sslsocket_class = _DeadlineSSLSocket
            # the handshake is bounded by what is left, as one wait with a deadline of its own
            _bound(sock, ends, within_s)
            tls = context.wrap_socket(sock, server_hostname=host)
            if isinstance(tls, _DeadlineSSLSocket):  # always, by `sslsocket_class`
                tls.ends, tls.within_s = ends, within_s
            sock = tls
        conn.sock = sock
        parts = urllib.parse.urlsplit(url)
        conn.request("GET", (parts.path or "/") + (f"?{parts.query}" if parts.query else ""))
        answer = conn.getresponse()
        ok = 200 <= answer.status < 300
        body = bytearray()
        while chunk := answer.read1(CAMERA_CHUNK_BYTES):
            body += chunk
            if not ok:
                break
            if len(body) > max_bytes:
                raise TransportError(
                    f"the camera sent more than {max_bytes / 1e6:g} MB for one frame, which no "
                    "still picture is: give --camera-url a URL that answers one JPEG or PNG"
                )
        if ok and answer.length:
            raise http.client.IncompleteRead(bytes(body), answer.length)
        return answer.status, answer.reason, answer.headers, bytes(body)
    except FrameTooSlow:
        raise
    except Exception as e:
        # a read the clock ended surfaces as the socket's own timeout, and may wake a hair
        # before the clock says so
        if isinstance(e, TimeoutError) or time.monotonic() >= ends:
            raise FrameTooSlow(too_slow) from e
        raise
    finally:
        if answer is not None:
            answer.close()
        conn.close()


def fetch_frame(url: str, *, deadline_s: float, max_bytes: int) -> tuple[bytes, float | None]:
    """One frame from an HTTP camera, and the age the camera stamps on it, fetched in the calling
    thread under one deadline for the whole fetch, redirects included.

    `http.client` and nothing above it, so no proxy variable is read, and a redirect is followed
    only to the same scheme, host and port, at most `MAX_REDIRECTS` times. The body is read a
    chunk at a time and refused past `max_bytes`. One clock bounds it all: each address the
    name resolves to gets its share of what is left for its connect, and every read and write
    after that, headers included, waits no longer than what is left. So the thread is done by
    the deadline and nothing reads on behind a caller that has given up. The name's lookup is
    the system resolver's, and is not bounded."""
    ends = time.monotonic() + deadline_s
    origin = _origin(url)
    for _ in range(MAX_REDIRECTS + 1):
        status, reason, headers, body = _get_once(url, origin, ends, deadline_s, max_bytes)
        if status in REDIRECTS:
            moved = urllib.parse.urljoin(url, headers.get("Location") or "")
            try:
                same = _origin(moved) == origin
            except ValueError:
                same = False
            if not same:
                raise TransportError(
                    f"the camera answered HTTP {status}, a redirect to {redacted_url(moved)}, "
                    "and Roboglot "
                    "follows one only to the same scheme, host and port: if that is your "
                    "camera, give --camera-url that URL"
                )
            url = moved
            continue
        if not 200 <= status < 300:
            # camd explains itself in the body ("the last frame is 30.1s old..."), and that
            # sentence is far more use to whoever is standing next to the duck than the status
            said: Any = None
            with contextlib.suppress(ValueError, AttributeError):
                said = json.loads(body).get("reason")
            raise TransportError(str(said) if said else f"HTTP Error {status}: {reason}")
        age = headers.get("X-Frame-Age")
        return body, (float(age) if age is not None else None)
    raise TransportError(f"the camera redirected more than {MAX_REDIRECTS} times")


def _host_key(name: str) -> Any:
    """A host as something to compare: an address when it is one, else its name in lower case."""
    try:
        return ipaddress.ip_address(name)
    except ValueError:
        return name.lower()


def _is_loopback(name: str) -> bool:
    key = _host_key(name)
    return key == "localhost" if isinstance(key, str) else bool(key.is_loopback)


def hello_camera_refusal(url: str, bridge_host: str, peer_host: str | None) -> str | None:
    """Why a camera URL that the bridge's hello names is not followed, or None when it is.

    It is followed only over HTTP or HTTPS, and only to the host this client connected to: by
    the name it was given, by the address it reached, or on loopback when it reached the bridge
    on loopback, which is how the documented `ssh -L` tunnel carries both ports. A URL the
    person named with `--camera-url` is theirs, and never comes here."""
    try:
        parts = urllib.parse.urlsplit(url)
        scheme, host = parts.scheme.lower(), parts.hostname or ""
    except ValueError:  # not a URL at all, such as an unclosed `[`: refused below, as named
        scheme, host = "", ""
    if camera_url_refusal(url) is None and scheme in CAMERA_SCHEMES and host:
        key = _host_key(host)
        if key == _host_key(bridge_host) or (peer_host and key == _host_key(peer_host)):
            return None
        if peer_host and _is_loopback(host) and _is_loopback(peer_host):
            return None
    return (
        f"the bridge named its camera at {redacted_url(url)}, which Roboglot does not fetch: a "
        "camera URL from the bridge is followed only over http or https to the host Roboglot "
        "connected to. If that is your camera, pass it with --camera-url."
    )


class OpenDuckBridge:
    name = "bridge"
    mobility = "legged"

    def __init__(
        self,
        address: str | None = None,
        *,
        camera_url: str | None = None,
        token: str | None = None,
        request_timeout_s: float = 2.0,
    ) -> None:
        self.address = address or f"tcp://open-duck.local:{DEFAULT_PORT}"
        self.camera_url = camera_url
        # The bridge's installer writes a token to the robot, so a duck set up by the book
        # refuses an unauthenticated client. It travels in the handshake and never in the
        # address, because addresses are printed and land in transcripts.
        self.token = token or os.environ.get(TOKEN_ENV) or None
        self.request_timeout_s = request_timeout_s
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._pump: asyncio.Task[None] | None = None
        self._pending: dict[int, asyncio.Future[Any]] = {}
        self._notifications: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=256)
        self._next_id = 1
        self._last_state: dict[str, Any] | None = None
        self._t0 = time.monotonic()
        self.hello: dict[str, Any] | None = None
        #: What this particular duck was built with, read at connect. The adapter narrows
        #: its manifest from these, so a duck with no camera loses the verbs that need one.
        self.features: dict[str, bool] = {}
        self.bridge_version: str | None = None
        self.runtime_commit: str | None = None
        #: Why the last `stop` did not reach the duck, or None if it did. `stop` is the verb
        #: the pilot is told to reach for when something is wrong, and it is asked for most
        #: often when the link is the thing that is wrong — so a stop that was never delivered
        #: must not be logged as one that was.
        self.stop_error: str | None = None
        #: Echoed on every `duck.command` so the bridge can tell a command this client sent
        #: after a stop from one that was already in flight when the stop went out. Learned
        #: from the stop's reply, and re-learned from any state read in case that reply was
        #: lost.
        self.stop_epoch = 0
        #: The tick count at the previous heartbeat. A count that has not moved between two
        #: beats means the control loop is wedged, which no rate can show: `loop_hz` is
        #: written by that same thread and freezes with it.
        self._last_ticks: int | None = None
        #: The bridge's own `safety` block, as it reported it at connect.
        self.safety: dict[str, Any] = {}
        #: Set when the bridge says it has no authentication but we sent a token anyway.
        self.auth_warning: str | None = None
        #: Set when the robot's runtime is not the commit Roboglot's refs were read at.
        self.runtime_warning: str | None = None
        #: The window the bridge said it enforces, or None if it claims no deadman at all.
        self.deadman_ms: int | None = None
        #: Set when the read pump sees EOF. Without it every later request waited out the
        #: full timeout on a socket nobody was reading.
        self._closed = False
        #: Why the bridge takes no move now, or None. Learned from its `duck.refused` answer to
        #: a command or from a state read, and kept: a bridge shutting down does not come back
        #: on this link. Every later move and look is refused with it rather than sent into a
        #: bridge that would drop it in silence while the verb reported a walk.
        self.moves_refused: str | None = None
        #: Frame bookkeeping, so `doctor` can prove the camera works before anything walks.
        self.camera_stale_after_s = CAMERA_STALE_AFTER_S
        self.frame_age_s: float | None = None
        self.camera_error: str | None = None
        self.frames = 0

    # ── wire ────────────────────────────────────────────────────────────────────────

    async def connect(self) -> None:
        host, port = parse_address(self.address)
        self.moves_refused = None  # a new link, to a bridge that may have been restarted
        try:
            self._reader, self._writer = await asyncio.open_connection(
                host, port, limit=READER_LIMIT
            )
        except OSError as e:
            raise TransportError(
                f"cannot connect to the bridge at {self.address}: {e}. Is "
                "roboglot-duck-bridge running on the duck's Pi?"
            ) from e
        self._pump = asyncio.create_task(self._read_loop(), name="roboglot-open-duck-pump")
        hello: dict[str, Any] = {"protocol": PROTOCOL, "protocol_version": PROTOCOL_VERSION}
        if self.token:
            hello["token"] = self.token
        try:
            result = await self.request(HELLO, hello)
        except TransportError as e:
            await self.close()
            if "2:" in str(e) and not self.token:
                raise TransportError(
                    f"the bridge at {self.address} wants a token and none was given. Its "
                    f"installer writes one on the robot; pass it with --token or "
                    f"{TOKEN_ENV}. Original answer: {e}"
                ) from e
            raise
        self.hello = result if isinstance(result, dict) else {}
        remote = self.hello.get("protocol_version")
        if remote is not None and int(remote) != PROTOCOL_VERSION:
            await self.close()
            raise TransportError(
                f"the bridge speaks {PROTOCOL} v{remote}, Roboglot speaks "
                f"v{PROTOCOL_VERSION}; refusing rather than guessing. Update whichever is older"
            )
        caps = self.hello.get("capabilities") or {}
        self.features = {k: bool(v) for k, v in caps.items()}
        self.bridge_version = self.hello.get("bridge_version")
        self.runtime_commit = (self.hello.get("runtime") or {}).get("commit")
        # Every upstream name the bridge borrows was read at one commit, and the class rebind
        # the whole thing stands on is the part a rename would break silently. This is a
        # warning, not a refusal: an owner tracking upstream's branch is doing the normal
        # thing, and Roboglot has no business refusing to talk to their duck over it.
        if self.runtime_commit and not self.runtime_commit.startswith(up.PIN[:7]):
            self.runtime_warning = (
                f"this duck's Open_Duck_Mini_Runtime is at {self.runtime_commit[:7]}, and "
                f"Roboglot read upstream at {up.PIN[:7]} ({up.READ_ON}). That is normal, but "
                "it is also where a renamed XBoxController would hide: if the bridge exits "
                "saying the walk loop never asked for a controller, this is why."
            )
        self.safety = dict(self.hello.get("safety") or {})
        # upstream_api.COMMAND_TTL states the invariant this implements: "the manifest claims
        # a deadman only when a bridge says it has one". It was a hardcoded True.
        window = self.safety.get("deadman_ms")
        self.deadman_ms = int(window) if window is not None else None
        if self.deadman_ms is not None and not (
            DEADMAN_MIN_MS <= self.deadman_ms <= DEADMAN_MAX_MS
        ):
            await self.close()
            raise TransportError(
                f"the bridge reports a {self.deadman_ms} ms deadman, which is outside the "
                f"{DEADMAN_MIN_MS}-{DEADMAN_MAX_MS} ms Roboglot can drive safely. Below that it "
                "trips on ordinary jitter; above it, it no longer fires before Roboglot's own "
                "0.5 s heartbeat. Fix --deadman-ms on the robot."
            )
        # A bridge that accepted our token without checking one is indistinguishable from a
        # bridge that checked it, which is exactly how an unreadable token file left
        # authentication silently off while four places in the docs promised it was on.
        if self.token and self.safety.get("auth") == "none":
            self.auth_warning = (
                "this bridge has no token configured, so the one you supplied was not "
                "checked and anything that can reach this port can walk the duck. Check the "
                "token file on the robot is readable by the service user."
            )
        named = (self.hello.get("camera") or {}).get("url")
        if not self.camera_url and named:
            peer = self._writer.get_extra_info("peername") if self._writer is not None else None
            refusal = hello_camera_refusal(str(named), host, peer[0] if peer else None)
            if refusal is None:
                self.camera_url = str(named)
            else:
                # A camera with nowhere Roboglot will fetch it from is no camera, as the bridge
                # itself rules for one with no URL: the verbs that need frames do not exist,
                # rather than exist and fail.
                self.features["camera"] = False
                self.camera_error = refusal
                log.warning("%s", refusal)

    async def close(self) -> None:
        if self._pump is not None:
            self._pump.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._pump
            self._pump = None
        if self._writer is not None:
            self._writer.close()
            with contextlib.suppress(Exception):
                await self._writer.wait_closed()
            self._writer = None
            self._reader = None

    async def _read_loop(self) -> None:
        """Every answer comes through here, so nothing one line does may end it. A pump that
        died left every request after it to wait out its timeout on a socket nobody read,
        while the commands, which are writes, went on feeding the daemon's deadman: the duck
        walked on until a heartbeat gave up, and the stop it was then sent went out with
        nobody to read its reply, so a stop that landed was reported as one that did not."""
        assert self._reader is not None
        while True:
            try:
                line = await self._reader.readline()
            except ValueError as e:
                # A line over READER_LIMIT. `readline` has already let go of it, so the next
                # read starts past it, or at a tail of it that `_take_line` throws away.
                log.warning("skipped a line from the bridge over %d bytes: %s", READER_LIMIT, e)
                continue
            except OSError as e:
                self._fail_pending(f"the link to the bridge failed: {e}")
                return
            except Exception as e:  # nothing else is expected of a socket's reader: end the link
                self._fail_pending(f"the link to the bridge failed: {type(e).__name__}: {e}")
                return
            if not line:
                self._fail_pending("the bridge closed the connection")
                return
            try:
                self._take_line(line)
            except Exception as e:  # one line Roboglot mishandles must not cut the duck off
                log.warning("skipped a line from the bridge that Roboglot could not read: %r", e)

    def _fail_pending(self, why: str) -> None:
        # Mark the link dead before failing the waiters: otherwise every later request sat out
        # its full timeout waiting for a pump that had exited, and `stop` in particular blocked
        # for seconds before reporting anything.
        self._closed = True
        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(TransportError(why))

    def _take_line(self, line: bytes) -> None:
        """One line from the bridge. Anything that is not a JSON object, or that answers with an
        id that is no number, is skipped rather than raised, and an answer is read whole before
        its request is let go, so no request is left waiting on one Roboglot could not read."""
        try:
            msg = json.loads(line)
        except ValueError:  # broken JSON, or bytes that are not UTF-8
            return
        if not isinstance(msg, dict):
            return
        if msg.get("id") is not None and ("result" in msg or "error" in msg):
            try:
                req_id = int(msg["id"])
            except (TypeError, ValueError):
                return
            pending = self._pending.pop(req_id, None)
            if pending is None or pending.done():
                return
            if "error" in msg:
                err = msg["error"]
                said = f"{err.get('code')}: {err.get('message')}" if isinstance(err, dict) else err
                pending.set_exception(TransportError(str(said)))
            else:
                pending.set_result(msg.get("result"))
        elif "method" in msg:
            params = msg.get("params")
            if params is not None and not isinstance(params, dict):
                return
            if msg["method"] == STATE:
                self._last_state = params or {}
            if msg["method"] == REFUSED:
                self.moves_refused = str((params or {}).get("message") or SHUTTING_DOWN)
            with contextlib.suppress(asyncio.QueueFull):
                self._notifications.put_nowait(msg)

    def _write(self, obj: dict[str, Any]) -> None:
        if self._closed:
            raise TransportError("the bridge closed the connection")
        if self._writer is None:
            raise TransportError("not connected to the bridge")
        self._writer.write((json.dumps(obj, separators=(",", ":")) + "\n").encode())

    async def request(self, method: str, params: dict[str, Any] | None = None) -> Any:
        req_id = self._next_id
        self._next_id += 1
        fut: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        self._pending[req_id] = fut
        msg: dict[str, Any] = {"jsonrpc": JSONRPC_VERSION, "id": req_id, "method": method}
        if params is not None:
            msg["params"] = params
        try:
            # One deadline for the write, the drain and the answer. The drain used to come
            # first and outside it, so on a link whose send buffer had filled (a peer gone
            # silently, the keepalive still writing) the heartbeat and the stop waited with no
            # deadline until TCP gave up, which is minutes.
            async with asyncio.timeout(self.request_timeout_s):
                self._write(msg)
                assert self._writer is not None
                await self._writer.drain()
                return await fut
        except TimeoutError as e:
            raise TransportError(f"{method}: no answer within {self.request_timeout_s:g}s") from e
        finally:
            self._pending.pop(req_id, None)

    async def notify(self, method: str, params: dict[str, Any]) -> None:
        self._write({"jsonrpc": JSONRPC_VERSION, "method": method, "params": params})
        assert self._writer is not None
        await self._writer.drain()

    # ── protocol ────────────────────────────────────────────────────────────────────

    async def get_frame(self) -> Image.Image | None:
        """The camera lives in its own process on the Pi: encoding a JPEG inside a 20 ms
        control tick is not affordable (up.CAM), so the bridge advertises a URL.

        Returns None rather than raising when there is no usable frame. `jsonrpc`'s own
        docstring records that raising here ended a session once, over a single dropped
        HTTP response, and the callers all have a clean `img is None` branch."""
        if not self.camera_url:
            return None
        url = self.camera_url

        def fetch() -> tuple[bytes, float | None]:
            # The one deadline is kept here, in the worker thread, which therefore ends by it.
            # A deadline around `to_thread` would only stop waiting, and leave the thread
            # reading. camd stamps every frame with how old it is (X-Frame-Age), which is how a
            # live camera is told from one serving its last good JPEG for an hour: what a ribbon
            # working loose on a walking duck looks like from here.
            return fetch_frame(url, deadline_s=CAMERA_TIMEOUT_S, max_bytes=CAMERA_MAX_BYTES)

        try:
            data, age = await asyncio.to_thread(fetch)
            frame = camera_url_frame(data)
        except Exception as e:
            self.camera_error = str(e)
            self.frame_age_s = None
            return None
        self.camera_error = None
        self.frame_age_s = age
        if age is not None and age > self.camera_stale_after_s:
            self.camera_error = f"the newest frame is {age:.1f}s old; the camera has stopped"
            return None
        self.frames += 1
        return frame

    def camera_health(self) -> dict[str, Any]:
        """What `doctor` probes for. It existed only on the Microduck transport, so
        `--camera-url` was accepted here, never checked, and a typo'd or unreachable snapshot
        server passed the checklist's go/no-go gate and failed at the first `observe` — with
        the duck already on the floor."""
        return {
            "url": self.camera_url,
            "frames": self.frames,
            "age_s": self.frame_age_s,
            "stale_after_s": self.camera_stale_after_s,
            "error": self.camera_error,
        }

    async def get_state(self) -> RobotState:
        state = self._last_state
        stale: str | None = None
        if state is None:
            try:
                result = await self.request(STATE)
                state = result if isinstance(result, dict) else {}
            except TransportError as e:
                # Swallowed, but recorded. A failed state read must not take a run down — the
                # heartbeat is what ends it — yet it must not be dressed up as a healthy duck
                # either. Everything below would otherwise be invented: posture standing-ish,
                # policy `stand`, a pose of exactly (0, 0, 0), and both preconditions passing
                # on all of it, so Roboglot would start a `move` into a link that is not there.
                stale = str(e)
                state = {}
        state = state or {}
        # Resynchronise the stop epoch from any state read. If a stop's own reply were lost,
        # this client would keep sending an epoch the bridge has moved past and have every
        # command dropped for the rest of the window; one state read recovers it.
        if state.get("stop_epoch") is not None:
            self.stop_epoch = int(state["stop_epoch"])
        if state.get("shutting_down") is True:
            self.moves_refused = SHUTTING_DOWN
        # `fallen` is tri-state on the wire: None means the bridge cannot see falls at all.
        # A duck nobody is watching must read as unknown, never as standing (up.FALL_SIGNAL).
        raw_fallen = state.get("fallen")
        detects_falls = raw_fallen is not None
        fallen = raw_fallen is True
        running = state.get("policy_running")
        posture: Posture
        if fallen:
            posture = "fallen"
        elif detects_falls and running:
            posture = "standing"
        else:
            posture = "unknown"
        # This robot has no odometry, and the bridge sends no pose. Defaulting the three to
        # 0.0 put `pose=(0.00, 0.00, 0.00 rad)` in front of the LLM on every single
        # observation, unchanged after the duck had walked across a room. None is the honest
        # answer and `summary()` omits it, which is what the Microduck transport already does.
        pose = state.get("pose") or {}
        return RobotState(
            t=self.now(),
            x=_maybe_float(pose.get("x")),
            y=_maybe_float(pose.get("y")),
            theta=_maybe_float(pose.get("theta")),
            policy="walk" if state.get("moving") else "stand",
            posture=posture,
            fallen=fallen,
            # nothing in the runtime reports a battery, so a battery abort cannot fire
            battery_percent=None,
            extras={
                "policy_running": running,
                "fall_detection": detects_falls,
                "loop_hz": state.get("loop_hz"),
                "tick_age_ms": state.get("tick_age_ms"),
                "command_age_ms": state.get("command_age_ms"),
                "deadman_tripped": state.get("deadman_tripped"),
                "pad_override": state.get("pad_override"),
                "stop_error": self.stop_error,
                "shutting_down": state.get("shutting_down"),
                "state_stale": stale,
                "assumptions": [up.FALL_SIGNAL.name, up.COMMAND_TTL.name],
            },
        )

    async def send_intent(self, intent: Intent) -> Ack:
        p = intent.params
        if intent.kind in ("move", "look") and self.moves_refused is not None:
            return Ack(accepted=False, reason=self.moves_refused)
        try:
            match intent.kind:
                case "move":
                    await self.notify(
                        COMMAND,
                        {
                            "vx": float(p.get("vx", 0.0)),
                            "vy": float(p.get("vy", 0.0)),
                            "vyaw": float(p.get("wz", 0.0)),
                            "epoch": self.stop_epoch,
                        },
                    )
                    return Ack()
                case "stop":
                    self._note_stop(await self.request(STOP))
                    return Ack()
                case "look":
                    return await self._look(p)
                case "sound":
                    tag = str(p.get("tag", "chirp"))
                    mood = tag if tag in MOODS else "chirp"
                    return _ack(await self.request(SOUND, {"mood": mood}))
                case "do":
                    kind, _, arg = str(p.get("skill")).partition(":")
                    if kind != "antennas" or arg not in GESTURES:
                        return Ack(
                            accepted=False,
                            reason=f"an Open Duck Mini v2 has no skill {p.get('skill')!r}",
                        )
                    return _ack(await self.request(ANTENNAS, {"gesture": arg}))
                case "enable":
                    if not p.get("on", True):
                        return Ack(accepted=False, reason="Roboglot never limps a robot")
                    return Ack(accepted=True, reason="this robot's policy is always enabled")
                case _:
                    return Ack(accepted=False, reason=f"no bridge mapping for {intent.kind}")
        except TransportError as e:
            return Ack(accepted=False, reason=str(e))

    async def _look(self, p: dict[str, Any]) -> Ack:
        """A gaze point comes in as a unit vector; the robot wants four joint angles.

        Only the keys we send are changed, so re-centring the gaze before a walk never
        yanks the neck or the roll to zero."""
        x, y, z = float(p.get("x", 1.0)), float(p.get("y", 0.0)), float(p.get("z", 0.0))
        yaw = math.atan2(y, x)
        pitch = math.atan2(z, math.hypot(x, y))
        wanted = {"head_yaw": yaw, "head_pitch": pitch}
        head = {
            "head_yaw": _clamp(yaw, HEAD_YAW_RANGE),
            "head_pitch": _clamp(pitch, HEAD_PITCH_RANGE),
        }
        for name, bounds in (("neck_pitch", NECK_PITCH_RANGE), ("head_roll", HEAD_ROLL_RANGE)):
            if name in p:
                head[name] = _clamp(float(p[name]), bounds)
        await self.notify(COMMAND, {"head": head, "epoch": self.stop_epoch})
        clamped = any(abs(head[k] - v) > 1e-9 for k, v in wanted.items())
        return Ack(accepted=True, reason="clamped to this neck's travel" if clamped else None)

    async def subscribe(self, topic: str) -> AsyncIterator[dict[str, Any]]:  # type: ignore[override]
        while True:
            msg = await self._notifications.get()
            yield {"topic": msg.get("method"), **(msg.get("params") or {})}

    async def heartbeat(self) -> None:
        try:
            health = await self.request(HEALTH)
        except TransportError as e:
            raise HeartbeatError(f"{HEALTH} failed: {e}") from e
        if not isinstance(health, dict):
            return
        if health.get("healthy") is False:
            raise HeartbeatError(f"the duck is unhealthy: {health.get('reason') or 'no reason'}")
        # The loop has not ticked twice yet, so there is no rate to judge. This window used to
        # abort a perfectly healthy duck during ONNX load and servo init, while the socket was
        # already listening and answering.
        if int(health.get("ticks") or 0) < 2:
            return
        # A stalled tick counter is the wedged-loop case: the rate below is written by the
        # control thread, so it stays frozen at whatever it last was.
        if self._last_ticks is not None and int(health["ticks"]) == self._last_ticks:
            raise HeartbeatError(
                f"the walk loop has not ticked between two heartbeats (still "
                f"{health['ticks']} ticks); it is wedged, not slow"
            )
        self._last_ticks = int(health["ticks"])
        loop_hz = health.get("loop_hz")
        if loop_hz is not None and float(loop_hz) < MIN_LOOP_HZ:
            raise HeartbeatError(
                f"the walk loop is down to {float(loop_hz):.1f} Hz (needs {MIN_LOOP_HZ:g}); "
                "the Pi is starved and the gait is degrading"
            )

    def _note_stop(self, result: Any) -> None:
        """Adopt the epoch the bridge just moved to, so the next command is accepted at once
        and the ones still in flight behind this stop are not."""
        if isinstance(result, dict) and result.get("stop_epoch") is not None:
            self.stop_epoch = int(result["stop_epoch"])

    async def stop(self) -> None:
        try:
            self._note_stop(await self.request(STOP))
        except (TransportError, OSError) as e:
            # Recorded rather than raised: `stop` must never itself take a run down. What
            # actually zeroes the legs when this fails is the daemon's own deadman, 300 ms
            # after the commands stop arriving — which is exactly what has just happened.
            self.stop_error = str(e)
        else:
            self.stop_error = None

    def now(self) -> float:
        return time.monotonic() - self._t0

    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(seconds)


def _maybe_float(value: Any) -> float | None:
    return None if value is None else float(value)


def _ack(result: Any) -> Ack:
    if isinstance(result, dict) and "accepted" in result:
        return Ack(accepted=bool(result["accepted"]), reason=result.get("reason"))
    return Ack()
