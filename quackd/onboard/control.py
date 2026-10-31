"""How `quackd onboard goal`, `status`, `stop`, `met` and `pin` reach the running service.

The service listens on 127.0.0.1 alone, on a port the system picks, and writes the port and a
fresh token to control.json in the robot's directory, readable by its user only. A client says
hello with that token, compared in constant time, and sends one op; anybody else on the robot
who cannot read the file cannot steer it. A goal is the one op that stays: its run's lines come
back the way `quackd ui` reads a run's (`quackd.front`), its questions are asked in the terminal
that gave it, and that terminal's Ctrl-C stops it, never the service.

    -> {"op": "hello", "token": "..."}
    <- {"ev": "hello", "protocol": 1, "robot": ..., "version": ...}
    -> {"op": "goal", "text": "...", "yes": false}
    <- the run's lines, then {"ev": "end", ...}
    -> {"op": "status"} | {"op": "stop"} | {"op": "met", "limit": 20} | {"op": "reload"}
       | {"op": "shutdown"}
    <- one reply each
"""

from __future__ import annotations

import contextlib
import hmac
import json
import os
import secrets
import socket
import threading
from collections.abc import Callable, Iterator, Mapping
from pathlib import Path
from typing import Any

PROTOCOL = 1
#: How long a client may take to say hello, and to send its op after it.
HELLO_S = 5.0
#: The most clients at once: a person's terminals, never a crowd.
CLIENTS = 8
#: The longest line either side sends.
MAX_LINE = 65_536

#: What the service answers one op with: the reply's members, or None to keep the connection
#: for a goal, which answers with its run's lines and closes when the run ends.
Handler = Callable[[Mapping[str, Any], "Connection"], dict[str, Any] | None]


class ControlError(RuntimeError):
    """The service is not running, not answering, or refused what was asked."""


class Connection:
    """One client: a socket, a reader for its lines and a writer for ours."""

    def __init__(self, sock: socket.socket) -> None:
        self.sock = sock
        self.reader = sock.makefile("rb")
        self.writer = sock.makefile("w", encoding="utf-8", newline="\n")
        self._lock = threading.Lock()

    def send(self, line: Mapping[str, Any]) -> None:
        with self._lock:
            self.writer.write(json.dumps(line) + "\n")
            self.writer.flush()

    def read(self) -> dict[str, Any] | None:
        raw = self.reader.readline(MAX_LINE)
        if not raw:
            return None
        try:
            value = json.loads(raw)
        except ValueError:
            return None
        return value if isinstance(value, dict) else None

    def close(self) -> None:
        for part in (self.writer, self.reader):
            with contextlib.suppress(Exception):
                part.close()
        with contextlib.suppress(OSError):
            self.sock.close()


