"""Talk's one way out: `ble://` to a link, `http://` to an inbox over the LAN, nothing else; and
the line between a host that never answered, which is tried again, and one that said no."""

from __future__ import annotations

import socket
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from quackd.passport import fetch
from quackd.passport.errors import PassportError
from quackd.radio.links import LinkDown
from quackd.radio.transport import Transports, http_post


class Inbox(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        body = self.rfile.read(int(self.headers["Content-Length"]))
        kind = self.headers["Content-Type"]
        reply = f"{kind}:{body.decode()}".encode()
        self.send_response(200 if self.path.endswith("/inbox") else 404)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(reply)))
        self.end_headers()
        self.wfile.write(reply)

    def log_message(self, format: str, *args: object) -> None:
        pass


@pytest.fixture
def inbox() -> Iterator[str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), Inbox)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


def closed_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


class Link:
    def __init__(self) -> None:
        self.posted: list[tuple[str, str]] = []

    def post(self, route: str, wire: str) -> tuple[int, str | None]:
        self.posted.append((route, wire))
        return 204, None


def test_each_route_goes_its_own_way_and_no_other() -> None:
    sent: list[tuple[str, str]] = []
    link = Link()
    transports = Transports(http=lambda r, w: sent.append((r, w)) or (200, "ok"), link=link)  # type: ignore[arg-type,func-returns-value]
    assert transports.post("ble://0000abcd", "a.b.c") == (204, None)
    assert transports.post("http://10.0.0.2:8790/robot-passport/inbox", "a.b.c") == (200, "ok")
    assert link.posted == [("ble://0000abcd", "a.b.c")]
    assert sent == [("http://10.0.0.2:8790/robot-passport/inbox", "a.b.c")]
    for elsewhere in ("mqtt://broker/x", "file:///etc/passwd", "ble:0000abcd"):
        with pytest.raises(PassportError, match="not a route") as refused:
            transports.post(elsewhere, "a.b.c")
        assert refused.value.rule == "6.2"
    with pytest.raises(LinkDown, match="no radio"):
        Transports(http=lambda r, w: (200, None)).post("ble://0000abcd", "a.b.c")


def test_a_message_goes_to_an_http_inbox_labelled_as_what_it_is(inbox: str) -> None:
    assert http_post(f"{inbox}/robot-passport/inbox", "h.p.s") == (200, "application/jose:h.p.s")
    assert http_post(f"{inbox}/robot-passport/inbox", '{"a":1}') == (
        200,
        'application/json:{"a":1}',
    )
    status, _reply = http_post(f"{inbox}/elsewhere", "h.p.s")
    assert status == 404


def test_a_host_that_never_answers_is_unreachable_and_one_that_says_no_is_not(inbox: str) -> None:
    with pytest.raises(fetch.Unreachable, match="could not be reached") as nobody:
        http_post(f"http://127.0.0.1:{closed_port()}/robot-passport/inbox", "h.p.s")
    assert nobody.value.rule == "5.7"
    # a host that takes the connection and then says nothing at all
    silent = socket.socket()
    silent.bind(("127.0.0.1", 0))
    silent.listen()
    try:
        with pytest.raises(fetch.Unreachable, match="took more than"):
            fetch.fetch(f"http://127.0.0.1:{silent.getsockname()[1]}/x", timeout=0.3)
    finally:
        silent.close()
    with pytest.raises(fetch.HttpStatus) as answered:
        fetch.fetch(f"{inbox}/robot-passport.json")
    assert not isinstance(answered.value, fetch.Unreachable)
