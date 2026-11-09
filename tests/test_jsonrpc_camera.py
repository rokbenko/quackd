"""`fetch_still`, and what becomes of a camera a robot of the family is given.

A Microduck and a Huebot read a camera through one transport, so a stream, a body past the cap, a
picture that never finishes and a header that never ends are the same bytes whichever body asked,
and the tests of `fetch_still` itself take no robot at all. The tests with a robot connected, of a
camera that streams, one that never answers and one still reading when the transport closes, run
on both bodies.
"""

from __future__ import annotations

import asyncio
import contextlib
import http.client
import io
import threading
import time
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest
from PIL import Image

from quackd_microduck import MicroduckAdapter
from quackd_microduck.transports.jsonrpc_unix import (
    SNAPSHOT_MAX_BYTES,
    JsonRpcUnixTransport,
    NotAStill,
    SnapshotRefused,
    StillTooSlow,
    fetch_still,
)
from tests.fake_robotd import MEMBERS, Member, on_its_own_loop

#: One small JPEG, the still every camera below sends.
_STILL = io.BytesIO()
Image.new("RGB", (32, 24), (90, 40, 40)).save(_STILL, format="JPEG")
STILL_JPEG = _STILL.getvalue()


@pytest.fixture(params=MEMBERS, ids=lambda member: member.body.name)
def member(request: pytest.FixtureRequest) -> Member:
    """Each body of the family in turn, with the fake that answers for it."""
    return request.param


@contextlib.contextmanager
def _camera(kind: str, **opts: float) -> Iterator[tuple[str, threading.Event]]:
    """An HTTP camera on loopback, and an event set once the client has dropped what it was
    sent. `still` answers one JPEG; `mjpeg` a `multipart/x-mixed-replace` stream of them;
    `endless` an `image/jpeg` body with no end; `trickle` one byte every `every_s`. The rest
    write the wire by hand: `slow-headers` a header that never ends, a byte at a time;
    `redirect` a 302 whose body never ends; `trailers` a chunked body whose trailer never ends;
    `truncated` 10 of the 100 bytes its length promises; `error` a 503 whose page trickles. All
    but `still` and `truncated` send for `for_s`, a minute unless told otherwise, so that a fetch
    that never lets go fails its test rather than hanging it."""
    dropped = threading.Event()

    class Camera(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:
            pass

        def do_GET(self) -> None:
            if kind in RAW:
                with contextlib.suppress(OSError):
                    self.wfile.write(RAW[kind])
                    if kind != "truncated":
                        self.keep_sending()
                return
            self.send_response(200)
            if kind == "still":
                self.send_header("Content-Type", "image/jpeg")
                self.send_header("Content-Length", str(len(STILL_JPEG)))
                self.end_headers()
                self.wfile.write(STILL_JPEG)
                return
            boundary = "frame"
            mime = f"multipart/x-mixed-replace; boundary={boundary}"
            self.send_header("Content-Type", mime if kind == "mjpeg" else "image/jpeg")
            self.end_headers()
            until = time.monotonic() + opts.get("for_s", 60.0)
            try:
                while time.monotonic() < until:
                    if kind == "mjpeg":
                        head = f"--{boundary}\r\nContent-Type: image/jpeg\r\n\r\n".encode()
                        self.wfile.write(head + STILL_JPEG + b"\r\n")
                        time.sleep(0.02)
                    elif kind == "endless":
                        self.wfile.write(STILL_JPEG * 16)
                    else:
                        self.wfile.write(b"x")
                        self.wfile.flush()
                        time.sleep(opts.get("every_s", 0.02))
            except OSError:
                dropped.set()

        def keep_sending(self) -> None:
            until = time.monotonic() + opts.get("for_s", 60.0)
            more = {"redirect": b"<p>moved</p>" * 1000, "trailers": b"X-More: yes\r\n"}
            try:
                while time.monotonic() < until:
                    self.wfile.write(more.get(kind, b"x"))
                    if kind != "redirect":
                        time.sleep(0.05)
            except OSError:
                dropped.set()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Camera)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, name="fake-camera", daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}/still.jpg", dropped
    finally:
        server.shutdown()
        server.server_close()


#: What each hand-written camera in `_camera` sends first, before `keep_sending` goes on.
RAW = {
    "slow-headers": b"HTTP/1.1 200 OK\r\nContent-Type: image/jpeg\r\nX-Slow: ",
    "redirect": (
        b"HTTP/1.1 302 Found\r\nLocation: /elsewhere.jpg\r\nContent-Type: text/html\r\n\r\n"
    ),
    "trailers": (
        b"HTTP/1.1 200 OK\r\nContent-Type: image/jpeg\r\nTransfer-Encoding: chunked\r\n\r\n"
        b"1\r\nx\r\n0\r\n"
    ),
    "truncated": (
        b"HTTP/1.1 200 OK\r\nContent-Type: image/jpeg\r\nContent-Length: 100\r\n\r\n" + b"x" * 10
    ),
    "error": b"HTTP/1.1 503 Service Unavailable\r\nContent-Type: text/plain\r\n\r\n",
}