class ControlServer:
    """The service's end: a listener on 127.0.0.1, a thread per client, one op each."""

    def __init__(self, handle: Handler, *, hello: Mapping[str, Any]) -> None:
        self.token = secrets.token_urlsafe(24)
        self._handle = handle
        self._hello = dict(hello)
        self._sock = socket.create_server(("127.0.0.1", 0))
        self._sock.settimeout(0.5)
        self._stopping = threading.Event()
        self._clients = threading.BoundedSemaphore(CLIENTS)
        self._thread = threading.Thread(target=self._accept, name="onboard-control", daemon=True)

    @property
    def port(self) -> int:
        return int(self._sock.getsockname()[1])

    def start(self, control_json: Path) -> ControlServer:
        """Listen, and write where to, with the token, to `control_json` for this user only."""
        write_private(control_json, json.dumps({"port": self.port, "token": self.token}) + "\n")
        self._thread.start()
        return self

    def close(self) -> None:
        self._stopping.set()
        with contextlib.suppress(OSError):
            self._sock.close()
        self._thread.join(timeout=2)

    def _accept(self) -> None:
        while not self._stopping.is_set():
            try:
                sock, _address = self._sock.accept()
            except TimeoutError:
                continue
            except OSError:
                return
            if not self._clients.acquire(blocking=False):
                with contextlib.suppress(OSError):
                    sock.close()
                continue
            threading.Thread(target=self._serve, args=(sock,), daemon=True).start()

    def _serve(self, sock: socket.socket) -> None:
        conn = Connection(sock)
        kept = False
        # the hello and the op inside `HELLO_S` in all, however slowly the bytes come, so no
        # client without the token holds one of the slots for longer than that
        cut_off = threading.Timer(HELLO_S, _close_quietly, args=(sock,))
        cut_off.daemon = True
        cut_off.start()
        try:
            sock.settimeout(HELLO_S)
            hello = conn.read()
            given = str((hello or {}).get("token", "")).encode("utf-8", errors="replace")
            if (hello or {}).get("op") != "hello" or not hmac.compare_digest(
                given, self.token.encode("utf-8")
            ):
                conn.send(
                    {"ev": "refused", "reason": "a client says hello with control.json's token"}
                )
                return
            conn.send({"ev": "hello", "protocol": PROTOCOL, **self._hello})
            op = conn.read()
            if op is None or not isinstance(op.get("op"), str):
                conn.send({"ev": "refused", "reason": "no op after the hello"})
                return
            cut_off.cancel()
            sock.settimeout(None)
            reply = self._handle(op, conn)
            if reply is None:
                kept = True  # a goal: its run's lines go out on `conn` until it ends
                return
            conn.send(reply)
        except (OSError, ValueError):
            return
        finally:
            cut_off.cancel()
            if not kept:
                conn.close()
            self._clients.release()


def _close_quietly(sock: socket.socket) -> None:
    with contextlib.suppress(OSError):
        sock.close()


def write_private(path: Path, text: str) -> None:
    """`text` to `path`, readable by this user alone where the system has modes: the mode is
    set on the empty file before a byte of the token is in it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with open(fd, "w", encoding="utf-8", newline="") as f:
        f.write(text)
    os.replace(tmp, path)


# ── the client's end ────────────────────────────────────────────────────────────────────


def _where(control_json: Path) -> tuple[int, str]:
    try:
        found = json.loads(control_json.read_text(encoding="utf-8"))
        return int(found["port"]), str(found["token"])
    except (OSError, ValueError, KeyError, TypeError) as e:
        raise ControlError(
            f"no service is running for this robot ({control_json} is missing or unreadable)"
        ) from e


@contextlib.contextmanager
def connect(
    control_json: Path, *, timeout: float = HELLO_S, then: float | None = None
) -> Iterator[Connection]:
    """A hello'd connection to the service whose control.json this is: `timeout` for the
    hello, and `then` for every read after it, None for as long as it takes, which a goal's
    lines need, since a model's turn can take longer than any hello."""
    port, token = _where(control_json)
    try:
        sock = socket.create_connection(("127.0.0.1", port), timeout=timeout)
    except OSError as e:
        raise ControlError(f"the service is not answering on 127.0.0.1:{port}: {e}") from e
    conn = Connection(sock)
    try:
        conn.send({"op": "hello", "token": token})
        hello = conn.read()
        if hello is None or hello.get("ev") != "hello":
            raise ControlError(str((hello or {}).get("reason", "the service refused the hello")))
        sock.settimeout(then)
        yield conn
    finally:
        conn.close()


def request(control_json: Path, op: Mapping[str, Any], *, timeout: float = 10.0) -> dict[str, Any]:
    """One op and the service's reply to it."""
    with connect(control_json, timeout=timeout, then=timeout) as conn:
        conn.send(dict(op))
        reply = conn.read()
    if reply is None:
        raise ControlError("the service closed the connection without an answer")
    if reply.get("ev") == "refused":
        raise ControlError(str(reply.get("reason", "refused")))
    return reply
