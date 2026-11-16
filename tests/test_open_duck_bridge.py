"""The bridge backend against a fake bridge daemon over TCP loopback.

The fake speaks exactly the protocol Roboglot defines for this robot, so this proves our
framing, the handshake and its version gate, the capability narrowing that decides which
verbs the robot has, the deadman-feeding command notifications, and the heartbeat that
refuses a starved control loop. It proves nothing about a real duck.
"""

from __future__ import annotations

import asyncio
import io
import json
import math
import socket
import threading
import time
from collections.abc import Callable
from typing import Any

import pytest
from PIL import Image

import roboglot_open_duck.bridge as client
from roboglot.glotfile.parser import parse_glot_text
from roboglot.perception.color_blob import ColorBlobDetector
from roboglot.safety import Executor, allow_all
from roboglot.transport.base import HeartbeatError, Intent, TransportError
from roboglot.verbs.registry import registry_from_manifest
from roboglot_open_duck import OpenDuckAdapter
from roboglot_open_duck.bridge import (
    COMMAND,
    MIN_LOOP_HZ,
    PROTOCOL,
    PROTOCOL_VERSION,
    STATE,
    TOKEN_ENV,
    OpenDuckBridge,
    parse_address,
)
from roboglot_open_duck.verbs import HEAD_YAW_RANGE

GLOT = parse_glot_text(
    "---\nglot: 1\nname: t\ndescription: d\nrequires: [move]\nverbs:\n"
    "  allow: [move, gaze, say, quack, express, report_state, stop]\n"
    "success: [x]\n---\n# Task\nx\n"
)
FULL_DUCK = {"camera": True, "speaker": True, "antennas": True, "head": True}


