"""The attacks on the daemons that run on a robot, replayed against the daemons themselves, with
their own fake bodies. A web page the operator happens to visit can reach them through the ssh
tunnel the docs recommend, and any peer that can reach the port can send anything.

Each attack here worked before this file existed. Nothing a real client sends is refused, and the
tests about real clients are in each daemon's own test file, unchanged."""

from __future__ import annotations

import http.client
import importlib.util
import json
import socket
import sys
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

REPO = Path(__file__).resolve().parents[1]
BROWSER_HEADERS = {"Origin": "https://evil.example", "Content-Type": "text/plain"}


def _load(relative: str, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, REPO / relative)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


DUCK = _load("bridge/open_duck/quackd_duck_bridge.py", "hostile_duck_bridge")
TODDLER = _load("bridge/toddlerbot/quackd_toddlerbot_bridge.py", "hostile_toddlerbot_bridge")
MOSS = _load("bridge/moss/quackd_moss_bridge.py", "hostile_moss_bridge")
HOSTD = _load("bridge/jetson/quackd_jetson_hostd.py", "hostile_jetson_hostd")
CAMD = _load("bridge/open_duck/quackd_duck_camd.py", "hostile_duck_camd")


def _browser_post(port: int, body: bytes) -> bytes:
    """What `fetch("http://127.0.0.1:port/", {method: "POST", mode: "no-cors", body})` puts on the
    wire. The page never sees the answer, and did not need to."""
    head = (
        f"POST / HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nOrigin: https://evil.example\r\n"
        f"Content-Type: text/plain\r\nContent-Length: {len(body)}\r\n\r\n"
    )
    return head.encode() + body


#: What a page sends as the method when it is same-origin, on a name it rebinds to 127.0.0.1: no
#: preflight, so Chrome writes any token as given. A check for capitals let every one of these by.
ANY_METHOD = ["POST", "patch", "foo", "M-SEARCH", "A1", "x_y", "GET", "OPTIONS"]


def _same_origin_request(port: int, method: str, body: bytes) -> bytes:
    head = (
        f"{method} / HTTP/1.1\r\nHost: evil.example:{port}\r\nContent-Type: text/plain\r\n"
        f"Content-Length: {len(body)}\r\n\r\n"
    )
    return head.encode() + body


def _browser_post_with_a_long_url(port: int, body: bytes, url_bytes: int) -> bytes:
    """The same fetch at a URL past a line daemon's limit. Chrome sends it. The request line was
    then dropped as too long without counting as the first line, so `Host:` was first, and the body
    ran."""
    head = (
        f"POST /{'a' * url_bytes} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n"
        f"Origin: https://evil.example\r\nContent-Type: text/plain\r\n"
        f"Content-Length: {len(body)}\r\n\r\n"
    )
    return head.encode() + body


def _closes_without_a_reply(sock: socket.socket) -> bool:
    sock.settimeout(5)
    try:
        return sock.recv(400) == b""
    except ConnectionError:
        return True


# ── the Open Duck bridge ─────────────────────────────────────────────────────────────────


@pytest.fixture
def duck() -> Iterator[Any]:
    core = DUCK.BridgeCore(token="s3cret")
    server = DUCK.Server(core, "127.0.0.1", 0)
    server.start()
    try:
        yield server
    finally:
        server.stop()
        server.join(timeout=5)


def _hello(port: int, token: str = "s3cret") -> dict[str, Any]:
    with socket.create_connection(("127.0.0.1", port), timeout=5) as sock:
        params = {
            "protocol": DUCK.PROTOCOL,
            "protocol_version": DUCK.PROTOCOL_VERSION,
            "token": token,
        }
        sock.sendall(
            json.dumps({"id": 1, "method": "duck.hello", "params": params}).encode() + b"\n"
        )
        return json.loads(sock.makefile("rb").readline())


@pytest.mark.parametrize(
    "line",
    [
        b"7\n",
        b'"hi"\n',
        b"[]\n",
        b"null\n",
        b"\xff\n",
        b'{"id": 1, "method": "duck.hello", "params": 3}\n',
        b'{"id": 1, "method": "duck.hello", "params": {"protocol_version": "x"}}\n',
    ],
    ids=[
        "a number",
        "a string",
        "a list",
        "null",
        "a stray byte",
        "params no dict",
        "version no int",
    ],
)
def test_no_line_a_stranger_sends_ends_the_open_ducks_only_thread(duck: Any, line: bytes) -> None:
    """Token or no token, each of these ended the one thread the server has, and with it every
    client's way in, while the deadman left the duck stopped, torqued and unreachable."""
    with socket.create_connection(("127.0.0.1", duck.port), timeout=5) as sock:
        sock.sendall(line)
        time.sleep(0.2)
    assert duck.is_alive(), f"{line!r} ended the server"
    assert "result" in _hello(duck.port), "and the next client is served"


