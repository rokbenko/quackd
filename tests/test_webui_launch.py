"""`quackd ui`'s launcher: where the page listens, the token it asks for, how Streamlit is started,
and the way to it from a phone (`quackd.webui.launch`). Plain Python: runs without Streamlit, on
every operating system, as the refusal of a taken port does."""

from __future__ import annotations

import io
import os
import socket
import stat
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from typer.testing import CliRunner

from quackd.cli import app
from quackd.webui import launch


def test_the_page_listens_on_this_computer_alone_unless_told_and_is_reached_where_it_listens(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Each address the page prints is one it answers on: a page bound to one address answers
    there alone, and an IPv6 address goes in brackets."""
    for address in ("127.0.0.1", "::1", "localhost", " LOCALHOST "):
        assert launch.is_loopback(address), address
    for address in ("0.0.0.0", "::", "192.168.1.23", "100.64.0.7"):
        assert not launch.is_loopback(address), address
    assert launch.urls("127.0.0.1", 8501, None) == ["http://127.0.0.1:8501"]
    assert launch.urls("127.0.0.1", 8501, "t0k") == ["http://127.0.0.1:8501/?token=t0k"]
    assert launch.urls("::1", 8501, "t0k") == ["http://[::1]:8501/?token=t0k"]
    assert launch.urls("fd7a:115c::5", 8600, "t0k") == ["http://[fd7a:115c::5]:8600/?token=t0k"]
    for bind, here in (
        ("127.0.0.1", "127.0.0.1"),
        ("0.0.0.0", "127.0.0.1"),
        ("::1", "::1"),
        ("192.168.1.23", "192.168.1.23"),
    ):
        assert launch.local_host(bind) == here, bind
    monkeypatch.setattr(launch, "lan_addresses", lambda: ["192.168.1.23", "10.0.0.5"])
    assert launch.urls("0.0.0.0", 8501, "t0k") == [
        "http://192.168.1.23:8501/?token=t0k",
        "http://10.0.0.5:8501/?token=t0k",
    ]
    assert launch.urls("100.64.0.7", 8600, "t0k") == ["http://100.64.0.7:8600/?token=t0k"]


def test_the_token_is_made_once_kept_from_other_users_and_made_anew_only_when_asked(
    tmp_path: Path,
) -> None:
    """The same at every start, so a phone's home-screen shortcut keeps working, and
    `--new-token` the one way to change it."""
    first = launch.token(tmp_path)
    assert len(first) >= 24
    assert launch.token(tmp_path) == first
    path = tmp_path / launch.TOKEN_FILE
    assert path.read_text(encoding="utf-8").strip() == first
    if sys.platform != "win32":  # Windows keeps a file in a user's profile to that user already
        assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    renewed = launch.token(tmp_path, renew=True)
    assert renewed != first and launch.token(tmp_path) == renewed


def test_streamlit_is_told_every_setting_that_decides_who_reaches_the_page() -> None:
    """On its command line, which outranks a `.streamlit/config.toml` or a variable a cloned
    project brings: neither may turn CORS or XSRF off, know the cookie secret, or move the page."""
    flags = launch.streamlit_flags("0.0.0.0", 8600)
    pairs = dict(zip(flags[::2], flags[1::2], strict=True))
    assert pairs["--server.address"] == "0.0.0.0", "never left to Streamlit, which takes all"
    assert pairs["--server.port"] == "8600"
    assert pairs["--server.enableCORS"] == "true"
    assert pairs["--server.enableXsrfProtection"] == "true"
    assert "--server.cookieSecret" not in pairs, "Streamlit refuses it on its command line"
    assert pairs["--server.baseUrlPath"] == ""
    assert pairs["--browser.gatherUsageStats"] == "false"
    assert pairs["--server.fileWatcherType"] == "none"
    assert pairs["--server.headless"] == "true"


def _fake_streamlit(monkeypatch: pytest.MonkeyPatch, started: list[list[str]]) -> None:
    """Streamlit's command line, as `serve` runs it, with nothing behind it: what it was given
    is kept, and the page's own wait for it to answer calls straight back."""
    cli = SimpleNamespace(main=lambda: started.append(list(sys.argv)))
    monkeypatch.setitem(sys.modules, "streamlit", SimpleNamespace(web=SimpleNamespace(cli=cli)))
    monkeypatch.setitem(sys.modules, "streamlit.web", SimpleNamespace(cli=cli))
    monkeypatch.setitem(sys.modules, "streamlit.web.cli", cli)
    monkeypatch.setattr(sys, "argv", ["quackd", "ui"])
    monkeypatch.setenv(launch.COOKIE_SECRET_ENV, "known-to-a-project")


@pytest.mark.parametrize(
    ("bind", "polled"),
    [
        ("127.0.0.1", "http://127.0.0.1:8611"),
        ("0.0.0.0", "http://127.0.0.1:8611"),
        ("192.168.1.23", "http://192.168.1.23:8611"),
        ("::1", "http://[::1]:8611"),
    ],
)
def test_the_page_asks_for_its_token_everywhere_and_is_waited_for_where_it_listens(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, bind: str, polled: str
) -> None:
    """On loopback too: another account on the computer reaches 127.0.0.1, and so can a page in
    your own browser that rebinds a name of its own to it. And a page bound to one address
    answers there alone, which is where it is waited for and opened."""
    started: list[list[str]] = []
    waited: list[str] = []
    opened: list[str] = []
    said: list[str] = []
    _fake_streamlit(monkeypatch, started)
    monkeypatch.setattr(launch, "lan_addresses", lambda: ["192.168.1.23"])

    def when_up(url: str, then: Any, within_s: float = 60.0) -> None:
        waited.append(url)
        then()

    monkeypatch.setattr(launch, "_when_up", when_up)
    monkeypatch.setattr(launch.webbrowser, "open", opened.append)
    monkeypatch.setattr(launch, "can_draw", lambda _stream: True)
    launch.serve(
        folder=tmp_path,
        bind=bind,
        port=8611,
        browser=True,
        token_dir=tmp_path,
        new_token=False,
        say=said.append,
    )
    config = launch.CONFIG
    assert config is not None and config.token and config.home == tmp_path
    assert waited == [polled]
    assert opened == [f"{polled}/?token={config.token}"]
    assert said[0].startswith("quackd ui: http") and said[0].endswith(f"?token={config.token}")
    warned = any("can drive your robots" in line for line in said)
    assert warned == (not launch.is_loopback(bind)), "the warning and the QR code: off loopback"
    (argv,) = started
    assert argv[argv.index("--server.address") + 1] == bind
    assert len(os.environ[launch.COOKIE_SECRET_ENV]) == 64, "a secret of this start's own"


def test_the_qr_picture_is_kept_private_and_reads_back_as_the_address(tmp_path: Path) -> None:
    import cv2

    address = "http://192.168.1.23:8501/?token=abcdefghijklmnopqrstuvwx"
    path = tmp_path / launch.QR_FILE
    launch.write_qr_png(address, path)
    if sys.platform != "win32":
        assert stat.S_IMODE(os.stat(path).st_mode) == 0o600, "the token is in it"
    read, _points, _code = cv2.QRCodeDetector().detectAndDecode(cv2.imread(str(path)))
    assert read == address


def test_the_saved_setup_is_kept_private(tmp_path: Path) -> None:
    """It holds no key, but an address or an extra body may carry a credential."""
    from quackd.webui.state import Setup

    setup = Setup(tmp_path / "ui-setup.json")
    setup.set("base_url", "https://user:pass@example.com/v1")
    assert Setup(tmp_path / "ui-setup.json").get("base_url") == "https://user:pass@example.com/v1"
    if sys.platform != "win32":
        assert stat.S_IMODE(os.stat(tmp_path / "ui-setup.json").st_mode) == 0o600


def test_quackd_ui_keeps_a_dotenv_out_of_the_pages_home_and_out_of_its_runs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The root command loads a `.env` for `quackd ui` as for any command. A cloned project's
    must not choose where the page keeps its token and its setup, and the runs the page starts
    load it themselves, so they can still tell a file's values from the shell's."""
    import quackd.webui.catalog as catalog
    from quackd.registry import DEFAULT_DIR
    from quackd.webui.jobs import _child_environment

    planted = tmp_path / "planted"
    work = tmp_path / "work"
    work.mkdir()
    (work / ".env").write_text(
        f"QUACKD_REGISTRY_DIR={planted}\nQUACKD_UI_FROM_DOTENV=yes\nSTREAMLIT_SERVER_ENABLE_CORS=false\n",
        encoding="utf-8",
    )
    names = ("QUACKD_REGISTRY_DIR", "QUACKD_UI_FROM_DOTENV", "STREAMLIT_SERVER_ENABLE_CORS")
    for name in names:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(work)
    seen: dict[str, Any] = {}

    def serve(**kw: Any) -> None:
        seen.update(kw)
        seen["child"] = _child_environment(None)
        seen["streamlit_cors"] = os.environ.get("STREAMLIT_SERVER_ENABLE_CORS")

    monkeypatch.setattr(catalog, "installed", lambda _name: True)
    monkeypatch.setattr(launch, "port_taken", lambda _bind, _port: False)
    monkeypatch.setattr(launch, "serve", serve)
    try:
        result = CliRunner().invoke(app, ["ui", "--no-browser"])
        assert result.exit_code == 0, result.output
        assert seen["token_dir"] == Path(DEFAULT_DIR).expanduser(), "never the .env's registry"
        assert "QUACKD_UI_FROM_DOTENV" not in seen["child"], "the run loads the .env itself"
        assert "QUACKD_REGISTRY_DIR" not in seen["child"]
        assert seen["streamlit_cors"] is None, "Streamlit's settings never come from a .env"
        assert os.environ.get("QUACKD_UI_FROM_DOTENV") == "yes", "the page itself still reads it"
    finally:
        for name in names:
            os.environ.pop(name, None)


def test_the_qr_code_drawn_in_the_terminal_reads_back_as_the_address() -> None:
    """Light modules drawn, dark ones left as a dark terminal's background, two rows of modules
    to a line of text: put back as a picture, a phone's reader gets the address."""
    import cv2
    import numpy as np

    address = "http://192.168.1.23:8501/?token=abcdefghijklmnopqrstuvwx"
    light = {"█": (True, True), "▀": (True, False), "▄": (False, True), " ": (False, False)}
    rows: list[list[bool]] = []
    for line in launch.qr_lines(address):
        pairs = [light[glyph] for glyph in line]
        rows.append([top for top, _ in pairs])
        rows.append([bottom for _, bottom in pairs])
    picture = np.array([[255 if lit else 0 for lit in row] for row in rows], dtype=np.uint8)
    picture = cv2.resize(picture, None, fx=8, fy=8, interpolation=cv2.INTER_NEAREST)
    read, _points, _code = cv2.QRCodeDetector().detectAndDecode(picture)
    assert read == address


def test_the_half_blocks_are_drawn_only_where_the_terminal_can_carry_them() -> None:
    assert launch.can_draw(io.TextIOWrapper(io.BytesIO(), encoding="utf-8"))
    assert not launch.can_draw(io.TextIOWrapper(io.BytesIO(), encoding="cp1252"))


def test_a_port_something_listens_on_is_taken_and_a_free_one_is_not() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as other:
        other.bind(("127.0.0.1", 0))
        other.listen()
        port = other.getsockname()[1]
        assert launch.port_taken("127.0.0.1", port)
        assert launch.port_taken("0.0.0.0", port), "the address it prints would reach the other"
    assert not launch.port_taken("127.0.0.1", port)


def test_quackd_ui_refuses_a_taken_port_before_it_announces_an_address(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Streamlit says a port is taken only after the page has printed, and opened, an address
    the other program answers."""
    import quackd.webui.catalog as catalog

    def must_not_start(**_kw: object) -> None:
        raise AssertionError("Streamlit was started on a taken port")

    monkeypatch.setattr(catalog, "installed", lambda _name: True)
    monkeypatch.setattr(launch, "serve", must_not_start)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as other:
        other.bind(("127.0.0.1", 0))
        other.listen()
        port = other.getsockname()[1]
        result = CliRunner().invoke(app, ["ui", "--port", str(port), "--no-browser"])
    assert result.exit_code == 1, result.output
    flat = " ".join(result.output.split())
    assert f"port {port} is already in use" in flat and f"quackd ui --port {port + 1}" in flat
    assert "quackd ui: http" not in result.output