class FakeBridge:
    """A duck's Pi, as far as the wire can tell."""

    def __init__(
        self,
        *,
        protocol_version: int = PROTOCOL_VERSION,
        capabilities: dict[str, bool] | None = None,
        healthy: bool = True,
        loop_hz: float = 49.8,
        fallen: bool | None = False,
        token: str | None = None,
    ) -> None:
        self.protocol_version = protocol_version
        self.capabilities = FULL_DUCK if capabilities is None else capabilities
        self.healthy = healthy
        self.loop_hz = loop_hz
        self.ticks = 0
        self.safety: dict[str, Any] = {}
        self.silent = False
        self.fallen = fallen
        self.token = token
        #: Bytes of padding on every `duck.state` answer, for lines longer than a reader takes.
        self.state_padding = 0
        #: The camera URL the hello names.
        self.camera_url = "http://127.0.0.1:9872/snapshot.jpg"
        self.notifications: list[dict[str, Any]] = []
        self.requests: list[dict[str, Any]] = []
        self.server: asyncio.AbstractServer | None = None
        self._clients: set[asyncio.StreamWriter] = set()
        self.port = 0
        self._arrived = asyncio.Event()

    async def start(self) -> None:
        self.server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        self.port = self.server.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        assert self.server is not None
        # From Python 3.12 `wait_closed` waits for every connection, so a test that failed
        # before closing its client hung here rather than failing.
        for writer in self._clients:
            writer.close()
        self.server.close()
        await self.server.wait_closed()

    @property
    def address(self) -> str:
        return f"tcp://127.0.0.1:{self.port}"

    async def wait_notifications(self, n: int, wait_s: float = 2.0) -> list[dict[str, Any]]:
        async with asyncio.timeout(wait_s):
            while len(self.notifications) < n:
                self._arrived.clear()
                await self._arrived.wait()
        return self.notifications

    def commands(self) -> list[dict[str, Any]]:
        return [m.get("params") or {} for m in self.notifications if m.get("method") == COMMAND]

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self._clients.add(writer)
        while line := await reader.readline():
            msg = json.loads(line)
            if "id" not in msg:
                self.notifications.append(msg)
                self._arrived.set()
                continue
            self.requests.append(msg)
            method, params = msg["method"], msg.get("params") or {}
            if self.silent:  # the socket is up and nothing comes back
                continue
            result: Any
            if method == "duck.hello":
                if self.token is not None and params.get("token") != self.token:
                    error = {"code": 2, "message": "bad or missing token; see the token file"}
                    payload = {"jsonrpc": "2.0", "id": msg["id"], "error": error}
                    writer.write((json.dumps(payload) + "\n").encode())
                    await writer.drain()
                    continue
                result = {
                    "protocol": PROTOCOL,
                    "protocol_version": self.protocol_version,
                    "bridge_version": "0.1.0",
                    "runtime": {"commit": "3203734", "dirty": False},
                    "capabilities": self.capabilities,
                    "camera": {"url": self.camera_url},
                    "safety": self.safety,
                }
            elif method == "duck.health":
                # `ticks` advances on every beat, because a real loop that is merely slow is
                # still ticking. The client needs two beats before it will judge a rate at
                # all (during ONNX load and servo init there is no rate yet), and a count
                # that does not move is how a wedged loop is told from a slow one.
                self.ticks += 7
                result = {"healthy": self.healthy, "loop_hz": self.loop_hz, "ticks": self.ticks}
                if not self.healthy:
                    result["reason"] = "the serial bus stopped answering"
            elif method == "duck.state":
                result = {
                    "policy_running": True,
                    "fallen": self.fallen,
                    "loop_hz": self.loop_hz,
                    "command_age_ms": 40,
                    "pose": {"x": 0.1, "y": 0.2, "theta": 0.3},
                }
                if self.state_padding:
                    result["notes"] = "x" * self.state_padding
            elif method == "duck.stop":
                result = {"stopped": True, "limp": False}
            elif method == "duck.sound":
                result = {"accepted": True, "played": params.get("mood")}
            elif method == "duck.antennas":
                result = {"accepted": True, "gesture": params.get("gesture")}
            else:
                error = {"code": -32601, "message": f"unknown method {method}"}
                payload = {"jsonrpc": "2.0", "id": msg["id"], "error": error}
                writer.write((json.dumps(payload) + "\n").encode())
                await writer.drain()
                continue
            writer.write(
                (json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": result}) + "\n").encode()
            )
            await writer.drain()
        writer.close()


@pytest.fixture
async def bridge():
    fake = FakeBridge()
    await fake.start()
    yield fake
    await fake.stop()


# ── the wire ────────────────────────────────────────────────────────────────────────────


async def test_handshake_reports_what_this_duck_was_built_with(bridge: FakeBridge) -> None:
    t = OpenDuckBridge(bridge.address)
    await t.connect()
    assert t.features == FULL_DUCK
    assert t.bridge_version == "0.1.0" and t.runtime_commit == "3203734"
    assert t.camera_url == "http://127.0.0.1:9872/snapshot.jpg"
    hello = bridge.requests[0]
    assert hello["method"] == "duck.hello"
    assert hello["params"] == {"protocol": PROTOCOL, "protocol_version": PROTOCOL_VERSION}
    await t.close()


async def test_a_camera_url_from_the_hello_is_followed_only_over_http_to_the_bridges_own_host(
    tmp_path: Any,
) -> None:
    """With no `--camera-url` the client fetched whatever URL the bridge's hello named, and
    `urllib` opens `file:` URLs too, so a bridge could make the laptop read a local picture and
    hand it on as the camera's frame, which goes to the LLM vendor with the observation. A URL
    from the hello is now taken only over HTTP or HTTPS to the host the client connected to;
    anything else is refused at connect with the reason, and the duck has no camera verbs
    rather than verbs that fail. A URL the person named is theirs, and is taken as named."""
    picture = tmp_path / "not-a-camera.jpg"
    picture.write_bytes(b"\xff\xd8\xff")
    fake = FakeBridge()
    await fake.start()
    try:
        for named in (
            picture.as_uri(),
            "ftp://127.0.0.1/snapshot.jpg",
            "http://192.0.2.7:9872/snapshot.jpg",
            "http://127.0.0.1.example:9872/snapshot.jpg",
        ):
            fake.camera_url = named
            t = OpenDuckBridge(fake.address)
            await t.connect()
            assert t.camera_url is None, named
            assert t.features["camera"] is False, named
            assert named in str(t.camera_error) and "--camera-url" in str(t.camera_error)
            assert await t.get_frame() is None
            await t.close()

        for named in ("http://127.0.0.1:9872/snapshot.jpg", "https://localhost:9872/still.jpg"):
            fake.camera_url = named
            t = OpenDuckBridge(fake.address)
            await t.connect()
            assert (t.camera_url, t.features["camera"], t.camera_error) == (named, True, None)
            await t.close()

        fake.camera_url = picture.as_uri()
        mine = "http://192.0.2.7:9872/snapshot.jpg"
        t = OpenDuckBridge(fake.address, camera_url=mine)
        await t.connect()
        assert (t.camera_url, t.features["camera"], t.camera_error) == (mine, True, None)
        await t.close()
    finally:
        await fake.stop()


async def test_a_request_ends_at_its_timeout_even_when_the_link_will_not_drain(
    bridge: FakeBridge,
) -> None:
    """`request()` awaited `drain()` before its timeout started, so on a link whose send
    buffer had filled (a peer that went silently, with the keepalive still writing) the
    heartbeat and the stop waited with no deadline until TCP gave up, which is minutes. The
    write, the drain and the wait for the answer now share the one timeout."""
    t = OpenDuckBridge(bridge.address, request_timeout_s=0.2)
    await t.connect()

    async def never_drains() -> None:
        await asyncio.Event().wait()

    assert t._writer is not None
    t._writer.drain = never_drains  # type: ignore[method-assign]
    try:
        async with asyncio.timeout(t.request_timeout_s * 20):
            with pytest.raises(HeartbeatError, match="no answer within"):
                await t.heartbeat()
            await t.stop()
        assert "no answer within" in str(t.stop_error)
    finally:
        await t.close()


class RawCamera(threading.Thread):
    """An HTTP camera on 127.0.0.1 that answers each request with whatever `answer` writes,
    raw, so a test can make it send too much, too slowly, or somewhere else. It notes the path
    of every request and the bytes it sent, and notices when the client hangs up on it."""

    def __init__(self, answer: Callable[[socket.socket, RawCamera, str], None]) -> None:
        super().__init__(name="roboglot-test-camera", daemon=True)
        self.answer = answer
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(8)
        self.sock.settimeout(0.05)
        self.port = self.sock.getsockname()[1]
        self.stopping = threading.Event()
        self.hung_up = threading.Event()
        self.paths: list[str] = []
        self.sent = 0

    def url(self, path: str = "/snapshot.jpg") -> str:
        return f"http://127.0.0.1:{self.port}{path}"

    def send(self, conn: socket.socket, data: bytes) -> None:
        conn.sendall(data)
        self.sent += len(data)

    def run(self) -> None:
        while not self.stopping.is_set():
            try:
                conn, _ = self.sock.accept()
            except OSError:
                continue
            with conn:
                conn.settimeout(5)
                head = b""
                try:
                    while b"\r\n\r\n" not in head:
                        chunk = conn.recv(4096)
                        if not chunk:
                            break
                        head += chunk
                    words = head.split(b" ", 2)
                    path = words[1].decode() if len(words) > 2 else ""
                    self.paths.append(path)
                    self.answer(conn, self, path)
                except OSError:
                    self.hung_up.set()
        self.sock.close()

    def finish(self) -> None:
        self.stopping.set()
        self.join(timeout=10)


def _jpeg() -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (24, 16), (200, 120, 40)).save(out, "JPEG")
    return out.getvalue()


