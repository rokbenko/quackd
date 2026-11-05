"""quackd's client for the Jetson host daemon, driven against a fake of the protocol on loopback.

Every test here talks real HTTP to `tests/fake_jetson_hostd.py` on 127.0.0.1, because the
things most worth proving are on the wire: which header the token rides in, what a 503 body
says, what urllib does with a redirect. What none of it proves is anything about a Jetson.
"""

from __future__ import annotations

import dataclasses
import json
import math
import socket
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

import pytest

from quackd import host as host_module
from quackd.host import (
    BOARD_TIMEOUT_S,
    DEFAULT_PORT,
    DETECT_TIMEOUT_S,
    HELLO_TIMEOUT_S,
    MAX_JPEG_BYTES,
    PROTOCOL,
    PROTOCOL_VERSION,
    SNAPSHOT_TIMEOUT_S,
    STALE_AFTER_S,
    TOKEN_ENV,
    TOKEN_HEADER,
    HostBox,
    HostClient,
    HostDetections,
    HostError,
    base_url,
    host_of,
    parse_host,
)
from tests.fake_jetson_hostd import (
    BALL_BOX,
    COMPATIBLE,
    MEMINFO,
    NVPMODEL_Q,
    ORIN_NANO,
    RELEASE_36_4_3,
    SIM_ORANGE,
    TEGRASTATS_LINE,
    ZRAM_SWAPS,
    CannedReply,
    FakeHostd,
    dead_address,
    jpeg_bytes,
)

TOKEN = "63d92f8974c051832d52dd04c78f314b01ef7436cc00e4a3805b9711e5318421"
"""Shaped like `openssl rand -hex 32`, the token bridge/jetson/README.md tells people to make,
and that long on purpose: a shorter one fits inside the 60 characters a field name is cut to,
and would hide a scrub that runs after the cut. Distinctive enough that finding it anywhere it
should not be is a real finding and not a coincidence of short strings."""


@pytest.fixture
def hostd() -> Iterator[FakeHostd]:
    with FakeHostd() as fake:
        yield fake


def _raises(call: Callable[[], Any]) -> HostError:
    with pytest.raises(HostError) as caught:
        call()
    return caught.value


def _longest_piece_of(secret: str, text: str) -> int:
    """How many characters of `secret` in a row appear in `text`. A cut through an echoed token
    leaves a piece of it behind, which `secret in text` does not see."""
    return max(
        (
            end - start
            for start in range(len(secret))
            for end in range(start + 1, len(secret) + 1)
            if secret[start:end] in text
        ),
        default=0,
    )


# ── the contract ────────────────────────────────────────────────────────────────────────


def test_the_constants_are_the_ones_the_protocol_names() -> None:
    """The daemon spells these too, and a drift on either side is a board that cannot be
    reached, so they are pinned here to the protocol's own values."""
    assert (PROTOCOL, PROTOCOL_VERSION, DEFAULT_PORT) == ("quackd-jetson-hostd", 1, 9874)
    assert (TOKEN_ENV, TOKEN_HEADER) == ("QUACKD_HOST_TOKEN", "X-Quackd-Token")
    assert (HELLO_TIMEOUT_S, SNAPSHOT_TIMEOUT_S, DETECT_TIMEOUT_S, BOARD_TIMEOUT_S) == (
        2.0,
        1.0,
        2.0,
        8.0,
    )
    assert STALE_AFTER_S == 2.0
    assert MAX_JPEG_BYTES == 8 * 1024 * 1024


def test_the_module_imports_nothing_heavier_than_pil() -> None:
    """`doctor` and the CLI import it on every run, so it must stay the standard library and
    PIL. The source is read rather than `sys.modules`, which other tests have already filled."""
    import ast
    from pathlib import Path

    source = Path(host_module.__file__).read_text(encoding="utf-8")
    top = {
        (node.module if isinstance(node, ast.ImportFrom) else alias.name).split(".")[0]
        for node in ast.parse(source).body
        if isinstance(node, ast.Import | ast.ImportFrom)
        for alias in node.names
    }
    assert top - {"__future__", "PIL"} <= {
        "collections",
        "dataclasses",
        "http",
        "io",
        "ipaddress",
        "json",
        "math",
        "os",  # `resolve_host` reads QUACKD_HOST and QUACKD_HOST_TOKEN
        "socket",
        "threading",
        "typing",
        "urllib",
    }


# ── --host ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("jetson.local", ("jetson.local", 9874)),
        ("jetson.local:9874", ("jetson.local", 9874)),
        ("192.168.1.5", ("192.168.1.5", 9874)),
        ("192.168.1.5:9000", ("192.168.1.5", 9000)),
        ("[::1]:9874", ("::1", 9874)),
        ("[::1]", ("::1", 9874)),
        ("::1", ("::1", 9874)),
        ("fe80::1", ("fe80::1", 9874)),
        ("[fe80::1]:65535", ("fe80::1", 65535)),
        ("  jetson.local:1  ", ("jetson.local", 1)),
    ],
)
def test_host_accepts_a_machine_with_or_without_a_port(
    text: str, expected: tuple[str, int]
) -> None:
    assert parse_host(text) == expected


