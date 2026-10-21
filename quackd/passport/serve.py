"""Serving passports over HTTP (spec 5.1): the well-known URL, an index, and nothing else.

One robot is served at `/.well-known/robot-passport.json`, and at `.jws` once it is signed.
Several are served each under `/robots/<name>/`, with an index at the well-known URL listing
them on this same host (spec 2.7), so a reader is never sent anywhere else. The standard
library's threading server is enough: a passport is a few kilobytes, read rarely.

It is on a network, so nobody on it may hold it up. A connection gets five seconds a read and
ten in all, at most 32 are served at once and the rest are closed as they arrive, and a POST
says how long it is before a byte of it is read.
"""

from __future__ import annotations

import contextlib
import json
import socket
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from quackd.passport import strict
from quackd.passport.beacon import WELL_KNOWN_JSON, WELL_KNOWN_JWS
from quackd.passport.document import VERSION

INBOX_PATH = "/robot-passport/inbox"
JSON_TYPE = "application/json"
JWS_TYPE = "application/jose"
READ_S = 5.0
CONNECTION_S = 10.0
CONNECTIONS = 32

#: What a talk inbox is: the request's body, its content type and the address it came from in,
#: a status, a body and its content type out.
Inbox = Callable[[bytes, str, str], tuple[int, bytes | None, str]]


@dataclass(frozen=True)
class Served:
    """One robot's passport: its exact bytes, and its JWS once it is signed."""

    name: str
    passport: bytes
    jws: str | None = None

    @property
    def id(self) -> str:
        return str(json.loads(self.passport)["id"])


def robot_paths(name: str) -> tuple[str, str]:
    return f"/robots/{name}/robot-passport.json", f"/robots/{name}/robot-passport.jws"


def routes(robots: Sequence[Served]) -> dict[str, tuple[bytes, str]]:
    """Every path this server answers, with its body and media type."""
    table: dict[str, tuple[bytes, str]] = {}
    for robot in robots:
        json_path, jws_path = robot_paths(robot.name)
        table[json_path] = (robot.passport, JSON_TYPE)
        if robot.jws is not None:
            table[jws_path] = (robot.jws.encode("ascii"), JWS_TYPE)
    if len(robots) == 1:
        only = robots[0]
        table[WELL_KNOWN_JSON] = (only.passport, JSON_TYPE)
        if only.jws is not None:
            table[WELL_KNOWN_JWS] = (only.jws.encode("ascii"), JWS_TYPE)
    elif robots:
        index = {
            "passport": VERSION,
            "robots": [{"name": r.name, "id": r.id, "url": robot_paths(r.name)[0]} for r in robots],
        }
        table[WELL_KNOWN_JSON] = (strict.dumps(index), JSON_TYPE)
    return table


class PassportServer:
    """An HTTP server for some robots' passports, on a thread of its own."""

    def __init__(
        self,
        robots: Sequence[Served],
        *,
        host: str = "0.0.0.0",
        port: int = 0,
        inbox: Inbox | None = None,
        inbox_limit: int = 4096,
    ) -> None:
        self._routes = routes(robots)
        self._inbox = inbox
        self._inbox_limit = inbox_limit
        self._httpd = _Bounded((host, port), self._handler())
        self._thread: threading.Thread | None = None

    @property
    def port(self) -> int:
        return int(self._httpd.server_address[1])

    def update(self, robots: Sequence[Served]) -> None:
        """Serve these passports from now on: one assignment, so a request sees old or new."""
        self._routes = routes(robots)

    def listen(self, inbox: Inbox) -> None:
        """Take talk messages at `INBOX_PATH` from now on, handing each to `inbox`."""
        self._inbox = inbox

    def start(self) -> PassportServer:
        self._thread = threading.Thread(
            target=self._httpd.serve_forever, name="passport-server", daemon=True
        )
        self._thread.start()
        return self

    def close(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)

    def _handler(self) -> type[BaseHTTPRequestHandler]:
        server = self

        class Handler(BaseHTTPRequestHandler):
            server_version = "quackd-passport"
            timeout = READ_S

            def setup(self) -> None:
                super().setup()
                # a client that sends a byte every few seconds never trips the read timeout,
                # so the connection as a whole has a deadline too
                self._deadline = threading.Timer(CONNECTION_S, self._cut)
                self._deadline.daemon = True
                self._deadline.start()

            def _cut(self) -> None:
                with contextlib.suppress(OSError):
                    self.connection.shutdown(socket.SHUT_RDWR)

            def finish(self) -> None:
                self._deadline.cancel()
                with contextlib.suppress(OSError):
                    super().finish()

            def version_string(self) -> str:
                return self.server_version  # not which Python, which is nobody's business

            def _send(self, status: int, body: bytes = b"", kind: str = "text/plain") -> None:
                self.send_response(status)
                self.send_header("Content-Type", kind)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                if self.command != "HEAD":
                    self.wfile.write(body)

            def do_GET(self) -> None:
                found = server._routes.get(self.path.split("?", 1)[0])
                if found is None:
                    self._send(404, b"no passport here\n")
                    return
                body, kind = found
                self._send(200, body, kind)

            def do_HEAD(self) -> None:
                self.do_GET()

            def do_POST(self) -> None:
                if self.path != INBOX_PATH or server._inbox is None:
                    self._send(404, b"no inbox here\n")
                    return
                said = self.headers.get("Content-Length")
                if said is None:
                    self._send(411, b"a message says how long it is\n")
                    return
                if not said.strip().isdigit():
                    self._send(400, b"a Content-Length that is not a length\n")
                    return
                length = int(said)
                if length > server._inbox_limit:
                    self._send(413, b"too large\n")
                    return
                body = self.rfile.read(length)
                kind = (self.headers.get("Content-Type") or "").split(";")[0].strip()
                status, reply, reply_kind = server._inbox(body, kind, self.client_address[0])
                self._send(status, reply or b"", reply_kind)

            def log_message(self, format: str, *args: Any) -> None:
                pass

        return Handler


class _Bounded(ThreadingHTTPServer):
    """A threading server that serves at most `CONNECTIONS` clients at once, closing the rest
    as they arrive rather than starting a thread for each, and says nothing about a client that
    hung up or was cut off."""

    daemon_threads = True
    block_on_close = False  # closing it never waits out a slow client

    def __init__(self, address: tuple[str, int], handler: type[BaseHTTPRequestHandler]) -> None:
        super().__init__(address, handler)
        self._slots = threading.BoundedSemaphore(CONNECTIONS)

    def process_request(self, request: Any, client_address: Any) -> None:
        if not self._slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self._slots.release()
            raise

    def process_request_thread(self, request: Any, client_address: Any) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._slots.release()

    def handle_error(self, request: Any, client_address: Any) -> None:
        pass