def _picture(conn: socket.socket, camera: RawCamera, _: str) -> None:
    body = _jpeg()
    head = f"HTTP/1.0 200 OK\r\nContent-Type: image/jpeg\r\nContent-Length: {len(body)}\r\n\r\n"
    camera.send(conn, head.encode() + body)


async def test_a_frame_is_read_under_the_frame_limit_and_the_fetch_ends_there() -> None:
    """A frame was read with `resp.read()`, whole and with no cap, so a camera, or a bridge that
    named one, could make the laptop hold whatever it sent. It is read in chunks now and given
    up past `CAMERA_MAX_BYTES`, the Microduck transport's limit for one picture, which is the
    frame limit Roboglot already reads HTTP cameras under; the client hangs up there."""
    from roboglot_microduck.transports.jsonrpc_unix import SNAPSHOT_MAX_BYTES

    endless_bytes = 3 * SNAPSHOT_MAX_BYTES

    def endless(conn: socket.socket, camera: RawCamera, _: str) -> None:
        camera.send(conn, b"HTTP/1.0 200 OK\r\nContent-Type: image/jpeg\r\n\r\n")
        block = b"\0" * 65536
        while camera.sent < endless_bytes:
            camera.send(conn, block)

    camera = RawCamera(endless)
    camera.start()
    try:
        t = OpenDuckBridge(camera_url=camera.url())
        assert await t.get_frame() is None
        assert "more than" in str(t.camera_error), t.camera_error
        assert camera.hung_up.wait(5) and camera.sent < endless_bytes, camera.sent
    finally:
        camera.finish()
    assert client.CAMERA_MAX_BYTES == SNAPSHOT_MAX_BYTES


