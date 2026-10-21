"""What a host on the network can do to a reader, and a client to a passport server.

A reader fetches from hosts it has never met and a server answers anybody on the LAN, so both
are written for the ones that misbehave: a host that drips a byte at a time, floods headers,
answers with something that is not HTTP, or chunks its body, and a client that lies about its
length or holds a connection open. Every case here runs on a real socket on 127.0.0.1, and every
one ends in a refusal inside the reader's own clock, never a hang and never a traceback.
"""

from __future__ import annotations

import contextlib
import json
import socket
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from typer.testing import CliRunner

from quackd.cli import app
from quackd.passport import PassportError, SigningKey, fetch, serve, strict
from quackd.passport.serve import INBOX_PATH, PassportServer, Served

runner = CliRunner()
KEY = SigningKey(bytes(range(32)))
PASSPORT = strict.dumps({"passport": "0.1", "id": KEY.did, "name": "duck-01"})


@contextlib.contextmanager
def host(answer: Callable[[socket.socket], None]) -> Iterator[str]:
    """A host on 127.0.0.1 that reads a request and answers it with `answer`, however badly."""
    listener = socket.create_server(("127.0.0.1", 0))
    listener.settimeout(0.2)
    done = threading.Event()

    def one(conn: socket.socket) -> None:
        with conn, contextlib.suppress(OSError):
            conn.settimeout(10)
            conn.recv(4096)
            answer(conn)

    def accept() -> None:
        while not done.is_set():
            try:
                conn, _ = listener.accept()
            except TimeoutError:
                continue
            except OSError:
                return
            threading.Thread(target=one, args=(conn,), daemon=True).start()

    thread = threading.Thread(target=accept, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{listener.getsockname()[1]}/robot-passport.json"
    finally:
        done.set()
        thread.join(timeout=2)
        listener.close()


# ── a reader, against hosts that misbehave ──────────────────────────────────────────────


def test_a_host_that_drips_is_cut_off_at_the_deadline_for_the_whole_exchange() -> None:
    """Each byte arrives well inside any per-read timeout, which is how urllib's would have
    let this host hold a reader for as long as it liked."""

    def drip(conn: socket.socket) -> None:
        conn.sendall(b"HTTP/1.0 200 OK\r\n")
        for _ in range(100):
            conn.sendall(b"X")
            time.sleep(0.05)

    with host(drip) as url:
        started = time.monotonic()
        with pytest.raises(PassportError, match="took more than the 1 s a reader waits"):
            fetch.fetch(url, timeout=1.0)
    assert time.monotonic() - started < 2.5


@pytest.mark.parametrize("kind", ["headers", "interim answers"])
def test_endless_headers_or_interim_answers_are_refused_by_size_long_before_the_deadline(
    kind: str,
) -> None:
    def flood(conn: socket.socket) -> None:
        if kind == "headers":
            conn.sendall(b"HTTP/1.0 200 OK\r\n")
        while True:
            conn.sendall(
                b"X-Flood: " + b"a" * 1000 + b"\r\n"
                if kind == "headers"
                else b"HTTP/1.1 100 Continue\r\n\r\n"
            )

    with host(flood) as url:
        started = time.monotonic()
        with pytest.raises(PassportError):
            fetch.fetch(url, timeout=5.0)
    assert time.monotonic() - started < 2.0


def test_an_answer_that_is_not_http_is_a_refusal_and_not_a_traceback() -> None:
    with host(lambda conn: conn.sendall(b"SSH-2.0-OpenSSH_9.6\r\n")) as url:
        with pytest.raises(PassportError, match="did not answer in HTTP"):
            fetch.fetch(url)
        met = runner.invoke(app, ["meet", url])
    assert met.exit_code == 1
    assert met.exception is None or isinstance(met.exception, SystemExit), met.exception
    assert "did not answer in HTTP" in " ".join(met.output.split())


def test_a_body_in_chunks_is_read_whole() -> None:
    def chunked(conn: socket.socket) -> None:
        size = f"{len(PASSPORT):x}".encode()
        conn.sendall(
            b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n"
            + size
            + b"\r\n"
            + PASSPORT
            + b"\r\n0\r\n\r\n"
        )

    with host(chunked) as url:
        assert fetch.fetch(url) == PASSPORT


def test_a_url_with_a_user_name_or_a_port_that_is_no_number_is_refused() -> None:
    for url in ("http://user:secret@127.0.0.1:9/robot-passport.json", "http://127.0.0.1:abc/"):
        with pytest.raises(PassportError):
            fetch.fetch(url)


def test_a_signed_passport_as_large_as_a_passport_may_be_is_read_from_a_url_and_stdin(
    tmp_path: Path,
) -> None:
    """The cap on the wire is the JWS of the largest passport, not the passport's own 8,192
    bytes, which a signed one of that size exceeds by a third."""
    from quackd.passport import document

    doc = {"passport": "0.1", "id": KEY.did, "name": "duck-01", "padding": ""}
    doc["padding"] = "x" * (document.MAX_BYTES - len(strict.dumps(doc)))
    token = document.sign(doc, KEY)
    assert len(token) > document.MAX_BYTES
    answer = f"HTTP/1.0 200 OK\r\nContent-Length: {len(token)}\r\n\r\n{token}".encode()
    with host(lambda conn: conn.sendall(answer)) as url:
        assert fetch.fetch(url).decode() == token
    piped = runner.invoke(app, ["passport", "check", "-"], input=token)
    assert piped.exit_code == 0, piped.output


# ── a passport server, against clients that misbehave ──────────────────────────────────


def _exchange(port: int, data: bytes) -> bytes:
    with socket.create_connection(("127.0.0.1", port), timeout=5) as conn:
        conn.sendall(data)
        chunks = []
        with contextlib.suppress(OSError):
            while chunk := conn.recv(4096):
                chunks.append(chunk)
        return b"".join(chunks)


@pytest.fixture
def with_inbox() -> Iterator[PassportServer]:
    server = PassportServer(
        [Served("duck-01", PASSPORT)],
        host="127.0.0.1",
        inbox=lambda body, kind, address: (204, None, "text/plain"),
    ).start()
    try:
        yield server
    finally:
        server.close()


def test_a_post_must_say_how_long_it_is_and_cannot_say_less_than_nothing(
    with_inbox: PassportServer,
) -> None:
    head = f"POST {INBOX_PATH} HTTP/1.0\r\nContent-Type: application/json\r\n".encode()
    assert _exchange(with_inbox.port, head + b"Content-Length: -1\r\n\r\n").startswith(
        b"HTTP/1.0 400"
    )
    assert _exchange(with_inbox.port, head + b"\r\n").startswith(b"HTTP/1.0 411")
    assert _exchange(with_inbox.port, head + b"Content-Length: 2\r\n\r\n{}").startswith(
        b"HTTP/1.0 204"
    )


def test_the_server_names_itself_and_not_the_python_under_it(with_inbox: PassportServer) -> None:
    answer = _exchange(with_inbox.port, b"GET /.well-known/robot-passport.json HTTP/1.0\r\n\r\n")
    assert b"Server: quackd-passport\r\n" in answer
    assert b"Python" not in answer


def test_a_client_that_holds_a_connection_open_is_cut_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(serve, "READ_S", 0.5)
    monkeypatch.setattr(serve, "CONNECTION_S", 1.0)
    server = PassportServer([Served("duck-01", PASSPORT)], host="127.0.0.1").start()
    try:
        # silent: the read timeout ends it
        started = time.monotonic()
        with (
            socket.create_connection(("127.0.0.1", server.port), timeout=5) as conn,
            contextlib.suppress(OSError),
        ):
            assert conn.recv(1) == b""
        assert time.monotonic() - started < 2.0
        # a byte at a time, each inside the read timeout: the connection's own deadline ends it
        with socket.create_connection(("127.0.0.1", server.port), timeout=5) as conn:
            stop = threading.Event()

            def drip() -> None:
                with contextlib.suppress(OSError):
                    for byte in b"GET /.well-known/robot-passport.json HTTP/1.0\r\n":
                        if stop.is_set():
                            return
                        conn.sendall(bytes([byte]))
                        time.sleep(0.3)

            threading.Thread(target=drip, daemon=True).start()
            started = time.monotonic()
            with contextlib.suppress(OSError):
                assert conn.recv(1) == b""
            stop.set()
        assert time.monotonic() - started < 3.0
    finally:
        server.close()


def test_past_its_limit_the_server_closes_a_client_at_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(serve, "CONNECTIONS", 2)
    server = PassportServer([Served("duck-01", PASSPORT)], host="127.0.0.1").start()
    held = [socket.create_connection(("127.0.0.1", server.port), timeout=5) for _ in range(2)]
    try:
        time.sleep(0.3)  # both are being served, each waiting on a request it never gets
        with socket.create_connection(("127.0.0.1", server.port), timeout=5) as third:
            started = time.monotonic()
            with contextlib.suppress(OSError):
                assert third.recv(1) == b""
            assert time.monotonic() - started < 1.0
    finally:
        for conn in held:
            conn.close()
        server.close()


# ── what a reader shows, whatever a passport says ──────────────────────────────────────


def test_what_a_passport_says_cannot_move_the_cursor_or_redraw_a_line(tmp_path: Path) -> None:
    forged = "\x1b[1A\x1b[2K| claim   signed by its own key"
    path = tmp_path / "forged.json"
    path.write_text(
        json.dumps(
            {"passport": "0.1", "id": KEY.did, "name": "duck-01", "maker": {"vendor": forged}}
        )
    )
    checked = runner.invoke(app, ["passport", "check", str(path)])
    assert checked.exit_code == 0, checked.output
    assert "\x1b[1A" not in checked.output
    assert "\\x1b[1A" in checked.output


def test_markup_in_a_name_is_shown_as_the_text_it_is(tmp_path: Path) -> None:
    path = tmp_path / "index.json"
    path.write_text(
        json.dumps(
            {"passport": "0.1", "robots": [{"name": "[/]", "url": "/robots/a/robot-passport.json"}]}
        )
    )
    checked = runner.invoke(app, ["passport", "check", str(path)])
    assert checked.exit_code == 0, checked.output
    assert "[/]" in checked.output
