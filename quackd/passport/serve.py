"""Serving passports over HTTP (spec 5.1): the well-known URL, an index, and nothing else.

One robot is served at `/.well-known/robot-passport.json`, and at `.jws` once it is signed.
Several are served each under `/robots/<name>/`, with an index at the well-known URL listing
them on this same host (spec 2.7), so a reader is never sent anywhere else. The standard
library's threading server is enough: a passport is a few kilobytes, read rarely.
"""

from __future__ import annotations

import json
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

#: What a talk inbox is: the request's body and content type in, a status and a body out.
Inbox = Callable[[bytes, str], tuple[int, bytes | None, str]]


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
        self._httpd = ThreadingHTTPServer((host, port), self._handler())
        self._httpd.daemon_threads = True
        self._thread: threading.Thread | None = None

    @property
    def port(self) -> int:
        return int(self._httpd.server_address[1])

    def update(self, robots: Sequence[Served]) -> None:
        """Serve these passports from now on: one assignment, so a request sees old or new."""
        self._routes = routes(robots)

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
                try:
                    length = int(self.headers.get("Content-Length") or 0)
                except ValueError:
                    self._send(400, b"a Content-Length that is not a number\n")
                    return
                if length > server._inbox_limit:
                    self._send(413, b"too large\n")
                    return
                body = self.rfile.read(length)
                kind = (self.headers.get("Content-Type") or "").split(";")[0].strip()
                status, reply, reply_kind = server._inbox(body, kind)
                self._send(status, reply or b"", reply_kind)

            def log_message(self, format: str, *args: Any) -> None:
                pass

        return Handler