@pytest.mark.parametrize(
    ("text", "says"),
    [
        ("", "empty"),
        ("   ", "empty"),
        ("http://jetson.local:9874", "--base-url"),
        ("https://jetson.local", "not a URL"),
        ("jetson.local:0", "1 to 65535"),
        ("jetson.local:65536", "1 to 65535"),
        ("jetson.local:http", "whole number"),
        ("jetson.local:", "whole number"),
        ("jetson.local:+80", "whole number"),
        ("jetson.local/v1", "no path or query"),
        ("jetson.local?port=9874", "no path or query"),
        ("jetson.local#top", "no path or query"),
        ("[::1]:abc", "whole number"),
        ("[::1]9874", "a colon and a port"),
        ("[::1", "brackets hold an IPv6 address"),
        ("[jetson.local]:9874", "brackets hold an IPv6 address"),
        ("fe80::zz", "neither a machine's name nor an IPv6 address"),
        (":9874", "no machine"),
        ("jetson local", "space"),
    ],
)
def test_host_refuses_what_is_not_a_machine_and_says_what_to_type(text: str, says: str) -> None:
    with pytest.raises(ValueError) as caught:
        parse_host(text)
    assert says in str(caught.value)


@pytest.mark.parametrize(
    "text", [f"{TOKEN}@jetson.local", f"user:{TOKEN}@jetson.local:9874", f"http://x:{TOKEN}@j"]
)
def test_a_token_written_into_host_is_refused_without_being_quoted_back(text: str) -> None:
    with pytest.raises(ValueError) as caught:
        parse_host(text)
    message = str(caught.value)
    assert "--host-token" in message and TOKEN_ENV in message
    assert TOKEN not in message


def test_host_of_is_the_bare_machine_and_the_base_url_brackets_only_ipv6() -> None:
    assert host_of("[::1]:9874") == "::1"
    assert host_of("jetson.local:9000") == "jetson.local"
    assert base_url("jetson.local") == "http://jetson.local:9874"
    assert base_url("192.168.1.5:9000") == "http://192.168.1.5:9000"
    assert base_url("[::1]:9000") == "http://[::1]:9000"
    assert base_url("fe80::1") == "http://[fe80::1]:9874"


def test_the_client_names_its_address_and_keeps_the_text_it_was_given() -> None:
    client = HostClient("fe80::1")
    assert (client.host, client.address, client.base_url) == (
        "fe80::1",
        "[fe80::1]:9874",
        "http://[fe80::1]:9874",
    )
    named = HostClient("jetson.local")
    assert (named.address, named.base_url) == ("jetson.local:9874", "http://jetson.local:9874")
    assert repr(named) == "HostClient('jetson.local:9874')"
    with pytest.raises(ValueError, match="--base-url"):
        HostClient("http://jetson.local:9874")


# ── /hello ──────────────────────────────────────────────────────────────────────────────


def test_hello_is_read_whole_and_asked_for_once(hostd: FakeHostd) -> None:
    client = HostClient(hostd.address)
    hello = client.hello()
    assert (hello.protocol, hello.protocol_version) == (PROTOCOL, 1)
    assert (hello.daemon_version, hello.hostname, hello.python) == ("0.1.0", "orin-nano", "3.10.12")
    assert hello.capabilities == {"camera": True, "detect": True, "tegra": True}
    assert hello.has_camera and hello.can_detect and hello.is_tegra
    assert hello.camera_fov_deg == 62.2
    assert hello.camera_size == (640, 480)
    assert hello.camera_fps == 5.0
    assert hello.label() == "yolov8n.pt on cuda"
    assert hello.board_model == ORIN_NANO
    assert hello.camera_error is None and hello.detect_error is None
    assert hello.to_dict()["detect"]["device"] == "cuda"

    assert client.hello() is hello, "nothing in /hello changes while the daemon runs"
    assert len(hostd.requests_to("/hello")) == 1
    hostd.hello["daemon_version"] = "0.1.1"
    assert client.hello(refresh=True).daemon_version == "0.1.1"
    assert len(hostd.requests_to("/hello")) == 2


def test_a_board_with_no_camera_and_no_detector_says_so_and_why(hostd: FakeHostd) -> None:
    hostd.without_camera().without_detector("ultralytics is not installed on this board")
    hello = HostClient(hostd.address).hello()
    assert not hello.has_camera and not hello.can_detect
    assert hello.camera is None and hello.detect is None
    assert hello.camera_fov_deg is None and hello.camera_size is None
    assert hello.label() is None
    assert hello.camera_error == "started with --camera none"
    assert hello.detect_error == "ultralytics is not installed on this board"


def test_a_lens_nobody_measured_has_no_field_of_view_rather_than_a_guessed_one(
    hostd: FakeHostd,
) -> None:
    """None is what tells the detector to mark its distances uncalibrated; a quiet default here
    would dress a guess up as a measurement."""
    hostd.hello["camera"]["fov_deg"] = None
    hello = HostClient(hostd.address).hello()
    assert hello.has_camera and hello.camera_fov_deg is None


def test_a_laptop_running_the_daemon_is_not_a_tegra(hostd: FakeHostd) -> None:
    hostd.hello["capabilities"]["tegra"] = False
    hostd.hello["board_model"] = None
    hello = HostClient(hostd.address).hello()
    assert not hello.is_tegra and hello.board_model is None


@pytest.mark.parametrize("protocol", ["quackd-open-duck-bridge", None, 7])
def test_a_different_protocol_is_refused_as_the_wrong_port(
    hostd: FakeHostd, protocol: object
) -> None:
    if protocol is None:
        del hostd.hello["protocol"]
    else:
        hostd.hello["protocol"] = protocol
    error = _raises(HostClient(hostd.address).hello)
    message = str(error)
    assert f"{hostd.address} is not quackd-jetson-hostd" in message
    assert "--host" in message and "quackd_jetson_hostd.py" in message