@pytest.mark.parametrize("source", ["hello", "--camera-url"])
async def test_a_trickling_camera_is_cut_at_one_deadline_and_nothing_reads_on(source: str) -> None:
    """`CAMERA_TIMEOUT_S` bounded each socket read, not the fetch, and `get_frame` awaited the
    worker thread with no deadline either. A camera that sends one byte every half of it never
    tripped it: `get_frame` did not return, and a caller that gave up left the thread reading.
    The fetch now ends at one deadline, whichever source named the camera, and hangs up."""

    def trickle(conn: socket.socket, camera: RawCamera, _: str) -> None:
        head = "HTTP/1.0 200 OK\r\nContent-Type: image/jpeg\r\nContent-Length: 10000000\r\n\r\n"
        camera.send(conn, head.encode())
        while not camera.stopping.wait(client.CAMERA_TIMEOUT_S / 2):
            camera.send(conn, b"\xff")

    camera = RawCamera(trickle)
    camera.start()
    fake = FakeBridge()
    await fake.start()
    try:
        if source == "hello":
            fake.camera_url = camera.url()
            t = OpenDuckBridge(fake.address)
        else:
            t = OpenDuckBridge(fake.address, camera_url=camera.url())
        await t.connect()
        started = time.monotonic()
        frame = await asyncio.wait_for(t.get_frame(), 5 * client.CAMERA_TIMEOUT_S)
        took = time.monotonic() - started
        assert frame is None and took < 3 * client.CAMERA_TIMEOUT_S, (took, t.camera_error)
        assert camera.hung_up.wait(3 * client.CAMERA_TIMEOUT_S), "the fetch read on after it"
        await t.close()
    finally:
        camera.finish()
        await fake.stop()


async def test_a_redirect_is_followed_only_to_the_same_scheme_host_and_port() -> None:
    """`urllib` followed a redirect anywhere it pointed, so a camera, or a bridge whose hello
    named it, could send the fetch to another machine or another service and hand on what came
    back as the camera's frame. A redirect is followed now only to the same scheme, host and
    port, any other is refused with where it pointed, and nothing is asked there."""
    elsewhere = RawCamera(_picture)
    elsewhere.start()
    moves = {
        "/same": "/snapshot.jpg",
        "/port": elsewhere.url(),
        "/scheme": "file:///C:/Users/me/picture.jpg",
        "/ftp": f"ftp://127.0.0.1:{elsewhere.port}/snapshot.jpg",
    }

    def redirecting(conn: socket.socket, camera: RawCamera, path: str) -> None:
        if path in moves:
            head = f"HTTP/1.0 302 Found\r\nLocation: {moves[path]}\r\nContent-Length: 0\r\n\r\n"
            camera.send(conn, head.encode())
        else:
            _picture(conn, camera, path)

    camera = RawCamera(redirecting)
    camera.start()
    try:
        t = OpenDuckBridge(camera_url=camera.url("/same"))
        assert await t.get_frame() is not None, t.camera_error
        for path in ("/port", "/scheme", "/ftp"):
            t = OpenDuckBridge(camera_url=camera.url(path))
            assert await t.get_frame() is None, path
            assert "redirect" in str(t.camera_error) and moves[path] in str(t.camera_error)
        assert elsewhere.paths == [], "a redirect to another port was followed"
    finally:
        camera.finish()
        elsewhere.finish()