@pytest.mark.parametrize("token", ["é", "\ud800"], ids=["not ascii", "a lone surrogate"])
def test_a_token_that_is_not_ascii_is_refused_and_ends_nothing(duck: Any, token: str) -> None:
    """`hmac.compare_digest` raises on a str that is not ASCII, and a lone surrogate cannot even be
    encoded the ordinary way. It reaches the comparison as a wrong token."""
    with socket.create_connection(("127.0.0.1", duck.port), timeout=5) as sock:
        params = {
            "protocol": DUCK.PROTOCOL,
            "protocol_version": DUCK.PROTOCOL_VERSION,
            "token": token,
        }
        sock.sendall(
            json.dumps({"id": 1, "method": "duck.hello", "params": params}).encode() + b"\n"
        )
        answer = json.loads(sock.makefile("rb").readline())
    assert answer["error"]["code"] == 2
    assert duck.is_alive()


def test_saying_the_word_and_leaving_stops_nobodys_duck(duck: Any) -> None:
    """A client that was driving stops the duck when it goes, which is right. One that never
    authenticated only had to say `duck.command` and hang up, and could do it as often as it liked
    to a duck somebody else was walking."""
    zeroed: list[int] = []
    real = duck.core._zero
    duck.core._zero = lambda: (zeroed.append(1), real())[1]
    with socket.create_connection(("127.0.0.1", duck.port), timeout=5) as sock:
        sock.sendall(b'{"method": "duck.command", "params": {"vx": 0.1}}\n')
        time.sleep(0.2)
    time.sleep(0.2)
    assert zeroed == [], "a stranger's goodbye stopped the duck"


def test_a_client_that_was_driving_still_stops_the_duck_when_it_goes(duck: Any) -> None:
    zeroed: list[int] = []
    real = duck.core._zero
    duck.core._zero = lambda: (zeroed.append(1), real())[1]
    with socket.create_connection(("127.0.0.1", duck.port), timeout=5) as sock:
        params = {
            "protocol": DUCK.PROTOCOL,
            "protocol_version": DUCK.PROTOCOL_VERSION,
            "token": "s3cret",
        }
        sock.sendall(
            json.dumps({"id": 1, "method": "duck.hello", "params": params}).encode() + b"\n"
        )
        assert "result" in json.loads(sock.makefile("rb").readline())
        sock.sendall(b'{"method": "duck.command", "params": {"vx": 0.1}}\n')
        time.sleep(0.2)
    time.sleep(0.3)
    assert zeroed, "a real pilot leaving has to stop what it was driving"


def test_a_browser_cannot_drive_a_tokenless_open_duck() -> None:
    core = DUCK.BridgeCore(token=None)
    server = DUCK.Server(core, "127.0.0.1", 0)
    server.start()
    try:
        hello = {"id": 7, "method": "duck.hello", "params": {"protocol": DUCK.PROTOCOL}}
        with socket.create_connection(("127.0.0.1", server.port), timeout=5) as sock:
            sock.sendall(_browser_post(server.port, json.dumps(hello).encode() + b"\n"))
            assert _closes_without_a_reply(sock)
        assert server.is_alive()
    finally:
        server.stop()
        server.join(timeout=5)


@pytest.mark.parametrize("method", ANY_METHOD)
def test_no_method_word_gets_a_page_past_a_tokenless_open_duck(method: str) -> None:
    core = DUCK.BridgeCore(token=None)
    server = DUCK.Server(core, "127.0.0.1", 0)
    server.start()
    try:
        hello = {"id": 7, "method": "duck.hello", "params": {"protocol": DUCK.PROTOCOL}}
        with socket.create_connection(("127.0.0.1", server.port), timeout=5) as sock:
            sock.sendall(
                _same_origin_request(server.port, method, json.dumps(hello).encode() + b"\n")
            )
            assert _closes_without_a_reply(sock), method
        assert server.is_alive()
    finally:
        server.stop()
        server.join(timeout=5)