@pytest.mark.parametrize(
    ("version", "update"),
    [(2, "update quackd on this machine"), (0, "update the daemon on the board")],
)
def test_a_protocol_version_mismatch_says_which_side_to_update(
    hostd: FakeHostd, version: int, update: str
) -> None:
    hostd.hello["protocol_version"] = version
    client = HostClient(hostd.address)
    message = str(_raises(client.hello))
    assert update in message
    assert f"version {version}" in message and hostd.address in message
    hostd.hello["protocol_version"] = 1
    assert client.hello().protocol_version == 1, "a refused hello is not cached"


@pytest.mark.parametrize("version", ["1", True, 1.0, None])
def test_a_protocol_version_that_is_not_a_whole_number_is_the_wrong_shape(
    hostd: FakeHostd, version: object
) -> None:
    """True is 1 to Python, and a daemon that sent it is not speaking version 1."""
    hostd.hello["protocol_version"] = version
    message = str(_raises(HostClient(hostd.address).hello))
    assert "wrong shape" in message and "protocol_version" in message


def _set(path: str, value: object) -> Callable[[dict[str, Any]], None]:
    """A change to one field of a reply, `camera.size` style, or its removal for `...`."""

    def apply(reply: dict[str, Any]) -> None:
        *parents, last = path.split(".")
        node = reply
        for key in parents:
            node = node[key]
        if value is ...:
            del node[last]
        else:
            node[last] = value

    return apply


@pytest.mark.parametrize(
    ("change", "field"),
    [
        (_set("capabilities", ["camera"]), "capabilities"),
        (_set("capabilities.camera", "yes"), "capabilities.camera"),
        (_set("daemon_version", ...), "daemon_version"),
        (_set("hostname", 5), "hostname"),
        (_set("camera", "csi"), "camera"),
        (_set("camera.size", [640]), "camera.size"),
        (_set("camera.size", [640, "480"]), "camera.size"),
        (_set("camera.fov_deg", "wide"), "camera.fov_deg"),
        (_set("camera.fov_deg", 0), "camera.fov_deg"),
        (_set("camera.fov_deg", 180.0), "camera.fov_deg"),
        (_set("camera.fps", -5), "camera.fps"),
        (_set("detect.model", ...), "detect.model"),
        (_set("detect.device", None), "detect.device"),
        (_set("detect_error", ["no"]), "detect_error"),
    ],
)
def test_a_hello_of_the_wrong_shape_is_a_host_error_naming_the_host_and_the_field(
    hostd: FakeHostd, change: Callable[[dict[str, Any]], None], field: str
) -> None:
    change(hostd.hello)
    message = str(_raises(HostClient(hostd.address).hello))
    assert hostd.address in message and "wrong shape" in message
    assert field in message


def test_something_else_on_the_port_is_named_as_not_the_daemon(hostd: FakeHostd) -> None:
    hostd.replies["/hello"] = CannedReply(404, b'{"ok": false, "reason": "nothing at /hello"}')
    error = _raises(HostClient(hostd.address).hello)
    assert f"{hostd.address} is not quackd-jetson-hostd" in str(error)
    assert error.status == 404


# ── the token ───────────────────────────────────────────────────────────────────────────


def test_a_daemon_that_wants_a_token_and_got_none_names_the_flag_and_the_variable(
    hostd: FakeHostd,
) -> None:
    hostd.token = TOKEN
    error = _raises(HostClient(hostd.address).hello)
    assert error.status == 401
    assert "none was given" in str(error)
    assert "--host-token" in str(error) and TOKEN_ENV in str(error)
    assert TOKEN not in str(error)


def test_a_wrong_token_is_refused_and_neither_token_is_quoted(hostd: FakeHostd) -> None:
    hostd.token = TOKEN
    wrong = "0000feedfacecafe0000feedfacecafe"
    client = HostClient(hostd.address, token=wrong)
    error = _raises(client.hello)
    assert error.status == 401
    assert "refused the token" in str(error) and "--host-token" in str(error)
    for secret in (TOKEN, wrong):
        assert secret not in str(error) and secret not in repr(error)
        assert secret not in repr(client)


def test_the_token_rides_in_the_header_on_every_path_and_never_in_a_url(
    hostd: FakeHostd,
) -> None:
    hostd.token = TOKEN
    client = HostClient(hostd.address, token=TOKEN)
    assert client.has_token
    client.hello()
    client.healthz()
    client.board()
    client.snapshot()
    client.detect(jpeg_bytes(), conf=0.3)
    seen = hostd.requests
    assert [r.path for r in seen] == ["/hello", "/healthz", "/board", "/snapshot.jpg", "/detect"]
    for request in seen:
        assert request.headers.get(TOKEN_HEADER.lower()) == TOKEN
        assert TOKEN not in request.path and TOKEN not in request.query
    assert TOKEN not in client.base_url and TOKEN not in client.snapshot_url
    assert TOKEN not in str(client.camera_health())


def test_a_blank_token_is_no_token(hostd: FakeHostd) -> None:
    client = HostClient(hostd.address, token="  \t ")
    assert not client.has_token
    client.hello()
    assert TOKEN_HEADER.lower() not in hostd.requests[-1].headers
    hostd.token = TOKEN
    assert "none was given" in str(_raises(lambda: client.hello(refresh=True)))


