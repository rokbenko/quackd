"""Stand-ins for Hugging Face on 127.0.0.1, for the tests: the proxy in front of an Inference
Endpoint.

Standard library only, so it runs wherever the suite does. `FakeProxy` is what a client of an
endpoint talks to: it wants the account's token as `Authorization: Bearer`, answers 503 while
the endpoint has no replica serving, and otherwise forwards each request to the server behind
it, passing the client's `Authorization` header on or stripping it, as told. It records every
request it is sent.

It proves what quackd sends and what it makes of an answer shaped like the ones Hugging Face
documents. It proves nothing about Hugging Face: what the real proxy answers while a replica
starts, and whether it passes `Authorization` on, are written down nowhere quackd could read
(`endpoint_upstream_api`), and nothing that passes against this has met a real endpoint.
"""

from __future__ import annotations

import http.client
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlsplit

HOP_BY_HOP = frozenset({"connection", "keep-alive", "transfer-encoding", "host", "content-length"})
"""Headers a proxy answers for itself rather than passing on."""


@dataclass
class Recorded:
    """One request a stand-in was sent: its method, its path with any query, its headers by
    lower-case name, and its body."""

    method: str
    path: str
    headers: dict[str, str]
    body: bytes


Reply = tuple[int, dict[str, str], bytes]
"""A status, the headers to send, and the body."""


def _serve(server: ThreadingHTTPServer) -> None:
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, name="fake-hugging-face", daemon=True).start()


@dataclass
class FakeProxy:
    """Hugging Face's proxy in front of one endpoint, as a context manager on a free port.

    `token` is the account token it wants as the bearer, and a request without it is answered
    401 in the shape Hugging Face's own errors take, a JSON `error`. `backend` is the server
    behind it, `http://127.0.0.1:PORT`, or None while the endpoint has no replica serving, when
    every request is answered `down` (503, as the autoscaling page says, unless a test asks
    for 502). `forward_authorization` says whether the client's `Authorization` header reaches
    the server. `woken` is called on every request that arrives while there is no backend, which
    is how a stand-in for the API wakes an endpoint that scaled to zero. `answer`, when it
    returns a reply, is sent instead, for the refusals a test wants by hand."""

    token: str
    backend: str | None = None
    forward_authorization: bool = True
    down: int = 503
    woken: Callable[[], None] | None = None
    answer: Callable[[Recorded], Reply | None] | None = None
    requests: list[Recorded] = field(default_factory=list)

    def __post_init__(self) -> None:
        self._lock = threading.Lock()
        self._server: ThreadingHTTPServer | None = None

    @property
    def url(self) -> str:
        assert self._server is not None, "start the stand-in first"
        return f"http://127.0.0.1:{self._server.server_address[1]}"

    def _reply(self, seen: Recorded) -> Reply:
        if seen.headers.get("authorization") != f"Bearer {self.token}":
            return 401, {"Content-Type": "application/json"}, b'{"error":"Invalid credentials"}'
        if self.answer is not None and (said := self.answer(seen)) is not None:
            return said
        backend = self.backend
        if backend is None:
            if self.woken is not None:
                self.woken()
            return self.down, {"Content-Type": "text/plain"}, b"Service Unavailable"
        return self._forward(backend, seen)

    def _forward(self, backend: str, seen: Recorded) -> Reply:
        parts = urlsplit(backend)
        conn = http.client.HTTPConnection(parts.hostname or "127.0.0.1", parts.port, timeout=30)
        headers = {
            name: value
            for name, value in seen.headers.items()
            if name not in HOP_BY_HOP and (self.forward_authorization or name != "authorization")
        }
        try:
            conn.request(seen.method, seen.path, body=seen.body or None, headers=headers)
            response = conn.getresponse()
            body = response.read()
            kept = {
                name: value
                for name, value in response.getheaders()
                if name.lower() not in HOP_BY_HOP
            }
            return response.status, kept, body
        except OSError:
            return 502, {"Content-Type": "text/plain"}, b"Bad Gateway"
        finally:
            conn.close()

    def __enter__(self) -> FakeProxy:
        proxy = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def _serve(self) -> None:
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length) if length else b""
                seen = Recorded(
                    self.command, self.path, {k.lower(): v for k, v in self.headers.items()}, body
                )
                with proxy._lock:
                    proxy.requests.append(seen)
                status, headers, payload = proxy._reply(seen)
                self.send_response(status)
                for name, value in headers.items():
                    self.send_header(name, value)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            do_GET = do_POST = do_PUT = do_DELETE = _serve

            def log_message(self, format: str, *args: Any) -> None:
                return None

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        _serve(self._server)
        return self

    def __exit__(self, *exc: object) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