def test_a_still_is_fetched_whole() -> None:
    with _camera("still") as (url, _):
        assert fetch_still(url, deadline_s=3.0, max_bytes=SNAPSHOT_MAX_BYTES) == STILL_JPEG


def test_a_stream_is_refused_by_its_type_before_any_of_it_is_read() -> None:
    with _camera("mjpeg") as (url, dropped):
        started = time.monotonic()
        with pytest.raises(NotAStill) as refused:
            fetch_still(url, deadline_s=3.0, max_bytes=SNAPSHOT_MAX_BYTES)
        assert time.monotonic() - started < 2.0
        said = str(refused.value)
        assert "multipart/x-mixed-replace stream, not one still image" in said, said
        assert "a URL that returns one still image, a JPEG or a PNG" in said, said
        assert dropped.wait(5.0), "the stream is still being read"


def test_a_body_past_the_cap_is_refused_and_dropped() -> None:
    """An answer that says it is a JPEG and never ends: the cap ends it, long before the
    deadline would."""
    with _camera("endless") as (url, dropped):
        started = time.monotonic()
        with pytest.raises(NotAStill, match=r"more than 0\.2 MB for one picture"):
            fetch_still(url, deadline_s=10.0, max_bytes=200_000)
        assert time.monotonic() - started < 8.0
        assert dropped.wait(5.0), "the body is still being read"


def test_a_picture_still_arriving_at_its_deadline_is_given_up() -> None:
    """A byte every 20 ms passes every read's own timeout, so only the deadline on the whole
    fetch ends it. The camera stops after 6 s, so a fetch without that deadline ends there,
    with every byte it was sent."""
    with _camera("trickle", every_s=0.02, for_s=6.0) as (url, _):
        started = time.monotonic()
        with pytest.raises(StillTooSlow, match=r"no whole picture within 0\.5 s"):
            fetch_still(url, deadline_s=0.5, max_bytes=SNAPSHOT_MAX_BYTES)
        assert time.monotonic() - started < 4.0


def test_a_fetch_told_to_stop_stops_at_its_next_read() -> None:
    """What `close()` does to a fetch still reading in its thread. The camera stops after 6 s,
    and the deadline is longer, so a fetch that ignored the stop would read until then."""
    stop = threading.Event()
    with _camera("trickle", every_s=0.02, for_s=6.0) as (url, _):
        threading.Timer(0.3, stop.set).start()
        started = time.monotonic()
        with pytest.raises(ConnectionAbortedError, match="the transport closed"):
            fetch_still(url, deadline_s=30.0, max_bytes=SNAPSHOT_MAX_BYTES, stop=stop)
        assert time.monotonic() - started < 4.0


@pytest.mark.parametrize("kind", ["slow-headers", "trailers"])
def test_a_fetch_is_cut_off_at_its_deadline_wherever_it_is_reading(kind: str) -> None:
    """A header that never ends and a chunked body's trailer that never ends are read inside
    urllib and http.client, where no check between reads of the body can see them, and each
    of their bytes comes inside every read's own timeout. The guard shuts the socket down at the
    deadline all the same. The camera stops after 4 s, so a fetch it did not cut ends there."""
    with _camera(kind, for_s=4.0) as (url, _):
        started = time.monotonic()
        with pytest.raises(StillTooSlow, match=r"no whole picture within 0\.5 s"):
            fetch_still(url, deadline_s=0.5, max_bytes=SNAPSHOT_MAX_BYTES)
        assert time.monotonic() - started < 3.0


def test_a_stop_ends_a_fetch_still_reading_its_headers() -> None:
    stop = threading.Event()
    with _camera("slow-headers", for_s=4.0) as (url, _):
        threading.Timer(0.3, stop.set).start()
        started = time.monotonic()
        with pytest.raises(ConnectionAbortedError, match="the transport closed"):
            fetch_still(url, deadline_s=30.0, max_bytes=SNAPSHOT_MAX_BYTES, stop=stop)
        assert time.monotonic() - started < 3.0


def test_a_redirect_is_refused_without_reading_its_body() -> None:
    """urllib reads a redirect's whole body before it follows it, past every bound a fetch
    sets, so a redirect is refused, naming where it points, and its body is never read."""
    with _camera("redirect", for_s=6.0) as (url, dropped):
        started = time.monotonic()
        with pytest.raises(NotAStill) as refused:
            fetch_still(url, deadline_s=5.0, max_bytes=SNAPSHOT_MAX_BYTES)
        assert time.monotonic() - started < 3.0
        said = str(refused.value)
        assert "HTTP 302 Found, a redirect to http://127.0.0.1:" in said, said
        assert "/elsewhere.jpg, which quackd does not follow" in said, said
        assert dropped.wait(5.0), "the redirect's body is still being read"