def test_a_token_read_from_a_file_with_its_newline_still_matches(hostd: FakeHostd) -> None:
    """`openssl rand -hex 32 | tee token` ends the file in a newline, and a header cannot carry
    one, so the token is trimmed before it is sent."""
    hostd.token = TOKEN
    assert HostClient(hostd.address, token=f"{TOKEN}\n").hello().protocol == PROTOCOL


@pytest.mark.parametrize("bad", [f"{TOKEN[:8]}\n{TOKEN[8:]}", f"{TOKEN}é", f"{TOKEN}\x00"])
def test_a_token_a_header_cannot_carry_is_refused_before_anything_is_sent(
    hostd: FakeHostd, bad: str
) -> None:
    """`http.client` would refuse it too, with a message that quotes the header's value."""
    with pytest.raises(ValueError) as caught:
        HostClient(hostd.address, token=bad)
    assert TOKEN[:8] not in str(caught.value) and TOKEN[8:] not in str(caught.value)
    assert hostd.requests == []


def test_no_message_and_no_repr_ever_carries_the_token(hostd: FakeHostd) -> None:
    """Every way this client can fail, with a token set, and one daemon that echoes the token in
    its reason: the one message that could carry it has the token replaced, and so does the
    payload the error keeps, which a caller may show as it is."""
    client = HostClient(hostd.address, token=TOKEN)
    messages: list[str] = []

    def collect(call: Callable[[], Any]) -> None:
        error = _raises(call)
        messages.extend((str(error), repr(error), json.dumps(error.payload)))

    hostd.token = "a-different-token-entirely-0000"
    for call in (client.hello, client.healthz, client.board, client.snapshot):
        collect(call)
    collect(lambda: client.detect(jpeg_bytes()))

    hostd.token = None
    hostd.replies["/board"] = CannedReply(
        503, f'{{"ok": false, "reason": "token {TOKEN} is not welcome here"}}'.encode()
    )
    collect(client.board)
    hostd.replies["/healthz"] = CannedReply(200, b"<html>not json</html>", "text/html")
    collect(client.healthz)
    hostd.frame_age_s = 9.0
    collect(client.snapshot)
    hostd.camera_stopped()
    collect(client.snapshot)
    hostd.without_detector()
    collect(lambda: client.detect(jpeg_bytes()))
    collect(lambda: client.detect(b"\xff\xd8" + bytes(MAX_JPEG_BYTES)))
    collect(HostClient(dead_address(), token=TOKEN).healthz)

    assert len(messages) == 36, "twelve failures, each read as str, as repr and as its payload"
    leaked = [m for m in messages if TOKEN in m]
    assert not leaked, leaked
    assert any("token <token> is not welcome here" in m for m in messages)
    assert TOKEN not in repr(client) and TOKEN not in str(client)
    assert TOKEN not in str(client.camera_health())


def test_no_reply_the_client_returns_carries_the_token(hostd: FakeHostd) -> None:
    """A daemon that echoes its token in an answer rather than a refusal: in a hostname, a
    capability's name, a health reason, a board file, the name of a board error and its text, a
    box and a model. Each comes back with `<token>` in its place, because whatever shows a reply
    would show the token with it: `doctor` prints the hello, the health and the board, and
    carries them in --json."""
    hostd.token = TOKEN
    hostd.hello["hostname"] = f"orin-{TOKEN}"
    hostd.hello["capabilities"][TOKEN] = True
    hostd.healthz["reason"] = f"token {TOKEN}"
    hostd.board["files"]["/proc/swaps"] = f"/swapfile {TOKEN}"
    hostd.board["errors"][TOKEN] = f"said {TOKEN}"
    hostd.detect_reply["boxes"][0]["name"] = f"ball {TOKEN}"
    hostd.detect_reply["model"] = f"yolo-{TOKEN}"
    client = HostClient(hostd.address, token=TOKEN)
    replies = json.dumps(
        [
            client.hello().to_dict(),
            client.healthz(),
            dataclasses.asdict(client.board()),
            dataclasses.asdict(client.detect(jpeg_bytes())),
        ]
    )
    assert TOKEN not in replies
    assert replies.count("<token>") == 8, "every echo is still there, as <token>"