def test_a_very_long_url_cannot_drive_a_tokenless_open_duck() -> None:
    core = DUCK.BridgeCore(token=None)
    server = DUCK.Server(core, "127.0.0.1", 0)
    server.start()
    try:
        hello = {"id": 7, "method": "duck.hello", "params": {"protocol": DUCK.PROTOCOL}}
        body = json.dumps(hello).encode() + b"\n"
        with socket.create_connection(("127.0.0.1", server.port), timeout=5) as sock:
            sock.sendall(_browser_post_with_a_long_url(server.port, body, 90_000))
            assert _closes_without_a_reply(sock)
        assert server.is_alive()
    finally:
        server.stop()
        server.join(timeout=5)


@pytest.mark.parametrize(
    "line",
    [b"[" * 5000 + b"\n", b"[" * 990 + b"]" * 990 + b"\n", b'{"a":' * 5000 + b"\n"],
    ids=["brackets", "balanced brackets", "objects"],
)
def test_nesting_a_parser_cannot_follow_ends_nothing(duck: Any, line: bytes) -> None:
    """A RecursionError is no ValueError, so past some depth this was one more line that ended the
    only thread the server has. Where the depth is depends on the Python."""
    with socket.create_connection(("127.0.0.1", duck.port), timeout=5) as sock:
        sock.sendall(line)
        time.sleep(0.2)
    assert duck.is_alive(), "the line ended the server"
    assert "result" in _hello(duck.port), "and the next client is served"


