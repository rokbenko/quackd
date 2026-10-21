"""Fetching a passport the way spec section 5.7 allows: no redirects, a byte budget, five seconds.

A reader that followed a passport wherever it pointed would turn every robot on a network into
a way to probe that network. This fetches exactly one URL, the one it was given, and nothing a
response names.

It speaks just enough HTTP itself, one HTTP/1.0 GET with the connection closed after it, rather
than through urllib, because the limits have to hold for the whole exchange. urllib's timeout
is per read, so a server dripping a byte at a time could hold a reader for minutes, and its
parser reads headers and interim responses before it counts a byte of the body. Here one clock
and one budget cover everything from the connect to the last byte."""

from __future__ import annotations

import re
import socket
import ssl
import time
from dataclasses import dataclass
from urllib.parse import parse_qs, urlsplit, urlunsplit

from quackd.passport import jws
from quackd.passport.errors import PassportError

WELL_KNOWN_JSON = "/.well-known/robot-passport.json"
WELL_KNOWN_JWS = "/.well-known/robot-passport.jws"
FETCH_TIMEOUT_S = 5.0
#: The most a reader reads of a passport: the JWS of the largest passport there may be, 8,192
#: bytes made a third longer by base64url, with a header and a signature (spec 5.7).
MAX_WIRE_BYTES = 12_288
#: The most a reader reads of a response's status line and headers, all of them together.
HEADER_BYTES = 8_192
_STATUS = re.compile(rb"^HTTP/1\.[01] ([1-5][0-9][0-9])(?: ([^\r\n]*))?$")
_REDIRECTS = (301, 302, 303, 307, 308)


class HttpStatus(PassportError):
    """A host that answered, with a status other than 200."""

    def __init__(self, url: str, status: int, reason: str) -> None:
        super().__init__("5.7", f"{url} answered {status} {reason}".rstrip())
        self.status = status


@dataclass(frozen=True)
class Target:
    """A URL to fetch, and the short id its `#rp=` fragment promised, if it had one."""

    url: str
    short_id: str | None


def target(text: str) -> Target:
    """A URL as a person typed it or a camera read it (spec 5.2).

    A bare host, `http://10.0.0.5:8790`, means its well-known URL. The fragment never travels
    to the server; `#rp=` in it is the short id the passport must turn out to have."""
    parts = urlsplit(text.strip())
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise PassportError("5.7", f"{text!r} is not an http or https URL")
    promised = parse_qs(parts.fragment).get("rp", [None])[0]
    path = parts.path if parts.path not in ("", "/") else WELL_KNOWN_JSON
    return Target(urlunsplit((parts.scheme, parts.netloc, path, parts.query, "")), promised)


def fetch(url: str, *, limit: int = MAX_WIRE_BYTES, timeout: float = FETCH_TIMEOUT_S) -> bytes:
    """The body at `url`, at most `limit` bytes and all of it within `timeout` seconds, or a
    `PassportError` saying why not."""
    parts = urlsplit(url)
    try:
        port = parts.port
    except ValueError as e:
        raise PassportError("5.7", f"{url!r} is not a URL a reader can fetch: {e}") from e
    host = parts.hostname
    if parts.scheme not in ("http", "https") or not host:
        raise PassportError("5.7", f"{url!r} is not an http or https URL")
    if parts.username is not None or parts.password is not None:
        raise PassportError("5.7", f"{url!r} carries a user name, which a reader never sends")
    path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
    if not path.isascii() or any(c in path for c in " \r\n"):
        raise PassportError("5.7", f"{url!r} has a path a request cannot carry as it is")
    authority = f"[{host}]" if ":" in host else host
    if port is not None:
        authority += f":{port}"
    request = (
        f"GET {path} HTTP/1.0\r\nHost: {authority}\r\n"
        "Accept: application/jose, application/json\r\nUser-Agent: quackd\r\n\r\n"
    ).encode("ascii")
    deadline = time.monotonic() + timeout
    try:
        raw = socket.create_connection(
            (host, port or (443 if parts.scheme == "https" else 80)), timeout=timeout
        )
        sock = raw
        try:
            if parts.scheme == "https":
                raw.settimeout(max(deadline - time.monotonic(), 0.001))
                # the TLS socket takes over the connection, so it is the one to close
                sock = ssl.create_default_context().wrap_socket(raw, server_hostname=host)
            sock.sendall(request)
            data = _read(sock, HEADER_BYTES + limit + 1, deadline, url, timeout)
        finally:
            sock.close()
            raw.close()
    except PassportError:
        raise
    except OSError as e:
        raise PassportError("5.7", f"{url} could not be reached: {e}") from e
    status, reason, headers, body = _parse(data, url)
    if status in _REDIRECTS:
        where = headers.get("location", "somewhere else")
        raise PassportError("5.7", f"it redirects to {where}, and a reader follows no redirects")
    if status != 200:
        raise HttpStatus(url, status, reason)
    if len(body) > limit:
        raise PassportError("rule 1", f"{url} sent more than the {limit} bytes a reader accepts")
    return body