def test_a_body_shorter_than_its_length_is_no_picture() -> None:
    with _camera("truncated") as (url, _), pytest.raises(http.client.IncompleteRead):
        fetch_still(url, deadline_s=3.0, max_bytes=SNAPSHOT_MAX_BYTES)


def test_an_error_page_that_trickles_is_read_no_longer_than_the_deadline() -> None:
    with _camera("error", for_s=4.0) as (url, _):
        started = time.monotonic()
        with pytest.raises(SnapshotRefused) as refused:
            fetch_still(url, deadline_s=0.5, max_bytes=SNAPSHOT_MAX_BYTES)
        assert refused.value.code == 503
        assert time.monotonic() - started < 3.0


# ── a camera on a robot that connected ───────────────────────────────────────────────────


def _transport(adapter: MicroduckAdapter) -> JsonRpcUnixTransport:
    assert isinstance(adapter.transport, JsonRpcUnixTransport)
    return adapter.transport


async def test_a_camera_that_never_answers_is_no_camera(member: Member) -> None:
    """`get_frame` used to raise, under a comment promising it would not, and `AgentLoop._observe`
    calls it every step and catches nothing, so one refused HTTP response ended the session. A
    camera that never answers is no camera now: no frame and no exception, no verb that needs a
    picture offered, and the connect knows why, on either body."""
    fake = member.fake()
    await fake.start()
    try:
        adapter = await member.connected(fake, camera_url="http://127.0.0.1:9/never")
        t = _transport(adapter)
        assert t.camera_working is False
        assert adapter.manifest is not None and "observe" not in adapter.manifest.verb_names()
        assert "camera" not in adapter.manifest.sensors
        assert await adapter.get_frame() is None and adapter.camera_error
        assert await t.get_frame() is None
        health = t.camera_health()
        assert health["configured"] is True and health["ok"] is False and health["error"]
        await adapter.disconnect()
    finally:
        await fake.stop()


def test_a_camera_that_streams_is_named_and_leaves_nothing_reading(member: Member) -> None:
    """An MJPEG camera answers each request with a body that never ends. Read as a still, it was
    read for as long as it ran: past the run's end, by a thread the transport's close did not
    stop, which `asyncio.run` then waited on at exit, while the connect waited out its time and
    said only that no frame came. Now the connect names it and offers no verb that needs a
    picture, the close is prompt, and the run's loop ends, on either body."""
    said: dict[str, Any] = {}
    with on_its_own_loop(member.fake) as fake, _camera("mjpeg") as (url, dropped):

        async def run() -> None:
            adapter = await member.connected(fake, camera_url=url)
            said["camera"] = _transport(adapter).camera_working
            said["error"] = adapter.camera_error
            said["verbs"] = adapter.manifest.verb_names() if adapter.manifest else []
            started = time.monotonic()
            await adapter.disconnect()
            said["close_s"] = time.monotonic() - started

        runner = threading.Thread(target=asyncio.run, args=(run(),), daemon=True)
        runner.start()
        runner.join(20.0)
        assert not runner.is_alive(), "asyncio.run is still waiting on a camera fetch"
        assert said["camera"] is False
        assert "multipart/x-mixed-replace stream, not one still image" in said["error"], said
        assert not {"observe", "go_to", "search_scan", "approach_and"} & set(said["verbs"])
        assert said["close_s"] < 1.0, said
        assert dropped.wait(5.0), "the stream is still being read"


def test_a_close_stops_the_fetch_still_reading(member: Member) -> None:
    """A camera that trickles, so each fetch reads until its 3 s deadline. A close in the middle
    of one ends it at its next read, rather than leaving it reading behind the close for the
    rest of its 3 s, with `asyncio.run` waiting on its thread at exit."""
    when: dict[str, float] = {}
    with (
        on_its_own_loop(member.fake) as fake,
        _camera("trickle", every_s=0.02, for_s=30.0) as (url, dropped),
    ):

        async def run() -> None:
            camera = JsonRpcUnixTransport(
                fake.address, body=member.body, camera_url=url, camera_wait_s=0.3
            )
            adapter = member.adapter(camera)
            await adapter.connect()
            assert camera.camera_working is False
            await asyncio.sleep(0.3)  # the first fetch is still reading
            await adapter.disconnect()
            when["closed"] = time.monotonic()

        runner = threading.Thread(target=asyncio.run, args=(run(),), daemon=True)
        runner.start()
        runner.join(20.0)
        assert not runner.is_alive(), "asyncio.run is still waiting on a camera fetch"
        exited = time.monotonic()
        assert "closed" in when, "the run did not get as far as its close"
        assert dropped.wait(1.0), "the fetch read on behind the close"
        assert exited - when["closed"] < 1.5, "asyncio.run waited on a fetch's thread"