@pytest.mark.parametrize("named", ["localhost", "a name whose first address refuses"])
async def test_a_camera_name_with_several_addresses_gives_each_its_share_of_the_deadline(
    named: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The connect tried each address a name resolves to with the whole of what was left, so an
    address that refused slowly spent the deadline the next one needed. On Windows `localhost` is
    `::1` first, and a refused connect there takes about two seconds, so a camera listening on
    127.0.0.1 alone, as camd does by default, never gave a frame, where the old fetch got one.
    Each address now gets its share of what is left, in the order given. The second case puts a
    closed loopback port first, under a name that never reaches a resolver."""
    camera = RawCamera(_picture)
    camera.start()
    host = "localhost"
    if named != "localhost":
        host = "camera.invalid"
        spare = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        spare.bind(("127.0.0.1", 0))
        closed_port = spare.getsockname()[1]
        spare.close()
        resolve = socket.getaddrinfo

        def addresses(name: str, *args: Any, **kwargs: Any) -> Any:
            if name != host:
                return resolve(name, *args, **kwargs)
            return [
                (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (address, port))
                for address, port in (("127.0.0.1", closed_port), ("127.0.0.1", camera.port))
            ]

        monkeypatch.setattr(socket, "getaddrinfo", addresses)
    try:
        t = OpenDuckBridge(camera_url=f"http://{host}:{camera.port}/snapshot.jpg")
        assert await t.get_frame() is not None, t.camera_error
    finally:
        camera.finish()


def test_a_header_byte_just_before_the_deadline_does_not_buy_a_second_deadline() -> None:
    """The socket's timeout was set once, to what was left, before `getresponse()`, which then
    read every header line under it. On Windows a shutdown does not wake a read already waiting,
    so one byte of a header arriving just before the deadline bought the next read a whole
    further timeout, and the fetch ended near twice its deadline. Every read now waits no longer
    than what is left of the one clock, headers included."""
    slack_s = client.CAMERA_TIMEOUT_S / 4  # scheduling, not a second read's worth of waiting
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    done = threading.Event()

    def camera() -> None:
        conn, _ = server.accept()
        try:
            conn.recv(4096)
            conn.sendall(b"HTTP/1.1 200 OK\r\n")
            time.sleep(client.CAMERA_TIMEOUT_S * 0.9)
            conn.sendall(b"X")
            done.wait(client.CAMERA_TIMEOUT_S * 5)
        finally:
            conn.close()

    threading.Thread(target=camera, daemon=True).start()
    started = time.monotonic()
    with pytest.raises(client.FrameTooSlow):
        client.fetch_frame(
            f"http://127.0.0.1:{server.getsockname()[1]}/snapshot.jpg",
            deadline_s=client.CAMERA_TIMEOUT_S,
            max_bytes=client.CAMERA_MAX_BYTES,
        )
    took = time.monotonic() - started
    done.set()
    server.close()
    assert took <= client.CAMERA_TIMEOUT_S + slack_s, f"the fetch took {took:.2f} s"


async def test_a_protocol_mismatch_refuses_rather_than_guessing() -> None:
    fake = FakeBridge(protocol_version=PROTOCOL_VERSION + 1)
    await fake.start()
    t = OpenDuckBridge(fake.address)
    with pytest.raises(TransportError, match="refusing rather than guessing"):
        await t.connect()
    await fake.stop()


async def test_a_missing_bridge_says_so_in_words_an_owner_can_act_on() -> None:
    t = OpenDuckBridge("tcp://127.0.0.1:1")
    with pytest.raises(TransportError, match="roboglot-duck-bridge running"):
        await t.connect()


def test_parse_address() -> None:
    assert parse_address("tcp://duck.local:9871") == ("duck.local", 9871)
    assert parse_address("duck.local:9871") == ("duck.local", 9871)
    assert parse_address("duck.local") == ("duck.local", 9871)
    with pytest.raises(TransportError):
        parse_address("http://duck.local:9871")


async def test_an_answer_over_asyncios_default_line_reaches_the_client(bridge: FakeBridge) -> None:
    """Over 64 KiB `readline` raised out of the pump, and every request after it, the
    heartbeat's and the stop's among them, waited out its timeout."""
    bridge.state_padding = 100_000
    t = OpenDuckBridge(bridge.address, request_timeout_s=1.0)
    await t.connect()
    assert len((await t.request(STATE))["notes"]) == 100_000
    await t.heartbeat()
    await t.close()


async def test_a_line_over_the_limit_costs_that_line_and_not_the_link(
    bridge: FakeBridge, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("roboglot_open_duck.bridge.READER_LIMIT", 2**12)
    t = OpenDuckBridge(bridge.address, request_timeout_s=0.5)
    await t.connect()
    bridge.state_padding = 10_000
    with pytest.raises(TransportError, match="no answer"):
        await t.request(STATE)
    bridge.state_padding = 0
    t.request_timeout_s = 3.0
    await t.heartbeat()
    await t.stop()
    assert t.stop_error is None
    await t.close()


async def test_a_line_the_client_cannot_read_does_not_end_the_link() -> None:
    """Each of these used to raise out of the pump, where only broken JSON was skipped."""
    t = OpenDuckBridge("tcp://127.0.0.1:1")
    t._reader = asyncio.StreamReader()
    answer = asyncio.get_running_loop().create_future()
    t._pending[7] = answer
    pump = asyncio.create_task(t._read_loop())
    for line in (
        b"5\n",
        b'{"id": "seven", "result": 1}\n',
        b"\xff\xfe\n",
        b"not json\n",
        b'{"method": "duck.state", "params": [1]}\n',
    ):
        t._reader.feed_data(line)
    t._reader.feed_data(b'{"id": 7, "result": "still here"}\n')
    assert await asyncio.wait_for(answer, 2.0) == "still here"
    assert t._last_state is None
    t._reader.feed_eof()
    await asyncio.wait_for(pump, 2.0)


async def test_an_error_that_is_no_object_still_answers_its_request() -> None:
    """The request was let go before its error was read, so an error Roboglot could not read
    left it waiting for an answer that had already come."""
    t = OpenDuckBridge("tcp://127.0.0.1:1")
    t._reader = asyncio.StreamReader()
    answer = asyncio.get_running_loop().create_future()
    t._pending[3] = answer
    pump = asyncio.create_task(t._read_loop())
    t._reader.feed_data(b'{"id": 3, "error": "the bus is down"}\n')
    with pytest.raises(TransportError, match="the bus is down"):
        await asyncio.wait_for(answer, 2.0)
    t._reader.feed_eof()
    await asyncio.wait_for(pump, 2.0)


async def test_a_link_that_fails_tells_whoever_is_waiting_at_once() -> None:
    """A reset raised out of the pump, left the request to wait out its timeout, and left the
    link looking open to every write after it."""

    class Reset:
        async def readline(self) -> bytes:
            raise ConnectionResetError("reset by peer")

    t = OpenDuckBridge("tcp://127.0.0.1:1")
    t._reader = Reset()  # type: ignore[assignment]
    answer = asyncio.get_running_loop().create_future()
    t._pending[7] = answer
    await asyncio.wait_for(t._read_loop(), 2.0)
    with pytest.raises(TransportError, match="the link to the bridge failed"):
        await asyncio.wait_for(answer, 2.0)
    with pytest.raises(TransportError, match="closed the connection"):
        await t.notify(COMMAND, {"vx": 0.0})


async def test_any_other_error_from_the_reader_ends_the_link_as_a_reset_does() -> None:
    """Nothing but a socket's errors and an over-long line is expected of the reader, and
    anything else left the pump dead with the link looking open."""

    class Odd:
        async def readline(self) -> bytes:
            raise RuntimeError("the reader broke")

    t = OpenDuckBridge("tcp://127.0.0.1:1")
    t._reader = Odd()  # type: ignore[assignment]
    answer = asyncio.get_running_loop().create_future()
    t._pending[7] = answer
    await asyncio.wait_for(t._read_loop(), 2.0)
    with pytest.raises(TransportError, match="RuntimeError: the reader broke"):
        await asyncio.wait_for(answer, 2.0)
    assert t._closed


# ── the intents ─────────────────────────────────────────────────────────────────────────


async def test_walking_feeds_the_deadman_and_then_stops(bridge: FakeBridge) -> None:
    """`move` re-sends at 10 Hz, which is what keeps the daemon's deadman from tripping."""
    adapter = OpenDuckAdapter(OpenDuckBridge(bridge.address))
    manifest = await adapter.connect()
    ex = Executor(
        registry_from_manifest(manifest, adapter),
        adapter,
        contract=GLOT.frontmatter,
        detector=ColorBlobDetector(),
        confirm=allow_all,
    )
    assert (await ex.run_verb("move", {"vx": 0.1, "duration_s": 0.3})).ok
    commands = [c for c in (await bridge.wait_notifications(3)) if c.get("method") == COMMAND]
    assert len(commands) >= 3
    assert (commands[0].get("params") or {})["vx"] == pytest.approx(0.1)
    assert any(r["method"] == "duck.stop" for r in bridge.requests)
    await adapter.disconnect()


async def test_a_gaze_becomes_head_angles_and_is_clamped_to_this_neck(bridge: FakeBridge) -> None:
    t = OpenDuckBridge(bridge.address)
    await t.connect()
    ack = await t.send_intent(Intent.look(1.0, 0.0, 0.0))
    assert ack.accepted and ack.reason is None
    await bridge.wait_notifications(1)  # a notification has no reply to wait on
    head = (bridge.commands()[-1])["head"]
    assert set(head) == {"head_yaw", "head_pitch"}  # unset joints are left where they are
    assert head["head_yaw"] == pytest.approx(0.0)

    # a yaw past the neck's travel is clamped, and the ack says so
    far = math.radians(80)
    ack = await t.send_intent(Intent.look(math.cos(far), math.sin(far), 0.0))
    assert ack.accepted and "clamped" in (ack.reason or "")
    await bridge.wait_notifications(2)
    assert (bridge.commands()[-1])["head"]["head_yaw"] == pytest.approx(HEAD_YAW_RANGE[1])
    await t.close()


async def test_sounds_and_antennas_go_out_as_this_robot_s_own_vocabulary(
    bridge: FakeBridge,
) -> None:
    t = OpenDuckBridge(bridge.address)
    await t.connect()
    assert (await t.send_intent(Intent.sound("greet", "hello"))).accepted
    assert bridge.requests[-1]["params"] == {"mood": "greet"}
    assert (await t.send_intent(Intent.sound("moonwalk", None))).accepted
    assert bridge.requests[-1]["params"] == {"mood": "chirp"}  # unknown moods degrade
    assert (await t.send_intent(Intent.do("antennas:perk"))).accepted
    assert bridge.requests[-1]["params"] == {"gesture": "perk"}
    await t.close()


async def test_the_bridge_has_no_word_for_going_limp(bridge: FakeBridge) -> None:
    """`stop` is a zero twist. Nothing in the protocol can reach torque, by construction."""
    t = OpenDuckBridge(bridge.address)
    await t.connect()
    assert not (await t.send_intent(Intent.enable(False))).accepted
    for skill in ("kick_left", "ground_pick", "sit_toggle"):
        ack = await t.send_intent(Intent.do(skill))
        assert not ack.accepted and "has no skill" in (ack.reason or "")
    await t.stop()
    assert bridge.requests[-1]["method"] == "duck.stop"
    assert not [r for r in bridge.requests if "relax" in r["method"] or "torque" in r["method"]]
    await t.close()


# ── health and state ────────────────────────────────────────────────────────────────────


async def test_a_starved_control_loop_fails_the_heartbeat() -> None:
    fake = FakeBridge(loop_hz=31.2)
    await fake.start()
    t = OpenDuckBridge(fake.address)
    await t.connect()
    with pytest.raises(HeartbeatError, match=r"31\.2"):
        await t.heartbeat()
    assert MIN_LOOP_HZ > 31.2
    await t.close()
    await fake.stop()


async def test_an_unhealthy_bridge_fails_the_heartbeat() -> None:
    fake = FakeBridge(healthy=False)
    await fake.start()
    t = OpenDuckBridge(fake.address)
    await t.connect()
    with pytest.raises(HeartbeatError, match="serial bus"):
        await t.heartbeat()
    await t.close()
    await fake.stop()


async def test_state_carries_no_battery_and_names_its_assumptions(bridge: FakeBridge) -> None:
    t = OpenDuckBridge(bridge.address)
    await t.connect()
    state = await t.get_state()
    assert state.battery_percent is None  # nothing in the runtime reports one
    assert state.posture == "standing" and not state.fallen
    assert state.extras["loop_hz"] == pytest.approx(49.8)
    assert "FALL_SIGNAL" in state.extras["assumptions"]
    await t.close()


async def test_a_fallen_duck_is_reported_and_nothing_tries_to_stand_it_up() -> None:
    fake = FakeBridge(fallen=True)
    await fake.start()
    adapter = OpenDuckAdapter(OpenDuckBridge(fake.address))
    manifest = await adapter.connect()
    assert not manifest.provides("stand_up")
    ex = Executor(
        registry_from_manifest(manifest, adapter),
        adapter,
        contract=GLOT.frontmatter,
        detector=ColorBlobDetector(),
        confirm=allow_all,
    )
    result = await ex.run_verb("move", {"vx": 0.1, "duration_s": 0.2})
    assert not result.ok and "by hand" in result.summary
    assert not [r for r in fake.requests if r["method"] == COMMAND]
    await adapter.disconnect()
    await fake.stop()


# ── the manifest the handshake produces ─────────────────────────────────────────────────


async def test_a_duck_with_no_camera_or_speaker_loses_exactly_those_verbs() -> None:
    fake = FakeBridge(
        capabilities={"camera": False, "speaker": False, "antennas": False, "head": False}
    )
    await fake.start()
    adapter = OpenDuckAdapter(OpenDuckBridge(fake.address))
    manifest = await adapter.connect()
    assert set(manifest.verb_names()) == {"report_state", "stop", "move"}
    assert manifest.backend == "bridge"
    assert manifest.extras["expression_features"] == {
        "camera": False,
        "speaker": False,
        "antennas": False,
        "microphone": False,
    }
    await adapter.disconnect()
    await fake.stop()


async def test_head_control_is_off_unless_the_bridge_says_it_is_on() -> None:
    fake = FakeBridge(capabilities={**FULL_DUCK, "head": False})
    await fake.start()
    adapter = OpenDuckAdapter(OpenDuckBridge(fake.address))
    manifest = await adapter.connect()
    assert not manifest.provides("gaze") and "gaze" not in manifest.intents
    assert manifest.provides("search_scan")  # it still scans, by turning the body
    await adapter.disconnect()
    await fake.stop()


async def test_a_bridge_that_cannot_see_falls_reports_unknown_not_standing() -> None:
    """A duck nobody is watching must never read as upright. Nothing upstream reports a
    fall, so a bridge with no IMU tap sends null and Roboglot says it does not know."""
    fake = FakeBridge(fallen=None)
    await fake.start()
    t = OpenDuckBridge(fake.address)
    await t.connect()
    state = await t.get_state()
    assert state.posture == "unknown" and state.fallen is False
    assert state.extras["fall_detection"] is False
    await t.close()
    await fake.stop()


# ── the token the installer writes ──────────────────────────────────────────────────────


async def test_the_token_travels_in_the_handshake_not_the_address() -> None:
    fake = FakeBridge(token="s3cret")
    await fake.start()
    t = OpenDuckBridge(fake.address, token="s3cret")
    await t.connect()
    assert fake.requests[0]["params"]["token"] == "s3cret"
    assert "s3cret" not in t.address, "an address is printed and lands in transcripts"
    await t.close()
    await fake.stop()


async def test_a_missing_token_says_which_flag_to_use() -> None:
    """The bridge's own installer writes a token, so this is what a by-the-book duck does."""
    fake = FakeBridge(token="s3cret")
    await fake.start()
    t = OpenDuckBridge(fake.address)
    with pytest.raises(TransportError, match="--token"):
        await t.connect()
    assert TOKEN_ENV in str(await _caught(fake.address))
    await fake.stop()


async def _caught(address: str) -> Exception:
    try:
        await OpenDuckBridge(address).connect()
    except TransportError as e:
        return e
    raise AssertionError("expected a refusal")


async def test_the_token_can_come_from_the_environment(monkeypatch) -> None:
    fake = FakeBridge(token="from-the-env")
    await fake.start()
    monkeypatch.setenv(TOKEN_ENV, "from-the-env")
    t = OpenDuckBridge(fake.address)
    await t.connect()
    assert fake.requests[0]["params"]["token"] == "from-the-env"
    await t.close()
    await fake.stop()


# ── an unreadable state, and a fall nobody is watching for ──────────────────────────────


async def test_a_state_that_could_not_be_read_is_not_a_healthy_duck() -> None:
    """The client used to suppress the error and return a default frame, so `fallen` was
    False, `policy_running` was None, both preconditions passed, and Roboglot would start a
    `move` into a link that was not there. `report_state` printed a pose of exactly
    (0, 0, 0) for a robot that has no odometry at all."""
    fake = FakeBridge()
    await fake.start()
    t = OpenDuckBridge(fake.address, request_timeout_s=0.2)
    await t.connect()
    fake.silent = True  # the socket is still up; nothing answers on it

    state = await t.get_state()
    assert state.extras["state_stale"], "a failed read is silence, not a verdict"
    assert state.posture == "unknown"
    assert "UNREADABLE" in state.summary()
    assert state.x is None and state.y is None and state.theta is None

    from roboglot_open_duck.verbs import open_duck_conditions

    for name, check in open_duck_conditions().items():
        assert check(state) is not None, f"{name} must refuse on a state it could not read"
    await t.close()
    await fake.stop()


async def test_a_fall_blind_duck_says_so_in_every_observation() -> None:
    """`posture=unknown` on its own reads as "no news". It is not: nothing is watching, so
    `fallen=False` is silence, and a pilot told that moving verbs refuse when it is down
    would read an accepted move as proof that it is upright."""
    fake = FakeBridge(fallen=None)  # the bridge cannot see falls at all
    await fake.start()
    t = OpenDuckBridge(fake.address)
    await t.connect()
    state = await t.get_state()
    assert state.extras["fall_detection"] is False
    assert "fall-blind" in state.summary()
    assert state.posture == "unknown"
    await t.close()
    await fake.stop()


async def test_a_bridge_with_no_token_says_so_when_one_was_sent() -> None:
    """An unreadable token file left authentication off while the client happily sent one,
    and nothing anywhere could tell the difference."""
    fake = FakeBridge()
    fake.safety = {"auth": "none", "deadman_ms": 300}
    await fake.start()
    t = OpenDuckBridge(fake.address, token="s3cret")
    await t.connect()
    assert t.safety["auth"] == "none"
    assert t.auth_warning and "was not checked" in t.auth_warning
    await t.close()
    await fake.stop()