def _read(sock: socket.socket, budget: int, deadline: float, url: str, timeout: float) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while True:
        left = deadline - time.monotonic()
        if left <= 0:
            raise PassportError("5.7", f"{url} took more than the {timeout:g} s a reader waits")
        sock.settimeout(left)
        try:
            chunk = sock.recv(min(65_536, budget - total))
        except TimeoutError as e:
            raise PassportError(
                "5.7", f"{url} took more than the {timeout:g} s a reader waits"
            ) from e
        if not chunk:
            return b"".join(chunks)
        chunks.append(chunk)
        total += len(chunk)
        if total >= budget:
            raise PassportError("rule 1", f"{url} sent more than a reader reads of a passport")


def _parse(data: bytes, url: str) -> tuple[int, str, dict[str, str], bytes]:
    """A response's status, reason, headers and body, or a refusal for one that is not HTTP."""
    head, blank, body = data.partition(b"\r\n\r\n")
    if not blank:
        if len(data) >= HEADER_BYTES:
            raise PassportError("5.7", f"{url} sent more headers than a reader reads")
        raise PassportError("5.7", f"{url} did not answer in HTTP, or stopped partway")
    if len(head) > HEADER_BYTES:
        raise PassportError("5.7", f"{url} sent more headers than a reader reads")
    status_line, *lines = head.split(b"\r\n")
    match = _STATUS.match(status_line)
    if match is None:
        raise PassportError("5.7", f"{url} did not answer in HTTP")
    headers: dict[str, str] = {}
    for line in lines:
        name, colon, value = line.partition(b":")
        if not colon:
            raise PassportError("5.7", f"{url} sent a header that is not a header")
        key = name.strip().decode("latin-1").lower()
        text = value.strip().decode("latin-1")
        if key in headers and headers[key] != text and key in ("content-length", "location"):
            raise PassportError("5.7", f"{url} sent two different {key} headers")
        headers[key] = text
    if headers.get("transfer-encoding", "").lower() == "chunked":
        body = _dechunk(body, url)
    elif "content-length" in headers:
        length = headers["content-length"]
        if not length.isdigit():
            raise PassportError("5.7", f"{url} sent a Content-Length that is not a number")
        if len(body) < int(length):
            raise PassportError("5.7", f"{url} closed before it sent all it said it would")
        body = body[: int(length)]
    return int(match.group(1)), (match.group(2) or b"").decode("latin-1"), headers, body


def _dechunk(body: bytes, url: str) -> bytes:
    """A body in HTTP/1.1 chunks, which a server should not send an HTTP/1.0 request but some do."""
    out = bytearray()
    rest = body
    while True:
        line, newline, rest = rest.partition(b"\r\n")
        size_text = line.split(b";", 1)[0].strip()
        if not newline or not re.fullmatch(rb"[0-9A-Fa-f]{1,8}", size_text):
            raise PassportError("5.7", f"{url} sent chunks a reader cannot follow")
        size = int(size_text, 16)
        if size == 0:
            return bytes(out)
        if len(rest) < size + 2:
            raise PassportError("5.7", f"{url} closed before it sent all it said it would")
        out += rest[:size]
        rest = rest[size + 2 :]


def fetch_passport(url: str, *, timeout: float = FETCH_TIMEOUT_S) -> bytes:
    """A passport at `url`, signed when it can be: for a `robot-passport.json`, the `.jws` beside
    it on the same host is asked for first, since a reader that verifies uses the JWS (spec 5.1),
    and the JSON is fetched only when the host has no JWS. Nothing else is ever fetched.

    What a `.jws` URL serves has to be a JWS. Otherwise a host could answer the signed URL with
    the unsigned document, and a reader that took it would have been talked down from a signed
    passport to a claim without being told."""
    parts = urlsplit(url)
    if parts.path.endswith("robot-passport.json"):
        signed = urlunsplit(
            (parts.scheme, parts.netloc, parts.path[: -len("json")] + "jws", parts.query, "")
        )
        try:
            return _signed(signed, fetch(signed, timeout=timeout))
        except HttpStatus as e:
            if e.status != 404:
                raise
    body = fetch(url, timeout=timeout)
    return _signed(url, body) if parts.path.endswith(".jws") else body


def _signed(url: str, body: bytes) -> bytes:
    if not jws.looks_like_jws(body.decode("utf-8", errors="replace")):
        raise PassportError("4.4", f"{url} is where a signed passport belongs, and it served none")
    return body