def test_whatever_one_request_does_costs_that_client_alone(
    duck: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Past every specific case: nothing one client's bytes raise may reach the server's loop."""

    def raises(*_: Any, **__: Any) -> None:
        raise RuntimeError("something nobody thought of")

    with socket.create_connection(("127.0.0.1", duck.port), timeout=5) as sock:
        monkeypatch.setattr(duck, "_send", raises)
        sock.sendall(b'{"id": 1, "method": "duck.hello", "params": {}}\n')
        time.sleep(0.2)
    monkeypatch.undo()
    assert duck.is_alive()
    assert "result" in _hello(duck.port)


# ── the ToddlerBot daemon ────────────────────────────────────────────────────────────────


class _Toddler:
    def __init__(self, token: str | None = None) -> None:
        robot = TODDLER.FakeRobot()
        self.daemon = TODDLER.Daemon(robot, TODDLER.FakeSim(robot), fake=True)
        TODDLER.Handler.daemon_ref = self.daemon
        TODDLER.Handler.token = token
        TODDLER.Handler.capabilities = {}
        TODDLER.Handler.robot_name = "toddlerbot_2xc"
        TODDLER.Handler.motors = robot.nu
        self.server = TODDLER.Server(("127.0.0.1", 0), TODDLER.Handler)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def toddler() -> Iterator[_Toddler]:
    serving = _Toddler()
    try:
        yield serving
    finally:
        serving.close()


def _ask_toddler(port: int, method: str, *, sock: socket.socket | None = None) -> dict[str, Any]:
    line = json.dumps({"id": 1, "method": method}).encode() + b"\n"
    if sock is not None:
        sock.sendall(line)
        return json.loads(sock.makefile("rb").readline())
    with socket.create_connection(("127.0.0.1", port), timeout=5) as own:
        own.sendall(line)
        return json.loads(own.makefile("rb").readline())


@pytest.mark.parametrize("method", ["bot.state", "bot.stand", "bot.stop"])
def test_a_browser_cannot_ask_a_tokenless_toddlerbot_anything(
    toddler: _Toddler, method: str
) -> None:
    """It used to read the state, and it got `bot.stand` accepted: the headers are no JSON and were
    skipped, and the body's line was then a command."""
    body = json.dumps({"id": 7, "method": method}).encode() + b"\n"
    with socket.create_connection(("127.0.0.1", toddler.port), timeout=5) as sock:
        sock.sendall(_browser_post(toddler.port, body))
        assert _closes_without_a_reply(sock)
    assert "result" in _ask_toddler(toddler.port, "bot.state"), "and a real client still is served"


@pytest.mark.parametrize("method", ANY_METHOD)
def test_no_method_word_gets_a_page_past_a_tokenless_toddlerbot(
    toddler: _Toddler, method: str
) -> None:
    body = json.dumps({"id": 7, "method": "bot.stand"}).encode() + b"\n"
    with socket.create_connection(("127.0.0.1", toddler.port), timeout=5) as sock:
        sock.sendall(_same_origin_request(toddler.port, method, body))
        assert _closes_without_a_reply(sock), method


@pytest.mark.parametrize(
    "line",
    [b'{"id": 1, "method": "bot.state"}\n', b'{ "id": 1, "method": "bot.state" }\n'],
    ids=["compact", "spaced"],
)
def test_a_real_clients_first_line_is_no_http_request(line: bytes) -> None:
    assert not TODDLER.HTTP_REQUEST_LINE.match(line)
    assert not DUCK.HTTP_REQUEST_LINE.match(line)
    # a JSON string holding the words is no request either, and no client opens with one anyway
    assert not TODDLER.HTTP_REQUEST_LINE.match(b'{"note": "POST / HTTP/1.1"}\n')


def test_a_very_long_url_cannot_drive_a_tokenless_toddlerbot(toddler: _Toddler) -> None:
    body = json.dumps({"id": 7, "method": "bot.stand"}).encode() + b"\n"
    with socket.create_connection(("127.0.0.1", toddler.port), timeout=5) as sock:
        sock.sendall(_browser_post_with_a_long_url(toddler.port, body, 70_000))
        assert _closes_without_a_reply(sock)
    assert "result" in _ask_toddler(toddler.port, "bot.state"), "and a real client still is served"


def test_a_line_of_junk_mid_session_still_costs_nothing(toddler: _Toddler) -> None:
    """The refusal is for a connection that opens as HTTP, not for a bad line: a session that has
    started keeps its tolerance, and a request line later on is only one more bad line."""
    with socket.create_connection(("127.0.0.1", toddler.port), timeout=5) as sock:
        assert "result" in _ask_toddler(toddler.port, "bot.state", sock=sock)
        sock.sendall(b"not json\nPOST / HTTP/1.1\n7\n")
        reader = sock.makefile("rb")
        sock.sendall(json.dumps({"id": 2, "method": "bot.state"}).encode() + b"\n")
        assert "result" in json.loads(reader.readline())


def test_a_line_with_no_end_is_cut_off_and_the_session_goes_on(
    toddler: _Toddler, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A peer that never sent a newline could fill the memory of the process walking the robot."""
    monkeypatch.setattr(TODDLER, "MAX_LINE", 1024)
    with socket.create_connection(("127.0.0.1", toddler.port), timeout=5) as sock:
        assert "result" in _ask_toddler(toddler.port, "bot.state", sock=sock)
        sock.sendall(b"x" * 8192 + b"\n")
        assert "result" in _ask_toddler(toddler.port, "bot.state", sock=sock)


def test_a_first_line_with_no_end_closes_the_connection(
    toddler: _Toddler, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No client opens with one, and reading it as one more bad line is how a browser's long URL
    got its request line out of first place."""
    monkeypatch.setattr(TODDLER, "MAX_LINE", 1024)
    with socket.create_connection(("127.0.0.1", toddler.port), timeout=5) as sock:
        sock.sendall(b"x" * 8192 + b"\n")
        assert _closes_without_a_reply(sock)


def test_silent_connections_cost_a_real_client_nothing(toddler: _Toddler) -> None:
    """A cap on connections and a deadline on the first line were tried here, and an adversarial
    review locked a real pilot out with sixteen blank lines, which the daemon without them does
    not allow. So a stranger who holds connections open costs only those, and the line cap is what
    bounds the memory one of them can use."""
    held = [socket.create_connection(("127.0.0.1", toddler.port), timeout=5) for _ in range(32)]
    try:
        for sock in held:
            sock.sendall(b"\n")
        assert "result" in _ask_toddler(toddler.port, "bot.state")
    finally:
        for sock in held:
            sock.close()


def test_a_session_may_wait_as_long_as_it_likes(toddler: _Toddler) -> None:
    """One held at a confirm prompt says nothing for as long as a person takes to answer."""
    with socket.create_connection(("127.0.0.1", toddler.port), timeout=5) as sock:
        assert "result" in _ask_toddler(toddler.port, "bot.state", sock=sock)
        time.sleep(0.4)
        assert "result" in _ask_toddler(toddler.port, "bot.state", sock=sock)


def test_a_toddlerbot_token_that_is_not_ascii_is_a_wrong_one() -> None:
    serving = _Toddler(token="s3cret")
    try:
        with socket.create_connection(("127.0.0.1", serving.port), timeout=5) as sock:
            hello = {"id": 1, "method": "bot.hello", "params": {"token": "é"}}
            sock.sendall(json.dumps(hello).encode() + b"\n")
            assert "error" in json.loads(sock.makefile("rb").readline())
    finally:
        serving.close()


# ── the three HTTP daemons ───────────────────────────────────────────────────────────────


def _request(
    port: int, method: str, path: str, headers: dict[str, str], body: bytes | None = None
) -> tuple[int, bytes]:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    try:
        conn.request(method, path, body=body, headers=headers)
        response = conn.getresponse()
        return response.status, response.read()
    finally:
        conn.close()


@pytest.fixture
def moss() -> Iterator[tuple[Any, int]]:
    bridge = MOSS.fake_bridge(token=None, fps=10.0, cycle_s=0.3)
    server = MOSS.serve(bridge, "127.0.0.1", 0)
    bridge.start()
    try:
        yield bridge, int(server.server_address[1])
    finally:
        server.shutdown()
        server.server_close()
        bridge.close()


@pytest.mark.parametrize("path", [MOSS.TWIST_PATH, MOSS.STOP_PATH, MOSS.ARM_CYCLE_PATH])
def test_a_web_page_cannot_post_to_a_tokenless_moss_bridge(moss: Any, path: str) -> None:
    """The rover's tracks and its arm's cycle, from a page in the operator's browser. The fake bus
    records every publish, so the refusal is shown to have reached nothing."""
    bridge, port = moss
    before = list(bridge.bus.published)
    status, raw = _request(port, "POST", path, BROWSER_HEADERS, b'{"vx": 0.2, "wz": 0.0}')
    assert status == 403, raw
    assert json.loads(raw)["reason"] == "not from a web page"
    time.sleep(0.2)
    moved = [sent for sent in bridge.bus.published if sent not in before and sent[2] != (0.0, 0.0)]
    assert not moved, f"the refused request reached the bus: {moved[:3]}"
    status, _ = _request(port, "GET", MOSS.HELLO_PATH, {})
    assert status == 200, "and a real client still is served"


def test_the_moss_bridge_answers_a_real_client_as_it_did(moss: Any) -> None:
    _, port = moss
    status, raw = _request(port, "GET", MOSS.HELLO_PATH, {})
    assert status == 200 and json.loads(raw)["ok"] is True


@pytest.fixture
def jetson() -> Iterator[int]:
    daemon = HOSTD.Hostd()
    server = HOSTD.serve(daemon, "127.0.0.1", 0)
    try:
        yield int(server.server_address[1])
    finally:
        server.shutdown()
        server.server_close()
        daemon.stop()


@pytest.mark.parametrize(
    ("method", "path"), [("GET", HOSTD.HELLO_PATH), ("GET", HOSTD.HEALTH_PATH), ("POST", "/detect")]
)
def test_a_web_page_gets_nothing_from_a_jetson_daemon(jetson: int, method: str, path: str) -> None:
    status, raw = _request(jetson, method, path, BROWSER_HEADERS, b"" if method == "POST" else None)
    assert status == 403, raw
    assert json.loads(raw)["reason"] == "not from a web page"


def test_the_jetson_daemon_answers_a_real_client_as_it_did(jetson: int) -> None:
    status, raw = _request(jetson, "GET", HOSTD.HELLO_PATH, {})
    assert status == 200 and json.loads(raw)["ok"] is True


@pytest.fixture
def camera() -> Iterator[int]:
    store = CAMD.FrameStore()
    stop = threading.Event()
    threading.Thread(
        target=CAMD.capture_loop, args=(store, CAMD.FakeCamera(64), 20.0, stop), daemon=True
    ).start()
    server = CAMD.serve(store, "127.0.0.1", 0)
    deadline = time.monotonic() + 5
    while store.get()[0] is None and time.monotonic() < deadline:
        time.sleep(0.01)
    try:
        yield int(server.server_address[1])
    finally:
        stop.set()
        server.shutdown()
        server.server_close()


def test_a_web_page_cannot_read_the_ducks_camera(camera: int) -> None:
    status, raw = _request(camera, "GET", CAMD.SNAPSHOT_PATH, {"Origin": "https://evil.example"})
    assert status == 403, raw[:80]
    assert json.loads(raw)["reason"] == "not from a web page"


def test_a_person_opening_a_frame_in_a_tab_still_gets_it(camera: int) -> None:
    """A browser's own navigation sends no `Origin`, and that is the difference between a person
    looking at their robot's camera and a page reading it."""
    status, raw = _request(camera, "GET", CAMD.SNAPSHOT_PATH, {"User-Agent": "Mozilla/5.0"})
    assert status == 200 and raw[:2] == b"\xff\xd8"