def test_a_name_with_no_address_is_unresolved_and_says_so(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The one failure that says nothing else at that name can be reached either, which is
    what lets `doctor` skip the LLM servers there. The lookup is refused here rather than
    made, because a real one for a missing name can take seconds to fail."""
    real = socket.getaddrinfo

    def lookup(host: Any, *args: Any, **kwargs: Any) -> Any:
        if host == "nosuch-jetson.invalid":
            raise socket.gaierror(socket.EAI_NONAME, "no such name")
        return real(host, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", lookup)
    error = _raises(HostClient("nosuch-jetson.invalid").hello)
    assert error.unresolved is True
    assert "no machine by that name could be found" in str(error)


# ── /snapshot.jpg ───────────────────────────────────────────────────────────────────────


def test_a_snapshot_is_an_rgb_image_and_its_age(hostd: FakeHostd) -> None:
    client = HostClient(hostd.address)
    image, age = client.snapshot()
    assert image.mode == "RGB" and image.size == (640, 480)
    assert age == pytest.approx(0.12)
    x1, y1, x2, y2 = BALL_BOX
    r, g, b = image.getpixel((int((x1 + x2) / 2), int((y1 + y2) / 2)))
    assert abs(r - SIM_ORANGE[0]) < 20 and abs(g - SIM_ORANGE[1]) < 20 and b < 40
    assert client.camera_health() == {
        "url": f"{hostd.url}/snapshot.jpg",
        "frames": 1,
        "age_s": pytest.approx(0.12),
        "stale_after_s": STALE_AFTER_S,
        "error": None,
    }


def test_a_frame_without_an_age_stamp_is_taken_with_no_age(hostd: FakeHostd) -> None:
    hostd.frame_age_s = None
    image, age = HostClient(hostd.address).snapshot()
    assert age is None and image.size == (640, 480)


def test_camera_health_has_the_shape_doctor_renders_for_the_open_duck() -> None:
    from quackd_open_duck.bridge import OpenDuckBridge

    duck = OpenDuckBridge(camera_url="http://duck.local:8081/snapshot.jpg").camera_health()
    assert set(HostClient("jetson.local").camera_health()) == set(duck)


def test_a_frame_stamped_older_than_two_seconds_is_refused_as_stale(hostd: FakeHostd) -> None:
    """The daemon expires its own frames sooner; this is the backstop for one that did not."""
    hostd.frame_age_s = 2.5
    client = HostClient(hostd.address)
    error = _raises(client.snapshot)
    assert "2.5s old" in str(error) and "stopped" in str(error) and hostd.address in str(error)
    health = client.camera_health()
    assert health["error"] == str(error)
    assert health["age_s"] == pytest.approx(2.5) and health["frames"] == 0


@pytest.mark.parametrize(
    ("setup", "reason", "age"),
    [
        (FakeHostd.no_frame_yet, "no frame captured yet", None),
        (FakeHostd.camera_stopped, "the last frame is 3.2s old (stale after 1.5s)", 3.2),
        (FakeHostd.without_camera, "no camera on this host: started with --camera none", None),
    ],
)
def test_the_daemons_own_503_reason_reaches_the_caller(
    hostd: FakeHostd, setup: Callable[[FakeHostd], Any], reason: str, age: float | None
) -> None:
    setup(hostd)
    client = HostClient(hostd.address)
    error = _raises(client.snapshot)
    assert error.status == 503
    assert reason in str(error) and hostd.address in str(error)
    health = client.camera_health()
    assert health["error"] == str(error) and health["frames"] == 0
    assert health["age_s"] == (pytest.approx(age) if age is not None else None)


def test_a_camera_that_comes_back_clears_its_error(hostd: FakeHostd) -> None:
    client = HostClient(hostd.address)
    hostd.no_frame_yet()
    _raises(client.snapshot)
    assert client.camera_error is not None
    hostd.snapshot_reason = None
    hostd.snapshot_jpeg = jpeg_bytes()
    hostd.frame_age_s = 0.3
    client.snapshot()
    assert client.camera_health()["error"] is None
    assert (client.frames, client.frame_age_s) == (1, pytest.approx(0.3))


def test_a_snapshot_that_is_not_an_image_is_a_host_error(hostd: FakeHostd) -> None:
    hostd.replies["/snapshot.jpg"] = CannedReply(200, b"not a jpeg at all", "image/jpeg")
    client = HostClient(hostd.address)
    error = _raises(client.snapshot)
    assert "not an image" in str(error)
    assert client.camera_error == str(error)


@pytest.mark.parametrize("stamp", ["soon", "nan", "-1.0", "inf"])
def test_a_frame_age_that_is_not_an_age_is_a_host_error(hostd: FakeHostd, stamp: str) -> None:
    hostd.replies["/snapshot.jpg"] = CannedReply(
        200, jpeg_bytes(), "image/jpeg", (("X-Frame-Age", stamp),)
    )
    assert "stamped its frame" in str(_raises(HostClient(hostd.address).snapshot))


# ── /detect ─────────────────────────────────────────────────────────────────────────────


def test_detect_posts_the_jpeg_and_reads_the_boxes(hostd: FakeHostd) -> None:
    jpeg = jpeg_bytes()
    found = HostClient(hostd.address).detect(jpeg, conf=0.3)
    (seen,) = hostd.requests_to("/detect")
    assert (seen.method, seen.query) == ("POST", "conf=0.3")
    assert seen.headers["content-type"] == "image/jpeg"
    assert seen.body == jpeg and seen.body_length == len(jpeg)
    assert found == HostDetections(
        w=640,
        h=480,
        boxes=(HostBox("sports ball", 0.87, 300.0, 200.0, 340.0, 240.0),),
        model="yolov8n.pt",
        device="cuda",
        ms=23.4,
    )
    assert found.boxes[0].as_tuple() == ("sports ball", 0.87, 300.0, 200.0, 340.0, 240.0)


def test_detect_without_a_confidence_sends_no_query_and_takes_no_boxes_as_an_answer(
    hostd: FakeHostd,
) -> None:
    hostd.detect_reply["boxes"] = []
    found = HostClient(hostd.address).detect(jpeg_bytes(ball=None))
    assert found.boxes == ()
    assert hostd.requests_to("/detect")[0].query == ""


def test_a_board_without_a_detector_says_why_when_asked_to_detect(hostd: FakeHostd) -> None:
    hostd.without_detector("ultralytics is not installed on this board")
    error = _raises(lambda: HostClient(hostd.address).detect(jpeg_bytes()))
    assert error.status == 503
    assert "detection is not available: ultralytics is not installed on this board" in str(error)


def test_a_body_the_daemon_cannot_decode_surfaces_its_reason(hostd: FakeHostd) -> None:
    error = _raises(lambda: HostClient(hostd.address).detect(b"GIF89a not a jpeg"))
    assert error.status == 400
    assert "not an image this daemon can decode" in str(error)


def test_an_oversized_frame_is_refused_before_a_byte_is_sent(hostd: FakeHostd) -> None:
    error = _raises(lambda: HostClient(hostd.address).detect(b"\xff\xd8" + bytes(MAX_JPEG_BYTES)))
    assert "smaller frame" in str(error)
    assert hostd.requests_to("/detect") == []


@pytest.mark.parametrize("conf", [1.5, -0.1, math.nan, True, 0.0, -0.0, 0])
def test_a_confidence_the_daemon_would_refuse_is_the_callers_mistake(
    hostd: FakeHostd, conf: float
) -> None:
    """The daemon takes a floor above 0 and at most 1. A 0 sent anyway would come back as a 400,
    a HostError, which every consumer reads as the board failing rather than as a caller's
    bug. `tests/test_jetson_host_contract.py` holds the two sides to one rule."""
    with pytest.raises(ValueError, match="above 0 and at most 1"):
        HostClient(hostd.address).detect(jpeg_bytes(), conf=conf)
    assert hostd.requests == []


@pytest.mark.parametrize(
    ("change", "field"),
    [
        (_set("boxes", {}), "boxes"),
        (_set("boxes", ...), "boxes"),
        (_set("w", ...), "w"),
        (_set("w", 0), "no pixels"),
        (_set("h", 480.5), "h"),
        (lambda r: r["boxes"].append("ball"), "boxes[1]"),
        (lambda r: r["boxes"][0].pop("y2"), "boxes[0].y2"),
        (lambda r: r["boxes"][0].update(conf=1.5), "boxes[0].conf"),
        (lambda r: r["boxes"][0].update(conf="high"), "boxes[0].conf"),
        (lambda r: r["boxes"][0].update(x1=math.nan), "boxes[0].x1"),
        (lambda r: r["boxes"][0].update(name=None), "boxes[0].name"),
        (_set("ms", "fast"), "ms"),
    ],
)
def test_detections_of_the_wrong_shape_are_a_host_error_not_a_crash(
    hostd: FakeHostd, change: Callable[[dict[str, Any]], Any], field: str
) -> None:
    """NaN included: `json` carries it, and a bearing computed from it is NaN too."""
    change(hostd.detect_reply)
    message = str(_raises(lambda: HostClient(hostd.address).detect(jpeg_bytes())))
    assert hostd.address in message and field in message


# ── /board and /healthz ─────────────────────────────────────────────────────────────────


def test_board_returns_the_raw_texts_untouched(hostd: FakeHostd) -> None:
    board = HostClient(hostd.address).board()
    assert board.files == {
        "/proc/device-tree/model": ORIN_NANO,
        "/proc/device-tree/compatible": COMPATIBLE,
        "/etc/nv_tegra_release": RELEASE_36_4_3,
        "/proc/meminfo": MEMINFO,
        "/proc/swaps": ZRAM_SWAPS,
    }
    assert board.nodes == {
        "/dev/nvgpu/igpu0": True,
        "/dev/nvhost-ctrl-gpu": False,
        "/dev/nvidia0": False,
    }
    assert board.commands == {"nvpmodel -q": NVPMODEL_Q, "tegrastats": TEGRASTATS_LINE}
    assert board.errors == {}


def test_what_the_board_could_not_read_is_none_with_its_reason(hostd: FakeHostd) -> None:
    hostd.board["files"]["/etc/nv_tegra_release"] = None
    hostd.board["commands"]["tegrastats"] = None
    hostd.board["errors"] = {
        "/etc/nv_tegra_release": "No such file or directory",
        "tegrastats": "not on PATH",
    }
    board = HostClient(hostd.address).board()
    assert board.files["/etc/nv_tegra_release"] is None
    assert board.commands["tegrastats"] is None
    assert board.errors["tegrastats"] == "not on PATH"


def test_the_board_texts_read_through_doctors_own_parsers(hostd: FakeHostd) -> None:
    """The daemon parses nothing so that one set of parsers reads every board. These are the
    assertions `doctor` makes of a board built from files, made of the same texts arriving over
    the network instead."""
    from quackd import doctor

    board = HostClient(hostd.address).board()
    release = board.files["/etc/nv_tegra_release"]
    assert release is not None
    l4t = doctor._parse_l4t(release)
    assert (l4t, doctor._jetpack_for(l4t or "")) == ("36.4.3", "6.2")
    assert "nvidia,tegra" in (board.files["/proc/device-tree/compatible"] or "")
    assert (board.files["/proc/device-tree/model"] or "").strip() == ORIN_NANO
    mem = doctor._kb_fields(board.files["/proc/meminfo"] or "", ("MemTotal", "MemAvailable"))
    assert mem == {"MemTotal": 7650336 * 1024, "MemAvailable": 5123456 * 1024}
    assert doctor._swaps(board.files["/proc/swaps"] or "") == (1017852 * 1024, ["/dev/zram0"])
    assert doctor._power_mode(board.commands["nvpmodel -q"]) == "15W"


@pytest.mark.parametrize(
    ("change", "field"),
    [
        (_set("files", ["model"]), "files"),
        (_set("commands", ...), "commands"),
        (lambda r: r["nodes"].update({"/dev/nvidia0": "yes"}), "nodes[/dev/nvidia0]"),
        (lambda r: r["files"].update({"/proc/swaps": 12}), "files[/proc/swaps]"),
        (lambda r: r["errors"].update({"tegrastats": 3}), "errors[tegrastats]"),
    ],
)
def test_a_board_dump_of_the_wrong_shape_is_a_host_error(
    hostd: FakeHostd, change: Callable[[dict[str, Any]], Any], field: str
) -> None:
    change(hostd.board)
    message = str(_raises(HostClient(hostd.address).board))
    assert hostd.address in message and field in message


def test_an_unhealthy_daemon_is_an_answer_not_an_error(hostd: FakeHostd) -> None:
    hostd.healthz["ok"] = False
    hostd.healthz["camera"]["ok"] = False
    health = HostClient(hostd.address).healthz()
    assert health["ok"] is False and health["camera"]["ok"] is False


def test_a_refusal_in_a_200_is_still_a_refusal(hostd: FakeHostd) -> None:
    hostd.board = {"ok": False, "reason": "the board lock is held"}
    message = str(_raises(HostClient(hostd.address).board))
    assert "refused /board: the board lock is held" in message


# ── replies that are not the daemon's ───────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("path", "reply"),
    [
        ("/hello", CannedReply(200, b"<html><body>router login</body></html>", "text/html")),
        ("/hello", CannedReply(200, b"[1, 2, 3]")),
        ("/hello", CannedReply(200, b"\xff\xfe\x00garbage")),
        ("/board", CannedReply(200, b'"a string"')),
        ("/healthz", CannedReply(200, b"null")),
        ("/healthz", CannedReply(200, b"[" * 100_000)),
        ("/detect", CannedReply(200, b"42")),
        ("/hello", CannedReply(500, b"Internal Server Error", "text/plain")),
    ],
)
def test_a_reply_that_is_not_a_json_object_is_a_host_error_not_a_crash(
    hostd: FakeHostd, path: str, reply: CannedReply
) -> None:
    """The ten thousand brackets are for `json.loads`, which meets them with a RecursionError
    rather than a ValueError."""
    hostd.replies[path] = reply
    client = HostClient(hostd.address)
    calls: dict[str, Callable[[], Any]] = {
        "/hello": client.hello,
        "/board": client.board,
        "/healthz": client.healthz,
        "/detect": lambda: client.detect(jpeg_bytes()),
    }
    error = _raises(calls[path])
    assert hostd.address in str(error)


def test_a_hostile_field_name_reaches_the_terminal_as_one_clean_line_without_the_token(
    hostd: FakeHostd,
) -> None:
    """A field name is the board's text, like a reason is. An escape sequence in one would
    reach the terminal of whoever runs doctor, and a daemon that echoed the token in one would
    put it on their screen, so both are cleaned, and the unclean original is not chained on
    where a traceback would print it."""
    hostd.hello["capabilities"][f"\x1b[2J{TOKEN}\nforged line"] = "yes"
    error = _raises(HostClient(hostd.address, token=TOKEN).hello)
    message = str(error)
    assert "wrong shape" in message and "<token>" in message
    assert TOKEN not in message and "\n" not in message and "\x1b" not in message
    assert error.__cause__ is None and error.__suppress_context__


_PADDING = "the daemon compared the header it was sent, " * 4
"""176 characters, so a 64-character token after them straddles the 200 a reason is cut at."""


def _echo_the_token(hostd: FakeHostd, where: str) -> Callable[[HostClient], Any]:
    """Has `hostd` echo the token in `where`, and returns the call that meets the echo."""
    echo = _PADDING + TOKEN
    if where == "a 503 reason":
        body = json.dumps({"ok": False, "reason": echo}).encode()
        hostd.replies["/board"] = CannedReply(503, body)
        return lambda client: client.board()
    if where == "a refusal in a 200":
        hostd.detect_reply = {"ok": False, "reason": echo}
        return lambda client: client.detect(jpeg_bytes())
    if where == "the protocol it names":
        hostd.hello["protocol"] = f"not-{TOKEN}"
        return lambda client: client.hello()
    if where == "a capability's name":
        hostd.hello["capabilities"][TOKEN] = "yes"
        return lambda client: client.hello()
    if where == "a board node's name":
        hostd.board["nodes"][TOKEN] = "yes"
        return lambda client: client.board()
    if where == "the frame's age stamp":
        stamp = (("X-Frame-Age", echo),)
        hostd.replies["/snapshot.jpg"] = CannedReply(200, jpeg_bytes(), "image/jpeg", stamp)
        return lambda client: client.snapshot()
    raise AssertionError(f"no echo arranged for {where!r}")


@pytest.mark.parametrize(
    "where",
    [
        "a 503 reason",
        "a refusal in a 200",
        "the protocol it names",
        "a capability's name",
        "a board node's name",
        "the frame's age stamp",
    ],
)
def test_a_token_echoed_across_the_cut_leaves_not_even_a_piece_of_itself(
    hostd: FakeHostd, where: str
) -> None:
    """The board's text is cut short before it goes in a sentence: a reason at 200 characters,
    a field name at 60. A token echoed across the cut would leave most of itself behind, in a
    piece that a replace run on the finished sentence cannot find, so the token is replaced
    before anything is cut. The payload the error keeps is scrubbed too, whole."""
    call = _echo_the_token(hostd, where)
    error = _raises(lambda: call(HostClient(hostd.address, token=TOKEN)))
    for shown in (str(error), repr(error), json.dumps(error.payload)):
        assert _longest_piece_of(TOKEN, shown) < 8, shown
    assert "<token>" in str(error)


@contextmanager
def _answering_once_with(raw: bytes) -> Iterator[str]:
    """A listener on loopback that answers one connection with `raw` as it is, for a reply no
    HTTP server would send; yields its address. It reads until the client hangs up rather than
    closing first, because Windows may drop the reply it has not yet delivered on a close."""
    listener = socket.create_server(("127.0.0.1", 0))
    listener.settimeout(5.0)

    def answer() -> None:
        try:
            conn, _ = listener.accept()
            with conn:
                conn.settimeout(5.0)
                conn.recv(64 * 1024)
                conn.sendall(raw)
                while conn.recv(64 * 1024):
                    pass
        except OSError:
            return

    thread = threading.Thread(target=answer, daemon=True)
    thread.start()
    try:
        yield f"127.0.0.1:{listener.getsockname()[1]}"
    finally:
        thread.join(timeout=5.0)
        listener.close()


def test_a_token_echoed_in_a_line_that_is_not_http_is_not_shown_either() -> None:
    """Something on the port that does not speak HTTP fails inside `http.client`, whose error
    quotes the line it was sent, and that line is the board's text like any other."""
    with _answering_once_with(f"QUACK {_PADDING}{TOKEN}\r\n\r\n".encode()) as address:
        error = _raises(HostClient(address, token=TOKEN).hello)
    assert _longest_piece_of(TOKEN, str(error)) < 8, str(error)
    assert "<token>" in str(error) and address in str(error)


def test_a_refusal_nested_deeper_than_python_recurses_is_still_a_host_error(
    hostd: FakeHostd,
) -> None:
    """The payload is scrubbed with a loop and not recursion. `json.loads` can read nesting
    deeper than a Python function can recurse (Python 3.12 reads this), and a RecursionError out
    of a refusal would be a crash where a sentence belongs. Where the parser gives up first, the
    refusal has no payload, which is a HostError too."""
    deep = "[" * 2500 + "]" * 2500
    body = f'{{"ok": false, "reason": "busy", "deep": {deep}}}'.encode()
    hostd.replies["/board"] = CannedReply(503, body)
    error = _raises(HostClient(hostd.address, token=TOKEN).board)
    assert error.status == 503 and hostd.address in str(error)


def test_a_reply_bigger_than_any_the_protocol_defines_is_refused(hostd: FakeHostd) -> None:
    hostd.replies["/healthz"] = CannedReply(200, b" " * (host_module.MAX_REPLY_BYTES + 1))
    message = str(_raises(HostClient(hostd.address).healthz))
    assert "more than 16 MB" in message and hostd.address in message


def test_a_redirect_is_not_followed_so_the_token_goes_nowhere_else(hostd: FakeHostd) -> None:
    """urllib would follow it with every header of the original request, token included."""
    with FakeHostd() as elsewhere:
        hostd.replies["/hello"] = CannedReply(
            302, b"", "text/plain", (("Location", f"{elsewhere.url}/hello"),)
        )
        error = _raises(HostClient(hostd.address, token=TOKEN).hello)
        assert "redirect" in str(error) and error.status == 302
        assert elsewhere.requests == []


def test_a_proxy_in_the_environment_is_never_used(
    hostd: FakeHostd, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A proxy would see the token header and every frame of a board on the local network.
    This one is a dead port, so the request only succeeds if it went straight to the board."""
    for name in ("HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy"):
        monkeypatch.setenv(name, f"http://{dead_address()}")
    for name in ("NO_PROXY", "no_proxy"):
        monkeypatch.delenv(name, raising=False)
    assert HostClient(hostd.address).hello().protocol == PROTOCOL


# ── a daemon that is not there ──────────────────────────────────────────────────────────


def test_a_daemon_that_is_down_is_a_host_error_within_the_timeout() -> None:
    """Linux refuses a closed loopback port at once and Windows retries until the timeout, so
    the bound is the timeout plus a margin, and both messages name the address and the daemon."""
    address = dead_address()
    client = HostClient(address)
    started = time.monotonic()
    error = _raises(client.hello)
    elapsed = time.monotonic() - started
    assert elapsed < HELLO_TIMEOUT_S + 1.5
    assert address in str(error) and "hostd" in str(error)
    assert error.status is None
    assert error.unresolved is False, "a machine that is there and refused is not a missing name"


def test_a_daemon_that_hangs_costs_one_timeout_window(hostd: FakeHostd) -> None:
    hostd.delays["/snapshot.jpg"] = SNAPSHOT_TIMEOUT_S + 1.5
    client = HostClient(hostd.address)
    started = time.monotonic()
    error = _raises(client.snapshot)
    assert time.monotonic() - started < SNAPSHOT_TIMEOUT_S + 1.0
    assert f"did not answer /snapshot.jpg within {SNAPSHOT_TIMEOUT_S:g}s" in str(error)
    assert client.camera_error == str(error)
